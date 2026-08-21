from __future__ import annotations

import shutil
from datetime import UTC, datetime
from pathlib import Path

from material_agent.ml_screening.adapters import FakeMLModelAdapter, FakeMLWorker
from material_agent.ml_screening.resources import (
    default_policy,
    fake_health_snapshot,
    fake_model_spec,
)
from material_agent.ml_screening.runner import Agent02RunnerAdapter
from material_agent.orchestrator.models import (
    ArtifactPointer,
    StageCapability,
    StageExecutionContext,
    StageId,
    StageStatus,
)
from material_agent.orchestrator.runners import (
    StageRunnerRegistry,
    default_capabilities,
)
from material_agent.retrieval.storage import LocalArtifactStore

FIXTURE = Path(__file__).parents[1] / "fixtures/contracts/agent02-v1"


def _setup(tmp_path: Path, count: int = 1, *, fail_once: set[str] | None = None):
    store = LocalArtifactStore(tmp_path)
    for source, target in [
        ("requirements/requirement.v1.json", "requirements/req.json"),
        ("policies/ml-screening-policy-v1.json", "policies/policy.json"),
        ("models/model-registry-v1.json", "models/registry.json"),
        ("models/fake-health.json", "models/health.json"),
        ("upstream/str_contract_si_o.cif", "upstream/structure.cif"),
    ]:
        destination = tmp_path / target
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(FIXTURE / source, destination)
    structure = store.inspect("artifact://upstream/structure.cif")
    rows = []
    for index in range(1, count + 1):
        rows.append(
            {
                "schema_version": "agent01-contract-v1",
                "candidate_id": f"cand-{index}",
                "decision": "PASS",
                "publication_rank": index,
                "formula": "SiO",
                "elements": ["O", "Si"],
                "num_sites": 2,
                "properties": [
                    {"name": "band_gap", "value": 0.8, "unit": "eV"},
                    {"name": "energy_above_hull", "value": 0.01, "unit": "eV/atom"},
                    {"name": "is_metal", "value": False, "unit": "dimensionless"},
                    {"name": "structural_dimensionality", "value": 3, "unit": "dimensionless"},
                ],
                "structure_id": f"struct-{index}",
                "structure_artifact_uri": structure.uri,
                "structure_artifact_sha256": structure.sha256,
            }
        )
    manifest = store.write_jsonl("upstream/manifest.jsonl", rows)
    requirement = store.inspect("artifact://requirements/req.json")
    refs = {
        name: store.inspect(uri)
        for name, uri in {
            "policy": "artifact://policies/policy.json",
            "registry": "artifact://models/registry.json",
            "health": "artifact://models/health.json",
        }.items()
    }
    snapshot = store.write_json("inputs/run/input.json", {"manifest": manifest.sha256})
    capability = StageCapability(stage=StageId.ML, agent_id="agent02", registered=True, is_mock=True, required_inputs=["requirement", "candidate_manifest"])
    worker = FakeMLWorker(
        adapter=FakeMLModelAdapter(model=fake_model_spec(), health=fake_health_snapshot()),
        policy=default_policy(),
        fail_once_candidate_ids=fail_once,
    )
    context = StageExecutionContext(
        project_id="project-test", run_id="run-ml", stage=StageId.ML,
        agent_id="agent02", attempt=1, requirement_revision=1,
        requirement_artifact=ArtifactPointer(uri=requirement.uri, sha256=requirement.sha256),
        input_artifacts={"candidate_manifest": ArtifactPointer(uri=manifest.uri, sha256=manifest.sha256), **{name: ArtifactPointer(uri=ref.uri, sha256=ref.sha256) for name, ref in refs.items()}},
        capability=capability,
        input_snapshot=ArtifactPointer(uri=snapshot.uri, sha256=snapshot.sha256),
    )
    adapter = Agent02RunnerAdapter(
        artifact_store=store,
        capability=capability,
        worker=worker,
        now=lambda: datetime(2026, 7, 25, 13, tzinfo=UTC),
    )
    return store, adapter, context


