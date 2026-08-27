"""Fail-closed validation contracts for future non-mock DFT task results.

This module deliberately consumes already-parsed, structured execution facts.
It does not parse VASP files, render VASP inputs, contact a backend, or turn a
task validation outcome into a scientific claim.  Those operations remain
behind the real-backend and approved-scientific-policy gates.
"""

from __future__ import annotations

import math
import re
from enum import StrEnum

from pydantic import Field, field_validator, model_validator

from .models import (
    ArtifactRef,
    DFTTaskResultEnvelope,
    DFTTaskSpec,
    JobStatus,
    Sha256,
    StrictModel,
)


class ValidationStatus(StrEnum):
    VALID = "VALID"
    VALID_WITH_WARNINGS = "VALID_WITH_WARNINGS"
    INCOMPLETE = "INCOMPLETE"
    INVALID = "INVALID"
    NOT_APPLICABLE = "NOT_APPLICABLE"


class ValidationSeverity(StrEnum):
    ERROR = "ERROR"
    WARNING = "WARNING"
    INFO = "INFO"


class ValidationIssue(StrictModel):
    severity: ValidationSeverity
    code: str = Field(min_length=1)
    scope: str = Field(min_length=1)
    observed: str | float | int | bool | None = None
    expected: str | float | int | bool | None = None
    message: str = Field(min_length=1)
    remediation_class: str = Field(min_length=1)


class TaskOutputEvidence(StrictModel):
    """A named, immutable output reported by an independent bridge/parser."""

    output_name: str = Field(min_length=1)
    artifact: ArtifactRef

    @field_validator("output_name")
    @classmethod
    def safe_output_name(cls, value: str) -> str:
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", value):
            raise ValueError("output_name must be a simple logical file name")
        return value


class TaskExecutionEvidence(StrictModel):
    """Parsed execution facts; absence stays explicit rather than inferred."""

    task_id: str = Field(min_length=1)
    backend_status: JobStatus
    exit_code: int | None = None
    normal_termination: bool | None = None
    actual_task_input_hash: Sha256
    actual_method_hash: Sha256 | None = None
    actual_structure_sha256: Sha256 | None = None
    outputs: tuple[TaskOutputEvidence, ...] = ()
    output_complete: bool | None = None
    electronic_converged: bool | None = None
    ionic_converged: bool | None = None
    has_non_finite_values: bool | None = None
    maximum_force_ev_per_angstrom: float | None = Field(default=None, ge=0)
    parser_warnings: tuple[str, ...] = ()
    is_mock: bool = False

    @model_validator(mode="after")
    def evidence_guard(self) -> TaskExecutionEvidence:
        if self.is_mock:
            raise ValueError("real task validation cannot consume mock evidence")
        if len({output.output_name for output in self.outputs}) != len(self.outputs):
            raise ValueError("output evidence names must be unique")
        if self.maximum_force_ev_per_angstrom is not None and not math.isfinite(
            self.maximum_force_ev_per_angstrom
        ):
            raise ValueError("maximum force must be finite")
        return self


