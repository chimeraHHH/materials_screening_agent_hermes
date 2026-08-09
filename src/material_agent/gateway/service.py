"""Pure-Python implementation of the four stable Materials Gateway tools."""

from __future__ import annotations

import hashlib
import hmac
import os
from contextlib import ExitStack
from collections.abc import Mapping
from pathlib import Path
from threading import Event, Thread
from typing import Any, Callable

from pydantic import ValidationError

try:  # local production target is macOS/Linux; unsupported hosts fail closed.
    import fcntl
except ImportError:  # pragma: no cover - exercised only on non-POSIX hosts
    fcntl = None  # type: ignore[assignment]

from material_agent.gateway.authorization import (
    ActionGrantReceipt,
    ActionAuthorizer,
    DenyAllActionAuthorizer,
    SqliteOneTimeActionGrantStore,
)
from material_agent.gateway.companion import CompletedInspirationRunRecovery
from material_agent.gateway.errors import (
    ActionAuthorizationError,
    AdapterContractError,
    ConcurrentUpdateError,
    IllegalActionError,
    ResultIntegrityError,
    ResultUnavailableError,
    RunNotFoundError,
    StaleInteractionError,
    SubmissionConflictError,
)
from material_agent.gateway.job_queue import (
    GatewayJob,
    JobLeaseLostError,
    SqliteGatewayJobQueue,
)
from material_agent.gateway.models import (
    ArtifactReferenceV1,
    CompanionTransitionV1,
    GatewayRunRecordV1,
    InspirationConstraintsV1,
    InspirationRunRequestV1,
    INTERACTION_ADAPTER,
    InteractionRequiredStateV1,
    MaterialsResultGetRequestV1,
    MaterialsResultViewV1,
    MaterialsRunActRequestV1,
    MaterialsRunGetRequestV1,
    MaterialsRunViewV1,
    ReadableReportV1,
    PartialStateV1,
    FailedStateV1,
    RunActionV1,
    RUN_ACTION_ADAPTER,
    RunningStateV1,
    SucceededStateV1,
    canonical_json_bytes,
    gateway_result_sha256,
    inspiration_request_sha256,
    inspiration_report_uri,
    inspiration_run_id,
    run_view,
    terminal_reference,
)
from material_agent.gateway.protocols import (
    ArtifactReader,
    GatewayRepository,
    InspirationCompanionAdapter,
)


