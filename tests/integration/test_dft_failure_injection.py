from __future__ import annotations

import json

from material_agent.dft.mock_backend import MockDFTBackend
from material_agent.dft.models import JobStatus
from tests.unit.test_dft_runner import _setup


class SubmitBeforeFailureBackend(MockDFTBackend):
    def submit(self, request, idempotency_key):
        raise ConnectionError("submit failed before backend accepted request")


class SubmitResponseLostBackend(MockDFTBackend):
    def __init__(self):
        super().__init__()
        self.submit_calls = 0
        self._lose_response = True

    def submit(self, request, idempotency_key):
        self.submit_calls += 1
        ref = super().submit(request, idempotency_key)
        if self._lose_response:
            self._lose_response = False
            raise ConnectionError("submit response was lost after acceptance")
        return ref


def _runner_with_backend(tmp_path, backend):
    store, runner, context = _setup(tmp_path)
    runner.backend = backend
    return store, runner, context


def test_submit_failure_before_acceptance_is_retryable_without_ledger_or_job(tmp_path):
    _store, runner, context = _runner_with_backend(
        tmp_path, SubmitBeforeFailureBackend()
    )
    prepared = runner.prepare(context)

    outcome = runner.start(context, prepared, "failure-before-submit")

    assert outcome.status.value == "RETRYABLE_FAILED"
    assert outcome.errors[0].category == "TRANSIENT_EXTERNAL"
    assert not runner.backend._jobs
    assert not runner.store.exists("artifact://stages/agent03/operations/failure-before-submit.json")


def test_submit_response_loss_retries_same_idempotent_mock_job(tmp_path):
    backend = SubmitResponseLostBackend()
    store, runner, context = _runner_with_backend(tmp_path, backend)
    prepared = runner.prepare(context)

    lost = runner.start(context, prepared, "response-lost")
    recovered = runner.start(context, prepared, "response-lost")

    assert lost.status.value == "RETRYABLE_FAILED"
    assert lost.errors[0].category == "TRANSIENT_EXTERNAL"
    assert recovered.status.value == "RUNNING"
    assert recovered.external_job_ref
    assert backend.submit_calls == 2
    assert len(backend._jobs) == 1
    operation = store.read_json("artifact://stages/agent03/operations/response-lost.json")
    assert operation["external_job_ref"]["external_job_ref_id"] == recovered.external_job_ref


def test_transient_status_and_fetch_errors_retry_without_resubmission(tmp_path):
    store, runner, context = _setup(tmp_path)
    prepared = runner.prepare(context)
    waiting = runner.start(context, prepared, "transient-retry")
    original_status = runner.backend.status
    status_calls = 0

    def status_once(ref):
        nonlocal status_calls
        status_calls += 1
        if status_calls == 1:
            raise TimeoutError("temporary status timeout")
        return original_status(ref)

    runner.backend.status = status_once
    first = runner.reconcile(context, prepared, waiting.external_job_ref, waiting.idempotency_key)
    second = runner.reconcile(context, prepared, waiting.external_job_ref, waiting.idempotency_key)
    assert first.status.value == "RETRYABLE_FAILED"
    assert first.errors[0].category == "TRANSIENT_EXTERNAL"
    assert second.status.value == "RUNNING"

    runner.reconcile(context, prepared, waiting.external_job_ref, waiting.idempotency_key)
    original_fetch = runner.backend.fetch_result
    fetch_calls = 0

    def fetch_once(ref):
        nonlocal fetch_calls
        fetch_calls += 1
        if fetch_calls == 1:
            raise ConnectionError("temporary fetch failure")
        return original_fetch(ref)

    runner.backend.fetch_result = fetch_once
    transient = runner.reconcile(context, prepared, waiting.external_job_ref, waiting.idempotency_key)
    completed = runner.reconcile(context, prepared, waiting.external_job_ref, waiting.idempotency_key)
    assert transient.errors[0].category == "TRANSIENT_EXTERNAL"
    assert completed.status.value == "SUCCEEDED"
    assert len(runner.backend._jobs) == 1
    assert json.loads(store.read_bytes(completed.native_result_uri).decode())["is_mock"] is True


def test_unknown_and_regressing_status_fail_closed(tmp_path):
    _store, runner, context = _setup(tmp_path)
    prepared = runner.prepare(context)
    waiting = runner.start(context, prepared, "bad-status")
    runner.backend.status = lambda _ref: JobStatus.UNKNOWN
    unknown = runner.reconcile(context, prepared, waiting.external_job_ref, waiting.idempotency_key)
    assert unknown.status.value == "PERMANENT_FAILED"
    assert unknown.errors[0].category == "BACKEND_INCONSISTENT"

    store2, runner2, context2 = _setup(tmp_path / "regression")
    prepared2 = runner2.prepare(context2)
    waiting2 = runner2.start(context2, prepared2, "regression")
    first = runner2.reconcile(context2, prepared2, waiting2.external_job_ref, waiting2.idempotency_key)
    assert first.status.value == "RUNNING"
    runner2.backend.status = lambda _ref: JobStatus.CREATED
    regressed = runner2.reconcile(context2, prepared2, waiting2.external_job_ref, waiting2.idempotency_key)
    assert regressed.errors[0].category == "BACKEND_INCONSISTENT"
    assert not store2.exists("artifact://stages/agent03/run-dft/attempt-1/operations/regression.status-2.json")


def test_never_finishes_is_bounded_by_caller_without_sleep_or_new_job(tmp_path):
    store, runner, context = _setup(tmp_path, "mock_never_finishes")
    prepared = runner.prepare(context)
    waiting = runner.start(context, prepared, "never-finishes")

    observations = [
        runner.reconcile(context, prepared, waiting.external_job_ref, waiting.idempotency_key)
        for _ in range(3)
    ]

    assert all(item.outcome.value == "WaitingExternal" for item in observations)
    assert all(item.external_status.value == "RUNNING" for item in observations)
    assert len(runner.backend._jobs) == 1
    assert store.exists("artifact://stages/agent03/run-dft/attempt-1/operations/never-finishes.status-3.json")
