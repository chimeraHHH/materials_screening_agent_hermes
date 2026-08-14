#!/usr/bin/env python3
"""One-command production lifecycle for the Materials Inspiration profile."""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import re
import signal
import stat
import subprocess
import sys
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from threading import Lock
from typing import Any, Iterator

from managed_checkout import ManagedCheckoutError, repair_generated_package_locks

from production_ops import (
    PID_SCHEMA_VERSION,
    ProductionOpsError,
    append_event,
    fsync_directory,
    gateway_queue_snapshot,
    metrics_snapshot,
    owned_process_alive,
    process_identity,
    probe_http_json,
    prometheus_metrics,
    read_json,
    rotate_bounded_file,
    utc_now,
    write_json_atomic,
)


REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
BOOTSTRAP_LOCK = REPOSITORY_ROOT / ".materials-inspiration-bootstrap.lock"
HERMES_ROOT = REPOSITORY_ROOT / "integrations" / "hermes"
SCRIPTS_ROOT = HERMES_ROOT / "scripts"
PROFILE_SOURCE = HERMES_ROOT / "profiles" / "materials-inspiration"
HERMES_ENV = REPOSITORY_ROOT / ".venv-hermes"
GATEWAY_ENV = REPOSITORY_ROOT / ".venv-gateway"
HERMES_SOURCE = REPOSITORY_ROOT / ".external" / "hermes-agent"
HERMES_LOCK = HERMES_ROOT / "hermes.lock.json"
EXPECTED_TOOLS = (
    "materials_inspiration_run",
    "materials_run_get",
    "materials_run_act",
    "materials_result_get",
)
MINIMUM_NODE = (22, 22, 0)
_IDENTIFIER = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
_PROVIDER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_MODEL = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/+-]{0,255}$")
_LOOPBACK = frozenset({"127.0.0.1", "::1", "localhost"})
_COMMON_CHILD_ENVIRONMENT_ALLOWLIST = frozenset(
    {
        "LANG",
        "LC_ALL",
        "LC_CTYPE",
        "PATH",
        "SYSTEM_VERSION_COMPAT",
        "TEMP",
        "TMP",
        "TMPDIR",
    }
)
_NETWORK_CHILD_ENVIRONMENT_ALLOWLIST = frozenset(
    {
        "ALL_PROXY",
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "NO_PROXY",
        "REQUESTS_CA_BUNDLE",
        "SSL_CERT_DIR",
        "SSL_CERT_FILE",
        "all_proxy",
        "http_proxy",
        "https_proxy",
        "no_proxy",
    }
)
_PROVIDER_ENVIRONMENT_ALLOWLIST = {
    "anthropic": frozenset({"ANTHROPIC_API_KEY", "ANTHROPIC_BASE_URL"}),
    "aws": frozenset(
        {
            "AWS_ACCESS_KEY_ID",
            "AWS_DEFAULT_REGION",
            "AWS_REGION",
            "AWS_SECRET_ACCESS_KEY",
            "AWS_SESSION_TOKEN",
        }
    ),
    "azure": frozenset(
        {
            "AZURE_OPENAI_API_KEY",
            "AZURE_OPENAI_ENDPOINT",
            "OPENAI_API_VERSION",
        }
    ),
    "bedrock": frozenset(
        {
            "AWS_ACCESS_KEY_ID",
            "AWS_DEFAULT_REGION",
            "AWS_REGION",
            "AWS_SECRET_ACCESS_KEY",
            "AWS_SESSION_TOKEN",
        }
    ),
    "deepseek": frozenset({"DEEPSEEK_API_KEY", "DEEPSEEK_BASE_URL"}),
    "google": frozenset(
        {
            "GEMINI_API_KEY",
            "GOOGLE_API_KEY",
            "GOOGLE_APPLICATION_CREDENTIALS",
            "GOOGLE_CLOUD_PROJECT",
        }
    ),
    "openai": frozenset(
        {
            "OPENAI_API_KEY",
            "OPENAI_BASE_URL",
            "OPENAI_ORG_ID",
            "OPENAI_PROJECT",
        }
    ),
    "openrouter": frozenset({"OPENROUTER_API_KEY", "OPENROUTER_BASE_URL"}),
    "vertex": frozenset(
        {
            "GOOGLE_APPLICATION_CREDENTIALS",
            "GOOGLE_CLOUD_PROJECT",
            "GOOGLE_CLOUD_REGION",
        }
    ),
}
WORKER_POLL_SECONDS = 1.0
WORKER_LEASE_SECONDS = 30
WORKER_STOP_TIMEOUT_SECONDS = 5
LOCAL_PROCESS_STOP_TIMEOUT_SECONDS = 2
MAX_RUNTIME_LOG_BYTES = 8_000_000
MAX_RUNTIME_LOG_GENERATIONS = 3
_LIFECYCLE_THREAD_LOCK = Lock()
_BOOTSTRAP_THREAD_LOCK = Lock()


class ProductionRuntimeError(RuntimeError):
    pass


_DEEPSEEK_RETIRED_MODEL_ALIASES = frozenset(
    {
        "deepseek-chat",
        "deepseek-reasoner",
    }
)

# Hermes' interactive chat and the materials research RAG deliberately use
# different provider contracts.  The dashboard may track the current fast
# chat model, while ``DeepSeekProvider`` in the scientific pipeline is frozen
# to the audited JSON-mode model below.  Never derive this value from
# ``settings.model`` or a parent shell that was configured for Hermes chat.
MATERIALS_RESEARCH_RAG_MODEL = "deepseek-v4-pro"


def _canonical_model(provider: str, model: str) -> str:
    """Match the model persisted in the UI to Hermes' runtime identity."""

    provider_family = provider.casefold().split(":", 1)[0]
    bare_model = model.rsplit("/", 1)[-1].casefold()
    if (
        provider_family == "deepseek"
        and bare_model in _DEEPSEEK_RETIRED_MODEL_ALIASES
    ):
        return "deepseek-v4-flash"
    return model


def parse_version(value: str) -> tuple[int, int, int]:
    match = re.fullmatch(r"v?(\d+)\.(\d+)\.(\d+)(?:[-+].*)?", value.strip())
    if match is None:
        raise ProductionRuntimeError("runtime version has an unexpected format")
    return tuple(int(part) for part in match.groups())  # type: ignore[return-value]


def _output(command: list[str], *, env: dict[str, str] | None = None) -> str:
    try:
        completed = subprocess.run(
            command,
            cwd=REPOSITORY_ROOT,
            env=env,
            check=True,
            capture_output=True,
            text=True,
            timeout=120,
        )
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        raise ProductionRuntimeError("runtime verification command failed") from exc
    return completed.stdout.strip()


def _run(
    command: list[str],
    *,
    env: dict[str, str] | None = None,
    timeout: int = 900,
) -> None:
    try:
        subprocess.run(
            command,
            cwd=REPOSITORY_ROOT,
            env=env,
            check=True,
            timeout=timeout,
        )
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        raise ProductionRuntimeError("runtime command failed") from exc


