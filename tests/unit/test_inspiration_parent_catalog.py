from __future__ import annotations

import hashlib
import inspect
import json
from importlib import resources

import pytest
from pydantic import ValidationError

import material_agent.inspiration.parent_catalog as parent_catalog_module
from material_agent.inspiration.parent_catalog import (
    FLAT_BAND_PARENT_CATALOG_ASSETS,
    FLAT_BAND_PARENT_CATALOG_ENTRY_COUNT,
    FLAT_BAND_PARENT_CATALOG_EXPECTED_UNIQUE_OUTPUTS,
    FLAT_BAND_PARENT_CATALOG_ID,
    ParentCatalogIntegrityError,
    ParentCatalogV1,
    load_flat_band_parent_catalog_v1,
    parent_catalog_manifest_bytes,
)

EXPECTED_MANIFEST_SHA256 = (
    "09d563732717e05ccf216d3b8572b1bcd1d855dd3f5d0106a4cdbc15b9197b99"
)
REFERENCE_ENTRY_ID = "tis2-1t-bulk-reference-v1"
CONTROL_ENTRY_ID = "tisse-1t-convergence-control-v1"


def test_package_owned_catalog_replays_six_routes_to_five_outputs() -> None:
    loaded = load_flat_band_parent_catalog_v1()

    assert loaded.manifest.catalog_id == FLAT_BAND_PARENT_CATALOG_ID
    assert loaded.manifest_sha256 == EXPECTED_MANIFEST_SHA256
    assert len(loaded.entries) == FLAT_BAND_PARENT_CATALOG_ENTRY_COUNT == 6
    assert all(entry.record.catalog_role == "ENGINEERING_CALIBRATION" for entry in loaded.entries)
    assert all(entry.record.property_status == "UNKNOWN" for entry in loaded.entries)
    assert all(entry.record.scientific_conclusion is False for entry in loaded.entries)
    assert all(entry.record.expected_dimensionality == 2 for entry in loaded.entries)
    assert all(entry.record.substitution_rule_id == "s-to-se-isovalent-v1" for entry in loaded.entries)

    records = {entry.record.entry_id: entry.record for entry in loaded.entries}
    assert len({item.expected_output_structure_id for item in records.values()}) == (
        FLAT_BAND_PARENT_CATALOG_EXPECTED_UNIQUE_OUTPUTS
    )
    reference = records[REFERENCE_ENTRY_ID]
    control = records[CONTROL_ENTRY_ID]
    assert reference.structure_id != control.structure_id
    assert reference.route_sha256 != control.route_sha256
    assert (
        reference.expected_output_structure_id
        == control.expected_output_structure_id
        == "str_b2d1869a30c3edb91d86b3e6"
    )
    assert (
        reference.expected_output_sha256
        == control.expected_output_sha256
        == "717b155fd4595965ae5b7befa187c2c4507b5255ea9afc7a0e03db6bfb5f2a57"
    )

    families_by_output: dict[str, set[str]] = {}
    for record in records.values():
        families_by_output.setdefault(record.expected_output_structure_id, set()).add(
            record.family_id
        )
    family_sets = tuple(families_by_output.values())
    assert len(family_sets) == FLAT_BAND_PARENT_CATALOG_EXPECTED_UNIQUE_OUTPUTS
    assert all(
        not left.intersection(right)
        for index, left in enumerate(family_sets)
        for right in family_sets[index + 1 :]
    )


def test_manifest_and_assets_are_installed_package_resources() -> None:
    root = resources.files("material_agent.inspiration").joinpath(
        "catalogs",
        "flat_band_parent_catalog_v1",
    )
    manifest = root.joinpath("manifest.json")
    assert manifest.is_file()
    manifest_bytes = manifest.read_bytes()
    assert hashlib.sha256(manifest_bytes).hexdigest() == EXPECTED_MANIFEST_SHA256
    parsed = ParentCatalogV1.model_validate_json(manifest_bytes)
    assert parent_catalog_manifest_bytes(parsed) == manifest_bytes

    assert tuple(
        sorted(
            asset_name
            for asset_name in FLAT_BAND_PARENT_CATALOG_ASSETS
            if root.joinpath(asset_name).is_file()
        )
    ) == FLAT_BAND_PARENT_CATALOG_ASSETS


def test_loader_accepts_no_external_path_or_payload() -> None:
    assert not inspect.signature(load_flat_band_parent_catalog_v1).parameters
    with pytest.raises(TypeError):
        load_flat_band_parent_catalog_v1("/tmp/untrusted.cif")  # type: ignore[call-arg]


def test_asset_tampering_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    real_reader = parent_catalog_module._read_package_resource

    def tampered_reader(resource_name: str) -> bytes:
        payload = real_reader(resource_name)
        if resource_name == "tis2-1t-bulk-reference.cif":
            return payload + b"# tampered\n"
        return payload

    monkeypatch.setattr(
        parent_catalog_module,
        "_read_package_resource",
        tampered_reader,
    )
    with pytest.raises(ParentCatalogIntegrityError) as raised:
        load_flat_band_parent_catalog_v1()
    assert raised.value.code == "ASSET_SIZE_MISMATCH"


def test_manifest_hash_and_canonical_bytes_fail_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    real_reader = parent_catalog_module._read_package_resource
    canonical = real_reader("manifest.json")

    def hash_tampered_reader(resource_name: str) -> bytes:
        if resource_name == "manifest.json":
            return canonical.replace(b'"catalog_version":"1"', b'"catalog_version":"2"')
        return real_reader(resource_name)

    monkeypatch.setattr(
        parent_catalog_module,
        "_read_package_resource",
        hash_tampered_reader,
    )
    with pytest.raises(ParentCatalogIntegrityError) as hash_error:
        load_flat_band_parent_catalog_v1()
    assert hash_error.value.code == "MANIFEST_HASH_MISMATCH"

    parsed = json.loads(canonical)
    noncanonical = (json.dumps(parsed, indent=2, sort_keys=True) + "\n").encode()

    def noncanonical_reader(resource_name: str) -> bytes:
        if resource_name == "manifest.json":
            return noncanonical
        return real_reader(resource_name)

    monkeypatch.setattr(
        parent_catalog_module,
        "_EXPECTED_MANIFEST_SHA256",
        hashlib.sha256(noncanonical).hexdigest(),
    )
    monkeypatch.setattr(
        parent_catalog_module,
        "_read_package_resource",
        noncanonical_reader,
    )
    with pytest.raises(ParentCatalogIntegrityError) as canonical_error:
        load_flat_band_parent_catalog_v1()
    assert canonical_error.value.code == "NONCANONICAL_MANIFEST"


def test_schema_cannot_promote_calibration_to_scientific_conclusion() -> None:
    loaded = load_flat_band_parent_catalog_v1()
    payload = loaded.manifest.model_dump(mode="json")
    payload["scientific_conclusion"] = True
    with pytest.raises(ValidationError):
        ParentCatalogV1.model_validate(payload)
