"""Process supervision for request-specific queued Gateway action deadlines."""

from __future__ import annotations

import hashlib
import json
import math
import os
import signal
import subprocess
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from material_agent.gateway.companion import CompletedInspirationRunRecovery
from material_agent.gateway.errors import AdapterContractError
from material_agent.gateway.job_queue import (
    GatewayJob,
    JobLeaseLostError,
)
from material_agent.gateway.models import (
    CompanionTransitionV1,
    canonical_json_bytes,
)
from material_agent.gateway.service import GatewayActionWorker

_INPUT_SCHEMA = "materials-gateway-supervised-action-input-v1"
_OUTPUT_SCHEMA = "materials-gateway-supervised-action-output-v1"
_MAX_PROTOCOL_BYTES = 1_000_000
_MAX_ORPHAN_PROTOCOL_FILES = 128
_MAX_ORPHAN_PROTOCOL_BYTES = 16_000_000
_MAX_PROTOCOL_DIRECTORIES = 512

# SIGTERM grace (at most two seconds) + a one-second forced-reap guard leaves
# headroom for the supervisor poll.  The production lifecycle may rely on this
# bound when asking an active worker to stop.
MAX_SUPERVISOR_SHUTDOWN_SECONDS = 4.0


class GatewayActionSupervisorError(RuntimeError):
    """A supervised action violated its process, timing, or protocol boundary."""


@dataclass(frozen=True)
class _ChildResult:
    kind: str
    returncode: int
    transition: CompanionTransitionV1 | None = None
    error_code: str | None = None
    child_active_ms: int = 0
    termination_grace_ms: int = 0
    stopped: bool = False
    timed_out: bool = False
    lease_lost: bool = False
    child_start_recorded: bool = False


ChildCommandBuilder = Callable[[Path, Path], Sequence[str]]


