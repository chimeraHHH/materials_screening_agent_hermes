#!/usr/bin/env python3
"""Loopback-only health and Prometheus sidecar for Materials Inspiration."""

from __future__ import annotations

import argparse
import json
import re
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from production_ops import (
    ProductionOpsError,
    metrics_snapshot,
    probe_http_json,
    prometheus_metrics,
    read_json,
)

_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "::1", "localhost"})


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Serve loopback-only Materials Inspiration health and metrics."
    )
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=9120)
    parser.add_argument("--dashboard-host", default="127.0.0.1")
    parser.add_argument("--dashboard-port", type=int, default=9119)
    parser.add_argument("--event-log", type=Path, required=True)
    parser.add_argument("--gateway-database", type=Path, required=True)
    parser.add_argument("--queue-database", type=Path, required=True)
    parser.add_argument("--process-file", type=Path, required=True)
    parser.add_argument("--runtime-binding-sha256", required=True)
    return parser


def _payload(
    settings: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    process_record = read_json(settings["process_file"])
    snapshot = metrics_snapshot(
        event_log=settings["event_log"],
        gateway_database=settings["gateway_database"],
        queue_database=settings["queue_database"],
        process_record=process_record,
        expected_runtime_binding_sha256=settings["runtime_binding_sha256"],
    )
    dashboard = probe_http_json(
        f"http://{settings['dashboard_host']}:{settings['dashboard_port']}/api/health"
    )
    local_components_ok = bool(
        dashboard["ok"]
        and snapshot["dashboard"]["up"]
        and snapshot["monitor"]["up"]
        and snapshot["worker"]["up"]
        and snapshot["database"]["integrity"]
        and snapshot["queue"]["integrity"]
        and snapshot["process_set_valid"]
        and snapshot["runtime_binding_match"]
    )
    health = {
        "dashboard_http": dashboard["ok"],
        "dashboard_process": snapshot["dashboard"]["up"],
        "gateway_database": snapshot["database"]["integrity"],
        "monitor_process": snapshot["monitor"]["up"],
        "ok": local_components_ok,
        "queue_database": snapshot["queue"]["integrity"],
        "runtime_binding_match": snapshot["runtime_binding_match"],
        "schema_version": "materials-inspiration-health-v2",
        "worker_identity": snapshot["worker"]["recorded_identity"],
        "worker_pid": snapshot["worker"]["pid"],
        "worker_process": snapshot["worker"]["up"],
    }
    readiness_reasons = list(snapshot["readiness"]["reasons"])
    if not dashboard["ok"]:
        readiness_reasons.append("DASHBOARD_HTTP_UNAVAILABLE")
    readiness = {
        **health,
        "ok": bool(local_components_ok and snapshot["readiness"]["ok"]),
        "queue": snapshot["queue"],
        "reasons": readiness_reasons,
        "schema_version": "materials-inspiration-readiness-v1",
    }
    snapshot["readiness"] = {
        **snapshot["readiness"],
        "ok": readiness["ok"],
        "reasons": readiness_reasons,
    }
    return health, readiness, snapshot


def _handler(settings: dict[str, Any]):
    class Handler(BaseHTTPRequestHandler):
        server_version = "materials-inspiration-monitor/1"

        def log_message(self, _format: str, *_arguments: object) -> None:
            return

        def _send(self, status: int, media_type: str, payload: bytes) -> None:
            self.send_response(status)
            self.send_header("Content-Type", media_type)
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def do_GET(self) -> None:  # noqa: N802 - stdlib handler contract
            try:
                health, readiness, snapshot = _payload(settings)
                if self.path in {"/healthz", "/readyz"}:
                    value = health if self.path == "/healthz" else readiness
                    payload = json.dumps(
                        value,
                        sort_keys=True,
                        separators=(",", ":"),
                    ).encode("utf-8")
                    self._send(
                        200 if value["ok"] else 503,
                        "application/json; charset=utf-8",
                        payload,
                    )
                    return
                if self.path == "/metrics":
                    self._send(
                        200,
                        "text/plain; version=0.0.4; charset=utf-8",
                        prometheus_metrics(snapshot).encode("utf-8"),
                    )
                    return
                self._send(404, "application/json", b'{"error":"not_found"}')
            except (OSError, ProductionOpsError):
                self._send(
                    503,
                    "application/json",
                    b'{"error":"health_unavailable","ok":false}',
                )

    return Handler


def main() -> int:
    args = _parser().parse_args()
    if args.host not in _LOOPBACK_HOSTS or args.dashboard_host not in _LOOPBACK_HOSTS:
        raise ProductionOpsError("operations monitor must remain loopback-bound")
    for port in (args.port, args.dashboard_port):
        if not 1 <= port <= 65_535:
            raise ProductionOpsError("operations port is outside the valid range")
    if args.port == args.dashboard_port:
        raise ProductionOpsError("monitor and dashboard ports must differ")
    if re.fullmatch(r"[0-9a-f]{64}", args.runtime_binding_sha256) is None:
        raise ProductionOpsError("runtime binding SHA-256 is invalid")
    settings = {
        "dashboard_host": args.dashboard_host,
        "dashboard_port": args.dashboard_port,
        "event_log": args.event_log.resolve(),
        "gateway_database": args.gateway_database.resolve(),
        "queue_database": args.queue_database.resolve(),
        "process_file": args.process_file.resolve(),
        "runtime_binding_sha256": args.runtime_binding_sha256,
    }
    server = ThreadingHTTPServer((args.host, args.port), _handler(settings))
    server.serve_forever(poll_interval=0.5)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ProductionOpsError) as exc:
        print(f"monitor startup failed: {exc}", file=sys.stderr)
        raise SystemExit(2) from exc
