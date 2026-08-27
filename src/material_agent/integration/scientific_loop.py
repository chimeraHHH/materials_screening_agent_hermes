"""Generic hypothesis-to-evidence contracts and one bounded feedback cycle.

The module deliberately separates scientific choice from deterministic safety:
DeepSeek proposes which calculation should answer which claim.  The local policy
only checks immutable inputs, registered runtime capabilities, applicability,
weights, budget and evidence ceilings.  It never adds a model or observable to
the route on its own.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from enum import StrEnum
from typing import Annotated, Literal, Protocol

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError
from pydantic import Field, JsonValue, model_validator
from pymatgen.core import Composition

from material_agent.inspiration.deepseek_agent import (
    DeepSeekAgentBudgetV1,
    DeepSeekAgentReceiptV1,
    DeepSeekFunctionTool,
    DeepSeekThinkingAgent,
)
from material_agent.inspiration.models import (
    ArtifactPointerV1,
    Identifier,
    StrictModel,
    canonical_sha256,
    deterministic_id,
)
from material_agent.inspiration.operator_planning import (
    CompileReasonedOperationArgsV3,
)
from material_agent.inspiration.research_graph import MaterialsResearchGraphResultV7
from material_agent.inspiration.research_memory import (
    DownstreamMemoryStage,
    DownstreamOutcomeMemoryV1,
    InspirationMemorySnapshotV1,
    InspirationMemoryStore,
    make_memory_event_v1,
)
from material_agent.orchestrator.llm import SecretResolver
from material_agent.retrieval.storage import LocalArtifactStore
from material_agent.softchem.operations import StructureOperationExecutionResultV2

SCIENTIFIC_LOOP_CONTRACT_VERSION = "scientific-validation-loop-v1"
SCIENTIFIC_LOOP_ROUTE_PROMPT_VERSION = "scientific-route-deepseek-v1"
SCIENTIFIC_LOOP_FEEDBACK_PROMPT_VERSION = "scientific-feedback-deepseek-v1"

Sha256 = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]


class OperatorResultStatus(StrEnum):
    PLANNED = "PLANNED"
    STRUCTURE_VALID = "STRUCTURE_VALID"
    REQUIRES_REVIEW = "REQUIRES_REVIEW"
    REJECTED = "REJECTED"


class ModelTaskInputKind(StrEnum):
    PARENT_STRUCTURE = "PARENT_STRUCTURE"
    OPERATOR_RESULT = "OPERATOR_RESULT"
    PREVIOUS_TASK = "PREVIOUS_TASK"


class ScientificTaskKind(StrEnum):
    GENERIC_MODEL_INFERENCE = "GENERIC_MODEL_INFERENCE"
    ML_PROPERTY_PREDICTION = "ML_PROPERTY_PREDICTION"
    ML_ENSEMBLE_VALIDATION = "ML_ENSEMBLE_VALIDATION"
    TWO_D_STRUCTURE_CHECK = "TWO_D_STRUCTURE_CHECK"
    ML_PRE_RELAXATION = "ML_PRE_RELAXATION"
    MAGNETIC_CONFIGURATION_ENUMERATION = "MAGNETIC_CONFIGURATION_ENUMERATION"
    OVERLAP_MATRIX_GENERATION = "OVERLAP_MATRIX_GENERATION"
    HAMILTONIAN_GRAPH_PREPARATION = "HAMILTONIAN_GRAPH_PREPARATION"
    NON_SCF_ATOMIC_BASIS_GRAPH_PREPARATION = "NON_SCF_ATOMIC_BASIS_GRAPH_PREPARATION"
    PRECOMPUTED_ELECTRONIC_INPUT_IMPORT = "PRECOMPUTED_ELECTRONIC_INPUT_IMPORT"
    DATABASE_BAND_DATA_IMPORT = "DATABASE_BAND_DATA_IMPORT"
    RESEARCH_EVIDENCE_IMPORT = "RESEARCH_EVIDENCE_IMPORT"
    DEEPSEEK_EVIDENCE_FUSION = "DEEPSEEK_EVIDENCE_FUSION"
    ML_HAMILTONIAN_NON_SOC = "ML_HAMILTONIAN_NON_SOC"
    ML_HAMILTONIAN_SOC = "ML_HAMILTONIAN_SOC"
    DFT_NON_SOC = "DFT_NON_SOC"
    MAGNETIC_GROUND_STATE_ANALYSIS = "MAGNETIC_GROUND_STATE_ANALYSIS"
    BAND_ORBITAL_ANALYSIS = "BAND_ORBITAL_ANALYSIS"
    DFT_SOC = "DFT_SOC"
    TOPOLOGY_ANALYSIS = "TOPOLOGY_ANALYSIS"
    EXCHANGE_TC_ESTIMATION = "EXCHANGE_TC_ESTIMATION"


class ScientificArtifactKind(StrEnum):
    SOURCE_STRUCTURE = "SOURCE_STRUCTURE"
    ML_PROPERTY_PREDICTIONS = "ML_PROPERTY_PREDICTIONS"
    CALIBRATED_PROPERTY_ASSESSMENT = "CALIBRATED_PROPERTY_ASSESSMENT"
    TWO_D_STRUCTURE_ASSESSMENT = "TWO_D_STRUCTURE_ASSESSMENT"
    RELAXED_STRUCTURE = "RELAXED_STRUCTURE"
    MAGNETIC_CONFIGURATIONS = "MAGNETIC_CONFIGURATIONS"
    OVERLAP_MATRIX = "OVERLAP_MATRIX"
    NON_SOC_HAMILTONIAN_GRAPH = "NON_SOC_HAMILTONIAN_GRAPH"
    SOC_HAMILTONIAN_GRAPH = "SOC_HAMILTONIAN_GRAPH"
    ML_NON_SOC_HAMILTONIAN = "ML_NON_SOC_HAMILTONIAN"
    ML_SOC_HAMILTONIAN = "ML_SOC_HAMILTONIAN"
    DATABASE_BAND_STRUCTURE = "DATABASE_BAND_STRUCTURE"
    RETRIEVED_EVIDENCE_BUNDLE = "RETRIEVED_EVIDENCE_BUNDLE"
    REASONED_PROPERTY_ASSESSMENT = "REASONED_PROPERTY_ASSESSMENT"
    DFT_TOTAL_ENERGY = "DFT_TOTAL_ENERGY"
    MAGNETIC_GROUND_STATE = "MAGNETIC_GROUND_STATE"
    NON_SOC_GROUND_STATE = "NON_SOC_GROUND_STATE"
    NON_SOC_BAND_STRUCTURE = "NON_SOC_BAND_STRUCTURE"
    ORBITAL_PROJECTIONS = "ORBITAL_PROJECTIONS"
    SOC_GROUND_STATE = "SOC_GROUND_STATE"
    SOC_BAND_STRUCTURE = "SOC_BAND_STRUCTURE"
    WAVEFUNCTION = "WAVEFUNCTION"
    WANNIER_HAMILTONIAN = "WANNIER_HAMILTONIAN"
    FLAT_BAND_ASSESSMENT = "FLAT_BAND_ASSESSMENT"
    TOPOLOGY_INVARIANTS = "TOPOLOGY_INVARIANTS"
    EXCHANGE_PARAMETERS = "EXCHANGE_PARAMETERS"
    TC_ESTIMATE = "TC_ESTIMATE"


class PhysicsCapabilityFeature(StrEnum):
    STRUCTURE_RELAXATION = "STRUCTURE_RELAXATION"
    CALIBRATED_UNCERTAINTY = "CALIBRATED_UNCERTAINTY"
    K_RESOLVED = "K_RESOLVED"
    ORBITAL_PROJECTED = "ORBITAL_PROJECTED"
    WAVEFUNCTION_AVAILABLE = "WAVEFUNCTION_AVAILABLE"
    HAMILTONIAN_AVAILABLE = "HAMILTONIAN_AVAILABLE"
    LEARNED_HAMILTONIAN = "LEARNED_HAMILTONIAN"
    OVERLAP_MATRIX_REQUIRED = "OVERLAP_MATRIX_REQUIRED"
    HAMILTONIAN_GRAPH_INPUT = "HAMILTONIAN_GRAPH_INPUT"
    PRECOMPUTED_ELECTRONIC_INPUT_ONLY = "PRECOMPUTED_ELECTRONIC_INPUT_ONLY"
    NON_SCF_ATOMIC_BASIS_INPUT = "NON_SCF_ATOMIC_BASIS_INPUT"
    BASIS_IDENTITY_FROZEN = "BASIS_IDENTITY_FROZEN"
    MAGNETIC_CONFIGURATION_CONDITIONED = "MAGNETIC_CONFIGURATION_CONDITIONED"
    SELF_CONSISTENT_TOTAL_ENERGY = "SELF_CONSISTENT_TOTAL_ENERGY"
    SOC_EXPLICIT = "SOC_EXPLICIT"
    MAGNETIC_ORDER_RESOLVED = "MAGNETIC_ORDER_RESOLVED"
    TOPOLOGICAL_INVARIANT_AVAILABLE = "TOPOLOGICAL_INVARIANT_AVAILABLE"
    EXCHANGE_MODEL_AVAILABLE = "EXCHANGE_MODEL_AVAILABLE"
    TC_ESTIMATE_AVAILABLE = "TC_ESTIMATE_AVAILABLE"
    RETRIEVED_EVIDENCE_GROUNDED = "RETRIEVED_EVIDENCE_GROUNDED"
    PROBABILISTIC_SCIENTIFIC_REASONING = "PROBABILISTIC_SCIENTIFIC_REASONING"


class WeightStatus(StrEnum):
    READY = "READY"
    NOT_REQUIRED = "NOT_REQUIRED"
    MISSING = "MISSING"
    UNVERIFIED = "UNVERIFIED"


class CapabilityAvailability(StrEnum):
    READY = "READY"
    UNAVAILABLE = "UNAVAILABLE"
    UNHEALTHY = "UNHEALTHY"


class ScientificExecutionMode(StrEnum):
    ML_ONLY = "ML_ONLY"
    HYBRID_DFT = "HYBRID_DFT"


_ML_ONLY_FORBIDDEN_TASKS = frozenset(
    {
        ScientificTaskKind.DFT_NON_SOC,
        ScientificTaskKind.DFT_SOC,
        ScientificTaskKind.EXCHANGE_TC_ESTIMATION,
        ScientificTaskKind.HAMILTONIAN_GRAPH_PREPARATION,
        ScientificTaskKind.MAGNETIC_GROUND_STATE_ANALYSIS,
        ScientificTaskKind.OVERLAP_MATRIX_GENERATION,
    }
)


class RouteAuditStatus(StrEnum):
    APPROVED = "APPROVED"
    BLOCKED = "BLOCKED"


class ScientificEvidenceLevel(StrEnum):
    NONE = "NONE"
    L0_PARSED = "L0_PARSED"
    L1_RETRIEVED = "L1_RETRIEVED"
    L2_ML_SCREENED = "L2_ML_SCREENED"
    L3_DFT_VALIDATED = "L3_DFT_VALIDATED"
    L4_EXPERIMENTAL = "L4_EXPERIMENTAL"
    L5_EXPERT_REVIEWED = "L5_EXPERT_REVIEWED"


_EVIDENCE_RANK = {
    ScientificEvidenceLevel.NONE: -1,
    ScientificEvidenceLevel.L0_PARSED: 0,
    ScientificEvidenceLevel.L1_RETRIEVED: 1,
    ScientificEvidenceLevel.L2_ML_SCREENED: 2,
    ScientificEvidenceLevel.L3_DFT_VALIDATED: 3,
    ScientificEvidenceLevel.L4_EXPERIMENTAL: 4,
    ScientificEvidenceLevel.L5_EXPERT_REVIEWED: 5,
}


class ScientificEvidenceVerdict(StrEnum):
    SUPPORTS = "SUPPORTS"
    CONTRADICTS = "CONTRADICTS"
    INCONCLUSIVE = "INCONCLUSIVE"
    EXECUTION_FAILED = "EXECUTION_FAILED"


class FeedbackAction(StrEnum):
    RETAIN = "RETAIN"
    ELIMINATE = "ELIMINATE"
    MODIFY_OPERATOR = "MODIFY_OPERATOR"
    REQUEST_HIGHER_EVIDENCE = "REQUEST_HIGHER_EVIDENCE"


class HypothesisCandidate(StrictModel):
    """One literature-grounded candidate, independent of any one operator."""

    schema_version: Literal["scientific-validation-loop-v1"] = (
        SCIENTIFIC_LOOP_CONTRACT_VERSION
    )
    candidate_id: Identifier
    material_name: str = Field(min_length=1, max_length=512)
    formula: str | None = Field(default=None, max_length=128)
    hypothesis: str = Field(min_length=10, max_length=4_000)
    mechanism: str = Field(min_length=10, max_length=4_000)
    database_candidate_ids: tuple[Identifier, ...] = Field(min_length=1, max_length=32)
    parent_structure: ArtifactPointerV1
    retrieved_evidence_bundle: ArtifactPointerV1 | None = None
    elements: tuple[str, ...] = Field(min_length=1, max_length=128)
    transition_metal_elements: tuple[str, ...] = Field(default=(), max_length=64)
    dimensionality: int | None = Field(default=None, ge=0, le=3)
    literature_evidence_ids: tuple[Identifier, ...] = Field(default=(), max_length=128)
    unresolved_claims: tuple[Identifier, ...] = Field(min_length=1, max_length=128)
    priority: int = Field(default=1, ge=1, le=1_000)
    scientific_conclusion: Literal[False] = False

    @model_validator(mode="after")
    def canonical_sets(self) -> HypothesisCandidate:
        for name in (
            "database_candidate_ids",
            "elements",
            "transition_metal_elements",
            "literature_evidence_ids",
            "unresolved_claims",
        ):
            values = getattr(self, name)
            if values != tuple(sorted(set(values))):
                raise ValueError(f"{name} must be sorted and unique")
        if not set(self.transition_metal_elements) <= set(self.elements):
            raise ValueError("transition-metal elements must be candidate elements")
        return self


class OperatorResult(StrictModel):
    """Generic projection of either a structural or condition operator result."""

    schema_version: Literal["scientific-validation-loop-v1"] = (
        SCIENTIFIC_LOOP_CONTRACT_VERSION
    )
    operator_result_id: Identifier
    candidate_id: Identifier
    plan_id: Identifier
    operator_id: Identifier
    operator_spec_id: Identifier
    route_sha256: Sha256
    status: OperatorResultStatus
    input_structure: ArtifactPointerV1
    output_structure: ArtifactPointerV1 | None = None
    reason_codes: tuple[Identifier, ...] = Field(min_length=1, max_length=128)
    evidence_level: Literal["NONE", "HEURISTIC_PRIOR_ONLY"] = "NONE"
    scientific_conclusion: Literal[False] = False

    @model_validator(mode="after")
    def validate_result(self) -> OperatorResult:
        expected = deterministic_id(
            "operator-result",
            self.model_dump(mode="python", exclude={"operator_result_id"}),
        )
        if self.operator_result_id != expected:
            raise ValueError("operator result ID differs from canonical content")
        if self.reason_codes != tuple(sorted(set(self.reason_codes))):
            raise ValueError("operator result reason codes must be sorted and unique")
        usable = self.status in {
            OperatorResultStatus.STRUCTURE_VALID,
            OperatorResultStatus.REQUIRES_REVIEW,
        }
        if usable != (self.output_structure is not None):
            raise ValueError(
                "only usable operator results may expose an output structure"
            )
        return self


def make_operator_result(**values: object) -> OperatorResult:
    payload = {
        "schema_version": SCIENTIFIC_LOOP_CONTRACT_VERSION,
        "evidence_level": "NONE",
        "scientific_conclusion": False,
        **values,
    }
    return OperatorResult(
        operator_result_id=deterministic_id("operator-result", payload),
        **payload,
    )


def operator_result_from_structure_execution(
    candidate_id: str,
    execution: StructureOperationExecutionResultV2,
) -> OperatorResult:
    """Project the registered v2 executor result without losing its lineage."""

    plan = execution.plan
    reasons = set(execution.prior.reason_codes)
    reasons.update(
        f"{check.check_id}:{check.status.value}" for check in plan.validation_checks
    )
    return make_operator_result(
        candidate_id=candidate_id,
        plan_id=plan.plan_id,
        operator_id=plan.operator_id,
        operator_spec_id=plan.operator_spec.operator_spec_id,
        route_sha256=plan.route_sha256,
        status=OperatorResultStatus(plan.status.value),
        input_structure=plan.parent_structure_artifact,
        output_structure=plan.output_structure_artifact,
        reason_codes=tuple(sorted(reasons)),
        evidence_level="HEURISTIC_PRIOR_ONLY",
    )


def hypothesis_candidates_from_research_graph(
    graph: MaterialsResearchGraphResultV7,
) -> tuple[HypothesisCandidate, ...]:
    """Project every generic-research candidate into the shared loop contract."""

    database = {item.database_candidate_id: item for item in graph.database_candidates}
    priority = {
        candidate_id: index
        for index, candidate_id in enumerate(
            graph.synthesis.ranked_candidate_ids, start=1
        )
    }
    unresolved: dict[str, set[str]] = {
        item.candidate_id: set() for item in graph.candidates.candidates
    }
    for value in graph.synthesis.unresolved_hard_constraints:
        candidate_id, separator, constraint_id = value.partition(":")
        if separator and candidate_id in unresolved and constraint_id:
            unresolved[candidate_id].add(constraint_id)
    all_constraint_ids = {item.constraint_id for item in graph.constraints.constraints}
    results: list[HypothesisCandidate] = []
    for candidate in graph.candidates.candidates:
        parent_records = [
            database[item]
            for item in candidate.database_candidate_ids
            if item in database
        ]
        if not parent_records:
            raise ValueError(
                f"candidate {candidate.candidate_id} has no resolved database parent"
            )
        anchor = parent_records[0]
        candidate_unresolved = unresolved[candidate.candidate_id] or all_constraint_ids
        formula_elements: set[str] = set()
        if candidate.formula is not None:
            try:
                formula_elements = {
                    element.symbol
                    for element in Composition(candidate.formula).elements
                }
            except ValueError:
                formula_elements = set()
        results.append(
            HypothesisCandidate(
                candidate_id=candidate.candidate_id,
                material_name=candidate.material_name,
                formula=candidate.formula,
                hypothesis=candidate.hypothesis,
                mechanism=candidate.mechanism,
                database_candidate_ids=tuple(
                    sorted(set(candidate.database_candidate_ids))
                ),
                parent_structure=ArtifactPointerV1(
                    uri=anchor.structure_artifact_uri,
                    sha256=anchor.structure_artifact_sha256,
                    media_type="chemical/x-cif",
                ),
                elements=tuple(
                    sorted(
                        {
                            element
                            for parent in parent_records
                            for element in parent.elements
                        }
                        | formula_elements
                    )
                ),
                transition_metal_elements=tuple(
                    sorted(
                        {
                            element
                            for parent in parent_records
                            for element in getattr(parent, "transition_metals", ())
                        }
                    )
                ),
                dimensionality=anchor.dimensionality,
                literature_evidence_ids=tuple(sorted(set(candidate.evidence_ids))),
                unresolved_claims=tuple(sorted(candidate_unresolved)),
                priority=priority.get(candidate.candidate_id, len(priority) + 1),
            )
        )
    return tuple(sorted(results, key=lambda item: (item.priority, item.candidate_id)))


class DeepSeekReasoningProvenance(StrictModel):
    provider: Literal["deepseek"] = "deepseek"
    model_id: Literal["deepseek-v4-pro"] = "deepseek-v4-pro"
    thinking_mode: Literal["enabled"] = "enabled"
    reasoning_content_persisted: Literal[False] = False
    prompt_version: Identifier
    receipt_sha256: Sha256
    live_call: bool
    receipt: DeepSeekAgentReceiptV1 | None = None

    @model_validator(mode="after")
    def validate_receipt(self) -> DeepSeekReasoningProvenance:
        if self.live_call and self.receipt is None:
            raise ValueError("live DeepSeek reasoning requires its complete receipt")
        if self.receipt is not None:
            if canonical_sha256(self.receipt) != self.receipt_sha256:
                raise ValueError("DeepSeek receipt hash differs from receipt content")
            if self.receipt.prompt_version != self.prompt_version:
                raise ValueError("DeepSeek receipt prompt version mismatch")
        return self


def reasoning_provenance(
    receipt: DeepSeekAgentReceiptV1,
    *,
    live_call: bool = True,
) -> DeepSeekReasoningProvenance:
    return DeepSeekReasoningProvenance(
        prompt_version=receipt.prompt_version,
        receipt_sha256=canonical_sha256(receipt),
        live_call=live_call,
        receipt=receipt,
    )


class ProposedModelTask(StrictModel):
    task_id: Identifier
    candidate_id: Identifier
    model_id: Identifier
    task_kind: ScientificTaskKind = ScientificTaskKind.GENERIC_MODEL_INFERENCE
    input_kind: ModelTaskInputKind
    operator_result_id: Identifier | None = None
    prerequisite_task_ids: tuple[Identifier, ...] = Field(default=(), max_length=32)
    required_input_artifact_kinds: tuple[ScientificArtifactKind, ...] = Field(
        default=(), max_length=32
    )
    produced_artifact_kinds: tuple[ScientificArtifactKind, ...] = Field(
        default=(), max_length=32
    )
    required_capability_features: tuple[PhysicsCapabilityFeature, ...] = Field(
        default=(), max_length=32
    )
    requested_observables: tuple[Identifier, ...] = Field(min_length=1, max_length=64)
    required_evidence_level: ScientificEvidenceLevel
    parameters: dict[Identifier, JsonValue] = Field(
        default_factory=dict, max_length=128
    )
    rationale: str = Field(min_length=10, max_length=2_000)
    falsification_rule: str = Field(min_length=10, max_length=2_000)

    @model_validator(mode="after")
    def validate_input(self) -> ProposedModelTask:
        needs_operator = self.input_kind is ModelTaskInputKind.OPERATOR_RESULT
        if needs_operator != (self.operator_result_id is not None):
            raise ValueError(
                "OPERATOR_RESULT input must bind exactly one operator result"
            )
        for name in (
            "prerequisite_task_ids",
            "required_input_artifact_kinds",
            "produced_artifact_kinds",
            "required_capability_features",
            "requested_observables",
        ):
            values = getattr(self, name)
            if values != tuple(sorted(set(values), key=str)):
                raise ValueError(f"{name} must be sorted and unique")
        if self.task_id in self.prerequisite_task_ids:
            raise ValueError("model task cannot depend on itself")
        if self.required_input_artifact_kinds and not self.prerequisite_task_ids:
            raise ValueError(
                "typed prerequisite artifacts require prerequisite task IDs"
            )
        if (
            self.required_input_artifact_kinds
            and self.input_kind is not ModelTaskInputKind.PREVIOUS_TASK
        ):
            raise ValueError("typed prerequisite artifacts require PREVIOUS_TASK input")
        if self.parameters != dict(sorted(self.parameters.items())):
            raise ValueError("model task parameters must use canonical key order")
        return self


class DeepSeekModelRouteDraft(StrictModel):
    tasks: tuple[ProposedModelTask, ...] = Field(min_length=1, max_length=128)
    route_summary: str = Field(min_length=10, max_length=4_000)


class DeepSeekModelRouteProposal(DeepSeekModelRouteDraft):
    schema_version: Literal["scientific-validation-loop-v1"] = (
        SCIENTIFIC_LOOP_CONTRACT_VERSION
    )
    goal_sha256: Sha256
    provenance: DeepSeekReasoningProvenance
    scientific_conclusion: Literal[False] = False

    @model_validator(mode="after")
    def unique_tasks(self) -> DeepSeekModelRouteProposal:
        ids = tuple(task.task_id for task in self.tasks)
        if len(ids) != len(set(ids)):
            raise ValueError("DeepSeek route task IDs must be unique")
        known: set[str] = set()
        for task in self.tasks:
            if not set(task.prerequisite_task_ids) <= known:
                raise ValueError("task prerequisites must refer to earlier route tasks")
            known.add(task.task_id)
        return self


class HamiltonianSocMode(StrEnum):
    NON_SOC = "NON_SOC"
    SOC = "SOC"


class HamiltonianInputMode(StrEnum):
    OVERLAP_MATRIX = "OVERLAP_MATRIX"
    DUAL_SOC_GRAPH = "DUAL_SOC_GRAPH"


class HamiltonianInputOrigin(StrEnum):
    """Whether inference can start from cached inputs without a runtime DFT step."""

    PRECOMPUTED_TRUSTED = "PRECOMPUTED_TRUSTED"
    RUNTIME_NON_SCF_ATOMIC_BASIS = "RUNTIME_NON_SCF_ATOMIC_BASIS"
    RUNTIME_ELECTRONIC_STRUCTURE = "RUNTIME_ELECTRONIC_STRUCTURE"


class HamiltonianBenchmark(StrictModel):
    """Quantitative applicability evidence for one learned Hamiltonian bundle."""

    benchmark_id: Identifier
    report_artifact: ArtifactPointerV1
    held_out_structure_count: int = Field(ge=1)
    max_band_energy_error_ev: float = Field(ge=0)
    max_flat_band_width_error_ev: float = Field(ge=0)
    band_ordering_accuracy: float = Field(ge=0, le=1)
    crossing_classification_accuracy: float = Field(ge=0, le=1)
    orbital_weight_mae: float = Field(ge=0, le=1)
    max_soc_gap_error_ev: float | None = Field(default=None, ge=0)
    topology_invariant_accuracy: float | None = Field(default=None, ge=0, le=1)


class HamiltonianModelApplicability(StrictModel):
    """Frozen basis/interface/domain identity owned by the runtime registry."""

    interface: Literal["openmx", "abacus", "fhi-aims", "siesta"]
    basis_id: Identifier
    dft_software_version: Identifier
    training_domain_id: Identifier
    training_domain_sha256: Sha256
    soc_mode: HamiltonianSocMode
    input_mode: HamiltonianInputMode = HamiltonianInputMode.OVERLAP_MATRIX
    input_origin: HamiltonianInputOrigin = (
        HamiltonianInputOrigin.RUNTIME_ELECTRONIC_STRUCTURE
    )
    required_input_artifact_kinds: tuple[ScientificArtifactKind, ...] = (
        ScientificArtifactKind.OVERLAP_MATRIX,
        ScientificArtifactKind.RELAXED_STRUCTURE,
    )
    supported_magnetic_modes: tuple[
        Literal["NONMAGNETIC", "COLLINEAR", "NONCOLLINEAR"], ...
    ] = Field(min_length=1, max_length=3)
    overlap_required: bool = True
    benchmark: HamiltonianBenchmark | None = None

    @model_validator(mode="after")
    def canonical_magnetic_modes(self) -> HamiltonianModelApplicability:
        if self.supported_magnetic_modes != tuple(
            sorted(set(self.supported_magnetic_modes))
        ):
            raise ValueError("supported magnetic modes must be sorted and unique")
        if (
            self.soc_mode is HamiltonianSocMode.SOC
            and self.benchmark is not None
            and self.benchmark.max_soc_gap_error_ev is None
        ):
            raise ValueError("SOC Hamiltonian benchmark requires a SOC-gap error")
        if self.required_input_artifact_kinds != tuple(
            sorted(set(self.required_input_artifact_kinds), key=str)
        ):
            raise ValueError("Hamiltonian input Artifact kinds are not canonical")
        required = set(self.required_input_artifact_kinds)
        if self.input_mode is HamiltonianInputMode.OVERLAP_MATRIX:
            if (
                not self.overlap_required
                or not {
                    ScientificArtifactKind.OVERLAP_MATRIX,
                    ScientificArtifactKind.RELAXED_STRUCTURE,
                }
                <= required
            ):
                raise ValueError(
                    "overlap input mode requires relaxed structure and overlap Artifacts"
                )
        elif (
            self.overlap_required
            or not {
                ScientificArtifactKind.NON_SOC_HAMILTONIAN_GRAPH,
                ScientificArtifactKind.SOC_HAMILTONIAN_GRAPH,
            }
            <= required
        ):
            raise ValueError("dual-graph input mode requires both graph Artifacts")
        return self


class ModelCapability(StrictModel):
    """Runtime-owned facts consumed by policy; this does not select science."""

    model_id: Identifier
    availability: CapabilityAvailability
    weight_status: WeightStatus
    checkpoint_sha256: Sha256 | None = None
    supported_observables: tuple[Identifier, ...] = Field(max_length=128)
    supported_task_kinds: tuple[ScientificTaskKind, ...] = Field(
        default=(), max_length=32
    )
    accepted_artifact_kinds: tuple[ScientificArtifactKind, ...] = Field(
        default=(), max_length=64
    )
    required_input_artifact_kinds: tuple[ScientificArtifactKind, ...] = Field(
        default=(), max_length=64
    )
    produced_artifact_kinds: tuple[ScientificArtifactKind, ...] = Field(
        default=(), max_length=64
    )
    parameter_contract_id: Identifier | None = None
    parameter_schema: dict[str, object] | None = None
    physics_features: tuple[PhysicsCapabilityFeature, ...] = Field(
        default=(), max_length=32
    )
    supported_elements: tuple[str, ...] | None = Field(default=None, max_length=128)
    supported_dimensionalities: tuple[int, ...] | None = Field(
        default=None, max_length=4
    )
    evidence_ceiling: ScientificEvidenceLevel
    estimated_cost_units: int = Field(ge=0, le=1_000_000)
    real_backend: bool
    benchmark_status: Literal["VALIDATED", "NOT_RUN", "FAILED"]
    hamiltonian_applicability: HamiltonianModelApplicability | None = None

    @model_validator(mode="after")
    def validate_capability(self) -> ModelCapability:
        for name in (
            "supported_observables",
            "supported_elements",
            "supported_task_kinds",
            "accepted_artifact_kinds",
            "required_input_artifact_kinds",
            "produced_artifact_kinds",
            "physics_features",
        ):
            values = getattr(self, name)
            if values is not None and values != tuple(sorted(set(values), key=str)):
                raise ValueError(f"{name} must be sorted and unique")
        dimensions = self.supported_dimensionalities
        if dimensions is not None and dimensions != tuple(sorted(set(dimensions))):
            raise ValueError("supported dimensionalities must be sorted and unique")
        if self.weight_status is WeightStatus.READY and self.checkpoint_sha256 is None:
            raise ValueError("ready model weights require a checkpoint SHA-256")
        if self.weight_status is WeightStatus.NOT_REQUIRED and self.checkpoint_sha256:
            raise ValueError("weight-free capability cannot bind a checkpoint")
        if (
            not self.real_backend
            and self.evidence_ceiling is not ScientificEvidenceLevel.NONE
        ):
            raise ValueError("non-real backend evidence ceiling must remain NONE")
        if not set(self.required_input_artifact_kinds) <= set(
            self.accepted_artifact_kinds
        ):
            raise ValueError(
                "required model inputs must be included in accepted Artifact kinds"
            )
        if (self.parameter_contract_id is None) != (self.parameter_schema is None):
            raise ValueError(
                "parameter contract ID and JSON schema must be declared together"
            )
        if self.parameter_schema is not None:
            try:
                Draft202012Validator.check_schema(self.parameter_schema)
            except SchemaError as exc:
                raise ValueError("capability parameter schema is invalid") from exc
        if (
            self.benchmark_status != "VALIDATED"
            and _EVIDENCE_RANK[self.evidence_ceiling]
            > _EVIDENCE_RANK[ScientificEvidenceLevel.L1_RETRIEVED]
        ):
            raise ValueError("unbenchmarked capability cannot claim L2 or higher")
        learned_hamiltonian_tasks = {
            ScientificTaskKind.ML_HAMILTONIAN_NON_SOC,
            ScientificTaskKind.ML_HAMILTONIAN_SOC,
        }
        declares_learned_hamiltonian = bool(
            learned_hamiltonian_tasks & set(self.supported_task_kinds)
        )
        if declares_learned_hamiltonian != (self.hamiltonian_applicability is not None):
            raise ValueError(
                "learned-Hamiltonian capability requires exactly one "
                "applicability contract"
            )
        if declares_learned_hamiltonian and not {
            PhysicsCapabilityFeature.HAMILTONIAN_AVAILABLE,
            PhysicsCapabilityFeature.LEARNED_HAMILTONIAN,
            PhysicsCapabilityFeature.BASIS_IDENTITY_FROZEN,
        } <= set(self.physics_features):
            raise ValueError(
                "learned-Hamiltonian capability is missing mandatory features"
            )
        applicability = self.hamiltonian_applicability
        if applicability is not None:
            if (self.benchmark_status == "VALIDATED") != (
                applicability.benchmark is not None
            ):
                raise ValueError(
                    "Hamiltonian benchmark status and applicability binding differ"
                )
            precomputed_feature = (
                PhysicsCapabilityFeature.PRECOMPUTED_ELECTRONIC_INPUT_ONLY
                in self.physics_features
            )
            if (
                applicability.input_origin is HamiltonianInputOrigin.PRECOMPUTED_TRUSTED
            ) != precomputed_feature:
                raise ValueError(
                    "precomputed Hamiltonian input origin and capability feature differ"
                )
            non_scf_feature = (
                PhysicsCapabilityFeature.NON_SCF_ATOMIC_BASIS_INPUT
                in self.physics_features
            )
            if (
                applicability.input_origin
                is HamiltonianInputOrigin.RUNTIME_NON_SCF_ATOMIC_BASIS
            ) != non_scf_feature:
                raise ValueError(
                    "non-SCF Hamiltonian input origin and capability feature differ"
                )
            if (
                applicability.input_mode is HamiltonianInputMode.OVERLAP_MATRIX
                and PhysicsCapabilityFeature.OVERLAP_MATRIX_REQUIRED
                not in self.physics_features
            ):
                raise ValueError("overlap Hamiltonian capability lacks overlap feature")
            if (
                applicability.input_mode is HamiltonianInputMode.DUAL_SOC_GRAPH
                and PhysicsCapabilityFeature.HAMILTONIAN_GRAPH_INPUT
                not in self.physics_features
            ):
                raise ValueError(
                    "dual-graph Hamiltonian capability lacks graph feature"
                )
            expected = (
                ScientificTaskKind.ML_HAMILTONIAN_SOC
                if applicability.soc_mode is HamiltonianSocMode.SOC
                else ScientificTaskKind.ML_HAMILTONIAN_NON_SOC
            )
            if expected not in self.supported_task_kinds:
                raise ValueError("Hamiltonian SOC mode differs from supported task")
            expected_artifact = (
                ScientificArtifactKind.ML_SOC_HAMILTONIAN
                if applicability.soc_mode is HamiltonianSocMode.SOC
                else ScientificArtifactKind.ML_NON_SOC_HAMILTONIAN
            )
            if expected_artifact not in self.produced_artifact_kinds:
                raise ValueError(
                    "Hamiltonian SOC mode differs from produced Artifact kind"
                )
            if (
                applicability.soc_mode is HamiltonianSocMode.SOC
                and PhysicsCapabilityFeature.SOC_EXPLICIT not in self.physics_features
            ):
                raise ValueError(
                    "SOC Hamiltonian capability must be explicitly spinful"
                )
        return self


class ValidationRoutePolicy(StrictModel):
    policy_id: Identifier = "scientific-route-audit-policy-v1"
    execution_mode: Literal[ScientificExecutionMode.ML_ONLY] = (
        ScientificExecutionMode.ML_ONLY
    )
    max_candidates: int = Field(default=16, ge=1, le=256)
    max_tasks: int = Field(default=64, ge=1, le=512)
    max_cost_units: int = Field(default=10_000, ge=0, le=10_000_000)
    require_real_backend: bool = True
    require_verified_benchmark_for_l2: bool = True
    require_live_deepseek_reasoning: bool = True
    allow_speed_first_real_ml_elimination: bool = True
    speed_first_elimination_reason_codes: tuple[Identifier, ...] = (
        "BANDWIDTH_EXCEEDS_POLICY",
        "CONTRIBUTOR_SUBLATTICE_NOT_PERIODIC_CONNECTED",
        "DISPERSIVE_BAND_CROSSES_FERMI",
        "FIRST_ORDER_BAND_CROSSING_DETECTED",
        "NOT_TWO_DIMENSIONAL",
        "PERIODIC_VOID_GAP_BELOW_POLICY",
        "SURROGATE_CLEAR_THRESHOLD_FAILURE",
        "UNCOMMON_TRANSITION_METAL_VALENCE",
    )
    # Speed-first inspiration thresholds.  These qualify ranking/screening evidence,
    # not a definitive electronic-structure claim.
    max_ml_band_energy_error_ev: float = Field(default=0.025, gt=0, le=1)
    max_ml_flat_band_width_error_ev: float = Field(default=0.015, gt=0, le=1)
    min_ml_band_ordering_accuracy: float = Field(default=0.95, ge=0, le=1)
    min_ml_crossing_accuracy: float = Field(default=0.95, ge=0, le=1)
    max_ml_orbital_weight_mae: float = Field(default=0.15, ge=0, le=1)
    max_ml_soc_gap_error_ev: float = Field(default=0.025, gt=0, le=1)

    @model_validator(mode="after")
    def canonical_speed_first_reasons(self) -> ValidationRoutePolicy:
        if self.speed_first_elimination_reason_codes != tuple(
            sorted(set(self.speed_first_elimination_reason_codes))
        ):
            raise ValueError(
                "speed-first elimination reason codes must be sorted and unique"
            )
        return self


class AuditedModelTask(StrictModel):
    proposed_task: ProposedModelTask
    status: RouteAuditStatus
    reason_codes: tuple[Identifier, ...] = Field(min_length=1, max_length=32)
    input_structure: ArtifactPointerV1 | None = None
    capability_sha256: Sha256 | None = None
    approved_evidence_ceiling: ScientificEvidenceLevel = ScientificEvidenceLevel.NONE

    @model_validator(mode="after")
    def validate_audit(self) -> AuditedModelTask:
        if self.reason_codes != tuple(sorted(set(self.reason_codes))):
            raise ValueError("task audit reason codes must be sorted and unique")
        if self.status is RouteAuditStatus.APPROVED and (
            self.input_structure is None or self.capability_sha256 is None
        ):
            raise ValueError("approved task requires structure and capability binding")
        if self.status is RouteAuditStatus.BLOCKED and (
            self.approved_evidence_ceiling is not ScientificEvidenceLevel.NONE
        ):
            raise ValueError("blocked task cannot carry an evidence allowance")
        return self


class ModelTaskPlan(StrictModel):
    schema_version: Literal["scientific-validation-loop-v1"] = (
        SCIENTIFIC_LOOP_CONTRACT_VERSION
    )
    plan_id: Identifier
    goal_sha256: Sha256
    route_proposal_sha256: Sha256
    policy_sha256: Sha256
    capability_snapshot_sha256: Sha256
    tasks: tuple[AuditedModelTask, ...] = Field(min_length=1, max_length=512)
    total_approved_cost_units: int = Field(ge=0)
    scientific_scope_source: Literal["DEEPSEEK_NATIVE_REASONING"] = (
        "DEEPSEEK_NATIVE_REASONING"
    )
    deterministic_policy_role: Literal[
        "APPLICABILITY_BUDGET_WEIGHT_AND_EVIDENCE_AUDIT_ONLY"
    ] = "APPLICABILITY_BUDGET_WEIGHT_AND_EVIDENCE_AUDIT_ONLY"
    scientific_conclusion: Literal[False] = False

    @model_validator(mode="after")
    def validate_identity(self) -> ModelTaskPlan:
        payload = self.model_dump(mode="python", exclude={"plan_id"})
        if self.plan_id != deterministic_id("model-task-plan", payload):
            raise ValueError("model task plan ID differs from canonical content")
        return self


def compile_model_task_plan(
    *,
    goal: str,
    hypotheses: Sequence[HypothesisCandidate],
    operator_results: Sequence[OperatorResult],
    proposal: DeepSeekModelRouteProposal,
    capabilities: Sequence[ModelCapability],
    policy: ValidationRoutePolicy,
) -> ModelTaskPlan:
    """Audit a DeepSeek route without changing its scientific contents."""

    goal_sha256 = canonical_sha256({"goal": goal})
    if proposal.goal_sha256 != goal_sha256:
        raise ValueError("DeepSeek route goal hash differs from the active goal")
    hypothesis_by_id = {item.candidate_id: item for item in hypotheses}
    if len(hypothesis_by_id) != len(hypotheses):
        raise ValueError("hypothesis candidate IDs must be unique")
    if len(hypotheses) > policy.max_candidates:
        raise ValueError("candidate count exceeds deterministic policy budget")
    if len(proposal.tasks) > policy.max_tasks:
        raise ValueError("DeepSeek task count exceeds deterministic policy budget")
    result_by_id = {item.operator_result_id: item for item in operator_results}
    if len(result_by_id) != len(operator_results):
        raise ValueError("operator result IDs must be unique")
    capability_by_id = {item.model_id: item for item in capabilities}
    if len(capability_by_id) != len(capabilities):
        raise ValueError("model capability IDs must be unique")

    audited: list[AuditedModelTask] = []
    approved_cost = 0
    approved_task_artifact_kinds: dict[str, set[ScientificArtifactKind]] = {}
    for task in proposal.tasks:
        reasons: set[str] = set()
        if policy.require_live_deepseek_reasoning and not proposal.provenance.live_call:
            reasons.add("LIVE_DEEPSEEK_ROUTE_REQUIRED")
        hypothesis = hypothesis_by_id.get(task.candidate_id)
        capability = capability_by_id.get(task.model_id)
        structure: ArtifactPointerV1 | None = None
        if hypothesis is None:
            reasons.add("UNKNOWN_HYPOTHESIS_CANDIDATE")
        elif task.input_kind is ModelTaskInputKind.PARENT_STRUCTURE:
            structure = hypothesis.parent_structure
        elif task.input_kind is ModelTaskInputKind.OPERATOR_RESULT:
            operator = result_by_id.get(task.operator_result_id or "")
            if operator is None or operator.candidate_id != task.candidate_id:
                reasons.add("OPERATOR_RESULT_NOT_BOUND_TO_CANDIDATE")
            elif operator.output_structure is None:
                reasons.add("OPERATOR_RESULT_HAS_NO_USABLE_STRUCTURE")
            else:
                structure = operator.output_structure
        else:
            missing = [
                item
                for item in task.prerequisite_task_ids
                if item not in approved_task_artifact_kinds
            ]
            if missing:
                reasons.add("PREVIOUS_TASK_OUTPUT_UNAVAILABLE")
            elif task.prerequisite_task_ids:
                structure = hypothesis.parent_structure
            else:
                reasons.add("PREVIOUS_TASK_INPUT_REQUIRES_PREREQUISITE")
            available_kinds = {
                kind
                for dependency_id in task.prerequisite_task_ids
                for kind in approved_task_artifact_kinds.get(dependency_id, set())
            }
            if not set(task.required_input_artifact_kinds) <= available_kinds:
                reasons.add("REQUIRED_PREREQUISITE_ARTIFACT_UNAVAILABLE")

        if (
            task.task_kind is not ScientificTaskKind.GENERIC_MODEL_INFERENCE
            and not task.produced_artifact_kinds
        ):
            reasons.add("TYPED_OUTPUT_ARTIFACTS_REQUIRED")
        if task.task_kind is ScientificTaskKind.RESEARCH_EVIDENCE_IMPORT:
            if hypothesis is None or hypothesis.retrieved_evidence_bundle is None:
                reasons.add("RETRIEVED_EVIDENCE_BUNDLE_NOT_BOUND")
            else:
                try:
                    requested_bundle = ArtifactPointerV1.model_validate(
                        task.parameters.get("bundle_pointer")
                    )
                except (TypeError, ValueError):
                    reasons.add("RETRIEVED_EVIDENCE_BUNDLE_POINTER_INVALID")
                else:
                    if requested_bundle != hypothesis.retrieved_evidence_bundle:
                        reasons.add("RETRIEVED_EVIDENCE_BUNDLE_LINEAGE_MISMATCH")
        if policy.execution_mode is ScientificExecutionMode.ML_ONLY:
            if task.task_kind in _ML_ONLY_FORBIDDEN_TASKS:
                reasons.add("DFT_DISABLED_BY_ML_ONLY_MODE")
            if task.task_kind in {
                ScientificTaskKind.OVERLAP_MATRIX_GENERATION,
                ScientificTaskKind.HAMILTONIAN_GRAPH_PREPARATION,
            }:
                reasons.add("RUNTIME_ELECTRONIC_PREPROCESSING_DISABLED_BY_ML_ONLY_MODE")
            if (
                _EVIDENCE_RANK[task.required_evidence_level]
                > _EVIDENCE_RANK[ScientificEvidenceLevel.L2_ML_SCREENED]
            ):
                reasons.add("ML_ONLY_EVIDENCE_CEILING_EXCEEDED")
            if PhysicsCapabilityFeature.SELF_CONSISTENT_TOTAL_ENERGY in set(
                task.required_capability_features
            ):
                reasons.add("SELF_CONSISTENT_DFT_DISABLED_BY_ML_ONLY_MODE")
        if task.task_kind in {
            ScientificTaskKind.ML_HAMILTONIAN_NON_SOC,
            ScientificTaskKind.ML_HAMILTONIAN_SOC,
        }:
            expected_output = (
                ScientificArtifactKind.ML_SOC_HAMILTONIAN
                if task.task_kind is ScientificTaskKind.ML_HAMILTONIAN_SOC
                else ScientificArtifactKind.ML_NON_SOC_HAMILTONIAN
            )
            if expected_output not in task.produced_artifact_kinds:
                reasons.add("ML_HAMILTONIAN_OUTPUT_KIND_MISMATCH")
            if (
                task.task_kind is ScientificTaskKind.ML_HAMILTONIAN_SOC
                and PhysicsCapabilityFeature.SOC_EXPLICIT
                not in task.required_capability_features
            ):
                reasons.add("SOC_EXPLICIT_FEATURE_NOT_REQUESTED")
        if task.task_kind is ScientificTaskKind.DFT_NON_SOC:
            if (
                ScientificArtifactKind.DFT_TOTAL_ENERGY
                not in task.produced_artifact_kinds
            ):
                reasons.add("DFT_TOTAL_ENERGY_OUTPUT_REQUIRED")
            if (
                PhysicsCapabilityFeature.SELF_CONSISTENT_TOTAL_ENERGY
                not in task.required_capability_features
            ):
                reasons.add("DFT_SELF_CONSISTENT_ENERGY_FEATURE_NOT_REQUESTED")
        if task.task_kind is ScientificTaskKind.MAGNETIC_GROUND_STATE_ANALYSIS:
            if (
                ScientificArtifactKind.DFT_TOTAL_ENERGY
                not in task.required_input_artifact_kinds
            ):
                reasons.add("MAGNETIC_GROUND_STATE_DFT_ENERGIES_REQUIRED")
            if task.produced_artifact_kinds != (
                ScientificArtifactKind.MAGNETIC_GROUND_STATE,
            ):
                reasons.add("MAGNETIC_GROUND_STATE_OUTPUT_REQUIRED")
        if task.task_kind is ScientificTaskKind.DFT_SOC and (
            PhysicsCapabilityFeature.SOC_EXPLICIT
            not in task.required_capability_features
        ):
            reasons.add("SOC_EXPLICIT_FEATURE_NOT_REQUESTED")

        if capability is None:
            reasons.add("MODEL_CAPABILITY_NOT_REGISTERED")
        else:
            if capability.availability is not CapabilityAvailability.READY:
                reasons.add("MODEL_BACKEND_NOT_READY")
            if capability.weight_status in {
                WeightStatus.MISSING,
                WeightStatus.UNVERIFIED,
            }:
                reasons.add("MODEL_WEIGHT_NOT_READY")
            if capability.parameter_schema is not None:
                parameter_errors = tuple(
                    Draft202012Validator(capability.parameter_schema).iter_errors(
                        task.parameters
                    )
                )
                if parameter_errors:
                    reasons.add("PARAMETERS_DO_NOT_MATCH_CAPABILITY_SCHEMA")
            if policy.require_real_backend and not capability.real_backend:
                reasons.add("REAL_BACKEND_REQUIRED")
            if not set(task.requested_observables) <= set(
                capability.supported_observables
            ):
                reasons.add("REQUESTED_OBSERVABLE_UNSUPPORTED")
            if (
                task.task_kind is not ScientificTaskKind.GENERIC_MODEL_INFERENCE
                and task.task_kind not in capability.supported_task_kinds
            ):
                reasons.add("TASK_KIND_UNSUPPORTED")
            if not set(task.required_input_artifact_kinds) <= set(
                capability.accepted_artifact_kinds
            ):
                reasons.add("INPUT_ARTIFACT_KIND_UNSUPPORTED")
            if not set(capability.required_input_artifact_kinds) <= set(
                task.required_input_artifact_kinds
            ):
                reasons.add("MODEL_REQUIRED_INPUT_ARTIFACT_MISSING")
            if not set(task.produced_artifact_kinds) <= set(
                capability.produced_artifact_kinds
            ):
                reasons.add("OUTPUT_ARTIFACT_KIND_UNSUPPORTED")
            if not set(task.required_capability_features) <= set(
                capability.physics_features
            ):
                reasons.add("REQUIRED_PHYSICS_FEATURE_UNAVAILABLE")
            if (
                hypothesis is not None
                and capability.supported_elements is not None
                and not set(hypothesis.elements) <= set(capability.supported_elements)
            ):
                reasons.add("ELEMENT_OUTSIDE_REVIEWED_DOMAIN")
            if (
                hypothesis is not None
                and hypothesis.dimensionality is not None
                and capability.supported_dimensionalities is not None
                and hypothesis.dimensionality
                not in capability.supported_dimensionalities
            ):
                reasons.add("DIMENSIONALITY_OUTSIDE_REVIEWED_DOMAIN")
            if (
                _EVIDENCE_RANK[task.required_evidence_level]
                > _EVIDENCE_RANK[capability.evidence_ceiling]
            ):
                reasons.add("EVIDENCE_CEILING_TOO_LOW")
            if (
                policy.require_verified_benchmark_for_l2
                and _EVIDENCE_RANK[task.required_evidence_level] >= 2
                and capability.benchmark_status != "VALIDATED"
            ):
                reasons.add("BENCHMARK_NOT_VALIDATED")
            if approved_cost + capability.estimated_cost_units > policy.max_cost_units:
                reasons.add("ROUTE_COST_BUDGET_EXCEEDED")
            if task.task_kind in {
                ScientificTaskKind.ML_HAMILTONIAN_NON_SOC,
                ScientificTaskKind.ML_HAMILTONIAN_SOC,
            }:
                applicability = capability.hamiltonian_applicability
                if applicability is None:
                    reasons.add("HAMILTONIAN_APPLICABILITY_UNAVAILABLE")
                else:
                    benchmark = applicability.benchmark
                    if (
                        policy.execution_mode is ScientificExecutionMode.ML_ONLY
                        and applicability.input_origin
                        not in {
                            HamiltonianInputOrigin.PRECOMPUTED_TRUSTED,
                            HamiltonianInputOrigin.RUNTIME_NON_SCF_ATOMIC_BASIS,
                        }
                    ):
                        reasons.add(
                            "PRECOMPUTED_ELECTRONIC_INPUT_REQUIRED_BY_ML_ONLY_MODE"
                        )
                    if not set(applicability.required_input_artifact_kinds) <= set(
                        task.required_input_artifact_kinds
                    ):
                        reasons.add("ML_HAMILTONIAN_REQUIRED_INPUTS_MISSING")
                    if benchmark is not None:
                        if (
                            benchmark.max_band_energy_error_ev
                            > policy.max_ml_band_energy_error_ev
                        ):
                            reasons.add("ML_BAND_ERROR_EXCEEDS_POLICY")
                        if (
                            benchmark.max_flat_band_width_error_ev
                            > policy.max_ml_flat_band_width_error_ev
                        ):
                            reasons.add("ML_FLAT_BAND_WIDTH_ERROR_EXCEEDS_POLICY")
                        if (
                            benchmark.band_ordering_accuracy
                            < policy.min_ml_band_ordering_accuracy
                        ):
                            reasons.add("ML_BAND_ORDERING_ACCURACY_BELOW_POLICY")
                        if (
                            benchmark.crossing_classification_accuracy
                            < policy.min_ml_crossing_accuracy
                        ):
                            reasons.add("ML_CROSSING_ACCURACY_BELOW_POLICY")
                        if (
                            benchmark.orbital_weight_mae
                            > policy.max_ml_orbital_weight_mae
                        ):
                            reasons.add("ML_ORBITAL_WEIGHT_ERROR_EXCEEDS_POLICY")
                        if task.task_kind is ScientificTaskKind.ML_HAMILTONIAN_SOC:
                            soc_error = benchmark.max_soc_gap_error_ev
                            if (
                                soc_error is None
                                or soc_error > policy.max_ml_soc_gap_error_ev
                            ):
                                reasons.add("ML_SOC_GAP_ERROR_EXCEEDS_POLICY")

        if reasons:
            audited.append(
                AuditedModelTask(
                    proposed_task=task,
                    status=RouteAuditStatus.BLOCKED,
                    reason_codes=tuple(sorted(reasons)),
                    input_structure=structure,
                    capability_sha256=(
                        canonical_sha256(capability) if capability is not None else None
                    ),
                )
            )
            continue
        assert capability is not None and structure is not None
        approved_cost += capability.estimated_cost_units
        approved_task_artifact_kinds[task.task_id] = set(task.produced_artifact_kinds)
        audited.append(
            AuditedModelTask(
                proposed_task=task,
                status=RouteAuditStatus.APPROVED,
                reason_codes=("DEEPSEEK_ROUTE_PASSED_DETERMINISTIC_AUDIT",),
                input_structure=structure,
                capability_sha256=canonical_sha256(capability),
                approved_evidence_ceiling=capability.evidence_ceiling,
            )
        )

    values = {
        "schema_version": SCIENTIFIC_LOOP_CONTRACT_VERSION,
        "goal_sha256": goal_sha256,
        "route_proposal_sha256": canonical_sha256(proposal),
        "policy_sha256": canonical_sha256(policy),
        "capability_snapshot_sha256": canonical_sha256(tuple(capabilities)),
        "tasks": tuple(audited),
        "total_approved_cost_units": approved_cost,
        "scientific_scope_source": "DEEPSEEK_NATIVE_REASONING",
        "deterministic_policy_role": (
            "APPLICABILITY_BUDGET_WEIGHT_AND_EVIDENCE_AUDIT_ONLY"
        ),
        "scientific_conclusion": False,
    }
    return ModelTaskPlan(
        plan_id=deterministic_id("model-task-plan", values),
        **values,
    )


class ScientificTaskArtifact(StrictModel):
    artifact_id: Identifier
    candidate_id: Identifier
    producer_task_id: Identifier
    kind: ScientificArtifactKind
    pointer: ArtifactPointerV1
    is_mock: bool

    @model_validator(mode="after")
    def validate_identity(self) -> ScientificTaskArtifact:
        expected = deterministic_id(
            "scientific-task-artifact",
            self.model_dump(mode="python", exclude={"artifact_id"}),
        )
        if self.artifact_id != expected:
            raise ValueError("scientific task Artifact ID differs from content")
        return self


def make_scientific_task_artifact(
    *,
    candidate_id: str,
    producer_task_id: str,
    kind: ScientificArtifactKind,
    pointer: ArtifactPointerV1,
    is_mock: bool,
) -> ScientificTaskArtifact:
    values = {
        "candidate_id": candidate_id,
        "producer_task_id": producer_task_id,
        "kind": kind,
        "pointer": pointer,
        "is_mock": is_mock,
    }
    return ScientificTaskArtifact(
        artifact_id=deterministic_id("scientific-task-artifact", values),
        **values,
    )


class ModelExecutionReceipt(StrictModel):
    task_id: Identifier
    status: Literal["SUCCEEDED", "FAILED", "BLOCKED"]
    verdict: ScientificEvidenceVerdict
    tested_claim_ids: tuple[Identifier, ...] = Field(min_length=1, max_length=128)
    evidence_level: ScientificEvidenceLevel
    result_artifact: ArtifactPointerV1 | None = None
    consumed_artifact_ids: tuple[Identifier, ...] = Field(default=(), max_length=128)
    produced_artifacts: tuple[ScientificTaskArtifact, ...] = Field(
        default=(), max_length=128
    )
    runtime_provenance: dict[Identifier, JsonValue] = Field(
        default_factory=dict, max_length=128
    )
    reason_codes: tuple[Identifier, ...] = Field(min_length=1, max_length=64)
    real_execution: bool

    @model_validator(mode="after")
    def validate_receipt(self) -> ModelExecutionReceipt:
        for name in ("tested_claim_ids", "consumed_artifact_ids", "reason_codes"):
            values = getattr(self, name)
            if values != tuple(sorted(set(values))):
                raise ValueError(f"{name} must be sorted and unique")
        artifact_ids = tuple(item.artifact_id for item in self.produced_artifacts)
        if artifact_ids != tuple(sorted(set(artifact_ids))):
            raise ValueError("produced task Artifacts must be sorted and unique")
        if self.runtime_provenance != dict(sorted(self.runtime_provenance.items())):
            raise ValueError("runtime provenance must use canonical key order")
        if self.status == "SUCCEEDED" and self.result_artifact is None:
            raise ValueError("successful execution requires a result artifact")
        if self.status != "SUCCEEDED" and self.verdict not in {
            ScientificEvidenceVerdict.EXECUTION_FAILED,
            ScientificEvidenceVerdict.INCONCLUSIVE,
        }:
            raise ValueError("non-successful execution cannot support or contradict")
        if (
            not self.real_execution
            and self.evidence_level is not ScientificEvidenceLevel.NONE
        ):
            raise ValueError("fixture/mock execution evidence level must remain NONE")
        return self


class ScientificEvidence(StrictModel):
    schema_version: Literal["scientific-validation-loop-v1"] = (
        SCIENTIFIC_LOOP_CONTRACT_VERSION
    )
    evidence_id: Identifier
    candidate_id: Identifier
    model_task_plan_id: Identifier
    task_id: Identifier
    model_id: Identifier
    task_kind: ScientificTaskKind
    execution_status: Literal["SUCCEEDED", "FAILED", "BLOCKED"]
    verdict: ScientificEvidenceVerdict
    tested_claim_ids: tuple[Identifier, ...]
    evidence_level: ScientificEvidenceLevel
    result_artifact: ArtifactPointerV1 | None = None
    consumed_artifact_ids: tuple[Identifier, ...] = ()
    produced_artifacts: tuple[ScientificTaskArtifact, ...] = ()
    runtime_provenance: dict[Identifier, JsonValue] = Field(default_factory=dict)
    reason_codes: tuple[Identifier, ...]
    real_execution: bool
    scientific_conclusion: Literal[False] = False

    @model_validator(mode="after")
    def validate_identity(self) -> ScientificEvidence:
        expected = deterministic_id(
            "scientific-evidence",
            self.model_dump(mode="python", exclude={"evidence_id"}),
        )
        if self.evidence_id != expected:
            raise ValueError("scientific evidence ID differs from canonical content")
        return self


def scientific_evidence_from_execution(
    plan: ModelTaskPlan,
    receipt: ModelExecutionReceipt,
    *,
    artifact_verifier: Callable[[ArtifactPointerV1], bool] | None = None,
) -> ScientificEvidence:
    audited = next(
        (item for item in plan.tasks if item.proposed_task.task_id == receipt.task_id),
        None,
    )
    if audited is None:
        raise ValueError("execution receipt task is not in the model plan")
    if audited.status is not RouteAuditStatus.APPROVED:
        raise ValueError("blocked model task cannot publish execution evidence")
    if (
        _EVIDENCE_RANK[receipt.evidence_level]
        > _EVIDENCE_RANK[audited.approved_evidence_ceiling]
    ):
        raise ValueError("execution evidence exceeds the audited model ceiling")
    task = audited.proposed_task
    if not set(receipt.tested_claim_ids) <= set(task.requested_observables):
        raise ValueError("execution receipt tested claims outside the frozen task")
    if receipt.real_execution:
        if artifact_verifier is None:
            raise ValueError("real execution evidence requires an artifact verifier")
        if receipt.result_artifact is None or not artifact_verifier(
            receipt.result_artifact
        ):
            raise ValueError("real execution result artifact failed verification")
        if any(
            not artifact_verifier(item.pointer) for item in receipt.produced_artifacts
        ):
            raise ValueError("real execution produced Artifact failed verification")
    if any(
        item.candidate_id != task.candidate_id or item.producer_task_id != task.task_id
        for item in receipt.produced_artifacts
    ):
        raise ValueError("produced Artifact task/candidate lineage mismatch")
    expected_kinds = set(task.produced_artifact_kinds)
    observed_kinds = {item.kind for item in receipt.produced_artifacts}
    if receipt.status == "SUCCEEDED" and expected_kinds != observed_kinds:
        raise ValueError("execution produced Artifact kinds differ from frozen task")
    if receipt.status != "SUCCEEDED" and receipt.produced_artifacts:
        raise ValueError("unsuccessful execution cannot publish task Artifacts")
    if receipt.real_execution and any(
        item.is_mock for item in receipt.produced_artifacts
    ):
        raise ValueError("real execution cannot publish mock task Artifacts")
    values = {
        "schema_version": SCIENTIFIC_LOOP_CONTRACT_VERSION,
        "candidate_id": task.candidate_id,
        "model_task_plan_id": plan.plan_id,
        "task_id": task.task_id,
        "model_id": task.model_id,
        "task_kind": task.task_kind,
        "execution_status": receipt.status,
        "verdict": receipt.verdict,
        "tested_claim_ids": receipt.tested_claim_ids,
        "evidence_level": receipt.evidence_level,
        "result_artifact": receipt.result_artifact,
        "consumed_artifact_ids": receipt.consumed_artifact_ids,
        "produced_artifacts": receipt.produced_artifacts,
        "runtime_provenance": receipt.runtime_provenance,
        "reason_codes": receipt.reason_codes,
        "real_execution": receipt.real_execution,
        "scientific_conclusion": False,
    }
    return ScientificEvidence(
        evidence_id=deterministic_id("scientific-evidence", values),
        **values,
    )


class TaskExecutionBinding(StrictModel):
    schema_version: Literal["scientific-validation-loop-v1"] = (
        SCIENTIFIC_LOOP_CONTRACT_VERSION
    )
    binding_id: Identifier
    model_task_plan_id: Identifier
    task_id: Identifier
    candidate_id: Identifier
    proposed_task: ProposedModelTask
    source_structure: ArtifactPointerV1
    prerequisite_evidence_ids: tuple[Identifier, ...]
    consumed_artifacts: tuple[ScientificTaskArtifact, ...]

    @model_validator(mode="after")
    def validate_identity(self) -> TaskExecutionBinding:
        if (
            self.proposed_task.task_id != self.task_id
            or self.proposed_task.candidate_id != self.candidate_id
        ):
            raise ValueError("execution binding task payload has inconsistent lineage")
        expected = deterministic_id(
            "task-execution-binding",
            self.model_dump(mode="python", exclude={"binding_id"}),
        )
        if self.binding_id != expected:
            raise ValueError("task execution binding ID differs from content")
        return self


def resolve_task_execution_binding(
    *,
    plan: ModelTaskPlan,
    task_id: str,
    prior_evidence: Sequence[ScientificEvidence],
) -> TaskExecutionBinding:
    """Bind a task to verified predecessor outputs instead of its original CIF.

    The planner may prove that a DAG is type-compatible, but only completed
    evidence can provide the actual immutable Artifact pointers consumed by a
    downstream task.
    """

    audited = next(
        (item for item in plan.tasks if item.proposed_task.task_id == task_id),
        None,
    )
    if audited is None:
        raise ValueError("task execution binding references an unknown task")
    if audited.status is not RouteAuditStatus.APPROVED:
        raise ValueError("blocked model task cannot receive an execution binding")
    if audited.input_structure is None:
        raise ValueError("approved model task lost its lineage structure")
    task = audited.proposed_task
    evidence_by_task: dict[str, ScientificEvidence] = {}
    for item in prior_evidence:
        if item.model_task_plan_id != plan.plan_id:
            raise ValueError("prior evidence belongs to a different model task plan")
        if item.task_id in evidence_by_task:
            raise ValueError(
                "multiple evidence records exist for one prerequisite task"
            )
        evidence_by_task[item.task_id] = item

    prerequisite_records: list[ScientificEvidence] = []
    for prerequisite_id in task.prerequisite_task_ids:
        record = evidence_by_task.get(prerequisite_id)
        if record is None:
            raise ValueError("prerequisite evidence is unavailable")
        if record.candidate_id != task.candidate_id:
            raise ValueError("prerequisite evidence candidate mismatch")
        if record.execution_status != "SUCCEEDED":
            raise ValueError("unsuccessful prerequisite cannot feed a downstream task")
        prerequisite_records.append(record)

    consumed = tuple(
        sorted(
            (
                artifact
                for record in prerequisite_records
                for artifact in record.produced_artifacts
            ),
            key=lambda item: item.artifact_id,
        )
    )
    if any(item.is_mock for item in consumed):
        raise ValueError("mock Artifact cannot feed the scientific execution DAG")
    available_kinds = {item.kind for item in consumed}
    if not set(task.required_input_artifact_kinds) <= available_kinds:
        raise ValueError("required predecessor Artifact kind is unavailable")
    relaxed_structures = tuple(
        item
        for item in consumed
        if item.kind is ScientificArtifactKind.RELAXED_STRUCTURE
    )
    if len(relaxed_structures) > 1:
        raise ValueError("multiple relaxed structures make task input ambiguous")
    bound_structure = (
        relaxed_structures[0].pointer if relaxed_structures else audited.input_structure
    )
    values = {
        "schema_version": SCIENTIFIC_LOOP_CONTRACT_VERSION,
        "model_task_plan_id": plan.plan_id,
        "task_id": task.task_id,
        "candidate_id": task.candidate_id,
        "proposed_task": task,
        "source_structure": bound_structure,
        "prerequisite_evidence_ids": tuple(
            sorted(item.evidence_id for item in prerequisite_records)
        ),
        "consumed_artifacts": consumed,
    }
    return TaskExecutionBinding(
        binding_id=deterministic_id("task-execution-binding", values),
        **values,
    )


class DeepSeekFeedbackItem(StrictModel):
    candidate_id: Identifier
    action: FeedbackAction
    evidence_ids: tuple[Identifier, ...] = Field(min_length=1, max_length=128)
    rationale: str = Field(min_length=10, max_length=2_000)
    operator_revision: CompileReasonedOperationArgsV3 | None = None
    escalation_tasks: tuple[ProposedModelTask, ...] = Field(default=(), max_length=32)

    @model_validator(mode="after")
    def validate_action_payload(self) -> DeepSeekFeedbackItem:
        if self.evidence_ids != tuple(sorted(set(self.evidence_ids))):
            raise ValueError("feedback evidence IDs must be sorted and unique")
        modify = self.action is FeedbackAction.MODIFY_OPERATOR
        escalate = self.action is FeedbackAction.REQUEST_HIGHER_EVIDENCE
        if modify != (self.operator_revision is not None):
            raise ValueError(
                "MODIFY_OPERATOR requires exactly one typed operator revision"
            )
        if escalate != bool(self.escalation_tasks):
            raise ValueError("REQUEST_HIGHER_EVIDENCE requires escalation tasks")
        return self


class DeepSeekFeedbackDraft(StrictModel):
    decisions: tuple[DeepSeekFeedbackItem, ...] = Field(min_length=1, max_length=256)
    cycle_summary: str = Field(min_length=10, max_length=4_000)


class DeepSeekFeedbackProposal(DeepSeekFeedbackDraft):
    schema_version: Literal["scientific-validation-loop-v1"] = (
        SCIENTIFIC_LOOP_CONTRACT_VERSION
    )
    provenance: DeepSeekReasoningProvenance
    scientific_conclusion: Literal[False] = False

    @model_validator(mode="after")
    def unique_candidate_decisions(self) -> DeepSeekFeedbackProposal:
        candidate_ids = tuple(item.candidate_id for item in self.decisions)
        if len(candidate_ids) != len(set(candidate_ids)):
            raise ValueError("feedback requires exactly one action per candidate")
        return self


class AuditedFeedbackDecision(StrictModel):
    proposal: DeepSeekFeedbackItem
    status: Literal["ACCEPTED", "REJECTED"]
    reason_codes: tuple[Identifier, ...] = Field(min_length=1, max_length=32)


class FeedbackCycleResult(StrictModel):
    schema_version: Literal["scientific-validation-loop-v1"] = (
        SCIENTIFIC_LOOP_CONTRACT_VERSION
    )
    cycle_id: Identifier
    memory_snapshot_id: Identifier
    feedback_proposal_sha256: Sha256
    decisions: tuple[AuditedFeedbackDecision, ...] = Field(min_length=1, max_length=256)
    scientific_conclusion: Literal[False] = False

    @model_validator(mode="after")
    def validate_identity(self) -> FeedbackCycleResult:
        expected = deterministic_id(
            "feedback-cycle",
            self.model_dump(mode="python", exclude={"cycle_id"}),
        )
        if self.cycle_id != expected:
            raise ValueError("feedback cycle ID differs from canonical content")
        return self


def audit_feedback_cycle(
    *,
    hypotheses: Sequence[HypothesisCandidate],
    evidence: Sequence[ScientificEvidence],
    memory_snapshot: InspirationMemorySnapshotV1,
    proposal: DeepSeekFeedbackProposal,
    allow_non_live_reasoner: bool = False,
    capabilities: Sequence[ModelCapability] = (),
    policy: ValidationRoutePolicy | None = None,
) -> FeedbackCycleResult:
    """Accept only feedback that cites evidence already written to memory."""

    candidates = {item.candidate_id: item for item in hypotheses}
    evidence_by_id = {item.evidence_id: item for item in evidence}
    remembered = {
        item.evidence_id
        for item in memory_snapshot.downstream_outcomes
        if item.evidence_id is not None
    }
    capability_by_id = {item.model_id: item for item in capabilities}
    if len(capability_by_id) != len(capabilities):
        raise ValueError("feedback capability IDs must be unique")
    decisions: list[AuditedFeedbackDecision] = []
    for item in proposal.decisions:
        reasons: set[str] = set()
        accepted_reason = "DEEPSEEK_FEEDBACK_PASSED_EVIDENCE_AUDIT"
        if not proposal.provenance.live_call and not allow_non_live_reasoner:
            reasons.add("LIVE_DEEPSEEK_FEEDBACK_REQUIRED")
        if item.candidate_id not in candidates:
            reasons.add("UNKNOWN_HYPOTHESIS_CANDIDATE")
        cited = [evidence_by_id.get(identifier) for identifier in item.evidence_ids]
        if any(record is None for record in cited):
            reasons.add("UNKNOWN_SCIENTIFIC_EVIDENCE")
        concrete = [record for record in cited if record is not None]
        if any(record.candidate_id != item.candidate_id for record in concrete):
            reasons.add("EVIDENCE_CANDIDATE_MISMATCH")
        if not set(item.evidence_ids) <= remembered:
            reasons.add("EVIDENCE_NOT_WRITTEN_TO_RESEARCH_MEMORY")
        if item.action is FeedbackAction.ELIMINATE:
            real_l2_contradiction = any(
                record.verdict is ScientificEvidenceVerdict.CONTRADICTS
                and record.real_execution
                and _EVIDENCE_RANK[record.evidence_level] >= 2
                for record in concrete
            )
            speed_first_contradiction = (
                policy is not None
                and policy.allow_speed_first_real_ml_elimination
                and any(
                    record.verdict is ScientificEvidenceVerdict.CONTRADICTS
                    and record.real_execution
                    and bool(
                        set(record.reason_codes)
                        & set(policy.speed_first_elimination_reason_codes)
                    )
                    for record in concrete
                )
            )
            if not real_l2_contradiction and not speed_first_contradiction:
                reasons.add("ELIMINATION_REQUIRES_REAL_L2_CONTRADICTION")
            elif speed_first_contradiction and not real_l2_contradiction:
                accepted_reason = "SPEED_FIRST_REAL_ML_SEARCH_POOL_ELIMINATION_ACCEPTED"
        if item.action is FeedbackAction.MODIFY_OPERATOR:
            revision = item.operator_revision
            if revision is not None and revision.candidate_id != item.candidate_id:
                reasons.add("OPERATOR_REVISION_CANDIDATE_MISMATCH")
        if item.action is FeedbackAction.REQUEST_HIGHER_EVIDENCE:
            current_rank = max(
                (_EVIDENCE_RANK[record.evidence_level] for record in concrete),
                default=0,
            )
            if not any(
                _EVIDENCE_RANK[task.required_evidence_level] > current_rank
                for task in item.escalation_tasks
            ):
                reasons.add("ESCALATION_MUST_REQUEST_HIGHER_EVIDENCE")
            if any(
                task.candidate_id != item.candidate_id for task in item.escalation_tasks
            ):
                reasons.add("ESCALATION_TASK_CANDIDATE_MISMATCH")
            for task in item.escalation_tasks:
                if policy is not None and (
                    task.task_kind in _ML_ONLY_FORBIDDEN_TASKS
                    or PhysicsCapabilityFeature.SELF_CONSISTENT_TOTAL_ENERGY
                    in task.required_capability_features
                ):
                    reasons.add("FEEDBACK_DFT_DISABLED_BY_ML_ONLY_MODE")
                if policy is not None and (
                    _EVIDENCE_RANK[task.required_evidence_level]
                    > _EVIDENCE_RANK[ScientificEvidenceLevel.L2_ML_SCREENED]
                ):
                    reasons.add("FEEDBACK_ML_ONLY_EVIDENCE_CEILING_EXCEEDED")
                if capabilities:
                    capability = capability_by_id.get(task.model_id)
                    if capability is None:
                        reasons.add("ESCALATION_MODEL_CAPABILITY_NOT_REGISTERED")
                        continue
                    if capability.availability is not CapabilityAvailability.READY:
                        reasons.add("ESCALATION_MODEL_BACKEND_NOT_READY")
                    if capability.weight_status in {
                        WeightStatus.MISSING,
                        WeightStatus.UNVERIFIED,
                    }:
                        reasons.add("ESCALATION_MODEL_WEIGHT_NOT_READY")
                    if not set(task.requested_observables) <= set(
                        capability.supported_observables
                    ):
                        reasons.add("ESCALATION_OBSERVABLE_UNSUPPORTED")
                    if (
                        task.task_kind is not ScientificTaskKind.GENERIC_MODEL_INFERENCE
                        and task.task_kind not in capability.supported_task_kinds
                    ):
                        reasons.add("ESCALATION_TASK_KIND_UNSUPPORTED")
                    if not set(task.required_capability_features) <= set(
                        capability.physics_features
                    ):
                        reasons.add("ESCALATION_PHYSICS_FEATURE_UNAVAILABLE")
                    if (
                        _EVIDENCE_RANK[task.required_evidence_level]
                        > _EVIDENCE_RANK[capability.evidence_ceiling]
                    ):
                        reasons.add("ESCALATION_EVIDENCE_CEILING_TOO_LOW")
                    if (
                        policy is not None
                        and policy.require_verified_benchmark_for_l2
                        and _EVIDENCE_RANK[task.required_evidence_level] >= 2
                        and capability.benchmark_status != "VALIDATED"
                    ):
                        reasons.add("ESCALATION_BENCHMARK_NOT_VALIDATED")
        decisions.append(
            AuditedFeedbackDecision(
                proposal=item,
                status="REJECTED" if reasons else "ACCEPTED",
                reason_codes=(
                    tuple(sorted(reasons)) if reasons else (accepted_reason,)
                ),
            )
        )
    values = {
        "schema_version": SCIENTIFIC_LOOP_CONTRACT_VERSION,
        "memory_snapshot_id": memory_snapshot.snapshot_id,
        "feedback_proposal_sha256": canonical_sha256(proposal),
        "decisions": tuple(decisions),
        "scientific_conclusion": False,
    }
    return FeedbackCycleResult(
        cycle_id=deterministic_id("feedback-cycle", values),
        **values,
    )


def write_evidence_to_research_memory(
    *,
    project_id: str,
    source_run_id: str,
    evidence: ScientificEvidence,
    artifact_store: LocalArtifactStore,
    memory_store: InspirationMemoryStore,
) -> InspirationMemorySnapshotV1:
    """Persist evidence first, then append one hash-linked downstream event."""

    if evidence.result_artifact is not None:
        try:
            observed = artifact_store.inspect(evidence.result_artifact.uri)
        except (FileNotFoundError, ValueError) as exc:
            raise ValueError(
                "scientific evidence result artifact is unavailable"
            ) from exc
        if observed.sha256 != evidence.result_artifact.sha256:
            raise ValueError("scientific evidence result artifact hash mismatch")
    ref = artifact_store.write_json(
        f"scientific_loop/{source_run_id}/evidence/{evidence.evidence_id}.json",
        evidence.model_dump(mode="json"),
        immutable=True,
    )
    pointer = ArtifactPointerV1(
        uri=ref.uri,
        sha256=ref.sha256,
        size_bytes=ref.size_bytes,
        media_type=ref.media_type,
    )
    payload = DownstreamOutcomeMemoryV1(
        candidate_id=evidence.candidate_id,
        stage=DownstreamMemoryStage.MODEL_VALIDATION,
        outcome=evidence.verdict.value,
        evidence_level=evidence.evidence_level.value,
        model_id=evidence.model_id,
        model_task_id=evidence.task_id,
        evidence_id=evidence.evidence_id,
        reason_codes=evidence.reason_codes,
        source_artifact_sha256=pointer.sha256,
    )
    memory_store.append(
        make_memory_event_v1(
            project_id=project_id,
            source_run_id=source_run_id,
            context_id=evidence.model_task_plan_id,
            source_artifact=pointer,
            payload=payload,
        )
    )
    return memory_store.snapshot(project_id)


class _InspectCapabilitiesArgs(StrictModel):
    """The complete bounded snapshot is returned without caller-selected IDs."""


class _InspectEvidenceArgs(StrictModel):
    """The complete bounded cycle evidence is returned without caller-selected IDs."""


class ScientificLoopReasoner(Protocol):
    def propose_route(
        self,
        *,
        goal: str,
        hypotheses: Sequence[HypothesisCandidate],
        operator_results: Sequence[OperatorResult],
        capabilities: Sequence[ModelCapability],
    ) -> DeepSeekModelRouteProposal: ...

    def propose_feedback(
        self,
        *,
        goal: str,
        hypotheses: Sequence[HypothesisCandidate],
        evidence: Sequence[ScientificEvidence],
        capabilities: Sequence[ModelCapability],
    ) -> DeepSeekFeedbackProposal: ...


class ScientificValidationLoopService:
    """Small orchestrator for one repeatable route→evidence→feedback cycle."""

    def __init__(
        self,
        *,
        project_id: str,
        source_run_id: str,
        goal: str,
        hypotheses: Sequence[HypothesisCandidate],
        capabilities: Sequence[ModelCapability],
        policy: ValidationRoutePolicy,
        artifact_store: LocalArtifactStore,
        memory_store: InspirationMemoryStore,
        reasoner: ScientificLoopReasoner,
    ) -> None:
        if not hypotheses:
            raise ValueError("scientific validation loop requires hypotheses")
        if not capabilities:
            raise ValueError("scientific validation loop requires capabilities")
        self.project_id = project_id
        self.source_run_id = source_run_id
        self.goal = goal
        self.hypotheses = tuple(hypotheses)
        self.capabilities = tuple(capabilities)
        self.policy = policy
        self.artifact_store = artifact_store
        self.memory_store = memory_store
        self.reasoner = reasoner

    def propose_and_audit_route(
        self,
        operator_results: Sequence[OperatorResult],
    ) -> tuple[DeepSeekModelRouteProposal, ModelTaskPlan]:
        proposal = self.reasoner.propose_route(
            goal=self.goal,
            hypotheses=self.hypotheses,
            operator_results=operator_results,
            capabilities=self.capabilities,
        )
        plan = compile_model_task_plan(
            goal=self.goal,
            hypotheses=self.hypotheses,
            operator_results=operator_results,
            proposal=proposal,
            capabilities=self.capabilities,
            policy=self.policy,
        )
        route_sha256 = canonical_sha256(proposal)
        self.artifact_store.write_json(
            f"scientific_loop/{self.source_run_id}/routes/{route_sha256}.json",
            proposal.model_dump(mode="json"),
            immutable=True,
        )
        self.artifact_store.write_json(
            f"scientific_loop/{self.source_run_id}/plans/{plan.plan_id}.json",
            plan.model_dump(mode="json"),
            immutable=True,
        )
        return proposal, plan

    def record_execution(
        self,
        plan: ModelTaskPlan,
        receipt: ModelExecutionReceipt,
    ) -> tuple[ScientificEvidence, InspirationMemorySnapshotV1]:
        evidence = scientific_evidence_from_execution(
            plan,
            receipt,
            artifact_verifier=self._verify_artifact,
        )
        snapshot = write_evidence_to_research_memory(
            project_id=self.project_id,
            source_run_id=self.source_run_id,
            evidence=evidence,
            artifact_store=self.artifact_store,
            memory_store=self.memory_store,
        )
        return evidence, snapshot

    def bind_task_execution(
        self,
        plan: ModelTaskPlan,
        task_id: str,
        prior_evidence: Sequence[ScientificEvidence],
    ) -> TaskExecutionBinding:
        binding = resolve_task_execution_binding(
            plan=plan,
            task_id=task_id,
            prior_evidence=prior_evidence,
        )
        self.artifact_store.write_json(
            (
                f"scientific_loop/{self.source_run_id}/bindings/"
                f"{binding.binding_id}.json"
            ),
            binding.model_dump(mode="json"),
            immutable=True,
        )
        return binding

    def propose_and_audit_feedback(
        self,
        evidence: Sequence[ScientificEvidence],
    ) -> tuple[DeepSeekFeedbackProposal, FeedbackCycleResult]:
        proposal = self.reasoner.propose_feedback(
            goal=self.goal,
            hypotheses=self.hypotheses,
            evidence=evidence,
            capabilities=self.capabilities,
        )
        cycle = audit_feedback_cycle(
            hypotheses=self.hypotheses,
            evidence=evidence,
            memory_snapshot=self.memory_store.snapshot(self.project_id),
            proposal=proposal,
            capabilities=self.capabilities,
            policy=self.policy,
        )
        feedback_sha256 = canonical_sha256(proposal)
        self.artifact_store.write_json(
            (f"scientific_loop/{self.source_run_id}/feedback/{feedback_sha256}.json"),
            proposal.model_dump(mode="json"),
            immutable=True,
        )
        self.artifact_store.write_json(
            f"scientific_loop/{self.source_run_id}/cycles/{cycle.cycle_id}.json",
            cycle.model_dump(mode="json"),
            immutable=True,
        )
        return proposal, cycle

    def _verify_artifact(self, pointer: ArtifactPointerV1) -> bool:
        try:
            observed = self.artifact_store.inspect(pointer.uri)
        except (FileNotFoundError, ValueError):
            return False
        return observed.sha256 == pointer.sha256 and (
            pointer.size_bytes is None or pointer.size_bytes == observed.size_bytes
        )


class DeepSeekScientificLoopReasoner:
    """Two real DeepSeek reasoning calls with strictly local inspection tools."""

    def __init__(
        self,
        *,
        secret_resolver: SecretResolver,
        reasoning_effort: Literal["high", "max"] = "high",
        budget: DeepSeekAgentBudgetV1 | None = None,
        agent_factory: Callable[..., DeepSeekThinkingAgent] = DeepSeekThinkingAgent,
        live_call: bool = True,
    ) -> None:
        self.secret_resolver = secret_resolver
        self.reasoning_effort = reasoning_effort
        self.budget = budget or DeepSeekAgentBudgetV1(
            max_rounds=6,
            max_tool_calls=12,
            max_completion_tokens_per_round=32_768,
            max_total_tokens=120_000,
            max_walltime_seconds=900,
        )
        self.agent_factory = agent_factory
        self.live_call = live_call

    def propose_route(
        self,
        *,
        goal: str,
        hypotheses: Sequence[HypothesisCandidate],
        operator_results: Sequence[OperatorResult],
        capabilities: Sequence[ModelCapability],
    ) -> DeepSeekModelRouteProposal:
        capability_payload = tuple(
            item.model_dump(mode="json") for item in capabilities
        )
        non_scf_graph_ready = any(
            ScientificTaskKind.NON_SCF_ATOMIC_BASIS_GRAPH_PREPARATION
            in item.supported_task_kinds
            and item.availability is CapabilityAvailability.READY
            for item in capabilities
        )
        graph_boundary = (
            "A listed capability can prepare atomic-basis non-SCF H0/overlap "
            "graphs from the exact structure; this is allowed, but it is not "
            "self-consistent DFT evidence."
            if non_scf_graph_ready
            else (
                "Runtime overlap generation and electronic-graph generation are "
                "unavailable; only trusted precomputed electronic inputs may be used."
            )
        )

        def inspect(arguments: _InspectCapabilitiesArgs) -> Mapping[str, object]:
            return {
                "candidate_ids": sorted(item.candidate_id for item in hypotheses),
                "capabilities": capability_payload,
                "policy_boundary": (
                    "This Hermes installation is strictly ML_ONLY: runtime DFT, "
                    f"self-consistent DFT and DFT fallback are unavailable. {graph_boundary} "
                    "Within that boundary, local policy "
                    "only audits applicability, budget, weights and evidence "
                    "ceilings; choose the scientific route yourself."
                ),
            }

        agent = self.agent_factory(
            secret_resolver=self.secret_resolver,
            tools=(
                DeepSeekFunctionTool(
                    name="inspect_validation_capabilities",
                    description=(
                        "Inspect runtime model/calculation capabilities and evidence "
                        "ceilings before proposing a scientific validation route."
                    ),
                    arguments_model=_InspectCapabilitiesArgs,
                    handler=inspect,
                    max_calls_per_run=1,
                ),
            ),
            budget=self.budget,
            reasoning_effort=self.reasoning_effort,
            timeout_seconds=900,
        )
        result = agent.run(
            system_prompt=(
                "You are the scientific validation director for materials discovery. "
                "Infer the calculations needed from the user's goal and the candidate "
                "mechanisms. You, not the deterministic policy, own model and observable "
                "selection. This installation is strictly ML-only: never propose DFT, "
                "a DFT fallback, or self-consistent electronic-structure calculation. "
                f"{graph_boundary} Call the no-argument "
                "capability tool, propose only model IDs it returns, and call that "
                "inspection tool exactly once. "
                "When a capability declares a parameter_contract_id and parameter_schema, "
                "produce parameters that validate against that exact schema. "
                "For RESEARCH_EVIDENCE_IMPORT, copy the candidate's exact "
                "retrieved_evidence_bundle pointer into bundle_pointer; never invent or "
                "substitute an evidence Artifact. Route its typed output into "
                "DEEPSEEK_EVIDENCE_FUSION when that capability is listed. "
                "Every required_capability_features entry must be present in that "
                "capability's physics_features. If a requested scientific property is "
                "unsupported, leave it explicitly unresolved instead of requesting a "
                "feature the capability does not have. Use each hypothesis's "
                "transition_metal_elements for transition-metal contributor parameters, "
                "not all chemical elements. "
                "bind transformed candidates to operator results when available, order "
                "prerequisites, declare every task kind, typed predecessor input Artifact, "
                "typed output Artifact, required physics feature and canonical parameter "
                "set, and state decisive falsification rules. A downstream task must use "
                "PREVIOUS_TASK and consume actual typed outputs rather than reuse the "
                "original structure. Do not claim any "
                "property has been verified. Every tuple-like list must be sorted and "
                "duplicate-free unless route dependency order requires otherwise."
            ),
            user_payload={
                "goal": goal,
                "hypotheses": [item.model_dump(mode="json") for item in hypotheses],
                "operator_results": [
                    item.model_dump(mode="json") for item in operator_results
                ],
                "required_output_schema": DeepSeekModelRouteDraft.model_json_schema(),
            },
            prompt_version=SCIENTIFIC_LOOP_ROUTE_PROMPT_VERSION,
            final_model=DeepSeekModelRouteDraft,
            require_tool_call=True,
        )
        return DeepSeekModelRouteProposal(
            goal_sha256=canonical_sha256({"goal": goal}),
            provenance=reasoning_provenance(result.receipt, live_call=self.live_call),
            **result.final.model_dump(mode="python"),
        )

    def propose_feedback(
        self,
        *,
        goal: str,
        hypotheses: Sequence[HypothesisCandidate],
        evidence: Sequence[ScientificEvidence],
        capabilities: Sequence[ModelCapability],
    ) -> DeepSeekFeedbackProposal:
        evidence_by_id = {item.evidence_id: item for item in evidence}
        non_scf_graph_ready = any(
            ScientificTaskKind.NON_SCF_ATOMIC_BASIS_GRAPH_PREPARATION
            in item.supported_task_kinds
            and item.availability is CapabilityAvailability.READY
            for item in capabilities
        )
        capability_payload = tuple(
            item.model_dump(mode="json") for item in capabilities
        )

        def inspect(arguments: _InspectEvidenceArgs) -> Mapping[str, object]:
            return {
                "capabilities": capability_payload,
                "evidence": [
                    evidence_by_id[item].model_dump(mode="json")
                    for item in sorted(evidence_by_id)
                ],
                "policy_boundary": (
                    "Strictly ML_ONLY with an L2 evidence ceiling. Runtime DFT, "
                    "self-consistent DFT and DFT fallback are unavailable. "
                    + (
                        "Registered non-SCF atomic-basis graph preparation is available. "
                        if non_scf_graph_ready
                        else "Runtime electronic-graph preparation is unavailable. "
                    )
                    + "Escalation tasks must use only "
                    "listed model IDs and respect their typed inputs and ceilings."
                ),
            }

        agent = self.agent_factory(
            secret_resolver=self.secret_resolver,
            tools=(
                DeepSeekFunctionTool(
                    name="inspect_scientific_evidence",
                    description=(
                        "Inspect hash-bound downstream evidence before deciding whether "
                        "to retain, eliminate, modify, or escalate a candidate."
                    ),
                    arguments_model=_InspectEvidenceArgs,
                    handler=inspect,
                    max_calls_per_run=1,
                ),
            ),
            budget=self.budget,
            reasoning_effort=self.reasoning_effort,
            timeout_seconds=900,
        )
        result = agent.run(
            system_prompt=(
                "You are the materials-discovery feedback director. Call the no-argument "
                "evidence tool exactly once and decide candidate-by-candidate "
                "whether to retain, eliminate, "
                "modify the typed minimal operator, or request a higher-evidence model "
                "task. Cite only supplied evidence IDs. Treat mock, failed, NONE and "
                "inconclusive outputs as reasons to revise or escalate, never as proof. "
                "This installation has no DFT route or DFT fallback. Escalation may use "
                "another registered ML model or ensemble, retrieved experimental or "
                "literature evidence, or human review; never request DFT. For an "
                "escalation task, use only a model ID returned by the evidence tool and "
                "respect its supported observables, task kinds, typed inputs and evidence "
                "ceiling. "
                "This feedback participates in an iterative candidate search. When a "
                "hard failure is specific to the current structure but the material "
                "mechanism remains plausible, prefer MODIFY_OPERATOR and propose one "
                "typed minimal operation that directly targets the observed failure. "
                "Do not merely ELIMINATE every parent while search budget remains. Use "
                "ELIMINATE when the family mechanism itself is contradicted, and RETAIN "
                "only when the evidence justifies keeping the exact current structure. "
                "Intercalation into a monolayer periodic-vacuum cell is forbidden: it "
                "creates an isolated atom rather than a connected interlayer structure. "
                "Do not claim a property is verified. Evidence IDs and other set-like "
                "lists must be sorted and duplicate-free."
            ),
            user_payload={
                "goal": goal,
                "hypotheses": [item.model_dump(mode="json") for item in hypotheses],
                "available_evidence_ids": sorted(evidence_by_id),
                "required_output_schema": DeepSeekFeedbackDraft.model_json_schema(),
            },
            prompt_version=SCIENTIFIC_LOOP_FEEDBACK_PROMPT_VERSION,
            final_model=DeepSeekFeedbackDraft,
            require_tool_call=True,
        )
        return DeepSeekFeedbackProposal(
            provenance=reasoning_provenance(result.receipt, live_call=self.live_call),
            **result.final.model_dump(mode="python"),
        )
