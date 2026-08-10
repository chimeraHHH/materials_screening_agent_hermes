"""Fail-closed execution closure for the flat/narrow-band benchmark.

The production inspiration contracts and the earlier draft research ledger are
intentionally not reused here.  This module defines a one-way, content-addressed
chain::

    BudgetManifestV1 -> ResearchRankingV1 -> TerminalRunResultV1

An :class:`ExecutionReleaseV1` embeds the complete expected split-case by
literal-system matrix and all terminal artifacts.  Its assembler rejects
complete-case omission and creates a fixed five-position projection for every
cell, including failed and empty runs.

The receipt layer is content-addressed internal accounting.  It makes budget
claims reproducible from bounded metadata identities, but it is deliberately
not represented as a cryptographic attestation that an external host returned
the claimed bytes.  Production runners may add signed transport evidence
without weakening the closure defined here.
"""

from __future__ import annotations

import hashlib
from datetime import datetime
from enum import StrEnum
from typing import Annotated, Literal, TypeVar

from pydantic import Field, field_validator, model_validator

from material_agent.inspiration.models import (
    Identifier,
    Sha256,
    ShortText,
    StrictModel,
    canonical_sha256,
    deterministic_id,
)
from material_agent.research.flatband_contracts import (
    SOURCE_CATALOG_V1_SHA256,
    BenchmarkSplit,
    BenchmarkSplitManifestV1,
    BenchmarkSplitManifestV2,
    HypothesisPacketV1,
    SplitCaseRefV1,
    SplitCaseRefV2,
)
from material_agent.research.flatband_cases import (
    FrozenCaseReleaseV3,
    PilotPreBudgetClosureReleaseV3,
    PreRunEligibilityReleaseV3,
    assert_frozen_case_ready_v3,
    assert_pre_run_eligibility_ready_v3,
)


class ResearchSystemId(StrEnum):
    B0 = "B0"
    E1 = "E1"
    E1_LOCAL = "E1-local"
    E2_A = "E2-A"
    E2_B = "E2-B"
    E3 = "E3"
    FUSION = "Fusion"


class SourceVariant(StrEnum):
    CROSSREF_ONLY = "CROSSREF_ONLY"
    E2_A = "E2-A"
    E2_B = "E2-B"


class RunCellStatus(StrEnum):
    SUCCEEDED = "SUCCEEDED"
    PARTIAL = "PARTIAL"
    FAILED = "FAILED"


class ExecutionPhase(StrEnum):
    PILOT_R1 = "PILOT_R1"
    PILOT_R2 = "PILOT_R2"
    DEVELOPMENT_ABLATIONS = "DEVELOPMENT_ABLATIONS"
    DEVELOPMENT_LOCAL_SENSITIVITY = "DEVELOPMENT_LOCAL_SENSITIVITY"
    DEVELOPMENT_FUSION = "DEVELOPMENT_FUSION"
    LOCKED_FUSION_COMPONENTS = "LOCKED_FUSION_COMPONENTS"
    LOCKED_PRIMARY = "LOCKED_PRIMARY"
    LOCKED_FUSION_MINUS_E1 = "LOCKED_FUSION_MINUS_E1"
    LOCKED_FUSION_MINUS_E2 = "LOCKED_FUSION_MINUS_E2"
    LOCKED_FUSION_MINUS_E3 = "LOCKED_FUSION_MINUS_E3"


class MissingPositionReason(StrEnum):
    RUN_FAILED = "RUN_FAILED"
    EMPTY_RANKING = "EMPTY_RANKING"
    UNDERFILL = "UNDERFILL"


class CacheDisposition(StrEnum):
    NETWORK_FETCH = "NETWORK_FETCH"
    CACHE_HIT = "CACHE_HIT"


_REDIRECT_STATUS_CODES = frozenset({301, 302, 303, 307, 308})
_WALLTIME_COMPLETION_TOLERANCE_MS = 1_000

# These literals are a code-side projection of the content-addressed source
# catalog.  They are deliberately narrow: only the four preregistered public
# bibliographic-metadata adapters are executable.  A receipt proves that our
# runner replayed this policy; it is *not* a signature by the remote provider.
_SOURCE_TRANSPORT_POLICIES: dict[str, dict[str, object]] = {
    "arxiv": {
        "adapter_sha256": canonical_sha256(("arxiv", "adapter-v1")),
        "hosts": ("export.arxiv.org",),
        "catalog_row_sha256": (
            "aa0ff8feb3cfeae163afca09c8b9ed836f79ff0dd8df81922b7d849affa4df5a"
        ),
        "request_path_class": "ARXIV_ATOM_QUERY",
        "request_path_template_sha256": canonical_sha256(
            ("arxiv", "ARXIV_ATOM_QUERY", "path-template-v1")
        ),
        "projected_fields": (
            "arxiv_id",
            "authors",
            "categories",
            "journal_doi",
            "license",
            "summary",
            "title",
            "version_history",
        ),
    },
    "crossref": {
        "adapter_sha256": canonical_sha256(("crossref", "adapter-v1")),
        "hosts": ("api.crossref.org",),
        "catalog_row_sha256": (
            "97b098f64f929b836e346fbd87b43f815110c58937a6e94cc87662232d0cde14"
        ),
        "request_path_class": "CROSSREF_REST_WORKS",
        "request_path_template_sha256": canonical_sha256(
            ("crossref", "CROSSREF_REST_WORKS", "path-template-v1")
        ),
        "projected_fields": (
            "abstract",
            "container_title",
            "doi",
            "is_referenced_by_count",
            "license",
            "published",
            "reference",
            "subject",
            "title",
        ),
    },
    "openaire": {
        "adapter_sha256": canonical_sha256(("openaire", "adapter-v1")),
        "hosts": ("api.openaire.eu",),
        "catalog_row_sha256": (
            "c9fd3e91fe55c8dd3d80bb578c2e37768c482eeeb1699e4bdd90bf436429b173"
        ),
        "request_path_class": "OPENAIRE_RESEARCH_PRODUCTS",
        "request_path_template_sha256": canonical_sha256(
            ("openaire", "OPENAIRE_RESEARCH_PRODUCTS", "path-template-v1")
        ),
        "projected_fields": (
            "access_right",
            "citations",
            "description",
            "fields_of_science",
            "instances",
            "license",
            "pid",
            "provenance",
            "subjects",
            "title",
        ),
    },
    "openalex": {
        "adapter_sha256": canonical_sha256(("openalex", "adapter-v1")),
        "hosts": ("api.openalex.org",),
        "catalog_row_sha256": (
            "f9cfc627276338ffdc11c86afc700129b66f6f677dec96f170445c131b90415f"
        ),
        "request_path_class": "OPENALEX_API_WORKS",
        "request_path_template_sha256": canonical_sha256(
            ("openalex", "OPENALEX_API_WORKS", "path-template-v1")
        ),
        "projected_fields": (
            "abstract_inverted_index",
            "cited_by_count",
            "display_name",
            "doi",
            "keywords",
            "locations",
            "open_access",
            "openalex_id",
            "referenced_works",
            "topics",
        ),
    },
}


def source_policy_values(source_id: str) -> dict[str, object]:
    """Return the frozen public-metadata policy used to build manifests.

    This helper exposes no network capability.  It exists so builders and
    tests do not independently retype source-catalog row identities.
    """

    policy = _SOURCE_TRANSPORT_POLICIES.get(source_id)
    if policy is None:
        raise ValueError("source has no frozen transport policy")
    fields = tuple(policy["projected_fields"])
    path_class = str(policy["request_path_class"])
    catalog_row_sha256 = str(policy["catalog_row_sha256"])
    field_projection_sha256 = canonical_sha256(
        {
            "source_catalog_sha256": SOURCE_CATALOG_V1_SHA256,
            "source_catalog_row_sha256": catalog_row_sha256,
            "source_id": source_id,
            "request_path_class": path_class,
            "projected_fields": fields,
        }
    )
    return {
        "retrieval_identity_sha256": str(policy["adapter_sha256"]),
        "allowed_request_hosts": tuple(policy["hosts"]),
        "source_catalog_sha256": SOURCE_CATALOG_V1_SHA256,
        "source_catalog_row_sha256": catalog_row_sha256,
        "request_path_class": path_class,
        "request_path_template_sha256": str(
            policy["request_path_template_sha256"]
        ),
        "projected_fields": fields,
        "source_field_projection_sha256": field_projection_sha256,
    }


def _require_timestamp(value: str) -> str:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("timestamp must be RFC3339-compatible") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("timestamp must include a UTC offset")
    return value


