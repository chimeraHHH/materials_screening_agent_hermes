from __future__ import annotations

from material_agent.inspiration.moose_shadow import (
    MooseShadowDecision,
    MooseShadowEvaluationV1,
    MooseShadowPolicyV1,
    RetrievalShadowCaseV1,
    evaluate_moose_shadow_v1,
)


def _documents(count: int = 500) -> tuple[str, ...]:
    return tuple(f"document-{index:04d}" for index in range(count))


def _cases(*, improved: bool, slow: bool = False) -> tuple[RetrievalShadowCaseV1, ...]:
    documents = _documents()
    cases = []
    for index in range(30):
        relevant = documents[index * 2]
        distractors = [item for item in documents if item != relevant]
        baseline = (*distractors[:60], relevant, *distractors[60:])
        moose = (
            (relevant, *distractors)
            if improved
            else (*distractors[:60], relevant, *distractors[60:])
        )
        cases.append(
            RetrievalShadowCaseV1(
                query_id=f"query-{index:02d}",
                relevant_document_ids=(relevant,),
                baseline_ranked_document_ids=baseline,
                moose_ranked_document_ids=moose,
                baseline_latency_seconds=1.0,
                moose_latency_seconds=3.0 if slow else 1.5,
            )
        )
    return tuple(cases)


def test_current_small_corpus_is_blocked_without_fake_metrics() -> None:
    result = evaluate_moose_shadow_v1(
        corpus_document_ids=_documents(8),
        cases=(),
    )

    assert result.decision is MooseShadowDecision.BLOCKED_INSUFFICIENT_EVIDENCE
    assert result.metrics.corpus_documents == 8
    assert result.metrics.labelled_queries == 0
    assert result.metrics.moose_macro_recall_at_50 is None
    assert result.default_ranker_changed is False
    assert result.scientific_conclusion is False


def test_shadow_promotes_only_after_recall_latency_and_failure_gates() -> None:
    result = evaluate_moose_shadow_v1(
        corpus_document_ids=_documents(),
        cases=_cases(improved=True),
    )

    assert result.decision is MooseShadowDecision.PROMOTE_OPTIONAL_RANKER
    assert result.metrics.baseline_macro_recall_at_50 == 0.0
    assert result.metrics.moose_macro_recall_at_50 == 1.0
    assert result.metrics.mean_latency_ratio == 1.5
    assert result.default_ranker_changed is False
    assert MooseShadowEvaluationV1.model_validate_json(result.model_dump_json()) == result


def test_shadow_stays_optional_when_recall_does_not_improve() -> None:
    result = evaluate_moose_shadow_v1(
        corpus_document_ids=_documents(),
        cases=_cases(improved=False),
    )

    assert result.decision is MooseShadowDecision.KEEP_SHADOW
    assert result.metrics.recall_improvement == 0.0
    assert "Recall@50" in result.reasons[0]


def test_shadow_stays_optional_when_latency_is_too_high() -> None:
    result = evaluate_moose_shadow_v1(
        corpus_document_ids=_documents(),
        cases=_cases(improved=True, slow=True),
        policy=MooseShadowPolicyV1(maximum_latency_ratio=2.0),
    )

    assert result.decision is MooseShadowDecision.KEEP_SHADOW
    assert result.metrics.mean_latency_ratio == 3.0
    assert any("latency" in reason for reason in result.reasons)


def test_evaluation_is_deterministic_under_case_reordering() -> None:
    cases = _cases(improved=True)
    forward = evaluate_moose_shadow_v1(
        corpus_document_ids=_documents(),
        cases=cases,
    )
    reverse = evaluate_moose_shadow_v1(
        corpus_document_ids=_documents(),
        cases=tuple(reversed(cases)),
    )

    assert forward == reverse
