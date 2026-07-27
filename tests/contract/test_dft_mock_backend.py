from __future__ import annotations

from material_agent.dft.mock_backend import MockDFTBackend
from material_agent.dft.models import DFTRequest
from material_agent.dft.planner import DFTPlanner


def make_request():
    p = {
        "request_id": "dftreq_test", "project_id": "p", "run_id": "r", "stage_run_id": "s",
        "execution_mode": "MOCK", "backend_id": "mock-dft", "workflow_template_id": "mock_dft_lifecycle_v1",
        "upstream_snapshot_hash": "a" * 64,
        "candidates": [{"candidate_id": "c", "structure_id": "st", "structure_artifact_uri": "artifact://structures/st.cif", "structure_artifact_sha256": "b" * 64}],
        "requested_claims": [{"claim_type": "workflow_lifecycle", "required_evidence_level": "L3_DFT_VALIDATED"}],
    }
    plan = DFTPlanner().plan(p)
    return DFTRequest(
        request_id=p["request_id"], project_id="p", run_id="r", stage_run_id="s", execution_mode="MOCK",
        backend_id="mock-dft", candidates=({"candidate_id": "c", "structure_id": "st", "structure_artifact": {"uri": "artifact://structures/st.cif", "sha256": "b" * 64}, "source_stage": "agent01"},),
        requested_claims=({"claim_type": "workflow_lifecycle", "required_evidence_level": "L3_DFT_VALIDATED"},),
        workflow_template_id=p["workflow_template_id"], workflow_plan_hash=plan.plan_hash, task_specs=plan.tasks,
        resource_estimate=plan.resource_estimate, upstream_snapshot_hash="a" * 64, is_mock=True,
    )


def test_submit_is_idempotent_and_success_has_no_scientific_values() -> None:
    request = make_request()
    backend = MockDFTBackend()
    ref1 = backend.submit(request, "p:r:dft:submit:hash")
    ref2 = backend.submit(request, "p:r:dft:submit:hash")
    assert ref1 == ref2
    assert backend.status(ref1).value == "QUEUED"
    assert backend.status(ref1).value == "RUNNING"
    assert backend.status(ref1).value == "SUCCEEDED"
    result = backend.fetch_result(ref1)
    assert result.is_mock is True
    assert result.claim_results[0].status.value == "NOT_EVALUATED_MOCK"
    assert result.claim_results[0].evidence_level != "L3_DFT_VALIDATED"
    assert result.task_results[0].parsed_summary["scientific_results"] is None


def test_failure_timeout_and_cancel_are_terminal() -> None:
    request = make_request()
    for scenario, expected in (("mock_backend_failed", "FAILED"), ("mock_timeout", "TIMEOUT"), ("mock_cancel_running", "CANCELLED")):
        backend = MockDFTBackend(scenario=scenario)
        ref = backend.submit(request, f"key:{scenario}")
        backend.status(ref)
        if scenario == "mock_cancel_running":
            assert backend.cancel(ref).value == "CANCEL_CONFIRMED"
        else:
            backend.status(ref)
        assert backend.status(ref).value == expected
        assert backend.fetch_result(ref).is_mock is True
