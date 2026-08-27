from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
from pathlib import Path
from typing import Any, TypeVar

import pytest
from pydantic import ValidationError

from material_agent.inspiration.models import (
    StrictModel,
    canonical_sha256,
    deterministic_id,
)
from material_agent.research.flatband_cases import (
    assert_pre_run_eligibility_precedes_execution_v3,
    build_pilot_pre_budget_closure_release_v3,
    build_pre_run_eligibility_release_v3,
)
from material_agent.research.flatband_contracts import (
    AssertedEvidenceRelation,
    BenchmarkSplit,
    BenchmarkSplitManifestV1,
    BenchmarkSplitManifestV2,
    Dimensionality,
    EvidenceSpanRefV1,
    FalsificationPlanV1,
    HypothesisPacketV1,
    MechanismFamily,
    OodHoldoutAxis,
    OodHoldoutFamilyV1,
    SplitCaseRefV1,
    SplitCaseRefV2,
    SplitManifestKind,
    TargetBandClass,
)
from material_agent.research.flatband_execution import (
    ActualSourceUsageV1,
    BudgetManifestV1,
    BudgetManifestV2,
    CacheDisposition,
    CacheInventoryEntryV1,
    EvidenceLinkReceiptV1,
    ExecutionMatrixV1,
    ExecutionMatrixV2,
    ExecutionPhase,
    ExecutionReleaseV3,
    LlmExecutionIdentityV1,
    LlmInvocationReceiptV1,
    LocalModelExecutionIdentityV1,
    LocalModelInvocationReceiptV1,
    LogicalPageReceiptV1,
    LogicalQueryReceiptV1,
    MetadataModelInputRefV1,
    MetadataRecordReceiptV1,
    MetadataSpanPreimageV1,
    MissingPositionReason,
    NormalizedMetadataArtifactV1,
    NormalizedMetadataFieldV1,
    PhysicalHopReceiptV1,
    RankingPositionV1,
    ResearchRankingV1,
    ResearchSystemId,
    RunCellStatus,
    SourceBudgetV1,
    SourceReceiptBundleV1,
    SourceVariant,
    SystemConfigV1,
    TerminalRunResultV1,
    assemble_execution_release,
    assemble_execution_release_v2,
    assemble_execution_release_v3,
    build_budget_manifest_v2,
    build_execution_matrix,
    build_execution_matrix_v2,
    replay_llm_usage,
    replay_local_model_usage,
    replay_source_usage,
    source_policy_values,
)

ModelT = TypeVar("ModelT", bound=StrictModel)
SHA_A = "a" * 64
SHA_B = "b" * 64
SHA_C = "c" * 64
SHA_D = "d" * 64


def _span_text(span_id: str) -> str:
    return f"Exact bounded metadata evidence for {span_id}."


def _utf8_sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _span_locator(receipt: EvidenceLinkReceiptV1 | MetadataSpanPreimageV1) -> str:
    return canonical_sha256(
        {
            "record_receipt_id": receipt.record_receipt_id,
            "record_receipt_sha256": receipt.record_receipt_sha256,
            "artifact_sha256": receipt.normalized_metadata_artifact_sha256,
            "field_artifact_id": receipt.field_artifact_id,
            "field_artifact_sha256": receipt.field_artifact_sha256,
            "field_name": (
                receipt.span_field
                if isinstance(receipt, EvidenceLinkReceiptV1)
                else receipt.field_name
            ),
            "json_path": (
                receipt.metadata_json_path
                if isinstance(receipt, EvidenceLinkReceiptV1)
                else receipt.json_path
            ),
            "start_byte": (
                receipt.span_start_byte
                if isinstance(receipt, EvidenceLinkReceiptV1)
                else receipt.start_byte
            ),
            "end_byte": (
                receipt.span_end_byte
                if isinstance(receipt, EvidenceLinkReceiptV1)
                else receipt.end_byte
            ),
            "span_utf8": receipt.span_utf8,
            "span_utf8_sha256": receipt.span_utf8_sha256,
        }
    )


def _identified(
    model_type: type[ModelT],
    *,
    id_field: str,
    sha_field: str,
    prefix: str,
    values: dict[str, Any],
) -> ModelT:
    draft = model_type.model_construct(**values)
    digest = canonical_sha256(
        draft.model_dump(mode="python", exclude={id_field, sha_field})
    )
    return model_type.model_validate(
        {
            **values,
            sha_field: digest,
            id_field: deterministic_id(prefix, {sha_field: digest}),
        }
    )


def _reidentified(
    model: ModelT,
    *,
    id_field: str,
    sha_field: str,
    prefix: str,
    **changes: Any,
) -> ModelT:
    values = {
        field_name: getattr(model, field_name)
        for field_name in type(model).model_fields
        if field_name not in {id_field, sha_field}
    }
    values.update(changes)
    return _identified(
        type(model),
        id_field=id_field,
        sha_field=sha_field,
        prefix=prefix,
        values=values,
    )


def _reidentified_evidence(
    model: EvidenceLinkReceiptV1, **changes: Any
) -> EvidenceLinkReceiptV1:
    """Readdress an evidence receipt after an adversarial semantic mutation."""

    values = {
        field_name: getattr(model, field_name)
        for field_name in type(model).model_fields
        if field_name not in {"evidence_receipt_id", "evidence_receipt_sha256"}
    }
    values.update(changes)
    draft = EvidenceLinkReceiptV1.model_construct(**values)
    values["span_locator_sha256"] = _span_locator(draft)
    return _identified(
        EvidenceLinkReceiptV1,
        id_field="evidence_receipt_id",
        sha_field="evidence_receipt_sha256",
        prefix="evidence-link-receipt",
        values=values,
    )


def _budgets(variant: SourceVariant) -> tuple[SourceBudgetV1, ...]:
    allocations = {
        SourceVariant.CROSSREF_ONLY: {"crossref": 8},
        SourceVariant.E2_A: {"arxiv": 2, "crossref": 3, "openalex": 3},
        SourceVariant.E2_B: {
            "arxiv": 2,
            "crossref": 2,
            "openaire": 2,
            "openalex": 2,
        },
    }[variant]
    information = {
        SourceVariant.CROSSREF_ONLY: {
            "crossref": (4, 4, 100, 100_000, 50, 2),
        },
        SourceVariant.E2_A: {
            "arxiv": (1, 1, 25, 25_000, 12, 0),
            "crossref": (1, 1, 37, 37_500, 19, 1),
            "openalex": (2, 2, 38, 37_500, 19, 1),
        },
        SourceVariant.E2_B: {
            "arxiv": (1, 1, 25, 25_000, 12, 0),
            "crossref": (1, 1, 25, 25_000, 12, 0),
            "openaire": (1, 1, 25, 25_000, 13, 1),
            "openalex": (1, 1, 25, 25_000, 13, 1),
        },
    }[variant]
    return tuple(
        SourceBudgetV1(
            source_id=source_id,
            **source_policy_values(source_id),
            max_physical_requests=physical,
            max_logical_queries=information[source_id][0],
            max_pages=information[source_id][1],
            max_records=information[source_id][2],
            max_response_bytes=information[source_id][3],
            max_unique_documents=information[source_id][4],
            max_cache_hits=information[source_id][5],
        )
        for source_id, physical in sorted(allocations.items())
    )


def _llm() -> LlmExecutionIdentityV1:
    return LlmExecutionIdentityV1(
        provider="provider",
        model="reasoner",
        revision="revision-20260809",
        prompt_sha256=SHA_A,
        tokenizer_sha256=SHA_B,
        output_schema_sha256=SHA_C,
        max_output_tokens=4_000,
    )


def _local_model() -> LocalModelExecutionIdentityV1:
    return LocalModelExecutionIdentityV1(
        bundle_sha256=SHA_A,
        tokenizer_sha256=SHA_B,
        model_card_sha256=SHA_C,
        license_manifest_sha256=SHA_D,
        vector_dimension=384,
    )


def _config(
    system_id: ResearchSystemId,
    **overrides: Any,
) -> SystemConfigV1:
    if system_id is ResearchSystemId.E2_A:
        variant = SourceVariant.E2_A
    elif system_id is ResearchSystemId.E2_B:
        variant = SourceVariant.E2_B
    else:
        variant = SourceVariant.CROSSREF_ONLY
    values: dict[str, Any] = {
        "system_id": system_id,
        "source_variant": variant,
        "source_budgets": _budgets(variant),
        "query_plan_sha256": SHA_A,
        "record_projection_sha256": SHA_B,
        "ranking_policy_sha256": SHA_C,
        "cache_policy_sha256": SHA_D,
        "cache_snapshot_sha256": canonical_sha256("empty-cache-v1"),
        "baseline_tag_graph_sha256": canonical_sha256("baseline-tags-v1"),
        "cross_domain_tag_graph_sha256": (
            canonical_sha256("cross-domain-tags-v1")
            if system_id is ResearchSystemId.E3
            else None
        ),
        "llm": _llm() if system_id is ResearchSystemId.E1 else None,
        "local_semantic_model": (
            _local_model()
            if system_id is ResearchSystemId.E1_LOCAL
            else None
        ),
        "fusion_components": (),
    }
    if system_id is ResearchSystemId.FUSION:
        components = (
            ResearchSystemId.E1,
            ResearchSystemId.E2_A,
            ResearchSystemId.E3,
        )
        values.update(
            {
                "source_variant": SourceVariant.E2_A,
                "source_budgets": _budgets(SourceVariant.E2_A),
                "cross_domain_tag_graph_sha256": canonical_sha256(
                    "cross-domain-tags-v1"
                ),
                "llm": _llm(),
                "fusion_components": components,
            }
        )
    values.update(overrides)
    return _identified(
        SystemConfigV1,
        id_field="config_id",
        sha_field="config_sha256",
        prefix="system-config",
        values=values,
    )


