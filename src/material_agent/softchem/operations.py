"""Deterministic kernels for DeepSeek-reasoned minimal structure operations.

Material-specific parameters come from a run-local reasoned spec. Site indices,
layer membership, and gap centers are derived from the hash-bound parent CIF.
Every output remains a proposal and carries both prior and validator receipts.
"""

from __future__ import annotations

import hashlib
import math
import warnings
from dataclasses import dataclass
from enum import StrEnum
from itertools import pairwise
from typing import Annotated, Any, Literal

from pydantic import Field, field_validator, model_validator
from pymatgen.core import Element, Lattice, Species, Structure
from pymatgen.symmetry.analyzer import SpacegroupAnalyzer

from material_agent.inspiration.models import (
    ArtifactPointerV1,
    Identifier,
    StrictModel,
    TransformationStatus,
    ValidationCheckV1,
    ValidationStatus,
    canonical_sha256,
    deterministic_id,
    transformation_route_sha256,
)
from material_agent.inspiration.transformations import (
    EQUIVALENT_SITE_ANGLE_TOLERANCE,
    EQUIVALENT_SITE_SYMPREC,
    STRUCTURE_ARTIFACT_MEDIA_TYPE,
    TransformationIntegrityError,
    _all_sites_ordered,
    _canonicalize_structure,
    _element_composition,
    _finite_structure,
    _minimum_periodic_distance,
    _round_trip_matches,
    _serialize_cif,
    _strict_match,
    _validate_parent_artifact,
)
from material_agent.retrieval.models import RetrievalPolicy
from material_agent.retrieval.structures import (
    StructureValidationError,
    calculate_dimensionality,
    process_structure,
)
from material_agent.softchem.prior import (
    DEFAULT_SMACT_PRIOR_POLICY_V1,
    SmactPriorDecision,
    SmactPriorEvaluator,
    SmactPriorPolicyV1,
)
from material_agent.softchem.registry import (
    DEFAULT_SOFTCHEM_OPERATOR_REGISTRY_V2,
    SoftChemOperatorRegistryV2,
    softchem_registry_bytes,
)

STRUCTURE_OPERATION_PLAN_SCHEMA_VERSION = "inspiration-structure-operation-plan-v2"
STRUCTURE_OPERATION_EXECUTION_SCHEMA_VERSION = "structure-operation-execution-v2"

OperationIdV2 = Literal[
    "APPLY_HOMOGENEOUS_STRAIN_V1",
    "INTERCALATE_REASONED_GAP_SITE_V1",
    "REMOVE_EQUIVALENT_SITE_CLASS_V1",
    "SLIDE_REASONED_LAYER_V1",
    "SUBSTITUTE_EQUIVALENT_SITE_V1",
]

class ReasonedSubstitutionParametersV1(StrictModel):
    parameter_schema_id: Literal["reasoned-substitution-parameters-v1"] = (
        "reasoned-substitution-parameters-v1"
    )
    operator_spec_id: Identifier
    equivalent_site_indices: Annotated[
        tuple[Annotated[int, Field(ge=0)], ...], Field(min_length=1, max_length=128)
    ]
    source_species: Annotated[str, Field(pattern=r"^[A-Z][a-z]?$", max_length=2)]
    target_species: Annotated[str, Field(pattern=r"^[A-Z][a-z]?$", max_length=2)]
    target_oxidation_state: Annotated[float, Field(ge=-8.0, le=8.0)]

    @model_validator(mode="after")
    def validate_substitution(self) -> ReasonedSubstitutionParametersV1:
        if self.source_species == self.target_species:
            raise ValueError("substitution source and target must differ")
        if self.equivalent_site_indices != tuple(
            sorted(set(self.equivalent_site_indices))
        ):
            raise ValueError("substitution indices must be sorted and unique")
        for value in (self.source_species, self.target_species):
            if str(Element(value)) != value:
                raise ValueError("substitution species must be canonical elements")
        return self


class HomogeneousStrainParametersV1(StrictModel):
    parameter_schema_id: Literal["homogeneous-strain-parameters-v1"] = (
        "homogeneous-strain-parameters-v1"
    )
    operator_spec_id: Identifier
    deformation_matrix: tuple[
        tuple[float, float, float],
        tuple[float, float, float],
        tuple[float, float, float],
    ]

    @model_validator(mode="after")
    def safe_matrix(self) -> HomogeneousStrainParametersV1:
        matrix = self.deformation_matrix
        if any(not math.isfinite(value) for row in matrix for value in row):
            raise ValueError("strain matrix must be finite")
        if any(abs(matrix[index][index] - 1.0) > 0.08 for index in range(3)):
            raise ValueError("normal strain exceeds the executor safety envelope")
        if any(
            abs(matrix[row][column]) > 0.03
            for row in range(3)
            for column in range(3)
            if row != column
        ):
            raise ValueError("shear strain exceeds the executor safety envelope")
        determinant = (
            matrix[0][0] * (matrix[1][1] * matrix[2][2] - matrix[1][2] * matrix[2][1])
            - matrix[0][1] * (matrix[1][0] * matrix[2][2] - matrix[1][2] * matrix[2][0])
            + matrix[0][2] * (matrix[1][0] * matrix[2][1] - matrix[1][1] * matrix[2][0])
        )
        if determinant <= 0.0:
            raise ValueError("strain matrix must preserve positive orientation")
        return self


