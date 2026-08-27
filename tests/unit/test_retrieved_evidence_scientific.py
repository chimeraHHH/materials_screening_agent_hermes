from __future__ import annotations

import json
from pathlib import Path

from material_agent.inspiration.deepseek_agent import (
    DeepSeekAgentReceiptV1,
    DeepSeekAgentResultV1,
    DeepSeekToolCallReceiptV1,
)
from material_agent.inspiration.models import canonical_sha256
from material_agent.inspiration.research_memory import InspirationMemoryStore
from material_agent.integration.retrieved_evidence_scientific import (
    DeepSeekEvidenceFusionExecutor,
    DeepSeekEvidenceFusionParameters,
    RetrievedConstraint,
    RetrievedConstraintAssessment,
    RetrievedDatabaseRecord,
    RetrievedEvidenceImportExecutor,
    RetrievedEvidenceImportParameters,
    RetrievedLiteratureRecord,
    RetrievedScientificEvidenceBundle,
)
from material_agent.integration.scientific_execution import (
    ScientificExecutorRegistry,
    execute_scientific_dag,
)
from material_agent.integration.scientific_executors import artifact_pointer_from_ref
from material_agent.integration.scientific_loop import (
    CapabilityAvailability,
    DeepSeekModelRouteProposal,
    DeepSeekReasoningProvenance,
    HypothesisCandidate,
    ModelCapability,
    ModelTaskInputKind,
    PhysicsCapabilityFeature,
    ProposedModelTask,
    RouteAuditStatus,
    ScientificArtifactKind,
    ScientificEvidenceLevel,
    ScientificEvidenceVerdict,
    ScientificTaskKind,
    ScientificValidationLoopService,
    ValidationRoutePolicy,
    WeightStatus,
)
from material_agent.retrieval.storage import LocalArtifactStore

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
MONOLAYER = (
    REPOSITORY_ROOT
    / "src/material_agent/inspiration/catalogs/flat_band_parent_catalog_v1"
    / "tis2-1t-vacuum-monolayer.cif"
)


class _UnusedSecretResolver:
    def resolve(self) -> str:
        raise AssertionError("fixture fusion agent must not resolve a secret")


class _FixtureFusionAgent:
    def __init__(self, **values: object) -> None:
        self.tools = values["tools"]

    def run(self, **values: object):
        tool = self.tools[0]
        bundle = tool.handler(tool.arguments_model())
        assert bundle["candidate_id"] == "candidate-mn-rich"
        assert "Do not request or assume DFT" in values["system_prompt"]
        final_model = values["final_model"]
        final = final_model.model_validate_json(
            json.dumps(
                {
                    "assessments": [
                        {
                            "constraint_id": "constraint-tc-above-30k",
                            "predicted_verdict": "LIKELY_FAIL",
                            "probability_pass": 0.18,
                            "evidence_ids": ["evidence-sl-only-tc"],
                            "rationale": (
                                "The cited single-phase septuple-layer sample remains "
                                "below the strict 30 K threshold."
                            ),
                            "decisive_missing_evidence": None,
                        }
                    ],
                    "joint_candidate_score": 0.18,
                    "recommended_action": "ELIMINATE",
                    "next_low_cost_actions": [
                        "Search for composition-matched experimental Tc evidence."
                    ],
                    "summary": (
                        "Retrieved experiment contradicts the strict Tc threshold for "
                        "the single-phase candidate."
                    ),
                }
            )
        )
        receipt = DeepSeekAgentReceiptV1(
            prompt_version=values["prompt_version"],
            reasoning_effort="high",
            rounds=2,
            tool_calls=(
                DeepSeekToolCallReceiptV1(
                    sequence=1,
                    round_index=1,
                    tool_call_id="fixture-fusion-tool-call",
                    tool_name=tool.name,
                    arguments_sha256="1" * 64,
                    result_sha256="2" * 64,
                    result_bytes=1_000,
                ),
            ),
            request_sha256_by_round=("3" * 64, "4" * 64),
            response_sha256_by_round=("5" * 64, "6" * 64),
            transport_attempts_by_round=(1, 1),
            transport_retry_count=0,
            prompt_tokens=500,
            completion_tokens=300,
            reasoning_tokens=200,
            total_tokens=800,
            final_response_sha256="7" * 64,
        )
        return DeepSeekAgentResultV1[final_model](final=final, receipt=receipt)


