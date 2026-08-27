#!/usr/bin/env python3
"""Fail-closed checks for the private Track A expert onboarding packet."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import sys
from datetime import datetime
from itertools import combinations
from pathlib import Path

EXPERTS = {
    "expert-a1": "ADJUDICATOR",
    "expert-ba1": "ADJUDICATOR",
    "expert-br1": "REVIEWER",
    "expert-r1": "REVIEWER",
    "expert-r2": "REVIEWER",
}
CASES = (
    ("cal-pre-001", "6", "2AsBeO5-1", "AsBeO5"),
    ("cal-pre-002", "7", "2CRbO3-1", "CRbO3"),
    ("cal-pre-003", "11", "1BBe2O5-1", "BBe2O5"),
    ("cal-pre-004", "18", "1MgN8-1", "MgN8"),
    ("cal-pre-005", "19", "1CoRe2O8-1", "CoRe2O8"),
    ("cal-pre-006", "26", "2NiPS3-1", "NiPS3"),
    ("cal-pre-007", "30", "1CoNaAs2S6-1", "CoNaAs2S6"),
    ("cal-pre-008", "35", "1Cu3I4-1", "Cu3I4"),
    ("cal-pre-009", "38", "1NaMo2O2Cl6-1", "NaMo2O2Cl6"),
    ("cal-pre-010", "50", "2MoBr3-1", "MoBr3"),
    ("cal-pre-011", "53", "2CuF-2", "CuF"),
    ("cal-pre-012", "59", "1AgSnF6-1", "AgSnF6"),
)
SHA256 = re.compile(r"^[0-9a-f]{64}$")
YES_NO = {"YES", "NO"}
GUIDE_SHA256 = "ea74d6e748e84aabf8bb35ed57e358e6c0ef0030a270960a4f01eb637ceb982a"
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


def _rows(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _required(row: dict[str, str], fields: tuple[str, ...], label: str) -> list[str]:
    return [
        f"{label}: missing {field}"
        for field in fields
        if not row.get(field, "").strip()
    ]


def _rfc3339(value: str) -> bool:
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return False
    return parsed.tzinfo is not None and parsed.utcoffset() is not None


def validate_intake(root: Path) -> list[str]:
    path = root / "expert_intake_private.csv"
    rows = _rows(path)
    errors: list[str] = []
    if [row["expert_id"] for row in rows] != sorted(EXPERTS):
        errors.append(
            "intake: expert IDs must exactly match the five sorted pseudonyms"
        )
        return errors
    required = (
        "legal_name",
        "current_institution",
        "current_group_or_department",
        "career_stage",
        "domain_expertise",
        "calibration_hours_committed",
        "pilot_r1_hours_committed",
        "compensation_or_acknowledgement",
        "possible_authored_topics_or_materials_for_recusal",
        "identity_evidence_type",
        "identity_evidence_private_uri",
        "declared_at",
        "signature_or_verifiable_acknowledgement",
    )
    declaration_fields = (
        "participated_in_system_implementation",
        "participated_in_taggraph_or_prompt_design",
        "saw_system_outputs_or_configuration",
        "recent_coauthorship_with_project_lead",
        "agrees_no_external_search",
        "agrees_independent_sealed_submission",
        "agrees_raw_answers_preserved",
    )
    for row in rows:
        expert_id = row["expert_id"]
        label = f"intake {expert_id}"
        if row.get("role") != EXPERTS[expert_id]:
            errors.append(f"{label}: wrong role")
        errors.extend(_required(row, required + declaration_fields, label))
        for field in declaration_fields:
            value = row.get(field, "").strip().upper()
            if value and value not in YES_NO:
                errors.append(f"{label}: {field} must be YES or NO")
        for field in (
            "participated_in_system_implementation",
            "participated_in_taggraph_or_prompt_design",
            "saw_system_outputs_or_configuration",
        ):
            if row.get(field, "").strip().upper() == "YES":
                errors.append(f"{label}: disqualified by {field}=YES")
        for field in (
            "agrees_no_external_search",
            "agrees_independent_sealed_submission",
            "agrees_raw_answers_preserved",
        ):
            if row.get(field, "").strip().upper() == "NO":
                errors.append(f"{label}: cannot accept protocol because {field}=NO")
        if row.get("declared_at") and not _rfc3339(row["declared_at"]):
            errors.append(f"{label}: declared_at is not timezone-aware RFC3339")
        if row.get("intake_status") != "SIGNED_ELIGIBLE":
            errors.append(f"{label}: intake_status must be SIGNED_ELIGIBLE")
    return errors


def validate_pairs(root: Path) -> list[str]:
    path = root / "pairwise_independence_private.csv"
    rows = _rows(path)
    errors: list[str] = []
    expected = set(combinations(sorted(EXPERTS), 2))
    observed = {(row["expert_id_1"], row["expert_id_2"]) for row in rows}
    if observed != expected or len(rows) != len(expected):
        return ["pairwise: rows must exactly cover all 10 sorted expert pairs"]
    for row in rows:
        pair = f"pairwise {row['expert_id_1']} / {row['expert_id_2']}"
        for field in (
            "advisor_student_relationship",
            "direct_reporting_relationship",
            "coauthored_since_2023_08_27",
            "same_group_or_institution",
            "other_declared_relationship",
            "pair_eligible",
        ):
            value = row.get(field, "").strip().upper()
            if value not in YES_NO:
                errors.append(f"{pair}: {field} must be YES or NO")
        for field in (
            "advisor_student_relationship",
            "direct_reporting_relationship",
            "coauthored_since_2023_08_27",
        ):
            if row.get(field, "").strip().upper() == "YES":
                errors.append(f"{pair}: prohibited relationship {field}=YES")
        if row.get("pair_eligible", "").strip().upper() != "YES":
            errors.append(f"{pair}: pair_eligible must be YES")
        if not _rfc3339(row.get("reviewed_by_custodian_at", "")):
            errors.append(f"{pair}: reviewed_by_custodian_at is not RFC3339")
        if row.get("pair_status") != "REVIEWED_ELIGIBLE":
            errors.append(f"{pair}: pair_status must be REVIEWED_ELIGIBLE")
    return errors


def validate_answers(root: Path) -> list[str]:
    errors: list[str] = []
    reviewer_rows: dict[str, dict[str, dict[str, str]]] = {}
    reviewer_file_shas: dict[str, str] = {}
    private_map_path = root / "reviewer_identity_maps_private.json"
    projection_bundle_path = root / "reviewer_projection_bundle_private.json"
    if not private_map_path.exists() or not projection_bundle_path.exists():
        return ["answers: reviewer projection identity map or bundle is missing"]
    private_map_payload = json.loads(private_map_path.read_text(encoding="utf-8"))
    projection_bundle = json.loads(projection_bundle_path.read_text(encoding="utf-8"))
    if projection_bundle.get("private_identity_map_file_sha256") != _sha256(
        private_map_path
    ):
        errors.append("answers: reviewer private identity-map hash drift")
    maps_by_reviewer: dict[str, list[dict[str, object]]] = {}
    for item in private_map_payload.get("maps", ()):
        maps_by_reviewer.setdefault(str(item["reviewer_id"]), []).append(item)
    enums = {
        "fermi_alignment": {"PASS", "FAIL", "UNKNOWN"},
        "target_class": {"FB100", "NB300", "EXCLUDE", "UNASSESSABLE"},
        "band_scope": {"HIGH_SYMMETRY_ONLY", "SUFFICIENT", "UNKNOWN"},
        "isolation_tracking": {"PASS", "FAIL", "UNKNOWN"},
        "assessable": YES_NO,
        "derivative_class": {
            "NOT",
            "VACANCY",
            "INTERCALATION",
            "NON_STOICHIOMETRIC",
            "ORDERED_DEFECT",
            "UNKNOWN",
        },
        "frozen_request_ready": YES_NO,
    }
    free_required = (
        "soc_state",
        "magnetic_order",
        "spin_channel",
        "hubbard_u",
        "primary_mechanism_stratum",
    )
    reviewer_ids = sorted(
        expert_id for expert_id, role in EXPERTS.items() if role == "REVIEWER"
    )
    for expert_id in reviewer_ids:
        path = root / expert_id / "calibration_answers.csv"
        if not path.exists():
            errors.append(f"answers {expert_id}: file missing")
            continue
        rows = _rows(path)
        reviewer_file_shas[expert_id] = _sha256(path)
        reviewer_map = sorted(
            maps_by_reviewer.get(expert_id, ()),
            key=lambda item: int(item["order_index"]),
        )
        if len(reviewer_map) != len(CASES):
            errors.append(f"answers {expert_id}: private map must contain 12 units")
            continue
        identities = tuple(
            (
                row.get("blinded_unit_id"),
                row.get("c2db_record"),
                row.get("formula"),
            )
            for row in rows
        )
        expected_identities = tuple(
            (
                str(item["blinded_unit_id"]),
                str(item["c2db_record_id"]),
                str(item["formula"]),
            )
            for item in reviewer_map
        )
        if identities != expected_identities:
            errors.append(
                f"answers {expert_id}: 12-unit blinded identity/order mismatch"
            )
            continue
        blind_to_precase = {
            str(item["blinded_unit_id"]): str(item["precase_id"])
            for item in reviewer_map
        }
        reviewer_rows[expert_id] = {
            blind_to_precase[row["blinded_unit_id"]]: row for row in rows
        }
        commitments: set[str] = set()
        for row in rows:
            label = f"answers {expert_id} {row['blinded_unit_id']}"
            if row.get("reviewer_pseudonym") != expert_id:
                errors.append(f"{label}: reviewer_pseudonym mismatch")
            for field, allowed in enums.items():
                if row.get(field, "").strip().upper() not in allowed:
                    errors.append(f"{label}: invalid {field}")
            errors.extend(_required(row, free_required, label))
            excluded = (
                row.get("target_class") in {"EXCLUDE", "UNASSESSABLE"}
                or row.get("assessable") == "NO"
                or row.get("derivative_class") != "NOT"
            )
            if excluded and not row.get("exclusion_reason", "").strip():
                errors.append(f"{label}: exclusion_reason required")
            try:
                if float(row.get("minutes_spent", "")) <= 0:
                    raise ValueError
            except ValueError:
                errors.append(f"{label}: minutes_spent must be positive")
            if not _rfc3339(row.get("completed_at", "")):
                errors.append(f"{label}: completed_at is not RFC3339")
            commitment = row.get("review_commitment_sha256", "")
            if not SHA256.fullmatch(commitment):
                errors.append(f"{label}: invalid review_commitment_sha256")
            else:
                commitments.add(commitment)
        if len(commitments) != 1:
            errors.append(
                f"answers {expert_id}: exactly one stable commitment is required"
            )

    disagreements: dict[str, tuple[str, ...]] = {}
    expected_precases = {case[0] for case in CASES}
    if (
        set(reviewer_rows.get("expert-r1", ())) == expected_precases
        and set(reviewer_rows.get("expert-r2", ())) == expected_precases
    ):
        for precase_id, *_ in CASES:
            r1_row = reviewer_rows["expert-r1"][precase_id]
            r2_row = reviewer_rows["expert-r2"][precase_id]
            fields = tuple(
                field
                for field in DECISION_FIELDS
                if r1_row.get(field, "").strip() != r2_row.get(field, "").strip()
            )
            if fields:
                disagreements[precase_id] = fields

    adjudication_fields = (
        "precase_id",
        "adjudicator_pseudonym",
        "disagreement_fields",
        "reviewer_r1_raw_sha256",
        "reviewer_r2_raw_sha256",
        "final_fermi_alignment",
        "final_target_class",
        "final_band_scope",
        "final_soc_state",
        "final_magnetic_order",
        "final_spin_channel",
        "final_hubbard_u",
        "final_isolation_tracking",
        "final_assessable",
        "final_derivative_class",
        "final_primary_mechanism_stratum",
        "final_frozen_request_ready",
        "final_exclusion_reason",
        "reason_code",
        "rationale",
        "minutes_spent",
        "completed_at",
        "adjudication_commitment_sha256",
    )
    adjudicator_ids = sorted(
        expert_id for expert_id, role in EXPERTS.items() if role == "ADJUDICATOR"
    )
    for expert_id in adjudicator_ids:
        path = root / expert_id / "adjudication_calibration.csv"
        if not path.exists():
            errors.append(f"adjudication {expert_id}: file missing")
            continue
        with path.open(newline="", encoding="utf-8") as handle:
            reader = csv.DictReader(handle)
            rows = list(reader)
            if tuple(reader.fieldnames or ()) != adjudication_fields:
                errors.append(f"adjudication {expert_id}: header mismatch")
                continue
        observed_cases = tuple(row.get("precase_id", "") for row in rows)
        expected_cases = tuple(disagreements)
        if observed_cases != expected_cases:
            errors.append(
                f"adjudication {expert_id}: rows must exactly cover reviewer disagreements"
            )
        no_disagreement = root / expert_id / "NO_DISAGREEMENT.json"
        if disagreements and no_disagreement.exists():
            errors.append(
                f"adjudication {expert_id}: NO_DISAGREEMENT conflicts with raw differences"
            )
        if not disagreements and not no_disagreement.exists():
            errors.append(
                f"adjudication {expert_id}: no rows and no sealed NO_DISAGREEMENT.json"
            )
        if not disagreements and no_disagreement.exists():
            try:
                declaration = json.loads(no_disagreement.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                errors.append(
                    f"adjudication {expert_id}: malformed NO_DISAGREEMENT.json"
                )
                continue
            expected_declaration = {
                "schema_version": "flatband-adjudication-no-disagreement-v1",
                "adjudicator_pseudonym": expert_id,
                "annotation_guide_sha256": GUIDE_SHA256,
                "status": "NO_DISAGREEMENT",
                "scientific_conclusion": False,
            }
            for field, expected in expected_declaration.items():
                if declaration.get(field) != expected:
                    errors.append(
                        f"adjudication {expert_id}: invalid NO_DISAGREEMENT {field}"
                    )
            for field in (
                "reviewer_r1_raw_sha256",
                "reviewer_r2_raw_sha256",
                "adjudication_commitment_sha256",
            ):
                if not SHA256.fullmatch(str(declaration.get(field, ""))):
                    errors.append(
                        f"adjudication {expert_id}: invalid NO_DISAGREEMENT {field}"
                    )
            if declaration.get("reviewer_r1_raw_sha256") != reviewer_file_shas.get(
                "expert-r1"
            ):
                errors.append(
                    f"adjudication {expert_id}: NO_DISAGREEMENT does not bind expert-r1 raw"
                )
            if declaration.get("reviewer_r2_raw_sha256") != reviewer_file_shas.get(
                "expert-r2"
            ):
                errors.append(
                    f"adjudication {expert_id}: NO_DISAGREEMENT does not bind expert-r2 raw"
                )
            if not _rfc3339(str(declaration.get("completed_at", ""))):
                errors.append(
                    f"adjudication {expert_id}: invalid NO_DISAGREEMENT completed_at"
                )
            try:
                if float(declaration.get("minutes_spent", "")) <= 0:
                    raise ValueError
            except (TypeError, ValueError):
                errors.append(
                    f"adjudication {expert_id}: invalid NO_DISAGREEMENT minutes_spent"
                )
        for row in rows:
            label = f"adjudication {expert_id} {row.get('precase_id', '')}"
            errors.extend(_required(row, adjudication_fields, label))
            if row.get("adjudicator_pseudonym") != expert_id:
                errors.append(f"{label}: adjudicator_pseudonym mismatch")
            expected_fields = "|".join(disagreements.get(row.get("precase_id", ""), ()))
            if row.get("disagreement_fields") != expected_fields:
                errors.append(f"{label}: disagreement_fields mismatch")
            for field in (
                "reviewer_r1_raw_sha256",
                "reviewer_r2_raw_sha256",
                "adjudication_commitment_sha256",
            ):
                if not SHA256.fullmatch(row.get(field, "")):
                    errors.append(f"{label}: invalid {field}")
            if row.get("reviewer_r1_raw_sha256") != reviewer_file_shas.get("expert-r1"):
                errors.append(f"{label}: does not bind expert-r1 raw")
            if row.get("reviewer_r2_raw_sha256") != reviewer_file_shas.get("expert-r2"):
                errors.append(f"{label}: does not bind expert-r2 raw")
            if not _rfc3339(row.get("completed_at", "")):
                errors.append(f"{label}: completed_at is not RFC3339")
            try:
                if float(row.get("minutes_spent", "")) <= 0:
                    raise ValueError
            except ValueError:
                errors.append(f"{label}: minutes_spent must be positive")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("root", type=Path)
    parser.add_argument(
        "--require-answers",
        action="store_true",
        help="also validate reviewer answers and adjudication-calibration closure",
    )
    args = parser.parse_args()
    errors = validate_intake(args.root) + validate_pairs(args.root)
    if args.require_answers:
        errors.extend(validate_answers(args.root))
    if errors:
        for error in errors:
            print(f"FAIL: {error}")
        print(f"ONBOARDING_NO_GO: {len(errors)} issue(s)")
        return 1
    print("ONBOARDING_INPUT_VALID: schema/eligibility fields pass deterministic checks")
    print("This does not itself authorize Pilot execution.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
