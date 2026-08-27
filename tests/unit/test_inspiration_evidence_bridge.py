from __future__ import annotations

import hashlib

from material_agent.inspiration import (
    ArtifactPointerV1,
    ComponentSnapshotV1,
    PassageLocatorKind,
    PassageLocatorV1,
    PassageV1,
    SearchHitV1,
)
from material_agent.inspiration.bridge import build_search_supported_bridges
from material_agent.inspiration.evidence import (
    build_evidence_cards,
    classify_evidence_relation,
)
from material_agent.inspiration.policy import SearchBudgetV1
from material_agent.inspiration.tag_graph import (
    curated_flat_band_tag_graph,
    plan_tag_queries,
)


def artifact(name: str, digest: str) -> ArtifactPointerV1:
    return ArtifactPointerV1(
        uri=f"artifact://inspiration/{name}",
        sha256=digest * 64,
        media_type="application/json",
    )


def test_passages_become_source_scoped_cards_and_one_supported_bridge() -> None:
    graph = curated_flat_band_tag_graph()
    query_plan = plan_tag_queries(
        graph,
        target_tag_ids=("electronic-flat-band",),
        budget=SearchBudgetV1(
            max_queries=7,
            max_direct_queries=1,
            max_bridge_queries=3,
            max_counter_queries=3,
            max_raw_hits=10,
            max_unique_documents=10,
        ),
    )
    queries = query_plan.queries
    bridge_query = next(
        query
        for query in queries
        if query.kind.value == "BRIDGE"
        and query.bridge_rule_id == "photonic-interference-to-electronic-flat-band"
    )
    counter_query = next(
        query
        for query in queries
        if query.kind.value == "COUNTER"
        and query.bridge_rule_id == bridge_query.bridge_rule_id
    )
    hits = (
        _hit("support", bridge_query.query_id, "a"),
        _hit("counter", counter_query.query_id, "b"),
    )
    passages = (
        _passage(
            "support",
            text="Compact localized states arise from destructive interference in the lattice.",
            matched_tags=("compact-localized-state", "destructive-interference"),
            digest="a",
        ),
        _passage(
            "counter",
            text="Path imbalance destroys the destructive-interference condition.",
            matched_tags=("destructive-interference",),
            digest="b",
        ),
    )

    evidence = build_evidence_cards(
        graph=graph,
        queries=queries,
        hits=hits,
        passages=passages,
    )
    bridges = build_search_supported_bridges(
        graph=graph,
        queries=queries,
        hits=hits,
        passages=passages,
        evidence_cards=evidence.cards,
    )

    assert [card.relation.value for card in evidence.cards] == ["COUNTER", "SUPPORT"]
    assert all(card.evidence_scope == "SOURCE_ASSERTION" for card in evidence.cards)
    assert len(bridges.packets) == 1
    packet = bridges.packets[0]
    assert packet.bridge_rule_id == bridge_query.bridge_rule_id
    assert packet.status == "SEARCH_SUPPORTED"
    assert set(packet.evidence_card_ids) == {
        card.evidence_card_id for card in evidence.cards
    }
    assert len(bridges.skipped) == 2


def test_missing_required_mechanism_never_promotes_a_packet() -> None:
    graph = curated_flat_band_tag_graph()
    queries = plan_tag_queries(
        graph,
        target_tag_ids=("electronic-flat-band",),
        budget=SearchBudgetV1(
            max_queries=4,
            max_direct_queries=1,
            max_bridge_queries=3,
            max_counter_queries=0,
            max_raw_hits=10,
            max_unique_documents=10,
        ),
    ).queries
    bridge_query = next(
        query
        for query in queries
        if query.kind.value == "BRIDGE"
        and query.bridge_rule_id == "photonic-interference-to-electronic-flat-band"
    )
    hits = (_hit("support", bridge_query.query_id, "a"),)
    passages = (
        _passage(
            "support",
            text="A compact localized state appears in the lattice.",
            matched_tags=("compact-localized-state",),
            digest="a",
        ),
    )
    evidence = build_evidence_cards(
        graph=graph,
        queries=queries,
        hits=hits,
        passages=passages,
    )

    result = build_search_supported_bridges(
        graph=graph,
        queries=queries,
        hits=hits,
        passages=passages,
        evidence_cards=evidence.cards,
    )

    assert result.packets == ()
    assert any("destructive-interference" in item.reason for item in result.skipped)


