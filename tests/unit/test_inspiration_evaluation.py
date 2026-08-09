from __future__ import annotations

import pytest
from pydantic import ValidationError

from material_agent.inspiration.evaluation import (
    EvaluationError,
    GoldEvidenceLabelV1,
    InspirationCasePredictionV1,
    InspirationGoldCaseV1,
    PredictedCandidateV1,
    PredictedEvidenceLabelV1,
    build_inspiration_gold_set,
    evaluate_inspiration_predictions,
)
from material_agent.inspiration.models import EvidenceRelation


def _case() -> InspirationGoldCaseV1:
    return InspirationGoldCaseV1(
        case_id="case-flat-band-1",
        relevant_document_ids=("document-1", "document-2"),
        rejected_document_ids=("document-3", "document-4"),
        evidence_labels=(
            GoldEvidenceLabelV1(
                passage_id="passage-1",
                relation=EvidenceRelation.SUPPORT,
            ),
            GoldEvidenceLabelV1(
                passage_id="passage-2",
                relation=EvidenceRelation.COUNTER,
            ),
        ),
        accepted_bridge_rule_ids=("bridge-1", "bridge-2"),
        rejected_bridge_rule_ids=("bridge-3",),
        accepted_candidate_family_ids=("family-1", "family-2"),
        requested_top_k=3,
    )


def _synthetic_gold_set():
    return build_inspiration_gold_set(
        review_status="SYNTHETIC_TEST_ONLY",
        source_license_notes=(
            "Synthetic CI labels only; no expert or scientific claim.",
        ),
        cases=(_case(),),
    )


def _candidate(
    rank: int,
    *,
    candidate_id: str,
    family_id: str = "family-1",
    structure_sha256: str = "a" * 64,
    strict_group: str = "strict-group-1",
    bridges: tuple[str, ...] = ("bridge-1",),
) -> PredictedCandidateV1:
    return PredictedCandidateV1(
        candidate_id=candidate_id,
        selection_rank=rank,
        family_id=family_id,
        canonical_structure_sha256=structure_sha256,
        strict_structure_group_id=strict_group,
        bridge_rule_ids=bridges,
    )


def _prediction() -> InspirationCasePredictionV1:
    return InspirationCasePredictionV1(
        case_id="case-flat-band-1",
        selected_document_ids=("document-1", "document-3"),
        evidence_labels=(
            PredictedEvidenceLabelV1(
                passage_id="passage-1",
                relation=EvidenceRelation.SUPPORT,
            ),
            PredictedEvidenceLabelV1(
                passage_id="passage-2",
                relation=EvidenceRelation.CONTEXT,
            ),
        ),
        supported_bridge_rule_ids=("bridge-1", "bridge-3"),
        candidates=(
            _candidate(1, candidate_id="candidate-1"),
            _candidate(
                2,
                candidate_id="candidate-2",
                bridges=("bridge-1", "bridge-3"),
            ),
        ),
        underfill_reason_codes=("CANDIDATE_POOL_BELOW_TOP_K",),
    )


def test_synthetic_gold_set_computes_closed_descriptive_metrics_only() -> None:
    gold = _synthetic_gold_set()
    first = evaluate_inspiration_predictions(
        gold_set=gold,
        predictions=(_prediction(),),
    )
    second = evaluate_inspiration_predictions(
        gold_set=gold,
        predictions=(_prediction(),),
    )

    assert first == second
    result = first.case_results[0]
    assert result.document_recall.model_dump() == {
        "numerator": 1,
        "denominator": 2,
        "value": 0.5,
    }
    assert result.document_precision.value == 0.5
    assert result.relation_macro_f1.value == 0.33333333
    assert result.relation_macro_f1.numerator == 1
    assert result.relation_macro_f1.denominator == 3
    assert tuple(row.relation for row in result.relation_confusion) == tuple(
        EvidenceRelation
    )

    confusion = result.bridge_confusion
    assert (
        confusion.true_positive,
        confusion.false_positive,
        confusion.false_negative,
        confusion.true_negative,
    ) == (1, 1, 1, 0)
    assert confusion.recall.value == 0.5
    assert confusion.precision.value == 0.5
    assert confusion.specificity.value == 0.0
    assert confusion.accuracy.model_dump() == {
        "numerator": 1,
        "denominator": 3,
        "value": 0.33333333,
    }

    assert result.candidate_family_coverage.value == 0.5
    assert result.candidate_fill_rate.model_dump() == {
        "numerator": 2,
        "denominator": 3,
        "value": 0.66666667,
    }
    assert result.candidate_underfill_rate.model_dump() == {
        "numerator": 1,
        "denominator": 3,
        "value": 0.33333333,
    }
    assert result.exact_duplicate_rate.value == 0.5
    assert result.strict_duplicate_rate.value == 0.5
    assert result.pairwise_route_diversity.model_dump() == {
        "numerator": 1,
        "denominator": 2,
        "value": 0.5,
    }
    assert result.pairwise_family_diversity.model_dump() == {
        "numerator": 0,
        "denominator": 1,
        "value": 0.0,
    }

    assert first.mean_pairwise_route_diversity.value == 0.5
    assert first.gold_review_status == "SYNTHETIC_TEST_ONLY"
    assert first.uncertainty_method == "DESCRIPTIVE_BOUNDED_CORPUS_NO_CI"
    assert first.evidence_class == "ENGINEERING_EVALUATION"
    assert first.scientific_conclusion is False


