#!/usr/bin/env python3
"""Build rank-masked, disagreement-only calibration packets for adjudicators."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import shutil
from pathlib import Path

from assemble_flatband_expert_evidence_packet import load_band_graph, render_band_plot

ADJUDICATORS = ("expert-a1", "expert-ba1")
REVIEWERS = ("expert-r1", "expert-r2")
DECISION_FIELDS = (
    "fermi_alignment",
    "target_class",
    "band_scope",
    "soc_state",
    "magnetic_order",
    "spin_channel",
    "hubbard_u",
    "isolation_tracking",
    "assessable",
    "derivative_class",
    "primary_mechanism_stratum",
    "frozen_request_ready",
    "exclusion_reason",
)
FINAL_FIELDS = tuple(f"final_{field}" for field in DECISION_FIELDS)
ANSWER_FIELDS = (
    "adjudication_unit_id",
    "c2db_record",
    "formula",
    "adjudicator_pseudonym",
    "disagreement_fields",
    "reviewer_left_values_json",
    "reviewer_right_values_json",
    "locked_symmetric_derivative_exclusion",
    *FINAL_FIELDS,
    "reason_code",
    "rationale",
    "minutes_spent",
    "completed_at",
    "adjudication_commitment_sha256",
)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_json(path: Path, value: object, mode: int) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    path.chmod(mode)


def write_text(path: Path, value: str, mode: int) -> None:
    path.write_text(value, encoding="utf-8")
    path.chmod(mode)


def read_rows(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def unit_id(adjudicator: str, precase_id: str, pair_commitment: str) -> str:
    value = f"flatband-adjudication-unit-v1|{adjudicator}|{precase_id}|{pair_commitment}"
    return f"adj-unit-{hashlib.sha256(value.encode()).hexdigest()[:16]}"


def order_key(adjudicator: str, precase_id: str, pair_commitment: str) -> str:
    value = f"flatband-adjudication-order-v1|{adjudicator}|{precase_id}|{pair_commitment}"
    return hashlib.sha256(value.encode()).hexdigest()


def left_is_r1(adjudicator: str, precase_id: str, pair_commitment: str) -> bool:
    value = f"flatband-adjudication-swap-v1|{adjudicator}|{precase_id}|{pair_commitment}"
    return int(hashlib.sha256(value.encode()).hexdigest()[-1], 16) % 2 == 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--onboarding-root", type=Path, required=True)
    parser.add_argument("--reviewer-root", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    args = parser.parse_args()

    onboarding = args.onboarding_root.resolve()
    reviewer_root = args.reviewer_root.resolve()
    output = args.output_root.resolve()
    if output.exists():
        raise FileExistsError(f"refusing to overwrite adjudication output: {output}")
    output.mkdir(parents=True, mode=0o700)

    identity_payload = json.loads(
        (onboarding / "reviewer_identity_maps_private.json").read_text(encoding="utf-8")
    )
    maps: dict[str, dict[str, dict[str, object]]] = {}
    for item in identity_payload["maps"]:
        maps.setdefault(str(item["reviewer_id"]), {})[
            str(item["blinded_unit_id"])
        ] = item

    rows_by_reviewer: dict[str, dict[str, dict[str, str]]] = {}
    raw_hashes: dict[str, str] = {}
    for reviewer in REVIEWERS:
        answer_path = reviewer_root / reviewer / "calibration_answers.csv"
        raw_hashes[reviewer] = sha256(answer_path)
        by_precase: dict[str, dict[str, str]] = {}
        for row in read_rows(answer_path):
            item = maps[reviewer][row["blinded_unit_id"]]
            by_precase[str(item["precase_id"])] = row
        rows_by_reviewer[reviewer] = by_precase

    if set(rows_by_reviewer[REVIEWERS[0]]) != set(rows_by_reviewer[REVIEWERS[1]]):
        raise ValueError("reviewer case sets differ")

    disagreements: dict[str, tuple[str, ...]] = {}
    for precase_id in rows_by_reviewer[REVIEWERS[0]]:
        r1 = rows_by_reviewer[REVIEWERS[0]][precase_id]
        r2 = rows_by_reviewer[REVIEWERS[1]][precase_id]
        fields = tuple(
            field
            for field in DECISION_FIELDS
            if r1.get(field, "").strip() != r2.get(field, "").strip()
        )
        if fields:
            disagreements[precase_id] = fields

    pair_material = "|".join(sorted(raw_hashes.values()))
    pair_commitment = hashlib.sha256(
        f"flatband-reviewer-pair-v1|{pair_material}".encode()
    ).hexdigest()
    private_maps: list[dict[str, object]] = []
    adjudicator_manifests: dict[str, dict[str, object]] = {}

    if not disagreements:
        for adjudicator in ADJUDICATORS:
            adjudicator_dir = output / adjudicator
            adjudicator_dir.mkdir(mode=0o700)
            write_text(
                adjudicator_dir / "NO_DISAGREEMENT_PRIVATE.md",
                "# No-disagreement completion\n\n"
                f"Adjudicator pseudonym: `{adjudicator}`\n\n"
                f"Reviewer-pair commitment: `{pair_commitment}`\n\n"
                "状态：`AWAITING_NATURAL_PERSON_CONFIRMATION`\n",
                0o600,
            )
        result = {
            "schema_version": "flatband-adjudication-bundle-v1",
            "disagreement_case_count": 0,
            "disagreement_field_count": 0,
            "reviewer_pair_commitment": pair_commitment,
            "adjudicators": list(ADJUDICATORS),
            "scientific_conclusion": False,
        }
        write_json(output / "ADJUDICATION_BUNDLE_PRIVATE.json", result, 0o600)
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0

    r1_private_by_precase = {
        str(item["precase_id"]): item
        for item in identity_payload["maps"]
        if item["reviewer_id"] == "expert-r1"
    }
    for adjudicator in ADJUDICATORS:
        adjudicator_dir = output / adjudicator
        adjudicator_dir.mkdir(mode=0o700)
        evidence_dir = adjudicator_dir / "evidence"
        evidence_dir.mkdir(mode=0o700)
        answer_rows: list[dict[str, str]] = []
        manifest_units: list[dict[str, object]] = []
        ordered = sorted(
            disagreements,
            key=lambda case_id: order_key(adjudicator, case_id, pair_commitment),
        )
        for index, precase_id in enumerate(ordered, start=1):
            adjudication_id = unit_id(adjudicator, precase_id, pair_commitment)
            r1 = rows_by_reviewer["expert-r1"][precase_id]
            r2 = rows_by_reviewer["expert-r2"][precase_id]
            fields = disagreements[precase_id]
            source_item = r1_private_by_precase[precase_id]
            source_blind_id = str(source_item["blinded_unit_id"])
            source_dir = onboarding / "expert-r1" / "evidence" / source_blind_id
            unit_dir = evidence_dir / adjudication_id
            unit_dir.mkdir(mode=0o700)
            for name in (
                "band-page.audit-only.html",
                "source-structure.json",
                "normalized-structure.envelope.json",
            ):
                shutil.copy2(source_dir / name, unit_dir / name)
                (unit_dir / name).chmod(0o400)
            source_summary = json.loads(
                (source_dir / "unit-summary.json").read_text(encoding="utf-8")
            )
            preaudit = source_summary["fermi_preaudit"]
            record_id = r1["c2db_record"]
            formula = r1["formula"]
            plot_path = unit_dir / "band-plot.png"
            render_band_plot(
                graph=load_band_graph(unit_dir / "band-page.audit-only.html"),
                record={**preaudit, "c2db_record_id": record_id},
                case_id=adjudication_id,
                formula=formula,
                output=plot_path,
            )
            plot_path.chmod(0o400)
            unit_summary = {
                "schema_version": "flatband-adjudication-unit-v1",
                "adjudication_unit_id": adjudication_id,
                "c2db_record_id": record_id,
                "formula": formula,
                "fermi_preaudit": preaudit,
                "disagreement_fields": list(fields),
                "reviewer_values_in_summary": False,
                "reviewer_identity_included": False,
                "selection_rank_included": False,
                "system_identity_included": False,
                "peer_adjudicator_included": False,
                "scientific_conclusion": False,
            }
            write_json(unit_dir / "unit-summary.json", unit_summary, 0o400)
            file_hashes = {
                name: sha256(unit_dir / name)
                for name in (
                    "band-page.audit-only.html",
                    "source-structure.json",
                    "normalized-structure.envelope.json",
                    "band-plot.png",
                    "unit-summary.json",
                )
            }
            r1_values = {field: r1[field] for field in fields}
            r2_values = {field: r2[field] for field in fields}
            if left_is_r1(adjudicator, precase_id, pair_commitment):
                left_values, right_values = r1_values, r2_values
                left_source, right_source = "expert-r1", "expert-r2"
            else:
                left_values, right_values = r2_values, r1_values
                left_source, right_source = "expert-r2", "expert-r1"
            locked_exclusion = (
                r1["derivative_class"] != "NOT" or r2["derivative_class"] != "NOT"
            )
            row = {field: "" for field in ANSWER_FIELDS}
            row.update(
                {
                    "adjudication_unit_id": adjudication_id,
                    "c2db_record": record_id,
                    "formula": formula,
                    "adjudicator_pseudonym": adjudicator,
                    "disagreement_fields": ";".join(fields),
                    "reviewer_left_values_json": json.dumps(
                        left_values, ensure_ascii=False, sort_keys=True
                    ),
                    "reviewer_right_values_json": json.dumps(
                        right_values, ensure_ascii=False, sort_keys=True
                    ),
                    "locked_symmetric_derivative_exclusion": (
                        "YES" if locked_exclusion else "NO"
                    ),
                }
            )
            answer_rows.append(row)
            manifest_units.append(
                {
                    "order_index": index,
                    "adjudication_unit_id": adjudication_id,
                    "disagreement_field_count": len(fields),
                    "file_sha256s": file_hashes,
                }
            )
            private_maps.append(
                {
                    "adjudicator_pseudonym": adjudicator,
                    "order_index": index,
                    "adjudication_unit_id": adjudication_id,
                    "precase_id": precase_id,
                    "left_source_reviewer": left_source,
                    "right_source_reviewer": right_source,
                    "locked_symmetric_derivative_exclusion": locked_exclusion,
                }
            )
            unit_dir.chmod(0o500)

        with (adjudicator_dir / "adjudication_answers.csv").open(
            "w", newline="", encoding="utf-8"
        ) as handle:
            writer = csv.DictWriter(handle, fieldnames=ANSWER_FIELDS)
            writer.writeheader()
            writer.writerows(answer_rows)
        (adjudicator_dir / "adjudication_answers.csv").chmod(0o600)
        manifest = {
            "schema_version": "flatband-adjudication-projection-v1",
            "adjudicator_pseudonym": adjudicator,
            "reviewer_pair_commitment": pair_commitment,
            "unit_count": len(manifest_units),
            "units": manifest_units,
            "selection_rank_included": False,
            "master_case_id_included": False,
            "reviewer_identity_included": False,
            "peer_adjudicator_included": False,
            "left_right_assignment_independent": True,
            "symmetric_derivative_exclusion_locked": True,
            "scientific_conclusion": False,
        }
        write_json(evidence_dir / "PROJECTION_MANIFEST.json", manifest, 0o400)
        evidence_dir.chmod(0o500)
        assignment = (
            f"# Assignment：{adjudicator}\n\n"
            "状态：`OPEN FOR INDEPENDENT DISAGREEMENT-ONLY ADJUDICATION`\n\n"
            "- 只使用本目录 evidence 与 adjudication_answers.csv；不看 master index、source rank、"
            " reviewer 身份或另一位 adjudicator 的答案；\n"
            "- left/right 在本席位内独立置换，不对应固定 reviewer；\n"
            "- 只填写 disagreement_fields 列出的 final_* 字段，其他 final_* 保持空白；\n"
            "- locked_symmetric_derivative_exclusion=YES 时，最终 benchmark 必须对所有系统对称排除，"
            " 裁决不能把该案例洗回；\n"
            "- reason_code、rationale、minutes_spent、completed_at 与 commitment 必填。\n\n"
            f"Reviewer-pair commitment：`{pair_commitment}`\n"
        )
        write_text(adjudicator_dir / "ASSIGNMENT_PRIVATE.md", assignment, 0o400)
        sealing = (
            f"# Adjudication sealing：{adjudicator}\n\n"
            "我确认仅查看本席位 disagreement-only packet，未查看 reviewer 身份、source rank、"
            "系统输出或另一位 adjudicator 的答案。\n\n"
            "自然人确认：`AWAITING_NATURAL_PERSON`\n\n"
            "完成时间：`AWAITING_NATURAL_PERSON`\n\n"
            "adjudication_answers.csv SHA-256：`AWAITING_CUSTODIAN`\n"
        )
        write_text(adjudicator_dir / "SEALING_PRIVATE.md", sealing, 0o600)
        adjudicator_manifests[adjudicator] = {
            "projection_manifest_file_sha256": sha256(
                evidence_dir / "PROJECTION_MANIFEST.json"
            ),
            "blank_answer_file_sha256": sha256(
                adjudicator_dir / "adjudication_answers.csv"
            ),
        }

    private_map = {
        "schema_version": "flatband-adjudication-private-identity-map-v1",
        "reviewer_raw_file_sha256s": raw_hashes,
        "reviewer_pair_commitment": pair_commitment,
        "maps": private_maps,
        "private_custody": True,
        "scientific_conclusion": False,
    }
    write_json(output / "ADJUDICATION_IDENTITY_MAP_PRIVATE.json", private_map, 0o600)
    result = {
        "schema_version": "flatband-adjudication-bundle-v1",
        "disagreement_case_count": len(disagreements),
        "disagreement_field_count": sum(map(len, disagreements.values())),
        "reviewer_pair_commitment": pair_commitment,
        "adjudicators": adjudicator_manifests,
        "selection_rank_included": False,
        "master_case_id_in_adjudicator_packets": False,
        "reviewer_identity_included": False,
        "peer_adjudicator_included": False,
        "pilot_execution_authorized": False,
        "scientific_conclusion": False,
    }
    write_json(output / "ADJUDICATION_BUNDLE_PRIVATE.json", result, 0o600)
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
