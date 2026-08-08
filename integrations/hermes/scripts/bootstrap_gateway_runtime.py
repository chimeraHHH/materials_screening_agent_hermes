#!/usr/bin/env python3
"""Install the Materials MCP gateway in an isolated, pinned environment."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
ENV_ROOT = REPO_ROOT / ".venv-gateway"
LOCK_PATH = REPO_ROOT / "integrations" / "hermes" / "gateway-requirements.lock"


def run(*command: str) -> None:
    subprocess.run(command, cwd=REPO_ROOT, check=True)


def main() -> int:
    if not LOCK_PATH.is_file():
        raise RuntimeError(f"gateway lock is missing: {LOCK_PATH}")
    python = ENV_ROOT / "bin" / "python"
    if not python.is_file():
        run("uv", "venv", "--python", "3.11", str(ENV_ROOT))
    version = subprocess.run(
        (
            str(python),
            "-c",
            "import sys; print(f'{sys.version_info[0]}.{sys.version_info[1]}')",
        ),
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    if version != "3.11":
        raise RuntimeError(f"gateway runtime must use Python 3.11, got {version}")
    run(
        "uv",
        "pip",
        "sync",
        "--python",
        str(python),
        str(LOCK_PATH),
    )
    run(
        "uv",
        "pip",
        "install",
        "--python",
        str(python),
        "--no-deps",
        "--editable",
        ".",
    )
    completed = subprocess.run(
        (str(python), "-m", "material_agent.integration.mcp_server", "--manifest"),
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    manifest = json.loads(completed.stdout)
    names = tuple(item["name"] for item in manifest)
    expected = (
        "materials_inspiration_run",
        "materials_run_get",
        "materials_run_act",
        "materials_result_get",
    )
    if names != expected:
        raise RuntimeError("Materials Gateway tool allowlist verification failed")
    print(f"Materials Gateway runtime ready: {python}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
