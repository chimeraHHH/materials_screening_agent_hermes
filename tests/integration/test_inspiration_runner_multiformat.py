from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

import pytest

from material_agent.inspiration.engine import PymatgenTransformationEngine
from material_agent.inspiration.fetch import (
    FixtureDocumentFetcher,
    FixtureFetchResponse,
)
from material_agent.inspiration.feedback import TagFeedbackReviewV1
from material_agent.inspiration.models import (
    ArtifactPointerV1,
    InspirationInputV1,
    ParentCandidateRefV1,
    PassageV1,
    PassageVectorV1,
    SearchQueryKind,
    SearchQueryV1,
    canonical_json_bytes,
)
from material_agent.inspiration.policy import (
    BridgeSearchPolicyV1,
    EmbeddingBudgetV1,
    FetchBudgetV1,
    InspirationPolicyV1,
    PassageBudgetV1,
    SearchBudgetV1,
    SelectionPolicyV1,
    TransformationBudgetV1,
)
from material_agent.inspiration.runner import (
    STRUCTURE_MEDIA_TYPE,
    InspirationRunner,
    InspirationRunnerError,
)
from material_agent.inspiration.search import FixtureSearchAdapter
from material_agent.inspiration.tag_graph import (
    curated_flat_band_tag_graph,
    plan_tag_queries,
)
from material_agent.inspiration.transformations import (
    DEFAULT_SUBSTITUTION_REGISTRY_V1,
    substitution_registry_bytes,
)
from material_agent.inspiration.vectorizer import (
    SIGNED_HASHING_SNAPSHOT,
    build_embedding_input_bytes,
)
from material_agent.integration.hermes_service import HermesFixtureProjector
from material_agent.retrieval.storage import LocalArtifactStore


FIXTURE_DIR = Path(__file__).parents[1] / "fixtures" / "inspiration"
ARTICLE_HOST = "articles.example.test"
ARTICLE_URLS = {
    "acoustic-resonance-to-electronic-flat-band": (
        f"https://{ARTICLE_HOST}/structured"
    ),
    "magnon-line-graph-to-electronic-flat-band": f"https://{ARTICLE_HOST}/jats",
    "photonic-interference-to-electronic-flat-band": (
        f"https://{ARTICLE_HOST}/plain"
    ),
}
BODY_FIXTURES = {
    ARTICLE_URLS["acoustic-resonance-to-electronic-flat-band"]: (
        "p33-structured.html",
        "text/html",
    ),
    ARTICLE_URLS["magnon-line-graph-to-electronic-flat-band"]: (
        "p33-jats.xml",
        "application/jats+xml",
    ),
    ARTICLE_URLS["photonic-interference-to-electronic-flat-band"]: (
        "p33-plain.html",
        "text/html",
    ),
}


def _pointer(reference) -> ArtifactPointerV1:
    return ArtifactPointerV1.model_validate(reference.model_dump(mode="python"))


def _read_jsonl(path: Path) -> tuple[dict[str, object], ...]:
    return tuple(
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    )


def _abstract_inverted_index(text: str) -> dict[str, list[int]]:
    inverted: dict[str, list[int]] = {}
    for position, token in enumerate(text.split()):
        inverted.setdefault(token, []).append(position)
    return inverted


