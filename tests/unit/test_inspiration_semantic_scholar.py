from __future__ import annotations

import json
from datetime import UTC, datetime
from urllib.parse import parse_qs, urlsplit

import pytest
from pydantic import ValidationError

from material_agent.inspiration.models import (
    ArtifactPointerV1,
    SearchQueryKind,
    SearchQueryV1,
)
from material_agent.inspiration.query_context import (
    AnchorPolarity,
    LiteratureAnchorV2,
    MaterialAliasSetV2,
    ReviewedQueryHintsV2,
    compile_query_context_v2,
)
from material_agent.inspiration.search import SearchAdapterError
from material_agent.inspiration.semantic_scholar import (
    SemanticScholarPublicAdapter,
    SemanticScholarRequestV1,
    SemanticScholarRoute,
    SemanticScholarTopicSearchAdapter,
    compile_semantic_scholar_requests,
    parse_semantic_scholar_page,
    parse_semantic_scholar_topic_page,
)
from material_agent.retrieval.models import (
    CandidateAuditRecordV2,
    Decision,
    HardConstraints,
    Requirement,
    ScientificTarget,
    SourceDatabase,
)


class _RecordingTransport:
    def __init__(self, payload: bytes) -> None:
        self.payload = payload
        self.calls: list[dict[str, object]] = []

    def get(self, url: str, **kwargs: object) -> bytes:
        self.calls.append({"url": url, **kwargs})
        return self.payload


def _context():
    requirement = Requirement(
        requirement_id="req-s2",
        revision=1,
        target_class="two-dimensional dichalcogenide",
        hard_constraints=HardConstraints(exact_formula="TiS2", dimensionality=2),
        scientific_targets=[ScientificTarget(name="electronic flat band")],
        confirmed_by_user=True,
        policy_version="test-v1",
    )
    candidates = (
        CandidateAuditRecordV2(
            candidate_id="cand-tis2",
            formula="TiS2",
            reduced_formula="TiS2",
            source_database=SourceDatabase.C2DB,
            source_database_version="test-v1",
            source_material_id="c2db-tis2",
            source_last_updated=datetime(2026, 8, 14, tzinfo=UTC),
            query_id="agent01-query",
            decision=Decision.PASS,
            publication_rank=1,
            published_downstream=True,
        ),
    )
    hints = ReviewedQueryHintsV2(
        material_aliases=(
            MaterialAliasSetV2(formula="TiS2", aliases=("titanium disulfide",)),
        ),
        anchors=(
            LiteratureAnchorV2(
                provider_record_id="10.1000/positive",
                title="Positive material anchor",
                polarity=AnchorPolarity.POSITIVE,
            ),
            LiteratureAnchorV2(
                provider_record_id="CorpusId:1234",
                title="Negative acoustic anchor",
                polarity=AnchorPolarity.NEGATIVE,
            ),
        ),
    )
    return compile_query_context_v2(
        requirement=requirement,
        parent_candidates=candidates,
        raw_request="Study TiS2 flat band by chalcogen substitution",
        reviewed_hints=hints,
        publication_year_from=1960,
        publication_year_to=2026,
    )


def _paper(paper_id: str = "abc") -> dict[str, object]:
    return {
        "paperId": paper_id,
        "externalIds": {"DOI": "10.1000/S2", "ArXiv": "2601.00001"},
        "url": "https://www.semanticscholar.org/paper/abc",
        "title": "Flat electronic bands in titanium disulfide",
        "abstract": "A bounded materials-physics abstract.",
        "venue": "Test Journal",
        "year": 1981,
        "authors": [{"authorId": "1", "name": "Ada Example"}],
        "fieldsOfStudy": ["Materials Science", "Physics"],
        "citationCount": 20,
        "referenceCount": 10,
    }


def _pointer() -> ArtifactPointerV1:
    return ArtifactPointerV1(
        uri="artifact://stages/inspiration/run/raw_search/s2.json",
        sha256="a" * 64,
        media_type="application/json",
    )


def _topic_query() -> SearchQueryV1:
    return SearchQueryV1(
        query_id="query-common-topic",
        kind=SearchQueryKind.DIRECT,
        text="layered transition-metal flat band",
        tag_ids=("flat-band",),
    )


def test_request_compiler_builds_autosci_style_routes() -> None:
    requests = compile_semantic_scholar_requests(_context())

    assert tuple(item.route for item in requests) == (
        SemanticScholarRoute.TOPIC,
        SemanticScholarRoute.RECOMMENDATIONS,
        SemanticScholarRoute.REFERENCES,
        SemanticScholarRoute.CITATIONS,
        SemanticScholarRoute.RECOMMENDATIONS,
    )
    assert requests[0].query_text is not None
    assert "TiS2" in requests[0].query_text
    assert "titanium disulfide" in requests[0].query_text
    assert requests[1].anchor_paper_id == "DOI:10.1000/positive"
    assert requests[-1].anchor_polarity is AnchorPolarity.NEGATIVE
    assert len({item.query_id for item in requests}) == len(requests)


