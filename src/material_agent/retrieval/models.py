"""Pydantic contracts for the deterministic retrieval stage."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

AGENT01_CONTRACT_VERSION = "agent01-contract-v1"
AGENT01_MULTI_SOURCE_CONTRACT_VERSION = "agent01-contract-v2"


def utc_now() -> datetime:
    return datetime.now(UTC)


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True)


class EvidenceLevel(StrEnum):
    L0_PARSED = "L0_PARSED"
    L1_RETRIEVED = "L1_RETRIEVED"
    L2_ML_SCREENED = "L2_ML_SCREENED"
    L3_DFT_VALIDATED = "L3_DFT_VALIDATED"
    L4_MANY_BODY_VALIDATED = "L4_MANY_BODY_VALIDATED"
    L5_EXPERT_REVIEWED = "L5_EXPERT_REVIEWED"


class Decision(StrEnum):
    PASS = "PASS"
    REJECT = "REJECT"
    UNCERTAIN = "UNCERTAIN"
    FAILED = "FAILED"


class ConstraintResult(StrEnum):
    MATCH = "MATCH"
    MISMATCH = "MISMATCH"
    MISSING = "MISSING"
    ERROR = "ERROR"


class StageStatus(StrEnum):
    PENDING = "PENDING"
    VALIDATING_INPUT = "VALIDATING_INPUT"
    BLOCKED_MISSING_INPUT = "BLOCKED_MISSING_INPUT"
    WAITING_APPROVAL = "WAITING_APPROVAL"
    READY = "READY"
    RUNNING = "RUNNING"
    RETRYABLE_FAILED = "RETRYABLE_FAILED"
    PERMANENT_FAILED = "PERMANENT_FAILED"
    PARTIAL = "PARTIAL"
    SUCCEEDED = "SUCCEEDED"
    CANCELLED = "CANCELLED"


class StageOutcomeType(StrEnum):
    COMPLETED = "Completed"
    WAITING_EXTERNAL = "WaitingExternal"
    BLOCKED = "Blocked"
    FAILED = "Failed"


class ProvenanceStatus(StrEnum):
    RESOLVED = "RESOLVED"
    PARTIAL = "PARTIAL"
    UNRESOLVED = "UNRESOLVED"


class RankingMode(StrEnum):
    MINIMIZE = "minimize"
    MAXIMIZE = "maximize"
    TARGET = "target"


class SourceDatabase(StrEnum):
    MATERIALS_PROJECT = "materials_project"
    NOMAD = "nomad"
    MC3D = "mc3d"
    C2DB = "c2db"
    TOPOLOGICAL_QUANTUM_CHEMISTRY = "topological_quantum_chemistry"
    NIMS_SUPERCON = "nims_supercon"
    ATOMLY = "atomly"


class NumericRange(StrictModel):
    min: float | None = None
    max: float | None = None
    unit: str

    @model_validator(mode="after")
    def validate_bounds(self) -> NumericRange:
        if self.min is None and self.max is None:
            raise ValueError("at least one range boundary is required")
        if self.min is not None and self.max is not None and self.min > self.max:
            raise ValueError("range min must not exceed max")
        return self

    @field_validator("min", "max")
    @classmethod
    def require_finite(cls, value: float | None) -> float | None:
        if value is not None:
            import math

            if not math.isfinite(value):
                raise ValueError("range boundaries must be finite")
        return value


class HardConstraints(StrictModel):
    exact_formula: str | None = None
    include_elements: list[str] = Field(default_factory=list)
    exclude_elements: list[str] = Field(default_factory=list)
    band_gap_ev: NumericRange | None = None
    energy_above_hull_ev_atom: NumericRange | None = None
    is_metal: bool | None = None
    dimensionality: Literal[0, 1, 2, 3] | None = None
    max_num_sites: int | None = Field(default=None, ge=1)
    source_constraints: SourceSpecificConstraints = Field(
        default_factory=lambda: SourceSpecificConstraints()
    )

    @model_validator(mode="after")
    def no_element_conflict(self) -> HardConstraints:
        overlap = set(self.include_elements) & set(self.exclude_elements)
        if overlap:
            raise ValueError(f"elements cannot be both included and excluded: {sorted(overlap)}")
        return self


class MaterialsProjectConstraints(StrictModel):
    """Simple scalar/label constraints available from MP summary records."""

    density_g_cm3: NumericRange | None = None
    volume_a3: NumericRange | None = None
    formation_energy_ev_atom: NumericRange | None = None
    is_stable: bool | None = None
    crystal_system: str | None = None
    spacegroup_number: int | None = Field(default=None, ge=1, le=230)
    is_gap_direct: bool | None = None
    magnetic_ordering: str | None = None


class C2DBConstraints(StrictModel):
    """C2DB table labels that require no scientific inference."""

    layer_group: str | None = None
    magnetic_label: str | None = None


class TQCConstraints(StrictModel):
    """TQC classification and diagnostics, kept as database labels."""

    topological_material: bool | None = None
    topological_classification: str | None = None
    topological_subclassification: str | None = None
    has_topological_indices: bool | None = None
    soc: bool | None = None
    fermi_crossing_count: int | None = Field(default=None, ge=0)
    line_crossing_label: str | None = None


class SourceSpecificConstraints(StrictModel):
    """Constraints whose meaning is specific to one selectable database."""

    materials_project: MaterialsProjectConstraints = Field(
        default_factory=MaterialsProjectConstraints
    )
    c2db: C2DBConstraints = Field(default_factory=C2DBConstraints)
    nomad: dict[str, Any] = Field(default_factory=dict)
    mc3d: dict[str, Any] = Field(default_factory=dict)
    topological_quantum_chemistry: TQCConstraints = Field(
        default_factory=TQCConstraints
    )

    # NOMAD/MC3D fields are retained as unmapped constraints until their
    # source-native catalogs expose executable fields; they are not rejected
    # during parsing or silently discarded.


class ScientificTarget(StrictModel):
    name: str
    operational_definition: str | None = None
    required_evidence_level: EvidenceLevel = EvidenceLevel.L1_RETRIEVED


class RankingPreference(StrictModel):
    property: Literal["energy_above_hull", "band_gap", "num_sites"]
    mode: RankingMode
    target: float | None = None

    @model_validator(mode="after")
    def target_required_for_target_mode(self) -> RankingPreference:
        if self.mode is RankingMode.TARGET and self.target is None:
            raise ValueError("target is required when ranking mode is 'target'")
        if self.mode is not RankingMode.TARGET and self.target is not None:
            raise ValueError("target is only valid when ranking mode is 'target'")
        return self


class Budget(StrictModel):
    max_candidates: int = Field(default=200, ge=1)
    allow_ml: bool = True
    allow_dft: bool = False
    allow_many_body: bool = False


class MaterialsProjectSourceOptions(StrictModel):
    include_gnome: bool = False


class DataSources(StrictModel):
    materials_project: MaterialsProjectSourceOptions = Field(
        default_factory=MaterialsProjectSourceOptions
    )


class Requirement(StrictModel):
    requirement_id: str
    revision: int = Field(ge=1)
    target_class: str
    hard_constraints: HardConstraints
    scientific_targets: list[ScientificTarget] = Field(default_factory=list)
    ranking_preferences: list[RankingPreference] = Field(default_factory=list)
    budget: Budget = Field(default_factory=Budget)
    data_sources: DataSources = Field(default_factory=DataSources)
    confirmed_by_user: bool
    policy_version: str


class RetrievalPolicy(StrictModel):
    policy_version: str = "retrieval-policy-v1"
    source_database: SourceDatabase = SourceDatabase.MATERIALS_PROJECT
    endpoint: str = "/materials/summary"
    include_deprecated: bool = False
    include_gnome_default: bool = False
    theoretical: bool | None = None
    chunk_size: int = Field(default=500, ge=1, le=1000)
    max_records_scanned: int = Field(default=5000, ge=1)
    origin_batch_size: int = Field(default=100, ge=1, le=1000)
    max_attempts: int = Field(default=3, ge=1, le=10)
    retry_base_seconds: float = Field(default=1.0, ge=0)
    numeric_tolerance: float = Field(default=1e-8, gt=0)
    canonical_float_digits: int = Field(default=12, ge=6, le=16)
    dimensionality_policy_version: str = "dimensionality-larsen-crystalnn-v1"
    canonicalization_policy_version: str = "canonical-structure-v1"
    mp_report: MaterialsProjectReportPolicy = Field(
        default_factory=lambda: MaterialsProjectReportPolicy()
    )

    @model_validator(mode="after")
    def chunks_cover_scan_limit(self) -> RetrievalPolicy:
        if self.max_records_scanned % self.chunk_size != 0:
            raise ValueError("max_records_scanned must be divisible by chunk_size")
        return self


class MaterialsProjectReportPolicy(StrictModel):
    """Bounded, deterministic enrichment policy for the MP Markdown report."""

    schema_version: str = "agent01-mp-report-policy-v1"
    heavy_candidate_limit: int = Field(default=20, ge=0, le=200)
    total_byte_limit: int = Field(default=2_147_483_648, ge=1)
    single_object_byte_limit: int = Field(default=536_870_912, ge=1)
    renderer_version: str = "mp-local-matplotlib-v1"


class RetrievalStageInput(StrictModel):
    project_id: str
    run_id: str
    requirement_revision: int = Field(ge=1)
    requirement_artifact_uri: str
    requirement_hash: str
    raw_request: str | None = None
    mp_screening_spec_uri: str | None = None
    mp_screening_spec_sha256: str | None = None
    retrieval_policy_version: str
    confirmed_by_user: bool


class RetrievalStageContext(StrictModel):
    requirement: Requirement
    stage_input: RetrievalStageInput


class StageInputValidation(StrictModel):
    valid: bool
    missing_fields: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)
    remediation: list[str] = Field(default_factory=list)
    error_category: str | None = None
    requirement_artifact_uri: str | None = None
    requirement_artifact_sha256: str | None = None


class SourceMetadata(StrictModel):
    database_version: str
    client_version: str
    available_fields: list[str]


class RetrievalQueryPlan(StrictModel):
    query_id: str
    source_database: SourceDatabase = SourceDatabase.MATERIALS_PROJECT
    endpoint: str
    database_version: str
    requirement_hash: str
    raw_request: str | None = None
    pushdown_filters: dict[str, Any]
    # Filters evaluated from a source's cheap listing response before any
    # per-record structure download.  They are intentionally separate from
    # pushdown_filters because the remote service did not apply them.
    predownload_filters: dict[str, Any] = Field(default_factory=dict)
    local_only_constraints: list[str]
    requested_fields: list[str]
    chunk_size: int
    num_chunks: int
    max_records_scanned: int
    max_candidates_published: int
    include_gnome: bool
    include_deprecated: bool
    theoretical_policy: bool | None
    sort_fields: str
    policy_version: str
    query_fingerprint: str
    client_version: str
    created_at: datetime = Field(default_factory=utc_now)


class RetrievalStagePlan(StrictModel):
    context: RetrievalStageContext
    query_plan: RetrievalQueryPlan
    source_metadata: SourceMetadata
    idempotency_key: str


class PropertyOrigin(StrictModel):
    endpoint: str
    database_version: str
    origin_task_id: str | None = None
    run_type: str | None = None
    task_type: str | None = None
    calc_type: str | None = None
    status: ProvenanceStatus


class PropertyValue(StrictModel):
    name: str
    value: float | int | bool | str | None
    unit: str
    source: str
    method: str | None = None
    evidence_level: EvidenceLevel = EvidenceLevel.L1_RETRIEVED
    uncertainty: float | None = None
    origin: PropertyOrigin
    retrieved_at: datetime
    is_derived: bool = False
    derived_from_structure_id: str | None = None
    derivation_policy_version: str | None = None


class ConstraintEvaluation(StrictModel):
    constraint_id: str
    constraint_type: str
    expected: Any
    observed: Any = None
    unit: str | None = None
    result: ConstraintResult
    reason_code: str
    property_origin: PropertyOrigin | None = None


class ScientificTargetEvaluation(StrictModel):
    name: str
    required_evidence_level: EvidenceLevel
    result: ConstraintResult
    reason_code: str
    observed_property: str | None = None


class CandidateAuditRecord(StrictModel):
    schema_version: Literal["agent01-contract-v1"] = AGENT01_CONTRACT_VERSION
    candidate_id: str
    formula: str
    source_database: Literal["materials_project"] = "materials_project"
    source_database_version: str
    source_material_id: str
    source_last_updated: datetime | None = None
    query_id: str
    structure_id: str | None = None
    structure_artifact_uri: str | None = None
    structure_artifact_sha256: str | None = None
    structure_source_artifact_uri: str | None = None
    structure_source_artifact_sha256: str | None = None
    reduced_formula: str | None = None
    elements: list[str] = Field(default_factory=list)
    num_sites: int | None = None
    properties: list[PropertyValue] = Field(default_factory=list)
    constraint_evaluations: list[ConstraintEvaluation] = Field(default_factory=list)
    scientific_target_evaluations: list[ScientificTargetEvaluation] = Field(
        default_factory=list
    )
    matched_constraints: list[str] = Field(default_factory=list)
    unmatched_constraints: list[str] = Field(default_factory=list)
    missing_evidence: list[str] = Field(default_factory=list)
    evidence_level: EvidenceLevel = EvidenceLevel.L1_RETRIEVED
    decision: Decision
    decision_reasons: list[str] = Field(default_factory=list)
    exact_duplicate_group_id: str | None = None
    similarity_cluster_id: str | None = None
    publication_rank: int | None = None
    published_downstream: bool = False
    data_quality_flags: list[str] = Field(default_factory=list)
    provenance: dict[str, Any] = Field(default_factory=dict)


class CandidateAuditRecordV2(CandidateAuditRecord):
    """Multi-source candidate contract used by non-MP retrieval sources."""

    schema_version: Literal["agent01-contract-v2"] = (
        AGENT01_MULTI_SOURCE_CONTRACT_VERSION
    )
    source_database: SourceDatabase


class ArtifactRef(StrictModel):
    uri: str
    sha256: str
    size_bytes: int
    media_type: str


class ErrorRecord(StrictModel):
    category: str
    retryable: bool
    operation: str
    public_message: str
    candidate_id: str | None = None


class StageResultEnvelope(StrictModel):
    schema_version: Literal["agent01-contract-v1"] = AGENT01_CONTRACT_VERSION
    run_id: str
    stage: Literal["agent01"] = "agent01"
    status: StageStatus
    input_snapshot_uri: str
    input_snapshot_sha256: str | None = None
    output_artifacts: list[ArtifactRef]
    candidate_manifest: ArtifactRef | None = None
    candidate_ids: list[str]
    warnings: list[str] = Field(default_factory=list)
    errors: list[ErrorRecord] = Field(default_factory=list)
    metrics: dict[str, Any] = Field(default_factory=dict)
    provenance: dict[str, Any] = Field(default_factory=dict)
    started_at: datetime
    finished_at: datetime


class StageResultEnvelopeV2(StageResultEnvelope):
    """Multi-source stage envelope while the MP v1 envelope remains frozen."""

    schema_version: Literal["agent01-contract-v2"] = (
        AGENT01_MULTI_SOURCE_CONTRACT_VERSION
    )


class OperationRecord(StrictModel):
    """Immutable completion record used to validate resumable stage output."""

    operation_id: str
    query_fingerprint: str
    result: StageResultEnvelope | StageResultEnvelopeV2
    registered_artifacts: list[ArtifactRef]


class StageOutcome(StrictModel):
    schema_version: Literal["agent01-contract-v1"] = AGENT01_CONTRACT_VERSION
    outcome: StageOutcomeType
    status: StageStatus
    operation_ref: str | None = None
    result: StageResultEnvelope | None = None
    errors: list[ErrorRecord] = Field(default_factory=list)


class StageOutcomeV2(StageOutcome):
    schema_version: Literal["agent01-contract-v2"] = (
        AGENT01_MULTI_SOURCE_CONTRACT_VERSION
    )
    result: StageResultEnvelopeV2 | None = None


CandidateRecord = CandidateAuditRecord | CandidateAuditRecordV2
StageResult = StageResultEnvelope | StageResultEnvelopeV2
NativeStageOutcome = StageOutcome | StageOutcomeV2
