"""Immutable, review-only yield feedback for the curated inspiration TagGraph.

This module deliberately produces a sidecar artifact.  It does not expose a
graph update, replacement, or mutation operation: observed search yield is
engineering calibration evidence, and expert scientific review remains
``UNKNOWN`` until it happens outside the runtime.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated, Literal

from pydantic import Field, model_validator

from material_agent.inspiration.models import (
    ArtifactPointerV1,
    BridgePacketV1,
    ComponentSnapshotV1,
    EvidenceCardV1,
    EvidenceRelation,
    Identifier,
    PassageV1,
    SearchHitV1,
    SearchQueryKind,
    SearchQueryV1,
    Sha256,
    StrictModel,
    TagGraphV1,
    TagKind,
    canonical_json_bytes,
    canonical_sha256,
    deterministic_id,
)
from material_agent.inspiration.search import SearchAttemptRecord


TAG_FEEDBACK_REVIEW_VERSION = "inspiration-tag-feedback-review-v1"
AGGREGATION_SEMANTICS = "INCLUSIVE_NON_ADDITIVE"
EXPERT_STATUS = "UNKNOWN"

_FEEDBACK_COMPILER_SPEC = {
    "component": "inspiration-tag-feedback-compiler",
    "version": "1",
    "artifact_role": "review-only-sidecar",
    "aggregation": AGGREGATION_SEMANTICS,
    "cost_attribution": (
        "attempt-per-query+fetch-per-document+vector-per-selected-passage"
    ),
    "evidence_attribution": (
        "lowest-passage-id-representative-hit-query-lineage"
    ),
    "bridge_status_precedence": (
        "SEARCH_SUPPORTED",
        "NOT_PLANNED",
        "NOT_EXECUTED",
        "EVIDENCE_INSUFFICIENT",
    ),
    "expert_status": EXPERT_STATUS,
    "tag_graph_mutation": False,
    "scientific_conclusion": False,
    "llm_calls": 0,
}
FEEDBACK_COMPILER_SNAPSHOT = ComponentSnapshotV1(
    component_id="inspiration-tag-feedback-compiler",
    version="1",
    implementation_sha256=canonical_sha256(_FEEDBACK_COMPILER_SPEC),
)


class FeedbackCompileError(ValueError):
    """The feedback inputs do not close over one immutable run."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(f"{code}: {message}")


class FeedbackInputArtifactV1(StrictModel):
    """A named, immutable input pointer included in the feedback fingerprint."""

    role: Identifier
    artifact: ArtifactPointerV1


class FeedbackFetchAllocationV1(StrictModel):
    """One physical document-fetch cost attributed inclusively to its queries.

    A document is fetched at most once by the runner.  When duplicate search
    hits connect that document to multiple queries, the same physical cost is
    visible in each relevant query row and is intentionally non-additive.
    """

    document_id: Identifier
    query_ids: Annotated[tuple[Identifier, ...], Field(min_length=1, max_length=64)]
    request_count: Annotated[int, Field(ge=1, le=64)]
    response_bytes: Annotated[int, Field(ge=0)]

    @model_validator(mode="after")
    def validate_query_ids(self) -> FeedbackFetchAllocationV1:
        if self.query_ids != tuple(sorted(set(self.query_ids))):
            raise ValueError("fetch allocation query_ids must be sorted and unique")
        return self


class FeedbackVectorAllocationV1(StrictModel):
    """Selected-passage vector cost; no document/full-text input is accepted."""

    passage_id: Identifier
    input_token_count: Annotated[int, Field(ge=1, le=1_000_000)]


class QueryFeedbackRowV1(StrictModel):
    query_id: Identifier
    kind: SearchQueryKind
    bridge_rule_id: Identifier | None = None
    tag_ids: Annotated[tuple[Identifier, ...], Field(min_length=1, max_length=32)]
    planned: Literal[True] = True
    executed: bool
    attempt_success_count: Annotated[int, Field(ge=0)]
    attempt_failure_count: Annotated[int, Field(ge=0)]
    inclusive_hit_count: Annotated[int, Field(ge=0)]
    inclusive_unique_document_count: Annotated[int, Field(ge=0)]
    inclusive_passage_count: Annotated[int, Field(ge=0)]
    inclusive_vectorized_passage_count: Annotated[int, Field(ge=0)]
    embedding_input_tokens: Annotated[int, Field(ge=0)]
    inclusive_evidence_card_count: Annotated[int, Field(ge=0)]
    inclusive_bridge_packet_count: Annotated[int, Field(ge=0)]
    search_request_count: Annotated[int, Field(ge=0)]
    search_response_bytes: Annotated[int, Field(ge=0)]
    fetch_request_count: Annotated[int, Field(ge=0)]
    fetch_response_bytes: Annotated[int, Field(ge=0)]

    @model_validator(mode="after")
    def validate_query_row(self) -> QueryFeedbackRowV1:
        if self.tag_ids != tuple(sorted(set(self.tag_ids))):
            raise ValueError("query feedback tag_ids must be sorted and unique")
        if self.search_request_count != (
            self.attempt_success_count + self.attempt_failure_count
        ):
            raise ValueError("search_request_count must equal the attempt counts")
        return self


