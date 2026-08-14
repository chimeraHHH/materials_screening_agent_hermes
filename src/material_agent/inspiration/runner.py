"""Bounded orchestration for the inspiration companion.

The runner is intentionally a thin composition layer.  Search, extraction,
passage selection, vectorization, evidence closure, bridge construction,
run-internal identity, and diverse selection remain in their pure modules.
Structure generation and optional bounded document retrieval are injected so
the same artifact contract can be exercised by byte-stable offline fixtures and
by explicitly enabled public metadata APIs. PDF full text remains disabled, and
the default public profile remains metadata-only.
"""

from __future__ import annotations

import hashlib
import json
import math
import time
from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass
from typing import Protocol

from material_agent.inspiration.bridge import build_search_supported_bridges
from material_agent.inspiration.component_identity import execution_identity_snapshots
from material_agent.inspiration.evidence import build_evidence_cards
from material_agent.inspiration.extractors import (
    ExtractionDecision,
    ExtractionLimits,
    ExtractionTier,
    extract_crossref_metadata,
    extract_document,
    extract_openalex_metadata,
    extract_search_hit_metadata,
    extract_semantic_scholar_metadata,
)
from material_agent.inspiration.feedback import (
    FEEDBACK_COMPILER_SNAPSHOT,
    FeedbackFetchAllocationV1,
    FeedbackInputArtifactV1,
    FeedbackVectorAllocationV1,
    compile_tag_feedback_review,
)
from material_agent.inspiration.fetch import (
    DisabledDocumentFetcher,
    DocumentFetcher,
    DocumentFetchError,
    DocumentFetchErrorCategory,
    DocumentFetchRequest,
    FetchAllowance,
    FetchAttemptRecord,
)
from material_agent.inspiration.identity import (
    CandidateProposalInput,
    StrictStructureGroupInput,
    merge_run_internal_candidates,
)
from material_agent.inspiration.models import (
    ArtifactPointerV1,
    BridgePacketV1,
    CandidateRouteRefV1,
    ComponentSnapshotV1,
    CostLedgerV1,
    EvidenceCardV1,
    InspirationBundleV1,
    InspirationInputV1,
    InspirationOutcome,
    InspirationStageResultV1,
    ParentCandidateRefV1,
    PassageV1,
    PassageVectorV1,
    SearchHitV1,
    SearchQueryKind,
    SearchQueryV1,
    TagGraphV1,
    TransformationPlanV1,
    TransformationStatus,
    canonical_json_bytes,
    deterministic_id,
)
from material_agent.inspiration.passages import (
    select_passage_drafts,
    selection_config_from_policy,
)
from material_agent.inspiration.policy import (
    InspirationPolicyV1,
    SearchExecutionMode,
)
from material_agent.inspiration.reporting import render_inspiration_report
from material_agent.inspiration.retrieval_quality import (
    MetadataQualityAuditV1,
    audit_metadata_hits,
)
from material_agent.inspiration.search import (
    BoundedHttpTransport,
    DocumentHitGroup,
    ParsedSearchPage,
    RawSearchPage,
    SearchAdapter,
    SearchAdapterError,
    SearchAttemptRecord,
    group_document_hits,
    parse_arxiv_page,
    parse_crossref_page,
    parse_multi_source_page,
    parse_openalex_page,
    public_search_adapter_from_environment,
)
from material_agent.inspiration.selection import (
    MechanismQuotaStatus,
    select_diverse_candidates_with_audit,
)
from material_agent.inspiration.tag_graph import QueryPlan, plan_tag_queries
from material_agent.inspiration.vectorizer import (
    SIGNED_HASHING_SNAPSHOT,
    VECTOR_ARTIFACT_MEDIA_TYPE,
    PassageVectorizationRequest,
    vectorize_selected_passages,
)
from material_agent.retrieval.storage import LocalArtifactStore

MAX_SEARCH_RESPONSE_BYTES = 1_000_000
STRUCTURE_MEDIA_TYPE = "chemical/x-cif"


