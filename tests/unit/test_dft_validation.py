from __future__ import annotations

from material_agent.dft.models import (
    ArtifactRef,
    DFTTaskResultEnvelope,
    DFTTaskSpec,
    JobStatus,
    ResourceEstimate,
    TaskType,
)
from material_agent.dft.validation import (
    DFTTaskValidationPolicy,
    TaskExecutionEvidence,
    TaskOutputEvidence,
    ValidationStatus,
    validate_real_task,
)


def _task() -> DFTTaskSpec:
    return DFTTaskSpec(
        task_id="dfttask_scf", task_type=TaskType.STATIC_SCF,
        candidate_id="cand_1", input_structure=ArtifactRef(
            uri="artifact://structures/input.cif", sha256="a" * 64
        ), method_spec=ArtifactRef(
            uri="artifact://policies/method.json", sha256="b" * 64
        ), method_hash="b" * 64,
        resource_spec=ResourceEstimate(
            estimate_mode="POLICY_RULE_BASED", resource_class="SMALL",
            is_mock=False,
        ), task_input_hash="c" * 64, is_mock=False,
    )


def _policy() -> DFTTaskValidationPolicy:
    return DFTTaskValidationPolicy(
        policy_ref=ArtifactRef(
            uri="artifact://policies/approved-validation.json", sha256="d" * 64
        ), policy_status="APPROVED", approved_method_hash="b" * 64,
        required_output_names=("result.json",),
    )


def _result() -> DFTTaskResultEnvelope:
    return DFTTaskResultEnvelope(
        task_id="dfttask_scf", attempt_id="attempt_1", task_input_hash="c" * 64,
        terminal_status=JobStatus.SUCCEEDED,
        output_artifacts=(ArtifactRef(
            uri="artifact://runs/result.json", sha256="e" * 64
        ),), is_mock=False,
    )


def _execution(*, electronic_converged: bool) -> TaskExecutionEvidence:
    return TaskExecutionEvidence(
        task_id="dfttask_scf", backend_status=JobStatus.SUCCEEDED,
        exit_code=0, normal_termination=True, actual_task_input_hash="c" * 64,
        actual_method_hash="b" * 64, actual_structure_sha256="a" * 64,
        outputs=(TaskOutputEvidence(
            output_name="result.json",
            artifact=ArtifactRef(uri="artifact://runs/result.json", sha256="e" * 64),
        ),), output_complete=True, electronic_converged=electronic_converged,
        has_non_finite_values=False,
    )


def test_backend_success_does_not_override_failed_electronic_convergence() -> None:
    outcome = validate_real_task(
        task=_task(), result=_result(), execution=_execution(electronic_converged=False),
        policy=_policy(),
    )
    assert outcome.status is ValidationStatus.INVALID
    assert {issue.code for issue in outcome.issues} == {"ELECTRONIC_NOT_CONVERGED"}


def test_valid_task_requires_full_execution_and_artifact_provenance() -> None:
    outcome = validate_real_task(
        task=_task(), result=_result(), execution=_execution(electronic_converged=True),
        policy=_policy(),
    )
    assert outcome.status is ValidationStatus.VALID
    assert outcome.issues == ()


def test_missing_convergence_fact_is_incomplete_and_fail_closed() -> None:
    execution = _execution(electronic_converged=True).model_copy(
        update={"electronic_converged": None}
    )
    outcome = validate_real_task(
        task=_task(), result=_result(), execution=execution, policy=_policy()
    )
    assert outcome.status is ValidationStatus.INCOMPLETE
    assert {issue.code for issue in outcome.issues} == {
        "ELECTRONIC_CONVERGENCE_UNKNOWN"
    }
