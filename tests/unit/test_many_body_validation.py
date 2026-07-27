from __future__ import annotations

import hashlib
import json
from pathlib import Path

from material_agent.many_body.models import EffectiveModelPackage, package_content_hash
from material_agent.many_body.validation import ValidationStatus, validate_model_package


ROOT = Path(__file__).parents[1] / "fixtures/contracts/agent04-v1"


def load(name: str) -> dict:
    return json.loads((ROOT / name).read_text(encoding="utf-8"))


def valid(name: str = "one-dimensional-hubbard.json") -> dict:
    value = load(name)
    package = EffectiveModelPackage.model_validate({**value, "package_hash": "0" * 64})
    value["package_hash"] = package_content_hash(package)
    return value


def test_hubbard_fixtures_are_ready_and_features_are_stable() -> None:
    for name, sites in (("one-dimensional-hubbard.json", 2), ("two-dimensional-2x2-hubbard.json", 4)):
        result = validate_model_package(valid(name))
        assert result.status is ValidationStatus.READY
        assert result.features is not None
        assert result.features.single_band is True
        assert result.features.onsite_u is True
        assert result.features.real_hopping is True
        assert result.features.num_sites == sites


def test_missing_required_physics_is_blocked_with_paths_codes_and_remediation() -> None:
    for mutate, expected in ((lambda p: p["interactions"].clear(), "MISSING_HUBBARD_U"), (lambda p: p["state_points"][0].pop("n_up"), "MISSING_N_UP"), (lambda p: p["state_points"][0].pop("n_down"), "MISSING_N_DOWN"), (lambda p: p["geometry"].pop("boundary_condition"), "MISSING_BOUNDARY_CONDITION")):
        package = valid()
        mutate(package)
        result = validate_model_package(package)
        assert result.status is ValidationStatus.BLOCKED_MISSING_INPUT
        assert any(issue.reason_code == expected and issue.field_path and issue.remediation for issue in result.issues)


def test_invalid_indices_units_numbers_and_nonhermitian_terms_fail_closed() -> None:
    cases = []
    package = valid()
    package["one_body"]["hopping_terms"][0]["source_orbital"] = "unknown"
    cases.append(package)
    package = valid()
    package["one_body"]["hopping_terms"][0]["unit"] = "J"
    cases.append(package)
    package = valid()
    package["one_body"]["stores_hermitian_conjugate"] = True
    cases.append(package)
    package = valid()
    package["interactions"][0]["value"] = float("inf")
    cases.append(package)
    for candidate in cases:
        result = validate_model_package(candidate)
        assert result.status is ValidationStatus.PERMANENT_FAILED
        assert result.issues


def test_artifact_hash_is_verified_read_only(tmp_path: Path) -> None:
    content = b"deterministic artifact\n"
    artifact = tmp_path / "models" / "parameters.json"
    artifact.parent.mkdir()
    artifact.write_bytes(content)
    package = valid()
    digest = hashlib.sha256(content).hexdigest()
    package["artifacts"] = [{"uri": "artifact://models/parameters.json", "sha256": digest}]
    parsed = EffectiveModelPackage.model_validate({**package, "package_hash": "0" * 64})
    package["package_hash"] = package_content_hash(parsed)
    assert validate_model_package(package, artifact_root=tmp_path).status is ValidationStatus.READY
    artifact.write_bytes(b"corrupted\n")
    result = validate_model_package(package, artifact_root=tmp_path)
    assert result.status is ValidationStatus.PERMANENT_FAILED
    assert any(issue.reason_code == "ARTIFACT_HASH_MISMATCH" for issue in result.issues)


def test_schema_valid_but_unsupported_features_are_not_applicable() -> None:
    for mutate, code in ((lambda p: p["basis"].update({"includes_soc": True}), "SOC_UNSUPPORTED"), (lambda p: p["one_body"].update({"is_complex": True}), "COMPLEX_HOPPING_UNSUPPORTED"), (lambda p: p["state_points"][0].update({"temperature_definition": "KELVIN", "temperature": 5.0}), "FINITE_T_UNSUPPORTED"), (lambda p: p.update({"model_family": "MULTI_ORBITAL_HUBBARD_KANAMORI"}), "MULTI_ORBITAL_UNSUPPORTED"), (lambda p: p["interactions"].append({"schema_version": "agent04-interaction-v1", "term_id": "v0", "kind": "NONLOCAL_DENSITY_V", "site_ids": ["s0", "s1"], "orbital_ids": [], "value": 1.0, "unit": "eV", "index_convention": "site-orbital-spin-v1", "density_density": True, "spin_flip": False, "pair_hopping": False, "provenance_id": "prov_fixture"}), "NONLOCAL_INTERACTION_UNSUPPORTED")):
        package = valid()
        mutate(package)
        if code != "COMPLEX_HOPPING_UNSUPPORTED":
            parsed = EffectiveModelPackage.model_validate({**package, "package_hash": "0" * 64})
            package["package_hash"] = package_content_hash(parsed)
        result = validate_model_package(package)
        assert result.status is ValidationStatus.NOT_APPLICABLE
        assert any(issue.reason_code == code for issue in result.issues)


def test_result_is_reproducible_and_has_no_control_plane_side_effects() -> None:
    package = valid()
    first = validate_model_package(package)
    second = validate_model_package(package)
    assert first == second
    assert not (ROOT / "approvals").exists()
    assert not (ROOT / "operations").exists()
    assert not (ROOT / "backend_jobs").exists()
