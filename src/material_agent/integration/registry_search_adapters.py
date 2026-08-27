"""Adapters from DeepSeek's operator compiler to the iterative search loop.

The adapters keep scientific scope in DeepSeek while enforcing a narrow local
boundary: only hash-pinned ``StructureOperationPlanV2`` objects can create a
new CIF.  Condition-only plans remain auditable proposals but can never be
misrepresented as generated structures.
"""

from __future__ import annotations

import hashlib
import warnings
from collections.abc import Callable, Mapping, Sequence
from typing import Literal

from pydantic import Field, model_validator
from pymatgen.core import Element, Structure

from material_agent.inspiration.deepseek_agent import (
    DeepSeekAgentBudgetV1,
    DeepSeekFunctionTool,
    DeepSeekThinkingAgent,
)
from material_agent.inspiration.models import (
    ArtifactPointerV1,
    Identifier,
    StrictModel,
    canonical_json_bytes,
    canonical_sha256,
    deterministic_id,
)
from material_agent.inspiration.operator_planning import (
    OperatorPlanningToolState,
    execution_request_from_compiled_operation_plan,
)
from material_agent.inspiration.research_graph import (
    DatabaseCandidateV1,
    DatabaseSourceRecordV1,
    RegisteredTransformationAuditV3,
)
from material_agent.integration.scientific_loop import (
    DeepSeekReasoningProvenance,
    FeedbackCycleResult,
    HypothesisCandidate,
    OperatorResultStatus,
    ScientificEvidence,
    ScientificExecutionMode,
    make_operator_result,
    operator_result_from_structure_execution,
    reasoning_provenance,
)
from material_agent.integration.scientific_search_loop import (
    CandidateGenerationResult,
    ScientificOperatorProposalBatch,
    make_candidate_generation_result,
    make_scientific_operator_batch,
    make_scientific_operator_proposal,
)
from material_agent.orchestrator.llm import LLMProviderError
from material_agent.retrieval.storage import LocalArtifactStore
from material_agent.retrieval.structures import calculate_dimensionality
from material_agent.softchem import (
    DEFAULT_SOFTCHEM_OPERATOR_REGISTRY_V2,
    SoftChemOperatorRegistryV2,
)
from material_agent.softchem.operations import (
    ReasonedConditionPlanV1,
    StructureOperationPlanV2,
    execute_registered_structure_operation,
)

REGISTRY_SEARCH_OPERATOR_PROMPT_VERSION = "registry-search-operator-deepseek-v1"
REGISTRY_SEARCH_ADAPTER_VERSION = "registry-search-adapters-v1"

DatabaseCandidateProvider = Callable[[], Sequence[DatabaseCandidateV1]]
DatabaseCandidateSource = Sequence[DatabaseCandidateV1] | DatabaseCandidateProvider


class DeepSeekCompiledPlanSelection(StrictModel):
    """DeepSeek's final selection from plans returned by the compiler tool."""

    selected_plan_ids: tuple[Identifier, ...] = Field(default=(), max_length=64)
    rationale: str = Field(min_length=10, max_length=4_000)
    scientific_conclusion: Literal[False] = False

    @model_validator(mode="after")
    def canonical_plan_ids(self) -> DeepSeekCompiledPlanSelection:
        if self.selected_plan_ids != tuple(sorted(set(self.selected_plan_ids))):
            raise ValueError("selected plan IDs must be sorted and unique")
        return self


