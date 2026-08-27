from __future__ import annotations

import hashlib
from pathlib import Path

from pymatgen.core import Structure

from material_agent.inspiration.models import ArtifactPointerV1, canonical_sha256
from material_agent.integration.electronic_structure import (
    BandOrbitalProjection,
    ExchangeTcInput,
    ExchangeTcPolicy,
    FlatBandAnalysisInput,
    FlatBandPolicy,
    MagneticEnumerationPolicy,
    ParsedHamGNNBandStructure,
    ScalarThresholdDirection,
    SurrogateCandidateEstimate,
    SurrogateCriterionEstimate,
    SurrogateProbabilityCriterionEstimate,
    SurrogateTriageDisposition,
    SurrogateTriagePolicy,
    TopologyAnalysisInput,
    TopologyPolicy,
    TwoDStructurePolicy,
    ValidationVerdict,
    assess_flat_band,
    assess_topology,
    assess_two_dimensional_structure,
    enumerate_collinear_magnetic_configurations,
    estimate_exchange_tc,
    parse_hamgnn_band_dat,
    triage_surrogate_candidates,
)
from material_agent.integration.scientific_loop import (
    CapabilityAvailability,
    DeepSeekModelRouteProposal,
    DeepSeekReasoningProvenance,
    HamiltonianBenchmark,
    HamiltonianInputMode,
    HamiltonianInputOrigin,
    HamiltonianModelApplicability,
    HamiltonianSocMode,
    HypothesisCandidate,
    ModelCapability,
    ModelExecutionReceipt,
    ModelTaskInputKind,
    PhysicsCapabilityFeature,
    ProposedModelTask,
    RouteAuditStatus,
    ScientificArtifactKind,
    ScientificEvidenceLevel,
    ScientificEvidenceVerdict,
    ScientificTaskKind,
    ValidationRoutePolicy,
    WeightStatus,
    compile_model_task_plan,
    make_scientific_task_artifact,
    resolve_task_execution_binding,
    scientific_evidence_from_execution,
)

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
MONOLAYER = (
    REPOSITORY_ROOT
    / "src/material_agent/inspiration/catalogs/flat_band_parent_catalog_v1"
    / "tis2-1t-vacuum-monolayer.cif"
)


def _pointer(name: str) -> ArtifactPointerV1:
    payload = name.encode()
    return ArtifactPointerV1(
        uri=f"artifact://electronic-structure/{name}",
        sha256=hashlib.sha256(payload).hexdigest(),
        size_bytes=len(payload),
        media_type="application/json",
    )


def _hypothesis() -> HypothesisCandidate:
    return HypothesisCandidate(
        candidate_id="candidate-tis2-dag",
        material_name="TiS2 monolayer",
        formula="TiS2",
        hypothesis="A connected Ti-ligand layer may host a narrow band near Fermi.",
        mechanism="Ligand-mediated hopping can create a connected narrow-band manifold.",
        database_candidate_ids=("database-tis2",),
        parent_structure=_pointer("source.cif"),
        elements=("S", "Ti"),
        dimensionality=2,
        unresolved_claims=("bandwidth_le_50_mev",),
    )


def _provenance() -> DeepSeekReasoningProvenance:
    return DeepSeekReasoningProvenance(
        prompt_version="typed-dag-fixture-v1",
        receipt_sha256="a" * 64,
        live_call=False,
    )


