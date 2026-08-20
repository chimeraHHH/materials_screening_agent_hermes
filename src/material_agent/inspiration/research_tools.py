"""Audited search tools for the generic materials research graph."""

from __future__ import annotations

import hashlib
import threading
from collections.abc import Callable, Mapping
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from typing import Any, Literal
from urllib.parse import urlsplit

from pydantic import Field, model_validator
from pymatgen.analysis.structure_matcher import SpeciesComparator, StructureMatcher
from pymatgen.core import Composition, Element, Structure

from material_agent.inspiration.deepseek_agent import DeepSeekFunctionTool
from material_agent.inspiration.fulltext import LawfulFullTextResolver
from material_agent.inspiration.models import (
    ArtifactPointerV1,
    SearchQueryKind,
    SearchQueryV1,
    canonical_json_bytes,
)
from material_agent.inspiration.native_search import (
    DeepSeekNativeSearchDiscovery,
    NativeSearchLeadV1,
)
from material_agent.inspiration.opencitations import (
    OpenCitationsPublicAdapter,
    OpenCitationsRequestV1,
    OpenCitationsRoute,
    parse_opencitations_page,
)
from material_agent.inspiration.query_context import AnchorPolarity
from material_agent.inspiration.research_graph import (
    CandidateLiteratureRetrievalV1,
    CandidateSetV1,
    ConstraintGraphV1,
    DatabaseCandidateV1,
    DatabaseFederationAuditV1,
    DatabaseSourceQueryReceiptV1,
    DatabaseSourceRecordV1,
    LeadEvidenceResolutionV1,
    ResolvedEvidenceV1,
)
from material_agent.inspiration.search import (
    SearchAdapter,
    SearchAdapterError,
    normalize_document_url,
    normalize_doi,
    parse_arxiv_page,
    parse_crossref_page,
    parse_multi_source_page,
    parse_openalex_page,
    parse_osti_page,
)
from material_agent.inspiration.semantic_scholar import (
    SemanticScholarPublicAdapter,
    SemanticScholarRequestV1,
    SemanticScholarRoute,
    parse_semantic_scholar_page,
    parse_semantic_scholar_topic_page,
)
from material_agent.inspiration.specter2 import Specter2EvidenceRanker
from material_agent.orchestrator.llm import LLMProviderError
from material_agent.orchestrator.models import StrictModel
from material_agent.retrieval.adapters import (
    C2dbAdapter,
    MaterialsProjectAdapter,
    MaterialsSourceAdapter,
    Mc3dAdapter,
    NomadAdapter,
)
from material_agent.retrieval.models import Requirement, SourceDatabase, SourceMetadata
from material_agent.retrieval.mp_deep_screen import (
    periodic_connectivity_score,
    transition_metal_elements,
)
from material_agent.retrieval.query import (
    build_query_plan,
    retrieval_policy_for_source,
)
from material_agent.retrieval.storage import LocalArtifactStore
from material_agent.retrieval.structures import (
    calculate_dimensionality,
    process_structure,
)


class NativeDiscoveryArgsV1(StrictModel):
    query: str = Field(min_length=3, max_length=512)


class AuthoritativeLiteratureSearchArgsV1(StrictModel):
    query: str = Field(min_length=3, max_length=512)
    target_constraint_ids: tuple[str, ...] = Field(
        min_length=1,
        max_length=16,
        description="One to sixteen existing constraint IDs; this list cannot be empty.",
    )
    max_hits: int = Field(ge=1, le=20)
    route: Literal[
        "TOPIC", "RECOMMENDATIONS", "REFERENCES", "CITATIONS", "FULL_TEXT"
    ] = "TOPIC"
    anchor_paper_id: str = Field(
        default="",
        max_length=256,
        description=(
            "Required for graph routes. Prefer a normalized DOI; Semantic Scholar paper "
            "IDs are also accepted when OpenCitations cross-validation is unavailable."
        ),
    )

    @model_validator(mode="after")
    def validate_route(self) -> AuthoritativeLiteratureSearchArgsV1:
        if self.route == "TOPIC" and self.anchor_paper_id:
            raise ValueError("TOPIC searches cannot contain anchor_paper_id")
        if self.route != "TOPIC" and not self.anchor_paper_id.strip():
            raise ValueError("graph searches require anchor_paper_id")
        return self


class CounterEvidenceSearchArgsV1(StrictModel):
    query: str = Field(min_length=3, max_length=512)
    target_constraint_ids: tuple[str, ...] = Field(min_length=1, max_length=16)
    max_hits: int = Field(default=10, ge=1, le=20)


class AuthoritativeLiteratureCheckpointV1(StrictModel):
    schema_version: Literal["authoritative-literature-tool-checkpoint-v1"] = (
        "authoritative-literature-tool-checkpoint-v1"
    )
    calls: int = Field(ge=0, le=24)
    candidate_calls: int = Field(ge=0, le=8)
    fulltext_calls: int = Field(ge=0, le=8)
    counter_calls: int = Field(ge=0, le=16)
    physical_requests: int = Field(ge=0, le=512)
    counter_queries: tuple[str, ...] = Field(default=(), max_length=16)
    evidence: tuple[ResolvedEvidenceV1, ...] = Field(default=(), max_length=256)

    @model_validator(mode="after")
    def validate_checkpoint(self) -> AuthoritativeLiteratureCheckpointV1:
        ids = tuple(item.evidence_id for item in self.evidence)
        if len(ids) != len(set(ids)):
            raise ValueError("evidence checkpoint IDs must be unique")
        if self.counter_calls < len(self.counter_queries):
            raise ValueError("executed counter queries cannot exceed counter calls")
        return self


class FederatedCandidateSearchArgsV1(StrictModel):
    required_elements: tuple[str, ...] = Field(
        min_length=1,
        max_length=4,
        description=(
            "One to four chemical element symbols; must include at least one transition metal."
        ),
    )
    excluded_elements: tuple[str, ...] = Field(
        max_length=16,
        description="Chemical element symbols to exclude; use an empty list if none.",
    )
    exact_formula: str = Field(
        max_length=64,
        description="Exact formula filter, or an empty string when no exact formula is required.",
    )
    max_candidates: int = Field(
        ge=1,
        le=8,
        description=(
            "Maximum candidates accepted from each enabled database for this query, "
            "from one to eight. Every enabled source is queried automatically."
        ),
    )


