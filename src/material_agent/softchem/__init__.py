"""Versioned soft-chemistry operator and downstream handoff contracts.

The package is deliberately a light control layer.  Scientific execution
continues to be owned by the existing Inspiration, Agent02, DeepH and Agent03
implementations; this package only permits a handoff when their native gates
agree on the exact, hash-bound structure.
"""

from material_agent.softchem.registry import (
    DEFAULT_SOFTCHEM_OPERATOR_REGISTRY_V1,
    SOFTCHEM_OPERATOR_REGISTRY_SCHEMA_VERSION,
    SoftChemOperatorRegistryV1,
    SoftChemOperatorSpecV1,
    execute_registered_softchem_operator,
    softchem_registry_bytes,
    softchem_registry_sha256,
)
from material_agent.softchem.prior import (
    DEFAULT_SMACT_PRIOR_POLICY_V1,
    SMACT_PRIOR_GATE_SCHEMA_VERSION,
    SMACT_PRIOR_POLICY_SCHEMA_VERSION,
    ElementStoichiometryV1,
    SmactPriorDecision,
    SmactPriorEvaluator,
    SmactPriorGateResultV1,
    SmactPriorPolicyV1,
    SmactPriorWorkerRequestV1,
    evaluate_smact_composition_prior,
    evaluate_smact_substitution_prior,
    smact_prior_policy_sha256,
)
from material_agent.softchem.prior_client import (
    SMACT_WORKER_LOCK_SHA256,
    SMACT_WORKER_PYTHON_ENV,
    SmactPriorProcessError,
    SmactPriorSubprocessClient,
    smact_prior_evaluator_from_environment,
)
from material_agent.softchem.pipeline import (
    SOFTCHEM_DOWNSTREAM_PLAN_VERSION,
    SOFTCHEM_DOWNSTREAM_RESULT_VERSION,
    DownstreamIntent,
    SoftChemDownstreamPlanV1,
    SoftChemDownstreamResultV1,
    SoftChemDownstreamRunner,
    SoftChemExecutionBindings,
    StageOutcomeV1,
    StageStatus,
    build_softchem_downstream_plan,
    reconcile_submitted_dft,
)

__all__ = [
    "DEFAULT_SOFTCHEM_OPERATOR_REGISTRY_V1",
    "DEFAULT_SMACT_PRIOR_POLICY_V1",
    "SMACT_PRIOR_GATE_SCHEMA_VERSION",
    "SMACT_PRIOR_POLICY_SCHEMA_VERSION",
    "SMACT_WORKER_LOCK_SHA256",
    "SMACT_WORKER_PYTHON_ENV",
    "SOFTCHEM_OPERATOR_REGISTRY_SCHEMA_VERSION",
    "SOFTCHEM_DOWNSTREAM_PLAN_VERSION",
    "SOFTCHEM_DOWNSTREAM_RESULT_VERSION",
    "DownstreamIntent",
    "ElementStoichiometryV1",
    "SoftChemDownstreamPlanV1",
    "SoftChemDownstreamResultV1",
    "SoftChemDownstreamRunner",
    "SoftChemExecutionBindings",
    "SoftChemOperatorRegistryV1",
    "SoftChemOperatorSpecV1",
    "SmactPriorDecision",
    "SmactPriorEvaluator",
    "SmactPriorGateResultV1",
    "SmactPriorPolicyV1",
    "SmactPriorProcessError",
    "SmactPriorSubprocessClient",
    "SmactPriorWorkerRequestV1",
    "StageOutcomeV1",
    "StageStatus",
    "build_softchem_downstream_plan",
    "execute_registered_softchem_operator",
    "evaluate_smact_composition_prior",
    "evaluate_smact_substitution_prior",
    "reconcile_submitted_dft",
    "softchem_registry_bytes",
    "softchem_registry_sha256",
    "smact_prior_policy_sha256",
    "smact_prior_evaluator_from_environment",
]
