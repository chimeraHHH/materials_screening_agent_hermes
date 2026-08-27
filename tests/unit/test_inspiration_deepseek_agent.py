from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

import pytest
from pydantic import Field

from material_agent.inspiration.deepseek_agent import (
    DeepSeekAgentBudgetV1,
    DeepSeekFunctionTool,
    DeepSeekThinkingAgent,
)
from material_agent.orchestrator.llm import LLMProviderError
from material_agent.orchestrator.models import StrictModel


class Args(StrictModel):
    query: str = Field(min_length=1, max_length=100)


class Final(StrictModel):
    answer: str
    evidence_ids: tuple[str, ...]


class Secret:
    def resolve(self) -> str:
        return "research-secret"


class ScriptedTransport:
    def __init__(self, responses: list[dict[str, Any]]) -> None:
        self.responses = responses
        self.payloads: list[dict[str, Any]] = []
        self.headers: list[Mapping[str, str]] = []

    def post_json(self, **kwargs: Any) -> tuple[int, bytes]:
        self.payloads.append(kwargs["payload"])
        self.headers.append(kwargs["headers"])
        return 200, json.dumps(self.responses.pop(0)).encode()


class FlakyTransport(ScriptedTransport):
    def __init__(self, responses: list[dict[str, Any]]) -> None:
        super().__init__(responses)
        self.attempts = 0

    def post_json(self, **kwargs: Any) -> tuple[int, bytes]:
        self.attempts += 1
        if self.attempts == 1:
            raise LLMProviderError(
                "TRANSIENT_EXTERNAL", "temporary incomplete response", retryable=True
            )
        return super().post_json(**kwargs)


