from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from material_agent.inspiration.deepseek_agent import DeepSeekAgentReceiptV1
from material_agent.inspiration.models import (
    ArtifactPointerV1,
    canonical_sha256,
    deterministic_id,
)
from material_agent.inspiration.research_memory import InspirationMemoryStore
from material_agent.integration.scientific_execution import ScientificExecutorRegistry
from material_agent.integration.scientific_loop import (
    CapabilityAvailability,
    DeepSeekFeedbackItem,
    DeepSeekFeedbackProposal,
    DeepSeekModelRouteProposal,
    DeepSeekReasoningProvenance,
    FeedbackAction,
    HypothesisCandidate,
    ModelCapability,
    ModelExecutionReceipt,
    ModelTaskInputKind,
    OperatorResultStatus,
    ProposedModelTask,
    ScientificEvidence,
    ScientificEvidenceLevel,
    ScientificEvidenceVerdict,
    ScientificExecutionMode,
    ScientificTaskKind,
    ValidationRoutePolicy,
    WeightStatus,
    audit_feedback_cycle,
    make_operator_result,
    write_evidence_to_research_memory,
)
from material_agent.integration.scientific_search_loop import (
    CandidateQualificationStage,
    CandidateSearchAction,
    ScientificCandidateSearchLoop,
    ScientificSearchBudget,
    ScientificSearchStopReason,
    make_candidate_generation_result,
    make_scientific_operator_batch,
    make_scientific_operator_proposal,
)
from material_agent.orchestrator.llm import LLMProviderError
from material_agent.retrieval.storage import LocalArtifactStore


def _pointer(ref) -> ArtifactPointerV1:
    return ArtifactPointerV1(
        uri=ref.uri,
        sha256=ref.sha256,
        size_bytes=ref.size_bytes,
        media_type=ref.media_type,
    )


def _live_provenance(prompt_version: str) -> DeepSeekReasoningProvenance:
    receipt = DeepSeekAgentReceiptV1(
        prompt_version=prompt_version,
        reasoning_effort="high",
        rounds=1,
        tool_calls=(),
        request_sha256_by_round=("1" * 64,),
        response_sha256_by_round=("2" * 64,),
        transport_attempts_by_round=(1,),
        transport_retry_count=0,
        prompt_tokens=10,
        completion_tokens=20,
        reasoning_tokens=10,
        total_tokens=30,
        final_response_sha256="3" * 64,
    )
    return DeepSeekReasoningProvenance(
        prompt_version=prompt_version,
        receipt_sha256=canonical_sha256(receipt),
        live_call=True,
        receipt=receipt,
    )


def _hypothesis(store: LocalArtifactStore) -> HypothesisCandidate:
    parent = store.write_text(
        "inputs/parent.cif",
        "data_parent\n_cell_length_a 3\n",
        media_type="chemical/x-cif",
        immutable=True,
    )
    return HypothesisCandidate(
        candidate_id="candidate-parent",
        material_name="Generic parent",
        formula="PdS2",
        hypothesis="A dynamic minimal operation may improve the target property.",
        mechanism="The operator may tune hopping while retaining connectivity.",
        database_candidate_ids=("db-candidate-" + "a" * 24,),
        parent_structure=_pointer(parent),
        elements=("Pd", "S"),
        transition_metal_elements=("Pd",),
        dimensionality=2,
        unresolved_claims=("target_property",),
    )


def _evidence(
    *,
    store: LocalArtifactStore,
    candidate_id: str,
    verdict: ScientificEvidenceVerdict,
    reason_codes: tuple[str, ...],
) -> ScientificEvidence:
    result = store.write_json(
        f"results/{candidate_id}.json",
        {"candidate_id": candidate_id, "verdict": verdict.value},
        immutable=True,
    )
    values = {
        "schema_version": "scientific-validation-loop-v1",
        "candidate_id": candidate_id,
        "model_task_plan_id": "plan-initial",
        "task_id": f"task-{candidate_id}",
        "model_id": "real-ml-screen-v1",
        "task_kind": ScientificTaskKind.GENERIC_MODEL_INFERENCE,
        "execution_status": "SUCCEEDED",
        "verdict": verdict,
        "tested_claim_ids": ("target_property",),
        "evidence_level": ScientificEvidenceLevel.L1_RETRIEVED,
        "result_artifact": _pointer(result),
        "consumed_artifact_ids": (),
        "produced_artifacts": (),
        "runtime_provenance": {"backend": "fixture-real-ml"},
        "reason_codes": reason_codes,
        "real_execution": True,
        "scientific_conclusion": False,
    }
    return ScientificEvidence(
        evidence_id=deterministic_id("scientific-evidence", values),
        **values,
    )


