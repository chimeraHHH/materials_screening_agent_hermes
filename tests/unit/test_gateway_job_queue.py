from __future__ import annotations

import sqlite3
from concurrent.futures import ThreadPoolExecutor
from multiprocessing import get_context
from pathlib import Path
from threading import Barrier, Lock

import pytest

from material_agent.gateway.job_queue import (
    GatewayJobStatus,
    JobCheckpointConflictError,
    JobEnqueueConflictError,
    JobFailureAcknowledgementConflictError,
    JobLeaseLostError,
    JobQueuePersistenceError,
    JobTerminalConflictError,
    SqliteGatewayJobQueue,
    canonical_payload_sha256,
)


class ManualClock:
    def __init__(self, value: int = 1_000_000) -> None:
        self.value = value
        self.lock = Lock()

    def __call__(self) -> int:
        with self.lock:
            return self.value

    def advance(self, milliseconds: int) -> None:
        with self.lock:
            self.value += milliseconds


def _enqueue(queue: SqliteGatewayJobQueue, key: str = "request-1"):
    return queue.enqueue(
        queue_name="inspiration",
        job_kind="execute-approved-run",
        idempotency_key=key,
        payload={"run_id": "inspiration-123", "manifest_sha256": "a" * 64},
    )


def _process_claim(database: str, owner: str, start, output) -> None:
    with SqliteGatewayJobQueue(database) as queue:
        start.wait(timeout=10)
        claimed = queue.claim(
            queue_name="inspiration",
            lease_owner=owner,
            lease_seconds=30,
        )
        output.put(None if claimed is None else claimed.job_id)


def test_enqueue_is_canonical_idempotent_and_conflicts_fail_closed(tmp_path: Path) -> None:
    with SqliteGatewayJobQueue(tmp_path / "jobs.sqlite3") as queue:
        first, created = _enqueue(queue)
        replay, replay_created = queue.enqueue(
            queue_name="inspiration",
            job_kind="execute-approved-run",
            idempotency_key="request-1",
            payload={"manifest_sha256": "a" * 64, "run_id": "inspiration-123"},
        )
        assert created is True
        assert replay_created is False
        assert replay == first
        assert first.payload_sha256 == canonical_payload_sha256(first.payload)
        assert [event.event_type for event in queue.list_events(first.job_id)] == ["ENQUEUED"]

        with pytest.raises(JobEnqueueConflictError):
            queue.enqueue(
                queue_name="inspiration",
                job_kind="execute-approved-run",
                idempotency_key="request-1",
                payload={"run_id": "other"},
            )


def test_claim_is_atomic_across_connections_and_only_one_active_lease_exists(tmp_path: Path) -> None:
    database = tmp_path / "jobs.sqlite3"
    with SqliteGatewayJobQueue(database) as setup:
        job, _ = _enqueue(setup)

    barrier = Barrier(2)

    def claim(owner: str):
        with SqliteGatewayJobQueue(database) as queue:
            barrier.wait()
            return queue.claim(queue_name="inspiration", lease_owner=owner, lease_seconds=30)

    with ThreadPoolExecutor(max_workers=2) as pool:
        claimed = list(pool.map(claim, ("worker-a", "worker-b")))

    leases = [item for item in claimed if item is not None]
    assert len(leases) == 1
    assert leases[0].job_id == job.job_id
    assert leases[0].status is GatewayJobStatus.RUNNING
    assert leases[0].attempt_count == 1
    assert leases[0].lease_generation == 1
    assert leases[0].lease_token is not None


def test_claim_is_atomic_across_spawned_processes(tmp_path: Path) -> None:
    database = tmp_path / "jobs.sqlite3"
    with SqliteGatewayJobQueue(database) as setup:
        job, _ = _enqueue(setup, "multiprocess-claim")
    context = get_context("spawn")
    start = context.Event()
    output = context.Queue()
    processes = [
        context.Process(
            target=_process_claim,
            args=(str(database), f"process-{index}", start, output),
        )
        for index in range(4)
    ]
    for process in processes:
        process.start()
    start.set()
    outcomes = [output.get(timeout=15) for _process in processes]
    for process in processes:
        process.join(timeout=15)
        assert process.exitcode == 0
    assert outcomes.count(job.job_id) == 1
    assert outcomes.count(None) == 3