class _RouteReasoner:
    def __init__(self, proposal: DeepSeekModelRouteProposal) -> None:
        self.proposal = proposal

    def propose_route(self, **_values: object) -> DeepSeekModelRouteProposal:
        return self.proposal

    def propose_feedback(self, **_values: object):
        raise AssertionError("fixture does not request a feedback round")


def _setup(tmp_path: Path):
    store = LocalArtifactStore(tmp_path / "project")
    source = artifact_pointer_from_ref(
        store.write_bytes(
            "inputs/mn-rich.cif",
            MONOLAYER.read_bytes(),
            "chemical/x-cif",
            immutable=True,
        )
    )
    bundle = RetrievedScientificEvidenceBundle(
        candidate_id="candidate-mn-rich",
        material_name="Mn-rich layered candidate",
        formula="Mn1+xSb2-xTe4",
        hypothesis=(
            "Mn antisites may create a ferromagnetic layered topological phase."
        ),
        mechanism=(
            "Antisite moments may alter interlayer exchange while heavy Te supplies SOC."
        ),
        source_graph_sha256="8" * 64,
        constraints=(
            RetrievedConstraint(
                constraint_id="constraint-tc-above-30k",
                statement="The experimental Curie temperature must exceed 30 K.",
                hard=True,
                threshold_value=30,
                threshold_unit="K",
            ),
        ),
        database_records=(
            RetrievedDatabaseRecord(
                database_candidate_id="db-candidate-mn-rich",
                source_database="c2db",
                source_material_id="fixture-mn-rich",
                formula="MnSb2Te4",
                dimensionality=2,
                structure_sha256=source.sha256,
            ),
        ),
        literature_records=(
            RetrievedLiteratureRecord(
                evidence_id="evidence-sl-only-tc",
                evidence_scope="METADATA_OR_ABSTRACT_ONLY",
                doi="10.0000/fixture",
                published_year=2023,
                excerpt=(
                    "Single-phase septuple-layer dominated samples show a Curie "
                    "temperature of 20 to 30 K."
                ),
            ),
        ),
        deterministic_assessments=(
            RetrievedConstraintAssessment(
                constraint_id="constraint-tc-above-30k",
                verdict="FAIL",
                rationale=(
                    "The reported single-phase interval does not strictly exceed 30 K."
                ),
                evidence_ids=("evidence-sl-only-tc",),
                database_candidate_ids=("db-candidate-mn-rich",),
            ),
        ),
    )
    bundle_pointer = artifact_pointer_from_ref(
        store.write_json(
            "inputs/mn-rich-evidence-bundle.json",
            bundle.model_dump(mode="json"),
            immutable=True,
        )
    )
    hypothesis = HypothesisCandidate(
        candidate_id=bundle.candidate_id,
        material_name=bundle.material_name,
        formula=bundle.formula,
        hypothesis=bundle.hypothesis,
        mechanism=bundle.mechanism,
        database_candidate_ids=("db-candidate-mn-rich",),
        parent_structure=source,
        retrieved_evidence_bundle=bundle_pointer,
        elements=("Mn", "Sb", "Te"),
        transition_metal_elements=("Mn",),
        dimensionality=2,
        literature_evidence_ids=("evidence-sl-only-tc",),
        unresolved_claims=("constraint-tc-above-30k",),
    )
    import_task = ProposedModelTask(
        task_id="task-import-retrieved-evidence",
        candidate_id=hypothesis.candidate_id,
        model_id="local-retrieved-evidence-import-v1",
        task_kind=ScientificTaskKind.RESEARCH_EVIDENCE_IMPORT,
        input_kind=ModelTaskInputKind.PARENT_STRUCTURE,
        produced_artifact_kinds=(
            ScientificArtifactKind.RETRIEVED_EVIDENCE_BUNDLE,
        ),
        required_capability_features=(
            PhysicsCapabilityFeature.RETRIEVED_EVIDENCE_GROUNDED,
        ),
        requested_observables=("retrieved_candidate_evidence",),
        required_evidence_level=ScientificEvidenceLevel.L1_RETRIEVED,
        parameters={"bundle_pointer": bundle_pointer.model_dump(mode="json")},
        rationale="Import the exact hash-bound research evidence for this candidate.",
        falsification_rule="Block when the bundle hash or candidate lineage differs.",
    )
    fusion_parameters = DeepSeekEvidenceFusionParameters(
        target_constraint_ids=("constraint-tc-above-30k",),
        pass_probability_threshold=0.7,
        fail_probability_threshold=0.3,
        maximum_next_actions=3,
    )
    fusion_task = ProposedModelTask(
        task_id="task-deepseek-evidence-fusion",
        candidate_id=hypothesis.candidate_id,
        model_id="deepseek-evidence-fusion-v1",
        task_kind=ScientificTaskKind.DEEPSEEK_EVIDENCE_FUSION,
        input_kind=ModelTaskInputKind.PREVIOUS_TASK,
        prerequisite_task_ids=(import_task.task_id,),
        required_input_artifact_kinds=(
            ScientificArtifactKind.RETRIEVED_EVIDENCE_BUNDLE,
        ),
        produced_artifact_kinds=(
            ScientificArtifactKind.REASONED_PROPERTY_ASSESSMENT,
        ),
        required_capability_features=(
            PhysicsCapabilityFeature.PROBABILISTIC_SCIENTIFIC_REASONING,
            PhysicsCapabilityFeature.RETRIEVED_EVIDENCE_GROUNDED,
        ),
        requested_observables=("constraint-tc-above-30k",),
        required_evidence_level=ScientificEvidenceLevel.L1_RETRIEVED,
        parameters=fusion_parameters.model_dump(mode="json"),
        rationale="Fuse direct retrieved evidence into a useful probability screen.",
        falsification_rule="Contradict when a hard constraint falls below 0.3.",
    )
    goal = "screen a vdW ferromagnetic topological insulator without DFT"
    proposal = DeepSeekModelRouteProposal(
        goal_sha256=canonical_sha256({"goal": goal}),
        provenance=DeepSeekReasoningProvenance(
            prompt_version="retrieved-evidence-route-fixture-v1",
            receipt_sha256="9" * 64,
            live_call=False,
        ),
        route_summary="Import candidate evidence and fuse it with DeepSeek reasoning.",
        tasks=(import_task, fusion_task),
    )
    capabilities = (
        ModelCapability(
            model_id=import_task.model_id,
            availability=CapabilityAvailability.READY,
            weight_status=WeightStatus.NOT_REQUIRED,
            supported_observables=import_task.requested_observables,
            supported_task_kinds=(ScientificTaskKind.RESEARCH_EVIDENCE_IMPORT,),
            produced_artifact_kinds=import_task.produced_artifact_kinds,
            parameter_contract_id="retrieved-evidence-import-parameters-v1",
            parameter_schema=RetrievedEvidenceImportParameters.model_json_schema(),
            physics_features=import_task.required_capability_features,
            supported_elements=None,
            supported_dimensionalities=(2,),
            evidence_ceiling=ScientificEvidenceLevel.L1_RETRIEVED,
            estimated_cost_units=1,
            real_backend=True,
            benchmark_status="NOT_RUN",
        ),
        ModelCapability(
            model_id=fusion_task.model_id,
            availability=CapabilityAvailability.READY,
            weight_status=WeightStatus.NOT_REQUIRED,
            supported_observables=fusion_task.requested_observables,
            supported_task_kinds=(ScientificTaskKind.DEEPSEEK_EVIDENCE_FUSION,),
            accepted_artifact_kinds=fusion_task.required_input_artifact_kinds,
            required_input_artifact_kinds=fusion_task.required_input_artifact_kinds,
            produced_artifact_kinds=fusion_task.produced_artifact_kinds,
            parameter_contract_id="deepseek-evidence-fusion-parameters-v1",
            parameter_schema=DeepSeekEvidenceFusionParameters.model_json_schema(),
            physics_features=fusion_task.required_capability_features,
            supported_elements=None,
            supported_dimensionalities=(2,),
            evidence_ceiling=ScientificEvidenceLevel.L1_RETRIEVED,
            estimated_cost_units=5,
            real_backend=True,
            benchmark_status="NOT_RUN",
        ),
    )
    return store, goal, hypothesis, proposal, capabilities


