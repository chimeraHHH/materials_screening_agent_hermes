from __future__ import annotations

import json
import sqlite3

import pytest
import requests

from material_agent.orchestrator.graph import OrchestratorGraph
from material_agent.orchestrator.models import RunStatus
from material_agent.orchestrator.runtime import OrchestratorRuntime


ACCEPTANCE_REQUEST = (
    "从 Materials Project 中寻找同时包含 Si 和 O、带隙为 0.5–1.0 eV、"
    "energy above hull 不超过 0.05 eV/atom 的非金属材料。"
)


def test_p0_resumes_across_runtime_instances_and_finishes_report(
    tmp_path, fixture_payload
) -> None:
    OrchestratorRuntime.create_project(tmp_path, "project-p0")
    with OrchestratorRuntime.from_workspace(tmp_path, "project-p0") as runtime:
        waiting = runtime.start_run(
            raw_request=ACCEPTANCE_REQUEST,
            fixture_payload=fixture_payload,
            run_id="run-p0",
        )
        assert waiting.status is RunStatus.REQUIREMENT_REVIEW
        assert len(waiting.interrupts) == 1
        pending = waiting.interrupts[0].value
        assert pending["interaction_type"] == "REQUIREMENT_CONFIRMATION"
        approval_id = pending["approval_id"]

    with OrchestratorRuntime.from_workspace(tmp_path, "project-p0") as runtime:
        completed = runtime.approve(
            run_id="run-p0",
            approval_id=approval_id,
            decision="approve",
        )
        assert completed.status is RunStatus.SUCCEEDED
        assert completed.stage_statuses == {"agent01": "SUCCEEDED"}
        assert len(completed.candidate_ids) == 1
        assert completed.report_uri == "artifact://reports/run-p0/report.md"
        report = runtime.read_report("run-p0")
        assert "Materials Project retrieval evidence at L1" in report
        resumed = runtime.resume(run_id="run-p0")
        assert resumed == completed

        database = sqlite3.connect(runtime.database_path)
        try:
            assert database.execute(
                "SELECT COUNT(*) FROM checkpoints WHERE thread_id = ?",
                ("run-p0",),
            ).fetchone()[0] > 1
            assert database.execute(
                "SELECT COUNT(*) FROM operations WHERE run_id = ?",
                ("run-p0",),
            ).fetchone()[0] == 1
        finally:
            database.close()


def test_orchestrator_run_selects_nomad_as_the_only_retrieval_source(
    tmp_path, requirement, fixture_payload
) -> None:
    OrchestratorRuntime.create_project(tmp_path, "project-nomad")
    with OrchestratorRuntime.from_workspace(
        tmp_path, "project-nomad"
    ) as runtime:
        waiting = runtime.start_run(
            raw_request="structured",
            initial_requirement=requirement.model_dump(mode="json"),
            fixture_payload=fixture_payload,
            run_id="run-nomad",
            retrieval_source="nomad",
        )
        approval_id = waiting.interrupts[0].value["approval_id"]
        completed = runtime.approve(
            run_id="run-nomad",
            approval_id=approval_id,
            decision="approve",
        )

        assert completed.status is RunStatus.SUCCEEDED
        assert "NOMAD retrieval evidence at L1" in runtime.read_report(
            "run-nomad"
        )
        manifest = json.loads(
            runtime.store.read_bytes(
                "artifact://stages/agent01/run-nomad/candidate_manifest.jsonl"
            ).decode("utf-8")
        )
        assert manifest["schema_version"] == "agent01-contract-v2"
        assert manifest["source_database"] == "nomad"


def test_requirement_rejection_cancels_without_calling_agent01(
    tmp_path, requirement, fixture_payload
) -> None:
    OrchestratorRuntime.create_project(tmp_path, "project-reject")
    with OrchestratorRuntime.from_workspace(
        tmp_path, "project-reject"
    ) as runtime:
        waiting = runtime.start_run(
            raw_request="structured",
            initial_requirement=requirement.model_dump(mode="json"),
            fixture_payload=fixture_payload,
            run_id="run-reject",
        )
        approval_id = waiting.interrupts[0].value["approval_id"]
        cancelled = runtime.approve(
            run_id="run-reject",
            approval_id=approval_id,
            decision="reject",
            reason="change the scientific request",
        )

        assert cancelled.status is RunStatus.CANCELLED
        assert cancelled.stage_statuses == {}
        assert cancelled.report_uri is None


def test_clarification_response_is_checkpointed_before_review(
    tmp_path, requirement, fixture_payload
) -> None:
    OrchestratorRuntime.create_project(tmp_path, "project-clarify")
    with OrchestratorRuntime.from_workspace(
        tmp_path, "project-clarify"
    ) as runtime:
        clarifying = runtime.start_run(
            raw_request="帮我找一些材料",
            fixture_payload=fixture_payload,
            run_id="run-clarify",
        )
        assert clarifying.status is RunStatus.CLARIFYING
        interaction = clarifying.interrupts[0]
        assert interaction.value["interaction_type"] == "CLARIFICATION"

        reviewing = runtime.respond(
            run_id="run-clarify",
            interaction_id=interaction.interaction_id,
            response={
                "requirement": requirement.model_dump(mode="json"),
            },
        )
        assert reviewing.status is RunStatus.REQUIREMENT_REVIEW
        assert reviewing.interrupts[0].value["interaction_type"] == (
            "REQUIREMENT_CONFIRMATION"
        )


