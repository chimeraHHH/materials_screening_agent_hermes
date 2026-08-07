"""Thread-safe in-memory Gateway dependencies for tests and local wiring."""

from __future__ import annotations

from threading import RLock

from material_agent.gateway.errors import ConcurrentUpdateError, ResultIntegrityError
from material_agent.gateway.models import (
    GatewayResultRecordV1,
    GatewayRunRecordV1,
    gateway_result_sha256,
    terminal_reference,
    validate_artifact_uri,
)


class InMemoryGatewayRepository:
    """Small optimistic repository implementing ``GatewayRepository``."""

    def __init__(self) -> None:
        self._runs: dict[str, GatewayRunRecordV1] = {}
        self._submission_index: dict[str, str] = {}
        self._results: dict[str, GatewayResultRecordV1] = {}
        self._lock = RLock()

    def create_or_get(
        self, record: GatewayRunRecordV1
    ) -> tuple[GatewayRunRecordV1, bool]:
        with self._lock:
            submission_id = record.request.submission_id
            existing_run_id = self._submission_index.get(submission_id)
            if existing_run_id is not None:
                return self._runs[existing_run_id], False
            existing = self._runs.get(record.run_id)
            if existing is not None:
                return existing, False
            self._runs[record.run_id] = record
            self._submission_index[submission_id] = record.run_id
            return record, True

    def get_run(self, run_id: str) -> GatewayRunRecordV1 | None:
        with self._lock:
            return self._runs.get(run_id)

    def replace_run(
        self,
        record: GatewayRunRecordV1,
        *,
        expected_revision: int,
        result: GatewayResultRecordV1 | None,
    ) -> GatewayRunRecordV1:
        with self._lock:
            current = self._runs.get(record.run_id)
            if current is None:
                raise ConcurrentUpdateError("run disappeared before update")
            if current.revision != expected_revision:
                raise ConcurrentUpdateError("run revision changed before update")
            if record.revision != expected_revision + 1:
                raise ConcurrentUpdateError("replacement revision must advance once")
            if (
                current.request != record.request
                or current.request_sha256 != record.request_sha256
            ):
                raise ConcurrentUpdateError("immutable request identity changed")
            if result is not None and result.run_id != record.run_id:
                raise ConcurrentUpdateError("result belongs to a different run")
            reference = terminal_reference(record.state)
            if (result is None) != (reference is None):
                raise ConcurrentUpdateError(
                    "terminal run and result must be persisted together"
                )
            if result is not None and reference != (
                result.report_uri,
                result.authoritative_sha256,
                gateway_result_sha256(result),
            ):
                raise ConcurrentUpdateError(
                    "terminal state does not bind the canonical result"
                )
            existing_result = self._results.get(record.run_id)
            if existing_result is not None and result != existing_result:
                raise ConcurrentUpdateError("immutable terminal result already differs")
            self._runs[record.run_id] = record
            if result is not None:
                self._results[record.run_id] = result
            return record

    def get_result(self, run_id: str) -> GatewayResultRecordV1 | None:
        with self._lock:
            result = self._results.get(run_id)
            if result is None:
                return None
            record = self._runs.get(run_id)
            reference = None if record is None else terminal_reference(record.state)
            if reference != (
                result.report_uri,
                result.authoritative_sha256,
                gateway_result_sha256(result),
            ):
                raise ResultIntegrityError(
                    "persisted terminal result does not match its run binding"
                )
            return result


class InMemoryArtifactStore:
    """Exact-URI byte store implementing the read-only artifact boundary."""

    def __init__(self) -> None:
        self._payloads: dict[str, bytes] = {}
        self._lock = RLock()

    def put_bytes(self, uri: str, payload: bytes) -> None:
        validate_artifact_uri(uri)
        with self._lock:
            if uri in self._payloads and self._payloads[uri] != bytes(payload):
                raise ValueError("immutable artifact URI already contains other bytes")
            self._payloads[uri] = bytes(payload)

    def read_bytes(self, uri: str) -> bytes:
        validate_artifact_uri(uri)
        with self._lock:
            if uri not in self._payloads:
                raise KeyError(uri)
            return bytes(self._payloads[uri])