def _initial_feedback(
    *,
    hypothesis: HypothesisCandidate,
    evidence: ScientificEvidence,
    memory_store: InspirationMemoryStore,
) -> object:
    proposal = DeepSeekFeedbackProposal(
        provenance=_live_provenance("initial-feedback-v1"),
        cycle_summary="The parent fails the current speed-first screening threshold.",
        decisions=(
            DeepSeekFeedbackItem(
                candidate_id=hypothesis.candidate_id,
                action=FeedbackAction.ELIMINATE,
                evidence_ids=(evidence.evidence_id,),
                rationale="The real database-local result is a decisive hard failure.",
            ),
        ),
    )
    return audit_feedback_cycle(
        hypotheses=(hypothesis,),
        evidence=(evidence,),
        memory_snapshot=memory_store.snapshot("project-search"),
        proposal=proposal,
        policy=ValidationRoutePolicy(),
    )


class _DynamicOperatorProposer:
    def __init__(self) -> None:
        self.calls = 0

    def propose_operators(self, **values: object):
        self.calls += 1
        parent = values["hypotheses"][0]
        proposal = make_scientific_operator_proposal(
            parent_candidate_id=parent.candidate_id,
            # This intentionally does not exist in any static operation enum.
            operator_id="deepseek-discovered-registry-operator-v9",
            operator_spec_id="operator-spec-dynamic-v9",
            parameters={"registry_parameter": 0.125},
            estimated_cost_units=3,
            scientific_rationale=(
                "Use a registry-provided minimal operation selected from the evidence."
            ),
            expected_mechanism="Tune the target interaction without DFT.",
            decisive_falsification_test="Reject when the real ML screen contradicts.",
        )
        return make_scientific_operator_batch(
            proposals=(proposal,),
            rationale="Generate one bounded child and immediately validate it.",
            provenance=_live_provenance("dynamic-operator-proposal-v1"),
        )


class _DynamicOperatorExecutor:
    execution_mode = ScientificExecutionMode.ML_ONLY

    def __init__(self, store: LocalArtifactStore) -> None:
        self.store = store

    def execute(self, *, proposal, parent):
        output = self.store.write_text(
            "generated/dynamic-child.cif",
            "data_child\n_cell_length_a 3.125\n",
            media_type="chemical/x-cif",
            immutable=True,
        )
        child_pointer = _pointer(output)
        child_id = "candidate-dynamic-child"
        operator_result = make_operator_result(
            candidate_id=child_id,
            plan_id="plan-dynamic-v9",
            operator_id=proposal.operator_id,
            operator_spec_id=proposal.operator_spec_id,
            route_sha256=hashlib.sha256(proposal.proposal_id.encode()).hexdigest(),
            status=OperatorResultStatus.STRUCTURE_VALID,
            input_structure=parent.parent_structure,
            output_structure=child_pointer,
            reason_codes=("DYNAMIC_REGISTRY_OPERATOR_VALID",),
        )
        candidate = HypothesisCandidate(
            candidate_id=child_id,
            material_name="Dynamically generated child",
            formula=parent.formula,
            hypothesis="The generated structure may now satisfy the target property.",
            mechanism="The dynamic operator changes the relevant interaction scale.",
            database_candidate_ids=parent.database_candidate_ids,
            parent_structure=child_pointer,
            elements=parent.elements,
            transition_metal_elements=parent.transition_metal_elements,
            dimensionality=parent.dimensionality,
            literature_evidence_ids=parent.literature_evidence_ids,
            unresolved_claims=parent.unresolved_claims,
        )
        return make_candidate_generation_result(
            proposal_id=proposal.proposal_id,
            operator_result=operator_result,
            candidate=candidate,
            charged_cost_units=3,
            real_execution=True,
            runtime_provenance={"executor": "dynamic-registry-fixture"},
        )