def test_natural_language_clarification_reaches_requirement_review(
    tmp_path, fixture_payload
) -> None:
    OrchestratorRuntime.create_project(tmp_path, "project-clarify-text")
    with OrchestratorRuntime.from_workspace(
        tmp_path, "project-clarify-text"
    ) as runtime:
        clarifying = runtime.start_run(
            raw_request="帮我找一些材料",
            fixture_payload=fixture_payload,
            run_id="run-clarify-text",
        )
        interaction = clarifying.interrupts[0]

        reviewing = runtime.respond(
            run_id="run-clarify-text",
            interaction_id=interaction.interaction_id,
            response={"answer": ACCEPTANCE_REQUEST},
        )

        assert reviewing.status is RunStatus.REQUIREMENT_REVIEW
        confirmation = reviewing.interrupts[0].value
        assert confirmation["interaction_type"] == "REQUIREMENT_CONFIRMATION"
        requirement = confirmation["payload"]["requirement"]
        assert requirement["hard_constraints"]["include_elements"] == ["O", "Si"]
        assert requirement["hard_constraints"]["band_gap_ev"] == {
            "min": 0.5,
            "max": 1.0,
            "unit": "eV",
        }
        database = sqlite3.connect(runtime.database_path)
        try:
            event = database.execute(
                "SELECT payload_json FROM events "
                "WHERE run_id = ? AND event_type = ?",
                (
                    "run-clarify-text",
                    "REQUIREMENT_CLARIFICATION_PARSED",
                ),
            ).fetchone()
        finally:
            database.close()

    assert json.loads(event[0])["parser"] == "offline-demo-parser"


def test_stale_interaction_id_is_rejected(
    tmp_path, requirement, fixture_payload
) -> None:
    OrchestratorRuntime.create_project(tmp_path, "project-stale")
    with OrchestratorRuntime.from_workspace(
        tmp_path, "project-stale"
    ) as runtime:
        runtime.start_run(
            raw_request="structured",
            initial_requirement=requirement.model_dump(mode="json"),
            fixture_payload=fixture_payload,
            run_id="run-stale",
        )
        with pytest.raises(KeyError, match="no pending interaction"):
            runtime.respond(
                run_id="run-stale",
                interaction_id="interaction-stale",
                response={"decision": "approve"},
            )


def test_fixture_is_copied_into_project_for_cross_process_resume(
    tmp_path, requirement, fixture_payload
) -> None:
    OrchestratorRuntime.create_project(tmp_path, "project-fixture")
    with OrchestratorRuntime.from_workspace(
        tmp_path, "project-fixture"
    ) as runtime:
        waiting = runtime.start_run(
            raw_request="structured",
            initial_requirement=requirement.model_dump(mode="json"),
            fixture_payload=fixture_payload,
            run_id="run-fixture",
        )
        fixture_path = (
            runtime.project_root
            / "inputs"
            / "run-fixture"
            / "materials_fixture.json"
        )
        assert json.loads(fixture_path.read_text()) == fixture_payload
        assert waiting.status is RunStatus.REQUIREMENT_REVIEW


def test_retryable_stage_failure_pauses_and_can_be_cancelled(
    tmp_path, requirement, fixture_payload, monkeypatch
) -> None:
    class TimeoutRunner:
        def prepare(self, _context):
            raise requests.Timeout("temporary timeout")

    monkeypatch.setattr(
        OrchestratorGraph,
        "_agent01_runner",
        lambda self, state, policy: TimeoutRunner(),
    )
    OrchestratorRuntime.create_project(tmp_path, "project-timeout")
    with OrchestratorRuntime.from_workspace(
        tmp_path, "project-timeout"
    ) as runtime:
        waiting = runtime.start_run(
            raw_request="structured",
            initial_requirement=requirement.model_dump(mode="json"),
            fixture_payload=fixture_payload,
            run_id="run-timeout",
        )
        paused = runtime.approve(
            run_id="run-timeout",
            approval_id=waiting.interrupts[0].value["approval_id"],
            decision="approve",
        )
        assert paused.status is RunStatus.PAUSED
        assert paused.stage_statuses == {"agent01": "RETRYABLE_FAILED"}
        assert paused.interrupts[0].value["interaction_type"] == (
            "RETRY_CONFIRMATION"
        )

        cancelled = runtime.cancel(
            run_id="run-timeout", reason="do not retry"
        )
        assert cancelled.status is RunStatus.CANCELLED
        assert cancelled.report_uri is not None
