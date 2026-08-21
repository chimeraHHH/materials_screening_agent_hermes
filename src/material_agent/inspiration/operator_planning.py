"""Compile DeepSeek structure ideas into frozen, executable soft-chemistry plans."""

from __future__ import annotations

import hashlib
import threading
import warnings
from collections.abc import Callable, Mapping
from typing import Any, Literal

from pydantic import Field, field_validator, model_validator
from pymatgen.core import Element, Structure
from pymatgen.symmetry.analyzer import SpacegroupAnalyzer

from material_agent.inspiration.deepseek_agent import DeepSeekFunctionTool
from material_agent.inspiration.models import (
    ArtifactPointerV1,
    SubstitutionParametersV1,
    TransformationPlanV1,
    TransformationStatus,
    canonical_json_bytes,
    deterministic_id,
    transformation_route_sha256,
)
from material_agent.inspiration.research_graph import (
    DatabaseCandidateV1,
    RegisteredTransformationAuditV3,
    TransformationCompileRejectionV3,
    TransformationPlanBindingV1,
)
from material_agent.inspiration.transformations import (
    DEFAULT_SUBSTITUTION_REGISTRY_V1,
    EQUIVALENT_SITE_ANGLE_TOLERANCE,
    EQUIVALENT_SITE_SYMPREC,
    STRUCTURE_ARTIFACT_MEDIA_TYPE,
    SubstitutionExecutionRequestV1,
    SubstitutionRegistryV1,
    substitution_registry_bytes,
    substitution_registry_sha256,
)
from material_agent.orchestrator.llm import LLMProviderError
from material_agent.orchestrator.models import StrictModel
from material_agent.retrieval.storage import LocalArtifactStore
from material_agent.softchem import (
    DEFAULT_SOFTCHEM_OPERATOR_REGISTRY_V2,
    SoftChemOperatorRegistryV2,
    softchem_registry_sha256,
)
from material_agent.softchem.operations import (
    CarrierDopingParametersV1,
    ConditionOperationPriorResultV1,
    ElectrostaticGateParametersV1,
    EquivalentSiteVacancyParametersV1,
    HomogeneousStrainParametersV1,
    MagneticProximityParametersV1,
    ReasonedConditionPlanV1,
    ReasonedIntercalationParametersV1,
    ReasonedLayerSlideParametersV1,
    ReasonedSubstitutionParametersV1,
    StructureOperationExecutionRequestV2,
    StructureOperationPlanV2,
    VdwHeterostructureParametersV1,
    bind_compile_prior,
    evaluate_condition_operation_prior,
    evaluate_structure_operation_prior,
    freeze_run_local_operator_spec,
    largest_c_gap,
    layer_groups,
    layer_partition_sha256,
    make_condition_plan,
    make_operation_plan,
    preview_structure_operation,
)


class CompileRegisteredSubstitutionArgsV1(StrictModel):
    candidate_id: str = Field(pattern=r"^candidate-[a-z0-9-]{1,64}$")
    database_candidate_id: str = Field(pattern=r"^db-candidate-[0-9a-f]{24}$")
    substitution_rule_id: Literal["s-to-se-isovalent-v1", "se-to-s-isovalent-v1"]


