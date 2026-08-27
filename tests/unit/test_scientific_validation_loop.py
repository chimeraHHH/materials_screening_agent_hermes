from __future__ import annotations

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from material_agent.inspiration.deepseek_agent import (
    DeepSeekAgentReceiptV1,
    DeepSeekAgentResultV1,
    DeepSeekToolCallReceiptV1,
)
from material_agent.inspiration.models import ArtifactPointerV1, canonical_sha256
from material_agent.inspiration.operator_planning import (
    CompileReasonedOperationArgsV3,
)
from material_agent.inspiration.research_memory import InspirationMemoryStore
from material_agent.integration.scientific_loop import (
    CapabilityAvailability,
    DeepSeekFeedbackItem,
    DeepSeekFeedbackProposal,
    DeepSeekModelRouteProposal,
    DeepSeekReasoningProvenance,
    DeepSeekScientificLoopReasoner,
    FeedbackAction,
    HypothesisCandidate,
    ModelCapability,
    ModelExecutionReceipt,
    ModelTaskInputKind,
    OperatorResult,
    OperatorResultStatus,
    ProposedModelTask,
    RouteAuditStatus,
    ScientificArtifactKind,
    ScientificEvidenceLevel,
    ScientificEvidenceVerdict,
    ScientificExecutionMode,
    ScientificTaskKind,
    ScientificValidationLoopService,
    ValidationRoutePolicy,
    WeightStatus,
    audit_feedback_cycle,
    compile_model_task_plan,
    hypothesis_candidates_from_research_graph,
    make_operator_result,
    operator_result_from_structure_execution,
    scientific_evidence_from_execution,
    write_evidence_to_research_memory,
)
from material_agent.retrieval.storage import LocalArtifactStore

PROMPT1 = """搜索数据库中的过渡金属二维平带材料。要求：
1. 必须是层状材料，有 vdW gap 的材料最优先。
2. 平带必须是费米面附近的第一条能带，贡献这条能带的电子轨道必须由过渡金属元素或过渡金属与配体的杂化态构成。
3. 平带和其他色散较大的能带不能有一阶交点，色散能带不能穿过费米面，但它们可以在高对称点有公共极值点。
4. 平带的定义是带宽 W<=50 meV 的能带。
5. 进行价态分析，过渡金属必须处于常见价态或常见价态的混合。
6. 平带不能由孤立原子或 cluster 形成，必须由互连的子晶格贡献。"""


def test_scientific_route_policy_rejects_dft_execution_mode() -> None:
    with pytest.raises(ValueError, match="ML_ONLY"):
        ValidationRoutePolicy(
            execution_mode=ScientificExecutionMode.HYBRID_DFT,
        )


def test_model_capability_requires_inputs_to_be_accepted() -> None:
    with pytest.raises(ValueError, match="required model inputs"):
        ModelCapability(
            model_id="invalid-required-input-model",
            availability=CapabilityAvailability.READY,
            weight_status=WeightStatus.NOT_REQUIRED,
            supported_observables=(),
            required_input_artifact_kinds=(
                ScientificArtifactKind.ML_SOC_HAMILTONIAN,
            ),
            evidence_ceiling=ScientificEvidenceLevel.L1_RETRIEVED,
            estimated_cost_units=1,
            real_backend=True,
            benchmark_status="VALIDATED",
        )


def test_route_audit_blocks_a_model_hard_input_omission() -> None:
    goal = "audit one ML-only model dependency"
    hypothesis = _hypotheses()[0]
    proposal = DeepSeekModelRouteProposal(
        goal_sha256=canonical_sha256({"goal": goal}),
        provenance=_provenance("required-input-audit-fixture-v1"),
        route_summary="Attempt one model task without its mandatory input Artifact.",
        tasks=(
            ProposedModelTask(
                task_id="task-missing-required-hamiltonian",
                candidate_id=hypothesis.candidate_id,
                model_id="band-model-with-hard-input-v1",
                input_kind=ModelTaskInputKind.PARENT_STRUCTURE,
                requested_observables=("bandwidth_le_50_mev",),
                required_evidence_level=ScientificEvidenceLevel.L1_RETRIEVED,
                rationale="Exercise the capability-owned hard-input audit.",
                falsification_rule="The task must be blocked before execution.",
            ),
        ),
    )
    capability = ModelCapability(
        model_id="band-model-with-hard-input-v1",
        availability=CapabilityAvailability.READY,
        weight_status=WeightStatus.NOT_REQUIRED,
        supported_observables=("bandwidth_le_50_mev",),
        accepted_artifact_kinds=(ScientificArtifactKind.ML_SOC_HAMILTONIAN,),
        required_input_artifact_kinds=(
            ScientificArtifactKind.ML_SOC_HAMILTONIAN,
        ),
        evidence_ceiling=ScientificEvidenceLevel.L1_RETRIEVED,
        estimated_cost_units=1,
        real_backend=True,
        benchmark_status="VALIDATED",
    )

    plan = compile_model_task_plan(
        goal=goal,
        hypotheses=(hypothesis,),
        operator_results=(),
        proposal=proposal,
        capabilities=(capability,),
        policy=ValidationRoutePolicy(require_live_deepseek_reasoning=False),
    )

    assert plan.tasks[0].status is RouteAuditStatus.BLOCKED
    assert "MODEL_REQUIRED_INPUT_ARTIFACT_MISSING" in plan.tasks[0].reason_codes


