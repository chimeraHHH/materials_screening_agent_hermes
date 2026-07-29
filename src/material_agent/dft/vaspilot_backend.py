"""Agent03 backend adapter for an authenticated structured VASPilot bridge."""

from __future__ import annotations

from hashlib import sha256
from urllib.parse import quote

from pydantic import ValidationError

from .bridge_models import (
    BridgeArtifactManifest,
    BridgeBackendDescriptor,
    BridgeHealth,
    BridgeSubmitRequest,
    BridgeWorkflowRecord,
)
from .bridge_transport import (
    BridgeNotFoundError,
    BridgeProtocolError,
    BridgeTransport,
    BridgeTransportError,
)
from .models import (
    CancelResult,
    DFTRequest,
    DFTResultEnvelope,
    ExternalJobRef,
    JobStatus,
    ResourceEstimate,
    canonical_hash,
)


class VASPilotBackend:
    """Map the frozen Agent03 backend protocol to Bridge HTTP operations.

    The adapter is intentionally transport-only.  It does not import
    VASPilot, CrewAI, pymatgen, ASE, Slurm clients, or VASP parsers.
    """

    def __init__(
        self,
        *,
        transport: BridgeTransport,
        descriptor: BridgeBackendDescriptor,
    ) -> None:
        self.transport = transport
        self.descriptor = descriptor
        self.backend_id = descriptor.backend_id
        self.backend_version = descriptor.backend_version
        self.adapter_version = descriptor.adapter_version

    def health(self) -> BridgeHealth:
        payload = self.transport.request("GET", "/integration/v1/health")
        try:
            health = BridgeHealth.model_validate(payload)
        except ValidationError as exc:
            raise BridgeProtocolError("invalid bridge health response") from exc
        if health.descriptor != self.descriptor:
            raise BridgeProtocolError("bridge health descriptor changed")
        return health

    def capability(self) -> BridgeBackendDescriptor:
        payload = self.transport.request(
            "GET", "/integration/v1/capabilities"
        )
        try:
            descriptor = BridgeBackendDescriptor.model_validate(payload)
        except ValidationError as exc:
            raise BridgeProtocolError(
                "invalid bridge capability response"
            ) from exc
        if descriptor != self.descriptor:
            raise BridgeProtocolError("bridge capability descriptor changed")
        return descriptor

    def validate_input(self, request: DFTRequest) -> bool:
        if request.backend_id != self.backend_id:
            raise ValueError("DFTRequest backend_id does not match bridge")
        if request.is_mock != self.descriptor.is_mock:
            raise ValueError("DFTRequest mock flag does not match bridge")
        if any(
            task.task_type.value not in self.descriptor.supported_task_types
            for task in request.task_specs
        ):
            raise ValueError("DFTRequest contains an unsupported task type")
        return True

    def estimate(self, request: DFTRequest) -> ResourceEstimate:
        self.validate_input(request)
        return request.resource_estimate

    def submit(
        self,
        request: DFTRequest,
        idempotency_key: str,
    ) -> ExternalJobRef:
        self.validate_input(request)
        if not idempotency_key:
            raise ValueError("idempotency_key is required")
        submit = BridgeSubmitRequest(
            idempotency_key=idempotency_key,
            request_sha256=canonical_hash(request),
            request=request,
        )
        try:
            payload = self.transport.request(
                "POST",
                "/integration/v1/dft-workflows",
                submit.model_dump(mode="json"),
            )
        except BridgeTransportError as submit_error:
            try:
                payload = self.transport.request(
                    "GET",
                    self._by_key_path(idempotency_key),
                )
            except BridgeNotFoundError:
                raise submit_error
        record = self._record(payload)
        self._validate_submission(record, submit)
        return self._external_ref(record)

    def status(self, job: ExternalJobRef) -> JobStatus:
        record = self._record(
            self.transport.request(
                "GET", self._workflow_path(job.backend_task_id)
            )
        )
        self._validate_job(record, job)
        if record.status is JobStatus.UNKNOWN:
            raise BridgeProtocolError("bridge returned UNKNOWN workflow status")
        return record.status

    def cancel(self, job: ExternalJobRef) -> CancelResult:
        record = self._record(
            self.transport.request(
                "POST",
                self._workflow_path(job.backend_task_id) + "/cancel",
                {},
            )
        )
        self._validate_job(record, job)
        if record.status is JobStatus.CANCELLED:
            return CancelResult.CANCEL_CONFIRMED
        if record.status in {
            JobStatus.SUCCEEDED,
            JobStatus.FAILED,
            JobStatus.TIMEOUT,
        }:
            return CancelResult.ALREADY_TERMINAL
        if record.status is JobStatus.CANCEL_REQUESTED:
            return CancelResult.CANCEL_REQUESTED
        return CancelResult.CANCEL_FAILED

    def fetch_result(self, job: ExternalJobRef) -> DFTResultEnvelope:
        record = self._record(
            self.transport.request(
                "GET", self._workflow_path(job.backend_task_id)
            )
        )
        self._validate_job(record, job)
        if record.terminal_result is None:
            raise RuntimeError(
                "bridge result is available only for a terminal workflow"
            )
        manifest_payload = self.transport.request(
            "GET",
            self._workflow_path(job.backend_task_id) + "/artifacts",
        )
        try:
            manifest = BridgeArtifactManifest.model_validate(manifest_payload)
        except ValidationError as exc:
            raise BridgeProtocolError(
                "invalid bridge artifact manifest"
            ) from exc
        if (
            manifest.workflow_id != record.workflow_id
            or manifest.task_input_hash != job.task_input_hash
            or manifest.artifacts != record.artifact_manifest
        ):
            raise BridgeProtocolError(
                "bridge artifact manifest conflicts with workflow"
            )
        result = record.terminal_result
        return DFTResultEnvelope(
            request_id=record.request_id,
            external_job_ref=job,
            terminal_status=result.terminal_status,
            workflow_plan_hash=record.workflow_plan_hash,
            task_results=result.task_results,
            claim_results=result.claim_results,
            output_artifacts=result.output_artifacts,
            warnings=result.warnings,
            errors=result.errors,
            provenance={
                **result.provenance,
                "bridge_protocol": record.protocol_version,
                "bridge_request_sha256": record.request_sha256,
                "backend_id": self.backend_id,
                "backend_version": self.backend_version,
                "adapter_version": self.adapter_version,
            },
            is_mock=result.is_mock,
        )

    def _record(self, payload: dict) -> BridgeWorkflowRecord:
        try:
            record = BridgeWorkflowRecord.model_validate(payload)
        except ValidationError as exc:
            raise BridgeProtocolError(
                "invalid bridge workflow response"
            ) from exc
        if record.descriptor != self.descriptor:
            raise BridgeProtocolError("bridge workflow descriptor changed")
        return record

    def _validate_submission(
        self,
        record: BridgeWorkflowRecord,
        submit: BridgeSubmitRequest,
    ) -> None:
        request = submit.request
        if (
            record.idempotency_key != submit.idempotency_key
            or record.request_id != request.request_id
            or record.request_sha256 != submit.request_sha256
            or record.workflow_plan_hash != request.workflow_plan_hash
            or record.upstream_snapshot_hash
            != request.upstream_snapshot_hash
            or record.task_input_hash
            != request.task_specs[0].task_input_hash
            or record.is_mock != request.is_mock
        ):
            raise BridgeProtocolError(
                "bridge workflow conflicts with submitted request"
            )

    def _validate_job(
        self,
        record: BridgeWorkflowRecord,
        job: ExternalJobRef,
    ) -> None:
        if (
            job.backend_id != self.backend_id
            or job.backend_version != self.backend_version
            or job.adapter_version != self.adapter_version
            or record.workflow_id != job.backend_task_id
            or record.idempotency_key != job.idempotency_key
            or record.task_input_hash != job.task_input_hash
            or record.is_mock != job.is_mock
            or self._external_ref(record) != job
        ):
            raise BridgeProtocolError(
                "bridge workflow conflicts with external job reference"
            )

    def _external_ref(
        self,
        record: BridgeWorkflowRecord,
    ) -> ExternalJobRef:
        identity = (
            f"{self.backend_id}:{record.workflow_id}:"
            f"{record.idempotency_key}:{record.task_input_hash}"
        )
        token = sha256(identity.encode("utf-8")).hexdigest()[:24]
        return ExternalJobRef(
            external_job_ref_id=f"extjob_{token}",
            backend_id=self.backend_id,
            backend_version=self.backend_version,
            adapter_version=self.adapter_version,
            backend_task_id=record.workflow_id,
            idempotency_key=record.idempotency_key,
            task_input_hash=record.task_input_hash,
            is_mock=record.is_mock,
        )

    @staticmethod
    def _workflow_path(workflow_id: str) -> str:
        return (
            "/integration/v1/dft-workflows/"
            + quote(workflow_id, safe="")
        )

    @staticmethod
    def _by_key_path(idempotency_key: str) -> str:
        return (
            "/integration/v1/dft-workflows/by-idempotency-key/"
            + quote(idempotency_key, safe="")
        )
