"""Deterministic Agent02 contracts and planning primitives.

This package intentionally has no imports of torch, chgnet, or ase.  The
production ML worker is a later implementation step and lives behind the JSON
worker protocol defined here.
"""

from material_agent.ml_screening.adapters import (
    FakeMLModelAdapter,
    FakeMLWorker,
    FakeWorkerCoordinator,
    MLModelAdapter,
)
from material_agent.ml_screening.models import (
    AGENT02_CONTRACT_VERSION,
    AGENT02_MODEL_HEALTH_VERSION,
    AGENT02_REQUEST_VERSION,
    AGENT02_STAGE_PLAN_VERSION,
    AGENT02_WORKER_PROTOCOL_VERSION,
    ApplicabilityAssessment,
    ApplicabilityStatus,
    EvidenceLevel,
    MLCandidateInput,
    MLCandidateManifestRecord,
    MLCandidateResult,
    MLDecision,
    MLExecutionIdentity,
    MLModelRegistry,
    MLModelSpec,
    MLNumericArtifactRef,
    MLScreeningPolicy,
    MLScreeningRequest,
    MLStagePlan,
    MLStageResultEnvelope,
    ModelHealthSnapshot,
    PreFilterEvaluation,
    SelectionMode,
    SelectionStatus,
)
from material_agent.ml_screening.planner import build_ml_stage_plan

__all__ = [
    "AGENT02_CONTRACT_VERSION",
    "AGENT02_MODEL_HEALTH_VERSION",
    "AGENT02_REQUEST_VERSION",
    "AGENT02_STAGE_PLAN_VERSION",
    "AGENT02_WORKER_PROTOCOL_VERSION",
    "ApplicabilityAssessment",
    "ApplicabilityStatus",
    "EvidenceLevel",
    "FakeMLModelAdapter",
    "FakeMLWorker",
    "FakeWorkerCoordinator",
    "MLCandidateInput",
    "MLCandidateManifestRecord",
    "MLCandidateResult",
    "MLDecision",
    "MLExecutionIdentity",
    "MLModelAdapter",
    "MLModelRegistry",
    "MLModelSpec",
    "MLNumericArtifactRef",
    "MLScreeningPolicy",
    "MLScreeningRequest",
    "MLStagePlan",
    "MLStageResultEnvelope",
    "ModelHealthSnapshot",
    "PreFilterEvaluation",
    "SelectionMode",
    "SelectionStatus",
    "build_ml_stage_plan",
]