def test_typed_dag_consumes_real_predecessor_artifact() -> None:
    hypothesis = _hypothesis()
    route = DeepSeekModelRouteProposal(
        goal_sha256=canonical_sha256({"goal": "validate a 2D electronic structure"}),
        provenance=_provenance(),
        route_summary="Check the 2D structure and then consume that assessment for relaxation.",
        tasks=(
            ProposedModelTask(
                task_id="task-2d-check",
                candidate_id=hypothesis.candidate_id,
                model_id="local-2d-validator-v1",
                task_kind=ScientificTaskKind.TWO_D_STRUCTURE_CHECK,
                input_kind=ModelTaskInputKind.PARENT_STRUCTURE,
                produced_artifact_kinds=(
                    ScientificArtifactKind.TWO_D_STRUCTURE_ASSESSMENT,
                ),
                requested_observables=("two_dimensional_structure",),
                required_evidence_level=ScientificEvidenceLevel.L1_RETRIEVED,
                parameters={"minimum_periodic_void_gap_angstrom": 3.0},
                rationale="Establish dimensionality and connectivity before relaxation.",
                falsification_rule="Stop if the candidate is not a connected 2D layer.",
            ),
            ProposedModelTask(
                task_id="task-ml-relax",
                candidate_id=hypothesis.candidate_id,
                model_id="reviewed-2d-relaxer-v1",
                task_kind=ScientificTaskKind.ML_PRE_RELAXATION,
                input_kind=ModelTaskInputKind.PREVIOUS_TASK,
                prerequisite_task_ids=("task-2d-check",),
                required_input_artifact_kinds=(
                    ScientificArtifactKind.TWO_D_STRUCTURE_ASSESSMENT,
                ),
                produced_artifact_kinds=(ScientificArtifactKind.RELAXED_STRUCTURE,),
                required_capability_features=(
                    PhysicsCapabilityFeature.STRUCTURE_RELAXATION,
                ),
                requested_observables=("relaxed_structure",),
                required_evidence_level=ScientificEvidenceLevel.L2_ML_SCREENED,
                parameters={"force_tolerance_ev_angstrom": 0.05},
                rationale="Relax only after the typed two-dimensional check passes.",
                falsification_rule="Stop when structure QC or convergence fails.",
            ),
        ),
    )
    capabilities = (
        ModelCapability(
            model_id="local-2d-validator-v1",
            availability=CapabilityAvailability.READY,
            weight_status=WeightStatus.NOT_REQUIRED,
            supported_observables=("two_dimensional_structure",),
            supported_task_kinds=(ScientificTaskKind.TWO_D_STRUCTURE_CHECK,),
            produced_artifact_kinds=(
                ScientificArtifactKind.TWO_D_STRUCTURE_ASSESSMENT,
            ),
            supported_elements=None,
            supported_dimensionalities=(2,),
            evidence_ceiling=ScientificEvidenceLevel.L1_RETRIEVED,
            estimated_cost_units=1,
            real_backend=True,
            benchmark_status="VALIDATED",
        ),
        ModelCapability(
            model_id="reviewed-2d-relaxer-v1",
            availability=CapabilityAvailability.READY,
            weight_status=WeightStatus.READY,
            checkpoint_sha256="b" * 64,
            supported_observables=("relaxed_structure",),
            supported_task_kinds=(ScientificTaskKind.ML_PRE_RELAXATION,),
            accepted_artifact_kinds=(
                ScientificArtifactKind.TWO_D_STRUCTURE_ASSESSMENT,
            ),
            produced_artifact_kinds=(ScientificArtifactKind.RELAXED_STRUCTURE,),
            physics_features=(PhysicsCapabilityFeature.STRUCTURE_RELAXATION,),
            supported_elements=("S", "Ti"),
            supported_dimensionalities=(2,),
            evidence_ceiling=ScientificEvidenceLevel.L2_ML_SCREENED,
            estimated_cost_units=10,
            real_backend=True,
            benchmark_status="VALIDATED",
        ),
    )
    plan = compile_model_task_plan(
        goal="validate a 2D electronic structure",
        hypotheses=(hypothesis,),
        operator_results=(),
        proposal=route,
        capabilities=capabilities,
        policy=ValidationRoutePolicy(require_live_deepseek_reasoning=False),
    )
    assert all(item.status is RouteAuditStatus.APPROVED for item in plan.tasks)
    produced = make_scientific_task_artifact(
        candidate_id=hypothesis.candidate_id,
        producer_task_id="task-2d-check",
        kind=ScientificArtifactKind.TWO_D_STRUCTURE_ASSESSMENT,
        pointer=_pointer("two-d-assessment.json"),
        is_mock=False,
    )
    evidence = scientific_evidence_from_execution(
        plan,
        ModelExecutionReceipt(
            task_id="task-2d-check",
            status="SUCCEEDED",
            verdict=ScientificEvidenceVerdict.SUPPORTS,
            tested_claim_ids=("two_dimensional_structure",),
            evidence_level=ScientificEvidenceLevel.L1_RETRIEVED,
            result_artifact=_pointer("two-d-manifest.json"),
            produced_artifacts=(produced,),
            reason_codes=("TWO_D_STRUCTURE_POLICY_PASSED",),
            real_execution=True,
        ),
        artifact_verifier=lambda _pointer: True,
    )
    binding = resolve_task_execution_binding(
        plan=plan,
        task_id="task-ml-relax",
        prior_evidence=(evidence,),
    )
    assert binding.consumed_artifacts == (produced,)
    assert binding.source_structure == hypothesis.parent_structure


