from __future__ import annotations

from datetime import UTC, datetime

import pytest

from material_agent.ml_screening.adapters import (
    FakeMLModelAdapter,
    FakeMLWorker,
    MLModelAdapter,
)
from material_agent.ml_screening.models import (
    ArtifactPointer,
    EvidenceLevel,
    MLDecision,
    MLExecutionIdentity,
    MLScreeningRequest,
    SelectionMode,
)
from material_agent.ml_screening.planner import build_ml_stage_plan
from material_agent.ml_screening.resources import artifact_pointer


def _pointer(name: str) -> ArtifactPointer:
    return artifact_pointer(f"artifact://fixtures/{name}.json", {"name": name})


def _build_plan(
    candidates,
    ml_requirement,
    ml_policy,
    ml_registry,
    ml_health,
    request=None,
):
    selected_request = request or MLScreeningRequest()
    return build_ml_stage_plan(
        project_id="project-contract",
        run_id="run-contract",
        requirement_revision=ml_requirement.revision,
        attempt=1,
        orchestrator_input_snapshot=_pointer("input"),
        requirement_artifact=_pointer("requirement"),
        candidate_manifest_artifact=_pointer("manifest"),
        stage_request_artifact=None,
        policy_artifact=artifact_pointer(
            "artifact://fixtures/policy.json",
            ml_policy,
        ),
        registry_artifact=artifact_pointer(
            "artifact://fixtures/registry.json",
            ml_registry,
        ),
        health_artifact=artifact_pointer(
            "artifact://fixtures/health.json",
            ml_health,
        ),
        requirement=ml_requirement,
        candidates=candidates,
        request=selected_request,
        policy=ml_policy,
        registry=ml_registry,
        health=ml_health,
        created_at=datetime(2026, 7, 26, tzinfo=UTC),
    )


def test_fake_adapter_satisfies_protocol(ml_model, ml_health) -> None:
    adapter = FakeMLModelAdapter(model=ml_model, health=ml_health)
    assert isinstance(adapter, MLModelAdapter)
    assert adapter.describe() == ml_model
    assert adapter.healthcheck("fixture") == ml_health


def test_fake_worker_generates_plan_manifest_and_report_without_l2(
    ml_candidate_factory,
    ml_requirement,
    ml_policy,
    ml_registry,
    ml_model,
    ml_health,
) -> None:
    candidates = [
        ml_candidate_factory("cand-b", rank=2),
        ml_candidate_factory("cand-a", rank=1),
    ]
    plan = _build_plan(
        candidates,
        ml_requirement,
        ml_policy,
        ml_registry,
        ml_health,
    )
    adapter = FakeMLModelAdapter(model=ml_model, health=ml_health)
    worker = FakeMLWorker(adapter=adapter, policy=ml_policy)
    artifacts = worker.generate_artifacts(plan)

    assert artifacts.plan == plan
    assert [record.candidate.candidate_id for record in artifacts.manifest] == [
        "cand-a",
        "cand-b",
    ]
    assert all(
        record.candidate.evidence_level is EvidenceLevel.L1_RETRIEVED
        for record in artifacts.manifest
    )
    assert all(
        record.candidate.decision is MLDecision.UNCERTAIN
        for record in artifacts.manifest
    )
    assert all(
        prop.is_mock
        for record in artifacts.manifest
        for prop in record.candidate.ml_properties
    )
    assert all(
        record.candidate.recommended_downstream_structure_id
        == record.candidate.source_structure_id
        for record in artifacts.manifest
    )
    assert "TEST FIXTURE / MOCK" in artifacts.report_markdown
    assert "no real ML inference" in artifacts.report_markdown
    assert adapter.calls["predict_static"] == 2
    assert adapter.calls["relax"] == 2


def test_dry_run_never_invokes_fake_inference(
    ml_candidate_factory,
    ml_requirement,
    ml_policy,
    ml_registry,
    ml_model,
    ml_health,
) -> None:
    request = MLScreeningRequest(allow_real_inference=False)
    plan = _build_plan(
        [ml_candidate_factory()],
        ml_requirement,
        ml_policy,
        ml_registry,
        ml_health,
        request,
    )
    adapter = FakeMLModelAdapter(model=ml_model, health=ml_health)
    worker = FakeMLWorker(adapter=adapter, policy=ml_policy)
    artifacts = worker.generate_artifacts(plan)
    assert adapter.calls["predict_static"] == 0
    assert adapter.calls["relax"] == 0
    assert artifacts.manifest[0].candidate.ml_properties == []
    assert artifacts.manifest[0].candidate.decision is MLDecision.UNCERTAIN


def test_worker_rejects_handshake_mismatch(
    ml_candidate_factory,
    ml_requirement,
    ml_policy,
    ml_registry,
    ml_model,
    ml_health,
) -> None:
    plan = _build_plan(
        [ml_candidate_factory()],
        ml_requirement,
        ml_policy,
        ml_registry,
        ml_health,
    )
    worker = FakeMLWorker(
        adapter=FakeMLModelAdapter(model=ml_model, health=ml_health),
        policy=ml_policy,
    )
    request = worker.request_for_candidate(
        plan,
        plan.inference_candidate_ids[0],
    )
    bad = MLExecutionIdentity.model_validate(
        {
            **request.expected_handshake.model_dump(mode="json"),
            "adapter_version": "wrong",
        }
    )
    malformed = request.model_copy(update={"expected_handshake": bad})
    with pytest.raises(ValueError, match="handshake"):
        worker.run(malformed)


def test_fake_output_is_strict_json_serializable(
    ml_candidate_factory,
    ml_requirement,
    ml_policy,
    ml_registry,
    ml_model,
    ml_health,
) -> None:
    plan = _build_plan(
        [ml_candidate_factory()],
        ml_requirement,
        ml_policy,
        ml_registry,
        ml_health,
    )
    worker = FakeMLWorker(
        adapter=FakeMLModelAdapter(model=ml_model, health=ml_health),
        policy=ml_policy,
    )
    response = worker.run(
        worker.request_for_candidate(plan, plan.inference_candidate_ids[0])
    )
    encoded = response.model_dump_json()
    assert '"is_mock":true' in encoded


def test_explicit_non_requested_candidates_remain_auditable(
    ml_candidate_factory,
    ml_requirement,
    ml_policy,
    ml_registry,
    ml_health,
) -> None:
    candidates = [
        ml_candidate_factory("cand-a", rank=1),
        ml_candidate_factory("cand-b", rank=2),
    ]
    request = MLScreeningRequest(
        selection_mode=SelectionMode.EXPLICIT_IDS,
        requested_candidate_ids=["cand-b"],
        max_candidates=1,
    )
    plan = _build_plan(
        candidates,
        ml_requirement,
        ml_policy,
        ml_registry,
        ml_health,
        request,
    )
    assert plan.inference_candidate_ids == ["cand-b"]
    assert plan.not_selected_candidate_ids == ["cand-a"]
