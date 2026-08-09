from __future__ import annotations

import sqlite3
from concurrent.futures import ThreadPoolExecutor
from multiprocessing import get_context
from pathlib import Path
from threading import Event

import pytest

import material_agent.gateway.authorization as authorization_module
from material_agent.gateway.authorization import SqliteOneTimeActionGrantStore
from material_agent.gateway.companion import CompanionAdapterError
from material_agent.gateway.errors import ActionAuthorizationError
from material_agent.gateway.job_queue import GatewayJobStatus, SqliteGatewayJobQueue
from material_agent.gateway.memory import InMemoryArtifactStore
from material_agent.gateway.mcp_server import GatewayToolDispatcher
from material_agent.gateway.models import (
    ApprovalInteractionV1,
    ApproveActionV1,
    CancelledStateV1,
    CompanionTransitionV1,
    InspirationBudgetV1,
    InspirationConstraintsV1,
    InspirationRunRequestV1,
    InteractionRequiredStateV1,
    RejectActionV1,
    RunningStateV1,
    RunActionV1,
    RunStateV1,
)
from material_agent.gateway.persistence import SqliteGatewayRepository
from material_agent.gateway.service import (
    GatewayActionWorker,
    QueuedMaterialsGatewayService,
    _RunScopedProcessLock,
)


class _ManualClock:
    def __init__(self) -> None:
        self.value = 10_000

    def __call__(self) -> int:
        return self.value

    def advance(self, value: int) -> None:
        self.value += value


class _SimulatedCrash(BaseException):
    pass


class _StaticCountingCompanion:
    def __init__(
        self,
        *,
        supports_completed_recovery: bool = False,
        partial_recovery: bool = False,
    ) -> None:
        self.start_calls = 0
        self.action_calls = 0
        self.runner_calls = 0
        self.crash_during_action = False
        self.supports_completed_recovery = supports_completed_recovery
        self.partial_recovery = partial_recovery
        self.completed = False
        if supports_completed_recovery:
            self.projector = _CompletedRecoveryMarker()

    @staticmethod
    def interaction() -> ApprovalInteractionV1:
        return ApprovalInteractionV1(
            interaction_id="interaction-queued-freeze",
            approval_kind="requirement_freeze",
            prompt="Freeze this bounded queued fixture?",
            input_sha256="a" * 64,
            execution_manifest_sha256="a" * 64,
        )

    def start(
        self, *, run_id: str, request: InspirationRunRequestV1
    ) -> CompanionTransitionV1:
        del run_id, request
        self.start_calls += 1
        return CompanionTransitionV1(
            state=InteractionRequiredStateV1(interaction=self.interaction())
        )

    def act(
        self,
        *,
        run_id: str,
        request: InspirationRunRequestV1,
        state: RunStateV1,
        action: RunActionV1,
    ) -> CompanionTransitionV1:
        del run_id, request
        assert isinstance(state, InteractionRequiredStateV1)
        assert action.interaction_id == state.interaction.interaction_id
        self.action_calls += 1
        if action.kind == "approve":
            if self.action_calls > 1 and self.partial_recovery:
                raise CompanionAdapterError("partial stage cannot be recovered")
            if not self.completed:
                self.runner_calls += 1
        if self.crash_during_action:
            raise _SimulatedCrash()
        self.completed = True
        return CompanionTransitionV1(
            state=CancelledStateV1(reason="fixture action completed durably")
        )


class _CompletedRecoveryMarker:
    def recover_or_bind_completed(self, **_arguments):
        raise AssertionError("the marker is inspected structurally, not called directly")


def _hold_run_lock(root: str, run_id: str, ready, release) -> None:
    with _RunScopedProcessLock(Path(root), run_id):
        ready.set()
        release.wait(timeout=15)


