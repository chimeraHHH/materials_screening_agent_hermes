"""Crash-safe SQLite job queue for bounded Gateway background work.

The queue is deliberately a separate execution ledger from operator approval
semantics.  For asynchronous actions, grant consumption and the initial outbox
insert can nevertheless share one SQLite transaction.  Subsequent leasing is
independent; lease tokens and monotonically increasing generations fence
workers that resume after their lease expired.
"""

from __future__ import annotations

import hashlib
import os
import re
import secrets
import sqlite3
import time
from contextlib import contextmanager
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from threading import RLock
from typing import Any, Callable, Iterator, Mapping

from material_agent.gateway.models import canonical_json_bytes


JOB_QUEUE_SCHEMA_VERSION = 2
MAX_PAYLOAD_BYTES = 1_000_000
MAX_CHECKPOINT_BYTES = 1_000_000
MAX_RESULT_BYTES = 1_000_000
MAX_LEASE_SECONDS = 3_600
_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_ERROR_CODE = re.compile(r"^[A-Z][A-Z0-9_]{0,127}$")


class GatewayJobQueueError(RuntimeError):
    """Base class for queue contract and persistence failures."""


class JobEnqueueConflictError(GatewayJobQueueError):
    """An idempotency key was reused with different immutable input."""


class JobNotFoundError(GatewayJobQueueError):
    """The requested job does not exist."""


class JobLeaseLostError(GatewayJobQueueError):
    """The worker no longer owns a live, matching lease."""


class JobCheckpointConflictError(GatewayJobQueueError):
    """Checkpoint compare-and-swap detected a stale writer."""


class JobTerminalConflictError(GatewayJobQueueError):
    """A terminal job was retried with a different terminal outcome."""


class JobFailureAcknowledgementConflictError(GatewayJobQueueError):
    """A terminal-failure acknowledgement was replayed with different input."""


class JobQueuePersistenceError(GatewayJobQueueError):
    """Persisted queue state is invalid or incompatible."""


class GatewayJobStatus(StrEnum):
    READY = "READY"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"


@dataclass(frozen=True)
class GatewayJob:
    job_id: str
    queue_name: str
    job_kind: str
    idempotency_key: str
    payload: dict[str, Any]
    payload_sha256: str
    status: GatewayJobStatus
    priority: int
    attempt_count: int
    lease_generation: int
    lease_owner: str | None
    lease_token: str | None
    lease_expires_at_ms: int | None
    checkpoint: dict[str, Any] | None
    checkpoint_sha256: str | None
    checkpoint_sequence: int
    result: dict[str, Any] | None
    result_sha256: str | None
    failure_code: str | None
    failure_message: str | None
    created_at_ms: int
    updated_at_ms: int
    terminal_at_ms: int | None
    failure_acknowledged_at_ms: int | None
    failure_acknowledged_by: str | None
    failure_acknowledgement_reason: str | None


@dataclass(frozen=True)
class GatewayJobAuditEvent:
    event_id: int
    job_id: str
    event_type: str
    occurred_at_ms: int
    lease_generation: int
    actor: str | None
    details: dict[str, Any]


@dataclass(frozen=True)
class GatewayJobAttempt:
    """One lease generation's durable operational timing record.

    Epoch timestamps make a crashed attempt reconstructable across processes.
    Durations marked ``MONOTONIC`` are measured by one live supervisor; a
    recovered attempt that never reported them is explicitly marked
    ``WALL_CLOCK_FALLBACK`` instead of pretending wall clock is monotonic.
    """

    job_id: str
    lease_generation: int
    lease_owner: str
    claimed_at_ms: int
    queue_wait_ms: int | None
    clock_anomaly: bool
    deadline_budget_ms: int | None
    execution_started_at_ms: int | None
    child_started_at_ms: int | None
    child_exited_at_ms: int | None
    ended_at_ms: int | None
    outcome: str
    duration_source: str | None
    worker_active_ms: int | None
    child_active_ms: int | None
    recovery_ms: int | None
    termination_grace_ms: int | None
    child_exit_code: int | None
    child_signal: int | None
    timeout_phase: str | None


def canonical_payload_sha256(payload: Mapping[str, Any]) -> str:
    """Return the exact canonical hash used by enqueue conflict detection."""

    if not isinstance(payload, Mapping):
        raise TypeError("job payload must be a mapping")
    return hashlib.sha256(canonical_json_bytes(dict(payload))).hexdigest()


def _identifier(value: str, *, field: str) -> str:
    if not isinstance(value, str) or _IDENTIFIER.fullmatch(value) is None:
        raise ValueError(f"{field} must be a bounded identifier")
    return value


def _canonical_mapping(
    value: Mapping[str, Any], *, field: str, max_bytes: int
) -> tuple[str, str]:
    if not isinstance(value, Mapping):
        raise TypeError(f"{field} must be a mapping")
    try:
        payload = canonical_json_bytes(dict(value))
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} must contain finite JSON values") from exc
    if len(payload) > max_bytes:
        raise ValueError(f"{field} exceeds its byte limit")
    return payload.decode("utf-8"), hashlib.sha256(payload).hexdigest()


