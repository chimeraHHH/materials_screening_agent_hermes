"""Agent04 task-5 adapter for the existing Orchestrator control plane.

This runner is intentionally a control-chain adapter.  The only executable
backend it accepts is the explicit mock backend; no scientific observable is
created here.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from material_agent.orchestrator.models import (
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
    operation_input_sha256_for,
)
from material_agent.retrieval.storage import LocalArtifactStore

from .evidence import evidence_ceiling
from .mock_backend import (
    ExternalJobRef,
    JobStatus,
    MockBackendError,
    MockManyBodyBackend,
)
from .models import (
    EffectiveModelPackage,
    EvidenceScope,
    ManyBodyRequest,
    ManyBodyResultEnvelope,
    MaterialLinkageStatus,
    SolverValidationStatus,
    canonical_hash,
)
from .registry import REGISTRY_URI, build_registry
from .routing import ROUTING_POLICY_VERSION, route_model
from .validation import ValidationStatus, validate_model_package

_TERMINAL = {JobStatus.SUCCEEDED, JobStatus.FAILED, JobStatus.TIMEOUT, JobStatus.CANCELLED}
_REMEDIATION = {
    "MISSING_INPUT": ["提供完整且 hash 匹配的 EffectiveModelPackage artifact 后重新创建阶段运行。"],
    "NOT_APPLICABLE": ["保留不适用记录；提供当前 capability 支持的模型，不能静默切换 solver。"],
    "BACKEND_INCONSISTENT": ["停止恢复，保留 immutable artifacts，检查 operation、job reference、plan 和 hash。"],
    "APPROVAL_REQUIRED": ["通过 Orchestrator 的冻结 plan 审批后再启动。"],
}


class ManyBodyStageRunner:
    """Map Agent04 native validation/lifecycle to P0.2 control outcomes."""

    backend_name = "mock-many-body"

    def __init__(
        self,
        *,
        artifact_store: LocalArtifactStore,
        capability: StageCapability,
        backend: MockManyBodyBackend | None = None,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        if capability.stage is not StageId.MANY_BODY or not capability.registered:
            raise ValueError("Agent04 runner requires an explicitly registered capability")
        if not capability.is_mock:
            raise ValueError("task 5 only supports an explicitly mock capability")
        self.store = artifact_store
        self.capability = capability
        self.backend = backend or MockManyBodyBackend()
        self.now = now or (lambda: datetime.now(UTC))

    def validate_input(self, context: StageExecutionContext) -> StageInputValidation:
        if context.stage is not StageId.MANY_BODY or context.agent_id != "agent04":
            return self._invalid("PERMANENT_CONFIGURATION", "Agent04 context identity is invalid", StageStatus.PERMANENT_FAILED)
        package_ref = context.input_artifacts.get("model_package")
        if package_ref is None:
            return self._invalid("MISSING_INPUT", "model_package artifact is required", StageStatus.BLOCKED_MISSING_INPUT, missing=["model_package"])
        try:
            result = validate_model_package(
                self.store.read_json(package_ref.uri),
                artifact_root=self.store.root,
            )
            if result.status is ValidationStatus.BLOCKED_MISSING_INPUT:
                return self._validation_result(result)
            if result.status is ValidationStatus.PERMANENT_FAILED:
                return self._validation_result(result)
            if result.status is ValidationStatus.NOT_APPLICABLE:
                return self._validation_result(result)
            if not context.capability.is_mock:
                return self._invalid("PERMANENT_CONFIGURATION", "Agent04 task 5 requires an explicit mock capability", StageStatus.PERMANENT_FAILED)
            package = result.package
            assert package is not None
            decision = route_model(package, "workflow_lifecycle")
            if decision.status != "READY" or decision.recommended_solver_id != "mock-many-body/v1":
                return StageInputValidation(
                    valid=False,
                    errors=["Agent04 model is not applicable to the mock control capability"],
                    error_code="NOT_APPLICABLE",
                    failure_status=StageStatus.PERMANENT_FAILED,
                    remediation=list(decision.remediation),
                )
            return StageInputValidation(valid=True)
        except (KeyError, TypeError, ValueError, json.JSONDecodeError, OSError) as exc:
            text = str(exc)
            code = "INPUT_INTEGRITY_ERROR" if "hash" in text.lower() or "integrity" in text.lower() else "PERMANENT_FAILED"
            return self._invalid(code, text, StageStatus.PERMANENT_FAILED)

    def prepare(self, context: StageExecutionContext) -> PreparedStagePlan:
        if context.input_snapshot is None:
            raise ValueError("Orchestrator input snapshot is required before prepare")
        validation = self.validate_input(context)
        if not validation.valid:
            raise ValueError(validation.errors[0] if validation.errors else validation.error_code)
        package_ref = context.input_artifacts["model_package"]
        package = EffectiveModelPackage.model_validate(self.store.read_json(package_ref.uri))
        decision = route_model(package, "workflow_lifecycle")
        request = ManyBodyRequest(
            request_id=f"mbreq_{canonical_hash({'run_id': context.run_id, 'attempt': context.attempt, 'model': package.package_hash})[:24]}",
            model_id=package.model_id,
            model_revision=package.revision,
            model_package={"uri": package_ref.uri, "sha256": package_ref.sha256},
            state_point_ids=tuple(item.state_point_id for item in package.state_points),
            requested_claims=("workflow_lifecycle",),
            approval_policy_version="many-body-approval/v1",
            routing_policy_version=ROUTING_POLICY_VERSION,
            is_mock=True,
        )
        native_uri = f"plans/{context.run_id}/stages/many_body/attempt-{context.attempt}.native.json"
        if self.store.exists(native_uri):
            native = self.store.read_json(native_uri)
        else:
            native = {
                "schema_version": "agent04-many-body-stage-plan-v1",
                "model": {"model_id": package.model_id, "revision": package.revision, "sha256": package.package_hash, "artifact": package_ref.model_dump(mode="json")},
                "request": request.model_dump(mode="json"),
                "routing": decision.model_dump(mode="json"),
                "registry_snapshot": {"uri": REGISTRY_URI, "sha256": build_registry().snapshot_hash},
                "resource_estimate": {"resource_class": "CONTROL_ONLY", "is_mock": True, "num_sites": package.geometry.num_sites, "active_orbitals": len([item for item in package.basis.orbitals if item.active])},
                "mock_scenario": self.backend.scenario,
                "approval_payload": {
                    "gate_type": "EXPENSIVE_BATCH_APPROVAL",
                    "model_id": package.model_id,
                    "model_revision": package.revision,
                    "model_sha256": package.package_hash,
                    "routing_decision": decision.model_dump(mode="json"),
                    "backend": {"id": self.backend.backend_id, "version": self.backend.backend_version},
                    "resource_estimate": {"resource_class": "CONTROL_ONLY", "is_mock": True},
                    "evidence_ceiling": evidence_ceiling(package, is_mock=True, solver_validation_status=SolverValidationStatus.MOCK_ONLY, material_linkage_status=MaterialLinkageStatus.NONE).value,
                    "limitations": ["仅 mock 控制链；不产生科学数值、observable 或 L4_MANY_BODY_VALIDATED。"],
                },
                "created_at": self.now().isoformat(),
            }
        native_ref = self._write_or_reuse(native_uri, native)
        operation_hash = operation_input_sha256_for(
            project_id=context.project_id, run_id=context.run_id,
            requirement_revision=context.requirement_revision, stage=context.stage,
            agent_id=context.agent_id, attempt=context.attempt,
            input_snapshot_sha256=context.input_snapshot.sha256,
            native_plan_sha256=native_ref.sha256, policy_version=ROUTING_POLICY_VERSION,
        )
        return PreparedStagePlan(
            project_id=context.project_id, run_id=context.run_id,
            requirement_revision=context.requirement_revision, stage=context.stage,
            agent_id=context.agent_id, attempt=context.attempt,
            input_snapshot_uri=context.input_snapshot.uri,
            input_snapshot_sha256=context.input_snapshot.sha256,
            native_plan_uri=native_ref.uri, native_plan_sha256=native_ref.sha256,
            operation_input_sha256=operation_hash, approval_required=True,
            gate_type="EXPENSIVE_BATCH_APPROVAL",
            resource_estimate=native["resource_estimate"], policy_version=ROUTING_POLICY_VERSION,
            risk_summary="仅 mock 控制链；无科学数值、observable 或 L4_MANY_BODY_VALIDATED。",
            created_at=datetime.fromisoformat(native["created_at"]),
        )

    def start(self, context: StageExecutionContext, prepared_plan: PreparedStagePlan, idempotency_key: str) -> ControlStageOutcome:
        try:
            self._validate_prepared(context, prepared_plan)
            operation_uri = self._operation_uri(idempotency_key)
            terminal_uri = self._terminal_uri(idempotency_key)
            if self.store.exists(terminal_uri):
                return self._outcome_from_operation(context, self.store.read_json(terminal_uri))
            if self.store.exists(operation_uri):
                return self._outcome_from_operation(context, self.store.read_json(operation_uri))
            native = self.store.read_json(prepared_plan.native_plan_uri)
            request = ManyBodyRequest.model_validate(native["request"])
            ref = self.backend.submit(request, idempotency_key)
            operation = {"schema_version": "agent04-many-body-operation-v1", "idempotency_key": idempotency_key, "request": request.model_dump(mode="json"), "external_job_ref": ref.__dict__, "status": JobStatus.CREATED.value, "status_sequence": 0, "scenario": self.backend.scenario, "plan_uri": prepared_plan.native_plan_uri, "plan_sha256": prepared_plan.native_plan_sha256}
            operation_ref = self.store.write_json(operation_uri, operation, immutable=True)
            return self._waiting(context, idempotency_key, ref, 0, JobStatus.CREATED, operation_ref.uri)
        except Exception as exc:  # noqa: BLE001
            return self._failed(context, idempotency_key, "start", exc)

    def reconcile(self, context: StageExecutionContext, prepared_plan: PreparedStagePlan, external_job_ref: str, idempotency_key: str) -> ControlStageOutcome:
        try:
            self._validate_prepared(context, prepared_plan)
            operation_uri = self._operation_uri(idempotency_key)
            if not self.store.exists(operation_uri):
                raise ValueError("operation ledger record is missing")
            operation = self.store.read_json(operation_uri)
            ref = ExternalJobRef(**operation["external_job_ref"])
            if ref.external_job_ref_id != external_job_ref or ref.idempotency_key != idempotency_key:
                raise ValueError("external job reference or operation key conflicts with ledger")
            request = ManyBodyRequest.model_validate(operation["request"])
            previous = JobStatus(operation["status"])
            sequence = int(operation.get("status_sequence", 0))
            if not self.backend._jobs.get(idempotency_key):
                self.backend.restore_job(request, ref, polls=sequence, status=previous)
            observed = self.backend.status(ref)
            self._validate_transition(previous, observed)
            sequence += 1
            status_ref = self.store.write_json(self._status_uri(context, idempotency_key, sequence), {"external_job_ref": ref.__dict__, "status": observed.value, "sequence": sequence}, immutable=True)
            operation.update({"status": observed.value, "status_sequence": sequence, "last_status_uri": status_ref.uri, "last_status_sha256": status_ref.sha256})
            if observed not in _TERMINAL:
                self.store.write_json(operation_uri, operation, immutable=False)
                return self._waiting(context, idempotency_key, ref, sequence, observed, status_ref.uri)
            if observed is not JobStatus.SUCCEEDED:
                terminal_ref = self.store.write_json(self._terminal_uri(idempotency_key), operation, immutable=True)
                return self._terminal_failure(context, idempotency_key, ref, observed, sequence, terminal_ref.uri)
            result = self.backend.fetch_result(ref)
            if result.external_job_ref != ref or result.request_hash != ref.request_hash or result.input_hash != ref.input_hash:
                raise MockBackendError(code=self._backend_code("REFERENCE_MISMATCH"), message="mock result does not match frozen request")
            metadata = dict(result.artifact_metadata)
            expected_result_hash = canonical_hash({key: value for key, value in metadata.items() if key != "content_sha256"})
            if result.result_hash != expected_result_hash or metadata.get("content_sha256") != result.result_hash:
                raise MockBackendError(code=self._backend_code("RESULT_HASH_MISMATCH"), message="mock result artifact hash does not match metadata")
            ManyBodyResultEnvelope.model_validate(result.envelope.model_dump(mode="json"))
            result_payload = {"envelope": result.envelope.model_dump(mode="json"), "artifact_metadata": metadata, "observables": []}
            result_ref = self.store.write_json(self._result_uri(context, idempotency_key), result_payload, immutable=True)
            operation.update({"result": {"uri": result_ref.uri, "sha256": result_ref.sha256}})
            terminal_ref = self.store.write_json(self._terminal_uri(idempotency_key), operation, immutable=True)
            return ControlStageOutcome(stage=context.stage, agent_id=context.agent_id, outcome=ControlOutcomeType.COMPLETED, status=StageStatus.SUCCEEDED, idempotency_key=idempotency_key, native_result_uri=result_ref.uri, native_result_sha256=result_ref.sha256, operation_ref=self._operation_uri(idempotency_key), external_job_ref=ref.external_job_ref_id, external_status=ExternalJobStatus.SUCCEEDED, external_status_sequence=sequence, summary={"is_mock": True, "mock_only": True, "observables": [], "evidence_level": "L0_PARSED", "evidence_scope": EvidenceScope.SOLVER_BENCHMARK.value, "plan_sha256": prepared_plan.native_plan_sha256, "result_sha256": result_ref.sha256})
        except Exception as exc:  # noqa: BLE001
            return self._failed(context, idempotency_key, "reconcile", exc)

    def cancel(self, external_job_ref: str, idempotency_key: str) -> CancelOutcome:
        operation = self.store.read_json(self._operation_uri(idempotency_key))
        ref = ExternalJobRef(**operation["external_job_ref"])
        if ref.external_job_ref_id != external_job_ref:
            raise ValueError("external job reference conflicts with operation ledger")
        request = ManyBodyRequest.model_validate(operation["request"])
        if not self.backend._jobs.get(idempotency_key):
            self.backend.restore_job(request, ref, polls=int(operation.get("status_sequence", 0)), status=JobStatus(operation["status"]))
        result = self.backend.cancel(ref)
        return CancelOutcome(external_job_ref=external_job_ref, status=self._external_status(result.value), idempotency_key=idempotency_key, message=f"mock backend returned {result.value}")

    def _validate_prepared(self, context: StageExecutionContext, prepared: PreparedStagePlan) -> None:
        if prepared.stage is not StageId.MANY_BODY or prepared.agent_id != "agent04" or prepared.run_id != context.run_id or prepared.attempt != context.attempt:
            raise ValueError("prepared Agent04 plan does not match execution context")
        if context.input_snapshot is None or prepared.input_snapshot_sha256 != context.input_snapshot.sha256:
            raise ValueError("prepared Agent04 plan input snapshot differs from context")
        if not self.store.exists_with_hash(prepared.native_plan_uri, prepared.native_plan_sha256):
            raise ValueError("native Agent04 plan failed integrity")

    def _write_or_reuse(self, uri: str, value: dict[str, Any]):
        if self.store.exists(uri):
            if self.store.read_json(uri) != value:
                raise ValueError("existing Agent04 native plan conflicts with current input")
            return self.store.inspect(uri, media_type="application/json")
        return self.store.write_json(uri, value, immutable=True)

    def _outcome_from_operation(self, context: StageExecutionContext, operation: dict[str, Any]) -> ControlStageOutcome:
        ref = ExternalJobRef(**operation["external_job_ref"])
        status = JobStatus(operation["status"])
        result = operation.get("result")
        if result:
            if not self.store.exists_with_hash(result["uri"], result["sha256"]):
                raise ValueError("result artifact failed integrity")
            return ControlStageOutcome(stage=context.stage, agent_id=context.agent_id, outcome=ControlOutcomeType.COMPLETED, status=StageStatus.SUCCEEDED, idempotency_key=operation["idempotency_key"], native_result_uri=result["uri"], native_result_sha256=result["sha256"], operation_ref=self._operation_uri(operation["idempotency_key"]), external_job_ref=ref.external_job_ref_id, external_status=ExternalJobStatus.SUCCEEDED, external_status_sequence=int(operation.get("status_sequence", 0)), summary={"is_mock": True, "mock_only": True, "observables": [], "evidence_level": "L0_PARSED"})
        if status in _TERMINAL:
            return self._terminal_failure(context, operation["idempotency_key"], ref, status, int(operation.get("status_sequence", 0)), self._terminal_uri(operation["idempotency_key"]))
        return self._waiting(context, operation["idempotency_key"], ref, int(operation.get("status_sequence", 0)), status, operation.get("last_status_uri"))

    def _waiting(self, context, key, ref, sequence, status, operation_ref=None):
        return ControlStageOutcome(stage=context.stage, agent_id=context.agent_id, outcome=ControlOutcomeType.WAITING_EXTERNAL, status=StageStatus.RUNNING, idempotency_key=key, operation_ref=self._operation_uri(key), external_job_ref=ref.external_job_ref_id, external_status=self._external_status(status.value), external_status_sequence=sequence, summary={"is_mock": True, "mock_only": True, "observables": [], "message": "Mock lifecycle only; no many-body scientific value was produced."})

    def _terminal_failure(self, context, key, ref, status, sequence, operation_ref):
        mapped = {JobStatus.FAILED: StageStatus.PERMANENT_FAILED, JobStatus.TIMEOUT: StageStatus.RETRYABLE_FAILED, JobStatus.CANCELLED: StageStatus.CANCELLED}[status]
        outcome = ControlOutcomeType.COMPLETED if mapped is StageStatus.CANCELLED else ControlOutcomeType.FAILED
        return ControlStageOutcome(stage=context.stage, agent_id=context.agent_id, outcome=outcome, status=mapped, idempotency_key=key, operation_ref=self._operation_uri(key), external_job_ref=ref.external_job_ref_id, external_status=self._external_status(status.value), external_status_sequence=sequence, summary={"is_mock": True, "mock_only": True, "observables": [], "reason_code": f"MOCK_{status.value}"})

    def _failed(self, context, key, operation, exc):
        text = str(exc)
        category = "BACKEND_INCONSISTENT" if any(word in text.lower() for word in ("hash", "integrity", "conflict", "regression", "reference", "terminal")) or isinstance(exc, MockBackendError) else "MOCK_BACKEND_FAILED"
        return ControlStageOutcome(stage=context.stage, agent_id=context.agent_id, outcome=ControlOutcomeType.FAILED, status=StageStatus.PERMANENT_FAILED, idempotency_key=key, operation_ref=self._operation_uri(key), summary={"is_mock": True, "mock_only": True, "observables": [], "reason_code": category, "message": text}, errors=[ControlError(category=category, operation=operation, public_message=text)])

    @staticmethod
    def _validate_transition(previous: JobStatus, observed: JobStatus) -> None:
        order = {JobStatus.CREATED: 0, JobStatus.QUEUED: 1, JobStatus.RUNNING: 2, JobStatus.SUCCEEDED: 3, JobStatus.FAILED: 3, JobStatus.TIMEOUT: 3, JobStatus.CANCELLED: 3}
        if previous in _TERMINAL and observed is not previous:
            raise ValueError(f"backend status changed after terminal state: {previous.value} -> {observed.value}")
        if order[observed] < order[previous]:
            raise ValueError(f"backend status regression: {previous.value} -> {observed.value}")

    @staticmethod
    def _invalid(code, message, status, *, missing=()):
        return StageInputValidation(valid=False, missing_fields=list(missing), errors=[message], error_code=code, failure_status=status, remediation=_REMEDIATION.get(code, ["检查冻结输入并创建新 attempt。"]))

    @staticmethod
    def _validation_result(result):
        first = result.issues[0] if result.issues else None
        missing = [item.field_path for item in result.issues if item.reason_code.startswith("MISSING_")]
        status = StageStatus.BLOCKED_MISSING_INPUT if result.status is ValidationStatus.BLOCKED_MISSING_INPUT else StageStatus.PERMANENT_FAILED
        return StageInputValidation(valid=False, missing_fields=missing, errors=[item.message for item in result.issues], error_code=first.reason_code if first else result.status.value, failure_status=status, remediation=[item.remediation for item in result.issues])

    @staticmethod
    def _operation_uri(key):
        return f"stages/agent04/operations/{key}.json"

    @staticmethod
    def _terminal_uri(key):
        return f"stages/agent04/operations/{key}.terminal.json"

    @staticmethod
    def _status_uri(context, key, sequence):
        return f"stages/agent04/{context.run_id}/attempt-{context.attempt}/operations/{key}.status-{sequence}.json"

    @staticmethod
    def _result_uri(context, key):
        return f"stages/agent04/{context.run_id}/attempt-{context.attempt}/many-body-result.json"

    @staticmethod
    def _external_status(status):
        return {"CREATED": ExternalJobStatus.SUBMITTED, "QUEUED": ExternalJobStatus.RUNNING, "RUNNING": ExternalJobStatus.RUNNING, "SUCCEEDED": ExternalJobStatus.SUCCEEDED, "FAILED": ExternalJobStatus.FAILED, "TIMEOUT": ExternalJobStatus.TIMEOUT, "CANCELLED": ExternalJobStatus.CANCELLED, "CANCEL_CONFIRMED": ExternalJobStatus.CANCELLED, "ALREADY_TERMINAL": ExternalJobStatus.CANCELLED}[status]

    @staticmethod
    def _backend_code(value):
        from .mock_backend import BackendErrorCode
        return BackendErrorCode(value)
