#!/usr/bin/env python3
"""Validate the approval-gated, read-only Hermes evolution profile."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

import yaml


REPO_ROOT = Path(__file__).resolve().parents[3]
HERMES_ROOT = REPO_ROOT / "integrations" / "hermes"
PROFILE_ROOT = HERMES_ROOT / "profiles" / "materials-inspiration-evolution"
SKILL_PATH = (
    PROFILE_ROOT
    / "skills"
    / "materials-inspiration-evolution"
    / "SKILL.md"
)
SOUL_PATH = PROFILE_ROOT / "SOUL.md"
PROMOTION_GATE_PATH = (
    PROFILE_ROOT
    / "skills"
    / "materials-inspiration-evolution"
    / "references"
    / "promotion-gate.md"
)
EXPECTED_TOOLS = ["materials_run_get", "materials_result_get"]
EXPECTED_FACTORY = (
    "material_agent.integration.queued_gateway:"
    "create_queued_hermes_inspiration_service"
)
NATIVE_TOOLSETS = {"skills", "memory", "delegation"}


def _expected_soul(skill_text: str) -> str:
    match = re.match(r"\A---\n.*?\n---\n\n?", skill_text, flags=re.DOTALL)
    if match is None:
        raise ValueError("evolution Skill frontmatter is malformed")
    digest = hashlib.sha256(skill_text.encode("utf-8")).hexdigest()
    body = skill_text[match.end() :].rstrip()
    return (
        "<!-- GENERATED FROM skills/materials-inspiration-evolution/SKILL.md; "
        f"source-sha256: {digest} -->\n\n{body}\n"
    )


def verify() -> None:
    lock = json.loads((HERMES_ROOT / "hermes.lock.json").read_text("utf-8"))
    if lock.get("package_version") != "0.20.0":
        raise ValueError("evolution profile requires the reviewed Hermes runtime")

    config = yaml.safe_load((PROFILE_ROOT / "config.yaml").read_text("utf-8"))
    distribution = yaml.safe_load(
        (PROFILE_ROOT / "distribution.yaml").read_text("utf-8")
    )
    if distribution.get("hermes_requires") != "==0.20.0":
        raise ValueError("evolution profile Hermes pin drifted")
    if config.get("_config_version") != 33:
        raise ValueError("evolution profile config schema drifted")

    expected_platforms = {
        "cli": ["materials", "skills", "memory", "delegation"],
        "api_server": ["materials", "skills", "memory", "delegation"],
    }
    if config.get("platform_toolsets") != expected_platforms:
        raise ValueError("evolution profile must use the reviewed native toolsets")
    disabled = set(config.get("agent", {}).get("disabled_toolsets", []))
    if disabled & NATIVE_TOOLSETS:
        raise ValueError("native evolution toolsets were globally disabled")
    if not {"terminal", "file", "code_execution", "web"}.issubset(disabled):
        raise ValueError("evolution profile gained an unreviewed general toolset")

    memory = config.get("memory", {})
    skills = config.get("skills", {})
    delegation = config.get("delegation", {})
    if memory.get("memory_enabled") is not True or memory.get("write_approval") is not True:
        raise ValueError("native memory must be enabled and approval-gated")
    if memory.get("user_profile_enabled") is not False:
        raise ValueError("evolution must not learn a user profile")
    if skills.get("write_approval") is not True or skills.get("guard_agent_created") is not True:
        raise ValueError("native skill evolution must be guarded and approval-gated")
    if (
        delegation.get("max_concurrent_children") != 2
        or delegation.get("max_spawn_depth") != 1
        or delegation.get("orchestrator_enabled") is not False
        or delegation.get("inherit_mcp_toolsets") is not False
        or delegation.get("subagent_auto_approve") is not False
    ):
        raise ValueError("native delegation boundary drifted")
    if config.get("curator", {}).get("consolidate") is not False:
        raise ValueError("automatic LLM skill consolidation must remain disabled")

    server = config.get("mcp_servers", {}).get("materials", {})
    args = server.get("args", [])
    if "--service-factory" not in args:
        raise ValueError("read-only Materials MCP factory is not pinned")
    index = args.index("--service-factory") + 1
    if index >= len(args) or args[index] != EXPECTED_FACTORY:
        raise ValueError("read-only Materials MCP factory drifted")
    tools = server.get("tools", {})
    if tools.get("include") != EXPECTED_TOOLS:
        raise ValueError("evolution profile gained a scientific mutation tool")
    if tools.get("resources") is not False or tools.get("prompts") is not False:
        raise ValueError("MCP resources and prompts must remain disabled")
    if server.get("supports_parallel_tool_calls") is not False:
        raise ValueError("parallel Materials MCP calls must remain disabled")
    if server.get("elicitation", {}).get("enabled") is not False:
        raise ValueError("review-only MCP must not request scientific actions")

    skill_text = SKILL_PATH.read_text("utf-8")
    if SOUL_PATH.read_text("utf-8") != _expected_soul(skill_text):
        raise ValueError("evolution Skill and SOUL drifted")
    for phrase in (
        "review-only",
        "All memory and skill writes are staged by Hermes for human approval",
        "do not build a second memory",
        "Do not patch the distribution-owned",
        "Promotion requires an explicit",
    ):
        if phrase not in skill_text:
            raise ValueError(f"evolution authority wording drifted: {phrase}")
    promotion = PROMOTION_GATE_PATH.read_text("utf-8")
    for phrase in ("human-reviewed source diff", "verify_evolution_bundle.py", "credentials"):
        if phrase not in promotion:
            raise ValueError(f"promotion gate wording drifted: {phrase}")

    committed_text = "\n".join(
        path.read_text("utf-8")
        for path in PROFILE_ROOT.rglob("*")
        if path.is_file()
    )
    if re.search(r"\bsk-[A-Za-z0-9_-]{20,}\b", committed_text):
        raise ValueError("credential-like value found in evolution profile")


if __name__ == "__main__":
    verify()
    print("Hermes evolution bundle valid")
