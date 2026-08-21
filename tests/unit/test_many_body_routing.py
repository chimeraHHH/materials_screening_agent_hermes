from __future__ import annotations

import json
from pathlib import Path

import pytest

from material_agent.many_body.evidence import assert_evidence_allowed, evidence_ceiling
from material_agent.many_body.models import (
    EffectiveModelPackage,
    EvidenceLevel,
    MaterialLinkageStatus,
    ModelDefinitionStatus,
    SolverValidationStatus,
    package_content_hash,
)
from material_agent.many_body.registry import DEFAULT_REGISTRY, build_registry
from material_agent.many_body.routing import route_model

ROOT = Path(__file__).parents[1] / "fixtures/contracts/agent04-v1"


def package(name: str = "one-dimensional-hubbard.json") -> EffectiveModelPackage:
    payload = json.loads((ROOT / name).read_text(encoding="utf-8"))
    parsed = EffectiveModelPackage.model_validate({**payload, "package_hash": "0" * 64})
    return parsed.model_copy(update={"package_hash": package_content_hash(parsed)})


def mutate_package(**changes) -> EffectiveModelPackage:
    value = package()
    data = value.model_dump(mode="json")
    for path, change in changes.items():
        target = data
        parts = path.split(".")
        for part in parts[:-1]:
            target = target[int(part)] if part.isdigit() else target[part]
        target[parts[-1]] = change
    parsed = EffectiveModelPackage.model_validate({**data, "package_hash": "0" * 64})
    return parsed.model_copy(update={"package_hash": package_content_hash(parsed)})


def test_registry_is_stable_and_separates_mock_from_planned_research_methods() -> None:
    first, second = build_registry(), build_registry()
    assert first.snapshot_hash == second.snapshot_hash
    assert first.snapshot == second.snapshot
    assert first.get("mock-many-body/v1").registered is True
    assert first.get("mock-many-body/v1").executable is True
    assert first.get("mock-many-body/v1").is_mock is True
    assert first.get("exact-diagonalization/v1-planned").registered is False
    assert first.get("exact-diagonalization/v1-planned").executable is False
    assert first.get("exact-diagonalization/v1-planned").lifecycle == "PLANNED"
    assert first.get("exact-diagonalization/v1-planned").registry_snapshot.sha256 == first.snapshot_hash
    for solver_id, method_family, catalog_id in (
        ("qmc/alf-v2.4-planned", "QMC", "qmc/alf-v2.4"),
        ("dmrg/tenpy-v1-planned", "DMRG", "dmrg/tenpy-v1"),
        ("dmft/solid-dmft-triqs4-planned", "DMFT", "dmft/solid-dmft-triqs4"),
    ):
        capability = first.get(solver_id)
        assert capability.lifecycle == "PLANNED"
        assert capability.registered is False
        assert capability.executable is False
        assert capability.method_family == method_family
        assert capability.research_catalog_id == catalog_id


def test_fixture_control_route_is_ready_but_never_claims_real_ed() -> None:
    decision = route_model(package(), "workflow_lifecycle")
    assert decision == route_model(package(), "workflow_lifecycle")
    assert decision.status == "READY"
    assert decision.recommended_solver_id == "mock-many-body/v1"
    assert decision.executable is True
    assert decision.requires_approval is True
    assert decision.control_flow_simulators == ("mock-many-body/v1",)
    assert decision.scientific_capability_matches == (
        "exact-diagonalization/v1-planned",
        "qmc/alf-v2.4-planned",
        "dmrg/tenpy-v1-planned",
    )
    assert "MOCK_CONTROL_FLOW_ONLY" in decision.reason_codes
    assert decision.registry_snapshot.sha256 == DEFAULT_REGISTRY.snapshot_hash


@pytest.mark.parametrize(
    ("changes", "reason", "path"),
    [
        ({"basis.includes_soc": True}, "SOC_UNSUPPORTED", "basis.includes_soc"),
        ({"state_points.0.temperature_definition": "KELVIN", "state_points.0.temperature": 5.0}, "FINITE_T_UNSUPPORTED", "state_points"),
        ({"model_family": "MULTI_ORBITAL_HUBBARD_KANAMORI"}, "MULTI_ORBITAL_UNSUPPORTED", "model_family"),
    ],
)
def test_unsupported_features_are_explicit_and_do_not_fallback(changes, reason, path) -> None:
    candidate = mutate_package(**changes)
    decision = route_model(candidate, "ground_state_energy")
    assert decision.status == "NOT_APPLICABLE"
    assert decision.recommended_solver_id is None
    assert not decision.executable
    assert reason in decision.reason_codes or reason in {item["reason_code"] for item in decision.capability_matches["exact-diagonalization/v1-planned"]["mismatches"]}
    assert path in decision.field_paths or path in str(decision.capability_matches)


