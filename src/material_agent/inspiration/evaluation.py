"""Versioned multi-candidate evaluation with an explicit expert/synthetic boundary.

The metrics in this module are descriptive engineering measurements over one
closed, frozen corpus.  They are not estimates of population performance and
must never be promoted to a scientific or novelty claim.
"""

from __future__ import annotations

from fractions import Fraction
from typing import Annotated, Literal, TypeVar

from pydantic import Field, ValidationError, model_validator

from material_agent.inspiration.models import (
    EvidenceRelation,
    Identifier,
    LongText,
    Sha256,
    StrictModel,
    canonical_sha256,
    deterministic_id,
)


class EvaluationError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(f"{code}: {message}")


class GoldEvidenceLabelV1(StrictModel):
    passage_id: Identifier
    relation: EvidenceRelation


class InspirationGoldCaseV1(StrictModel):
    case_id: Identifier
    relevant_document_ids: Annotated[
        tuple[Identifier, ...], Field(min_length=1, max_length=512)
    ]
    rejected_document_ids: Annotated[
        tuple[Identifier, ...], Field(min_length=1, max_length=2_000)
    ]
    evidence_labels: Annotated[
        tuple[GoldEvidenceLabelV1, ...], Field(min_length=1, max_length=2_000)
    ]
    accepted_bridge_rule_ids: Annotated[
        tuple[Identifier, ...], Field(min_length=1, max_length=128)
    ]
    rejected_bridge_rule_ids: Annotated[
        tuple[Identifier, ...], Field(min_length=1, max_length=128)
    ]
    accepted_candidate_family_ids: Annotated[
        tuple[Identifier, ...], Field(min_length=1, max_length=128)
    ]
    requested_top_k: Annotated[int, Field(ge=1, le=32)]

    @model_validator(mode="after")
    def validate_case(self) -> InspirationGoldCaseV1:
        for label, values in (
            ("relevant documents", self.relevant_document_ids),
            ("rejected documents", self.rejected_document_ids),
            ("accepted bridges", self.accepted_bridge_rule_ids),
            ("rejected bridges", self.rejected_bridge_rule_ids),
            ("candidate families", self.accepted_candidate_family_ids),
        ):
            if values != tuple(sorted(set(values))):
                raise ValueError(f"{label} must be sorted and unique")
        passage_ids = tuple(item.passage_id for item in self.evidence_labels)
        if passage_ids != tuple(sorted(set(passage_ids))):
            raise ValueError("gold evidence labels must be passage-ID sorted and unique")
        if set(self.relevant_document_ids) & set(self.rejected_document_ids):
            raise ValueError("relevant and rejected document labels overlap")
        if set(self.accepted_bridge_rule_ids) & set(self.rejected_bridge_rule_ids):
            raise ValueError("accepted and rejected bridge labels overlap")
        return self