class NativeSearchToolState:
    """Expose native search while retaining only unresolved lead projections."""

    def __init__(
        self,
        discovery: DeepSeekNativeSearchDiscovery,
        *,
        max_calls: int = 12,
        max_physical_search_requests: int = 64,
    ) -> None:
        if not 1 <= max_calls <= 16:
            raise ValueError("max_calls must be between 1 and 16")
        if not 1 <= max_physical_search_requests <= 256:
            raise ValueError("max_physical_search_requests must be between 1 and 256")
        self.discovery = discovery
        self.max_calls = max_calls
        self.max_physical_search_requests = max_physical_search_requests
        self._calls = 0
        self._physical_search_requests = 0
        self._leads: dict[str, NativeSearchLeadV1] = {}
        self._lock = threading.Lock()

    def as_tool(self) -> DeepSeekFunctionTool:
        return DeepSeekFunctionTool(
            name="native_web_search",
            description=(
                "Use DeepSeek's native web search to discover broad leads. "
                "Every returned item is UNRESOLVED_LEAD and cannot support a claim."
            ),
            arguments_model=NativeDiscoveryArgsV1,
            handler=self._handle,
        )

    def snapshot(self) -> tuple[Mapping[str, Any], ...]:
        with self._lock:
            return tuple(item.model_dump(mode="json") for item in self._leads.values())

    def restore_snapshot(self, values: object) -> None:
        if not isinstance(values, list):
            raise TypeError("native-search checkpoint snapshot must be a list")
        restored = [NativeSearchLeadV1.model_validate(item) for item in values]
        with self._lock:
            if self._calls or self._leads:
                raise ValueError("native-search state must be empty before restore")
            self._leads = {item.lead_id: item for item in restored}

    def _handle(self, arguments: NativeDiscoveryArgsV1) -> Mapping[str, Any]:
        with self._lock:
            if self._calls >= self.max_calls:
                raise LLMProviderError(
                    "BUDGET_EXHAUSTED",
                    "native-search tool exhausted its call budget",
                    retryable=False,
                )
            self._calls += 1
        result = self.discovery.discover(arguments.query)
        with self._lock:
            self._physical_search_requests += result.receipt.web_search_requests
            if self._physical_search_requests > self.max_physical_search_requests:
                raise LLMProviderError(
                    "BUDGET_EXHAUSTED",
                    "native search exceeded its physical-request budget",
                    retryable=False,
                )
            for lead in result.leads:
                self._leads.setdefault(lead.lead_id, lead)
        return {
            "query": result.query,
            "leads": [lead.model_dump(mode="json") for lead in result.leads],
            "scientific_evidence_allowed": False,
            "receipt": result.receipt.model_dump(mode="json"),
        }


