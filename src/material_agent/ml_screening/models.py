"""Strict, versioned JSON contracts for Agent02.

The contracts are deliberately independent from Agent01's frozen public
models.  Agent02 consumes a normalized, immutable view of upstream records and
publishes additive ML evidence in its own manifest.
"""

from __future__ import annotations

import math
import hashlib
import json
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import PurePosixPath
from typing import Any, Literal, Protocol

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)
from typing_extensions import Annotated


AGENT02_REQUEST_VERSION = "agent02-request-v1"
AGENT02_STAGE_PLAN_VERSION = "agent02-stage-plan-v1"
AGENT02_CONTRACT_VERSION = "agent02-contract-v1"
AGENT02_MODEL_HEALTH_VERSION = "agent02-model-health-v1"
AGENT02_WORKER_PROTOCOL_VERSION = "agent02-worker-protocol-v1"
AGENT02_POLICY_VERSION = "ml-screening-policy-v1"
AGENT02_MODEL_REGISTRY_VERSION = "model-registry-v1"

Sha256 = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]


def utc_now() -> datetime:
    return datetime.now(UTC)


class StrictFrozenModel(BaseModel):
    model_config = ConfigDict(
        allow_inf_nan=False,
        extra="forbid",
        frozen=True,
        ser_json_inf_nan="constants",
        validate_assignment=True,
    )


class SelectionMode(StrEnum):
    POLICY_TOP_N = "POLICY_TOP_N"
    EXPLICIT_IDS = "EXPLICIT_IDS"


class MLTask(StrEnum):
    STATIC_PREDICTION = "static_prediction"
    STRUCTURE_RELAXATION = "structure_relaxation"


class MLDecision(StrEnum):
    PASS = "PASS"
    REJECT = "REJECT"
    UNCERTAIN = "UNCERTAIN"
    FAILED = "FAILED"


class EvidenceLevel(StrEnum):
    L1_RETRIEVED = "L1_RETRIEVED"
    L2_ML_SCREENED = "L2_ML_SCREENED"


class PreFilterReasonCode(StrEnum):
    UPSTREAM_REJECTED = "UPSTREAM_REJECTED"
    UPSTREAM_FAILED = "UPSTREAM_FAILED"
    HARD_CONSTRAINT_INCLUDE_ELEMENT = "HARD_CONSTRAINT_INCLUDE_ELEMENT"
    HARD_CONSTRAINT_EXCLUDE_ELEMENT = "HARD_CONSTRAINT_EXCLUDE_ELEMENT"
    HARD_CONSTRAINT_NUM_SITES = "HARD_CONSTRAINT_NUM_SITES"
    HARD_CONSTRAINT_PROPERTY_RANGE = "HARD_CONSTRAINT_PROPERTY_RANGE"
    HARD_CONSTRAINT_METALLICITY = "HARD_CONSTRAINT_METALLICITY"
    HARD_CONSTRAINT_DIMENSIONALITY = "HARD_CONSTRAINT_DIMENSIONALITY"
    MISSING_REQUIRED_PROPERTY = "MISSING_REQUIRED_PROPERTY"
    INVALID_PROPERTY_UNIT = "INVALID_PROPERTY_UNIT"
    INVALID_PROPERTY_VALUE = "INVALID_PROPERTY_VALUE"
    INVALID_STRUCTURE = "INVALID_STRUCTURE"
    PREFILTER_PASSED = "PREFILTER_PASSED"


class CheckStatus(StrEnum):
    PASS = "PASS"
    FAIL = "FAIL"
    UNKNOWN = "UNKNOWN"


class CheckSeverity(StrEnum):
    INFO = "INFO"
    WARNING = "WARNING"
    BLOCKING = "BLOCKING"


class ApplicabilityStatus(StrEnum):
    APPLICABLE = "APPLICABLE"
    NOT_APPLICABLE = "NOT_APPLICABLE"
    UNKNOWN = "UNKNOWN"


class ApplicabilityReasonCode(StrEnum):
    APPLICABLE = "APPLICABLE"
    STRUCTURE_HASH_MISMATCH = "STRUCTURE_HASH_MISMATCH"
    STRUCTURE_UNPARSEABLE = "STRUCTURE_UNPARSEABLE"
    NON_PERIODIC_STRUCTURE = "NON_PERIODIC_STRUCTURE"
    NON_INORGANIC_STRUCTURE = "NON_INORGANIC_STRUCTURE"
    NUM_SITES_EXCEEDED = "NUM_SITES_EXCEEDED"
    UNSUPPORTED_ELEMENT = "UNSUPPORTED_ELEMENT"
    ELEMENT_COVERAGE_UNKNOWN = "ELEMENT_COVERAGE_UNKNOWN"
    DIMENSIONALITY_NOT_BULK = "DIMENSIONALITY_NOT_BULK"
    DIMENSIONALITY_UNKNOWN = "DIMENSIONALITY_UNKNOWN"
    STRUCTURE_CONVERSION_FAILED = "STRUCTURE_CONVERSION_FAILED"
    INVALID_NUMERIC_STRUCTURE_DATA = "INVALID_NUMERIC_STRUCTURE_DATA"
    INVALID_CELL_VOLUME = "INVALID_CELL_VOLUME"
    ATOM_OVERLAP = "ATOM_OVERLAP"
    MODEL_HEALTH_FAILED = "MODEL_HEALTH_FAILED"


class SelectionStatus(StrEnum):
    SELECTED = "SELECTED"
    NOT_SELECTED_BUDGET = "NOT_SELECTED_BUDGET"
    NOT_REQUESTED = "NOT_REQUESTED"
    PREFILTER_REJECTED = "PREFILTER_REJECTED"
    PREFILTER_UNCERTAIN = "PREFILTER_UNCERTAIN"
    NOT_APPLICABLE = "NOT_APPLICABLE"
    APPLICABILITY_UNKNOWN = "APPLICABILITY_UNKNOWN"


class ExecutionStatus(StrEnum):
    NOT_RUN = "NOT_RUN"
    DRY_RUN = "DRY_RUN"
    MOCK_COMPLETED = "MOCK_COMPLETED"
    CONVERGED = "CONVERGED"
    MAX_STEPS = "MAX_STEPS"
    INVALID_OUTPUT = "INVALID_OUTPUT"
    RUNTIME_FAILED = "RUNTIME_FAILED"


class RelaxationStatus(StrEnum):
    CONVERGED = "CONVERGED"
    MAX_STEPS = "MAX_STEPS"
    INVALID_OUTPUT = "INVALID_OUTPUT"
    RUNTIME_FAILED = "RUNTIME_FAILED"


class SmokeTestStatus(StrEnum):
    PASS = "PASS"
    FAIL = "FAIL"


class NativeStageStatus(StrEnum):
    SUCCEEDED = "SUCCEEDED"
    PARTIAL = "PARTIAL"
    RETRYABLE_FAILED = "RETRYABLE_FAILED"
    PERMANENT_FAILED = "PERMANENT_FAILED"


class NativeOutcomeType(StrEnum):
    COMPLETED = "Completed"
    FAILED = "Failed"


class WorkerResponseStatus(StrEnum):
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"


class InorganicReasonCode(StrEnum):
    TRUSTED_UPSTREAM_INORGANIC = "TRUSTED_UPSTREAM_INORGANIC"
    TRUSTED_UPSTREAM_ORGANIC = "TRUSTED_UPSTREAM_ORGANIC"
    NO_CARBON_PERIODIC_CRYSTAL = "NO_CARBON_PERIODIC_CRYSTAL"
    EXPLICIT_NON_INORGANIC_METADATA = "EXPLICIT_NON_INORGANIC_METADATA"
    EXPLICIT_MOLECULAR_SYSTEM = "EXPLICIT_MOLECULAR_SYSTEM"
    AMBIGUOUS_CARBON_COMPOSITION = "AMBIGUOUS_CARBON_COMPOSITION"
    CLASSIFICATION_INPUT_MISSING = "CLASSIFICATION_INPUT_MISSING"


class TrustedMaterialClass(StrEnum):
    INORGANIC = "INORGANIC"
    ORGANIC = "ORGANIC"
    MOLECULAR = "MOLECULAR"
    METAL_ORGANIC = "METAL_ORGANIC"
    UNKNOWN = "UNKNOWN"


class MLExecutionIdentity(StrictFrozenModel):
    model_id: str = Field(min_length=1)
    checkpoint_sha256: Sha256
    package_lock_sha256: Sha256
    adapter_version: str = Field(min_length=1)
    worker_protocol_version: Literal["agent02-worker-protocol-v1"] = (
        AGENT02_WORKER_PROTOCOL_VERSION
    )
    environment_fingerprint_sha256: Sha256
    is_mock: bool


class ArtifactPointer(StrictFrozenModel):
    uri: str = Field(min_length=1)
    sha256: Sha256


class ArtifactRef(ArtifactPointer):
    size_bytes: int = Field(ge=0)
    media_type: str = Field(min_length=1)


class NumericRange(StrictFrozenModel):
    min: float | None = None
    max: float | None = None
    unit: str

    @model_validator(mode="after")
    def validate_bounds(self) -> NumericRange:
        if self.min is None and self.max is None:
            raise ValueError("at least one range boundary is required")
        if self.min is not None and self.max is not None and self.min > self.max:
            raise ValueError("range min must not exceed max")
        for value in (self.min, self.max):
            if value is not None and not math.isfinite(value):
                raise ValueError("range boundaries must be finite")
        return self


