from __future__ import annotations

import json
import shutil
from datetime import UTC, datetime
from pathlib import Path

from material_agent.dft.mock_backend import MockDFTBackend
from material_agent.dft.runner import DFTStageRunner
from material_agent.orchestrator.models import (
    ArtifactPointer,
    StageCapability,
    StageExecutionContext,
    StageId,
)
from material_agent.orchestrator.runners import StageRunnerRegistry
from material_agent.retrieval.storage import LocalArtifactStore


FIXTURE = Path(__file__).parents[1] / "fixtures/contracts/agent01-v1"


def _setup(tmp_path: Path, scenario: str = "mock_success"):
    store = LocalArtifactStore(tmp_path)
    requirement = json.loads((FIXTURE / "requirements/requirement.v1.json").read_text())
    requirement["budget"]["allow_dft"] = True
    requirement_ref = store.write_json("requirements/requirement.json", requirement)
    structure = store.write_bytes(
        "candidates/structure.cif",
        (FIXTURE / "candidates/structures/str_adb835107dec06a0d9280aa6.cif").read_bytes(),
    )
    row = json.loads(
        (FIXTURE / "stages/agent01/run-contract-fixture/candidate_manifest.jsonl")
        .read_text()
        .splitlines()[0]
    )
    row["structure_artifact_uri"] = structure.uri
    row["structure_artifact_sha256"] = structure.sha256
    manifest = store.write_jsonl("upstream/candidate_manifest.jsonl", [row])
    snapshot = store.write_json("plans/run-dft/input.json", {"manifest": manifest.sha256})
    capability = StageCapability(
        stage=StageId.DFT,
        agent_id="agent03",
        registered=True,
        is_mock=True,
        required_inputs=["requirement", "candidate_manifest"],
        requires_approval=True,
        supports_external=True,
    )
    context = StageExecutionContext(
        project_id="project-dft",
        run_id="run-dft",
        stage=StageId.DFT,
        agent_id="agent03",
        attempt=1,
        requirement_revision=1,
        requirement_artifact=ArtifactPointer(uri=requirement_ref.uri, sha256=requirement_ref.sha256),
        input_artifacts={"candidate_manifest": ArtifactPointer(uri=manifest.uri, sha256=manifest.sha256)},
        capability=capability,
        input_snapshot=ArtifactPointer(uri=snapshot.uri, sha256=snapshot.sha256),
    )
    backend = MockDFTBackend(scenario=scenario)
    runner = DFTStageRunner(
        artifact_store=store,
        capability=capability,
        backend=backend,
        now=lambda: datetime(2026, 7, 27, tzinfo=UTC),
    )
    return store, runner, context


def test_dft_runner_freezes_plan_and_waits_then_reconciles(tmp_path):
    store, runner, context = _setup(tmp_path)
    assert runner.validate_input(context).valid
    prepared = runner.prepare(context)
    assert prepared.approval_required is True
    assert runner.prepare(context).native_plan_sha256 == prepared.native_plan_sha256

    first = runner.start(context, prepared, "operation-dft-1")
    assert first.outcome.value == "WaitingExternal"
    assert first.status.value == "RUNNING"
    second = runner.start(context, prepared, "operation-dft-1")
    assert second.external_job_ref == first.external_job_ref

    running = runner.reconcile(context, prepared, first.external_job_ref or "", first.idempotency_key)
    assert running.outcome.value == "WaitingExternal"
    done = runner.reconcile(context, prepared, first.external_job_ref or "", first.idempotency_key)
    assert done.status.value == "RUNNING"
    done = runner.reconcile(context, prepared, first.external_job_ref or "", first.idempotency_key)
    assert done.status.value == "SUCCEEDED"
    result = store.read_json(done.native_result_uri or "")
    assert result["is_mock"] is True
    assert all(claim["status"] == "NOT_EVALUATED_MOCK" for claim in result["claim_results"])
    assert "band_gap" not in json.dumps(result)
    assert "total_energy" not in json.dumps(result)


def test_dft_runner_restores_mock_job_in_new_process_and_detects_tamper(tmp_path):
    store, runner, context = _setup(tmp_path)
    prepared = runner.prepare(context)
    waiting = runner.start(context, prepared, "operation-dft-2")
    # A new adapter/backend instance reconstructs only from durable artifacts.
    restored = DFTStageRunner(
        artifact_store=store,
        capability=runner.capability,
        backend=MockDFTBackend(),
        now=runner.now,
    )
    done = restored.reconcile(context, prepared, waiting.external_job_ref or "", waiting.idempotency_key)
    assert done.status.value == "RUNNING"
    native_path = tmp_path / prepared.native_plan_uri.removeprefix("artifact://")
    native_path.write_text("tampered", encoding="utf-8")
    failed = restored.reconcile(context, prepared, waiting.external_job_ref or "", waiting.idempotency_key)
    assert failed.status.value == "PERMANENT_FAILED"
    assert failed.errors[0].category == "BACKEND_INCONSISTENT"


def test_dft_mock_capability_is_only_explicitly_registered():
    registry = StageRunnerRegistry()
    assert registry.has_runner(StageId.DFT) is False


def test_dft_runner_maps_failure_and_timeout_without_scientific_claims(tmp_path):
    for scenario, expected in (("mock_backend_failed", "PERMANENT_FAILED"), ("mock_timeout", "RETRYABLE_FAILED")):
        store, runner, context = _setup(tmp_path / scenario, scenario)
        prepared = runner.prepare(context)
        waiting = runner.start(context, prepared, f"operation-{scenario}")
        runner.reconcile(context, prepared, waiting.external_job_ref or "", waiting.idempotency_key)
        terminal = runner.reconcile(context, prepared, waiting.external_job_ref or "", waiting.idempotency_key)
        assert terminal.status.value == expected
        assert terminal.summary == {"is_mock": True, "claim_status": "NOT_EVALUATED_MOCK", "message": "Mock lifecycle only; no DFT calculation was executed."}


def test_dft_runner_cancel_is_terminal_and_idempotent(tmp_path):
    _store, runner, context = _setup(tmp_path, "mock_cancel_running")
    prepared = runner.prepare(context)
    waiting = runner.start(context, prepared, "operation-cancel")
    cancelled = runner.cancel(waiting.external_job_ref or "", waiting.idempotency_key)
    assert cancelled.status.value == "CANCELLED"
    again = runner.cancel(waiting.external_job_ref or "", waiting.idempotency_key)
    assert again.status.value == "CANCELLED"
