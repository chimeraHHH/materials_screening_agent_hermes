"""Strict contracts for the optional, structure-based ALIGNN companion flow."""

from __future__ import annotations

import hashlib
import json
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

ALIGNN_REQUEST_VERSION = "agent02-alignn-request-v1"
ALIGNN_PLAN_VERSION = "agent02-alignn-plan-v1"
ALIGNN_WORKER_VERSION = "agent02-alignn-worker-v1"
ALIGNN_RESULT_VERSION = "agent02-alignn-result-v1"
Sha256 = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]


class AlignnStrictModel(BaseModel):
    model_config = ConfigDict(allow_inf_nan=False, extra="forbid", frozen=True)


def validate_root_relative_path(value: str) -> str:
    path = PurePosixPath(value)
    if "\\" in value or path.is_absolute() or value in {"", "."}:
        raise ValueError("paths must be normalized and root-relative")
    if any(part in {"", ".", ".."} for part in path.parts) or value != path.as_posix():
        raise ValueError("paths must not contain traversal")
    return value


class AlignnArtifact(AlignnStrictModel):
    artifact_uri: str
    root_relative_path: str
    sha256: Sha256
    size_bytes: int = Field(gt=0)
    media_type: str = Field(min_length=1)

    @field_validator("root_relative_path")
    @classmethod
    def safe_path(cls, value: str) -> str:
        return validate_root_relative_path(value)

    @model_validator(mode="after")
    def uri_matches_path(self) -> AlignnArtifact:
        if self.artifact_uri != f"artifact://{self.root_relative_path}":
            raise ValueError("artifact URI must match root-relative path")
        return self


class AlignnProperty(StrEnum):
    JARVIS_OPTB88VDW_BAND_GAP = "jarvis_optb88vdw_band_gap"
    JARVIS_MBJ_BAND_GAP = "jarvis_mbj_band_gap"


_PROPERTY_METADATA = {
    AlignnProperty.JARVIS_OPTB88VDW_BAND_GAP: ("jv_optb88vdw_bandgap_alignn", "eV", "JARVIS-DFT OptB88-vdW band gap"),
    AlignnProperty.JARVIS_MBJ_BAND_GAP: ("jv_mbj_bandgap_alignn", "eV", "JARVIS-DFT mBJ band gap"),
}


class AlignnInferenceRequest(AlignnStrictModel):
    schema_version: Literal["agent02-alignn-request-v1"] = ALIGNN_REQUEST_VERSION
    project_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
    run_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
    candidate_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
    input_structure: AlignnArtifact
    model_archive: AlignnArtifact
    target_property: AlignnProperty = AlignnProperty.JARVIS_OPTB88VDW_BAND_GAP
    alignn_version: str = Field(min_length=1)
    environment_lock_sha256: Sha256
    source_revision: str = Field(min_length=7)
    is_mock: bool = False

    @model_validator(mode="after")
    def validate_input_media_types(self) -> AlignnInferenceRequest:
        if self.input_structure.media_type != "chemical/x-cif":
            raise ValueError("ALIGNN v1 accepts only canonical CIF input")
        if self.model_archive.media_type != "application/zip":
            raise ValueError("ALIGNN v1 model must be an explicit ZIP artifact")
        if self.input_structure.root_relative_path == self.model_archive.root_relative_path:
            raise ValueError("structure and model archive must differ")
        return self

    @property
    def upstream_model_name(self) -> str:
        return _PROPERTY_METADATA[self.target_property][0]

    @property
    def unit(self) -> str:
        return _PROPERTY_METADATA[self.target_property][1]

    @property
    def target_method(self) -> str:
        return _PROPERTY_METADATA[self.target_property][2]


class AlignnExecutionPlan(AlignnStrictModel):
    schema_version: Literal["agent02-alignn-plan-v1"] = ALIGNN_PLAN_VERSION
    request: AlignnInferenceRequest
    operation_key: Sha256
    output_sandbox_relative_path: str
    created_at: datetime

    @field_validator("output_sandbox_relative_path")
    @classmethod
    def safe_sandbox(cls, value: str) -> str:
        return validate_root_relative_path(value)

    @model_validator(mode="after")
    def matches_request(self) -> AlignnExecutionPlan:
        if self.operation_key != alignn_operation_key(self.request):
            raise ValueError("ALIGNN operation key differs from frozen request")
        expected = f"stages/agent02/{self.request.run_id}/alignn/{self.operation_key}/worker-output"
        if self.output_sandbox_relative_path != expected:
            raise ValueError("ALIGNN sandbox differs from operation identity")
        if self.created_at.tzinfo is None:
            raise ValueError("ALIGNN plan timestamp must be timezone-aware")
        return self


class AlignnWorkerRequest(AlignnStrictModel):
    schema_version: Literal["agent02-alignn-worker-v1"] = ALIGNN_WORKER_VERSION
    plan: AlignnExecutionPlan
    wall_time_seconds: int = Field(default=300, gt=0, le=3600)
    max_stdout_bytes: int = Field(default=100_000, gt=0, le=2_000_000)


class AlignnWorkerResponse(AlignnStrictModel):
    schema_version: Literal["agent02-alignn-worker-v1"] = ALIGNN_WORKER_VERSION
    operation_key: Sha256
    status: Literal["SUCCEEDED", "FAILED"]
    prediction: float | None = None
    output: AlignnArtifact | None = None
    is_mock: bool
    warnings: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def response_consistency(self) -> AlignnWorkerResponse:
        if self.status == "SUCCEEDED" and (self.prediction is None or self.output is None or self.errors):
            raise ValueError("successful ALIGNN response requires prediction and output only")
        if self.status == "FAILED" and not self.errors:
            raise ValueError("failed ALIGNN response requires a public error")
        return self


class AlignnResult(AlignnStrictModel):
    schema_version: Literal["agent02-alignn-result-v1"] = ALIGNN_RESULT_VERSION
    project_id: str
    run_id: str
    candidate_id: str
    operation_key: Sha256
    plan_uri: str
    plan_sha256: Sha256
    target_property: AlignnProperty
    value: float | None
    unit: str
    target_method: str
    status: Literal["SUCCEEDED", "FAILED"]
    is_mock: bool
    evidence_level: Literal["NONE"] = "NONE"
    benchmark_status: Literal["NOT_RUN"] = "NOT_RUN"
    scientific_conclusion: Literal[False] = False
    warnings: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)


def alignn_operation_key(request: AlignnInferenceRequest) -> str:
    payload = json.dumps(request.model_dump(mode="json"), sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(b"agent02-alignn-operation-v1\0" + payload).hexdigest()
