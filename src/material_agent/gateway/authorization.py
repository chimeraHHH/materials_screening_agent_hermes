"""Operator-side, one-time authorization for Materials Gateway actions.

MCP callers can request an action, but they cannot mint an authorization.  A
separate operator process records one exact canonical action in an independent
SQLite database.  The Gateway atomically consumes that grant before invoking
the companion, giving production actions at-most-once authorization semantics.
"""

from __future__ import annotations

import hashlib
import os
import re
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from threading import RLock
from typing import Literal, Protocol

from material_agent.gateway.errors import ActionAuthorizationError
from material_agent.gateway.job_queue import (
    GatewayJob,
    SqliteGatewayJobQueue,
    enqueue_gateway_job_in_transaction,
)
from material_agent.gateway.models import (
    ApprovalInteractionV1,
    ApproveActionV1,
    CancelActionV1,
    GatewayRunRecordV1,
    InteractionRequiredStateV1,
    InteractionV1,
    RejectActionV1,
    RunActionV1,
    canonical_json_bytes,
)
from material_agent.gateway.protocols import GatewayRepository

ACTION_GRANT_SCHEMA_VERSION = 3
_LEGACY_ACTION_GRANT_SCHEMA_VERSION = 2
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_CONFIRMATION_REFERENCE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{2,255}$")
RequirementFreezeDecision = Literal["approve", "reject", "cancel"]


def is_valid_confirmation_reference(value: object) -> bool:
    """Return whether an operator reference satisfies the public grant contract."""

    return (
        isinstance(value, str)
        and _CONFIRMATION_REFERENCE_RE.fullmatch(value) is not None
    )


class ActionAuthorizer(Protocol):
    """Consume trusted authority for exactly one pending Gateway action."""

    def authorize_and_consume(
        self,
        *,
        run_id: str,
        interaction: InteractionV1,
        request_sha256: str,
        action: RunActionV1,
    ) -> None: ...


class DenyAllActionAuthorizer:
    """Fail-closed default for services without an operator grant channel."""

    def authorize_and_consume(
        self,
        *,
        run_id: str,
        interaction: InteractionV1,
        request_sha256: str,
        action: RunActionV1,
    ) -> None:
        del run_id, interaction, request_sha256, action
        raise ActionAuthorizationError(
            "a trusted operator grant is required for this action"
        )


class ActionGrantStoreError(RuntimeError):
    """The private approval database is unsafe or unavailable."""


class OperatorApprovalError(RuntimeError):
    """An operator request cannot decide the current Gateway interaction."""


@dataclass(frozen=True, slots=True)
class ActionGrantReceipt:
    grant_id: str
    run_id: str
    interaction_id: str
    interaction_sha256: str
    request_sha256: str
    action_sha256: str
    action_json: str
    decision: str
    execution_manifest_sha256: str | None
    confirmation_reference: str

    def as_json_value(self) -> dict[str, str | None]:
        return {
            "action_sha256": self.action_sha256,
            "action_json": self.action_json,
            "confirmation_reference": self.confirmation_reference,
            "decision": self.decision,
            "execution_manifest_sha256": self.execution_manifest_sha256,
            "grant_id": self.grant_id,
            "interaction_id": self.interaction_id,
            "interaction_sha256": self.interaction_sha256,
            "request_sha256": self.request_sha256,
            "run_id": self.run_id,
        }