class _TwoStepOperatorProposer:
    def __init__(self) -> None:
        self.parent_ids: list[str] = []

    def propose_operators(self, **values: object):
        parent = values["hypotheses"][0]
        self.parent_ids.append(parent.candidate_id)
        step = len(self.parent_ids)
        proposal = make_scientific_operator_proposal(
            parent_candidate_id=parent.candidate_id,
            operator_id="deepseek-iterative-child-operator-v1",
            operator_spec_id=f"operator-spec-step-{step}",
            parameters={"step": step},
            estimated_cost_units=1,
            scientific_rationale="Apply the next evidence-directed minimal operation.",
            expected_mechanism="Iteratively tune the target interaction.",
            decisive_falsification_test="Continue when real ML contradicts the claim.",
        )
        return make_scientific_operator_batch(
            proposals=(proposal,),
            rationale="Use the current frontier child as this iteration's parent.",
            provenance=_live_provenance(f"two-step-operator-{step}"),
        )


class _TwoStepOperatorExecutor:
    execution_mode = ScientificExecutionMode.ML_ONLY

    def __init__(self, store: LocalArtifactStore) -> None:
        self.store = store

    def execute(self, *, proposal, parent):
        step = int(proposal.parameters["step"])
        child_id = f"candidate-child-{step}"
        pointer = _pointer(
            self.store.write_text(
                f"generated/child-{step}.cif",
                f"data_child_{step}\n_cell_length_a {3 + step / 10}\n",
                media_type="chemical/x-cif",
                immutable=True,
            )
        )
        operator_result = make_operator_result(
            candidate_id=child_id,
            plan_id=f"plan-child-{step}",
            operator_id=proposal.operator_id,
            operator_spec_id=proposal.operator_spec_id,
            route_sha256=hashlib.sha256(proposal.proposal_id.encode()).hexdigest(),
            status=OperatorResultStatus.STRUCTURE_VALID,
            input_structure=parent.parent_structure,
            output_structure=pointer,
            reason_codes=("ITERATIVE_CHILD_STRUCTURE_VALID",),
        )
        candidate = HypothesisCandidate(
            candidate_id=child_id,
            material_name=f"Iterative child {step}",
            formula=parent.formula,
            hypothesis="This child receives another evidence-directed ML screen.",
            mechanism="Sequential minimal operations tune the target interaction.",
            database_candidate_ids=parent.database_candidate_ids,
            parent_structure=pointer,
            elements=parent.elements,
            transition_metal_elements=parent.transition_metal_elements,
            dimensionality=parent.dimensionality,
            literature_evidence_ids=parent.literature_evidence_ids,
            unresolved_claims=parent.unresolved_claims,
        )
        return make_candidate_generation_result(
            proposal_id=proposal.proposal_id,
            operator_result=operator_result,
            candidate=candidate,
            charged_cost_units=1,
            real_execution=True,
            runtime_provenance={"executor": "two-step-ml-only"},
        )


class _ScientificReasoner:
    def propose_route(self, **values: object) -> DeepSeekModelRouteProposal:
        hypothesis = values["hypotheses"][0]
        operator = values["operator_results"][0]
        return DeepSeekModelRouteProposal(
            goal_sha256=canonical_sha256({"goal": values["goal"]}),
            provenance=_live_provenance("child-route-v1"),
            route_summary="Run the registered real ML screen on the generated child.",
            tasks=(
                ProposedModelTask(
                    task_id=f"task-screen-{hypothesis.candidate_id}",
                    candidate_id=hypothesis.candidate_id,
                    model_id="real-ml-screen-v1",
                    input_kind=ModelTaskInputKind.OPERATOR_RESULT,
                    operator_result_id=operator.operator_result_id,
                    requested_observables=("target_property",),
                    required_evidence_level=ScientificEvidenceLevel.L1_RETRIEVED,
                    rationale="Screen the generated structure with the real ML backend.",
                    falsification_rule="Contradict when the target threshold fails.",
                ),
            ),
        )

    def propose_feedback(self, **values: object) -> DeepSeekFeedbackProposal:
        hypothesis = values["hypotheses"][0]
        evidence = values["evidence"][0]
        return DeepSeekFeedbackProposal(
            provenance=_live_provenance("child-feedback-v1"),
            cycle_summary="Retain the first generated child supported by the ML screen.",
            decisions=(
                DeepSeekFeedbackItem(
                    candidate_id=hypothesis.candidate_id,
                    action=FeedbackAction.RETAIN,
                    evidence_ids=(evidence.evidence_id,),
                    rationale="The real screening evidence supports retaining this child.",
                ),
            ),
        )


