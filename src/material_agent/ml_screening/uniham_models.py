"""Strict contracts for the optional Uni-HamGNN companion flow.

Uni-HamGNN consumes pickle-bearing model and graph artifacts.  They are kept
outside the main process and are accepted only with explicit trust provenance,
content hashes, and structure/basis linkage.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime
from enum import StrEnum
from pathlib import PurePosixPath
from typing import Annotated, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)

UNIHAM_REQUEST_VERSION = "agent02-uniham-request-v1"
UNIHAM_PLAN_VERSION = "agent02-uniham-plan-v1"
UNIHAM_WORKER_PROTOCOL_VERSION = "agent02-uniham-worker-v1"
UNIHAM_RESULT_VERSION = "agent02-uniham-result-v1"
UNIHAM_GRAPH_MANIFEST_VERSION = "agent02-uniham-graph-manifest-v1"

Sha256 = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]


class UniHamStrictModel(BaseModel):
    model_config = ConfigDict(
        allow_inf_nan=False,
        extra="forbid",
        frozen=True,
        validate_assignment=True,
    )


class UniHamWorkerStatus(StrEnum):
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"


class UniHamSocMode(StrEnum):
    NON_SOC = "non_soc"
    SOC = "soc"


class UniHamArtifactFile(UniHamStrictModel):
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
    def validate_uri_matches_path(self) -> UniHamArtifactFile:
        if self.artifact_uri != f"artifact://{self.root_relative_path}":
            raise ValueError("artifact URI must match root-relative path")
        return self


class UniHamGraphBundle(UniHamStrictModel):
    root_relative_directory: str = Field(min_length=1)
    graph_data: UniHamArtifactFile
    manifest: UniHamArtifactFile

    @field_validator("root_relative_directory")
    @classmethod
    def validate_relative_directory(cls, value: str) -> str:
        return validate_root_relative_path(value)

    @model_validator(mode="after")
    def freeze_bundle_layout(self) -> UniHamGraphBundle:
        expected_graph = f"{self.root_relative_directory}/graph_data.npz"
        expected_manifest = (
            f"{self.root_relative_directory}/hermes-graph-manifest.json"
        )
        if self.graph_data.root_relative_path != expected_graph:
            raise ValueError("Uni-HamGNN graph bundle requires graph_data.npz")
        if self.manifest.root_relative_path != expected_manifest:
            raise ValueError(
                "Uni-HamGNN graph bundle requires hermes-graph-manifest.json"
            )
        return self


class UniHamGraphManifest(UniHamStrictModel):
    schema_version: Literal["agent02-uniham-graph-manifest-v1"] = (
        UNIHAM_GRAPH_MANIFEST_VERSION
    )
    structure_sha256: Sha256
    graph_data_sha256: Sha256
    soc_mode: UniHamSocMode
    interface: Literal["openmx"] = "openmx"
    basis_id: str = Field(min_length=1)
    dft_data_version: str = Field(min_length=1)
    graph_generator_revision: str = Field(min_length=7)
    nao_max: Literal[26] = 26


class UniHamInputTrust(UniHamStrictModel):
    trusted_executable_inputs: Literal[True]
    reviewed_by: str = Field(min_length=1)
    reviewed_at: datetime
    review_basis: str = Field(min_length=8)

    @field_validator("reviewed_at")
    @classmethod
    def require_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("Uni-HamGNN trust review must be timezone-aware")
        return value


class UniHamBenchmarkMetrics(UniHamStrictModel):
    hamiltonian_mae_ev: float = Field(ge=0)
    band_energy_mae_ev: float = Field(ge=0)
    flat_band_width_mae_ev: float = Field(ge=0)
    soc_gap_mae_ev: float = Field(ge=0)
    band_ordering_accuracy: float = Field(ge=0, le=1)
    crossing_classification_accuracy: float = Field(ge=0, le=1)
    topology_invariant_accuracy: float | None = Field(default=None, ge=0, le=1)


class UniHamBenchmarkBinding(UniHamStrictModel):
    benchmark_id: str = Field(min_length=1)
    report_artifact: UniHamArtifactFile
    model_sha256: Sha256
    hamgnn_source_revision: str = Field(min_length=7)
    dataset_id: str = Field(min_length=1)
    dataset_sha256: Sha256
    held_out_structure_count: int = Field(ge=1)
    dimensionalities: tuple[int, ...] = Field(min_length=1, max_length=4)
    metrics: UniHamBenchmarkMetrics
    reviewed_by: str = Field(min_length=1)
    reviewed_at: datetime

    @model_validator(mode="after")
    def validate_benchmark(self) -> UniHamBenchmarkBinding:
        if self.dimensionalities != tuple(sorted(set(self.dimensionalities))):
            raise ValueError("Uni-HamGNN benchmark dimensionalities are not canonical")
        if any(value not in {0, 1, 2, 3} for value in self.dimensionalities):
            raise ValueError("Uni-HamGNN benchmark dimensionality is invalid")
        if self.reviewed_at.tzinfo is None:
            raise ValueError("Uni-HamGNN benchmark review must be timezone-aware")
        return self


class UniHamInferenceRequest(UniHamStrictModel):
    schema_version: Literal["agent02-uniham-request-v1"] = (
        UNIHAM_REQUEST_VERSION
    )
    project_id: str = Field(min_length=1)
    run_id: str = Field(min_length=1)
    candidate_id: str = Field(min_length=1)
    input_structure: UniHamArtifactFile
    model_pickle: UniHamArtifactFile
    non_soc_graph: UniHamGraphBundle
    soc_graph: UniHamGraphBundle
    model_id: str = Field(min_length=1)
    model_source_url: str = Field(min_length=8)
    model_revision: str = Field(min_length=7)
    weights_license: str = Field(min_length=1)
    hamgnn_source_revision: str = Field(min_length=7)
    predictor_script_sha256: Sha256
    input_trust: UniHamInputTrust
    benchmark: UniHamBenchmarkBinding | None = None
    device: Literal["cpu", "cuda"] = "cpu"
    calculate_mae: Literal[False] = False
    is_mock: bool = False

    @field_validator("project_id", "run_id", "candidate_id")
    @classmethod
    def validate_path_safe_identity(cls, value: str) -> str:
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", value):
            raise ValueError("Uni-HamGNN identities must be path-safe tokens")
        if value in {".", ".."}:
            raise ValueError("Uni-HamGNN identities cannot be dot segments")
        return value

    @model_validator(mode="after")
    def validate_distinct_inputs(self) -> UniHamInferenceRequest:
        if not self.model_pickle.root_relative_path.endswith(".pkl"):
            raise ValueError("Uni-HamGNN model artifact must be a .pkl file")
        roots = {
            self.non_soc_graph.root_relative_directory,
            self.soc_graph.root_relative_directory,
        }
        if len(roots) != 2:
            raise ValueError("non-SOC and SOC graph directories must differ")
        paths = {
            self.input_structure.root_relative_path,
            self.model_pickle.root_relative_path,
            self.non_soc_graph.graph_data.root_relative_path,
            self.non_soc_graph.manifest.root_relative_path,
            self.soc_graph.graph_data.root_relative_path,
            self.soc_graph.manifest.root_relative_path,
        }
        if len(paths) != 6:
            raise ValueError("Uni-HamGNN input artifacts must be distinct")
        if self.benchmark is not None:
            if self.is_mock:
                raise ValueError("mock Uni-HamGNN request cannot bind a benchmark")
            if self.benchmark.model_sha256 != self.model_pickle.sha256:
                raise ValueError("Uni-HamGNN benchmark model hash mismatch")
            if (
                self.benchmark.hamgnn_source_revision
                != self.hamgnn_source_revision
            ):
                raise ValueError("Uni-HamGNN benchmark source revision mismatch")
        return self


class UniHamExecutionPlan(UniHamStrictModel):
    schema_version: Literal["agent02-uniham-plan-v1"] = UNIHAM_PLAN_VERSION
    request: UniHamInferenceRequest
    non_soc_manifest: UniHamGraphManifest
    soc_manifest: UniHamGraphManifest
    operation_key: Sha256
    output_sandbox_relative_path: str
    created_at: datetime

    @field_validator("output_sandbox_relative_path")
    @classmethod
    def validate_output_sandbox(cls, value: str) -> str:
        return validate_root_relative_path(value)

    @model_validator(mode="after")
    def validate_operation_identity(self) -> UniHamExecutionPlan:
        expected = uniham_operation_key(self.request)
        if self.operation_key != expected:
            raise ValueError("Uni-HamGNN operation key differs from request")
        expected_sandbox = (
            f"stages/agent02/{self.request.run_id}/uniham/"
            f"{self.operation_key}/worker-output"
        )
        if self.output_sandbox_relative_path != expected_sandbox:
            raise ValueError("Uni-HamGNN sandbox differs from operation identity")
        if self.created_at.tzinfo is None:
            raise ValueError("Uni-HamGNN plan timestamp must be timezone-aware")
        return self


class UniHamWorkerLimits(UniHamStrictModel):
    wall_time_seconds: int = Field(default=3600, gt=0, le=86_400)
    max_stdout_bytes: int = Field(default=2_000_000, gt=0)
    max_single_artifact_bytes: int = Field(default=4_000_000_000, gt=0)
    max_total_output_bytes: int = Field(default=4_100_000_000, gt=0)


class UniHamWorkerRequest(UniHamStrictModel):
    schema_version: Literal["agent02-uniham-worker-v1"] = (
        UNIHAM_WORKER_PROTOCOL_VERSION
    )
    plan: UniHamExecutionPlan
    limits: UniHamWorkerLimits = Field(default_factory=UniHamWorkerLimits)


class UniHamProducedArtifact(UniHamStrictModel):
    root_relative_path: str
    sha256: Sha256
    size_bytes: int = Field(gt=0)
    media_type: str = Field(default="application/octet-stream", min_length=1)

    @field_validator("root_relative_path")
    @classmethod
    def validate_relative_path(cls, value: str) -> str:
        return validate_root_relative_path(value)


class UniHamRuntimeProvenance(UniHamStrictModel):
    requested_device: Literal["cpu", "cuda"]
    observed_device: Literal["cpu", "cuda"]
    cuda_visible_device_count: int = Field(ge=0, le=1)
    cuda_device_name: str | None = None
    torch_version: str | None = None
    torch_cuda_version: str | None = None

    @model_validator(mode="after")
    def validate_device(self) -> UniHamRuntimeProvenance:
        if self.requested_device != self.observed_device:
            raise ValueError("Uni-HamGNN silently changed the requested device")
        if self.observed_device == "cuda":
            if self.cuda_visible_device_count != 1:
                raise ValueError("Uni-HamGNN CUDA runtime must expose exactly one GPU")
            if not self.cuda_device_name or not self.torch_version:
                raise ValueError("Uni-HamGNN CUDA runtime identity is incomplete")
        elif any(
            value is not None
            for value in (self.cuda_device_name, self.torch_cuda_version)
        ) or self.cuda_visible_device_count != 0:
            raise ValueError("Uni-HamGNN CPU runtime cannot report CUDA resources")
        return self


class UniHamWorkerResponse(UniHamStrictModel):
    schema_version: Literal["agent02-uniham-worker-v1"] = (
        UNIHAM_WORKER_PROTOCOL_VERSION
    )
    operation_key: Sha256
    status: UniHamWorkerStatus
    produced_artifacts: list[UniHamProducedArtifact] = Field(
        default_factory=list
    )
    is_mock: bool
    warnings: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)
    runtime_provenance: UniHamRuntimeProvenance | None = None

    @model_validator(mode="after")
    def validate_status_payload(self) -> UniHamWorkerResponse:
        if self.status is UniHamWorkerStatus.SUCCEEDED:
            if (
                self.errors
                or not self.produced_artifacts
                or self.runtime_provenance is None
            ):
                raise ValueError("successful Uni-HamGNN response is inconsistent")
        elif not self.errors:
            raise ValueError("failed Uni-HamGNN response requires an error")
        return self


class UniHamResult(UniHamStrictModel):
    schema_version: Literal["agent02-uniham-result-v1"] = UNIHAM_RESULT_VERSION
    project_id: str
    run_id: str
    candidate_id: str
    operation_key: Sha256
    plan_uri: str
    plan_sha256: Sha256
    status: UniHamWorkerStatus
    hamiltonian_artifact: UniHamProducedArtifact
    execution_artifacts: list[UniHamProducedArtifact]
    model_id: str
    hamgnn_source_revision: str
    device: Literal["cpu", "cuda"]
    runtime_provenance: UniHamRuntimeProvenance
    is_mock: bool
    evidence_level: Literal["NONE", "L2_ML_SCREENED"] = "NONE"
    benchmark_status: Literal["NOT_RUN", "VALIDATED", "FAILED"] = "NOT_RUN"
    benchmark: UniHamBenchmarkBinding | None = None
    scientific_conclusion: Literal[False] = False
    warnings: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_evidence(self) -> UniHamResult:
        validated = self.benchmark_status == "VALIDATED"
        if validated != (self.benchmark is not None):
            raise ValueError("Uni-HamGNN validated status requires its benchmark")
        if (self.evidence_level == "L2_ML_SCREENED") != validated:
            raise ValueError("Uni-HamGNN L2 evidence requires a validated benchmark")
        if self.is_mock and self.evidence_level != "NONE":
            raise ValueError("mock Uni-HamGNN result cannot publish L2 evidence")
        return self


class UniHamBenchmarkPolicy(UniHamStrictModel):
    maximum_hamiltonian_mae_ev: float = Field(default=0.025, gt=0)
    maximum_band_energy_mae_ev: float = Field(default=0.025, gt=0)
    maximum_flat_band_width_mae_ev: float = Field(default=0.015, gt=0)
    maximum_soc_gap_mae_ev: float = Field(default=0.025, gt=0)
    minimum_band_ordering_accuracy: float = Field(default=0.95, ge=0, le=1)
    minimum_crossing_classification_accuracy: float = Field(
        default=0.95,
        ge=0,
        le=1,
    )
    minimum_held_out_structures: int = Field(default=100, ge=1)
    require_two_dimensional_examples: bool = True


def uniham_operation_key(request: UniHamInferenceRequest) -> str:
    payload = {
        "schema_version": "agent02-uniham-operation-v1",
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