def test_expired_lease_is_reclaimed_with_checkpoint_and_old_worker_is_fenced(tmp_path: Path) -> None:
    clock = ManualClock()
    database = tmp_path / "jobs.sqlite3"
    first = SqliteGatewayJobQueue(database, clock_ms=clock)
    second = SqliteGatewayJobQueue(database, clock_ms=clock)
    job, _ = _enqueue(first)
    old = first.claim(queue_name="inspiration", lease_owner="worker-old", lease_seconds=2)
    assert old is not None and old.lease_token is not None
    checkpointed = first.checkpoint(
        job_id=job.job_id,
        lease_owner="worker-old",
        lease_token=old.lease_token,
        checkpoint={"completed_queries": 3, "cursor": "page-4"},
        expected_sequence=0,
    )
    assert checkpointed.checkpoint_sequence == 1

    clock.advance(2_001)
    reclaimed = second.claim(
        queue_name="inspiration", lease_owner="worker-new", lease_seconds=10
    )
    assert reclaimed is not None and reclaimed.lease_token is not None
    assert reclaimed.job_id == job.job_id
    assert reclaimed.attempt_count == 2
    assert reclaimed.lease_generation == 2
    assert reclaimed.checkpoint == {"completed_queries": 3, "cursor": "page-4"}

    with pytest.raises(JobLeaseLostError):
        first.heartbeat(
            job_id=job.job_id,
            lease_owner="worker-old",
            lease_token=old.lease_token,
            lease_seconds=10,
        )
    with pytest.raises(JobLeaseLostError):
        first.complete(
            job_id=job.job_id,
            lease_owner="worker-old",
            lease_token=old.lease_token,
            result={"incorrect": True},
        )

    complete = second.complete(
        job_id=job.job_id,
        lease_owner="worker-new",
        lease_token=reclaimed.lease_token,
        result={"stage_result_uri": "artifact://stages/inspiration/result.json"},
    )
    assert complete.status is GatewayJobStatus.SUCCEEDED
    assert [event.event_type for event in second.list_events(job.job_id)] == [
        "ENQUEUED",
        "LEASE_CLAIMED",
        "CHECKPOINTED",
        "LEASE_RECLAIMED",
        "COMPLETED",
    ]
    first.close()
    second.close()


def test_heartbeat_checkpoint_cas_and_terminal_retry_are_safe(tmp_path: Path) -> None:
    clock = ManualClock()
    with SqliteGatewayJobQueue(tmp_path / "jobs.sqlite3", clock_ms=clock) as queue:
        job, _ = _enqueue(queue)
        lease = queue.claim(queue_name="inspiration", lease_owner="worker-a", lease_seconds=2)
        assert lease is not None and lease.lease_token is not None
        clock.advance(1_000)
        renewed = queue.heartbeat(
            job_id=job.job_id,
            lease_owner="worker-a",
            lease_token=lease.lease_token,
            lease_seconds=5,
        )
        assert renewed.lease_expires_at_ms == clock() + 5_000
        saved = queue.checkpoint(
            job_id=job.job_id,
            lease_owner="worker-a",
            lease_token=lease.lease_token,
            checkpoint={"phase": "search"},
            expected_sequence=0,
        )
        with pytest.raises(JobCheckpointConflictError):
            queue.checkpoint(
                job_id=job.job_id,
                lease_owner="worker-a",
                lease_token=lease.lease_token,
                checkpoint={"phase": "evidence"},
                expected_sequence=0,
            )
        done = queue.complete(
            job_id=job.job_id,
            lease_owner="worker-a",
            lease_token=lease.lease_token,
            result={"ok": True},
        )
        replay = queue.complete(
            job_id=job.job_id,
            lease_owner="worker-a",
            lease_token=lease.lease_token,
            result={"ok": True},
        )
        assert replay == done
        assert saved.checkpoint_sequence == 1
        with pytest.raises(JobTerminalConflictError):
            queue.complete(
                job_id=job.job_id,
                lease_owner="worker-a",
                lease_token=lease.lease_token,
                result={"ok": False},
            )


