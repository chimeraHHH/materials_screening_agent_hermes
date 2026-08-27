"""Hash-bound contracts for CIF -> non-SCF OpenMX graph preparation.

This stage constructs H0/overlap inputs only.  It must never invoke an OpenMX
self-consistent executable and it cannot publish DFT evidence.
"""

from __future__ import annotations

import hashlib
import json
import re
from enum import StrEnum
from typing import Literal

from pydantic import Field, field_validator, model_validator

from material_agent.inspiration.models import ArtifactPointerV1
from material_agent.ml_screening.uniham_models import (
    Sha256,
    UniHamGraphManifest,
    UniHamStrictModel,
    validate_root_relative_path,
)

GRAPH_PREP_VERSION = "agent02-uniham-graph-prep-v1"


class GraphPrepStatus(StrEnum):
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"


class NonSCFGraphPrepParameters(UniHamStrictModel):
    """DeepSeek-selectable parameters exposed by the runtime capability."""

    basis_id: Literal["openmx-dft-data19-nao26"] = "openmx-dft-data19-nao26"
    nao_max: Literal[26] = 26
    k_grid: tuple[int, int, int] = (1, 1, 1)
    energy_cutoff_ry: float = Field(default=150.0, ge=80.0, le=400.0)
    electronic_temperature_k: float = Field(default=300.0, ge=1.0, le=2_000.0)

    @model_validator(mode="after")
    def validate_grid(self) -> NonSCFGraphPrepParameters:
        if any(value < 1 or value > 24 for value in self.k_grid):
            raise ValueError("non-SCF graph-prep k grid must be within [1, 24]")
        return self


class UniHamGraphPrepRequest(UniHamStrictModel):
    schema_version: Literal["agent02-uniham-graph-prep-v1"] = GRAPH_PREP_VERSION
    project_id: str
    run_id: str
    candidate_id: str
    input_structure: ArtifactPointerV1
    parameters: NonSCFGraphPrepParameters
    hamgnn_source_revision: str = Field(min_length=7)
    graph_generator_revision: str = Field(min_length=7)
    dft_data_version: Literal["DFT_DATA19"] = "DFT_DATA19"
    is_mock: Literal[False] = False

    @field_validator("project_id", "run_id", "candidate_id")
    @classmethod
    def path_safe(cls, value: str) -> str:
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", value):
            raise ValueError("graph-prep identities must be path-safe")
        return value


def graph_prep_operation_key(request: UniHamGraphPrepRequest) -> str:
    payload = {
        "schema_version": GRAPH_PREP_VERSION,
        "request": request.model_dump(mode="json"),
    }
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


class UniHamGraphPrepPlan(UniHamStrictModel):
    schema_version: Literal["agent02-uniham-graph-prep-v1"] = GRAPH_PREP_VERSION
    request: UniHamGraphPrepRequest
    operation_key: Sha256
    input_root_relative_path: str
    output_root_relative_path: str

    @field_validator("input_root_relative_path", "output_root_relative_path")
    @classmethod
    def relative_path(cls, value: str) -> str:
        return validate_root_relative_path(value)

    @model_validator(mode="after")
    def validate_identity(self) -> UniHamGraphPrepPlan:
        expected = graph_prep_operation_key(self.request)
        if self.operation_key != expected:
            raise ValueError("graph-prep operation key differs from request")
        root = f"stages/agent02/{self.request.run_id}/graph-prep/{expected}"
        if self.input_root_relative_path != f"{root}/input/structure.cif":
            raise ValueError("graph-prep input path differs from operation identity")
        if self.output_root_relative_path != f"{root}/worker-output":
            raise ValueError("graph-prep output path differs from operation identity")
        return self


def build_graph_prep_plan(request: UniHamGraphPrepRequest) -> UniHamGraphPrepPlan:
    key = graph_prep_operation_key(request)
    root = f"stages/agent02/{request.run_id}/graph-prep/{key}"
    return UniHamGraphPrepPlan(
        request=request,
        operation_key=key,
        input_root_relative_path=f"{root}/input/structure.cif",
        output_root_relative_path=f"{root}/worker-output",
    )


class UniHamGraphPrepToolchain(UniHamStrictModel):
    openmx_postprocess_sha256: Sha256
    read_openmx_sha256: Sha256
    graph_data_gen_sha256: Sha256
    dft_data_manifest_sha256: Sha256
    forbids_self_consistent_openmx: Literal[True] = True


class UniHamGraphPrepWorkerLimits(UniHamStrictModel):
    wall_time_seconds: int = Field(default=1_800, ge=1, le=14_400)
    max_stdout_bytes: int = Field(default=2_000_000, ge=1)
    max_single_artifact_bytes: int = Field(default=2_000_000_000, ge=1)
    max_total_output_bytes: int = Field(default=4_000_000_000, ge=1)


class UniHamGraphPrepWorkerRequest(UniHamStrictModel):
    schema_version: Literal["agent02-uniham-graph-prep-v1"] = GRAPH_PREP_VERSION
    plan: UniHamGraphPrepPlan
    toolchain: UniHamGraphPrepToolchain
    limits: UniHamGraphPrepWorkerLimits = Field(
        default_factory=UniHamGraphPrepWorkerLimits
    )


class UniHamGraphPrepArtifact(UniHamStrictModel):
    root_relative_path: str
    sha256: Sha256
    size_bytes: int = Field(gt=0)
    media_type: str = Field(min_length=1)

    @field_validator("root_relative_path")
    @classmethod
    def relative_path(cls, value: str) -> str:
        return validate_root_relative_path(value)


class UniHamGraphPrepWorkerResponse(UniHamStrictModel):
    schema_version: Literal["agent02-uniham-graph-prep-v1"] = GRAPH_PREP_VERSION
    operation_key: Sha256
    status: GraphPrepStatus
    artifacts: tuple[UniHamGraphPrepArtifact, ...] = ()
    non_soc_manifest: UniHamGraphManifest | None = None
    soc_manifest: UniHamGraphManifest | None = None
    errors: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()
    self_consistent_dft_invocations: Literal[0] = 0
    real_execution: bool

    @model_validator(mode="after")
    def validate_status(self) -> UniHamGraphPrepWorkerResponse:
        if self.status is GraphPrepStatus.SUCCEEDED:
            if self.errors or not self.artifacts:
                raise ValueError("successful graph prep requires artifacts")
            if self.non_soc_manifest is None or self.soc_manifest is None:
                raise ValueError("successful graph prep requires both manifests")
        elif not self.errors:
            raise ValueError("failed graph prep requires bounded errors")
        return self
