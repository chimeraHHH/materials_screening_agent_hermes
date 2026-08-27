"""Deterministic test-only transport for the VASPilot Bridge contract."""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from typing import Any
from urllib.parse import unquote

from .bridge_models import (
    BridgeArtifactManifest,
    BridgeBackendDescriptor,
    BridgeChildJob,
    BridgeHealth,
    BridgeSubmitRequest,
    BridgeTerminalResult,
    BridgeWorkflowRecord,
)
from .bridge_transport import (
    BridgeConflictError,
    BridgeNotFoundError,
    BridgeProtocolError,
    BridgeTransportError,
)
from .models import (
    ArtifactRef,
    ClaimResult,
    ClaimStatus,
    DFTTaskResultEnvelope,
    JobStatus,
)


@dataclass
class _FakeWorkflow:
    submit: BridgeSubmitRequest
    workflow_id: str
    polls: int = 0
    status: JobStatus = JobStatus.CREATED


class FakeVASPilotBridgeTransport:
    """Exercise the bridge lifecycle without claiming a real DFT execution.

    This transport accepts mock requests only and is never registered by the
    production runner registry.
    """

    def __init__(
        self,
        *,
        descriptor: BridgeBackendDescriptor,
        lose_first_submit_response: bool = False,
    ) -> None:
        if not descriptor.is_mock:
            raise ValueError("Fake Bridge descriptor must set is_mock=true")
        self.descriptor = descriptor
        self.lose_first_submit_response = lose_first_submit_response
        self._response_lost = False
        self._by_key: dict[str, _FakeWorkflow] = {}
        self._by_id: dict[str, _FakeWorkflow] = {}
        self.submit_calls = 0

    @property
    def workflow_count(self) -> int:
        return len(self._by_id)

    def request(
        self,
        method: str,
        path: str,
        body: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if method == "GET" and path == "/integration/v1/health":
            return BridgeHealth(
                descriptor=self.descriptor,
                status="READY",
                detail="deterministic test-only bridge",
            ).model_dump(mode="json")
        if (
            method == "GET"
            and path == "/integration/v1/capabilities"
        ):
            return self.descriptor.model_dump(mode="json")
        if (
            method == "POST"
            and path == "/integration/v1/dft-workflows"
        ):
            return self._submit(body)
        by_key_prefix = (
            "/integration/v1/dft-workflows/by-idempotency-key/"
        )
        if method == "GET" and path.startswith(by_key_prefix):
            key = unquote(path.removeprefix(by_key_prefix))
            workflow = self._by_key.get(key)
            if workflow is None:
                raise BridgeNotFoundError("fake bridge key was not found")
            return self._record(workflow).model_dump(mode="json")
        workflow_prefix = "/integration/v1/dft-workflows/"
        if path.startswith(workflow_prefix):
            suffix = path.removeprefix(workflow_prefix)
            if suffix.endswith("/cancel"):
                if method != "POST":
                    raise BridgeProtocolError("fake cancel requires POST")
                workflow_id = unquote(suffix.removesuffix("/cancel"))
                workflow = self._workflow(workflow_id)
                if workflow.status not in {
                    JobStatus.SUCCEEDED,
                    JobStatus.FAILED,
                    JobStatus.TIMEOUT,
                    JobStatus.CANCELLED,
                }:
                    workflow.status = JobStatus.CANCELLED
                return self._record(workflow).model_dump(mode="json")
            if suffix.endswith("/artifacts"):
                if method != "GET":
                    raise BridgeProtocolError(
                        "fake artifact lookup requires GET"
                    )
                workflow_id = unquote(suffix.removesuffix("/artifacts"))
                workflow = self._workflow(workflow_id)
                record = self._record(workflow)
                return BridgeArtifactManifest(
                    workflow_id=workflow_id,
                    task_input_hash=record.task_input_hash,
                    artifacts=record.artifact_manifest,
                ).model_dump(mode="json")
            if method == "GET":
                workflow = self._workflow(unquote(suffix))
                self._advance(workflow)
                return self._record(workflow).model_dump(mode="json")
        raise BridgeNotFoundError("fake bridge route was not found")

    def _submit(
        self,
        body: dict[str, Any] | None,
    ) -> dict[str, Any]:
        self.submit_calls += 1
        if body is None:
            raise BridgeProtocolError("fake bridge submit body is required")
        try:
            submit = BridgeSubmitRequest.model_validate(body)
        except Exception as exc:
            raise BridgeProtocolError(
                "fake bridge received an invalid submit request"
            ) from exc
        request = submit.request
        if (
            not request.is_mock
            or request.backend_id != self.descriptor.backend_id
        ):
            raise BridgeProtocolError(
                "fake bridge accepts matching mock requests only"
            )
        existing = self._by_key.get(submit.idempotency_key)
        if existing is not None:
            if existing.submit.request_sha256 != submit.request_sha256:
                raise BridgeConflictError(
                    "idempotency key is bound to another request"
                )
            workflow = existing
        else:
            identity = (
                f"{submit.idempotency_key}:{submit.request_sha256}"
            )
            token = sha256(identity.encode("utf-8")).hexdigest()[:24]
            workflow = _FakeWorkflow(
                submit=submit,
                workflow_id=f"fakewf_{token}",
            )
            self._by_key[submit.idempotency_key] = workflow
            self._by_id[workflow.workflow_id] = workflow
        if (
            self.lose_first_submit_response
            and not self._response_lost
        ):
            self._response_lost = True
            raise BridgeTransportError(
                "fake bridge lost the submit response after acceptance"
            )
        return self._record(workflow).model_dump(mode="json")

    def _workflow(self, workflow_id: str) -> _FakeWorkflow:
        workflow = self._by_id.get(workflow_id)
        if workflow is None:
            raise BridgeNotFoundError("fake bridge workflow was not found")
        return workflow

    @staticmethod
    def _advance(workflow: _FakeWorkflow) -> None:
        if workflow.status in {
            JobStatus.SUCCEEDED,
            JobStatus.FAILED,
            JobStatus.TIMEOUT,
            JobStatus.CANCELLED,
        }:
            return
        workflow.polls += 1
        workflow.status = (
            JobStatus.QUEUED
            if workflow.polls == 1
            else JobStatus.RUNNING
            if workflow.polls == 2
            else JobStatus.SUCCEEDED
        )

    def _record(
        self,
        workflow: _FakeWorkflow,
    ) -> BridgeWorkflowRecord:
        request = workflow.submit.request
        child_jobs = tuple(
            BridgeChildJob(
                task_id=task.task_id,
                calculation_id=(
                    "fakecalc_"
                    + sha256(task.task_id.encode("utf-8")).hexdigest()[:16]
                ),
                task_input_hash=task.task_input_hash,
                status=workflow.status,
                raw_status=f"fake:{workflow.status.value}",
            )
            for task in request.task_specs
        )
        terminal_result = None
        manifest: tuple[ArtifactRef, ...] = ()
        if workflow.status in {
            JobStatus.SUCCEEDED,
            JobStatus.FAILED,
            JobStatus.TIMEOUT,
            JobStatus.CANCELLED,
        }:
            message = (
                "Fake Bridge lifecycle completed; no VASP calculation "
                "was executed."
            )
            artifact = ArtifactRef(
                uri=(
                    f"bridge://fake/{workflow.workflow_id}/"
                    "lifecycle-result.json"
                ),
                sha256=sha256(message.encode("utf-8")).hexdigest(),
            )
            manifest = (artifact,)
            task_results = tuple(
                DFTTaskResultEnvelope(
                    task_id=task.task_id,
                    attempt_id=f"fakeattempt_{workflow.workflow_id[7:]}",
                    task_input_hash=task.task_input_hash,
                    terminal_status=workflow.status,
                    parsed_summary={
                        "scientific_results": None,
                        "message": message,
                    },
                    warnings=(),
                    errors=(
                        ()
                        if workflow.status is JobStatus.SUCCEEDED
                        else (message,)
                    ),
                    is_mock=True,
                )
                for task in request.task_specs
            )
            claim_results = tuple(
                ClaimResult(
                    claim_id=(
                        "fakeclaim_"
                        + sha256(
                            claim.claim_type.encode("utf-8")
                        ).hexdigest()[:16]
                    ),
                    candidate_id=request.candidates[0].candidate_id,
                    claim_type=claim.claim_type,
                    status=ClaimStatus.NOT_EVALUATED_MOCK,
                    evidence_level="L1_RETRIEVED",
                    is_mock=True,
                    limitations=(
                        ("Fake Bridge did not execute VASP or produce "
                        "scientific evidence"),
                    ),
                )
                for claim in request.requested_claims
            )
            terminal_result = BridgeTerminalResult(
                terminal_status=workflow.status,
                task_results=task_results,
                claim_results=claim_results,
                output_artifacts=manifest,
                provenance={
                    "bridge": "fake",
                    "is_mock": True,
                },
                is_mock=True,
            )
        return BridgeWorkflowRecord(
            descriptor=self.descriptor,
            workflow_id=workflow.workflow_id,
            idempotency_key=workflow.submit.idempotency_key,
            request_id=request.request_id,
            request_sha256=workflow.submit.request_sha256,
            workflow_plan_hash=request.workflow_plan_hash,
            upstream_snapshot_hash=request.upstream_snapshot_hash,
            task_input_hash=request.task_specs[0].task_input_hash,
            status=workflow.status,
            raw_status=f"fake:{workflow.status.value}",
            child_jobs=child_jobs,
            artifact_manifest=manifest,
            terminal_result=terminal_result,
            is_mock=True,
        )