class EquivalentSiteVacancyParametersV1(StrictModel):
    parameter_schema_id: Literal["equivalent-site-vacancy-parameters-v1"] = (
        "equivalent-site-vacancy-parameters-v1"
    )
    operator_spec_id: Identifier
    equivalent_site_indices: Annotated[
        tuple[Annotated[int, Field(ge=0)], ...], Field(min_length=1, max_length=128)
    ]
    removed_species: Annotated[str, Field(pattern=r"^[A-Z][a-z]?$", max_length=2)]
    maximum_removed_site_fraction: Annotated[float, Field(gt=0.0, le=0.5)]

    @model_validator(mode="after")
    def canonical_indices(self) -> EquivalentSiteVacancyParametersV1:
        if self.equivalent_site_indices != tuple(
            sorted(set(self.equivalent_site_indices))
        ):
            raise ValueError("vacancy indices must be sorted and unique")
        if str(Element(self.removed_species)) != self.removed_species:
            raise ValueError("removed species must be a canonical element")
        return self


class ReasonedIntercalationParametersV1(StrictModel):
    parameter_schema_id: Literal["reasoned-intercalation-parameters-v1"] = (
        "reasoned-intercalation-parameters-v1"
    )
    operator_spec_id: Identifier
    intercalant: Annotated[str, Field(pattern=r"^[A-Z][a-z]?$", max_length=2)]
    intercalant_oxidation_state: Annotated[float, Field(ge=-8.0, le=8.0)]
    insertion_frac_coords: tuple[float, float, float]
    minimum_parent_gap_angstrom: Annotated[float, Field(ge=2.5, le=8.0)]

    @field_validator("insertion_frac_coords")
    @classmethod
    def bounded_coords(
        cls, value: tuple[float, float, float]
    ) -> tuple[float, float, float]:
        if any(not math.isfinite(item) or item < 0.0 or item >= 1.0 for item in value):
            raise ValueError("derived insertion coordinates must lie in [0,1)")
        return value

    @model_validator(mode="after")
    def canonical_species(self) -> ReasonedIntercalationParametersV1:
        if str(Element(self.intercalant)) != self.intercalant:
            raise ValueError("intercalant must be a canonical chemical element")
        return self


class ReasonedLayerSlideParametersV1(StrictModel):
    parameter_schema_id: Literal["reasoned-layer-slide-parameters-v1"] = (
        "reasoned-layer-slide-parameters-v1"
    )
    operator_spec_id: Identifier
    layer_site_indices: Annotated[
        tuple[Annotated[int, Field(ge=0)], ...], Field(min_length=1, max_length=512)
    ]
    translation_fractional_ab: tuple[float, float]
    layer_partition_sha256: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]

    @model_validator(mode="after")
    def bounded_translation(self) -> ReasonedLayerSlideParametersV1:
        if self.layer_site_indices != tuple(sorted(set(self.layer_site_indices))):
            raise ValueError("layer indices must be sorted and unique")
        if any(
            not math.isfinite(value) or abs(value) > 1.0
            for value in self.translation_fractional_ab
        ):
            raise ValueError("layer translation must lie in the executor envelope")
        return self


StructureOperationParametersV2 = (
    ReasonedSubstitutionParametersV1
    | HomogeneousStrainParametersV1
    | EquivalentSiteVacancyParametersV1
    | ReasonedIntercalationParametersV1
    | ReasonedLayerSlideParametersV1
)


def operation_parameter_sha256(parameters: StructureOperationParametersV2) -> str:
    return canonical_sha256(
        parameters.model_dump(mode="python", exclude={"operator_spec_id"})
    )


_PARAMETER_TYPE_BY_OPERATOR = {
    "APPLY_HOMOGENEOUS_STRAIN_V1": HomogeneousStrainParametersV1,
    "INTERCALATE_REASONED_GAP_SITE_V1": ReasonedIntercalationParametersV1,
    "REMOVE_EQUIVALENT_SITE_CLASS_V1": EquivalentSiteVacancyParametersV1,
    "SLIDE_REASONED_LAYER_V1": ReasonedLayerSlideParametersV1,
    "SUBSTITUTE_EQUIVALENT_SITE_V1": ReasonedSubstitutionParametersV1,
}

_COMMON_EXECUTION_CHECKS = (
    "canonical_round_trip",
    "chemical_prior",
    "dimensionality_and_site_budget",
    "finite_structure",
    "minimum_distance",
    "operation_semantics",
    "operator_allowed",
    "ordered_occupancy",
    "parameter_model",
    "parent_hash_verified",
    "positive_volume",
)


class RunLocalOperatorSpecV1(StrictModel):
    """One DeepSeek-proposed scientific operation frozen for deterministic replay."""

    schema_version: Literal["run-local-operator-spec-v1"] = "run-local-operator-spec-v1"
    operator_spec_id: Identifier
    operator_id: OperationIdV2
    parent_structure_id: Identifier
    parameter_sha256: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    proposal_sha256: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    operator_registry_sha256: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    scientific_rationale: Annotated[str, Field(min_length=10, max_length=2_000)]
    expected_mechanism: Annotated[str, Field(min_length=5, max_length=1_000)]
    chemical_prior_rationale: Annotated[str, Field(min_length=5, max_length=1_000)]
    decisive_falsification_test: Annotated[str, Field(min_length=5, max_length=1_000)]
    generated_by: Literal["DEEPSEEK_NATIVE_REASONING"] = "DEEPSEEK_NATIVE_REASONING"
    spec_sha256: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    scientific_conclusion: Literal[False] = False

    @model_validator(mode="after")
    def validate_hash(self) -> RunLocalOperatorSpecV1:
        payload = self.model_dump(
            mode="python", exclude={"operator_spec_id", "spec_sha256"}
        )
        expected = canonical_sha256(payload)
        if self.spec_sha256 != expected:
            raise ValueError("run-local operator spec SHA-256 mismatch")
        if self.operator_spec_id != deterministic_id("operator-spec", payload):
            raise ValueError("run-local operator spec ID mismatch")
        return self


