from __future__ import annotations

import pytest

from material_agent.dft.planner import DFTPlanner, StageInputValidator, build_approval_payload


def payload(claim: str = "workflow_lifecycle") -> dict:
    return {
        "request_id": "dftreq_test", "project_id": "p", "run_id": "r", "stage_run_id": "s",
        "execution_mode": "MOCK", "backend_id": "mock-dft", "workflow_template_id": "mock_dft_lifecycle_v1",
        "upstream_snapshot_hash": "a" * 64,
        "candidates": [{"candidate_id": "c", "structure_id": "st", "structure_artifact_uri": "artifact://structures/st.cif", "structure_artifact_sha256": "b" * 64}],
        "requested_claims": [{"claim_type": claim, "required_evidence_level": "L3_DFT_VALIDATED"}],
    }


def test_validator_reports_missing_input() -> None:
    result = StageInputValidator().validate({})
    assert not result.valid
    assert result.status == "BLOCKED_MISSING_INPUT"
    assert "candidates" in {issue.path for issue in result.issues}


def test_planner_is_deterministic_and_marks_unsupported_claim() -> None:
    planner = DFTPlanner()
    first = planner.plan(payload("band_gap_computed"))
    second = planner.plan(payload("band_gap_computed"))
    assert first.plan_hash == second.plan_hash
    assert first.workflow_id == second.workflow_id
    assert first.blockers == ("NOT_SUPPORTED:band_gap_computed",)
    approval = build_approval_payload(first, project_id="p", run_id="r")
    assert approval["is_mock"] is True
    assert "不会运行 VASP" in approval["message"]
    assert "科研数值" in approval["message"]


def test_planner_does_not_invent_real_policy() -> None:
    with pytest.raises(ValueError, match="BLOCKED_MISSING_SCIENTIFIC_POLICY"):
        DFTPlanner().plan({**payload(), "execution_mode": "REAL", "backend_id": "vasp"})
