from __future__ import annotations

import hashlib

import pytest
from pydantic import ValidationError

from material_agent.inspiration.bridge import build_search_supported_bridges
from material_agent.inspiration.evidence import build_evidence_cards
from material_agent.inspiration.feedback import (
    AGGREGATION_SEMANTICS,
    EXPERT_STATUS,
    FEEDBACK_COMPILER_SNAPSHOT,
    FeedbackCompileError,
    FeedbackFetchAllocationV1,
    FeedbackInputArtifactV1,
    FeedbackVectorAllocationV1,
    TagFeedbackReviewV1,
    compile_tag_feedback_review,
    tag_feedback_review_bytes,
)
from material_agent.inspiration.models import (
    ArtifactPointerV1,
    ComponentSnapshotV1,
    EvidenceCardV1,
    EvidenceRelation,
    PassageLocatorKind,
    PassageLocatorV1,
    PassageV1,
    SearchHitV1,
    canonical_json_bytes,
)
from material_agent.inspiration.policy import SearchBudgetV1
from material_agent.inspiration.search import SearchAttemptRecord
from material_agent.inspiration.tag_graph import (
    curated_flat_band_tag_graph,
    plan_tag_queries,
)


def _sha(label: str) -> str:
    return hashlib.sha256(label.encode("utf-8")).hexdigest()


def _artifact(name: str, *, payload: bytes | None = None) -> ArtifactPointerV1:
    digest = hashlib.sha256(payload).hexdigest() if payload is not None else _sha(name)
    return ArtifactPointerV1(
        uri=f"artifact://feedback/{name}",
        sha256=digest,
        size_bytes=len(payload) if payload is not None else None,
        media_type="application/json",
    )


def _hit(name: str, query_id: str) -> SearchHitV1:
    return SearchHitV1(
        hit_id=f"hit-{name}",
        document_id=f"document-{name}",
        provider="fixture",
        provider_record_id=f"record-{name}",
        query_ids=(query_id,),
        provider_rank=1,
        title=f"Fixture {name}",
        raw_response_artifact=_artifact(f"raw-{name}.json"),
    )


def _passage(
    name: str,
    *,
    matched_tags: tuple[str, ...],
    text: str,
) -> PassageV1:
    return PassageV1(
        passage_id=f"passage-{name}",
        hit_id=f"hit-{name}",
        document_id=f"document-{name}",
        source_artifact=_artifact(f"body-{name}.html"),
        normalizer=ComponentSnapshotV1(
            component_id="test-normalizer",
            version="1",
            implementation_sha256=_sha("test-normalizer"),
        ),
        locator=PassageLocatorV1(
            kind=PassageLocatorKind.HTML_META,
            selector="meta[name=description]",
            section_heading="Abstract",
        ),
        text=text,
        char_count=len(text),
        estimated_token_count=len(text.split()),
        normalized_text_sha256=_sha(text),
        matched_tag_ids=matched_tags,
        lexical_score=0.9,
    )


