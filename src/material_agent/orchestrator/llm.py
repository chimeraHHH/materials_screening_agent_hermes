"""Provider-neutral structured LLM boundary and DeepSeek implementation."""

from __future__ import annotations

import hashlib
import http.client
import json
import os
import queue
import subprocess
import threading
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable
from urllib.parse import urlparse

from material_agent.orchestrator.models import LLMCallAudit

DEEPSEEK_PROVIDER_VERSION = "deepseek-openai-compatible-v2"
DEEPSEEK_BASE_URL = "https://api.deepseek.com"
DEEPSEEK_MODEL_ID = "deepseek-v4-pro"
DEFAULT_LLM_API_KEY_ENV = "MATERIAL_AGENT_LLM_API_KEY"
DEFAULT_KEYCHAIN_SERVICE = "material-screening-agent-llm-api"
MAX_RESPONSE_BYTES = 1024 * 1024


class LLMProviderError(RuntimeError):
    """Safe provider failure that never embeds credentials or response bodies."""

    def __init__(
        self,
        category: str,
        message: str,
        *,
        retryable: bool,
        usage: Mapping[str, int] | None = None,
        role: str | None = None,
    ) -> None:
        super().__init__(message)
        self.category = category
        self.retryable = retryable
        self.usage = dict(usage) if usage is not None else None
        self.role = role


@runtime_checkable
class SecretResolver(Protocol):
    def resolve(self) -> str: ...


@runtime_checkable
class JSONTransport(Protocol):
    def post_json(
        self,
        *,
        url: str,
        headers: Mapping[str, str],
        payload: dict[str, Any],
        timeout_seconds: float,
        max_response_bytes: int,
    ) -> tuple[int, bytes]: ...


@runtime_checkable
class LLMProvider(Protocol):
    name: str
    version: str

    def structured_generate(
        self,
        *,
        system_prompt: str,
        user_payload: dict[str, Any],
        prompt_version: str,
    ) -> StructuredLLMResponse: ...


@dataclass(frozen=True)
class StructuredLLMResponse:
    payload: dict[str, Any]
    audit: LLMCallAudit


