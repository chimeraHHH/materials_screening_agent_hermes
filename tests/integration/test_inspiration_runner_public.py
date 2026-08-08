from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from pathlib import Path

from material_agent.inspiration.engine import PymatgenTransformationEngine
from material_agent.inspiration.models import (
    ArtifactPointerV1,
    InspirationInputV1,
    InspirationOutcome,
    ParentCandidateRefV1,
)
from material_agent.inspiration.policy import (
    BridgeSearchPolicyV1,
    EmbeddingBudgetV1,
    FetchBudgetV1,
    InspirationPolicyV1,
    PassageBudgetV1,
    SearchBudgetV1,
    SearchExecutionMode,
    SelectionPolicyV1,
    TransformationBudgetV1,
)
from material_agent.inspiration.runner import STRUCTURE_MEDIA_TYPE, InspirationRunner
from material_agent.inspiration.search import CrossrefPublicAdapter
from material_agent.inspiration.tag_graph import curated_flat_band_tag_graph
from material_agent.inspiration.transformations import (
    DEFAULT_SUBSTITUTION_REGISTRY_V1,
    substitution_registry_bytes,
)
from material_agent.inspiration.vectorizer import SIGNED_HASHING_SNAPSHOT
from material_agent.retrieval.storage import LocalArtifactStore


FIXTURE_DIR = Path(__file__).parents[1] / "fixtures" / "inspiration"


def _pointer(reference) -> ArtifactPointerV1:
    return ArtifactPointerV1.model_validate(reference.model_dump(mode="python"))


class StaticCrossrefTransport:
    """Network-boundary double; runner still uses the production adapter path."""

    def __init__(self, payload: bytes) -> None:
        self.payload = payload
        self.calls: list[dict[str, object]] = []

    def get(
        self,
        url: str,
        *,
        headers: Mapping[str, str],
        timeout_seconds: int,
        max_response_bytes: int,
    ) -> bytes:
        self.calls.append(
            {
                "url": url,
                "headers": dict(headers),
                "timeout_seconds": timeout_seconds,
                "max_response_bytes": max_response_bytes,
            }
        )
        return self.payload


