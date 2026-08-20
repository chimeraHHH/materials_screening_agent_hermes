"""DeepSeek-native web search used only for bounded lead discovery.

This boundary uses DeepSeek's Anthropic-compatible server-side web-search
blocks.  Returned URLs are explicitly labelled unresolved: a downstream
authoritative resolver must retrieve and hash an accepted source before any
lead can support a scientific constraint.
"""

from __future__ import annotations

import hashlib
import json
import time
from collections.abc import Callable, Mapping
from typing import Any, Literal

from pydantic import Field

from material_agent.inspiration.models import canonical_json_bytes
from material_agent.orchestrator.llm import (
    DEEPSEEK_BASE_URL,
    DEEPSEEK_MODEL_ID,
    MAX_RESPONSE_BYTES,
    JSONTransport,
    LLMProviderError,
    SecretResolver,
    UrllibJSONTransport,
)
from material_agent.orchestrator.models import StrictModel


DEEPSEEK_NATIVE_SEARCH_VERSION = "deepseek-native-search-discovery-v1"
DEEPSEEK_NATIVE_SEARCH_HARD_MAX_REQUESTS = 16


class NativeSearchLeadV1(StrictModel):
    lead_id: str = Field(pattern=r"^lead-[0-9a-f]{24}$")
    title: str = Field(min_length=1, max_length=1_000)
    url: str = Field(min_length=8, max_length=2_048)
    page_age: str | None = Field(default=None, max_length=128)
    evidence_status: Literal["UNRESOLVED_LEAD"] = "UNRESOLVED_LEAD"


class NativeSearchReceiptV1(StrictModel):
    schema_version: Literal["deepseek-native-search-discovery-v1"] = (
        DEEPSEEK_NATIVE_SEARCH_VERSION
    )
    provider: Literal["deepseek-anthropic-compatible"] = (
        "deepseek-anthropic-compatible"
    )
    model_id: Literal["deepseek-v4-pro"] = DEEPSEEK_MODEL_ID
    endpoint_family: Literal["anthropic-messages"] = "anthropic-messages"
    thinking_mode: Literal["enabled"] = "enabled"
    reasoning_effort: Literal["high", "max"]
    reasoning_content_persisted: Literal[False] = False
    request_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    response_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)
    requested_max_uses: int = Field(ge=1, le=16)
    web_search_requests: int = Field(
        ge=0, le=DEEPSEEK_NATIVE_SEARCH_HARD_MAX_REQUESTS
    )
    transport_attempts: int = Field(ge=1, le=3)


class NativeSearchDiscoveryV1(StrictModel):
    query: str = Field(min_length=3, max_length=512)
    leads: tuple[NativeSearchLeadV1, ...] = Field(max_length=64)
    synthesis_text_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    receipt: NativeSearchReceiptV1
    scientific_evidence_allowed: Literal[False] = False


