from __future__ import annotations

from material_agent.inspiration.contextual_runner import (
    semantic_scholar_contextual_runner_from_environment,
)
from material_agent.inspiration.policy import (
    FetchBudgetV1,
    InspirationPolicyV1,
    SearchExecutionMode,
)
from material_agent.inspiration.tag_graph import curated_flat_band_tag_graph
from material_agent.orchestrator import (
    InspirationCompositeLaunchV2,
    InspirationQueryCompositeLaunchV3,
    InspirationQueryCompositeRuntimeV3,
    InspirationQueryCompositeStatus,
    OrchestratorRuntime,
)
from material_agent.retrieval.storage import LocalArtifactStore
from tests.integration.test_orchestrator_inspiration_query_public_e2e import (
    _NoopTransformationEngine,
    _SemanticScholarFixtureTransport,
)


def test_workspace_runtime_resolves_agent01_and_runs_contextual_v3(
    tmp_path,
    requirement,
    fixture_payload,
) -> None:
    project_id = "project-contextual-v3-runtime"
    project = OrchestratorRuntime.create_project(tmp_path, project_id)
    project_root = project["project_root"]
    with OrchestratorRuntime.from_workspace(tmp_path, project_id) as runtime:
        reviewing = runtime.start_run(
            raw_request="contextual V3 runtime source",
            initial_requirement=requirement.model_dump(mode="json"),
            fixture_payload=fixture_payload,
            run_id="run-contextual-source",
        )
        completed = runtime.approve(
            run_id="run-contextual-source",
            approval_id=reviewing.interrupts[0].value["approval_id"],
            decision="approve",
        )
        assert completed.candidate_ids

    store = LocalArtifactStore(project_root)
    policy = InspirationPolicyV1(
        policy_id="public-contextual-runtime-v1",
        search_mode=SearchExecutionMode.PUBLIC_METADATA_API,
        network_access=True,
        fetch=FetchBudgetV1(
            max_requests=0,
            max_total_bytes=0,
            max_bytes_per_response=0,
        ),
    )
    policy_ref = store.write_json(
        "policies/public-contextual-runtime-v1.json",
        policy.model_dump(mode="json"),
        immutable=True,
    )
    graph_ref = store.write_json(
        "policies/contextual-runtime-tag-graph.json",
        curated_flat_band_tag_graph().model_dump(mode="json"),
        immutable=True,
    )
    registry_ref = store.write_json(
        "policies/contextual-runtime-registry.json",
        {"schema_version": "contextual-runtime-registry-v1"},
        immutable=True,
    )
    transport = _SemanticScholarFixtureTransport()
    runner = semantic_scholar_contextual_runner_from_environment(
        store=store,
        transformation_engine=_NoopTransformationEngine(),
        environment={"SEMANTIC_SCHOLAR_API_KEY": "fixture-secret"},
        transport=transport,
        monotonic_clock=lambda: 100.0,
    )
    launch = InspirationQueryCompositeLaunchV3(
        base_launch=InspirationCompositeLaunchV2(
            request_id="request-contextual-runtime",
            source_run_id="run-contextual-source",
            inspiration_run_id="run-contextual-inspiration",
            requirement_revision=1,
            policy_artifact={"uri": policy_ref.uri, "sha256": policy_ref.sha256},
            tag_graph_artifact={"uri": graph_ref.uri, "sha256": graph_ref.sha256},
            transformation_registry_artifact={
                "uri": registry_ref.uri,
                "sha256": registry_ref.sha256,
            },
            target_tag_ids=["electronic-flat-band"],
        ),
        raw_request=(
            "Use the Agent01 structures to study flat-band mechanisms and "
            "historical soft-chemistry literature."
        ),
    )

    with InspirationQueryCompositeRuntimeV3(project_root, runner=runner) as runtime:
        result = runtime.execute(launch)

    assert result.status is InspirationQueryCompositeStatus.SCIENTIFIC_NO_MATCH
    assert result.parent_candidate_ids == completed.candidate_ids
    assert result.handoff is not None
    assert transport.urls
    assert "fixture-secret" not in result.model_dump_json()
