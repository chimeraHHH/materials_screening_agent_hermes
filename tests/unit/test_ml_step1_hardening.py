from __future__ import annotations

import hashlib
import json
from datetime import timedelta

import pytest
from pydantic import ValidationError

from material_agent.ml_screening.adapters import (
    FakeMLModelAdapter,
    FakeMLWorker,
    FakeWorkerCoordinator,
)
from material_agent.ml_screening.models import (
    ArtifactRef,
    EvidenceLevel,
    MLCandidateResult,
    MLCandidateResultSummary,
    MLDecision,
    MLScreeningRequest,
    MLStagePlan,
    MLStageResultEnvelope,
    NativeStageStatus,
    SelectionMode,
)
from material_agent.ml_screening.resources import artifact_pointer


@pytest.mark.parametrize("limit", [1, 3, 5])
def test_policy_top_n_uses_the_requested_limit(
    limit,
    ml_candidate_factory,
    ml_plan_factory,
) -> None:
    candidates = [
        ml_candidate_factory(f"cand-{index}", rank=index)
        for index in range(1, 8)
    ]
    plan = ml_plan_factory(
        candidates,
        request=MLScreeningRequest(max_candidates=limit),
    )
    assert plan.selection_limit == limit
    assert plan.requested_candidate_count == limit
    assert plan.inference_candidate_ids == [
        f"cand-{index}" for index in range(1, limit + 1)
    ]
    assert plan.approval_required is False


@pytest.mark.parametrize(
    ("count", "approval_required"),
    [(5, False), (6, True), (20, True)],
)
def test_plan_itself_enforces_explicit_approval_boundary(
    count,
    approval_required,
    ml_candidate_factory,
    ml_plan_factory,
) -> None:
    candidates = [
        ml_candidate_factory(f"cand-{index:02d}", rank=index)
        for index in range(1, count + 1)
    ]
    plan = ml_plan_factory(
        candidates,
        request=MLScreeningRequest(
            selection_mode=SelectionMode.EXPLICIT_IDS,
            requested_candidate_ids=[
                candidate.candidate_id for candidate in candidates
            ],
            max_candidates=count,
        ),
    )
    assert plan.manifest_candidate_count == count
    assert plan.requested_candidate_count == count
    assert plan.approval_required is approval_required
    payload = plan.model_dump(mode="json")
    payload["approval_required"] = not approval_required
    with pytest.raises(ValidationError, match="batch approval"):
        MLStagePlan.model_validate(payload)


def test_twenty_one_candidate_plan_cannot_be_constructed(
    ml_candidate_factory,
    ml_plan_factory,
) -> None:
    candidates = [
        ml_candidate_factory(f"cand-{index:02d}", rank=index)
        for index in range(1, 21)
    ]
    request = MLScreeningRequest(
        selection_mode=SelectionMode.EXPLICIT_IDS,
        requested_candidate_ids=[
            candidate.candidate_id for candidate in candidates
        ],
        max_candidates=20,
    )
    plan = ml_plan_factory(candidates, request=request)
    payload = plan.model_dump(mode="json")
    payload["selection_limit"] = 21
    payload["requested_candidate_count"] = 21
    payload["requested_candidate_ids"].append("cand-21")
    payload["resource_estimate"]["requested_candidate_count"] = 21
    with pytest.raises(ValidationError):
        MLStagePlan.model_validate(payload)
    with pytest.raises(ValidationError):
        MLScreeningRequest(
            selection_mode=SelectionMode.EXPLICIT_IDS,
            requested_candidate_ids=[
                f"cand-{index:02d}" for index in range(1, 22)
            ],
            max_candidates=21,
        )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("manifest_candidate_count", 999),
        ("requested_candidate_count", 0),
        ("selection_limit", 0),
    ],
)
def test_plan_rejects_forged_frozen_counts(
    field,
    value,
    ml_candidate_factory,
    ml_plan_factory,
) -> None:
    plan = ml_plan_factory(
        [
            ml_candidate_factory(f"cand-{index}", rank=index)
            for index in range(1, 4)
        ],
        request=MLScreeningRequest(max_candidates=3),
    )
    payload = plan.model_dump(mode="json")
    payload[field] = value
    with pytest.raises(ValidationError):
        MLStagePlan.model_validate(payload)


def test_plan_rejects_registry_health_mock_disagreement(
    ml_candidate_factory,
    ml_plan_factory,
    ml_registry,
    ml_health,
) -> None:
    health_payload = ml_health.model_dump(mode="json")
    health_payload["is_mock"] = False
    health_payload["installed_package_versions"] = {
        "chgnet": "0.4.2",
        "torch": "fixture",
        "pymatgen": "fixture",
        "ase": "fixture",
        "numpy": "fixture",
        "material-screening-agent": "0.1.0",
    }
    mismatched = type(ml_health).model_validate(health_payload)
    with pytest.raises(ValueError, match="mock identities"):
        ml_plan_factory(
            [ml_candidate_factory()],
            registry=ml_registry,
            health=mismatched,
        )


