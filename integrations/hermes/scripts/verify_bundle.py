#!/usr/bin/env python3
"""Fail closed when the committed Hermes profile and Skill drift apart."""

from __future__ import annotations

import hashlib
import json
import re
import sys
import textwrap
from pathlib import Path

import yaml


REPO_ROOT = Path(__file__).resolve().parents[3]
HERMES_ROOT = REPO_ROOT / "integrations" / "hermes"
PROFILE_ROOT = HERMES_ROOT / "profiles" / "materials-inspiration"
SKILL_PATH = PROFILE_ROOT / "skills" / "materials-inspiration" / "SKILL.md"
GATEWAY_CONTRACT_PATH = (
    PROFILE_ROOT
    / "skills"
    / "materials-inspiration"
    / "references"
    / "gateway-contract.md"
)
SOUL_PATH = PROFILE_ROOT / "SOUL.md"
EXPECTED_TOOLS = [
    "materials_inspiration_run",
    "materials_run_get",
    "materials_run_act",
    "materials_result_get",
]
EXPECTED_SERVICE_FACTORY = (
    "material_agent.integration.hermes_service:create_hermes_fixture_service"
)
GATEWAY_REFERENCE_GUIDANCE = (
    "Read [the Gateway contract](references/gateway-contract.md) before the first tool\n"
    "call when tool arguments, states, or evidence boundaries are unclear."
)
SOUL_SCHEMA_GUIDANCE = (
    "Use the live MCP schemas as the only source for tool arguments and legal actions;\n"
    "never invent an unavailable action field."
)
PILOT_GOAL = (
    "Find bounded mechanism-guided structure proposals for a layered "
    "transition-metal compound."
)
PILOT_CONSTRAINTS = {
    "required_elements": ["Se", "Ti"],
    "excluded_elements": ["Pb"],
    "material_classes": ["layered transition-metal dichalcogenide"],
    "dimensionality": "2D",
    "target_features": ["electronic flat band"],
    "top_k": 1,
    "require_diverse_routes": True,
    "budget": {
        "max_search_requests": 3,
        "max_unique_documents": 1,
        "max_passages": 3,
        "max_model_calls": 0,
        "max_walltime_seconds": 300,
        "allow_full_pdf": False,
        "allow_expensive_computation": False,
    },
}


def expected_soul(skill_text: str) -> str:
    match = re.match(r"\A---\n.*?\n---\n\n?", skill_text, flags=re.DOTALL)
    if match is None:
        raise ValueError("Skill frontmatter is malformed")
    digest = hashlib.sha256(skill_text.encode("utf-8")).hexdigest()
    body = skill_text[match.end() :].rstrip()
    body = body.replace(GATEWAY_REFERENCE_GUIDANCE, SOUL_SCHEMA_GUIDANCE)
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
    distribution = yaml.safe_load(
        (PROFILE_ROOT / "distribution.yaml").read_text(encoding="utf-8")
    )
    if distribution.get("hermes_requires") != "==0.20.0":
        raise ValueError("Hermes profile compatibility pin drifted")
    if config.get("_config_version") != 33:
        raise ValueError("Hermes config schema must remain at v33 for the pinned runtime")
    expected_platforms = {"cli": ["materials"], "api_server": ["materials"]}
    if config.get("platform_toolsets") != expected_platforms:
        raise ValueError("profile must expose only the raw materials MCP toolset")
    if "skills" not in config.get("agent", {}).get("disabled_toolsets", []):
        raise ValueError("native skill management must remain disabled")
    server = config.get("mcp_servers", {}).get("materials", {})
    args = server.get("args", [])
    if "--service-factory" not in args:
        raise ValueError("materials MCP server must pin its trusted service factory")
    factory_index = args.index("--service-factory") + 1
    if factory_index >= len(args) or args[factory_index] != EXPECTED_SERVICE_FACTORY:
        raise ValueError("materials MCP service factory drifted")
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
    example_match = re.search(
        r"Use this shape:\n\n   ```json\n(?P<payload>.*?)\n   ```",
        skill_text,
        flags=re.DOTALL,
    )
    if example_match is None:
        raise ValueError("Skill must contain one parseable run request example")
    example = json.loads(textwrap.dedent(example_match.group("payload")))
    if set(example) != {"submission_id", "goal", "constraints"}:
        raise ValueError("Skill example must contain only the three run inputs")
    if "budget" in example or "budget" not in example.get("constraints", {}):
        raise ValueError("Skill example must nest budget only inside constraints")
    if example.get("goal") != PILOT_GOAL:
        raise ValueError("Skill example must use the canonical pilot goal")
    if example.get("constraints") != PILOT_CONSTRAINTS:
        raise ValueError("Skill example must contain the complete frozen constraints")
    if GATEWAY_REFERENCE_GUIDANCE not in skill_text:
        raise ValueError("Skill schema-retry guidance drifted")
    if skill_text.count(PILOT_GOAL) != 2:
        raise ValueError("Skill must bind the canonical pilot goal and example")
    gateway_contract = GATEWAY_CONTRACT_PATH.read_text(encoding="utf-8")
    if "`budget` is a field of `constraints`" not in gateway_contract:
        raise ValueError("Gateway contract must bind nested budget placement")
    if PILOT_GOAL not in gateway_contract:
        raise ValueError("Gateway contract must document the canonical pilot goal")
    if "complete frozen\nrequest shown in `SKILL.md`" not in gateway_contract:
        raise ValueError("Gateway contract must bind the complete frozen request")
    if re.search(r"terminal status is\s+`PARTIAL`", skill_text) is None:
        raise ValueError("Skill must preserve terminal warnings in result reports")
    if "Gateway materials-service ledger" not in skill_text:
        raise ValueError("Skill must distinguish Gateway and Hermes usage ledgers")
    if "does not guarantee two or more routes" not in skill_text:
        raise ValueError("Skill must document bounded diversity semantics")
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
