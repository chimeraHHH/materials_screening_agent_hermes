"""Versioned, JSON-only contracts for the Orchestrator control plane."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Literal, TypedDict

from pydantic import BaseModel, ConfigDict, Field, model_validator


LEGACY_ORCHESTRATOR_CONTRACT_VERSION = "orchestrator-p0-v1"
P01_ORCHESTRATOR_CONTRACT_VERSION = "orchestrator-p0.1-v2"
ORCHESTRATOR_CONTRACT_VERSION = "orchestrator-p0.2-v3"
ORCHESTRATOR_STAGE_PLAN_VERSION = "orchestrator-stage-plan-v2"
ORCHESTRATOR_REPORT_VERSION = "orchestrator-report-p0.2-v3"


def utc_now() -> datetime:
    return datetime.now(UTC)


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True)


class RunStatus(StrEnum):
    INTAKE = "INTAKE"
    CLARIFYING = "CLARIFYING"
    REQUIREMENT_REVIEW = "REQUIREMENT_REVIEW"
    PLANNED = "PLANNED"
    WAITING_APPROVAL = "WAITING_APPROVAL"
    RUNNING = "RUNNING"
    PAUSED = "PAUSED"
    SUCCEEDED = "SUCCEEDED"
    PARTIAL = "PARTIAL"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class StageStatus(StrEnum):
    PENDING = "PENDING"
    VALIDATING_INPUT = "VALIDATING_INPUT"
    BLOCKED_MISSING_INPUT = "BLOCKED_MISSING_INPUT"
    CAPABILITY_UNAVAILABLE = "CAPABILITY_UNAVAILABLE"
    WAITING_APPROVAL = "WAITING_APPROVAL"
    READY = "READY"
    RUNNING = "RUNNING"
    RETRYABLE_FAILED = "RETRYABLE_FAILED"
    PERMANENT_FAILED = "PERMANENT_FAILED"
    PARTIAL = "PARTIAL"
    SUCCEEDED = "SUCCEEDED"
    CANCELLED = "CANCELLED"
    SKIPPED = "SKIPPED"


class StageId(StrEnum):
    RETRIEVAL = "retrieval"
    ML = "ml"
    DFT = "dft"
    MANY_BODY = "many_body"


STAGE_TO_AGENT: dict[StageId, str] = {
    StageId.RETRIEVAL: "agent01",
    StageId.ML: "agent02",
    StageId.DFT: "agent03",
    StageId.MANY_BODY: "agent04",
}
AGENT_TO_STAGE: dict[str, StageId] = {
    agent_id: stage_id for stage_id, agent_id in STAGE_TO_AGENT.items()
}


class StageDisposition(StrEnum):
    SELECTED = "SELECTED"
    SKIPPED = "SKIPPED"
    BLOCKED = "BLOCKED"
    UNAVAILABLE = "UNAVAILABLE"


class ControlOutcomeType(StrEnum):
    COMPLETED = "Completed"
    WAITING_EXTERNAL = "WaitingExternal"
    BLOCKED = "Blocked"
    FAILED = "Failed"


class ExternalJobStatus(StrEnum):
    SUBMITTED = "SUBMITTED"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"
    TIMEOUT = "TIMEOUT"


class InteractionType(StrEnum):
    CLARIFICATION = "CLARIFICATION"
    REQUIREMENT_CONFIRMATION = "REQUIREMENT_CONFIRMATION"
    EXPENSIVE_BATCH_APPROVAL = "EXPENSIVE_BATCH_APPROVAL"
    RETRY_CONFIRMATION = "RETRY_CONFIRMATION"


class ApprovalStatus(StrEnum):
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    REVISED = "REVISED"
    CANCELLED = "CANCELLED"


class ArtifactPointer(StrictModel):
    uri: str
    sha256: str


class StageCapability(StrictModel):
    stage: StageId
    agent_id: str
    registered: bool
    is_mock: bool = False
    required_inputs: list[str] = Field(default_factory=list)
    requires_approval: bool = False
    supports_external: bool = False
    unavailable_reason: str | None = None

    @model_validator(mode="after")
    def validate_agent_mapping(self) -> StageCapability:
        if STAGE_TO_AGENT[self.stage] != self.agent_id:
            raise ValueError("stage and agent_id mapping is inconsistent")
        if self.is_mock and not self.registered:
            raise ValueError("an unregistered capability cannot be a mock")
        if self.registered and self.unavailable_reason:
            raise ValueError(
                "a registered capability cannot have unavailable_reason"
            )
        return self


class StageRoute(StrictModel):
    stage: StageId
    agent_id: str
    required: bool
    disposition: StageDisposition
    reason: str
    required_inputs: list[str] = Field(default_factory=list)
    input_artifacts: dict[str, ArtifactPointer] = Field(default_factory=dict)
    required_gates: list[str] = Field(default_factory=list)
    capability: StageCapability

    @model_validator(mode="after")
    def validate_route(self) -> StageRoute:
        if self.agent_id != STAGE_TO_AGENT[self.stage]:
            raise ValueError("route agent_id does not match stage")
        if self.capability.stage != self.stage:
            raise ValueError("route capability does not match stage")
        return self


class ExecutionPlan(StrictModel):
    schema_version: Literal["orchestrator-p0.2-v3"] = (
        ORCHESTRATOR_CONTRACT_VERSION
    )
    plan_id: str
    run_id: str
    requirement_revision: int = Field(ge=1)
    requirement_artifact: ArtifactPointer
    routes: list[StageRoute]
    required_gates: list[str]
    input_snapshot_sha256: str
    policy_version: str = "orchestrator-routing-policy-v2"
    created_at: datetime

    @model_validator(mode="after")
    def require_four_ordered_routes(self) -> ExecutionPlan:
        expected = list(StageId)
        actual = [route.stage for route in self.routes]
        if actual != expected:
            raise ValueError(
                "routes must contain retrieval, ml, dft, and many_body in order"
            )
        return self

    @property
    def stages(self) -> list[str]:
        """Compatibility view for callers that only need selected agent IDs."""

        return [
            route.agent_id
            for route in self.routes
            if route.disposition is StageDisposition.SELECTED
        ]


class ControlError(StrictModel):
    category: str
    retryable: bool = False
    operation: str
    public_message: str


class StageInputValidation(StrictModel):
    valid: bool
    missing_fields: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)
    remediation: list[str] = Field(default_factory=list)
    error_code: str | None = None
    failure_status: StageStatus | None = None

    @model_validator(mode="after")
    def validate_failure_status(self) -> StageInputValidation:
        allowed = {
            StageStatus.BLOCKED_MISSING_INPUT,
            StageStatus.PERMANENT_FAILED,
        }
        if self.failure_status is not None and self.failure_status not in allowed:
            raise ValueError(
                "failure_status must be BLOCKED_MISSING_INPUT or PERMANENT_FAILED"
            )
        if self.valid and (self.error_code or self.failure_status):
            raise ValueError(
                "valid input cannot carry error_code or failure_status"
            )
        return self


class StageExecutionContext(StrictModel):
    project_id: str
    run_id: str
    stage: StageId
    agent_id: str
    attempt: int = Field(ge=1)
    requirement_revision: int = Field(ge=1)
    requirement_artifact: ArtifactPointer
    input_artifacts: dict[str, ArtifactPointer] = Field(default_factory=dict)
    capability: StageCapability
    input_snapshot: ArtifactPointer | None = None


def operation_input_sha256_for(
    *,
    project_id: str,
    run_id: str,
    requirement_revision: int,
    stage: StageId,
    agent_id: str,
    attempt: int,
    input_snapshot_sha256: str,
    native_plan_sha256: str,
    policy_version: str,
) -> str:
    payload = {
        "schema_version": ORCHESTRATOR_STAGE_PLAN_VERSION,
        "project_id": project_id,
        "run_id": run_id,
        "requirement_revision": requirement_revision,
        "stage": stage.value,
        "agent_id": agent_id,
        "attempt": attempt,
        "input_snapshot_sha256": input_snapshot_sha256,
        "native_plan_sha256": native_plan_sha256,
        "policy_version": policy_version,
    }
    encoded = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


class PreparedStagePlan(StrictModel):
    schema_version: Literal["orchestrator-stage-plan-v2"] = (
        ORCHESTRATOR_STAGE_PLAN_VERSION
    )
    project_id: str
    run_id: str
    requirement_revision: int = Field(ge=1)
    stage: StageId
    agent_id: str
    attempt: int = Field(ge=1)
    input_snapshot_uri: str
    input_snapshot_sha256: str
    native_plan_uri: str
    native_plan_sha256: str
    operation_input_sha256: str
    approval_required: bool = False
    gate_type: str | None = None
    resource_estimate: dict[str, Any] = Field(default_factory=dict)
    policy_version: str
    risk_summary: str = ""
    created_at: datetime

    @model_validator(mode="after")
    def validate_identity_and_hash(self) -> PreparedStagePlan:
        if STAGE_TO_AGENT[self.stage] != self.agent_id:
            raise ValueError("prepared plan stage and agent_id are inconsistent")
        if self.approval_required and not self.gate_type:
            raise ValueError("approval-required plan must provide gate_type")
        expected = operation_input_sha256_for(
            project_id=self.project_id,
            run_id=self.run_id,
            requirement_revision=self.requirement_revision,
            stage=self.stage,
            agent_id=self.agent_id,
            attempt=self.attempt,
            input_snapshot_sha256=self.input_snapshot_sha256,
            native_plan_sha256=self.native_plan_sha256,
            policy_version=self.policy_version,
        )
        if self.operation_input_sha256 != expected:
            raise ValueError("prepared plan operation input hash is invalid")
        return self


def effective_stage_approval(
    capability: StageCapability,
    prepared_plan: PreparedStagePlan,
) -> bool:
    """Merge the immutable capability floor with a runner-owned plan decision."""

    if (
        capability.stage != prepared_plan.stage
        or capability.agent_id != prepared_plan.agent_id
    ):
        raise ValueError("capability and prepared plan target different stages")
    return capability.requires_approval or prepared_plan.approval_required


class ControlStageOutcome(StrictModel):
    schema_version: Literal["orchestrator-p0.2-v3"] = (
        ORCHESTRATOR_CONTRACT_VERSION
    )
    stage: StageId
    agent_id: str
    outcome: ControlOutcomeType
    status: StageStatus
    idempotency_key: str
    native_result_uri: str | None = None
    native_result_sha256: str | None = None
    operation_ref: str | None = None
    external_job_ref: str | None = None
    external_status: ExternalJobStatus | None = None
    external_status_sequence: int | None = Field(default=None, ge=0)
    summary: dict[str, Any] = Field(default_factory=dict)
    errors: list[ControlError] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_references(self) -> ControlStageOutcome:
        if bool(self.native_result_uri) != bool(self.native_result_sha256):
            raise ValueError("native result URI and hash must be provided together")
        if (
            self.outcome is ControlOutcomeType.COMPLETED
            and self.status in {StageStatus.SUCCEEDED, StageStatus.PARTIAL}
            and not self.native_result_uri
        ):
            raise ValueError(
                "successful completion requires a native result URI and hash"
            )
        if self.outcome is ControlOutcomeType.WAITING_EXTERNAL:
            if not self.external_job_ref or self.external_status is None:
                raise ValueError(
                    "WaitingExternal requires external job reference and status"
                )
            if self.status is not StageStatus.RUNNING:
                raise ValueError("WaitingExternal must keep the stage RUNNING")
        return self


class StageExecutionRecord(StrictModel):
    run_id: str
    stage: StageId
    agent_id: str
    attempt: int = Field(ge=1)
    operation_key: str
    status: StageStatus
    plan_uri: str | None = None
    plan_sha256: str | None = None
    result_uri: str | None = None
    result_sha256: str | None = None
    error: ControlError | None = None
    updated_at: datetime

    @model_validator(mode="after")
    def validate_identity_and_references(self) -> StageExecutionRecord:
        if STAGE_TO_AGENT[self.stage] != self.agent_id:
            raise ValueError("stage execution stage and agent_id are inconsistent")
        if bool(self.plan_uri) != bool(self.plan_sha256):
            raise ValueError("stage plan URI and hash must be provided together")
        if bool(self.result_uri) != bool(self.result_sha256):
            raise ValueError("stage result URI and hash must be provided together")
        return self


class ExternalJobRecord(StrictModel):
    run_id: str
    stage: StageId
    attempt: int = Field(ge=1)
    backend: str
    external_job_ref: str
    status: ExternalJobStatus
    status_sequence: int = Field(ge=0)
    submit_operation_key: str
    result_uri: str | None = None
    result_sha256: str | None = None
    updated_at: datetime


class CancelOutcome(StrictModel):
    external_job_ref: str
    status: ExternalJobStatus
    idempotency_key: str
    message: str | None = None


class StageStartInput(StrictModel):
    schema_version: Literal["orchestrator-p0.2-v3"] = (
        ORCHESTRATOR_CONTRACT_VERSION
    )
    source_run_id: str
    requirement_revision: int = Field(ge=1)
    requirement_artifact_uri: str
    requirement_artifact_sha256: str
    artifacts: dict[str, ArtifactPointer] = Field(default_factory=dict)


class LLMCallAudit(StrictModel):
    provider: str
    provider_version: str
    model_id: str
    base_url: str
    prompt_version: str
    request_sha256: str
    response_sha256: str
    thinking_mode: str
    reasoning_effort: str
    response_format: str
    prompt_tokens: int | None = Field(default=None, ge=0)
    completion_tokens: int | None = Field(default=None, ge=0)
    total_tokens: int | None = Field(default=None, ge=0)


class ParseResult(StrictModel):
    requirement: dict[str, Any]
    clarification_questions: list[str] = Field(default_factory=list)
    parser_name: str
    parser_version: str
    llm_audit: LLMCallAudit | None = None


class PendingInteraction(StrictModel):
    interaction_id: str
    interaction_type: InteractionType
    run_id: str
    approval_id: str | None = None
    prompt: str
    payload: dict[str, Any]


class RuntimeInterrupt(StrictModel):
    interaction_id: str
    value: dict[str, Any]


class RuntimeView(StrictModel):
    project_id: str
    run_id: str
    status: RunStatus
    current_stage: str | None = None
    requirement_revision: int | None = None
    report_uri: str | None = None
    interrupts: list[RuntimeInterrupt] = Field(default_factory=list)
    stage_statuses: dict[str, str] = Field(default_factory=dict)
    candidate_ids: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    errors: list[dict[str, Any]] = Field(default_factory=list)


class OrchestratorState(TypedDict, total=False):
    schema_version: str
    project_id: str
    run_id: str
    thread_id: str
    run_mode: str
    direct_stage: str | None
    direct_stage_input: dict[str, Any] | None
    raw_request: str
    requirement_id: str
    initial_requirement: dict[str, Any] | None
    requirement_draft: dict[str, Any]
    requirement_revision: int
    requirement_artifact_uri: str
    requirement_artifact_sha256: str
    parser_name: str
    parser_version: str
    clarification_questions: list[str]
    execution_plan: dict[str, Any]
    execution_plan_uri: str
    execution_plan_sha256: str
    route_cursor: int
    current_route: dict[str, Any] | None
    stage_input_validation: dict[str, Any] | None
    prepared_stage_plan: dict[str, Any] | None
    prepared_stage_plan_uri: str | None
    prepared_stage_plan_sha256: str | None
    stage_plan_refs: dict[str, dict[str, str]]
    stage_approval_required: bool | None
    pending_control_outcome: dict[str, Any] | None
    run_status: str
    current_stage: str | None
    stage_statuses: dict[str, str]
    stage_outcomes: dict[str, dict[str, Any]]
    candidate_ids: list[str]
    pending_interaction: dict[str, Any] | None
    clarification_decision: str | None
    review_round: int
    review_decision: str | None
    stage_approval_decision: str | None
    retry_decision: str | None
    retrieval_fixture_uri: str | None
    retry_counters: dict[str, int]
    report_uri: str | None
    report_sha256: str | None
    warnings: list[str]
    errors: list[dict[str, Any]]
    created_at: str
    updated_at: str