class MLRequirementView(StrictFrozenModel):
    """Only the confirmed requirement fields Agent02 is allowed to interpret."""

    requirement_id: str
    revision: int = Field(ge=1)
    confirmed_by_user: bool
    allow_ml: bool
    include_elements: list[str] = Field(default_factory=list)
    exclude_elements: list[str] = Field(default_factory=list)
    band_gap_ev: NumericRange | None = None
    energy_above_hull_ev_atom: NumericRange | None = None
    is_metal: bool | None = None
    dimensionality: Literal[0, 1, 2, 3] | None = None
    max_num_sites: int | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def validate_elements(self) -> MLRequirementView:
        overlap = set(self.include_elements) & set(self.exclude_elements)
        if overlap:
            raise ValueError(
                f"elements cannot be both included and excluded: {sorted(overlap)}"
            )
        return self


class MLScreeningRequest(StrictFrozenModel):
    schema_version: Literal["agent02-request-v1"] = AGENT02_REQUEST_VERSION
    selection_mode: SelectionMode = SelectionMode.POLICY_TOP_N
    requested_candidate_ids: list[str] | None = None
    max_candidates: int = Field(default=5, ge=1, le=20)
    requested_tasks: list[MLTask] = Field(
        default_factory=lambda: [
            MLTask.STATIC_PREDICTION,
            MLTask.STRUCTURE_RELAXATION,
        ]
    )
    model_ref: Literal["chgnet-mptrj-0.3.0"] = "chgnet-mptrj-0.3.0"
    relaxation_profile: Literal["bulk-standard-v1"] = "bulk-standard-v1"
    allow_real_inference: bool = True

    @field_validator("requested_candidate_ids")
    @classmethod
    def require_unique_candidate_ids(
        cls, value: list[str] | None
    ) -> list[str] | None:
        if value is not None:
            if any(not candidate_id for candidate_id in value):
                raise ValueError("candidate IDs cannot be empty")
            if len(set(value)) != len(value):
                raise ValueError("requested candidate IDs must be unique")
        return value

    @field_validator("requested_tasks")
    @classmethod
    def freeze_requested_tasks(cls, value: list[MLTask]) -> list[MLTask]:
        required = {
            MLTask.STATIC_PREDICTION,
            MLTask.STRUCTURE_RELAXATION,
        }
        if set(value) != required or len(value) != len(required):
            raise ValueError(
                "v1 requires exactly static_prediction and "
                "structure_relaxation"
            )
        return [
            MLTask.STATIC_PREDICTION,
            MLTask.STRUCTURE_RELAXATION,
        ]

    @model_validator(mode="after")
    def validate_selection_mode(self) -> MLScreeningRequest:
        if self.selection_mode is SelectionMode.POLICY_TOP_N:
            if self.requested_candidate_ids is not None:
                raise ValueError(
                    "POLICY_TOP_N requires requested_candidate_ids=null"
                )
            if self.max_candidates > 5:
                raise ValueError("POLICY_TOP_N cannot exceed the automatic Top-5")
        else:
            if not self.requested_candidate_ids:
                raise ValueError(
                    "EXPLICIT_IDS requires at least one requested candidate"
                )
            if len(self.requested_candidate_ids) > self.max_candidates:
                raise ValueError(
                    "requested candidate count exceeds max_candidates"
                )
        return self


class MLScreeningPolicy(StrictFrozenModel):
    schema_version: Literal["ml-screening-policy-v1"] = AGENT02_POLICY_VERSION
    policy_version: Literal["ml-screening-policy-v1"] = AGENT02_POLICY_VERSION
    default_model_ref: Literal["chgnet-mptrj-0.3.0"] = (
        "chgnet-mptrj-0.3.0"
    )
    default_relaxation_profile: Literal["bulk-standard-v1"] = (
        "bulk-standard-v1"
    )
    automatic_max_candidates: Literal[5] = 5
    hard_max_candidates: Literal[20] = 20
    max_num_sites: Literal[100] = 100
    supported_dimensionality: Literal[3] = 3
    numeric_tolerance: float = Field(default=1e-8, gt=0)
    fmax_ev_angstrom: Literal[0.1] = 0.1
    relaxation_steps: Literal[200] = 200
    relax_cell: Literal[True] = True
    optimizer: Literal["FIRE"] = "FIRE"
    ase_filter: Literal["FrechetCellFilter"] = "FrechetCellFilter"
    minimum_interatomic_distance_angstrom: Literal[0.5] = 0.5
    maximum_energy_increase_ev_atom: Literal[0.0001] = 0.0001
    minimum_volume_ratio: Literal[0.8] = 0.8
    maximum_volume_ratio: Literal[1.2] = 1.2
    structure_matcher_ltol: Literal[0.2] = 0.2
    structure_matcher_stol: Literal[0.3] = 0.3
    structure_matcher_angle_tol_degrees: Literal[5.0] = 5.0
    health_snapshot_validity_seconds: int = Field(default=86_400, gt=0)
    inorganic_classifier_version: Literal[
        "inorganic-composition-policy-v1"
    ] = "inorganic-composition-policy-v1"


class ModelCard(StrictFrozenModel):
    schema_version: Literal["agent02-model-card-v1"] = "agent02-model-card-v1"
    model_id: str
    display_name: str
    summary: str
    intended_use: list[str]
    out_of_scope: list[str]
    training_data: str
    limitations: list[str]
    evidence_constraints: list[str]
    sources: list[str]
    is_mock: bool = False


class MLModelSpec(StrictFrozenModel):
    schema_version: Literal["agent02-model-spec-v1"] = "agent02-model-spec-v1"
    model_id: str
    adapter_type: str
    adapter_version: str
    package_name: str
    package_version: str
    checkpoint_name: str
    checkpoint_artifact_uri: str
    checkpoint_sha256: Sha256
    package_lock_uri: str
    package_lock_sha256: Sha256
    license: str
    training_dataset: str
    training_method: str
    supported_tasks: list[MLTask]
    supported_properties: list[str]
    supported_elements: list[str]
    supported_elements_source: str
    element_coverage_complete: bool
    supported_dimensionalities: list[Literal[0, 1, 2, 3]]
    input_requirements: list[str]
    max_num_sites_policy: int = Field(ge=1)
    supported_devices: list[str]
    native_uncertainty: bool
    known_limitations: list[str]
    model_card_uri: str
    model_card_sha256: Sha256
    is_mock: bool = False

    @model_validator(mode="after")
    def validate_unique_capabilities(self) -> MLModelSpec:
        collections = (
            self.supported_tasks,
            self.supported_properties,
            self.supported_elements,
            self.supported_dimensionalities,
            self.supported_devices,
        )
        if any(len(items) != len(set(items)) for items in collections):
            raise ValueError("model capability lists must not contain duplicates")
        return self

    def validate_execution_identity(
        self,
        identity: MLExecutionIdentity,
    ) -> None:
        expected = (
            self.model_id,
            self.checkpoint_sha256,
            self.package_lock_sha256,
            self.adapter_version,
            self.is_mock,
        )
        observed = (
            identity.model_id,
            identity.checkpoint_sha256,
            identity.package_lock_sha256,
            identity.adapter_version,
            identity.is_mock,
        )
        if observed != expected:
            raise ValueError(
                "model registry spec differs from execution identity"
            )


class MLModelRegistry(StrictFrozenModel):
    schema_version: Literal["model-registry-v1"] = (
        AGENT02_MODEL_REGISTRY_VERSION
    )
    models: list[MLModelSpec]

    @model_validator(mode="after")
    def require_unique_model_ids(self) -> MLModelRegistry:
        model_ids = [model.model_id for model in self.models]
        if len(model_ids) != len(set(model_ids)):
            raise ValueError("model registry IDs must be unique")
        return self

    def resolve(self, model_id: str) -> MLModelSpec:
        for model in self.models:
            if model.model_id == model_id:
                return model
        raise KeyError(f"model is not registered: {model_id}")


class ModelHealthSnapshot(StrictFrozenModel):
    schema_version: Literal["agent02-model-health-v1"] = (
        AGENT02_MODEL_HEALTH_VERSION
    )
    model_id: str
    checkpoint_sha256: Sha256
    package_lock_sha256: Sha256
    worker_protocol_version: Literal["agent02-worker-protocol-v1"] = (
        AGENT02_WORKER_PROTOCOL_VERSION
    )
    python_version: str
    platform: str
    architecture: str
    device_policy: str
    available_devices: list[str]
    smoke_test_status: SmokeTestStatus
    parity_metrics: dict[str, float] | None = None
    tested_at: datetime
    expires_at: datetime
    installed_package_versions: dict[str, str]
    environment_fingerprint_sha256: Sha256
    adapter_version: str
    is_mock: bool = False

    @model_validator(mode="after")
    def validate_health_window_and_versions(self) -> ModelHealthSnapshot:
        if self.tested_at.tzinfo is None or self.expires_at.tzinfo is None:
            raise ValueError("health timestamps must be timezone-aware")
        if self.tested_at >= self.expires_at:
            raise ValueError("tested_at must be earlier than expires_at")
        if not self.available_devices:
            raise ValueError("available_devices cannot be empty")
        if len(self.available_devices) != len(set(self.available_devices)):
            raise ValueError("available_devices must be unique")
        if self.device_policy not in self.available_devices:
            raise ValueError("device_policy must be present in available_devices")
        if not self.installed_package_versions or any(
            not name or not version
            for name, version in self.installed_package_versions.items()
        ):
            raise ValueError("installed package versions must be non-empty")
        real_required = {"chgnet", "torch", "pymatgen", "ase", "numpy"}
        if self.is_mock:
            forbidden = real_required & set(self.installed_package_versions)
            if forbidden:
                raise ValueError(
                    "mock health cannot invent heavy package versions: "
                    f"{sorted(forbidden)}"
                )
        elif not real_required.issubset(self.installed_package_versions):
            missing = sorted(
                real_required - set(self.installed_package_versions)
            )
            raise ValueError(
                f"real health is missing installed package versions: {missing}"
            )
        if self.parity_metrics is not None and any(
            not math.isfinite(value) for value in self.parity_metrics.values()
        ):
            raise ValueError("health parity metrics must be finite")
        return self

    def execution_identity(self) -> MLExecutionIdentity:
        return MLExecutionIdentity(
            model_id=self.model_id,
            checkpoint_sha256=self.checkpoint_sha256,
            package_lock_sha256=self.package_lock_sha256,
            adapter_version=self.adapter_version,
            worker_protocol_version=self.worker_protocol_version,
            environment_fingerprint_sha256=(
                self.environment_fingerprint_sha256
            ),
            is_mock=self.is_mock,
        )


