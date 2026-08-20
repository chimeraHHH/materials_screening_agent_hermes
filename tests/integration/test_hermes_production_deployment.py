from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread
from urllib.error import HTTPError
from urllib.request import urlopen

import pytest


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
HERMES = REPOSITORY_ROOT / ".venv-hermes" / "bin" / "hermes"
HERMES_PYTHON = REPOSITORY_ROOT / ".venv-hermes" / "bin" / "python"
GATEWAY_PYTHON = REPOSITORY_ROOT / ".venv-gateway" / "bin" / "python"
PROFILE_SOURCE = (
    REPOSITORY_ROOT / "integrations" / "hermes" / "profiles" / "materials-inspiration"
)
SCRIPTS = REPOSITORY_ROOT / "integrations" / "hermes" / "scripts"
sys.path.insert(0, str(SCRIPTS))

import production_ops  # noqa: E402
import production_runtime  # noqa: E402
from material_agent.gateway.job_queue import (  # noqa: E402
    JOB_QUEUE_SCHEMA_VERSION,
    SqliteGatewayJobQueue,
)


class _DashboardHealthHandler(BaseHTTPRequestHandler):
    def log_message(self, _format: str, *_arguments: object) -> None:
        return

    def do_GET(self) -> None:  # noqa: N802 - stdlib handler contract
        if self.path != "/api/health":
            self.send_response(404)
            self.end_headers()
            return
        payload = b'{"ok":true}'
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)


def _unused_loopback_port() -> int:
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])
    finally:
        listener.close()


def _http_payload(url: str) -> tuple[int, str]:
    try:
        with urlopen(url, timeout=5) as response:
            return int(response.status), response.read(1_000_000).decode("utf-8")
    except HTTPError as error:
        return int(error.code), error.read(1_000_000).decode("utf-8")


