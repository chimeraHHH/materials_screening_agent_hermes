#!/usr/bin/env python3
"""Fail closed when the committed Hermes profile and Skill drift apart."""

from __future__ import annotations

import hashlib
import json
import re
import sys
from pathlib import Path

import yaml


REPO_ROOT = Path(__file__).resolve().parents[3]
HERMES_ROOT = REPO_ROOT / "integrations" / "hermes"
PROFILE_ROOT = HERMES_ROOT / "profiles" / "materials-inspiration"
SKILL_PATH = PROFILE_ROOT / "skills" / "materials-inspiration" / "SKILL.md"
SOUL_PATH = PROFILE_ROOT / "SOUL.md"
EXPECTED_TOOLS = [
    "materials_inspiration_run",
    "materials_run_get",
    "materials_run_act",
    "materials_result_get",
]


def expected_soul(skill_text: str) -> str:
    match = re.match(r"\A---\n.*?\n---\n\n?", skill_text, flags=re.DOTALL)
    if match is None:
        raise ValueError("Skill frontmatter is malformed")
    digest = hashlib.sha256(skill_text.encode("utf-8")).hexdigest()
    body = skill_text[match.end() :].rstrip()
    body = body.replace(
        "Read [the Gateway contract](references/gateway-contract.md) before the first tool\n"
        "call when tool arguments, states, or evidence boundaries are unclear.",
        "Read the bundled Gateway contract before the first tool call when tool arguments,\n"
        "states, or evidence boundaries are unclear.",
    )
    return (
        "<!-- GENERATED FROM skills/materials-inspiration/SKILL.md; "
        f"source-sha256: {digest} -->\n\n{body}\n"
    )


def verify() -> None:
    lock = json.loads((HERMES_ROOT / "hermes.lock.json").read_text(encoding="utf-8"))
    if lock["package_version"] != "0.20.0" or lock["release_tag"] != "v2026.8.3":
        raise ValueError("Hermes release lock changed without a compatibility update")

    config = yaml.safe_load(
        (PROFILE_ROOT / "config.yaml").read_text(encoding="utf-8")
    )
    if config.get("_config_version") != 33:
        raise ValueError("Hermes config schema must remain at v33 for the pinned runtime")
    expected_platforms = {"cli": ["materials"], "api_server": ["materials"]}
    if config.get("platform_toolsets") != expected_platforms:
        raise ValueError("profile must expose only the raw materials MCP toolset")
    if "skills" not in config.get("agent", {}).get("disabled_toolsets", []):
        raise ValueError("native skill management must remain disabled")
    server = config.get("mcp_servers", {}).get("materials", {})
    if server.get("tools", {}).get("include") != EXPECTED_TOOLS:
        raise ValueError("materials MCP tool allowlist drifted")
    if server.get("tools", {}).get("resources") is not False:
        raise ValueError("MCP resources must remain disabled")
    if server.get("tools", {}).get("prompts") is not False:
        raise ValueError("MCP prompts must remain disabled")
    if server.get("supports_parallel_tool_calls") is not False:
        raise ValueError("parallel Materials Gateway calls must remain disabled")
    if config.get("gateway", {}).get("api_server", {}).get("host") != "127.0.0.1":
        raise ValueError("development API server must remain loopback-bound")

    skill_text = SKILL_PATH.read_text(encoding="utf-8")
    if SOUL_PATH.read_text(encoding="utf-8") != expected_soul(skill_text):
        raise ValueError("SOUL.md does not exactly mirror the versioned Skill")


def main() -> int:
    try:
        verify()
    except (KeyError, OSError, TypeError, ValueError, yaml.YAMLError) as exc:
        print(f"Hermes bundle invalid: {exc}", file=sys.stderr)
        return 1
    print("Hermes bundle valid")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
