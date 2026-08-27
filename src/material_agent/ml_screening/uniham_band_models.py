"""Hash-bound contracts for band postprocessing of a learned Hamiltonian."""

from __future__ import annotations

import hashlib
import json
import re
from enum import StrEnum
from typing import Literal

from pydantic import Field, field_validator, model_validator

try:
    from material_agent.ml_screening.uniham_models import (
        Sha256,
        UniHamArtifactFile,
        UniHamGraphBundle,
        UniHamGraphManifest,
        UniHamRuntimeProvenance,
        UniHamStrictModel,
        validate_root_relative_path,
    )
except ModuleNotFoundError:  # pragma: no cover - standalone GPU companion
    from uniham_models import (  # type: ignore[no-redef]
        Sha256,
        UniHamArtifactFile,
        UniHamGraphBundle,
        UniHamGraphManifest,
        UniHamRuntimeProvenance,
        UniHamStrictModel,
        validate_root_relative_path,
    )

UNIHAM_BAND_REQUEST_VERSION = "agent02-uniham-band-request-v1"
UNIHAM_BAND_WORKER_VERSION = "agent02-uniham-band-worker-v1"


class UniHamBandWorkerStatus(StrEnum):
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"


class UniHamBandRequest(UniHamStrictModel):
    schema_version: Literal["agent02-uniham-band-request-v1"] = (
        UNIHAM_BAND_REQUEST_VERSION
    )
    project_id: str = Field(min_length=1)
    run_id: str = Field(min_length=1)
    candidate_id: str = Field(min_length=1)
    input_structure: UniHamArtifactFile
    soc_graph: UniHamGraphBundle
    soc_manifest: UniHamGraphManifest
    hamiltonian: UniHamArtifactFile
    hamiltonian_operation_key: Sha256
    hamgnn_source_revision: str = Field(min_length=7)
    band_calculator_sha256: Sha256
    nk: int = Field(default=120, ge=24, le=2_000)
    structure_name: Literal["crystal"] = "crystal"
    soc_switch: Literal[True] = True
    spin_colinear: Literal[False] = False
    auto_mode: Literal[True] = True
    ham_type: Literal["openmx"] = "openmx"
    nao_max: Literal[26] = 26
    device: Literal["cpu"] = "cpu"
    is_mock: Literal[False] = False

    @field_validator("project_id", "run_id", "candidate_id")
    @classmethod
    def validate_identity(cls, value: str) -> str:
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", value):
            raise ValueError("Uni-HamGNN band identity must be path-safe")
        return value

    @model_validator(mode="after")
    def validate_lineage(self) -> UniHamBandRequest:
        if self.soc_manifest.soc_mode != "soc":
            raise ValueError("band postprocessing requires the SOC graph manifest")
        if self.soc_manifest.structure_sha256 != self.input_structure.sha256:
            raise ValueError("band graph and structure hashes differ")
        if self.soc_manifest.graph_data_sha256 != self.soc_graph.graph_data.sha256:
            raise ValueError("band graph data and manifest hashes differ")
        if self.soc_manifest.nao_max != self.nao_max:
            raise ValueError("band graph and request nao_max differ")
        if not self.hamiltonian.root_relative_path.endswith(
            "/output/hamiltonian.npy"
        ):
            raise ValueError("band request requires a worker Hamiltonian Artifact")
        expected_fragment = (
            f"/{self.hamiltonian_operation_key}/worker-output/"
            "output/hamiltonian.npy"
        )
        if expected_fragment not in f"/{self.hamiltonian.root_relative_path}":
            raise ValueError("band Hamiltonian path and operation key differ")
        return self


class UniHamBandExecutionPlan(UniHamStrictModel):
    request: UniHamBandRequest
    operation_key: Sha256
    output_sandbox_relative_path: str

    @field_validator("output_sandbox_relative_path")
    @classmethod
    def validate_sandbox(cls, value: str) -> str:
        return validate_root_relative_path(value)

    @model_validator(mode="after")
    def validate_operation(self) -> UniHamBandExecutionPlan:
        expected = uniham_band_operation_key(self.request)
        if self.operation_key != expected:
            raise ValueError("band operation key differs from request")
        sandbox = (
            f"stages/agent02/{self.request.run_id}/uniham-band/"
            f"{self.operation_key}/worker-output"
        )
        if self.output_sandbox_relative_path != sandbox:
            raise ValueError("band output sandbox differs from operation key")
        return self


class UniHamBandWorkerLimits(UniHamStrictModel):
    wall_time_seconds: int = Field(default=900, gt=0, le=86_400)
    max_stdout_bytes: int = Field(default=2_000_000, gt=0)
    max_single_artifact_bytes: int = Field(default=500_000_000, gt=0)
    max_total_output_bytes: int = Field(default=600_000_000, gt=0)


class UniHamBandWorkerRequest(UniHamStrictModel):
    schema_version: Literal["agent02-uniham-band-worker-v1"] = (
        UNIHAM_BAND_WORKER_VERSION
    )
    plan: UniHamBandExecutionPlan
    limits: UniHamBandWorkerLimits = Field(default_factory=UniHamBandWorkerLimits)


class UniHamBandProducedArtifact(UniHamStrictModel):
    root_relative_path: str
    sha256: Sha256
    size_bytes: int = Field(gt=0)
    media_type: str = Field(min_length=1)

    @field_validator("root_relative_path")
    @classmethod
    def validate_path(cls, value: str) -> str:
        return validate_root_relative_path(value)


class UniHamBandWorkerResponse(UniHamStrictModel):
    schema_version: Literal["agent02-uniham-band-worker-v1"] = (
        UNIHAM_BAND_WORKER_VERSION
    )
    operation_key: Sha256
    status: UniHamBandWorkerStatus
    produced_artifacts: tuple[UniHamBandProducedArtifact, ...] = ()
    runtime_provenance: UniHamRuntimeProvenance | None = None
    warnings: tuple[str, ...] = ()
    errors: tuple[str, ...] = ()
    is_mock: Literal[False] = False

    @model_validator(mode="after")
    def validate_response(self) -> UniHamBandWorkerResponse:
        if self.status is UniHamBandWorkerStatus.SUCCEEDED:
            if self.errors or self.runtime_provenance is None:
                raise ValueError("successful band response is inconsistent")
            suffixes = tuple(
                sorted(
                    artifact.root_relative_path.rsplit(".", maxsplit=1)[-1]
                    for artifact in self.produced_artifacts
                )
            )
            if suffixes != ("cif", "dat", "json", "png", "yaml"):
                raise ValueError("band response requires exact output types")
        elif not self.errors:
            raise ValueError("failed band response requires an error")
        return self


def build_uniham_band_plan(request: UniHamBandRequest) -> UniHamBandExecutionPlan:
    operation_key = uniham_band_operation_key(request)
    return UniHamBandExecutionPlan(
        request=request,
        operation_key=operation_key,
        output_sandbox_relative_path=(
            f"stages/agent02/{request.run_id}/uniham-band/"
            f"{operation_key}/worker-output"
        ),
    )


def uniham_band_operation_key(request: UniHamBandRequest) -> str:
    payload = {
        "schema_version": "agent02-uniham-band-operation-v1",
        "request": request.model_dump(mode="json"),
    }
    encoded = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode()
    return hashlib.sha256(encoded).hexdigest()
