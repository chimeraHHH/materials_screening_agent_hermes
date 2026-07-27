from __future__ import annotations

import hashlib
import json
import shutil
from datetime import UTC, datetime
from pathlib import Path

from material_agent.ml_screening.adapters import FakeMLModelAdapter, FakeMLWorker
from material_agent.ml_screening.resources import default_policy, fake_health_snapshot, fake_model_spec
from material_agent.ml_screening.runner import Agent02RunnerAdapter
from material_agent.orchestrator.models import StageCapability, StageId
from material_agent.orchestrator.runners import StageRunnerRegistry
from material_agent.orchestrator.runtime import OrchestratorRuntime

from tests.integration.test_orchestrator_p01 import _complete_source_run, _stage_input


FIXTURE = Path(__file__).parents[1] / "fixtures/contracts/agent02-v1"


def _registry(project_root: Path) -> StageRunnerRegistry:
    capability = StageCapability(
        stage=StageId.ML, agent_id="agent02", registered=True, is_mock=True,
        required_inputs=["requirement", "candidate_manifest"],
    )
    registry = StageRunnerRegistry()

    def factory(_context):
        from material_agent.retrieval.storage import LocalArtifactStore

        artifact_store = LocalArtifactStore(project_root)
        worker = FakeMLWorker(
            adapter=FakeMLModelAdapter(model=fake_model_spec(), health=fake_health_snapshot()),
            policy=default_policy(),
        )
        return Agent02RunnerAdapter(
            artifact_store=artifact_store,
            capability=capability,
            worker=worker,
            now=lambda: datetime(2026, 7, 25, 13, tzinfo=UTC),
        )

    registry.register(StageId.ML, factory, capability)
    return registry


def test_run_stage_ml_fake_e2e_uses_explicit_test_registry(tmp_path, requirement, fixture_payload):
    project_id = "project-agent02-p02-e2e"
    requirement_row, manifest = _complete_source_run(
        tmp_path, requirement, fixture_payload,
        project_id=project_id, run_id="run-source",
    )
    project_root = tmp_path / project_id
    for source, target in [
        ("requirements/requirement.v1.json", "requirements/ml-request.json"),
        ("policies/ml-screening-policy-v1.json", "policies/ml-policy.json"),
        ("models/model-registry-v1.json", "models/ml-registry.json"),
        ("models/fake-health.json", "models/ml-health.json"),
    ]:
        destination = project_root / target
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(FIXTURE / source, destination)
    from material_agent.retrieval.storage import LocalArtifactStore

    store = LocalArtifactStore(project_root)
    stage_input = _stage_input("run-source", requirement_row, manifest)
    for name, relative in {
        "policy": "policies/ml-policy.json",
        "registry": "models/ml-registry.json",
        "health": "models/ml-health.json",
    }.items():
        ref = store.inspect(f"artifact://{relative}")
        stage_input["artifacts"][name] = {"uri": ref.uri, "sha256": ref.sha256}
    with OrchestratorRuntime.from_workspace(
        tmp_path, project_id, runner_registry=_registry(project_root)
    ) as runtime:
        result = runtime.start_stage_run(
            stage=StageId.ML, stage_input=stage_input, run_id="run-ml-fake-e2e"
        )
        assert result.status.value == "SUCCEEDED", {
            "errors": result.errors,
            "warnings": result.warnings,
            "stages": result.stage_statuses,
        }
        stage_result = runtime.store.read_json(
            "artifact://stages/agent02/run-ml-fake-e2e/attempt-1/stage-result.json"
        )
    assert result.status.value == "SUCCEEDED"
    assert stage_result["provenance"]["is_mock"] is True
    assert stage_result["candidate_manifest"]["sha256"]


def test_l2_target_full_graph_does_not_treat_fake_result_as_l2(
    tmp_path, requirement, fixture_payload
):
    project_id = "project-agent02-p02-l2-gate"
    payload = requirement.model_dump(mode="json")
    payload["budget"]["allow_ml"] = True
    payload["scientific_targets"] = [
        {"name": "ML screening evidence", "required_evidence_level": "L2_ML_SCREENED"}
    ]
    OrchestratorRuntime.create_project(tmp_path, project_id)
    with OrchestratorRuntime.from_workspace(
        tmp_path, project_id, runner_registry=_registry(tmp_path / project_id)
    ) as runtime:
        waiting = runtime.start_run(
            raw_request="structured L2 fixture request",
            initial_requirement=payload,
            fixture_payload=fixture_payload,
            run_id="run-l2-gate",
        )
        completed = runtime.approve(
            run_id="run-l2-gate",
            approval_id=waiting.interrupts[0].value["approval_id"],
            decision="approve",
        )
    # The remaining required downstream stages are unavailable; importantly,
    # the Fake Agent02 result is not allowed to satisfy the L2 requirement.
    assert completed.status.value == "FAILED"
    assert completed.stage_statuses["agent02"] == "BLOCKED_MISSING_INPUT"
