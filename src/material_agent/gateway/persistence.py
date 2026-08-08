"""Durable SQLite repository for Materials Gateway companion state.

The repository stores only strict Gateway DTO JSON.  Scientific artifacts stay
in the project-scoped ``LocalArtifactStore``; this database owns idempotency,
interaction state, revisions, and the bounded terminal result projection.
"""

from __future__ import annotations

import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from threading import RLock
from typing import Iterator

from material_agent.gateway.errors import ConcurrentUpdateError
from material_agent.gateway.models import (
    GatewayResultRecordV1,
    GatewayRunRecordV1,
    canonical_json_bytes,
    gateway_result_sha256,
    terminal_reference,
)


# Version 1 terminal rows did not bind their canonical structured result hash.
# Such rows cannot be upgraded safely after the fact because doing so would
# bless any pre-existing result_json tampering.
GATEWAY_DATABASE_SCHEMA_VERSION = 2


class GatewayPersistenceError(RuntimeError):
    """Persisted Gateway state is unreadable or violates its strict contract."""


class SqliteGatewayRepository:
    """Process-restart-safe implementation of ``GatewayRepository``.

    Writes use ``BEGIN IMMEDIATE`` plus optimistic record revisions.  This does
    not make runner execution transactional with SQLite, but deterministic,
    immutable runner artifacts can be replayed after a crash before commit.
    """

    def __init__(self, database_path: Path | str) -> None:
        self.database_path = Path(database_path)
        parent = self.database_path.parent
        if parent.is_symlink():
            raise GatewayPersistenceError(
                "Gateway database directory cannot be a symlink"
            )
        parent.mkdir(parents=True, exist_ok=True)
        if not parent.is_dir():
            raise GatewayPersistenceError("Gateway database directory is unavailable")
        if self.database_path.is_symlink():
            raise GatewayPersistenceError("Gateway database path cannot be a symlink")
        if self.database_path.exists() and not self.database_path.is_file():
            raise GatewayPersistenceError(
                "Gateway database path is not a regular file"
            )
        self._lock = RLock()
        self.connection = sqlite3.connect(
            self.database_path,
            timeout=30.0,
            check_same_thread=False,
            isolation_level=None,
        )
        if self.database_path.is_symlink():
            self.connection.close()
            raise GatewayPersistenceError("Gateway database path became a symlink")
        os.chmod(self.database_path, 0o600)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA foreign_keys = ON")
        self.connection.execute("PRAGMA journal_mode = WAL")
        self.connection.execute("PRAGMA busy_timeout = 30000")
        try:
            self._setup()
        except Exception:
            self.connection.close()
            raise

    def close(self) -> None:
        with self._lock:
            self.connection.close()

    def _setup(self) -> None:
        required_tables = {
            "gateway_schema_metadata",
            "gateway_runs",
            "gateway_results",
        }
        with self._lock:
            existing_tables = {
                row["name"]
                for row in self.connection.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                ).fetchall()
            }
            if "gateway_schema_metadata" in existing_tables:
                row = self.connection.execute(
                    "SELECT value FROM gateway_schema_metadata "
                    "WHERE key = 'schema_version'"
                ).fetchone()
                try:
                    version = None if row is None else int(row["value"])
                except (TypeError, ValueError):
                    version = None
                if version != GATEWAY_DATABASE_SCHEMA_VERSION:
                    raise GatewayPersistenceError(
                        "unsupported Materials Gateway database schema version"
                    )
                if not required_tables.issubset(existing_tables):
                    raise GatewayPersistenceError(
                        "Materials Gateway database schema is incomplete"
                    )
                return
            if existing_tables:
                raise GatewayPersistenceError(
                    "Materials Gateway database has unversioned tables"
                )

            # sqlite3.executescript() manages its own transaction boundary, so
            # DDL must not run inside our explicit BEGIN/COMMIT context.
            self.connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS gateway_schema_metadata (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS gateway_runs (
                    run_id TEXT PRIMARY KEY,
                    submission_id TEXT NOT NULL UNIQUE,
                    request_sha256 TEXT NOT NULL,
                    revision INTEGER NOT NULL,
                    record_json TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS gateway_results (
                    run_id TEXT PRIMARY KEY
                        REFERENCES gateway_runs(run_id) ON DELETE RESTRICT,
                    result_json TEXT NOT NULL
                );
                """
            )
        with self._transaction():
            row = self.connection.execute(
                "SELECT value FROM gateway_schema_metadata "
                "WHERE key = 'schema_version'"
            ).fetchone()
            if row is None:
                self.connection.execute(
                    "INSERT INTO gateway_schema_metadata(key, value) VALUES (?, ?)",
                    ("schema_version", str(GATEWAY_DATABASE_SCHEMA_VERSION)),
                )
            elif row["value"] != str(GATEWAY_DATABASE_SCHEMA_VERSION):
                raise GatewayPersistenceError(
                    "unsupported Materials Gateway database schema version"
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

    @staticmethod
    def _record_json(record: GatewayRunRecordV1) -> str:
        if not isinstance(record, GatewayRunRecordV1):
            raise TypeError("record must be GatewayRunRecordV1")
        return canonical_json_bytes(record).decode("utf-8")

    @staticmethod
    def _result_json(result: GatewayResultRecordV1) -> str:
        if not isinstance(result, GatewayResultRecordV1):
            raise TypeError("result must be GatewayResultRecordV1")
        return canonical_json_bytes(result).decode("utf-8")

    @staticmethod
    def _decode_record(row: sqlite3.Row) -> GatewayRunRecordV1:
        try:
            record = GatewayRunRecordV1.model_validate_json(row["record_json"])
        except (TypeError, ValueError) as exc:
            raise GatewayPersistenceError(
                "persisted Materials Gateway run is invalid"
            ) from exc
        if (
            record.run_id != row["run_id"]
            or record.request.submission_id != row["submission_id"]
            or record.request_sha256 != row["request_sha256"]
            or record.revision != row["revision"]
        ):
            raise GatewayPersistenceError(
                "persisted Materials Gateway run columns disagree"
            )
        return record

    def create_or_get(
        self,
        record: GatewayRunRecordV1,
    ) -> tuple[GatewayRunRecordV1, bool]:
        record_json = self._record_json(record)
        with self._transaction():
            rows = self.connection.execute(
                "SELECT * FROM gateway_runs "
                "WHERE run_id = ? OR submission_id = ?",
                (record.run_id, record.request.submission_id),
            ).fetchall()
            if rows:
                if len(rows) != 1:
                    raise ConcurrentUpdateError(
                        "run and submission identity resolve to different records"
                    )
                return self._decode_record(rows[0]), False
            self.connection.execute(
                """
                INSERT INTO gateway_runs(
                    run_id, submission_id, request_sha256, revision, record_json
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (
                    record.run_id,
                    record.request.submission_id,
                    record.request_sha256,
                    record.revision,
                    record_json,
                ),
            )
            return record, True

    def get_run(self, run_id: str) -> GatewayRunRecordV1 | None:
        with self._lock:
            row = self.connection.execute(
                "SELECT * FROM gateway_runs WHERE run_id = ?",
                (run_id,),
            ).fetchone()
            return None if row is None else self._decode_record(row)

    def replace_run(
        self,
        record: GatewayRunRecordV1,
        *,
        expected_revision: int,
        result: GatewayResultRecordV1 | None,
    ) -> GatewayRunRecordV1:
        record_json = self._record_json(record)
        result_json = None if result is None else self._result_json(result)
        with self._transaction():
            row = self.connection.execute(
                "SELECT * FROM gateway_runs WHERE run_id = ?",
                (record.run_id,),
            ).fetchone()
            if row is None:
                raise ConcurrentUpdateError("run disappeared before update")
            current = self._decode_record(row)
            if current.revision != expected_revision:
                raise ConcurrentUpdateError("run revision changed before update")
            if record.revision != expected_revision + 1:
                raise ConcurrentUpdateError("replacement revision must advance once")
            if (
                current.request != record.request
                or current.request_sha256 != record.request_sha256
            ):
                raise ConcurrentUpdateError("immutable request identity changed")
            if result is not None and result.run_id != record.run_id:
                raise ConcurrentUpdateError("result belongs to a different run")
            reference = terminal_reference(record.state)
            if (result is None) != (reference is None):
                raise ConcurrentUpdateError(
                    "terminal run and result must be persisted together"
                )
            if result is not None and reference != (
                result.report_uri,
                result.authoritative_sha256,
                gateway_result_sha256(result),
            ):
                raise ConcurrentUpdateError(
                    "terminal state does not bind the canonical result"
                )

            cursor = self.connection.execute(
                """
                UPDATE gateway_runs
                SET revision = ?, record_json = ?
                WHERE run_id = ? AND revision = ?
                """,
                (
                    record.revision,
                    record_json,
                    record.run_id,
                    expected_revision,
                ),
            )
            if cursor.rowcount != 1:
                raise ConcurrentUpdateError("run revision changed before update")

            existing_result = self.connection.execute(
                "SELECT result_json FROM gateway_results WHERE run_id = ?",
                (record.run_id,),
            ).fetchone()
            if result_json is not None:
                if (
                    existing_result is not None
                    and existing_result["result_json"] != result_json
                ):
                    raise ConcurrentUpdateError(
                        "immutable terminal result already differs"
                    )
                self.connection.execute(
                    """
                    INSERT INTO gateway_results(run_id, result_json)
                    VALUES (?, ?)
                    ON CONFLICT(run_id) DO NOTHING
                    """,
                    (record.run_id, result_json),
                )
            elif existing_result is not None:
                raise ConcurrentUpdateError(
                    "terminal result cannot be detached from its run"
                )
            return record

    def get_result(self, run_id: str) -> GatewayResultRecordV1 | None:
        with self._lock:
            row = self.connection.execute(
                """
                SELECT
                    gateway_results.result_json,
                    gateway_runs.run_id,
                    gateway_runs.submission_id,
                    gateway_runs.request_sha256,
                    gateway_runs.revision,
                    gateway_runs.record_json
                FROM gateway_results
                JOIN gateway_runs USING (run_id)
                WHERE gateway_results.run_id = ?
                """,
                (run_id,),
            ).fetchone()
            if row is None:
                return None
            try:
                result = GatewayResultRecordV1.model_validate_json(
                    row["result_json"]
                )
            except (TypeError, ValueError) as exc:
                raise GatewayPersistenceError(
                    "persisted Materials Gateway result is invalid"
                ) from exc
            if result.run_id != run_id:
                raise GatewayPersistenceError(
                    "persisted Materials Gateway result belongs to another run"
                )
            record = self._decode_record(row)
            if terminal_reference(record.state) != (
                result.report_uri,
                result.authoritative_sha256,
                gateway_result_sha256(result),
            ):
                raise GatewayPersistenceError(
                    "persisted terminal result failed canonical SHA-256 binding"
                )
            return result