def _full_fixture() -> dict[str, object]:
    graph = curated_flat_band_tag_graph()
    graph_payload = canonical_json_bytes(graph)
    planned = plan_tag_queries(
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
    acoustic = next(
        query
        for query in planned
        if query.bridge_rule_id
        == "acoustic-resonance-to-electronic-flat-band"
    )
    magnon = next(
        query
        for query in planned
        if query.bridge_rule_id
        == "magnon-line-graph-to-electronic-flat-band"
    )
    photonic = next(
        query
        for query in planned
        if query.bridge_rule_id
        == "photonic-interference-to-electronic-flat-band"
    )
    executed = (magnon, acoustic)
    hits = (
        _hit("magnon", magnon.query_id),
        _hit("acoustic", acoustic.query_id),
    )
    passages = (
        _passage(
            "magnon",
            matched_tags=("local-resonance",),
            text="A local resonance is reported, but no line-graph mechanism is shown.",
        ),
        _passage(
            "acoustic",
            matched_tags=("local-resonance",),
            text=(
                "A spectrally isolated local resonance produces a weakly "
                "dispersive mode."
            ),
        ),
    )
    evidence = build_evidence_cards(
        graph=graph,
        queries=executed,
        hits=hits,
        passages=passages,
    ).cards
    packets = build_search_supported_bridges(
        graph=graph,
        queries=executed,
        hits=hits,
        passages=passages,
        evidence_cards=evidence,
    ).packets
    attempts = (
        SearchAttemptRecord(
            query_id=photonic.query_id,
            attempt_number=1,
            outcome="error",
            error_code="FIXTURE_TIMEOUT",
            http_status=None,
            retry_delay_seconds=0.0,
            response_bytes=0,
        ),
        SearchAttemptRecord(
            query_id=acoustic.query_id,
            attempt_number=2,
            outcome="success",
            error_code=None,
            http_status=200,
            retry_delay_seconds=0.0,
            response_bytes=101,
        ),
        SearchAttemptRecord(
            query_id=magnon.query_id,
            attempt_number=1,
            outcome="success",
            error_code=None,
            http_status=200,
            retry_delay_seconds=0.0,
            response_bytes=89,
        ),
        SearchAttemptRecord(
            query_id=acoustic.query_id,
            attempt_number=1,
            outcome="error",
            error_code="FIXTURE_RETRY",
            http_status=503,
            retry_delay_seconds=0.0,
            response_bytes=17,
        ),
    )
    return {
        "run_id": "feedback-run-1",
        "graph": graph,
        "graph_artifact": _artifact("tag-graph.json", payload=graph_payload),
        "input_artifacts": (
            FeedbackInputArtifactV1(
                role="passages", artifact=_artifact("passages.jsonl")
            ),
            FeedbackInputArtifactV1(
                role="query-plan", artifact=_artifact("query-plan.jsonl")
            ),
        ),
        "planned_queries": planned,
        "executed_queries": executed,
        "search_attempts": attempts,
        "hits": hits,
        "passages": passages,
        "evidence_cards": evidence,
        "bridge_packets": packets,
        "fetch_allocations": (
            FeedbackFetchAllocationV1(
                document_id="document-acoustic",
                query_ids=(acoustic.query_id,),
                request_count=2,
                response_bytes=512,
            ),
        ),
        "vector_allocations": (
            FeedbackVectorAllocationV1(
                passage_id="passage-acoustic", input_token_count=7
            ),
            FeedbackVectorAllocationV1(
                passage_id="passage-magnon", input_token_count=9
            ),
        ),
    }


def test_feedback_covers_all_three_rules_without_making_a_graph_decision() -> None:
    fixture = _full_fixture()
    graph_before = canonical_json_bytes(fixture["graph"])

    review = compile_tag_feedback_review(**fixture)

    assert review.compiler == FEEDBACK_COMPILER_SNAPSHOT
    assert review.feedback_id.startswith("tag-feedback-")
    assert review.run_id == "feedback-run-1"
    assert review.aggregation_semantics == AGGREGATION_SEMANTICS
    assert review.expert_status == EXPERT_STATUS
    assert review.applies_to_tag_graph is False
    assert review.scientific_conclusion is False
    assert review.review_disposition == "REVIEW_ONLY"
    assert review.llm_calls == review.llm_input_tokens == review.llm_output_tokens == 0
    assert canonical_json_bytes(fixture["graph"]) == graph_before

    rows = {row.bridge_rule_id: row for row in review.bridge_rows}
    assert set(rows) == {
        rule.bridge_rule_id for rule in fixture["graph"].bridge_rules
    }
    assert all(row.expert_status == "UNKNOWN" for row in rows.values())
    assert all(row.activation == "ENABLED" for row in rows.values())
    assert rows["acoustic-resonance-to-electronic-flat-band"].status == (
        "SEARCH_SUPPORTED"
    )
    assert rows["acoustic-resonance-to-electronic-flat-band"].rule_version == "1"
    assert rows[
        "acoustic-resonance-to-electronic-flat-band"
    ].missing_required_evidence_tag_ids == ()
    assert rows["acoustic-resonance-to-electronic-flat-band"].fetch_request_count == 2
    assert (
        rows["acoustic-resonance-to-electronic-flat-band"].fetch_response_bytes
        == 512
    )
    assert rows[
        "acoustic-resonance-to-electronic-flat-band"
    ].inclusive_vectorized_passage_count == 1
    assert (
        rows["acoustic-resonance-to-electronic-flat-band"].embedding_input_tokens
        == 7
    )

    assert rows["magnon-line-graph-to-electronic-flat-band"].status == (
        "EVIDENCE_INSUFFICIENT"
    )
    assert rows[
        "magnon-line-graph-to-electronic-flat-band"
    ].missing_required_evidence_tag_ids == ("line-graph-localization",)
    assert rows["photonic-interference-to-electronic-flat-band"].status == (
        "NOT_EXECUTED"
    )
    assert (
        rows["photonic-interference-to-electronic-flat-band"].attempt_failure_count
        == 1
    )


def test_feedback_is_canonical_under_input_order_and_tracks_inclusive_costs() -> None:
    fixture = _full_fixture()
    first = compile_tag_feedback_review(**fixture)
    reordered = dict(fixture)
    for key in (
        "input_artifacts",
        "planned_queries",
        "executed_queries",
        "search_attempts",
        "hits",
        "passages",
        "evidence_cards",
        "bridge_packets",
        "fetch_allocations",
        "vector_allocations",
    ):
        reordered[key] = tuple(reversed(reordered[key]))
    second = compile_tag_feedback_review(**reordered)

    assert tag_feedback_review_bytes(first) == tag_feedback_review_bytes(second)
    query_rows = {row.query_id: row for row in first.query_rows}
    acoustic_query = next(
        row
        for row in first.query_rows
        if row.bridge_rule_id == "acoustic-resonance-to-electronic-flat-band"
    )
    assert query_rows[acoustic_query.query_id].attempt_success_count == 1
    assert query_rows[acoustic_query.query_id].attempt_failure_count == 1
    assert query_rows[acoustic_query.query_id].search_request_count == 2
    assert query_rows[acoustic_query.query_id].search_response_bytes == 118
    assert query_rows[acoustic_query.query_id].inclusive_hit_count == 1
    assert query_rows[acoustic_query.query_id].inclusive_unique_document_count == 1
    assert query_rows[acoustic_query.query_id].inclusive_passage_count == 1
    assert query_rows[acoustic_query.query_id].inclusive_evidence_card_count == 1

    local_resonance = next(
        row for row in first.tag_rows if row.tag_id == "local-resonance"
    )
    assert local_resonance.aggregation_semantics == "INCLUSIVE_NON_ADDITIVE"
    assert local_resonance.inclusive_hit_count == 1
    serialized = tag_feedback_review_bytes(first)
    assert b"replacement" not in serialized
    assert b"mutation" not in serialized


def test_unplanned_and_not_executed_are_distinct_for_every_curated_rule() -> None:
    graph = curated_flat_band_tag_graph()
    graph_payload = canonical_json_bytes(graph)
    planned = plan_tag_queries(
        graph,
        target_tag_ids=("electronic-flat-band",),
        budget=SearchBudgetV1(
            max_queries=2,
            max_direct_queries=1,
            max_bridge_queries=1,
            max_counter_queries=0,
            max_raw_hits=5,
            max_unique_documents=5,
        ),
    ).queries
    review = compile_tag_feedback_review(
        run_id="feedback-run-unexecuted",
        graph=graph,
        graph_artifact=_artifact("small-tag-graph.json", payload=graph_payload),
        input_artifacts=(
            FeedbackInputArtifactV1(
                role="query-plan", artifact=_artifact("small-query-plan.jsonl")
            ),
        ),
        planned_queries=planned,
        executed_queries=(),
        search_attempts=(),
        hits=(),
        passages=(),
        evidence_cards=(),
        bridge_packets=(),
    )

    rows = {row.bridge_rule_id: row for row in review.bridge_rows}
    assert rows["acoustic-resonance-to-electronic-flat-band"].status == "NOT_EXECUTED"
    assert rows["magnon-line-graph-to-electronic-flat-band"].status == "NOT_PLANNED"
    assert rows["photonic-interference-to-electronic-flat-band"].status == "NOT_PLANNED"


def test_pointer_tampering_and_multi_rule_evidence_fail_closed() -> None:
    fixture = _full_fixture()
    wrong_pointer_fixture = dict(fixture)
    wrong_pointer_fixture["graph_artifact"] = _artifact("wrong-graph.json")
    with pytest.raises(FeedbackCompileError, match="GRAPH_POINTER_HASH_MISMATCH"):
        compile_tag_feedback_review(**wrong_pointer_fixture)

    review = compile_tag_feedback_review(**fixture)
    payload = review.model_dump()
    input_artifacts = list(payload["input_artifacts"])
    first = dict(input_artifacts[0])
    pointer = dict(first["artifact"])
    pointer["sha256"] = "f" * 64
    first["artifact"] = pointer
    input_artifacts[0] = first
    payload["input_artifacts"] = tuple(input_artifacts)
    with pytest.raises(ValidationError, match="input_fingerprint_sha256"):
        TagFeedbackReviewV1.model_validate(payload)

    passages = fixture["passages"]
    mixed = EvidenceCardV1(
        evidence_card_id="evidence-mixed-lineage",
        relation=EvidenceRelation.SUPPORT,
        claim_text="This synthetic card must not be assigned to two bridge rules.",
        mechanism_tag_ids=("line-graph-localization", "local-resonance"),
        applicability_conditions=("fixture only",),
        passage_ids=tuple(sorted(passage.passage_id for passage in passages)),
    )
    mixed_fixture = dict(fixture)
    mixed_fixture["evidence_cards"] = (mixed,)
    mixed_fixture["bridge_packets"] = ()
    with pytest.raises(FeedbackCompileError, match="EVIDENCE_SPANS_BRIDGE_RULES"):
        compile_tag_feedback_review(**mixed_fixture)
