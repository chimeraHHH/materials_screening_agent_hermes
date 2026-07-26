"""Generic runner registry and adapters for Orchestrator-controlled stages."""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from typing import Any, Protocol, runtime_checkable

from material_agent.orchestrator.models import (
    ArtifactPointer,
    CancelOutcome,
    ControlError,
    ControlOutcomeType,
    ControlStageOutcome,
    ExternalJobStatus,
    StageCapability,
    StageExecutionContext,
    StageId,
    StageInputValidation,
    StageStatus,
    STAGE_TO_AGENT,
)
from material_agent.retrieval.models import (
    Requirement,
    RetrievalPolicy,
    RetrievalStageContext,
    RetrievalStageInput,
    StageOutcome as NativeStageOutcome,
    StageOutcomeType as NativeOutcomeType,
    StageStatus as NativeStageStatus,
)
from material_agent.retrieval.runner import RetrievalStageRunner
from material_agent.retrieval.storage import LocalArtifactStore


RunnerFactory = Callable[[StageExecutionContext], "StageRunner"]


@runtime_checkable
class StageRunner(Protocol):
    capability: StageCapability
    backend_name: str

    def validate_input(
        self, context: StageExecutionContext
    ) -> StageInputValidation: ...

    def start(
        self, context: StageExecutionContext, idempotency_key: str
    ) -> ControlStageOutcome: ...

    def reconcile(
        self,
        context: StageExecutionContext,
        external_job_ref: str,
        idempotency_key: str,
    ) -> ControlStageOutcome: ...

    def cancel(
        self, external_job_ref: str, idempotency_key: str
    ) -> CancelOutcome: ...


class StageRunnerRegistry:
    """Inject runner factories while keeping capability snapshots explicit."""

    def __init__(
        self, capabilities: dict[StageId, StageCapability] | None = None
    ) -> None:
        self._capabilities = capabilities or default_capabilities()
        self._factories: dict[StageId, RunnerFactory] = {}

    def register(
        self,
        stage: StageId,
        factory: RunnerFactory,
        capability: StageCapability,
    ) -> None:
        if capability.stage is not stage or not capability.registered:
            raise ValueError("registered runner requires a matching capability")
        self._capabilities[stage] = capability
        self._factories[stage] = factory

    def capability(self, stage: StageId) -> StageCapability:
        return self._capabilities[stage].model_copy(deep=True)

    def has_runner(self, stage: StageId) -> bool:
        return stage in self._factories

    def runner(self, context: StageExecutionContext) -> StageRunner:
        try:
            factory = self._factories[context.stage]
        except KeyError as exc:
            raise KeyError(
                f"no production runner is registered for {context.stage.value}"
            ) from exc
        runner = factory(context)
        if runner.capability != context.capability:
            raise ValueError("runner capability differs from plan snapshot")
        return runner


def default_capabilities() -> dict[StageId, StageCapability]:
    return {
        StageId.RETRIEVAL: StageCapability(
            stage=StageId.RETRIEVAL,
            agent_id=STAGE_TO_AGENT[StageId.RETRIEVAL],
            registered=True,
            required_inputs=["requirement"],
        ),
        StageId.ML: StageCapability(
            stage=StageId.ML,
            agent_id=STAGE_TO_AGENT[StageId.ML],
            registered=False,
            required_inputs=["requirement", "candidate_manifest"],
            unavailable_reason="Agent02 ML capability is not implemented",
        ),
        StageId.DFT: StageCapability(
            stage=StageId.DFT,
            agent_id=STAGE_TO_AGENT[StageId.DFT],
            registered=False,
            required_inputs=["requirement", "candidate_manifest"],
            requires_approval=True,
            supports_external=True,
            unavailable_reason="Agent03 DFT capability is not implemented",
        ),
        StageId.MANY_BODY: StageCapability(
            stage=StageId.MANY_BODY,
            agent_id=STAGE_TO_AGENT[StageId.MANY_BODY],
            registered=False,
            required_inputs=["requirement", "dft_result"],
            requires_approval=True,
            supports_external=True,
            unavailable_reason="Agent04 many-body capability is not implemented",
        ),
    }