def _node_check() -> dict[str, Any]:
    node = _output(["node", "--version"])
    version = parse_version(node)
    if version < MINIMUM_NODE:
        raise ProductionRuntimeError(
            "Hermes dashboard requires Node >=22.22.0; upgrade Node before deployment"
        )
    npm = _output(["npm", "--version"])
    parse_version(npm)
    return {"node": node.lstrip("v"), "npm": npm}


def _python_minor(executable: Path) -> str:
    # ``uv venv`` deliberately creates ``bin/python`` as a symlink to its
    # managed CPython.  Reject broken links and non-regular resolved targets,
    # but accept that standard isolated-environment layout.
    try:
        resolved = executable.resolve(strict=True)
    except (OSError, RuntimeError):
        raise ProductionRuntimeError(
            "required isolated Python is unavailable"
        ) from None
    if not resolved.is_file():
        raise ProductionRuntimeError("required isolated Python is unavailable")
    return _output(
        [
            str(executable),
            "-c",
            "import sys; print(f'{sys.version_info[0]}.{sys.version_info[1]}')",
        ]
    )


def _hermes_identity_check() -> dict[str, str]:
    try:
        lock = json.loads(HERMES_LOCK.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ProductionRuntimeError("Hermes release lock is invalid") from exc
    expected_commit = str(lock.get("resolved_commit") or "")
    expected_version = str(lock.get("package_version") or "")
    if not re.fullmatch(r"[0-9a-f]{40}", expected_commit) or not expected_version:
        raise ProductionRuntimeError("Hermes release identity is invalid")
    if not (HERMES_SOURCE / ".git").is_dir():
        raise ProductionRuntimeError("pinned Hermes checkout is unavailable")
    resolved_commit = _output(
        ["git", "-C", str(HERMES_SOURCE), "rev-parse", "HEAD"]
    )
    if resolved_commit != expected_commit:
        raise ProductionRuntimeError("Hermes checkout commit differs from the release lock")
    dirty = _output(["git", "-C", str(HERMES_SOURCE), "status", "--porcelain"])
    if dirty:
        raise ProductionRuntimeError("Hermes checkout is dirty")
    version_output = _output([str(HERMES_ENV / "bin" / "hermes"), "--version"])
    if expected_version not in version_output:
        raise ProductionRuntimeError("Hermes executable version differs from the release lock")
    return {"commit": resolved_commit, "version": expected_version}


def _repair_hermes_generated_drift() -> tuple[str, ...]:
    """Repair only npm lock metadata that the managed Hermes UI may rewrite."""

    try:
        lock = json.loads(HERMES_LOCK.read_text(encoding="utf-8"))
        repaired = repair_generated_package_locks(
            HERMES_SOURCE,
            expected_commit=str(lock["resolved_commit"]),
        )
    except (OSError, KeyError, json.JSONDecodeError, ManagedCheckoutError) as exc:
        raise ProductionRuntimeError(
            "Hermes runtime left unsafe checkout drift"
        ) from exc
    if repaired:
        print(
            "Restored npm-generated Hermes lockfile drift: " + ", ".join(repaired),
            file=sys.stderr,
        )
    return repaired


@dataclass(frozen=True)
class Settings:
    workspace: Path
    project: str
    profile: str
    provider: str
    model: str
    dashboard_host: str
    dashboard_port: int
    monitor_host: str
    monitor_port: int
    hermes_home: Path
    ops_dir: Path

    @property
    def profile_root(self) -> Path:
        return self.hermes_home / "profiles" / self.profile

    @property
    def event_log(self) -> Path:
        return self.ops_dir / "events.jsonl"

    @property
    def process_file(self) -> Path:
        return self.project_root / ".gateway" / "production-processes.json"

    @property
    def lifecycle_lock(self) -> Path:
        return self.project_root / ".gateway" / "production-lifecycle.lock"

    @property
    def gateway_database(self) -> Path:
        return (
            self.workspace
            / self.project
            / ".gateway"
            / "materials-gateway.sqlite3"
        )

    @property
    def project_root(self) -> Path:
        return self.workspace / self.project

    @property
    def approval_database(self) -> Path:
        return (
            self.project_root
            / ".gateway"
            / "operator-approval-grants.sqlite3"
        )

    @property
    def artifact_root(self) -> Path:
        return self.project_root

    @property
    def mcp_host(self) -> str:
        return self.monitor_host

    @property
    def mcp_port(self) -> int:
        return self.monitor_port + 1

    @property
    def mcp_base_url(self) -> str:
        return f"http://{self.mcp_host}:{self.mcp_port}"

    @property
    def dashboard_log(self) -> Path:
        return self.ops_dir / "dashboard.log"

    @property
    def monitor_log(self) -> Path:
        return self.ops_dir / "monitor.log"

    @property
    def worker_log(self) -> Path:
        return self.ops_dir / "gateway-worker.log"

    @classmethod
    def from_args(cls, args: argparse.Namespace, *, require_model: bool) -> "Settings":
        if not args.workspace:
            raise ProductionRuntimeError(
                "--workspace or MATERIAL_AGENT_WORKSPACE is required"
            )
        workspace_input = Path(args.workspace).expanduser()
        if workspace_input.is_symlink():
            raise ProductionRuntimeError("workspace cannot be a symlink")
        workspace_input.mkdir(parents=True, exist_ok=True)
        workspace = workspace_input.resolve()
        if not _IDENTIFIER.fullmatch(args.project):
            raise ProductionRuntimeError("project ID is invalid")
        if not _IDENTIFIER.fullmatch(args.profile):
            raise ProductionRuntimeError("profile ID is invalid")
        if (workspace / args.project).is_symlink():
            raise ProductionRuntimeError("project root cannot be a symlink")
        provider = (args.provider or "").strip()
        model = (args.model or "").strip()
        if require_model and not _PROVIDER.fullmatch(provider):
            raise ProductionRuntimeError(
                "--provider or MATERIALS_HERMES_PROVIDER is required"
            )
        if require_model and not _MODEL.fullmatch(model):
            raise ProductionRuntimeError(
                "--model or MATERIALS_HERMES_MODEL is required"
            )
        model = _canonical_model(provider, model)
        if args.dashboard_host not in _LOOPBACK or args.monitor_host not in _LOOPBACK:
            raise ProductionRuntimeError("dashboard and monitor must remain loopback-bound")
        for port in (args.dashboard_port, args.monitor_port):
            if not 1 <= port <= 65_535:
                raise ProductionRuntimeError("runtime port is outside the valid range")
        if args.monitor_port == 65_535:
            raise ProductionRuntimeError("monitor port leaves no port for the MCP Hub")
        if args.dashboard_port == args.monitor_port:
            raise ProductionRuntimeError("dashboard and monitor ports must differ")
        if args.dashboard_port == args.monitor_port + 1:
            raise ProductionRuntimeError("dashboard and MCP Hub ports must differ")
        hermes_home = (
            Path(args.hermes_home).expanduser().resolve()
            if args.hermes_home
            else (workspace / ".hermes-runtime").resolve()
        )
        ops_dir = (
            Path(args.ops_dir).expanduser().resolve()
            if args.ops_dir
            else (workspace / ".materials-inspiration-ops").resolve()
        )
        for path, label in ((hermes_home, "Hermes home"), (ops_dir, "ops directory")):
            if path == workspace or workspace not in path.parents:
                raise ProductionRuntimeError(f"{label} must stay below the workspace")
            if path.is_symlink():
                raise ProductionRuntimeError(f"{label} cannot be a symlink")
            path.mkdir(parents=True, exist_ok=True, mode=0o700)
            os.chmod(path, 0o700)
        return cls(
            workspace=workspace,
            project=args.project,
            profile=args.profile,
            provider=provider,
            model=model,
            dashboard_host=args.dashboard_host,
            dashboard_port=args.dashboard_port,
            monitor_host=args.monitor_host,
            monitor_port=args.monitor_port,
            hermes_home=hermes_home,
            ops_dir=ops_dir,
        )


@contextmanager
def _exclusive_file_lock(
    path: Path,
    *,
    thread_lock: Any,
    busy_message: str,
) -> Iterator[None]:
    if not thread_lock.acquire(blocking=False):
        raise ProductionRuntimeError(busy_message)
    descriptor: int | None = None
    flock_acquired = False
    try:
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        if path.is_symlink():
            raise ProductionRuntimeError("production lock cannot be a symlink")
        existed = path.exists()
        flags = os.O_CREAT | os.O_RDWR
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        descriptor = os.open(path, flags, 0o600)
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode):
            raise ProductionRuntimeError("production lock is not a regular file")
        os.fchmod(descriptor, 0o600)
        if not existed:
            fsync_directory(path.parent)
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise ProductionRuntimeError(busy_message) from exc
        flock_acquired = True
        yield
    finally:
        if descriptor is not None:
            if flock_acquired:
                fcntl.flock(descriptor, fcntl.LOCK_UN)
            os.close(descriptor)
        thread_lock.release()


