"""Pydantic contracts for the deterministic retrieval stage."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


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


class ProvenanceStatus(StrEnum):
    RESOLVED = "RESOLVED"
    PARTIAL = "PARTIAL"
    UNRESOLVED = "UNRESOLVED"


class RankingMode(StrEnum):
    MINIMIZE = "minimize"
    MAXIMIZE = "maximize"
    TARGET = "target"


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
    include_elements: list[str] = Field(default_factory=list)
    exclude_elements: list[str] = Field(default_factory=list)
    band_gap_ev: NumericRange | None = None
    energy_above_hull_ev_atom: NumericRange | None = None
    is_metal: bool | None = None
    dimensionality: Literal[0, 1, 2, 3] | None = None
    max_num_sites: int | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def no_element_conflict(self) -> HardConstraints:
        overlap = set(self.include_elements) & set(self.exclude_elements)
        if overlap:
            raise ValueError(f"elements cannot be both included and excluded: {sorted(overlap)}")
        return self


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

    @model_validator(mode="after")
    def chunks_cover_scan_limit(self) -> RetrievalPolicy:
        if self.max_records_scanned % self.chunk_size != 0:
            raise ValueError("max_records_scanned must be divisible by chunk_size")
        return self


class RetrievalStageInput(StrictModel):
    project_id: str
    run_id: str
    requirement_revision: int = Field(ge=1)
    requirement_artifact_uri: str
    requirement_hash: str
    retrieval_policy_version: str
    confirmed_by_user: bool


class StageInputValidation(StrictModel):
    valid: bool
    missing_fields: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)
    remediation: list[str] = Field(default_factory=list)


class SourceMetadata(StrictModel):
    database_version: str
    client_version: str
    available_fields: list[str]


class RetrievalQueryPlan(StrictModel):
    query_id: str
    source_database: Literal["materials_project"] = "materials_project"
    endpoint: str
    database_version: str
    requirement_hash: str
    pushdown_filters: dict[str, Any]
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
    candidate_id: str
    formula: str
    source_database: Literal["materials_project"] = "materials_project"
    source_database_version: str
    source_material_id: str
    source_last_updated: datetime | None = None
    query_id: str
    structure_id: str | None = None
    structure_artifact_uri: str | None = None
    structure_source_artifact_uri: str | None = None
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
    run_id: str
    stage: Literal["agent01"] = "agent01"
    status: StageStatus
    input_snapshot_uri: str
    output_artifacts: list[ArtifactRef]
    candidate_ids: list[str]
    warnings: list[str] = Field(default_factory=list)
    errors: list[ErrorRecord] = Field(default_factory=list)
    metrics: dict[str, Any] = Field(default_factory=dict)
    provenance: dict[str, Any] = Field(default_factory=dict)
    started_at: datetime
    finished_at: datetime
