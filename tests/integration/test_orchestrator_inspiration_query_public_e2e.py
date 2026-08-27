from __future__ import annotations

import hashlib
import json
from urllib.parse import parse_qs, urlsplit

from material_agent.inspiration.contextual_runner import (
    SemanticScholarContextualInspirationRunnerV3,
)
from material_agent.inspiration.models import ComponentSnapshotV1
from material_agent.inspiration.policy import (
    FetchBudgetV1,
    InspirationPolicyV1,
    SearchExecutionMode,
)
from material_agent.inspiration.semantic_scholar import SemanticScholarPublicAdapter
from material_agent.orchestrator.inspiration_query_composite import (
    InspirationQueryCompositeRequestV3,
    InspirationQueryCompositeResultV3,
    InspirationQueryCompositeStatus,
    build_inspiration_query_composite_v3,
)
from material_agent.orchestrator.models import ArtifactPointer
from tests.unit.test_orchestrator_inspiration_composite import _seed_request


class _SemanticScholarFixtureTransport:
    def __init__(self) -> None:
        self.urls: list[str] = []

    def get(self, url: str, **_kwargs):
        self.urls.append(url)
        query = parse_qs(urlsplit(url).query)
        year_range = query.get("year", ["2020-2026"])[0]
        lower = year_range.split("-", 1)[0]
        year = int(lower) if lower else 2020
        paper_id = "paper-" + hashlib.sha256(url.encode("utf-8")).hexdigest()[:16]
        payload = {
            "data": [
                {
                    "paperId": paper_id,
                    "externalIds": {},
                    "url": f"https://example.org/{paper_id}",
                    "title": "Local resonance and destructive interference in a flat band",
                    "abstract": (
                        "We demonstrate a local resonance mechanism in an acoustic "
                        "metamaterial because destructive interference suppresses "
                        "dispersion and produces a compact localized state. The "
                        "result suggests a transferable connectivity condition."
                    ),
                    "venue": "Fixture Journal",
                    "year": year,
                    "authors": [{"authorId": "a1", "name": "A. Researcher"}],
                    "fieldsOfStudy": ["Materials Science", "Physics"],
                    "citationCount": 1,
                    "referenceCount": 1,
                }
            ]
        }
        return json.dumps(payload, sort_keys=True).encode("utf-8")


class _NoopTransformationEngine:
    component = ComponentSnapshotV1(
        component_id="contextual-e2e-noop-transformation",
        version="v1",
        implementation_sha256="f" * 64,
    )

    def generate(self, _context):
        return ()


def test_agent01_to_contextual_public_literature_reaches_real_runner(
    tmp_path,
    requirement,
) -> None:
    store, base_request = _seed_request(tmp_path, requirement)
    policy = InspirationPolicyV1(
        policy_id="public-contextual-e2e-v1",
        search_mode=SearchExecutionMode.PUBLIC_METADATA_API,
        network_access=True,
        fetch=FetchBudgetV1(
            max_requests=0,
            max_total_bytes=0,
            max_bytes_per_response=0,
        ),
    )
    policy_ref = store.write_json(
        "policies/public-contextual-e2e-v1.json",
        policy.model_dump(mode="json"),
        immutable=True,
    )
    base_request = base_request.model_copy(
        update={
            "policy_artifact": ArtifactPointer(
                uri=policy_ref.uri,
                sha256=policy_ref.sha256,
            ),
            "search_fixture_artifact": None,
        }
    )
    transport = _SemanticScholarFixtureTransport()
    runner = SemanticScholarContextualInspirationRunnerV3(
        store=store,
        semantic_scholar_adapter=SemanticScholarPublicAdapter(
            transport=transport,
            api_key_resolver=lambda: "",
        ),
        transformation_engine=_NoopTransformationEngine(),
        monotonic_clock=lambda: 100.0,
    )
    graph = build_inspiration_query_composite_v3(
        store=store,
        runner=runner,
    ).compile()
    request = InspirationQueryCompositeRequestV3(
        base_request=base_request,
        raw_request=(
            "Study flat-band mechanisms in the Agent01 material, include "
            "historical literature, and consider chalcogen substitution."
        ),
    )

    state = graph.invoke({"request": request.model_dump(mode="json")})
    result = InspirationQueryCompositeResultV3.model_validate(state["result"])

    assert result.status is InspirationQueryCompositeStatus.SCIENTIFIC_NO_MATCH
    assert result.error_code is None
    assert result.stage_result_artifact is not None
    assert result.bundle_artifact is not None
    assert transport.urls
    assert any("year=1960-1979" in url for url in transport.urls)
    assert any("year=1980-1999" in url for url in transport.urls)
    assert store.exists_with_hash(
        result.stage_result_artifact.uri,
        result.stage_result_artifact.sha256,
    )
    query_rows = store.read_jsonl(
        "stages/inspiration/run-inspiration/query_plans.jsonl"
    )
    attempt_rows = store.read_jsonl(
        "stages/inspiration/run-inspiration/search_attempts.jsonl"
    )
    assert len(query_rows) == len(transport.urls)
    assert {item["query_id"] for item in query_rows} == {
        item["query_id"] for item in attempt_rows
    }