class _FeedbackBudgetReasoner(_ScientificReasoner):
    def propose_feedback(self, **_values: object):
        raise LLMProviderError(
            "BUDGET_EXHAUSTED",
            "fixture feedback budget exhausted",
            retryable=False,
        )


class _RealMLExecutor:
    def __init__(self, store: LocalArtifactStore) -> None:
        self.store = store

    def execute(self, *, binding):
        pointer = _pointer(
            self.store.write_json(
                f"ml/{binding.task_id}.json",
                {"prediction": 0.97, "task_id": binding.task_id},
                immutable=True,
            )
        )
        return ModelExecutionReceipt(
            task_id=binding.task_id,
            status="SUCCEEDED",
            verdict=ScientificEvidenceVerdict.SUPPORTS,
            tested_claim_ids=binding.proposed_task.requested_observables,
            evidence_level=ScientificEvidenceLevel.L1_RETRIEVED,
            result_artifact=pointer,
            consumed_artifact_ids=tuple(
                item.artifact_id for item in binding.consumed_artifacts
            ),
            runtime_provenance={"backend": "real-ml-fixture"},
            reason_codes=("REAL_ML_TARGET_SCREEN_PASSED",),
            real_execution=True,
        )


class _TwoStepMLExecutor(_RealMLExecutor):
    def execute(self, *, binding):
        receipt = super().execute(binding=binding)
        if binding.candidate_id != "candidate-child-1":
            return receipt
        payload = receipt.model_dump(mode="python")
        payload.update(
            verdict=ScientificEvidenceVerdict.CONTRADICTS,
            reason_codes=("SURROGATE_CLEAR_THRESHOLD_FAILURE",),
        )
        return ModelExecutionReceipt.model_validate(payload)


def _fixture(tmp_path: Path):
    store = LocalArtifactStore(tmp_path / "artifacts")
    memory = InspirationMemoryStore(
        tmp_path / "memory.sqlite",
        artifact_verifier=lambda pointer: (
            store.exists(pointer.uri)
            and store.inspect(pointer.uri).sha256 == pointer.sha256
        ),
    )
    hypothesis = _hypothesis(store)
    evidence = _evidence(
        store=store,
        candidate_id=hypothesis.candidate_id,
        verdict=ScientificEvidenceVerdict.CONTRADICTS,
        reason_codes=("BANDWIDTH_EXCEEDS_POLICY",),
    )
    write_evidence_to_research_memory(
        project_id="project-search",
        source_run_id="source-initial",
        evidence=evidence,
        artifact_store=store,
        memory_store=memory,
    )
    feedback = _initial_feedback(
        hypothesis=hypothesis,
        evidence=evidence,
        memory_store=memory,
    )
    capability = ModelCapability(
        model_id="real-ml-screen-v1",
        availability=CapabilityAvailability.READY,
        weight_status=WeightStatus.NOT_REQUIRED,
        supported_observables=("target_property",),
        evidence_ceiling=ScientificEvidenceLevel.L1_RETRIEVED,
        estimated_cost_units=5,
        real_backend=True,
        benchmark_status="NOT_RUN",
    )
    executors = ScientificExecutorRegistry()
    executors.register("real-ml-screen-v1", _RealMLExecutor(store))
    return store, memory, hypothesis, evidence, feedback, capability, executors


