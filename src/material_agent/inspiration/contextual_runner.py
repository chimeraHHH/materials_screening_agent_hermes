"""Production adapter from the V3 query handoff to ``InspirationRunner``.

The V3 LangGraph freezes material-aware Semantic Scholar requests and a
family/era budget before execution.  This module converts only the selected
requests into the existing search-query contract, preserves the provider's
exact raw bytes, and then reuses the established Inspiration evidence and
structure-generation pipeline.
"""

from __future__ import annotations

import hashlib
import time
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from material_agent.inspiration.fetch import DocumentFetcher
from material_agent.inspiration.literature_budget import (
    LiteratureBudgetPlanV2,
    LiteratureBudgetPolicyV2,
    LiteratureQueryCandidateV2,
    LiteratureQueryFamily,
)
from material_agent.inspiration.models import (
    ArtifactPointerV1,
    ComponentSnapshotV1,
    InspirationInputV1,
    SearchHitV1,
    SearchQueryKind,
    SearchQueryV1,
    TagGraphV1,
    canonical_sha256,
    deterministic_id,
)
from material_agent.inspiration.policy import (
    InspirationPolicyV1,
    SearchBudgetV1,
    SearchExecutionMode,
)
from material_agent.inspiration.query_context import QueryContextV2
from material_agent.inspiration.research_memory import InspirationMemorySnapshotV1
from material_agent.inspiration.retrieval_quality import (
    CuratedQueryCandidatePoolV1,
    PhysicalQueryAllowanceV1,
    QueryAllocationAuditV1,
    QueryCandidateOrigin,
    QueryCandidateV1,
    make_query_candidate,
)
from material_agent.inspiration.runner import (
    InspirationRunner,
    InspirationRunnerError,
    InspirationRunResult,
    TransformationEngine,
)
from material_agent.inspiration.search import (
    ParsedSearchPage,
    RawSearchPage,
    SearchAdapterError,
    SearchAttemptRecord,
)
from material_agent.inspiration.semantic_scholar import (
    SemanticScholarPublicAdapter,
    SemanticScholarRequestV1,
    parse_semantic_scholar_page,
)
from material_agent.inspiration.tag_graph import QueryPlan, plan_tag_queries
from material_agent.inspiration.vectorizer import SIGNED_HASHING_SNAPSHOT
from material_agent.retrieval.storage import LocalArtifactStore

CONTEXTUAL_SEARCH_ADAPTER_VERSION = "semantic-scholar-contextual-v3"


class SemanticScholarContextualSearchAdapter:
    """Bind V1 search queries to frozen V3 Semantic Scholar requests."""

    network_access = True

    def __init__(
        self,
        provider: SemanticScholarPublicAdapter,
        *,
        request_by_query_id: Mapping[str, SemanticScholarRequestV1] | None = None,
    ) -> None:
        self.provider = provider
        self.request_by_query_id = dict(request_by_query_id or {})
        self.component = ComponentSnapshotV1(
            component_id="semantic-scholar-contextual-v3-adapter",
            version=CONTEXTUAL_SEARCH_ADAPTER_VERSION,
            implementation_sha256=canonical_sha256(
                {
                    "provider": provider.component.model_dump(mode="json"),
                    "source_sha256": hashlib.sha256(
                        Path(__file__).read_bytes()
                    ).hexdigest(),
                }
            ),
        )

    def search(
        self,
        query: SearchQueryV1,
        *,
        max_response_bytes: int,
        remaining_walltime_seconds: float | None = None,
        max_physical_requests: int | None = None,
    ) -> RawSearchPage:
        request = self.request_by_query_id.get(query.query_id)
        if request is None:
            raise SearchAdapterError(
                "CONTEXTUAL_QUERY_NOT_BOUND",
                "search query has no frozen Semantic Scholar request",
            )
        try:
            page = self.provider.search(
                request,
                max_response_bytes=max_response_bytes,
                remaining_walltime_seconds=remaining_walltime_seconds,
                max_physical_requests=max_physical_requests,
            )
        except SearchAdapterError as error:
            raise error.with_attempts(
                tuple(_remap_attempt(item, query.query_id) for item in error.attempts)
            ) from error
        return RawSearchPage(
            provider=CONTEXTUAL_SEARCH_ADAPTER_VERSION,
            query_id=query.query_id,
            payload=page.payload,
            media_type=page.media_type,
            attempts=tuple(
                _remap_attempt(item, query.query_id) for item in page.attempts
            ),
        )

    def parse_page(
        self,
        *,
        query: SearchQueryV1,
        page: RawSearchPage,
        raw_response_artifact: ArtifactPointerV1,
        max_hits: int,
    ) -> ParsedSearchPage:
        if page.provider != CONTEXTUAL_SEARCH_ADAPTER_VERSION:
            raise SearchAdapterError(
                "CONTEXTUAL_PROVIDER_MISMATCH",
                "contextual parser received a response from another provider",
            )
        request = self.request_by_query_id.get(query.query_id)
        if request is None:
            raise SearchAdapterError(
                "CONTEXTUAL_QUERY_NOT_BOUND",
                "search query has no frozen Semantic Scholar request",
            )
        parsed = parse_semantic_scholar_page(
            request=request,
            payload=page.payload,
            raw_response_artifact=raw_response_artifact,
        )
        hits = tuple(
            _remap_hit(item, query.query_id) for item in parsed.hits[:max_hits]
        )
        return ParsedSearchPage(hits=hits, warnings=parsed.warnings)