class InspirationGoldSetV1(StrictModel):
    schema_version: Literal["inspiration-expert-gold-set-v1"] = (
        "inspiration-expert-gold-set-v1"
    )
    gold_set_id: Identifier
    review_status: Literal["SYNTHETIC_TEST_ONLY", "ADJUDICATED"]
    reviewer_ids: Annotated[tuple[Identifier, ...], Field(max_length=16)] = ()
    adjudicator_id: Identifier | None = None
    source_license_notes: Annotated[
        tuple[LongText, ...], Field(min_length=1, max_length=64)
    ]
    cases: Annotated[tuple[InspirationGoldCaseV1, ...], Field(min_length=1, max_length=256)]
    gold_set_sha256: Sha256
    scientific_conclusion: Literal[False] = False

    @model_validator(mode="after")
    def validate_gold_set(self) -> InspirationGoldSetV1:
        if self.reviewer_ids != tuple(sorted(set(self.reviewer_ids))):
            raise ValueError("gold-set reviewer IDs must be sorted and unique")
        if len(set(self.source_license_notes)) != len(self.source_license_notes):
            raise ValueError("gold-set source-license notes must be unique")
        case_ids = tuple(item.case_id for item in self.cases)
        if case_ids != tuple(sorted(set(case_ids))):
            raise ValueError("gold-set cases must be case-ID sorted and unique")
        if self.review_status == "ADJUDICATED":
            if len(self.reviewer_ids) < 2 or self.adjudicator_id is None:
                raise ValueError(
                    "adjudicated gold sets require two reviewers and an adjudicator"
                )
            if self.adjudicator_id in self.reviewer_ids:
                raise ValueError("the adjudicator must be independent of the reviewers")
        elif self.reviewer_ids or self.adjudicator_id is not None:
            raise ValueError("synthetic gold sets cannot claim expert identities")
        expected_sha256 = canonical_sha256(
            {
                "adjudicator_id": self.adjudicator_id,
                "cases": self.cases,
                "review_status": self.review_status,
                "reviewer_ids": self.reviewer_ids,
                "schema_version": self.schema_version,
                "source_license_notes": self.source_license_notes,
            }
        )
        if self.gold_set_sha256 != expected_sha256:
            raise ValueError("gold-set SHA-256 does not match its semantic content")
        expected_id = deterministic_id(
            "inspiration-gold-set",
            {
                "gold_set_sha256": self.gold_set_sha256,
                "review_status": self.review_status,
            },
        )
        if self.gold_set_id != expected_id:
            raise ValueError("gold-set ID does not match its content")
        return self


class PredictedEvidenceLabelV1(StrictModel):
    passage_id: Identifier
    relation: EvidenceRelation


class PredictedCandidateV1(StrictModel):
    candidate_id: Identifier
    selection_rank: Annotated[int, Field(ge=1, le=32)]
    family_id: Identifier
    canonical_structure_sha256: Sha256
    strict_structure_group_id: Identifier
    bridge_rule_ids: Annotated[
        tuple[Identifier, ...], Field(min_length=1, max_length=128)
    ]

    @model_validator(mode="after")
    def validate_candidate(self) -> PredictedCandidateV1:
        if self.bridge_rule_ids != tuple(sorted(set(self.bridge_rule_ids))):
            raise ValueError("candidate bridge-rule IDs must be sorted and unique")
        return self


class InspirationCasePredictionV1(StrictModel):
    case_id: Identifier
    selected_document_ids: Annotated[
        tuple[Identifier, ...], Field(max_length=2_000)
    ] = ()
    evidence_labels: Annotated[
        tuple[PredictedEvidenceLabelV1, ...], Field(max_length=2_000)
    ] = ()
    supported_bridge_rule_ids: Annotated[
        tuple[Identifier, ...], Field(max_length=256)
    ] = ()
    candidates: Annotated[tuple[PredictedCandidateV1, ...], Field(max_length=32)] = ()
    underfill_reason_codes: Annotated[
        tuple[Identifier, ...], Field(max_length=32)
    ] = ()

    @model_validator(mode="after")
    def validate_prediction(self) -> InspirationCasePredictionV1:
        for label, values in (
            ("selected documents", self.selected_document_ids),
            ("supported bridges", self.supported_bridge_rule_ids),
            ("underfill reasons", self.underfill_reason_codes),
        ):
            if values != tuple(sorted(set(values))):
                raise ValueError(f"predicted {label} must be sorted and unique")
        passage_ids = tuple(item.passage_id for item in self.evidence_labels)
        if passage_ids != tuple(sorted(set(passage_ids))):
            raise ValueError("predicted evidence must be passage-ID sorted and unique")
        candidate_ids = tuple(item.candidate_id for item in self.candidates)
        if len(candidate_ids) != len(set(candidate_ids)):
            raise ValueError("predicted candidate IDs must be unique")
        ranks = tuple(item.selection_rank for item in self.candidates)
        if ranks != tuple(range(1, len(self.candidates) + 1)):
            raise ValueError("predicted candidate ranks must be contiguous and ordered")
        return self