class EnvironmentOrKeychainSecretResolver:
    """Resolve a secret lazily from the process environment or macOS Keychain."""

    def __init__(
        self,
        *,
        environment: Mapping[str, str] | None = None,
        environment_name: str = DEFAULT_LLM_API_KEY_ENV,
        keychain_service: str = DEFAULT_KEYCHAIN_SERVICE,
        keychain_account: str,
        command_runner: Callable[..., subprocess.CompletedProcess[str]] | None = None,
    ) -> None:
        self.environment = environment if environment is not None else os.environ
        self.environment_name = _safe_label(environment_name, "environment name")
        self.keychain_service = _safe_label(
            keychain_service, "Keychain service"
        )
        self.keychain_account = _safe_label(
            keychain_account, "Keychain account"
        )
        self.command_runner = command_runner or subprocess.run

    def resolve(self) -> str:
        environment_value = self.environment.get(self.environment_name, "").strip()
        if environment_value:
            return environment_value
        try:
            result = self.command_runner(
                [
                    "security",
                    "find-generic-password",
                    "-a",
                    self.keychain_account,
                    "-s",
                    self.keychain_service,
                    "-w",
                ],
                check=False,
                capture_output=True,
                text=True,
                timeout=5,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise LLMProviderError(
                "PERMANENT_CONFIGURATION",
                "LLM API key is unavailable from the configured secret sources",
                retryable=False,
            ) from exc
        if result.returncode != 0:
            raise LLMProviderError(
                "PERMANENT_CONFIGURATION",
                "LLM API key is unavailable from the configured secret sources",
                retryable=False,
            )
        key = result.stdout.strip()
        if not key:
            raise LLMProviderError(
                "PERMANENT_CONFIGURATION",
                "LLM API key is empty in the configured secret source",
                retryable=False,
            )
        return key


class UrllibJSONTransport:
    """Small HTTPS transport with strict response-size and wall-clock ceilings."""

    def post_json(
        self,
        *,
        url: str,
        headers: Mapping[str, str],
        payload: dict[str, Any],
        timeout_seconds: float,
        max_response_bytes: int,
    ) -> tuple[int, bytes]:
        body = _canonical_json(payload)
        request = urllib.request.Request(
            url,
            data=body,
            headers=dict(headers),
            method="POST",
        )
        try:
            with urllib.request.urlopen(
                request, timeout=timeout_seconds
            ) as response:
                content = _read_response_with_deadline(
                    response,
                    max_bytes=max_response_bytes + 1,
                    timeout_seconds=timeout_seconds,
                )
                status = int(response.status)
        except urllib.error.HTTPError as exc:
            content = _read_response_with_deadline(
                exc,
                max_bytes=max_response_bytes + 1,
                timeout_seconds=timeout_seconds,
            )
            status = int(exc.code)
        except (
            TimeoutError,
            urllib.error.URLError,
            OSError,
            http.client.HTTPException,
            AttributeError,
            ValueError,
        ) as exc:
            raise LLMProviderError(
                "TRANSIENT_EXTERNAL",
                "LLM provider request failed before receiving a valid response",
                retryable=True,
            ) from exc
        if len(content) > max_response_bytes:
            raise LLMProviderError(
                "INVALID_RESPONSE",
                "LLM provider response exceeded the configured size limit",
                retryable=False,
            )
        return status, content


def _read_response_with_deadline(
    response: Any, *, max_bytes: int, timeout_seconds: float
) -> bytes:
    """Read a response with a total deadline, not only a socket-idle timeout.

    urllib's socket timeout can be reset indefinitely by a trickling chunked
    response.  A daemon reader lets the caller close that response and fail
    safely once the configured wall-clock budget is exhausted.
    """

    completed: queue.Queue[tuple[bool, bytes | Exception]] = queue.Queue(
        maxsize=1
    )

    def read_once() -> None:
        try:
            completed.put((True, response.read(max_bytes)))
        except (
            TimeoutError,
            urllib.error.URLError,
            OSError,
            http.client.HTTPException,
            AttributeError,
            ValueError,
        ) as exc:
            completed.put((False, exc))

    reader = threading.Thread(target=read_once, daemon=True)
    reader.start()
    try:
        succeeded, value = completed.get(timeout=timeout_seconds)
    except queue.Empty as exc:
        try:
            response.close()
        except (OSError, http.client.HTTPException, AttributeError, ValueError):
            pass
        raise LLMProviderError(
            "TRANSIENT_EXTERNAL",
            "LLM provider response exceeded the total read deadline",
            retryable=True,
        ) from exc
    if not succeeded:
        assert isinstance(value, Exception)
        raise value
    assert isinstance(value, bytes)
    return value


class DeepSeekProvider:
    """DeepSeek V4-Pro JSON provider over OpenAI-compatible Chat Completions."""

    name = "deepseek"
    version = DEEPSEEK_PROVIDER_VERSION

    def __init__(
        self,
        *,
        secret_resolver: SecretResolver,
        base_url: str = DEEPSEEK_BASE_URL,
        model_id: str = DEEPSEEK_MODEL_ID,
        transport: JSONTransport | None = None,
        timeout_seconds: float = 60.0,
        max_attempts: int = 2,
        retry_base_seconds: float = 0.25,
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        self.base_url = _validate_deepseek_base_url(base_url)
        if model_id != DEEPSEEK_MODEL_ID:
            raise ValueError(
                f"DeepSeek Stage0 model must be exactly {DEEPSEEK_MODEL_ID}"
            )
        if timeout_seconds <= 0 or timeout_seconds > 300:
            raise ValueError("LLM timeout_seconds must be in (0, 300]")
        if max_attempts < 1 or max_attempts > 3:
            raise ValueError("LLM max_attempts must be between 1 and 3")
        if retry_base_seconds < 0 or retry_base_seconds > 5:
            raise ValueError("LLM retry_base_seconds must be in [0, 5]")
        self.model_id = model_id
        self.secret_resolver = secret_resolver
        self.transport = transport or UrllibJSONTransport()
        self.timeout_seconds = timeout_seconds
        self.max_attempts = max_attempts
        self.retry_base_seconds = retry_base_seconds
        self.sleeper = sleeper

    def structured_generate(
        self,
        *,
        system_prompt: str,
        user_payload: dict[str, Any],
        prompt_version: str,
    ) -> StructuredLLMResponse:
        if not system_prompt.strip():
            raise ValueError("system_prompt cannot be empty")
        if not prompt_version.strip():
            raise ValueError("prompt_version cannot be empty")
        request_payload = {
            "model": self.model_id,
            "messages": [
                {"role": "system", "content": system_prompt},
                {
                    "role": "user",
                    "content": json.dumps(
                        user_payload,
                        ensure_ascii=False,
                        sort_keys=True,
                        separators=(",", ":"),
                    ),
                },
            ],
            "stream": False,
            "response_format": {"type": "json_object"},
            # DeepSeek documents that JSON Output can occasionally return an
            # empty ``content``. Stage0 needs only the final JSON, not a chain
            # of thought, so disable thinking and retry an empty final answer.
            "thinking": {"type": "disabled"},
            "max_tokens": 4096,
        }
        request_sha256 = hashlib.sha256(
            _canonical_json(request_payload)
        ).hexdigest()
        api_key = self.secret_resolver.resolve()
        if not api_key.strip():
            raise LLMProviderError(
                "PERMANENT_CONFIGURATION",
                "LLM API key is empty",
                retryable=False,
            )
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": "material-screening-agent/stage0-v1",
        }
        endpoint = f"{self.base_url}/chat/completions"
        status = 0
        response: dict[str, Any] | None = None
        for attempt in range(1, self.max_attempts + 1):
            try:
                status, response_body = self.transport.post_json(
                    url=endpoint,
                    headers=headers,
                    payload=request_payload,
                    timeout_seconds=self.timeout_seconds,
                    max_response_bytes=MAX_RESPONSE_BYTES,
                )
            except LLMProviderError as exc:
                if not exc.retryable or attempt == self.max_attempts:
                    raise
            else:
                if 200 <= status < 300:
                    try:
                        response = _parse_provider_response(
                            response_body, expected_model=self.model_id
                        )
                    except LLMProviderError as exc:
                        if exc.category != "EMPTY_CONTENT" or attempt == self.max_attempts:
                            if exc.category == "EMPTY_CONTENT":
                                raise LLMProviderError(
                                    "INVALID_RESPONSE",
                                    "LLM provider returned empty structured content after retry",
                                    retryable=False,
                                ) from exc
                            raise
                    else:
                        break
                else:
                    retryable = status == 429 or 500 <= status <= 599
                    if not retryable or attempt == self.max_attempts:
                        category = (
                            "AUTHENTICATION_FAILED"
                            if status in {401, 403}
                            else "TRANSIENT_EXTERNAL"
                            if retryable
                            else "PERMANENT_CONFIGURATION"
                        )
                        raise LLMProviderError(
                            category,
                            f"LLM provider returned HTTP status {status}",
                            retryable=retryable,
                        )
            if self.retry_base_seconds:
                self.sleeper(self.retry_base_seconds * attempt)

        if response is None:  # defensive: loop either returned a response or raised
            raise LLMProviderError(
                "TRANSIENT_EXTERNAL", "LLM provider did not return a usable response", retryable=True
            )
        content = response["content"]
        try:
            structured_payload = json.loads(content)
        except json.JSONDecodeError as exc:
            raise LLMProviderError(
                "INVALID_RESPONSE",
                "LLM provider content was not valid JSON",
                retryable=False,
            ) from exc
        if not isinstance(structured_payload, dict):
            raise LLMProviderError(
                "INVALID_RESPONSE",
                "LLM provider JSON content must be an object",
                retryable=False,
            )
        usage = response["usage"]
        return StructuredLLMResponse(
            payload=structured_payload,
            audit=LLMCallAudit(
                provider=self.name,
                provider_version=self.version,
                model_id=self.model_id,
                base_url=self.base_url,
                prompt_version=prompt_version,
                request_sha256=request_sha256,
                response_sha256=hashlib.sha256(
                    content.encode("utf-8")
                ).hexdigest(),
                thinking_mode="disabled",
                reasoning_effort="none",
                response_format="json_object",
                prompt_tokens=usage.get("prompt_tokens"),
                completion_tokens=usage.get("completion_tokens"),
                total_tokens=usage.get("total_tokens"),
            ),
        )


def _parse_provider_response(
    body: bytes, *, expected_model: str
) -> dict[str, Any]:
    try:
        payload = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise LLMProviderError(
            "INVALID_RESPONSE",
            "LLM provider returned an invalid JSON envelope",
            retryable=False,
        ) from exc
    if not isinstance(payload, dict):
        raise LLMProviderError(
            "INVALID_RESPONSE",
            "LLM provider envelope must be an object",
            retryable=False,
        )
    if payload.get("model") != expected_model:
        raise LLMProviderError(
            "INVALID_RESPONSE",
            "LLM provider returned an unexpected model identity",
            retryable=False,
        )
    choices = payload.get("choices")
    if not isinstance(choices, list) or len(choices) != 1:
        raise LLMProviderError(
            "INVALID_RESPONSE",
            "LLM provider must return exactly one choice",
            retryable=False,
        )
    message = choices[0].get("message") if isinstance(choices[0], dict) else None
    content = message.get("content") if isinstance(message, dict) else None
    if not isinstance(content, str) or not content.strip():
        raise LLMProviderError(
            "EMPTY_CONTENT",
            "LLM provider returned empty structured content",
            retryable=True,
        )
    usage = payload.get("usage", {})
    if not isinstance(usage, dict):
        raise LLMProviderError(
            "INVALID_RESPONSE",
            "LLM provider usage metadata must be an object",
            retryable=False,
        )
    parsed_usage: dict[str, int | None] = {}
    for field in ("prompt_tokens", "completion_tokens", "total_tokens"):
        value = usage.get(field)
        if value is not None and (
            isinstance(value, bool) or not isinstance(value, int) or value < 0
        ):
            raise LLMProviderError(
                "INVALID_RESPONSE",
                "LLM provider usage metadata is invalid",
                retryable=False,
            )
        parsed_usage[field] = value
    return {"content": content, "usage": parsed_usage}


def _validate_deepseek_base_url(value: str) -> str:
    normalized = value.rstrip("/")
    parsed = urlparse(normalized)
    if (
        normalized != DEEPSEEK_BASE_URL
        or parsed.scheme != "https"
        or parsed.hostname != "api.deepseek.com"
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError(
            f"DeepSeek Stage0 base URL must be exactly {DEEPSEEK_BASE_URL}"
        )
    return normalized


def _safe_label(value: str, name: str) -> str:
    selected = value.strip()
    if not selected or len(selected) > 128:
        raise ValueError(f"{name} must contain 1 to 128 characters")
    if any(ord(character) < 32 for character in selected):
        raise ValueError(f"{name} cannot contain control characters")
    return selected


def _canonical_json(payload: dict[str, Any]) -> bytes:
    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
