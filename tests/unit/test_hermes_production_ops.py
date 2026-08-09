from __future__ import annotations

import hashlib
import os
import sqlite3
import stat
import subprocess
import sys
from dataclasses import replace
from pathlib import Path

import pytest


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS_ROOT = REPOSITORY_ROOT / "integrations" / "hermes" / "scripts"
sys.path.insert(0, str(SCRIPTS_ROOT))

import production_ops  # noqa: E402
import production_monitor  # noqa: E402
import production_runtime  # noqa: E402
from material_agent.gateway.job_queue import SqliteGatewayJobQueue  # noqa: E402


def _gateway_database(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    try:
        connection.execute("CREATE TABLE gateway_runs(record_json TEXT NOT NULL)")
        connection.commit()
    finally:
        connection.close()


def _identity(pid: int) -> dict[str, object]:
    return {
        "command_sha256": hashlib.sha256(f"process-{pid}".encode()).hexdigest(),
        "pid": pid,
        "start_marker": "Sun Aug  9 12:00:00 2026",
    }


def _linux_stat_payload(start_ticks: bytes) -> bytes:
    fields = [b"S", *(str(value).encode() for value in range(4, 22)), start_ticks]
    return b"42 (worker ) name) " + b" ".join(fields) + b"\n"


def _settings(tmp_path: Path) -> production_runtime.Settings:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    ops_dir = workspace / ".materials-inspiration-ops"
    ops_dir.mkdir()
    return production_runtime.Settings(
        workspace=workspace.resolve(),
        project="materials-inspiration",
        profile="materials-inspiration",
        provider="openrouter",
        model="openai/gpt-4.1",
        dashboard_host="127.0.0.1",
        dashboard_port=19119,
        monitor_host="127.0.0.1",
        monitor_port=19120,
        hermes_home=workspace / ".hermes-runtime",
        ops_dir=ops_dir,
    )


def test_operational_events_redact_secrets_and_emit_bounded_metrics(
    tmp_path: Path,
) -> None:
    event_log = tmp_path / "ops" / "events.jsonl"
    production_ops.append_event(
        event_log,
        event="preflight",
        outcome="success",
        details={
            "api_key": "must-not-appear",
            "contact_email": "private@example.test",
            "mcp_tool_count": 4,
        },
    )

    raw = event_log.read_text(encoding="utf-8")
    assert "must-not-appear" not in raw
    assert "private@example.test" not in raw
    assert raw.count("<redacted>") == 2
    queue_database = tmp_path / "approval.sqlite3"
    SqliteGatewayJobQueue(queue_database).close()
    snapshot = production_ops.metrics_snapshot(
        event_log=event_log,
        gateway_database=tmp_path / "missing.sqlite3",
        queue_database=queue_database,
        process_record=None,
        expected_runtime_binding_sha256="a" * 64,
    )
    metrics = production_ops.prometheus_metrics(snapshot)
    assert 'event="preflight",outcome="success"} 1' in metrics
    assert "materials_inspiration_gateway_database_up 0" in metrics
    assert "materials_inspiration_queue_database_up 1" in metrics
    assert "materials_inspiration_worker_up 0" in metrics


def test_node_floor_fails_before_deployment_commands(monkeypatch) -> None:
    versions = {("node", "--version"): "v20.20.2", ("npm", "--version"): "10.9.0"}
    monkeypatch.setattr(
        production_runtime,
        "_output",
        lambda command, **_kwargs: versions[tuple(command)],
    )

    with pytest.raises(
        production_runtime.ProductionRuntimeError,
        match=r"Node >=22\.22\.0",
    ):
        production_runtime._node_check()


def test_worker_and_monitor_environment_excludes_model_and_server_secrets(
    tmp_path: Path,
    monkeypatch,
) -> None:
    settings = _settings(tmp_path)
    monkeypatch.setenv("API_SERVER_KEY", "server-secret")
    monkeypatch.setenv("OPENROUTER_API_KEY", "provider-secret")
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "cloud-secret")
    monkeypatch.setenv("AZURE_STORAGE_CONNECTION_STRING", "cloud-connection-secret")
    monkeypatch.setenv("MATERIALS_CROSSREF_CONTACT_EMAIL", "ops@example.test")

    worker_environment = production_runtime._worker_env(settings)
    monitor_environment = production_runtime._monitor_env(settings)
    dashboard_environment = production_runtime._runtime_env(settings)
    build_environment = production_runtime._build_env(settings)

    assert "API_SERVER_KEY" not in worker_environment
    assert "OPENROUTER_API_KEY" not in worker_environment
    assert "AWS_ACCESS_KEY_ID" not in worker_environment
    assert "AZURE_STORAGE_CONNECTION_STRING" not in worker_environment
    assert "HERMES_HOME" not in worker_environment
    assert worker_environment["MATERIALS_CROSSREF_CONTACT_EMAIL"] == "ops@example.test"
    assert worker_environment["MATERIAL_AGENT_WORKSPACE"] == str(settings.workspace)
    assert "MATERIALS_CROSSREF_CONTACT_EMAIL" not in monitor_environment
    assert "HTTPS_PROXY" not in monitor_environment
    assert dashboard_environment["API_SERVER_KEY"] == "server-secret"
    assert dashboard_environment["OPENROUTER_API_KEY"] == "provider-secret"
    assert "AWS_ACCESS_KEY_ID" not in dashboard_environment
    assert "AZURE_STORAGE_CONNECTION_STRING" not in dashboard_environment
    assert build_environment["HERMES_HOME"] == str(settings.hermes_home)
    assert "API_SERVER_KEY" not in build_environment
    assert "OPENROUTER_API_KEY" not in build_environment
    assert "AWS_ACCESS_KEY_ID" not in build_environment


