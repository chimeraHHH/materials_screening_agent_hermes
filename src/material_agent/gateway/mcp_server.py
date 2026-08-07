"""Lazy MCP stdio binding for the four Materials Gateway tools.

Importing this module does not require the optional ``mcp`` package.  The
protocol-neutral manifest and dispatcher remain testable in the main material
environment; MCP is imported only when a server is constructed or started.
"""

from __future__ import annotations

import argparse
import importlib
import json
import os
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ValidationError

from material_agent.gateway.errors import MaterialsGatewayError
from material_agent.gateway.memory import (
    InMemoryArtifactStore,
    InMemoryGatewayRepository,
)
from material_agent.gateway.models import (
    CompanionTransitionV1,
    FailedStateV1,
    InspirationRunRequestV1,
    MATERIALS_GATEWAY_VERSION,
    MaterialsResultGetRequestV1,
    MaterialsResultViewV1,
    MaterialsRunActRequestV1,
    MaterialsRunGetRequestV1,
    MaterialsRunViewV1,
    RunActionV1,
    RunStateV1,
    canonical_json_bytes,
)
from material_agent.gateway.service import MaterialsGatewayService


MATERIALS_TOOL_NAMES = (
    "materials_inspiration_run",
    "materials_run_get",
    "materials_run_act",
    "materials_result_get",
)


class GatewayDispatchError(RuntimeError):
    """A bounded public dispatch or optional-runtime failure."""


class MCPUnavailableError(GatewayDispatchError):
    """The optional MCP SDK is not installed in the active interpreter."""


@dataclass(frozen=True)
class GatewayToolSpec:
    name: str
    description: str
    request_model: type[BaseModel]
    response_model: type[BaseModel]
    read_only: bool

    def manifest(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "inputSchema": self.request_model.model_json_schema(),
            "outputSchema": self.response_model.model_json_schema(),
            "readOnly": self.read_only,
        }


TOOL_SPECS: tuple[GatewayToolSpec, ...] = (
    GatewayToolSpec(
        name="materials_inspiration_run",
        description="Create or idempotently recover one inspiration companion run.",
        request_model=InspirationRunRequestV1,
        response_model=MaterialsRunViewV1,
        read_only=False,
    ),
    GatewayToolSpec(
        name="materials_run_get",
        description="Read the bounded status projection for one materials run.",
        request_model=MaterialsRunGetRequestV1,
        response_model=MaterialsRunViewV1,
        read_only=True,
    ),
    GatewayToolSpec(
        name="materials_run_act",
        description="Apply one exact action to the current advertised interaction.",
        request_model=MaterialsRunActRequestV1,
        response_model=MaterialsRunViewV1,
        read_only=False,
    ),
    GatewayToolSpec(
        name="materials_result_get",
        description="Read a terminal result after authoritative report verification.",
        request_model=MaterialsResultGetRequestV1,
        response_model=MaterialsResultViewV1,
        read_only=True,
    ),
)

_SPECS_BY_NAME = {spec.name: spec for spec in TOOL_SPECS}
if tuple(_SPECS_BY_NAME) != MATERIALS_TOOL_NAMES:  # pragma: no cover - import invariant
    raise RuntimeError("Materials Gateway tool manifest is inconsistent")


def gateway_tool_manifest() -> tuple[dict[str, Any], ...]:
    """Return the complete JSON-safe manifest without importing MCP."""

    return tuple(spec.manifest() for spec in TOOL_SPECS)


class GatewayToolDispatcher:
    """Strict synchronous dispatch shared by MCP and protocol-neutral tests."""

    def __init__(self, service: MaterialsGatewayService) -> None:
        self.service = service

    def dispatch(self, tool_name: str, arguments: Mapping[str, Any]) -> dict[str, Any]:
        spec = _SPECS_BY_NAME.get(tool_name)
        if spec is None:
            raise GatewayDispatchError("unknown Materials Gateway tool")
        if not isinstance(arguments, Mapping) or not all(
            isinstance(key, str) for key in arguments
        ):
            raise GatewayDispatchError("tool arguments must be a JSON object")

        try:
            request = spec.request_model.model_validate_json(
                canonical_json_bytes(dict(arguments))
            )
        except (TypeError, ValueError, ValidationError):
            raise GatewayDispatchError(
                f"invalid arguments for {tool_name}"
            ) from None

        try:
            if isinstance(request, InspirationRunRequestV1):
                response = self.service.materials_inspiration_run(
                    submission_id=request.submission_id,
                    goal=request.goal,
                    constraints=request.constraints,
                )
            elif isinstance(request, MaterialsRunGetRequestV1):
                response = self.service.materials_run_get(run_id=request.run_id)
            elif isinstance(request, MaterialsRunActRequestV1):
                response = self.service.materials_run_act(
                    run_id=request.run_id,
                    action=request.action,
                )
            elif isinstance(request, MaterialsResultGetRequestV1):
                response = self.service.materials_result_get(run_id=request.run_id)
            else:  # pragma: no cover - frozen manifest exhaustiveness
                raise GatewayDispatchError(
                    "unsupported Materials Gateway request DTO"
                )
        except MaterialsGatewayError as exc:
            raise GatewayDispatchError(str(exc)) from None
        except GatewayDispatchError:
            raise
        except Exception:
            raise GatewayDispatchError(f"{tool_name} failed") from None

        try:
            validated = spec.response_model.model_validate_json(
                canonical_json_bytes(response)
            )
        except (TypeError, ValueError, ValidationError):
            raise GatewayDispatchError(
                f"invalid response from {tool_name}"
            ) from None
        return validated.model_dump(mode="json", by_alias=True)


