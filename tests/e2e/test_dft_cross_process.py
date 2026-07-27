from __future__ import annotations

from material_agent.orchestrator.models import StageId
from material_agent.orchestrator.runtime import OrchestratorRuntime

from tests.integration.test_dft_runner_orchestrator import _registry
from tests.integration.test_orchestrator_p01 import _complete_source_run, _stage_input


def test_dft_waiting_external_survives_new_runtime(tmp_path, requirement, fixture_payload):
    project_id = "project-agent03-cross-process"
    dft_requirement = requirement.model_copy(
        update={"budget": requirement.budget.model_copy(update={"allow_dft": True})}
    )
    requirement_row, manifest = _complete_source_run(
        tmp_path, dft_requirement, fixture_payload, project_id=project_id, run_id="run-source"
    )
    stage_input = _stage_input("run-source", requirement_row, manifest)
    registry = _registry(tmp_path / project_id)

    with OrchestratorRuntime.from_workspace(tmp_path, project_id, runner_registry=registry) as runtime:
        view = runtime.start_stage_run(stage=StageId.DFT, stage_input=stage_input, run_id="run-dft-cross")
        approval_id = view.interrupts[0].value["approval_id"]
        waiting = runtime.approve(run_id="run-dft-cross", approval_id=approval_id, decision="approve")
        assert waiting.stage_statuses["agent03"] == "RUNNING"

    with OrchestratorRuntime.from_workspace(tmp_path, project_id, runner_registry=_registry(tmp_path / project_id)) as restarted:
        assert restarted.status("run-dft-cross").stage_statuses["agent03"] == "RUNNING"
        for _ in range(3):
            view = restarted.resume(run_id="run-dft-cross")
        assert view.stage_statuses["agent03"] == "SUCCEEDED"