def _pilot_manifest() -> BenchmarkSplitManifestV1:
    mechanisms = (
        MechanismFamily.LATTICE_INTERFERENCE,
        MechanismFamily.LINE_GRAPH,
        MechanismFamily.ORBITAL_FRUSTRATION_HYBRIDIZATION,
        MechanismFamily.SYMMETRY_INDUCED,
        MechanismFamily.CONFINEMENT,
    )
    cases = tuple(
        SplitCaseRefV1(
            case_id=f"case-{index:02d}",
            case_sha256=canonical_sha256(("case", index)),
            split=BenchmarkSplit.PILOT_R1,
            target_class=(
                TargetBandClass.FB100 if index < 15 else TargetBandClass.NB300
            ),
            dimensionality=(
                Dimensionality.TWO_D if index % 2 == 0 else Dimensionality.THREE_D
            ),
            primary_mechanism_stratum=mechanisms[index // 6],
            leakage_group_ids=(f"component-{index:02d}",),
        )
        for index in range(30)
    )
    return _identified(
        BenchmarkSplitManifestV1,
        id_field="manifest_id",
        sha_field="manifest_sha256",
        prefix="split-manifest",
        values={
            "manifest_kind": SplitManifestKind.PILOT_R1,
            "split_seed": 20260809,
            "cases": cases,
        },
    )


def _pilot_manifest_v2() -> BenchmarkSplitManifestV2:
    legacy = _pilot_manifest()
    cases = tuple(
        SplitCaseRefV2(
            case_id=case.case_id,
            case_sha256=case.case_sha256,
            split=case.split,
            target_class=case.target_class,
            dimensionality=case.dimensionality,
            primary_mechanism_stratum=case.primary_mechanism_stratum,
            independence_group_ids=case.leakage_group_ids,
            holdout_memberships=(),
        )
        for case in legacy.cases
    )
    return _identified(
        BenchmarkSplitManifestV2,
        id_field="manifest_id",
        sha_field="manifest_sha256",
        prefix="split-manifest-v2",
        values={
            "manifest_kind": SplitManifestKind.PILOT_R1,
            "split_seed": legacy.split_seed,
            "cases": cases,
        },
    )


def _main_manifest() -> BenchmarkSplitManifestV1:
    cases: list[SplitCaseRefV1] = []
    holdouts: list[str] = []
    for index in range(120):
        if index < 60:
            split = BenchmarkSplit.DEVELOPMENT
            group_id = f"development-component-{index:03d}"
        elif index < 90:
            split = BenchmarkSplit.LOCKED_IID
            group_id = f"iid-component-{index:03d}"
        else:
            split = BenchmarkSplit.LOCKED_OOD
            group_id = f"ood-holdout-{index:03d}"
            holdouts.append(group_id)
        cases.append(
            SplitCaseRefV1(
                case_id=f"main-case-{index:03d}",
                case_sha256=canonical_sha256(("main-case", index)),
                split=split,
                target_class=(
                    TargetBandClass.FB100
                    if index % 2 == 0
                    else TargetBandClass.NB300
                ),
                dimensionality=(
                    Dimensionality.TWO_D
                    if index % 2 == 0
                    else Dimensionality.THREE_D
                ),
                primary_mechanism_stratum=MechanismFamily.LATTICE_INTERFERENCE,
                leakage_group_ids=(group_id,),
            )
        )
    return _identified(
        BenchmarkSplitManifestV1,
        id_field="manifest_id",
        sha_field="manifest_sha256",
        prefix="split-manifest",
        values={
            "manifest_kind": SplitManifestKind.MAIN_120,
            "split_seed": 20260810,
            "cases": tuple(cases),
            "ood_holdout_families": tuple(
                OodHoldoutFamilyV1(
                    axis=OodHoldoutAxis.MECHANISM_FAMILY,
                    group_id=group_id,
                )
                for group_id in holdouts
            ),
        },
    )


def _pilot_configs() -> tuple[SystemConfigV1, ...]:
    return tuple(
        sorted(
            (
                _config(ResearchSystemId.B0),
                _config(ResearchSystemId.E1),
                _config(ResearchSystemId.E2_B),
                _config(ResearchSystemId.E3),
            ),
            key=lambda item: item.system_id.value,
        )
    )


def _budget(
    matrix: ExecutionMatrixV1 | ExecutionMatrixV2, index: int
) -> BudgetManifestV1:
    cell = matrix.cells[index]
    config = next(
        item for item in matrix.system_configs if item.config_id == cell.system_config_id
    )
    return _identified(
        BudgetManifestV1,
        id_field="budget_manifest_id",
        sha_field="budget_manifest_sha256",
        prefix="budget-manifest",
        values={
            "execution_matrix_id": matrix.matrix_id,
            "execution_matrix_sha256": matrix.matrix_sha256,
            "cell_id": cell.cell_id,
            "cell_sha256": cell.cell_sha256,
            "run_id": f"run-{index:03d}",
            "case_id": cell.case_id,
            "case_sha256": cell.case_sha256,
            "system_config": config,
            "git_commit": "1" * 40,
            "runtime_environment_sha256": SHA_C,
            "analysis_environment_sha256": SHA_D,
            "max_walltime_seconds": 300,
            "frozen_at": "2026-08-09T12:00:00+08:00",
        },
    )


def _ranking(
    budget: BudgetManifestV1,
    *,
    count: int,
    created_at: str = "2026-08-09T12:01:00+08:00",
) -> ResearchRankingV1:
    packets = tuple(
        _packet(
            case_id=budget.case_id,
            case_sha256=budget.case_sha256,
            run_id=budget.run_id,
            rank=rank,
        )
        for rank in range(1, count + 1)
    )
    positions = tuple(
        RankingPositionV1(
            selection_rank=rank,
            packet_id=packet.packet_id,
            packet_sha256=packet.packet_sha256,
        )
        for rank, packet in enumerate(packets, start=1)
    )
    return _identified(
        ResearchRankingV1,
        id_field="ranking_id",
        sha_field="ranking_sha256",
        prefix="research-ranking",
        values={
            "budget_manifest_id": budget.budget_manifest_id,
            "budget_manifest_sha256": budget.budget_manifest_sha256,
            "cell_id": budget.cell_id,
            "run_id": budget.run_id,
            "case_id": budget.case_id,
            "case_sha256": budget.case_sha256,
            "system_config_id": budget.system_config.config_id,
            "system_config_sha256": budget.system_config.config_sha256,
            "positions": positions,
            "underfill_reason_codes": () if count == 5 else ("UNDERFILLED",),
            "created_at": created_at,
        },
    )


def _packet(
    *, case_id: str, case_sha256: str, run_id: str, rank: int
) -> HypothesisPacketV1:
    link_id = f"evidence-{run_id}-{rank}"
    values = {
        "case_id": case_id,
        "case_sha256": case_sha256,
        "candidate_structure_sha256": canonical_sha256(
            (case_id, run_id, rank, "candidate")
        ),
        "strict_structure_group_id": f"structure-{run_id}-{rank}",
        "strict_hypothesis_group_id": f"hypothesis-{run_id}-{rank}",
        "transformation_operator_id": "SUBSTITUTE_EQUIVALENT_SITE_V1",
        "transformation_summary": "A bounded substitution preserving connectivity.",
        "source_domain": "photonic lattices",
        "mechanism_family": MechanismFamily.LATTICE_INTERFERENCE,
        "source_mechanism": "Destructive interference forms a compact localized mode.",
        "shared_invariant": "Connectivity-preserving destructive interference.",
        "target_mapping": "Map the source connectivity onto the target orbital graph.",
        "transferable_control": "Tune the symmetry-allowed hopping hierarchy.",
        "transfer_principle": "Preserve connectivity while changing orbital weight.",
        "required_conditions": ("dominant local hopping",),
        "breaking_conditions": ("large symmetry-breaking hopping",),
        "evidence_links": (
            EvidenceSpanRefV1(
                evidence_link_id=link_id,
                source_id="crossref",
                source_record_id=f"record-{run_id}-{rank}",
                source_url="https://doi.org/10.1000/example",
                span_id=f"span-{run_id}-{rank}",
                span_sha256=_utf8_sha256(_span_text(f"span-{run_id}-{rank}")),
                asserted_relation=AssertedEvidenceRelation.SUPPORT,
                claim_summary="The source reports a localized interference mode.",
                private_text_artifact_uri=None,
            ),
        ),
        "contradictions": (),
        "falsification": FalsificationPlanV1(
            observable="Tracked-band width",
            method="Compute the frozen target band structure.",
            pass_condition="The tracked band meets the frozen width threshold.",
            fail_condition="The tracked band exceeds the frozen width threshold.",
        ),
    }
    return _identified(
        HypothesisPacketV1,
        id_field="packet_id",
        sha_field="packet_sha256",
        prefix="hypothesis-packet",
        values=values,
    )


def _packet_artifacts(
    rankings: tuple[ResearchRankingV1, ...],
) -> tuple[HypothesisPacketV1, ...]:
    packets = {
        position.packet_id: _packet(
            case_id=ranking.case_id,
            case_sha256=ranking.case_sha256,
            run_id=ranking.run_id,
            rank=position.selection_rank,
        )
        for ranking in rankings
        for position in ranking.positions
    }
    assert all(
        packets[position.packet_id].packet_sha256 == position.packet_sha256
        for ranking in rankings
        for position in ranking.positions
    )
    return tuple(sorted(packets.values(), key=lambda item: item.packet_id))


def _receipt_bundles(
    budget: BudgetManifestV1,
) -> tuple[SourceReceiptBundleV1, ...]:
    return tuple(
        _identified(
            SourceReceiptBundleV1,
            id_field="receipt_bundle_id",
            sha_field="receipt_bundle_sha256",
            prefix="source-receipt-bundle",
            values={
                "source_id": item.source_id,
                "retrieval_identity_sha256": item.retrieval_identity_sha256,
                "cache_snapshot_sha256": budget.system_config.cache_snapshot_sha256,
                "budget_manifest_id": budget.budget_manifest_id,
                "budget_manifest_sha256": budget.budget_manifest_sha256,
                "cell_id": budget.cell_id,
                "run_id": budget.run_id,
                "system_config_id": budget.system_config.config_id,
                "system_config_sha256": budget.system_config.config_sha256,
                "cache_inventory_entries": (),
                "logical_queries": (),
                "logical_pages": (),
                "metadata_records": (),
                "evidence_links": (),
                "physical_hops": (),
            },
        )
        for item in budget.system_config.source_budgets
    )


def _source_receipt_bundle(
    budget: BudgetManifestV1,
    *,
    source_id: str | None = None,
    physical_requests: int = 0,
    logical_queries: int | None = None,
    pages: int = 0,
    records: int = 0,
    response_bytes: int = 0,
    unique_documents: int = 0,
    cache_hits: int = 0,
    request_host: str | None = None,
    retrieval_identity_sha256: str | None = None,
    cache_snapshot_sha256: str | None = None,
    query_plan_sha256: str | None = None,
    record_projection_sha256: str | None = None,
    request_path_class: str | None = None,
    request_path_template_sha256: str | None = None,
    source_catalog_row_sha256: str | None = None,
    source_field_projection_sha256: str | None = None,
    query_created_at: str = "2026-08-09T12:00:00+08:00",
    hop_completed_at: str = "2026-08-09T12:00:01+08:00",
    page_completed_at: str = "2026-08-09T12:00:02+08:00",
    hypothesis_packets: tuple[HypothesisPacketV1, ...] = (),
) -> SourceReceiptBundleV1:
    """Build content-addressed receipt fixtures whose counters must be replayed."""

    source = next(
        item
        for item in budget.system_config.source_budgets
        if source_id is None or item.source_id == source_id
    )
    receipt_binding = {
        "budget_manifest_id": budget.budget_manifest_id,
        "budget_manifest_sha256": budget.budget_manifest_sha256,
        "cell_id": budget.cell_id,
        "run_id": budget.run_id,
        "system_config_id": budget.system_config.config_id,
        "system_config_sha256": budget.system_config.config_sha256,
    }
    packet_links = tuple(
        (packet, link)
        for packet in hypothesis_packets
        for link in packet.evidence_links
        if link.source_id == source.source_id
    )
    evidence_record_keys = tuple(
        sorted(
            {
                (link.source_record_id, link.source_url)
                for _packet_item, link in packet_links
            }
        )
    )
    assert len({item[0] for item in evidence_record_keys}) == len(
        evidence_record_keys
    )
    assert records >= len(evidence_record_keys)
    query_count = pages if logical_queries is None else logical_queries
    if pages == 0:
        assert query_count == cache_hits == physical_requests == records == 0
    else:
        assert 1 <= query_count <= pages
    assert 0 <= cache_hits <= pages
    assert 0 <= unique_documents <= records
    network_page_count = pages - cache_hits
    assert physical_requests >= network_page_count
    if physical_requests:
        assert network_page_count

    host = request_host or source.allowed_request_hosts[0]
    query_plan = query_plan_sha256 or budget.system_config.query_plan_sha256
    projection = (
        record_projection_sha256
        or budget.system_config.record_projection_sha256
    )
    cache_snapshot = (
        cache_snapshot_sha256 or budget.system_config.cache_snapshot_sha256
    )
    path_class = request_path_class or source.request_path_class
    path_template = (
        request_path_template_sha256 or source.request_path_template_sha256
    )
    catalog_row = source_catalog_row_sha256 or source.source_catalog_row_sha256
    field_projection = (
        source_field_projection_sha256
        or source.source_field_projection_sha256
    )
    assignments = [(index, 1) for index in range(query_count)]
    assignments.extend(
        (0, index + 2) for index in range(pages - query_count)
    )
    cache_indexes = set(range(cache_hits))
    network_indexes = tuple(
        index for index in range(pages) if index not in cache_indexes
    )
    record_index = network_indexes[0] if network_indexes else (0 if pages else None)
    byte_index = record_index
    document_hashes = tuple(
        sorted(
            canonical_sha256((budget.run_id, source.source_id, "document", index))
            for index in range(unique_documents)
        )
    )

    cache_entries: list[CacheInventoryEntryV1] = []
    logical_queries: list[LogicalQueryReceiptV1] = []
    logical_pages: list[LogicalPageReceiptV1] = []
    queries_by_index: dict[int, LogicalQueryReceiptV1] = {}
    for query_index in range(query_count):
        normalized_query_sha256 = canonical_sha256(
            (budget.run_id, source.source_id, "normalized-query", query_index)
        )
        filter_sha256 = canonical_sha256(
            (budget.run_id, source.source_id, "filter", query_index)
        )
        sort_sha256 = canonical_sha256(
            (budget.run_id, source.source_id, "sort", query_index)
        )
        query_identity = canonical_sha256(
            {
                "source_id": source.source_id,
                "query_plan_sha256": query_plan,
                "source_catalog_row_sha256": catalog_row,
                "request_path_class": path_class,
                "source_field_projection_sha256": field_projection,
                "normalized_query_sha256": normalized_query_sha256,
                "filter_sha256": filter_sha256,
                "sort_sha256": sort_sha256,
            }
        )
        logical_query_id = deterministic_id(
            "logical-query",
            {
                "source_id": source.source_id,
                "query_plan_sha256": query_plan,
                "query_identity_sha256": query_identity,
            },
        )
        query = _identified(
            LogicalQueryReceiptV1,
            id_field="query_receipt_id",
            sha_field="query_receipt_sha256",
            prefix="logical-query-receipt",
            values={
                "source_id": source.source_id,
                **receipt_binding,
                "logical_query_id": logical_query_id,
                "query_plan_sha256": query_plan,
                "source_catalog_sha256": source.source_catalog_sha256,
                "source_catalog_row_sha256": catalog_row,
                "request_path_class": path_class,
                "request_path_template_sha256": path_template,
                "source_field_projection_sha256": field_projection,
                "normalized_query_sha256": normalized_query_sha256,
                "filter_sha256": filter_sha256,
                "sort_sha256": sort_sha256,
                "query_identity_sha256": query_identity,
                "created_at": query_created_at,
            },
        )
        queries_by_index[query_index] = query
        logical_queries.append(query)

    for index, (query_index, page_number) in enumerate(assignments):
        query = queries_by_index[query_index]
        query_identity = query.query_identity_sha256
        logical_query_id = query.logical_query_id
        path_parameters = canonical_sha256(
            (budget.run_id, source.source_id, "path-parameters", query_index)
        )
        request_path = canonical_sha256(
            {
                "request_path_template_sha256": (
                    path_template
                ),
                "path_parameters_sha256": path_parameters,
            }
        )
        cursor_sha256 = (
            None
            if page_number == 1
            else canonical_sha256(
                (budget.run_id, source.source_id, "cursor", query_index, page_number)
            )
        )
        request_query = canonical_sha256(
            {
                "query_identity_sha256": query_identity,
                "page_number": page_number,
                "cursor_sha256": cursor_sha256,
            }
        )
        request_identity = canonical_sha256(
            {
                "source_id": source.source_id,
                "request_method": "GET",
                "request_host": host,
                "request_path_sha256": request_path,
                "request_query_sha256": request_query,
                "data_class": "PUBLIC_BIBLIOGRAPHIC_METADATA",
            }
        )
        response_identity = canonical_sha256(
            (source.source_id, "response", query_index, page_number)
        )
        cache_key = canonical_sha256(
            (source.source_id, "cache-key", query_index, page_number)
        )
        page_records: tuple[str, ...] = ()
        page_documents: tuple[str, ...] = ()
        page_bytes = response_bytes if index == byte_index else 0
        is_cache = index in cache_indexes
        cache_entry: CacheInventoryEntryV1 | None = None
        if is_cache:
            cache_entry = _identified(
                CacheInventoryEntryV1,
                id_field="cache_entry_id",
                sha_field="cache_entry_sha256",
                prefix="cache-inventory-entry",
                values={
                    "source_id": source.source_id,
                    "cache_snapshot_sha256": cache_snapshot,
                    "cache_key_sha256": cache_key,
                    "request_method": "GET",
                    "request_host": host,
                    "request_path_class": path_class,
                    "request_path_template_sha256": (
                        path_template
                    ),
                    "path_parameters_sha256": path_parameters,
                    "request_path_sha256": request_path,
                    "request_query_sha256": request_query,
                    "data_class": "PUBLIC_BIBLIOGRAPHIC_METADATA",
                    "request_identity_sha256": request_identity,
                    "response_status_code": 200,
                    "response_media_type": "application/json",
                    "response_identity_sha256": response_identity,
                    "response_bytes": page_bytes,
                    "record_identity_sha256s": page_records,
                    "document_identity_sha256s": page_documents,
                },
            )
            cache_entries.append(cache_entry)
        logical_pages.append(
            _identified(
                LogicalPageReceiptV1,
                id_field="page_receipt_id",
                sha_field="page_receipt_sha256",
                prefix="logical-page-receipt",
                values={
                    "source_id": source.source_id,
                    **receipt_binding,
                    "logical_query_id": logical_query_id,
                    "query_receipt_id": query.query_receipt_id,
                    "query_receipt_sha256": query.query_receipt_sha256,
                    "query_plan_sha256": query_plan,
                    "query_identity_sha256": query_identity,
                    "page_number": page_number,
                    "cursor_sha256": cursor_sha256,
                    "request_method": "GET",
                    "request_host": host,
                    "request_path_class": path_class,
                    "request_path_template_sha256": (
                        path_template
                    ),
                    "path_parameters_sha256": path_parameters,
                    "request_path_sha256": request_path,
                    "request_query_sha256": request_query,
                    "data_class": "PUBLIC_BIBLIOGRAPHIC_METADATA",
                    "request_identity_sha256": request_identity,
                    "cache_disposition": (
                        CacheDisposition.CACHE_HIT
                        if is_cache
                        else CacheDisposition.NETWORK_FETCH
                    ),
                    "cache_key_sha256": cache_key,
                    "cache_entry_id": (
                        None if cache_entry is None else cache_entry.cache_entry_id
                    ),
                    "cache_entry_sha256": (
                        None if cache_entry is None else cache_entry.cache_entry_sha256
                    ),
                    "response_status_code": 200,
                    "response_media_type": "application/json",
                    "response_identity_sha256": response_identity,
                    "response_bytes": page_bytes,
                    "record_projection_sha256": projection,
                    "source_field_projection_sha256": field_projection,
                    "record_identity_sha256s": page_records,
                    "document_identity_sha256s": page_documents,
                    "completed_at": page_completed_at,
                },
            )
        )

    default_span_field = {
        "arxiv": "summary",
        "crossref": "abstract",
        "openaire": "description",
        "openalex": "abstract_inverted_index",
    }[source.source_id]
    metadata_records: list[MetadataRecordReceiptV1] = []
    metadata_artifacts: list[NormalizedMetadataArtifactV1] = []
    if record_index is not None:
        page = logical_pages[record_index]
        for index in range(records):
            if index < len(evidence_record_keys):
                source_record_id, source_url = evidence_record_keys[index]
            else:
                source_record_id = (
                    f"record-{budget.run_id}-{source.source_id}-generic-{index}"
                )
                source_url = (
                    f"https://example.invalid/{source.source_id}/{source_record_id}"
                )
            source_url_sha256 = canonical_sha256(source_url)
            record_span_texts = tuple(
                sorted(
                    {
                        _span_text(link.span_id)
                        for _packet_item, link in packet_links
                        if link.source_record_id == source_record_id
                    }
                )
            )
            field_values = {
                default_span_field: (
                    " | ".join(record_span_texts)
                    if record_span_texts
                    else f"Bounded normalized metadata for {source_record_id}."
                )
            }
            if source.source_id == "crossref":
                field_values["container_title"] = (
                    f"Unrelated journal container for {source_record_id}."
                )
            artifact_fields = tuple(
                _identified(
                    NormalizedMetadataFieldV1,
                    id_field="field_artifact_id",
                    sha_field="field_artifact_sha256",
                    prefix="metadata-field-artifact",
                    values={
                        "field_name": field_name,
                        "json_path": f"/{field_name}",
                        "value_utf8": value,
                        "value_utf8_sha256": _utf8_sha256(value),
                        "value_utf8_bytes": len(value.encode("utf-8")),
                    },
                )
                for field_name, value in sorted(field_values.items())
            )
            normalized_metadata_sha256 = canonical_sha256(
                tuple(
                    {
                        "field_name": item.field_name,
                        "json_path": item.json_path,
                        "value_utf8": item.value_utf8,
                        "value_utf8_sha256": item.value_utf8_sha256,
                    }
                    for item in artifact_fields
                )
            )
            artifact = _identified(
                NormalizedMetadataArtifactV1,
                id_field="artifact_id",
                sha_field="artifact_sha256",
                prefix="normalized-metadata-artifact",
                values={
                    "source_id": source.source_id,
                    **receipt_binding,
                    "source_record_id": source_record_id,
                    "source_url_sha256": source_url_sha256,
                    "source_field_projection_sha256": field_projection,
                    "normalized_metadata_sha256": normalized_metadata_sha256,
                    "fields": artifact_fields,
                },
            )
            metadata_artifacts.append(artifact)
            record_identity_sha256 = canonical_sha256(
                {
                    "source_id": source.source_id,
                    "source_record_id": source_record_id,
                    "source_url_sha256": source_url_sha256,
                    "normalized_metadata_sha256": normalized_metadata_sha256,
                    "source_field_projection_sha256": field_projection,
                }
            )
            metadata_records.append(
                _identified(
                    MetadataRecordReceiptV1,
                    id_field="record_receipt_id",
                    sha_field="record_receipt_sha256",
                    prefix="metadata-record-receipt",
                    values={
                        "source_id": source.source_id,
                        **receipt_binding,
                        "page_receipt_id": page.page_receipt_id,
                        "page_receipt_sha256": page.page_receipt_sha256,
                        "source_record_id": source_record_id,
                        "source_url_sha256": source_url_sha256,
                        "normalized_metadata_artifact_id": artifact.artifact_id,
                        "normalized_metadata_artifact_sha256": (
                            artifact.artifact_sha256
                        ),
                        "normalized_metadata_sha256": normalized_metadata_sha256,
                        "source_field_projection_sha256": field_projection,
                        "record_identity_sha256": record_identity_sha256,
                        "document_identity_sha256": (
                            document_hashes[index % unique_documents]
                            if unique_documents
                            else None
                        ),
                    },
                )
            )
        record_hashes = tuple(
            sorted(item.record_identity_sha256 for item in metadata_records)
        )
        page_documents = tuple(
            sorted(
                {
                    item.document_identity_sha256
                    for item in metadata_records
                    if item.document_identity_sha256 is not None
                }
            )
        )
        old_page = logical_pages[record_index]
        new_page = _reidentified(
            old_page,
            id_field="page_receipt_id",
            sha_field="page_receipt_sha256",
            prefix="logical-page-receipt",
            record_identity_sha256s=record_hashes,
            document_identity_sha256s=page_documents,
        )
        logical_pages[record_index] = new_page
        metadata_records = [
            _reidentified(
                item,
                id_field="record_receipt_id",
                sha_field="record_receipt_sha256",
                prefix="metadata-record-receipt",
                page_receipt_id=new_page.page_receipt_id,
                page_receipt_sha256=new_page.page_receipt_sha256,
            )
            for item in metadata_records
        ]
        for cache_index, entry in enumerate(cache_entries):
            if entry.request_identity_sha256 == new_page.request_identity_sha256:
                cache_entries[cache_index] = _reidentified(
                    entry,
                    id_field="cache_entry_id",
                    sha_field="cache_entry_sha256",
                    prefix="cache-inventory-entry",
                    record_identity_sha256s=record_hashes,
                    document_identity_sha256s=page_documents,
                )
                logical_pages[record_index] = _reidentified(
                    new_page,
                    id_field="page_receipt_id",
                    sha_field="page_receipt_sha256",
                    prefix="logical-page-receipt",
                    cache_entry_id=cache_entries[cache_index].cache_entry_id,
                    cache_entry_sha256=cache_entries[cache_index].cache_entry_sha256,
                )
                final_page = logical_pages[record_index]
                metadata_records = [
                    _reidentified(
                        item,
                        id_field="record_receipt_id",
                        sha_field="record_receipt_sha256",
                        prefix="metadata-record-receipt",
                        page_receipt_id=final_page.page_receipt_id,
                        page_receipt_sha256=final_page.page_receipt_sha256,
                    )
                    for item in metadata_records
                ]
                break

    evidence_links: list[EvidenceLinkReceiptV1] = []
    metadata_span_preimages: list[MetadataSpanPreimageV1] = []
    record_by_source_id = {
        item.source_record_id: item for item in metadata_records
    }
    artifact_by_source_id = {
        item.source_record_id: item for item in metadata_artifacts
    }
    preimage_by_key: dict[tuple[str, str, str], MetadataSpanPreimageV1] = {}
    for packet, link in packet_links:
        record = record_by_source_id[link.source_record_id]
        artifact = artifact_by_source_id[link.source_record_id]
        field = next(
            item for item in artifact.fields if item.field_name == default_span_field
        )
        span_utf8 = _span_text(link.span_id)
        span_bytes = span_utf8.encode("utf-8")
        start_byte = field.value_utf8.encode("utf-8").find(span_bytes)
        assert start_byte >= 0
        end_byte = start_byte + len(span_bytes)
        assert link.span_sha256 == _utf8_sha256(span_utf8)
        preimage_key = (record.record_receipt_id, link.span_id, link.span_sha256)
        preimage = preimage_by_key.get(preimage_key)
        if preimage is None:
            preimage = _identified(
                MetadataSpanPreimageV1,
                id_field="span_preimage_id",
                sha_field="span_preimage_sha256",
                prefix="metadata-span-preimage",
                values={
                    "source_id": source.source_id,
                    **receipt_binding,
                    "record_receipt_id": record.record_receipt_id,
                    "record_receipt_sha256": record.record_receipt_sha256,
                    "normalized_metadata_artifact_id": artifact.artifact_id,
                    "normalized_metadata_artifact_sha256": artifact.artifact_sha256,
                    "field_artifact_id": field.field_artifact_id,
                    "field_artifact_sha256": field.field_artifact_sha256,
                    "field_name": field.field_name,
                    "json_path": field.json_path,
                    "span_id": link.span_id,
                    "start_byte": start_byte,
                    "end_byte": end_byte,
                    "span_utf8": span_utf8,
                    "span_utf8_sha256": link.span_sha256,
                    "span_utf8_bytes": len(span_bytes),
                },
            )
            preimage_by_key[preimage_key] = preimage
            metadata_span_preimages.append(preimage)
        evidence_values = {
            "source_id": source.source_id,
            **receipt_binding,
            "record_receipt_id": record.record_receipt_id,
            "record_receipt_sha256": record.record_receipt_sha256,
            "normalized_metadata_artifact_id": artifact.artifact_id,
            "normalized_metadata_artifact_sha256": artifact.artifact_sha256,
            "field_artifact_id": field.field_artifact_id,
            "field_artifact_sha256": field.field_artifact_sha256,
            "packet_id": packet.packet_id,
            "packet_sha256": packet.packet_sha256,
            "evidence_link_id": link.evidence_link_id,
            "evidence_link_sha256": canonical_sha256(
                link.model_dump(mode="python")
            ),
            "source_record_id": link.source_record_id,
            "source_url_sha256": canonical_sha256(link.source_url),
            "span_id": link.span_id,
            "span_sha256": link.span_sha256,
            "span_field": field.field_name,
            "metadata_json_path": field.json_path,
            "span_start_byte": preimage.start_byte,
            "span_end_byte": preimage.end_byte,
            "span_utf8": preimage.span_utf8,
            "span_utf8_sha256": preimage.span_utf8_sha256,
            "span_utf8_bytes": preimage.span_utf8_bytes,
            "span_preimage_id": preimage.span_preimage_id,
            "span_preimage_sha256": preimage.span_preimage_sha256,
            "private_text_artifact_uri_sha256": (
                None
                if link.private_text_artifact_uri is None
                else canonical_sha256(link.private_text_artifact_uri)
            ),
        }
        evidence_draft = EvidenceLinkReceiptV1.model_construct(**evidence_values)
        evidence_links.append(
            _identified(
                EvidenceLinkReceiptV1,
                id_field="evidence_receipt_id",
                sha_field="evidence_receipt_sha256",
                prefix="evidence-link-receipt",
                values={
                    **evidence_values,
                    "span_locator_sha256": _span_locator(evidence_draft),
                },
            )
        )

    hops: list[PhysicalHopReceiptV1] = []
    extra_hops = physical_requests - network_page_count
    for network_offset, page_index in enumerate(network_indexes):
        page = logical_pages[page_index]
        hop_count = 1 + (extra_hops if network_offset == 0 else 0)
        request_metadata = tuple(
            (
                page.path_parameters_sha256
                if hop_index == 1
                else canonical_sha256(
                    (page.page_receipt_id, "redirect-path-parameters", hop_index)
                ),
                page.request_query_sha256
                if hop_index == 1
                else canonical_sha256(
                    (page.page_receipt_id, "redirect-query", hop_index)
                ),
            )
            for hop_index in range(1, hop_count + 1)
        )
        request_paths = tuple(
            canonical_sha256(
                {
                    "request_path_template_sha256": (
                        path_template
                    ),
                    "path_parameters_sha256": path_parameters,
                }
            )
            for path_parameters, _query_sha256 in request_metadata
        )
        request_hashes = tuple(
            canonical_sha256(
                {
                    "source_id": source.source_id,
                    "request_method": "GET",
                    "request_host": host,
                    "request_path_sha256": path_sha256,
                    "request_query_sha256": query_sha256,
                    "data_class": "PUBLIC_BIBLIOGRAPHIC_METADATA",
                }
            )
            for path_sha256, (_parameters, query_sha256) in zip(
                request_paths, request_metadata, strict=True
            )
        )
        for hop_offset, request_identity in enumerate(request_hashes, start=1):
            terminal_hop = hop_offset == hop_count
            path_parameters, request_query = request_metadata[hop_offset - 1]
            request_path = request_paths[hop_offset - 1]
            hops.append(
                _identified(
                    PhysicalHopReceiptV1,
                    id_field="hop_receipt_id",
                    sha_field="hop_receipt_sha256",
                    prefix="physical-hop-receipt",
                    values={
                        "source_id": source.source_id,
                        **receipt_binding,
                        "logical_page_receipt_id": page.page_receipt_id,
                        "logical_page_receipt_sha256": page.page_receipt_sha256,
                        "hop_index": hop_offset,
                        "request_method": "GET",
                        "request_host": host,
                        "request_path_class": path_class,
                        "request_path_template_sha256": (
                            path_template
                        ),
                        "path_parameters_sha256": path_parameters,
                        "request_path_sha256": request_path,
                        "request_query_sha256": request_query,
                        "data_class": "PUBLIC_BIBLIOGRAPHIC_METADATA",
                        "request_identity_sha256": request_identity,
                        "response_status_code": 200 if terminal_hop else 302,
                        "response_media_type": "application/json",
                        "response_identity_sha256": (
                            page.response_identity_sha256
                            if terminal_hop
                            else canonical_sha256(
                                (page.page_receipt_id, "redirect-response", hop_offset)
                            )
                        ),
                        "response_bytes": page.response_bytes if terminal_hop else 0,
                        "redirect_target_request_sha256": (
                            None if terminal_hop else request_hashes[hop_offset]
                        ),
                        "completed_at": hop_completed_at,
                    },
                )
            )
    return _identified(
        SourceReceiptBundleV1,
        id_field="receipt_bundle_id",
        sha_field="receipt_bundle_sha256",
        prefix="source-receipt-bundle",
        values={
            "source_id": source.source_id,
            "retrieval_identity_sha256": (
                retrieval_identity_sha256 or source.retrieval_identity_sha256
            ),
            "cache_snapshot_sha256": cache_snapshot,
            "budget_manifest_id": budget.budget_manifest_id,
            "budget_manifest_sha256": budget.budget_manifest_sha256,
            "cell_id": budget.cell_id,
            "run_id": budget.run_id,
            "system_config_id": budget.system_config.config_id,
            "system_config_sha256": budget.system_config.config_sha256,
            "cache_inventory_entries": tuple(
                sorted(
                    cache_entries,
                    key=lambda item: (item.cache_key_sha256, item.cache_entry_id),
                )
            ),
            "logical_queries": tuple(
                sorted(
                    logical_queries,
                    key=lambda item: (
                        item.logical_query_id,
                        item.query_receipt_id,
                    ),
                )
            ),
            "logical_pages": tuple(
                sorted(
                    logical_pages,
                    key=lambda item: (
                        item.logical_query_id,
                        item.page_number,
                        item.page_receipt_id,
                    ),
                )
            ),
            "metadata_records": tuple(
                sorted(
                    metadata_records,
                    key=lambda item: (
                        item.page_receipt_id,
                        item.source_record_id,
                        item.record_receipt_id,
                    ),
                )
            ),
            "normalized_metadata_artifacts": tuple(
                sorted(
                    metadata_artifacts,
                    key=lambda item: (item.source_record_id, item.artifact_id),
                )
            ),
            "metadata_span_preimages": tuple(
                sorted(
                    metadata_span_preimages,
                    key=lambda item: (
                        item.record_receipt_id,
                        item.field_artifact_id,
                        item.start_byte,
                        item.end_byte,
                        item.span_preimage_id,
                    ),
                )
            ),
            "evidence_links": tuple(
                sorted(
                    evidence_links,
                    key=lambda item: (
                        item.packet_id,
                        item.evidence_link_id,
                        item.evidence_receipt_id,
                    ),
                )
            ),
            "physical_hops": tuple(
                sorted(
                    hops,
                    key=lambda item: (
                        item.logical_page_receipt_id,
                        item.hop_index,
                        item.hop_receipt_id,
                    ),
                )
            ),
        },
    )


def _replace_source_bundle(
    budget: BudgetManifestV1,
    replacement: SourceReceiptBundleV1,
) -> tuple[SourceReceiptBundleV1, ...]:
    return tuple(
        sorted(
            (
                replacement
                if item.source_id == replacement.source_id
                else item
                for item in _receipt_bundles(budget)
            ),
            key=lambda item: item.source_id,
        )
    )


def _bind_source_bundle(
    budget: BudgetManifestV1, bundle: SourceReceiptBundleV1
) -> SourceReceiptBundleV1:
    return _reidentified(
        bundle,
        id_field="receipt_bundle_id",
        sha_field="receipt_bundle_sha256",
        prefix="source-receipt-bundle",
        budget_manifest_id=budget.budget_manifest_id,
        budget_manifest_sha256=budget.budget_manifest_sha256,
        cell_id=budget.cell_id,
        run_id=budget.run_id,
        system_config_id=budget.system_config.config_id,
        system_config_sha256=budget.system_config.config_sha256,
    )


def _packets_for_ranking(
    ranking: ResearchRankingV1,
) -> tuple[HypothesisPacketV1, ...]:
    return tuple(
        _packet(
            case_id=ranking.case_id,
            case_sha256=ranking.case_sha256,
            run_id=ranking.run_id,
            rank=position.selection_rank,
        )
        for position in ranking.positions
    )


def _with_packet_evidence(
    budget: BudgetManifestV1,
    ranking: ResearchRankingV1 | None,
    bundles: tuple[SourceReceiptBundleV1, ...],
) -> tuple[SourceReceiptBundleV1, ...]:
    bound = tuple(_bind_source_bundle(budget, item) for item in bundles)
    if ranking is None or not ranking.positions:
        return tuple(sorted(bound, key=lambda item: item.source_id))
    packets = _packets_for_ranking(ranking)
    source_ids = {link.source_id for packet in packets for link in packet.evidence_links}
    rebuilt: list[SourceReceiptBundleV1] = []
    for bundle in bound:
        if bundle.source_id not in source_ids:
            rebuilt.append(bundle)
            continue
        usage = replay_source_usage(bundle)
        links = tuple(
            link
            for packet in packets
            for link in packet.evidence_links
            if link.source_id == bundle.source_id
        )
        unique_records = len(
            {(item.source_record_id, item.source_url) for item in links}
        )
        host = (
            bundle.logical_pages[0].request_host
            if bundle.logical_pages
            else next(
                item.allowed_request_hosts[0]
                for item in budget.system_config.source_budgets
                if item.source_id == bundle.source_id
            )
        )
        query_plan = (
            bundle.logical_queries[0].query_plan_sha256
            if bundle.logical_queries
            else budget.system_config.query_plan_sha256
        )
        record_projection = (
            bundle.logical_pages[0].record_projection_sha256
            if bundle.logical_pages
            else budget.system_config.record_projection_sha256
        )
        rebuilt.append(
            _source_receipt_bundle(
                budget,
                source_id=bundle.source_id,
                physical_requests=usage.physical_requests + 1,
                logical_queries=usage.logical_queries + 1,
                pages=usage.pages + 1,
                records=usage.records + unique_records,
                response_bytes=usage.response_bytes,
                unique_documents=usage.unique_documents + unique_records,
                cache_hits=usage.cache_hits,
                request_host=host,
                retrieval_identity_sha256=bundle.retrieval_identity_sha256,
                cache_snapshot_sha256=bundle.cache_snapshot_sha256,
                query_plan_sha256=query_plan,
                record_projection_sha256=record_projection,
                hypothesis_packets=packets,
            )
        )
    return tuple(sorted(rebuilt, key=lambda item: item.source_id))


def _usage(
    budget: BudgetManifestV1,
    bundles: tuple[SourceReceiptBundleV1, ...] | None = None,
) -> tuple[ActualSourceUsageV1, ...]:
    return tuple(
        replay_source_usage(item)
        for item in (_receipt_bundles(budget) if bundles is None else bundles)
    )


def _terminal(
    budget: BudgetManifestV1,
    ranking: ResearchRankingV1 | None,
    *,
    status: RunCellStatus,
    completed_at: str = "2026-08-09T12:02:00+08:00",
    source_usage: tuple[ActualSourceUsageV1, ...] | None = None,
    source_receipt_bundles: tuple[SourceReceiptBundleV1, ...] | None = None,
    walltime_ms: int = 1_000,
) -> TerminalRunResultV1:
    bundles = (
        _receipt_bundles(budget)
        if source_receipt_bundles is None
        else source_receipt_bundles
    )
    bundles = _with_packet_evidence(budget, ranking, bundles)
    return _identified(
        TerminalRunResultV1,
        id_field="terminal_result_id",
        sha_field="terminal_result_sha256",
        prefix="terminal-result",
        values={
            "budget_manifest_id": budget.budget_manifest_id,
            "budget_manifest_sha256": budget.budget_manifest_sha256,
            "ranking_id": None if ranking is None else ranking.ranking_id,
            "ranking_sha256": None if ranking is None else ranking.ranking_sha256,
            "cell_id": budget.cell_id,
            "run_id": budget.run_id,
            "case_id": budget.case_id,
            "case_sha256": budget.case_sha256,
            "system_config_id": budget.system_config.config_id,
            "system_config_sha256": budget.system_config.config_sha256,
            "git_commit": budget.git_commit,
            "runtime_environment_sha256": budget.runtime_environment_sha256,
            "status": status,
            "source_receipt_bundles": bundles,
            "source_usage": (
                _usage(budget, bundles) if source_usage is None else source_usage
            ),
            "walltime_ms": walltime_ms,
            "failure_reason_codes": (
                () if status is RunCellStatus.SUCCEEDED else ("RUN_INCOMPLETE",)
            ),
            "completed_at": completed_at,
        },
    )


def _reterminal_with_bundles(
    terminal: TerminalRunResultV1,
    bundles: tuple[SourceReceiptBundleV1, ...],
    **changes: Any,
) -> TerminalRunResultV1:
    return _reidentified(
        terminal,
        id_field="terminal_result_id",
        sha_field="terminal_result_sha256",
        prefix="terminal-result",
        source_receipt_bundles=tuple(sorted(bundles, key=lambda item: item.source_id)),
        source_usage=tuple(
            replay_source_usage(item)
            for item in sorted(bundles, key=lambda item: item.source_id)
        ),
        **changes,
    )


def _first_model_input(terminal: TerminalRunResultV1) -> MetadataModelInputRefV1:
    bundle = next(
        item for item in terminal.source_receipt_bundles if item.metadata_records
    )
    record = bundle.metadata_records[0]
    packet_sha256 = canonical_sha256(
        {
            "source_id": bundle.source_id,
            "receipt_bundle_id": bundle.receipt_bundle_id,
            "receipt_bundle_sha256": bundle.receipt_bundle_sha256,
            "record_receipt_id": record.record_receipt_id,
            "record_receipt_sha256": record.record_receipt_sha256,
            "record_identity_sha256": record.record_identity_sha256,
        }
    )
    return MetadataModelInputRefV1(
        source_id=bundle.source_id,
        metadata_packet_id=deterministic_id(
            "model-metadata-packet",
            {"metadata_packet_sha256": packet_sha256},
        ),
        metadata_packet_sha256=packet_sha256,
        receipt_bundle_id=bundle.receipt_bundle_id,
        receipt_bundle_sha256=bundle.receipt_bundle_sha256,
        record_receipt_id=record.record_receipt_id,
        record_receipt_sha256=record.record_receipt_sha256,
        record_identity_sha256=record.record_identity_sha256,
    )


def _llm_receipt(
    budget: BudgetManifestV1,
    terminal: TerminalRunResultV1,
    *,
    prompt_sha256: str | None = None,
    started_at: str = "2026-08-09T12:00:30+08:00",
    completed_at: str = "2026-08-09T12:00:31+08:00",
) -> LlmInvocationReceiptV1:
    llm = budget.system_config.llm or _llm()
    inputs = (_first_model_input(terminal),)
    prompt = prompt_sha256 or llm.prompt_sha256
    input_payload_sha256 = canonical_sha256(
        {
            "prompt_sha256": prompt,
            "output_schema_sha256": llm.output_schema_sha256,
            "metadata_inputs": tuple(
                item.model_dump(mode="python") for item in inputs
            ),
        }
    )
    return _identified(
        LlmInvocationReceiptV1,
        id_field="invocation_id",
        sha_field="invocation_sha256",
        prefix="llm-invocation-receipt",
        values={
            "budget_manifest_id": budget.budget_manifest_id,
            "budget_manifest_sha256": budget.budget_manifest_sha256,
            "cell_id": budget.cell_id,
            "run_id": budget.run_id,
            "system_config_id": budget.system_config.config_id,
            "system_config_sha256": budget.system_config.config_sha256,
            "call_index": 1,
            "provider": llm.provider,
            "model": llm.model,
            "revision": llm.revision,
            "prompt_sha256": prompt,
            "tokenizer_sha256": llm.tokenizer_sha256,
            "output_schema_sha256": llm.output_schema_sha256,
            "metadata_inputs": inputs,
            "input_payload_sha256": input_payload_sha256,
            "tokenized_input_sha256": canonical_sha256(
                (input_payload_sha256, llm.tokenizer_sha256, "tokens")
            ),
            "input_tokens": 80,
            "output_tokens": 20,
            "output_identity_sha256": canonical_sha256(
                (budget.run_id, "llm-output")
            ),
            "started_at": started_at,
            "completed_at": completed_at,
        },
    )


def _local_receipt(
    budget: BudgetManifestV1,
    terminal: TerminalRunResultV1,
) -> LocalModelInvocationReceiptV1:
    model = _local_model()
    inputs = (_first_model_input(terminal),)
    input_payload_sha256 = canonical_sha256(
        {
            "metadata_inputs": tuple(
                item.model_dump(mode="python") for item in inputs
            ),
            "bundle_sha256": model.bundle_sha256,
            "tokenizer_sha256": model.tokenizer_sha256,
        }
    )
    return _identified(
        LocalModelInvocationReceiptV1,
        id_field="invocation_id",
        sha_field="invocation_sha256",
        prefix="local-model-invocation-receipt",
        values={
            "budget_manifest_id": budget.budget_manifest_id,
            "budget_manifest_sha256": budget.budget_manifest_sha256,
            "cell_id": budget.cell_id,
            "run_id": budget.run_id,
            "system_config_id": budget.system_config.config_id,
            "system_config_sha256": budget.system_config.config_sha256,
            "invocation_index": 1,
            "bundle_sha256": model.bundle_sha256,
            "tokenizer_sha256": model.tokenizer_sha256,
            "model_card_sha256": model.model_card_sha256,
            "license_manifest_sha256": model.license_manifest_sha256,
            "vector_dimension": model.vector_dimension,
            "metadata_inputs": inputs,
            "input_payload_sha256": input_payload_sha256,
            "tokenized_input_sha256": canonical_sha256(
                (input_payload_sha256, model.tokenizer_sha256, "tokens")
            ),
            "input_tokens": 64,
            "output_vectors_sha256": canonical_sha256(
                (budget.run_id, "local-vectors")
            ),
            "started_at": "2026-08-09T12:00:20+08:00",
            "completed_at": "2026-08-09T12:00:21+08:00",
        },
    )


@pytest.fixture(scope="module")
def valid_objects() -> tuple[
    ExecutionMatrixV1,
    tuple[BudgetManifestV1, ...],
    tuple[ResearchRankingV1, ...],
    tuple[TerminalRunResultV1, ...],
]:
    matrix = build_execution_matrix(
        _pilot_manifest(), _pilot_configs(), phase=ExecutionPhase.PILOT_R1
    )
    budgets: list[BudgetManifestV1] = []
    rankings: list[ResearchRankingV1] = []
    terminals: list[TerminalRunResultV1] = []
    for index, _cell in enumerate(matrix.cells):
        budget = _budget(matrix, index)
        if index == 0:
            status, count = RunCellStatus.SUCCEEDED, 5
        elif index == 1:
            status, count = RunCellStatus.PARTIAL, 5
        elif index == 2:
            status, count = RunCellStatus.PARTIAL, 2
        elif index == 3:
            status, count = RunCellStatus.PARTIAL, 0
        else:
            status, count = RunCellStatus.FAILED, None
        ranking = None if count is None else _ranking(budget, count=count)
        budgets.append(budget)
        if ranking is not None:
            rankings.append(ranking)
        terminals.append(_terminal(budget, ranking, status=status))
    return matrix, tuple(budgets), tuple(rankings), tuple(terminals)


def _assemble(
    objects: tuple[
        ExecutionMatrixV1,
        tuple[BudgetManifestV1, ...],
        tuple[ResearchRankingV1, ...],
        tuple[TerminalRunResultV1, ...],
    ],
):
    return assemble_execution_release(
        *objects,
        hypothesis_packets=_packet_artifacts(objects[2]),
        assembled_at="2026-08-09T12:03:00+08:00",
    )


def test_phase_matrix_is_exact_pilot_case_by_literal_system() -> None:
    matrix = build_execution_matrix(
        _pilot_manifest(), _pilot_configs(), phase=ExecutionPhase.PILOT_R1
    )
    assert len(matrix.cells) == 30 * 4
    assert {item.system_id for item in matrix.cells} == {
        ResearchSystemId.B0,
        ResearchSystemId.E1,
        ResearchSystemId.E2_B,
        ResearchSystemId.E3,
    }
    assert all(item.split is BenchmarkSplit.PILOT_R1 for item in matrix.cells)


def test_formal_v2_matrix_uses_split_v2_without_broad_taxonomy_edges() -> None:
    manifest = _pilot_manifest_v2()
    matrix = build_execution_matrix_v2(
        manifest, _pilot_configs(), phase=ExecutionPhase.PILOT_R1
    )
    assert matrix.split_manifest == manifest
    assert len(matrix.cells) == 30 * 4
    assert all(
        case.independence_group_ids
        and not case.holdout_memberships
        for case in matrix.split_manifest.cases
    )


def test_formal_v2_release_exactly_closes_the_v2_matrix() -> None:
    matrix = build_execution_matrix_v2(
        _pilot_manifest_v2(), _pilot_configs(), phase=ExecutionPhase.PILOT_R1
    )
    budgets: list[BudgetManifestV1] = []
    rankings: list[ResearchRankingV1] = []
    terminals: list[TerminalRunResultV1] = []
    for index, _cell in enumerate(matrix.cells):
        budget = _budget(matrix, index)
        ranking = _ranking(budget, count=1) if index == 0 else None
        budgets.append(budget)
        if ranking is not None:
            rankings.append(ranking)
        terminals.append(
            _terminal(
                budget,
                ranking,
                status=(
                    RunCellStatus.PARTIAL
                    if ranking is not None
                    else RunCellStatus.FAILED
                ),
            )
        )
    release = assemble_execution_release_v2(
        matrix,
        tuple(budgets),
        tuple(rankings),
        tuple(terminals),
        hypothesis_packets=_packet_artifacts(tuple(rankings)),
        assembled_at="2026-08-09T12:03:00+08:00",
    )
    assert release.execution_matrix.schema_version == "flatband-execution-matrix-v2"
    assert release.schema_version == "flatband-execution-release-v2"
    assert len(release.top5_projections) == len(matrix.cells)
    assert sum(
        not position.forced_zero
        for projection in release.top5_projections
        for position in projection.positions
    ) == 1
    bundle = next(
        item
        for item in release.terminal_results[0].source_receipt_bundles
        if item.evidence_links
    )
    evidence = bundle.evidence_links[0]
    record = next(
        item
        for item in bundle.metadata_records
        if item.record_receipt_id == evidence.record_receipt_id
    )
    artifact = next(
        item
        for item in bundle.normalized_metadata_artifacts
        if item.artifact_id == record.normalized_metadata_artifact_id
    )
    field = next(
        item
        for item in artifact.fields
        if item.field_artifact_id == evidence.field_artifact_id
    )
    preimage = next(
        item
        for item in bundle.metadata_span_preimages
        if item.span_preimage_id == evidence.span_preimage_id
    )
    assert {
        bundle.provenance_scope,
        artifact.provenance_scope,
        field.provenance_scope,
        preimage.provenance_scope,
        evidence.provenance_scope,
    } == {"INTERNAL_REPLAY_NOT_EXTERNAL_ATTESTATION"}
    assert field.value_utf8.encode("utf-8")[
        preimage.start_byte : preimage.end_byte
    ] == evidence.span_utf8.encode("utf-8")
    assert evidence.span_locator_sha256 == _span_locator(evidence)


def test_phase_matrix_rejects_missing_b0_or_unregistered_arm_subset() -> None:
    configs = tuple(
        item for item in _pilot_configs() if item.system_id is not ResearchSystemId.B0
    )
    with pytest.raises(ValueError, match="preregistered phase"):
        build_execution_matrix(
            _pilot_manifest(), configs, phase=ExecutionPhase.PILOT_R1
        )


@pytest.mark.parametrize(
    ("phase", "systems", "expected_cells", "expected_splits"),
    [
        (
            ExecutionPhase.DEVELOPMENT_ABLATIONS,
            (
                ResearchSystemId.B0,
                ResearchSystemId.E1,
                ResearchSystemId.E2_A,
                ResearchSystemId.E2_B,
                ResearchSystemId.E3,
            ),
            300,
            {BenchmarkSplit.DEVELOPMENT},
        ),
        (
            ExecutionPhase.DEVELOPMENT_FUSION,
            (ResearchSystemId.B0, ResearchSystemId.FUSION),
            120,
            {BenchmarkSplit.DEVELOPMENT},
        ),
        (
            ExecutionPhase.DEVELOPMENT_LOCAL_SENSITIVITY,
            (ResearchSystemId.E1_LOCAL,),
            60,
            {BenchmarkSplit.DEVELOPMENT},
        ),
        (
            ExecutionPhase.LOCKED_PRIMARY,
            (ResearchSystemId.B0, ResearchSystemId.FUSION),
            120,
            {BenchmarkSplit.LOCKED_IID, BenchmarkSplit.LOCKED_OOD},
        ),
        (
            ExecutionPhase.LOCKED_FUSION_COMPONENTS,
            (
                ResearchSystemId.E1,
                ResearchSystemId.E2_A,
                ResearchSystemId.E2_B,
                ResearchSystemId.E3,
            ),
            240,
            {BenchmarkSplit.LOCKED_IID, BenchmarkSplit.LOCKED_OOD},
        ),
        *(
            (
                phase,
                (ResearchSystemId.FUSION,),
                60,
                {BenchmarkSplit.LOCKED_IID, BenchmarkSplit.LOCKED_OOD},
            )
            for phase in (
                ExecutionPhase.LOCKED_FUSION_MINUS_E1,
                ExecutionPhase.LOCKED_FUSION_MINUS_E2,
                ExecutionPhase.LOCKED_FUSION_MINUS_E3,
            )
        ),
    ],
)
def test_main_phases_select_only_preregistered_cases_and_systems(
    phase: ExecutionPhase,
    systems: tuple[ResearchSystemId, ...],
    expected_cells: int,
    expected_splits: set[BenchmarkSplit],
) -> None:
    configs = tuple(_config(system_id) for system_id in systems)
    matrix = build_execution_matrix(_main_manifest(), configs, phase=phase)
    assert len(matrix.cells) == expected_cells
    assert {item.system_id for item in matrix.cells} == set(systems)
    assert {item.split for item in matrix.cells} == expected_splits


@pytest.mark.parametrize(
    "mutation",
    ["missing", "duplicate", "wrong_case", "wrong_system", "wrong_config"],
)
def test_matrix_rejects_adversarial_cell_set(mutation: str) -> None:
    matrix = build_execution_matrix(
        _pilot_manifest(), _pilot_configs(), phase=ExecutionPhase.PILOT_R1
    )
    cells = list(matrix.cells)
    if mutation == "missing":
        cells.pop()
    elif mutation == "duplicate":
        cells[-1] = cells[0]
    elif mutation == "wrong_case":
        cells[0] = _reidentified(
            cells[0],
            id_field="cell_id",
            sha_field="cell_sha256",
            prefix="execution-cell",
            case_id="forged-case",
        )
    elif mutation == "wrong_system":
        cells[0] = _reidentified(
            cells[0],
            id_field="cell_id",
            sha_field="cell_sha256",
            prefix="execution-cell",
            system_id=ResearchSystemId.E3,
        )
    else:
        cells[0] = _reidentified(
            cells[0],
            id_field="cell_id",
            sha_field="cell_sha256",
            prefix="execution-cell",
            system_config_sha256="f" * 64,
        )
    values = {
        field_name: getattr(matrix, field_name)
        for field_name in type(matrix).model_fields
        if field_name not in {"matrix_id", "matrix_sha256"}
    }
    values["cells"] = tuple(cells)
    with pytest.raises(ValidationError, match="exactly equal"):
        _identified(
            ExecutionMatrixV1,
            id_field="matrix_id",
            sha_field="matrix_sha256",
            prefix="execution-matrix",
            values=values,
        )


def test_release_preserves_success_partial_full_underfill_empty_and_failure(
    valid_objects: tuple[
        ExecutionMatrixV1,
        tuple[BudgetManifestV1, ...],
        tuple[ResearchRankingV1, ...],
        tuple[TerminalRunResultV1, ...],
    ],
) -> None:
    release = _assemble(valid_objects)
    assert len(release.top5_projections) == len(release.execution_matrix.cells) == 120
    assert [item.status for item in release.top5_projections[:5]] == [
        RunCellStatus.SUCCEEDED,
        RunCellStatus.PARTIAL,
        RunCellStatus.PARTIAL,
        RunCellStatus.PARTIAL,
        RunCellStatus.FAILED,
    ]
    assert all(not item.forced_zero for item in release.top5_projections[1].positions)
    assert [item.forced_zero for item in release.top5_projections[2].positions] == [
        False,
        False,
        True,
        True,
        True,
    ]
    assert {
        item.missing_reason for item in release.top5_projections[3].positions
    } == {MissingPositionReason.EMPTY_RANKING}
    assert {
        item.missing_reason for item in release.top5_projections[4].positions
    } == {MissingPositionReason.RUN_FAILED}
    assert all(
        item.fixed_gain == 0
        for projection in release.top5_projections
        for item in projection.positions
        if item.forced_zero
    )


@pytest.mark.parametrize("artifact", ["budget", "terminal"])
def test_release_rejects_missing_cell_artifact(valid_objects: Any, artifact: str) -> None:
    matrix, budgets, rankings, terminals = valid_objects
    if artifact == "budget":
        budgets = budgets[:-1]
    else:
        terminals = terminals[:-1]
    with pytest.raises(ValueError, match="exactly cover"):
        assemble_execution_release(
            matrix,
            budgets,
            rankings,
            terminals,
            hypothesis_packets=_packet_artifacts(rankings),
            assembled_at="2026-08-09T12:03:00+08:00",
        )


def test_release_rejects_duplicate_terminal_cell(valid_objects: Any) -> None:
    matrix, budgets, rankings, terminals = valid_objects
    with pytest.raises(ValueError, match="duplicate terminal cell"):
        assemble_execution_release(
            matrix,
            budgets,
            rankings,
            (*terminals[:-1], terminals[0]),
            hypothesis_packets=_packet_artifacts(rankings),
            assembled_at="2026-08-09T12:03:00+08:00",
        )


def test_release_rejects_forged_ranking_reference(valid_objects: Any) -> None:
    matrix, budgets, rankings, terminals = valid_objects
    original = rankings[0]
    forged = _reidentified(
        original,
        id_field="ranking_id",
        sha_field="ranking_sha256",
        prefix="research-ranking",
        budget_manifest_sha256="f" * 64,
    )
    terminal = _reidentified(
        terminals[0],
        id_field="terminal_result_id",
        sha_field="terminal_result_sha256",
        prefix="terminal-result",
        ranking_id=forged.ranking_id,
        ranking_sha256=forged.ranking_sha256,
    )
    with pytest.raises(ValueError, match="prior budget"):
        assemble_execution_release(
            matrix,
            budgets,
            (forged, *rankings[1:]),
            (terminal, *terminals[1:]),
            hypothesis_packets=_packet_artifacts((forged, *rankings[1:])),
            assembled_at="2026-08-09T12:03:00+08:00",
        )


def test_release_rejects_packet_alias_that_reuses_an_authoritative_sha(
    valid_objects: Any,
) -> None:
    matrix, budgets, rankings, terminals = valid_objects
    original = rankings[0]
    positions = list(original.positions)
    positions[0] = positions[0].model_copy(update={"packet_id": "packet-alias"})
    forged = _reidentified(
        original,
        id_field="ranking_id",
        sha_field="ranking_sha256",
        prefix="research-ranking",
        positions=tuple(positions),
    )
    terminal = _reidentified(
        terminals[0],
        id_field="terminal_result_id",
        sha_field="terminal_result_sha256",
        prefix="terminal-result",
        ranking_id=forged.ranking_id,
        ranking_sha256=forged.ranking_sha256,
    )

    with pytest.raises(ValueError, match="missing or forged hypothesis packet"):
        assemble_execution_release(
            matrix,
            budgets,
            (forged, *rankings[1:]),
            (terminal, *terminals[1:]),
            hypothesis_packets=_packet_artifacts(rankings),
            assembled_at="2026-08-09T12:03:00+08:00",
        )


def test_release_rejects_ranking_created_after_terminal(valid_objects: Any) -> None:
    matrix, budgets, rankings, terminals = valid_objects
    late = _reidentified(
        rankings[0],
        id_field="ranking_id",
        sha_field="ranking_sha256",
        prefix="research-ranking",
        created_at="2026-08-09T12:05:00+08:00",
    )
    terminal = _reidentified(
        terminals[0],
        id_field="terminal_result_id",
        sha_field="terminal_result_sha256",
        prefix="terminal-result",
        ranking_id=late.ranking_id,
        ranking_sha256=late.ranking_sha256,
    )
    with pytest.raises(ValueError, match="after the terminal"):
        assemble_execution_release(
            matrix,
            budgets,
            (late, *rankings[1:]),
            (terminal, *terminals[1:]),
            hypothesis_packets=_packet_artifacts((late, *rankings[1:])),
            assembled_at="2026-08-09T12:06:00+08:00",
        )


def test_release_rejects_unreferenced_ranking(valid_objects: Any) -> None:
    matrix, budgets, rankings, terminals = valid_objects
    extra = _reidentified(
        rankings[0],
        id_field="ranking_id",
        sha_field="ranking_sha256",
        prefix="research-ranking",
        created_at="2026-08-09T12:01:01+08:00",
    )
    with pytest.raises(ValueError, match="unreferenced"):
        assemble_execution_release(
            matrix,
            budgets,
            (*rankings, extra),
            terminals,
            hypothesis_packets=_packet_artifacts((*rankings, extra)),
            assembled_at="2026-08-09T12:03:00+08:00",
        )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("physical_requests", 3),
        ("logical_queries", 5),
        ("pages", 5),
        ("records", 101),
        ("response_bytes", 100_001),
        ("unique_documents", 51),
        ("cache_hits", 3),
    ],
)
def test_release_rejects_each_source_budget_overrun(
    valid_objects: Any, field: str, value: int
) -> None:
    matrix, budgets, rankings, terminals = valid_objects
    index = 2 if field == "physical_requests" else 0
    budget = budgets[index]
    receipt_counts = {
        "physical_requests": {
            "physical_requests": value,
            "logical_queries": 1,
            "pages": 1,
        },
        "logical_queries": {
            "physical_requests": value,
            "logical_queries": value,
            "pages": value,
        },
        "pages": {
            "physical_requests": value,
            "logical_queries": 1,
            "pages": value,
        },
        "records": {
            "physical_requests": 1,
            "logical_queries": 1,
            "pages": 1,
            "records": value,
        },
        "response_bytes": {
            "physical_requests": 1,
            "logical_queries": 1,
            "pages": 1,
            "response_bytes": value,
        },
        "unique_documents": {
            "physical_requests": 1,
            "logical_queries": 1,
            "pages": 1,
            "records": value,
            "unique_documents": value,
        },
        "cache_hits": {
            "logical_queries": value,
            "pages": value,
            "cache_hits": value,
        },
    }[field]
    replacement = _source_receipt_bundle(budget, **receipt_counts)
    bundles = _replace_source_bundle(budget, replacement)
    terminal = _terminal(
        budget,
        rankings[index],
        status=terminals[index].status,
        source_receipt_bundles=bundles,
    )
    mutated_terminals = list(terminals)
    mutated_terminals[index] = terminal
    with pytest.raises(ValueError, match="exceeds frozen logical budget"):
        assemble_execution_release(
            matrix,
            budgets,
            rankings,
            tuple(mutated_terminals),
            hypothesis_packets=_packet_artifacts(rankings),
            assembled_at="2026-08-09T12:03:00+08:00",
        )


