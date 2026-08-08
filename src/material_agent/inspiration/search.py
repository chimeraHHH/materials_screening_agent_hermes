"""Bounded metadata search primitives for the inspiration capability.

The offline adapter returns raw response bytes; callers must persist those bytes
before parsing them into :class:`SearchHitV1` objects.  This module intentionally
has no PDF or full-document interface.
"""

from __future__ import annotations

import hashlib
import html
import json
import socket
from collections.abc import Mapping
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Protocol
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
            raise SearchAdapterError(
                "HTTP_ERROR",
                f"metadata endpoint returned HTTP {error.code}",
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
            )
        return payload


class CrossrefPublicAdapter:
    """Keyless Crossref v1 metadata search with a finite single-page result."""

    component = ComponentSnapshotV1(
        component_id="crossref-public-adapter",
        version="v1",
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
        self.max_results = max_results
        self.timeout_seconds = timeout_seconds
        self.contact_email = contact_email
        self.transport = transport or UrlLibBoundedTransport()

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
        payload = self.transport.get(
            url,
            headers={
                "Accept": "application/json",
                "Accept-Encoding": "identity",
                "User-Agent": "materials-screening-agent/0.1 (metadata-only)",
            },
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
            )
        return RawSearchPage(
            provider="crossref",
            query_id=query.query_id,
            payload=payload,
        )


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