def test_fail_is_durable_and_idempotent_after_process_restart(tmp_path: Path) -> None:
    database = tmp_path / "jobs.sqlite3"
    first = SqliteGatewayJobQueue(database)
    job, _ = _enqueue(first, "failure-case")
    lease = first.claim(queue_name="inspiration", lease_owner="worker-a", lease_seconds=30)
    assert lease is not None and lease.lease_token is not None
    failed = first.fail(
        job_id=job.job_id,
        lease_owner="worker-a",
        lease_token=lease.lease_token,
        error_code="TRANSIENT_EXTERNAL_EXHAUSTED",
        error_message="bounded retries were exhausted",
    )
    first.close()

    with SqliteGatewayJobQueue(database) as reopened:
        recovered = reopened.get_job(job.job_id)
        assert recovered == failed
        assert recovered is not None and recovered.status is GatewayJobStatus.FAILED
        assert reopened.fail(
            job_id=job.job_id,
            lease_owner="worker-a",
            lease_token=lease.lease_token,
            error_code="TRANSIENT_EXTERNAL_EXHAUSTED",
            error_message="bounded retries were exhausted",
        ) == failed
        assert reopened.claim(
            queue_name="inspiration", lease_owner="worker-b", lease_seconds=30
        ) is None


def test_concurrent_enqueue_reuses_one_job_and_persisted_tamper_is_detected(tmp_path: Path) -> None:
    database = tmp_path / "jobs.sqlite3"
    with SqliteGatewayJobQueue(database):
        pass
    barrier = Barrier(8)

    def enqueue_once(_index: int):
        with SqliteGatewayJobQueue(database) as queue:
            barrier.wait()
            return _enqueue(queue, "concurrent-enqueue")

    with ThreadPoolExecutor(max_workers=8) as pool:
        outcomes = list(pool.map(enqueue_once, range(8)))
    assert sum(created for _job, created in outcomes) == 1
    assert len({job.job_id for job, _created in outcomes}) == 1
    job_id = outcomes[0][0].job_id

    connection = sqlite3.connect(database)
    connection.execute(
        "UPDATE gateway_jobs SET payload_json=? WHERE job_id=?",
        ('{"tampered":true}', job_id),
    )
    connection.commit()
    connection.close()
    with (
        SqliteGatewayJobQueue(database) as reopened,
        pytest.raises(JobQueuePersistenceError, match="payload SHA-256"),
    ):
        reopened.get_job(job_id)


def test_attempt_ledger_records_monotonic_hard_deadline_and_acknowledgement(
    tmp_path: Path,
) -> None:
    clock = ManualClock()
    database = tmp_path / "jobs.sqlite3"
    with SqliteGatewayJobQueue(database, clock_ms=clock) as queue:
        job, _ = _enqueue(queue, "hard-deadline")
        clock.advance(25)
        lease = queue.claim(
            queue_name="inspiration",
            lease_owner="worker-supervisor",
            lease_seconds=30,
        )
        assert lease is not None and lease.lease_token is not None
        started = queue.begin_attempt_execution(
            job_id=job.job_id,
            lease_owner="worker-supervisor",
            lease_token=lease.lease_token,
            deadline_budget_ms=300_000,
        )
        assert started.queue_wait_ms == 25
        assert started.deadline_budget_ms == 300_000
        queue.mark_child_started(
            job_id=job.job_id,
            lease_owner="worker-supervisor",
            lease_token=lease.lease_token,
        )
        for _ in range(30):
            clock.advance(10_000)
            queue.heartbeat(
                job_id=job.job_id,
                lease_owner="worker-supervisor",
                lease_token=lease.lease_token,
                lease_seconds=30,
            )
        clock.advance(1)
        timed = queue.record_attempt_timing(
            job_id=job.job_id,
            lease_owner="worker-supervisor",
            lease_token=lease.lease_token,
            worker_active_ms=300_001,
            child_active_ms=300_000,
            termination_grace_ms=1,
            child_exit_code=-15,
            child_signal=15,
            timeout_phase="hard-deadline",
        )
        assert timed.duration_source == "MONOTONIC"
        failed = queue.fail(
            job_id=job.job_id,
            lease_owner="worker-supervisor",
            lease_token=lease.lease_token,
            error_code="HARD_DEADLINE_EXPIRED",
            error_message="the supervised action exceeded its frozen deadline",
        )
        attempts = queue.list_attempts(job.job_id)
        assert failed.status is GatewayJobStatus.FAILED
        assert len(attempts) == 1
        assert attempts[0].outcome == "HARD_DEADLINE_EXPIRED"
        assert attempts[0].worker_active_ms == 300_001
        assert attempts[0].child_active_ms == 300_000

        with pytest.raises(JobFailureAcknowledgementConflictError):
            queue.acknowledge_terminal_failure(
                job_id=job.job_id,
                expected_failure_code="BLOCKED_MANUAL_RECOVERY",
                actor="operator-release",
                reason="stale failure identity",
            )

        acknowledged = queue.acknowledge_terminal_failure(
            job_id=job.job_id,
            expected_failure_code="HARD_DEADLINE_EXPIRED",
            actor="operator-release",
            reason="inspected immutable partial artifacts and opened a fresh run",
        )
        replay = queue.acknowledge_terminal_failure(
            job_id=job.job_id,
            expected_failure_code="HARD_DEADLINE_EXPIRED",
            actor="operator-release",
            reason="inspected immutable partial artifacts and opened a fresh run",
        )
        assert replay == acknowledged
        assert acknowledged.failure_acknowledged_at_ms == clock()
        with pytest.raises(JobFailureAcknowledgementConflictError):
            queue.acknowledge_terminal_failure(
                job_id=job.job_id,
                expected_failure_code="HARD_DEADLINE_EXPIRED",
                actor="different-operator",
                reason="different replay",
            )
        assert [event.event_type for event in queue.list_events(job.job_id)].count(
            "TERMINAL_FAILURE_ACKNOWLEDGED"
        ) == 1


