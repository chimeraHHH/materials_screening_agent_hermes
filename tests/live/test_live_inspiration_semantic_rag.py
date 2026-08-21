from __future__ import annotations

import hashlib
import os

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
    SemanticRAGRequestV1,
    semantic_rag_judge_from_environment,
)

pytestmark = pytest.mark.live_semantic_rag


def test_live_deepseek_grounded_semantic_rag() -> None:
    assert os.environ.get("MATERIAL_AGENT_INSPIRATION_RAG_PROVIDER") == "deepseek"
    judge = semantic_rag_judge_from_environment()
    assert isinstance(judge, GroundedSemanticRAGJudge)

    text = (
        "Destructive interference can localize an electronic state and produce "
        "a narrow band when the interfering hopping paths are retained."
    )
    passage = PassageV1(
        passage_id="live-passage-1",
        hit_id="live-hit-1",
        document_id="live-document-1",
        source_artifact=ArtifactPointerV1(
            uri="artifact://live/deepseek-rag-source.json",
            sha256="a" * 64,
            media_type="application/json",
            size_bytes=256,
        ),
        normalizer=ComponentSnapshotV1(
            component_id="live-rag-normalizer",
            version="1",
            implementation_sha256="b" * 64,
        ),
        locator=PassageLocatorV1(
            kind=PassageLocatorKind.API_FIELD,
            selector="abstract",
        ),
        text=text,
        char_count=len(text),
        estimated_token_count=24,
        normalized_text_sha256=hashlib.sha256(text.encode("utf-8")).hexdigest(),
        matched_tag_ids=("destructive-interference",),
        lexical_score=0.8,
    )
    request = SemanticRAGRequestV1(
        request_id="live-deepseek-rag-1",
        query="Rank the supplied hypothesis only against the bounded evidence.",
        passages=(passage,),
        evidence_cards=(
            EvidenceCardV1(
                evidence_card_id="live-evidence-1",
                relation=EvidenceRelation.SUPPORT,
                claim_text=(
                    "The bounded source passage links retained interfering paths "
                    "to a localized narrow electronic state."
                ),
                mechanism_tag_ids=("destructive-interference",),
                applicability_conditions=("interfering hopping paths are retained",),
                passage_ids=("live-passage-1",),
            ),
        ),
        candidates=(
            LocalRAGCandidateV1(
                candidate_id="live-candidate-1",
                description=(
                    "Preserve the interfering motif while proposing the hypothetical "
                    "structure."
                ),
                evidence_card_ids=("live-evidence-1",),
            ),
        ),
    )
    result = judge.rerank(
        request,
        budget=SemanticRAGBudgetV1(
            max_input_tokens=4_000,
        ),
    )

    assert len(result.judgements) == 1
    judgement = result.judgements[0]
    assert judgement.candidate_id == "live-candidate-1"
    assert judgement.cited_passage_ids == ("live-passage-1",)
    assert judgement.cited_evidence_card_ids == ("live-evidence-1",)
    assert result.receipt.provider == "deepseek"
    assert result.receipt.model_id == "deepseek-v4-pro"
    assert result.receipt.reasoning_content_persisted is False
