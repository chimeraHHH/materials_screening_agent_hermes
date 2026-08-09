#!/usr/bin/env python3
"""Automated production-factory E2E with static Crossref and optional live smoke."""

from __future__ import annotations

import argparse
import http.client
import json
import os
import ssl
import sys
import time
import uuid
from collections.abc import Mapping
from functools import partial
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import anyio
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from material_agent.gateway.mcp_server import GatewayServerSettings
from material_agent.gateway.models import InspirationBudgetV1, InspirationConstraintsV1
from material_agent.integration.operator_approval import issue_requirement_freeze_grant
from material_agent.integration.queued_gateway import (
    create_queued_hermes_inspiration_service,
    worker_from_service,
)


EXPECTED_TOOLS = (
    "materials_inspiration_run",
    "materials_run_get",
    "materials_run_act",
    "materials_result_get",
)
STATIC_FACTORY = "run_production_e2e:create_static_e2e_service"
QUEUED_PRODUCTION_FACTORY = (
    "material_agent.integration.queued_gateway:"
    "create_queued_hermes_inspiration_service"
)
_STATIC_PAYLOAD = json.dumps(
    {
        "message": {
            "items": [
                {
                    "DOI": "10.5555/materials-production-e2e.1",
                    "URL": "https://doi.org/10.5555/materials-production-e2e.1",
                    "abstract": (
                        "<jats:p>Local resonance in an acoustic metamaterial produces "
                        "a weakly dispersive mode because a resonator couples weakly "
                        "to an extended lattice. The local resonance mechanism "
                        "preserves spectral separation and suppresses dispersion, "
                        "which can guide electronic flat band hypotheses when "
                        "connectivity and equivalent site chemistry remain controlled. "
                        "Strong hybridization breaks localization and broadens the "
                        "mode, providing a falsification condition.</jats:p>"
                    ),
                    "author": [{"family": "Production", "given": "E2E"}],
                    "published": {"date-parts": [[2026]]},
                    "subject": ["Local resonance", "Electronic flat band"],
                    "title": ["Bounded production E2E local resonance"],
                }
            ]
        },
        "status": "ok",
    },
    sort_keys=True,
    separators=(",", ":"),
).encode("utf-8")


class ProductionE2EError(RuntimeError):
    pass


class StaticCrossrefTransport:
    """The production adapter boundary with no network access."""

    def get(
        self,
        url: str,
        *,
        headers: Mapping[str, str],
        timeout_seconds: float,
        max_response_bytes: int,
        deadline_monotonic: float | None = None,
        max_physical_requests: int | None = None,
    ) -> bytes:
        del headers, timeout_seconds, deadline_monotonic
        if max_physical_requests is not None and max_physical_requests < 1:
            raise ProductionE2EError("static physical request budget is exhausted")
        parsed = urlsplit(url)
        if not (
            parsed.scheme == "https"
            and parsed.hostname == "api.crossref.org"
            and parsed.path.rstrip("/") in {"/works", "/v1/works"}
        ):
            raise ProductionE2EError("static transport received an unexpected endpoint")
        if len(_STATIC_PAYLOAD) > max_response_bytes:
            raise ProductionE2EError("static response exceeds the production byte budget")
        return _STATIC_PAYLOAD


def create_static_e2e_service(settings: GatewayServerSettings):
    """Subprocess-loadable queued factory with only the HTTP seam replaced."""

    return create_queued_hermes_inspiration_service(
        settings,
        transport=StaticCrossrefTransport(),
        sleeper=lambda _seconds: None,
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Exercise the production service, real MCP stdio, approval, restart, "
            "artifact closure, and readable result without network access."
        )
    )
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--project", default="materials-inspiration-e2e")
    parser.add_argument("--submission-id")
    parser.add_argument(
        "--live-crossref-smoke",
        action="store_true",
        help=(
            "add one bounded Crossref metadata connectivity request; this does not "
            "authorize or execute a live Gateway run"
        ),
    )
    return parser