def test_reclaimed_attempt_preserves_wall_clock_fallback_and_stale_token_fencing(
    tmp_path: Path,
) -> None:
    clock = ManualClock()
    with SqliteGatewayJobQueue(tmp_path / "jobs.sqlite3", clock_ms=clock) as queue:
        job, _ = _enqueue(queue, "crashed-attempt")
        old = queue.claim(
            queue_name="inspiration", lease_owner="worker-old", lease_seconds=2
        )
        assert old is not None and old.lease_token is not None
        queue.begin_attempt_execution(
            job_id=job.job_id,
            lease_owner="worker-old",
            lease_token=old.lease_token,
            deadline_budget_ms=5_000,
        )
        clock.advance(2_001)
        new = queue.claim(
            queue_name="inspiration", lease_owner="worker-new", lease_seconds=30
        )
        assert new is not None and new.lease_token is not None
        attempts = queue.list_attempts(job.job_id)
        assert [attempt.outcome for attempt in attempts] == ["LEASE_EXPIRED", "ACTIVE"]
        assert attempts[0].duration_source == "WALL_CLOCK_FALLBACK"
        assert attempts[0].worker_active_ms == 2_001
        with pytest.raises(JobLeaseLostError):
            queue.record_attempt_timing(
                job_id=job.job_id,
                lease_owner="worker-old",
                lease_token=old.lease_token,
                worker_active_ms=2_001,
            )


def test_safe_release_allows_immediate_recovery_and_fences_old_token(
    tmp_path: Path,
) -> None:
    with SqliteGatewayJobQueue(tmp_path / "jobs.sqlite3") as queue:
        job, _ = _enqueue(queue, "graceful-worker-stop")
        old = queue.claim(
            queue_name="inspiration", lease_owner="worker-old", lease_seconds=30
        )
        assert old is not None and old.lease_token is not None
        queue.begin_attempt_execution(
            job_id=job.job_id,
            lease_owner="worker-old",
            lease_token=old.lease_token,
            deadline_budget_ms=300_000,
        )
        queue.record_attempt_timing(
            job_id=job.job_id,
            lease_owner="worker-old",
            lease_token=old.lease_token,
            worker_active_ms=10,
            timeout_phase="worker-shutdown",
        )
        released = queue.release(
            job_id=job.job_id,
            lease_owner="worker-old",
            lease_token=old.lease_token,
            reason="worker-shutdown",
        )
        assert released.status is GatewayJobStatus.READY
        assert queue.list_attempts(job.job_id)[0].outcome == "RELEASED"
        recovered = queue.claim(
            queue_name="inspiration", lease_owner="worker-new", lease_seconds=30
        )
        assert recovered is not None and recovered.lease_generation == 2
        with pytest.raises(JobLeaseLostError):
            queue.heartbeat(
                job_id=job.job_id,
                lease_owner="worker-old",
                lease_token=old.lease_token,
                lease_seconds=30,
            )


