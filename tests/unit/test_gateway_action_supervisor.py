from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from material_agent.gateway.action_supervisor import (
    MAX_SUPERVISOR_SHUTDOWN_SECONDS,
    GatewayActionProcessSupervisor,
)
from material_agent.gateway.authorization import SqliteOneTimeActionGrantStore
from material_agent.gateway.job_queue import GatewayJobStatus, SqliteGatewayJobQueue
from material_agent.gateway.memory import InMemoryArtifactStore
from material_agent.gateway.models import (
    ApprovalInteractionV1,
    ApproveActionV1,
    CancelledStateV1,
    CompanionTransitionV1,
    InspirationBudgetV1,
    InspirationConstraintsV1,
    InspirationRunRequestV1,
    InteractionRequiredStateV1,
)
from material_agent.gateway.persistence import SqliteGatewayRepository
from material_agent.gateway.service import (
    GatewayActionWorker,
    QueuedMaterialsGatewayService,
)


class _StaticCompanion:
    @staticmethod
    def start(
        *, run_id: str, request: InspirationRunRequestV1
    ) -> CompanionTransitionV1:
        del run_id, request
        return CompanionTransitionV1(
            state=InteractionRequiredStateV1(
                interaction=ApprovalInteractionV1(
                    interaction_id="interaction-supervisor-freeze",
                    approval_kind="requirement_freeze",
                    prompt="Freeze the supervised test request?",
                    input_sha256="a" * 64,
                    execution_manifest_sha256="a" * 64,
                )
            )
        )

    @staticmethod
    def act(**_arguments: object) -> CompanionTransitionV1:
        return CompanionTransitionV1(
            state=CancelledStateV1(reason="static supervised test transition")
        )


def _build_queued_action(tmp_path: Path):
    repository = SqliteGatewayRepository(tmp_path / "gateway.sqlite3")
    grant_store = SqliteOneTimeActionGrantStore(tmp_path / "actions.sqlite3")
    queue = SqliteGatewayJobQueue(grant_store.database_path)
    companion = _StaticCompanion()
    service = QueuedMaterialsGatewayService(
        repository=repository,
        companion=companion,
        artifact_reader=InMemoryArtifactStore(),
        grant_store=grant_store,
        action_queue=queue,
    )
    started = service.materials_inspiration_run(
        submission_id="supervised-submission",
        goal="Exercise the process supervisor boundary",
        constraints=InspirationConstraintsV1(
            budget=InspirationBudgetV1(
                max_search_requests=0,
                max_unique_documents=0,
                max_passages=0,
                max_walltime_seconds=1,
            )
        ),
    )
    assert isinstance(started.state, InteractionRequiredStateV1)
    action = ApproveActionV1(
        interaction_id=started.state.interaction.interaction_id,
        confirmed_by_user=True,
    )
    record = repository.get_run(started.run_id)
    assert record is not None
    grant_store.issue_grant(
        run_id=started.run_id,
        interaction=started.state.interaction,
        request_sha256=record.request_sha256,
        action=action,
        confirmation_reference="test:supervisor",
    )
    service.materials_run_act(
        run_id=started.run_id,
        action=action.model_dump(mode="json"),
    )
    worker = GatewayActionWorker(
        repository=repository,
        companion=companion,
        artifact_reader=InMemoryArtifactStore(),
        grant_store=grant_store,
        action_queue=queue,
    )
    return worker, queue, repository, started.run_id


def _forking_hang_command(pid_path: Path) -> tuple[str, ...]:
    descendant = (
        "import signal,time;"
        "signal.signal(signal.SIGTERM,signal.SIG_IGN);"
        "time.sleep(60)"
    )
    leader = (
        "import os,pathlib,signal,subprocess,sys,time;"
        "signal.signal(signal.SIGTERM,signal.SIG_IGN);"
        f"child=subprocess.Popen([sys.executable,'-c',{descendant!r}]);"
        f"pathlib.Path({str(pid_path)!r}).write_text("
        "f'{os.getpid()} {child.pid}',encoding='utf-8');"
        "time.sleep(60)"
    )
    return (sys.executable, "-c", leader)


def _read_pids(pid_path: Path) -> tuple[int, int]:
    deadline = time.monotonic() + 2
    while not pid_path.exists() and time.monotonic() < deadline:
        time.sleep(0.01)
    assert pid_path.is_file()
    direct, descendant = pid_path.read_text(encoding="utf-8").split()
    return int(direct), int(descendant)


def _assert_process_gone(pid: int) -> None:
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return
        time.sleep(0.01)
    pytest.fail(f"process {pid} was not terminated")


def _supervisor(
    tmp_path: Path,
    worker: GatewayActionWorker,
    command_builder,
    **kwargs,
) -> GatewayActionProcessSupervisor:
    return GatewayActionProcessSupervisor(
        worker=worker,
        attempt_root=tmp_path / "supervisor-protocol",
        child_command_builder=command_builder,
        lease_seconds=2,
        heartbeat_interval_seconds=0.2,
        kill_grace_seconds=0.05,
        poll_interval_seconds=0.01,
        **kwargs,
    )