class TagFeedbackRowV1(StrictModel):
    tag_id: Identifier
    kind: TagKind
    aggregation_semantics: Literal["INCLUSIVE_NON_ADDITIVE"] = (
        AGGREGATION_SEMANTICS
    )
    planned_query_count: Annotated[int, Field(ge=0)]
    executed_query_count: Annotated[int, Field(ge=0)]
    attempt_success_count: Annotated[int, Field(ge=0)]
    attempt_failure_count: Annotated[int, Field(ge=0)]
    inclusive_hit_count: Annotated[int, Field(ge=0)]
    inclusive_unique_document_count: Annotated[int, Field(ge=0)]
    inclusive_passage_count: Annotated[int, Field(ge=0)]
    inclusive_vectorized_passage_count: Annotated[int, Field(ge=0)]
    embedding_input_tokens: Annotated[int, Field(ge=0)]
    inclusive_evidence_card_count: Annotated[int, Field(ge=0)]
    inclusive_bridge_packet_count: Annotated[int, Field(ge=0)]
    search_request_count: Annotated[int, Field(ge=0)]
    search_response_bytes: Annotated[int, Field(ge=0)]
    fetch_request_count: Annotated[int, Field(ge=0)]
    fetch_response_bytes: Annotated[int, Field(ge=0)]

    @model_validator(mode="after")
    def validate_tag_row(self) -> TagFeedbackRowV1:
        if self.executed_query_count > self.planned_query_count:
            raise ValueError("executed tag queries cannot exceed planned queries")
        if self.search_request_count != (
            self.attempt_success_count + self.attempt_failure_count
        ):
            raise ValueError("search_request_count must equal the attempt counts")
        return self


class BridgeFeedbackRowV1(StrictModel):
    bridge_rule_id: Identifier
    rule_version: Annotated[str, Field(min_length=1, max_length=512)]
    activation: Literal["ENABLED"] = "ENABLED"
    status: Literal[
        "SEARCH_SUPPORTED",
        "EVIDENCE_INSUFFICIENT",
        "NOT_PLANNED",
        "NOT_EXECUTED",
    ]
    expert_status: Literal["UNKNOWN"] = EXPERT_STATUS
    required_evidence_tag_ids: Annotated[
        tuple[Identifier, ...], Field(min_length=1, max_length=32)
    ]
    missing_required_evidence_tag_ids: Annotated[
        tuple[Identifier, ...], Field(max_length=32)
    ] = ()
    observed_support_tag_ids: Annotated[
        tuple[Identifier, ...], Field(max_length=32)
    ] = ()
    evidence_card_ids: Annotated[tuple[Identifier, ...], Field(max_length=256)] = ()
    bridge_packet_ids: Annotated[tuple[Identifier, ...], Field(max_length=16)] = ()
    planned_query_count: Annotated[int, Field(ge=0)]
    executed_query_count: Annotated[int, Field(ge=0)]
    attempt_success_count: Annotated[int, Field(ge=0)]
    attempt_failure_count: Annotated[int, Field(ge=0)]
    inclusive_hit_count: Annotated[int, Field(ge=0)]
    inclusive_unique_document_count: Annotated[int, Field(ge=0)]
    inclusive_passage_count: Annotated[int, Field(ge=0)]
    inclusive_vectorized_passage_count: Annotated[int, Field(ge=0)]
    embedding_input_tokens: Annotated[int, Field(ge=0)]
    inclusive_evidence_card_count: Annotated[int, Field(ge=0)]
    inclusive_bridge_packet_count: Annotated[int, Field(ge=0)]
    search_request_count: Annotated[int, Field(ge=0)]
    search_response_bytes: Annotated[int, Field(ge=0)]
    fetch_request_count: Annotated[int, Field(ge=0)]
    fetch_response_bytes: Annotated[int, Field(ge=0)]

    @model_validator(mode="after")
    def validate_bridge_row(self) -> BridgeFeedbackRowV1:
        for label, values in (
            ("required_evidence_tag_ids", self.required_evidence_tag_ids),
            (
                "missing_required_evidence_tag_ids",
                self.missing_required_evidence_tag_ids,
            ),
            ("observed_support_tag_ids", self.observed_support_tag_ids),
            ("evidence_card_ids", self.evidence_card_ids),
            ("bridge_packet_ids", self.bridge_packet_ids),
        ):
            if values != tuple(sorted(set(values))):
                raise ValueError(f"{label} must be sorted and unique")
        expected_missing = tuple(
            sorted(
                set(self.required_evidence_tag_ids)
                - set(self.observed_support_tag_ids)
            )
        )
        if self.missing_required_evidence_tag_ids != expected_missing:
            raise ValueError(
                "missing_required_evidence_tag_ids must equal required minus observed"
            )
        if self.executed_query_count > self.planned_query_count:
            raise ValueError("executed bridge queries cannot exceed planned queries")
        if self.search_request_count != (
            self.attempt_success_count + self.attempt_failure_count
        ):
            raise ValueError("search_request_count must equal the attempt counts")
        if self.inclusive_evidence_card_count != len(self.evidence_card_ids):
            raise ValueError("bridge evidence count must match evidence_card_ids")
        if self.inclusive_bridge_packet_count != len(self.bridge_packet_ids):
            raise ValueError("bridge packet count must match bridge_packet_ids")
        if (self.status == "SEARCH_SUPPORTED") != bool(self.bridge_packet_ids):
            raise ValueError("only SEARCH_SUPPORTED rows may contain a bridge packet")
        return self


