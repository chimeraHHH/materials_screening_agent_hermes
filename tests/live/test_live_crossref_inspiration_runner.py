from __future__ import annotations

import json
from pathlib import Path

import pytest

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


def _live_policy() -> InspirationPolicyV1:
    return InspirationPolicyV1(
        policy_id="inspiration-live-crossref-v1",
        search_mode=SearchExecutionMode.PUBLIC_METADATA_API,
        network_access=True,
        search=SearchBudgetV1(
            max_queries=3,
            max_direct_queries=1,
            max_bridge_queries=1,
            max_counter_queries=1,
            max_raw_hits=3,
            max_unique_documents=3,
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


@pytest.mark.live_crossref
def test_live_crossref_full_inspiration_runner_gate(tmp_path: Path) -> None:
    store = LocalArtifactStore(tmp_path)
    policy = _live_policy()
    graph = curated_flat_band_tag_graph()
    adapter = CrossrefPublicAdapter(max_results=1, timeout_seconds=30)

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
        project_id="project-live-crossref",
        request_id="request-live-crossref",
        run_id="run-live-crossref",
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

    prefix = "stages/inspiration/run-live-crossref"
    raw_paths = sorted((store.root / prefix / "raw_search").glob("*.json"))
    query_plans = store.read_jsonl(f"{prefix}/query_plans.jsonl")
    attempts = store.read_jsonl(f"{prefix}/search_attempts.jsonl")
    passages = store.read_jsonl(f"{prefix}/passages.jsonl")
    evidence = store.read_jsonl(f"{prefix}/evidence_cards.jsonl")
    bridges = store.read_jsonl(f"{prefix}/bridge_packets.jsonl")
    proposals = store.read_jsonl(f"{prefix}/transformation_proposals.jsonl")
    ledger = result.bundle.cost_ledger

    assert result.stage_result.outcome is InspirationOutcome.SUCCEEDED
    assert len(query_plans) == len(raw_paths) == 3
    assert len(attempts) == ledger.search_requests
    assert ledger.search_requests >= len(raw_paths)
    assert sum(row["outcome"] == "success" for row in attempts) == len(raw_paths)
    assert all(path.stat().st_size > 0 for path in raw_paths)
    assert sum(path.stat().st_size for path in raw_paths) == (
        ledger.search_response_bytes
    )
    assert passages
    assert evidence
    assert bridges
    assert proposals
    assert proposals[0]["status"] == "STRUCTURE_VALID"
    assert proposals[0]["output_structure_id"].startswith("str_")
    assert len(result.bundle.selected_candidates) == 1
    assert ledger.fetch_requests == 0
    assert ledger.fetch_response_bytes == 0
    assert ledger.llm_calls == 0
    assert ledger.llm_input_tokens == 0
    assert ledger.llm_output_tokens == 0
    assert "PDF full-text reads: `0`" in result.report
    assert "novelty" not in result.report.casefold()
    runner.verify_stage_result(result.stage_result)

    print(
        "LIVE_CROSSREF_RUN_SUMMARY="
        + json.dumps(
            {
                "bridge_count": len(bridges),
                "bundle_sha256": result.stage_result.bundle_artifact.sha256,
                "candidate_count": len(result.bundle.selected_candidates),
                "evidence_count": len(evidence),
                "passage_count": len(passages),
                "proposal_count": len(proposals),
                "raw_response_bytes": ledger.search_response_bytes,
                "search_requests": ledger.search_requests,
                "search_retries": ledger.search_requests - len(query_plans),
                "stage_result_sha256": result.stage_result_artifact.sha256,
            },
            sort_keys=True,
        )
    )
