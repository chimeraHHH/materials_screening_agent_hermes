"""Bounded candidate generation -> ML validation -> feedback search loop.

Scientific scope stays with the supplied reasoners.  This module does not know
which chemistry operator or model is scientifically appropriate; it only
checks lineage, immutable artifacts, real execution, the ML-only boundary and
the three search budgets before advancing another generation.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from enum import StrEnum
from typing import Literal, Protocol

from pydantic import Field, JsonValue, model_validator

from material_agent.inspiration.models import (
    Identifier,
    StrictModel,
    canonical_sha256,
    deterministic_id,
)
from material_agent.inspiration.research_memory import InspirationMemoryStore
from material_agent.integration.scientific_execution import (
    ScientificDAGRunResult,
    ScientificExecutorRegistry,
    execute_scientific_dag,
)
from material_agent.integration.scientific_loop import (
    DeepSeekReasoningProvenance,
    FeedbackAction,
    FeedbackCycleResult,
    HypothesisCandidate,
    ModelCapability,
    ModelTaskPlan,
    OperatorResult,
    OperatorResultStatus,
    ScientificEvidence,
    ScientificEvidenceLevel,
    ScientificEvidenceVerdict,
    ScientificExecutionMode,
    ScientificLoopReasoner,
    ScientificValidationLoopService,
    ValidationRoutePolicy,
)
from material_agent.orchestrator.llm import LLMProviderError
from material_agent.retrieval.storage import LocalArtifactStore

SCIENTIFIC_SEARCH_LOOP_VERSION = "scientific-candidate-search-loop-v1"


class ScientificSearchStopReason(StrEnum):
    CANDIDATE_FOUND = "CANDIDATE_FOUND"
    MAX_ITERATIONS_REACHED = "MAX_ITERATIONS_REACHED"
    MAX_CANDIDATES_REACHED = "MAX_CANDIDATES_REACHED"
    MAX_COST_REACHED = "MAX_COST_REACHED"
    NO_NOVEL_OPERATOR = "NO_NOVEL_OPERATOR"
    NO_VALID_GENERATED_CANDIDATES = "NO_VALID_GENERATED_CANDIDATES"
    CAPABILITY_GAP = "CAPABILITY_GAP"
    REASONING_FAILURE_AFTER_EVIDENCE = "REASONING_FAILURE_AFTER_EVIDENCE"


class CandidateSearchAction(StrEnum):
    CONTINUE = "CONTINUE"
    CANDIDATE_FOUND = "CANDIDATE_FOUND"
    BUDGET_EXHAUSTED = "BUDGET_EXHAUSTED"
    NO_NOVEL_OPERATOR = "NO_NOVEL_OPERATOR"
    CAPABILITY_GAP = "CAPABILITY_GAP"
    NO_VALID_GENERATED_CANDIDATES = "NO_VALID_GENERATED_CANDIDATES"


class CandidateQualificationStage(StrEnum):
    GENERATED_STRUCTURE_CANDIDATE = "GENERATED_STRUCTURE_CANDIDATE"
    ML_SCREENING_CANDIDATE = "ML_SCREENING_CANDIDATE"


class CandidateQualification(StrictModel):
    candidate_id: Identifier
    stage: CandidateQualificationStage
    evidence_ids: tuple[Identifier, ...] = ()
    unresolved_claim_ids: tuple[Identifier, ...] = ()
    reason_codes: tuple[Identifier, ...] = Field(min_length=1, max_length=64)

    @model_validator(mode="after")
    def canonical_sets(self) -> CandidateQualification:
        for name in ("evidence_ids", "unresolved_claim_ids", "reason_codes"):
            values = getattr(self, name)
            if values != tuple(sorted(set(values))):
                raise ValueError(f"{name} must be sorted and unique")
        if (
            self.stage is CandidateQualificationStage.ML_SCREENING_CANDIDATE
            and self.unresolved_claim_ids
        ):
            raise ValueError("ML screening candidate cannot retain unknown hard claims")
        return self


class SearchCampaignState(StrictModel):
    state_id: Identifier
    iteration: int = Field(ge=0)
    action: CandidateSearchAction
    frontier_candidate_ids: tuple[Identifier, ...]
    generated_structure_candidate_ids: tuple[Identifier, ...]
    ml_screening_candidate_ids: tuple[Identifier, ...]
    capability_gap_claim_ids: tuple[Identifier, ...] = ()
    total_candidate_count: int = Field(ge=1)
    total_cost_units: int = Field(ge=0)
    reason_codes: tuple[Identifier, ...] = Field(min_length=1, max_length=64)

    @model_validator(mode="after")
    def validate_identity(self) -> SearchCampaignState:
        for name in (
            "frontier_candidate_ids",
            "generated_structure_candidate_ids",
            "ml_screening_candidate_ids",
            "capability_gap_claim_ids",
            "reason_codes",
        ):
            values = getattr(self, name)
            if values != tuple(sorted(set(values))):
                raise ValueError(f"{name} must be sorted and unique")
        expected = deterministic_id(
            "search-campaign-state",
            self.model_dump(mode="python", exclude={"state_id"}),
        )
        if self.state_id != expected:
            raise ValueError("search campaign state ID differs from content")
        return self


def make_search_campaign_state(**values: object) -> SearchCampaignState:
    return SearchCampaignState(
        state_id=deterministic_id("search-campaign-state", values),
        **values,
    )


class ScientificSearchBudget(StrictModel):
    """Whole-search budgets; initial candidates count toward ``max_candidates``."""

    max_iterations: int = Field(default=8, ge=1, le=128)
    max_candidates: int = Field(default=64, ge=1, le=4_096)
    max_cost_units: int = Field(default=100_000, ge=1, le=100_000_000)


class ScientificOperatorProposal(StrictModel):
    """Opaque run-local operator request selected by native scientific reasoning.

    ``operator_id`` and ``parameters`` are deliberately not enumerated here.  A
    registry-aware executor owns their parameter model and applicability checks.
    """

    proposal_id: Identifier
    parent_candidate_id: Identifier
    operator_id: Identifier
    operator_spec_id: Identifier
    parameters: dict[Identifier, JsonValue] = Field(max_length=256)
    estimated_cost_units: int = Field(ge=0, le=10_000_000)
    scientific_rationale: str = Field(min_length=10, max_length=2_000)
    expected_mechanism: str = Field(min_length=5, max_length=2_000)
    decisive_falsification_test: str = Field(min_length=5, max_length=2_000)
    execution_mode: Literal[ScientificExecutionMode.ML_ONLY] = (
        ScientificExecutionMode.ML_ONLY
    )
    scientific_scope_source: Literal["DEEPSEEK_NATIVE_REASONING"] = (
        "DEEPSEEK_NATIVE_REASONING"
    )
    scientific_conclusion: Literal[False] = False

    @model_validator(mode="after")
    def validate_identity(self) -> ScientificOperatorProposal:
        if self.parameters != dict(sorted(self.parameters.items())):
            raise ValueError("operator parameters must use canonical key order")
        expected = deterministic_id(
            "scientific-operator-proposal",
            self.model_dump(mode="python", exclude={"proposal_id"}),
        )
        if self.proposal_id != expected:
            raise ValueError("scientific operator proposal ID differs from content")
        return self


def make_scientific_operator_proposal(**values: object) -> ScientificOperatorProposal:
    payload = {
        "execution_mode": ScientificExecutionMode.ML_ONLY,
        "scientific_scope_source": "DEEPSEEK_NATIVE_REASONING",
        "scientific_conclusion": False,
        **values,
    }
    return ScientificOperatorProposal(
        proposal_id=deterministic_id("scientific-operator-proposal", payload),
        **payload,
    )


class ScientificOperatorProposalBatch(StrictModel):
    batch_id: Identifier
    proposals: tuple[ScientificOperatorProposal, ...] = Field(max_length=512)
    rationale: str = Field(min_length=10, max_length=4_000)
    provenance: DeepSeekReasoningProvenance
    selection_recovery: Literal[
        "NONE", "ALL_NEW_COMPILED_STRUCTURE_PLANS_AFTER_REASONING_FAILURE"
    ] = "NONE"
    scientific_conclusion: Literal[False] = False

    @model_validator(mode="after")
    def validate_identity(self) -> ScientificOperatorProposalBatch:
        proposal_ids = tuple(item.proposal_id for item in self.proposals)
        if len(proposal_ids) != len(set(proposal_ids)):
            raise ValueError("operator proposal IDs must be unique")
        expected = deterministic_id(
            "scientific-operator-batch",
            self.model_dump(mode="python", exclude={"batch_id"}),
        )
        if self.batch_id != expected:
            raise ValueError("scientific operator batch ID differs from content")
        return self


def make_scientific_operator_batch(**values: object) -> ScientificOperatorProposalBatch:
    payload = {
        "selection_recovery": "NONE",
        "scientific_conclusion": False,
        **values,
    }
    return ScientificOperatorProposalBatch(
        batch_id=deterministic_id("scientific-operator-batch", payload),
        **payload,
    )


class CandidateGenerationResult(StrictModel):
    generation_id: Identifier
    proposal_id: Identifier
    operator_result: OperatorResult
    candidate: HypothesisCandidate | None = None
    charged_cost_units: int = Field(ge=0, le=10_000_000)
    real_execution: bool
    runtime_provenance: dict[Identifier, JsonValue] = Field(default_factory=dict)
    scientific_conclusion: Literal[False] = False

    @model_validator(mode="after")
    def validate_identity(self) -> CandidateGenerationResult:
        usable = self.operator_result.status in {
            OperatorResultStatus.STRUCTURE_VALID,
            OperatorResultStatus.REQUIRES_REVIEW,
        }
        if usable != (self.candidate is not None):
            raise ValueError("only a usable operator result may generate a candidate")
        if self.runtime_provenance != dict(sorted(self.runtime_provenance.items())):
            raise ValueError("runtime provenance must use canonical key order")
        expected = deterministic_id(
            "candidate-generation",
            self.model_dump(mode="python", exclude={"generation_id"}),
        )
        if self.generation_id != expected:
            raise ValueError("candidate generation ID differs from content")
        return self


def make_candidate_generation_result(**values: object) -> CandidateGenerationResult:
    payload = {"scientific_conclusion": False, **values}
    return CandidateGenerationResult(
        generation_id=deterministic_id("candidate-generation", payload),
        **payload,
    )


class CandidateOperatorProposer(Protocol):
    """Usually backed by DeepSeek native reasoning and a dynamic registry view."""

    def propose_operators(
        self,
        *,
        goal: str,
        iteration: int,
        hypotheses: Sequence[HypothesisCandidate],
        evidence: Sequence[ScientificEvidence],
        feedback: FeedbackCycleResult,
        memory_snapshot_id: str,
        remaining_candidate_slots: int,
        remaining_cost_units: int,
    ) -> ScientificOperatorProposalBatch: ...


class CandidateOperatorExecutor(Protocol):
    """Registry-aware local/ML structure executor; DFT modes are rejected."""

    @property
    def execution_mode(self) -> ScientificExecutionMode: ...

    def execute(
        self,
        *,
        proposal: ScientificOperatorProposal,
        parent: HypothesisCandidate,
    ) -> CandidateGenerationResult: ...


class ScientificSearchIteration(StrictModel):
    iteration: int = Field(ge=1)
    input_candidate_ids: tuple[Identifier, ...]
    operator_batch_id: Identifier
    generation_results: tuple[CandidateGenerationResult, ...]
    generated_candidate_ids: tuple[Identifier, ...]
    model_task_plan: ModelTaskPlan
    dag_run: ScientificDAGRunResult
    feedback: FeedbackCycleResult | None = None
    campaign_state: SearchCampaignState
    cumulative_cost_units: int = Field(ge=0)

    @model_validator(mode="after")
    def canonical_ids(self) -> ScientificSearchIteration:
        for name in ("input_candidate_ids", "generated_candidate_ids"):
            values = getattr(self, name)
            if values != tuple(sorted(set(values))):
                raise ValueError(f"{name} must be sorted and unique")
        if self.model_task_plan.plan_id != self.dag_run.model_task_plan_id:
            raise ValueError("iteration DAG result does not bind its model task plan")
        return self


class CandidateSearchResult(StrictModel):
    schema_version: Literal["scientific-candidate-search-loop-v1"] = (
        SCIENTIFIC_SEARCH_LOOP_VERSION
    )
    search_id: Identifier
    goal_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    stop_reason: ScientificSearchStopReason
    initial_candidate_ids: tuple[Identifier, ...]
    all_candidate_ids: tuple[Identifier, ...]
    retained_candidate_ids: tuple[Identifier, ...]
    capability_gap_claim_ids: tuple[Identifier, ...] = ()
    qualifications: tuple[CandidateQualification, ...]
    iterations: tuple[ScientificSearchIteration, ...]
    final_state: SearchCampaignState
    final_feedback_cycle_id: Identifier
    final_memory_snapshot_id: Identifier
    total_cost_units: int = Field(ge=0)
    scientific_conclusion: Literal[False] = False

    @model_validator(mode="after")
    def validate_identity(self) -> CandidateSearchResult:
        for name in (
            "initial_candidate_ids",
            "all_candidate_ids",
            "retained_candidate_ids",
            "capability_gap_claim_ids",
        ):
            values = getattr(self, name)
            if values != tuple(sorted(set(values))):
                raise ValueError(f"{name} must be sorted and unique")
        if not set(self.initial_candidate_ids) <= set(self.all_candidate_ids):
            raise ValueError("initial candidates must be included in all candidates")
        if not set(self.retained_candidate_ids) <= set(self.all_candidate_ids):
            raise ValueError("retained candidates must be included in all candidates")
        if bool(self.retained_candidate_ids) != (
            self.stop_reason is ScientificSearchStopReason.CANDIDATE_FOUND
        ):
            raise ValueError("retained stop reason and retained candidates differ")
        if self.retained_candidate_ids != self.final_state.ml_screening_candidate_ids:
            raise ValueError("result and final campaign state candidates differ")
        if self.capability_gap_claim_ids != self.final_state.capability_gap_claim_ids:
            raise ValueError("result and final campaign capability gaps differ")
        if bool(self.capability_gap_claim_ids) != (
            self.stop_reason is ScientificSearchStopReason.CAPABILITY_GAP
        ):
            raise ValueError("capability-gap stop requires explicit unresolved claims")
        expected = deterministic_id(
            "scientific-candidate-search",
            self.model_dump(mode="python", exclude={"search_id"}),
        )
        if self.search_id != expected:
            raise ValueError("scientific candidate search ID differs from content")
        return self


# Compatibility-oriented descriptive alias for callers that name the whole service.
ScientificSearchResult = CandidateSearchResult


class ScientificCandidateSearchLoop:
    """Iterate new structures until an accepted RETAIN or a hard budget stop."""

    def __init__(
        self,
        *,
        project_id: str,
        source_run_id: str,
        goal: str,
        capabilities: Sequence[ModelCapability],
        route_policy: ValidationRoutePolicy,
        search_budget: ScientificSearchBudget,
        artifact_store: LocalArtifactStore,
        memory_store: InspirationMemoryStore,
        scientific_reasoner: ScientificLoopReasoner,
        operator_proposer: CandidateOperatorProposer,
        operator_executor: CandidateOperatorExecutor,
        scientific_executors: ScientificExecutorRegistry,
        require_live_operator_reasoning: bool = True,
        require_real_operator_execution: bool = True,
    ) -> None:
        if route_policy.execution_mode is not ScientificExecutionMode.ML_ONLY:
            raise ValueError("candidate search loop is strictly ML_ONLY")
        if operator_executor.execution_mode is not ScientificExecutionMode.ML_ONLY:
            raise ValueError("candidate operator executor must be ML_ONLY")
        if not capabilities:
            raise ValueError("candidate search loop requires model capabilities")
        self.project_id = project_id
        self.source_run_id = source_run_id
        self.goal = goal
        self.capabilities = tuple(capabilities)
        self.route_policy = route_policy
        self.search_budget = search_budget
        self.artifact_store = artifact_store
        self.memory_store = memory_store
        self.scientific_reasoner = scientific_reasoner
        self.operator_proposer = operator_proposer
        self.operator_executor = operator_executor
        self.scientific_executors = scientific_executors
        self.require_live_operator_reasoning = require_live_operator_reasoning
        self.require_real_operator_execution = require_real_operator_execution

    def run(
        self,
        *,
        hypotheses: Sequence[HypothesisCandidate],
        evidence: Sequence[ScientificEvidence],
        feedback: FeedbackCycleResult,
    ) -> CandidateSearchResult:
        """Continue an already-audited feedback cycle with generated candidates."""

        if not hypotheses:
            raise ValueError(
                "candidate search requires at least one initial hypothesis"
            )
        candidate_by_id = {item.candidate_id: item for item in hypotheses}
        if len(candidate_by_id) != len(hypotheses):
            raise ValueError("initial candidate IDs must be unique")
        if len(candidate_by_id) > self.search_budget.max_candidates:
            raise ValueError("initial candidates exceed the whole-search budget")
        self._validate_feedback_scope(hypotheses, evidence, feedback)
        remembered_evidence_ids = {
            item.evidence_id
            for item in self.memory_store.snapshot(self.project_id).downstream_outcomes
            if item.evidence_id is not None
        }
        cited_evidence_ids = {
            evidence_id
            for decision in feedback.decisions
            for evidence_id in decision.proposal.evidence_ids
        }
        if not cited_evidence_ids <= remembered_evidence_ids:
            raise ValueError("initial feedback evidence is absent from research memory")

        initial_ids = tuple(sorted(candidate_by_id))
        current_hypotheses = tuple(hypotheses)
        current_evidence = tuple(evidence)
        current_feedback = feedback
        iterations: list[ScientificSearchIteration] = []
        qualifications: list[CandidateQualification] = []
        seen_operator_signatures: set[str] = set()
        capability_gap_claim_ids: tuple[str, ...] = ()
        total_cost = 0

        retained, initial_screening = self._qualified_retained_candidates(
            current_hypotheses,
            current_evidence,
            current_feedback,
        )
        qualifications.extend(initial_screening)
        if retained:
            return self._finish(
                stop_reason=ScientificSearchStopReason.CANDIDATE_FOUND,
                initial_ids=initial_ids,
                candidate_by_id=candidate_by_id,
                retained=retained,
                capability_gap_claim_ids=(),
                qualifications=qualifications,
                iterations=iterations,
                feedback=current_feedback,
                frontier_candidate_ids=tuple(
                    sorted(item.candidate_id for item in current_hypotheses)
                ),
                total_cost=total_cost,
            )

        stop_reason = ScientificSearchStopReason.MAX_ITERATIONS_REACHED
        for iteration_index in range(1, self.search_budget.max_iterations + 1):
            remaining_slots = self.search_budget.max_candidates - len(candidate_by_id)
            if remaining_slots <= 0:
                stop_reason = ScientificSearchStopReason.MAX_CANDIDATES_REACHED
                break
            remaining_cost = self.search_budget.max_cost_units - total_cost
            if remaining_cost <= 0:
                stop_reason = ScientificSearchStopReason.MAX_COST_REACHED
                break

            snapshot = self.memory_store.snapshot(self.project_id)
            batch = self.operator_proposer.propose_operators(
                goal=self.goal,
                iteration=iteration_index,
                hypotheses=current_hypotheses,
                evidence=current_evidence,
                feedback=current_feedback,
                memory_snapshot_id=snapshot.snapshot_id,
                remaining_candidate_slots=remaining_slots,
                remaining_cost_units=remaining_cost,
            )
            self._write_json("operator-batches", batch.batch_id, batch)
            recovered_live_compiler_plans = batch.selection_recovery == (
                "ALL_NEW_COMPILED_STRUCTURE_PLANS_AFTER_REASONING_FAILURE"
            )
            if (
                self.require_live_operator_reasoning
                and not batch.provenance.live_call
                and not recovered_live_compiler_plans
            ):
                raise ValueError("live DeepSeek operator reasoning is required")
            if not batch.proposals:
                stop_reason = ScientificSearchStopReason.NO_NOVEL_OPERATOR
                break

            novel_proposals = tuple(
                proposal
                for proposal in batch.proposals
                if self._operator_signature(proposal) not in seen_operator_signatures
            )
            if not novel_proposals:
                stop_reason = ScientificSearchStopReason.NO_NOVEL_OPERATOR
                break

            parent_by_id = {item.candidate_id: item for item in current_hypotheses}
            generation_results: list[CandidateGenerationResult] = []
            generated: list[HypothesisCandidate] = []
            budget_limited = False
            for proposal in novel_proposals:
                seen_operator_signatures.add(self._operator_signature(proposal))
                if len(generated) >= remaining_slots:
                    budget_limited = True
                    break
                parent = parent_by_id.get(proposal.parent_candidate_id)
                if parent is None:
                    raise ValueError(
                        "operator proposal references a non-current parent"
                    )
                if proposal.estimated_cost_units > (
                    self.search_budget.max_cost_units - total_cost
                ):
                    budget_limited = True
                    break
                result = self.operator_executor.execute(
                    proposal=proposal,
                    parent=parent,
                )
                self._validate_generation(proposal, parent, result, candidate_by_id)
                generation_results.append(result)
                total_cost += result.charged_cost_units
                self._write_json("generations", result.generation_id, result)
                if result.candidate is not None:
                    generated.append(result.candidate)
                    candidate_by_id[result.candidate.candidate_id] = result.candidate
                    qualifications.append(
                        CandidateQualification(
                            candidate_id=result.candidate.candidate_id,
                            stage=(
                                CandidateQualificationStage.GENERATED_STRUCTURE_CANDIDATE
                            ),
                            unresolved_claim_ids=result.candidate.unresolved_claims,
                            reason_codes=("REAL_OPERATOR_STRUCTURE_GENERATED",),
                        )
                    )

            if not generated:
                stop_reason = (
                    ScientificSearchStopReason.MAX_COST_REACHED
                    if budget_limited
                    else ScientificSearchStopReason.NO_VALID_GENERATED_CANDIDATES
                )
                break

            remaining_model_cost = self.search_budget.max_cost_units - total_cost
            if remaining_model_cost <= 0:
                stop_reason = ScientificSearchStopReason.MAX_COST_REACHED
                current_hypotheses = tuple(generated)
                break

            iteration_policy = self.route_policy.model_copy(
                update={
                    "max_candidates": min(
                        self.route_policy.max_candidates, len(generated)
                    ),
                    "max_cost_units": min(
                        self.route_policy.max_cost_units,
                        remaining_model_cost,
                    ),
                }
            )
            service = ScientificValidationLoopService(
                project_id=self.project_id,
                source_run_id=self.source_run_id,
                goal=self.goal,
                hypotheses=tuple(generated),
                capabilities=self.capabilities,
                policy=iteration_policy,
                artifact_store=self.artifact_store,
                memory_store=self.memory_store,
                reasoner=self.scientific_reasoner,
            )
            operator_results = tuple(
                result.operator_result
                for result in generation_results
                if result.candidate is not None
            )
            _route, plan = service.propose_and_audit_route(operator_results)
            route_cost_blocked = any(
                "ROUTE_COST_BUDGET_EXCEEDED" in task.reason_codes for task in plan.tasks
            )
            approved_claims_by_candidate: dict[str, set[str]] = {
                item.candidate_id: set() for item in generated
            }
            for audited in plan.tasks:
                if audited.status.value == "APPROVED":
                    approved_claims_by_candidate[
                        audited.proposed_task.candidate_id
                    ].update(audited.proposed_task.requested_observables)
            uncovered_claims = {
                item.candidate_id: tuple(
                    sorted(
                        set(item.unresolved_claims)
                        - approved_claims_by_candidate[item.candidate_id]
                    )
                )
                for item in generated
            }
            dag_run = execute_scientific_dag(
                plan=plan,
                service=service,
                executors=self.scientific_executors,
            )
            total_cost += plan.total_approved_cost_units
            if not dag_run.evidence:
                stop_reason = (
                    ScientificSearchStopReason.MAX_COST_REACHED
                    if route_cost_blocked
                    else ScientificSearchStopReason.CAPABILITY_GAP
                )
                current_hypotheses = tuple(generated)
                if not route_cost_blocked:
                    capability_gap_claim_ids = tuple(
                        sorted(
                            {
                                claim
                                for item in generated
                                for claim in item.unresolved_claims
                            }
                        )
                    )
                campaign_state = self._make_state(
                    iteration=iteration_index,
                    action=(
                        CandidateSearchAction.BUDGET_EXHAUSTED
                        if route_cost_blocked
                        else CandidateSearchAction.CAPABILITY_GAP
                    ),
                    frontier_candidate_ids=tuple(
                        sorted(item.candidate_id for item in generated)
                    ),
                    candidate_by_id=candidate_by_id,
                    qualifications=qualifications,
                    total_cost=total_cost,
                    reason_codes=(
                        "ROUTE_COST_BUDGET_EXCEEDED"
                        if route_cost_blocked
                        else "NO_APPROVED_OR_EXECUTABLE_MODEL_TASK",
                    ),
                    capability_gap_claim_ids=capability_gap_claim_ids,
                )
                iteration_record = ScientificSearchIteration(
                    iteration=iteration_index,
                    input_candidate_ids=tuple(sorted(parent_by_id)),
                    operator_batch_id=batch.batch_id,
                    generation_results=tuple(generation_results),
                    generated_candidate_ids=tuple(
                        sorted(item.candidate_id for item in generated)
                    ),
                    model_task_plan=plan,
                    dag_run=dag_run,
                    feedback=None,
                    campaign_state=campaign_state,
                    cumulative_cost_units=total_cost,
                )
                iterations.append(iteration_record)
                self._write_json(
                    "search-iterations",
                    f"iteration-{iteration_index:04d}",
                    iteration_record,
                )
                break
            try:
                _feedback_proposal, next_feedback = service.propose_and_audit_feedback(
                    dag_run.evidence
                )
            except LLMProviderError as error:
                stop_reason = (
                    ScientificSearchStopReason.REASONING_FAILURE_AFTER_EVIDENCE
                )
                current_hypotheses = tuple(generated)
                current_evidence = dag_run.evidence
                reason_code = f"DEEPSEEK_FEEDBACK_{error.category}"
                self.artifact_store.write_json(
                    (
                        f"scientific_loop/{self.source_run_id}/failures/"
                        f"feedback-iteration-{iteration_index:04d}.json"
                    ),
                    {
                        "category": error.category,
                        "iteration": iteration_index,
                        "reason_code": reason_code,
                        "retryable": error.retryable,
                        "scientific_conclusion": False,
                    },
                    immutable=True,
                )
                campaign_state = self._make_state(
                    iteration=iteration_index,
                    action=CandidateSearchAction.BUDGET_EXHAUSTED,
                    frontier_candidate_ids=tuple(
                        sorted(item.candidate_id for item in generated)
                    ),
                    candidate_by_id=candidate_by_id,
                    qualifications=qualifications,
                    total_cost=total_cost,
                    reason_codes=(reason_code,),
                )
                iteration_record = ScientificSearchIteration(
                    iteration=iteration_index,
                    input_candidate_ids=tuple(sorted(parent_by_id)),
                    operator_batch_id=batch.batch_id,
                    generation_results=tuple(generation_results),
                    generated_candidate_ids=tuple(
                        sorted(item.candidate_id for item in generated)
                    ),
                    model_task_plan=plan,
                    dag_run=dag_run,
                    feedback=None,
                    campaign_state=campaign_state,
                    cumulative_cost_units=total_cost,
                )
                iterations.append(iteration_record)
                self._write_json(
                    "search-iterations",
                    f"iteration-{iteration_index:04d}",
                    iteration_record,
                )
                break
            self._validate_feedback_scope(generated, dag_run.evidence, next_feedback)
            retained, screening_qualifications = self._qualified_retained_candidates(
                generated,
                dag_run.evidence,
                next_feedback,
            )
            qualifications.extend(screening_qualifications)
            has_complete_route = any(
                not uncovered_claims[item.candidate_id] for item in generated
            )
            if retained:
                iteration_action = CandidateSearchAction.CANDIDATE_FOUND
                iteration_reasons = ("REAL_ML_ALL_HARD_CLAIMS_RESOLVED",)
            elif not has_complete_route:
                if route_cost_blocked:
                    iteration_action = CandidateSearchAction.BUDGET_EXHAUSTED
                    iteration_reasons = ("ROUTE_COST_BUDGET_EXCEEDED",)
                else:
                    iteration_action = CandidateSearchAction.CAPABILITY_GAP
                    iteration_reasons = ("HARD_CLAIM_MODEL_COVERAGE_GAP",)
                    capability_gap_claim_ids = tuple(
                        sorted(
                            {
                                claim
                                for claims in uncovered_claims.values()
                                for claim in claims
                            }
                        )
                    )
            else:
                iteration_action = CandidateSearchAction.CONTINUE
                iteration_reasons = ("NO_ML_SCREENING_CANDIDATE_YET",)
            campaign_state = self._make_state(
                iteration=iteration_index,
                action=iteration_action,
                frontier_candidate_ids=tuple(
                    sorted(item.candidate_id for item in generated)
                ),
                candidate_by_id=candidate_by_id,
                qualifications=qualifications,
                total_cost=total_cost,
                reason_codes=iteration_reasons,
                capability_gap_claim_ids=capability_gap_claim_ids,
            )
            iteration_record = ScientificSearchIteration(
                iteration=iteration_index,
                input_candidate_ids=tuple(sorted(parent_by_id)),
                operator_batch_id=batch.batch_id,
                generation_results=tuple(generation_results),
                generated_candidate_ids=tuple(
                    sorted(item.candidate_id for item in generated)
                ),
                model_task_plan=plan,
                dag_run=dag_run,
                feedback=next_feedback,
                campaign_state=campaign_state,
                cumulative_cost_units=total_cost,
            )
            iterations.append(iteration_record)
            self._write_json(
                "search-iterations",
                f"iteration-{iteration_index:04d}",
                iteration_record,
            )
            current_hypotheses = tuple(generated)
            current_evidence = dag_run.evidence
            current_feedback = next_feedback
            if retained:
                stop_reason = ScientificSearchStopReason.CANDIDATE_FOUND
                break
            if not has_complete_route:
                stop_reason = (
                    ScientificSearchStopReason.MAX_COST_REACHED
                    if route_cost_blocked
                    else ScientificSearchStopReason.CAPABILITY_GAP
                )
                break
            if (
                budget_limited
                and len(candidate_by_id) >= self.search_budget.max_candidates
            ):
                stop_reason = ScientificSearchStopReason.MAX_CANDIDATES_REACHED
                break

        return self._finish(
            stop_reason=stop_reason,
            initial_ids=initial_ids,
            candidate_by_id=candidate_by_id,
            retained=(
                retained
                if stop_reason is ScientificSearchStopReason.CANDIDATE_FOUND
                else ()
            ),
            qualifications=qualifications,
            capability_gap_claim_ids=capability_gap_claim_ids,
            iterations=iterations,
            feedback=current_feedback,
            frontier_candidate_ids=tuple(
                sorted(item.candidate_id for item in current_hypotheses)
            ),
            total_cost=total_cost,
        )

    def _validate_generation(
        self,
        proposal: ScientificOperatorProposal,
        parent: HypothesisCandidate,
        result: CandidateGenerationResult,
        known_candidates: Mapping[str, HypothesisCandidate],
    ) -> None:
        if result.proposal_id != proposal.proposal_id:
            raise ValueError("operator execution result does not bind its proposal")
        if result.charged_cost_units > proposal.estimated_cost_units:
            raise ValueError("operator execution exceeded its reserved cost")
        if self.require_real_operator_execution and not result.real_execution:
            raise ValueError("real operator execution is required")
        operator = result.operator_result
        if operator.input_structure != parent.parent_structure:
            raise ValueError("operator result input structure differs from parent")
        candidate = result.candidate
        if candidate is None:
            return
        if candidate.candidate_id in known_candidates:
            raise ValueError("generated candidate ID already exists in the search")
        if operator.candidate_id != candidate.candidate_id:
            raise ValueError("operator result and generated candidate IDs differ")
        if operator.output_structure != candidate.parent_structure:
            raise ValueError(
                "generated candidate does not bind operator output structure"
            )

    @staticmethod
    def _accepted_retained_ids(feedback: FeedbackCycleResult) -> tuple[str, ...]:
        return tuple(
            sorted(
                decision.proposal.candidate_id
                for decision in feedback.decisions
                if decision.status == "ACCEPTED"
                and decision.proposal.action is FeedbackAction.RETAIN
            )
        )

    @classmethod
    def _qualified_retained_candidates(
        cls,
        hypotheses: Sequence[HypothesisCandidate],
        evidence: Sequence[ScientificEvidence],
        feedback: FeedbackCycleResult,
    ) -> tuple[tuple[str, ...], tuple[CandidateQualification, ...]]:
        """Promote RETAIN only after every hard claim has real positive evidence."""

        retained = set(cls._accepted_retained_ids(feedback))
        evidence_by_candidate: dict[str, list[ScientificEvidence]] = {
            item.candidate_id: [] for item in hypotheses
        }
        for item in evidence:
            evidence_by_candidate.setdefault(item.candidate_id, []).append(item)
        qualified_ids: list[str] = []
        qualifications: list[CandidateQualification] = []
        disqualifying_fragments = (
            "INCONCLUSIVE",
            "MISSING",
            "NOT_TESTED",
            "UNAVAILABLE",
            "UNKNOWN",
            "UNRESOLVED",
        )
        for hypothesis in hypotheses:
            if hypothesis.candidate_id not in retained:
                continue
            records = evidence_by_candidate.get(hypothesis.candidate_id, [])
            positive = [
                item
                for item in records
                if item.real_execution
                and item.execution_status == "SUCCEEDED"
                and item.verdict is ScientificEvidenceVerdict.SUPPORTS
                and item.evidence_level
                in {
                    ScientificEvidenceLevel.L1_RETRIEVED,
                    ScientificEvidenceLevel.L2_ML_SCREENED,
                }
                and not any(
                    fragment in reason.upper()
                    for reason in item.reason_codes
                    for fragment in disqualifying_fragments
                )
            ]
            supported_claims = {
                claim for item in positive for claim in item.tested_claim_ids
            }
            unresolved = tuple(
                sorted(set(hypothesis.unresolved_claims) - supported_claims)
            )
            adverse_claims = {
                claim
                for item in records
                if item.execution_status != "SUCCEEDED"
                or item.verdict is not ScientificEvidenceVerdict.SUPPORTS
                or not item.real_execution
                for claim in item.tested_claim_ids
            }
            if unresolved or adverse_claims & set(hypothesis.unresolved_claims):
                continue
            qualified_ids.append(hypothesis.candidate_id)
            qualifications.append(
                CandidateQualification(
                    candidate_id=hypothesis.candidate_id,
                    stage=CandidateQualificationStage.ML_SCREENING_CANDIDATE,
                    evidence_ids=tuple(sorted(item.evidence_id for item in positive)),
                    unresolved_claim_ids=(),
                    reason_codes=("REAL_ML_ALL_HARD_CLAIMS_RESOLVED",),
                )
            )
        return tuple(sorted(qualified_ids)), tuple(qualifications)

    @staticmethod
    def _operator_signature(proposal: ScientificOperatorProposal) -> str:
        return canonical_sha256(
            {
                "parent_candidate_id": proposal.parent_candidate_id,
                "operator_id": proposal.operator_id,
                "operator_spec_id": proposal.operator_spec_id,
                "parameters": proposal.parameters,
            }
        )

    @staticmethod
    def _validate_feedback_scope(
        hypotheses: Sequence[HypothesisCandidate],
        evidence: Sequence[ScientificEvidence],
        feedback: FeedbackCycleResult,
    ) -> None:
        candidate_ids = {item.candidate_id for item in hypotheses}
        evidence_by_id = {item.evidence_id: item for item in evidence}
        if len(evidence_by_id) != len(evidence):
            raise ValueError("scientific evidence IDs must be unique")
        for decision in feedback.decisions:
            proposal = decision.proposal
            if proposal.candidate_id not in candidate_ids:
                raise ValueError("feedback decision is outside the current generation")
            if not set(proposal.evidence_ids) <= set(evidence_by_id):
                raise ValueError(
                    "feedback cites evidence outside the current generation"
                )
            if any(
                evidence_by_id[item].candidate_id != proposal.candidate_id
                for item in proposal.evidence_ids
            ):
                raise ValueError("feedback evidence has inconsistent candidate lineage")

    def _finish(
        self,
        *,
        stop_reason: ScientificSearchStopReason,
        initial_ids: tuple[str, ...],
        candidate_by_id: Mapping[str, HypothesisCandidate],
        retained: tuple[str, ...],
        qualifications: Sequence[CandidateQualification],
        capability_gap_claim_ids: tuple[str, ...],
        iterations: Sequence[ScientificSearchIteration],
        feedback: FeedbackCycleResult,
        frontier_candidate_ids: tuple[str, ...],
        total_cost: int,
    ) -> CandidateSearchResult:
        final_action, final_reason_codes = self._final_action(stop_reason)
        final_state = self._make_state(
            iteration=len(iterations),
            action=final_action,
            frontier_candidate_ids=frontier_candidate_ids,
            candidate_by_id=candidate_by_id,
            qualifications=qualifications,
            total_cost=total_cost,
            reason_codes=final_reason_codes,
            capability_gap_claim_ids=capability_gap_claim_ids,
        )
        values = {
            "schema_version": SCIENTIFIC_SEARCH_LOOP_VERSION,
            "goal_sha256": canonical_sha256({"goal": self.goal}),
            "stop_reason": stop_reason,
            "initial_candidate_ids": initial_ids,
            "all_candidate_ids": tuple(sorted(candidate_by_id)),
            "retained_candidate_ids": tuple(sorted(retained)),
            "capability_gap_claim_ids": tuple(sorted(capability_gap_claim_ids)),
            "qualifications": tuple(
                sorted(
                    qualifications,
                    key=lambda item: (item.candidate_id, item.stage.value),
                )
            ),
            "iterations": tuple(iterations),
            "final_state": final_state,
            "final_feedback_cycle_id": feedback.cycle_id,
            "final_memory_snapshot_id": self.memory_store.snapshot(
                self.project_id
            ).snapshot_id,
            "total_cost_units": total_cost,
            "scientific_conclusion": False,
        }
        result = CandidateSearchResult(
            search_id=deterministic_id("scientific-candidate-search", values),
            **values,
        )
        self._write_json("search-results", result.search_id, result)
        return result

    @staticmethod
    def _final_action(
        stop_reason: ScientificSearchStopReason,
    ) -> tuple[CandidateSearchAction, tuple[str, ...]]:
        if stop_reason is ScientificSearchStopReason.CANDIDATE_FOUND:
            return (
                CandidateSearchAction.CANDIDATE_FOUND,
                ("REAL_ML_ALL_HARD_CLAIMS_RESOLVED",),
            )
        if stop_reason in {
            ScientificSearchStopReason.MAX_ITERATIONS_REACHED,
            ScientificSearchStopReason.MAX_CANDIDATES_REACHED,
            ScientificSearchStopReason.MAX_COST_REACHED,
            ScientificSearchStopReason.REASONING_FAILURE_AFTER_EVIDENCE,
        }:
            return CandidateSearchAction.BUDGET_EXHAUSTED, (stop_reason.value,)
        if stop_reason is ScientificSearchStopReason.NO_NOVEL_OPERATOR:
            return CandidateSearchAction.NO_NOVEL_OPERATOR, (stop_reason.value,)
        if stop_reason is ScientificSearchStopReason.CAPABILITY_GAP:
            return CandidateSearchAction.CAPABILITY_GAP, (stop_reason.value,)
        return (
            CandidateSearchAction.NO_VALID_GENERATED_CANDIDATES,
            (stop_reason.value,),
        )

    @staticmethod
    def _make_state(
        *,
        iteration: int,
        action: CandidateSearchAction,
        frontier_candidate_ids: tuple[str, ...],
        candidate_by_id: Mapping[str, HypothesisCandidate],
        qualifications: Sequence[CandidateQualification],
        total_cost: int,
        reason_codes: tuple[str, ...],
        capability_gap_claim_ids: tuple[str, ...] = (),
    ) -> SearchCampaignState:
        generated_ids = tuple(
            sorted(
                {
                    item.candidate_id
                    for item in qualifications
                    if item.stage
                    is CandidateQualificationStage.GENERATED_STRUCTURE_CANDIDATE
                }
            )
        )
        screened_ids = tuple(
            sorted(
                {
                    item.candidate_id
                    for item in qualifications
                    if item.stage is CandidateQualificationStage.ML_SCREENING_CANDIDATE
                }
            )
        )
        return make_search_campaign_state(
            iteration=iteration,
            action=action,
            frontier_candidate_ids=tuple(sorted(set(frontier_candidate_ids))),
            generated_structure_candidate_ids=generated_ids,
            ml_screening_candidate_ids=screened_ids,
            capability_gap_claim_ids=tuple(sorted(set(capability_gap_claim_ids))),
            total_candidate_count=len(candidate_by_id),
            total_cost_units=total_cost,
            reason_codes=tuple(sorted(set(reason_codes))),
        )

    def _write_json(self, collection: str, name: str, value: StrictModel) -> None:
        self.artifact_store.write_json(
            f"scientific_loop/{self.source_run_id}/{collection}/{name}.json",
            value.model_dump(mode="json"),
            immutable=True,
        )