def _arguments(submission_id: str) -> dict[str, object]:
    constraints = InspirationConstraintsV1(
        required_elements=("Se", "Ti"),
        excluded_elements=("Pb",),
        material_classes=("layered transition-metal dichalcogenide",),
        dimensionality="2D",
        target_features=("narrow electronic band",),
        top_k=1,
        require_diverse_routes=True,
        budget=InspirationBudgetV1(
            max_search_requests=8,
            max_unique_documents=4,
            max_passages=4,
            max_model_calls=0,
            max_walltime_seconds=300,
        ),
    )
    return {
        "constraints": constraints.model_dump(mode="json"),
        "goal": "Find a reviewable narrow-band mechanism using bounded public metadata.",
        "submission_id": submission_id,
    }


def _parameters(workspace: Path, project: str) -> StdioServerParameters:
    repository_root = Path(__file__).resolve().parents[3]
    environment = dict(os.environ)
    python_path = [str(Path(__file__).resolve().parent), str(repository_root / "src")]
    if environment.get("PYTHONPATH"):
        python_path.append(environment["PYTHONPATH"])
    environment["PYTHONPATH"] = os.pathsep.join(python_path)
    return StdioServerParameters(
        command=sys.executable,
        args=[
            "-m",
            "material_agent.integration.mcp_server",
            "--workspace",
            str(workspace),
            "--project",
            project,
            "--service-factory",
            STATIC_FACTORY,
        ],
        cwd=str(repository_root),
        env=environment,
    )


async def _submit(
    *, workspace: Path, project: str, submission_id: str
) -> dict[str, Any]:
    with anyio.fail_after(30):
        with open(os.devnull, "w", encoding="utf-8") as error_log:
            async with stdio_client(
                _parameters(workspace, project), errlog=error_log
            ) as streams:
                async with ClientSession(*streams) as session:
                    await session.initialize()
                    listed = await session.list_tools()
                    tools = tuple(tool.name for tool in listed.tools)
                    if tools != EXPECTED_TOOLS:
                        raise ProductionE2EError("MCP tool allowlist differs")
                    response = await session.call_tool(
                        "materials_inspiration_run", _arguments(submission_id)
                    )
                    if response.isError or response.structuredContent is None:
                        raise ProductionE2EError("production E2E submission failed")
                    started = response.structuredContent
                    if started["state"]["status"] != "INTERACTION_REQUIRED":
                        raise ProductionE2EError("production run did not stop for approval")
                    interaction_id = started["state"]["interaction"]["interaction_id"]
                    ungranted = await session.call_tool(
                        "materials_run_act",
                        {
                            "action": {
                                "confirmed_by_user": True,
                                "interaction_id": interaction_id,
                                "kind": "approve",
                            },
                            "run_id": started["run_id"],
                        },
                    )
                    if not ungranted.isError:
                        raise ProductionE2EError("ungranted approval unexpectedly succeeded")
                    recovered = await session.call_tool(
                        "materials_run_get", {"run_id": started["run_id"]}
                    )
                    if recovered.isError or recovered.structuredContent != started:
                        raise ProductionE2EError("rejected action changed persisted state")
                    return {"run": started, "tools": tools}


