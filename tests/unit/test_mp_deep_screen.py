from material_agent.retrieval.mp_deep_screen import common_transition_metal_valence


def test_integer_common_average_oxidation_state_is_usable_evidence() -> None:
    result = common_transition_metal_valence(
        {"average_oxidation_states": {"Mo": 6.0, "O": -2.0}},
        elements=["Mo", "O"],
    )

    assert result.value is True
    assert result.status == "RESOLVED"
    assert result.method == "mp_oxidation_states_average"


def test_fractional_average_does_not_claim_mixed_valence_proof() -> None:
    result = common_transition_metal_valence(
        {"average_oxidation_states": {"Fe": 2.5, "O": -2.0}},
        elements=["Fe", "O"],
    )

    assert result.value is None
    assert result.status == "MISSING"
