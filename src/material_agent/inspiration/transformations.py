"""Constrained, replayable structure substitutions for inspiration runs.

The only executable operation is ``SUBSTITUTE_EQUIVALENT_SITE_V1``.  It
replaces a caller-supplied complete equivalence class and never accepts free
coordinates, code, or an unregistered species pair.  Results remain structure
proposals; this module makes no property or discovery claim.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import warnings
from dataclasses import dataclass
from typing import Annotated, Literal

from pydantic import Field, field_validator, model_validator
from pymatgen.analysis.structure_matcher import SpeciesComparator, StructureMatcher
from pymatgen.core import Element, Lattice, Species, Structure
from pymatgen.io.cif import CifWriter
from pymatgen.symmetry.analyzer import SpacegroupAnalyzer

from material_agent.inspiration.models import (
    ArtifactPointerV1,
    Identifier,
    StrictModel,
    TransformationPlanV1,
    TransformationStatus,
    ValidationCheckV1,
    ValidationStatus,
    canonical_json_bytes,
)
from material_agent.retrieval.models import RetrievalPolicy
from material_agent.retrieval.structures import (
    StructureValidationError,
    calculate_dimensionality,
    process_structure,
)


SUBSTITUTION_OPERATOR_ID = "SUBSTITUTE_EQUIVALENT_SITE_V1"
SUBSTITUTION_OPERATOR_VERSION = "1"
SUBSTITUTION_REGISTRY_SCHEMA_VERSION = "inspiration-substitution-registry-v1"
SUBSTITUTION_EXECUTION_SCHEMA_VERSION = "inspiration-substitution-execution-v1"
STRUCTURE_ARTIFACT_MEDIA_TYPE = "chemical/x-cif"
EQUIVALENT_SITE_SYMPREC = 1e-3
EQUIVALENT_SITE_ANGLE_TOLERANCE = 0.1

_CANONICAL_STRUCTURE_POLICY = RetrievalPolicy(
    canonical_float_digits=12,
    canonicalization_policy_version="canonical-structure-v1",
)

_SAFE_ELEMENT = re.compile(r"^[A-Z][a-z]?$", flags=re.ASCII)


class TransformationIntegrityError(ValueError):
    """Input/hash/provenance failure that must not become a scientific result."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(f"{code}: {message}")


class SubstitutionRuleV1(StrictModel):
    rule_id: Identifier
    source_element: Annotated[str, Field(min_length=1, max_length=3)]
    target_element: Annotated[str, Field(min_length=1, max_length=3)]
    allowed_oxidation_states: tuple[
        Annotated[float, Field(ge=-8.0, le=8.0, allow_inf_nan=False)], ...
    ] = ()
    preserve_explicit_oxidation_state: Literal[True] = True

    @field_validator("source_element", "target_element")
    @classmethod
    def validate_element(cls, value: str) -> str:
        if not _SAFE_ELEMENT.fullmatch(value):
            raise ValueError("substitution species must be bare element symbols")
        try:
            canonical = str(Element(value))
        except ValueError as error:
            raise ValueError("substitution species is not a chemical element") from error
        if canonical != value:
            raise ValueError("substitution element symbols must be canonical")
        return value

    @model_validator(mode="after")
    def validate_rule(self) -> SubstitutionRuleV1:
        if self.source_element == self.target_element:
            raise ValueError("substitution rule source and target must differ")
        if (
            tuple(sorted(self.allowed_oxidation_states))
            != self.allowed_oxidation_states
            or len(set(self.allowed_oxidation_states))
            != len(self.allowed_oxidation_states)
        ):
            raise ValueError("allowed oxidation states must be sorted and unique")
        return self


class SubstitutionRegistryV1(StrictModel):
    schema_version: Literal["inspiration-substitution-registry-v1"] = (
        SUBSTITUTION_REGISTRY_SCHEMA_VERSION
    )
    registry_id: Literal["inspiration-substitution-registry-v1"] = (
        "inspiration-substitution-registry-v1"
    )
    registry_version: Literal["1"] = "1"
    operator_id: Literal["SUBSTITUTE_EQUIVALENT_SITE_V1"] = (
        SUBSTITUTION_OPERATOR_ID
    )
    operator_version: Literal["1"] = SUBSTITUTION_OPERATOR_VERSION
    rules: Annotated[tuple[SubstitutionRuleV1, ...], Field(min_length=1, max_length=128)]

    @model_validator(mode="after")
    def validate_rules(self) -> SubstitutionRegistryV1:
        rule_ids = tuple(rule.rule_id for rule in self.rules)
        pairs = tuple(
            (rule.source_element, rule.target_element) for rule in self.rules
        )
        if tuple(sorted(rule_ids)) != rule_ids or len(set(rule_ids)) != len(rule_ids):
            raise ValueError("substitution rules must be sorted by unique rule ID")
        if len(set(pairs)) != len(pairs):
            raise ValueError("substitution source-target pairs must be unique")
        return self