class IterativeParentCatalog:
    """Merge initial database parents with generated, hash-bound derivatives.

    Generated records are compatibility views for the existing compiler input
    contract.  Their raw receipt explicitly records that they are Hermes
    derivatives, not newly retrieved database evidence.
    """

    def __init__(
        self,
        *,
        store: LocalArtifactStore,
        database_candidates: DatabaseCandidateSource,
    ) -> None:
        self.store = store
        self._source = database_candidates
        self._generated: dict[str, DatabaseCandidateV1] = {}

    def _initial(self) -> tuple[DatabaseCandidateV1, ...]:
        values = self._source() if callable(self._source) else self._source
        return tuple(values)

    def snapshot(self) -> tuple[DatabaseCandidateV1, ...]:
        merged = {
            item.database_candidate_id: item
            for item in (*self._initial(), *self._generated.values())
        }
        return tuple(
            sorted(merged.values(), key=lambda item: item.database_candidate_id)
        )

    @property
    def generated_candidates(self) -> tuple[DatabaseCandidateV1, ...]:
        return tuple(
            sorted(
                self._generated.values(), key=lambda item: item.database_candidate_id
            )
        )

    def find(self, database_candidate_id: str) -> DatabaseCandidateV1:
        match = next(
            (
                item
                for item in self.snapshot()
                if item.database_candidate_id == database_candidate_id
            ),
            None,
        )
        if match is None:
            raise ValueError(f"unknown parent catalog entry: {database_candidate_id}")
        return match

    def contains_structure_hash(self, sha256: str) -> bool:
        return any(item.structure_artifact_sha256 == sha256 for item in self.snapshot())

    def register_generated(
        self,
        *,
        execution_plan: StructureOperationPlanV2,
        output_structure: Structure,
        output_pointer: ArtifactPointerV1,
    ) -> DatabaseCandidateV1:
        parent = self.find(execution_plan.parent_candidate_id)
        elements = tuple(
            sorted(
                str(element.symbol) for element in output_structure.composition.elements
            )
        )
        transition_metals = tuple(
            sorted(symbol for symbol in elements if Element(symbol).is_transition_metal)
        )
        if not transition_metals:
            raise ValueError(
                "generated parent catalog currently requires a transition-metal element"
            )
        structure_id = execution_plan.output_structure_id
        if structure_id is None:
            raise ValueError("executed structure plan lacks a canonical structure ID")
        lineage = {
            "adapter_version": REGISTRY_SEARCH_ADAPTER_VERSION,
            "artifact_role": "GENERATED_DERIVATIVE_NOT_DATABASE_EVIDENCE",
            "parent_database_candidate_id": parent.database_candidate_id,
            "plan_id": execution_plan.plan_id,
            "operator_id": execution_plan.operator_id,
            "operator_spec_id": execution_plan.operator_spec.operator_spec_id,
            "route_sha256": execution_plan.route_sha256,
            "output_structure": output_pointer.model_dump(mode="json"),
            "scientific_conclusion": False,
        }
        database_candidate_id = deterministic_id("db-candidate", lineage)
        raw = self.store.write_json(
            f"scientific_search/generated_parents/{database_candidate_id}.json",
            lineage,
            immutable=True,
        )
        source_record = DatabaseSourceRecordV1(
            # DatabaseCandidateV1 has no GENERATED source discriminator.  Retain
            # the ancestor's source solely as a compiler-compatibility view;
            # the version and raw receipt make the derivative boundary explicit.
            source_database=parent.source_database,
            source_material_id=f"hermes-generated:{execution_plan.plan_id}",
            source_database_version="hermes-generated-derivative-v1",
            query_fingerprint=canonical_sha256(lineage),
            canonical_structure_id=structure_id,
            structure_artifact_uri=output_pointer.uri,
            structure_artifact_sha256=output_pointer.sha256,
            raw_response_artifact_uri=raw.uri,
            raw_response_artifact_sha256=raw.sha256,
            license=parent.source_records[0].license,
        )
        dimensionality = calculate_dimensionality(output_structure)
        result = DatabaseCandidateV1(
            database_candidate_id=database_candidate_id,
            source_database=parent.source_database,
            source_material_id=source_record.source_material_id,
            canonical_structure_id=structure_id,
            source_records=(source_record,),
            formula=output_structure.composition.reduced_formula,
            elements=elements,
            transition_metals=transition_metals,
            dimensionality=dimensionality.value
            if dimensionality.error is None
            else None,
            dimensionality_status=(
                "RESOLVED" if dimensionality.error is None else "UNKNOWN"
            ),
            connectivity_status="UNKNOWN",
            structure_artifact_uri=output_pointer.uri,
            structure_artifact_sha256=output_pointer.sha256,
            raw_response_artifact_uri=raw.uri,
            raw_response_artifact_sha256=raw.sha256,
        )
        prior = self._generated.get(database_candidate_id)
        if prior is not None and prior != result:
            raise ValueError("generated parent ID collision")
        self._generated[database_candidate_id] = result
        return result