class CountMetricV1(StrictModel):
    """An auditable ratio; zero denominator is defined as a descriptive zero."""

    numerator: Annotated[int, Field(ge=0)]
    denominator: Annotated[int, Field(ge=0)]
    value: Annotated[float, Field(ge=0.0, le=1.0)]

    @model_validator(mode="after")
    def validate_ratio(self) -> CountMetricV1:
        expected = 0.0 if self.denominator == 0 else self.numerator / self.denominator
        if self.value != round(expected, 8):
            raise ValueError("count metric value differs from numerator/denominator")
        if self.numerator > self.denominator:
            raise ValueError("count metric numerator exceeds denominator")
        return self


class RelationConfusionV1(StrictModel):
    relation: EvidenceRelation
    true_positive: Annotated[int, Field(ge=0)]
    false_positive: Annotated[int, Field(ge=0)]
    false_negative: Annotated[int, Field(ge=0)]
    true_negative: Annotated[int, Field(ge=0)]
    precision: CountMetricV1
    recall: CountMetricV1
    f1: CountMetricV1

    @model_validator(mode="after")
    def validate_metrics(self) -> RelationConfusionV1:
        if self.precision != _count_metric(
            self.true_positive, self.true_positive + self.false_positive
        ):
            raise ValueError("relation precision does not match confusion counts")
        if self.recall != _count_metric(
            self.true_positive, self.true_positive + self.false_negative
        ):
            raise ValueError("relation recall does not match confusion counts")
        if self.f1 != _count_metric(
            2 * self.true_positive,
            2 * self.true_positive + self.false_positive + self.false_negative,
        ):
            raise ValueError("relation F1 does not match confusion counts")
        return self


class BridgeConfusionV1(StrictModel):
    true_positive: Annotated[int, Field(ge=0)]
    false_positive: Annotated[int, Field(ge=0)]
    false_negative: Annotated[int, Field(ge=0)]
    true_negative: Annotated[int, Field(ge=0)]
    precision: CountMetricV1
    recall: CountMetricV1
    specificity: CountMetricV1
    accuracy: CountMetricV1
    f1: CountMetricV1

    @model_validator(mode="after")
    def validate_metrics(self) -> BridgeConfusionV1:
        total = (
            self.true_positive
            + self.false_positive
            + self.false_negative
            + self.true_negative
        )
        expected = {
            "precision": _count_metric(
                self.true_positive, self.true_positive + self.false_positive
            ),
            "recall": _count_metric(
                self.true_positive, self.true_positive + self.false_negative
            ),
            "specificity": _count_metric(
                self.true_negative, self.true_negative + self.false_positive
            ),
            "accuracy": _count_metric(self.true_positive + self.true_negative, total),
            "f1": _count_metric(
                2 * self.true_positive,
                2 * self.true_positive + self.false_positive + self.false_negative,
            ),
        }
        for name, metric in expected.items():
            if getattr(self, name) != metric:
                raise ValueError(f"bridge {name} does not match confusion counts")
        return self


class CaseEvaluationV1(StrictModel):
    case_id: Identifier
    document_recall: CountMetricV1
    document_precision: CountMetricV1
    relation_macro_f1: CountMetricV1
    relation_confusion: Annotated[
        tuple[RelationConfusionV1, ...], Field(min_length=3, max_length=3)
    ]
    bridge_confusion: BridgeConfusionV1
    candidate_family_coverage: CountMetricV1
    candidate_fill_rate: CountMetricV1
    candidate_underfill_rate: CountMetricV1
    exact_duplicate_rate: CountMetricV1
    strict_duplicate_rate: CountMetricV1
    pairwise_route_diversity: CountMetricV1
    pairwise_family_diversity: CountMetricV1
    underfill_reason_codes: Annotated[tuple[Identifier, ...], Field(max_length=32)]

    @model_validator(mode="after")
    def validate_case_metrics(self) -> CaseEvaluationV1:
        relations = tuple(item.relation for item in self.relation_confusion)
        if relations != tuple(EvidenceRelation):
            raise ValueError("relation confusion must cover every relation in enum order")
        if self.candidate_fill_rate.denominator != self.candidate_underfill_rate.denominator:
            raise ValueError("candidate fill and underfill denominators differ")
        if (
            self.candidate_fill_rate.numerator
            + self.candidate_underfill_rate.numerator
            != self.candidate_fill_rate.denominator
        ):
            raise ValueError("candidate fill and underfill counts do not close")
        return self


