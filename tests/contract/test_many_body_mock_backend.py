from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from material_agent.many_body import (
    BackendErrorCode,
    CancelResult,
    EvidenceLevel,
    JobStatus,
    ManyBodyRequest,
    MockBackendError,
    MockManyBodyBackend,
    SolverValidationStatus,
    package_content_hash,
)
from material_agent.many_body.models import EffectiveModelPackage

ROOT = Path(__file__).parents[1] / "fixtures/contracts/agent04-v1"


def request() -> ManyBodyRequest:
    package = EffectiveModelPackage.model_validate(json.loads((ROOT / "one-dimensional-hubbard.json").read_text()))
    return ManyBodyRequest(
        request_id="mock-request",
        model_id=package.model_id,
        model_revision=package.revision,
        model_package={"uri": "artifact://models/one-dimensional-hubbard.json", "sha256": package_content_hash(package)},
        state_point_ids=(package.state_points[0].state_point_id,),
        requested_observables=(),
        requested_claims=("workflow_lifecycle",),
        approval_policy_version="many-body-approval/v1",
        routing_policy_version="many-body-routing/v1",
        is_mock=True,
    )


def test_submit_is_deterministic_and_conflicts_on_frozen_input_hash() -> None:
    backend = MockManyBodyBackend(scenario="immediate_success")
    first = backend.submit(request(), "key-1")
    assert backend.submit(request(), "key-1") == first
    changed = request().model_copy(update={"request_id": "different-request"})
    with pytest.raises(MockBackendError) as error:
        backend.submit(changed, "key-1")
    assert error.value.code is BackendErrorCode.IDEMPOTENCY_CONFLICT


def test_queued_running_success_and_result_safety() -> None:
    backend = MockManyBodyBackend()
    ref = backend.submit(request(), "success")
    assert [backend.status(ref), backend.status(ref), backend.status(ref)] == [JobStatus.QUEUED, JobStatus.RUNNING, JobStatus.SUCCEEDED]
    result = backend.fetch_result(ref)
    assert result.is_mock is True
    assert result.observables == ()
    assert result.envelope.solver_validation_status.value == "MOCK_ONLY"
    assert result.envelope.evidence_level.value != "L4_MANY_BODY_VALIDATED"
    assert result.artifact_metadata["observables"] == []
    assert result.artifact_metadata["request_hash"] == ref.request_hash
    assert result.status_history[-1].status is JobStatus.SUCCEEDED


@pytest.mark.parametrize(
    ("scenario", "expected"),
    [("numerical_failure", JobStatus.FAILED), ("timeout", JobStatus.TIMEOUT)],
)
def test_failure_and_timeout_are_terminal_and_fetch_is_rejected(scenario: str, expected: JobStatus) -> None:
    backend = MockManyBodyBackend(scenario=scenario)
    ref = backend.submit(request(), scenario)
    backend.status(ref)
    assert backend.status(ref) is expected
    with pytest.raises(MockBackendError) as error:
        backend.fetch_result(ref)
    assert error.value.code is BackendErrorCode.RESULT_NOT_READY


def test_retryable_and_permanent_submit_failures_are_structured() -> None:
    retry = MockManyBodyBackend(scenario="retryable_submit_failure")
    with pytest.raises(MockBackendError) as error:
        retry.submit(request(), "retry")
    assert error.value.code is BackendErrorCode.SUBMIT_RETRYABLE
    assert error.value.retryable is True
    assert retry.submit(request(), "retry").external_job_ref_id.startswith("mbjob_")
    with pytest.raises(MockBackendError) as error:
        MockManyBodyBackend(scenario="permanent_submit_failure").submit(request(), "permanent")
    assert error.value.code is BackendErrorCode.SUBMIT_PERMANENT


def test_cancel_is_idempotent_for_nonterminal_and_terminal_jobs() -> None:
    backend = MockManyBodyBackend(scenario="cancelled")
    ref = backend.submit(request(), "cancel")
    assert backend.cancel(ref) is CancelResult.CANCEL_CONFIRMED
    assert backend.cancel(ref) is CancelResult.ALREADY_TERMINAL
    assert backend.status(ref) is JobStatus.CANCELLED
    with pytest.raises(MockBackendError, match="only after terminal success"):
        backend.fetch_result(ref)

    terminal = MockManyBodyBackend(scenario="immediate_success")
    terminal_ref = terminal.submit(request(), "terminal")
    terminal.status(terminal_ref)
    assert terminal.cancel(terminal_ref) is CancelResult.ALREADY_TERMINAL


def test_submit_response_loss_can_be_recovered_by_idempotent_resubmit() -> None:
    backend = MockManyBodyBackend()
    expected = backend.submit(request(), "lost-response")
    recovered = backend.submit(request(), "lost-response")
    assert recovered == expected
    assert len(backend._jobs) == 1


def test_status_regression_is_recorded_and_can_fail_closed() -> None:
    backend = MockManyBodyBackend(scenario="status_regression")
    ref = backend.submit(request(), "regression")
    backend.status(ref)
    backend.status(ref)
    assert backend.status(ref) is JobStatus.QUEUED
    with pytest.raises(MockBackendError) as error:
        backend.assert_monotonic_history(ref)
    assert error.value.code is BackendErrorCode.STATUS_REGRESSION


def test_result_hash_mismatch_and_unknown_reference_are_explicit() -> None:
    backend = MockManyBodyBackend(scenario="result_hash_mismatch")
    ref = backend.submit(request(), "bad-hash")
    assert backend.status(ref) is JobStatus.QUEUED
    assert backend.status(ref) is JobStatus.RUNNING
    assert backend.status(ref) is JobStatus.SUCCEEDED
    with pytest.raises(MockBackendError) as error:
        backend.fetch_result(ref)
    assert error.value.code is BackendErrorCode.RESULT_HASH_MISMATCH
    unknown = ref.__class__(**{**ref.__dict__, "external_job_ref_id": "mbjob_missing"})
    with pytest.raises(MockBackendError) as error:
        backend.status(unknown)
    assert error.value.code is BackendErrorCode.REFERENCE_MISMATCH


def test_mock_result_rejects_scientific_observables_and_l4_by_construction() -> None:
    backend = MockManyBodyBackend(scenario="immediate_success")
    ref = backend.submit(request(), "guards")
    assert backend.status(ref) is JobStatus.SUCCEEDED
    result = backend.fetch_result(ref)
    assert result.observables == ()
    assert result.envelope.solver_validation_status.value == "MOCK_ONLY"
    assert result.envelope.evidence_level.value == "L1_RETRIEVED"
    with pytest.raises(MockBackendError, match="empty observables"):
        backend.validate_result(replace(result, observables=("forbidden",)))
    with pytest.raises(MockBackendError, match="L4/L5"):
        backend.validate_result(
            SimpleNamespace(
                observables=(),
                envelope=SimpleNamespace(
                    evidence_level=EvidenceLevel.L4_MANY_BODY_VALIDATED,
                    solver_validation_status=SolverValidationStatus.MOCK_ONLY,
                ),
            )
        )
