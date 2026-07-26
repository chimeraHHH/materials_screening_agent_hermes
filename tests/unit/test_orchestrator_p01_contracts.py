from __future__ import annotations

import sqlite3

import pytest
from pydantic import ValidationError

from material_agent.orchestrator.models import (
    ControlOutcomeType,
    ControlStageOutcome,
    ORCHESTRATOR_CONTRACT_VERSION,
    StageId,
    StageStatus,
)
from material_agent.orchestrator.state_machine import (
    InvalidStateTransition,
    validate_run_transition,
    validate_stage_transition,
)
from material_agent.orchestrator.storage import (
    BUSINESS_SCHEMA_VERSION,
    OrchestratorRepository,
    RepositoryConflictError,
)


def test_control_outcome_is_strict_and_separate_from_agent01_envelope() -> None:
    outcome = ControlStageOutcome(
        stage=StageId.RETRIEVAL,
        agent_id="agent01",
        outcome=ControlOutcomeType.COMPLETED,
        status=StageStatus.SUCCEEDED,
        idempotency_key="operation-key",
        native_result_uri="artifact://native/result.json",
        native_result_sha256="abc",
    )
    payload = outcome.model_dump(mode="json")

    assert payload["schema_version"] == ORCHESTRATOR_CONTRACT_VERSION
    assert "candidate_ids" not in payload
    with pytest.raises(ValidationError):
        ControlStageOutcome.model_validate({**payload, "unknown": True})
    with pytest.raises(ValidationError):
        ControlStageOutcome.model_validate(
            {
                **payload,
                "native_result_sha256": None,
            }
        )


def test_waiting_external_requires_running_stage_and_job_identity() -> None:
    with pytest.raises(ValidationError):
        ControlStageOutcome(
            stage=StageId.DFT,
            agent_id="agent03",
            outcome=ControlOutcomeType.WAITING_EXTERNAL,
            status=StageStatus.SUCCEEDED,
            idempotency_key="operation-key",
        )


def test_transition_matrix_rejects_terminal_regression() -> None:
    with pytest.raises(InvalidStateTransition):
        validate_run_transition("SUCCEEDED", "RUNNING")
    with pytest.raises(InvalidStateTransition):
        validate_stage_transition("SUCCEEDED", "RUNNING")


def test_business_schema_migrates_legacy_tables_without_touching_checkpoints(
    tmp_path,
) -> None:
    database_path = tmp_path / "legacy.sqlite3"
    connection = sqlite3.connect(database_path)
    connection.executescript(
        """
        CREATE TABLE projects (
            project_id TEXT PRIMARY KEY,
            project_root TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE TABLE runs (
            run_id TEXT PRIMARY KEY,
            project_id TEXT NOT NULL REFERENCES projects(project_id),
            thread_id TEXT NOT NULL UNIQUE,
            raw_request TEXT NOT NULL,
            status TEXT NOT NULL,
            current_stage TEXT,
            requirement_revision INTEGER,
            report_uri TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE TABLE stage_runs (
            run_id TEXT NOT NULL REFERENCES runs(run_id),
            stage TEXT NOT NULL,
            status TEXT NOT NULL,
            operation_ref TEXT,
            result_uri TEXT,
            updated_at TEXT NOT NULL,
            PRIMARY KEY (run_id, stage)
        );
        CREATE TABLE checkpoint_sentinel (
            value TEXT NOT NULL
        );
        INSERT INTO checkpoint_sentinel(value) VALUES ('untouched');
        INSERT INTO projects VALUES (
            'project-legacy', '/tmp/project-legacy', '2026-01-01T00:00:00Z'
        );
        INSERT INTO runs VALUES (
            'run-legacy', 'project-legacy', 'run-legacy', 'request',
            'PAUSED', 'agent01', NULL, NULL,
            '2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z'
        );
        INSERT INTO stage_runs VALUES (
            'run-legacy', 'agent01', 'RETRYABLE_FAILED', NULL, NULL,
            '2026-01-01T00:00:00Z'
        );
        """
    )
    connection.commit()
    connection.close()

    repository = OrchestratorRepository(database_path)
    try:
        assert repository.schema_version == BUSINESS_SCHEMA_VERSION
        legacy = repository.get_run("run-legacy")
        assert legacy["checkpoint_schema_version"] == "orchestrator-p0-v1"
        assert legacy["stage_statuses"] == {
            "agent01": "RETRYABLE_FAILED"
        }
        assert repository.connection.execute(
            "SELECT value FROM checkpoint_sentinel"
        ).fetchone()[0] == "untouched"
    finally:
        repository.close()


def test_event_key_conflict_is_not_silently_ignored(tmp_path) -> None:
    repository = OrchestratorRepository(tmp_path / "events.sqlite3")
    try:
        repository.create_project("project-test", str(tmp_path))
        repository.create_run(
            run_id="run-test",
            project_id="project-test",
            raw_request="request",
        )
        repository.append_event(
            event_key="event-key",
            run_id="run-test",
            event_type="TEST",
            payload={"value": 1},
        )
        repository.append_event(
            event_key="event-key",
            run_id="run-test",
            event_type="TEST",
            payload={"value": 1},
        )
        with pytest.raises(RepositoryConflictError):
            repository.append_event(
                event_key="event-key",
                run_id="run-test",
                event_type="TEST",
                payload={"value": 2},
            )
    finally:
        repository.close()