def test_soc_task_is_blocked_without_explicit_soc_feature() -> None:
    hypothesis = _hypothesis()
    route = DeepSeekModelRouteProposal(
        goal_sha256=canonical_sha256({"goal": "validate SOC"}),
        provenance=_provenance(),
        route_summary="Request an explicitly spin-orbit-coupled electronic calculation.",
        tasks=(
            ProposedModelTask(
                task_id="task-soc",
                candidate_id=hypothesis.candidate_id,
                model_id="non-soc-only-dft",
                task_kind=ScientificTaskKind.DFT_SOC,
                input_kind=ModelTaskInputKind.PARENT_STRUCTURE,
                produced_artifact_kinds=(ScientificArtifactKind.SOC_BAND_STRUCTURE,),
                required_capability_features=(PhysicsCapabilityFeature.SOC_EXPLICIT,),
                requested_observables=("soc_band_structure",),
                required_evidence_level=ScientificEvidenceLevel.L3_DFT_VALIDATED,
                rationale="SOC bands are required before topological analysis.",
                falsification_rule="Block when SOC is not explicit in the backend.",
            ),
        ),
    )
    capability = ModelCapability(
        model_id="non-soc-only-dft",
        availability=CapabilityAvailability.READY,
        weight_status=WeightStatus.NOT_REQUIRED,
        supported_observables=("soc_band_structure",),
        supported_task_kinds=(ScientificTaskKind.DFT_SOC,),
        produced_artifact_kinds=(ScientificArtifactKind.SOC_BAND_STRUCTURE,),
        supported_elements=None,
        supported_dimensionalities=(2,),
        evidence_ceiling=ScientificEvidenceLevel.L3_DFT_VALIDATED,
        estimated_cost_units=100,
        real_backend=True,
        benchmark_status="VALIDATED",
    )
    plan = compile_model_task_plan(
        goal="validate SOC",
        hypotheses=(hypothesis,),
        operator_results=(),
        proposal=route,
        capabilities=(capability,),
        policy=ValidationRoutePolicy(require_live_deepseek_reasoning=False),
    )
    assert plan.tasks[0].status is RouteAuditStatus.BLOCKED
    assert "REQUIRED_PHYSICS_FEATURE_UNAVAILABLE" in plan.tasks[0].reason_codes


def test_ml_only_mode_blocks_dft_even_when_backend_is_ready() -> None:
    hypothesis = _hypothesis()
    route = DeepSeekModelRouteProposal(
        goal_sha256=canonical_sha256({"goal": "no DFT"}),
        provenance=_provenance(),
        route_summary="Try a DFT task against an explicitly ML-only runtime policy.",
        tasks=(
            ProposedModelTask(
                task_id="task-forbidden-dft",
                candidate_id=hypothesis.candidate_id,
                model_id="ready-dft",
                task_kind=ScientificTaskKind.DFT_NON_SOC,
                input_kind=ModelTaskInputKind.PARENT_STRUCTURE,
                produced_artifact_kinds=(ScientificArtifactKind.DFT_TOTAL_ENERGY,),
                required_capability_features=(
                    PhysicsCapabilityFeature.SELF_CONSISTENT_TOTAL_ENERGY,
                ),
                requested_observables=("total_energy",),
                required_evidence_level=ScientificEvidenceLevel.L3_DFT_VALIDATED,
                rationale="This task must be rejected by the user-selected ML-only mode.",
                falsification_rule="Any approval would violate the no-DFT runtime policy.",
            ),
        ),
    )
    capability = ModelCapability(
        model_id="ready-dft",
        availability=CapabilityAvailability.READY,
        weight_status=WeightStatus.NOT_REQUIRED,
        supported_observables=("total_energy",),
        supported_task_kinds=(ScientificTaskKind.DFT_NON_SOC,),
        produced_artifact_kinds=(ScientificArtifactKind.DFT_TOTAL_ENERGY,),
        physics_features=(PhysicsCapabilityFeature.SELF_CONSISTENT_TOTAL_ENERGY,),
        supported_elements=None,
        supported_dimensionalities=(2,),
        evidence_ceiling=ScientificEvidenceLevel.L3_DFT_VALIDATED,
        estimated_cost_units=100,
        real_backend=True,
        benchmark_status="VALIDATED",
    )
    plan = compile_model_task_plan(
        goal="no DFT",
        hypotheses=(hypothesis,),
        operator_results=(),
        proposal=route,
        capabilities=(capability,),
        policy=ValidationRoutePolicy(
            require_live_deepseek_reasoning=False,
        ),
    )
    reasons = set(plan.tasks[0].reason_codes)
    assert "DFT_DISABLED_BY_ML_ONLY_MODE" in reasons
    assert "ML_ONLY_EVIDENCE_CEILING_EXCEEDED" in reasons