class Agent01RunnerAdapter:
    """Validate Agent01's frozen native envelope, then map it to control state."""

    backend_name = "materials_project"

    def __init__(
        self,
        *,
        native_runner: RetrievalStageRunner,
        artifact_store: LocalArtifactStore,
        capability: StageCapability,
    ) -> None:
        self.native_runner = native_runner
        self.store = artifact_store
        self.capability = capability

    def validate_input(
        self, context: StageExecutionContext
    ) -> StageInputValidation:
        missing = _missing_inputs(context)
        if missing:
            return StageInputValidation(
                valid=False,
                missing_fields=missing,
                remediation=[
                    "provide the confirmed Requirement artifact and hash"
                ],
            )
        native_context = self._native_context(context)
        validator = getattr(self.native_runner, "validate_input", None)
        if validator is None:
            return StageInputValidation(valid=True)
        validation = validator(native_context)
        return StageInputValidation(
            valid=validation.valid,
            missing_fields=list(validation.missing_fields),
            errors=list(validation.errors),
            remediation=list(validation.remediation),
        )

    def start(
        self, context: StageExecutionContext, idempotency_key: str
    ) -> ControlStageOutcome:
        del idempotency_key
        try:
            plan = self.native_runner.prepare(self._native_context(context))
            native = self.native_runner.start(plan, plan.idempotency_key)
        except Exception as exc:
            return _exception_outcome(
                context,
                _generic_operation_key(context),
                exc,
            )
        return self._map_native(context, native, plan.idempotency_key)

    def reconcile(
        self,
        context: StageExecutionContext,
        external_job_ref: str,
        idempotency_key: str,
    ) -> ControlStageOutcome:
        native = self.native_runner.reconcile(external_job_ref)
        return self._map_native(context, native, idempotency_key)

    def cancel(
        self, external_job_ref: str, idempotency_key: str
    ) -> CancelOutcome:
        raise RuntimeError("Agent01 is synchronous and has no external job")

    def _native_context(
        self, context: StageExecutionContext
    ) -> RetrievalStageContext:
        requirement = Requirement.model_validate(
            self.store.read_json(context.requirement_artifact.uri)
        )
        policy = RetrievalPolicy()
        return RetrievalStageContext(
            requirement=requirement,
            stage_input=RetrievalStageInput(
                project_id=context.project_id,
                run_id=context.run_id,
                requirement_revision=context.requirement_revision,
                requirement_artifact_uri=context.requirement_artifact.uri,
                requirement_hash=context.requirement_artifact.sha256,
                retrieval_policy_version=policy.policy_version,
                confirmed_by_user=requirement.confirmed_by_user,
            ),
        )

    def _map_native(
        self,
        context: StageExecutionContext,
        native: NativeStageOutcome,
        idempotency_key: str,
    ) -> ControlStageOutcome:
        errors = [
            ControlError(
                category=error.category,
                retryable=error.retryable,
                operation=error.operation,
                public_message=error.public_message,
            )
            for error in (
                native.result.errors if native.result is not None else native.errors
            )
        ]
        native_uri = None
        native_sha256 = None
        summary: dict[str, Any] = {}
        if native.result is not None:
            integrity_errors = self._validate_native_result(
                native.result, context.run_id
            )
            errors.extend(integrity_errors)
            if not integrity_errors:
                native_uri = (
                    f"artifact://stages/agent01/{context.run_id}/"
                    "stage_result.json"
                )
                native_ref = self.store.inspect(
                    native_uri, media_type="application/json"
                )
                native_sha256 = native_ref.sha256
                summary = {
                    "candidate_ids": list(native.result.candidate_ids),
                    "metrics": dict(native.result.metrics),
                    "warnings": list(native.result.warnings),
                    "is_mock": bool(
                        native.result.provenance.get("is_mock", False)
                    ),
                    "artifacts": (
                        {
                            "candidate_manifest": {
                                "uri": native.result.candidate_manifest.uri,
                                "sha256": (
                                    native.result.candidate_manifest.sha256
                                ),
                            }
                        }
                        if native.result.candidate_manifest is not None
                        else {}
                    ),
                }
        if errors and any(
            error.category == "BACKEND_INCONSISTENT" for error in errors
        ):
            return ControlStageOutcome(
                stage=context.stage,
                agent_id=context.agent_id,
                outcome=ControlOutcomeType.FAILED,
                status=StageStatus.PERMANENT_FAILED,
                idempotency_key=idempotency_key,
                operation_ref=native.operation_ref,
                errors=errors,
            )
        outcome = {
            NativeOutcomeType.COMPLETED: ControlOutcomeType.COMPLETED,
            NativeOutcomeType.WAITING_EXTERNAL: (
                ControlOutcomeType.WAITING_EXTERNAL
            ),
            NativeOutcomeType.BLOCKED: ControlOutcomeType.BLOCKED,
            NativeOutcomeType.FAILED: ControlOutcomeType.FAILED,
        }[native.outcome]
        status = _map_native_status(native.status)
        return ControlStageOutcome(
            stage=context.stage,
            agent_id=context.agent_id,
            outcome=outcome,
            status=status,
            idempotency_key=idempotency_key,
            native_result_uri=native_uri,
            native_result_sha256=native_sha256,
            operation_ref=native.operation_ref,
            summary=summary,
            errors=errors,
        )

    def _validate_native_result(
        self, result: Any, run_id: str
    ) -> list[ControlError]:
        errors: list[ControlError] = []
        if result.run_id != run_id or result.stage != "agent01":
            errors.append(
                ControlError(
                    category="BACKEND_INCONSISTENT",
                    operation="validate_stage_result",
                    public_message=(
                        "Agent01 result run or stage does not match control input"
                    ),
                )
            )
        refs = list(result.output_artifacts)
        if result.candidate_manifest is not None:
            refs.append(result.candidate_manifest)
        for artifact in refs:
            if not self.store.exists_with_hash(artifact.uri, artifact.sha256):
                errors.append(
                    ControlError(
                        category="BACKEND_INCONSISTENT",
                        operation="validate_stage_result",
                        public_message=(
                            f"Agent01 artifact failed integrity check: "
                            f"{artifact.uri}"
                        ),
                    )
                )
        if (
            not result.input_snapshot_sha256
            or not self.store.exists_with_hash(
                result.input_snapshot_uri, result.input_snapshot_sha256
            )
        ):
            errors.append(
                ControlError(
                    category="BACKEND_INCONSISTENT",
                    operation="validate_stage_result",
                    public_message="Agent01 input snapshot failed integrity check",
                )
            )
        return errors


