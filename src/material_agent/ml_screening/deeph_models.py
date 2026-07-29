"""Strict contracts for the optional DeepH companion flow.

These contracts are intentionally independent from the frozen CHGNet v1
contracts.  DeepH-pack needs a trained model and a matching DFT overlap
calculation; a crystal structure alone is never treated as sufficient input.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime
from enum import StrEnum
from pathlib import PurePosixPath
from typing import Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)
from typing_extensions import Annotated


DEEPH_REQUEST_VERSION = "agent02-deeph-request-v1"
DEEPH_PLAN_VERSION = "agent02-deeph-plan-v1"
DEEPH_WORKER_PROTOCOL_VERSION = "agent02-deeph-worker-v1"
DEEPH_RESULT_VERSION = "agent02-deeph-result-v1"

Sha256 = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]


class DeepHStrictModel(BaseModel):
    model_config = ConfigDict(
        allow_inf_nan=False,
        extra="forbid",
        frozen=True,
        validate_assignment=True,
    )


class DeepHInterface(StrEnum):
    OPENMX = "openmx"
    ABACUS = "abacus"


class DeepHWorkerStatus(StrEnum):
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"


class DeepHArtifactFile(DeepHStrictModel):
    artifact_uri: str = Field(min_length=12)
    root_relative_path: str = Field(min_length=1)
    sha256: Sha256
    size_bytes: int = Field(gt=0)
    media_type: str = Field(default="application/octet-stream", min_length=1)

    @field_validator("root_relative_path")
    @classmethod
    def validate_relative_path(cls, value: str) -> str:
        return validate_root_relative_path(value)

    @model_validator(mode="after")
    def validate_uri_matches_path(self) -> DeepHArtifactFile:
        if self.artifact_uri != f"artifact://{self.root_relative_path}":
            raise ValueError("artifact URI must match root-relative path")
        return self


class DeepHArtifactBundle(DeepHStrictModel):
    root_relative_directory: str = Field(min_length=1)
    files: list[DeepHArtifactFile] = Field(min_length=1)

    @field_validator("root_relative_directory")
    @classmethod
    def validate_relative_directory(cls, value: str) -> str:
        return validate_root_relative_path(value)

    @model_validator(mode="after")
    def validate_files_are_unique_and_beneath_root(
        self,
    ) -> DeepHArtifactBundle:
        directory = PurePosixPath(self.root_relative_directory)
        paths = [item.root_relative_path for item in self.files]
        if len(paths) != len(set(paths)):
            raise ValueError("bundle file paths must be unique")
        if any(directory not in PurePosixPath(path).parents for path in paths):
            raise ValueError("bundle files must be beneath the declared directory")
        return self


class DeepHCompatibility(DeepHStrictModel):
    interface: DeepHInterface
    basis_id: str = Field(min_length=1)
    dft_software_version: str = Field(min_length=1)


class DeepHInferenceRequest(DeepHStrictModel):
    schema_version: Literal["agent02-deeph-request-v1"] = (
        DEEPH_REQUEST_VERSION
    )
    project_id: str = Field(min_length=1)
    run_id: str = Field(min_length=1)
    candidate_id: str = Field(min_length=1)
    input_structure: DeepHArtifactFile
    trained_model: DeepHArtifactBundle
    overlap: DeepHArtifactBundle
    model_compatibility: DeepHCompatibility
    overlap_compatibility: DeepHCompatibility
    overlap_structure_sha256: Sha256
    model_id: str = Field(min_length=1)
    deeph_source_revision: str = Field(min_length=7)
    tasks: list[Literal[1, 2, 3, 4]] = Field(
        default_factory=lambda: [1, 2, 3, 4]
    )
    device: Literal["cpu"] = "cpu"
    is_mock: bool = False

    @field_validator("project_id", "run_id", "candidate_id")
    @classmethod
    def validate_path_safe_identity(cls, value: str) -> str:
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", value):
            raise ValueError("DeepH identities must be path-safe ASCII tokens")
        if value in {".", ".."}:
            raise ValueError("DeepH identities cannot be dot path segments")
        return value

    @field_validator("tasks")
    @classmethod
    def freeze_hamiltonian_tasks(
        cls, value: list[Literal[1, 2, 3, 4]]
    ) -> list[Literal[1, 2, 3, 4]]:
        if value != [1, 2, 3, 4]:
            raise ValueError(
                "v1 freezes DeepH Hamiltonian inference to tasks [1,2,3,4]"
            )
        return value

    @model_validator(mode="after")
    def validate_scientific_linkage(self) -> DeepHInferenceRequest:
        if self.model_compatibility != self.overlap_compatibility:
            raise ValueError(
                "trained model and overlap must use the same interface, "
                "basis identity and DFT software version"
            )
        if self.overlap_structure_sha256 != self.input_structure.sha256:
            raise ValueError(
                "overlap structure hash must match the selected input structure"
            )
        roots = {
            self.trained_model.root_relative_directory,
            self.overlap.root_relative_directory,
        }
        if len(roots) != 2:
            raise ValueError("trained-model and overlap directories must differ")
        return self


class DeepHExecutionPlan(DeepHStrictModel):
    schema_version: Literal["agent02-deeph-plan-v1"] = DEEPH_PLAN_VERSION
    request: DeepHInferenceRequest
    operation_key: Sha256
    output_sandbox_relative_path: str
    created_at: datetime

    @field_validator("output_sandbox_relative_path")
    @classmethod
    def validate_output_sandbox(cls, value: str) -> str:
        return validate_root_relative_path(value)

    @model_validator(mode="after")
    def validate_operation_key_and_sandbox(self) -> DeepHExecutionPlan:
        expected = deeph_operation_key(self.request)
        if self.operation_key != expected:
            raise ValueError("DeepH operation key differs from frozen request")
        expected_sandbox = (
            f"stages/agent02/{self.request.run_id}/deeph/"
            f"{self.operation_key}/worker-output"
        )
        if self.output_sandbox_relative_path != expected_sandbox:
            raise ValueError("DeepH output sandbox differs from operation identity")
        if self.created_at.tzinfo is None:
            raise ValueError("DeepH plan timestamp must be timezone-aware")
        return self


class DeepHWorkerLimits(DeepHStrictModel):
    wall_time_seconds: int = Field(default=1800, gt=0, le=86_400)
    max_stdout_bytes: int = Field(default=2_000_000, gt=0)
    max_single_artifact_bytes: int = Field(
        default=1_000_000_000, gt=0
    )
    max_total_output_bytes: int = Field(default=5_000_000_000, gt=0)


class DeepHWorkerRequest(DeepHStrictModel):
    schema_version: Literal["agent02-deeph-worker-v1"] = (
        DEEPH_WORKER_PROTOCOL_VERSION
    )
    plan: DeepHExecutionPlan
    limits: DeepHWorkerLimits = Field(default_factory=DeepHWorkerLimits)


class DeepHProducedArtifact(DeepHStrictModel):
    root_relative_path: str
    sha256: Sha256
    size_bytes: int = Field(gt=0)
    media_type: str = Field(default="application/octet-stream", min_length=1)

    @field_validator("root_relative_path")
    @classmethod
    def validate_relative_path(cls, value: str) -> str:
        return validate_root_relative_path(value)


class DeepHWorkerResponse(DeepHStrictModel):
    schema_version: Literal["agent02-deeph-worker-v1"] = (
        DEEPH_WORKER_PROTOCOL_VERSION
    )
    operation_key: Sha256
    status: DeepHWorkerStatus
    produced_artifacts: list[DeepHProducedArtifact] = Field(
        default_factory=list
    )
    is_mock: bool
    warnings: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_status_payload(self) -> DeepHWorkerResponse:
        if self.status is DeepHWorkerStatus.SUCCEEDED:
            if self.errors:
                raise ValueError("successful DeepH response cannot contain errors")
            if not self.produced_artifacts:
                raise ValueError("successful DeepH response requires artifacts")
        elif not self.errors:
            raise ValueError("failed DeepH response requires a public error")
        return self


class DeepHResult(DeepHStrictModel):
    schema_version: Literal["agent02-deeph-result-v1"] = DEEPH_RESULT_VERSION
    project_id: str
    run_id: str
    candidate_id: str
    operation_key: Sha256
    plan_uri: str
    plan_sha256: Sha256
    status: DeepHWorkerStatus
    produced_artifacts: list[DeepHProducedArtifact]
    is_mock: bool
    evidence_level: Literal["NONE"] = "NONE"
    benchmark_status: Literal["NOT_RUN"] = "NOT_RUN"
    scientific_conclusion: Literal[False] = False
    warnings: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def preserve_evidence_ceiling(self) -> DeepHResult:
        if self.status is DeepHWorkerStatus.SUCCEEDED and self.errors:
            raise ValueError("successful DeepH result cannot contain errors")
        return self


def deeph_operation_key(request: DeepHInferenceRequest) -> str:
    payload = {
        "schema_version": "agent02-deeph-operation-v1",
        "request": request.model_dump(mode="json"),
    }
    encoded = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def validate_root_relative_path(value: str) -> str:
    if "\\" in value:
        raise ValueError("worker paths must use POSIX separators")
    path = PurePosixPath(value)
    if path.is_absolute() or value in {"", "."}:
        raise ValueError("worker paths must be non-empty and root-relative")
    if any(part in {"", ".", ".."} for part in path.parts):
        raise ValueError("worker paths must be normalized without traversal")
    if value != path.as_posix():
        raise ValueError("worker paths must use canonical POSIX form")
    return value
