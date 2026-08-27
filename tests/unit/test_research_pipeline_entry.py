from __future__ import annotations

import threading
import time
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from material_agent.inspiration.models import canonical_json_bytes
from material_agent.integration.generic_research import (
    GENERIC_RESEARCH_TOOL_NAME,
    GENERIC_RESEARCH_TOOL_RECEIPT_MAX_BYTES,
    RESEARCH_ROLE_MAX_ATTEMPTS,
    RESEARCH_ROLE_RESPONSE_TIMEOUT_SECONDS,
    GenericMaterialsResearchService,
    GenericResearchRunRequestV1,
    GenericResearchToolCountsV1,
    GenericResearchToolReceiptV1,
    allocate_research_search_calls,
    generic_research_tool_manifest,
    research_role_budget,
)
from material_agent.integration.research_pipeline import (
    RESEARCH_PIPELINE_TOOL_NAME,
    ResearchPipelineRunRequestV1,
    ResearchPipelineService,
    ResearchPipelineStatus,
    ResearchStageRecordV1,
    ResearchStageStatus,
    accuracy_search_environment,
    research_pipeline_tool_manifest,
)
from material_agent.integration.research_pipeline_mcp import (
    ResearchPipelineDispatcher,
    ResearchPipelineDispatchError,
)
from material_agent.integration.track_b_benchmark import (
    TRACK_B_CALIBRATION_PROFILE_ID,
    TRACK_B_MAX_AUTHORITATIVE_SEARCH_CALLS,
    TRACK_B_MAX_CONTRACT_REPAIRS,
    TRACK_B_MAX_NATIVE_SEARCH_CALLS,
    TRACK_B_TOTAL_TOKEN_CEILING,
    track_b_calibration_role_budget,
    track_b_worst_case_token_envelope,
)
from material_agent.orchestrator.models import (
    RunStatus,
    StageExecutionRecord,
    StageId,
    StageStatus,
)
from material_agent.orchestrator.runtime import OrchestratorRuntime


def _request() -> ResearchPipelineRunRequestV1:
    return ResearchPipelineRunRequestV1(
        submission_id="unit-research-entry",
        goal="Generate one literature-grounded TiSe2 hypothesis without DFT.",
        top_k=1,
    )


def test_generic_search_fanout_never_exceeds_physical_request_ceiling() -> None:
    authoritative, candidate, counter = allocate_research_search_calls(
        provider_count=4, requested_authoritative_calls=12
    )
    assert (authoritative, candidate, counter) == (12, 1, 3)
    assert (authoritative + candidate + counter) * 4 == 64

    authoritative, candidate, counter = allocate_research_search_calls(
        provider_count=5, requested_authoritative_calls=12
    )
    assert (authoritative, candidate, counter) == (10, 1, 1)
    assert (authoritative + candidate + counter) * 5 <= 64


def test_full_research_roles_receive_non_truncating_production_budgets() -> None:
    assert RESEARCH_ROLE_RESPONSE_TIMEOUT_SECONDS == 600
    assert RESEARCH_ROLE_MAX_ATTEMPTS == 2
    for role in (
        "requirements_analyst",
        "query_strategist",
        "native_search_scout",
        "evidence_researcher",
        "database_scout",
        "mechanism_chemist",
        "skeptic",
        "hypothesis_reasoner",
        "synthesist",
        "contract_repair",
    ):
        budget = research_role_budget(
            role=role,
            requested_rounds=20,
            native_search_calls=8,
            authoritative_calls=12,
        )
        assert budget.max_tool_calls == 96
        assert budget.max_completion_tokens_per_round == 32_768
        assert budget.max_total_tokens == 1_000_000
        assert budget.max_total_tool_result_bytes == 8_000_000
        assert budget.max_walltime_seconds == 3_600

    assert research_role_budget(
        role="requirements_analyst",
        requested_rounds=20,
        native_search_calls=8,
        authoritative_calls=12,
    ).max_rounds == 6
    assert research_role_budget(
        role="native_search_scout",
        requested_rounds=20,
        native_search_calls=8,
        authoritative_calls=12,
    ).max_rounds == 11
    assert research_role_budget(
        role="evidence_researcher",
        requested_rounds=20,
        native_search_calls=8,
        authoritative_calls=12,
    ).max_rounds == 15
    assert research_role_budget(
        role="skeptic",
        requested_rounds=20,
        native_search_calls=8,
        authoritative_calls=12,
    ).max_rounds == 20
    assert research_role_budget(
        role="contract_repair",
        requested_rounds=20,
        native_search_calls=8,
        authoritative_calls=12,
    ).max_rounds == 4


