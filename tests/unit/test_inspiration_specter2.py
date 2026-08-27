from __future__ import annotations

import hashlib

from material_agent.inspiration.research_graph import FineGrainedEvidenceSpanV1
from material_agent.inspiration.specter2 import Specter2EvidenceRanker


def _span(index: int, text: str) -> FineGrainedEvidenceSpanV1:
    return FineGrainedEvidenceSpanV1(
        span_id="span-" + str(index) * 24,
        document_id="document-" + "1" * 24,
        evidence_id="evidence-" + "2" * 24,
        section_path=("Results",),
        paragraph_index=index,
        text_excerpt=text,
        text_sha256=hashlib.sha256(text.encode()).hexdigest(),
        locator=f"section=Results;paragraph={index};pages=unknown",
        parser="DOCLING",
    )


def test_specter2_ranker_orders_spans_by_semantic_vector_similarity() -> None:
    class Provider:
        model_identity = "specter2:fixture"

        def encode(self, texts):
            assert len(texts) == 3
            return ((1.0, 0.0), (0.1, 0.9), (0.95, 0.05))

    first = _span(1, "Synthesis conditions")
    second = _span(2, "Flat band width and orbital character")
    ranked = Specter2EvidenceRanker(Provider()).rank_spans(
        "flat band orbital",
        (first, second),
    )

    assert ranked == (second, first)


def test_specter2_ranker_is_stable_for_equal_scores() -> None:
    class Provider:
        model_identity = "specter2:fixture"

        def encode(self, texts):
            return tuple((1.0, 0.0) for _ in texts)

    spans = (_span(1, "one"), _span(2, "two"))
    assert Specter2EvidenceRanker(Provider()).rank_spans("query", spans) == spans