class StructureOperationPlanV2(StrictModel):
    schema_version: Literal["inspiration-structure-operation-plan-v2"] = (
        STRUCTURE_OPERATION_PLAN_SCHEMA_VERSION
    )
    plan_id: Identifier
    parent_candidate_id: Identifier
    parent_structure_id: Identifier
    parent_structure_artifact: ArtifactPointerV1
    operator_id: OperationIdV2
    operator_version: Literal["1"] = "1"
    operator_spec: RunLocalOperatorSpecV1
    parameters: StructureOperationParametersV2
    preserved_features: Annotated[tuple[str, ...], Field(min_length=1, max_length=16)]
    changed_features: Annotated[tuple[str, ...], Field(min_length=1, max_length=16)]
    falsification_tests: Annotated[tuple[str, ...], Field(min_length=1, max_length=16)]
    bridge_packet_ids: Annotated[
        tuple[Identifier, ...], Field(min_length=1, max_length=16)
    ]
    compile_prior_decision: Literal["NOT_EVALUATED", "PASS", "REQUIRES_REVIEW"] = (
        "NOT_EVALUATED"
    )
    compile_prior_reason_codes: Annotated[
        tuple[Identifier, ...], Field(max_length=16)
    ] = ()
    validator_ids: Annotated[tuple[Identifier, ...], Field(max_length=16)] = ()
    route_sha256: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    status: TransformationStatus = TransformationStatus.PLANNED
    validation_checks: Annotated[
        tuple[ValidationCheckV1, ...], Field(max_length=64)
    ] = ()
    output_structure_id: Identifier | None = None
    output_structure_artifact: ArtifactPointerV1 | None = None
    scientific_conclusion: Literal[False] = False

    @model_validator(mode="after")
    def validate_plan(self) -> StructureOperationPlanV2:
        expected_type = _PARAMETER_TYPE_BY_OPERATOR[self.operator_id]
        if not isinstance(self.parameters, expected_type):
            raise TypeError("operator and parameter schema do not match")
        if self.operator_spec.operator_id != self.operator_id:
            raise ValueError("operator spec and execution kernel do not match")
        if self.operator_spec.parent_structure_id != self.parent_structure_id:
            raise ValueError("operator spec and parent structure do not match")
        if self.operator_spec.parameter_sha256 != operation_parameter_sha256(
            self.parameters
        ):
            raise ValueError("operator spec does not bind the plan parameters")
        if self.parameters.operator_spec_id != self.operator_spec.operator_spec_id:
            raise ValueError("parameters do not bind the run-local operator spec")
        expected_hash = transformation_route_sha256(
            parent_structure_id=self.parent_structure_id,
            operator_id=self.operator_id,
            operator_version=self.operator_version,
            parameters=self.parameters,
        )
        if self.route_sha256 != expected_hash:
            raise ValueError("route_sha256 does not match the structure operation")
        if len(set(self.bridge_packet_ids)) != len(self.bridge_packet_ids):
            raise ValueError("bridge packet IDs must be unique")
        for name, values in (
            ("compile prior reasons", self.compile_prior_reason_codes),
            ("validator IDs", self.validator_ids),
        ):
            if values != tuple(sorted(set(values))):
                raise ValueError(f"{name} must be sorted and unique")
        prior_unset = self.compile_prior_decision == "NOT_EVALUATED"
        if (
            prior_unset and (self.compile_prior_reason_codes or self.validator_ids)
        ) or (
            not prior_unset
            and (not self.compile_prior_reason_codes or not self.validator_ids)
        ):
            raise ValueError(
                "evaluated compile prior requires reason codes and validator IDs"
            )
        has_output = (
            self.output_structure_id is not None
            and self.output_structure_artifact is not None
        )
        if (self.output_structure_id is None) != (
            self.output_structure_artifact is None
        ):
            raise ValueError(
                "output structure ID and artifact must be supplied together"
            )
        checks = {item.check_id: item.status for item in self.validation_checks}
        if len(checks) != len(self.validation_checks):
            raise ValueError("validation check IDs must be unique")
        if self.status is TransformationStatus.PLANNED:
            if has_output or self.validation_checks:
                raise ValueError("planned operation cannot contain execution output")
            return self
        if self.status is TransformationStatus.REJECTED:
            if has_output or ValidationStatus.FAIL not in set(checks.values()):
                raise ValueError(
                    "rejected operation needs a failing check and no output"
                )
            return self
        if not has_output or any(
            checks.get(item) is not ValidationStatus.PASS
            for item in _COMMON_EXECUTION_CHECKS
        ):
            raise ValueError(
                "non-rejected output requires all common structural checks"
            )
        charge = checks.get("charge_or_oxidation")
        if (
            self.status is TransformationStatus.STRUCTURE_VALID
            and charge is not ValidationStatus.PASS
        ):
            raise ValueError("structure-valid operation requires resolved charge")
        if (
            self.status is TransformationStatus.REQUIRES_REVIEW
            and charge is not ValidationStatus.UNKNOWN
        ):
            raise ValueError("review-required operation needs unresolved charge")
        return self


class StructureOperationExecutionRequestV2(StrictModel):
    schema_version: Literal["structure-operation-execution-v2"] = (
        STRUCTURE_OPERATION_EXECUTION_SCHEMA_VERSION
    )
    plan: StructureOperationPlanV2
    operator_registry_artifact: ArtifactPointerV1
    allowed_output_dimensionalities: tuple[Annotated[int, Field(ge=0, le=3)], ...] = (
        0,
        1,
        2,
        3,
    )
    max_sites: Annotated[int, Field(ge=1, le=2000)] = 512
    minimum_distance_angstrom: Annotated[float, Field(ge=0.5, le=5.0)] = 0.8

    @model_validator(mode="after")
    def planned_only(self) -> StructureOperationExecutionRequestV2:
        if self.plan.status is not TransformationStatus.PLANNED:
            raise ValueError("only PLANNED operations may execute")
        if self.allowed_output_dimensionalities != tuple(
            sorted(set(self.allowed_output_dimensionalities))
        ):
            raise ValueError("allowed dimensionalities must be sorted and unique")
        return self