def test_track_b_calibration_profile_has_a_closed_worst_case_token_envelope() -> None:
    assert track_b_worst_case_token_envelope() == 595_000
    assert track_b_worst_case_token_envelope() < TRACK_B_TOTAL_TOKEN_CEILING
    for role in (
        "requirements_analyst",
        "query_strategist",
        "native_search_scout",
        "evidence_researcher",
        "database_scout",
        "mechanism_chemist",
        "skeptic",
        "hypothesis_reasoner",
        "synthesist",
        "contract_repair",
    ):
        budget = track_b_calibration_role_budget(
            role=role,
            requested_rounds=12,
            native_search_calls=TRACK_B_MAX_NATIVE_SEARCH_CALLS,
            authoritative_calls=TRACK_B_MAX_AUTHORITATIVE_SEARCH_CALLS,
        )
        assert budget.max_total_tokens < TRACK_B_TOTAL_TOKEN_CEILING
        assert budget.max_rounds <= 8
    assert track_b_calibration_role_budget(
        role="requirements_analyst",
        requested_rounds=4,
        native_search_calls=TRACK_B_MAX_NATIVE_SEARCH_CALLS,
        authoritative_calls=TRACK_B_MAX_AUTHORITATIVE_SEARCH_CALLS,
    ).max_total_tokens == 25_000
    repair_budget = track_b_calibration_role_budget(
        role="contract_repair",
        requested_rounds=4,
        native_search_calls=TRACK_B_MAX_NATIVE_SEARCH_CALLS,
        authoritative_calls=TRACK_B_MAX_AUTHORITATIVE_SEARCH_CALLS,
    )
    assert repair_budget.max_total_tokens == 25_000
    database_budget = track_b_calibration_role_budget(
        role="database_scout",
        requested_rounds=4,
        native_search_calls=TRACK_B_MAX_NATIVE_SEARCH_CALLS,
        authoritative_calls=TRACK_B_MAX_AUTHORITATIVE_SEARCH_CALLS,
    )
    assert database_budget.max_tool_calls == 12
    assert repair_budget.max_completion_tokens_per_round == 5_000


def test_track_b_execution_profile_changes_generic_run_identity(tmp_path) -> None:
    request = GenericResearchRunRequestV1(
        submission_id="track-b-profile-unit",
        goal="Find a bounded materials hypothesis without DFT calculations.",
    )
    production = GenericMaterialsResearchService(
        workspace=tmp_path, project_id="production-identity"
    )
    benchmark = GenericMaterialsResearchService(
        workspace=tmp_path,
        project_id="benchmark-identity",
        role_budget_factory=track_b_calibration_role_budget,
        execution_profile_id=TRACK_B_CALIBRATION_PROFILE_ID,
        max_contract_repair_attempts=1,
        max_total_contract_repairs=TRACK_B_MAX_CONTRACT_REPAIRS,
    )

    assert production._identity(request)[1] != benchmark._identity(request)[1]
    assert production._identity(request)[2] != benchmark._identity(request)[2]


class _Service:
    def run(self, request):
        assert request == _request()
        return type(
            "Result",
            (),
            {
                "model_dump": lambda self, **_kwargs: {
                    "schema_version": "materials-research-pipeline-v1",
                    "run_id": "research-1",
                    "submission_id": request.submission_id,
                    "request_sha256": "a" * 64,
                    "status": ResearchPipelineStatus.PARTIAL.value,
                    "source_run_id": "agent01-1",
                    "inspiration_run_id": "inspiration-1",
                    "selected_candidate_ids": ["plan-1"],
                    "stages": [],
                    "result_artifact_uri": "artifact://research_pipeline/research-1/result.json",
                    "evidence_boundary": "HYPOTHESIS_AND_STRUCTURE_PROPOSAL_ONLY_NO_PROPERTY_CONCLUSION",
                    "scientific_conclusion": False,
                }
            },
        )()


