from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from material_agent.gateway.errors import ConcurrentUpdateError
from material_agent.gateway.models import (
    GatewayResultRecordV1,
    GatewayRunRecordV1,
    InspirationBundleSummaryV1,
    InspirationConstraintsV1,
    InspirationRunRequestV1,
    RunningStateV1,
    SucceededStateV1,
    canonical_json_bytes,
    gateway_result_sha256,
    inspiration_report_uri,
    inspiration_request_sha256,
    inspiration_run_id,
)
from material_agent.gateway.persistence import (
    GATEWAY_DATABASE_SCHEMA_VERSION,
    GatewayPersistenceError,
    SqliteGatewayRepository,
)


def _record(submission_id: str = "persistent-submission") -> GatewayRunRecordV1:
    request = InspirationRunRequestV1(
        submission_id=submission_id,
        goal="Exercise durable Gateway state",
        constraints=InspirationConstraintsV1(),
    )
    return GatewayRunRecordV1(
        run_id=inspiration_run_id(submission_id),
        request=request,
        request_sha256=inspiration_request_sha256(request),
        state=RunningStateV1(message="accepted"),
    )


def _result(run_id: str) -> GatewayResultRecordV1:
    return GatewayResultRecordV1(
        run_id=run_id,
        report_uri=inspiration_report_uri(run_id),
        authoritative_sha256="a" * 64,
        bundle=InspirationBundleSummaryV1(
            outcome="SCIENTIFIC_NO_MATCH",
            limitations=("The persistence fixture contains no science result.",),
            next_validation_steps=("Run the real companion separately.",),
        ),
        validation_boundaries=("This record tests persistence only.",),
    )


def test_sqlite_repository_initializes_and_survives_restart(tmp_path: Path) -> None:
    database = tmp_path / "state" / "gateway.sqlite3"
    initial = _record()
    first = SqliteGatewayRepository(database)
    persisted, created = first.create_or_get(initial)
    assert created is True
    assert persisted == initial

    result = _result(initial.run_id)
    terminal = initial.model_copy(
        update={
            "revision": 1,
            "state": SucceededStateV1(
                report_uri=result.report_uri,
                authoritative_sha256=result.authoritative_sha256,
                result_sha256=gateway_result_sha256(result),
            ),
        }
    )
    first.replace_run(terminal, expected_revision=0, result=result)
    first.close()

    reopened = SqliteGatewayRepository(database)
    assert reopened.get_run(initial.run_id) == terminal
    assert reopened.get_result(initial.run_id) == result
    recovered, created = reopened.create_or_get(initial)
    assert created is False
    assert recovered == terminal
    reopened.close()


def test_sqlite_repository_rejects_valid_shape_result_json_tampering(
    tmp_path: Path,
) -> None:
    database = tmp_path / "gateway.sqlite3"
    initial = _record("tampered-terminal-result")
    result = _result(initial.run_id)
    terminal = initial.model_copy(
        update={
            "revision": 1,
            "state": SucceededStateV1(
                report_uri=result.report_uri,
                authoritative_sha256=result.authoritative_sha256,
                result_sha256=gateway_result_sha256(result),
            ),
        }
    )
    repository = SqliteGatewayRepository(database)
    repository.create_or_get(initial)
    repository.replace_run(terminal, expected_revision=0, result=result)
    repository.close()

    tampered = result.model_copy(
        update={
            "validation_boundaries": (
                "This remains schema-valid but is not the committed result.",
            )
        }
    )
    connection = sqlite3.connect(database)
    connection.execute(
        "UPDATE gateway_results SET result_json = ? WHERE run_id = ?",
        (canonical_json_bytes(tampered).decode("utf-8"), initial.run_id),
    )
    connection.commit()
    connection.close()

    reopened = SqliteGatewayRepository(database)
    with pytest.raises(GatewayPersistenceError, match="canonical SHA-256"):
        reopened.get_result(initial.run_id)
    reopened.close()