def test_retrieved_evidence_import_and_deepseek_fusion_form_typed_dag(
    tmp_path: Path,
) -> None:
    store, goal, hypothesis, proposal, capabilities = _setup(tmp_path)
    with InspirationMemoryStore(
        tmp_path / "memory.sqlite3",
        artifact_verifier=lambda pointer: store.inspect(pointer.uri).sha256
        == pointer.sha256,
    ) as memory:
        service = ScientificValidationLoopService(
            project_id="retrieved-evidence-fusion-test",
            source_run_id="retrieved-evidence-fusion-test-r1",
            goal=goal,
            hypotheses=(hypothesis,),
            capabilities=capabilities,
            policy=ValidationRoutePolicy(require_live_deepseek_reasoning=False),
            artifact_store=store,
            memory_store=memory,
            reasoner=_RouteReasoner(proposal),
        )
        _route, plan = service.propose_and_audit_route(())
        assert all(item.status is RouteAuditStatus.APPROVED for item in plan.tasks)
        registry = ScientificExecutorRegistry()
        registry.register(
            "local-retrieved-evidence-import-v1",
            RetrievedEvidenceImportExecutor(store),
        )
        registry.register(
            "deepseek-evidence-fusion-v1",
            DeepSeekEvidenceFusionExecutor(
                artifact_store=store,
                secret_resolver=_UnusedSecretResolver(),
                agent_factory=_FixtureFusionAgent,
                live_call=False,
            ),
        )
        result = execute_scientific_dag(
            plan=plan,
            service=service,
            executors=registry,
        )

    assert result.all_audited_tasks_succeeded is True
    assert len(result.evidence) == 2
    fusion = next(
        item
        for item in result.evidence
        if item.task_kind is ScientificTaskKind.DEEPSEEK_EVIDENCE_FUSION
    )
    assert fusion.verdict is ScientificEvidenceVerdict.CONTRADICTS
    assert fusion.evidence_level is ScientificEvidenceLevel.L1_RETRIEVED
    assert fusion.reason_codes == ("REASONED_HARD_CONSTRAINT_FAILURE",)