def test_adjudicated_gold_set_requires_independent_expert_closure() -> None:
    with pytest.raises(ValidationError, match="two reviewers"):
        build_inspiration_gold_set(
            review_status="ADJUDICATED",
            source_license_notes=("Public metadata labels.",),
            cases=(_case(),),
            reviewer_ids=("expert-a",),
            adjudicator_id=None,
        )

    with pytest.raises(ValidationError, match="independent"):
        build_inspiration_gold_set(
            review_status="ADJUDICATED",
            source_license_notes=("Public metadata labels.",),
            cases=(_case(),),
            reviewer_ids=("expert-a", "expert-b"),
            adjudicator_id="expert-a",
        )

    adjudicated = build_inspiration_gold_set(
        review_status="ADJUDICATED",
        source_license_notes=("Public metadata labels.",),
        cases=(_case(),),
        reviewer_ids=("expert-a", "expert-b"),
        adjudicator_id="expert-c",
    )
    assert adjudicated.review_status == "ADJUDICATED"
    assert adjudicated.reviewer_ids == ("expert-a", "expert-b")
    assert adjudicated.adjudicator_id == "expert-c"


def test_evaluation_requires_exact_case_and_label_closure() -> None:
    with pytest.raises(EvaluationError, match="CASE_CLOSURE_MISMATCH"):
        evaluate_inspiration_predictions(
            gold_set=_synthetic_gold_set(),
            predictions=(),
        )

    unknown_document = _prediction().model_copy(
        update={"selected_document_ids": ("document-1", "document-unknown")}
    )
    with pytest.raises(EvaluationError, match="DOCUMENT_CLOSURE_MISMATCH"):
        evaluate_inspiration_predictions(
            gold_set=_synthetic_gold_set(), predictions=(unknown_document,)
        )

    unknown_evidence = _prediction().model_copy(
        update={
            "evidence_labels": (
                PredictedEvidenceLabelV1(
                    passage_id="passage-unknown",
                    relation=EvidenceRelation.SUPPORT,
                ),
            )
        }
    )
    with pytest.raises(EvaluationError, match="EVIDENCE_CLOSURE_MISMATCH"):
        evaluate_inspiration_predictions(
            gold_set=_synthetic_gold_set(), predictions=(unknown_evidence,)
        )


def test_bridge_labels_are_closed_and_candidate_routes_must_be_supported() -> None:
    unknown_bridge = _prediction().model_copy(
        update={"supported_bridge_rule_ids": ("bridge-1", "bridge-unknown")}
    )
    with pytest.raises(EvaluationError, match="BRIDGE_CLOSURE_MISMATCH"):
        evaluate_inspiration_predictions(
            gold_set=_synthetic_gold_set(), predictions=(unknown_bridge,)
        )

    unsupported_candidate_route = _prediction().model_copy(
        update={"supported_bridge_rule_ids": ("bridge-1",)}
    )
    with pytest.raises(EvaluationError, match="CANDIDATE_BRIDGE_UNSUPPORTED"):
        evaluate_inspiration_predictions(
            gold_set=_synthetic_gold_set(),
            predictions=(unsupported_candidate_route,),
        )


def test_strict_round_trip_rejects_model_copy_validator_bypass() -> None:
    case = _case()
    forged_case = case.model_copy(
        update={"rejected_bridge_rule_ids": ("bridge-1",)}
    )
    with pytest.raises(EvaluationError, match="INVALID_GOLD_CASE"):
        build_inspiration_gold_set(
            review_status="SYNTHETIC_TEST_ONLY",
            source_license_notes=("Synthetic labels.",),
            cases=(forged_case,),
        )

    gold = _synthetic_gold_set()
    forged_gold = gold.model_copy(update={"review_status": "ADJUDICATED"})
    with pytest.raises(EvaluationError, match="INVALID_GOLD_SET"):
        evaluate_inspiration_predictions(
            gold_set=forged_gold,
            predictions=(_prediction(),),
        )

    forged_prediction = _prediction().model_copy(
        update={"supported_bridge_rule_ids": ("bridge-1", "bridge-1")}
    )
    with pytest.raises(EvaluationError, match="INVALID_PREDICTIONS"):
        evaluate_inspiration_predictions(
            gold_set=gold,
            predictions=(forged_prediction,),
        )


def test_candidate_count_and_underfill_reason_closure_fail_closed() -> None:
    no_reason = _prediction().model_copy(update={"underfill_reason_codes": ()})
    with pytest.raises(EvaluationError, match="UNDERFILL_REASON_MISMATCH"):
        evaluate_inspiration_predictions(
            gold_set=_synthetic_gold_set(), predictions=(no_reason,)
        )

    four_candidates = _prediction().model_copy(
        update={
            "candidates": tuple(
                _candidate(
                    rank,
                    candidate_id=f"candidate-{rank}",
                    structure_sha256=f"{rank:064x}",
                    strict_group=f"strict-group-{rank}",
                )
                for rank in range(1, 5)
            ),
            "underfill_reason_codes": (),
        }
    )
    with pytest.raises(EvaluationError, match="CANDIDATE_TOP_K_EXCEEDED"):
        evaluate_inspiration_predictions(
            gold_set=_synthetic_gold_set(), predictions=(four_candidates,)
        )
