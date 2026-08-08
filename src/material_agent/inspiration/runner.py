"""Bounded orchestration for the inspiration companion.

The runner is intentionally a thin composition layer.  Search, extraction,
passage selection, vectorization, evidence closure, bridge construction,
run-internal identity, and diverse selection remain in their pure modules.
Structure generation is injected behind :class:`TransformationEngine` so the
same artifact contract can be exercised by byte-stable offline fixtures and by
explicitly enabled public metadata APIs.  Neither mode exposes a PDF or
full-document fetch path.
"""

from __future__ import annotations

import hashlib
import json
import time
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

from material_agent.inspiration.bridge import build_search_supported_bridges
from material_agent.inspiration.evidence import build_evidence_cards
from material_agent.inspiration.extractors import (
    ExtractionDecision,
    ExtractionLimits,
    extract_crossref_metadata,
    extract_openalex_metadata,
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
from material_agent.inspiration.search import (
    DocumentHitGroup,
    ParsedSearchPage,
    RawSearchPage,
    SearchAdapter,
    SearchAdapterError,
    SearchAttemptRecord,
    group_document_hits,
    parse_crossref_page,
    parse_openalex_page,
)
from material_agent.inspiration.selection import select_diverse_candidates
from material_agent.inspiration.tag_graph import plan_tag_queries
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
    ) -> None:
        self.store = store
        self.search_adapter = search_adapter
        self.transformation_engine = transformation_engine
        self.vectorizer = vectorizer

    @property
    def execution_components(self) -> tuple[ComponentSnapshotV1, ...]:
        """Return the injected scientific implementation bound at approval."""

        return (self.transformation_engine.component,)

    def run(
        self,
        *,
        inspiration_input: InspirationInputV1,
        policy: InspirationPolicyV1,
        tag_graph: TagGraphV1,
        target_tag_ids: Sequence[str],
    ) -> InspirationRunResult:
        started_ns = time.monotonic_ns()
        self._validate_run_inputs(
            inspiration_input=inspiration_input,
            policy=policy,
            tag_graph=tag_graph,
        )
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

        query_plan = plan_tag_queries(
            tag_graph,
            target_tag_ids=tuple(target_tag_ids),
            budget=policy.search,
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
            remaining_hits = policy.search.max_raw_hits - len(hits)
            if remaining_hits <= 0:
                warnings.append("RAW_HIT_BUDGET_EXHAUSTED")
                break
            try:
                page = self.search_adapter.search(
                    query,
                    max_response_bytes=MAX_SEARCH_RESPONSE_BYTES,
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

        passages, fetch_manifest, extraction_warnings = self._extract_passages(
            policy=policy,
            graph=tag_graph,
            queries=tuple(executed_queries),
            hits=hit_tuple,
            groups=groups,
            raw_pages=raw_pages,
        )
        warnings.extend(extraction_warnings)
        fetch_pointer = self._write_jsonl(
            f"{prefix}/fetch_manifest.jsonl",
            fetch_manifest,
        )
        passage_pointer = self._write_jsonl(
            f"{prefix}/passages.jsonl",
            passages,
        )

        vectors, vector_artifacts, embedding_tokens = self._vectorize_passages(
            prefix=prefix,
            policy=policy,
            hits=hit_tuple,
            passages=passages,
        )
        vector_manifest_pointer = self._write_jsonl(
            f"{prefix}/passage_vectors.jsonl",
            vectors,
        )

        evidence_result = build_evidence_cards(
            graph=tag_graph,
            queries=tuple(executed_queries),
            hits=hit_tuple,
            passages=passages,
        )
        warnings.extend(evidence_result.warnings)
        evidence_pointer = self._write_jsonl(
            f"{prefix}/evidence_cards.jsonl",
            evidence_result.cards,
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
        )
        plans = tuple(draft.plan for draft in drafts)
        structure_artifacts = self._persist_transformation_structures(
            prefix=prefix,
            drafts=drafts,
        )
        transformation_pointer = self._write_jsonl(
            f"{prefix}/transformation_proposals.jsonl",
            plans,
        )

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
        selected_candidates = select_diverse_candidates(
            identities,
            policy=policy.selection,
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

        elapsed_ms = (time.monotonic_ns() - started_ns) // 1_000_000
        if elapsed_ms > policy.runtime.max_walltime_seconds * 1_000:
            raise InspirationRunnerError(
                "WALLTIME_BUDGET_EXCEEDED",
                "inspiration run exceeded its walltime budget",
            )
        # Offline artifacts are required to replay byte-for-byte, so all modes
        # share the same deterministic ledger representation. Wall-clock
        # duration is enforced above but intentionally not serialized.
        ledger = CostLedgerV1(
            search_requests=len(search_attempts),
            search_response_bytes=search_response_bytes,
            fetch_requests=0,
            fetch_response_bytes=0,
            raw_documents=len(hit_tuple),
            unique_documents=len(groups),
            extracted_passages=len(passages),
            vectorized_passages=len(vectors),
            embedding_input_tokens=embedding_tokens,
            llm_calls=0,
            llm_input_tokens=0,
            llm_output_tokens=0,
            generated_plans=len(plans),
            rejected_plans=sum(
                plan.status is TransformationStatus.REJECTED for plan in plans
            ),
            candidates_after_internal_dedup=len(identities),
            walltime_ms=0,
        )
        cost_pointer = self._write_json(f"{prefix}/cost_ledger.json", ledger)

        intermediate = _unique_pointers(
            (
                query_plan_pointer,
                attempt_pointer,
                *raw_pointers,
                hit_pointer,
                fetch_pointer,
                passage_pointer,
                *vector_artifacts,
                vector_manifest_pointer,
                evidence_pointer,
                tag_graph_pointer,
                bridge_pointer,
                *structure_artifacts,
                transformation_pointer,
                duplicate_pointer,
            )
        )
        outcome = (
            InspirationOutcome.SUCCEEDED
            if selected_candidates
            else InspirationOutcome.SCIENTIFIC_NO_MATCH
        )
        limitations = (
            "Search evidence is limited to bounded metadata passages and may omit relevant context.",
            "Generated structures have no downstream property validation; target property status is UNKNOWN.",
            "Artifacts record walltime_ms as zero while enforcing the configured walltime ceiling.",
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
        )
        report_pointer = self._write_text(f"{prefix}/report.md", report)
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
        if page.media_type != "application/json":
            raise InspirationRunnerError(
                "UNSUPPORTED_SEARCH_MEDIA_TYPE",
                "metadata search responses must be application/json",
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
            if page.provider != "crossref":
                raise InspirationRunnerError(
                    "UNSUPPORTED_PUBLIC_METADATA_PROVIDER",
                    "public metadata mode currently accepts only Crossref responses",
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

    @staticmethod
    def _parse_search_page(
        *,
        query: SearchQueryV1,
        page: RawSearchPage,
        raw_response_artifact: ArtifactPointerV1,
        max_hits: int,
    ) -> ParsedSearchPage:
        if page.provider == "crossref":
            return parse_crossref_page(
                query=query,
                payload=page.payload,
                raw_response_artifact=raw_response_artifact,
                max_hits=max_hits,
            )
        if page.provider == "openalex-fixture":
            return parse_openalex_page(
                query=query,
                payload=page.payload,
                raw_response_artifact=raw_response_artifact,
                provider=page.provider,
                max_hits=max_hits,
            )
        raise InspirationRunnerError(
            "UNSUPPORTED_SEARCH_PROVIDER",
            f"no bounded parser is registered for provider {page.provider!r}",
        )

    def _extract_passages(
        self,
        *,
        policy: InspirationPolicyV1,
        graph: TagGraphV1,
        queries: tuple[SearchQueryV1, ...],
        hits: tuple[SearchHitV1, ...],
        groups: tuple[DocumentHitGroup, ...],
        raw_pages: dict[str, RawSearchPage],
    ) -> tuple[tuple[PassageV1, ...], tuple[dict[str, object], ...], tuple[str, ...]]:
        query_index = {query.query_id: query for query in queries}
        hit_index = {hit.hit_id: hit for hit in hits}
        tag_index = {tag.tag_id: tag for tag in graph.tags}
        selected: list[PassageV1] = []
        selected_keys: set[tuple[str, str]] = set()
        manifest: list[dict[str, object]] = []
        warnings: list[str] = []
        config = selection_config_from_policy(policy.passages)
        limits = ExtractionLimits(
            max_input_bytes=MAX_SEARCH_RESPONSE_BYTES,
            max_drafts=64,
            min_abstract_tokens=policy.passages.min_tokens,
        )
        for group in groups:
            member_hits = tuple(hit_index[hit_id] for hit_id in group.member_hit_ids)
            hit = _select_document_processing_hit(
                member_hits,
                query_index=query_index,
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
                    limits=limits,
                    result_index=hit.provider_rank - 1,
                )
            elif (
                hit.provider == "openalex-fixture"
                and page.provider == "openalex-fixture"
            ):
                extraction = extract_openalex_metadata(
                    page.payload,
                    target_terms=target_terms,
                    limits=limits,
                    result_index=hit.provider_rank - 1,
                )
            else:
                raise InspirationRunnerError(
                    "SEARCH_EXTRACTION_PROVIDER_MISMATCH",
                    "search hit and raw page do not share a bounded extractor",
                )
            passage_drafts = select_passage_drafts(
                extraction.drafts,
                hit_id=hit.hit_id,
                document_id=group.document_id,
                query_terms=target_terms,
                tag_terms=tag_terms,
                config=config,
                document_title=extraction.title or hit.title,
            )
            accepted_for_hit = 0
            deduplicated_for_hit = 0
            for draft in passage_drafts:
                passage_key = (group.document_id, draft.normalized_text_sha256)
                if passage_key in selected_keys:
                    deduplicated_for_hit += 1
                    continue
                if len(selected) >= policy.passages.max_total:
                    warnings.append("PASSAGE_BUDGET_EXHAUSTED")
                    break
                selected_keys.add(passage_key)
                selected.append(
                    PassageV1(
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
                        source_artifact=hit.raw_response_artifact,
                        normalizer=draft.normalizer,
                        locator=draft.locator,
                        text=draft.text,
                        char_count=len(draft.text),
                        estimated_token_count=draft.estimated_token_count,
                        normalized_text_sha256=draft.normalized_text_sha256,
                        matched_tag_ids=draft.matched_tag_ids,
                        lexical_score=draft.lexical_score,
                    )
                )
                accepted_for_hit += 1
            fetched = False
            manifest.append(
                {
                    "decision": extraction.decision.value,
                    "deduplicated_passage_count": deduplicated_for_hit,
                    "document_id": group.document_id,
                    "fetched": fetched,
                    "hit_id": hit.hit_id,
                    "member_hit_ids": group.member_hit_ids,
                    "media_type": extraction.media_type,
                    "query_ids": member_query_ids,
                    "raw_response_uri": hit.raw_response_artifact.uri,
                    "representative_hit_id": hit.hit_id,
                    "selected_passage_count": accepted_for_hit,
                    "warnings": extraction.warnings,
                }
            )
            warnings.extend(extraction.warnings)
            if extraction.decision is ExtractionDecision.FETCH_BODY_METADATA_INSUFFICIENT:
                warnings.append(f"BODY_NOT_FETCHED:{hit.hit_id}")
        return tuple(selected), tuple(manifest), tuple(warnings)

    def _vectorize_passages(
        self,
        *,
        prefix: str,
        policy: InspirationPolicyV1,
        hits: tuple[SearchHitV1, ...],
        passages: tuple[PassageV1, ...],
    ) -> tuple[tuple[PassageVectorV1, ...], tuple[ArtifactPointerV1, ...], int]:
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
        records: list[PassageVectorV1] = []
        pointers: list[ArtifactPointerV1] = []
        for item in generated:
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
        return (
            tuple(records),
            tuple(pointers),
            sum(item.input_token_count for item in generated),
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
    ) -> tuple[TransformationDraft, ...]:
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
    ) -> tuple[ArtifactPointerV1, ...]:
        pointers: list[ArtifactPointerV1] = []
        for draft in drafts:
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
                    kind_priority,
                    lineage_key,
                    hit.provider_rank,
                    hit.provider,
                    hit.hit_id,
                ),
                hit,
            )
        )
    return min(ranked, key=lambda item: item[0])[1]


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
