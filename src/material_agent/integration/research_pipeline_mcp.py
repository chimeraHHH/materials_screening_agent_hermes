"""One-tool MCP stdio server for the direct non-DFT research pipeline."""

from __future__ import annotations

import argparse
import json
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from material_agent.integration.research_pipeline import (
    RESEARCH_PIPELINE_SCHEMA_VERSION,
    RESEARCH_PIPELINE_TOOL_NAME,
    ResearchPipelineRunRequestV1,
    ResearchPipelineService,
    research_pipeline_tool_manifest,
)


class ResearchPipelineDispatchError(RuntimeError):
    pass


class ResearchPipelineDispatcher:
    def __init__(self, service: ResearchPipelineService) -> None:
        self.service = service

    def dispatch(self, tool_name: str, arguments: Mapping[str, Any]) -> dict[str, Any]:
        if tool_name != RESEARCH_PIPELINE_TOOL_NAME:
            raise ResearchPipelineDispatchError("unknown research pipeline tool")
        if not isinstance(arguments, Mapping) or not all(
            isinstance(key, str) for key in arguments
        ):
            raise ResearchPipelineDispatchError("tool arguments must be a JSON object")
        try:
            request = ResearchPipelineRunRequestV1.model_validate(dict(arguments))
        except (TypeError, ValueError, ValidationError):
            raise ResearchPipelineDispatchError("invalid research pipeline arguments") from None
        try:
            submit = getattr(self.service, "submit", None)
            result = submit(request) if callable(submit) else self.service.run(request)
        except Exception as exc:
            code = getattr(exc, "code", None) or getattr(exc, "category", None)
            label = str(code or type(exc).__name__)
            raise ResearchPipelineDispatchError(
                f"research pipeline failed ({label})"
            ) from None
        return result.model_dump(mode="json")


def create_mcp_server(dispatcher: ResearchPipelineDispatcher):
    try:
        from mcp import types
        from mcp.server.lowlevel import Server
    except ImportError as exc:  # pragma: no cover - isolated runtime only
        raise ResearchPipelineDispatchError("MCP support is unavailable") from exc

    manifest = research_pipeline_tool_manifest()[0]
    server = Server(
        "materials-research-pipeline",
        version=RESEARCH_PIPELINE_SCHEMA_VERSION,
        instructions=(
            "Expose only the direct non-DFT local research pipeline.  The tool "
            "returns explicit stage outcomes and never upgrades blocked ML gates."
        ),
    )

    @server.list_tools()
    async def list_tools():
        return [
            types.Tool(
                name=RESEARCH_PIPELINE_TOOL_NAME,
                description=str(manifest["description"]),
                inputSchema=ResearchPipelineRunRequestV1.model_json_schema(),
                outputSchema=manifest["outputSchema"],
                annotations=types.ToolAnnotations(readOnlyHint=False),
            )
        ]

    @server.call_tool(validate_input=True)
    async def call_tool(name: str, arguments: dict[str, Any]):
        import anyio

        return await anyio.to_thread.run_sync(
            dispatcher.dispatch, name, arguments
        )

    return server


async def serve_stdio_async(dispatcher: ResearchPipelineDispatcher) -> None:
    try:
        from mcp.server.stdio import stdio_server
    except ImportError as exc:  # pragma: no cover - isolated runtime only
        raise ResearchPipelineDispatchError("MCP stdio support is unavailable") from exc
    server = create_mcp_server(dispatcher)
    async with stdio_server() as (read_stream, write_stream):
        await server.run(
            read_stream,
            write_stream,
            server.create_initialization_options(),
        )


def serve_stdio(dispatcher: ResearchPipelineDispatcher) -> None:
    try:
        import anyio
    except ImportError as exc:  # pragma: no cover - isolated runtime only
        raise ResearchPipelineDispatchError("MCP stdio runtime is unavailable") from exc
    anyio.run(serve_stdio_async, dispatcher)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="material-agent-research-pipeline",
        description="Run the one-tool direct non-DFT research MCP server.",
    )
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--project", default="materials-inspiration")
    parser.add_argument("--smact-worker-python", type=Path, required=True)
    parser.add_argument("--chgnet-worker-python", type=Path)
    parser.add_argument("--manifest", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    if args.manifest:
        print(
            json.dumps(
                research_pipeline_tool_manifest(),
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
        )
        return 0
    try:
        service = ResearchPipelineService(
            workspace=args.workspace.resolve(),
            project_id=args.project,
            smact_worker_python=args.smact_worker_python,
            chgnet_worker_python=args.chgnet_worker_python,
        )
        serve_stdio(ResearchPipelineDispatcher(service))
    except ResearchPipelineDispatchError as exc:
        parser.exit(2, f"research pipeline startup failed: {exc}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
