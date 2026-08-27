"""Bounded OpenCitations Index + Meta citation-graph adapter."""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import time
from collections.abc import Callable, Mapping
from enum import StrEnum
from pathlib import Path
from typing import Any, Literal
from urllib.parse import quote

from pydantic import Field, model_validator

from material_agent.inspiration.models import (
    ArtifactPointerV1,
    ComponentSnapshotV1,
    SearchHitV1,
    StrictModel,
    canonical_json_bytes,
    deterministic_id,
)
from material_agent.inspiration.search import (
    BoundedHttpResult,
    BoundedHttpTransport,
    ParsedSearchPage,
    PhysicalSearchHop,
    RawSearchPage,
    SearchAdapterError,
    SearchAttemptRecord,
    UrlLibBoundedTransport,
    document_id_for,
    normalize_doi,
)

OPENCITATIONS_REQUEST_VERSION = "opencitations-graph-request-v1"
OPENCITATIONS_ENVELOPE_VERSION = "opencitations-graph-envelope-v1"
OPENCITATIONS_ACCESS_TOKEN_ENV = "OPENCITATIONS_ACCESS_TOKEN"
_OPENCITATIONS_HOST = "api.opencitations.net"
_DOI_TOKEN = re.compile(r"(?:^|\s)doi:([^\s]+)", re.IGNORECASE)
_AUTHOR_IDENTIFIERS = re.compile(r"\s*\[[^\]]*\]\s*$")


class OpenCitationsRoute(StrEnum):
    REFERENCES = "REFERENCES"
    CITATIONS = "CITATIONS"


class OpenCitationsRequestV1(StrictModel):
    schema_version: Literal["opencitations-graph-request-v1"] = (
        OPENCITATIONS_REQUEST_VERSION
    )
    query_id: str = Field(min_length=1, max_length=128)
    route: OpenCitationsRoute
    anchor_doi: str = Field(min_length=6, max_length=256)
    max_results: int = Field(ge=1, le=20)
    publication_year_from: int | None = Field(default=None, ge=1600, le=2200)
    publication_year_to: int | None = Field(default=None, ge=1600, le=2200)

    @model_validator(mode="after")
    def validate_request(self) -> OpenCitationsRequestV1:
        normalized = normalize_doi(self.anchor_doi)
        if normalized is None or normalized != self.anchor_doi:
            raise ValueError("anchor_doi must be a normalized DOI")
        if (
            self.publication_year_from is not None
            and self.publication_year_to is not None
            and self.publication_year_from > self.publication_year_to
        ):
            raise ValueError("publication year range is reversed")
        return self