class CandidateProperty(StrictFrozenModel):
    name: str
    value: float | int | bool | str | None
    unit: str
    evidence_level: str = EvidenceLevel.L1_RETRIEVED


class TrustedMaterialClassification(StrictFrozenModel):
    material_class: TrustedMaterialClass
    source: str = Field(min_length=1)
    classifier_version: str = Field(min_length=1)
    provenance_uri: str = Field(min_length=1)
    provenance_sha256: Sha256
    trusted: Literal[True] = True


class InorganicCompositionAssessment(StrictFrozenModel):
    classifier_version: Literal["inorganic-composition-policy-v1"] = (
        "inorganic-composition-policy-v1"
    )
    status: CheckStatus
    reason_code: InorganicReasonCode
    input_source: str = Field(min_length=1)
    input_provenance: dict[str, str]
    used_trusted_upstream_classification: bool

    @model_validator(mode="after")
    def validate_reason_status(self) -> InorganicCompositionAssessment:
        expected = {
            InorganicReasonCode.TRUSTED_UPSTREAM_INORGANIC: CheckStatus.PASS,
            InorganicReasonCode.TRUSTED_UPSTREAM_ORGANIC: CheckStatus.FAIL,
            InorganicReasonCode.NO_CARBON_PERIODIC_CRYSTAL: CheckStatus.PASS,
            InorganicReasonCode.EXPLICIT_NON_INORGANIC_METADATA: (
                CheckStatus.FAIL
            ),
            InorganicReasonCode.EXPLICIT_MOLECULAR_SYSTEM: CheckStatus.FAIL,
            InorganicReasonCode.AMBIGUOUS_CARBON_COMPOSITION: (
                CheckStatus.UNKNOWN
            ),
            InorganicReasonCode.CLASSIFICATION_INPUT_MISSING: (
                CheckStatus.UNKNOWN
            ),
        }[self.reason_code]
        if self.status is not expected:
            raise ValueError("inorganic reason code and status disagree")
        if not self.input_provenance or any(
            not key or not value
            for key, value in self.input_provenance.items()
        ):
            raise ValueError(
                "inorganic classification provenance cannot be empty"
            )
        trusted_reason = self.reason_code in {
            InorganicReasonCode.TRUSTED_UPSTREAM_INORGANIC,
            InorganicReasonCode.TRUSTED_UPSTREAM_ORGANIC,
            InorganicReasonCode.EXPLICIT_MOLECULAR_SYSTEM,
        }
        if self.used_trusted_upstream_classification != trusted_reason:
            raise ValueError(
                "trusted upstream marker disagrees with classification reason"
            )
        return self


class StructureRef(StrictFrozenModel):
    structure_id: str
    uri: str
    sha256: Sha256
    num_sites: int | None = Field(default=None, ge=1)
    elements: list[str] = Field(default_factory=list)
    dimensionality: Literal[0, 1, 2, 3] | None = None
    is_periodic: bool | None = None
    is_inorganic: bool | None = None
    trusted_material_classification: TrustedMaterialClassification | None = (
        None
    )
    parseable: bool | None = None
    ase_compatible: bool | None = None
    has_finite_values: bool | None = None
    positive_volume: bool | None = None
    minimum_distance_angstrom: float | None = Field(default=None, ge=0)
    hash_verified: bool | None = None


class MLCandidateInput(StrictFrozenModel):
    candidate_id: str
    upstream_manifest_uri: str
    upstream_manifest_sha256: Sha256
    formula: str
    elements: list[str]
    num_sites: int | None = Field(default=None, ge=1)
    publication_rank: int | None = Field(default=None, ge=1)
    upstream_decision: MLDecision
    upstream_evidence_level: EvidenceLevel = EvidenceLevel.L1_RETRIEVED
    properties: list[CandidateProperty] = Field(default_factory=list)
    source_structure: StructureRef

    @model_validator(mode="after")
    def validate_structure_identity(self) -> MLCandidateInput:
        if (
            self.num_sites is not None
            and self.source_structure.num_sites is not None
            and self.num_sites != self.source_structure.num_sites
        ):
            raise ValueError("candidate and structure num_sites differ")
        if self.source_structure.elements and set(self.elements) != set(
            self.source_structure.elements
        ):
            raise ValueError("candidate and structure elements differ")
        return self


class PreFilterEvaluation(StrictFrozenModel):
    candidate_id: str
    decision: MLDecision
    reason_codes: list[PreFilterReasonCode]
    matched_constraints: list[str] = Field(default_factory=list)
    mismatched_constraints: list[str] = Field(default_factory=list)
    missing_constraints: list[str] = Field(default_factory=list)


class ApplicabilityCheck(StrictFrozenModel):
    check_id: str
    status: CheckStatus
    severity: CheckSeverity
    expected: Any = None
    observed: Any = None
    source: str
    message: str
    reason_code: ApplicabilityReasonCode


class ApplicabilityAssessment(StrictFrozenModel):
    candidate_id: str
    structure_id: str
    model_id: str
    status: ApplicabilityStatus
    eligible_for_real_inference: bool
    eligible_for_l2: bool
    inorganic_classification: InorganicCompositionAssessment
    checks: list[ApplicabilityCheck]
    reasons: list[ApplicabilityReasonCode]
    warnings: list[str] = Field(default_factory=list)
    policy_version: str

    @model_validator(mode="after")
    def validate_eligibility(self) -> ApplicabilityAssessment:
        if (
            self.eligible_for_real_inference
            and self.status is not ApplicabilityStatus.APPLICABLE
        ):
            raise ValueError(
                "only applicable candidates can be eligible for real inference"
            )
        if self.eligible_for_l2 and not self.eligible_for_real_inference:
            raise ValueError("L2 eligibility requires real inference eligibility")
        inorganic_check = next(
            (
                check
                for check in self.checks
                if check.check_id == "inorganic_composition"
            ),
            None,
        )
        if inorganic_check is None:
            raise ValueError("inorganic composition check is required")
        if inorganic_check.status is not self.inorganic_classification.status:
            raise ValueError(
                "inorganic assessment and applicability check disagree"
            )
        return self


class PlannedCandidate(StrictFrozenModel):
    candidate: MLCandidateInput
    pre_filter: PreFilterEvaluation
    applicability: ApplicabilityAssessment
    selection_status: SelectionStatus

    @model_validator(mode="after")
    def validate_planned_candidate(self) -> PlannedCandidate:
        if self.pre_filter.candidate_id != self.candidate.candidate_id:
            raise ValueError("pre-filter candidate ID differs from candidate")
        if self.applicability.candidate_id != self.candidate.candidate_id:
            raise ValueError("applicability candidate ID differs from candidate")
        if (
            self.applicability.structure_id
            != self.candidate.source_structure.structure_id
        ):
            raise ValueError(
                "applicability structure differs from candidate source"
            )
        if self.selection_status in {
            SelectionStatus.SELECTED,
            SelectionStatus.NOT_SELECTED_BUDGET,
        } and (
            self.pre_filter.decision is not MLDecision.PASS
            or self.applicability.status is not ApplicabilityStatus.APPLICABLE
        ):
            raise ValueError(
                "selected/budget plan entries must pass both gates"
            )
        if (
            self.selection_status is SelectionStatus.NOT_APPLICABLE
            and self.applicability.status
            is not ApplicabilityStatus.NOT_APPLICABLE
        ):
            raise ValueError(
                "NOT_APPLICABLE plan entry requires matching assessment"
            )
        if (
            self.selection_status is SelectionStatus.APPLICABILITY_UNKNOWN
            and self.applicability.status is not ApplicabilityStatus.UNKNOWN
        ):
            raise ValueError(
                "APPLICABILITY_UNKNOWN plan entry requires unknown assessment"
            )
        return self


class ResourceEstimate(StrictFrozenModel):
    requested_candidate_count: int = Field(ge=0)
    inference_candidate_count: int = Field(ge=0)
    maximum_num_sites: int = Field(ge=0)
    maximum_relaxation_steps: Literal[200] = 200
    device_policy: str
    estimated_wall_time: str
    estimated_memory: str


