from __future__ import annotations

import json
import subprocess
import sys

import pytest
from pydantic import ValidationError

from material_agent.ml_screening.models import (
    AGENT02_CONTRACT_VERSION,
    AGENT02_REQUEST_VERSION,
    AGENT02_STAGE_PLAN_VERSION,
    EvidenceLevel,
    MLPropertyValue,
    MLScreeningRequest,
    MLStagePlan,
    MLStageResultEnvelope,
    SelectionMode,
    WorkerRequest,
    WorkerResponse,
)
from material_agent.ml_screening.resources import fake_model_spec
from material_agent.orchestrator.models import StageId
from material_agent.orchestrator.runners import default_capabilities


def test_default_request_freezes_policy_top_five() -> None:
    request = MLScreeningRequest()
    assert request.schema_version == AGENT02_REQUEST_VERSION
    assert request.selection_mode is SelectionMode.POLICY_TOP_N
    assert request.requested_candidate_ids is None
    assert request.max_candidates == 5
    assert [task.value for task in request.requested_tasks] == [
        "static_prediction",
        "structure_relaxation",
    ]


@pytest.mark.parametrize(
    "payload",
    [
        {"unknown": True},
        {
            "selection_mode": "POLICY_TOP_N",
            "requested_candidate_ids": ["cand-a"],
        },
        {"selection_mode": "POLICY_TOP_N", "max_candidates": 6},
        {
            "selection_mode": "EXPLICIT_IDS",
            "requested_candidate_ids": [],
        },
        {
            "selection_mode": "EXPLICIT_IDS",
            "requested_candidate_ids": ["cand-a", "cand-a"],
        },
        {
            "selection_mode": "EXPLICIT_IDS",
            "requested_candidate_ids": ["cand-a", "cand-b"],
            "max_candidates": 1,
        },
        {"max_candidates": 21},
        {"requested_tasks": ["static_prediction"]},
    ],
)
def test_request_rejects_invalid_or_expansive_input(payload: dict) -> None:
    with pytest.raises(ValidationError):
        MLScreeningRequest.model_validate(payload)


def test_contract_models_are_frozen(ml_model) -> None:
    with pytest.raises(ValidationError):
        ml_model.package_version = "mutated"


def test_fake_property_cannot_claim_l2(ml_candidate_factory) -> None:
    candidate = ml_candidate_factory()
    model = fake_model_spec()
    with pytest.raises(ValidationError, match="mock properties cannot claim L2"):
        MLPropertyValue(
            property_name="mlip_potential_energy",
            value=-1.0,
            unit="eV/atom",
            evidence_level=EvidenceLevel.L2_ML_SCREENED,
            method="fixture",
            model_id=model.model_id,
            checkpoint_sha256=model.checkpoint_sha256,
            input_structure_id=candidate.source_structure.structure_id,
            is_mock=True,
        )


def test_versioned_models_publish_json_schema() -> None:
    assert (
        MLStagePlan.model_json_schema()["properties"]["schema_version"][
            "default"
        ]
        == AGENT02_STAGE_PLAN_VERSION
    )
    assert (
        MLStageResultEnvelope.model_json_schema()["properties"][
            "schema_version"
        ]["default"]
        == AGENT02_CONTRACT_VERSION
    )
    assert (
        WorkerRequest.model_json_schema()["properties"]["schema_version"][
            "default"
        ]
        == "agent02-worker-request-v1"
    )
    assert (
        WorkerResponse.model_json_schema()["properties"]["schema_version"][
            "default"
        ]
        == "agent02-worker-response-v1"
    )


def test_importing_agent02_does_not_import_heavy_ml_modules() -> None:
    script = (
        "import json, sys; "
        "import material_agent.ml_screening; "
        "print(json.dumps(sorted("
        "{'torch','chgnet','ase','pymatgen',"
        "'material_agent.retrieval.models'} & set(sys.modules))))"
    )
    result = subprocess.run(
        [sys.executable, "-c", script],
        check=True,
        capture_output=True,
        text=True,
    )
    assert json.loads(result.stdout) == []


def test_step_one_does_not_register_production_ml_capability() -> None:
    capability = default_capabilities()[StageId.ML]
    assert capability.registered is False
    assert capability.is_mock is False
    assert capability.unavailable_reason == (
        "Agent02 ML capability is not implemented"
    )