class MaterialsGatewayService:
    """Bounded control plane suitable for later MCP decoration.

    ``materials_run_get`` never calls the adapter.  The adapter is invoked only
    for a new idempotent submission or one explicit, currently legal action.
    """

    def __init__(
        self,
        *,
        repository: GatewayRepository,
        companion: InspirationCompanionAdapter,
        artifact_reader: ArtifactReader,
        action_authorizer: ActionAuthorizer | None = None,
        max_report_bytes: int = 1_000_000,
        max_closure_bytes: int = 100_000_000,
        max_readable_report_chars: int = 24_000,
    ) -> None:
        if max_report_bytes < 1:
            raise ValueError("max_report_bytes must be positive")
        if max_closure_bytes < max_report_bytes:
            raise ValueError("max_closure_bytes must cover max_report_bytes")
        if not 1 <= max_readable_report_chars <= 32_000:
            raise ValueError(
                "max_readable_report_chars must be between 1 and 32000"
            )
        self.repository = repository
        self.companion = companion
        self.artifact_reader = artifact_reader
        self.action_authorizer = action_authorizer or DenyAllActionAuthorizer()
        self.max_report_bytes = max_report_bytes
        self.max_closure_bytes = max_closure_bytes
        self.max_readable_report_chars = max_readable_report_chars

    def materials_inspiration_run(
        self,
        *,
        submission_id: str,
        goal: str,
        constraints: InspirationConstraintsV1 | Mapping[str, Any],
    ) -> MaterialsRunViewV1:
        """Create or idempotently recover one companion inspiration run."""

        request = InspirationRunRequestV1.model_validate_json(
            canonical_json_bytes(
                {
                    "submission_id": submission_id,
                    "goal": goal,
                    "constraints": constraints,
                }
            )
        )
        request_sha256 = inspiration_request_sha256(request)
        run_id = inspiration_run_id(request.submission_id)
        initial = GatewayRunRecordV1(
            run_id=run_id,
            request=request,
            request_sha256=request_sha256,
            state=RunningStateV1(
                progress_percent=0,
                message="inspiration submission accepted",
            ),
        )
        persisted, created = self.repository.create_or_get(initial)
        if not created:
            if persisted.request_sha256 != request_sha256:
                raise SubmissionConflictError(
                    "submission_id is already bound to a different request"
                )
            return run_view(persisted)

        try:
            raw_transition = self.companion.start(run_id=run_id, request=request)
            transition = self._validate_transition(
                raw_transition,
                run_id=run_id,
                request=request,
            )
        except AdapterContractError:
            transition = self._failure_transition(
                code="ADAPTER_CONTRACT_ERROR",
                message="inspiration companion returned an invalid transition",
            )
        except Exception:  # adapter traceback and internal details stay private
            transition = self._failure_transition(
                code="ADAPTER_EXECUTION_ERROR",
                message="inspiration companion failed during start",
            )
        committed = self._commit(persisted, transition)
        return run_view(committed)

    def materials_run_get(self, *, run_id: str) -> MaterialsRunViewV1:
        """Return a read-only safe status projection."""

        request = MaterialsRunGetRequestV1(run_id=run_id)
        return run_view(self._require_run(request.run_id))

    def materials_run_act(
        self,
        *,
        run_id: str,
        action: RunActionV1 | Mapping[str, Any],
    ) -> MaterialsRunViewV1:
        """Apply exactly one advertised action to the current interaction."""

        request = MaterialsRunActRequestV1.model_validate_json(
            canonical_json_bytes({"run_id": run_id, "action": action})
        )
        parsed_action = request.action
        record = self._require_run(request.run_id)
        if not isinstance(record.state, InteractionRequiredStateV1):
            raise StaleInteractionError("run has no pending interaction")
        interaction = record.state.interaction
        if parsed_action.interaction_id != interaction.interaction_id:
            raise StaleInteractionError("interaction is stale or belongs to another run")
        if parsed_action.kind not in interaction.allowed_actions:
            raise IllegalActionError(
                f"action {parsed_action.kind!r} is not legal for this interaction"
            )

        try:
            self.action_authorizer.authorize_and_consume(
                run_id=record.run_id,
                interaction=interaction,
                request_sha256=record.request_sha256,
                action=parsed_action,
            )
        except ActionAuthorizationError:
            raise
        except Exception:
            raise ActionAuthorizationError(
                "operator authorization could not be verified"
            ) from None

        try:
            raw_transition = self.companion.act(
                run_id=record.run_id,
                request=record.request,
                state=record.state,
                action=parsed_action,
            )
            transition = self._validate_transition(
                raw_transition,
                run_id=record.run_id,
                request=record.request,
                previous_interaction_id=interaction.interaction_id,
            )
        except AdapterContractError:
            transition = self._failure_transition(
                code="ADAPTER_CONTRACT_ERROR",
                message="inspiration companion returned an invalid transition",
            )
        except Exception:  # an uncertain action outcome must not be retried implicitly
            transition = self._failure_transition(
                code="ADAPTER_EXECUTION_ERROR",
                message="inspiration companion failed during action",
            )
        committed = self._commit(record, transition)
        return run_view(committed)

    def materials_result_get(self, *, run_id: str) -> MaterialsResultViewV1:
        """Return a bounded result only after exact URI/SHA verification."""

        request = MaterialsResultGetRequestV1(run_id=run_id)
        record = self._require_run(request.run_id)
        reference = terminal_reference(record.state)
        if reference is None:
            raise ResultUnavailableError(
                "run is not SUCCEEDED or PARTIAL; no terminal result is available"
            )
        result = self.repository.get_result(request.run_id)
        if result is None:
            raise ResultUnavailableError("terminal run has no complete result record")
        if result.run_id != request.run_id:
            raise ResultIntegrityError("result belongs to a different run")
        if result.report_uri != inspiration_report_uri(request.run_id):
            raise ResultIntegrityError("result report URI is not bound to this run")
        if (result.report_uri, result.authoritative_sha256) != reference[:2]:
            raise ResultIntegrityError("run and result URI/SHA reference differ")
        observed_result_sha256 = gateway_result_sha256(result)
        if not hmac.compare_digest(observed_result_sha256, reference[2]):
            raise ResultIntegrityError(
                "terminal result failed canonical SHA-256 validation"
            )

        closure = result.artifact_closure
        if closure is None:
            raise ResultIntegrityError(
                "terminal result predates full artifact-closure verification"
            )
        declared_total = closure.stage_result.size_bytes + sum(
            item.size_bytes for item in closure.artifacts
        )
        if declared_total > self.max_closure_bytes:
            raise ResultIntegrityError("artifact closure exceeds the total size limit")

        report_payload: bytes | None = None
        for artifact in (closure.stage_result, *closure.artifacts):
            payload = self._read_verified_artifact(artifact)
            if artifact.uri == result.report_uri:
                report_payload = payload
        if report_payload is None:
            raise ResultIntegrityError("artifact closure omitted the bound report")
        if len(report_payload) > self.max_report_bytes:
            raise ResultIntegrityError("bound report artifact exceeds the size limit")
        report_reference = next(
            item for item in closure.artifacts if item.uri == result.report_uri
        )
        if report_reference.media_type != "text/markdown":
            raise ResultIntegrityError("bound report artifact is not Markdown")
        try:
            full_report = report_payload.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ResultIntegrityError("bound report artifact is not UTF-8") from exc
        if not full_report:
            raise ResultIntegrityError("bound report artifact is empty")
        readable_content = full_report[: self.max_readable_report_chars]
        readable_report = ReadableReportV1(
            content=readable_content,
            returned_char_count=len(readable_content),
            original_char_count=len(full_report),
            original_size_bytes=len(report_payload),
            full_content_sha256=result.authoritative_sha256,
            truncated=len(readable_content) < len(full_report),
        )
        return MaterialsResultViewV1.model_validate_json(
            canonical_json_bytes(
                {
                    **result.model_dump(mode="json"),
                    "result_sha256": observed_result_sha256,
                    "readable_report": readable_report,
                    "verified": True,
                }
            )
        )

    def _read_verified_artifact(self, artifact: ArtifactReferenceV1) -> bytes:
        try:
            payload = self.artifact_reader.read_bytes(artifact.uri)
        except Exception as exc:
            raise ResultIntegrityError(
                f"artifact closure member is unavailable: {artifact.uri}"
            ) from exc
        if len(payload) != artifact.size_bytes:
            raise ResultIntegrityError(
                f"artifact closure member size differs: {artifact.uri}"
            )
        observed_sha256 = hashlib.sha256(payload).hexdigest()
        if not hmac.compare_digest(observed_sha256, artifact.sha256):
            raise ResultIntegrityError(
                f"artifact closure member SHA-256 differs: {artifact.uri}"
            )
        return payload

    def _require_run(self, run_id: str) -> GatewayRunRecordV1:
        record = self.repository.get_run(run_id)
        if record is None:
            raise RunNotFoundError(f"materials run {run_id!r} was not found")
        return record

    def _validate_transition(
        self,
        raw_transition: CompanionTransitionV1 | Mapping[str, Any],
        *,
        run_id: str,
        request: InspirationRunRequestV1,
        previous_interaction_id: str | None = None,
    ) -> CompanionTransitionV1:
        try:
            if isinstance(raw_transition, CompanionTransitionV1):
                transition = raw_transition
            else:
                transition = CompanionTransitionV1.model_validate_json(
                    canonical_json_bytes(raw_transition)
                )
        except (TypeError, ValueError, ValidationError) as exc:
            raise AdapterContractError(
                "inspiration companion returned an invalid transition"
            ) from exc

        if isinstance(transition.state, (SucceededStateV1, PartialStateV1)):
            if transition.result is None:
                raise AdapterContractError(
                    "terminal inspiration transition is missing its result"
                )
            if not hmac.compare_digest(
                gateway_result_sha256(transition.result),
                transition.state.result_sha256,
            ):
                raise AdapterContractError(
                    "terminal inspiration state does not bind its canonical result"
                )
            if transition.result.run_id != run_id:
                raise AdapterContractError(
                    "terminal inspiration result belongs to a different run"
                )
            expected_uri = inspiration_report_uri(run_id)
            if transition.state.report_uri != expected_uri:
                raise AdapterContractError(
                    "terminal inspiration report URI is not bound to its run"
                )
            budget = request.constraints.budget
            result = transition.result
            if len(result.bundle.selected_candidates) > request.constraints.top_k:
                raise AdapterContractError("terminal result exceeds requested top_k")
            ledger = result.cost_ledger
            if ledger.search_requests > budget.max_search_requests:
                raise AdapterContractError("terminal result exceeds search budget")
            if ledger.fetched_documents > budget.max_unique_documents:
                raise AdapterContractError("terminal result exceeds document budget")
            if (
                ledger.extracted_passages > budget.max_passages
                or ledger.vectorized_passages > budget.max_passages
            ):
                raise AdapterContractError("terminal result exceeds passage budget")
            if ledger.model_calls > budget.max_model_calls:
                raise AdapterContractError("terminal result exceeds model-call budget")
            if ledger.walltime_ms > budget.max_walltime_seconds * 1_000:
                raise AdapterContractError("terminal result exceeds walltime budget")
        elif transition.result is not None:
            raise AdapterContractError("non-terminal transition cannot carry a result")

        if (
            previous_interaction_id is not None
            and isinstance(transition.state, InteractionRequiredStateV1)
            and transition.state.interaction.interaction_id
            == previous_interaction_id
        ):
            raise AdapterContractError(
                "a new interaction must use a fresh interaction_id"
            )
        return transition

    @staticmethod
    def _failure_transition(*, code: str, message: str) -> CompanionTransitionV1:
        return CompanionTransitionV1(
            state=FailedStateV1(
                public_error_code=code,
                public_message=message,
                retryable=False,
            )
        )

    def _commit(
        self,
        record: GatewayRunRecordV1,
        transition: CompanionTransitionV1,
    ) -> GatewayRunRecordV1:
        replacement = record.model_copy(
            update={
                "state": transition.state,
                "revision": record.revision + 1,
            }
        )
        return self.repository.replace_run(
            replacement,
            expected_revision=record.revision,
            result=transition.result,
        )


