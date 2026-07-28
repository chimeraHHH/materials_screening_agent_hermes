from __future__ import annotations

import json
from pathlib import Path

from material_agent.many_body.models import package_content_hash
from material_agent.many_body.mock_backend import MockManyBodyBackend
from material_agent.many_body.models import EffectiveModelPackage
from material_agent.many_body.runner import ManyBodyStageRunner
from material_agent.orchestrator.models import (
    ArtifactPointer,
    StageCapability,
    StageExecutionContext,
    StageId,
)
from material_agent.retrieval.storage import LocalArtifactStore


FIXTURES = Path(__file__).parents[1] / "fixtures/contracts/agent04-v1"


def _context(store: LocalArtifactStore, *, name: str = "one-dimensional-hubbard.json"):
    ref = store.write_bytes("models/model.json", (FIXTURES / name).read_bytes(), immutable=True)
    capability = StageCapability(
        stage=StageId.MANY_BODY,
        agent_id="agent04",
        registered=True,
        is_mock=True,
        required_inputs=["requirement", "model_package"],
        requires_approval=True,
        supports_external=True,
    )
    return StageExecutionContext(
        project_id="agent04-project",
        run_id="run-many-body",
        stage=StageId.MANY_BODY,
        agent_id="agent04",
        attempt=1,
        requirement_revision=1,
        requirement_artifact=ArtifactPointer(uri="artifact://requirements/r.json", sha256="0" * 64),
        input_artifacts={"model_package": ArtifactPointer(uri=ref.uri, sha256=ref.sha256)},
        capability=capability,
        input_snapshot=ArtifactPointer(uri="artifact://plans/run-many-body/input.json", sha256="1" * 64),
    ), capability


def test_many_body_runner_freezes_plan_and_waits_then_completes(tmp_path):
    store = LocalArtifactStore(tmp_path)
    context, capability = _context(store)
    backend = MockManyBodyBackend(scenario="queued_running_success")
    runner = ManyBodyStageRunner(artifact_store=store, capability=capability, backend=backend)

    assert runner.validate_input(context).valid
    prepared = runner.prepare(context)
    assert prepared.approval_required is True
    assert "无科学数值" in prepared.risk_summary
    native_plan = store.read_json(prepared.native_plan_uri)
    assert native_plan["model"]["model_id"] == "em_fixture_1d_chain"
    assert native_plan["routing"]["recommended_solver_id"] == "mock-many-body/v1"
    assert native_plan["resource_estimate"] == {
        "resource_class": "CONTROL_ONLY",
        "is_mock": True,
        "num_sites": 2,
        "active_orbitals": 2,
    }
    assert native_plan["approval_payload"]["evidence_ceiling"] == "L1_RETRIEVED"
    assert backend._jobs == {}
    assert runner.prepare(context) == prepared

    waiting = runner.start(context, prepared, "project:run:agent04:ed:input")
    assert waiting.outcome.value == "WaitingExternal"
    assert waiting.status.value == "RUNNING"
    assert len(backend._jobs) == 1
    assert runner.start(context, prepared, "project:run:agent04:ed:input").external_job_ref == waiting.external_job_ref

    waiting = runner.reconcile(context, prepared, waiting.external_job_ref, waiting.idempotency_key)
    assert waiting.outcome.value == "WaitingExternal"
    waiting = runner.reconcile(context, prepared, waiting.external_job_ref, waiting.idempotency_key)
    assert waiting.outcome.value == "WaitingExternal"
    completed = runner.reconcile(context, prepared, waiting.external_job_ref, waiting.idempotency_key)
    assert completed.status.value == "SUCCEEDED"
    payload = store.read_json(completed.native_result_uri)
    assert payload["envelope"]["is_mock"] is True
    assert payload["envelope"]["solver_validation_status"] == "MOCK_ONLY"
    assert payload["envelope"]["evidence_level"] != "L4_MANY_BODY_VALIDATED"
    assert payload["observables"] == []


