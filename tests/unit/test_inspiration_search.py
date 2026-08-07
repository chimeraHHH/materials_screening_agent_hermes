from __future__ import annotations

import json
from urllib.parse import parse_qs, urlsplit

import pytest

from material_agent.inspiration import (
    ArtifactPointerV1,
    SearchQueryKind,
    SearchQueryV1,
)
from material_agent.inspiration.search import (
    CrossrefPublicAdapter,
    FixtureSearchAdapter,
    SearchAdapterError,
    document_id_for,
    group_document_hits,
    normalize_doi,
    parse_crossref_page,
    parse_openalex_page,
)


def query(query_id: str = "query-1") -> SearchQueryV1:
    return SearchQueryV1(
        query_id=query_id,
        kind=SearchQueryKind.DIRECT,
        text="flat band compact localized state",
        tag_ids=("compact-localized-state",),
    )


def artifact(digest: str = "a") -> ArtifactPointerV1:
    return ArtifactPointerV1(
        uri="artifact://stages/inspiration/run-1/raw_search/query-1.json",
        sha256=digest * 64,
        media_type="application/json",
    )


def response_bytes(*, doi: str = "https://doi.org/10.1000/ABC") -> bytes:
    return json.dumps(
        {
            "results": [
                {
                    "id": "https://openalex.org/W1",
                    "doi": doi,
                    "title": "Compact localized states in a photonic lattice",
                    "publication_year": 2024,
                    "primary_location": {
                        "landing_page_url": "https://example.org/paper?tracking=1"
                    },
                    "authorships": [
                        {"author": {"display_name": "Ada Example"}},
                        {"author": {"display_name": "Ada Example"}},
                    ],
                    "keywords": [
                        {"display_name": "Flat band"},
                        {"display_name": "Interference"},
                    ],
                    "abstract_inverted_index": {
                        "Localized": [1],
                        "modes": [2],
                        "Compact": [0],
                    },
                }
            ]
        },
        sort_keys=True,
    ).encode()


def crossref_response_bytes() -> bytes:
    return json.dumps(
        {
            "status": "ok",
            "message": {
                "items": [
                    {
                        "DOI": "10.1000/CROSSREF",
                        "title": ["Localized interference in a mechanical lattice"],
                        "author": [
                            {"given": "Ada", "family": "Example"},
                            {"given": "Ada", "family": "Example"},
                        ],
                        "published": {"date-parts": [[2025, 2, 3]]},
                        "URL": "https://doi.org/10.1000/CROSSREF",
                        "abstract": (
                            "<jats:p>Compact localized modes arise because destructive "
                            "interference suppresses transport in the lattice.</jats:p>"
                        ),
                        "subject": ["Mechanical metamaterials", "Flat bands"],
                    }
                ]
            },
        },
        sort_keys=True,
    ).encode()


class _RecordingTransport:
    def __init__(self, payload: object) -> None:
        self.payload = payload
        self.calls: list[dict[str, object]] = []

    def get(
        self,
        url: str,
        *,
        headers: dict[str, str],
        timeout_seconds: int,
        max_response_bytes: int,
    ) -> bytes:
        self.calls.append(
            {
                "url": url,
                "headers": headers,
                "timeout_seconds": timeout_seconds,
                "max_response_bytes": max_response_bytes,
            }
        )
        return self.payload  # type: ignore[return-value]


def test_fixture_adapter_is_network_free_and_enforces_byte_budget() -> None:
    payload = response_bytes()
    adapter = FixtureSearchAdapter({"query-1": payload})

    page = adapter.search(query(), max_response_bytes=len(payload))

    assert adapter.network_access is False
    assert page.payload == payload
    assert page.query_id == "query-1"

    with pytest.raises(SearchAdapterError) as raised:
        adapter.search(query(), max_response_bytes=len(payload) - 1)
    assert raised.value.code == "RESPONSE_BUDGET_EXCEEDED"

    with pytest.raises(SearchAdapterError) as raised:
        adapter.search(query("missing-query"), max_response_bytes=10_000)
    assert raised.value.code == "FIXTURE_QUERY_NOT_FOUND"


def test_crossref_adapter_requests_only_bounded_metadata_fields() -> None:
    payload = crossref_response_bytes()
    transport = _RecordingTransport(payload)
    adapter = CrossrefPublicAdapter(
        max_results=3,
        timeout_seconds=7,
        transport=transport,
    )

    page = adapter.search(query(), max_response_bytes=len(payload))

    assert page.provider == "crossref"
    assert page.payload == payload
    assert adapter.network_access is True
    call = transport.calls[0]
    parameters = parse_qs(urlsplit(str(call["url"])).query)
    assert parameters["rows"] == ["3"]
    assert parameters["filter"] == ["has-abstract:true"]
    assert parameters["query.bibliographic"] == [query().text]
    assert parameters["select"] == [
        "DOI,title,author,published,URL,abstract,subject"
    ]
    assert "link" not in parameters["select"][0].casefold()
    assert call["max_response_bytes"] == len(payload)
    assert call["headers"] == {
        "Accept": "application/json",
        "Accept-Encoding": "identity",
        "User-Agent": "materials-screening-agent/0.1 (metadata-only)",
    }