async def _enqueue(
    *, workspace: Path, project: str, run_id: str, interaction_id: str
) -> dict[str, Any]:
    with anyio.fail_after(30):
        with open(os.devnull, "w", encoding="utf-8") as error_log:
            async with stdio_client(
                _parameters(workspace, project), errlog=error_log
            ) as streams:
                async with ClientSession(*streams) as session:
                    await session.initialize()
                    before = await session.call_tool("materials_run_get", {"run_id": run_id})
                    if before.isError or before.structuredContent is None:
                        raise ProductionE2EError("pending run did not survive MCP restart")
                    queued = await session.call_tool(
                        "materials_run_act",
                        {
                            "action": {
                                "confirmed_by_user": True,
                                "interaction_id": interaction_id,
                                "kind": "approve",
                            },
                            "run_id": run_id,
                        },
                    )
                    if queued.isError or queued.structuredContent is None:
                        raise ProductionE2EError("authorized production enqueue failed")
                    queued_body = queued.structuredContent
                    if queued_body["state"]["status"] != "RUNNING":
                        raise ProductionE2EError(
                            "queued action did not return RUNNING before worker execution"
                        )
                    premature_result = await session.call_tool(
                        "materials_result_get", {"run_id": run_id}
                    )
                    if not premature_result.isError:
                        raise ProductionE2EError(
                            "result became available before worker execution"
                        )
                    return {
                        "premature_result_rejected": True,
                        "run": queued_body,
                    }


def _run_worker(*, workspace: Path, project: str) -> dict[str, Any]:
    service = create_static_e2e_service(GatewayServerSettings(workspace, project))
    job = worker_from_service(service).run_once(
        worker_id=f"production-e2e-worker-{os.getpid()}",
        lease_seconds=3_600,
    )
    if job is None:
        raise ProductionE2EError("independent worker did not claim the queued action")
    if job.status.value != "SUCCEEDED":
        raise ProductionE2EError("independent worker did not complete the queued action")
    return {
        "attempt_count": job.attempt_count,
        "job_id": job.job_id,
        "status": job.status.value,
    }


async def _finish(*, workspace: Path, project: str, run_id: str) -> dict[str, Any]:
    with anyio.fail_after(30):
        with open(os.devnull, "w", encoding="utf-8") as error_log:
            async with stdio_client(
                _parameters(workspace, project), errlog=error_log
            ) as streams:
                async with ClientSession(*streams) as session:
                    await session.initialize()
                    terminal = await session.call_tool(
                        "materials_run_get", {"run_id": run_id}
                    )
                    if terminal.isError or terminal.structuredContent is None:
                        raise ProductionE2EError("worker terminal state is unavailable")
                    if terminal.structuredContent["state"]["status"] not in {
                        "SUCCEEDED",
                        "PARTIAL",
                    }:
                        raise ProductionE2EError("worker did not commit a terminal run")
                    result = await session.call_tool(
                        "materials_result_get", {"run_id": run_id}
                    )
                    if result.isError or result.structuredContent is None:
                        raise ProductionE2EError("terminal result failed online verification")
                    body = result.structuredContent
                    if body.get("verified") is not True:
                        raise ProductionE2EError("terminal result was not verified")
                    if not body.get("readable_report", {}).get("content"):
                        raise ProductionE2EError("bounded readable report is unavailable")
                    if not body.get("readable_evidence", {}).get("items"):
                        raise ProductionE2EError("bounded readable evidence is unavailable")
                    return {"result": body, "run": terminal.structuredContent}


async def _recover(*, workspace: Path, project: str, run_id: str) -> dict[str, Any]:
    with anyio.fail_after(30):
        with open(os.devnull, "w", encoding="utf-8") as error_log:
            async with stdio_client(
                _parameters(workspace, project), errlog=error_log
            ) as streams:
                async with ClientSession(*streams) as session:
                    await session.initialize()
                    run = await session.call_tool("materials_run_get", {"run_id": run_id})
                    result = await session.call_tool(
                        "materials_result_get", {"run_id": run_id}
                    )
                    if run.isError or result.isError:
                        raise ProductionE2EError("terminal run did not survive MCP restart")
                    return {
                        "result": result.structuredContent,
                        "run": run.structuredContent,
                    }


