from __future__ import annotations

import hashlib
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from material_agent.gateway.authorization import (
    ACTION_GRANT_SCHEMA_VERSION,
    ActionGrantStoreError,
    OperatorApprovalError,
    SqliteOneTimeActionGrantStore,
)
from material_agent.gateway.errors import ActionAuthorizationError
from material_agent.gateway.models import (
    ApprovalInteractionV1,
    ApproveActionV1,
    RejectActionV1,
    canonical_json_bytes,
)


def _binding():
    interaction = ApprovalInteractionV1(
        interaction_id="interaction-freeze",
        approval_kind="requirement_freeze",
        prompt="Freeze the current requirement?",
        input_sha256="a" * 64,
    )
    action = ApproveActionV1(
        interaction_id=interaction.interaction_id,
        confirmed_by_user=True,
    )
    return interaction, action


def test_sqlite_grant_is_exact_atomic_and_one_time(tmp_path: Path) -> None:
    database = tmp_path / "operator-approval-grants.sqlite3"
    issuer = SqliteOneTimeActionGrantStore(database)
    interaction, action = _binding()
    issuer.issue_grant(
        run_id="inspiration-run",
        interaction=interaction,
        request_sha256="a" * 64,
        action=action,
        confirmation_reference="ticket:approval-001",
    )

    def consume() -> str:
        authorizer = SqliteOneTimeActionGrantStore(database)
        try:
            authorizer.authorize_and_consume(
                run_id="inspiration-run",
                interaction=interaction,
                request_sha256="a" * 64,
                action=action,
            )
        except ActionAuthorizationError:
            return "denied"
        return "consumed"

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = tuple(pool.map(lambda _index: consume(), range(2)))

    assert sorted(outcomes) == ["consumed", "denied"]
    with pytest.raises(ActionAuthorizationError):
        issuer.authorize_and_consume(
            run_id="inspiration-run",
            interaction=interaction,
            request_sha256="a" * 64,
            action=action,
        )

    recovered = issuer.recover_consumed_grant(
        run_id="inspiration-run",
        interaction=interaction,
        request_sha256="a" * 64,
        action=action,
        confirmation_reference="ticket:crash-recovery-002",
    )
    assert recovered.confirmation_reference == "ticket:crash-recovery-002"
    issuer.authorize_and_consume(
        run_id="inspiration-run",
        interaction=interaction,
        request_sha256="a" * 64,
        action=action,
    )
    with pytest.raises(ActionAuthorizationError):
        issuer.authorize_and_consume(
            run_id="inspiration-run",
            interaction=interaction,
            request_sha256="a" * 64,
            action=action,
        )


def test_grant_database_path_cannot_be_a_symlink(tmp_path: Path) -> None:
    target = tmp_path / "target.sqlite3"
    target.write_bytes(b"")
    link = tmp_path / "approval.sqlite3"
    link.symlink_to(target)

    with pytest.raises(ActionGrantStoreError, match="symlink"):
        SqliteOneTimeActionGrantStore(link)


def test_grant_binds_the_complete_interaction_manifest(tmp_path: Path) -> None:
    store = SqliteOneTimeActionGrantStore(tmp_path / "approval.sqlite3")
    interaction, action = _binding()
    store.issue_grant(
        run_id="inspiration-manifest-bound",
        interaction=interaction,
        request_sha256="a" * 64,
        action=action,
        confirmation_reference="ticket:manifest-bound-001",
    )
    changed = interaction.model_copy(
        update={"input_sha256": "b" * 64}
    )

    with pytest.raises(ActionAuthorizationError):
        store.authorize_and_consume(
            run_id="inspiration-manifest-bound",
            interaction=changed,
            request_sha256="a" * 64,
            action=action,
        )

    store.authorize_and_consume(
        run_id="inspiration-manifest-bound",
        interaction=interaction,
        request_sha256="a" * 64,
        action=action,
    )