def _pointer(name: str, payload: bytes = b"fixture") -> ArtifactPointerV1:
    return ArtifactPointerV1(
        uri=f"artifact://case/{name}",
        sha256=hashlib.sha256(payload).hexdigest(),
        size_bytes=len(payload),
        media_type="chemical/x-cif",
    )


def _hypotheses() -> tuple[HypothesisCandidate, ...]:
    shared_claims = tuple(
        sorted(
            {
                "bandwidth_le_50_mev",
                "connected_tm_sublattice",
                "dispersive_band_no_fermi_crossing",
                "flat_band_first_near_fermi",
                "no_first_order_crossing",
                "tm_or_ligand_hybrid_orbital_character",
            }
        )
    )
    return (
        HypothesisCandidate(
            candidate_id="candidate-pd3p2s8-strain",
            material_name="Biaxially strained Pd3P2S8",
            formula="P2Pd3S8",
            hypothesis=(
                "Small biaxial tensile strain may narrow the connected Pd-kagome "
                "flat band while retaining its Fermi-level ordering."
            ),
            mechanism=(
                "Reduced Pd-Pd hopping preserves destructive interference on the "
                "connected transition-metal sublattice."
            ),
            database_candidate_ids=("db-candidate-" + "4" * 24,),
            parent_structure=_pointer("pd3p2s8-parent.cif", b"parent-pd3p2s8"),
            elements=("P", "Pd", "S"),
            dimensionality=2,
            literature_evidence_ids=("evidence-kagome-parent",),
            unresolved_claims=shared_claims,
            priority=1,
        ),
        HypothesisCandidate(
            candidate_id="candidate-pd3p2s8-li-intercalated",
            material_name="Li-intercalated Pd3P2S8",
            formula="LiP2Pd3S8",
            hypothesis=(
                "Li in the van der Waals gap may shift the Fermi level toward the "
                "Pd-derived flat band without breaking the Pd network."
            ),
            mechanism=(
                "Gap-site charge transfer changes filling while the connected "
                "in-plane Pd sublattice is retained."
            ),
            database_candidate_ids=("db-candidate-" + "4" * 24,),
            parent_structure=_pointer("pd3p2s8-parent.cif", b"parent-pd3p2s8"),
            elements=("Li", "P", "Pd", "S"),
            dimensionality=2,
            literature_evidence_ids=("evidence-kagome-parent",),
            unresolved_claims=shared_claims,
            priority=2,
        ),
        HypothesisCandidate(
            candidate_id="candidate-lip2pds6-li-vacancy",
            material_name="Li-deficient LiP2PdS6",
            formula="LiP2PdS6",
            hypothesis=(
                "A bounded Li-vacancy operation may tune filling without producing "
                "an isolated defect flat band."
            ),
            mechanism=(
                "Hole doping may shift the Pd-ligand manifold while preserving the "
                "interconnected host framework."
            ),
            database_candidate_ids=("db-candidate-" + "c" * 24,),
            parent_structure=_pointer("lip2pds6-parent.cif", b"parent-lip2pds6"),
            elements=("Li", "P", "Pd", "S"),
            dimensionality=2,
            literature_evidence_ids=(),
            unresolved_claims=shared_claims,
            priority=3,
        ),
    )


def _provenance(prompt: str) -> DeepSeekReasoningProvenance:
    return DeepSeekReasoningProvenance(
        prompt_version=prompt,
        receipt_sha256="a" * 64,
        live_call=False,
    )


class _UnusedSecretResolver:
    def resolve(self) -> str:
        raise AssertionError("fixture agent must not resolve a real secret")


class _FixtureRouteAgent:
    def __init__(self, **values: object) -> None:
        self.tools = values["tools"]

    def run(self, **values: object):
        assert "required_output_schema" in values["user_payload"]
        assert "strictly ML-only" in values["system_prompt"]
        assert "never propose DFT" in values["system_prompt"]
        tool = self.tools[0]
        snapshot = tool.handler(tool.arguments_model())
        assert len(snapshot["capabilities"]) == 1
        candidate_id = values["user_payload"]["hypotheses"][0]["candidate_id"]
        final_model = values["final_model"]
        final = final_model.model_validate_json(
            json.dumps(
                {
                    "route_summary": (
                        "Use the inspected scalar model only as an L1 screen."
                    ),
                    "tasks": [
                        {
                            "task_id": "task-fixture-band-gap",
                            "candidate_id": candidate_id,
                            "model_id": "ct-uae-band-gap-v1",
                            "input_kind": "PARENT_STRUCTURE",
                            "operator_result_id": None,
                            "prerequisite_task_ids": [],
                            "requested_observables": ["band_gap_ev"],
                            "required_evidence_level": "L1_RETRIEVED",
                            "rationale": (
                                "Use the only inspected available scalar capability."
                            ),
                            "falsification_rule": (
                                "Do not interpret band gap as flat-band evidence."
                            ),
                        }
                    ],
                }
            )
        )
        receipt = DeepSeekAgentReceiptV1(
            prompt_version=values["prompt_version"],
            reasoning_effort="high",
            rounds=1,
            tool_calls=(
                DeepSeekToolCallReceiptV1(
                    sequence=1,
                    round_index=1,
                    tool_call_id="fixture-call-1",
                    tool_name=tool.name,
                    arguments_sha256="1" * 64,
                    result_sha256="2" * 64,
                    result_bytes=100,
                ),
            ),
            request_sha256_by_round=("3" * 64,),
            response_sha256_by_round=("4" * 64,),
            transport_attempts_by_round=(1,),
            transport_retry_count=0,
            prompt_tokens=100,
            completion_tokens=100,
            reasoning_tokens=50,
            total_tokens=200,
            final_response_sha256="5" * 64,
        )
        return DeepSeekAgentResultV1[final_model](final=final, receipt=receipt)


