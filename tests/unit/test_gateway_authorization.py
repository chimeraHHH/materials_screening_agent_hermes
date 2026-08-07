from __future__ import annotations

import sqlite3
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from material_agent.gateway.authorization import (
    ActionGrantStoreError,
    SqliteOneTimeActionGrantStore,
)
from material_agent.gateway.errors import ActionAuthorizationError
from material_agent.gateway.models import ApprovalInteractionV1, ApproveActionV1


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
