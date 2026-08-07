from __future__ import annotations

import hashlib
import warnings
from dataclasses import fields

import pytest
from pydantic import ValidationError
from pymatgen.analysis.structure_matcher import SpeciesComparator, StructureMatcher
from pymatgen.core import Lattice, Structure
from pymatgen.io.cif import CifWriter

from material_agent.inspiration import (
    ArtifactPointerV1,
    SubstitutionParametersV1,
    TransformationPlanV1,
    TransformationStatus,
    ValidationStatus,
    deterministic_id,
    transformation_route_sha256,
)
from material_agent.inspiration.transformations import (
    DEFAULT_SUBSTITUTION_REGISTRY_V1,
    STRUCTURE_ARTIFACT_MEDIA_TYPE,
    SubstitutionExecutionRequestV1,
    SubstitutionRegistryV1,
    SubstitutionRuleV1,
    TransformationExecutionResult,
    TransformationIntegrityError,
    execute_equivalent_site_substitution,
    substitution_registry_bytes,
    substitution_registry_sha256,
)


def _cif_bytes(structure: Structure) -> bytes:
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


def _parent(*, charged: bool = False) -> Structure:
    species = ("Ti4+", "S2-", "S2-") if charged else ("Ti", "S", "S")
    return Structure(
        Lattice.hexagonal(3.4, 6.0),
        species,
        (
            (0.0, 0.0, 0.0),
            (1.0 / 3.0, 2.0 / 3.0, 0.25),
            (2.0 / 3.0, 1.0 / 3.0, 0.75),
        ),
    )


def _parent_pointer(parent_bytes: bytes) -> ArtifactPointerV1:
    return ArtifactPointerV1(
        uri="artifact://retrieval/structures/parent.cif",
        sha256=hashlib.sha256(parent_bytes).hexdigest(),
        size_bytes=len(parent_bytes),
        media_type=STRUCTURE_ARTIFACT_MEDIA_TYPE,
    )


def _planned(
    parent_pointer: ArtifactPointerV1,
    *,
    indices: tuple[int, ...] = (1, 2),
    source: str = "S",
    target: str = "Se",
) -> TransformationPlanV1:
    parameters = SubstitutionParametersV1(
        equivalent_site_indices=indices,
        source_species=source,
        target_species=target,
    )
    route_sha256 = transformation_route_sha256(
        parent_structure_id="structure-parent",
        operator_id="SUBSTITUTE_EQUIVALENT_SITE_V1",
        operator_version="1",
        parameters=parameters,
    )
    return TransformationPlanV1(
        plan_id=deterministic_id("plan", {"route": route_sha256}),
        parent_candidate_id="candidate-parent",
        parent_structure_id="structure-parent",
        parent_structure_artifact=parent_pointer,
        parameters=parameters,
        preserved_features=("ordered lattice and fractional coordinates",),
        changed_features=("complete equivalent-site species class",),
        falsification_tests=("review charge then compute the band structure",),
        bridge_packet_ids=("bridge-1",),
        route_sha256=route_sha256,
        status=TransformationStatus.PLANNED,
    )


def _registry_pointer(
    registry: SubstitutionRegistryV1 = DEFAULT_SUBSTITUTION_REGISTRY_V1,
) -> ArtifactPointerV1:
    payload = substitution_registry_bytes(registry)
    return ArtifactPointerV1(
        uri="artifact://inspiration/registry/substitutions-v1.json",
        sha256=hashlib.sha256(payload).hexdigest(),
        size_bytes=len(payload),
        media_type="application/json",
    )