def test_iterates_after_parent_elimination_until_generated_candidate_is_retained(
    tmp_path: Path,
) -> None:
    store, memory, hypothesis, evidence, feedback, capability, executors = _fixture(
        tmp_path
    )
    proposer = _DynamicOperatorProposer()
    loop = ScientificCandidateSearchLoop(
        project_id="project-search",
        source_run_id="source-search",
        goal="Discover a candidate through dynamically reasoned minimal operations.",
        capabilities=(capability,),
        route_policy=ValidationRoutePolicy(max_cost_units=100),
        search_budget=ScientificSearchBudget(
            max_iterations=4,
            max_candidates=4,
            max_cost_units=30,
        ),
        artifact_store=store,
        memory_store=memory,
        scientific_reasoner=_ScientificReasoner(),
        operator_proposer=proposer,
        operator_executor=_DynamicOperatorExecutor(store),
        scientific_executors=executors,
    )

    result = loop.run(
        hypotheses=(hypothesis,),
        evidence=(evidence,),
        feedback=feedback,
    )

    assert result.stop_reason is ScientificSearchStopReason.CANDIDATE_FOUND
    assert result.retained_candidate_ids == ("candidate-dynamic-child",)
    assert result.all_candidate_ids == (
        "candidate-dynamic-child",
        "candidate-parent",
    )
    assert result.total_cost_units == 8
    assert len(result.iterations) == 1
    assert result.iterations[0].model_task_plan.total_approved_cost_units == 5
    assert result.iterations[0].dag_run.all_audited_tasks_succeeded is True
    assert result.final_state.action is CandidateSearchAction.CANDIDATE_FOUND
    assert [item.stage for item in result.qualifications] == [
        CandidateQualificationStage.GENERATED_STRUCTURE_CANDIDATE,
        CandidateQualificationStage.ML_SCREENING_CANDIDATE,
    ]
    assert proposer.calls == 1
    assert len(memory.snapshot("project-search").downstream_outcomes) == 2
    assert store.exists(
        f"scientific_loop/source-search/search-results/{result.search_id}.json"
    )


def test_candidate_budget_stops_before_another_operator_call(tmp_path: Path) -> None:
    store, memory, hypothesis, evidence, feedback, capability, executors = _fixture(
        tmp_path
    )
    proposer = _DynamicOperatorProposer()
    loop = ScientificCandidateSearchLoop(
        project_id="project-search",
        source_run_id="source-budget",
        goal="Respect the whole-search candidate budget.",
        capabilities=(capability,),
        route_policy=ValidationRoutePolicy(),
        search_budget=ScientificSearchBudget(
            max_iterations=4,
            max_candidates=1,
            max_cost_units=30,
        ),
        artifact_store=store,
        memory_store=memory,
        scientific_reasoner=_ScientificReasoner(),
        operator_proposer=proposer,
        operator_executor=_DynamicOperatorExecutor(store),
        scientific_executors=executors,
    )

    result = loop.run(
        hypotheses=(hypothesis,),
        evidence=(evidence,),
        feedback=feedback,
    )

    assert result.stop_reason is ScientificSearchStopReason.MAX_CANDIDATES_REACHED
    assert result.iterations == ()
    assert proposer.calls == 0


def test_retain_does_not_fake_success_when_a_hard_claim_has_no_ml_capability(
    tmp_path: Path,
) -> None:
    store, memory, original, evidence, _feedback, capability, executors = _fixture(
        tmp_path
    )
    hypothesis = original.model_copy(
        update={"unresolved_claims": ("missing_hard_claim", "target_property")}
    )
    feedback = _initial_feedback(
        hypothesis=hypothesis,
        evidence=evidence,
        memory_store=memory,
    )
    loop = ScientificCandidateSearchLoop(
        project_id="project-search",
        source_run_id="source-capability-gap",
        goal="Never promote an unknown hard claim to a candidate.",
        capabilities=(capability,),
        route_policy=ValidationRoutePolicy(max_cost_units=100),
        search_budget=ScientificSearchBudget(
            max_iterations=4,
            max_candidates=4,
            max_cost_units=30,
        ),
        artifact_store=store,
        memory_store=memory,
        scientific_reasoner=_ScientificReasoner(),
        operator_proposer=_DynamicOperatorProposer(),
        operator_executor=_DynamicOperatorExecutor(store),
        scientific_executors=executors,
    )

    result = loop.run(
        hypotheses=(hypothesis,),
        evidence=(evidence,),
        feedback=feedback,
    )

    assert result.stop_reason is ScientificSearchStopReason.CAPABILITY_GAP
    assert result.retained_candidate_ids == ()
    assert result.final_state.action is CandidateSearchAction.CAPABILITY_GAP
    assert result.capability_gap_claim_ids == ("missing_hard_claim",)
    assert [item.stage for item in result.qualifications] == [
        CandidateQualificationStage.GENERATED_STRUCTURE_CANDIDATE
    ]
    assert (
        result.iterations[0].feedback.decisions[0].proposal.action
        is FeedbackAction.RETAIN
    )