def envelope(
    message: dict[str, Any], finish: str, *, tokens: int = 10
) -> dict[str, Any]:
    return {
        "model": "deepseek-v4-pro",
        "choices": [{"message": message, "finish_reason": finish}],
        "usage": {
            "prompt_tokens": tokens,
            "completion_tokens": tokens,
            "total_tokens": 2 * tokens,
            "completion_tokens_details": {"reasoning_tokens": tokens // 2},
        },
    }


def tool_call(call_id: str, query: str) -> dict[str, Any]:
    return {
        "id": call_id,
        "type": "function",
        "function": {"name": "lookup", "arguments": json.dumps({"query": query})},
    }


def build_agent(
    transport: ScriptedTransport, *, timeout_seconds: float = 120, **budget: Any
) -> DeepSeekThinkingAgent:
    return DeepSeekThinkingAgent(
        secret_resolver=Secret(),
        tools=(
            DeepSeekFunctionTool(
                name="lookup",
                description="Resolve one bounded evidence query.",
                arguments_model=Args,
                handler=lambda args: {"evidence_id": f"ev-{args.query}"},
            ),
        ),
        budget=DeepSeekAgentBudgetV1(**budget),
        reasoning_effort="max",
        transport=transport,
        timeout_seconds=timeout_seconds,
    )


def test_long_scientific_round_accepts_bounded_thirty_minute_deadline() -> None:
    transport = ScriptedTransport([])
    agent = build_agent(transport, timeout_seconds=1_800)
    assert agent.timeout_seconds == 1_800

    with pytest.raises(ValueError, match="3600"):
        build_agent(transport, timeout_seconds=3_601)


def test_multiround_thinking_tools_and_receipt_do_not_persist_reasoning() -> None:
    transport = ScriptedTransport(
        [
            envelope(
                {
                    "role": "assistant",
                    "content": None,
                    "reasoning_content": "private chain one",
                    "tool_calls": [tool_call("call-1", "layered")],
                },
                "tool_calls",
            ),
            envelope(
                {
                    "role": "assistant",
                    "content": None,
                    "reasoning_content": "private chain two",
                    "tool_calls": [tool_call("call-2", "flat band")],
                },
                "tool_calls",
            ),
            envelope(
                {
                    "role": "assistant",
                    "reasoning_content": "private final chain",
                    "content": json.dumps(
                        {"answer": "hypothesis only", "evidence_ids": ["ev-layered"]}
                    ),
                },
                "stop",
            ),
        ]
    )
    result = build_agent(transport).run(
        system_prompt="Return JSON and use evidence tools.",
        user_payload={"goal": "find a material"},
        prompt_version="test-v1",
        final_model=Final,
    )

    assert result.final.answer == "hypothesis only"
    assert result.receipt.rounds == 3
    assert len(result.receipt.tool_calls) == 2
    assert result.receipt.reasoning_content_persisted is False
    serialized = result.model_dump_json()
    assert "private chain" not in serialized
    assert "research-secret" not in serialized
    assert transport.payloads[0]["thinking"] == {"type": "enabled"}
    assert transport.payloads[0]["max_tokens"] == 32_768
    assert "json" in transport.payloads[0]["messages"][0]["content"]
    assert transport.payloads[0]["reasoning_effort"] == "max"
    assert transport.payloads[0]["tools"][0]["function"]["strict"] is True
    assert (
        transport.payloads[1]["messages"][2]["reasoning_content"] == "private chain one"
    )
    assert transport.payloads[1]["messages"][3]["role"] == "tool"


def test_provider_schema_projects_to_deepseek_strict_subset() -> None:
    schema = (
        build_agent(ScriptedTransport([]))
        .tools["lookup"]
        .provider_schema()["function"]["parameters"]
    )
    assert schema["required"] == ["query"]
    assert schema["additionalProperties"] is False
    assert "minLength" not in schema["properties"]["query"]
    assert "maxLength" not in schema["properties"]["query"]


def test_rejects_unknown_tool_without_executing_anything() -> None:
    call = tool_call("call-x", "x")
    call["function"]["name"] = "shell"
    transport = ScriptedTransport(
        [
            envelope(
                {"role": "assistant", "content": None, "tool_calls": [call]},
                "tool_calls",
            )
        ]
    )
    with pytest.raises(LLMProviderError, match="unavailable tool") as error:
        build_agent(transport).run(
            system_prompt="JSON",
            user_payload={},
            prompt_version="test",
            final_model=Final,
        )
    assert error.value.category == "INVALID_RESPONSE"


def test_rejects_invalid_tool_arguments_locally() -> None:
    bad_call = tool_call("call-x", "x")
    bad_call["function"]["arguments"] = json.dumps({"query": "", "extra": 1})
    transport = ScriptedTransport(
        [
            envelope(
                {"role": "assistant", "content": None, "tool_calls": [bad_call]},
                "tool_calls",
            ),
            envelope(
                {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [tool_call("call-y", "corrected")],
                },
                "tool_calls",
            ),
            envelope(
                {
                    "role": "assistant",
                    "content": json.dumps(
                        {"answer": "x", "evidence_ids": ["ev-corrected"]}
                    ),
                },
                "stop",
            ),
        ]
    )
    result = build_agent(transport).run(
        system_prompt="JSON", user_payload={}, prompt_version="test", final_model=Final
    )
    assert result.receipt.tool_calls[0].status == "FAILED"
    assert (
        result.receipt.tool_calls[0].error_category == "TOOL_ARGUMENT_VALIDATION_FAILED"
    )
    assert result.receipt.tool_calls[1].status == "SUCCEEDED"


def test_round_budget_fails_closed() -> None:
    transport = ScriptedTransport(
        [
            envelope(
                {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [tool_call("c1", "a")],
                },
                "tool_calls",
            ),
            envelope(
                {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [tool_call("c2", "b")],
                },
                "tool_calls",
            ),
        ]
    )
    with pytest.raises(LLMProviderError, match="round budget") as error:
        build_agent(transport, max_rounds=2).run(
            system_prompt="JSON",
            user_payload={},
            prompt_version="test",
            final_model=Final,
        )
    assert error.value.category == "BUDGET_EXHAUSTED"


def test_single_call_inspection_tool_forces_finalization_after_one_use() -> None:
    transport = ScriptedTransport(
        [
            envelope(
                {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [tool_call("c1", "evidence")],
                },
                "tool_calls",
            ),
            envelope(
                {
                    "role": "assistant",
                    "content": json.dumps(
                        {"answer": "finalized", "evidence_ids": ["ev-evidence"]}
                    ),
                },
                "stop",
            ),
        ]
    )
    agent = DeepSeekThinkingAgent(
        secret_resolver=Secret(),
        tools=(
            DeepSeekFunctionTool(
                name="lookup",
                description="Read a fixed evidence bundle once.",
                arguments_model=Args,
                handler=lambda args: {"evidence_id": f"ev-{args.query}"},
                max_calls_per_run=1,
            ),
        ),
        transport=transport,
    )

    result = agent.run(
        system_prompt="Inspect once, then return JSON.",
        user_payload={},
        prompt_version="single-call-v1",
        final_model=Final,
    )

    assert result.final.answer == "finalized"
    assert transport.payloads[0]["tool_choice"] == "auto"
    assert transport.payloads[1]["tool_choice"] == "none"


def test_dynamic_tool_saturation_forces_finalization_at_scientific_target() -> None:
    transport = ScriptedTransport(
        [
            envelope(
                {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [tool_call("c1", "candidate")],
                },
                "tool_calls",
            ),
            envelope(
                {
                    "role": "assistant",
                    "content": json.dumps(
                        {"answer": "target reached", "evidence_ids": ["ev-candidate"]}
                    ),
                },
                "stop",
            ),
        ]
    )
    state = {"compiled": 0}

    def compile_candidate(args: Args) -> dict[str, str]:
        state["compiled"] += 1
        return {"evidence_id": f"ev-{args.query}"}

    agent = DeepSeekThinkingAgent(
        secret_resolver=Secret(),
        tools=(
            DeepSeekFunctionTool(
                name="lookup",
                description="Compile candidates until the bounded target is reached.",
                arguments_model=Args,
                handler=compile_candidate,
                saturation_predicate=lambda: state["compiled"] >= 1,
            ),
        ),
        transport=transport,
    )

    result = agent.run(
        system_prompt="Compile only the bounded target, then return JSON.",
        user_payload={},
        prompt_version="dynamic-saturation-v1",
        final_model=Final,
    )

    assert result.final.answer == "target reached"
    assert state["compiled"] == 1
    assert transport.payloads[0]["tool_choice"] == "auto"
    assert transport.payloads[1]["tool_choice"] == "none"


def test_final_schema_failure_exhausts_bounded_repair_rounds() -> None:
    transport = ScriptedTransport(
        [
            envelope(
                {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [tool_call("c1", "a")],
                },
                "tool_calls",
            ),
            envelope(
                {
                    "role": "assistant",
                    "content": json.dumps({"answer": "x", "extra": 1}),
                },
                "stop",
            ),
        ]
    )
    with pytest.raises(LLMProviderError, match="round budget"):
        build_agent(transport, max_rounds=2).run(
            system_prompt="JSON",
            user_payload={},
            prompt_version="test",
            final_model=Final,
        )


def test_truncated_final_is_repaired_within_the_same_agent_budget() -> None:
    transport = ScriptedTransport(
        [
            envelope(
                {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [tool_call("c1", "a")],
                },
                "tool_calls",
            ),
            envelope({"role": "assistant", "content": '{"answer":'}, "length"),
            envelope(
                {
                    "role": "assistant",
                    "content": json.dumps(
                        {"answer": "repaired", "evidence_ids": ["ev-a"]}
                    ),
                },
                "stop",
            ),
        ]
    )
    result = build_agent(transport).run(
        system_prompt="JSON", user_payload={}, prompt_version="test", final_model=Final
    )
    assert result.final.answer == "repaired"
    assert result.receipt.finalization_retry_count == 1


def test_agent_retries_transient_transport_failure_and_audits_it() -> None:
    transport = FlakyTransport(
        [
            envelope(
                {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [tool_call("c1", "a")],
                },
                "tool_calls",
            ),
            envelope(
                {
                    "role": "assistant",
                    "content": json.dumps({"answer": "x", "evidence_ids": ["ev-a"]}),
                },
                "stop",
            ),
        ]
    )
    agent = build_agent(transport)
    agent.retry_base_seconds = 0
    result = agent.run(
        system_prompt="JSON", user_payload={}, prompt_version="test", final_model=Final
    )
    assert result.receipt.transport_attempts_by_round == (2, 1)
    assert result.receipt.transport_retry_count == 1


def test_tool_budget_failure_is_returned_to_model_for_safe_degradation() -> None:
    transport = ScriptedTransport(
        [
            envelope(
                {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [tool_call("c1", "a")],
                },
                "tool_calls",
            ),
            envelope(
                {
                    "role": "assistant",
                    "content": json.dumps(
                        {"answer": "unknown after budget", "evidence_ids": []}
                    ),
                },
                "stop",
            ),
        ]
    )

    def exhausted(args: Args):
        del args
        raise LLMProviderError(
            "BUDGET_EXHAUSTED", "provider details must not leak", retryable=False
        )

    agent = DeepSeekThinkingAgent(
        secret_resolver=Secret(),
        tools=(
            DeepSeekFunctionTool(
                name="lookup",
                description="Resolve one bounded evidence query.",
                arguments_model=Args,
                handler=exhausted,
            ),
        ),
        transport=transport,
    )
    result = agent.run(
        system_prompt="JSON", user_payload={}, prompt_version="test", final_model=Final
    )
    assert result.receipt.tool_calls[0].status == "FAILED"
    assert result.receipt.tool_calls[0].error_category == "BUDGET_EXHAUSTED"
    tool_message = transport.payloads[1]["messages"][-1]["content"]
    assert "provider details must not leak" not in tool_message
    assert "mark gaps UNKNOWN" in tool_message
    assert transport.payloads[1]["tool_choice"] == "none"


def test_total_token_budget_failure_preserves_safe_provider_usage() -> None:
    transport = ScriptedTransport(
        [
            envelope(
                {
                    "role": "assistant",
                    "content": json.dumps({"answer": "x", "evidence_ids": []}),
                },
                "stop",
                tokens=550,
            )
        ]
    )
    agent = build_agent(transport)
    agent.budget = agent.budget.model_copy(update={"max_total_tokens": 1_000})

    with pytest.raises(LLMProviderError, match="total token budget") as caught:
        agent.run(
            system_prompt="JSON",
            user_payload={},
            prompt_version="test",
            final_model=Final,
            require_tool_call=False,
        )

    assert caught.value.category == "BUDGET_EXHAUSTED"
    assert caught.value.usage == {
        "prompt_tokens": 550,
        "completion_tokens": 550,
        "reasoning_tokens": 275,
        "total_tokens": 1_100,
    }
