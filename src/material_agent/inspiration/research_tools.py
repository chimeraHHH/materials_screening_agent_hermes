"""Audited search tools for the generic materials research graph."""

from __future__ import annotations

import hashlib
import threading
from collections.abc import Mapping
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from typing import Any

from pydantic import Field
from pymatgen.analysis.structure_matcher import SpeciesComparator, StructureMatcher
from pymatgen.core import Composition, Element, Structure

from material_agent.inspiration.deepseek_agent import DeepSeekFunctionTool
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
from material_agent.inspiration.research_graph import (
    DatabaseCandidateV1,
    DatabaseFederationAuditV1,
    DatabaseSourceQueryReceiptV1,
    DatabaseSourceRecordV1,
    ResolvedEvidenceV1,
)
from material_agent.inspiration.search import (
    SearchAdapter,
    SearchAdapterError,
    parse_arxiv_page,
    parse_crossref_page,
    parse_multi_source_page,
    parse_openalex_page,
    parse_osti_page,
)
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
        self.adapter = adapter
        self.store = store
        self.run_id = run_id
        self.publication_year_from = publication_year_from
        self.publication_year_to = publication_year_to
        self.max_calls = max_calls
        self.max_physical_requests = max_physical_requests
        self.max_response_bytes = max_response_bytes
        self._calls = 0
        self._physical_requests = 0
        self._evidence: dict[str, ResolvedEvidenceV1] = {}
        self._lock = threading.Lock()

    def as_tool(self) -> DeepSeekFunctionTool:
        return DeepSeekFunctionTool(
            name="authoritative_literature_search",
            description=(
                "Search accepted scholarly metadata providers. Exact raw bytes are persisted "
                "before parsing; returned metadata/abstract evidence cannot establish band properties."
            ),
            arguments_model=AuthoritativeLiteratureSearchArgsV1,
            handler=self._handle,
        )

    def snapshot(self) -> tuple[ResolvedEvidenceV1, ...]:
        with self._lock:
            return tuple(self._evidence.values())

    def restore_snapshot(self, values: object) -> None:
        if not isinstance(values, list):
            raise TypeError("evidence checkpoint snapshot must be a list")
        restored = [ResolvedEvidenceV1.model_validate(item) for item in values]
        with self._lock:
            if self._calls or self._evidence:
                raise ValueError("evidence state must be empty before restore")
            self._evidence = {item.evidence_id: item for item in restored}

    def _handle(
        self, arguments: AuthoritativeLiteratureSearchArgsV1
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
        with self._lock:
            if self._calls >= self.max_calls:
                raise LLMProviderError(
                    "BUDGET_EXHAUSTED",
                    "authoritative-search tool exhausted its call budget",
                    retryable=False,
                )
            remaining_requests = self.max_physical_requests - self._physical_requests
            if remaining_requests <= 0:
                raise LLMProviderError(
                    "BUDGET_EXHAUSTED",
                    "authoritative-search tool exhausted its physical-request budget",
                    retryable=False,
                )
            self._calls += 1
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
        created: list[ResolvedEvidenceV1] = []
        for hit in parsed.hits:
            stable_id = hit.doi or hit.arxiv_id or hit.provider_record_id
            evidence_id = (
                "evidence-"
                + hashlib.sha256(
                    canonical_json_bytes(
                        {"provider": hit.provider, "stable_record_id": stable_id}
                    )
                ).hexdigest()[:24]
            )
            record = ResolvedEvidenceV1(
                evidence_id=evidence_id,
                provider=hit.provider,
                stable_record_id=stable_id,
                title=hit.title,
                published_year=hit.published_year,
                doi=hit.doi,
                arxiv_id=hit.arxiv_id,
                canonical_url=hit.canonical_url,
                abstract_excerpt=(hit.abstract[:2_000] if hit.abstract else None),
                raw_response_uri=pointer.uri,
                raw_response_sha256=pointer.sha256,
                supported_constraint_ids=arguments.target_constraint_ids,
            )
            with self._lock:
                self._evidence.setdefault(record.evidence_id, record)
            created.append(record)
        return {
            "query_id": query_id,
            "records": [item.model_dump(mode="json") for item in created],
            "warnings": parsed.warnings,
            "evidence_scope": "METADATA_OR_ABSTRACT_ONLY",
            "raw_response": pointer.model_dump(mode="json"),
        }

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
