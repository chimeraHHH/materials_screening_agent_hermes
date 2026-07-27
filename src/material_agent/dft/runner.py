"""Agent03 Task 4 adapter for the existing Orchestrator control plane."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any, Callable

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
    operation_input_sha256_for,
)
from material_agent.retrieval.models import Requirement
from material_agent.retrieval.storage import LocalArtifactStore

from .mock_backend import MockDFTBackend
from .models import (
    ArtifactRef,
    ClaimRequest,
    DFTRequest,
    DFTResultEnvelope,
    ExecutionMode,
    ExternalJobRef,
    JobStatus,
    canonical_hash,
)
from .planner import DFTPlanner, StageInputValidator, build_approval_payload


class DFTStageRunner:
    """Map the frozen Agent03 native lifecycle to P0.2 runner semantics.

    The class is intentionally injectable and is not registered by the
    default production registry. Tests may register it with an explicit mock
    capability.
    """

    backend_name = "mock-dft"

    def __init__(
        self,
        *,
        artifact_store: LocalArtifactStore,
        capability: StageCapability,
        backend: MockDFTBackend | None = None,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        if capability.stage is not StageId.DFT or not capability.registered:
            raise ValueError("DFT runner requires an explicitly registered capability")
        if not capability.is_mock:
            raise ValueError("Task 4 only supports an explicitly mock capability")
        self.store = artifact_store
        self.capability = capability
        self.backend = backend or MockDFTBackend()
        self.now = now or (lambda: datetime.now(UTC))
        self.planner = DFTPlanner()

    def validate_input(self, context: StageExecutionContext) -> StageInputValidation:
        missing = [
            name for name in ("candidate_manifest",)
            if name not in context.input_artifacts
        ]
        if missing:
            return StageInputValidation(
                valid=False,
                missing_fields=missing,
                error_code="MISSING_INPUT",
                failure_status=StageStatus.BLOCKED_MISSING_INPUT,
                remediation=["provide immutable Agent01 candidate manifest and structure artifacts"],
            )
        try:
            payload = self._planning_payload(context)
            validation = StageInputValidator().validate(payload)
            if not validation.valid:
                return StageInputValidation(
                    valid=False,
                    errors=[issue.message for issue in validation.issues],
                    error_code=validation.issues[0].code,
                    failure_status=StageStatus.BLOCKED_MISSING_INPUT,
                    remediation=[issue.how_to_resolve for issue in validation.issues],
                )
            if not context.capability.is_mock:
                return StageInputValidation(
                    valid=False,
                    errors=["Task 4 only supports an explicitly mock capability"],
                    error_code="PERMANENT_CONFIGURATION",
                    failure_status=StageStatus.PERMANENT_FAILED,
                )
            return StageInputValidation(valid=True)
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            return StageInputValidation(
                valid=False,
                errors=[str(exc)],
                error_code="INPUT_INTEGRITY_ERROR",
                failure_status=StageStatus.PERMANENT_FAILED,
                remediation=["repair the immutable requirement, manifest, or structure references"],
            )

    def prepare(self, context: StageExecutionContext) -> PreparedStagePlan:
        if context.input_snapshot is None:
            raise ValueError("Orchestrator input snapshot is required before prepare")
        validation = self.validate_input(context)
        if not validation.valid:
            raise ValueError(validation.errors[0] if validation.errors else validation.error_code)
        native_uri = f"plans/{context.run_id}/stages/dft/attempt-{context.attempt}.native.json"
        payload = self._planning_payload(context)
        workflow = self.planner.plan(payload)
        approval = build_approval_payload(
            workflow, project_id=context.project_id, run_id=context.run_id
        )
        native_payload = {
            "schema_version": "agent03-dft-stage-plan-v1",
            "workflow_plan": workflow.__dict__,
            "approval_payload": approval,
            "request_payload": payload,
        }
        # Dataclasses contain Pydantic models; normalize before hashing/writing.
        native_payload["workflow_plan"] = {
            "request_id": workflow.request_id,
            "workflow_id": workflow.workflow_id,
            "candidate_id": workflow.candidate_id,
            "template_id": workflow.template_id,
            "tasks": [task.model_dump(mode="json") for task in workflow.tasks],
            "requested_claims": [claim.model_dump(mode="json") for claim in workflow.requested_claims],
            "resource_estimate": workflow.resource_estimate.model_dump(mode="json"),
            "plan_hash": workflow.plan_hash,
            "requires_approval": workflow.requires_approval,
            "blockers": list(workflow.blockers),
        }
        native_ref = self._write_or_reuse(native_uri, native_payload)
        operation_hash = operation_input_sha256_for(
            project_id=context.project_id,
            run_id=context.run_id,
            requirement_revision=context.requirement_revision,
            stage=context.stage,
            agent_id=context.agent_id,
            attempt=context.attempt,
            input_snapshot_sha256=context.input_snapshot.sha256,
            native_plan_sha256=native_ref.sha256,
            policy_version="agent03-dft-policy-v1",
        )
        return PreparedStagePlan(
            project_id=context.project_id,
            run_id=context.run_id,
            requirement_revision=context.requirement_revision,
            stage=context.stage,
            agent_id=context.agent_id,
            attempt=context.attempt,
            input_snapshot_uri=context.input_snapshot.uri,
            input_snapshot_sha256=context.input_snapshot.sha256,
            native_plan_uri=native_ref.uri,
            native_plan_sha256=native_ref.sha256,
            operation_input_sha256=operation_hash,
            approval_required=True,
            gate_type="EXPENSIVE_BATCH_APPROVAL",
            resource_estimate=workflow.resource_estimate.model_dump(mode="json"),
            policy_version="agent03-dft-policy-v1",
            risk_summary="Mock lifecycle only; no VASP execution or scientific result.",
            created_at=self.now(),
        )

    def start(
        self,
        context: StageExecutionContext,
        prepared_plan: PreparedStagePlan,
        idempotency_key: str,
    ) -> ControlStageOutcome:
        try:
            self._validate_prepared(context, prepared_plan)
            native = self._load_native(prepared_plan)
            operation_uri = self._operation_uri(context, idempotency_key)
            terminal_uri = self._terminal_operation_uri(context, idempotency_key)
            if self.store.exists(terminal_uri):
                return self._outcome_from_operation(
                    context, prepared_plan, self.store.read_json(terminal_uri)
                )
            if self.store.exists(operation_uri):
                operation = self.store.read_json(operation_uri)
                if operation["idempotency_key"] != idempotency_key:
                    raise ValueError("operation ledger key mismatch")
                return self._outcome_from_operation(context, prepared_plan, operation)
            request = self._request_from_native(context, native, prepared_plan)
            ref = self.backend.submit(request, idempotency_key)
            operation = {
                "schema_version": "agent03-dft-operation-v1",
                "idempotency_key": idempotency_key,
                "request": request.model_dump(mode="json"),
                "external_job_ref": ref.model_dump(mode="json"),
                "scenario": self.backend.scenario,
                "status": JobStatus.CREATED.value,
                "status_sequence": 0,
                "result": None,
            }
            self.store.write_json(operation_uri, operation, immutable=True)
            return self._waiting(context, idempotency_key, ref, 0, JobStatus.CREATED)
        except Exception as exc:
            return self._failed(context, idempotency_key, "start", exc)

    def reconcile(
        self,
        context: StageExecutionContext,
        prepared_plan: PreparedStagePlan,
        external_job_ref: str,
        idempotency_key: str,
    ) -> ControlStageOutcome:
        try:
            self._validate_prepared(context, prepared_plan)
            operation_uri = self._operation_uri(context, idempotency_key)
            if not self.store.exists(operation_uri):
                raise ValueError("operation ledger record is missing")
            operation = self.store.read_json(operation_uri)
            ref = ExternalJobRef.model_validate(operation["external_job_ref"])
            if ref.external_job_ref_id != external_job_ref or ref.idempotency_key != idempotency_key:
                raise ValueError("external job reference or operation key conflicts with ledger")
            request = DFTRequest.model_validate(operation["request"])
            terminal_uri = self._terminal_operation_uri(context, idempotency_key)
            if self.store.exists(terminal_uri):
                return self._outcome_from_operation(
                    context, prepared_plan, self.store.read_json(terminal_uri)
                )
            previous, sequence = self._latest_status(context, idempotency_key, operation)
            self.backend.restore_job(
                request, ref, polls=sequence, status=previous
            )
            observed = self.backend.status(ref)
            sequence += 1
            snapshot_ref = self.store.write_json(
                self._status_uri(context, idempotency_key, sequence),
                {"external_job_ref": ref.model_dump(mode="json"), "status": observed.value, "sequence": sequence},
                immutable=True,
            )
            if observed not in {JobStatus.SUCCEEDED, JobStatus.FAILED, JobStatus.TIMEOUT, JobStatus.CANCELLED}:
                return self._waiting(context, idempotency_key, ref, sequence, observed, snapshot_ref.uri)
            result = self.backend.fetch_result(ref)
            result = DFTResultEnvelope.model_validate(result.model_dump(mode="json"))
            if result.workflow_plan_hash != request.workflow_plan_hash or result.external_job_ref != ref:
                raise ValueError("DFT result envelope does not match frozen request")
            result_ref = self.store.write_json(
                self._result_uri(context, idempotency_key), result.model_dump(mode="json"), immutable=True
            )
            final_operation = dict(operation)
            final_operation.update({"status": observed.value, "status_sequence": sequence, "result": result_ref.model_dump(mode="json")})
            # Keep the operation ledger append-only: terminal state is a new artifact.
            terminal_ref = self.store.write_json(
                self._terminal_operation_uri(context, idempotency_key), final_operation, immutable=True
            )
            return self._terminal_outcome(context, idempotency_key, ref, observed, sequence, result_ref, idempotency_key)
        except Exception as exc:
            category = "BACKEND_INCONSISTENT" if any(token in str(exc).lower() for token in ("hash", "integrity", "conflict", "missing", "match")) else "DFT_RECONCILE_FAILED"
            return self._failed(context, idempotency_key, "reconcile", exc, category=category)

    def cancel(self, external_job_ref: str, idempotency_key: str) -> CancelOutcome:
        ref = self._find_ref(external_job_ref, idempotency_key)
        try:
            result = self.backend.cancel(ref)
        except KeyError:
            operation_uri = self._operation_uri_from_key(ref.idempotency_key)
            operation = self.store.read_json(operation_uri)
            request = DFTRequest.model_validate(operation["request"])
            self.backend.restore_job(
                request,
                ref,
                polls=int(operation.get("status_sequence", 0)),
                status=JobStatus(operation.get("status", JobStatus.CREATED.value)),
            )
            result = self.backend.cancel(ref)
        return CancelOutcome(
            external_job_ref=external_job_ref,
            status=self._external_status(result.value),
            idempotency_key=idempotency_key,
            message=f"mock backend returned {result.value}",
        )

    def _planning_payload(self, context: StageExecutionContext) -> dict[str, Any]:
        if not context.requirement_artifact.uri.startswith("artifact://"):
            raise ValueError("requirement artifact must use artifact:// URI")
        if not self.store.exists_with_hash(context.requirement_artifact.uri, context.requirement_artifact.sha256):
            raise ValueError("requirement artifact hash failed integrity")
        manifest_ref = context.input_artifacts["candidate_manifest"]
        if not self.store.exists_with_hash(manifest_ref.uri, manifest_ref.sha256):
            raise ValueError("candidate manifest hash failed integrity")
        requirement = Requirement.model_validate(self.store.read_json(context.requirement_artifact.uri))
        if not requirement.budget.allow_dft:
            raise ValueError("Requirement budget allow_dft must be true")
        rows = self.store.read_jsonl(manifest_ref.uri)
        if len(rows) != 1:
            raise ValueError("Task 4 mock lifecycle accepts exactly one candidate")
        row = rows[0]
        structure_uri = row.get("structure_artifact_uri")
        structure_hash = row.get("structure_artifact_sha256")
        if not structure_uri or not structure_hash or not self.store.exists_with_hash(structure_uri, structure_hash):
            raise ValueError("candidate structure artifact hash failed integrity")
        claims = [
            {"claim_type": target.name, "required_evidence_level": target.required_evidence_level.value}
            for target in requirement.scientific_targets
        ] or [{"claim_type": "workflow_lifecycle", "required_evidence_level": "L1_RETRIEVED"}]
        request_identity = {
            "run_id": context.run_id,
            "attempt": context.attempt,
            "manifest": manifest_ref.sha256,
        }
        return {
            "project_id": context.project_id,
            "run_id": context.run_id,
            "stage_run_id": f"{context.run_id}:dft:attempt-{context.attempt}",
            "request_id": f"dftreq_{canonical_hash(request_identity)[:24]}",
            "candidates": [{"candidate_id": row["candidate_id"], "structure_id": row["structure_id"], "structure_artifact_uri": structure_uri, "structure_artifact_sha256": structure_hash, "source_stage": "agent01"}],
            "requested_claims": claims,
            "workflow_template_id": "mock_dft_lifecycle_v1",
            "backend_id": "mock-dft",
            "execution_mode": "MOCK",
            "upstream_snapshot_hash": context.input_snapshot.sha256 if context.input_snapshot else manifest_ref.sha256,
        }

    def _request_from_native(self, context: StageExecutionContext, native: dict[str, Any], prepared: PreparedStagePlan) -> DFTRequest:
        workflow = native["workflow_plan"]
        payload = native["request_payload"]
        return DFTRequest(
            request_id=workflow["request_id"], project_id=context.project_id, run_id=context.run_id,
            stage_run_id=payload["stage_run_id"], execution_mode=ExecutionMode.MOCK,
            backend_id="mock-dft", candidates=tuple(self._candidate_inputs(payload)),
            requested_claims=tuple(ClaimRequest.model_validate(item) for item in workflow["requested_claims"]),
            workflow_template_id=workflow["template_id"], workflow_plan_hash=workflow["plan_hash"],
            task_specs=tuple(self._task_specs(workflow)), resource_estimate=workflow["resource_estimate"],
            approval_id=native["approval_payload"]["approval_id"], upstream_snapshot_hash=payload["upstream_snapshot_hash"], is_mock=True,
        )

    def _candidate_inputs(self, payload: dict[str, Any]):
        from .models import CandidateDFTInput
        return [CandidateDFTInput(candidate_id=item["candidate_id"], structure_id=item["structure_id"], structure_artifact=ArtifactRef(uri=item["structure_artifact_uri"], sha256=item["structure_artifact_sha256"]), source_stage=item["source_stage"]) for item in payload["candidates"]]

    def _task_specs(self, workflow: dict[str, Any]):
        from .models import DFTTaskSpec
        return [DFTTaskSpec.model_validate(item) for item in workflow["tasks"]]

    def _load_native(self, prepared: PreparedStagePlan) -> dict[str, Any]:
        if not self.store.exists_with_hash(prepared.native_plan_uri, prepared.native_plan_sha256):
            raise ValueError("native DFT plan failed integrity")
        return self.store.read_json(prepared.native_plan_uri)

    def _validate_prepared(self, context: StageExecutionContext, prepared: PreparedStagePlan) -> None:
        if prepared.stage is not StageId.DFT or prepared.agent_id != "agent03" or prepared.run_id != context.run_id or prepared.attempt != context.attempt:
            raise ValueError("prepared DFT plan does not match execution context")
        if context.input_snapshot is None or prepared.input_snapshot_sha256 != context.input_snapshot.sha256:
            raise ValueError("prepared DFT plan input snapshot differs from context")
        if not self.store.exists_with_hash(prepared.native_plan_uri, prepared.native_plan_sha256):
            raise ValueError("native DFT plan failed integrity")

    def _write_or_reuse(self, uri: str, value: dict[str, Any]):
        if self.store.exists(uri):
            if self.store.read_json(uri) != value:
                raise ValueError("existing DFT native plan conflicts with current input")
            return self.store.inspect(uri, media_type="application/json")
        return self.store.write_json(uri, value, immutable=True)

    @staticmethod
    def _operation_uri(context: StageExecutionContext, key: str) -> str:
        return DFTStageRunner._operation_uri_from_key(key)

    @staticmethod
    def _operation_uri_from_key(key: str) -> str:
        return f"stages/agent03/operations/{key}.json"

    @staticmethod
    def _terminal_operation_uri(context: StageExecutionContext, key: str) -> str:
        return f"stages/agent03/operations/{key}.terminal.json"

    @staticmethod
    def _status_uri(context: StageExecutionContext, key: str, sequence: int) -> str:
        return f"stages/agent03/{context.run_id}/attempt-{context.attempt}/operations/{key}.status-{sequence}.json"

    @staticmethod
    def _result_uri(context: StageExecutionContext, key: str) -> str:
        return f"stages/agent03/{context.run_id}/attempt-{context.attempt}/dft-result.json"

    def _outcome_from_operation(self, context: StageExecutionContext, prepared: PreparedStagePlan, operation: dict[str, Any]) -> ControlStageOutcome:
        ref = ExternalJobRef.model_validate(operation["external_job_ref"])
        status = JobStatus(operation["status"])
        if operation.get("result"):
            result_ref = ArtifactPointer.model_validate(operation["result"])
            return self._terminal_outcome(context, operation["idempotency_key"], ref, status, int(operation.get("status_sequence", 0)), result_ref, operation["result"]["uri"])
        status, sequence = self._latest_status(context, operation["idempotency_key"], operation)
        return self._waiting(context, operation["idempotency_key"], ref, sequence, status)

    def _latest_status(self, context, key: str, operation: dict[str, Any]) -> tuple[JobStatus, int]:
        sequence = 0
        status = JobStatus(operation["status"])
        while self.store.exists(self._status_uri(context, key, sequence + 1)):
            sequence += 1
            status = JobStatus(
                self.store.read_json(self._status_uri(context, key, sequence))["status"]
            )
        return status, sequence

    def _waiting(self, context, key, ref, sequence, status, snapshot_uri=None):
        return ControlStageOutcome(stage=context.stage, agent_id=context.agent_id, outcome=ControlOutcomeType.WAITING_EXTERNAL, status=StageStatus.RUNNING, idempotency_key=key, operation_ref=key, external_job_ref=ref.external_job_ref_id, external_status=self._external_status(status.value), external_status_sequence=sequence, summary={"is_mock": True, "status_snapshot_uri": snapshot_uri, "message": "Mock lifecycle only; no DFT calculation was executed."})

    def _terminal_outcome(self, context, key, ref, status, sequence, result_ref, operation_ref):
        mapped = {JobStatus.SUCCEEDED: StageStatus.SUCCEEDED, JobStatus.FAILED: StageStatus.PERMANENT_FAILED, JobStatus.TIMEOUT: StageStatus.RETRYABLE_FAILED, JobStatus.CANCELLED: StageStatus.CANCELLED}[status]
        outcome_type = ControlOutcomeType.COMPLETED if mapped in {StageStatus.SUCCEEDED, StageStatus.CANCELLED} else ControlOutcomeType.FAILED
        return ControlStageOutcome(stage=context.stage, agent_id=context.agent_id, outcome=outcome_type, status=mapped, idempotency_key=key, native_result_uri=result_ref.uri, native_result_sha256=result_ref.sha256, operation_ref=operation_ref, external_job_ref=ref.external_job_ref_id, external_status=self._external_status(status.value), external_status_sequence=sequence, summary={"is_mock": True, "claim_status": "NOT_EVALUATED_MOCK", "message": "Mock lifecycle only; no DFT calculation was executed."})

    def _failed(self, context, key, operation, exc, *, category="DFT_EXECUTION_FAILED"):
        return ControlStageOutcome(stage=context.stage, agent_id=context.agent_id, outcome=ControlOutcomeType.FAILED, status=StageStatus.PERMANENT_FAILED, idempotency_key=key, errors=[ControlError(category=category, operation=operation, public_message=str(exc))])

    def _find_ref(self, external_job_ref: str, key: str) -> ExternalJobRef:
        # cancel is called through the durable operation artifact, not memory.
        operation_dir = self.store.root / "stages" / "agent03" / "operations"
        candidates = [operation_dir / f"{key}.json"]
        if operation_dir.is_dir():
            candidates.extend(operation_dir.glob("*.json"))
        for path in candidates:
            if not path.is_file() or path.name.endswith(".terminal.json"):
                continue
            relative = path.relative_to(self.store.root)
            operation = self.store.read_json(f"artifact://{relative}")
            ref = ExternalJobRef.model_validate(operation["external_job_ref"])
            if ref.external_job_ref_id == external_job_ref:
                return ref
        raise ValueError("external job is not present in the operation ledger")

    @staticmethod
    def _external_status(status: str) -> ExternalJobStatus:
        return {"CREATED": ExternalJobStatus.SUBMITTED, "QUEUED": ExternalJobStatus.RUNNING, "RUNNING": ExternalJobStatus.RUNNING, "SUCCEEDED": ExternalJobStatus.SUCCEEDED, "FAILED": ExternalJobStatus.FAILED, "TIMEOUT": ExternalJobStatus.TIMEOUT, "CANCELLED": ExternalJobStatus.CANCELLED, "CANCEL_CONFIRMED": ExternalJobStatus.CANCELLED, "ALREADY_TERMINAL": ExternalJobStatus.CANCELLED}[status]