def test_terminal_rejects_caller_supplied_usage_not_backed_by_receipts(
    valid_objects: Any,
) -> None:
    budget = valid_objects[1][0]
    usage = list(_usage(budget))
    usage[0] = usage[0].model_copy(update={"physical_requests": 1})
    with pytest.raises(ValidationError, match="replayed from source receipts"):
        _terminal(
            budget,
            valid_objects[2][0],
            status=RunCellStatus.SUCCEEDED,
            source_usage=tuple(usage),
        )


def test_budget_config_identity_must_equal_matrix_config(valid_objects: Any) -> None:
    matrix, budgets, rankings, terminals = valid_objects
    changed_config = _reidentified(
        budgets[0].system_config,
        id_field="config_id",
        sha_field="config_sha256",
        prefix="system-config",
        query_plan_sha256="f" * 64,
    )
    forged_budget = _reidentified(
        budgets[0],
        id_field="budget_manifest_id",
        sha_field="budget_manifest_sha256",
        prefix="budget-manifest",
        system_config=changed_config,
    )
    with pytest.raises(ValueError, match="exact matrix cell"):
        assemble_execution_release(
            matrix,
            (forged_budget, *budgets[1:]),
            rankings,
            terminals,
            hypothesis_packets=_packet_artifacts(rankings),
            assembled_at="2026-08-09T12:03:00+08:00",
        )