class MLStagePlan(StrictFrozenModel):
    schema_version: Literal["agent02-stage-plan-v1"] = (
        AGENT02_STAGE_PLAN_VERSION
    )
    project_id: str
    run_id: str
    requirement_revision: int = Field(ge=1)
    attempt: int = Field(ge=1)
    orchestrator_input_snapshot: ArtifactPointer
    requirement_artifact: ArtifactPointer
    candidate_manifest_artifact: ArtifactPointer
    stage_request_artifact: ArtifactPointer | None = None
    policy_artifact: ArtifactPointer
    registry_artifact: ArtifactPointer
    health_artifact: ArtifactPointer
    selection_mode: SelectionMode
    selection_limit: int = Field(ge=1, le=20)
    manifest_candidate_count: int = Field(ge=0)
    requested_candidate_ids: list[str] | None
    requested_candidate_count: int = Field(ge=0, le=20)
    inference_candidate_ids: list[str]
    not_selected_candidate_ids: list[str]
    candidate_operation_keys: dict[str, Sha256]
    planned_candidates: list[PlannedCandidate]
    model_id: str
    checkpoint_sha256: Sha256
    package_lock_sha256: Sha256
    execution_identity: MLExecutionIdentity
    device_policy: str
    relaxation_profile: Literal["bulk-standard-v1"]
    requested_tasks: list[MLTask]
    allow_real_inference: bool
    resource_estimate: ResourceEstimate
    approval_required: bool
    policy_version: str
    created_at: datetime = Field(default_factory=utc_now)

    @model_validator(mode="after")
    def validate_candidate_sets(self) -> MLStagePlan:
        planned_ids = [
            item.candidate.candidate_id for item in self.planned_candidates
        ]
        if len(planned_ids) != len(set(planned_ids)):
            raise ValueError("planned candidate IDs must be unique")
        if self.requested_tasks != [
            MLTask.STATIC_PREDICTION,
            MLTask.STRUCTURE_RELAXATION,
        ]:
            raise ValueError(
                "v1 stage plan requires the two frozen ML tasks in order"
            )
        if self.manifest_candidate_count != len(planned_ids):
            raise ValueError(
                "manifest_candidate_count must match planned candidates"
            )
        selected = [
            item.candidate.candidate_id
            for item in self.planned_candidates
            if item.selection_status is SelectionStatus.SELECTED
        ]
        if selected != self.inference_candidate_ids:
            raise ValueError(
                "inference_candidate_ids must match selected candidates"
            )
        not_selected = [
            candidate_id
            for candidate_id in planned_ids
            if candidate_id not in set(selected)
        ]
        if not_selected != self.not_selected_candidate_ids:
            raise ValueError(
                "not_selected_candidate_ids must match the frozen plan"
            )
        if set(selected) & set(not_selected):
            raise ValueError("selected and not-selected IDs cannot overlap")
        if set(selected) | set(not_selected) != set(planned_ids):
            raise ValueError(
                "selected and not-selected IDs must cover the manifest"
            )
        if set(self.candidate_operation_keys) != set(selected):
            raise ValueError(
                "candidate operation keys must cover selected candidates"
            )
        candidate_by_id = {
            item.candidate.candidate_id: item.candidate
            for item in self.planned_candidates
        }
        if any(
            item.applicability.model_id != self.execution_identity.model_id
            or item.applicability.policy_version != self.policy_version
            for item in self.planned_candidates
        ):
            raise ValueError(
                "planned applicability model/policy differs from stage plan"
            )
        for candidate_id, operation_key in self.candidate_operation_keys.items():
            candidate = candidate_by_id[candidate_id]
            operation_payload = {
                "schema_version": "agent02-candidate-operation-v1",
                "project_id": self.project_id,
                "run_id": self.run_id,
                "candidate_id": candidate_id,
                "input_structure_sha256": (
                    candidate.source_structure.sha256
                ),
                "model_id": self.execution_identity.model_id,
                "checkpoint_sha256": (
                    self.execution_identity.checkpoint_sha256
                ),
                "package_lock_sha256": (
                    self.execution_identity.package_lock_sha256
                ),
                "environment_fingerprint_sha256": (
                    self.execution_identity.environment_fingerprint_sha256
                ),
                "adapter_version": self.execution_identity.adapter_version,
                "worker_protocol_version": (
                    self.execution_identity.worker_protocol_version
                ),
                "device_policy": self.device_policy,
                "policy_sha256": self.policy_artifact.sha256,
                "relaxation_profile": self.relaxation_profile,
                "requested_tasks": sorted(
                    task.value for task in self.requested_tasks
                ),
            }
            expected_key = hashlib.sha256(
                json.dumps(
                    operation_payload,
                    sort_keys=True,
                    separators=(",", ":"),
                    ensure_ascii=False,
                ).encode("utf-8")
            ).hexdigest()
            if operation_key != expected_key:
                raise ValueError(
                    "candidate operation key does not match frozen inputs"
                )
        if (
            self.resource_estimate.requested_candidate_count
            != self.requested_candidate_count
        ):
            raise ValueError(
                "resource requested count must match the frozen request"
            )
        if (
            self.resource_estimate.inference_candidate_count
            != len(self.inference_candidate_ids)
        ):
            raise ValueError(
                "resource inference count must match selected candidates"
            )
        selected_maximum_sites = max(
            (
                candidate_by_id[candidate_id].num_sites
                or candidate_by_id[candidate_id].source_structure.num_sites
                or 0
                for candidate_id in selected
            ),
            default=0,
        )
        if (
            self.resource_estimate.maximum_num_sites
            != selected_maximum_sites
        ):
            raise ValueError(
                "resource maximum sites must match selected candidates"
            )
        if self.resource_estimate.device_policy != self.device_policy:
            raise ValueError(
                "resource and stage plan device policies must match"
            )
        if not (
            len(self.inference_candidate_ids)
            <= self.requested_candidate_count
            <= self.selection_limit
        ):
            raise ValueError(
                "inference/requested counts must respect selection_limit"
            )
        if self.selection_mode is SelectionMode.POLICY_TOP_N:
            if self.requested_candidate_ids is not None:
                raise ValueError(
                    "POLICY_TOP_N requires requested_candidate_ids=null"
                )
            if not 1 <= self.selection_limit <= 5:
                raise ValueError("POLICY_TOP_N selection_limit must be 1..5")
            expected_requested = min(
                self.manifest_candidate_count,
                self.selection_limit,
            )
            if self.requested_candidate_count != expected_requested:
                raise ValueError(
                    "policy requested count must be min(manifest, limit)"
                )
            if self.approval_required:
                raise ValueError("POLICY_TOP_N cannot require batch approval")
            if any(
                item.selection_status is SelectionStatus.NOT_REQUESTED
                for item in self.planned_candidates
            ):
                raise ValueError(
                    "POLICY_TOP_N candidates cannot be NOT_REQUESTED"
                )
        else:
            if not self.requested_candidate_ids:
                raise ValueError(
                    "EXPLICIT_IDS requires requested candidate IDs"
                )
            if not 1 <= self.selection_limit <= 20:
                raise ValueError("explicit selection_limit must be 1..20")
            if len(set(self.requested_candidate_ids)) != len(
                self.requested_candidate_ids
            ):
                raise ValueError("requested candidate IDs must be unique")
            if self.requested_candidate_count != len(
                self.requested_candidate_ids
            ):
                raise ValueError(
                    "explicit requested count must match requested IDs"
                )
            if not set(self.requested_candidate_ids).issubset(planned_ids):
                raise ValueError(
                    "explicit requested IDs must exist in the manifest"
                )
            if not set(selected).issubset(self.requested_candidate_ids):
                raise ValueError(
                    "explicit selected IDs must be explicitly requested"
                )
            requested_set = set(self.requested_candidate_ids)
            if any(
                (
                    item.candidate.candidate_id in requested_set
                    and item.selection_status is SelectionStatus.NOT_REQUESTED
                )
                or (
                    item.candidate.candidate_id not in requested_set
                    and item.selection_status is not SelectionStatus.NOT_REQUESTED
                )
                for item in self.planned_candidates
            ):
                raise ValueError(
                    "explicit request membership and selection status disagree"
                )
            required_approval = self.requested_candidate_count >= 6
            if self.approval_required is not required_approval:
                raise ValueError(
                    "explicit batch approval must be false for 1..5 and "
                    "true for 6..20 candidates"
                )
        if self.model_id != self.execution_identity.model_id:
            raise ValueError("plan model differs from execution identity")
        if (
            self.checkpoint_sha256
            != self.execution_identity.checkpoint_sha256
        ):
            raise ValueError(
                "plan checkpoint differs from execution identity"
            )
        if (
            self.package_lock_sha256
            != self.execution_identity.package_lock_sha256
        ):
            raise ValueError(
                "plan package lock differs from execution identity"
            )
        return self


class UncertaintyRecord(StrictFrozenModel):
    kind: Literal["NOT_AVAILABLE"] = "NOT_AVAILABLE"
    value: None = None
    unit: None = None
    calibrated: Literal[False] = False
    reason: str = (
        "CHGNet v1 uses a single pretrained checkpoint without calibrated "
        "predictive uncertainty."
    )


class StructureLineage(StrictFrozenModel):
    structure_id: str
    structure_uri: str
    structure_sha256: Sha256
    parent_structure_id: str
    transformation: Literal["ml_relaxation"] = "ml_relaxation"
    model_id: str
    checkpoint_sha256: Sha256
    policy_version: str
    is_mock: bool


def _validate_finite_numeric_tree(
    value: Any,
    *,
    path: str,
    allow_bool: bool = False,
) -> None:
    if isinstance(value, bool):
        if allow_bool:
            return
        raise ValueError(f"{path} must contain numeric values, not bool")
    if isinstance(value, (int, float)):
        if not math.isfinite(float(value)):
            raise ValueError(f"{path} must contain only finite values")
        return
    if isinstance(value, (list, tuple)):
        if not value:
            raise ValueError(f"{path} arrays cannot be empty")
        for index, item in enumerate(value):
            _validate_finite_numeric_tree(
                item,
                path=f"{path}[{index}]",
                allow_bool=allow_bool,
            )
        return
    raise ValueError(f"{path} must contain only numeric scalars or arrays")


