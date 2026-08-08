"""Operator-owned, SHA-pinned parent structures for the flat-band beta.

The public loader accepts no path or payload.  It reads one source-controlled
package resource, verifies its canonical manifest and every CIF byte string,
and then replays the pinned substitution operator as an engineering integrity
check.  Catalog entries are calibration inputs, not validated materials or
property claims.
"""

from __future__ import annotations

import hashlib
import json
import math
import warnings
from dataclasses import dataclass
from importlib import resources
from importlib.metadata import version as package_version
from importlib.resources.abc import Traversable
from typing import Annotated, Literal

from pydantic import Field, ValidationError, field_validator, model_validator
from pymatgen.core import Structure
from pymatgen.symmetry.analyzer import SpacegroupAnalyzer

from material_agent.inspiration.models import (
    ArtifactPointerV1,
    Identifier,
    Sha256,
    ShortText,
    StrictModel,
    SubstitutionParametersV1,
    TransformationPlanV1,
    TransformationStatus,
    ValidationStatus,
    canonical_json_bytes,
    deterministic_id,
    transformation_route_sha256,
)
from material_agent.inspiration.tag_graph import curated_flat_band_tag_graph
from material_agent.inspiration.transformations import (
    DEFAULT_SUBSTITUTION_REGISTRY_V1,
    EQUIVALENT_SITE_ANGLE_TOLERANCE,
    EQUIVALENT_SITE_SYMPREC,
    STRUCTURE_ARTIFACT_MEDIA_TYPE,
    SUBSTITUTION_OPERATOR_ID,
    SUBSTITUTION_OPERATOR_VERSION,
    SubstitutionExecutionRequestV1,
    execute_equivalent_site_substitution,
    substitution_registry_bytes,
)
from material_agent.retrieval.models import RetrievalPolicy
from material_agent.retrieval.structures import (
    StructureValidationError,
    calculate_dimensionality,
    process_structure,
)


PARENT_CATALOG_SCHEMA_VERSION = "inspiration-parent-catalog-v1"
FLAT_BAND_PARENT_CATALOG_ID = "flat-band-parent-catalog-v1"
FLAT_BAND_PARENT_CATALOG_VERSION = "1"
FLAT_BAND_PARENT_CATALOG_MANIFEST = "manifest.json"
FLAT_BAND_PARENT_CATALOG_EXPECTED_UNIQUE_OUTPUTS = 5
FLAT_BAND_PARENT_CATALOG_ENTRY_COUNT = 6

FLAT_BAND_PARENT_CATALOG_ASSETS = (
    "tis2-1h-vacuum-monolayer.cif",
    "tis2-1t-biaxial-plus3pct.cif",
    "tis2-1t-bulk-reference.cif",
    "tis2-1t-vacuum-monolayer.cif",
    "tis2-2h-two-layer-bulk.cif",
    "tisse-1t-convergence-control.cif",
)

_PACKAGE_NAME = "material_agent.inspiration"
_CATALOG_DIRECTORY = "flat_band_parent_catalog_v1"
_CATALOG_RESOURCE_ROOT: Traversable = resources.files(_PACKAGE_NAME).joinpath(
    "catalogs",
    _CATALOG_DIRECTORY,
)
_ALLOWED_RESOURCE_NAMES = frozenset(
    (FLAT_BAND_PARENT_CATALOG_MANIFEST, *FLAT_BAND_PARENT_CATALOG_ASSETS)
)

# Patched only when the reviewed canonical manifest intentionally changes.
_EXPECTED_MANIFEST_SHA256 = (
    "09d563732717e05ccf216d3b8572b1bcd1d855dd3f5d0106a4cdbc15b9197b99"
)

_CANONICAL_STRUCTURE_POLICY = RetrievalPolicy(
    canonical_float_digits=12,
    canonicalization_policy_version="canonical-structure-v1",
)
_ALLOWED_PROCESSING_FLAGS = frozenset({"CIF_ROUND_TRIP_WARNING"})
_REGISTRY_BYTES = substitution_registry_bytes(DEFAULT_SUBSTITUTION_REGISTRY_V1)
_REGISTRY_SHA256 = hashlib.sha256(_REGISTRY_BYTES).hexdigest()


