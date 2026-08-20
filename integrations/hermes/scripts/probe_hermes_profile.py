#!/usr/bin/env python3
"""Fail-closed Hermes profile/model/provider probe with redacted output."""

from __future__ import annotations

import argparse
import copy
import json
import os
import sys
from pathlib import Path

import yaml


EXPECTED_TOOLS = (
    "materials_inspiration_run",
    "materials_run_get",
    "materials_run_act",
    "materials_result_get",
)
EXPECTED_RESEARCH_TOOLS = (
    "materials_research_pipeline_run",
    "materials_generic_research_run",
)
EXPECTED_FACTORY = "shared-loopback-mcp-http-hub-v1"
MAX_MANAGED_PROFILE_FILE_BYTES = 1_000_000
MAX_MANAGED_SKILL_FILES = 64
MAX_MANAGED_SKILL_DIRECTORIES = 64
MAX_MANAGED_SKILL_BYTES = 8_000_000
IMMUTABLE_PROFILE_FILES = (
    Path(".no-bundled-skills"),
    Path("SOUL.md"),
)


class ProfileProbeError(RuntimeError):
    pass


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Probe one installed Hermes profile.")
    parser.add_argument("--profile-root", type=Path, required=True)
    parser.add_argument("--expected-profile", required=True)
    parser.add_argument("--expected-provider", required=True)
    parser.add_argument("--expected-model", required=True)
    parser.add_argument("--expected-gateway-python", type=Path, required=True)
    parser.add_argument("--expected-workspace", type=Path, required=True)
    parser.add_argument("--expected-project", required=True)
    parser.add_argument("--expected-mcp-base-url", required=True)
    parser.add_argument("--expected-source-profile", type=Path, required=True)
    return parser


def _bounded_file(path: Path) -> bytes:
    if path.is_symlink() or not path.is_file():
        raise ProfileProbeError("required profile file is unavailable")
    if path.stat().st_size > MAX_MANAGED_PROFILE_FILE_BYTES:
        raise ProfileProbeError("managed profile file exceeds its read budget")
    payload = path.read_bytes()
    if len(payload) > MAX_MANAGED_PROFILE_FILE_BYTES:
        raise ProfileProbeError("managed profile file exceeds its read budget")
    return payload


def _mapping(path: Path) -> dict[str, object]:
    value = yaml.safe_load(_bounded_file(path).decode("utf-8"))
    if not isinstance(value, dict):
        raise ProfileProbeError("profile document must be a mapping")
    return value


def _managed_skill_files(root: Path) -> dict[Path, bytes]:
    skills = root / "skills"
    if skills.is_symlink() or not skills.is_dir():
        raise ProfileProbeError("managed profile skills directory is unavailable")
    result: dict[Path, bytes] = {}
    pending = [skills]
    directories = 0
    total_bytes = 0
    while pending:
        current = pending.pop()
        directories += 1
        if directories > MAX_MANAGED_SKILL_DIRECTORIES:
            raise ProfileProbeError("managed profile Skill directory budget is exceeded")
        for entry in os.scandir(current):
            path = Path(entry.path)
            if entry.is_symlink():
                raise ProfileProbeError("managed profile skills contain a symlink")
            if entry.is_dir(follow_symlinks=False):
                pending.append(path)
            elif entry.is_file(follow_symlinks=False):
                relative = path.relative_to(root)
                payload = _bounded_file(path)
                result[relative] = payload
                total_bytes += len(payload)
                if (
                    len(result) > MAX_MANAGED_SKILL_FILES
                    or total_bytes > MAX_MANAGED_SKILL_BYTES
                ):
                    raise ProfileProbeError("managed profile Skill closure is too large")
            else:
                raise ProfileProbeError("managed profile skills contain a special file")
    return result


def _normalized_env_requires(value: object) -> list[dict[str, object]]:
    if not isinstance(value, list):
        raise ProfileProbeError("profile environment requirements are invalid")
    normalized: list[dict[str, object]] = []
    for item in value:
        if not isinstance(item, dict) or set(item) - {"description", "name", "required"}:
            raise ProfileProbeError("profile environment requirement fields differ")
        if not isinstance(item.get("name"), str) or not isinstance(
            item.get("description"), str
        ):
            raise ProfileProbeError("profile environment requirement is invalid")
        normalized.append(
            {"description": item["description"], "name": item["name"]}
        )
    return normalized


def _verify_source_closure(
    *,
    installed_root: Path,
    source_root_arg: Path,
    installed_config: dict[str, object],
    expected_provider: str,
    expected_model: str,
) -> None:
    source_root = source_root_arg.resolve()
    if source_root_arg.is_symlink() or not source_root.is_dir():
        raise ProfileProbeError("source profile root is unavailable")

    expected_config = copy.deepcopy(_mapping(source_root / "config.yaml"))
    expected_config["model"] = {
        "default": expected_model,
        "provider": expected_provider,
    }
    if installed_config != expected_config:
        raise ProfileProbeError("installed profile configuration differs from source")

    for relative in IMMUTABLE_PROFILE_FILES:
        if _bounded_file(installed_root / relative) != _bounded_file(
            source_root / relative
        ):
            raise ProfileProbeError("installed profile managed file differs from source")
    if _managed_skill_files(installed_root) != _managed_skill_files(source_root):
        raise ProfileProbeError("installed profile Skill closure differs from source")

    installed_distribution = _mapping(installed_root / "distribution.yaml")
    source_distribution = _mapping(source_root / "distribution.yaml")
    expected_keys = set(source_distribution) | {"installed_at", "source"}
    if set(installed_distribution) != expected_keys:
        raise ProfileProbeError("installed profile distribution fields differ")
    for key in (
        "description",
        "distribution_owned",
        "hermes_requires",
        "name",
        "version",
    ):
        if installed_distribution.get(key) != source_distribution.get(key):
            raise ProfileProbeError("installed profile distribution differs from source")
    if _normalized_env_requires(
        installed_distribution.get("env_requires")
    ) != _normalized_env_requires(source_distribution.get("env_requires")):
        raise ProfileProbeError("installed profile environment requirements differ")
    if installed_distribution.get("source") != str(source_root):
        raise ProfileProbeError("installed profile source binding differs")
    if not isinstance(installed_distribution.get("installed_at"), str) or not str(
        installed_distribution["installed_at"]
    ).strip():
        raise ProfileProbeError("installed profile timestamp is unavailable")