class AuthoritativeLiteratureSearchState:
    """Run accepted metadata adapters and persist exact bytes before parsing."""

    def __init__(
        self,
        *,
        adapter: SearchAdapter,
        store: LocalArtifactStore,
        run_id: str,
        publication_year_from: int | None = None,
        publication_year_to: int | None = None,
        max_calls: int = 24,
        max_physical_requests: int = 64,
        max_response_bytes: int = 4_000_000,
        semantic_scholar_adapter: SemanticScholarPublicAdapter | None = None,
        opencitations_adapter: OpenCitationsPublicAdapter | None = None,
        native_leads_snapshot: Callable[[], tuple[Mapping[str, Any], ...]] | None = None,
        max_candidate_calls: int = 4,
        fulltext_resolver: LawfulFullTextResolver | None = None,
        max_fulltext_calls: int = 4,
        max_counter_calls: int = 8,
        evidence_ranker: Specter2EvidenceRanker | None = None,
    ) -> None:
        if (
            not run_id
            or len(run_id) > 128
            or any(
                character
                not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-"
                for character in run_id
            )
        ):
            raise ValueError("run_id must be a safe bounded identifier")
        if not 1 <= max_calls <= 32:
            raise ValueError("max_calls must be between 1 and 32")
        if not 1 <= max_physical_requests <= 128:
            raise ValueError("max_physical_requests must be between 1 and 128")
        if not 1 <= max_response_bytes <= 10_000_000:
            raise ValueError("max_response_bytes must be between 1 and 10000000")
        if not 1 <= max_candidate_calls <= 8:
            raise ValueError("max_candidate_calls must be between 1 and 8")
        if not 1 <= max_fulltext_calls <= 8:
            raise ValueError("max_fulltext_calls must be between 1 and 8")
        if not 1 <= max_counter_calls <= 16:
            raise ValueError("max_counter_calls must be between 1 and 16")
        self.adapter = adapter
        self.store = store
        self.run_id = run_id
        self.publication_year_from = publication_year_from
        self.publication_year_to = publication_year_to
        self.max_calls = max_calls
        self.max_physical_requests = max_physical_requests
        self.max_response_bytes = max_response_bytes
        self.semantic_scholar_adapter = semantic_scholar_adapter
        self.opencitations_adapter = opencitations_adapter
        self.native_leads_snapshot = native_leads_snapshot or (lambda: ())
        self.max_candidate_calls = max_candidate_calls
        self.fulltext_resolver = fulltext_resolver
        self.max_fulltext_calls = max_fulltext_calls
        self.max_counter_calls = max_counter_calls
        self.evidence_ranker = evidence_ranker
        self._calls = 0
        self._candidate_calls = 0
        self._fulltext_calls = 0
        self._counter_calls = 0
        self._counter_queries: list[str] = []
        self._physical_requests = 0
        self._evidence: dict[str, ResolvedEvidenceV1] = {}
        self._lock = threading.Lock()

    def as_tool(self) -> DeepSeekFunctionTool:
        return DeepSeekFunctionTool(
            name="authoritative_literature_search",
            description=(
                "Search accepted scholarly metadata providers by TOPIC, or traverse an "
                "anchor paper through Semantic Scholar RECOMMENDATIONS, REFERENCES, and "
                "CITATIONS. DOI anchors on reference/citation routes are independently "
                "cross-checked through OpenCitations Index + Meta. Exact raw bytes are "
                "persisted before parsing. FULL_TEXT resolves only Unpaywall-reported OA "
                "PDFs and extracts page/section/sentence locators through local GROBID."
            ),
            arguments_model=AuthoritativeLiteratureSearchArgsV1,
            handler=self._handle,
        )

    def as_counter_tool(self) -> DeepSeekFunctionTool:
        return DeepSeekFunctionTool(
            name="execute_counter_evidence_search",
            description=(
                "Execute a concrete falsification, null-result, instability, competing-"
                "mechanism, or prior-art query against the authoritative literature "
                "federation. Every counter_evidence_query in the final skeptic output must "
                "first be executed with this tool."
            ),
            arguments_model=CounterEvidenceSearchArgsV1,
            handler=self._handle_counter_search,
        )

    def counter_queries_snapshot(self) -> tuple[str, ...]:
        with self._lock:
            return tuple(self._counter_queries)

    def snapshot(self) -> tuple[ResolvedEvidenceV1, ...]:
        with self._lock:
            return tuple(self._evidence.values())

    def checkpoint_snapshot(self) -> Mapping[str, Any]:
        with self._lock:
            checkpoint = AuthoritativeLiteratureCheckpointV1(
                calls=self._calls,
                candidate_calls=self._candidate_calls,
                fulltext_calls=self._fulltext_calls,
                counter_calls=self._counter_calls,
                physical_requests=self._physical_requests,
                counter_queries=tuple(self._counter_queries),
                evidence=tuple(self._evidence.values()),
            )
        return checkpoint.model_dump(mode="json")

    def lead_resolutions_snapshot(self) -> tuple[LeadEvidenceResolutionV1, ...]:
        with self._lock:
            evidence = tuple(self._evidence.values())
        by_lead = {
            lead_id: item
            for item in evidence
            for lead_id in item.source_lead_ids
        }
        resolutions: list[LeadEvidenceResolutionV1] = []
        for lead in self.native_leads_snapshot():
            lead_id = lead.get("lead_id")
            if not isinstance(lead_id, str):
                continue
            item = by_lead.get(lead_id)
            if item is None:
                resolutions.append(
                    LeadEvidenceResolutionV1(
                        lead_id=lead_id,
                        status="UNRESOLVED",
                        resolution_method="NO_AUTHORITATIVE_MATCH",
                    )
                )
                continue
            method = self._lead_match_method(lead, item)
            resolutions.append(
                LeadEvidenceResolutionV1(
                    lead_id=lead_id,
                    status="RESOLVED",
                    doi=item.doi,
                    document_id=item.document_id,
                    evidence_id=item.evidence_id,
                    resolution_method=method or "NORMALIZED_TITLE",
                )
            )
        return tuple(resolutions)

    def restore_snapshot(self, values: object) -> None:
        if isinstance(values, list):
            checkpoint = AuthoritativeLiteratureCheckpointV1(
                calls=0,
                candidate_calls=0,
                fulltext_calls=0,
                counter_calls=0,
                physical_requests=0,
                evidence=tuple(
                    ResolvedEvidenceV1.model_validate_json(canonical_json_bytes(item))
                    for item in values
                ),
            )
        else:
            checkpoint = AuthoritativeLiteratureCheckpointV1.model_validate_json(
                canonical_json_bytes(values)
            )
        if checkpoint.calls > self.max_calls:
            raise ValueError("evidence checkpoint exceeds primary call budget")
        if checkpoint.candidate_calls > self.max_candidate_calls:
            raise ValueError("evidence checkpoint exceeds candidate call budget")
        if checkpoint.fulltext_calls > self.max_fulltext_calls:
            raise ValueError("evidence checkpoint exceeds full-text call budget")
        if checkpoint.counter_calls > self.max_counter_calls:
            raise ValueError("evidence checkpoint exceeds counter call budget")
        if checkpoint.physical_requests > self.max_physical_requests:
            raise ValueError("evidence checkpoint exceeds physical-request budget")
        with self._lock:
            self._calls = checkpoint.calls
            self._candidate_calls = checkpoint.candidate_calls
            self._fulltext_calls = checkpoint.fulltext_calls
            self._counter_calls = checkpoint.counter_calls
            self._physical_requests = checkpoint.physical_requests
            self._counter_queries = list(checkpoint.counter_queries)
            self._evidence = {
                item.evidence_id: item for item in checkpoint.evidence
            }

    def _handle(
        self, arguments: AuthoritativeLiteratureSearchArgsV1
    ) -> Mapping[str, Any]:
        if arguments.route == "FULL_TEXT":
            return self._handle_fulltext(arguments)
        if arguments.route != "TOPIC":
            return self._handle_graph(arguments)
        return self._handle_topic(arguments)

    def _handle_counter_search(
        self, arguments: CounterEvidenceSearchArgsV1
    ) -> Mapping[str, Any]:
        normalized = " ".join(arguments.query.split())
        result = self._handle_topic(
            AuthoritativeLiteratureSearchArgsV1(
                query=normalized,
                target_constraint_ids=arguments.target_constraint_ids,
                max_hits=arguments.max_hits,
            ),
            lane="counter",
        )
        with self._lock:
            self._counter_queries.append(normalized)
        return {**result, "counter_query_executed": normalized}

    def _handle_fulltext(
        self, arguments: AuthoritativeLiteratureSearchArgsV1
    ) -> Mapping[str, Any]:
        doi = normalize_doi(arguments.anchor_paper_id)
        if doi is None:
            raise LLMProviderError(
                "FULLTEXT_REQUIRES_DOI",
                "FULL_TEXT requires a DOI anchor",
                retryable=False,
            )
        if self.fulltext_resolver is None:
            raise LLMProviderError(
                "FULLTEXT_UNAVAILABLE",
                "lawful full-text resolver is not configured",
                retryable=False,
            )
        with self._lock:
            if self._fulltext_calls >= self.max_fulltext_calls:
                raise LLMProviderError(
                    "BUDGET_EXHAUSTED",
                    "full-text resolution exhausted its call budget",
                    retryable=False,
                )
            record = next(
                (item for item in self._evidence.values() if item.doi == doi), None
            )
            self._fulltext_calls += 1
        if record is None:
            raise LLMProviderError(
                "FULLTEXT_METADATA_REQUIRED",
                "resolve DOI metadata before requesting full text",
                retryable=False,
            )
        result = self.fulltext_resolver.resolve(
            doi=doi,
            document_id=record.document_id,
            evidence_id=record.evidence_id,
        )
        if result.status == "RESOLVED":
            spans = (
                self.evidence_ranker.rank_spans(
                    arguments.query, result.spans, max_results=64
                )
                if self.evidence_ranker is not None
                else result.spans
            )
            updated = record.model_copy(
                update={
                    "full_text_status": "RESOLVED",
                    "full_text_spans": spans,
                    "literature_figures": result.figures,
                    "evidence_scope": "OPEN_ACCESS_FULL_TEXT",
                    "supported_constraint_ids": tuple(
                        dict.fromkeys(
                            (
                                *record.supported_constraint_ids,
                                *arguments.target_constraint_ids,
                            )
                        )
                    ),
                }
            )
        else:
            updated = record.model_copy(update={"full_text_status": "UNAVAILABLE"})
        with self._lock:
            self._evidence[record.evidence_id] = updated
        return {
            "record": updated.model_dump(mode="json"),
            "fulltext_resolution": result.model_dump(mode="json"),
            "scientific_evidence_allowed": result.status == "RESOLVED",
            "span_ranking_model": (
                self.evidence_ranker.provider.model_identity
                if self.evidence_ranker is not None
                else None
            ),
        }

    def _handle_topic(
        self,
        arguments: AuthoritativeLiteratureSearchArgsV1,
        *,
        lane: Literal["primary", "candidate", "counter"] = "primary",
    ) -> Mapping[str, Any]:
        semantic = {
            "query": " ".join(arguments.query.split()),
            "target_constraint_ids": arguments.target_constraint_ids,
        }
        query_id = (
            "query-" + hashlib.sha256(canonical_json_bytes(semantic)).hexdigest()[:24]
        )
        query = SearchQueryV1(
            query_id=query_id,
            kind=SearchQueryKind.DIRECT,
            text=semantic["query"],
            tag_ids=("materials-research",),
        )
        remaining_requests = self._reserve_logical_call(
            lane=lane
        )
        try:
            page = self.adapter.search(
                query,
                max_response_bytes=self.max_response_bytes,
                max_physical_requests=remaining_requests,
            )
        except SearchAdapterError as exc:
            with self._lock:
                self._physical_requests += len(exc.attempts)
            raise LLMProviderError(
                "AUTHORITATIVE_SEARCH_FAILED",
                f"authoritative metadata search failed with {exc.code}",
                retryable=False,
            ) from exc
        with self._lock:
            self._physical_requests += len(page.attempts)
            if self._physical_requests > self.max_physical_requests:
                raise LLMProviderError(
                    "BUDGET_EXHAUSTED",
                    "authoritative search exceeded its physical-request budget",
                    retryable=False,
                )
        raw_hash = hashlib.sha256(page.payload).hexdigest()
        extension = "xml" if "xml" in page.media_type else "json"
        raw_ref = self.store.write_bytes(
            f"research/{self.run_id}/raw_search/{query_id}-{raw_hash[:16]}.{extension}",
            page.payload,
            page.media_type,
            immutable=True,
        )
        pointer = ArtifactPointerV1.model_validate(raw_ref.model_dump(mode="python"))
        parsed = self._parse(
            page.provider,
            query=query,
            payload=page.payload,
            pointer=pointer,
            max_hits=arguments.max_hits,
        )
        created = self._record_hits(
            parsed.hits,
            pointer=pointer,
            target_constraint_ids=arguments.target_constraint_ids,
        )
        return {
            "query_id": query_id,
            "records": [item.model_dump(mode="json") for item in created],
            "warnings": parsed.warnings,
            "evidence_scope": "METADATA_OR_ABSTRACT_ONLY",
            "raw_response": pointer.model_dump(mode="json"),
        }

    def search_candidates(
        self, candidates: CandidateSetV1, constraints: ConstraintGraphV1
    ) -> CandidateLiteratureRetrievalV1:
        before = {item.evidence_id for item in self.snapshot()}
        queries: list[str] = []
        failures: list[str] = []
        constraint_ids = tuple(item.constraint_id for item in constraints.constraints)
        for candidate in candidates.candidates[: self.max_candidate_calls]:
            terms = [candidate.material_name]
            if candidate.formula and candidate.formula.casefold() not in (
                candidate.material_name.casefold()
            ):
                terms.append(candidate.formula)
            terms.extend(
                [
                    candidate.mechanism[:180],
                    "flat band orbital Fermi level oxidation state layered material",
                ]
            )
            query = " ".join(" ".join(terms).split())[:512]
            queries.append(query)
            try:
                self._handle_topic(
                    AuthoritativeLiteratureSearchArgsV1(
                        query=query,
                        target_constraint_ids=constraint_ids[:16],
                        max_hits=10,
                    ),
                    lane="candidate",
                )
            except (LLMProviderError, SearchAdapterError) as exc:
                failures.append(
                    f"{candidate.candidate_id}:{getattr(exc, 'code', type(exc).__name__)}"
                )
        after = {item.evidence_id for item in self.snapshot()}
        return CandidateLiteratureRetrievalV1(
            triggered=True,
            queries=tuple(queries),
            new_evidence_ids=tuple(sorted(after - before)),
            failures=tuple(failures),
        )

    def _reserve_logical_call(
        self, *, lane: Literal["primary", "candidate", "counter"]
    ) -> int:
        with self._lock:
            if lane == "candidate":
                used, limit = self._candidate_calls, self.max_candidate_calls
            elif lane == "counter":
                used, limit = self._counter_calls, self.max_counter_calls
            else:
                used, limit = self._calls, self.max_calls
            if used >= limit:
                raise LLMProviderError(
                    "BUDGET_EXHAUSTED",
                    f"{lane} search exhausted its call budget",
                    retryable=False,
                )
            remaining_requests = self.max_physical_requests - self._physical_requests
            if remaining_requests <= 0:
                raise LLMProviderError(
                    "BUDGET_EXHAUSTED",
                    "authoritative-search tool exhausted its physical-request budget",
                    retryable=False,
                )
            if lane == "candidate":
                self._candidate_calls += 1
            elif lane == "counter":
                self._counter_calls += 1
            else:
                self._calls += 1
            return remaining_requests

    def _handle_graph(
        self, arguments: AuthoritativeLiteratureSearchArgsV1
    ) -> Mapping[str, Any]:
        semantic = {
            "route": arguments.route,
            "anchor_paper_id": arguments.anchor_paper_id.strip(),
            "query": " ".join(arguments.query.split()),
            "target_constraint_ids": arguments.target_constraint_ids,
        }
        query_id = (
            "query-" + hashlib.sha256(canonical_json_bytes(semantic)).hexdigest()[:24]
        )
        self._reserve_logical_call(lane="primary")
        pages: list[tuple[object, ArtifactPointerV1, tuple[object, ...]]] = []
        failures: list[str] = []
        route = SemanticScholarRoute(arguments.route)
        anchor_doi = normalize_doi(arguments.anchor_paper_id)
        if self.semantic_scholar_adapter is not None:
            payload = {
                "schema_version": "semantic-scholar-request-v1",
                "context_id": query_id,
                "route": route,
                "query_text": None,
                "anchor_paper_id": (
                    f"DOI:{anchor_doi}"
                    if anchor_doi is not None
                    else arguments.anchor_paper_id.strip()
                ),
                "anchor_polarity": AnchorPolarity.POSITIVE,
                "max_results": arguments.max_hits,
                "publication_year_from": self.publication_year_from,
                "publication_year_to": self.publication_year_to,
            }
            request = SemanticScholarRequestV1(
                query_id=(
                    "s2-query-"
                    + hashlib.sha256(canonical_json_bytes(payload)).hexdigest()[:24]
                ),
                **payload,
            )
            try:
                page = self._run_graph_adapter(self.semantic_scholar_adapter, request)
                pointer = self._persist_graph_page(page, query_id, "semantic-scholar")
                parsed = parse_semantic_scholar_page(
                    request=request,
                    payload=page.payload,
                    raw_response_artifact=pointer,
                )
                pages.append((page, pointer, parsed.hits))
            except (SearchAdapterError, LLMProviderError) as exc:
                failures.append(f"semantic-scholar:{getattr(exc, 'code', type(exc).__name__)}")
        if (
            self.opencitations_adapter is not None
            and anchor_doi is not None
            and arguments.route in {"REFERENCES", "CITATIONS"}
        ):
            request = OpenCitationsRequestV1(
                query_id=query_id,
                route=OpenCitationsRoute(arguments.route),
                anchor_doi=anchor_doi,
                max_results=arguments.max_hits,
                publication_year_from=self.publication_year_from,
                publication_year_to=self.publication_year_to,
            )
            try:
                page = self._run_graph_adapter(self.opencitations_adapter, request)
                pointer = self._persist_graph_page(page, query_id, "opencitations")
                parsed = parse_opencitations_page(
                    request=request,
                    payload=page.payload,
                    raw_response_artifact=pointer,
                )
                pages.append((page, pointer, parsed.hits))
            except (SearchAdapterError, LLMProviderError) as exc:
                failures.append(f"opencitations:{getattr(exc, 'code', type(exc).__name__)}")
        if not pages:
            raise LLMProviderError(
                "AUTHORITATIVE_GRAPH_SEARCH_FAILED",
                "all configured citation-graph providers failed or were unavailable",
                retryable=False,
            )
        created: list[ResolvedEvidenceV1] = []
        raw_responses: list[Mapping[str, Any]] = []
        for _, pointer, hits in pages:
            created.extend(
                self._record_hits(
                    hits,
                    pointer=pointer,
                    target_constraint_ids=arguments.target_constraint_ids,
                )
            )
            raw_responses.append(pointer.model_dump(mode="json"))
        unique = {item.evidence_id: item for item in created}
        return {
            "query_id": query_id,
            "route": arguments.route,
            "anchor_paper_id": arguments.anchor_paper_id,
            "records": [item.model_dump(mode="json") for item in unique.values()],
            "provider_failures": failures,
            "evidence_scope": "METADATA_OR_ABSTRACT_ONLY",
            "raw_responses": raw_responses,
        }

    def _run_graph_adapter(self, adapter: object, request: object):
        with self._lock:
            remaining = self.max_physical_requests - self._physical_requests
        if remaining <= 0:
            raise LLMProviderError(
                "BUDGET_EXHAUSTED",
                "authoritative-search tool exhausted its physical-request budget",
                retryable=False,
            )
        try:
            page = adapter.search(  # type: ignore[attr-defined]
                request,
                max_response_bytes=self.max_response_bytes,
                max_physical_requests=remaining,
            )
        except SearchAdapterError as exc:
            with self._lock:
                self._physical_requests += len(exc.attempts)
            raise
        with self._lock:
            self._physical_requests += len(page.attempts)
            if self._physical_requests > self.max_physical_requests:
                raise LLMProviderError(
                    "BUDGET_EXHAUSTED",
                    "authoritative search exceeded its physical-request budget",
                    retryable=False,
                )
        return page

    def _persist_graph_page(
        self, page: object, query_id: str, provider: str
    ) -> ArtifactPointerV1:
        payload = page.payload  # type: ignore[attr-defined]
        raw_hash = hashlib.sha256(payload).hexdigest()
        raw_ref = self.store.write_bytes(
            f"research/{self.run_id}/raw_search/{query_id}-{provider}-{raw_hash[:16]}.json",
            payload,
            "application/json",
            immutable=True,
        )
        return ArtifactPointerV1.model_validate(raw_ref.model_dump(mode="python"))

    def _record_hits(
        self,
        hits: tuple[object, ...],
        *,
        pointer: ArtifactPointerV1,
        target_constraint_ids: tuple[str, ...],
    ) -> list[ResolvedEvidenceV1]:
        created: list[ResolvedEvidenceV1] = []
        for hit in hits:
            stable_id = hit.doi or hit.arxiv_id or hit.provider_record_id  # type: ignore[attr-defined]
            document_id = hit.document_id  # type: ignore[attr-defined]
            evidence_id = (
                "evidence-"
                + hashlib.sha256(
                    canonical_json_bytes({"document_id": document_id})
                ).hexdigest()[:24]
            )
            provisional = ResolvedEvidenceV1(
                evidence_id=evidence_id,
                document_id=document_id,
                provider=hit.provider,  # type: ignore[attr-defined]
                source_providers=(hit.provider,),  # type: ignore[attr-defined]
                stable_record_id=stable_id,
                title=hit.title,  # type: ignore[attr-defined]
                published_year=hit.published_year,  # type: ignore[attr-defined]
                doi=hit.doi,  # type: ignore[attr-defined]
                arxiv_id=hit.arxiv_id,  # type: ignore[attr-defined]
                canonical_url=hit.canonical_url,  # type: ignore[attr-defined]
                abstract_excerpt=(
                    hit.abstract[:2_000] if hit.abstract else None  # type: ignore[attr-defined]
                ),
                raw_response_uri=pointer.uri,
                raw_response_sha256=pointer.sha256,
                supported_constraint_ids=target_constraint_ids,
                source_lead_ids=self._matching_lead_ids(hit),
            )
            with self._lock:
                existing = self._evidence.get(evidence_id)
                record = (
                    provisional
                    if existing is None
                    else existing.model_copy(
                        update={
                            "source_providers": tuple(
                                dict.fromkeys(
                                    (*existing.source_providers, hit.provider)  # type: ignore[attr-defined]
                                )
                            ),
                            "supported_constraint_ids": tuple(
                                dict.fromkeys(
                                    (*existing.supported_constraint_ids, *target_constraint_ids)
                                )
                            ),
                            "source_lead_ids": tuple(
                                dict.fromkeys(
                                    (*existing.source_lead_ids, *provisional.source_lead_ids)
                                )
                            ),
                            "abstract_excerpt": (
                                existing.abstract_excerpt
                                or provisional.abstract_excerpt
                            ),
                        }
                    )
                )
                self._evidence[evidence_id] = record
            created.append(record)
        return created

    def _matching_lead_ids(self, hit: object) -> tuple[str, ...]:
        return tuple(
            lead_id
            for lead in self.native_leads_snapshot()
            if isinstance((lead_id := lead.get("lead_id")), str)
            and self._lead_match_method(lead, hit) is not None
        )

    @staticmethod
    def _lead_match_method(
        lead: Mapping[str, Any], evidence: object
    ) -> Literal["DOI_URL", "NORMALIZED_URL", "NORMALIZED_TITLE"] | None:
        lead_url = lead.get("url")
        evidence_doi = getattr(evidence, "doi", None)
        if isinstance(lead_url, str) and evidence_doi is not None:
            parsed = urlsplit(lead_url)
            lead_doi = normalize_doi(
                lead_url
                if parsed.netloc.casefold() in {"doi.org", "dx.doi.org"}
                else None
            )
            if lead_doi == evidence_doi:
                return "DOI_URL"
        evidence_url = getattr(evidence, "canonical_url", None)
        if isinstance(lead_url, str) and normalize_document_url(lead_url) == (
            normalize_document_url(evidence_url)
        ):
            return "NORMALIZED_URL"
        lead_title = lead.get("title")
        evidence_title = getattr(evidence, "title", None)
        if isinstance(lead_title, str) and isinstance(evidence_title, str) and (
            " ".join(lead_title.casefold().split())
            == " ".join(evidence_title.casefold().split())
        ):
            return "NORMALIZED_TITLE"
        return None

    def _parse(
        self,
        provider: str,
        *,
        query: SearchQueryV1,
        payload: bytes,
        pointer: ArtifactPointerV1,
        max_hits: int,
    ):
        common = {
            "query": query,
            "payload": payload,
            "raw_response_artifact": pointer,
            "max_hits": max_hits,
            "publication_year_from": self.publication_year_from,
            "publication_year_to": self.publication_year_to,
        }
        if provider == "multi-source-v1":
            return parse_multi_source_page(**common)
        if provider == "crossref":
            return parse_crossref_page(**common)
        if provider in {"openalex", "openalex-fixture"}:
            return parse_openalex_page(**common, provider=provider)
        if provider == "arxiv":
            return parse_arxiv_page(**common)
        if provider == "osti":
            return parse_osti_page(**common)
        if provider == "semantic-scholar":
            return parse_semantic_scholar_topic_page(**common)
        raise LLMProviderError(
            "AUTHORITATIVE_SEARCH_FAILED",
            "authoritative metadata adapter returned an unsupported provider",
            retryable=False,
        )


