"""Bounded DeepSeek thinking loop for auditable materials research.

The loop deliberately persists only hashes, usage and typed tool receipts.  The
provider's ``reasoning_content`` is replayed only inside one in-memory atomic
conversation because DeepSeek requires it for thinking-mode tool continuations;
it is never included in :class:`DeepSeekAgentResultV1`.
"""

from __future__ import annotations

import hashlib
import json
import re
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any, Generic, Literal, TypeVar

from pydantic import BaseModel, Field, ValidationError, model_validator

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

DEEPSEEK_AGENT_SCHEMA_VERSION = "deepseek-materials-agent-v1"
DEEPSEEK_AGENT_PROVIDER_VERSION = "deepseek-thinking-tools-v1"
_TOOL_NAME = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_TOOL_CALL_ID = re.compile(r"^[A-Za-z0-9_.:-]{1,128}$")


class DeepSeekAgentBudgetV1(StrictModel):
    """Hard ceilings for one atomic research-agent conversation."""

    max_rounds: int = Field(default=24, ge=2, le=40)
    max_tool_calls: int = Field(default=36, ge=1, le=96)
    max_tool_result_bytes: int = Field(default=128_000, ge=256, le=1_000_000)
    max_total_tool_result_bytes: int = Field(
        default=1_000_000, ge=256, le=8_000_000
    )
    max_final_response_bytes: int = Field(default=256_000, ge=256, le=1_000_000)
    max_completion_tokens_per_round: int = Field(default=32_768, ge=256, le=32_768)
    max_total_tokens: int = Field(default=240_000, ge=1_000, le=1_000_000)
    max_walltime_seconds: float = Field(default=900.0, gt=0.0, le=3_600.0)


class DeepSeekToolCallReceiptV1(StrictModel):
    sequence: int = Field(ge=1)
    round_index: int = Field(ge=1)
    tool_call_id: str = Field(min_length=1, max_length=128)
    tool_name: str = Field(min_length=1, max_length=64)
    arguments_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    result_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    result_bytes: int = Field(ge=0)
    status: Literal["SUCCEEDED", "FAILED"] = "SUCCEEDED"
    error_category: str | None = Field(default=None, max_length=64)

    @model_validator(mode="after")
    def validate_error_state(self) -> DeepSeekToolCallReceiptV1:
        if self.status == "SUCCEEDED" and self.error_category is not None:
            raise ValueError("successful tool receipts cannot contain an error category")
        if self.status == "FAILED" and not self.error_category:
            raise ValueError("failed tool receipts require an error category")
        return self


class DeepSeekAgentReceiptV1(StrictModel):
    schema_version: Literal["deepseek-materials-agent-v1"] = (
        DEEPSEEK_AGENT_SCHEMA_VERSION
    )
    provider: Literal["deepseek"] = "deepseek"
    provider_version: Literal["deepseek-thinking-tools-v1"] = (
        DEEPSEEK_AGENT_PROVIDER_VERSION
    )
    model_id: Literal["deepseek-v4-pro"] = DEEPSEEK_MODEL_ID
    base_url: Literal["https://api.deepseek.com"] = DEEPSEEK_BASE_URL
    prompt_version: str = Field(min_length=1, max_length=128)
    thinking_mode: Literal["enabled"] = "enabled"
    reasoning_effort: Literal["high", "max"]
    reasoning_content_persisted: Literal[False] = False
    rounds: int = Field(ge=1, le=40)
    tool_calls: tuple[DeepSeekToolCallReceiptV1, ...] = Field(max_length=96)
    request_sha256_by_round: tuple[str, ...] = Field(min_length=1, max_length=40)
    response_sha256_by_round: tuple[str, ...] = Field(min_length=1, max_length=40)
    transport_attempts_by_round: tuple[int, ...] = Field(min_length=1, max_length=40)
    transport_retry_count: int = Field(ge=0, le=80)
    finalization_retry_count: int = Field(default=0, ge=0, le=40)
    prompt_tokens: int | None = Field(default=None, ge=0)
    completion_tokens: int | None = Field(default=None, ge=0)
    reasoning_tokens: int | None = Field(default=None, ge=0)
    total_tokens: int | None = Field(default=None, ge=0)
    final_response_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


