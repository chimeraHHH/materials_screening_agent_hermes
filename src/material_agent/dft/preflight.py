"""Side-effect-free real-DFT preflight gate.

Preflight validates immutable snapshots that a caller has already obtained. It
does not perform network I/O, execute a workflow, or treat a READY decision as
scientific evidence.
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum

from pydantic import Field, model_validator

from .bridge_models import BridgeBackendDescriptor, BridgeHealth
from .models import DFTRequest, ExecutionMode, StrictModel, canonical_hash
from .policies import PolicyKind, PolicySnapshot, PolicyStatus, find_policy
from .workflows import WorkflowTemplate, unique_task_types


class PreflightStatus(StrEnum):
    READY = "READY"
    BLOCKED = "BLOCKED"


class PreflightIssue(StrictModel):
    code: str = Field(min_length=1)
    message: str = Field(min_length=1)
    remediation: str = Field(min_length=1)


class ApprovalSnapshot(StrictModel):
    approval_id: str = Field(min_length=1)
    status: str
    request_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    workflow_plan_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    approved_by: str | None = None
    approved_at: datetime | None = None
    expires_at: datetime | None = None

    @model_validator(mode="after")
    def approval_guard(self) -> ApprovalSnapshot:
        if self.status == "APPROVED" and (
            self.approved_by is None or self.approved_at is None
        ):
            raise ValueError("APPROVED approval snapshot requires approver and time")
        return self


class DFTPreflightInput(StrictModel):
    request: DFTRequest
    workflow_template: WorkflowTemplate
    policy_snapshots: tuple[PolicySnapshot, ...]
    approval: ApprovalSnapshot
    backend_descriptor: BridgeBackendDescriptor
    backend_health: BridgeHealth
    checked_at: datetime


class DFTPreflightResult(StrictModel):
    status: PreflightStatus
    checked_at: datetime
    issues: tuple[PreflightIssue, ...] = ()


def evaluate_real_preflight(input: DFTPreflightInput) -> DFTPreflightResult:
    """Fail closed unless all non-scientific prerequisites are frozen and ready."""

    request = input.request
    issues: list[PreflightIssue] = []

    def block(code: str, message: str, remediation: str) -> None:
        issues.append(PreflightIssue(code=code, message=message, remediation=remediation))

    if request.execution_mode is not ExecutionMode.REAL or request.is_mock:
        block("REAL_EXECUTION_REQUIRED", "preflight accepts only a non-mock REAL request", "create a new frozen REAL request after all P2 gates pass")
    if request.workflow_template_id != input.workflow_template.template_id:
        block("WORKFLOW_TEMPLATE_MISMATCH", "request does not match the supplied workflow template", "freeze the intended template into a new request")
    try:
        expected_task_types = unique_task_types(input.workflow_template.tasks)
    except ValueError as exc:
        block("AMBIGUOUS_WORKFLOW_TEMPLATE", str(exc), "use a template with one entry per task type")
    else:
        observed_task_types = tuple(task.task_type for task in request.task_specs)
        if observed_task_types != expected_task_types:
            block("WORKFLOW_TASK_MISMATCH", "request task sequence differs from the frozen template", "rebuild the request from the approved template")
    if any(task.is_mock for task in request.task_specs):
        block("MOCK_TASK_PRESENT", "a REAL request contains a mock task", "rebuild the request with only real tasks")

    method = find_policy(input.policy_snapshots, PolicyKind.METHOD)
    resource = find_policy(input.policy_snapshots, PolicyKind.RESOURCE)
    evidence = find_policy(input.policy_snapshots, PolicyKind.EVIDENCE)
    for kind, snapshot in ((PolicyKind.METHOD, method), (PolicyKind.RESOURCE, resource), (PolicyKind.EVIDENCE, evidence)):
        if snapshot is None:
            block("MISSING_POLICY_SNAPSHOT", f"{kind.value} policy snapshot is missing", "supply one immutable APPROVED policy snapshot")
        elif snapshot.status is not PolicyStatus.APPROVED:
            block("POLICY_NOT_APPROVED", f"{kind.value} policy is not APPROVED", "obtain expert approval for a new policy snapshot")
    if method is not None and request.method_policy_ref != method.artifact:
        block("METHOD_POLICY_REFERENCE_MISMATCH", "request method policy reference differs from snapshot", "create a new request bound to the approved method policy")
    if resource is not None and request.resource_policy_ref != resource.artifact:
        block("RESOURCE_POLICY_REFERENCE_MISMATCH", "request resource policy reference differs from snapshot", "create a new request bound to the approved resource policy")

    descriptor = input.backend_descriptor
    if descriptor.is_mock:
        block("MOCK_BACKEND", "real preflight cannot target a mock backend", "supply a registered non-mock backend descriptor")
    if descriptor.backend_id != request.backend_id:
        block("BACKEND_ID_MISMATCH", "backend descriptor differs from request backend", "freeze the intended backend in a new request")
    if input.backend_health.descriptor != descriptor:
        block("BACKEND_HEALTH_IDENTITY_MISMATCH", "health snapshot does not describe the submitted backend", "refresh health from the same authenticated backend")
    if input.backend_health.status != "READY":
        block("BACKEND_NOT_READY", "backend health is not READY", "restore backend health and obtain a new snapshot")
    unsupported = {
        task.task_type.value
        for task in request.task_specs
        if task.task_type.value not in descriptor.supported_task_types
    }
    if unsupported:
        block("BACKEND_TASK_UNSUPPORTED", "backend does not advertise all requested task types", "select a capability-compatible backend or workflow")

    approval = input.approval
    if request.approval_id != approval.approval_id:
        block("APPROVAL_ID_MISMATCH", "request approval ID differs from approval snapshot", "obtain approval for this exact request")
    if approval.status != "APPROVED":
        block("APPROVAL_NOT_GRANTED", "approval snapshot is not APPROVED", "obtain explicit approval before submission")
    if approval.workflow_plan_hash != request.workflow_plan_hash:
        block("APPROVAL_PLAN_HASH_MISMATCH", "approval is bound to a different workflow plan", "obtain a new approval for this plan")
    if approval.request_sha256 != canonical_hash(request):
        block("APPROVAL_REQUEST_HASH_MISMATCH", "approval is bound to different request content", "obtain a new approval for this request")
    now = input.checked_at.astimezone(UTC)
    if approval.expires_at is not None and approval.expires_at.astimezone(UTC) <= now:
        block("APPROVAL_EXPIRED", "approval snapshot has expired", "obtain a new approval")

    return DFTPreflightResult(
        status=PreflightStatus.BLOCKED if issues else PreflightStatus.READY,
        checked_at=input.checked_at,
        issues=tuple(issues),
    )
