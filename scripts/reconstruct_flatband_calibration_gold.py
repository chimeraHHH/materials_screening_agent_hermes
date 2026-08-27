#!/usr/bin/env python3
"""Reconstruct private Track A calibration decisions from sealed raw inputs."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
from pathlib import Path

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
OUTPUT_FIELDS = (
    "precase_id",
    "c2db_record",
    "formula",
    *DECISION_FIELDS,
    "raw_derivative_exclusion_locked",
    "benchmark_eligibility_status",
    "disagreement_fields",
    "formal_adjudicator_used",
    "reviewer_r1_raw_sha256",
    "reviewer_r2_raw_sha256",
    "adjudicator_a1_raw_sha256",
)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_rows(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def write_json(path: Path, value: object) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    path.chmod(0o400)


def eligibility(final: dict[str, str], derivative_locked: bool) -> str:
    if derivative_locked:
        return "EXCLUDED_RAW_DERIVATIVE_LOCK"
    if final["fermi_alignment"] != "PASS":
        return "EXCLUDED_FERMI_ALIGNMENT"
    if final["target_class"] not in {"FB100", "NB300"}:
        return "EXCLUDED_TARGET_CLASS"
    if final["assessable"] != "YES":
        return "EXCLUDED_UNASSESSABLE"
    if final["derivative_class"] != "NOT":
        return "EXCLUDED_FINAL_DERIVATIVE"
    if final["frozen_request_ready"] != "YES":
        return "NOT_READY_FROZEN_REQUEST"
    return "ELIGIBLE_FOR_FORMAL_CALIBRATION_CASE_BUILD"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--onboarding-root", type=Path, required=True)
    parser.add_argument("--reviewer-root", type=Path, required=True)
    parser.add_argument("--adjudication-root", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    args = parser.parse_args()

    onboarding = args.onboarding_root.resolve()
    reviewer_root = args.reviewer_root.resolve()
    adjudication_root = args.adjudication_root.resolve()
    output = args.output_root.resolve()
    if output.exists():
        raise FileExistsError(f"refusing to overwrite reconstruction: {output}")
    output.mkdir(parents=True, mode=0o700)

    reviewer_map_payload = json.loads(
        (onboarding / "reviewer_identity_maps_private.json").read_text(encoding="utf-8")
    )
    reviewer_maps: dict[str, dict[str, str]] = {}
    for item in reviewer_map_payload["maps"]:
        reviewer_maps.setdefault(str(item["reviewer_id"]), {})[
            str(item["blinded_unit_id"])
        ] = str(item["precase_id"])

    reviewer_rows: dict[str, dict[str, dict[str, str]]] = {}
    reviewer_hashes: dict[str, str] = {}
    for reviewer in ("expert-r1", "expert-r2"):
        path = reviewer_root / reviewer / "calibration_answers.csv"
        reviewer_hashes[reviewer] = sha256(path)
        reviewer_rows[reviewer] = {
            reviewer_maps[reviewer][row["blinded_unit_id"]]: row
            for row in read_rows(path)
        }

    adjudication_map_payload = json.loads(
        (adjudication_root / "ADJUDICATION_IDENTITY_MAP_PRIVATE.json").read_text(
            encoding="utf-8"
        )
    )
    adjudication_maps = {
        (str(item["adjudicator_pseudonym"]), str(item["adjudication_unit_id"])): str(
            item["precase_id"]
        )
        for item in adjudication_map_payload["maps"]
    }
    adjudicator_rows: dict[str, dict[str, dict[str, str]]] = {}
    adjudicator_hashes: dict[str, str] = {}
    for adjudicator in ("expert-a1", "expert-ba1"):
        path = adjudication_root / adjudicator / "adjudication_answers.csv"
        adjudicator_hashes[adjudicator] = sha256(path)
        adjudicator_rows[adjudicator] = {
            adjudication_maps[(adjudicator, row["adjudication_unit_id"])]: row
            for row in read_rows(path)
        }

    precase_ids = sorted(reviewer_rows["expert-r1"])
    if precase_ids != sorted(reviewer_rows["expert-r2"]):
        raise ValueError("reviewer case sets differ")
    output_rows: list[dict[str, str]] = []
    backup_comparisons = 0
    backup_disagreements = 0
    disagreement_case_count = 0
    disagreement_field_count = 0
    status_counts: dict[str, int] = {}
    for precase_id in precase_ids:
        r1 = reviewer_rows["expert-r1"][precase_id]
        r2 = reviewer_rows["expert-r2"][precase_id]
        disagreements = tuple(
            field for field in DECISION_FIELDS if r1[field].strip() != r2[field].strip()
        )
        final: dict[str, str] = {}
        a1 = adjudicator_rows["expert-a1"].get(precase_id)
        ba1 = adjudicator_rows["expert-ba1"].get(precase_id)
        if disagreements:
            disagreement_case_count += 1
            disagreement_field_count += len(disagreements)
            if a1 is None or ba1 is None:
                raise ValueError("disagreement lacks A1 or BA1 adjudication")
        for field in DECISION_FIELDS:
            if field in disagreements:
                value = a1[f"final_{field}"].strip() if a1 else ""
                if not value:
                    raise ValueError("formal adjudicator omitted a disagreement field")
                final[field] = value
                backup_comparisons += 1
                if ba1 and ba1[f"final_{field}"].strip() != value:
                    backup_disagreements += 1
            else:
                final[field] = r1[field].strip()
        derivative_locked = (
            r1["derivative_class"].strip() != "NOT"
            or r2["derivative_class"].strip() != "NOT"
        )
        status = eligibility(final, derivative_locked)
        status_counts[status] = status_counts.get(status, 0) + 1
        output_rows.append(
            {
                "precase_id": precase_id,
                "c2db_record": r1["c2db_record"],
                "formula": r1["formula"],
                **final,
                "raw_derivative_exclusion_locked": "YES" if derivative_locked else "NO",
                "benchmark_eligibility_status": status,
                "disagreement_fields": ";".join(disagreements),
                "formal_adjudicator_used": "expert-a1" if disagreements else "NONE",
                "reviewer_r1_raw_sha256": reviewer_hashes["expert-r1"],
                "reviewer_r2_raw_sha256": reviewer_hashes["expert-r2"],
                "adjudicator_a1_raw_sha256": adjudicator_hashes["expert-a1"],
            }
        )

    gold_path = output / "calibration_gold_private.csv"
    with gold_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=OUTPUT_FIELDS)
        writer.writeheader()
        writer.writerows(output_rows)
    gold_path.chmod(0o400)
    backup_audit = {
        "schema_version": "flatband-backup-adjudicator-audit-v1",
        "primary_adjudicator": "expert-a1",
        "backup_adjudicator": "expert-ba1",
        "backup_is_second_vote": False,
        "compared_disagreement_field_count": backup_comparisons,
        "backup_divergent_field_count": backup_disagreements,
        "backup_field_concordance": (
            (backup_comparisons - backup_disagreements) / backup_comparisons
            if backup_comparisons
            else 1.0
        ),
        "primary_adjudication_sha256": adjudicator_hashes["expert-a1"],
        "backup_adjudication_sha256": adjudicator_hashes["expert-ba1"],
        "scientific_conclusion": False,
    }
    write_json(output / "BACKUP_ADJUDICATOR_AUDIT_PRIVATE.json", backup_audit)
    manifest = {
        "schema_version": "flatband-calibration-reconstruction-v1",
        "case_count": len(output_rows),
        "disagreement_case_count": disagreement_case_count,
        "disagreement_field_count": disagreement_field_count,
        "status_counts": status_counts,
        "formal_adjudicator": "expert-a1",
        "backup_adjudicator": "expert-ba1",
        "backup_is_second_vote": False,
        "reviewer_raw_file_sha256s": reviewer_hashes,
        "adjudicator_raw_file_sha256s": adjudicator_hashes,
        "calibration_gold_file_sha256": sha256(gold_path),
        "derivative_exclusion_rule": "ANY_PRIMARY_RAW_NON_NOT_EXCLUDES_SYMMETRICALLY",
        "pilot_execution_authorized": False,
        "publication_grade_registry_complete": False,
        "scientific_conclusion": False,
    }
    write_json(output / "CALIBRATION_RECONSTRUCTION_MANIFEST_PRIVATE.json", manifest)
    print(
        json.dumps(
            {
                "case_count": len(output_rows),
                "disagreement_case_count": disagreement_case_count,
                "disagreement_field_count": disagreement_field_count,
                "status_counts": status_counts,
                "backup_field_concordance": backup_audit["backup_field_concordance"],
                "calibration_gold_file_sha256": manifest["calibration_gold_file_sha256"],
                "pilot_execution_authorized": False,
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