FEDERATED_SOURCE_ORDER = (
    SourceDatabase.C2DB,
    SourceDatabase.MC3D,
    SourceDatabase.NOMAD,
    SourceDatabase.MATERIALS_PROJECT,
)
FEDERATED_SOURCE_LICENSES = {
    SourceDatabase.C2DB: "CC-BY-NC-4.0",
    SourceDatabase.MC3D: "LicenseRef-MC3D-Source-Terms",
    SourceDatabase.NOMAD: "LicenseRef-PerUpload-Unresolved",
    SourceDatabase.MATERIALS_PROJECT: "CC-BY-4.0",
}


@dataclass(frozen=True, slots=True)
class _SourceSearchResult:
    source: SourceDatabase
    metadata: SourceMetadata
    query_fingerprint: str
    documents: tuple[dict[str, Any], ...]


@dataclass(frozen=True, slots=True)
class _NormalizedSourceRecord:
    source_record: DatabaseSourceRecordV1
    structure: Structure
    formula: str
    elements: tuple[str, ...]
    transition_metals: tuple[str, ...]
    dimensionality: int | None
    connectivity: float | None


class FederatedCandidateSearchState:
    """Federate audited structure searches and preserve source-level failures."""

    def __init__(
        self,
        *,
        store: LocalArtifactStore,
        run_id: str,
        adapters: Mapping[SourceDatabase | str, MaterialsSourceAdapter] | None = None,
        enabled_sources: tuple[SourceDatabase | str, ...] | None = None,
        environment: Mapping[str, str] | None = None,
        max_calls: int = 4,
        max_total_candidates: int = 24,
    ) -> None:
        if (
            not run_id
            or len(run_id) > 128
            or any(
                character
                not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-"
                for character in run_id
            )
        ):
            raise ValueError("run_id must be a safe bounded identifier")
        if not 1 <= max_calls <= 24:
            raise ValueError("max_calls must be between 1 and 24")
        if not 1 <= max_total_candidates <= 64:
            raise ValueError("max_total_candidates must be between 1 and 64")
        if adapters is None:
            selected_adapters: dict[SourceDatabase, MaterialsSourceAdapter] = {
                SourceDatabase.C2DB: C2dbAdapter(max_concurrent_downloads=4),
                SourceDatabase.MC3D: Mc3dAdapter(),
                SourceDatabase.NOMAD: NomadAdapter(),
                SourceDatabase.MATERIALS_PROJECT: MaterialsProjectAdapter(
                    environment=environment
                ),
            }
        else:
            selected_adapters = {
                SourceDatabase(source): adapter for source, adapter in adapters.items()
            }
        selected_sources = tuple(
            SourceDatabase(source)
            for source in (
                enabled_sources
                if enabled_sources is not None
                else tuple(selected_adapters)
            )
        )
        if len(selected_sources) < 2 or len(set(selected_sources)) != len(
            selected_sources
        ):
            raise ValueError("federated search requires at least two unique sources")
        unsupported = set(selected_sources) - set(FEDERATED_SOURCE_ORDER)
        missing = set(selected_sources) - set(selected_adapters)
        if unsupported or missing:
            raise ValueError(
                "federated search contains an unsupported or missing adapter"
            )
        self.store = store
        self.run_id = run_id
        self.adapters = selected_adapters
        self.enabled_sources = tuple(
            source for source in FEDERATED_SOURCE_ORDER if source in selected_sources
        )
        self.max_calls = max_calls
        self.max_total_candidates = max_total_candidates
        self._calls = 0
        self._candidates: dict[str, DatabaseCandidateV1] = {}
        self._candidate_structures: dict[str, Structure] = {}
        self._receipts: list[DatabaseSourceQueryReceiptV1] = []
        self._source_record_keys: set[tuple[str, str]] = set()
        self._source_record_count = 0
        self._merge_count = 0
        self._lock = threading.RLock()
        self._matcher = StructureMatcher(
            ltol=0.1,
            stol=0.15,
            angle_tol=3,
            primitive_cell=True,
            scale=False,
            attempt_supercell=False,
            allow_subset=False,
            comparator=SpeciesComparator(),
        )

    def as_tool(self) -> DeepSeekFunctionTool:
        return DeepSeekFunctionTool(
            name="search_federated_materials_candidates",
            description=(
                "Search every enabled official materials database in one federated call "
                "(C2DB, Materials Cloud MC3D, NOMAD, and Materials Project when its "
                "credential is available). "
                "The service, not the model, enforces multi-source fan-out, per-source "
                "failure isolation, canonical structure normalization, strict cross-source "
                "deduplication, and complete provenance. Returns structure/scalar evidence "
                "only; flat-band, Fermi-ordering, orbital and oxidation conclusions remain "
                "UNKNOWN and belong to the separate hypothesis-reasoning layer."
            ),
            arguments_model=FederatedCandidateSearchArgsV1,
            handler=self._handle,
        )

    def snapshot(self) -> tuple[DatabaseCandidateV1, ...]:
        with self._lock:
            return tuple(self._candidates[key] for key in sorted(self._candidates))

    def federation_snapshot(self) -> DatabaseFederationAuditV1:
        with self._lock:
            return DatabaseFederationAuditV1(
                enabled_sources=tuple(source.value for source in self.enabled_sources),
                receipts=tuple(self._receipts),
                source_record_count=self._source_record_count,
                federated_candidate_count=len(self._candidates),
                exact_or_equivalent_merge_count=self._merge_count,
            )

    def checkpoint_snapshot(self) -> Mapping[str, Any]:
        with self._lock:
            return {
                "candidates": [
                    self._candidates[key].model_dump(mode="json")
                    for key in sorted(self._candidates)
                ],
                "receipts": [item.model_dump(mode="json") for item in self._receipts],
                "source_record_count": self._source_record_count,
                "merge_count": self._merge_count,
            }

    def restore_snapshot(self, values: object) -> None:
        if not isinstance(values, dict):
            raise TypeError("federated database checkpoint snapshot must be an object")
        candidates = values.get("candidates")
        receipts = values.get("receipts")
        if not isinstance(candidates, list) or not isinstance(receipts, list):
            raise TypeError("federated database checkpoint lists are missing")
        restored = [DatabaseCandidateV1.model_validate(item) for item in candidates]
        restored_receipts = [
            DatabaseSourceQueryReceiptV1.model_validate(item) for item in receipts
        ]
        with self._lock:
            if self._calls or self._candidates or self._receipts:
                raise ValueError("database state must be empty before restore")
            for item in restored:
                path = self.store.root / item.structure_artifact_uri.removeprefix(
                    "artifact://"
                )
                self._candidate_structures[item.database_candidate_id] = (
                    Structure.from_str(path.read_text(), fmt="cif")
                )
                self._candidates[item.database_candidate_id] = item
                self._source_record_keys.update(
                    (source.source_database, source.source_material_id)
                    for source in item.source_records
                )
            self._receipts = restored_receipts
            self._source_record_count = int(values.get("source_record_count", 0))
            self._merge_count = int(values.get("merge_count", 0))

    def _handle(self, arguments: FederatedCandidateSearchArgsV1) -> Mapping[str, Any]:
        required = tuple(sorted(set(arguments.required_elements)))
        excluded = tuple(sorted(set(arguments.excluded_elements)))
        exact_formula = arguments.exact_formula.strip() or None
        if set(required) & set(excluded):
            raise ValueError("required and excluded elements overlap")
        for symbol in (*required, *excluded):
            try:
                Element(symbol)
            except (ValueError, KeyError) as exc:
                raise ValueError(
                    "federated search contains an invalid element"
                ) from exc
        if not transition_metal_elements(required):
            raise ValueError("each federated query must include a transition metal")
        with self._lock:
            if self._calls >= self.max_calls:
                raise LLMProviderError(
                    "BUDGET_EXHAUSTED",
                    "federated database search exhausted its call budget",
                    retryable=False,
                )
            if len(self._candidates) >= self.max_total_candidates:
                raise LLMProviderError(
                    "BUDGET_EXHAUSTED",
                    "federated database search exhausted its candidate budget",
                    retryable=False,
                )
            self._calls += 1
            query_ordinal = self._calls
        requirement = self._requirement(
            required=required,
            excluded=excluded,
            exact_formula=exact_formula,
            max_candidates=arguments.max_candidates,
            query_ordinal=query_ordinal,
        )
        requirement_hash = hashlib.sha256(
            canonical_json_bytes(requirement.model_dump(mode="json"))
        ).hexdigest()
        source_results: dict[SourceDatabase, _SourceSearchResult] = {}
        failures: dict[SourceDatabase, DatabaseSourceQueryReceiptV1] = {}
        with ThreadPoolExecutor(
            max_workers=len(self.enabled_sources),
            thread_name_prefix="federated-materials-db",
        ) as executor:
            futures = {
                executor.submit(
                    self._search_source,
                    source,
                    requirement,
                    requirement_hash,
                    arguments.max_candidates,
                ): source
                for source in self.enabled_sources
            }
            for future in as_completed(futures):
                source = futures[future]
                try:
                    source_results[source] = future.result()
                # Source adapters intentionally have heterogeneous HTTP/client
                # exception hierarchies; isolation must catch any source-local failure.
                except Exception as exc:  # noqa: BLE001
                    unavailable = source is SourceDatabase.MATERIALS_PROJECT and (
                        "API key is unavailable" in str(exc)
                        or "API key is empty" in str(exc)
                    )
                    failures[source] = DatabaseSourceQueryReceiptV1(
                        source_database=source.value,
                        query_ordinal=query_ordinal,
                        status=("UNAVAILABLE_CREDENTIAL" if unavailable else "FAILED"),
                        error_category=(
                            "CREDENTIAL_UNAVAILABLE"
                            if unavailable
                            else type(exc).__name__
                        ),
                    )

        normalized: list[_NormalizedSourceRecord] = []
        receipts: list[DatabaseSourceQueryReceiptV1] = []
        for source in self.enabled_sources:
            if source in failures:
                receipts.append(failures[source])
                continue
            result = source_results[source]
            accepted = 0
            for document in result.documents:
                item = self._normalize_document(
                    source=source,
                    metadata=result.metadata,
                    query_fingerprint=result.query_fingerprint,
                    document=document,
                    required_elements=required,
                    excluded_elements=excluded,
                    exact_formula=exact_formula,
                )
                if item is not None:
                    normalized.append(item)
                    accepted += 1
            receipts.append(
                DatabaseSourceQueryReceiptV1(
                    source_database=source.value,
                    query_ordinal=query_ordinal,
                    status="SUCCEEDED" if result.documents else "EMPTY",
                    database_version=result.metadata.database_version,
                    query_fingerprint=result.query_fingerprint,
                    raw_record_count=len(result.documents),
                    accepted_record_count=accepted,
                    error_category=(
                        "ALL_RECORDS_REJECTED_BY_LOCAL_STRUCTURE_FILTERS"
                        if result.documents and not accepted
                        else None
                    ),
                )
            )
        with self._lock:
            self._receipts.extend(receipts)
            touched = self._merge_normalized(normalized)
            returned = [self._candidates[key] for key in sorted(touched)]
            audit = self.federation_snapshot().model_dump(mode="json")
        return {
            "records": [item.model_dump(mode="json") for item in returned],
            "federation_audit": audit,
            "all_enabled_sources_queried": True,
            "scientific_conclusion": False,
        }

    def _requirement(
        self,
        *,
        required: tuple[str, ...],
        excluded: tuple[str, ...],
        exact_formula: str | None,
        max_candidates: int,
        query_ordinal: int,
    ) -> Requirement:
        return Requirement.model_validate(
            {
                "requirement_id": f"req-{self.run_id}-{query_ordinal}",
                "revision": 1,
                "target_class": "2D transition-metal parent structure",
                "hard_constraints": {
                    "exact_formula": exact_formula,
                    "include_elements": list(required),
                    "exclude_elements": list(excluded),
                    "dimensionality": 2,
                    "max_num_sites": 64,
                },
                "scientific_targets": [],
                "ranking_preferences": [{"property": "num_sites", "mode": "minimize"}],
                "budget": {
                    "max_candidates": max_candidates,
                    "allow_ml": False,
                    "allow_dft": False,
                    "allow_many_body": False,
                },
                "confirmed_by_user": True,
                "policy_version": "requirement-policy-v1",
            }
        )

    def _search_source(
        self,
        source: SourceDatabase,
        requirement: Requirement,
        requirement_hash: str,
        max_candidates: int,
    ) -> _SourceSearchResult:
        adapter = self.adapters[source]
        metadata = adapter.metadata()
        scan_limit = 25 if source is SourceDatabase.C2DB else max_candidates
        policy = retrieval_policy_for_source(source).model_copy(
            update={"chunk_size": scan_limit, "max_records_scanned": scan_limit}
        )
        plan = build_query_plan(requirement, requirement_hash, metadata, policy)
        documents = tuple(adapter.search(plan)[:max_candidates])
        return _SourceSearchResult(
            source=source,
            metadata=metadata,
            query_fingerprint=plan.query_fingerprint,
            documents=documents,
        )

    def _normalize_document(
        self,
        *,
        source: SourceDatabase,
        metadata: SourceMetadata,
        query_fingerprint: str,
        document: Mapping[str, Any],
        required_elements: tuple[str, ...],
        excluded_elements: tuple[str, ...],
        exact_formula: str | None,
    ) -> _NormalizedSourceRecord | None:
        try:
            policy = retrieval_policy_for_source(source)
            processed = process_structure(
                document.get("structure"),
                summary_elements=document.get("elements"),
                summary_num_sites=document.get("nsites"),
                summary_formula=document.get("formula_pretty"),
                policy=policy,
            )
            element_set = set(processed.elements)
            if not set(required_elements) <= element_set:
                return None
            if set(excluded_elements) & element_set or processed.num_sites > 64:
                return None
            if (
                exact_formula is not None
                and Composition(exact_formula).reduced_formula
                != Composition(processed.reduced_formula).reduced_formula
            ):
                return None
            metals = tuple(transition_metal_elements(processed.elements))
            if not metals:
                return None
            dimensionality = calculate_dimensionality(processed.structure)
            if dimensionality.value is not None and dimensionality.value != 2:
                return None
            connectivity = periodic_connectivity_score(
                processed.structure, contributor_elements=set(metals)
            )
            source_id = str(document["material_id"])
            source_identity = hashlib.sha256(
                canonical_json_bytes({"source": source.value, "material_id": source_id})
            ).hexdigest()[:24]
            raw_ref = self.store.write_json(
                f"research/{self.run_id}/database/{source.value}/"
                f"source-{source_identity}.raw.json",
                document.get("source_response", {}),
                immutable=True,
            )
            structure_ref = self.store.write_text(
                f"research/{self.run_id}/database/{source.value}/"
                f"source-{source_identity}.cif",
                processed.cif_text,
                media_type="chemical/x-cif",
                immutable=True,
            )
            source_provenance = document.get("source_provenance")
            source_license = (
                source_provenance.get("license")
                if isinstance(source_provenance, Mapping)
                and isinstance(source_provenance.get("license"), str)
                and source_provenance.get("license")
                else FEDERATED_SOURCE_LICENSES[source]
            )
            source_record = DatabaseSourceRecordV1(
                source_database=source.value,
                source_material_id=source_id,
                source_database_version=metadata.database_version,
                query_fingerprint=query_fingerprint,
                canonical_structure_id=processed.structure_id,
                band_gap_ev=document.get("band_gap"),
                formation_energy_ev_atom=document.get(
                    "formation_energy_per_atom"
                ),
                energy_above_hull_ev_atom=document.get("energy_above_hull"),
                structure_artifact_uri=structure_ref.uri,
                structure_artifact_sha256=structure_ref.sha256,
                raw_response_artifact_uri=raw_ref.uri,
                raw_response_artifact_sha256=raw_ref.sha256,
                license=source_license,
            )
            return _NormalizedSourceRecord(
                source_record=source_record,
                structure=processed.structure,
                formula=processed.reduced_formula,
                elements=tuple(processed.elements),
                transition_metals=metals,
                dimensionality=dimensionality.value,
                connectivity=(
                    float(connectivity.value)
                    if connectivity.status == "RESOLVED"
                    and isinstance(connectivity.value, (int, float))
                    else None
                ),
            )
        # One malformed source record must not erase valid records from this source.
        except Exception:  # noqa: BLE001
            return None

    def _merge_normalized(self, normalized: list[_NormalizedSourceRecord]) -> set[str]:
        touched: set[str] = set()
        for item in sorted(
            normalized,
            key=lambda value: (
                FEDERATED_SOURCE_ORDER.index(
                    SourceDatabase(value.source_record.source_database)
                ),
                value.source_record.source_material_id,
            ),
        ):
            source_key = (
                item.source_record.source_database,
                item.source_record.source_material_id,
            )
            if source_key in self._source_record_keys:
                continue
            match_id = self._matching_candidate(item)
            if match_id is None:
                if len(self._candidates) >= self.max_total_candidates:
                    continue
                match_id = (
                    "db-candidate-"
                    + hashlib.sha256(
                        canonical_json_bytes(
                            {
                                "canonical_structure_id": item.source_record.canonical_structure_id
                            }
                        )
                    ).hexdigest()[:24]
                )
                while match_id in self._candidates:
                    match_id = (
                        "db-candidate-"
                        + hashlib.sha256(
                            canonical_json_bytes(
                                {
                                    "canonical_structure_id": item.source_record.canonical_structure_id,
                                    "source": item.source_record.source_database,
                                    "material_id": item.source_record.source_material_id,
                                }
                            )
                        ).hexdigest()[:24]
                    )
                self._candidates[match_id] = self._candidate_from_item(match_id, item)
                self._candidate_structures[match_id] = item.structure
            else:
                existing = self._candidates[match_id]
                records = tuple(
                    sorted(
                        (*existing.source_records, item.source_record),
                        key=lambda record: (
                            FEDERATED_SOURCE_ORDER.index(
                                SourceDatabase(record.source_database)
                            ),
                            record.source_material_id,
                        ),
                    )
                )
                self._candidates[match_id] = existing.model_copy(
                    update={"source_records": records}
                )
                self._merge_count += 1
            self._source_record_keys.add(source_key)
            self._source_record_count += 1
            touched.add(match_id)
        return touched

    def _matching_candidate(self, item: _NormalizedSourceRecord) -> str | None:
        for candidate_id in sorted(self._candidates):
            candidate = self._candidates[candidate_id]
            if candidate.formula != item.formula:
                continue
            if any(
                record.canonical_structure_id
                == item.source_record.canonical_structure_id
                for record in candidate.source_records
            ):
                return candidate_id
            if self._matcher.fit(
                self._candidate_structures[candidate_id], item.structure
            ):
                return candidate_id
        return None

    @staticmethod
    def _candidate_from_item(
        candidate_id: str, item: _NormalizedSourceRecord
    ) -> DatabaseCandidateV1:
        source = item.source_record
        return DatabaseCandidateV1(
            database_candidate_id=candidate_id,
            source_database=source.source_database,
            source_material_id=source.source_material_id,
            canonical_structure_id=source.canonical_structure_id,
            source_records=(source,),
            formula=item.formula,
            elements=item.elements,
            transition_metals=item.transition_metals,
            band_gap_ev=source.band_gap_ev,
            dimensionality=item.dimensionality,
            dimensionality_status=(
                "RESOLVED" if item.dimensionality is not None else "UNKNOWN"
            ),
            connected_transition_metal_sublattice_proxy=item.connectivity,
            connectivity_status=(
                "RESOLVED_PROXY" if item.connectivity is not None else "UNKNOWN"
            ),
            structure_artifact_uri=source.structure_artifact_uri,
            structure_artifact_sha256=source.structure_artifact_sha256,
            raw_response_artifact_uri=source.raw_response_artifact_uri,
            raw_response_artifact_sha256=source.raw_response_artifact_sha256,
        )