def test_hard_deadline_kills_ignoring_process_group_and_fails_deterministically(
    tmp_path: Path,
) -> None:
    worker, queue, repository, run_id = _build_queued_action(tmp_path)
    pid_path = tmp_path / "deadline-pids.txt"
    supervisor = _supervisor(
        tmp_path,
        worker,
        lambda _input, _output: _forking_hang_command(pid_path),
    )

    started = time.monotonic()
    failed = supervisor.run_once(worker_id="supervisor-hard-deadline")
    elapsed = time.monotonic() - started

    assert failed is not None and failed.status is GatewayJobStatus.FAILED
    assert failed.failure_code == "HARD_DEADLINE_EXPIRED"
    assert elapsed < 2
    direct, descendant = _read_pids(pid_path)
    _assert_process_gone(direct)
    _assert_process_gone(descendant)
    persisted = repository.get_run(run_id)
    assert persisted is not None and persisted.state.status == "FAILED"
    assert persisted.state.public_error_code == "HARD_DEADLINE_EXPIRED"  # type: ignore[union-attr]
    attempt = queue.list_attempts(failed.job_id)[0]
    assert attempt.outcome == "HARD_DEADLINE_EXPIRED"
    assert attempt.duration_source == "MONOTONIC"
    assert attempt.timeout_phase == "hard-deadline"
    assert not tuple((tmp_path / "supervisor-protocol").rglob("*.json"))


def test_stop_during_hang_kills_reaps_and_releases_with_public_shutdown_bound(
    tmp_path: Path,
) -> None:
    worker, queue, repository, run_id = _build_queued_action(tmp_path)
    pid_path = tmp_path / "shutdown-pids.txt"
    supervisor = _supervisor(
        tmp_path,
        worker,
        lambda _input, _output: _forking_hang_command(pid_path),
    )

    started = time.monotonic()
    released = supervisor.run_once(
        worker_id="supervisor-worker-stop",
        stop_requested=pid_path.exists,
    )
    elapsed = time.monotonic() - started

    assert released is not None and released.status is GatewayJobStatus.READY
    assert elapsed < MAX_SUPERVISOR_SHUTDOWN_SECONDS
    direct, descendant = _read_pids(pid_path)
    _assert_process_gone(direct)
    _assert_process_gone(descendant)
    persisted = repository.get_run(run_id)
    assert persisted is not None and persisted.state.status == "RUNNING"
    assert queue.list_attempts(released.job_id)[0].outcome == "RELEASED"
    assert not tuple((tmp_path / "supervisor-protocol").rglob("*.json"))


def test_exception_after_popen_still_kills_and_reaps_child_group(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    worker, queue, _repository, _run_id = _build_queued_action(tmp_path)
    pid_path = tmp_path / "exception-pids.txt"
    processes: list[subprocess.Popen[bytes]] = []

    def process_factory(*args, **kwargs):
        process = subprocess.Popen(*args, **kwargs)
        processes.append(process)
        _read_pids(pid_path)
        return process

    def fail_mark(**_arguments):
        raise RuntimeError("injected mark_child_started failure")

    monkeypatch.setattr(queue, "mark_child_started", fail_mark)
    supervisor = _supervisor(
        tmp_path,
        worker,
        lambda _input, _output: _forking_hang_command(pid_path),
        process_factory=process_factory,
    )

    with pytest.raises(RuntimeError, match="injected mark_child_started"):
        supervisor.run_once(worker_id="supervisor-mark-failure")

    direct, descendant = _read_pids(pid_path)
    assert processes and processes[0].poll() is not None
    _assert_process_gone(direct)
    _assert_process_gone(descendant)
    assert not tuple((tmp_path / "supervisor-protocol").rglob("*.json"))


class _ExitedProcess:
    pid = 99_999_999
    returncode = 0

    @staticmethod
    def poll() -> int:
        return 0

    @staticmethod
    def wait(timeout: float | None = None) -> int:
        del timeout
        return 0


def test_transition_first_observed_at_deadline_is_never_read_or_accepted(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    worker, queue, repository, run_id = _build_queued_action(tmp_path)
    job = queue.claim(
        queue_name="gateway-actions",
        lease_owner="supervisor-late-output",
        lease_seconds=2,
    )
    assert job is not None and job.lease_token is not None
    queue.begin_attempt_execution(
        job_id=job.job_id,
        lease_owner="supervisor-late-output",
        lease_token=job.lease_token,
        deadline_budget_ms=1_000,
    )
    envelope = worker._validate_job(job)
    record = repository.get_run(run_id)
    assert record is not None
    readings = iter((0.0, 0.0, 0.0, 1.0, 1.0))
    supervisor = _supervisor(
        tmp_path,
        worker,
        lambda _input, _output: ("fixed-child",),
        monotonic_clock=lambda: next(readings, 1.0),
        process_factory=lambda *_args, **_kwargs: _ExitedProcess(),
    )

    def forbidden_read(_path: Path):
        raise AssertionError("late child output must not be read")

    monkeypatch.setattr(supervisor, "_read_child_output", forbidden_read)
    result = supervisor._run_child(
        job=job,
        record=record,
        envelope=envelope,
        child_index=0,
        hard_deadline=1.0,
        worker_id="supervisor-late-output",
        stop_requested=lambda: False,
    )

    assert result.timed_out is True
    assert result.transition is None
    assert not tuple((tmp_path / "supervisor-protocol").rglob("*.json"))
