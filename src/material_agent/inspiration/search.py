"""Bounded metadata search primitives for the inspiration capability.

The offline adapter returns raw response bytes; callers must persist those bytes
before parsing them into :class:`SearchHitV1` objects.  This module intentionally
has no PDF or full-document interface.
"""

from __future__ import annotations

import hashlib
import html
import json
import math
import socket
import threading
import time
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import timezone
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Callable, Literal, Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit, urlunsplit
from urllib.request import Request, urlopen

from material_agent.inspiration.models import (
    ArtifactPointerV1,
    ComponentSnapshotV1,
    SearchHitV1,
    SearchQueryV1,
    deterministic_id,
)


_TRANSIENT_HTTP_STATUSES = frozenset({408, 429, 500, 502, 503, 504})
_MAX_RETRIES = 5
_MAX_RETRY_DELAY_SECONDS = 60.0
_MAX_TOTAL_WAIT_SECONDS = 120.0
_CROSSREF_PUBLIC_MIN_INTERVAL_SECONDS = 1.0
_CROSSREF_POLITE_MIN_INTERVAL_SECONDS = 1.0 / 3.0


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
    ) -> None:
        if type(response_bytes) is not int or not 0 <= response_bytes <= 10_000_001:
            raise ValueError("response_bytes must be between 0 and 10000001")
        self.code = code
        self.message = message
        self.http_status = http_status
        self.retry_after = retry_after
        self.response_bytes = response_bytes
        self.attempts = tuple(attempts)
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
        )


@dataclass(frozen=True)
class SearchAttemptRecord:
    """One deterministic provider attempt suitable for an audit Artifact."""

    query_id: str
    attempt_number: int
    outcome: Literal["success", "error"]
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
        if self.outcome not in {"success", "error"}:
            raise ValueError("outcome must be success or error")
        if self.outcome == "success" and self.error_code is not None:
            raise ValueError("successful attempts cannot have an error_code")
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

    def search(self, query: SearchQueryV1, *, max_response_bytes: int) -> RawSearchPage:
        """Return one bounded raw metadata response for ``query``."""


class BoundedHttpTransport(Protocol):
    """Small injectable boundary used by public metadata adapters."""

    def get(
        self,
        url: str,
        *,
        headers: Mapping[str, str],
        timeout_seconds: int,
        max_response_bytes: int,
    ) -> bytes:
        """Return a response only when it fits the declared byte budget."""