def test_sqlite_repository_rejects_unbound_terminal_result(tmp_path: Path) -> None:
    repository = SqliteGatewayRepository(tmp_path / "gateway.sqlite3")
    initial = _record("unbound-terminal-result")
    result = _result(initial.run_id)
    repository.create_or_get(initial)
    unbound = initial.model_copy(
        update={
            "revision": 1,
            "state": SucceededStateV1(
                report_uri=result.report_uri,
                authoritative_sha256=result.authoritative_sha256,
                result_sha256="0" * 64,
            ),
        }
    )

    with pytest.raises(ConcurrentUpdateError, match="canonical result"):
        repository.replace_run(unbound, expected_revision=0, result=result)
    assert repository.get_run(initial.run_id) == initial
    assert repository.get_result(initial.run_id) is None
    repository.close()


def test_sqlite_repository_detects_persisted_json_tampering(tmp_path: Path) -> None:
    database = tmp_path / "gateway.sqlite3"
    record = _record("tampered-persistence")
    repository = SqliteGatewayRepository(database)
    repository.create_or_get(record)
    repository.close()

    connection = sqlite3.connect(database)
    connection.execute(
        "UPDATE gateway_runs SET record_json = '{}' WHERE run_id = ?",
        (record.run_id,),
    )
    connection.commit()
    connection.close()

    reopened = SqliteGatewayRepository(database)
    with pytest.raises(GatewayPersistenceError, match="run is invalid"):
        reopened.get_run(record.run_id)
    reopened.close()


def test_sqlite_repository_rejects_pre_hash_binding_schema(tmp_path: Path) -> None:
    database = tmp_path / "legacy-gateway.sqlite3"
    connection = sqlite3.connect(database)
    connection.execute(
        "CREATE TABLE gateway_schema_metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL)"
    )
    connection.execute(
        "INSERT INTO gateway_schema_metadata(key, value) VALUES ('schema_version', '1')"
    )
    connection.commit()
    connection.close()

    assert GATEWAY_DATABASE_SCHEMA_VERSION == 2
    with pytest.raises(GatewayPersistenceError, match="unsupported"):
        SqliteGatewayRepository(database)

    connection = sqlite3.connect(database)
    tables = {
        row[0]
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        ).fetchall()
    }
    connection.close()
    assert tables == {"gateway_schema_metadata"}


def test_sqlite_repository_rejects_database_symlink(tmp_path: Path) -> None:
    target = tmp_path / "target.sqlite3"
    target.write_bytes(b"")
    link = tmp_path / "gateway.sqlite3"
    link.symlink_to(target)

    with pytest.raises(GatewayPersistenceError, match="symlink"):
        SqliteGatewayRepository(link)


def test_sqlite_repository_rejects_unversioned_existing_tables(
    tmp_path: Path,
) -> None:
    database = tmp_path / "unversioned.sqlite3"
    connection = sqlite3.connect(database)
    connection.execute("CREATE TABLE unexpected_state (value TEXT NOT NULL)")
    connection.commit()
    connection.close()

    with pytest.raises(GatewayPersistenceError, match="unversioned"):
        SqliteGatewayRepository(database)


def test_sqlite_repository_rejects_stale_cross_connection_revision(
    tmp_path: Path,
) -> None:
    database = tmp_path / "gateway.sqlite3"
    first = SqliteGatewayRepository(database)
    second = SqliteGatewayRepository(database)
    initial = _record("concurrent-persistence")
    first.create_or_get(initial)
    stale = second.get_run(initial.run_id)
    assert stale == initial

    committed = initial.model_copy(
        update={
            "revision": 1,
            "state": RunningStateV1(progress_percent=25, message="first writer"),
        }
    )
    first.replace_run(committed, expected_revision=0, result=None)
    conflicting = stale.model_copy(
        update={
            "revision": 1,
            "state": RunningStateV1(progress_percent=50, message="stale writer"),
        }
    )
    with pytest.raises(ConcurrentUpdateError, match="revision changed"):
        second.replace_run(conflicting, expected_revision=0, result=None)

    assert second.get_run(initial.run_id) == committed
    first.close()
    second.close()
