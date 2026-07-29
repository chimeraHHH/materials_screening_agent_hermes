"""Strict JSON contracts for the Agent03 ↔ VASPilot bridge boundary."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import Field, model_validator

from .models import (
    ArtifactRef,
    ClaimResult,
    DFTRequest,
    DFTTaskResultEnvelope,
    JobStatus,
    Sha256,
    StrictModel,
    canonical_hash,
)

BRIDGE_PROTOCOL_VERSION = "agent03-vaspilot-bridge-v1"


class BridgeBackendDescriptor(StrictModel):
    schema_version: Literal["1.0.0"] = "1.0.0"
    protocol_version: Literal["agent03-vaspilot-bridge-v1"] = (
        BRIDGE_PROTOCOL_VERSION
    )
    backend_id: str = Field(min_length=1)
    backend_version: str = Field(min_length=1)
    adapter_version: str = Field(min_length=1)
    is_mock: bool
    supported_task_types: tuple[str, ...] = ()


class BridgeHealth(StrictModel):
    schema_version: Literal["1.0.0"] = "1.0.0"
    descriptor: BridgeBackendDescriptor
    status: Literal["READY", "DEGRADED", "UNAVAILABLE"]
    detail: str | None = None


class BridgeSubmitRequest(StrictModel):
    schema_version: Literal["1.0.0"] = "1.0.0"
    protocol_version: Literal["agent03-vaspilot-bridge-v1"] = (
        BRIDGE_PROTOCOL_VERSION
    )
    idempotency_key: str = Field(min_length=1)
    request_sha256: Sha256
    request: DFTRequest

    @model_validator(mode="after")
    def validate_request_hash(self) -> BridgeSubmitRequest:
        if self.request_sha256 != canonical_hash(self.request):
            raise ValueError("bridge request_sha256 does not match DFTRequest")
        return self


class BridgeChildJob(StrictModel):
    task_id: str = Field(min_length=1)
    calculation_id: str = Field(min_length=1)
    task_input_hash: Sha256
    status: JobStatus
    raw_status: str = Field(min_length=1)
    slurm_id: str | None = None


class BridgeTerminalResult(StrictModel):
    terminal_status: JobStatus
    task_results: tuple[DFTTaskResultEnvelope, ...]
    claim_results: tuple[ClaimResult, ...] = ()
    output_artifacts: tuple[ArtifactRef, ...] = ()
    warnings: tuple[str, ...] = ()
    errors: tuple[str, ...] = ()
    provenance: dict[str, Any] = Field(default_factory=dict)
    is_mock: bool

    @model_validator(mode="after")
    def terminal_invariants(self) -> BridgeTerminalResult:
        terminal = {
            JobStatus.SUCCEEDED,
            JobStatus.FAILED,
            JobStatus.TIMEOUT,
            JobStatus.CANCELLED,
        }
        if self.terminal_status not in terminal:
            raise ValueError("bridge terminal result requires a terminal status")
        if any(task.is_mock != self.is_mock for task in self.task_results):
            raise ValueError("bridge task result mock flags are inconsistent")
        if any(claim.is_mock != self.is_mock for claim in self.claim_results):
            raise ValueError("bridge claim result mock flags are inconsistent")
        return self


class BridgeWorkflowRecord(StrictModel):
    schema_version: Literal["1.0.0"] = "1.0.0"
    protocol_version: Literal["agent03-vaspilot-bridge-v1"] = (
        BRIDGE_PROTOCOL_VERSION
    )
    descriptor: BridgeBackendDescriptor
    workflow_id: str = Field(min_length=1)
    idempotency_key: str = Field(min_length=1)
    request_id: str = Field(min_length=1)
    request_sha256: Sha256
    workflow_plan_hash: Sha256
    upstream_snapshot_hash: Sha256
    task_input_hash: Sha256
    status: JobStatus
    raw_status: str = Field(min_length=1)
    child_jobs: tuple[BridgeChildJob, ...] = ()
    artifact_manifest: tuple[ArtifactRef, ...] = ()
    terminal_result: BridgeTerminalResult | None = None
    is_mock: bool

    @model_validator(mode="after")
    def record_invariants(self) -> BridgeWorkflowRecord:
        if self.is_mock != self.descriptor.is_mock:
            raise ValueError("bridge descriptor and workflow mock flags disagree")
        terminal = {
            JobStatus.SUCCEEDED,
            JobStatus.FAILED,
            JobStatus.TIMEOUT,
            JobStatus.CANCELLED,
        }
        if self.status in terminal:
            if self.terminal_result is None:
                raise ValueError("terminal bridge workflow is missing its result")
            if (
                self.terminal_result.terminal_status != self.status
                or self.terminal_result.is_mock != self.is_mock
            ):
                raise ValueError("bridge terminal result conflicts with workflow")
        elif self.terminal_result is not None:
            raise ValueError("non-terminal bridge workflow cannot contain a result")
        if self.child_jobs and self.task_input_hash not in {
            child.task_input_hash for child in self.child_jobs
        }:
            raise ValueError("bridge primary task hash is absent from child jobs")
        result_artifacts = (
            set(self.terminal_result.output_artifacts)
            if self.terminal_result is not None
            else set()
        )
        if not result_artifacts.issubset(set(self.artifact_manifest)):
            raise ValueError(
                "bridge result references an artifact absent from the manifest"
            )
        return self


class BridgeArtifactManifest(StrictModel):
    schema_version: Literal["1.0.0"] = "1.0.0"
    protocol_version: Literal["agent03-vaspilot-bridge-v1"] = (
        BRIDGE_PROTOCOL_VERSION
    )
    workflow_id: str = Field(min_length=1)
    task_input_hash: Sha256
    artifacts: tuple[ArtifactRef, ...] = ()
