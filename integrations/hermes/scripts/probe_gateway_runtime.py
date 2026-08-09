#!/usr/bin/env python3
"""Probe the production Gateway DB, real MCP stdio surface, and Crossref."""

from __future__ import annotations

import argparse
import http.client
import json
import os
import sqlite3
import ssl
import stat
import sys
import time
from functools import partial
from pathlib import Path

import anyio
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from material_agent.gateway.job_queue import (
    JOB_QUEUE_SCHEMA_VERSION,
    GatewayJobAttempt,
)
from material_agent.integration.hermes_service import (
    GATEWAY_STATE_DATABASE_NAME,
    OPERATOR_APPROVAL_DATABASE_NAME,
)


EXPECTED_TOOLS = (
    "materials_inspiration_run",
    "materials_run_get",
    "materials_run_act",
    "materials_result_get",
)
SERVICE_FACTORY = (
    "material_agent.integration.queued_gateway:create_queued_hermes_inspiration_service"
)
MAX_CROSSREF_HEALTH_BYTES = 65_536


class GatewayProbeError(RuntimeError):
    pass


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Probe the production Gateway runtime.")
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--project", required=True)
    parser.add_argument("--crossref", action="store_true")
    return parser


async def _probe_mcp_inner(*, workspace: Path, project: str) -> tuple[str, ...]:
    parameters = StdioServerParameters(
        command=sys.executable,
        args=[
            "-m",
            "material_agent.integration.mcp_server",
            "--workspace",
            str(workspace),
            "--project",
            project,
            "--service-factory",
            SERVICE_FACTORY,
        ],
        cwd=str(Path(__file__).resolve().parents[3]),
        env=dict(os.environ),
    )
    with open(os.devnull, "w", encoding="utf-8") as error_log:
        async with stdio_client(parameters, errlog=error_log) as streams:
            async with ClientSession(*streams) as session:
                await session.initialize()
                listed = await session.list_tools()
                return tuple(tool.name for tool in listed.tools)


async def _probe_mcp(*, workspace: Path, project: str) -> tuple[str, ...]:
    with anyio.fail_after(30):
        return await _probe_mcp_inner(workspace=workspace, project=project)


def _database(path: Path, *, expected_tables: frozenset[str]) -> dict[str, object]:
    if path.is_symlink() or not path.is_file():
        raise GatewayProbeError("required Gateway database is unavailable")
    if stat.S_IMODE(path.stat().st_mode) != 0o600:
        raise GatewayProbeError("Gateway database permissions are not mode 0600")
    connection = sqlite3.connect(f"file:{path.resolve().as_posix()}?mode=ro", uri=True)
    try:
        connection.execute("PRAGMA query_only = ON")
        if connection.execute("PRAGMA quick_check").fetchone() != ("ok",):
            raise GatewayProbeError("Gateway database failed quick_check")
        tables = frozenset(
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        )
    finally:
        connection.close()
    if not expected_tables.issubset(tables):
        raise GatewayProbeError("Gateway database schema is incomplete")
    return {"path_present": True, "quick_check": "ok"}


def _job_queue(path: Path) -> dict[str, object]:
    connection = sqlite3.connect(f"file:{path.resolve().as_posix()}?mode=ro", uri=True)
    try:
        connection.execute("PRAGMA query_only = ON")
        row = connection.execute(
            "SELECT value FROM gateway_job_queue_metadata WHERE key='schema_version'"
        ).fetchone()
        job_columns = frozenset(
            item[1]
            for item in connection.execute("PRAGMA table_info(gateway_jobs)").fetchall()
        )
        attempt_columns = frozenset(
            item[1]
            for item in connection.execute(
                "PRAGMA table_info(gateway_job_attempts)"
            ).fetchall()
        )
        foreign_key_errors = connection.execute("PRAGMA foreign_key_check").fetchall()
    finally:
        connection.close()
    if row != (str(JOB_QUEUE_SCHEMA_VERSION),):
        raise GatewayProbeError("Gateway job queue schema differs")
    required_job_columns = {
        "failure_acknowledged_at_ms",
        "failure_acknowledged_by",
        "failure_acknowledgement_reason",
        "job_id",
        "lease_generation",
        "status",
    }
    required_attempt_columns = set(GatewayJobAttempt.__dataclass_fields__)
    if (
        not required_job_columns.issubset(job_columns)
        or not required_attempt_columns.issubset(attempt_columns)
        or foreign_key_errors
    ):
        raise GatewayProbeError("Gateway job queue schema is incomplete")
    return {
        "database_shared_with_approval_grants": True,
        "schema_version": JOB_QUEUE_SCHEMA_VERSION,
    }


