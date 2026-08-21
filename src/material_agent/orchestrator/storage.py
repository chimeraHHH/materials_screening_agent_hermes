"""Versioned SQLite business-state projection for the Orchestrator."""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from material_agent.orchestrator.models import (
    ORCHESTRATOR_CONTRACT_VERSION,
    ApprovalStatus,
    ExternalJobRecord,
    RunStatus,
    StageExecutionRecord,
    StageId,
    StageStatus,
)
from material_agent.orchestrator.state_machine import (
    validate_approval_transition,
    validate_external_transition,
    validate_run_transition,
    validate_stage_transition,
)

BUSINESS_SCHEMA_VERSION = 2
LEGACY_BUSINESS_SCHEMA_VERSION = 1


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


class RepositoryConflictError(RuntimeError):
    """Raised when an immutable orchestration record conflicts."""


class UnsupportedSchemaVersion(RuntimeError):
    """Raised when a business database is newer than this runtime."""


class OrchestratorRepository:
    """Keep queryable business state separate from LangGraph checkpoints."""

    def __init__(self, database_path: Path | str) -> None:
        self.database_path = Path(database_path)
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(
            self.database_path, check_same_thread=False
        )
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA foreign_keys = ON")
        self.connection.execute("PRAGMA journal_mode = WAL")
        self._setup()

    def close(self) -> None:
        self.connection.close()

    @property
    def schema_version(self) -> int:
        row = self.connection.execute(
            "SELECT value FROM orchestrator_schema_metadata "
            "WHERE key = 'business_schema_version'"
        ).fetchone()
        if row is None:
            raise RuntimeError("business schema version is missing")
        return int(row["value"])

    def _setup(self) -> None:
        self.connection.execute(
            """
            CREATE TABLE IF NOT EXISTS orchestrator_schema_metadata (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        version_row = self.connection.execute(
            "SELECT value FROM orchestrator_schema_metadata "
            "WHERE key = 'business_schema_version'"
        ).fetchone()
        has_legacy_runs = self.connection.execute(
            "SELECT 1 FROM sqlite_master "
            "WHERE type = 'table' AND name = 'runs'"
        ).fetchone()
        if version_row is None:
            if has_legacy_runs:
                self._set_schema_version(LEGACY_BUSINESS_SCHEMA_VERSION)
            else:
                self._create_v2_tables()
                self._set_schema_version(BUSINESS_SCHEMA_VERSION)
                self.connection.commit()
                return

        version = self.schema_version
        if version > BUSINESS_SCHEMA_VERSION:
            raise UnsupportedSchemaVersion(
                f"business schema {version} is newer than supported "
                f"version {BUSINESS_SCHEMA_VERSION}"
            )
        if version == LEGACY_BUSINESS_SCHEMA_VERSION:
            self._migrate_v1_to_v2()
        self._create_v2_tables()
        self.connection.commit()

    def _set_schema_version(self, version: int) -> None:
        self.connection.execute(
            """
            INSERT INTO orchestrator_schema_metadata(key, value, updated_at)
            VALUES ('business_schema_version', ?, ?)
            ON CONFLICT(key) DO UPDATE SET
                value = excluded.value,
                updated_at = excluded.updated_at
            """,
            (str(version), _now()),
        )

    def _create_v2_tables(self) -> None:
        self.connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS projects (
                project_id TEXT PRIMARY KEY,
                project_root TEXT NOT NULL,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS runs (
                run_id TEXT PRIMARY KEY,
                project_id TEXT NOT NULL REFERENCES projects(project_id),
                thread_id TEXT NOT NULL UNIQUE,
                raw_request TEXT NOT NULL,
                status TEXT NOT NULL,
                current_stage TEXT,
                requirement_revision INTEGER,
                report_uri TEXT,
                checkpoint_schema_version TEXT NOT NULL,
                run_mode TEXT NOT NULL DEFAULT 'request',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS requirement_revisions (
                run_id TEXT NOT NULL REFERENCES runs(run_id),
                revision INTEGER NOT NULL,
                artifact_uri TEXT NOT NULL,
                artifact_sha256 TEXT NOT NULL,
                confirmed_at TEXT NOT NULL,
                PRIMARY KEY (run_id, revision)
            );

            CREATE TABLE IF NOT EXISTS stage_runs (
                run_id TEXT NOT NULL REFERENCES runs(run_id),
                stage TEXT NOT NULL,
                stage_id TEXT,
                agent_id TEXT,
                status TEXT NOT NULL,
                attempt INTEGER NOT NULL DEFAULT 1,
                operation_key TEXT,
                operation_ref TEXT,
                result_uri TEXT,
                result_sha256 TEXT,
                required INTEGER,
                disposition TEXT,
                error_json TEXT,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (run_id, stage)
            );

            CREATE TABLE IF NOT EXISTS stage_attempts (
                run_id TEXT NOT NULL REFERENCES runs(run_id),
                stage_id TEXT NOT NULL,
                attempt INTEGER NOT NULL,
                agent_id TEXT NOT NULL,
                operation_key TEXT NOT NULL,
                status TEXT NOT NULL,
                plan_uri TEXT,
                plan_sha256 TEXT,
                result_uri TEXT,
                result_sha256 TEXT,
                error_json TEXT,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (run_id, stage_id, attempt),
                UNIQUE (operation_key)
            );

            CREATE TABLE IF NOT EXISTS approvals (
                approval_id TEXT PRIMARY KEY,
                interaction_id TEXT NOT NULL UNIQUE,
                run_id TEXT NOT NULL REFERENCES runs(run_id),
                gate_type TEXT NOT NULL,
                status TEXT NOT NULL,
                input_sha256 TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                decision_json TEXT,
                created_at TEXT NOT NULL,
                decided_at TEXT
            );

            CREATE TABLE IF NOT EXISTS operations (
                idempotency_key TEXT PRIMARY KEY,
                run_id TEXT NOT NULL REFERENCES runs(run_id),
                stage TEXT NOT NULL,
                status TEXT NOT NULL,
                operation_ref TEXT,
                payload_json TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS external_jobs (
                run_id TEXT NOT NULL REFERENCES runs(run_id),
                stage_id TEXT NOT NULL,
                attempt INTEGER NOT NULL,
                backend TEXT NOT NULL,
                external_job_ref TEXT NOT NULL,
                status TEXT NOT NULL,
                status_sequence INTEGER NOT NULL,
                submit_operation_key TEXT NOT NULL UNIQUE,
                result_uri TEXT,
                result_sha256 TEXT,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (run_id, stage_id, attempt),
                UNIQUE (backend, external_job_ref)
            );

            CREATE TABLE IF NOT EXISTS interaction_responses (
                interaction_id TEXT PRIMARY KEY,
                run_id TEXT NOT NULL REFERENCES runs(run_id),
                response_json TEXT NOT NULL,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS events (
                event_key TEXT PRIMARY KEY,
                run_id TEXT NOT NULL REFERENCES runs(run_id),
                event_type TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            """
        )

    def _migrate_v1_to_v2(self) -> None:
        """Migrate only Orchestrator-owned tables, never LangGraph tables."""

        additions = {
            "runs": {
                "checkpoint_schema_version": (
                    "TEXT NOT NULL DEFAULT 'orchestrator-p0-v1'"
                ),
                "run_mode": "TEXT NOT NULL DEFAULT 'request'",
            },
            "stage_runs": {
                "stage_id": "TEXT",
                "agent_id": "TEXT",
                "attempt": "INTEGER NOT NULL DEFAULT 1",
                "operation_key": "TEXT",
                "result_sha256": "TEXT",
                "required": "INTEGER",
                "disposition": "TEXT",
                "error_json": "TEXT",
            },
        }
        for table, columns in additions.items():
            existing = {
                row["name"]
                for row in self.connection.execute(
                    f"PRAGMA table_info({table})"
                ).fetchall()
            }
            for column, declaration in columns.items():
                if column not in existing:
                    self.connection.execute(
                        f"ALTER TABLE {table} ADD COLUMN "
                        f"{column} {declaration}"
                    )
        self.connection.execute(
            """
            UPDATE stage_runs
            SET stage_id = CASE stage
                    WHEN 'agent01' THEN 'retrieval'
                    WHEN 'agent02' THEN 'ml'
                    WHEN 'agent03' THEN 'dft'
                    WHEN 'agent04' THEN 'many_body'
                    ELSE stage
                END,
                agent_id = stage
            WHERE stage_id IS NULL OR agent_id IS NULL
            """
        )
        self._create_v2_tables()
        self._set_schema_version(BUSINESS_SCHEMA_VERSION)
        self.connection.commit()

    def create_project(self, project_id: str, project_root: str) -> None:
        existing = self.connection.execute(
            "SELECT * FROM projects WHERE project_id = ?", (project_id,)
        ).fetchone()
        if existing:
            if existing["project_root"] != project_root:
                raise RepositoryConflictError(
                    f"project {project_id} already points to another root"
                )
            return
        self.connection.execute(
            "INSERT INTO projects(project_id, project_root, created_at) "
            "VALUES (?, ?, ?)",
            (project_id, project_root, _now()),
        )
        self.connection.commit()

    def get_project(self, project_id: str) -> dict[str, Any] | None:
        row = self.connection.execute(
            "SELECT * FROM projects WHERE project_id = ?", (project_id,)
        ).fetchone()
        return dict(row) if row else None

    def create_run(
        self,
        *,
        run_id: str,
        project_id: str,
        raw_request: str,
        status: RunStatus = RunStatus.INTAKE,
        run_mode: str = "request",
        checkpoint_schema_version: str = ORCHESTRATOR_CONTRACT_VERSION,
    ) -> None:
        now = _now()
        self.connection.execute(
            """
            INSERT INTO runs(
                run_id, project_id, thread_id, raw_request, status,
                checkpoint_schema_version, run_mode, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                run_id,
                project_id,
                run_id,
                raw_request,
                status.value,
                checkpoint_schema_version,
                run_mode,
                now,
                now,
            ),
        )
        self.connection.commit()

    def update_run(
        self,
        run_id: str,
        *,
        status: RunStatus | str | None = None,
        current_stage: str | None = None,
        requirement_revision: int | None = None,
        report_uri: str | None = None,
        clear_current_stage: bool = False,
    ) -> None:
        existing = self.connection.execute(
            "SELECT status FROM runs WHERE run_id = ?", (run_id,)
        ).fetchone()
        if existing is None:
            raise KeyError(f"unknown run: {run_id}")
        assignments: list[str] = ["updated_at = ?"]
        values: list[Any] = [_now()]
        if status is not None:
            target = status.value if isinstance(status, RunStatus) else status
            validate_run_transition(existing["status"], target)
            assignments.append("status = ?")
            values.append(target)
        if clear_current_stage:
            assignments.append("current_stage = NULL")
        elif current_stage is not None:
            assignments.append("current_stage = ?")
            values.append(current_stage)
        if requirement_revision is not None:
            assignments.append("requirement_revision = ?")
            values.append(requirement_revision)
        if report_uri is not None:
            assignments.append("report_uri = ?")
            values.append(report_uri)
        values.append(run_id)
        self.connection.execute(
            f"UPDATE runs SET {', '.join(assignments)} WHERE run_id = ?",
            values,
        )
        self.connection.commit()

    def get_run(self, run_id: str) -> dict[str, Any] | None:
        row = self.connection.execute(
            "SELECT * FROM runs WHERE run_id = ?", (run_id,)
        ).fetchone()
        if row is None:
            return None
        result = dict(row)
        stages = self.connection.execute(
            """
            SELECT COALESCE(agent_id, stage) AS agent_id, status
            FROM stage_runs WHERE run_id = ? ORDER BY stage
            """,
            (run_id,),
        ).fetchall()
        result["stage_statuses"] = {
            stage["agent_id"]: stage["status"] for stage in stages
        }
        return result

    def seal_failed_run(
        self,
        run_id: str,
        *,
        category: str,
        operation: str,
        public_message: str,
    ) -> None:
        """Atomically seal a nonterminal run and its active stage as failed.

        This is the durable escape hatch for an exception that crosses the
        LangGraph invocation boundary before the normal outcome-recording node
        can run.  Without it, an outer composition can persist a terminal
        failure while the Orchestrator business tables remain RUNNING.
        """

        run = self.connection.execute(
            "SELECT status, current_stage FROM runs WHERE run_id = ?", (run_id,)
        ).fetchone()
        if run is None:
            raise KeyError(f"unknown run: {run_id}")
        if run["status"] == RunStatus.FAILED.value:
            return
        terminal = {
            RunStatus.SUCCEEDED.value,
            RunStatus.PARTIAL.value,
            RunStatus.CANCELLED.value,
        }
        if run["status"] in terminal:
            raise RepositoryConflictError(
                "cannot seal a successfully completed or cancelled run as failed"
            )
        validate_run_transition(run["status"], RunStatus.FAILED.value)

        error = {
            "category": category,
            "retryable": False,
            "operation": operation,
            "public_message": public_message,
        }
        error_json = _json(error)
        now = _now()
        current_stage = run["current_stage"]
        try:
            self.connection.execute("BEGIN IMMEDIATE")
            if current_stage:
                stage = self.connection.execute(
                    """
                    SELECT stage, stage_id, agent_id, status, attempt
                    FROM stage_runs
                    WHERE run_id = ? AND (stage = ? OR agent_id = ?)
                    """,
                    (run_id, current_stage, current_stage),
                ).fetchone()
            else:
                stage = self.connection.execute(
                    """
                    SELECT stage, stage_id, agent_id, status, attempt
                    FROM stage_runs
                    WHERE run_id = ? AND status IN (?, ?, ?)
                    ORDER BY updated_at DESC LIMIT 1
                    """,
                    (
                        run_id,
                        StageStatus.VALIDATING_INPUT.value,
                        StageStatus.READY.value,
                        StageStatus.RUNNING.value,
                    ),
                ).fetchone()
            if stage is not None:
                current_stage = stage["agent_id"] or stage["stage"]
                if stage["status"] != StageStatus.PERMANENT_FAILED.value:
                    validate_stage_transition(
                        stage["status"], StageStatus.PERMANENT_FAILED.value
                    )
                    self.connection.execute(
                        """
                        UPDATE stage_runs
                        SET status = ?, error_json = ?, updated_at = ?
                        WHERE run_id = ? AND stage = ?
                        """,
                        (
                            StageStatus.PERMANENT_FAILED.value,
                            error_json,
                            now,
                            run_id,
                            stage["stage"],
                        ),
                    )
                    attempt = self.connection.execute(
                        """
                        SELECT status FROM stage_attempts
                        WHERE run_id = ? AND stage_id = ? AND attempt = ?
                        """,
                        (run_id, stage["stage_id"], stage["attempt"]),
                    ).fetchone()
                    if attempt is not None and attempt["status"] != StageStatus.PERMANENT_FAILED.value:
                        validate_stage_transition(
                            attempt["status"], StageStatus.PERMANENT_FAILED.value
                        )
                        self.connection.execute(
                            """
                            UPDATE stage_attempts
                            SET status = ?, error_json = ?, updated_at = ?
                            WHERE run_id = ? AND stage_id = ? AND attempt = ?
                            """,
                            (
                                StageStatus.PERMANENT_FAILED.value,
                                error_json,
                                now,
                                run_id,
                                stage["stage_id"],
                                stage["attempt"],
                            ),
                        )
            self.connection.execute(
                """
                UPDATE runs
                SET status = ?, current_stage = NULL, updated_at = ?
                WHERE run_id = ?
                """,
                (RunStatus.FAILED.value, now, run_id),
            )
            self.connection.execute(
                """
                INSERT OR IGNORE INTO events(
                    event_key, run_id, event_type, payload_json, created_at
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (
                    f"{run_id}:run:sealed-failed:{category}",
                    run_id,
                    "RUN_SEALED_FAILED",
                    _json({"error": error, "stage": current_stage}),
                    now,
                ),
            )
            self.connection.commit()
        except Exception:
            self.connection.rollback()
            raise

    def record_requirement(
        self,
        *,
        run_id: str,
        revision: int,
        artifact_uri: str,
        artifact_sha256: str,
    ) -> None:
        existing = self.connection.execute(
            "SELECT * FROM requirement_revisions "
            "WHERE run_id = ? AND revision = ?",
            (run_id, revision),
        ).fetchone()
        expected = (artifact_uri, artifact_sha256)
        if existing:
            actual = (existing["artifact_uri"], existing["artifact_sha256"])
            if actual != expected:
                raise RepositoryConflictError(
                    "immutable Requirement revision conflicts with existing record"
                )
            return
        self.connection.execute(
            """
            INSERT INTO requirement_revisions(
                run_id, revision, artifact_uri, artifact_sha256, confirmed_at
            ) VALUES (?, ?, ?, ?, ?)
            """,
            (run_id, revision, artifact_uri, artifact_sha256, _now()),
        )
        self.connection.commit()

    def get_requirement(
        self, run_id: str, revision: int
    ) -> dict[str, Any] | None:
        row = self.connection.execute(
            "SELECT * FROM requirement_revisions "
            "WHERE run_id = ? AND revision = ?",
            (run_id, revision),
        ).fetchone()
        return dict(row) if row else None

    def upsert_stage_run(
        self,
        *,
        run_id: str,
        stage: str,
        status: str,
        stage_id: StageId | str | None = None,
        agent_id: str | None = None,
        attempt: int = 1,
        operation_key: str | None = None,
        operation_ref: str | None = None,
        result_uri: str | None = None,
        result_sha256: str | None = None,
        required: bool | None = None,
        disposition: str | None = None,
        error: dict[str, Any] | None = None,
    ) -> None:
        existing = self.connection.execute(
            "SELECT * FROM stage_runs WHERE run_id = ? AND stage = ?",
            (run_id, stage),
        ).fetchone()
        if existing is not None:
            validate_stage_transition(existing["status"], status)
            if (
                operation_ref
                and existing["operation_ref"]
                and operation_ref != existing["operation_ref"]
            ):
                raise RepositoryConflictError(
                    "stage operation_ref changed for the same stage run"
                )
        normalized_stage_id = (
            stage_id.value if isinstance(stage_id, StageId) else stage_id
        )
        self.connection.execute(
            """
            INSERT INTO stage_runs(
                run_id, stage, stage_id, agent_id, status, attempt,
                operation_key, operation_ref, result_uri, result_sha256,
                required, disposition, error_json, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(run_id, stage) DO UPDATE SET
                stage_id = COALESCE(excluded.stage_id, stage_runs.stage_id),
                agent_id = COALESCE(excluded.agent_id, stage_runs.agent_id),
                status = excluded.status,
                attempt = excluded.attempt,
                operation_key = COALESCE(
                    excluded.operation_key, stage_runs.operation_key
                ),
                operation_ref = COALESCE(
                    excluded.operation_ref, stage_runs.operation_ref
                ),
                result_uri = COALESCE(
                    excluded.result_uri, stage_runs.result_uri
                ),
                result_sha256 = COALESCE(
                    excluded.result_sha256, stage_runs.result_sha256
                ),
                required = COALESCE(excluded.required, stage_runs.required),
                disposition = COALESCE(
                    excluded.disposition, stage_runs.disposition
                ),
                error_json = COALESCE(
                    excluded.error_json, stage_runs.error_json
                ),
                updated_at = excluded.updated_at
            """,
            (
                run_id,
                stage,
                normalized_stage_id,
                agent_id or stage,
                status,
                attempt,
                operation_key,
                operation_ref,
                result_uri,
                result_sha256,
                None if required is None else int(required),
                disposition,
                _json(error) if error else None,
                _now(),
            ),
        )
        self.connection.commit()

    def get_stage_run(self, run_id: str, stage: str) -> dict[str, Any] | None:
        row = self.connection.execute(
            """
            SELECT * FROM stage_runs
            WHERE run_id = ? AND (stage = ? OR stage_id = ? OR agent_id = ?)
            """,
            (run_id, stage, stage, stage),
        ).fetchone()
        if row is None:
            return None
        result = dict(row)
        result["error"] = (
            json.loads(result["error_json"]) if result["error_json"] else None
        )
        return result

    def record_stage_attempt(self, record: StageExecutionRecord) -> None:
        payload = record.model_dump(mode="json")
        existing = self.connection.execute(
            """
            SELECT * FROM stage_attempts
            WHERE run_id = ? AND stage_id = ? AND attempt = ?
            """,
            (record.run_id, record.stage.value, record.attempt),
        ).fetchone()
        if existing:
            if existing["operation_key"] != record.operation_key:
                raise RepositoryConflictError(
                    "stage attempt operation key is immutable"
                )
            if (
                existing["plan_sha256"]
                and record.plan_sha256
                and existing["plan_sha256"] != record.plan_sha256
            ):
                raise RepositoryConflictError(
                    "stage attempt plan hash changed"
                )
            if (
                existing["plan_uri"]
                and record.plan_uri
                and existing["plan_uri"] != record.plan_uri
            ):
                raise RepositoryConflictError(
                    "stage attempt plan URI changed"
                )
            validate_stage_transition(existing["status"], record.status.value)
            if (
                existing["result_sha256"]
                and record.result_sha256
                and existing["result_sha256"] != record.result_sha256
            ):
                raise RepositoryConflictError(
                    "stage attempt result hash changed"
                )
        self.connection.execute(
            """
            INSERT INTO stage_attempts(
                run_id, stage_id, attempt, agent_id, operation_key, status,
                plan_uri, plan_sha256, result_uri, result_sha256,
                error_json, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(run_id, stage_id, attempt) DO UPDATE SET
                status = excluded.status,
                result_uri = COALESCE(
                    excluded.result_uri, stage_attempts.result_uri
                ),
                result_sha256 = COALESCE(
                    excluded.result_sha256, stage_attempts.result_sha256
                ),
                error_json = COALESCE(
                    excluded.error_json, stage_attempts.error_json
                ),
                updated_at = excluded.updated_at
            """,
            (
                record.run_id,
                record.stage.value,
                record.attempt,
                record.agent_id,
                record.operation_key,
                record.status.value,
                record.plan_uri,
                record.plan_sha256,
                record.result_uri,
                record.result_sha256,
                _json(payload["error"]) if payload["error"] else None,
                record.updated_at.isoformat(),
            ),
        )
        self.connection.commit()

    def get_stage_attempt(
        self,
        run_id: str,
        stage: StageId | str,
        attempt: int,
    ) -> dict[str, Any] | None:
        stage_id = stage.value if isinstance(stage, StageId) else stage
        row = self.connection.execute(
            """
            SELECT * FROM stage_attempts
            WHERE run_id = ? AND stage_id = ? AND attempt = ?
            """,
            (run_id, stage_id, attempt),
        ).fetchone()
        if row is None:
            return None
        result = dict(row)
        result["error"] = (
            json.loads(result["error_json"]) if result["error_json"] else None
        )
        return result

    def ensure_approval(
        self,
        *,
        approval_id: str,
        interaction_id: str,
        run_id: str,
        gate_type: str,
        input_sha256: str,
        payload: dict[str, Any],
    ) -> None:
        payload_json = _json(payload)
        existing = self.connection.execute(
            "SELECT * FROM approvals WHERE approval_id = ?", (approval_id,)
        ).fetchone()
        if existing:
            actual = (
                existing["interaction_id"],
                existing["run_id"],
                existing["gate_type"],
                existing["input_sha256"],
                existing["payload_json"],
            )
            expected = (
                interaction_id,
                run_id,
                gate_type,
                input_sha256,
                payload_json,
            )
            if actual != expected:
                raise RepositoryConflictError(
                    "approval ID conflicts with a different input snapshot"
                )
            return
        self.connection.execute(
            """
            INSERT INTO approvals(
                approval_id, interaction_id, run_id, gate_type, status,
                input_sha256, payload_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                approval_id,
                interaction_id,
                run_id,
                gate_type,
                ApprovalStatus.PENDING.value,
                input_sha256,
                payload_json,
                _now(),
            ),
        )
        self.connection.commit()

    def decide_approval(
        self,
        approval_id: str,
        *,
        status: ApprovalStatus,
        decision: dict[str, Any],
    ) -> None:
        row = self.connection.execute(
            "SELECT * FROM approvals WHERE approval_id = ?", (approval_id,)
        ).fetchone()
        if row is None:
            raise KeyError(f"unknown approval: {approval_id}")
        decision_json = _json(decision)
        if row["status"] != ApprovalStatus.PENDING.value:
            if row["status"] == status.value and row["decision_json"] == decision_json:
                return
            raise RepositoryConflictError(
                f"approval {approval_id} was already decided"
            )
        validate_approval_transition(row["status"], status)
        self.connection.execute(
            """
            UPDATE approvals
            SET status = ?, decision_json = ?, decided_at = ?
            WHERE approval_id = ?
            """,
            (status.value, decision_json, _now(), approval_id),
        )
        self.connection.commit()

    def get_approval(self, approval_id: str) -> dict[str, Any] | None:
        row = self.connection.execute(
            "SELECT * FROM approvals WHERE approval_id = ?", (approval_id,)
        ).fetchone()
        if row is None:
            return None
        result = dict(row)
        result["payload"] = json.loads(result.pop("payload_json"))
        result["decision"] = (
            json.loads(result["decision_json"])
            if result["decision_json"]
            else None
        )
        return result

    def record_operation(
        self,
        *,
        idempotency_key: str,
        run_id: str,
        stage: str,
        status: str,
        operation_ref: str | None,
        payload: dict[str, Any],
    ) -> None:
        payload_json = _json(payload)
        existing = self.connection.execute(
            "SELECT * FROM operations WHERE idempotency_key = ?",
            (idempotency_key,),
        ).fetchone()
        if existing:
            if existing["run_id"] != run_id or existing["stage"] != stage:
                raise RepositoryConflictError(
                    "idempotency key belongs to another run or stage"
                )
            if (
                existing["operation_ref"]
                and operation_ref
                and existing["operation_ref"] != operation_ref
            ):
                raise RepositoryConflictError(
                    "operation reference changed for an idempotency key"
                )
            if existing["status"] in {
                StageStatus.SUCCEEDED.value,
                StageStatus.PARTIAL.value,
                StageStatus.PERMANENT_FAILED.value,
                StageStatus.CANCELLED.value,
            }:
                if (
                    existing["status"] != status
                    or existing["payload_json"] != payload_json
                ):
                    raise RepositoryConflictError(
                        "terminal operation conflicts with existing record"
                    )
                return
        self.connection.execute(
            """
            INSERT INTO operations(
                idempotency_key, run_id, stage, status, operation_ref,
                payload_json, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(idempotency_key) DO UPDATE SET
                status = excluded.status,
                operation_ref = COALESCE(
                    excluded.operation_ref, operations.operation_ref
                ),
                payload_json = excluded.payload_json,
                updated_at = excluded.updated_at
            """,
            (
                idempotency_key,
                run_id,
                stage,
                status,
                operation_ref,
                payload_json,
                _now(),
            ),
        )
        self.connection.commit()

    def get_operation(self, idempotency_key: str) -> dict[str, Any] | None:
        row = self.connection.execute(
            "SELECT * FROM operations WHERE idempotency_key = ?",
            (idempotency_key,),
        ).fetchone()
        if row is None:
            return None
        result = dict(row)
        result["payload"] = json.loads(result.pop("payload_json"))
        return result

    def record_external_job(self, record: ExternalJobRecord) -> None:
        self.validate_external_job_record(record)
        self.connection.execute(
            """
            INSERT INTO external_jobs(
                run_id, stage_id, attempt, backend, external_job_ref,
                status, status_sequence, submit_operation_key,
                result_uri, result_sha256, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(run_id, stage_id, attempt) DO UPDATE SET
                status = excluded.status,
                status_sequence = excluded.status_sequence,
                result_uri = COALESCE(
                    excluded.result_uri, external_jobs.result_uri
                ),
                result_sha256 = COALESCE(
                    excluded.result_sha256, external_jobs.result_sha256
                ),
                updated_at = excluded.updated_at
            """,
            (
                record.run_id,
                record.stage.value,
                record.attempt,
                record.backend,
                record.external_job_ref,
                record.status.value,
                record.status_sequence,
                record.submit_operation_key,
                record.result_uri,
                record.result_sha256,
                record.updated_at.isoformat(),
            ),
        )
        self.connection.commit()

    def validate_external_job_record(
        self, record: ExternalJobRecord
    ) -> None:
        existing = self.connection.execute(
            """
            SELECT * FROM external_jobs
            WHERE run_id = ? AND stage_id = ? AND attempt = ?
            """,
            (record.run_id, record.stage.value, record.attempt),
        ).fetchone()
        if existing:
            identity = (
                existing["backend"],
                existing["external_job_ref"],
                existing["submit_operation_key"],
            )
            expected = (
                record.backend,
                record.external_job_ref,
                record.submit_operation_key,
            )
            if identity != expected:
                raise RepositoryConflictError(
                    "external job identity changed for an existing attempt"
                )
            if record.status_sequence < existing["status_sequence"]:
                raise RepositoryConflictError(
                    "external backend status sequence moved backwards"
                )
            if (
                record.status_sequence == existing["status_sequence"]
                and record.status.value != existing["status"]
            ):
                raise RepositoryConflictError(
                    "external backend changed status at the same sequence"
                )
            validate_external_transition(existing["status"], record.status)
            if (
                existing["result_sha256"]
                and record.result_sha256
                and existing["result_sha256"] != record.result_sha256
            ):
                raise RepositoryConflictError(
                    "external completed result hash changed"
                )

    def get_external_job(
        self, run_id: str, stage: StageId | str, attempt: int | None = None
    ) -> dict[str, Any] | None:
        normalized = stage.value if isinstance(stage, StageId) else stage
        if attempt is None:
            row = self.connection.execute(
                """
                SELECT * FROM external_jobs
                WHERE run_id = ? AND stage_id = ?
                ORDER BY attempt DESC LIMIT 1
                """,
                (run_id, normalized),
            ).fetchone()
        else:
            row = self.connection.execute(
                """
                SELECT * FROM external_jobs
                WHERE run_id = ? AND stage_id = ? AND attempt = ?
                """,
                (run_id, normalized, attempt),
            ).fetchone()
        return dict(row) if row else None

    def append_event(
        self,
        *,
        event_key: str,
        run_id: str,
        event_type: str,
        payload: dict[str, Any],
    ) -> None:
        payload_json = _json(payload)
        existing = self.connection.execute(
            "SELECT * FROM events WHERE event_key = ?", (event_key,)
        ).fetchone()
        if existing:
            if (
                existing["run_id"] != run_id
                or existing["event_type"] != event_type
                or existing["payload_json"] != payload_json
            ):
                raise RepositoryConflictError(
                    "event key conflicts with a different event payload"
                )
            return
        self.connection.execute(
            """
            INSERT INTO events(
                event_key, run_id, event_type, payload_json, created_at
            ) VALUES (?, ?, ?, ?, ?)
            """,
            (event_key, run_id, event_type, payload_json, _now()),
        )
        self.connection.commit()

    def record_interaction_response(
        self,
        *,
        interaction_id: str,
        run_id: str,
        response: dict[str, Any],
    ) -> None:
        response_json = _json(response)
        existing = self.connection.execute(
            "SELECT * FROM interaction_responses WHERE interaction_id = ?",
            (interaction_id,),
        ).fetchone()
        if existing:
            if (
                existing["run_id"] != run_id
                or existing["response_json"] != response_json
            ):
                raise RepositoryConflictError(
                    "interaction was already answered differently"
                )
            return
        self.connection.execute(
            """
            INSERT INTO interaction_responses(
                interaction_id, run_id, response_json, created_at
            ) VALUES (?, ?, ?, ?)
            """,
            (interaction_id, run_id, response_json, _now()),
        )
        self.connection.commit()

    def get_interaction_response(
        self, interaction_id: str
    ) -> dict[str, Any] | None:
        row = self.connection.execute(
            "SELECT * FROM interaction_responses WHERE interaction_id = ?",
            (interaction_id,),
        ).fetchone()
        if row is None:
            return None
        result = dict(row)
        result["response"] = json.loads(result.pop("response_json"))
        return result