class InspirationRunnerError(RuntimeError):
    """Fail-closed orchestration error with a stable machine-readable code."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(f"{code}: {message}")


@dataclass(slots=True)
class _RunDeadline:
    """Injectable monotonic deadline shared by every runner phase."""

    clock: Callable[[], float]
    started_seconds: float
    deadline_seconds: float
    last_observed_seconds: float

    @classmethod
    def start(
        cls,
        *,
        clock: Callable[[], float],
        max_walltime_seconds: int,
    ) -> _RunDeadline:
        started = _read_monotonic_clock(clock)
        return cls(
            clock=clock,
            started_seconds=started,
            deadline_seconds=started + max_walltime_seconds,
            last_observed_seconds=started,
        )

    def remaining(self, phase: str) -> float:
        now = _read_monotonic_clock(self.clock)
        if now < self.last_observed_seconds:
            raise InspirationRunnerError(
                "INVALID_MONOTONIC_CLOCK",
                f"monotonic clock moved backwards before {phase}",
            )
        self.last_observed_seconds = now
        remaining = self.deadline_seconds - now
        if remaining <= 0:
            raise InspirationRunnerError(
                "WALLTIME_BUDGET_EXCEEDED",
                f"inspiration run deadline expired before {phase}",
            )
        return remaining

    def elapsed_ms(self, phase: str) -> int:
        self.remaining(phase)
        elapsed_seconds = self.last_observed_seconds - self.started_seconds
        return math.ceil(elapsed_seconds * 1_000)


def _read_monotonic_clock(clock: Callable[[], float]) -> float:
    value = clock()
    if (
        not isinstance(value, (int, float))
        or isinstance(value, bool)
        or not math.isfinite(value)
    ):
        raise InspirationRunnerError(
            "INVALID_MONOTONIC_CLOCK",
            "monotonic clock must return finite seconds",
        )
    return float(value)


@dataclass(frozen=True, slots=True)
class ParentStructureInput:
    reference: ParentCandidateRefV1
    artifact_bytes: bytes


@dataclass(frozen=True, slots=True)
class _FixtureResponseBinding:
    query_id: str
    provider: str
    payload_artifact: ArtifactPointerV1


@dataclass(frozen=True, slots=True)
class _PassageExtractionResult:
    passages: tuple[PassageV1, ...]
    fetch_manifest: tuple[dict[str, object], ...]
    fetch_attempts: tuple[FetchAttemptRecord, ...]
    fetched_artifacts: tuple[ArtifactPointerV1, ...]
    fetch_allocations: tuple[FeedbackFetchAllocationV1, ...]
    warnings: tuple[str, ...]
    transient_failure_count: int = 0


@dataclass(frozen=True, slots=True)
class _VectorizationResult:
    vectors: tuple[PassageVectorV1, ...]
    artifacts: tuple[ArtifactPointerV1, ...]
    input_tokens: int
    feedback_allocations: tuple[FeedbackVectorAllocationV1, ...]


@dataclass(frozen=True, slots=True)
class TransformationContext:
    inspiration_input: InspirationInputV1
    policy: InspirationPolicyV1
    tag_graph: TagGraphV1
    bridge_packets: tuple[BridgePacketV1, ...]
    evidence_cards: tuple[EvidenceCardV1, ...]
    parents: tuple[ParentStructureInput, ...]
    registry_artifact: ArtifactPointerV1
    registry_bytes: bytes
    artifact_prefix: str


@dataclass(frozen=True, slots=True)
class TransformationDraft:
    """One injected transformation result and its selection-only metadata."""

    plan: TransformationPlanV1
    artifact_bytes: bytes | None = None
    structure_identity: StrictStructureGroupInput | None = None
    parent_family_id: str | None = None
    quality: float | None = None
    evidence_coverage: float | None = None
    next_falsification_step: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.plan, TransformationPlanV1):
            raise InspirationRunnerError(
                "INVALID_TRANSFORMATION_DRAFT",
                "plan must be a TransformationPlanV1",
            )
        has_output = self.plan.output_structure_artifact is not None
        if has_output != (self.artifact_bytes is not None):
            raise InspirationRunnerError(
                "INVALID_TRANSFORMATION_DRAFT",
                "structure bytes must accompany exactly one output artifact",
            )
        if self.artifact_bytes is not None:
            if not isinstance(self.artifact_bytes, bytes):
                raise InspirationRunnerError(
                    "INVALID_TRANSFORMATION_DRAFT",
                    "structure artifact payload must be bytes",
                )
            pointer = self.plan.output_structure_artifact
            if pointer is None:  # Explicit even when Python assertions are disabled.
                raise InspirationRunnerError(
                    "INVALID_TRANSFORMATION_DRAFT",
                    "structure bytes require an output artifact pointer",
                )
            digest = hashlib.sha256(self.artifact_bytes).hexdigest()
            if pointer.sha256 != digest:
                raise InspirationRunnerError(
                    "TRANSFORMATION_ARTIFACT_HASH_MISMATCH",
                    "output pointer does not match structure bytes",
                )
            if pointer.size_bytes != len(self.artifact_bytes):
                raise InspirationRunnerError(
                    "TRANSFORMATION_ARTIFACT_SIZE_MISMATCH",
                    "output pointer does not match structure byte length",
                )
        if self.plan.status is TransformationStatus.STRUCTURE_VALID:
            if (
                self.structure_identity is None
                or self.parent_family_id is None
                or self.quality is None
                or self.evidence_coverage is None
                or self.next_falsification_step is None
            ):
                raise InspirationRunnerError(
                    "INCOMPLETE_CANDIDATE_IDENTITY",
                    "structure-valid results require complete selection metadata",
                )
            if (
                self.structure_identity.canonical_structure_id
                != self.plan.output_structure_id
            ):
                raise InspirationRunnerError(
                    "STRUCTURE_IDENTITY_MISMATCH",
                    "canonical identity must equal the plan output structure ID",
                )


class TransformationEngine(Protocol):
    """Injected constrained structure engine; it receives only verified bytes."""

    component: ComponentSnapshotV1

    def generate(
        self,
        context: TransformationContext,
    ) -> Sequence[TransformationDraft]:
        """Return bounded transformation records for one frozen context."""


@dataclass(frozen=True, slots=True)
class InspirationRunResult:
    stage_result: InspirationStageResultV1
    stage_result_artifact: ArtifactPointerV1
    bundle: InspirationBundleV1
    report: str
    review_items: tuple[str, ...]


def verify_artifact_pointer(
    store: LocalArtifactStore,
    pointer: ArtifactPointerV1,
) -> None:
    """Re-read and verify URI, SHA-256, and byte size for one artifact."""

    if pointer.size_bytes is None:
        raise InspirationRunnerError(
            "ARTIFACT_SIZE_REQUIRED",
            f"artifact pointer has no byte size: {pointer.uri}",
        )
    try:
        inspected = store.inspect(
            pointer.uri,
            media_type=pointer.media_type or "application/octet-stream",
        )
    except (FileNotFoundError, OSError) as error:
        raise InspirationRunnerError(
            "ARTIFACT_MISSING",
            f"artifact cannot be read: {pointer.uri}",
        ) from error
    if inspected.uri != pointer.uri:
        raise InspirationRunnerError(
            "ARTIFACT_URI_MISMATCH",
            f"artifact resolved to a different URI: {pointer.uri}",
        )
    if inspected.sha256 != pointer.sha256:
        raise InspirationRunnerError(
            "ARTIFACT_HASH_MISMATCH",
            f"artifact SHA-256 mismatch: {pointer.uri}",
        )
    if inspected.size_bytes != pointer.size_bytes:
        raise InspirationRunnerError(
            "ARTIFACT_SIZE_MISMATCH",
            f"artifact byte-size mismatch: {pointer.uri}",
        )


class InspirationRunner:
    """Compose one bounded inspiration run and persist its complete lineage."""

    def __init__(
        self,
        *,
        store: LocalArtifactStore,
        search_adapter: SearchAdapter,
        transformation_engine: TransformationEngine,
        vectorizer: ComponentSnapshotV1 = SIGNED_HASHING_SNAPSHOT,
        document_fetcher: DocumentFetcher | None = None,
        monotonic_clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if not callable(monotonic_clock):
            raise ValueError("monotonic_clock must be callable")
        self.store = store
        self.search_adapter = search_adapter
        self.transformation_engine = transformation_engine
        self.vectorizer = vectorizer
        self.document_fetcher = document_fetcher or DisabledDocumentFetcher()
        self.monotonic_clock = monotonic_clock

    @property
    def execution_components(self) -> tuple[ComponentSnapshotV1, ...]:
        """Return the complete, freshly content-addressed approval identity."""

        components = (
            *execution_identity_snapshots(),
            self.search_adapter.component,
            self.vectorizer,
            self.transformation_engine.component,
            self.document_fetcher.component,
            FEEDBACK_COMPILER_SNAPSHOT,
        )
        component_ids = tuple(component.component_id for component in components)
        if len(component_ids) != len(set(component_ids)):
            raise InspirationRunnerError(
                "DUPLICATE_EXECUTION_COMPONENT",
                "approval execution component IDs must be unique",
            )
        return tuple(sorted(components, key=lambda component: component.component_id))

    def run(
        self,
        *,
        inspiration_input: InspirationInputV1,
        policy: InspirationPolicyV1,
        tag_graph: TagGraphV1,
        target_tag_ids: Sequence[str],
        query_plan_override: QueryPlan | None = None,
    ) -> InspirationRunResult:
        deadline = _RunDeadline.start(
            clock=self.monotonic_clock,
            max_walltime_seconds=policy.runtime.max_walltime_seconds,
        )
        self._validate_run_inputs(
            inspiration_input=inspiration_input,
            policy=policy,
            tag_graph=tag_graph,
        )
        deadline.remaining("input snapshot persistence")
        prefix = f"stages/inspiration/{inspiration_input.run_id}"

        input_pointer = self._write_json(
            f"{prefix}/input_snapshot.json",
            inspiration_input,
        )
        policy_pointer = self._write_json(f"{prefix}/policy.json", policy)
        tag_graph_pointer = self._write_json(
            f"{prefix}/tag_graph.json",
            tag_graph,
        )
        deadline.remaining("query planning")

        query_plan = query_plan_override or plan_tag_queries(
            tag_graph,
            target_tag_ids=tuple(target_tag_ids),
            budget=policy.search,
        )
        if query_plan_override is not None:
            if query_plan.candidate_pool is None:
                raise InspirationRunnerError(
                    "QUERY_CANDIDATE_POOL_MISSING",
                    "the contextual query plan has no candidate pool",
                )
            if (
                query_plan.candidate_pool.graph_id != tag_graph.graph_id
                or query_plan.candidate_pool.graph_version != tag_graph.graph_version
                or query_plan.candidate_pool.target_tag_ids
                != tuple(target_tag_ids)
            ):
                raise InspirationRunnerError(
                    "CONTEXTUAL_QUERY_GRAPH_MISMATCH",
                    "the contextual query plan is not bound to the frozen tag graph",
                )
            if len(query_plan.queries) > policy.search.max_queries:
                raise InspirationRunnerError(
                    "QUERY_BUDGET_EXCEEDED",
                    "the contextual query plan exceeds the frozen logical-query budget",
                )
            if (
                query_plan.allocation_audit is None
                or query_plan.allocation_audit.max_physical_requests
                > policy.search.max_physical_requests
            ):
                raise InspirationRunnerError(
                    "SEARCH_REQUEST_BUDGET_EXCEEDED",
                    "the contextual query plan exceeds the frozen physical-request budget",
                )
        if query_plan.allocation_audit is None:
            raise InspirationRunnerError(
                "QUERY_ALLOCATION_AUDIT_MISSING",
                "the frozen query plan has no physical-request allocation audit",
            )
        physical_allowance_by_query = {
            allowance.query_id: allowance.max_physical_requests
            for allowance in query_plan.allocation_audit.allowances
        }
        if set(physical_allowance_by_query) != {
            query.query_id for query in query_plan.queries
        }:
            raise InspirationRunnerError(
                "QUERY_ALLOCATION_MISMATCH",
                "physical-request allowances do not exactly cover the query plan",
            )
        if query_plan.candidate_pool is None:
            raise InspirationRunnerError(
                "QUERY_CANDIDATE_POOL_MISSING",
                "the frozen query plan has no curated candidate pool",
            )
        query_candidate_pool_pointer = self._write_json(
            f"{prefix}/query_candidate_pool.json",
            query_plan.candidate_pool,
        )
        query_allocation_pointer = self._write_json(
            f"{prefix}/query_allocation_audit.json",
            query_plan.allocation_audit,
        )
        query_plan_pointer = self._write_jsonl(
            f"{prefix}/query_plans.jsonl",
            query_plan.queries,
        )
        fixture_bindings = (
            self._load_search_fixture(
                inspiration_input.search_fixture_artifact,
                queries=query_plan.queries,
            )
            if policy.search_mode is SearchExecutionMode.OFFLINE_FIXTURE
            else None
        )

        executed_queries: list[SearchQueryV1] = []
        hits: list[SearchHitV1] = []
        raw_pages: dict[str, RawSearchPage] = {}
        raw_pointers: list[ArtifactPointerV1] = []
        search_attempts: list[SearchAttemptRecord] = []
        warnings: list[str] = [
            f"QUERY_RULE_SKIPPED:{rule_id}" for rule_id in query_plan.skipped_rule_ids
        ]
        search_response_bytes = 0
        for query in query_plan.queries:
            try:
                remaining_walltime = deadline.remaining(
                    f"search query {query.query_id}"
                )
            except InspirationRunnerError:
                self._write_jsonl(
                    f"{prefix}/search_attempts.jsonl",
                    tuple(attempt.to_dict() for attempt in search_attempts),
                )
                raise
            remaining_hits = policy.search.max_raw_hits - len(hits)
            if remaining_hits <= 0:
                warnings.append("RAW_HIT_BUDGET_EXHAUSTED")
                break
            try:
                page = self.search_adapter.search(
                    query,
                    max_response_bytes=MAX_SEARCH_RESPONSE_BYTES,
                    remaining_walltime_seconds=remaining_walltime,
                    max_physical_requests=min(
                        physical_allowance_by_query[query.query_id],
                        policy.search.max_physical_requests - len(search_attempts),
                    ),
                )
            except SearchAdapterError as error:
                search_attempts.extend(error.attempts)
                # Failure records remain discoverable at the deterministic run
                # path even though no scientific bundle can be produced.
                self._write_jsonl(
                    f"{prefix}/search_attempts.jsonl",
                    tuple(attempt.to_dict() for attempt in search_attempts),
                )
                raise
            page_attempts = page.attempts or (
                SearchAttemptRecord(
                    query_id=query.query_id,
                    attempt_number=1,
                    outcome="success",
                    error_code=None,
                    http_status=None,
                    retry_delay_seconds=0.0,
                    pacing_delay_seconds=0.0,
                    response_bytes=len(page.payload),
                ),
            )
            search_attempts.extend(page_attempts)
            if len(search_attempts) > policy.search.max_physical_requests:
                self._write_jsonl(
                    f"{prefix}/search_attempts.jsonl",
                    tuple(attempt.to_dict() for attempt in search_attempts),
                )
                raise InspirationRunnerError(
                    "SEARCH_REQUEST_BUDGET_EXCEEDED",
                    "search adapter exceeded the frozen physical request budget",
                )
            try:
                deadline.remaining(f"search query {query.query_id} response")
            except InspirationRunnerError:
                self._write_jsonl(
                    f"{prefix}/search_attempts.jsonl",
                    tuple(attempt.to_dict() for attempt in search_attempts),
                )
                raise
            try:
                self._validate_raw_page(
                    query,
                    page,
                    policy=policy,
                    binding=(
                        fixture_bindings[query.query_id]
                        if fixture_bindings is not None
                        else None
                    ),
                )
                raw_pointer = self._write_bytes(
                    f"{prefix}/raw_search/{query.query_id}.json",
                    page.payload,
                    media_type=page.media_type,
                )
                raw_pages[query.query_id] = page
                raw_pointers.append(raw_pointer)
                search_response_bytes += len(page.payload)
                parsed = self._parse_search_page(
                    query=query,
                    page=page,
                    raw_response_artifact=raw_pointer,
                    max_hits=remaining_hits,
                    publication_year_from=policy.search.publication_year_from,
                    publication_year_to=policy.search.publication_year_to,
                )
            except (InspirationRunnerError, SearchAdapterError):
                # A provider response that later fails contract/schema checks is
                # still a real, billable attempt and must remain auditable.
                self._write_jsonl(
                    f"{prefix}/search_attempts.jsonl",
                    tuple(attempt.to_dict() for attempt in search_attempts),
                )
                raise
            executed_queries.append(query)
            hits.extend(parsed.hits)
            warnings.extend(parsed.warnings)

        attempt_pointer = self._write_jsonl(
            f"{prefix}/search_attempts.jsonl",
            tuple(attempt.to_dict() for attempt in search_attempts),
        )
        hit_tuple = tuple(hits)
        metadata_quality = audit_metadata_hits(
            hit_tuple,
            require_abstract=policy.fetch.max_requests == 0,
        )
        metadata_quality_pointer = self._write_json(
            f"{prefix}/metadata_quality_audit.json",
            metadata_quality,
        )
        groups = group_document_hits(hit_tuple)
        if len(groups) > policy.search.max_unique_documents:
            raise InspirationRunnerError(
                "UNIQUE_DOCUMENT_BUDGET_EXCEEDED",
                f"parsed {len(groups)} unique documents; policy allows "
                f"{policy.search.max_unique_documents}",
            )
        hit_pointer = self._write_jsonl(
            f"{prefix}/search_hits.jsonl",
            hit_tuple,
        )

        extraction_result = self._extract_passages(
            prefix=prefix,
            policy=policy,
            graph=tag_graph,
            queries=tuple(executed_queries),
            hits=hit_tuple,
            groups=groups,
            raw_pages=raw_pages,
            deadline=deadline,
            metadata_quality=metadata_quality,
        )
        passages = extraction_result.passages
        warnings.extend(extraction_result.warnings)
        fetch_attempt_pointer = self._write_jsonl(
            f"{prefix}/fetch_attempts.jsonl",
            tuple(
                attempt.to_dict() for attempt in extraction_result.fetch_attempts
            ),
        )
        fetch_pointer = self._write_jsonl(
            f"{prefix}/fetch_manifest.jsonl",
            extraction_result.fetch_manifest,
        )
        passage_pointer = self._write_jsonl(
            f"{prefix}/passages.jsonl",
            passages,
        )
        if extraction_result.transient_failure_count and not passages:
            raise InspirationRunnerError(
                "EXTERNAL_FETCH_UNAVAILABLE",
                "all body-dependent evidence paths failed transiently",
            )

        vectorization = self._vectorize_passages(
            prefix=prefix,
            policy=policy,
            hits=hit_tuple,
            passages=passages,
            deadline=deadline,
        )
        vectors = vectorization.vectors
        vector_manifest_pointer = self._write_jsonl(
            f"{prefix}/passage_vectors.jsonl",
            vectors,
        )

        deadline.remaining("evidence classification")
        evidence_result = build_evidence_cards(
            graph=tag_graph,
            queries=tuple(executed_queries),
            hits=hit_tuple,
            passages=passages,
        )
        deadline.remaining("bridge construction")
        warnings.extend(evidence_result.warnings)
        evidence_pointer = self._write_jsonl(
            f"{prefix}/evidence_cards.jsonl",
            evidence_result.cards,
        )
        if (
            extraction_result.transient_failure_count
            and not evidence_result.cards
        ):
            raise InspirationRunnerError(
                "EXTERNAL_FETCH_UNAVAILABLE",
                "transient body-fetch failures prevented all mechanism evidence",
            )
        bridge_result = build_search_supported_bridges(
            graph=tag_graph,
            queries=tuple(executed_queries),
            hits=hit_tuple,
            passages=passages,
            evidence_cards=evidence_result.cards,
        )
        if len(bridge_result.packets) > policy.bridge.max_bridge_packets:
            raise InspirationRunnerError(
                "BRIDGE_PACKET_BUDGET_EXCEEDED",
                f"built {len(bridge_result.packets)} bridge packets; policy allows "
                f"{policy.bridge.max_bridge_packets}",
            )
        warnings.extend(
            f"BRIDGE_SKIPPED:{item.bridge_rule_id}:{item.reason}"
            for item in bridge_result.skipped
        )
        bridge_pointer = self._write_jsonl(
            f"{prefix}/bridge_packets.jsonl",
            bridge_result.packets,
        )

        drafts = self._generate_transformations(
            prefix=prefix,
            inspiration_input=inspiration_input,
            policy=policy,
            tag_graph=tag_graph,
            bridges=bridge_result.packets,
            evidence_cards=evidence_result.cards,
            deadline=deadline,
        )
        plans = tuple(draft.plan for draft in drafts)
        structure_artifacts = self._persist_transformation_structures(
            prefix=prefix,
            drafts=drafts,
            deadline=deadline,
        )
        transformation_pointer = self._write_jsonl(
            f"{prefix}/transformation_proposals.jsonl",
            plans,
        )

        deadline.remaining("candidate identity and selection")
        proposals = self._candidate_proposals(
            inspiration_input=inspiration_input,
            drafts=drafts,
            bridge_packets=bridge_result.packets,
            evidence_cards=evidence_result.cards,
        )
        identities = merge_run_internal_candidates(
            proposals,
            run_id=inspiration_input.run_id,
            policy_id=policy.policy_id,
            selection_policy=policy.selection,
        )
        selection_result = select_diverse_candidates_with_audit(
            identities,
            policy=policy.selection,
        )
        selected_candidates = selection_result.candidates
        selection_audit = selection_result.audit
        requested_route_count = (
            2 if policy.selection.min_mechanisms_when_available >= 2 else 1
        )
        if (
            selection_audit.selected_distinct_physical_route_count
            >= requested_route_count
        ):
            route_quota_status = "MET"
        elif (
            selection_audit.pool_distinct_physical_route_count
            < requested_route_count
        ):
            route_quota_status = "POOL_INSUFFICIENT"
        else:
            route_quota_status = "HARD_QUOTA_INFEASIBLE"
        selection_audit_record = {
            "schema_version": "inspiration-selection-audit-v1",
            "diversity_mode": (
                "MECHANISM_COVERAGE_WHEN_FEASIBLE"
                if policy.selection.min_mechanisms_when_available >= 2
                else "MMR_ONLY"
            ),
            "structure_valid_proposal_count": len(proposals),
            "post_exact_merge_candidate_count": len(identities),
            "exact_merge_reduction_count": len(proposals) - len(identities),
            "requested_distinct_physical_route_count": requested_route_count,
            "route_quota_status": route_quota_status,
            **asdict(selection_audit),
        }
        selection_audit_pointer = self._write_json(
            f"{prefix}/selection_audit.json",
            selection_audit_record,
        )
        duplicate_pointer = self._write_jsonl(
            f"{prefix}/internal_duplicate_groups.jsonl",
            self._duplicate_group_records(identities),
        )

        review_items = tuple(
            f"Review transformation `{plan.plan_id}` because its structural "
            "checks contain an unresolved status."
            for plan in plans
            if plan.status is TransformationStatus.REQUIRES_REVIEW
        )
        if not selected_candidates and not review_items:
            review_items = (
                "Review the rejected or absent transformation records; no "
                "structure-qualified candidate entered selection.",
            )
            warnings.append("REVIEW_REQUIRED:NO_ELIGIBLE_CANDIDATE")
        warnings.extend(
            f"SELECTION_UNDERFILL:{reason}"
            for reason in selection_audit.underfill_reasons
        )
        if selection_audit.quota_status is not MechanismQuotaStatus.MET:
            warnings.append(
                "SELECTION_MECHANISM_QUOTA:"
                f"{selection_audit.quota_status.value}"
            )
        if route_quota_status != "MET":
            warnings.append(f"SELECTION_ROUTE_QUOTA:{route_quota_status}")

        elapsed_ms = deadline.elapsed_ms("cost ledger snapshot")
        ledger = CostLedgerV1(
            search_requests=len(search_attempts),
            search_response_bytes=search_response_bytes,
            fetch_requests=len(extraction_result.fetch_attempts),
            fetch_response_bytes=sum(
                attempt.response_bytes
                for attempt in extraction_result.fetch_attempts
            ),
            raw_documents=len(hit_tuple),
            unique_documents=len(groups),
            extracted_passages=len(passages),
            vectorized_passages=len(vectors),
            embedding_input_tokens=vectorization.input_tokens,
            llm_calls=0,
            llm_input_tokens=0,
            llm_output_tokens=0,
            generated_plans=len(plans),
            rejected_plans=sum(
                plan.status is TransformationStatus.REJECTED for plan in plans
            ),
            candidates_after_internal_dedup=len(identities),
            walltime_ms=elapsed_ms,
        )
        cost_pointer = self._write_json(f"{prefix}/cost_ledger.json", ledger)
        deadline.remaining("tag feedback compilation")

        feedback_input_artifacts = (
            FeedbackInputArtifactV1(
                role="query-plan",
                artifact=query_plan_pointer,
            ),
            FeedbackInputArtifactV1(
                role="search-attempts",
                artifact=attempt_pointer,
            ),
            FeedbackInputArtifactV1(role="search-hits", artifact=hit_pointer),
            FeedbackInputArtifactV1(
                role="fetch-attempts",
                artifact=fetch_attempt_pointer,
            ),
            FeedbackInputArtifactV1(
                role="fetch-manifest",
                artifact=fetch_pointer,
            ),
            FeedbackInputArtifactV1(role="passages", artifact=passage_pointer),
            FeedbackInputArtifactV1(
                role="passage-vectors",
                artifact=vector_manifest_pointer,
            ),
            FeedbackInputArtifactV1(
                role="evidence-cards",
                artifact=evidence_pointer,
            ),
            FeedbackInputArtifactV1(
                role="bridge-packets",
                artifact=bridge_pointer,
            ),
            FeedbackInputArtifactV1(role="cost-ledger", artifact=cost_pointer),
            *tuple(
                FeedbackInputArtifactV1(
                    role=f"fetched-body-{index:03d}",
                    artifact=pointer,
                )
                for index, pointer in enumerate(
                    extraction_result.fetched_artifacts,
                    start=1,
                )
            ),
        )
        feedback = compile_tag_feedback_review(
            run_id=inspiration_input.run_id,
            graph=tag_graph,
            graph_artifact=tag_graph_pointer,
            input_artifacts=feedback_input_artifacts,
            planned_queries=query_plan.queries,
            executed_queries=tuple(executed_queries),
            search_attempts=tuple(search_attempts),
            hits=hit_tuple,
            passages=passages,
            evidence_cards=evidence_result.cards,
            bridge_packets=bridge_result.packets,
            fetch_allocations=extraction_result.fetch_allocations,
            vector_allocations=vectorization.feedback_allocations,
        )
        feedback_pointer = self._write_json(
            f"{prefix}/tag_feedback.json",
            feedback,
        )
        deadline.remaining("terminal bundle rendering")

        intermediate = _unique_pointers(
            (
                query_candidate_pool_pointer,
                query_allocation_pointer,
                query_plan_pointer,
                attempt_pointer,
                *raw_pointers,
                hit_pointer,
                metadata_quality_pointer,
                fetch_attempt_pointer,
                fetch_pointer,
                *extraction_result.fetched_artifacts,
                passage_pointer,
                *vectorization.artifacts,
                vector_manifest_pointer,
                evidence_pointer,
                tag_graph_pointer,
                bridge_pointer,
                *structure_artifacts,
                transformation_pointer,
                duplicate_pointer,
                selection_audit_pointer,
                feedback_pointer,
            )
        )
        outcome = (
            InspirationOutcome.SUCCEEDED
            if selected_candidates
            else InspirationOutcome.SCIENTIFIC_NO_MATCH
        )
        selection_limitations = (
            (
                "Selection returned "
                f"{len(selected_candidates)} of requested "
                f"{policy.selection.top_k} candidates; audit reasons: "
                + ", ".join(selection_audit.underfill_reasons)
                + ".",
            )
            if selection_audit.underfill_reasons
            else ()
        )
        evidence_scope_limitation = (
            "Search evidence is limited to bounded metadata and selected body "
            "passages; it may omit relevant context."
            if ledger.fetch_requests
            else "Search evidence is limited to bounded metadata passages and may "
            "omit relevant context."
        )
        limitations = (
            evidence_scope_limitation,
            "Generated structures have no downstream property validation; target property status is UNKNOWN.",
            "walltime_ms is a monotonic runtime snapshot; the runner also enforces the deadline through terminal Artifact verification.",
            *selection_limitations,
        )
        next_steps = tuple(
            dict.fromkeys(
                candidate.next_falsification_step for candidate in selected_candidates
            )
        ) or (
            "Resolve the listed review item before any downstream property calculation.",
        )
        bundle = InspirationBundleV1(
            bundle_id=deterministic_id(
                "bundle",
                {
                    "request_id": inspiration_input.request_id,
                    "run_id": inspiration_input.run_id,
                    "outcome": outcome.value,
                    "candidate_ids": tuple(
                        candidate.candidate_id for candidate in selected_candidates
                    ),
                    "lineage": tuple(
                        (pointer.uri, pointer.sha256, pointer.size_bytes)
                        for pointer in intermediate
                    ),
                },
            ),
            request_id=inspiration_input.request_id,
            run_id=inspiration_input.run_id,
            outcome=outcome,
            selected_candidates=selected_candidates,
            limitations=limitations,
            next_validation_steps=next_steps,
            cost_ledger=ledger,
            lineage_artifacts=intermediate,
        )
        bundle_pointer = self._write_json(
            f"{prefix}/inspiration_bundle.json",
            bundle,
        )

        bounded_warnings = _bounded_warnings(warnings)
        report = render_inspiration_report(
            inspiration_input=inspiration_input,
            bundle=bundle,
            queries=tuple(executed_queries),
            hits=hit_tuple,
            passages=passages,
            evidence_cards=evidence_result.cards,
            bridge_packets=bridge_result.packets,
            transformation_plans=plans,
            review_items=review_items,
            warnings=bounded_warnings,
            search_attempts=tuple(search_attempts),
            fetch_attempts=extraction_result.fetch_attempts,
            tag_feedback=feedback,
            selection_audit=selection_audit_record,
        )
        report_pointer = self._write_text(f"{prefix}/report.md", report)
        deadline.remaining("stage result verification")
        stage_result = InspirationStageResultV1(
            result_id=deterministic_id(
                "inspiration-result",
                {
                    "project_id": inspiration_input.project_id,
                    "request_id": inspiration_input.request_id,
                    "run_id": inspiration_input.run_id,
                    "bundle_sha256": bundle_pointer.sha256,
                },
            ),
            project_id=inspiration_input.project_id,
            request_id=inspiration_input.request_id,
            run_id=inspiration_input.run_id,
            outcome=outcome,
            input_snapshot_artifact=input_pointer,
            policy_artifact=policy_pointer,
            bundle_artifact=bundle_pointer,
            report_artifact=report_pointer,
            cost_ledger_artifact=cost_pointer,
            intermediate_artifacts=intermediate,
            warnings=bounded_warnings,
        )
        self.verify_stage_result(stage_result)
        result_pointer = self._write_json(
            f"{prefix}/stage_result.json",
            stage_result,
        )
        verify_artifact_pointer(self.store, result_pointer)
        deadline.remaining("terminal result return")
        return InspirationRunResult(
            stage_result=stage_result,
            stage_result_artifact=result_pointer,
            bundle=bundle,
            report=report,
            review_items=review_items,
        )

    def verify_stage_result(self, result: InspirationStageResultV1) -> None:
        """Verify every artifact named by a completed stage result."""

        for pointer in (
            result.input_snapshot_artifact,
            result.policy_artifact,
            result.bundle_artifact,
            result.report_artifact,
            result.cost_ledger_artifact,
            *result.intermediate_artifacts,
        ):
            verify_artifact_pointer(self.store, pointer)

    def _validate_run_inputs(
        self,
        *,
        inspiration_input: InspirationInputV1,
        policy: InspirationPolicyV1,
        tag_graph: TagGraphV1,
    ) -> None:
        if policy.search_mode is SearchExecutionMode.OFFLINE_FIXTURE:
            if policy.network_access or self.search_adapter.network_access:
                raise InspirationRunnerError(
                    "NETWORK_ACCESS_FORBIDDEN",
                    "offline inspiration runs cannot use a networked adapter",
                )
            if inspiration_input.search_fixture_artifact is None:
                raise InspirationRunnerError(
                    "SEARCH_FIXTURE_REQUIRED",
                    "offline input must reference a frozen search fixture",
                )
        elif policy.search_mode is SearchExecutionMode.PUBLIC_METADATA_API:
            if not policy.network_access or not self.search_adapter.network_access:
                raise InspirationRunnerError(
                    "NETWORK_ACCESS_REQUIRED",
                    "public metadata mode requires an explicitly networked adapter",
                )
            if inspiration_input.search_fixture_artifact is not None:
                raise InspirationRunnerError(
                    "SEARCH_FIXTURE_FORBIDDEN",
                    "public metadata mode cannot mix live search with a fixture manifest",
                )
        else:  # Defensive for future enum values.
            raise InspirationRunnerError(
                "UNSUPPORTED_SEARCH_MODE",
                f"unsupported search mode: {policy.search_mode}",
            )
        fetch_component = getattr(self.document_fetcher, "component", None)
        fetch_network_access = getattr(
            self.document_fetcher,
            "network_access",
            None,
        )
        if not isinstance(fetch_component, ComponentSnapshotV1) or type(
            fetch_network_access
        ) is not bool:
            raise InspirationRunnerError(
                "INVALID_DOCUMENT_FETCHER",
                "document fetcher must expose a frozen component and bool network flag",
            )
        if (
            policy.search_mode is SearchExecutionMode.OFFLINE_FIXTURE
            and fetch_network_access
        ):
            raise InspirationRunnerError(
                "FETCH_NETWORK_ACCESS_FORBIDDEN",
                "offline inspiration runs cannot use a networked document fetcher",
            )
        if (
            policy.search_mode is SearchExecutionMode.PUBLIC_METADATA_API
            and policy.fetch.max_requests > 0
            and not fetch_network_access
        ):
            raise InspirationRunnerError(
                "FETCH_NETWORK_ACCESS_REQUIRED",
                "public body fetching requires an explicitly networked fetcher",
            )
        if self.search_adapter.component != inspiration_input.search_adapter:
            raise InspirationRunnerError(
                "SEARCH_ADAPTER_SNAPSHOT_MISMATCH",
                "search adapter differs from the frozen input snapshot",
            )
        if self.vectorizer != inspiration_input.vectorizer:
            raise InspirationRunnerError(
                "VECTORIZER_SNAPSHOT_MISMATCH",
                "vectorizer differs from the frozen input snapshot",
            )
        if self.vectorizer != SIGNED_HASHING_SNAPSHOT:
            raise InspirationRunnerError(
                "UNSUPPORTED_VECTORIZER",
                "inspiration runner requires the signed-hashing-v1 snapshot",
            )
        required_pointers = (
            inspiration_input.requirement_artifact,
            *(parent.structure_artifact for parent in inspiration_input.parent_candidates),
            inspiration_input.policy_artifact,
            inspiration_input.tag_graph_artifact,
            inspiration_input.transformation_registry_artifact,
            *(
                (inspiration_input.search_fixture_artifact,)
                if inspiration_input.search_fixture_artifact is not None
                else ()
            ),
        )
        for pointer in required_pointers:
            verify_artifact_pointer(self.store, pointer)
        self._verify_canonical_value(inspiration_input.policy_artifact, policy)
        self._verify_canonical_value(inspiration_input.tag_graph_artifact, tag_graph)

    def _verify_canonical_value(self, pointer: ArtifactPointerV1, value: object) -> None:
        if self.store.read_bytes(pointer.uri) != canonical_json_bytes(value):
            raise InspirationRunnerError(
                "ARTIFACT_VALUE_MISMATCH",
                f"artifact bytes do not match the supplied frozen value: {pointer.uri}",
            )

    def _load_search_fixture(
        self,
        pointer: ArtifactPointerV1 | None,
        *,
        queries: tuple[SearchQueryV1, ...],
    ) -> dict[str, _FixtureResponseBinding]:
        if pointer is None:
            raise InspirationRunnerError(
                "SEARCH_FIXTURE_REQUIRED",
                "offline input must reference a frozen search fixture",
            )
        try:
            manifest = self.store.read_json(pointer.uri)
        except (UnicodeDecodeError, json.JSONDecodeError, OSError) as error:
            raise InspirationRunnerError(
                "INVALID_SEARCH_FIXTURE",
                "search fixture manifest is not readable JSON",
            ) from error
        if not isinstance(manifest, dict) or set(manifest) != {
            "responses",
            "schema_version",
        }:
            raise InspirationRunnerError(
                "INVALID_SEARCH_FIXTURE",
                "search fixture must contain only schema_version and responses",
            )
        if manifest["schema_version"] != "inspiration-search-fixture-v1":
            raise InspirationRunnerError(
                "INVALID_SEARCH_FIXTURE",
                "unsupported search fixture schema version",
            )
        raw_responses = manifest["responses"]
        if not isinstance(raw_responses, list) or not raw_responses:
            raise InspirationRunnerError(
                "INVALID_SEARCH_FIXTURE",
                "search fixture responses must be a non-empty array",
            )
        bindings: dict[str, _FixtureResponseBinding] = {}
        for raw in raw_responses:
            if not isinstance(raw, dict) or set(raw) != {
                "payload_artifact",
                "provider",
                "query_id",
            }:
                raise InspirationRunnerError(
                    "INVALID_SEARCH_FIXTURE",
                    "each fixture response has an invalid field set",
                )
            query_id = raw["query_id"]
            provider = raw["provider"]
            if not isinstance(query_id, str) or not isinstance(provider, str):
                raise InspirationRunnerError(
                    "INVALID_SEARCH_FIXTURE",
                    "fixture query ID and provider must be strings",
                )
            try:
                payload_pointer = ArtifactPointerV1.model_validate(
                    raw["payload_artifact"]
                )
            except (TypeError, ValueError) as error:
                raise InspirationRunnerError(
                    "INVALID_SEARCH_FIXTURE",
                    "fixture payload artifact pointer is invalid",
                ) from error
            if payload_pointer.media_type != "application/json":
                raise InspirationRunnerError(
                    "INVALID_SEARCH_FIXTURE",
                    "fixture payload artifacts must be application/json",
                )
            if query_id in bindings:
                raise InspirationRunnerError(
                    "INVALID_SEARCH_FIXTURE",
                    f"duplicate fixture response for query {query_id}",
                )
            verify_artifact_pointer(self.store, payload_pointer)
            bindings[query_id] = _FixtureResponseBinding(
                query_id=query_id,
                provider=provider,
                payload_artifact=payload_pointer,
            )
        expected_ids = {query.query_id for query in queries}
        if set(bindings) != expected_ids:
            raise InspirationRunnerError(
                "SEARCH_FIXTURE_QUERY_MISMATCH",
                "fixture response IDs do not exactly match the frozen query plan",
            )
        return bindings

    def _validate_raw_page(
        self,
        query: SearchQueryV1,
        page: RawSearchPage,
        *,
        policy: InspirationPolicyV1,
        binding: _FixtureResponseBinding | None,
    ) -> None:
        if not isinstance(page, RawSearchPage):
            raise InspirationRunnerError(
                "INVALID_SEARCH_RESPONSE",
                "search adapter must return a RawSearchPage",
            )
        if page.query_id != query.query_id:
            raise InspirationRunnerError(
                "SEARCH_QUERY_MISMATCH",
                "search adapter returned a response for a different query",
            )
        if page.media_type not in {"application/json", "application/atom+xml"}:
            raise InspirationRunnerError(
                "UNSUPPORTED_SEARCH_MEDIA_TYPE",
                "metadata search responses must be application/json or "
                "application/atom+xml",
            )
        if not isinstance(page.payload, bytes):
            raise InspirationRunnerError(
                "INVALID_SEARCH_PAYLOAD",
                "search adapter responses must contain bytes",
            )
        if len(page.payload) > MAX_SEARCH_RESPONSE_BYTES:
            raise InspirationRunnerError(
                "SEARCH_RESPONSE_BUDGET_EXCEEDED",
                "search adapter exceeded the response byte budget",
            )
        if policy.search_mode is SearchExecutionMode.PUBLIC_METADATA_API:
            if binding is not None:
                raise InspirationRunnerError(
                    "SEARCH_FIXTURE_FORBIDDEN",
                    "public metadata responses cannot use fixture bindings",
                )
            if page.provider not in {
                "arxiv",
                "crossref",
                "openalex",
                "multi-source-v1",
            } and not callable(getattr(self.search_adapter, "parse_page", None)):
                raise InspirationRunnerError(
                    "UNSUPPORTED_PUBLIC_METADATA_PROVIDER",
                    "public metadata mode received an unregistered provider",
                )
            return
        if binding is None:
            raise InspirationRunnerError(
                "SEARCH_FIXTURE_REQUIRED",
                "offline search responses require exact fixture bindings",
            )
        if page.provider != binding.provider:
            raise InspirationRunnerError(
                "SEARCH_FIXTURE_PROVIDER_MISMATCH",
                "search response provider differs from the frozen fixture",
            )
        digest = hashlib.sha256(page.payload).hexdigest()
        pointer = binding.payload_artifact
        if digest != pointer.sha256:
            raise InspirationRunnerError(
                "SEARCH_FIXTURE_HASH_MISMATCH",
                "search response bytes differ from the frozen fixture hash",
            )
        if pointer.size_bytes != len(page.payload):
            raise InspirationRunnerError(
                "SEARCH_FIXTURE_SIZE_MISMATCH",
                "search response bytes differ from the frozen fixture size",
            )
        if self.store.read_bytes(pointer.uri) != page.payload:
            raise InspirationRunnerError(
                "SEARCH_FIXTURE_CONTENT_MISMATCH",
                "search response bytes differ from the frozen fixture artifact",
            )

    def _parse_search_page(
        self,
        *,
        query: SearchQueryV1,
        page: RawSearchPage,
        raw_response_artifact: ArtifactPointerV1,
        max_hits: int,
        publication_year_from: int | None,
        publication_year_to: int | None,
    ) -> ParsedSearchPage:
        contextual_parser = getattr(self.search_adapter, "parse_page", None)
        if callable(contextual_parser):
            return contextual_parser(
                query=query,
                page=page,
                raw_response_artifact=raw_response_artifact,
                max_hits=max_hits,
            )
        if page.provider == "crossref":
            return parse_crossref_page(
                query=query,
                payload=page.payload,
                raw_response_artifact=raw_response_artifact,
                max_hits=max_hits,
                publication_year_from=publication_year_from,
                publication_year_to=publication_year_to,
            )
        if page.provider == "arxiv":
            return parse_arxiv_page(
                query=query,
                payload=page.payload,
                raw_response_artifact=raw_response_artifact,
                max_hits=max_hits,
                publication_year_from=publication_year_from,
                publication_year_to=publication_year_to,
            )
        if page.provider == "openalex":
            return parse_openalex_page(
                query=query,
                payload=page.payload,
                raw_response_artifact=raw_response_artifact,
                provider="openalex",
                max_hits=max_hits,
                publication_year_from=publication_year_from,
                publication_year_to=publication_year_to,
            )
        if page.provider == "multi-source-v1":
            return parse_multi_source_page(
                query=query,
                payload=page.payload,
                raw_response_artifact=raw_response_artifact,
                max_hits=max_hits,
                publication_year_from=publication_year_from,
                publication_year_to=publication_year_to,
            )
        if page.provider == "openalex-fixture":
            return parse_openalex_page(
                query=query,
                payload=page.payload,
                raw_response_artifact=raw_response_artifact,
                provider=page.provider,
                max_hits=max_hits,
                publication_year_from=publication_year_from,
                publication_year_to=publication_year_to,
            )
        raise InspirationRunnerError(
            "UNSUPPORTED_SEARCH_PROVIDER",
            f"no bounded parser is registered for provider {page.provider!r}",
        )

    def _extract_passages(
        self,
        *,
        prefix: str,
        policy: InspirationPolicyV1,
        graph: TagGraphV1,
        queries: tuple[SearchQueryV1, ...],
        hits: tuple[SearchHitV1, ...],
        groups: tuple[DocumentHitGroup, ...],
        raw_pages: dict[str, RawSearchPage],
        deadline: _RunDeadline,
        metadata_quality: MetadataQualityAuditV1,
    ) -> _PassageExtractionResult:
        query_index = {query.query_id: query for query in queries}
        hit_index = {hit.hit_id: hit for hit in hits}
        tag_index = {tag.tag_id: tag for tag in graph.tags}
        selected: list[PassageV1] = []
        selected_keys: set[tuple[str, str]] = set()
        manifest: list[dict[str, object]] = []
        warnings: list[str] = []
        fetch_attempts: list[FetchAttemptRecord] = []
        fetched_artifacts: list[ArtifactPointerV1] = []
        fetch_allocations: list[FeedbackFetchAllocationV1] = []
        transient_failure_count = 0
        config = selection_config_from_policy(policy.passages)
        metadata_limits = ExtractionLimits(
            max_input_bytes=MAX_SEARCH_RESPONSE_BYTES,
            max_drafts=64,
            min_abstract_tokens=policy.passages.min_tokens,
        )
        body_limits = ExtractionLimits(
            max_input_bytes=max(1, policy.fetch.max_bytes_per_response),
            max_drafts=64,
            min_abstract_tokens=policy.passages.min_tokens,
        )
        quality_rank_by_hit_id = {
            item.hit_id: (
                item.eligible_for_evidence_ranking,
                item.quality_rank,
            )
            for item in metadata_quality.entries
        }
        if set(quality_rank_by_hit_id) != set(hit_index):
            raise InspirationRunnerError(
                "METADATA_QUALITY_AUDIT_MISMATCH",
                "metadata quality audit does not exactly cover parsed hits",
            )
        for group in groups:
            deadline.remaining(f"passage extraction for {group.document_id}")
            member_hits = tuple(hit_index[hit_id] for hit_id in group.member_hit_ids)
            hit = _select_document_processing_hit(
                member_hits,
                query_index=query_index,
                quality_rank_by_hit_id=quality_rank_by_hit_id,
            )
            source_query_id = hit.query_ids[0]
            page = raw_pages[source_query_id]
            member_query_ids = tuple(
                sorted(
                    {
                        query_id
                        for member in member_hits
                        for query_id in member.query_ids
                    }
                )
            )
            member_queries = tuple(
                query_index[query_id] for query_id in member_query_ids
            )
            member_tag_ids = tuple(
                sorted(
                    {
                        tag_id
                        for query in member_queries
                        for tag_id in query.tag_ids
                    }
                )
            )
            tag_terms = {
                tag_id: (
                    *tag_index[tag_id].query_terms,
                    *tag_index[tag_id].synonyms,
                    tag_index[tag_id].label,
                )
                for tag_id in member_tag_ids
            }
            target_terms = tuple(
                dict.fromkeys(
                    tuple(query.text for query in member_queries)
                    + tuple(term for terms in tag_terms.values() for term in terms)
                )
            )
            if hit.provider == "crossref" and page.provider == "crossref":
                extraction = extract_crossref_metadata(
                    page.payload,
                    target_terms=target_terms,
                    limits=metadata_limits,
                    result_index=hit.provider_rank - 1,
                )
            elif (
                hit.provider == "openalex-fixture"
                and page.provider == "openalex-fixture"
            ):
                extraction = extract_openalex_metadata(
                    page.payload,
                    target_terms=target_terms,
                    limits=metadata_limits,
                    result_index=hit.provider_rank - 1,
                )
            elif (
                hit.provider == "semantic-scholar"
                and page.provider == "semantic-scholar-contextual-v3"
            ):
                extraction = extract_semantic_scholar_metadata(
                    page.payload,
                    target_terms=target_terms,
                    limits=metadata_limits,
                    result_index=hit.provider_rank - 1,
                )
            elif (
                (page.provider == hit.provider and hit.provider in {"arxiv", "openalex"})
                or (
                    page.provider == "multi-source-v1"
                    and hit.provider in {"arxiv", "crossref", "openalex"}
                )
            ):
                extraction = extract_search_hit_metadata(
                    hit,
                    target_terms=target_terms,
                    limits=metadata_limits,
                )
            else:
                raise InspirationRunnerError(
                    "SEARCH_EXTRACTION_PROVIDER_MISMATCH",
                    "search hit and raw page do not share a bounded extractor",
                )
            metadata_decision = extraction.decision
            metadata_warnings = extraction.warnings
            selected_extraction = extraction
            body_pointer: ArtifactPointerV1 | None = None
            fetch_request_id: str | None = None
            fetch_status = (
                "SKIPPED_METADATA_SUFFICIENT"
                if metadata_decision
                is ExtractionDecision.SKIP_BODY_ABSTRACT_SUFFICIENT
                else "METADATA_UNEXTRACTABLE"
            )
            fetch_error_code: str | None = None
            fetch_error_category: str | None = None
            fetched_document = None
            document_attempts: tuple[FetchAttemptRecord, ...] = ()

            if metadata_decision is ExtractionDecision.FETCH_BODY_METADATA_INSUFFICIENT:
                fetch_status = "NOT_FETCHED"
                remaining_requests = (
                    policy.fetch.max_requests - len(fetch_attempts)
                )
                consumed_bytes = sum(
                    attempt.response_bytes for attempt in fetch_attempts
                )
                remaining_bytes = policy.fetch.max_total_bytes - consumed_bytes
                if policy.fetch.max_requests == 0:
                    fetch_status = "DISABLED_BY_POLICY"
                    fetch_error_code = "DOCUMENT_FETCH_DISABLED_BY_POLICY"
                elif hit.canonical_url is None:
                    fetch_status = "NO_CANONICAL_URL"
                    fetch_error_code = "DOCUMENT_URL_UNAVAILABLE"
                elif remaining_requests <= 0:
                    fetch_status = "REQUEST_BUDGET_EXHAUSTED"
                    fetch_error_code = "FETCH_REQUEST_BUDGET_EXHAUSTED"
                elif remaining_bytes <= 0:
                    fetch_status = "BYTE_BUDGET_EXHAUSTED"
                    fetch_error_code = "FETCH_BYTE_BUDGET_EXHAUSTED"
                else:
                    remaining_fetch_walltime = deadline.remaining(
                        f"document fetch for {group.document_id}"
                    )
                    fetch_timeout_seconds = min(
                        policy.fetch.timeout_seconds,
                        int(
                            math.floor(
                                remaining_fetch_walltime / remaining_requests
                            )
                        ),
                    )
                    if fetch_timeout_seconds < 1:
                        raise InspirationRunnerError(
                            "WALLTIME_BUDGET_EXCEEDED",
                            "remaining walltime cannot cover every allowed fetch slot",
                        )
                    fetch_request_id = deterministic_id(
                        "fetch",
                        {
                            "artifact_prefix": prefix,
                            "document_id": group.document_id,
                            "url": hit.canonical_url,
                        },
                    )
                    try:
                        fetched_document = self.document_fetcher.fetch(
                            DocumentFetchRequest(
                                request_id=fetch_request_id,
                                document_id=group.document_id,
                                url=hit.canonical_url,
                            ),
                            allowance=FetchAllowance(
                                remaining_requests=remaining_requests,
                                remaining_total_bytes=remaining_bytes,
                                max_bytes_per_response=min(
                                    policy.fetch.max_bytes_per_response,
                                    remaining_bytes,
                                ),
                                timeout_seconds=fetch_timeout_seconds,
                                max_retries_per_request=(
                                    policy.fetch.max_retries_per_request
                                ),
                                allow_html=policy.fetch.allow_html,
                                allow_jats_xml=policy.fetch.allow_jats_xml,
                            ),
                        )
                    except DocumentFetchError as error:
                        document_attempts = error.attempts
                        fetch_attempts.extend(document_attempts)
                        fetch_error_code = error.code
                        fetch_error_category = error.category.value
                        fetch_status = f"FAILED_{error.category.value}"
                        if document_attempts:
                            fetch_allocations.append(
                                FeedbackFetchAllocationV1(
                                    document_id=group.document_id,
                                    query_ids=member_query_ids,
                                    request_count=len(document_attempts),
                                    response_bytes=sum(
                                        attempt.response_bytes
                                        for attempt in document_attempts
                                    ),
                                )
                            )
                        if error.category is DocumentFetchErrorCategory.CONTRACT:
                            raise InspirationRunnerError(
                                "DOCUMENT_FETCH_CONTRACT_VIOLATION",
                                f"fetcher contract failed for {group.document_id}: "
                                f"{error.code}",
                            ) from error
                        if error.category is DocumentFetchErrorCategory.TRANSIENT:
                            transient_failure_count += 1
                        warnings.append(
                            f"BODY_FETCH_FAILED:{group.document_id}:{error.code}"
                        )
                    else:
                        if (
                            fetched_document.request_id != fetch_request_id
                            or fetched_document.document_id != group.document_id
                        ):
                            raise InspirationRunnerError(
                                "DOCUMENT_FETCH_IDENTITY_MISMATCH",
                                "fetcher returned a document for a different request",
                            )
                        document_attempts = fetched_document.attempts
                        fetch_attempts.extend(document_attempts)
                        fetch_allocations.append(
                            FeedbackFetchAllocationV1(
                                document_id=group.document_id,
                                query_ids=member_query_ids,
                                request_count=len(document_attempts),
                                response_bytes=sum(
                                    attempt.response_bytes
                                    for attempt in document_attempts
                                ),
                            )
                        )
                        body_pointer = self._write_bytes(
                            f"{prefix}/fetched_documents/{group.document_id}"
                            f"{_fetched_document_extension(fetched_document.media_type)}",
                            fetched_document.payload,
                            media_type=fetched_document.media_type,
                        )
                        if body_pointer.sha256 != fetched_document.payload_sha256:
                            raise InspirationRunnerError(
                                "FETCHED_DOCUMENT_HASH_MISMATCH",
                                "persisted body differs from the fetcher payload",
                            )
                        fetched_artifacts.append(body_pointer)
                        body_extraction = extract_document(
                            fetched_document.payload,
                            media_type=fetched_document.media_type,
                            target_terms=target_terms,
                            limits=body_limits,
                        )
                        selected_extraction = body_extraction
                        fetch_status = "FETCHED"
                        if (
                            body_extraction.decision
                            is not ExtractionDecision.BODY_EXTRACTED
                        ):
                            fetch_status = "FETCHED_UNEXTRACTABLE"
                        warnings.extend(body_extraction.warnings)

                    deadline.remaining(
                        f"document fetch completion for {group.document_id}"
                    )

            body_drafts = (
                selected_extraction.drafts
                if selected_extraction is not extraction
                else ()
            )
            passage_drafts = select_passage_drafts(
                (*extraction.drafts, *body_drafts),
                hit_id=hit.hit_id,
                document_id=group.document_id,
                query_terms=target_terms,
                tag_terms=tag_terms,
                config=config,
                document_title=(
                    selected_extraction.title or extraction.title or hit.title
                ),
            )
            accepted_for_hit = 0
            deduplicated_for_hit = 0
            accepted_passage_ids: list[str] = []
            selected_locator_kinds: set[str] = set()
            for draft in passage_drafts:
                passage_key = (group.document_id, draft.normalized_text_sha256)
                if passage_key in selected_keys:
                    deduplicated_for_hit += 1
                    continue
                if len(selected) >= policy.passages.max_total:
                    warnings.append("PASSAGE_BUDGET_EXHAUSTED")
                    break
                selected_keys.add(passage_key)
                if (
                    draft.source_tier is not ExtractionTier.METADATA_API
                    and body_pointer is None
                ):
                    raise InspirationRunnerError(
                        "PASSAGE_SOURCE_ARTIFACT_MISSING",
                        "body-derived passage has no fetched source Artifact",
                    )
                source_artifact = (
                    hit.raw_response_artifact
                    if draft.source_tier is ExtractionTier.METADATA_API
                    else body_pointer
                )
                if source_artifact is None:
                    raise InspirationRunnerError(
                        "PASSAGE_SOURCE_ARTIFACT_MISSING",
                        "selected passage has no source Artifact",
                    )
                passage = PassageV1(
                    passage_id=deterministic_id(
                        "passage",
                        {
                            "document_id": group.document_id,
                            "locator": draft.locator,
                            "normalized_text_sha256": (
                                draft.normalized_text_sha256
                            ),
                        },
                    ),
                    hit_id=hit.hit_id,
                    document_id=group.document_id,
                    source_artifact=source_artifact,
                    normalizer=draft.normalizer,
                    locator=draft.locator,
                    text=draft.text,
                    char_count=len(draft.text),
                    estimated_token_count=draft.estimated_token_count,
                    normalized_text_sha256=draft.normalized_text_sha256,
                    matched_tag_ids=draft.matched_tag_ids,
                    lexical_score=draft.lexical_score,
                )
                selected.append(passage)
                accepted_passage_ids.append(passage.passage_id)
                selected_locator_kinds.add(passage.locator.kind.value)
                accepted_for_hit += 1
            manifest.append(
                {
                    "schema_version": "inspiration-fetch-manifest-v1",
                    "decision": selected_extraction.decision.value,
                    "metadata_decision": metadata_decision.value,
                    "deduplicated_passage_count": deduplicated_for_hit,
                    "document_id": group.document_id,
                    "fetch_error_category": fetch_error_category,
                    "fetch_error_code": fetch_error_code,
                    "fetch_request_id": fetch_request_id,
                    "fetch_response_bytes": sum(
                        attempt.response_bytes for attempt in document_attempts
                    ),
                    "fetch_status": fetch_status,
                    "fetched": body_pointer is not None,
                    "fetched_body_artifact": body_pointer,
                    "hit_id": hit.hit_id,
                    "member_hit_ids": group.member_hit_ids,
                    "media_type": selected_extraction.media_type,
                    "metadata_media_type": extraction.media_type,
                    "available_locator_kinds": tuple(
                        sorted(
                            {
                                draft.locator_kind.value
                                for draft in (
                                    *extraction.drafts,
                                    *(
                                        selected_extraction.drafts
                                        if selected_extraction is not extraction
                                        else ()
                                    ),
                                )
                            }
                        )
                    ),
                    "physical_request_count": len(document_attempts),
                    "query_ids": member_query_ids,
                    "raw_response_uri": hit.raw_response_artifact.uri,
                    "representative_hit_id": hit.hit_id,
                    "selected_locator_kinds": tuple(
                        sorted(selected_locator_kinds)
                    ),
                    "selected_passage_ids": tuple(accepted_passage_ids),
                    "selected_passage_count": accepted_for_hit,
                    "warnings": tuple(
                        sorted(
                            set(metadata_warnings)
                            | set(selected_extraction.warnings)
                        )
                    ),
                }
            )
            warnings.extend(metadata_warnings)
            if (
                metadata_decision
                is ExtractionDecision.FETCH_BODY_METADATA_INSUFFICIENT
                and body_pointer is None
            ):
                warnings.append(f"BODY_NOT_FETCHED:{hit.hit_id}")
        if len(fetch_attempts) > policy.fetch.max_requests:
            raise InspirationRunnerError(
                "FETCH_REQUEST_BUDGET_EXCEEDED",
                "fetcher exceeded the frozen physical request budget",
            )
        return _PassageExtractionResult(
            passages=tuple(selected),
            fetch_manifest=tuple(manifest),
            fetch_attempts=tuple(fetch_attempts),
            fetched_artifacts=tuple(fetched_artifacts),
            fetch_allocations=tuple(fetch_allocations),
            warnings=tuple(warnings),
            transient_failure_count=transient_failure_count,
        )

    def _vectorize_passages(
        self,
        *,
        prefix: str,
        policy: InspirationPolicyV1,
        hits: tuple[SearchHitV1, ...],
        passages: tuple[PassageV1, ...],
        deadline: _RunDeadline,
    ) -> _VectorizationResult:
        deadline.remaining("passage vectorization")
        hit_index = {hit.hit_id: hit for hit in hits}
        unique_passages: list[PassageV1] = []
        seen_passages: set[tuple[str, str]] = set()
        for passage in passages:
            passage_key = (passage.document_id, passage.normalized_text_sha256)
            if passage_key in seen_passages:
                continue
            seen_passages.add(passage_key)
            unique_passages.append(passage)
        vectorized_passages = tuple(unique_passages[: policy.embedding.max_passages])
        requests = tuple(
            PassageVectorizationRequest(
                passage=passage,
                title=hit_index[passage.hit_id].title,
                normalized_tags=passage.matched_tag_ids,
                vector_artifact=f"{passage.passage_id}.f32le",
            )
            for passage in vectorized_passages
        )
        generated = vectorize_selected_passages(
            requests,
            budget=policy.embedding,
            vectorizer=self.vectorizer,
        )
        deadline.remaining("passage vector persistence")
        records: list[PassageVectorV1] = []
        pointers: list[ArtifactPointerV1] = []
        for item in generated:
            deadline.remaining(
                f"vector Artifact {item.passage_vector.passage_id}"
            )
            path = (
                f"{prefix}/vectors/"
                f"{item.passage_vector.passage_id}.f32le"
            )
            pointer = self._write_bytes(
                path,
                item.artifact_bytes,
                media_type=VECTOR_ARTIFACT_MEDIA_TYPE,
            )
            if pointer.sha256 != item.passage_vector.vector_sha256:
                raise InspirationRunnerError(
                    "VECTOR_ARTIFACT_HASH_MISMATCH",
                    "persisted vector bytes differ from the vector record",
                )
            payload = item.passage_vector.model_dump(mode="python")
            payload["vector_artifact"] = pointer
            records.append(PassageVectorV1.model_validate(payload))
            pointers.append(pointer)
        return _VectorizationResult(
            vectors=tuple(records),
            artifacts=tuple(pointers),
            input_tokens=sum(item.input_token_count for item in generated),
            feedback_allocations=tuple(
                FeedbackVectorAllocationV1(
                    passage_id=item.passage_vector.passage_id,
                    input_token_count=item.input_token_count,
                )
                for item in generated
            ),
        )

    def _generate_transformations(
        self,
        *,
        prefix: str,
        inspiration_input: InspirationInputV1,
        policy: InspirationPolicyV1,
        tag_graph: TagGraphV1,
        bridges: tuple[BridgePacketV1, ...],
        evidence_cards: tuple[EvidenceCardV1, ...],
        deadline: _RunDeadline,
    ) -> tuple[TransformationDraft, ...]:
        deadline.remaining("transformation input loading")
        parents = tuple(
            ParentStructureInput(
                reference=parent,
                artifact_bytes=self.store.read_bytes(parent.structure_artifact.uri),
            )
            for parent in inspiration_input.parent_candidates
        )
        context = TransformationContext(
            inspiration_input=inspiration_input,
            policy=policy,
            tag_graph=tag_graph,
            bridge_packets=bridges,
            evidence_cards=evidence_cards,
            parents=parents,
            registry_artifact=inspiration_input.transformation_registry_artifact,
            registry_bytes=self.store.read_bytes(
                inspiration_input.transformation_registry_artifact.uri
            ),
            artifact_prefix=prefix,
        )
        raw_result = self.transformation_engine.generate(context)
        deadline.remaining("transformation result validation")
        if not isinstance(raw_result, Sequence):
            raise InspirationRunnerError(
                "INVALID_TRANSFORMATION_RESULT",
                "transformation engine must return a finite sequence",
            )
        if len(raw_result) > policy.transformation.max_plans:
            raise InspirationRunnerError(
                "TRANSFORMATION_BUDGET_EXCEEDED",
                "transformation engine exceeded the total plan budget",
            )
        raw_drafts = tuple(raw_result)
        if any(not isinstance(draft, TransformationDraft) for draft in raw_drafts):
            raise InspirationRunnerError(
                "INVALID_TRANSFORMATION_RESULT",
                "transformation engine returned an unsupported record",
            )
        drafts = tuple(sorted(raw_drafts, key=lambda draft: draft.plan.plan_id))
        plan_ids = tuple(draft.plan.plan_id for draft in drafts)
        route_hashes = tuple(draft.plan.route_sha256 for draft in drafts)
        if len(set(plan_ids)) != len(plan_ids) or len(set(route_hashes)) != len(
            route_hashes
        ):
            raise InspirationRunnerError(
                "DUPLICATE_TRANSFORMATION_IDENTITY",
                "plan IDs and route hashes must be unique before identity merge",
            )
        parent_index = {
            parent.candidate_id: parent for parent in inspiration_input.parent_candidates
        }
        bridge_ids = {bridge.bridge_packet_id for bridge in bridges}
        per_parent = Counter(draft.plan.parent_candidate_id for draft in drafts)
        if any(
            count > policy.transformation.max_plans_per_parent
            for count in per_parent.values()
        ):
            raise InspirationRunnerError(
                "TRANSFORMATION_PARENT_BUDGET_EXCEEDED",
                "transformation engine exceeded a per-parent plan budget",
            )
        for draft in drafts:
            deadline.remaining(f"transformation plan {draft.plan.plan_id}")
            plan = draft.plan
            if plan.status is TransformationStatus.PLANNED:
                raise InspirationRunnerError(
                    "UNEXECUTED_TRANSFORMATION_PLAN",
                    "runner accepts only executed transformation records",
                )
            parent = parent_index.get(plan.parent_candidate_id)
            if parent is None:
                raise InspirationRunnerError(
                    "UNKNOWN_TRANSFORMATION_PARENT",
                    "transformation references a parent outside the frozen input",
                )
            if (
                plan.parent_structure_id != parent.structure_id
                or plan.parent_structure_artifact != parent.structure_artifact
            ):
                raise InspirationRunnerError(
                    "TRANSFORMATION_PARENT_MISMATCH",
                    "transformation parent lineage differs from the frozen input",
                )
            if not set(plan.bridge_packet_ids).issubset(bridge_ids):
                raise InspirationRunnerError(
                    "TRANSFORMATION_BRIDGE_MISMATCH",
                    "transformation references a bridge outside this run",
                )
        return drafts

    def _persist_transformation_structures(
        self,
        *,
        prefix: str,
        drafts: tuple[TransformationDraft, ...],
        deadline: _RunDeadline,
    ) -> tuple[ArtifactPointerV1, ...]:
        pointers: list[ArtifactPointerV1] = []
        for draft in drafts:
            deadline.remaining(
                f"transformation structure {draft.plan.plan_id}"
            )
            if draft.artifact_bytes is None:
                continue
            output_id = draft.plan.output_structure_id
            expected_uri = f"artifact://{prefix}/structures/{output_id}.cif"
            pointer = draft.plan.output_structure_artifact
            if pointer is None:
                raise InspirationRunnerError(
                    "TRANSFORMATION_ARTIFACT_MISSING",
                    "structure bytes require a frozen output pointer",
                )
            if pointer.uri != expected_uri or pointer.media_type != STRUCTURE_MEDIA_TYPE:
                raise InspirationRunnerError(
                    "UNSAFE_TRANSFORMATION_ARTIFACT_PATH",
                    "transformation output must use the run-scoped CIF path",
                )
            persisted = self._write_bytes(
                expected_uri.removeprefix("artifact://"),
                draft.artifact_bytes,
                media_type=STRUCTURE_MEDIA_TYPE,
            )
            if persisted != pointer:
                raise InspirationRunnerError(
                    "TRANSFORMATION_ARTIFACT_MISMATCH",
                    "persisted structure pointer differs from the frozen plan",
                )
            pointers.append(persisted)
        return _unique_pointers(tuple(pointers))

    @staticmethod
    def _candidate_proposals(
        *,
        inspiration_input: InspirationInputV1,
        drafts: tuple[TransformationDraft, ...],
        bridge_packets: tuple[BridgePacketV1, ...],
        evidence_cards: tuple[EvidenceCardV1, ...],
    ) -> tuple[CandidateProposalInput, ...]:
        bridge_index = {
            packet.bridge_packet_id: packet for packet in bridge_packets
        }
        evidence_index = {
            card.evidence_card_id: card for card in evidence_cards
        }
        proposals: list[CandidateProposalInput] = []
        for draft in drafts:
            plan = draft.plan
            if plan.status is not TransformationStatus.STRUCTURE_VALID:
                continue
            referenced_packets = tuple(
                bridge_index[packet_id] for packet_id in plan.bridge_packet_ids
            )
            evidence_ids = tuple(
                sorted(
                    {
                        card_id
                        for packet in referenced_packets
                        for card_id in packet.evidence_card_ids
                    }
                )
            )
            mechanism_ids = tuple(
                sorted(
                    {
                        tag_id
                        for card_id in evidence_ids
                        for tag_id in evidence_index[card_id].mechanism_tag_ids
                    }
                )
            )
            if not mechanism_ids or not evidence_ids:
                raise InspirationRunnerError(
                    "CANDIDATE_EVIDENCE_MISSING",
                    "structure-valid transformation has no mechanism evidence",
                )
            route = CandidateRouteRefV1(
                plan_id=plan.plan_id,
                route_sha256=plan.route_sha256,
                parent_candidate_id=plan.parent_candidate_id,
                mechanism_tag_ids=mechanism_ids,
                bridge_packet_ids=tuple(sorted(plan.bridge_packet_ids)),
                evidence_card_ids=evidence_ids,
            )
            if (
                plan.output_structure_artifact is None
                or draft.structure_identity is None
                or draft.parent_family_id is None
                or draft.quality is None
                or draft.evidence_coverage is None
                or draft.next_falsification_step is None
            ):
                raise InspirationRunnerError(
                    "INCOMPLETE_CANDIDATE_IDENTITY",
                    "structure-valid result lost required selection metadata",
                )
            proposals.append(
                CandidateProposalInput(
                    run_id=inspiration_input.run_id,
                    structure_identity=draft.structure_identity,
                    structure_artifact=plan.output_structure_artifact,
                    route=route,
                    parent_family_id=draft.parent_family_id,
                    quality=draft.quality,
                    evidence_coverage=draft.evidence_coverage,
                    next_falsification_step=draft.next_falsification_step,
                )
            )
        return tuple(proposals)

    @staticmethod
    def _duplicate_group_records(identities) -> tuple[dict[str, object], ...]:
        return tuple(
            {
                "candidate_id": identity.candidate.candidate_id,
                "canonical_structure_id": identity.candidate.canonical_structure_id,
                "merged_plan_ids": tuple(
                    route.plan_id for route in identity.candidate.merged_routes
                ),
                "merged_route_sha256s": tuple(
                    route.route_sha256 for route in identity.candidate.merged_routes
                ),
                "parent_family_ids": identity.parent_family_ids,
                "strict_structure_group_id": (
                    identity.structure_identity.strict_structure_group_id
                ),
            }
            for identity in identities
        )

    def _write_json(self, path: str, value: object) -> ArtifactPointerV1:
        reference = self.store.write_json(
            path,
            _json_value(value),
            immutable=True,
        )
        pointer = _artifact_pointer(reference)
        verify_artifact_pointer(self.store, pointer)
        return pointer

    def _write_jsonl(
        self,
        path: str,
        values: Sequence[object],
    ) -> ArtifactPointerV1:
        reference = self.store.write_jsonl(
            path,
            (_json_value(value) for value in values),
            immutable=True,
        )
        pointer = _artifact_pointer(reference)
        verify_artifact_pointer(self.store, pointer)
        return pointer

    def _write_bytes(
        self,
        path: str,
        payload: bytes,
        *,
        media_type: str,
    ) -> ArtifactPointerV1:
        reference = self.store.write_bytes(
            path,
            payload,
            media_type=media_type,
            immutable=True,
        )
        pointer = _artifact_pointer(reference)
        verify_artifact_pointer(self.store, pointer)
        return pointer

    def _write_text(self, path: str, value: str) -> ArtifactPointerV1:
        reference = self.store.write_text(
            path,
            value,
            media_type="text/markdown",
            immutable=True,
        )
        pointer = _artifact_pointer(reference)
        verify_artifact_pointer(self.store, pointer)
        return pointer


def _select_document_processing_hit(
    hits: tuple[SearchHitV1, ...],
    *,
    query_index: dict[str, SearchQueryV1],
    quality_rank_by_hit_id: dict[str, tuple[bool, int]] | None = None,
) -> SearchHitV1:
    """Choose one real hit without losing the bridge needed for evidence closure.

    ``PassageV1`` can reference only one hit and ``EvidenceCardV1`` can express
    only one relation.  P3.1 therefore prefers a SUPPORT-capable BRIDGE hit over
    DIRECT and COUNTER duplicates.  All raw hits remain in ``search_hits.jsonl``
    and their document/query membership is repeated in ``fetch_manifest.jsonl``.
    Reusing one passage as evidence for multiple bridge rules would require an
    explicit lineage schema v2; silently merging those rule/relations here would
    make the existing closure validator reject the result.
    """

    if not hits:
        raise InspirationRunnerError(
            "EMPTY_DOCUMENT_GROUP",
            "document processing requires at least one search hit",
        )

    ranked: list[tuple[tuple[object, ...], SearchHitV1]] = []
    for hit in hits:
        if quality_rank_by_hit_id is None:
            quality_priority = (0, 0)
        else:
            try:
                eligible, quality_rank = quality_rank_by_hit_id[hit.hit_id]
            except KeyError as error:
                raise InspirationRunnerError(
                    "METADATA_QUALITY_AUDIT_MISMATCH",
                    f"search hit {hit.hit_id!r} has no metadata quality decision",
                ) from error
            quality_priority = (0 if eligible else 1, quality_rank)
        try:
            queries = tuple(query_index[query_id] for query_id in hit.query_ids)
        except KeyError as error:
            raise InspirationRunnerError(
                "SEARCH_HIT_QUERY_NOT_FOUND",
                f"search hit {hit.hit_id!r} references an unknown query",
            ) from error
        kinds = {query.kind for query in queries}
        bridge_rule_ids = {
            query.bridge_rule_id
            for query in queries
            if query.bridge_rule_id is not None
        }
        if (
            SearchQueryKind.BRIDGE in kinds
            and SearchQueryKind.COUNTER in kinds
        ) or len(bridge_rule_ids) > 1:
            raise InspirationRunnerError(
                "AMBIGUOUS_HIT_QUERY_LINEAGE",
                f"search hit {hit.hit_id!r} cannot close through one evidence relation",
            )
        if SearchQueryKind.BRIDGE in kinds:
            kind_priority = 0
        elif SearchQueryKind.DIRECT in kinds:
            kind_priority = 1
        else:
            kind_priority = 2
        lineage_key = tuple(
            sorted(
                (
                    query.bridge_rule_id or "",
                    query.kind.value,
                    query.query_id,
                )
                for query in queries
            )
        )
        ranked.append(
            (
                (
                    quality_priority[0],
                    kind_priority,
                    lineage_key,
                    quality_priority[1],
                    hit.provider_rank,
                    hit.provider,
                    hit.hit_id,
                ),
                hit,
            )
        )
    return min(ranked, key=lambda item: item[0])[1]


def _fetched_document_extension(media_type: str) -> str:
    normalized = media_type.partition(";")[0].strip().casefold()
    if normalized in {"text/html", "application/xhtml+xml"}:
        return ".html"
    if normalized in {
        "application/xml",
        "text/xml",
        "application/jats+xml",
        "application/vnd.jats+xml",
    }:
        return ".xml"
    if normalized == "application/ld+json":
        return ".jsonld"
    if normalized == "application/json" or normalized.endswith("+json"):
        return ".json"
    return ".body"


def _json_value(value: object) -> object:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    return value


def _artifact_pointer(reference) -> ArtifactPointerV1:
    return ArtifactPointerV1.model_validate(reference.model_dump(mode="python"))


def _unique_pointers(
    pointers: Sequence[ArtifactPointerV1],
) -> tuple[ArtifactPointerV1, ...]:
    by_uri: dict[str, ArtifactPointerV1] = {}
    for pointer in pointers:
        existing = by_uri.get(pointer.uri)
        if existing is not None and existing != pointer:
            raise InspirationRunnerError(
                "ARTIFACT_IDENTITY_CONFLICT",
                f"one artifact URI has conflicting metadata: {pointer.uri}",
            )
        by_uri[pointer.uri] = pointer
    return tuple(by_uri.values())


def _bounded_warnings(warnings: Sequence[str]) -> tuple[str, ...]:
    unique = tuple(sorted(set(warnings)))
    if len(unique) <= 64:
        return unique
    return (*unique[:63], "WARNINGS_TRUNCATED")


def public_inspiration_runner_from_environment(
    *,
    store: LocalArtifactStore,
    policy: InspirationPolicyV1,
    transformation_engine: TransformationEngine,
    environment: Mapping[str, str] | None = None,
    crossref_transport: BoundedHttpTransport | None = None,
    openalex_transport: BoundedHttpTransport | None = None,
    arxiv_transport: BoundedHttpTransport | None = None,
    document_fetcher: DocumentFetcher | None = None,
    monotonic_clock: Callable[[], float] = time.monotonic,
) -> InspirationRunner:
    """Explicit public-search runner factory; default search remains Crossref.

    Multi-source construction is possible only through the opt-in environment
    switch enforced by ``public_search_adapter_from_environment``.  The same
    frozen policy supplies both the query/request budget and publication-year
    range, preventing adapter configuration from silently diverging from the
    runner input.
    """

    if policy.search_mode is not SearchExecutionMode.PUBLIC_METADATA_API:
        raise ValueError("public runner factory requires PUBLIC_METADATA_API policy")
    adapter = public_search_adapter_from_environment(
        budget=policy.search,
        environment=environment,
        crossref_transport=crossref_transport,
        openalex_transport=openalex_transport,
        arxiv_transport=arxiv_transport,
    )
    return InspirationRunner(
        store=store,
        search_adapter=adapter,
        transformation_engine=transformation_engine,
        document_fetcher=document_fetcher,
        monotonic_clock=monotonic_clock,
    )
