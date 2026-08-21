"""Deterministic Agent02 contracts and planning primitives.

This package intentionally has no imports of torch, chgnet, or ase.  The
CHGNet and DeepH execution paths remain behind independent subprocess
boundaries.
"""

from material_agent.ml_screening.adapters import (
    FakeMLModelAdapter,
    FakeMLWorker,
    FakeWorkerCoordinator,
    MLModelAdapter,
)
from material_agent.ml_screening.alignn_models import (
    AlignnExecutionPlan,
    AlignnInferenceRequest,
    AlignnProperty,
    AlignnResult,
)
from material_agent.ml_screening.alignn_planner import build_alignn_plan
from material_agent.ml_screening.deeph_models import (
    DeepHExecutionPlan,
    DeepHInferenceRequest,
    DeepHResult,
)
from material_agent.ml_screening.deeph_planner import build_deeph_plan
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
from material_agent.ml_screening.property_catalog import (
    reviewed_property_model_families,
)
from material_agent.ml_screening.property_execution import (
    PropertyExecutionPlan,
    PropertyPredictionResult,
    PropertyWorkerRequest,
    PropertyWorkerResponse,
    build_property_execution_plan,
)
from material_agent.ml_screening.property_models import (
    PropertyCapability,
    PropertyInputKind,
    PropertyModelAvailability,
    PropertyModelFamily,
    PropertyModelRegistry,
    PropertyModelSelection,
    PropertyModelSpec,
    PropertyNeed,
    PropertyPredictionRequest,
    select_property_model,
)

__all__ = [
    "AGENT02_CONTRACT_VERSION",
    "AGENT02_MODEL_HEALTH_VERSION",
    "AGENT02_REQUEST_VERSION",
    "AGENT02_STAGE_PLAN_VERSION",
    "AGENT02_WORKER_PROTOCOL_VERSION",
    "AlignnExecutionPlan",
    "AlignnInferenceRequest",
    "AlignnProperty",
    "AlignnResult",
    "ApplicabilityAssessment",
    "ApplicabilityStatus",
    "DeepHExecutionPlan",
    "DeepHInferenceRequest",
    "DeepHResult",
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
    "PropertyCapability",
    "PropertyExecutionPlan",
    "PropertyInputKind",
    "PropertyModelAvailability",
    "PropertyModelFamily",
    "PropertyModelRegistry",
    "PropertyModelSelection",
    "PropertyModelSpec",
    "PropertyNeed",
    "PropertyPredictionRequest",
    "PropertyPredictionResult",
    "PropertyWorkerRequest",
    "PropertyWorkerResponse",
    "SelectionMode",
    "SelectionStatus",
    "build_alignn_plan",
    "build_deeph_plan",
    "build_ml_stage_plan",
    "build_property_execution_plan",
    "reviewed_property_model_families",
    "select_property_model",
]