class OperationPriorDecision(StrEnum):
    PASS = "PASS"
    REQUIRES_REVIEW = "REQUIRES_REVIEW"
    REJECT = "REJECT"


class StructureOperationPriorResultV1(StrictModel):
    schema_version: Literal["structure-operation-prior-v1"] = (
        "structure-operation-prior-v1"
    )
    operator_id: OperationIdV2
    decision: OperationPriorDecision
    reason_codes: Annotated[tuple[Identifier, ...], Field(min_length=1, max_length=16)]
    input_formula: str
    proposed_formula: str
    removed_site_fraction: float | None = Field(default=None, ge=0.0, le=1.0)
    parent_gap_angstrom: float | None = Field(default=None, ge=0.0)
    smact_decision: SmactPriorDecision | None = None
    evidence_level: Literal["HEURISTIC_PRIOR_ONLY"] = "HEURISTIC_PRIOR_ONLY"
    scientific_conclusion: Literal[False] = False


@dataclass(frozen=True, slots=True)
class StructureOperationExecutionResultV2:
    plan: StructureOperationPlanV2
    prior: StructureOperationPriorResultV1
    output_structure: Structure | None
    artifact_bytes: bytes | None

    def __post_init__(self) -> None:
        if self.plan.status is TransformationStatus.REJECTED:
            if self.output_structure is not None or self.artifact_bytes is not None:
                raise TransformationIntegrityError(
                    "INVALID_V2_EXECUTION_RESULT",
                    "rejected operations cannot expose structure bytes",
                )
            return
        if self.output_structure is None or self.artifact_bytes is None:
            raise TransformationIntegrityError(
                "INVALID_V2_EXECUTION_RESULT",
                "review/valid operations require a structure and CIF bytes",
            )
        pointer = self.plan.output_structure_artifact
        if (
            pointer is None
            or pointer.sha256 != hashlib.sha256(self.artifact_bytes).hexdigest()
        ):
            raise TransformationIntegrityError(
                "INVALID_V2_EXECUTION_RESULT",
                "output CIF hash differs from the plan artifact",
            )
        if pointer.size_bytes != len(self.artifact_bytes) or not _round_trip_matches(
            self.artifact_bytes, self.output_structure
        ):
            raise TransformationIntegrityError(
                "INVALID_V2_EXECUTION_RESULT",
                "output CIF size or round-trip differs from the result structure",
            )

    @property
    def candidate_selection_eligible(self) -> bool:
        return self.plan.status is TransformationStatus.STRUCTURE_VALID


_CANONICAL_POLICY = RetrievalPolicy(
    canonical_float_digits=12,
    canonicalization_policy_version="canonical-structure-v1",
)


def _bare_element(site: Any) -> str:
    specie = site.specie
    element = getattr(specie, "element", specie)
    return str(element.symbol)


def _check(check_id: str, status: ValidationStatus, detail: str) -> ValidationCheckV1:
    return ValidationCheckV1(check_id=check_id, status=status, detail=detail)


def largest_c_gap(structure: Structure) -> tuple[float, float]:
    """Return (midpoint fractional z, approximate gap Å) for the largest c gap."""

    if not len(structure):
        raise ValueError("structure has no sites")
    values = sorted(float(site.frac_coords[2]) % 1.0 for site in structure)
    candidates = []
    for index, start in enumerate(values):
        end = values[(index + 1) % len(values)] + (
            1.0 if index + 1 == len(values) else 0.0
        )
        candidates.append((end - start, (start + (end - start) / 2.0) % 1.0))
    fraction, midpoint = max(candidates, key=lambda item: (item[0], -item[1]))
    return midpoint, fraction * float(structure.lattice.c)


def layer_groups(
    structure: Structure, *, separation_angstrom: float = 2.0
) -> tuple[tuple[int, ...], ...]:
    """Partition sites into c-normal layers after cutting the largest periodic gap."""

    if not len(structure):
        return ()
    ordered = sorted(
        (float(site.frac_coords[2]) % 1.0, index)
        for index, site in enumerate(structure)
    )
    gaps = []
    for position, (start, _) in enumerate(ordered):
        end = ordered[(position + 1) % len(ordered)][0] + (
            1.0 if position + 1 == len(ordered) else 0.0
        )
        gaps.append((end - start, position))
    _, cut = max(gaps)
    rotated = ordered[cut + 1 :] + [(z + 1.0, index) for z, index in ordered[: cut + 1]]
    groups: list[list[int]] = [[rotated[0][1]]]
    for (previous_z, _), (current_z, current_index) in pairwise(rotated):
        if (current_z - previous_z) * float(structure.lattice.c) >= separation_angstrom:
            groups.append([])
        groups[-1].append(current_index)
    return tuple(tuple(sorted(group)) for group in groups)


def layer_partition_sha256(groups: tuple[tuple[int, ...], ...]) -> str:
    from material_agent.inspiration.models import canonical_json_bytes

    return hashlib.sha256(canonical_json_bytes(groups)).hexdigest()


def _equivalent_site_groups(structure: Structure) -> tuple[tuple[int, ...], ...]:
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
    if not groups or {index for group in groups for index in group} != set(
        range(len(structure))
    ):
        raise ValueError("symmetry analysis did not partition every parent site")
    return groups


def _smact_decision(
    output: Structure,
    parent: Structure,
    evaluator: SmactPriorEvaluator | None,
    policy: SmactPriorPolicyV1,
) -> SmactPriorDecision:
    if evaluator is None:
        return SmactPriorDecision.REQUIRES_REVIEW
    return evaluator.evaluate(
        output.composition,
        input_formula=parent.composition.reduced_formula,
        policy=policy,
    ).decision


