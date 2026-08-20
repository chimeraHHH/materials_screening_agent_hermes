from __future__ import annotations

import json
from typing import Any

import pytest

from material_agent.inspiration.native_search import DeepSeekNativeSearchDiscovery
from material_agent.orchestrator.llm import LLMProviderError


class Secret:
    def resolve(self) -> str:
        return "secret"


class Transport:
    def __init__(self, response: dict[str, Any]) -> None:
        self.response = response
        self.call: dict[str, Any] = {}

    def post_json(self, **kwargs: Any) -> tuple[int, bytes]:
        self.call = kwargs
        return 200, json.dumps(self.response).encode()


class FlakyTransport(Transport):
    def __init__(self, response: dict[str, Any]) -> None:
        super().__init__(response)
        self.attempts = 0

    def post_json(self, **kwargs: Any) -> tuple[int, bytes]:
        self.attempts += 1
        if self.attempts == 1:
            raise LLMProviderError(
                "TRANSIENT_EXTERNAL", "temporary incomplete response", retryable=True
            )
        return super().post_json(**kwargs)


def test_native_search_returns_unresolved_leads_and_safe_receipt() -> None:
    transport = Transport(
        {
            "model": "deepseek-v4-pro",
            "content": [
                {"type": "thinking", "thinking": "private reasoning"},
                {
                    "type": "server_tool_use",
                    "id": "srv-1",
                    "name": "web_search",
                    "input": {"query": "flat band kagome"},
                },
                {
                    "type": "web_search_tool_result",
                    "tool_use_id": "srv-1",
                    "content": [
                        {
                            "type": "web_search_result",
                            "title": "A paper",
                            "url": "https://example.org/paper",
                            "page_age": "2026-01-01",
                            "encrypted_content": "opaque",
                        }
                    ],
                },
                {"type": "text", "text": "unverified synthesis"},
            ],
            "usage": {
                "input_tokens": 10,
                "output_tokens": 20,
                "server_tool_use": {"web_search_requests": 1},
            },
        }
    )
    result = DeepSeekNativeSearchDiscovery(
        secret_resolver=Secret(), transport=transport, reasoning_effort="max"
    ).discover("transition metal layered flat band")

    assert result.leads[0].evidence_status == "UNRESOLVED_LEAD"
    assert result.scientific_evidence_allowed is False
    assert result.receipt.web_search_requests == 1
    assert result.receipt.requested_max_uses == 8
    serialized = result.model_dump_json()
    assert "private reasoning" not in serialized
    assert "unverified synthesis" not in serialized
    assert "secret" not in serialized
    assert transport.call["url"].endswith("/anthropic/v1/messages")
    assert transport.call["payload"]["tools"][0]["name"] == "web_search"


def test_native_search_server_error_is_not_evidence() -> None:
    transport = Transport(
        {
            "model": "deepseek-v4-pro",
            "content": [
                {
                    "type": "web_search_tool_result",
                    "tool_use_id": "srv-1",
                    "content": {
                        "type": "web_search_tool_result_error",
                        "error_code": "unavailable",
                    },
                }
            ],
            "usage": {"input_tokens": 1, "output_tokens": 1},
        }
    )
    with pytest.raises(LLMProviderError) as error:
        DeepSeekNativeSearchDiscovery(secret_resolver=Secret(), transport=transport).discover(
            "transition metal flat band"
        )
    assert error.value.category == "NATIVE_SEARCH_UNAVAILABLE"


def test_native_search_retries_transient_transport_failure() -> None:
    transport = FlakyTransport(
        {
            "model": "deepseek-v4-pro",
            "content": [{"type": "text", "text": "no useful lead"}],
            "usage": {
                "input_tokens": 1,
                "output_tokens": 1,
                "server_tool_use": {"web_search_requests": 0},
            },
        }
    )
    result = DeepSeekNativeSearchDiscovery(
        secret_resolver=Secret(),
        transport=transport,
        retry_base_seconds=0,
    ).discover("transition metal flat band")
    assert transport.attempts == 2
    assert result.receipt.transport_attempts == 2