def test_research_manifest_exposes_one_direct_non_dft_tool() -> None:
    manifest = research_pipeline_tool_manifest()

    assert len(manifest) == 1
    assert manifest[0]["name"] == RESEARCH_PIPELINE_TOOL_NAME
    properties = manifest[0]["inputSchema"]["properties"]
    assert "submission_id" not in manifest[0]["inputSchema"]["required"]
    assert properties["skip_dft"]["const"] is True
    assert properties["skip_many_body"]["const"] is True


def test_automatic_submission_identity_is_stable_and_revision_bound(tmp_path) -> None:
    service = ResearchPipelineService(workspace=tmp_path, project_id="materials")
    natural = _request().model_copy(update={"submission_id": None})

    first = service._identity(natural)
    second = service._identity(natural.model_dump(mode="json"))

    assert first == second
    assert first[0].submission_id is not None
    assert first[0].submission_id.startswith("auto-")
    assert first[2].startswith("research-")


def test_accuracy_search_environment_uses_all_available_sources() -> None:
    without_openalex = accuracy_search_environment({})
    with_openalex = accuracy_search_environment({"OPENALEX_API_KEY": "test-key"})
    explicit = accuracy_search_environment(
        {"MATERIAL_AGENT_INSPIRATION_SEARCH_PROVIDER": "osti"}
    )

    assert without_openalex["MATERIAL_AGENT_INSPIRATION_SEARCH_PROVIDER"] == (
        "crossref+arxiv+osti"
    )
    assert with_openalex["MATERIAL_AGENT_INSPIRATION_SEARCH_PROVIDER"] == (
        "crossref+openalex+arxiv+osti"
    )
    assert explicit["MATERIAL_AGENT_INSPIRATION_SEARCH_PROVIDER"] == "osti"
    assert all(
        item["MATERIAL_AGENT_INSPIRATION_SEARCH_MAX_RESULTS"] == "20"
        for item in (without_openalex, with_openalex, explicit)
    )


def test_dispatcher_validates_and_routes_direct_request() -> None:
    dispatcher = ResearchPipelineDispatcher(_Service())

    response = dispatcher.dispatch(
        RESEARCH_PIPELINE_TOOL_NAME, _request().model_dump(mode="json")
    )

    assert response["run_id"] == "research-1"
    assert response["scientific_conclusion"] is False


def test_generic_manifest_and_dispatcher_accept_open_ended_goal() -> None:
    request = GenericResearchRunRequestV1(
        submission_id="generic-unit",
        goal="Find layered transition-metal flat-band hypotheses with audited evidence.",
        reasoning_effort="max",
    )

    class GenericService:
        def run_for_tool(self, selected):
            assert selected == request
            return GenericResearchToolReceiptV1(
                run_id="generic-1",
                submission_id="generic-unit",
                request_sha256="b" * 64,
                cache_hit=False,
                canonical_result_artifact_uri=(
                    "artifact://generic_research/generic-1/result.json"
                ),
                canonical_result_artifact_sha256="c" * 64,
                canonical_result_size_bytes=610_000,
                research_graph_sha256="d" * 64,
                report_artifact_uri=(
                    "artifact://generic_research/generic-1/report.md"
                ),
                report_artifact_sha256="e" * 64,
                report_manifest_artifact_uri=(
                    "artifact://generic_research/generic-1/report_manifest.json"
                ),
                report_manifest_artifact_sha256="f" * 64,
                counts=GenericResearchToolCountsV1(
                    constraint_count=7,
                    discovery_reference_count=2,
                    resolved_evidence_count=1,
                    database_candidate_count=1,
                    hypothesis_candidate_count=1,
                    unknown_constraint_count=7,
                    required_next_computation_count=1,
                    role_count=9,
                    repair_count=0,
                    deterministic_normalization_count=0,
                ),
            )

    manifest = generic_research_tool_manifest()
    assert manifest[0]["name"] == GENERIC_RESEARCH_TOOL_NAME
    assert "workflow" not in manifest[0]["inputSchema"]["properties"]
    assert {
        "report_artifact_uri",
        "report_manifest_artifact_uri",
    } <= manifest[0]["outputSchema"]["properties"].keys()
    assert "research_graph" not in manifest[0]["outputSchema"]["properties"]
    dispatcher = ResearchPipelineDispatcher(_Service(), GenericService())
    response = dispatcher.dispatch(
        GENERIC_RESEARCH_TOOL_NAME, request.model_dump(mode="json")
    )
    assert response["run_id"] == "generic-1"
    assert response["scientific_conclusion_status"] == "REASONED_HYPOTHESIS"
    assert response["canonical_result_artifact_sha256"] == "c" * 64
    assert response["counts"]["unknown_constraint_count"] == 7
    assert "research_graph" not in response
    assert response["property_verification_complete"] is False