def test_ml_only_mode_blocks_runtime_electronic_preprocessing() -> None:
    hypothesis = _hypothesis()
    route = DeepSeekModelRouteProposal(
        goal_sha256=canonical_sha256({"goal": "pure ML"}),
        provenance=_provenance(),
        route_summary="Attempt runtime OpenMX graph preparation in pure-ML mode.",
        tasks=(
            ProposedModelTask(
                task_id="task-runtime-graph",
                candidate_id=hypothesis.candidate_id,
                model_id="openmx-graph-generator",
                task_kind=ScientificTaskKind.HAMILTONIAN_GRAPH_PREPARATION,
                input_kind=ModelTaskInputKind.PARENT_STRUCTURE,
                produced_artifact_kinds=(
                    ScientificArtifactKind.NON_SOC_HAMILTONIAN_GRAPH,
                ),
                requested_observables=("hamiltonian_graph",),
                required_evidence_level=ScientificEvidenceLevel.L1_RETRIEVED,
                rationale="This is intentionally forbidden by the no-DFT policy.",
                falsification_rule="Approval would violate pure-ML execution mode.",
            ),
        ),
    )
    capability = ModelCapability(
        model_id="openmx-graph-generator",
        availability=CapabilityAvailability.READY,
        weight_status=WeightStatus.NOT_REQUIRED,
        supported_observables=("hamiltonian_graph",),
        supported_task_kinds=(ScientificTaskKind.HAMILTONIAN_GRAPH_PREPARATION,),
        produced_artifact_kinds=(
            ScientificArtifactKind.NON_SOC_HAMILTONIAN_GRAPH,
        ),
        supported_elements=None,
        supported_dimensionalities=(2,),
        evidence_ceiling=ScientificEvidenceLevel.L1_RETRIEVED,
        estimated_cost_units=5,
        real_backend=True,
        benchmark_status="VALIDATED",
    )
    plan = compile_model_task_plan(
        goal="pure ML",
        hypotheses=(hypothesis,),
        operator_results=(),
        proposal=route,
        capabilities=(capability,),
        policy=ValidationRoutePolicy(require_live_deepseek_reasoning=False),
    )
    assert plan.tasks[0].status is RouteAuditStatus.BLOCKED
    assert (
        "RUNTIME_ELECTRONIC_PREPROCESSING_DISABLED_BY_ML_ONLY_MODE"
        in plan.tasks[0].reason_codes
    )


def test_surrogate_triage_spends_budget_on_ambiguous_and_ood_candidates() -> None:
    def candidate(
        candidate_id: str,
        value: float,
        error: float,
        score: float,
        *,
        in_domain: bool = True,
    ) -> SurrogateCandidateEstimate:
        return SurrogateCandidateEstimate(
            candidate_id=candidate_id,
            priority_score=score,
            in_validated_domain=in_domain,
            criteria=(
                SurrogateCriterionEstimate(
                    criterion_id="flat_band_width_ev",
                    predicted_value=value,
                    calibrated_absolute_error=error,
                    threshold=0.05,
                    direction=ScalarThresholdDirection.MAXIMUM,
                ),
            ),
        )

    result = triage_surrogate_candidates(
        (
            candidate("clear-pass", 0.02, 0.005, 0.90),
            candidate("ambiguous", 0.048, 0.01, 0.80),
            candidate("clear-fail", 0.08, 0.01, 0.70),
            candidate("out-of-domain", 0.03, 0.005, 0.95, in_domain=False),
        ),
        SurrogateTriagePolicy(
            ensemble_validation_fraction=0.5,
            max_ensemble_candidates=2,
        ),
    )
    by_id = {item.candidate_id: item.disposition for item in result.decisions}
    assert result.ensemble_candidate_ids == ("ambiguous", "out-of-domain")
    assert by_id["clear-pass"] is SurrogateTriageDisposition.RETAIN_AT_L2
    assert by_id["clear-fail"] is SurrogateTriageDisposition.REJECT_AT_L2
    assert (
        by_id["ambiguous"]
        is SurrogateTriageDisposition.ESCALATE_TO_ML_ENSEMBLE
    )


def test_surrogate_triage_combines_scalar_and_probability_intervals() -> None:
    candidates = (
        SurrogateCandidateEstimate(
            candidate_id="clear-pure-ml-pass",
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
                    positive_probability=0.92,
                    calibrated_absolute_error=0.05,
                    minimum_positive_probability=0.85,
                ),
            ),
        ),
        SurrogateCandidateEstimate(
            candidate_id="ambiguous-pure-ml",
            priority_score=2.0,
            in_validated_domain=True,
            probability_criteria=(
                SurrogateProbabilityCriterionEstimate(
                    criterion_id="ferromagnetic_tc_above_30k_probability",
                    positive_probability=0.83,
                    calibrated_absolute_error=0.08,
                    minimum_positive_probability=0.85,
                ),
            ),
        ),
    )
    result = triage_surrogate_candidates(
        candidates,
        SurrogateTriagePolicy(
            ensemble_validation_fraction=0.5,
            max_ensemble_candidates=1,
        ),
    )
    decisions = {item.candidate_id: item for item in result.decisions}
    assert (
        decisions["clear-pure-ml-pass"].disposition
        is SurrogateTriageDisposition.RETAIN_AT_L2
    )
    assert (
        decisions["ambiguous-pure-ml"].disposition
        is SurrogateTriageDisposition.ESCALATE_TO_ML_ENSEMBLE
    )