def test_free_form_provenance_cannot_override_mock_identity(
    ml_candidate_factory,
    ml_plan_factory,
    ml_model,
    ml_health,
    ml_policy,
) -> None:
    plan = ml_plan_factory([ml_candidate_factory()])
    artifacts = FakeMLWorker(
        adapter=FakeMLModelAdapter(model=ml_model, health=ml_health),
        policy=ml_policy,
    ).generate_artifacts(plan)
    payload = artifacts.manifest[0].candidate.model_dump(mode="json")
    payload["ml_properties"] = []
    payload["decision"] = "PASS"
    payload["evidence_level"] = "L2_ML_SCREENED"
    payload["recommended_downstream_structure_id"] = payload[
        "relaxation_result"
    ]["output_structure_id"]
    payload["relaxation_result"]["provenance"] = {"is_mock": False}
    with pytest.raises(ValidationError, match="mock candidate"):
        MLCandidateResult.model_validate(payload)


def test_third_candidate_crash_preserves_two_adapter_side_completions(
    ml_candidate_factory,
    ml_plan_factory,
    ml_model,
    ml_health,
    ml_policy,
) -> None:
    candidates = [
        ml_candidate_factory(f"cand-{index}", rank=index)
        for index in range(1, 4)
    ]
    plan = ml_plan_factory(
        candidates,
        request=MLScreeningRequest(max_candidates=3),
    )
    worker = FakeMLWorker(
        adapter=FakeMLModelAdapter(model=ml_model, health=ml_health),
        policy=ml_policy,
        fail_once_candidate_ids={"cand-3"},
    )
    ledger: dict[str, MLCandidateResult] = {}
    first = FakeWorkerCoordinator(
        worker=worker,
        completion_ledger=ledger,
    )
    with pytest.raises(RuntimeError, match="cand-3"):
        first.execute(plan)
    assert set(ledger) == {
        plan.candidate_operation_keys["cand-1"],
        plan.candidate_operation_keys["cand-2"],
    }

    resumed = FakeWorkerCoordinator(
        worker=worker,
        completion_ledger=ledger,
    )
    artifacts = resumed.execute(plan)
    assert [item.candidate.candidate_id for item in artifacts.manifest] == [
        "cand-1",
        "cand-2",
        "cand-3",
    ]
    assert resumed.worker_invocations["cand-1"] == 0
    assert resumed.worker_invocations["cand-2"] == 0
    assert resumed.worker_invocations["cand-3"] == 1


def test_candidate_operation_key_is_canonical_and_attempt_independent(
    ml_candidate_factory,
    ml_plan_factory,
) -> None:
    candidate = ml_candidate_factory()
    first = ml_plan_factory([candidate], attempt=1)
    retry = ml_plan_factory([candidate], attempt=2)
    key = first.candidate_operation_keys[candidate.candidate_id]
    assert retry.candidate_operation_keys[candidate.candidate_id] == key
    payload = first.model_dump(mode="json")
    payload["candidate_operation_keys"][candidate.candidate_id] = "f" * 64
    with pytest.raises(ValidationError, match="operation key"):
        MLStagePlan.model_validate(payload)


def test_stage_envelope_recomputes_counts_and_cross_validates_plan(
    ml_candidate_factory,
    ml_plan_factory,
    ml_model,
    ml_health,
    ml_policy,
    ml_fixed_time,
) -> None:
    plan = ml_plan_factory([ml_candidate_factory()])
    artifacts = FakeMLWorker(
        adapter=FakeMLModelAdapter(model=ml_model, health=ml_health),
        policy=ml_policy,
    ).generate_artifacts(plan)
    manifest_bytes = (
        "\n".join(
            json.dumps(
                record.model_dump(mode="json"),
                sort_keys=True,
                separators=(",", ":"),
            )
            for record in artifacts.manifest
        )
        + "\n"
    ).encode()
    plan_pointer = artifact_pointer("artifact://plan.json", plan)
    envelope = MLStageResultEnvelope(
        project_id=plan.project_id,
        run_id=plan.run_id,
        status=NativeStageStatus.SUCCEEDED,
        operation_key="stage-operation-key",
        plan=plan_pointer,
        execution_identity=plan.execution_identity,
        candidate_manifest=ArtifactRef(
            uri="artifact://manifest.jsonl",
            sha256=hashlib.sha256(manifest_bytes).hexdigest(),
            size_bytes=len(manifest_bytes),
            media_type="application/x-ndjson",
        ),
        output_artifacts=[],
        candidate_ids=[
            record.candidate.candidate_id for record in artifacts.manifest
        ],
        candidate_summaries=[
            MLCandidateResultSummary.from_result(record.candidate)
            for record in artifacts.manifest
        ],
        status_counts={"UNCERTAIN": 1},
        started_at=ml_fixed_time,
        finished_at=ml_fixed_time + timedelta(seconds=1),
    )
    envelope.validate_against(
        plan=plan,
        manifest=artifacts.manifest,
        operation_key="stage-operation-key",
    )
    invalid = envelope.model_dump(mode="json")
    invalid["status_counts"] = {"PASS": 1}
    with pytest.raises(ValidationError, match="status_counts"):
        MLStageResultEnvelope.model_validate(invalid)
    invalid = envelope.model_dump(mode="json")
    invalid["finished_at"] = (
        ml_fixed_time - timedelta(seconds=1)
    ).isoformat()
    with pytest.raises(ValidationError, match="finished_at"):
        MLStageResultEnvelope.model_validate(invalid)
