#!/usr/bin/env python3
"""Build reviewer-specific, rank-masked Track A calibration projections."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import shutil
from pathlib import Path

from assemble_flatband_expert_evidence_packet import load_band_graph, render_band_plot

REVIEWERS = ("expert-br1", "expert-r1", "expert-r2")
SELECTION_SHA256 = "e6ac22a244cd6de30a593f3240f1228c30b4e9ac58891682e44fb3d5573b2f2c"
GUIDE_SHA256 = "ea74d6e748e84aabf8bb35ed57e358e6c0ef0030a270960a4f01eb637ceb982a"
ANSWER_FIELDS = (
    "blinded_unit_id",
    "c2db_record",
    "formula",
    "reviewer_pseudonym",
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
    "minutes_spent",
    "completed_at",
    "review_commitment_sha256",
)
DECISION_FIELDS = ANSWER_FIELDS[4:]


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def blind_id(reviewer_id: str, precase_id: str) -> str:
    digest = hashlib.sha256(
        f"flatband-calibration-blind-v1|{SELECTION_SHA256}|{reviewer_id}|{precase_id}".encode()
    ).hexdigest()
    return f"cal-unit-{digest[:16]}"


def order_key(reviewer_id: str, precase_id: str) -> str:
    return hashlib.sha256(
        f"flatband-calibration-order-v1|{GUIDE_SHA256}|{reviewer_id}|{precase_id}".encode()
    ).hexdigest()


def write_json(path: Path, value: object, mode: int) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    path.chmod(mode)


def assert_existing_answers_blank(path: Path) -> None:
    if not path.exists():
        return
    with path.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    for row in rows:
        for field in DECISION_FIELDS:
            if row.get(field, "").strip():
                raise ValueError(f"refusing to replace non-empty answers: {path}")


def masked_preaudit(record: dict[str, object]) -> dict[str, object]:
    allowed = (
        "band_page_sha256",
        "trace_name",
        "fermi_alignment_method",
        "fermi_coordinate_in_plot_e_v",
        "fermi_window_e_v",
        "pbe_fermi_wrt_vacuum_e_v",
        "pbe_vbm_wrt_vacuum_e_v",
        "plot_energy_reference",
        "window_status",
        "narrowest_band_index_in_window",
        "narrowest_bandwidth_e_v",
        "distance_to_fermi_e_v",
        "band_count",
        "k_point_count",
        "soc_explicit",
        "target_class_assignment_authorized",
        "gold_label_status",
        "scientific_conclusion",
    )
    return {field: record[field] for field in allowed}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--onboarding-root", type=Path, required=True)
    args = parser.parse_args()
    root = args.onboarding_root.resolve()
    master = root / "evidence"
    with (master / "EVIDENCE_INDEX.csv").open(newline="", encoding="utf-8") as handle:
        cases = list(csv.DictReader(handle))
    if len(cases) != 12:
        raise ValueError("master evidence index must contain 12 cases")

    private_maps: list[dict[str, object]] = []
    public_manifests: dict[str, dict[str, object]] = {}
    for reviewer_id in REVIEWERS:
        reviewer_dir = root / reviewer_id
        answer_path = reviewer_dir / "calibration_answers.csv"
        assert_existing_answers_blank(answer_path)
        projected = reviewer_dir / "evidence"
        if projected.is_symlink():
            projected.unlink()
        elif projected.exists():
            raise FileExistsError(f"refusing to overwrite projection: {projected}")
        projected.mkdir(mode=0o700)
        ordered = sorted(
            cases, key=lambda row: order_key(reviewer_id, row["precase_id"])
        )
        answer_rows: list[dict[str, str]] = []
        manifest_units: list[dict[str, object]] = []
        for order_index, case in enumerate(ordered, start=1):
            precase_id = case["precase_id"]
            unit_id = blind_id(reviewer_id, precase_id)
            record_id = case["c2db_record"]
            formula = case["formula"]
            source_dir = master / precase_id
            unit_dir = projected / unit_id
            unit_dir.mkdir(mode=0o700)
            for name in (
                "band-page.audit-only.html",
                "source-structure.json",
                "normalized-structure.envelope.json",
            ):
                destination = unit_dir / name
                shutil.copy2(source_dir / name, destination)
                destination.chmod(0o400)
            source_summary = json.loads(
                (source_dir / "case-summary.json").read_text(encoding="utf-8")
            )
            preaudit = masked_preaudit(source_summary["fermi_preaudit"])
            plot_path = unit_dir / "band-plot.png"
            render_band_plot(
                graph=load_band_graph(unit_dir / "band-page.audit-only.html"),
                record={**preaudit, "c2db_record_id": record_id},
                case_id=unit_id,
                formula=formula,
                output=plot_path,
            )
            plot_path.chmod(0o400)
            summary = {
                "schema_version": "flatband-reviewer-calibration-unit-v1",
                "blinded_unit_id": unit_id,
                "c2db_record_id": record_id,
                "formula": formula,
                "fermi_preaudit": preaudit,
                "annotation_guide_sha256": GUIDE_SHA256,
                "selection_rank_included": False,
                "system_identity_included": False,
                "peer_answer_included": False,
                "gold_label_status": "NOT_ANNOTATED",
                "scientific_conclusion": False,
            }
            write_json(unit_dir / "unit-summary.json", summary, 0o400)
            file_shas = {
                name: sha256(unit_dir / name)
                for name in (
                    "band-page.audit-only.html",
                    "source-structure.json",
                    "normalized-structure.envelope.json",
                    "band-plot.png",
                    "unit-summary.json",
                )
            }
            manifest_units.append(
                {
                    "order_index": order_index,
                    "blinded_unit_id": unit_id,
                    "c2db_record_id": record_id,
                    "formula": formula,
                    "file_sha256s": file_shas,
                }
            )
            private_maps.append(
                {
                    "reviewer_id": reviewer_id,
                    "order_index": order_index,
                    "blinded_unit_id": unit_id,
                    "precase_id": precase_id,
                    "c2db_record_id": record_id,
                    "formula": formula,
                }
            )
            answer_rows.append(
                {
                    **{field: "" for field in ANSWER_FIELDS},
                    "blinded_unit_id": unit_id,
                    "c2db_record": record_id,
                    "formula": formula,
                    "reviewer_pseudonym": reviewer_id,
                }
            )
            unit_dir.chmod(0o500)
        manifest = {
            "schema_version": "flatband-reviewer-calibration-projection-v1",
            "reviewer_id": reviewer_id,
            "annotation_guide_sha256": GUIDE_SHA256,
            "selection_rank_included": False,
            "system_identity_included": False,
            "peer_answer_included": False,
            "independent_order": True,
            "unit_count": len(manifest_units),
            "units": manifest_units,
            "calibration_only": True,
            "pilot_execution_authorized": False,
            "scientific_conclusion": False,
        }
        write_json(projected / "PROJECTION_MANIFEST.json", manifest, 0o400)
        projected.chmod(0o500)
        with answer_path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=ANSWER_FIELDS)
            writer.writeheader()
            writer.writerows(answer_rows)
        answer_path.chmod(0o600)
        public_manifests[reviewer_id] = {
            "projection_manifest_file_sha256": sha256(
                projected / "PROJECTION_MANIFEST.json"
            ),
            "answer_template_file_sha256": sha256(answer_path),
        }

    private_map = {
        "schema_version": "flatband-reviewer-calibration-private-identity-map-v1",
        "selection_manifest_sha256": SELECTION_SHA256,
        "annotation_guide_sha256": GUIDE_SHA256,
        "maps": sorted(
            private_maps,
            key=lambda row: (str(row["reviewer_id"]), int(row["order_index"])),
        ),
        "private_custody": True,
        "scientific_conclusion": False,
    }
    write_json(root / "reviewer_identity_maps_private.json", private_map, 0o600)
    result = {
        "schema_version": "flatband-reviewer-calibration-projection-bundle-v1",
        "reviewer_count": len(REVIEWERS),
        "unit_count_per_reviewer": 12,
        "private_identity_map_file_sha256": sha256(
            root / "reviewer_identity_maps_private.json"
        ),
        "reviewers": public_manifests,
        "selection_rank_included": False,
        "system_identity_included": False,
        "pilot_execution_authorized": False,
        "scientific_conclusion": False,
    }
    write_json(root / "reviewer_projection_bundle_private.json", result, 0o600)
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
