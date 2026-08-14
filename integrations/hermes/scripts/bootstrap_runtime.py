#!/usr/bin/env python3
"""Install the pinned Hermes runtime into an isolated repository-local venv."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path

from managed_checkout import ManagedCheckoutError, repair_generated_package_locks


REPO_ROOT = Path(__file__).resolve().parents[3]
INTEGRATION_ROOT = REPO_ROOT / "integrations" / "hermes"
LOCK_PATH = INTEGRATION_ROOT / "hermes.lock.json"
SOURCE_ROOT = REPO_ROOT / ".external" / "hermes-agent"
ENV_ROOT = REPO_ROOT / ".venv-hermes"


def parse_runtime_version(value: str) -> tuple[int, int, int]:
    match = re.fullmatch(r"v?(\d+)\.(\d+)\.(\d+)(?:[-+].*)?", value.strip())
    if match is None:
        raise RuntimeError("runtime version has an unexpected format")
    return tuple(int(part) for part in match.groups())  # type: ignore[return-value]


def ensure_node(minimum: str) -> None:
    """Fail before checkout/install when the dashboard Node floor is unmet."""

    for executable in ("node", "npm"):
        if shutil.which(executable) is None:
            raise RuntimeError(f"required executable is unavailable: {executable}")
    actual = output("node", "--version")
    if parse_runtime_version(actual) < parse_runtime_version(minimum):
        raise RuntimeError(
            f"Hermes dashboard requires Node >= {minimum}, got {actual.lstrip('v')}"
        )
    parse_runtime_version(output("npm", "--version"))


def run(*args: str, cwd: Path | None = None, env: dict[str, str] | None = None) -> None:
    subprocess.run(args, cwd=cwd, env=env, check=True)


def output(*args: str, cwd: Path | None = None) -> str:
    return subprocess.check_output(args, cwd=cwd, text=True).strip()


def ensure_source(repository: str, tag: str, commit: str, version: str) -> None:
    if SOURCE_ROOT.exists() and not (SOURCE_ROOT / ".git").is_dir():
        raise RuntimeError(f"managed source path is not a Git checkout: {SOURCE_ROOT}")
    if not SOURCE_ROOT.exists():
        SOURCE_ROOT.parent.mkdir(parents=True, exist_ok=True)
        SOURCE_ROOT.mkdir()
        run("git", "init", "--quiet", cwd=SOURCE_ROOT)
        run("git", "remote", "add", "origin", repository, cwd=SOURCE_ROOT)
    origin = output("git", "remote", "get-url", "origin", cwd=SOURCE_ROOT)
    if origin != repository:
        raise RuntimeError(f"unexpected Hermes origin: {origin}")
    try:
        current = output("git", "rev-parse", "HEAD", cwd=SOURCE_ROOT)
    except subprocess.CalledProcessError:
        current = ""
    dirty = output("git", "status", "--porcelain", cwd=SOURCE_ROOT)
    if dirty:
        if current != commit:
            raise RuntimeError(
                "Hermes source checkout is dirty and is not at the pinned commit"
            )
        try:
            repaired = repair_generated_package_locks(
                SOURCE_ROOT,
                expected_commit=commit,
            )
        except ManagedCheckoutError as exc:
            raise RuntimeError(str(exc)) from exc
        if repaired:
            print("Restored npm-generated Hermes lockfile drift: " + ", ".join(repaired))
    run(
        "git",
        "fetch",
        "--depth",
        "1",
        "origin",
        f"refs/tags/{tag}:refs/tags/{tag}",
        cwd=SOURCE_ROOT,
    )
    tagged_commit = output("git", "rev-parse", f"{tag}^{{commit}}", cwd=SOURCE_ROOT)
    if tagged_commit != commit:
        raise RuntimeError(
            f"Hermes tag mismatch: expected {commit}, got {tagged_commit}"
        )
    if current != commit:
        run("git", "checkout", "--detach", commit, cwd=SOURCE_ROOT)
    resolved = output("git", "rev-parse", "HEAD", cwd=SOURCE_ROOT)
    if resolved != commit:
        raise RuntimeError(f"Hermes commit mismatch: expected {commit}, got {resolved}")
    metadata = tomllib.loads((SOURCE_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    installed_version = metadata.get("project", {}).get("version")
    if installed_version != version:
        raise RuntimeError(
            f"Hermes package version mismatch: expected {version}, got {installed_version}"
        )


def main() -> int:
    lock = json.loads(LOCK_PATH.read_text(encoding="utf-8"))
    for executable in ("git", "uv"):
        if shutil.which(executable) is None:
            raise RuntimeError(f"required executable is unavailable: {executable}")
    ensure_node(lock["node_minimum"])
    ensure_source(
        lock["repository"],
        lock["release_tag"],
        lock["resolved_commit"],
        lock["package_version"],
    )
    sync_env = os.environ.copy()
    sync_env["UV_PROJECT_ENVIRONMENT"] = str(ENV_ROOT)
    command = [
        "uv",
        "sync",
        "--project",
        str(SOURCE_ROOT),
        "--locked",
        "--python",
        lock["python"],
    ]
    for extra in lock["uv_extras"]:
        command.extend(("--extra", extra))
    run(*command, env=sync_env)
    hermes_python = ENV_ROOT / "bin" / "python"
    run(
        str(hermes_python),
        "-c",
        (
            "import aiohttp; "
            "from gateway.platforms.api_server import APIServerAdapter; "
            "from plugins.platforms.slack.adapter import SlackAdapter"
        ),
    )
    hermes = ENV_ROOT / "bin" / "hermes"
    if not hermes.is_file():
        raise RuntimeError(f"Hermes executable was not installed: {hermes}")
    run(str(hermes), "--version")
    print(f"Hermes {lock['package_version']} is pinned at {lock['resolved_commit']}")
    print(f"Runtime: {ENV_ROOT}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (RuntimeError, ManagedCheckoutError, subprocess.CalledProcessError) as exc:
        print(f"bootstrap failed: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