def test_node_floor_accepts_the_locked_minimum(monkeypatch) -> None:
    versions = {("node", "--version"): "v22.22.0", ("npm", "--version"): "10.9.4"}
    monkeypatch.setattr(
        production_runtime,
        "_output",
        lambda command, **_kwargs: versions[tuple(command)],
    )

    assert production_runtime._node_check() == {
        "node": "22.22.0",
        "npm": "10.9.4",
    }


def test_python_minor_accepts_standard_uv_venv_symlink(
    tmp_path: Path,
    monkeypatch,
) -> None:
    target = tmp_path / "managed-python3.11"
    target.write_bytes(b"placeholder executable")
    interpreter = tmp_path / "venv" / "bin" / "python"
    interpreter.parent.mkdir(parents=True)
    interpreter.symlink_to(target)
    observed: list[list[str]] = []

    def output(command: list[str], **_kwargs) -> str:
        observed.append(command)
        return "3.11"

    monkeypatch.setattr(production_runtime, "_output", output)

    assert production_runtime._python_minor(interpreter) == "3.11"
    assert observed[0][0] == str(interpreter)


def test_python_minor_rejects_broken_symlink(tmp_path: Path) -> None:
    interpreter = tmp_path / "venv" / "bin" / "python"
    interpreter.parent.mkdir(parents=True)
    interpreter.symlink_to(tmp_path / "missing-python")

    with pytest.raises(
        production_runtime.ProductionRuntimeError,
        match="required isolated Python is unavailable",
    ):
        production_runtime._python_minor(interpreter)


def test_linux_stat_parser_uses_field_22_with_parentheses_in_command() -> None:
    assert production_ops._linux_start_marker_from_stat(
        _linux_stat_payload(b"424242")
    ) == "linux-proc-start-ticks:424242"


@pytest.mark.parametrize(
    "payload",
    (
        b"42 worker S 1 2 3\n",
        b"42 (worker) S 1 2 3\n",
        _linux_stat_payload(b"not-a-number"),
        _linux_stat_payload(b"0"),
        b"x" * (production_ops._LINUX_PROC_STAT_MAX_BYTES + 1),
    ),
)
def test_linux_stat_parser_rejects_malformed_or_unbounded_payloads(
    payload: bytes,
) -> None:
    assert production_ops._linux_start_marker_from_stat(payload) is None


def test_linux_process_identity_rejects_pid_reuse_during_capture(
    monkeypatch,
) -> None:
    markers = iter(
        ("linux-proc-start-ticks:100", "linux-proc-start-ticks:101")
    )
    monkeypatch.setattr(
        production_ops,
        "_read_linux_start_marker",
        lambda _pid: next(markers),
    )
    monkeypatch.setattr(
        production_ops,
        "_read_linux_command",
        lambda _pid: b"python\0-m\0worker\0",
    )

    assert production_ops._linux_process_identity(42) is None


@pytest.mark.parametrize("command", (None, b"", b"\0\0"))
def test_linux_process_identity_rejects_missing_or_empty_command(
    monkeypatch,
    command: bytes | None,
) -> None:
    monkeypatch.setattr(
        production_ops,
        "_read_linux_start_marker",
        lambda _pid: "linux-proc-start-ticks:100",
    )
    monkeypatch.setattr(
        production_ops,
        "_read_linux_command",
        lambda _pid: command,
    )

    assert production_ops._linux_process_identity(42) is None