class FixtureStageRunner:
    """Deterministic test-only runner; it never claims scientific evidence."""

    backend_name = "fixture"

    def __init__(
        self,
        *,
        capability: StageCapability,
        artifact_store: LocalArtifactStore,
        status: StageStatus = StageStatus.SUCCEEDED,
    ) -> None:
        if not capability.is_mock:
            raise ValueError("FixtureStageRunner requires is_mock=true")
        self.capability = capability
        self.store = artifact_store
        self.status = status

    def validate_input(
        self, context: StageExecutionContext
    ) -> StageInputValidation:
        missing = _missing_inputs(context)
        return StageInputValidation(
            valid=not missing,
            missing_fields=missing,
            remediation=[f"provide {name}" for name in missing],
        )

    def start(
        self, context: StageExecutionContext, idempotency_key: str
    ) -> ControlStageOutcome:
        payload = {
            "schema_version": "orchestrator-fixture-result-v1",
            "run_id": context.run_id,
            "stage": context.stage.value,
            "status": self.status.value,
            "is_mock": True,
            "scientific_values": [],
        }
        ref = self.store.write_json(
            (
                f"fixtures/stages/{context.stage.value}/{context.run_id}/"
                f"attempt-{context.attempt}.json"
            ),
            payload,
            immutable=True,
        )
        outcome = (
            ControlOutcomeType.COMPLETED
            if self.status
            in {StageStatus.SUCCEEDED, StageStatus.PARTIAL}
            else ControlOutcomeType.FAILED
        )
        return ControlStageOutcome(
            stage=context.stage,
            agent_id=context.agent_id,
            outcome=outcome,
            status=self.status,
            idempotency_key=idempotency_key,
            native_result_uri=ref.uri,
            native_result_sha256=ref.sha256,
            summary={"is_mock": True, "scientific_values": []},
        )

    def reconcile(
        self,
        context: StageExecutionContext,
        external_job_ref: str,
        idempotency_key: str,
    ) -> ControlStageOutcome:
        del context, external_job_ref, idempotency_key
        raise RuntimeError("synchronous fixture runner cannot reconcile")

    def cancel(
        self, external_job_ref: str, idempotency_key: str
    ) -> CancelOutcome:
        return CancelOutcome(
            external_job_ref=external_job_ref,
            status=ExternalJobStatus.CANCELLED,
            idempotency_key=idempotency_key,
        )


def _missing_inputs(context: StageExecutionContext) -> list[str]:
    missing: list[str] = []
    for name in context.capability.required_inputs:
        if name == "requirement":
            if not context.requirement_artifact.uri:
                missing.append(name)
        elif name not in context.input_artifacts:
            missing.append(name)
    return missing


def _map_native_status(status: NativeStageStatus) -> StageStatus:
    return StageStatus(status.value)


def _generic_operation_key(context: StageExecutionContext) -> str:
    payload = (
        f"{context.project_id}:{context.run_id}:{context.stage.value}:"
        f"{context.attempt}:{context.requirement_artifact.sha256}"
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _exception_outcome(
    context: StageExecutionContext,
    idempotency_key: str,
    exc: Exception,
) -> ControlStageOutcome:
    retryable = _is_retryable_external(exc)
    return ControlStageOutcome(
        stage=context.stage,
        agent_id=context.agent_id,
        outcome=ControlOutcomeType.FAILED,
        status=(
            StageStatus.RETRYABLE_FAILED
            if retryable
            else StageStatus.PERMANENT_FAILED
        ),
        idempotency_key=idempotency_key,
        errors=[
            ControlError(
                category=(
                    "TRANSIENT_EXTERNAL"
                    if retryable
                    else "ORCHESTRATOR_STAGE_EXCEPTION"
                ),
                retryable=retryable,
                operation=context.agent_id,
                public_message=(
                    f"{context.agent_id} invocation failed "
                    f"({type(exc).__name__})"
                ),
            )
        ],
    )


def _is_retryable_external(exc: Exception) -> bool:
    try:
        import requests

        if isinstance(exc, (requests.Timeout, requests.ConnectionError)):
            return True
        if isinstance(exc, requests.HTTPError) and exc.response is not None:
            return (
                exc.response.status_code == 429
                or exc.response.status_code >= 500
            )
    except ImportError:  # pragma: no cover
        pass
    status_code = getattr(exc, "status_code", None)
    if status_code == 429 or (
        isinstance(status_code, int) and status_code >= 500
    ):
        return True
    message = str(exc).lower()
    return any(
        token in message
        for token in ("timeout", "rate limit", "429", "temporar")
    )
