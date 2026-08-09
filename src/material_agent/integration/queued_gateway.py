"""Production factory and supervised worker for queued Gateway actions.

The pinned Hermes profile uses ``create_queued_hermes_inspiration_service``.
A separate process must run this module's CLI; readiness must fail when that
worker is absent.  The historical synchronous factory remains available only
for compatibility.
"""

from __future__ import annotations

import argparse
import json
import os
import signal
import sys
import time
from collections.abc import Callable, Sequence
from pathlib import Path

from material_agent.gateway.authorization import SqliteOneTimeActionGrantStore
from material_agent.gateway.action_supervisor import GatewayActionProcessSupervisor
from material_agent.gateway.job_queue import SqliteGatewayJobQueue
from material_agent.gateway.service import (
    GatewayActionWorker,
    QueuedMaterialsGatewayService,
)
from material_agent.inspiration.search import BoundedHttpTransport
from material_agent.integration.hermes_service import (
    GatewayServerSettings,
    create_hermes_fixture_service,
    create_hermes_inspiration_service,
)


class QueuedGatewayConfigurationError(RuntimeError):
    """The synchronous production components cannot be safely wrapped."""


def _queued_from_base(base) -> QueuedMaterialsGatewayService:
    grant_store = base.action_authorizer
    if not isinstance(grant_store, SqliteOneTimeActionGrantStore):
        raise QueuedGatewayConfigurationError(
            "queued Gateway requires the SQLite one-time grant store"
        )
    queue = SqliteGatewayJobQueue(grant_store.database_path)
    return QueuedMaterialsGatewayService(
        repository=base.repository,
        companion=base.companion,
        artifact_reader=base.artifact_reader,
        grant_store=grant_store,
        action_queue=queue,
        max_report_bytes=base.max_report_bytes,
        max_closure_bytes=base.max_closure_bytes,
        max_readable_report_chars=base.max_readable_report_chars,
    )


def create_queued_hermes_fixture_service(
    settings: GatewayServerSettings,
) -> QueuedMaterialsGatewayService:
    """Build the offline fixture service with durable asynchronous actions."""

    return _queued_from_base(create_hermes_fixture_service(settings))


def create_queued_hermes_inspiration_service(
    settings: GatewayServerSettings,
    *,
    transport: BoundedHttpTransport | None = None,
    sleeper: Callable[[float], None] | None = None,
    wall_clock: Callable[[], float] | None = None,
    monotonic_clock: Callable[[], float] | None = None,
) -> QueuedMaterialsGatewayService:
    """Build the public Crossref service with durable asynchronous actions."""

    return _queued_from_base(
        create_hermes_inspiration_service(
            settings,
            transport=transport,
            sleeper=sleeper,
            wall_clock=wall_clock,
            monotonic_clock=monotonic_clock,
        )
    )


def worker_from_service(service: QueuedMaterialsGatewayService) -> GatewayActionWorker:
    """Build the legacy in-process worker for compatibility-only callers."""

    return GatewayActionWorker(
        repository=service.repository,
        companion=service.companion,
        artifact_reader=service.artifact_reader,
        grant_store=service.grant_store,
        action_queue=service.action_queue,
    )


def supervisor_from_service(
    service: QueuedMaterialsGatewayService,
    settings: GatewayServerSettings,
    *,
    service_mode: str,
    lease_seconds: int = 30,
    heartbeat_interval_seconds: float | None = None,
    kill_grace_seconds: float = 2.0,
    poll_interval_seconds: float = 0.05,
) -> GatewayActionProcessSupervisor:
    """Build the fixed child-process boundary used by the production worker."""

    if service_mode not in {"fixture", "public"}:
        raise QueuedGatewayConfigurationError(
            "queued Gateway child service mode is invalid"
        )
    if settings.workspace is None:
        raise QueuedGatewayConfigurationError(
            "queued Gateway supervisor requires an explicit workspace"
        )
    workspace = Path(settings.workspace).resolve()
    project_id = settings.project_id
    if heartbeat_interval_seconds is None:
        heartbeat_interval_seconds = min(5.0, max(0.25, lease_seconds / 3))

    def child_command(input_path: Path, output_path: Path) -> tuple[str, ...]:
        return (
            sys.executable,
            "-m",
            "material_agent.integration.queued_action_child",
            "--workspace",
            str(workspace),
            "--project",
            project_id,
            "--service-mode",
            service_mode,
            "--input",
            str(input_path),
            "--output",
            str(output_path),
        )

    return GatewayActionProcessSupervisor(
        worker=worker_from_service(service),
        attempt_root=(
            service.action_queue.database_path.parent
            / "gateway-action-supervisor"
        ),
        child_command_builder=child_command,
        lease_seconds=lease_seconds,
        heartbeat_interval_seconds=heartbeat_interval_seconds,
        kill_grace_seconds=kill_grace_seconds,
        poll_interval_seconds=poll_interval_seconds,
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the queued Gateway action worker")
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--project", required=True)
    parser.add_argument("--once", action="store_true")
    parser.add_argument(
        "--service-mode",
        choices=("fixture", "public"),
        default="public",
        help="fixed execution backend; fixture is for offline deployment smoke tests",
    )
    parser.add_argument("--poll-seconds", type=float, default=1.0)
    parser.add_argument("--lease-seconds", type=int, default=30)
    parser.add_argument("--heartbeat-seconds", type=float)
    parser.add_argument("--kill-grace-seconds", type=float, default=2.0)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if not 0.1 <= args.poll_seconds <= 60:
        raise SystemExit("--poll-seconds must be between 0.1 and 60")
    if not 2 <= args.lease_seconds <= 3_600:
        raise SystemExit("--lease-seconds must be between 2 and 3600")
    if args.heartbeat_seconds is not None and not (
        0 < args.heartbeat_seconds < args.lease_seconds / 2
    ):
        raise SystemExit(
            "--heartbeat-seconds must be positive and less than half the lease"
        )
    if not 0 < args.kill_grace_seconds <= 2:
        raise SystemExit("--kill-grace-seconds must be greater than zero and at most 2")
    settings = GatewayServerSettings(args.workspace, args.project)
    service = (
        create_queued_hermes_fixture_service(settings)
        if args.service_mode == "fixture"
        else create_queued_hermes_inspiration_service(settings)
    )
    supervisor = supervisor_from_service(
        service,
        settings,
        service_mode=args.service_mode,
        lease_seconds=args.lease_seconds,
        heartbeat_interval_seconds=args.heartbeat_seconds,
        kill_grace_seconds=args.kill_grace_seconds,
    )
    stopped = False

    def stop(_signum: int, _frame: object) -> None:
        nonlocal stopped
        stopped = True

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    worker_id = f"gateway-worker-{os.getpid()}"
    while not stopped:
        try:
            job = supervisor.run_once(
                worker_id=worker_id,
                stop_requested=lambda: stopped,
            )
        except Exception as exc:
            print(
                json.dumps(
                    {
                        "error": type(exc).__name__,
                        "status": "WORKER_ERROR",
                    },
                    sort_keys=True,
                ),
                flush=True,
            )
            return 1
        if job is not None:
            print(
                json.dumps(
                    {
                        "job_id": job.job_id,
                        "status": job.status.value,
                    },
                    sort_keys=True,
                ),
                flush=True,
            )
        if args.once:
            return 0
        if job is None:
            time.sleep(args.poll_seconds)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