def test_linux_process_identity_hashes_raw_nul_delimited_command(
    monkeypatch,
) -> None:
    command = b"python\0-m\0material_agent.integration.queued_gateway\0"
    monkeypatch.setattr(
        production_ops,
        "_read_linux_start_marker",
        lambda _pid: "linux-proc-start-ticks:100",
    )
    monkeypatch.setattr(
        production_ops,
        "_read_linux_command",
        lambda _pid: command,
    )

    assert production_ops._linux_process_identity(42) == {
        "command_sha256": hashlib.sha256(command).hexdigest(),
        "pid": 42,
        "start_marker": "linux-proc-start-ticks:100",
    }


def test_ps_process_identity_remains_the_non_linux_fallback(monkeypatch) -> None:
    command = "python -m material_agent.integration.queued_gateway"
    completed = subprocess.CompletedProcess(
        args=("ps",),
        returncode=0,
        stdout=f"Sun Aug  9 12:00:00 2026 {command}\n",
        stderr="",
    )
    monkeypatch.setattr(
        production_ops.subprocess,
        "run",
        lambda *_args, **_kwargs: completed,
    )

    assert production_ops._ps_process_identity(42) == {
        "command_sha256": hashlib.sha256(command.encode()).hexdigest(),
        "pid": 42,
        "start_marker": "Sun Aug 9 12:00:00 2026",
    }


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="requires /proc")
def test_linux_real_process_identity_is_stable_and_exact() -> None:
    process = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(30)"],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    identity: dict[str, object] | None = None
    try:
        observations = [production_ops.process_identity(process.pid) for _ in range(8)]
        identity = observations[0]
        assert identity is not None
        assert observations == [identity] * len(observations)
        assert str(identity["start_marker"]).startswith(
            "linux-proc-start-ticks:"
        )
        assert production_ops.owned_process_alive(identity) is True
        assert production_ops.owned_process_alive(
            {
                **identity,
                "start_marker": f'{identity["start_marker"]}-tampered',
            }
        ) is False
        command_sha256 = str(identity["command_sha256"])
        different_prefix = "0" if command_sha256[0] != "0" else "1"
        assert production_ops.owned_process_alive(
            {
                **identity,
                "command_sha256": different_prefix + command_sha256[1:],
            }
        ) is False
    finally:
        process.terminate()
        process.wait(timeout=5)
    assert identity is not None
    assert production_ops.owned_process_alive(identity) is False


def test_v2_process_record_is_not_silently_accepted_by_v3(
    tmp_path: Path,
) -> None:
    gateway_database = tmp_path / "materials-gateway.sqlite3"
    queue_database = tmp_path / "operator-approval-grants.sqlite3"
    _gateway_database(gateway_database)
    SqliteGatewayJobQueue(queue_database).close()
    expected_binding = "a" * 64

    snapshot = production_ops.metrics_snapshot(
        event_log=tmp_path / "events.jsonl",
        gateway_database=gateway_database,
        queue_database=queue_database,
        process_record={
            "processes": {},
            "runtime_binding_sha256": expected_binding,
            "schema_version": "materials-inspiration-process-set-v2",
        },
        expected_runtime_binding_sha256=expected_binding,
    )

    assert production_ops.PID_SCHEMA_VERSION == "materials-inspiration-process-set-v3"
    assert snapshot["process_set_valid"] is False
    assert snapshot["runtime_binding_match"] is False
    assert "PROCESS_SET_INVALID" in snapshot["readiness"]["reasons"]
    assert "RUNTIME_BINDING_MISMATCH" in snapshot["readiness"]["reasons"]