class DFTTaskValidationPolicy(StrictModel):
    """A narrow, frozen validator view of an already-approved method policy."""

    policy_ref: ArtifactRef
    policy_status: str
    approved_method_hash: Sha256
    required_output_names: tuple[str, ...] = ()
    require_electronic_convergence: bool = True
    require_ionic_convergence: bool = False
    require_normal_termination: bool = True
    maximum_force_ev_per_angstrom: float | None = Field(default=None, ge=0)
    allow_parser_warnings: bool = False

    @field_validator("policy_status")
    @classmethod
    def approved_status(cls, value: str) -> str:
        if value != "APPROVED":
            raise ValueError("real task validation requires an APPROVED policy")
        return value

    @field_validator("required_output_names")
    @classmethod
    def output_names_are_unique(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        if len(set(values)) != len(values):
            raise ValueError("required output names must be unique")
        for value in values:
            if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", value):
                raise ValueError("required output names must be simple logical file names")
        return values


class TaskValidationResult(StrictModel):
    task_id: str = Field(min_length=1)
    status: ValidationStatus
    policy_ref: ArtifactRef
    issues: tuple[ValidationIssue, ...] = ()


def validate_real_task(
    *,
    task: DFTTaskSpec,
    result: DFTTaskResultEnvelope,
    execution: TaskExecutionEvidence,
    policy: DFTTaskValidationPolicy,
) -> TaskValidationResult:
    """Validate a terminal non-mock task without trusting backend completion.

    The caller must keep the returned result separate from claim evaluation. A
    ``VALID`` task is necessary but not sufficient for scientific evidence.
    """

    if task.is_mock or result.is_mock:
        raise ValueError("real task validation cannot consume mock task results")

    issues: list[ValidationIssue] = []
    scope = task.task_id

    def issue(
        severity: ValidationSeverity,
        code: str,
        message: str,
        remediation_class: str,
        *,
        observed: str | float | bool | None = None,
        expected: str | float | bool | None = None,
    ) -> None:
        issues.append(
            ValidationIssue(
                severity=severity,
                code=code,
                scope=scope,
                observed=observed,
                expected=expected,
                message=message,
                remediation_class=remediation_class,
            )
        )

    if result.task_id != task.task_id:
        issue(ValidationSeverity.ERROR, "TASK_ID_MISMATCH", "result task ID differs from the approved task", "BACKEND_INCIDENT", observed=result.task_id, expected=task.task_id)
    if result.task_input_hash != task.task_input_hash:
        issue(ValidationSeverity.ERROR, "RESULT_INPUT_HASH_MISMATCH", "result input hash differs from the approved task", "BACKEND_INCIDENT", observed=result.task_input_hash, expected=task.task_input_hash)
    if execution.task_id != task.task_id:
        issue(ValidationSeverity.ERROR, "EXECUTION_TASK_ID_MISMATCH", "execution evidence belongs to another task", "BACKEND_INCIDENT", observed=execution.task_id, expected=task.task_id)
    if execution.backend_status != result.terminal_status:
        issue(ValidationSeverity.ERROR, "BACKEND_STATUS_CONFLICT", "execution evidence and result envelope disagree on terminal status", "BACKEND_INCIDENT", observed=execution.backend_status.value, expected=result.terminal_status.value)
    if result.terminal_status is not JobStatus.SUCCEEDED:
        issue(ValidationSeverity.ERROR, "TASK_NOT_SUCCEEDED", "a non-success terminal task cannot support scientific validation", "EXECUTION_RETRY_OR_REPLAN", observed=result.terminal_status.value, expected=JobStatus.SUCCEEDED.value)
    if execution.actual_task_input_hash != task.task_input_hash:
        issue(ValidationSeverity.ERROR, "UNAPPROVED_INPUT_HASH", "actual task input differs from the approved input", "NEW_APPROVAL_REQUIRED", observed=execution.actual_task_input_hash, expected=task.task_input_hash)
    if task.method_hash is None:
        issue(ValidationSeverity.ERROR, "MISSING_APPROVED_METHOD_HASH", "approved task has no method hash", "POLICY_CONFIGURATION_REQUIRED")
    elif task.method_hash != policy.approved_method_hash:
        issue(ValidationSeverity.ERROR, "TASK_POLICY_METHOD_MISMATCH", "task method hash differs from the approved validation policy", "NEW_APPROVAL_REQUIRED", observed=task.method_hash, expected=policy.approved_method_hash)
    if execution.actual_method_hash is None:
        issue(ValidationSeverity.ERROR, "MISSING_ACTUAL_METHOD_HASH", "execution evidence does not identify the rendered method", "BACKEND_INCIDENT")
    elif execution.actual_method_hash != policy.approved_method_hash:
        issue(ValidationSeverity.ERROR, "UNAPPROVED_METHOD_HASH", "actual method hash differs from the approved policy", "NEW_APPROVAL_REQUIRED", observed=execution.actual_method_hash, expected=policy.approved_method_hash)
    if execution.actual_structure_sha256 is None:
        issue(ValidationSeverity.ERROR, "MISSING_ACTUAL_STRUCTURE_HASH", "execution evidence does not identify the input structure", "BACKEND_INCIDENT")
    elif execution.actual_structure_sha256 != task.input_structure.sha256:
        issue(ValidationSeverity.ERROR, "UNAPPROVED_STRUCTURE_HASH", "actual structure differs from the approved task structure", "NEW_APPROVAL_REQUIRED", observed=execution.actual_structure_sha256, expected=task.input_structure.sha256)

    if execution.exit_code is None:
        issue(ValidationSeverity.ERROR, "MISSING_EXIT_CODE", "execution evidence has no process exit code", "BACKEND_INCIDENT")
    elif execution.exit_code != 0:
        issue(ValidationSeverity.ERROR, "NONZERO_EXIT_CODE", "underlying execution exited unsuccessfully", "EXECUTION_RETRY_OR_REPLAN", observed=execution.exit_code, expected=0)
    if policy.require_normal_termination:
        if execution.normal_termination is None:
            issue(ValidationSeverity.ERROR, "MISSING_NORMAL_TERMINATION", "normal termination was not established", "BACKEND_INCIDENT")
        elif not execution.normal_termination:
            issue(ValidationSeverity.ERROR, "ABNORMAL_TERMINATION", "execution did not report normal termination", "EXECUTION_RETRY_OR_REPLAN")

    output_names = {output.output_name for output in execution.outputs}
    for output_name in policy.required_output_names:
        if output_name not in output_names:
            issue(ValidationSeverity.ERROR, "REQUIRED_OUTPUT_MISSING", "a policy-required output is absent", "BACKEND_INCIDENT", observed=output_name)
    evidence_artifacts = {output.artifact for output in execution.outputs}
    for artifact in result.output_artifacts:
        if artifact not in evidence_artifacts:
            issue(ValidationSeverity.ERROR, "RESULT_ARTIFACT_UNACCOUNTED", "result artifact is absent from execution evidence", "BACKEND_INCIDENT", observed=artifact.uri)
    if execution.output_complete is None:
        issue(ValidationSeverity.ERROR, "OUTPUT_COMPLETENESS_UNKNOWN", "output completeness was not established", "BACKEND_INCIDENT")
    elif not execution.output_complete:
        issue(ValidationSeverity.ERROR, "OUTPUT_INCOMPLETE", "backend reported incomplete output", "BACKEND_INCIDENT")

    if policy.require_electronic_convergence:
        if execution.electronic_converged is None:
            issue(ValidationSeverity.ERROR, "ELECTRONIC_CONVERGENCE_UNKNOWN", "electronic convergence was not established", "SCIENTIFIC_VALIDATION_REQUIRED")
        elif not execution.electronic_converged:
            issue(ValidationSeverity.ERROR, "ELECTRONIC_NOT_CONVERGED", "electronic convergence did not meet the approved policy", "SCIENTIFIC_PARAMETER_CHANGE_REQUIRED")
    if policy.require_ionic_convergence:
        if execution.ionic_converged is None:
            issue(ValidationSeverity.ERROR, "IONIC_CONVERGENCE_UNKNOWN", "ionic convergence was not established", "SCIENTIFIC_VALIDATION_REQUIRED")
        elif not execution.ionic_converged:
            issue(ValidationSeverity.ERROR, "IONIC_NOT_CONVERGED", "ionic convergence did not meet the approved policy", "SCIENTIFIC_PARAMETER_CHANGE_REQUIRED")
    if execution.has_non_finite_values is None:
        issue(ValidationSeverity.ERROR, "FINITE_VALUE_CHECK_UNKNOWN", "non-finite numerical values were not checked", "SCIENTIFIC_VALIDATION_REQUIRED")
    elif execution.has_non_finite_values:
        issue(ValidationSeverity.ERROR, "NONFINITE_NUMERICAL_VALUE", "parsed numerical data contains NaN or infinity", "BACKEND_INCIDENT")
    if policy.maximum_force_ev_per_angstrom is not None:
        if execution.maximum_force_ev_per_angstrom is None:
            issue(ValidationSeverity.ERROR, "MAXIMUM_FORCE_UNKNOWN", "maximum force required by policy was not reported", "SCIENTIFIC_VALIDATION_REQUIRED")
        elif execution.maximum_force_ev_per_angstrom > policy.maximum_force_ev_per_angstrom:
            issue(ValidationSeverity.ERROR, "MAXIMUM_FORCE_EXCEEDED", "maximum force exceeds the approved policy threshold", "SCIENTIFIC_PARAMETER_CHANGE_REQUIRED", observed=execution.maximum_force_ev_per_angstrom, expected=policy.maximum_force_ev_per_angstrom)
    if execution.parser_warnings:
        issue(ValidationSeverity.WARNING, "PARSER_WARNINGS_PRESENT", "parser reported warnings that require disclosure", "EXPERT_REVIEW", observed=len(execution.parser_warnings))
        if not policy.allow_parser_warnings:
            issue(ValidationSeverity.ERROR, "PARSER_WARNINGS_NOT_ALLOWED", "approved policy does not allow parser warnings", "EXPERT_REVIEW")

    incomplete_codes = {
        "MISSING_EXIT_CODE",
        "MISSING_NORMAL_TERMINATION",
        "OUTPUT_COMPLETENESS_UNKNOWN",
        "ELECTRONIC_CONVERGENCE_UNKNOWN",
        "IONIC_CONVERGENCE_UNKNOWN",
        "FINITE_VALUE_CHECK_UNKNOWN",
        "MAXIMUM_FORCE_UNKNOWN",
    }
    error_codes = {
        item.code for item in issues if item.severity is ValidationSeverity.ERROR
    }
    if error_codes:
        status = (
            ValidationStatus.INCOMPLETE
            if error_codes.issubset(incomplete_codes)
            else ValidationStatus.INVALID
        )
    elif any(item.severity is ValidationSeverity.WARNING for item in issues):
        status = ValidationStatus.VALID_WITH_WARNINGS
    else:
        status = ValidationStatus.VALID
    return TaskValidationResult(
        task_id=task.task_id,
        status=status,
        policy_ref=policy.policy_ref,
        issues=tuple(issues),
    )