class SemanticScholarContextualInspirationRunnerV3:
    """Execute a V3 handoff using the real bounded Semantic Scholar adapter."""

    def __init__(
        self,
        *,
        store: LocalArtifactStore,
        semantic_scholar_adapter: SemanticScholarPublicAdapter,
        transformation_engine: TransformationEngine,
        vectorizer: ComponentSnapshotV1 = SIGNED_HASHING_SNAPSHOT,
        document_fetcher: DocumentFetcher | None = None,
        monotonic_clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.store = store
        self.semantic_scholar_adapter = semantic_scholar_adapter
        self.search_adapter = SemanticScholarContextualSearchAdapter(
            semantic_scholar_adapter
        )
        self.transformation_engine = transformation_engine
        self.vectorizer = vectorizer
        self.document_fetcher = document_fetcher
        self.monotonic_clock = monotonic_clock

    def run(self, **_kwargs: Any) -> InspirationRunResult:
        raise InspirationRunnerError(
            "CONTEXTUAL_RUNNER_REQUIRES_V3",
            "this runner must be invoked through run_contextual",
        )

    def run_contextual(
        self,
        *,
        inspiration_input: InspirationInputV1,
        policy: InspirationPolicyV1,
        tag_graph: TagGraphV1,
        target_tag_ids: tuple[str, ...],
        query_context: QueryContextV2,
        memory_snapshot: InspirationMemorySnapshotV1,
        literature_requests: tuple[SemanticScholarRequestV1, ...],
        literature_candidates: tuple[LiteratureQueryCandidateV2, ...],
        literature_budget_policy: LiteratureBudgetPolicyV2,
        literature_budget_plan: LiteratureBudgetPlanV2,
        handoff: Any,
    ) -> InspirationRunResult:
        self._validate_handoff(
            inspiration_input=inspiration_input,
            policy=policy,
            query_context=query_context,
            memory_snapshot=memory_snapshot,
            literature_requests=literature_requests,
            literature_candidates=literature_candidates,
            literature_budget_policy=literature_budget_policy,
            literature_budget_plan=literature_budget_plan,
            handoff=handoff,
        )
        query_plan, request_by_query_id = compile_contextual_query_plan_v3(
            tag_graph=tag_graph,
            target_tag_ids=target_tag_ids,
            query_context=query_context,
            literature_requests=literature_requests,
            literature_candidates=literature_candidates,
            literature_budget_plan=literature_budget_plan,
        )
        adapter = SemanticScholarContextualSearchAdapter(
            self.semantic_scholar_adapter,
            request_by_query_id=request_by_query_id,
        )
        if adapter.component != self.search_adapter.component:
            raise InspirationRunnerError(
                "CONTEXTUAL_ADAPTER_IDENTITY_DRIFT",
                "bound adapter differs from the input compiler snapshot",
            )
        runner = InspirationRunner(
            store=self.store,
            search_adapter=adapter,
            transformation_engine=self.transformation_engine,
            vectorizer=self.vectorizer,
            document_fetcher=self.document_fetcher,
            monotonic_clock=self.monotonic_clock,
        )
        return runner.run(
            inspiration_input=inspiration_input,
            policy=policy,
            tag_graph=tag_graph,
            target_tag_ids=target_tag_ids,
            query_plan_override=query_plan,
        )

    def _validate_handoff(
        self,
        *,
        inspiration_input: InspirationInputV1,
        policy: InspirationPolicyV1,
        query_context: QueryContextV2,
        memory_snapshot: InspirationMemorySnapshotV1,
        literature_requests: tuple[SemanticScholarRequestV1, ...],
        literature_candidates: tuple[LiteratureQueryCandidateV2, ...],
        literature_budget_policy: LiteratureBudgetPolicyV2,
        literature_budget_plan: LiteratureBudgetPlanV2,
        handoff: Any,
    ) -> None:
        if policy.search_mode is not SearchExecutionMode.PUBLIC_METADATA_API:
            raise InspirationRunnerError(
                "CONTEXTUAL_PUBLIC_SEARCH_REQUIRED",
                "V3 contextual execution requires PUBLIC_METADATA_API policy",
            )
        if (
            query_context.requirement_revision != inspiration_input.requirement_revision
            or set(query_context.source_candidate_ids)
            != {item.candidate_id for item in inspiration_input.parent_candidates}
        ):
            raise InspirationRunnerError(
                "QUERY_CONTEXT_INPUT_MISMATCH",
                "query context does not close to the Inspiration input",
            )
        if memory_snapshot.project_id != inspiration_input.project_id:
            raise InspirationRunnerError(
                "MEMORY_PROJECT_MISMATCH",
                "memory snapshot belongs to another project",
            )
        if (
            literature_budget_plan.policy_sha256
            != literature_budget_policy.policy_sha256
        ):
            raise InspirationRunnerError(
                "LITERATURE_POLICY_MISMATCH",
                "literature budget plan is not bound to the supplied policy",
            )
        if (
            literature_budget_plan.max_queries > policy.search.max_queries
            or literature_budget_plan.max_raw_hits > policy.search.max_raw_hits
            or literature_budget_plan.max_physical_requests
            > policy.search.max_physical_requests
        ):
            raise InspirationRunnerError(
                "LITERATURE_BUDGET_EXCEEDS_RUN_POLICY",
                "V3 literature allowances exceed the frozen Inspiration policy",
            )
        request_ids = tuple(item.query_id for item in literature_requests)
        if len(set(request_ids)) != len(request_ids) or any(
            item.context_id != query_context.context_id for item in literature_requests
        ):
            raise InspirationRunnerError(
                "LITERATURE_REQUEST_CONTEXT_MISMATCH",
                "literature requests do not close to the frozen query context",
            )
        request_by_id = {item.query_id: item for item in literature_requests}
        candidate_by_id = {item.candidate_id: item for item in literature_candidates}
        if len(candidate_by_id) != len(literature_candidates):
            raise InspirationRunnerError(
                "DUPLICATE_LITERATURE_CANDIDATE",
                "literature candidates must be unique",
            )
        for candidate in literature_candidates:
            request = request_by_id.get(candidate.provider_query_id)
            if candidate.provider_id != "semantic-scholar" or request is None:
                raise InspirationRunnerError(
                    "LITERATURE_PROVIDER_CLOSURE_FAILED",
                    "literature candidate has no Semantic Scholar request",
                )
            if candidate.family is LiteratureQueryFamily.HISTORICAL and (
                candidate.publication_year_from != request.publication_year_from
                or candidate.publication_year_to != request.publication_year_to
            ):
                raise InspirationRunnerError(
                    "HISTORICAL_YEAR_BINDING_MISMATCH",
                    "historical candidate and provider request use different eras",
                )
        if any(
            item.candidate_id not in candidate_by_id
            or item.provider_query_id
            != candidate_by_id[item.candidate_id].provider_query_id
            for item in literature_budget_plan.allowances
        ):
            raise InspirationRunnerError(
                "LITERATURE_ALLOWANCE_CLOSURE_FAILED",
                "literature allowances do not close to the candidate pool",
            )
        for field in (
            "inspiration_input_artifact",
            "query_context_artifact",
            "memory_snapshot_artifact",
            "literature_budget_policy_artifact",
            "literature_request_artifact",
            "literature_candidate_artifact",
            "literature_budget_plan_artifact",
        ):
            pointer = getattr(handoff, field, None)
            if pointer is None or not self.store.exists_with_hash(
                pointer.uri, pointer.sha256
            ):
                raise InspirationRunnerError(
                    "CONTEXTUAL_HANDOFF_ARTIFACT_MISMATCH",
                    f"V3 handoff artifact failed integrity verification: {field}",
                )


def compile_contextual_query_plan_v3(
    *,
    tag_graph: TagGraphV1,
    target_tag_ids: tuple[str, ...],
    query_context: QueryContextV2,
    literature_requests: tuple[SemanticScholarRequestV1, ...],
    literature_candidates: tuple[LiteratureQueryCandidateV2, ...],
    literature_budget_plan: LiteratureBudgetPlanV2,
) -> tuple[QueryPlan, dict[str, SemanticScholarRequestV1]]:
    """Project selected V3 provider requests into the existing query ledger."""

    del query_context  # identity is already closed by every provider request
    request_by_id = {item.query_id: item for item in literature_requests}
    candidate_by_id = {item.candidate_id: item for item in literature_candidates}
    tag_template_plan = plan_tag_queries(
        tag_graph,
        target_tag_ids=target_tag_ids,
        budget=SearchBudgetV1(
            max_queries=6,
            max_physical_requests=6,
            max_direct_queries=0,
            max_bridge_queries=4,
            max_counter_queries=2,
            max_raw_hits=60,
            max_unique_documents=30,
        ),
    )
    assert tag_template_plan.candidate_pool is not None
    reviewed_by_kind_and_text = {
        (item.kind, " ".join(item.text.split()).casefold()): item
        for item in tag_template_plan.candidate_pool.candidates
    }
    pool_version = (
        f"{tag_graph.graph_id}@{tag_graph.graph_version}:"
        f"contextual-v3:{literature_budget_plan.plan_id}"
    )
    query_candidates: list[QueryCandidateV1] = []
    queries: list[SearchQueryV1] = []
    allowances: list[PhysicalQueryAllowanceV1] = []
    request_by_query_id: dict[str, SemanticScholarRequestV1] = {}
    for allowance in literature_budget_plan.allowances:
        candidate = candidate_by_id[allowance.candidate_id]
        request = request_by_id[candidate.provider_query_id]
        text = request.query_text or f"citation route for {request.anchor_paper_id}"
        if candidate.family in {
            LiteratureQueryFamily.BRIDGE,
            LiteratureQueryFamily.COUNTER,
        }:
            kind = (
                SearchQueryKind.BRIDGE
                if candidate.family is LiteratureQueryFamily.BRIDGE
                else SearchQueryKind.COUNTER
            )
            reviewed = reviewed_by_kind_and_text.get(
                (kind, " ".join(text.split()).casefold())
            )
            if reviewed is None:
                raise InspirationRunnerError(
                    "UNREVIEWED_CONTEXTUAL_BRIDGE",
                    "bridge/counter request does not match the frozen tag registry",
                )
            query_candidate = make_query_candidate(
                pool_version=pool_version,
                kind=kind,
                text=text,
                tag_ids=reviewed.tag_ids,
                bridge_rule_id=reviewed.bridge_rule_id,
                family_id=reviewed.family_id,
                origin=reviewed.origin,
                review_basis=reviewed.review_basis,
            )
        else:
            matched_tags = _matched_contextual_tags(
                text,
                tag_graph=tag_graph,
                target_tag_ids=target_tag_ids,
            )
            query_candidate = make_query_candidate(
                pool_version=pool_version,
                kind=SearchQueryKind.DIRECT,
                text=text,
                tag_ids=matched_tags,
                bridge_rule_id=None,
                family_id=f"contextual:{candidate.family.value.casefold()}",
                origin=QueryCandidateOrigin.FROZEN_CONTEXT_COMPILER,
                review_basis=(
                    f"literature-budget-v2:{candidate.family.value}:"
                    f"{candidate.year_bucket_id or 'all-years'}"
                ),
            )
        query_id = deterministic_id(
            "query",
            {
                "candidate_id": query_candidate.candidate_id,
                "provider_query_id": request.query_id,
            },
        )
        query = SearchQueryV1(
            query_id=query_id,
            kind=query_candidate.kind,
            text=query_candidate.text,
            tag_ids=query_candidate.tag_ids,
            bridge_rule_id=query_candidate.bridge_rule_id,
        )
        query_candidates.append(query_candidate)
        queries.append(query)
        request_by_query_id[query_id] = request
        allowances.append(
            PhysicalQueryAllowanceV1(
                query_id=query_id,
                candidate_id=query_candidate.candidate_id,
                kind=query.kind,
                execution_ordinal=allowance.execution_ordinal,
                max_physical_requests=allowance.max_physical_requests,
            )
        )
    pool = CuratedQueryCandidatePoolV1(
        pool_version=pool_version,
        graph_id=tag_graph.graph_id,
        graph_version=tag_graph.graph_version,
        target_tag_ids=target_tag_ids,
        candidates=tuple(query_candidates),
    )
    audit = QueryAllocationAuditV1(
        pool_version=pool.pool_version,
        pool_sha256=canonical_sha256(pool.model_dump(mode="json")),
        max_queries=literature_budget_plan.max_queries,
        max_physical_requests=literature_budget_plan.max_physical_requests,
        selected_candidate_ids=tuple(item.candidate_id for item in query_candidates),
        allowances=tuple(allowances),
    )
    return (
        QueryPlan(
            queries=tuple(queries),
            candidate_pool=pool,
            allocation_audit=audit,
        ),
        request_by_query_id,
    )


def _matched_contextual_tags(
    text: str,
    *,
    tag_graph: TagGraphV1,
    target_tag_ids: tuple[str, ...],
) -> tuple[str, ...]:
    normalized = " ".join(text.split()).casefold()
    matched = set(target_tag_ids)
    for tag in tag_graph.tags:
        if any(term.casefold() in normalized for term in tag.query_terms):
            matched.add(tag.tag_id)
    return tuple(sorted(matched))[:16]


def _remap_attempt(
    attempt: SearchAttemptRecord,
    query_id: str,
) -> SearchAttemptRecord:
    return SearchAttemptRecord(
        query_id=query_id,
        attempt_number=attempt.attempt_number,
        outcome=attempt.outcome,
        error_code=attempt.error_code,
        http_status=attempt.http_status,
        retry_delay_seconds=attempt.retry_delay_seconds,
        pacing_delay_seconds=attempt.pacing_delay_seconds,
        response_bytes=attempt.response_bytes,
    )


def _remap_hit(hit: SearchHitV1, query_id: str) -> SearchHitV1:
    return hit.model_copy(
        update={
            "hit_id": deterministic_id(
                "hit",
                {
                    "provider": hit.provider,
                    "provider_record_id": hit.provider_record_id,
                    "query_id": query_id,
                    "provider_rank": hit.provider_rank,
                },
            ),
            "query_ids": (query_id,),
        }
    )