def test_adapter_validates_real_artifacts_and_reuses_prepare(tmp_path):
    _store, adapter, context = _setup(tmp_path)
    assert adapter.validate_input(context).valid
    first = adapter.prepare(context)
    second = adapter.prepare(context)
    assert first.native_plan_uri == second.native_plan_uri
    assert first.native_plan_sha256 == second.native_plan_sha256
    assert first.approval_required is False


def test_adapter_blocks_over_limit_before_plan(tmp_path):
    _store, adapter, context = _setup(tmp_path, count=21)
    validation = adapter.validate_input(context)
    assert validation.error_code == "BATCH_LIMIT_EXCEEDED"
    assert validation.failure_status is StageStatus.BLOCKED_MISSING_INPUT
    assert not (tmp_path / "plans").exists()


def test_adapter_writes_real_hashes_and_reuses_completed_start(tmp_path):
    store, adapter, context = _setup(tmp_path)
    prepared = adapter.prepare(context)
    first = adapter.start(context, prepared, "a" * 64)
    second = adapter.start(context, prepared, "a" * 64)
    assert first.status is StageStatus.SUCCEEDED
    assert second.native_result_uri == first.native_result_uri
    assert store.exists_with_hash(first.native_result_uri, first.native_result_sha256)
    assert store.exists("artifact://stages/agent02/run-ml/attempt-1/ml-candidate-manifest.jsonl")


def test_adapter_preserves_partial_failure_and_rejects_tampered_plan(tmp_path):
    _store, adapter, context = _setup(tmp_path, count=2, fail_once={"cand-2"})
    prepared = adapter.prepare(context)
    outcome = adapter.start(context, prepared, "b" * 64)
    assert outcome.status is StageStatus.PARTIAL
    complete = list((tmp_path / "stages/agent02/run-ml/candidate-operations").glob("*/operation-complete.json"))
    assert len(complete) == 1
    plan_path = tmp_path / prepared.native_plan_uri.removeprefix("artifact://")
    plan_path.write_text("tampered", encoding="utf-8")
    failed = adapter.start(context, prepared, "b" * 64)
    assert failed.status is StageStatus.PERMANENT_FAILED
    assert failed.errors[0].category == "BACKEND_INCONSISTENT"


def test_adapter_maps_an_all_candidate_failure_to_permanent_failed(tmp_path):
    store, adapter, context = _setup(
        tmp_path, count=1, fail_once={"cand-1"}
    )
    prepared = adapter.prepare(context)
    outcome = adapter.start(context, prepared, "d" * 64)
    assert outcome.status is StageStatus.PERMANENT_FAILED
    assert outcome.errors[0].category == "AGENT02_EXECUTION_FAILED"
    assert outcome.native_result_uri is not None
    envelope = store.read_json(outcome.native_result_uri)
    assert envelope["status"] == "PERMANENT_FAILED"
    assert envelope["candidate_summaries"][0]["decision"] == "FAILED"


def test_reconcile_is_explicitly_unsupported(tmp_path):
    _store, adapter, context = _setup(tmp_path)
    prepared = adapter.prepare(context)
    outcome = adapter.reconcile(context, prepared, "external-job", "c" * 64)
    assert outcome.status is StageStatus.PERMANENT_FAILED
    assert outcome.errors[0].category == "UNSUPPORTED_OPERATION"


def test_fake_capability_is_only_registered_by_explicit_test_registry(tmp_path):
    assert default_capabilities()[StageId.ML].registered is False
    _store, adapter, _context = _setup(tmp_path)
    registry = StageRunnerRegistry()
    registry.register(StageId.ML, lambda _context: adapter, adapter.capability)
    assert registry.has_runner(StageId.ML)
    assert registry.capability(StageId.ML).is_mock is True
