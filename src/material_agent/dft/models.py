"""Strict, Orchestrator-independent native contracts for Agent03 v1."""

from __future__ import annotations

import math
import re
from datetime import UTC, datetime
from enum import StrEnum
from hashlib import sha256
from typing import Any, Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator, model_validator

DFT_CONTRACT_VERSION = "agent03-dft-contract-v1"
Sha256 = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, validate_assignment=True, allow_inf_nan=False)


class ExecutionMode(StrEnum):
    MOCK = "MOCK"
    REAL = "REAL"


class TaskType(StrEnum):
    PREFLIGHT = "PREFLIGHT"
    RELAXATION = "RELAXATION"
    STATIC_SCF = "STATIC_SCF"
    DOS = "DOS"
    BAND_NSCF = "BAND_NSCF"
    MOCK_TASK = "MOCK_TASK"


class JobStatus(StrEnum):
    CREATED = "CREATED"
    SUBMITTING = "SUBMITTING"
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    COMPLETING = "COMPLETING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    TIMEOUT = "TIMEOUT"
    CANCEL_REQUESTED = "CANCEL_REQUESTED"
    CANCELLED = "CANCELLED"
    UNKNOWN = "UNKNOWN"


class CancelResult(StrEnum):
    NOT_FOUND = "NOT_FOUND"
    ALREADY_TERMINAL = "ALREADY_TERMINAL"
    CANCEL_REQUESTED = "CANCEL_REQUESTED"
    CANCEL_CONFIRMED = "CANCEL_CONFIRMED"
    CANCEL_FAILED = "CANCEL_FAILED"
    UNKNOWN = "UNKNOWN"


class ClaimStatus(StrEnum):
    REQUESTED = "REQUESTED"
    PLANNED = "PLANNED"
    COMPUTED = "COMPUTED"
    VALIDATED = "VALIDATED"
    VALIDATED_WITH_WARNINGS = "VALIDATED_WITH_WARNINGS"
    NOT_SUPPORTED = "NOT_SUPPORTED"
    NOT_APPLICABLE = "NOT_APPLICABLE"
    INCONCLUSIVE = "INCONCLUSIVE"
    FAILED = "FAILED"
    NOT_EVALUATED_MOCK = "NOT_EVALUATED_MOCK"


class ArtifactRef(StrictModel):
    uri: str
    sha256: Sha256

    @field_validator("uri")
    @classmethod
    def safe_uri(cls, value: str) -> str:
        if not value or value.startswith(("/", "\\")) or re.match(r"^[A-Za-z]:[\\/]", value):
            raise ValueError("artifact URI must be non-empty and not an absolute path")
        if ".." in value.replace("\\", "/").split("/"):
            raise ValueError("artifact URI must not contain path traversal")
        if not re.match(r"^[A-Za-z][A-Za-z0-9+.-]*://[^\s]+$", value):
            raise ValueError("artifact URI must be a logical URI")
        return value


class CandidateDFTInput(StrictModel):
    candidate_id: str = Field(min_length=1)
    structure_id: str = Field(min_length=1)
    structure_artifact: ArtifactRef
    source_stage: str = Field(min_length=1)


class ClaimRequest(StrictModel):
    claim_type: str = Field(min_length=1)
    required_evidence_level: str = Field(min_length=1)
    priority: Literal["CORE", "SECONDARY"] = "CORE"
    operational_definition_ref: ArtifactRef | None = None


class ResourceEstimate(StrictModel):
    estimate_mode: Literal["MOCK_RULE_BASED", "POLICY_RULE_BASED"] = "MOCK_RULE_BASED"
    resource_class: Literal["TRIVIAL", "SMALL", "MEDIUM", "LARGE", "UNSUPPORTED"]
    confidence: Literal["LOW"] = "LOW"
    is_mock: bool = True
    scientific_cost_values: dict[str, float] | None = None

    @model_validator(mode="after")
    def resource_guard(self) -> ResourceEstimate:
        if self.is_mock and (self.estimate_mode != "MOCK_RULE_BASED" or self.scientific_cost_values is not None):
            raise ValueError("mock resource estimates must not contain scientific cost values")
        if not self.is_mock and self.estimate_mode != "POLICY_RULE_BASED":
            raise ValueError("real resource estimates require a frozen policy rule")
        if self.scientific_cost_values is not None and any(not math.isfinite(value) for value in self.scientific_cost_values.values()):
            raise ValueError("resource values must be finite")
        return self


class DFTTaskSpec(StrictModel):
    task_id: str = Field(min_length=1)
    task_revision: int = Field(default=1, ge=1)
    task_type: TaskType
    candidate_id: str = Field(min_length=1)
    input_structure: ArtifactRef
    parent_task_ids: tuple[str, ...] = ()
    input_artifacts: tuple[ArtifactRef, ...] = ()
    method_spec: ArtifactRef | None = None
    method_hash: Sha256 | None = None
    resource_spec: ResourceEstimate
    expected_outputs: tuple[str, ...] = ()
    validator_ids: tuple[str, ...] = ()
    task_input_hash: Sha256
    is_mock: bool

    @model_validator(mode="after")
    def validate_task(self) -> DFTTaskSpec:
        if (self.method_spec is None) != (self.method_hash is None):
            raise ValueError("method URI and hash must be provided together")
        if self.is_mock != self.resource_spec.is_mock:
            raise ValueError("task mock flag must agree with resource estimate")
        if self.is_mock and self.task_type is not TaskType.MOCK_TASK:
            raise ValueError("mock tasks must use MOCK_TASK")
        return self