def _response_bytes() -> bytes:
    abstract = (
        "Local resonance in an acoustic metamaterial produces a weakly dispersive "
        "mode because a resonator couples weakly to an extended lattice. The local "
        "resonance mechanism preserves spectral separation and suppresses dispersion, "
        "which can guide electronic flat band hypotheses when connectivity and "
        "equivalent site chemistry remain controlled. Strong hybridization breaks "
        "localization and broadens the mode, providing a direct falsification condition."
    )
    return json.dumps(
        {
            "status": "ok",
            "message": {
                "items": [
                    {
                        "DOI": "10.5555/material-agent.crossref.1",
                        "title": [
                            "Local resonance as a bounded cross-domain mechanism"
                        ],
                        "author": [{"given": "Fixture", "family": "Author"}],
                        "published": {"date-parts": [[2025]]},
                        "URL": (
                            "https://doi.org/10.5555/material-agent.crossref.1"
                        ),
                        "abstract": f"<jats:p>{abstract}</jats:p>",
                        "subject": ["Local resonance", "Electronic flat band"],
                    }
                ]
            },
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _policy() -> InspirationPolicyV1:
    return InspirationPolicyV1(
        policy_id="inspiration-public-crossref-v1",
        search_mode=SearchExecutionMode.PUBLIC_METADATA_API,
        network_access=True,
        search=SearchBudgetV1(
            max_queries=3,
            max_direct_queries=1,
            max_bridge_queries=1,
            max_counter_queries=1,
            max_raw_hits=3,
            max_unique_documents=1,
        ),
        fetch=FetchBudgetV1(
            max_requests=0,
            max_total_bytes=0,
            max_bytes_per_response=0,
        ),
        passages=PassageBudgetV1(
            min_tokens=16,
            max_tokens=64,
            max_per_hit=1,
            max_total=3,
        ),
        embedding=EmbeddingBudgetV1(
            vector_dimension=32,
            max_passages=3,
            max_input_tokens=1_000,
        ),
        bridge=BridgeSearchPolicyV1(max_bridge_packets=1),
        transformation=TransformationBudgetV1(
            max_plans=2,
            max_plans_per_parent=2,
        ),
        selection=SelectionPolicyV1(
            top_k=1,
            min_mechanisms_when_available=1,
        ),
    )


def _run_public(root: Path):
    store = LocalArtifactStore(root)
    policy = _policy()
    graph = curated_flat_band_tag_graph()
    payload = _response_bytes()
    transport = StaticCrossrefTransport(payload)
    adapter = CrossrefPublicAdapter(
        max_results=1,
        timeout_seconds=7,
        transport=transport,
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
        store.write_json(
            "inputs/policy.json",
            policy.model_dump(mode="json"),
            immutable=True,
        )
    )
    graph_pointer = _pointer(
        store.write_json(
            "inputs/tag_graph.json",
            graph.model_dump(mode="json"),
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
    parent_pointer = _pointer(
        store.write_bytes(
            "inputs/parent-tis2.cif",
            (FIXTURE_DIR / "parent-tis2.cif").read_bytes(),
            media_type=STRUCTURE_MEDIA_TYPE,
            immutable=True,
        )
    )
    inspiration_input = InspirationInputV1(
        project_id="project-public-crossref",
        request_id="request-public-crossref",
        run_id="run-public-crossref",
        requirement_revision=1,
        requirement_artifact=requirement_pointer,
        parent_candidates=(
            ParentCandidateRefV1(
                candidate_id="parent-candidate-tis2",
                structure_id="parent-structure-tis2",
                structure_artifact=parent_pointer,
            ),
        ),
        policy_artifact=policy_pointer,
        tag_graph_artifact=graph_pointer,
        transformation_registry_artifact=registry_pointer,
        search_fixture_artifact=None,
        search_adapter=adapter.component,
        vectorizer=SIGNED_HASHING_SNAPSHOT,
    )
    runner = InspirationRunner(
        store=store,
        search_adapter=adapter,
        transformation_engine=PymatgenTransformationEngine(),
    )
    result = runner.run(
        inspiration_input=inspiration_input,
        policy=policy,
        tag_graph=graph,
        target_tag_ids=("electronic-flat-band",),
    )
    return store, runner, result, inspiration_input, transport


def test_public_crossref_vertical_slice_persists_raw_metadata_and_no_body(
    tmp_path: Path,
) -> None:
    store, runner, result, inspiration_input, transport = _run_public(
        tmp_path / "public"
    )

    assert inspiration_input.search_fixture_artifact is None
    assert result.stage_result.outcome is InspirationOutcome.SUCCEEDED
    assert len(result.bundle.selected_candidates) == 1
    assert len(transport.calls) == 3
    ledger = result.bundle.cost_ledger
    assert ledger.search_requests == 3
    assert ledger.search_response_bytes == 3 * len(_response_bytes())
    assert ledger.fetch_requests == 0
    assert ledger.fetch_response_bytes == 0
    assert ledger.llm_calls == 0

    prefix = "stages/inspiration/run-public-crossref"
    hits = store.read_jsonl(f"{prefix}/search_hits.jsonl")
    assert len(hits) == 3
    assert {record["provider"] for record in hits} == {"crossref"}
    for record in hits:
        pointer = ArtifactPointerV1.model_validate(record["raw_response_artifact"])
        assert store.read_bytes(pointer.uri) == _response_bytes()
        assert pointer.sha256 == hashlib.sha256(_response_bytes()).hexdigest()
        assert pointer.size_bytes == len(_response_bytes())

    passages = store.read_jsonl(f"{prefix}/passages.jsonl")
    assert len(passages) == 3
    assert {
        record["locator"]["selector"] for record in passages
    } == {"$.message.items[0].abstract"}
    assert all(record["locator"]["kind"] == "JSON_PATH" for record in passages)
    assert all(
        record["source_artifact"]["uri"].startswith(
            f"artifact://{prefix}/raw_search/"
        )
        for record in passages
    )
    assert "PDF full-text reads: `0`" in result.report
    assert "novelty" not in result.report.casefold()
    runner.verify_stage_result(result.stage_result)