def _hamiltonian_benchmark(
    *,
    band_error_ev: float = 0.008,
    soc_gap_error_ev: float | None = None,
) -> HamiltonianBenchmark:
    return HamiltonianBenchmark(
        benchmark_id="benchmark-deeph-tis2-v1",
        report_artifact=_pointer("deeph-benchmark.json"),
        held_out_structure_count=32,
        max_band_energy_error_ev=band_error_ev,
        max_flat_band_width_error_ev=0.006,
        band_ordering_accuracy=1.0,
        crossing_classification_accuracy=1.0,
        orbital_weight_mae=0.02,
        max_soc_gap_error_ev=soc_gap_error_ev,
    )


def _learned_hamiltonian_capability(
    *,
    benchmark: HamiltonianBenchmark,
) -> ModelCapability:
    return ModelCapability(
        model_id="deeph-tis2-non-soc-v1",
        availability=CapabilityAvailability.READY,
        weight_status=WeightStatus.READY,
        checkpoint_sha256="d" * 64,
        supported_observables=("non_soc_hamiltonian",),
        supported_task_kinds=(ScientificTaskKind.ML_HAMILTONIAN_NON_SOC,),
        accepted_artifact_kinds=(
            ScientificArtifactKind.OVERLAP_MATRIX,
            ScientificArtifactKind.RELAXED_STRUCTURE,
        ),
        produced_artifact_kinds=(
            ScientificArtifactKind.ML_NON_SOC_HAMILTONIAN,
        ),
        physics_features=(
            PhysicsCapabilityFeature.BASIS_IDENTITY_FROZEN,
            PhysicsCapabilityFeature.HAMILTONIAN_AVAILABLE,
            PhysicsCapabilityFeature.LEARNED_HAMILTONIAN,
            PhysicsCapabilityFeature.OVERLAP_MATRIX_REQUIRED,
        ),
        supported_elements=("S", "Ti"),
        supported_dimensionalities=(2,),
        evidence_ceiling=ScientificEvidenceLevel.L2_ML_SCREENED,
        estimated_cost_units=10,
        real_backend=True,
        benchmark_status="VALIDATED",
        hamiltonian_applicability=HamiltonianModelApplicability(
            interface="openmx",
            basis_id="openmx-tis2-basis-v1",
            dft_software_version="openmx-3.9.9",
            training_domain_id="tis2-strain-domain-v1",
            training_domain_sha256="e" * 64,
            soc_mode=HamiltonianSocMode.NON_SOC,
            supported_magnetic_modes=("COLLINEAR", "NONMAGNETIC"),
            benchmark=benchmark,
        ),
    )


