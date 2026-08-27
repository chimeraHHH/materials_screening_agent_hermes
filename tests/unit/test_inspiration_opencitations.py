from __future__ import annotations

import base64
import json
from urllib.parse import unquote, urlsplit

import pytest

from material_agent.inspiration.models import ArtifactPointerV1
from material_agent.inspiration.opencitations import (
    OpenCitationsPublicAdapter,
    OpenCitationsRequestV1,
    OpenCitationsRoute,
    parse_opencitations_page,
)
from material_agent.inspiration.search import SearchAdapterError


class _SequenceTransport:
    def __init__(self, payloads: list[bytes]) -> None:
        self.payloads = payloads
        self.calls: list[dict[str, object]] = []

    def get(self, url: str, **kwargs: object) -> bytes:
        self.calls.append({"url": url, **kwargs})
        return self.payloads[len(self.calls) - 1]


def _request(route: OpenCitationsRoute = OpenCitationsRoute.CITATIONS):
    return OpenCitationsRequestV1(
        query_id="query-citation-graph",
        route=route,
        anchor_doi="10.1000/anchor",
        max_results=5,
        publication_year_from=1960,
        publication_year_to=2026,
    )


def _pointer() -> ArtifactPointerV1:
    return ArtifactPointerV1(
        uri="artifact://research/run/raw_search/opencitations.json",
        sha256="a" * 64,
        media_type="application/json",
    )


def test_adapter_resolves_citation_edges_through_batched_meta_api() -> None:
    index = json.dumps(
        [
            {
                "oci": "1-2",
                "citing": "omid:br/1 doi:10.1000/CITING openalex:W1",
                "cited": "omid:br/2 doi:10.1000/anchor",
            }
        ]
    ).encode()
    metadata = json.dumps(
        [
            {
                "id": "doi:10.1000/citing omid:br/1",
                "title": "A citing flat-band study",
                "author": "Example, Ada [orcid:1]; Example, Bob",
                "pub_date": "2025-02",
                "venue": "Test Journal [issn:1234]",
                "type": "journal article",
            }
        ]
    ).encode()
    transport = _SequenceTransport([index, metadata])
    adapter = OpenCitationsPublicAdapter(
        access_token_resolver=lambda: "test-token",
        transport=transport,
        monotonic_clock=lambda: 100.0,
    )

    page = adapter.search(
        _request(),
        max_response_bytes=100_000,
        remaining_walltime_seconds=5.0,
        max_physical_requests=2,
    )
    parsed = parse_opencitations_page(
        request=_request(),
        payload=page.payload,
        raw_response_artifact=_pointer(),
    )

    assert len(transport.calls) == 2
    assert urlsplit(str(transport.calls[0]["url"])).path == (
        "/index/v2/citations/doi:10.1000/anchor"
    )
    assert unquote(urlsplit(str(transport.calls[1]["url"])).path) == (
        "/meta/v1/metadata/doi:10.1000/citing"
    )
    assert transport.calls[0]["headers"]["authorization"] == "test-token"  # type: ignore[index]
    assert transport.calls[0]["deadline_monotonic"] == 105.0
    assert len(page.attempts) == 2
    assert len(parsed.hits) == 1
    assert parsed.hits[0].doi == "10.1000/citing"
    assert parsed.hits[0].authors == ("Example, Ada", "Example, Bob")
    assert parsed.hits[0].published_year == 2025

    envelope = json.loads(page.payload)
    assert base64.b64decode(envelope["index_payload_base64"]) == index
    assert base64.b64decode(envelope["metadata_payload_base64"]) == metadata


def test_adapter_uses_cited_side_for_references_and_skips_empty_meta_call() -> None:
    index = json.dumps(
        [
            {
                "citing": "doi:10.1000/anchor",
                "cited": "omid:br/2",
            }
        ]
    ).encode()
    transport = _SequenceTransport([index])
    page = OpenCitationsPublicAdapter(transport=transport).search(
        _request(OpenCitationsRoute.REFERENCES),
        max_response_bytes=100_000,
        max_physical_requests=1,
    )

    assert len(transport.calls) == 1
    parsed = parse_opencitations_page(
        request=_request(OpenCitationsRoute.REFERENCES),
        payload=page.payload,
        raw_response_artifact=_pointer(),
    )
    assert parsed.hits == ()


def test_adapter_refuses_unbudgeted_metadata_resolution() -> None:
    index = json.dumps(
        [{"citing": "doi:10.1000/citing", "cited": "doi:10.1000/anchor"}]
    ).encode()
    transport = _SequenceTransport([index])

    with pytest.raises(SearchAdapterError, match="second request"):
        OpenCitationsPublicAdapter(transport=transport).search(
            _request(),
            max_response_bytes=100_000,
            max_physical_requests=1,
        )

    assert len(transport.calls) == 1