def _numeric_shape(value: Any) -> tuple[int, ...]:
    if not isinstance(value, (list, tuple)):
        return ()
    if not value:
        raise ValueError("numeric arrays cannot be empty")
    child_shapes = [_numeric_shape(item) for item in value]
    if any(shape != child_shapes[0] for shape in child_shapes[1:]):
        raise ValueError("numeric arrays must be rectangular")
    return (len(value), *child_shapes[0])


class MLNumericArtifactRef(StrictFrozenModel):
    uri: str = Field(min_length=1)
    sha256: Sha256
    size_bytes: int = Field(gt=0)
    media_type: Literal["application/x-npz"] = "application/x-npz"
    array_key: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9_]*$")
    dtype: str = Field(
        pattern=r"^(?:float(?:16|32|64)|int(?:8|16|32|64)|uint(?:8|16|32|64))$"
    )
    shape: list[int] = Field(min_length=1)
    allow_pickle: Literal[False] = False

    @field_validator("shape")
    @classmethod
    def require_positive_shape(cls, value: list[int]) -> list[int]:
        if any(dimension <= 0 for dimension in value):
            raise ValueError("numeric artifact dimensions must be positive")
        return value


_PROPERTY_CATALOG: dict[str, dict[str, Any]] = {
    "mlip_potential_energy": {
        "unit": "eV/atom",
        "source": "value",
        "shape": (),
        "nonnegative": False,
    },
    "maximum_force": {
        "unit": "eV/angstrom",
        "source": "value",
        "shape": (),
        "nonnegative": True,
    },
    "forces": {
        "unit": "eV/angstrom",
        "source": "artifact",
        "shape_suffix": (3,),
        "rank": 2,
    },
    "stress": {
        "unit": "GPa",
        "source": "value",
        "shape": (3, 3),
        "symmetric": True,
    },
    "stress_trajectory": {
        "unit": "GPa",
        "source": "artifact",
        "shape_suffix": (3, 3),
        "rank": 3,
    },
    "site_magnetic_moments": {
        "unit": "mu_B",
        "source": "artifact",
        "rank": 1,
    },
}


class MLPropertyValue(StrictFrozenModel):
    property_name: str
    value: float | int | list[Any] | None = None
    artifact_ref: MLNumericArtifactRef | None = None
    unit: str
    evidence_level: EvidenceLevel
    method: str
    model_id: str
    checkpoint_sha256: Sha256
    input_structure_id: str
    output_structure_id: str | None = None
    num_sites: int | None = Field(default=None, gt=0)
    num_steps: int | None = Field(default=None, gt=0)
    is_mock: bool
    provenance: dict[str, Any] = Field(default_factory=dict)

    @field_validator("value", mode="before")
    @classmethod
    def reject_non_numeric_or_non_finite_value(cls, value: Any) -> Any:
        if value is not None:
            _validate_finite_numeric_tree(value, path="property value")
            _numeric_shape(value)
        return value

    @model_validator(mode="after")
    def require_one_value_source(self) -> MLPropertyValue:
        if (self.value is None) == (self.artifact_ref is None):
            raise ValueError(
                "ML property requires exactly one of value or artifact_ref"
            )
        catalog = _PROPERTY_CATALOG.get(self.property_name)
        if catalog is None:
            raise ValueError(
                f"unsupported Agent02 property: {self.property_name}"
            )
        if self.unit != catalog["unit"]:
            raise ValueError(
                f"{self.property_name} requires unit {catalog['unit']}"
            )
        expected_source = catalog["source"]
        if expected_source == "value" and self.value is None:
            raise ValueError(
                f"{self.property_name} must use an inline numeric value"
            )
        if expected_source == "artifact" and self.artifact_ref is None:
            raise ValueError(
                f"{self.property_name} must use a numeric artifact"
            )
        if self.value is not None:
            actual_shape = _numeric_shape(self.value)
            if actual_shape != catalog["shape"]:
                raise ValueError(
                    f"{self.property_name} requires shape "
                    f"{catalog['shape']}, got {actual_shape}"
                )
            if catalog.get("nonnegative") and float(self.value) < 0:
                raise ValueError(
                    f"{self.property_name} must be non-negative"
                )
            if catalog.get("symmetric"):
                matrix = self.value
                assert isinstance(matrix, list)
                if any(
                    not math.isclose(
                        float(matrix[row][column]),
                        float(matrix[column][row]),
                        rel_tol=0.0,
                        abs_tol=1e-12,
                    )
                    for row in range(3)
                    for column in range(3)
                ):
                    raise ValueError("stress tensor must be symmetric")
        if self.artifact_ref is not None:
            shape = tuple(self.artifact_ref.shape)
            if len(shape) != catalog["rank"]:
                raise ValueError(
                    f"{self.property_name} numeric artifact has wrong rank"
                )
            suffix = catalog.get("shape_suffix")
            if suffix and shape[-len(suffix) :] != suffix:
                raise ValueError(
                    f"{self.property_name} numeric artifact has wrong shape"
                )
            if self.property_name in {
                "forces",
                "site_magnetic_moments",
            }:
                if self.num_sites is None or shape[0] != self.num_sites:
                    raise ValueError(
                        f"{self.property_name} shape must match num_sites"
                    )
            elif self.property_name == "stress_trajectory":
                if self.num_steps is None or shape[0] != self.num_steps:
                    raise ValueError(
                        "stress trajectory shape must match num_steps"
                    )
        elif self.num_sites is not None or self.num_steps is not None:
            raise ValueError(
                "inline property values cannot declare artifact dimensions"
            )
        if self.is_mock and self.evidence_level is EvidenceLevel.L2_ML_SCREENED:
            raise ValueError("mock properties cannot claim L2 evidence")
        return self


class StaticPredictionRequest(StrictFrozenModel):
    candidate_id: str
    structure: StructureRef
    model_id: str
    requested_properties: list[str]


class StaticPredictionResult(StrictFrozenModel):
    candidate_id: str
    properties: list[MLPropertyValue]
    is_mock: bool
    warnings: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def preserve_mock_marker(self) -> StaticPredictionResult:
        if any(prop.is_mock != self.is_mock for prop in self.properties):
            raise ValueError("property mock markers must match result")
        return self


class RelaxationRequest(StrictFrozenModel):
    candidate_id: str
    structure: StructureRef
    model_id: str
    relaxation_profile: str
    device_policy: str


class MLRelaxationResult(StrictFrozenModel):
    candidate_id: str
    input_structure_id: str
    input_structure_uri: str
    input_structure_sha256: Sha256
    output_structure_id: str | None = None
    output_structure_uri: str | None = None
    output_structure_sha256: Sha256 | None = None
    model_id: str
    checkpoint_sha256: Sha256
    adapter_version: str
    execution_identity: MLExecutionIdentity
    device: str
    relaxation_profile: str
    status: RelaxationStatus
    num_steps: int = Field(ge=0, le=200)
    initial_energy_ev: float | None = None
    final_energy_ev: float | None = None
    initial_energy_ev_atom: float | None = None
    final_energy_ev_atom: float | None = None
    delta_energy_ev_atom: float | None = None
    initial_max_force_ev_angstrom: float | None = Field(default=None, ge=0)
    final_max_force_ev_angstrom: float | None = Field(default=None, ge=0)
    final_stress_gpa_3x3: list[list[float]] | None = None
    magmom_summary: dict[str, float] = Field(default_factory=dict)
    structure_drift: dict[str, float | bool] = Field(default_factory=dict)
    qc_passed: bool
    uncertainty: UncertaintyRecord = Field(default_factory=UncertaintyRecord)
    warnings: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)
    wall_time_seconds: float = Field(ge=0)
    is_mock: bool
    provenance: dict[str, Any] = Field(default_factory=dict)

    @field_validator(
        "final_stress_gpa_3x3",
        "magmom_summary",
        "structure_drift",
        mode="before",
    )
    @classmethod
    def reject_invalid_nested_numerics(
        cls,
        value: Any,
        info,
    ) -> Any:
        if value is None:
            return value
        if info.field_name == "structure_drift":
            if not isinstance(value, dict):
                raise ValueError("structure_drift must be an object")
            for key, item in value.items():
                _validate_finite_numeric_tree(
                    item,
                    path=f"structure_drift.{key}",
                    allow_bool=True,
                )
            return value
        if info.field_name == "magmom_summary":
            if not isinstance(value, dict):
                raise ValueError("magmom_summary must be an object")
            for key, item in value.items():
                _validate_finite_numeric_tree(
                    item,
                    path=f"magmom_summary.{key}",
                )
            return value
        _validate_finite_numeric_tree(value, path="final_stress_gpa_3x3")
        return value

    @model_validator(mode="after")
    def validate_output_structure(self) -> MLRelaxationResult:
        output_fields = (
            self.output_structure_id,
            self.output_structure_uri,
            self.output_structure_sha256,
        )
        if any(value is None for value in output_fields) and any(
            value is not None for value in output_fields
        ):
            raise ValueError("output structure fields must be all set or all null")
        numeric_values = (
            self.initial_energy_ev,
            self.final_energy_ev,
            self.initial_energy_ev_atom,
            self.final_energy_ev_atom,
            self.delta_energy_ev_atom,
            self.initial_max_force_ev_angstrom,
            self.final_max_force_ev_angstrom,
            self.wall_time_seconds,
        )
        if any(
            value is not None and not math.isfinite(value)
            for value in numeric_values
        ):
            raise ValueError("relaxation numeric values must be finite")
        if self.model_id != self.execution_identity.model_id:
            raise ValueError("relaxation model differs from execution identity")
        if self.checkpoint_sha256 != (
            self.execution_identity.checkpoint_sha256
        ):
            raise ValueError(
                "relaxation checkpoint differs from execution identity"
            )
        if self.adapter_version != self.execution_identity.adapter_version:
            raise ValueError(
                "relaxation adapter differs from execution identity"
            )
        if self.is_mock != self.execution_identity.is_mock:
            raise ValueError(
                "relaxation mock marker differs from execution identity"
            )
        if self.final_stress_gpa_3x3 is not None:
            shape = _numeric_shape(self.final_stress_gpa_3x3)
            if shape != (3, 3):
                raise ValueError("final stress must have shape 3x3")
            if any(
                not math.isclose(
                    self.final_stress_gpa_3x3[row][column],
                    self.final_stress_gpa_3x3[column][row],
                    rel_tol=0.0,
                    abs_tol=1e-12,
                )
                for row in range(3)
                for column in range(3)
            ):
                raise ValueError("final stress must be symmetric")
        if self.qc_passed and self.status is not RelaxationStatus.CONVERGED:
            raise ValueError("QC can pass only for a converged relaxation")
        if self.qc_passed and any(value is None for value in output_fields):
            raise ValueError("QC pass requires a complete output structure")
        if self.qc_passed:
            if (
                self.final_max_force_ev_angstrom is None
                or self.final_max_force_ev_angstrom > 0.1
            ):
                raise ValueError(
                    "QC pass requires final maximum force <= 0.1 eV/angstrom"
                )
            if (
                self.delta_energy_ev_atom is None
                or self.delta_energy_ev_atom > 0.0001
            ):
                raise ValueError(
                    "QC pass exceeds the maximum energy increase"
                )
            if self.final_stress_gpa_3x3 is None:
                raise ValueError("QC pass requires normalized final stress")
            volume_ratio = self.structure_drift.get("volume_ratio")
            structure_match = self.structure_drift.get("structure_match")
            if (
                isinstance(volume_ratio, bool)
                or not isinstance(volume_ratio, (int, float))
                or not 0.8 <= float(volume_ratio) <= 1.2
                or structure_match is not True
            ):
                raise ValueError(
                    "QC pass requires valid volume ratio and structure match"
                )
        return self


