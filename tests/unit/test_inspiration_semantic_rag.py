from __future__ import annotations

import hashlib

import pytest

from material_agent.inspiration.models import (
    ArtifactPointerV1,
    ComponentSnapshotV1,
    EvidenceCardV1,
    EvidenceRelation,
    PassageLocatorKind,
    PassageLocatorV1,
    PassageV1,
)
from material_agent.inspiration.semantic_rag import (
    GroundedSemanticRAGJudge,
    LocalRAGCandidateV1,
    SemanticRAGBudgetV1,
    SemanticRAGError,
    SemanticRAGRequestV1,
    semantic_rag_judge_from_environment,
)
from material_agent.orchestrator.llm import StructuredLLMResponse
from material_agent.orchestrator.models import LLMCallAudit


def _passage() -> PassageV1:
    text = "Compact localized modes arise because interference suppresses transport."
    return PassageV1(
        passage_id="passage-1",
        hit_id="hit-1",
        document_id="document-1",
        source_artifact=ArtifactPointerV1(
            uri="artifact://stages/inspiration/run/raw.json",
            sha256="a" * 64,
            media_type="application/json",
            size_bytes=100,
        ),
        normalizer=ComponentSnapshotV1(
            component_id="normalizer",
            version="1",
            implementation_sha256="b" * 64,
        ),
        locator=PassageLocatorV1(
            kind=PassageLocatorKind.API_FIELD,
            selector="abstract",
        ),
        text=text,
        char_count=len(text),
        estimated_token_count=12,
        normalized_text_sha256=hashlib.sha256(text.encode()).hexdigest(),
        matched_tag_ids=("compact-localized-state",),
        lexical_score=0.8,
    )


def _request() -> SemanticRAGRequestV1:
    return SemanticRAGRequestV1(
        request_id="rag-request-1",
        query="Rank the bounded mechanism hypothesis against the supplied evidence.",
        passages=(_passage(),),
        evidence_cards=(
            EvidenceCardV1(
                evidence_card_id="evidence-1",
                relation=EvidenceRelation.SUPPORT,
                claim_text="The source attributes localization to interference.",
                mechanism_tag_ids=("compact-localized-state",),
                applicability_conditions=("interfering paths are retained",),
                passage_ids=("passage-1",),
            ),
        ),
        candidates=(
            LocalRAGCandidateV1(
                candidate_id="candidate-1",
                description="Retain the interfering motif in a local structure proposal.",
                evidence_card_ids=("evidence-1",),
            ),
        ),
    )


class _FakeProvider:
    name = "fake-deepseek"
    version = "fake-v1"

    def __init__(
        self,
        payload: dict[str, object] | None = None,
        *,
        completion_tokens: int = 100,
    ) -> None:
        self.calls: list[dict[str, object]] = []
        self.payload = payload or {
            "judgements": [
                {
                    "candidate_id": "candidate-1",
                    "rank": 1,
                    "relevance_score": 0.82,
                    "verdict": "SUPPORTED",
                    "cited_passage_ids": ["passage-1"],
                    "cited_evidence_card_ids": ["evidence-1"],
                    "rationale": "The cited source passage supports the retained motif mechanism.",
                }
            ],
            "limitations": ["This is a bounded source-grounding judgement, not validation."],
        }
        self.completion_tokens = completion_tokens

    def structured_generate(
        self,
        *,
        system_prompt: str,
        user_payload: dict[str, object],
        prompt_version: str,
    ) -> StructuredLLMResponse:
        self.calls.append(
            {
                "system_prompt": system_prompt,
                "user_payload": user_payload,
                "prompt_version": prompt_version,
            }
        )
        return StructuredLLMResponse(
            payload=self.payload,
            audit=LLMCallAudit(
                provider=self.name,
                provider_version=self.version,
                model_id="deepseek-v4-pro",
                base_url="https://api.deepseek.com",
                prompt_version=prompt_version,
                request_sha256="c" * 64,
                response_sha256="d" * 64,
                thinking_mode="disabled",
                reasoning_effort="none",
                response_format="json_object",
                prompt_tokens=500,
                completion_tokens=self.completion_tokens,
                total_tokens=500 + self.completion_tokens,
            ),
        )