class TagFeedbackReviewV1(StrictModel):
    """Deterministic calibration artifact that cannot modify the curated graph."""

    schema_version: Literal["inspiration-tag-feedback-review-v1"] = (
        TAG_FEEDBACK_REVIEW_VERSION
    )
    compiler: ComponentSnapshotV1 = FEEDBACK_COMPILER_SNAPSHOT
    feedback_id: Identifier
    run_id: Identifier
    graph_id: Identifier
    graph_version: Annotated[str, Field(min_length=1, max_length=512)]
    graph_artifact: ArtifactPointerV1
    input_artifacts: Annotated[
        tuple[FeedbackInputArtifactV1, ...], Field(min_length=1, max_length=512)
    ]
    semantic_input_sha256: Sha256
    input_fingerprint_sha256: Sha256
    aggregation_semantics: Literal["INCLUSIVE_NON_ADDITIVE"] = (
        AGGREGATION_SEMANTICS
    )
    expert_status: Literal["UNKNOWN"] = EXPERT_STATUS
    review_disposition: Literal["REVIEW_ONLY"] = "REVIEW_ONLY"
    applies_to_tag_graph: Literal[False] = False
    scientific_conclusion: Literal[False] = False
    llm_calls: Literal[0] = 0
    llm_input_tokens: Literal[0] = 0
    llm_output_tokens: Literal[0] = 0
    query_rows: Annotated[
        tuple[QueryFeedbackRowV1, ...], Field(min_length=1, max_length=512)
    ]
    tag_rows: Annotated[
        tuple[TagFeedbackRowV1, ...], Field(min_length=1, max_length=2_000)
    ]
    bridge_rows: Annotated[tuple[BridgeFeedbackRowV1, ...], Field(max_length=1_000)]

    @model_validator(mode="after")
    def validate_review(self) -> TagFeedbackReviewV1:
        artifact_keys = tuple(
            (item.role, item.artifact.uri, item.artifact.sha256)
            for item in self.input_artifacts
        )
        if artifact_keys != tuple(sorted(set(artifact_keys))):
            raise ValueError("input_artifacts must be canonically sorted and unique")
        roles = tuple(item.role for item in self.input_artifacts)
        if len(roles) != len(set(roles)):
            raise ValueError("input artifact roles must be unique")
        for label, rows, attribute in (
            ("query", self.query_rows, "query_id"),
            ("tag", self.tag_rows, "tag_id"),
            ("bridge", self.bridge_rows, "bridge_rule_id"),
        ):
            identifiers = tuple(getattr(row, attribute) for row in rows)
            if identifiers != tuple(sorted(set(identifiers))):
                raise ValueError(f"{label} rows must be sorted and unique")
        expected = feedback_input_fingerprint_for(
            graph_artifact=self.graph_artifact,
            input_artifacts=self.input_artifacts,
            semantic_input_sha256=self.semantic_input_sha256,
        )
        if self.input_fingerprint_sha256 != expected:
            raise ValueError("input_fingerprint_sha256 does not match input pointers")
        expected_id = deterministic_id(
            "tag-feedback",
            {
                "run_id": self.run_id,
                "graph_id": self.graph_id,
                "graph_version": self.graph_version,
                "input_fingerprint_sha256": self.input_fingerprint_sha256,
            },
        )
        if self.feedback_id != expected_id:
            raise ValueError("feedback_id does not match the immutable run inputs")
        return self


