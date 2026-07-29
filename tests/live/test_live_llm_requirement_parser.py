from __future__ import annotations

import os

import pytest

from material_agent.orchestrator.parser import (
    LLMRequirementParser,
    requirement_parser_from_environment,
)
from material_agent.retrieval.models import Requirement


pytestmark = pytest.mark.live_llm

ACCEPTANCE_REQUEST = (
    "从 Materials Project 中寻找同时包含 Si 和 O、带隙为 0.5–1.0 eV、"
    "energy above hull 不超过 0.05 eV/atom 的非金属材料。"
)


def test_live_deepseek_stage0_requirement_parser() -> None:
    assert os.environ.get("MATERIAL_AGENT_LLM_PROVIDER") == "deepseek", (
        "set MATERIAL_AGENT_LLM_PROVIDER=deepseek before opting into this test"
    )

    parser = requirement_parser_from_environment()
    assert isinstance(parser, LLMRequirementParser)

    parsed = parser.parse(ACCEPTANCE_REQUEST, "req-live-llm")
    requirement = Requirement.model_validate(parsed.requirement)

    assert parsed.clarification_questions == []
    assert requirement.requirement_id == "req-live-llm"
    assert requirement.revision == 1
    assert requirement.confirmed is False
    assert requirement.policy_version == "requirement-policy-v1"
    hard = requirement.hard_constraints
    assert hard.include_elements == ["O", "Si"]
    assert hard.band_gap_ev is not None
    assert hard.band_gap_ev.min == 0.5
    assert hard.band_gap_ev.max == 1.0
    assert hard.band_gap_ev.unit == "eV"
    assert hard.energy_above_hull_ev_atom is not None
    assert hard.energy_above_hull_ev_atom.max == 0.05
    assert hard.energy_above_hull_ev_atom.unit == "eV/atom"
    assert hard.is_metal is False

    assert parsed.llm_audit is not None
    assert parsed.llm_audit.provider == "deepseek"
    assert parsed.llm_audit.model_id == "deepseek-v4-pro"
    assert parsed.llm_audit.response_format == "json_object"