@pytest.mark.skipif(
    not (HERMES.is_file() and HERMES_PYTHON.is_file() and GATEWAY_PYTHON.is_file()),
    reason="isolated Hermes and Gateway runtimes are required",
)
def test_fresh_profile_provider_and_real_mcp_database_probes(tmp_path: Path) -> None:
    hermes_home = tmp_path / "hermes-home"
    workspace = (tmp_path / "workspace").resolve()
    workspace.mkdir()
    environment = dict(os.environ)
    environment["HERMES_HOME"] = str(hermes_home)
    subprocess.run(
        [
            str(HERMES),
            "profile",
            "install",
            str(PROFILE_SOURCE),
            "--name",
            "materials-inspiration",
            "--force",
            "--yes",
        ],
        cwd=REPOSITORY_ROOT,
        env=environment,
        check=True,
        capture_output=True,
        text=True,
    )
    for key, value in (
        ("model.provider", "openrouter"),
        ("model.default", "openai/gpt-4.1"),
    ):
        subprocess.run(
            [str(HERMES), "-p", "materials-inspiration", "config", "set", key, value],
            cwd=REPOSITORY_ROOT,
            env=environment,
            check=True,
            capture_output=True,
            text=True,
        )

    profile_root = hermes_home / "profiles" / "materials-inspiration"
    secret = "test-only-openrouter-secret-00000000000000000000"
    probe_environment = dict(environment)
    probe_environment.update(
        {
            "API_SERVER_KEY": "test-only-server-secret-000000000000000000000",
            "HERMES_HOME": str(profile_root),
            "MATERIAL_AGENT_PROJECT_ID": "materials-inspiration",
            "MATERIAL_AGENT_MCP_BASE_URL": "http://127.0.0.1:19121",
            "MATERIAL_AGENT_PYTHON": str(GATEWAY_PYTHON),
            "MATERIAL_AGENT_SMACT_WORKER_PYTHON": str(
                REPOSITORY_ROOT / ".venv-smact" / "bin" / "python"
            ),
            "MATERIAL_AGENT_WORKSPACE": str(workspace),
            "OPENROUTER_API_KEY": secret,
        }
    )
    profile_probe_command = [
        str(HERMES_PYTHON),
        str(SCRIPTS / "probe_hermes_profile.py"),
        "--profile-root",
        str(profile_root),
        "--expected-profile",
        "materials-inspiration",
        "--expected-provider",
        "openrouter",
        "--expected-model",
        "openai/gpt-4.1",
        "--expected-gateway-python",
        str(GATEWAY_PYTHON),
        "--expected-workspace",
        str(workspace),
        "--expected-project",
        "materials-inspiration",
        "--expected-mcp-base-url",
        "http://127.0.0.1:19121",
        "--expected-source-profile",
        str(PROFILE_SOURCE),
    ]
    profile_probe = subprocess.run(
        profile_probe_command,
        cwd=REPOSITORY_ROOT,
        env=probe_environment,
        check=True,
        capture_output=True,
        text=True,
    )
    profile_payload = json.loads(profile_probe.stdout)
    assert profile_payload["credential_ready"] is True
    assert profile_payload["schema_version"] == "materials-hermes-profile-probe-v2"
    assert profile_payload["queued_actions"] is True
    assert profile_payload["profile_source_verified"] is True
    assert profile_payload["service_factory"] == "shared-loopback-mcp-http-hub-v1"
    assert tuple(profile_payload["tools"]) == (
        "materials_inspiration_run",
        "materials_run_get",
        "materials_run_act",
        "materials_result_get",
    )
    assert tuple(profile_payload["research_tools"]) == (
        "materials_research_pipeline_run",
        "materials_generic_research_run",
    )
    assert secret not in profile_probe.stdout + profile_probe.stderr

    installed_config = profile_root / "config.yaml"
    original_config = installed_config.read_bytes()
    tampered_config = original_config.replace(
        b"plugins:\n  enabled: []",
        b"plugins:\n  enabled: [untrusted-plugin]",
    )
    assert tampered_config != original_config
    installed_config.write_bytes(tampered_config)
    config_drift = subprocess.run(
        profile_probe_command,
        cwd=REPOSITORY_ROOT,
        env=probe_environment,
        check=False,
        capture_output=True,
        text=True,
    )
    assert config_drift.returncode == 2
    assert "configuration differs from source" in config_drift.stderr
    assert secret not in config_drift.stdout + config_drift.stderr
    installed_config.write_bytes(original_config)

    installed_skill = (
        profile_root / "skills" / "materials-inspiration" / "SKILL.md"
    )
    installed_skill.write_bytes(installed_skill.read_bytes() + b"\nunsafe drift\n")
    skill_drift = subprocess.run(
        profile_probe_command,
        cwd=REPOSITORY_ROOT,
        env=probe_environment,
        check=False,
        capture_output=True,
        text=True,
    )
    assert skill_drift.returncode == 2
    assert "Skill closure differs from source" in skill_drift.stderr
    assert secret not in skill_drift.stdout + skill_drift.stderr

    gateway_probe = subprocess.run(
        [
            str(GATEWAY_PYTHON),
            str(SCRIPTS / "probe_gateway_runtime.py"),
            "--workspace",
            str(workspace),
            "--project",
            "materials-inspiration",
        ],
        cwd=REPOSITORY_ROOT,
        env=probe_environment,
        check=True,
        capture_output=True,
        text=True,
    )
    gateway_payload = json.loads(gateway_probe.stdout)
    assert gateway_payload["schema_version"] == (
        "materials-gateway-production-probe-v2"
    )
    assert gateway_payload["gateway_database"]["quick_check"] == "ok"
    assert gateway_payload["approval_database"]["quick_check"] == "ok"
    assert gateway_payload["crossref"]["status"] == "not_requested"
    assert gateway_payload["job_queue"] == {
        "database_shared_with_approval_grants": True,
        "schema_version": JOB_QUEUE_SCHEMA_VERSION,
    }
    assert gateway_payload["queued_actions"] is True
    assert gateway_payload["service_factory"] == (
        "material_agent.integration.queued_gateway:"
        "create_queued_hermes_inspiration_service"
    )


@pytest.mark.skipif(
    not GATEWAY_PYTHON.is_file(),
    reason="isolated Gateway runtime is required",
)
def test_production_factory_static_transport_mcp_restart_e2e(tmp_path: Path) -> None:
    completed = subprocess.run(
        [
            str(GATEWAY_PYTHON),
            str(SCRIPTS / "run_production_e2e.py"),
            "--workspace",
            str(tmp_path / "e2e-workspace"),
            "--project",
            "materials-inspiration-e2e",
            "--submission-id",
            "pytest-production-e2e-20260809",
        ],
        cwd=REPOSITORY_ROOT,
        check=True,
        capture_output=True,
        text=True,
        timeout=180,
    )
    payload = json.loads(completed.stdout.splitlines()[-1])
    assert payload["act_status"] == "RUNNING"
    assert payload["gateway_service_factory"] == (
        "material_agent.integration.queued_gateway:"
        "create_queued_hermes_inspiration_service"
    )
    assert payload["premature_result_rejected"] is True
    assert payload["queued_execution"] is True
    assert payload["schema_version"] == "materials-inspiration-production-e2e-v2"
    assert payload["stage_result_absent_after_act"] is True
    assert payload["static_transport"] is True
    assert payload["restart_recovery"] is True
    assert payload["verified"] is True
    assert payload["readable_evidence_count"] >= 1
    assert payload["status"] in {"SUCCEEDED", "PARTIAL"}
    assert payload["worker_attempt_count"] == 1
    assert payload["worker_job_status"] == "SUCCEEDED"