@contextmanager
def _lifecycle_lock(settings: Settings) -> Iterator[None]:
    """Own one fail-fast lifecycle transaction across processes and threads."""

    with _exclusive_file_lock(
        settings.lifecycle_lock,
        thread_lock=_LIFECYCLE_THREAD_LOCK,
        busy_message="another production lifecycle operation is in progress",
    ):
        yield


@contextmanager
def _bootstrap_lock() -> Iterator[None]:
    """Serialize repository-wide environment mutation across all workspaces."""

    with _exclusive_file_lock(
        BOOTSTRAP_LOCK,
        thread_lock=_BOOTSTRAP_THREAD_LOCK,
        busy_message="another production bootstrap is in progress",
    ):
        yield


def _runtime_binding(settings: Settings) -> dict[str, Any]:
    """Return the exact non-secret filesystem binding shared by MCP and worker."""

    return {
        "approval_database": str(settings.approval_database),
        "artifact_root": str(settings.artifact_root),
        "dashboard_host": settings.dashboard_host,
        "dashboard_port": settings.dashboard_port,
        "gateway_database": str(settings.gateway_database),
        "hermes_home": str(settings.hermes_home),
        "monitor_host": settings.monitor_host,
        "monitor_port": settings.monitor_port,
        "mcp_base_url": settings.mcp_base_url,
        "ops_dir": str(settings.ops_dir),
        "process_file": str(settings.process_file),
        "profile": settings.profile,
        "profile_root": str(settings.profile_root),
        "project": settings.project,
        "workspace": str(settings.workspace),
    }


