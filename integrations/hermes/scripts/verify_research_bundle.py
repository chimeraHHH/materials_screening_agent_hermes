#!/usr/bin/env python3
"""Validate the isolated generic materials-research Hermes profile."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[3]
HERMES_ROOT = REPO_ROOT / "integrations" / "hermes"
PROFILE_ROOT = HERMES_ROOT / "profiles" / "materials-inspiration-research"
SKILL_PATH = (
    PROFILE_ROOT
    / "skills"
    / "materials-inspiration-research"
    / "SKILL.md"
)
SOUL_PATH = PROFILE_ROOT / "SOUL.md"
EXPECTED_TOOLS = ["materials_generic_research_run"]


def expected_soul(skill_text: str) -> str:
    match = re.match(r"\A---\n.*?\n---\n\n?", skill_text, flags=re.DOTALL)
    if match is None:
        raise ValueError("research Skill frontmatter is malformed")
    digest = hashlib.sha256(skill_text.encode("utf-8")).hexdigest()
    body = skill_text[match.end() :].rstrip()
    return (
        "<!-- GENERATED FROM skills/materials-inspiration-research/SKILL.md; "
        f"source-sha256: {digest} -->\n\n{body}\n"
    )


def verify() -> None:
    lock = json.loads((HERMES_ROOT / "hermes.lock.json").read_text("utf-8"))
    if lock.get("package_version") != "0.20.0":
        raise ValueError("research profile requires the reviewed Hermes runtime")
    config = yaml.safe_load((PROFILE_ROOT / "config.yaml").read_text("utf-8"))
    distribution = yaml.safe_load(
        (PROFILE_ROOT / "distribution.yaml").read_text("utf-8")
    )
    if distribution.get("name") != "materials-inspiration-research":
        raise ValueError("research profile identity drifted")
    if distribution.get("hermes_requires") != "==0.20.0":
        raise ValueError("research profile Hermes pin drifted")
    if config.get("_config_version") != 33:
        raise ValueError("research profile config schema drifted")
    expected_platforms = {
        "cli": ["materials_research"],
        "api_server": ["materials_research"],
    }
    if config.get("platform_toolsets") != expected_platforms:
        raise ValueError("research profile gained another platform toolset")
    disabled = set(config.get("agent", {}).get("disabled_toolsets", []))
    if not {"web", "browser", "terminal", "file", "code_execution", "memory", "delegation"} <= disabled:
        raise ValueError("research profile gained an unreviewed raw capability")
    if config.get("tool_loop_guardrails", {}).get("loop_caps", {}).get("max_web_searches") != 0:
        raise ValueError("Hermes host web search must remain disabled")
    server = config.get("mcp_servers", {}).get("materials_research", {})
    if server.get("url") != "${MATERIAL_AGENT_MCP_BASE_URL}/research/mcp":
        raise ValueError("research MCP URL drifted")
    if server.get("timeout") != 14_400:
        raise ValueError("research MCP timeout must cover the bounded nine-role run")
    if server.get("tools", {}).get("include") != EXPECTED_TOOLS:
        raise ValueError("research MCP allowlist drifted")
    if server.get("tools", {}).get("resources") is not False or server.get("tools", {}).get("prompts") is not False:
        raise ValueError("research MCP resources/prompts must remain disabled")
    skill_text = SKILL_PATH.read_text("utf-8")
    if SOUL_PATH.read_text("utf-8") != expected_soul(skill_text):
        raise ValueError("research Skill and SOUL drifted")
    for phrase in (
        "nine",
        "unresolved leads",
        "PASS`, `FAIL`, or `UNKNOWN",
        "`LIKELY_PASS` or `LIKELY_FAIL`",
        "REASONED_HYPOTHESIS",
        "only exposed scientific action",
    ):
        if phrase not in skill_text:
            raise ValueError(f"research authority wording drifted: {phrase}")
    committed = "\n".join(
        path.read_text("utf-8")
        for path in PROFILE_ROOT.rglob("*")
        if path.is_file()
    )
    if re.search(r"\bsk-[A-Za-z0-9_-]{20,}\b", committed):
        raise ValueError("credential-like value found in research profile")


if __name__ == "__main__":
    verify()
    print("Hermes research bundle valid")