class _RouteOnlyReasoner:
    def __init__(self, proposal: DeepSeekModelRouteProposal) -> None:
        self.proposal = proposal

    def propose_route(self, **_values: object) -> DeepSeekModelRouteProposal:
        return self.proposal

    def propose_feedback(self, **_values: object):
        raise AssertionError("route-only fixture must not propose feedback")


def _operator_result(
    candidate_id: str,
    *,
    plan_id: str,
    output: ArtifactPointerV1,
) -> OperatorResult:
    return make_operator_result(
        candidate_id=candidate_id,
        plan_id=plan_id,
        operator_id="APPLY_HOMOGENEOUS_STRAIN_V1",
        operator_spec_id=f"operator-spec-{plan_id[-8:]}",
        route_sha256=hashlib.sha256(plan_id.encode()).hexdigest(),
        status=OperatorResultStatus.STRUCTURE_VALID,
        input_structure=_pointer(f"{candidate_id}-input.cif", candidate_id.encode()),
        output_structure=output,
        reason_codes=("REGISTERED_OPERATOR_STRUCTURE_VALID",),
    )


def test_deepseek_reasoner_inspects_complete_capability_snapshot_and_keeps_receipt() -> None:
    hypothesis = _hypotheses()[0]
    reasoner = DeepSeekScientificLoopReasoner(
        secret_resolver=_UnusedSecretResolver(),
        agent_factory=_FixtureRouteAgent,
        live_call=False,
    )
    proposal = reasoner.propose_route(
        goal=PROMPT1,
        hypotheses=(hypothesis,),
        operator_results=(),
        capabilities=(
            ModelCapability(
                model_id="ct-uae-band-gap-v1",
                availability=CapabilityAvailability.READY,
                weight_status=WeightStatus.READY,
                checkpoint_sha256="8" * 64,
                supported_observables=("band_gap_ev",),
                supported_elements=None,
                supported_dimensionalities=(2, 3),
                evidence_ceiling=ScientificEvidenceLevel.L1_RETRIEVED,
                estimated_cost_units=1,
                real_backend=True,
                benchmark_status="NOT_RUN",
            ),
        ),
    )

    assert proposal.tasks[0].model_id == "ct-uae-band-gap-v1"
    assert proposal.provenance.prompt_version == "scientific-route-deepseek-v1"
    assert proposal.provenance.live_call is False
    assert proposal.provenance.reasoning_content_persisted is False


def test_historical_prompt1_graph_projects_all_candidates_and_formula_elements() -> None:
    database_parent = SimpleNamespace(
        database_candidate_id="db-candidate-" + "4" * 24,
        structure_artifact_uri="artifact://prompt1/pd3p2s8.cif",
        structure_artifact_sha256="9" * 64,
        elements=("P", "Pd", "S"),
        dimensionality=2,
    )
    candidates = tuple(
        SimpleNamespace(
            candidate_id=candidate_id,
            material_name=name,
            formula=formula,
            hypothesis="This prompt1 candidate remains a falsifiable flat-band hypothesis.",
            mechanism="A connected Pd-ligand network supplies the proposed band mechanism.",
            database_candidate_ids=(database_parent.database_candidate_id,),
            evidence_ids=("evidence-prompt1-parent",),
        )
        for candidate_id, name, formula in (
            (
                "candidate-pd3p2s8-strain",
                "Biaxially strained Pd3P2S8",
                "P2Pd3S8",
            ),
            (
                "candidate-pd3p2s8-li-intercalated",
                "Li-intercalated Pd3P2S8",
                "LiP2Pd3S8",
            ),
            (
                "candidate-lip2pds6-li-vacancy",
                "Li-deficient LiP2PdS6",
                "LiP2PdS6",
            ),
        )
    )
    ranked = tuple(item.candidate_id for item in candidates)
    graph = SimpleNamespace(
        database_candidates=(database_parent,),
        candidates=SimpleNamespace(candidates=candidates),
        synthesis=SimpleNamespace(
            ranked_candidate_ids=ranked,
            unresolved_hard_constraints=tuple(
                f"{candidate_id}:bandwidth_le_50_mev"
                for candidate_id in ranked
            ),
        ),
        constraints=SimpleNamespace(
            constraints=(SimpleNamespace(constraint_id="bandwidth_le_50_mev"),)
        ),
    )

    projected = hypothesis_candidates_from_research_graph(graph)

    assert tuple(item.candidate_id for item in projected) == ranked
    assert projected[1].elements == ("Li", "P", "Pd", "S")
    assert projected[2].elements == ("Li", "P", "Pd", "S")
    assert all(item.unresolved_claims == ("bandwidth_le_50_mev",) for item in projected)


