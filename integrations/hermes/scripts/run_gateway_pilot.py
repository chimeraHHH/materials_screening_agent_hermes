#!/usr/bin/env python3
"""Run the fixed Hermes inspiration pilot through the real MCP stdio boundary."""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Sequence
from functools import partial
from pathlib import Path
from typing import Any

import anyio
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from material_agent.gateway.models import inspiration_run_id
from material_agent.integration.hermes_service import (
    HERMES_FIXTURE_CONSTRAINTS,
    HERMES_FIXTURE_GOAL,
)

EXPECTED_TOOLS = (
    "materials_inspiration_run",
    "materials_run_get",
    "materials_run_act",
    "materials_result_get",
)
SERVICE_FACTORY = (
    "material_agent.integration.hermes_service:create_hermes_fixture_service"
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Exercise the source-controlled pilot through MCP stdio."
    )
    parser.add_argument("phase", choices=("submit", "finish"))
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--project", required=True)
    parser.add_argument("--submission-id", required=True)
    return parser


def _parameters(workspace: Path, project: str) -> StdioServerParameters:
    repository_root = Path(__file__).resolve().parents[3]
    environment = dict(os.environ)
    python_path = [str(repository_root / "src")]
    if environment.get("PYTHONPATH"):
        python_path.append(environment["PYTHONPATH"])
    environment["PYTHONPATH"] = os.pathsep.join(python_path)
    return StdioServerParameters(
        command=sys.executable,
        args=[
            "-m",
            "material_agent.integration.mcp_server",
            "--workspace",
            str(workspace.resolve()),
            "--project",
            project,
            "--service-factory",
            SERVICE_FACTORY,
        ],
        cwd=str(repository_root),
        env=environment,
    )


async def _run(
    *,
    phase: str,
    workspace: Path,
    project: str,
    submission_id: str,
) -> dict[str, Any]:
    parameters = _parameters(workspace, project)
    run_id = inspiration_run_id(submission_id)
    with open(  # noqa: ASYNC230 - MCP requires a synchronous stderr stream
        os.devnull, "w", encoding="utf-8"
    ) as error_log:
        async with stdio_client(parameters, errlog=error_log) as streams:
            async with ClientSession(*streams) as session:
                await session.initialize()
                listed = await session.list_tools()
                tool_names = tuple(tool.name for tool in listed.tools)
                if tool_names != EXPECTED_TOOLS:
                    raise RuntimeError("MCP tool allowlist drifted")

                if phase == "submit":
                    response = await session.call_tool(
                        "materials_inspiration_run",
                        {
                            "submission_id": submission_id,
                            "goal": HERMES_FIXTURE_GOAL,
                            "constraints": HERMES_FIXTURE_CONSTRAINTS.model_dump(
                                mode="json"
                            ),
                        },
                    )
                    if response.isError or response.structuredContent is None:
                        raise RuntimeError("pilot submission failed")
                    started = response.structuredContent
                    interaction = started["state"]["interaction"]
                    ungranted = await session.call_tool(
                        "materials_run_act",
                        {
                            "run_id": started["run_id"],
                            "action": {
                                "kind": "approve",
                                "interaction_id": interaction["interaction_id"],
                                "confirmed_by_user": True,
                            },
                        },
                    )
                    if not ungranted.isError:
                        raise RuntimeError("ungranted approval unexpectedly succeeded")
                    recovered = await session.call_tool(
                        "materials_run_get",
                        {"run_id": started["run_id"]},
                    )
                    if recovered.isError or recovered.structuredContent != started:
                        raise RuntimeError("ungranted action changed Gateway state")
                    return {
                        "phase": phase,
                        "run": started,
                        "tools": tool_names,
                        "ungranted_action_rejected": True,
                    }

                status = await session.call_tool(
                    "materials_run_get",
                    {"run_id": run_id},
                )
                if status.isError or status.structuredContent is None:
                    raise RuntimeError("pending pilot run was not found")
                current = status.structuredContent
                interaction = current["state"]["interaction"]
                terminal = await session.call_tool(
                    "materials_run_act",
                    {
                        "run_id": run_id,
                        "action": {
                            "kind": "approve",
                            "interaction_id": interaction["interaction_id"],
                            "confirmed_by_user": True,
                        },
                    },
                )
                if terminal.isError or terminal.structuredContent is None:
                    raise RuntimeError("authorized pilot action failed")
                result = await session.call_tool(
                    "materials_result_get",
                    {"run_id": run_id},
                )
                if result.isError or result.structuredContent is None:
                    raise RuntimeError("terminal pilot result failed verification")
                return {
                    "phase": phase,
                    "result": result.structuredContent,
                    "run": terminal.structuredContent,
                    "tools": tool_names,
                }


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    payload = anyio.run(
        partial(
            _run,
            phase=args.phase,
            workspace=args.workspace,
            project=args.project,
            submission_id=args.submission_id,
        ),
    )
    print(
        json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