@pytest.mark.parametrize("payload", ["not-bytes", b"x" * 10])
def test_crossref_adapter_rechecks_an_injected_transport(payload: object) -> None:
    adapter = CrossrefPublicAdapter(transport=_RecordingTransport(payload))

    with pytest.raises(SearchAdapterError) as raised:
        adapter.search(query(), max_response_bytes=5)

    assert raised.value.code in {
        "INVALID_NETWORK_PAYLOAD",
        "RESPONSE_BUDGET_EXCEEDED",
    }


def test_openalex_parser_uses_only_metadata_and_rebuilds_abstract() -> None:
    parsed = parse_openalex_page(
        query=query(),
        payload=response_bytes(),
        raw_response_artifact=artifact(),
        max_hits=5,
    )

    assert len(parsed.hits) == 1
    hit = parsed.hits[0]
    assert hit.abstract == "Compact Localized modes"
    assert hit.doi == "10.1000/abc"
    assert hit.canonical_url == "https://example.org/paper"
    assert hit.authors == ("Ada Example",)
    assert hit.query_ids == ("query-1",)
    assert hit.raw_response_artifact.sha256 == "a" * 64
    assert parsed.warnings == ()


def test_crossref_parser_normalizes_jats_metadata_without_fetching_full_text() -> None:
    parsed = parse_crossref_page(
        query=query(),
        payload=crossref_response_bytes(),
        raw_response_artifact=artifact(),
        max_hits=5,
    )

    assert len(parsed.hits) == 1
    hit = parsed.hits[0]
    assert hit.provider == "crossref"
    assert hit.provider_record_id == "10.1000/crossref"
    assert hit.doi == "10.1000/crossref"
    assert hit.authors == ("Ada Example",)
    assert hit.published_year == 2025
    assert hit.abstract == (
        "Compact localized modes arise because destructive interference "
        "suppresses transport in the lattice."
    )
    assert hit.keywords == ("Mechanical metamaterials", "Flat bands")
    assert hit.raw_response_artifact == artifact()


def test_document_identity_prefers_normalized_doi_across_queries() -> None:
    first = parse_openalex_page(
        query=query("query-1"),
        payload=response_bytes(doi="https://doi.org/10.1000/ABC"),
        raw_response_artifact=artifact("a"),
        max_hits=5,
    ).hits[0]
    second = parse_openalex_page(
        query=query("query-2"),
        payload=response_bytes(doi="doi:10.1000/abc"),
        raw_response_artifact=artifact("b"),
        max_hits=5,
    ).hits[0]

    assert first.document_id == second.document_id
    groups = group_document_hits((second, first))
    assert len(groups) == 1
    assert groups[0].member_hit_ids == tuple(sorted((first.hit_id, second.hit_id)))
    assert groups[0].representative_hit_id == min(first.hit_id, second.hit_id)


def test_document_identity_falls_back_without_claiming_cross_database_uniqueness() -> None:
    by_url = document_id_for(
        provider="openalex",
        provider_record_id="W1",
        doi=None,
        arxiv_id=None,
        canonical_url="HTTPS://EXAMPLE.ORG/paper/?tracking=1#part",
    )
    same_url = document_id_for(
        provider="crossref",
        provider_record_id="other",
        doi=None,
        arxiv_id=None,
        canonical_url="https://example.org/paper",
    )
    provider_fallback = document_id_for(
        provider="openalex",
        provider_record_id="W1",
        doi=None,
        arxiv_id=None,
        canonical_url=None,
    )

    assert by_url == same_url
    assert provider_fallback != by_url
    assert normalize_doi("not a DOI") is None


@pytest.mark.parametrize(
    "payload",
    [
        b"not-json",
        b"{}",
        json.dumps({"results": [None]}).encode(),
        json.dumps({"results": [{"id": "W1"}]}).encode(),
        json.dumps(
            {
                "results": [
                    {
                        "id": "W1",
                        "title": "title",
                        "abstract_inverted_index": {"a": [0], "b": [0]},
                    }
                ]
            }
        ).encode(),
    ],
)
def test_openalex_schema_drift_fails_closed(payload: bytes) -> None:
    with pytest.raises(SearchAdapterError):
        parse_openalex_page(
            query=query(),
            payload=payload,
            raw_response_artifact=artifact(),
            max_hits=5,
        )


def test_hit_budget_is_applied_before_model_construction() -> None:
    record = {
        "id": "https://openalex.org/W1",
        "title": "first",
    }
    payload = json.dumps({"results": [record, {**record, "id": "W2", "title": "second"}]}).encode()

    parsed = parse_openalex_page(
        query=query(),
        payload=payload,
        raw_response_artifact=artifact(),
        max_hits=1,
    )

    assert [hit.title for hit in parsed.hits] == ["first"]
