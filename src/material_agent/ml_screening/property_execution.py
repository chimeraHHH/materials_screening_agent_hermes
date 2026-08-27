"""Immutable planning contracts for one Agent02 property-model prediction."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Literal

from pydantic import Field, field_validator, model_validator

from material_agent.ml_screening.property_models import (
    PropertyCapability,
    PropertyInputKind,
    PropertyModelSelection,
    PropertyModelSpec,
    PropertyPredictionRequest,
    StrictFrozenModel,
)

PROPERTY_PLAN_VERSION = "agent02-property-plan-v1"
PROPERTY_WORKER_VERSION = "agent02-property-worker-v1"
PROPERTY_RESULT_VERSION = "agent02-property-result-v1"


def _safe_relative_path(value: str) -> str:
    path = PurePosixPath(value)
    if not value or "\\" in value or path.is_absolute() or value != path.as_posix():
        raise ValueError("path must be normalized and root-relative")
    if any(part in {"", ".", ".."} for part in path.parts):
        raise ValueError("path traversal is not allowed")
    return value


class PropertyInputArtifact(StrictFrozenModel):
    artifact_uri: str
    root_relative_path: str
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    size_bytes: int = Field(gt=0)
    media_type: Literal["chemical/x-cif"]

    @field_validator("root_relative_path")
    @classmethod
    def validate_relative_path(cls, value: str) -> str:
        return _safe_relative_path(value)

    @model_validator(mode="after")
    def validate_uri(self) -> PropertyInputArtifact:
        if self.artifact_uri != f"artifact://{self.root_relative_path}":
            raise ValueError("artifact URI must match its root-relative path")
        return self


class PropertyExecutionPlan(StrictFrozenModel):
    schema_version: Literal["agent02-property-plan-v1"] = PROPERTY_PLAN_VERSION
    request: PropertyPredictionRequest
    selected_model: PropertyModelSpec
    selected_capability: PropertyCapability
    input_structure: PropertyInputArtifact | None = None
    operation_key: str = Field(pattern=r"^[0-9a-f]{64}$")
    output_sandbox_relative_path: str
    created_at: datetime

    @field_validator("output_sandbox_relative_path")
    @classmethod
    def validate_sandbox(cls, value: str) -> str:
        return _safe_relative_path(value)

    @model_validator(mode="after")
    def validate_plan(self) -> PropertyExecutionPlan:
        if self.selected_model.capability_for(
            self.request.need.property_id, self.request.need.unit
        ) != self.selected_capability:
            raise ValueError("selected capability does not match the request")
        if self.selected_model.input_kind is PropertyInputKind.STRUCTURE_CIF and self.input_structure is None:
            raise ValueError("structure model requires a verified CIF input artifact")
        if self.selected_model.input_kind is PropertyInputKind.COMPOSITION and self.input_structure is not None:
            raise ValueError("composition model must not receive an unused structure artifact")
        expected = property_operation_key(
            request=self.request,
            model=self.selected_model,
            capability=self.selected_capability,
            input_structure=self.input_structure,
        )
        if expected != self.operation_key:
            raise ValueError("property operation key differs from frozen inputs")
        expected_sandbox = f"stages/agent02/{self.request.run_id}/property/{self.operation_key}/worker-output"
        if self.output_sandbox_relative_path != expected_sandbox:
            raise ValueError("property output sandbox differs from operation identity")
        if self.created_at.tzinfo is None:
            raise ValueError("property plan timestamp must be timezone-aware")
        return self


class PropertyWorkerRequest(StrictFrozenModel):
    schema_version: Literal["agent02-property-worker-v1"] = PROPERTY_WORKER_VERSION
    plan: PropertyExecutionPlan
    wall_time_seconds: int = Field(default=600, gt=0, le=7200)
    max_stdout_bytes: int = Field(default=100_000, gt=0, le=2_000_000)


class PropertyOutputArtifact(StrictFrozenModel):
    artifact_uri: str
    root_relative_path: str
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    size_bytes: int = Field(gt=0)
    media_type: Literal["application/json"]

    @field_validator("root_relative_path")
    @classmethod
    def validate_relative_path(cls, value: str) -> str:
        return _safe_relative_path(value)

    @model_validator(mode="after")
    def validate_uri(self) -> PropertyOutputArtifact:
        if self.artifact_uri != f"artifact://{self.root_relative_path}":
            raise ValueError("artifact URI must match its root-relative path")
        return self


class PropertyWorkerResponse(StrictFrozenModel):
    schema_version: Literal["agent02-property-worker-v1"] = PROPERTY_WORKER_VERSION
    operation_key: str = Field(pattern=r"^[0-9a-f]{64}$")
    model_id: str
    property_id: str
    unit: str
    status: Literal["SUCCEEDED", "FAILED"]
    prediction: float | None = None
    output: PropertyOutputArtifact | None = None
    is_mock: bool
    warnings: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_response(self) -> PropertyWorkerResponse:
        if self.status == "SUCCEEDED":
            if self.prediction is None or self.output is None or self.errors:
                raise ValueError("successful property response requires prediction and output only")
        elif not self.errors:
            raise ValueError("failed property response requires a public error")
        return self


class PropertyPredictionResult(StrictFrozenModel):
    schema_version: Literal["agent02-property-result-v1"] = PROPERTY_RESULT_VERSION
    project_id: str
    run_id: str
    candidate_id: str
    operation_key: str = Field(pattern=r"^[0-9a-f]{64}$")
    plan_uri: str
    plan_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    model_id: str
    property_id: str
    value: float | None
    unit: str
    status: Literal["SUCCEEDED", "FAILED"]
    is_mock: bool
    evidence_level: Literal["L1_RETRIEVED"] = "L1_RETRIEVED"
    scientific_conclusion: Literal[False] = False
    warnings: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)


def property_operation_key(
    *,
    request: PropertyPredictionRequest,
    model: PropertyModelSpec,
    capability: PropertyCapability,
    input_structure: PropertyInputArtifact | None,
) -> str:
    payload = {
        "schema_version": "agent02-property-operation-v1",
        "request": request.model_dump(mode="json"),
        "model": model.model_dump(mode="json"),
        "capability": capability.model_dump(mode="json"),
        "input_structure": None if input_structure is None else input_structure.model_dump(mode="json"),
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def build_property_execution_plan(
    request: PropertyPredictionRequest,
    selection: PropertyModelSelection,
    *,
    artifact_root: Path,
    created_at: datetime | None = None,
) -> PropertyExecutionPlan:
    if selection.selected_model is None or selection.selected_capability is None:
        raise ValueError("a compatible READY property model must be selected before planning")
    root = artifact_root.resolve(strict=True)
    _verify_registered_artifact(root, selection.selected_model.environment_lock, "environment lock")
    assert selection.selected_model.checkpoint is not None
    _verify_registered_artifact(root, selection.selected_model.checkpoint, "model checkpoint")
    structure = None
    if request.input_structure is not None:
        relative_path = request.input_structure.uri.removeprefix("artifact://")
        _safe_relative_path(relative_path)
        path = root.joinpath(*relative_path.split("/"))
        try:
            resolved = path.resolve(strict=True)
        except FileNotFoundError as exc:
            raise ValueError("property structure input is unsafe") from exc
        if root not in resolved.parents or path.is_symlink() or not path.is_file():
            raise ValueError("property structure input is unsafe")
        payload = path.read_bytes()
        if hashlib.sha256(payload).hexdigest() != request.input_structure.sha256:
            raise ValueError("property structure input hash mismatch")
        structure = PropertyInputArtifact(
            artifact_uri=request.input_structure.uri,
            root_relative_path=relative_path,
            sha256=request.input_structure.sha256,
            size_bytes=len(payload),
            media_type="chemical/x-cif",
        )
    key = property_operation_key(
        request=request,
        model=selection.selected_model,
        capability=selection.selected_capability,
        input_structure=structure,
    )
    return PropertyExecutionPlan(
        request=request,
        selected_model=selection.selected_model,
        selected_capability=selection.selected_capability,
        input_structure=structure,
        operation_key=key,
        output_sandbox_relative_path=f"stages/agent02/{request.run_id}/property/{key}/worker-output",
        created_at=created_at or datetime.now(UTC),
    )


def _verify_registered_artifact(root: Path, artifact, label: str) -> None:
    """Require READY deployment assets to exist under the artifact root."""

    relative_path = artifact.uri.removeprefix("artifact://")
    _safe_relative_path(relative_path)
    path = root.joinpath(*relative_path.split("/"))
    try:
        resolved = path.resolve(strict=True)
    except FileNotFoundError as exc:
        raise ValueError(f"registered {label} is unsafe or missing") from exc
    if root not in resolved.parents or path.is_symlink() or not path.is_file():
        raise ValueError(f"registered {label} is unsafe or missing")
    if hashlib.sha256(path.read_bytes()).hexdigest() != artifact.sha256:
        raise ValueError(f"registered {label} hash mismatch")