class DeepSeekNativeSearchDiscovery:
    """One bounded native-search turn; result text is not persisted."""

    def __init__(
        self,
        *,
        secret_resolver: SecretResolver,
        transport: JSONTransport | None = None,
        reasoning_effort: Literal["high", "max"] = "high",
        max_uses: int = 8,
        max_tokens: int = 8_192,
        timeout_seconds: float = 180.0,
        max_attempts: int = 3,
        retry_base_seconds: float = 0.5,
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        if not 1 <= max_uses <= 16:
            raise ValueError("max_uses must be between 1 and 16")
        if not 512 <= max_tokens <= 32_768:
            raise ValueError("max_tokens must be between 512 and 32768")
        if not 0 < timeout_seconds <= 300:
            raise ValueError("timeout_seconds must be in (0, 300]")
        if not 1 <= max_attempts <= 3:
            raise ValueError("max_attempts must be between 1 and 3")
        if not 0 <= retry_base_seconds <= 5:
            raise ValueError("retry_base_seconds must be in [0, 5]")
        self.secret_resolver = secret_resolver
        self.transport = transport or UrllibJSONTransport()
        self.reasoning_effort = reasoning_effort
        self.max_uses = max_uses
        self.max_tokens = max_tokens
        self.timeout_seconds = timeout_seconds
        self.max_attempts = max_attempts
        self.retry_base_seconds = retry_base_seconds
        self.sleeper = sleeper

    def discover(self, query: str) -> NativeSearchDiscoveryV1:
        selected_query = " ".join(query.split())
        if not 3 <= len(selected_query) <= 512:
            raise ValueError("native search query must contain 3 to 512 characters")
        key = self.secret_resolver.resolve().strip()
        if not key:
            raise LLMProviderError(
                "PERMANENT_CONFIGURATION", "LLM API key is empty", retryable=False
            )
        payload = {
            "model": DEEPSEEK_MODEL_ID,
            "max_tokens": self.max_tokens,
            "thinking": {"type": "enabled", "budget_tokens": 4_096},
            "output_config": {"effort": self.reasoning_effort},
            "system": (
                "Search broadly for materials-science leads. Return a concise synthesis, "
                "but do not claim that a search result is verified scientific evidence."
            ),
            "messages": [{"role": "user", "content": selected_query}],
            "tools": [
                {
                    "type": "web_search_20250305",
                    "name": "web_search",
                    "max_uses": self.max_uses,
                }
            ],
        }
        headers = {
            "x-api-key": key,
            "anthropic-version": "2023-06-01",
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": "material-screening-agent/native-search-v1",
        }
        status, body, transport_attempts = self._post_with_retry(
            headers=headers, payload=payload
        )
        if not 200 <= status < 300:
            retryable = status == 429 or 500 <= status <= 599
            raise LLMProviderError(
                "AUTHENTICATION_FAILED"
                if status in {401, 403}
                else "TRANSIENT_EXTERNAL"
                if retryable
                else "NATIVE_SEARCH_UNAVAILABLE",
                f"DeepSeek native search returned HTTP status {status}",
                retryable=retryable,
            )
        decoded = _parse_native_search_response(body)
        leads: dict[str, NativeSearchLeadV1] = {}
        text_parts: list[str] = []
        observed_searches = 0
        for block in decoded["content"]:
            block_type = block.get("type")
            if block_type == "text" and isinstance(block.get("text"), str):
                text_parts.append(block["text"])
            elif block_type == "server_tool_use" and block.get("name") == "web_search":
                observed_searches += 1
            elif block_type == "web_search_tool_result":
                content = block.get("content")
                if isinstance(content, dict):
                    raise LLMProviderError(
                        "NATIVE_SEARCH_UNAVAILABLE",
                        "DeepSeek native search returned a server-tool error",
                        retryable=content.get("error_code") in {"too_many_requests", "unavailable"},
                    )
                if not isinstance(content, list):
                    raise _invalid("native search result content must be a list")
                for item in content:
                    if not isinstance(item, dict) or item.get("type") != "web_search_result":
                        continue
                    title, url = item.get("title"), item.get("url")
                    if not isinstance(title, str) or not title.strip():
                        continue
                    if not isinstance(url, str) or not url.startswith(("https://", "http://")):
                        continue
                    lead_id = "lead-" + hashlib.sha256(url.encode("utf-8")).hexdigest()[:24]
                    page_age = item.get("page_age")
                    leads.setdefault(
                        lead_id,
                        NativeSearchLeadV1(
                            lead_id=lead_id,
                            title=title[:1_000],
                            url=url[:2_048],
                            page_age=page_age[:128] if isinstance(page_age, str) else None,
                        ),
                    )
        usage = decoded["usage"]
        server_usage = usage.get("server_tool_use", {})
        reported_searches = (
            server_usage.get("web_search_requests", 0)
            if isinstance(server_usage, dict)
            else 0
        )
        if isinstance(reported_searches, bool) or not isinstance(reported_searches, int):
            raise _invalid("native search usage is invalid")
        search_count = max(observed_searches, reported_searches)
        # The Anthropic-compatible endpoint currently accepts ``max_uses`` but
        # may report more physical searches than requested. Preserve both
        # values for cost audit and enforce an adapter-side hard ceiling.
        if search_count > DEEPSEEK_NATIVE_SEARCH_HARD_MAX_REQUESTS:
            raise _invalid("native search exceeded the adapter hard safety ceiling")
        synthesis_hash = hashlib.sha256("\n".join(text_parts).encode("utf-8")).hexdigest()
        return NativeSearchDiscoveryV1(
            query=selected_query,
            leads=tuple(leads.values()),
            synthesis_text_sha256=synthesis_hash,
            receipt=NativeSearchReceiptV1(
                reasoning_effort=self.reasoning_effort,
                request_sha256=hashlib.sha256(canonical_json_bytes(payload)).hexdigest(),
                response_sha256=hashlib.sha256(body).hexdigest(),
                input_tokens=_optional_token(usage.get("input_tokens")),
                output_tokens=_optional_token(usage.get("output_tokens")),
                requested_max_uses=self.max_uses,
                web_search_requests=search_count,
                transport_attempts=transport_attempts,
            ),
        )

    def _post_with_retry(
        self, *, headers: Mapping[str, str], payload: dict[str, Any]
    ) -> tuple[int, bytes, int]:
        for attempt in range(1, self.max_attempts + 1):
            try:
                status, body = self.transport.post_json(
                    url=f"{DEEPSEEK_BASE_URL}/anthropic/v1/messages",
                    headers=headers,
                    payload=payload,
                    timeout_seconds=self.timeout_seconds,
                    max_response_bytes=MAX_RESPONSE_BYTES,
                )
            except LLMProviderError as exc:
                if not exc.retryable or attempt == self.max_attempts:
                    raise
            else:
                retryable_status = status == 429 or 500 <= status <= 599
                if not retryable_status or attempt == self.max_attempts:
                    return status, body, attempt
            if self.retry_base_seconds:
                self.sleeper(self.retry_base_seconds * (2 ** (attempt - 1)))
        raise AssertionError("unreachable retry loop")


def _parse_native_search_response(body: bytes) -> dict[str, Any]:
    try:
        payload = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise _invalid("native search returned invalid JSON") from exc
    if (
        not isinstance(payload, dict)
        or payload.get("model") != DEEPSEEK_MODEL_ID
        or not isinstance(payload.get("content"), list)
        or not isinstance(payload.get("usage", {}), dict)
    ):
        raise _invalid("native search returned an invalid envelope")
    return payload


def _optional_token(value: Any) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise _invalid("native search usage contains an invalid token count")
    return value


def _invalid(message: str) -> LLMProviderError:
    return LLMProviderError("INVALID_RESPONSE", message, retryable=False)