class MLCandidateResult(StrictFrozenModel):
    project_id: str
    run_id: str
    candidate_id: str
    candidate_operation_key: Sha256 | None = None
    upstream_manifest_uri: str
    upstream_manifest_sha256: Sha256
    source_structure_id: str
    source_structure_uri: str
    source_structure_sha256: Sha256
    pre_filter_decision: MLDecision
    applicability: ApplicabilityAssessment
    selection_status: SelectionStatus
    execution_status: ExecutionStatus
    decision: MLDecision
    evidence_level: EvidenceLevel
    execution_identity: MLExecutionIdentity
    ml_properties: list[MLPropertyValue] = Field(default_factory=list)
    relaxation_result: MLRelaxationResult | None = None
    structure_lineage: StructureLineage | None = None
    recommended_downstream_structure_id: str
    warnings: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def enforce_result_consistency(self) -> MLCandidateResult:
        if self.applicability.candidate_id != self.candidate_id:
            raise ValueError("applicability candidate ID differs from result")
        if self.applicability.structure_id != self.source_structure_id:
            raise ValueError("applicability structure differs from result")
        if (
            self.applicability.model_id
            != self.execution_identity.model_id
        ):
            raise ValueError("applicability model differs from execution")

        executed_statuses = {
            ExecutionStatus.MOCK_COMPLETED,
            ExecutionStatus.CONVERGED,
            ExecutionStatus.MAX_STEPS,
            ExecutionStatus.INVALID_OUTPUT,
            ExecutionStatus.RUNTIME_FAILED,
        }
        if self.selection_status is SelectionStatus.SELECTED:
            if self.execution_status is ExecutionStatus.NOT_RUN:
                raise ValueError("selected candidates require an execution state")
            if self.candidate_operation_key is None:
                raise ValueError(
                    "selected candidates require a candidate operation key"
                )
        else:
            if self.execution_status is not ExecutionStatus.NOT_RUN:
                raise ValueError(
                    "non-selected candidates must have execution_status=NOT_RUN"
                )
            if self.candidate_operation_key is not None:
                raise ValueError(
                    "non-selected candidates cannot have an operation key"
                )
        if self.execution_status in executed_statuses:
            if self.relaxation_result is None:
                raise ValueError(
                    "executed candidates require a relaxation result"
                )
        elif self.execution_status in {
            ExecutionStatus.NOT_RUN,
            ExecutionStatus.DRY_RUN,
        } and self.relaxation_result is not None:
            raise ValueError(
                "not-run and dry-run candidates cannot have relaxation output"
            )

        if self.relaxation_result is not None:
            relaxation = self.relaxation_result
            if relaxation.candidate_id != self.candidate_id:
                raise ValueError("relaxation candidate ID differs from result")
            if relaxation.input_structure_id != self.source_structure_id:
                raise ValueError("relaxation input structure differs from result")
            if relaxation.input_structure_uri != self.source_structure_uri:
                raise ValueError("relaxation input URI differs from result")
            if (
                relaxation.input_structure_sha256
                != self.source_structure_sha256
            ):
                raise ValueError("relaxation input hash differs from result")
            if relaxation.execution_identity != self.execution_identity:
                raise ValueError(
                    "relaxation execution identity differs from result"
                )
            expected_execution = {
                RelaxationStatus.CONVERGED: (
                    ExecutionStatus.MOCK_COMPLETED
                    if relaxation.is_mock
                    else ExecutionStatus.CONVERGED
                ),
                RelaxationStatus.MAX_STEPS: ExecutionStatus.MAX_STEPS,
                RelaxationStatus.INVALID_OUTPUT: (
                    ExecutionStatus.INVALID_OUTPUT
                ),
                RelaxationStatus.RUNTIME_FAILED: (
                    ExecutionStatus.RUNTIME_FAILED
                ),
            }[relaxation.status]
            if self.execution_status is not expected_execution:
                raise ValueError(
                    "execution status disagrees with relaxation status"
                )

        mock_sources = [self.execution_identity.is_mock]
        mock_sources.extend(prop.is_mock for prop in self.ml_properties)
        if self.relaxation_result is not None:
            mock_sources.append(self.relaxation_result.is_mock)
        if self.structure_lineage is not None:
            mock_sources.append(self.structure_lineage.is_mock)
        has_mock_source = any(mock_sources)

        for prop in self.ml_properties:
            if prop.model_id != self.execution_identity.model_id:
                raise ValueError("property model differs from execution identity")
            if (
                prop.checkpoint_sha256
                != self.execution_identity.checkpoint_sha256
            ):
                raise ValueError(
                    "property checkpoint differs from execution identity"
                )
            if prop.is_mock != self.execution_identity.is_mock:
                raise ValueError(
                    "property mock marker differs from execution identity"
                )
            if prop.input_structure_id != self.source_structure_id:
                raise ValueError("property input structure differs from result")

        output_structure_id = (
            self.relaxation_result.output_structure_id
            if self.relaxation_result is not None
            else None
        )
        if self.structure_lineage is not None:
            lineage = self.structure_lineage
            if lineage.parent_structure_id != self.source_structure_id:
                raise ValueError("lineage parent differs from source structure")
            if lineage.model_id != self.execution_identity.model_id:
                raise ValueError("lineage model differs from execution identity")
            if (
                lineage.checkpoint_sha256
                != self.execution_identity.checkpoint_sha256
            ):
                raise ValueError(
                    "lineage checkpoint differs from execution identity"
                )
            if lineage.is_mock != self.execution_identity.is_mock:
                raise ValueError(
                    "lineage mock marker differs from execution identity"
                )
            if output_structure_id != lineage.structure_id:
                raise ValueError(
                    "lineage structure differs from relaxation output"
                )
            relaxation = self.relaxation_result
            if relaxation is None:
                raise ValueError("lineage requires a relaxation result")
            if (
                lineage.structure_uri != relaxation.output_structure_uri
                or lineage.structure_sha256
                != relaxation.output_structure_sha256
            ):
                raise ValueError(
                    "lineage artifact differs from relaxation output"
                )

        for prop in self.ml_properties:
            if prop.output_structure_id is not None and (
                prop.output_structure_id != output_structure_id
            ):
                raise ValueError(
                    "property output structure differs from relaxation"
                )
            if (
                prop.num_steps is not None
                and self.relaxation_result is not None
                and prop.num_steps != self.relaxation_result.num_steps
            ):
                raise ValueError(
                    "property trajectory length differs from relaxation"
                )

        if has_mock_source:
            if self.evidence_level is EvidenceLevel.L2_ML_SCREENED:
                raise ValueError("mock candidate results cannot claim L2")
            if self.decision is MLDecision.PASS:
                raise ValueError("mock candidate results cannot claim PASS")
            if (
                self.recommended_downstream_structure_id
                != self.source_structure_id
            ):
                raise ValueError(
                    "mock results must recommend the source structure"
                )

        disallowed_l2_statuses = {
            ExecutionStatus.NOT_RUN,
            ExecutionStatus.DRY_RUN,
            ExecutionStatus.MOCK_COMPLETED,
            ExecutionStatus.MAX_STEPS,
            ExecutionStatus.INVALID_OUTPUT,
            ExecutionStatus.RUNTIME_FAILED,
        }
        if (
            self.execution_status in disallowed_l2_statuses
            or self.applicability.status is not ApplicabilityStatus.APPLICABLE
            or self.selection_status is not SelectionStatus.SELECTED
        ) and self.evidence_level is EvidenceLevel.L2_ML_SCREENED:
            raise ValueError(
                "non-converged or inapplicable results cannot claim L2"
            )

        if self.evidence_level is EvidenceLevel.L2_ML_SCREENED:
            relaxation = self.relaxation_result
            if (
                self.execution_identity.is_mock
                or self.decision is not MLDecision.PASS
                or self.execution_status is not ExecutionStatus.CONVERGED
                or not self.applicability.eligible_for_l2
                or relaxation is None
                or not relaxation.qc_passed
                or self.structure_lineage is None
                or output_structure_id is None
                or not any(
                    prop.evidence_level is EvidenceLevel.L2_ML_SCREENED
                    and not prop.is_mock
                    and prop.output_structure_id == output_structure_id
                    for prop in self.ml_properties
                )
            ):
                raise ValueError(
                    "L2 requires real applicable converged QC-passed "
                    "property, output structure, and lineage"
                )
        if self.decision is MLDecision.PASS:
            if self.evidence_level is not EvidenceLevel.L2_ML_SCREENED:
                raise ValueError("PASS requires L2 evidence")
            if (
                self.recommended_downstream_structure_id
                != output_structure_id
            ):
                raise ValueError(
                    "PASS must recommend the validated ML output structure"
                )
        elif (
            self.recommended_downstream_structure_id
            != self.source_structure_id
        ):
            raise ValueError(
                "non-PASS results must recommend the source structure"
            )

        if self.decision is MLDecision.FAILED:
            if self.execution_status not in {
                ExecutionStatus.INVALID_OUTPUT,
                ExecutionStatus.RUNTIME_FAILED,
                ExecutionStatus.NOT_RUN,
            }:
                raise ValueError(
                    "FAILED decision requires a failed execution state"
                )
        if (
            self.selection_status is SelectionStatus.NOT_APPLICABLE
            and self.applicability.status
            is not ApplicabilityStatus.NOT_APPLICABLE
        ):
            raise ValueError(
                "NOT_APPLICABLE selection requires matching applicability"
            )
        if (
            self.selection_status is SelectionStatus.APPLICABILITY_UNKNOWN
            and self.applicability.status is not ApplicabilityStatus.UNKNOWN
        ):
            raise ValueError(
                "APPLICABILITY_UNKNOWN requires unknown applicability"
            )
        if self.selection_status in {
            SelectionStatus.SELECTED,
            SelectionStatus.NOT_SELECTED_BUDGET,
        } and (
            self.pre_filter_decision is not MLDecision.PASS
            or self.applicability.status is not ApplicabilityStatus.APPLICABLE
        ):
            raise ValueError(
                "selected/budget candidates require passed pre-filter and "
                "applicable model domain"
            )
        if (
            self.selection_status is SelectionStatus.PREFILTER_REJECTED
            and self.pre_filter_decision
            not in {MLDecision.REJECT, MLDecision.FAILED}
        ):
            raise ValueError(
                "pre-filter rejected selection requires reject/failed decision"
            )
        if (
            self.selection_status is SelectionStatus.PREFILTER_UNCERTAIN
            and self.pre_filter_decision is not MLDecision.UNCERTAIN
        ):
            raise ValueError(
                "pre-filter uncertain selection requires uncertain decision"
            )
        if (
            self.decision is MLDecision.REJECT
            and self.pre_filter_decision is not MLDecision.REJECT
        ):
            raise ValueError("only deterministic pre-filter can reject")
        return self


