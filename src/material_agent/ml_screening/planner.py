"""Pure Agent02 native-stage planner."""

from __future__ import annotations

from datetime import datetime, timedelta

from material_agent.ml_screening.applicability import assess_applicability
from material_agent.ml_screening.models import (
    ArtifactPointer,
    MLCandidateInput,
    MLModelRegistry,
    MLRequirementView,
    MLScreeningPolicy,
    MLScreeningRequest,
    MLStagePlan,
    ModelHealthSnapshot,
    ResourceEstimate,
    SelectionMode,
    SelectionStatus,
)
from material_agent.ml_screening.prefilter import evaluate_pre_filter
from material_agent.ml_screening.resources import (
    candidate_operation_key,
    environment_fingerprint_sha256,
    sha256_payload,
)
from material_agent.ml_screening.selection import select_candidates


def build_ml_stage_plan(
    *,
    project_id: str,
    run_id: str,
    requirement_revision: int,
    attempt: int,
    orchestrator_input_snapshot: ArtifactPointer,
    requirement_artifact: ArtifactPointer,
    candidate_manifest_artifact: ArtifactPointer,
    stage_request_artifact: ArtifactPointer | None,
    policy_artifact: ArtifactPointer,
    registry_artifact: ArtifactPointer,
    health_artifact: ArtifactPointer,
    requirement: MLRequirementView,
    candidates: list[MLCandidateInput],
    request: MLScreeningRequest,
    policy: MLScreeningPolicy,
    registry: MLModelRegistry,
    health: ModelHealthSnapshot,
    created_at: datetime,
) -> MLStagePlan:
    """Build an immutable plan without loading or invoking any ML model."""

    if not requirement.confirmed_by_user:
        raise ValueError("Agent02 requires a confirmed Requirement revision")
    if not requirement.allow_ml:
        raise ValueError("Requirement budget does not allow ML")
    if requirement.revision != requirement_revision:
        raise ValueError("Requirement revision does not match stage context")
    if request.max_candidates > policy.hard_max_candidates:
        raise ValueError("requested batch exceeds the policy hard limit")
    if created_at.tzinfo is None:
        raise ValueError("plan created_at must be timezone-aware")
    if policy_artifact.sha256 != sha256_payload(policy):
        raise ValueError("policy artifact hash does not match policy")
    if registry_artifact.sha256 != sha256_payload(registry):
        raise ValueError("registry artifact hash does not match registry")
    if health_artifact.sha256 != sha256_payload(health):
        raise ValueError("health artifact hash does not match snapshot")

    candidate_ids = [candidate.candidate_id for candidate in candidates]
    if len(candidate_ids) != len(set(candidate_ids)):
        raise ValueError("candidate manifest contains duplicate IDs")
    if request.requested_candidate_ids:
        unknown = sorted(
            set(request.requested_candidate_ids) - set(candidate_ids)
        )
        if unknown:
            raise ValueError(
                f"requested candidate IDs are not in manifest: {unknown}"
            )

    model = registry.resolve(request.model_ref)
    if health.model_id != model.model_id:
        raise ValueError("health snapshot model differs from registry model")
    if health.checkpoint_sha256 != model.checkpoint_sha256:
        raise ValueError("health snapshot checkpoint hash differs from registry")
    if health.package_lock_sha256 != model.package_lock_sha256:
        raise ValueError("health snapshot package lock differs from registry")
    if health.is_mock != model.is_mock:
        raise ValueError("registry and health mock identities differ")
    if health.adapter_version != model.adapter_version:
        raise ValueError("health adapter version differs from registry")
    installed_model_version = health.installed_package_versions.get(
        model.package_name
    )
    if installed_model_version != model.package_version:
        raise ValueError(
            "health installed model package version differs from registry"
        )
    expected_fingerprint = environment_fingerprint_sha256(
        python_version=health.python_version,
        installed_package_versions=health.installed_package_versions,
        platform=health.platform,
        architecture=health.architecture,
        device_policy=health.device_policy,
        available_devices=health.available_devices,
        package_lock_sha256=health.package_lock_sha256,
    )
    if health.environment_fingerprint_sha256 != expected_fingerprint:
        raise ValueError("health environment fingerprint is invalid")
    expected_expiry = health.tested_at + timedelta(
        seconds=policy.health_snapshot_validity_seconds
    )
    if health.expires_at != expected_expiry:
        raise ValueError(
            "health expiry does not match the policy validity window"
        )
    if not health.tested_at <= created_at < health.expires_at:
        raise ValueError("health snapshot is not valid at plan creation time")

    execution_identity = health.execution_identity()
    model.validate_execution_identity(execution_identity)

    pre_filters = {
        candidate.candidate_id: evaluate_pre_filter(
            candidate,
            requirement,
            policy,
        )
        for candidate in candidates
    }
    applicability = {
        candidate.candidate_id: assess_applicability(
            candidate,
            model,
            health,
            policy,
        )
        for candidate in candidates
    }
    planned = select_candidates(
        candidates,
        pre_filters,
        applicability,
        request,
        policy,
    )
    inference_ids = [
        item.candidate.candidate_id
        for item in planned
        if item.selection_status is SelectionStatus.SELECTED
    ]
    not_selected_ids = [
        item.candidate.candidate_id
        for item in planned
        if item.selection_status is not SelectionStatus.SELECTED
    ]
    selected_sites = [
        (
            item.candidate.num_sites
            or item.candidate.source_structure.num_sites
            or 0
        )
        for item in planned
        if item.selection_status is SelectionStatus.SELECTED
    ]
    requested_count = (
        len(request.requested_candidate_ids or [])
        if request.selection_mode is SelectionMode.EXPLICIT_IDS
        else min(len(candidates), request.max_candidates)
    )
    approval_required = (
        request.selection_mode is SelectionMode.EXPLICIT_IDS
        and len(request.requested_candidate_ids or []) > 5
    )
    operation_keys = {
        item.candidate.candidate_id: candidate_operation_key(
            project_id=project_id,
            run_id=run_id,
            candidate_id=item.candidate.candidate_id,
            input_structure_sha256=item.candidate.source_structure.sha256,
            model_id=model.model_id,
            checkpoint_sha256=model.checkpoint_sha256,
            package_lock_sha256=model.package_lock_sha256,
            environment_fingerprint_sha256_value=(
                health.environment_fingerprint_sha256
            ),
            adapter_version=model.adapter_version,
            worker_protocol_version=health.worker_protocol_version,
            device_policy=health.device_policy,
            policy_sha256=policy_artifact.sha256,
            relaxation_profile=request.relaxation_profile,
            requested_tasks=request.requested_tasks,
        )
        for item in planned
        if item.selection_status is SelectionStatus.SELECTED
    }
    return MLStagePlan(
        project_id=project_id,
        run_id=run_id,
        requirement_revision=requirement_revision,
        attempt=attempt,
        orchestrator_input_snapshot=orchestrator_input_snapshot,
        requirement_artifact=requirement_artifact,
        candidate_manifest_artifact=candidate_manifest_artifact,
        stage_request_artifact=stage_request_artifact,
        policy_artifact=policy_artifact,
        registry_artifact=registry_artifact,
        health_artifact=health_artifact,
        selection_mode=request.selection_mode,
        selection_limit=request.max_candidates,
        manifest_candidate_count=len(candidates),
        requested_candidate_ids=request.requested_candidate_ids,
        requested_candidate_count=requested_count,
        inference_candidate_ids=inference_ids,
        not_selected_candidate_ids=not_selected_ids,
        candidate_operation_keys=operation_keys,
        planned_candidates=planned,
        model_id=model.model_id,
        checkpoint_sha256=model.checkpoint_sha256,
        package_lock_sha256=model.package_lock_sha256,
        execution_identity=execution_identity,
        device_policy=health.device_policy,
        relaxation_profile=request.relaxation_profile,
        requested_tasks=request.requested_tasks,
        allow_real_inference=request.allow_real_inference,
        resource_estimate=ResourceEstimate(
            requested_candidate_count=requested_count,
            inference_candidate_count=len(inference_ids),
            maximum_num_sites=max(selected_sites, default=0),
            maximum_relaxation_steps=policy.relaxation_steps,
            device_policy=health.device_policy,
            estimated_wall_time="not-estimated-in-step1",
            estimated_memory="not-estimated-in-step1",
        ),
        approval_required=approval_required,
        policy_version=policy.policy_version,
        created_at=created_at,
    )