def feedback_input_fingerprint_for(
    *,
    graph_artifact: ArtifactPointerV1,
    input_artifacts: tuple[FeedbackInputArtifactV1, ...],
    semantic_input_sha256: str,
) -> str:
    """Bind semantic inputs to every immutable pointer used by the compiler."""

    return canonical_sha256(
        {
            "fingerprint_version": "inspiration-tag-feedback-input-v1",
            "graph_artifact": graph_artifact,
            "input_artifacts": input_artifacts,
            "semantic_input_sha256": semantic_input_sha256,
        }
    )


@dataclass(frozen=True, slots=True)
class _YieldMetrics:
    attempt_success_count: int
    attempt_failure_count: int
    inclusive_hit_count: int
    inclusive_unique_document_count: int
    inclusive_passage_count: int
    inclusive_vectorized_passage_count: int
    embedding_input_tokens: int
    inclusive_evidence_card_count: int
    inclusive_bridge_packet_count: int
    search_request_count: int
    search_response_bytes: int
    fetch_request_count: int
    fetch_response_bytes: int

    def as_dict(self) -> dict[str, int]:
        return {
            "attempt_success_count": self.attempt_success_count,
            "attempt_failure_count": self.attempt_failure_count,
            "inclusive_hit_count": self.inclusive_hit_count,
            "inclusive_unique_document_count": (
                self.inclusive_unique_document_count
            ),
            "inclusive_passage_count": self.inclusive_passage_count,
            "inclusive_vectorized_passage_count": (
                self.inclusive_vectorized_passage_count
            ),
            "embedding_input_tokens": self.embedding_input_tokens,
            "inclusive_evidence_card_count": self.inclusive_evidence_card_count,
            "inclusive_bridge_packet_count": self.inclusive_bridge_packet_count,
            "search_request_count": self.search_request_count,
            "search_response_bytes": self.search_response_bytes,
            "fetch_request_count": self.fetch_request_count,
            "fetch_response_bytes": self.fetch_response_bytes,
        }


