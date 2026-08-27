"""Fail-closed soft-chemistry downstream plan and execution bridge.

This module joins existing native capabilities without replacing them:

``registered operator result -> Agent02 CHGNet worker -> optional DeepH/DFT``.

Only the existing independent subprocess clients and the structured real DFT
bridge are accepted.  Missing configuration is represented as ``BLOCKED`` or
``NOT_RUN``; no fixture or mock result can upgrade evidence.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Literal

from pydantic import Field, model_validator

from material_agent.dft.models import (
    DFTResultEnvelope,
    ExternalJobRef,
    JobStatus,
)
from material_agent.dft.models import (
    canonical_hash as dft_canonical_hash,
)
from material_agent.dft.preflight import (
    DFTPreflightInput,
    PreflightStatus,
    evaluate_real_preflight,
)
from material_agent.dft.vaspilot_backend import VASPilotBackend
from material_agent.inspiration.models import (
    ArtifactPointerV1,
    Identifier,
    StrictModel,
    TransformationPlanV1,
    TransformationStatus,
    ValidationStatus,
    canonical_sha256,
)
from material_agent.inspiration.transformations import (
    STRUCTURE_ARTIFACT_MEDIA_TYPE,
    TransformationExecutionResult,
)
from material_agent.ml_screening.deeph_client import (
    DeepHFlowRunner,
    DeepHSubprocessClient,
)
from material_agent.ml_screening.deeph_models import (
    DeepHInferenceRequest,
    DeepHResult,
    DeepHWorkerStatus,
)
from material_agent.ml_screening.deeph_planner import build_deeph_plan
from material_agent.ml_screening.models import (
    ApplicabilityStatus,
    EvidenceLevel,
    MLCandidateResult,
    MLDecision,
    MLModelRegistry,
    MLStagePlan,
    ModelHealthSnapshot,
    RelaxationStatus,
    SelectionStatus,
    SmokeTestStatus,
    WorkerResponseStatus,
)
from material_agent.ml_screening.real_resources import (
    AGENT02_PACKAGE_LOCK_SHA256,
    CHGNET_ADAPTER_VERSION,
    CHGNET_CHECKPOINT_SHA256,
    CHGNET_MODEL_ID,
)
from material_agent.ml_screening.worker_client import (
    SubprocessWorkerClient,
    sha256_file,
)
from material_agent.retrieval.storage import LocalArtifactStore
from material_agent.softchem.registry import (
    SoftChemOperatorRegistryV1,
    softchem_registry_bytes,
)

SOFTCHEM_DOWNSTREAM_PLAN_VERSION = "softchem-downstream-plan-v1"
SOFTCHEM_DOWNSTREAM_RESULT_VERSION = "softchem-downstream-result-v1"


class DownstreamIntent(StrEnum):
    RUN = "RUN"
    SKIP = "SKIP"


class StageStatus(StrEnum):
    SUCCEEDED = "SUCCEEDED"
    COMPLETED = "COMPLETED"
    SUBMITTED = "SUBMITTED"
    BLOCKED = "BLOCKED"
    NOT_RUN = "NOT_RUN"
    FAILED = "FAILED"


class StageOutcomeV1(StrictModel):
    stage_id: Literal["operator", "chgnet", "deeph", "dft"]
    status: StageStatus
    reason_codes: tuple[Identifier, ...]
    input_structure: ArtifactPointerV1 | None = None
    output_structure: ArtifactPointerV1 | None = None
    evidence_level: Literal["NONE", "L2_ML_SCREENED"] = "NONE"
    scientific_conclusion: Literal[False] = False

    @model_validator(mode="after")
    def validate_outcome(self) -> StageOutcomeV1:
        if not self.reason_codes:
            raise ValueError("stage outcome requires at least one reason code")
        if len(set(self.reason_codes)) != len(self.reason_codes):
            raise ValueError("stage reason codes must be unique")
        if self.evidence_level == "L2_ML_SCREENED" and not (
            self.stage_id == "chgnet" and self.status is StageStatus.SUCCEEDED
        ):
            raise ValueError("only a successful real CHGNet stage may carry L2")
        return self


class SoftChemDownstreamPlanV1(StrictModel):
    schema_version: Literal["softchem-downstream-plan-v1"] = (
        SOFTCHEM_DOWNSTREAM_PLAN_VERSION
    )
    operation_key: str = Field(pattern=r"^[0-9a-f]{64}$")
    parent_candidate_id: Identifier
    transformation_plan_id: Identifier
    transformation_route_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    operator_registry_artifact: ArtifactPointerV1
    operator_id: Identifier
    operator_version: str = Field(min_length=1)
    proposed_structure: ArtifactPointerV1 | None = None
    chgnet_plan_sha256: str | None = Field(
        default=None, pattern=r"^[0-9a-f]{64}$"
    )
    deeph_intent: DownstreamIntent
    deeph_request_sha256: str | None = Field(
        default=None, pattern=r"^[0-9a-f]{64}$"
    )
    dft_intent: DownstreamIntent
    dft_preflight_sha256: str | None = Field(
        default=None, pattern=r"^[0-9a-f]{64}$"
    )
    created_at: datetime

    @model_validator(mode="after")
    def validate_plan_identity(self) -> SoftChemDownstreamPlanV1:
        if self.created_at.tzinfo is None:
            raise ValueError("downstream plan timestamp must be timezone-aware")
        expected = _downstream_operation_key(
            parent_candidate_id=self.parent_candidate_id,
            transformation_plan_id=self.transformation_plan_id,
            transformation_route_sha256=self.transformation_route_sha256,
            operator_registry_artifact=self.operator_registry_artifact,
            operator_id=self.operator_id,
            operator_version=self.operator_version,
            proposed_structure=self.proposed_structure,
            chgnet_plan_sha256=self.chgnet_plan_sha256,
            deeph_intent=self.deeph_intent,
            deeph_request_sha256=self.deeph_request_sha256,
            dft_intent=self.dft_intent,
            dft_preflight_sha256=self.dft_preflight_sha256,
        )
        if expected != self.operation_key:
            raise ValueError("downstream operation key differs from frozen inputs")
        return self


class SoftChemDownstreamResultV1(StrictModel):
    schema_version: Literal["softchem-downstream-result-v1"] = (
        SOFTCHEM_DOWNSTREAM_RESULT_VERSION
    )
    operation_key: str = Field(pattern=r"^[0-9a-f]{64}$")
    operator: StageOutcomeV1
    chgnet: StageOutcomeV1
    deeph: StageOutcomeV1
    dft: StageOutcomeV1
    transformation_plan: TransformationPlanV1
    chgnet_result: MLCandidateResult | None = None
    deeph_result: DeepHResult | None = None
    dft_job: ExternalJobRef | None = None
    scientific_conclusion: Literal[False] = False

    @model_validator(mode="after")
    def prevent_mock_evidence(self) -> SoftChemDownstreamResultV1:
        if self.chgnet_result is not None and self.chgnet_result.execution_identity.is_mock:
            raise ValueError("softchem pipeline cannot retain a mock CHGNet result")
        if self.deeph_result is not None and self.deeph_result.is_mock:
            raise ValueError("softchem pipeline cannot retain a mock DeepH result")
        if self.dft_job is not None and self.dft_job.is_mock:
            raise ValueError("softchem pipeline cannot retain a mock DFT job")
        return self


@dataclass(frozen=True, slots=True)
class SoftChemExecutionBindings:
    chgnet_plan: MLStagePlan | None = None
    chgnet_worker: SubprocessWorkerClient | None = None
    deeph_request: DeepHInferenceRequest | None = None
    deeph_runner: DeepHFlowRunner | None = None
    dft_preflight: DFTPreflightInput | None = None
    dft_backend: VASPilotBackend | None = None


def _pointer_matches_payload(pointer: ArtifactPointerV1, payload: bytes) -> bool:
    return (
        hashlib.sha256(payload).hexdigest() == pointer.sha256
        and (pointer.size_bytes is None or pointer.size_bytes == len(payload))
    )


def _downstream_operation_key(**payload) -> str:
    return canonical_sha256(
        {
            "schema_version": "softchem-downstream-operation-v1",
            **payload,
        }
    )


def _dft_preflight_hash(preflight: DFTPreflightInput) -> str:
    payload = preflight.model_dump(mode="json")
    # Refresh this clock immediately before submission so approval expiry is
    # evaluated at execution time.  All substantive snapshots remain bound.
    payload.pop("checked_at", None)
    return canonical_sha256(payload)


def _require_smact_prior_pass(
    transformation: TransformationExecutionResult,
) -> None:
    prior_checks = tuple(
        check
        for check in transformation.plan.validation_checks
        if check.check_id == "smact_prior_gate"
    )
    if len(prior_checks) != 1 or prior_checks[0].status is not ValidationStatus.PASS:
        raise ValueError(
            "downstream softchem plan requires exactly one passing SMACT prior gate"
        )


def build_softchem_downstream_plan(
    transformation: TransformationExecutionResult,
    *,
    operator_registry: SoftChemOperatorRegistryV1,
    operator_registry_artifact: ArtifactPointerV1,
    chgnet_plan: MLStagePlan | None = None,
    deeph_intent: DownstreamIntent = DownstreamIntent.SKIP,
    deeph_request: DeepHInferenceRequest | None = None,
    dft_intent: DownstreamIntent = DownstreamIntent.SKIP,
    dft_preflight: DFTPreflightInput | None = None,
    created_at: datetime | None = None,
) -> SoftChemDownstreamPlanV1:
    """Freeze the exact native plans/requests used by the downstream runner."""

    _require_smact_prior_pass(transformation)
    registry_payload = softchem_registry_bytes(operator_registry)
    if (
        not _pointer_matches_payload(operator_registry_artifact, registry_payload)
        or operator_registry_artifact.media_type not in {None, "application/json"}
    ):
        raise ValueError("operator registry Artifact does not match registry bytes")
    operator_registry.resolve(
        transformation.plan.operator_id,
        transformation.plan.operator_version,
    )
    chgnet_hash = canonical_sha256(chgnet_plan) if chgnet_plan is not None else None
    deeph_hash = canonical_sha256(deeph_request) if deeph_request is not None else None
    dft_hash = _dft_preflight_hash(dft_preflight) if dft_preflight is not None else None
    identity = {
        "parent_candidate_id": transformation.plan.parent_candidate_id,
        "transformation_plan_id": transformation.plan.plan_id,
        "transformation_route_sha256": transformation.plan.route_sha256,
        "operator_registry_artifact": operator_registry_artifact,
        "operator_id": transformation.plan.operator_id,
        "operator_version": transformation.plan.operator_version,
        "proposed_structure": transformation.plan.output_structure_artifact,
        "chgnet_plan_sha256": chgnet_hash,
        "deeph_intent": deeph_intent,
        "deeph_request_sha256": deeph_hash,
        "dft_intent": dft_intent,
        "dft_preflight_sha256": dft_hash,
    }
    return SoftChemDownstreamPlanV1(
        operation_key=_downstream_operation_key(**identity),
        **identity,
        created_at=created_at or datetime.now(UTC),
    )


class SoftChemDownstreamRunner:
    """Execute only native capabilities whose immutable gates are all ready."""

    def __init__(self, *, artifact_store: LocalArtifactStore) -> None:
        self.store = artifact_store

    def execute(
        self,
        plan: SoftChemDownstreamPlanV1,
        *,
        transformation: TransformationExecutionResult,
        operator_registry: SoftChemOperatorRegistryV1,
        bindings: SoftChemExecutionBindings,
        checked_at: datetime | None = None,
    ) -> SoftChemDownstreamResultV1:
        now = checked_at or datetime.now(UTC)
        if now.tzinfo is None:
            raise ValueError("downstream gate timestamp must be timezone-aware")
        self._verify_frozen_inputs(plan, transformation, operator_registry, bindings)
        operator = self._publish_operator_result(plan, transformation, operator_registry)
        if operator.status is not StageStatus.SUCCEEDED:
            return self._terminal_without_chgnet(plan, transformation, operator)

        chgnet, chgnet_result, relaxed = self._run_chgnet(
            plan,
            transformation,
            bindings,
            now=now,
        )
        if relaxed is None:
            dependency = "CHGNET_RELAXED_STRUCTURE_UNAVAILABLE"
            return SoftChemDownstreamResultV1(
                operation_key=plan.operation_key,
                operator=operator,
                chgnet=chgnet,
                deeph=self._not_run("deeph", dependency, transformation),
                dft=self._not_run("dft", dependency, transformation),
                transformation_plan=transformation.plan,
                chgnet_result=chgnet_result,
            )

        deeph, deeph_result = self._run_deeph(plan, bindings, relaxed)
        dft, dft_job = self._run_dft(
            plan,
            bindings,
            relaxed,
            checked_at=now,
        )
        return SoftChemDownstreamResultV1(
            operation_key=plan.operation_key,
            operator=operator,
            chgnet=chgnet,
            deeph=deeph,
            dft=dft,
            transformation_plan=transformation.plan,
            chgnet_result=chgnet_result,
            deeph_result=deeph_result,
            dft_job=dft_job,
        )

    def _verify_frozen_inputs(
        self,
        plan: SoftChemDownstreamPlanV1,
        transformation: TransformationExecutionResult,
        registry: SoftChemOperatorRegistryV1,
        bindings: SoftChemExecutionBindings,
    ) -> None:
        _require_smact_prior_pass(transformation)
        native = transformation.plan
        if (
            native.plan_id != plan.transformation_plan_id
            or native.route_sha256 != plan.transformation_route_sha256
            or native.parent_candidate_id != plan.parent_candidate_id
            or native.operator_id != plan.operator_id
            or native.operator_version != plan.operator_version
            or native.output_structure_artifact != plan.proposed_structure
        ):
            raise ValueError("transformation result differs from downstream plan")
        registry_payload = softchem_registry_bytes(registry)
        if (
            not _pointer_matches_payload(plan.operator_registry_artifact, registry_payload)
            or plan.operator_registry_artifact.media_type
            not in {None, "application/json"}
        ):
            raise ValueError("operator registry differs from downstream plan")
        registry.resolve(plan.operator_id, plan.operator_version)
        observed_chgnet = (
            canonical_sha256(bindings.chgnet_plan)
            if bindings.chgnet_plan is not None
            else None
        )
        observed_deeph = (
            canonical_sha256(bindings.deeph_request)
            if bindings.deeph_request is not None
            else None
        )
        observed_dft = (
            _dft_preflight_hash(bindings.dft_preflight)
            if bindings.dft_preflight is not None
            else None
        )
        if (
            observed_chgnet != plan.chgnet_plan_sha256
            or observed_deeph != plan.deeph_request_sha256
            or observed_dft != plan.dft_preflight_sha256
        ):
            raise ValueError("native downstream binding differs from frozen plan")

    def _publish_operator_result(
        self,
        plan: SoftChemDownstreamPlanV1,
        transformation: TransformationExecutionResult,
        registry: SoftChemOperatorRegistryV1,
    ) -> StageOutcomeV1:
        registry_payload = softchem_registry_bytes(registry)
        registry_ref = self.store.write_bytes(
            plan.operator_registry_artifact.uri.removeprefix("artifact://"),
            registry_payload,
            media_type="application/json",
            immutable=True,
        )
        if registry_ref.sha256 != plan.operator_registry_artifact.sha256:
            raise ValueError("persisted operator registry hash changed")
        status = transformation.plan.status
        if status is TransformationStatus.REJECTED:
            return StageOutcomeV1(
                stage_id="operator",
                status=StageStatus.BLOCKED,
                reason_codes=("OPERATOR_RESULT_REJECTED",),
                input_structure=transformation.plan.parent_structure_artifact,
            )
        if status is TransformationStatus.REQUIRES_REVIEW:
            return StageOutcomeV1(
                stage_id="operator",
                status=StageStatus.BLOCKED,
                reason_codes=("CHARGE_OR_OXIDATION_REVIEW_REQUIRED",),
                input_structure=transformation.plan.parent_structure_artifact,
                output_structure=transformation.plan.output_structure_artifact,
            )
        if status is not TransformationStatus.STRUCTURE_VALID:
            return StageOutcomeV1(
                stage_id="operator",
                status=StageStatus.BLOCKED,
                reason_codes=("OPERATOR_RESULT_NOT_EXECUTABLE",),
                input_structure=transformation.plan.parent_structure_artifact,
            )
        if transformation.artifact_bytes is None or plan.proposed_structure is None:
            raise ValueError("valid operator result is missing structure bytes")
        if not _pointer_matches_payload(plan.proposed_structure, transformation.artifact_bytes):
            raise ValueError("operator output bytes differ from frozen Artifact")
        output_ref = self.store.write_bytes(
            plan.proposed_structure.uri.removeprefix("artifact://"),
            transformation.artifact_bytes,
            media_type=STRUCTURE_ARTIFACT_MEDIA_TYPE,
            immutable=True,
        )
        if output_ref.sha256 != plan.proposed_structure.sha256:
            raise ValueError("persisted operator output hash changed")
        return StageOutcomeV1(
            stage_id="operator",
            status=StageStatus.SUCCEEDED,
            reason_codes=("REGISTERED_OPERATOR_STRUCTURE_VALID",),
            input_structure=transformation.plan.parent_structure_artifact,
            output_structure=plan.proposed_structure,
        )

    def _run_chgnet(
        self,
        plan: SoftChemDownstreamPlanV1,
        transformation: TransformationExecutionResult,
        bindings: SoftChemExecutionBindings,
        *,
        now: datetime,
    ) -> tuple[StageOutcomeV1, MLCandidateResult | None, ArtifactPointerV1 | None]:
        native = bindings.chgnet_plan
        worker = bindings.chgnet_worker
        proposed = plan.proposed_structure
        if native is None or worker is None or proposed is None:
            return (
                StageOutcomeV1(
                    stage_id="chgnet",
                    status=StageStatus.BLOCKED,
                    reason_codes=("REAL_CHGNET_BINDING_UNAVAILABLE",),
                    input_structure=proposed,
                ),
                None,
                None,
            )
        issues, candidate_id = self._chgnet_gate(
            native,
            worker,
            transformation,
            now=now,
        )
        if issues or candidate_id is None:
            return (
                StageOutcomeV1(
                    stage_id="chgnet",
                    status=StageStatus.BLOCKED,
                    reason_codes=tuple(issues or ["CHGNET_CANDIDATE_UNRESOLVED"]),
                    input_structure=proposed,
                ),
                None,
                None,
            )
        try:
            request = worker.request_for_candidate(native, candidate_id)
            response = worker.run(request)
            response.validate_against_request(request)
        except Exception:  # noqa: BLE001
            return (
                StageOutcomeV1(
                    stage_id="chgnet",
                    status=StageStatus.FAILED,
                    reason_codes=("CHGNET_WORKER_EXECUTION_FAILED",),
                    input_structure=proposed,
                ),
                None,
                None,
            )
        if (
            response.status is not WorkerResponseStatus.SUCCEEDED
            or response.candidate_result is None
        ):
            return (
                StageOutcomeV1(
                    stage_id="chgnet",
                    status=StageStatus.FAILED,
                    reason_codes=("CHGNET_WORKER_DID_NOT_SUCCEED",),
                    input_structure=proposed,
                ),
                None,
                None,
            )
        result = response.candidate_result
        try:
            relaxed = self._validated_relaxed_structure(result, proposed)
        except Exception:  # noqa: BLE001
            return (
                StageOutcomeV1(
                    stage_id="chgnet",
                    status=StageStatus.FAILED,
                    reason_codes=("CHGNET_RESULT_INTEGRITY_FAILED",),
                    input_structure=proposed,
                ),
                None,
                None,
            )
        if relaxed is None:
            return (
                StageOutcomeV1(
                    stage_id="chgnet",
                    status=StageStatus.COMPLETED,
                    reason_codes=("CHGNET_COMPLETED_WITHOUT_QC_PASSED_RELAXATION",),
                    input_structure=proposed,
                ),
                result,
                None,
            )
        return (
            StageOutcomeV1(
                stage_id="chgnet",
                status=StageStatus.SUCCEEDED,
                reason_codes=("REAL_CHGNET_RELAXATION_QC_PASSED",),
                input_structure=proposed,
                output_structure=relaxed,
                evidence_level="L2_ML_SCREENED",
            ),
            result,
            relaxed,
        )

    def _chgnet_gate(
        self,
        plan: MLStagePlan,
        worker: SubprocessWorkerClient,
        transformation: TransformationExecutionResult,
        *,
        now: datetime,
    ) -> tuple[list[str], str | None]:
        issues: list[str] = []
        proposed = transformation.plan.output_structure_artifact
        if type(worker) is not SubprocessWorkerClient or worker.is_mock:
            issues.append("CHGNET_INDEPENDENT_WORKER_REQUIRED")
        elif worker.artifact_root.resolve() != self.store.root:
            issues.append("CHGNET_ARTIFACT_ROOT_MISMATCH")
        identity = plan.execution_identity
        if (
            identity.is_mock
            or identity.model_id != CHGNET_MODEL_ID
            or identity.checkpoint_sha256 != CHGNET_CHECKPOINT_SHA256
            or identity.package_lock_sha256 != AGENT02_PACKAGE_LOCK_SHA256
            or identity.adapter_version != CHGNET_ADAPTER_VERSION
        ):
            issues.append("CHGNET_REAL_IDENTITY_REQUIRED")
        if not plan.allow_real_inference:
            issues.append("CHGNET_REAL_INFERENCE_NOT_ALLOWED")
        if plan.approval_required:
            issues.append("CHGNET_EXTERNAL_APPROVAL_NOT_BOUND")
        for pointer, code in (
            (plan.registry_artifact, "CHGNET_REGISTRY_ARTIFACT_INVALID"),
            (plan.health_artifact, "CHGNET_HEALTH_ARTIFACT_INVALID"),
            (plan.policy_artifact, "CHGNET_POLICY_ARTIFACT_INVALID"),
        ):
            if not self.store.exists_with_hash(pointer.uri, pointer.sha256):
                issues.append(code)
        model_spec = None
        if "CHGNET_REGISTRY_ARTIFACT_INVALID" not in issues:
            try:
                registry = MLModelRegistry.model_validate(
                    self.store.read_json(plan.registry_artifact.uri)
                )
                model_spec = registry.resolve(plan.model_id)
                model_spec.validate_execution_identity(identity)
            except Exception:  # noqa: BLE001
                issues.append("CHGNET_MODEL_NOT_REGISTERED")
        if "CHGNET_HEALTH_ARTIFACT_INVALID" not in issues:
            try:
                health = ModelHealthSnapshot.model_validate(
                    self.store.read_json(plan.health_artifact.uri)
                )
                if (
                    health.is_mock
                    or health.smoke_test_status is not SmokeTestStatus.PASS
                    or health.execution_identity() != identity
                    or health.expires_at.astimezone(UTC) <= now.astimezone(UTC)
                ):
                    raise ValueError("health not ready")
            except Exception:  # noqa: BLE001
                issues.append("CHGNET_HEALTH_NOT_READY")
        if type(worker) is SubprocessWorkerClient:
            try:
                if sha256_file(worker.package_lock_path) != identity.package_lock_sha256:
                    raise ValueError("lock mismatch")
            except Exception:  # noqa: BLE001
                issues.append("CHGNET_WORKER_LOCK_MISMATCH")

        matches = []
        if proposed is not None:
            matches = [
                item
                for item in plan.planned_candidates
                if item.candidate.source_structure.uri == proposed.uri
                and item.candidate.source_structure.sha256 == proposed.sha256
                and item.candidate.source_structure.structure_id
                == transformation.plan.output_structure_id
            ]
        if len(matches) != 1:
            issues.append("CHGNET_TRANSFORMED_CANDIDATE_NOT_BOUND")
            return list(dict.fromkeys(issues)), None
        planned = matches[0]
        candidate = planned.candidate
        if (
            planned.selection_status is not SelectionStatus.SELECTED
            or planned.applicability.status is not ApplicabilityStatus.APPLICABLE
            or not planned.applicability.eligible_for_real_inference
            or not planned.applicability.eligible_for_l2
        ):
            issues.append("CHGNET_APPLICABILITY_GATE_NOT_PASSED")
        actual_elements = tuple(
            sorted(
                element.symbol
                for element in (
                    transformation.output_structure.composition.elements
                    if transformation.output_structure is not None
                    else ()
                )
            )
        )
        if tuple(sorted(candidate.elements)) != actual_elements:
            issues.append("CHGNET_CANDIDATE_COMPOSITION_MISMATCH")
        if model_spec is not None and not set(actual_elements).issubset(
            model_spec.supported_elements
        ):
            issues.append("CHGNET_ELEMENT_DOMAIN_NOT_REVIEWED")
        actual_site_count = (
            len(transformation.output_structure)
            if transformation.output_structure is not None
            else 0
        )
        if candidate.num_sites != actual_site_count:
            issues.append("CHGNET_CANDIDATE_SITE_COUNT_MISMATCH")
        if model_spec is not None and (
            candidate.source_structure.dimensionality
            not in model_spec.supported_dimensionalities
            or candidate.source_structure.num_sites is None
            or candidate.source_structure.num_sites > model_spec.max_num_sites_policy
            or candidate.source_structure.hash_verified is not True
            or candidate.source_structure.parseable is not True
            or candidate.source_structure.has_finite_values is not True
            or candidate.source_structure.positive_volume is not True
        ):
            issues.append("CHGNET_STRUCTURE_DOMAIN_NOT_REVIEWED")
        if candidate.candidate_id not in plan.inference_candidate_ids:
            issues.append("CHGNET_CANDIDATE_NOT_SELECTED_FOR_INFERENCE")
        return list(dict.fromkeys(issues)), candidate.candidate_id

    def _validated_relaxed_structure(
        self,
        result: MLCandidateResult,
        proposed: ArtifactPointerV1,
    ) -> ArtifactPointerV1 | None:
        identity = result.execution_identity
        if (
            identity.is_mock
            or identity.model_id != CHGNET_MODEL_ID
            or identity.checkpoint_sha256 != CHGNET_CHECKPOINT_SHA256
            or identity.package_lock_sha256 != AGENT02_PACKAGE_LOCK_SHA256
            or identity.adapter_version != CHGNET_ADAPTER_VERSION
            or result.source_structure_uri != proposed.uri
            or result.source_structure_sha256 != proposed.sha256
        ):
            raise ValueError("CHGNet result identity or input structure changed")
        relaxation = result.relaxation_result
        if (
            result.decision is not MLDecision.PASS
            or result.evidence_level is not EvidenceLevel.L2_ML_SCREENED
            or relaxation is None
            or relaxation.is_mock
            or relaxation.status is not RelaxationStatus.CONVERGED
            or not relaxation.qc_passed
            or relaxation.output_structure_id is None
            or relaxation.output_structure_uri is None
            or relaxation.output_structure_sha256 is None
            or result.recommended_downstream_structure_id
            != relaxation.output_structure_id
        ):
            return None
        if not self.store.exists_with_hash(
            relaxation.output_structure_uri,
            relaxation.output_structure_sha256,
        ):
            raise ValueError("CHGNet relaxed structure failed Artifact integrity")
        inspected = self.store.inspect(
            relaxation.output_structure_uri,
            media_type=STRUCTURE_ARTIFACT_MEDIA_TYPE,
        )
        return ArtifactPointerV1(
            uri=inspected.uri,
            sha256=inspected.sha256,
            size_bytes=inspected.size_bytes,
            media_type=inspected.media_type,
        )

    def _run_deeph(
        self,
        plan: SoftChemDownstreamPlanV1,
        bindings: SoftChemExecutionBindings,
        relaxed: ArtifactPointerV1,
    ) -> tuple[StageOutcomeV1, DeepHResult | None]:
        if plan.deeph_intent is DownstreamIntent.SKIP:
            return self._not_run("deeph", "DEEPH_NOT_REQUESTED", relaxed), None
        request = bindings.deeph_request
        runner = bindings.deeph_runner
        issues: list[str] = []
        if request is None or runner is None:
            issues.append("REAL_DEEPH_BINDING_UNAVAILABLE")
        else:
            if request.is_mock:
                issues.append("REAL_DEEPH_REQUEST_REQUIRED")
            if (
                request.input_structure.artifact_uri != relaxed.uri
                or request.input_structure.sha256 != relaxed.sha256
                or request.overlap_structure_sha256 != relaxed.sha256
            ):
                issues.append("DEEPH_RELAXED_STRUCTURE_OR_OVERLAP_MISMATCH")
            if request.model_compatibility != request.overlap_compatibility:
                issues.append("DEEPH_DFT_IDENTITY_MISMATCH")
            if not request.trained_model.files:
                issues.append("DEEPH_TRAINED_MODEL_REQUIRED")
            if not request.overlap.files:
                issues.append("DEEPH_DFT_OVERLAP_REQUIRED")
            if (
                type(runner.client) is not DeepHSubprocessClient
                or runner.store.root != self.store.root
            ):
                issues.append("DEEPH_INDEPENDENT_WORKER_REQUIRED")
            else:
                try:
                    build_deeph_plan(request, artifact_root=self.store.root)
                except Exception:  # noqa: BLE001
                    issues.append("DEEPH_INPUT_ARTIFACTS_NOT_VERIFIED")
        if issues:
            return (
                StageOutcomeV1(
                    stage_id="deeph",
                    status=StageStatus.BLOCKED,
                    reason_codes=tuple(dict.fromkeys(issues)),
                    input_structure=relaxed,
                ),
                None,
            )
        try:
            assert request is not None and runner is not None
            result = runner.execute(request)
        except Exception:  # noqa: BLE001
            return (
                StageOutcomeV1(
                    stage_id="deeph",
                    status=StageStatus.FAILED,
                    reason_codes=("DEEPH_EXECUTION_FAILED",),
                    input_structure=relaxed,
                ),
                None,
            )
        if (
            result.is_mock
            or result.status is not DeepHWorkerStatus.SUCCEEDED
            or result.benchmark_status != "NOT_RUN"
            or result.evidence_level != "NONE"
            or result.scientific_conclusion
        ):
            return (
                StageOutcomeV1(
                    stage_id="deeph",
                    status=StageStatus.FAILED,
                    reason_codes=("DEEPH_RESULT_EVIDENCE_CEILING_VIOLATION",),
                    input_structure=relaxed,
                ),
                None,
            )
        return (
            StageOutcomeV1(
                stage_id="deeph",
                status=StageStatus.SUCCEEDED,
                reason_codes=("REAL_DEEPH_INFERENCE_COMPLETED_NO_SCIENTIFIC_CLAIM",),
                input_structure=relaxed,
            ),
            result,
        )

    def _run_dft(
        self,
        plan: SoftChemDownstreamPlanV1,
        bindings: SoftChemExecutionBindings,
        relaxed: ArtifactPointerV1,
        *,
        checked_at: datetime | None = None,
    ) -> tuple[StageOutcomeV1, ExternalJobRef | None]:
        if plan.dft_intent is DownstreamIntent.SKIP:
            return self._not_run("dft", "DFT_NOT_REQUESTED", relaxed), None
        preflight_input = bindings.dft_preflight
        backend = bindings.dft_backend
        issues: list[str] = []
        if preflight_input is None or backend is None:
            issues.append("REAL_DFT_BINDING_UNAVAILABLE")
        else:
            request = preflight_input.request
            if type(backend) is not VASPilotBackend or backend.descriptor.is_mock:
                issues.append("REAL_DFT_BACKEND_REQUIRED")
            if backend.descriptor != preflight_input.backend_descriptor:
                issues.append("DFT_BACKEND_DESCRIPTOR_MISMATCH")
            if len(request.candidates) != 1 or any(
                candidate.structure_artifact.uri != relaxed.uri
                or candidate.structure_artifact.sha256 != relaxed.sha256
                for candidate in request.candidates
            ):
                issues.append("DFT_RELAXED_CANDIDATE_NOT_BOUND")
            if any(
                task.input_structure.uri != relaxed.uri
                or task.input_structure.sha256 != relaxed.sha256
                for task in request.task_specs
            ):
                issues.append("DFT_TASK_STRUCTURE_NOT_BOUND")
            try:
                current_check = preflight_input.model_copy(
                    update={"checked_at": checked_at or datetime.now(UTC)}
                )
                preflight = evaluate_real_preflight(current_check)
                if preflight.status is not PreflightStatus.READY:
                    issues.extend(issue.code for issue in preflight.issues)
            except Exception:  # noqa: BLE001
                issues.append("DFT_PREFLIGHT_INVALID")
            if not issues:
                try:
                    live_descriptor = backend.capability()
                    live_health = backend.health()
                    if (
                        live_descriptor != preflight_input.backend_descriptor
                        or live_health.descriptor != live_descriptor
                        or live_health.status != "READY"
                    ):
                        raise ValueError("backend is not currently ready")
                except Exception:  # noqa: BLE001
                    issues.append("DFT_LIVE_BACKEND_HEALTH_NOT_READY")
        if issues:
            return (
                StageOutcomeV1(
                    stage_id="dft",
                    status=StageStatus.BLOCKED,
                    reason_codes=tuple(dict.fromkeys(issues)),
                    input_structure=relaxed,
                ),
                None,
            )
        assert preflight_input is not None and backend is not None
        request = preflight_input.request
        idempotency_key = hashlib.sha256(
            f"{plan.operation_key}:{dft_canonical_hash(request)}".encode()
        ).hexdigest()
        try:
            if not backend.validate_input(request):
                raise ValueError("backend rejected input")
            job = backend.submit(request, idempotency_key)
        except Exception:  # noqa: BLE001
            return (
                StageOutcomeV1(
                    stage_id="dft",
                    status=StageStatus.FAILED,
                    reason_codes=("DFT_SUBMISSION_FAILED",),
                    input_structure=relaxed,
                ),
                None,
            )
        if job.is_mock or job.backend_id != request.backend_id:
            return (
                StageOutcomeV1(
                    stage_id="dft",
                    status=StageStatus.FAILED,
                    reason_codes=("DFT_JOB_IDENTITY_MISMATCH",),
                    input_structure=relaxed,
                ),
                None,
            )
        return (
            StageOutcomeV1(
                stage_id="dft",
                status=StageStatus.SUBMITTED,
                reason_codes=("REAL_DFT_JOB_SUBMITTED_VALIDATION_PENDING",),
                input_structure=relaxed,
            ),
            job,
        )

    def _terminal_without_chgnet(
        self,
        plan: SoftChemDownstreamPlanV1,
        transformation: TransformationExecutionResult,
        operator: StageOutcomeV1,
    ) -> SoftChemDownstreamResultV1:
        reason = "OPERATOR_STRUCTURE_NOT_EXECUTABLE"
        return SoftChemDownstreamResultV1(
            operation_key=plan.operation_key,
            operator=operator,
            chgnet=self._not_run("chgnet", reason, transformation.plan.parent_structure_artifact),
            deeph=self._not_run("deeph", reason, transformation.plan.parent_structure_artifact),
            dft=self._not_run("dft", reason, transformation.plan.parent_structure_artifact),
            transformation_plan=transformation.plan,
        )

    @staticmethod
    def _not_run(
        stage: Literal["chgnet", "deeph", "dft"],
        reason: str,
        structure: TransformationExecutionResult | ArtifactPointerV1,
    ) -> StageOutcomeV1:
        pointer = (
            structure.plan.output_structure_artifact
            if isinstance(structure, TransformationExecutionResult)
            else structure
        )
        return StageOutcomeV1(
            stage_id=stage,
            status=StageStatus.NOT_RUN,
            reason_codes=(reason,),
            input_structure=pointer,
        )


def reconcile_submitted_dft(
    *,
    backend: VASPilotBackend,
    job: ExternalJobRef,
) -> DFTResultEnvelope | None:
    """Fetch a real terminal envelope without interpreting it as L3 evidence."""

    if type(backend) is not VASPilotBackend or backend.descriptor.is_mock or job.is_mock:
        raise ValueError("real VASPilot backend and job are required")
    status = backend.status(job)
    if status in {JobStatus.QUEUED, JobStatus.RUNNING, JobStatus.CANCEL_REQUESTED}:
        return None
    result = backend.fetch_result(job)
    if result.is_mock or result.external_job_ref != job:
        raise ValueError("DFT result identity changed")
    return result
