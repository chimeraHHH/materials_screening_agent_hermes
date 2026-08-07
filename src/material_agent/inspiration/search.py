"""Bounded metadata search primitives for the inspiration capability.

The offline adapter returns raw response bytes; callers must persist those bytes
before parsing them into :class:`SearchHitV1` objects.  This module intentionally
has no PDF or full-document interface.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import urlsplit, urlunsplit

from material_agent.inspiration.models import (
    ArtifactPointerV1,
    ComponentSnapshotV1,
    SearchHitV1,
    SearchQueryV1,
    deterministic_id,
)


class SearchAdapterError(RuntimeError):
    """A bounded search adapter or response violated its frozen contract."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(f"{code}: {message}")


@dataclass(frozen=True)
class RawSearchPage:
    """One finite response that must be written as an Artifact before parsing."""

    provider: str
    query_id: str
    payload: bytes
    media_type: str = "application/json"


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