def compile_tag_feedback_review(
    *,
    run_id: str,
    graph: TagGraphV1,
    graph_artifact: ArtifactPointerV1,
    input_artifacts: tuple[FeedbackInputArtifactV1, ...],
    planned_queries: tuple[SearchQueryV1, ...],
    executed_queries: tuple[SearchQueryV1, ...],
    search_attempts: tuple[SearchAttemptRecord, ...],
    hits: tuple[SearchHitV1, ...],
    passages: tuple[PassageV1, ...],
    evidence_cards: tuple[EvidenceCardV1, ...],
    bridge_packets: tuple[BridgePacketV1, ...],
    fetch_allocations: tuple[FeedbackFetchAllocationV1, ...] = (),
    vector_allocations: tuple[FeedbackVectorAllocationV1, ...] = (),
) -> TagFeedbackReviewV1:
    """Compile byte-stable yield calibration without changing ``graph``.

    Counts are inclusive within each row.  Consequently one shared document or
    physical fetch can appear in multiple query/tag rows and row totals must not
    be added to recover global totals.
    """

    graph_bytes = canonical_json_bytes(graph)
    if graph_artifact.sha256 != canonical_sha256(graph):
        raise FeedbackCompileError(
            "GRAPH_POINTER_HASH_MISMATCH",
            "graph_artifact does not identify the supplied canonical TagGraph",
        )
    if (
        graph_artifact.size_bytes is not None
        and graph_artifact.size_bytes != len(graph_bytes)
    ):
        raise FeedbackCompileError(
            "GRAPH_POINTER_SIZE_MISMATCH",
            "graph_artifact size does not match canonical TagGraph bytes",
        )
    if graph_artifact.media_type not in {None, "application/json"}:
        raise FeedbackCompileError(
            "GRAPH_POINTER_MEDIA_TYPE_MISMATCH",
            "graph_artifact must use application/json when media_type is present",
        )

    planned = _normalize_models(planned_queries, "query_id", "planned query")
    if not planned:
        raise FeedbackCompileError(
            "EMPTY_QUERY_PLAN", "feedback requires at least one planned query"
        )
    executed = _normalize_models(executed_queries, "query_id", "executed query")
    hit_rows = _normalize_models(hits, "hit_id", "search hit")
    passage_rows = _normalize_models(passages, "passage_id", "passage")
    card_rows = _normalize_models(
        evidence_cards, "evidence_card_id", "evidence card"
    )
    packet_rows = _normalize_models(
        bridge_packets, "bridge_packet_id", "bridge packet"
    )
    artifact_rows = _normalize_input_artifacts(input_artifacts)
    attempt_rows = _normalize_attempts(search_attempts)
    fetch_rows = _normalize_fetch_allocations(fetch_allocations)
    vector_rows = _normalize_vector_allocations(vector_allocations)

    planned_by_id = {query.query_id: query for query in planned}
    executed_by_id = {query.query_id: query for query in executed}
    for query_id, query in executed_by_id.items():
        planned_query = planned_by_id.get(query_id)
        if planned_query is None:
            raise FeedbackCompileError(
                "UNPLANNED_EXECUTED_QUERY",
                f"executed query {query_id!r} is absent from the query plan",
            )
        if query != planned_query:
            raise FeedbackCompileError(
                "QUERY_DEFINITION_MISMATCH",
                f"executed query {query_id!r} differs from the planned query",
            )

    graph_tags = {tag.tag_id: tag for tag in graph.tags}
    graph_rules = {rule.bridge_rule_id: rule for rule in graph.bridge_rules}
    for query in planned:
        unknown_tags = sorted(set(query.tag_ids) - set(graph_tags))
        if unknown_tags:
            raise FeedbackCompileError(
                "QUERY_UNKNOWN_TAG",
                f"query {query.query_id!r} references unknown tags {unknown_tags!r}",
            )
        if (
            query.bridge_rule_id is not None
            and query.bridge_rule_id not in graph_rules
        ):
            raise FeedbackCompileError(
                "QUERY_UNKNOWN_BRIDGE_RULE",
                f"query {query.query_id!r} references an unknown bridge rule",
            )

    for attempt in attempt_rows:
        if attempt.query_id not in planned_by_id:
            raise FeedbackCompileError(
                "ATTEMPT_UNKNOWN_QUERY",
                f"search attempt references unplanned query {attempt.query_id!r}",
            )
    successful_query_ids = {
        attempt.query_id for attempt in attempt_rows if attempt.outcome == "success"
    }
    missing_success = sorted(set(executed_by_id) - successful_query_ids)
    if missing_success:
        raise FeedbackCompileError(
            "EXECUTED_QUERY_WITHOUT_SUCCESS",
            f"executed queries have no successful attempt: {missing_success!r}",
        )

    hits_by_id = {hit.hit_id: hit for hit in hit_rows}
    for hit in hit_rows:
        unknown_queries = sorted(set(hit.query_ids) - set(executed_by_id))
        if unknown_queries:
            raise FeedbackCompileError(
                "HIT_UNKNOWN_EXECUTED_QUERY",
                f"hit {hit.hit_id!r} references non-executed queries "
                f"{unknown_queries!r}",
            )

    passages_by_id = {passage.passage_id: passage for passage in passage_rows}
    for passage in passage_rows:
        hit = hits_by_id.get(passage.hit_id)
        if hit is None:
            raise FeedbackCompileError(
                "PASSAGE_UNKNOWN_HIT",
                f"passage {passage.passage_id!r} references a missing hit",
            )
        if passage.document_id != hit.document_id:
            raise FeedbackCompileError(
                "PASSAGE_DOCUMENT_MISMATCH",
                f"passage {passage.passage_id!r} disagrees with its hit document",
            )

    for allocation in vector_rows:
        if allocation.passage_id not in passages_by_id:
            raise FeedbackCompileError(
                "VECTOR_UNKNOWN_PASSAGE",
                "vector allocation references missing passage "
                f"{allocation.passage_id!r}",
            )

    for allocation in fetch_rows:
        unknown_queries = sorted(set(allocation.query_ids) - set(executed_by_id))
        if unknown_queries:
            raise FeedbackCompileError(
                "FETCH_UNKNOWN_EXECUTED_QUERY",
                f"fetch allocation references non-executed queries {unknown_queries!r}",
            )

    evidence_query_ids: dict[str, frozenset[str]] = {}
    evidence_rule_ids: dict[str, str | None] = {}
    for card in card_rows:
        referenced: list[PassageV1] = []
        for passage_id in card.passage_ids:
            passage = passages_by_id.get(passage_id)
            if passage is None:
                raise FeedbackCompileError(
                    "EVIDENCE_UNKNOWN_PASSAGE",
                    f"evidence card {card.evidence_card_id!r} references a "
                    "missing passage",
                )
            referenced.append(passage)
        all_rule_ids = {
            planned_by_id[query_id].bridge_rule_id
            for passage in referenced
            for query_id in hits_by_id[passage.hit_id].query_ids
            if planned_by_id[query_id].bridge_rule_id is not None
        }
        if len(all_rule_ids) > 1:
            raise FeedbackCompileError(
                "EVIDENCE_SPANS_BRIDGE_RULES",
                f"evidence card {card.evidence_card_id!r} spans multiple "
                "bridge rules",
            )
        representative = min(referenced, key=lambda item: item.passage_id)
        representative_hit = hits_by_id[representative.hit_id]
        evidence_query_ids[card.evidence_card_id] = frozenset(
            representative_hit.query_ids
        )
        evidence_rule_ids[card.evidence_card_id] = next(
            iter(all_rule_ids), None
        )

    cards_by_id = {card.evidence_card_id: card for card in card_rows}
    packets_by_rule: dict[str, BridgePacketV1] = {}
    for packet in packet_rows:
        rule = graph_rules.get(packet.bridge_rule_id)
        if rule is None:
            raise FeedbackCompileError(
                "PACKET_UNKNOWN_BRIDGE_RULE",
                f"bridge packet {packet.bridge_packet_id!r} references an unknown rule",
            )
        if packet.bridge_rule_id in packets_by_rule:
            raise FeedbackCompileError(
                "DUPLICATE_RULE_PACKET",
                f"multiple bridge packets claim rule {packet.bridge_rule_id!r}",
            )
        packet_cards: list[EvidenceCardV1] = []
        for card_id in packet.evidence_card_ids:
            card = cards_by_id.get(card_id)
            if card is None:
                raise FeedbackCompileError(
                    "PACKET_UNKNOWN_EVIDENCE",
                    f"bridge packet {packet.bridge_packet_id!r} references "
                    "missing evidence",
                )
            if evidence_rule_ids[card_id] != packet.bridge_rule_id:
                raise FeedbackCompileError(
                    "PACKET_EVIDENCE_RULE_MISMATCH",
                    "a bridge packet cannot assign direct or other-rule evidence "
                    f"to {packet.bridge_rule_id!r}",
                )
            packet_cards.append(card)
        supported_tags = {
            tag_id
            for card in packet_cards
            if card.relation is EvidenceRelation.SUPPORT
            for tag_id in card.mechanism_tag_ids
        }
        missing_tags = sorted(
            set(rule.required_evidence_tag_ids) - supported_tags
        )
        if missing_tags:
            raise FeedbackCompileError(
                "PACKET_REQUIRED_EVIDENCE_MISSING",
                f"bridge packet {packet.bridge_packet_id!r} lacks SUPPORT tags "
                f"{missing_tags!r}",
            )
        packets_by_rule[packet.bridge_rule_id] = packet

    packet_query_ids = {
        packet.bridge_packet_id: frozenset(
            query_id
            for card_id in packet.evidence_card_ids
            for query_id in evidence_query_ids[card_id]
        )
        for packet in packet_rows
    }

    def metrics(query_ids: frozenset[str]) -> _YieldMetrics:
        relevant_attempts = tuple(
            attempt for attempt in attempt_rows if attempt.query_id in query_ids
        )
        relevant_hits = tuple(
            hit for hit in hit_rows if query_ids.intersection(hit.query_ids)
        )
        relevant_hit_ids = {hit.hit_id for hit in relevant_hits}
        relevant_passage_ids = {
            passage.passage_id
            for passage in passage_rows
            if passage.hit_id in relevant_hit_ids
        }
        relevant_vectors = tuple(
            allocation
            for allocation in vector_rows
            if allocation.passage_id in relevant_passage_ids
        )
        relevant_evidence_ids = {
            card_id
            for card_id, card_query_ids in evidence_query_ids.items()
            if query_ids.intersection(card_query_ids)
        }
        relevant_packet_ids = {
            packet_id
            for packet_id, query_membership in packet_query_ids.items()
            if query_ids.intersection(query_membership)
        }
        relevant_fetches = tuple(
            allocation
            for allocation in fetch_rows
            if query_ids.intersection(allocation.query_ids)
        )
        successes = sum(
            attempt.outcome == "success" for attempt in relevant_attempts
        )
        failures = len(relevant_attempts) - successes
        return _YieldMetrics(
            attempt_success_count=successes,
            attempt_failure_count=failures,
            inclusive_hit_count=len(relevant_hits),
            inclusive_unique_document_count=len(
                {hit.document_id for hit in relevant_hits}
            ),
            inclusive_passage_count=len(relevant_passage_ids),
            inclusive_vectorized_passage_count=len(relevant_vectors),
            embedding_input_tokens=sum(
                allocation.input_token_count for allocation in relevant_vectors
            ),
            inclusive_evidence_card_count=len(relevant_evidence_ids),
            inclusive_bridge_packet_count=len(relevant_packet_ids),
            search_request_count=len(relevant_attempts),
            search_response_bytes=sum(
                attempt.response_bytes for attempt in relevant_attempts
            ),
            fetch_request_count=sum(
                allocation.request_count for allocation in relevant_fetches
            ),
            fetch_response_bytes=sum(
                allocation.response_bytes for allocation in relevant_fetches
            ),
        )

    query_rows = tuple(
        QueryFeedbackRowV1(
            query_id=query.query_id,
            kind=query.kind,
            bridge_rule_id=query.bridge_rule_id,
            tag_ids=tuple(sorted(query.tag_ids)),
            executed=query.query_id in executed_by_id,
            **metrics(frozenset({query.query_id})).as_dict(),
        )
        for query in planned
    )

    tag_rows = tuple(
        TagFeedbackRowV1(
            tag_id=tag.tag_id,
            kind=tag.kind,
            planned_query_count=len(
                query_ids := frozenset(
                    query.query_id for query in planned if tag.tag_id in query.tag_ids
                )
            ),
            executed_query_count=len(query_ids.intersection(executed_by_id)),
            **metrics(query_ids).as_dict(),
        )
        for tag in sorted(graph.tags, key=lambda item: item.tag_id)
    )

    bridge_rows: list[BridgeFeedbackRowV1] = []
    for rule in sorted(graph.bridge_rules, key=lambda item: item.bridge_rule_id):
        rule_query_ids = frozenset(
            query.query_id
            for query in planned
            if query.bridge_rule_id == rule.bridge_rule_id
        )
        executed_rule_query_ids = rule_query_ids.intersection(executed_by_id)
        packet = packets_by_rule.get(rule.bridge_rule_id)
        if packet is not None:
            status = "SEARCH_SUPPORTED"
        elif not rule_query_ids:
            status = "NOT_PLANNED"
        elif not executed_rule_query_ids:
            status = "NOT_EXECUTED"
        else:
            status = "EVIDENCE_INSUFFICIENT"
        rule_card_ids = tuple(
            sorted(
                card.evidence_card_id
                for card in card_rows
                if evidence_rule_ids[card.evidence_card_id] == rule.bridge_rule_id
            )
        )
        observed_support_tags = tuple(
            sorted(
                {
                    tag_id
                    for card_id in rule_card_ids
                    if cards_by_id[card_id].relation is EvidenceRelation.SUPPORT
                    for tag_id in cards_by_id[card_id].mechanism_tag_ids
                }
            )
        )
        bridge_rows.append(
            BridgeFeedbackRowV1(
                bridge_rule_id=rule.bridge_rule_id,
                rule_version=rule.rule_version,
                status=status,
                required_evidence_tag_ids=tuple(
                    sorted(rule.required_evidence_tag_ids)
                ),
                observed_support_tag_ids=observed_support_tags,
                missing_required_evidence_tag_ids=tuple(
                    sorted(
                        set(rule.required_evidence_tag_ids)
                        - set(observed_support_tags)
                    )
                ),
                evidence_card_ids=rule_card_ids,
                bridge_packet_ids=(packet.bridge_packet_id,) if packet else (),
                planned_query_count=len(rule_query_ids),
                executed_query_count=len(executed_rule_query_ids),
                **metrics(rule_query_ids).as_dict(),
            )
        )

    semantic_input_sha256 = canonical_sha256(
        {
            "semantic_version": "inspiration-tag-feedback-semantic-input-v1",
            "run_id": run_id,
            "graph": graph,
            "planned_queries": planned,
            "executed_queries": executed,
            "search_attempts": tuple(attempt.to_dict() for attempt in attempt_rows),
            "hits": hit_rows,
            "passages": passage_rows,
            "evidence_cards": card_rows,
            "bridge_packets": packet_rows,
            "fetch_allocations": fetch_rows,
            "vector_allocations": vector_rows,
        }
    )
    fingerprint = feedback_input_fingerprint_for(
        graph_artifact=graph_artifact,
        input_artifacts=artifact_rows,
        semantic_input_sha256=semantic_input_sha256,
    )
    return TagFeedbackReviewV1(
        feedback_id=deterministic_id(
            "tag-feedback",
            {
                "run_id": run_id,
                "graph_id": graph.graph_id,
                "graph_version": graph.graph_version,
                "input_fingerprint_sha256": fingerprint,
            },
        ),
        run_id=run_id,
        graph_id=graph.graph_id,
        graph_version=graph.graph_version,
        graph_artifact=graph_artifact,
        input_artifacts=artifact_rows,
        semantic_input_sha256=semantic_input_sha256,
        input_fingerprint_sha256=fingerprint,
        query_rows=query_rows,
        tag_rows=tag_rows,
        bridge_rows=tuple(bridge_rows),
    )


