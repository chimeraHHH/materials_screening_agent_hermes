"""Bounded, auditable document fetching for the inspiration capability.

This module is the only network-facing seam for optional article-body retrieval.
It accepts operator-owned exact host allowlists, never follows redirects
implicitly, never asks for compressed or PDF content, and returns untrusted bytes
for local extraction.  It deliberately does not parse, persist, or vectorize the
response.
"""

from __future__ import annotations

import hashlib
import ipaddress
import json
import math
import socket
import threading
import time
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from email.message import Message
from enum import StrEnum
from pathlib import Path
from typing import Literal, Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin, urlsplit, urlunsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from material_agent.inspiration.models import ComponentSnapshotV1


_TRANSIENT_HTTP_STATUSES = frozenset({408, 429, 500, 502, 503, 504})
_REDIRECT_HTTP_STATUSES = frozenset({301, 302, 303, 307, 308})
_PDF_MEDIA_TYPES = frozenset({"application/pdf", "application/x-pdf"})
_HTML_MEDIA_TYPES = frozenset({"text/html", "application/xhtml+xml"})
_JATS_XML_MEDIA_TYPES = frozenset(
    {
        "application/jats+xml",
        "application/vnd.jats+xml",
        "application/xml",
        "text/xml",
    }
)
_MAX_URL_LENGTH = 4_096
_MAX_RESPONSE_BYTES = 10_000_000
_MAX_TOTAL_BYTES = 100_000_000
_READ_CHUNK_BYTES = 64 * 1024


class DocumentFetchErrorCategory(StrEnum):
    """Stable routing category for a failed logical document fetch."""

    TRANSIENT = "TRANSIENT"
    PERMANENT = "PERMANENT"
    SECURITY = "SECURITY"
    BUDGET = "BUDGET"
    CONTRACT = "CONTRACT"


@dataclass(frozen=True, slots=True)
class FetchAttemptRecord:
    """One physical HTTP attempt without timestamps or URL query secrets."""

    request_id: str
    document_id: str
    attempt_number: int
    outcome: Literal["success", "redirect", "error"]
    error_code: str | None
    http_status: int | None
    response_bytes: int
    request_url: str
    request_url_sha256: str
    redirect_url: str | None = None
    redirect_url_sha256: str | None = None
    retry_delay_seconds: float = 0.0

    def __post_init__(self) -> None:
        _validate_identifier("request_id", self.request_id)
        _validate_identifier("document_id", self.document_id)
        if type(self.attempt_number) is not int or self.attempt_number < 1:
            raise ValueError("attempt_number must be a positive integer")
        if self.outcome not in {"success", "redirect", "error"}:
            raise ValueError("outcome must be success, redirect, or error")
        if self.outcome == "error" and not self.error_code:
            raise ValueError("failed attempts require an error_code")
        if self.outcome != "error" and self.error_code is not None:
            raise ValueError("non-error attempts cannot have an error_code")
        if self.http_status is not None and not 100 <= self.http_status <= 599:
            raise ValueError("http_status must be between 100 and 599")
        if (
            type(self.response_bytes) is not int
            or not 0 <= self.response_bytes <= _MAX_TOTAL_BYTES + 1
        ):
            raise ValueError("response_bytes is outside the supported range")
        _validate_safe_url("request_url", self.request_url)
        _validate_sha256("request_url_sha256", self.request_url_sha256)
        if self.outcome == "redirect":
            if not self.redirect_url or not self.redirect_url_sha256:
                raise ValueError("redirect attempts require a target URL and hash")
            _validate_safe_url("redirect_url", self.redirect_url)
            _validate_sha256("redirect_url_sha256", self.redirect_url_sha256)
        elif self.redirect_url is not None or self.redirect_url_sha256 is not None:
            raise ValueError("only redirect attempts may contain redirect fields")
        _validate_bounded_seconds(
            "retry_delay_seconds",
            self.retry_delay_seconds,
            maximum=120.0,
        )

    def to_dict(self) -> dict[str, object]:
        """Return a deterministic JSON-compatible audit record."""

        return {
            "request_id": self.request_id,
            "document_id": self.document_id,
            "attempt_number": self.attempt_number,
            "outcome": self.outcome,
            "error_code": self.error_code,
            "http_status": self.http_status,
            "response_bytes": self.response_bytes,
            "request_url": self.request_url,
            "request_url_sha256": self.request_url_sha256,
            "redirect_url": self.redirect_url,
            "redirect_url_sha256": self.redirect_url_sha256,
            "retry_delay_seconds": self.retry_delay_seconds,
        }