@pytest.mark.parametrize(
    ("changes", "reason"),
    [
        ({"state_points.0.ensemble": "GRAND_CANONICAL", "state_points.0.chemical_potential": 0.0}, "ENSEMBLE_UNSUPPORTED"),
        ({"interactions": ({"schema_version": "agent04-interaction-v1", "term_id": "u0", "kind": "ONSITE_HUBBARD_U", "site_ids": ["s0"], "orbital_ids": ["o0"], "value": 4.0, "unit": "eV", "index_convention": "site-orbital-spin-v1", "density_density": True, "spin_flip": False, "pair_hopping": False, "provenance_id": "prov_fixture"}, {"schema_version": "agent04-interaction-v1", "term_id": "v0", "kind": "NONLOCAL_DENSITY_V", "site_ids": ["s0", "s1"], "orbital_ids": [], "value": 1.0, "unit": "eV", "index_convention": "site-orbital-spin-v1", "density_density": True, "spin_flip": False, "pair_hopping": False, "provenance_id": "prov_fixture"})}, "INTERACTION_TERM_UNSUPPORTED"),
    ],
)
def test_routing_rejects_ensemble_and_interaction_extensions(changes, reason) -> None:
    decision = route_model(mutate_package(**changes), "ground_state_energy")
    assert decision.status == "NOT_APPLICABLE"
    assert reason in decision.reason_codes


def test_user_preference_cannot_override_mismatch() -> None:
    request = {
        "request_id": "request",
        "model_id": package().model_id,
        "model_revision": 1,
        "model_package": {"uri": "artifact://models/model.json", "sha256": package().package_hash},
        "state_point_ids": ("sp_half_filling",),
        "approval_policy_version": "many-body-approval/v1",
        "routing_policy_version": "many-body-routing/v1",
        "is_mock": True,
    }
    from material_agent.many_body.models import ManyBodyRequest
    parsed = ManyBodyRequest(solver_preference="exact-diagonalization/v1-planned", **request)
    decision = route_model(mutate_package(**{"basis.includes_soc": True}), "ground_state_energy", request=parsed)
    assert decision.status == "NOT_APPLICABLE"
    assert decision.recommended_solver_id is None


def test_evidence_ceiling_rejects_fixture_mock_and_missing_linkage_l4() -> None:
    fixture = package()
    assert evidence_ceiling(fixture, is_mock=True, solver_validation_status=SolverValidationStatus.MOCK_ONLY, material_linkage_status=MaterialLinkageStatus.NONE) == EvidenceLevel.L1_RETRIEVED
    for is_mock, status in ((True, SolverValidationStatus.MOCK_ONLY), (False, SolverValidationStatus.NOT_RUN)):
        with pytest.raises(ValueError, match="L4_MANY_BODY_VALIDATED"):
            assert_evidence_allowed(
                fixture,
                requested_level=EvidenceLevel.L4_MANY_BODY_VALIDATED,
                is_mock=is_mock,
                solver_validation_status=status,
                material_linkage_status=MaterialLinkageStatus.NONE,
            )

    real = fixture.model_copy(update={"fixture": False, "is_mock": False})
    with pytest.raises(ValueError, match="L4_MANY_BODY_VALIDATED"):
        assert_evidence_allowed(
            real,
            requested_level=EvidenceLevel.L4_MANY_BODY_VALIDATED,
            is_mock=False,
            solver_validation_status=SolverValidationStatus.NUMERICALLY_VALIDATED,
            material_linkage_status=MaterialLinkageStatus.PARTIAL_PROVENANCE,
            model_definition_status=ModelDefinitionStatus.VALIDATED_MODEL,
        )

    with pytest.raises(ValueError, match="L4_MANY_BODY_VALIDATED"):
        assert_evidence_allowed(
            real,
            requested_level=EvidenceLevel.L4_MANY_BODY_VALIDATED,
            is_mock=False,
            solver_validation_status=SolverValidationStatus.NUMERICALLY_VALIDATED,
            material_linkage_status=MaterialLinkageStatus.EXPERT_APPROVED,
            model_definition_status=ModelDefinitionStatus.VALIDATED_MODEL,
            backend_id="exact-diagonalization",
            backend_version="1.0.0-planned",
        )
