from __future__ import annotations

import json
from datetime import UTC, datetime
from email.message import Message
from typing import Self
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlsplit

import pytest

from material_agent.inspiration import (
    ArtifactPointerV1,
    ComponentSnapshotV1,
    SearchQueryKind,
    SearchQueryV1,
)
from material_agent.inspiration import search as inspiration_search
from material_agent.inspiration.policy import (
    InspirationPolicyV1,
    SearchBudgetV1,
    SearchExecutionMode,
)
from material_agent.inspiration.runner import public_inspiration_runner_from_environment
from material_agent.inspiration.search import (
    ArxivPublicAdapter,
    CrossrefPublicAdapter,
    FixtureSearchAdapter,
    MultiSourceSearchAdapter,
    OpenAlexPublicAdapter,
    OstiPublicAdapter,
    ParsedSearchPage,
    RawSearchPage,
    SearchAdapterError,
    UrlLibBoundedTransport,
    document_id_for,
    group_document_hits,
    normalize_arxiv_id,
    normalize_doi,
    parse_arxiv_page,
    parse_crossref_page,
    parse_multi_source_page,
    parse_openalex_page,
    parse_osti_page,
    public_search_adapter_from_environment,
)
from material_agent.retrieval.storage import LocalArtifactStore


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


def arxiv_response_bytes(*, published_year: int = 2024) -> bytes:
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom"
      xmlns:arxiv="http://arxiv.org/schemas/atom">
  <title>arXiv Query</title>
  <entry>
    <id>http://arxiv.org/abs/2401.01234v2</id>
    <updated>{published_year}-01-03T00:00:00Z</updated>
    <published>{published_year}-01-02T00:00:00Z</published>
    <title>Compact localized states in a materials lattice</title>
    <summary>Compact localized states arise because destructive interference
    suppresses electronic hopping and produces a narrow band in the lattice.</summary>
    <author><name>Ada Example</name></author>
    <author><name>Ada Example</name></author>
    <category term="cond-mat.mtrl-sci" />
    <category term="cond-mat.str-el" />
    <arxiv:doi>10.1000/ABC</arxiv:doi>
  </entry>
