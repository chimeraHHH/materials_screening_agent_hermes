from __future__ import annotations

from pathlib import Path

import pytest

from material_agent.inspiration.models import ArtifactPointerV1, canonical_sha256
from material_agent.inspiration.research_memory import InspirationMemoryStore
from material_agent.integration.electronic_structure import (
    ScalarThresholdDirection,
    SurrogateCandidateEstimate,
    SurrogateCriterionEstimate,
    SurrogatePredictionProvenance,
    SurrogateProbabilityCriterionEstimate,
    SurrogateScientificPrediction,
)
from material_agent.integration.scientific_execution import (
    ScientificExecutorRegistry,
    execute_scientific_dag,
)
from material_agent.integration.scientific_executors import (
    CalibratedMLPropertyExecutor,
    LocalMagneticEnumerationExecutor,
    LocalTwoDStructureExecutor,
    artifact_pointer_from_ref,
)
from material_agent.integration.scientific_loop import (
    CapabilityAvailability,
    DeepSeekModelRouteProposal,
    DeepSeekReasoningProvenance,
    HypothesisCandidate,
    ModelCapability,
    ModelTaskInputKind,
    PhysicsCapabilityFeature,
    ProposedModelTask,
    ScientificArtifactKind,
    ScientificEvidenceLevel,
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


class _RouteReasoner:
    def __init__(self, proposal: DeepSeekModelRouteProposal) -> None:
        self.proposal = proposal

    def propose_route(self, **_values: object) -> DeepSeekModelRouteProposal:
        return self.proposal

    def propose_feedback(self, **_values: object):
        raise AssertionError("execution fixture does not request feedback")


def test_real_local_executors_publish_and_bind_artifacts(tmp_path: Path) -> None:
    store = LocalArtifactStore(tmp_path / "project")
    source = artifact_pointer_from_ref(
        store.write_bytes(
            "inputs/tis2.cif",
            MONOLAYER.read_bytes(),
            "chemical/x-cif",
            immutable=True,
        )
    )
    hypothesis = HypothesisCandidate(
        candidate_id="candidate-tis2-execution",
        material_name="TiS2 monolayer",
        formula="TiS2",
        hypothesis="A connected Ti layer requires explicit electronic validation.",
        mechanism="Ligand-mediated Ti connectivity is checked before magnetism.",
        database_candidate_ids=("database-tis2",),
        parent_structure=source,
        elements=("S", "Ti"),
        dimensionality=2,
        unresolved_claims=("connected_tm_sublattice",),
    )
    goal = "execute deterministic structure checks"
    proposal = DeepSeekModelRouteProposal(
        goal_sha256=canonical_sha256({"goal": goal}),
        provenance=DeepSeekReasoningProvenance(
            prompt_version="scientific-execution-fixture-v1",
            receipt_sha256="a" * 64,
            live_call=False,
        ),
        route_summary="Check the real CIF, then enumerate bounded magnetic seeds.",
        tasks=(
            ProposedModelTask(
                task_id="task-two-d",
                candidate_id=hypothesis.candidate_id,
                model_id="local-two-d-v1",
                task_kind=ScientificTaskKind.TWO_D_STRUCTURE_CHECK,
                input_kind=ModelTaskInputKind.PARENT_STRUCTURE,
                produced_artifact_kinds=(
                    ScientificArtifactKind.TWO_D_STRUCTURE_ASSESSMENT,
                ),
                requested_observables=("two_dimensional_structure",),
                required_evidence_level=ScientificEvidenceLevel.L1_RETRIEVED,
                parameters={
                    "contributor_elements": ["Ti"],
                    "minimum_periodic_void_gap_angstrom": 3.0,
                },
                rationale="Validate layer geometry, valence and connected contributors.",
                falsification_rule="Reject a non-2D or disconnected contributor network.",
            ),
            ProposedModelTask(
                task_id="task-magnetic-seeds",
                candidate_id=hypothesis.candidate_id,
                model_id="local-magnetic-enumerator-v1",
                task_kind=ScientificTaskKind.MAGNETIC_CONFIGURATION_ENUMERATION,
                input_kind=ModelTaskInputKind.PREVIOUS_TASK,
                prerequisite_task_ids=("task-two-d",),
                required_input_artifact_kinds=(
                    ScientificArtifactKind.TWO_D_STRUCTURE_ASSESSMENT,
                ),
                produced_artifact_kinds=(
                    ScientificArtifactKind.MAGNETIC_CONFIGURATIONS,
                ),
                requested_observables=("magnetic_configuration_seeds",),
                required_evidence_level=ScientificEvidenceLevel.L1_RETRIEVED,
                parameters={
                    "initial_moment_magnitude_mu_b": 1.0,
                    "magnetic_elements": ["Ti"],
                    "max_configurations": 8,
                },
                rationale="Enumerate deterministic starting points without claiming a ground state.",
                falsification_rule="Stop if the requested magnetic sublattice is absent.",
            ),
        ),
    )
    capabilities = (
        ModelCapability(
            model_id="local-two-d-v1",
            availability=CapabilityAvailability.READY,
            weight_status=WeightStatus.NOT_REQUIRED,
            supported_observables=("two_dimensional_structure",),
            supported_task_kinds=(ScientificTaskKind.TWO_D_STRUCTURE_CHECK,),
            produced_artifact_kinds=(
                ScientificArtifactKind.TWO_D_STRUCTURE_ASSESSMENT,
            ),
            supported_elements=("S", "Ti"),
            supported_dimensionalities=(2,),
            evidence_ceiling=ScientificEvidenceLevel.L1_RETRIEVED,
            estimated_cost_units=1,
            real_backend=True,
            benchmark_status="VALIDATED",
        ),
        ModelCapability(
            model_id="local-magnetic-enumerator-v1",
            availability=CapabilityAvailability.READY,
            weight_status=WeightStatus.NOT_REQUIRED,
            supported_observables=("magnetic_configuration_seeds",),
            supported_task_kinds=(
                ScientificTaskKind.MAGNETIC_CONFIGURATION_ENUMERATION,
            ),
            accepted_artifact_kinds=(
                ScientificArtifactKind.TWO_D_STRUCTURE_ASSESSMENT,
            ),
            produced_artifact_kinds=(
                ScientificArtifactKind.MAGNETIC_CONFIGURATIONS,
            ),
            supported_elements=("S", "Ti"),
            supported_dimensionalities=(2,),
            evidence_ceiling=ScientificEvidenceLevel.L1_RETRIEVED,
            estimated_cost_units=1,
            real_backend=True,
            benchmark_status="VALIDATED",
        ),
    )

    def verify(pointer: ArtifactPointerV1) -> bool:
        return store.exists_with_hash(pointer.uri, pointer.sha256)

    with InspirationMemoryStore(
        tmp_path / "memory.sqlite3",
        artifact_verifier=verify,
    ) as memory:
        service = ScientificValidationLoopService(
            project_id="scientific-execution-project",
            source_run_id="scientific-execution-run",
            goal=goal,
            hypotheses=(hypothesis,),
            capabilities=capabilities,
            policy=ValidationRoutePolicy(
                require_live_deepseek_reasoning=False,
            ),
            artifact_store=store,
            memory_store=memory,
            reasoner=_RouteReasoner(proposal),
        )
        _proposal, plan = service.propose_and_audit_route(())
        registry = ScientificExecutorRegistry()
        registry.register("local-two-d-v1", LocalTwoDStructureExecutor(store))
        registry.register(
            "local-magnetic-enumerator-v1",
            LocalMagneticEnumerationExecutor(store),
        )
        result = execute_scientific_dag(
            plan=plan,
            service=service,
            executors=registry,
        )
        snapshot = memory.snapshot("scientific-execution-project")

    assert result.all_audited_tasks_succeeded
    assert len(result.evidence) == 2
    assert result.evidence[0].real_execution
    assert result.evidence[1].consumed_artifact_ids == (
        result.evidence[0].produced_artifacts[0].artifact_id,
    )
    assert len(snapshot.downstream_outcomes) == 2


@pytest.mark.parametrize(
    "task_kind",
    (
        ScientificTaskKind.ML_PROPERTY_PREDICTION,
        ScientificTaskKind.ML_ENSEMBLE_VALIDATION,
    ),
)
def test_calibrated_property_executor_produces_pure_ml_l2_evidence(
    tmp_path: Path,
    task_kind: ScientificTaskKind,
) -> None:
    store = LocalArtifactStore(tmp_path / "project")
    source = artifact_pointer_from_ref(
        store.write_bytes(
            "inputs/tis2.cif",
            MONOLAYER.read_bytes(),
            "chemical/x-cif",
            immutable=True,
        )
    )
    hypothesis = HypothesisCandidate(
        candidate_id="candidate-pure-ml",
        material_name="TiS2 monolayer",
        formula="TiS2",
        hypothesis="A calibrated ML ensemble may prioritize this layered candidate.",
        mechanism="Structure-to-property surrogates estimate target observables quickly.",
        database_candidate_ids=("database-tis2",),
        parent_structure=source,
        elements=("S", "Ti"),
        dimensionality=2,
        unresolved_claims=("flat_band_and_topology_proxy",),
    )
    goal = "rank candidates with no DFT"
    task = ProposedModelTask(
        task_id="task-pure-ml-properties",
        candidate_id=hypothesis.candidate_id,
        model_id="calibrated-property-model-v1",
        task_kind=task_kind,
        input_kind=ModelTaskInputKind.PARENT_STRUCTURE,
        produced_artifact_kinds=(
            ScientificArtifactKind.CALIBRATED_PROPERTY_ASSESSMENT,
            ScientificArtifactKind.ML_PROPERTY_PREDICTIONS,
        ),
        required_capability_features=(
            PhysicsCapabilityFeature.CALIBRATED_UNCERTAINTY,
        ),
        requested_observables=("flat_band_width", "topological_insulator"),
        required_evidence_level=ScientificEvidenceLevel.L2_ML_SCREENED,
        rationale="Use calibrated direct-ML outputs for fast candidate ranking.",
        falsification_rule="Reject only when calibrated intervals clearly fail.",
    )
    proposal = DeepSeekModelRouteProposal(
        goal_sha256=canonical_sha256({"goal": goal}),
        provenance=DeepSeekReasoningProvenance(
            prompt_version="pure-ml-execution-fixture-v1",
            receipt_sha256="a" * 64,
            live_call=False,
        ),
        route_summary="Use a benchmarked direct-ML property model without DFT.",
        tasks=(task,),
    )
    capability = ModelCapability(
        model_id="calibrated-property-model-v1",
        availability=CapabilityAvailability.READY,
        weight_status=WeightStatus.READY,
        checkpoint_sha256="b" * 64,
        supported_observables=("flat_band_width", "topological_insulator"),
        supported_task_kinds=(task_kind,),
        produced_artifact_kinds=(
            ScientificArtifactKind.CALIBRATED_PROPERTY_ASSESSMENT,
            ScientificArtifactKind.ML_PROPERTY_PREDICTIONS,
        ),
        physics_features=(PhysicsCapabilityFeature.CALIBRATED_UNCERTAINTY,),
        supported_elements=("S", "Ti"),
        supported_dimensionalities=(2,),
        evidence_ceiling=ScientificEvidenceLevel.L2_ML_SCREENED,
        estimated_cost_units=2,
        real_backend=True,
        benchmark_status="VALIDATED",
    )

    def predict(_binding) -> SurrogateScientificPrediction:
        return SurrogateScientificPrediction(
            estimate=SurrogateCandidateEstimate(
                candidate_id=hypothesis.candidate_id,
                priority_score=1.0,
                in_validated_domain=True,
                criteria=(
                    SurrogateCriterionEstimate(
                        criterion_id="flat_band_width_ev",
                        predicted_value=0.030,
                        calibrated_absolute_error=0.010,
                        threshold=0.050,
                        direction=ScalarThresholdDirection.MAXIMUM,
                    ),
                ),
                probability_criteria=(
                    SurrogateProbabilityCriterionEstimate(
                        criterion_id="topological_insulator_probability",
                        positive_probability=0.94,
                        calibrated_absolute_error=0.04,
                        minimum_positive_probability=0.85,
                    ),
                ),
            ),
            provenance=SurrogatePredictionProvenance(
                model_id=task.model_id,
                checkpoint_sha256="b" * 64,
                benchmark_id="two-d-heldout-v1",
                benchmark_report_sha256="c" * 64,
                environment_fingerprint_sha256="d" * 64,
                device="cuda:0",
            ),
        )

    def verify(pointer: ArtifactPointerV1) -> bool:
        return store.exists_with_hash(pointer.uri, pointer.sha256)

    with InspirationMemoryStore(
        tmp_path / "memory.sqlite3",
        artifact_verifier=verify,
    ) as memory:
        service = ScientificValidationLoopService(
            project_id="pure-ml-project",
            source_run_id="pure-ml-run",
            goal=goal,
            hypotheses=(hypothesis,),
            capabilities=(capability,),
            policy=ValidationRoutePolicy(require_live_deepseek_reasoning=False),
            artifact_store=store,
            memory_store=memory,
            reasoner=_RouteReasoner(proposal),
        )
        _proposal, plan = service.propose_and_audit_route(())
        registry = ScientificExecutorRegistry()
        registry.register(
            task.model_id,
            CalibratedMLPropertyExecutor(store, predict),
        )
        result = execute_scientific_dag(
            plan=plan,
            service=service,
            executors=registry,
        )

    assert result.all_audited_tasks_succeeded
    assert result.evidence[0].evidence_level is ScientificEvidenceLevel.L2_ML_SCREENED
    assert {item.kind for item in result.evidence[0].produced_artifacts} == {
        ScientificArtifactKind.CALIBRATED_PROPERTY_ASSESSMENT,
        ScientificArtifactKind.ML_PROPERTY_PREDICTIONS,
    }
