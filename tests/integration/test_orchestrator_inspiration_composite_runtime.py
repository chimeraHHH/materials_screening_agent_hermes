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
    InspirationCompositeLaunchV2,
    InspirationCompositeRuntimeV2,
    InspirationCompositeStatus,
)
from material_agent.orchestrator.runtime import OrchestratorRuntime
from material_agent.retrieval.storage import LocalArtifactStore


class _RecordingRunner:
    def __init__(self, store: LocalArtifactStore) -> None:
        self.store = store
        self.search_adapter = SimpleNamespace(
            component=ComponentSnapshotV1(
                component_id="integration-search",
                version="v1",
                implementation_sha256="2" * 64,
            )
        )
        self.vectorizer = SIGNED_HASHING_SNAPSHOT
        self.inputs = []

    def run(self, **kwargs):
        inspiration_input = kwargs["inspiration_input"]
        self.inputs.append(inspiration_input)
        prefix = f"stages/inspiration/{inspiration_input.run_id}"
        bundle_ref = self.store.write_json(
            f"{prefix}/inspiration_bundle.json",
            {"outcome": "SUCCEEDED"},
            immutable=True,
        )
        result_ref = self.store.write_json(
            f"{prefix}/stage_result.json",
            {"outcome": "SUCCEEDED"},
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


def test_opt_in_runtime_resolves_existing_agent01_run_and_executes(
    tmp_path, requirement, fixture_payload
) -> None:
    project_id = "project-composite-runtime"
    project = OrchestratorRuntime.create_project(tmp_path, project_id)
    project_root = project["project_root"]
    with OrchestratorRuntime.from_workspace(tmp_path, project_id) as runtime:
        reviewing = runtime.start_run(
            raw_request="structured composite source",
            initial_requirement=requirement.model_dump(mode="json"),
            fixture_payload=fixture_payload,
            run_id="run-agent01-source",
        )
        completed = runtime.approve(
            run_id="run-agent01-source",
            approval_id=reviewing.interrupts[0].value["approval_id"],
            decision="approve",
        )
        assert completed.candidate_ids

    store = LocalArtifactStore(project_root)
    policy_ref = store.write_json(
        "policies/runtime-inspiration-policy.json",
        InspirationPolicyV1().model_dump(mode="json"),
        immutable=True,
    )
    tag_graph_ref = store.write_json(
        "policies/runtime-tag-graph.json",
        curated_flat_band_tag_graph().model_dump(mode="json"),
        immutable=True,
    )
    registry_ref = store.write_json(
        "policies/runtime-transformation-registry.json",
        {"schema_version": "integration-registry-v1"},
        immutable=True,
    )
    search_ref = store.write_json(
        "inputs/runtime-search-fixture.json",
        {"schema_version": "integration-search-v1"},
        immutable=True,
    )
    runner = _RecordingRunner(store)
    launch = InspirationCompositeLaunchV2(
        request_id="request-composite-runtime",
        source_run_id="run-agent01-source",
        inspiration_run_id="run-inspiration-runtime",
        requirement_revision=1,
        policy_artifact={"uri": policy_ref.uri, "sha256": policy_ref.sha256},
        tag_graph_artifact={
            "uri": tag_graph_ref.uri,
            "sha256": tag_graph_ref.sha256,
        },
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

    with InspirationCompositeRuntimeV2(project_root, runner=runner) as runtime:
        result = runtime.execute(launch)

    assert result.status is InspirationCompositeStatus.SUCCEEDED
    assert result.parent_candidate_ids == completed.candidate_ids
    assert len(runner.inputs) == 1
    assert [
        parent.candidate_id for parent in runner.inputs[0].parent_candidates
    ] == completed.candidate_ids
