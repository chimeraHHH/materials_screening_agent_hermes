"""Decision contract for an optional MOOSE-Star retrieval shadow.

The evaluator compares already-produced rankings.  It does not import
MOOSE-Star, start SGLang, or treat synthetic fixtures as materials-science
validation.  Promotion is impossible until corpus size, labelled query count,
recall improvement, failure rate, and latency gates all pass.
"""

from __future__ import annotations

from enum import StrEnum
from statistics import fmean
from typing import Annotated, Literal

from pydantic import Field, model_validator

from material_agent.inspiration.models import (
    Identifier,
    Score,
    ShortText,
    StrictModel,
    deterministic_id,
)

MOOSE_SHADOW_EVALUATION_VERSION = "moose-star-shadow-evaluation-v1"


class MooseShadowDecision(StrEnum):
    BLOCKED_INSUFFICIENT_EVIDENCE = "BLOCKED_INSUFFICIENT_EVIDENCE"
    KEEP_SHADOW = "KEEP_SHADOW"
    PROMOTE_OPTIONAL_RANKER = "PROMOTE_OPTIONAL_RANKER"


class MooseShadowPolicyV1(StrictModel):
    min_corpus_documents: Annotated[int, Field(ge=100, le=10_000_000)] = 500
    min_labelled_queries: Annotated[int, Field(ge=5, le=10_000)] = 30
    primary_k: Literal[50] = 50
    minimum_recall_improvement: Score = 0.10
    maximum_latency_ratio: Annotated[float, Field(ge=1.0, le=20.0)] = 2.0
    maximum_failure_rate: Score = 0.02


class RetrievalShadowCaseV1(StrictModel):
    query_id: Identifier
    relevant_document_ids: Annotated[
        tuple[Identifier, ...], Field(min_length=1, max_length=1_000)
    ]
    baseline_ranked_document_ids: Annotated[
        tuple[Identifier, ...], Field(min_length=1, max_length=10_000)
    ]
    moose_ranked_document_ids: Annotated[
        tuple[Identifier, ...], Field(min_length=1, max_length=10_000)
    ]
    baseline_latency_seconds: Annotated[float, Field(gt=0.0, le=3_600.0)]
    moose_latency_seconds: Annotated[float, Field(gt=0.0, le=3_600.0)]
    moose_failed: bool = False

    @model_validator(mode="after")
    def validate_rankings(self) -> RetrievalShadowCaseV1:
        for name in (
            "relevant_document_ids",
            "baseline_ranked_document_ids",
            "moose_ranked_document_ids",
        ):
            values = getattr(self, name)
            if len(set(values)) != len(values):
                raise ValueError(f"{name} must be unique")
        return self


class MooseShadowMetricsV1(StrictModel):
    corpus_documents: Annotated[int, Field(ge=0)]
    labelled_queries: Annotated[int, Field(ge=0)]
    baseline_macro_recall_at_50: Score | None = None
    moose_macro_recall_at_50: Score | None = None
    recall_improvement: Annotated[float, Field(ge=-1.0, le=1.0)] | None = None
    baseline_mean_reciprocal_rank: Score | None = None
    moose_mean_reciprocal_rank: Score | None = None
    mean_latency_ratio: Annotated[float, Field(ge=0.0, le=1_000.0)] | None = None
    moose_failure_rate: Score | None = None


class MooseShadowEvaluationV1(StrictModel):
    schema_version: Literal["moose-star-shadow-evaluation-v1"] = (
        MOOSE_SHADOW_EVALUATION_VERSION
    )
    evaluation_id: Identifier
    decision: MooseShadowDecision
    metrics: MooseShadowMetricsV1
    reasons: Annotated[tuple[ShortText, ...], Field(min_length=1, max_length=16)]
    default_ranker_changed: Literal[False] = False
    scientific_conclusion: Literal[False] = False

    @model_validator(mode="after")
    def validate_identity(self) -> MooseShadowEvaluationV1:
        expected = deterministic_id(
            "moose-shadow",
            self.model_dump(mode="json", exclude={"evaluation_id"}),
        )
        if self.evaluation_id != expected:
            raise ValueError("MOOSE shadow evaluation ID does not match canonical content")
        return self