def test_attempt_ledger_tamper_and_incomplete_v2_schema_fail_closed(
    tmp_path: Path,
) -> None:
    database = tmp_path / "tampered.sqlite3"
    with SqliteGatewayJobQueue(database) as queue:
        job, _ = _enqueue(queue, "attempt-tamper")
        lease = queue.claim(
            queue_name="inspiration", lease_owner="worker-a", lease_seconds=30
        )
        assert lease is not None

    connection = sqlite3.connect(database)
    connection.execute(
        "UPDATE gateway_job_attempts SET outcome='FORGED' WHERE job_id=?",
        (job.job_id,),
    )
    connection.commit()
    connection.close()
    with (
        SqliteGatewayJobQueue(database) as reopened,
        pytest.raises(JobQueuePersistenceError, match="attempt is invalid"),
    ):
        reopened.list_attempts(job.job_id)

    incomplete = tmp_path / "incomplete.sqlite3"
    with SqliteGatewayJobQueue(incomplete):
        pass
    connection = sqlite3.connect(incomplete)
    connection.execute("DROP TABLE gateway_job_attempts")
    connection.execute(
        "CREATE TABLE gateway_job_attempts(job_id TEXT, lease_generation INTEGER)"
    )
    connection.commit()
    connection.close()
    with pytest.raises(JobQueuePersistenceError, match="schema is incomplete"):
        SqliteGatewayJobQueue(incomplete)


@pytest.mark.parametrize(
    "statement",
    (
        "DROP TABLE gateway_jobs",
        "DROP TABLE gateway_job_events",
        "DROP TABLE gateway_job_attempts",
        "DELETE FROM gateway_job_queue_metadata WHERE key='schema_version'",
    ),
)
def test_versioned_queue_never_recreates_missing_authoritative_state(
    tmp_path: Path,
    statement: str,
) -> None:
    database = tmp_path / (statement.split()[2].replace("'", "") + ".sqlite3")
    with SqliteGatewayJobQueue(database) as queue:
        _enqueue(queue, "durable-before-schema-loss")
    connection = sqlite3.connect(database)
    connection.execute(statement)
    connection.commit()
    connection.close()

    with pytest.raises(JobQueuePersistenceError):
        SqliteGatewayJobQueue(database)
    connection = sqlite3.connect(database)
    missing_name = statement.split()[2].strip("'")
    if statement.startswith("DROP TABLE"):
        assert connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
            (missing_name,),
        ).fetchone() is None
    else:
        assert connection.execute(
            "SELECT value FROM gateway_job_queue_metadata WHERE key='schema_version'"
        ).fetchone() is None
    connection.close()


def test_versioned_queue_rejects_same_name_index_with_wrong_definition(
    tmp_path: Path,
) -> None:
    database = tmp_path / "wrong-index.sqlite3"
    with SqliteGatewayJobQueue(database):
        pass
    connection = sqlite3.connect(database)
    connection.execute("DROP INDEX gateway_jobs_claim_idx")
    connection.execute(
        "CREATE INDEX gateway_jobs_claim_idx ON gateway_jobs(queue_name)"
    )
    connection.commit()
    connection.close()

    with pytest.raises(JobQueuePersistenceError, match="index definition differs"):
        SqliteGatewayJobQueue(database)


def test_v1_live_lease_migrates_with_explicit_fallback_attempt(
    tmp_path: Path,
) -> None:
    database = tmp_path / "v1-live-lease.sqlite3"
    with SqliteGatewayJobQueue(database) as queue:
        job, _ = _enqueue(queue, "v1-live-lease")
        lease = queue.claim(
            queue_name="inspiration",
            lease_owner="legacy-worker",
            lease_seconds=30,
        )
        assert lease is not None and lease.lease_token is not None

    connection = sqlite3.connect(database)
    connection.execute("DROP TABLE gateway_job_attempts")
    for column in (
        "failure_acknowledged_at_ms",
        "failure_acknowledged_by",
        "failure_acknowledgement_reason",
    ):
        connection.execute(f"ALTER TABLE gateway_jobs DROP COLUMN {column}")
    connection.execute(
        "UPDATE gateway_job_queue_metadata SET value='1' "
        "WHERE key='schema_version'"
    )
    connection.commit()
    connection.close()

    with SqliteGatewayJobQueue(database) as migrated:
        completed = migrated.complete(
            job_id=job.job_id,
            lease_owner="legacy-worker",
            lease_token=lease.lease_token,
            result={"migrated": True},
        )
        attempt = migrated.list_attempts(job.job_id)[0]
        assert completed.status is GatewayJobStatus.SUCCEEDED
        assert attempt.outcome == "SUCCEEDED"
        assert attempt.duration_source == "WALL_CLOCK_FALLBACK"