DEFAULT_SUBSTITUTION_REGISTRY_V1 = SubstitutionRegistryV1(
    rules=(
        SubstitutionRuleV1(
            rule_id="s-to-se-isovalent-v1",
            source_element="S",
            target_element="Se",
            allowed_oxidation_states=(-2.0,),
        ),
        SubstitutionRuleV1(
            rule_id="se-to-s-isovalent-v1",
            source_element="Se",
            target_element="S",
            allowed_oxidation_states=(-2.0,),
        ),
    )
)


def substitution_registry_bytes(registry: SubstitutionRegistryV1) -> bytes:
    """Return the canonical bytes that a registry artifact must contain."""

    if not isinstance(registry, SubstitutionRegistryV1):
        raise TransformationIntegrityError(
            "INVALID_REGISTRY",
            "registry must be a SubstitutionRegistryV1",
        )
    return canonical_json_bytes(registry)


def substitution_registry_sha256(registry: SubstitutionRegistryV1) -> str:
    """Hash the exact canonical registry bytes used by execution."""

    return hashlib.sha256(substitution_registry_bytes(registry)).hexdigest()


class SubstitutionExecutionRequestV1(StrictModel):
    schema_version: Literal["inspiration-substitution-execution-v1"] = (
        SUBSTITUTION_EXECUTION_SCHEMA_VERSION
    )
    plan: TransformationPlanV1
    registry_artifact: ArtifactPointerV1
    equivalent_site_groups: Annotated[
        tuple[
            Annotated[
                tuple[Annotated[int, Field(ge=0)], ...],
                Field(min_length=1, max_length=64),
            ],
            ...,
        ],
        Field(min_length=1, max_length=512),
    ]
    allowed_output_elements: Annotated[
        tuple[Annotated[str, Field(min_length=1, max_length=3)], ...],
        Field(min_length=1, max_length=64),
    ]
    allowed_output_dimensionalities: Annotated[
        tuple[Annotated[int, Field(ge=0, le=3)], ...],
        Field(min_length=1, max_length=4),
    ] = (0, 1, 2, 3)
    max_sites: Annotated[int, Field(ge=1, le=2_000)] = 256
    minimum_distance_angstrom: Annotated[
        float, Field(gt=0.0, le=10.0, allow_inf_nan=False)
    ] = 0.5

    @field_validator("allowed_output_elements")
    @classmethod
    def validate_allowed_elements(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        for value in values:
            if not _SAFE_ELEMENT.fullmatch(value):
                raise ValueError("allowed output elements must be canonical symbols")
            try:
                canonical = str(Element(value))
            except ValueError as error:
                raise ValueError("allowed output element is invalid") from error
            if canonical != value:
                raise ValueError("allowed output elements must be canonical symbols")
        if tuple(sorted(values)) != values or len(set(values)) != len(values):
            raise ValueError("allowed output elements must be sorted and unique")
        return values

    @model_validator(mode="after")
    def validate_execution_request(self) -> SubstitutionExecutionRequestV1:
        if self.plan.status is not TransformationStatus.PLANNED:
            raise ValueError("only a PLANNED transformation may be executed")
        seen_indices: set[int] = set()
        for group in self.equivalent_site_groups:
            if tuple(sorted(group)) != group or len(set(group)) != len(group):
                raise ValueError("each equivalent-site group must be sorted and unique")
            if seen_indices.intersection(group):
                raise ValueError("equivalent-site groups must not overlap")
            seen_indices.update(group)
        if tuple(sorted(self.equivalent_site_groups)) != self.equivalent_site_groups:
            raise ValueError("equivalent-site groups must be canonically sorted")
        if (
            tuple(sorted(self.allowed_output_dimensionalities))
            != self.allowed_output_dimensionalities
            or len(set(self.allowed_output_dimensionalities))
            != len(self.allowed_output_dimensionalities)
        ):
            raise ValueError("allowed dimensionalities must be sorted and unique")
        return self


@dataclass(frozen=True, slots=True)
class TransformationExecutionResult:
    """A structured plan result plus bytes; never an inspiration candidate."""

    plan: TransformationPlanV1
    output_structure: Structure | None
    artifact_bytes: bytes | None

    def __post_init__(self) -> None:
        has_output = self.output_structure is not None and self.artifact_bytes is not None
        if self.plan.status is TransformationStatus.REJECTED:
            if self.output_structure is not None or self.artifact_bytes is not None:
                raise TransformationIntegrityError(
                    "INVALID_EXECUTION_RESULT",
                    "rejected transformations cannot expose output structure bytes",
                )
        elif not has_output:
            raise TransformationIntegrityError(
                "INVALID_EXECUTION_RESULT",
                "review or valid transformations require structure bytes",
            )
        if has_output:
            pointer = self.plan.output_structure_artifact
            if pointer is None:
                raise TransformationIntegrityError(
                    "INVALID_EXECUTION_RESULT",
                    "output bytes require an artifact pointer",
                )
            digest = hashlib.sha256(self.artifact_bytes or b"").hexdigest()
            if pointer.sha256 != digest:
                raise TransformationIntegrityError(
                    "INVALID_EXECUTION_RESULT",
                    "output pointer hash does not match artifact bytes",
                )
            if pointer.size_bytes != len(self.artifact_bytes or b""):
                raise TransformationIntegrityError(
                    "INVALID_EXECUTION_RESULT",
                    "output pointer size does not match artifact bytes",
                )
            if pointer.media_type != STRUCTURE_ARTIFACT_MEDIA_TYPE:
                raise TransformationIntegrityError(
                    "INVALID_EXECUTION_RESULT",
                    "output pointer does not identify a CIF artifact",
                )
            if not _round_trip_matches(
                self.artifact_bytes or b"",
                self.output_structure,
            ):
                raise TransformationIntegrityError(
                    "INVALID_EXECUTION_RESULT",
                    "output Structure does not match artifact bytes",
                )

    @property
    def candidate_selection_eligible(self) -> bool:
        """Only fully validated, charge-resolved outputs may enter identity."""

        return self.plan.status is TransformationStatus.STRUCTURE_VALID


def _check(
    check_id: str,
    status: ValidationStatus,
    detail: str,
) -> ValidationCheckV1:
    return ValidationCheckV1(check_id=check_id, status=status, detail=detail)


def _result_plan(
    source: TransformationPlanV1,
    *,
    status: TransformationStatus,
    checks: tuple[ValidationCheckV1, ...],
    output_structure_id: str | None = None,
    output_structure_artifact: ArtifactPointerV1 | None = None,
) -> TransformationPlanV1:
    return TransformationPlanV1(
        plan_id=source.plan_id,
        parent_candidate_id=source.parent_candidate_id,
        parent_structure_id=source.parent_structure_id,
        parent_structure_artifact=source.parent_structure_artifact,
        operator_id=source.operator_id,
        operator_version=source.operator_version,
        parameters=source.parameters,
        preserved_features=source.preserved_features,
        changed_features=source.changed_features,
        falsification_tests=source.falsification_tests,
        bridge_packet_ids=source.bridge_packet_ids,
        route_sha256=source.route_sha256,
        status=status,
        validation_checks=checks,
        output_structure_id=output_structure_id,
        output_structure_artifact=output_structure_artifact,
    )


def _rejected(
    source: TransformationPlanV1,
    checks: list[ValidationCheckV1],
) -> TransformationExecutionResult:
    return TransformationExecutionResult(
        plan=_result_plan(
            source,
            status=TransformationStatus.REJECTED,
            checks=tuple(checks),
        ),
        output_structure=None,
        artifact_bytes=None,
    )


def _strict_match(first: Structure, second: Structure) -> bool:
    if len(first) != len(second):
        return False
    matcher = StructureMatcher(
        ltol=1e-6,
        stol=1e-5,
        angle_tol=1e-5,
        primitive_cell=False,
        scale=False,
        attempt_supercell=False,
        allow_subset=False,
        comparator=SpeciesComparator(),
    )
    try:
        return bool(matcher.fit(first, second))
    except Exception:
        return False


def _species_composition(structure: Structure) -> dict[str, float]:
    return {
        str(species): float(amount)
        for species, amount in structure.composition.items()
    }


def _validate_parent_artifact(
    plan: TransformationPlanV1,
    *,
    parent_structure: Structure,
    parent_artifact_bytes: bytes,
) -> Structure:
    if not isinstance(parent_structure, Structure):
        raise TransformationIntegrityError(
            "INVALID_PARENT_STRUCTURE",
            "parent_structure must be a pymatgen Structure",
        )
    if not isinstance(parent_artifact_bytes, bytes):
        raise TransformationIntegrityError(
            "INVALID_PARENT_ARTIFACT",
            "parent artifact must be bytes",
        )
    pointer = plan.parent_structure_artifact
    digest = hashlib.sha256(parent_artifact_bytes).hexdigest()
    if digest != pointer.sha256:
        raise TransformationIntegrityError(
            "PARENT_HASH_MISMATCH",
            "parent structure bytes do not match the planned artifact SHA-256",
        )
    if pointer.size_bytes is not None and pointer.size_bytes != len(parent_artifact_bytes):
        raise TransformationIntegrityError(
            "PARENT_SIZE_MISMATCH",
            "parent structure bytes do not match the planned artifact size",
        )
    if pointer.media_type not in {None, STRUCTURE_ARTIFACT_MEDIA_TYPE}:
        raise TransformationIntegrityError(
            "PARENT_MEDIA_TYPE_MISMATCH",
            "the constrained operator accepts only CIF parent artifacts",
        )
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            restored = Structure.from_str(
                parent_artifact_bytes.decode("utf-8"),
                fmt="cif",
            )
    except Exception as error:
        raise TransformationIntegrityError(
            "PARENT_ARTIFACT_INVALID",
            f"parent CIF cannot be parsed: {type(error).__name__}",
        ) from error
    if (
        _species_composition(restored) != _species_composition(parent_structure)
        or not _strict_match(parent_structure, restored)
    ):
        raise TransformationIntegrityError(
            "PARENT_STRUCTURE_MISMATCH",
            "parent Structure does not match its hash-verified CIF artifact",
        )
    return restored


def _validate_registry_artifact(
    request: SubstitutionExecutionRequestV1,
    registry: SubstitutionRegistryV1,
) -> None:
    registry_bytes = substitution_registry_bytes(registry)
    pointer = request.registry_artifact
    digest = hashlib.sha256(registry_bytes).hexdigest()
    if digest != pointer.sha256:
        raise TransformationIntegrityError(
            "REGISTRY_HASH_MISMATCH",
            "registry model does not match its artifact SHA-256",
        )
    if pointer.size_bytes is not None and pointer.size_bytes != len(registry_bytes):
        raise TransformationIntegrityError(
            "REGISTRY_SIZE_MISMATCH",
            "registry model does not match its artifact size",
        )
    if pointer.media_type not in {None, "application/json"}:
        raise TransformationIntegrityError(
            "REGISTRY_MEDIA_TYPE_MISMATCH",
            "substitution registry artifacts must be JSON",
        )


def _all_sites_ordered(structure: Structure) -> bool:
    return all(
        site.is_ordered
        and math.isclose(
            math.fsum(float(amount) for amount in site.species.values()),
            1.0,
            rel_tol=0.0,
            abs_tol=1e-8,
        )
        for site in structure
    )


def _finite_structure(structure: Structure) -> bool:
    return all(
        math.isfinite(float(value))
        for row in structure.lattice.matrix
        for value in row
    ) and all(
        math.isfinite(float(value))
        for site in structure
        for value in site.frac_coords
    )


def _minimum_periodic_distance(structure: Structure) -> float | None:
    if len(structure) < 2:
        return None
    matrix = structure.distance_matrix
    return min(
        float(matrix[left, right])
        for left in range(len(structure))
        for right in range(left + 1, len(structure))
    )


def _canonicalize_structure(structure: Structure) -> Structure:
    sites: list[tuple[str, object, tuple[float, float, float]]] = []
    for site in structure:
        normalized_species = {
            species: float(amount) for species, amount in site.species.items()
        }
        species_key = json.dumps(
            {str(species): amount for species, amount in normalized_species.items()},
            sort_keys=True,
            separators=(",", ":"),
        )
        coordinates = tuple(float(value) % 1.0 for value in site.frac_coords)
        # CIF parsing and in-memory replacement can represent unit occupancy as
        # either integer ``1`` or float ``1.0``.  Normalize every occupancy here
        # so identical canonical structures always serialize to identical bytes,
        # regardless of which parent route supplied an already-present species.
        sites.append((species_key, normalized_species, coordinates))
    sites.sort(
        key=lambda item: (
            item[0],
            round(item[2][0], 12),
            round(item[2][1], 12),
            round(item[2][2], 12),
        )
    )
    return Structure(
        Lattice(structure.lattice.matrix.copy()),
        [item[1] for item in sites],
        [item[2] for item in sites],
        coords_are_cartesian=False,
        to_unit_cell=True,
    )


def _serialize_cif(structure: Structure) -> bytes:
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        text = str(
            CifWriter(
                structure,
                symprec=None,
                write_magmoms=False,
                significant_figures=12,
            )
        )
    text = text.replace("\r\n", "\n")
    if not text.endswith("\n"):
        text += "\n"
    return text.encode("utf-8")


def _round_trip_matches(artifact_bytes: bytes, expected: Structure) -> bool:
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            restored = Structure.from_str(artifact_bytes.decode("utf-8"), fmt="cif")
    except Exception:
        return False
    if _species_composition(restored) != _species_composition(expected):
        return False
    for restored_value, expected_value in zip(
        (*restored.lattice.abc, *restored.lattice.angles),
        (*expected.lattice.abc, *expected.lattice.angles),
        strict=True,
    ):
        if not math.isclose(
            float(restored_value),
            float(expected_value),
            rel_tol=1e-9,
            abs_tol=1e-7,
        ):
            return False
    return _strict_match(expected, restored)


def _charge_target_species(
    source_species: tuple[Element | Species, ...],
    *,
    rule: SubstitutionRuleV1,
) -> tuple[ValidationStatus, str, tuple[Element | Species, ...] | None]:
    oxidation_states = tuple(
        getattr(species, "oxi_state", None) for species in source_species
    )
    if all(value is None for value in oxidation_states):
        targets = tuple(Element(rule.target_element) for _ in source_species)
        return (
            ValidationStatus.UNKNOWN,
            "Parent sites have no explicit oxidation states; the substitution "
            "requires human or downstream charge review.",
            targets,
        )
    if any(value is None for value in oxidation_states):
        return (
            ValidationStatus.FAIL,
            "Equivalent source sites mix explicit and unknown oxidation states.",
            None,
        )
    numeric_states = tuple(float(value) for value in oxidation_states if value is not None)
    if len(set(numeric_states)) != 1:
        return (
            ValidationStatus.FAIL,
            "Equivalent source sites have inconsistent explicit oxidation states.",
            None,
        )
    oxidation_state = numeric_states[0]
    if not any(
        math.isclose(
            oxidation_state,
            allowed,
            rel_tol=0.0,
            abs_tol=1e-12,
        )
        for allowed in rule.allowed_oxidation_states
    ):
        return (
            ValidationStatus.FAIL,
            "The explicit source oxidation state is not allowed by the registry rule.",
            None,
        )
    targets = tuple(
        Species(rule.target_element, oxidation_state) for _ in source_species
    )
    return (
        ValidationStatus.PASS,
        "Explicit source oxidation state is preserved by the pinned registry rule.",
        targets,
    )


def _whole_structure_charge_status(
    structure: Structure,
) -> tuple[ValidationStatus, str]:
    oxidation_states = tuple(
        getattr(site.specie, "oxi_state", None) for site in structure
    )
    if any(value is None for value in oxidation_states):
        return (
            ValidationStatus.UNKNOWN,
            "At least one output site has no explicit oxidation state; total "
            "charge cannot be verified.",
        )
    total_charge = math.fsum(float(value) for value in oxidation_states)
    if not math.isfinite(total_charge) or not math.isclose(
        total_charge,
        0.0,
        rel_tol=0.0,
        abs_tol=1e-8,
    ):
        return (
            ValidationStatus.FAIL,
            f"Explicit output oxidation states give non-neutral charge {total_charge:.12g}.",
        )
    return (
        ValidationStatus.PASS,
        "All output oxidation states are explicit and their total charge is neutral.",
    )


def _computed_equivalent_site_groups(
    structure: Structure,
) -> tuple[tuple[int, ...], ...] | None:
    try:
        with warnings.catch_warnings():
            # Pymatgen/spglib currently emit deprecation warnings from their
            # compatibility layer; those must not turn a frozen scientific
            # validation into environment-dependent failure under ``-W error``.
            warnings.simplefilter("ignore")
            analyzer = SpacegroupAnalyzer(
                structure,
                symprec=EQUIVALENT_SITE_SYMPREC,
                angle_tolerance=EQUIVALENT_SITE_ANGLE_TOLERANCE,
            )
            groups = tuple(
                sorted(
                    tuple(sorted(int(index) for index in group))
                    for group in analyzer.get_symmetrized_structure().equivalent_indices
                )
            )
    except Exception:
        return None
    if not groups or {index for group in groups for index in group} != set(
        range(len(structure))
    ):
        return None
    return groups


def _element_composition(structure: Structure) -> dict[str, float]:
    return {
        str(element): float(amount)
        for element, amount in structure.composition.element_composition.items()
    }


def _compositions_close(left: dict[str, float], right: dict[str, float]) -> bool:
    elements = set(left) | set(right)
    return all(
        math.isclose(
            left.get(element, 0.0),
            right.get(element, 0.0),
            rel_tol=0.0,
            abs_tol=1e-8,
        )
        for element in elements
    )


def execute_equivalent_site_substitution(
    request: SubstitutionExecutionRequestV1,
    *,
    parent_structure: Structure,
    parent_artifact_bytes: bytes,
    registry: SubstitutionRegistryV1,
) -> TransformationExecutionResult:
    """Execute the pinned substitution operator and return an auditable result."""

    if not isinstance(request, SubstitutionExecutionRequestV1):
        raise TransformationIntegrityError(
            "INVALID_EXECUTION_REQUEST",
            "request must be a SubstitutionExecutionRequestV1",
        )
    if not isinstance(registry, SubstitutionRegistryV1):
        raise TransformationIntegrityError(
            "INVALID_REGISTRY",
            "registry must be a SubstitutionRegistryV1",
        )
    _validate_registry_artifact(request, registry)
    verified_parent = _validate_parent_artifact(
        request.plan,
        parent_structure=parent_structure,
        parent_artifact_bytes=parent_artifact_bytes,
    )
    # The parsed, hash-verified artifact is the execution source of truth.  The
    # caller-provided object is only a cross-check and cannot perturb replay.
    parent_structure = verified_parent

    plan = request.plan
    parameters = plan.parameters
    checks: list[ValidationCheckV1] = [
        _check(
            "parent_hash_verified",
            ValidationStatus.PASS,
            "Parent Structure matches its hash-verified CIF artifact.",
        )
    ]

    operator_allowed = (
        plan.operator_id == registry.operator_id
        and plan.operator_version == registry.operator_version
        and plan.operator_id == SUBSTITUTION_OPERATOR_ID
        and plan.operator_version == SUBSTITUTION_OPERATOR_VERSION
    )
    checks.append(
        _check(
            "operator_allowed",
            ValidationStatus.PASS if operator_allowed else ValidationStatus.FAIL,
            "Operator ID/version matches the pinned substitution registry."
            if operator_allowed
            else "Operator ID/version is not allowed by the pinned registry.",
        )
    )
    if not operator_allowed:
        return _rejected(plan, checks)

    try:
        source_element = str(Element(parameters.source_species))
        target_element = str(Element(parameters.target_species))
    except ValueError:
        checks.append(
            _check(
                "target_species_valid",
                ValidationStatus.FAIL,
                "Route species are not canonical bare element symbols.",
            )
        )
        return _rejected(plan, checks)
    if (
        source_element != parameters.source_species
        or target_element != parameters.target_species
    ):
        checks.append(
            _check(
                "target_species_valid",
                ValidationStatus.FAIL,
                "Route species must use canonical bare element symbols.",
            )
        )
        return _rejected(plan, checks)

    rule = next(
        (
            item
            for item in registry.rules
            if item.source_element == source_element
            and item.target_element == target_element
        ),
        None,
    )
    checks.append(
        _check(
            "target_species_valid",
            ValidationStatus.PASS if rule is not None else ValidationStatus.FAIL,
            "Source-target pair is present in the pinned substitution registry."
            if rule is not None
            else "Source-target pair is not present in the pinned registry.",
        )
    )
    if rule is None:
        return _rejected(plan, checks)

    selected_indices = parameters.equivalent_site_indices
    computed_equivalence_groups = _computed_equivalent_site_groups(parent_structure)
    complete_equivalence_group = (
        computed_equivalence_groups is not None
        and request.equivalent_site_groups == computed_equivalence_groups
        and selected_indices in computed_equivalence_groups
    )
    in_bounds = all(index < len(parent_structure) for index in selected_indices)
    checks.append(
        _check(
            "equivalent_sites_complete",
            ValidationStatus.PASS
            if complete_equivalence_group and in_bounds
            else ValidationStatus.FAIL,
            "Route indices and the supplied partition exactly match the pinned "
            "pymatgen/spglib equivalence groups."
            if complete_equivalence_group and in_bounds
            else "Route indices or the supplied partition do not match the "
            "pinned pymatgen/spglib equivalence groups.",
        )
    )
    if not complete_equivalence_group or not in_bounds:
        return _rejected(plan, checks)

    ordered_parent = _all_sites_ordered(parent_structure)
    checks.append(
        _check(
            "ordered_occupancy",
            ValidationStatus.PASS if ordered_parent else ValidationStatus.FAIL,
            "Parent and selected sites have unit ordered occupancy."
            if ordered_parent
            else "The parent contains a disordered or invalid-occupancy site.",
        )
    )
    if not ordered_parent:
        return _rejected(plan, checks)

    selected_source_species = tuple(
        parent_structure[index].specie for index in selected_indices
    )
    source_matches = all(
        species.symbol == source_element for species in selected_source_species
    )
    checks.append(
        _check(
            "source_species_present",
            ValidationStatus.PASS if source_matches else ValidationStatus.FAIL,
            "Every site in the complete equivalence group has the route source species."
            if source_matches
            else "At least one selected site does not have the route source species.",
        )
    )
    if not source_matches:
        return _rejected(plan, checks)

    substitution_charge_status, substitution_charge_detail, target_species = (
        _charge_target_species(
        selected_source_species,
        rule=rule,
        )
    )
    if (
        substitution_charge_status is ValidationStatus.FAIL
        or target_species is None
    ):
        checks.append(
            _check(
                "charge_or_oxidation",
                substitution_charge_status,
                substitution_charge_detail,
            )
        )
        return _rejected(plan, checks)

    output = parent_structure.copy()
    for index, species in zip(selected_indices, target_species, strict=True):
        output.replace(index, species)

    total_charge_status, total_charge_detail = _whole_structure_charge_status(output)
    if total_charge_status is ValidationStatus.FAIL:
        checks.append(
            _check(
                "charge_or_oxidation",
                ValidationStatus.FAIL,
                f"{substitution_charge_detail} {total_charge_detail}",
            )
        )
        return _rejected(plan, checks)
    charge_status = (
        ValidationStatus.PASS
        if substitution_charge_status is ValidationStatus.PASS
        and total_charge_status is ValidationStatus.PASS
        else ValidationStatus.UNKNOWN
    )
    charge_detail = f"{substitution_charge_detail} {total_charge_detail}"

    site_count_preserved = len(output) == len(parent_structure)
    checks.append(
        _check(
            "site_count_preserved",
            ValidationStatus.PASS if site_count_preserved else ValidationStatus.FAIL,
            "Substitution preserved the parent site count."
            if site_count_preserved
            else "Substitution changed the parent site count.",
        )
    )
    if not site_count_preserved:
        return _rejected(plan, checks)

    lattice_preserved = all(
        float(before) == float(after)
        for before_row, after_row in zip(
            parent_structure.lattice.matrix,
            output.lattice.matrix,
            strict=True,
        )
        for before, after in zip(before_row, after_row, strict=True)
    )
    checks.append(
        _check(
            "lattice_preserved",
            ValidationStatus.PASS if lattice_preserved else ValidationStatus.FAIL,
            "Substitution preserved every lattice-matrix value exactly."
            if lattice_preserved
            else "Substitution changed the lattice matrix.",
        )
    )
    if not lattice_preserved:
        return _rejected(plan, checks)

    coordinates_preserved = all(
        float(before) == float(after)
        for before_site, after_site in zip(parent_structure, output, strict=True)
        for before, after in zip(
            before_site.frac_coords,
            after_site.frac_coords,
            strict=True,
        )
    )
    checks.append(
        _check(
            "coordinates_preserved",
            ValidationStatus.PASS if coordinates_preserved else ValidationStatus.FAIL,
            "Substitution preserved every fractional coordinate exactly."
            if coordinates_preserved
            else "Substitution changed one or more fractional coordinates.",
        )
    )
    if not coordinates_preserved:
        return _rejected(plan, checks)

    ordered_output = _all_sites_ordered(output)
    if not ordered_output:
        for check_index, check in enumerate(checks):
            if check.check_id == "ordered_occupancy":
                checks[check_index] = _check(
                    "ordered_occupancy",
                    ValidationStatus.FAIL,
                    "The substituted structure contains invalid occupancy.",
                )
                break
        return _rejected(plan, checks)

    expected_composition = _element_composition(parent_structure)
    expected_composition[source_element] = (
        expected_composition.get(source_element, 0.0) - len(selected_indices)
    )
    if math.isclose(expected_composition[source_element], 0.0, abs_tol=1e-12):
        del expected_composition[source_element]
    expected_composition[target_element] = (
        expected_composition.get(target_element, 0.0) + len(selected_indices)
    )
    output_composition = _element_composition(output)
    output_elements = tuple(sorted(output_composition))
    expected_species_at_sites = all(
        output[index].specie.symbol == target_element for index in selected_indices
    )
    unselected_species_preserved = all(
        output[index].species == parent_structure[index].species
        for index in range(len(output))
        if index not in set(selected_indices)
    )
    composition_valid = (
        _compositions_close(expected_composition, output_composition)
        and set(output_elements).issubset(request.allowed_output_elements)
        and expected_species_at_sites
        and unselected_species_preserved
    )
    checks.append(
        _check(
            "requirement_composition",
            ValidationStatus.PASS if composition_valid else ValidationStatus.FAIL,
            "Output composition matches the route and the frozen allowed element set."
            if composition_valid
            else "Output composition or species changes violate frozen constraints.",
        )
    )
    if not composition_valid:
        return _rejected(plan, checks)

    finite = _finite_structure(output)
    checks.append(
        _check(
            "finite_structure",
            ValidationStatus.PASS if finite else ValidationStatus.FAIL,
            "Lattice and fractional coordinates are finite."
            if finite
            else "Lattice or fractional coordinates contain non-finite values.",
        )
    )
    if not finite:
        return _rejected(plan, checks)

    positive_volume = math.isfinite(float(output.volume)) and output.volume > 0.0
    checks.append(
        _check(
            "positive_volume",
            ValidationStatus.PASS if positive_volume else ValidationStatus.FAIL,
            "Output lattice has finite positive volume."
            if positive_volume
            else "Output lattice volume is non-positive or non-finite.",
        )
    )
    if not positive_volume:
        return _rejected(plan, checks)

    minimum_distance = _minimum_periodic_distance(output)
    distance_valid = (
        minimum_distance is None
        or minimum_distance >= request.minimum_distance_angstrom
    )
    distance_detail = (
        "Single-site structure has no distinct-site distance."
        if minimum_distance is None
        else f"Minimum periodic distance is {minimum_distance:.12g} angstrom."
    )
    checks.append(
        _check(
            "minimum_distance",
            ValidationStatus.PASS if distance_valid else ValidationStatus.FAIL,
            distance_detail,
        )
    )
    if not distance_valid:
        return _rejected(plan, checks)

    canonical = _canonicalize_structure(output)
    canonicalized = _strict_match(output, canonical)
    checks.append(
        _check(
            "canonicalization",
            ValidationStatus.PASS if canonicalized else ValidationStatus.FAIL,
            "Canonical site ordering preserves the substituted structure."
            if canonicalized
            else "Canonical site ordering changed the substituted structure.",
        )
    )
    if not canonicalized:
        return _rejected(plan, checks)

    try:
        with warnings.catch_warnings(record=True) as processing_warnings:
            warnings.simplefilter("always")
            processed = process_structure(
                canonical,
                summary_elements=sorted(_element_composition(canonical)),
                summary_num_sites=len(canonical),
                summary_formula=None,
                policy=_CANONICAL_STRUCTURE_POLICY,
            )
    except (StructureValidationError, ValueError, TypeError) as error:
        checks.append(
            _check(
                "retrieval_structure_processing",
                ValidationStatus.FAIL,
                f"Pinned process_structure failed: {type(error).__name__}.",
            )
        )
        return _rejected(plan, checks)
    processing_valid = (
        processed.structure_id.startswith("str_")
        and processed.num_sites == len(canonical)
        and processed.elements == sorted(_element_composition(canonical))
        and _strict_match(canonical, processed.structure)
        and not {
            flag
            for flag in processed.data_quality_flags
            if flag != "CIF_ROUND_TRIP_WARNING"
        }
    )
    processing_note_count = len(processing_warnings) + len(
        processed.data_quality_warnings
    )
    checks.append(
        _check(
            "retrieval_structure_processing",
            ValidationStatus.PASS if processing_valid else ValidationStatus.FAIL,
            "Pinned process_structure reproduced the canonical structure and ID; "
            f"recorded {processing_note_count} non-fatal parser/runtime warnings."
            if processing_valid
            else "Pinned process_structure changed the canonical structure or "
            "reported a hard data-quality flag.",
        )
    )
    if not processing_valid:
        return _rejected(plan, checks)
    canonical = processed.structure

    dimensionality = calculate_dimensionality(canonical)
    dimensionality_valid = (
        len(canonical) <= request.max_sites
        and dimensionality.value in request.allowed_output_dimensionalities
        and dimensionality.error is None
    )
    checks.append(
        _check(
            "dimensionality_and_site_budget",
            ValidationStatus.PASS if dimensionality_valid else ValidationStatus.FAIL,
            f"Output has {len(canonical)} sites and dimensionality "
            f"{dimensionality.value} via {dimensionality.method}."
            if dimensionality_valid
            else "Output exceeds the site budget or has disallowed/unknown dimensionality.",
        )
    )
    if not dimensionality_valid:
        return _rejected(plan, checks)

    try:
        artifact_bytes = _serialize_cif(canonical)
    except Exception as error:
        checks.append(
            _check(
                "canonical_round_trip",
                ValidationStatus.FAIL,
                f"Canonical CIF serialization failed: {type(error).__name__}.",
            )
        )
        return _rejected(plan, checks)
    round_trip_valid = _round_trip_matches(artifact_bytes, canonical)
    checks.append(
        _check(
            "canonical_round_trip",
            ValidationStatus.PASS if round_trip_valid else ValidationStatus.FAIL,
            "Canonical CIF round-trip preserves species, lattice, and structure."
            if round_trip_valid
            else "Canonical CIF round-trip changed the structure.",
        )
    )
    if not round_trip_valid:
        return _rejected(plan, checks)

    checks.append(_check("charge_or_oxidation", charge_status, charge_detail))
    artifact_sha256 = hashlib.sha256(artifact_bytes).hexdigest()
    output_structure_id = processed.structure_id
    output_pointer = ArtifactPointerV1(
        uri=f"artifact://inspiration/structures/{output_structure_id}.cif",
        sha256=artifact_sha256,
        size_bytes=len(artifact_bytes),
        media_type=STRUCTURE_ARTIFACT_MEDIA_TYPE,
    )
    status = (
        TransformationStatus.STRUCTURE_VALID
        if charge_status is ValidationStatus.PASS
        else TransformationStatus.REQUIRES_REVIEW
    )
    result_plan = _result_plan(
        plan,
        status=status,
        checks=tuple(checks),
        output_structure_id=output_structure_id,
        output_structure_artifact=output_pointer,
    )
    return TransformationExecutionResult(
        plan=result_plan,
        output_structure=canonical,
        artifact_bytes=artifact_bytes,
    )