def test_reject_grant_receipt_and_database_bind_exact_action_and_manifest(
    tmp_path: Path,
) -> None:
    database = tmp_path / "operator-approval-grants.sqlite3"
    store = SqliteOneTimeActionGrantStore(database)
    interaction, approve = _binding()
    reject = RejectActionV1(
        interaction_id=interaction.interaction_id,
        confirmed_by_user=True,
        reason="scientific scope not accepted",
    )
    receipt = store.issue_grant(
        run_id="inspiration-reject",
        interaction=interaction,
        request_sha256="b" * 64,
        action=reject,
        confirmation_reference="ticket:reject-001",
    )

    assert receipt.decision == "reject"
    assert receipt.execution_manifest_sha256 == interaction.input_sha256
    assert receipt.action_json == canonical_json_bytes(reject).decode("utf-8")
    assert receipt.action_sha256 == hashlib.sha256(
        canonical_json_bytes(reject)
    ).hexdigest()
    connection = sqlite3.connect(database)
    row = connection.execute(
        "SELECT action_kind, execution_manifest_sha256, action_json, consumed "
        "FROM one_time_action_grants"
    ).fetchone()
    connection.close()
    assert row == (
        "reject",
        interaction.input_sha256,
        canonical_json_bytes(reject).decode("utf-8"),
        0,
    )

    with pytest.raises(ActionAuthorizationError, match="exact action"):
        store.authorize_and_consume(
            run_id="inspiration-reject",
            interaction=interaction,
            request_sha256="b" * 64,
            action=approve,
        )
    store.authorize_and_consume(
        run_id="inspiration-reject",
        interaction=interaction,
        request_sha256="b" * 64,
        action=reject,
    )


def test_one_interaction_cannot_receive_conflicting_decision_grants(
    tmp_path: Path,
) -> None:
    store = SqliteOneTimeActionGrantStore(tmp_path / "approval.sqlite3")
    interaction, approve = _binding()
    reject = RejectActionV1(
        interaction_id=interaction.interaction_id,
        confirmed_by_user=True,
    )
    store.issue_grant(
        run_id="inspiration-one-decision",
        interaction=interaction,
        request_sha256="f" * 64,
        action=reject,
        confirmation_reference="ticket:one-decision-reject",
    )

    with pytest.raises(OperatorApprovalError, match="different decision"):
        store.issue_grant(
            run_id="inspiration-one-decision",
            interaction=interaction,
            request_sha256="f" * 64,
            action=approve,
            confirmation_reference="ticket:conflicting-approve",
        )
    with pytest.raises(ActionAuthorizationError, match="exact action"):
        store.authorize_and_consume(
            run_id="inspiration-one-decision",
            interaction=interaction,
            request_sha256="f" * 64,
            action=approve,
        )
    store.authorize_and_consume(
        run_id="inspiration-one-decision",
        interaction=interaction,
        request_sha256="f" * 64,
        action=reject,
    )


def test_concurrent_conflicting_decisions_persist_exactly_one_grant(
    tmp_path: Path,
) -> None:
    database = tmp_path / "approval.sqlite3"
    SqliteOneTimeActionGrantStore(database)
    interaction, approve = _binding()
    reject = RejectActionV1(
        interaction_id=interaction.interaction_id,
        confirmed_by_user=True,
    )

    def issue(candidate) -> str:
        store = SqliteOneTimeActionGrantStore(database)
        try:
            store.issue_grant(
                run_id="inspiration-concurrent-decision",
                interaction=interaction,
                request_sha256="1" * 64,
                action=candidate,
                confirmation_reference=f"ticket:concurrent-{candidate.kind}",
            )
        except OperatorApprovalError:
            return "denied"
        return candidate.kind

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = tuple(pool.map(issue, (approve, reject)))
    assert outcomes.count("denied") == 1
    assert set(outcomes) in ({"approve", "denied"}, {"reject", "denied"})

    connection = sqlite3.connect(database)
    rows = connection.execute(
        "SELECT action_kind, consumed FROM one_time_action_grants"
    ).fetchall()
    connection.close()
    assert len(rows) == 1
    assert rows[0][0] in {"approve", "reject"}
    assert rows[0][1] == 0


def test_explicit_grant_audit_tampering_fails_closed(tmp_path: Path) -> None:
    database = tmp_path / "operator-approval-grants.sqlite3"
    store = SqliteOneTimeActionGrantStore(database)
    interaction, action = _binding()
    store.issue_grant(
        run_id="inspiration-audit-tamper",
        interaction=interaction,
        request_sha256="c" * 64,
        action=action,
        confirmation_reference="ticket:audit-tamper-001",
    )
    connection = sqlite3.connect(database)
    connection.execute(
        "UPDATE one_time_action_grants "
        "SET execution_manifest_sha256=NULL"
    )
    connection.commit()
    connection.close()

    with pytest.raises(ActionAuthorizationError, match="audit binding"):
        store.authorize_and_consume(
            run_id="inspiration-audit-tamper",
            interaction=interaction,
            request_sha256="c" * 64,
            action=action,
        )