class InspirationEvaluationReportV1(StrictModel):
    schema_version: Literal["inspiration-evaluation-report-v1"] = (
        "inspiration-evaluation-report-v1"
    )
    evaluation_id: Identifier
    gold_set_id: Identifier
    gold_set_sha256: Sha256
    gold_review_status: Literal["SYNTHETIC_TEST_ONLY", "ADJUDICATED"]
    case_results: Annotated[tuple[CaseEvaluationV1, ...], Field(min_length=1, max_length=256)]
    mean_document_recall: CountMetricV1
    mean_document_precision: CountMetricV1
    mean_relation_macro_f1: CountMetricV1
    mean_bridge_recall: CountMetricV1
    mean_bridge_precision: CountMetricV1
    mean_bridge_specificity: CountMetricV1
    mean_bridge_accuracy: CountMetricV1
    mean_candidate_family_coverage: CountMetricV1
    mean_candidate_fill_rate: CountMetricV1
    mean_exact_duplicate_rate: CountMetricV1
    mean_strict_duplicate_rate: CountMetricV1
    mean_pairwise_route_diversity: CountMetricV1
    mean_pairwise_family_diversity: CountMetricV1
    uncertainty_method: Literal["DESCRIPTIVE_BOUNDED_CORPUS_NO_CI"] = (
        "DESCRIPTIVE_BOUNDED_CORPUS_NO_CI"
    )
    evidence_class: Literal["ENGINEERING_EVALUATION"] = "ENGINEERING_EVALUATION"
    scientific_conclusion: Literal[False] = False

    @model_validator(mode="after")
    def validate_report(self) -> InspirationEvaluationReportV1:
        case_ids = tuple(item.case_id for item in self.case_results)
        if case_ids != tuple(sorted(set(case_ids))):
            raise ValueError("evaluation cases must be case-ID sorted and unique")
        semantic = self.model_dump(mode="python", exclude={"evaluation_id"})
        if self.evaluation_id != deterministic_id("inspiration-evaluation", semantic):
            raise ValueError("evaluation ID does not match report content")
        return self


ModelT = TypeVar("ModelT", bound=StrictModel)


def _strict_revalidate(value: object, model_type: type[ModelT], *, code: str) -> ModelT:
    """Round-trip an input so ``model_copy(update=...)`` cannot bypass validators."""

    if type(value) is not model_type:
        raise EvaluationError(code, f"expected an exact {model_type.__name__} object")
    try:
        return model_type.model_validate(value.model_dump(mode="python"))
    except (AttributeError, ValidationError, ValueError, TypeError) as exc:
        raise EvaluationError(code, f"{model_type.__name__} failed strict revalidation") from exc


def build_inspiration_gold_set(
    *,
    review_status: Literal["SYNTHETIC_TEST_ONLY", "ADJUDICATED"],
    source_license_notes: tuple[str, ...],
    cases: tuple[InspirationGoldCaseV1, ...],
    reviewer_ids: tuple[str, ...] = (),
    adjudicator_id: str | None = None,
) -> InspirationGoldSetV1:
    revalidated_cases = tuple(
        _strict_revalidate(item, InspirationGoldCaseV1, code="INVALID_GOLD_CASE")
        for item in cases
    )
    ordered_reviewers = tuple(sorted(set(reviewer_ids)))
    ordered_cases = tuple(sorted(revalidated_cases, key=lambda item: item.case_id))
    semantic = {
        "adjudicator_id": adjudicator_id,
        "cases": ordered_cases,
        "review_status": review_status,
        "reviewer_ids": ordered_reviewers,
        "schema_version": "inspiration-expert-gold-set-v1",
        "source_license_notes": source_license_notes,
    }
    gold_set_sha256 = canonical_sha256(semantic)
    return InspirationGoldSetV1(
        gold_set_id=deterministic_id(
            "inspiration-gold-set",
            {
                "gold_set_sha256": gold_set_sha256,
                "review_status": review_status,
            },
        ),
        review_status=review_status,
        reviewer_ids=ordered_reviewers,
        adjudicator_id=adjudicator_id,
        source_license_notes=source_license_notes,
        cases=ordered_cases,
        gold_set_sha256=gold_set_sha256,
    )


