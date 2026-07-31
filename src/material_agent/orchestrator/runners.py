"""Generic runner registry and adapters for Orchestrator-controlled stages."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
import hashlib
import os
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from material_agent.orchestrator.models import (
    ArtifactPointer,
    CancelOutcome,
    ControlError,
    ControlOutcomeType,
    ControlStageOutcome,
    ExternalJobStatus,
    PreparedStagePlan,
    StageCapability,
    StageExecutionContext,
    StageId,
    StageInputValidation,
    StageStatus,
    STAGE_TO_AGENT,
    operation_input_sha256_for,
)
from material_agent.retrieval.models import (
    NativeStageOutcome,
    Requirement,
    RetrievalPolicy,
    RetrievalStageContext,
    RetrievalStageInput,
    RetrievalStagePlan,
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

    def prepare(self, context: StageExecutionContext) -> PreparedStagePlan: ...

    def start(
        self,
        context: StageExecutionContext,
        prepared_plan: PreparedStagePlan,
        idempotency_key: str,
    ) -> ControlStageOutcome: ...

    def reconcile(
        self,
        context: StageExecutionContext,
        prepared_plan: PreparedStagePlan,
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

    def set_unavailable(self, capability: StageCapability) -> None:
        """Set an explicit fail-closed capability snapshot without a runner."""

        if capability.registered:
            raise ValueError("unavailable capability must not be registered")
        self._capabilities[capability.stage] = capability
        self._factories.pop(capability.stage, None)

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


def configure_agent02_production(
    registry: StageRunnerRegistry,
    *,
    project_root: Path,
) -> None:
    """Optionally register the real Agent02 worker from explicit runtime config.

    The environment variable is an opt-in switch, not a fallback mechanism.
    All run-specific registry, policy and health Artifact checks remain in the
    Adapter's frozen-plan validation path.
    """

    worker_value = os.environ.get("MATERIAL_AGENT_ML_WORKER_PYTHON")
    if not worker_value:
        registry.set_unavailable(
            _agent02_unavailable_capability(
                "set MATERIAL_AGENT_ML_WORKER_PYTHON to a validated "
                "dedicated Python 3.11 executable"
            )
        )
        return
    try:
        worker_python = Path(worker_value)
        if not worker_python.is_absolute():
            raise ValueError("MATERIAL_AGENT_ML_WORKER_PYTHON must be absolute")
        worker_python = worker_python.resolve(strict=True)
        if not worker_python.is_file() or not os.access(worker_python, os.X_OK):
            raise ValueError("configured Agent02 worker Python is not executable")
        repository_root = Path(__file__).resolve().parents[3]
        source_root = repository_root / "src"
        lock_path = repository_root / "requirements-agent02.lock"
        model_card_path = (
            repository_root / "config/agent02/chgnet-0.3.0-model-card.json"
        )
        if not source_root.is_dir() or not lock_path.is_file():
            raise ValueError("Agent02 repository resources are unavailable")
        from material_agent.ml_screening.models import ModelCard
        from material_agent.ml_screening.real_resources import (
            AGENT02_PACKAGE_LOCK_SHA256,
            real_model_card,
            real_model_spec,
        )
        from material_agent.ml_screening.resources import sha256_payload

        if _sha256_file(lock_path) != AGENT02_PACKAGE_LOCK_SHA256:
            raise ValueError("Agent02 package lock hash does not match registry")
        card = ModelCard.model_validate_json(model_card_path.read_text("utf-8"))
        if (
            card != real_model_card()
            or sha256_payload(card) != real_model_spec().model_card_sha256
        ):
            raise ValueError("Agent02 model card does not match registry")
    except (OSError, ValueError) as exc:
        registry.set_unavailable(
            _agent02_unavailable_capability(f"Agent02 configuration invalid: {exc}")
        )
        return

    capability = StageCapability(
        stage=StageId.ML,
        agent_id=STAGE_TO_AGENT[StageId.ML],
        registered=True,
        is_mock=False,
        required_inputs=[
            "requirement",
            "candidate_manifest",
            "policy",
            "registry",
            "health",
        ],
        requires_approval=False,
        supports_external=False,
    )

    def factory(_context: StageExecutionContext):
        from material_agent.ml_screening.runner import Agent02RunnerAdapter
        from material_agent.ml_screening.worker_client import SubprocessWorkerClient

        return Agent02RunnerAdapter(
            artifact_store=LocalArtifactStore(project_root),
            capability=capability,
            worker=SubprocessWorkerClient(
                python_executable=worker_python,
                artifact_root=project_root,
                package_lock_path=lock_path,
                source_root=source_root,
            ),
        )

    registry.register(StageId.ML, factory, capability)


def _agent02_unavailable_capability(reason: str) -> StageCapability:
    return StageCapability(
        stage=StageId.ML,
        agent_id=STAGE_TO_AGENT[StageId.ML],
        registered=False,
        is_mock=False,
        # Preserve the default control-plane boundary: without an explicit
        # production worker, a complete legacy stage input reaches the
        # capability-unavailable outcome rather than being reclassified as a
        # missing real-worker artifact.
        required_inputs=["requirement", "candidate_manifest"],
        unavailable_reason=reason,
    )


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


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
            error_code=validation.error_category,
            failure_status=(
                StageStatus.BLOCKED_MISSING_INPUT
                if validation.error_category == "MISSING_INPUT"
                else (
                    StageStatus.PERMANENT_FAILED
                    if not validation.valid
                    else None
                )
            ),
        )

    def prepare(self, context: StageExecutionContext) -> PreparedStagePlan:
        input_snapshot = _require_input_snapshot(context)
        native_uri = _native_plan_uri(context)
        if self.store.exists(native_uri):
            native_ref = self.store.inspect(
                native_uri, media_type="application/json"
            )
            native_plan = RetrievalStagePlan.model_validate(
                self.store.read_json(native_uri)
            )
            _validate_native_plan_context(native_plan, context)
        else:
            native_plan = self.native_runner.prepare(
                self._native_context(context)
            )
            native_ref = self.store.write_json(
                native_uri,
                native_plan.model_dump(mode="json"),
                immutable=True,
            )
        operation_hash = operation_input_sha256_for(
            project_id=context.project_id,
            run_id=context.run_id,
            requirement_revision=context.requirement_revision,
            stage=context.stage,
            agent_id=context.agent_id,
            attempt=context.attempt,
            input_snapshot_sha256=input_snapshot.sha256,
            native_plan_sha256=native_ref.sha256,
            policy_version=native_plan.query_plan.policy_version,
        )
        return PreparedStagePlan(
            project_id=context.project_id,
            run_id=context.run_id,
            requirement_revision=context.requirement_revision,
            stage=context.stage,
            agent_id=context.agent_id,
            attempt=context.attempt,
            input_snapshot_uri=input_snapshot.uri,
            input_snapshot_sha256=input_snapshot.sha256,
            native_plan_uri=native_ref.uri,
            native_plan_sha256=native_ref.sha256,
            operation_input_sha256=operation_hash,
            approval_required=False,
            resource_estimate={"class": "retrieval"},
            policy_version=native_plan.query_plan.policy_version,
            risk_summary=(
                f"Read-only {native_plan.query_plan.source_database.value} "
                "retrieval."
            ),
            created_at=native_plan.query_plan.created_at,
        )

    def start(
        self,
        context: StageExecutionContext,
        prepared_plan: PreparedStagePlan,
        idempotency_key: str,
    ) -> ControlStageOutcome:
        try:
            _validate_prepared_plan_context(prepared_plan, context)
            if not self.store.exists_with_hash(
                prepared_plan.native_plan_uri,
                prepared_plan.native_plan_sha256,
            ):
                raise ValueError("Agent01 native plan failed integrity check")
            plan = RetrievalStagePlan.model_validate(
                self.store.read_json(prepared_plan.native_plan_uri)
            )
            _validate_native_plan_context(plan, context)
            native = self.native_runner.start(plan, plan.idempotency_key)
        except Exception as exc:
            return runner_exception_outcome(
                context,
                idempotency_key,
                exc,
            )
        return self._map_native(context, native, idempotency_key)

    def reconcile(
        self,
        context: StageExecutionContext,
        prepared_plan: PreparedStagePlan,
        external_job_ref: str,
        idempotency_key: str,
    ) -> ControlStageOutcome:
        _validate_prepared_plan_context(prepared_plan, context)
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
        policy = getattr(self.native_runner, "policy", RetrievalPolicy())
        return RetrievalStageContext(
            requirement=requirement,
            stage_input=RetrievalStageInput(
                project_id=context.project_id,
                run_id=context.run_id,
                requirement_revision=context.requirement_revision,
                requirement_artifact_uri=context.requirement_artifact.uri,
                requirement_hash=context.requirement_artifact.sha256,
                raw_request=context.raw_request,
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
        candidate_count: int = 1,
        approval_required: bool | None = None,
        lifecycle_counters: dict[str, int] | None = None,
    ) -> None:
        if not capability.is_mock:
            raise ValueError("FixtureStageRunner requires is_mock=true")
        if candidate_count < 0:
            raise ValueError("candidate_count cannot be negative")
        self.capability = capability
        self.store = artifact_store
        self.status = status
        self.candidate_count = candidate_count
        self.approval_required = approval_required
        self.lifecycle_counters = lifecycle_counters

    def validate_input(
        self, context: StageExecutionContext
    ) -> StageInputValidation:
        self._count("validate")
        missing = _missing_inputs(context)
        if self.candidate_count > 20:
            return StageInputValidation(
                valid=False,
                errors=["fixture batch exceeds the 20-candidate hard limit"],
                remediation=["reduce requested candidate count to 20 or fewer"],
                error_code="BATCH_LIMIT_EXCEEDED",
                failure_status=StageStatus.BLOCKED_MISSING_INPUT,
            )
        return StageInputValidation(
            valid=not missing,
            missing_fields=missing,
            remediation=[f"provide {name}" for name in missing],
        )

    def prepare(self, context: StageExecutionContext) -> PreparedStagePlan:
        self._count("prepare")
        input_snapshot = _require_input_snapshot(context)
        native_uri = _native_plan_uri(context)
        approval_required = (
            self.approval_required
            if self.approval_required is not None
            else 6 <= self.candidate_count <= 20
        )
        native_payload = {
            "schema_version": "orchestrator-fixture-native-plan-v2",
            "project_id": context.project_id,
            "run_id": context.run_id,
            "requirement_revision": context.requirement_revision,
            "stage": context.stage.value,
            "agent_id": context.agent_id,
            "attempt": context.attempt,
            "input_snapshot_uri": input_snapshot.uri,
            "input_snapshot_sha256": input_snapshot.sha256,
            "candidate_count": self.candidate_count,
            "is_mock": True,
            "approval_required": approval_required,
            "resource_estimate": {
                "candidate_count": self.candidate_count,
                "max_num_sites": 100,
                "device": "fixture",
                "relaxation_steps": 200,
                "estimated_wall_time": "fixture-only",
                "model": "fixture-no-scientific-model",
            },
            "policy_version": "orchestrator-fixture-plan-policy-v2",
        }
        if self.store.exists(native_uri):
            existing = self.store.read_json(native_uri)
            if existing != native_payload:
                raise ValueError(
                    "existing fixture native plan conflicts with current input"
                )
            native_ref = self.store.inspect(
                native_uri, media_type="application/json"
            )
        else:
            native_ref = self.store.write_json(
                native_uri, native_payload, immutable=True
            )
        operation_hash = operation_input_sha256_for(
            project_id=context.project_id,
            run_id=context.run_id,
            requirement_revision=context.requirement_revision,
            stage=context.stage,
            agent_id=context.agent_id,
            attempt=context.attempt,
            input_snapshot_sha256=input_snapshot.sha256,
            native_plan_sha256=native_ref.sha256,
            policy_version=native_payload["policy_version"],
        )
        return PreparedStagePlan(
            project_id=context.project_id,
            run_id=context.run_id,
            requirement_revision=context.requirement_revision,
            stage=context.stage,
            agent_id=context.agent_id,
            attempt=context.attempt,
            input_snapshot_uri=input_snapshot.uri,
            input_snapshot_sha256=input_snapshot.sha256,
            native_plan_uri=native_ref.uri,
            native_plan_sha256=native_ref.sha256,
            operation_input_sha256=operation_hash,
            approval_required=approval_required,
            gate_type=(
                "EXPENSIVE_BATCH_APPROVAL" if approval_required else None
            ),
            resource_estimate=native_payload["resource_estimate"],
            policy_version=native_payload["policy_version"],
            risk_summary=(
                "Fixture-only batch used to validate control flow; "
                "no scientific evidence is produced."
            ),
            created_at=datetime(2000, 1, 1, tzinfo=UTC),
        )

    def start(
        self,
        context: StageExecutionContext,
        prepared_plan: PreparedStagePlan,
        idempotency_key: str,
    ) -> ControlStageOutcome:
        self._count("start")
        _validate_prepared_plan_context(prepared_plan, context)
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
        prepared_plan: PreparedStagePlan,
        external_job_ref: str,
        idempotency_key: str,
    ) -> ControlStageOutcome:
        del context, prepared_plan, external_job_ref, idempotency_key
        raise RuntimeError("synchronous fixture runner cannot reconcile")

    def cancel(
        self, external_job_ref: str, idempotency_key: str
    ) -> CancelOutcome:
        return CancelOutcome(
            external_job_ref=external_job_ref,
            status=ExternalJobStatus.CANCELLED,
            idempotency_key=idempotency_key,
        )

    def _count(self, operation: str) -> None:
        if self.lifecycle_counters is not None:
            self.lifecycle_counters[operation] = (
                self.lifecycle_counters.get(operation, 0) + 1
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


def _require_input_snapshot(
    context: StageExecutionContext,
) -> ArtifactPointer:
    if context.input_snapshot is None:
        raise ValueError("stage preparation requires an input snapshot")
    return context.input_snapshot


def _native_plan_uri(context: StageExecutionContext) -> str:
    return (
        f"plans/{context.run_id}/stages/{context.stage.value}/"
        f"attempt-{context.attempt}.native.json"
    )


def _validate_prepared_plan_context(
    prepared_plan: PreparedStagePlan,
    context: StageExecutionContext,
) -> None:
    expected = (
        context.project_id,
        context.run_id,
        context.requirement_revision,
        context.stage,
        context.agent_id,
        context.attempt,
    )
    actual = (
        prepared_plan.project_id,
        prepared_plan.run_id,
        prepared_plan.requirement_revision,
        prepared_plan.stage,
        prepared_plan.agent_id,
        prepared_plan.attempt,
    )
    if actual != expected:
        raise ValueError("prepared stage plan does not match execution context")
    input_snapshot = _require_input_snapshot(context)
    if (
        prepared_plan.input_snapshot_uri != input_snapshot.uri
        or prepared_plan.input_snapshot_sha256 != input_snapshot.sha256
    ):
        raise ValueError("prepared stage plan input snapshot changed")


def _validate_native_plan_context(
    native_plan: RetrievalStagePlan,
    context: StageExecutionContext,
) -> None:
    stage_input = native_plan.context.stage_input
    expected = (
        context.project_id,
        context.run_id,
        context.requirement_revision,
        context.requirement_artifact.uri,
        context.requirement_artifact.sha256,
    )
    actual = (
        stage_input.project_id,
        stage_input.run_id,
        stage_input.requirement_revision,
        stage_input.requirement_artifact_uri,
        stage_input.requirement_hash,
    )
    if actual != expected:
        raise ValueError("Agent01 native plan does not match execution context")


def runner_exception_outcome(
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


# Agent02 remains opt-in: importing the adapter does not register a runner or
# alter default_capabilities(). The lazy attribute avoids a control-plane ↔
# Agent02 import cycle while allowing explicit test imports.
def __getattr__(name: str):
    if name == "Agent02RunnerAdapter":
        from material_agent.ml_screening.runner import Agent02RunnerAdapter

        return Agent02RunnerAdapter
    raise AttributeError(name)
