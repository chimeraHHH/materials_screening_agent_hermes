"""One process-wide Streamable HTTP hub for local Materials MCP servers."""

from __future__ import annotations

import contextlib
import threading
import time
from collections.abc import Mapping
from typing import Any


class MCPHttpHubError(RuntimeError):
    pass


class MCPHttpHub:
    """Serve several low-level MCP servers from one loopback HTTP process."""

    def __init__(
        self,
        servers: Mapping[str, Any],
        *,
        host: str,
        port: int,
    ) -> None:
        if host not in {"127.0.0.1", "::1", "localhost"}:
            raise MCPHttpHubError("MCP HTTP Hub must remain loopback-bound")
        if not 1 <= port <= 65_535:
            raise MCPHttpHubError("MCP HTTP Hub port is invalid")
        if not servers or any(not name or "/" in name for name in servers):
            raise MCPHttpHubError("MCP HTTP Hub server names are invalid")
        self.host = host
        self.port = port
        self._thread: threading.Thread | None = None
        self._server: Any | None = None
        self._app = self._build_app(dict(servers))

    @staticmethod
    def _build_app(servers: dict[str, Any]):
        try:
            from mcp.server.streamable_http_manager import (
                StreamableHTTPSessionManager,
            )
            from starlette.applications import Starlette
            from starlette.responses import JSONResponse
            from starlette.routing import Mount, Route
        except ImportError as exc:  # pragma: no cover - isolated runtime only
            raise MCPHttpHubError("MCP HTTP dependencies are unavailable") from exc

        managers = {
            name: StreamableHTTPSessionManager(
                server,
                json_response=True,
                stateless=True,
            )
            for name, server in servers.items()
        }

        async def health(_request):
            return JSONResponse(
                {
                    "ok": True,
                    "schema_version": "materials-mcp-http-hub-v1",
                    "servers": sorted(managers),
                }
            )

        @contextlib.asynccontextmanager
        async def lifespan(_app):
            async with contextlib.AsyncExitStack() as stack:
                for manager in managers.values():
                    await stack.enter_async_context(manager.run())
                yield

        routes = [Route("/healthz", health, methods=["GET"])]
        routes.extend(
            Mount(f"/{name}/mcp", app=manager.handle_request)
            for name, manager in managers.items()
        )
        return Starlette(routes=routes, lifespan=lifespan)

    def start(self, *, timeout_seconds: float = 20.0) -> None:
        if self._thread is not None:
            raise MCPHttpHubError("MCP HTTP Hub is already started")
        try:
            import uvicorn
        except ImportError as exc:  # pragma: no cover - isolated runtime only
            raise MCPHttpHubError("uvicorn is unavailable") from exc
        config = uvicorn.Config(
            self._app,
            host=self.host,
            port=self.port,
            access_log=False,
            log_level="warning",
        )
        self._server = uvicorn.Server(config)
        self._thread = threading.Thread(
            target=self._server.run,
            name="materials-mcp-http-hub",
            daemon=True,
        )
        self._thread.start()
        deadline = time.monotonic() + timeout_seconds
        while time.monotonic() < deadline:
            if self._server.started:
                return
            if not self._thread.is_alive():
                break
            time.sleep(0.05)
        self.stop()
        raise MCPHttpHubError("MCP HTTP Hub failed to start")

    def stop(self, *, timeout_seconds: float = 10.0) -> None:
        if self._server is not None:
            self._server.should_exit = True
        if self._thread is not None:
            self._thread.join(timeout=timeout_seconds)
            if self._thread.is_alive():
                raise MCPHttpHubError("MCP HTTP Hub failed to stop")
        self._thread = None
        self._server = None