class ExternalJobRef(StrictModel):
    external_job_ref_id: str = Field(min_length=1)
    backend_id: str = Field(min_length=1)
    backend_version: str = Field(min_length=1)
    adapter_version: str = Field(min_length=1)
    backend_task_id: str = Field(min_length=1)
    idempotency_key: str = Field(min_length=1)
    task_input_hash: Sha256
    is_mock: bool


class ClaimResult(StrictModel):
    claim_id: str = Field(min_length=1)
    candidate_id: str = Field(min_length=1)
    claim_type: str = Field(min_length=1)
    status: ClaimStatus
    evidence_level: str = Field(min_length=1)
    is_mock: bool
    limitations: tuple[str, ...] = ()

    @model_validator(mode="after")
    def claim_evidence_guard(self) -> ClaimResult:
        if self.is_mock and self.status is not ClaimStatus.NOT_EVALUATED_MOCK:
            raise ValueError("mock claim must be NOT_EVALUATED_MOCK")
        if self.is_mock and self.evidence_level == "L3_DFT_VALIDATED":
            raise ValueError("mock claim cannot have L3_DFT_VALIDATED evidence")
        if self.status is ClaimStatus.NOT_EVALUATED_MOCK and not self.is_mock:
            raise ValueError("NOT_EVALUATED_MOCK requires is_mock=true")
        return self


class DFTTaskResultEnvelope(StrictModel):
    schema_version: Literal["1.0.0"] = "1.0.0"
    task_id: str = Field(min_length=1)
    attempt_id: str = Field(min_length=1)
    task_input_hash: Sha256
    terminal_status: JobStatus
    output_artifacts: tuple[ArtifactRef, ...] = ()
    parsed_summary: dict[str, Any] = Field(default_factory=dict)
    warnings: tuple[str, ...] = ()
    errors: tuple[str, ...] = ()
    is_mock: bool


class DFTResultEnvelope(StrictModel):
    schema_version: Literal["1.0.0"] = "1.0.0"
    request_id: str = Field(min_length=1)
    external_job_ref: ExternalJobRef
    terminal_status: JobStatus
    workflow_plan_hash: Sha256
    task_results: tuple[DFTTaskResultEnvelope, ...]
    claim_results: tuple[ClaimResult, ...] = ()
    output_artifacts: tuple[ArtifactRef, ...] = ()
    warnings: tuple[str, ...] = ()
    errors: tuple[str, ...] = ()
    provenance: dict[str, Any] = Field(default_factory=dict)
    is_mock: bool

    @model_validator(mode="after")
    def result_guard(self) -> DFTResultEnvelope:
        if self.is_mock != self.external_job_ref.is_mock:
            raise ValueError("result and external job mock flags must agree")
        if self.is_mock and any(not task.is_mock for task in self.task_results):
            raise ValueError("mock result cannot contain real task results")
        if self.is_mock and any(not claim.is_mock for claim in self.claim_results):
            raise ValueError("mock result cannot contain real claim results")
        return self


class DFTRequest(StrictModel):
    schema_version: Literal["1.0.0"] = "1.0.0"
    request_id: str = Field(min_length=1)
    revision: int = Field(default=1, ge=1)
    project_id: str = Field(min_length=1)
    run_id: str = Field(min_length=1)
    stage_run_id: str = Field(min_length=1)
    execution_mode: ExecutionMode
    backend_id: str = Field(min_length=1)
    candidates: tuple[CandidateDFTInput, ...] = Field(min_length=1)
    requested_claims: tuple[ClaimRequest, ...] = Field(min_length=1)
    workflow_template_id: str = Field(min_length=1)
    workflow_plan_hash: Sha256
    task_specs: tuple[DFTTaskSpec, ...] = Field(min_length=1)
    resource_estimate: ResourceEstimate
    method_policy_ref: ArtifactRef | None = None
    resource_policy_ref: ArtifactRef | None = None
    approval_id: str | None = None
    upstream_snapshot_hash: Sha256
    is_mock: bool

    @model_validator(mode="after")
    def request_guard(self) -> DFTRequest:
        if self.is_mock != (self.execution_mode is ExecutionMode.MOCK):
            raise ValueError("request mock flag must match execution mode")
        if self.execution_mode is ExecutionMode.MOCK:
            if self.backend_id != "mock-dft":
                raise ValueError("MOCK requests must use mock-dft")
            if not all(task.is_mock for task in self.task_specs):
                raise ValueError("MOCK requests cannot contain real tasks")
        elif self.backend_id == "mock-dft":
            raise ValueError("REAL requests cannot use mock-dft")
        if self.execution_mode is ExecutionMode.REAL:
            if self.method_policy_ref is None or self.resource_policy_ref is None:
                raise ValueError("REAL requests require frozen method and resource policy references")
            if self.resource_estimate.is_mock or any(task.is_mock for task in self.task_specs):
                raise ValueError("REAL requests cannot contain mock estimates or tasks")
        if len({candidate.candidate_id for candidate in self.candidates}) != len(self.candidates):
            raise ValueError("candidate IDs must be unique")
        if any(task.candidate_id not in {c.candidate_id for c in self.candidates} for task in self.task_specs):
            raise ValueError("task candidate is not present in request")
        return self


def canonical_hash(value: BaseModel | dict[str, Any]) -> str:
    data = value.model_dump(mode="json") if isinstance(value, BaseModel) else value
    import json
    return sha256(json.dumps(data, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()