def test_loop_service_orchestrates_route_execution_and_memory(tmp_path: Path) -> None:
    hypothesis = _hypotheses()[0]
    proposal = DeepSeekModelRouteProposal(
        goal_sha256=canonical_sha256({"goal": PROMPT1}),
        provenance=_provenance("service-route-fixture-v1"),
        route_summary="Run one fixture task to exercise the service control flow.",
        tasks=(
            ProposedModelTask(
                task_id="task-service-fixture",
                candidate_id=hypothesis.candidate_id,
                model_id="fixture-service-model",
                input_kind=ModelTaskInputKind.PARENT_STRUCTURE,
                requested_observables=("bandwidth_le_50_mev",),
                required_evidence_level=ScientificEvidenceLevel.NONE,
                rationale="Exercise route, receipt, artifact and memory orchestration.",
                falsification_rule="Fixture output cannot falsify the material hypothesis.",
            ),
        ),
    )
    capability = ModelCapability(
        model_id="fixture-service-model",
        availability=CapabilityAvailability.READY,
        weight_status=WeightStatus.NOT_REQUIRED,
        supported_observables=("bandwidth_le_50_mev",),
        supported_elements=None,
        supported_dimensionalities=(2,),
        evidence_ceiling=ScientificEvidenceLevel.NONE,
        estimated_cost_units=0,
        real_backend=False,
        benchmark_status="NOT_RUN",
    )
    artifact_store = LocalArtifactStore(tmp_path / "service-project")

    def verify(pointer: ArtifactPointerV1) -> bool:
        try:
            return artifact_store.inspect(pointer.uri).sha256 == pointer.sha256
        except (FileNotFoundError, ValueError):
            return False

    with InspirationMemoryStore(
        tmp_path / "service-memory.sqlite3",
        artifact_verifier=verify,
    ) as memory:
        service = ScientificValidationLoopService(
            project_id="prompt1-service-project",
            source_run_id="prompt1-service-run",
            goal=PROMPT1,
            hypotheses=(hypothesis,),
            capabilities=(capability,),
            policy=ValidationRoutePolicy(
                require_real_backend=False,
                require_live_deepseek_reasoning=False,
            ),
            artifact_store=artifact_store,
            memory_store=memory,
            reasoner=_RouteOnlyReasoner(proposal),
        )
        returned, plan = service.propose_and_audit_route(())
        assert returned == proposal
        assert plan.tasks[0].status is RouteAuditStatus.APPROVED
        assert artifact_store.exists(
            f"artifact://scientific_loop/prompt1-service-run/plans/{plan.plan_id}.json"
        )
        result_ref = artifact_store.write_bytes(
            "service-results/task-service-fixture.json",
            b"fixture service result",
            "application/json",
            immutable=True,
        )
        evidence, snapshot = service.record_execution(
            plan,
            ModelExecutionReceipt(
                task_id="task-service-fixture",
                status="SUCCEEDED",
                verdict=ScientificEvidenceVerdict.INCONCLUSIVE,
                tested_claim_ids=("bandwidth_le_50_mev",),
                evidence_level=ScientificEvidenceLevel.NONE,
                result_artifact=ArtifactPointerV1(
                    uri=result_ref.uri,
                    sha256=result_ref.sha256,
                    size_bytes=result_ref.size_bytes,
                    media_type=result_ref.media_type,
                ),
                reason_codes=("FIXTURE_CONTROL_ONLY",),
                real_execution=False,
            ),
        )

    assert snapshot.downstream_outcomes[0].evidence_id == evidence.evidence_id


def test_registered_structure_execution_projects_to_generic_operator_result() -> None:
    parent = _pointer("operator-parent.cif", b"operator-parent")
    output = _pointer("operator-output.cif", b"operator-output")
    execution = SimpleNamespace(
        prior=SimpleNamespace(reason_codes=("CHEMICAL_PRIOR_PASS",)),
        plan=SimpleNamespace(
            plan_id="plan-prompt1-operation",
            operator_id="APPLY_HOMOGENEOUS_STRAIN_V1",
            operator_spec=SimpleNamespace(
                operator_spec_id="operator-spec-prompt1-operation"
            ),
            route_sha256="7" * 64,
            status=SimpleNamespace(value="STRUCTURE_VALID"),
            parent_structure_artifact=parent,
            output_structure_artifact=output,
            validation_checks=(
                SimpleNamespace(
                    check_id="canonical_round_trip",
                    status=SimpleNamespace(value="PASS"),
                ),
            ),
        ),
    )

    projected = operator_result_from_structure_execution(
        "candidate-pd3p2s8-strain",
        execution,
    )

    assert projected.status is OperatorResultStatus.STRUCTURE_VALID
    assert projected.output_structure == output
    assert projected.evidence_level == "HEURISTIC_PRIOR_ONLY"
    assert projected.reason_codes == (
        "CHEMICAL_PRIOR_PASS",
        "canonical_round_trip:PASS",
    )