class SqliteOneTimeActionGrantStore:
    """Independent SQLite grant issuer and atomic action authorizer.

    One binding may be granted only once.  A consumed grant remains as an audit
    record and cannot be reissued or replayed, even with another confirmation
    reference.
    """

    def __init__(self, database_path: Path | str) -> None:
        self.database_path = Path(database_path)
        self._lock = RLock()
        self._validate_path(create_parent=True)
        self._setup()
        # The action outbox lives in this exact database so consumption and
        # enqueue can share one SQLite transaction.  A separate connection is
        # used only to install/validate the queue schema before any grant use.
        queue = SqliteGatewayJobQueue(self.database_path)
        queue.close()

    def _validate_path(self, *, create_parent: bool) -> None:
        parent = self.database_path.parent
        if parent.is_symlink():
            raise ActionGrantStoreError(
                "approval database directory cannot be a symlink"
            )
        if create_parent:
            parent.mkdir(parents=True, exist_ok=True)
        if not parent.is_dir():
            raise ActionGrantStoreError("approval database directory is unavailable")
        if self.database_path.is_symlink():
            raise ActionGrantStoreError("approval database path cannot be a symlink")
        if self.database_path.exists() and not self.database_path.is_file():
            raise ActionGrantStoreError("approval database path is not a regular file")

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        self._validate_path(create_parent=False)
        try:
            connection = sqlite3.connect(
                self.database_path,
                timeout=30.0,
                isolation_level=None,
            )
        except sqlite3.Error:
            raise ActionGrantStoreError(
                "approval database could not be opened"
            ) from None
        try:
            if self.database_path.is_symlink():
                raise ActionGrantStoreError("approval database path became a symlink")
            os.chmod(self.database_path, 0o600)
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA foreign_keys = ON")
            connection.execute("PRAGMA synchronous = FULL")
            connection.execute("PRAGMA busy_timeout = 30000")
            yield connection
        except sqlite3.Error:
            raise ActionGrantStoreError("approval database operation failed") from None
        finally:
            connection.close()

    @contextmanager
    def _transaction(self) -> Iterator[sqlite3.Connection]:
        with self._lock, self._connection() as connection:
            try:
                connection.execute("BEGIN IMMEDIATE")
                yield connection
                connection.execute("COMMIT")
            except Exception:
                if connection.in_transaction:
                    connection.execute("ROLLBACK")
                raise

    def _setup(self) -> None:
        required_tables = {
            "approval_schema_metadata",
            "one_time_action_grant_recoveries",
            "one_time_action_grants",
        }
        with self._lock, self._connection() as connection:
            existing_tables = {
                row["name"]
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                ).fetchall()
            }
            if "approval_schema_metadata" in existing_tables:
                row = connection.execute(
                    "SELECT value FROM approval_schema_metadata "
                    "WHERE key = 'schema_version'"
                ).fetchone()
                if row is None or row["value"] not in {
                    str(_LEGACY_ACTION_GRANT_SCHEMA_VERSION),
                    str(ACTION_GRANT_SCHEMA_VERSION),
                }:
                    raise ActionGrantStoreError(
                        "unsupported approval database schema version"
                    )
                if not required_tables.issubset(existing_tables):
                    raise ActionGrantStoreError(
                        "approval database schema is incomplete"
                    )
                if row["value"] == str(_LEGACY_ACTION_GRANT_SCHEMA_VERSION):
                    self._migrate_v2_to_v3(connection)
                self._validate_current_schema(connection)
                return
            if existing_tables:
                raise ActionGrantStoreError("approval database has unversioned tables")
            connection.execute("PRAGMA journal_mode = WAL")
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS approval_schema_metadata (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS one_time_action_grants (
                    grant_id TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL,
                    interaction_id TEXT NOT NULL,
                    interaction_sha256 TEXT NOT NULL,
                    request_sha256 TEXT NOT NULL,
                    action_sha256 TEXT NOT NULL,
                    action_json TEXT NOT NULL,
                    action_kind TEXT NOT NULL,
                    execution_manifest_sha256 TEXT,
                    audit_binding_version INTEGER NOT NULL DEFAULT 1
                        CHECK(audit_binding_version IN (0, 1)),
                    confirmation_reference TEXT NOT NULL,
                    consumed INTEGER NOT NULL DEFAULT 0 CHECK(consumed IN (0, 1)),
                    UNIQUE(
                        run_id,
                        interaction_id,
                        interaction_sha256,
                        request_sha256,
                        action_sha256,
                        action_json
                    ),
                    UNIQUE(
                        run_id,
                        interaction_id,
                        interaction_sha256,
                        request_sha256
                    )
                );

                CREATE TABLE IF NOT EXISTS one_time_action_grant_recoveries (
                    recovery_id TEXT PRIMARY KEY,
                    grant_id TEXT NOT NULL
                        REFERENCES one_time_action_grants(grant_id)
                        ON DELETE RESTRICT,
                    previous_confirmation_reference TEXT NOT NULL,
                    recovery_confirmation_reference TEXT NOT NULL,
                    UNIQUE(grant_id, recovery_confirmation_reference)
                );
                """
            )
        with self._transaction() as connection:
            row = connection.execute(
                "SELECT value FROM approval_schema_metadata "
                "WHERE key = 'schema_version'"
            ).fetchone()
            if row is None:
                connection.execute(
                    "INSERT INTO approval_schema_metadata(key, value) VALUES (?, ?)",
                    ("schema_version", str(ACTION_GRANT_SCHEMA_VERSION)),
                )
            elif row["value"] != str(ACTION_GRANT_SCHEMA_VERSION):
                raise ActionGrantStoreError(
                    "unsupported approval database schema version"
                )

    @staticmethod
    def _migrate_v2_to_v3(connection: sqlite3.Connection) -> None:
        """Add readable decision/manifest audit fields without trusting old rows.

        A v2 row already cryptographically binds the canonical action and full
        interaction.  Its explicit audit columns are populated only when that
        same binding is re-read by issuance, recovery, or consumption; a
        mismatched action can therefore never bless a legacy row.
        """

        try:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT value FROM approval_schema_metadata "
                "WHERE key = 'schema_version'"
            ).fetchone()
            if row is None:
                raise ActionGrantStoreError(
                    "approval database schema metadata is missing"
                )
            if row["value"] == str(ACTION_GRANT_SCHEMA_VERSION):
                connection.execute("COMMIT")
                return
            if row["value"] != str(_LEGACY_ACTION_GRANT_SCHEMA_VERSION):
                raise ActionGrantStoreError(
                    "unsupported approval database schema version"
                )
            columns = {
                item["name"]
                for item in connection.execute(
                    "PRAGMA table_info(one_time_action_grants)"
                ).fetchall()
            }
            if "action_kind" not in columns:
                connection.execute(
                    "ALTER TABLE one_time_action_grants "
                    "ADD COLUMN action_kind TEXT"
                )
            if "execution_manifest_sha256" not in columns:
                connection.execute(
                    "ALTER TABLE one_time_action_grants "
                    "ADD COLUMN execution_manifest_sha256 TEXT"
                )
            if "audit_binding_version" not in columns:
                connection.execute(
                    "ALTER TABLE one_time_action_grants "
                    "ADD COLUMN audit_binding_version INTEGER NOT NULL DEFAULT 0 "
                    "CHECK(audit_binding_version IN (0, 1))"
                )
            connection.execute(
                "UPDATE approval_schema_metadata SET value = ? "
                "WHERE key = 'schema_version' AND value = ?",
                (
                    str(ACTION_GRANT_SCHEMA_VERSION),
                    str(_LEGACY_ACTION_GRANT_SCHEMA_VERSION),
                ),
            )
            connection.execute("COMMIT")
        except Exception:
            if connection.in_transaction:
                connection.execute("ROLLBACK")
            raise

    @staticmethod
    def _validate_current_schema(connection: sqlite3.Connection) -> None:
        row = connection.execute(
            "SELECT value FROM approval_schema_metadata "
            "WHERE key = 'schema_version'"
        ).fetchone()
        if row is None or row["value"] != str(ACTION_GRANT_SCHEMA_VERSION):
            raise ActionGrantStoreError(
                "unsupported approval database schema version"
            )
        columns = {
            item["name"]
            for item in connection.execute(
                "PRAGMA table_info(one_time_action_grants)"
            ).fetchall()
        }
        required_columns = {
            "action_json",
            "action_kind",
            "action_sha256",
            "audit_binding_version",
            "confirmation_reference",
            "consumed",
            "execution_manifest_sha256",
            "grant_id",
            "interaction_id",
            "interaction_sha256",
            "request_sha256",
            "run_id",
        }
        if not required_columns.issubset(columns):
            raise ActionGrantStoreError("approval database schema is incomplete")
        conflicting = connection.execute(
            """
            SELECT 1 FROM one_time_action_grants
            GROUP BY run_id, interaction_id, interaction_sha256, request_sha256
            HAVING COUNT(*) > 1
            LIMIT 1
            """
        ).fetchone()
        if conflicting is not None:
            raise ActionGrantStoreError(
                "approval database contains conflicting interaction decisions"
            )

    @staticmethod
    def _canonical_action(action: RunActionV1) -> tuple[str, str]:
        payload = canonical_json_bytes(action)
        return payload.decode("utf-8"), hashlib.sha256(payload).hexdigest()

    @staticmethod
    def _interaction_sha256(interaction: InteractionV1) -> str:
        return hashlib.sha256(canonical_json_bytes(interaction)).hexdigest()

    @staticmethod
    def _execution_manifest_sha256(interaction: InteractionV1) -> str | None:
        if isinstance(interaction, ApprovalInteractionV1):
            return interaction.input_sha256
        return None

    @staticmethod
    def _audit_columns_match_or_are_legacy(
        row: sqlite3.Row,
        *,
        action_kind: str,
        execution_manifest_sha256: str | None,
    ) -> bool:
        audit_binding_version = row["audit_binding_version"]
        if audit_binding_version == 0:
            return (
                row["action_kind"] in {None, action_kind}
                and row["execution_manifest_sha256"]
                in {None, execution_manifest_sha256}
            )
        return (
            audit_binding_version == 1
            and row["action_kind"] == action_kind
            and row["execution_manifest_sha256"]
            == execution_manifest_sha256
        )

    @staticmethod
    def _populate_legacy_audit_columns(
        connection: sqlite3.Connection,
        *,
        grant_id: str,
        action_kind: str,
        execution_manifest_sha256: str | None,
    ) -> None:
        connection.execute(
            """
            UPDATE one_time_action_grants
            SET action_kind = COALESCE(action_kind, ?),
                execution_manifest_sha256 =
                    COALESCE(execution_manifest_sha256, ?),
                audit_binding_version = 1
            WHERE grant_id = ? AND audit_binding_version = 0
            """,
            (action_kind, execution_manifest_sha256, grant_id),
        )

    @staticmethod
    def _validate_binding(
        *,
        run_id: str,
        interaction_id: str,
        request_sha256: str,
    ) -> None:
        if not run_id or len(run_id) > 128:
            raise ValueError("run_id is invalid")
        if not interaction_id or len(interaction_id) > 128:
            raise ValueError("interaction_id is invalid")
        if _SHA256_RE.fullmatch(request_sha256) is None:
            raise ValueError("request_sha256 is invalid")

    @staticmethod
    def _validate_action_for_interaction(
        *, interaction: InteractionV1, action: RunActionV1
    ) -> None:
        if action.interaction_id != interaction.interaction_id:
            raise OperatorApprovalError("action interaction does not match")
        if action.kind not in interaction.allowed_actions:
            raise OperatorApprovalError(
                "action is not advertised by the current interaction"
            )

    def issue_grant(
        self,
        *,
        run_id: str,
        interaction: InteractionV1,
        request_sha256: str,
        action: RunActionV1,
        confirmation_reference: str,
    ) -> ActionGrantReceipt:
        self._validate_binding(
            run_id=run_id,
            interaction_id=interaction.interaction_id,
            request_sha256=request_sha256,
        )
        self._validate_action_for_interaction(
            interaction=interaction,
            action=action,
        )
        if not is_valid_confirmation_reference(confirmation_reference):
            raise OperatorApprovalError(
                "confirmation reference must be a bounded opaque identifier"
            )
        action_json, action_sha256 = self._canonical_action(action)
        interaction_sha256 = self._interaction_sha256(interaction)
        execution_manifest_sha256 = self._execution_manifest_sha256(interaction)
        grant_id = (
            "grant-"
            + hashlib.sha256(
                canonical_json_bytes(
                    {
                        "action_sha256": action_sha256,
                        "confirmation_reference": confirmation_reference,
                        "interaction_id": interaction.interaction_id,
                        "interaction_sha256": interaction_sha256,
                        "request_sha256": request_sha256,
                        "run_id": run_id,
                    }
                )
            ).hexdigest()[:24]
        )
        receipt = ActionGrantReceipt(
            grant_id=grant_id,
            run_id=run_id,
            interaction_id=interaction.interaction_id,
            interaction_sha256=interaction_sha256,
            request_sha256=request_sha256,
            action_sha256=action_sha256,
            action_json=action_json,
            decision=action.kind,
            execution_manifest_sha256=execution_manifest_sha256,
            confirmation_reference=confirmation_reference,
        )
        with self._transaction() as connection:
            existing_rows = connection.execute(
                """
                SELECT grant_id, confirmation_reference, consumed,
                       action_kind, execution_manifest_sha256,
                       audit_binding_version, action_sha256, action_json
                FROM one_time_action_grants
                WHERE run_id = ? AND interaction_id = ?
                  AND interaction_sha256 = ? AND request_sha256 = ?
                """,
                (
                    run_id,
                    interaction.interaction_id,
                    interaction_sha256,
                    request_sha256,
                ),
            ).fetchall()
            if len(existing_rows) > 1:
                raise OperatorApprovalError(
                    "the pending interaction has conflicting grant records"
                )
            existing = existing_rows[0] if existing_rows else None
            if existing is not None:
                if (
                    existing["action_sha256"] != action_sha256
                    or existing["action_json"] != action_json
                ):
                    raise OperatorApprovalError(
                        "the pending interaction already has a different decision"
                    )
                if not self._audit_columns_match_or_are_legacy(
                    existing,
                    action_kind=action.kind,
                    execution_manifest_sha256=execution_manifest_sha256,
                ):
                    raise OperatorApprovalError(
                        "the persisted grant audit binding is inconsistent"
                    )
                if (
                    existing["grant_id"] == grant_id
                    and existing["confirmation_reference"] == confirmation_reference
                    and existing["consumed"] == 0
                ):
                    self._populate_legacy_audit_columns(
                        connection,
                        grant_id=grant_id,
                        action_kind=action.kind,
                        execution_manifest_sha256=execution_manifest_sha256,
                    )
                    return receipt
                raise OperatorApprovalError(
                    "the exact pending action already has a grant record"
                )
            connection.execute(
                """
                INSERT INTO one_time_action_grants(
                    grant_id, run_id, interaction_id, request_sha256,
                    interaction_sha256, action_sha256, action_json,
                    action_kind, execution_manifest_sha256,
                    audit_binding_version,
                    confirmation_reference
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?)
                """,
                (
                    grant_id,
                    run_id,
                    interaction.interaction_id,
                    request_sha256,
                    interaction_sha256,
                    action_sha256,
                    action_json,
                    action.kind,
                    execution_manifest_sha256,
                    confirmation_reference,
                ),
            )
        return receipt

    def recover_consumed_grant(
        self,
        *,
        run_id: str,
        interaction: InteractionV1,
        request_sha256: str,
        action: RunActionV1,
        confirmation_reference: str,
    ) -> ActionGrantReceipt:
        """Re-arm a stranded grant after an operator confirms process death.

        The caller must first verify that the Gateway still advertises the
        same interaction and that the immutable execution manifest still
        hashes to ``interaction.input_sha256``.  This method is intentionally
        not part of the MCP surface.
        """

        self._validate_binding(
            run_id=run_id,
            interaction_id=interaction.interaction_id,
            request_sha256=request_sha256,
        )
        if not is_valid_confirmation_reference(confirmation_reference):
            raise OperatorApprovalError(
                "confirmation reference must be a bounded opaque identifier"
            )
        self._validate_action_for_interaction(
            interaction=interaction,
            action=action,
        )
        action_json, action_sha256 = self._canonical_action(action)
        interaction_sha256 = self._interaction_sha256(interaction)
        execution_manifest_sha256 = self._execution_manifest_sha256(interaction)
        with self._transaction() as connection:
            binding_count = connection.execute(
                """
                SELECT COUNT(*) FROM one_time_action_grants
                WHERE run_id = ? AND interaction_id = ?
                  AND interaction_sha256 = ? AND request_sha256 = ?
                """,
                (
                    run_id,
                    interaction.interaction_id,
                    interaction_sha256,
                    request_sha256,
                ),
            ).fetchone()[0]
            if binding_count != 1:
                raise OperatorApprovalError(
                    "the current interaction does not have one exact operator grant"
                )
            existing = connection.execute(
                """
                SELECT grant_id, confirmation_reference, consumed,
                       action_kind, execution_manifest_sha256,
                       audit_binding_version
                FROM one_time_action_grants
                WHERE run_id = ? AND interaction_id = ?
                  AND interaction_sha256 = ? AND request_sha256 = ?
                  AND action_sha256 = ? AND action_json = ?
                """,
                (
                    run_id,
                    interaction.interaction_id,
                    interaction_sha256,
                    request_sha256,
                    action_sha256,
                    action_json,
                ),
            ).fetchone()
            if existing is None or existing["consumed"] != 1:
                raise OperatorApprovalError(
                    "no consumed grant is stranded on the current interaction"
                )
            if not self._audit_columns_match_or_are_legacy(
                existing,
                action_kind=action.kind,
                execution_manifest_sha256=execution_manifest_sha256,
            ):
                raise OperatorApprovalError(
                    "the persisted grant audit binding is inconsistent"
                )
            self._populate_legacy_audit_columns(
                connection,
                grant_id=existing["grant_id"],
                action_kind=action.kind,
                execution_manifest_sha256=execution_manifest_sha256,
            )
            previous_reference = existing["confirmation_reference"]
            if confirmation_reference == previous_reference:
                raise OperatorApprovalError(
                    "grant recovery requires a fresh confirmation reference"
                )
            recovery_id = (
                "recovery-"
                + hashlib.sha256(
                    canonical_json_bytes(
                        {
                            "confirmation_reference": confirmation_reference,
                            "grant_id": existing["grant_id"],
                            "previous_confirmation_reference": previous_reference,
                        }
                    )
                ).hexdigest()[:24]
            )
            try:
                connection.execute(
                    """
                    INSERT INTO one_time_action_grant_recoveries(
                        recovery_id, grant_id, previous_confirmation_reference,
                        recovery_confirmation_reference
                    ) VALUES (?, ?, ?, ?)
                    """,
                    (
                        recovery_id,
                        existing["grant_id"],
                        previous_reference,
                        confirmation_reference,
                    ),
                )
            except ActionGrantStoreError:
                raise
            except Exception:  # noqa: BLE001 - map backend uniqueness failures
                raise OperatorApprovalError(
                    "the recovery confirmation reference was already used"
                ) from None
            cursor = connection.execute(
                """
                UPDATE one_time_action_grants
                SET confirmation_reference = ?, consumed = 0
                WHERE grant_id = ? AND consumed = 1
                """,
                (confirmation_reference, existing["grant_id"]),
            )
            if cursor.rowcount != 1:
                raise OperatorApprovalError("consumed grant changed during recovery")
        return ActionGrantReceipt(
            grant_id=existing["grant_id"],
            run_id=run_id,
            interaction_id=interaction.interaction_id,
            interaction_sha256=interaction_sha256,
            request_sha256=request_sha256,
            action_sha256=action_sha256,
            action_json=action_json,
            decision=action.kind,
            execution_manifest_sha256=execution_manifest_sha256,
            confirmation_reference=confirmation_reference,
        )

    def authorize_and_consume(
        self,
        *,
        run_id: str,
        interaction: InteractionV1,
        request_sha256: str,
        action: RunActionV1,
    ) -> None:
        try:
            self._validate_binding(
                run_id=run_id,
                interaction_id=interaction.interaction_id,
                request_sha256=request_sha256,
            )
            if action.interaction_id != interaction.interaction_id:
                raise ValueError("action interaction does not match")
            action_json, action_sha256 = self._canonical_action(action)
            interaction_sha256 = self._interaction_sha256(interaction)
            execution_manifest_sha256 = self._execution_manifest_sha256(interaction)
            with self._transaction() as connection:
                binding_count = connection.execute(
                    """
                    SELECT COUNT(*) FROM one_time_action_grants
                    WHERE run_id = ? AND interaction_id = ?
                      AND interaction_sha256 = ? AND request_sha256 = ?
                    """,
                    (
                        run_id,
                        interaction.interaction_id,
                        interaction_sha256,
                        request_sha256,
                    ),
                ).fetchone()[0]
                if binding_count != 1:
                    raise ActionAuthorizationError(
                        "interaction must have exactly one operator grant decision"
                    )
                row = connection.execute(
                    """
                    SELECT grant_id, action_kind, execution_manifest_sha256,
                           audit_binding_version
                    FROM one_time_action_grants
                    WHERE run_id = ? AND interaction_id = ?
                      AND interaction_sha256 = ? AND request_sha256 = ?
                      AND action_sha256 = ?
                      AND action_json = ? AND consumed = 0
                    """,
                    (
                        run_id,
                        interaction.interaction_id,
                        interaction_sha256,
                        request_sha256,
                        action_sha256,
                        action_json,
                    ),
                ).fetchone()
                if row is None:
                    raise ActionAuthorizationError(
                        "no unconsumed operator grant matches the exact action"
                    )
                if not self._audit_columns_match_or_are_legacy(
                    row,
                    action_kind=action.kind,
                    execution_manifest_sha256=execution_manifest_sha256,
                ):
                    raise ActionAuthorizationError(
                        "operator grant audit binding does not match the exact action"
                    )
                self._populate_legacy_audit_columns(
                    connection,
                    grant_id=row["grant_id"],
                    action_kind=action.kind,
                    execution_manifest_sha256=execution_manifest_sha256,
                )
                cursor = connection.execute(
                    """
                    UPDATE one_time_action_grants SET consumed = 1
                    WHERE grant_id = ? AND consumed = 0
                    """,
                    (row["grant_id"],),
                )
                if cursor.rowcount != 1:
                    raise ActionAuthorizationError(
                        "operator grant was already consumed"
                    )
        except ActionAuthorizationError:
            raise
        except Exception:  # noqa: BLE001 - expose only the authorization boundary
            raise ActionAuthorizationError(
                "operator authorization could not be verified"
            ) from None

    def authorize_consume_and_enqueue(
        self,
        *,
        run_id: str,
        run_revision: int,
        interaction: InteractionV1,
        request_sha256: str,
        action: RunActionV1,
        queue_name: str = "gateway-actions",
    ) -> tuple[ActionGrantReceipt, GatewayJob]:
        """Atomically consume one exact grant and create its durable outbox job.

        An exact retry after a committed response loss returns the already
        enqueued job.  A legacy grant consumed without an outbox row is not
        retroactively blessed, because it may already have executed through
        the historical synchronous service.
        """

        try:
            self._validate_binding(
                run_id=run_id,
                interaction_id=interaction.interaction_id,
                request_sha256=request_sha256,
            )
            if not isinstance(run_revision, int) or run_revision < 0:
                raise ValueError("run_revision must be non-negative")
            self._validate_action_for_interaction(
                interaction=interaction,
                action=action,
            )
            action_json, action_sha256 = self._canonical_action(action)
            interaction_sha256 = self._interaction_sha256(interaction)
            execution_manifest_sha256 = self._execution_manifest_sha256(interaction)
            with self._transaction() as connection:
                rows = connection.execute(
                    """SELECT * FROM one_time_action_grants
                       WHERE run_id=? AND interaction_id=?
                         AND interaction_sha256=? AND request_sha256=?""",
                    (
                        run_id,
                        interaction.interaction_id,
                        interaction_sha256,
                        request_sha256,
                    ),
                ).fetchall()
                if len(rows) != 1:
                    raise ActionAuthorizationError(
                        "interaction must have exactly one operator grant decision"
                    )
                row = rows[0]
                if row["action_sha256"] != action_sha256 or row["action_json"] != action_json:
                    raise ActionAuthorizationError(
                        "no operator grant matches the exact queued action"
                    )
                if not self._audit_columns_match_or_are_legacy(
                    row,
                    action_kind=action.kind,
                    execution_manifest_sha256=execution_manifest_sha256,
                ):
                    raise ActionAuthorizationError(
                        "operator grant audit binding does not match the queued action"
                    )
                receipt = ActionGrantReceipt(
                    grant_id=row["grant_id"],
                    run_id=run_id,
                    interaction_id=interaction.interaction_id,
                    interaction_sha256=interaction_sha256,
                    request_sha256=request_sha256,
                    action_sha256=action_sha256,
                    action_json=action_json,
                    decision=action.kind,
                    execution_manifest_sha256=execution_manifest_sha256,
                    confirmation_reference=row["confirmation_reference"],
                )
                job_payload = {
                    "action": action.model_dump(mode="json"),
                    "action_sha256": action_sha256,
                    "execution_manifest_sha256": execution_manifest_sha256,
                    "expected_run_revision": run_revision,
                    "grant_receipt": receipt.as_json_value(),
                    "interaction": interaction.model_dump(mode="json"),
                    "interaction_sha256": interaction_sha256,
                    "request_sha256": request_sha256,
                    "run_id": run_id,
                    "schema_version": "materials-gateway-action-job-v1",
                }
                job, created = enqueue_gateway_job_in_transaction(
                    connection,
                    queue_name=queue_name,
                    job_kind=f"gateway-action-{action.kind}",
                    idempotency_key=receipt.grant_id,
                    payload=job_payload,
                )
                if row["consumed"] == 1:
                    if created:
                        raise ActionAuthorizationError(
                            "consumed legacy grant has no trusted action outbox"
                        )
                    return receipt, job
                self._populate_legacy_audit_columns(
                    connection,
                    grant_id=row["grant_id"],
                    action_kind=action.kind,
                    execution_manifest_sha256=execution_manifest_sha256,
                )
                cursor = connection.execute(
                    "UPDATE one_time_action_grants SET consumed=1 "
                    "WHERE grant_id=? AND consumed=0",
                    (row["grant_id"],),
                )
                if cursor.rowcount != 1:
                    raise ActionAuthorizationError(
                        "operator grant changed before action enqueue"
                    )
                return receipt, job
        except ActionAuthorizationError:
            raise
        except Exception:  # noqa: BLE001 - expose only authorization boundary
            raise ActionAuthorizationError(
                "operator authorization and action enqueue could not be committed"
            ) from None

    def verify_consumed_grant_receipt(self, receipt: ActionGrantReceipt) -> None:
        """Revalidate a job's complete receipt against the consumed grant row."""

        if not isinstance(receipt, ActionGrantReceipt):
            raise TypeError("receipt must be ActionGrantReceipt")
        with self._lock, self._connection() as connection:
            row = connection.execute(
                "SELECT * FROM one_time_action_grants WHERE grant_id=?",
                (receipt.grant_id,),
            ).fetchone()
        if row is None or row["consumed"] != 1:
            raise ActionAuthorizationError(
                "queued action grant is absent or not consumed"
            )
        observed = ActionGrantReceipt(
            grant_id=row["grant_id"],
            run_id=row["run_id"],
            interaction_id=row["interaction_id"],
            interaction_sha256=row["interaction_sha256"],
            request_sha256=row["request_sha256"],
            action_sha256=row["action_sha256"],
            action_json=row["action_json"],
            decision=row["action_kind"],
            execution_manifest_sha256=row["execution_manifest_sha256"],
            confirmation_reference=row["confirmation_reference"],
        )
        if observed != receipt:
            raise ActionAuthorizationError(
                "queued action receipt differs from its consumed grant"
            )


class RequirementFreezeGrantIssuer:
    """Operator-only issuance for the current requirement-freeze approval."""

    def __init__(
        self,
        *,
        repository: GatewayRepository,
        grant_store: SqliteOneTimeActionGrantStore,
    ) -> None:
        self.repository = repository
        self.grant_store = grant_store

    def grant_current(
        self,
        *,
        run_id: str,
        confirmation_reference: str,
        expected_execution_manifest_sha256: str,
        decision: RequirementFreezeDecision = "approve",
        reason: str | None = None,
        recover_consumed: bool = False,
    ) -> ActionGrantReceipt:
        record = self.repository.get_run(run_id)
        if record is None:
            raise OperatorApprovalError("run was not found in the fixed project")
        return self.grant_record(
            record=record,
            confirmation_reference=confirmation_reference,
            expected_execution_manifest_sha256=(expected_execution_manifest_sha256),
            decision=decision,
            reason=reason,
            recover_consumed=recover_consumed,
        )

    def grant_record(
        self,
        *,
        record: GatewayRunRecordV1,
        confirmation_reference: str,
        expected_execution_manifest_sha256: str,
        decision: RequirementFreezeDecision = "approve",
        reason: str | None = None,
        recover_consumed: bool = False,
    ) -> ActionGrantReceipt:
        state = record.state
        if not isinstance(state, InteractionRequiredStateV1):
            raise OperatorApprovalError("run has no current approval interaction")
        interaction = state.interaction
        if not isinstance(interaction, ApprovalInteractionV1):
            raise OperatorApprovalError("current interaction is not an approval")
        if interaction.approval_kind != "requirement_freeze":
            raise OperatorApprovalError(
                "operator CLI can decide only requirement_freeze"
            )
        if interaction.input_sha256 != expected_execution_manifest_sha256:
            raise OperatorApprovalError(
                "current interaction differs from the verified execution manifest"
            )
        if decision not in interaction.allowed_actions:
            raise OperatorApprovalError(
                "decision is not advertised by the current interaction"
            )
        if reason is not None and decision != "reject":
            raise OperatorApprovalError("reason is supported only for reject")
        try:
            if decision == "approve":
                action: RunActionV1 = ApproveActionV1(
                    interaction_id=interaction.interaction_id,
                    confirmed_by_user=True,
                )
            elif decision == "reject":
                action = RejectActionV1(
                    interaction_id=interaction.interaction_id,
                    confirmed_by_user=True,
                    reason=reason,
                )
            elif decision == "cancel":
                action = CancelActionV1(
                    interaction_id=interaction.interaction_id,
                    confirmed_by_user=True,
                )
            else:
                raise OperatorApprovalError("unsupported operator decision")
        except (TypeError, ValueError) as exc:
            raise OperatorApprovalError("operator decision payload is invalid") from exc
        operation = (
            self.grant_store.recover_consumed_grant
            if recover_consumed
            else self.grant_store.issue_grant
        )
        return operation(
            run_id=record.run_id,
            interaction=interaction,
            request_sha256=record.request_sha256,
            action=action,
            confirmation_reference=confirmation_reference,
        )