@pytest.mark.skipif(
    not GATEWAY_PYTHON.is_file(),
    reason="isolated Gateway runtime is required",
)
def test_real_queued_worker_process_uses_shared_project_state_and_stops_cleanly(
    tmp_path: Path,
) -> None:
    workspace = (tmp_path / "worker-workspace").resolve()
    workspace.mkdir()
    settings = production_runtime.Settings(
        workspace=workspace,
        project="worker-lifecycle",
        profile="materials-inspiration",
        provider="",
        model="",
        dashboard_host="127.0.0.1",
        dashboard_port=19119,
        monitor_host="127.0.0.1",
        monitor_port=19120,
        hermes_home=workspace / ".hermes-runtime",
        ops_dir=workspace / ".materials-inspiration-ops",
    )
    settings.ops_dir.mkdir(mode=0o700)
    environment = dict(os.environ)
    environment.update(
        {
            "MATERIAL_AGENT_PROJECT_ID": settings.project,
            "MATERIAL_AGENT_WORKSPACE": str(settings.workspace),
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONUNBUFFERED": "1",
        }
    )
    worker = production_runtime._spawn(
        production_runtime._worker_command(settings),
        env=environment,
        log_path=settings.worker_log,
    )
    try:
        assert production_runtime._wait_worker_ready(
            settings,
            process=worker,
            timeout_seconds=30,
        )
        assert production_ops.owned_process_alive(worker)
        assert settings.artifact_root == workspace / "worker-lifecycle"
        assert settings.approval_database == (
            settings.artifact_root
            / ".gateway"
            / "operator-approval-grants.sqlite3"
        )
        assert settings.gateway_database.is_file()
        queue = production_ops.gateway_queue_snapshot(settings.approval_database)
        assert queue["integrity"] is True
        assert queue["total_jobs"] == 0
    finally:
        stopped = production_runtime._terminate(worker, timeout_seconds=15)
    assert stopped is True
    assert production_ops.owned_process_alive(worker) is False