def test_queue_snapshot_exposes_backlog_lease_and_blocked_counts(
    tmp_path: Path,
) -> None:
    now = [100_000]
    queue = SqliteGatewayJobQueue(
        tmp_path / "operator-approval-grants.sqlite3",
        clock_ms=lambda: now[0],
        token_factory=lambda: "1" * 64,
    )
    queue.enqueue(
        queue_name="gateway-actions",
        job_kind="gateway-action",
        idempotency_key="ready-action",
        payload={"action": "approve"},
    )
    queue.enqueue(
        queue_name="gateway-actions",
        job_kind="gateway-action",
        idempotency_key="blocked-action",
        payload={"action": "approve"},
    )
    claimed = queue.claim(
        queue_name="gateway-actions",
        lease_owner="test-worker",
        lease_seconds=60,
    )
    assert claimed is not None
    queue.fail(
        job_id=claimed.job_id,
        lease_owner="test-worker",
        lease_token=claimed.lease_token or "",
        error_code="BLOCKED_MANUAL_RECOVERY",
        error_message="operator inspection required",
    )
    queue.enqueue(
        queue_name="gateway-actions",
        job_kind="gateway-action",
        idempotency_key="running-action",
        payload={"action": "approve"},
    )
    running = queue.claim(
        queue_name="gateway-actions",
        lease_owner="test-worker",
        lease_seconds=60,
    )
    assert running is not None
    queue.close()

    snapshot = production_ops.gateway_queue_snapshot(
        tmp_path / "operator-approval-grants.sqlite3",
        now_ms=225_000,
    )
    assert snapshot["integrity"] is True
    assert snapshot["counts"]["READY"] == 1
    assert snapshot["counts"]["RUNNING"] == 1
    assert snapshot["counts"]["FAILED"] == 1
    assert snapshot["oldest_ready_age_seconds"] == 125.0
    assert snapshot["oldest_running_lease_age_seconds"] == 125.0
    assert snapshot["expired_running_count"] == 1
    assert snapshot["failed_count"] == 1
    assert snapshot["blocked_count"] == 1

    acknowledged_queue = SqliteGatewayJobQueue(
        tmp_path / "operator-approval-grants.sqlite3",
        clock_ms=lambda: 226_000,
    )
    acknowledged_queue.acknowledge_terminal_failure(
        job_id=claimed.job_id,
        expected_failure_code="BLOCKED_MANUAL_RECOVERY",
        actor="operator-1",
        reason="incident reviewed and retained for audit",
    )
    acknowledged_queue.close()
    acknowledged = production_ops.gateway_queue_snapshot(
        tmp_path / "operator-approval-grants.sqlite3",
        now_ms=226_000,
    )
    assert acknowledged["failed_count"] == 1
    assert acknowledged["blocked_count"] == 0
    assert acknowledged["acknowledged_blocked_total"] == 1


def test_queue_snapshot_rejects_foreign_key_and_exact_schema_corruption(
    tmp_path: Path,
) -> None:
    database = tmp_path / "operator-approval-grants.sqlite3"
    SqliteGatewayJobQueue(database).close()
    connection = sqlite3.connect(database)
    try:
        connection.execute("PRAGMA foreign_keys = OFF")
        connection.execute(
            """INSERT INTO gateway_job_events(
                   job_id,event_type,occurred_at_ms,lease_generation,actor,details_json
               ) VALUES(?,?,?,?,?,?)""",
            ("job-missing", "CORRUPTED", 1, 0, None, "{}"),
        )
        connection.commit()
    finally:
        connection.close()

    foreign_key_snapshot = production_ops.gateway_queue_snapshot(database)
    assert foreign_key_snapshot["schema_valid"] is True
    assert foreign_key_snapshot["integrity"] is False

    connection = sqlite3.connect(database)
    try:
        connection.execute("DELETE FROM gateway_job_events")
        connection.execute("DROP INDEX gateway_job_events_job_idx")
        connection.commit()
    finally:
        connection.close()
    schema_snapshot = production_ops.gateway_queue_snapshot(database)
    assert schema_snapshot["schema_valid"] is False
    assert schema_snapshot["integrity"] is False


def test_readiness_fails_for_worker_missing_stale_backlog_and_blocked_job() -> None:
    process = {
        "identity_match": True,
        "pid": 10,
        "recorded_identity": {},
        "up": True,
    }
    snapshot = {
        "dashboard": process,
        "database": {"integrity": True},
        "monitor": process,
        "process_set_valid": True,
        "queue": {
            "blocked_count": 1,
            "expired_running_count": 1,
            "integrity": True,
            "oldest_ready_age_seconds": 121.0,
            "oldest_running_lease_age_seconds": 91.0,
        },
        "runtime_binding_match": True,
        "worker": {**process, "up": False, "identity_match": False},
    }

    readiness = production_ops.operational_readiness(snapshot)

    assert readiness["ok"] is False
    assert readiness["reasons"] == [
        "WORKER_PROCESS_MISSING",
        "READY_BACKLOG_STALE",
        "RUNNING_LEASE_STALE",
        "RUNNING_LEASE_EXPIRED",
        "MANUAL_RECOVERY_BLOCKED",
    ]