def test_grounded_rag_reranks_one_closed_local_candidate_without_cot() -> None:
    provider = _FakeProvider()
    result = GroundedSemanticRAGJudge(provider).rerank(
        _request(),
        budget=SemanticRAGBudgetV1(),
    )

    assert result.judgements[0].candidate_id == "candidate-1"
    assert result.receipt.model_id == "deepseek-v4-pro"
    assert result.receipt.reasoning_content_persisted is False
    assert result.receipt.prompt_tokens == 500
    sent = provider.calls[0]["user_payload"]
    assert isinstance(sent, dict)
    assert set(sent) == {
        "request_id",
        "query",
        "passages",
        "evidence_cards",
        "candidates",
    }
    assert sent["passages"] == (
        {
            "passage_id": "passage-1",
            "text": _passage().text,
        },
    )
    assert "chain-of-thought" in str(provider.calls[0]["system_prompt"])


def test_grounded_rag_rejects_hallucinated_citations_and_candidate_ids() -> None:
    hallucinated = _FakeProvider(
        {
            "judgements": [
                {
                    "candidate_id": "candidate-1",
                    "rank": 1,
                    "relevance_score": 0.5,
                    "verdict": "MIXED",
                    "cited_passage_ids": ["passage-outside-input"],
                    "cited_evidence_card_ids": ["evidence-1"],
                    "rationale": "Invalid citation injected by the fixture.",
                }
            ],
            "limitations": [],
        }
    )
    with pytest.raises(SemanticRAGError) as citation_error:
        GroundedSemanticRAGJudge(hallucinated).rerank(
            _request(),
            budget=SemanticRAGBudgetV1(),
        )
    assert citation_error.value.code == "CITATION_CLOSURE_MISMATCH"

    wrong_candidate = _FakeProvider(
        {
            "judgements": [
                {
                    "candidate_id": "candidate-invented",
                    "rank": 1,
                    "relevance_score": 0.5,
                    "verdict": "INSUFFICIENT",
                    "cited_passage_ids": ["passage-1"],
                    "cited_evidence_card_ids": ["evidence-1"],
                    "rationale": "Invalid candidate injected by the fixture.",
                }
            ],
            "limitations": [],
        }
    )
    with pytest.raises(SemanticRAGError) as candidate_error:
        GroundedSemanticRAGJudge(wrong_candidate).rerank(
            _request(),
            budget=SemanticRAGBudgetV1(),
        )
    assert candidate_error.value.code == "CANDIDATE_CLOSURE_MISMATCH"


def test_grounded_rag_budget_fails_before_model_call_and_usage_is_enforced() -> None:
    provider = _FakeProvider()
    with pytest.raises(SemanticRAGError) as input_error:
        GroundedSemanticRAGJudge(provider).rerank(
            _request(),
            budget=SemanticRAGBudgetV1(max_input_tokens=256),
        )
    assert input_error.value.code == "LLM_INPUT_BUDGET_EXCEEDED"
    assert provider.calls == []

    provider = _FakeProvider(completion_tokens=4_097)
    with pytest.raises(SemanticRAGError) as output_error:
        GroundedSemanticRAGJudge(provider).rerank(
            _request(),
            budget=SemanticRAGBudgetV1(),
        )
    assert output_error.value.code == "LLM_OUTPUT_BUDGET_EXCEEDED"


def test_deepseek_rag_factory_is_disabled_by_default_and_secret_resolution_is_lazy() -> None:
    assert semantic_rag_judge_from_environment(environment={}) is None
    judge = semantic_rag_judge_from_environment(
        environment={
            "MATERIAL_AGENT_INSPIRATION_RAG_PROVIDER": "deepseek",
            "MATERIAL_AGENT_LLM_API_KEY": "offline-test-secret",
        }
    )
    assert isinstance(judge, GroundedSemanticRAGJudge)
    assert "offline-test-secret" not in repr(judge)