def main() -> int:
    args = _parser().parse_args()
    root = args.profile_root.resolve()
    if args.profile_root.is_symlink() or not root.is_dir():
        raise ProfileProbeError("profile root is unavailable")
    distribution = _mapping(root / "distribution.yaml")
    config = _mapping(root / "config.yaml")
    if distribution.get("name") != args.expected_profile:
        raise ProfileProbeError("installed profile identity differs")
    if distribution.get("hermes_requires") != "==0.20.0":
        raise ProfileProbeError("installed profile Hermes constraint differs")

    model = config.get("model")
    if not isinstance(model, dict):
        raise ProfileProbeError("profile model configuration is missing")
    if model.get("provider") != args.expected_provider:
        raise ProfileProbeError("profile provider differs from deployment input")
    if model.get("default") != args.expected_model:
        raise ProfileProbeError("profile model differs from deployment input")
    _verify_source_closure(
        installed_root=root,
        source_root_arg=args.expected_source_profile,
        installed_config=config,
        expected_provider=args.expected_provider,
        expected_model=args.expected_model,
    )

    materials = (config.get("mcp_servers") or {}).get("materials")
    if not isinstance(materials, dict):
        raise ProfileProbeError("Materials MCP profile entry is missing")
    if materials.get("url") != "${MATERIAL_AGENT_MCP_BASE_URL}/materials/mcp":
        raise ProfileProbeError("Materials MCP URL differs from the shared Hub")
    tools = ((materials.get("tools") or {}).get("include"))
    if tuple(tools or ()) != EXPECTED_TOOLS:
        raise ProfileProbeError("Materials MCP allowlist differs")
    research = (config.get("mcp_servers") or {}).get("materials_research")
    if not isinstance(research, dict):
        raise ProfileProbeError("Research MCP profile entry is missing")
    if research.get("url") != "${MATERIAL_AGENT_MCP_BASE_URL}/research/mcp":
        raise ProfileProbeError("Research MCP URL differs from the shared Hub")
    research_tools = ((research.get("tools") or {}).get("include"))
    if tuple(research_tools or ()) != EXPECTED_RESEARCH_TOOLS:
        raise ProfileProbeError("Research MCP allowlist differs")

    expected_environment = {
        "MATERIAL_AGENT_PROJECT_ID": args.expected_project,
        "MATERIAL_AGENT_MCP_BASE_URL": args.expected_mcp_base_url,
        # The venv Python path is an execution identity, not merely a filesystem
        # identity.  Resolving its symlink would discard the Gateway site-packages.
        "MATERIAL_AGENT_PYTHON": str(args.expected_gateway_python.absolute()),
        "MATERIAL_AGENT_SMACT_WORKER_PYTHON": str(
            (Path(__file__).resolve().parents[3] / ".venv-smact" / "bin" / "python")
            .absolute()
        ),
        "MATERIAL_AGENT_WORKSPACE": str(args.expected_workspace.resolve()),
    }
    for name, expected in expected_environment.items():
        if os.environ.get(name) != expected:
            raise ProfileProbeError("profile runtime environment differs")
    api_server_key = os.environ.get("API_SERVER_KEY", "")
    if len(api_server_key) < 32 or api_server_key.isspace():
        raise ProfileProbeError("API server key is missing or too short")

    try:
        from hermes_cli.runtime_provider import resolve_runtime_provider

        runtime = resolve_runtime_provider(
            requested=args.expected_provider,
            target_model=args.expected_model,
        )
    except Exception as exc:
        raise ProfileProbeError("provider credentials could not be resolved") from exc
    credential = runtime.get("api_key")
    if not (callable(credential) or (isinstance(credential, str) and credential.strip())):
        raise ProfileProbeError("provider credentials are unavailable")
    resolved_provider = str(runtime.get("provider") or "").strip()
    if not resolved_provider:
        raise ProfileProbeError("provider runtime identity is unavailable")

    print(
        json.dumps(
            {
                "credential_ready": True,
                "model": args.expected_model,
                "profile": args.expected_profile,
                "profile_source_verified": True,
                "provider": args.expected_provider,
                "queued_actions": True,
                "resolved_provider": resolved_provider,
                "schema_version": "materials-hermes-profile-probe-v2",
                "service_factory": EXPECTED_FACTORY,
                "tools": EXPECTED_TOOLS,
                "research_tools": EXPECTED_RESEARCH_TOOLS,
            },
            sort_keys=True,
            separators=(",", ":"),
        )
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ProfileProbeError, UnicodeDecodeError, yaml.YAMLError) as exc:
        print(f"profile probe failed: {exc}", file=sys.stderr)
        raise SystemExit(2) from exc
