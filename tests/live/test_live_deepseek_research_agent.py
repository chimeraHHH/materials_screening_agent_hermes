from __future__ import annotations

from typing import Literal

import pytest

from material_agent.inspiration.deepseek_agent import (
    DeepSeekAgentBudgetV1,
    DeepSeekFunctionTool,
    DeepSeekThinkingAgent,
)
from material_agent.inspiration.native_search import DeepSeekNativeSearchDiscovery
from material_agent.integration.generic_research import (
    research_secret_resolver_from_environment,
)
from material_agent.orchestrator.models import StrictModel

pytestmark = pytest.mark.live_llm


class ProbeArgs(StrictModel):
    phase: Literal["first", "second"]


class ProbeFinal(StrictModel):
    status: Literal["MULTI_ROUND_TOOL_LOOP_CONFIRMED"]
    observed_phases: tuple[Literal["first", "second"], ...]


def test_live_deepseek_max_thinking_multiround_strict_tools() -> None:
    phases: list[str] = []

    def handle(arguments: ProbeArgs):
        expected = "first" if not phases else "second"
        if arguments.phase != expected or len(phases) >= 2:
            raise ValueError("provider did not follow the bounded probe sequence")
        phases.append(arguments.phase)
        return {
            "accepted_phase": arguments.phase,
            "next_required_phase": "second" if len(phases) == 1 else None,
            "may_finalize": len(phases) == 2,
        }

    resolver = research_secret_resolver_from_environment()
    agent = DeepSeekThinkingAgent(
        secret_resolver=resolver,
        tools=(
            DeepSeekFunctionTool(
                name="probe_research_state",
                description=(
                    "Call exactly once with first, inspect the result, then call exactly "
                    "once with second before returning final JSON."
                ),
                arguments_model=ProbeArgs,
                handler=handle,
            ),
        ),
        budget=DeepSeekAgentBudgetV1(
            max_rounds=6,
            max_tool_calls=2,
            max_total_tokens=30_000,
        ),
        reasoning_effort="max",
    )
    result = agent.run(
        system_prompt=(
            "This is a release probe. Use probe_research_state with phase first. "
            "After reading its tool result, call it with phase second. Then return only "
            '{"status":"MULTI_ROUND_TOOL_LOOP_CONFIRMED",'
            '"observed_phases":["first","second"]}. Do not skip either tool call.'
        ),
        user_payload={"probe": "DeepSeek thinking and strict multi-round tool use"},
        prompt_version="live-deepseek-research-agent-v1",
        final_model=ProbeFinal,
    )
    assert phases == ["first", "second"]
    assert result.final.observed_phases == ("first", "second")
    assert len(result.receipt.tool_calls) == 2
    assert result.receipt.rounds >= 2
    assert result.receipt.reasoning_effort == "max"
    assert result.receipt.reasoning_tokens is None or result.receipt.reasoning_tokens > 0
    assert result.receipt.reasoning_content_persisted is False
    assert '"reasoning_content":' not in result.model_dump_json()


def test_live_deepseek_native_web_search_is_discovery_only() -> None:
    result = DeepSeekNativeSearchDiscovery(
        secret_resolver=research_secret_resolver_from_environment(),
        reasoning_effort="high",
        max_uses=2,
    ).discover(
        "Find current scholarly leads for layered transition-metal electronic flat bands"
    )
    assert result.receipt.web_search_requests >= 1
    assert result.scientific_evidence_allowed is False
    assert all(lead.evidence_status == "UNRESOLVED_LEAD" for lead in result.leads)
    assert result.receipt.reasoning_content_persisted is False