class CompileReasonedOperationArgsV3(StrictModel):
    """Scientific search space proposed by DeepSeek; null marks unused fields."""

    candidate_id: str = Field(pattern=r"^candidate-[a-z0-9-]{1,64}$")
    database_candidate_id: str = Field(pattern=r"^db-candidate-[0-9a-f]{24}$")
    operation_kind: Literal[
        "SUBSTITUTION",
        "HOMOGENEOUS_STRAIN",
        "VACANCY",
        "INTERCALATION",
        "LAYER_SLIDE",
        "CARRIER_DOPING",
        "ELECTROSTATIC_GATE",
        "MAGNETIC_PROXIMITY",
        "VDW_HETEROSTRUCTURE",
    ]
    partner_database_candidate_id: str | None = Field(
        default=None, pattern=r"^db-candidate-[0-9a-f]{24}$"
    )
    source_element: str | None = Field(default=None, max_length=2)
    target_element: str | None = Field(default=None, max_length=2)
    target_oxidation_state: float | None = Field(default=None, ge=-8.0, le=8.0)
    normal_strain_x_percent: float | None = None
    normal_strain_y_percent: float | None = None
    normal_strain_z_percent: float | None = None
    shear_strain_xy_percent: float | None = None
    shear_strain_xz_percent: float | None = None
    shear_strain_yz_percent: float | None = None
    vacancy_element: str | None = Field(default=None, max_length=2)
    maximum_removed_site_fraction: float | None = Field(default=None, gt=0.0, le=0.5)
    intercalant: str | None = Field(default=None, max_length=2)
    intercalant_oxidation_state: float | None = Field(default=None, ge=-8.0, le=8.0)
    intercalation_site_a_fraction: float | None = Field(default=None, ge=0.0, lt=1.0)
    intercalation_site_b_fraction: float | None = Field(default=None, ge=0.0, lt=1.0)
    minimum_parent_gap_angstrom: float | None = Field(default=None, ge=2.5, le=8.0)
    layer_from_top: int | None = Field(default=None, ge=1, le=8)
    slide_a_fraction: float | None = None
    slide_b_fraction: float | None = None
    carrier_type: Literal["ELECTRON", "HOLE"] | None = None
    carriers_per_primitive_cell: float | None = Field(
        default=None, gt=0.0, le=2.0
    )
    sample_charge_states: tuple[float, ...] | None = Field(
        default=None, min_length=2, max_length=16
    )
    electric_field_v_per_angstrom: float | None = Field(
        default=None, ge=-1.0, le=1.0
    )
    field_direction: Literal["C_POSITIVE", "C_NEGATIVE"] | None = None
    minimum_vacuum_angstrom: float | None = Field(default=None, ge=12.0, le=50.0)
    interface_separation_angstrom: float | None = Field(
        default=None, ge=2.0, le=8.0
    )
    relative_twist_degrees: float | None = Field(default=None, ge=-30.0, le=30.0)
    maximum_lattice_mismatch_percent: float | None = Field(
        default=None, gt=0.0, le=10.0
    )
    maximum_supercell_area_factor: int | None = Field(default=None, ge=1, le=64)
    magnetization_alignment: Literal[
        "PARALLEL", "ANTIPARALLEL", "SCAN_BOTH"
    ] | None = None
    interface_registry: str | None = Field(default=None, min_length=1, max_length=64)
    scientific_rationale: str = Field(min_length=10, max_length=2_000)
    expected_mechanism: str = Field(min_length=5, max_length=1_000)
    chemical_prior_rationale: str = Field(min_length=5, max_length=1_000)
    decisive_falsification_test: str = Field(min_length=5, max_length=1_000)

    @field_validator(
        "source_element", "target_element", "vacancy_element", "intercalant"
    )
    @classmethod
    def canonical_optional_element(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if str(Element(value)) != value:
            raise ValueError("operation species must be a canonical element")
        return value

    @model_validator(mode="after")
    def validate_operation_fields(self) -> CompileReasonedOperationArgsV3:
        selected = {
            "SUBSTITUTION": (
                self.source_element is not None
                and self.target_element is not None
                and self.target_oxidation_state is not None
            ),
            "HOMOGENEOUS_STRAIN": (
                self.normal_strain_x_percent is not None
                and self.normal_strain_y_percent is not None
                and self.normal_strain_z_percent is not None
                and self.shear_strain_xy_percent is not None
                and self.shear_strain_xz_percent is not None
                and self.shear_strain_yz_percent is not None
            ),
            "VACANCY": (
                self.vacancy_element is not None
                and self.maximum_removed_site_fraction is not None
            ),
            "INTERCALATION": (
                self.intercalant is not None
                and self.intercalant_oxidation_state is not None
                and self.intercalation_site_a_fraction is not None
                and self.intercalation_site_b_fraction is not None
                and self.minimum_parent_gap_angstrom is not None
            ),
            "LAYER_SLIDE": (
                self.layer_from_top is not None
                and self.slide_a_fraction is not None
                and self.slide_b_fraction is not None
            ),
            "CARRIER_DOPING": (
                self.carrier_type is not None
                and self.carriers_per_primitive_cell is not None
                and self.sample_charge_states is not None
            ),
            "ELECTROSTATIC_GATE": (
                self.electric_field_v_per_angstrom is not None
                and self.field_direction is not None
                and self.minimum_vacuum_angstrom is not None
            ),
            "MAGNETIC_PROXIMITY": (
                self.partner_database_candidate_id is not None
                and self.interface_separation_angstrom is not None
                and self.relative_twist_degrees is not None
                and self.maximum_lattice_mismatch_percent is not None
                and self.magnetization_alignment is not None
                and self.interface_registry is not None
            ),
            "VDW_HETEROSTRUCTURE": (
                self.partner_database_candidate_id is not None
                and self.interface_separation_angstrom is not None
                and self.relative_twist_degrees is not None
                and self.maximum_lattice_mismatch_percent is not None
                and self.maximum_supercell_area_factor is not None
                and self.interface_registry is not None
            ),
        }
        if not selected[self.operation_kind]:
            raise ValueError("selected operation is missing its reasoned parameters")
        normal = (
            self.normal_strain_x_percent,
            self.normal_strain_y_percent,
            self.normal_strain_z_percent,
        )
        if any(value is not None and abs(value) > 8.0 for value in normal):
            raise ValueError("normal strain exceeds the executor safety envelope")
        shear = (
            self.shear_strain_xy_percent,
            self.shear_strain_xz_percent,
            self.shear_strain_yz_percent,
        )
        if any(value is not None and abs(value) > 3.0 for value in shear):
            raise ValueError("shear strain exceeds the executor safety envelope")
        slide = (self.slide_a_fraction, self.slide_b_fraction)
        if any(value is not None and abs(value) > 1.0 for value in slide):
            raise ValueError("slide vector exceeds the executor safety envelope")
        if (
            self.partner_database_candidate_id is not None
            and self.partner_database_candidate_id == self.database_candidate_id
        ):
            raise ValueError("interface operations require two distinct parents")
        return self


class ConditionPriorRejected(ValueError):
    def __init__(self, prior: ConditionOperationPriorResultV1) -> None:
        self.prior = prior
        super().__init__(",".join(prior.reason_codes))


def _bare_element(site: Any) -> str | None:
    if not site.is_ordered:
        return None
    specie = site.specie
    element = getattr(specie, "element", specie)
    symbol = getattr(element, "symbol", None)
    return symbol if isinstance(symbol, str) else None


def equivalent_site_groups(structure: Structure) -> tuple[tuple[int, ...], ...]:
    """Return canonical symmetry-equivalence groups used by the executor request."""

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        symmetrized = SpacegroupAnalyzer(
            structure,
            symprec=EQUIVALENT_SITE_SYMPREC,
            angle_tolerance=EQUIVALENT_SITE_ANGLE_TOLERANCE,
        ).get_symmetrized_structure()
    groups = tuple(
        sorted(
            tuple(sorted(int(index) for index in group))
            for group in symmetrized.equivalent_indices
        )
    )
    if not groups or {index for group in groups for index in group} != set(
        range(len(structure))
    ):
        raise ValueError("symmetry analysis did not partition every structure site")
    return groups


def compile_registered_substitution_plans(
    *,
    candidate_id: str,
    database_candidate: DatabaseCandidateV1,
    parent_structure: Structure,
    parent_artifact_bytes: bytes,
    substitution_rule_id: str,
    operator_registry: SoftChemOperatorRegistryV2 = (
        DEFAULT_SOFTCHEM_OPERATOR_REGISTRY_V2
    ),
    substitution_registry: SubstitutionRegistryV1 = DEFAULT_SUBSTITUTION_REGISTRY_V1,
) -> tuple[TransformationPlanV1, ...]:
    """Compile every eligible complete equivalence class for one pinned rule."""

    operator_registry.resolve(
        substitution_registry.operator_id, substitution_registry.operator_version
    )
    rule = next(
        (
            item
            for item in substitution_registry.rules
            if item.rule_id == substitution_rule_id
        ),
        None,
    )
    if rule is None:
        raise KeyError(f"substitution rule is not registered: {substitution_rule_id}")
    digest = hashlib.sha256(parent_artifact_bytes).hexdigest()
    if digest != database_candidate.structure_artifact_sha256:
        raise ValueError(
            "parent CIF bytes differ from the database-candidate artifact hash"
        )
    parent_pointer = ArtifactPointerV1(
        uri=database_candidate.structure_artifact_uri,
        sha256=digest,
        size_bytes=len(parent_artifact_bytes),
        media_type=STRUCTURE_ARTIFACT_MEDIA_TYPE,
    )
    plans: list[TransformationPlanV1] = []
    for group in equivalent_site_groups(parent_structure):
        species = tuple(_bare_element(parent_structure[index]) for index in group)
        if not species or any(item != rule.source_element for item in species):
            continue
        parameters = SubstitutionParametersV1(
            equivalent_site_indices=group,
            source_species=rule.source_element,
            target_species=rule.target_element,
        )
        route_sha256 = transformation_route_sha256(
            parent_structure_id=database_candidate.canonical_structure_id,
            operator_id=substitution_registry.operator_id,
            operator_version=substitution_registry.operator_version,
            parameters=parameters,
        )
        plans.append(
            TransformationPlanV1(
                plan_id=deterministic_id(
                    "plan",
                    {"candidate_id": candidate_id, "route_sha256": route_sha256},
                ),
                parent_candidate_id=database_candidate.database_candidate_id,
                parent_structure_id=database_candidate.canonical_structure_id,
                parent_structure_artifact=parent_pointer,
                operator_id=substitution_registry.operator_id,
                operator_version=substitution_registry.operator_version,
                parameters=parameters,
                preserved_features=(
                    "lattice vectors and fractional coordinates",
                    "complete crystallographic equivalence classes",
                ),
                changed_features=(
                    f"{rule.source_element}-to-{rule.target_element} species on sites {group}",
                ),
                falsification_tests=(
                    "SMACT oxidation and charge prior",
                    "registered structure-integrity validation suite",
                    "downstream relaxation and target-property calculation",
                ),
                bridge_packet_ids=("generic-research-mechanism-v1",),
                route_sha256=route_sha256,
                status=TransformationStatus.PLANNED,
            )
        )
    return tuple(plans)


def _parent_pointer(
    database_candidate: DatabaseCandidateV1,
    parent_artifact_bytes: bytes,
) -> ArtifactPointerV1:
    digest = hashlib.sha256(parent_artifact_bytes).hexdigest()
    if digest != database_candidate.structure_artifact_sha256:
        raise ValueError(
            "parent CIF bytes differ from the database-candidate artifact hash"
        )
    return ArtifactPointerV1(
        uri=database_candidate.structure_artifact_uri,
        sha256=digest,
        size_bytes=len(parent_artifact_bytes),
        media_type=STRUCTURE_ARTIFACT_MEDIA_TYPE,
    )


def compile_reasoned_structure_operation_plans(
    *,
    database_candidate: DatabaseCandidateV1,
    parent_structure: Structure,
    parent_artifact_bytes: bytes,
    proposal: CompileReasonedOperationArgsV3,
    partner_database_candidate: DatabaseCandidateV1 | None = None,
    partner_structure: Structure | None = None,
    partner_artifact_bytes: bytes | None = None,
    operator_registry: SoftChemOperatorRegistryV2 = DEFAULT_SOFTCHEM_OPERATOR_REGISTRY_V2,
) -> tuple[StructureOperationPlanV2 | ReasonedConditionPlanV1, ...]:
    """Freeze one DeepSeek-proposed spec and derive its executable structure details."""

    pointer = _parent_pointer(database_candidate, parent_artifact_bytes)
    common = {
        "candidate_id": proposal.candidate_id,
        "parent_candidate_id": database_candidate.database_candidate_id,
        "parent_structure_id": database_candidate.canonical_structure_id,
        "parent_pointer": pointer,
    }
    proposal_payload = proposal.model_dump(mode="python")
    registry_sha256 = softchem_registry_sha256(operator_registry)
    partner_pointer = (
        _parent_pointer(partner_database_candidate, partner_artifact_bytes)
        if partner_database_candidate is not None
        and partner_artifact_bytes is not None
        else None
    )

    def freeze(
        operator_id: Any,
        parameters: Any,
    ) -> tuple[Any, Any]:
        return freeze_run_local_operator_spec(
            operator_id=operator_id,
            parent_structure_id=database_candidate.canonical_structure_id,
            parameters=parameters,
            proposal_payload=proposal_payload,
            operator_registry_sha256=registry_sha256,
            scientific_rationale=proposal.scientific_rationale,
            expected_mechanism=proposal.expected_mechanism,
            chemical_prior_rationale=proposal.chemical_prior_rationale,
            decisive_falsification_test=proposal.decisive_falsification_test,
        )

    plans: list[StructureOperationPlanV2 | ReasonedConditionPlanV1] = []
    if proposal.operation_kind == "SUBSTITUTION":
        operator_registry.resolve("SUBSTITUTE_EQUIVALENT_SITE_V1", "1")
        assert proposal.source_element is not None
        assert proposal.target_element is not None
        assert proposal.target_oxidation_state is not None
        for group in equivalent_site_groups(parent_structure):
            if not all(
                _bare_element(parent_structure[index]) == proposal.source_element
                for index in group
            ):
                continue
            parameters = ReasonedSubstitutionParametersV1(
                operator_spec_id="pending-spec",
                equivalent_site_indices=group,
                source_species=proposal.source_element,
                target_species=proposal.target_element,
                target_oxidation_state=proposal.target_oxidation_state,
            )
            spec, parameters = freeze("SUBSTITUTE_EQUIVALENT_SITE_V1", parameters)
            plans.append(
                make_operation_plan(
                    **common,
                    operator_id="SUBSTITUTE_EQUIVALENT_SITE_V1",
                    operator_spec=spec,
                    parameters=parameters,
                    preserved_features=(
                        "lattice",
                        "site count",
                        "fractional coordinates",
                    ),
                    changed_features=(
                        f"DeepSeek-proposed {proposal.source_element}-to-{proposal.target_element} complete-class substitution",
                    ),
                )
            )
    elif proposal.operation_kind == "HOMOGENEOUS_STRAIN":
        operator_registry.resolve("APPLY_HOMOGENEOUS_STRAIN_V1", "1")
        normal = (
            proposal.normal_strain_x_percent,
            proposal.normal_strain_y_percent,
            proposal.normal_strain_z_percent,
        )
        shear = (
            proposal.shear_strain_xy_percent,
            proposal.shear_strain_xz_percent,
            proposal.shear_strain_yz_percent,
        )
        assert all(value is not None for value in normal + shear)
        normal_values = tuple(float(value) for value in normal if value is not None)
        shear_values = tuple(float(value) for value in shear if value is not None)
        parameters = HomogeneousStrainParametersV1(
            operator_spec_id="pending-spec",
            deformation_matrix=(
                (
                    1.0 + normal_values[0] / 100.0,
                    shear_values[0] / 100.0,
                    shear_values[1] / 100.0,
                ),
                (
                    shear_values[0] / 100.0,
                    1.0 + normal_values[1] / 100.0,
                    shear_values[2] / 100.0,
                ),
                (
                    shear_values[1] / 100.0,
                    shear_values[2] / 100.0,
                    1.0 + normal_values[2] / 100.0,
                ),
            ),
        )
        spec, parameters = freeze("APPLY_HOMOGENEOUS_STRAIN_V1", parameters)
        plans.append(
            make_operation_plan(
                **common,
                operator_id="APPLY_HOMOGENEOUS_STRAIN_V1",
                operator_spec=spec,
                parameters=parameters,
                preserved_features=(
                    "composition",
                    "site count",
                    "fractional coordinates",
                ),
                changed_features=(
                    f"lattice by DeepSeek-proposed strain {normal_values}/{shear_values} percent",
                ),
            )
        )
    elif proposal.operation_kind == "VACANCY":
        operator_registry.resolve("REMOVE_EQUIVALENT_SITE_CLASS_V1", "1")
        for group in equivalent_site_groups(parent_structure):
            species = {_bare_element(parent_structure[index]) for index in group}
            if len(species) != 1 or None in species:
                continue
            symbol = next(iter(species))
            assert symbol is not None
            if symbol != proposal.vacancy_element:
                continue
            parameters = EquivalentSiteVacancyParametersV1(
                operator_spec_id="pending-spec",
                equivalent_site_indices=group,
                removed_species=symbol,
                maximum_removed_site_fraction=proposal.maximum_removed_site_fraction,
            )
            spec, parameters = freeze("REMOVE_EQUIVALENT_SITE_CLASS_V1", parameters)
            plans.append(
                make_operation_plan(
                    **common,
                    operator_id="REMOVE_EQUIVALENT_SITE_CLASS_V1",
                    operator_spec=spec,
                    parameters=parameters,
                    preserved_features=("lattice", "all unselected sites"),
                    changed_features=(
                        f"remove complete {symbol} equivalence class {group}",
                    ),
                )
            )
    elif proposal.operation_kind == "INTERCALATION":
        operator_registry.resolve("INTERCALATE_REASONED_GAP_SITE_V1", "1")
        midpoint, gap = largest_c_gap(parent_structure)
        assert proposal.minimum_parent_gap_angstrom is not None
        if gap < proposal.minimum_parent_gap_angstrom:
            return ()
        assert (
            proposal.intercalant is not None
            and proposal.intercalant_oxidation_state is not None
            and proposal.intercalation_site_a_fraction is not None
            and proposal.intercalation_site_b_fraction is not None
        )
        xy = (
            proposal.intercalation_site_a_fraction,
            proposal.intercalation_site_b_fraction,
        )
        parameters = ReasonedIntercalationParametersV1(
            operator_spec_id="pending-spec",
            intercalant=proposal.intercalant,
            intercalant_oxidation_state=proposal.intercalant_oxidation_state,
            insertion_frac_coords=(*xy, midpoint),
            minimum_parent_gap_angstrom=proposal.minimum_parent_gap_angstrom,
        )
        spec, parameters = freeze("INTERCALATE_REASONED_GAP_SITE_V1", parameters)
        plans.append(
            make_operation_plan(
                **common,
                operator_id="INTERCALATE_REASONED_GAP_SITE_V1",
                operator_spec=spec,
                parameters=parameters,
                preserved_features=("host lattice", "all host sites"),
                changed_features=(
                    f"insert one {proposal.intercalant} at DeepSeek-proposed in-plane {xy} and derived largest-gap midpoint",
                ),
            )
        )
    elif proposal.operation_kind == "LAYER_SLIDE":
        operator_registry.resolve("SLIDE_REASONED_LAYER_V1", "1")
        groups = layer_groups(parent_structure)
        assert proposal.layer_from_top is not None
        assert proposal.slide_a_fraction is not None
        assert proposal.slide_b_fraction is not None
        if len(groups) < 2 or proposal.layer_from_top > len(groups):
            return ()
        selected_group = groups[-proposal.layer_from_top]
        parameters = ReasonedLayerSlideParametersV1(
            operator_spec_id="pending-spec",
            layer_site_indices=selected_group,
            translation_fractional_ab=(
                proposal.slide_a_fraction,
                proposal.slide_b_fraction,
            ),
            layer_partition_sha256=layer_partition_sha256(groups),
        )
        spec, parameters = freeze("SLIDE_REASONED_LAYER_V1", parameters)
        plans.append(
            make_operation_plan(
                **common,
                operator_id="SLIDE_REASONED_LAYER_V1",
                operator_spec=spec,
                parameters=parameters,
                preserved_features=(
                    "composition",
                    "lattice",
                    "site count",
                    "intralayer geometry",
                ),
                changed_features=(
                    f"translate derived layer {selected_group} by registered vector",
                ),
            )
        )
    elif proposal.operation_kind == "CARRIER_DOPING":
        operator_id = "APPLY_CARRIER_DOPING_V1"
        registry_spec = operator_registry.resolve(operator_id, "1")
        assert proposal.carrier_type is not None
        assert proposal.carriers_per_primitive_cell is not None
        assert proposal.sample_charge_states is not None
        parameters = CarrierDopingParametersV1(
            operator_spec_id="pending-spec",
            carrier_type=proposal.carrier_type,
            carriers_per_primitive_cell=proposal.carriers_per_primitive_cell,
            sample_charge_states=proposal.sample_charge_states,
        )
        spec, parameters = freeze(operator_id, parameters)
        assert isinstance(parameters, CarrierDopingParametersV1)
        prior = evaluate_condition_operation_prior(
            operator_id=operator_id,
            parameters=parameters,
            parent_structure=parent_structure,
        )
        if prior.decision.value == "REJECT":
            raise ConditionPriorRejected(prior)
        plans.append(
            make_condition_plan(
                **common,
                partner_candidate_id=None,
                partner_structure_id=None,
                partner_pointer=None,
                operator_id=operator_id,
                operator_spec=spec,
                parameters=parameters,
                preserved_features=("parent CIF", "composition", "site geometry"),
                changed_features=(
                    f"electronic occupation by {proposal.carriers_per_primitive_cell} {proposal.carrier_type.casefold()} carriers per primitive cell",
                ),
                prior=prior,
                validator_ids=registry_spec.validator_ids,
            )
        )
    elif proposal.operation_kind == "ELECTROSTATIC_GATE":
        operator_id = "APPLY_ELECTROSTATIC_GATE_V1"
        registry_spec = operator_registry.resolve(operator_id, "1")
        assert proposal.electric_field_v_per_angstrom is not None
        assert proposal.field_direction is not None
        assert proposal.minimum_vacuum_angstrom is not None
        parameters = ElectrostaticGateParametersV1(
            operator_spec_id="pending-spec",
            electric_field_v_per_angstrom=proposal.electric_field_v_per_angstrom,
            field_direction=proposal.field_direction,
            minimum_vacuum_angstrom=proposal.minimum_vacuum_angstrom,
        )
        spec, parameters = freeze(operator_id, parameters)
        assert isinstance(parameters, ElectrostaticGateParametersV1)
        prior = evaluate_condition_operation_prior(
            operator_id=operator_id,
            parameters=parameters,
            parent_structure=parent_structure,
        )
        if prior.decision.value == "REJECT":
            raise ConditionPriorRejected(prior)
        plans.append(
            make_condition_plan(
                **common,
                partner_candidate_id=None,
                partner_structure_id=None,
                partner_pointer=None,
                operator_id=operator_id,
                operator_spec=spec,
                parameters=parameters,
                preserved_features=("parent CIF", "composition", "site geometry"),
                changed_features=(
                    f"external field {proposal.electric_field_v_per_angstrom} V/angstrom with dipole correction",
                ),
                prior=prior,
                validator_ids=registry_spec.validator_ids,
            )
        )
    elif proposal.operation_kind in {"MAGNETIC_PROXIMITY", "VDW_HETEROSTRUCTURE"}:
        if (
            partner_database_candidate is None
            or partner_structure is None
            or partner_pointer is None
        ):
            raise ValueError("interface operation is missing its resolved partner")
        assert proposal.partner_database_candidate_id is not None
        assert proposal.interface_separation_angstrom is not None
        assert proposal.relative_twist_degrees is not None
        assert proposal.maximum_lattice_mismatch_percent is not None
        assert proposal.interface_registry is not None
        if proposal.operation_kind == "MAGNETIC_PROXIMITY":
            operator_id = "PLAN_MAGNETIC_PROXIMITY_V1"
            assert proposal.magnetization_alignment is not None
            parameters = MagneticProximityParametersV1(
                operator_spec_id="pending-spec",
                partner_database_candidate_id=(
                    proposal.partner_database_candidate_id
                ),
                interface_separation_angstrom=(
                    proposal.interface_separation_angstrom
                ),
                relative_twist_degrees=proposal.relative_twist_degrees,
                maximum_lattice_mismatch_percent=(
                    proposal.maximum_lattice_mismatch_percent
                ),
                magnetization_alignment=proposal.magnetization_alignment,
                interface_registry=proposal.interface_registry,
            )
            changed = "planned magnetic-proximity interface and alignment scan"
        else:
            operator_id = "PLAN_VDW_HETEROSTRUCTURE_V1"
            assert proposal.maximum_supercell_area_factor is not None
            parameters = VdwHeterostructureParametersV1(
                operator_spec_id="pending-spec",
                partner_database_candidate_id=(
                    proposal.partner_database_candidate_id
                ),
                interface_separation_angstrom=(
                    proposal.interface_separation_angstrom
                ),
                relative_twist_degrees=proposal.relative_twist_degrees,
                maximum_lattice_mismatch_percent=(
                    proposal.maximum_lattice_mismatch_percent
                ),
                maximum_supercell_area_factor=(
                    proposal.maximum_supercell_area_factor
                ),
                interface_registry=proposal.interface_registry,
            )
            changed = "planned commensurate vdW interface construction"
        registry_spec = operator_registry.resolve(operator_id, "1")
        spec, parameters = freeze(operator_id, parameters)
        assert isinstance(
            parameters,
            MagneticProximityParametersV1 | VdwHeterostructureParametersV1,
        )
        prior = evaluate_condition_operation_prior(
            operator_id=operator_id,
            parameters=parameters,
            parent_structure=parent_structure,
            partner_structure=partner_structure,
        )
        if prior.decision.value == "REJECT":
            raise ConditionPriorRejected(prior)
        plans.append(
            make_condition_plan(
                **common,
                partner_candidate_id=(
                    partner_database_candidate.database_candidate_id
                ),
                partner_structure_id=(
                    partner_database_candidate.canonical_structure_id
                ),
                partner_pointer=partner_pointer,
                operator_id=operator_id,
                operator_spec=spec,
                parameters=parameters,
                preserved_features=(
                    "both hash-bound parent CIFs",
                    "both parent compositions",
                    "both parent intralayer geometries",
                ),
                changed_features=(changed,),
                prior=prior,
                validator_ids=registry_spec.validator_ids,
            )
        )
    else:
        raise KeyError(f"unsupported reasoned operation: {proposal.operation_kind}")
    return tuple(plans)


def execution_request_from_compiled_operation_plan(
    plan: StructureOperationPlanV2,
    *,
    operator_registry: SoftChemOperatorRegistryV2 = DEFAULT_SOFTCHEM_OPERATOR_REGISTRY_V2,
) -> StructureOperationExecutionRequestV2:
    payload = canonical_json_bytes(operator_registry)
    return StructureOperationExecutionRequestV2(
        plan=plan,
        operator_registry_artifact=ArtifactPointerV1(
            uri="artifact://softchem/registry/operators-v2.json",
            sha256=hashlib.sha256(payload).hexdigest(),
            size_bytes=len(payload),
            media_type="application/json",
        ),
    )


def execution_request_from_compiled_plan(
    plan: TransformationPlanV1,
    *,
    parent_structure: Structure,
    substitution_registry: SubstitutionRegistryV1 = DEFAULT_SUBSTITUTION_REGISTRY_V1,
) -> SubstitutionExecutionRequestV1:
    """Turn a compiled graph plan into the existing executor's strict request."""

    rule = next(
        (
            item
            for item in substitution_registry.rules
            if item.source_element == plan.parameters.source_species
            and item.target_element == plan.parameters.target_species
        ),
        None,
    )
    if rule is None:
        raise ValueError(
            "compiled plan no longer resolves in the substitution registry"
        )
    registry_payload = substitution_registry_bytes(substitution_registry)
    output_elements = {
        _bare_element(site)
        for site in parent_structure
        if _bare_element(site) is not None
    }
    output_elements.discard(rule.source_element)
    output_elements.add(rule.target_element)
    return SubstitutionExecutionRequestV1(
        plan=plan,
        registry_artifact=ArtifactPointerV1(
            uri="artifact://inspiration/registry/substitutions-v1.json",
            sha256=hashlib.sha256(registry_payload).hexdigest(),
            size_bytes=len(registry_payload),
            media_type="application/json",
        ),
        equivalent_site_groups=equivalent_site_groups(parent_structure),
        allowed_output_elements=tuple(sorted(output_elements)),
    )


class OperatorPlanningToolState:
    """Bounded compiler tool with checkpointable plans and rejection receipts."""

    def __init__(
        self,
        *,
        store: LocalArtifactStore,
        database_candidates_snapshot: Callable[[], tuple[DatabaseCandidateV1, ...]],
        max_calls: int = 16,
        operator_registry: SoftChemOperatorRegistryV2 = (
            DEFAULT_SOFTCHEM_OPERATOR_REGISTRY_V2
        ),
        substitution_registry: SubstitutionRegistryV1 = (
            DEFAULT_SUBSTITUTION_REGISTRY_V1
        ),
    ) -> None:
        if not 1 <= max_calls <= 32:
            raise ValueError("max_calls must be between 1 and 32")
        self.store = store
        self.database_candidates_snapshot = database_candidates_snapshot
        self.max_calls = max_calls
        self.operator_registry = operator_registry
        self.substitution_registry = substitution_registry
        self._calls = 0
        self._plans: dict[
            str,
            TransformationPlanV1
            | StructureOperationPlanV2
            | ReasonedConditionPlanV1,
        ] = {}
        self._bindings: dict[str, TransformationPlanBindingV1] = {}
        self._rejections: list[TransformationCompileRejectionV3] = []
        self._lock = threading.Lock()

    def as_tool(self) -> DeepSeekFunctionTool:
        return DeepSeekFunctionTool(
            name="compile_reasoned_operation",
            description=(
                "Propose a material-specific minimal operation using native scientific "
                "reasoning. Choose substitution/oxidation, strain tensor, vacancy element/"
                "fraction, intercalant/in-plane site/gap, layer/vector, carrier doping, "
                "electrostatic gating, magnetic proximity, or a two-parent vdW interface; "
                "provide mechanism, chemistry prior, "
                "and falsifier. Local code freezes a run-local hash-pinned spec, derives "
                "sites/coordinates from the CIF, and applies safety/chemistry validators. "
                "Set fields for other operation kinds to null. No fixed scientific rule "
                "list, unvalidated coordinates, or code is accepted."
            ),
            arguments_model=CompileReasonedOperationArgsV3,
            handler=self._handle,
        )

    def audit_snapshot(self) -> RegisteredTransformationAuditV3:
        with self._lock:
            return RegisteredTransformationAuditV3(
                registry_status="HASH_PINNED",
                operator_registry_sha256=softchem_registry_sha256(
                    self.operator_registry
                ),
                substitution_registry_sha256=substitution_registry_sha256(
                    self.substitution_registry
                ),
                compile_attempt_count=self._calls,
                plans=tuple(
                    sorted(self._plans.values(), key=lambda item: item.plan_id)
                ),
                bindings=tuple(
                    sorted(self._bindings.values(), key=lambda item: item.plan_id)
                ),
                rejections=tuple(self._rejections),
            )

    def checkpoint_snapshot(self) -> Mapping[str, Any]:
        return self.audit_snapshot().model_dump(mode="json")

    def restore_snapshot(self, value: object) -> None:
        audit = RegisteredTransformationAuditV3.model_validate_json(
            canonical_json_bytes(value)
        )
        if audit.registry_status != "HASH_PINNED":
            raise ValueError("operator-planning checkpoint must bind registries")
        if audit.operator_registry_sha256 != softchem_registry_sha256(
            self.operator_registry
        ) or audit.substitution_registry_sha256 != substitution_registry_sha256(
            self.substitution_registry
        ):
            raise ValueError("operator-planning checkpoint registry hash mismatch")
        with self._lock:
            if self._calls or self._plans or self._bindings or self._rejections:
                raise ValueError("operator-planning state must be empty before restore")
            self._calls = audit.compile_attempt_count
            self._plans = {item.plan_id: item for item in audit.plans}
            self._bindings = {item.plan_id: item for item in audit.bindings}
            self._rejections = list(audit.rejections)

    def _handle(
        self,
        arguments: CompileReasonedOperationArgsV3
        | CompileRegisteredSubstitutionArgsV1,
    ) -> Mapping[str, Any]:
        with self._lock:
            if self._calls >= self.max_calls:
                raise LLMProviderError(
                    "BUDGET_EXHAUSTED",
                    "operator compiler exhausted its call budget",
                    retryable=False,
                )
            self._calls += 1
        candidates = {
            item.database_candidate_id: item
            for item in self.database_candidates_snapshot()
        }
        parent = candidates.get(arguments.database_candidate_id)
        if parent is None:
            return self._reject(arguments, "UNKNOWN_DATABASE_CANDIDATE")
        partner = (
            candidates.get(arguments.partner_database_candidate_id)
            if isinstance(arguments, CompileReasonedOperationArgsV3)
            and arguments.partner_database_candidate_id is not None
            else None
        )
        if (
            isinstance(arguments, CompileReasonedOperationArgsV3)
            and arguments.operation_kind
            in {"MAGNETIC_PROXIMITY", "VDW_HETEROSTRUCTURE"}
            and partner is None
        ):
            return self._reject(arguments, "UNKNOWN_PARTNER_DATABASE_CANDIDATE")
        legacy_substitution = isinstance(arguments, CompileRegisteredSubstitutionArgsV1)
        operation_kind = "SUBSTITUTION" if legacy_substitution else arguments.operation_kind
        rule_id = arguments.substitution_rule_id if legacy_substitution else None
        if legacy_substitution and not any(
            item.rule_id == rule_id for item in self.substitution_registry.rules
        ):
            return self._reject(arguments, "UNREGISTERED_SUBSTITUTION_RULE")
        try:
            payload = self.store.read_bytes(parent.structure_artifact_uri)
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                structure = Structure.from_str(payload.decode("utf-8"), fmt="cif")
            partner_payload = None
            partner_structure = None
            if partner is not None:
                partner_payload = self.store.read_bytes(
                    partner.structure_artifact_uri
                )
                with warnings.catch_warnings():
                    warnings.simplefilter("ignore")
                    partner_structure = Structure.from_str(
                        partner_payload.decode("utf-8"), fmt="cif"
                    )
            if legacy_substitution:
                plans = compile_registered_substitution_plans(
                    candidate_id=arguments.candidate_id,
                    database_candidate=parent,
                    parent_structure=structure,
                    parent_artifact_bytes=payload,
                    substitution_rule_id=rule_id,
                    operator_registry=self.operator_registry,
                    substitution_registry=self.substitution_registry,
                )
            else:
                plans = compile_reasoned_structure_operation_plans(
                    database_candidate=parent,
                    parent_structure=structure,
                    parent_artifact_bytes=payload,
                    proposal=arguments,
                    partner_database_candidate=partner,
                    partner_structure=partner_structure,
                    partner_artifact_bytes=partner_payload,
                    operator_registry=self.operator_registry,
                )
        except ConditionPriorRejected as exc:
            return self._reject(
                arguments,
                "OPERATION_PRIOR_REJECTED",
                detail=",".join(exc.prior.reason_codes),
            )
        except (KeyError, UnicodeDecodeError, ValueError) as exc:
            return self._reject(
                arguments,
                "PARENT_OR_ROUTE_INTEGRITY_FAILURE",
                detail=type(exc).__name__,
            )
        if not plans:
            reason = {
                "LAYER_SLIDE": "NO_MULTILAYER_PARTITION",
                "INTERCALATION": "NO_QUALIFYING_VDW_GAP",
            }.get(operation_kind, "NO_COMPLETE_EQUIVALENCE_CLASS")
            return self._reject(arguments, reason)
        compile_priors: dict[str, Mapping[str, Any]] = {}
        accepted_plans: list[
            TransformationPlanV1
            | StructureOperationPlanV2
            | ReasonedConditionPlanV1
        ] = []
        rejected_prior_reasons: list[str] = []
        try:
            for plan in plans:
                if isinstance(plan, StructureOperationPlanV2):
                    preview = preview_structure_operation(plan, structure)
                    prior = evaluate_structure_operation_prior(
                        plan,
                        parent_structure=structure,
                        proposed_structure=preview,
                    )
                    compile_priors[plan.plan_id] = prior.model_dump(mode="json")
                    if prior.decision.value == "REJECT":
                        rejected_prior_reasons.extend(prior.reason_codes)
                        continue
                    spec = self.operator_registry.resolve(
                        plan.operator_id, plan.operator_version
                    )
                    plan = bind_compile_prior(
                        plan,
                        prior,
                        validator_ids=spec.validator_ids,
                    )
                elif isinstance(plan, ReasonedConditionPlanV1):
                    compile_priors[plan.plan_id] = {
                        "decision": plan.compile_prior_decision,
                        "reason_codes": plan.compile_prior_reason_codes,
                        "evidence_level": "INPUT_AND_GEOMETRY_PRIOR_ONLY",
                        "scientific_conclusion": False,
                    }
                accepted_plans.append(plan)
        except (KeyError, TypeError, ValueError) as exc:
            return self._reject(
                arguments,
                "OPERATION_PREFLIGHT_INTEGRITY_FAILURE",
                detail=type(exc).__name__,
            )
        plans = tuple(accepted_plans)
        if not plans:
            return self._reject(
                arguments,
                "OPERATION_PRIOR_REJECTED",
                detail=",".join(sorted(set(rejected_prior_reasons))),
            )
        with self._lock:
            self._plans.update((item.plan_id, item) for item in plans)
            self._bindings.update(
                (
                    item.plan_id,
                    TransformationPlanBindingV1(
                        candidate_id=arguments.candidate_id,
                        plan_id=item.plan_id,
                    ),
                )
                for item in plans
            )
        return {
            "status": "COMPILED",
            "candidate_id": arguments.candidate_id,
            "database_candidate_id": arguments.database_candidate_id,
            "operator_registry_sha256": softchem_registry_sha256(
                self.operator_registry
            ),
            "substitution_registry_sha256": substitution_registry_sha256(
                self.substitution_registry
            ),
            "plans": tuple(item.model_dump(mode="json") for item in plans),
            "compile_priors": compile_priors,
            "execution_boundary": (
                "SPECIALIZED_COMPUTATION_OR_INTERFACE_BUILDER_REQUIRED"
                if any(isinstance(item, ReasonedConditionPlanV1) for item in plans)
                else "PLANNED_NOT_EXECUTED_OR_PROPERTY_VERIFIED"
            ),
        }

    def _reject(
        self,
        arguments: CompileReasonedOperationArgsV3
        | CompileRegisteredSubstitutionArgsV1,
        reason_code: str,
        *,
        detail: str | None = None,
    ) -> Mapping[str, Any]:
        rejection = TransformationCompileRejectionV3(
            candidate_id=arguments.candidate_id,
            database_candidate_id=arguments.database_candidate_id,
            operation_proposal_id=(
                arguments.substitution_rule_id
                if isinstance(arguments, CompileRegisteredSubstitutionArgsV1)
                else f"reasoned-{arguments.operation_kind.casefold()}"
            ),
            reason_code=reason_code,
        )
        with self._lock:
            self._rejections.append(rejection)
        return {
            "status": "REJECTED",
            "reason_code": reason_code,
            "detail": detail,
            "available_execution_kernels": tuple(
                item.operator_id for item in self.operator_registry.operators
            ),
            "scientific_conclusion": False,
        }


def planning_state_sha256(state: OperatorPlanningToolState) -> str:
    """Stable helper for audit tests and downstream cache keys."""

    return hashlib.sha256(canonical_json_bytes(state.checkpoint_snapshot())).hexdigest()
