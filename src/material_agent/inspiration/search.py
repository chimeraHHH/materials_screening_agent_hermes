"""Bounded metadata search primitives for the inspiration capability.

The offline adapter returns raw response bytes; callers must persist those bytes
before parsing them into :class:`SearchHitV1` objects.  This module intentionally
has no PDF or full-document interface.
"""

from __future__ import annotations

import base64
import hashlib
import html
import json
import math
import os
import re
import socket
import threading
import time
from collections.abc import Callable, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, replace
from datetime import timezone
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Literal, Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urljoin, urlsplit, urlunsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener
from xml.etree import ElementTree

from material_agent.inspiration.models import (
    ArtifactPointerV1,
    ComponentSnapshotV1,
    SearchHitV1,
    SearchQueryV1,
    canonical_json_bytes,
    canonical_sha256,
    deterministic_id,
)

_TRANSIENT_HTTP_STATUSES = frozenset({408, 429, 500, 502, 503, 504})
_MAX_RETRIES = 5
_MAX_RETRY_DELAY_SECONDS = 60.0
_MAX_TOTAL_WAIT_SECONDS = 120.0
_CROSSREF_PUBLIC_MIN_INTERVAL_SECONDS = 1.0
_CROSSREF_POLITE_MIN_INTERVAL_SECONDS = 1.0 / 3.0
_ARXIV_MIN_INTERVAL_SECONDS = 3.0
_METADATA_REDIRECT_STATUSES = frozenset({301, 302, 303, 307, 308})
_MAX_METADATA_REDIRECTS = 5
_MAX_REDIRECT_LOCATION_LENGTH = 2_048
PUBLIC_SEARCH_PROVIDER_ENV = "MATERIAL_AGENT_INSPIRATION_SEARCH_PROVIDER"
PUBLIC_SEARCH_MAX_RESULTS_ENV = "MATERIAL_AGENT_INSPIRATION_SEARCH_MAX_RESULTS"
OPENALEX_API_KEY_ENV = "OPENALEX_API_KEY"
CROSSREF_CONTACT_EMAIL_ENV = "MATERIALS_CROSSREF_CONTACT_EMAIL"

_ATOM_NAMESPACE = "http://www.w3.org/2005/Atom"
_ARXIV_NAMESPACE = "http://arxiv.org/schemas/atom"
_ARXIV_VERSION_SUFFIX = re.compile(r"v[1-9][0-9]*$")


class _RejectRedirectHandler(HTTPRedirectHandler):
    """Keep redirects observable so every physical hop can be authorized first."""

    def redirect_request(  # type: ignore[no-untyped-def]
        self,
        req,
        fp,
        code,
        msg,
        headers,
        newurl,
    ):
        del req, fp, code, msg, headers, newurl
        return None


_NO_REDIRECT_OPENER = build_opener(_RejectRedirectHandler())


def urlopen(request: Request, *, timeout: float):  # type: ignore[no-untyped-def]
    """Open exactly one URL without urllib's implicit redirect handling.

    The module-level boundary is intentionally retained so tests can replace the
    physical network call.  Redirects surface as :class:`HTTPError` instances and
    are handled explicitly by :class:`UrlLibBoundedTransport`.
    """

    return _NO_REDIRECT_OPENER.open(request, timeout=timeout)  # noqa: S310


@dataclass(frozen=True, slots=True)
class PhysicalSearchHop:
    """One transport-observed HTTP request, including explicit redirects."""

    outcome: Literal["success", "redirect", "error"]
    error_code: str | None
    http_status: int | None
    response_bytes: int = 0

    def __post_init__(self) -> None:
        if self.outcome not in {"success", "redirect", "error"}:
            raise ValueError("physical search hop outcome is invalid")
        if self.outcome == "error" and not self.error_code:
            raise ValueError("failed physical search hops require an error code")
        if self.outcome != "error" and self.error_code is not None:
            raise ValueError("non-error physical search hops cannot have an error code")
        if self.http_status is not None and not 100 <= self.http_status <= 599:
            raise ValueError("physical search hop HTTP status is invalid")
        if type(self.response_bytes) is not int or not 0 <= self.response_bytes <= 10_000_001:
            raise ValueError("physical search hop response bytes are invalid")


@dataclass(frozen=True, slots=True)
class BoundedHttpResult:
    """Bounded response bytes plus the exact physical HTTP hop sequence."""

    payload: bytes
    physical_hops: tuple[PhysicalSearchHop, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.payload, bytes):
            raise TypeError("bounded HTTP payload must be bytes")
        if not self.physical_hops:
            raise ValueError("bounded HTTP result requires at least one physical hop")
        if self.physical_hops[-1].outcome != "success":
            raise ValueError("bounded HTTP result requires a terminal successful hop")


class SearchAdapterError(RuntimeError):
    """A bounded search adapter or response violated its frozen contract.

    ``http_status`` and ``retry_after`` are transport metadata, not instructions to
    retry by themselves.  The public adapter applies its own finite allowlist and
    bounded retry policy.  ``attempts`` is populated when an adapter exhausts or
    rejects a request so callers can persist the failed-attempt ledger as well.
    """

    def __init__(
        self,
        code: str,
        message: str,
        *,
        http_status: int | None = None,
        retry_after: str | None = None,
        response_bytes: int = 0,
        attempts: tuple[SearchAttemptRecord, ...] = (),
        physical_hops: tuple[PhysicalSearchHop, ...] = (),
    ) -> None:
        if type(response_bytes) is not int or not 0 <= response_bytes <= 10_000_001:
            raise ValueError("response_bytes must be between 0 and 10000001")
        self.code = code
        self.message = message
        self.http_status = http_status
        self.retry_after = retry_after
        self.response_bytes = response_bytes
        self.attempts = tuple(attempts)
        self.physical_hops = tuple(physical_hops)
        super().__init__(f"{code}: {message}")

    def with_attempts(
        self,
        attempts: tuple[SearchAttemptRecord, ...],
    ) -> SearchAdapterError:
        """Copy the error while attaching a stable, timestamp-free attempt ledger."""

        return SearchAdapterError(
            self.code,
            self.message,
            http_status=self.http_status,
            retry_after=self.retry_after,
            response_bytes=self.response_bytes,
            attempts=attempts,
            physical_hops=self.physical_hops,
        )


@dataclass(frozen=True)
class SearchAttemptRecord:
    """One deterministic provider attempt suitable for an audit Artifact."""

    query_id: str
    attempt_number: int
    outcome: Literal["success", "redirect", "error"]
    error_code: str | None
    http_status: int | None
    retry_delay_seconds: float
    response_bytes: int
    pacing_delay_seconds: float = 0.0

    def __post_init__(self) -> None:
        if not self.query_id:
            raise ValueError("query_id must be non-empty")
        if self.attempt_number < 1:
            raise ValueError("attempt_number must be positive")
        if self.outcome not in {"success", "redirect", "error"}:
            raise ValueError("outcome must be success, redirect, or error")
        if self.outcome != "error" and self.error_code is not None:
            raise ValueError("non-error attempts cannot have an error_code")
        if self.outcome == "error" and not self.error_code:
            raise ValueError("failed attempts require an error_code")
        if self.http_status is not None and not 100 <= self.http_status <= 599:
            raise ValueError("http_status must be between 100 and 599")
        if (
            not isinstance(self.retry_delay_seconds, (int, float))
            or isinstance(self.retry_delay_seconds, bool)
            or not 0 <= self.retry_delay_seconds <= 120
        ):
            raise ValueError("retry_delay_seconds must be between 0 and 120")
        if (
            not isinstance(self.pacing_delay_seconds, (int, float))
            or isinstance(self.pacing_delay_seconds, bool)
            or not 0 <= self.pacing_delay_seconds <= 120
        ):
            raise ValueError("pacing_delay_seconds must be between 0 and 120")
        if self.response_bytes < 0 or self.response_bytes > 10_000_001:
            raise ValueError("response_bytes must be between 0 and 10000001")

    def to_dict(self) -> dict[str, object]:
        """Return a stable JSON-serializable record with no absolute timestamp."""

        return {
            "query_id": self.query_id,
            "attempt_number": self.attempt_number,
            "outcome": self.outcome,
            "error_code": self.error_code,
            "http_status": self.http_status,
            "retry_delay_seconds": self.retry_delay_seconds,
            "pacing_delay_seconds": self.pacing_delay_seconds,
            "response_bytes": self.response_bytes,
        }


@dataclass(frozen=True)
class RawSearchPage:
    """One finite response that must be written as an Artifact before parsing."""

    provider: str
    query_id: str
    payload: bytes
    media_type: str = "application/json"
    attempts: tuple[SearchAttemptRecord, ...] = ()


@dataclass(frozen=True)
class ParsedSearchPage:
    hits: tuple[SearchHitV1, ...]
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True)
class DocumentHitGroup:
    """Run-local duplicate group without discarding any raw hit lineage."""

    document_id: str
    representative_hit_id: str
    member_hit_ids: tuple[str, ...]


class SearchAdapter(Protocol):
    component: ComponentSnapshotV1
    network_access: bool

    def search(
        self,
        query: SearchQueryV1,
        *,
        max_response_bytes: int,
        remaining_walltime_seconds: float | None = None,
        max_physical_requests: int | None = None,
    ) -> RawSearchPage:
        """Return one bounded raw metadata response for ``query``."""


class BoundedHttpTransport(Protocol):
    """Small injectable boundary used by public metadata adapters."""

    def get(
        self,
        url: str,
        *,
        headers: Mapping[str, str],
        timeout_seconds: float,
        max_response_bytes: int,
        deadline_monotonic: float | None = None,
        max_physical_requests: int | None = None,
    ) -> bytes | BoundedHttpResult:
        """Return a response only when it fits the declared byte budget."""


def _validated_metadata_url(
    url: str,
    *,
    allowed_hosts: frozenset[str],
    is_redirect: bool,
) -> str:
    """Return a fragment-free URL only after validating its network authority."""

    error_code = (
        "UNTRUSTED_METADATA_REDIRECT"
        if is_redirect
        else "UNTRUSTED_METADATA_ENDPOINT"
    )
    error_message = (
        "metadata redirect must use an allowlisted HTTPS endpoint on port 443"
        if is_redirect
        else "metadata requests must use an allowlisted HTTPS endpoint on port 443"
    )
    if not isinstance(url, str) or not url or any(
        ord(character) <= 0x20 or ord(character) == 0x7F for character in url
    ):
        raise SearchAdapterError(error_code, error_message)
    try:
        parsed = urlsplit(url)
        port = parsed.port
    except ValueError as error:
        raise SearchAdapterError(error_code, error_message) from error
    hostname = parsed.hostname.casefold() if parsed.hostname is not None else None
    if (
        parsed.scheme.casefold() != "https"
        or hostname not in allowed_hosts
        or port not in {None, 443}
        or parsed.username is not None
        or parsed.password is not None
    ):
        raise SearchAdapterError(error_code, error_message)
    return urlunsplit(
        (
            "https",
            parsed.netloc,
            parsed.path,
            parsed.query,
            "",
        )
    )


def _metadata_url_key(url: str) -> tuple[str, str, int, str, str]:
    """Canonical comparison key for redirect-loop and hidden-redirect checks."""

    parsed = urlsplit(url)
    assert parsed.hostname is not None
    return (
        parsed.scheme.casefold(),
        parsed.hostname.casefold(),
        parsed.port or 443,
        parsed.path or "/",
        parsed.query,
    )


def _redirect_target(current_url: str, headers: object) -> str:
    """Resolve one bounded Location header without authorizing or requesting it."""

    location = headers.get("Location") if hasattr(headers, "get") else None
    if not isinstance(location, str) or not location.strip():
        raise SearchAdapterError(
            "MISSING_REDIRECT_LOCATION",
            "metadata redirect did not include a Location header",
        )
    if len(location) > _MAX_REDIRECT_LOCATION_LENGTH:
        raise SearchAdapterError(
            "INVALID_REDIRECT_LOCATION",
            "metadata redirect Location exceeded its bounded length",
        )
    return urljoin(current_url, location)


