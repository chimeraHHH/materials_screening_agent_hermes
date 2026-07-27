from __future__ import annotations

from datetime import UTC, datetime

import pytest

from material_agent.dft.mock_backend import MockDFTBackend
from material_agent.dft.runner import DFTStageRunner
from material_agent.orchestrator.models import StageCapability, StageId
from material_agent.orchestrator.runners import StageRunnerRegistry
from material_agent.orchestrator.runtime import OrchestratorRuntime

from tests.integration.test_orchestrator_p01 import _complete_source_run, _stage_input


def _registry(project_root, scenario: str = "mock_success") -> StageRunnerRegistry:
    capability = StageCapability(
        stage=StageId.DFT,
        agent_id="agent03",
        registered=True,
        is_mock=True,
        required_inputs=["requirement", "candidate_manifest"],
        requires_approval=True,
        supports_external=True,
    )
    registry = StageRunnerRegistry()

    def factory(context):
        from material_agent.retrieval.storage import LocalArtifactStore

        return DFTStageRunner(
            artifact_store=LocalArtifactStore(project_root),
            capability=capability,
            backend=MockDFTBackend(scenario=scenario),
            now=lambda: datetime(2026, 7, 27, tzinfo=UTC),
        )

    registry.register(StageId.DFT, factory, capability)
    return registry


def test_dft_orchestrator_approval_waiting_resume_and_report(
    tmp_path, requirement, fixture_payload
):
    project_id = "project-agent03-task4"
    dft_requirement = requirement.model_copy(
        update={"budget": requirement.budget.model_copy(update={"allow_dft": True})}
    )
    requirement_row, manifest = _complete_source_run(
        tmp_path,
        dft_requirement,
        fixture_payload,
        project_id=project_id,
        run_id="run-source",
    )
    stage_input = _stage_input("run-source", requirement_row, manifest)
    project_root = tmp_path / project_id
    with OrchestratorRuntime.from_workspace(
        tmp_path, project_id, runner_registry=_registry(project_root)
    ) as runtime:
        waiting_approval = runtime.start_stage_run(
            stage=StageId.DFT,
            stage_input=stage_input,
            run_id="run-dft-task4",
        )
        assert waiting_approval.interrupts
        approval_id = waiting_approval.interrupts[0].value["approval_id"]
        waiting_external = runtime.approve(
            run_id="run-dft-task4", approval_id=approval_id, decision="approve"
        )
        assert waiting_external.status.value == "PAUSED"
        assert waiting_external.stage_statuses["agent03"] == "RUNNING"
        first_state = runtime.graph.get_state(runtime._config("run-dft-task4")).values
        first_ref = first_state["stage_outcomes"]["agent03"]["external_job_ref"]
        resumed = runtime.resume(run_id="run-dft-task4")
        assert resumed.stage_statuses["agent03"] == "RUNNING"
        resumed = runtime.resume(run_id="run-dft-task4")
        assert resumed.stage_statuses["agent03"] == "RUNNING"
        completed = runtime.resume(run_id="run-dft-task4")
        assert completed.stage_statuses["agent03"] == "SUCCEEDED", completed.errors
        final_state = runtime.graph.get_state(runtime._config("run-dft-task4")).values
        assert final_state["stage_outcomes"]["agent03"]["external_job_ref"] == first_ref
        result = runtime.store.read_json(final_state["stage_outcomes"]["agent03"]["native_result_uri"])
        assert result["is_mock"] is True


def test_dft_approval_rejects_without_submit(tmp_path, requirement, fixture_payload):
    project_id = "project-agent03-reject"
    dft_requirement = requirement.model_copy(
        update={"budget": requirement.budget.model_copy(update={"allow_dft": True})}
    )
    requirement_row, manifest = _complete_source_run(
        tmp_path, dft_requirement, fixture_payload, project_id=project_id, run_id="run-source"
    )
    with OrchestratorRuntime.from_workspace(
        tmp_path, project_id, runner_registry=_registry(tmp_path / project_id)
    ) as runtime:
        view = runtime.start_stage_run(
            stage=StageId.DFT,
            stage_input=_stage_input("run-source", requirement_row, manifest),
            run_id="run-dft-reject",
        )
        approval_id = view.interrupts[0].value["approval_id"]
        rejected = runtime.approve(
            run_id="run-dft-reject", approval_id=approval_id, decision="reject"
        )
        assert rejected.stage_statuses["agent03"] == "CANCELLED"
        assert not (tmp_path / project_id / "stages/agent03/operations").exists()
        report = runtime.read_report("run-dft-reject")
        assert "USER_REJECTED_STAGE" in report
        assert "PARTIAL" in report or "CANCELLED" in report
        assert "upstream" not in report.lower() or "agent01" in report


@pytest.mark.parametrize(
    ("scenario", "expected"),
    [("mock_backend_failed", "PERMANENT_FAILED"), ("mock_timeout", "RETRYABLE_FAILED")],
)
def test_dft_orchestrator_maps_terminal_backend_failures(
    tmp_path, requirement, fixture_payload, scenario, expected
):
    project_id = f"project-agent03-{scenario}"
    dft_requirement = requirement.model_copy(
        update={"budget": requirement.budget.model_copy(update={"allow_dft": True})}
    )
    requirement_row, manifest = _complete_source_run(
        tmp_path, dft_requirement, fixture_payload, project_id=project_id, run_id="run-source"
    )
    with OrchestratorRuntime.from_workspace(
        tmp_path, project_id, runner_registry=_registry(tmp_path / project_id, scenario)
    ) as runtime:
        view = runtime.start_stage_run(
            stage=StageId.DFT,
            stage_input=_stage_input("run-source", requirement_row, manifest),
            run_id="run-dft-failure",
        )
        view = runtime.approve(
            run_id="run-dft-failure",
            approval_id=view.interrupts[0].value["approval_id"],
            decision="approve",
        )
        view = runtime.resume(run_id="run-dft-failure")
        view = runtime.resume(run_id="run-dft-failure")
        assert view.stage_statuses["agent03"] == expected


def test_dft_orchestrator_cancel_maps_external_job(tmp_path, requirement, fixture_payload):
    project_id = "project-agent03-cancel"
    dft_requirement = requirement.model_copy(
        update={"budget": requirement.budget.model_copy(update={"allow_dft": True})}
    )
    requirement_row, manifest = _complete_source_run(
        tmp_path, dft_requirement, fixture_payload, project_id=project_id, run_id="run-source"
    )
    with OrchestratorRuntime.from_workspace(
        tmp_path, project_id, runner_registry=_registry(tmp_path / project_id, "mock_cancel_running")
    ) as runtime:
        view = runtime.start_stage_run(
            stage=StageId.DFT,
            stage_input=_stage_input("run-source", requirement_row, manifest),
            run_id="run-dft-cancel",
        )
        runtime.approve(
            run_id="run-dft-cancel",
            approval_id=view.interrupts[0].value["approval_id"],
            decision="approve",
        )
        cancelled = runtime.cancel(run_id="run-dft-cancel")
        assert cancelled.stage_statuses["agent03"] == "CANCELLED"