class UrlLibBoundedTransport:
    """HTTPS-only reader that checks redirects, media type, and byte limits."""

    allowed_hosts = frozenset({"api.crossref.org"})

    def get(
        self,
        url: str,
        *,
        headers: Mapping[str, str],
        timeout_seconds: int,
        max_response_bytes: int,
    ) -> bytes:
        if not 1 <= timeout_seconds <= 120:
            raise SearchAdapterError(
                "INVALID_TIMEOUT",
                "timeout_seconds must be between 1 and 120",
            )
        if not 1 <= max_response_bytes <= 10_000_000:
            raise SearchAdapterError(
                "INVALID_RESPONSE_BUDGET",
                "max_response_bytes must be between 1 and 10000000",
            )
        requested = urlsplit(url)
        if requested.scheme != "https" or requested.hostname not in self.allowed_hosts:
            raise SearchAdapterError(
                "UNTRUSTED_METADATA_ENDPOINT",
                "metadata requests must use an allowlisted HTTPS endpoint",
            )

        request = Request(url, headers=dict(headers), method="GET")
        try:
            with urlopen(request, timeout=timeout_seconds) as response:  # noqa: S310
                resolved = urlsplit(response.geturl())
                if (
                    resolved.scheme != "https"
                    or resolved.hostname not in self.allowed_hosts
                ):
                    raise SearchAdapterError(
                        "UNTRUSTED_METADATA_REDIRECT",
                        "metadata endpoint redirected outside the allowlist",
                    )
                media_type = response.headers.get_content_type().casefold()
                if media_type not in {"application/json", "application/vnd.api+json"}:
                    raise SearchAdapterError(
                        "UNEXPECTED_MEDIA_TYPE",
                        f"metadata endpoint returned {media_type!r}",
                    )
                content_length = response.headers.get("Content-Length")
                if content_length is not None:
                    try:
                        declared_length = int(content_length)
                    except ValueError as error:
                        raise SearchAdapterError(
                            "INVALID_CONTENT_LENGTH",
                            "metadata endpoint returned an invalid Content-Length",
                        ) from error
                    if declared_length > max_response_bytes:
                        raise SearchAdapterError(
                            "RESPONSE_BUDGET_EXCEEDED",
                            "metadata response exceeds its declared byte budget",
                        )
                payload = response.read(max_response_bytes + 1)
        except SearchAdapterError:
            raise
        except HTTPError as error:
            status = int(error.code)
            retry_after = None
            if error.headers is not None:
                candidate = error.headers.get("Retry-After")
                if isinstance(candidate, str) and len(candidate) <= 128:
                    retry_after = candidate
            raise SearchAdapterError(
                (
                    "TRANSIENT_HTTP_ERROR"
                    if status in _TRANSIENT_HTTP_STATUSES
                    else "HTTP_ERROR"
                ),
                f"metadata endpoint returned HTTP {status}",
                http_status=status,
                retry_after=retry_after,
            ) from error
        except (TimeoutError, socket.timeout, URLError, OSError) as error:
            raise SearchAdapterError(
                "NETWORK_ERROR",
                "metadata endpoint could not be read within the bounded request",
            ) from error

        if len(payload) > max_response_bytes:
            raise SearchAdapterError(
                "RESPONSE_BUDGET_EXCEEDED",
                "metadata response exceeded its byte budget while streaming",
                response_bytes=len(payload),
            )
        return payload


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
        self.max_results = max_results
        self.timeout_seconds = timeout_seconds
        self.contact_email = contact_email
        self.transport = transport or UrlLibBoundedTransport()
        self.max_retries = max_retries
        self.retry_backoff_seconds = float(retry_backoff_seconds)
        self.max_retry_delay_seconds = float(max_retry_delay_seconds)
        self.max_total_wait_seconds = float(max_total_wait_seconds)
        self.min_request_interval_seconds = float(min_request_interval_seconds)
        self.sleeper = sleeper
        self.wall_clock = wall_clock
        self.monotonic_clock = monotonic_clock
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
        )

    def search(self, query: SearchQueryV1, *, max_response_bytes: int) -> RawSearchPage:
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
        parameters = {
            "filter": "has-abstract:true",
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
        for attempt_number in range(1, self.max_retries + 2):
            try:
                pacing_delay = self._pace_request(
                    remaining_wait_seconds=(
                        self.max_total_wait_seconds - total_wait_seconds
                    )
                )
            except SearchAdapterError as error:
                raise error.with_attempts(tuple(attempts)) from error
            total_wait_seconds += pacing_delay
            try:
                payload = self.transport.get(
                    url,
                    headers=headers,
                    timeout_seconds=self.timeout_seconds,
                    max_response_bytes=max_response_bytes,
                )
                if not isinstance(payload, bytes):
                    raise SearchAdapterError(
                        "INVALID_NETWORK_PAYLOAD",
                        "metadata transport must return bytes",
                    )
                if len(payload) > max_response_bytes:
                    raise SearchAdapterError(
                        "RESPONSE_BUDGET_EXCEEDED",
                        "metadata transport returned more than the declared byte budget",
                        response_bytes=len(payload),
                    )
            except SearchAdapterError as error:
                can_retry = (
                    attempt_number <= self.max_retries
                    and _is_transient_search_error(error)
                )
                retry_delay = 0.0
                if can_retry:
                    retry_delay = self._retry_delay_seconds(
                        error,
                        retry_number=attempt_number,
                    )
                    remaining_wait = self.max_total_wait_seconds - total_wait_seconds
                    if retry_delay > remaining_wait:
                        can_retry = False
                        retry_delay = 0.0
                attempts.append(
                    SearchAttemptRecord(
                        query_id=query.query_id,
                        attempt_number=attempt_number,
                        outcome="error",
                        error_code=error.code,
                        http_status=error.http_status,
                        retry_delay_seconds=retry_delay,
                        pacing_delay_seconds=pacing_delay,
                        response_bytes=error.response_bytes,
                    )
                )
                if not can_retry:
                    raise error.with_attempts(tuple(attempts)) from error
                if retry_delay:
                    self.sleeper(retry_delay)
                    total_wait_seconds += retry_delay
                continue

            attempts.append(
                SearchAttemptRecord(
                    query_id=query.query_id,
                    attempt_number=attempt_number,
                    outcome="success",
                    error_code=None,
                    http_status=200,
                    retry_delay_seconds=0.0,
                    pacing_delay_seconds=pacing_delay,
                    response_bytes=len(payload),
                )
            )
            return RawSearchPage(
                provider="crossref",
                query_id=query.query_id,
                payload=payload,
                attempts=tuple(attempts),
            )
        raise AssertionError("finite Crossref retry loop did not return or raise")

    def _pace_request(self, *, remaining_wait_seconds: float) -> float:
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

    def search(self, query: SearchQueryV1, *, max_response_bytes: int) -> RawSearchPage:
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


def parse_openalex_page(
    *,
    query: SearchQueryV1,
    payload: bytes,
    raw_response_artifact: ArtifactPointerV1,
    provider: str = "openalex",
    max_hits: int,
) -> ParsedSearchPage:
    """Parse bounded OpenAlex-style JSON metadata without fetching any work page."""

    if max_hits < 1:
        raise SearchAdapterError("INVALID_HIT_BUDGET", "max_hits must be positive")
    try:
        decoded = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise SearchAdapterError("INVALID_JSON", "search response is not UTF-8 JSON") from error
    if not isinstance(decoded, dict) or not isinstance(decoded.get("results"), list):
        raise SearchAdapterError(
            "SCHEMA_DRIFT",
            "OpenAlex response must contain a results array",
        )

    raw_results = decoded["results"][:max_hits]
    hits: list[SearchHitV1] = []
    warnings: list[str] = []
    for rank, record in enumerate(raw_results, start=1):
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
) -> ParsedSearchPage:
    """Parse a bounded Crossref v1 response without following full-text links."""

    if max_hits < 1:
        raise SearchAdapterError("INVALID_HIT_BUDGET", "max_hits must be positive")
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
    for rank, record in enumerate(message["items"][:max_hits], start=1):
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