def _count_metric(numerator: int, denominator: int) -> CountMetricV1:
    return CountMetricV1(
        numerator=numerator,
        denominator=denominator,
        value=round(0.0 if denominator == 0 else numerator / denominator, 8),
    )


def _fraction_metric(value: Fraction) -> CountMetricV1:
    return _count_metric(value.numerator, value.denominator)


def _mean_metric(metrics: tuple[CountMetricV1, ...]) -> CountMetricV1:
    """Mean the displayed ratios with a fixed, bounded eight-decimal basis."""

    scale = 100_000_000
    numerator = sum(round(item.value * scale) for item in metrics)
    return _fraction_metric(Fraction(numerator, scale * len(metrics)))


def _relation_confusion(
    expected: dict[str, EvidenceRelation],
    predicted: dict[str, EvidenceRelation],
) -> tuple[tuple[RelationConfusionV1, ...], CountMetricV1]:
    rows: list[RelationConfusionV1] = []
    f1_fractions: list[Fraction] = []
    passage_ids = set(expected)
    for relation in EvidenceRelation:
        true_positive = sum(
            expected[item] is relation and predicted.get(item) is relation
            for item in passage_ids
        )
        false_positive = sum(
            expected[item] is not relation and predicted.get(item) is relation
            for item in passage_ids
        )
        false_negative = sum(
            expected[item] is relation and predicted.get(item) is not relation
            for item in passage_ids
        )
        true_negative = len(passage_ids) - true_positive - false_positive - false_negative
        f1_denominator = 2 * true_positive + false_positive + false_negative
        if f1_denominator:
            f1_fractions.append(Fraction(2 * true_positive, f1_denominator))
        rows.append(
            RelationConfusionV1(
                relation=relation,
                true_positive=true_positive,
                false_positive=false_positive,
                false_negative=false_negative,
                true_negative=true_negative,
                precision=_count_metric(
                    true_positive, true_positive + false_positive
                ),
                recall=_count_metric(
                    true_positive, true_positive + false_negative
                ),
                f1=_count_metric(2 * true_positive, f1_denominator),
            )
        )
    macro_f1 = (
        _fraction_metric(sum(f1_fractions, start=Fraction()) / len(f1_fractions))
        if f1_fractions
        else _count_metric(0, 0)
    )
    return tuple(rows), macro_f1


def _bridge_confusion(
    *, accepted: set[str], rejected: set[str], supported: set[str]
) -> BridgeConfusionV1:
    true_positive = len(accepted & supported)
    false_positive = len(rejected & supported)
    false_negative = len(accepted - supported)
    true_negative = len(rejected - supported)
    total = len(accepted) + len(rejected)
    return BridgeConfusionV1(
        true_positive=true_positive,
        false_positive=false_positive,
        false_negative=false_negative,
        true_negative=true_negative,
        precision=_count_metric(true_positive, true_positive + false_positive),
        recall=_count_metric(true_positive, true_positive + false_negative),
        specificity=_count_metric(true_negative, true_negative + false_positive),
        accuracy=_count_metric(true_positive + true_negative, total),
        f1=_count_metric(
            2 * true_positive,
            2 * true_positive + false_positive + false_negative,
        ),
    )


def _pairwise_route_diversity(
    candidates: tuple[PredictedCandidateV1, ...],
) -> CountMetricV1:
    """Return micro-aggregated Jaccard distance over unordered route-set pairs."""

    numerator = 0
    denominator = 0
    for left_index, left in enumerate(candidates):
        left_routes = set(left.bridge_rule_ids)
        for right in candidates[left_index + 1 :]:
            right_routes = set(right.bridge_rule_ids)
            numerator += len(left_routes ^ right_routes)
            denominator += len(left_routes | right_routes)
    return _count_metric(numerator, denominator)


