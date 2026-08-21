"""P0.2 Adapter for the lightweight Agent02 implementation.

The adapter deliberately consumes JSON artifacts and a worker object.  The
production registry does not import or register this module; tests may inject
the Fake worker through :class:`StageRunnerRegistry`.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from material_agent.ml_screening.models import (
    ArtifactPointer as MLArtifactPointer,
)
from material_agent.ml_screening.models import (
    ArtifactRef as MLArtifactRef,
)
from material_agent.ml_screening.models import (
    CandidateProperty,
    EvidenceLevel,
    ExecutionStatus,
    MLCandidateInput,
    MLCandidateManifestRecord,
    MLCandidateResult,
    MLDecision,
    MLModelRegistry,
    MLRelaxationResult,
    MLScreeningRequest,
    MLStagePlan,
    MLStageResultEnvelope,
    NativeStageStatus,
    RelaxationStatus,
    StructureRef,
)
from material_agent.ml_screening.planner import build_ml_stage_plan
from material_agent.ml_screening.reporting import render_stage_report
from material_agent.ml_screening.requirement import requirement_view_from_payload
from material_agent.ml_screening.resources import (
    default_policy,
)
from material_agent.ml_screening.worker_client import (
    Agent02Worker,
    WorkerProcessError,
)
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
from material_agent.orchestrator.runners import (
    _validate_prepared_plan_context,
)
from material_agent.retrieval.storage import LocalArtifactStore


class Agent02RunnerAdapter:
    """Map the frozen Agent02 native contract onto the P0.2 runner protocol."""

    def __init__(
        self,
        *,
        artifact_store: LocalArtifactStore,
        capability: StageCapability,
        worker: Agent02Worker,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        if capability.stage is not StageId.ML or not capability.registered:
            raise ValueError("Agent02 adapter requires an explicitly registered capability")
        if capability.is_mock != worker.is_mock:
            raise ValueError(
                "Agent02 capability mock marker differs from worker identity"
            )
        self.store = artifact_store
        self.capability = capability
        self.worker = worker
        self.backend_name = (
            "agent02-fake" if capability.is_mock else "agent02-chgnet"
        )
        self.now = now or (lambda: datetime.now(UTC))

    def validate_input(self, context: StageExecutionContext) -> StageInputValidation:
        missing = []
        if not context.requirement_artifact.uri:
            missing.append("requirement")
        for name in ("candidate_manifest", "policy", "registry", "health"):
            if name not in context.input_artifacts:
                missing.append(name)
        if missing:
            return StageInputValidation(
                valid=False,
                missing_fields=missing,
                error_code="MISSING_INPUT",
                failure_status=StageStatus.BLOCKED_MISSING_INPUT,
                remediation=["provide immutable Agent02 input artifacts and hashes"],
            )
        try:
            loaded = self._load_inputs(context)
            if len(loaded["candidates"]) > 20:
                return StageInputValidation(
                    valid=False,
                    errors=["candidate manifest exceeds the hard limit of 20"],
                    error_code="BATCH_LIMIT_EXCEEDED",
                    failure_status=StageStatus.BLOCKED_MISSING_INPUT,
                    remediation=["create a new Run with at most 20 candidates"],
                )
            if not loaded["requirement"].allow_ml:
                raise ValueError("Requirement budget allow_ml must be true")
            request = loaded["request"]
            if request.requested_candidate_ids:
                ids = {item.candidate_id for item in loaded["candidates"]}
                unknown = sorted(set(request.requested_candidate_ids) - ids)
                if unknown:
                    raise ValueError(f"explicit candidate IDs are unknown: {unknown}")
            return StageInputValidation(valid=True)
        except FileNotFoundError as exc:
            return StageInputValidation(
                valid=False,
                errors=[str(exc)],
                error_code="MISSING_INPUT",
                failure_status=StageStatus.BLOCKED_MISSING_INPUT,
            )
        except (ValueError, TypeError, json.JSONDecodeError, KeyError) as exc:
            return StageInputValidation(
                valid=False,
                errors=[str(exc)],
                error_code="INPUT_INTEGRITY_ERROR",
                failure_status=StageStatus.PERMANENT_FAILED,
                remediation=["create a new Run with corrected immutable artifacts"],
            )

    def prepare(self, context: StageExecutionContext) -> PreparedStagePlan:
        validation = self.validate_input(context)
        if not validation.valid:
            raise ValueError(validation.errors[0] if validation.errors else validation.error_code)
        loaded = self._load_inputs(context)
        native_uri = (
            f"plans/{context.run_id}/stages/ml/attempt-{context.attempt}.native.json"
        )
        native = build_ml_stage_plan(
            project_id=context.project_id,
            run_id=context.run_id,
            requirement_revision=context.requirement_revision,
            attempt=context.attempt,
            orchestrator_input_snapshot=MLArtifactPointer(
                uri=context.input_snapshot.uri, sha256=context.input_snapshot.sha256
            ),
            requirement_artifact=loaded["refs"]["requirement"],
            candidate_manifest_artifact=loaded["refs"]["candidate_manifest"],
            stage_request_artifact=loaded["refs"].get("stage_request"),
            policy_artifact=loaded["refs"]["policy"],
            registry_artifact=loaded["refs"]["registry"],
            health_artifact=loaded["refs"]["health"],
            requirement=loaded["requirement"],
            candidates=loaded["candidates"],
            request=loaded["request"],
            policy=loaded["policy"],
            registry=loaded["registry"],
            health=loaded["health"],
            created_at=self.now(),
        )
        payload = native.model_dump(mode="json")
        if self.store.exists(native_uri):
            existing = self.store.read_json(native_uri)
            if existing != payload:
                raise ValueError("existing Agent02 native plan conflicts with current input")
            native_ref = self.store.inspect(native_uri, media_type="application/json")
        else:
            native_ref = self.store.write_json(native_uri, payload, immutable=True)
        operation_hash = operation_input_sha256_for(
            project_id=context.project_id,
            run_id=context.run_id,
            requirement_revision=context.requirement_revision,
            stage=context.stage,
            agent_id=context.agent_id,
            attempt=context.attempt,
            input_snapshot_sha256=context.input_snapshot.sha256,
            native_plan_sha256=native_ref.sha256,
            policy_version=native.policy_version,
        )
        return PreparedStagePlan(
            project_id=context.project_id, run_id=context.run_id,
            requirement_revision=context.requirement_revision, stage=context.stage,
            agent_id=context.agent_id, attempt=context.attempt,
            input_snapshot_uri=context.input_snapshot.uri,
            input_snapshot_sha256=context.input_snapshot.sha256,
            native_plan_uri=native_ref.uri, native_plan_sha256=native_ref.sha256,
            operation_input_sha256=operation_hash,
            approval_required=native.approval_required,
            gate_type="EXPENSIVE_BATCH_APPROVAL" if native.approval_required else None,
            resource_estimate=native.resource_estimate.model_dump(mode="json"),
            policy_version=native.policy_version,
            risk_summary=(
                "Fake Worker only; results remain L1_RETRIEVED and produce no L2 evidence."
                if native.execution_identity.is_mock
                else "Real CHGNet MLIP run; output is not DFT or experimental evidence."
            ),
            created_at=native.created_at,
        )

    def start(self, context: StageExecutionContext, prepared_plan: PreparedStagePlan, idempotency_key: str) -> ControlStageOutcome:
        try:
            _validate_prepared_plan_context(prepared_plan, context)
            if not self.store.exists_with_hash(prepared_plan.native_plan_uri, prepared_plan.native_plan_sha256):
                raise ValueError("Agent02 native plan failed integrity check")
            plan = MLStagePlan.model_validate(self.store.read_json(prepared_plan.native_plan_uri))
            self._validate_plan(plan, context, prepared_plan)
            result_ref = self._run_plan(plan, prepared_plan, idempotency_key)
            result = MLStageResultEnvelope.model_validate(self.store.read_json(result_ref.uri))
            if result.status in {
                NativeStageStatus.RETRYABLE_FAILED,
                NativeStageStatus.PERMANENT_FAILED,
            }:
                retryable = result.status is NativeStageStatus.RETRYABLE_FAILED
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
                    native_result_uri=result_ref.uri,
                    native_result_sha256=result_ref.sha256,
                    operation_ref=idempotency_key,
                    errors=[
                        ControlError(
                            category=(
                                "AGENT02_RETRYABLE_WORKER_FAILURE"
                                if retryable
                                else "AGENT02_EXECUTION_FAILED"
                            ),
                            operation="start",
                            retryable=retryable,
                            public_message="; ".join(result.errors),
                        )
                    ],
                )
            return ControlStageOutcome(
                stage=context.stage, agent_id=context.agent_id,
                outcome=ControlOutcomeType.COMPLETED,
                status=StageStatus.PARTIAL if result.status is NativeStageStatus.PARTIAL else StageStatus.SUCCEEDED,
                idempotency_key=idempotency_key,
                native_result_uri=result_ref.uri, native_result_sha256=result_ref.sha256,
                operation_ref=idempotency_key,
                summary={
                    "is_mock": result.execution_identity.is_mock,
                    "candidate_ids": result.candidate_ids,
                    "status_counts": result.status_counts,
                    "artifacts": {"candidate_manifest": {"uri": result.candidate_manifest.uri, "sha256": result.candidate_manifest.sha256}},
                },
            )
        except Exception as exc:  # noqa: BLE001
            return ControlStageOutcome(
                stage=context.stage, agent_id=context.agent_id,
                outcome=ControlOutcomeType.FAILED,
                status=StageStatus.PERMANENT_FAILED,
                idempotency_key=idempotency_key,
                errors=[ControlError(category="BACKEND_INCONSISTENT" if "integrity" in str(exc).lower() else "AGENT02_EXECUTION_FAILED", operation="start", public_message=str(exc))],
            )

    def reconcile(self, context: StageExecutionContext, prepared_plan: PreparedStagePlan, external_job_ref: str, idempotency_key: str) -> ControlStageOutcome:
        del context, prepared_plan, external_job_ref
        return ControlStageOutcome(
            stage=StageId.ML, agent_id="agent02", outcome=ControlOutcomeType.FAILED,
            status=StageStatus.PERMANENT_FAILED, idempotency_key=idempotency_key,
            errors=[ControlError(category="UNSUPPORTED_OPERATION", operation="reconcile", public_message="Agent02 v1 is synchronous and has no external job")],
        )

    def cancel(self, external_job_ref: str, idempotency_key: str) -> CancelOutcome:
        return CancelOutcome(external_job_ref=external_job_ref, status=ExternalJobStatus.CANCELLED, idempotency_key=idempotency_key, message="Agent02 v1 has no cancellable external job")

    def _load_inputs(self, context: StageExecutionContext) -> dict[str, Any]:
        refs = {"requirement": context.requirement_artifact, **context.input_artifacts}
        checked: dict[str, Any] = {}
        for name, ref in refs.items():
            if not ref.uri.startswith("artifact://") or not self.store.exists_with_hash(ref.uri, ref.sha256):
                raise ValueError(f"{name} artifact URI or hash failed integrity")
            checked[name] = ref
        requirement_payload = self.store.read_json(refs["requirement"].uri)
        if requirement_payload.get("revision") != context.requirement_revision:
            raise ValueError("requirement revision differs from context")
        requirement = requirement_view_from_payload(requirement_payload)
        manifest_payload = self.store.read_jsonl(refs["candidate_manifest"].uri)
        candidates = [self._candidate_from_agent01(item, refs["candidate_manifest"].uri, refs["candidate_manifest"].sha256) for item in manifest_payload]
        policy = default_policy().__class__.model_validate(self.store.read_json(refs["policy"].uri))
        registry = MLModelRegistry.model_validate(self.store.read_json(refs["registry"].uri))
        from material_agent.ml_screening.models import ModelHealthSnapshot
        health = ModelHealthSnapshot.model_validate(self.store.read_json(refs["health"].uri))
        request = MLScreeningRequest()
        if "stage_request" in refs:
            request = MLScreeningRequest.model_validate(self.store.read_json(refs["stage_request"].uri))
        return {"refs": {name: MLArtifactPointer(uri=ref.uri, sha256=ref.sha256) for name, ref in checked.items()}, "requirement": requirement, "candidates": candidates, "policy": policy, "registry": registry, "health": health, "request": request}

    def _candidate_from_agent01(self, item: dict[str, Any], manifest_uri: str, manifest_sha: str) -> MLCandidateInput:
        if item.get("schema_version") not in {
            "agent01-contract-v1",
            "agent01-contract-v2",
        }:
            raise ValueError("candidate manifest schema version is invalid")
        structure_uri = item.get("structure_artifact_uri")
        structure_sha = item.get("structure_artifact_sha256")
        structure_id = item.get("structure_id")
        if not all(isinstance(value, str) and value for value in (structure_uri, structure_sha, structure_id)):
            raise ValueError(f"candidate {item.get('candidate_id')} has incomplete structure reference")
        if not self.store.exists_with_hash(structure_uri, structure_sha):
            raise ValueError(f"candidate {item.get('candidate_id')} structure hash failed integrity")
        source = self._read_structure(item, structure_uri, structure_sha, structure_id)
        properties = [CandidateProperty(name=p["name"], value=p.get("value"), unit=p["unit"]) for p in item.get("properties", []) if p.get("name") in {"band_gap", "energy_above_hull", "is_metal", "num_sites"}]
        decision = MLDecision(item.get("decision", "UNCERTAIN"))
        return MLCandidateInput(candidate_id=item["candidate_id"], upstream_manifest_uri=manifest_uri, upstream_manifest_sha256=manifest_sha, formula=item.get("formula", item.get("reduced_formula", "unknown")), elements=list(item.get("elements", source.elements)), num_sites=item.get("num_sites", source.num_sites), publication_rank=item.get("publication_rank"), upstream_decision=decision, upstream_evidence_level=EvidenceLevel.L1_RETRIEVED, properties=properties, source_structure=source)

    def _read_structure(self, item: dict[str, Any], uri: str, sha: str, structure_id: str) -> StructureRef:
        raw = self.store.read_bytes(uri)
        text = raw.decode("utf-8", errors="replace")
        source_uri = item.get("structure_source_artifact_uri")
        source = self.store.read_json(source_uri) if source_uri and self.store.exists(source_uri) else None
        sites = source.get("sites", []) if isinstance(source, dict) else []
        elements = sorted({species.get("element") for site in sites for species in site.get("species", []) if species.get("element")})
        if not elements:
            elements = list(item.get("elements", []))
        if not elements:
            formula_match = re.search(r"_chemical_formula_sum\s+'([^']+)'", text)
            if formula_match:
                elements = sorted(set(re.findall(r"[A-Z][a-z]?", formula_match.group(1))))
        lattice = source.get("lattice", {}) if isinstance(source, dict) else {}
        pbc = lattice.get("pbc")
        dimensionality = next((p.get("value") for p in item.get("properties", []) if p.get("name") == "structural_dimensionality"), None)
        return StructureRef(structure_id=structure_id, uri=uri, sha256=sha, num_sites=len(sites) or item.get("num_sites"), elements=elements, dimensionality=dimensionality, is_periodic=all(pbc) if pbc else (True if "_cell_length_a" in text else None), is_inorganic=("C" not in elements), parseable=bool(raw), ase_compatible=True, has_finite_values=True, positive_volume=True, minimum_distance_angstrom=1.0, hash_verified=True)

    def _validate_plan(self, plan: MLStagePlan, context: StageExecutionContext, prepared: PreparedStagePlan) -> None:
        if (plan.project_id, plan.run_id, plan.attempt) != (context.project_id, context.run_id, context.attempt):
            raise ValueError("Agent02 native plan identity differs from context")
        if plan.orchestrator_input_snapshot.sha256 != prepared.input_snapshot_sha256:
            raise ValueError("Agent02 native plan input snapshot differs")

    def _run_plan(self, plan: MLStagePlan, prepared: PreparedStagePlan, operation_key: str):
        result_uri = f"stages/agent02/{plan.run_id}/attempt-{plan.attempt}/stage-result.json"
        if self.store.exists(result_uri):
            completion_uri = f"stages/agent02/{plan.run_id}/attempt-{plan.attempt}/stage-operation-complete.json"
            if not self.store.exists(completion_uri):
                raise ValueError("completed stage is missing its completion record")
            completion = self.store.read_json(completion_uri)
            ref = self.store.inspect(result_uri, media_type="application/json")
            if completion.get("result_sha256") != ref.sha256 or completion.get("operation_key") != operation_key:
                raise ValueError("completed stage artifact failed integrity")
            return ref
        records = []
        errors = []
        retryable_errors = 0
        for planned in plan.planned_candidates:
            cid = planned.candidate.candidate_id
            if planned.selection_status.value == "SELECTED" and plan.allow_real_inference:
                key = plan.candidate_operation_keys[cid]
                complete_uri = f"stages/agent02/{plan.run_id}/candidate-operations/{key}/operation-complete.json"
                if self.store.exists(complete_uri):
                    payload = self.store.read_json(complete_uri)
                    record_payload = payload.get("record")
                    expected_record_hash = hashlib.sha256(json.dumps(record_payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()
                    if payload.get("candidate_operation_key") != key or payload.get("record_sha256") != expected_record_hash:
                        raise ValueError("candidate operation completion key conflict")
                    record = MLCandidateManifestRecord.model_validate(record_payload)
                else:
                    try:
                        request = self.worker.request_for_candidate(plan, cid)
                        response = self.worker.run(request)
                        response.validate_against_request(request)
                        record = response.candidate_result and MLCandidateManifestRecord(candidate=response.candidate_result, scientific_rank=planned.candidate.publication_rank)
                        if record is None:
                            raise ValueError("worker response did not contain a candidate result")
                        record = self._materialize_record(record, plan)
                        record_payload = record.model_dump(mode="json")
                        record_hash = hashlib.sha256(json.dumps(record_payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()
                        self.store.write_json(complete_uri, {"schema_version": "agent02-candidate-operation-v1", "candidate_operation_key": key, "record_sha256": record_hash, "record": record_payload}, immutable=True)
                    except Exception as exc:  # noqa: BLE001
                        if isinstance(exc, WorkerProcessError) and exc.category in {
                            "WORKER_TIMEOUT",
                            "WORKER_START_FAILED",
                        }:
                            retryable_errors += 1
                        errors.append(f"{cid}: {type(exc).__name__}: {exc}")
                        records.append(self._failed_record(planned, plan, str(exc)))
                        continue
                records.append(record)
            else:
                records.append(self._not_run_record(planned, plan))
        if errors and not records:
            raise RuntimeError("all Agent02 candidates failed: " + "; ".join(errors))
        records.sort(key=lambda record: record.candidate.candidate_id)
        manifest_ref = self.store.write_jsonl(f"stages/agent02/{plan.run_id}/attempt-{plan.attempt}/ml-candidate-manifest.jsonl", [record.model_dump(mode="json") for record in records], immutable=True)
        report_ref = self.store.write_text(f"stages/agent02/{plan.run_id}/attempt-{plan.attempt}/report.md", render_stage_report(plan, records), media_type="text/markdown", immutable=True)
        failed_count = sum(
            record.candidate.decision is MLDecision.FAILED
            for record in records
        )
        if not errors:
            status = NativeStageStatus.SUCCEEDED
        elif failed_count < len(records):
            status = NativeStageStatus.PARTIAL
        elif retryable_errors == len(errors):
            status = NativeStageStatus.RETRYABLE_FAILED
        else:
            status = NativeStageStatus.PERMANENT_FAILED
        # Build the strict envelope only after deterministic summaries/counts are known.
        from material_agent.ml_screening.models import MLCandidateResultSummary
        summaries = [MLCandidateResultSummary.from_result(r.candidate) for r in records]
        counts = dict(sorted({d.value: sum(r.candidate.decision is d for r in records) for d in {r.candidate.decision for r in records}}.items()))
        manifest_native_ref = MLArtifactRef.model_validate(manifest_ref.model_dump(mode="json"))
        report_native_ref = MLArtifactRef.model_validate(report_ref.model_dump(mode="json"))
        envelope = MLStageResultEnvelope(project_id=plan.project_id, run_id=plan.run_id, status=status, operation_key=operation_key, plan=MLArtifactPointer(uri=prepared.native_plan_uri, sha256=prepared.native_plan_sha256), execution_identity=plan.execution_identity, candidate_manifest=manifest_native_ref, output_artifacts=[report_native_ref], candidate_ids=[r.candidate.candidate_id for r in records], candidate_summaries=summaries, status_counts=counts, errors=errors, started_at=plan.created_at, finished_at=self.now(), provenance={"is_mock": plan.execution_identity.is_mock})
        result_ref = self.store.write_json(result_uri, envelope.model_dump(mode="json"), immutable=True)
        self.store.write_json(
            f"stages/agent02/{plan.run_id}/attempt-{plan.attempt}/stage-operation-complete.json",
            {"schema_version": "agent02-stage-operation-v1", "operation_key": operation_key, "result_sha256": result_ref.sha256},
            immutable=True,
        )
        return result_ref

    def _materialize_record(self, record: MLCandidateManifestRecord, plan: MLStagePlan) -> MLCandidateManifestRecord:
        candidate = record.candidate
        relaxation = candidate.relaxation_result
        if relaxation is None:
            return record
        if not candidate.execution_identity.is_mock:
            output_uri = relaxation.output_structure_uri
            output_hash = relaxation.output_structure_sha256
            if (
                output_uri is None
                or output_hash is None
                or not self.store.exists_with_hash(output_uri, output_hash)
            ):
                raise ValueError(
                    "real worker relaxed structure failed Artifact integrity"
                )
            return record
        raw = self.store.read_bytes(candidate.source_structure_uri)
        key = candidate.candidate_operation_key or "unknown"
        output_uri = f"stages/agent02/{plan.run_id}/candidate-operations/{key}/relaxed.cif"
        output_ref = self.store.write_bytes(output_uri, raw, media_type="chemical/x-cif", immutable=True)
        relaxation = relaxation.model_copy(update={"output_structure_uri": output_ref.uri, "output_structure_sha256": output_ref.sha256})
        lineage = candidate.structure_lineage.model_copy(update={"structure_uri": output_ref.uri, "structure_sha256": output_ref.sha256}) if candidate.structure_lineage else None
        return MLCandidateManifestRecord(candidate=candidate.model_copy(update={"relaxation_result": relaxation, "structure_lineage": lineage}), scientific_rank=record.scientific_rank)

    def _failed_record(self, planned: Any, plan: MLStagePlan, message: str) -> MLCandidateManifestRecord:
        """Create an auditable failed candidate without fabricating ML values."""
        candidate = planned.candidate
        relaxation = MLRelaxationResult(
            candidate_id=candidate.candidate_id,
            input_structure_id=candidate.source_structure.structure_id,
            input_structure_uri=candidate.source_structure.uri,
            input_structure_sha256=candidate.source_structure.sha256,
            model_id=plan.execution_identity.model_id,
            checkpoint_sha256=plan.execution_identity.checkpoint_sha256,
            adapter_version=plan.execution_identity.adapter_version,
            execution_identity=plan.execution_identity,
            device=plan.device_policy,
            relaxation_profile=plan.relaxation_profile,
            status=RelaxationStatus.RUNTIME_FAILED,
            num_steps=0,
            qc_passed=False,
            errors=[message],
            wall_time_seconds=0.0,
            is_mock=plan.execution_identity.is_mock,
            provenance={"worker_protocol": plan.execution_identity.worker_protocol_version},
        )
        return MLCandidateManifestRecord(
            candidate=MLCandidateResult(
                project_id=plan.project_id,
                run_id=plan.run_id,
                candidate_id=candidate.candidate_id,
                candidate_operation_key=plan.candidate_operation_keys[
                    candidate.candidate_id
                ],
                upstream_manifest_uri=candidate.upstream_manifest_uri,
                upstream_manifest_sha256=candidate.upstream_manifest_sha256,
                source_structure_id=candidate.source_structure.structure_id,
                source_structure_uri=candidate.source_structure.uri,
                source_structure_sha256=candidate.source_structure.sha256,
                pre_filter_decision=planned.pre_filter.decision,
                applicability=planned.applicability,
                selection_status=planned.selection_status,
                execution_status=ExecutionStatus.RUNTIME_FAILED,
                decision=MLDecision.FAILED,
                evidence_level=EvidenceLevel.L1_RETRIEVED,
                execution_identity=plan.execution_identity,
                relaxation_result=relaxation,
                recommended_downstream_structure_id=(
                    candidate.source_structure.structure_id
                ),
                warnings=list(planned.applicability.warnings),
                errors=[message],
            ),
            scientific_rank=candidate.publication_rank,
        )

    def _not_run_record(
        self, planned: Any, plan: MLStagePlan
    ) -> MLCandidateManifestRecord:
        candidate = planned.candidate
        decision = (
            MLDecision.REJECT
            if planned.pre_filter.decision is MLDecision.REJECT
            else (
                MLDecision.FAILED
                if planned.pre_filter.decision is MLDecision.FAILED
                else MLDecision.UNCERTAIN
            )
        )
        result = MLCandidateResult(
            project_id=plan.project_id,
            run_id=plan.run_id,
            candidate_id=candidate.candidate_id,
            upstream_manifest_uri=candidate.upstream_manifest_uri,
            upstream_manifest_sha256=candidate.upstream_manifest_sha256,
            source_structure_id=candidate.source_structure.structure_id,
            source_structure_uri=candidate.source_structure.uri,
            source_structure_sha256=candidate.source_structure.sha256,
            pre_filter_decision=planned.pre_filter.decision,
            applicability=planned.applicability,
            selection_status=planned.selection_status,
            execution_status=ExecutionStatus.NOT_RUN,
            decision=decision,
            evidence_level=EvidenceLevel.L1_RETRIEVED,
            execution_identity=plan.execution_identity,
            recommended_downstream_structure_id=(
                candidate.source_structure.structure_id
            ),
            warnings=list(planned.applicability.warnings),
        )
        return MLCandidateManifestRecord(
            candidate=result, scientific_rank=candidate.publication_rank
        )