def test_prompt1_multi_candidate_route_is_deepseek_owned_and_policy_only_audits() -> None:
    hypotheses = _hypotheses()
    strained = _operator_result(
        hypotheses[0].candidate_id,
        plan_id="plan-strained-pd3p2s8",
        output=_pointer("strained-pd3p2s8.cif", b"strained"),
    )
    proposal = DeepSeekModelRouteProposal(
        goal_sha256=canonical_sha256({"goal": PROMPT1}),
        provenance=_provenance("prompt1-route-fixture-v1"),
        route_summary=(
            "Relax transformed structures, then calculate Fermi-referenced bands "
            "and orbital projections for all three hypotheses."
        ),
        tasks=(
            ProposedModelTask(
                task_id="task-strain-chgnet",
                candidate_id=hypotheses[0].candidate_id,
                model_id="chgnet-relaxation-v1",
                input_kind=ModelTaskInputKind.OPERATOR_RESULT,
                operator_result_id=strained.operator_result_id,
                requested_observables=("relaxed_structure",),
                required_evidence_level=ScientificEvidenceLevel.L2_ML_SCREENED,
                rationale="Relax the DeepSeek-proposed strain before electronic validation.",
                falsification_rule="Reject the route if structural integrity is lost.",
            ),
            ProposedModelTask(
                task_id="task-strain-hse-pdos",
                candidate_id=hypotheses[0].candidate_id,
                model_id="hse-soc-band-pdos-v1",
                input_kind=ModelTaskInputKind.OPERATOR_RESULT,
                operator_result_id=strained.operator_result_id,
                requested_observables=tuple(
                    sorted(
                        {
                            "bandwidth_le_50_mev",
                            "dispersive_band_no_fermi_crossing",
                            "flat_band_first_near_fermi",
                            "no_first_order_crossing",
                            "tm_or_ligand_hybrid_orbital_character",
                        }
                    )
                ),
                required_evidence_level=ScientificEvidenceLevel.L3_DFT_VALIDATED,
                rationale="Directly calculate every unresolved electronic constraint.",
                falsification_rule="Contradict if any frozen flat-band criterion fails.",
            ),
            ProposedModelTask(
                task_id="task-intercalated-uniham",
                candidate_id=hypotheses[1].candidate_id,
                model_id="uniham-soc-v1",
                input_kind=ModelTaskInputKind.PARENT_STRUCTURE,
                requested_observables=("soc_hamiltonian",),
                required_evidence_level=ScientificEvidenceLevel.L2_ML_SCREENED,
                rationale="Predict a SOC Hamiltonian for the intercalated hypothesis.",
                falsification_rule="Block when compatible weights or graph inputs are absent.",
            ),
            ProposedModelTask(
                task_id="task-vacancy-unknown-model",
                candidate_id=hypotheses[2].candidate_id,
                model_id="deepseek-invented-model",
                input_kind=ModelTaskInputKind.PARENT_STRUCTURE,
                requested_observables=("bandwidth_le_50_mev",),
                required_evidence_level=ScientificEvidenceLevel.L2_ML_SCREENED,
                rationale="Test whether an unregistered route can bypass the local registry.",
                falsification_rule="The deterministic policy must block unknown capabilities.",
            ),
        ),
    )
    capabilities = (
        ModelCapability(
            model_id="chgnet-relaxation-v1",
            availability=CapabilityAvailability.READY,
            weight_status=WeightStatus.READY,
            checkpoint_sha256="1" * 64,
            supported_observables=("relaxed_structure",),
            supported_elements=("Si",),
            supported_dimensionalities=(3,),
            evidence_ceiling=ScientificEvidenceLevel.L2_ML_SCREENED,
            estimated_cost_units=2,
            real_backend=True,
            benchmark_status="VALIDATED",
        ),
        ModelCapability(
            model_id="hse-soc-band-pdos-v1",
            availability=CapabilityAvailability.READY,
            weight_status=WeightStatus.NOT_REQUIRED,
            supported_observables=tuple(
                sorted(
                    {
                        "bandwidth_le_50_mev",
                        "dispersive_band_no_fermi_crossing",
                        "flat_band_first_near_fermi",
                        "no_first_order_crossing",
                        "tm_or_ligand_hybrid_orbital_character",
                    }
                )
            ),
            supported_elements=None,
            supported_dimensionalities=(2, 3),
            evidence_ceiling=ScientificEvidenceLevel.L3_DFT_VALIDATED,
            estimated_cost_units=400,
            real_backend=True,
            benchmark_status="VALIDATED",
        ),
        ModelCapability(
            model_id="uniham-soc-v1",
            availability=CapabilityAvailability.UNAVAILABLE,
            weight_status=WeightStatus.MISSING,
            supported_observables=("soc_hamiltonian",),
            supported_elements=None,
            supported_dimensionalities=(2, 3),
            evidence_ceiling=ScientificEvidenceLevel.NONE,
            estimated_cost_units=10,
            real_backend=True,
            benchmark_status="NOT_RUN",
        ),
    )

    production_audit = compile_model_task_plan(
        goal=PROMPT1,
        hypotheses=hypotheses,
        operator_results=(strained,),
        proposal=proposal,
        capabilities=capabilities,
        policy=ValidationRoutePolicy(max_cost_units=1_000),
    )
    assert all(
        "LIVE_DEEPSEEK_ROUTE_REQUIRED" in item.reason_codes
        for item in production_audit.tasks
    )

    plan = compile_model_task_plan(
        goal=PROMPT1,
        hypotheses=hypotheses,
        operator_results=(strained,),
        proposal=proposal,
        capabilities=capabilities,
        policy=ValidationRoutePolicy(
            max_cost_units=1_000,
            require_live_deepseek_reasoning=False,
        ),
    )

    by_id = {item.proposed_task.task_id: item for item in plan.tasks}
    assert len({task.proposed_task.candidate_id for task in plan.tasks}) == 3
    assert by_id["task-strain-hse-pdos"].status is RouteAuditStatus.BLOCKED
    assert "ML_ONLY_EVIDENCE_CEILING_EXCEEDED" in by_id[
        "task-strain-hse-pdos"
    ].reason_codes
    assert set(by_id["task-strain-chgnet"].reason_codes) == {
        "DIMENSIONALITY_OUTSIDE_REVIEWED_DOMAIN",
        "ELEMENT_OUTSIDE_REVIEWED_DOMAIN",
    }
    assert "MODEL_WEIGHT_NOT_READY" in by_id["task-intercalated-uniham"].reason_codes
    assert by_id["task-vacancy-unknown-model"].reason_codes == (
        "MODEL_CAPABILITY_NOT_REGISTERED",
    )
    assert plan.scientific_scope_source == "DEEPSEEK_NATIVE_REASONING"
    assert plan.deterministic_policy_role.endswith("AUDIT_ONLY")