def _deeph_route() -> DeepSeekModelRouteProposal:
    hypothesis = _hypothesis()
    return DeepSeekModelRouteProposal(
        goal_sha256=canonical_sha256({"goal": "screen a learned Hamiltonian"}),
        provenance=_provenance(),
        route_summary=(
            "Generate a compatible overlap and use a benchmarked non-SOC "
            "learned Hamiltonian only as an L2 screen."
        ),
        tasks=(
            ProposedModelTask(
                task_id="task-relax",
                candidate_id=hypothesis.candidate_id,
                model_id="relax-fixture",
                task_kind=ScientificTaskKind.ML_PRE_RELAXATION,
                input_kind=ModelTaskInputKind.PARENT_STRUCTURE,
                produced_artifact_kinds=(ScientificArtifactKind.RELAXED_STRUCTURE,),
                required_capability_features=(
                    PhysicsCapabilityFeature.STRUCTURE_RELAXATION,
                ),
                requested_observables=("relaxed_structure",),
                required_evidence_level=ScientificEvidenceLevel.L2_ML_SCREENED,
                rationale="Freeze the relaxed geometry used by overlap and DeepH.",
                falsification_rule="Stop if structure relaxation quality control fails.",
            ),
            ProposedModelTask(
                task_id="task-overlap",
                candidate_id=hypothesis.candidate_id,
                model_id="openmx-overlap-v1",
                task_kind=ScientificTaskKind.OVERLAP_MATRIX_GENERATION,
                input_kind=ModelTaskInputKind.PREVIOUS_TASK,
                prerequisite_task_ids=("task-relax",),
                required_input_artifact_kinds=(
                    ScientificArtifactKind.RELAXED_STRUCTURE,
                ),
                produced_artifact_kinds=(ScientificArtifactKind.OVERLAP_MATRIX,),
                requested_observables=("overlap_matrix",),
                required_evidence_level=ScientificEvidenceLevel.L1_RETRIEVED,
                rationale="Generate overlap in the same basis as the model bundle.",
                falsification_rule="Stop on basis, structure, or interface mismatch.",
            ),
            ProposedModelTask(
                task_id="task-deeph",
                candidate_id=hypothesis.candidate_id,
                model_id="deeph-tis2-non-soc-v1",
                task_kind=ScientificTaskKind.ML_HAMILTONIAN_NON_SOC,
                input_kind=ModelTaskInputKind.PREVIOUS_TASK,
                prerequisite_task_ids=("task-overlap", "task-relax"),
                required_input_artifact_kinds=(
                    ScientificArtifactKind.OVERLAP_MATRIX,
                    ScientificArtifactKind.RELAXED_STRUCTURE,
                ),
                produced_artifact_kinds=(
                    ScientificArtifactKind.ML_NON_SOC_HAMILTONIAN,
                ),
                required_capability_features=(
                    PhysicsCapabilityFeature.BASIS_IDENTITY_FROZEN,
                    PhysicsCapabilityFeature.HAMILTONIAN_AVAILABLE,
                    PhysicsCapabilityFeature.LEARNED_HAMILTONIAN,
                    PhysicsCapabilityFeature.OVERLAP_MATRIX_REQUIRED,
                ),
                requested_observables=("non_soc_hamiltonian",),
                required_evidence_level=ScientificEvidenceLevel.L2_ML_SCREENED,
                rationale="Use the held-out benchmarked Hamiltonian as an L2 screen.",
                falsification_rule="Block if any domain or quantitative error gate fails.",
            ),
        ),
    )


def _deeph_supporting_capabilities() -> tuple[ModelCapability, ModelCapability]:
    return (
        ModelCapability(
            model_id="relax-fixture",
            availability=CapabilityAvailability.READY,
            weight_status=WeightStatus.READY,
            checkpoint_sha256="1" * 64,
            supported_observables=("relaxed_structure",),
            supported_task_kinds=(ScientificTaskKind.ML_PRE_RELAXATION,),
            produced_artifact_kinds=(ScientificArtifactKind.RELAXED_STRUCTURE,),
            physics_features=(PhysicsCapabilityFeature.STRUCTURE_RELAXATION,),
            supported_elements=("S", "Ti"),
            supported_dimensionalities=(2,),
            evidence_ceiling=ScientificEvidenceLevel.L2_ML_SCREENED,
            estimated_cost_units=10,
            real_backend=True,
            benchmark_status="VALIDATED",
        ),
        ModelCapability(
            model_id="openmx-overlap-v1",
            availability=CapabilityAvailability.READY,
            weight_status=WeightStatus.NOT_REQUIRED,
            supported_observables=("overlap_matrix",),
            supported_task_kinds=(ScientificTaskKind.OVERLAP_MATRIX_GENERATION,),
            accepted_artifact_kinds=(ScientificArtifactKind.RELAXED_STRUCTURE,),
            produced_artifact_kinds=(ScientificArtifactKind.OVERLAP_MATRIX,),
            supported_elements=("S", "Ti"),
            supported_dimensionalities=(2,),
            evidence_ceiling=ScientificEvidenceLevel.L1_RETRIEVED,
            estimated_cost_units=5,
            real_backend=True,
            benchmark_status="VALIDATED",
        ),
    )