def test_parameter_schema_and_bundle_lineage_fail_closed(tmp_path: Path) -> None:
    store, goal, hypothesis, proposal, capabilities = _setup(tmp_path)
    invalid_import = proposal.tasks[0].model_copy(
        update={"parameters": {"bundle_pointer": {"uri": "artifact://wrong"}}}
    )
    invalid_proposal = proposal.model_copy(
        update={"tasks": (invalid_import, proposal.tasks[1])}
    )
    with InspirationMemoryStore(
        tmp_path / "memory.sqlite3",
        artifact_verifier=lambda pointer: store.inspect(pointer.uri).sha256
        == pointer.sha256,
    ) as memory:
        service = ScientificValidationLoopService(
            project_id="retrieved-evidence-lineage-test",
            source_run_id="retrieved-evidence-lineage-test-r1",
            goal=goal,
            hypotheses=(hypothesis,),
            capabilities=capabilities,
            policy=ValidationRoutePolicy(require_live_deepseek_reasoning=False),
            artifact_store=store,
            memory_store=memory,
            reasoner=_RouteReasoner(invalid_proposal),
        )
        _route, plan = service.propose_and_audit_route(())

    first = plan.tasks[0]
    assert first.status is RouteAuditStatus.BLOCKED
    assert "PARAMETERS_DO_NOT_MATCH_CAPABILITY_SCHEMA" in first.reason_codes
    assert "RETRIEVED_EVIDENCE_BUNDLE_POINTER_INVALID" in first.reason_codes