def test_prompt1_evidence_is_written_to_memory_before_feedback_actions(
    tmp_path: Path,
) -> None:
    hypotheses = _hypotheses()
    claims = tuple(sorted(hypotheses[0].unresolved_claims))
    route = DeepSeekModelRouteProposal(
        goal_sha256=canonical_sha256({"goal": PROMPT1}),
        provenance=_provenance("prompt1-route-fixture-v2"),
        route_summary="Run one validated band-and-PDOS calculation per candidate.",
        tasks=tuple(
            ProposedModelTask(
                task_id=f"task-band-{index}",
                candidate_id=hypothesis.candidate_id,
                model_id="validated-band-pdos-ml-v1",
                input_kind=ModelTaskInputKind.PARENT_STRUCTURE,
                requested_observables=claims,
                required_evidence_level=ScientificEvidenceLevel.L2_ML_SCREENED,
                rationale="Evaluate all frozen flat-band constraints on this candidate.",
                falsification_rule="Contradict the candidate when any hard criterion fails.",
            )
            for index, hypothesis in enumerate(hypotheses, start=1)
        ),
    )
    capability = ModelCapability(
        model_id="validated-band-pdos-ml-v1",
        availability=CapabilityAvailability.READY,
        weight_status=WeightStatus.NOT_REQUIRED,
        supported_observables=claims,
        supported_elements=None,
        supported_dimensionalities=(2, 3),
        evidence_ceiling=ScientificEvidenceLevel.L2_ML_SCREENED,
        estimated_cost_units=100,
        real_backend=True,
        benchmark_status="VALIDATED",
    )
    plan = compile_model_task_plan(
        goal=PROMPT1,
        hypotheses=hypotheses,
        operator_results=(),
        proposal=route,
        capabilities=(capability,),
        policy=ValidationRoutePolicy(
            max_cost_units=1_000,
            require_live_deepseek_reasoning=False,
        ),
    )

    artifact_store = LocalArtifactStore(tmp_path / "project")
    memory_path = tmp_path / "memory.sqlite3"

    def verify(pointer: ArtifactPointerV1) -> bool:
        try:
            observed = artifact_store.inspect(pointer.uri)
        except (FileNotFoundError, ValueError):
            return False
        return observed.sha256 == pointer.sha256

    evidence = []
    verdicts = (
        ScientificEvidenceVerdict.INCONCLUSIVE,
        ScientificEvidenceVerdict.CONTRADICTS,
        ScientificEvidenceVerdict.INCONCLUSIVE,
    )
    with InspirationMemoryStore(memory_path, artifact_verifier=verify) as memory:
        snapshot = memory.snapshot("prompt1-project")
        for audited, verdict in zip(plan.tasks, verdicts, strict=True):
            task_id = audited.proposed_task.task_id
            payload = f"{task_id}:{verdict.value}".encode()
            result_ref = artifact_store.write_bytes(
                f"executions/{task_id}.json",
                payload,
                media_type="application/json",
                immutable=True,
            )
            result_pointer = ArtifactPointerV1(
                uri=result_ref.uri,
                sha256=result_ref.sha256,
                size_bytes=result_ref.size_bytes,
                media_type=result_ref.media_type,
            )
            record = scientific_evidence_from_execution(
                plan,
                ModelExecutionReceipt(
                    task_id=task_id,
                    status="SUCCEEDED",
                    verdict=verdict,
                    tested_claim_ids=claims,
                    evidence_level=ScientificEvidenceLevel.L2_ML_SCREENED,
                    result_artifact=result_pointer,
                    reason_codes=(
                        "ALL_TESTS_INCONCLUSIVE"
                        if verdict is ScientificEvidenceVerdict.INCONCLUSIVE
                        else "DISPERSIVE_BAND_CROSSES_FERMI",
                    ),
                    real_execution=True,
                ),
                artifact_verifier=verify,
            )
            evidence.append(record)
            snapshot = write_evidence_to_research_memory(
                project_id="prompt1-project",
                source_run_id="prompt1-case-cycle",
                evidence=record,
                artifact_store=artifact_store,
                memory_store=memory,
            )

        feedback = DeepSeekFeedbackProposal(
            provenance=_provenance("prompt1-feedback-fixture-v1"),
            cycle_summary=(
                "Eliminate the contradicted intercalated candidate, revise the "
                "vacancy route, and escalate the strained candidate."
            ),
            decisions=(
                DeepSeekFeedbackItem(
                    candidate_id=hypotheses[1].candidate_id,
                    action=FeedbackAction.ELIMINATE,
                    evidence_ids=(evidence[1].evidence_id,),
                    rationale=(
                        "Calibrated L2 ML evidence finds a dispersive Fermi crossing that "
                        "violates a frozen hard constraint."
                    ),
                ),
                DeepSeekFeedbackItem(
                    candidate_id=hypotheses[2].candidate_id,
                    action=FeedbackAction.MODIFY_OPERATOR,
                    evidence_ids=(evidence[2].evidence_id,),
                    rationale=(
                        "The vacancy route is inconclusive, so switch to a smaller "
                        "composition-preserving strain operation."
                    ),
                    operator_revision=CompileReasonedOperationArgsV3(
                        candidate_id=hypotheses[2].candidate_id,
                        database_candidate_id=hypotheses[2].database_candidate_ids[0],
                        operation_kind="HOMOGENEOUS_STRAIN",
                        normal_strain_x_percent=0.5,
                        normal_strain_y_percent=0.5,
                        normal_strain_z_percent=0.0,
                        shear_strain_xy_percent=0.0,
                        shear_strain_xz_percent=0.0,
                        shear_strain_yz_percent=0.0,
                        scientific_rationale=(
                            "Use minimal strain after vacancy evidence remains inconclusive."
                        ),
                        expected_mechanism="Tune Pd hopping without a defect state.",
                        chemical_prior_rationale="Composition and common valence are retained.",
                        decisive_falsification_test="Recompute band width and orbital character.",
                    ),
                ),
                DeepSeekFeedbackItem(
                    candidate_id=hypotheses[0].candidate_id,
                    action=FeedbackAction.REQUEST_HIGHER_EVIDENCE,
                    evidence_ids=(evidence[0].evidence_id,),
                    rationale=(
                        "The current calculation is inconclusive, so request an "
                        "expert-reviewed experimental comparison."
                    ),
                    escalation_tasks=(
                        ProposedModelTask(
                            task_id="task-strain-experimental-review",
                            candidate_id=hypotheses[0].candidate_id,
                            model_id="expert-experimental-review-v1",
                            input_kind=ModelTaskInputKind.PARENT_STRUCTURE,
                            requested_observables=claims,
                            required_evidence_level=ScientificEvidenceLevel.L4_EXPERIMENTAL,
                            rationale="Resolve an L2-inconclusive band assignment.",
                            falsification_rule="Reject if experiment contradicts the band claim.",
                        ),
                    ),
                ),
            ),
        )
        cycle = audit_feedback_cycle(
            hypotheses=hypotheses,
            evidence=evidence,
            memory_snapshot=snapshot,
            proposal=feedback,
            allow_non_live_reasoner=True,
        )
        unsafe_feedback = DeepSeekFeedbackProposal(
            provenance=_provenance("unsafe-dft-feedback-fixture-v1"),
            cycle_summary="Attempt a forbidden DFT escalation from an ML-only cycle.",
            decisions=(
                DeepSeekFeedbackItem(
                    candidate_id=hypotheses[0].candidate_id,
                    action=FeedbackAction.REQUEST_HIGHER_EVIDENCE,
                    evidence_ids=(evidence[0].evidence_id,),
                    rationale="Request a deliberately forbidden DFT calculation.",
                    escalation_tasks=(
                        ProposedModelTask(
                            task_id="task-forbidden-dft-escalation",
                            candidate_id=hypotheses[0].candidate_id,
                            model_id="unregistered-dft-model",
                            task_kind=ScientificTaskKind.DFT_SOC,
                            input_kind=ModelTaskInputKind.PARENT_STRUCTURE,
                            requested_observables=claims,
                            required_evidence_level=(
                                ScientificEvidenceLevel.L3_DFT_VALIDATED
                            ),
                            rationale="Exercise the ML-only feedback boundary.",
                            falsification_rule="This task must never execute.",
                        ),
                    ),
                ),
            ),
        )
        unsafe_cycle = audit_feedback_cycle(
            hypotheses=hypotheses,
            evidence=evidence,
            memory_snapshot=snapshot,
            proposal=unsafe_feedback,
            allow_non_live_reasoner=True,
            capabilities=(capability,),
            policy=ValidationRoutePolicy(require_live_deepseek_reasoning=False),
        )
        speed_result_ref = artifact_store.write_bytes(
            "executions/speed-first-none-contradiction.json",
            b"real unbenchmarked model contradiction",
            media_type="application/json",
            immutable=True,
        )
        speed_evidence = scientific_evidence_from_execution(
            plan,
            ModelExecutionReceipt(
                task_id=plan.tasks[1].proposed_task.task_id,
                status="SUCCEEDED",
                verdict=ScientificEvidenceVerdict.CONTRADICTS,
                tested_claim_ids=claims,
                evidence_level=ScientificEvidenceLevel.NONE,
                result_artifact=ArtifactPointerV1(
                    uri=speed_result_ref.uri,
                    sha256=speed_result_ref.sha256,
                    size_bytes=speed_result_ref.size_bytes,
                    media_type=speed_result_ref.media_type,
                ),
                reason_codes=("BANDWIDTH_EXCEEDS_POLICY",),
                real_execution=True,
            ),
            artifact_verifier=verify,
        )
        snapshot = write_evidence_to_research_memory(
            project_id="prompt1-project",
            source_run_id="prompt1-speed-first-cycle",
            evidence=speed_evidence,
            artifact_store=artifact_store,
            memory_store=memory,
        )
        speed_cycle = audit_feedback_cycle(
            hypotheses=hypotheses,
            evidence=(speed_evidence,),
            memory_snapshot=snapshot,
            proposal=DeepSeekFeedbackProposal(
                provenance=_provenance("speed-first-feedback-fixture-v1"),
                cycle_summary=(
                    "Eliminate one candidate from the search pool on a real hard ML "
                    "failure without promoting it to a scientific conclusion."
                ),
                decisions=(
                    DeepSeekFeedbackItem(
                        candidate_id=hypotheses[1].candidate_id,
                        action=FeedbackAction.ELIMINATE,
                        evidence_ids=(speed_evidence.evidence_id,),
                        rationale=(
                            "The real screening model reports a decisive bandwidth "
                            "threshold failure, so stop spending search budget."
                        ),
                    ),
                ),
            ),
            allow_non_live_reasoner=True,
            capabilities=(capability,),
            policy=ValidationRoutePolicy(require_live_deepseek_reasoning=False),
        )

    assert len(snapshot.downstream_outcomes) == 4
    assert all(item.evidence_id for item in snapshot.downstream_outcomes)
    assert {item.proposal.action for item in cycle.decisions} == {
        FeedbackAction.ELIMINATE,
        FeedbackAction.MODIFY_OPERATOR,
        FeedbackAction.REQUEST_HIGHER_EVIDENCE,
    }
    assert all(item.status == "ACCEPTED" for item in cycle.decisions)
    assert unsafe_cycle.decisions[0].status == "REJECTED"
    assert {
        "ESCALATION_MODEL_CAPABILITY_NOT_REGISTERED",
        "FEEDBACK_DFT_DISABLED_BY_ML_ONLY_MODE",
        "FEEDBACK_ML_ONLY_EVIDENCE_CEILING_EXCEEDED",
    } <= set(unsafe_cycle.decisions[0].reason_codes)
    assert speed_cycle.decisions[0].status == "ACCEPTED"
    assert speed_cycle.decisions[0].reason_codes == (
        "SPEED_FIRST_REAL_ML_SEARCH_POOL_ELIMINATION_ACCEPTED",
    )


