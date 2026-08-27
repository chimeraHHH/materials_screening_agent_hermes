from __future__ import annotations

from types import SimpleNamespace

from material_agent.inspiration.models import (
    ArtifactPointerV1,
    ComponentSnapshotV1,
    InspirationOutcome,
)
from material_agent.inspiration.policy import InspirationPolicyV1
from material_agent.inspiration.tag_graph import curated_flat_band_tag_graph
from material_agent.inspiration.vectorizer import SIGNED_HASHING_SNAPSHOT
from material_agent.orchestrator.inspiration_composite import (
    InspirationCompositeRequestV2,
    InspirationCompositeResultV2,
    InspirationCompositeStatus,
    build_inspiration_composite_v2,
)
from material_agent.retrieval.models import CandidateAuditRecord, Decision
from material_agent.retrieval.storage import LocalArtifactStore


class _FakeInspirationRunner:
    def __init__(self, store: LocalArtifactStore) -> None:
        self.store = store
        self.search_adapter = SimpleNamespace(
            component=ComponentSnapshotV1(
                component_id="fixture-search",
                version="v1",
                implementation_sha256="1" * 64,
            )
        )
        self.vectorizer = SIGNED_HASHING_SNAPSHOT
        self.calls = []

    def run(self, **kwargs):
        self.calls.append(kwargs)
        run_id = kwargs["inspiration_input"].run_id
        bundle_ref = self.store.write_json(
            f"stages/inspiration/{run_id}/inspiration_bundle.json",
            {"outcome": "SUCCEEDED"},
            immutable=True,
        )
        result_ref = self.store.write_json(
            f"stages/inspiration/{run_id}/stage_result.json",
            {"outcome": "SUCCEEDED", "bundle": bundle_ref.model_dump(mode="json")},
            immutable=True,
        )
        bundle_pointer = ArtifactPointerV1.model_validate(
            bundle_ref.model_dump(mode="json")
        )
        return SimpleNamespace(
            stage_result=SimpleNamespace(
                outcome=InspirationOutcome.SUCCEEDED,
                bundle_artifact=bundle_pointer,
            ),
            stage_result_artifact=ArtifactPointerV1.model_validate(
                result_ref.model_dump(mode="json")
            ),
            bundle=SimpleNamespace(
                selected_candidates=(SimpleNamespace(candidate_id="proposal-1"),)
            ),
        )


def _seed_request(tmp_path, requirement, *, bad_structure_hash=False):
    store = LocalArtifactStore(tmp_path)
    source_run_id = "run-agent01"
    run_id = "run-inspiration"
    confirmed = requirement.model_copy(
        update={"revision": 1, "confirmed_by_user": True}
    )
    requirement_ref = store.write_json(
        f"requirements/{source_run_id}/requirement.v1.json",
        confirmed.model_dump(mode="json"),
        immutable=True,
    )
    structure_ref = store.write_bytes(
        "candidates/structures/structure-dynamic.cif",
        b"data_dynamic\n",
        "chemical/x-cif",
        immutable=True,
    )
    candidate = CandidateAuditRecord(
        candidate_id="candidate-dynamic",
        formula="SiO2",
        source_database_version="fixture-v1",
        source_material_id="mp-dynamic",
        query_id="query-dynamic",
        structure_id="structure-dynamic",
        structure_artifact_uri=structure_ref.uri,
        structure_artifact_sha256=(
            "0" * 64 if bad_structure_hash else structure_ref.sha256
        ),
        decision=Decision.PASS,
        publication_rank=1,
        published_downstream=True,
    )
    manifest_ref = store.write_jsonl(
        f"stages/agent01/{source_run_id}/candidate_manifest.jsonl",
        [candidate.model_dump(mode="json")],
        immutable=True,
    )
    policy = InspirationPolicyV1()
    policy_ref = store.write_json(
        "policies/inspiration-policy-v1.json",
        policy.model_dump(mode="json"),
        immutable=True,
    )
    graph_ref = store.write_json(
        "policies/tag-graph-v1.json",
        curated_flat_band_tag_graph().model_dump(mode="json"),
        immutable=True,
    )
    registry_ref = store.write_json(
        "policies/transformation-registry-v1.json",
        {"schema_version": "test-registry-v1"},
        immutable=True,
    )
    search_ref = store.write_json(
        "inputs/search-fixture-manifest.json",
        {"schema_version": "test-search-fixture-v1"},
        immutable=True,
    )
    request = InspirationCompositeRequestV2(
        project_id="project-dynamic",
        request_id="request-dynamic",
        source_run_id=source_run_id,
        inspiration_run_id=run_id,
        requirement_revision=1,
        requirement_artifact={
            "uri": requirement_ref.uri,
            "sha256": requirement_ref.sha256,
        },
        candidate_manifest_artifact={
            "uri": manifest_ref.uri,
            "sha256": manifest_ref.sha256,
        },
        policy_artifact={"uri": policy_ref.uri, "sha256": policy_ref.sha256},
        tag_graph_artifact={"uri": graph_ref.uri, "sha256": graph_ref.sha256},
        transformation_registry_artifact={
            "uri": registry_ref.uri,
            "sha256": registry_ref.sha256,
        },
        search_fixture_artifact={
            "uri": search_ref.uri,
            "sha256": search_ref.sha256,
        },
        target_tag_ids=["electronic-flat-band"],
    )
    return store, request


def test_composite_graph_compiles_dynamic_agent01_parent_and_executes(
    tmp_path, requirement
) -> None:
    store, request = _seed_request(tmp_path, requirement)
    runner = _FakeInspirationRunner(store)
    graph = build_inspiration_composite_v2(store=store, runner=runner).compile()

    state = graph.invoke({"request": request.model_dump(mode="json")})
    result = InspirationCompositeResultV2.model_validate(state["result"])

    assert result.status is InspirationCompositeStatus.SUCCEEDED
    assert result.parent_candidate_ids == ["candidate-dynamic"]
    assert result.selected_candidate_ids == ["proposal-1"]
    assert result.frozen_input_artifact is not None
    assert store.exists_with_hash(
        result.frozen_input_artifact.uri,
        result.frozen_input_artifact.sha256,
    )
    assert len(runner.calls) == 1
    assert runner.calls[0]["inspiration_input"].parent_candidates[0].structure_id == (
        "structure-dynamic"
    )


def test_composite_graph_fails_closed_on_agent01_structure_hash_mismatch(
    tmp_path, requirement
) -> None:
    store, request = _seed_request(
        tmp_path, requirement, bad_structure_hash=True
    )
    runner = _FakeInspirationRunner(store)
    graph = build_inspiration_composite_v2(store=store, runner=runner).compile()

    state = graph.invoke({"request": request.model_dump(mode="json")})
    result = InspirationCompositeResultV2.model_validate(state["result"])

    assert result.status is InspirationCompositeStatus.FAILED
    assert result.error_code == "AGENT01_STRUCTURE_HASH_MISMATCH"
    assert runner.calls == []