def evaluate_moose_shadow_v1(
    *,
    corpus_document_ids: tuple[str, ...],
    cases: tuple[RetrievalShadowCaseV1, ...],
    policy: MooseShadowPolicyV1 | None = None,
) -> MooseShadowEvaluationV1:
    """Return a deterministic promotion decision from labelled shadow rankings."""

    selected_policy = policy or MooseShadowPolicyV1()
    if len(set(corpus_document_ids)) != len(corpus_document_ids):
        raise ValueError("corpus document IDs must be unique")
    if len({case.query_id for case in cases}) != len(cases):
        raise ValueError("shadow query IDs must be unique")
    corpus_ids = set(corpus_document_ids)
    for case in cases:
        observed = {
            *case.relevant_document_ids,
            *case.baseline_ranked_document_ids,
            *case.moose_ranked_document_ids,
        }
        if not observed <= corpus_ids:
            raise ValueError("shadow case references a document outside the corpus")

    if (
        len(corpus_document_ids) < selected_policy.min_corpus_documents
        or len(cases) < selected_policy.min_labelled_queries
    ):
        reasons = []
        if len(corpus_document_ids) < selected_policy.min_corpus_documents:
            reasons.append(
                "Corpus is below the minimum required for hierarchical retrieval evaluation."
            )
        if len(cases) < selected_policy.min_labelled_queries:
            reasons.append(
                "Expert-labelled query count is below the promotion threshold."
            )
        return _evaluation(
            decision=MooseShadowDecision.BLOCKED_INSUFFICIENT_EVIDENCE,
            metrics=MooseShadowMetricsV1(
                corpus_documents=len(corpus_document_ids),
                labelled_queries=len(cases),
            ),
            reasons=tuple(reasons),
        )

    baseline_recall = fmean(
        _recall_at_k(
            case.baseline_ranked_document_ids,
            case.relevant_document_ids,
            selected_policy.primary_k,
        )
        for case in cases
    )
    moose_recall = fmean(
        0.0
        if case.moose_failed
        else _recall_at_k(
            case.moose_ranked_document_ids,
            case.relevant_document_ids,
            selected_policy.primary_k,
        )
        for case in cases
    )
    baseline_mrr = fmean(
        _reciprocal_rank(
            case.baseline_ranked_document_ids,
            case.relevant_document_ids,
        )
        for case in cases
    )
    moose_mrr = fmean(
        0.0
        if case.moose_failed
        else _reciprocal_rank(
            case.moose_ranked_document_ids,
            case.relevant_document_ids,
        )
        for case in cases
    )
    latency_ratio = fmean(
        case.moose_latency_seconds / case.baseline_latency_seconds for case in cases
    )
    failure_rate = sum(case.moose_failed for case in cases) / len(cases)
    improvement = moose_recall - baseline_recall
    metrics = MooseShadowMetricsV1(
        corpus_documents=len(corpus_document_ids),
        labelled_queries=len(cases),
        baseline_macro_recall_at_50=baseline_recall,
        moose_macro_recall_at_50=moose_recall,
        recall_improvement=improvement,
        baseline_mean_reciprocal_rank=baseline_mrr,
        moose_mean_reciprocal_rank=moose_mrr,
        mean_latency_ratio=latency_ratio,
        moose_failure_rate=failure_rate,
    )
    reasons: list[str] = []
    if improvement < selected_policy.minimum_recall_improvement:
        reasons.append("Recall@50 improvement does not meet the promotion threshold.")
    if latency_ratio > selected_policy.maximum_latency_ratio:
        reasons.append("Mean latency ratio exceeds the promotion threshold.")
    if failure_rate > selected_policy.maximum_failure_rate:
        reasons.append("MOOSE shadow failure rate exceeds the promotion threshold.")
    if reasons:
        return _evaluation(
            decision=MooseShadowDecision.KEEP_SHADOW,
            metrics=metrics,
            reasons=tuple(reasons),
        )
    return _evaluation(
        decision=MooseShadowDecision.PROMOTE_OPTIONAL_RANKER,
        metrics=metrics,
        reasons=(
            "All corpus, labelled-query, Recall@50, latency, and failure gates passed.",
        ),
    )


def _evaluation(
    *,
    decision: MooseShadowDecision,
    metrics: MooseShadowMetricsV1,
    reasons: tuple[str, ...],
) -> MooseShadowEvaluationV1:
    values = {
        "schema_version": MOOSE_SHADOW_EVALUATION_VERSION,
        "decision": decision,
        "metrics": metrics,
        "reasons": reasons,
        "default_ranker_changed": False,
        "scientific_conclusion": False,
    }
    return MooseShadowEvaluationV1(
        evaluation_id=deterministic_id("moose-shadow", values),
        **values,
    )


def _recall_at_k(
    ranked: tuple[str, ...], relevant: tuple[str, ...], k: int
) -> float:
    return len(set(ranked[:k]) & set(relevant)) / len(relevant)


def _reciprocal_rank(ranked: tuple[str, ...], relevant: tuple[str, ...]) -> float:
    relevant_set = set(relevant)
    for index, document_id in enumerate(ranked, start=1):
        if document_id in relevant_set:
            return 1.0 / index
    return 0.0