def _timestamp(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _require_sorted_unique(values: tuple[str, ...], label: str) -> None:
    if values != tuple(sorted(set(values))):
        raise ValueError(f"{label} must be sorted and unique")


ModelT = TypeVar("ModelT", bound=StrictModel)


def _revalidate(value: ModelT, model_type: type[ModelT]) -> ModelT:
    """Revalidate serialized content, including adversarial ``model_copy`` data."""

    return model_type.model_validate(
        value.model_dump(mode="python", round_trip=True)
    )


def _identity_values(
    model: StrictModel, *, id_field: str, sha_field: str, prefix: str
) -> tuple[str, str]:
    semantic = model.model_dump(mode="python", exclude={id_field, sha_field})
    digest = canonical_sha256(semantic)
    identifier = deterministic_id(prefix, {sha_field: digest})
    return identifier, digest


def _assert_identity(
    model: StrictModel, *, id_field: str, sha_field: str, prefix: str
) -> None:
    identifier, digest = _identity_values(
        model, id_field=id_field, sha_field=sha_field, prefix=prefix
    )
    if getattr(model, sha_field) != digest:
        raise ValueError(f"{sha_field} does not match semantic content")
    if getattr(model, id_field) != identifier:
        raise ValueError(f"{id_field} does not match {sha_field}")


def _build_identified(
    model_type: type[ModelT],
    *,
    id_field: str,
    sha_field: str,
    prefix: str,
    values: dict[str, object],
) -> ModelT:
    draft = model_type.model_construct(**values)
    identifier, digest = _identity_values(
        draft, id_field=id_field, sha_field=sha_field, prefix=prefix
    )
    return model_type.model_validate(
        {**values, id_field: identifier, sha_field: digest}
    )


def _utf8_sha256(value: str) -> str:
    """Hash the exact UTF-8 preimage, rather than its canonical-JSON encoding."""

    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _metadata_json_path(field_name: str) -> str:
    """Return the only accepted JSON Pointer for a normalized top-level field."""

    escaped = field_name.replace("~", "~0").replace("/", "~1")
    return f"/{escaped}"


def _evidence_span_locator_sha256(
    *,
    record_receipt_id: str,
    record_receipt_sha256: str,
    artifact_sha256: str,
    field_artifact_id: str,
    field_artifact_sha256: str,
    field_name: str,
    json_path: str,
    start_byte: int,
    end_byte: int,
    span_utf8: str,
    span_utf8_sha256: str,
) -> str:
    """Address one exact byte span in one exact normalized metadata record."""

    return canonical_sha256(
        {
            "record_receipt_id": record_receipt_id,
            "record_receipt_sha256": record_receipt_sha256,
            "artifact_sha256": artifact_sha256,
            "field_artifact_id": field_artifact_id,
            "field_artifact_sha256": field_artifact_sha256,
            "field_name": field_name,
            "json_path": json_path,
            "start_byte": start_byte,
            "end_byte": end_byte,
            "span_utf8": span_utf8,
            "span_utf8_sha256": span_utf8_sha256,
        }
    )


class SourceBudgetV1(StrictModel):
    """Frozen request and information budget for one retrieval source."""

    source_id: Identifier
    retrieval_identity_sha256: Sha256
    allowed_request_hosts: Annotated[
        tuple[ShortText, ...], Field(min_length=1, max_length=8)
    ]
    max_physical_requests: Annotated[int, Field(ge=0, le=8)]
    max_logical_queries: Annotated[int, Field(ge=0, le=128)]
    max_pages: Annotated[int, Field(ge=0, le=128)]
    max_records: Annotated[int, Field(ge=0, le=100_000)]
    max_response_bytes: Annotated[int, Field(ge=0, le=100_000_000)]
    max_unique_documents: Annotated[int, Field(ge=0, le=20_000)]
    max_cache_hits: Annotated[int, Field(ge=0, le=100_000)]
    source_catalog_sha256: Literal[SOURCE_CATALOG_V1_SHA256] = (
        SOURCE_CATALOG_V1_SHA256
    )
    source_catalog_row_sha256: Sha256
    request_path_class: ShortText
    request_path_template_sha256: Sha256
    projected_fields: Annotated[
        tuple[ShortText, ...], Field(min_length=1, max_length=64)
    ]
    source_field_projection_sha256: Sha256

    @model_validator(mode="after")
    def validate_budget(self) -> "SourceBudgetV1":
        _require_sorted_unique(self.allowed_request_hosts, "allowed request hosts")
        expected = source_policy_values(self.source_id)
        if self.retrieval_identity_sha256 != expected["retrieval_identity_sha256"]:
            raise ValueError("source retrieval identity differs from frozen transport policy")
        if self.allowed_request_hosts != expected["allowed_request_hosts"]:
            raise ValueError("source hosts differ from frozen transport policy")
        for field in (
            "source_catalog_sha256",
            "source_catalog_row_sha256",
            "request_path_class",
            "request_path_template_sha256",
            "projected_fields",
            "source_field_projection_sha256",
        ):
            if getattr(self, field) != expected[field]:
                raise ValueError(
                    f"source {field} differs from frozen source-catalog policy"
                )
        _require_sorted_unique(self.projected_fields, "source projected fields")
        if self.max_physical_requests == 0:
            if any(
                (
                    self.max_logical_queries,
                    self.max_pages,
                    self.max_records,
                    self.max_response_bytes,
                    self.max_unique_documents,
                    self.max_cache_hits,
                )
            ):
                raise ValueError("zero-request source must have zero logical budget")
            return self
        if self.max_records < self.max_unique_documents:
            raise ValueError("unique-document budget cannot exceed record budget")
        if self.max_cache_hits > self.max_pages:
            raise ValueError("cache-hit budget cannot exceed page budget")
        if not all(
            (
                self.max_logical_queries,
                self.max_pages,
                self.max_records,
                self.max_response_bytes,
                self.max_unique_documents,
            )
        ):
            raise ValueError("active source requires positive information budgets")
        return self


class LlmExecutionIdentityV1(StrictModel):
    provider: ShortText
    model: ShortText
    revision: ShortText
    prompt_sha256: Sha256
    tokenizer_sha256: Sha256
    output_schema_sha256: Sha256
    max_calls: Literal[2] = 2
    max_input_tokens: Literal[12_000] = 12_000
    max_output_tokens: Annotated[int, Field(ge=1, le=16_000)]
    metadata_packet_limit: Literal[20] = 20


class LocalModelExecutionIdentityV1(StrictModel):
    bundle_sha256: Sha256
    tokenizer_sha256: Sha256
    model_card_sha256: Sha256
    license_manifest_sha256: Sha256
    vector_dimension: Annotated[int, Field(ge=1, le=65_536)]


_SOURCE_ALLOCATIONS: dict[SourceVariant, dict[str, int]] = {
    SourceVariant.CROSSREF_ONLY: {"crossref": 8},
    SourceVariant.E2_A: {"arxiv": 2, "crossref": 3, "openalex": 3},
    SourceVariant.E2_B: {
        "arxiv": 2,
        "crossref": 2,
        "openaire": 2,
        "openalex": 2,
    },
}


class SystemConfigV1(StrictModel):
    """Literal, content-addressed intervention arm.

    The six registered system IDs cannot be relabelled with a different source,
    semantic, TagGraph, query, cache, or local-model intervention.
    """

    schema_version: Literal["flatband-system-config-v1"] = (
        "flatband-system-config-v1"
    )
    config_id: Identifier
    config_sha256: Sha256
    system_id: ResearchSystemId
    source_variant: SourceVariant
    source_budgets: Annotated[
        tuple[SourceBudgetV1, ...], Field(min_length=1, max_length=4)
    ]
    query_plan_sha256: Sha256
    record_projection_sha256: Sha256
    ranking_policy_sha256: Sha256
    cache_policy_sha256: Sha256
    cache_snapshot_sha256: Sha256
    baseline_tag_graph_sha256: Sha256
    cross_domain_tag_graph_sha256: Sha256 | None = None
    llm: LlmExecutionIdentityV1 | None = None
    local_semantic_model: LocalModelExecutionIdentityV1 | None = None
    fusion_components: Annotated[
        tuple[ResearchSystemId, ...], Field(max_length=4)
    ] = ()
    source_catalog_sha256: Literal[SOURCE_CATALOG_V1_SHA256] = (
        SOURCE_CATALOG_V1_SHA256
    )
    scientific_conclusion: Literal[False] = False

    @model_validator(mode="after")
    def validate_config(self) -> "SystemConfigV1":
        budgets = tuple(
            _revalidate(item, SourceBudgetV1) for item in self.source_budgets
        )
        source_ids = tuple(item.source_id for item in budgets)
        if source_ids != tuple(sorted(set(source_ids))):
            raise ValueError("source budgets must be source-ID sorted and unique")
        observed = {item.source_id: item.max_physical_requests for item in budgets}
        if observed != _SOURCE_ALLOCATIONS[self.source_variant]:
            raise ValueError("physical source allocation differs from frozen arm")
        if sum(observed.values()) != 8:
            raise ValueError("every system must freeze exactly eight physical requests")

        if tuple(item.value for item in self.fusion_components) != tuple(
            sorted({item.value for item in self.fusion_components})
        ):
            raise ValueError("fusion components must be value-sorted and unique")
        allowed_components = {
            ResearchSystemId.E1,
            ResearchSystemId.E2_A,
            ResearchSystemId.E2_B,
            ResearchSystemId.E3,
        }
        if not set(self.fusion_components) <= allowed_components:
            raise ValueError("Fusion may contain only E1/E2-A/E2-B/E3")
        if {
            ResearchSystemId.E2_A,
            ResearchSystemId.E2_B,
        } <= set(self.fusion_components):
            raise ValueError("Fusion cannot contain both E2 variants")

        if self.system_id is ResearchSystemId.B0:
            expected_variant = SourceVariant.CROSSREF_ONLY
            semantic = False
            local_semantic = False
            cross_domain = False
            expected_components: tuple[ResearchSystemId, ...] = ()
        elif self.system_id is ResearchSystemId.E1:
            expected_variant = SourceVariant.CROSSREF_ONLY
            semantic = True
            local_semantic = False
            cross_domain = False
            expected_components = ()
        elif self.system_id is ResearchSystemId.E1_LOCAL:
            expected_variant = SourceVariant.CROSSREF_ONLY
            semantic = False
            local_semantic = True
            cross_domain = False
            expected_components = ()
        elif self.system_id is ResearchSystemId.E2_A:
            expected_variant = SourceVariant.E2_A
            semantic = False
            local_semantic = False
            cross_domain = False
            expected_components = ()
        elif self.system_id is ResearchSystemId.E2_B:
            expected_variant = SourceVariant.E2_B
            semantic = False
            local_semantic = False
            cross_domain = False
            expected_components = ()
        elif self.system_id is ResearchSystemId.E3:
            expected_variant = SourceVariant.CROSSREF_ONLY
            semantic = False
            local_semantic = False
            cross_domain = True
            expected_components = ()
        else:
            if not self.fusion_components:
                raise ValueError("Fusion requires at least one promoted component")
            if ResearchSystemId.E2_B in self.fusion_components:
                expected_variant = SourceVariant.E2_B
            elif ResearchSystemId.E2_A in self.fusion_components:
                expected_variant = SourceVariant.E2_A
            else:
                expected_variant = SourceVariant.CROSSREF_ONLY
            semantic = ResearchSystemId.E1 in self.fusion_components
            local_semantic = False
            cross_domain = ResearchSystemId.E3 in self.fusion_components
            expected_components = self.fusion_components

        if self.source_variant is not expected_variant:
            raise ValueError("system ID is relabelled with the wrong source variant")
        if (self.llm is not None) != semantic:
            raise ValueError("system ID is relabelled with the wrong LLM intervention")
        if self.llm is not None:
            _revalidate(self.llm, LlmExecutionIdentityV1)
        if (self.local_semantic_model is not None) != local_semantic:
            raise ValueError(
                "system ID is relabelled with the wrong local semantic intervention"
            )
        if self.local_semantic_model is not None:
            _revalidate(self.local_semantic_model, LocalModelExecutionIdentityV1)
        if (self.cross_domain_tag_graph_sha256 is not None) != cross_domain:
            raise ValueError("system ID is relabelled with the wrong TagGraph intervention")
        if self.fusion_components != expected_components:
            raise ValueError("non-Fusion systems cannot declare fusion components")
        _assert_identity(
            self,
            id_field="config_id",
            sha_field="config_sha256",
            prefix="system-config",
        )
        return self


class ExecutionCellV1(StrictModel):
    cell_id: Identifier
    cell_sha256: Sha256
    split: BenchmarkSplit
    case_id: Identifier
    case_sha256: Sha256
    system_id: ResearchSystemId
    system_config_id: Identifier
    system_config_sha256: Sha256

    @model_validator(mode="after")
    def validate_cell(self) -> "ExecutionCellV1":
        _assert_identity(
            self,
            id_field="cell_id",
            sha_field="cell_sha256",
            prefix="execution-cell",
        )
        return self


def _information_budget_totals(config: SystemConfigV1) -> tuple[int, ...]:
    return tuple(
        sum(getattr(item, field) for item in config.source_budgets)
        for field in (
            "max_physical_requests",
            "max_logical_queries",
            "max_pages",
            "max_records",
            "max_response_bytes",
            "max_unique_documents",
            "max_cache_hits",
        )
    )


def _intervention_allowlist(config: SystemConfigV1) -> frozenset[str]:
    allowed = {"system_id"}
    components = set(config.fusion_components)
    if config.system_id is ResearchSystemId.E1 or ResearchSystemId.E1 in components:
        allowed.add("llm")
    if config.system_id is ResearchSystemId.E1_LOCAL:
        allowed.add("local_semantic_model")
    if config.system_id in {ResearchSystemId.E2_A, ResearchSystemId.E2_B} or components & {
        ResearchSystemId.E2_A,
        ResearchSystemId.E2_B,
    }:
        allowed.update({"source_variant", "source_budgets", "query_plan_sha256"})
    if config.system_id is ResearchSystemId.E3 or ResearchSystemId.E3 in components:
        allowed.add("cross_domain_tag_graph_sha256")
    if config.system_id is ResearchSystemId.FUSION:
        allowed.add("fusion_components")
    return frozenset(allowed)


def _assert_phase_identifiability(configs: tuple[SystemConfigV1, ...]) -> None:
    """Reject any between-arm difference outside the registered intervention."""

    if len({_information_budget_totals(item) for item in configs}) != 1:
        raise ValueError("phase arms do not share one aggregate information budget")
    anchors = tuple(item for item in configs if item.system_id is ResearchSystemId.B0)
    if not anchors:
        return
    anchor = anchors[0].model_dump(
        mode="python", exclude={"config_id", "config_sha256"}
    )
    for config in configs:
        observed = config.model_dump(
            mode="python", exclude={"config_id", "config_sha256"}
        )
        differences = {
            field for field in anchor if anchor[field] != observed[field]
        }
        forbidden = differences - _intervention_allowlist(config)
        if forbidden:
            raise ValueError(
                "phase arm changes non-intervention nuisance fields: "
                + ",".join(sorted(forbidden))
            )


class ExecutionMatrixV1(StrictModel):
    schema_version: Literal["flatband-execution-matrix-v1"] = (
        "flatband-execution-matrix-v1"
    )
    matrix_id: Identifier
    matrix_sha256: Sha256
    phase: ExecutionPhase
    split_manifest: BenchmarkSplitManifestV1
    system_configs: Annotated[
        tuple[SystemConfigV1, ...], Field(min_length=1, max_length=6)
    ]
    cells: Annotated[
        tuple[ExecutionCellV1, ...], Field(min_length=1, max_length=720)
    ]
    scientific_conclusion: Literal[False] = False

    @model_validator(mode="after")
    def validate_matrix(self) -> "ExecutionMatrixV1":
        manifest = _revalidate(self.split_manifest, BenchmarkSplitManifestV1)
        configs = tuple(
            _revalidate(item, SystemConfigV1) for item in self.system_configs
        )
        _assert_phase_identifiability(configs)
        config_keys = tuple(item.system_id for item in configs)
        expected_systems, included_splits = _phase_design(self.phase, manifest)
        if config_keys != expected_systems:
            raise ValueError("system configs do not exactly match the preregistered phase")
        cells = tuple(_revalidate(item, ExecutionCellV1) for item in self.cells)
        expected = tuple(
            _make_execution_cell(case, config)
            for case in manifest.cases
            if case.split in included_splits
            for config in configs
        )
        expected = tuple(sorted(expected, key=_cell_sort_key))
        observed = tuple(sorted(cells, key=_cell_sort_key))
        if observed != expected:
            raise ValueError(
                "execution cells must exactly equal split case x literal system config"
            )
        if self.cells != observed:
            raise ValueError("execution cells must be case/system sorted")
        _assert_identity(
            self,
            id_field="matrix_id",
            sha_field="matrix_sha256",
            prefix="execution-matrix",
        )
        return self


class ExecutionMatrixV2(StrictModel):
    """Formal execution matrix over the leakage-safe split V2 contract.

    V1 remains available only for historical/draft releases.  V2 is the
    scientific Pilot/Main path because ``BenchmarkSplitManifestV2`` separates
    OOD taxonomy memberships from connected-component independence edges.
    """

    schema_version: Literal["flatband-execution-matrix-v2"] = (
        "flatband-execution-matrix-v2"
    )
    matrix_id: Identifier
    matrix_sha256: Sha256
    phase: ExecutionPhase
    split_manifest: BenchmarkSplitManifestV2
    system_configs: Annotated[
        tuple[SystemConfigV1, ...], Field(min_length=1, max_length=6)
    ]
    cells: Annotated[
        tuple[ExecutionCellV1, ...], Field(min_length=1, max_length=720)
    ]
    scientific_conclusion: Literal[False] = False

    @model_validator(mode="after")
    def validate_matrix(self) -> "ExecutionMatrixV2":
        manifest = _revalidate(self.split_manifest, BenchmarkSplitManifestV2)
        configs = tuple(
            _revalidate(item, SystemConfigV1) for item in self.system_configs
        )
        _assert_phase_identifiability(configs)
        config_keys = tuple(item.system_id for item in configs)
        expected_systems, included_splits = _phase_design(self.phase, manifest)
        if config_keys != expected_systems:
            raise ValueError("system configs do not exactly match the preregistered phase")
        cells = tuple(_revalidate(item, ExecutionCellV1) for item in self.cells)
        expected = tuple(
            _make_execution_cell(case, config)
            for case in manifest.cases
            if case.split in included_splits
            for config in configs
        )
        expected = tuple(sorted(expected, key=_cell_sort_key))
        observed = tuple(sorted(cells, key=_cell_sort_key))
        if observed != expected:
            raise ValueError(
                "execution cells must exactly equal split case x literal system config"
            )
        if self.cells != observed:
            raise ValueError("execution cells must be case/system sorted")
        _assert_identity(
            self,
            id_field="matrix_id",
            sha_field="matrix_sha256",
            prefix="execution-matrix-v2",
        )
        return self


def _cell_sort_key(cell: ExecutionCellV1) -> tuple[str, str]:
    return cell.case_id, cell.system_id.value


def _phase_design(
    phase: ExecutionPhase,
    manifest: BenchmarkSplitManifestV1 | BenchmarkSplitManifestV2,
) -> tuple[tuple[ResearchSystemId, ...], frozenset[BenchmarkSplit]]:
    if phase is ExecutionPhase.PILOT_R1:
        systems = (
            ResearchSystemId.B0,
            ResearchSystemId.E1,
            ResearchSystemId.E2_B,
            ResearchSystemId.E3,
        )
        splits = frozenset({BenchmarkSplit.PILOT_R1})
    elif phase is ExecutionPhase.PILOT_R2:
        systems = (
            ResearchSystemId.B0,
            ResearchSystemId.E1,
            ResearchSystemId.E2_B,
            ResearchSystemId.E3,
        )
        splits = frozenset({BenchmarkSplit.PILOT_R2})
    elif phase is ExecutionPhase.DEVELOPMENT_ABLATIONS:
        systems = (
            ResearchSystemId.B0,
            ResearchSystemId.E1,
            ResearchSystemId.E2_A,
            ResearchSystemId.E2_B,
            ResearchSystemId.E3,
        )
        splits = frozenset({BenchmarkSplit.DEVELOPMENT})
    elif phase is ExecutionPhase.DEVELOPMENT_LOCAL_SENSITIVITY:
        systems = (ResearchSystemId.E1_LOCAL,)
        splits = frozenset({BenchmarkSplit.DEVELOPMENT})
    elif phase is ExecutionPhase.DEVELOPMENT_FUSION:
        systems = (ResearchSystemId.B0, ResearchSystemId.FUSION)
        splits = frozenset({BenchmarkSplit.DEVELOPMENT})
    elif phase is ExecutionPhase.LOCKED_FUSION_COMPONENTS:
        # Private derivation-only cells.  Both E2 variants are frozen so the
        # development-selected variant can be exact-joined later without
        # reopening the locked execution policy.  These cells are never
        # analysis arms or public comparison rows.
        systems = (
            ResearchSystemId.E1,
            ResearchSystemId.E2_A,
            ResearchSystemId.E2_B,
            ResearchSystemId.E3,
        )
        splits = frozenset({BenchmarkSplit.LOCKED_IID, BenchmarkSplit.LOCKED_OOD})
    elif phase is ExecutionPhase.LOCKED_PRIMARY:
        systems = (ResearchSystemId.B0, ResearchSystemId.FUSION)
        splits = frozenset({BenchmarkSplit.LOCKED_IID, BenchmarkSplit.LOCKED_OOD})
    elif phase in {
        ExecutionPhase.LOCKED_FUSION_MINUS_E1,
        ExecutionPhase.LOCKED_FUSION_MINUS_E2,
        ExecutionPhase.LOCKED_FUSION_MINUS_E3,
    }:
        systems = (ResearchSystemId.FUSION,)
        splits = frozenset({BenchmarkSplit.LOCKED_IID, BenchmarkSplit.LOCKED_OOD})
    else:  # pragma: no cover - exhaustive over the frozen enum
        raise ValueError("execution phase is not registered")
    manifest_splits = {case.split for case in manifest.cases}
    if not splits <= manifest_splits:
        raise ValueError("execution phase is incompatible with the split manifest")
    return tuple(sorted(systems, key=lambda item: item.value)), splits


def _make_execution_cell(
    case: SplitCaseRefV1 | SplitCaseRefV2, config: SystemConfigV1
) -> ExecutionCellV1:
    return _build_identified(
        ExecutionCellV1,
        id_field="cell_id",
        sha_field="cell_sha256",
        prefix="execution-cell",
        values={
            "split": case.split,
            "case_id": case.case_id,
            "case_sha256": case.case_sha256,
            "system_id": config.system_id,
            "system_config_id": config.config_id,
            "system_config_sha256": config.config_sha256,
        },
    )


def build_execution_matrix(
    split_manifest: BenchmarkSplitManifestV1,
    system_configs: tuple[SystemConfigV1, ...],
    *,
    phase: ExecutionPhase,
) -> ExecutionMatrixV1:
    manifest = _revalidate(split_manifest, BenchmarkSplitManifestV1)
    configs = tuple(_revalidate(item, SystemConfigV1) for item in system_configs)
    configs = tuple(sorted(configs, key=lambda item: item.system_id.value))
    _assert_phase_identifiability(configs)
    expected_systems, included_splits = _phase_design(phase, manifest)
    if tuple(item.system_id for item in configs) != expected_systems:
        raise ValueError("system configs do not exactly match the preregistered phase")
    cells = tuple(
        sorted(
            (
                _make_execution_cell(case, config)
                for case in manifest.cases
                if case.split in included_splits
                for config in configs
            ),
            key=_cell_sort_key,
        )
    )
    return _build_identified(
        ExecutionMatrixV1,
        id_field="matrix_id",
        sha_field="matrix_sha256",
        prefix="execution-matrix",
        values={
            "phase": phase,
            "split_manifest": manifest,
            "system_configs": configs,
            "cells": cells,
        },
    )


def build_execution_matrix_v2(
    split_manifest: BenchmarkSplitManifestV2,
    system_configs: tuple[SystemConfigV1, ...],
    *,
    phase: ExecutionPhase,
) -> ExecutionMatrixV2:
    """Build the formal split-V2 case-by-system execution matrix."""

    manifest = _revalidate(split_manifest, BenchmarkSplitManifestV2)
    configs = tuple(_revalidate(item, SystemConfigV1) for item in system_configs)
    configs = tuple(sorted(configs, key=lambda item: item.system_id.value))
    _assert_phase_identifiability(configs)
    expected_systems, included_splits = _phase_design(phase, manifest)
    if tuple(item.system_id for item in configs) != expected_systems:
        raise ValueError("system configs do not exactly match the preregistered phase")
    cells = tuple(
        sorted(
            (
                _make_execution_cell(case, config)
                for case in manifest.cases
                if case.split in included_splits
                for config in configs
            ),
            key=_cell_sort_key,
        )
    )
    return _build_identified(
        ExecutionMatrixV2,
        id_field="matrix_id",
        sha_field="matrix_sha256",
        prefix="execution-matrix-v2",
        values={
            "phase": phase,
            "split_manifest": manifest,
            "system_configs": configs,
            "cells": cells,
        },
    )


class BudgetManifestV1(StrictModel):
    schema_version: Literal["flatband-budget-manifest-v1"] = (
        "flatband-budget-manifest-v1"
    )
    budget_manifest_id: Identifier
    budget_manifest_sha256: Sha256
    execution_matrix_id: Identifier
    execution_matrix_sha256: Sha256
    cell_id: Identifier
    cell_sha256: Sha256
    run_id: Identifier
    case_id: Identifier
    case_sha256: Sha256
    system_config: SystemConfigV1
    git_commit: Annotated[str, Field(pattern=r"^[0-9a-f]{40}$")]
    runtime_environment_sha256: Sha256
    analysis_environment_sha256: Sha256
    max_walltime_seconds: Annotated[int, Field(ge=1, le=3_600)]
    frozen_at: Annotated[str, Field(min_length=20, max_length=40)]
    scientific_conclusion: Literal[False] = False

    @field_validator("frozen_at")
    @classmethod
    def validate_frozen_at(cls, value: str) -> str:
        return _require_timestamp(value)

    @model_validator(mode="after")
    def validate_manifest(self) -> "BudgetManifestV1":
        _revalidate(self.system_config, SystemConfigV1)
        _assert_identity(
            self,
            id_field="budget_manifest_id",
            sha_field="budget_manifest_sha256",
            prefix="budget-manifest",
        )
        return self


class BudgetManifestV2(StrictModel):
    """A formal cell budget exact-bound to every V3 upstream seal."""

    schema_version: Literal["flatband-budget-manifest-v2"] = (
        "flatband-budget-manifest-v2"
    )
    budget_manifest_id: Identifier
    budget_manifest_sha256: Sha256
    frozen_case_release_id: Identifier
    frozen_case_release_sha256: Sha256
    pre_run_eligibility_release_id: Identifier
    pre_run_eligibility_release_sha256: Sha256
    pre_budget_closure_release_id: Identifier
    pre_budget_closure_release_sha256: Sha256
    execution_matrix_id: Identifier
    execution_matrix_sha256: Sha256
    cell_id: Identifier
    cell_sha256: Sha256
    run_id: Identifier
    case_id: Identifier
    case_sha256: Sha256
    system_config: SystemConfigV1
    git_commit: Annotated[str, Field(pattern=r"^[0-9a-f]{40}$")]
    runtime_environment_sha256: Sha256
    analysis_environment_sha256: Sha256
    max_walltime_seconds: Annotated[int, Field(ge=1, le=3_600)]
    frozen_at: Annotated[str, Field(min_length=20, max_length=40)]
    legacy_budget_alias_allowed: Literal[False] = False
    scientific_conclusion: Literal[False] = False

    @field_validator("frozen_at")
    @classmethod
    def validate_frozen_at(cls, value: str) -> str:
        return _require_timestamp(value)

    @model_validator(mode="after")
    def validate_manifest(self) -> "BudgetManifestV2":
        _revalidate(self.system_config, SystemConfigV1)
        _assert_identity(
            self,
            id_field="budget_manifest_id",
            sha_field="budget_manifest_sha256",
            prefix="budget-manifest-v2",
        )
        return self


def build_budget_manifest_v2(
    *,
    execution_matrix: ExecutionMatrixV2,
    cell_id: str,
    frozen_case_release: FrozenCaseReleaseV3,
    pre_run_eligibility_release: PreRunEligibilityReleaseV3,
    pre_budget_closure_release: PilotPreBudgetClosureReleaseV3,
    run_id: str,
    git_commit: str,
    runtime_environment_sha256: str,
    analysis_environment_sha256: str,
    max_walltime_seconds: int,
    frozen_at: str,
) -> BudgetManifestV2:
    matrix = _revalidate(execution_matrix, ExecutionMatrixV2)
    frozen = _revalidate(frozen_case_release, FrozenCaseReleaseV3)
    eligibility = _revalidate(
        pre_run_eligibility_release, PreRunEligibilityReleaseV3
    )
    closure = _revalidate(
        pre_budget_closure_release, PilotPreBudgetClosureReleaseV3
    )
    assert_frozen_case_ready_v3(frozen)
    assert_pre_run_eligibility_ready_v3(eligibility)
    if frozen.pre_run_eligibility_release != eligibility:
        raise ValueError("V3 budget receives a foreign eligibility release")
    if closure.frozen_case_release != frozen:
        raise ValueError("V3 budget receives a foreign pre-budget closure")
    if matrix.split_manifest != frozen.split_manifest:
        raise ValueError("V3 budget matrix uses a foreign frozen split")
    if matrix.phase.value != closure.study_phase:
        raise ValueError("V3 budget matrix phase differs from pre-budget closure")
    cell_by_id = {item.cell_id: item for item in matrix.cells}
    cell = cell_by_id.get(cell_id)
    if cell is None:
        raise ValueError("V3 budget references a foreign execution cell")
    selected = {
        item.selected_case_id: item.selected_case_sha256
        for item in eligibility.active_selections
        if item.selected_case_id is not None
    }
    if selected.get(cell.case_id) != cell.case_sha256:
        raise ValueError("V3 budget cell is absent from eligible selection")
    config = {
        item.config_id: item for item in matrix.system_configs
    }[cell.system_config_id]
    timestamp = _require_timestamp(frozen_at)
    if _timestamp(timestamp) <= max(
        _timestamp(frozen.frozen_at),
        _timestamp(eligibility.sealed_at),
        _timestamp(closure.sealed_at),
    ):
        raise ValueError("V3 budget was not frozen after all formal seals")
    return _build_identified(
        BudgetManifestV2,
        id_field="budget_manifest_id",
        sha_field="budget_manifest_sha256",
        prefix="budget-manifest-v2",
        values={
            "frozen_case_release_id": frozen.release_id,
            "frozen_case_release_sha256": frozen.release_sha256,
            "pre_run_eligibility_release_id": eligibility.release_id,
            "pre_run_eligibility_release_sha256": eligibility.release_sha256,
            "pre_budget_closure_release_id": closure.release_id,
            "pre_budget_closure_release_sha256": closure.release_sha256,
            "execution_matrix_id": matrix.matrix_id,
            "execution_matrix_sha256": matrix.matrix_sha256,
            "cell_id": cell.cell_id,
            "cell_sha256": cell.cell_sha256,
            "run_id": run_id,
            "case_id": cell.case_id,
            "case_sha256": cell.case_sha256,
            "system_config": config,
            "git_commit": git_commit,
            "runtime_environment_sha256": runtime_environment_sha256,
            "analysis_environment_sha256": analysis_environment_sha256,
            "max_walltime_seconds": max_walltime_seconds,
            "frozen_at": timestamp,
        },
    )


class RankingPositionV1(StrictModel):
    selection_rank: Annotated[int, Field(ge=1, le=5)]
    packet_id: Identifier
    packet_sha256: Sha256


class ResearchRankingV1(StrictModel):
    schema_version: Literal["flatband-research-ranking-v1"] = (
        "flatband-research-ranking-v1"
    )
    ranking_id: Identifier
    ranking_sha256: Sha256
    budget_manifest_id: Identifier
    budget_manifest_sha256: Sha256
    cell_id: Identifier
    run_id: Identifier
    case_id: Identifier
    case_sha256: Sha256
    system_config_id: Identifier
    system_config_sha256: Sha256
    requested_top_k: Literal[5] = 5
    positions: Annotated[tuple[RankingPositionV1, ...], Field(max_length=5)] = ()
    underfill_reason_codes: Annotated[
        tuple[Identifier, ...], Field(max_length=16)
    ] = ()
    created_at: Annotated[str, Field(min_length=20, max_length=40)]
    scientific_conclusion: Literal[False] = False

    @field_validator("created_at")
    @classmethod
    def validate_created_at(cls, value: str) -> str:
        return _require_timestamp(value)

    @model_validator(mode="after")
    def validate_ranking(self) -> "ResearchRankingV1":
        ranks = tuple(item.selection_rank for item in self.positions)
        if ranks != tuple(range(1, len(self.positions) + 1)):
            raise ValueError("ranking positions must be contiguous from one")
        packet_ids = tuple(item.packet_id for item in self.positions)
        if len(packet_ids) != len(set(packet_ids)):
            raise ValueError("a ranking cannot repeat an exact packet")
        _require_sorted_unique(self.underfill_reason_codes, "underfill reasons")
        if len(self.positions) < 5 and not self.underfill_reason_codes:
            raise ValueError("underfilled or empty ranking requires a reason")
        if len(self.positions) == 5 and self.underfill_reason_codes:
            raise ValueError("full ranking cannot carry an underfill reason")
        _assert_identity(
            self,
            id_field="ranking_id",
            sha_field="ranking_sha256",
            prefix="research-ranking",
        )
        return self


RequestHost = Annotated[
    str,
    Field(
        min_length=1,
        max_length=253,
        pattern=r"^[A-Za-z0-9](?:[A-Za-z0-9.-]{0,251}[A-Za-z0-9])?$",
    ),
]

MetadataMediaType = Literal[
    "application/json",
    "application/xml",
    "application/atom+xml",
]

MetadataDataClass = Literal["PUBLIC_BIBLIOGRAPHIC_METADATA"]


def _logical_query_identity(
    *,
    source_id: str,
    query_plan_sha256: str,
    source_catalog_row_sha256: str,
    request_path_class: str,
    source_field_projection_sha256: str,
    normalized_query_sha256: str,
    filter_sha256: str,
    sort_sha256: str,
) -> str:
    """Replay a redacted query identity from its frozen semantic parts."""

    return canonical_sha256(
        {
            "source_id": source_id,
            "query_plan_sha256": query_plan_sha256,
            "source_catalog_row_sha256": source_catalog_row_sha256,
            "request_path_class": request_path_class,
            "source_field_projection_sha256": source_field_projection_sha256,
            "normalized_query_sha256": normalized_query_sha256,
            "filter_sha256": filter_sha256,
            "sort_sha256": sort_sha256,
        }
    )


def _page_query_identity(
    *, query_identity_sha256: str, page_number: int, cursor_sha256: str | None
) -> str:
    return canonical_sha256(
        {
            "query_identity_sha256": query_identity_sha256,
            "page_number": page_number,
            "cursor_sha256": cursor_sha256,
        }
    )


def _request_path_identity(
    *, request_path_template_sha256: str, path_parameters_sha256: str
) -> str:
    return canonical_sha256(
        {
            "request_path_template_sha256": request_path_template_sha256,
            "path_parameters_sha256": path_parameters_sha256,
        }
    )


def _transport_request_identity(
    *,
    source_id: str,
    request_method: str,
    request_host: str,
    request_path_sha256: str,
    request_query_sha256: str,
    data_class: str,
) -> str:
    return canonical_sha256(
        {
            "source_id": source_id,
            "request_method": request_method,
            "request_host": request_host,
            "request_path_sha256": request_path_sha256,
            "request_query_sha256": request_query_sha256,
            "data_class": data_class,
        }
    )


class PhysicalHopReceiptV1(StrictModel):
    """One real HTTP hop; redirects are separate, counted receipts."""

    schema_version: Literal["flatband-physical-hop-receipt-v1"] = (
        "flatband-physical-hop-receipt-v1"
    )
    hop_receipt_id: Identifier
    hop_receipt_sha256: Sha256
    source_id: Identifier
    budget_manifest_id: Identifier
    budget_manifest_sha256: Sha256
    cell_id: Identifier
    run_id: Identifier
    system_config_id: Identifier
    system_config_sha256: Sha256
    logical_page_receipt_id: Identifier
    logical_page_receipt_sha256: Sha256
    hop_index: Annotated[int, Field(ge=1, le=8)]
    request_method: Literal["GET"] = "GET"
    request_host: RequestHost
    request_path_class: ShortText
    request_path_template_sha256: Sha256
    path_parameters_sha256: Sha256
    request_path_sha256: Sha256
    request_query_sha256: Sha256
    data_class: MetadataDataClass = (
        "PUBLIC_BIBLIOGRAPHIC_METADATA"
    )
    request_identity_sha256: Sha256
    response_status_code: Annotated[int, Field(ge=100, le=599)]
    response_media_type: MetadataMediaType
    response_identity_sha256: Sha256
    response_bytes: Annotated[int, Field(ge=0, le=100_000_000)]
    redirect_target_request_sha256: Sha256 | None = None
    completed_at: Annotated[str, Field(min_length=20, max_length=40)]

    @field_validator("completed_at")
    @classmethod
    def validate_completed_at(cls, value: str) -> str:
        return _require_timestamp(value)

    @model_validator(mode="after")
    def validate_hop(self) -> "PhysicalHopReceiptV1":
        expected_path = _request_path_identity(
            request_path_template_sha256=self.request_path_template_sha256,
            path_parameters_sha256=self.path_parameters_sha256,
        )
        if self.request_path_sha256 != expected_path:
            raise ValueError("physical request path does not match its frozen template")
        expected_request = _transport_request_identity(
            source_id=self.source_id,
            request_method=self.request_method,
            request_host=self.request_host,
            request_path_sha256=self.request_path_sha256,
            request_query_sha256=self.request_query_sha256,
            data_class=self.data_class,
        )
        if self.request_identity_sha256 != expected_request:
            raise ValueError("physical request identity does not match transport metadata")
        is_redirect = self.response_status_code in _REDIRECT_STATUS_CODES
        if is_redirect != (self.redirect_target_request_sha256 is not None):
            raise ValueError("redirect status and target request identity must agree")
        _assert_identity(
            self,
            id_field="hop_receipt_id",
            sha_field="hop_receipt_sha256",
            prefix="physical-hop-receipt",
        )
        return self


class CacheInventoryEntryV1(StrictModel):
    """Frozen cache-snapshot entry; metadata identities only, never response text."""

    schema_version: Literal["flatband-cache-inventory-entry-v1"] = (
        "flatband-cache-inventory-entry-v1"
    )
    cache_entry_id: Identifier
    cache_entry_sha256: Sha256
    source_id: Identifier
    cache_snapshot_sha256: Sha256
    cache_key_sha256: Sha256
    request_method: Literal["GET"] = "GET"
    request_host: RequestHost
    request_path_class: ShortText
    request_path_template_sha256: Sha256
    path_parameters_sha256: Sha256
    request_path_sha256: Sha256
    request_query_sha256: Sha256
    data_class: MetadataDataClass = (
        "PUBLIC_BIBLIOGRAPHIC_METADATA"
    )
    request_identity_sha256: Sha256
    response_status_code: Annotated[int, Field(ge=100, le=599)]
    response_media_type: MetadataMediaType
    response_identity_sha256: Sha256
    response_bytes: Annotated[int, Field(ge=0, le=100_000_000)]
    record_identity_sha256s: Annotated[
        tuple[Sha256, ...], Field(max_length=10_000)
    ] = ()
    document_identity_sha256s: Annotated[
        tuple[Sha256, ...], Field(max_length=10_000)
    ] = ()

    @model_validator(mode="after")
    def validate_entry(self) -> "CacheInventoryEntryV1":
        expected_path = _request_path_identity(
            request_path_template_sha256=self.request_path_template_sha256,
            path_parameters_sha256=self.path_parameters_sha256,
        )
        if self.request_path_sha256 != expected_path:
            raise ValueError("cache request path does not match its frozen template")
        expected_request = _transport_request_identity(
            source_id=self.source_id,
            request_method=self.request_method,
            request_host=self.request_host,
            request_path_sha256=self.request_path_sha256,
            request_query_sha256=self.request_query_sha256,
            data_class=self.data_class,
        )
        if self.request_identity_sha256 != expected_request:
            raise ValueError("cache request identity does not match transport metadata")
        _require_sorted_unique(
            self.record_identity_sha256s, "cache record identities"
        )
        _require_sorted_unique(
            self.document_identity_sha256s, "cache document identities"
        )
        if len(self.document_identity_sha256s) > len(self.record_identity_sha256s):
            raise ValueError("cache unique documents cannot exceed records")
        if self.response_status_code >= 400 and (
            self.record_identity_sha256s or self.document_identity_sha256s
        ):
            raise ValueError("failed cache response cannot carry records")
        if self.response_status_code in _REDIRECT_STATUS_CODES:
            raise ValueError("cache inventory must store a terminal response")
        _assert_identity(
            self,
            id_field="cache_entry_id",
            sha_field="cache_entry_sha256",
            prefix="cache-inventory-entry",
        )
        return self


class LogicalQueryReceiptV1(StrictModel):
    """Redacted, content-addressed logical query provenance.

    Query text and provider credentials are not stored.  The normalized query,
    filters and sort are represented by hashes whose combination is replayed
    here; external execution authenticity remains an explicit unclosed seam.
    """

    schema_version: Literal["flatband-logical-query-receipt-v1"] = (
        "flatband-logical-query-receipt-v1"
    )
    query_receipt_id: Identifier
    query_receipt_sha256: Sha256
    source_id: Identifier
    budget_manifest_id: Identifier
    budget_manifest_sha256: Sha256
    cell_id: Identifier
    run_id: Identifier
    system_config_id: Identifier
    system_config_sha256: Sha256
    logical_query_id: Identifier
    query_plan_sha256: Sha256
    source_catalog_sha256: Literal[SOURCE_CATALOG_V1_SHA256] = (
        SOURCE_CATALOG_V1_SHA256
    )
    source_catalog_row_sha256: Sha256
    request_path_class: ShortText
    request_path_template_sha256: Sha256
    source_field_projection_sha256: Sha256
    normalized_query_sha256: Sha256
    filter_sha256: Sha256
    sort_sha256: Sha256
    query_identity_sha256: Sha256
    created_at: Annotated[str, Field(min_length=20, max_length=40)]
    external_provider_attestation: Literal["NOT_PROVIDED"] = "NOT_PROVIDED"

    @field_validator("created_at")
    @classmethod
    def validate_created_at(cls, value: str) -> str:
        return _require_timestamp(value)

    @model_validator(mode="after")
    def validate_query(self) -> "LogicalQueryReceiptV1":
        expected_identity = _logical_query_identity(
            source_id=self.source_id,
            query_plan_sha256=self.query_plan_sha256,
            source_catalog_row_sha256=self.source_catalog_row_sha256,
            request_path_class=self.request_path_class,
            source_field_projection_sha256=self.source_field_projection_sha256,
            normalized_query_sha256=self.normalized_query_sha256,
            filter_sha256=self.filter_sha256,
            sort_sha256=self.sort_sha256,
        )
        if self.query_identity_sha256 != expected_identity:
            raise ValueError("query identity is not replayable from query provenance")
        expected_id = deterministic_id(
            "logical-query",
            {
                "source_id": self.source_id,
                "query_plan_sha256": self.query_plan_sha256,
                "query_identity_sha256": expected_identity,
            },
        )
        if self.logical_query_id != expected_id:
            raise ValueError("logical query ID does not match query provenance")
        _assert_identity(
            self,
            id_field="query_receipt_id",
            sha_field="query_receipt_sha256",
            prefix="logical-query-receipt",
        )
        return self


class LogicalPageReceiptV1(StrictModel):
    """Bounded metadata-page receipt; it never stores response text or a URL."""

    schema_version: Literal["flatband-logical-page-receipt-v1"] = (
        "flatband-logical-page-receipt-v1"
    )
    page_receipt_id: Identifier
    page_receipt_sha256: Sha256
    source_id: Identifier
    budget_manifest_id: Identifier
    budget_manifest_sha256: Sha256
    cell_id: Identifier
    run_id: Identifier
    system_config_id: Identifier
    system_config_sha256: Sha256
    logical_query_id: Identifier
    query_receipt_id: Identifier
    query_receipt_sha256: Sha256
    query_plan_sha256: Sha256
    query_identity_sha256: Sha256
    page_number: Annotated[int, Field(ge=1, le=128)]
    cursor_sha256: Sha256 | None = None
    request_method: Literal["GET"] = "GET"
    request_host: RequestHost
    request_path_class: ShortText
    request_path_template_sha256: Sha256
    path_parameters_sha256: Sha256
    request_path_sha256: Sha256
    request_query_sha256: Sha256
    data_class: MetadataDataClass = (
        "PUBLIC_BIBLIOGRAPHIC_METADATA"
    )
    request_identity_sha256: Sha256
    cache_disposition: CacheDisposition
    cache_key_sha256: Sha256
    cache_entry_id: Identifier | None = None
    cache_entry_sha256: Sha256 | None = None
    response_status_code: Annotated[int, Field(ge=100, le=599)]
    response_media_type: MetadataMediaType
    response_identity_sha256: Sha256
    response_bytes: Annotated[int, Field(ge=0, le=100_000_000)]
    record_projection_sha256: Sha256
    source_field_projection_sha256: Sha256
    record_identity_sha256s: Annotated[
        tuple[Sha256, ...], Field(max_length=10_000)
    ] = ()
    document_identity_sha256s: Annotated[
        tuple[Sha256, ...], Field(max_length=10_000)
    ] = ()
    completed_at: Annotated[str, Field(min_length=20, max_length=40)]

    @field_validator("completed_at")
    @classmethod
    def validate_completed_at(cls, value: str) -> str:
        return _require_timestamp(value)

    @model_validator(mode="after")
    def validate_page(self) -> "LogicalPageReceiptV1":
        expected_query_id = deterministic_id(
            "logical-query",
            {
                "source_id": self.source_id,
                "query_plan_sha256": self.query_plan_sha256,
                "query_identity_sha256": self.query_identity_sha256,
            },
        )
        if self.logical_query_id != expected_query_id:
            raise ValueError("logical query ID does not match query identity")
        expected_page_query = _page_query_identity(
            query_identity_sha256=self.query_identity_sha256,
            page_number=self.page_number,
            cursor_sha256=self.cursor_sha256,
        )
        if self.request_query_sha256 != expected_page_query:
            raise ValueError("page request query does not replay from logical query")
        expected_path = _request_path_identity(
            request_path_template_sha256=self.request_path_template_sha256,
            path_parameters_sha256=self.path_parameters_sha256,
        )
        if self.request_path_sha256 != expected_path:
            raise ValueError("logical request path does not match its frozen template")
        expected_request = _transport_request_identity(
            source_id=self.source_id,
            request_method=self.request_method,
            request_host=self.request_host,
            request_path_sha256=self.request_path_sha256,
            request_query_sha256=self.request_query_sha256,
            data_class=self.data_class,
        )
        if self.request_identity_sha256 != expected_request:
            raise ValueError("logical request identity does not match transport metadata")
        has_cache_entry = self.cache_entry_id is not None or self.cache_entry_sha256 is not None
        if (self.cache_entry_id is None) != (self.cache_entry_sha256 is None):
            raise ValueError("cache entry ID and SHA must be present together")
        if (self.cache_disposition is CacheDisposition.CACHE_HIT) != has_cache_entry:
            raise ValueError("cache hit must bind one frozen cache inventory entry")
        _require_sorted_unique(
            self.record_identity_sha256s, "page record identities"
        )
        _require_sorted_unique(
            self.document_identity_sha256s, "page document identities"
        )
        if len(self.document_identity_sha256s) > len(self.record_identity_sha256s):
            raise ValueError("page unique documents cannot exceed records")
        if self.response_status_code >= 400 and (
            self.record_identity_sha256s or self.document_identity_sha256s
        ):
            raise ValueError("failed metadata response cannot carry records")
        if self.response_status_code in _REDIRECT_STATUS_CODES:
            raise ValueError("logical page must record the terminal, non-redirect response")
        _assert_identity(
            self,
            id_field="page_receipt_id",
            sha_field="page_receipt_sha256",
            prefix="logical-page-receipt",
        )
        return self


class NormalizedMetadataFieldV1(StrictModel):
    """One private, bounded, content-addressed normalized metadata field."""

    schema_version: Literal["flatband-normalized-metadata-field-v1"] = (
        "flatband-normalized-metadata-field-v1"
    )
    field_artifact_id: Identifier
    field_artifact_sha256: Sha256
    field_name: ShortText
    json_path: ShortText
    value_utf8: Annotated[str, Field(min_length=1, max_length=16_384)]
    value_utf8_sha256: Sha256
    value_utf8_bytes: Annotated[int, Field(ge=1, le=65_536)]
    provenance_scope: Literal["INTERNAL_REPLAY_NOT_EXTERNAL_ATTESTATION"] = (
        "INTERNAL_REPLAY_NOT_EXTERNAL_ATTESTATION"
    )
    private_storage_required: Literal[True] = True
    public_release_allowed: Literal[False] = False

    @model_validator(mode="after")
    def validate_field(self) -> "NormalizedMetadataFieldV1":
        if self.json_path != _metadata_json_path(self.field_name):
            raise ValueError("metadata field JSON path must identify its exact top-level field")
        encoded = self.value_utf8.encode("utf-8")
        if self.value_utf8_bytes != len(encoded):
            raise ValueError("metadata field byte count does not match its UTF-8 preimage")
        if self.value_utf8_sha256 != _utf8_sha256(self.value_utf8):
            raise ValueError("metadata field SHA does not match its UTF-8 preimage")
        _assert_identity(
            self,
            id_field="field_artifact_id",
            sha_field="field_artifact_sha256",
            prefix="metadata-field-artifact",
        )
        return self


class NormalizedMetadataArtifactV1(StrictModel):
    """Private normalized metadata preimage; never an article-body artifact."""

    schema_version: Literal["flatband-normalized-metadata-artifact-v1"] = (
        "flatband-normalized-metadata-artifact-v1"
    )
    artifact_id: Identifier
    artifact_sha256: Sha256
    source_id: Identifier
    budget_manifest_id: Identifier
    budget_manifest_sha256: Sha256
    cell_id: Identifier
    run_id: Identifier
    system_config_id: Identifier
    system_config_sha256: Sha256
    source_record_id: Identifier
    source_url_sha256: Sha256
    source_field_projection_sha256: Sha256
    normalized_metadata_sha256: Sha256
    fields: Annotated[
        tuple[NormalizedMetadataFieldV1, ...], Field(min_length=1, max_length=64)
    ]
    provenance_scope: Literal["INTERNAL_REPLAY_NOT_EXTERNAL_ATTESTATION"] = (
        "INTERNAL_REPLAY_NOT_EXTERNAL_ATTESTATION"
    )
    private_storage_required: Literal[True] = True
    public_release_allowed: Literal[False] = False
    external_provider_attestation: Literal["NOT_PROVIDED"] = "NOT_PROVIDED"

    @model_validator(mode="after")
    def validate_artifact(self) -> "NormalizedMetadataArtifactV1":
        fields = tuple(_revalidate(item, NormalizedMetadataFieldV1) for item in self.fields)
        keys = tuple((item.field_name, item.json_path) for item in fields)
        if keys != tuple(sorted(set(keys))):
            raise ValueError("normalized metadata fields must be name/path sorted and unique")
        if len({item.field_artifact_id for item in fields}) != len(
            {item.field_artifact_sha256 for item in fields}
        ):
            raise ValueError("metadata field artifact IDs and hashes must be one-to-one")
        if sum(item.value_utf8_bytes for item in fields) > 65_536:
            raise ValueError("normalized metadata artifact exceeds its private byte bound")
        policy = source_policy_values(self.source_id)
        if self.source_field_projection_sha256 != policy[
            "source_field_projection_sha256"
        ]:
            raise ValueError("metadata artifact uses a foreign field projection")
        projected_fields = set(policy["projected_fields"])
        if any(item.field_name not in projected_fields for item in fields):
            raise ValueError("metadata artifact contains a non-projected or body field")
        expected_metadata_sha256 = canonical_sha256(
            tuple(
                {
                    "field_name": item.field_name,
                    "json_path": item.json_path,
                    "value_utf8": item.value_utf8,
                    "value_utf8_sha256": item.value_utf8_sha256,
                }
                for item in fields
            )
        )
        if self.normalized_metadata_sha256 != expected_metadata_sha256:
            raise ValueError("normalized metadata SHA does not replay from private fields")
        _assert_identity(
            self,
            id_field="artifact_id",
            sha_field="artifact_sha256",
            prefix="normalized-metadata-artifact",
        )
        return self


class MetadataRecordReceiptV1(StrictModel):
    """Normalized metadata record identity without response or article body."""

    schema_version: Literal["flatband-metadata-record-receipt-v1"] = (
        "flatband-metadata-record-receipt-v1"
    )
    record_receipt_id: Identifier
    record_receipt_sha256: Sha256
    source_id: Identifier
    budget_manifest_id: Identifier
    budget_manifest_sha256: Sha256
    cell_id: Identifier
    run_id: Identifier
    system_config_id: Identifier
    system_config_sha256: Sha256
    page_receipt_id: Identifier
    page_receipt_sha256: Sha256
    source_record_id: Identifier
    source_url_sha256: Sha256
    normalized_metadata_artifact_id: Identifier
    normalized_metadata_artifact_sha256: Sha256
    normalized_metadata_sha256: Sha256
    source_field_projection_sha256: Sha256
    record_identity_sha256: Sha256
    document_identity_sha256: Sha256 | None = None
    external_provider_attestation: Literal["NOT_PROVIDED"] = "NOT_PROVIDED"

    @model_validator(mode="after")
    def validate_record(self) -> "MetadataRecordReceiptV1":
        expected = canonical_sha256(
            {
                "source_id": self.source_id,
                "source_record_id": self.source_record_id,
                "source_url_sha256": self.source_url_sha256,
                "normalized_metadata_sha256": self.normalized_metadata_sha256,
                "source_field_projection_sha256": (
                    self.source_field_projection_sha256
                ),
            }
        )
        if self.record_identity_sha256 != expected:
            raise ValueError("record identity does not replay from normalized metadata")
        _assert_identity(
            self,
            id_field="record_receipt_id",
            sha_field="record_receipt_sha256",
            prefix="metadata-record-receipt",
        )
        return self


class MetadataSpanPreimageV1(StrictModel):
    """Private exact UTF-8 byte-span preimage for one metadata record field."""

    schema_version: Literal["flatband-metadata-span-preimage-v1"] = (
        "flatband-metadata-span-preimage-v1"
    )
    span_preimage_id: Identifier
    span_preimage_sha256: Sha256
    source_id: Identifier
    budget_manifest_id: Identifier
    budget_manifest_sha256: Sha256
    cell_id: Identifier
    run_id: Identifier
    system_config_id: Identifier
    system_config_sha256: Sha256
    record_receipt_id: Identifier
    record_receipt_sha256: Sha256
    normalized_metadata_artifact_id: Identifier
    normalized_metadata_artifact_sha256: Sha256
    field_artifact_id: Identifier
    field_artifact_sha256: Sha256
    field_name: ShortText
    json_path: ShortText
    span_id: Identifier
    start_byte: Annotated[int, Field(ge=0, le=65_535)]
    end_byte: Annotated[int, Field(ge=1, le=65_536)]
    span_utf8: Annotated[str, Field(min_length=1, max_length=4_096)]
    span_utf8_sha256: Sha256
    span_utf8_bytes: Annotated[int, Field(ge=1, le=16_384)]
    provenance_scope: Literal["INTERNAL_REPLAY_NOT_EXTERNAL_ATTESTATION"] = (
        "INTERNAL_REPLAY_NOT_EXTERNAL_ATTESTATION"
    )
    private_storage_required: Literal[True] = True
    public_release_allowed: Literal[False] = False
    external_provider_attestation: Literal["NOT_PROVIDED"] = "NOT_PROVIDED"

    @model_validator(mode="after")
    def validate_span(self) -> "MetadataSpanPreimageV1":
        encoded = self.span_utf8.encode("utf-8")
        if self.span_utf8_bytes != len(encoded):
            raise ValueError("metadata span byte count does not match its UTF-8 preimage")
        if self.end_byte - self.start_byte != len(encoded):
            raise ValueError("metadata span offsets do not match its UTF-8 byte length")
        if self.span_utf8_sha256 != _utf8_sha256(self.span_utf8):
            raise ValueError("metadata span SHA does not match its UTF-8 preimage")
        if self.json_path != _metadata_json_path(self.field_name):
            raise ValueError("metadata span JSON path does not match its field")
        _assert_identity(
            self,
            id_field="span_preimage_id",
            sha_field="span_preimage_sha256",
            prefix="metadata-span-preimage",
        )
        return self


class EvidenceLinkReceiptV1(StrictModel):
    """Exact join from a packet link to one private normalized-metadata span."""

    schema_version: Literal["flatband-evidence-link-receipt-v1"] = (
        "flatband-evidence-link-receipt-v1"
    )
    evidence_receipt_id: Identifier
    evidence_receipt_sha256: Sha256
    source_id: Identifier
    budget_manifest_id: Identifier
    budget_manifest_sha256: Sha256
    cell_id: Identifier
    run_id: Identifier
    system_config_id: Identifier
    system_config_sha256: Sha256
    record_receipt_id: Identifier
    record_receipt_sha256: Sha256
    normalized_metadata_artifact_id: Identifier
    normalized_metadata_artifact_sha256: Sha256
    field_artifact_id: Identifier
    field_artifact_sha256: Sha256
    packet_id: Identifier
    packet_sha256: Sha256
    evidence_link_id: Identifier
    evidence_link_sha256: Sha256
    source_record_id: Identifier
    source_url_sha256: Sha256
    span_id: Identifier
    span_sha256: Sha256
    span_field: ShortText
    metadata_json_path: ShortText
    span_start_byte: Annotated[int, Field(ge=0, le=65_535)]
    span_end_byte: Annotated[int, Field(ge=1, le=65_536)]
    span_utf8: Annotated[str, Field(min_length=1, max_length=4_096)]
    span_utf8_sha256: Sha256
    span_utf8_bytes: Annotated[int, Field(ge=1, le=16_384)]
    span_preimage_id: Identifier
    span_preimage_sha256: Sha256
    span_locator_sha256: Sha256
    private_text_artifact_uri_sha256: Sha256 | None = None
    provenance_scope: Literal["INTERNAL_REPLAY_NOT_EXTERNAL_ATTESTATION"] = (
        "INTERNAL_REPLAY_NOT_EXTERNAL_ATTESTATION"
    )
    external_provider_attestation: Literal["NOT_PROVIDED"] = "NOT_PROVIDED"

    @model_validator(mode="after")
    def validate_evidence(self) -> "EvidenceLinkReceiptV1":
        encoded = self.span_utf8.encode("utf-8")
        if self.span_utf8_bytes != len(encoded):
            raise ValueError("evidence span byte count does not match its UTF-8 preimage")
        if self.span_end_byte - self.span_start_byte != len(encoded):
            raise ValueError("evidence span offsets do not match its UTF-8 byte length")
        if self.span_utf8_sha256 != _utf8_sha256(self.span_utf8):
            raise ValueError("evidence span SHA does not match its UTF-8 preimage")
        if self.span_sha256 != self.span_utf8_sha256:
            raise ValueError("packet span SHA does not match the exact metadata span bytes")
        if self.metadata_json_path != _metadata_json_path(self.span_field):
            raise ValueError("evidence JSON path does not match its metadata field")
        expected_locator = _evidence_span_locator_sha256(
            record_receipt_id=self.record_receipt_id,
            record_receipt_sha256=self.record_receipt_sha256,
            artifact_sha256=self.normalized_metadata_artifact_sha256,
            field_artifact_id=self.field_artifact_id,
            field_artifact_sha256=self.field_artifact_sha256,
            field_name=self.span_field,
            json_path=self.metadata_json_path,
            start_byte=self.span_start_byte,
            end_byte=self.span_end_byte,
            span_utf8=self.span_utf8,
            span_utf8_sha256=self.span_utf8_sha256,
        )
        if self.span_locator_sha256 != expected_locator:
            raise ValueError(
                "evidence span locator does not replay from record/artifact/field/span preimage"
            )
        _assert_identity(
            self,
            id_field="evidence_receipt_id",
            sha_field="evidence_receipt_sha256",
            prefix="evidence-link-receipt",
        )
        return self


class ActualSourceUsageV1(StrictModel):
    source_id: Identifier
    receipt_bundle_id: Identifier
    receipt_bundle_sha256: Sha256
    physical_requests: Annotated[int, Field(ge=0, le=8)]
    logical_queries: Annotated[int, Field(ge=0, le=128)]
    pages: Annotated[int, Field(ge=0, le=128)]
    records: Annotated[int, Field(ge=0, le=100_000)]
    response_bytes: Annotated[int, Field(ge=0, le=100_000_000)]
    unique_documents: Annotated[int, Field(ge=0, le=20_000)]
    cache_hits: Annotated[int, Field(ge=0, le=100_000)]

    @model_validator(mode="after")
    def validate_usage(self) -> "ActualSourceUsageV1":
        if self.unique_documents > self.records:
            raise ValueError("actual unique documents cannot exceed records")
        if self.cache_hits > self.pages:
            raise ValueError("actual cache hits cannot exceed pages")
        # A frozen cache page may lawfully replay records with no network hop.
        # Its conservation is checked against the cache inventory and record
        # receipts, rather than by assuming every record requires a live hop.
        return self


class SourceReceiptBundleV1(StrictModel):
    schema_version: Literal["flatband-source-receipt-bundle-v1"] = (
        "flatband-source-receipt-bundle-v1"
    )
    receipt_bundle_id: Identifier
    receipt_bundle_sha256: Sha256
    source_id: Identifier
    retrieval_identity_sha256: Sha256
    cache_snapshot_sha256: Sha256
    budget_manifest_id: Identifier | None = None
    budget_manifest_sha256: Sha256 | None = None
    cell_id: Identifier | None = None
    run_id: Identifier | None = None
    system_config_id: Identifier | None = None
    system_config_sha256: Sha256 | None = None
    source_catalog_sha256: Literal[SOURCE_CATALOG_V1_SHA256] = (
        SOURCE_CATALOG_V1_SHA256
    )
    provenance_scope: Literal["INTERNAL_REPLAY_NOT_EXTERNAL_ATTESTATION"] = (
        "INTERNAL_REPLAY_NOT_EXTERNAL_ATTESTATION"
    )
    cache_inventory_entries: Annotated[
        tuple[CacheInventoryEntryV1, ...], Field(max_length=128)
    ] = ()
    logical_queries: Annotated[
        tuple[LogicalQueryReceiptV1, ...], Field(max_length=128)
    ] = ()
    logical_pages: Annotated[
        tuple[LogicalPageReceiptV1, ...], Field(max_length=128)
    ] = ()
    metadata_records: Annotated[
        tuple[MetadataRecordReceiptV1, ...], Field(max_length=100_000)
    ] = ()
    normalized_metadata_artifacts: Annotated[
        tuple[NormalizedMetadataArtifactV1, ...], Field(max_length=100_000)
    ] = ()
    metadata_span_preimages: Annotated[
        tuple[MetadataSpanPreimageV1, ...], Field(max_length=320)
    ] = ()
    evidence_links: Annotated[
        tuple[EvidenceLinkReceiptV1, ...], Field(max_length=320)
    ] = ()
    physical_hops: Annotated[
        tuple[PhysicalHopReceiptV1, ...], Field(max_length=8)
    ] = ()

    @model_validator(mode="after")
    def validate_bundle(self) -> "SourceReceiptBundleV1":
        binding = (
            self.budget_manifest_id,
            self.budget_manifest_sha256,
            self.cell_id,
            self.run_id,
            self.system_config_id,
            self.system_config_sha256,
        )
        if any(value is None for value in binding) and any(
            value is not None for value in binding
        ):
            raise ValueError("source bundle execution binding must be all present or absent")
        cache_entries = tuple(
            _revalidate(item, CacheInventoryEntryV1)
            for item in self.cache_inventory_entries
        )
        queries = tuple(
            _revalidate(item, LogicalQueryReceiptV1)
            for item in self.logical_queries
        )
        pages = tuple(_revalidate(item, LogicalPageReceiptV1) for item in self.logical_pages)
        records = tuple(
            _revalidate(item, MetadataRecordReceiptV1)
            for item in self.metadata_records
        )
        artifacts = tuple(
            _revalidate(item, NormalizedMetadataArtifactV1)
            for item in self.normalized_metadata_artifacts
        )
        span_preimages = tuple(
            _revalidate(item, MetadataSpanPreimageV1)
            for item in self.metadata_span_preimages
        )
        evidence = tuple(
            _revalidate(item, EvidenceLinkReceiptV1)
            for item in self.evidence_links
        )
        hops = tuple(_revalidate(item, PhysicalHopReceiptV1) for item in self.physical_hops)
        query_keys = tuple(
            (item.logical_query_id, item.query_receipt_id) for item in queries
        )
        if query_keys != tuple(sorted(set(query_keys))):
            raise ValueError("logical query receipts must be query-ID sorted and unique")
        query_by_id = {item.query_receipt_id: item for item in queries}
        if len(query_by_id) != len({item.query_receipt_sha256 for item in queries}):
            raise ValueError("logical query receipt IDs and hashes must be one-to-one")
        page_keys = tuple(
            (item.logical_query_id, item.page_number, item.page_receipt_id)
            for item in pages
        )
        if page_keys != tuple(sorted(set(page_keys))):
            raise ValueError("logical pages must be query/page sorted and unique")
        logical_page_keys = tuple(
            (item.logical_query_id, item.page_number) for item in pages
        )
        if len(logical_page_keys) != len(set(logical_page_keys)):
            raise ValueError("logical query cannot repeat a page number")
        for query_id in {item.logical_query_id for item in pages}:
            page_numbers = tuple(
                item.page_number for item in pages if item.logical_query_id == query_id
            )
            if page_numbers != tuple(range(1, len(page_numbers) + 1)):
                raise ValueError("logical query pages must be contiguous from one")
        page_by_id = {item.page_receipt_id: item for item in pages}
        if len(page_by_id) != len({item.page_receipt_sha256 for item in pages}):
            raise ValueError("logical page receipt IDs and hashes must be one-to-one")
        record_keys = tuple(
            (item.page_receipt_id, item.source_record_id, item.record_receipt_id)
            for item in records
        )
        if record_keys != tuple(sorted(set(record_keys))):
            raise ValueError("metadata records must be page/record sorted and unique")
        record_by_id = {item.record_receipt_id: item for item in records}
        if len(record_by_id) != len({item.record_receipt_sha256 for item in records}):
            raise ValueError("metadata record receipt IDs and hashes must be one-to-one")
        artifact_keys = tuple(
            (item.source_record_id, item.artifact_id) for item in artifacts
        )
        if artifact_keys != tuple(sorted(set(artifact_keys))):
            raise ValueError("normalized metadata artifacts must be record sorted and unique")
        artifact_by_id = {item.artifact_id: item for item in artifacts}
        if len(artifact_by_id) != len({item.artifact_sha256 for item in artifacts}):
            raise ValueError("normalized metadata artifact IDs and hashes must be one-to-one")
        span_preimage_keys = tuple(
            (
                item.record_receipt_id,
                item.field_artifact_id,
                item.start_byte,
                item.end_byte,
                item.span_preimage_id,
            )
            for item in span_preimages
        )
        if span_preimage_keys != tuple(sorted(set(span_preimage_keys))):
            raise ValueError("metadata span preimages must be record/field/offset sorted and unique")
        span_preimage_by_id = {
            item.span_preimage_id: item for item in span_preimages
        }
        if len(span_preimage_by_id) != len(
            {item.span_preimage_sha256 for item in span_preimages}
        ):
            raise ValueError("metadata span preimage IDs and hashes must be one-to-one")
        evidence_keys = tuple(
            (item.packet_id, item.evidence_link_id, item.evidence_receipt_id)
            for item in evidence
        )
        if evidence_keys != tuple(sorted(set(evidence_keys))):
            raise ValueError("evidence receipts must be packet/link sorted and unique")
        packet_link_keys = tuple(
            (item.packet_id, item.evidence_link_id) for item in evidence
        )
        if len(packet_link_keys) != len(set(packet_link_keys)):
            raise ValueError("one packet evidence link cannot alias multiple receipts")
        packet_semantic_keys = tuple(
            (item.packet_id, item.evidence_link_sha256) for item in evidence
        )
        if len(packet_semantic_keys) != len(set(packet_semantic_keys)):
            raise ValueError("one packet evidence semantic cannot use multiple aliases")
        if len({item.evidence_receipt_id for item in evidence}) != len(
            {item.evidence_receipt_sha256 for item in evidence}
        ):
            raise ValueError("evidence receipt IDs and hashes must be one-to-one")
        cache_keys = tuple(
            (item.cache_key_sha256, item.cache_entry_id) for item in cache_entries
        )
        if cache_keys != tuple(sorted(set(cache_keys))):
            raise ValueError("cache inventory entries must be key-sorted and unique")
        cache_by_id = {item.cache_entry_id: item for item in cache_entries}
        hop_keys = tuple(
            (item.logical_page_receipt_id, item.hop_index, item.hop_receipt_id)
            for item in hops
        )
        if hop_keys != tuple(sorted(set(hop_keys))):
            raise ValueError("physical hops must be page/index sorted and unique")
        logical_hop_keys = tuple(
            (item.logical_page_receipt_id, item.hop_index) for item in hops
        )
        if len(logical_hop_keys) != len(set(logical_hop_keys)):
            raise ValueError("logical page cannot repeat a physical hop index")
        for item in (
            *cache_entries,
            *queries,
            *pages,
            *records,
            *artifacts,
            *span_preimages,
            *evidence,
            *hops,
        ):
            if item.source_id != self.source_id:
                raise ValueError("receipt source differs from its source bundle")
        if any((queries, pages, records, artifacts, span_preimages, evidence, hops)):
            if any(value is None for value in binding):
                raise ValueError("non-empty source bundle requires an execution binding")
            for item in (
                *queries,
                *pages,
                *records,
                *artifacts,
                *span_preimages,
                *evidence,
                *hops,
            ):
                if (
                    item.budget_manifest_id,
                    item.budget_manifest_sha256,
                    item.cell_id,
                    item.run_id,
                    item.system_config_id,
                    item.system_config_sha256,
                ) != binding:
                    raise ValueError("nested receipt binds another cell/run")
        referenced_query_ids: set[str] = set()
        for page in pages:
            query = query_by_id.get(page.query_receipt_id)
            if query is None or query.query_receipt_sha256 != page.query_receipt_sha256:
                raise ValueError("logical page references a foreign query receipt")
            if (
                page.logical_query_id,
                page.query_plan_sha256,
                page.query_identity_sha256,
                page.request_path_class,
                page.request_path_template_sha256,
                page.source_field_projection_sha256,
            ) != (
                query.logical_query_id,
                query.query_plan_sha256,
                query.query_identity_sha256,
                query.request_path_class,
                query.request_path_template_sha256,
                query.source_field_projection_sha256,
            ):
                raise ValueError("logical page drifts from its query provenance")
            if _timestamp(page.completed_at) < _timestamp(query.created_at):
                raise ValueError("logical page completed before its query was created")
            referenced_query_ids.add(query.query_receipt_id)
        if referenced_query_ids != set(query_by_id):
            raise ValueError("logical query receipts contain a missing or orphan query")
        for entry in cache_entries:
            if entry.cache_snapshot_sha256 != self.cache_snapshot_sha256:
                raise ValueError("cache inventory entry binds another snapshot")
        for hop in hops:
            page = page_by_id.get(hop.logical_page_receipt_id)
            if page is None or hop.logical_page_receipt_sha256 != page.page_receipt_sha256:
                raise ValueError("physical hop references a foreign logical page")
        for page in pages:
            page_hops = tuple(
                item for item in hops if item.logical_page_receipt_id == page.page_receipt_id
            )
            if page.cache_disposition is CacheDisposition.CACHE_HIT:
                if page_hops:
                    raise ValueError("cache-hit page cannot carry physical hops")
                entry = cache_by_id.get(page.cache_entry_id or "")
                if entry is None or entry.cache_entry_sha256 != page.cache_entry_sha256:
                    raise ValueError("cache-hit page references a foreign inventory entry")
                if (
                    page.cache_key_sha256,
                    page.request_method,
                    page.request_host,
                    page.request_path_class,
                    page.request_path_template_sha256,
                    page.path_parameters_sha256,
                    page.request_path_sha256,
                    page.request_query_sha256,
                    page.data_class,
                    page.request_identity_sha256,
                    page.response_status_code,
                    page.response_media_type,
                    page.response_identity_sha256,
                    page.response_bytes,
                    page.record_identity_sha256s,
                    page.document_identity_sha256s,
                ) != (
                    entry.cache_key_sha256,
                    entry.request_method,
                    entry.request_host,
                    entry.request_path_class,
                    entry.request_path_template_sha256,
                    entry.path_parameters_sha256,
                    entry.request_path_sha256,
                    entry.request_query_sha256,
                    entry.data_class,
                    entry.request_identity_sha256,
                    entry.response_status_code,
                    entry.response_media_type,
                    entry.response_identity_sha256,
                    entry.response_bytes,
                    entry.record_identity_sha256s,
                    entry.document_identity_sha256s,
                ):
                    raise ValueError("cache-hit page differs from frozen inventory content")
                continue
            if not page_hops:
                raise ValueError("network page requires at least one physical hop")
            if tuple(item.hop_index for item in page_hops) != tuple(
                range(1, len(page_hops) + 1)
            ):
                raise ValueError("physical hop indexes must be contiguous from one")
            first = page_hops[0]
            if (
                first.request_method,
                first.request_host,
                first.request_path_class,
                first.request_path_template_sha256,
                first.path_parameters_sha256,
                first.request_path_sha256,
                first.request_query_sha256,
                first.data_class,
                first.request_identity_sha256,
            ) != (
                page.request_method,
                page.request_host,
                page.request_path_class,
                page.request_path_template_sha256,
                page.path_parameters_sha256,
                page.request_path_sha256,
                page.request_query_sha256,
                page.data_class,
                page.request_identity_sha256,
            ):
                raise ValueError("first physical request differs from logical page request")
            for current, following in zip(page_hops, page_hops[1:]):
                if (
                    current.response_status_code not in _REDIRECT_STATUS_CODES
                    or current.redirect_target_request_sha256
                    != following.request_identity_sha256
                ):
                    raise ValueError("physical redirect chain is not closed")
                if _timestamp(following.completed_at) < _timestamp(current.completed_at):
                    raise ValueError("physical hop completion order is reversed")
            final = page_hops[-1]
            if final.response_status_code in _REDIRECT_STATUS_CODES:
                raise ValueError("physical redirect chain has no terminal response")
            if (
                final.response_status_code,
                final.response_media_type,
                final.response_identity_sha256,
                final.response_bytes,
            ) != (
                page.response_status_code,
                page.response_media_type,
                page.response_identity_sha256,
                page.response_bytes,
            ):
                raise ValueError("logical page does not match its terminal physical response")
            if _timestamp(final.completed_at) > _timestamp(page.completed_at):
                raise ValueError("logical page completed before its terminal physical hop")

        page_record_pairs = {
            (page.page_receipt_id, identity)
            for page in pages
            for identity in page.record_identity_sha256s
        }
        record_pairs: set[tuple[str, str]] = set()
        source_record_to_identity: dict[str, str] = {}
        record_identity_to_source: dict[str, str] = {}
        for record in records:
            page = page_by_id.get(record.page_receipt_id)
            if page is None or page.page_receipt_sha256 != record.page_receipt_sha256:
                raise ValueError("metadata record references a foreign logical page")
            if record.source_field_projection_sha256 != page.source_field_projection_sha256:
                raise ValueError("metadata record uses a foreign field projection")
            record_pairs.add((record.page_receipt_id, record.record_identity_sha256))
            prior_identity = source_record_to_identity.setdefault(
                record.source_record_id, record.record_identity_sha256
            )
            if prior_identity != record.record_identity_sha256:
                raise ValueError("one source record ID aliases multiple record identities")
            prior_source = record_identity_to_source.setdefault(
                record.record_identity_sha256, record.source_record_id
            )
            if prior_source != record.source_record_id:
                raise ValueError("one record identity aliases multiple source record IDs")
        if record_pairs != page_record_pairs:
            raise ValueError("metadata records do not exactly replay page record identities")
        referenced_artifact_ids: list[str] = []
        for record in records:
            artifact = artifact_by_id.get(record.normalized_metadata_artifact_id)
            if (
                artifact is None
                or artifact.artifact_sha256
                != record.normalized_metadata_artifact_sha256
            ):
                raise ValueError(
                    "metadata record references a missing or foreign normalized artifact"
                )
            if (
                artifact.source_id,
                artifact.source_record_id,
                artifact.source_url_sha256,
                artifact.source_field_projection_sha256,
                artifact.normalized_metadata_sha256,
            ) != (
                record.source_id,
                record.source_record_id,
                record.source_url_sha256,
                record.source_field_projection_sha256,
                record.normalized_metadata_sha256,
            ):
                raise ValueError("normalized metadata artifact drifts from its record")
            referenced_artifact_ids.append(artifact.artifact_id)
        if (
            len(referenced_artifact_ids) != len(set(referenced_artifact_ids))
            or set(referenced_artifact_ids) != set(artifact_by_id)
        ):
            raise ValueError(
                "normalized metadata artifacts do not exactly cover metadata records"
            )
        for page in pages:
            page_documents = {
                item.document_identity_sha256
                for item in records
                if item.page_receipt_id == page.page_receipt_id
                and item.document_identity_sha256 is not None
            }
            if page_documents != set(page.document_identity_sha256s):
                raise ValueError("metadata records do not replay page document identities")

        referenced_span_preimage_ids: set[str] = set()
        for item in evidence:
            record = record_by_id.get(item.record_receipt_id)
            if record is None or record.record_receipt_sha256 != item.record_receipt_sha256:
                raise ValueError("evidence receipt references a foreign metadata record")
            if (
                item.source_id,
                item.source_record_id,
                item.source_url_sha256,
            ) != (
                record.source_id,
                record.source_record_id,
                record.source_url_sha256,
            ):
                raise ValueError("evidence receipt drifts from its metadata record")
            artifact = artifact_by_id.get(item.normalized_metadata_artifact_id)
            if (
                artifact is None
                or artifact.artifact_sha256
                != item.normalized_metadata_artifact_sha256
                or artifact.artifact_id != record.normalized_metadata_artifact_id
                or artifact.artifact_sha256
                != record.normalized_metadata_artifact_sha256
            ):
                raise ValueError("evidence receipt references a foreign metadata artifact")
            fields_by_id = {
                field.field_artifact_id: field for field in artifact.fields
            }
            field = fields_by_id.get(item.field_artifact_id)
            if (
                field is None
                or field.field_artifact_sha256 != item.field_artifact_sha256
                or (field.field_name, field.json_path)
                != (item.span_field, item.metadata_json_path)
            ):
                raise ValueError("evidence receipt references a foreign metadata field")
            preimage = span_preimage_by_id.get(item.span_preimage_id)
            if (
                preimage is None
                or preimage.span_preimage_sha256 != item.span_preimage_sha256
            ):
                raise ValueError("evidence receipt references a missing or foreign span preimage")
            if (
                preimage.source_id,
                preimage.record_receipt_id,
                preimage.record_receipt_sha256,
                preimage.normalized_metadata_artifact_id,
                preimage.normalized_metadata_artifact_sha256,
                preimage.field_artifact_id,
                preimage.field_artifact_sha256,
                preimage.field_name,
                preimage.json_path,
                preimage.span_id,
                preimage.start_byte,
                preimage.end_byte,
                preimage.span_utf8,
                preimage.span_utf8_sha256,
                preimage.span_utf8_bytes,
            ) != (
                item.source_id,
                item.record_receipt_id,
                item.record_receipt_sha256,
                item.normalized_metadata_artifact_id,
                item.normalized_metadata_artifact_sha256,
                item.field_artifact_id,
                item.field_artifact_sha256,
                item.span_field,
                item.metadata_json_path,
                item.span_id,
                item.span_start_byte,
                item.span_end_byte,
                item.span_utf8,
                item.span_utf8_sha256,
                item.span_utf8_bytes,
            ):
                raise ValueError("evidence receipt drifts from its exact span preimage")
            field_bytes = field.value_utf8.encode("utf-8")
            if field_bytes[preimage.start_byte : preimage.end_byte] != (
                preimage.span_utf8.encode("utf-8")
            ):
                raise ValueError(
                    "metadata span preimage does not replay from field/path/offset bytes"
                )
            referenced_span_preimage_ids.add(preimage.span_preimage_id)
        if referenced_span_preimage_ids != set(span_preimage_by_id):
            raise ValueError(
                "metadata span preimages do not exactly cover evidence receipts"
            )
        referenced_cache_entries = {
            item.cache_entry_id
            for item in pages
            if item.cache_disposition is CacheDisposition.CACHE_HIT
        }
        if referenced_cache_entries != set(cache_by_id):
            raise ValueError("cache inventory slice contains missing or orphaned entries")
        _assert_identity(
            self,
            id_field="receipt_bundle_id",
            sha_field="receipt_bundle_sha256",
            prefix="source-receipt-bundle",
        )
        return self


def replay_source_usage(bundle: SourceReceiptBundleV1) -> ActualSourceUsageV1:
    """Deterministically derive source usage; caller-supplied counters are not trusted."""

    bundle = _revalidate(bundle, SourceReceiptBundleV1)
    cache_pages = tuple(
        item
        for item in bundle.logical_pages
        if item.cache_disposition is CacheDisposition.CACHE_HIT
    )
    response_bytes = sum(item.response_bytes for item in bundle.physical_hops) + sum(
        item.response_bytes for item in cache_pages
    )
    records = sum(len(item.record_identity_sha256s) for item in bundle.logical_pages)
    unique_documents = {
        document
        for item in bundle.logical_pages
        for document in item.document_identity_sha256s
    }
    return ActualSourceUsageV1(
        source_id=bundle.source_id,
        receipt_bundle_id=bundle.receipt_bundle_id,
        receipt_bundle_sha256=bundle.receipt_bundle_sha256,
        physical_requests=len(bundle.physical_hops),
        logical_queries=len({item.logical_query_id for item in bundle.logical_pages}),
        pages=len(bundle.logical_pages),
        records=records,
        response_bytes=response_bytes,
        unique_documents=len(unique_documents),
        cache_hits=len(cache_pages),
    )


class MetadataModelInputRefV1(StrictModel):
    """Exact same-cell normalized metadata record supplied to a model."""

    source_id: Identifier
    metadata_packet_id: Identifier
    metadata_packet_sha256: Sha256
    receipt_bundle_id: Identifier
    receipt_bundle_sha256: Sha256
    record_receipt_id: Identifier
    record_receipt_sha256: Sha256
    record_identity_sha256: Sha256

    @model_validator(mode="after")
    def validate_packet(self) -> "MetadataModelInputRefV1":
        expected_sha256 = canonical_sha256(
            {
                "source_id": self.source_id,
                "receipt_bundle_id": self.receipt_bundle_id,
                "receipt_bundle_sha256": self.receipt_bundle_sha256,
                "record_receipt_id": self.record_receipt_id,
                "record_receipt_sha256": self.record_receipt_sha256,
                "record_identity_sha256": self.record_identity_sha256,
            }
        )
        if self.metadata_packet_sha256 != expected_sha256:
            raise ValueError("metadata packet identity does not replay from its record")
        if self.metadata_packet_id != deterministic_id(
            "model-metadata-packet",
            {"metadata_packet_sha256": expected_sha256},
        ):
            raise ValueError("metadata packet ID does not match its content")
        return self


class LlmInvocationReceiptV1(StrictModel):
    """Internally replayable LLM call accounting, without provider transcript."""

    schema_version: Literal["flatband-llm-invocation-receipt-v1"] = (
        "flatband-llm-invocation-receipt-v1"
    )
    invocation_id: Identifier
    invocation_sha256: Sha256
    budget_manifest_id: Identifier
    budget_manifest_sha256: Sha256
    cell_id: Identifier
    run_id: Identifier
    system_config_id: Identifier
    system_config_sha256: Sha256
    call_index: Annotated[int, Field(ge=1, le=2)]
    provider: ShortText
    model: ShortText
    revision: ShortText
    prompt_sha256: Sha256
    tokenizer_sha256: Sha256
    output_schema_sha256: Sha256
    metadata_inputs: Annotated[
        tuple[MetadataModelInputRefV1, ...], Field(min_length=1, max_length=20)
    ]
    input_payload_sha256: Sha256
    tokenized_input_sha256: Sha256
    input_tokens: Annotated[int, Field(ge=1, le=12_000)]
    output_tokens: Annotated[int, Field(ge=0, le=16_000)]
    output_identity_sha256: Sha256
    started_at: Annotated[str, Field(min_length=20, max_length=40)]
    completed_at: Annotated[str, Field(min_length=20, max_length=40)]
    provider_attestation: Literal["NOT_PROVIDED"] = "NOT_PROVIDED"

    @field_validator("started_at", "completed_at")
    @classmethod
    def validate_timestamp(cls, value: str) -> str:
        return _require_timestamp(value)

    @model_validator(mode="after")
    def validate_invocation(self) -> "LlmInvocationReceiptV1":
        keys = tuple(
            (item.source_id, item.record_receipt_id) for item in self.metadata_inputs
        )
        if keys != tuple(sorted(set(keys))):
            raise ValueError("LLM metadata inputs must be source/record sorted and unique")
        expected_payload = canonical_sha256(
            {
                "prompt_sha256": self.prompt_sha256,
                "output_schema_sha256": self.output_schema_sha256,
                "metadata_inputs": tuple(
                    item.model_dump(mode="python") for item in self.metadata_inputs
                ),
            }
        )
        if self.input_payload_sha256 != expected_payload:
            raise ValueError("LLM input payload does not replay from packet identities")
        if _timestamp(self.completed_at) < _timestamp(self.started_at):
            raise ValueError("LLM invocation completion precedes start")
        _assert_identity(
            self,
            id_field="invocation_id",
            sha_field="invocation_sha256",
            prefix="llm-invocation-receipt",
        )
        return self


class LocalModelInvocationReceiptV1(StrictModel):
    """Internally replayable local embedding invocation accounting."""

    schema_version: Literal["flatband-local-model-invocation-receipt-v1"] = (
        "flatband-local-model-invocation-receipt-v1"
    )
    invocation_id: Identifier
    invocation_sha256: Sha256
    budget_manifest_id: Identifier
    budget_manifest_sha256: Sha256
    cell_id: Identifier
    run_id: Identifier
    system_config_id: Identifier
    system_config_sha256: Sha256
    invocation_index: Annotated[int, Field(ge=1, le=128)]
    bundle_sha256: Sha256
    tokenizer_sha256: Sha256
    model_card_sha256: Sha256
    license_manifest_sha256: Sha256
    vector_dimension: Annotated[int, Field(ge=1, le=65_536)]
    metadata_inputs: Annotated[
        tuple[MetadataModelInputRefV1, ...], Field(min_length=1, max_length=20_000)
    ]
    input_payload_sha256: Sha256
    tokenized_input_sha256: Sha256
    input_tokens: Annotated[int, Field(ge=1, le=1_000_000)]
    output_vectors_sha256: Sha256
    started_at: Annotated[str, Field(min_length=20, max_length=40)]
    completed_at: Annotated[str, Field(min_length=20, max_length=40)]
    execution_attestation: Literal["INTERNAL_REPLAY_ONLY"] = "INTERNAL_REPLAY_ONLY"

    @field_validator("started_at", "completed_at")
    @classmethod
    def validate_timestamp(cls, value: str) -> str:
        return _require_timestamp(value)

    @model_validator(mode="after")
    def validate_invocation(self) -> "LocalModelInvocationReceiptV1":
        keys = tuple(
            (item.source_id, item.record_receipt_id) for item in self.metadata_inputs
        )
        if keys != tuple(sorted(set(keys))):
            raise ValueError(
                "local-model metadata inputs must be source/record sorted and unique"
            )
        expected_payload = canonical_sha256(
            {
                "metadata_inputs": tuple(
                    item.model_dump(mode="python") for item in self.metadata_inputs
                ),
                "bundle_sha256": self.bundle_sha256,
                "tokenizer_sha256": self.tokenizer_sha256,
            }
        )
        if self.input_payload_sha256 != expected_payload:
            raise ValueError(
                "local-model input payload does not replay from packet identities"
            )
        if _timestamp(self.completed_at) < _timestamp(self.started_at):
            raise ValueError("local-model invocation completion precedes start")
        _assert_identity(
            self,
            id_field="invocation_id",
            sha_field="invocation_sha256",
            prefix="local-model-invocation-receipt",
        )
        return self


def replay_llm_usage(
    receipts: tuple[LlmInvocationReceiptV1, ...],
) -> tuple[int, int, int, int]:
    validated = tuple(_revalidate(item, LlmInvocationReceiptV1) for item in receipts)
    unique_inputs = {
        (item.metadata_packet_id, item.metadata_packet_sha256)
        for receipt in validated
        for item in receipt.metadata_inputs
    }
    return (
        len(validated),
        sum(item.input_tokens for item in validated),
        sum(item.output_tokens for item in validated),
        len(unique_inputs),
    )


def replay_local_model_usage(
    receipts: tuple[LocalModelInvocationReceiptV1, ...],
) -> int:
    validated = tuple(
        _revalidate(item, LocalModelInvocationReceiptV1) for item in receipts
    )
    return sum(item.input_tokens for item in validated)


class TerminalRunResultV1(StrictModel):
    schema_version: Literal["flatband-terminal-run-result-v1"] = (
        "flatband-terminal-run-result-v1"
    )
    terminal_result_id: Identifier
    terminal_result_sha256: Sha256
    budget_manifest_id: Identifier
    budget_manifest_sha256: Sha256
    ranking_id: Identifier | None = None
    ranking_sha256: Sha256 | None = None
    cell_id: Identifier
    run_id: Identifier
    case_id: Identifier
    case_sha256: Sha256
    system_config_id: Identifier
    system_config_sha256: Sha256
    git_commit: Annotated[str, Field(pattern=r"^[0-9a-f]{40}$")]
    runtime_environment_sha256: Sha256
    status: RunCellStatus
    source_receipt_bundles: Annotated[
        tuple[SourceReceiptBundleV1, ...], Field(min_length=1, max_length=4)
    ]
    source_usage: Annotated[
        tuple[ActualSourceUsageV1, ...], Field(min_length=1, max_length=4)
    ]
    llm_invocation_receipts: Annotated[
        tuple[LlmInvocationReceiptV1, ...], Field(max_length=2)
    ] = ()
    local_model_invocation_receipts: Annotated[
        tuple[LocalModelInvocationReceiptV1, ...], Field(max_length=128)
    ] = ()
    actual_llm_calls: Annotated[int, Field(ge=0, le=2)] = 0
    actual_llm_input_tokens: Annotated[int, Field(ge=0, le=12_000)] = 0
    actual_llm_output_tokens: Annotated[int, Field(ge=0, le=16_000)] = 0
    actual_llm_metadata_packets: Annotated[int, Field(ge=0, le=20)] = 0
    actual_local_model_input_tokens: Annotated[
        int, Field(ge=0, le=1_000_000)
    ] = 0
    walltime_ms: Annotated[int, Field(ge=0, le=3_600_000)]
    article_body_fetch_requests: Literal[0] = 0
    full_pdf_reads: Literal[0] = 0
    failure_reason_codes: Annotated[
        tuple[Identifier, ...], Field(max_length=32)
    ] = ()
    completed_at: Annotated[str, Field(min_length=20, max_length=40)]
    scientific_conclusion: Literal[False] = False

    @field_validator("completed_at")
    @classmethod
    def validate_completed_at(cls, value: str) -> str:
        return _require_timestamp(value)

    @model_validator(mode="after")
    def validate_terminal(self) -> "TerminalRunResultV1":
        bundles = tuple(
            _revalidate(item, SourceReceiptBundleV1)
            for item in self.source_receipt_bundles
        )
        bundle_source_ids = tuple(item.source_id for item in bundles)
        if bundle_source_ids != tuple(sorted(set(bundle_source_ids))):
            raise ValueError("source receipt bundles must be source-ID sorted and unique")
        if len({item.receipt_bundle_id for item in bundles}) != len(
            {item.receipt_bundle_sha256 for item in bundles}
        ):
            raise ValueError("source bundle IDs and hashes must be one-to-one")
        expected_binding = (
            self.budget_manifest_id,
            self.budget_manifest_sha256,
            self.cell_id,
            self.run_id,
            self.system_config_id,
            self.system_config_sha256,
        )
        for bundle in bundles:
            if (
                bundle.budget_manifest_id,
                bundle.budget_manifest_sha256,
                bundle.cell_id,
                bundle.run_id,
                bundle.system_config_id,
                bundle.system_config_sha256,
            ) != expected_binding:
                raise ValueError("source receipt bundle does not bind its terminal cell/run")
        source_ids = tuple(item.source_id for item in self.source_usage)
        if source_ids != tuple(sorted(set(source_ids))):
            raise ValueError("actual source usage must be source-ID sorted and unique")
        replayed = tuple(replay_source_usage(item) for item in bundles)
        if self.source_usage != replayed:
            raise ValueError("actual source usage must be replayed from source receipts")
        llm_receipts = tuple(
            _revalidate(item, LlmInvocationReceiptV1)
            for item in self.llm_invocation_receipts
        )
        local_receipts = tuple(
            _revalidate(item, LocalModelInvocationReceiptV1)
            for item in self.local_model_invocation_receipts
        )
        if tuple(item.call_index for item in llm_receipts) != tuple(
            range(1, len(llm_receipts) + 1)
        ):
            raise ValueError("LLM invocation indexes must be contiguous from one")
        if tuple(item.invocation_index for item in local_receipts) != tuple(
            range(1, len(local_receipts) + 1)
        ):
            raise ValueError("local-model invocation indexes must be contiguous from one")
        if len({item.invocation_id for item in (*llm_receipts, *local_receipts)}) != (
            len(llm_receipts) + len(local_receipts)
        ):
            raise ValueError("model invocation IDs must be unique in one run")
        bundle_by_id = {item.receipt_bundle_id: item for item in bundles}
        for receipt in (*llm_receipts, *local_receipts):
            if (
                receipt.budget_manifest_id,
                receipt.budget_manifest_sha256,
                receipt.cell_id,
                receipt.run_id,
                receipt.system_config_id,
                receipt.system_config_sha256,
            ) != expected_binding:
                raise ValueError("model invocation receipt binds another cell/run")
            if _timestamp(receipt.completed_at) > _timestamp(self.completed_at):
                raise ValueError("model invocation completed after terminal result")
            for input_ref in receipt.metadata_inputs:
                bundle = bundle_by_id.get(input_ref.receipt_bundle_id)
                if (
                    bundle is None
                    or bundle.receipt_bundle_sha256 != input_ref.receipt_bundle_sha256
                    or bundle.source_id != input_ref.source_id
                ):
                    raise ValueError("model input references a foreign source bundle")
                records = {
                    item.record_receipt_id: item for item in bundle.metadata_records
                }
                record = records.get(input_ref.record_receipt_id)
                if (
                    record is None
                    or record.record_receipt_sha256
                    != input_ref.record_receipt_sha256
                    or record.record_identity_sha256
                    != input_ref.record_identity_sha256
                ):
                    raise ValueError("model input references a foreign metadata record")
        replayed_llm = replay_llm_usage(llm_receipts)
        if (
            self.actual_llm_calls,
            self.actual_llm_input_tokens,
            self.actual_llm_output_tokens,
            self.actual_llm_metadata_packets,
        ) != replayed_llm:
            raise ValueError("actual LLM usage must be replayed from invocation receipts")
        if self.actual_local_model_input_tokens != replay_local_model_usage(
            local_receipts
        ):
            raise ValueError(
                "actual local-model usage must be replayed from invocation receipts"
            )
        _require_sorted_unique(self.failure_reason_codes, "terminal failure reasons")
        has_ranking = self.ranking_id is not None or self.ranking_sha256 is not None
        if (self.ranking_id is None) != (self.ranking_sha256 is None):
            raise ValueError("ranking ID and SHA must be present together")
        if self.status is RunCellStatus.SUCCEEDED:
            if not has_ranking or self.failure_reason_codes:
                raise ValueError("successful run requires ranking and no failures")
        elif self.status is RunCellStatus.PARTIAL:
            if not has_ranking or not self.failure_reason_codes:
                raise ValueError("partial run requires ranking and failure reasons")
        elif has_ranking or not self.failure_reason_codes:
            raise ValueError("failed run requires reasons and no ranking")
        _assert_identity(
            self,
            id_field="terminal_result_id",
            sha_field="terminal_result_sha256",
            prefix="terminal-result",
        )
        return self


class ProjectedTop5PositionV1(StrictModel):
    position: Annotated[int, Field(ge=1, le=5)]
    packet_id: Identifier | None = None
    packet_sha256: Sha256 | None = None
    forced_zero: bool
    fixed_gain: Literal[0] | None = None
    missing_reason: MissingPositionReason | None = None

    @model_validator(mode="after")
    def validate_position(self) -> "ProjectedTop5PositionV1":
        present = self.packet_id is not None or self.packet_sha256 is not None
        if (self.packet_id is None) != (self.packet_sha256 is None):
            raise ValueError("projected packet ID and SHA must be present together")
        if present and (
            self.forced_zero
            or self.fixed_gain is not None
            or self.missing_reason is not None
        ):
            raise ValueError("present ranking position cannot be forced to zero")
        if not present and (
            not self.forced_zero
            or self.fixed_gain != 0
            or self.missing_reason is None
        ):
            raise ValueError("missing ranking position must be forced to zero")
        return self


class Top5ProjectionV1(StrictModel):
    schema_version: Literal["flatband-top5-projection-v1"] = (
        "flatband-top5-projection-v1"
    )
    projection_id: Identifier
    projection_sha256: Sha256
    cell_id: Identifier
    terminal_result_id: Identifier
    terminal_result_sha256: Sha256
    ranking_id: Identifier | None = None
    ranking_sha256: Sha256 | None = None
    status: RunCellStatus
    positions: Annotated[
        tuple[ProjectedTop5PositionV1, ...], Field(min_length=5, max_length=5)
    ]
    denominator_size: Literal[5] = 5
    scientific_conclusion: Literal[False] = False

    @model_validator(mode="after")
    def validate_projection(self) -> "Top5ProjectionV1":
        if tuple(item.position for item in self.positions) != (1, 2, 3, 4, 5):
            raise ValueError("Top-5 projection must contain positions one through five")
        if (self.ranking_id is None) != (self.ranking_sha256 is None):
            raise ValueError("projection ranking ID and SHA must be present together")
        _assert_identity(
            self,
            id_field="projection_id",
            sha_field="projection_sha256",
            prefix="top5-projection",
        )
        return self


class ExecutionReleaseV1(StrictModel):
    schema_version: Literal["flatband-execution-release-v1"] = (
        "flatband-execution-release-v1"
    )
    release_id: Identifier
    release_sha256: Sha256
    git_commit: Annotated[str, Field(pattern=r"^[0-9a-f]{40}$")]
    runtime_environment_sha256: Sha256
    analysis_environment_sha256: Sha256
    execution_matrix: ExecutionMatrixV1
    budget_manifests: tuple[BudgetManifestV1, ...]
    rankings: tuple[ResearchRankingV1, ...]
    hypothesis_packets: tuple[HypothesisPacketV1, ...]
    terminal_results: tuple[TerminalRunResultV1, ...]
    top5_projections: tuple[Top5ProjectionV1, ...]
    assembled_at: Annotated[str, Field(min_length=20, max_length=40)]
    complete_case_omission_allowed: Literal[False] = False
    scientific_conclusion: Literal[False] = False

    @field_validator("assembled_at")
    @classmethod
    def validate_assembled_at(cls, value: str) -> str:
        return _require_timestamp(value)

    @model_validator(mode="after")
    def validate_release(self) -> "ExecutionReleaseV1":
        _validate_release_closure(
            self.execution_matrix,
            self.budget_manifests,
            self.rankings,
            self.hypothesis_packets,
            self.terminal_results,
            self.top5_projections,
            assembled_at=self.assembled_at,
            git_commit=self.git_commit,
            runtime_environment_sha256=self.runtime_environment_sha256,
            analysis_environment_sha256=self.analysis_environment_sha256,
        )
        _assert_identity(
            self,
            id_field="release_id",
            sha_field="release_sha256",
            prefix="execution-release",
        )
        return self


class ExecutionReleaseV2(StrictModel):
    """Unpublished legacy draft whose denominator is a split-V2 matrix."""

    schema_version: Literal["flatband-execution-release-v2"] = (
        "flatband-execution-release-v2"
    )
    release_id: Identifier
    release_sha256: Sha256
    git_commit: Annotated[str, Field(pattern=r"^[0-9a-f]{40}$")]
    runtime_environment_sha256: Sha256
    analysis_environment_sha256: Sha256
    execution_matrix: ExecutionMatrixV2
    budget_manifests: tuple[BudgetManifestV1, ...]
    rankings: tuple[ResearchRankingV1, ...]
    hypothesis_packets: tuple[HypothesisPacketV1, ...]
    terminal_results: tuple[TerminalRunResultV1, ...]
    top5_projections: tuple[Top5ProjectionV1, ...]
    assembled_at: Annotated[str, Field(min_length=20, max_length=40)]
    complete_case_omission_allowed: Literal[False] = False
    scientific_conclusion: Literal[False] = False

    @field_validator("assembled_at")
    @classmethod
    def validate_assembled_at(cls, value: str) -> str:
        return _require_timestamp(value)

    @model_validator(mode="after")
    def validate_release(self) -> "ExecutionReleaseV2":
        _validate_release_closure(
            self.execution_matrix,
            self.budget_manifests,
            self.rankings,
            self.hypothesis_packets,
            self.terminal_results,
            self.top5_projections,
            assembled_at=self.assembled_at,
            git_commit=self.git_commit,
            runtime_environment_sha256=self.runtime_environment_sha256,
            analysis_environment_sha256=self.analysis_environment_sha256,
        )
        _assert_identity(
            self,
            id_field="release_id",
            sha_field="release_sha256",
            prefix="execution-release-v2",
        )
        return self


class ExecutionReleaseV3(StrictModel):
    """The unique formal Pilot execution release for the acyclic V3 chain."""

    schema_version: Literal["flatband-execution-release-v3"] = (
        "flatband-execution-release-v3"
    )
    release_id: Identifier
    release_sha256: Sha256
    frozen_case_release_id: Identifier
    frozen_case_release_sha256: Sha256
    pre_run_eligibility_release_id: Identifier
    pre_run_eligibility_release_sha256: Sha256
    pre_budget_closure_release_id: Identifier
    pre_budget_closure_release_sha256: Sha256
    frozen_case_release: FrozenCaseReleaseV3
    pre_run_eligibility_release: PreRunEligibilityReleaseV3
    pre_budget_closure_release: PilotPreBudgetClosureReleaseV3
    git_commit: Annotated[str, Field(pattern=r"^[0-9a-f]{40}$")]
    runtime_environment_sha256: Sha256
    analysis_environment_sha256: Sha256
    execution_matrix: ExecutionMatrixV2
    budget_manifests: tuple[BudgetManifestV2, ...]
    rankings: tuple[ResearchRankingV1, ...]
    hypothesis_packets: tuple[HypothesisPacketV1, ...]
    terminal_results: tuple[TerminalRunResultV1, ...]
    top5_projections: tuple[Top5ProjectionV1, ...]
    assembled_at: Annotated[str, Field(min_length=20, max_length=40)]
    complete_case_omission_allowed: Literal[False] = False
    alternate_eligibility_alias_allowed: Literal[False] = False
    legacy_execution_v1_v2_formal_alias_allowed: Literal[False] = False
    scientific_conclusion: Literal[False] = False

    @field_validator("assembled_at")
    @classmethod
    def validate_assembled_at(cls, value: str) -> str:
        return _require_timestamp(value)

    @model_validator(mode="after")
    def validate_release(self) -> "ExecutionReleaseV3":
        frozen = _revalidate(self.frozen_case_release, FrozenCaseReleaseV3)
        eligibility = _revalidate(
            self.pre_run_eligibility_release, PreRunEligibilityReleaseV3
        )
        closure = _revalidate(
            self.pre_budget_closure_release,
            PilotPreBudgetClosureReleaseV3,
        )
        matrix = _revalidate(self.execution_matrix, ExecutionMatrixV2)
        budgets = tuple(
            _revalidate(item, BudgetManifestV2)
            for item in self.budget_manifests
        )
        assert_frozen_case_ready_v3(frozen)
        assert_pre_run_eligibility_ready_v3(eligibility)
        if frozen.pre_run_eligibility_release != eligibility:
            raise ValueError("ExecutionReleaseV3 crosswires frozen and eligibility")
        if closure.frozen_case_release != frozen:
            raise ValueError("ExecutionReleaseV3 crosswires pre-budget closure")
        if (
            self.frozen_case_release_id,
            self.frozen_case_release_sha256,
            self.pre_run_eligibility_release_id,
            self.pre_run_eligibility_release_sha256,
            self.pre_budget_closure_release_id,
            self.pre_budget_closure_release_sha256,
        ) != (
            frozen.release_id,
            frozen.release_sha256,
            eligibility.release_id,
            eligibility.release_sha256,
            closure.release_id,
            closure.release_sha256,
        ):
            raise ValueError("ExecutionReleaseV3 formal release references do not replay")
        if matrix.split_manifest != frozen.split_manifest:
            raise ValueError("ExecutionReleaseV3 matrix uses a foreign split")
        if matrix.phase.value != closure.study_phase:
            raise ValueError("ExecutionReleaseV3 matrix uses a foreign Pilot phase")
        expected_binding = (
            frozen.release_id,
            frozen.release_sha256,
            eligibility.release_id,
            eligibility.release_sha256,
            closure.release_id,
            closure.release_sha256,
        )
        for budget in budgets:
            if (
                budget.frozen_case_release_id,
                budget.frozen_case_release_sha256,
                budget.pre_run_eligibility_release_id,
                budget.pre_run_eligibility_release_sha256,
                budget.pre_budget_closure_release_id,
                budget.pre_budget_closure_release_sha256,
            ) != expected_binding:
                raise ValueError("V3 budget uses a foreign formal release chain")
            if _timestamp(budget.frozen_at) <= max(
                _timestamp(frozen.frozen_at),
                _timestamp(eligibility.sealed_at),
                _timestamp(closure.sealed_at),
            ):
                raise ValueError("V3 budget predates a formal upstream seal")
        selected = {
            item.selected_case_id: item.selected_case_sha256
            for item in eligibility.active_selections
            if item.selected_case_id is not None
        }
        if any(
            selected.get(cell.case_id) != cell.case_sha256
            for cell in matrix.cells
        ):
            raise ValueError("ExecutionReleaseV3 contains an ineligible case cell")
        _validate_release_closure(
            matrix,
            budgets,
            self.rankings,
            self.hypothesis_packets,
            self.terminal_results,
            self.top5_projections,
            assembled_at=self.assembled_at,
            git_commit=self.git_commit,
            runtime_environment_sha256=self.runtime_environment_sha256,
            analysis_environment_sha256=self.analysis_environment_sha256,
            budget_model_type=BudgetManifestV2,
        )
        _assert_identity(
            self,
            id_field="release_id",
            sha_field="release_sha256",
            prefix="execution-release-v3",
        )
        return self


def _project_top5(
    terminal: TerminalRunResultV1,
    ranking: ResearchRankingV1 | None,
) -> Top5ProjectionV1:
    positions: list[ProjectedTop5PositionV1] = []
    returned = () if ranking is None else ranking.positions
    for position in range(1, 6):
        if position <= len(returned):
            item = returned[position - 1]
            positions.append(
                ProjectedTop5PositionV1(
                    position=position,
                    packet_id=item.packet_id,
                    packet_sha256=item.packet_sha256,
                    forced_zero=False,
                )
            )
        else:
            if terminal.status is RunCellStatus.FAILED:
                reason = MissingPositionReason.RUN_FAILED
            elif not returned:
                reason = MissingPositionReason.EMPTY_RANKING
            else:
                reason = MissingPositionReason.UNDERFILL
            positions.append(
                ProjectedTop5PositionV1(
                    position=position,
                    forced_zero=True,
                    fixed_gain=0,
                    missing_reason=reason,
                )
            )
    return _build_identified(
        Top5ProjectionV1,
        id_field="projection_id",
        sha_field="projection_sha256",
        prefix="top5-projection",
        values={
            "cell_id": terminal.cell_id,
            "terminal_result_id": terminal.terminal_result_id,
            "terminal_result_sha256": terminal.terminal_result_sha256,
            "ranking_id": None if ranking is None else ranking.ranking_id,
            "ranking_sha256": None if ranking is None else ranking.ranking_sha256,
            "status": terminal.status,
            "positions": tuple(positions),
        },
    )


def _exact_map(
    values: tuple[ModelT, ...], *, attribute: str, label: str
) -> dict[str, ModelT]:
    mapped: dict[str, ModelT] = {}
    for value in values:
        key = getattr(value, attribute)
        if key in mapped:
            raise ValueError(f"duplicate {label}: {key}")
        mapped[key] = value
    return mapped


def _same_cell_identity(
    cell: ExecutionCellV1,
    *,
    case_id: str,
    case_sha256: str,
    config_id: str,
    config_sha256: str,
) -> bool:
    return (
        case_id,
        case_sha256,
        config_id,
        config_sha256,
    ) == (
        cell.case_id,
        cell.case_sha256,
        cell.system_config_id,
        cell.system_config_sha256,
    )


def _validate_release_closure(
    execution_matrix: ExecutionMatrixV1 | ExecutionMatrixV2,
    budget_manifests: tuple[BudgetManifestV1 | BudgetManifestV2, ...],
    rankings: tuple[ResearchRankingV1, ...],
    hypothesis_packets: tuple[HypothesisPacketV1, ...],
    terminal_results: tuple[TerminalRunResultV1, ...],
    top5_projections: tuple[Top5ProjectionV1, ...],
    *,
    assembled_at: str,
    git_commit: str,
    runtime_environment_sha256: str,
    analysis_environment_sha256: str,
    budget_model_type: type[BudgetManifestV1] | type[BudgetManifestV2] = (
        BudgetManifestV1
    ),
) -> None:
    if isinstance(execution_matrix, ExecutionMatrixV2):
        matrix = _revalidate(execution_matrix, ExecutionMatrixV2)
    elif isinstance(execution_matrix, ExecutionMatrixV1):
        matrix = _revalidate(execution_matrix, ExecutionMatrixV1)
    else:  # pragma: no cover - runtime callers are type checked by Pydantic
        raise ValueError("unsupported execution matrix contract")
    budgets = tuple(_revalidate(item, budget_model_type) for item in budget_manifests)
    rankings = tuple(_revalidate(item, ResearchRankingV1) for item in rankings)
    packets = tuple(
        _revalidate(item, HypothesisPacketV1) for item in hypothesis_packets
    )
    terminals = tuple(
        _revalidate(item, TerminalRunResultV1) for item in terminal_results
    )
    projections = tuple(
        _revalidate(item, Top5ProjectionV1) for item in top5_projections
    )

    cell_map = _exact_map(matrix.cells, attribute="cell_id", label="matrix cell")
    budget_map = _exact_map(budgets, attribute="cell_id", label="budget cell")
    terminal_map = _exact_map(terminals, attribute="cell_id", label="terminal cell")
    projection_map = _exact_map(projections, attribute="cell_id", label="projection cell")
    expected_cells = set(cell_map)
    for label, observed in (
        ("budget", set(budget_map)),
        ("terminal", set(terminal_map)),
        ("projection", set(projection_map)),
    ):
        if observed != expected_cells:
            raise ValueError(f"{label} artifacts do not exactly cover execution matrix")

    ranking_by_id = _exact_map(rankings, attribute="ranking_id", label="ranking")
    packet_by_id = _exact_map(packets, attribute="packet_id", label="packet")
    packet_by_sha = _exact_map(packets, attribute="packet_sha256", label="packet SHA-256")
    if len(packet_by_id) != len(packet_by_sha):  # pragma: no cover - explicit invariant
        raise ValueError("packet IDs and SHA-256 identities are not one-to-one")
    referenced_rankings: set[str] = set()
    referenced_packets: set[str] = set()
    run_ids: set[str] = set()
    receipt_bundle_ids: set[str] = set()
    receipt_bundle_hashes: set[str] = set()
    config_map = {item.config_id: item for item in matrix.system_configs}
    release_identities = {
        (
            item.git_commit,
            item.runtime_environment_sha256,
            item.analysis_environment_sha256,
        )
        for item in budgets
    }
    if release_identities != {
        (git_commit, runtime_environment_sha256, analysis_environment_sha256)
    }:
        raise ValueError("execution release requires one frozen git/runtime/analysis identity")

    for cell_id in sorted(expected_cells):
        cell = cell_map[cell_id]
        budget = budget_map[cell_id]
        terminal = terminal_map[cell_id]
        projection = projection_map[cell_id]
        if budget.run_id in run_ids:
            raise ValueError("run ID must be globally unique in an execution release")
        run_ids.add(budget.run_id)
        if (
            budget.execution_matrix_id != matrix.matrix_id
            or budget.execution_matrix_sha256 != matrix.matrix_sha256
            or budget.cell_sha256 != cell.cell_sha256
            or budget.system_config.config_id != cell.system_config_id
            or budget.system_config.config_sha256 != cell.system_config_sha256
            or budget.system_config != config_map[cell.system_config_id]
            or not _same_cell_identity(
                cell,
                case_id=budget.case_id,
                case_sha256=budget.case_sha256,
                config_id=budget.system_config.config_id,
                config_sha256=budget.system_config.config_sha256,
            )
        ):
            raise ValueError("budget manifest does not bind its exact matrix cell")
        if (
            terminal.budget_manifest_id != budget.budget_manifest_id
            or terminal.budget_manifest_sha256 != budget.budget_manifest_sha256
            or terminal.run_id != budget.run_id
            or terminal.git_commit != budget.git_commit
            or terminal.runtime_environment_sha256
            != budget.runtime_environment_sha256
            or not _same_cell_identity(
                cell,
                case_id=terminal.case_id,
                case_sha256=terminal.case_sha256,
                config_id=terminal.system_config_id,
                config_sha256=terminal.system_config_sha256,
            )
        ):
            raise ValueError("terminal result does not bind its budget and matrix cell")
        if _timestamp(terminal.completed_at) < _timestamp(budget.frozen_at):
            raise ValueError("terminal result precedes its frozen budget")
        if _timestamp(terminal.completed_at) > _timestamp(assembled_at):
            raise ValueError("release was assembled before terminal completion")
        if terminal.walltime_ms > budget.max_walltime_seconds * 1_000:
            raise ValueError("terminal walltime exceeds frozen budget")
        elapsed_ms = int(
            (_timestamp(terminal.completed_at) - _timestamp(budget.frozen_at)).total_seconds()
            * 1_000
        )
        if (
            elapsed_ms
            > budget.max_walltime_seconds * 1_000
            + _WALLTIME_COMPLETION_TOLERANCE_MS
        ):
            raise ValueError(
                "frozen-to-completed interval exceeds walltime budget plus one-second tolerance"
            )
        if terminal.walltime_ms > elapsed_ms + _WALLTIME_COMPLETION_TOLERANCE_MS:
            raise ValueError(
                "terminal walltime exceeds frozen-to-completed interval plus one-second tolerance"
            )

        usage_map = _exact_map(
            terminal.source_usage, attribute="source_id", label="source usage"
        )
        frozen_sources = {
            item.source_id: item for item in budget.system_config.source_budgets
        }
        if set(usage_map) != set(frozen_sources):
            raise ValueError("actual source usage differs from frozen source set")
        receipt_map = _exact_map(
            terminal.source_receipt_bundles,
            attribute="source_id",
            label="source receipt bundle",
        )
        if set(receipt_map) != set(frozen_sources):
            raise ValueError("source receipt bundles differ from frozen source set")
        for bundle in receipt_map.values():
            if (
                bundle.receipt_bundle_id in receipt_bundle_ids
                or bundle.receipt_bundle_sha256 in receipt_bundle_hashes
            ):
                raise ValueError(
                    "source receipt bundle is reused or aliased across execution cells"
                )
            receipt_bundle_ids.add(bundle.receipt_bundle_id)
            receipt_bundle_hashes.add(bundle.receipt_bundle_sha256)
        for source_id, usage in usage_map.items():
            frozen = frozen_sources[source_id]
            bundle = receipt_map[source_id]
            if bundle.retrieval_identity_sha256 != frozen.retrieval_identity_sha256:
                raise ValueError("source receipt bundle uses a foreign retrieval identity")
            if (
                bundle.cache_snapshot_sha256
                != budget.system_config.cache_snapshot_sha256
            ):
                raise ValueError("source receipt bundle uses a foreign cache snapshot")
            if bundle.source_catalog_sha256 != frozen.source_catalog_sha256:
                raise ValueError("source receipt bundle uses a foreign source catalog")
            for query in bundle.logical_queries:
                if (
                    query.query_plan_sha256
                    != budget.system_config.query_plan_sha256
                    or query.source_catalog_sha256 != frozen.source_catalog_sha256
                    or query.source_catalog_row_sha256
                    != frozen.source_catalog_row_sha256
                    or query.request_path_class != frozen.request_path_class
                    or query.request_path_template_sha256
                    != frozen.request_path_template_sha256
                    or query.source_field_projection_sha256
                    != frozen.source_field_projection_sha256
                ):
                    raise ValueError(
                        "logical query drifts from frozen catalog/path/projection policy"
                    )
                if not (
                    _timestamp(budget.frozen_at)
                    <= _timestamp(query.created_at)
                    <= _timestamp(terminal.completed_at)
                ):
                    raise ValueError("logical query receipt lies outside the run interval")
            for page in bundle.logical_pages:
                if page.request_host not in frozen.allowed_request_hosts:
                    raise ValueError("logical page uses a host outside the frozen allowlist")
                if page.query_plan_sha256 != budget.system_config.query_plan_sha256:
                    raise ValueError("logical page uses a foreign query plan")
                if (
                    page.record_projection_sha256
                    != budget.system_config.record_projection_sha256
                ):
                    raise ValueError("logical page uses a foreign record projection")
                if (
                    page.request_path_class != frozen.request_path_class
                    or page.request_path_template_sha256
                    != frozen.request_path_template_sha256
                    or page.source_field_projection_sha256
                    != frozen.source_field_projection_sha256
                ):
                    raise ValueError(
                        "logical page drifts from frozen path/field projection policy"
                    )
                if not (
                    _timestamp(budget.frozen_at)
                    <= _timestamp(page.completed_at)
                    <= _timestamp(terminal.completed_at)
                ):
                    raise ValueError("logical page receipt lies outside the run interval")
            for hop in bundle.physical_hops:
                if hop.request_host not in frozen.allowed_request_hosts:
                    raise ValueError("physical hop uses a host outside the frozen allowlist")
                if (
                    hop.request_path_class != frozen.request_path_class
                    or hop.request_path_template_sha256
                    != frozen.request_path_template_sha256
                ):
                    raise ValueError("physical hop drifts from frozen path policy")
                if not (
                    _timestamp(budget.frozen_at)
                    <= _timestamp(hop.completed_at)
                    <= _timestamp(terminal.completed_at)
                ):
                    raise ValueError("physical hop receipt lies outside the run interval")
            for entry in bundle.cache_inventory_entries:
                if entry.request_host not in frozen.allowed_request_hosts:
                    raise ValueError("cache entry uses a host outside the frozen allowlist")
                if (
                    entry.request_path_class != frozen.request_path_class
                    or entry.request_path_template_sha256
                    != frozen.request_path_template_sha256
                ):
                    raise ValueError("cache entry drifts from frozen path policy")
            for record in bundle.metadata_records:
                if (
                    record.source_field_projection_sha256
                    != frozen.source_field_projection_sha256
                ):
                    raise ValueError("metadata record uses a foreign field projection")
            for evidence in bundle.evidence_links:
                if evidence.span_field not in frozen.projected_fields:
                    raise ValueError("evidence span field is outside source catalog projection")
            actual = (
                usage.physical_requests,
                usage.logical_queries,
                usage.pages,
                usage.records,
                usage.response_bytes,
                usage.unique_documents,
                usage.cache_hits,
            )
            maximum = (
                frozen.max_physical_requests,
                frozen.max_logical_queries,
                frozen.max_pages,
                frozen.max_records,
                frozen.max_response_bytes,
                frozen.max_unique_documents,
                frozen.max_cache_hits,
            )
            if any(value > cap for value, cap in zip(actual, maximum, strict=True)):
                raise ValueError("actual source usage exceeds frozen logical budget")
        if sum(item.physical_requests for item in terminal.source_usage) > 8:
            raise ValueError("run exceeds the eight physical metadata-request cap")
        if terminal.article_body_fetch_requests or terminal.full_pdf_reads:
            raise ValueError("article body and PDF access are forbidden")

        llm = budget.system_config.llm
        llm_actual = (
            terminal.actual_llm_calls,
            terminal.actual_llm_input_tokens,
            terminal.actual_llm_output_tokens,
            terminal.actual_llm_metadata_packets,
        )
        if llm is None:
            if terminal.llm_invocation_receipts or any(llm_actual):
                raise ValueError("LLM-disabled arm reports model use")
        else:
            if any(
                value > cap
                for value, cap in zip(
                    llm_actual,
                    (
                        llm.max_calls,
                        llm.max_input_tokens,
                        llm.max_output_tokens,
                        llm.metadata_packet_limit,
                    ),
                    strict=True,
                )
            ):
                raise ValueError("actual LLM use exceeds frozen budget")
            for receipt in terminal.llm_invocation_receipts:
                if (
                    receipt.provider,
                    receipt.model,
                    receipt.revision,
                    receipt.prompt_sha256,
                    receipt.tokenizer_sha256,
                    receipt.output_schema_sha256,
                ) != (
                    llm.provider,
                    llm.model,
                    llm.revision,
                    llm.prompt_sha256,
                    llm.tokenizer_sha256,
                    llm.output_schema_sha256,
                ):
                    raise ValueError("LLM receipt uses a foreign model/prompt identity")
                if _timestamp(receipt.started_at) < _timestamp(budget.frozen_at):
                    raise ValueError("LLM invocation predates its frozen budget")
        local_model = budget.system_config.local_semantic_model
        if local_model is None:
            if (
                terminal.local_model_invocation_receipts
                or terminal.actual_local_model_input_tokens
            ):
                raise ValueError("local-model-disabled arm reports model use")
        else:
            for receipt in terminal.local_model_invocation_receipts:
                if (
                    receipt.bundle_sha256,
                    receipt.tokenizer_sha256,
                    receipt.model_card_sha256,
                    receipt.license_manifest_sha256,
                    receipt.vector_dimension,
                ) != (
                    local_model.bundle_sha256,
                    local_model.tokenizer_sha256,
                    local_model.model_card_sha256,
                    local_model.license_manifest_sha256,
                    local_model.vector_dimension,
                ):
                    raise ValueError("local-model receipt uses a foreign frozen model")
                if _timestamp(receipt.started_at) < _timestamp(budget.frozen_at):
                    raise ValueError("local-model invocation predates its frozen budget")

        ranking: ResearchRankingV1 | None
        if terminal.ranking_id is None:
            ranking = None
        else:
            ranking = ranking_by_id.get(terminal.ranking_id)
            if ranking is None or ranking.ranking_sha256 != terminal.ranking_sha256:
                raise ValueError("terminal result references a missing or forged ranking")
            if ranking.ranking_id in referenced_rankings:
                raise ValueError("one ranking is referenced by multiple terminal results")
            referenced_rankings.add(ranking.ranking_id)
            if (
                ranking.budget_manifest_id != budget.budget_manifest_id
                or ranking.budget_manifest_sha256 != budget.budget_manifest_sha256
                or ranking.cell_id != cell.cell_id
                or ranking.run_id != budget.run_id
                or not _same_cell_identity(
                    cell,
                    case_id=ranking.case_id,
                    case_sha256=ranking.case_sha256,
                    config_id=ranking.system_config_id,
                    config_sha256=ranking.system_config_sha256,
                )
            ):
                raise ValueError("ranking does not bind its prior budget and matrix cell")
            if _timestamp(ranking.created_at) < _timestamp(budget.frozen_at):
                raise ValueError("ranking predates its frozen budget")
            if _timestamp(ranking.created_at) > _timestamp(terminal.completed_at):
                raise ValueError("ranking was created after the terminal result")
            receipt_completion_times = tuple(
                _timestamp(item.completed_at)
                for bundle in terminal.source_receipt_bundles
                for item in (*bundle.logical_pages, *bundle.physical_hops)
            ) + tuple(
                _timestamp(item.completed_at)
                for item in (
                    *terminal.llm_invocation_receipts,
                    *terminal.local_model_invocation_receipts,
                )
            )
            if receipt_completion_times and _timestamp(ranking.created_at) < max(
                receipt_completion_times
            ):
                raise ValueError("ranking predates a retrieval or model receipt")
            for position in ranking.positions:
                packet = packet_by_id.get(position.packet_id)
                if packet is None or packet.packet_sha256 != position.packet_sha256:
                    raise ValueError(
                        "ranking position references a missing or forged hypothesis packet"
                    )
                if (packet.case_id, packet.case_sha256) != (
                    cell.case_id,
                    cell.case_sha256,
                ):
                    raise ValueError("ranked hypothesis packet belongs to another case")
                referenced_packets.add(packet.packet_id)

        expected_evidence: dict[tuple[str, str], tuple[HypothesisPacketV1, object]] = {}
        if ranking is not None:
            for position in ranking.positions:
                packet = packet_by_id[position.packet_id]
                for link in packet.evidence_links:
                    key = (packet.packet_id, link.evidence_link_id)
                    if key in expected_evidence:
                        raise ValueError("ranked packet evidence link is aliased")
                    expected_evidence[key] = (packet, link)
        actual_evidence: dict[tuple[str, str], EvidenceLinkReceiptV1] = {}
        for bundle in terminal.source_receipt_bundles:
            for evidence in bundle.evidence_links:
                key = (evidence.packet_id, evidence.evidence_link_id)
                if key in actual_evidence:
                    raise ValueError("evidence provenance is duplicated across source bundles")
                actual_evidence[key] = evidence
        if set(actual_evidence) != set(expected_evidence):
            raise ValueError(
                "evidence receipts do not exactly cover same-cell ranked packet links"
            )
        for key, (packet, link) in expected_evidence.items():
            receipt = actual_evidence[key]
            if (
                receipt.packet_sha256,
                receipt.source_id,
                receipt.source_record_id,
                receipt.source_url_sha256,
                receipt.span_id,
                receipt.span_sha256,
                receipt.evidence_link_sha256,
                receipt.private_text_artifact_uri_sha256,
            ) != (
                packet.packet_sha256,
                link.source_id,
                link.source_record_id,
                canonical_sha256(link.source_url),
                link.span_id,
                link.span_sha256,
                canonical_sha256(link.model_dump(mode="python")),
                (
                    None
                    if link.private_text_artifact_uri is None
                    else canonical_sha256(link.private_text_artifact_uri)
                ),
            ):
                raise ValueError("packet evidence link drifts from its record/span receipt")
            source_bundle = receipt_map.get(link.source_id)
            if source_bundle is None:
                raise ValueError("packet evidence link uses a foreign source")
            record = {
                item.record_receipt_id: item
                for item in source_bundle.metadata_records
            }.get(receipt.record_receipt_id)
            if record is None or record.record_receipt_sha256 != receipt.record_receipt_sha256:
                raise ValueError("packet evidence link references a foreign record/span")

        if terminal.status is RunCellStatus.SUCCEEDED and (
            ranking is None or len(ranking.positions) != 5
        ):
            raise ValueError("successful cell requires a full Top-5 ranking")
        if terminal.status is RunCellStatus.PARTIAL and ranking is None:
            raise ValueError("partial cell requires an explicit ranking, including empty")
        expected_projection = _project_top5(terminal, ranking)
        if projection != expected_projection:
            raise ValueError("Top-5 projection is not the deterministic fixed denominator")

    if referenced_rankings != set(ranking_by_id):
        raise ValueError("release contains an unreferenced or late ranking")
    if referenced_packets != set(packet_by_id):
        raise ValueError("release contains an unreferenced hypothesis packet")
    ordered_cells = tuple(item.cell_id for item in matrix.cells)
    for values, label in (
        (tuple(item.cell_id for item in budgets), "budgets"),
        (tuple(item.cell_id for item in terminals), "terminal results"),
        (tuple(item.cell_id for item in projections), "Top-5 projections"),
    ):
        if values != ordered_cells:
            raise ValueError(f"{label} must follow execution-matrix cell order")
    ranking_order = tuple(
        item.cell_id for item in rankings
    )
    if ranking_order != tuple(sorted(ranking_order, key=ordered_cells.index)):
        raise ValueError("rankings must follow execution-matrix cell order")
    if tuple(item.packet_id for item in packets) != tuple(sorted(packet_by_id)):
        raise ValueError("hypothesis packets must be packet-ID sorted")


def assemble_execution_release(
    execution_matrix: ExecutionMatrixV1,
    budget_manifests: tuple[BudgetManifestV1, ...],
    rankings: tuple[ResearchRankingV1, ...],
    terminal_results: tuple[TerminalRunResultV1, ...],
    *,
    hypothesis_packets: tuple[HypothesisPacketV1, ...],
    assembled_at: str,
) -> ExecutionReleaseV1:
    """Assemble exact coverage and deterministic five-position denominator rows."""

    matrix = _revalidate(execution_matrix, ExecutionMatrixV1)
    budgets = tuple(_revalidate(item, BudgetManifestV1) for item in budget_manifests)
    rankings = tuple(_revalidate(item, ResearchRankingV1) for item in rankings)
    packets = tuple(
        sorted(
            (_revalidate(item, HypothesisPacketV1) for item in hypothesis_packets),
            key=lambda item: item.packet_id,
        )
    )
    terminals = tuple(
        _revalidate(item, TerminalRunResultV1) for item in terminal_results
    )
    order = {cell.cell_id: index for index, cell in enumerate(matrix.cells)}
    budgets = tuple(sorted(budgets, key=lambda item: order.get(item.cell_id, 10**9)))
    terminals = tuple(sorted(terminals, key=lambda item: order.get(item.cell_id, 10**9)))
    rankings = tuple(sorted(rankings, key=lambda item: order.get(item.cell_id, 10**9)))
    ranking_by_id = _exact_map(rankings, attribute="ranking_id", label="ranking")
    projections = tuple(
        _project_top5(
            terminal,
            None
            if terminal.ranking_id is None
            else ranking_by_id.get(terminal.ranking_id),
        )
        for terminal in terminals
    )
    frozen_identities = {
        (
            item.git_commit,
            item.runtime_environment_sha256,
            item.analysis_environment_sha256,
        )
        for item in budgets
    }
    if len(frozen_identities) != 1:
        raise ValueError("execution release requires one frozen git/runtime/analysis identity")
    git_commit, runtime_environment_sha256, analysis_environment_sha256 = next(
        iter(frozen_identities)
    )
    values: dict[str, object] = {
        "git_commit": git_commit,
        "runtime_environment_sha256": runtime_environment_sha256,
        "analysis_environment_sha256": analysis_environment_sha256,
        "execution_matrix": matrix,
        "budget_manifests": budgets,
        "rankings": rankings,
        "hypothesis_packets": packets,
        "terminal_results": terminals,
        "top5_projections": projections,
        "assembled_at": _require_timestamp(assembled_at),
    }
    return _build_identified(
        ExecutionReleaseV1,
        id_field="release_id",
        sha_field="release_sha256",
        prefix="execution-release",
        values=values,
    )


def assemble_execution_release_v2(
    execution_matrix: ExecutionMatrixV2,
    budget_manifests: tuple[BudgetManifestV1, ...],
    rankings: tuple[ResearchRankingV1, ...],
    terminal_results: tuple[TerminalRunResultV1, ...],
    *,
    hypothesis_packets: tuple[HypothesisPacketV1, ...],
    assembled_at: str,
) -> ExecutionReleaseV2:
    """Assemble the legacy split-V2 draft with an exact denominator."""

    matrix = _revalidate(execution_matrix, ExecutionMatrixV2)
    budgets = tuple(_revalidate(item, BudgetManifestV1) for item in budget_manifests)
    rankings = tuple(_revalidate(item, ResearchRankingV1) for item in rankings)
    packets = tuple(
        sorted(
            (_revalidate(item, HypothesisPacketV1) for item in hypothesis_packets),
            key=lambda item: item.packet_id,
        )
    )
    terminals = tuple(
        _revalidate(item, TerminalRunResultV1) for item in terminal_results
    )
    order = {cell.cell_id: index for index, cell in enumerate(matrix.cells)}
    budgets = tuple(sorted(budgets, key=lambda item: order.get(item.cell_id, 10**9)))
    terminals = tuple(sorted(terminals, key=lambda item: order.get(item.cell_id, 10**9)))
    rankings = tuple(sorted(rankings, key=lambda item: order.get(item.cell_id, 10**9)))
    ranking_by_id = _exact_map(rankings, attribute="ranking_id", label="ranking")
    projections = tuple(
        _project_top5(
            terminal,
            None
            if terminal.ranking_id is None
            else ranking_by_id.get(terminal.ranking_id),
        )
        for terminal in terminals
    )
    frozen_identities = {
        (
            item.git_commit,
            item.runtime_environment_sha256,
            item.analysis_environment_sha256,
        )
        for item in budgets
    }
    if len(frozen_identities) != 1:
        raise ValueError("execution release requires one frozen git/runtime/analysis identity")
    git_commit, runtime_environment_sha256, analysis_environment_sha256 = next(
        iter(frozen_identities)
    )
    values: dict[str, object] = {
        "git_commit": git_commit,
        "runtime_environment_sha256": runtime_environment_sha256,
        "analysis_environment_sha256": analysis_environment_sha256,
        "execution_matrix": matrix,
        "budget_manifests": budgets,
        "rankings": rankings,
        "hypothesis_packets": packets,
        "terminal_results": terminals,
        "top5_projections": projections,
        "assembled_at": _require_timestamp(assembled_at),
    }
    return _build_identified(
        ExecutionReleaseV2,
        id_field="release_id",
        sha_field="release_sha256",
        prefix="execution-release-v2",
        values=values,
    )


def assemble_execution_release_v3(
    execution_matrix: ExecutionMatrixV2,
    budget_manifests: tuple[BudgetManifestV2, ...],
    rankings: tuple[ResearchRankingV1, ...],
    terminal_results: tuple[TerminalRunResultV1, ...],
    *,
    frozen_case_release: FrozenCaseReleaseV3,
    pre_run_eligibility_release: PreRunEligibilityReleaseV3,
    pre_budget_closure_release: PilotPreBudgetClosureReleaseV3,
    hypothesis_packets: tuple[HypothesisPacketV1, ...],
    assembled_at: str,
) -> ExecutionReleaseV3:
    """Assemble and internally replay the exact V3 formal preimage."""

    frozen = _revalidate(frozen_case_release, FrozenCaseReleaseV3)
    eligibility = _revalidate(
        pre_run_eligibility_release, PreRunEligibilityReleaseV3
    )
    closure = _revalidate(
        pre_budget_closure_release, PilotPreBudgetClosureReleaseV3
    )
    if frozen.pre_run_eligibility_release != eligibility:
        raise ValueError("V3 assembler receives alternate eligibility")
    if closure.frozen_case_release != frozen:
        raise ValueError("V3 assembler receives alternate pre-budget closure")
    matrix = _revalidate(execution_matrix, ExecutionMatrixV2)
    if matrix.split_manifest != frozen.split_manifest:
        raise ValueError("V3 assembler receives a foreign split matrix")
    budgets = tuple(
        _revalidate(item, BudgetManifestV2) for item in budget_manifests
    )
    rankings = tuple(_revalidate(item, ResearchRankingV1) for item in rankings)
    packets = tuple(
        sorted(
            (_revalidate(item, HypothesisPacketV1) for item in hypothesis_packets),
            key=lambda item: item.packet_id,
        )
    )
    terminals = tuple(
        _revalidate(item, TerminalRunResultV1) for item in terminal_results
    )
    order = {cell.cell_id: index for index, cell in enumerate(matrix.cells)}
    budgets = tuple(
        sorted(budgets, key=lambda item: order.get(item.cell_id, 10**9))
    )
    terminals = tuple(
        sorted(terminals, key=lambda item: order.get(item.cell_id, 10**9))
    )
    rankings = tuple(
        sorted(rankings, key=lambda item: order.get(item.cell_id, 10**9))
    )
    ranking_by_id = _exact_map(rankings, attribute="ranking_id", label="ranking")
    projections = tuple(
        _project_top5(
            terminal,
            None
            if terminal.ranking_id is None
            else ranking_by_id.get(terminal.ranking_id),
        )
        for terminal in terminals
    )
    frozen_identities = {
        (
            item.git_commit,
            item.runtime_environment_sha256,
            item.analysis_environment_sha256,
        )
        for item in budgets
    }
    if len(frozen_identities) != 1:
        raise ValueError("V3 execution requires one git/runtime/analysis identity")
    git_commit, runtime_environment_sha256, analysis_environment_sha256 = next(
        iter(frozen_identities)
    )
    return _build_identified(
        ExecutionReleaseV3,
        id_field="release_id",
        sha_field="release_sha256",
        prefix="execution-release-v3",
        values={
            "frozen_case_release_id": frozen.release_id,
            "frozen_case_release_sha256": frozen.release_sha256,
            "pre_run_eligibility_release_id": eligibility.release_id,
            "pre_run_eligibility_release_sha256": eligibility.release_sha256,
            "pre_budget_closure_release_id": closure.release_id,
            "pre_budget_closure_release_sha256": closure.release_sha256,
            "frozen_case_release": frozen,
            "pre_run_eligibility_release": eligibility,
            "pre_budget_closure_release": closure,
            "git_commit": git_commit,
            "runtime_environment_sha256": runtime_environment_sha256,
            "analysis_environment_sha256": analysis_environment_sha256,
            "execution_matrix": matrix,
            "budget_manifests": budgets,
            "rankings": rankings,
            "hypothesis_packets": packets,
            "terminal_results": terminals,
            "top5_projections": projections,
            "assembled_at": _require_timestamp(assembled_at),
        },
    )


__all__ = [
    "ActualSourceUsageV1",
    "BudgetManifestV1",
    "BudgetManifestV2",
    "CacheDisposition",
    "CacheInventoryEntryV1",
    "EvidenceLinkReceiptV1",
    "ExecutionCellV1",
    "ExecutionMatrixV1",
    "ExecutionMatrixV2",
    "ExecutionPhase",
    "ExecutionReleaseV1",
    "ExecutionReleaseV2",
    "ExecutionReleaseV3",
    "LlmExecutionIdentityV1",
    "LlmInvocationReceiptV1",
    "LocalModelExecutionIdentityV1",
    "LocalModelInvocationReceiptV1",
    "LogicalQueryReceiptV1",
    "LogicalPageReceiptV1",
    "MetadataSpanPreimageV1",
    "MetadataModelInputRefV1",
    "MetadataRecordReceiptV1",
    "NormalizedMetadataArtifactV1",
    "NormalizedMetadataFieldV1",
    "MissingPositionReason",
    "PhysicalHopReceiptV1",
    "ProjectedTop5PositionV1",
    "RankingPositionV1",
    "ResearchRankingV1",
    "ResearchSystemId",
    "RunCellStatus",
    "SourceBudgetV1",
    "SourceReceiptBundleV1",
    "SourceVariant",
    "SystemConfigV1",
    "TerminalRunResultV1",
    "Top5ProjectionV1",
    "assemble_execution_release",
    "assemble_execution_release_v2",
    "assemble_execution_release_v3",
    "build_budget_manifest_v2",
    "build_execution_matrix",
    "build_execution_matrix_v2",
    "replay_source_usage",
    "replay_llm_usage",
    "replay_local_model_usage",
    "source_policy_values",
]
