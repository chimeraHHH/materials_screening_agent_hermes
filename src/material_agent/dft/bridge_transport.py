"""Authenticated, bounded standard-library HTTP transport for VASPilot Bridge."""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any, Protocol, runtime_checkable
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin, urlsplit
from urllib.request import Request, urlopen


class BridgeError(RuntimeError):
    """Base class for structured bridge failures."""


class BridgeTransportError(BridgeError):
    """Retryable network or response-loss failure."""


class BridgeConflictError(BridgeError):
    """An idempotency key is bound to different immutable input."""


class BridgeNotFoundError(BridgeError):
    """The requested bridge workflow does not exist."""


class BridgeProtocolError(BridgeError):
    """The bridge returned an invalid or unsafe response."""


@runtime_checkable
class BridgeTransport(Protocol):
    def request(
        self,
        method: str,
        path: str,
        body: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]: ...


class UrllibBridgeTransport:
    """Small HTTP/JSON client with no new runtime dependency."""

    def __init__(
        self,
        *,
        base_url: str,
        bearer_token: str,
        timeout_seconds: float = 30.0,
        max_response_bytes: int = 2 * 1024 * 1024,
    ) -> None:
        parsed = urlsplit(base_url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ValueError("bridge base_url must be an absolute HTTP(S) URL")
        if parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError("bridge base_url must not embed credentials or query")
        local_hosts = {"localhost", "127.0.0.1", "::1"}
        if parsed.scheme != "https" and parsed.hostname not in local_hosts:
            raise ValueError("non-local bridge URLs must use HTTPS")
        if not bearer_token:
            raise ValueError("bridge bearer token is required")
        if timeout_seconds <= 0 or max_response_bytes <= 0:
            raise ValueError("bridge timeout and response limit must be positive")
        self.base_url = base_url.rstrip("/") + "/"
        self._bearer_token = bearer_token
        self.timeout_seconds = timeout_seconds
        self.max_response_bytes = max_response_bytes

    def request(
        self,
        method: str,
        path: str,
        body: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        if not path.startswith("/") or ".." in path.split("/"):
            raise ValueError("bridge path must be absolute and traversal-free")
        payload = None
        headers = {
            "Accept": "application/json",
            "Authorization": f"Bearer {self._bearer_token}",
        }
        if body is not None:
            payload = json.dumps(
                dict(body),
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            ).encode("utf-8")
            headers["Content-Type"] = "application/json"
        request = Request(
            urljoin(self.base_url, path.lstrip("/")),
            data=payload,
            headers=headers,
            method=method,
        )
        try:
            with urlopen(request, timeout=self.timeout_seconds) as response:
                content_type = response.headers.get_content_type()
                if content_type != "application/json":
                    raise BridgeProtocolError(
                        f"bridge returned unsupported content type: {content_type}"
                    )
                raw = response.read(self.max_response_bytes + 1)
        except HTTPError as exc:
            if exc.code == 404:
                raise BridgeNotFoundError("bridge workflow was not found") from exc
            if exc.code == 409:
                raise BridgeConflictError(
                    "bridge idempotency key conflicts with immutable input"
                ) from exc
            if exc.code in {408, 425, 429, 500, 502, 503, 504}:
                raise BridgeTransportError(
                    f"bridge returned retryable HTTP {exc.code}"
                ) from exc
            raise BridgeProtocolError(
                f"bridge returned non-retryable HTTP {exc.code}"
            ) from exc
        except (TimeoutError, URLError, OSError) as exc:
            raise BridgeTransportError("bridge request failed") from exc
        if len(raw) > self.max_response_bytes:
            raise BridgeProtocolError("bridge response exceeds configured limit")
        try:
            value = json.loads(raw)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise BridgeProtocolError("bridge response is not valid JSON") from exc
        if not isinstance(value, dict):
            raise BridgeProtocolError("bridge response must be a JSON object")
        return value
