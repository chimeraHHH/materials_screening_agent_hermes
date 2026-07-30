"""Task-validation dependency gate before a domain-specific claim validator."""

from __future__ import annotations

from enum import StrEnum

from pydantic import Field

from .models import ClaimRequest, StrictModel
from .validation import TaskValidationResult, ValidationStatus
from .workflows import ClaimDependencyTemplate


class ClaimReadinessStatus(StrEnum):
    READY_FOR_DOMAIN_VALIDATOR = "READY_FOR_DOMAIN_VALIDATOR"
    BLOCKED = "BLOCKED"


class ClaimReadinessIssue(StrictModel):
    code: str = Field(min_length=1)
    message: str = Field(min_length=1)


class ClaimReadiness(StrictModel):
    claim_type: str = Field(min_length=1)
    status: ClaimReadinessStatus
    domain_validator_id: str | None = None
    issues: tuple[ClaimReadinessIssue, ...] = ()


def evaluate_claim_readiness(
    *,
    claim: ClaimRequest,
    dependency: ClaimDependencyTemplate | None,
    task_results: dict[str, TaskValidationResult],
    is_mock: bool,
) -> ClaimReadiness:
    """Check prerequisite task validation without creating a scientific claim."""

    issues: list[ClaimReadinessIssue] = []
    if is_mock:
        issues.append(ClaimReadinessIssue(
            code="MOCK_RESULT", message="mock task results cannot enter a claim validator"
        ))
    if dependency is None:
        issues.append(ClaimReadinessIssue(
            code="CLAIM_RULE_MISSING", message="workflow has no dependency rule for this claim"
        ))
    elif dependency.claim_type != claim.claim_type:
        issues.append(ClaimReadinessIssue(
            code="CLAIM_RULE_MISMATCH", message="dependency rule is for another claim type"
        ))
    else:
        for task_key in dependency.supporting_task_keys:
            result = task_results.get(task_key)
            if result is None:
                issues.append(ClaimReadinessIssue(
                    code="SUPPORTING_TASK_MISSING", message=f"supporting task has no validation result: {task_key}"
                ))
            elif result.status not in {
                ValidationStatus.VALID,
                ValidationStatus.VALID_WITH_WARNINGS,
            }:
                issues.append(ClaimReadinessIssue(
                    code="SUPPORTING_TASK_NOT_VALID", message=f"supporting task is not valid: {task_key} ({result.status.value})"
                ))
    return ClaimReadiness(
        claim_type=claim.claim_type,
        status=(ClaimReadinessStatus.BLOCKED if issues else ClaimReadinessStatus.READY_FOR_DOMAIN_VALIDATOR),
        domain_validator_id=(None if issues or dependency is None else dependency.domain_validator_id),
        issues=tuple(issues),
    )