def test_negated_bridge_passage_is_counter_not_support() -> None:
    graph = curated_flat_band_tag_graph()
    queries = plan_tag_queries(
        graph,
        target_tag_ids=("electronic-flat-band",),
        budget=SearchBudgetV1(
            max_queries=4,
            max_direct_queries=1,
            max_bridge_queries=3,
            max_counter_queries=0,
            max_raw_hits=10,
            max_unique_documents=10,
        ),
    ).queries
    bridge_query = next(
        query
        for query in queries
        if query.bridge_rule_id == "acoustic-resonance-to-electronic-flat-band"
    )
    hits = (_hit("negated", bridge_query.query_id, "d"),)
    passages = (
        _passage(
            "negated",
            text="Local resonance does not cause a flat band in this system.",
            matched_tags=("local-resonance",),
            digest="d",
        ),
    )

    evidence = build_evidence_cards(
        graph=graph,
        queries=queries,
        hits=hits,
        passages=passages,
    )
    bridges = build_search_supported_bridges(
        graph=graph,
        queries=queries,
        hits=hits,
        passages=passages,
        evidence_cards=evidence.cards,
    )

    assert len(evidence.cards) == 1
    assert evidence.cards[0].relation.value == "COUNTER"
    assert bridges.packets == ()
    assert any(
        item.bridge_rule_id == bridge_query.bridge_rule_id
        for item in bridges.skipped
    )


def test_hedged_or_bare_keyword_passages_remain_context() -> None:
    assert (
        classify_evidence_relation(
            "Local resonance might possibly cause a flat band.",
            counter_query=False,
        ).value
        == "CONTEXT"
    )
    assert (
        classify_evidence_relation(
            "The abstract lists local resonance and flat band keywords.",
            counter_query=False,
        ).value
        == "CONTEXT"
    )
    assert (
        classify_evidence_relation(
            "A showcase and extrapolation table list both keywords.",
            counter_query=False,
        ).value
        == "CONTEXT"
    )
    assert (
        classify_evidence_relation(
            "Many measurements demonstrate localization.",
            counter_query=False,
        ).value
        == "SUPPORT"
    )


def test_counter_query_requires_an_explicit_breaking_assertion() -> None:
    assert (
        classify_evidence_relation(
            "Local resonance supports a weakly dispersive branch.",
            counter_query=True,
        ).value
        == "CONTEXT"
    )
    assert (
        classify_evidence_relation(
            "Path imbalance destroys the local-resonance condition.",
            counter_query=True,
        ).value
        == "COUNTER"
    )


def test_non_mechanism_passage_is_recorded_but_not_promoted() -> None:
    graph = curated_flat_band_tag_graph()
    queries = plan_tag_queries(
        graph,
        target_tag_ids=("electronic-flat-band",),
        budget=SearchBudgetV1(
            max_queries=1,
            max_direct_queries=1,
            max_bridge_queries=0,
            max_counter_queries=0,
            max_raw_hits=10,
            max_unique_documents=10,
        ),
    ).queries
    hits = (_hit("context", queries[0].query_id, "a"),)
    passages = (
        _passage(
            "context",
            text="The electronic flat band is measured through its dispersion.",
            matched_tags=("electronic-flat-band",),
            digest="a",
        ),
    )

    evidence = build_evidence_cards(
        graph=graph,
        queries=queries,
        hits=hits,
        passages=passages,
    )

    assert evidence.cards == ()
    assert evidence.warnings == ("NO_MECHANISM_TAG:passage-context",)


def _hit(name: str, query_id: str, digest: str) -> SearchHitV1:
    return SearchHitV1(
        hit_id=f"hit-{name}",
        document_id=f"document-{name}",
        provider="fixture",
        provider_record_id=f"record-{name}",
        query_ids=(query_id,),
        provider_rank=1,
        title=f"Fixture {name}",
        raw_response_artifact=artifact(f"raw/{name}.json", digest),
    )


def _passage(
    name: str,
    *,
    text: str,
    matched_tags: tuple[str, ...],
    digest: str,
) -> PassageV1:
    return PassageV1(
        passage_id=f"passage-{name}",
        hit_id=f"hit-{name}",
        document_id=f"document-{name}",
        source_artifact=artifact(f"raw/{name}.json", digest),
        normalizer=ComponentSnapshotV1(
            component_id="passage-normalizer",
            version="1",
            implementation_sha256="c" * 64,
        ),
        locator=PassageLocatorV1(
            kind=PassageLocatorKind.JSON_PATH,
            selector="$.abstract",
            section_heading="Abstract",
        ),
        text=text,
        char_count=len(text),
        estimated_token_count=len(text.split()),
        normalized_text_sha256=hashlib.sha256(text.encode()).hexdigest(),
        matched_tag_ids=matched_tags,
        lexical_score=0.9,
    )