def _request(
    plan: TransformationPlanV1,
    *,
    registry: SubstitutionRegistryV1 = DEFAULT_SUBSTITUTION_REGISTRY_V1,
    groups: tuple[tuple[int, ...], ...] = ((0,), (1, 2)),
    allowed_elements: tuple[str, ...] = ("Se", "Ti"),
    max_sites: int = 256,
    minimum_distance: float = 0.5,
) -> SubstitutionExecutionRequestV1:
    return SubstitutionExecutionRequestV1(
        plan=plan,
        registry_artifact=_registry_pointer(registry),
        equivalent_site_groups=groups,
        allowed_output_elements=allowed_elements,
        allowed_output_dimensionalities=(0, 1, 2, 3),
        max_sites=max_sites,
        minimum_distance_angstrom=minimum_distance,
    )


def _execute(
    *,
    charged: bool = False,
    indices: tuple[int, ...] = (1, 2),
    source: str = "S",
    target: str = "Se",
    groups: tuple[tuple[int, ...], ...] = ((0,), (1, 2)),
    allowed_elements: tuple[str, ...] = ("Se", "Ti"),
    max_sites: int = 256,
    minimum_distance: float = 0.5,
) -> tuple[
    TransformationExecutionResult,
    Structure,
    bytes,
    SubstitutionExecutionRequestV1,
]:
    parent = _parent(charged=charged)
    parent_bytes = _cif_bytes(parent)
    plan = _planned(
        _parent_pointer(parent_bytes),
        indices=indices,
        source=source,
        target=target,
    )
    request = _request(
        plan,
        groups=groups,
        allowed_elements=allowed_elements,
        max_sites=max_sites,
        minimum_distance=minimum_distance,
    )
    result = execute_equivalent_site_substitution(
        request,
        parent_structure=parent,
        parent_artifact_bytes=parent_bytes,
        registry=DEFAULT_SUBSTITUTION_REGISTRY_V1,
    )
    return result, parent, parent_bytes, request


def _checks(result: TransformationExecutionResult) -> dict[str, ValidationStatus]:
    return {
        check.check_id: check.status for check in result.plan.validation_checks
    }


def test_bare_species_requires_review_and_never_emits_executable_candidate() -> None:
    result, parent, _, _ = _execute()

    assert result.plan.status is TransformationStatus.REQUIRES_REVIEW
    assert result.candidate_selection_eligible is False
    assert not hasattr(result, "candidate")
    assert result.output_structure is not None
    assert result.artifact_bytes is not None
    assert parent.composition.element_composition.as_dict() == {"Ti": 1.0, "S": 2.0}
    assert result.output_structure.composition.element_composition.as_dict() == {
        "Ti": 1.0,
        "Se": 2.0,
    }
    assert all(
        site.specie.symbol == "Se"
        for site in result.output_structure
        if site.specie.symbol != "Ti"
    )

    checks = _checks(result)
    expected_passes = {
        "parent_hash_verified",
        "operator_allowed",
        "source_species_present",
        "target_species_valid",
        "equivalent_sites_complete",
        "requirement_composition",
        "ordered_occupancy",
        "finite_structure",
        "positive_volume",
        "site_count_preserved",
        "lattice_preserved",
        "coordinates_preserved",
        "minimum_distance",
        "canonicalization",
        "canonical_round_trip",
        "dimensionality_and_site_budget",
        "retrieval_structure_processing",
    }
    assert all(checks[check_id] is ValidationStatus.PASS for check_id in expected_passes)
    assert checks["charge_or_oxidation"] is ValidationStatus.UNKNOWN


def test_explicit_allowed_oxidation_state_can_be_structure_valid() -> None:
    result, _, _, _ = _execute(charged=True)

    assert result.plan.status is TransformationStatus.STRUCTURE_VALID
    assert result.candidate_selection_eligible is True
    assert _checks(result)["charge_or_oxidation"] is ValidationStatus.PASS
    assert result.output_structure is not None
    substituted = [
        site.specie
        for site in result.output_structure
        if site.specie.symbol == "Se"
    ]
    assert len(substituted) == 2
    assert all(species.oxi_state == -2.0 for species in substituted)


