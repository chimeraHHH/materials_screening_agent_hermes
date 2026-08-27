"""Automatic post-relaxation property-model chaining for Agent02.

The chain is an explicit companion flow: it consumes one already-produced,
hash-verified relaxed structure (for example a MatterSim CIF), then invokes
ct-UAE and ALIGNN using their existing independent ledgers.  It does not
relax structures itself and never upgrades ML evidence to a scientific claim.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Literal

from pydantic import Field, model_validator

from material_agent.ml_screening.alignn_client import AlignnFlowRunner
from material_agent.ml_screening.alignn_models import (
    AlignnInferenceRequest,
    AlignnResult,
)
from material_agent.ml_screening.models import ArtifactPointer, StrictFrozenModel
from material_agent.ml_screening.property_client import PropertyPredictionFlowRunner
from material_agent.ml_screening.property_execution import PropertyPredictionResult
from material_agent.ml_screening.property_models import (
    PropertyModelFamily,
    PropertyModelRegistry,
    PropertyPredictionRequest,
)
from material_agent.retrieval.storage import LocalArtifactStore

PROPERTY_CHAIN_VERSION = "agent02-post-relaxation-property-chain-v1"


class PropertyPredictionChainRequest(StrictFrozenModel):
    schema_version: Literal["agent02-post-relaxation-property-chain-v1"] = PROPERTY_CHAIN_VERSION
    project_id: str
    run_id: str
    candidate_id: str
    relaxation_method: str = Field(min_length=1, max_length=128)
    relaxed_structure: ArtifactPointer
    ct_uae_request: PropertyPredictionRequest
    ct_uae_registry: PropertyModelRegistry
    alignn_request: AlignnInferenceRequest

    @model_validator(mode="after")
    def validate_post_relaxation_inputs(self) -> PropertyPredictionChainRequest:
        if (self.project_id, self.run_id, self.candidate_id) != (
            self.ct_uae_request.project_id,
            self.ct_uae_request.run_id,
            self.ct_uae_request.candidate_id,
        ) or (self.project_id, self.run_id, self.candidate_id) != (
            self.alignn_request.project_id,
            self.alignn_request.run_id,
            self.alignn_request.candidate_id,
        ):
            raise ValueError("all chained model identities must match")
        if self.ct_uae_request.input_structure is None:
            raise ValueError("ct-UAE chain request must consume the relaxed structure")
        if self.ct_uae_request.input_structure != self.relaxed_structure:
            raise ValueError("ct-UAE input is not the declared relaxed structure")
        if (
            self.alignn_request.input_structure.artifact_uri != self.relaxed_structure.uri
            or self.alignn_request.input_structure.sha256 != self.relaxed_structure.sha256
        ):
            raise ValueError("ALIGNN input is not the declared relaxed structure")
        if any(model.family is not PropertyModelFamily.CT_UAE for model in self.ct_uae_registry.models):
            raise ValueError("the ct-UAE chain registry may contain only ct-UAE models")
        return self


class PropertyPredictionChainResult(StrictFrozenModel):
    schema_version: Literal["agent02-post-relaxation-property-chain-v1"] = PROPERTY_CHAIN_VERSION
    project_id: str
    run_id: str
    candidate_id: str
    operation_key: str = Field(pattern=r"^[0-9a-f]{64}$")
    relaxation_method: str
    relaxed_structure: ArtifactPointer
    status: Literal["SUCCEEDED", "PARTIAL", "FAILED"]
    ct_uae: PropertyPredictionResult | None = None
    alignn: AlignnResult | None = None
    errors: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    scientific_conclusion: Literal[False] = False


@dataclass(frozen=True)
class PostRelaxationPropertyChain:
    """Run both property models automatically from one relaxed CIF."""

    artifact_store: LocalArtifactStore
    ct_uae_flow: PropertyPredictionFlowRunner
    alignn_flow: AlignnFlowRunner

    def __post_init__(self) -> None:
        root = self.artifact_store.root
        if self.ct_uae_flow.store.root != root or self.alignn_flow.store.root != root:
            raise ValueError("all chained property flows must share one artifact root")

    def execute(self, request: PropertyPredictionChainRequest) -> PropertyPredictionChainResult:
        if not self.artifact_store.exists_with_hash(
            request.relaxed_structure.uri, request.relaxed_structure.sha256
        ):
            raise ValueError("relaxed structure failed Artifact integrity validation")
        operation_key = property_chain_operation_key(request)
        result_relative = (
            f"stages/agent02/{request.run_id}/property-chain/"
            f"{operation_key}/result.json"
        )
        complete_relative = (
            f"stages/agent02/{request.run_id}/property-chain/"
            f"{operation_key}/operation-complete.json"
        )
        if self.artifact_store.exists(complete_relative):
            if not self.artifact_store.exists(result_relative):
                raise ValueError("property chain completion record lacks its result")
            result_ref = self.artifact_store.inspect(result_relative, media_type="application/json")
            completion = self.artifact_store.read_json(complete_relative)
            if completion != {
                "operation_key": operation_key,
                "result_sha256": result_ref.sha256,
            }:
                raise ValueError("property chain completion record failed integrity")
            return PropertyPredictionChainResult.model_validate(
                self.artifact_store.read_json(result_relative)
            )
        if self.artifact_store.exists(result_relative):
            raise ValueError("property chain result exists without a completion record")
        ct_result: PropertyPredictionResult | None = None
        alignn_result: AlignnResult | None = None
        errors: list[str] = []

        # The two calls use the same frozen input but retain independent plans
        # and completion ledgers.  A failure in one must not suppress the other.
        try:
            ct_result = self.ct_uae_flow.execute(request.ct_uae_request)
        except Exception as exc:  # noqa: BLE001
            errors.append(f"ct-UAE chain step failed: {type(exc).__name__}: {exc}")
        try:
            alignn_result = self.alignn_flow.execute(request.alignn_request)
        except Exception as exc:  # noqa: BLE001
            errors.append(f"ALIGNN chain step failed: {type(exc).__name__}: {exc}")

        successful = sum(
            result is not None and result.status == "SUCCEEDED"
            for result in (ct_result, alignn_result)
        )
        status = "SUCCEEDED" if successful == 2 else ("PARTIAL" if successful else "FAILED")
        result = PropertyPredictionChainResult(
            project_id=request.project_id,
            run_id=request.run_id,
            candidate_id=request.candidate_id,
            operation_key=operation_key,
            relaxation_method=request.relaxation_method,
            relaxed_structure=request.relaxed_structure,
            status=status,
            ct_uae=ct_result,
            alignn=alignn_result,
            errors=errors,
            warnings=[
                "ct-UAE and ALIGNN outputs are independent ML estimates from the same relaxed structure.",
                "The chain does not predict flat-band width, orbital character, crossings, stability, or DFT evidence.",
            ],
        )
        result_ref = self.artifact_store.write_json(
            result_relative, result.model_dump(mode="json"), immutable=True
        )
        self.artifact_store.write_json(
            complete_relative,
            {"operation_key": operation_key, "result_sha256": result_ref.sha256},
            immutable=True,
        )
        return result


def property_chain_operation_key(request: PropertyPredictionChainRequest) -> str:
    payload = json.dumps(
        request.model_dump(mode="json"), sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(
        b"agent02-property-chain-operation-v1\0" + payload
    ).hexdigest()