@pytest.mark.parametrize(
    ("system_id", "overrides", "message"),
    [
        (ResearchSystemId.E1, {"llm": None}, "wrong LLM"),
        (ResearchSystemId.B0, {"llm": _llm()}, "wrong LLM"),
        (
            ResearchSystemId.E3,
            {"cross_domain_tag_graph_sha256": None},
            "wrong TagGraph",
        ),
        (
            ResearchSystemId.E2_A,
            {
                "source_variant": SourceVariant.CROSSREF_ONLY,
                "source_budgets": _budgets(SourceVariant.CROSSREF_ONLY),
            },
            "wrong source variant",
        ),
        (
            ResearchSystemId.B0,
            {
                "local_semantic_model": LocalModelExecutionIdentityV1(
                    bundle_sha256=SHA_A,
                    tokenizer_sha256=SHA_B,
                    model_card_sha256=SHA_C,
                    license_manifest_sha256=SHA_D,
                    vector_dimension=384,
                )
            },
            "wrong local semantic intervention",
        ),
    ],
)
def test_literal_system_interventions_fail_closed(
    system_id: ResearchSystemId,
    overrides: dict[str, Any],
    message: str,
) -> None:
    with pytest.raises(ValidationError, match=message):
        _config(system_id, **overrides)


def test_content_hash_tampering_is_rejected(valid_objects: Any) -> None:
    matrix = valid_objects[0]
    forged = matrix.model_copy(update={"matrix_sha256": "f" * 64})
    with pytest.raises(ValidationError, match="matrix_sha256"):
        ExecutionMatrixV1.model_validate(
            forged.model_dump(mode="python", round_trip=True)
        )


