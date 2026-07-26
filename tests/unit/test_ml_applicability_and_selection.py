from __future__ import annotations

from datetime import UTC, datetime

import pytest

from material_agent.ml_screening.applicability import assess_applicability
from material_agent.ml_screening.models import (
    ApplicabilityReasonCode,
    ApplicabilityStatus,
    ArtifactPointer,
    MLModelRegistry,
    MLModelSpec,
    MLScreeningRequest,
    SelectionMode,
    SelectionStatus,
    SmokeTestStatus,
)
from material_agent.ml_screening.planner import build_ml_stage_plan
from material_agent.ml_screening.resources import (
    artifact_pointer,
    fake_model_card,
)


def _pointer(name: str) -> ArtifactPointer:
    return artifact_pointer(f"artifact://fixtures/{name}.json", {"name": name})


def _plan(
    *,
    candidates,
    request,
    ml_requirement,
    ml_policy,
    ml_registry,
    ml_health,
):
    return build_ml_stage_plan(
        project_id="project-ml",
        run_id="run-ml",
        requirement_revision=ml_requirement.revision,
        attempt=1,
        orchestrator_input_snapshot=_pointer("input"),
        requirement_artifact=_pointer("requirement"),
        candidate_manifest_artifact=_pointer("manifest"),
        stage_request_artifact=(
            _pointer("request") if request != MLScreeningRequest() else None
        ),
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
        request=request,
        policy=ml_policy,
        registry=ml_registry,
        health=ml_health,
        created_at=datetime(2026, 7, 26, tzinfo=UTC),
    )


def test_applicable_fake_model_can_be_selected_but_not_l2_eligible(
    ml_candidate_factory,
    ml_model,
    ml_health,
    ml_policy,
) -> None:
    result = assess_applicability(
        ml_candidate_factory(),
        ml_model,
        ml_health,
        ml_policy,
    )
    assert result.status is ApplicabilityStatus.APPLICABLE
    assert result.eligible_for_real_inference is False
    assert result.eligible_for_l2 is False


@pytest.mark.parametrize(
    ("candidate_kwargs", "reason"),
    [
        (
            {"dimensionality": 2},
            ApplicabilityReasonCode.DIMENSIONALITY_NOT_BULK,
        ),
        (
            {"num_sites": 101},
            ApplicabilityReasonCode.NUM_SITES_EXCEEDED,
        ),
        (
            {"elements": ["C", "O"]},
            ApplicabilityReasonCode.UNSUPPORTED_ELEMENT,
        ),
        (
            {"is_periodic": False},
            ApplicabilityReasonCode.NON_PERIODIC_STRUCTURE,
        ),
        (
            {"minimum_distance": 0.49},
            ApplicabilityReasonCode.ATOM_OVERLAP,
        ),
    ],
)
def test_applicability_blocks_known_out_of_domain_cases(
    candidate_kwargs,
    reason,
    ml_candidate_factory,
    ml_model,
    ml_health,
    ml_policy,
) -> None:
    result = assess_applicability(
        ml_candidate_factory(**candidate_kwargs),
        ml_model,
        ml_health,
        ml_policy,
    )
    assert result.status is ApplicabilityStatus.NOT_APPLICABLE
    assert reason in result.reasons


def test_incomplete_element_coverage_yields_unknown(
    ml_candidate_factory,
    ml_model,
    ml_health,
    ml_policy,
) -> None:
    payload = ml_model.model_dump(mode="json")
    payload["element_coverage_complete"] = False
    model = MLModelSpec.model_validate(payload)
    result = assess_applicability(
        ml_candidate_factory(elements=["C", "O"]),
        model,
        ml_health,
        ml_policy,
    )
    assert result.status is ApplicabilityStatus.UNKNOWN
    assert (
        ApplicabilityReasonCode.ELEMENT_COVERAGE_UNKNOWN in result.reasons
    )