def _explicit_charge_decision(structure: Structure) -> OperationPriorDecision:
    oxidation_states = tuple(
        getattr(site.specie, "oxi_state", None) for site in structure
    )
    if any(value is None for value in oxidation_states):
        return OperationPriorDecision.REQUIRES_REVIEW
    charge = math.fsum(float(value) for value in oxidation_states)
    return (
        OperationPriorDecision.PASS
        if math.isfinite(charge) and math.isclose(charge, 0.0, abs_tol=1e-8)
        else OperationPriorDecision.REQUIRES_REVIEW
    )


def evaluate_structure_operation_prior(
    plan: StructureOperationPlanV2,
    *,
    parent_structure: Structure,
    proposed_structure: Structure,
    smact_evaluator: SmactPriorEvaluator | None = None,
    smact_policy: SmactPriorPolicyV1 = DEFAULT_SMACT_PRIOR_POLICY_V1,
) -> StructureOperationPriorResultV1:
    """Combine bounded geometric priors with independent composition chemistry."""

    parameters = plan.parameters
    reasons: list[str] = []
    decision = OperationPriorDecision.PASS
    removed_fraction = None
    gap = None
    smact = None
    if isinstance(parameters, ReasonedSubstitutionParametersV1):
        smact = _smact_decision(
            proposed_structure, parent_structure, smact_evaluator, smact_policy
        )
        common_states = tuple(
            float(value)
            for value in Element(parameters.target_species).common_oxidation_states
        )
        common_valence = any(
            math.isclose(parameters.target_oxidation_state, value, abs_tol=1e-8)
            for value in common_states
        )
        charge_decision = _explicit_charge_decision(proposed_structure)
        decision = (
            OperationPriorDecision.REJECT
            if smact is SmactPriorDecision.REJECT or not common_valence
            else OperationPriorDecision.PASS
            if smact is SmactPriorDecision.PASS
            and charge_decision is OperationPriorDecision.PASS
            else OperationPriorDecision.REQUIRES_REVIEW
        )
        reasons.extend(
            (
                f"SMACT_{smact.value}",
                "TARGET_COMMON_VALENCE_PASS"
                if common_valence
                else "TARGET_UNCOMMON_VALENCE",
                f"EXPLICIT_CHARGE_{charge_decision.value}",
            )
        )
    elif isinstance(parameters, HomogeneousStrainParametersV1):
        maximum = max(
            abs(
                parameters.deformation_matrix[row][column]
                - (1.0 if row == column else 0.0)
            )
            for row in range(3)
            for column in range(3)
        )
        reasons.append(
            "STRAIN_WITHIN_CONSERVATIVE_PRIOR"
            if maximum <= 0.03
            else "STRAIN_REQUIRES_REVIEW"
        )
        geometric_decision = (
            OperationPriorDecision.PASS
            if maximum <= 0.03
            else OperationPriorDecision.REQUIRES_REVIEW
        )
        charge_decision = _explicit_charge_decision(proposed_structure)
        decision = (
            OperationPriorDecision.PASS
            if geometric_decision is OperationPriorDecision.PASS
            and charge_decision is OperationPriorDecision.PASS
            else OperationPriorDecision.REQUIRES_REVIEW
        )
        reasons.append(f"EXPLICIT_CHARGE_{charge_decision.value}")
    elif isinstance(parameters, EquivalentSiteVacancyParametersV1):
        removed_fraction = len(parameters.equivalent_site_indices) / len(
            parent_structure
        )
        if removed_fraction > parameters.maximum_removed_site_fraction:
            decision = OperationPriorDecision.REJECT
            reasons.append("VACANCY_FRACTION_EXCEEDS_PROPOSED_LIMIT")
        else:
            smact = _smact_decision(
                proposed_structure, parent_structure, smact_evaluator, smact_policy
            )
            charge_decision = _explicit_charge_decision(proposed_structure)
            decision = (
                OperationPriorDecision.REJECT
                if smact is SmactPriorDecision.REJECT
                else OperationPriorDecision.PASS
                if smact is SmactPriorDecision.PASS
                and charge_decision is OperationPriorDecision.PASS
                else OperationPriorDecision.REQUIRES_REVIEW
            )
            reasons.append("VACANCY_FRACTION_WITHIN_BOUND")
            reasons.append(f"SMACT_{smact.value}")
            reasons.append(f"EXPLICIT_CHARGE_{charge_decision.value}")
    elif isinstance(parameters, ReasonedIntercalationParametersV1):
        _, gap = largest_c_gap(parent_structure)
        if gap < parameters.minimum_parent_gap_angstrom:
            decision = OperationPriorDecision.REJECT
            reasons.append("PARENT_GAP_BELOW_3_ANGSTROM")
        else:
            smact = _smact_decision(
                proposed_structure, parent_structure, smact_evaluator, smact_policy
            )
            common_states = tuple(
                float(value)
                for value in Element(parameters.intercalant).common_oxidation_states
            )
            common_valence = any(
                math.isclose(
                    parameters.intercalant_oxidation_state,
                    value,
                    abs_tol=1e-8,
                )
                for value in common_states
            )
            decision = (
                OperationPriorDecision.REJECT
                if smact is SmactPriorDecision.REJECT or not common_valence
                else OperationPriorDecision.REQUIRES_REVIEW
            )
            reasons.extend(
                (
                    "HOST_REDOX_ASSIGNMENT_REQUIRED",
                    "INTERCALANT_COMMON_VALENCE_PASS"
                    if common_valence
                    else "INTERCALANT_UNCOMMON_VALENCE",
                    "REASONED_VDW_GAP_SITE",
                    f"SMACT_{smact.value}",
                )
            )
    else:
        groups = layer_groups(parent_structure)
        if len(groups) < 2:
            decision = OperationPriorDecision.REJECT
            reasons.append("FEWER_THAN_TWO_RESOLVED_LAYERS")
        else:
            charge_decision = _explicit_charge_decision(proposed_structure)
            decision = charge_decision
            reasons.append("REGISTERED_LAYER_PARTITION_AND_TRANSLATION")
            reasons.append(f"EXPLICIT_CHARGE_{charge_decision.value}")
    return StructureOperationPriorResultV1(
        operator_id=plan.operator_id,
        decision=decision,
        reason_codes=tuple(sorted(set(reasons))),
        input_formula=parent_structure.composition.reduced_formula,
        proposed_formula=proposed_structure.composition.reduced_formula,
        removed_site_fraction=removed_fraction,
        parent_gap_angstrom=gap,
        smact_decision=smact,
    )