class GatewayActionProcessSupervisor:
    """Claim one action, supervise an isolated child, then commit deterministically.

    A short renewable lease controls queue ownership.  The frozen request's
    ``max_walltime_seconds`` independently controls an absolute monotonic child
    deadline.  The parent is the only process allowed to checkpoint or commit
    the Gateway transition.
    """

    def __init__(
        self,
        *,
        worker: GatewayActionWorker,
        attempt_root: Path,
        child_command_builder: ChildCommandBuilder,
        lease_seconds: int = 30,
        heartbeat_interval_seconds: float = 5.0,
        kill_grace_seconds: float = 2.0,
        poll_interval_seconds: float = 0.05,
        monotonic_clock: Callable[[], float] = time.monotonic,
        sleeper: Callable[[float], None] = time.sleep,
        process_factory: Callable[..., subprocess.Popen[bytes]] = subprocess.Popen,
    ) -> None:
        if not 2 <= lease_seconds <= 3_600:
            raise ValueError("lease_seconds must be between 2 and 3600")
        if not 0 < heartbeat_interval_seconds < lease_seconds / 2:
            raise ValueError("heartbeat interval must be less than half the lease")
        if not 0 < kill_grace_seconds <= 2:
            raise ValueError("kill grace must be greater than zero and at most 2 seconds")
        if not 0.01 <= poll_interval_seconds <= 0.25:
            raise ValueError("poll interval must be between 0.01 and 0.25 seconds")
        if not callable(child_command_builder) or not callable(monotonic_clock):
            raise TypeError("supervisor callbacks must be callable")
        if not callable(sleeper) or not callable(process_factory):
            raise TypeError("supervisor process callbacks must be callable")
        if attempt_root.is_symlink():
            raise GatewayActionSupervisorError("supervisor attempt root cannot be a symlink")
        attempt_root.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(attempt_root, 0o700)
        if not attempt_root.is_dir():
            raise GatewayActionSupervisorError("supervisor attempt root is unavailable")
        self.worker = worker
        self.queue = worker.action_queue
        self.attempt_root = attempt_root.resolve()
        self.child_command_builder = child_command_builder
        self.lease_seconds = lease_seconds
        self.heartbeat_interval_seconds = float(heartbeat_interval_seconds)
        self.kill_grace_seconds = float(kill_grace_seconds)
        self.poll_interval_seconds = float(poll_interval_seconds)
        self.monotonic_clock = monotonic_clock
        self.sleeper = sleeper
        self.process_factory = process_factory

    def run_once(
        self,
        *,
        worker_id: str,
        stop_requested: Callable[[], bool] | None = None,
    ) -> GatewayJob | None:
        stop_requested = stop_requested or (lambda: False)
        job = self.queue.claim(
            queue_name="gateway-actions",
            lease_owner=worker_id,
            lease_seconds=self.lease_seconds,
        )
        if job is None:
            return None
        if job.lease_token is None:
            raise GatewayActionSupervisorError("claimed job has no lease token")
        worker_started = self._now()
        envelope = self.worker._validate_job(job)
        initial_record = self.worker.repository.get_run(envelope["run_id"])
        if initial_record is None:
            raise GatewayActionSupervisorError("queued action run disappeared")
        if initial_record.request_sha256 != envelope["request_sha256"]:
            raise GatewayActionSupervisorError("queued action request SHA-256 differs")
        budget_ms = initial_record.request.constraints.budget.max_walltime_seconds * 1_000
        self.queue.begin_attempt_execution(
            job_id=job.job_id,
            lease_owner=worker_id,
            lease_token=job.lease_token,
            deadline_budget_ms=budget_ms,
        )
        hard_deadline = worker_started + budget_ms / 1_000

        record = self.worker._ensure_gateway_running(
            job,
            envelope,
            lease_seconds=self.lease_seconds,
            enforce_lease_covers_walltime=False,
        )
        checkpoint_preexisting = job.checkpoint is not None
        checkpoint = job.checkpoint
        if checkpoint is None:
            job = self.queue.checkpoint(
                job_id=job.job_id,
                lease_owner=worker_id,
                lease_token=job.lease_token,
                checkpoint={
                    "gateway_revision": record.revision,
                    "phase": "GATEWAY_RUNNING",
                },
                expected_sequence=job.checkpoint_sequence,
            )
            checkpoint = job.checkpoint

        transition = self.worker._transition_from_checkpoint(checkpoint)
        if transition is not None:
            try:
                transition = self.worker.validator._validate_transition(
                    transition,
                    run_id=record.run_id,
                    request=record.request,
                    previous_interaction_id=envelope[
                        "interaction"
                    ].interaction_id,
                )
            except AdapterContractError:
                self._record_timing(
                    job=job,
                    worker_id=worker_id,
                    worker_started=worker_started,
                    child_active_ms=None,
                    recovery_ms=0,
                    termination_grace_ms=0,
                    child_returncode=None,
                    timeout_phase="manual-recovery",
                )
                return self.worker._block_manual_recovery(
                    job=job,
                    envelope=envelope,
                    worker_id=worker_id,
                )
            return self._finish_transition(
                job=job,
                envelope=envelope,
                transition=transition,
                worker_id=worker_id,
                worker_started=worker_started,
                child_active_ms=None,
                recovery_ms=0,
                termination_grace_ms=0,
                child_returncode=None,
            )

        recovery_only = bool(
            job.attempt_count > 1
            and checkpoint_preexisting
            and checkpoint is not None
            and checkpoint.get("phase") == "GATEWAY_RUNNING"
        )
        completed_recovery = isinstance(
            getattr(self.worker.companion, "projector", None),
            CompletedInspirationRunRecovery,
        )
        deterministic_replay = envelope["action"].kind in {"reject", "cancel"}
        if recovery_only and not (completed_recovery or deterministic_replay):
            return self.worker._block_manual_recovery(
                job=job,
                envelope=envelope,
                worker_id=worker_id,
            )

        total_child_ms = 0
        recovery_ms = 0
        total_grace_ms = 0
        last_returncode: int | None = None
        for child_index in range(2):
            if stop_requested():
                self._record_timing(
                    job=job,
                    worker_id=worker_id,
                    worker_started=worker_started,
                    child_active_ms=(total_child_ms or None),
                    recovery_ms=recovery_ms,
                    termination_grace_ms=total_grace_ms,
                    child_returncode=last_returncode,
                    timeout_phase="worker-shutdown",
                )
                return self.queue.release(
                    job_id=job.job_id,
                    lease_owner=worker_id,
                    lease_token=job.lease_token,
                    reason="worker-shutdown",
                )
            remaining = hard_deadline - self._now()
            if remaining <= 0:
                return self._hard_deadline_failure(
                    job=job,
                    envelope=envelope,
                    worker_id=worker_id,
                    worker_started=worker_started,
                    total_child_ms=total_child_ms,
                    recovery_ms=recovery_ms,
                    total_grace_ms=total_grace_ms,
                    child_returncode=last_returncode,
                )
            child_started = self._now()
            child = self._run_child(
                job=job,
                record=record,
                envelope=envelope,
                child_index=child_index,
                hard_deadline=hard_deadline,
                worker_id=worker_id,
                stop_requested=stop_requested,
            )
            elapsed = (
                max(child.child_active_ms, self._elapsed_ms(child_started))
                if child.child_start_recorded
                else 0
            )
            total_child_ms += elapsed
            total_grace_ms += child.termination_grace_ms
            last_returncode = child.returncode
            if child_index == 1:
                recovery_ms += elapsed
            if child.lease_lost:
                return self.queue.get_job(job.job_id)
            if child.stopped:
                self._record_timing(
                    job=job,
                    worker_id=worker_id,
                    worker_started=worker_started,
                    child_active_ms=total_child_ms,
                    recovery_ms=recovery_ms,
                    termination_grace_ms=total_grace_ms,
                    child_returncode=last_returncode,
                    timeout_phase="worker-shutdown",
                )
                return self.queue.release(
                    job_id=job.job_id,
                    lease_owner=worker_id,
                    lease_token=job.lease_token or "",
                    reason="worker-shutdown",
                )
            if child.timed_out:
                return self._hard_deadline_failure(
                    job=job,
                    envelope=envelope,
                    worker_id=worker_id,
                    worker_started=worker_started,
                    total_child_ms=total_child_ms,
                    recovery_ms=recovery_ms,
                    total_grace_ms=total_grace_ms,
                    child_returncode=last_returncode,
                )
            if child.kind == "TRANSITION" and child.transition is not None:
                try:
                    validated_transition = self.worker.validator._validate_transition(
                        child.transition,
                        run_id=record.run_id,
                        request=record.request,
                        previous_interaction_id=envelope[
                            "interaction"
                        ].interaction_id,
                    )
                except AdapterContractError:
                    if recovery_only:
                        self._record_timing(
                            job=job,
                            worker_id=worker_id,
                            worker_started=worker_started,
                            child_active_ms=total_child_ms,
                            recovery_ms=recovery_ms,
                            termination_grace_ms=total_grace_ms,
                            child_returncode=last_returncode,
                            timeout_phase="manual-recovery",
                        )
                        return self.worker._block_manual_recovery(
                            job=job,
                            envelope=envelope,
                            worker_id=worker_id,
                        )
                    validated_transition = self.worker.validator._failure_transition(
                        code="ADAPTER_CONTRACT_ERROR",
                        message=(
                            "inspiration companion returned an invalid transition"
                        ),
                    )
                return self._finish_transition(
                    job=job,
                    envelope=envelope,
                    transition=validated_transition,
                    worker_id=worker_id,
                    worker_started=worker_started,
                    child_active_ms=total_child_ms,
                    recovery_ms=recovery_ms,
                    termination_grace_ms=total_grace_ms,
                    child_returncode=last_returncode,
                )
            if child.error_code == "RUN_LOCK_BUSY":
                self._record_timing(
                    job=job,
                    worker_id=worker_id,
                    worker_started=worker_started,
                    child_active_ms=total_child_ms,
                    recovery_ms=recovery_ms,
                    termination_grace_ms=total_grace_ms,
                    child_returncode=last_returncode,
                    timeout_phase="run-lock-busy",
                )
                return self.queue.get_job(job.job_id)
            if child.kind == "NO_RESULT" and child_index == 0 and self._now() < hard_deadline:
                recovery_only = True
                continue
            if child.error_code == "ADAPTER_CONTRACT_ERROR" and recovery_only:
                self._record_timing(
                    job=job,
                    worker_id=worker_id,
                    worker_started=worker_started,
                    child_active_ms=total_child_ms,
                    recovery_ms=recovery_ms,
                    termination_grace_ms=total_grace_ms,
                    child_returncode=last_returncode,
                    timeout_phase="manual-recovery",
                )
                return self.worker._block_manual_recovery(
                    job=job,
                    envelope=envelope,
                    worker_id=worker_id,
                )
            transition = self.worker.validator._failure_transition(
                code=(
                    "ADAPTER_CONTRACT_ERROR"
                    if child.error_code == "ADAPTER_CONTRACT_ERROR"
                    else "ADAPTER_EXECUTION_ERROR"
                ),
                message=(
                    "inspiration companion returned an invalid transition"
                    if child.error_code == "ADAPTER_CONTRACT_ERROR"
                    else "inspiration companion failed during supervised action"
                ),
            )
            return self._finish_transition(
                job=job,
                envelope=envelope,
                transition=transition,
                worker_id=worker_id,
                worker_started=worker_started,
                child_active_ms=total_child_ms,
                recovery_ms=recovery_ms,
                termination_grace_ms=total_grace_ms,
                child_returncode=last_returncode,
            )
        raise AssertionError("bounded child recovery loop did not return")

    def _run_child(
        self,
        *,
        job: GatewayJob,
        record: Any,
        envelope: dict[str, Any],
        child_index: int,
        hard_deadline: float,
        worker_id: str,
        stop_requested: Callable[[], bool],
    ) -> _ChildResult:
        before_protocol = self._now()
        if before_protocol >= hard_deadline:
            return _ChildResult(
                kind="NO_RESULT",
                returncode=-getattr(signal, "SIGALRM", 14),
                timed_out=True,
            )
        remaining_ms = max(1, math.floor((hard_deadline - before_protocol) * 1_000))
        input_path, output_path = self._protocol_paths(job, child_index)
        process: subprocess.Popen[bytes] | None = None
        try:
            self._write_protocol(
                input_path,
                {
                    "action": envelope["action"].model_dump(mode="json"),
                    "execution_manifest_sha256": job.payload[
                        "execution_manifest_sha256"
                    ],
                    "interaction": envelope["interaction"].model_dump(mode="json"),
                    "request": record.request.model_dump(mode="json"),
                    "request_sha256": record.request_sha256,
                    "run_id": record.run_id,
                    "schema_version": _INPUT_SCHEMA,
                    "timeout_ms": remaining_ms,
                },
            )
            command = tuple(self.child_command_builder(input_path, output_path))
            if (
                not command
                or any(
                    not isinstance(argument, str)
                    or not argument
                    or len(argument) > 4_096
                    for argument in command
                )
            ):
                raise GatewayActionSupervisorError("child command is invalid")
            # Protocol serialization and command construction are inside the
            # request walltime.  Never start a child once that budget is gone.
            if self._now() >= hard_deadline:
                return _ChildResult(
                    kind="NO_RESULT",
                    returncode=-getattr(signal, "SIGALRM", 14),
                    timed_out=True,
                )
            try:
                process = self.process_factory(
                    command,
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    close_fds=True,
                    start_new_session=True,
                )
            except OSError as exc:
                raise GatewayActionSupervisorError(
                    "supervised child could not start"
                ) from exc
            # Recheck immediately after fork/exec.  If the boundary elapsed in
            # that race, kill before a SQLite write or output read can delay us.
            child_started = self._now()
            if child_started >= hard_deadline:
                grace_ms = self._terminate(process)
                return _ChildResult(
                    kind="NO_RESULT",
                    returncode=(
                        process.returncode if process.returncode is not None else -9
                    ),
                    termination_grace_ms=grace_ms,
                    timed_out=True,
                )
            self.queue.mark_child_started(
                job_id=job.job_id,
                lease_owner=worker_id,
                lease_token=job.lease_token or "",
            )
            next_heartbeat = child_started + self.heartbeat_interval_seconds
            while True:
                now = self._now()
                returncode = process.poll()
                # Observation time is authoritative.  A transition first seen at
                # or after the deadline is never accepted, even if the process
                # happened to exit between two polls.
                if now >= hard_deadline:
                    grace_ms = (
                        self._terminate(process)
                        if returncode is None
                        else self._kill_remaining_group(process)
                    )
                    return _ChildResult(
                        kind="NO_RESULT",
                        returncode=(
                            process.returncode if process.returncode is not None else -9
                        ),
                        child_active_ms=self._elapsed_ms(child_started),
                        termination_grace_ms=grace_ms,
                        timed_out=True,
                        child_start_recorded=True,
                    )
                if returncode is not None:
                    if returncode == -getattr(signal, "SIGALRM", 14):
                        grace_ms = self._kill_remaining_group(process)
                        return _ChildResult(
                            kind="NO_RESULT",
                            returncode=returncode,
                            child_active_ms=self._elapsed_ms(child_started),
                            termination_grace_ms=grace_ms,
                            timed_out=True,
                            child_start_recorded=True,
                        )
                    # A future child implementation must not leave descendants
                    # behind after its direct process reports completion.
                    grace_ms = self._kill_remaining_group(process)
                    output = self._read_child_output(output_path)
                    output_status = output.get("status", "NO_RESULT")
                    if not (
                        (output_status == "TRANSITION" and returncode == 0)
                        or (output_status == "ERROR" and returncode == 1)
                    ):
                        output = {"status": "NO_RESULT"}
                    return _ChildResult(
                        kind=output.get("status", "NO_RESULT"),
                        returncode=returncode,
                        transition=output.get("transition"),
                        error_code=output.get("error_code"),
                        child_active_ms=self._elapsed_ms(child_started),
                        termination_grace_ms=grace_ms,
                        child_start_recorded=True,
                    )
                if stop_requested():
                    grace_ms = self._terminate(process)
                    return _ChildResult(
                        kind="NO_RESULT",
                        returncode=process.returncode if process.returncode is not None else -9,
                        child_active_ms=self._elapsed_ms(child_started),
                        termination_grace_ms=grace_ms,
                        stopped=True,
                        child_start_recorded=True,
                    )
                if now >= next_heartbeat:
                    try:
                        self.queue.heartbeat(
                            job_id=job.job_id,
                            lease_owner=worker_id,
                            lease_token=job.lease_token or "",
                            lease_seconds=self.lease_seconds,
                        )
                    except JobLeaseLostError:
                        grace_ms = self._terminate(process)
                        return _ChildResult(
                            kind="NO_RESULT",
                            returncode=(
                                process.returncode if process.returncode is not None else -9
                            ),
                            child_active_ms=self._elapsed_ms(child_started),
                            termination_grace_ms=grace_ms,
                            lease_lost=True,
                            child_start_recorded=True,
                        )
                    next_heartbeat = now + self.heartbeat_interval_seconds
                delay = min(
                    self.poll_interval_seconds,
                    max(0.0, next_heartbeat - now),
                    max(0.0, hard_deadline - now),
                )
                if delay > 0:
                    self.sleeper(delay)
        finally:
            # This is deliberately BaseException-safe: a SQLite error, an
            # injected/broken clock, a stop callback failure, KeyboardInterrupt,
            # or SystemExit after Popen must still terminate the independent
            # child process group and reap the direct child.
            cleanup_error: BaseException | None = None
            if process is not None:
                try:
                    if process.poll() is None:
                        self._terminate(process)
                    else:
                        self._kill_remaining_group(process)
                except BaseException as exc:  # safety cleanup before propagation  # noqa: BLE001
                    cleanup_error = exc
                    self._force_kill_and_reap(process)
            try:
                self._cleanup_protocol(input_path, output_path)
            except BaseException as exc:  # cleanup is part of the security boundary  # noqa: BLE001
                if cleanup_error is None:
                    cleanup_error = exc
            if cleanup_error is not None:
                raise cleanup_error

    def _finish_transition(
        self,
        *,
        job: GatewayJob,
        envelope: dict[str, Any],
        transition: CompanionTransitionV1,
        worker_id: str,
        worker_started: float,
        child_active_ms: int | None,
        recovery_ms: int,
        termination_grace_ms: int,
        child_returncode: int | None,
    ) -> GatewayJob:
        self._record_timing(
            job=job,
            worker_id=worker_id,
            worker_started=worker_started,
            child_active_ms=child_active_ms,
            recovery_ms=recovery_ms,
            termination_grace_ms=termination_grace_ms,
            child_returncode=child_returncode,
            timeout_phase=None,
        )
        if self.worker._transition_from_checkpoint(job.checkpoint) is None:
            transition_value = transition.model_dump(mode="json")
            job = self.queue.checkpoint(
                job_id=job.job_id,
                lease_owner=worker_id,
                lease_token=job.lease_token or "",
                checkpoint={
                    "gateway_revision": envelope["expected_revision"] + 1,
                    "phase": "TRANSITION_READY",
                    "transition": transition_value,
                    "transition_sha256": hashlib.sha256(
                        canonical_json_bytes(transition_value)
                    ).hexdigest(),
                },
                expected_sequence=job.checkpoint_sequence,
            )
        committed = self.worker._ensure_transition_committed(
            job=job,
            envelope=envelope,
            transition=transition,
        )
        terminal_failure = self.worker._terminal_failure_from_checkpoint(job.checkpoint)
        if terminal_failure is not None:
            return self.queue.fail(
                job_id=job.job_id,
                lease_owner=worker_id,
                lease_token=job.lease_token or "",
                error_code=terminal_failure[0],
                error_message=terminal_failure[1],
            )
        return self.queue.complete(
            job_id=job.job_id,
            lease_owner=worker_id,
            lease_token=job.lease_token or "",
            result={
                "gateway_record_sha256": hashlib.sha256(
                    canonical_json_bytes(committed)
                ).hexdigest(),
                "gateway_revision": committed.revision,
                "run_id": committed.run_id,
            },
        )

    def _hard_deadline_failure(
        self,
        *,
        job: GatewayJob,
        envelope: dict[str, Any],
        worker_id: str,
        worker_started: float,
        total_child_ms: int,
        recovery_ms: int,
        total_grace_ms: int,
        child_returncode: int | None,
    ) -> GatewayJob:
        message = "the supervised action exceeded its frozen hard deadline"
        self._record_timing(
            job=job,
            worker_id=worker_id,
            worker_started=worker_started,
            child_active_ms=(total_child_ms or None),
            recovery_ms=recovery_ms,
            termination_grace_ms=total_grace_ms,
            child_returncode=child_returncode,
            timeout_phase="hard-deadline",
        )
        transition = self.worker.validator._failure_transition(
            code="HARD_DEADLINE_EXPIRED",
            message=message,
        )
        transition_value = transition.model_dump(mode="json")
        job = self.queue.checkpoint(
            job_id=job.job_id,
            lease_owner=worker_id,
            lease_token=job.lease_token or "",
            checkpoint={
                "gateway_revision": envelope["expected_revision"] + 1,
                "phase": "TRANSITION_READY",
                "terminal_disposition": {
                    "error_code": "HARD_DEADLINE_EXPIRED",
                    "error_message": message,
                    "kind": "FAIL",
                },
                "transition": transition_value,
                "transition_sha256": hashlib.sha256(
                    canonical_json_bytes(transition_value)
                ).hexdigest(),
            },
            expected_sequence=job.checkpoint_sequence,
        )
        committed = self.worker._ensure_transition_committed(
            job=job,
            envelope=envelope,
            transition=transition,
        )
        del committed
        return self.queue.fail(
            job_id=job.job_id,
            lease_owner=worker_id,
            lease_token=job.lease_token or "",
            error_code="HARD_DEADLINE_EXPIRED",
            error_message=message,
        )

    def _record_timing(
        self,
        *,
        job: GatewayJob,
        worker_id: str,
        worker_started: float,
        child_active_ms: int | None,
        recovery_ms: int,
        termination_grace_ms: int,
        child_returncode: int | None,
        timeout_phase: str | None,
    ) -> None:
        child_signal = (
            -child_returncode
            if child_returncode is not None and child_returncode < 0
            else None
        )
        self.queue.record_attempt_timing(
            job_id=job.job_id,
            lease_owner=worker_id,
            lease_token=job.lease_token or "",
            worker_active_ms=self._elapsed_ms(worker_started),
            child_active_ms=child_active_ms,
            recovery_ms=recovery_ms,
            termination_grace_ms=termination_grace_ms,
            child_exit_code=child_returncode,
            child_signal=child_signal,
            timeout_phase=timeout_phase,
        )

    def _protocol_paths(self, job: GatewayJob, child_index: int) -> tuple[Path, Path]:
        self._enforce_protocol_quota()
        directory = self.attempt_root / job.job_id / f"attempt-{job.lease_generation}"
        if directory.is_symlink():
            raise GatewayActionSupervisorError("supervisor attempt directory is unsafe")
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(directory, 0o700)
        input_path = directory / f"child-{child_index}.input.json"
        output_path = directory / f"child-{child_index}.output.json"
        if input_path.is_symlink() or output_path.is_symlink() or output_path.exists():
            raise GatewayActionSupervisorError("supervisor protocol path already exists")
        return input_path, output_path

    @staticmethod
    def _write_protocol(path: Path, value: dict[str, Any]) -> None:
        payload = canonical_json_bytes(value)
        if len(payload) > _MAX_PROTOCOL_BYTES:
            raise GatewayActionSupervisorError("supervisor child input exceeds its limit")
        descriptor = os.open(
            path,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
            0o600,
        )
        try:
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
        except Exception:
            try:
                path.unlink()
            except OSError:
                pass
            raise
        directory = os.open(path.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(directory)
        finally:
            os.close(directory)

    @staticmethod
    def _read_child_output(path: Path) -> dict[str, Any]:
        if path.is_symlink() or not path.is_file() or path.stat().st_size > _MAX_PROTOCOL_BYTES:
            return {"status": "NO_RESULT"}
        try:
            value = json.loads(path.read_bytes())
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            return {"status": "NO_RESULT"}
        if not isinstance(value, dict) or value.get("schema_version") != _OUTPUT_SCHEMA:
            return {"status": "NO_RESULT"}
        if value.get("status") == "TRANSITION" and set(value) == {
            "schema_version",
            "status",
            "transition",
        }:
            try:
                transition = CompanionTransitionV1.model_validate_json(
                    canonical_json_bytes(value["transition"])
                )
            except (TypeError, ValueError):
                return {"status": "ERROR", "error_code": "ADAPTER_CONTRACT_ERROR"}
            return {"status": "TRANSITION", "transition": transition}
        if value.get("status") == "ERROR" and set(value) == {
            "error_code",
            "schema_version",
            "status",
        }:
            code = value.get("error_code")
            if isinstance(code, str) and code in {
                "ADAPTER_CONTRACT_ERROR",
                "ADAPTER_EXECUTION_ERROR",
                "CHILD_PROTOCOL_ERROR",
                "RUN_LOCK_BUSY",
                "WORKER_BOUNDARY_ERROR",
            }:
                return {"status": "ERROR", "error_code": code}
        return {"status": "NO_RESULT"}

    def _terminate(self, process: subprocess.Popen[bytes]) -> int:
        # Cleanup cannot depend on the injectable request clock/sleeper: those
        # are precisely among the failures this method must contain.
        started = time.monotonic()
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            process.wait(timeout=1)
            return max(0, math.ceil((time.monotonic() - started) * 1_000))
        grace_deadline = started + self.kill_grace_seconds
        try:
            process.wait(timeout=max(0.0, grace_deadline - time.monotonic()))
        except subprocess.TimeoutExpired:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            try:
                process.wait(timeout=1)
            except subprocess.TimeoutExpired as exc:
                raise GatewayActionSupervisorError(
                    "supervised child could not be reaped after SIGKILL"
                ) from exc
        else:
            # The direct child may handle TERM while a descendant ignores it.
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        return max(0, math.ceil((time.monotonic() - started) * 1_000))

    def _kill_remaining_group(self, process: subprocess.Popen[bytes]) -> int:
        """Best-effort cleanup after alarm/deadline observation.

        The current approved companion never spawns descendants.  The process
        group cleanup keeps that boundary fail-closed if a future implementation
        drifts, while the execution source manifest ensures such drift invalidates
        all prior approvals.
        """

        started = time.monotonic()
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        try:
            process.wait(timeout=1)
        except subprocess.TimeoutExpired as exc:
            raise GatewayActionSupervisorError(
                "supervised child could not be reaped"
            ) from exc
        return max(0, math.ceil((time.monotonic() - started) * 1_000))

    @staticmethod
    def _force_kill_and_reap(process: subprocess.Popen[bytes]) -> None:
        """Last-resort cleanup used while another BaseException is active."""

        try:
            os.killpg(process.pid, signal.SIGKILL)
        except (OSError, ProcessLookupError):
            pass
        try:
            process.wait(timeout=1)
        except BaseException:  # noqa: BLE001, S110
            # There is no safer local escalation beyond SIGKILL + wait.  The
            # caller will surface the original cleanup failure and exit.
            pass

    def _cleanup_protocol(self, *paths: Path) -> None:
        for path in paths:
            try:
                path.relative_to(self.attempt_root)
            except ValueError as exc:
                raise GatewayActionSupervisorError(
                    "supervisor protocol cleanup escaped its root"
                ) from exc
            if path.is_symlink():
                raise GatewayActionSupervisorError(
                    "supervisor protocol cleanup encountered a symlink"
                )
            try:
                path.unlink()
            except FileNotFoundError:
                pass
        directory = paths[0].parent if paths else self.attempt_root
        try:
            descriptor = os.open(
                directory,
                os.O_RDONLY | getattr(os, "O_DIRECTORY", 0),
            )
        except FileNotFoundError:
            return
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        # Normal, timeout, shutdown and exception paths retain only the hashed
        # ops ledger, not a second plaintext copy of the research request.
        for candidate in (directory, directory.parent):
            if candidate == self.attempt_root:
                break
            try:
                candidate.rmdir()
            except (FileNotFoundError, OSError):
                break
        root_descriptor = os.open(
            self.attempt_root,
            os.O_RDONLY | getattr(os, "O_DIRECTORY", 0),
        )
        try:
            os.fsync(root_descriptor)
        finally:
            os.close(root_descriptor)

    def _enforce_protocol_quota(self) -> None:
        """Bound crash residue without silently deleting forensic evidence."""

        files = 0
        directories = 0
        total_bytes = 0
        pending = [self.attempt_root]
        while pending:
            current = pending.pop()
            directories += 1
            if directories > _MAX_PROTOCOL_DIRECTORIES:
                raise GatewayActionSupervisorError(
                    "supervisor protocol directory quota is exhausted"
                )
            try:
                entries = tuple(os.scandir(current))
            except OSError as exc:
                raise GatewayActionSupervisorError(
                    "supervisor protocol residue could not be inspected"
                ) from exc
            for entry in entries:
                if entry.is_symlink():
                    raise GatewayActionSupervisorError(
                        "supervisor protocol residue contains a symlink"
                    )
                if entry.is_dir(follow_symlinks=False):
                    pending.append(Path(entry.path))
                    continue
                if not entry.is_file(follow_symlinks=False):
                    raise GatewayActionSupervisorError(
                        "supervisor protocol residue contains a special file"
                    )
                files += 1
                total_bytes += entry.stat(follow_symlinks=False).st_size
                if (
                    files >= _MAX_ORPHAN_PROTOCOL_FILES
                    or total_bytes >= _MAX_ORPHAN_PROTOCOL_BYTES
                ):
                    raise GatewayActionSupervisorError(
                        "supervisor protocol residue quota is exhausted"
                    )

    def _now(self) -> float:
        value = self.monotonic_clock()
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            raise GatewayActionSupervisorError("monotonic clock is invalid")
        value = float(value)
        if not math.isfinite(value):
            raise GatewayActionSupervisorError("monotonic clock is invalid")
        return value

    def _elapsed_ms(self, started: float) -> int:
        elapsed = self._now() - started
        if elapsed < 0:
            raise GatewayActionSupervisorError("monotonic clock moved backwards")
        return math.ceil(elapsed * 1_000)