def test_failed_health_snapshot_blocks_applicability(
    ml_candidate_factory,
    ml_model,
    ml_health,
    ml_policy,
) -> None:
    payload = ml_health.model_dump(mode="json")
    payload["smoke_test_status"] = SmokeTestStatus.FAIL
    failed_health = type(ml_health).model_validate(payload)
    result = assess_applicability(
        ml_candidate_factory(),
        ml_model,
        failed_health,
        ml_policy,
    )
    assert result.status is ApplicabilityStatus.NOT_APPLICABLE
    assert ApplicabilityReasonCode.MODEL_HEALTH_FAILED in result.reasons


def test_policy_top_five_skips_ood_without_consuming_budget(
    ml_candidate_factory,
    ml_requirement,
    ml_policy,
    ml_registry,
    ml_health,
) -> None:
    candidates = [
            ml_candidate_factory(
                f"cand-{index}",
                rank=index,
                elements=(
                    ["C", "O", "Si"] if index == 1 else ["O", "Si"]
                ),
            )
        for index in range(1, 8)
    ]
    plan = _plan(
        candidates=candidates,
        request=MLScreeningRequest(),
        ml_requirement=ml_requirement,
        ml_policy=ml_policy,
        ml_registry=ml_registry,
        ml_health=ml_health,
    )
    assert plan.inference_candidate_ids == [
        "cand-2",
        "cand-3",
        "cand-4",
        "cand-5",
        "cand-6",
    ]
    status = {
        item.candidate.candidate_id: item.selection_status
        for item in plan.planned_candidates
    }
    assert status["cand-1"] is SelectionStatus.NOT_APPLICABLE
    assert status["cand-7"] is SelectionStatus.NOT_SELECTED_BUDGET
    assert plan.approval_required is False


@pytest.mark.parametrize(
    ("count", "approval_required"),
    [(5, False), (6, True), (20, True)],
)
def test_explicit_batch_approval_boundaries(
    count,
    approval_required,
    ml_candidate_factory,
    ml_requirement,
    ml_policy,
    ml_registry,
    ml_health,
) -> None:
    candidates = [
        ml_candidate_factory(f"cand-{index}", rank=index)
        for index in range(1, count + 1)
    ]
    request = MLScreeningRequest(
        selection_mode=SelectionMode.EXPLICIT_IDS,
        requested_candidate_ids=[
            candidate.candidate_id for candidate in candidates
        ],
        max_candidates=count,
    )
    plan = _plan(
        candidates=candidates,
        request=request,
        ml_requirement=ml_requirement,
        ml_policy=ml_policy,
        ml_registry=ml_registry,
        ml_health=ml_health,
    )
    assert plan.approval_required is approval_required
    assert len(plan.inference_candidate_ids) == count


def test_explicit_unknown_candidate_is_rejected_before_plan(
    ml_candidate_factory,
    ml_requirement,
    ml_policy,
    ml_registry,
    ml_health,
) -> None:
    request = MLScreeningRequest(
        selection_mode=SelectionMode.EXPLICIT_IDS,
        requested_candidate_ids=["missing"],
        max_candidates=1,
    )
    with pytest.raises(ValueError, match="not in manifest"):
        _plan(
            candidates=[ml_candidate_factory()],
            request=request,
            ml_requirement=ml_requirement,
            ml_policy=ml_policy,
            ml_registry=ml_registry,
            ml_health=ml_health,
        )


def test_plan_rejects_model_health_hash_mismatch(
    ml_candidate_factory,
    ml_requirement,
    ml_policy,
    ml_registry,
    ml_health,
) -> None:
    payload = ml_health.model_dump(mode="json")
    payload["checkpoint_sha256"] = "f" * 64
    mismatched = type(ml_health).model_validate(payload)
    with pytest.raises(ValueError, match="checkpoint hash"):
        _plan(
            candidates=[ml_candidate_factory()],
            request=MLScreeningRequest(),
            ml_requirement=ml_requirement,
            ml_policy=ml_policy,
            ml_registry=ml_registry,
            ml_health=mismatched,
        )


def test_model_card_hash_is_bound_into_registry(ml_model) -> None:
    card = fake_model_card()
    assert ml_model.model_card_sha256 == artifact_pointer(
        ml_model.model_card_uri,
        card,
    ).sha256