def _apply_operation(plan: StructureOperationPlanV2, parent: Structure) -> Structure:
    parameters = plan.parameters
    if isinstance(parameters, ReasonedSubstitutionParametersV1):
        output = parent.copy()
        target = Species(parameters.target_species, parameters.target_oxidation_state)
        for index in parameters.equivalent_site_indices:
            output.replace(index, target)
        return output
    if isinstance(parameters, HomogeneousStrainParametersV1):
        matrix = parameters.deformation_matrix
        old = parent.lattice.matrix
        new_matrix = [
            [
                sum(matrix[row][k] * float(old[k][col]) for k in range(3))
                for col in range(3)
            ]
            for row in range(3)
        ]
        return Structure(
            Lattice(new_matrix),
            [site.species for site in parent],
            [site.frac_coords for site in parent],
        )
    if isinstance(parameters, EquivalentSiteVacancyParametersV1):
        output = parent.copy()
        output.remove_sites(list(parameters.equivalent_site_indices))
        return output
    if isinstance(parameters, ReasonedIntercalationParametersV1):
        output = parent.copy()
        output.append(
            Species(
                parameters.intercalant,
                parameters.intercalant_oxidation_state,
            ),
            parameters.insertion_frac_coords,
            coords_are_cartesian=False,
            validate_proximity=False,
        )
        return output
    output = parent.copy()
    assert isinstance(parameters, ReasonedLayerSlideParametersV1)
    vector = (*parameters.translation_fractional_ab, 0.0)
    output.translate_sites(
        list(parameters.layer_site_indices), vector, frac_coords=True, to_unit_cell=True
    )
    return output


def preview_structure_operation(
    plan: StructureOperationPlanV2, parent_structure: Structure
) -> Structure:
    """Apply only the deterministic delta for compiler-side prior evaluation."""

    return _apply_operation(plan, parent_structure)


def bind_compile_prior(
    plan: StructureOperationPlanV2,
    prior: StructureOperationPriorResultV1,
    *,
    validator_ids: tuple[str, ...],
) -> StructureOperationPlanV2:
    if prior.decision is OperationPriorDecision.REJECT:
        raise ValueError("rejected prior cannot be bound to a compiled plan")
    payload = plan.model_dump(mode="python")
    payload.update(
        {
            "compile_prior_decision": prior.decision.value,
            "compile_prior_reason_codes": prior.reason_codes,
            "validator_ids": tuple(sorted(set(validator_ids))),
        }
    )
    return StructureOperationPlanV2.model_validate(payload)


def _operation_semantics(
    plan: StructureOperationPlanV2, parent: Structure, output: Structure
) -> tuple[bool, str]:
    parameters = plan.parameters
    if isinstance(parameters, ReasonedSubstitutionParametersV1):
        groups = _equivalent_site_groups(parent)
        indices = parameters.equivalent_site_indices
        valid = (
            indices in groups
            and all(
                _bare_element(parent[index]) == parameters.source_species
                for index in indices
            )
            and all(
                _bare_element(output[index]) == parameters.target_species
                for index in indices
            )
            and len(output) == len(parent)
            and output.lattice == parent.lattice
        )
        return valid, "One complete symmetry-equivalence class was substituted."
    if isinstance(parameters, HomogeneousStrainParametersV1):
        valid = (
            len(output) == len(parent)
            and output.composition == parent.composition
            and all(
                before.species == after.species
                and all(
                    float(x) == float(y)
                    for x, y in zip(before.frac_coords, after.frac_coords, strict=True)
                )
                for before, after in zip(parent, output, strict=True)
            )
        )
        return (
            valid,
            "Only the lattice changed under the registered deformation matrix.",
        )
    if isinstance(parameters, EquivalentSiteVacancyParametersV1):
        groups = _equivalent_site_groups(parent)
        indices = parameters.equivalent_site_indices
        valid = (
            indices in groups
            and all(
                _bare_element(parent[index]) == parameters.removed_species
                for index in indices
            )
            and len(output) == len(parent) - len(indices)
        )
        return valid, "Exactly one complete symmetry-equivalence class was removed."
    if isinstance(parameters, ReasonedIntercalationParametersV1):
        midpoint, gap = largest_c_gap(parent)
        expected = (*parameters.insertion_frac_coords[:2], midpoint)
        valid = (
            len(output) == len(parent) + 1
            and all(
                math.isclose(a, b, abs_tol=1e-10)
                for a, b in zip(parameters.insertion_frac_coords, expected, strict=True)
            )
            and gap >= parameters.minimum_parent_gap_angstrom
        )
        return (
            valid,
            "One reasoned intercalant was inserted at its proposed in-plane position and the derived largest-gap midpoint.",
        )
    assert isinstance(parameters, ReasonedLayerSlideParametersV1)
    groups = layer_groups(parent)
    valid = (
        parameters.layer_site_indices in groups
        and parameters.layer_partition_sha256 == layer_partition_sha256(groups)
        and len(groups) >= 2
        and len(output) == len(parent)
        and output.composition == parent.composition
    )
    return valid, "One derived complete layer was translated by a registered vector."


def _result_plan(
    source: StructureOperationPlanV2,
    *,
    status: TransformationStatus,
    checks: tuple[ValidationCheckV1, ...],
    structure_id: str | None = None,
    pointer: ArtifactPointerV1 | None = None,
) -> StructureOperationPlanV2:
    payload = source.model_dump(mode="python")
    payload.update(
        {
            "status": status,
            "validation_checks": checks,
            "output_structure_id": structure_id,
            "output_structure_artifact": pointer,
        }
    )
    return StructureOperationPlanV2.model_validate(payload)