def tag_feedback_review_bytes(review: TagFeedbackReviewV1) -> bytes:
    """Return the canonical byte representation expected for persistence."""

    return canonical_json_bytes(review)


def _normalize_models(values, identifier_field: str, label: str):
    normalized = []
    by_identifier = {}
    for value in values:
        identifier = getattr(value, identifier_field)
        previous = by_identifier.get(identifier)
        if previous is not None:
            if previous != value:
                raise FeedbackCompileError(
                    "CONFLICTING_DUPLICATE_INPUT",
                    f"{label} {identifier!r} has conflicting definitions",
                )
            continue
        by_identifier[identifier] = value
        normalized.append(value)
    return tuple(sorted(normalized, key=lambda item: getattr(item, identifier_field)))


def _normalize_input_artifacts(
    values: tuple[FeedbackInputArtifactV1, ...],
) -> tuple[FeedbackInputArtifactV1, ...]:
    if not values:
        raise FeedbackCompileError(
            "EMPTY_INPUT_ARTIFACTS",
            "feedback requires at least one immutable input artifact",
        )
    by_role: dict[str, FeedbackInputArtifactV1] = {}
    for value in values:
        previous = by_role.get(value.role)
        if previous is not None:
            if previous != value:
                raise FeedbackCompileError(
                    "CONFLICTING_INPUT_ARTIFACT_ROLE",
                    f"input artifact role {value.role!r} has multiple pointers",
                )
            continue
        by_role[value.role] = value
    return tuple(
        sorted(
            by_role.values(),
            key=lambda item: (item.role, item.artifact.uri, item.artifact.sha256),
        )
    )