class SqliteGatewayJobQueue:
    """Thread/process-safe durable queue using short ``BEGIN IMMEDIATE`` writes."""

    def __init__(
        self,
        database_path: Path | str,
        *,
        clock_ms: Callable[[], int] | None = None,
        token_factory: Callable[[], str] | None = None,
    ) -> None:
        self.database_path = Path(database_path)
        parent = self.database_path.parent
        if parent.is_symlink():
            raise JobQueuePersistenceError("queue database directory cannot be a symlink")
        parent.mkdir(parents=True, exist_ok=True)
        if not parent.is_dir() or self.database_path.is_symlink():
            raise JobQueuePersistenceError("queue database path is unsafe")
        if self.database_path.exists() and not self.database_path.is_file():
            raise JobQueuePersistenceError("queue database path is not a regular file")
        self._clock_ms = clock_ms or (lambda: time.time_ns() // 1_000_000)
        self._token_factory = token_factory or (lambda: secrets.token_hex(32))
        self._lock = RLock()
        self.connection = sqlite3.connect(
            self.database_path,
            timeout=30.0,
            check_same_thread=False,
            isolation_level=None,
        )
        if self.database_path.is_symlink():
            self.connection.close()
            raise JobQueuePersistenceError("queue database path became a symlink")
        os.chmod(self.database_path, 0o600)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA foreign_keys = ON")
        self.connection.execute("PRAGMA journal_mode = WAL")
        self.connection.execute("PRAGMA synchronous = FULL")
        self.connection.execute("PRAGMA busy_timeout = 30000")
        try:
            self._setup()
        except Exception:
            self.connection.close()
            raise

    def close(self) -> None:
        with self._lock:
            self.connection.close()

    def __enter__(self) -> SqliteGatewayJobQueue:
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()

    def _setup(self) -> None:
        schema_v2 = """
                CREATE TABLE gateway_job_queue_metadata (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
                CREATE TABLE gateway_jobs (
                    job_id TEXT PRIMARY KEY,
                    queue_name TEXT NOT NULL,
                    job_kind TEXT NOT NULL,
                    idempotency_key TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    payload_sha256 TEXT NOT NULL,
                    status TEXT NOT NULL CHECK(status IN ('READY','RUNNING','SUCCEEDED','FAILED')),
                    priority INTEGER NOT NULL,
                    attempt_count INTEGER NOT NULL DEFAULT 0 CHECK(attempt_count >= 0),
                    lease_generation INTEGER NOT NULL DEFAULT 0 CHECK(lease_generation >= 0),
                    lease_owner TEXT,
                    lease_token TEXT,
                    lease_expires_at_ms INTEGER,
                    checkpoint_json TEXT,
                    checkpoint_sha256 TEXT,
                    checkpoint_sequence INTEGER NOT NULL DEFAULT 0 CHECK(checkpoint_sequence >= 0),
                    result_json TEXT,
                    result_sha256 TEXT,
                    failure_code TEXT,
                    failure_message TEXT,
                    terminal_lease_owner TEXT,
                    terminal_lease_token TEXT,
                    terminal_at_ms INTEGER,
                    failure_acknowledged_at_ms INTEGER,
                    failure_acknowledged_by TEXT,
                    failure_acknowledgement_reason TEXT,
                    created_at_ms INTEGER NOT NULL,
                    updated_at_ms INTEGER NOT NULL,
                    UNIQUE(queue_name, idempotency_key),
                    CHECK(
                        (status = 'RUNNING' AND lease_owner IS NOT NULL
                         AND lease_token IS NOT NULL AND lease_expires_at_ms IS NOT NULL)
                        OR
                        (status != 'RUNNING' AND lease_owner IS NULL
                         AND lease_token IS NULL AND lease_expires_at_ms IS NULL)
                    )
                );
                CREATE INDEX gateway_jobs_claim_idx
                    ON gateway_jobs(queue_name, status, priority DESC, created_at_ms, job_id);
                CREATE TABLE gateway_job_events (
                    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    job_id TEXT NOT NULL REFERENCES gateway_jobs(job_id) ON DELETE RESTRICT,
                    event_type TEXT NOT NULL,
                    occurred_at_ms INTEGER NOT NULL,
                    lease_generation INTEGER NOT NULL,
                    actor TEXT,
                    details_json TEXT NOT NULL
                );
                CREATE INDEX gateway_job_events_job_idx
                    ON gateway_job_events(job_id, event_id);
                CREATE TABLE gateway_job_attempts (
                    job_id TEXT NOT NULL REFERENCES gateway_jobs(job_id) ON DELETE RESTRICT,
                    lease_generation INTEGER NOT NULL CHECK(lease_generation > 0),
                    lease_owner TEXT NOT NULL,
                    claimed_at_ms INTEGER NOT NULL,
                    queue_wait_ms INTEGER,
                    clock_anomaly INTEGER NOT NULL CHECK(clock_anomaly IN (0,1)),
                    deadline_budget_ms INTEGER,
                    execution_started_at_ms INTEGER,
                    child_started_at_ms INTEGER,
                    child_exited_at_ms INTEGER,
                    ended_at_ms INTEGER,
                    outcome TEXT NOT NULL,
                    duration_source TEXT,
                    worker_active_ms INTEGER,
                    child_active_ms INTEGER,
                    recovery_ms INTEGER,
                    termination_grace_ms INTEGER,
                    child_exit_code INTEGER,
                    child_signal INTEGER,
                    timeout_phase TEXT,
                    PRIMARY KEY(job_id, lease_generation),
                    CHECK(queue_wait_ms IS NULL OR queue_wait_ms >= 0),
                    CHECK(deadline_budget_ms IS NULL OR deadline_budget_ms > 0),
                    CHECK(worker_active_ms IS NULL OR worker_active_ms >= 0),
                    CHECK(child_active_ms IS NULL OR child_active_ms >= 0),
                    CHECK(recovery_ms IS NULL OR recovery_ms >= 0),
                    CHECK(termination_grace_ms IS NULL OR termination_grace_ms >= 0)
                );
                """
        attempt_table_sql = schema_v2[
            schema_v2.index("CREATE TABLE gateway_job_attempts") :
        ]
        with self._lock:
            objects = {
                row["name"]: row["type"]
                for row in self.connection.execute(
                    "SELECT name,type FROM sqlite_master "
                    "WHERE name LIKE 'gateway_job%'"
                ).fetchall()
            }
            queue_tables = {
                "gateway_job_queue_metadata",
                "gateway_jobs",
                "gateway_job_events",
                "gateway_job_attempts",
            }
            present_tables = {
                name for name, kind in objects.items() if kind == "table"
            }
            if not (present_tables & queue_tables):
                try:
                    self.connection.executescript(
                        "BEGIN IMMEDIATE;"
                        + schema_v2
                        + "INSERT INTO gateway_job_queue_metadata(key,value) "
                        f"VALUES('schema_version','{JOB_QUEUE_SCHEMA_VERSION}');"
                        + "COMMIT;"
                    )
                except Exception:
                    if self.connection.in_transaction:
                        self.connection.execute("ROLLBACK")
                    raise
            else:
                if "gateway_job_queue_metadata" not in present_tables:
                    raise JobQueuePersistenceError(
                        "Gateway job queue metadata is missing"
                    )
                metadata = self.connection.execute(
                    "SELECT key,value FROM gateway_job_queue_metadata ORDER BY key"
                ).fetchall()
                if len(metadata) != 1 or metadata[0]["key"] != "schema_version":
                    raise JobQueuePersistenceError(
                        "Gateway job queue metadata is incomplete"
                    )
                version = metadata[0]["value"]
                if version == "1":
                    expected_v1_tables = {
                        "gateway_job_queue_metadata",
                        "gateway_jobs",
                        "gateway_job_events",
                    }
                    if present_tables & queue_tables != expected_v1_tables:
                        raise JobQueuePersistenceError(
                            "Gateway job queue v1 schema is incomplete"
                        )
                    required_v1_columns = self._required_job_columns() - {
                        "failure_acknowledged_at_ms",
                        "failure_acknowledged_by",
                        "failure_acknowledgement_reason",
                    }
                    if self._table_columns("gateway_jobs") != required_v1_columns:
                        raise JobQueuePersistenceError(
                            "Gateway job queue v1 jobs schema differs"
                        )
                    if self._table_columns("gateway_job_events") != self._required_event_columns():
                        raise JobQueuePersistenceError(
                            "Gateway job queue v1 events schema differs"
                        )
                    with self._transaction():
                        for name in (
                            "failure_acknowledged_at_ms",
                            "failure_acknowledged_by",
                            "failure_acknowledgement_reason",
                        ):
                            self.connection.execute(
                                f"ALTER TABLE gateway_jobs ADD COLUMN {name} "
                                + ("INTEGER" if name.endswith("_ms") else "TEXT")
                            )
                        self.connection.execute(attempt_table_sql)
                        cursor = self.connection.execute(
                            "UPDATE gateway_job_queue_metadata SET value=? "
                            "WHERE key='schema_version' AND value='1'",
                            (str(JOB_QUEUE_SCHEMA_VERSION),),
                        )
                        if cursor.rowcount != 1:
                            raise JobQueuePersistenceError(
                                "Gateway job queue migration lost its version fence"
                            )
                elif version != str(JOB_QUEUE_SCHEMA_VERSION):
                    raise JobQueuePersistenceError(
                        "unsupported Gateway job queue schema version"
                    )
            self._validate_current_schema()

    @staticmethod
    def _required_job_columns() -> set[str]:
        return {
            "attempt_count",
            "checkpoint_json",
            "checkpoint_sequence",
            "checkpoint_sha256",
            "created_at_ms",
            "failure_acknowledged_at_ms",
            "failure_acknowledged_by",
            "failure_acknowledgement_reason",
            "failure_code",
            "failure_message",
            "idempotency_key",
            "job_id",
            "job_kind",
            "lease_expires_at_ms",
            "lease_generation",
            "lease_owner",
            "lease_token",
            "payload_json",
            "payload_sha256",
            "priority",
            "queue_name",
            "result_json",
            "result_sha256",
            "status",
            "terminal_at_ms",
            "terminal_lease_owner",
            "terminal_lease_token",
            "updated_at_ms",
        }

    @staticmethod
    def _required_event_columns() -> set[str]:
        return {
            "actor",
            "details_json",
            "event_id",
            "event_type",
            "job_id",
            "lease_generation",
            "occurred_at_ms",
        }

    def _table_columns(self, table: str) -> set[str]:
        return {
            item["name"]
            for item in self.connection.execute(f"PRAGMA table_info({table})").fetchall()
        }

    def _validate_current_schema(self) -> None:
        metadata = self.connection.execute(
            "SELECT key,value FROM gateway_job_queue_metadata ORDER BY key"
        ).fetchall()
        if (
            len(metadata) != 1
            or metadata[0]["key"] != "schema_version"
            or metadata[0]["value"] != str(JOB_QUEUE_SCHEMA_VERSION)
        ):
            raise JobQueuePersistenceError("Gateway job queue metadata is invalid")
        if self._table_columns("gateway_jobs") != self._required_job_columns():
            raise JobQueuePersistenceError("Gateway jobs schema is incomplete")
        if self._table_columns("gateway_job_events") != self._required_event_columns():
            raise JobQueuePersistenceError("Gateway job events schema is incomplete")
        required_attempt_columns = set(GatewayJobAttempt.__dataclass_fields__)
        if self._table_columns("gateway_job_attempts") != required_attempt_columns:
            raise JobQueuePersistenceError("Gateway job attempt schema is incomplete")
        indexes = {
            row["name"]
            for row in self.connection.execute(
                "SELECT name FROM sqlite_master WHERE type='index' "
                "AND name IN ('gateway_jobs_claim_idx','gateway_job_events_job_idx')"
            ).fetchall()
        }
        if indexes != {"gateway_jobs_claim_idx", "gateway_job_events_job_idx"}:
            raise JobQueuePersistenceError("Gateway job queue indexes are incomplete")
        expected_index_keys = {
            "gateway_jobs_claim_idx": (
                ("queue_name", 0),
                ("status", 0),
                ("priority", 1),
                ("created_at_ms", 0),
                ("job_id", 0),
            ),
            "gateway_job_events_job_idx": (("job_id", 0), ("event_id", 0)),
        }
        for index_name, expected in expected_index_keys.items():
            observed = tuple(
                (row["name"], row["desc"])
                for row in self.connection.execute(
                    f"PRAGMA index_xinfo({index_name})"
                ).fetchall()
                if row["key"] == 1
            )
            if observed != expected:
                raise JobQueuePersistenceError(
                    "Gateway job queue index definition differs"
                )
        sql_rows = {
            row["name"]: "".join((row["sql"] or "").upper().split())
            for row in self.connection.execute(
                "SELECT name,sql FROM sqlite_master WHERE type='table' "
                "AND name IN ('gateway_jobs','gateway_job_events','gateway_job_attempts')"
            ).fetchall()
        }
        required_sql_tokens = {
            "gateway_jobs": (
                "UNIQUE(QUEUE_NAME,IDEMPOTENCY_KEY)",
                "STATUSIN('READY','RUNNING','SUCCEEDED','FAILED')",
                "STATUS='RUNNING'ANDLEASE_OWNERISNOTNULL",
            ),
            "gateway_job_events": (
                "REFERENCESGATEWAY_JOBS(JOB_ID)ONDELETERESTRICT",
            ),
            "gateway_job_attempts": (
                "PRIMARYKEY(JOB_ID,LEASE_GENERATION)",
                "REFERENCESGATEWAY_JOBS(JOB_ID)ONDELETERESTRICT",
                "CLOCK_ANOMALYIN(0,1)",
            ),
        }
        if set(sql_rows) != set(required_sql_tokens) or any(
            token not in sql_rows[name]
            for name, tokens in required_sql_tokens.items()
            for token in tokens
        ):
            raise JobQueuePersistenceError(
                "Gateway job queue constraints are incomplete"
            )

    @contextmanager
    def _transaction(self) -> Iterator[None]:
        with self._lock:
            try:
                self.connection.execute("BEGIN IMMEDIATE")
                yield
                self.connection.execute("COMMIT")
            except Exception:
                if self.connection.in_transaction:
                    self.connection.execute("ROLLBACK")
                raise

    def _now(self) -> int:
        value = self._clock_ms()
        if not isinstance(value, int) or value < 0:
            raise ValueError("clock_ms must return a non-negative integer")
        return value

    @staticmethod
    def _job_id(queue_name: str, idempotency_key: str) -> str:
        identity = canonical_json_bytes(
            {
                "schema_version": "materials-gateway-job-identity-v1",
                "queue_name": queue_name,
                "idempotency_key": idempotency_key,
            }
        )
        return "job-" + hashlib.sha256(identity).hexdigest()[:32]

    @staticmethod
    def _decode_mapping(value: str | None) -> dict[str, Any] | None:
        if value is None:
            return None
        import json

        decoded = json.loads(value)
        if not isinstance(decoded, dict):
            raise JobQueuePersistenceError("persisted job JSON is not an object")
        return decoded

    @classmethod
    def _decode_job(cls, row: sqlite3.Row) -> GatewayJob:
        try:
            payload = cls._decode_mapping(row["payload_json"])
            if payload is None:
                raise JobQueuePersistenceError("persisted job payload is missing")
            checkpoint = cls._decode_mapping(row["checkpoint_json"])
            result = cls._decode_mapping(row["result_json"])
            status = GatewayJobStatus(row["status"])
        except (TypeError, ValueError, KeyError) as exc:
            raise JobQueuePersistenceError("persisted Gateway job is invalid") from exc
        if canonical_payload_sha256(payload) != row["payload_sha256"]:
            raise JobQueuePersistenceError("persisted job payload SHA-256 disagrees")
        if checkpoint is not None and canonical_payload_sha256(checkpoint) != row["checkpoint_sha256"]:
            raise JobQueuePersistenceError("persisted checkpoint SHA-256 disagrees")
        if result is not None and canonical_payload_sha256(result) != row["result_sha256"]:
            raise JobQueuePersistenceError("persisted result SHA-256 disagrees")
        return GatewayJob(
            job_id=row["job_id"], queue_name=row["queue_name"], job_kind=row["job_kind"],
            idempotency_key=row["idempotency_key"], payload=payload,
            payload_sha256=row["payload_sha256"], status=status, priority=row["priority"],
            attempt_count=row["attempt_count"], lease_generation=row["lease_generation"],
            lease_owner=row["lease_owner"], lease_token=row["lease_token"],
            lease_expires_at_ms=row["lease_expires_at_ms"], checkpoint=checkpoint,
            checkpoint_sha256=row["checkpoint_sha256"], checkpoint_sequence=row["checkpoint_sequence"],
            result=result, result_sha256=row["result_sha256"], failure_code=row["failure_code"],
            failure_message=row["failure_message"], created_at_ms=row["created_at_ms"],
            updated_at_ms=row["updated_at_ms"], terminal_at_ms=row["terminal_at_ms"],
            failure_acknowledged_at_ms=row["failure_acknowledged_at_ms"],
            failure_acknowledged_by=row["failure_acknowledged_by"],
            failure_acknowledgement_reason=row["failure_acknowledgement_reason"],
        )

    @staticmethod
    def _decode_attempt(row: sqlite3.Row) -> GatewayJobAttempt:
        attempt = GatewayJobAttempt(
            job_id=row["job_id"],
            lease_generation=row["lease_generation"],
            lease_owner=row["lease_owner"],
            claimed_at_ms=row["claimed_at_ms"],
            queue_wait_ms=row["queue_wait_ms"],
            clock_anomaly=bool(row["clock_anomaly"]),
            deadline_budget_ms=row["deadline_budget_ms"],
            execution_started_at_ms=row["execution_started_at_ms"],
            child_started_at_ms=row["child_started_at_ms"],
            child_exited_at_ms=row["child_exited_at_ms"],
            ended_at_ms=row["ended_at_ms"],
            outcome=row["outcome"],
            duration_source=row["duration_source"],
            worker_active_ms=row["worker_active_ms"],
            child_active_ms=row["child_active_ms"],
            recovery_ms=row["recovery_ms"],
            termination_grace_ms=row["termination_grace_ms"],
            child_exit_code=row["child_exit_code"],
            child_signal=row["child_signal"],
            timeout_phase=row["timeout_phase"],
        )
        allowed_outcomes = {
            "ACTIVE",
            "FAILED",
            "HARD_DEADLINE_EXPIRED",
            "LEASE_EXPIRED",
            "RELEASED",
            "SUCCEEDED",
        }
        if (
            _IDENTIFIER.fullmatch(attempt.job_id) is None
            or _IDENTIFIER.fullmatch(attempt.lease_owner) is None
            or attempt.lease_generation <= 0
            or attempt.claimed_at_ms < 0
            or attempt.outcome not in allowed_outcomes
            or attempt.duration_source
            not in {None, "MONOTONIC", "WALL_CLOCK_FALLBACK"}
        ):
            raise JobQueuePersistenceError("persisted Gateway attempt is invalid")
        nonnegative = (
            attempt.queue_wait_ms,
            attempt.execution_started_at_ms,
            attempt.child_started_at_ms,
            attempt.child_exited_at_ms,
            attempt.ended_at_ms,
            attempt.worker_active_ms,
            attempt.child_active_ms,
            attempt.recovery_ms,
            attempt.termination_grace_ms,
        )
        if any(value is not None and value < 0 for value in nonnegative):
            raise JobQueuePersistenceError(
                "persisted Gateway attempt contains a negative timing"
            )
        if attempt.deadline_budget_ms is not None and attempt.deadline_budget_ms <= 0:
            raise JobQueuePersistenceError(
                "persisted Gateway attempt deadline is invalid"
            )
        if attempt.timeout_phase is not None and _IDENTIFIER.fullmatch(
            attempt.timeout_phase
        ) is None:
            raise JobQueuePersistenceError(
                "persisted Gateway attempt timeout phase is invalid"
            )
        if attempt.child_exited_at_ms is not None and attempt.child_started_at_ms is None:
            raise JobQueuePersistenceError(
                "persisted Gateway attempt child timestamps are incomplete"
            )
        if attempt.child_started_at_ms is not None and attempt.execution_started_at_ms is None:
            raise JobQueuePersistenceError(
                "persisted Gateway attempt child predates execution"
            )
        if attempt.outcome == "ACTIVE" and attempt.ended_at_ms is not None:
            raise JobQueuePersistenceError(
                "active Gateway attempt has a terminal timestamp"
            )
        if attempt.outcome != "ACTIVE" and (
            attempt.ended_at_ms is None
            or attempt.duration_source is None
            or attempt.worker_active_ms is None
        ):
            raise JobQueuePersistenceError(
                "terminal Gateway attempt has incomplete timing"
            )
        ordered = tuple(
            value
            for value in (
                attempt.claimed_at_ms,
                attempt.execution_started_at_ms,
                attempt.child_started_at_ms,
                attempt.child_exited_at_ms,
                attempt.ended_at_ms,
            )
            if value is not None
        )
        if not attempt.clock_anomaly and any(
            later < earlier for earlier, later in zip(ordered, ordered[1:])
        ):
            raise JobQueuePersistenceError(
                "persisted Gateway attempt timestamps move backwards"
            )
        return attempt

    def _event(
        self, job_id: str, event_type: str, now: int, generation: int,
        *, actor: str | None, details: Mapping[str, Any],
    ) -> None:
        details_json, _ = _canonical_mapping(details, field="audit details", max_bytes=32_768)
        self.connection.execute(
            "INSERT INTO gateway_job_events(job_id,event_type,occurred_at_ms,lease_generation,actor,details_json) VALUES(?,?,?,?,?,?)",
            (job_id, event_type, now, generation, actor, details_json),
        )

    def enqueue(
        self, *, queue_name: str, job_kind: str, idempotency_key: str,
        payload: Mapping[str, Any], priority: int = 0,
    ) -> tuple[GatewayJob, bool]:
        queue_name = _identifier(queue_name, field="queue_name")
        job_kind = _identifier(job_kind, field="job_kind")
        idempotency_key = _identifier(idempotency_key, field="idempotency_key")
        if not isinstance(priority, int) or not -1_000 <= priority <= 1_000:
            raise ValueError("priority must be an integer from -1000 to 1000")
        payload_json, payload_sha = _canonical_mapping(
            payload, field="job payload", max_bytes=MAX_PAYLOAD_BYTES
        )
        job_id = self._job_id(queue_name, idempotency_key)
        now = self._now()
        with self._transaction():
            row = self.connection.execute(
                "SELECT * FROM gateway_jobs WHERE queue_name=? AND idempotency_key=?",
                (queue_name, idempotency_key),
            ).fetchone()
            if row is not None:
                job = self._decode_job(row)
                if (job.payload_sha256, job.job_kind, job.priority) != (payload_sha, job_kind, priority):
                    raise JobEnqueueConflictError("idempotency key is bound to different immutable input")
                return job, False
            self.connection.execute(
                """INSERT INTO gateway_jobs(
                    job_id,queue_name,job_kind,idempotency_key,payload_json,payload_sha256,
                    status,priority,created_at_ms,updated_at_ms
                ) VALUES(?,?,?,?,?,?,'READY',?,?,?)""",
                (job_id, queue_name, job_kind, idempotency_key, payload_json, payload_sha, priority, now, now),
            )
            self._event(job_id, "ENQUEUED", now, 0, actor=None, details={"payload_sha256": payload_sha})
            row = self.connection.execute("SELECT * FROM gateway_jobs WHERE job_id=?", (job_id,)).fetchone()
            assert row is not None
            return self._decode_job(row), True

    def claim(self, *, queue_name: str, lease_owner: str, lease_seconds: int) -> GatewayJob | None:
        queue_name = _identifier(queue_name, field="queue_name")
        lease_owner = _identifier(lease_owner, field="lease_owner")
        if not isinstance(lease_seconds, int) or not 1 <= lease_seconds <= MAX_LEASE_SECONDS:
            raise ValueError("lease_seconds is outside the allowed range")
        now = self._now()
        token = self._token_factory()
        if not isinstance(token, str) or re.fullmatch(r"[0-9a-f]{64}", token) is None:
            raise ValueError("token_factory must return 64 lowercase hex characters")
        with self._transaction():
            row = self.connection.execute(
                """SELECT * FROM gateway_jobs
                   WHERE queue_name=? AND (status='READY' OR (status='RUNNING' AND lease_expires_at_ms<=?))
                   ORDER BY priority DESC, created_at_ms, job_id LIMIT 1""",
                (queue_name, now),
            ).fetchone()
            if row is None:
                return None
            reclaimed = row["status"] == "RUNNING"
            generation = row["lease_generation"] + 1
            expires = now + lease_seconds * 1_000
            if reclaimed:
                previous = self.connection.execute(
                    "SELECT claimed_at_ms FROM gateway_job_attempts "
                    "WHERE job_id=? AND lease_generation=? AND outcome='ACTIVE'",
                    (row["job_id"], row["lease_generation"]),
                ).fetchone()
                if previous is not None:
                    fallback_duration = max(0, now - previous["claimed_at_ms"])
                    self.connection.execute(
                        """UPDATE gateway_job_attempts
                           SET ended_at_ms=?,outcome='LEASE_EXPIRED',
                               duration_source=COALESCE(
                                   duration_source,'WALL_CLOCK_FALLBACK'
                               ),
                               worker_active_ms=COALESCE(worker_active_ms,?),
                               clock_anomaly=MAX(clock_anomaly,?)
                           WHERE job_id=? AND lease_generation=? AND outcome='ACTIVE'""",
                        (
                            now,
                            fallback_duration,
                            int(now < previous["claimed_at_ms"]),
                            row["job_id"],
                            row["lease_generation"],
                        ),
                    )
            self.connection.execute(
                """UPDATE gateway_jobs SET status='RUNNING',attempt_count=attempt_count+1,
                   lease_generation=?,lease_owner=?,lease_token=?,lease_expires_at_ms=?,updated_at_ms=?
                   WHERE job_id=?""",
                (generation, lease_owner, token, expires, now, row["job_id"]),
            )
            queue_origin = (
                row["lease_expires_at_ms"] if reclaimed else row["created_at_ms"]
            )
            clock_anomaly = queue_origin is None or now < queue_origin
            queue_wait_ms = None if clock_anomaly else now - queue_origin
            self.connection.execute(
                """INSERT INTO gateway_job_attempts(
                       job_id,lease_generation,lease_owner,claimed_at_ms,
                       queue_wait_ms,clock_anomaly,outcome
                   ) VALUES(?,?,?,?,?,?,'ACTIVE')""",
                (
                    row["job_id"],
                    generation,
                    lease_owner,
                    now,
                    queue_wait_ms,
                    int(clock_anomaly),
                ),
            )
            self._event(
                row["job_id"], "LEASE_RECLAIMED" if reclaimed else "LEASE_CLAIMED",
                now, generation, actor=lease_owner,
                details={"expires_at_ms": expires, "previous_owner": row["lease_owner"] if reclaimed else None},
            )
            claimed = self.connection.execute("SELECT * FROM gateway_jobs WHERE job_id=?", (row["job_id"],)).fetchone()
            assert claimed is not None
            return self._decode_job(claimed)

    def _active_row(self, job_id: str, owner: str, token: str, now: int) -> sqlite3.Row:
        _identifier(job_id, field="job_id")
        _identifier(owner, field="lease_owner")
        if re.fullmatch(r"[0-9a-f]{64}", token or "") is None:
            raise JobLeaseLostError("lease token is invalid")
        row = self.connection.execute("SELECT * FROM gateway_jobs WHERE job_id=?", (job_id,)).fetchone()
        if row is None:
            raise JobNotFoundError(job_id)
        if (
            row["status"] != "RUNNING" or row["lease_owner"] != owner
            or row["lease_token"] != token or row["lease_expires_at_ms"] <= now
        ):
            raise JobLeaseLostError("job lease is absent, expired, or fenced")
        return row

    def heartbeat(self, *, job_id: str, lease_owner: str, lease_token: str, lease_seconds: int) -> GatewayJob:
        if not isinstance(lease_seconds, int) or not 1 <= lease_seconds <= MAX_LEASE_SECONDS:
            raise ValueError("lease_seconds is outside the allowed range")
        now = self._now()
        with self._transaction():
            row = self._active_row(job_id, lease_owner, lease_token, now)
            expires = now + lease_seconds * 1_000
            self.connection.execute(
                "UPDATE gateway_jobs SET lease_expires_at_ms=?,updated_at_ms=? WHERE job_id=?",
                (expires, now, job_id),
            )
            self._event(job_id, "LEASE_HEARTBEAT", now, row["lease_generation"], actor=lease_owner, details={"expires_at_ms": expires})
            updated = self.connection.execute("SELECT * FROM gateway_jobs WHERE job_id=?", (job_id,)).fetchone()
            assert updated is not None
            return self._decode_job(updated)

    def begin_attempt_execution(
        self,
        *,
        job_id: str,
        lease_owner: str,
        lease_token: str,
        deadline_budget_ms: int,
    ) -> GatewayJobAttempt:
        """Bind one request-specific hard deadline to the active lease attempt."""

        if not isinstance(deadline_budget_ms, int) or deadline_budget_ms <= 0:
            raise ValueError("deadline_budget_ms must be a positive integer")
        now = self._now()
        with self._transaction():
            row = self._active_row(job_id, lease_owner, lease_token, now)
            attempt = self.connection.execute(
                "SELECT * FROM gateway_job_attempts WHERE job_id=? AND lease_generation=?",
                (job_id, row["lease_generation"]),
            ).fetchone()
            if attempt is None:
                raise JobQueuePersistenceError("active lease has no attempt ledger")
            if attempt["execution_started_at_ms"] is not None:
                if attempt["deadline_budget_ms"] != deadline_budget_ms:
                    raise JobCheckpointConflictError(
                        "attempt deadline is already bound to another budget"
                    )
                return self._decode_attempt(attempt)
            self.connection.execute(
                """UPDATE gateway_job_attempts
                   SET execution_started_at_ms=?,deadline_budget_ms=?
                   WHERE job_id=? AND lease_generation=? AND outcome='ACTIVE'""",
                (now, deadline_budget_ms, job_id, row["lease_generation"]),
            )
            self._event(
                job_id,
                "EXECUTION_STARTED",
                now,
                row["lease_generation"],
                actor=lease_owner,
                details={"deadline_budget_ms": deadline_budget_ms},
            )
            updated = self.connection.execute(
                "SELECT * FROM gateway_job_attempts WHERE job_id=? AND lease_generation=?",
                (job_id, row["lease_generation"]),
            ).fetchone()
            assert updated is not None
            return self._decode_attempt(updated)

    def mark_child_started(
        self,
        *,
        job_id: str,
        lease_owner: str,
        lease_token: str,
    ) -> GatewayJobAttempt:
        now = self._now()
        with self._transaction():
            row = self._active_row(job_id, lease_owner, lease_token, now)
            attempt = self.connection.execute(
                "SELECT * FROM gateway_job_attempts WHERE job_id=? AND lease_generation=?",
                (job_id, row["lease_generation"]),
            ).fetchone()
            if attempt is None or attempt["execution_started_at_ms"] is None:
                raise JobQueuePersistenceError(
                    "child cannot start before the attempt deadline is bound"
                )
            if attempt["child_started_at_ms"] is not None:
                return self._decode_attempt(attempt)
            self.connection.execute(
                "UPDATE gateway_job_attempts SET child_started_at_ms=? "
                "WHERE job_id=? AND lease_generation=? AND outcome='ACTIVE'",
                (now, job_id, row["lease_generation"]),
            )
            self._event(
                job_id,
                "CHILD_STARTED",
                now,
                row["lease_generation"],
                actor=lease_owner,
                details={},
            )
            updated = self.connection.execute(
                "SELECT * FROM gateway_job_attempts WHERE job_id=? AND lease_generation=?",
                (job_id, row["lease_generation"]),
            ).fetchone()
            assert updated is not None
            return self._decode_attempt(updated)

    def record_attempt_timing(
        self,
        *,
        job_id: str,
        lease_owner: str,
        lease_token: str,
        worker_active_ms: int,
        child_active_ms: int | None = None,
        recovery_ms: int = 0,
        termination_grace_ms: int = 0,
        child_exit_code: int | None = None,
        child_signal: int | None = None,
        timeout_phase: str | None = None,
    ) -> GatewayJobAttempt:
        """Persist same-process monotonic timings before terminal state changes."""

        durations = {
            "worker_active_ms": worker_active_ms,
            "child_active_ms": child_active_ms,
            "recovery_ms": recovery_ms,
            "termination_grace_ms": termination_grace_ms,
        }
        for name, value in durations.items():
            if value is not None and (not isinstance(value, int) or value < 0):
                raise ValueError(f"{name} must be a non-negative integer or None")
        for name, value in (
            ("child_exit_code", child_exit_code),
            ("child_signal", child_signal),
        ):
            if value is not None and not isinstance(value, int):
                raise ValueError(f"{name} must be an integer or None")
        if timeout_phase is not None:
            timeout_phase = _identifier(timeout_phase, field="timeout_phase")
        now = self._now()
        with self._transaction():
            row = self._active_row(job_id, lease_owner, lease_token, now)
            attempt = self.connection.execute(
                "SELECT * FROM gateway_job_attempts WHERE job_id=? AND lease_generation=?",
                (job_id, row["lease_generation"]),
            ).fetchone()
            if attempt is None or attempt["execution_started_at_ms"] is None:
                raise JobQueuePersistenceError("attempt execution has not started")
            supplied = {
                **durations,
                "child_exit_code": child_exit_code,
                "child_signal": child_signal,
                "timeout_phase": timeout_phase,
            }
            if attempt["duration_source"] is not None:
                if any(attempt[name] != value for name, value in supplied.items()):
                    raise JobCheckpointConflictError(
                        "attempt timing was replayed with different values"
                    )
                return self._decode_attempt(attempt)
            child_exited_at_ms = (
                now
                if attempt["child_started_at_ms"] is not None
                or child_active_ms is not None
                else None
            )
            self.connection.execute(
                """UPDATE gateway_job_attempts
                   SET child_exited_at_ms=?,duration_source='MONOTONIC',
                       worker_active_ms=?,child_active_ms=?,recovery_ms=?,
                       termination_grace_ms=?,child_exit_code=?,child_signal=?,
                       timeout_phase=?
                   WHERE job_id=? AND lease_generation=? AND outcome='ACTIVE'""",
                (
                    child_exited_at_ms,
                    worker_active_ms,
                    child_active_ms,
                    recovery_ms,
                    termination_grace_ms,
                    child_exit_code,
                    child_signal,
                    timeout_phase,
                    job_id,
                    row["lease_generation"],
                ),
            )
            self._event(
                job_id,
                "CHILD_EXITED",
                now,
                row["lease_generation"],
                actor=lease_owner,
                details={
                    "child_exit_code": child_exit_code,
                    "child_signal": child_signal,
                    "timeout_phase": timeout_phase,
                    "worker_active_ms": worker_active_ms,
                },
            )
            updated = self.connection.execute(
                "SELECT * FROM gateway_job_attempts WHERE job_id=? AND lease_generation=?",
                (job_id, row["lease_generation"]),
            ).fetchone()
            assert updated is not None
            return self._decode_attempt(updated)

    def checkpoint(
        self, *, job_id: str, lease_owner: str, lease_token: str,
        checkpoint: Mapping[str, Any], expected_sequence: int | None = None,
    ) -> GatewayJob:
        checkpoint_json, checkpoint_sha = _canonical_mapping(
            checkpoint, field="checkpoint", max_bytes=MAX_CHECKPOINT_BYTES
        )
        now = self._now()
        with self._transaction():
            row = self._active_row(job_id, lease_owner, lease_token, now)
            if expected_sequence is not None and expected_sequence != row["checkpoint_sequence"]:
                raise JobCheckpointConflictError("checkpoint sequence changed")
            if row["checkpoint_sha256"] == checkpoint_sha and row["checkpoint_json"] == checkpoint_json:
                return self._decode_job(row)
            sequence = row["checkpoint_sequence"] + 1
            self.connection.execute(
                "UPDATE gateway_jobs SET checkpoint_json=?,checkpoint_sha256=?,checkpoint_sequence=?,updated_at_ms=? WHERE job_id=?",
                (checkpoint_json, checkpoint_sha, sequence, now, job_id),
            )
            self._event(job_id, "CHECKPOINTED", now, row["lease_generation"], actor=lease_owner, details={"checkpoint_sequence": sequence, "checkpoint_sha256": checkpoint_sha})
            updated = self.connection.execute("SELECT * FROM gateway_jobs WHERE job_id=?", (job_id,)).fetchone()
            assert updated is not None
            return self._decode_job(updated)

    def release(
        self,
        *,
        job_id: str,
        lease_owner: str,
        lease_token: str,
        reason: str,
    ) -> GatewayJob:
        """Release a safely stopped and reaped child for immediate recovery."""

        reason = _identifier(reason, field="release_reason")
        now = self._now()
        with self._transaction():
            row = self._active_row(job_id, lease_owner, lease_token, now)
            self._finalize_attempt(
                job_id=job_id,
                lease_generation=row["lease_generation"],
                now=now,
                outcome="RELEASED",
            )
            self.connection.execute(
                """UPDATE gateway_jobs SET status='READY',lease_owner=NULL,
                   lease_token=NULL,lease_expires_at_ms=NULL,updated_at_ms=?
                   WHERE job_id=?""",
                (now, job_id),
            )
            self._event(
                job_id,
                "LEASE_RELEASED",
                now,
                row["lease_generation"],
                actor=lease_owner,
                details={"reason": reason},
            )
            updated = self.connection.execute(
                "SELECT * FROM gateway_jobs WHERE job_id=?", (job_id,)
            ).fetchone()
            assert updated is not None
            return self._decode_job(updated)

    def _finalize_attempt(
        self,
        *,
        job_id: str,
        lease_generation: int,
        now: int,
        outcome: str,
    ) -> None:
        attempt = self.connection.execute(
            "SELECT * FROM gateway_job_attempts WHERE job_id=? AND lease_generation=?",
            (job_id, lease_generation),
        ).fetchone()
        if attempt is None:
            # A schema-v1 lease may survive an in-place migration.  Preserve an
            # explicit fallback row instead of silently losing its operation.
            job = self.connection.execute(
                "SELECT lease_owner,created_at_ms FROM gateway_jobs WHERE job_id=?",
                (job_id,),
            ).fetchone()
            if job is None:
                raise JobNotFoundError(job_id)
            claimed = job["created_at_ms"]
            clock_anomaly = now < claimed
            self.connection.execute(
                """INSERT INTO gateway_job_attempts(
                       job_id,lease_generation,lease_owner,claimed_at_ms,
                       queue_wait_ms,clock_anomaly,ended_at_ms,outcome,
                       duration_source,worker_active_ms
                   ) VALUES(?,?,?,?,0,?, ?,?, 'WALL_CLOCK_FALLBACK',?)""",
                (
                    job_id,
                    lease_generation,
                    job["lease_owner"] or "legacy-worker",
                    claimed,
                    int(clock_anomaly),
                    now,
                    outcome,
                    max(0, now - claimed),
                ),
            )
            return
        if attempt["outcome"] != "ACTIVE":
            if attempt["outcome"] != outcome:
                raise JobTerminalConflictError(
                    "attempt already ended with another outcome"
                )
            return
        duration_source = attempt["duration_source"] or "WALL_CLOCK_FALLBACK"
        worker_active_ms = attempt["worker_active_ms"]
        clock_anomaly = bool(attempt["clock_anomaly"])
        if worker_active_ms is None:
            clock_anomaly = clock_anomaly or now < attempt["claimed_at_ms"]
            worker_active_ms = max(0, now - attempt["claimed_at_ms"])
        self.connection.execute(
            """UPDATE gateway_job_attempts
               SET ended_at_ms=?,outcome=?,duration_source=?,worker_active_ms=?,
                   clock_anomaly=?
               WHERE job_id=? AND lease_generation=? AND outcome='ACTIVE'""",
            (
                now,
                outcome,
                duration_source,
                worker_active_ms,
                int(clock_anomaly),
                job_id,
                lease_generation,
            ),
        )

    def complete(
        self, *, job_id: str, lease_owner: str, lease_token: str,
        result: Mapping[str, Any],
    ) -> GatewayJob:
        result_json, result_sha = _canonical_mapping(result, field="result", max_bytes=MAX_RESULT_BYTES)
        now = self._now()
        with self._transaction():
            existing = self.connection.execute("SELECT * FROM gateway_jobs WHERE job_id=?", (job_id,)).fetchone()
            if existing is None:
                raise JobNotFoundError(job_id)
            if existing["status"] == "SUCCEEDED":
                if (existing["terminal_lease_owner"], existing["terminal_lease_token"], existing["result_sha256"], existing["result_json"]) == (lease_owner, lease_token, result_sha, result_json):
                    return self._decode_job(existing)
                raise JobTerminalConflictError("job already succeeded with another terminal outcome")
            if existing["status"] == "FAILED":
                raise JobTerminalConflictError("job already failed")
            row = self._active_row(job_id, lease_owner, lease_token, now)
            self._finalize_attempt(
                job_id=job_id,
                lease_generation=row["lease_generation"],
                now=now,
                outcome="SUCCEEDED",
            )
            self.connection.execute(
                """UPDATE gateway_jobs SET status='SUCCEEDED',lease_owner=NULL,lease_token=NULL,
                   lease_expires_at_ms=NULL,result_json=?,result_sha256=?,terminal_lease_owner=?,
                   terminal_lease_token=?,terminal_at_ms=?,updated_at_ms=? WHERE job_id=?""",
                (result_json, result_sha, lease_owner, lease_token, now, now, job_id),
            )
            self._event(job_id, "COMPLETED", now, row["lease_generation"], actor=lease_owner, details={"result_sha256": result_sha})
            updated = self.connection.execute("SELECT * FROM gateway_jobs WHERE job_id=?", (job_id,)).fetchone()
            assert updated is not None
            return self._decode_job(updated)

    def fail(
        self, *, job_id: str, lease_owner: str, lease_token: str,
        error_code: str, error_message: str,
    ) -> GatewayJob:
        if not isinstance(error_code, str) or _ERROR_CODE.fullmatch(error_code) is None:
            raise ValueError("error_code must be a bounded uppercase identifier")
        if not isinstance(error_message, str) or not error_message.strip() or len(error_message) > 2_000:
            raise ValueError("error_message must be bounded non-empty text")
        error_message = error_message.strip()
        now = self._now()
        with self._transaction():
            existing = self.connection.execute("SELECT * FROM gateway_jobs WHERE job_id=?", (job_id,)).fetchone()
            if existing is None:
                raise JobNotFoundError(job_id)
            if existing["status"] == "FAILED":
                if (existing["terminal_lease_owner"], existing["terminal_lease_token"], existing["failure_code"], existing["failure_message"]) == (lease_owner, lease_token, error_code, error_message):
                    return self._decode_job(existing)
                raise JobTerminalConflictError("job already failed with another terminal outcome")
            if existing["status"] == "SUCCEEDED":
                raise JobTerminalConflictError("job already succeeded")
            row = self._active_row(job_id, lease_owner, lease_token, now)
            self._finalize_attempt(
                job_id=job_id,
                lease_generation=row["lease_generation"],
                now=now,
                outcome=(
                    "HARD_DEADLINE_EXPIRED"
                    if error_code == "HARD_DEADLINE_EXPIRED"
                    else "FAILED"
                ),
            )
            self.connection.execute(
                """UPDATE gateway_jobs SET status='FAILED',lease_owner=NULL,lease_token=NULL,
                   lease_expires_at_ms=NULL,failure_code=?,failure_message=?,terminal_lease_owner=?,
                   terminal_lease_token=?,terminal_at_ms=?,updated_at_ms=? WHERE job_id=?""",
                (error_code, error_message, lease_owner, lease_token, now, now, job_id),
            )
            self._event(job_id, "FAILED", now, row["lease_generation"], actor=lease_owner, details={"error_code": error_code})
            updated = self.connection.execute("SELECT * FROM gateway_jobs WHERE job_id=?", (job_id,)).fetchone()
            assert updated is not None
            return self._decode_job(updated)

    def get_job(self, job_id: str) -> GatewayJob | None:
        _identifier(job_id, field="job_id")
        with self._lock:
            row = self.connection.execute("SELECT * FROM gateway_jobs WHERE job_id=?", (job_id,)).fetchone()
            return None if row is None else self._decode_job(row)

    def list_attempts(self, job_id: str) -> tuple[GatewayJobAttempt, ...]:
        _identifier(job_id, field="job_id")
        with self._lock:
            rows = self.connection.execute(
                "SELECT * FROM gateway_job_attempts WHERE job_id=? "
                "ORDER BY lease_generation",
                (job_id,),
            ).fetchall()
        return tuple(self._decode_attempt(row) for row in rows)

    def acknowledge_terminal_failure(
        self,
        *,
        job_id: str,
        expected_failure_code: str,
        actor: str,
        reason: str,
    ) -> GatewayJob:
        """Acknowledge, but never erase, one exact terminal failure.

        This operator-only primitive prevents a historical manual-recovery
        incident from keeping readiness red forever.  It is hash/state bound,
        append-only in the audit log, and conflicting replays fail closed.
        """

        _identifier(job_id, field="job_id")
        actor = _identifier(actor, field="actor")
        if _ERROR_CODE.fullmatch(expected_failure_code or "") is None:
            raise ValueError("expected_failure_code must be an uppercase identifier")
        if not isinstance(reason, str) or not reason.strip() or len(reason) > 1_000:
            raise ValueError("reason must be bounded non-empty text")
        reason = reason.strip()
        now = self._now()
        with self._transaction():
            row = self.connection.execute(
                "SELECT * FROM gateway_jobs WHERE job_id=?", (job_id,)
            ).fetchone()
            if row is None:
                raise JobNotFoundError(job_id)
            if row["status"] != "FAILED" or row["failure_code"] != expected_failure_code:
                raise JobFailureAcknowledgementConflictError(
                    "terminal failure identity differs from the acknowledgement"
                )
            if row["failure_acknowledged_at_ms"] is not None:
                if (
                    row["failure_acknowledged_by"] == actor
                    and row["failure_acknowledgement_reason"] == reason
                ):
                    return self._decode_job(row)
                raise JobFailureAcknowledgementConflictError(
                    "terminal failure was acknowledged with different input"
                )
            self.connection.execute(
                """UPDATE gateway_jobs
                   SET failure_acknowledged_at_ms=?,failure_acknowledged_by=?,
                       failure_acknowledgement_reason=?,updated_at_ms=?
                   WHERE job_id=? AND status='FAILED'
                     AND failure_code=? AND failure_acknowledged_at_ms IS NULL""",
                (now, actor, reason, now, job_id, expected_failure_code),
            )
            self._event(
                job_id,
                "TERMINAL_FAILURE_ACKNOWLEDGED",
                now,
                row["lease_generation"],
                actor=actor,
                details={
                    "failure_code": expected_failure_code,
                    "reason": reason,
                },
            )
            updated = self.connection.execute(
                "SELECT * FROM gateway_jobs WHERE job_id=?", (job_id,)
            ).fetchone()
            assert updated is not None
            return self._decode_job(updated)

    def list_events(self, job_id: str, *, after_event_id: int = 0, limit: int = 1_000) -> tuple[GatewayJobAuditEvent, ...]:
        _identifier(job_id, field="job_id")
        if not isinstance(after_event_id, int) or after_event_id < 0:
            raise ValueError("after_event_id must be non-negative")
        if not isinstance(limit, int) or not 1 <= limit <= 1_000:
            raise ValueError("limit is outside the allowed range")
        import json

        with self._lock:
            rows = self.connection.execute(
                "SELECT * FROM gateway_job_events WHERE job_id=? AND event_id>? ORDER BY event_id LIMIT ?",
                (job_id, after_event_id, limit),
            ).fetchall()
        return tuple(
            GatewayJobAuditEvent(
                event_id=row["event_id"], job_id=row["job_id"], event_type=row["event_type"],
                occurred_at_ms=row["occurred_at_ms"], lease_generation=row["lease_generation"],
                actor=row["actor"], details=json.loads(row["details_json"]),
            )
            for row in rows
        )


def enqueue_gateway_job_in_transaction(
    connection: sqlite3.Connection,
    *,
    queue_name: str,
    job_kind: str,
    idempotency_key: str,
    payload: Mapping[str, Any],
    priority: int = 0,
    now_ms: int | None = None,
) -> tuple[GatewayJob, bool]:
    """Enqueue on a caller-owned SQLite transaction.

    This seam exists so an operator grant and its durable action outbox can be
    committed atomically in the *same database transaction*.  It deliberately
    refuses to open or commit a transaction itself.
    """

    if not isinstance(connection, sqlite3.Connection):
        raise TypeError("connection must be sqlite3.Connection")
    if not connection.in_transaction:
        raise JobQueuePersistenceError(
            "transactional enqueue requires an active caller transaction"
        )
    queue_name = _identifier(queue_name, field="queue_name")
    job_kind = _identifier(job_kind, field="job_kind")
    idempotency_key = _identifier(idempotency_key, field="idempotency_key")
    if not isinstance(priority, int) or not -1_000 <= priority <= 1_000:
        raise ValueError("priority must be an integer from -1000 to 1000")
    if now_ms is None:
        now_ms = time.time_ns() // 1_000_000
    if not isinstance(now_ms, int) or now_ms < 0:
        raise ValueError("now_ms must be a non-negative integer")
    payload_json, payload_sha = _canonical_mapping(
        payload,
        field="job payload",
        max_bytes=MAX_PAYLOAD_BYTES,
    )
    job_id = SqliteGatewayJobQueue._job_id(queue_name, idempotency_key)
    row = connection.execute(
        "SELECT * FROM gateway_jobs WHERE queue_name=? AND idempotency_key=?",
        (queue_name, idempotency_key),
    ).fetchone()
    if row is not None:
        job = SqliteGatewayJobQueue._decode_job(row)
        if (job.payload_sha256, job.job_kind, job.priority) != (
            payload_sha,
            job_kind,
            priority,
        ):
            raise JobEnqueueConflictError(
                "idempotency key is bound to different immutable input"
            )
        return job, False
    connection.execute(
        """INSERT INTO gateway_jobs(
            job_id,queue_name,job_kind,idempotency_key,payload_json,payload_sha256,
            status,priority,created_at_ms,updated_at_ms
        ) VALUES(?,?,?,?,?,?,'READY',?,?,?)""",
        (
            job_id,
            queue_name,
            job_kind,
            idempotency_key,
            payload_json,
            payload_sha,
            priority,
            now_ms,
            now_ms,
        ),
    )
    details_json, _ = _canonical_mapping(
        {"payload_sha256": payload_sha},
        field="audit details",
        max_bytes=32_768,
    )
    connection.execute(
        """INSERT INTO gateway_job_events(
            job_id,event_type,occurred_at_ms,lease_generation,actor,details_json
        ) VALUES(?,'ENQUEUED',?,0,NULL,?)""",
        (job_id, now_ms, details_json),
    )
    row = connection.execute(
        "SELECT * FROM gateway_jobs WHERE job_id=?",
        (job_id,),
    ).fetchone()
    if row is None:  # pragma: no cover - SQLite statement invariant
        raise JobQueuePersistenceError("transactional enqueue lost its inserted row")
    return SqliteGatewayJobQueue._decode_job(row), True