class UrlLibBoundedTransport:
    """HTTPS-only reader that checks redirects, media type, and byte limits."""

    allowed_hosts = frozenset({"api.crossref.org"})

    def __init__(
        self,
        *,
        monotonic_clock: Callable[[], float] = time.monotonic,
        allowed_hosts: frozenset[str] | None = None,
        allowed_media_types: frozenset[str] | None = None,
    ) -> None:
        if not callable(monotonic_clock):
            raise ValueError("monotonic_clock must be callable")
        self.monotonic_clock = monotonic_clock
        if allowed_hosts is not None:
            if not allowed_hosts or any(
                not isinstance(host, str) or not host.strip()
                for host in allowed_hosts
            ):
                raise ValueError("allowed_hosts must contain bounded host names")
            self.allowed_hosts = frozenset(host.casefold() for host in allowed_hosts)
        self.allowed_media_types = frozenset(
            {"application/json", "application/vnd.api+json"}
            if allowed_media_types is None
            else (item.casefold() for item in allowed_media_types)
        )
        if not self.allowed_media_types or any(
            not item.strip() for item in self.allowed_media_types
        ):
            raise ValueError("allowed_media_types must contain bounded media types")

    def get(
        self,
        url: str,
        *,
        headers: Mapping[str, str],
        timeout_seconds: float,
        max_response_bytes: int,
        deadline_monotonic: float | None = None,
        max_physical_requests: int | None = None,
    ) -> BoundedHttpResult:
        if (
            not isinstance(timeout_seconds, (int, float))
            or isinstance(timeout_seconds, bool)
            or not math.isfinite(timeout_seconds)
            or not 0 < timeout_seconds <= 120
        ):
            raise SearchAdapterError(
                "INVALID_TIMEOUT",
                "timeout_seconds must be greater than zero and at most 120",
            )
        if not 1 <= max_response_bytes <= 10_000_000:
            raise SearchAdapterError(
                "INVALID_RESPONSE_BUDGET",
                "max_response_bytes must be between 1 and 10000000",
            )
        if deadline_monotonic is not None and (
            not isinstance(deadline_monotonic, (int, float))
            or isinstance(deadline_monotonic, bool)
            or not math.isfinite(deadline_monotonic)
        ):
            raise SearchAdapterError(
                "INVALID_DEADLINE",
                "deadline_monotonic must be a finite number of seconds",
            )
        if max_physical_requests is not None and (
            type(max_physical_requests) is not int
            or not 1 <= max_physical_requests <= 64
        ):
            raise SearchAdapterError(
                "INVALID_PHYSICAL_REQUEST_BUDGET",
                "max_physical_requests must be between 1 and 64",
            )
        current_url = _validated_metadata_url(
            url,
            allowed_hosts=self.allowed_hosts,
            is_redirect=False,
        )
        seen_urls = {_metadata_url_key(current_url)}
        redirects_followed = 0
        physical_hops: list[PhysicalSearchHop] = []

        while True:
            if (
                max_physical_requests is not None
                and len(physical_hops) >= max_physical_requests
            ):
                raise SearchAdapterError(
                    "SEARCH_REQUEST_BUDGET_EXCEEDED",
                    "metadata redirects exhausted the physical request budget",
                    physical_hops=tuple(physical_hops),
                )
            # Validate again at the physical-request boundary.  In particular, no
            # redirected URL reaches urlopen before its scheme, host, and port pass.
            current_url = _validated_metadata_url(
                current_url,
                allowed_hosts=self.allowed_hosts,
                is_redirect=redirects_followed > 0,
            )
            request = Request(current_url, headers=dict(headers), method="GET")
            request_timeout = float(timeout_seconds)
            if deadline_monotonic is not None:
                remaining = deadline_monotonic - _read_finite_clock(
                    self.monotonic_clock,
                    "transport_monotonic_clock",
                )
                if remaining <= 0:
                    raise SearchAdapterError(
                        "WALLTIME_BUDGET_EXCEEDED",
                        "metadata request deadline expired before the next hop",
                        physical_hops=tuple(physical_hops),
                    )
                request_timeout = min(request_timeout, remaining)
            try:
                with urlopen(request, timeout=request_timeout) as response:  # noqa: S310
                    try:
                        resolved_url = _validated_metadata_url(
                            response.geturl(),
                            allowed_hosts=self.allowed_hosts,
                            is_redirect=True,
                        )
                        if _metadata_url_key(resolved_url) != _metadata_url_key(
                            current_url
                        ):
                            raise SearchAdapterError(
                                "UNEXPECTED_METADATA_REDIRECT",
                                "metadata transport followed an unvalidated redirect",
                            )
                        media_type = response.headers.get_content_type().casefold()
                        if media_type not in self.allowed_media_types:
                            raise SearchAdapterError(
                                "UNEXPECTED_MEDIA_TYPE",
                                f"metadata endpoint returned {media_type!r}",
                            )
                        content_length = response.headers.get("Content-Length")
                        if content_length is not None:
                            normalized_length = content_length.strip()
                            if (
                                not normalized_length.isascii()
                                or not normalized_length.isdigit()
                            ):
                                raise SearchAdapterError(
                                    "INVALID_CONTENT_LENGTH",
                                    "metadata endpoint returned an invalid Content-Length",
                                )
                            significant_length = normalized_length.lstrip("0") or "0"
                            budget_text = str(max_response_bytes)
                            if (
                                len(significant_length) > len(budget_text)
                                or (
                                    len(significant_length) == len(budget_text)
                                    and significant_length > budget_text
                                )
                            ):
                                raise SearchAdapterError(
                                    "RESPONSE_BUDGET_EXCEEDED",
                                    "metadata response exceeds its declared byte budget",
                                )
                        payload = response.read(max_response_bytes + 1)
                        if len(payload) > max_response_bytes:
                            raise SearchAdapterError(
                                "RESPONSE_BUDGET_EXCEEDED",
                                "metadata response exceeded its byte budget while streaming",
                                response_bytes=len(payload),
                            )
                        if deadline_monotonic is not None and (
                            _read_finite_clock(
                                self.monotonic_clock,
                                "transport_monotonic_clock",
                            )
                            >= deadline_monotonic
                        ):
                            raise SearchAdapterError(
                                "WALLTIME_BUDGET_EXCEEDED",
                                "metadata response completed after the run deadline",
                                response_bytes=len(payload),
                            )
                    except SearchAdapterError as error:
                        failed_hops = (
                            *physical_hops,
                            PhysicalSearchHop(
                                outcome="error",
                                error_code=error.code,
                                http_status=error.http_status or 200,
                                response_bytes=error.response_bytes,
                            ),
                        )
                        raise SearchAdapterError(
                            error.code,
                            error.message,
                            http_status=error.http_status,
                            retry_after=error.retry_after,
                            response_bytes=error.response_bytes,
                            physical_hops=failed_hops,
                        ) from error
                physical_hops.append(
                    PhysicalSearchHop(
                        outcome="success",
                        error_code=None,
                        http_status=200,
                        response_bytes=len(payload),
                    )
                )
                return BoundedHttpResult(
                    payload=payload,
                    physical_hops=tuple(physical_hops),
                )
            except HTTPError as error:
                status = int(error.code)
                if status in _METADATA_REDIRECT_STATUSES:
                    try:
                        try:
                            target_url = _redirect_target(current_url, error.headers)
                            target_url = _validated_metadata_url(
                                target_url,
                                allowed_hosts=self.allowed_hosts,
                                is_redirect=True,
                            )
                            target_key = _metadata_url_key(target_url)
                            if target_key in seen_urls:
                                raise SearchAdapterError(
                                    "METADATA_REDIRECT_LOOP",
                                    "metadata endpoint returned a redirect loop",
                                    http_status=status,
                                )
                            if redirects_followed >= _MAX_METADATA_REDIRECTS:
                                raise SearchAdapterError(
                                    "TOO_MANY_METADATA_REDIRECTS",
                                    "metadata endpoint exceeded the redirect limit",
                                    http_status=status,
                                )
                        except SearchAdapterError as redirect_error:
                            failed_hops = (
                                *physical_hops,
                                PhysicalSearchHop(
                                    outcome="error",
                                    error_code=redirect_error.code,
                                    http_status=status,
                                ),
                            )
                            raise SearchAdapterError(
                                redirect_error.code,
                                redirect_error.message,
                                http_status=status,
                                physical_hops=failed_hops,
                            ) from redirect_error
                    finally:
                        error.close()
                    physical_hops.append(
                        PhysicalSearchHop(
                            outcome="redirect",
                            error_code=None,
                            http_status=status,
                        )
                    )
                    seen_urls.add(target_key)
                    redirects_followed += 1
                    current_url = target_url
                    continue
                retry_after = None
                if error.headers is not None:
                    candidate = error.headers.get("Retry-After")
                    if isinstance(candidate, str) and len(candidate) <= 128:
                        retry_after = candidate
                code = (
                    "TRANSIENT_HTTP_ERROR"
                    if status in _TRANSIENT_HTTP_STATUSES
                    else "HTTP_ERROR"
                )
                failed_hops = (
                    *physical_hops,
                    PhysicalSearchHop(
                        outcome="error",
                        error_code=code,
                        http_status=status,
                    ),
                )
                raise SearchAdapterError(
                    code,
                    f"metadata endpoint returned HTTP {status}",
                    http_status=status,
                    retry_after=retry_after,
                    physical_hops=failed_hops,
                ) from error
            except (TimeoutError, socket.timeout, URLError, OSError) as error:
                failed_hops = (
                    *physical_hops,
                    PhysicalSearchHop(
                        outcome="error",
                        error_code="NETWORK_ERROR",
                        http_status=None,
                    ),
                )
                raise SearchAdapterError(
                    "NETWORK_ERROR",
                    "metadata endpoint could not be read within the bounded request",
                    physical_hops=failed_hops,
                ) from error


def _validate_bounded_seconds(name: str, value: float, *, maximum: float) -> None:
    if (
        not isinstance(value, (int, float))
        or isinstance(value, bool)
        or not math.isfinite(value)
        or not 0 <= value <= maximum
    ):
        raise ValueError(f"{name} must be between 0 and {maximum:g}")


def _read_finite_clock(clock: Callable[[], float], name: str) -> float:
    value = clock()
    if (
        not isinstance(value, (int, float))
        or isinstance(value, bool)
        or not math.isfinite(value)
    ):
        raise SearchAdapterError(
            "INVALID_CLOCK",
            f"{name} must return a finite number of seconds",
        )
    return float(value)


def _parse_retry_after_seconds(
    value: str | None,
    *,
    wall_clock: Callable[[], float],
) -> float | None:
    """Parse RFC Retry-After delta-seconds or HTTP-date without sleeping."""

    if value is None:
        return None
    candidate = value.strip()
    if not candidate or len(candidate) > 128:
        return None
    if candidate.isascii() and candidate.isdigit():
        parsed_delta = float(candidate)
        return parsed_delta if math.isfinite(parsed_delta) else None
    try:
        parsed_date = parsedate_to_datetime(candidate)
        if parsed_date is None:
            return None
        if parsed_date.tzinfo is None:
            parsed_date = parsed_date.replace(tzinfo=timezone.utc)
        delta = parsed_date.timestamp() - _read_finite_clock(wall_clock, "wall_clock")
    except (OverflowError, TypeError, ValueError):
        return None
    return max(0.0, delta) if math.isfinite(delta) else None


def _is_transient_search_error(error: SearchAdapterError) -> bool:
    return error.code == "NETWORK_ERROR" or (
        error.http_status in _TRANSIENT_HTTP_STATUSES
    )


def _remaining_before_deadline(
    deadline_monotonic: float | None,
    *,
    monotonic_clock: Callable[[], float],
) -> float | None:
    if deadline_monotonic is None:
        return None
    remaining = deadline_monotonic - _read_finite_clock(
        monotonic_clock,
        "monotonic_clock",
    )
    if remaining <= 0:
        raise SearchAdapterError(
            "WALLTIME_BUDGET_EXCEEDED",
            "the inspiration run deadline has expired",
        )
    return remaining


def _attempt_records_from_physical_hops(
    *,
    query_id: str,
    first_attempt_number: int,
    physical_hops: tuple[PhysicalSearchHop, ...],
    pacing_delay_seconds: float,
) -> tuple[SearchAttemptRecord, ...]:
    return tuple(
        SearchAttemptRecord(
            query_id=query_id,
            attempt_number=first_attempt_number + offset,
            outcome=hop.outcome,
            error_code=hop.error_code,
            http_status=hop.http_status,
            retry_delay_seconds=0.0,
            pacing_delay_seconds=(pacing_delay_seconds if offset == 0 else 0.0),
            response_bytes=hop.response_bytes,
        )
        for offset, hop in enumerate(physical_hops)
    )


def _validate_publication_year_range(
    publication_year_from: int | None,
    publication_year_to: int | None,
) -> None:
    for label, value in (
        ("publication_year_from", publication_year_from),
        ("publication_year_to", publication_year_to),
    ):
        if value is not None and (
            type(value) is not int or not 1600 <= value <= 2200
        ):
            raise ValueError(f"{label} must be an integer between 1600 and 2200")
    if (
        publication_year_from is not None
        and publication_year_to is not None
        and publication_year_from > publication_year_to
    ):
        raise ValueError("publication_year_from exceeds publication_year_to")