def test_source_policy_registry_replays_frozen_catalog_rows() -> None:
    catalog_path = (
        Path(__file__).resolve().parents[2]
        / "artifacts/experiment/flatband-benchmark-20260809/source_catalog.jsonl"
    )
    rows = {
        row["source_id"]: row
        for row in (
            json.loads(line)
            for line in catalog_path.read_text(encoding="utf-8").splitlines()
        )
    }
    for source_id in ("arxiv", "crossref", "openaire", "openalex"):
        policy = source_policy_values(source_id)
        assert policy["source_catalog_row_sha256"] == canonical_sha256(
            rows[source_id]
        )
        assert policy["projected_fields"] == tuple(
            sorted(rows[source_id]["fields_used"])
        )


def test_cache_only_records_replay_without_a_physical_request(
    valid_objects: Any,
) -> None:
    matrix, budgets, rankings, terminals = valid_objects
    index = 4  # a preregistered failed cell with no ranking/evidence output
    cached = _source_receipt_bundle(
        budgets[index],
        physical_requests=0,
        logical_queries=1,
        pages=1,
        records=2,
        unique_documents=1,
        cache_hits=1,
    )
    usage = replay_source_usage(cached)
    assert (usage.physical_requests, usage.records, usage.cache_hits) == (0, 2, 1)
    bundles = _replace_source_bundle(budgets[index], cached)
    terminal = _terminal(
        budgets[index],
        None,
        status=RunCellStatus.FAILED,
        source_receipt_bundles=bundles,
    )
    changed = list(terminals)
    changed[index] = terminal
    release = assemble_execution_release(
        matrix,
        budgets,
        rankings,
        tuple(changed),
        hypothesis_packets=_packet_artifacts(rankings),
        assembled_at="2026-08-09T12:03:00+08:00",
    )
    replayed = next(
        item
        for item in release.terminal_results[index].source_usage
        if item.source_id == cached.source_id
    )
    assert (replayed.physical_requests, replayed.records) == (0, 2)


