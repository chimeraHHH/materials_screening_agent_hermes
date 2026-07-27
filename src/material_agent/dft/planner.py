"""Pure validation and deterministic mock workflow planning for Agent03."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .models import (
    ArtifactRef, CandidateDFTInput, ClaimRequest, DFTRequest, DFTTaskSpec,
    ExecutionMode, ResourceEstimate, TaskType, canonical_hash,
)

SUPPORTED_MOCK_CLAIMS = frozenset({"workflow_lifecycle"})


@dataclass(frozen=True)
class ValidationIssue:
    path: str
    code: str
    message: str
    how_to_resolve: str


@dataclass(frozen=True)
class StageInputValidation:
    valid: bool
    status: str
    issues: tuple[ValidationIssue, ...] = ()


class StageInputValidator:
    """Validate the minimum context without reading artifacts or calling services."""

    def validate(self, payload: dict[str, Any]) -> StageInputValidation:
        required = (
            "project_id", "run_id", "stage_run_id", "candidates", "requested_claims",
            "workflow_template_id", "backend_id", "execution_mode", "upstream_snapshot_hash",
        )
        issues: list[ValidationIssue] = []
        for name in required:
            if payload.get(name) in (None, "", [], ()):
                issues.append(ValidationIssue(name, "MISSING_INPUT", f"missing required input: {name}", f"provide {name}"))
        candidates = payload.get("candidates") or []
        if candidates and len({c.get("candidate_id") for c in candidates}) != len(candidates):
            issues.append(ValidationIssue("candidates", "DUPLICATE_CANDIDATE_ID", "candidate IDs must be unique", "submit one record per candidate"))
        for index, candidate in enumerate(candidates):
            for name in ("candidate_id", "structure_id", "structure_artifact_uri", "structure_artifact_sha256"):
                if not candidate.get(name):
                    issues.append(ValidationIssue(f"candidates[{index}].{name}", "MISSING_INPUT", f"missing {name}", f"provide {name} from the immutable Artifact Store"))
        if payload.get("execution_mode") == "MOCK" and payload.get("backend_id") not in (None, "mock-dft"):
            issues.append(ValidationIssue("backend_id", "MOCK_BACKEND_MISMATCH", "MOCK requests require mock-dft", "select mock-dft"))
        if payload.get("execution_mode") == "REAL" and payload.get("backend_id") == "mock-dft":
            issues.append(ValidationIssue("backend_id", "REAL_BACKEND_MISMATCH", "REAL requests cannot use mock-dft", "select an approved real backend"))
        return StageInputValidation(not issues, "READY" if not issues else "BLOCKED_MISSING_INPUT", tuple(issues))


@dataclass(frozen=True)
class BackendCapability:
    backend_id: str
    version: str
    supported_task_types: frozenset[TaskType]
    supports_mock: bool = False


@dataclass(frozen=True)
class DFTWorkflowPlan:
    request_id: str
    workflow_id: str
    candidate_id: str
    template_id: str
    tasks: tuple[DFTTaskSpec, ...]
    requested_claims: tuple[ClaimRequest, ...]
    resource_estimate: ResourceEstimate
    plan_hash: str
    requires_approval: bool
    blockers: tuple[str, ...] = ()


def default_mock_capability() -> BackendCapability:
    return BackendCapability("mock-dft", "1.0.0", frozenset({TaskType.MOCK_TASK}), True)


def build_approval_payload(plan: DFTWorkflowPlan, *, project_id: str, run_id: str) -> dict[str, Any]:
    return {
        "approval_id": f"approval_{plan.plan_hash[:24]}",
        "gate_type": "EXPENSIVE_BATCH_APPROVAL",
        "project_id": project_id,
        "run_id": run_id,
        "workflow_id": plan.workflow_id,
        "plan_hash": plan.plan_hash,
        "requested_claims": [claim.model_dump(mode="json") for claim in plan.requested_claims],
        "resource_estimate": plan.resource_estimate.model_dump(mode="json"),
        "message": "计划运行一个 mock 生命周期；不会运行 VASP，也不会产生科研数值。",
        "is_mock": True,
    }


class DFTPlanner:
    def __init__(self, capability: BackendCapability | None = None) -> None:
        self.capability = capability or default_mock_capability()

    def plan(self, payload: dict[str, Any]) -> DFTWorkflowPlan:
        validation = StageInputValidator().validate(payload)
        if not validation.valid:
            raise ValueError("BLOCKED_MISSING_INPUT: " + "; ".join(issue.path for issue in validation.issues))
        if payload["execution_mode"] == "REAL":
            if not payload.get("method_policy_ref") or not payload.get("resource_policy_ref"):
                raise ValueError("BLOCKED_MISSING_SCIENTIFIC_POLICY: frozen method and resource policies are required")
            raise ValueError("NOT_SUPPORTED: real DFT planning is not implemented in v1")
        if payload["execution_mode"] != "MOCK" or payload["backend_id"] != "mock-dft":
            raise ValueError("PERMANENT_CONFIGURATION: v1 mock planner requires mock-dft")
        claims = tuple(ClaimRequest.model_validate(claim) for claim in payload["requested_claims"])
        unsupported = tuple(claim.claim_type for claim in claims if claim.claim_type not in SUPPORTED_MOCK_CLAIMS)
        candidate = payload["candidates"][0]
        structure = ArtifactRef(uri=candidate["structure_artifact_uri"], sha256=candidate["structure_artifact_sha256"])
        input_hash = canonical_hash({"structure": structure.model_dump(mode="json"), "task_type": "MOCK_TASK", "template": payload["workflow_template_id"]})
        task = DFTTaskSpec(
            task_id=f"dfttask_{input_hash[:24]}", task_type=TaskType.MOCK_TASK,
            candidate_id=candidate["candidate_id"], input_structure=structure,
            resource_spec=ResourceEstimate(resource_class="TRIVIAL"), task_input_hash=input_hash, is_mock=True,
        )
        identity = {
            "candidate": candidate,
            "claims": [c.model_dump(mode="json") for c in claims],
            "template": payload["workflow_template_id"],
            "task": task.model_dump(mode="json"),
            "backend": {
                "backend_id": self.capability.backend_id,
                "version": self.capability.version,
                "supported_task_types": sorted(item.value for item in self.capability.supported_task_types),
                "supports_mock": self.capability.supports_mock,
            },
        }
        plan_hash = canonical_hash(identity)
        return DFTWorkflowPlan(
            request_id=payload.get("request_id", f"dftreq_{plan_hash[:24]}"), workflow_id=f"dftwf_{plan_hash[:24]}",
            candidate_id=candidate["candidate_id"], template_id=payload["workflow_template_id"], tasks=(task,),
            requested_claims=claims, resource_estimate=task.resource_spec, plan_hash=plan_hash,
            requires_approval=True, blockers=tuple(f"NOT_SUPPORTED:{claim}" for claim in unsupported),
        )
