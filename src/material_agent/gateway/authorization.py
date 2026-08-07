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
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from threading import RLock
from typing import Iterator, Protocol

from material_agent.gateway.errors import ActionAuthorizationError
from material_agent.gateway.models import (
    ApprovalInteractionV1,
    ApproveActionV1,
    GatewayRunRecordV1,
    InteractionRequiredStateV1,
    InteractionV1,
    RunActionV1,
    canonical_json_bytes,
)
from material_agent.gateway.protocols import GatewayRepository


ACTION_GRANT_SCHEMA_VERSION = 2
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_CONFIRMATION_REFERENCE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{2,255}$")


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
    """An operator request cannot approve the current Gateway interaction."""


@dataclass(frozen=True, slots=True)
class ActionGrantReceipt:
    grant_id: str
    run_id: str
    interaction_id: str
    interaction_sha256: str
    request_sha256: str
    action_sha256: str
    confirmation_reference: str

    def as_json_value(self) -> dict[str, str]:
        return {
            "action_sha256": self.action_sha256,
            "confirmation_reference": self.confirmation_reference,
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

    def _validate_path(self, *, create_parent: bool) -> None:
        parent = self.database_path.parent
        if parent.is_symlink():
            raise ActionGrantStoreError(
                "approval database directory cannot be a symlink"
            )
        if create_parent:
            parent.mkdir(parents=True, exist_ok=True)
        if not parent.is_dir():
            raise ActionGrantStoreError(
                "approval database directory is unavailable"
            )
        if self.database_path.is_symlink():
            raise ActionGrantStoreError(
                "approval database path cannot be a symlink"
            )
        if self.database_path.exists() and not self.database_path.is_file():
            raise ActionGrantStoreError(
                "approval database path is not a regular file"
            )

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
                raise ActionGrantStoreError(
                    "approval database path became a symlink"
                )
            os.chmod(self.database_path, 0o600)
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA foreign_keys = ON")
            connection.execute("PRAGMA busy_timeout = 30000")
            yield connection
        except sqlite3.Error:
            raise ActionGrantStoreError(
                "approval database operation failed"
            ) from None
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
                if (
                    row is None
                    or row["value"] != str(ACTION_GRANT_SCHEMA_VERSION)
                ):
                    raise ActionGrantStoreError(
                        "unsupported approval database schema version"
                    )
                if not required_tables.issubset(existing_tables):
                    raise ActionGrantStoreError(
                        "approval database schema is incomplete"
                    )
                return
            if existing_tables:
                raise ActionGrantStoreError(
                    "approval database has unversioned tables"
                )
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
                    confirmation_reference TEXT NOT NULL,
                    consumed INTEGER NOT NULL DEFAULT 0 CHECK(consumed IN (0, 1)),
                    UNIQUE(
                        run_id,
                        interaction_id,
                        interaction_sha256,
                        request_sha256,
                        action_sha256,
                        action_json
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
    def _canonical_action(action: RunActionV1) -> tuple[str, str]:
        payload = canonical_json_bytes(action)
        return payload.decode("utf-8"), hashlib.sha256(payload).hexdigest()

    @staticmethod
    def _interaction_sha256(interaction: InteractionV1) -> str:
        return hashlib.sha256(canonical_json_bytes(interaction)).hexdigest()

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
        if (
            not isinstance(confirmation_reference, str)
            or _CONFIRMATION_REFERENCE_RE.fullmatch(confirmation_reference) is None
        ):
            raise OperatorApprovalError(
                "confirmation reference must be a bounded opaque identifier"
            )
        action_json, action_sha256 = self._canonical_action(action)
        interaction_sha256 = self._interaction_sha256(interaction)
        grant_id = "grant-" + hashlib.sha256(
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
        receipt = ActionGrantReceipt(
            grant_id=grant_id,
            run_id=run_id,
            interaction_id=interaction.interaction_id,
            interaction_sha256=interaction_sha256,
            request_sha256=request_sha256,
            action_sha256=action_sha256,
            confirmation_reference=confirmation_reference,
        )
        with self._transaction() as connection:
            existing = connection.execute(
                """
                SELECT grant_id, confirmation_reference, consumed
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
            if existing is not None:
                if (
                    existing["grant_id"] == grant_id
                    and existing["confirmation_reference"] == confirmation_reference
                    and existing["consumed"] == 0
                ):
                    return receipt
                raise OperatorApprovalError(
                    "the exact pending action already has a grant record"
                )
            connection.execute(
                """
                INSERT INTO one_time_action_grants(
                    grant_id, run_id, interaction_id, request_sha256,
                    interaction_sha256, action_sha256, action_json,
                    confirmation_reference
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    grant_id,
                    run_id,
                    interaction.interaction_id,
                    request_sha256,
                    interaction_sha256,
                    action_sha256,
                    action_json,
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
        if (
            not isinstance(confirmation_reference, str)
            or _CONFIRMATION_REFERENCE_RE.fullmatch(confirmation_reference) is None
        ):
            raise OperatorApprovalError(
                "confirmation reference must be a bounded opaque identifier"
            )
        if action.interaction_id != interaction.interaction_id:
            raise OperatorApprovalError("action interaction does not match")
        action_json, action_sha256 = self._canonical_action(action)
        interaction_sha256 = self._interaction_sha256(interaction)
        with self._transaction() as connection:
            existing = connection.execute(
                """
                SELECT grant_id, confirmation_reference, consumed
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
            previous_reference = existing["confirmation_reference"]
            if confirmation_reference == previous_reference:
                raise OperatorApprovalError(
                    "grant recovery requires a fresh confirmation reference"
                )
            recovery_id = "recovery-" + hashlib.sha256(
                canonical_json_bytes(
                    {
                        "confirmation_reference": confirmation_reference,
                        "grant_id": existing["grant_id"],
                        "previous_confirmation_reference": previous_reference,
                    }
                )
            ).hexdigest()[:24]
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
            except Exception:
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
            with self._transaction() as connection:
                row = connection.execute(
                    """
                    SELECT grant_id FROM one_time_action_grants
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
        except Exception:
            raise ActionAuthorizationError(
                "operator authorization could not be verified"
            ) from None


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
        recover_consumed: bool = False,
    ) -> ActionGrantReceipt:
        record = self.repository.get_run(run_id)
        if record is None:
            raise OperatorApprovalError("run was not found in the fixed project")
        return self.grant_record(
            record=record,
            confirmation_reference=confirmation_reference,
            expected_execution_manifest_sha256=(
                expected_execution_manifest_sha256
            ),
            recover_consumed=recover_consumed,
        )

    def grant_record(
        self,
        *,
        record: GatewayRunRecordV1,
        confirmation_reference: str,
        expected_execution_manifest_sha256: str,
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
                "operator CLI can approve only requirement_freeze"
            )
        if interaction.input_sha256 != expected_execution_manifest_sha256:
            raise OperatorApprovalError(
                "current interaction differs from the verified execution manifest"
            )
        action = ApproveActionV1(
            interaction_id=interaction.interaction_id,
            confirmed_by_user=True,
        )
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
