from __future__ import annotations

import copy

import pytest

from material_agent.retrieval.structures import (
    StructureValidationError,
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