def _normalize_attempts(
    values: tuple[SearchAttemptRecord, ...],
) -> tuple[SearchAttemptRecord, ...]:
    by_key: dict[tuple[str, int], SearchAttemptRecord] = {}
    for value in values:
        key = (value.query_id, value.attempt_number)
        previous = by_key.get(key)
        if previous is not None:
            if previous != value:
                raise FeedbackCompileError(
                    "CONFLICTING_SEARCH_ATTEMPT",
                    f"search attempt {key!r} has conflicting records",
                )
            continue
        by_key[key] = value
    return tuple(
        sorted(
            by_key.values(),
            key=lambda item: (item.query_id, item.attempt_number),
        )
    )


def _normalize_fetch_allocations(
    values: tuple[FeedbackFetchAllocationV1, ...],
) -> tuple[FeedbackFetchAllocationV1, ...]:
    by_document: dict[str, FeedbackFetchAllocationV1] = {}
    for value in values:
        previous = by_document.get(value.document_id)
        if previous is not None:
            if previous != value:
                raise FeedbackCompileError(
                    "CONFLICTING_FETCH_ALLOCATION",
                    f"document {value.document_id!r} has multiple fetch allocations",
                )
            continue
        by_document[value.document_id] = value
    return tuple(sorted(by_document.values(), key=lambda item: item.document_id))


def _normalize_vector_allocations(
    values: tuple[FeedbackVectorAllocationV1, ...],
) -> tuple[FeedbackVectorAllocationV1, ...]:
    by_passage: dict[str, FeedbackVectorAllocationV1] = {}
    for value in values:
        previous = by_passage.get(value.passage_id)
        if previous is not None:
            if previous != value:
                raise FeedbackCompileError(
                    "CONFLICTING_VECTOR_ALLOCATION",
                    f"passage {value.passage_id!r} has multiple vector allocations",
                )
            continue
        by_passage[value.passage_id] = value
    return tuple(sorted(by_passage.values(), key=lambda item: item.passage_id))