def test_cache_inventory_forgery_without_matching_page_record_is_rejected(
    valid_objects: Any,
) -> None:
    budget = valid_objects[1][4]
    bundle = _source_receipt_bundle(
        budget,
        logical_queries=1,
        pages=1,
        records=2,
        unique_documents=1,
        cache_hits=1,
    )
    record = bundle.metadata_records[0]
    normalized = canonical_sha256("forged-normalized-cache-record")
    forged_identity = canonical_sha256(
        {
            "source_id": record.source_id,
            "source_record_id": record.source_record_id,
            "source_url_sha256": record.source_url_sha256,
            "normalized_metadata_sha256": normalized,
            "source_field_projection_sha256": (
                record.source_field_projection_sha256
            ),
        }
    )
    forged_record = _reidentified(
        record,
        id_field="record_receipt_id",
        sha_field="record_receipt_sha256",
        prefix="metadata-record-receipt",
        normalized_metadata_sha256=normalized,
        record_identity_sha256=forged_identity,
    )
    with pytest.raises(ValidationError, match="exactly replay page record"):
        _reidentified(
            bundle,
            id_field="receipt_bundle_id",
            sha_field="receipt_bundle_sha256",
            prefix="source-receipt-bundle",
            metadata_records=(forged_record, *bundle.metadata_records[1:]),
        )


@pytest.mark.parametrize("mutation", ["foreign_record", "foreign_span", "orphan"])
def test_release_rejects_foreign_or_orphan_evidence_provenance(
    valid_objects: Any, mutation: str
) -> None:
    matrix, budgets, rankings, terminals = valid_objects
    terminal = terminals[0]
    source = next(
        item for item in terminal.source_receipt_bundles if item.evidence_links
    )
    original = source.evidence_links[0]
    evidence = list(source.evidence_links)
    if mutation == "foreign_record":
        other = next(
            item
            for item in source.metadata_records
            if item.source_record_id != original.source_record_id
        )
        evidence[0] = _reidentified_evidence(
            original,
            record_receipt_id=other.record_receipt_id,
            record_receipt_sha256=other.record_receipt_sha256,
            source_record_id=other.source_record_id,
            source_url_sha256=other.source_url_sha256,
        )
    elif mutation == "foreign_span":
        forged_span = "X" + original.span_utf8[1:]
        forged_span_sha256 = _utf8_sha256(forged_span)
        evidence[0] = _reidentified_evidence(
            original,
            span_sha256=forged_span_sha256,
            span_utf8=forged_span,
            span_utf8_sha256=forged_span_sha256,
        )
    else:
        evidence.append(
            _reidentified(
                original,
                id_field="evidence_receipt_id",
                sha_field="evidence_receipt_sha256",
                prefix="evidence-link-receipt",
                evidence_link_id="orphan-evidence-link",
                evidence_link_sha256=canonical_sha256("orphan-evidence-semantic"),
            )
        )
    sorted_evidence = tuple(
        sorted(
            evidence,
            key=lambda item: (
                item.packet_id,
                item.evidence_link_id,
                item.evidence_receipt_id,
            ),
        )
    )
    if mutation != "orphan":
        with pytest.raises(
            ValidationError,
            match="foreign metadata artifact|exact span preimage",
        ):
            _reidentified(
                source,
                id_field="receipt_bundle_id",
                sha_field="receipt_bundle_sha256",
                prefix="source-receipt-bundle",
                evidence_links=sorted_evidence,
            )
        return
    changed_source = _reidentified(
        source,
        id_field="receipt_bundle_id",
        sha_field="receipt_bundle_sha256",
        prefix="source-receipt-bundle",
        evidence_links=sorted_evidence,
    )
    bundles = tuple(
        changed_source if item.source_id == source.source_id else item
        for item in terminal.source_receipt_bundles
    )
    changed_terminal = _reterminal_with_bundles(terminal, bundles)
    changed_terminals = list(terminals)
    changed_terminals[0] = changed_terminal
    with pytest.raises(
        ValueError,
        match=(
            "metadata record|span preimage|exactly cover|record/span"
        ),
    ):
        assemble_execution_release(
            matrix,
            budgets,
            rankings,
            tuple(changed_terminals),
            hypothesis_packets=_packet_artifacts(rankings),
            assembled_at="2026-08-09T12:03:00+08:00",
        )


def test_evidence_link_semantic_alias_is_rejected(valid_objects: Any) -> None:
    source = next(
        item
        for item in valid_objects[3][0].source_receipt_bundles
        if item.evidence_links
    )
    original = source.evidence_links[0]
    alias = _reidentified(
        original,
        id_field="evidence_receipt_id",
        sha_field="evidence_receipt_sha256",
        prefix="evidence-link-receipt",
        evidence_link_id="evidence-link-alias",
    )
    with pytest.raises(ValidationError, match="semantic.*aliases"):
        _reidentified(
            source,
            id_field="receipt_bundle_id",
            sha_field="receipt_bundle_sha256",
            prefix="source-receipt-bundle",
            evidence_links=tuple(
                sorted(
                    (*source.evidence_links, alias),
                    key=lambda item: (
                        item.packet_id,
                        item.evidence_link_id,
                        item.evidence_receipt_id,
                    ),
                )
            ),
        )


def test_evidence_span_locator_must_replay_from_record_field_and_span(
    valid_objects: Any,
) -> None:
    source = next(
        item
        for item in valid_objects[3][0].source_receipt_bundles
        if item.evidence_links
    )
    original = source.evidence_links[0]
    forged = original.model_copy(
        update={
            "span_field": "title",
            "metadata_json_path": "/title",
            "span_locator_sha256": "f" * 64,
        }
    )
    with pytest.raises(ValidationError, match="span locator"):
        EvidenceLinkReceiptV1.model_validate(
            forged.model_dump(mode="python", round_trip=True)
        )


def test_container_title_readdressing_cannot_forge_an_abstract_span(
    valid_objects: Any,
) -> None:
    """Rehashing every local receipt cannot move bytes into another field."""

    source = next(
        item
        for item in valid_objects[3][0].source_receipt_bundles
        if item.evidence_links and item.source_id == "crossref"
    )
    original = source.evidence_links[0]
    artifact = next(
        item
        for item in source.normalized_metadata_artifacts
        if item.artifact_id == original.normalized_metadata_artifact_id
    )
    container = next(
        item for item in artifact.fields if item.field_name == "container_title"
    )
    preimage = next(
        item
        for item in source.metadata_span_preimages
        if item.span_preimage_id == original.span_preimage_id
    )
    forged_preimage = _reidentified(
        preimage,
        id_field="span_preimage_id",
        sha_field="span_preimage_sha256",
        prefix="metadata-span-preimage",
        field_artifact_id=container.field_artifact_id,
        field_artifact_sha256=container.field_artifact_sha256,
        field_name=container.field_name,
        json_path=container.json_path,
        start_byte=0,
        end_byte=preimage.span_utf8_bytes,
    )
    forged_evidence = _reidentified_evidence(
        original,
        field_artifact_id=container.field_artifact_id,
        field_artifact_sha256=container.field_artifact_sha256,
        span_field=container.field_name,
        metadata_json_path=container.json_path,
        span_start_byte=forged_preimage.start_byte,
        span_end_byte=forged_preimage.end_byte,
        span_preimage_id=forged_preimage.span_preimage_id,
        span_preimage_sha256=forged_preimage.span_preimage_sha256,
    )
    forged_evidence_links = tuple(
        sorted(
            (
                forged_evidence
                if item.evidence_receipt_id == original.evidence_receipt_id
                else item
                for item in source.evidence_links
            ),
            key=lambda item: (
                item.packet_id,
                item.evidence_link_id,
                item.evidence_receipt_id,
            ),
        )
    )
    forged_preimages = tuple(
        sorted(
            (
                forged_preimage
                if item.span_preimage_id == preimage.span_preimage_id
                else item
                for item in source.metadata_span_preimages
            ),
            key=lambda item: (
                item.record_receipt_id,
                item.field_artifact_id,
                item.start_byte,
                item.end_byte,
                item.span_preimage_id,
            ),
        )
    )
    with pytest.raises(ValidationError, match="field/path/offset bytes"):
        _reidentified(
            source,
            id_field="receipt_bundle_id",
            sha_field="receipt_bundle_sha256",
            prefix="source-receipt-bundle",
            evidence_links=forged_evidence_links,
            metadata_span_preimages=forged_preimages,
        )


def test_readdressed_json_path_mutation_is_rejected(valid_objects: Any) -> None:
    source = next(
        item
        for item in valid_objects[3][0].source_receipt_bundles
        if item.evidence_links
    )
    original = source.evidence_links[0]
    forged = original.model_copy(update={"metadata_json_path": "/container_title"})
    forged = forged.model_copy(update={"span_locator_sha256": _span_locator(forged)})
    with pytest.raises(ValidationError, match="JSON path"):
        EvidenceLinkReceiptV1.model_validate(
            forged.model_dump(mode="python", round_trip=True)
        )


