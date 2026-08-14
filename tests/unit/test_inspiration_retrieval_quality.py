from __future__ import annotations

from material_agent.inspiration.models import (
    ArtifactPointerV1,
    SearchHitV1,
    SearchQueryKind,
)
from material_agent.inspiration.policy import SearchBudgetV1
from material_agent.inspiration.retrieval_quality import (
    QueryCandidateOrigin,
    audit_metadata_hits,
    evaluate_metadata_quality_fixture,
)
from material_agent.inspiration.runner import _order_document_groups_for_extraction
from material_agent.inspiration.search import DocumentHitGroup
from material_agent.inspiration.tag_graph import (
    curated_flat_band_tag_graph,
    plan_tag_queries,
)


def _artifact(digest: str) -> ArtifactPointerV1:
    return ArtifactPointerV1(
        uri=f"artifact://stages/inspiration/run/raw_search/{digest}.json",
        sha256=digest * 64,
        size_bytes=128,
        media_type="application/json",
    )


def _hit(
    hit_id: str,
    *,
    rank: int,
    digest: str,
    doi: str | None,
    canonical_url: str | None,
    abstract: str | None,
    authors: tuple[str, ...] = (),
    published_year: int | None = None,
    keywords: tuple[str, ...] = (),
) -> SearchHitV1:
    return SearchHitV1(
        hit_id=hit_id,
        document_id=f"document-{hit_id}",
        provider="crossref",
        provider_record_id=f"record-{hit_id}",
        query_ids=("query-1",),
        provider_rank=rank,
        title=f"Metadata title {hit_id}",
        authors=authors,
        published_year=published_year,
        doi=doi,
        canonical_url=canonical_url,
        abstract=abstract,
        keywords=keywords,
        raw_response_artifact=_artifact(digest),
    )


def test_curated_candidate_pool_is_versioned_bounded_and_graph_derived() -> None:
    graph = curated_flat_band_tag_graph()
    plan = plan_tag_queries(
        graph,
        target_tag_ids=("electronic-flat-band",),
        budget=SearchBudgetV1(
            max_queries=5,
            max_physical_requests=9,
            max_direct_queries=1,
            max_bridge_queries=3,
            max_counter_queries=1,
            max_raw_hits=10,
            max_unique_documents=5,
        ),
    )
    assert plan.candidate_pool is not None
    assert plan.allocation_audit is not None
    pool = plan.candidate_pool
    known_tag_ids = {tag.tag_id for tag in graph.tags}
    known_rule_ids = {rule.bridge_rule_id for rule in graph.bridge_rules}

    assert pool.graph_id == graph.graph_id
    assert graph.graph_version in pool.pool_version
    assert len(pool.candidates) == 8
    assert {item.origin for item in pool.candidates} == {
        QueryCandidateOrigin.CURATED_TAG_TERMS,
        QueryCandidateOrigin.REVIEWED_BRIDGE_TEMPLATE,
        QueryCandidateOrigin.REVIEWED_BREAKING_CONDITION,
    }
    assert all(set(item.tag_ids) <= known_tag_ids for item in pool.candidates)
    assert all(
        item.bridge_rule_id is None or item.bridge_rule_id in known_rule_ids
        for item in pool.candidates
    )
    assert plan == plan_tag_queries(
        graph,
        target_tag_ids=("electronic-flat-band",),
        budget=SearchBudgetV1(
            max_queries=5,
            max_physical_requests=9,
            max_direct_queries=1,
            max_bridge_queries=3,
            max_counter_queries=1,
            max_raw_hits=10,
            max_unique_documents=5,
        ),
    )


def test_passage_budget_processes_metadata_quality_before_document_hash() -> None:
    lower = _hit(
        "hit-lower",
        rank=2,
        digest="a",
        doi="10.1000/lower",
        canonical_url="https://example.org/lower",
        abstract="short abstract",
    )
    higher = _hit(
        "hit-higher",
        rank=1,
        digest="b",
        doi="10.1000/higher",
        canonical_url="https://example.org/higher",
        abstract="complete mechanism abstract",
    )
    lower_group = DocumentHitGroup(
        document_id=lower.document_id,
        representative_hit_id=lower.hit_id,
        member_hit_ids=(lower.hit_id,),
    )
    higher_group = DocumentHitGroup(
        document_id=higher.document_id,
        representative_hit_id=higher.hit_id,
        member_hit_ids=(higher.hit_id,),
    )

    ordered = _order_document_groups_for_extraction(
        (lower_group, higher_group),
        hit_index={lower.hit_id: lower, higher.hit_id: higher},
        quality_rank_by_hit_id={lower.hit_id: (True, 2), higher.hit_id: (True, 1)},
    )

    assert tuple(item.document_id for item in ordered) == (
        higher.document_id,
        lower.document_id,
    )