class OpenCitationsPublicAdapter:
    """Resolve citation edges and their metadata in two bounded HTTP requests."""

    network_access = True
    provider_id = "opencitations"

    def __init__(
        self,
        *,
        timeout_seconds: int = 20,
        access_token_resolver: Callable[[], str] | None = None,
        transport: BoundedHttpTransport | None = None,
        monotonic_clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if type(timeout_seconds) is not int or not 1 <= timeout_seconds <= 120:
            raise ValueError("timeout_seconds must be between 1 and 120")
        if access_token_resolver is not None and not callable(access_token_resolver):
            raise TypeError("access_token_resolver must be callable")
        if not callable(monotonic_clock):
            raise TypeError("monotonic_clock must be callable")
        self.timeout_seconds = timeout_seconds
        self.access_token_resolver = access_token_resolver or (
            lambda: os.environ.get(OPENCITATIONS_ACCESS_TOKEN_ENV, "")
        )
        self.transport = transport or UrlLibBoundedTransport(
            monotonic_clock=monotonic_clock,
            allowed_hosts=frozenset({_OPENCITATIONS_HOST}),
        )
        self.monotonic_clock = monotonic_clock
        self.component = ComponentSnapshotV1(
            component_id="opencitations-graph-public-adapter",
            version="index-v2-meta-v1",
            implementation_sha256=hashlib.sha256(
                canonical_json_bytes(
                    {
                        "source_sha256": hashlib.sha256(
                            Path(__file__).read_bytes()
                        ).hexdigest(),
                        "timeout_seconds": timeout_seconds,
                    }
                )
            ).hexdigest(),
        )

    def search(
        self,
        request: OpenCitationsRequestV1,
        *,
        max_response_bytes: int,
        remaining_walltime_seconds: float | None = None,
        max_physical_requests: int | None = None,
    ) -> RawSearchPage:
        if not isinstance(request, OpenCitationsRequestV1):
            raise SearchAdapterError(
                "INVALID_QUERY", "request must be OpenCitationsRequestV1"
            )
        if not 1 <= max_response_bytes <= 10_000_000:
            raise SearchAdapterError(
                "INVALID_RESPONSE_BUDGET",
                "max_response_bytes must be between 1 and 10000000",
            )
        if max_physical_requests is not None and max_physical_requests < 1:
            raise SearchAdapterError(
                "SEARCH_REQUEST_BUDGET_EXCEEDED",
                "no physical OpenCitations request remains",
            )
        deadline = (
            None
            if remaining_walltime_seconds is None
            else self.monotonic_clock() + remaining_walltime_seconds
        )
        headers = self._headers()
        route = request.route.value.casefold()
        anchor = quote(f"doi:{request.anchor_doi}", safe=":/")
        index_url = f"https://{_OPENCITATIONS_HOST}/index/v2/{route}/{anchor}"
        index_payload, index_hops = self._get(
            index_url,
            headers=headers,
            max_response_bytes=max_response_bytes,
            deadline=deadline,
            max_physical_requests=max_physical_requests,
        )
        target_dois = _target_dois(index_payload, request)[: request.max_results]
        metadata_payload = b"[]"
        metadata_hops: tuple[PhysicalSearchHop, ...] = ()
        if target_dois:
            remaining_requests = (
                None
                if max_physical_requests is None
                else max_physical_requests - len(index_hops)
            )
            if remaining_requests is not None and remaining_requests < 1:
                raise SearchAdapterError(
                    "SEARCH_REQUEST_BUDGET_EXCEEDED",
                    "OpenCitations metadata resolution requires a second request",
                    attempts=_attempts(request.query_id, index_hops),
                )
            identifiers = "__".join(
                quote(f"doi:{doi}", safe=":/") for doi in target_dois
            )
            metadata_url = (
                f"https://{_OPENCITATIONS_HOST}/meta/v1/metadata/{identifiers}"
            )
            metadata_payload, metadata_hops = self._get(
                metadata_url,
                headers=headers,
                max_response_bytes=max_response_bytes,
                deadline=deadline,
                max_physical_requests=remaining_requests,
            )
        envelope = canonical_json_bytes(
            {
                "schema_version": OPENCITATIONS_ENVELOPE_VERSION,
                "route": request.route,
                "anchor_doi": request.anchor_doi,
                "target_dois": target_dois,
                "index_payload_base64": base64.b64encode(index_payload).decode("ascii"),
                "metadata_payload_base64": base64.b64encode(metadata_payload).decode(
                    "ascii"
                ),
            }
        )
        if len(envelope) > max_response_bytes:
            raise SearchAdapterError(
                "RESPONSE_BUDGET_EXCEEDED",
                "combined OpenCitations response exceeded the byte budget",
                response_bytes=len(envelope),
                attempts=_attempts(request.query_id, (*index_hops, *metadata_hops)),
            )
        return RawSearchPage(
            provider="opencitations",
            query_id=request.query_id,
            payload=envelope,
            attempts=_attempts(request.query_id, (*index_hops, *metadata_hops)),
        )

    def _headers(self) -> dict[str, str]:
        headers = {
            "Accept": "application/json",
            "Accept-Encoding": "identity",
            "User-Agent": "materials-screening-agent/0.1 (citation-metadata-only)",
        }
        try:
            value = self.access_token_resolver()
        except (OSError, RuntimeError, ValueError) as exc:
            raise SearchAdapterError(
                "OPENCITATIONS_CREDENTIAL_ERROR",
                "OpenCitations credential source could not be read",
            ) from exc
        token = value.strip() if isinstance(value, str) else ""
        if token:
            if len(token) > 512 or any(ord(character) < 33 for character in token):
                raise SearchAdapterError(
                    "OPENCITATIONS_CREDENTIAL_ERROR",
                    "OpenCitations access token is malformed",
                )
            headers["authorization"] = token
        return headers

    def _get(
        self,
        url: str,
        *,
        headers: Mapping[str, str],
        max_response_bytes: int,
        deadline: float | None,
        max_physical_requests: int | None,
    ) -> tuple[bytes, tuple[PhysicalSearchHop, ...]]:
        result = self.transport.get(
            url,
            headers=headers,
            timeout_seconds=float(self.timeout_seconds),
            max_response_bytes=max_response_bytes,
            deadline_monotonic=deadline,
            max_physical_requests=max_physical_requests,
        )
        if isinstance(result, bytes):
            return result, (
                PhysicalSearchHop(
                    outcome="success",
                    error_code=None,
                    http_status=200,
                    response_bytes=len(result),
                ),
            )
        if isinstance(result, BoundedHttpResult):
            return result.payload, result.physical_hops
        raise SearchAdapterError(
            "INVALID_NETWORK_PAYLOAD",
            "OpenCitations transport must return bytes or BoundedHttpResult",
        )


def parse_opencitations_page(
    *,
    request: OpenCitationsRequestV1,
    payload: bytes,
    raw_response_artifact: ArtifactPointerV1,
) -> ParsedSearchPage:
    try:
        envelope = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SearchAdapterError(
            "INVALID_JSON", "OpenCitations envelope is not valid UTF-8 JSON"
        ) from exc
    if not isinstance(envelope, dict) or envelope.get("schema_version") != (
        OPENCITATIONS_ENVELOPE_VERSION
    ):
        raise SearchAdapterError("SCHEMA_DRIFT", "invalid OpenCitations envelope")
    if envelope.get("route") != request.route or envelope.get("anchor_doi") != (
        request.anchor_doi
    ):
        raise SearchAdapterError(
            "PROVENANCE_MISMATCH", "OpenCitations envelope request identity differs"
        )
    target_dois = envelope.get("target_dois")
    if not isinstance(target_dois, list) or any(
        not isinstance(item, str) for item in target_dois
    ):
        raise SearchAdapterError("SCHEMA_DRIFT", "target_dois must be an array")
    metadata = _decode_embedded_json(envelope, "metadata_payload_base64")
    if not isinstance(metadata, list):
        raise SearchAdapterError("SCHEMA_DRIFT", "metadata response must be an array")
    by_doi: dict[str, Mapping[str, Any]] = {}
    for item in metadata:
        if not isinstance(item, Mapping):
            raise SearchAdapterError("SCHEMA_DRIFT", "metadata row must be an object")
        for doi in _identifier_dois(item.get("id")):
            by_doi.setdefault(doi, item)
    hits: list[SearchHitV1] = []
    warnings: list[str] = []
    for doi in target_dois[: request.max_results]:
        record = by_doi.get(doi)
        if record is None:
            warnings.append(f"OPENCITATIONS_METADATA_UNRESOLVED:{doi}")
            continue
        title = record.get("title")
        if not isinstance(title, str) or not title.strip():
            warnings.append(f"OPENCITATIONS_TITLE_UNRESOLVED:{doi}")
            continue
        year = _publication_year(record.get("pub_date"))
        if not _year_allowed(year, request):
            warnings.append(f"OPENCITATIONS_YEAR_FILTERED:{doi}")
            continue
        authors = _authors(record.get("author"))
        canonical_url = f"https://doi.org/{doi}"
        document_id = document_id_for(
            provider="opencitations",
            provider_record_id=doi,
            doi=doi,
            arxiv_id=None,
            canonical_url=canonical_url,
        )
        hits.append(
            SearchHitV1(
                hit_id=deterministic_id(
                    "hit",
                    {
                        "provider": "opencitations",
                        "provider_record_id": doi,
                        "query_id": request.query_id,
                        "provider_rank": len(hits) + 1,
                    },
                ),
                document_id=document_id,
                provider="opencitations",
                provider_record_id=doi,
                query_ids=(request.query_id,),
                provider_rank=len(hits) + 1,
                title=title.strip()[:1_000],
                authors=authors,
                published_year=year,
                doi=doi,
                canonical_url=canonical_url,
                keywords=tuple(
                    value.strip()[:256]
                    for key in ("type", "venue")
                    if isinstance((value := record.get(key)), str) and value.strip()
                ),
                raw_response_artifact=raw_response_artifact,
            )
        )
    return ParsedSearchPage(hits=tuple(hits), warnings=tuple(warnings))


def _target_dois(payload: bytes, request: OpenCitationsRequestV1) -> list[str]:
    try:
        rows = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SearchAdapterError(
            "INVALID_JSON", "OpenCitations Index response is not valid UTF-8 JSON"
        ) from exc
    if not isinstance(rows, list):
        raise SearchAdapterError("SCHEMA_DRIFT", "citation response must be an array")
    field = "cited" if request.route is OpenCitationsRoute.REFERENCES else "citing"
    values: list[str] = []
    for row in rows:
        if not isinstance(row, Mapping):
            raise SearchAdapterError("SCHEMA_DRIFT", "citation row must be an object")
        raw = row.get(field)
        if not isinstance(raw, str):
            raise SearchAdapterError(
                "SCHEMA_DRIFT", f"citation row requires a {field} identifier string"
            )
        for doi in _identifier_dois(raw):
            if doi != request.anchor_doi and doi not in values:
                values.append(doi)
    return values


def _identifier_dois(value: object) -> tuple[str, ...]:
    if not isinstance(value, str):
        return ()
    values: list[str] = []
    for match in _DOI_TOKEN.finditer(value):
        doi = normalize_doi(match.group(1))
        if doi is not None and doi not in values:
            values.append(doi)
    return tuple(values)


def _decode_embedded_json(envelope: Mapping[str, Any], field: str) -> Any:
    value = envelope.get(field)
    if not isinstance(value, str):
        raise SearchAdapterError("SCHEMA_DRIFT", f"{field} must be base64 text")
    try:
        return json.loads(base64.b64decode(value, validate=True).decode("utf-8"))
    except (ValueError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SearchAdapterError("INVALID_JSON", f"{field} is invalid") from exc


def _publication_year(value: object) -> int | None:
    if value in (None, ""):
        return None
    if not isinstance(value, str) or len(value) < 4 or not value[:4].isdigit():
        raise SearchAdapterError("SCHEMA_DRIFT", "pub_date is invalid")
    year = int(value[:4])
    if not 1600 <= year <= 2200:
        raise SearchAdapterError("SCHEMA_DRIFT", "pub_date year is invalid")
    return year


def _year_allowed(year: int | None, request: OpenCitationsRequestV1) -> bool:
    if year is None:
        return True
    return not (
        request.publication_year_from is not None
        and year < request.publication_year_from
        or request.publication_year_to is not None
        and year > request.publication_year_to
    )


def _authors(value: object) -> tuple[str, ...]:
    if not isinstance(value, str) or not value.strip():
        return ()
    return tuple(
        dict.fromkeys(
            cleaned[:256]
            for item in value.split(";")
            if (cleaned := _AUTHOR_IDENTIFIERS.sub("", item).strip())
        )
    )


def _attempts(
    query_id: str, hops: tuple[PhysicalSearchHop, ...]
) -> tuple[SearchAttemptRecord, ...]:
    return tuple(
        SearchAttemptRecord(
            query_id=query_id,
            attempt_number=index,
            outcome=hop.outcome,
            error_code=hop.error_code,
            http_status=hop.http_status,
            retry_delay_seconds=0.0,
            pacing_delay_seconds=0.0,
            response_bytes=hop.response_bytes,
        )
        for index, hop in enumerate(hops, start=1)
    )