@pytest.mark.skipif(
    not GATEWAY_PYTHON.is_file(),
    reason="isolated Gateway runtime is required",
)
def test_real_monitor_gates_worker_backlog_blocked_acknowledgement_and_metrics(
    tmp_path: Path,
) -> None:
    dashboard_server = ThreadingHTTPServer(
        ("127.0.0.1", 0),
        _DashboardHealthHandler,
    )
    dashboard_thread = Thread(target=dashboard_server.serve_forever, daemon=True)
    dashboard_thread.start()
    workspace = (tmp_path / "monitor-workspace").resolve()
    workspace.mkdir()
    settings = production_runtime.Settings(
        workspace=workspace,
        project="monitor-lifecycle",
        profile="materials-inspiration",
        provider="",
        model="",
        dashboard_host="127.0.0.1",
        dashboard_port=int(dashboard_server.server_address[1]),
        monitor_host="127.0.0.1",
        monitor_port=_unused_loopback_port(),
        hermes_home=workspace / ".hermes-runtime",
        ops_dir=workspace / ".materials-inspiration-ops",
    )
    settings.ops_dir.mkdir(mode=0o700)
    environment = dict(os.environ)
    environment.update(
        {
            "MATERIAL_AGENT_PROJECT_ID": settings.project,
            "MATERIAL_AGENT_WORKSPACE": str(settings.workspace),
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONUNBUFFERED": "1",
        }
    )
    worker: dict[str, object] | None = None
    monitor: dict[str, object] | None = None
    try:
        worker = production_runtime._spawn(
            production_runtime._worker_command(settings),
            env=environment,
            log_path=settings.worker_log,
        )
        assert production_runtime._wait_worker_ready(
            settings,
            process=worker,
            timeout_seconds=30,
        )
        dashboard_identity = production_ops.process_identity(os.getpid())
        assert dashboard_identity is not None
        binding_sha256 = production_runtime._runtime_binding_sha256(settings)
        process_record = {
            "created_at": production_ops.utc_now(),
            "processes": {
                "dashboard": dashboard_identity,
                "worker": worker,
            },
            "profile": settings.profile,
            "runtime_binding": production_runtime._runtime_binding(settings),
            "runtime_binding_sha256": binding_sha256,
            "schema_version": production_ops.PID_SCHEMA_VERSION,
        }
        production_ops.write_json_atomic(settings.process_file, process_record)
        monitor = production_runtime._spawn(
            production_runtime._monitor_command(
                settings,
                binding_sha256=binding_sha256,
            ),
            env=environment,
            log_path=settings.monitor_log,
        )
        process_record["processes"]["monitor"] = monitor
        production_ops.write_json_atomic(settings.process_file, process_record)
        ready_url = f"http://127.0.0.1:{settings.monitor_port}/readyz"
        health_url = f"http://127.0.0.1:{settings.monitor_port}/healthz"
        metrics_url = f"http://127.0.0.1:{settings.monitor_port}/metrics"
        assert production_runtime._wait_http(
            ready_url,
            process=monitor,
            timeout_seconds=20,
        )
        health_status, health_raw = _http_payload(health_url)
        health = json.loads(health_raw)
        assert health_status == 200
        assert health["worker_pid"] == worker["pid"]
        assert health["worker_identity"]["command_sha256"] == worker["command_sha256"]
        status = production_runtime._status(settings)
        assert status["local_ready"] is True
        assert status["worker_pid"] == worker["pid"]
        assert status["worker_identity"]["command_sha256"] == worker["command_sha256"]
        assert status["runtime_binding_match"] is True

        now_ms = time.time_ns() // 1_000_000
        queue_clock = [now_ms - 121_000]
        queue = SqliteGatewayJobQueue(
            settings.approval_database,
            clock_ms=lambda: queue_clock[0],
        )
        queue.enqueue(
            queue_name="ops-observation",
            job_kind="test-observation",
            idempotency_key="stale-ready",
            payload={"kind": "readiness-test"},
        )
        queue_clock[0] = now_ms
        blocked, _ = queue.enqueue(
            queue_name="ops-observation",
            job_kind="test-observation",
            idempotency_key="blocked-manual",
            payload={"kind": "readiness-test"},
            priority=100,
        )
        claimed = queue.claim(
            queue_name="ops-observation",
            lease_owner="ops-test-worker",
            lease_seconds=30,
        )
        assert claimed is not None and claimed.job_id == blocked.job_id
        queue.fail(
            job_id=claimed.job_id,
            lease_owner="ops-test-worker",
            lease_token=claimed.lease_token or "",
            error_code="BLOCKED_MANUAL_RECOVERY",
            error_message="test-only manual recovery block",
        )
        queue.close()

        health_status, _ = _http_payload(health_url)
        ready_status, ready_raw = _http_payload(ready_url)
        readiness = json.loads(ready_raw)
        assert health_status == 200
        assert ready_status == 503
        assert "READY_BACKLOG_STALE" in readiness["reasons"]
        assert "MANUAL_RECOVERY_BLOCKED" in readiness["reasons"]
        metrics_status, metrics = _http_payload(metrics_url)
        assert metrics_status == 200
        assert "materials_inspiration_worker_up 1" in metrics
        assert "materials_inspiration_queue_failed_jobs 1" in metrics
        assert "materials_inspiration_queue_blocked_jobs 1" in metrics

        queue = SqliteGatewayJobQueue(settings.approval_database)
        queue.acknowledge_terminal_failure(
            job_id=blocked.job_id,
            expected_failure_code="BLOCKED_MANUAL_RECOVERY",
            actor="operator-test",
            reason="test incident reviewed",
        )
        stale = queue.claim(
            queue_name="ops-observation",
            lease_owner="ops-test-worker",
            lease_seconds=30,
        )
        assert stale is not None
        queue.complete(
            job_id=stale.job_id,
            lease_owner="ops-test-worker",
            lease_token=stale.lease_token or "",
            result={"observed": True},
        )
        queue.close()
        assert production_runtime._wait_http(
            ready_url,
            process=monitor,
            timeout_seconds=10,
        )
        metrics_status, metrics = _http_payload(metrics_url)
        assert metrics_status == 200
        assert "materials_inspiration_queue_blocked_jobs 0" in metrics
        assert "materials_inspiration_queue_acknowledged_blocked_total 1" in metrics

        assert production_runtime._terminate(worker, timeout_seconds=15)
        worker = None
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            health_status, health_raw = _http_payload(health_url)
            if health_status == 503:
                break
            time.sleep(0.1)
        assert health_status == 503
        assert json.loads(health_raw)["worker_process"] is False
    finally:
        if monitor is not None:
            production_runtime._terminate(monitor, timeout_seconds=10)
        if worker is not None:
            production_runtime._terminate(worker, timeout_seconds=10)
        dashboard_server.shutdown()
        dashboard_server.server_close()
        dashboard_thread.join(timeout=5)
