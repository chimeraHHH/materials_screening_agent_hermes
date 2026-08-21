from __future__ import annotations

from pathlib import Path

from material_agent.many_body.mock_backend import MockManyBodyBackend
from material_agent.many_body.runner import ManyBodyStageRunner
from material_agent.orchestrator.models import StageCapability, StageId
from material_agent.orchestrator.runners import StageRunnerRegistry
from material_agent.orchestrator.runtime import OrchestratorRuntime
from material_agent.retrieval.storage import LocalArtifactStore
from tests.integration.test_orchestrator_p01 import _complete_source_run

FIXTURE = Path(__file__).parents[1] / "fixtures/contracts/agent04-v1/one-dimensional-hubbard.json"


def _registry(project_root: Path, scenario: str = "queued_running_success"):
    capability = StageCapability(
        stage=StageId.MANY_BODY,
        agent_id="agent04",
        registered=True,
        is_mock=True,
        required_inputs=["requirement", "model_package"],
        requires_approval=True,
        supports_external=True,
    )
    registry = StageRunnerRegistry()

    def factory(_context):
        return ManyBodyStageRunner(
            artifact_store=LocalArtifactStore(project_root),
            capability=capability,
            backend=MockManyBodyBackend(scenario=scenario),
        )

    registry.register(StageId.MANY_BODY, factory, capability)
    return registry


def test_many_body_approval_and_resume_survive_runtime_restart(tmp_path, requirement, fixture_payload):
    project_id = "project-agent04-cross-process"
    requirement_row, _ = _complete_source_run(
        tmp_path, requirement, fixture_payload, project_id=project_id, run_id="run-source"
    )
    project_root = tmp_path / project_id
    model_ref = LocalArtifactStore(project_root).write_bytes(
        "models/one-dimensional-hubbard.json", FIXTURE.read_bytes(), immutable=True
    )
    stage_input = {
        "source_run_id": "run-source",
        "requirement_revision": requirement_row["revision"],
        "requirement_artifact_uri": requirement_row["artifact_uri"],
        "requirement_artifact_sha256": requirement_row["artifact_sha256"],
        "artifacts": {"model_package": {"uri": model_ref.uri, "sha256": model_ref.sha256}},
    }

    with OrchestratorRuntime.from_workspace(tmp_path, project_id, runner_registry=_registry(project_root)) as runtime:
        waiting_approval = runtime.start_stage_run(stage=StageId.MANY_BODY, stage_input=stage_input, run_id="run-many-body")
        assert waiting_approval.interrupts
        approval_payload = waiting_approval.interrupts[0].value
        approval = approval_payload["approval_id"]
        assert approval_payload["interaction_type"] == "EXPENSIVE_BATCH_APPROVAL"
        assert approval_payload["payload"]["native_plan_sha256"]
        frozen_plan = runtime.store.read_json(approval_payload["payload"]["native_plan_uri"])
        assert frozen_plan["approval_payload"]["evidence_ceiling"] == "L1_RETRIEVED"
        waiting = runtime.approve(run_id="run-many-body", approval_id=approval, decision="approve")
        assert waiting.stage_statuses["agent04"] == "RUNNING"

    with OrchestratorRuntime.from_workspace(tmp_path, project_id, runner_registry=_registry(project_root)) as restarted:
        assert restarted.status("run-many-body").stage_statuses["agent04"] == "RUNNING"
        assert restarted.status("run-many-body").status.value == "PAUSED"
        for _ in range(3):
            view = restarted.resume(run_id="run-many-body")
        assert view.stage_statuses["agent04"] == "SUCCEEDED"
        state = restarted.graph.get_state(restarted._config("run-many-body")).values
        outcome = state["stage_outcomes"]["agent04"]
        result = restarted.store.read_json(outcome["native_result_uri"])
        assert result["envelope"]["is_mock"] is True
        assert result["envelope"]["solver_validation_status"] == "MOCK_ONLY"
        assert result["observables"] == []
        report = restarted.read_report("run-many-body")
        assert "L4_MANY_BODY_VALIDATED" not in report
        assert "mock" in report.lower()


def test_many_body_rejected_approval_does_not_submit_or_report_scientific_result(
    tmp_path, requirement, fixture_payload
):
    project_id = "project-agent04-reject"
    requirement_row, _ = _complete_source_run(
        tmp_path, requirement, fixture_payload, project_id=project_id, run_id="run-source"
    )
    project_root = tmp_path / project_id
    model_ref = LocalArtifactStore(project_root).write_bytes(
        "models/one-dimensional-hubbard.json", FIXTURE.read_bytes(), immutable=True
    )
    stage_input = {
        "source_run_id": "run-source",
        "requirement_revision": requirement_row["revision"],
        "requirement_artifact_uri": requirement_row["artifact_uri"],
        "requirement_artifact_sha256": requirement_row["artifact_sha256"],
        "artifacts": {"model_package": {"uri": model_ref.uri, "sha256": model_ref.sha256}},
    }

    with OrchestratorRuntime.from_workspace(tmp_path, project_id, runner_registry=_registry(project_root)) as runtime:
        waiting = runtime.start_stage_run(
            stage=StageId.MANY_BODY, stage_input=stage_input, run_id="run-many-body-reject"
        )
        approval = waiting.interrupts[0].value["approval_id"]
        cancelled = runtime.approve(
            run_id="run-many-body-reject", approval_id=approval, decision="reject"
        )
        assert cancelled.stage_statuses["agent04"] == "CANCELLED"
        assert not (project_root / "stages/agent04/operations").exists()
        report = runtime.read_report("run-many-body-reject")
        assert "USER_REJECTED_STAGE" in report
        assert "L4_MANY_BODY_VALIDATED" not in report