def create_mcp_server(dispatcher: GatewayToolDispatcher):
    """Build the low-level MCP server, importing the optional SDK lazily."""

    try:
        from mcp import types
        from mcp.server.lowlevel import Server
    except ImportError as exc:  # pragma: no cover - environment dependent
        raise MCPUnavailableError(
            "MCP support is unavailable; use the isolated Hermes environment"
        ) from exc

    server = Server(
        "materials-gateway",
        version=MATERIALS_GATEWAY_VERSION,
        instructions="Only the four bounded Materials Gateway tools are exposed.",
    )

    @server.list_tools()
    async def list_tools():
        return [
            types.Tool(
                name=spec.name,
                description=spec.description,
                inputSchema=spec.request_model.model_json_schema(),
                outputSchema=spec.response_model.model_json_schema(),
                annotations=types.ToolAnnotations(readOnlyHint=spec.read_only),
            )
            for spec in TOOL_SPECS
        ]

    @server.call_tool(validate_input=True)
    async def call_tool(name: str, arguments: dict[str, Any]):
        return dispatcher.dispatch(name, arguments)

    return server


async def serve_stdio_async(dispatcher: GatewayToolDispatcher) -> None:
    """Run the allowlisted server over MCP stdio."""

    try:
        from mcp.server.stdio import stdio_server
    except ImportError as exc:  # pragma: no cover - environment dependent
        raise MCPUnavailableError(
            "MCP stdio support is unavailable; use the isolated Hermes environment"
        ) from exc

    server = create_mcp_server(dispatcher)
    async with stdio_server() as (read_stream, write_stream):
        await server.run(
            read_stream,
            write_stream,
            server.create_initialization_options(),
        )


def serve_stdio(dispatcher: GatewayToolDispatcher) -> None:
    try:
        import anyio
    except ImportError as exc:  # pragma: no cover - environment dependent
        raise MCPUnavailableError(
            "MCP stdio runtime is unavailable; use the isolated Hermes environment"
        ) from exc
    anyio.run(serve_stdio_async, dispatcher)


@dataclass(frozen=True)
class GatewayServerSettings:
    workspace: Path | None
    project_id: str


class _UnavailableCompanion:
    """Fail-closed default until an operator injects a real service factory."""

    def start(
        self,
        *,
        run_id: str,
        request: InspirationRunRequestV1,
    ) -> CompanionTransitionV1:
        del run_id, request
        return CompanionTransitionV1(
            state=FailedStateV1(
                public_error_code="CAPABILITY_UNAVAILABLE",
                public_message=(
                    "inspiration companion runner is not configured for this server"
                ),
                retryable=False,
            )
        )

    def act(
        self,
        *,
        run_id: str,
        request: InspirationRunRequestV1,
        state: RunStateV1,
        action: RunActionV1,
    ) -> CompanionTransitionV1:
        del run_id, request, state, action
        raise GatewayDispatchError("inspiration companion runner is unavailable")


def _default_service(_settings: GatewayServerSettings) -> MaterialsGatewayService:
    artifacts = InMemoryArtifactStore()
    return MaterialsGatewayService(
        repository=InMemoryGatewayRepository(),
        companion=_UnavailableCompanion(),
        artifact_reader=artifacts,
    )


def _load_service(
    factory_spec: str | None,
    settings: GatewayServerSettings,
) -> MaterialsGatewayService:
    if not factory_spec:
        return _default_service(settings)
    if factory_spec.count(":") != 1:
        raise GatewayDispatchError(
            "service factory must use the form 'module:callable'"
        )
    module_name, attribute_name = factory_spec.split(":", 1)
    if not module_name or not attribute_name:
        raise GatewayDispatchError(
            "service factory must use the form 'module:callable'"
        )
    try:
        module = importlib.import_module(module_name)
    except Exception:
        raise GatewayDispatchError(
            "configured service factory could not be imported"
        ) from None
    factory = getattr(module, attribute_name, None)
    if not callable(factory):
        raise GatewayDispatchError("configured service factory is not callable")
    try:
        service = factory(settings)
    except Exception:
        raise GatewayDispatchError("configured service factory failed") from None
    if not isinstance(service, MaterialsGatewayService):
        raise GatewayDispatchError(
            "configured service factory did not return MaterialsGatewayService"
        )
    return service


def _argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="material-agent-gateway",
        description="Run the four-tool Materials Gateway MCP stdio server.",
    )
    parser.add_argument("--workspace", type=Path, default=None)
    parser.add_argument("--project", default="materials-inspiration")
    parser.add_argument(
        "--service-factory",
        default=os.environ.get("MATERIALS_GATEWAY_SERVICE_FACTORY"),
        help="operator-controlled module:callable returning MaterialsGatewayService",
    )
    parser.add_argument(
        "--manifest",
        action="store_true",
        help="print the four-tool manifest and exit without importing MCP",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = _argument_parser()
    args = parser.parse_args(argv)
    if args.manifest:
        print(
            json.dumps(
                gateway_tool_manifest(),
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
        )
        return 0

    settings = GatewayServerSettings(
        workspace=args.workspace.resolve() if args.workspace is not None else None,
        project_id=args.project,
    )
    try:
        service = _load_service(args.service_factory, settings)
        serve_stdio(GatewayToolDispatcher(service))
    except GatewayDispatchError as exc:
        parser.exit(2, f"gateway startup failed: {exc}\n")
    return 0


if __name__ == "__main__":  # pragma: no cover - exercised through wrapper
    raise SystemExit(main())
