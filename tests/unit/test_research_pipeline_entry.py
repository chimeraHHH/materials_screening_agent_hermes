from __future__ import annotations

import threading
import time
from datetime import UTC, datetime
from types import SimpleNamespace

from material_agent.integration.generic_research import (
    GENERIC_RESEARCH_TOOL_NAME,
    GenericResearchRunRequestV1,
    generic_research_tool_manifest,
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
        def run(self, selected):
            assert selected == request
            return type(
                "GenericResult",
                (),
                {
                    "model_dump": lambda self, **_kwargs: {
                        "schema_version": "materials-generic-research-run-v4",
                        "run_id": "generic-1",
                        "submission_id": "generic-unit",
                        "request_sha256": "b" * 64,
                        "status": "SUCCEEDED",
                        "scientific_conclusion": (
                            "The layered candidate is a reasoned hypothesis."
                        ),
                        "scientific_conclusion_status": "REASONED_HYPOTHESIS",
                        "property_verification_complete": False,
                    }
                },
            )()

    manifest = generic_research_tool_manifest()
    assert manifest[0]["name"] == GENERIC_RESEARCH_TOOL_NAME
    assert "workflow" not in manifest[0]["inputSchema"]["properties"]
    assert {
        "report_artifact_uri",
        "report_manifest_artifact_uri",
    } <= manifest[0]["outputSchema"]["properties"].keys()
    dispatcher = ResearchPipelineDispatcher(_Service(), GenericService())
    response = dispatcher.dispatch(
        GENERIC_RESEARCH_TOOL_NAME, request.model_dump(mode="json")
    )
    assert response["run_id"] == "generic-1"
    assert response["scientific_conclusion_status"] == "REASONED_HYPOTHESIS"
    assert response["scientific_conclusion"]
    assert response["property_verification_complete"] is False


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