def test_explicit_non_neutral_output_is_rejected() -> None:
    parent = Structure(
        Lattice.hexagonal(3.4, 6.0),
        ("Ti3+", "S2-", "S2-"),
        (
            (0.0, 0.0, 0.0),
            (1.0 / 3.0, 2.0 / 3.0, 0.25),
            (2.0 / 3.0, 1.0 / 3.0, 0.75),
        ),
    )
    parent_bytes = _cif_bytes(parent)
    request = _request(_planned(_parent_pointer(parent_bytes)))

    result = execute_equivalent_site_substitution(
        request,
        parent_structure=parent,
        parent_artifact_bytes=parent_bytes,
        registry=DEFAULT_SUBSTITUTION_REGISTRY_V1,
    )

    assert result.plan.status is TransformationStatus.REJECTED
    assert result.output_structure is None
    assert result.artifact_bytes is None
    assert _checks(result)["charge_or_oxidation"] is ValidationStatus.FAIL


def test_caller_cannot_forge_the_equivalent_site_partition() -> None:
    parent = Structure(
        Lattice.from_parameters(3.4, 4.1, 6.3, 81.0, 92.0, 103.0),
        ("Ti", "S", "S"),
        ((0.0, 0.0, 0.0), (0.13, 0.27, 0.31), (0.41, 0.18, 0.73)),
    )
    parent_bytes = _cif_bytes(parent)
    request = _request(_planned(_parent_pointer(parent_bytes)))

    result = execute_equivalent_site_substitution(
        request,
        parent_structure=parent,
        parent_artifact_bytes=parent_bytes,
        registry=DEFAULT_SUBSTITUTION_REGISTRY_V1,
    )

    assert result.plan.status is TransformationStatus.REJECTED
    assert _checks(result)["equivalent_sites_complete"] is ValidationStatus.FAIL


def test_artifact_bytes_hash_round_trip_and_route_replay_are_deterministic() -> None:
    first, parent, parent_bytes, request = _execute()
    second = execute_equivalent_site_substitution(
        request,
        parent_structure=parent.copy(),
        parent_artifact_bytes=bytes(parent_bytes),
        registry=DEFAULT_SUBSTITUTION_REGISTRY_V1,
    )

    assert first.plan == second.plan
    assert first.artifact_bytes == second.artifact_bytes
    assert first.plan.route_sha256 == request.plan.route_sha256
    assert first.plan.bridge_packet_ids == request.plan.bridge_packet_ids
    assert first.plan.parameters == request.plan.parameters
    assert first.plan.output_structure_artifact is not None
    assert first.artifact_bytes is not None
    assert first.plan.output_structure_artifact.sha256 == hashlib.sha256(
        first.artifact_bytes
    ).hexdigest()
    assert first.plan.output_structure_id is not None
    assert first.plan.output_structure_id.startswith("str_")
    assert first.plan.output_structure_artifact.sha256 == (
        "662d5a8dddafd523380f56e2c42e60dbf5631b81c2853997c17cf2588e2570e6"
    )
    assert first.plan.output_structure_artifact.size_bytes == len(first.artifact_bytes)
    assert substitution_registry_sha256(DEFAULT_SUBSTITUTION_REGISTRY_V1) == (
        request.registry_artifact.sha256
    )
    assert request.registry_artifact.sha256 == (
        "0df10f816808f075bd2e571b018b46a1e1cf0831b73d4be58782e679625cd4fb"
    )

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        restored = Structure.from_str(
            first.artifact_bytes.decode("utf-8"),
            fmt="cif",
        )
    assert first.output_structure is not None
    matcher = StructureMatcher(
        ltol=1e-6,
        stol=1e-5,
        angle_tol=1e-5,
        primitive_cell=False,
        scale=False,
        attempt_supercell=False,
        comparator=SpeciesComparator(),
    )
    assert matcher.fit(first.output_structure, restored)


