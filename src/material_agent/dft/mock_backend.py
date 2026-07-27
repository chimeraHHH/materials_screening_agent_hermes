"""Deterministic, non-scientific backend used only for lifecycle contracts."""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256

from .models import (
    CancelResult, ClaimResult, ClaimStatus, DFTRequest, DFTResultEnvelope,
    DFTTaskResultEnvelope, ExternalJobRef, JobStatus,
)


SCENARIOS = frozenset({"mock_success", "mock_backend_failed", "mock_never_finishes", "mock_cancel_queued", "mock_cancel_running", "mock_timeout"})


@dataclass
class _Job:
    request: DFTRequest
    ref: ExternalJobRef
    scenario: str
    polls: int = 0
    status: JobStatus = JobStatus.CREATED


class MockDFTBackend:
    backend_id = "mock-dft"
    backend_version = "1.0.0"
    adapter_version = "agent03-mock-adapter-v1"

    def __init__(self, *, scenario: str = "mock_success") -> None:
        if scenario not in SCENARIOS:
            raise ValueError(f"unsupported mock scenario: {scenario}")
        self.scenario = scenario
        self._jobs: dict[str, _Job] = {}

    def validate_input(self, request: DFTRequest) -> bool:
        if not request.is_mock or request.backend_id != self.backend_id:
            raise ValueError("MockDFTBackend accepts MOCK requests for mock-dft only")
        return True

    def estimate(self, request: DFTRequest):
        self.validate_input(request)
        return request.resource_estimate

    def submit(self, request: DFTRequest, idempotency_key: str) -> ExternalJobRef:
        self.validate_input(request)
        if not idempotency_key:
            raise ValueError("idempotency_key is required")
        task_hash = request.task_specs[0].task_input_hash
        existing = self._jobs.get(idempotency_key)
        if existing:
            if existing.request.workflow_plan_hash != request.workflow_plan_hash:
                raise ValueError("idempotency key is already bound to a different request")
            return existing.ref
        token = sha256(f"{idempotency_key}:{request.workflow_plan_hash}".encode()).hexdigest()[:24]
        ref = ExternalJobRef(
            external_job_ref_id=f"extjob_{token}", backend_id=self.backend_id,
            backend_version=self.backend_version, adapter_version=self.adapter_version,
            backend_task_id=f"mocktask_{token}", idempotency_key=idempotency_key,
            task_input_hash=task_hash, is_mock=True,
        )
        self._jobs[idempotency_key] = _Job(request, ref, self.scenario)
        return ref

    def status(self, job: ExternalJobRef) -> JobStatus:
        record = self._find(job)
        record.polls += 1
        if record.status in {JobStatus.CANCELLED, JobStatus.SUCCEEDED, JobStatus.FAILED, JobStatus.TIMEOUT}:
            return record.status
        if record.scenario in {"mock_cancel_queued", "mock_cancel_running"}:
            record.status = JobStatus.QUEUED if record.polls == 1 else JobStatus.RUNNING
        elif record.scenario == "mock_never_finishes":
            record.status = JobStatus.RUNNING
        elif record.scenario == "mock_timeout":
            record.status = JobStatus.TIMEOUT if record.polls >= 2 else JobStatus.RUNNING
        elif record.scenario == "mock_backend_failed":
            record.status = JobStatus.FAILED if record.polls >= 2 else JobStatus.RUNNING
        else:
            record.status = JobStatus.QUEUED if record.polls == 1 else JobStatus.RUNNING if record.polls == 2 else JobStatus.SUCCEEDED
        return record.status

    def cancel(self, job: ExternalJobRef) -> CancelResult:
        record = self._find(job)
        if record.status in {JobStatus.SUCCEEDED, JobStatus.FAILED, JobStatus.TIMEOUT, JobStatus.CANCELLED}:
            return CancelResult.ALREADY_TERMINAL
        record.status = JobStatus.CANCELLED
        return CancelResult.CANCEL_CONFIRMED

    def fetch_result(self, job: ExternalJobRef) -> DFTResultEnvelope:
        record = self._find(job)
        if record.status not in {JobStatus.SUCCEEDED, JobStatus.FAILED, JobStatus.TIMEOUT, JobStatus.CANCELLED}:
            raise RuntimeError("result is available only for a terminal job")
        terminal = record.status
        message = "Mock lifecycle completed; no DFT calculation was executed." if terminal is JobStatus.SUCCEEDED else f"Mock lifecycle ended with {terminal.value}; no DFT calculation was executed."
        task_results = tuple(DFTTaskResultEnvelope(task_id=task.task_id, attempt_id=f"dftattempt_{record.ref.external_job_ref_id[7:]}", task_input_hash=task.task_input_hash, terminal_status=terminal, parsed_summary={"scientific_results": None, "message": message}, errors=() if terminal is JobStatus.SUCCEEDED else (message,), is_mock=True) for task in record.request.task_specs)
        claims = tuple(ClaimResult(claim_id=f"claim_{sha256(claim.claim_type.encode()).hexdigest()[:16]}", candidate_id=record.request.candidates[0].candidate_id, claim_type=claim.claim_type, status=ClaimStatus.NOT_EVALUATED_MOCK, evidence_level="L1_RETRIEVED", is_mock=True, limitations=("mock backend did not execute VASP and produced no scientific value",)) for claim in record.request.requested_claims)
        return DFTResultEnvelope(request_id=record.request.request_id, external_job_ref=record.ref, terminal_status=terminal, workflow_plan_hash=record.request.workflow_plan_hash, task_results=task_results, claim_results=claims, provenance={"backend": self.backend_id, "backend_version": self.backend_version, "scenario": record.scenario}, is_mock=True)

    def _find(self, job: ExternalJobRef) -> _Job:
        record = self._jobs.get(job.idempotency_key)
        if record is None or record.ref != job:
            raise KeyError("unknown or inconsistent external job reference")
        return record