def _openalex_response(
    query: SearchQueryV1,
    *,
    share_acoustic_magnon_document: bool = False,
) -> bytes:
    if query.kind is SearchQueryKind.DIRECT:
        title = "Electronic flat-band metadata control"
        abstract = (
            "This electronic flat band metadata control provides a bounded target "
            "description and enough relevant abstract words to skip article body "
            "retrieval while retaining an explicitly non-conclusive search record."
        )
        canonical_url = f"https://{ARTICLE_HOST}/must-not-fetch"
        keywords = ("electronic flat band",)
        suffix = "direct"
    else:
        assert query.bridge_rule_id is not None
        title, keywords, suffix = {
            "acoustic-resonance-to-electronic-flat-band": (
                "Acoustic local-resonance body calibration",
                ("acoustic metamaterial", "local resonance flat band"),
                "acoustic",
            ),
            "magnon-line-graph-to-electronic-flat-band": (
                "Magnon line-graph-localization body calibration",
                (
                    "frustrated magnetism flat magnon band",
                    "line graph flat band localization",
                ),
                "magnon",
            ),
            "photonic-interference-to-electronic-flat-band": (
                "Photonic compact-localized-state body calibration",
                (
                    "photonic lattice",
                    "compact localized state",
                    "destructive interference flat band",
                ),
                "photonic",
            ),
        }[query.bridge_rule_id]
        abstract = None
        canonical_url = ARTICLE_URLS[query.bridge_rule_id]

    identity_suffix = suffix
    if share_acoustic_magnon_document and suffix == "magnon":
        identity_suffix = "acoustic"
        canonical_url = ARTICLE_URLS[
            "acoustic-resonance-to-electronic-flat-band"
        ]

    record: dict[str, object] = {
        "authorships": [
            {"author": {"display_name": "P3.3 Offline Fixture Author"}}
        ],
        "display_name": title,
        "doi": f"https://doi.org/10.5555/p33.multiformat.{identity_suffix}",
        "id": f"https://openalex.org/WP33{suffix.upper()}",
        "keywords": [{"display_name": keyword} for keyword in keywords],
        "primary_location": {"landing_page_url": canonical_url},
        "publication_year": 2026,
        "title": title,
    }
    if abstract is not None:
        record["abstract_inverted_index"] = _abstract_inverted_index(abstract)
    return json.dumps(
        {"meta": {"count": 1}, "results": [record]},
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _policy(*, fetch_request_budget: int = 3) -> InspirationPolicyV1:
    return InspirationPolicyV1(
        policy_id="inspiration-p33-multiformat-fixture-v1",
        search=SearchBudgetV1(
            max_queries=4,
            max_direct_queries=1,
            max_bridge_queries=3,
            max_counter_queries=0,
            max_raw_hits=4,
            max_unique_documents=4,
        ),
        fetch=FetchBudgetV1(
            max_requests=fetch_request_budget,
            max_total_bytes=16_000,
            max_bytes_per_response=4_000,
            timeout_seconds=5,
            max_retries_per_request=0,
            allow_html=True,
            allow_jats_xml=True,
        ),
        passages=PassageBudgetV1(
            min_tokens=16,
            max_tokens=128,
            max_per_hit=3,
            max_total=10,
        ),
        embedding=EmbeddingBudgetV1(
            vector_dimension=32,
            max_passages=10,
            max_input_tokens=4_000,
        ),
        bridge=BridgeSearchPolicyV1(max_bridge_packets=3),
        transformation=TransformationBudgetV1(
            max_plans=1,
            max_plans_per_parent=1,
        ),
        selection=SelectionPolicyV1(
            top_k=1,
            min_mechanisms_when_available=1,
        ),
    )


@dataclass(frozen=True, slots=True)
class _CompletedRun:
    store: LocalArtifactStore
    runner: InspirationRunner
    result: object
    policy_pointer: ArtifactPointerV1
    graph_pointer: ArtifactPointerV1
    response_bytes: tuple[bytes, ...]

    @property
    def stage_root(self) -> Path:
        return self.store.root / "stages" / "inspiration" / "run-p33-multiformat"


def _run(
    workspace: Path,
    *,
    share_acoustic_magnon_document: bool = False,
    transient_body_failures: bool = False,
    fetch_request_budget: int = 3,
) -> _CompletedRun:
    store = LocalArtifactStore(workspace)
    policy = _policy(fetch_request_budget=fetch_request_budget)
    graph = curated_flat_band_tag_graph()
    queries = plan_tag_queries(
        graph,
        target_tag_ids=("electronic-flat-band",),
        budget=policy.search,
    ).queries
    assert len(queries) == 4
    assert sum(query.kind is SearchQueryKind.DIRECT for query in queries) == 1
    assert sum(query.kind is SearchQueryKind.BRIDGE for query in queries) == 3

    responses = {
        query.query_id: _openalex_response(
            query,
            share_acoustic_magnon_document=share_acoustic_magnon_document,
        )
        for query in queries
    }
    response_bindings: list[dict[str, object]] = []
    for query in queries:
        pointer = _pointer(
            store.write_bytes(
                f"inputs/search/{query.query_id}.json",
                responses[query.query_id],
                media_type="application/json",
                immutable=True,
            )
        )
        response_bindings.append(
            {
                "payload_artifact": pointer.model_dump(mode="json"),
                "provider": "openalex-fixture",
                "query_id": query.query_id,
            }
        )

    requirement_pointer = _pointer(
        store.write_bytes(
            "inputs/requirement.json",
            (FIXTURE_DIR / "requirement.json").read_bytes(),
            media_type="application/json",
            immutable=True,
        )
    )
    policy_pointer = _pointer(
        store.write_bytes(
            "inputs/policy.json",
            canonical_json_bytes(policy),
            media_type="application/json",
            immutable=True,
        )
    )
    graph_pointer = _pointer(
        store.write_bytes(
            "inputs/tag_graph.json",
            canonical_json_bytes(graph),
            media_type="application/json",
            immutable=True,
        )
    )
    registry_pointer = _pointer(
        store.write_bytes(
            "inputs/substitution_registry.json",
            substitution_registry_bytes(DEFAULT_SUBSTITUTION_REGISTRY_V1),
            media_type="application/json",
            immutable=True,
        )
    )
    search_fixture_pointer = _pointer(
        store.write_json(
            "inputs/search_fixture_manifest.json",
            {
                "responses": response_bindings,
                "schema_version": "inspiration-search-fixture-v1",
            },
            immutable=True,
        )
    )
    parent_pointer = _pointer(
        store.write_bytes(
            "inputs/parent-tis2.cif",
            (FIXTURE_DIR / "parent-tis2.cif").read_bytes(),
            media_type=STRUCTURE_MEDIA_TYPE,
            immutable=True,
        )
    )

    search_adapter = FixtureSearchAdapter(responses)
    fetcher = FixtureDocumentFetcher(
        fixtures={
            url: FixtureFetchResponse(
                payload=(
                    b""
                    if transient_body_failures
                    else (FIXTURE_DIR / filename).read_bytes()
                ),
                media_type=media_type,
                status_code=503 if transient_body_failures else 200,
            )
            for url, (filename, media_type) in BODY_FIXTURES.items()
        },
        allowed_hosts=(ARTICLE_HOST,),
    )
    inspiration_input = InspirationInputV1(
        project_id="project-p33-multiformat",
        request_id="request-p33-multiformat",
        run_id="run-p33-multiformat",
        requirement_revision=1,
        requirement_artifact=requirement_pointer,
        parent_candidates=(
            ParentCandidateRefV1(
                candidate_id="parent-candidate-p33-tis2",
                structure_id="parent-structure-p33-tis2",
                structure_artifact=parent_pointer,
            ),
        ),
        policy_artifact=policy_pointer,
        tag_graph_artifact=graph_pointer,
        transformation_registry_artifact=registry_pointer,
        search_fixture_artifact=search_fixture_pointer,
        search_adapter=search_adapter.component,
        vectorizer=SIGNED_HASHING_SNAPSHOT,
    )
    runner = InspirationRunner(
        store=store,
        search_adapter=search_adapter,
        document_fetcher=fetcher,
        transformation_engine=PymatgenTransformationEngine(),
    )
    result = runner.run(
        inspiration_input=inspiration_input,
        policy=policy,
        tag_graph=graph,
        target_tag_ids=("electronic-flat-band",),
    )
    return _CompletedRun(
        store=store,
        runner=runner,
        result=result,
        policy_pointer=policy_pointer,
        graph_pointer=graph_pointer,
        response_bytes=tuple(responses[query.query_id] for query in queries),
    )


def _artifact_hashes(run: _CompletedRun) -> dict[str, str]:
    return {
        path.relative_to(run.store.root).as_posix(): hashlib.sha256(
            path.read_bytes()
        ).hexdigest()
        for path in sorted(run.stage_root.rglob("*"))
        if path.is_file()
    }


def test_offline_runner_fetches_only_three_insufficient_metadata_bodies_and_replays(
    tmp_path: Path,
) -> None:
    first = _run(tmp_path / "first")
    second = _run(tmp_path / "second")

    assert _artifact_hashes(first) == _artifact_hashes(second)
    assert first.result.stage_result_artifact.sha256 == (
        second.result.stage_result_artifact.sha256
    )
    first.runner.verify_stage_result(first.result.stage_result)
    second.runner.verify_stage_result(second.result.stage_result)
    execution_component_ids = {
        component.component_id for component in first.runner.execution_components
    }
    assert "fixture-document-fetcher" in execution_component_ids
    assert "inspiration-tag-feedback-compiler" in execution_component_ids

    ledger = first.result.bundle.cost_ledger
    expected_fetch_bytes = sum(
        (FIXTURE_DIR / filename).stat().st_size
        for filename, _media_type in BODY_FIXTURES.values()
    )
    assert ledger.search_requests == 4
    assert ledger.search_response_bytes == sum(map(len, first.response_bytes))
    assert ledger.raw_documents == 4
    assert ledger.unique_documents == 4
    assert ledger.fetch_requests == 3
    assert ledger.fetch_response_bytes == expected_fetch_bytes
    assert ledger.llm_calls == 0
    assert ledger.llm_input_tokens == 0
    assert ledger.llm_output_tokens == 0
    assert "PDF full-text reads: `0`" in first.result.report

    fetch_manifest = _read_jsonl(first.stage_root / "fetch_manifest.jsonl")
    assert len(fetch_manifest) == 4
    direct = next(row for row in fetch_manifest if not row["fetched"])
    fetched = tuple(row for row in fetch_manifest if row["fetched"])
    assert direct["decision"] == "SKIP_BODY_ABSTRACT_SUFFICIENT"
    assert direct["fetch_status"] == "SKIPPED_METADATA_SUFFICIENT"
    assert direct["physical_request_count"] == 0
    assert direct["fetch_response_bytes"] == 0
    assert len(fetched) == 3
    assert {row["decision"] for row in fetched} == {"BODY_EXTRACTED"}
    assert {row["fetch_status"] for row in fetched} == {"FETCHED"}
    assert {row["physical_request_count"] for row in fetched} == {1}
    assert sum(int(row["fetch_response_bytes"]) for row in fetched) == (
        expected_fetch_bytes
    )
    assert all(row["fetched_body_artifact"] is not None for row in fetched)

    attempts = _read_jsonl(first.stage_root / "fetch_attempts.jsonl")
    assert len(attempts) == 3
    assert {attempt["outcome"] for attempt in attempts} == {"success"}
    assert sum(int(attempt["response_bytes"]) for attempt in attempts) == (
        expected_fetch_bytes
    )
    assert all("?" not in str(attempt["request_url"]) for attempt in attempts)

    expected_body_hashes = {
        hashlib.sha256((FIXTURE_DIR / filename).read_bytes()).hexdigest()
        for filename, _media_type in BODY_FIXTURES.values()
    }
    persisted_body_hashes = {
        row["fetched_body_artifact"]["sha256"] for row in fetched
    }
    assert persisted_body_hashes == expected_body_hashes
    for row in fetched:
        artifact = ArtifactPointerV1.model_validate(row["fetched_body_artifact"])
        assert first.store.inspect(
            artifact.uri,
            media_type=artifact.media_type or "application/octet-stream",
        ).sha256 == artifact.sha256

    passages = tuple(
        PassageV1.model_validate_json(line)
        for line in (first.stage_root / "passages.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
        if line.strip()
    )
    locator_kinds = {passage.locator.kind.value for passage in passages}
    assert {
        "JSON_PATH",
        "JSON_LD",
        "HTML_META",
        "JATS_XPATH",
        "CSS_SELECTOR",
    } <= locator_kinds
    raw_search_hashes = {
        hashlib.sha256(payload).hexdigest() for payload in first.response_bytes
    }
    body_source_hashes = {
        passage.source_artifact.sha256
        for passage in passages
        if passage.locator.kind.value not in {"API_FIELD", "JSON_PATH"}
    }
    assert body_source_hashes == expected_body_hashes
    metadata_passages = tuple(
        passage
        for passage in passages
        if passage.locator.kind.value in {"API_FIELD", "JSON_PATH"}
    )
    assert len(metadata_passages) == 1
    assert metadata_passages[0].source_artifact.sha256 in raw_search_hashes

    vectors = tuple(
        PassageVectorV1.model_validate_json(line)
        for line in (first.stage_root / "passage_vectors.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
        if line.strip()
    )
    passage_by_id = {passage.passage_id: passage for passage in passages}
    hits = {
        row["hit_id"]: row
        for row in _read_jsonl(first.stage_root / "search_hits.jsonl")
    }
    assert len(passage_by_id) == len(passages)
    assert len({vector.passage_id for vector in vectors}) == len(vectors)
    assert {vector.passage_id for vector in vectors} == set(passage_by_id)
    for vector in vectors:
        passage = passage_by_id[vector.passage_id]
        rebuilt = build_embedding_input_bytes(
            passage,
            title=str(hits[passage.hit_id]["title"]),
            normalized_tags=passage.matched_tag_ids,
        )
        assert hashlib.sha256(rebuilt).hexdigest() == vector.embedding_input_sha256
        assert passage.text.encode("utf-8") in rebuilt
        assert vector.vector_artifact.size_bytes == 32 * 4
    assert ledger.vectorized_passages == len(vectors) == len(passages)

    bridge_packets = _read_jsonl(first.stage_root / "bridge_packets.jsonl")
    assert len(bridge_packets) == 3
    assert {packet["bridge_rule_id"] for packet in bridge_packets} == set(ARTICLE_URLS)

    feedback = TagFeedbackReviewV1.model_validate_json(
        (first.stage_root / "tag_feedback.json").read_bytes()
    )
    bridge_rows = {row.bridge_rule_id: row for row in feedback.bridge_rows}
    assert set(bridge_rows) == set(ARTICLE_URLS)
    assert {row.status for row in bridge_rows.values()} == {"SEARCH_SUPPORTED"}
    assert {row.expert_status for row in bridge_rows.values()} == {"UNKNOWN"}
    assert feedback.expert_status == "UNKNOWN"
    assert feedback.review_disposition == "REVIEW_ONLY"
    assert feedback.applies_to_tag_graph is False
    assert feedback.scientific_conclusion is False
    assert feedback.llm_calls == 0
    assert feedback.graph_artifact.sha256 == first.graph_pointer.sha256
    query_rows = {row.query_id: row for row in feedback.query_rows}
    direct_query = next(
        query for query in query_rows.values() if query.kind is SearchQueryKind.DIRECT
    )
    assert direct_query.fetch_request_count == 0
    assert all(
        row.fetch_request_count == 1
        for row in query_rows.values()
        if row.kind is SearchQueryKind.BRIDGE
    )
    input_roles = {item.role for item in feedback.input_artifacts}
    assert {"cost-ledger", "fetch-attempts", "fetch-manifest"} <= input_roles
    assert sum(role.startswith("fetched-body-") for role in input_roles) == 3

    assert HermesFixtureProjector(first.store)._count_fetched_documents(
        first.result.stage_result,
        run_id="run-p33-multiformat",
    ) == 3
    assert "## Query, tag, and bridge yield feedback" in first.result.report
    assert "INCLUSIVE_NON_ADDITIVE" in first.result.report
    assert "Expert review status: `UNKNOWN`" in first.result.report
    assert "PROMPT_INJECTION_SENTINEL" not in first.result.report

    prompt_passages = tuple(
        passage for passage in passages if "PROMPT_INJECTION_SENTINEL" in passage.text
    )
    assert len(prompt_passages) == 1
    assert first.store.read_bytes(first.policy_pointer.uri) == canonical_json_bytes(
        _policy()
    )
    assert first.store.read_bytes(first.graph_pointer.uri) == canonical_json_bytes(
        curated_flat_band_tag_graph()
    )
    assert _policy().fetch.allow_pdf_fulltext is False
    assert all("pdf" not in str(row["media_type"]).casefold() for row in fetched)


def test_duplicate_document_across_bridge_queries_is_fetched_once_with_inclusive_cost(
    tmp_path: Path,
) -> None:
    completed = _run(
        tmp_path / "shared-document",
        share_acoustic_magnon_document=True,
    )

    ledger = completed.result.bundle.cost_ledger
    assert ledger.raw_documents == 4
    assert ledger.unique_documents == 3
    assert ledger.fetch_requests == 2
    assert ledger.fetch_response_bytes == (
        (FIXTURE_DIR / "p33-structured.html").stat().st_size
        + (FIXTURE_DIR / "p33-plain.html").stat().st_size
    )

    manifest = _read_jsonl(completed.stage_root / "fetch_manifest.jsonl")
    shared = tuple(row for row in manifest if len(row["member_hit_ids"]) == 2)
    assert len(shared) == 1
    assert shared[0]["physical_request_count"] == 1
    assert len(shared[0]["query_ids"]) == 2
    assert shared[0]["fetched"] is True

    feedback = TagFeedbackReviewV1.model_validate_json(
        (completed.stage_root / "tag_feedback.json").read_bytes()
    )
    bridge_queries = {
        row.bridge_rule_id: row
        for row in feedback.query_rows
        if row.kind is SearchQueryKind.BRIDGE
    }
    assert bridge_queries[
        "acoustic-resonance-to-electronic-flat-band"
    ].fetch_request_count == 1
    assert bridge_queries[
        "magnon-line-graph-to-electronic-flat-band"
    ].fetch_request_count == 1
    assert bridge_queries[
        "acoustic-resonance-to-electronic-flat-band"
    ].fetch_response_bytes == (FIXTURE_DIR / "p33-structured.html").stat().st_size
    assert bridge_queries[
        "magnon-line-graph-to-electronic-flat-band"
    ].fetch_response_bytes == (FIXTURE_DIR / "p33-structured.html").stat().st_size

    bridge_rows = {row.bridge_rule_id: row for row in feedback.bridge_rows}
    assert bridge_rows[
        "acoustic-resonance-to-electronic-flat-band"
    ].status == "SEARCH_SUPPORTED"
    assert bridge_rows[
        "magnon-line-graph-to-electronic-flat-band"
    ].status == "EVIDENCE_INSUFFICIENT"


def test_transient_body_failures_are_not_reported_as_scientific_no_match(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "transient-fetch-failure"
    with pytest.raises(InspirationRunnerError) as raised:
        _run(workspace, transient_body_failures=True)

    assert raised.value.code == "EXTERNAL_FETCH_UNAVAILABLE"
    stage_root = workspace / "stages" / "inspiration" / "run-p33-multiformat"
    attempts = _read_jsonl(stage_root / "fetch_attempts.jsonl")
    manifest = _read_jsonl(stage_root / "fetch_manifest.jsonl")
    assert len(attempts) == 3
    assert {attempt["error_code"] for attempt in attempts} == {
        "TRANSIENT_HTTP_ERROR"
    }
    assert sum(row["fetch_status"] == "FAILED_TRANSIENT" for row in manifest) == 3
    assert not (stage_root / "stage_result.json").exists()
    assert not (stage_root / "tag_feedback.json").exists()


def test_runner_stops_body_requests_at_the_shared_physical_budget(
    tmp_path: Path,
) -> None:
    completed = _run(tmp_path / "request-budget", fetch_request_budget=1)

    assert completed.result.bundle.cost_ledger.fetch_requests == 1
    attempts = _read_jsonl(completed.stage_root / "fetch_attempts.jsonl")
    manifest = _read_jsonl(completed.stage_root / "fetch_manifest.jsonl")
    assert len(attempts) == 1
    assert sum(row["fetch_status"] == "FETCHED" for row in manifest) == 1
    assert sum(
        row["fetch_status"] == "REQUEST_BUDGET_EXHAUSTED"
        for row in manifest
    ) == 2
    assert sum(int(row["physical_request_count"]) for row in manifest) == 1