def _crossref() -> dict[str, object]:
    started = time.monotonic()
    connection = http.client.HTTPSConnection(
        "api.crossref.org",
        443,
        timeout=10,
        context=ssl.create_default_context(),
    )
    try:
        connection.request(
            "GET",
            "/works?rows=0&select=DOI",
            headers={
                "Accept": "application/json",
                "User-Agent": "materials-screening-agent-health/1",
            },
        )
        response = connection.getresponse()
        if response.status != 200:
            raise GatewayProbeError("Crossref health request returned a non-200 status")
        declared_length = response.getheader("Content-Length")
        if declared_length is not None and int(declared_length) > MAX_CROSSREF_HEALTH_BYTES:
            raise GatewayProbeError("Crossref health response exceeds its byte budget")
        payload = response.read(MAX_CROSSREF_HEALTH_BYTES + 1)
    except (OSError, ssl.SSLError, ValueError) as exc:
        raise GatewayProbeError("Crossref health request failed") from exc
    finally:
        connection.close()
    if len(payload) > MAX_CROSSREF_HEALTH_BYTES:
        raise GatewayProbeError("Crossref health response exceeds its byte budget")
    try:
        value = json.loads(payload)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise GatewayProbeError("Crossref health response is not valid JSON") from exc
    if not isinstance(value, dict) or value.get("status") != "ok":
        raise GatewayProbeError("Crossref health response has an unexpected schema")
    return {
        "duration_ms": max(0, int((time.monotonic() - started) * 1_000)),
        "host": "api.crossref.org",
        "status": "ok",
    }


def main() -> int:
    args = _parser().parse_args()
    workspace = args.workspace.resolve()
    if args.workspace.is_symlink() or not workspace.is_dir():
        raise GatewayProbeError("Gateway workspace is unavailable")
    started = time.monotonic()
    tools = anyio.run(partial(_probe_mcp, workspace=workspace, project=args.project))
    if tools != EXPECTED_TOOLS:
        raise GatewayProbeError("MCP stdio tool allowlist differs")

    database_root = workspace / args.project / ".gateway"
    if database_root.is_symlink() or not database_root.is_dir():
        raise GatewayProbeError("Gateway database directory is unavailable")
    gateway = _database(
        database_root / GATEWAY_STATE_DATABASE_NAME,
        expected_tables=frozenset(
            {"gateway_schema_metadata", "gateway_runs", "gateway_results"}
        ),
    )
    approval = _database(
        database_root / OPERATOR_APPROVAL_DATABASE_NAME,
        expected_tables=frozenset(
            {
                "approval_schema_metadata",
                "gateway_job_events",
                "gateway_job_attempts",
                "gateway_job_queue_metadata",
                "gateway_jobs",
                "one_time_action_grants",
            }
        ),
    )
    job_queue = _job_queue(database_root / OPERATOR_APPROVAL_DATABASE_NAME)
    crossref = _crossref() if args.crossref else {"status": "not_requested"}
    print(
        json.dumps(
            {
                "approval_database": approval,
                "crossref": crossref,
                "duration_ms": max(0, int((time.monotonic() - started) * 1_000)),
                "gateway_database": gateway,
                "job_queue": job_queue,
                "queued_actions": True,
                "schema_version": "materials-gateway-production-probe-v2",
                "service_factory": SERVICE_FACTORY,
                "tools": tools,
            },
            sort_keys=True,
            separators=(",", ":"),
        )
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (GatewayProbeError, OSError, sqlite3.Error) as exc:
        print(f"Gateway probe failed: {exc}", file=sys.stderr)
        raise SystemExit(2) from exc
