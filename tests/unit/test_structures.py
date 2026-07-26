from __future__ import annotations

import copy

import pytest
from pymatgen.core import Lattice, Structure

from material_agent.retrieval.structures import (
    StructureValidationError,
    calculate_dimensionality,
    process_structure,
)


def test_structure_id_is_invariant_to_site_order(fixture_payload, policy) -> None:
    document = fixture_payload["documents"][0]
    original = process_structure(
        document["structure"],
        summary_elements=document["elements"],
        summary_num_sites=document["nsites"],
        policy=policy,
    )
    reordered_payload = copy.deepcopy(document["structure"])
    reordered_payload["sites"].reverse()
    reordered = process_structure(
        reordered_payload,
        summary_elements=document["elements"],
        summary_num_sites=document["nsites"],
        policy=policy,
    )
    assert original.structure_id == reordered.structure_id
    assert original.canonical_payload == reordered.canonical_payload


def test_structure_validation_rejects_missing_structure(policy) -> None:
    with pytest.raises(StructureValidationError):
        process_structure(
            None,
            summary_elements=["Si", "O"],
            summary_num_sites=2,
            policy=policy,
        )


def test_summary_mismatch_is_a_quality_flag(fixture_payload, policy) -> None:
    document = fixture_payload["documents"][0]
    result = process_structure(
        document["structure"],
        summary_elements=["Si"],
        summary_num_sites=99,
        policy=policy,
    )
    assert "SOURCE_ELEMENT_SET_MISMATCH" in result.data_quality_flags
    assert "SOURCE_NSITES_MISMATCH" in result.data_quality_flags


def test_summary_formula_mismatch_uses_structure_formula(
    fixture_payload, policy
) -> None:
    document = fixture_payload["documents"][0]
    result = process_structure(
        document["structure"],
        summary_elements=document["elements"],
        summary_num_sites=document["nsites"],
        summary_formula="C",
        policy=policy,
    )
    assert "SOURCE_FORMULA_MISMATCH" in result.data_quality_flags
    assert result.reduced_formula != "C"


def test_canonical_payload_uses_active_policy_version(
    fixture_payload, policy
) -> None:
    document = fixture_payload["documents"][0]
    changed_policy = policy.model_copy(
        update={"canonicalization_policy_version": "canonical-structure-test-v2"}
    )
    result = process_structure(
        document["structure"],
        summary_elements=document["elements"],
        summary_num_sites=document["nsites"],
        policy=changed_policy,
    )
    assert result.canonical_payload["policy_version"] == "canonical-structure-test-v2"


def test_canonical_cif_round_trip_preserves_structure(
    fixture_payload, policy
) -> None:
    document = fixture_payload["documents"][0]
    result = process_structure(
        document["structure"],
        summary_elements=document["elements"],
        summary_num_sites=document["nsites"],
        policy=policy,
    )
    restored = Structure.from_str(result.cif_text, fmt="cif")
    assert len(restored) == result.num_sites
    assert restored.composition == result.structure.composition


@pytest.mark.parametrize(
    "mutation",
    ["non_finite_coordinate", "invalid_occupancy", "zero_volume_lattice"],
)
def test_structure_validation_rejects_invalid_payload(
    fixture_payload, policy, mutation
) -> None:
    payload = copy.deepcopy(fixture_payload["documents"][0]["structure"])
    if mutation == "non_finite_coordinate":
        payload["sites"][0]["abc"][0] = float("nan")
    elif mutation == "invalid_occupancy":
        payload["sites"][0]["species"][0]["occu"] = 1.2
    else:
        payload["lattice"]["matrix"][2] = payload["lattice"]["matrix"][1]
    with pytest.raises(StructureValidationError):
        process_structure(
            payload,
            summary_elements=["O", "Si"],
            summary_num_sites=3,
            policy=policy,
        )


def test_different_polymorph_payloads_have_different_structure_ids(
    fixture_payload, policy
) -> None:
    document = fixture_payload["documents"][0]
    changed_payload = copy.deepcopy(document["structure"])
    changed_payload["sites"][0]["abc"][0] += 0.1
    first = process_structure(
        document["structure"],
        summary_elements=document["elements"],
        summary_num_sites=document["nsites"],
        policy=policy,
    )
    second = process_structure(
        changed_payload,
        summary_elements=document["elements"],
        summary_num_sites=document["nsites"],
        policy=policy,
    )
    assert first.structure_id != second.structure_id


@pytest.mark.parametrize(
    ("expected", "structure"),
    [
        (0, Structure(Lattice.cubic(20), ["He"], [[0.5, 0.5, 0.5]])),
        (1, Structure(Lattice.orthorhombic(1.4, 15, 15), ["C"], [[0, 0, 0]])),
        (
            2,
            Structure(
                Lattice.hexagonal(2.46, 15),
                ["C", "C"],
                [[0, 0, 0], [1 / 3, 2 / 3, 0]],
            ),
        ),
        (
            3,
            Structure.from_spacegroup(
                "Fd-3m", Lattice.cubic(5.43), ["Si"], [[0, 0, 0]]
            ),
        ),
    ],
    ids=["0d-molecule", "1d-chain", "2d-layer", "3d-network"],
)
def test_fixed_dimensionality_fixtures(expected: int, structure: Structure) -> None:
    result = calculate_dimensionality(structure)
    assert result.error is None
    assert result.value == expected


def test_nonorthogonal_cif_round_trip_allows_equivalent_lattice_orientation(
    policy,
) -> None:
    structure = Structure(
        Lattice(
            [
                [5.578818, 0.0, 0.0],
                [0.0, 5.539545, 0.0],
                [0.0, 5.419927, 7.030953],
            ]
        ),
        ["Si", "O", "O"],
        [[0.0, 0.0, 0.0], [0.25, 0.25, 0.25], [0.75, 0.75, 0.75]],
    )
    result = process_structure(
        structure.as_dict(),
        summary_elements=["O", "Si"],
        summary_num_sites=3,
        summary_formula="SiO2",
        policy=policy,
    )
    restored = Structure.from_str(result.cif_text, fmt="cif")
    assert restored.lattice.abc == pytest.approx(structure.lattice.abc, abs=1e-6)
    assert restored.lattice.angles == pytest.approx(
        structure.lattice.angles, abs=1e-6
    )