def test_generated_child_becomes_the_next_iteration_parent(tmp_path: Path) -> None:
    store, memory, hypothesis, evidence, feedback, capability, _executors = _fixture(
        tmp_path
    )
    executors = ScientificExecutorRegistry()
    executors.register("real-ml-screen-v1", _TwoStepMLExecutor(store))
    proposer = _TwoStepOperatorProposer()
    loop = ScientificCandidateSearchLoop(
        project_id="project-search",
        source_run_id="source-child-frontier",
        goal="Use generated children as the next search frontier.",
        capabilities=(capability,),
        route_policy=ValidationRoutePolicy(max_cost_units=100),
        search_budget=ScientificSearchBudget(
            max_iterations=3,
            max_candidates=4,
            max_cost_units=30,
        ),
        artifact_store=store,
        memory_store=memory,
        scientific_reasoner=_ScientificReasoner(),
        operator_proposer=proposer,
        operator_executor=_TwoStepOperatorExecutor(store),
        scientific_executors=executors,
    )

    result = loop.run(
        hypotheses=(hypothesis,),
        evidence=(evidence,),
        feedback=feedback,
    )

    assert result.stop_reason is ScientificSearchStopReason.CANDIDATE_FOUND
    assert result.retained_candidate_ids == ("candidate-child-2",)
    assert proposer.parent_ids == ["candidate-parent", "candidate-child-1"]
    assert len(result.iterations) == 2


def test_rejects_non_ml_operator_executor(tmp_path: Path) -> None:
    (
        store,
        memory,
        _hypothesis_value,
        _evidence_value,
        _feedback,
        capability,
        executors,
    ) = _fixture(tmp_path)

    class _DFTExecutor:
        execution_mode = ScientificExecutionMode.HYBRID_DFT

    with pytest.raises(ValueError, match="must be ML_ONLY"):
        ScientificCandidateSearchLoop(
            project_id="project-search",
            source_run_id="source-dft-rejected",
            goal="Reject DFT execution.",
            capabilities=(capability,),
            route_policy=ValidationRoutePolicy(),
            search_budget=ScientificSearchBudget(),
            artifact_store=store,
            memory_store=memory,
            scientific_reasoner=_ScientificReasoner(),
            operator_proposer=_DynamicOperatorProposer(),
            operator_executor=_DFTExecutor(),
            scientific_executors=executors,
        )


def test_feedback_failure_preserves_generated_candidates_and_evidence(
    tmp_path: Path,
) -> None:
    store, memory, hypothesis, evidence, feedback, capability, executors = _fixture(
        tmp_path
    )
    loop = ScientificCandidateSearchLoop(
        project_id="project-search",
        source_run_id="source-feedback-recovery",
        goal="Preserve useful partial work when feedback reasoning exhausts its budget.",
        capabilities=(capability,),
        route_policy=ValidationRoutePolicy(max_cost_units=100),
        search_budget=ScientificSearchBudget(
            max_iterations=2,
            max_candidates=4,
            max_cost_units=30,
        ),
        artifact_store=store,
        memory_store=memory,
        scientific_reasoner=_FeedbackBudgetReasoner(),
        operator_proposer=_DynamicOperatorProposer(),
        operator_executor=_DynamicOperatorExecutor(store),
        scientific_executors=executors,
    )

    result = loop.run(
        hypotheses=(hypothesis,),
        evidence=(evidence,),
        feedback=feedback,
    )

    assert result.stop_reason is (
        ScientificSearchStopReason.REASONING_FAILURE_AFTER_EVIDENCE
    )
    assert result.final_state.action is CandidateSearchAction.BUDGET_EXHAUSTED
    assert result.iterations[0].feedback is None
    assert result.iterations[0].dag_run.evidence
    assert result.final_state.generated_structure_candidate_ids == (
        "candidate-dynamic-child",
    )