class DocumentFetchError(RuntimeError):
    """Typed failure carrying the complete physical-attempt ledger."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        category: DocumentFetchErrorCategory,
        http_status: int | None = None,
        response_bytes: int = 0,
        attempts: tuple[FetchAttemptRecord, ...] = (),
    ) -> None:
        if not isinstance(category, DocumentFetchErrorCategory):
            raise TypeError("category must be a DocumentFetchErrorCategory")
        if not code or len(code) > 128:
            raise ValueError("error code must be non-empty and at most 128 chars")
        if http_status is not None and not 100 <= http_status <= 599:
            raise ValueError("http_status must be between 100 and 599")
        if (
            type(response_bytes) is not int
            or not 0 <= response_bytes <= _MAX_TOTAL_BYTES + 1
        ):
            raise ValueError("response_bytes is outside the supported range")
        self.code = code
        self.message = message
        self.category = category
        self.http_status = http_status
        self.response_bytes = response_bytes
        self.attempts = tuple(attempts)
        super().__init__(f"{category.value}/{code}: {message}")

    @property
    def retryable(self) -> bool:
        return self.category is DocumentFetchErrorCategory.TRANSIENT

    def with_attempts(
        self,
        attempts: tuple[FetchAttemptRecord, ...],
    ) -> DocumentFetchError:
        """Copy this error while attaching a deterministic attempt ledger."""

        return DocumentFetchError(
            self.code,
            self.message,
            category=self.category,
            http_status=self.http_status,
            response_bytes=self.response_bytes,
            attempts=attempts,
        )


@dataclass(frozen=True, slots=True)
class DocumentFetchRequest:
    """One logical article-body request; policy is supplied separately."""

    request_id: str
    document_id: str
    url: str

    def __post_init__(self) -> None:
        _validate_identifier("request_id", self.request_id)
        _validate_identifier("document_id", self.document_id)
        if not isinstance(self.url, str) or not self.url or len(self.url) > _MAX_URL_LENGTH:
            raise ValueError("url must be non-empty and at most 4096 chars")


@dataclass(frozen=True, slots=True)
class FetchAllowance:
    """Run-ledger remainder and immutable per-request extraction policy."""

    remaining_requests: int
    remaining_total_bytes: int
    max_bytes_per_response: int
    timeout_seconds: int
    max_retries_per_request: int
    allow_html: bool
    allow_jats_xml: bool

    def __post_init__(self) -> None:
        _validate_int_range("remaining_requests", self.remaining_requests, 0, 2_000)
        _validate_int_range(
            "remaining_total_bytes",
            self.remaining_total_bytes,
            0,
            _MAX_TOTAL_BYTES,
        )
        _validate_int_range(
            "max_bytes_per_response",
            self.max_bytes_per_response,
            0,
            _MAX_RESPONSE_BYTES,
        )
        _validate_int_range("timeout_seconds", self.timeout_seconds, 1, 120)
        _validate_int_range(
            "max_retries_per_request",
            self.max_retries_per_request,
            0,
            5,
        )
        if type(self.allow_html) is not bool or type(self.allow_jats_xml) is not bool:
            raise TypeError("media-type allowance flags must be bool values")


@dataclass(frozen=True, slots=True)
class FetchedDocument:
    """Bounded untrusted response bytes ready for Artifact persistence."""

    request_id: str
    document_id: str
    requested_url: str
    requested_url_sha256: str
    resolved_url: str
    resolved_url_sha256: str
    media_type: str
    payload: bytes
    attempts: tuple[FetchAttemptRecord, ...]
    payload_sha256: str = field(init=False)

    def __post_init__(self) -> None:
        _validate_identifier("request_id", self.request_id)
        _validate_identifier("document_id", self.document_id)
        _validate_safe_url("requested_url", self.requested_url)
        _validate_safe_url("resolved_url", self.resolved_url)
        _validate_sha256("requested_url_sha256", self.requested_url_sha256)
        _validate_sha256("resolved_url_sha256", self.resolved_url_sha256)
        if not isinstance(self.media_type, str) or not self.media_type:
            raise ValueError("media_type must be non-empty")
        if not isinstance(self.payload, bytes):
            raise TypeError("payload must be bytes")
        if not self.attempts or self.attempts[-1].outcome != "success":
            raise ValueError("fetched documents require a terminal successful attempt")
        object.__setattr__(
            self,
            "payload_sha256",
            hashlib.sha256(self.payload).hexdigest(),
        )

    @property
    def physical_request_count(self) -> int:
        return len(self.attempts)

    @property
    def response_bytes(self) -> int:
        return sum(attempt.response_bytes for attempt in self.attempts)


class DocumentFetcher(Protocol):
    """Injectable body-fetch boundary used by the inspiration runner."""

    component: ComponentSnapshotV1
    network_access: bool

    def fetch(
        self,
        request: DocumentFetchRequest,
        *,
        allowance: FetchAllowance,
    ) -> FetchedDocument:
        """Return one bounded body or raise a typed, auditable failure."""


class OpenFetchResponse(Protocol):
    """Headers-first response; callers decide whether any body may be read."""

    status_code: int
    headers: Mapping[str, str] | Message

    def read(self, size: int) -> bytes:
        """Read at most ``size`` bytes from the response stream."""

    def close(self) -> None:
        """Release the response stream."""


class DocumentFetchTransport(Protocol):
    """Small injectable transport that must not follow redirects itself."""

    def open(
        self,
        url: str,
        *,
        headers: Mapping[str, str],
        timeout_seconds: int,
    ) -> OpenFetchResponse:
        """Open a single physical HTTPS response without redirect following."""


class _NoRedirectHandler(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001
        del req, fp, code, msg, headers, newurl
        return None


class _UrllibOpenResponse:
    def __init__(self, response) -> None:  # noqa: ANN001
        self._response = response
        self.status_code = int(response.getcode())
        self.headers = response.headers

    def read(self, size: int) -> bytes:
        return self._response.read(size)

    def close(self) -> None:
        self._response.close()


class UrllibNoRedirectTransport:
    """TLS-verifying stdlib transport with automatic redirects disabled."""

    def __init__(self) -> None:
        self._opener = build_opener(_NoRedirectHandler())

    def open(
        self,
        url: str,
        *,
        headers: Mapping[str, str],
        timeout_seconds: int,
    ) -> OpenFetchResponse:
        request = Request(url, headers=dict(headers), method="GET")
        try:
            response = self._opener.open(request, timeout=timeout_seconds)  # noqa: S310
        except HTTPError as error:
            response = error
        return _UrllibOpenResponse(response)


AddressResolver = Callable[[str, int], Iterable[str]]


class SafeNetworkDocumentFetcher:
    """HTTPS body fetcher confined to an operator-owned exact host allowlist."""

    network_access = True

    def __init__(
        self,
        *,
        allowed_hosts: Iterable[str],
        transport: DocumentFetchTransport | None = None,
        address_resolver: AddressResolver | None = None,
        verify_dns_addresses: bool = True,
        max_redirects: int = 3,
        retry_backoff_seconds: float = 0.5,
        max_retry_delay_seconds: float = 5.0,
        max_total_wait_seconds: float = 15.0,
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        self.allowed_hosts = _normalize_allowed_hosts(allowed_hosts)
        if not self.allowed_hosts:
            raise ValueError("allowed_hosts must contain at least one exact host")
        _validate_int_range("max_redirects", max_redirects, 0, 10)
        _validate_bounded_seconds(
            "retry_backoff_seconds",
            retry_backoff_seconds,
            maximum=30.0,
        )
        _validate_bounded_seconds(
            "max_retry_delay_seconds",
            max_retry_delay_seconds,
            maximum=60.0,
        )
        _validate_bounded_seconds(
            "max_total_wait_seconds",
            max_total_wait_seconds,
            maximum=120.0,
        )
        if type(verify_dns_addresses) is not bool:
            raise TypeError("verify_dns_addresses must be a bool")
        self.transport = transport or UrllibNoRedirectTransport()
        self.address_resolver = address_resolver or _resolve_host_addresses
        self.verify_dns_addresses = verify_dns_addresses
        self.max_redirects = max_redirects
        self.retry_backoff_seconds = float(retry_backoff_seconds)
        self.max_retry_delay_seconds = float(max_retry_delay_seconds)
        self.max_total_wait_seconds = float(max_total_wait_seconds)
        self.sleeper = sleeper
        self.component = _component_snapshot(
            component_id="safe-network-document-fetcher",
            version="v1",
            config={
                "allowed_hosts": sorted(self.allowed_hosts),
                "max_redirects": self.max_redirects,
                "retry_backoff_seconds": self.retry_backoff_seconds,
                "max_retry_delay_seconds": self.max_retry_delay_seconds,
                "max_total_wait_seconds": self.max_total_wait_seconds,
                "verify_dns_addresses": self.verify_dns_addresses,
                "transport": _qualified_name(self.transport),
            },
        )

    def fetch(
        self,
        request: DocumentFetchRequest,
        *,
        allowance: FetchAllowance,
    ) -> FetchedDocument:
        if not isinstance(request, DocumentFetchRequest):
            raise DocumentFetchError(
                "INVALID_FETCH_REQUEST",
                "request must be a DocumentFetchRequest",
                category=DocumentFetchErrorCategory.CONTRACT,
            )
        if not isinstance(allowance, FetchAllowance):
            raise DocumentFetchError(
                "INVALID_FETCH_ALLOWANCE",
                "allowance must be a FetchAllowance",
                category=DocumentFetchErrorCategory.CONTRACT,
            )
        if allowance.remaining_requests == 0:
            raise DocumentFetchError(
                "FETCH_REQUEST_BUDGET_EXHAUSTED",
                "no physical document requests remain",
                category=DocumentFetchErrorCategory.BUDGET,
            )
        if (
            allowance.remaining_total_bytes == 0
            or allowance.max_bytes_per_response == 0
        ):
            raise DocumentFetchError(
                "FETCH_BYTE_BUDGET_EXHAUSTED",
                "no document response bytes remain",
                category=DocumentFetchErrorCategory.BUDGET,
            )

        requested_url = request.url
        current_url = request.url
        self._preflight_url(current_url)
        attempts: list[FetchAttemptRecord] = []
        redirects = 0
        retries = 0
        total_wait_seconds = 0.0
        remaining_bytes = allowance.remaining_total_bytes

        while True:
            if len(attempts) >= allowance.remaining_requests:
                raise DocumentFetchError(
                    "FETCH_REQUEST_BUDGET_EXHAUSTED",
                    "redirects or retries exhausted the physical request budget",
                    category=DocumentFetchErrorCategory.BUDGET,
                    attempts=tuple(attempts),
                )
            if remaining_bytes <= 0:
                raise DocumentFetchError(
                    "FETCH_BYTE_BUDGET_EXHAUSTED",
                    "earlier attempts exhausted the total response-byte budget",
                    category=DocumentFetchErrorCategory.BUDGET,
                    attempts=tuple(attempts),
                )

            self._preflight_url(current_url)
            attempt_number = len(attempts) + 1
            response: OpenFetchResponse | None = None
            try:
                response = self.transport.open(
                    current_url,
                    headers=_request_headers(allowance),
                    timeout_seconds=allowance.timeout_seconds,
                )
            except DocumentFetchError as error:
                should_retry, retry_delay = self._retry_decision(
                    error,
                    retries=retries,
                    attempts_used=attempt_number,
                    allowance=allowance,
                    total_wait_seconds=total_wait_seconds,
                )
                attempts.append(
                    _attempt_error(
                        request=request,
                        attempt_number=attempt_number,
                        url=current_url,
                        error=error,
                        retry_delay_seconds=retry_delay,
                    )
                )
                remaining_bytes = max(0, remaining_bytes - error.response_bytes)
                if not should_retry:
                    raise error.with_attempts(tuple(attempts)) from error
                retries += 1
                if retry_delay:
                    self.sleeper(retry_delay)
                    total_wait_seconds += retry_delay
                continue
            except (TimeoutError, socket.timeout, URLError, OSError) as cause:
                error = DocumentFetchError(
                    "NETWORK_ERROR",
                    "document endpoint could not be opened within the bounded request",
                    category=DocumentFetchErrorCategory.TRANSIENT,
                )
                should_retry, retry_delay = self._retry_decision(
                    error,
                    retries=retries,
                    attempts_used=attempt_number,
                    allowance=allowance,
                    total_wait_seconds=total_wait_seconds,
                )
                attempts.append(
                    _attempt_error(
                        request=request,
                        attempt_number=attempt_number,
                        url=current_url,
                        error=error,
                        retry_delay_seconds=retry_delay,
                    )
                )
                if not should_retry:
                    raise error.with_attempts(tuple(attempts)) from cause
                retries += 1
                if retry_delay:
                    self.sleeper(retry_delay)
                    total_wait_seconds += retry_delay
                continue

            try:
                status = _response_status(response)
                if status in _REDIRECT_HTTP_STATUSES:
                    target = _redirect_target(response.headers, base_url=current_url)
                    attempts.append(
                        FetchAttemptRecord(
                            request_id=request.request_id,
                            document_id=request.document_id,
                            attempt_number=attempt_number,
                            outcome="redirect",
                            error_code=None,
                            http_status=status,
                            response_bytes=0,
                            request_url=_safe_url(current_url),
                            request_url_sha256=_url_sha256(current_url),
                            redirect_url=_safe_url(target),
                            redirect_url_sha256=_url_sha256(target),
                        )
                    )
                    if redirects >= self.max_redirects:
                        raise DocumentFetchError(
                            "TOO_MANY_REDIRECTS",
                            "document endpoint exceeded the redirect-hop limit",
                            category=DocumentFetchErrorCategory.SECURITY,
                            http_status=status,
                            attempts=tuple(attempts),
                        )
                    if any(
                        attempt.request_url_sha256 == _url_sha256(target)
                        for attempt in attempts
                    ):
                        raise DocumentFetchError(
                            "REDIRECT_LOOP",
                            "document endpoint returned a redirect loop",
                            category=DocumentFetchErrorCategory.SECURITY,
                            http_status=status,
                            attempts=tuple(attempts),
                        )
                    try:
                        self._preflight_url(target)
                    except DocumentFetchError as error:
                        raise error.with_attempts(tuple(attempts)) from error
                    redirects += 1
                    current_url = target
                    continue

                if not 200 <= status <= 299:
                    error = _http_status_error(status)
                    should_retry, retry_delay = self._retry_decision(
                        error,
                        retries=retries,
                        attempts_used=attempt_number,
                        allowance=allowance,
                        total_wait_seconds=total_wait_seconds,
                        retry_after=_header_value(response.headers, "Retry-After"),
                    )
                    attempts.append(
                        _attempt_error(
                            request=request,
                            attempt_number=attempt_number,
                            url=current_url,
                            error=error,
                            retry_delay_seconds=retry_delay,
                        )
                    )
                    if not should_retry:
                        raise error.with_attempts(tuple(attempts)) from error
                    retries += 1
                    if retry_delay:
                        self.sleeper(retry_delay)
                        total_wait_seconds += retry_delay
                    continue

                response_cap = min(
                    allowance.max_bytes_per_response,
                    remaining_bytes,
                )
                media_type = _validate_response_headers(
                    response.headers,
                    allowance=allowance,
                    response_cap=response_cap,
                )
                try:
                    payload = _read_bounded(response, response_cap=response_cap)
                except DocumentFetchError as error:
                    remaining_bytes = max(0, remaining_bytes - error.response_bytes)
                    should_retry, retry_delay = self._retry_decision(
                        error,
                        retries=retries,
                        attempts_used=attempt_number,
                        allowance=allowance,
                        total_wait_seconds=total_wait_seconds,
                    )
                    attempts.append(
                        _attempt_error(
                            request=request,
                            attempt_number=attempt_number,
                            url=current_url,
                            error=error,
                            retry_delay_seconds=retry_delay,
                            http_status=status,
                        )
                    )
                    if not should_retry:
                        raise error.with_attempts(tuple(attempts)) from error
                    retries += 1
                    if retry_delay:
                        self.sleeper(retry_delay)
                        total_wait_seconds += retry_delay
                    continue

                remaining_bytes = max(0, remaining_bytes - len(payload))
                if _looks_like_pdf(payload):
                    error = DocumentFetchError(
                        "PDF_CONTENT_FORBIDDEN",
                        "document payload has PDF magic bytes",
                        category=DocumentFetchErrorCategory.SECURITY,
                        http_status=status,
                        response_bytes=len(payload),
                    )
                    attempts.append(
                        _attempt_error(
                            request=request,
                            attempt_number=attempt_number,
                            url=current_url,
                            error=error,
                            http_status=status,
                        )
                    )
                    raise error.with_attempts(tuple(attempts)) from error

                attempts.append(
                    FetchAttemptRecord(
                        request_id=request.request_id,
                        document_id=request.document_id,
                        attempt_number=attempt_number,
                        outcome="success",
                        error_code=None,
                        http_status=status,
                        response_bytes=len(payload),
                        request_url=_safe_url(current_url),
                        request_url_sha256=_url_sha256(current_url),
                    )
                )
                return FetchedDocument(
                    request_id=request.request_id,
                    document_id=request.document_id,
                    requested_url=_safe_url(requested_url),
                    requested_url_sha256=_url_sha256(requested_url),
                    resolved_url=_safe_url(current_url),
                    resolved_url_sha256=_url_sha256(current_url),
                    media_type=media_type,
                    payload=payload,
                    attempts=tuple(attempts),
                )
            except DocumentFetchError as error:
                if error.attempts:
                    raise
                attempts.append(
                    _attempt_error(
                        request=request,
                        attempt_number=attempt_number,
                        url=current_url,
                        error=error,
                        http_status=error.http_status,
                    )
                )
                raise error.with_attempts(tuple(attempts)) from error
            finally:
                try:
                    response.close()
                except Exception:  # pragma: no cover - close failure cannot alter evidence
                    pass

    def _preflight_url(self, url: str) -> None:
        host = _validate_fetch_url(url, allowed_hosts=self.allowed_hosts)
        if not self.verify_dns_addresses:
            return
        try:
            addresses = tuple(self.address_resolver(host, 443))
        except DocumentFetchError:
            raise
        except (socket.gaierror, TimeoutError, OSError) as error:
            raise DocumentFetchError(
                "DNS_RESOLUTION_ERROR",
                "allowlisted document host could not be resolved",
                category=DocumentFetchErrorCategory.TRANSIENT,
            ) from error
        if not addresses:
            raise DocumentFetchError(
                "DNS_RESOLUTION_ERROR",
                "allowlisted document host resolved to no addresses",
                category=DocumentFetchErrorCategory.TRANSIENT,
            )
        for address in addresses:
            try:
                parsed = ipaddress.ip_address(address)
            except ValueError as error:
                raise DocumentFetchError(
                    "INVALID_RESOLVED_ADDRESS",
                    "host resolver returned a non-IP address",
                    category=DocumentFetchErrorCategory.CONTRACT,
                ) from error
            if not parsed.is_global:
                raise DocumentFetchError(
                    "NON_PUBLIC_RESOLVED_ADDRESS",
                    "document host resolved to a private or reserved address",
                    category=DocumentFetchErrorCategory.SECURITY,
                )

    def _retry_decision(
        self,
        error: DocumentFetchError,
        *,
        retries: int,
        attempts_used: int,
        allowance: FetchAllowance,
        total_wait_seconds: float,
        retry_after: str | None = None,
    ) -> tuple[bool, float]:
        if (
            not error.retryable
            or retries >= allowance.max_retries_per_request
            or attempts_used >= allowance.remaining_requests
        ):
            return False, 0.0
        delay = min(
            self.retry_backoff_seconds * (2**retries),
            self.max_retry_delay_seconds,
        )
        if retry_after is not None and retry_after.strip().isdigit():
            delay = min(float(int(retry_after.strip())), self.max_retry_delay_seconds)
        if total_wait_seconds + delay > self.max_total_wait_seconds:
            return False, 0.0
        return True, delay


@dataclass(frozen=True, slots=True)
class FixtureFetchResponse:
    """One deterministic, offline headers/body response."""

    payload: bytes = b""
    media_type: str = "text/html"
    status_code: int = 200
    headers: tuple[tuple[str, str], ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.payload, bytes):
            raise TypeError("fixture payload must be bytes")
        if not isinstance(self.media_type, str) or not self.media_type:
            raise ValueError("fixture media_type must be non-empty")
        if type(self.status_code) is not int or not 100 <= self.status_code <= 599:
            raise ValueError("fixture status_code must be between 100 and 599")
        for name, value in self.headers:
            if not name or "\n" in name or "\r" in name:
                raise ValueError("fixture header names must be safe and non-empty")
            if "\n" in value or "\r" in value:
                raise ValueError("fixture header values cannot contain newlines")


class _FixtureOpenResponse:
    def __init__(self, fixture: FixtureFetchResponse) -> None:
        self.status_code = fixture.status_code
        headers = dict(fixture.headers)
        if not any(name.casefold() == "content-type" for name in headers):
            headers["Content-Type"] = fixture.media_type
        if not any(name.casefold() == "content-length" for name in headers):
            headers["Content-Length"] = str(len(fixture.payload))
        self.headers = headers
        self._payload = fixture.payload
        self._offset = 0

    def read(self, size: int) -> bytes:
        start = self._offset
        end = min(len(self._payload), start + size)
        self._offset = end
        return self._payload[start:end]

    def close(self) -> None:
        return None


class _FixtureTransport:
    def __init__(
        self,
        fixtures: Mapping[str, tuple[FixtureFetchResponse, ...]],
    ) -> None:
        self._fixtures = dict(fixtures)
        self._positions: dict[str, int] = {}
        self._lock = threading.Lock()

    def open(
        self,
        url: str,
        *,
        headers: Mapping[str, str],
        timeout_seconds: int,
    ) -> OpenFetchResponse:
        del headers, timeout_seconds
        sequence = self._fixtures.get(url)
        if not sequence:
            raise DocumentFetchError(
                "FETCH_FIXTURE_NOT_FOUND",
                "offline document URL is absent from the fixture map",
                category=DocumentFetchErrorCategory.CONTRACT,
            )
        with self._lock:
            position = self._positions.get(url, 0)
            self._positions[url] = position + 1
        return _FixtureOpenResponse(sequence[min(position, len(sequence) - 1)])


class FixtureDocumentFetcher(SafeNetworkDocumentFetcher):
    """Exercise URL, redirect, media, retry, and budget logic offline.

    Fixture transport does not exercise DNS, TLS, or peer-address binding and
    therefore cannot establish that public-network body fetching is safe.
    """

    network_access = False

    def __init__(
        self,
        *,
        fixtures: Mapping[
            str,
            FixtureFetchResponse | Sequence[FixtureFetchResponse],
        ],
        allowed_hosts: Iterable[str],
        max_redirects: int = 3,
        retry_backoff_seconds: float = 0.0,
        max_retry_delay_seconds: float = 0.0,
        max_total_wait_seconds: float = 0.0,
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        normalized: dict[str, tuple[FixtureFetchResponse, ...]] = {}
        for url, value in fixtures.items():
            if isinstance(value, FixtureFetchResponse):
                sequence = (value,)
            else:
                sequence = tuple(value)
            if not sequence or not all(
                isinstance(item, FixtureFetchResponse) for item in sequence
            ):
                raise TypeError("fixture values must contain FixtureFetchResponse values")
            normalized[url] = sequence
        transport = _FixtureTransport(normalized)
        super().__init__(
            allowed_hosts=allowed_hosts,
            transport=transport,
            verify_dns_addresses=False,
            max_redirects=max_redirects,
            retry_backoff_seconds=retry_backoff_seconds,
            max_retry_delay_seconds=max_retry_delay_seconds,
            max_total_wait_seconds=max_total_wait_seconds,
            sleeper=sleeper,
        )
        self.component = _component_snapshot(
            component_id="fixture-document-fetcher",
            version="v1",
            config={
                "allowed_hosts": sorted(self.allowed_hosts),
                "fixtures": {
                    url: [
                        {
                            "headers": sorted(item.headers),
                            "media_type": item.media_type,
                            "payload_sha256": hashlib.sha256(item.payload).hexdigest(),
                            "status_code": item.status_code,
                        }
                        for item in sequence
                    ]
                    for url, sequence in sorted(normalized.items())
                },
                "max_redirects": self.max_redirects,
            },
        )


class DisabledDocumentFetcher:
    """Default fail-closed fetcher for metadata-only execution profiles."""

    network_access = False
    component: ComponentSnapshotV1

    def fetch(
        self,
        request: DocumentFetchRequest,
        *,
        allowance: FetchAllowance,
    ) -> FetchedDocument:
        if not isinstance(request, DocumentFetchRequest) or not isinstance(
            allowance,
            FetchAllowance,
        ):
            raise DocumentFetchError(
                "INVALID_FETCH_CALL",
                "disabled fetcher still requires typed request and allowance values",
                category=DocumentFetchErrorCategory.CONTRACT,
            )
        raise DocumentFetchError(
            "DOCUMENT_FETCH_DISABLED",
            "the active execution profile is metadata-only",
            category=DocumentFetchErrorCategory.PERMANENT,
        )


def _request_headers(allowance: FetchAllowance) -> dict[str, str]:
    accepted = ["application/json", "application/ld+json"]
    if allowance.allow_jats_xml:
        accepted.extend(["application/vnd.jats+xml", "application/xml", "text/xml"])
    if allowance.allow_html:
        accepted.extend(["text/html", "application/xhtml+xml"])
    return {
        "Accept": ", ".join(accepted),
        "Accept-Encoding": "identity",
        "User-Agent": "materials-screening-agent/0.1 (bounded-evidence-fetch)",
    }


def _validate_response_headers(
    headers: Mapping[str, str] | Message,
    *,
    allowance: FetchAllowance,
    response_cap: int,
) -> str:
    content_encoding = _header_value(headers, "Content-Encoding")
    if content_encoding is not None and content_encoding.casefold() not in {
        "",
        "identity",
    }:
        raise DocumentFetchError(
            "UNEXPECTED_CONTENT_ENCODING",
            "document endpoint ignored the identity encoding requirement",
            category=DocumentFetchErrorCategory.SECURITY,
        )
    raw_media_type = _header_value(headers, "Content-Type")
    if raw_media_type is None:
        raise DocumentFetchError(
            "MISSING_MEDIA_TYPE",
            "document endpoint omitted Content-Type",
            category=DocumentFetchErrorCategory.CONTRACT,
        )
    media_type = raw_media_type.split(";", 1)[0].strip().casefold()
    if media_type in _PDF_MEDIA_TYPES:
        raise DocumentFetchError(
            "PDF_CONTENT_FORBIDDEN",
            "PDF full text is disabled for the inspiration fetch path",
            category=DocumentFetchErrorCategory.SECURITY,
        )
    if media_type in _HTML_MEDIA_TYPES:
        allowed = allowance.allow_html
    elif media_type in _JATS_XML_MEDIA_TYPES:
        allowed = allowance.allow_jats_xml
    else:
        allowed = media_type == "application/json" or media_type.endswith("+json")
    if not allowed:
        raise DocumentFetchError(
            "UNSUPPORTED_MEDIA_TYPE",
            f"document media type {media_type!r} is not allowed by policy",
            category=DocumentFetchErrorCategory.SECURITY,
        )

    content_length = _header_value(headers, "Content-Length")
    if content_length is not None:
        try:
            declared_length = int(content_length)
        except ValueError as error:
            raise DocumentFetchError(
                "INVALID_CONTENT_LENGTH",
                "document endpoint returned a non-integer Content-Length",
                category=DocumentFetchErrorCategory.CONTRACT,
            ) from error
        if declared_length < 0:
            raise DocumentFetchError(
                "INVALID_CONTENT_LENGTH",
                "document endpoint returned a negative Content-Length",
                category=DocumentFetchErrorCategory.CONTRACT,
            )
        if declared_length > response_cap:
            raise DocumentFetchError(
                "FETCH_RESPONSE_BUDGET_EXCEEDED",
                "declared document size exceeds the remaining byte budget",
                category=DocumentFetchErrorCategory.BUDGET,
            )
    return media_type


def _read_bounded(
    response: OpenFetchResponse,
    *,
    response_cap: int,
) -> bytes:
    collected = bytearray()
    limit = response_cap + 1
    while len(collected) < limit:
        request_size = min(_READ_CHUNK_BYTES, limit - len(collected))
        try:
            chunk = response.read(request_size)
        except DocumentFetchError as error:
            response_bytes = min(
                _MAX_TOTAL_BYTES + 1,
                len(collected) + error.response_bytes,
            )
            raise DocumentFetchError(
                error.code,
                error.message,
                category=error.category,
                http_status=error.http_status,
                response_bytes=response_bytes,
            ) from error
        except (TimeoutError, socket.timeout, URLError, OSError) as error:
            raise DocumentFetchError(
                "NETWORK_ERROR",
                "document response stream failed before completion",
                category=DocumentFetchErrorCategory.TRANSIENT,
                response_bytes=len(collected),
            ) from error
        if not isinstance(chunk, bytes):
            raise DocumentFetchError(
                "INVALID_NETWORK_PAYLOAD",
                "document transport returned a non-bytes body chunk",
                category=DocumentFetchErrorCategory.CONTRACT,
                response_bytes=len(collected),
            )
        if len(chunk) > request_size:
            collected.extend(chunk[:request_size])
            raise DocumentFetchError(
                "TRANSPORT_READ_CONTRACT_VIOLATION",
                "document transport returned more bytes than requested",
                category=DocumentFetchErrorCategory.CONTRACT,
                response_bytes=len(collected),
            )
        if not chunk:
            break
        collected.extend(chunk)
    if len(collected) > response_cap:
        raise DocumentFetchError(
            "FETCH_RESPONSE_BUDGET_EXCEEDED",
            "document response exceeded its byte budget while streaming",
            category=DocumentFetchErrorCategory.BUDGET,
            response_bytes=len(collected),
        )
    return bytes(collected)


def _response_status(response: OpenFetchResponse) -> int:
    status = getattr(response, "status_code", None)
    if type(status) is not int or not 100 <= status <= 599:
        raise DocumentFetchError(
            "INVALID_HTTP_STATUS",
            "document transport returned an invalid HTTP status",
            category=DocumentFetchErrorCategory.CONTRACT,
        )
    if not hasattr(response, "headers") or not hasattr(response.headers, "items"):
        raise DocumentFetchError(
            "INVALID_HTTP_HEADERS",
            "document transport returned invalid response headers",
            category=DocumentFetchErrorCategory.CONTRACT,
        )
    return status


def _redirect_target(
    headers: Mapping[str, str] | Message,
    *,
    base_url: str,
) -> str:
    location = _header_value(headers, "Location")
    if location is None or not location.strip():
        raise DocumentFetchError(
            "MISSING_REDIRECT_LOCATION",
            "redirect response omitted Location",
            category=DocumentFetchErrorCategory.CONTRACT,
        )
    if len(location) > _MAX_URL_LENGTH or "\r" in location or "\n" in location:
        raise DocumentFetchError(
            "INVALID_REDIRECT_LOCATION",
            "redirect Location is unsafe or too long",
            category=DocumentFetchErrorCategory.SECURITY,
        )
    return urljoin(base_url, location.strip())


def _header_value(
    headers: Mapping[str, str] | Message,
    name: str,
) -> str | None:
    values = [
        str(value).strip()
        for candidate, value in headers.items()
        if str(candidate).casefold() == name.casefold()
    ]
    if not values:
        return None
    if len(set(values)) != 1:
        raise DocumentFetchError(
            "AMBIGUOUS_RESPONSE_HEADER",
            f"document endpoint returned conflicting {name} headers",
            category=DocumentFetchErrorCategory.SECURITY,
        )
    return values[0]


def _http_status_error(status: int) -> DocumentFetchError:
    if status in _TRANSIENT_HTTP_STATUSES:
        return DocumentFetchError(
            "TRANSIENT_HTTP_ERROR",
            f"document endpoint returned HTTP {status}",
            category=DocumentFetchErrorCategory.TRANSIENT,
            http_status=status,
        )
    return DocumentFetchError(
        "HTTP_ERROR",
        f"document endpoint returned HTTP {status}",
        category=DocumentFetchErrorCategory.PERMANENT,
        http_status=status,
    )


def _attempt_error(
    *,
    request: DocumentFetchRequest,
    attempt_number: int,
    url: str,
    error: DocumentFetchError,
    retry_delay_seconds: float = 0.0,
    http_status: int | None = None,
) -> FetchAttemptRecord:
    return FetchAttemptRecord(
        request_id=request.request_id,
        document_id=request.document_id,
        attempt_number=attempt_number,
        outcome="error",
        error_code=error.code,
        http_status=error.http_status if http_status is None else http_status,
        response_bytes=error.response_bytes,
        request_url=_safe_url(url),
        request_url_sha256=_url_sha256(url),
        retry_delay_seconds=retry_delay_seconds,
    )


def _validate_fetch_url(url: str, *, allowed_hosts: frozenset[str]) -> str:
    if not isinstance(url, str) or not url or len(url) > _MAX_URL_LENGTH:
        raise DocumentFetchError(
            "INVALID_DOCUMENT_URL",
            "document URL must be non-empty and bounded",
            category=DocumentFetchErrorCategory.SECURITY,
        )
    if any(ord(character) < 0x20 for character in url) or "\\" in url:
        raise DocumentFetchError(
            "INVALID_DOCUMENT_URL",
            "document URL contains unsafe characters",
            category=DocumentFetchErrorCategory.SECURITY,
        )
    try:
        parsed = urlsplit(url)
        port = parsed.port
    except ValueError as error:
        raise DocumentFetchError(
            "INVALID_DOCUMENT_URL",
            "document URL has an invalid authority or port",
            category=DocumentFetchErrorCategory.SECURITY,
        ) from error
    if parsed.scheme.casefold() != "https":
        raise DocumentFetchError(
            "HTTPS_REQUIRED",
            "document fetches require HTTPS",
            category=DocumentFetchErrorCategory.SECURITY,
        )
    if parsed.username is not None or parsed.password is not None:
        raise DocumentFetchError(
            "URL_USERINFO_FORBIDDEN",
            "document URLs cannot contain user information",
            category=DocumentFetchErrorCategory.SECURITY,
        )
    host = parsed.hostname.casefold() if parsed.hostname else ""
    if not host:
        raise DocumentFetchError(
            "INVALID_DOCUMENT_HOST",
            "document URL requires a host",
            category=DocumentFetchErrorCategory.SECURITY,
        )
    if port not in {None, 443}:
        raise DocumentFetchError(
            "NON_STANDARD_HTTPS_PORT",
            "document URLs may use only HTTPS port 443",
            category=DocumentFetchErrorCategory.SECURITY,
        )
    try:
        ipaddress.ip_address(host)
    except ValueError:
        pass
    else:
        raise DocumentFetchError(
            "IP_LITERAL_FORBIDDEN",
            "document URLs cannot use IP literals",
            category=DocumentFetchErrorCategory.SECURITY,
        )
    if host == "localhost" or host.endswith(".localhost"):
        raise DocumentFetchError(
            "LOCALHOST_FORBIDDEN",
            "document URLs cannot use localhost",
            category=DocumentFetchErrorCategory.SECURITY,
        )
    if host not in allowed_hosts:
        raise DocumentFetchError(
            "DOCUMENT_HOST_NOT_ALLOWLISTED",
            "document host is absent from the operator allowlist",
            category=DocumentFetchErrorCategory.SECURITY,
        )
    if parsed.fragment:
        raise DocumentFetchError(
            "URL_FRAGMENT_FORBIDDEN",
            "document URLs cannot contain fragments",
            category=DocumentFetchErrorCategory.SECURITY,
        )
    return host


def _normalize_allowed_hosts(hosts: Iterable[str]) -> frozenset[str]:
    normalized: set[str] = set()
    for value in hosts:
        if not isinstance(value, str):
            raise TypeError("allowlisted hosts must be strings")
        host = value.strip().casefold().rstrip(".")
        if (
            not host
            or len(host) > 253
            or ":" in host
            or "/" in host
            or "*" in host
            or host == "localhost"
            or host.endswith(".localhost")
        ):
            raise ValueError("allowed_hosts must contain exact DNS host names")
        try:
            host.encode("ascii")
        except UnicodeEncodeError as error:
            raise ValueError("allowed_hosts must use ASCII/punycode DNS names") from error
        try:
            ipaddress.ip_address(host)
        except ValueError:
            pass
        else:
            raise ValueError("allowed_hosts cannot contain IP literals")
        labels = host.split(".")
        if any(
            not label
            or len(label) > 63
            or label.startswith("-")
            or label.endswith("-")
            or not all(character.isalnum() or character == "-" for character in label)
            for label in labels
        ):
            raise ValueError("allowed_hosts contains an invalid DNS name")
        normalized.add(host)
    return frozenset(normalized)


def _resolve_host_addresses(host: str, port: int) -> tuple[str, ...]:
    records = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    return tuple(sorted({record[4][0] for record in records}))


def _safe_url(url: str) -> str:
    parsed = urlsplit(url)
    host = parsed.hostname.casefold() if parsed.hostname else "invalid"
    netloc = host if parsed.port in {None, 443} else f"{host}:{parsed.port}"
    return urlunsplit((parsed.scheme.casefold(), netloc, parsed.path or "/", "", ""))


def _url_sha256(url: str) -> str:
    return hashlib.sha256(url.encode("utf-8")).hexdigest()


def _looks_like_pdf(payload: bytes) -> bool:
    return payload.lstrip()[:5].lower() == b"%pdf-"


def _component_snapshot(
    *,
    component_id: str,
    version: str,
    config: Mapping[str, object],
) -> ComponentSnapshotV1:
    source_sha256 = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    fingerprint = json.dumps(
        {"config": config, "source_sha256": source_sha256},
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    ).encode("utf-8")
    return ComponentSnapshotV1(
        component_id=component_id,
        version=version,
        implementation_sha256=hashlib.sha256(fingerprint).hexdigest(),
    )


def _qualified_name(value: object) -> str:
    selected = type(value)
    return f"{selected.__module__}.{selected.__qualname__}"


def _validate_identifier(name: str, value: str) -> None:
    if not isinstance(value, str) or not value.strip() or len(value) > 128:
        raise ValueError(f"{name} must be non-empty and at most 128 chars")


def _validate_safe_url(name: str, value: str) -> None:
    if not isinstance(value, str) or not value.startswith("https://"):
        raise ValueError(f"{name} must be a safe HTTPS URL")
    parsed = urlsplit(value)
    if parsed.query or parsed.fragment or parsed.username or parsed.password:
        raise ValueError(f"{name} cannot contain query, fragment, or userinfo")


def _validate_sha256(name: str, value: str) -> None:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise ValueError(f"{name} must be a lowercase SHA-256 digest")


def _validate_int_range(name: str, value: int, minimum: int, maximum: int) -> None:
    if type(value) is not int or not minimum <= value <= maximum:
        raise ValueError(f"{name} must be between {minimum} and {maximum}")


def _validate_bounded_seconds(name: str, value: float, *, maximum: float) -> None:
    if (
        not isinstance(value, (int, float))
        or isinstance(value, bool)
        or not math.isfinite(value)
        or not 0 <= value <= maximum
    ):
        raise ValueError(f"{name} must be between 0 and {maximum:g}")


DisabledDocumentFetcher.component = _component_snapshot(
    component_id="disabled-document-fetcher",
    version="v1",
    config={"network_access": False},
)