def test_monitor_distinguishes_liveness_from_queue_readiness(monkeypatch) -> None:
    process = {
        "identity_match": True,
        "pid": 123,
        "recorded_identity": {
            "command_sha256": "a" * 64,
            "start_marker": "Sun Aug  9 12:00:00 2026",
        },
        "up": True,
    }
    snapshot = {
        "dashboard": process,
        "database": {"integrity": True},
        "monitor": process,
        "process_set_valid": True,
        "queue": {
            "blocked_count": 1,
            "counts": {"FAILED": 1, "READY": 0, "RUNNING": 0, "SUCCEEDED": 0},
            "expired_running_count": 0,
            "failed_count": 1,
            "integrity": True,
            "oldest_ready_age_seconds": 0.0,
            "oldest_running_lease_age_seconds": 0.0,
            "present": True,
            "schema_valid": True,
            "total_jobs": 1,
        },
        "readiness": {
            "max_ready_age_seconds": 120.0,
            "max_running_lease_age_seconds": 90.0,
            "ok": False,
            "reasons": ["MANUAL_RECOVERY_BLOCKED"],
        },
        "runtime_binding_match": True,
        "schema_version": production_ops.METRICS_SCHEMA_VERSION,
        "worker": process,
    }
    monkeypatch.setattr(production_monitor, "read_json", lambda _path: {})
    monkeypatch.setattr(production_monitor, "metrics_snapshot", lambda **_kwargs: snapshot)
    monkeypatch.setattr(
        production_monitor,
        "probe_http_json",
        lambda *_args, **_kwargs: {"ok": True},
    )

    health, readiness, _metrics = production_monitor._payload(
        {
            "dashboard_host": "127.0.0.1",
            "dashboard_port": 19119,
            "event_log": Path("events.jsonl"),
            "gateway_database": Path("gateway.sqlite3"),
            "queue_database": Path("approval.sqlite3"),
            "process_file": Path("processes.json"),
            "runtime_binding_sha256": "a" * 64,
        }
    )
    assert health["ok"] is True
    assert readiness["ok"] is False
    assert readiness["reasons"] == ["MANUAL_RECOVERY_BLOCKED"]
    assert readiness["worker_pid"] == 123

    snapshot["worker"] = {
        "identity_match": False,
        "pid": 123,
        "recorded_identity": process["recorded_identity"],
        "up": False,
    }
    snapshot["readiness"] = {
        **snapshot["readiness"],
        "ok": False,
        "reasons": ["WORKER_PROCESS_MISSING"],
    }
    health, readiness, _metrics = production_monitor._payload(
        {
            "dashboard_host": "127.0.0.1",
            "dashboard_port": 19119,
            "event_log": Path("events.jsonl"),
            "gateway_database": Path("gateway.sqlite3"),
            "queue_database": Path("approval.sqlite3"),
            "process_file": Path("processes.json"),
            "runtime_binding_sha256": "a" * 64,
        }
    )
    assert health["ok"] is False
    assert readiness["ok"] is False
    assert readiness["reasons"] == ["WORKER_PROCESS_MISSING"]