def test_mock_or_unremembered_result_cannot_eliminate_candidate(tmp_path: Path) -> None:
    hypotheses = _hypotheses()[:1]
    route = DeepSeekModelRouteProposal(
        goal_sha256=canonical_sha256({"goal": PROMPT1}),
        provenance=_provenance("mock-route-v1"),
        route_summary="Run a fixture-only contract probe with no scientific evidence.",
        tasks=(
            ProposedModelTask(
                task_id="task-mock-band",
                candidate_id=hypotheses[0].candidate_id,
                model_id="fixture-band-model",
                input_kind=ModelTaskInputKind.PARENT_STRUCTURE,
                requested_observables=("bandwidth_le_50_mev",),
                required_evidence_level=ScientificEvidenceLevel.NONE,
                rationale="Exercise only the route and feedback control contracts.",
                falsification_rule="Fixture output cannot falsify a scientific candidate.",
            ),
        ),
    )
    capability = ModelCapability(
        model_id="fixture-band-model",
        availability=CapabilityAvailability.READY,
        weight_status=WeightStatus.NOT_REQUIRED,
        supported_observables=("bandwidth_le_50_mev",),
        supported_elements=None,
        supported_dimensionalities=(2,),
        evidence_ceiling=ScientificEvidenceLevel.NONE,
        estimated_cost_units=0,
        real_backend=False,
        benchmark_status="NOT_RUN",
    )
    plan = compile_model_task_plan(
        goal=PROMPT1,
        hypotheses=hypotheses,
        operator_results=(),
        proposal=route,
        capabilities=(capability,),
        policy=ValidationRoutePolicy(
            require_real_backend=False,
            require_live_deepseek_reasoning=False,
        ),
    )
    payload = b"mock-output"
    evidence = scientific_evidence_from_execution(
        plan,
        ModelExecutionReceipt(
            task_id="task-mock-band",
            status="SUCCEEDED",
            verdict=ScientificEvidenceVerdict.CONTRADICTS,
            tested_claim_ids=("bandwidth_le_50_mev",),
            evidence_level=ScientificEvidenceLevel.NONE,
            result_artifact=_pointer("mock-result.json", payload),
            reason_codes=("FIXTURE_ONLY",),
            real_execution=False,
        ),
    )
    empty_memory = InspirationMemoryStore(
        tmp_path / "empty.sqlite3", artifact_verifier=lambda _pointer: True
    )
    try:
        cycle = audit_feedback_cycle(
            hypotheses=hypotheses,
            evidence=(evidence,),
            memory_snapshot=empty_memory.snapshot("prompt1-project"),
            proposal=DeepSeekFeedbackProposal(
                provenance=_provenance("unsafe-feedback-v1"),
                cycle_summary="Attempt an unsafe elimination from fixture-only output.",
                decisions=(
                    DeepSeekFeedbackItem(
                        candidate_id=hypotheses[0].candidate_id,
                        action=FeedbackAction.ELIMINATE,
                        evidence_ids=(evidence.evidence_id,),
                        rationale="This fixture claims a contradiction but is not evidence.",
                    ),
                ),
            ),
            allow_non_live_reasoner=True,
        )
    finally:
        empty_memory.close()

    assert cycle.decisions[0].status == "REJECTED"
    assert set(cycle.decisions[0].reason_codes) == {
        "ELIMINATION_REQUIRES_REAL_L2_CONTRADICTION",
        "EVIDENCE_NOT_WRITTEN_TO_RESEARCH_MEMORY",
    }