def test_deeph_route_requires_precomputed_inputs_and_quantitative_accuracy() -> None:
    support = _deeph_supporting_capabilities()
    approved = compile_model_task_plan(
        goal="screen a learned Hamiltonian",
        hypotheses=(_hypothesis(),),
        operator_results=(),
        proposal=_deeph_route(),
        capabilities=(
            *support,
            _learned_hamiltonian_capability(
                benchmark=_hamiltonian_benchmark()
            ),
        ),
        policy=ValidationRoutePolicy(require_live_deepseek_reasoning=False),
    )
    assert approved.tasks[0].status is RouteAuditStatus.APPROVED
    assert "RUNTIME_ELECTRONIC_PREPROCESSING_DISABLED_BY_ML_ONLY_MODE" in (
        approved.tasks[1].reason_codes
    )
    assert "PRECOMPUTED_ELECTRONIC_INPUT_REQUIRED_BY_ML_ONLY_MODE" in (
        approved.tasks[2].reason_codes
    )

    blocked = compile_model_task_plan(
        goal="screen a learned Hamiltonian",
        hypotheses=(_hypothesis(),),
        operator_results=(),
        proposal=_deeph_route(),
        capabilities=(
            *support,
            _learned_hamiltonian_capability(
                benchmark=_hamiltonian_benchmark(band_error_ev=0.030)
            ),
        ),
        policy=ValidationRoutePolicy(require_live_deepseek_reasoning=False),
    )
    assert blocked.tasks[-1].status is RouteAuditStatus.BLOCKED
    assert "ML_BAND_ERROR_EXCEEDS_POLICY" in blocked.tasks[-1].reason_codes


def test_uniham_capability_uses_dual_graphs_instead_of_deeph_overlap() -> None:
    capability = ModelCapability(
        model_id="uniham-soc-gpu-v1",
        availability=CapabilityAvailability.READY,
        weight_status=WeightStatus.READY,
        checkpoint_sha256="7" * 64,
        supported_observables=("soc_hamiltonian",),
        supported_task_kinds=(ScientificTaskKind.ML_HAMILTONIAN_SOC,),
        accepted_artifact_kinds=(
            ScientificArtifactKind.NON_SOC_HAMILTONIAN_GRAPH,
            ScientificArtifactKind.RELAXED_STRUCTURE,
            ScientificArtifactKind.SOC_HAMILTONIAN_GRAPH,
        ),
        produced_artifact_kinds=(ScientificArtifactKind.ML_SOC_HAMILTONIAN,),
        physics_features=(
            PhysicsCapabilityFeature.BASIS_IDENTITY_FROZEN,
            PhysicsCapabilityFeature.HAMILTONIAN_AVAILABLE,
            PhysicsCapabilityFeature.HAMILTONIAN_GRAPH_INPUT,
            PhysicsCapabilityFeature.LEARNED_HAMILTONIAN,
            PhysicsCapabilityFeature.PRECOMPUTED_ELECTRONIC_INPUT_ONLY,
            PhysicsCapabilityFeature.SOC_EXPLICIT,
        ),
        supported_elements=("S", "Ti"),
        supported_dimensionalities=(2,),
        evidence_ceiling=ScientificEvidenceLevel.L2_ML_SCREENED,
        estimated_cost_units=10,
        real_backend=True,
        benchmark_status="VALIDATED",
        hamiltonian_applicability=HamiltonianModelApplicability(
            interface="openmx",
            basis_id="openmx-nao26-v1",
            dft_software_version="openmx-3.9-dft-data19",
            training_domain_id="uniham-public-domain-v1",
            training_domain_sha256="8" * 64,
            soc_mode=HamiltonianSocMode.SOC,
            input_mode=HamiltonianInputMode.DUAL_SOC_GRAPH,
            input_origin=HamiltonianInputOrigin.PRECOMPUTED_TRUSTED,
            required_input_artifact_kinds=(
                ScientificArtifactKind.NON_SOC_HAMILTONIAN_GRAPH,
                ScientificArtifactKind.RELAXED_STRUCTURE,
                ScientificArtifactKind.SOC_HAMILTONIAN_GRAPH,
            ),
            supported_magnetic_modes=("NONMAGNETIC",),
            overlap_required=False,
            benchmark=_hamiltonian_benchmark(soc_gap_error_ev=0.008),
        ),
    )
    assert (
        capability.hamiltonian_applicability.input_mode
        is HamiltonianInputMode.DUAL_SOC_GRAPH
    )
    assert (
        PhysicsCapabilityFeature.OVERLAP_MATRIX_REQUIRED
        not in capability.physics_features
    )


def test_two_dimensional_structure_and_magnetic_enumeration_are_deterministic() -> None:
    structure = Structure.from_file(MONOLAYER)
    assessment = assess_two_dimensional_structure(
        structure,
        TwoDStructurePolicy(
            minimum_periodic_void_gap_angstrom=3.0,
            contributor_elements=("Ti",),
        ),
    )
    assert assessment.dimensionality == 2
    assert assessment.has_periodic_void_gap
    assert assessment.contributor_site_indices
    assert assessment.oxidation_assignments[0].all_observed_states_common
    enumeration = enumerate_collinear_magnetic_configurations(
        structure,
        MagneticEnumerationPolicy(
            magnetic_elements=("Ti",),
            initial_moment_magnitude_mu_b=1.0,
        ),
    )
    assert enumeration.configurations[0].label == "FM"
    assert enumeration.configurations[0].initial_moments_mu_b == (1.0,)