def test_runtime_start_stop_owns_worker_and_exact_shared_binding(
    tmp_path: Path,
    monkeypatch,
) -> None:
    settings = _settings(tmp_path)
    spawned: list[tuple[list[str], Path]] = []
    terminated: list[tuple[int, float]] = []
    preflight_modes: list[bool] = []

    def spawn(command: list[str], *, env: dict[str, str], log_path: Path):
        del env
        identity = _identity(1_001 + len(spawned))
        spawned.append((command, log_path))
        return identity

    def terminate(record, *, timeout_seconds=10.0):
        if record is None:
            return False
        terminated.append((record["pid"], timeout_seconds))
        return True

    def preflight(_settings, *, live_crossref: bool):
        preflight_modes.append(live_crossref)
        if live_crossref:
            raise production_runtime.ProductionRuntimeError("Crossref unavailable")
        return {
            "duration_ms": 7,
            "gateway": {"crossref": {"status": "not_requested"}},
        }

    monkeypatch.setattr(production_runtime, "_runtime_env", lambda _settings: {})
    monkeypatch.setattr(production_runtime, "_preflight", preflight)
    monkeypatch.setattr(production_runtime, "_spawn", spawn)
    monkeypatch.setattr(production_runtime, "_wait_worker_ready", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(production_runtime, "_wait_http", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(
        production_runtime,
        "probe_http_json",
        lambda *_args, **_kwargs: {"ok": True, "value": {"ok": True}},
    )
    monkeypatch.setattr(production_runtime, "_terminate", terminate)

    result = production_runtime._start(settings)
    assert preflight_modes == [False]
    assert result["worker"]["pid"] == 1_001
    worker_command = spawned[0][0]
    assert worker_command[:3] == [
        str(production_runtime.GATEWAY_ENV / "bin" / "python"),
        "-m",
        "material_agent.integration.queued_gateway",
    ]
    assert worker_command[worker_command.index("--workspace") + 1] == str(
        settings.workspace
    )
    assert worker_command[worker_command.index("--project") + 1] == settings.project
    monitor_command = spawned[2][0]
    assert monitor_command[monitor_command.index("--queue-database") + 1] == str(
        settings.approval_database
    )
    record = production_ops.read_json(settings.process_file)
    assert record is not None
    assert record["runtime_binding"] == production_runtime._runtime_binding(settings)
    assert record["processes"]["worker"]["pid"] == 1_001

    stopped = production_runtime._stop(settings)
    assert stopped == {
        "dashboard_stopped": True,
        "monitor_stopped": True,
        "worker_stopped": True,
    }
    assert terminated == [
        (1_002, production_runtime.LOCAL_PROCESS_STOP_TIMEOUT_SECONDS),
        (1_001, production_runtime.WORKER_STOP_TIMEOUT_SECONDS),
        (1_003, production_runtime.LOCAL_PROCESS_STOP_TIMEOUT_SECONDS),
    ]
    assert production_runtime._stop(settings) == {
        "dashboard_stopped": False,
        "monitor_stopped": False,
        "worker_stopped": False,
    }


def test_strict_crossref_start_fails_before_any_process_is_spawned(
    tmp_path: Path,
    monkeypatch,
) -> None:
    settings = _settings(tmp_path)
    spawned: list[list[str]] = []

    def strict_preflight(_settings, *, live_crossref: bool):
        assert live_crossref is True
        raise production_runtime.ProductionRuntimeError("Crossref unavailable")

    monkeypatch.setattr(production_runtime, "_preflight", strict_preflight)
    monkeypatch.setattr(
        production_runtime,
        "_spawn",
        lambda command, **_kwargs: spawned.append(command),
    )

    with pytest.raises(
        production_runtime.ProductionRuntimeError,
        match="Crossref unavailable",
    ):
        production_runtime._start(settings, require_live_crossref=True)
    assert spawned == []


@pytest.mark.parametrize(
    ("fault", "expected_terminated"),
    (
        ("worker_write", [1_001]),
        ("dashboard_spawn", [1_001]),
        ("ready_wait", [1_003, 1_002, 1_001]),
    ),
)
def test_startup_faults_roll_back_all_owned_processes_in_reverse_order(
    tmp_path: Path,
    monkeypatch,
    fault: str,
    expected_terminated: list[int],
) -> None:
    settings = _settings(tmp_path)
    spawned = 0
    terminated: list[int] = []
    writes = 0

    def spawn(_command, **_kwargs):
        nonlocal spawned
        spawned += 1
        if fault == "dashboard_spawn" and spawned == 2:
            raise production_runtime.ProductionRuntimeError("dashboard spawn failed")
        return _identity(1_000 + spawned)

    original_write = production_runtime.write_json_atomic

    def write(path, payload):
        nonlocal writes
        writes += 1
        if fault == "worker_write" and writes == 1:
            raise production_ops.ProductionOpsError("process record write failed")
        original_write(path, payload)

    def terminate(record, **_kwargs):
        if record is None:
            return False
        terminated.append(record["pid"])
        return True

    def wait_http(url: str, **_kwargs) -> bool:
        return not (fault == "ready_wait" and url.endswith("/readyz"))

    monkeypatch.setattr(production_runtime, "_runtime_env", lambda _settings: {})
    monkeypatch.setattr(production_runtime, "_spawn", spawn)
    monkeypatch.setattr(production_runtime, "write_json_atomic", write)
    monkeypatch.setattr(production_runtime, "_wait_worker_ready", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(production_runtime, "_wait_http", wait_http)
    monkeypatch.setattr(production_runtime, "_terminate", terminate)
    monkeypatch.setattr(
        production_runtime,
        "probe_http_json",
        lambda *_args, **_kwargs: {"ok": True, "value": {"ok": True}},
    )

    with pytest.raises((production_ops.ProductionOpsError, production_runtime.ProductionRuntimeError)):
        production_runtime._start(
            settings,
            completed_preflight={
                "duration_ms": 1,
                "gateway": {"crossref": {"status": "not_requested"}},
            },
        )
    assert terminated == expected_terminated
    record = production_ops.read_json(settings.process_file)
    assert record is not None
    assert record["processes"] == {}
    assert "startup_rollback_at" in record


def test_health_keeps_local_readiness_when_crossref_is_unavailable(
    tmp_path: Path,
    monkeypatch,
) -> None:
    settings = _settings(tmp_path)
    monkeypatch.setattr(
        production_runtime,
        "_preflight",
        lambda *_args, **_kwargs: {"duration_ms": 1, "gateway": {"crossref": {"status": "not_requested"}}},
    )
    monkeypatch.setattr(
        production_runtime,
        "_status",
        lambda _settings: {"local_ready": True, "running": True},
    )

    def unavailable(*_args, **_kwargs):
        raise production_runtime.ProductionRuntimeError("Crossref unavailable")

    monkeypatch.setattr(production_runtime, "_gateway_probe", unavailable)
    result = production_runtime._health(settings)
    assert result["local_ready"] is True
    assert result["healthy"] is True
    assert result["external_crossref_ready"] is False
    assert result["external_crossref"]["status"] == "unavailable"


def test_lifecycle_lock_rejects_concurrent_process_and_is_reusable(
    tmp_path: Path,
) -> None:
    settings = _settings(tmp_path)
    settings.lifecycle_lock.parent.mkdir(parents=True, exist_ok=True)
    holder = subprocess.Popen(
        [
            sys.executable,
            "-c",
            (
                "import fcntl,os,sys;"
                "fd=os.open(sys.argv[1],os.O_CREAT|os.O_RDWR,0o600);"
                "fcntl.flock(fd,fcntl.LOCK_EX);"
                "print('locked',flush=True);"
                "sys.stdin.read(1)"
            ),
            str(settings.lifecycle_lock),
        ],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        assert holder.stdout is not None
        assert holder.stdout.readline().strip() == "locked"
        with pytest.raises(
            production_runtime.ProductionRuntimeError,
            match="another production lifecycle operation",
        ):
            with production_runtime._lifecycle_lock(settings):
                pytest.fail("concurrent lifecycle lock was acquired")
    finally:
        if holder.stdin is not None:
            try:
                holder.stdin.write("x")
                holder.stdin.flush()
            except BrokenPipeError:
                pass
            holder.stdin.close()
        holder.wait(timeout=5)

    with production_runtime._lifecycle_lock(settings):
        assert settings.lifecycle_lock.is_file()
    assert stat.S_IMODE(settings.lifecycle_lock.stat().st_mode) == 0o600


def test_repository_bootstrap_lock_is_fail_fast_and_reusable(
    tmp_path: Path,
    monkeypatch,
) -> None:
    lock_path = tmp_path / "repository-bootstrap.lock"
    monkeypatch.setattr(production_runtime, "BOOTSTRAP_LOCK", lock_path)

    with production_runtime._bootstrap_lock():
        with pytest.raises(
            production_runtime.ProductionRuntimeError,
            match="another production bootstrap",
        ):
            with production_runtime._bootstrap_lock():
                pytest.fail("nested bootstrap lock was acquired")

    with production_runtime._bootstrap_lock():
        assert lock_path.is_file()
    assert stat.S_IMODE(lock_path.stat().st_mode) == 0o600


def test_lifecycle_ownership_cannot_be_aliased_by_ops_directory(
    tmp_path: Path,
) -> None:
    settings = _settings(tmp_path)
    aliased = replace(settings, ops_dir=settings.workspace / "alternate-ops")

    assert aliased.lifecycle_lock == settings.lifecycle_lock
    assert aliased.process_file == settings.process_file
    assert production_runtime._runtime_binding(aliased) != (
        production_runtime._runtime_binding(settings)
    )
    with production_runtime._lifecycle_lock(settings):
        with pytest.raises(
            production_runtime.ProductionRuntimeError,
            match="another production lifecycle operation",
        ):
            with production_runtime._lifecycle_lock(aliased):
                pytest.fail("ops-dir alias bypassed canonical project lock")


def test_atomic_json_fsyncs_parent_directory(
    tmp_path: Path,
    monkeypatch,
) -> None:
    path = tmp_path / "ops" / "processes.json"
    observed_directory_fsync = False
    real_fsync = production_ops.os.fsync

    def fsync(descriptor: int) -> None:
        nonlocal observed_directory_fsync
        if stat.S_ISDIR(os.fstat(descriptor).st_mode):
            observed_directory_fsync = True
        real_fsync(descriptor)

    monkeypatch.setattr(production_ops.os, "fsync", fsync)
    production_ops.write_json_atomic(path, {"schema_version": "test-v1"})

    assert production_ops.read_json(path) == {"schema_version": "test-v1"}
    assert observed_directory_fsync is True
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_runtime_and_event_logs_rotate_with_bounded_mode_0600_generations(
    tmp_path: Path,
    monkeypatch,
) -> None:
    runtime_log = tmp_path / "ops" / "worker.log"
    runtime_log.parent.mkdir()
    runtime_log.write_bytes(b"old-runtime-log")
    runtime_log.chmod(0o644)
    monkeypatch.setattr(production_runtime, "MAX_RUNTIME_LOG_BYTES", 4)
    monkeypatch.setattr(production_runtime, "MAX_RUNTIME_LOG_GENERATIONS", 2)

    with production_runtime._open_log(runtime_log) as stream:
        stream.write(b"new-runtime-log")

    assert runtime_log.read_bytes() == b"new-runtime-log"
    assert runtime_log.with_name("worker.log.1").read_bytes() == b"old-runtime-log"
    for path in (runtime_log, runtime_log.with_name("worker.log.1")):
        assert stat.S_IMODE(path.stat().st_mode) == 0o600

    event_log = tmp_path / "ops" / "events.jsonl"
    monkeypatch.setattr(production_ops, "MAX_EVENT_LOG_BYTES", 512)
    monkeypatch.setattr(production_ops, "MAX_EVENT_LOG_GENERATIONS", 2)
    monkeypatch.setattr(production_ops, "MAX_EVENT_TOTAL_BYTES", 1_536)
    for index in range(8):
        production_ops.append_event(
            event_log,
            event="rotation_test",
            outcome="success",
            details={"index": index},
        )

    rows = production_ops.read_events(event_log)
    assert rows[-1]["details"]["index"] == 7
    assert event_log.with_name("events.jsonl.1").is_file()
    for path in event_log.parent.glob("events.jsonl*"):
        assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_ack_failure_entrypoint_is_exact_idempotent_and_reason_safe(
    tmp_path: Path,
) -> None:
    settings = _settings(tmp_path)
    queue = SqliteGatewayJobQueue(settings.approval_database)
    job, _ = queue.enqueue(
        queue_name="gateway-actions",
        job_kind="gateway-action",
        idempotency_key="blocked-for-operator",
        payload={"action": "approve"},
    )
    claimed = queue.claim(
        queue_name="gateway-actions",
        lease_owner="worker-test",
        lease_seconds=30,
    )
    assert claimed is not None and claimed.job_id == job.job_id
    queue.fail(
        job_id=claimed.job_id,
        lease_owner="worker-test",
        lease_token=claimed.lease_token or "",
        error_code="BLOCKED_MANUAL_RECOVERY",
        error_message="manual inspection required",
    )
    queue.close()

    reason = "incident reviewed without deleting history"
    first = production_runtime._ack_failure(
        settings,
        job_id=job.job_id,
        expected_code="BLOCKED_MANUAL_RECOVERY",
        actor="operator-1",
        reason=reason,
    )
    replay = production_runtime._ack_failure(
        settings,
        job_id=job.job_id,
        expected_code="BLOCKED_MANUAL_RECOVERY",
        actor="operator-1",
        reason=reason,
    )
    assert replay == first
    assert set(first) == {"acknowledged_at_ms", "actor", "job_id", "status"}
    assert reason not in settings.event_log.read_text(encoding="utf-8")

    for expected_code, actor, conflicting_reason in (
        ("OTHER_FAILURE", "operator-1", reason),
        ("BLOCKED_MANUAL_RECOVERY", "operator-2", reason),
        ("BLOCKED_MANUAL_RECOVERY", "operator-1", "different review"),
    ):
        with pytest.raises(
            production_runtime.ProductionRuntimeError,
            match="failure acknowledgement was rejected",
        ):
            production_runtime._ack_failure(
                settings,
                job_id=job.job_id,
                expected_code=expected_code,
                actor=actor,
                reason=conflicting_reason,
            )

    queue = SqliteGatewayJobQueue(settings.approval_database)
    events = queue.list_events(job.job_id)
    queue.close()
    assert sum(
        event.event_type == "TERMINAL_FAILURE_ACKNOWLEDGED"
        for event in events
    ) == 1
