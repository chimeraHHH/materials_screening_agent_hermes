from __future__ import annotations

import json
from datetime import datetime, timezone
from email.message import Message
from urllib.error import HTTPError
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
    UrlLibBoundedTransport,
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
        tz=timezone.utc,
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
