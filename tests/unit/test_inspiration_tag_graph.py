from __future__ import annotations

import pytest

from material_agent.inspiration import SearchQueryKind, TagKind
from material_agent.inspiration.policy import SearchBudgetV1
from material_agent.inspiration.tag_graph import (
    QueryPlanningError,
    curated_flat_band_tag_graph,
    plan_tag_queries,
)


def test_curated_graph_contains_reviewable_cross_domain_routes() -> None:
    graph = curated_flat_band_tag_graph()
    tags = {tag.tag_id: tag for tag in graph.tags}

    assert graph.curation_status == "CURATED"
    assert len(graph.bridge_rules) == 3
    assert {
        source_id
        for rule in graph.bridge_rules
        for source_id in rule.source_domain_tag_ids
    } == {"photonic-lattice", "acoustic-metamaterial", "frustrated-magnetism"}
    assert all(
        tags[source_id].kind is TagKind.ANALOGY_DOMAIN
        for rule in graph.bridge_rules
        for source_id in rule.source_domain_tag_ids
    )
    assert all(rule.shared_invariant for rule in graph.bridge_rules)
    assert all(rule.breaking_conditions for rule in graph.bridge_rules)


def test_query_plan_is_deterministic_bounded_and_rule_attributed() -> None:
    graph = curated_flat_band_tag_graph()
    budget = SearchBudgetV1(
        max_queries=6,
        max_direct_queries=1,
        max_bridge_queries=3,
        max_counter_queries=2,
        max_raw_hits=20,
        max_unique_documents=10,
    )

    first = plan_tag_queries(
        graph,
        target_tag_ids=(
            "electronic-flat-band",
            "layered-transition-metal-compound",
        ),
        budget=budget,
    )
    second = plan_tag_queries(
        graph,
        target_tag_ids=(
            "electronic-flat-band",
            "layered-transition-metal-compound",
        ),
        budget=budget,
    )

    assert first == second
    assert len(first.queries) == 6
    assert sum(q.kind is SearchQueryKind.DIRECT for q in first.queries) == 1
    assert sum(q.kind is SearchQueryKind.BRIDGE for q in first.queries) == 3
    assert sum(q.kind is SearchQueryKind.COUNTER for q in first.queries) == 2
    assert all(
        query.bridge_rule_id is not None
        for query in first.queries
        if query.kind is not SearchQueryKind.DIRECT
    )
    assert len({query.query_id for query in first.queries}) == len(first.queries)


def test_bridge_budget_records_skipped_routes_instead_of_hiding_them() -> None:
    graph = curated_flat_band_tag_graph()
    budget = SearchBudgetV1(
        max_queries=2,
        max_direct_queries=1,
        max_bridge_queries=1,
        max_counter_queries=0,
        max_raw_hits=10,
        max_unique_documents=5,
    )

    plan = plan_tag_queries(
        graph,
        target_tag_ids=("electronic-flat-band",),
        budget=budget,
    )

    assert len(plan.queries) == 2
    assert len(plan.skipped_rule_ids) == 2


def test_unknown_target_and_unknown_template_placeholder_fail_closed() -> None:
    graph = curated_flat_band_tag_graph()
    with pytest.raises(QueryPlanningError, match="unknown target"):
        plan_tag_queries(
            graph,
            target_tag_ids=("missing-target",),
            budget=SearchBudgetV1(),
        )

    first_rule = graph.bridge_rules[0]
    bad_rule = first_rule.model_copy(update={"query_templates": ("{invented}",)})
    bad_graph = graph.model_copy(
        update={"bridge_rules": (bad_rule, *graph.bridge_rules[1:])}
    )
    with pytest.raises(QueryPlanningError, match="unknown placeholders"):
        plan_tag_queries(
            bad_graph,
            target_tag_ids=("electronic-flat-band",),
            budget=SearchBudgetV1(),
        )


def test_graph_and_queries_do_not_encode_external_originality_judgments() -> None:
    graph = curated_flat_band_tag_graph()
    plan = plan_tag_queries(
        graph,
        target_tag_ids=("electronic-flat-band",),
        budget=SearchBudgetV1(),
    )
    text = repr((graph.model_dump(mode="json"), plan)).lower()

    for forbidden in ("novelty", "prior-art", "prior_art", "unprecedented"):
        assert forbidden not in text