class MLCandidateManifestRecord(StrictFrozenModel):
    schema_version: Literal["agent02-contract-v1"] = (
        AGENT02_CONTRACT_VERSION
    )
    candidate: MLCandidateResult
    scientific_rank: int | None = Field(default=None, ge=1)
    downstream_readiness_rank: int | None = Field(default=None, ge=1)


class MLCandidateResultSummary(StrictFrozenModel):
    candidate_id: str
    candidate_operation_key: Sha256 | None = None
    selection_status: SelectionStatus
    execution_status: ExecutionStatus
    decision: MLDecision
    evidence_level: EvidenceLevel
    execution_identity: MLExecutionIdentity

    @classmethod
    def from_result(
        cls,
        result: MLCandidateResult,
    ) -> MLCandidateResultSummary:
        return cls(
            candidate_id=result.candidate_id,
            candidate_operation_key=result.candidate_operation_key,
            selection_status=result.selection_status,
            execution_status=result.execution_status,
            decision=result.decision,
            evidence_level=result.evidence_level,
            execution_identity=result.execution_identity,
        )


class MLStageResultEnvelope(StrictFrozenModel):
    schema_version: Literal["agent02-contract-v1"] = (
        AGENT02_CONTRACT_VERSION
    )
    project_id: str
    run_id: str
    stage: Literal["agent02"] = "agent02"
    status: NativeStageStatus
    operation_key: str = Field(min_length=1)
    plan: ArtifactPointer
    execution_identity: MLExecutionIdentity
    candidate_manifest: ArtifactRef
    output_artifacts: list[ArtifactRef]
    candidate_ids: list[str]
    candidate_summaries: list[MLCandidateResultSummary]
    status_counts: dict[str, int]
    warnings: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)
    started_at: datetime
    finished_at: datetime
    provenance: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_stage_summary(self) -> MLStageResultEnvelope:
        if self.started_at.tzinfo is None or self.finished_at.tzinfo is None:
            raise ValueError("stage timestamps must be timezone-aware")
        if self.finished_at < self.started_at:
            raise ValueError("finished_at must not precede started_at")
        summary_ids = [
            summary.candidate_id for summary in self.candidate_summaries
        ]
        if len(summary_ids) != len(set(summary_ids)):
            raise ValueError("stage candidate IDs must be unique")
        if self.candidate_ids != summary_ids:
            raise ValueError(
                "candidate IDs must match candidate summary order"
            )
        expected_counts = dict(
            sorted(
                {
                    decision: sum(
                        summary.decision.value == decision
                        for summary in self.candidate_summaries
                    )
                    for decision in {
                        summary.decision.value
                        for summary in self.candidate_summaries
                    }
                }.items()
            )
        )
        if self.status_counts != expected_counts:
            raise ValueError(
                "status_counts must be recomputed from candidate decisions"
            )
        if any(
            summary.execution_identity != self.execution_identity
            for summary in self.candidate_summaries
        ):
            raise ValueError(
                "candidate and stage execution identities must match"
            )
        failed_count = sum(
            summary.decision is MLDecision.FAILED
            for summary in self.candidate_summaries
        )
        if self.status is NativeStageStatus.SUCCEEDED:
            if failed_count or self.errors:
                raise ValueError(
                    "SUCCEEDED cannot contain failed candidates or errors"
                )
        elif self.status is NativeStageStatus.PARTIAL:
            if not (
                0 < failed_count < len(self.candidate_summaries)
                and self.errors
            ):
                raise ValueError(
                    "PARTIAL requires both failed and non-failed candidates "
                    "plus stage errors"
                )
        else:
            if not self.errors:
                raise ValueError("failed stage envelopes require errors")
            if self.candidate_summaries and failed_count != len(
                self.candidate_summaries
            ):
                raise ValueError(
                    "failed stage envelopes require every candidate to fail"
                )
        if (
            self.execution_identity.is_mock
            and any(
                summary.decision is MLDecision.PASS
                or summary.evidence_level is EvidenceLevel.L2_ML_SCREENED
                for summary in self.candidate_summaries
            )
        ):
            raise ValueError("mock stage envelopes cannot contain PASS or L2")
        if len(self.output_artifacts) != len(
            {artifact.uri for artifact in self.output_artifacts}
        ):
            raise ValueError("stage output artifact URIs must be unique")
        return self

    def validate_against(
        self,
        *,
        plan: MLStagePlan,
        manifest: list[MLCandidateManifestRecord],
        operation_key: str,
    ) -> None:
        if self.project_id != plan.project_id or self.run_id != plan.run_id:
            raise ValueError("stage envelope project/run differs from plan")
        plan_sha256 = hashlib.sha256(
            json.dumps(
                plan.model_dump(mode="json"),
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
            ).encode("utf-8")
        ).hexdigest()
        if self.plan.sha256 != plan_sha256:
            raise ValueError("stage envelope plan hash differs from plan")
        if self.operation_key != operation_key:
            raise ValueError("stage envelope operation key differs")
        if self.execution_identity != plan.execution_identity:
            raise ValueError("stage envelope execution identity differs from plan")
        if self.candidate_ids != [
            record.candidate.candidate_id for record in manifest
        ]:
            raise ValueError("stage envelope candidate order differs from manifest")
        manifest_payload = (
            (
                "\n".join(
                    json.dumps(
                        record.model_dump(mode="json"),
                        sort_keys=True,
                        separators=(",", ":"),
                        ensure_ascii=False,
                    )
                    for record in manifest
                )
                + "\n"
            )
            if manifest
            else ""
        ).encode("utf-8")
        if (
            self.candidate_manifest.sha256
            != hashlib.sha256(manifest_payload).hexdigest()
            or self.candidate_manifest.size_bytes != len(manifest_payload)
        ):
            raise ValueError(
                "candidate manifest hash/size differs from records"
            )
        if self.candidate_summaries != [
            MLCandidateResultSummary.from_result(record.candidate)
            for record in manifest
        ]:
            raise ValueError(
                "stage envelope summaries differ from candidate manifest"
            )
        if self.candidate_ids != [
            item.candidate.candidate_id for item in plan.planned_candidates
        ]:
            raise ValueError("stage envelope candidate order differs from plan")
        readiness_ranks = [
            record.downstream_readiness_rank for record in manifest
        ]
        if any(rank is None for rank in readiness_ranks) or sorted(
            rank for rank in readiness_ranks if rank is not None
        ) != list(range(1, len(manifest) + 1)):
            raise ValueError(
                "downstream readiness ranks must be unique and contiguous"
            )
        for record in manifest:
            result = record.candidate
            if result.project_id != plan.project_id or result.run_id != plan.run_id:
                raise ValueError("candidate project/run differs from plan")
            expected_key = plan.candidate_operation_keys.get(
                result.candidate_id
            )
            if result.candidate_operation_key != expected_key:
                raise ValueError(
                    "candidate operation key differs from plan"
                )


