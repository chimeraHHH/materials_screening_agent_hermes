from __future__ import annotations

import pytest

from material_agent.orchestrator.parser import OfflineRequirementParser
from material_agent.retrieval.models import Requirement


ACCEPTANCE_REQUEST = (
    "从 Materials Project 中寻找同时包含 Si 和 O、带隙为 0.5–1.0 eV、"
    "energy above hull 不超过 0.05 eV/atom 的非金属材料。"
)


def test_offline_parser_parses_fixed_acceptance_request() -> None:
    parsed = OfflineRequirementParser().parse(ACCEPTANCE_REQUEST, "req-test")
    requirement = Requirement.model_validate(parsed.requirement)

    assert parsed.clarification_questions == []
    assert requirement.hard_constraints.include_elements == ["O", "Si"]
    assert requirement.hard_constraints.band_gap_ev.min == 0.5
    assert requirement.hard_constraints.band_gap_ev.max == 1.0
    assert requirement.hard_constraints.energy_above_hull_ev_atom.max == 0.05
    assert requirement.hard_constraints.is_metal is False
    assert requirement.confirmed_by_user is False


def test_offline_parser_does_not_guess_missing_scientific_constraints() -> None:
    parsed = OfflineRequirementParser().parse(
        "帮我寻找一些有趣的材料", "req-test"
    )

    assert len(parsed.clarification_questions) == 4
    assert parsed.requirement["hard_constraints"]["include_elements"] == []
    assert parsed.requirement["hard_constraints"]["band_gap_ev"] is None


def test_clarification_response_requires_structured_changes() -> None:
    parser = OfflineRequirementParser()
    parsed = parser.parse("帮我寻找材料", "req-test")

    with pytest.raises(ValueError, match="requirement.*changes"):
        parser.apply_response(parsed.requirement, {"answer": "SiO2"})