class DeepSeekRegistryOperatorProposer:
    """Use live DeepSeek reasoning plus the hash-pinned compiler tool."""

    def __init__(
        self,
        *,
        store: LocalArtifactStore,
        database_candidates: DatabaseCandidateSource,
        secret_resolver: object,
        parent_catalog: IterativeParentCatalog | None = None,
        planning_state: OperatorPlanningToolState | None = None,
        reasoning_effort: Literal["high", "max"] = "high",
        budget: DeepSeekAgentBudgetV1 | None = None,
        agent_factory: Callable[..., DeepSeekThinkingAgent] = DeepSeekThinkingAgent,
        estimated_cost_units_per_operation: int = 1,
        max_plans_per_reasoning: int = 4,
        live_call: bool = True,
    ) -> None:
        if estimated_cost_units_per_operation < 0:
            raise ValueError("estimated operator cost must be non-negative")
        if not 1 <= max_plans_per_reasoning <= 32:
            raise ValueError("max_plans_per_reasoning must be between 1 and 32")
        self.store = store
        self.parent_catalog = parent_catalog or IterativeParentCatalog(
            store=store, database_candidates=database_candidates
        )
        self.planning_state = planning_state or OperatorPlanningToolState(
            store=store,
            database_candidates_snapshot=self.parent_catalog.snapshot,
            max_calls=32,
        )
        self.secret_resolver = secret_resolver
        self.reasoning_effort = reasoning_effort
        self.budget = budget or DeepSeekAgentBudgetV1(
            max_rounds=8,
            max_tool_calls=24,
            max_completion_tokens_per_round=32_768,
            max_total_tokens=160_000,
            max_walltime_seconds=900,
        )
        self.agent_factory = agent_factory
        self.estimated_cost_units_per_operation = estimated_cost_units_per_operation
        self.max_plans_per_reasoning = max_plans_per_reasoning
        self.live_call = live_call
        self._checkpoint_uris: list[str] = []
        self._last_user_payload_bytes: int | None = None
        self._pending_checkpoint_plan_ids: set[str] = set()

    @property
    def planning_audit(self) -> RegisteredTransformationAuditV3:
        return self.planning_state.audit_snapshot()

    @property
    def checkpoint_uris(self) -> tuple[str, ...]:
        return tuple(self._checkpoint_uris)

    @property
    def last_user_payload_bytes(self) -> int | None:
        """Canonical byte size of the most recent compressed reasoning payload."""

        return self._last_user_payload_bytes

    def recover_checkpoint(self, checkpoint_uri: str) -> None:
        """Restore interrupted DeepSeek compilation without re-proposing science.

        This is intentionally explicit: a checkpoint should only be resumed when
        execution was interrupted after compilation and before the saved plans
        were consumed. Registry hashes and the complete typed audit are validated
        by ``OperatorPlanningToolState.restore_snapshot``.
        """

        if self.planning_audit.compile_attempt_count:
            raise ValueError("operator planning state must be empty before recovery")
        snapshot = self.store.read_json(checkpoint_uri)
        self.planning_state.restore_snapshot(snapshot)
        self._pending_checkpoint_plan_ids = {
            item.plan_id
            for item in self.planning_audit.plans
            if isinstance(item, StructureOperationPlanV2)
        }
        if not self._pending_checkpoint_plan_ids:
            raise ValueError("checkpoint contains no executable structure plans")
        if checkpoint_uri not in self._checkpoint_uris:
            self._checkpoint_uris.append(checkpoint_uri)

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
    ) -> ScientificOperatorProposalBatch:
        before_plan_ids = {item.plan_id for item in self.planning_audit.plans}
        maximum_by_cost = (
            remaining_candidate_slots
            if self.estimated_cost_units_per_operation == 0
            else remaining_cost_units // self.estimated_cost_units_per_operation
        )
        target_plan_count = min(
            remaining_candidate_slots,
            maximum_by_cost,
            self.max_plans_per_reasoning,
        )
        if target_plan_count <= 0:
            raise LLMProviderError(
                "BUDGET_EXHAUSTED",
                "remaining search budget cannot fund one structure operation",
                retryable=False,
            )
        frontier_parent_ids = {
            database_id
            for item in hypotheses
            for database_id in item.database_candidate_ids
        }
        checkpoint_plan_by_id = {
            item.plan_id: item for item in self.planning_audit.plans
        }
        recovered_plan_ids = tuple(
            sorted(
                plan_id
                for plan_id in self._pending_checkpoint_plan_ids
                if plan_id in checkpoint_plan_by_id
                and checkpoint_plan_by_id[plan_id].parent_candidate_id
                in frontier_parent_ids
            )
        )[:target_plan_count]
        if recovered_plan_ids:
            self._pending_checkpoint_plan_ids.difference_update(recovered_plan_ids)
            checkpoint_uri = self._checkpoint_uris[-1]
            return self._make_batch_from_plans(
                plan_ids=recovered_plan_ids,
                hypotheses=hypotheses,
                remaining_cost_units=remaining_cost_units,
                rationale=(
                    "Recovered DeepSeek-compiled, registry-valid structure plans from "
                    "an immutable checkpoint after an interrupted run; no new scientific "
                    "parameters were generated during recovery."
                ),
                provenance=DeepSeekReasoningProvenance(
                    prompt_version=REGISTRY_SEARCH_OPERATOR_PROMPT_VERSION,
                    receipt_sha256=canonical_sha256(
                        {
                            "checkpoint_uri": checkpoint_uri,
                            "plan_ids": recovered_plan_ids,
                            "planning_audit_sha256": canonical_sha256(
                                self.planning_audit
                            ),
                            "recovery": (
                                "ALL_NEW_COMPILED_STRUCTURE_PLANS_AFTER_REASONING_FAILURE"
                            ),
                        }
                    ),
                    live_call=False,
                ),
                recovery=("ALL_NEW_COMPILED_STRUCTURE_PLANS_AFTER_REASONING_FAILURE"),
            )
        compiler_parent_bindings = [
            {
                "hypothesis_candidate_id": item.candidate_id,
                "compiler_candidate_id": (
                    item.candidate_id
                    if item.candidate_id.startswith("candidate-")
                    else deterministic_id(
                        "candidate", {"hypothesis_candidate_id": item.candidate_id}
                    )
                ),
                "database_candidate_ids": item.database_candidate_ids,
            }
            for item in hypotheses
        ]
        base_tool = self.planning_state.as_compact_tool()

        def new_structure_plan_count() -> int:
            return sum(
                item.plan_id not in before_plan_ids
                and isinstance(item, StructureOperationPlanV2)
                for item in self.planning_audit.plans
            )

        def compile_and_checkpoint(arguments: object) -> Mapping[str, object]:
            result = base_tool.handler(arguments)
            audit = self.planning_audit
            digest = canonical_sha256(audit)
            reference = self.store.write_json(
                f"scientific_search/operator_checkpoints/{digest}.json",
                audit.model_dump(mode="json"),
                immutable=True,
            )
            if reference.uri not in self._checkpoint_uris:
                self._checkpoint_uris.append(reference.uri)
            return result

        compiler_tool = DeepSeekFunctionTool(
            name=base_tool.name,
            description=base_tool.description,
            arguments_model=base_tool.arguments_model,
            handler=compile_and_checkpoint,
            saturation_predicate=(
                lambda: new_structure_plan_count() >= target_plan_count
            ),
        )
        agent = self.agent_factory(
            secret_resolver=self.secret_resolver,
            tools=(compiler_tool,),
            budget=self.budget,
            reasoning_effort=self.reasoning_effort,
            timeout_seconds=900,
        )
        system_prompt = (
            "You direct a bounded, iterative materials-structure search. Infer the "
            "scientifically useful minimal operations and all parameters from the "
            "goal, evidence, failures and current candidate structures. Use the "
            "compiler tool to inspect its dynamic registry and compile each proposed "
            "route. This workflow is strictly ML_ONLY: never request DFT or a DFT "
            "fallback. Select only plan IDs returned by compiler calls in this turn. "
            "The compiler's candidate_id field has a legacy candidate-* syntax; use "
            "the supplied compiler_parent_bindings.compiler_candidate_id there, "
            "while database_candidate_id must be one of the same binding's database "
            "candidate IDs. "
            "A condition-only plan changes a calculation condition and does not create "
            "a CIF; do not select it when the requested next step requires a new "
            "structure. Respect the remaining candidate and cost budgets. Do not claim "
            "that compilation verifies any target property. Return plan IDs in sorted, "
            "duplicate-free order."
        )
        user_payload = {
            "goal": goal,
            "iteration": iteration,
            "hypotheses": [self._hypothesis_summary(item) for item in hypotheses],
            "evidence": [self._evidence_summary(item) for item in evidence],
            "feedback": self._feedback_summary(feedback),
            "memory_snapshot_id": memory_snapshot_id,
            "remaining_candidate_slots": remaining_candidate_slots,
            "remaining_cost_units": remaining_cost_units,
            "compiler_parent_bindings": compiler_parent_bindings,
            "available_parent_catalog": [
                self._parent_summary(item) for item in self.parent_catalog.snapshot()
            ],
            "required_output_schema": (
                DeepSeekCompiledPlanSelection.model_json_schema()
            ),
        }
        self._last_user_payload_bytes = len(canonical_json_bytes(user_payload))
        recovery = "NONE"
        reasoning_error: LLMProviderError | None = None
        try:
            result = agent.run(
                system_prompt=system_prompt,
                user_payload=user_payload,
                prompt_version=REGISTRY_SEARCH_OPERATOR_PROMPT_VERSION,
                final_model=DeepSeekCompiledPlanSelection,
                require_tool_call=True,
            )
        except LLMProviderError as error:
            reasoning_error = error
            result = None
        audit = self.planning_audit
        plan_by_id = {item.plan_id: item for item in audit.plans}
        new_plan_ids = set(plan_by_id) - before_plan_ids
        if result is None:
            selected = tuple(
                sorted(
                    plan_id
                    for plan_id in new_plan_ids
                    if isinstance(plan_by_id[plan_id], StructureOperationPlanV2)
                )
            )[:target_plan_count]
            if not selected:
                assert reasoning_error is not None
                raise reasoning_error
            rationale = (
                "DeepSeek failed during final selection after compiling these new "
                "structure plans; deterministic recovery retained every compiled, "
                "registry-valid structure plan within the remaining budgets."
            )
            provenance = DeepSeekReasoningProvenance(
                prompt_version=REGISTRY_SEARCH_OPERATOR_PROMPT_VERSION,
                receipt_sha256=canonical_sha256(
                    {
                        "failure_category": reasoning_error.category,
                        "new_plan_ids": tuple(sorted(new_plan_ids)),
                        "planning_audit_sha256": canonical_sha256(audit),
                        "recovery": (
                            "ALL_NEW_COMPILED_STRUCTURE_PLANS_AFTER_REASONING_FAILURE"
                        ),
                    }
                ),
                live_call=False,
            )
            recovery = "ALL_NEW_COMPILED_STRUCTURE_PLANS_AFTER_REASONING_FAILURE"
        else:
            selected = result.final.selected_plan_ids
            if not set(selected) <= new_plan_ids:
                raise ValueError(
                    "DeepSeek selected a plan not compiled in this reasoning turn"
                )
            rationale = result.final.rationale
            provenance = reasoning_provenance(result.receipt, live_call=self.live_call)
        return self._make_batch_from_plans(
            plan_ids=selected[:target_plan_count],
            hypotheses=hypotheses,
            remaining_cost_units=remaining_cost_units,
            rationale=rationale,
            provenance=provenance,
            recovery=recovery,
        )

    def _make_batch_from_plans(
        self,
        *,
        plan_ids: Sequence[str],
        hypotheses: Sequence[HypothesisCandidate],
        remaining_cost_units: int,
        rationale: str,
        provenance: DeepSeekReasoningProvenance,
        recovery: Literal[
            "NONE", "ALL_NEW_COMPILED_STRUCTURE_PLANS_AFTER_REASONING_FAILURE"
        ],
    ) -> ScientificOperatorProposalBatch:
        plan_by_id = {item.plan_id: item for item in self.planning_audit.plans}
        hypothesis_by_id = {item.candidate_id: item for item in hypotheses}
        proposals = []
        for plan_id in plan_ids:
            plan = plan_by_id[plan_id]
            possible_parents = [
                item
                for item in hypothesis_by_id.values()
                if plan.parent_candidate_id in item.database_candidate_ids
            ]
            if len(possible_parents) != 1:
                raise ValueError(
                    "compiled database parent does not resolve to one frontier hypothesis"
                )
            parent = possible_parents[0]
            spec = plan.operator_spec
            proposal = make_scientific_operator_proposal(
                parent_candidate_id=parent.candidate_id,
                operator_id=plan.operator_id,
                operator_spec_id=spec.operator_spec_id,
                parameters=dict(
                    sorted(plan.parameters.model_dump(mode="json").items())
                ),
                estimated_cost_units=min(
                    self.estimated_cost_units_per_operation,
                    remaining_cost_units,
                ),
                scientific_rationale=spec.scientific_rationale,
                expected_mechanism=spec.expected_mechanism,
                decisive_falsification_test=spec.decisive_falsification_test,
            )
            proposals.append(proposal)
        return make_scientific_operator_batch(
            proposals=tuple(proposals),
            rationale=rationale,
            provenance=provenance,
            selection_recovery=recovery,
        )

    @staticmethod
    def _hypothesis_summary(item: HypothesisCandidate) -> Mapping[str, object]:
        return {
            "candidate_id": item.candidate_id,
            "material_name": item.material_name,
            "formula": item.formula,
            "hypothesis": item.hypothesis,
            "mechanism": item.mechanism,
            "database_candidate_ids": item.database_candidate_ids,
            "parent_structure": item.parent_structure.model_dump(mode="json"),
            "elements": item.elements,
            "transition_metal_elements": item.transition_metal_elements,
            "dimensionality": item.dimensionality,
            "unresolved_claims": item.unresolved_claims,
            "priority": item.priority,
            "scientific_conclusion": False,
        }

    @staticmethod
    def _evidence_summary(item: ScientificEvidence) -> Mapping[str, object]:
        return {
            "evidence_id": item.evidence_id,
            "candidate_id": item.candidate_id,
            "model_id": item.model_id,
            "task_kind": item.task_kind,
            "execution_status": item.execution_status,
            "verdict": item.verdict,
            "tested_claim_ids": item.tested_claim_ids,
            "evidence_level": item.evidence_level,
            "reason_codes": item.reason_codes,
            "result_artifact": (
                item.result_artifact.model_dump(mode="json")
                if item.result_artifact is not None
                else None
            ),
            "real_execution": item.real_execution,
            "scientific_conclusion": False,
        }

    @staticmethod
    def _feedback_summary(item: FeedbackCycleResult) -> Mapping[str, object]:
        # Keep test doubles and older persisted feedback readable during the
        # migration to compact prompts. Production FeedbackCycleResult objects
        # always provide these attributes.
        cycle_id = getattr(item, "cycle_id", None)
        decisions = getattr(item, "decisions", ())
        if cycle_id is None and hasattr(item, "model_dump"):
            dumped = item.model_dump(mode="json")
            cycle_id = dumped.get("cycle_id", "feedback-unknown")
        return {
            "cycle_id": cycle_id or "feedback-unknown",
            "decisions": tuple(
                {
                    "candidate_id": decision.proposal.candidate_id,
                    "action": decision.proposal.action,
                    "evidence_ids": decision.proposal.evidence_ids,
                    "rationale": decision.proposal.rationale,
                    "operator_revision": (
                        decision.proposal.operator_revision.model_dump(mode="json")
                        if decision.proposal.operator_revision is not None
                        else None
                    ),
                    "audit_status": decision.status,
                    "audit_reason_codes": decision.reason_codes,
                }
                for decision in decisions
            ),
            "scientific_conclusion": False,
        }

    @staticmethod
    def _parent_summary(item: DatabaseCandidateV1) -> Mapping[str, object]:
        return {
            "database_candidate_id": item.database_candidate_id,
            "source_database": item.source_database,
            "source_material_id": item.source_material_id,
            "canonical_structure_id": item.canonical_structure_id,
            "formula": item.formula,
            "elements": item.elements,
            "transition_metals": item.transition_metals,
            "band_gap_ev": item.band_gap_ev,
            "dimensionality": item.dimensionality,
            "connectivity_status": item.connectivity_status,
            "structure_artifact_uri": item.structure_artifact_uri,
            "structure_artifact_sha256": item.structure_artifact_sha256,
            "scientific_conclusion": False,
        }