def _rejected(
    plan: StructureOperationPlanV2,
    prior: StructureOperationPriorResultV1,
    checks: list[ValidationCheckV1],
) -> StructureOperationExecutionResultV2:
    return StructureOperationExecutionResultV2(
        plan=_result_plan(
            plan, status=TransformationStatus.REJECTED, checks=tuple(checks)
        ),
        prior=prior,
        output_structure=None,
        artifact_bytes=None,
    )


def execute_registered_structure_operation(
    request: StructureOperationExecutionRequestV2,
    *,
    parent_structure: Structure,
    parent_artifact_bytes: bytes,
    operator_registry: SoftChemOperatorRegistryV2 = DEFAULT_SOFTCHEM_OPERATOR_REGISTRY_V2,
    smact_evaluator: SmactPriorEvaluator | None = None,
    smact_policy: SmactPriorPolicyV1 = DEFAULT_SMACT_PRIOR_POLICY_V1,
) -> StructureOperationExecutionResultV2:
    """Execute one v2 operation after registry, prior, delta, and CIF validation."""

    registry_bytes = softchem_registry_bytes(operator_registry)
    pointer = request.operator_registry_artifact
    if pointer.sha256 != hashlib.sha256(registry_bytes).hexdigest() or (
        pointer.size_bytes is not None and pointer.size_bytes != len(registry_bytes)
    ):
        raise TransformationIntegrityError(
            "SOFTCHEM_REGISTRY_HASH_MISMATCH",
            "v2 registry artifact is not the execution registry",
        )
    if (
        request.plan.operator_spec.operator_registry_sha256
        != hashlib.sha256(registry_bytes).hexdigest()
    ):
        raise TransformationIntegrityError(
            "RUN_LOCAL_SPEC_REGISTRY_MISMATCH",
            "run-local operator spec does not bind the execution registry",
        )
    parent = _validate_parent_artifact(
        request.plan,
        parent_structure=parent_structure,
        parent_artifact_bytes=parent_artifact_bytes,
    )
    checks = [
        _check(
            "parent_hash_verified",
            ValidationStatus.PASS,
            "Parent Structure matches its hash-bound CIF.",
        )
    ]
    try:
        spec = operator_registry.resolve(
            request.plan.operator_id, request.plan.operator_version
        )
    except KeyError:
        checks.append(
            _check(
                "operator_allowed",
                ValidationStatus.FAIL,
                "Operator is absent from the v2 registry.",
            )
        )
        placeholder = StructureOperationPriorResultV1(
            operator_id=request.plan.operator_id,
            decision=OperationPriorDecision.REJECT,
            reason_codes=("UNREGISTERED_OPERATOR",),
            input_formula=parent.composition.reduced_formula,
            proposed_formula=parent.composition.reduced_formula,
        )
        return _rejected(request.plan, placeholder, checks)
    checks.append(
        _check(
            "operator_allowed",
            ValidationStatus.PASS,
            f"Resolved {spec.executor_id} from the hash-bound v2 registry.",
        )
    )
    checks.append(
        _check(
            "parameter_model",
            ValidationStatus.PASS,
            f"Validated {request.plan.parameters.parameter_schema_id}.",
        )
    )
    output = _apply_operation(request.plan, parent)
    prior = evaluate_structure_operation_prior(
        request.plan,
        parent_structure=parent,
        proposed_structure=output,
        smact_evaluator=smact_evaluator,
        smact_policy=smact_policy,
    )
    prior_status = (
        ValidationStatus.FAIL
        if prior.decision is OperationPriorDecision.REJECT
        else ValidationStatus.PASS
    )
    checks.append(
        _check(
            "chemical_prior",
            prior_status,
            f"{prior.decision.value}: {','.join(prior.reason_codes)}. Prior only; no property conclusion.",
        )
    )
    if prior.decision is OperationPriorDecision.REJECT:
        return _rejected(request.plan, prior, checks)
    semantics, detail = _operation_semantics(request.plan, parent, output)
    checks.append(
        _check(
            "operation_semantics",
            ValidationStatus.PASS if semantics else ValidationStatus.FAIL,
            detail,
        )
    )
    if not semantics:
        return _rejected(request.plan, prior, checks)
    ordered = _all_sites_ordered(output)
    finite = _finite_structure(output)
    positive = math.isfinite(float(output.volume)) and output.volume > 0
    distance = _minimum_periodic_distance(output)
    distant = distance is None or distance >= request.minimum_distance_angstrom
    checks.extend(
        (
            _check(
                "ordered_occupancy",
                ValidationStatus.PASS if ordered else ValidationStatus.FAIL,
                "All sites have ordered unit occupancy.",
            ),
            _check(
                "finite_structure",
                ValidationStatus.PASS if finite else ValidationStatus.FAIL,
                "All lattice and coordinate values are finite.",
            ),
            _check(
                "positive_volume",
                ValidationStatus.PASS if positive else ValidationStatus.FAIL,
                "Output has finite positive volume.",
            ),
            _check(
                "minimum_distance",
                ValidationStatus.PASS if distant else ValidationStatus.FAIL,
                "No distinct sites are closer than the execution threshold."
                if distance is None
                else f"Minimum periodic distance is {distance:.6g} angstrom.",
            ),
        )
    )
    if not all((ordered, finite, positive, distant)):
        return _rejected(request.plan, prior, checks)
    canonical = _canonicalize_structure(output)
    # CIF oxidation labels are not portable across elements/readers (for example,
    # pymatgen writes ``Li+`` but reads it back as bare ``Li``). The proposed
    # valence remains hash-bound in the operator spec and prior; the structural
    # artifact deliberately uses bare elements so its geometry can round-trip.
    artifact_structure = canonical.copy()
    artifact_structure.remove_oxidation_states()
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            processed = process_structure(
                artifact_structure,
                summary_elements=sorted(_element_composition(artifact_structure)),
                summary_num_sites=len(artifact_structure),
                policy=_CANONICAL_POLICY,
            )
    except (StructureValidationError, TypeError, ValueError) as error:
        checks.append(
            _check(
                "canonical_round_trip",
                ValidationStatus.FAIL,
                f"Canonical processing failed: {type(error).__name__}.",
            )
        )
        return _rejected(request.plan, prior, checks)
    dimensionality = calculate_dimensionality(processed.structure)
    dimensionality_ok = (
        len(processed.structure) <= request.max_sites
        and dimensionality.error is None
        and dimensionality.value in request.allowed_output_dimensionalities
    )
    checks.append(
        _check(
            "dimensionality_and_site_budget",
            ValidationStatus.PASS if dimensionality_ok else ValidationStatus.FAIL,
            f"Output has {len(processed.structure)} sites; dimensionality={dimensionality.value}.",
        )
    )
    if not dimensionality_ok:
        return _rejected(request.plan, prior, checks)
    artifact = _serialize_cif(processed.structure)
    round_trip = _round_trip_matches(artifact, processed.structure) and _strict_match(
        artifact_structure, processed.structure
    )
    checks.append(
        _check(
            "canonical_round_trip",
            ValidationStatus.PASS if round_trip else ValidationStatus.FAIL,
            "Canonical CIF round-trip preserves the output structure.",
        )
    )
    if not round_trip:
        return _rejected(request.plan, prior, checks)
    charge_status = (
        ValidationStatus.PASS
        if prior.decision is OperationPriorDecision.PASS
        else ValidationStatus.UNKNOWN
    )
    checks.append(
        _check(
            "charge_or_oxidation",
            charge_status,
            "Composition/charge prior passed."
            if charge_status is ValidationStatus.PASS
            else "Composition or charge remains review-required.",
        )
    )
    output_pointer = ArtifactPointerV1(
        uri=f"artifact://inspiration/structures/{processed.structure_id}.cif",
        sha256=hashlib.sha256(artifact).hexdigest(),
        size_bytes=len(artifact),
        media_type=STRUCTURE_ARTIFACT_MEDIA_TYPE,
    )
    status = (
        TransformationStatus.STRUCTURE_VALID
        if charge_status is ValidationStatus.PASS
        else TransformationStatus.REQUIRES_REVIEW
    )
    return StructureOperationExecutionResultV2(
        plan=_result_plan(
            request.plan,
            status=status,
            checks=tuple(checks),
            structure_id=processed.structure_id,
            pointer=output_pointer,
        ),
        prior=prior,
        output_structure=processed.structure,
        artifact_bytes=artifact,
    )