@pytest.mark.parametrize("mutation", ["offset", "span_bytes"])
def test_readdressed_offset_or_span_bytes_must_replay_from_field(
    valid_objects: Any, mutation: str
) -> None:
    source = next(
        item
        for item in valid_objects[3][0].source_receipt_bundles
        if item.evidence_links
    )
    original = source.evidence_links[0]
    preimage = next(
        item
        for item in source.metadata_span_preimages
        if item.span_preimage_id == original.span_preimage_id
    )
    if mutation == "offset":
        span_changes: dict[str, Any] = {
            "start_byte": preimage.start_byte + 1,
            "end_byte": preimage.end_byte + 1,
        }
    else:
        forged_span = "X" + preimage.span_utf8[1:]
        span_changes = {
            "span_utf8": forged_span,
            "span_utf8_sha256": _utf8_sha256(forged_span),
        }
    forged_preimage = _reidentified(
        preimage,
        id_field="span_preimage_id",
        sha_field="span_preimage_sha256",
        prefix="metadata-span-preimage",
        **span_changes,
    )
    forged_evidence = _reidentified_evidence(
        original,
        span_sha256=forged_preimage.span_utf8_sha256,
        span_start_byte=forged_preimage.start_byte,
        span_end_byte=forged_preimage.end_byte,
        span_utf8=forged_preimage.span_utf8,
        span_utf8_sha256=forged_preimage.span_utf8_sha256,
        span_utf8_bytes=forged_preimage.span_utf8_bytes,
        span_preimage_id=forged_preimage.span_preimage_id,
        span_preimage_sha256=forged_preimage.span_preimage_sha256,
    )
    with pytest.raises(ValidationError, match="field/path/offset bytes"):
        _reidentified(
            source,
            id_field="receipt_bundle_id",
            sha_field="receipt_bundle_sha256",
            prefix="source-receipt-bundle",
            evidence_links=tuple(
                sorted(
                    (
                        forged_evidence
                        if item.evidence_receipt_id == original.evidence_receipt_id
                        else item
                        for item in source.evidence_links
                    ),
                    key=lambda item: (
                        item.packet_id,
                        item.evidence_link_id,
                        item.evidence_receipt_id,
                    ),
                )
            ),
            metadata_span_preimages=tuple(
                sorted(
                    (
                        forged_preimage
                        if item.span_preimage_id == preimage.span_preimage_id
                        else item
                        for item in source.metadata_span_preimages
                    ),
                    key=lambda item: (
                        item.record_receipt_id,
                        item.field_artifact_id,
                        item.start_byte,
                        item.end_byte,
                        item.span_preimage_id,
                    ),
                )
            ),
        )


@pytest.mark.parametrize(
    "mutation",
    [
        "missing_artifact",
        "orphan_artifact",
        "cross_cell_artifact",
        "missing_span",
        "orphan_span",
        "cross_cell_span",
    ],
)
def test_source_bundle_exactly_covers_private_artifacts_and_span_preimages(
    valid_objects: Any, mutation: str
) -> None:
    source = next(
        item
        for item in valid_objects[3][0].source_receipt_bundles
        if item.evidence_links
    )
    artifact = source.normalized_metadata_artifacts[0]
    preimage = source.metadata_span_preimages[0]
    artifacts = list(source.normalized_metadata_artifacts)
    preimages = list(source.metadata_span_preimages)
    if mutation == "missing_artifact":
        artifacts.remove(artifact)
    elif mutation == "orphan_artifact":
        artifacts.append(
            _reidentified(
                artifact,
                id_field="artifact_id",
                sha_field="artifact_sha256",
                prefix="normalized-metadata-artifact",
                source_record_id="orphan-source-record",
                source_url_sha256=canonical_sha256("https://example.invalid/orphan"),
            )
        )
    elif mutation == "cross_cell_artifact":
        artifacts[0] = _reidentified(
            artifact,
            id_field="artifact_id",
            sha_field="artifact_sha256",
            prefix="normalized-metadata-artifact",
            cell_id="foreign-cell",
        )
    elif mutation == "missing_span":
        preimages.remove(preimage)
    elif mutation == "orphan_span":
        preimages.append(
            _reidentified(
                preimage,
                id_field="span_preimage_id",
                sha_field="span_preimage_sha256",
                prefix="metadata-span-preimage",
                span_id="orphan-span",
            )
        )
    else:
        preimages[0] = _reidentified(
            preimage,
            id_field="span_preimage_id",
            sha_field="span_preimage_sha256",
            prefix="metadata-span-preimage",
            cell_id="foreign-cell",
        )
    with pytest.raises(
        ValidationError,
        match="missing or foreign|exactly cover|another cell/run",
    ):
        _reidentified(
            source,
            id_field="receipt_bundle_id",
            sha_field="receipt_bundle_sha256",
            prefix="source-receipt-bundle",
            normalized_metadata_artifacts=tuple(
                sorted(artifacts, key=lambda item: (item.source_record_id, item.artifact_id))
            ),
            metadata_span_preimages=tuple(
                sorted(
                    preimages,
                    key=lambda item: (
                        item.record_receipt_id,
                        item.field_artifact_id,
                        item.start_byte,
                        item.end_byte,
                        item.span_preimage_id,
                    ),
                )
            ),
        )


def test_private_metadata_field_hash_requires_exact_utf8_preimage(
    valid_objects: Any,
) -> None:
    source = next(
        item
        for item in valid_objects[3][0].source_receipt_bundles
        if item.normalized_metadata_artifacts
    )
    field = source.normalized_metadata_artifacts[0].fields[0]
    forged = field.model_copy(update={"value_utf8": field.value_utf8 + " forged"})
    with pytest.raises(ValidationError, match="byte count|UTF-8 preimage"):
        NormalizedMetadataFieldV1.model_validate(
            forged.model_dump(mode="python", round_trip=True)
        )


def test_same_true_record_and_span_may_support_multiple_packets(
    valid_objects: Any,
) -> None:
    matrix, budgets, rankings, terminals = valid_objects
    ranking = rankings[0]
    packets = {item.packet_id: item for item in _packet_artifacts(rankings)}
    first = packets[ranking.positions[0].packet_id]
    second = packets[ranking.positions[1].packet_id]
    shared = first.evidence_links[0]
    second_link = second.evidence_links[0].model_copy(
        update={
            "source_record_id": shared.source_record_id,
            "source_url": shared.source_url,
            "span_id": shared.span_id,
            "span_sha256": shared.span_sha256,
        }
    )
    second_values = second.model_dump(
        mode="python", exclude={"packet_id", "packet_sha256"}
    )
    second_values["evidence_links"] = (second_link,)
    second_values["falsification"] = second.falsification
    changed_second = _identified(
        HypothesisPacketV1,
        id_field="packet_id",
        sha_field="packet_sha256",
        prefix="hypothesis-packet",
        values=second_values,
    )

    positions = list(ranking.positions)
    positions[1] = RankingPositionV1(
        selection_rank=2,
        packet_id=changed_second.packet_id,
        packet_sha256=changed_second.packet_sha256,
    )
    changed_ranking = _reidentified(
        ranking,
        id_field="ranking_id",
        sha_field="ranking_sha256",
        prefix="research-ranking",
        positions=tuple(positions),
    )
    terminal = terminals[0]
    source = next(
        item for item in terminal.source_receipt_bundles if item.evidence_links
    )
    first_receipt = next(
        item for item in source.evidence_links if item.packet_id == first.packet_id
    )
    old_second_receipt = next(
        item for item in source.evidence_links if item.packet_id == second.packet_id
    )
    shared_record = next(
        item
        for item in source.metadata_records
        if item.record_receipt_id == first_receipt.record_receipt_id
    )
    changed_second_receipt = _reidentified_evidence(
        old_second_receipt,
        record_receipt_id=shared_record.record_receipt_id,
        record_receipt_sha256=shared_record.record_receipt_sha256,
        normalized_metadata_artifact_id=(
            first_receipt.normalized_metadata_artifact_id
        ),
        normalized_metadata_artifact_sha256=(
            first_receipt.normalized_metadata_artifact_sha256
        ),
        field_artifact_id=first_receipt.field_artifact_id,
        field_artifact_sha256=first_receipt.field_artifact_sha256,
        packet_id=changed_second.packet_id,
        packet_sha256=changed_second.packet_sha256,
        evidence_link_sha256=canonical_sha256(
            second_link.model_dump(mode="python")
        ),
        source_record_id=second_link.source_record_id,
        source_url_sha256=canonical_sha256(second_link.source_url),
        span_id=second_link.span_id,
        span_sha256=second_link.span_sha256,
        span_field=first_receipt.span_field,
        metadata_json_path=first_receipt.metadata_json_path,
        span_start_byte=first_receipt.span_start_byte,
        span_end_byte=first_receipt.span_end_byte,
        span_utf8=first_receipt.span_utf8,
        span_utf8_sha256=first_receipt.span_utf8_sha256,
        span_utf8_bytes=first_receipt.span_utf8_bytes,
        span_preimage_id=first_receipt.span_preimage_id,
        span_preimage_sha256=first_receipt.span_preimage_sha256,
    )
    changed_evidence = tuple(
        sorted(
            (
                changed_second_receipt
                if item.packet_id == second.packet_id
                else item
                for item in source.evidence_links
            ),
            key=lambda item: (
                item.packet_id,
                item.evidence_link_id,
                item.evidence_receipt_id,
            ),
        )
    )
    changed_source = _reidentified(
        source,
        id_field="receipt_bundle_id",
        sha_field="receipt_bundle_sha256",
        prefix="source-receipt-bundle",
        evidence_links=changed_evidence,
        metadata_span_preimages=tuple(
            item
            for item in source.metadata_span_preimages
            if item.span_preimage_id
            in {receipt.span_preimage_id for receipt in changed_evidence}
        ),
    )
    changed_bundles = tuple(
        changed_source if item.source_id == source.source_id else item
        for item in terminal.source_receipt_bundles
    )
    changed_terminal = _reterminal_with_bundles(terminal, changed_bundles)
    changed_terminal = _reidentified(
        changed_terminal,
        id_field="terminal_result_id",
        sha_field="terminal_result_sha256",
        prefix="terminal-result",
        ranking_id=changed_ranking.ranking_id,
        ranking_sha256=changed_ranking.ranking_sha256,
    )
    changed_rankings = (changed_ranking, *rankings[1:])
    changed_terminals = (changed_terminal, *terminals[1:])
    changed_packets = tuple(
        sorted(
            (
                changed_second if item.packet_id == second.packet_id else item
                for item in packets.values()
            ),
            key=lambda item: item.packet_id,
        )
    )
    release = assemble_execution_release(
        matrix,
        budgets,
        changed_rankings,
        changed_terminals,
        hypothesis_packets=changed_packets,
        assembled_at="2026-08-09T12:03:00+08:00",
    )
    shared_receipts = [
        item
        for item in release.terminal_results[0].source_receipt_bundles[0].evidence_links
        if item.record_receipt_id == shared_record.record_receipt_id
        and item.span_id == shared.span_id
    ]
    assert len(shared_receipts) == 2


def test_terminal_rejects_receipt_bundle_crosswired_from_another_cell(
    valid_objects: Any,
) -> None:
    foreign_bundles = valid_objects[3][0].source_receipt_bundles
    target = valid_objects[3][1]
    forged = target.model_copy(
        update={
            "source_receipt_bundles": foreign_bundles,
            "source_usage": tuple(replay_source_usage(item) for item in foreign_bundles),
        }
    )
    with pytest.raises(ValidationError, match="does not bind its terminal cell/run"):
        TerminalRunResultV1.model_validate(
            forged.model_dump(mode="python", round_trip=True)
        )


@pytest.mark.parametrize(
    ("override", "message"),
    [
        ({"request_host": "evil.example"}, "host outside"),
        ({"request_path_class": "ARTICLE_BODY_PATH"}, "catalog/path/projection|path policy"),
        ({"source_catalog_row_sha256": "f" * 64}, "catalog/path/projection"),
        ({"source_field_projection_sha256": "e" * 64}, "catalog/path/projection"),
    ],
)
def test_release_rejects_host_catalog_path_or_projection_drift(
    valid_objects: Any, override: dict[str, Any], message: str
) -> None:
    matrix, budgets, rankings, terminals = valid_objects
    index = 4
    replacement = _source_receipt_bundle(
        budgets[index],
        physical_requests=1,
        logical_queries=1,
        pages=1,
        **override,
    )
    terminal = _terminal(
        budgets[index],
        None,
        status=RunCellStatus.FAILED,
        source_receipt_bundles=_replace_source_bundle(
            budgets[index], replacement
        ),
    )
    changed = list(terminals)
    changed[index] = terminal
    with pytest.raises(ValueError, match=message):
        assemble_execution_release(
            matrix,
            budgets,
            rankings,
            tuple(changed),
            hypothesis_packets=_packet_artifacts(rankings),
            assembled_at="2026-08-09T12:03:00+08:00",
        )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("data_class", "ARTICLE_BODY"),
        ("response_media_type", "application/pdf"),
    ],
)
def test_transport_receipt_rejects_body_or_pdf_data_class(
    valid_objects: Any, field: str, value: str
) -> None:
    hop = next(
        item
        for bundle in valid_objects[3][0].source_receipt_bundles
        for item in bundle.physical_hops
    )
    forged = hop.model_copy(update={field: value})
    with pytest.raises(ValidationError):
        PhysicalHopReceiptV1.model_validate(
            forged.model_dump(mode="python", round_trip=True)
        )


def test_release_rejects_receipts_outside_frozen_terminal_interval(
    valid_objects: Any,
) -> None:
    matrix, budgets, rankings, terminals = valid_objects
    index = 4
    replacement = _source_receipt_bundle(
        budgets[index],
        physical_requests=1,
        logical_queries=1,
        pages=1,
        query_created_at="2026-08-09T11:59:57+08:00",
        hop_completed_at="2026-08-09T11:59:58+08:00",
        page_completed_at="2026-08-09T11:59:59+08:00",
    )
    terminal = _terminal(
        budgets[index],
        None,
        status=RunCellStatus.FAILED,
        source_receipt_bundles=_replace_source_bundle(
            budgets[index], replacement
        ),
    )
    changed = list(terminals)
    changed[index] = terminal
    with pytest.raises(ValueError, match="outside the run interval"):
        assemble_execution_release(
            matrix,
            budgets,
            rankings,
            tuple(changed),
            hypothesis_packets=_packet_artifacts(rankings),
            assembled_at="2026-08-09T12:03:00+08:00",
        )


def test_llm_aggregate_is_replayed_and_bound_to_frozen_model(
    valid_objects: Any,
) -> None:
    matrix, budgets, rankings, terminals = valid_objects
    index = 1  # E1
    receipt = _llm_receipt(budgets[index], terminals[index])
    replayed = replay_llm_usage((receipt,))
    changed_terminal = _reidentified(
        terminals[index],
        id_field="terminal_result_id",
        sha_field="terminal_result_sha256",
        prefix="terminal-result",
        llm_invocation_receipts=(receipt,),
        actual_llm_calls=replayed[0],
        actual_llm_input_tokens=replayed[1],
        actual_llm_output_tokens=replayed[2],
        actual_llm_metadata_packets=replayed[3],
    )
    changed = list(terminals)
    changed[index] = changed_terminal
    release = assemble_execution_release(
        matrix,
        budgets,
        rankings,
        tuple(changed),
        hypothesis_packets=_packet_artifacts(rankings),
        assembled_at="2026-08-09T12:03:00+08:00",
    )
    assert release.terminal_results[index].actual_llm_input_tokens == 80
    assert receipt.provider_attestation == "NOT_PROVIDED"

    forged = changed_terminal.model_copy(update={"actual_llm_input_tokens": 81})
    with pytest.raises(ValidationError, match="replayed from invocation receipts"):
        TerminalRunResultV1.model_validate(
            forged.model_dump(mode="python", round_trip=True)
        )
    forged_input = receipt.metadata_inputs[0].model_copy(
        update={"metadata_packet_sha256": "f" * 64}
    )
    with pytest.raises(ValidationError, match="metadata packet identity"):
        MetadataModelInputRefV1.model_validate(
            forged_input.model_dump(mode="python", round_trip=True)
        )


