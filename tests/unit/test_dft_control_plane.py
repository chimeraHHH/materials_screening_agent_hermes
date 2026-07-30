from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from material_agent.dft.bridge_models import BridgeBackendDescriptor, BridgeHealth
from material_agent.dft.claim_gate import (
    ClaimReadinessStatus,
    evaluate_claim_readiness,
)
from material_agent.dft.models import (
    ArtifactRef,
    ClaimRequest,
    DFTRequest,
    DFTTaskSpec,
    ExecutionMode,
    ResourceEstimate,
    TaskType,
    canonical_hash,
)
from material_agent.dft.policies import PolicyKind, PolicySnapshot, PolicyStatus
from material_agent.dft.preflight import (
    ApprovalSnapshot,
    DFTPreflightInput,
    PreflightStatus,
    evaluate_real_preflight,
)
from material_agent.dft.validation import TaskValidationResult, ValidationStatus
from material_agent.dft.workflows import (
    ClaimDependencyTemplate,
    WorkflowTaskTemplate,
    WorkflowTemplate,
    WorkflowTemplateRegistry,
)


NOW = datetime(2026, 7, 30, tzinfo=UTC)


def _ref(name: str, letter: str) -> ArtifactRef:
    return ArtifactRef(uri=f"artifact://{name}", sha256=letter * 64)


def _workflow() -> WorkflowTemplate:
    return WorkflowTemplate(
        template_id="single-scf-v1", version="1.0.0", target_class="test-only",
        tasks=(WorkflowTaskTemplate(
            task_key="scf", task_type=TaskType.STATIC_SCF,
            required_output_names=("result.json",),
            validator_ids=("electronic-convergence@1",),
        ),),
        claim_dependencies=(ClaimDependencyTemplate(
            claim_type="band_gap_computed", supporting_task_keys=("scf",),
            domain_validator_id="band-gap@1",
        ),),
    )


def _policies() -> tuple[PolicySnapshot, ...]:
    return (
        PolicySnapshot(
            kind=PolicyKind.METHOD, policy_id="method", version="1.0.0",
            status=PolicyStatus.APPROVED, artifact=_ref("policies/method.json", "a"),
            effective_from=NOW, approved_by="expert", approved_at=NOW,
        ),
        PolicySnapshot(
            kind=PolicyKind.RESOURCE, policy_id="resource", version="1.0.0",
            status=PolicyStatus.APPROVED, artifact=_ref("policies/resource.json", "b"),
            effective_from=NOW, approved_by="expert", approved_at=NOW,
        ),
        PolicySnapshot(
            kind=PolicyKind.EVIDENCE, policy_id="evidence", version="1.0.0",
            status=PolicyStatus.APPROVED, artifact=_ref("policies/evidence.json", "c"),
            effective_from=NOW, approved_by="expert", approved_at=NOW,
        ),
    )


def _request() -> DFTRequest:
    method, resource, _evidence = _policies()
    task = DFTTaskSpec(
        task_id="scf", task_type=TaskType.STATIC_SCF, candidate_id="cand_1",
        input_structure=_ref("structures/input.cif", "d"),
        method_spec=method.artifact, method_hash=method.artifact.sha256,
        resource_spec=ResourceEstimate(
            estimate_mode="POLICY_RULE_BASED", resource_class="SMALL", is_mock=False
        ), expected_outputs=("result.json",),
        validator_ids=("electronic-convergence@1",), task_input_hash="e" * 64,
        is_mock=False,
    )
    return DFTRequest(
        request_id="dftreq_1", project_id="project", run_id="run", stage_run_id="stage",
        execution_mode=ExecutionMode.REAL, backend_id="vaspilot",
        candidates=({
            "candidate_id": "cand_1", "structure_id": "structure_1",
            "structure_artifact": _ref("structures/input.cif", "d"),
            "source_stage": "agent01",
        },),
        requested_claims=(ClaimRequest(
            claim_type="band_gap_computed", required_evidence_level="L3_DFT_VALIDATED"
        ),),
        workflow_template_id="single-scf-v1", workflow_plan_hash="f" * 64,
        task_specs=(task,), resource_estimate=task.resource_spec,
        method_policy_ref=method.artifact, resource_policy_ref=resource.artifact,
        approval_id="approval_1", upstream_snapshot_hash="0" * 64, is_mock=False,
    )


def _preflight_input(*, health_status: str = "READY") -> DFTPreflightInput:
    request = _request()
    descriptor = BridgeBackendDescriptor(
        backend_id="vaspilot", backend_version="future", adapter_version="v1",
        is_mock=False, supported_task_types=("STATIC_SCF",),
    )
    approval = ApprovalSnapshot(
        approval_id="approval_1", status="APPROVED", request_sha256=canonical_hash(request),
        workflow_plan_hash=request.workflow_plan_hash, approved_by="expert", approved_at=NOW,
        expires_at=NOW + timedelta(hours=1),
    )
    return DFTPreflightInput(
        request=request, workflow_template=_workflow(), policy_snapshots=_policies(),
        approval=approval, backend_descriptor=descriptor,
        backend_health=BridgeHealth(descriptor=descriptor, status=health_status),
        checked_at=NOW,
    )


def test_real_preflight_is_ready_only_for_frozen_approved_snapshots() -> None:
    result = evaluate_real_preflight(_preflight_input())

    assert result.status is PreflightStatus.READY
    assert result.issues == ()


def test_real_preflight_blocks_unhealthy_backend_without_executing_it() -> None:
    result = evaluate_real_preflight(_preflight_input(health_status="DEGRADED"))

    assert result.status is PreflightStatus.BLOCKED
    assert {issue.code for issue in result.issues} == {"BACKEND_NOT_READY"}


def test_workflow_registry_validates_dag_and_claim_dependencies() -> None:
    registry = WorkflowTemplateRegistry(templates=(_workflow(),))
    assert registry.resolve("single-scf-v1").topological_task_keys() == ("scf",)
    with pytest.raises(ValueError, match="dependency cycle"):
        WorkflowTemplate(
            template_id="cycle", version="1", target_class="test",
            tasks=(
                WorkflowTaskTemplate(task_key="a", task_type=TaskType.RELAXATION, depends_on=("b",)),
                WorkflowTaskTemplate(task_key="b", task_type=TaskType.STATIC_SCF, depends_on=("a",)),
            ),
        )


def test_claim_gate_requires_valid_supporting_tasks_before_domain_validation() -> None:
    claim = ClaimRequest(
        claim_type="band_gap_computed", required_evidence_level="L3_DFT_VALIDATED"
    )
    invalid = TaskValidationResult(
        task_id="scf", status=ValidationStatus.INVALID,
        policy_ref=_ref("policies/evidence.json", "c"), issues=(),
    )
    blocked = evaluate_claim_readiness(
        claim=claim, dependency=_workflow().claim_dependency(claim.claim_type),
        task_results={"scf": invalid}, is_mock=False,
    )
    assert blocked.status is ClaimReadinessStatus.BLOCKED
    assert blocked.issues[0].code == "SUPPORTING_TASK_NOT_VALID"

    valid = invalid.model_copy(update={"status": ValidationStatus.VALID})
    ready = evaluate_claim_readiness(
        claim=claim, dependency=_workflow().claim_dependency(claim.claim_type),
        task_results={"scf": valid}, is_mock=False,
    )
    assert ready.status is ClaimReadinessStatus.READY_FOR_DOMAIN_VALIDATOR
    assert ready.domain_validator_id == "band-gap@1"
