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

from material_agent.gateway.models import InspirationRunRequestV1
from material_agent.integration.request_compiler import (
    HermesInspirationRequestCompiler,
)
from material_agent.inspiration.policy import SearchExecutionMode


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
HERMES_README_PATH = HERMES_ROOT / "README.md"
EXPECTED_TOOLS = [
    "materials_inspiration_run",
    "materials_run_get",
    "materials_run_act",
    "materials_result_get",
]
EXPECTED_SERVICE_FACTORY = (
    "material_agent.integration.hermes_service:create_hermes_inspiration_service"
)
GATEWAY_REFERENCE_GUIDANCE = (
    "Read [the Gateway contract](references/gateway-contract.md) before the first tool\n"
    "call when tool arguments, states, or evidence boundaries are unclear."
)
SOUL_SCHEMA_GUIDANCE = (
    "Use the live MCP schemas as the only source for tool arguments and legal actions;\n"
    "never invent an unavailable action field."
)
CANONICAL_SUPPORTED_GOAL = (
    "Find a reviewable narrow-band mechanism using bounded public metadata."
)
CANONICAL_SUPPORTED_CONSTRAINTS = {
    "required_elements": ["Se", "Ti"],
    "excluded_elements": ["Pb"],
    "material_classes": ["layered transition-metal dichalcogenide"],
    "dimensionality": "2D",
    "target_features": ["narrow electronic band"],
    "top_k": 1,
    "require_diverse_routes": True,
    "budget": {
        "max_search_requests": 8,
        "max_unique_documents": 4,
        "max_passages": 4,
        "max_model_calls": 0,
        "max_walltime_seconds": 300,
        "allow_full_pdf": False,
        "allow_expensive_computation": False,
    },
}
SUPPORTED_TARGET_FEATURES = (
    "electronic flat band",
    "electronic narrow band",
    "flat electronic band",
    "narrow electronic band",
)
SUPPORTED_MATERIAL_CLASSES = (
    "layered transition metal compound",
    "layered transition metal dichalcogenide",
    "transition metal dichalcogenide",
)


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
    if example.get("goal") != CANONICAL_SUPPORTED_GOAL:
        raise ValueError("Skill example must use the supported paraphrased goal")
    if example.get("constraints") != CANONICAL_SUPPORTED_CONSTRAINTS:
        raise ValueError("Skill example must contain the canonical supported constraints")
    request = InspirationRunRequestV1.model_validate_json(json.dumps(example))
    compiled = HermesInspirationRequestCompiler(max_retries_per_query=1).compile(
        request
    )
    if (
        compiled.policy.search_mode is not SearchExecutionMode.PUBLIC_METADATA_API
        or compiled.policy.network_access is not True
    ):
        raise ValueError("production request must compile to public metadata mode")
    fetch = compiled.policy.fetch
    if (
        fetch.max_requests != 0
        or fetch.max_total_bytes != 0
        or fetch.max_bytes_per_response != 0
        or fetch.allow_pdf_fulltext is not False
    ):
        raise ValueError("production request must compile to disabled body fetching")
    if GATEWAY_REFERENCE_GUIDANCE not in skill_text:
        raise ValueError("Skill schema-retry guidance drifted")
    if skill_text.count(CANONICAL_SUPPORTED_GOAL) != 1:
        raise ValueError("Skill must contain one canonical supported example goal")
    if "goal is\n   approval-bound rationale" not in skill_text:
        raise ValueError("Skill must forbid deriving execution scope from the goal")
    for term in (*SUPPORTED_TARGET_FEATURES, *SUPPORTED_MATERIAL_CLASSES):
        if f"`{term}`" not in skill_text:
            raise ValueError(f"Skill omits supported vocabulary term: {term}")
    for wording in (
        "at least eight physical search attempts",
        "four unique\n     documents, four passages, zero model calls, and 180 seconds",
        "subset of `Ti` and `Se`",
        "excluded_elements` must contain neither",
        "`UNSUPPORTED_INSPIRATION_REQUEST` before approval or network",
        "`EXTERNAL_SEARCH_UNAVAILABLE` with `retryable=true`",
        "new run with a new\n   `submission_id` and obtain a fresh approval",
        "Crossref schema drift is nonretryable",
        "live approval prompt must disclose\n   public Crossref metadata/abstract network access",
        "prompt that says offline execution for this public request\n   as a contract mismatch",
        "Never request, read, or summarize full PDFs",
        "immutable, review-only runtime Artifact",
        "`review_disposition=REVIEW_ONLY`",
        "`applies_to_tag_graph=false`",
        "`scientific_conclusion=false`",
        "`aggregation_semantics=INCLUSIVE_NON_ADDITIVE`",
        "every\n  bridge row use `expert_status=UNKNOWN`",
        "schema accepts no expert-review\n  input",
        "inclusive and non-additive",
        "body-fetch request budget is zero",
        "does not authorize public-network body fetching",
        "binds\n  DNS validation to the actual connection address",
        "does not expose feedback\nrows",
        "`MATERIALS_CROSSREF_CONTACT_EMAIL`",
        "never appear in an Artifact",
    ):
        if wording not in skill_text:
            raise ValueError(f"Skill public contract wording drifted: {wording}")
    gateway_contract = GATEWAY_CONTRACT_PATH.read_text(encoding="utf-8")
    if "`budget` is a field of `constraints`" not in gateway_contract:
        raise ValueError("Gateway contract must bind nested budget placement")
    if CANONICAL_SUPPORTED_GOAL in gateway_contract:
        raise ValueError("Gateway contract must not bind execution to example goal text")
    for wording in (
        "approval-bound rationale",
        "Scientific execution comes only from these reviewed\nconstraints",
        "`UNSUPPORTED_INSPIRATION_REQUEST`",
        "at least eight physical search attempts",
        "`EXTERNAL_SEARCH_UNAVAILABLE`",
        "new `submission_id` and obtain a fresh approval",
        "schema drift and other permanent adapter failures are nonretryable",
        "approval prompt must accurately\ndisclose the prepared execution mode",
        "public\nprepared run advertises offline execution",
        "immutable, review-only internal Artifact",
        "inclusive, non-additive",
        "Only the Gateway `CostLedger` is additive",
        "`review_disposition=REVIEW_ONLY`",
        "`applies_to_tag_graph=false`",
        "`scientific_conclusion=false`",
        "`aggregation_semantics=INCLUSIVE_NON_ADDITIVE`",
        "v1 accepts no expert-review input",
        "not its query, tag, or bridge rows",
        "body-fetch request budget is zero",
        "not evidence that\npublic-network body fetching is enabled",
        "DNS validation is bound to the actual connection address",
        "`MATERIALS_CROSSREF_CONTACT_EMAIL`",
    ):
        if wording not in gateway_contract:
            raise ValueError(f"Gateway contract wording drifted: {wording}")
    if re.search(r"terminal status is\s+`PARTIAL`", skill_text) is None:
        raise ValueError("Skill must preserve terminal warnings in result reports")
    if "Gateway materials-service ledger" not in skill_text:
        raise ValueError("Skill must distinguish Gateway and Hermes usage ledgers")
    if "requires two supported mechanism" not in skill_text:
        raise ValueError("Skill must document bounded diversity semantics")
    hermes_readme = HERMES_README_PATH.read_text(encoding="utf-8")
    if EXPECTED_SERVICE_FACTORY not in hermes_readme:
        raise ValueError("Hermes README must document the production factory")
    if "--service-mode fixture" not in hermes_readme:
        raise ValueError("Hermes README must make fixture approval mode explicit")
    if "CLI defaults to the production public service" not in hermes_readme:
        raise ValueError("Hermes README must document the operator CLI default")
    if (
        "new submission ID" not in hermes_readme
        or "new user decision" not in hermes_readme
    ):
        raise ValueError("Hermes README must document fresh transient-failure approval")
    for wording in (
        "zero body-fetch request\nbudget",
        "interaction must explicitly say that approval\npermits bounded public Crossref metadata/abstract network access",
        "incorrectly says the production run\nis offline is not informed approval",
        "offline and fixture-backed",
        "not public-network body-fetch\ncapability",
        "DNS validation is bound\nto the actual connection address",
        "immutable and review-only",
        "`review_disposition=REVIEW_ONLY`",
        "`applies_to_tag_graph=false`",
        "`scientific_conclusion=false`",
        "`aggregation_semantics=INCLUSIVE_NON_ADDITIVE`",
        "v1 accepts no expert-review input",
        "inclusive and non-additive",
        "`CostLedger` is the sole\nadditive run total",
        "do not close DNS\nrebinding/TOCTOU",
        "exposes only the\nfeedback Artifact URI/hash, not its rows",
    ):
        if wording not in hermes_readme:
            raise ValueError(f"Hermes README P3.3 boundary drifted: {wording}")
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
