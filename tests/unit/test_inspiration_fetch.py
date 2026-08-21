from __future__ import annotations

import hashlib
from collections.abc import Mapping

import pytest

from material_agent.inspiration.fetch import (
    DisabledDocumentFetcher,
    DocumentFetchError,
    DocumentFetchErrorCategory,
    DocumentFetchRequest,
    FetchAllowance,
    FixtureDocumentFetcher,
    FixtureFetchResponse,
    SafeNetworkDocumentFetcher,
)

URL = "https://articles.example.test/paper?token=secret"
REQUEST = DocumentFetchRequest(
    request_id="fetch-1",
    document_id="document-1",
    url=URL,
)


def allowance(**overrides: object) -> FetchAllowance:
    values: dict[str, object] = {
        "remaining_requests": 4,
        "remaining_total_bytes": 100,
        "max_bytes_per_response": 50,
        "timeout_seconds": 3,
        "max_retries_per_request": 1,
        "allow_html": True,
        "allow_jats_xml": True,
    }
    values.update(overrides)
    return FetchAllowance(**values)  # type: ignore[arg-type]


class Response:
    def __init__(
        self,
        status: int,
        body: bytes = b"",
        headers: Mapping[str, str] | None = None,
        *,
        fail_after_first_chunk: bool = False,
    ) -> None:
        self.status_code = status
        self.headers = dict(headers or {})
        self.body = body
        self.offset = 0
        self.read_calls = 0
        self.closed = False
        self.fail_after_first_chunk = fail_after_first_chunk

    def read(self, size: int) -> bytes:
        self.read_calls += 1
        if self.fail_after_first_chunk and self.read_calls > 1:
            raise OSError("injected partial read")
        start = self.offset
        end = min(len(self.body), start + size)
        self.offset = end
        return self.body[start:end]

    def close(self) -> None:
        self.closed = True


class SequenceTransport:
    def __init__(self, events: list[Response | Exception]) -> None:
        self.events = list(events)
        self.calls: list[tuple[str, Mapping[str, str], int]] = []

    def open(
        self,
        url: str,
        *,
        headers: Mapping[str, str],
        timeout_seconds: int,
    ) -> Response:
        self.calls.append((url, dict(headers), timeout_seconds))
        event = self.events.pop(0)
        if isinstance(event, Exception):
            raise event
        return event


def fetcher(transport: SequenceTransport, **overrides: object):
    values: dict[str, object] = {
        "allowed_hosts": ("articles.example.test",),
        "transport": transport,
        "address_resolver": lambda host, port: ("8.8.8.8",),
        "retry_backoff_seconds": 0,
        "max_retry_delay_seconds": 0,
        "max_total_wait_seconds": 0,
    }
    values.update(overrides)
    return SafeNetworkDocumentFetcher(**values)  # type: ignore[arg-type]


def test_safe_fetch_returns_hashes_safe_urls_and_stable_attempt_record() -> None:
    response = Response(
        200,
        b"<html>evidence</html>",
        {"Content-Type": "text/html; charset=utf-8", "Content-Length": "21"},
    )
    transport = SequenceTransport([response])

    result = fetcher(transport).fetch(REQUEST, allowance=allowance())

    assert result.requested_url == "https://articles.example.test/paper"
    assert result.resolved_url == "https://articles.example.test/paper"
    assert result.requested_url_sha256 == hashlib.sha256(URL.encode()).hexdigest()
    assert result.payload_sha256 == hashlib.sha256(result.payload).hexdigest()
    assert result.response_bytes == len(result.payload)
    assert result.attempts[0].to_dict()["outcome"] == "success"
    assert "token" not in result.attempts[0].request_url
    assert transport.calls[0][1]["Accept-Encoding"] == "identity"
    assert "application/pdf" not in transport.calls[0][1]["Accept"]
    assert response.closed is True


@pytest.mark.parametrize(
    ("url", "code"),
    [
        ("http://articles.example.test/a", "HTTPS_REQUIRED"),
        ("https://user:pw@articles.example.test/a", "URL_USERINFO_FORBIDDEN"),
        ("https://127.0.0.1/a", "IP_LITERAL_FORBIDDEN"),
        ("https://[::1]/a", "IP_LITERAL_FORBIDDEN"),
        ("https://localhost/a", "LOCALHOST_FORBIDDEN"),
        ("https://articles.example.test:8443/a", "NON_STANDARD_HTTPS_PORT"),
        ("https://untrusted.example/a", "DOCUMENT_HOST_NOT_ALLOWLISTED"),
        ("https://articles.example.test/a#fragment", "URL_FRAGMENT_FORBIDDEN"),
    ],
)
def test_unsafe_url_is_rejected_before_transport(url: str, code: str) -> None:
    transport = SequenceTransport([])
    request = DocumentFetchRequest(request_id="fetch-1", document_id="doc-1", url=url)

    with pytest.raises(DocumentFetchError) as raised:
        fetcher(transport).fetch(request, allowance=allowance())

    assert raised.value.code == code
    assert raised.value.category is DocumentFetchErrorCategory.SECURITY
    assert raised.value.attempts == ()
    assert transport.calls == []


