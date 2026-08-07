"""Pure-Python implementation of the four stable Materials Gateway tools."""

from __future__ import annotations

import hashlib
import hmac
from collections.abc import Mapping
from typing import Any

from pydantic import ValidationError

from material_agent.gateway.errors import (
    AdapterContractError,
    IllegalActionError,
    ResultIntegrityError,
    ResultUnavailableError,
    RunNotFoundError,
    StaleInteractionError,
    SubmissionConflictError,
)
from material_agent.gateway.models import (
    CompanionTransitionV1,
    GatewayRunRecordV1,
    InspirationConstraintsV1,
    InspirationRunRequestV1,
    InteractionRequiredStateV1,
    MaterialsResultGetRequestV1,
    MaterialsResultViewV1,
    MaterialsRunActRequestV1,
    MaterialsRunGetRequestV1,
    MaterialsRunViewV1,
    PartialStateV1,
    FailedStateV1,
    RunActionV1,
    RunningStateV1,
    SucceededStateV1,
    canonical_json_bytes,
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
        max_report_bytes: int = 1_000_000,
    ) -> None:
        if max_report_bytes < 1:
            raise ValueError("max_report_bytes must be positive")
        self.repository = repository
        self.companion = companion
        self.artifact_reader = artifact_reader
        self.max_report_bytes = max_report_bytes

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
        if (result.report_uri, result.authoritative_sha256) != reference:
            raise ResultIntegrityError("run and result URI/SHA reference differ")

        try:
            payload = self.artifact_reader.read_bytes(result.report_uri)
        except Exception as exc:
            raise ResultIntegrityError("bound report artifact is unavailable") from exc
        if len(payload) > self.max_report_bytes:
            raise ResultIntegrityError("bound report artifact exceeds the size limit")

        observed_sha256 = hashlib.sha256(payload).hexdigest()
        if not hmac.compare_digest(observed_sha256, result.authoritative_sha256):
            raise ResultIntegrityError(
                "bound report artifact failed authoritative SHA-256 validation"
            )
        return MaterialsResultViewV1.model_validate_json(canonical_json_bytes(result))

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