class MLStageOutcome(StrictFrozenModel):
    schema_version: Literal["agent02-contract-v1"] = (
        AGENT02_CONTRACT_VERSION
    )
    outcome: NativeOutcomeType
    status: NativeStageStatus
    result: MLStageResultEnvelope | None = None
    errors: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_outcome(self) -> MLStageOutcome:
        completed = self.outcome is NativeOutcomeType.COMPLETED
        if completed:
            if (
                self.result is None
                or self.status
                not in {
                    NativeStageStatus.SUCCEEDED,
                    NativeStageStatus.PARTIAL,
                }
                or self.result.status is not self.status
                or self.errors
            ):
                raise ValueError(
                    "completed outcome requires a matching successful result"
                )
        elif (
            self.result is not None
            or self.status
            not in {
                NativeStageStatus.RETRYABLE_FAILED,
                NativeStageStatus.PERMANENT_FAILED,
            }
            or not self.errors
        ):
            raise ValueError(
                "failed outcome requires failure status and public errors"
            )
        return self


class WorkerInputArtifact(StrictFrozenModel):
    artifact_uri: str = Field(min_length=1)
    root_relative_path: str
    sha256: Sha256
    size_bytes: int = Field(ge=0)

    @field_validator("root_relative_path")
    @classmethod
    def validate_relative_path(cls, value: str) -> str:
        return validate_root_relative_path(value)


class WorkerLimits(StrictFrozenModel):
    wall_time_seconds: float = Field(gt=0)
    max_stdout_bytes: int = Field(gt=0)
    max_single_artifact_bytes: int = Field(gt=0)
    max_total_output_bytes: int = Field(gt=0)

    @model_validator(mode="after")
    def validate_limit_relationships(self) -> WorkerLimits:
        if self.max_single_artifact_bytes > self.max_total_output_bytes:
            raise ValueError(
                "single artifact limit cannot exceed total output limit"
            )
        return self


class MLNumericArtifactMetadata(StrictFrozenModel):
    array_key: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9_]*$")
    dtype: str = Field(
        pattern=r"^(?:float(?:16|32|64)|int(?:8|16|32|64)|uint(?:8|16|32|64))$"
    )
    shape: list[int] = Field(min_length=1)
    allow_pickle: Literal[False] = False

    @field_validator("shape")
    @classmethod
    def validate_shape(cls, value: list[int]) -> list[int]:
        if any(dimension <= 0 for dimension in value):
            raise ValueError("numeric artifact dimensions must be positive")
        return value


class WorkerProducedArtifact(StrictFrozenModel):
    root_relative_path: str
    sha256: Sha256
    size_bytes: int = Field(ge=0)
    media_type: str = Field(min_length=1)
    numeric_metadata: MLNumericArtifactMetadata | None = None

    @field_validator("root_relative_path")
    @classmethod
    def validate_relative_path(cls, value: str) -> str:
        return validate_root_relative_path(value)

    @model_validator(mode="after")
    def validate_numeric_media_type(self) -> WorkerProducedArtifact:
        if (
            self.numeric_metadata is not None
            and self.media_type != "application/x-npz"
        ):
            raise ValueError("numeric metadata requires application/x-npz")
        if (
            self.media_type == "application/x-npz"
            and self.numeric_metadata is None
        ):
            raise ValueError("NPZ artifacts require numeric metadata")
        return self


def validate_root_relative_path(value: str) -> str:
    if not value or value.strip() != value or "\\" in value:
        raise ValueError("artifact paths must be non-empty normalized POSIX paths")
    path = PurePosixPath(value)
    if path.is_absolute() or value.startswith("/"):
        raise ValueError("artifact paths must be root-relative")
    if any(part in {"", ".", ".."} for part in path.parts):
        raise ValueError("artifact paths cannot contain dot segments")
    normalized = path.as_posix()
    if normalized != value:
        raise ValueError("artifact paths must already be normalized")
    return value


class WorkerRequest(StrictFrozenModel):
    schema_version: Literal["agent02-worker-request-v1"] = (
        "agent02-worker-request-v1"
    )
    expected_handshake: MLExecutionIdentity
    plan: MLStagePlan
    candidate_id: str = Field(min_length=1)
    candidate_operation_key: Sha256
    inputs: list[WorkerInputArtifact] = Field(min_length=1)
    output_sandbox_relative_path: str
    limits: WorkerLimits

    @field_validator("output_sandbox_relative_path")
    @classmethod
    def validate_output_sandbox(cls, value: str) -> str:
        return validate_root_relative_path(value)

    @model_validator(mode="after")
    def validate_single_candidate_request(self) -> WorkerRequest:
        if self.expected_handshake != self.plan.execution_identity:
            raise ValueError("worker handshake differs from stage plan")
        if self.candidate_id not in self.plan.inference_candidate_ids:
            raise ValueError("worker request candidate is not selected")
        planned = next(
            item
            for item in self.plan.planned_candidates
            if item.candidate.candidate_id == self.candidate_id
        )
        expected_key = self.plan.candidate_operation_keys.get(
            self.candidate_id
        )
        if self.candidate_operation_key != expected_key:
            raise ValueError(
                "worker candidate operation key differs from stage plan"
            )
        if self.candidate_operation_key not in PurePosixPath(
            self.output_sandbox_relative_path
        ).parts:
            raise ValueError(
                "worker output sandbox must be bound to the operation key"
            )
        input_paths = [item.root_relative_path for item in self.inputs]
        if len(input_paths) != len(set(input_paths)):
            raise ValueError("worker input paths must be unique")
        structure = planned.candidate.source_structure
        if sum(
            item.artifact_uri == structure.uri
            and item.sha256 == structure.sha256
            for item in self.inputs
        ) != 1:
            raise ValueError(
                "worker inputs must contain the frozen candidate structure"
            )
        return self


class WorkerResponse(StrictFrozenModel):
    schema_version: Literal["agent02-worker-response-v1"] = (
        "agent02-worker-response-v1"
    )
    actual_handshake: MLExecutionIdentity
    candidate_id: str = Field(min_length=1)
    candidate_operation_key: Sha256
    status: WorkerResponseStatus
    candidate_result: MLCandidateResult | None = None
    produced_artifacts: list[WorkerProducedArtifact]
    warnings: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_response_shape(self) -> WorkerResponse:
        output_paths = [
            artifact.root_relative_path
            for artifact in self.produced_artifacts
        ]
        if len(output_paths) != len(set(output_paths)):
            raise ValueError("worker output paths must be unique")
        forbidden_names = {
            "operation-complete.json",
            "stage-result.json",
        }
        if any(
            PurePosixPath(path).name in forbidden_names
            for path in output_paths
        ):
            raise ValueError("worker cannot write authoritative ledgers")
        if self.status is WorkerResponseStatus.SUCCEEDED:
            if self.candidate_result is None or self.errors:
                raise ValueError(
                    "successful worker response requires one result and no errors"
                )
        elif self.candidate_result is not None or not self.errors:
            raise ValueError(
                "failed worker response requires errors and no candidate result"
            )
        if self.candidate_result is not None:
            if self.candidate_result.candidate_id != self.candidate_id:
                raise ValueError(
                    "worker response candidate IDs do not match"
                )
            if (
                self.candidate_result.candidate_operation_key
                != self.candidate_operation_key
            ):
                raise ValueError(
                    "worker result operation key differs from response"
                )
            if (
                self.candidate_result.execution_identity
                != self.actual_handshake
            ):
                raise ValueError(
                    "worker result execution identity differs from handshake"
                )
        return self

    def validate_against_request(self, request: WorkerRequest) -> None:
        if self.actual_handshake != request.expected_handshake:
            raise ValueError("worker actual handshake differs from expected")
        if self.candidate_id != request.candidate_id:
            raise ValueError("worker response candidate differs from request")
        if self.candidate_operation_key != request.candidate_operation_key:
            raise ValueError(
                "worker response operation key differs from request"
            )
        if self.candidate_result is not None:
            if (
                self.candidate_result.project_id != request.plan.project_id
                or self.candidate_result.run_id != request.plan.run_id
            ):
                raise ValueError(
                    "worker result project/run differs from stage plan"
                )
        sandbox = PurePosixPath(request.output_sandbox_relative_path)
        for artifact in self.produced_artifacts:
            path = PurePosixPath(artifact.root_relative_path)
            if path == sandbox or sandbox not in path.parents:
                raise ValueError(
                    "worker output is outside the candidate sandbox"
                )


class FakeStageArtifacts(StrictFrozenModel):
    plan: MLStagePlan
    manifest: list[MLCandidateManifestRecord]
    report_markdown: str
    worker_responses: list[WorkerResponse]


class MLModelAdapter(Protocol):
    def describe(self) -> MLModelSpec: ...

    def healthcheck(self, device: str) -> ModelHealthSnapshot: ...

    def predict_static(
        self,
        structure: StructureRef,
        request: StaticPredictionRequest,
    ) -> StaticPredictionResult: ...

    def relax(
        self,
        structure: StructureRef,
        request: RelaxationRequest,
    ) -> MLRelaxationResult: ...