def test_hamgnn_band_parser_removes_repeated_high_symmetry_rows() -> None:
    parsed = parse_hamgnn_band_dat(
        """# k_lable: G X G
# k_node: 0.000000 0.500000 1.000000
0.0 -1.0
0.5 -0.8

0.5 -0.8
1.0 -1.0

0.0 0.2
0.5 0.4

0.5 0.4
1.0 0.2
"""
    )
    assert isinstance(parsed, ParsedHamGNNBandStructure)
    assert parsed.k_distances_inv_angstrom == (0.0, 0.5, 1.0)
    assert parsed.band_energies_ev == ((-1.0, -0.8, -1.0), (0.2, 0.4, 0.2))


def test_flat_band_validator_checks_bandwidth_crossings_orbitals_and_soc() -> None:
    result = assess_flat_band(
        FlatBandAnalysisInput(
            fermi_energy_ev=0.0,
            k_distances_inv_angstrom=(0.0, 0.25, 0.5, 0.75, 1.0),
            high_symmetry_indices=(0, 2, 4),
            band_energies_ev=(
                (-0.35, -0.34, -0.33, -0.34, -0.35),
                (-0.010, -0.005, 0.0, 0.005, 0.010),
                (0.25, 0.24, 0.23, 0.24, 0.25),
            ),
            target_band_index=1,
            orbital_projections=(
                BandOrbitalProjection(
                    band_index=1,
                    transition_metal_weight_fraction=0.65,
                    ligand_weight_fraction=0.25,
                    contributing_site_indices=(0, 1, 2),
                ),
            ),
            contributor_sublattice_connected=True,
            soc_explicit=True,
        ),
        FlatBandPolicy(
            maximum_bandwidth_ev=0.05,
            near_fermi_window_ev=0.05,
            degeneracy_tolerance_ev=1e-4,
            slope_difference_tolerance_ev_angstrom=1e-3,
            minimum_transition_metal_weight_fraction=0.2,
            minimum_tm_ligand_weight_fraction=0.6,
            require_soc_explicit=True,
        ),
    )
    assert result.verdict is ValidationVerdict.PASS
    assert result.target_bandwidth_ev <= 0.05


def test_flat_band_validator_keeps_missing_orbitals_inconclusive() -> None:
    result = assess_flat_band(
        FlatBandAnalysisInput(
            fermi_energy_ev=0.0,
            k_distances_inv_angstrom=(0.0, 0.5, 1.0),
            high_symmetry_indices=(0, 2),
            band_energies_ev=(
                (-0.4, -0.4, -0.4),
                (-0.01, 0.0, 0.01),
                (0.3, 0.3, 0.3),
            ),
            target_band_index=1,
            contributor_sublattice_connected=True,
            soc_explicit=True,
        ),
        FlatBandPolicy(
            maximum_bandwidth_ev=0.05,
            near_fermi_window_ev=0.05,
            degeneracy_tolerance_ev=1e-4,
            slope_difference_tolerance_ev_angstrom=1e-3,
            minimum_transition_metal_weight_fraction=0.2,
            minimum_tm_ligand_weight_fraction=0.6,
            require_soc_explicit=True,
        ),
    )

    assert result.verdict is ValidationVerdict.INCONCLUSIVE
    assert result.orbital_character_passed is None
    assert result.reason_codes == ("ORBITAL_CHARACTER_UNRESOLVED",)


def test_topology_and_exchange_tc_validators_keep_method_limitations() -> None:
    topology = assess_topology(
        TopologyAnalysisInput(
            soc_explicit=True,
            direct_gap_ev=0.12,
            indirect_gap_ev=0.04,
            wilson_loop_winding=1,
            chern_number=0.0,
            invariant_method="BOTH",
            wannier_hamiltonian_sha256="c" * 64,
        ),
        TopologyPolicy(
            minimum_direct_gap_ev=0.01,
            integer_invariant_tolerance=0.05,
        ),
    )
    assert topology.verdict is ValidationVerdict.PASS
    assert topology.z2_index == 1
    tc = estimate_exchange_tc(
        ExchangeTcInput(
            exchange_parameters_mev=(2.0,),
            coordination_numbers=(3,),
            spin_quantum_number=0.5,
            fit_rank=1,
            fit_parameter_count=1,
            fit_rmse_mev=0.1,
        ),
        ExchangeTcPolicy(maximum_fit_rmse_mev=0.5),
    )
    assert tc.verdict is ValidationVerdict.PASS
    assert tc.tc_kelvin is not None and tc.tc_kelvin > 30
    assert "upper-biased" in tc.limitations[0]