def test_generic_dispatcher_fails_closed_without_compact_receipt_route() -> None:
    class FullResultOnlyService:
        def run(self, _selected):
            raise AssertionError("the full result route must not be called")

    request = GenericResearchRunRequestV1(
        goal="Find one bounded hypothesis without using DFT calculations."
    )
    dispatcher = ResearchPipelineDispatcher(_Service(), FullResultOnlyService())

    with pytest.raises(
        ResearchPipelineDispatchError,
        match=r"research pipeline failed \(ResearchPipelineDispatchError\)",
    ):
        dispatcher.dispatch(
            GENERIC_RESEARCH_TOOL_NAME, request.model_dump(mode="json")
        )


def test_generic_tool_receipt_is_bounded_hash_pinned_and_cache_aware(
    tmp_path, monkeypatch
) -> None:
    service = GenericMaterialsResearchService(
        workspace=tmp_path, project_id="compact-receipt"
    )
    request = GenericResearchRunRequestV1(
        submission_id="compact-receipt-unit",
        goal="Find a bounded materials hypothesis without DFT calculations.",
    )
    selected, request_sha, run_id, result_uri = service._identity(request)
    report_uri = f"artifact://generic_research/{run_id}/report.md"
    manifest_uri = f"artifact://generic_research/{run_id}/report_manifest.json"
    graph = {
        "constraints": {"constraints": [{"constraint_id": "constraint-1"}]},
        "discovery_review": {
            "useful_lead_ids": ["lead-1"],
            "rejected_lead_ids": ["lead-2"],
        },
        "resolved_evidence": [{"evidence_id": "evidence-1"}],
        "database_candidates": [{"database_candidate_id": "db-1"}],
        "candidates": {"candidates": [{"candidate_id": "candidate-1"}]},
        "skeptic_review": {
            "matrix": [
                {
                    "candidate_id": "candidate-1",
                    "assessments": [
                        {"constraint_id": "constraint-1", "verdict": "UNKNOWN"}
                    ],
                }
            ]
        },
        "synthesis": {"required_next_computations": ["Run an ML check."]},
        "roles": [{} for _ in range(9)],
        "repairs": [{}],
        "deterministic_normalizations": ["NORMALIZED"],
        # Reproduce the observed transport pressure without constructing science.
        "large_canonical_only_payload": "x" * 610_000,
    }
    graph_sha = "e" * 64
    payload = {
        "schema_version": "materials-generic-research-run-v7",
        "implementation_revision": "generic-research-20260826-r25",
        "run_id": run_id,
        "submission_id": selected.submission_id,
        "request_sha256": request_sha,
        "status": "SUCCEEDED",
        "research_graph": graph,
        "research_graph_sha256": graph_sha,
        "result_artifact_uri": result_uri,
        "report_artifact_uri": report_uri,
        "report_manifest_artifact_uri": manifest_uri,
        "scientific_conclusion": "Stored only in the canonical result.",
        "scientific_conclusion_status": "REASONED_HYPOTHESIS",
        "property_verification_complete": False,
    }

    class FullResult:
        result_artifact_uri = result_uri
        report_artifact_uri = report_uri
        report_manifest_artifact_uri = manifest_uri

        def model_dump(self, **_kwargs):
            return payload

    def fake_run(_selected):
        service.store.write_text(
            report_uri.removeprefix("artifact://"),
            "# Durable full report\n",
            "text/markdown",
            immutable=True,
        )
        service.store.write_json(
            manifest_uri.removeprefix("artifact://"),
            {"report_artifact_uri": report_uri},
            immutable=True,
        )
        service.store.write_json(
            result_uri.removeprefix("artifact://"), payload, immutable=True
        )
        return FullResult()

    monkeypatch.setattr(service, "run", fake_run)

    first = service.run_for_tool(request)
    second = service.run_for_tool(request)
    first_payload = first.model_dump(mode="json")

    assert len(service.store.read_bytes(result_uri)) > 600_000
    assert (
        len(canonical_json_bytes(first_payload))
        < GENERIC_RESEARCH_TOOL_RECEIPT_MAX_BYTES
    )
    assert first.cache_hit is False
    assert second.cache_hit is True
    assert first.canonical_result_artifact_sha256 == (
        second.canonical_result_artifact_sha256
    )
    assert first.canonical_result_size_bytes > 600_000
    assert first.research_graph_sha256 == graph_sha
    assert first.report_artifact_sha256 is not None
    assert first.report_manifest_artifact_sha256 is not None
    assert first.counts.unknown_constraint_count == 1
    assert first.counts.role_count == 9