def test_private_dns_result_is_rejected_before_transport() -> None:
    transport = SequenceTransport([])
    selected = fetcher(
        transport,
        address_resolver=lambda host, port: ("10.0.0.4",),
    )

    with pytest.raises(DocumentFetchError) as raised:
        selected.fetch(REQUEST, allowance=allowance())

    assert raised.value.code == "NON_PUBLIC_RESOLVED_ADDRESS"
    assert raised.value.category is DocumentFetchErrorCategory.SECURITY
    assert transport.calls == []


def test_cross_host_redirect_is_recorded_then_rejected_without_second_request() -> None:
    transport = SequenceTransport(
        [Response(302, headers={"Location": "https://evil.example/full"})]
    )

    with pytest.raises(DocumentFetchError) as raised:
        fetcher(transport).fetch(REQUEST, allowance=allowance())

    assert raised.value.code == "DOCUMENT_HOST_NOT_ALLOWLISTED"
    assert len(raised.value.attempts) == 1
    assert raised.value.attempts[0].outcome == "redirect"
    assert raised.value.attempts[0].redirect_url == "https://evil.example/full"
    assert len(transport.calls) == 1


def test_each_allowlisted_redirect_hop_is_a_physical_attempt() -> None:
    redirect = Response(302, headers={"Location": "/resolved?ticket=private"})
    success = Response(
        200,
        b"<html>ok</html>",
        {"Content-Type": "text/html", "Content-Length": "15"},
    )
    transport = SequenceTransport([redirect, success])

    result = fetcher(transport).fetch(REQUEST, allowance=allowance())

    assert [attempt.outcome for attempt in result.attempts] == ["redirect", "success"]
    assert result.attempts[0].redirect_url == "https://articles.example.test/resolved"
    assert result.resolved_url == "https://articles.example.test/resolved"
    assert len(transport.calls) == 2


def test_redirect_hop_cannot_exceed_physical_request_budget() -> None:
    transport = SequenceTransport(
        [Response(302, headers={"Location": "/next"})]
    )

    with pytest.raises(DocumentFetchError) as raised:
        fetcher(transport).fetch(
            REQUEST,
            allowance=allowance(remaining_requests=1),
        )

    assert raised.value.code == "FETCH_REQUEST_BUDGET_EXHAUSTED"
    assert raised.value.category is DocumentFetchErrorCategory.BUDGET
    assert len(raised.value.attempts) == 1


@pytest.mark.parametrize("status", [408, 429, 500, 502, 503, 504])
def test_allowlisted_transient_http_status_retries_once(status: int) -> None:
    transport = SequenceTransport(
        [
            Response(status),
            Response(200, b"{}", {"Content-Type": "application/json"}),
        ]
    )

    result = fetcher(transport).fetch(REQUEST, allowance=allowance())

    assert [attempt.outcome for attempt in result.attempts] == ["error", "success"]
    assert result.attempts[0].error_code == "TRANSIENT_HTTP_ERROR"
    assert result.attempts[0].http_status == status
    assert len(transport.calls) == 2


def test_permanent_http_status_is_not_retried() -> None:
    transport = SequenceTransport([Response(404)])

    with pytest.raises(DocumentFetchError) as raised:
        fetcher(transport).fetch(REQUEST, allowance=allowance())

    assert raised.value.code == "HTTP_ERROR"
    assert raised.value.category is DocumentFetchErrorCategory.PERMANENT
    assert len(transport.calls) == 1


def test_pdf_mime_is_rejected_without_reading_body() -> None:
    response = Response(200, b"%PDF-1.7", {"Content-Type": "application/pdf"})

    with pytest.raises(DocumentFetchError) as raised:
        fetcher(SequenceTransport([response])).fetch(REQUEST, allowance=allowance())

    assert raised.value.code == "PDF_CONTENT_FORBIDDEN"
    assert raised.value.category is DocumentFetchErrorCategory.SECURITY
    assert raised.value.attempts[0].response_bytes == 0
    assert response.read_calls == 0