@pytest.mark.parametrize(
    ("kwargs", "failed_check"),
    [
        ({"indices": (1,), "groups": ((0,), (1, 2))}, "equivalent_sites_complete"),
        ({"indices": (0,), "groups": ((0,), (1, 2))}, "source_species_present"),
        (
            {
                "target": "O",
                "allowed_elements": ("O", "Ti"),
            },
            "target_species_valid",
        ),
        ({"allowed_elements": ("Ti",)}, "requirement_composition"),
        ({"minimum_distance": 3.0}, "minimum_distance"),
        ({"max_sites": 2}, "dimensionality_and_site_budget"),
    ],
)
def test_invalid_or_out_of_policy_transformations_are_rejected(
    kwargs: dict[str, object],
    failed_check: str,
) -> None:
    result, _, _, _ = _execute(**kwargs)  # type: ignore[arg-type]

    assert result.plan.status is TransformationStatus.REJECTED
    assert result.output_structure is None
    assert result.artifact_bytes is None
    assert result.candidate_selection_eligible is False
    assert _checks(result)[failed_check] is ValidationStatus.FAIL


def test_parent_and_registry_hash_tampering_fail_before_execution() -> None:
    parent = _parent()
    parent_bytes = _cif_bytes(parent)
    plan = _planned(_parent_pointer(parent_bytes))
    request = _request(plan)

    with pytest.raises(TransformationIntegrityError) as parent_error:
        execute_equivalent_site_substitution(
            request,
            parent_structure=parent,
            parent_artifact_bytes=parent_bytes + b"tamper",
            registry=DEFAULT_SUBSTITUTION_REGISTRY_V1,
        )
    assert parent_error.value.code == "PARENT_HASH_MISMATCH"

    bad_registry_pointer = request.registry_artifact.model_copy(
        update={"sha256": "f" * 64}
    )
    bad_request = SubstitutionExecutionRequestV1.model_validate(
        {
            **request.model_dump(mode="python"),
            "registry_artifact": bad_registry_pointer,
        }
    )
    with pytest.raises(TransformationIntegrityError) as registry_error:
        execute_equivalent_site_substitution(
            bad_request,
            parent_structure=parent,
            parent_artifact_bytes=parent_bytes,
            registry=DEFAULT_SUBSTITUTION_REGISTRY_V1,
        )
    assert registry_error.value.code == "REGISTRY_HASH_MISMATCH"


def test_registry_and_equivalence_inputs_are_strict_and_versioned() -> None:
    with pytest.raises(ValidationError):
        SubstitutionRuleV1(
            rule_id="invalid-rule",
            source_element="S",
            target_element="not-an-element",
        )
    with pytest.raises(ValidationError):
        SubstitutionRegistryV1(
            rules=tuple(reversed(DEFAULT_SUBSTITUTION_REGISTRY_V1.rules))
        )

    parent = _parent()
    parent_bytes = _cif_bytes(parent)
    plan = _planned(_parent_pointer(parent_bytes))
    with pytest.raises(ValidationError):
        _request(plan, groups=((0, 1), (1, 2)))

    assert DEFAULT_SUBSTITUTION_REGISTRY_V1.operator_id == (
        "SUBSTITUTE_EQUIVALENT_SITE_V1"
    )
    assert DEFAULT_SUBSTITUTION_REGISTRY_V1.operator_version == "1"


def test_transformation_execution_types_have_no_prohibited_claim_field() -> None:
    dataclass_field_names = {
        field.name.casefold() for field in fields(TransformationExecutionResult)
    }
    model_field_names = {
        name.casefold()
        for model in (
            SubstitutionRuleV1,
            SubstitutionRegistryV1,
            SubstitutionExecutionRequestV1,
        )
        for name in model.model_fields
    }
    assert all("novel" not in name for name in dataclass_field_names | model_field_names)
    assert all("prior_art" not in name for name in dataclass_field_names | model_field_names)