def test_generic_goal_preserves_full_complex_materials_contract() -> None:
    complete_goal = "Lieb lattice hard constraint. " * 220
    request = GenericResearchRunRequestV1(goal=complete_goal)
    manifest = generic_research_tool_manifest()

    assert len(complete_goal) > 4_000
    assert request.goal == complete_goal
    assert manifest[0]["inputSchema"]["properties"]["goal"]["maxLength"] == 12_000

    with pytest.raises(ValidationError):
        GenericResearchRunRequestV1(goal="x" * 12_001)


def test_finish_is_idempotent_and_keeps_scientific_boundary(tmp_path) -> None:
    service = ResearchPipelineService(workspace=tmp_path, project_id="materials")
    request = _request()
    stages = [
        ResearchStageRecordV1(
            stage_id="chgnet",
            status=ResearchStageStatus.BLOCKED,
            reason_codes=("REAL_CHGNET_BINDING_UNAVAILABLE",),
            summary="No real reviewed binding is available.",
        ),
        service._skipped("dft", "DFT_EXCLUDED_BY_RESEARCH_ENTRY"),
    ]

    result = service._finish(
        request,
        request_sha256="b" * 64,
        run_id="research-test",
        result_path="research_pipeline/research-test/result.json",
        source_run_id="agent01-test",
        inspiration_run_id="inspiration-test",
        selected_ids=("plan-1",),
        stages=stages,
    )
    recovered = service.store.read_json(result.result_artifact_uri)

    assert result.status is ResearchPipelineStatus.PARTIAL
    assert result.scientific_conclusion is False
    assert recovered == result.model_dump(mode="json")