def test_pdf_magic_is_rejected_after_bounded_read_and_charged() -> None:
    payload = b" \n%PDF-1.7"
    response = Response(200, payload, {"Content-Type": "text/html"})

    with pytest.raises(DocumentFetchError) as raised:
        fetcher(SequenceTransport([response])).fetch(REQUEST, allowance=allowance())

    assert raised.value.code == "PDF_CONTENT_FORBIDDEN"
    assert raised.value.attempts[0].response_bytes == len(payload)


@pytest.mark.parametrize(
    ("media_type", "overrides", "accepted"),
    [
        ("text/html", {"allow_html": False}, False),
        ("application/jats+xml", {"allow_jats_xml": False}, False),
        ("application/json", {"allow_html": False, "allow_jats_xml": False}, True),
        ("application/octet-stream", {}, False),
    ],
)
def test_media_type_is_controlled_only_by_allowance(
    media_type: str,
    overrides: dict[str, object],
    accepted: bool,
) -> None:
    transport = SequenceTransport(
        [Response(200, b"{}", {"Content-Type": media_type})]
    )
    if accepted:
        assert fetcher(transport).fetch(
            REQUEST,
            allowance=allowance(**overrides),
        ).payload == b"{}"
    else:
        with pytest.raises(DocumentFetchError) as raised:
            fetcher(transport).fetch(REQUEST, allowance=allowance(**overrides))
        assert raised.value.code == "UNSUPPORTED_MEDIA_TYPE"


def test_content_length_is_rejected_before_body_read() -> None:
    response = Response(
        200,
        b"ignored",
        {"Content-Type": "text/html", "Content-Length": "51"},
    )

    with pytest.raises(DocumentFetchError) as raised:
        fetcher(SequenceTransport([response])).fetch(REQUEST, allowance=allowance())

    assert raised.value.code == "FETCH_RESPONSE_BUDGET_EXCEEDED"
    assert response.read_calls == 0


def test_streaming_reader_uses_max_plus_one_and_records_budget_failure() -> None:
    payload = b"x" * 51
    response = Response(200, payload, {"Content-Type": "text/html"})

    with pytest.raises(DocumentFetchError) as raised:
        fetcher(SequenceTransport([response])).fetch(REQUEST, allowance=allowance())

    assert raised.value.code == "FETCH_RESPONSE_BUDGET_EXCEEDED"
    assert raised.value.attempts[0].response_bytes == 51


def test_partial_retry_bytes_reduce_remaining_total_budget() -> None:
    partial = Response(
        200,
        b"abc",
        {"Content-Type": "text/html"},
        fail_after_first_chunk=True,
    )
    retry = Response(
        200,
        b"123456",
        {"Content-Type": "text/html", "Content-Length": "6"},
    )
    transport = SequenceTransport([partial, retry])

    with pytest.raises(DocumentFetchError) as raised:
        fetcher(transport).fetch(
            REQUEST,
            allowance=allowance(remaining_total_bytes=8, max_bytes_per_response=8),
        )

    assert raised.value.code == "FETCH_RESPONSE_BUDGET_EXCEEDED"
    assert [attempt.response_bytes for attempt in raised.value.attempts] == [3, 0]
    assert raised.value.attempts[0].error_code == "NETWORK_ERROR"
    assert retry.read_calls == 0


def test_fixture_fetcher_is_offline_and_component_binds_fixture_bytes() -> None:
    fixtures = {
        URL: FixtureFetchResponse(payload=b"<html>fixture</html>"),
    }
    first = FixtureDocumentFetcher(
        fixtures=fixtures,
        allowed_hosts=("articles.example.test",),
    )
    second = FixtureDocumentFetcher(
        fixtures=fixtures,
        allowed_hosts=("articles.example.test",),
    )

    result = first.fetch(REQUEST, allowance=allowance())

    assert first.network_access is False
    assert result.payload == b"<html>fixture</html>"
    assert first.component == second.component


def test_disabled_fetcher_fails_closed_without_an_attempt() -> None:
    with pytest.raises(DocumentFetchError) as raised:
        DisabledDocumentFetcher().fetch(REQUEST, allowance=allowance())

    assert raised.value.code == "DOCUMENT_FETCH_DISABLED"
    assert raised.value.category is DocumentFetchErrorCategory.PERMANENT
    assert raised.value.attempts == ()