class RegistryStructureOperatorExecutor:
    """Execute compiled structure plans and register each child for the next round."""

    execution_mode = ScientificExecutionMode.ML_ONLY

    def __init__(
        self,
        *,
        store: LocalArtifactStore,
        parent_catalog: IterativeParentCatalog,
        planning_state: OperatorPlanningToolState,
        operator_registry: SoftChemOperatorRegistryV2 = (
            DEFAULT_SOFTCHEM_OPERATOR_REGISTRY_V2
        ),
        charged_cost_units_per_operation: int = 1,
    ) -> None:
        if charged_cost_units_per_operation < 0:
            raise ValueError("charged operator cost must be non-negative")
        self.store = store
        self.parent_catalog = parent_catalog
        self.planning_state = planning_state
        self.operator_registry = operator_registry
        self.charged_cost_units_per_operation = charged_cost_units_per_operation

    @classmethod
    def from_proposer(
        cls,
        proposer: DeepSeekRegistryOperatorProposer,
        *,
        charged_cost_units_per_operation: int = 1,
    ) -> RegistryStructureOperatorExecutor:
        return cls(
            store=proposer.store,
            parent_catalog=proposer.parent_catalog,
            planning_state=proposer.planning_state,
            charged_cost_units_per_operation=charged_cost_units_per_operation,
        )

    @property
    def planning_audit(self) -> RegisteredTransformationAuditV3:
        return self.planning_state.audit_snapshot()

    def execute(
        self,
        *,
        proposal: object,
        parent: HypothesisCandidate,
    ) -> CandidateGenerationResult:
        from material_agent.integration.scientific_search_loop import (
            ScientificOperatorProposal,
        )

        proposal = ScientificOperatorProposal.model_validate(proposal)
        if proposal.execution_mode is not ScientificExecutionMode.ML_ONLY:
            raise ValueError("registry structure execution is strictly ML_ONLY")
        audit = self.planning_audit
        matches = [
            item
            for item in audit.plans
            if item.operator_spec.operator_spec_id == proposal.operator_spec_id
        ]
        if len(matches) != 1:
            raise ValueError("operator proposal does not resolve to one compiled plan")
        plan = matches[0]
        expected_parameters = dict(
            sorted(plan.parameters.model_dump(mode="json").items())
        )
        if (
            proposal.operator_id != plan.operator_id
            or proposal.parameters != expected_parameters
        ):
            raise ValueError(
                "operator proposal differs from its hash-pinned compiled plan"
            )
        if proposal.parent_candidate_id != parent.candidate_id:
            raise ValueError("operator proposal differs from the supplied parent")
        if plan.parent_candidate_id not in parent.database_candidate_ids:
            raise ValueError("compiled database parent is absent from supplied lineage")
        if plan.parent_structure_artifact != parent.parent_structure:
            raise ValueError("compiled plan structure differs from the supplied parent")
        prospective_id = deterministic_id(
            "candidate",
            {"proposal_id": proposal.proposal_id, "plan_id": plan.plan_id},
        )
        charged = min(
            self.charged_cost_units_per_operation,
            proposal.estimated_cost_units,
        )
        if isinstance(plan, ReasonedConditionPlanV1):
            operator_result = make_operator_result(
                candidate_id=prospective_id,
                plan_id=plan.plan_id,
                operator_id=plan.operator_id,
                operator_spec_id=plan.operator_spec.operator_spec_id,
                route_sha256=plan.route_sha256,
                status=OperatorResultStatus.REJECTED,
                input_structure=plan.parent_structure_artifact,
                output_structure=None,
                reason_codes=("CONDITION_ONLY_PLAN_NO_STRUCTURE_ARTIFACT",),
            )
            return make_candidate_generation_result(
                proposal_id=proposal.proposal_id,
                operator_result=operator_result,
                candidate=None,
                charged_cost_units=0,
                real_execution=True,
                runtime_provenance={
                    "adapter": REGISTRY_SEARCH_ADAPTER_VERSION,
                    "execution_mode": "ML_ONLY",
                    "result": "CONDITION_ONLY_NOT_A_CIF",
                },
            )
        if not isinstance(plan, StructureOperationPlanV2):
            raise TypeError(
                "legacy transformation plans are unsupported by this adapter"
            )
        parent_bytes = self.store.read_bytes(parent.parent_structure.uri)
        if hashlib.sha256(parent_bytes).hexdigest() != parent.parent_structure.sha256:
            raise ValueError("parent CIF differs from its hypothesis hash")
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            parent_structure = Structure.from_str(
                parent_bytes.decode("utf-8"), fmt="cif"
            )
        execution = execute_registered_structure_operation(
            execution_request_from_compiled_operation_plan(
                plan, operator_registry=self.operator_registry
            ),
            parent_structure=parent_structure,
            parent_artifact_bytes=parent_bytes,
            operator_registry=self.operator_registry,
        )
        if (
            execution.artifact_bytes is None
            or execution.plan.output_structure_artifact is None
        ):
            operator_result = operator_result_from_structure_execution(
                prospective_id, execution
            )
            return make_candidate_generation_result(
                proposal_id=proposal.proposal_id,
                operator_result=operator_result,
                candidate=None,
                charged_cost_units=charged,
                real_execution=True,
                runtime_provenance={
                    "adapter": REGISTRY_SEARCH_ADAPTER_VERSION,
                    "execution_mode": "ML_ONLY",
                    "registry_execution_status": execution.plan.status.value,
                },
            )
        output_pointer = execution.plan.output_structure_artifact
        if self.parent_catalog.contains_structure_hash(output_pointer.sha256):
            duplicate_result = make_operator_result(
                candidate_id=prospective_id,
                plan_id=execution.plan.plan_id,
                operator_id=execution.plan.operator_id,
                operator_spec_id=execution.plan.operator_spec.operator_spec_id,
                route_sha256=execution.plan.route_sha256,
                status=OperatorResultStatus.REJECTED,
                input_structure=execution.plan.parent_structure_artifact,
                output_structure=None,
                reason_codes=("DUPLICATE_STRUCTURE_SHA256",),
                evidence_level="HEURISTIC_PRIOR_ONLY",
            )
            return make_candidate_generation_result(
                proposal_id=proposal.proposal_id,
                operator_result=duplicate_result,
                candidate=None,
                charged_cost_units=charged,
                real_execution=True,
                runtime_provenance={
                    "adapter": REGISTRY_SEARCH_ADAPTER_VERSION,
                    "duplicate_sha256": output_pointer.sha256,
                    "execution_mode": "ML_ONLY",
                    "registry_execution_status": execution.plan.status.value,
                },
            )
        written = self.store.write_bytes(
            output_pointer.uri.removeprefix("artifact://"),
            execution.artifact_bytes,
            media_type=output_pointer.media_type or "chemical/x-cif",
            immutable=True,
        )
        if written.sha256 != output_pointer.sha256:
            raise ValueError("persisted child CIF differs from executor output hash")
        assert execution.output_structure is not None
        catalog_child = self.parent_catalog.register_generated(
            execution_plan=execution.plan,
            output_structure=execution.output_structure,
            output_pointer=output_pointer,
        )
        child_id = deterministic_id(
            "candidate",
            {
                "parent_candidate_id": parent.candidate_id,
                "plan_id": execution.plan.plan_id,
                "structure_sha256": output_pointer.sha256,
            },
        )
        operator_result = operator_result_from_structure_execution(child_id, execution)
        child = HypothesisCandidate(
            candidate_id=child_id,
            material_name=(
                f"{catalog_child.formula} generated by {execution.plan.operator_id}"
            ),
            formula=catalog_child.formula,
            hypothesis=(
                "This registry-generated structure is a new candidate for the stated "
                "scientific goal and requires fresh ML validation."
            ),
            mechanism=execution.plan.operator_spec.expected_mechanism,
            database_candidate_ids=(catalog_child.database_candidate_id,),
            parent_structure=output_pointer,
            elements=catalog_child.elements,
            transition_metal_elements=catalog_child.transition_metals,
            dimensionality=catalog_child.dimensionality,
            literature_evidence_ids=parent.literature_evidence_ids,
            unresolved_claims=parent.unresolved_claims,
            priority=parent.priority,
        )
        return make_candidate_generation_result(
            proposal_id=proposal.proposal_id,
            operator_result=operator_result,
            candidate=child,
            charged_cost_units=charged,
            real_execution=True,
            runtime_provenance={
                "adapter": REGISTRY_SEARCH_ADAPTER_VERSION,
                "child_database_candidate_id": catalog_child.database_candidate_id,
                "execution_mode": "ML_ONLY",
                "output_structure_sha256": output_pointer.sha256,
                "registry_execution_status": execution.plan.status.value,
            },
        )


__all__ = [
    "DeepSeekCompiledPlanSelection",
    "DeepSeekRegistryOperatorProposer",
    "IterativeParentCatalog",
    "RegistryStructureOperatorExecutor",
]