def test_v2_grant_schema_migrates_and_populates_audit_on_exact_consumption(
    tmp_path: Path,
) -> None:
    database = tmp_path / "operator-approval-grants.sqlite3"
    interaction, action = _binding()
    action_json = canonical_json_bytes(action).decode("utf-8")
    action_sha256 = hashlib.sha256(action_json.encode("utf-8")).hexdigest()
    interaction_sha256 = hashlib.sha256(
        canonical_json_bytes(interaction)
    ).hexdigest()
    connection = sqlite3.connect(database)
    connection.executescript(
        """
        CREATE TABLE approval_schema_metadata (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );
        CREATE TABLE one_time_action_grants (
            grant_id TEXT PRIMARY KEY,
            run_id TEXT NOT NULL,
            interaction_id TEXT NOT NULL,
            interaction_sha256 TEXT NOT NULL,
            request_sha256 TEXT NOT NULL,
            action_sha256 TEXT NOT NULL,
            action_json TEXT NOT NULL,
            confirmation_reference TEXT NOT NULL,
            consumed INTEGER NOT NULL DEFAULT 0 CHECK(consumed IN (0, 1)),
            UNIQUE(run_id, interaction_id, interaction_sha256, request_sha256,
                   action_sha256, action_json)
        );
        CREATE TABLE one_time_action_grant_recoveries (
            recovery_id TEXT PRIMARY KEY,
            grant_id TEXT NOT NULL REFERENCES one_time_action_grants(grant_id),
            previous_confirmation_reference TEXT NOT NULL,
            recovery_confirmation_reference TEXT NOT NULL,
            UNIQUE(grant_id, recovery_confirmation_reference)
        );
        INSERT INTO approval_schema_metadata(key, value)
        VALUES ('schema_version', '2');
        """
    )
    connection.execute(
        """
        INSERT INTO one_time_action_grants(
            grant_id, run_id, interaction_id, interaction_sha256,
            request_sha256, action_sha256, action_json,
            confirmation_reference, consumed
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0)
        """,
        (
            "grant-legacy-exact",
            "inspiration-legacy",
            interaction.interaction_id,
            interaction_sha256,
            "e" * 64,
            action_sha256,
            action_json,
            "ticket:legacy-approval-001",
        ),
    )
    connection.commit()
    connection.close()

    store = SqliteOneTimeActionGrantStore(database)
    store.authorize_and_consume(
        run_id="inspiration-legacy",
        interaction=interaction,
        request_sha256="e" * 64,
        action=action,
    )
    connection = sqlite3.connect(database)
    metadata = connection.execute(
        "SELECT value FROM approval_schema_metadata WHERE key='schema_version'"
    ).fetchone()
    row = connection.execute(
        "SELECT action_kind, execution_manifest_sha256, consumed "
        "FROM one_time_action_grants"
    ).fetchone()
    connection.close()
    assert metadata == (str(ACTION_GRANT_SCHEMA_VERSION),)
    assert row == ("approve", interaction.input_sha256, 1)


def test_grant_store_rejects_legacy_schema_without_mutating_it(
    tmp_path: Path,
) -> None:
    database = tmp_path / "legacy-approval.sqlite3"
    connection = sqlite3.connect(database)
    connection.execute(
        "CREATE TABLE approval_schema_metadata "
        "(key TEXT PRIMARY KEY, value TEXT NOT NULL)"
    )
    connection.execute(
        "INSERT INTO approval_schema_metadata(key, value) "
        "VALUES ('schema_version', '0')"
    )
    connection.commit()
    connection.close()

    with pytest.raises(ActionGrantStoreError, match="unsupported"):
        SqliteOneTimeActionGrantStore(database)

    connection = sqlite3.connect(database)
    tables = {
        row[0]
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        ).fetchall()
    }
    connection.close()
    assert tables == {"approval_schema_metadata"}
