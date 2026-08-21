"""Versioned, model-agnostic contracts for Agent02 property prediction.

This companion flow intentionally does not change the frozen CHGNet v1 stage
contract.  A property model must be registered with immutable source, lock and
checkpoint references before it can be selected or executed.
"""

from __future__ import annotations

import hashlib
import json
import math
from enum import StrEnum
from typing import Annotated, Literal

from pydantic import Field, StringConstraints, field_validator, model_validator

from material_agent.ml_screening.models import ArtifactPointer, StrictFrozenModel

PROPERTY_REQUEST_VERSION = "agent02-property-request-v1"
PROPERTY_REGISTRY_VERSION = "agent02-property-registry-v1"

Sha256 = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
GitRevision = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{40}$")]
PropertyId = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_]{0,63}$")]


class PropertyModelFamily(StrEnum):
    CRYSTALFORMER = "crystalformer"
    CRYSTALFRAMER = "crystalframer"
    CT_UAE = "ct_uae"
    CRABNET = "crabnet"
    MODNET = "modnet"


class PropertyInputKind(StrEnum):
    STRUCTURE_CIF = "structure_cif"
    COMPOSITION = "composition"


class PropertyModelAvailability(StrEnum):
    READY = "READY"
    REGISTERED_ONLY = "REGISTERED_ONLY"


class PropertyCapability(StrictFrozenModel):
    """One target head trained for a named property and unit."""

    property_id: PropertyId
    unit: str = Field(min_length=1)
    training_dataset: str = Field(min_length=1)
    target_label: str = Field(min_length=1)
    calibration_status: Literal["NOT_CALIBRATED", "CALIBRATED"] = "NOT_CALIBRATED"


class PropertyModelSpec(StrictFrozenModel):
    """A deployable property head, not merely a source repository name."""

    model_id: str = Field(pattern=r"^[a-z0-9][a-z0-9._-]{0,127}$")
    family: PropertyModelFamily
    display_name: str = Field(min_length=1)
    source_repository: str = Field(pattern=r"^https://github\.com/[^/]+/[^/]+$")
    source_revision: GitRevision
    license: str = Field(min_length=1)
    input_kind: PropertyInputKind
    supported_properties: list[PropertyCapability] = Field(min_length=1)
    environment_lock: ArtifactPointer
    checkpoint: ArtifactPointer | None = None
    availability: PropertyModelAvailability = PropertyModelAvailability.REGISTERED_ONLY
    is_mock: bool = False
    limitations: list[str] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_deployment(self) -> PropertyModelSpec:
        property_ids = [capability.property_id for capability in self.supported_properties]
        if len(property_ids) != len(set(property_ids)):
            raise ValueError("property model capabilities must have unique property IDs")
        if self.availability is PropertyModelAvailability.READY and self.checkpoint is None:
            raise ValueError("READY property model requires an immutable checkpoint artifact")
        if self.is_mock and self.availability is PropertyModelAvailability.READY:
            raise ValueError("mock property models cannot be marked READY")
        return self

    def capability_for(self, property_id: str, unit: str) -> PropertyCapability | None:
        return next(
            (
                capability
                for capability in self.supported_properties
                if capability.property_id == property_id and capability.unit == unit
            ),
            None,
        )


class PropertyModelRegistry(StrictFrozenModel):
    schema_version: Literal["agent02-property-registry-v1"] = PROPERTY_REGISTRY_VERSION
    models: list[PropertyModelSpec] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_unique_model_ids(self) -> PropertyModelRegistry:
        model_ids = [model.model_id for model in self.models]
        if len(model_ids) != len(set(model_ids)):
            raise ValueError("property model registry IDs must be unique")
        return self


class PropertyNeed(StrictFrozenModel):
    """The user's explicit prediction need; it never changes screening thresholds."""

    property_id: PropertyId
    unit: str = Field(min_length=1)
    min_value: float | None = None
    max_value: float | None = None
    preferred_model_ids: list[str] = Field(default_factory=list)

    @field_validator("preferred_model_ids")
    @classmethod
    def validate_preferred_models(cls, value: list[str]) -> list[str]:
        if any(not item for item in value) or len(value) != len(set(value)):
            raise ValueError("preferred model IDs must be unique and non-empty")
        return value

    @model_validator(mode="after")
    def validate_range(self) -> PropertyNeed:
        for value in (self.min_value, self.max_value):
            if value is not None and not math.isfinite(value):
                raise ValueError("property need bounds must be finite")
        if self.min_value is not None and self.max_value is not None and self.min_value > self.max_value:
            raise ValueError("property need min_value must not exceed max_value")
        return self


class PropertyPredictionRequest(StrictFrozenModel):
    schema_version: Literal["agent02-property-request-v1"] = PROPERTY_REQUEST_VERSION
    project_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
    run_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
    candidate_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
    need: PropertyNeed
    composition: dict[str, float] = Field(min_length=1)
    input_structure: ArtifactPointer | None = None
    allow_real_inference: bool = True

    @field_validator("composition")
    @classmethod
    def validate_composition(cls, value: dict[str, float]) -> dict[str, float]:
        if any(not element or not math.isfinite(amount) or amount <= 0 for element, amount in value.items()):
            raise ValueError("composition entries must have non-empty elements and positive finite amounts")
        return dict(sorted(value.items()))


class PropertyModelSelection(StrictFrozenModel):
    request_sha256: Sha256
    selected_model: PropertyModelSpec | None = None
    selected_capability: PropertyCapability | None = None
    rejected_models: dict[str, str] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_pairing(self) -> PropertyModelSelection:
        if (self.selected_model is None) != (self.selected_capability is None):
            raise ValueError("selected model and capability must either both be set or both be null")
        return self


def request_sha256(request: PropertyPredictionRequest) -> str:
    payload = json.dumps(request.model_dump(mode="json"), sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def select_property_model(
    request: PropertyPredictionRequest,
    registry: PropertyModelRegistry,
) -> PropertyModelSelection:
    """Select only a frozen, compatible and ready model.

    Explicit user preferences take precedence.  Otherwise the selection is
    deterministic by model ID.  A model cannot be selected from a repository
    alone: its model-specific checkpoint and lock must be registered.
    """

    rejected: dict[str, str] = {}
    compatible: list[tuple[PropertyModelSpec, PropertyCapability]] = []
    for model in registry.models:
        capability = model.capability_for(request.need.property_id, request.need.unit)
        if capability is None:
            rejected[model.model_id] = "PROPERTY_OR_UNIT_UNSUPPORTED"
        elif model.availability is not PropertyModelAvailability.READY:
            rejected[model.model_id] = "MODEL_ASSETS_NOT_REGISTERED"
        elif model.is_mock or not request.allow_real_inference:
            rejected[model.model_id] = "REAL_INFERENCE_DISABLED"
        elif model.input_kind is PropertyInputKind.STRUCTURE_CIF and request.input_structure is None:
            rejected[model.model_id] = "STRUCTURE_INPUT_REQUIRED"
        else:
            compatible.append((model, capability))

    preferred_index = {model_id: index for index, model_id in enumerate(request.need.preferred_model_ids)}
    compatible.sort(key=lambda item: (preferred_index.get(item[0].model_id, len(preferred_index)), item[0].model_id))
    if not compatible:
        return PropertyModelSelection(request_sha256=request_sha256(request), rejected_models=rejected)
    model, capability = compatible[0]
    return PropertyModelSelection(
        request_sha256=request_sha256(request),
        selected_model=model,
        selected_capability=capability,
        rejected_models=rejected,
    )
