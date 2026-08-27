"""Two-tool MCP server for fixed and generic materials research flows."""

from __future__ import annotations

import argparse
import json
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from material_agent.integration.generic_research import (
    GENERIC_RESEARCH_TOOL_NAME,
    GenericMaterialsResearchService,
    GenericResearchRunRequestV1,
    generic_research_tool_manifest,
)
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
    def __init__(
        self,
        service: ResearchPipelineService,
        generic_service: GenericMaterialsResearchService | None = None,
    ) -> None:
        self.service = service
        self.generic_service = generic_service

    def dispatch(self, tool_name: str, arguments: Mapping[str, Any]) -> dict[str, Any]:
        if tool_name not in {RESEARCH_PIPELINE_TOOL_NAME, GENERIC_RESEARCH_TOOL_NAME}:
            raise ResearchPipelineDispatchError("unknown research pipeline tool")
        if not isinstance(arguments, Mapping) or not all(
            isinstance(key, str) for key in arguments
        ):
            raise ResearchPipelineDispatchError("tool arguments must be a JSON object")
        try:
            request = (
                GenericResearchRunRequestV1.model_validate(dict(arguments))
                if tool_name == GENERIC_RESEARCH_TOOL_NAME
                else ResearchPipelineRunRequestV1.model_validate(dict(arguments))
            )
        except (TypeError, ValueError, ValidationError):
            raise ResearchPipelineDispatchError("invalid research pipeline arguments") from None
        try:
            selected_service = (
                self.generic_service
                if tool_name == GENERIC_RESEARCH_TOOL_NAME
                else self.service
            )
            if selected_service is None:
                raise ResearchPipelineDispatchError(
                    "generic research service is unavailable"
                )
            run_for_tool = getattr(selected_service, "run_for_tool", None)
            submit = getattr(selected_service, "submit", None)
            if tool_name == GENERIC_RESEARCH_TOOL_NAME:
                if not callable(run_for_tool):
                    raise ResearchPipelineDispatchError(
                        "generic research compact receipt is unavailable"
                    )
                result = run_for_tool(request)
            else:
                result = (
                    submit(request)
                    if callable(submit)
                    else selected_service.run(request)
                )
        except Exception as exc:  # noqa: BLE001
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

    manifests = {
        item["name"]: item
        for item in (*research_pipeline_tool_manifest(), *generic_research_tool_manifest())
    }
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
        tools = [
            types.Tool(
                name=RESEARCH_PIPELINE_TOOL_NAME,
                description=str(manifests[RESEARCH_PIPELINE_TOOL_NAME]["description"]),
                inputSchema=ResearchPipelineRunRequestV1.model_json_schema(),
                outputSchema=manifests[RESEARCH_PIPELINE_TOOL_NAME]["outputSchema"],
                annotations=types.ToolAnnotations(readOnlyHint=False),
            )
        ]
        if dispatcher.generic_service is not None:
            generic = manifests[GENERIC_RESEARCH_TOOL_NAME]
            tools.append(
                types.Tool(
                    name=GENERIC_RESEARCH_TOOL_NAME,
                    description=str(generic["description"]),
                    inputSchema=GenericResearchRunRequestV1.model_json_schema(),
                    outputSchema=generic["outputSchema"],
                    annotations=types.ToolAnnotations(readOnlyHint=False),
                )
            )
        return tools

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
        description="Run the bounded fixed and generic materials research MCP server.",
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
                (*research_pipeline_tool_manifest(), *generic_research_tool_manifest()),
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
        generic_service = GenericMaterialsResearchService(
            workspace=args.workspace.resolve(),
            project_id=args.project,
        )
        serve_stdio(ResearchPipelineDispatcher(service, generic_service))
    except ResearchPipelineDispatchError as exc:
        parser.exit(2, f"research pipeline startup failed: {exc}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
