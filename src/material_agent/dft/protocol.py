"""Backend boundaries shared by mock and future real Agent03 adapters."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from .models import (
    CancelResult,
    DFTRequest,
    DFTResultEnvelope,
    ExternalJobRef,
    JobStatus,
    ResourceEstimate,
)


@runtime_checkable
class DFTBackend(Protocol):
    """Minimal execution contract owned by Agent03.

    Implementations may use an in-process mock, an authenticated HTTP bridge,
    or another external execution service.  The Orchestrator only sees this
    contract and never imports backend-specific scientific dependencies.
    """

    backend_id: str
    backend_version: str
    adapter_version: str

    def validate_input(self, request: DFTRequest) -> bool: ...

    def estimate(self, request: DFTRequest) -> ResourceEstimate: ...

    def submit(
        self,
        request: DFTRequest,
        idempotency_key: str,
    ) -> ExternalJobRef: ...

    def status(self, job: ExternalJobRef) -> JobStatus: ...

    def cancel(self, job: ExternalJobRef) -> CancelResult: ...

    def fetch_result(self, job: ExternalJobRef) -> DFTResultEnvelope: ...


@runtime_checkable
class RestorableDFTBackend(Protocol):
    """Optional hook for in-memory test backends.

    Real external backends must recover from ``ExternalJobRef`` alone.  The
    deterministic mock needs this hook because its execution state lives only
    in memory and is reconstructed from the durable Agent03 operation ledger.
    """

    def restore_job(
        self,
        request: DFTRequest,
        ref: ExternalJobRef,
        *,
        polls: int,
        status: JobStatus,
    ) -> None: ...