</feed>""".encode()


def osti_response_bytes(*, published_year: int = 1987) -> bytes:
    return json.dumps(
        [
            {
                "osti_id": "1234567",
                "title": "Flat-band behavior in layered <sub>Ti</sub> compounds",
                "publication_date": f"{published_year}-06-01T00:00:00Z",
                "doi": "10.1000/CROSSREF",
                "authors": [
                    "Ada Example [National Laboratory]",
                    "Ada Example [National Laboratory]",
                ],
                "description": (
                    "<p>Destructive interference suppresses hopping and produces "
                    "a narrow electronic band in a layered inorganic lattice.</p>"
                ),
                "subjects": ["Materials Science", "Condensed Matter Physics"],
                "product_type": "Technical Report",
                "links": [
                    {
                        "rel": "citation",
                        "href": "https://www.osti.gov/biblio/1234567",
                    }
                ],
            }
        ],
        sort_keys=True,
    ).encode("utf-8")


class _RecordingTransport:
    def __init__(self, payload: object) -> None:
        self.payload = payload
        self.calls: list[dict[str, object]] = []

    def get(
        self,
        url: str,
        *,
        headers: dict[str, str],
        timeout_seconds: float,
        max_response_bytes: int,
        deadline_monotonic: float | None = None,
        max_physical_requests: int | None = None,
    ) -> bytes:
        del deadline_monotonic, max_physical_requests
        self.calls.append(
            {
                "url": url,
                "headers": headers,
                "timeout_seconds": timeout_seconds,
                "max_response_bytes": max_response_bytes,
            }
        )
        return self.payload  # type: ignore[return-value]


class _NoopTransformationEngine:
    component = ComponentSnapshotV1(
        component_id="noop-transformation-engine",
        version="1",
        implementation_sha256="f" * 64,
    )

    def generate(self, context):
        del context
        return ()


class _FailingTransport:
    def __init__(self) -> None:
        self.calls = 0

    def get(self, *args, **kwargs) -> bytes:
        del args, kwargs
        self.calls += 1
        raise SearchAdapterError(
            "NETWORK_ERROR",
            "injected bounded transport failure",
        )


class _SequenceTransport:
    def __init__(self, outcomes: list[bytes | SearchAdapterError]) -> None:
        self.outcomes = list(outcomes)
        self.calls = 0

    def get(self, *args, **kwargs) -> bytes:
        del args, kwargs
        self.calls += 1
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, SearchAdapterError):
            raise outcome
        return outcome


class _FakeClock:
    def __init__(self, *, monotonic: float = 0.0, wall: float = 0.0) -> None:
        self.monotonic_seconds = monotonic
        self.wall_seconds = wall
        self.sleeps: list[float] = []

    def monotonic(self) -> float:
        return self.monotonic_seconds

    def wall(self) -> float:
        return self.wall_seconds

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.monotonic_seconds += seconds
        self.wall_seconds += seconds


class _HttpResponse:
    def __init__(
        self,
        url: str,
        payload: bytes = b"{}",
        *,
        content_type: str = "application/json",
        content_length: str | None = None,
    ) -> None:
        self.url = url
        self.payload = payload
        self.headers = Message()
        self.headers["Content-Type"] = content_type
        if content_length is not None:
            self.headers["Content-Length"] = content_length
        self.read_sizes: list[int] = []

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *args: object) -> None:
        del args

    def geturl(self) -> str:
        return self.url

    def read(self, size: int) -> bytes:
        self.read_sizes.append(size)
        return self.payload[:size]


class _UrlOpenSequence:
    def __init__(self, outcomes: list[_HttpResponse | HTTPError]) -> None:
        self.outcomes = list(outcomes)
        self.calls: list[tuple[str, float]] = []

    def __call__(self, request: object, *, timeout: float) -> _HttpResponse:
        self.calls.append((str(request.full_url), timeout))
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, HTTPError):
            raise outcome
        return outcome


def _redirect_error(
    source_url: str,
    location: str | None,
    *,
    status: int = 302,
) -> HTTPError:
    headers = Message()
    if location is not None:
        headers["Location"] = location
    return HTTPError(source_url, status, "injected redirect", headers, None)


def transient_http_error(
    status: int,
    *,
    retry_after: str | None = None,
) -> SearchAdapterError:
    return SearchAdapterError(
        "TRANSIENT_HTTP_ERROR",
        f"injected HTTP {status}",
        http_status=status,
        retry_after=retry_after,
    )


def test_fixture_adapter_is_network_free_and_enforces_byte_budget() -> None:
    payload = response_bytes()
    adapter = FixtureSearchAdapter({"query-1": payload})

    page = adapter.search(query(), max_response_bytes=len(payload))

    assert adapter.network_access is False
    assert page.payload == payload
    assert page.query_id == "query-1"
    assert len(page.attempts) == 1
    assert page.attempts[0].outcome == "success"
    assert page.attempts[0].response_bytes == len(payload)
    assert page.attempts[0].http_status is None

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
    assert len(page.attempts) == 1
    assert page.attempts[0].http_status == 200
    assert page.attempts[0].response_bytes == len(payload)
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
    transport = _RecordingTransport(payload)
    adapter = CrossrefPublicAdapter(transport=transport)

    with pytest.raises(SearchAdapterError) as raised:
        adapter.search(query(), max_response_bytes=5)

    assert raised.value.code in {
        "INVALID_NETWORK_PAYLOAD",
        "RESPONSE_BUDGET_EXCEEDED",
    }
    assert len(transport.calls) == 1
    assert len(raised.value.attempts) == 1
    assert raised.value.attempts[0].response_bytes == (
        len(payload) if isinstance(payload, bytes) else 0
    )


def test_crossref_adapter_preserves_structured_network_errors() -> None:
    transport = _FailingTransport()
    adapter = CrossrefPublicAdapter(
        transport=transport,
        max_retries=0,
    )

    with pytest.raises(SearchAdapterError) as raised:
        adapter.search(query(), max_response_bytes=1_000)

    assert raised.value.code == "NETWORK_ERROR"
    assert transport.calls == 1
    assert len(raised.value.attempts) == 1


@pytest.mark.parametrize("status", [408, 429, 500, 502, 503, 504])
def test_url_transport_classifies_only_allowlisted_transient_http_statuses(
    monkeypatch: pytest.MonkeyPatch,
    status: int,
) -> None:
    headers = Message()
    headers["Retry-After"] = "7"

    def fail(*args, **kwargs):
        del args, kwargs
        raise HTTPError(
            "https://api.crossref.org/v1/works",
            status,
            "injected",
            headers,
            None,
        )

    monkeypatch.setattr("material_agent.inspiration.search.urlopen", fail)

    with pytest.raises(SearchAdapterError) as raised:
        UrlLibBoundedTransport().get(
            "https://api.crossref.org/v1/works",
            headers={"Accept": "application/json"},
            timeout_seconds=1,
            max_response_bytes=100,
        )

    assert raised.value.code == "TRANSIENT_HTTP_ERROR"
    assert raised.value.http_status == status
    assert raised.value.retry_after == "7"


def test_url_transport_marks_permanent_4xx_without_retry_metadata_loss(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    headers = Message()
    headers["Retry-After"] = "7"

    def fail(*args, **kwargs):
        del args, kwargs
        raise HTTPError(
            "https://api.crossref.org/v1/works",
            404,
            "injected",
            headers,
            None,
        )

    monkeypatch.setattr("material_agent.inspiration.search.urlopen", fail)

    with pytest.raises(SearchAdapterError) as raised:
        UrlLibBoundedTransport().get(
            "https://api.crossref.org/v1/works",
            headers={"Accept": "application/json"},
            timeout_seconds=1,
            max_response_bytes=100,
        )

    assert raised.value.code == "HTTP_ERROR"
    assert raised.value.http_status == 404
    assert raised.value.retry_after == "7"


def test_default_url_opener_has_redirect_following_disabled() -> None:
    handlers = [
        handler
        for handler in inspiration_search._NO_REDIRECT_OPENER.handlers
        if isinstance(handler, inspiration_search._RejectRedirectHandler)
    ]

    assert len(handlers) == 1
    assert handlers[0].redirect_request(None, None, 302, None, None, None) is None


@pytest.mark.parametrize(
    "redirect_url",
    [
        "http://api.crossref.org/v1/works",
        "https://evil.example/v1/works",
        "https://api.crossref.org:444/v1/works",
    ],
)
def test_url_transport_rejects_untrusted_redirect_before_second_request(
    monkeypatch: pytest.MonkeyPatch,
    redirect_url: str,
) -> None:
    start_url = "https://api.crossref.org/v1/works"
    urlopen = _UrlOpenSequence(
        [
            _redirect_error(start_url, redirect_url),
            _HttpResponse(redirect_url),
        ]
    )
    monkeypatch.setattr("material_agent.inspiration.search.urlopen", urlopen)

    with pytest.raises(SearchAdapterError) as raised:
        UrlLibBoundedTransport().get(
            start_url,
            headers={"Accept": "application/json"},
            timeout_seconds=7,
            max_response_bytes=100,
        )

    assert raised.value.code == "UNTRUSTED_METADATA_REDIRECT"
    assert urlopen.calls == [(start_url, 7)]


@pytest.mark.parametrize(
    "url",
    [
        "http://api.crossref.org/v1/works",
        "https://evil.example/v1/works",
        "https://api.crossref.org:444/v1/works",
    ],
)
def test_url_transport_validates_initial_scheme_host_and_port_before_request(
    monkeypatch: pytest.MonkeyPatch,
    url: str,
) -> None:
    urlopen = _UrlOpenSequence([])
    monkeypatch.setattr("material_agent.inspiration.search.urlopen", urlopen)

    with pytest.raises(SearchAdapterError) as raised:
        UrlLibBoundedTransport().get(
            url,
            headers={"Accept": "application/json"},
            timeout_seconds=1,
            max_response_bytes=100,
        )

    assert raised.value.code == "UNTRUSTED_METADATA_ENDPOINT"
    assert urlopen.calls == []


@pytest.mark.parametrize("status", [301, 302, 303, 307, 308])
def test_url_transport_follows_bounded_same_host_redirect_explicitly(
    monkeypatch: pytest.MonkeyPatch,
    status: int,
) -> None:
    start_url = "https://api.crossref.org/v1/works"
    target_url = "https://api.crossref.org/v1/works?cursor=next"
    response = _HttpResponse(target_url, b'{"status":"ok"}')
    urlopen = _UrlOpenSequence(
        [
            _redirect_error(
                start_url,
                "/v1/works?cursor=next#ignored",
                status=status,
            ),
            response,
        ]
    )
    monkeypatch.setattr("material_agent.inspiration.search.urlopen", urlopen)

    result = UrlLibBoundedTransport().get(
        start_url,
        headers={"Accept": "application/json"},
        timeout_seconds=9,
        max_response_bytes=100,
    )

    assert result.payload == b'{"status":"ok"}'
    assert tuple(hop.outcome for hop in result.physical_hops) == (
        "redirect",
        "success",
    )
    assert urlopen.calls == [(start_url, 9), (target_url, 9)]
    assert response.read_sizes == [101]


def test_crossref_adapter_audits_every_redirect_as_a_physical_attempt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    start_url = "https://api.crossref.org/v1/works"
    target_url = "https://api.crossref.org/v1/works?cursor=next"
    urlopen = _UrlOpenSequence(
        [
            _redirect_error(start_url, target_url),
            _HttpResponse(target_url, crossref_response_bytes()),
        ]
    )
    monkeypatch.setattr("material_agent.inspiration.search.urlopen", urlopen)
    adapter = CrossrefPublicAdapter(
        transport=UrlLibBoundedTransport(),
        max_retries=0,
    )

    page = adapter.search(query(), max_response_bytes=10_000)

    assert tuple(attempt.outcome for attempt in page.attempts) == (
        "redirect",
        "success",
    )
    assert tuple(attempt.attempt_number for attempt in page.attempts) == (1, 2)
    assert tuple(attempt.http_status for attempt in page.attempts) == (302, 200)


def test_url_transport_recomputes_timeout_for_each_redirect_hop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    start_url = "https://api.crossref.org/v1/works"
    target_url = "https://api.crossref.org/v1/works?cursor=next"
    clock = _FakeClock()
    sequence = _UrlOpenSequence(
        [
            _redirect_error(start_url, target_url),
            _HttpResponse(target_url, b"{}"),
        ]
    )

    def advancing_urlopen(request: object, *, timeout: float) -> _HttpResponse:
        try:
            return sequence(request, timeout=timeout)
        finally:
            clock.monotonic_seconds += 0.4

    monkeypatch.setattr(
        "material_agent.inspiration.search.urlopen",
        advancing_urlopen,
    )

    result = UrlLibBoundedTransport(
        monotonic_clock=clock.monotonic
    ).get(
        start_url,
        headers={},
        timeout_seconds=9,
        max_response_bytes=100,
        deadline_monotonic=1.0,
    )

    assert result.payload == b"{}"
    assert sequence.calls[0][1] == 1.0
    assert sequence.calls[1][1] == pytest.approx(0.6)


def test_crossref_redirect_cannot_exceed_physical_request_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    start_url = "https://api.crossref.org/v1/works"
    target_url = "https://api.crossref.org/v1/works?cursor=next"
    urlopen = _UrlOpenSequence(
        [
            _redirect_error(start_url, target_url),
            _HttpResponse(target_url, crossref_response_bytes()),
        ]
    )
    monkeypatch.setattr("material_agent.inspiration.search.urlopen", urlopen)
    adapter = CrossrefPublicAdapter(
        transport=UrlLibBoundedTransport(),
        max_retries=0,
    )

    with pytest.raises(SearchAdapterError) as raised:
        adapter.search(
            query(),
            max_response_bytes=10_000,
            max_physical_requests=1,
        )

    assert raised.value.code == "SEARCH_REQUEST_BUDGET_EXCEEDED"
    assert len(raised.value.attempts) == 1
    assert raised.value.attempts[0].outcome == "redirect"
    assert len(urlopen.calls) == 1


def test_url_transport_rejects_redirect_loop_before_repeating_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    start_url = "https://api.crossref.org/v1/works"
    second_url = "https://api.crossref.org/v1/works?hop=1"
    urlopen = _UrlOpenSequence(
        [
            _redirect_error(start_url, "/v1/works?hop=1"),
            _redirect_error(second_url, "/v1/works"),
        ]
    )
    monkeypatch.setattr("material_agent.inspiration.search.urlopen", urlopen)

    with pytest.raises(SearchAdapterError) as raised:
        UrlLibBoundedTransport().get(
            start_url,
            headers={},
            timeout_seconds=1,
            max_response_bytes=100,
        )

    assert raised.value.code == "METADATA_REDIRECT_LOOP"
    assert urlopen.calls == [(start_url, 1), (second_url, 1)]


def test_url_transport_enforces_maximum_redirect_count(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    start_url = "https://api.crossref.org/v1/works?hop=0"
    outcomes = [
        _redirect_error(
            f"https://api.crossref.org/v1/works?hop={hop}",
            f"/v1/works?hop={hop + 1}",
        )
        for hop in range(6)
    ]
    urlopen = _UrlOpenSequence(outcomes)
    monkeypatch.setattr("material_agent.inspiration.search.urlopen", urlopen)

    with pytest.raises(SearchAdapterError) as raised:
        UrlLibBoundedTransport().get(
            start_url,
            headers={},
            timeout_seconds=1,
            max_response_bytes=100,
        )

    assert raised.value.code == "TOO_MANY_METADATA_REDIRECTS"
    assert len(urlopen.calls) == 6


def test_url_transport_rejects_redirect_without_location(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    start_url = "https://api.crossref.org/v1/works"
    urlopen = _UrlOpenSequence([_redirect_error(start_url, None)])
    monkeypatch.setattr("material_agent.inspiration.search.urlopen", urlopen)

    with pytest.raises(SearchAdapterError) as raised:
        UrlLibBoundedTransport().get(
            start_url,
            headers={},
            timeout_seconds=1,
            max_response_bytes=100,
        )

    assert raised.value.code == "MISSING_REDIRECT_LOCATION"
    assert urlopen.calls == [(start_url, 1)]


def test_url_transport_rejects_non_json_before_reading_body(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    url = "https://api.crossref.org/v1/works"
    response = _HttpResponse(url, b"<html></html>", content_type="text/html")
    monkeypatch.setattr(
        "material_agent.inspiration.search.urlopen",
        _UrlOpenSequence([response]),
    )

    with pytest.raises(SearchAdapterError) as raised:
        UrlLibBoundedTransport().get(
            url,
            headers={},
            timeout_seconds=1,
            max_response_bytes=100,
        )

    assert raised.value.code == "UNEXPECTED_MEDIA_TYPE"
    assert response.read_sizes == []


@pytest.mark.parametrize("content_length", ["-1", "+1", "abc", "1, 2", ""])
def test_url_transport_rejects_invalid_content_length_before_read(
    monkeypatch: pytest.MonkeyPatch,
    content_length: str,
) -> None:
    url = "https://api.crossref.org/v1/works"
    response = _HttpResponse(url, content_length=content_length)
    monkeypatch.setattr(
        "material_agent.inspiration.search.urlopen",
        _UrlOpenSequence([response]),
    )

    with pytest.raises(SearchAdapterError) as raised:
        UrlLibBoundedTransport().get(
            url,
            headers={},
            timeout_seconds=1,
            max_response_bytes=5,
        )

    assert raised.value.code == "INVALID_CONTENT_LENGTH"
    assert response.read_sizes == []


@pytest.mark.parametrize("content_length", ["6", "9" * 5_000])
def test_url_transport_rejects_oversized_declared_length_before_read(
    monkeypatch: pytest.MonkeyPatch,
    content_length: str,
) -> None:
    url = "https://api.crossref.org/v1/works"
    response = _HttpResponse(url, content_length=content_length)
    monkeypatch.setattr(
        "material_agent.inspiration.search.urlopen",
        _UrlOpenSequence([response]),
    )

    with pytest.raises(SearchAdapterError) as raised:
        UrlLibBoundedTransport().get(
            url,
            headers={},
            timeout_seconds=1,
            max_response_bytes=5,
        )

    assert raised.value.code == "RESPONSE_BUDGET_EXCEEDED"
    assert response.read_sizes == []


def test_url_transport_uses_max_plus_one_to_enforce_stream_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    url = "https://api.crossref.org/v1/works"
    response = _HttpResponse(url, b"123456")
    monkeypatch.setattr(
        "material_agent.inspiration.search.urlopen",
        _UrlOpenSequence([response]),
    )

    with pytest.raises(SearchAdapterError) as raised:
        UrlLibBoundedTransport().get(
            url,
            headers={},
            timeout_seconds=1,
            max_response_bytes=5,
        )

    assert raised.value.code == "RESPONSE_BUDGET_EXCEEDED"
    assert raised.value.response_bytes == 6
    assert response.read_sizes == [6]


@pytest.mark.parametrize("status", [408, 429, 500, 502, 503, 504])
def test_crossref_adapter_retries_each_allowlisted_transient_status(
    status: int,
) -> None:
    payload = crossref_response_bytes()
    transport = _SequenceTransport(
        [transient_http_error(status, retry_after="0"), payload]
    )
    clock = _FakeClock()
    adapter = CrossrefPublicAdapter(
        transport=transport,
        max_retries=1,
        sleeper=clock.sleep,
        wall_clock=clock.wall,
        monotonic_clock=clock.monotonic,
    )

    page = adapter.search(query(), max_response_bytes=len(payload))

    assert transport.calls == 2
    assert [attempt.outcome for attempt in page.attempts] == ["error", "success"]
    assert page.attempts[0].http_status == status
    assert page.attempts[0].retry_delay_seconds == 0
    assert page.attempts[1].pacing_delay_seconds == 1
    assert clock.sleeps == [1]


def test_crossref_adapter_retries_network_errors_with_finite_attempt_ledger() -> None:
    payload = crossref_response_bytes()
    transport = _SequenceTransport(
        [
            SearchAdapterError("NETWORK_ERROR", "first"),
            SearchAdapterError("NETWORK_ERROR", "second"),
            payload,
        ]
    )
    clock = _FakeClock()
    adapter = CrossrefPublicAdapter(
        transport=transport,
        max_retries=2,
        retry_backoff_seconds=1,
        sleeper=clock.sleep,
        wall_clock=clock.wall,
        monotonic_clock=clock.monotonic,
    )

    page = adapter.search(query(), max_response_bytes=len(payload))

    assert transport.calls == 3
    assert clock.sleeps == [1, 2]
    assert [attempt.attempt_number for attempt in page.attempts] == [1, 2, 3]
    assert [attempt.retry_delay_seconds for attempt in page.attempts] == [1, 2, 0]
    assert [attempt.response_bytes for attempt in page.attempts] == [0, 0, len(payload)]


def test_max_retries_counts_only_attempts_after_the_initial_request() -> None:
    transport = _FailingTransport()
    clock = _FakeClock()
    adapter = CrossrefPublicAdapter(
        transport=transport,
        max_retries=1,
        retry_backoff_seconds=1,
        sleeper=clock.sleep,
        wall_clock=clock.wall,
        monotonic_clock=clock.monotonic,
    )

    with pytest.raises(SearchAdapterError) as raised:
        adapter.search(query(), max_response_bytes=1_000)

    assert transport.calls == 2
    assert [attempt.attempt_number for attempt in raised.value.attempts] == [1, 2]
    assert [attempt.retry_delay_seconds for attempt in raised.value.attempts] == [1, 0]
    serialized = json.dumps(
        [attempt.to_dict() for attempt in raised.value.attempts],
        sort_keys=True,
    )
    assert '"attempt_number": 2' in serialized
    assert "timestamp" not in serialized


def test_crossref_adapter_does_not_retry_permanent_4xx() -> None:
    transport = _SequenceTransport(
        [
            SearchAdapterError(
                "HTTP_ERROR",
                "injected HTTP 400",
                http_status=400,
            ),
            crossref_response_bytes(),
        ]
    )
    clock = _FakeClock()
    adapter = CrossrefPublicAdapter(
        transport=transport,
        max_retries=2,
        sleeper=clock.sleep,
        wall_clock=clock.wall,
        monotonic_clock=clock.monotonic,
    )

    with pytest.raises(SearchAdapterError) as raised:
        adapter.search(query(), max_response_bytes=10_000)

    assert raised.value.code == "HTTP_ERROR"
    assert transport.calls == 1
    assert clock.sleeps == []
    assert len(raised.value.attempts) == 1


def test_retry_after_delta_is_capped_before_sleep() -> None:
    payload = crossref_response_bytes()
    transport = _SequenceTransport(
        [transient_http_error(429, retry_after="100"), payload]
    )
    clock = _FakeClock()
    adapter = CrossrefPublicAdapter(
        transport=transport,
        max_retries=1,
        max_retry_delay_seconds=3,
        max_total_wait_seconds=10,
        sleeper=clock.sleep,
        wall_clock=clock.wall,
        monotonic_clock=clock.monotonic,
    )

    page = adapter.search(query(), max_response_bytes=len(payload))

    assert clock.sleeps == [3]
    assert page.attempts[0].retry_delay_seconds == 3
    assert page.attempts[1].pacing_delay_seconds == 0


def test_retry_after_http_date_uses_injected_wall_clock() -> None:
    wall_time = 1_700_000_000.0
    retry_date = datetime.fromtimestamp(
        wall_time + 4,
        tz=UTC,
    ).strftime("%a, %d %b %Y %H:%M:%S GMT")
    payload = crossref_response_bytes()
    transport = _SequenceTransport(
        [transient_http_error(503, retry_after=retry_date), payload]
    )
    clock = _FakeClock(wall=wall_time)
    adapter = CrossrefPublicAdapter(
        transport=transport,
        max_retries=1,
        sleeper=clock.sleep,
        wall_clock=clock.wall,
        monotonic_clock=clock.monotonic,
    )

    page = adapter.search(query(), max_response_bytes=len(payload))

    assert clock.sleeps == [4]
    assert page.attempts[0].retry_delay_seconds == 4


def test_retry_stops_when_next_wait_would_exceed_total_budget() -> None:
    transport = _SequenceTransport(
        [
            transient_http_error(429, retry_after="4"),
            transient_http_error(429, retry_after="4"),
            crossref_response_bytes(),
        ]
    )
    clock = _FakeClock()
    adapter = CrossrefPublicAdapter(
        transport=transport,
        max_retries=5,
        max_total_wait_seconds=5,
        sleeper=clock.sleep,
        wall_clock=clock.wall,
        monotonic_clock=clock.monotonic,
    )

    with pytest.raises(SearchAdapterError) as raised:
        adapter.search(query(), max_response_bytes=10_000)

    assert transport.calls == 2
    assert clock.sleeps == [4]
    assert sum(attempt.retry_delay_seconds for attempt in raised.value.attempts) == 4
    assert raised.value.attempts[-1].retry_delay_seconds == 0


def test_crossref_rate_pacing_is_injected_and_audited_without_timestamps() -> None:
    payload = crossref_response_bytes()
    transport = _RecordingTransport(payload)
    clock = _FakeClock()
    adapter = CrossrefPublicAdapter(
        transport=transport,
        sleeper=clock.sleep,
        wall_clock=clock.wall,
        monotonic_clock=clock.monotonic,
    )

    first = adapter.search(query("query-1"), max_response_bytes=len(payload))
    second = adapter.search(query("query-2"), max_response_bytes=len(payload))

    assert first.attempts[0].pacing_delay_seconds == 0
    assert second.attempts[0].pacing_delay_seconds == 1
    assert clock.sleeps == [1]
    assert not hasattr(second.attempts[0], "timestamp")


def test_crossref_request_timeout_is_capped_by_remaining_run_walltime() -> None:
    payload = crossref_response_bytes()
    transport = _RecordingTransport(payload)
    clock = _FakeClock()
    adapter = CrossrefPublicAdapter(
        timeout_seconds=7,
        transport=transport,
        sleeper=clock.sleep,
        monotonic_clock=clock.monotonic,
    )

    adapter.search(
        query(),
        max_response_bytes=len(payload),
        remaining_walltime_seconds=0.25,
    )

    assert transport.calls[0]["timeout_seconds"] == 0.25


def test_crossref_refuses_retry_that_cannot_finish_before_deadline() -> None:
    clock = _FakeClock()
    adapter = CrossrefPublicAdapter(
        transport=_SequenceTransport(
            [SearchAdapterError("NETWORK_ERROR", "injected timeout")]
        ),
        max_retries=1,
        retry_backoff_seconds=1.0,
        sleeper=clock.sleep,
        monotonic_clock=clock.monotonic,
    )

    with pytest.raises(SearchAdapterError) as raised:
        adapter.search(
            query(),
            max_response_bytes=100,
            remaining_walltime_seconds=0.5,
        )

    assert raised.value.code == "WALLTIME_BUDGET_EXCEEDED"
    assert len(raised.value.attempts) == 1
    assert raised.value.attempts[0].error_code == "NETWORK_ERROR"
    assert raised.value.attempts[0].retry_delay_seconds == 0.0
    assert clock.sleeps == []


def test_crossref_polite_pool_paces_at_three_requests_per_second() -> None:
    payload = crossref_response_bytes()
    clock = _FakeClock()
    adapter = CrossrefPublicAdapter(
        contact_email="operator@example.org",
        transport=_RecordingTransport(payload),
        sleeper=clock.sleep,
        wall_clock=clock.wall,
        monotonic_clock=clock.monotonic,
    )

    adapter.search(query("query-1"), max_response_bytes=len(payload))
    second = adapter.search(query("query-2"), max_response_bytes=len(payload))

    assert clock.sleeps == [pytest.approx(1 / 3)]
    assert second.attempts[0].pacing_delay_seconds == pytest.approx(1 / 3)


def test_polite_pool_and_retry_configuration_are_bound_without_email_value() -> None:
    public = CrossrefPublicAdapter(max_retries=1)
    polite = CrossrefPublicAdapter(
        contact_email="operator@example.org",
        max_retries=1,
    )
    different_retry = CrossrefPublicAdapter(max_retries=2)

    assert public.min_request_interval_seconds == 1
    assert polite.min_request_interval_seconds == pytest.approx(1 / 3)
    assert public.component != polite.component
    assert public.component != different_retry.component
    assert "operator@example.org" not in str(polite.component.model_dump(mode="json"))


def test_crossref_rejects_unsafe_retry_and_rate_limits() -> None:
    with pytest.raises(ValueError, match="max_retries"):
        CrossrefPublicAdapter(max_retries=6)
    with pytest.raises(ValueError, match="max_total_wait_seconds"):
        CrossrefPublicAdapter(max_total_wait_seconds=121)
    with pytest.raises(ValueError, match="public pool"):
        CrossrefPublicAdapter(min_request_interval_seconds=0.5)
    with pytest.raises(ValueError, match="polite pool"):
        CrossrefPublicAdapter(
            contact_email="operator@example.org",
            min_request_interval_seconds=0.1,
        )


def test_crossref_schema_failure_occurs_after_one_http_attempt_without_retry() -> None:
    transport = _RecordingTransport(b"not-json")
    adapter = CrossrefPublicAdapter(transport=transport)

    page = adapter.search(query(), max_response_bytes=100)
    with pytest.raises(SearchAdapterError) as raised:
        parse_crossref_page(
            query=query(),
            payload=page.payload,
            raw_response_artifact=artifact(),
            max_hits=5,
        )

    assert raised.value.code == "INVALID_JSON"
    assert len(transport.calls) == 1
    assert len(page.attempts) == 1


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


def test_crossref_request_encodes_frozen_publication_year_range() -> None:
    transport = _RecordingTransport(crossref_response_bytes())
    adapter = CrossrefPublicAdapter(
        transport=transport,
        max_retries=0,
        publication_year_from=1960,
        publication_year_to=1990,
    )

    adapter.search(query(), max_response_bytes=100_000, max_physical_requests=1)

    parameters = parse_qs(urlsplit(str(transport.calls[0]["url"])).query)
    assert parameters["filter"] == [
        "has-abstract:true,from-pub-date:1960-01-01,until-pub-date:1990-12-31"
    ]
    assert adapter.component != CrossrefPublicAdapter(
        transport=transport,
        max_retries=0,
    ).component


def test_openalex_request_is_bounded_year_filtered_and_secret_is_lazy() -> None:
    transport = _RecordingTransport(response_bytes())
    calls = 0

    def resolve_key() -> str:
        nonlocal calls
        calls += 1
        return "offline-test-key"

    adapter = OpenAlexPublicAdapter(
        transport=transport,
        api_key_resolver=resolve_key,
        publication_year_from=1960,
        publication_year_to=1990,
    )
    assert calls == 0

    page = adapter.search(
        query(),
        max_response_bytes=100_000,
        max_physical_requests=1,
    )

    assert calls == 1
    assert page.provider == "openalex"
    assert len(page.attempts) == 1
    parameters = parse_qs(urlsplit(str(transport.calls[0]["url"])).query)
    assert parameters["filter"] == [
        "from_publication_date:1960-01-01,to_publication_date:1990-12-31"
    ]
    assert parameters["api_key"] == ["offline-test-key"]
    assert "offline-test-key" not in repr(page)
    assert "offline-test-key" not in adapter.component.model_dump_json()


def test_arxiv_adapter_builds_bounded_atom_query_and_paces_requests() -> None:
    payload = arxiv_response_bytes()
    transport = _RecordingTransport(payload)
    clock = _FakeClock()
    adapter = ArxivPublicAdapter(
        max_results=3,
        timeout_seconds=7,
        publication_year_from=1990,
        publication_year_to=2025,
        transport=transport,
        sleeper=clock.sleep,
        monotonic_clock=clock.monotonic,
    )

    first = adapter.search(query(), max_response_bytes=len(payload))
    second = adapter.search(query("query-2"), max_response_bytes=len(payload))

    assert first.provider == "arxiv"
    assert first.media_type == "application/atom+xml"
    assert second.attempts[0].pacing_delay_seconds == 3.0
    assert clock.sleeps == [3.0]
    parameters = parse_qs(urlsplit(str(transport.calls[0]["url"])).query)
    assert parameters["max_results"] == ["3"]
    assert parameters["start"] == ["0"]
    assert parameters["sortBy"] == ["relevance"]
    assert parameters["search_query"] == [
        ('all:"flat band compact localized state" AND '
        "submittedDate:[199001010000 TO 202512312359]")
    ]
    assert transport.calls[0]["headers"] == {
        "Accept": "application/atom+xml",
        "Accept-Encoding": "identity",
        "User-Agent": "materials-screening-agent/0.1 (metadata-only)",
    }


def test_parse_arxiv_page_normalizes_version_and_deduplicates_by_doi() -> None:
    parsed = parse_arxiv_page(
        query=query(),
        payload=arxiv_response_bytes(),
        raw_response_artifact=artifact(),
        max_hits=5,
    )
    openalex = parse_openalex_page(
        query=query(),
        payload=response_bytes(),
        raw_response_artifact=artifact(),
        max_hits=5,
    )

    assert len(parsed.hits) == 1
    hit = parsed.hits[0]
    assert hit.provider == "arxiv"
    assert hit.provider_record_id == "2401.01234"
    assert hit.arxiv_id == "2401.01234"
    assert hit.doi == "10.1000/abc"
    assert hit.published_year == 2024
    assert hit.authors == ("Ada Example",)
    assert hit.keywords == ("cond-mat.mtrl-sci", "cond-mat.str-el")
    assert hit.document_id == openalex.hits[0].document_id
    assert normalize_arxiv_id("https://arxiv.org/abs/2401.01234v9") == "2401.01234"


def test_parse_arxiv_page_rechecks_year_and_rejects_dtd() -> None:
    filtered = parse_arxiv_page(
        query=query(),
        payload=arxiv_response_bytes(published_year=2024),
        raw_response_artifact=artifact(),
        max_hits=5,
        publication_year_from=1960,
        publication_year_to=1990,
    )
    assert filtered.hits == ()
    assert filtered.warnings == ("PUBLICATION_YEAR_REJECTED:2401.01234",)

    unsafe = arxiv_response_bytes().replace(
        b"<feed ",
        b"<!DOCTYPE feed [<!ENTITY x 'bad'>]><feed ",
        1,
    )
    with pytest.raises(SearchAdapterError) as error:
        parse_arxiv_page(
            query=query(),
            payload=unsafe,
            raw_response_artifact=artifact(),
            max_hits=5,
        )
    assert error.value.code == "UNSAFE_XML"


def test_public_search_factory_supports_arxiv_single_and_multi_source() -> None:
    single = public_search_adapter_from_environment(
        budget=SearchBudgetV1(
            max_queries=2,
            max_physical_requests=2,
            max_direct_queries=2,
            max_bridge_queries=0,
            max_counter_queries=0,
        ),
        environment={"MATERIAL_AGENT_INSPIRATION_SEARCH_PROVIDER": "arxiv"},
        arxiv_transport=_RecordingTransport(arxiv_response_bytes()),
    )
    assert isinstance(single, ArxivPublicAdapter)

    multi = public_search_adapter_from_environment(
        budget=SearchBudgetV1(
            max_queries=2,
            max_physical_requests=4,
            max_direct_queries=2,
            max_bridge_queries=0,
            max_counter_queries=0,
        ),
        environment={
            "MATERIAL_AGENT_INSPIRATION_SEARCH_PROVIDER": "crossref+arxiv"
        },
        crossref_transport=_RecordingTransport(crossref_response_bytes()),
        arxiv_transport=_RecordingTransport(arxiv_response_bytes()),
    )
    assert isinstance(multi, MultiSourceSearchAdapter)
    assert {item.component.component_id for item in multi.adapters} == {
        "arxiv-public-adapter",
        "crossref-public-adapter",
    }


def test_osti_adapter_builds_bounded_historical_query() -> None:
    payload = osti_response_bytes()
    transport = _RecordingTransport(payload)
    adapter = OstiPublicAdapter(
        max_results=7,
        timeout_seconds=9,
        publication_year_from=1960,
        publication_year_to=1990,
        transport=transport,
    )

    page = adapter.search(
        query(),
        max_response_bytes=len(payload),
        max_physical_requests=1,
    )

    assert page.provider == "osti"
    parameters = parse_qs(urlsplit(str(transport.calls[0]["url"])).query)
    assert parameters == {
        "page": ["1"],
        "publication_date_end": ["12/31/1990"],
        "publication_date_start": ["01/01/1960"],
        "q": [query().text],
        "rows": ["7"],
    }
    assert page.attempts[0].response_bytes == len(payload)


def test_parse_osti_page_strips_markup_and_deduplicates_by_doi() -> None:
    parsed = parse_osti_page(
        query=query(),
        payload=osti_response_bytes(),
        raw_response_artifact=artifact(),
        max_hits=5,
        publication_year_from=1960,
        publication_year_to=1990,
    )
    crossref = parse_crossref_page(
        query=query(),
        payload=crossref_response_bytes(),
        raw_response_artifact=artifact(),
        max_hits=5,
    )

    assert len(parsed.hits) == 1
    hit = parsed.hits[0]
    assert hit.provider == "osti"
    assert hit.provider_record_id == "1234567"
    assert hit.title == "Flat-band behavior in layered Ti compounds"
    assert hit.published_year == 1987
    assert hit.doi == "10.1000/crossref"
    assert hit.authors == ("Ada Example [National Laboratory]",)
    assert hit.keywords == (
        "Materials Science",
        "Condensed Matter Physics",
        "Technical Report",
    )
    assert hit.document_id == crossref.hits[0].document_id


def test_parse_osti_page_rechecks_year_and_schema() -> None:
    filtered = parse_osti_page(
        query=query(),
        payload=osti_response_bytes(published_year=1987),
        raw_response_artifact=artifact(),
        max_hits=5,
        publication_year_from=2000,
        publication_year_to=2026,
    )
    assert filtered.hits == ()
    assert filtered.warnings == ("PUBLICATION_YEAR_REJECTED:1234567",)

    with pytest.raises(SearchAdapterError) as error:
        parse_osti_page(
            query=query(),
            payload=b'{"records": []}',
            raw_response_artifact=artifact(),
            max_hits=5,
        )
    assert error.value.code == "SCHEMA_DRIFT"


def test_public_search_factory_supports_crossref_arxiv_osti() -> None:
    multi = public_search_adapter_from_environment(
        budget=SearchBudgetV1(
            max_queries=2,
            max_physical_requests=6,
            max_direct_queries=2,
            max_bridge_queries=0,
            max_counter_queries=0,
        ),
        environment={
            "MATERIAL_AGENT_INSPIRATION_SEARCH_PROVIDER": "crossref+arxiv+osti",
            "MATERIAL_AGENT_INSPIRATION_SEARCH_MAX_RESULTS": "20",
        },
        crossref_transport=_RecordingTransport(crossref_response_bytes()),
        arxiv_transport=_RecordingTransport(arxiv_response_bytes()),
        osti_transport=_RecordingTransport(osti_response_bytes()),
    )
    assert isinstance(multi, MultiSourceSearchAdapter)
    assert [item.component.component_id for item in multi.adapters] == [
        "crossref-public-adapter",
        "arxiv-public-adapter",
        "osti-public-adapter",
    ]
    assert all(item.max_results == 20 for item in multi.adapters)
    page = multi.search(
        query(),
        max_response_bytes=1_000_000,
        max_physical_requests=3,
    )
    parsed = parse_multi_source_page(
        query=query(),
        payload=page.payload,
        raw_response_artifact=artifact(),
        max_hits=10,
    )
    assert {hit.provider for hit in parsed.hits} == {"arxiv", "crossref", "osti"}


@pytest.mark.parametrize("value", ["0", "21", "many"])
def test_public_search_factory_rejects_invalid_max_results(value: str) -> None:
    with pytest.raises(ValueError, match="must be an integer from 1 to 20"):
        public_search_adapter_from_environment(
            budget=SearchBudgetV1(),
            environment={
                "MATERIAL_AGENT_INSPIRATION_SEARCH_MAX_RESULTS": value,
            },
        )


def test_openalex_missing_secret_fails_before_network() -> None:
    transport = _RecordingTransport(response_bytes())
    adapter = OpenAlexPublicAdapter(
        transport=transport,
        api_key_resolver=lambda: "",
    )

    with pytest.raises(SearchAdapterError) as error:
        adapter.search(query(), max_response_bytes=100_000)

    assert error.value.code == "OPENALEX_CREDENTIAL_UNAVAILABLE"
    assert transport.calls == []


def test_multi_source_adapter_preserves_exact_child_payloads_and_parses_both() -> None:
    crossref_payload = crossref_response_bytes()
    openalex_payload = response_bytes()
    adapter = MultiSourceSearchAdapter(
        (
            CrossrefPublicAdapter(
                transport=_RecordingTransport(crossref_payload),
                max_retries=0,
            ),
            OpenAlexPublicAdapter(
                transport=_RecordingTransport(openalex_payload),
                api_key_resolver=lambda: "offline-test-key",
            ),
        )
    )

    page = adapter.search(
        query(),
        max_response_bytes=1_000_000,
        max_physical_requests=2,
    )
    parsed = parse_multi_source_page(
        query=query(),
        payload=page.payload,
        raw_response_artifact=artifact(),
        max_hits=10,
    )

    assert page.provider == "multi-source-v1"
    assert [attempt.attempt_number for attempt in page.attempts] == [1, 2]
    assert {hit.provider for hit in parsed.hits} == {"crossref", "openalex"}
    assert len(parsed.hits) == 2

    historical_only = parse_multi_source_page(
        query=query(),
        payload=page.payload,
        raw_response_artifact=artifact(),
        max_hits=10,
        publication_year_from=1960,
        publication_year_to=1990,
    )
    assert historical_only.hits == ()
    assert len(historical_only.warnings) == 2
    assert all(
        warning.startswith("PUBLICATION_YEAR_REJECTED:")
        for warning in historical_only.warnings
    )


def test_multi_source_search_is_fail_open_with_explicit_provider_warning() -> None:
    adapter = MultiSourceSearchAdapter(
        (
            CrossrefPublicAdapter(
                transport=_RecordingTransport(crossref_response_bytes()),
                max_retries=0,
            ),
            OpenAlexPublicAdapter(
                transport=_RecordingTransport(response_bytes()),
                api_key_resolver=lambda: "",
            ),
        )
    )

    page = adapter.search(
        query(),
        max_response_bytes=1_000_000,
        max_physical_requests=2,
    )
    parsed = parse_multi_source_page(
        query=query(),
        payload=page.payload,
        raw_response_artifact=artifact(),
        max_hits=10,
    )

    assert [hit.provider for hit in parsed.hits] == ["crossref"]
    assert "PROVIDER_FAILED:openalex:OPENALEX_CREDENTIAL_UNAVAILABLE" in parsed.warnings
    envelope = json.loads(page.payload)
    assert envelope["schema_version"] == "inspiration-multi-source-page-v2"
    assert envelope["failures"][0]["provider"] == "openalex"


def test_multi_source_hit_budget_is_fair_across_providers() -> None:
    crossref_payload = json.loads(crossref_response_bytes())
    original = crossref_payload["message"]["items"][0]
    crossref_payload["message"]["items"] = [
        {**original, "DOI": f"10.1000/CROSSREF-{index}", "title": [f"Paper {index}"]}
        for index in range(3)
    ]
    adapter = MultiSourceSearchAdapter(
        (
            CrossrefPublicAdapter(
                transport=_RecordingTransport(json.dumps(crossref_payload).encode()),
                max_results=3,
                max_retries=0,
            ),
            OpenAlexPublicAdapter(
                transport=_RecordingTransport(response_bytes()),
                api_key_resolver=lambda: "offline-test-key",
            ),
        )
    )

    page = adapter.search(
        query(),
        max_response_bytes=1_000_000,
        max_physical_requests=2,
    )
    parsed = parse_multi_source_page(
        query=query(),
        payload=page.payload,
        raw_response_artifact=artifact(),
        max_hits=2,
    )

    assert [hit.provider for hit in parsed.hits] == ["crossref", "openalex"]


def test_multi_source_parser_registry_accepts_new_provider_without_branch_change() -> None:
    class CustomAdapter:
        network_access = True
        component = ComponentSnapshotV1(
            component_id="custom-public-adapter",
            version="1",
            implementation_sha256="f" * 64,
        )

        def search(self, selected_query, **_kwargs):
            return RawSearchPage(
                provider="custom",
                query_id=selected_query.query_id,
                payload=b"{}",
            )

    adapter = MultiSourceSearchAdapter(
        (
            CustomAdapter(),
            CrossrefPublicAdapter(
                transport=_RecordingTransport(crossref_response_bytes()),
                max_retries=0,
            ),
        )
    )
    page = adapter.search(
        query(),
        max_response_bytes=1_000_000,
        max_physical_requests=2,
    )
    parsed = parse_multi_source_page(
        query=query(),
        payload=page.payload,
        raw_response_artifact=artifact(),
        max_hits=10,
        provider_parsers={
            "custom": lambda **_kwargs: ParsedSearchPage(hits=()),
            "crossref": parse_crossref_page,
        },
    )

    assert [hit.provider for hit in parsed.hits] == ["crossref"]


@pytest.mark.parametrize(
    ("year_from", "year_to"),
    ((1599, None), (None, 2201), (1991, 1990)),
)
def test_public_search_adapters_reject_invalid_year_ranges(
    year_from: int | None,
    year_to: int | None,
) -> None:
    with pytest.raises(ValueError):
        CrossrefPublicAdapter(
            publication_year_from=year_from,
            publication_year_to=year_to,
        )
    with pytest.raises(ValueError):
        OpenAlexPublicAdapter(
            publication_year_from=year_from,
            publication_year_to=year_to,
        )


def test_public_search_factory_is_crossref_by_default_and_multi_source_is_opt_in() -> None:
    default = public_search_adapter_from_environment(
        budget=SearchBudgetV1(
            publication_year_from=1960,
            publication_year_to=1990,
        ),
        environment={},
        crossref_transport=_RecordingTransport(crossref_response_bytes()),
    )
    assert isinstance(default, CrossrefPublicAdapter)
    assert default.publication_year_from == 1960
    assert default.publication_year_to == 1990

    multi = public_search_adapter_from_environment(
        budget=SearchBudgetV1(
            max_queries=2,
            max_physical_requests=4,
            max_direct_queries=2,
            max_bridge_queries=0,
            max_counter_queries=0,
            publication_year_from=1960,
            publication_year_to=1990,
        ),
        environment={
            "MATERIAL_AGENT_INSPIRATION_SEARCH_PROVIDER": "crossref+openalex",
            "OPENALEX_API_KEY": "offline-test-key",
        },
        crossref_transport=_RecordingTransport(crossref_response_bytes()),
        openalex_transport=_RecordingTransport(response_bytes()),
    )
    assert isinstance(multi, MultiSourceSearchAdapter)
    assert {adapter.component.component_id for adapter in multi.adapters} == {
        "crossref-public-adapter",
        "openalex-public-adapter",
    }

    with pytest.raises(ValueError, match="two physical requests"):
        public_search_adapter_from_environment(
            budget=SearchBudgetV1(
                max_queries=2,
                max_physical_requests=2,
                max_direct_queries=2,
                max_bridge_queries=0,
                max_counter_queries=0,
            ),
            environment={
                "MATERIAL_AGENT_INSPIRATION_SEARCH_PROVIDER": "crossref+openalex"
            },
        )


def test_public_runner_factory_binds_opt_in_multi_source_policy(tmp_path) -> None:
    policy = InspirationPolicyV1(
        search_mode=SearchExecutionMode.PUBLIC_METADATA_API,
        network_access=True,
        search=SearchBudgetV1(
            max_queries=2,
            max_physical_requests=4,
            max_direct_queries=2,
            max_bridge_queries=0,
            max_counter_queries=0,
            publication_year_from=1960,
            publication_year_to=1990,
        ),
    )
    runner = public_inspiration_runner_from_environment(
        store=LocalArtifactStore(tmp_path),
        policy=policy,
        transformation_engine=_NoopTransformationEngine(),
        environment={
            "MATERIAL_AGENT_INSPIRATION_SEARCH_PROVIDER": "crossref+openalex",
            "OPENALEX_API_KEY": "offline-test-key",
        },
        crossref_transport=_RecordingTransport(crossref_response_bytes()),
        openalex_transport=_RecordingTransport(response_bytes()),
    )

    assert isinstance(runner.search_adapter, MultiSourceSearchAdapter)
    assert all(
        adapter.publication_year_from == 1960
        and adapter.publication_year_to == 1990
        for adapter in runner.search_adapter.adapters
    )
