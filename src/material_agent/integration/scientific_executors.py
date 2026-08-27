"""Concrete deterministic executors for structure-side scientific DAG nodes."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from pymatgen.core import Structure

from material_agent.dft.qe_remote import (
    QERemoteClient,
    QERemoteScientificBinding,
    QERemoteScientificSCFSummary,
)
from material_agent.inspiration.models import ArtifactPointerV1, canonical_json_bytes
from material_agent.integration.electronic_structure import (
    DFTTotalEnergyObservation,
    MagneticEnumerationPolicy,
    MagneticGroundStatePolicy,
    SurrogateScientificPrediction,
    SurrogateTriageDisposition,
    SurrogateTriagePolicy,
    TwoDStructurePolicy,
    ValidationVerdict,
    assess_magnetic_ground_state,
    assess_two_dimensional_structure,
    enumerate_collinear_magnetic_configurations,
    triage_surrogate_candidates,
)
from material_agent.integration.scientific_loop import (
    ModelExecutionReceipt,
    ScientificArtifactKind,
    ScientificEvidenceLevel,
    ScientificEvidenceVerdict,
    ScientificTaskKind,
    TaskExecutionBinding,
    make_scientific_task_artifact,
)
from material_agent.ml_screening.models import (
    EvidenceLevel,
    ExecutionStatus,
    MLCandidateResult,
    MLDecision,
    MLStagePlan,
)
from material_agent.ml_screening.worker_client import Agent02Worker
from material_agent.retrieval.models import ArtifactRef
from material_agent.retrieval.storage import LocalArtifactStore


def artifact_pointer_from_ref(reference: ArtifactRef) -> ArtifactPointerV1:
    return ArtifactPointerV1(
        uri=reference.uri,
        sha256=reference.sha256,
        size_bytes=reference.size_bytes,
        media_type=reference.media_type,
    )


@dataclass(frozen=True)
class LocalTwoDStructureExecutor:
    artifact_store: LocalArtifactStore
    executor_id: str = "local-two-d-structure-executor-v1"

    def execute(self, *, binding: TaskExecutionBinding) -> ModelExecutionReceipt:
        task = binding.proposed_task
        if task.task_kind is not ScientificTaskKind.TWO_D_STRUCTURE_CHECK:
            raise ValueError("2D executor received an incompatible scientific task")
        if task.produced_artifact_kinds != (
            ScientificArtifactKind.TWO_D_STRUCTURE_ASSESSMENT,
        ):
            raise ValueError("2D executor requires its exact typed output")
        structure = _read_structure(self.artifact_store, binding.source_structure)
        policy = TwoDStructurePolicy.model_validate_json(
            canonical_json_bytes(task.parameters)
        )
        assessment = assess_two_dimensional_structure(structure, policy)
        reference = self.artifact_store.write_json(
            (
                f"scientific_loop/tasks/{task.task_id}/"
                f"{binding.binding_id}/two-d-structure-assessment.json"
            ),
            assessment.model_dump(mode="json"),
            immutable=True,
        )
        pointer = artifact_pointer_from_ref(reference)
        artifact = make_scientific_task_artifact(
            candidate_id=task.candidate_id,
            producer_task_id=task.task_id,
            kind=ScientificArtifactKind.TWO_D_STRUCTURE_ASSESSMENT,
            pointer=pointer,
            is_mock=False,
        )
        verdict = {
            ValidationVerdict.PASS: ScientificEvidenceVerdict.SUPPORTS,
            ValidationVerdict.FAIL: ScientificEvidenceVerdict.CONTRADICTS,
            ValidationVerdict.INCONCLUSIVE: ScientificEvidenceVerdict.INCONCLUSIVE,
        }[assessment.verdict]
        return ModelExecutionReceipt(
            task_id=task.task_id,
            status="SUCCEEDED",
            verdict=verdict,
            tested_claim_ids=task.requested_observables,
            evidence_level=task.required_evidence_level,
            result_artifact=pointer,
            consumed_artifact_ids=tuple(
                item.artifact_id for item in binding.consumed_artifacts
            ),
            produced_artifacts=(artifact,),
            runtime_provenance={
                "executor_id": self.executor_id,
                "input_structure_sha256": binding.source_structure.sha256,
                "policy_sha256": _policy_sha256(policy),
            },
            reason_codes=assessment.reason_codes,
            real_execution=True,
        )


@dataclass(frozen=True)
class LocalMagneticEnumerationExecutor:
    artifact_store: LocalArtifactStore
    executor_id: str = "local-magnetic-enumeration-executor-v1"

    def execute(self, *, binding: TaskExecutionBinding) -> ModelExecutionReceipt:
        task = binding.proposed_task
        if (
            task.task_kind
            is not ScientificTaskKind.MAGNETIC_CONFIGURATION_ENUMERATION
        ):
            raise ValueError(
                "magnetic enumeration executor received an incompatible task"
            )
        if task.produced_artifact_kinds != (
            ScientificArtifactKind.MAGNETIC_CONFIGURATIONS,
        ):
            raise ValueError("magnetic executor requires its exact typed output")
        structure = _read_structure(self.artifact_store, binding.source_structure)
        policy = MagneticEnumerationPolicy.model_validate_json(
            canonical_json_bytes(task.parameters)
        )
        enumeration = enumerate_collinear_magnetic_configurations(
            structure,
            policy,
        )
        reference = self.artifact_store.write_json(
            (
                f"scientific_loop/tasks/{task.task_id}/"
                f"{binding.binding_id}/magnetic-configurations.json"
            ),
            enumeration.model_dump(mode="json"),
            immutable=True,
        )
        pointer = artifact_pointer_from_ref(reference)
        artifact = make_scientific_task_artifact(
            candidate_id=task.candidate_id,
            producer_task_id=task.task_id,
            kind=ScientificArtifactKind.MAGNETIC_CONFIGURATIONS,
            pointer=pointer,
            is_mock=False,
        )
        return ModelExecutionReceipt(
            task_id=task.task_id,
            status="SUCCEEDED",
            verdict=ScientificEvidenceVerdict.INCONCLUSIVE,
            tested_claim_ids=task.requested_observables,
            evidence_level=task.required_evidence_level,
            result_artifact=pointer,
            consumed_artifact_ids=tuple(
                item.artifact_id for item in binding.consumed_artifacts
            ),
            produced_artifacts=(artifact,),
            runtime_provenance={
                "executor_id": self.executor_id,
                "input_structure_sha256": binding.source_structure.sha256,
                "policy_sha256": _policy_sha256(policy),
            },
            reason_codes=("MAGNETIC_ENUMERATION_IS_NOT_GROUND_STATE_EVIDENCE",),
            real_execution=True,
        )


@dataclass(frozen=True)
class CalibratedMLPropertyExecutor:
    """Run one real calibrated model or ensemble without any DFT fallback."""

    artifact_store: LocalArtifactStore
    predictor: Callable[[TaskExecutionBinding], SurrogateScientificPrediction]
    executor_id: str = "calibrated-ml-property-executor-v1"

    def execute(self, *, binding: TaskExecutionBinding) -> ModelExecutionReceipt:
        task = binding.proposed_task
        if task.task_kind not in {
            ScientificTaskKind.ML_PROPERTY_PREDICTION,
            ScientificTaskKind.ML_ENSEMBLE_VALIDATION,
        }:
            raise ValueError("property executor received an incompatible task")
        expected_outputs = {
            ScientificArtifactKind.ML_PROPERTY_PREDICTIONS,
            ScientificArtifactKind.CALIBRATED_PROPERTY_ASSESSMENT,
        }
        if set(task.produced_artifact_kinds) != expected_outputs:
            raise ValueError("property executor requires prediction and assessment outputs")
        prediction = self.predictor(binding)
        if prediction.estimate.candidate_id != task.candidate_id:
            raise ValueError("property prediction candidate differs from DAG binding")
        if prediction.provenance.model_id != task.model_id:
            raise ValueError("property prediction model differs from audited capability")
        triage = triage_surrogate_candidates(
            (prediction.estimate,),
            SurrogateTriagePolicy(
                ensemble_validation_fraction=0,
                max_ensemble_candidates=0,
                confirm_top_clear_passes=False,
            ),
        )
        decision = triage.decisions[0]
        base = f"scientific_loop/tasks/{task.task_id}/{binding.binding_id}"
        prediction_ref = self.artifact_store.write_json(
            f"{base}/ml-property-prediction.json",
            prediction.model_dump(mode="json"),
            immutable=True,
        )
        assessment_ref = self.artifact_store.write_json(
            f"{base}/calibrated-property-assessment.json",
            {
                "decision": decision.model_dump(mode="json"),
                "prediction_sha256": prediction_ref.sha256,
                "scientific_conclusion": False,
            },
            immutable=True,
        )
        assessment_pointer = artifact_pointer_from_ref(assessment_ref)
        artifacts = tuple(
            sorted(
                (
                    make_scientific_task_artifact(
                        candidate_id=task.candidate_id,
                        producer_task_id=task.task_id,
                        kind=ScientificArtifactKind.ML_PROPERTY_PREDICTIONS,
                        pointer=artifact_pointer_from_ref(prediction_ref),
                        is_mock=False,
                    ),
                    make_scientific_task_artifact(
                        candidate_id=task.candidate_id,
                        producer_task_id=task.task_id,
                        kind=ScientificArtifactKind.CALIBRATED_PROPERTY_ASSESSMENT,
                        pointer=assessment_pointer,
                        is_mock=False,
                    ),
                ),
                key=lambda item: item.artifact_id,
            )
        )
        verdict = {
            SurrogateTriageDisposition.RETAIN_AT_L2: (
                ScientificEvidenceVerdict.SUPPORTS
            ),
            SurrogateTriageDisposition.REJECT_AT_L2: (
                ScientificEvidenceVerdict.CONTRADICTS
            ),
            SurrogateTriageDisposition.RETAIN_UNCERTAIN_AT_L2: (
                ScientificEvidenceVerdict.INCONCLUSIVE
            ),
            SurrogateTriageDisposition.ESCALATE_TO_ML_ENSEMBLE: (
                ScientificEvidenceVerdict.INCONCLUSIVE
            ),
        }[decision.disposition]
        provenance = prediction.provenance
        return ModelExecutionReceipt(
            task_id=task.task_id,
            status="SUCCEEDED",
            verdict=verdict,
            tested_claim_ids=task.requested_observables,
            evidence_level=task.required_evidence_level,
            result_artifact=assessment_pointer,
            consumed_artifact_ids=tuple(
                item.artifact_id for item in binding.consumed_artifacts
            ),
            produced_artifacts=artifacts,
            runtime_provenance={
                "benchmark_id": provenance.benchmark_id,
                "benchmark_report_sha256": provenance.benchmark_report_sha256,
                "checkpoint_sha256": provenance.checkpoint_sha256,
                "device": provenance.device,
                "environment_fingerprint_sha256": (
                    provenance.environment_fingerprint_sha256
                ),
                "executor_id": self.executor_id,
                "model_id": provenance.model_id,
                "task_kind": task.task_kind,
            },
            reason_codes=decision.reason_codes,
            real_execution=True,
        )


@dataclass(frozen=True)
class CHGNetScientificExecutor:
    """Adapt one frozen real Agent02 plan into an ML-pre-relax DAG receipt."""

    artifact_store: LocalArtifactStore
    worker: Agent02Worker
    plan_factory: Callable[[TaskExecutionBinding], tuple[MLStagePlan, str]]
    executor_id: str = "chgnet-scientific-executor-v1"

    def execute(self, *, binding: TaskExecutionBinding) -> ModelExecutionReceipt:
        task = binding.proposed_task
        if task.task_kind is not ScientificTaskKind.ML_PRE_RELAXATION:
            raise ValueError("CHGNet executor received an incompatible task")
        if task.produced_artifact_kinds != (
            ScientificArtifactKind.RELAXED_STRUCTURE,
        ):
            raise ValueError("CHGNet executor requires its exact typed output")
        if self.worker.is_mock:
            raise ValueError("mock Agent02 worker cannot enter the scientific DAG")
        plan, candidate_id = self.plan_factory(binding)
        planned = next(
            (
                item
                for item in plan.planned_candidates
                if item.candidate.candidate_id == candidate_id
            ),
            None,
        )
        if planned is None:
            raise ValueError("CHGNet plan does not contain the bound candidate")
        source = planned.candidate.source_structure
        if (
            source.uri != binding.source_structure.uri
            or source.sha256 != binding.source_structure.sha256
        ):
            raise ValueError("CHGNet plan structure differs from DAG binding")
        request = self.worker.request_for_candidate(plan, candidate_id)
        response = self.worker.run(request)
        response.validate_against_request(request)
        result = response.candidate_result
        if result is None:
            return self._failed_receipt(
                binding=binding,
                result=None,
                reason="CHGNET_WORKER_RETURNED_NO_RESULT",
            )
        if not _chgnet_relaxation_passed(result):
            return self._failed_receipt(
                binding=binding,
                result=result,
                reason="CHGNET_RELAXATION_QC_FAILED",
            )
        relaxation = result.relaxation_result
        assert relaxation is not None
        assert relaxation.output_structure_uri is not None
        assert relaxation.output_structure_sha256 is not None
        output_ref = self.artifact_store.inspect(
            relaxation.output_structure_uri,
            media_type="chemical/x-cif",
        )
        if output_ref.sha256 != relaxation.output_structure_sha256:
            raise ValueError("CHGNet relaxed structure failed Artifact integrity")
        output_pointer = artifact_pointer_from_ref(output_ref)
        summary_pointer = self._write_summary(binding, result)
        artifact = make_scientific_task_artifact(
            candidate_id=task.candidate_id,
            producer_task_id=task.task_id,
            kind=ScientificArtifactKind.RELAXED_STRUCTURE,
            pointer=output_pointer,
            is_mock=False,
        )
        identity = result.execution_identity
        return ModelExecutionReceipt(
            task_id=task.task_id,
            status="SUCCEEDED",
            verdict=ScientificEvidenceVerdict.SUPPORTS,
            tested_claim_ids=task.requested_observables,
            evidence_level=task.required_evidence_level,
            result_artifact=summary_pointer,
            consumed_artifact_ids=tuple(
                item.artifact_id for item in binding.consumed_artifacts
            ),
            produced_artifacts=(artifact,),
            runtime_provenance={
                "adapter_version": identity.adapter_version,
                "checkpoint_sha256": identity.checkpoint_sha256,
                "device": relaxation.device,
                "environment_fingerprint_sha256": (
                    identity.environment_fingerprint_sha256
                ),
                "executor_id": self.executor_id,
                "model_id": identity.model_id,
                "worker_protocol_version": identity.worker_protocol_version,
            },
            reason_codes=("CHGNET_RELAXATION_QC_PASSED",),
            real_execution=True,
        )

    def _failed_receipt(
        self,
        *,
        binding: TaskExecutionBinding,
        result: MLCandidateResult | None,
        reason: str,
    ) -> ModelExecutionReceipt:
        task = binding.proposed_task
        summary_pointer = self._write_summary(binding, result)
        return ModelExecutionReceipt(
            task_id=task.task_id,
            status="FAILED",
            verdict=ScientificEvidenceVerdict.INCONCLUSIVE,
            tested_claim_ids=task.requested_observables,
            evidence_level=ScientificEvidenceLevel.NONE,
            result_artifact=summary_pointer,
            consumed_artifact_ids=tuple(
                item.artifact_id for item in binding.consumed_artifacts
            ),
            runtime_provenance={"executor_id": self.executor_id},
            reason_codes=(reason,),
            real_execution=True,
        )

    def _write_summary(
        self,
        binding: TaskExecutionBinding,
        result: MLCandidateResult | None,
    ) -> ArtifactPointerV1:
        task = binding.proposed_task
        reference = self.artifact_store.write_json(
            (
                f"scientific_loop/tasks/{task.task_id}/"
                f"{binding.binding_id}/chgnet-execution-summary.json"
            ),
            {
                "binding_id": binding.binding_id,
                "candidate_result": (
                    result.model_dump(mode="json") if result is not None else None
                ),
                "executor_id": self.executor_id,
                "scientific_conclusion": False,
            },
            immutable=True,
        )
        return artifact_pointer_from_ref(reference)


@dataclass(frozen=True)
class QEScientificExecutor:
    """Execute one non-SOC self-consistent energy task on the remote QE worker."""

    artifact_store: LocalArtifactStore
    client: QERemoteClient
    binding_factory: Callable[
        [TaskExecutionBinding], QERemoteScientificBinding
    ]
    executor_id: str = "qe-remote-scientific-executor-v1"

    def execute(self, *, binding: TaskExecutionBinding) -> ModelExecutionReceipt:
        task = binding.proposed_task
        if task.task_kind is not ScientificTaskKind.DFT_NON_SOC:
            raise ValueError("QE SCF executor received an incompatible task")
        if task.produced_artifact_kinds != (
            ScientificArtifactKind.DFT_TOTAL_ENERGY,
        ):
            raise ValueError("QE SCF executor requires one total-energy output")
        scientific_binding = self.binding_factory(binding)
        if scientific_binding.source_structure_sha256 != binding.source_structure.sha256:
            raise ValueError("QE request structure differs from the DAG binding")
        if scientific_binding.soc_explicit:
            raise ValueError("non-SOC QE task cannot bind a spin-orbit calculation")
        try:
            result = self.client.run_scf(scientific_binding.request)
        except Exception as exc:  # noqa: BLE001
            return self._failed_receipt(
                binding=binding,
                scientific_binding=scientific_binding,
                exception=exc,
            )
        parsed = result.parsed_pw_result
        if not (
            result.completion.status == "SUCCEEDED"
            and result.completion.return_code == 0
            and result.completion.job_done
            and parsed.job_done
            and parsed.electronic_converged
            and parsed.total_energy_ry is not None
        ):
            return self._failed_receipt(
                binding=binding,
                scientific_binding=scientific_binding,
                exception=None,
            )
        summary = QERemoteScientificSCFSummary(
            binding=scientific_binding,
            remote_result=result,
        )
        reference = self.artifact_store.write_json(
            (
                f"scientific_loop/tasks/{task.task_id}/{binding.binding_id}/"
                f"qe-scf-{result.result_id}.json"
            ),
            summary.model_dump(mode="json"),
            immutable=True,
        )
        pointer = artifact_pointer_from_ref(reference)
        artifact = make_scientific_task_artifact(
            candidate_id=task.candidate_id,
            producer_task_id=task.task_id,
            kind=ScientificArtifactKind.DFT_TOTAL_ENERGY,
            pointer=pointer,
            is_mock=False,
        )
        return ModelExecutionReceipt(
            task_id=task.task_id,
            status="SUCCEEDED",
            verdict=ScientificEvidenceVerdict.SUPPORTS,
            tested_claim_ids=task.requested_observables,
            evidence_level=task.required_evidence_level,
            result_artifact=pointer,
            consumed_artifact_ids=tuple(
                item.artifact_id for item in binding.consumed_artifacts
            ),
            produced_artifacts=(artifact,),
            runtime_provenance={
                "executor_id": self.executor_id,
                "host_alias": result.host_alias,
                "magnetic_configuration_id": (
                    scientific_binding.magnetic_configuration_id
                ),
                "method_sha256": scientific_binding.method_sha256,
                "pw_binary_sha256": result.completion.pw_binary_sha256,
                "qe_version": parsed.qe_version,
                "remote_job_id": result.request.job_id,
                "source_structure_sha256": (
                    scientific_binding.source_structure_sha256
                ),
            },
            reason_codes=("QE_SELF_CONSISTENT_TOTAL_ENERGY_VALIDATED",),
            real_execution=True,
        )

    def _failed_receipt(
        self,
        *,
        binding: TaskExecutionBinding,
        scientific_binding: QERemoteScientificBinding,
        exception: Exception | None,
    ) -> ModelExecutionReceipt:
        task = binding.proposed_task
        payload = {
            "binding": scientific_binding.model_dump(mode="json"),
            "exception_type": type(exception).__name__ if exception else None,
            "executor_id": self.executor_id,
            "reason_code": "QE_SCF_EXECUTION_OR_CONVERGENCE_FAILED",
            "scientific_conclusion": False,
        }
        from material_agent.inspiration.models import canonical_sha256

        payload_sha256 = canonical_sha256(payload)
        reference = self.artifact_store.write_json(
            (
                f"scientific_loop/tasks/{task.task_id}/{binding.binding_id}/"
                f"qe-scf-failure-{payload_sha256[:24]}.json"
            ),
            payload,
            immutable=True,
        )
        return ModelExecutionReceipt(
            task_id=task.task_id,
            status="FAILED",
            verdict=ScientificEvidenceVerdict.EXECUTION_FAILED,
            tested_claim_ids=task.requested_observables,
            evidence_level=ScientificEvidenceLevel.NONE,
            result_artifact=artifact_pointer_from_ref(reference),
            consumed_artifact_ids=tuple(
                item.artifact_id for item in binding.consumed_artifacts
            ),
            runtime_provenance={"executor_id": self.executor_id},
            reason_codes=("QE_SCF_EXECUTION_OR_CONVERGENCE_FAILED",),
            real_execution=True,
        )


@dataclass(frozen=True)
class LocalMagneticGroundStateExecutor:
    """Compare multiple artifact-bound QE energies without inventing a state."""

    artifact_store: LocalArtifactStore
    executor_id: str = "local-magnetic-ground-state-executor-v1"

    def execute(self, *, binding: TaskExecutionBinding) -> ModelExecutionReceipt:
        task = binding.proposed_task
        if task.task_kind is not ScientificTaskKind.MAGNETIC_GROUND_STATE_ANALYSIS:
            raise ValueError("magnetic ground-state executor got an incompatible task")
        if task.produced_artifact_kinds != (
            ScientificArtifactKind.MAGNETIC_GROUND_STATE,
        ):
            raise ValueError("magnetic selector requires its exact typed output")
        energy_artifacts = tuple(
            item
            for item in binding.consumed_artifacts
            if item.kind is ScientificArtifactKind.DFT_TOTAL_ENERGY
        )
        if len(energy_artifacts) < 2:
            raise ValueError("magnetic selector requires at least two DFT energies")
        observations: list[DFTTotalEnergyObservation] = []
        for artifact in energy_artifacts:
            if not self.artifact_store.exists_with_hash(
                artifact.pointer.uri,
                artifact.pointer.sha256,
            ):
                raise ValueError("DFT total-energy Artifact failed integrity")
            summary = QERemoteScientificSCFSummary.model_validate(
                self.artifact_store.read_json(artifact.pointer.uri)
            )
            parsed = summary.remote_result.parsed_pw_result
            if parsed.total_energy_ry is None:
                raise ValueError("DFT total-energy Artifact has no total energy")
            observations.append(
                DFTTotalEnergyObservation(
                    result_artifact_sha256=artifact.pointer.sha256,
                    source_structure_sha256=(
                        summary.binding.source_structure_sha256
                    ),
                    method_sha256=summary.binding.method_sha256,
                    magnetic_configuration_id=(
                        summary.binding.magnetic_configuration_id
                    ),
                    total_energy_ry=parsed.total_energy_ry,
                    electronic_converged=parsed.electronic_converged,
                    soc_explicit=summary.binding.soc_explicit,
                )
            )
        policy = MagneticGroundStatePolicy.model_validate_json(
            canonical_json_bytes(task.parameters)
        )
        assessment = assess_magnetic_ground_state(tuple(observations), policy)
        reference = self.artifact_store.write_json(
            (
                f"scientific_loop/tasks/{task.task_id}/{binding.binding_id}/"
                "magnetic-ground-state-assessment.json"
            ),
            assessment.model_dump(mode="json"),
            immutable=True,
        )
        pointer = artifact_pointer_from_ref(reference)
        artifact = make_scientific_task_artifact(
            candidate_id=task.candidate_id,
            producer_task_id=task.task_id,
            kind=ScientificArtifactKind.MAGNETIC_GROUND_STATE,
            pointer=pointer,
            is_mock=False,
        )
        verdict = (
            ScientificEvidenceVerdict.SUPPORTS
            if assessment.verdict is ValidationVerdict.PASS
            else ScientificEvidenceVerdict.INCONCLUSIVE
        )
        return ModelExecutionReceipt(
            task_id=task.task_id,
            status="SUCCEEDED",
            verdict=verdict,
            tested_claim_ids=task.requested_observables,
            evidence_level=task.required_evidence_level,
            result_artifact=pointer,
            consumed_artifact_ids=tuple(
                item.artifact_id for item in binding.consumed_artifacts
            ),
            produced_artifacts=(artifact,),
            runtime_provenance={
                "executor_id": self.executor_id,
                "input_energy_artifact_count": len(energy_artifacts),
                "policy_sha256": _policy_sha256(policy),
            },
            reason_codes=assessment.reason_codes,
            real_execution=True,
        )


def _read_structure(
    store: LocalArtifactStore,
    pointer: ArtifactPointerV1,
) -> Structure:
    if not store.exists_with_hash(pointer.uri, pointer.sha256):
        raise ValueError("scientific structure Artifact failed hash verification")
    payload = store.read_bytes(pointer.uri)
    if pointer.size_bytes is not None and len(payload) != pointer.size_bytes:
        raise ValueError("scientific structure Artifact size mismatch")
    suffix = pointer.uri.rsplit(".", maxsplit=1)[-1].lower()
    format_name = "cif" if suffix == "cif" else "poscar"
    try:
        return Structure.from_str(payload.decode("utf-8"), fmt=format_name)
    except (UnicodeDecodeError, ValueError) as exc:
        raise ValueError("scientific structure Artifact is not parseable") from exc


def _policy_sha256(policy: object) -> str:
    from material_agent.inspiration.models import canonical_sha256

    return canonical_sha256(policy)


def _chgnet_relaxation_passed(result: MLCandidateResult) -> bool:
    relaxation = result.relaxation_result
    return bool(
        not result.execution_identity.is_mock
        and result.decision is MLDecision.PASS
        and result.evidence_level is EvidenceLevel.L2_ML_SCREENED
        and result.execution_status is ExecutionStatus.CONVERGED
        and relaxation is not None
        and relaxation.qc_passed
        and not relaxation.is_mock
        and relaxation.output_structure_uri
        and relaxation.output_structure_sha256
    )