def make_operation_plan(
    *,
    candidate_id: str,
    parent_candidate_id: str,
    parent_structure_id: str,
    parent_pointer: ArtifactPointerV1,
    operator_id: OperationIdV2,
    operator_spec: RunLocalOperatorSpecV1,
    parameters: StructureOperationParametersV2,
    preserved_features: tuple[str, ...],
    changed_features: tuple[str, ...],
) -> StructureOperationPlanV2:
    route = transformation_route_sha256(
        parent_structure_id=parent_structure_id,
        operator_id=operator_id,
        operator_version="1",
        parameters=parameters,
    )
    return StructureOperationPlanV2(
        plan_id=deterministic_id(
            "plan", {"candidate_id": candidate_id, "route_sha256": route}
        ),
        parent_candidate_id=parent_candidate_id,
        parent_structure_id=parent_structure_id,
        parent_structure_artifact=parent_pointer,
        operator_id=operator_id,
        operator_spec=operator_spec,
        parameters=parameters,
        preserved_features=preserved_features,
        changed_features=changed_features,
        falsification_tests=(
            "registered chemistry prior",
            "registered structure validators",
            "downstream relaxation and property calculation",
        ),
        bridge_packet_ids=("generic-research-mechanism-v2",),
        route_sha256=route,
    )


def freeze_run_local_operator_spec(
    *,
    operator_id: OperationIdV2,
    parent_structure_id: str,
    parameters: StructureOperationParametersV2,
    proposal_payload: dict[str, object],
    operator_registry_sha256: str,
    scientific_rationale: str,
    expected_mechanism: str,
    chemical_prior_rationale: str,
    decisive_falsification_test: str,
) -> tuple[RunLocalOperatorSpecV1, StructureOperationParametersV2]:
    """Freeze one reasoned proposal and bind its derived executable parameters."""

    parameter_sha256 = operation_parameter_sha256(parameters)
    spec_payload = {
        "schema_version": "run-local-operator-spec-v1",
        "operator_id": operator_id,
        "parent_structure_id": parent_structure_id,
        "parameter_sha256": parameter_sha256,
        "proposal_sha256": canonical_sha256(proposal_payload),
        "operator_registry_sha256": operator_registry_sha256,
        "scientific_rationale": scientific_rationale,
        "expected_mechanism": expected_mechanism,
        "chemical_prior_rationale": chemical_prior_rationale,
        "decisive_falsification_test": decisive_falsification_test,
        "generated_by": "DEEPSEEK_NATIVE_REASONING",
        "scientific_conclusion": False,
    }
    spec = RunLocalOperatorSpecV1(
        **spec_payload,
        operator_spec_id=deterministic_id("operator-spec", spec_payload),
        spec_sha256=canonical_sha256(spec_payload),
    )
    parameter_payload = parameters.model_dump(mode="python")
    parameter_payload["operator_spec_id"] = spec.operator_spec_id
    bound_parameters = type(parameters).model_validate(parameter_payload)
    return spec, bound_parameters