def _live_crossref_smoke() -> dict[str, object]:
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
                "User-Agent": "materials-screening-agent-e2e/1",
            },
        )
        response = connection.getresponse()
        payload = response.read(65_537)
    except (OSError, ssl.SSLError) as exc:
        raise ProductionE2EError("live Crossref smoke failed") from exc
    finally:
        connection.close()
    if response.status != 200 or len(payload) > 65_536:
        raise ProductionE2EError("live Crossref smoke violated status/byte bounds")
    try:
        value = json.loads(payload)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ProductionE2EError("live Crossref smoke returned invalid JSON") from exc
    if not isinstance(value, dict) or value.get("status") != "ok":
        raise ProductionE2EError("live Crossref smoke returned an unexpected schema")
    return {
        "duration_ms": max(0, int((time.monotonic() - started) * 1_000)),
        "status": "ok",
    }


def main() -> int:
    args = _parser().parse_args()
    workspace = args.workspace.resolve()
    if args.workspace.is_symlink():
        raise ProductionE2EError("E2E workspace cannot be a symlink")
    workspace.mkdir(parents=True, exist_ok=True)
    submission_id = args.submission_id or f"production-e2e-{uuid.uuid4().hex[:20]}"
    started_at = time.monotonic()
    submitted = anyio.run(
        partial(
            _submit,
            workspace=workspace,
            project=args.project,
            submission_id=submission_id,
        )
    )
    run = submitted["run"]
    interaction = run["state"]["interaction"]
    receipt = issue_requirement_freeze_grant(
        settings=GatewayServerSettings(workspace, args.project),
        run_id=run["run_id"],
        confirmation_reference=f"e2e:static:{submission_id}",
        service_mode="public",
    )
    enqueued = anyio.run(
        partial(
            _enqueue,
            workspace=workspace,
            project=args.project,
            run_id=run["run_id"],
            interaction_id=interaction["interaction_id"],
        )
    )
    stage_result_path = (
        workspace
        / args.project
        / "stages"
        / "inspiration"
        / run["run_id"]
        / "stage_result.json"
    )
    if stage_result_path.exists():
        raise ProductionE2EError("runner wrote stage_result before worker execution")
    worker = _run_worker(workspace=workspace, project=args.project)
    finished = anyio.run(
        partial(
            _finish,
            workspace=workspace,
            project=args.project,
            run_id=run["run_id"],
        )
    )
    recovered = anyio.run(
        partial(
            _recover,
            workspace=workspace,
            project=args.project,
            run_id=run["run_id"],
        )
    )
    if recovered["run"] != finished["run"] or recovered["result"] != finished["result"]:
        raise ProductionE2EError("terminal projection changed across MCP restart")
    result = finished["result"]
    live = _live_crossref_smoke() if args.live_crossref_smoke else {
        "status": "not_requested"
    }
    print(
        json.dumps(
            {
                "approval_decision": receipt["decision"],
                "act_status": enqueued["run"]["state"]["status"],
                "duration_ms": max(0, int((time.monotonic() - started_at) * 1_000)),
                "e2e_service_factory": STATIC_FACTORY,
                "gateway_service_factory": QUEUED_PRODUCTION_FACTORY,
                "live_crossref": live,
                "premature_result_rejected": enqueued[
                    "premature_result_rejected"
                ],
                "queued_execution": True,
                "readable_evidence_count": len(result["readable_evidence"]["items"]),
                "report_sha256": result["authoritative_sha256"],
                "restart_recovery": True,
                "run_id": run["run_id"],
                "schema_version": "materials-inspiration-production-e2e-v2",
                "stage_result_absent_after_act": True,
                "static_transport": True,
                "status": finished["run"]["state"]["status"],
                "tools": submitted["tools"],
                "verified": result["verified"],
                "worker_attempt_count": worker["attempt_count"],
                "worker_job_id": worker["job_id"],
                "worker_job_status": worker["status"],
            },
            sort_keys=True,
            separators=(",", ":"),
        )
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ProductionE2EError, ValueError) as exc:
        print(f"production E2E failed: {exc}", file=sys.stderr)
        raise SystemExit(2) from exc