GATEWAY_ACTION_QUEUE = "gateway-actions"


class GatewayActionWorkerError(RuntimeError):
    """A durable action job failed its receipt, state, or revision fencing."""


class _RunScopedProcessLock:
    """Prevent two live local processes from running one artifact namespace."""

    def __init__(self, root: Path, run_id: str) -> None:
        self.root = root / "gateway-action-run-locks"
        self.name = hashlib.sha256(run_id.encode("utf-8")).hexdigest() + ".lock"
        self.fd: int | None = None

    def __enter__(self) -> _RunScopedProcessLock:
        if fcntl is None:
            raise GatewayActionWorkerError(
                "run-scoped worker locking is unavailable on this platform"
            )
        if self.root.is_symlink():
            raise GatewayActionWorkerError("run-lock directory cannot be a symlink")
        self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(self.root, 0o700)
        path = self.root / self.name
        if path.is_symlink():
            raise GatewayActionWorkerError("run-lock path cannot be a symlink")
        flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0)
        self.fd = os.open(path, flags, 0o600)
        os.fchmod(self.fd, 0o600)
        try:
            if path.is_symlink():
                raise GatewayActionWorkerError("run-lock path became a symlink")
            fcntl.flock(self.fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            os.close(self.fd)
            self.fd = None
            raise GatewayActionWorkerError(
                "another live process still owns this run's artifact namespace"
            ) from exc
        except Exception:
            os.close(self.fd)
            self.fd = None
            raise
        return self

    def __exit__(self, *_args: object) -> None:
        if self.fd is not None:
            assert fcntl is not None
            fcntl.flock(self.fd, fcntl.LOCK_UN)
            os.close(self.fd)
            self.fd = None


def _queued_action_state(job_id: str) -> RunningStateV1:
    return RunningStateV1(
        progress_percent=0,
        message=f"queued durable action {job_id}",
    )


class QueuedMaterialsGatewayService(MaterialsGatewayService):
    """Async production act service; submissions and reads remain unchanged.

    ``materials_run_act`` consumes the exact operator grant and appends an
    action job in one approval-database transaction.  It never invokes the
    companion in the request thread.  ``MaterialsGatewayService`` remains the
    backward-compatible synchronous implementation.
    """

    def __init__(
        self,
        *,
        repository: GatewayRepository,
        companion: InspirationCompanionAdapter,
        artifact_reader: ArtifactReader,
        grant_store: SqliteOneTimeActionGrantStore,
        action_queue: SqliteGatewayJobQueue,
        after_enqueue_hook: Callable[[GatewayJob], None] | None = None,
        max_report_bytes: int = 1_000_000,
        max_closure_bytes: int = 100_000_000,
        max_readable_report_chars: int = 24_000,
    ) -> None:
        if grant_store.database_path.resolve() != action_queue.database_path.resolve():
            raise ValueError(
                "grant store and action queue must use the same SQLite database"
            )
        super().__init__(
            repository=repository,
            companion=companion,
            artifact_reader=artifact_reader,
            action_authorizer=grant_store,
            max_report_bytes=max_report_bytes,
            max_closure_bytes=max_closure_bytes,
            max_readable_report_chars=max_readable_report_chars,
        )
        self.grant_store = grant_store
        self.action_queue = action_queue
        self.after_enqueue_hook = after_enqueue_hook

    def materials_run_act(
        self,
        *,
        run_id: str,
        action: RunActionV1 | Mapping[str, Any],
    ) -> MaterialsRunViewV1:
        request = MaterialsRunActRequestV1.model_validate_json(
            canonical_json_bytes({"run_id": run_id, "action": action})
        )
        parsed_action = request.action
        record = self._require_run(request.run_id)
        if not isinstance(record.state, InteractionRequiredStateV1):
            raise StaleInteractionError("run has no pending interaction")
        interaction = record.state.interaction
        if parsed_action.interaction_id != interaction.interaction_id:
            raise StaleInteractionError(
                "interaction is stale or belongs to another run"
            )
        if parsed_action.kind not in interaction.allowed_actions:
            raise IllegalActionError(
                f"action {parsed_action.kind!r} is not legal for this interaction"
            )
        try:
            _receipt, job = self.grant_store.authorize_consume_and_enqueue(
                run_id=record.run_id,
                run_revision=record.revision,
                interaction=interaction,
                request_sha256=record.request_sha256,
                action=parsed_action,
                queue_name=GATEWAY_ACTION_QUEUE,
            )
        except ActionAuthorizationError:
            raise
        except Exception:
            raise ActionAuthorizationError(
                "operator authorization and durable enqueue could not be verified"
            ) from None
        if self.after_enqueue_hook is not None:
            self.after_enqueue_hook(job)
        queued = CompanionTransitionV1(state=_queued_action_state(job.job_id))
        try:
            committed = self._commit(record, queued)
        except ConcurrentUpdateError:
            current = self._require_run(record.run_id)
            if (
                current.revision == record.revision + 1
                and current.state == queued.state
            ):
                committed = current
            else:
                current_job = self.action_queue.get_job(job.job_id)
                checkpoint_transition = (
                    None
                    if current_job is None
                    else GatewayActionWorker._transition_from_checkpoint(
                        current_job.checkpoint
                    )
                )
                if (
                    current.revision == record.revision + 2
                    and checkpoint_transition is not None
                    and current.state == checkpoint_transition.state
                ):
                    committed = current
                else:
                    raise
        return run_view(committed)


class _LeaseHeartbeat:
    def __init__(
        self,
        *,
        queue: SqliteGatewayJobQueue,
        job: GatewayJob,
        lease_seconds: int,
        interval_seconds: float | None,
    ) -> None:
        self.queue = queue
        self.job = job
        self.lease_seconds = lease_seconds
        self.interval_seconds = interval_seconds
        self.stop_event = Event()
        self.error: BaseException | None = None
        self.thread: Thread | None = None

    def __enter__(self) -> _LeaseHeartbeat:
        if self.interval_seconds is not None:
            if self.interval_seconds <= 0:
                raise ValueError("heartbeat interval must be positive")
            self.thread = Thread(target=self._run, daemon=True)
            self.thread.start()
        return self

    def _run(self) -> None:
        assert self.interval_seconds is not None
        assert self.job.lease_token is not None
        while not self.stop_event.wait(self.interval_seconds):
            try:
                self.queue.heartbeat(
                    job_id=self.job.job_id,
                    lease_owner=self.job.lease_owner or "",
                    lease_token=self.job.lease_token,
                    lease_seconds=self.lease_seconds,
                )
            except BaseException as exc:  # preserve fencing failure for main worker
                self.error = exc
                self.stop_event.set()
                return

    def ensure_healthy(self) -> None:
        if self.error is not None:
            raise JobLeaseLostError("action worker heartbeat lost its lease") from self.error

    def __exit__(self, *_args: object) -> None:
        self.stop_event.set()
        if self.thread is not None:
            self.thread.join(timeout=max(1.0, (self.interval_seconds or 0.0) * 2))


class GatewayActionWorker:
    """Claim, verify, execute and recover durable Gateway action jobs."""

    def __init__(
        self,
        *,
        repository: GatewayRepository,
        companion: InspirationCompanionAdapter,
        artifact_reader: ArtifactReader,
        grant_store: SqliteOneTimeActionGrantStore,
        action_queue: SqliteGatewayJobQueue,
    ) -> None:
        if grant_store.database_path.resolve() != action_queue.database_path.resolve():
            raise ValueError(
                "grant store and action queue must use the same SQLite database"
            )
        self.repository = repository
        self.companion = companion
        self.grant_store = grant_store
        self.action_queue = action_queue
        self.validator = MaterialsGatewayService(
            repository=repository,
            companion=companion,
            artifact_reader=artifact_reader,
        )

    @staticmethod
    def _fault(
        hook: Callable[[str, GatewayJob], None] | None,
        point: str,
        job: GatewayJob,
    ) -> None:
        if hook is not None:
            hook(point, job)

    def run_once(
        self,
        *,
        worker_id: str,
        lease_seconds: int = 3_600,
        heartbeat_interval_seconds: float | None = None,
        fault_hook: Callable[[str, GatewayJob], None] | None = None,
    ) -> GatewayJob | None:
        job = self.action_queue.claim(
            queue_name=GATEWAY_ACTION_QUEUE,
            lease_owner=worker_id,
            lease_seconds=lease_seconds,
        )
        if job is None:
            return None
        if heartbeat_interval_seconds is None:
            heartbeat_interval_seconds = min(30.0, max(0.25, lease_seconds / 3))
        with ExitStack() as stack:
            heartbeat = stack.enter_context(
                _LeaseHeartbeat(
                    queue=self.action_queue,
                    job=job,
                    lease_seconds=lease_seconds,
                    interval_seconds=heartbeat_interval_seconds,
                )
            )
            self._fault(fault_hook, "after_claim", job)
            envelope = self._validate_job(job)
            stack.enter_context(
                _RunScopedProcessLock(
                    self.action_queue.database_path.parent,
                    envelope["run_id"],
                )
            )
            record = self._ensure_gateway_running(
                job,
                envelope,
                lease_seconds=lease_seconds,
            )
            heartbeat.ensure_healthy()
            checkpoint_preexisting = job.checkpoint is not None
            checkpoint = job.checkpoint
            if checkpoint is None:
                job = self.action_queue.checkpoint(
                    job_id=job.job_id,
                    lease_owner=worker_id,
                    lease_token=job.lease_token or "",
                    checkpoint={
                        "gateway_revision": record.revision,
                        "phase": "GATEWAY_RUNNING",
                    },
                    expected_sequence=job.checkpoint_sequence,
                )
                checkpoint = job.checkpoint
            self._fault(fault_hook, "after_gateway_running", job)

            transition = self._transition_from_checkpoint(checkpoint)
            recovery_only = (
                transition is None
                and job.attempt_count > 1
                and checkpoint_preexisting
                and checkpoint is not None
                and checkpoint.get("phase") == "GATEWAY_RUNNING"
            )
            completed_recovery = isinstance(
                getattr(self.companion, "projector", None),
                CompletedInspirationRunRecovery,
            )
            deterministic_replay = envelope["action"].kind in {"reject", "cancel"}
            if recovery_only and not (completed_recovery or deterministic_replay):
                return self._block_manual_recovery(
                    job=job,
                    envelope=envelope,
                    worker_id=worker_id,
                )
            if transition is None:
                try:
                    raw_transition = self.companion.act(
                        run_id=envelope["run_id"],
                        request=record.request,
                        state=InteractionRequiredStateV1(
                            interaction=envelope["interaction"]
                        ),
                        action=envelope["action"],
                    )
                    transition = self.validator._validate_transition(
                        raw_transition,
                        run_id=record.run_id,
                        request=record.request,
                        previous_interaction_id=envelope["interaction"].interaction_id,
                    )
                    self._fault(
                        fault_hook,
                        "after_companion_before_checkpoint",
                        job,
                    )
                except AdapterContractError:
                    if recovery_only:
                        return self._block_manual_recovery(
                            job=job,
                            envelope=envelope,
                            worker_id=worker_id,
                        )
                    else:
                        transition = self.validator._failure_transition(
                            code="ADAPTER_CONTRACT_ERROR",
                            message="inspiration companion returned an invalid transition",
                        )
                except Exception:
                    transition = self.validator._failure_transition(
                        code="ADAPTER_EXECUTION_ERROR",
                        message="inspiration companion failed during queued action",
                    )
                transition_value = transition.model_dump(mode="json")
                job = self.action_queue.checkpoint(
                    job_id=job.job_id,
                    lease_owner=worker_id,
                    lease_token=job.lease_token or "",
                    checkpoint={
                        "gateway_revision": record.revision,
                        "phase": "TRANSITION_READY",
                        "transition": transition_value,
                        "transition_sha256": hashlib.sha256(
                            canonical_json_bytes(transition_value)
                        ).hexdigest(),
                    },
                    expected_sequence=job.checkpoint_sequence,
                )
            heartbeat.ensure_healthy()
            self._fault(fault_hook, "after_runner_checkpoint", job)
            committed = self._ensure_transition_committed(
                job=job,
                envelope=envelope,
                transition=transition,
            )
            self._fault(fault_hook, "after_gateway_terminal", job)
            heartbeat.ensure_healthy()
            terminal_failure = self._terminal_failure_from_checkpoint(job.checkpoint)
            if terminal_failure is not None:
                error_code, error_message = terminal_failure
                failed = self.action_queue.fail(
                    job_id=job.job_id,
                    lease_owner=worker_id,
                    lease_token=job.lease_token or "",
                    error_code=error_code,
                    error_message=error_message,
                )
                self._fault(fault_hook, "after_job_complete", failed)
                return failed
            completed = self.action_queue.complete(
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
            self._fault(fault_hook, "after_job_complete", completed)
            return completed

    def _block_manual_recovery(
        self,
        *,
        job: GatewayJob,
        envelope: dict[str, Any],
        worker_id: str,
    ) -> GatewayJob:
        blocked = self.validator._failure_transition(
            code="BLOCKED_MANUAL_RECOVERY",
            message=(
                "the previous worker stopped after runner execution could "
                "have started; automatic replay is disabled"
            ),
        )
        self._ensure_transition_committed(
            job=job,
            envelope=envelope,
            transition=blocked,
        )
        return self.action_queue.fail(
            job_id=job.job_id,
            lease_owner=worker_id,
            lease_token=job.lease_token or "",
            error_code="BLOCKED_MANUAL_RECOVERY",
            error_message=(
                "runner completion was not checkpointed; inspect attempt "
                "artifacts before an operator-authorized recovery"
            ),
        )

    def _validate_job(self, job: GatewayJob) -> dict[str, Any]:
        expected_keys = {
            "action",
            "action_sha256",
            "execution_manifest_sha256",
            "expected_run_revision",
            "grant_receipt",
            "interaction",
            "interaction_sha256",
            "request_sha256",
            "run_id",
            "schema_version",
        }
        if set(job.payload) != expected_keys:
            raise GatewayActionWorkerError("action job payload fields differ")
        if job.payload["schema_version"] != "materials-gateway-action-job-v1":
            raise GatewayActionWorkerError("action job schema version differs")
        try:
            interaction = INTERACTION_ADAPTER.validate_json(
                canonical_json_bytes(job.payload["interaction"]),
                strict=True,
            )
            action = RUN_ACTION_ADAPTER.validate_json(
                canonical_json_bytes(job.payload["action"]),
                strict=True,
            )
            receipt = ActionGrantReceipt(**job.payload["grant_receipt"])
        except (TypeError, ValueError) as exc:
            raise GatewayActionWorkerError("action job binding is invalid") from exc
        interaction_sha256 = hashlib.sha256(canonical_json_bytes(interaction)).hexdigest()
        action_sha256 = hashlib.sha256(canonical_json_bytes(action)).hexdigest()
        if (
            interaction_sha256 != job.payload["interaction_sha256"]
            or action_sha256 != job.payload["action_sha256"]
            or receipt.interaction_sha256 != interaction_sha256
            or receipt.action_sha256 != action_sha256
            or receipt.action_json != canonical_json_bytes(action).decode("utf-8")
            or receipt.run_id != job.payload["run_id"]
            or receipt.request_sha256 != job.payload["request_sha256"]
            or receipt.execution_manifest_sha256
            != job.payload["execution_manifest_sha256"]
            or receipt.decision != action.kind
            or receipt.grant_id != job.idempotency_key
            or job.job_kind != f"gateway-action-{action.kind}"
        ):
            raise GatewayActionWorkerError("action job hashes or receipt differ")
        if isinstance(interaction, type(None)) or action.interaction_id != interaction.interaction_id:
            raise GatewayActionWorkerError("action interaction differs")
        self.grant_store.verify_consumed_grant_receipt(receipt)
        revision = job.payload["expected_run_revision"]
        if not isinstance(revision, int) or revision < 0:
            raise GatewayActionWorkerError("expected run revision is invalid")
        return {
            "action": action,
            "expected_revision": revision,
            "interaction": interaction,
            "request_sha256": receipt.request_sha256,
            "run_id": receipt.run_id,
        }

    def _ensure_gateway_running(
        self,
        job: GatewayJob,
        envelope: dict[str, Any],
        *,
        lease_seconds: int,
        enforce_lease_covers_walltime: bool = True,
    ) -> GatewayRunRecordV1:
        record = self.repository.get_run(envelope["run_id"])
        if record is None:
            raise GatewayActionWorkerError("queued action run disappeared")
        expected_revision = envelope["expected_revision"]
        original_state = InteractionRequiredStateV1(
            interaction=envelope["interaction"]
        )
        queued_state = _queued_action_state(job.job_id)
        if record.request_sha256 != envelope["request_sha256"]:
            raise GatewayActionWorkerError("queued action request SHA-256 differs")
        if (
            enforce_lease_covers_walltime
            and lease_seconds < record.request.constraints.budget.max_walltime_seconds
        ):
            raise GatewayActionWorkerError(
                "job lease must cover the frozen maximum runner walltime"
            )
        if record.revision == expected_revision and record.state == original_state:
            replacement = record.model_copy(
                update={"revision": record.revision + 1, "state": queued_state}
            )
            try:
                return self.repository.replace_run(
                    replacement,
                    expected_revision=record.revision,
                    result=None,
                )
            except ConcurrentUpdateError:
                record = self.repository.get_run(envelope["run_id"])
                if record is None:
                    raise GatewayActionWorkerError("queued action run disappeared")
        if (
            record.revision >= expected_revision + 1
            and (
                record.state == queued_state
                or self._transition_from_checkpoint(job.checkpoint) is not None
            )
        ):
            return record
        raise GatewayActionWorkerError("queued action failed Gateway revision fencing")

    @staticmethod
    def _transition_from_checkpoint(
        checkpoint: dict[str, Any] | None,
    ) -> CompanionTransitionV1 | None:
        if checkpoint is None or checkpoint.get("phase") == "GATEWAY_RUNNING":
            return None
        if checkpoint.get("phase") != "TRANSITION_READY":
            raise GatewayActionWorkerError("action checkpoint phase is invalid")
        transition_value = checkpoint.get("transition")
        observed = hashlib.sha256(canonical_json_bytes(transition_value)).hexdigest()
        if observed != checkpoint.get("transition_sha256"):
            raise GatewayActionWorkerError("action checkpoint transition hash differs")
        try:
            return CompanionTransitionV1.model_validate_json(
                canonical_json_bytes(transition_value)
            )
        except (TypeError, ValueError) as exc:
            raise GatewayActionWorkerError("action checkpoint transition is invalid") from exc

    @staticmethod
    def _terminal_failure_from_checkpoint(
        checkpoint: dict[str, Any] | None,
    ) -> tuple[str, str] | None:
        if checkpoint is None or checkpoint.get("terminal_disposition") is None:
            return None
        disposition = checkpoint["terminal_disposition"]
        if not isinstance(disposition, dict) or set(disposition) != {
            "error_code",
            "error_message",
            "kind",
        }:
            raise GatewayActionWorkerError(
                "action checkpoint terminal disposition is invalid"
            )
        if disposition["kind"] != "FAIL":
            raise GatewayActionWorkerError(
                "action checkpoint terminal disposition is unsupported"
            )
        error_code = disposition["error_code"]
        error_message = disposition["error_message"]
        if (
            not isinstance(error_code, str)
            or not error_code
            or len(error_code) > 128
            or not error_code.replace("_", "").isalnum()
            or error_code != error_code.upper()
            or not isinstance(error_message, str)
            or not error_message.strip()
            or len(error_message) > 2_000
        ):
            raise GatewayActionWorkerError(
                "action checkpoint terminal failure fields are invalid"
            )
        return error_code, error_message.strip()

    def _ensure_transition_committed(
        self,
        *,
        job: GatewayJob,
        envelope: dict[str, Any],
        transition: CompanionTransitionV1,
    ) -> GatewayRunRecordV1:
        record = self.repository.get_run(envelope["run_id"])
        if record is None:
            raise GatewayActionWorkerError("queued action run disappeared")
        queued_state = _queued_action_state(job.job_id)
        expected_revision = envelope["expected_revision"]
        if record.revision == expected_revision + 1 and record.state == queued_state:
            return self.validator._commit(record, transition)
        if record.revision == expected_revision + 2 and record.state == transition.state:
            if transition.result is not None:
                persisted_result = self.repository.get_result(record.run_id)
                if persisted_result != transition.result:
                    raise GatewayActionWorkerError(
                        "committed action result differs from its checkpoint"
                    )
            return record
        raise GatewayActionWorkerError(
            "queued action terminal commit failed revision fencing"
        )
