from __future__ import annotations

from material_agent.ml_screening.adapters import (
    FakeMLModelAdapter,
    FakeMLWorker,
    _assign_readiness_ranks,
)
from material_agent.ml_screening.models import (
    MLCandidateManifestRecord,
    MLCandidateResult,
    MLDecision,
    MLScreeningRequest,
)


def _artifacts_for(
    candidates,
    *,
    ml_plan_factory,
    ml_model,
    ml_health,
    ml_policy,
    request=None,
):
    plan = ml_plan_factory(candidates, request=request)
    return FakeMLWorker(
        adapter=FakeMLModelAdapter(model=ml_model, health=ml_health),
        policy=ml_policy,
    ).generate_artifacts(plan)


def _as_real_l2(record: MLCandidateManifestRecord) -> MLCandidateManifestRecord:
    payload = record.candidate.model_dump(mode="json")
    identity = payload["execution_identity"]
    identity["is_mock"] = False
    payload["applicability"]["eligible_for_real_inference"] = True
    payload["applicability"]["eligible_for_l2"] = True
    payload["execution_status"] = "CONVERGED"
    payload["decision"] = "PASS"
    payload["evidence_level"] = "L2_ML_SCREENED"
    payload["relaxation_result"]["execution_identity"] = identity
    payload["relaxation_result"]["is_mock"] = False
    payload["relaxation_result"]["provenance"] = {"is_mock": True}
    payload["structure_lineage"]["is_mock"] = False
    for prop in payload["ml_properties"]:
        prop["is_mock"] = False
        prop["evidence_level"] = "L2_ML_SCREENED"
        prop["output_structure_id"] = payload["relaxation_result"][
            "output_structure_id"
        ]
    payload["recommended_downstream_structure_id"] = payload[
        "relaxation_result"
    ]["output_structure_id"]
    return MLCandidateManifestRecord(
        candidate=MLCandidateResult.model_validate(payload),
        scientific_rank=record.scientific_rank,
    )


def test_downstream_readiness_uses_all_five_groups_in_frozen_order(
    ml_candidate_factory,
    ml_plan_factory,
    ml_model,
    ml_health,
    ml_policy,
) -> None:
    real = _as_real_l2(
        _artifacts_for(
            [ml_candidate_factory("cand-real", rank=5)],
            ml_plan_factory=ml_plan_factory,
            ml_model=ml_model,
            ml_health=ml_health,
            ml_policy=ml_policy,
        ).manifest[0]
    )
    budget_artifacts = _artifacts_for(
        [
            ml_candidate_factory("cand-selected", rank=1),
            ml_candidate_factory("cand-budget", rank=2),
        ],
        request=MLScreeningRequest(max_candidates=1),
        ml_plan_factory=ml_plan_factory,
        ml_model=ml_model,
        ml_health=ml_health,
        ml_policy=ml_policy,
    )
    budget = next(
        item
        for item in budget_artifacts.manifest
        if item.candidate.candidate_id == "cand-budget"
    )
    uncertain = _artifacts_for(
        [ml_candidate_factory("cand-uncertain", rank=3)],
        ml_plan_factory=ml_plan_factory,
        ml_model=ml_model,
        ml_health=ml_health,
        ml_policy=ml_policy,
    ).manifest[0]
    domain = _artifacts_for(
        [
            ml_candidate_factory(
                "cand-domain",
                rank=1,
                elements=["C", "O"],
            )
        ],
        ml_plan_factory=ml_plan_factory,
        ml_model=ml_model,
        ml_health=ml_health,
        ml_policy=ml_policy,
    ).manifest[0]
    failed = _artifacts_for(
        [
            ml_candidate_factory(
                "cand-failed",
                rank=1,
                decision=MLDecision.FAILED,
            )
        ],
        ml_plan_factory=ml_plan_factory,
        ml_model=ml_model,
        ml_health=ml_health,
        ml_policy=ml_policy,
    ).manifest[0]

    ranked = _assign_readiness_ranks(
        [failed, uncertain, domain, budget, real]
    )
    ordered = sorted(
        ranked,
        key=lambda item: item.downstream_readiness_rank or 0,
    )
    assert [item.candidate.candidate_id for item in ordered] == [
        "cand-real",
        "cand-budget",
        "cand-uncertain",
        "cand-domain",
        "cand-failed",
    ]


def test_readiness_ties_use_scientific_rank_then_candidate_id_and_missing_last(
    ml_candidate_factory,
    ml_plan_factory,
    ml_model,
    ml_health,
    ml_policy,
) -> None:
    artifacts = _artifacts_for(
        [
            ml_candidate_factory("cand-z", rank=None),
            ml_candidate_factory("cand-b", rank=2),
            ml_candidate_factory("cand-a", rank=2),
        ],
        request=MLScreeningRequest(max_candidates=1),
        ml_plan_factory=ml_plan_factory,
        ml_model=ml_model,
        ml_health=ml_health,
        ml_policy=ml_policy,
    )
    records = []
    for record in artifacts.manifest:
        payload = record.candidate.model_dump(mode="json")
        payload["selection_status"] = "NOT_SELECTED_BUDGET"
        payload["execution_status"] = "NOT_RUN"
        payload["candidate_operation_key"] = None
        payload["ml_properties"] = []
        payload["relaxation_result"] = None
        payload["structure_lineage"] = None
        payload["decision"] = "UNCERTAIN"
        records.append(
            MLCandidateManifestRecord(
                candidate=MLCandidateResult.model_validate(payload),
                scientific_rank=record.scientific_rank,
            )
        )
    ranked = _assign_readiness_ranks(records)
    ordered = sorted(
        ranked,
        key=lambda item: item.downstream_readiness_rank or 0,
    )
    assert [item.candidate.candidate_id for item in ordered] == [
        "cand-a",
        "cand-b",
        "cand-z",
    ]