def _runtime_binding_sha256(settings: Settings) -> str:
    encoded = json.dumps(
        _runtime_binding(settings),
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _runtime_binding_allows_stop(record: dict[str, Any], settings: Settings) -> bool:
    """Allow additive binding upgrades while preserving every old state root."""

    existing = record.get("runtime_binding")
    if not isinstance(existing, dict):
        return False
    current = _runtime_binding(settings)
    required = set(current) - {"mcp_base_url"}
    return required <= set(existing) and all(
        existing[key] == current[key] for key in required
    )


def _selected_environment(keys: frozenset[str] | set[str]) -> dict[str, str]:
    return {key: os.environ[key] for key in sorted(keys) if key in os.environ}


def _material_agent_environment(settings: Settings) -> dict[str, str]:
    return {
        "MATERIAL_AGENT_PROJECT_ID": settings.project,
        "MATERIAL_AGENT_MCP_BASE_URL": settings.mcp_base_url,
        # Keep the virtual-environment entrypoint.  Resolving this uv-created
        # symlink yields the base CPython binary and drops the Gateway site-packages
        # when Hermes starts the MCP server.
        "MATERIAL_AGENT_PYTHON": str(GATEWAY_ENV / "bin" / "python"),
        "MATERIAL_AGENT_SMACT_WORKER_PYTHON": str(
            REPOSITORY_ROOT / ".venv-smact" / "bin" / "python"
        ),
        "MATERIAL_AGENT_WORKSPACE": str(settings.workspace),
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONUNBUFFERED": "1",
    }


def _runtime_env(settings: Settings) -> dict[str, str]:
    provider_family = settings.provider.casefold().split(":", 1)[0]
    allowed = set(_COMMON_CHILD_ENVIRONMENT_ALLOWLIST)
    allowed.update(_NETWORK_CHILD_ENVIRONMENT_ALLOWLIST)
    allowed.update({"API_SERVER_KEY", "HOME"})
    allowed.update(_PROVIDER_ENVIRONMENT_ALLOWLIST.get(provider_family, ()))
    environment = _selected_environment(allowed)
    environment.update(
        {
            "HERMES_HOME": str(settings.hermes_home),
            "HERMES_INFERENCE_MODEL": settings.model,
            "HERMES_INFERENCE_PROVIDER": settings.provider,
            **_material_agent_environment(settings),
        }
    )
    if (
        provider_family == "deepseek"
        and environment.get("DEEPSEEK_API_KEY")
        and not environment.get("MATERIAL_AGENT_LLM_API_KEY")
    ):
        environment["MATERIAL_AGENT_LLM_API_KEY"] = environment["DEEPSEEK_API_KEY"]
    return environment


def _build_env(settings: Settings) -> dict[str, str]:
    """Build/install environment without provider or API-server credentials."""

    allowed = set(_COMMON_CHILD_ENVIRONMENT_ALLOWLIST)
    allowed.add("HOME")
    environment = _selected_environment(allowed)
    environment.update(_material_agent_environment(settings))
    environment["HERMES_HOME"] = str(settings.hermes_home)
    return environment


def _worker_env(settings: Settings) -> dict[str, str]:
    """Build the bounded Gateway/MCP Hub worker environment."""

    allowed = set(_COMMON_CHILD_ENVIRONMENT_ALLOWLIST)
    allowed.update(_NETWORK_CHILD_ENVIRONMENT_ALLOWLIST)
    allowed.update(
        {
            "DEEPSEEK_API_KEY",
            "DEEPSEEK_BASE_URL",
            "MATERIALS_CROSSREF_CONTACT_EMAIL",
            "MATERIAL_AGENT_INSPIRATION_RAG_PROVIDER",
            "MATERIAL_AGENT_LLM_API_KEY",
            "MATERIAL_AGENT_LLM_BASE_URL",
            "MATERIAL_AGENT_LLM_MODEL",
            "MATERIAL_AGENT_ML_WORKER_PYTHON",
        }
    )
    environment = _selected_environment(allowed)
    environment.update(_material_agent_environment(settings))
    if (
        environment.get("MATERIAL_AGENT_INSPIRATION_RAG_PROVIDER", "")
        .strip()
        .casefold()
        == "deepseek"
    ):
        environment["MATERIAL_AGENT_LLM_MODEL"] = MATERIALS_RESEARCH_RAG_MODEL
    if environment.get("DEEPSEEK_API_KEY") and not environment.get(
        "MATERIAL_AGENT_LLM_API_KEY"
    ):
        environment["MATERIAL_AGENT_LLM_API_KEY"] = environment["DEEPSEEK_API_KEY"]
    return environment


def _monitor_env(settings: Settings) -> dict[str, str]:
    """Build a local-only monitor environment without network identity."""

    environment = _selected_environment(set(_COMMON_CHILD_ENVIRONMENT_ALLOWLIST))
    environment.update(_material_agent_environment(settings))
    return environment


def _require_secret_environment() -> None:
    api_server_key = os.environ.get("API_SERVER_KEY", "")
    if len(api_server_key) < 32 or api_server_key.isspace():
        raise ProductionRuntimeError(
            "API_SERVER_KEY must be supplied through the environment and contain at least 32 characters"
        )


def _bootstrap_unlocked() -> dict[str, Any]:
    node = _node_check()
    if not HERMES_LOCK.is_file():
        raise ProductionRuntimeError("Hermes release lock is unavailable")
    _run([sys.executable, str(SCRIPTS_ROOT / "bootstrap_gateway_runtime.py")])
    _run([sys.executable, str(SCRIPTS_ROOT / "bootstrap_runtime.py")])
    gateway_python = GATEWAY_ENV / "bin" / "python"
    hermes_python = HERMES_ENV / "bin" / "python"
    if _python_minor(gateway_python) != "3.11":
        raise ProductionRuntimeError("Gateway Python must be 3.11")
    if _python_minor(hermes_python) != "3.11":
        raise ProductionRuntimeError("Hermes Python must be 3.11")
    return {
        **node,
        "gateway_python": "3.11",
        "hermes_python": "3.11",
    }


def _bootstrap() -> dict[str, Any]:
    with _bootstrap_lock():
        return _bootstrap_unlocked()


def _profile_environment(settings: Settings) -> dict[str, str]:
    environment = _build_env(settings)
    environment["HERMES_HOME"] = str(settings.hermes_home)
    return environment


def _install_profile(settings: Settings) -> None:
    hermes = HERMES_ENV / "bin" / "hermes"
    if not hermes.is_file():
        raise ProductionRuntimeError("Hermes executable is unavailable")
    environment = _profile_environment(settings)
    _run(
        [
            str(hermes),
            "profile",
            "install",
            str(PROFILE_SOURCE),
            "--name",
            settings.profile,
            "--force",
            "--yes",
        ],
        env=environment,
    )
    for key, value in (
        ("model.provider", settings.provider),
        ("model.default", settings.model),
    ):
        _run(
            [str(hermes), "-p", settings.profile, "config", "set", key, value],
            env=environment,
        )
    _run(
        [str(GATEWAY_ENV / "bin" / "python"), str(SCRIPTS_ROOT / "verify_bundle.py")],
        env=environment,
    )


def _build_dashboard(settings: Settings) -> None:
    environment = _profile_environment(settings)
    environment["HERMES_HOME"] = str(settings.profile_root)
    command = (
        "from hermes_cli.main import PROJECT_ROOT,_build_web_ui; "
        "raise SystemExit(0 if _build_web_ui(PROJECT_ROOT / 'web', fatal=True) else 1)"
    )
    _run(
        [str(HERMES_ENV / "bin" / "python"), "-c", command],
        env=environment,
        timeout=1_800,
    )
    _repair_hermes_generated_drift()
    index = HERMES_SOURCE / "hermes_cli" / "web_dist" / "index.html"
    if not index.is_file() or index.stat().st_size == 0:
        raise ProductionRuntimeError("Hermes dashboard build did not produce index.html")


def _profile_probe(settings: Settings) -> dict[str, Any]:
    environment = _runtime_env(settings)
    environment["HERMES_HOME"] = str(settings.profile_root)
    output = _output(
        [
            str(HERMES_ENV / "bin" / "python"),
            str(SCRIPTS_ROOT / "probe_hermes_profile.py"),
            "--profile-root",
            str(settings.profile_root),
            "--expected-profile",
            settings.profile,
            "--expected-provider",
            settings.provider,
            "--expected-model",
            settings.model,
            "--expected-gateway-python",
            str(GATEWAY_ENV / "bin" / "python"),
            "--expected-workspace",
            str(settings.workspace),
            "--expected-project",
            settings.project,
            "--expected-mcp-base-url",
            settings.mcp_base_url,
            "--expected-source-profile",
            str(PROFILE_SOURCE),
        ],
        env=environment,
    )
    try:
        return json.loads(output)
    except json.JSONDecodeError as exc:
        raise ProductionRuntimeError("Hermes profile probe returned invalid JSON") from exc


def _gateway_probe(settings: Settings, *, live_crossref: bool) -> dict[str, Any]:
    environment = _worker_env(settings)
    command = [
        str(GATEWAY_ENV / "bin" / "python"),
        str(SCRIPTS_ROOT / "probe_gateway_runtime.py"),
        "--workspace",
        str(settings.workspace),
        "--project",
        settings.project,
    ]
    if live_crossref:
        command.append("--crossref")
    output = _output(command, env=environment)
    try:
        return json.loads(output)
    except json.JSONDecodeError as exc:
        raise ProductionRuntimeError("Gateway probe returned invalid JSON") from exc


def _preflight(settings: Settings, *, live_crossref: bool) -> dict[str, Any]:
    started = time.monotonic()
    _require_secret_environment()
    node = _node_check()
    if _python_minor(GATEWAY_ENV / "bin" / "python") != "3.11":
        raise ProductionRuntimeError("Gateway Python must be 3.11")
    if _python_minor(HERMES_ENV / "bin" / "python") != "3.11":
        raise ProductionRuntimeError("Hermes Python must be 3.11")
    _repair_hermes_generated_drift()
    hermes = _hermes_identity_check()
    profile = _profile_probe(settings)
    gateway = _gateway_probe(settings, live_crossref=live_crossref)
    payload = {
        "duration_ms": max(0, int((time.monotonic() - started) * 1_000)),
        "gateway": gateway,
        "hermes": hermes,
        "node": node,
        "profile": profile,
    }
    append_event(
        settings.event_log,
        event="preflight",
        outcome="success",
        duration_ms=payload["duration_ms"],
        details={
            "crossref": gateway["crossref"]["status"],
            "mcp_tool_count": len(gateway["tools"]),
            "profile": settings.profile,
            "provider": settings.provider,
        },
    )
    return payload


def _open_log(path: Path):
    try:
        rotate_bounded_file(
            path,
            max_bytes=MAX_RUNTIME_LOG_BYTES,
            generations=MAX_RUNTIME_LOG_GENERATIONS,
        )
    except (OSError, ProductionOpsError, ValueError) as exc:
        raise ProductionRuntimeError("runtime log rotation failed") from exc
    existed = path.exists()
    flags = os.O_APPEND | os.O_CREAT | os.O_WRONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(path, flags, 0o600)
    metadata = os.fstat(descriptor)
    if not stat.S_ISREG(metadata.st_mode):
        os.close(descriptor)
        raise ProductionRuntimeError("runtime log is not a regular file")
    os.fchmod(descriptor, 0o600)
    if not existed:
        fsync_directory(path.parent)
    return os.fdopen(descriptor, "ab", buffering=0)


def _spawn(command: list[str], *, env: dict[str, str], log_path: Path) -> dict[str, Any]:
    log = _open_log(log_path)
    try:
        process = subprocess.Popen(
            command,
            cwd=REPOSITORY_ROOT,
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
    finally:
        log.close()
    for _ in range(20):
        identity = process_identity(process.pid)
        if identity is not None:
            return identity
        if process.poll() is not None:
            break
        time.sleep(0.1)
    if process.poll() is None:
        try:
            os.killpg(process.pid, signal.SIGTERM)
            process.wait(timeout=2)
        except (OSError, ProcessLookupError, subprocess.TimeoutExpired):
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except (OSError, ProcessLookupError):
                pass
    raise ProductionRuntimeError("runtime process exited before identity capture")


def _terminate(
    record: dict[str, Any] | None,
    *,
    timeout_seconds: float = 10.0,
) -> bool:
    if timeout_seconds <= 0:
        raise ValueError("process termination timeout must be positive")
    if not owned_process_alive(record):
        return False
    assert record is not None
    pid = int(record["pid"])
    descendants = _owned_descendants(pid)
    for child in descendants:
        _signal_owned_process(child, signal.SIGTERM)
    _signal_owned_process(record, signal.SIGTERM)
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if not owned_process_alive(record) and not any(
            owned_process_alive(child) for child in descendants
        ):
            return True
        time.sleep(0.1)
    for child in descendants:
        _signal_owned_process(child, signal.SIGKILL)
    _signal_owned_process(record, signal.SIGKILL)
    return not owned_process_alive(record) and not any(
        owned_process_alive(child) for child in descendants
    )


def _owned_descendants(root_pid: int) -> tuple[dict[str, Any], ...]:
    """Capture exact descendant identities before a parent can reparent them."""

    try:
        completed = subprocess.run(
            ["ps", "-axo", "pid=,ppid="],
            check=True,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return ()
    children: dict[int, list[int]] = {}
    for line in completed.stdout.splitlines():
        fields = line.split()
        if len(fields) != 2:
            continue
        try:
            child_pid, parent_pid = (int(value) for value in fields)
        except ValueError:
            continue
        children.setdefault(parent_pid, []).append(child_pid)
    pending: list[tuple[int, int]] = [(root_pid, 0)]
    discovered: list[tuple[int, int]] = []
    while pending:
        parent, depth = pending.pop()
        for child_pid in children.get(parent, ()):
            discovered.append((child_pid, depth + 1))
            pending.append((child_pid, depth + 1))
    identities: list[tuple[int, dict[str, Any]]] = []
    for child_pid, depth in discovered:
        identity = process_identity(child_pid)
        if identity is not None:
            identities.append((depth, identity))
    return tuple(
        identity
        for _, identity in sorted(
            identities, key=lambda item: item[0], reverse=True
        )
    )


def _signal_owned_process(record: dict[str, Any], signum: int) -> None:
    if not owned_process_alive(record):
        return
    pid = int(record["pid"])
    try:
        process_group = os.getpgid(pid)
        if process_group == pid:
            os.killpg(process_group, signum)
        else:
            os.kill(pid, signum)
    except (OSError, ProcessLookupError):
        return


def _wait_http(url: str, *, process: dict[str, Any], timeout_seconds: int) -> bool:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if not owned_process_alive(process):
            return False
        if probe_http_json(url, timeout_seconds=1.0)["ok"]:
            return True
        time.sleep(0.5)
    return False


def _worker_command(settings: Settings) -> list[str]:
    command = [
        str(GATEWAY_ENV / "bin" / "python"),
        "-m",
        "material_agent.integration.queued_gateway",
        "--workspace",
        str(settings.workspace),
        "--project",
        settings.project,
        "--poll-seconds",
        str(WORKER_POLL_SECONDS),
        "--lease-seconds",
        str(WORKER_LEASE_SECONDS),
        "--mcp-host",
        settings.mcp_host,
        "--mcp-port",
        str(settings.mcp_port),
        "--smact-worker-python",
        str(REPOSITORY_ROOT / ".venv-smact" / "bin" / "python"),
    ]
    worker_python = os.environ.get("MATERIAL_AGENT_ML_WORKER_PYTHON", "").strip()
    if not worker_python:
        default_worker = REPOSITORY_ROOT / ".venv-agent02" / "bin" / "python"
        if default_worker.is_file():
            worker_python = str(default_worker)
    if worker_python:
        selected = Path(worker_python)
        if not selected.is_absolute() or not selected.is_file():
            raise ProductionRuntimeError(
                "MATERIAL_AGENT_ML_WORKER_PYTHON must be an existing absolute file"
            )
        command.extend(("--chgnet-worker-python", str(selected)))
    return command


def _monitor_command(settings: Settings, *, binding_sha256: str) -> list[str]:
    return [
        str(GATEWAY_ENV / "bin" / "python"),
        str(SCRIPTS_ROOT / "production_monitor.py"),
        "--host",
        settings.monitor_host,
        "--port",
        str(settings.monitor_port),
        "--dashboard-host",
        settings.dashboard_host,
        "--dashboard-port",
        str(settings.dashboard_port),
        "--event-log",
        str(settings.event_log),
        "--gateway-database",
        str(settings.gateway_database),
        "--queue-database",
        str(settings.approval_database),
        "--process-file",
        str(settings.process_file),
        "--runtime-binding-sha256",
        binding_sha256,
    ]


def _wait_worker_ready(
    settings: Settings,
    *,
    process: dict[str, Any],
    timeout_seconds: int,
) -> bool:
    """Require a stable owned worker and the shared approval/outbox DB."""

    deadline = time.monotonic() + timeout_seconds
    consecutive_ready_checks = 0
    while time.monotonic() < deadline:
        if not owned_process_alive(process):
            return False
        queue = gateway_queue_snapshot(settings.approval_database)
        hub = probe_http_json(
            f"{settings.mcp_base_url}/healthz", timeout_seconds=1.0
        )
        if queue["integrity"] and hub["ok"]:
            consecutive_ready_checks += 1
            if consecutive_ready_checks >= 3:
                return True
        else:
            consecutive_ready_checks = 0
        time.sleep(0.25)
    return False


def _rollback_started_processes(
    settings: Settings,
    process_record: dict[str, Any],
) -> None:
    """Best-effort inverse-order rollback without masking the startup error."""

    processes = process_record.get("processes", {})
    stopped: dict[str, bool] = {}
    for name in ("monitor", "dashboard", "worker"):
        try:
            stopped[name] = _terminate(
                processes.get(name),
                timeout_seconds=(
                    WORKER_STOP_TIMEOUT_SECONDS
                    if name == "worker"
                    else LOCAL_PROCESS_STOP_TIMEOUT_SECONDS
                ),
            )
        except (OSError, ProductionRuntimeError, ValueError):
            stopped[name] = False
    stopped_record = {
        "processes": {},
        "profile": settings.profile,
        "runtime_binding": _runtime_binding(settings),
        "runtime_binding_sha256": _runtime_binding_sha256(settings),
        "schema_version": PID_SCHEMA_VERSION,
        "startup_rollback_at": utc_now(),
    }
    try:
        write_json_atomic(settings.process_file, stopped_record)
    except (OSError, ProductionOpsError):
        pass
    try:
        append_event(
            settings.event_log,
            event="start",
            outcome="rollback",
            details={f"{name}_stopped": value for name, value in stopped.items()},
        )
    except (OSError, ProductionOpsError):
        pass


def _start(
    settings: Settings,
    *,
    completed_preflight: dict[str, Any] | None = None,
    require_live_crossref: bool = False,
) -> dict[str, Any]:
    existing = read_json(settings.process_file)
    existing_processes = (existing or {}).get("processes", {})
    if any(
        owned_process_alive(existing_processes.get(name))
        for name in ("dashboard", "monitor", "worker")
    ):
        status = _status(settings)
        if status["running"] and status["local_ready"]:
            preflight = _preflight(
                settings,
                live_crossref=require_live_crossref,
            )
            result: dict[str, Any] = {
                "already_running": True,
                "health": status,
                "preflight": preflight,
            }
            return result
        raise ProductionRuntimeError(
            "an owned runtime process is present but the deployment is not ready; stop it first"
        )

    preflight = completed_preflight or _preflight(
        settings,
        live_crossref=require_live_crossref,
    )
    environment = _runtime_env(settings)
    worker_environment = _worker_env(settings)
    monitor_environment = _monitor_env(settings)
    binding = _runtime_binding(settings)
    binding_sha256 = _runtime_binding_sha256(settings)
    process_record = {
        "created_at": utc_now(),
        "dashboard_host": settings.dashboard_host,
        "dashboard_port": settings.dashboard_port,
        "monitor_host": settings.monitor_host,
        "monitor_port": settings.monitor_port,
        "processes": {},
        "profile": settings.profile,
        "runtime_binding": binding,
        "runtime_binding_sha256": binding_sha256,
        "schema_version": PID_SCHEMA_VERSION,
    }
    try:
        worker = _spawn(
            _worker_command(settings),
            env=worker_environment,
            log_path=settings.worker_log,
        )
        process_record["processes"]["worker"] = worker
        write_json_atomic(settings.process_file, process_record)
        if not _wait_worker_ready(settings, process=worker, timeout_seconds=20):
            raise ProductionRuntimeError(
                "queued Gateway worker did not become ready on the shared approval database"
            )

        dashboard = _spawn(
            [
                str(HERMES_ENV / "bin" / "python"),
                str(SCRIPTS_ROOT / "managed_dashboard.py"),
                "-p",
                settings.profile,
                "dashboard",
                "--isolated",
                "--no-open",
                "--skip-build",
                "--host",
                settings.dashboard_host,
                "--port",
                str(settings.dashboard_port),
            ],
            env=environment,
            log_path=settings.dashboard_log,
        )
        process_record["processes"]["dashboard"] = dashboard
        write_json_atomic(settings.process_file, process_record)
        dashboard_url = (
            f"http://{settings.dashboard_host}:{settings.dashboard_port}/api/health"
        )
        if not _wait_http(dashboard_url, process=dashboard, timeout_seconds=60):
            raise ProductionRuntimeError("Hermes dashboard did not become healthy")

        monitor = _spawn(
            _monitor_command(settings, binding_sha256=binding_sha256),
            env=monitor_environment,
            log_path=settings.monitor_log,
        )
        process_record["processes"]["monitor"] = monitor
        write_json_atomic(settings.process_file, process_record)
        monitor_url = f"http://{settings.monitor_host}:{settings.monitor_port}/healthz"
        if not _wait_http(monitor_url, process=monitor, timeout_seconds=20):
            raise ProductionRuntimeError("operations monitor did not become healthy")
        ready_url = f"http://{settings.monitor_host}:{settings.monitor_port}/readyz"
        if not _wait_http(ready_url, process=monitor, timeout_seconds=20):
            raise ProductionRuntimeError("local worker and queue readiness did not pass")
        health = probe_http_json(monitor_url)["value"]
        append_event(
            settings.event_log,
            event="start",
            outcome="success",
            duration_ms=preflight["duration_ms"],
            details={
                "crossref": preflight["gateway"]["crossref"]["status"],
                "dashboard_port": settings.dashboard_port,
                "monitor_port": settings.monitor_port,
                "runtime_binding_sha256": binding_sha256,
                "worker_pid": worker["pid"],
            },
        )
    except Exception:
        _rollback_started_processes(settings, process_record)
        raise
    return {
        "already_running": False,
        "dashboard_url": (
            f"http://{settings.dashboard_host}:{settings.dashboard_port}/"
            f"?profile={settings.profile}"
        ),
        "health": health,
        "metrics_url": f"http://{settings.monitor_host}:{settings.monitor_port}/metrics",
        "preflight": preflight,
        "worker": {
            "command_sha256": worker["command_sha256"],
            "pid": worker["pid"],
            "start_marker": worker["start_marker"],
        },
    }


def _stop(settings: Settings) -> dict[str, Any]:
    record = read_json(settings.process_file)
    if record is None:
        return {
            "dashboard_stopped": False,
            "monitor_stopped": False,
            "worker_stopped": False,
        }
    if record.get("schema_version") != PID_SCHEMA_VERSION:
        raise ProductionRuntimeError("runtime process file schema is unknown")
    if (
        record.get("runtime_binding_sha256") != _runtime_binding_sha256(settings)
        and not _runtime_binding_allows_stop(record, settings)
    ):
        raise ProductionRuntimeError("runtime process file is bound to different state roots")
    processes = record.get("processes", {})
    dashboard = _terminate(
        processes.get("dashboard"),
        timeout_seconds=LOCAL_PROCESS_STOP_TIMEOUT_SECONDS,
    )
    worker = _terminate(
        processes.get("worker"),
        timeout_seconds=WORKER_STOP_TIMEOUT_SECONDS,
    )
    monitor = _terminate(
        processes.get("monitor"),
        timeout_seconds=LOCAL_PROCESS_STOP_TIMEOUT_SECONDS,
    )
    write_json_atomic(
        settings.process_file,
        {
            "processes": {},
            "profile": settings.profile,
            "runtime_binding": _runtime_binding(settings),
            "runtime_binding_sha256": _runtime_binding_sha256(settings),
            "schema_version": PID_SCHEMA_VERSION,
            "stopped_at": utc_now(),
        },
    )
    # Persist the stopped ownership record before checkout cleanup so even an
    # unsafe, non-lockfile edit cannot leave dead processes recorded as live.
    repaired_lockfiles = _repair_hermes_generated_drift()
    append_event(
        settings.event_log,
        event="stop",
        outcome="success",
        details={
            "dashboard_stopped": dashboard,
            "monitor_stopped": monitor,
            "repaired_lockfile_count": len(repaired_lockfiles),
            "worker_stopped": worker,
        },
    )
    return {
        "dashboard_stopped": dashboard,
        "monitor_stopped": monitor,
        "worker_stopped": worker,
    }


def _ops_snapshot(settings: Settings) -> dict[str, Any]:
    return metrics_snapshot(
        event_log=settings.event_log,
        gateway_database=settings.gateway_database,
        queue_database=settings.approval_database,
        process_record=read_json(settings.process_file),
        expected_runtime_binding_sha256=_runtime_binding_sha256(settings),
    )


def _status(settings: Settings) -> dict[str, Any]:
    snapshot = _ops_snapshot(settings)
    dashboard_http = probe_http_json(
        f"http://{settings.dashboard_host}:{settings.dashboard_port}/api/health",
        timeout_seconds=1.0,
    )["ok"]
    monitor_health = probe_http_json(
        f"http://{settings.monitor_host}:{settings.monitor_port}/healthz",
        timeout_seconds=1.0,
    )["ok"]
    monitor_ready = probe_http_json(
        f"http://{settings.monitor_host}:{settings.monitor_port}/readyz",
        timeout_seconds=1.0,
    )["ok"]
    mcp_hub_http = probe_http_json(
        f"{settings.mcp_base_url}/healthz", timeout_seconds=1.0
    )["ok"]
    running = bool(
        snapshot["dashboard"]["up"]
        and snapshot["monitor"]["up"]
        and snapshot["worker"]["up"]
        and dashboard_http
        and monitor_health
        and mcp_hub_http
    )
    return {
        "dashboard_http": dashboard_http,
        "dashboard_process": snapshot["dashboard"]["up"],
        "gateway_database_integrity": snapshot["database"]["integrity"],
        "local_ready": bool(running and monitor_ready and snapshot["readiness"]["ok"]),
        "monitor_http": monitor_health,
        "monitor_process": snapshot["monitor"]["up"],
        "monitor_ready": monitor_ready,
        "mcp_hub_http": mcp_hub_http,
        "owned_processes_present": bool(
            snapshot["dashboard"]["up"]
            or snapshot["monitor"]["up"]
            or snapshot["worker"]["up"]
        ),
        "queue": snapshot["queue"],
        "readiness_reasons": snapshot["readiness"]["reasons"],
        "running": running,
        "runtime_binding_match": snapshot["runtime_binding_match"],
        "worker_identity": snapshot["worker"]["recorded_identity"],
        "worker_pid": snapshot["worker"]["pid"],
        "worker_process": snapshot["worker"]["up"],
    }


def _health(settings: Settings) -> dict[str, Any]:
    preflight = _preflight(settings, live_crossref=False)
    status = _status(settings)
    local_ready = bool(status["local_ready"])
    external_crossref_ready = False
    try:
        external_probe = _gateway_probe(settings, live_crossref=True)
        external_crossref_ready = external_probe["crossref"]["status"] == "ok"
    except ProductionRuntimeError:
        external_probe = {"crossref": {"status": "unavailable"}}
    append_event(
        settings.event_log,
        event="health",
        outcome="success" if local_ready else "failure",
        duration_ms=preflight["duration_ms"],
        details={
            "external_crossref_ready": external_crossref_ready,
            "local_ready": local_ready,
            "runtime": status,
        },
    )
    return {
        "external_crossref": external_probe["crossref"],
        "external_crossref_ready": external_crossref_ready,
        "healthy": local_ready,
        "local_ready": local_ready,
        "preflight": preflight,
        "runtime": status,
    }


def _metrics(settings: Settings, *, as_json: bool) -> str:
    snapshot = _ops_snapshot(settings)
    dashboard_http = probe_http_json(
        f"http://{settings.dashboard_host}:{settings.dashboard_port}/api/health",
        timeout_seconds=1.0,
    )["ok"]
    if not dashboard_http:
        reasons = list(snapshot["readiness"]["reasons"])
        if "DASHBOARD_HTTP_UNAVAILABLE" not in reasons:
            reasons.append("DASHBOARD_HTTP_UNAVAILABLE")
        snapshot["readiness"] = {
            **snapshot["readiness"],
            "ok": False,
            "reasons": reasons,
        }
    snapshot["dashboard_http"] = dashboard_http
    if as_json:
        return json.dumps(snapshot, sort_keys=True, separators=(",", ":"))
    return prometheus_metrics(snapshot).rstrip()


_ACK_FAILURE_SCRIPT = """
import json
import sys
from pathlib import Path
from material_agent.gateway.job_queue import SqliteGatewayJobQueue

request = json.load(sys.stdin)
queue = SqliteGatewayJobQueue(Path(request["database"]))
try:
    job = queue.acknowledge_terminal_failure(
        job_id=request["job_id"],
        expected_failure_code=request["expected_code"],
        actor=request["actor"],
        reason=request["reason"],
    )
finally:
    queue.close()
print(json.dumps({
    "acknowledged_at_ms": job.failure_acknowledged_at_ms,
    "actor": job.failure_acknowledged_by,
    "job_id": job.job_id,
    "status": job.status.value,
}, sort_keys=True, separators=(",", ":")))
""".strip()


def _ack_failure(
    settings: Settings,
    *,
    job_id: str,
    expected_code: str,
    actor: str,
    reason: str,
) -> dict[str, Any]:
    """Acknowledge one exact terminal failure without exposing its reason."""

    if any(
        len(value.encode("utf-8")) > 4_096
        for value in (job_id, expected_code, actor, reason)
    ):
        raise ProductionRuntimeError("failure acknowledgement input is too large")
    queue = gateway_queue_snapshot(settings.approval_database)
    if not queue["integrity"]:
        raise ProductionRuntimeError("approval/outbox database is unavailable")
    request = json.dumps(
        {
            "actor": actor,
            "database": str(settings.approval_database),
            "expected_code": expected_code,
            "job_id": job_id,
            "reason": reason,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    try:
        completed = subprocess.run(
            [
                str(GATEWAY_ENV / "bin" / "python"),
                "-c",
                _ACK_FAILURE_SCRIPT,
            ],
            cwd=REPOSITORY_ROOT,
            env=_monitor_env(settings),
            input=request,
            check=True,
            capture_output=True,
            text=True,
            timeout=30,
        )
        result = json.loads(completed.stdout)
        if (
            not isinstance(result, dict)
            or result.get("job_id") != job_id
            or result.get("actor") != actor
            or result.get("status") != "FAILED"
            or not isinstance(result.get("acknowledged_at_ms"), int)
        ):
            raise ProductionRuntimeError(
                "failure acknowledgement returned an invalid result"
            )
    except (
        OSError,
        subprocess.CalledProcessError,
        subprocess.TimeoutExpired,
        json.JSONDecodeError,
        ProductionRuntimeError,
    ) as exc:
        try:
            append_event(
                settings.event_log,
                event="ack_failure",
                outcome="failure",
                details={
                    "actor": actor,
                    "expected_code": expected_code,
                    "job_id": job_id,
                },
            )
        except (OSError, ProductionOpsError):
            pass
        raise ProductionRuntimeError("failure acknowledgement was rejected") from exc
    append_event(
        settings.event_log,
        event="ack_failure",
        outcome="success",
        details={
            "acknowledged_at_ms": result["acknowledged_at_ms"],
            "actor": actor,
            "expected_code": expected_code,
            "job_id": job_id,
        },
    )
    return result


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Deploy and operate the bounded Materials Inspiration profile."
    )
    parser.add_argument(
        "--workspace", default=os.environ.get("MATERIAL_AGENT_WORKSPACE")
    )
    parser.add_argument(
        "--project",
        default=os.environ.get("MATERIAL_AGENT_PROJECT_ID", "materials-inspiration"),
    )
    parser.add_argument("--profile", default="materials-inspiration")
    parser.add_argument("--provider", default=os.environ.get("MATERIALS_HERMES_PROVIDER"))
    parser.add_argument("--model", default=os.environ.get("MATERIALS_HERMES_MODEL"))
    parser.add_argument("--dashboard-host", default="127.0.0.1")
    parser.add_argument("--dashboard-port", type=int, default=9119)
    parser.add_argument("--monitor-host", default="127.0.0.1")
    parser.add_argument("--monitor-port", type=int, default=9120)
    parser.add_argument("--hermes-home")
    parser.add_argument("--ops-dir")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("bootstrap")
    deploy = subparsers.add_parser("deploy")
    deploy.add_argument("--prepare-only", action="store_true")
    deploy.add_argument(
        "--require-live-crossref",
        action="store_true",
        help="fail deployment unless the bounded Crossref smoke probe succeeds",
    )
    start = subparsers.add_parser("start")
    start.add_argument(
        "--require-live-crossref",
        action="store_true",
        help="fail startup unless the bounded Crossref smoke probe succeeds",
    )
    subparsers.add_parser("stop")
    subparsers.add_parser("status")
    subparsers.add_parser("health")
    metrics = subparsers.add_parser("metrics")
    metrics.add_argument("--json", action="store_true")
    acknowledge = subparsers.add_parser("ack-failure")
    acknowledge.add_argument("--job-id", required=True)
    acknowledge.add_argument("--expected-code", required=True)
    acknowledge.add_argument("--actor", required=True)
    acknowledge.add_argument("--reason", required=True)
    return parser


def _execute_command(args: argparse.Namespace, settings: Settings) -> int:
    if args.command == "deploy":
        if _status(settings)["owned_processes_present"]:
            raise ProductionRuntimeError("stop the owned deployment before redeploying")
        with _bootstrap_lock():
            bootstrap = _bootstrap_unlocked()
            _require_secret_environment()
            _install_profile(settings)
            _build_dashboard(settings)
            prepared = _preflight(
                settings,
                live_crossref=bool(args.require_live_crossref),
            )
            result: dict[str, Any] = {
                "bootstrap": bootstrap,
                "prepared": prepared,
                "profile_root": str(settings.profile_root),
            }
            if not args.prepare_only:
                result["runtime"] = _start(
                    settings,
                    completed_preflight=prepared,
                    require_live_crossref=bool(args.require_live_crossref),
                )
        append_event(
            settings.event_log,
            event="deploy",
            outcome="success",
            details={
                "prepare_only": bool(args.prepare_only),
                "require_live_crossref": bool(args.require_live_crossref),
            },
        )
        print(json.dumps(result, sort_keys=True, separators=(",", ":")))
        return 0
    if args.command == "start":
        print(
            json.dumps(
                _start(
                    settings,
                    require_live_crossref=bool(args.require_live_crossref),
                ),
                sort_keys=True,
                separators=(",", ":"),
            )
        )
        return 0
    if args.command == "stop":
        print(json.dumps(_stop(settings), sort_keys=True, separators=(",", ":")))
        return 0
    if args.command == "status":
        print(json.dumps(_status(settings), sort_keys=True, separators=(",", ":")))
        return 0
    if args.command == "health":
        result = _health(settings)
        print(json.dumps(result, sort_keys=True, separators=(",", ":")))
        return 0 if result["healthy"] else 2
    if args.command == "metrics":
        print(_metrics(settings, as_json=args.json))
        return 0
    if args.command == "ack-failure":
        result = _ack_failure(
            settings,
            job_id=args.job_id,
            expected_code=args.expected_code,
            actor=args.actor,
            reason=args.reason,
        )
        print(json.dumps(result, sort_keys=True, separators=(",", ":")))
        return 0
    raise ProductionRuntimeError("unknown production lifecycle command")


def main() -> int:
    args = _parser().parse_args()
    if args.command == "bootstrap":
        print(json.dumps(_bootstrap(), sort_keys=True, separators=(",", ":")))
        return 0
    require_model = args.command in {"deploy", "start", "health"}
    settings = Settings.from_args(args, require_model=require_model)
    with _lifecycle_lock(settings):
        return _execute_command(args, settings)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ProductionOpsError, ProductionRuntimeError) as exc:
        print(f"production lifecycle failed: {exc}", file=sys.stderr)
        raise SystemExit(2) from exc