def test_many_body_runner_rehydrates_without_submit_after_process_restart(tmp_path):
    store = LocalArtifactStore(tmp_path)
    context, capability = _context(store, name="two-dimensional-2x2-hubbard.json")
    first = ManyBodyStageRunner(
        artifact_store=store,
        capability=capability,
        backend=MockManyBodyBackend(scenario="queued_running_success"),
    )
    prepared = first.prepare(context)
    waiting = first.start(context, prepared, "durable-key")

    restarted_backend = MockManyBodyBackend(scenario="queued_running_success")
    restarted = ManyBodyStageRunner(artifact_store=store, capability=capability, backend=restarted_backend)
    resumed = restarted.reconcile(context, prepared, waiting.external_job_ref, waiting.idempotency_key)
    assert resumed.outcome.value == "WaitingExternal"
    resumed = restarted.reconcile(context, prepared, waiting.external_job_ref, waiting.idempotency_key)
    assert resumed.outcome.value == "WaitingExternal"
    completed = restarted.reconcile(context, prepared, waiting.external_job_ref, waiting.idempotency_key)
    assert completed.status.value == "SUCCEEDED"
    assert len(restarted_backend._jobs) == 1
    assert store.exists(completed.native_result_uri)


def test_many_body_runner_rejects_missing_and_tampered_inputs_before_prepare(tmp_path):
    store = LocalArtifactStore(tmp_path)
    context, capability = _context(store)
    runner = ManyBodyStageRunner(artifact_store=store, capability=capability)
    missing = context.model_copy(update={"input_artifacts": {}})
    result = runner.validate_input(missing)
    assert result.error_code == "MISSING_INPUT"
    assert result.failure_status.value == "BLOCKED_MISSING_INPUT"
    assert runner.backend._jobs == {}

    (tmp_path / "models/model.json").write_text("tampered", encoding="utf-8")
    result = runner.validate_input(context)
    assert not result.valid
    assert result.error_code in {"SCHEMA_INVALID", "INPUT_INTEGRITY_ERROR", "PERMANENT_FAILED"}
    assert runner.backend._jobs == {}


def test_many_body_runner_2d_fixture_is_ready_and_has_no_duplicate_edges(tmp_path):
    store = LocalArtifactStore(tmp_path)
    context, capability = _context(store, name="two-dimensional-2x2-hubbard.json")
    runner = ManyBodyStageRunner(artifact_store=store, capability=capability)

    validation = runner.validate_input(context)
    assert validation.valid
    prepared = runner.prepare(context)
    native_plan = store.read_json(prepared.native_plan_uri)
    package = EffectiveModelPackage.model_validate(
        store.read_json(context.input_artifacts["model_package"].uri)
    )
    edges = {(edge.source_site, edge.target_site) for edge in package.geometry.edges}
    assert len(edges) == len(package.geometry.edges) == 4
    assert package.geometry.dimension == 2
    assert native_plan["resource_estimate"]["num_sites"] == 4
    assert native_plan["approval_payload"]["routing_decision"]["status"] == "READY"


def test_many_body_missing_physics_does_not_create_plan_or_backend_job(tmp_path):
    store = LocalArtifactStore(tmp_path)
    context, capability = _context(store)
    payload = store.read_json(context.input_artifacts["model_package"].uri)
    payload["interactions"] = []
    bad_ref = store.write_json("models/missing-u.json", payload, immutable=True)
    bad_context = context.model_copy(
        update={"input_artifacts": {"model_package": ArtifactPointer(uri=bad_ref.uri, sha256=bad_ref.sha256)}}
    )
    runner = ManyBodyStageRunner(artifact_store=store, capability=capability)

    result = runner.validate_input(bad_context)
    assert result.valid is False
    assert result.failure_status.value == "BLOCKED_MISSING_INPUT"
    assert result.error_code == "MISSING_HUBBARD_U"
    assert runner.backend._jobs == {}
    assert not (tmp_path / "plans").exists()


def test_many_body_result_hash_mismatch_fails_closed(tmp_path):
    store = LocalArtifactStore(tmp_path)
    context, capability = _context(store)
    runner = ManyBodyStageRunner(
        artifact_store=store,
        capability=capability,
        backend=MockManyBodyBackend(scenario="result_hash_mismatch"),
    )
    prepared = runner.prepare(context)
    waiting = runner.start(context, prepared, "bad-result")
    waiting = runner.reconcile(context, prepared, waiting.external_job_ref, waiting.idempotency_key)
    waiting = runner.reconcile(context, prepared, waiting.external_job_ref, waiting.idempotency_key)
    failed = runner.reconcile(context, prepared, waiting.external_job_ref, waiting.idempotency_key)
    assert failed.status.value == "PERMANENT_FAILED"
    assert failed.errors[0].category == "BACKEND_INCONSISTENT"