class ParentCatalogIntegrityError(RuntimeError):
    """A package-owned catalog resource failed a frozen integrity check."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(f"{code}: {message}")


class ParentCatalogRuntimeV1(StrictModel):
    pymatgen_version: ShortText
    spglib_version: ShortText
    equivalence_symprec: Literal[0.001] = EQUIVALENT_SITE_SYMPREC
    equivalence_angle_tolerance: Literal[0.1] = (
        EQUIVALENT_SITE_ANGLE_TOLERANCE
    )
    canonicalization_policy_version: Literal["canonical-structure-v1"] = (
        "canonical-structure-v1"
    )
    minimum_distance_angstrom: Literal[0.5] = 0.5


class ParentCatalogArtifactV1(StrictModel):
    asset_name: Identifier
    sha256: Sha256
    size_bytes: Annotated[int, Field(ge=1, le=10_000_000)]
    media_type: Literal["chemical/x-cif"] = STRUCTURE_ARTIFACT_MEDIA_TYPE

    @field_validator("asset_name")
    @classmethod
    def validate_asset_name(cls, value: str) -> str:
        if "/" in value or "\\" in value or not value.endswith(".cif"):
            raise ValueError("catalog assets must be basename-only CIF resources")
        return value


class ParentCatalogEntryV1(StrictModel):
    entry_id: Identifier
    candidate_id: Identifier
    structure_id: Identifier
    family_id: Identifier
    description: ShortText
    catalog_role: Literal["ENGINEERING_CALIBRATION"] = "ENGINEERING_CALIBRATION"
    property_status: Literal["UNKNOWN"] = "UNKNOWN"
    scientific_conclusion: Literal[False] = False
    artifact: ParentCatalogArtifactV1
    expected_formula: ShortText
    expected_species: Annotated[
        tuple[ShortText, ...], Field(min_length=1, max_length=256)
    ]
    expected_site_count: Annotated[int, Field(ge=1, le=256)]
    expected_dimensionality: Literal[2] = 2
    expected_space_group_number: Annotated[int, Field(ge=1, le=230)]
    expected_equivalent_site_groups: Annotated[
        tuple[
            Annotated[
                tuple[Annotated[int, Field(ge=0)], ...],
                Field(min_length=1, max_length=256),
            ],
            ...,
        ],
        Field(min_length=1, max_length=256),
    ]
    selected_equivalent_site_indices: Annotated[
        tuple[Annotated[int, Field(ge=0)], ...],
        Field(min_length=1, max_length=256),
    ]
    substitution_rule_id: Identifier
    reviewed_bridge_rule_id: Identifier
    route_sha256: Sha256
    expected_output_elements: Annotated[
        tuple[ShortText, ...], Field(min_length=1, max_length=32)
    ]
    expected_output_structure_id: Identifier
    expected_output_sha256: Sha256
    expected_output_size_bytes: Annotated[int, Field(ge=1, le=10_000_000)]
    output_convergence_group_id: Identifier | None = None

    @model_validator(mode="after")
    def validate_entry_ordering(self) -> ParentCatalogEntryV1:
        groups = self.expected_equivalent_site_groups
        if tuple(sorted(groups)) != groups:
            raise ValueError("equivalent-site groups must be canonically sorted")
        seen: set[int] = set()
        for group in groups:
            if tuple(sorted(group)) != group or len(set(group)) != len(group):
                raise ValueError("equivalent-site groups must be sorted and unique")
            if seen.intersection(group):
                raise ValueError("equivalent-site groups must not overlap")
            seen.update(group)
        if seen != set(range(self.expected_site_count)):
            raise ValueError("equivalent-site groups must partition every site")
        if self.selected_equivalent_site_indices not in groups:
            raise ValueError("selected substitution indices must be a complete group")
        if (
            tuple(sorted(self.expected_output_elements))
            != self.expected_output_elements
            or len(set(self.expected_output_elements))
            != len(self.expected_output_elements)
        ):
            raise ValueError("expected output elements must be sorted and unique")
        return self


class ParentCatalogV1(StrictModel):
    schema_version: Literal["inspiration-parent-catalog-v1"] = (
        PARENT_CATALOG_SCHEMA_VERSION
    )
    catalog_id: Literal["flat-band-parent-catalog-v1"] = (
        FLAT_BAND_PARENT_CATALOG_ID
    )
    catalog_version: Literal["1"] = FLAT_BAND_PARENT_CATALOG_VERSION
    catalog_role: Literal["ENGINEERING_CALIBRATION"] = "ENGINEERING_CALIBRATION"
    property_status: Literal["UNKNOWN"] = "UNKNOWN"
    scientific_conclusion: Literal[False] = False
    operator_id: Literal["SUBSTITUTE_EQUIVALENT_SITE_V1"] = (
        SUBSTITUTION_OPERATOR_ID
    )
    operator_version: Literal["1"] = SUBSTITUTION_OPERATOR_VERSION
    substitution_registry_id: Literal["inspiration-substitution-registry-v1"] = (
        "inspiration-substitution-registry-v1"
    )
    substitution_registry_sha256: Sha256
    allowlisted_substitution_rule_ids: Annotated[
        tuple[Identifier, ...], Field(min_length=1, max_length=32)
    ]
    reviewed_bridge_rule_ids: Annotated[
        tuple[Identifier, ...], Field(min_length=1, max_length=32)
    ]
    expected_unique_output_count: Literal[5] = (
        FLAT_BAND_PARENT_CATALOG_EXPECTED_UNIQUE_OUTPUTS
    )
    runtime: ParentCatalogRuntimeV1
    entries: Annotated[
        tuple[ParentCatalogEntryV1, ...],
        Field(min_length=6, max_length=6),
    ]

    @model_validator(mode="after")
    def validate_catalog_identity(self) -> ParentCatalogV1:
        if tuple(sorted(self.allowlisted_substitution_rule_ids)) != (
            self.allowlisted_substitution_rule_ids
        ) or len(set(self.allowlisted_substitution_rule_ids)) != len(
            self.allowlisted_substitution_rule_ids
        ):
            raise ValueError("allowlisted substitution rules must be sorted and unique")
        if tuple(sorted(self.reviewed_bridge_rule_ids)) != self.reviewed_bridge_rule_ids or len(
            set(self.reviewed_bridge_rule_ids)
        ) != len(self.reviewed_bridge_rule_ids):
            raise ValueError("reviewed bridge rules must be sorted and unique")
        if tuple(sorted(self.entries, key=lambda item: item.entry_id)) != self.entries:
            raise ValueError("catalog entries must be sorted by entry ID")

        unique_fields = {
            "entry IDs": tuple(item.entry_id for item in self.entries),
            "candidate IDs": tuple(item.candidate_id for item in self.entries),
            "structure IDs": tuple(item.structure_id for item in self.entries),
            "asset names": tuple(item.artifact.asset_name for item in self.entries),
            "route hashes": tuple(item.route_sha256 for item in self.entries),
        }
        for label, values in unique_fields.items():
            if len(set(values)) != len(values):
                raise ValueError(f"catalog {label} must be unique")
        if tuple(sorted(item.artifact.asset_name for item in self.entries)) != (
            FLAT_BAND_PARENT_CATALOG_ASSETS
        ):
            raise ValueError("catalog asset set differs from the package allowlist")
        if any(
            item.substitution_rule_id
            not in self.allowlisted_substitution_rule_ids
            for item in self.entries
        ):
            raise ValueError("entry uses a substitution rule outside the allowlist")
        if any(
            item.reviewed_bridge_rule_id not in self.reviewed_bridge_rule_ids
            for item in self.entries
        ):
            raise ValueError("entry uses a bridge rule outside the reviewed set")
        return self


@dataclass(frozen=True, slots=True)
class LoadedParentCatalogEntry:
    record: ParentCatalogEntryV1
    artifact_bytes: bytes


@dataclass(frozen=True, slots=True)
class LoadedParentCatalog:
    manifest: ParentCatalogV1
    manifest_bytes: bytes
    manifest_sha256: str
    entries: tuple[LoadedParentCatalogEntry, ...]


def parent_catalog_manifest_bytes(catalog: ParentCatalogV1) -> bytes:
    """Return the one canonical UTF-8 representation accepted by the loader."""

    if not isinstance(catalog, ParentCatalogV1):
        raise TypeError("catalog must be a ParentCatalogV1")
    return canonical_json_bytes(catalog) + b"\n"


def load_flat_band_parent_catalog_v1() -> LoadedParentCatalog:
    """Load and fully verify the one package-owned flat-band parent catalog."""

    manifest_bytes = _read_package_resource(FLAT_BAND_PARENT_CATALOG_MANIFEST)
    manifest_sha256 = hashlib.sha256(manifest_bytes).hexdigest()
    if manifest_sha256 != _EXPECTED_MANIFEST_SHA256:
        raise ParentCatalogIntegrityError(
            "MANIFEST_HASH_MISMATCH",
            "package catalog manifest differs from the reviewed SHA-256",
        )
    try:
        catalog = ParentCatalogV1.model_validate_json(manifest_bytes)
    except (ValidationError, ValueError, TypeError) as error:
        raise ParentCatalogIntegrityError(
            "INVALID_MANIFEST",
            "package catalog manifest does not satisfy schema v1",
        ) from error
    if parent_catalog_manifest_bytes(catalog) != manifest_bytes:
        raise ParentCatalogIntegrityError(
            "NONCANONICAL_MANIFEST",
            "package catalog manifest is not canonical JSON plus one newline",
        )
    _validate_catalog_contract(catalog)

    loaded_entries: list[LoadedParentCatalogEntry] = []
    outputs: dict[str, tuple[str, str]] = {}
    for entry in catalog.entries:
        payload = _read_package_resource(entry.artifact.asset_name)
        _validate_asset_binding(entry, payload)
        output_structure_id, output_sha256 = _validate_structure_and_route(
            catalog,
            entry,
            payload,
        )
        outputs[entry.entry_id] = (output_structure_id, output_sha256)
        loaded_entries.append(
            LoadedParentCatalogEntry(record=entry, artifact_bytes=payload)
        )
    _validate_output_convergence(catalog, outputs)
    return LoadedParentCatalog(
        manifest=catalog,
        manifest_bytes=manifest_bytes,
        manifest_sha256=manifest_sha256,
        entries=tuple(loaded_entries),
    )


def _read_package_resource(resource_name: str) -> bytes:
    if resource_name not in _ALLOWED_RESOURCE_NAMES:
        raise ParentCatalogIntegrityError(
            "RESOURCE_NOT_ALLOWED",
            "catalog loader requested a resource outside its fixed allowlist",
        )
    resource = _CATALOG_RESOURCE_ROOT.joinpath(resource_name)
    try:
        if not resource.is_file():
            raise ParentCatalogIntegrityError(
                "RESOURCE_MISSING",
                f"package catalog resource is unavailable: {resource_name}",
            )
        payload = resource.read_bytes()
    except ParentCatalogIntegrityError:
        raise
    except (OSError, RuntimeError) as error:
        raise ParentCatalogIntegrityError(
            "RESOURCE_UNREADABLE",
            f"package catalog resource cannot be read: {resource_name}",
        ) from error
    if not isinstance(payload, bytes):
        raise ParentCatalogIntegrityError(
            "RESOURCE_UNREADABLE",
            "package resource reader returned a non-bytes value",
        )
    return payload


def _validate_catalog_contract(catalog: ParentCatalogV1) -> None:
    if catalog.substitution_registry_sha256 != _REGISTRY_SHA256:
        raise ParentCatalogIntegrityError(
            "REGISTRY_HASH_MISMATCH",
            "catalog does not bind the production substitution registry bytes",
        )
    if catalog.runtime.pymatgen_version != package_version("pymatgen") or (
        catalog.runtime.spglib_version != package_version("spglib")
    ):
        raise ParentCatalogIntegrityError(
            "RUNTIME_VERSION_MISMATCH",
            "catalog equivalence/QC runtime differs from installed pinned versions",
        )

    registry_rules = {
        rule.rule_id: rule for rule in DEFAULT_SUBSTITUTION_REGISTRY_V1.rules
    }
    if set(catalog.allowlisted_substitution_rule_ids) != {"s-to-se-isovalent-v1"}:
        raise ParentCatalogIntegrityError(
            "RULE_ALLOWLIST_MISMATCH",
            "catalog v1 must allow exactly the reviewed S-to-Se rule",
        )
    if not set(catalog.allowlisted_substitution_rule_ids).issubset(registry_rules):
        raise ParentCatalogIntegrityError(
            "UNKNOWN_SUBSTITUTION_RULE",
            "catalog substitution rule is absent from the pinned registry",
        )

    graph = curated_flat_band_tag_graph()
    graph_rules = {rule.bridge_rule_id: rule for rule in graph.bridge_rules}
    if set(catalog.reviewed_bridge_rule_ids) != set(graph_rules):
        raise ParentCatalogIntegrityError(
            "BRIDGE_ALLOWLIST_MISMATCH",
            "catalog reviewed bridge set differs from the curated TagGraph",
        )
    for bridge_rule_id in catalog.reviewed_bridge_rule_ids:
        if "electronic-flat-band" not in graph_rules[bridge_rule_id].target_tag_ids:
            raise ParentCatalogIntegrityError(
                "BRIDGE_TARGET_MISMATCH",
                "catalog bridge does not target the supported flat-band tag",
            )


def _validate_asset_binding(entry: ParentCatalogEntryV1, payload: bytes) -> None:
    if len(payload) != entry.artifact.size_bytes:
        raise ParentCatalogIntegrityError(
            "ASSET_SIZE_MISMATCH",
            f"catalog asset size differs for {entry.entry_id}",
        )
    if hashlib.sha256(payload).hexdigest() != entry.artifact.sha256:
        raise ParentCatalogIntegrityError(
            "ASSET_HASH_MISMATCH",
            f"catalog asset SHA-256 differs for {entry.entry_id}",
        )


def _parse_structure(entry: ParentCatalogEntryV1, payload: bytes) -> Structure:
    try:
        text = payload.decode("utf-8")
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            structure = Structure.from_str(text, fmt="cif")
    except (UnicodeDecodeError, ValueError, TypeError, IndexError) as error:
        raise ParentCatalogIntegrityError(
            "INVALID_CIF",
            f"catalog asset cannot be parsed for {entry.entry_id}",
        ) from error
    return structure


def _equivalent_site_groups(structure: Structure) -> tuple[tuple[int, ...], ...]:
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            groups = tuple(
                sorted(
                    tuple(sorted(int(index) for index in group))
                    for group in SpacegroupAnalyzer(
                        structure,
                        symprec=EQUIVALENT_SITE_SYMPREC,
                        angle_tolerance=EQUIVALENT_SITE_ANGLE_TOLERANCE,
                    )
                    .get_symmetrized_structure()
                    .equivalent_indices
                )
            )
    except Exception as error:
        raise ParentCatalogIntegrityError(
            "EQUIVALENCE_DISCOVERY_FAILED",
            "pymatgen/spglib could not reproduce catalog equivalence groups",
        ) from error
    if not groups or {index for group in groups for index in group} != set(
        range(len(structure))
    ):
        raise ParentCatalogIntegrityError(
            "EQUIVALENCE_DISCOVERY_FAILED",
            "catalog equivalence groups do not partition the parent structure",
        )
    return groups


def _validate_structure_and_route(
    catalog: ParentCatalogV1,
    entry: ParentCatalogEntryV1,
    payload: bytes,
) -> tuple[str, str]:
    structure = _parse_structure(entry, payload)
    if len(structure) != entry.expected_site_count:
        raise ParentCatalogIntegrityError(
            "SITE_COUNT_MISMATCH",
            f"catalog site count differs for {entry.entry_id}",
        )
    if not all(site.is_ordered for site in structure):
        raise ParentCatalogIntegrityError(
            "UNORDERED_PARENT",
            f"catalog parent is not fully ordered for {entry.entry_id}",
        )
    species = tuple(str(site.specie) for site in structure)
    oxidation_states = tuple(
        getattr(site.specie, "oxi_state", None) for site in structure
    )
    if species != entry.expected_species or any(
        state is None or not math.isfinite(float(state))
        for state in oxidation_states
    ):
        raise ParentCatalogIntegrityError(
            "OXIDATION_STATE_MISMATCH",
            f"catalog explicit species differ for {entry.entry_id}",
        )
    total_charge = math.fsum(float(state) for state in oxidation_states if state is not None)
    if not math.isclose(total_charge, 0.0, rel_tol=0.0, abs_tol=1e-8):
        raise ParentCatalogIntegrityError(
            "NONNEUTRAL_PARENT",
            f"catalog parent is not explicitly charge neutral for {entry.entry_id}",
        )
    if structure.composition.reduced_formula != entry.expected_formula:
        raise ParentCatalogIntegrityError(
            "FORMULA_MISMATCH",
            f"catalog formula differs for {entry.entry_id}",
        )

    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            processed = process_structure(
                structure,
                summary_elements=sorted(
                    str(element)
                    for element in structure.composition.element_composition
                ),
                summary_num_sites=len(structure),
                summary_formula=None,
                policy=_CANONICAL_STRUCTURE_POLICY,
            )
    except (StructureValidationError, ValueError, TypeError) as error:
        raise ParentCatalogIntegrityError(
            "STRUCTURE_PROCESSING_FAILED",
            f"canonical structure processing failed for {entry.entry_id}",
        ) from error
    unexpected_flags = set(processed.data_quality_flags) - _ALLOWED_PROCESSING_FLAGS
    if processed.structure_id != entry.structure_id or unexpected_flags:
        raise ParentCatalogIntegrityError(
            "STRUCTURE_ID_MISMATCH",
            f"canonical structure identity differs for {entry.entry_id}",
        )

    dimensionality = calculate_dimensionality(processed.structure)
    if (
        dimensionality.error is not None
        or dimensionality.value != entry.expected_dimensionality
    ):
        raise ParentCatalogIntegrityError(
            "DIMENSIONALITY_MISMATCH",
            f"catalog parent is not reproducibly 2D for {entry.entry_id}",
        )
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        space_group_number = SpacegroupAnalyzer(
            structure,
            symprec=EQUIVALENT_SITE_SYMPREC,
            angle_tolerance=EQUIVALENT_SITE_ANGLE_TOLERANCE,
        ).get_space_group_number()
    if space_group_number != entry.expected_space_group_number:
        raise ParentCatalogIntegrityError(
            "SPACE_GROUP_MISMATCH",
            f"catalog space group differs for {entry.entry_id}",
        )

    groups = _equivalent_site_groups(structure)
    if groups != entry.expected_equivalent_site_groups:
        raise ParentCatalogIntegrityError(
            "EQUIVALENCE_GROUP_MISMATCH",
            f"catalog equivalence groups differ for {entry.entry_id}",
        )
    sulfur_groups = tuple(
        group
        for group in groups
        if all(structure[index].specie.symbol == "S" for index in group)
    )
    if sulfur_groups != (entry.selected_equivalent_site_indices,):
        raise ParentCatalogIntegrityError(
            "SUBSTITUTION_GROUP_MISMATCH",
            f"catalog must expose exactly one complete sulfur class for {entry.entry_id}",
        )

    registry_rule = next(
        (
            rule
            for rule in DEFAULT_SUBSTITUTION_REGISTRY_V1.rules
            if rule.rule_id == entry.substitution_rule_id
        ),
        None,
    )
    if (
        registry_rule is None
        or registry_rule.source_element != "S"
        or registry_rule.target_element != "Se"
    ):
        raise ParentCatalogIntegrityError(
            "SUBSTITUTION_RULE_MISMATCH",
            f"catalog route does not resolve to S-to-Se for {entry.entry_id}",
        )

    parameters = SubstitutionParametersV1(
        equivalent_site_indices=entry.selected_equivalent_site_indices,
        source_species="S",
        target_species="Se",
    )
    route_sha256 = transformation_route_sha256(
        parent_structure_id=entry.structure_id,
        operator_id=catalog.operator_id,
        operator_version=catalog.operator_version,
        parameters=parameters,
    )
    if route_sha256 != entry.route_sha256:
        raise ParentCatalogIntegrityError(
            "ROUTE_HASH_MISMATCH",
            f"catalog physical route hash differs for {entry.entry_id}",
        )

    parent_pointer = ArtifactPointerV1(
        uri=(
            "artifact://catalogs/flat-band-parent-catalog-v1/"
            f"{entry.artifact.asset_name}"
        ),
        sha256=entry.artifact.sha256,
        size_bytes=entry.artifact.size_bytes,
        media_type=entry.artifact.media_type,
    )
    plan = TransformationPlanV1(
        plan_id=deterministic_id(
            "catalog-plan",
            {"entry_id": entry.entry_id, "route_sha256": route_sha256},
        ),
        parent_candidate_id=entry.candidate_id,
        parent_structure_id=entry.structure_id,
        parent_structure_artifact=parent_pointer,
        parameters=parameters,
        preserved_features=(
            "ordered lattice and fractional coordinates",
            "complete pymatgen/spglib equivalence class",
        ),
        changed_features=(
            "complete sulfur equivalence class becomes selenium",
        ),
        falsification_tests=(
            "Downstream property calculation remains required.",
        ),
        bridge_packet_ids=(
            deterministic_id(
                "catalog-bridge",
                {"bridge_rule_id": entry.reviewed_bridge_rule_id},
            ),
        ),
        route_sha256=route_sha256,
        status=TransformationStatus.PLANNED,
    )
    registry_pointer = ArtifactPointerV1(
        uri="artifact://catalogs/flat-band-parent-catalog-v1/registry.json",
        sha256=_REGISTRY_SHA256,
        size_bytes=len(_REGISTRY_BYTES),
        media_type="application/json",
    )
    request = SubstitutionExecutionRequestV1(
        plan=plan,
        registry_artifact=registry_pointer,
        equivalent_site_groups=groups,
        allowed_output_elements=entry.expected_output_elements,
        allowed_output_dimensionalities=(2,),
        max_sites=256,
        minimum_distance_angstrom=catalog.runtime.minimum_distance_angstrom,
    )
    result = execute_equivalent_site_substitution(
        request,
        parent_structure=structure,
        parent_artifact_bytes=payload,
        registry=DEFAULT_SUBSTITUTION_REGISTRY_V1,
    )
    output_pointer = result.plan.output_structure_artifact
    if (
        result.plan.status is not TransformationStatus.STRUCTURE_VALID
        or result.output_structure is None
        or result.artifact_bytes is None
        or output_pointer is None
        or any(
            check.status is not ValidationStatus.PASS
            for check in result.plan.validation_checks
        )
    ):
        raise ParentCatalogIntegrityError(
            "OPERATOR_VALIDATION_FAILED",
            f"pinned operator did not validate {entry.entry_id}",
        )
    output_elements = tuple(
        sorted(
            str(element)
            for element in result.output_structure.composition.element_composition
        )
    )
    if (
        result.plan.output_structure_id != entry.expected_output_structure_id
        or output_pointer.sha256 != entry.expected_output_sha256
        or output_pointer.size_bytes != entry.expected_output_size_bytes
        or output_elements != entry.expected_output_elements
    ):
        raise ParentCatalogIntegrityError(
            "OUTPUT_IDENTITY_MISMATCH",
            f"pinned operator output differs for {entry.entry_id}",
        )
    return result.plan.output_structure_id, output_pointer.sha256


def _validate_output_convergence(
    catalog: ParentCatalogV1,
    outputs: dict[str, tuple[str, str]],
) -> None:
    unique_structure_ids = {structure_id for structure_id, _ in outputs.values()}
    if len(unique_structure_ids) != catalog.expected_unique_output_count:
        raise ParentCatalogIntegrityError(
            "UNIQUE_OUTPUT_COUNT_MISMATCH",
            "catalog does not produce the frozen number of canonical outputs",
        )
    convergence_groups: dict[str, list[ParentCatalogEntryV1]] = {}
    for entry in catalog.entries:
        if entry.output_convergence_group_id is not None:
            convergence_groups.setdefault(entry.output_convergence_group_id, []).append(
                entry
            )
    if len(convergence_groups) != 1:
        raise ParentCatalogIntegrityError(
            "CONVERGENCE_CONTROL_MISMATCH",
            "catalog v1 requires exactly one output convergence group",
        )
    members = next(iter(convergence_groups.values()))
    if len(members) != 2:
        raise ParentCatalogIntegrityError(
            "CONVERGENCE_CONTROL_MISMATCH",
            "output convergence group must contain exactly two physical routes",
        )
    member_outputs = {outputs[item.entry_id][0] for item in members}
    member_output_hashes = {outputs[item.entry_id][1] for item in members}
    member_routes = {item.route_sha256 for item in members}
    member_parents = {item.structure_id for item in members}
    if (
        len(member_outputs) != 1
        or len(member_output_hashes) != 1
        or len(member_routes) != 2
        or len(member_parents) != 2
    ):
        raise ParentCatalogIntegrityError(
            "CONVERGENCE_CONTROL_MISMATCH",
            "control routes must be distinct and converge to one canonical CIF",
        )


__all__ = [
    "FLAT_BAND_PARENT_CATALOG_ASSETS",
    "FLAT_BAND_PARENT_CATALOG_ENTRY_COUNT",
    "FLAT_BAND_PARENT_CATALOG_EXPECTED_UNIQUE_OUTPUTS",
    "FLAT_BAND_PARENT_CATALOG_ID",
    "LoadedParentCatalog",
    "LoadedParentCatalogEntry",
    "PARENT_CATALOG_SCHEMA_VERSION",
    "ParentCatalogArtifactV1",
    "ParentCatalogEntryV1",
    "ParentCatalogIntegrityError",
    "ParentCatalogRuntimeV1",
    "ParentCatalogV1",
    "load_flat_band_parent_catalog_v1",
    "parent_catalog_manifest_bytes",
]
