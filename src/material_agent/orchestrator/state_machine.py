"""Central state-transition policy for durable Orchestrator records."""

from __future__ import annotations

from enum import StrEnum

from material_agent.orchestrator.models import (
    ApprovalStatus,
    ExternalJobStatus,
    RunStatus,
    StageStatus,
)


class InvalidStateTransition(RuntimeError):
    """Raised when persisted control state would move backwards or sideways."""


_RUN_TRANSITIONS: dict[RunStatus, set[RunStatus]] = {
    RunStatus.INTAKE: {
        RunStatus.CLARIFYING,
        RunStatus.REQUIREMENT_REVIEW,
        RunStatus.PLANNED,
        RunStatus.RUNNING,
        RunStatus.CANCELLED,
        RunStatus.FAILED,
    },
    RunStatus.CLARIFYING: {
        RunStatus.REQUIREMENT_REVIEW,
        RunStatus.CANCELLED,
        RunStatus.FAILED,
    },
    RunStatus.REQUIREMENT_REVIEW: {
        RunStatus.REQUIREMENT_REVIEW,
        RunStatus.PLANNED,
        RunStatus.CANCELLED,
        RunStatus.FAILED,
    },
    RunStatus.PLANNED: {
        RunStatus.WAITING_APPROVAL,
        RunStatus.RUNNING,
        RunStatus.PAUSED,
        RunStatus.SUCCEEDED,
        RunStatus.PARTIAL,
        RunStatus.FAILED,
        RunStatus.CANCELLED,
    },
    RunStatus.WAITING_APPROVAL: {
        RunStatus.RUNNING,
        RunStatus.PAUSED,
        RunStatus.PARTIAL,
        RunStatus.FAILED,
        RunStatus.CANCELLED,
    },
    RunStatus.RUNNING: {
        RunStatus.WAITING_APPROVAL,
        RunStatus.PAUSED,
        RunStatus.SUCCEEDED,
        RunStatus.PARTIAL,
        RunStatus.FAILED,
        RunStatus.CANCELLED,
    },
    RunStatus.PAUSED: {
        RunStatus.WAITING_APPROVAL,
        RunStatus.RUNNING,
        RunStatus.SUCCEEDED,
        RunStatus.PARTIAL,
        RunStatus.FAILED,
        RunStatus.CANCELLED,
    },
    RunStatus.SUCCEEDED: set(),
    RunStatus.PARTIAL: set(),
    RunStatus.FAILED: set(),
    RunStatus.CANCELLED: set(),
}


_STAGE_TRANSITIONS: dict[StageStatus, set[StageStatus]] = {
    StageStatus.PENDING: {
        StageStatus.VALIDATING_INPUT,
        StageStatus.BLOCKED_MISSING_INPUT,
        StageStatus.CAPABILITY_UNAVAILABLE,
        StageStatus.SKIPPED,
        StageStatus.CANCELLED,
    },
    StageStatus.VALIDATING_INPUT: {
        StageStatus.BLOCKED_MISSING_INPUT,
        StageStatus.CAPABILITY_UNAVAILABLE,
        StageStatus.WAITING_APPROVAL,
        StageStatus.READY,
        StageStatus.CANCELLED,
    },
    StageStatus.BLOCKED_MISSING_INPUT: {
        StageStatus.VALIDATING_INPUT,
        StageStatus.CANCELLED,
    },
    StageStatus.CAPABILITY_UNAVAILABLE: {
        StageStatus.VALIDATING_INPUT,
        StageStatus.CANCELLED,
    },
    StageStatus.WAITING_APPROVAL: {
        StageStatus.READY,
        StageStatus.CANCELLED,
    },
    StageStatus.READY: {
        StageStatus.RUNNING,
        StageStatus.CANCELLED,
    },
    StageStatus.RUNNING: {
        StageStatus.RUNNING,
        StageStatus.RETRYABLE_FAILED,
        StageStatus.PERMANENT_FAILED,
        StageStatus.PARTIAL,
        StageStatus.SUCCEEDED,
        StageStatus.CANCELLED,
    },
    StageStatus.RETRYABLE_FAILED: {
        StageStatus.VALIDATING_INPUT,
        StageStatus.RUNNING,
        StageStatus.PERMANENT_FAILED,
        StageStatus.CANCELLED,
    },
    StageStatus.PERMANENT_FAILED: set(),
    StageStatus.PARTIAL: set(),
    StageStatus.SUCCEEDED: set(),
    StageStatus.CANCELLED: set(),
    StageStatus.SKIPPED: set(),
}


_APPROVAL_TRANSITIONS: dict[ApprovalStatus, set[ApprovalStatus]] = {
    ApprovalStatus.PENDING: {
        ApprovalStatus.APPROVED,
        ApprovalStatus.REJECTED,
        ApprovalStatus.REVISED,
        ApprovalStatus.CANCELLED,
    },
    ApprovalStatus.APPROVED: set(),
    ApprovalStatus.REJECTED: set(),
    ApprovalStatus.REVISED: set(),
    ApprovalStatus.CANCELLED: set(),
}


_EXTERNAL_TRANSITIONS: dict[ExternalJobStatus, set[ExternalJobStatus]] = {
    ExternalJobStatus.SUBMITTED: {
        ExternalJobStatus.RUNNING,
        ExternalJobStatus.SUCCEEDED,
        ExternalJobStatus.FAILED,
        ExternalJobStatus.CANCELLED,
        ExternalJobStatus.TIMEOUT,
    },
    ExternalJobStatus.RUNNING: {
        ExternalJobStatus.RUNNING,
        ExternalJobStatus.SUCCEEDED,
        ExternalJobStatus.FAILED,
        ExternalJobStatus.CANCELLED,
        ExternalJobStatus.TIMEOUT,
    },
    ExternalJobStatus.SUCCEEDED: set(),
    ExternalJobStatus.FAILED: set(),
    ExternalJobStatus.CANCELLED: set(),
    ExternalJobStatus.TIMEOUT: set(),
}


def validate_run_transition(
    current: RunStatus | str, target: RunStatus | str
) -> None:
    _validate("Run", RunStatus(current), RunStatus(target), _RUN_TRANSITIONS)


def validate_stage_transition(
    current: StageStatus | str, target: StageStatus | str
) -> None:
    _validate(
        "Stage", StageStatus(current), StageStatus(target), _STAGE_TRANSITIONS
    )


def validate_approval_transition(
    current: ApprovalStatus | str, target: ApprovalStatus | str
) -> None:
    _validate(
        "Approval",
        ApprovalStatus(current),
        ApprovalStatus(target),
        _APPROVAL_TRANSITIONS,
    )


def validate_external_transition(
    current: ExternalJobStatus | str, target: ExternalJobStatus | str
) -> None:
    _validate(
        "ExternalJob",
        ExternalJobStatus(current),
        ExternalJobStatus(target),
        _EXTERNAL_TRANSITIONS,
    )


def _validate(
    label: str,
    current: StrEnum,
    target: StrEnum,
    matrix: dict[StrEnum, set[StrEnum]],
) -> None:
    if current == target:
        return
    if target not in matrix[current]:
        raise InvalidStateTransition(
            f"{label} state cannot transition from {current.value} "
            f"to {target.value}"
        )