def test_submit_returns_running_and_reuses_one_canonical_background_job(
    tmp_path, monkeypatch
) -> None:
    service = ResearchPipelineService(workspace=tmp_path, project_id="materials")
    request = _request()
    started = threading.Event()
    release = threading.Event()
    calls = 0

    def complete(selected):
        nonlocal calls
        calls += 1
        started.set()
        assert release.wait(timeout=2)
        _, request_sha, run_id, source_run_id, inspiration_run_id = service._identity(
            selected
        )
        return service._finish(
            selected,
            request_sha256=request_sha,
            run_id=run_id,
            result_path=f"research_pipeline/{run_id}/result.json",
            source_run_id=source_run_id,
            inspiration_run_id=inspiration_run_id,
            selected_ids=(),
            stages=[service._skipped("dft", "DFT_EXCLUDED_BY_RESEARCH_ENTRY")],
        )

    monkeypatch.setattr(service, "run", complete)

    first = service.submit(request)
    assert started.wait(timeout=2)
    second = service.submit(request)

    assert first.status is ResearchPipelineStatus.RUNNING
    assert second.status is ResearchPipelineStatus.RUNNING
    assert calls == 1
    release.set()
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline:
        terminal = service.submit(request)
        if terminal.status is not ResearchPipelineStatus.RUNNING:
            break
        time.sleep(0.01)
    assert terminal.status is ResearchPipelineStatus.SUCCEEDED
    assert calls == 1


def test_agent01_running_checkpoint_is_resumed_instead_of_failed(
    tmp_path, monkeypatch
) -> None:
    service = ResearchPipelineService(workspace=tmp_path, project_id="materials")
    resumed: list[str] = []

    class Runtime:
        def __init__(self, _root):
            self.repository = SimpleNamespace(get_run=lambda _run_id: {"status": "RUNNING"})

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def status(self, _run_id):
            return SimpleNamespace(
                status=RunStatus.RUNNING,
                interrupts=[],
                candidate_ids=[],
            )

        def resume(self, *, run_id):
            resumed.append(run_id)
            return SimpleNamespace(
                status=RunStatus.SUCCEEDED,
                interrupts=[],
                candidate_ids=["candidate-1"],
            )

    monkeypatch.setattr(
        "material_agent.integration.research_pipeline.OrchestratorRuntime", Runtime
    )

    view = service._run_agent01(_request(), "agent01-recovery")

    assert resumed == ["agent01-recovery"]
    assert view.candidate_ids == ["candidate-1"]


def test_terminal_research_failure_seals_stale_orchestrator_projection(
    tmp_path,
) -> None:
    service = ResearchPipelineService(workspace=tmp_path, project_id="materials")
    source_run_id = "agent01-stale-projection"
    with OrchestratorRuntime(service.project_root) as runtime:
        runtime.repository.create_run(
            run_id=source_run_id,
            project_id=service.project_id,
            raw_request="stale fixture",
            status=RunStatus.RUNNING,
        )
        runtime.repository.upsert_stage_run(
            run_id=source_run_id,
            stage="agent01",
            stage_id=StageId.RETRIEVAL,
            agent_id="agent01",
            status=StageStatus.RUNNING.value,
            attempt=1,
            operation_key="operation-stale-projection",
        )
        runtime.repository.record_stage_attempt(
            StageExecutionRecord(
                run_id=source_run_id,
                stage=StageId.RETRIEVAL,
                agent_id="agent01",
                attempt=1,
                operation_key="operation-stale-projection",
                status=StageStatus.RUNNING,
                updated_at=datetime.now(UTC),
            )
        )

    failed = service._finish(
        _request(),
        request_sha256="c" * 64,
        run_id="research-stale-projection",
        result_path="research_pipeline/research-stale-projection/result.json",
        source_run_id=source_run_id,
        inspiration_run_id=None,
        selected_ids=(),
        stages=[
            ResearchStageRecordV1(
                stage_id="agent01",
                status=ResearchStageStatus.FAILED,
                reason_codes=("RUNTIMEERROR_FAILED",),
                summary="Agent01 failed.",
            )
        ],
    )
    service._reconcile_terminal_result(failed)

    with OrchestratorRuntime(service.project_root) as runtime:
        run = runtime.repository.get_run(source_run_id)
        stage = runtime.repository.get_stage_run(source_run_id, "agent01")
        attempt = runtime.repository.get_stage_attempt(
            source_run_id, StageId.RETRIEVAL, 1
        )
    assert run is not None and run["status"] == RunStatus.FAILED.value
    assert run["current_stage"] is None
    assert stage is not None and stage["status"] == StageStatus.PERMANENT_FAILED.value
    assert attempt is not None and attempt["status"] == StageStatus.PERMANENT_FAILED.value