def _pairwise_family_diversity(
    candidates: tuple[PredictedCandidateV1, ...],
) -> CountMetricV1:
    """Return the fraction of unordered candidate pairs from different families."""

    pair_count = len(candidates) * (len(candidates) - 1) // 2
    different_count = sum(
        left.family_id != right.family_id
        for left_index, left in enumerate(candidates)
        for right in candidates[left_index + 1 :]
    )
    return _count_metric(different_count, pair_count)


def evaluate_inspiration_predictions(
    *,
    gold_set: InspirationGoldSetV1,
    predictions: tuple[InspirationCasePredictionV1, ...],
) -> InspirationEvaluationReportV1:
    """Compute bounded descriptive metrics; never upgrade them to a science claim."""

    checked_gold = _strict_revalidate(
        gold_set, InspirationGoldSetV1, code="INVALID_GOLD_SET"
    )
    checked_predictions = tuple(
        _strict_revalidate(
            item, InspirationCasePredictionV1, code="INVALID_PREDICTIONS"
        )
        for item in predictions
    )
    prediction_ids = tuple(item.case_id for item in checked_predictions)
    if prediction_ids != tuple(sorted(set(prediction_ids))):
        raise EvaluationError(
            "INVALID_PREDICTIONS", "predictions must be case-ID sorted and unique"
        )
    prediction_index = {item.case_id: item for item in checked_predictions}
    gold_case_ids = {item.case_id for item in checked_gold.cases}
    if set(prediction_ids) != gold_case_ids:
        raise EvaluationError(
            "CASE_CLOSURE_MISMATCH", "predictions must cover every gold case exactly"
        )

    results: list[CaseEvaluationV1] = []
    for case in checked_gold.cases:
        prediction = prediction_index[case.case_id]
        selected_documents = set(prediction.selected_document_ids)
        relevant_documents = set(case.relevant_document_ids)
        rejected_documents = set(case.rejected_document_ids)
        if not selected_documents <= relevant_documents | rejected_documents:
            raise EvaluationError(
                "DOCUMENT_CLOSURE_MISMATCH",
                f"case {case.case_id} predicts documents outside the judged closure",
            )
        relevant_selected = len(selected_documents & relevant_documents)

        expected_relations = {
            item.passage_id: item.relation for item in case.evidence_labels
        }
        predicted_relations = {
            item.passage_id: item.relation for item in prediction.evidence_labels
        }
        if not set(predicted_relations) <= set(expected_relations):
            raise EvaluationError(
                "EVIDENCE_CLOSURE_MISMATCH",
                f"case {case.case_id} predicts passages outside the judged closure",
            )
        relation_confusion, relation_macro_f1 = _relation_confusion(
            expected_relations, predicted_relations
        )

        accepted_bridges = set(case.accepted_bridge_rule_ids)
        rejected_bridges = set(case.rejected_bridge_rule_ids)
        judged_bridges = accepted_bridges | rejected_bridges
        supported_bridges = set(prediction.supported_bridge_rule_ids)
        candidate_bridges = {
            bridge_id
            for candidate in prediction.candidates
            for bridge_id in candidate.bridge_rule_ids
        }
        if not supported_bridges <= judged_bridges or not candidate_bridges <= judged_bridges:
            raise EvaluationError(
                "BRIDGE_CLOSURE_MISMATCH",
                f"case {case.case_id} predicts bridge rules outside the judged closure",
            )
        if not candidate_bridges <= supported_bridges:
            raise EvaluationError(
                "CANDIDATE_BRIDGE_UNSUPPORTED",
                f"case {case.case_id} candidate routes must be supported by the prediction",
            )
        bridge_confusion = _bridge_confusion(
            accepted=accepted_bridges,
            rejected=rejected_bridges,
            supported=supported_bridges,
        )

        candidate_count = len(prediction.candidates)
        if candidate_count > case.requested_top_k:
            raise EvaluationError(
                "CANDIDATE_TOP_K_EXCEEDED",
                f"case {case.case_id} predicts more candidates than requested_top_k",
            )
        underfill_count = case.requested_top_k - candidate_count
        if bool(underfill_count) != bool(prediction.underfill_reason_codes):
            raise EvaluationError(
                "UNDERFILL_REASON_MISMATCH",
                f"case {case.case_id} underfill reasons must be present exactly on underfill",
            )

        accepted_families = set(case.accepted_candidate_family_ids)
        predicted_families = {item.family_id for item in prediction.candidates}
        canonical_hashes = tuple(
            item.canonical_structure_sha256 for item in prediction.candidates
        )
        strict_groups = tuple(
            item.strict_structure_group_id for item in prediction.candidates
        )
        results.append(
            CaseEvaluationV1(
                case_id=case.case_id,
                document_recall=_count_metric(
                    relevant_selected, len(relevant_documents)
                ),
                document_precision=_count_metric(
                    relevant_selected, len(selected_documents)
                ),
                relation_macro_f1=relation_macro_f1,
                relation_confusion=relation_confusion,
                bridge_confusion=bridge_confusion,
                candidate_family_coverage=_count_metric(
                    len(accepted_families & predicted_families),
                    len(accepted_families),
                ),
                candidate_fill_rate=_count_metric(
                    candidate_count, case.requested_top_k
                ),
                candidate_underfill_rate=_count_metric(
                    underfill_count, case.requested_top_k
                ),
                exact_duplicate_rate=_count_metric(
                    candidate_count - len(set(canonical_hashes)), candidate_count
                ),
                strict_duplicate_rate=_count_metric(
                    candidate_count - len(set(strict_groups)), candidate_count
                ),
                pairwise_route_diversity=_pairwise_route_diversity(
                    prediction.candidates
                ),
                pairwise_family_diversity=_pairwise_family_diversity(
                    prediction.candidates
                ),
                underfill_reason_codes=prediction.underfill_reason_codes,
            )
        )
    case_results = tuple(results)

    def mean(attribute: str) -> CountMetricV1:
        return _mean_metric(
            tuple(getattr(item, attribute) for item in case_results)
        )

    def mean_bridge(attribute: str) -> CountMetricV1:
        return _mean_metric(
            tuple(getattr(item.bridge_confusion, attribute) for item in case_results)
        )

    semantic = {
        "schema_version": "inspiration-evaluation-report-v1",
        "gold_set_id": checked_gold.gold_set_id,
        "gold_set_sha256": checked_gold.gold_set_sha256,
        "gold_review_status": checked_gold.review_status,
        "case_results": case_results,
        "mean_document_recall": mean("document_recall"),
        "mean_document_precision": mean("document_precision"),
        "mean_relation_macro_f1": mean("relation_macro_f1"),
        "mean_bridge_recall": mean_bridge("recall"),
        "mean_bridge_precision": mean_bridge("precision"),
        "mean_bridge_specificity": mean_bridge("specificity"),
        "mean_bridge_accuracy": mean_bridge("accuracy"),
        "mean_candidate_family_coverage": mean("candidate_family_coverage"),
        "mean_candidate_fill_rate": mean("candidate_fill_rate"),
        "mean_exact_duplicate_rate": mean("exact_duplicate_rate"),
        "mean_strict_duplicate_rate": mean("strict_duplicate_rate"),
        "mean_pairwise_route_diversity": mean("pairwise_route_diversity"),
        "mean_pairwise_family_diversity": mean("pairwise_family_diversity"),
        "uncertainty_method": "DESCRIPTIVE_BOUNDED_CORPUS_NO_CI",
        "evidence_class": "ENGINEERING_EVALUATION",
        "scientific_conclusion": False,
    }
    return InspirationEvaluationReportV1(
        evaluation_id=deterministic_id("inspiration-evaluation", semantic),
        **semantic,
    )
