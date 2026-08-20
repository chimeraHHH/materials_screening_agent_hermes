"""Bounded Semantic Scholar topic and citation-graph retrieval.

This adapter implements the AutoSci-inspired acquisition routes without
introducing a second orchestrator.  It returns raw metadata bytes for the
existing artifact-first evidence path and supports topic search, semantic
recommendations, backward references, and forward citations.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import time
from collections.abc import Callable, Mapping
from enum import StrEnum
from pathlib import Path
from typing import Annotated, Any, Literal
from urllib.parse import quote, urlencode

from pydantic import Field, model_validator

from material_agent.inspiration.models import (
    ArtifactPointerV1,
    ComponentSnapshotV1,
    Identifier,
    SearchHitV1,
    SearchQueryV1,
    ShortText,
    StrictModel,
    canonical_json_bytes,
    deterministic_id,
)
from material_agent.inspiration.query_context import (
    AnchorPolarity,
    QueryContextV2,
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

SEMANTIC_SCHOLAR_REQUEST_VERSION = "semantic-scholar-request-v1"
SEMANTIC_SCHOLAR_API_KEY_ENV = "SEMANTIC_SCHOLAR_API_KEY"
_SEMANTIC_SCHOLAR_HOST = "api.semanticscholar.org"
_PAPER_FIELDS = (
    "paperId,externalIds,url,title,abstract,venue,year,authors,"
    "fieldsOfStudy,citationCount,referenceCount"
)


class SemanticScholarRoute(StrEnum):
    TOPIC = "TOPIC"
    RECOMMENDATIONS = "RECOMMENDATIONS"
    REFERENCES = "REFERENCES"
    CITATIONS = "CITATIONS"


class SemanticScholarRequestV1(StrictModel):
    """One replayable logical request to an official metadata-only endpoint."""

    schema_version: Literal["semantic-scholar-request-v1"] = (
        SEMANTIC_SCHOLAR_REQUEST_VERSION
    )
    query_id: Identifier
    context_id: Identifier
    route: SemanticScholarRoute
    query_text: Annotated[str, Field(min_length=3, max_length=512)] | None = None
    anchor_paper_id: ShortText | None = None
    anchor_polarity: AnchorPolarity | None = None
    max_results: Annotated[int, Field(ge=1, le=100)] = 10
    publication_year_from: Annotated[int, Field(ge=1600, le=2200)] | None = None
    publication_year_to: Annotated[int, Field(ge=1600, le=2200)] | None = None

    @model_validator(mode="after")
    def validate_route(self) -> SemanticScholarRequestV1:
        if self.route is SemanticScholarRoute.TOPIC:
            if self.query_text is None:
                raise ValueError("topic requests require query_text")
            if self.anchor_paper_id is not None or self.anchor_polarity is not None:
                raise ValueError("topic requests cannot reference an anchor")
        else:
            if self.query_text is not None:
                raise ValueError("anchor requests cannot contain query_text")
            if self.anchor_paper_id is None or self.anchor_polarity is None:
                raise ValueError("anchor requests require paper ID and polarity")
            if (
                self.anchor_polarity is AnchorPolarity.NEGATIVE
                and self.route is not SemanticScholarRoute.RECOMMENDATIONS
            ):
                raise ValueError("negative anchors support recommendations only")
        if (
            self.publication_year_from is not None
            and self.publication_year_to is not None
            and self.publication_year_from > self.publication_year_to
        ):
            raise ValueError("publication year range is reversed")
        expected = deterministic_id(
            "s2-query",
            self.model_dump(mode="json", exclude={"query_id"}),
        )
        if self.query_id != expected:
            raise ValueError("Semantic Scholar query ID does not match canonical content")
        return self


def compile_semantic_scholar_requests(
    context: QueryContextV2,
    *,
    max_results_per_request: int = 10,
    max_requests: int = 16,
) -> tuple[SemanticScholarRequestV1, ...]:
    """Build bounded topic and anchor routes from a frozen QueryContextV2."""

    if not isinstance(context, QueryContextV2):
        raise TypeError("context must be QueryContextV2")
    if type(max_results_per_request) is not int or not 1 <= max_results_per_request <= 100:
        raise ValueError("max_results_per_request must be between 1 and 100")
    if type(max_requests) is not int or not 1 <= max_requests <= 64:
        raise ValueError("max_requests must be between 1 and 64")

    payloads: list[dict[str, Any]] = []
    for material in context.materials:
        terms = [
            *material.query_terms[:3],
            *context.mechanism_terms[:2],
            *context.operation_terms[:1],
        ]
        query_text = _bounded_unique_join(terms, maximum=512)
        payloads.append(
            {
                "schema_version": SEMANTIC_SCHOLAR_REQUEST_VERSION,
                "context_id": context.context_id,
                "route": SemanticScholarRoute.TOPIC,
                "query_text": query_text,
                "anchor_paper_id": None,
                "anchor_polarity": None,
                "max_results": max_results_per_request,
                "publication_year_from": context.publication_year_from,
                "publication_year_to": context.publication_year_to,
            }
        )

    for anchor in (*context.positive_anchors, *context.negative_anchors):
        routes = (
            (
                SemanticScholarRoute.RECOMMENDATIONS,
                SemanticScholarRoute.REFERENCES,
                SemanticScholarRoute.CITATIONS,
            )
            if anchor.polarity is AnchorPolarity.POSITIVE
            else (SemanticScholarRoute.RECOMMENDATIONS,)
        )
        for route in routes:
            payloads.append(
                {
                    "schema_version": SEMANTIC_SCHOLAR_REQUEST_VERSION,
                    "context_id": context.context_id,
                    "route": route,
                    "query_text": None,
                    "anchor_paper_id": _semantic_scholar_paper_id(
                        anchor.provider_record_id
                    ),
                    "anchor_polarity": anchor.polarity,
                    "max_results": max_results_per_request,
                    "publication_year_from": context.publication_year_from,
                    "publication_year_to": context.publication_year_to,
                }
            )

    requests = tuple(
        SemanticScholarRequestV1(
            query_id=deterministic_id("s2-query", payload),
            **payload,
        )
        for payload in payloads[:max_requests]
    )
    if not requests:
        raise ValueError("query context produced no Semantic Scholar requests")
    return requests


class SemanticScholarPublicAdapter:
    """One-call official API adapter with lazy optional credentials."""

    network_access = True

    def __init__(
        self,
        *,
        timeout_seconds: int = 20,
        api_key_resolver: Callable[[], str] | None = None,
        transport: BoundedHttpTransport | None = None,
        monotonic_clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if type(timeout_seconds) is not int or not 1 <= timeout_seconds <= 120:
            raise ValueError("timeout_seconds must be between 1 and 120")
        if api_key_resolver is not None and not callable(api_key_resolver):
            raise ValueError("api_key_resolver must be callable")
        if not callable(monotonic_clock):
            raise ValueError("monotonic_clock must be callable")
        self.timeout_seconds = timeout_seconds
        self.api_key_resolver = api_key_resolver or (
            lambda: os.environ.get(SEMANTIC_SCHOLAR_API_KEY_ENV, "")
        )
        self.transport = transport or UrlLibBoundedTransport(
            monotonic_clock=monotonic_clock,
            allowed_hosts=frozenset({_SEMANTIC_SCHOLAR_HOST}),
        )
        self.monotonic_clock = monotonic_clock
        fingerprint = canonical_json_bytes(
            {
                "source_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                "timeout_seconds": timeout_seconds,
            }
        )
        self.component = ComponentSnapshotV1(
            component_id="semantic-scholar-public-adapter",
            version="graph-recommendations-v1",
            implementation_sha256=hashlib.sha256(fingerprint).hexdigest(),
        )

    def search(
        self,
        request: SemanticScholarRequestV1,
        *,
        max_response_bytes: int,
        remaining_walltime_seconds: float | None = None,
        max_physical_requests: int | None = None,
    ) -> RawSearchPage:
        if not isinstance(request, SemanticScholarRequestV1):
            raise SearchAdapterError(
                "INVALID_QUERY", "request must be SemanticScholarRequestV1"
            )
        if type(max_response_bytes) is not int or not 1 <= max_response_bytes <= 10_000_000:
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
                "no physical Semantic Scholar request remains",
            )
        deadline = _deadline_from_remaining(
            remaining_walltime_seconds,
            monotonic_clock=self.monotonic_clock,
        )
        headers = {
            "Accept": "application/json",
            "Accept-Encoding": "identity",
            "User-Agent": "materials-screening-agent/0.1 (metadata-only)",
        }
        try:
            resolved_key = self.api_key_resolver()
        except (OSError, RuntimeError, ValueError) as exc:
            raise SearchAdapterError(
                "SEMANTIC_SCHOLAR_CREDENTIAL_ERROR",
                "Semantic Scholar credential source could not be read",
            ) from exc
        api_key = resolved_key.strip() if isinstance(resolved_key, str) else ""
        if api_key:
            if len(api_key) > 512 or any(ord(character) < 33 for character in api_key):
                raise SearchAdapterError(
                    "SEMANTIC_SCHOLAR_CREDENTIAL_ERROR",
                    "Semantic Scholar credential is malformed",
                )
            headers["x-api-key"] = api_key

        url = _request_url(request)
        try:
            result = self.transport.get(
                url,
                headers=headers,
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
            raise error.with_attempts(
                _attempts_from_hops(request.query_id, hops)
            ) from error
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
                "Semantic Scholar response exceeded the byte budget",
                response_bytes=len(payload),
                attempts=_attempts_from_hops(request.query_id, hops),
            )
        return RawSearchPage(
            provider="semantic-scholar",
            query_id=request.query_id,
            payload=payload,
            attempts=_attempts_from_hops(request.query_id, hops),
        )


class SemanticScholarTopicSearchAdapter:
    """Adapt the existing four-route client to the common topic-search boundary."""

    network_access = True
    provider_id = "semantic-scholar"

    def __init__(
        self,
        *,
        max_results: int = 5,
        publication_year_from: int | None = None,
        publication_year_to: int | None = None,
        api_key_resolver: Callable[[], str] | None = None,
        transport: BoundedHttpTransport | None = None,
    ) -> None:
        if not 1 <= max_results <= 100:
            raise ValueError("max_results must be between 1 and 100")
        self.max_results = max_results
        self.publication_year_from = publication_year_from
        self.publication_year_to = publication_year_to
        self.provider = SemanticScholarPublicAdapter(
            api_key_resolver=api_key_resolver,
            transport=transport,
        )
        self.component = ComponentSnapshotV1(
            component_id="semantic-scholar-topic-public-adapter",
            version="topic-v1",
            implementation_sha256=hashlib.sha256(
                canonical_json_bytes(
                    {
                        "child": self.provider.component.implementation_sha256,
                        "max_results": max_results,
                        "publication_year_from": publication_year_from,
                        "publication_year_to": publication_year_to,
                        "source_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                    }
                )
            ).hexdigest(),
        )

    def search(
        self,
        query: SearchQueryV1,
        *,
        max_response_bytes: int,
        remaining_walltime_seconds: float | None = None,
        max_physical_requests: int | None = None,
    ) -> RawSearchPage:
        request = semantic_scholar_topic_request(
            query,
            max_results=self.max_results,
            publication_year_from=self.publication_year_from,
            publication_year_to=self.publication_year_to,
        )
        return self.provider.search(
            request,
            max_response_bytes=max_response_bytes,
            remaining_walltime_seconds=remaining_walltime_seconds,
            max_physical_requests=max_physical_requests,
        )


def semantic_scholar_topic_request(
    query: SearchQueryV1,
    *,
    max_results: int,
    publication_year_from: int | None,
    publication_year_to: int | None,
) -> SemanticScholarRequestV1:
    if not isinstance(query, SearchQueryV1):
        raise SearchAdapterError("INVALID_QUERY", "query must be SearchQueryV1")
    payload = {
        "schema_version": SEMANTIC_SCHOLAR_REQUEST_VERSION,
        "context_id": deterministic_id(
            "s2-context", {"search_query_id": query.query_id}
        ),
        "route": SemanticScholarRoute.TOPIC,
        "query_text": query.text,
        "anchor_paper_id": None,
        "anchor_polarity": None,
        "max_results": max_results,
        "publication_year_from": publication_year_from,
        "publication_year_to": publication_year_to,
    }
    return SemanticScholarRequestV1(
        query_id=deterministic_id("s2-query", payload),
        **payload,
    )


def parse_semantic_scholar_topic_page(
    *,
    query: SearchQueryV1,
    payload: bytes,
    raw_response_artifact: ArtifactPointerV1,
    max_hits: int,
    publication_year_from: int | None = None,
    publication_year_to: int | None = None,
) -> ParsedSearchPage:
    request = semantic_scholar_topic_request(
        query,
        max_results=max_hits,
        publication_year_from=publication_year_from,
        publication_year_to=publication_year_to,
    )
    parsed = parse_semantic_scholar_page(
        request=request,
        payload=payload,
        raw_response_artifact=raw_response_artifact,
    )
    hits = tuple(
        hit.model_copy(
            update={
                "hit_id": deterministic_id(
                    "hit",
                    {
                        "provider": hit.provider,
                        "provider_record_id": hit.provider_record_id,
                        "query_id": query.query_id,
                        "provider_rank": hit.provider_rank,
                    },
                ),
                "query_ids": (query.query_id,),
            }
        )
        for hit in parsed.hits
    )
    return ParsedSearchPage(hits=hits, warnings=parsed.warnings)


def parse_semantic_scholar_page(
    *,
    request: SemanticScholarRequestV1,
    payload: bytes,
    raw_response_artifact: ArtifactPointerV1,
) -> ParsedSearchPage:
    """Parse one route response and locally recheck its requested year range."""

    try:
        decoded = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SearchAdapterError(
            "INVALID_JSON", "Semantic Scholar response is not valid UTF-8 JSON"
        ) from exc
    if not isinstance(decoded, Mapping):
        raise SearchAdapterError("SCHEMA_DRIFT", "response root must be an object")
    raw_records = _records_for_route(decoded, request.route)
    hits: list[SearchHitV1] = []
    warnings: list[str] = []
    for provider_rank, raw_record in enumerate(raw_records, start=1):
        if len(hits) >= request.max_results:
            break
        if not isinstance(raw_record, Mapping):
            raise SearchAdapterError("SCHEMA_DRIFT", "paper record must be an object")
        record = _paper_for_route(raw_record, request.route)
        if record is None:
            warnings.append(f"SEMANTIC_SCHOLAR_NULL_PAPER:{provider_rank}")
            continue
        paper_id = _required_string(record, "paperId")
        title = _required_string(record, "title", maximum=1_000)
        year = record.get("year")
        if year is not None and (type(year) is not int or not 1600 <= year <= 2200):
            raise SearchAdapterError("SCHEMA_DRIFT", "paper year is invalid")
        if not _year_allowed(year, request):
            warnings.append(f"SEMANTIC_SCHOLAR_YEAR_FILTERED:{paper_id}")
            continue
        external = record.get("externalIds") or {}
        if not isinstance(external, Mapping):
            raise SearchAdapterError("SCHEMA_DRIFT", "externalIds must be an object")
        doi = normalize_doi(_optional_string(external.get("DOI")))
        arxiv_id = _optional_string(external.get("ArXiv"))
        url = _optional_string(record.get("url"))
        if url is not None and not url.startswith(("https://", "http://")):
            raise SearchAdapterError("SCHEMA_DRIFT", "paper URL must be HTTP(S)")
        abstract = _optional_string(record.get("abstract"))
        if abstract is not None and len(abstract) > 20_000:
            raise SearchAdapterError("SCHEMA_DRIFT", "paper abstract is too long")
        authors_raw = record.get("authors") or []
        if not isinstance(authors_raw, list):
            raise SearchAdapterError("SCHEMA_DRIFT", "paper authors must be an array")
        authors = tuple(
            dict.fromkeys(
                name
                for item in authors_raw
                if isinstance(item, Mapping)
                and (name := _optional_string(item.get("name"))) is not None
            )
        )
        fields = record.get("fieldsOfStudy") or []
        if not isinstance(fields, list) or any(not isinstance(item, str) for item in fields):
            raise SearchAdapterError(
                "SCHEMA_DRIFT", "fieldsOfStudy must be an array of strings"
            )
        keywords = tuple(dict.fromkeys(item.strip() for item in fields if item.strip()))
        document_id = document_id_for(
            provider="semantic-scholar",
            provider_record_id=paper_id,
            doi=doi,
            arxiv_id=arxiv_id,
            canonical_url=url,
        )
        hit_payload = {
            "provider": "semantic-scholar",
            "provider_record_id": paper_id,
            "query_id": request.query_id,
            "provider_rank": provider_rank,
        }
        hits.append(
            SearchHitV1(
                hit_id=deterministic_id("hit", hit_payload),
                document_id=document_id,
                provider="semantic-scholar",
                provider_record_id=paper_id,
                query_ids=(request.query_id,),
                provider_rank=provider_rank,
                title=title,
                authors=authors,
                published_year=year,
                doi=doi,
                arxiv_id=arxiv_id,
                canonical_url=url,
                abstract=abstract,
                keywords=keywords,
                raw_response_artifact=raw_response_artifact,
            )
        )
    return ParsedSearchPage(hits=tuple(hits), warnings=tuple(warnings))


def _request_url(request: SemanticScholarRequestV1) -> str:
    common = {"limit": str(request.max_results), "fields": _PAPER_FIELDS}
    if request.route is SemanticScholarRoute.TOPIC:
        assert request.query_text is not None
        parameters = {**common, "query": request.query_text}
        year = _year_parameter(request)
        if year is not None:
            parameters["year"] = year
        path = "/graph/v1/paper/search"
    else:
        assert request.anchor_paper_id is not None
        anchor = quote(request.anchor_paper_id, safe=":")
        if request.route is SemanticScholarRoute.RECOMMENDATIONS:
            path = f"/recommendations/v1/papers/forpaper/{anchor}"
        elif request.route is SemanticScholarRoute.REFERENCES:
            path = f"/graph/v1/paper/{anchor}/references"
            common["fields"] = "contexts,intents,isInfluential,citedPaper." + _PAPER_FIELDS.replace(
                ",", ",citedPaper."
            )
        else:
            path = f"/graph/v1/paper/{anchor}/citations"
            common["fields"] = (
                "contexts,intents,isInfluential,citingPaper."
                + _PAPER_FIELDS.replace(",", ",citingPaper.")
            )
        parameters = common
    return f"https://{_SEMANTIC_SCHOLAR_HOST}{path}?{urlencode(parameters)}"


def _records_for_route(
    decoded: Mapping[str, Any], route: SemanticScholarRoute
) -> list[Any]:
    field = "recommendedPapers" if route is SemanticScholarRoute.RECOMMENDATIONS else "data"
    records = decoded.get(field)
    if not isinstance(records, list):
        raise SearchAdapterError(
            "SCHEMA_DRIFT", f"Semantic Scholar response requires a {field} array"
        )
    return records


def _paper_for_route(
    record: Mapping[str, Any], route: SemanticScholarRoute
) -> Mapping[str, Any] | None:
    if route in {SemanticScholarRoute.TOPIC, SemanticScholarRoute.RECOMMENDATIONS}:
        return record
    field = "citedPaper" if route is SemanticScholarRoute.REFERENCES else "citingPaper"
    paper = record.get(field)
    if paper is None:
        return None
    if not isinstance(paper, Mapping):
        raise SearchAdapterError("SCHEMA_DRIFT", f"{field} must be an object or null")
    return paper


def _semantic_scholar_paper_id(value: str) -> str:
    normalized = value.strip()
    doi = normalize_doi(normalized)
    if doi is not None:
        return f"DOI:{doi}"
    if (
        not normalized
        or len(normalized) > 256
        or any(character.isspace() for character in normalized)
    ):
        raise ValueError("anchor provider_record_id is not a supported paper ID")
    return normalized


def _year_parameter(request: SemanticScholarRequestV1) -> str | None:
    lower = request.publication_year_from
    upper = request.publication_year_to
    if lower is None and upper is None:
        return None
    if lower is None:
        return f"-{upper}"
    if upper is None:
        return f"{lower}-"
    return f"{lower}-{upper}"


def _year_allowed(year: int | None, request: SemanticScholarRequestV1) -> bool:
    if request.publication_year_from is None and request.publication_year_to is None:
        return True
    if year is None:
        return False
    if request.publication_year_from is not None and year < request.publication_year_from:
        return False
    return request.publication_year_to is None or year <= request.publication_year_to


def _deadline_from_remaining(
    value: float | None, *, monotonic_clock: Callable[[], float]
) -> float | None:
    if value is None:
        return None
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
        or value <= 0
    ):
        raise SearchAdapterError(
            "WALLTIME_BUDGET_EXCEEDED", "remaining walltime must be finite and positive"
        )
    return float(monotonic_clock()) + float(value)


def _attempts_from_hops(
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


def _required_string(
    record: Mapping[str, Any], field: str, *, maximum: int = 512
) -> str:
    value = _optional_string(record.get(field))
    if value is None or len(value) > maximum:
        raise SearchAdapterError(
            "SCHEMA_DRIFT", f"paper {field} must be a bounded non-empty string"
        )
    return value


def _optional_string(value: Any) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise SearchAdapterError("SCHEMA_DRIFT", "paper text field must be a string")
    normalized = " ".join(value.split())
    return normalized or None


def _bounded_unique_join(values: list[str], *, maximum: int) -> str:
    selected: list[str] = []
    seen: set[str] = set()
    for value in values:
        normalized = " ".join(value.split())
        if not normalized or normalized.casefold() in seen:
            continue
        proposal = " ".join((*selected, normalized))
        if len(proposal) > maximum:
            break
        seen.add(normalized.casefold())
        selected.append(normalized)
    if not selected:
        raise ValueError("topic query has no bounded terms")
    return " ".join(selected)
