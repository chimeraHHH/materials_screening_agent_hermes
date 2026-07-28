"""Deterministic, non-scientific Agent04 backend for lifecycle contracts.

The backend is deliberately in-memory.  It owns the mock job state for the
duration of a test process, while the returned immutable references and status
history are sufficient for a future controller to persist and reconcile.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from hashlib import sha256
from types import MappingProxyType
from typing import Any, Mapping

from .models import (
    EvidenceLevel,
    EvidenceScope,
    ManyBodyRequest,
    ManyBodyResultEnvelope,
    MaterialLinkageStatus,
    ModelDefinitionStatus,
    ProvenanceRecord,
    SolverValidationStatus,
    canonical_hash,
    canonical_json,
)


class JobStatus(StrEnum):
    CREATED = "CREATED"
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    TIMEOUT = "TIMEOUT"
    CANCELLED = "CANCELLED"


class CancelResult(StrEnum):
    NOT_FOUND = "NOT_FOUND"
    CANCEL_CONFIRMED = "CANCEL_CONFIRMED"
    ALREADY_TERMINAL = "ALREADY_TERMINAL"


class BackendErrorCode(StrEnum):
    INVALID_INPUT = "INVALID_INPUT"
    IDEMPOTENCY_CONFLICT = "IDEMPOTENCY_CONFLICT"
    SUBMIT_RETRYABLE = "SUBMIT_RETRYABLE"
    SUBMIT_PERMANENT = "SUBMIT_PERMANENT"
    NOT_FOUND = "NOT_FOUND"
    REFERENCE_MISMATCH = "REFERENCE_MISMATCH"
    RESULT_NOT_READY = "RESULT_NOT_READY"
    STATUS_REGRESSION = "STATUS_REGRESSION"
    RESULT_HASH_MISMATCH = "RESULT_HASH_MISMATCH"
    MOCK_RESULT_INVALID = "MOCK_RESULT_INVALID"


class MockBackendError(RuntimeError):
    """Structured, deterministic backend error suitable for controller mapping."""

    def __init__(self, code: BackendErrorCode, message: str, *, retryable: bool = False) -> None:
        super().__init__(message)
        self.code = code
        self.retryable = retryable

    def as_dict(self) -> dict[str, Any]:
        return {"code": self.code.value, "retryable": self.retryable, "message": str(self)}


@dataclass(frozen=True)
class ExternalJobRef:
    external_job_ref_id: str
    backend_id: str
    backend_version: str
    adapter_version: str
    idempotency_key: str
    request_hash: str
    input_hash: str
    is_mock: bool = True


@dataclass(frozen=True)
class StatusObservation:
    sequence: int
    status: JobStatus


@dataclass(frozen=True)
class ManyBodyBackendResult:
    """Lifecycle result plus metadata; it intentionally contains no observables."""

    envelope: ManyBodyResultEnvelope
    external_job_ref: ExternalJobRef
    request_hash: str
    input_hash: str
    status_history: tuple[StatusObservation, ...]
    artifact_metadata: Mapping[str, Any]
    result_hash: str
    observables: tuple[Any, ...] = ()

    @property
    def is_mock(self) -> bool:
        return self.envelope.is_mock


SCENARIOS = frozenset(
    {
        "immediate_success",
        "queued_running_success",
        "retryable_submit_failure",
        "permanent_submit_failure",
        "numerical_failure",
        "timeout",
        "cancelled",
        "status_regression",
        "result_hash_mismatch",
        # Agent03-style names are accepted only as harmless fixture aliases.
        "mock_success",
        "mock_backend_failed",
        "mock_timeout",
        "mock_cancel_queued",
        "mock_cancel_running",
    }
)

_TERMINAL = frozenset({JobStatus.SUCCEEDED, JobStatus.FAILED, JobStatus.TIMEOUT, JobStatus.CANCELLED})
_ALIASES = {
    "mock_success": "queued_running_success",
    "mock_backend_failed": "numerical_failure",
    "mock_timeout": "timeout",
    "mock_cancel_queued": "cancelled",
    "mock_cancel_running": "cancelled",
}


@dataclass
class _Job:
    request: ManyBodyRequest
    ref: ExternalJobRef
    scenario: str
    polls: int = 0
    current: JobStatus = JobStatus.CREATED
    history: list[StatusObservation] | None = None

    def __post_init__(self) -> None:
        self.history = [StatusObservation(0, JobStatus.CREATED)]


class MockManyBodyBackend:
    """Offline backend that models control flow only, never many-body science."""

    backend_id = "mock-many-body"
    backend_version = "1.0.0"
    adapter_version = "agent04-mock-backend-v1"

    def __init__(self, *, scenario: str = "queued_running_success") -> None:
        if scenario not in SCENARIOS:
            raise ValueError(f"unsupported mock scenario: {scenario}")
        self.scenario = _ALIASES.get(scenario, scenario)
        self._jobs: dict[str, _Job] = {}
        self._submit_attempts: dict[str, int] = {}

    def validate_input(self, request: ManyBodyRequest) -> bool:
        if not request.is_mock:
            raise MockBackendError(BackendErrorCode.INVALID_INPUT, "mock backend requires is_mock=true")
        return True

    def estimate(self, request: ManyBodyRequest) -> Mapping[str, Any]:
        self.validate_input(request)
        return MappingProxyType({"is_mock": True, "resource_class": "CONTROL_ONLY"})

    def submit(self, request: ManyBodyRequest, idempotency_key: str) -> ExternalJobRef:
        self.validate_input(request)
        if not idempotency_key:
            raise MockBackendError(BackendErrorCode.INVALID_INPUT, "idempotency_key is required")
        request_hash = canonical_hash(request)
        input_hash = request.model_package.sha256
        existing = self._jobs.get(idempotency_key)
        if existing is not None:
            if existing.ref.request_hash != request_hash or existing.ref.input_hash != input_hash:
                raise MockBackendError(
                    BackendErrorCode.IDEMPOTENCY_CONFLICT,
                    "idempotency key is already bound to a different frozen input hash",
                )
            return existing.ref
        attempt = self._submit_attempts.get(idempotency_key, 0) + 1
        self._submit_attempts[idempotency_key] = attempt
        if self.scenario == "retryable_submit_failure" and attempt == 1:
            raise MockBackendError(BackendErrorCode.SUBMIT_RETRYABLE, "mock submit failed before acceptance", retryable=True)
        if self.scenario == "permanent_submit_failure":
            raise MockBackendError(BackendErrorCode.SUBMIT_PERMANENT, "mock submit rejected permanently")
        token = sha256(f"{idempotency_key}:{request_hash}:{input_hash}".encode()).hexdigest()[:24]
        ref = ExternalJobRef(
            external_job_ref_id=f"mbjob_{token}",
            backend_id=self.backend_id,
            backend_version=self.backend_version,
            adapter_version=self.adapter_version,
            idempotency_key=idempotency_key,
            request_hash=request_hash,
            input_hash=input_hash,
        )
        self._jobs[idempotency_key] = _Job(request, ref, self.scenario)
        return ref

    def restore_job(
        self,
        request: ManyBodyRequest,
        ref: ExternalJobRef,
        *,
        polls: int,
        status: JobStatus,
    ) -> None:
        """Rehydrate the backend view from the runner's durable operation ledger.

        The operation ledger remains owned by the runner.  This method only
        reconstructs the deterministic in-memory backend state after a new
        process starts; it never submits a second job.
        """
        self.validate_input(request)
        if ref.idempotency_key in self._jobs:
            existing = self._jobs[ref.idempotency_key]
            if existing.ref != ref or canonical_hash(existing.request) != canonical_hash(request):
                raise MockBackendError(
                    BackendErrorCode.REFERENCE_MISMATCH,
                    "restored mock job conflicts with the frozen request",
                )
            return
        if polls < 0:
            raise MockBackendError(BackendErrorCode.INVALID_INPUT, "invalid durable mock job state")
        job = _Job(request, ref, self.scenario, polls=polls, current=status)
        job.history = [StatusObservation(0, JobStatus.CREATED)]
        if status is not JobStatus.CREATED:
            job.history.append(StatusObservation(polls, status))
        self._jobs[ref.idempotency_key] = job

    def status(self, external_job_ref: ExternalJobRef) -> JobStatus:
        job = self._find(external_job_ref)
        if job.current in _TERMINAL:
            return job.current
        job.polls += 1
        if job.scenario == "immediate_success":
            next_status = JobStatus.SUCCEEDED
        elif job.scenario == "status_regression":
            next_status = (JobStatus.QUEUED, JobStatus.RUNNING, JobStatus.QUEUED)[min(job.polls - 1, 2)]
        elif job.scenario == "timeout":
            next_status = JobStatus.RUNNING if job.polls == 1 else JobStatus.TIMEOUT
        elif job.scenario == "numerical_failure":
            next_status = JobStatus.RUNNING if job.polls == 1 else JobStatus.FAILED
        else:
            next_status = (JobStatus.QUEUED, JobStatus.RUNNING, JobStatus.SUCCEEDED)[min(job.polls - 1, 2)]
        job.current = next_status
        job.history.append(StatusObservation(job.polls, next_status))
        return next_status

    def cancel(self, external_job_ref: ExternalJobRef) -> CancelResult:
        job = self._find(external_job_ref)
        if job.current in _TERMINAL:
            return CancelResult.ALREADY_TERMINAL
        job.current = JobStatus.CANCELLED
        job.history.append(StatusObservation(job.polls + 1, JobStatus.CANCELLED))
        return CancelResult.CANCEL_CONFIRMED

    def fetch_result(self, external_job_ref: ExternalJobRef) -> ManyBodyBackendResult:
        job = self._find(external_job_ref)
        if job.current is not JobStatus.SUCCEEDED:
            raise MockBackendError(BackendErrorCode.RESULT_NOT_READY, "result is available only after terminal success")
        result = self._build_result(job)
        self.validate_result(result)
        actual_hash = canonical_hash({key: value for key, value in result.artifact_metadata.items() if key != "content_sha256"})
        if result.result_hash != actual_hash:
            raise MockBackendError(BackendErrorCode.RESULT_HASH_MISMATCH, "mock result artifact hash does not match metadata")
        return result

    def validate_result(self, result: ManyBodyBackendResult) -> None:
        """Reject mock envelopes that attempt to smuggle science or evidence in."""
        if result.observables:
            raise MockBackendError(BackendErrorCode.MOCK_RESULT_INVALID, "mock results must contain empty observables")
        if result.envelope.evidence_level in (EvidenceLevel.L4_MANY_BODY_VALIDATED, EvidenceLevel.L5_EXPERT_REVIEWED):
            raise MockBackendError(BackendErrorCode.MOCK_RESULT_INVALID, "mock results cannot claim L4/L5 evidence")
        if result.envelope.solver_validation_status is not SolverValidationStatus.MOCK_ONLY:
            raise MockBackendError(BackendErrorCode.MOCK_RESULT_INVALID, "mock results must use MOCK_ONLY solver status")

    def status_history(self, external_job_ref: ExternalJobRef) -> tuple[StatusObservation, ...]:
        return tuple(self._find(external_job_ref).history or ())

    def assert_monotonic_history(self, external_job_ref: ExternalJobRef) -> None:
        history = self.status_history(external_job_ref)
        rank = {JobStatus.CREATED: 0, JobStatus.QUEUED: 1, JobStatus.RUNNING: 2, JobStatus.SUCCEEDED: 3, JobStatus.FAILED: 3, JobStatus.TIMEOUT: 3, JobStatus.CANCELLED: 3}
        if any(rank[current.status] < rank[previous.status] for previous, current in zip(history, history[1:])):
            raise MockBackendError(BackendErrorCode.STATUS_REGRESSION, "backend status history regressed")

    def _build_result(self, job: _Job) -> ManyBodyBackendResult:
        request = job.request
        warning = "Mock lifecycle only; no many-body scientific conclusion was produced."
        provenance = (ProvenanceRecord(provenance_id="mock-backend", source_type="FIXTURE", method="deterministic-lifecycle"),)
        envelope = ManyBodyResultEnvelope(
            request_id=request.request_id,
            model_snapshot=request.model_package,
            backend_id=self.backend_id,
            backend_version=self.backend_version,
            is_mock=True,
            fixture=True,
            execution_status=JobStatus.SUCCEEDED.value,
            model_definition_status=ModelDefinitionStatus.VALIDATED_MODEL,
            solver_validation_status=SolverValidationStatus.MOCK_ONLY,
            material_linkage_status=MaterialLinkageStatus.NONE,
            evidence_scope=EvidenceScope.SOLVER_BENCHMARK,
            evidence_level=EvidenceLevel.L1_RETRIEVED,
            provenance=provenance,
            warnings=(warning,),
        )
        external_job_ref_hash = canonical_hash(request)
        metadata = {
            "schema_version": "agent04-mock-result-metadata-v1",
            "is_mock": True,
            "backend_id": self.backend_id,
            "backend_version": self.backend_version,
            "scenario": job.scenario,
            "request_hash": external_job_ref_hash,
            "input_hash": request.model_package.sha256,
            "external_job_ref": job.ref.external_job_ref_id,
            "artifact_uri": f"artifact://many-body/mock-results/{job.ref.external_job_ref_id}.json",
            "status_history": [{"sequence": item.sequence, "status": item.status.value} for item in (job.history or ())],
            "observables": [],
            "warning": warning,
        }
        result_hash = sha256(canonical_json(metadata).encode()).hexdigest()
        if job.scenario == "result_hash_mismatch":
            result_hash = "0" * 64
        metadata["content_sha256"] = result_hash
        return ManyBodyBackendResult(
            envelope=envelope,
            external_job_ref=job.ref,
            request_hash=external_job_ref_hash,
            input_hash=request.model_package.sha256,
            status_history=tuple(job.history or ()),
            artifact_metadata=MappingProxyType(metadata),
            result_hash=result_hash,
            observables=(),
        )

    def _find(self, external_job_ref: ExternalJobRef) -> _Job:
        job = self._jobs.get(external_job_ref.idempotency_key)
        if job is None:
            raise MockBackendError(BackendErrorCode.NOT_FOUND, "external job reference was not found")
        if job.ref != external_job_ref:
            raise MockBackendError(BackendErrorCode.REFERENCE_MISMATCH, "external job reference does not match the frozen request")
        return job