def test_physical_request_slots_are_reserved_round_robin_across_classes() -> None:
    plan = plan_tag_queries(
        curated_flat_band_tag_graph(),
        target_tag_ids=("electronic-flat-band",),
        budget=SearchBudgetV1(
            max_queries=6,
            max_physical_requests=11,
            max_direct_queries=2,
            max_bridge_queries=3,
            max_counter_queries=1,
            max_raw_hits=10,
            max_unique_documents=5,
        ),
    )

    assert plan.allocation_audit is not None
    allowances = plan.allocation_audit.allowances
    assert tuple(item.kind for item in allowances) == (
        SearchQueryKind.DIRECT,
        SearchQueryKind.DIRECT,
        SearchQueryKind.BRIDGE,
        SearchQueryKind.BRIDGE,
        SearchQueryKind.BRIDGE,
        SearchQueryKind.COUNTER,
    )
    assert tuple(item.max_physical_requests for item in allowances) == (
        2,
        2,
        2,
        2,
        1,
        2,
    )
    assert sum(item.max_physical_requests for item in allowances) == 11
    assert plan.allocation_audit.audit_sha256 == plan.allocation_audit.audit_sha256


def test_metadata_quality_audit_retains_every_hit_and_all_rejection_reasons() -> None:
    complete = _hit(
        "hit-complete",
        rank=2,
        digest="a",
        doi="10.1000/complete",
        canonical_url="https://doi.org/10.1000/complete",
        abstract="A bounded abstract describing a compact localized mode. " * 8,
        authors=("Ada Example",),
        published_year=2025,
        keywords=("flat band",),
    )
    no_abstract = _hit(
        "hit-no-abstract",
        rank=1,
        digest="b",
        doi="10.1000/no-abstract",
        canonical_url="https://doi.org/10.1000/no-abstract",
        abstract=None,
    )
    provider_only = _hit(
        "hit-provider-only",
        rank=3,
        digest="c",
        doi=None,
        canonical_url=None,
        abstract=None,
    )

    audit = audit_metadata_hits(
        (provider_only, no_abstract, complete),
        require_abstract=True,
        min_source_quality_score=0.40,
    )

    assert audit.raw_hit_count == 3
    assert audit.eligible_hit_count == 1
    assert audit.rejected_hit_count == 2
    assert audit.ranked_hit_ids[0] == complete.hit_id
    assert {item.hit_id for item in audit.entries} == {
        complete.hit_id,
        no_abstract.hit_id,
        provider_only.hit_id,
    }
    assert all(item.retained for item in audit.entries)
    by_id = {item.hit_id: item for item in audit.entries}
    assert by_id[no_abstract.hit_id].rejection_reasons == (
        "ABSTRACT_REQUIRED_IN_METADATA_ONLY_MODE",
    )
    assert by_id[provider_only.hit_id].rejection_reasons == (
        "PERSISTENT_IDENTITY_MISSING",
        "ABSTRACT_REQUIRED_IN_METADATA_ONLY_MODE",
        "SOURCE_QUALITY_BELOW_FROZEN_THRESHOLD",
    )
    assert {item.raw_response_sha256 for item in audit.entries} == {
        "a" * 64,
        "b" * 64,
        "c" * 64,
    }
    assert audit.scientific_conclusion is False
    assert len(audit.audit_sha256) == 64


def test_synthetic_quality_evaluation_keeps_expert_and_multi_source_gates_open() -> None:
    eligible = _hit(
        "hit-eligible",
        rank=1,
        digest="d",
        doi="10.1000/eligible",
        canonical_url="https://doi.org/10.1000/eligible",
        abstract="Metadata-only evidence text.",
    )
    rejected = _hit(
        "hit-rejected",
        rank=2,
        digest="e",
        doi="10.1000/rejected",
        canonical_url="https://doi.org/10.1000/rejected",
        abstract=None,
    )
    audit = audit_metadata_hits(
        (rejected, eligible),
        require_abstract=True,
    )

    evaluation = evaluate_metadata_quality_fixture(
        audit,
        fixture_id="synthetic-metadata-quality-v1",
        expected_eligible_hit_ids=(eligible.hit_id,),
    )

    assert evaluation.fixture_recall == 1.0
    assert evaluation.fixture_precision == 1.0
    assert evaluation.synthetic_fixture_only is True
    assert evaluation.scientific_conclusion is False
    assert evaluation.external_gates == (
        "EXPERT_ADJUDICATED_GOLD_REQUIRED",
        "MULTI_SOURCE_RECALL_REQUIRED",
    )