def test_adapter_builds_bounded_topic_request_and_keeps_key_out_of_url() -> None:
    request = compile_semantic_scholar_requests(_context())[0]
    transport = _RecordingTransport(json.dumps({"data": []}).encode())
    adapter = SemanticScholarPublicAdapter(
        api_key_resolver=lambda: "secret-test-key",
        transport=transport,
        monotonic_clock=lambda: 100.0,
    )

    page = adapter.search(
        request,
        max_response_bytes=10_000,
        remaining_walltime_seconds=5.0,
        max_physical_requests=1,
    )

    assert page.provider == "semantic-scholar"
    call = transport.calls[0]
    url = str(call["url"])
    parsed = urlsplit(url)
    parameters = parse_qs(parsed.query)
    assert parsed.netloc == "api.semanticscholar.org"
    assert parsed.path == "/graph/v1/paper/search"
    assert parameters["year"] == ["1960-2026"]
    assert "secret-test-key" not in url
    assert call["headers"]["x-api-key"] == "secret-test-key"  # type: ignore[index]
    assert call["deadline_monotonic"] == 105.0


def test_common_topic_adapter_and_parser_preserve_common_query_identity() -> None:
    transport = _RecordingTransport(json.dumps({"data": [_paper()]}).encode())
    adapter = SemanticScholarTopicSearchAdapter(
        max_results=7,
        publication_year_from=1960,
        publication_year_to=2026,
        transport=transport,
    )

    page = adapter.search(
        _topic_query(),
        max_response_bytes=10_000,
        max_physical_requests=1,
    )
    parsed = parse_semantic_scholar_topic_page(
        query=_topic_query(),
        payload=page.payload,
        raw_response_artifact=_pointer(),
        max_hits=7,
        publication_year_from=1960,
        publication_year_to=2026,
    )

    parameters = parse_qs(urlsplit(str(transport.calls[0]["url"])).query)
    assert parameters["query"] == [_topic_query().text]
    assert parameters["limit"] == ["7"]
    assert len(parsed.hits) == 1
    assert parsed.hits[0].query_ids == ("query-common-topic",)


def test_parser_maps_topic_metadata_with_local_year_check() -> None:
    request = compile_semantic_scholar_requests(_context())[0]
    payload = json.dumps({"data": [_paper(), {**_paper("future"), "year": 2030}]}).encode()

    parsed = parse_semantic_scholar_page(
        request=request,
        payload=payload,
        raw_response_artifact=_pointer(),
    )

    assert len(parsed.hits) == 1
    hit = parsed.hits[0]
    assert hit.provider == "semantic-scholar"
    assert hit.doi == "10.1000/s2"
    assert hit.arxiv_id == "2601.00001"
    assert hit.published_year == 1981
    assert hit.keywords == ("Materials Science", "Physics")
    assert parsed.warnings == ("SEMANTIC_SCHOLAR_YEAR_FILTERED:future",)


@pytest.mark.parametrize(
    ("route", "field"),
    (
        (SemanticScholarRoute.REFERENCES, "citedPaper"),
        (SemanticScholarRoute.CITATIONS, "citingPaper"),
    ),
)
def test_parser_maps_reference_and_citation_edges(
    route: SemanticScholarRoute,
    field: str,
) -> None:
    context = _context()
    request = next(
        item
        for item in compile_semantic_scholar_requests(context)
        if item.route is route
    )
    payload = json.dumps(
        {"data": [{"contexts": [], "intents": [], field: _paper()}]}
    ).encode()

    parsed = parse_semantic_scholar_page(
        request=request,
        payload=payload,
        raw_response_artifact=_pointer(),
    )

    assert len(parsed.hits) == 1
    assert parsed.hits[0].query_ids == (request.query_id,)


def test_zero_physical_budget_fails_before_transport() -> None:
    request = compile_semantic_scholar_requests(_context())[0]
    transport = _RecordingTransport(b"{}")
    adapter = SemanticScholarPublicAdapter(transport=transport)

    with pytest.raises(SearchAdapterError, match="SEARCH_REQUEST_BUDGET_EXCEEDED"):
        adapter.search(
            request,
            max_response_bytes=1_000,
            max_physical_requests=0,
        )

    assert transport.calls == []


def test_request_contract_rejects_negative_reference_route() -> None:
    payload = {
        "schema_version": "semantic-scholar-request-v1",
        "context_id": _context().context_id,
        "route": SemanticScholarRoute.REFERENCES,
        "query_text": None,
        "anchor_paper_id": "CorpusId:1234",
        "anchor_polarity": AnchorPolarity.NEGATIVE,
        "max_results": 10,
        "publication_year_from": 1960,
        "publication_year_to": 2026,
    }
    from material_agent.inspiration.models import deterministic_id

    with pytest.raises(ValidationError, match="negative anchors"):
        SemanticScholarRequestV1(
            query_id=deterministic_id("s2-query", payload),
            **payload,
        )