def _crossref_component_snapshot(
    *,
    max_results: int,
    timeout_seconds: int,
    max_retries: int,
    retry_backoff_seconds: float,
    max_retry_delay_seconds: float,
    max_total_wait_seconds: float,
    min_request_interval_seconds: float,
    polite_pool: bool,
    publication_year_from: int | None,
    publication_year_to: int | None,
) -> ComponentSnapshotV1:
    """Bind behavior-affecting public-search configuration without the email."""

    config = {
        "max_results": max_results,
        "timeout_seconds": timeout_seconds,
        "retry": {
            "max_retries": max_retries,
            "retry_backoff_seconds": retry_backoff_seconds,
            "max_retry_delay_seconds": max_retry_delay_seconds,
            "max_total_wait_seconds": max_total_wait_seconds,
        },
        "rate_limit": {
            "min_request_interval_seconds": min_request_interval_seconds,
            "polite_pool": polite_pool,
        },
        "publication_year": {
            "from": publication_year_from,
            "to": publication_year_to,
        },
    }
    fingerprint = json.dumps(
        {
            "source_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            "config": config,
        },
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return ComponentSnapshotV1(
        component_id="crossref-public-adapter",
        version="v1-retry-v1",
        implementation_sha256=hashlib.sha256(fingerprint).hexdigest(),
    )


class CrossrefPublicAdapter:
    """Crossref v1 metadata search with finite retries and request pacing.

    ``max_retries`` counts retries *after* the initial HTTP attempt.  Therefore
    one logical search makes at most ``max_retries + 1`` HTTP requests.  All
    pacing and retry sleeps within one logical search share
    ``max_total_wait_seconds``; the constructor also places hard upper bounds on
    attempts and waits.
    """

    component = ComponentSnapshotV1(
        component_id="crossref-public-adapter",
        version="v1-retry-v1",
        implementation_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
    )
    network_access = True

    def __init__(
        self,
        *,
        max_results: int = 5,
        timeout_seconds: int = 20,
        contact_email: str | None = None,
        transport: BoundedHttpTransport | None = None,
        max_retries: int = 2,
        retry_backoff_seconds: float = 1.0,
        max_retry_delay_seconds: float = 30.0,
        max_total_wait_seconds: float = 60.0,
        min_request_interval_seconds: float | None = None,
        sleeper: Callable[[float], None] = time.sleep,
        wall_clock: Callable[[], float] = time.time,
        monotonic_clock: Callable[[], float] = time.monotonic,
        publication_year_from: int | None = None,
        publication_year_to: int | None = None,
    ) -> None:
        if not 1 <= max_results <= 20:
            raise ValueError("max_results must be between 1 and 20")
        if not 1 <= timeout_seconds <= 120:
            raise ValueError("timeout_seconds must be between 1 and 120")
        if contact_email is not None:
            if (
                len(contact_email) > 254
                or "@" not in contact_email
                or any(character.isspace() for character in contact_email)
            ):
                raise ValueError("contact_email must be a bounded email address")
        if type(max_retries) is not int or not 0 <= max_retries <= _MAX_RETRIES:
            raise ValueError(f"max_retries must be between 0 and {_MAX_RETRIES}")
        _validate_bounded_seconds(
            "retry_backoff_seconds",
            retry_backoff_seconds,
            maximum=_MAX_RETRY_DELAY_SECONDS,
        )
        _validate_bounded_seconds(
            "max_retry_delay_seconds",
            max_retry_delay_seconds,
            maximum=_MAX_RETRY_DELAY_SECONDS,
        )
        _validate_bounded_seconds(
            "max_total_wait_seconds",
            max_total_wait_seconds,
            maximum=_MAX_TOTAL_WAIT_SECONDS,
        )
        required_interval = (
            _CROSSREF_POLITE_MIN_INTERVAL_SECONDS
            if contact_email is not None
            else _CROSSREF_PUBLIC_MIN_INTERVAL_SECONDS
        )
        if min_request_interval_seconds is None:
            min_request_interval_seconds = required_interval
        _validate_bounded_seconds(
            "min_request_interval_seconds",
            min_request_interval_seconds,
            maximum=_MAX_RETRY_DELAY_SECONDS,
        )
        if min_request_interval_seconds < required_interval:
            pool = "polite" if contact_email is not None else "public"
            raise ValueError(
                "min_request_interval_seconds must be at least "
                f"{required_interval:g} for the Crossref {pool} pool"
            )
        if not callable(sleeper):
            raise ValueError("sleeper must be callable")
        if not callable(wall_clock):
            raise ValueError("wall_clock must be callable")
        if not callable(monotonic_clock):
            raise ValueError("monotonic_clock must be callable")
        _validate_publication_year_range(
            publication_year_from,
            publication_year_to,
        )
        self.max_results = max_results
        self.timeout_seconds = timeout_seconds
        self.contact_email = contact_email
        self.transport = transport or UrlLibBoundedTransport(
            monotonic_clock=monotonic_clock
        )
        self.max_retries = max_retries
        self.retry_backoff_seconds = float(retry_backoff_seconds)
        self.max_retry_delay_seconds = float(max_retry_delay_seconds)
        self.max_total_wait_seconds = float(max_total_wait_seconds)
        self.min_request_interval_seconds = float(min_request_interval_seconds)
        self.sleeper = sleeper
        self.wall_clock = wall_clock
        self.monotonic_clock = monotonic_clock
        self.publication_year_from = publication_year_from
        self.publication_year_to = publication_year_to
        self._pacing_lock = threading.Lock()
        self._last_request_started: float | None = None
        self.component = _crossref_component_snapshot(
            max_results=max_results,
            timeout_seconds=timeout_seconds,
            max_retries=max_retries,
            retry_backoff_seconds=self.retry_backoff_seconds,
            max_retry_delay_seconds=self.max_retry_delay_seconds,
            max_total_wait_seconds=self.max_total_wait_seconds,
            min_request_interval_seconds=self.min_request_interval_seconds,
            polite_pool=contact_email is not None,
            publication_year_from=publication_year_from,
            publication_year_to=publication_year_to,
        )

    def search(
        self,
        query: SearchQueryV1,
        *,
        max_response_bytes: int,
        remaining_walltime_seconds: float | None = None,
        max_physical_requests: int | None = None,
    ) -> RawSearchPage:
        if not isinstance(query, SearchQueryV1):
            raise SearchAdapterError(
                "INVALID_QUERY",
                "query must be a SearchQueryV1 record",
            )
        if not 1 <= max_response_bytes <= 10_000_000:
            raise SearchAdapterError(
                "INVALID_RESPONSE_BUDGET",
                "max_response_bytes must be between 1 and 10000000",
            )
        if remaining_walltime_seconds is not None:
            if (
                not isinstance(remaining_walltime_seconds, (int, float))
                or isinstance(remaining_walltime_seconds, bool)
                or not math.isfinite(remaining_walltime_seconds)
                or remaining_walltime_seconds <= 0
            ):
                raise SearchAdapterError(
                    "WALLTIME_BUDGET_EXCEEDED",
                    "remaining_walltime_seconds must be finite and positive",
                )
            deadline_monotonic = _read_finite_clock(
                self.monotonic_clock,
                "monotonic_clock",
            ) + float(remaining_walltime_seconds)
        else:
            deadline_monotonic = None
        if max_physical_requests is not None and (
            type(max_physical_requests) is not int
            or not 0 <= max_physical_requests <= 64
        ):
            raise SearchAdapterError(
                "INVALID_PHYSICAL_REQUEST_BUDGET",
                "max_physical_requests must be between 0 and 64",
            )
        if max_physical_requests == 0:
            raise SearchAdapterError(
                "SEARCH_REQUEST_BUDGET_EXCEEDED",
                "no physical metadata request remains",
            )
        filters = ["has-abstract:true"]
        if self.publication_year_from is not None:
            filters.append(f"from-pub-date:{self.publication_year_from}-01-01")
        if self.publication_year_to is not None:
            filters.append(f"until-pub-date:{self.publication_year_to}-12-31")
        parameters = {
            "filter": ",".join(filters),
            "query.bibliographic": query.text,
            "rows": str(self.max_results),
            "select": "DOI,title,author,published,URL,abstract,subject",
        }
        if self.contact_email is not None:
            parameters["mailto"] = self.contact_email
        url = f"https://api.crossref.org/v1/works?{urlencode(parameters)}"
        headers = {
            "Accept": "application/json",
            "Accept-Encoding": "identity",
            "User-Agent": "materials-screening-agent/0.1 (metadata-only)",
        }
        attempts: list[SearchAttemptRecord] = []
        total_wait_seconds = 0.0
        for logical_attempt_number in range(1, self.max_retries + 2):
            try:
                pacing_delay = self._pace_request(
                    remaining_wait_seconds=(
                        self.max_total_wait_seconds - total_wait_seconds
                    ),
                    deadline_monotonic=deadline_monotonic,
                )
            except SearchAdapterError as error:
                raise error.with_attempts(tuple(attempts)) from error
            total_wait_seconds += pacing_delay
            try:
                remaining_for_request = _remaining_before_deadline(
                    deadline_monotonic,
                    monotonic_clock=self.monotonic_clock,
                )
                transport_result = self.transport.get(
                    url,
                    headers=headers,
                    timeout_seconds=min(
                        float(self.timeout_seconds),
                        (
                            remaining_for_request
                            if remaining_for_request is not None
                            else float(self.timeout_seconds)
                        ),
                    ),
                    max_response_bytes=max_response_bytes,
                    deadline_monotonic=deadline_monotonic,
                    max_physical_requests=(
                        None
                        if max_physical_requests is None
                        else max_physical_requests - len(attempts)
                    ),
                )
                if isinstance(transport_result, bytes):
                    payload = transport_result
                    physical_hops = (
                        PhysicalSearchHop(
                            outcome="success",
                            error_code=None,
                            http_status=200,
                            response_bytes=len(payload),
                        ),
                    )
                elif isinstance(transport_result, BoundedHttpResult):
                    payload = transport_result.payload
                    physical_hops = transport_result.physical_hops
                else:
                    raise SearchAdapterError(
                        "INVALID_NETWORK_PAYLOAD",
                        "metadata transport must return bytes or BoundedHttpResult",
                    )
                if len(payload) > max_response_bytes:
                    raise SearchAdapterError(
                        "RESPONSE_BUDGET_EXCEEDED",
                        "metadata transport returned more than the declared byte budget",
                        response_bytes=len(payload),
                    )
                if (
                    max_physical_requests is not None
                    and len(attempts) + len(physical_hops) > max_physical_requests
                ):
                    raise SearchAdapterError(
                        "SEARCH_REQUEST_BUDGET_EXCEEDED",
                        "metadata transport exceeded the physical request budget",
                        physical_hops=physical_hops,
                    )
                try:
                    _remaining_before_deadline(
                        deadline_monotonic,
                        monotonic_clock=self.monotonic_clock,
                    )
                except SearchAdapterError as deadline_error:
                    failed_hops = (
                        *physical_hops[:-1],
                        PhysicalSearchHop(
                            outcome="error",
                            error_code=deadline_error.code,
                            http_status=physical_hops[-1].http_status,
                            response_bytes=len(payload),
                        ),
                    )
                    raise SearchAdapterError(
                        deadline_error.code,
                        deadline_error.message,
                        response_bytes=len(payload),
                        physical_hops=failed_hops,
                    ) from deadline_error
            except SearchAdapterError as error:
                physical_hops = error.physical_hops
                if not physical_hops and error.code not in {
                    "WALLTIME_BUDGET_EXCEEDED",
                    "SEARCH_REQUEST_BUDGET_EXCEEDED",
                }:
                    physical_hops = (
                        PhysicalSearchHop(
                            outcome="error",
                            error_code=error.code,
                            http_status=error.http_status,
                            response_bytes=error.response_bytes,
                        ),
                    )
                new_records = _attempt_records_from_physical_hops(
                    query_id=query.query_id,
                    first_attempt_number=len(attempts) + 1,
                    physical_hops=physical_hops,
                    pacing_delay_seconds=pacing_delay,
                )
                can_retry = (
                    logical_attempt_number <= self.max_retries
                    and _is_transient_search_error(error)
                    and (
                        max_physical_requests is None
                        or len(attempts) + len(new_records) < max_physical_requests
                    )
                )
                retry_delay = 0.0
                deadline_prevents_retry = False
                if can_retry:
                    retry_delay = self._retry_delay_seconds(
                        error,
                        retry_number=logical_attempt_number,
                    )
                    remaining_wait = self.max_total_wait_seconds - total_wait_seconds
                    if retry_delay > remaining_wait:
                        can_retry = False
                        retry_delay = 0.0
                    else:
                        try:
                            deadline_remaining = _remaining_before_deadline(
                                deadline_monotonic,
                                monotonic_clock=self.monotonic_clock,
                            )
                        except SearchAdapterError:
                            deadline_remaining = 0.0
                        if (
                            deadline_remaining is not None
                            and retry_delay >= deadline_remaining
                        ):
                            can_retry = False
                            deadline_prevents_retry = True
                            retry_delay = 0.0
                if new_records and retry_delay:
                    new_records = (
                        *new_records[:-1],
                        replace(
                            new_records[-1],
                            retry_delay_seconds=retry_delay,
                        ),
                    )
                attempts.extend(new_records)
                if deadline_prevents_retry:
                    raise SearchAdapterError(
                        "WALLTIME_BUDGET_EXCEEDED",
                        "the remaining run walltime cannot cover the retry delay",
                        attempts=tuple(attempts),
                    ) from error
                if not can_retry:
                    raise error.with_attempts(tuple(attempts)) from error
                if retry_delay:
                    self.sleeper(retry_delay)
                    total_wait_seconds += retry_delay
                    try:
                        _remaining_before_deadline(
                            deadline_monotonic,
                            monotonic_clock=self.monotonic_clock,
                        )
                    except SearchAdapterError as deadline_error:
                        raise deadline_error.with_attempts(tuple(attempts)) from error
                continue

            attempts.extend(
                _attempt_records_from_physical_hops(
                    query_id=query.query_id,
                    first_attempt_number=len(attempts) + 1,
                    physical_hops=physical_hops,
                    pacing_delay_seconds=pacing_delay,
                )
            )
            return RawSearchPage(
                provider="crossref",
                query_id=query.query_id,
                payload=payload,
                attempts=tuple(attempts),
            )
        raise AssertionError("finite Crossref retry loop did not return or raise")

    def _pace_request(
        self,
        *,
        remaining_wait_seconds: float,
        deadline_monotonic: float | None,
    ) -> float:
        """Wait until the next provider request is within the configured rate."""

        with self._pacing_lock:
            now = _read_finite_clock(self.monotonic_clock, "monotonic_clock")
            if self._last_request_started is None:
                delay = 0.0
            else:
                elapsed = now - self._last_request_started
                if elapsed < 0:
                    raise SearchAdapterError(
                        "INVALID_MONOTONIC_CLOCK",
                        "monotonic_clock moved backwards",
                    )
                delay = max(0.0, self.min_request_interval_seconds - elapsed)
            if delay > remaining_wait_seconds:
                raise SearchAdapterError(
                    "WAIT_BUDGET_EXCEEDED",
                    "Crossref request pacing exceeds the remaining wait budget",
                )
            deadline_remaining = _remaining_before_deadline(
                deadline_monotonic,
                monotonic_clock=self.monotonic_clock,
            )
            if deadline_remaining is not None and delay >= deadline_remaining:
                raise SearchAdapterError(
                    "WALLTIME_BUDGET_EXCEEDED",
                    "Crossref pacing cannot complete before the run deadline",
                )
            if delay:
                self.sleeper(delay)
            self._last_request_started = _read_finite_clock(
                self.monotonic_clock,
                "monotonic_clock",
            )
            return delay

    def _retry_delay_seconds(
        self,
        error: SearchAdapterError,
        *,
        retry_number: int,
    ) -> float:
        retry_after = _parse_retry_after_seconds(
            error.retry_after,
            wall_clock=self.wall_clock,
        )
        if retry_after is None:
            retry_after = self.retry_backoff_seconds * (2 ** (retry_number - 1))
        return min(retry_after, self.max_retry_delay_seconds)


class ArxivPublicAdapter:
    """Official arXiv Query API adapter for bounded Atom metadata.

    The adapter performs one request per logical query and enforces arXiv's
    documented three-second spacing between sequential requests.  It retrieves
    only the Atom feed; PDF links are metadata and are never followed here.
    """

    network_access = True

    def __init__(
        self,
        *,
        max_results: int = 5,
        timeout_seconds: int = 20,
        publication_year_from: int | None = None,
        publication_year_to: int | None = None,
        min_request_interval_seconds: float = _ARXIV_MIN_INTERVAL_SECONDS,
        transport: BoundedHttpTransport | None = None,
        sleeper: Callable[[float], None] = time.sleep,
        monotonic_clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if not 1 <= max_results <= 20:
            raise ValueError("max_results must be between 1 and 20")
        if not 1 <= timeout_seconds <= 120:
            raise ValueError("timeout_seconds must be between 1 and 120")
        _validate_publication_year_range(
            publication_year_from,
            publication_year_to,
        )
        _validate_bounded_seconds(
            "min_request_interval_seconds",
            min_request_interval_seconds,
            maximum=_MAX_TOTAL_WAIT_SECONDS,
        )
        if min_request_interval_seconds < _ARXIV_MIN_INTERVAL_SECONDS:
            raise ValueError(
                "min_request_interval_seconds must be at least 3 for arXiv"
            )
        if not callable(sleeper):
            raise ValueError("sleeper must be callable")
        if not callable(monotonic_clock):
            raise ValueError("monotonic_clock must be callable")
        self.max_results = max_results
        self.timeout_seconds = timeout_seconds
        self.publication_year_from = publication_year_from
        self.publication_year_to = publication_year_to
        self.min_request_interval_seconds = float(min_request_interval_seconds)
        self.transport = transport or UrlLibBoundedTransport(
            monotonic_clock=monotonic_clock,
            allowed_hosts=frozenset({"export.arxiv.org"}),
            allowed_media_types=frozenset({"application/atom+xml"}),
        )
        self.sleeper = sleeper
        self.monotonic_clock = monotonic_clock
        self._pacing_lock = threading.Lock()
        self._last_request_started: float | None = None
        fingerprint = canonical_json_bytes(
            {
                "max_results": max_results,
                "min_request_interval_seconds": self.min_request_interval_seconds,
                "publication_year_from": publication_year_from,
                "publication_year_to": publication_year_to,
                "source_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                "timeout_seconds": timeout_seconds,
            }
        )
        self.component = ComponentSnapshotV1(
            component_id="arxiv-public-adapter",
            version="query-api-atom-v1",
            implementation_sha256=hashlib.sha256(fingerprint).hexdigest(),
        )

    def search(
        self,
        query: SearchQueryV1,
        *,
        max_response_bytes: int,
        remaining_walltime_seconds: float | None = None,
        max_physical_requests: int | None = None,
    ) -> RawSearchPage:
        if not isinstance(query, SearchQueryV1):
            raise SearchAdapterError("INVALID_QUERY", "query must be SearchQueryV1")
        if not 1 <= max_response_bytes <= 10_000_000:
            raise SearchAdapterError(
                "INVALID_RESPONSE_BUDGET",
                "max_response_bytes must be between 1 and 10000000",
            )
        if max_physical_requests is not None and (
            type(max_physical_requests) is not int
            or not 0 <= max_physical_requests <= 64
        ):
            raise SearchAdapterError(
                "INVALID_PHYSICAL_REQUEST_BUDGET",
                "max_physical_requests must be between 0 and 64",
            )
        if max_physical_requests == 0:
            raise SearchAdapterError(
                "SEARCH_REQUEST_BUDGET_EXCEEDED",
                "no physical arXiv request remains",
            )
        deadline = None
        if remaining_walltime_seconds is not None:
            if (
                isinstance(remaining_walltime_seconds, bool)
                or not isinstance(remaining_walltime_seconds, (int, float))
                or not math.isfinite(remaining_walltime_seconds)
                or remaining_walltime_seconds <= 0
            ):
                raise SearchAdapterError(
                    "WALLTIME_BUDGET_EXCEEDED",
                    "remaining walltime must be finite and positive",
                )
            deadline = _read_finite_clock(
                self.monotonic_clock,
                "monotonic_clock",
            ) + float(remaining_walltime_seconds)
        pacing_delay = self._pace_request(deadline_monotonic=deadline)
        cleaned_query = " ".join(query.text.replace('"', " ").split())
        clauses = [f'all:"{cleaned_query}"']
        if (
            self.publication_year_from is not None
            or self.publication_year_to is not None
        ):
            lower_year = self.publication_year_from or 1991
            upper_year = self.publication_year_to or 2200
            clauses.append(
                "submittedDate:"
                f"[{lower_year:04d}01010000 TO {upper_year:04d}12312359]"
            )
        url = "https://export.arxiv.org/api/query?" + urlencode(
            {
                "max_results": str(self.max_results),
                "search_query": " AND ".join(clauses),
                "sortBy": "relevance",
                "sortOrder": "descending",
                "start": "0",
            }
        )
        try:
            result = self.transport.get(
                url,
                headers={
                    "Accept": "application/atom+xml",
                    "Accept-Encoding": "identity",
                    "User-Agent": "materials-screening-agent/0.1 (metadata-only)",
                },
                timeout_seconds=float(self.timeout_seconds),
                max_response_bytes=max_response_bytes,
                deadline_monotonic=deadline,
                max_physical_requests=max_physical_requests,
            )
        except SearchAdapterError as error:
            hops = error.physical_hops or (
                PhysicalSearchHop(
                    outcome="error",
                    error_code=error.code,
                    http_status=error.http_status,
                    response_bytes=error.response_bytes,
                ),
            )
            attempts = _attempt_records_from_physical_hops(
                query_id=query.query_id,
                first_attempt_number=1,
                physical_hops=hops,
                pacing_delay_seconds=pacing_delay,
            )
            raise error.with_attempts(attempts) from error
        if isinstance(result, bytes):
            payload = result
            hops = (
                PhysicalSearchHop(
                    outcome="success",
                    error_code=None,
                    http_status=200,
                    response_bytes=len(payload),
                ),
            )
        elif isinstance(result, BoundedHttpResult):
            payload = result.payload
            hops = result.physical_hops
        else:
            raise SearchAdapterError(
                "INVALID_NETWORK_PAYLOAD",
                "metadata transport must return bytes or BoundedHttpResult",
            )
        if len(payload) > max_response_bytes:
            raise SearchAdapterError(
                "RESPONSE_BUDGET_EXCEEDED",
                "metadata transport returned more than the declared byte budget",
                response_bytes=len(payload),
            )
        attempts = _attempt_records_from_physical_hops(
            query_id=query.query_id,
            first_attempt_number=1,
            physical_hops=hops,
            pacing_delay_seconds=pacing_delay,
        )
        return RawSearchPage(
            provider="arxiv",
            query_id=query.query_id,
            payload=payload,
            media_type="application/atom+xml",
            attempts=attempts,
        )

    def _pace_request(self, *, deadline_monotonic: float | None) -> float:
        with self._pacing_lock:
            now = _read_finite_clock(self.monotonic_clock, "monotonic_clock")
            delay = 0.0
            if self._last_request_started is not None:
                elapsed = now - self._last_request_started
                if elapsed < 0:
                    raise SearchAdapterError(
                        "INVALID_MONOTONIC_CLOCK",
                        "monotonic_clock moved backwards",
                    )
                delay = max(0.0, self.min_request_interval_seconds - elapsed)
            deadline_remaining = _remaining_before_deadline(
                deadline_monotonic,
                monotonic_clock=self.monotonic_clock,
            )
            if deadline_remaining is not None and delay >= deadline_remaining:
                raise SearchAdapterError(
                    "WALLTIME_BUDGET_EXCEEDED",
                    "arXiv pacing cannot complete before the run deadline",
                )
            if delay:
                self.sleeper(delay)
            self._last_request_started = _read_finite_clock(
                self.monotonic_clock,
                "monotonic_clock",
            )
            return delay


class OpenAlexPublicAdapter:
    """OpenAlex Works metadata adapter with an explicit key and one-call budget.

    OpenAlex credentials are resolved only when ``search`` is invoked.  They are
    never included in component identity, attempt records, exceptions, or raw
    response Artifacts.  Callers can inject a resolver for a secret store; the
    default reads ``OPENALEX_API_KEY`` from the process environment.
    """

    network_access = True

    def __init__(
        self,
        *,
        max_results: int = 5,
        timeout_seconds: int = 20,
        publication_year_from: int | None = None,
        publication_year_to: int | None = None,
        api_key_resolver: Callable[[], str] | None = None,
        transport: BoundedHttpTransport | None = None,
        monotonic_clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if not 1 <= max_results <= 20:
            raise ValueError("max_results must be between 1 and 20")
        if not 1 <= timeout_seconds <= 120:
            raise ValueError("timeout_seconds must be between 1 and 120")
        _validate_publication_year_range(
            publication_year_from,
            publication_year_to,
        )
        if api_key_resolver is not None and not callable(api_key_resolver):
            raise ValueError("api_key_resolver must be callable")
        if not callable(monotonic_clock):
            raise ValueError("monotonic_clock must be callable")
        self.max_results = max_results
        self.timeout_seconds = timeout_seconds
        self.publication_year_from = publication_year_from
        self.publication_year_to = publication_year_to
        self.api_key_resolver = api_key_resolver or (
            lambda: os.environ.get("OPENALEX_API_KEY", "")
        )
        self.transport = transport or UrlLibBoundedTransport(
            monotonic_clock=monotonic_clock,
            allowed_hosts=frozenset({"api.openalex.org"}),
        )
        self.monotonic_clock = monotonic_clock
        fingerprint = canonical_json_bytes(
            {
                "max_results": max_results,
                "publication_year_from": publication_year_from,
                "publication_year_to": publication_year_to,
                "source_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                "timeout_seconds": timeout_seconds,
            }
        )
        self.component = ComponentSnapshotV1(
            component_id="openalex-public-adapter",
            version="works-api-v1",
            implementation_sha256=hashlib.sha256(fingerprint).hexdigest(),
        )

    def search(
        self,
        query: SearchQueryV1,
        *,
        max_response_bytes: int,
        remaining_walltime_seconds: float | None = None,
        max_physical_requests: int | None = None,
    ) -> RawSearchPage:
        if not isinstance(query, SearchQueryV1):
            raise SearchAdapterError("INVALID_QUERY", "query must be SearchQueryV1")
        if not 1 <= max_response_bytes <= 10_000_000:
            raise SearchAdapterError(
                "INVALID_RESPONSE_BUDGET",
                "max_response_bytes must be between 1 and 10000000",
            )
        if max_physical_requests is not None and (
            type(max_physical_requests) is not int
            or not 0 <= max_physical_requests <= 64
        ):
            raise SearchAdapterError(
                "INVALID_PHYSICAL_REQUEST_BUDGET",
                "max_physical_requests must be between 0 and 64",
            )
        if max_physical_requests == 0:
            raise SearchAdapterError(
                "SEARCH_REQUEST_BUDGET_EXCEEDED",
                "no physical OpenAlex request remains",
            )
        try:
            resolved_key = self.api_key_resolver()
        except (OSError, RuntimeError, ValueError) as error:
            raise SearchAdapterError(
                "OPENALEX_CREDENTIAL_UNAVAILABLE",
                "OpenAlex API key is unavailable from the configured secret source",
            ) from error
        api_key = resolved_key.strip() if isinstance(resolved_key, str) else ""
        if not api_key or len(api_key) > 512 or any(ord(char) < 33 for char in api_key):
            raise SearchAdapterError(
                "OPENALEX_CREDENTIAL_UNAVAILABLE",
                "OpenAlex API key is unavailable from the configured secret source",
            )
        deadline = None
        if remaining_walltime_seconds is not None:
            if (
                isinstance(remaining_walltime_seconds, bool)
                or not isinstance(remaining_walltime_seconds, (int, float))
                or not math.isfinite(remaining_walltime_seconds)
                or remaining_walltime_seconds <= 0
            ):
                raise SearchAdapterError(
                    "WALLTIME_BUDGET_EXCEEDED",
                    "remaining walltime must be finite and positive",
                )
            deadline = _read_finite_clock(
                self.monotonic_clock,
                "monotonic_clock",
            ) + float(remaining_walltime_seconds)
        filters: list[str] = []
        if self.publication_year_from is not None:
            filters.append(f"from_publication_date:{self.publication_year_from}-01-01")
        if self.publication_year_to is not None:
            filters.append(f"to_publication_date:{self.publication_year_to}-12-31")
        parameters = {
            "api_key": api_key,
            "per_page": str(self.max_results),
            "search": query.text,
            "select": (
                "id,doi,title,publication_year,primary_location,authorships,"
                "keywords,abstract_inverted_index"
            ),
        }
        if filters:
            parameters["filter"] = ",".join(filters)
        url = f"https://api.openalex.org/works?{urlencode(parameters)}"
        try:
            result = self.transport.get(
                url,
                headers={
                    "Accept": "application/json",
                    "Accept-Encoding": "identity",
                    "User-Agent": "materials-screening-agent/0.1 (metadata-only)",
                },
                timeout_seconds=float(self.timeout_seconds),
                max_response_bytes=max_response_bytes,
                deadline_monotonic=deadline,
                max_physical_requests=max_physical_requests,
            )
        except SearchAdapterError as error:
            hops = error.physical_hops or (
                PhysicalSearchHop(
                    outcome="error",
                    error_code=error.code,
                    http_status=error.http_status,
                    response_bytes=error.response_bytes,
                ),
            )
            attempts = _attempt_records_from_physical_hops(
                query_id=query.query_id,
                first_attempt_number=1,
                physical_hops=hops,
                pacing_delay_seconds=0.0,
            )
            raise error.with_attempts(attempts) from error
        if isinstance(result, bytes):
            payload = result
            hops = (
                PhysicalSearchHop(
                    outcome="success",
                    error_code=None,
                    http_status=200,
                    response_bytes=len(payload),
                ),
            )
        elif isinstance(result, BoundedHttpResult):
            payload = result.payload
            hops = result.physical_hops
        else:
            raise SearchAdapterError(
                "INVALID_NETWORK_PAYLOAD",
                "metadata transport must return bytes or BoundedHttpResult",
            )
        attempts = _attempt_records_from_physical_hops(
            query_id=query.query_id,
            first_attempt_number=1,
            physical_hops=hops,
            pacing_delay_seconds=0.0,
        )
        return RawSearchPage(
            provider="openalex",
            query_id=query.query_id,
            payload=payload,
            attempts=attempts,
        )


class OstiPublicAdapter:
    """Official OSTI.GOV v1 adapter for bounded DOE research metadata."""

    network_access = True

    def __init__(
        self,
        *,
        max_results: int = 5,
        timeout_seconds: int = 20,
        publication_year_from: int | None = None,
        publication_year_to: int | None = None,
        transport: BoundedHttpTransport | None = None,
        monotonic_clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if not 1 <= max_results <= 20:
            raise ValueError("max_results must be between 1 and 20")
        if not 1 <= timeout_seconds <= 120:
            raise ValueError("timeout_seconds must be between 1 and 120")
        _validate_publication_year_range(
            publication_year_from,
            publication_year_to,
        )
        if not callable(monotonic_clock):
            raise ValueError("monotonic_clock must be callable")
        self.max_results = max_results
        self.timeout_seconds = timeout_seconds
        self.publication_year_from = publication_year_from
        self.publication_year_to = publication_year_to
        self.transport = transport or UrlLibBoundedTransport(
            monotonic_clock=monotonic_clock,
            allowed_hosts=frozenset({"www.osti.gov"}),
        )
        self.monotonic_clock = monotonic_clock
        fingerprint = canonical_json_bytes(
            {
                "max_results": max_results,
                "publication_year_from": publication_year_from,
                "publication_year_to": publication_year_to,
                "source_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                "timeout_seconds": timeout_seconds,
            }
        )
        self.component = ComponentSnapshotV1(
            component_id="osti-public-adapter",
            version="records-api-v1",
            implementation_sha256=hashlib.sha256(fingerprint).hexdigest(),
        )

    def search(
        self,
        query: SearchQueryV1,
        *,
        max_response_bytes: int,
        remaining_walltime_seconds: float | None = None,
        max_physical_requests: int | None = None,
    ) -> RawSearchPage:
        if not isinstance(query, SearchQueryV1):
            raise SearchAdapterError("INVALID_QUERY", "query must be SearchQueryV1")
        if not 1 <= max_response_bytes <= 10_000_000:
            raise SearchAdapterError(
                "INVALID_RESPONSE_BUDGET",
                "max_response_bytes must be between 1 and 10000000",
            )
        if max_physical_requests is not None and (
            type(max_physical_requests) is not int
            or not 0 <= max_physical_requests <= 64
        ):
            raise SearchAdapterError(
                "INVALID_PHYSICAL_REQUEST_BUDGET",
                "max_physical_requests must be between 0 and 64",
            )
        if max_physical_requests == 0:
            raise SearchAdapterError(
                "SEARCH_REQUEST_BUDGET_EXCEEDED",
                "no physical OSTI request remains",
            )
        deadline = None
        if remaining_walltime_seconds is not None:
            if (
                isinstance(remaining_walltime_seconds, bool)
                or not isinstance(remaining_walltime_seconds, (int, float))
                or not math.isfinite(remaining_walltime_seconds)
                or remaining_walltime_seconds <= 0
            ):
                raise SearchAdapterError(
                    "WALLTIME_BUDGET_EXCEEDED",
                    "remaining walltime must be finite and positive",
                )
            deadline = _read_finite_clock(
                self.monotonic_clock,
                "monotonic_clock",
            ) + float(remaining_walltime_seconds)
        parameters = {
            "page": "1",
            "q": query.text,
            "rows": str(self.max_results),
        }
        if self.publication_year_from is not None:
            parameters["publication_date_start"] = (
                f"01/01/{self.publication_year_from:04d}"
            )
        if self.publication_year_to is not None:
            parameters["publication_date_end"] = (
                f"12/31/{self.publication_year_to:04d}"
            )
        url = "https://www.osti.gov/api/v1/records?" + urlencode(parameters)
        try:
            result = self.transport.get(
                url,
                headers={
                    "Accept": "application/json",
                    "Accept-Encoding": "identity",
                    "User-Agent": "materials-screening-agent/0.1 (metadata-only)",
                },
                timeout_seconds=float(self.timeout_seconds),
                max_response_bytes=max_response_bytes,
                deadline_monotonic=deadline,
                max_physical_requests=max_physical_requests,
            )
        except SearchAdapterError as error:
            hops = error.physical_hops or (
                PhysicalSearchHop(
                    outcome="error",
                    error_code=error.code,
                    http_status=error.http_status,
                    response_bytes=error.response_bytes,
                ),
            )
            attempts = _attempt_records_from_physical_hops(
                query_id=query.query_id,
                first_attempt_number=1,
                physical_hops=hops,
                pacing_delay_seconds=0.0,
            )
            raise error.with_attempts(attempts) from error
        if isinstance(result, bytes):
            payload = result
            hops = (
                PhysicalSearchHop(
                    outcome="success",
                    error_code=None,
                    http_status=200,
                    response_bytes=len(payload),
                ),
            )
        elif isinstance(result, BoundedHttpResult):
            payload = result.payload
            hops = result.physical_hops
        else:
            raise SearchAdapterError(
                "INVALID_NETWORK_PAYLOAD",
                "metadata transport must return bytes or BoundedHttpResult",
            )
        if len(payload) > max_response_bytes:
            raise SearchAdapterError(
                "RESPONSE_BUDGET_EXCEEDED",
                "metadata transport returned more than the declared byte budget",
                response_bytes=len(payload),
            )
        attempts = _attempt_records_from_physical_hops(
            query_id=query.query_id,
            first_attempt_number=1,
            physical_hops=hops,
            pacing_delay_seconds=0.0,
        )
        return RawSearchPage(
            provider="osti",
            query_id=query.query_id,
            payload=payload,
            attempts=attempts,
        )


class MultiSourceSearchAdapter:
    """Execute one bounded query concurrently against an explicit provider set.

    The returned payload is a canonical envelope containing each provider's
    exact response bytes as base64 plus its SHA-256.  This keeps one existing
    ``SearchAdapter`` call boundary while preserving byte-level provenance.  A
    child failure is retained in the envelope and does not discard successful
    sibling responses.  The logical query fails only when every provider fails.
    """

    network_access = True

    def __init__(self, adapters: Sequence[SearchAdapter]) -> None:
        selected = tuple(adapters)
        if len(selected) < 2 or len(selected) > 16:
            raise ValueError("multi-source search requires two to sixteen adapters")
        if any(not adapter.network_access for adapter in selected):
            raise ValueError("multi-source public search requires networked adapters")
        provider_ids = tuple(adapter.component.component_id for adapter in selected)
        if len(set(provider_ids)) != len(provider_ids):
            raise ValueError("multi-source adapter component IDs must be unique")
        self.adapters = selected
        self.component = ComponentSnapshotV1(
            component_id="multi-source-public-search-adapter",
            version="v1",
            implementation_sha256=canonical_sha256(
                {
                    "children": tuple(
                        (component.component_id, component.version, component.implementation_sha256)
                        for component in (adapter.component for adapter in selected)
                    ),
                    "source_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                }
            ),
        )

    def search(
        self,
        query: SearchQueryV1,
        *,
        max_response_bytes: int,
        remaining_walltime_seconds: float | None = None,
        max_physical_requests: int | None = None,
    ) -> RawSearchPage:
        if not isinstance(query, SearchQueryV1):
            raise SearchAdapterError("INVALID_QUERY", "query must be SearchQueryV1")
        if not 1 <= max_response_bytes <= 10_000_000:
            raise SearchAdapterError(
                "INVALID_RESPONSE_BUDGET",
                "max_response_bytes must be between 1 and 10000000",
            )
        if max_physical_requests is not None and (
            type(max_physical_requests) is not int
            or not 0 <= max_physical_requests <= 64
        ):
            raise SearchAdapterError(
                "INVALID_PHYSICAL_REQUEST_BUDGET",
                "max_physical_requests must be between 0 and 64",
            )
        if (
            remaining_walltime_seconds is not None
            and (
                isinstance(remaining_walltime_seconds, bool)
                or not isinstance(remaining_walltime_seconds, (int, float))
                or not math.isfinite(remaining_walltime_seconds)
                or remaining_walltime_seconds <= 0
            )
        ):
            raise SearchAdapterError(
                "WALLTIME_BUDGET_EXCEEDED",
                "remaining walltime must be finite and positive",
            )
        if max_physical_requests is not None and max_physical_requests < len(self.adapters):
            raise SearchAdapterError(
                "SEARCH_REQUEST_BUDGET_EXCEEDED",
                "multi-source query budget cannot cover every configured provider",
            )
        provider_count = len(self.adapters)
        child_response_budget = max(1, max_response_bytes // (2 * provider_count))
        if max_physical_requests is None:
            child_request_budgets: tuple[int | None, ...] = (None,) * provider_count
        else:
            base, remainder = divmod(max_physical_requests, provider_count)
            child_request_budgets = tuple(
                base + (1 if index < remainder else 0)
                for index in range(provider_count)
            )

        def execute(index: int, adapter: SearchAdapter):
            try:
                page = adapter.search(
                    query,
                    max_response_bytes=child_response_budget,
                    remaining_walltime_seconds=remaining_walltime_seconds,
                    max_physical_requests=child_request_budgets[index],
                )
            except SearchAdapterError as error:
                return index, None, error
            return index, page, None

        completed: dict[int, tuple[RawSearchPage | None, SearchAdapterError | None]] = {}
        with ThreadPoolExecutor(
            max_workers=provider_count,
            thread_name_prefix="hermes-literature-provider",
        ) as executor:
            futures = {
                executor.submit(execute, index, adapter): index
                for index, adapter in enumerate(self.adapters)
            }
            for future in as_completed(futures):
                index, page, error = future.result()
                completed[index] = (page, error)

        pages: list[dict[str, str]] = []
        failures: list[dict[str, object]] = []
        attempts: list[SearchAttemptRecord] = []
        for index, adapter in enumerate(self.adapters):
            page, error = completed[index]
            if error is not None:
                attempts.extend(error.attempts)
                failures.append(
                    {
                        "error_code": error.code,
                        "http_status": error.http_status,
                        "provider": _adapter_provider_label(adapter),
                        "response_bytes": error.response_bytes,
                    }
                )
                continue
            if page is None:  # pragma: no cover - defensive executor invariant
                raise SearchAdapterError(
                    "INVALID_PROVIDER_RESULT",
                    "multi-source provider returned neither a page nor an error",
                )
            attempts.extend(page.attempts)
            pages.append(
                {
                    "payload_base64": base64.b64encode(page.payload).decode("ascii"),
                    "payload_sha256": hashlib.sha256(page.payload).hexdigest(),
                    "provider": page.provider,
                }
            )
        if not pages:
            raise SearchAdapterError(
                "ALL_PROVIDERS_FAILED",
                "every configured literature provider failed",
                attempts=_renumber_attempts(query.query_id, tuple(attempts)),
            )
        payload = canonical_json_bytes(
            {
                "failures": failures,
                "pages": pages,
                "schema_version": "inspiration-multi-source-page-v2",
            }
        )
        if len(payload) > max_response_bytes:
            raise SearchAdapterError(
                "RESPONSE_BUDGET_EXCEEDED",
                "encoded multi-source response exceeds the aggregate byte budget",
                attempts=_renumber_attempts(query.query_id, tuple(attempts)),
            )
        return RawSearchPage(
            provider="multi-source-v1",
            query_id=query.query_id,
            payload=payload,
            attempts=_renumber_attempts(query.query_id, tuple(attempts)),
        )


def _adapter_provider_label(adapter: SearchAdapter) -> str:
    """Return a stable non-secret label even when a child fails before a page exists."""

    explicit = getattr(adapter, "provider_id", None)
    if isinstance(explicit, str) and explicit:
        return explicit
    component_id = adapter.component.component_id
    suffix = "-public-adapter"
    return component_id[: -len(suffix)] if component_id.endswith(suffix) else component_id


def _renumber_attempts(
    query_id: str,
    attempts: Sequence[SearchAttemptRecord],
) -> tuple[SearchAttemptRecord, ...]:
    return tuple(
        replace(item, query_id=query_id, attempt_number=index)
        for index, item in enumerate(attempts, start=1)
    )


def public_search_adapter_from_environment(
    *,
    budget: "SearchBudgetV1",
    environment: Mapping[str, str] | None = None,
    crossref_transport: BoundedHttpTransport | None = None,
    openalex_transport: BoundedHttpTransport | None = None,
    semantic_scholar_transport: BoundedHttpTransport | None = None,
    arxiv_transport: BoundedHttpTransport | None = None,
    osti_transport: BoundedHttpTransport | None = None,
) -> SearchAdapter:
    """Build Crossref by default or an explicitly opted-in provider set.

    This factory binds the policy year window to every provider request.  The
    OpenAlex key remains lazy: constructing the adapter does not resolve or
    persist the secret.  Multi-source modes reserve one physical request per
    provider and logical query, and disable Crossref retries so every provider
    is covered by the frozen request budget.
    """

    from material_agent.inspiration.policy import SearchBudgetV1

    if not isinstance(budget, SearchBudgetV1):
        raise TypeError("budget must be SearchBudgetV1")
    selected = environment if environment is not None else os.environ
    raw_max_results = selected.get(PUBLIC_SEARCH_MAX_RESULTS_ENV, "5").strip()
    try:
        max_results = int(raw_max_results)
    except ValueError as error:
        raise ValueError(
            f"{PUBLIC_SEARCH_MAX_RESULTS_ENV} must be an integer from 1 to 20"
        ) from error
    if not 1 <= max_results <= 20:
        raise ValueError(
            f"{PUBLIC_SEARCH_MAX_RESULTS_ENV} must be an integer from 1 to 20"
        )
    mode = selected.get(PUBLIC_SEARCH_PROVIDER_ENV, "crossref").strip().casefold()
    if not mode:
        mode = "crossref"
    requested = tuple(part.strip() for part in mode.split("+") if part.strip())
    supported = ("crossref", "openalex", "semantic-scholar", "arxiv", "osti")
    if (
        not requested
        or len(requested) != len(set(requested))
        or any(provider not in supported for provider in requested)
    ):
        raise ValueError(
            f"{PUBLIC_SEARCH_PROVIDER_ENV} must contain unique providers from "
            "'crossref', 'openalex', 'semantic-scholar', 'arxiv', and 'osti'"
        )
    provider_ids = tuple(provider for provider in supported if provider in requested)
    required_requests = budget.max_queries * len(provider_ids)
    if budget.max_physical_requests < required_requests:
        provider_count = {2: "two", 3: "three", 4: "four"}.get(
            len(provider_ids),
            str(len(provider_ids)),
        )
        raise ValueError(
            f"{'+'.join(provider_ids)} requires at least {provider_count} "
            "physical requests per query"
        )
    adapters: list[SearchAdapter] = []
    for provider in provider_ids:
        if provider == "crossref":
            adapters.append(
                CrossrefPublicAdapter(
                    contact_email=selected.get(CROSSREF_CONTACT_EMAIL_ENV) or None,
                    max_results=max_results,
                    max_retries=0 if len(provider_ids) > 1 else 2,
                    publication_year_from=budget.publication_year_from,
                    publication_year_to=budget.publication_year_to,
                    transport=crossref_transport,
                )
            )
        elif provider == "openalex":
            adapters.append(
                OpenAlexPublicAdapter(
                    api_key_resolver=lambda: selected.get(OPENALEX_API_KEY_ENV, ""),
                    max_results=max_results,
                    publication_year_from=budget.publication_year_from,
                    publication_year_to=budget.publication_year_to,
                    transport=openalex_transport,
                )
            )
        elif provider == "semantic-scholar":
            from material_agent.inspiration.semantic_scholar import (
                SemanticScholarTopicSearchAdapter,
            )

            adapters.append(
                SemanticScholarTopicSearchAdapter(
                    max_results=max_results,
                    publication_year_from=budget.publication_year_from,
                    publication_year_to=budget.publication_year_to,
                    api_key_resolver=lambda: selected.get(
                        "SEMANTIC_SCHOLAR_API_KEY", ""
                    ),
                    transport=semantic_scholar_transport,
                )
            )
        elif provider == "arxiv":
            adapters.append(
                ArxivPublicAdapter(
                    max_results=max_results,
                    publication_year_from=budget.publication_year_from,
                    publication_year_to=budget.publication_year_to,
                    transport=arxiv_transport,
                )
            )
        else:
            adapters.append(
                OstiPublicAdapter(
                    max_results=max_results,
                    publication_year_from=budget.publication_year_from,
                    publication_year_to=budget.publication_year_to,
                    transport=osti_transport,
                )
            )
    if len(adapters) == 1:
        return adapters[0]
    return MultiSourceSearchAdapter(tuple(adapters))


class FixtureSearchAdapter:
    """Deterministic, network-free adapter backed by committed response bytes."""

    component = ComponentSnapshotV1(
        component_id="fixture-search-adapter",
        version="1",
        implementation_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
    )
    network_access = False

    def __init__(self, responses: Mapping[str, bytes]) -> None:
        self._responses = dict(responses)

    def search(
        self,
        query: SearchQueryV1,
        *,
        max_response_bytes: int,
        remaining_walltime_seconds: float | None = None,
        max_physical_requests: int | None = None,
    ) -> RawSearchPage:
        del remaining_walltime_seconds
        if max_physical_requests == 0:
            raise SearchAdapterError(
                "SEARCH_REQUEST_BUDGET_EXCEEDED",
                "no fixture search attempt remains",
            )
        if max_response_bytes < 1:
            raise SearchAdapterError(
                "INVALID_RESPONSE_BUDGET",
                "max_response_bytes must be positive",
            )
        try:
            payload = self._responses[query.query_id]
        except KeyError as error:
            raise SearchAdapterError(
                "FIXTURE_QUERY_NOT_FOUND",
                f"no fixture response for query {query.query_id!r}",
            ) from error
        if not isinstance(payload, bytes):
            raise SearchAdapterError(
                "INVALID_FIXTURE_PAYLOAD",
                f"fixture response for {query.query_id!r} is not bytes",
            )
        if len(payload) > max_response_bytes:
            raise SearchAdapterError(
                "RESPONSE_BUDGET_EXCEEDED",
                f"fixture response has {len(payload)} bytes; limit is {max_response_bytes}",
            )
        return RawSearchPage(
            provider="openalex-fixture",
            query_id=query.query_id,
            payload=payload,
            attempts=(
                SearchAttemptRecord(
                    query_id=query.query_id,
                    attempt_number=1,
                    outcome="success",
                    error_code=None,
                    http_status=None,
                    retry_delay_seconds=0.0,
                    pacing_delay_seconds=0.0,
                    response_bytes=len(payload),
                ),
            ),
        )


def normalize_doi(value: str | None) -> str | None:
    """Normalize a DOI without resolving it or making a network request."""

    if value is None:
        return None
    normalized = value.strip().lower()
    for prefix in (
        "https://doi.org/",
        "http://doi.org/",
        "https://dx.doi.org/",
        "http://dx.doi.org/",
        "doi:",
    ):
        if normalized.startswith(prefix):
            normalized = normalized[len(prefix) :]
            break
    if not normalized.startswith("10.") or "/" not in normalized:
        return None
    return normalized


def normalize_document_url(value: str | None) -> str | None:
    if value is None:
        return None
    candidate = value.strip()
    parts = urlsplit(candidate)
    if parts.scheme.lower() not in {"http", "https"} or not parts.netloc:
        return None
    path = parts.path.rstrip("/") or "/"
    return urlunsplit(
        (parts.scheme.lower(), parts.netloc.lower(), path, "", "")
    )


def document_id_for(
    *,
    provider: str,
    provider_record_id: str,
    doi: str | None,
    arxiv_id: str | None,
    canonical_url: str | None,
) -> str:
    """Create a stable identity using the strongest available public key."""

    normalized_doi = normalize_doi(doi)
    if normalized_doi is not None:
        identity: dict[str, str] = {"doi": normalized_doi}
    elif arxiv_id:
        identity = {"arxiv_id": arxiv_id.strip().lower()}
    else:
        normalized_url = normalize_document_url(canonical_url)
        if normalized_url is not None:
            identity = {"url": normalized_url}
        else:
            identity = {
                "provider": provider.strip().lower(),
                "provider_record_id": provider_record_id.strip(),
            }
    return deterministic_id("document", identity)


def normalize_arxiv_id(value: str | None) -> str | None:
    """Normalize an arXiv identifier and collapse versioned records."""

    if value is None:
        return None
    candidate = value.strip()
    for prefix in (
        "https://arxiv.org/abs/",
        "http://arxiv.org/abs/",
        "arxiv:",
    ):
        if candidate.casefold().startswith(prefix):
            candidate = candidate[len(prefix) :]
            break
    candidate = _ARXIV_VERSION_SUFFIX.sub("", candidate).strip()
    if not candidate or len(candidate) > 64:
        return None
    if any(character.isspace() for character in candidate):
        return None
    return candidate


def parse_arxiv_page(
    *,
    query: SearchQueryV1,
    payload: bytes,
    raw_response_artifact: ArtifactPointerV1,
    max_hits: int,
    publication_year_from: int | None = None,
    publication_year_to: int | None = None,
) -> ParsedSearchPage:
    """Parse one bounded official arXiv Atom feed without following links."""

    if max_hits < 1:
        raise SearchAdapterError("INVALID_HIT_BUDGET", "max_hits must be positive")
    _validate_publication_year_range(publication_year_from, publication_year_to)
    upper_payload = payload.upper()
    if b"<!DOCTYPE" in upper_payload or b"<!ENTITY" in upper_payload:
        raise SearchAdapterError(
            "UNSAFE_XML",
            "arXiv Atom metadata cannot contain DTD or entity declarations",
        )
    try:
        root = ElementTree.fromstring(payload)
    except ElementTree.ParseError as error:
        raise SearchAdapterError(
            "INVALID_XML",
            "arXiv response is not well-formed Atom XML",
        ) from error
    if root.tag != f"{{{_ATOM_NAMESPACE}}}feed":
        raise SearchAdapterError(
            "SCHEMA_DRIFT",
            "arXiv response root must be an Atom feed",
        )

    hits: list[SearchHitV1] = []
    warnings: list[str] = []
    for rank, entry in enumerate(
        root.findall(f"{{{_ATOM_NAMESPACE}}}entry"),
        start=1,
    ):
        if len(hits) >= max_hits:
            break
        raw_id = _atom_required_text(entry, "id")
        arxiv_id = normalize_arxiv_id(raw_id)
        if arxiv_id is None:
            raise SearchAdapterError(
                "SCHEMA_DRIFT",
                "arXiv entry has no valid identifier",
            )
        title = _atom_required_text(entry, "title")
        abstract = _atom_optional_text(entry, "summary")
        if abstract is not None and len(abstract) > 20_000:
            abstract = abstract[:20_000]
            warnings.append(f"ABSTRACT_TRUNCATED:{arxiv_id}")
        published = _atom_required_text(entry, "published")
        try:
            published_year = int(published[:4])
        except (TypeError, ValueError) as error:
            raise SearchAdapterError(
                "SCHEMA_DRIFT",
                f"arXiv published date for {arxiv_id!r} is invalid",
            ) from error
        if not 1600 <= published_year <= 2200:
            raise SearchAdapterError(
                "SCHEMA_DRIFT",
                f"arXiv published year for {arxiv_id!r} is invalid",
            )
        if not _publication_year_allowed(
            published_year,
            publication_year_from=publication_year_from,
            publication_year_to=publication_year_to,
        ):
            warnings.append(f"PUBLICATION_YEAR_REJECTED:{arxiv_id}")
            continue
        authors = tuple(
            dict.fromkeys(
                text
                for author in entry.findall(f"{{{_ATOM_NAMESPACE}}}author")
                if (
                    text := _clean_atom_text(
                        author.findtext(f"{{{_ATOM_NAMESPACE}}}name")
                    )
                )
            )
        )[:256]
        keywords = tuple(
            dict.fromkeys(
                term
                for category in entry.findall(f"{{{_ATOM_NAMESPACE}}}category")
                if (term := _clean_atom_text(category.attrib.get("term")))
            )
        )[:128]
        doi = normalize_doi(
            _clean_atom_text(entry.findtext(f"{{{_ARXIV_NAMESPACE}}}doi"))
        )
        canonical_url = f"https://arxiv.org/abs/{arxiv_id}"
        document_id = document_id_for(
            provider="arxiv",
            provider_record_id=arxiv_id,
            doi=doi,
            arxiv_id=arxiv_id,
            canonical_url=canonical_url,
        )
        hit_id = deterministic_id(
            "hit",
            {
                "provider": "arxiv",
                "provider_record_id": arxiv_id,
                "query_id": query.query_id,
                "raw_response_sha256": raw_response_artifact.sha256,
            },
        )
        hits.append(
            SearchHitV1(
                hit_id=hit_id,
                document_id=document_id,
                provider="arxiv",
                provider_record_id=arxiv_id,
                query_ids=(query.query_id,),
                provider_rank=rank,
                title=title,
                authors=authors,
                published_year=published_year,
                doi=doi,
                arxiv_id=arxiv_id,
                canonical_url=canonical_url,
                abstract=abstract,
                keywords=keywords,
                raw_response_artifact=raw_response_artifact,
            )
        )
    return ParsedSearchPage(hits=tuple(hits), warnings=tuple(warnings))


def _clean_atom_text(value: str | None) -> str | None:
    if not isinstance(value, str):
        return None
    cleaned = " ".join(html.unescape(value).split())
    return cleaned or None


def _atom_optional_text(entry: ElementTree.Element, local_name: str) -> str | None:
    return _clean_atom_text(entry.findtext(f"{{{_ATOM_NAMESPACE}}}{local_name}"))


def _atom_required_text(entry: ElementTree.Element, local_name: str) -> str:
    value = _atom_optional_text(entry, local_name)
    if value is None:
        raise SearchAdapterError(
            "SCHEMA_DRIFT",
            f"arXiv entry is missing Atom field {local_name!r}",
        )
    return value


def parse_osti_page(
    *,
    query: SearchQueryV1,
    payload: bytes,
    raw_response_artifact: ArtifactPointerV1,
    max_hits: int,
    publication_year_from: int | None = None,
    publication_year_to: int | None = None,
) -> ParsedSearchPage:
    """Parse one bounded OSTI.GOV records response without following links."""

    if max_hits < 1:
        raise SearchAdapterError("INVALID_HIT_BUDGET", "max_hits must be positive")
    _validate_publication_year_range(publication_year_from, publication_year_to)
    try:
        decoded = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise SearchAdapterError(
            "INVALID_JSON",
            "OSTI response is not UTF-8 JSON",
        ) from error
    if not isinstance(decoded, list):
        raise SearchAdapterError(
            "SCHEMA_DRIFT",
            "OSTI response must be a records array",
        )

    hits: list[SearchHitV1] = []
    warnings: list[str] = []
    for rank, record in enumerate(decoded, start=1):
        if len(hits) >= max_hits:
            break
        if not isinstance(record, dict):
            raise SearchAdapterError("SCHEMA_DRIFT", "OSTI record is not an object")
        record_id = _required_text(record, "osti_id")
        raw_title = _required_text(record, "title")
        title = _crossref_abstract(raw_title)
        if title is None:
            raise SearchAdapterError("SCHEMA_DRIFT", "OSTI record title is empty")
        publication_date = record.get("publication_date")
        published_year: int | None = None
        if publication_date is not None:
            if not isinstance(publication_date, str) or len(publication_date) < 4:
                raise SearchAdapterError(
                    "SCHEMA_DRIFT",
                    f"OSTI publication date for {record_id!r} is invalid",
                )
            try:
                published_year = int(publication_date[:4])
            except ValueError as error:
                raise SearchAdapterError(
                    "SCHEMA_DRIFT",
                    f"OSTI publication date for {record_id!r} is invalid",
                ) from error
            if not 1600 <= published_year <= 2200:
                raise SearchAdapterError(
                    "SCHEMA_DRIFT",
                    f"OSTI publication year for {record_id!r} is invalid",
                )
        if not _publication_year_allowed(
            published_year,
            publication_year_from=publication_year_from,
            publication_year_to=publication_year_to,
        ):
            warnings.append(f"PUBLICATION_YEAR_REJECTED:{record_id}")
            continue
        authors_value = record.get("authors")
        if authors_value is None:
            authors = ()
        elif isinstance(authors_value, list):
            authors = tuple(
                dict.fromkeys(
                    cleaned[:512]
                    for item in authors_value[:256]
                    if isinstance(item, str)
                    if (cleaned := " ".join(html.unescape(item).split()))
                )
            )
        else:
            raise SearchAdapterError(
                "SCHEMA_DRIFT",
                f"OSTI authors for {record_id!r} are not an array",
            )
        subjects_value = record.get("subjects")
        if subjects_value is None:
            subjects: tuple[str, ...] = ()
        elif isinstance(subjects_value, list):
            subjects = tuple(
                dict.fromkeys(
                    cleaned[:512]
                    for item in subjects_value[:128]
                    if isinstance(item, str)
                    if (cleaned := " ".join(html.unescape(item).split()))
                )
            )
        else:
            raise SearchAdapterError(
                "SCHEMA_DRIFT",
                f"OSTI subjects for {record_id!r} are not an array",
            )
        product_type = _optional_text(record.get("product_type"))
        keywords = tuple(dict.fromkeys((*subjects, *((product_type,) if product_type else ()))))
        abstract = _crossref_abstract(record.get("description"))
        if abstract is not None and len(abstract) > 20_000:
            abstract = abstract[:20_000]
            warnings.append(f"ABSTRACT_TRUNCATED:{record_id}")
        doi = normalize_doi(_optional_text(record.get("doi")))
        canonical_url = f"https://www.osti.gov/biblio/{record_id}"
        document_id = document_id_for(
            provider="osti",
            provider_record_id=record_id,
            doi=doi,
            arxiv_id=None,
            canonical_url=canonical_url,
        )
        hit_id = deterministic_id(
            "hit",
            {
                "provider": "osti",
                "provider_record_id": record_id,
                "query_id": query.query_id,
                "raw_response_sha256": raw_response_artifact.sha256,
            },
        )
        hits.append(
            SearchHitV1(
                hit_id=hit_id,
                document_id=document_id,
                provider="osti",
                provider_record_id=record_id,
                query_ids=(query.query_id,),
                provider_rank=rank,
                title=title,
                authors=authors,
                published_year=published_year,
                doi=doi,
                canonical_url=canonical_url,
                abstract=abstract,
                keywords=keywords,
                raw_response_artifact=raw_response_artifact,
            )
        )
    return ParsedSearchPage(hits=tuple(hits), warnings=tuple(warnings))


def parse_openalex_page(
    *,
    query: SearchQueryV1,
    payload: bytes,
    raw_response_artifact: ArtifactPointerV1,
    provider: str = "openalex",
    max_hits: int,
    publication_year_from: int | None = None,
    publication_year_to: int | None = None,
) -> ParsedSearchPage:
    """Parse bounded OpenAlex-style JSON metadata without fetching any work page."""

    if max_hits < 1:
        raise SearchAdapterError("INVALID_HIT_BUDGET", "max_hits must be positive")
    _validate_publication_year_range(publication_year_from, publication_year_to)
    try:
        decoded = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise SearchAdapterError("INVALID_JSON", "search response is not UTF-8 JSON") from error
    if not isinstance(decoded, dict) or not isinstance(decoded.get("results"), list):
        raise SearchAdapterError(
            "SCHEMA_DRIFT",
            "OpenAlex response must contain a results array",
        )

    raw_results = decoded["results"]
    hits: list[SearchHitV1] = []
    warnings: list[str] = []
    for rank, record in enumerate(raw_results, start=1):
        if len(hits) >= max_hits:
            break
        if not isinstance(record, dict):
            raise SearchAdapterError("SCHEMA_DRIFT", "OpenAlex result is not an object")
        record_id = _required_text(record, "id")
        title = _required_text(record, "title")
        doi = normalize_doi(_optional_text(record.get("doi")))
        canonical_url = _openalex_landing_url(record) or normalize_document_url(record_id)
        abstract = _openalex_abstract(record.get("abstract_inverted_index"))
        if abstract is not None and len(abstract) > 20_000:
            abstract = abstract[:20_000]
            warnings.append(f"ABSTRACT_TRUNCATED:{record_id}")
        authors = _openalex_authors(record.get("authorships"))
        keywords = _openalex_keywords(record.get("keywords"))
        published_year = record.get("publication_year")
        if published_year is not None and type(published_year) is not int:
            raise SearchAdapterError(
                "SCHEMA_DRIFT",
                f"publication_year for {record_id!r} is not an integer",
            )
        if not _publication_year_allowed(
            published_year,
            publication_year_from=publication_year_from,
            publication_year_to=publication_year_to,
        ):
            warnings.append(f"PUBLICATION_YEAR_REJECTED:{record_id}")
            continue
        document_id = document_id_for(
            provider=provider,
            provider_record_id=record_id,
            doi=doi,
            arxiv_id=None,
            canonical_url=canonical_url,
        )
        hit_id = deterministic_id(
            "hit",
            {
                "provider": provider,
                "provider_record_id": record_id,
                "query_id": query.query_id,
                "raw_response_sha256": raw_response_artifact.sha256,
            },
        )
        hits.append(
            SearchHitV1(
                hit_id=hit_id,
                document_id=document_id,
                provider=provider,
                provider_record_id=record_id,
                query_ids=(query.query_id,),
                provider_rank=rank,
                title=title,
                authors=authors,
                published_year=published_year,
                doi=doi,
                canonical_url=canonical_url,
                abstract=abstract,
                keywords=keywords,
                raw_response_artifact=raw_response_artifact,
            )
        )
    return ParsedSearchPage(hits=tuple(hits), warnings=tuple(warnings))


def parse_crossref_page(
    *,
    query: SearchQueryV1,
    payload: bytes,
    raw_response_artifact: ArtifactPointerV1,
    max_hits: int,
    publication_year_from: int | None = None,
    publication_year_to: int | None = None,
) -> ParsedSearchPage:
    """Parse a bounded Crossref v1 response without following full-text links."""

    if max_hits < 1:
        raise SearchAdapterError("INVALID_HIT_BUDGET", "max_hits must be positive")
    _validate_publication_year_range(publication_year_from, publication_year_to)
    try:
        decoded = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise SearchAdapterError("INVALID_JSON", "search response is not UTF-8 JSON") from error
    if not isinstance(decoded, dict) or decoded.get("status") != "ok":
        raise SearchAdapterError("SCHEMA_DRIFT", "Crossref response status is not ok")
    message = decoded.get("message")
    if not isinstance(message, dict) or not isinstance(message.get("items"), list):
        raise SearchAdapterError(
            "SCHEMA_DRIFT",
            "Crossref response must contain a message.items array",
        )

    hits: list[SearchHitV1] = []
    warnings: list[str] = []
    for rank, record in enumerate(message["items"], start=1):
        if len(hits) >= max_hits:
            break
        if not isinstance(record, dict):
            raise SearchAdapterError("SCHEMA_DRIFT", "Crossref item is not an object")
        doi = normalize_doi(_optional_text(record.get("DOI")))
        if doi is None:
            raise SearchAdapterError("SCHEMA_DRIFT", "Crossref item has no valid DOI")
        title = _crossref_title(record.get("title"))
        canonical_url = normalize_document_url(_optional_text(record.get("URL")))
        if canonical_url is None:
            canonical_url = f"https://doi.org/{doi}"
        abstract = _crossref_abstract(record.get("abstract"))
        if abstract is not None and len(abstract) > 20_000:
            abstract = abstract[:20_000]
            warnings.append(f"ABSTRACT_TRUNCATED:{doi}")
        authors = _crossref_authors(record.get("author"))
        keywords = _crossref_subjects(record.get("subject"))
        published_year = _crossref_year(record.get("published"))
        if not _publication_year_allowed(
            published_year,
            publication_year_from=publication_year_from,
            publication_year_to=publication_year_to,
        ):
            warnings.append(f"PUBLICATION_YEAR_REJECTED:{doi}")
            continue
        document_id = document_id_for(
            provider="crossref",
            provider_record_id=doi,
            doi=doi,
            arxiv_id=None,
            canonical_url=canonical_url,
        )
        hit_id = deterministic_id(
            "hit",
            {
                "provider": "crossref",
                "provider_record_id": doi,
                "query_id": query.query_id,
                "raw_response_sha256": raw_response_artifact.sha256,
            },
        )
        hits.append(
            SearchHitV1(
                hit_id=hit_id,
                document_id=document_id,
                provider="crossref",
                provider_record_id=doi,
                query_ids=(query.query_id,),
                provider_rank=rank,
                title=title,
                authors=authors,
                published_year=published_year,
                doi=doi,
                canonical_url=canonical_url,
                abstract=abstract,
                keywords=keywords,
                raw_response_artifact=raw_response_artifact,
            )
        )
    return ParsedSearchPage(hits=tuple(hits), warnings=tuple(warnings))


def parse_multi_source_page(
    *,
    query: SearchQueryV1,
    payload: bytes,
    raw_response_artifact: ArtifactPointerV1,
    max_hits: int,
    publication_year_from: int | None = None,
    publication_year_to: int | None = None,
    provider_parsers: Mapping[str, Callable[..., ParsedSearchPage]] | None = None,
) -> ParsedSearchPage:
    """Parse a fail-open provider envelope with fair round-robin hit selection.

    ``max_hits`` remains an aggregate caller budget, but no early provider can
    consume it before later providers are inspected.  Parsers are selected from
    an explicit registry rather than a fixed provider branch, so additional
    audited providers can join without changing this envelope parser.
    """

    if type(max_hits) is not int or not 1 <= max_hits <= 10_000:
        raise SearchAdapterError("INVALID_QUERY", "max_hits must be between 1 and 10000")

    try:
        decoded = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise SearchAdapterError(
            "INVALID_JSON",
            "multi-source response is not UTF-8 JSON",
        ) from error
    if (
        not isinstance(decoded, dict)
        or decoded.get("schema_version")
        not in {
            "inspiration-multi-source-page-v1",
            "inspiration-multi-source-page-v2",
        }
        or not isinstance(decoded.get("pages"), list)
        or not decoded["pages"]
    ):
        raise SearchAdapterError(
            "SCHEMA_DRIFT",
            "multi-source response envelope is invalid",
        )
    parsers = dict(provider_parsers or _default_multi_source_parsers())
    if not parsers or any(not key or not callable(value) for key, value in parsers.items()):
        raise SearchAdapterError("INVALID_PARSER_REGISTRY", "provider parser registry is invalid")
    failures = decoded.get("failures", [])
    if not isinstance(failures, list):
        raise SearchAdapterError("SCHEMA_DRIFT", "multi-source failures must be an array")
    warnings: list[str] = []
    seen_providers: set[str] = set()
    for failure in failures:
        if not isinstance(failure, dict) or set(failure) != {
            "error_code",
            "http_status",
            "provider",
            "response_bytes",
        }:
            raise SearchAdapterError("SCHEMA_DRIFT", "multi-source failure is invalid")
        provider = failure["provider"]
        error_code = failure["error_code"]
        if (
            not isinstance(provider, str)
            or not provider
            or provider in seen_providers
            or not isinstance(error_code, str)
            or not error_code
        ):
            raise SearchAdapterError("SCHEMA_DRIFT", "multi-source failure identity is invalid")
        seen_providers.add(provider)
        warnings.append(f"PROVIDER_FAILED:{provider}:{error_code}")

    parsed_pages: list[tuple[str, ParsedSearchPage]] = []
    for page in decoded["pages"]:
        if not isinstance(page, dict) or set(page) != {
            "payload_base64",
            "payload_sha256",
            "provider",
        }:
            raise SearchAdapterError("SCHEMA_DRIFT", "multi-source page is invalid")
        provider = page["provider"]
        if not isinstance(provider, str) or not provider or provider in seen_providers:
            raise SearchAdapterError(
                "SCHEMA_DRIFT",
                "multi-source provider set is invalid or duplicated",
            )
        seen_providers.add(provider)
        try:
            child_payload = base64.b64decode(page["payload_base64"], validate=True)
        except (TypeError, ValueError) as error:
            raise SearchAdapterError(
                "SCHEMA_DRIFT",
                "multi-source payload encoding is invalid",
            ) from error
        if hashlib.sha256(child_payload).hexdigest() != page["payload_sha256"]:
            raise SearchAdapterError(
                "RAW_RESPONSE_HASH_MISMATCH",
                "multi-source child response hash does not match",
            )
        parser = parsers.get(provider)
        if parser is None:
            raise SearchAdapterError(
                "UNSUPPORTED_PROVIDER",
                f"no parser is registered for provider {provider!r}",
            )
        parsed = parser(
            query=query,
            payload=child_payload,
            raw_response_artifact=raw_response_artifact,
            max_hits=max_hits,
            publication_year_from=publication_year_from,
            publication_year_to=publication_year_to,
        )
        parsed_pages.append((provider, parsed))
        warnings.extend(parsed.warnings)

    hits: list[SearchHitV1] = []
    maximum_provider_hits = max((len(item.hits) for _, item in parsed_pages), default=0)
    for rank in range(maximum_provider_hits):
        for _provider, parsed in parsed_pages:
            if rank >= len(parsed.hits):
                continue
            hit = parsed.hits[rank]
            hits.append(hit)
            if len(hits) >= max_hits:
                return ParsedSearchPage(hits=tuple(hits), warnings=tuple(warnings))
    return ParsedSearchPage(hits=tuple(hits), warnings=tuple(warnings))


def _parse_openalex_multi_source_page(**kwargs: Any) -> ParsedSearchPage:
    return parse_openalex_page(provider="openalex", **kwargs)


def _default_multi_source_parsers() -> Mapping[str, Callable[..., ParsedSearchPage]]:
    from material_agent.inspiration.semantic_scholar import (
        parse_semantic_scholar_topic_page,
    )

    return {
        "arxiv": parse_arxiv_page,
        "crossref": parse_crossref_page,
        "openalex": _parse_openalex_multi_source_page,
        "osti": parse_osti_page,
        "semantic-scholar": parse_semantic_scholar_topic_page,
    }


def _publication_year_allowed(
    published_year: int | None,
    *,
    publication_year_from: int | None,
    publication_year_to: int | None,
) -> bool:
    if publication_year_from is None and publication_year_to is None:
        return True
    if published_year is None:
        return False
    if publication_year_from is not None and published_year < publication_year_from:
        return False
    return publication_year_to is None or published_year <= publication_year_to


def group_document_hits(hits: tuple[SearchHitV1, ...]) -> tuple[DocumentHitGroup, ...]:
    """Group run-local duplicate documents while retaining every hit ID."""

    by_document: dict[str, list[SearchHitV1]] = {}
    seen_hit_ids: set[str] = set()
    for hit in hits:
        if hit.hit_id in seen_hit_ids:
            raise SearchAdapterError(
                "DUPLICATE_HIT_ID",
                f"duplicate search hit ID {hit.hit_id!r}",
            )
        seen_hit_ids.add(hit.hit_id)
        by_document.setdefault(hit.document_id, []).append(hit)
    groups: list[DocumentHitGroup] = []
    for document_id, members in sorted(by_document.items()):
        ordered = sorted(
            members,
            key=lambda hit: (hit.provider_rank, hit.provider, hit.hit_id),
        )
        groups.append(
            DocumentHitGroup(
                document_id=document_id,
                representative_hit_id=ordered[0].hit_id,
                member_hit_ids=tuple(sorted(hit.hit_id for hit in members)),
            )
        )
    return tuple(groups)


def _required_text(record: Mapping[str, Any], field_name: str) -> str:
    value = record.get(field_name)
    if not isinstance(value, str) or not value.strip():
        raise SearchAdapterError(
            "SCHEMA_DRIFT",
            f"OpenAlex result has no non-empty {field_name}",
        )
    return value.strip()


def _optional_text(value: Any) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def _openalex_landing_url(record: Mapping[str, Any]) -> str | None:
    primary = record.get("primary_location")
    if not isinstance(primary, dict):
        return None
    return normalize_document_url(_optional_text(primary.get("landing_page_url")))


def _openalex_abstract(value: Any) -> str | None:
    if value is None:
        return None
    if not isinstance(value, dict):
        raise SearchAdapterError(
            "SCHEMA_DRIFT",
            "abstract_inverted_index is not an object",
        )
    positions: dict[int, str] = {}
    for token, raw_positions in value.items():
        if not isinstance(token, str) or not isinstance(raw_positions, list):
            raise SearchAdapterError(
                "SCHEMA_DRIFT",
                "abstract_inverted_index has invalid token positions",
            )
        for position in raw_positions:
            if not isinstance(position, int) or position < 0 or position > 100_000:
                raise SearchAdapterError(
                    "SCHEMA_DRIFT",
                    "abstract_inverted_index position is invalid",
                )
            existing = positions.get(position)
            if existing is not None and existing != token:
                raise SearchAdapterError(
                    "SCHEMA_DRIFT",
                    "abstract_inverted_index reuses a position",
                )
            positions[position] = token
    return " ".join(token for _, token in sorted(positions.items())) or None


def _openalex_authors(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list):
        raise SearchAdapterError("SCHEMA_DRIFT", "authorships is not an array")
    names: list[str] = []
    for authorship in value[:256]:
        if not isinstance(authorship, dict):
            continue
        author = authorship.get("author")
        if not isinstance(author, dict):
            continue
        name = _optional_text(author.get("display_name"))
        if name is not None and name not in names:
            names.append(name)
    return tuple(names)


def _openalex_keywords(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list):
        raise SearchAdapterError("SCHEMA_DRIFT", "keywords is not an array")
    keywords: list[str] = []
    for item in value[:128]:
        if not isinstance(item, dict):
            continue
        keyword = _optional_text(item.get("display_name"))
        if keyword is not None and keyword not in keywords:
            keywords.append(keyword)
    return tuple(keywords)


def _crossref_title(value: Any) -> str:
    if not isinstance(value, list):
        raise SearchAdapterError("SCHEMA_DRIFT", "Crossref title is not an array")
    title = next(
        (item.strip() for item in value if isinstance(item, str) and item.strip()),
        None,
    )
    if title is None:
        raise SearchAdapterError("SCHEMA_DRIFT", "Crossref item has no title")
    return title[:1_000]


class _TextOnlyHTMLParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.fragments: list[str] = []

    def handle_data(self, data: str) -> None:
        if data.strip():
            self.fragments.append(data)


def _crossref_abstract(value: Any) -> str | None:
    abstract = _optional_text(value)
    if abstract is None:
        return None
    parser = _TextOnlyHTMLParser()
    try:
        parser.feed(abstract)
        parser.close()
    except (TypeError, ValueError):
        return None
    normalized = " ".join(html.unescape(" ".join(parser.fragments)).split())
    return normalized or None


def _crossref_authors(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list):
        raise SearchAdapterError("SCHEMA_DRIFT", "Crossref author is not an array")
    authors: list[str] = []
    for item in value[:256]:
        if not isinstance(item, dict):
            continue
        name = " ".join(
            part.strip()
            for field in ("given", "family")
            if isinstance((part := item.get(field)), str) and part.strip()
        )
        if name and name not in authors:
            authors.append(name[:512])
    return tuple(authors)


def _crossref_subjects(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list):
        raise SearchAdapterError("SCHEMA_DRIFT", "Crossref subject is not an array")
    return tuple(
        dict.fromkeys(
            item.strip()[:512]
            for item in value[:128]
            if isinstance(item, str) and item.strip()
        )
    )


def _crossref_year(value: Any) -> int | None:
    if value is None:
        return None
    if not isinstance(value, dict):
        raise SearchAdapterError("SCHEMA_DRIFT", "Crossref published is not an object")
    date_parts = value.get("date-parts")
    if not isinstance(date_parts, list) or not date_parts:
        return None
    first = date_parts[0]
    if not isinstance(first, list) or not first:
        return None
    year = first[0]
    if type(year) is not int:
        raise SearchAdapterError("SCHEMA_DRIFT", "Crossref publication year is invalid")
    return year