def test_release_rejects_foreign_prompt_or_early_llm_receipt(
    valid_objects: Any,
) -> None:
    matrix, budgets, rankings, terminals = valid_objects
    index = 1
    for receipt, message in (
        (
            _llm_receipt(budgets[index], terminals[index], prompt_sha256=SHA_D),
            "foreign model/prompt",
        ),
        (
            _llm_receipt(
                budgets[index],
                terminals[index],
                started_at="2026-08-09T11:59:59+08:00",
                completed_at="2026-08-09T12:00:01+08:00",
            ),
            "predates its frozen budget",
        ),
        (
            _llm_receipt(
                budgets[index],
                terminals[index],
                completed_at="2026-08-09T12:01:30+08:00",
            ),
            "ranking predates a retrieval or model receipt",
        ),
    ):
        replayed = replay_llm_usage((receipt,))
        changed_terminal = _reidentified(
            terminals[index],
            id_field="terminal_result_id",
            sha_field="terminal_result_sha256",
            prefix="terminal-result",
            llm_invocation_receipts=(receipt,),
            actual_llm_calls=replayed[0],
            actual_llm_input_tokens=replayed[1],
            actual_llm_output_tokens=replayed[2],
            actual_llm_metadata_packets=replayed[3],
        )
        changed = list(terminals)
        changed[index] = changed_terminal
        with pytest.raises(ValueError, match=message):
            assemble_execution_release(
                matrix,
                budgets,
                rankings,
                tuple(changed),
                hypothesis_packets=_packet_artifacts(rankings),
                assembled_at="2026-08-09T12:03:00+08:00",
            )


def test_disabled_model_arms_require_empty_invocation_receipts(
    valid_objects: Any,
) -> None:
    matrix, budgets, rankings, terminals = valid_objects
    index = 0  # B0 has neither LLM nor local semantic model
    llm_receipt = _llm_receipt(budgets[index], terminals[index])
    llm_usage = replay_llm_usage((llm_receipt,))
    llm_terminal = _reidentified(
        terminals[index],
        id_field="terminal_result_id",
        sha_field="terminal_result_sha256",
        prefix="terminal-result",
        llm_invocation_receipts=(llm_receipt,),
        actual_llm_calls=llm_usage[0],
        actual_llm_input_tokens=llm_usage[1],
        actual_llm_output_tokens=llm_usage[2],
        actual_llm_metadata_packets=llm_usage[3],
    )
    changed = list(terminals)
    changed[index] = llm_terminal
    with pytest.raises(ValueError, match="LLM-disabled"):
        assemble_execution_release(
            matrix,
            budgets,
            rankings,
            tuple(changed),
            hypothesis_packets=_packet_artifacts(rankings),
            assembled_at="2026-08-09T12:03:00+08:00",
        )

    local_receipt = _local_receipt(budgets[index], terminals[index])
    local_terminal = _reidentified(
        terminals[index],
        id_field="terminal_result_id",
        sha_field="terminal_result_sha256",
        prefix="terminal-result",
        local_model_invocation_receipts=(local_receipt,),
        actual_local_model_input_tokens=replay_local_model_usage((local_receipt,)),
    )
    changed[index] = local_terminal
    with pytest.raises(ValueError, match="local-model-disabled"):
        assemble_execution_release(
            matrix,
            budgets,
            rankings,
            tuple(changed),
            hypothesis_packets=_packet_artifacts(rankings),
            assembled_at="2026-08-09T12:03:00+08:00",
        )


def test_local_sensitivity_usage_replays_from_same_cell_receipt() -> None:
    config = _config(ResearchSystemId.E1_LOCAL)
    matrix = build_execution_matrix(
        _main_manifest(),
        (config,),
        phase=ExecutionPhase.DEVELOPMENT_LOCAL_SENSITIVITY,
    )
    budgets = tuple(_budget(matrix, index) for index in range(len(matrix.cells)))
    terminals: list[TerminalRunResultV1] = []
    for index, budget in enumerate(budgets):
        if index == 0:
            source = _source_receipt_bundle(
                budget,
                physical_requests=1,
                logical_queries=1,
                pages=1,
                records=1,
                unique_documents=1,
            )
            terminal = _terminal(
                budget,
                None,
                status=RunCellStatus.FAILED,
                source_receipt_bundles=_replace_source_bundle(budget, source),
            )
            receipt = _local_receipt(budget, terminal)
            terminal = _reidentified(
                terminal,
                id_field="terminal_result_id",
                sha_field="terminal_result_sha256",
                prefix="terminal-result",
                local_model_invocation_receipts=(receipt,),
                actual_local_model_input_tokens=replay_local_model_usage(
                    (receipt,)
                ),
            )
        else:
            terminal = _terminal(
                budget,
                None,
                status=RunCellStatus.FAILED,
            )
        terminals.append(terminal)
    release = assemble_execution_release(
        matrix,
        budgets,
        (),
        tuple(terminals),
        hypothesis_packets=(),
        assembled_at="2026-08-09T12:03:00+08:00",
    )
    assert release.terminal_results[0].actual_local_model_input_tokens == 64
    assert (
        release.terminal_results[0]
        .local_model_invocation_receipts[0]
        .execution_attestation
        == "INTERNAL_REPLAY_ONLY"
    )

    forged = release.terminal_results[0].model_copy(
        update={"actual_local_model_input_tokens": 65}
    )
    with pytest.raises(ValidationError, match="replayed from invocation receipts"):
        TerminalRunResultV1.model_validate(
            forged.model_dump(mode="python", round_trip=True)
        )


def _formal_v3_upstream_fixture() -> Any:
    """Load the explicit sibling fixture without relying on ``tests`` package imports."""

    module_name = "_flatband_research_cases_v3_fixture"
    module = sys.modules.get(module_name)
    if module is None:
        path = Path(__file__).with_name("test_flatband_research_cases.py")
        spec = importlib.util.spec_from_file_location(module_name, path)
        if spec is None or spec.loader is None:  # pragma: no cover - fixed test path
            raise RuntimeError("cannot load formal V3 cases fixture")
        module = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = module
        spec.loader.exec_module(module)
    return module._formal_v3_study()


@pytest.fixture(scope="module")
def formal_v3_execution() -> dict[str, Any]:
    upstream = _formal_v3_upstream_fixture()
    matrix = build_execution_matrix_v2(
        upstream.manifest,
        _pilot_configs(),
        phase=ExecutionPhase.PILOT_R1,
    )
    budgets = tuple(
        _identified(
            BudgetManifestV2,
            id_field="budget_manifest_id",
            sha_field="budget_manifest_sha256",
            prefix="budget-manifest-v2",
            values={
                "frozen_case_release_id": upstream.frozen.release_id,
                "frozen_case_release_sha256": upstream.frozen.release_sha256,
                "pre_run_eligibility_release_id": upstream.eligibility.release_id,
                "pre_run_eligibility_release_sha256": (
                    upstream.eligibility.release_sha256
                ),
                "pre_budget_closure_release_id": (
                    upstream.pre_budget_closure.release_id
                ),
                "pre_budget_closure_release_sha256": (
                    upstream.pre_budget_closure.release_sha256
                ),
                "execution_matrix_id": matrix.matrix_id,
                "execution_matrix_sha256": matrix.matrix_sha256,
                "cell_id": cell.cell_id,
                "cell_sha256": cell.cell_sha256,
                "run_id": f"formal-v3-run-{index:03d}",
                "case_id": cell.case_id,
                "case_sha256": cell.case_sha256,
                "system_config": next(
                    item
                    for item in matrix.system_configs
                    if item.config_id == cell.system_config_id
                ),
                "git_commit": "1" * 40,
                "runtime_environment_sha256": SHA_C,
                "analysis_environment_sha256": SHA_D,
                "max_walltime_seconds": 300,
                "frozen_at": "2026-08-09T20:41:00+08:00",
            },
        )
        for index, cell in enumerate(matrix.cells)
    )
    built_first = build_budget_manifest_v2(
        execution_matrix=matrix,
        cell_id=matrix.cells[0].cell_id,
        frozen_case_release=upstream.frozen,
        pre_run_eligibility_release=upstream.eligibility,
        pre_budget_closure_release=upstream.pre_budget_closure,
        run_id="formal-v3-run-000",
        git_commit="1" * 40,
        runtime_environment_sha256=SHA_C,
        analysis_environment_sha256=SHA_D,
        max_walltime_seconds=300,
        frozen_at="2026-08-09T20:41:00+08:00",
    )
    assert built_first == budgets[0]
    terminals = tuple(
        _terminal(
            budget,
            None,
            status=RunCellStatus.FAILED,
            completed_at="2026-08-09T20:42:00+08:00",
        )
        for budget in budgets
    )
    release = assemble_execution_release_v3(
        matrix,
        budgets,
        (),
        terminals,
        frozen_case_release=upstream.frozen,
        pre_run_eligibility_release=upstream.eligibility,
        pre_budget_closure_release=upstream.pre_budget_closure,
        hypothesis_packets=(),
        assembled_at="2026-08-09T20:43:00+08:00",
    )
    return {
        "upstream": upstream,
        "matrix": matrix,
        "budgets": budgets,
        "terminals": terminals,
        "release": release,
    }


def test_formal_v3_execution_exactly_closes_30_case_failure_denominator(
    formal_v3_execution: dict[str, Any],
) -> None:
    release = formal_v3_execution["release"]
    assert isinstance(release, ExecutionReleaseV3)
    assert len(release.budget_manifests) == 30 * 4
    assert len(release.top5_projections) == 30 * 4
    assert all(
        position.forced_zero
        for projection in release.top5_projections
        for position in projection.positions
    )
    assert_pre_run_eligibility_precedes_execution_v3(
        frozen_case_release=formal_v3_execution["upstream"].frozen,
        eligibility_release=formal_v3_execution["upstream"].eligibility,
        execution_release=release,
    )


def test_formal_v3_execution_rejects_budget_chain_crosswire(
    formal_v3_execution: dict[str, Any],
) -> None:
    budgets = list(formal_v3_execution["budgets"])
    budgets[0] = _reidentified(
        budgets[0],
        id_field="budget_manifest_id",
        sha_field="budget_manifest_sha256",
        prefix="budget-manifest-v2",
        pre_run_eligibility_release_sha256=SHA_A,
    )
    with pytest.raises(ValueError, match="foreign formal release chain"):
        assemble_execution_release_v3(
            formal_v3_execution["matrix"],
            tuple(budgets),
            (),
            formal_v3_execution["terminals"],
            frozen_case_release=formal_v3_execution["upstream"].frozen,
            pre_run_eligibility_release=(
                formal_v3_execution["upstream"].eligibility
            ),
            pre_budget_closure_release=(
                formal_v3_execution["upstream"].pre_budget_closure
            ),
            hypothesis_packets=(),
            assembled_at="2026-08-09T20:43:00+08:00",
        )


def test_formal_v3_execution_rejects_alternate_eligibility_same_selection(
    formal_v3_execution: dict[str, Any],
) -> None:
    upstream = formal_v3_execution["upstream"]
    audits = list(upstream.raw_audits)
    consensus_index = next(
        index
        for index, item in enumerate(audits)
        if item.case_id != upstream.primary_case_id
    )
    audits[consensus_index] = _reidentified(
        audits[consensus_index],
        id_field="audit_id",
        sha_field="audit_sha256",
        prefix="eligibility-audit-v3",
        rationale_sha256=canonical_sha256("alternate-same-selection-rationale"),
    )
    alternate = build_pre_run_eligibility_release_v3(
        assignment_release=upstream.assignment_release,
        raw_audits=tuple(audits),
        adjudications=upstream.adjudications,
        sealed_at=upstream.eligibility.sealed_at,
    )
    assert alternate.active_selections == upstream.eligibility.active_selections
    assert alternate.release_sha256 != upstream.eligibility.release_sha256
    with pytest.raises(ValueError, match="alternate eligibility"):
        assemble_execution_release_v3(
            formal_v3_execution["matrix"],
            formal_v3_execution["budgets"],
            (),
            formal_v3_execution["terminals"],
            frozen_case_release=upstream.frozen,
            pre_run_eligibility_release=alternate,
            pre_budget_closure_release=upstream.pre_budget_closure,
            hypothesis_packets=(),
            assembled_at="2026-08-09T20:43:00+08:00",
        )


def test_formal_v3_execution_rejects_alternate_prebudget_same_universes(
    formal_v3_execution: dict[str, Any],
) -> None:
    upstream = formal_v3_execution["upstream"]
    original = upstream.pre_budget_closure
    alternate = build_pilot_pre_budget_closure_release_v3(
        frozen_case_release=upstream.frozen,
        current_leakage_context=original.current_leakage_context,
        current_candidate_pool_context=(
            original.current_candidate_pool_context
        ),
        calibration_context=original.calibration_context,
        structure_union_replay_release=(
            original.structure_union_replay_release
        ),
        sealed_at="2026-08-09T20:40:31+08:00",
    )
    assert alternate.current_candidate_pool_context == (
        original.current_candidate_pool_context
    )
    assert alternate.release_sha256 != original.release_sha256
    with pytest.raises(ValueError, match="foreign formal release chain"):
        assemble_execution_release_v3(
            formal_v3_execution["matrix"],
            formal_v3_execution["budgets"],
            (),
            formal_v3_execution["terminals"],
            frozen_case_release=upstream.frozen,
            pre_run_eligibility_release=upstream.eligibility,
            pre_budget_closure_release=alternate,
            hypothesis_packets=(),
            assembled_at="2026-08-09T20:43:00+08:00",
        )


def test_formal_v3_budget_must_follow_all_upstream_seals(
    formal_v3_execution: dict[str, Any],
) -> None:
    upstream = formal_v3_execution["upstream"]
    with pytest.raises(ValueError, match="after all formal seals"):
        build_budget_manifest_v2(
            execution_matrix=formal_v3_execution["matrix"],
            cell_id=formal_v3_execution["matrix"].cells[0].cell_id,
            frozen_case_release=upstream.frozen,
            pre_run_eligibility_release=upstream.eligibility,
            pre_budget_closure_release=upstream.pre_budget_closure,
            run_id="formal-v3-early-budget",
            git_commit="1" * 40,
            runtime_environment_sha256=SHA_C,
            analysis_environment_sha256=SHA_D,
            max_walltime_seconds=300,
            frozen_at=upstream.frozen.frozen_at,
        )


def test_formal_v3_verifier_rejects_execution_v2_alias(
    formal_v3_execution: dict[str, Any],
) -> None:
    upstream = formal_v3_execution["upstream"]
    with pytest.raises(ValidationError):
        assert_pre_run_eligibility_precedes_execution_v3(
            frozen_case_release=upstream.frozen,
            eligibility_release=upstream.eligibility,
            execution_release={
                "schema_version": "flatband-execution-release-v2",
            },
        )
