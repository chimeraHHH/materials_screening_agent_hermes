from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from material_agent.many_body.models import (
    ArtifactRef,
    EffectiveModelPackage,
    EvidenceLevel,
    EvidenceScope,
    ManyBodyResultEnvelope,
    MaterialLinkageStatus,
    ModelDefinitionStatus,
    ProvenanceRecord,
    SolverValidationStatus,
    canonical_hash,
    canonical_json,
    package_content_hash,
)

ROOT = Path(__file__).parents[1] / "fixtures/contracts/agent04-v1"


def load(name: str) -> dict:
    return json.loads((ROOT / name).read_text(encoding="utf-8"))


def test_hubbard_fixtures_validate_and_round_trip() -> None:
    for name in ("one-dimensional-hubbard.json", "two-dimensional-2x2-hubbard.json"):
        package = EffectiveModelPackage.model_validate(load(name))
        assert package.fixture is True
        assert package.is_mock is True
        assert package_content_hash(package) == canonical_hash(package, exclude={"package_hash"})
        assert EffectiveModelPackage.model_validate(
            json.loads(package.model_dump_json())
        ) == package


def test_canonical_hash_is_independent_of_json_key_order() -> None:
    first = {"z": 1, "a": {"b": 2, "a": 3}}
    second = {"a": {"a": 3, "b": 2}, "z": 1}
    assert canonical_json(first) == canonical_json(second)
    assert canonical_hash(first) == canonical_hash(second)


@pytest.mark.parametrize(
    "mutate",
    [
        lambda d: d["provenance"].clear(),
        lambda d: d.update({"package_hash": "not-a-hash"}),
        lambda d: d["geometry"].update({"site_ids": ["s0", "s0"]}),
        lambda d: d["one_body"]["hopping_terms"][0].update({"value": float("nan")}),
        lambda d: d["one_body"]["hopping_terms"][0].update({"unit": ""}),
    ],
)
def test_minimal_invalid_fixtures_are_rejected(mutate) -> None:
    payload = load("one-dimensional-hubbard.json")
    mutate(payload)
    with pytest.raises((ValidationError, ValueError, TypeError)):
        EffectiveModelPackage.model_validate(payload)


def test_committed_invalid_fixture_is_rejected() -> None:
    with pytest.raises(ValidationError):
        EffectiveModelPackage.model_validate(load("invalid-missing-provenance.json"))


def test_fixture_manifest_hashes_and_safety() -> None:
    manifest = load("fixture_manifest.json")
    assert manifest["contract_version"] == "agent04-many-body-domain-v1"
    text = ""
    for item in manifest["files"]:
        path = ROOT / item["path"]
        content = path.read_bytes()
        assert hashlib.sha256(content).hexdigest() == item["sha256"]
        text += content.decode("utf-8")
    lowered = text.lower()
    for forbidden in ("mp_api_key", "traceback", "/users/", "/home/", "l4_many_body_validated", "ground_state_energy", "double_occupancy"):
        assert forbidden not in lowered


def test_artifact_uri_rejects_machine_paths_and_pickle_like_inputs() -> None:
    for uri in ("/tmp/model.json", "file:///tmp/model.json", "artifact://x/../model.json", "artifact://x/model.pkl"):
        with pytest.raises(ValidationError):
            ArtifactRef(uri=uri, sha256="a" * 64)


def test_l4_requires_real_validated_solver_and_expert_linkage() -> None:
    base = {
        "schema_version": "many-body-result/v1",
        "request_id": "request-1",
        "model_snapshot": {"uri": "artifact://models/m.json", "sha256": "a" * 64},
        "backend_id": "ed",
        "backend_version": "1.0",
        "is_mock": True,
        "fixture": True,
        "execution_status": "SUCCEEDED",
        "model_definition_status": "VALIDATED_MODEL",
        "solver_validation_status": "MOCK_ONLY",
        "material_linkage_status": "NONE",
        "evidence_scope": "SOLVER_BENCHMARK",
        "evidence_level": "L4_MANY_BODY_VALIDATED",
        "provenance": [{"provenance_id": "p", "source_type": "FIXTURE", "method": "fixture"}],
    }
    with pytest.raises(ValidationError):
        ManyBodyResultEnvelope.model_validate(base)


def test_result_can_record_independent_status_axes_without_scientific_values() -> None:
    result = ManyBodyResultEnvelope(
        request_id="request-1",
        model_snapshot={"uri": "artifact://models/m.json", "sha256": "a" * 64},
        backend_id="fixture",
        backend_version="1.0",
        is_mock=True,
        fixture=True,
        execution_status="SUCCEEDED",
        model_definition_status=ModelDefinitionStatus.VALIDATED_MODEL,
        solver_validation_status=SolverValidationStatus.MOCK_ONLY,
        material_linkage_status=MaterialLinkageStatus.NONE,
        evidence_scope=EvidenceScope.SOLVER_BENCHMARK,
        evidence_level=EvidenceLevel.L1_RETRIEVED,
        provenance=(ProvenanceRecord(provenance_id="p", source_type="FIXTURE", method="fixture"),),
    )
    assert result.model_definition_status is ModelDefinitionStatus.VALIDATED_MODEL
    assert result.solver_validation_status is SolverValidationStatus.MOCK_ONLY
    assert result.material_linkage_status is MaterialLinkageStatus.NONE
    assert result.artifacts == ()