def _build_stack(
    tmp_path: Path,
    *,
    after_enqueue_hook=None,
    decision: str = "approve",
    supports_completed_recovery: bool = False,
    partial_recovery: bool = False,
):
    gateway_database = tmp_path / "gateway.sqlite3"
    action_database = tmp_path / "action-outbox.sqlite3"
    clock = _ManualClock()
    repository = SqliteGatewayRepository(gateway_database)
    grant_store = SqliteOneTimeActionGrantStore(action_database)
    queue = SqliteGatewayJobQueue(action_database, clock_ms=clock)
    companion = _StaticCountingCompanion(
        supports_completed_recovery=supports_completed_recovery,
        partial_recovery=partial_recovery,
    )
    service = QueuedMaterialsGatewayService(
        repository=repository,
        companion=companion,
        artifact_reader=InMemoryArtifactStore(),
        grant_store=grant_store,
        action_queue=queue,
        after_enqueue_hook=after_enqueue_hook,
    )
    started = service.materials_inspiration_run(
        submission_id="queued-submission",
        goal="Exercise queued production action recovery",
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
    if decision == "approve":
        parsed_action = ApproveActionV1(
            interaction_id=started.state.interaction.interaction_id,
            confirmed_by_user=True,
        )
    elif decision == "reject":
        parsed_action = RejectActionV1(
            interaction_id=started.state.interaction.interaction_id,
            confirmed_by_user=True,
            reason="fixture rejection",
        )
    else:
        raise AssertionError("unsupported fixture decision")
    grant_store.issue_grant(
        run_id=started.run_id,
        interaction=started.state.interaction,
        request_sha256=repository.get_run(started.run_id).request_sha256,  # type: ignore[union-attr]
        action=parsed_action,
        confirmation_reference="ticket:queued-action-001",
    )
    action = parsed_action.model_dump(mode="json")
    return service, repository, grant_store, queue, companion, clock, started, action


def _worker(repository, grant_store, queue, companion):
    return GatewayActionWorker(
        repository=repository,
        companion=companion,
        artifact_reader=InMemoryArtifactStore(),
        grant_store=grant_store,
        action_queue=queue,
    )


def test_grant_consumption_and_outbox_insert_rollback_together(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database = tmp_path / "actions.sqlite3"
    store = SqliteOneTimeActionGrantStore(database)
    interaction = _StaticCountingCompanion.interaction()
    action = ApproveActionV1(
        interaction_id=interaction.interaction_id,
        confirmed_by_user=True,
    )
    store.issue_grant(
        run_id="inspiration-atomic",
        interaction=interaction,
        request_sha256="b" * 64,
        action=action,
        confirmation_reference="ticket:atomic-001",
    )

    def fail_enqueue(*_args, **_kwargs):
        raise RuntimeError("injected outbox failure")

    monkeypatch.setattr(
        authorization_module,
        "enqueue_gateway_job_in_transaction",
        fail_enqueue,
    )
    with pytest.raises(ActionAuthorizationError):
        store.authorize_consume_and_enqueue(
            run_id="inspiration-atomic",
            run_revision=1,
            interaction=interaction,
            request_sha256="b" * 64,
            action=action,
        )
    connection = sqlite3.connect(database)
    consumed = connection.execute(
        "SELECT consumed FROM one_time_action_grants"
    ).fetchone()[0]
    jobs = connection.execute("SELECT COUNT(*) FROM gateway_jobs").fetchone()[0]
    connection.close()
    assert (consumed, jobs) == (0, 0)


def test_enqueue_commit_crash_is_recovered_without_request_thread_runner(
    tmp_path: Path,
) -> None:
    def crash_after_enqueue(_job):
        raise _SimulatedCrash()

    stack = _build_stack(tmp_path, after_enqueue_hook=crash_after_enqueue)
    service, repository, grant_store, queue, companion, _clock, started, action = stack
    with pytest.raises(_SimulatedCrash):
        service.materials_run_act(run_id=started.run_id, action=action)
    assert companion.action_calls == 0
    assert isinstance(repository.get_run(started.run_id).state, InteractionRequiredStateV1)  # type: ignore[union-attr]
    service.after_enqueue_hook = None
    queued = service.materials_run_act(run_id=started.run_id, action=action)
    assert queued.state.status == "RUNNING"
    assert companion.action_calls == 0
    job = _worker(repository, grant_store, queue, companion).run_once(
        worker_id="worker-recover-enqueue",
        lease_seconds=1,
        heartbeat_interval_seconds=1_000,
    )
    assert job is not None and job.status is GatewayJobStatus.SUCCEEDED
    assert companion.action_calls == 1


def test_mcp_dispatcher_act_only_enqueues_until_worker_runs(tmp_path: Path) -> None:
    stack = _build_stack(tmp_path)
    service, repository, grant_store, queue, companion, _clock, started, action = stack
    dispatched = GatewayToolDispatcher(service).dispatch(
        "materials_run_act",
        {"run_id": started.run_id, "action": action},
    )
    assert dispatched["state"]["status"] == "RUNNING"
    assert companion.action_calls == 0
    completed = _worker(repository, grant_store, queue, companion).run_once(
        worker_id="worker-after-mcp-enqueue",
        lease_seconds=1,
        heartbeat_interval_seconds=1_000,
    )
    assert completed is not None and completed.status is GatewayJobStatus.SUCCEEDED
    assert companion.action_calls == 1


def test_worker_can_win_running_commit_race_without_request_error(tmp_path: Path) -> None:
    stack = _build_stack(tmp_path)
    service, repository, grant_store, queue, companion, _clock, started, action = stack
    worker = _worker(repository, grant_store, queue, companion)
    outbox_committed = Event()
    worker_finished = Event()

    def hold_request_thread(_job):
        outbox_committed.set()
        assert worker_finished.wait(timeout=10)

    service.after_enqueue_hook = hold_request_thread
    with ThreadPoolExecutor(max_workers=1) as pool:
        request_future = pool.submit(
            service.materials_run_act,
            run_id=started.run_id,
            action=action,
        )
        assert outbox_committed.wait(timeout=10)
        completed = worker.run_once(
            worker_id="worker-wins-running-race",
            lease_seconds=1,
            heartbeat_interval_seconds=1_000,
        )
        assert completed is not None
        worker_finished.set()
        returned = request_future.result(timeout=10)
    assert returned.state.status == "CANCELLED"
    assert companion.action_calls == 1


@pytest.mark.parametrize(
    ("crash_point", "expect_claim_after_restart"),
    [
        ("after_runner_checkpoint", True),
        ("after_gateway_terminal", True),
        ("after_job_complete", False),
    ],
)
def test_worker_crash_boundaries_do_not_repeat_runner(
    tmp_path: Path,
    crash_point: str,
    expect_claim_after_restart: bool,
) -> None:
    stack = _build_stack(tmp_path)
    service, repository, grant_store, queue, companion, clock, started, action = stack
    service.materials_run_act(run_id=started.run_id, action=action)
    worker = _worker(repository, grant_store, queue, companion)

    def crash(point, _job):
        if point == crash_point:
            raise _SimulatedCrash()

    with pytest.raises(_SimulatedCrash):
        worker.run_once(
            worker_id="worker-before-crash",
            lease_seconds=1,
            heartbeat_interval_seconds=1_000,
            fault_hook=crash,
        )
    assert companion.action_calls == 1
    gateway_database = repository.database_path
    action_database = queue.database_path
    repository.close()
    queue.close()
    repository = SqliteGatewayRepository(gateway_database)
    grant_store = SqliteOneTimeActionGrantStore(action_database)
    queue = SqliteGatewayJobQueue(action_database, clock_ms=clock)
    worker = _worker(repository, grant_store, queue, companion)
    clock.advance(1_001)
    recovered = worker.run_once(
        worker_id="worker-after-restart",
        lease_seconds=1,
        heartbeat_interval_seconds=1_000,
    )
    assert (recovered is not None) is expect_claim_after_restart
    if recovered is not None:
        assert recovered.status is GatewayJobStatus.SUCCEEDED
    assert companion.action_calls == 1
    assert repository.get_run(started.run_id).state.status == "CANCELLED"  # type: ignore[union-attr]


def test_mid_runner_crash_is_blocked_for_manual_recovery_not_replayed(
    tmp_path: Path,
) -> None:
    stack = _build_stack(tmp_path)
    service, repository, grant_store, queue, companion, clock, started, action = stack
    service.materials_run_act(run_id=started.run_id, action=action)
    worker = _worker(repository, grant_store, queue, companion)
    companion.crash_during_action = True
    with pytest.raises(_SimulatedCrash):
        worker.run_once(
            worker_id="worker-mid-runner-crash",
            lease_seconds=1,
            heartbeat_interval_seconds=1_000,
        )
    assert companion.action_calls == 1

    companion.crash_during_action = False
    clock.advance(1_001)
    blocked = worker.run_once(
        worker_id="worker-manual-recovery-block",
        lease_seconds=1,
        heartbeat_interval_seconds=1_000,
    )
    assert blocked is not None
    assert blocked.status is GatewayJobStatus.FAILED
    assert blocked.failure_code == "BLOCKED_MANUAL_RECOVERY"
    assert companion.action_calls == 1
    persisted = repository.get_run(started.run_id)
    assert persisted is not None and persisted.state.status == "FAILED"
    assert persisted.state.public_error_code == "BLOCKED_MANUAL_RECOVERY"  # type: ignore[union-attr]


def test_completed_recovery_seam_reuses_stage_without_second_runner_call(
    tmp_path: Path,
) -> None:
    stack = _build_stack(tmp_path, supports_completed_recovery=True)
    service, repository, grant_store, queue, companion, clock, started, action = stack
    service.materials_run_act(run_id=started.run_id, action=action)
    worker = _worker(repository, grant_store, queue, companion)

    def crash(point, _job):
        if point == "after_companion_before_checkpoint":
            raise _SimulatedCrash()

    with pytest.raises(_SimulatedCrash):
        worker.run_once(
            worker_id="worker-completed-before-checkpoint",
            lease_seconds=1,
            heartbeat_interval_seconds=1_000,
            fault_hook=crash,
        )
    assert (companion.action_calls, companion.runner_calls) == (1, 1)
    clock.advance(1_001)
    recovered = worker.run_once(
        worker_id="worker-completed-recovery",
        lease_seconds=1,
        heartbeat_interval_seconds=1_000,
    )
    assert recovered is not None and recovered.status is GatewayJobStatus.SUCCEEDED
    assert (companion.action_calls, companion.runner_calls) == (2, 1)


def test_partial_completed_recovery_still_blocks_manual_without_rerun(
    tmp_path: Path,
) -> None:
    stack = _build_stack(
        tmp_path,
        supports_completed_recovery=True,
        partial_recovery=True,
    )
    service, repository, grant_store, queue, companion, clock, started, action = stack
    service.materials_run_act(run_id=started.run_id, action=action)
    worker = _worker(repository, grant_store, queue, companion)
    companion.crash_during_action = True
    with pytest.raises(_SimulatedCrash):
        worker.run_once(
            worker_id="worker-partial-stage",
            lease_seconds=1,
            heartbeat_interval_seconds=1_000,
        )
    companion.crash_during_action = False
    clock.advance(1_001)
    blocked = worker.run_once(
        worker_id="worker-partial-recovery",
        lease_seconds=1,
        heartbeat_interval_seconds=1_000,
    )
    assert blocked is not None
    assert blocked.failure_code == "BLOCKED_MANUAL_RECOVERY"
    assert companion.runner_calls == 1


def test_reject_transition_can_be_deterministically_replayed_after_crash(
    tmp_path: Path,
) -> None:
    stack = _build_stack(tmp_path, decision="reject")
    service, repository, grant_store, queue, companion, clock, started, action = stack
    service.materials_run_act(run_id=started.run_id, action=action)
    worker = _worker(repository, grant_store, queue, companion)

    def crash(point, _job):
        if point == "after_companion_before_checkpoint":
            raise _SimulatedCrash()

    with pytest.raises(_SimulatedCrash):
        worker.run_once(
            worker_id="worker-reject-before-checkpoint",
            lease_seconds=1,
            heartbeat_interval_seconds=1_000,
            fault_hook=crash,
        )
    clock.advance(1_001)
    recovered = worker.run_once(
        worker_id="worker-reject-replay",
        lease_seconds=1,
        heartbeat_interval_seconds=1_000,
    )
    assert recovered is not None and recovered.status is GatewayJobStatus.SUCCEEDED
    assert companion.action_calls == 2
    assert companion.runner_calls == 0


def test_live_process_run_lock_prevents_reclaimed_worker_from_running_in_parallel(
    tmp_path: Path,
) -> None:
    stack = _build_stack(tmp_path)
    service, repository, grant_store, queue, companion, clock, started, action = stack
    service.materials_run_act(run_id=started.run_id, action=action)
    context = get_context("spawn")
    ready = context.Event()
    release = context.Event()
    process = context.Process(
        target=_hold_run_lock,
        args=(str(queue.database_path.parent), started.run_id, ready, release),
    )
    process.start()
    assert ready.wait(timeout=10)
    worker = _worker(repository, grant_store, queue, companion)
    with pytest.raises(RuntimeError, match="another live process"):
        worker.run_once(
            worker_id="worker-fenced-by-live-process",
            lease_seconds=1,
            heartbeat_interval_seconds=1_000,
        )
    assert companion.action_calls == 0

    release.set()
    process.join(timeout=10)
    assert process.exitcode == 0
    clock.advance(1_001)
    recovered = worker.run_once(
        worker_id="worker-after-process-death",
        lease_seconds=1,
        heartbeat_interval_seconds=1_000,
    )
    assert recovered is not None and recovered.status is GatewayJobStatus.SUCCEEDED
    assert companion.action_calls == 1


def test_worker_rejects_gateway_revision_drift_before_runner(tmp_path: Path) -> None:
    stack = _build_stack(tmp_path)
    service, repository, grant_store, queue, companion, _clock, started, action = stack
    service.materials_run_act(run_id=started.run_id, action=action)
    current = repository.get_run(started.run_id)
    assert current is not None
    drifted = current.model_copy(
        update={
            "revision": current.revision + 1,
            "state": RunningStateV1(
                progress_percent=5,
                message="unrelated writer changed this run",
            ),
        }
    )
    repository.replace_run(
        drifted,
        expected_revision=current.revision,
        result=None,
    )
    with pytest.raises(RuntimeError, match="revision fencing"):
        _worker(repository, grant_store, queue, companion).run_once(
            worker_id="worker-revision-fenced",
            lease_seconds=1,
            heartbeat_interval_seconds=1_000,
        )
    assert companion.action_calls == 0