FinalModelT = TypeVar("FinalModelT", bound=BaseModel)


class DeepSeekAgentResultV1(StrictModel, Generic[FinalModelT]):
    final: FinalModelT
    receipt: DeepSeekAgentReceiptV1


@dataclass(frozen=True, slots=True)
class DeepSeekFunctionTool:
    """One strict function tool with local Pydantic argument validation."""

    name: str
    description: str
    arguments_model: type[BaseModel]
    handler: Callable[[BaseModel], BaseModel | Mapping[str, Any]]

    def __post_init__(self) -> None:
        if not _TOOL_NAME.fullmatch(self.name):
            raise ValueError("tool name must match [A-Za-z0-9_-]{1,64}")
        if not self.description.strip() or len(self.description) > 1_024:
            raise ValueError("tool description must contain 1 to 1024 characters")
        if not issubclass(self.arguments_model, BaseModel):
            raise TypeError("arguments_model must be a Pydantic model class")

    def provider_schema(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": _deepseek_strict_schema(
                    self.arguments_model.model_json_schema()
                ),
                "strict": True,
            },
        }


class DeepSeekThinkingAgent:
    """DeepSeek V4-Pro high/max-thinking, multi-round function-tool agent."""

    def __init__(
        self,
        *,
        secret_resolver: SecretResolver,
        tools: tuple[DeepSeekFunctionTool, ...],
        budget: DeepSeekAgentBudgetV1 | None = None,
        reasoning_effort: Literal["high", "max"] = "high",
        base_url: str = DEEPSEEK_BASE_URL,
        model_id: str = DEEPSEEK_MODEL_ID,
        transport: JSONTransport | None = None,
        timeout_seconds: float = 120.0,
        max_attempts: int = 3,
        retry_base_seconds: float = 0.5,
        sleeper: Callable[[float], None] = time.sleep,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        if base_url.rstrip("/") != DEEPSEEK_BASE_URL:
            raise ValueError(f"base_url must be exactly {DEEPSEEK_BASE_URL}")
        if model_id != DEEPSEEK_MODEL_ID:
            raise ValueError(f"model_id must be exactly {DEEPSEEK_MODEL_ID}")
        if not tools:
            raise ValueError("at least one function tool is required")
        if len({tool.name for tool in tools}) != len(tools):
            raise ValueError("function tool names must be unique")
        if not 0 < timeout_seconds <= 3_600:
            raise ValueError("timeout_seconds must be in (0, 3600]")
        if not 1 <= max_attempts <= 3:
            raise ValueError("max_attempts must be between 1 and 3")
        if not 0 <= retry_base_seconds <= 5:
            raise ValueError("retry_base_seconds must be in [0, 5]")
        self.secret_resolver = secret_resolver
        self.tools = {tool.name: tool for tool in tools}
        self.budget = budget or DeepSeekAgentBudgetV1()
        self.reasoning_effort = reasoning_effort
        self.base_url = base_url.rstrip("/")
        self.model_id = model_id
        self.transport = transport or UrllibJSONTransport()
        self.timeout_seconds = timeout_seconds
        self.max_attempts = max_attempts
        self.retry_base_seconds = retry_base_seconds
        self.sleeper = sleeper
        self.monotonic = monotonic

    def run(
        self,
        *,
        system_prompt: str,
        user_payload: Mapping[str, Any],
        prompt_version: str,
        final_model: type[FinalModelT],
        require_tool_call: bool = True,
    ) -> DeepSeekAgentResultV1[FinalModelT]:
        if not system_prompt.strip():
            raise ValueError("system_prompt cannot be empty")
        if not prompt_version.strip() or len(prompt_version) > 128:
            raise ValueError("prompt_version must contain 1 to 128 characters")
        api_key = self.secret_resolver.resolve().strip()
        if not api_key:
            raise LLMProviderError(
                "PERMANENT_CONFIGURATION", "LLM API key is empty", retryable=False
            )
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": "material-screening-agent/deepseek-research-v1",
        }
        messages: list[dict[str, Any]] = [
            {
                "role": "system",
                # DeepSeek rejects ``response_format=json_object`` unless the
                # prompt itself explicitly contains the word "json". Keep the
                # provider contract here so every caller is live-compatible.
                "content": (
                    system_prompt.rstrip()
                    + "\nReturn the final response as exactly one valid json object."
                ),
            },
            {
                "role": "user",
                "content": json.dumps(
                    dict(user_payload),
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ),
            },
        ]
        started = self.monotonic()
        request_hashes: list[str] = []
        response_hashes: list[str] = []
        transport_attempts: list[int] = []
        tool_receipts: list[DeepSeekToolCallReceiptV1] = []
        total_tool_bytes = 0
        usage_totals = {
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "reasoning_tokens": 0,
            "total_tokens": 0,
        }
        usage_seen = False
        finalization_retries = 0

        for round_index in range(1, self.budget.max_rounds + 1):
            self._check_walltime(started)
            request_payload = {
                "model": self.model_id,
                "messages": messages,
                "stream": False,
                "thinking": {"type": "enabled"},
                "reasoning_effort": self.reasoning_effort,
                "tools": [tool.provider_schema() for tool in self.tools.values()],
                "tool_choice": (
                    "none"
                    if any(
                        (
                            receipt.status == "FAILED"
                            and receipt.error_category == "BUDGET_EXHAUSTED"
                        )
                        or (
                            receipt.status == "SUCCEEDED"
                            and receipt.tool_name == "read_research_state"
                        )
                        for receipt in tool_receipts
                    )
                    else "auto"
                ),
                "response_format": {"type": "json_object"},
                "max_tokens": self.budget.max_completion_tokens_per_round,
            }
            request_hashes.append(_sha256_json(request_payload))
            status, response_body, attempt_count = self._post_with_retry(
                headers=headers,
                payload=request_payload,
                started=started,
            )
            transport_attempts.append(attempt_count)
            response_hashes.append(hashlib.sha256(response_body).hexdigest())
            if not 200 <= status < 300:
                retryable = status == 429 or 500 <= status <= 599
                raise LLMProviderError(
                    "AUTHENTICATION_FAILED"
                    if status in {401, 403}
                    else "TRANSIENT_EXTERNAL"
                    if retryable
                    else "PERMANENT_CONFIGURATION",
                    f"LLM provider returned HTTP status {status}",
                    retryable=retryable,
                )
            message, finish_reason, usage = _parse_agent_envelope(
                response_body, expected_model=self.model_id
            )
            usage_seen = True
            for key in usage_totals:
                usage_totals[key] += usage[key]
            if usage_totals["total_tokens"] > self.budget.max_total_tokens:
                raise LLMProviderError(
                    "BUDGET_EXHAUSTED",
                    "DeepSeek agent exceeded the total token budget",
                    retryable=False,
                )

            tool_calls = message.get("tool_calls", [])
            if tool_calls:
                if finish_reason != "tool_calls":
                    raise _invalid("tool calls require finish_reason=tool_calls")
                assistant_message: dict[str, Any] = {
                    "role": "assistant",
                    "content": message.get("content"),
                    "tool_calls": tool_calls,
                }
                reasoning_content = message.get("reasoning_content")
                if reasoning_content is not None:
                    assistant_message["reasoning_content"] = reasoning_content
                messages.append(assistant_message)
                for call in tool_calls:
                    if len(tool_receipts) >= self.budget.max_tool_calls:
                        raise LLMProviderError(
                            "BUDGET_EXHAUSTED",
                            "DeepSeek agent exceeded the tool-call budget",
                            retryable=False,
                        )
                    call_id, name, arguments_bytes, arguments = _parse_tool_call(call)
                    tool = self.tools.get(name)
                    if tool is None:
                        raise _invalid("DeepSeek requested an unavailable tool")
                    tool_status: Literal["SUCCEEDED", "FAILED"] = "SUCCEEDED"
                    error_category: str | None = None
                    try:
                        validated_arguments = tool.arguments_model.model_validate(
                            arguments
                        )
                    except ValidationError:
                        tool_status = "FAILED"
                        error_category = "TOOL_ARGUMENT_VALIDATION_FAILED"
                        result_object = {
                            "ok": False,
                            "error_category": error_category,
                            "retryable": True,
                            "instruction": (
                                "Correct the arguments using the function schema and field "
                                "descriptions, then retry once. Do not invent a result."
                            ),
                        }
                    else:
                        try:
                            raw_result = tool.handler(validated_arguments)
                            result_object = (
                                raw_result.model_dump(mode="json")
                                if isinstance(raw_result, BaseModel)
                                else dict(raw_result)
                            )
                        except LLMProviderError as exc:
                            if exc.category in {
                                "AUTHENTICATION_FAILED",
                                "PERMANENT_CONFIGURATION",
                            }:
                                raise
                            tool_status = "FAILED"
                            error_category = exc.category[:64]
                            result_object = {
                                "ok": False,
                                "error_category": error_category,
                                "retryable": exc.retryable,
                                "instruction": (
                                    "Do not invent missing data. If the budget is exhausted, "
                                    "finalize from existing results and mark gaps UNKNOWN."
                                ),
                            }
                        except Exception:  # noqa: BLE001 - tool handlers are isolation boundaries
                            tool_status = "FAILED"
                            error_category = "TOOL_ARGUMENT_OR_EXECUTION_REJECTED"
                            result_object = {
                                "ok": False,
                                "error_category": error_category,
                                "retryable": False,
                                "instruction": (
                                    "The request was rejected. Do not invent a result; correct "
                                    "the arguments once or finalize with UNKNOWN."
                                ),
                            }
                    result_bytes = canonical_json_bytes(result_object)
                    if len(result_bytes) > self.budget.max_tool_result_bytes:
                        raise LLMProviderError(
                            "BUDGET_EXHAUSTED",
                            "one research tool result exceeded its byte budget",
                            retryable=False,
                        )
                    total_tool_bytes += len(result_bytes)
                    if total_tool_bytes > self.budget.max_total_tool_result_bytes:
                        raise LLMProviderError(
                            "BUDGET_EXHAUSTED",
                            "research tool results exceeded the total byte budget",
                            retryable=False,
                        )
                    tool_receipts.append(
                        DeepSeekToolCallReceiptV1(
                            sequence=len(tool_receipts) + 1,
                            round_index=round_index,
                            tool_call_id=call_id,
                            tool_name=name,
                            arguments_sha256=hashlib.sha256(arguments_bytes).hexdigest(),
                            result_sha256=hashlib.sha256(result_bytes).hexdigest(),
                            result_bytes=len(result_bytes),
                            status=tool_status,
                            error_category=error_category,
                        )
                    )
                    messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": call_id,
                            "content": result_bytes.decode("utf-8"),
                        }
                    )
                continue

            if finish_reason in {"length", "insufficient_system_resource"}:
                finalization_retries += 1
                messages.append(
                    {
                        "role": "user",
                        "content": (
                            "The prior finalization was incomplete. Return a concise, "
                            "complete json object matching the supplied output schema."
                        ),
                    }
                )
                continue
            if finish_reason == "content_filter":
                raise _invalid("DeepSeek final response was content-filtered")
            if finish_reason != "stop":
                raise _invalid("DeepSeek agent returned an unsupported finish reason")
            if require_tool_call and not tool_receipts:
                finalization_retries += 1
                messages.append(
                    {
                        "role": "user",
                        "content": (
                            "You have not used the required tool. Call it before returning "
                            "the final json object."
                        ),
                    }
                )
                continue
            content = message.get("content")
            if not isinstance(content, str) or not content.strip():
                finalization_retries += 1
                messages.append(
                    {
                        "role": "user",
                        "content": (
                            "The final content was empty. Return one concise, complete json "
                            "object matching the supplied output schema."
                        ),
                    }
                )
                continue
            final_bytes = content.encode("utf-8")
            if len(final_bytes) > self.budget.max_final_response_bytes:
                raise LLMProviderError(
                    "BUDGET_EXHAUSTED",
                    "DeepSeek final response exceeded its byte budget",
                    retryable=False,
                )
            try:
                final_payload = json.loads(content)
            except json.JSONDecodeError:
                finalization_retries += 1
                messages.append(
                    {
                        "role": "user",
                        "content": (
                            "The prior response was not complete valid json. Regenerate one "
                            "concise complete object from the existing tool results."
                        ),
                    }
                )
                continue
            try:
                final = final_model.model_validate(final_payload)
            except ValidationError as exc:
                finalization_retries += 1
                issues = [
                    {
                        "path": ".".join(str(item) for item in issue["loc"]),
                        "type": issue["type"],
                    }
                    for issue in exc.errors(include_url=False, include_input=False)[:16]
                ]
                messages.append(
                    {
                        "role": "user",
                        "content": json.dumps(
                            {
                                "instruction": (
                                    "Regenerate one concise complete json object. Fix these "
                                    "schema paths without inventing identifiers or evidence."
                                ),
                                "schema_issues": issues,
                            },
                            separators=(",", ":"),
                        ),
                    }
                )
                continue
            return DeepSeekAgentResultV1[final_model](
                final=final,
                receipt=DeepSeekAgentReceiptV1(
                    prompt_version=prompt_version,
                    reasoning_effort=self.reasoning_effort,
                    rounds=round_index,
                    tool_calls=tuple(tool_receipts),
                    request_sha256_by_round=tuple(request_hashes),
                    response_sha256_by_round=tuple(response_hashes),
                    transport_attempts_by_round=tuple(transport_attempts),
                    transport_retry_count=sum(transport_attempts) - len(transport_attempts),
                    finalization_retry_count=finalization_retries,
                    prompt_tokens=usage_totals["prompt_tokens"] if usage_seen else None,
                    completion_tokens=usage_totals["completion_tokens"] if usage_seen else None,
                    reasoning_tokens=usage_totals["reasoning_tokens"] if usage_seen else None,
                    total_tokens=usage_totals["total_tokens"] if usage_seen else None,
                    final_response_sha256=hashlib.sha256(final_bytes).hexdigest(),
                ),
            )

        raise LLMProviderError(
            "BUDGET_EXHAUSTED",
            "DeepSeek agent exhausted its round budget before a final answer",
            retryable=False,
        )

    def _post_with_retry(
        self,
        *,
        headers: Mapping[str, str],
        payload: dict[str, Any],
        started: float,
    ) -> tuple[int, bytes, int]:
        """Retry only transport failures and retryable HTTP responses."""

        for attempt in range(1, self.max_attempts + 1):
            self._check_walltime(started)
            remaining = self.budget.max_walltime_seconds - (
                self.monotonic() - started
            )
            try:
                status, body = self.transport.post_json(
                    # Strict tools are exposed through the beta route.
                    url=f"{self.base_url}/beta/chat/completions",
                    headers=headers,
                    payload=payload,
                    timeout_seconds=min(self.timeout_seconds, max(0.001, remaining)),
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

    def _check_walltime(self, started: float) -> None:
        if self.monotonic() - started >= self.budget.max_walltime_seconds:
            raise LLMProviderError(
                "BUDGET_EXHAUSTED",
                "DeepSeek agent exceeded the walltime budget",
                retryable=False,
            )


def _parse_agent_envelope(
    body: bytes, *, expected_model: str
) -> tuple[dict[str, Any], str, dict[str, int]]:
    try:
        payload = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise _invalid("DeepSeek returned an invalid JSON envelope") from exc
    if not isinstance(payload, dict) or payload.get("model") != expected_model:
        raise _invalid("DeepSeek returned an invalid model envelope")
    choices = payload.get("choices")
    if not isinstance(choices, list) or len(choices) != 1 or not isinstance(choices[0], dict):
        raise _invalid("DeepSeek must return exactly one choice")
    message = choices[0].get("message")
    finish_reason = choices[0].get("finish_reason")
    if not isinstance(message, dict) or finish_reason not in {
        "stop",
        "tool_calls",
        "length",
        "content_filter",
        "insufficient_system_resource",
    }:
        raise _invalid("DeepSeek returned an invalid choice")
    reasoning = message.get("reasoning_content")
    if reasoning is not None and not isinstance(reasoning, str):
        raise _invalid("DeepSeek reasoning_content must be text or null")
    calls = message.get("tool_calls", [])
    if calls is not None and not isinstance(calls, list):
        raise _invalid("DeepSeek tool_calls must be a list")
    usage_payload = payload.get("usage", {})
    if not isinstance(usage_payload, dict):
        raise _invalid("DeepSeek usage must be an object")
    details = usage_payload.get("completion_tokens_details", {})
    if details is None:
        details = {}
    if not isinstance(details, dict):
        raise _invalid("DeepSeek completion token details must be an object")
    usage: dict[str, int] = {}
    for key, raw in {
        "prompt_tokens": usage_payload.get("prompt_tokens", 0),
        "completion_tokens": usage_payload.get("completion_tokens", 0),
        "total_tokens": usage_payload.get("total_tokens", 0),
        "reasoning_tokens": details.get("reasoning_tokens", 0),
    }.items():
        if isinstance(raw, bool) or not isinstance(raw, int) or raw < 0:
            raise _invalid("DeepSeek usage contains an invalid token count")
        usage[key] = raw
    return message, finish_reason, usage


def _parse_tool_call(
    call: Any,
) -> tuple[str, str, bytes, dict[str, Any]]:
    if not isinstance(call, dict) or call.get("type") != "function":
        raise _invalid("DeepSeek returned an invalid tool call")
    call_id = call.get("id")
    function = call.get("function")
    if not isinstance(call_id, str) or not _TOOL_CALL_ID.fullmatch(call_id):
        raise _invalid("DeepSeek returned an invalid tool-call id")
    if not isinstance(function, dict):
        raise _invalid("DeepSeek returned an invalid function call")
    name = function.get("name")
    raw_arguments = function.get("arguments")
    if not isinstance(name, str) or not _TOOL_NAME.fullmatch(name):
        raise _invalid("DeepSeek returned an invalid tool name")
    if not isinstance(raw_arguments, str) or len(raw_arguments.encode("utf-8")) > 128_000:
        raise _invalid("DeepSeek returned invalid or oversized tool arguments")
    try:
        arguments = json.loads(raw_arguments)
    except json.JSONDecodeError as exc:
        raise _invalid("DeepSeek tool arguments were not valid JSON") from exc
    if not isinstance(arguments, dict):
        raise _invalid("DeepSeek tool arguments must be an object")
    canonical_arguments = canonical_json_bytes(arguments)
    return call_id, name, canonical_arguments, arguments


def _sha256_json(payload: Mapping[str, Any]) -> str:
    return hashlib.sha256(canonical_json_bytes(dict(payload))).hexdigest()


def _deepseek_strict_schema(schema: Mapping[str, Any]) -> dict[str, Any]:
    """Project Pydantic JSON Schema onto DeepSeek strict-mode's subset.

    DeepSeek requires every object property to be required and rejects string
    length and array cardinality keywords. Full constraints remain enforced by
    the local Pydantic model immediately before any handler executes.
    """

    unsupported = {"minLength", "maxLength", "minItems", "maxItems", "title"}

    def project(value: Any) -> Any:
        if isinstance(value, list):
            return [project(item) for item in value]
        if not isinstance(value, dict):
            return value
        result = {
            key: project(item)
            for key, item in value.items()
            if key not in unsupported and key != "default"
        }
        properties = result.get("properties")
        if result.get("type") == "object" and isinstance(properties, dict):
            result["required"] = list(properties)
            result["additionalProperties"] = False
        return result

    projected = project(dict(schema))
    if not isinstance(projected, dict):
        raise TypeError("projected tool schema must be an object")
    return projected


def _invalid(message: str) -> LLMProviderError:
    return LLMProviderError("INVALID_RESPONSE", message, retryable=False)
