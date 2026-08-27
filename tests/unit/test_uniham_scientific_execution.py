from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

from material_agent.inspiration.models import ArtifactPointerV1, canonical_sha256
from material_agent.inspiration.research_memory import InspirationMemoryStore
from material_agent.integration.scientific_execution import (
    ScientificExecutorRegistry,
    execute_scientific_dag,
)
from material_agent.integration.scientific_executors import artifact_pointer_from_ref
from material_agent.integration.scientific_loop import (
    CapabilityAvailability,
    DeepSeekModelRouteProposal,
    DeepSeekReasoningProvenance,
    HamiltonianInputMode,
    HamiltonianInputOrigin,
    HamiltonianModelApplicability,
    HamiltonianSocMode,
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
from material_agent.integration.uniham_scientific import (
    PrecomputedUniHamInputBundle,
    PrecomputedUniHamInputImportExecutor,
    UniHamBandScientificExecutor,
    UniHamScientificExecutor,
)
from material_agent.ml_screening.uniham_band_models import (
    UniHamBandRequest,
    build_uniham_band_plan,
)
from material_agent.ml_screening.uniham_models import (
    UniHamArtifactFile,
    UniHamGraphBundle,
    UniHamGraphManifest,
    UniHamInferenceRequest,
    UniHamInputTrust,
    UniHamRuntimeProvenance,
    UniHamSocMode,
)
from material_agent.retrieval.storage import LocalArtifactStore


class _RouteReasoner:
    def __init__(self, proposal: DeepSeekModelRouteProposal) -> None:
        self.proposal = proposal

    def propose_route(self, **_values: object) -> DeepSeekModelRouteProposal:
        return self.proposal

    def propose_feedback(self, **_values: object):
        raise AssertionError("Uni-HamGNN execution fixture does not request feedback")


class _FakeUniHamRemoteClient:
    def __init__(
        self,
        *,
        store: LocalArtifactStore,
        operation_key: str,
    ) -> None:
        self.store = store
        self.operation_key = operation_key
        self.call_count = 0

    def run(
        self,
        request: UniHamInferenceRequest,
        *,
        non_soc_manifest: UniHamGraphManifest,
        soc_manifest: UniHamGraphManifest,
        limits: object | None = None,
    ) -> SimpleNamespace:
        del limits
        self.call_count += 1
        assert request.device == "cuda"
        assert non_soc_manifest.soc_mode is UniHamSocMode.NON_SOC
        assert soc_manifest.soc_mode is UniHamSocMode.SOC
        pointer = artifact_pointer_from_ref(
            self.store.write_bytes(
                "remote-cache/uniham/hamiltonian.npy",
                b"\x93NUMPY-fixture-hamiltonian",
                "application/x-npy",
                immutable=True,
            )
        )
        runtime = UniHamRuntimeProvenance(
            requested_device="cuda",
            observed_device="cuda",
            cuda_visible_device_count=1,
            cuda_device_name="NVIDIA L40S",
            torch_version="2.10.0+cu128",
            torch_cuda_version="12.8",
        )
        return SimpleNamespace(
            hamiltonian_pointer=pointer,
            local_artifacts=(SimpleNamespace(local_pointer=pointer),),
            result_id="uniham-remote-fixture-result",
            worker_response=SimpleNamespace(runtime_provenance=runtime),
            plan=SimpleNamespace(operation_key=self.operation_key),
            host_alias="whu-ext",
        )


class _FakeUniHamBandRemoteClient:
    def __init__(self, store: LocalArtifactStore) -> None:
        self.store = store
        self.call_count = 0

    def run(self, request: UniHamBandRequest) -> SimpleNamespace:
        self.call_count += 1
        band = artifact_pointer_from_ref(
            self.store.write_bytes(
                "remote-cache/uniham-band/band_1.dat",
                b"""# k_lable: G X G
# k_node: 0.0 0.5 1.0
0.0 -1.0
0.5 -1.0
1.0 -1.0

0.0 -0.8
0.5 -0.4
1.0 0.0

0.0 1.2
0.5 1.2
1.0 1.2
""",
                "text/plain",
                immutable=True,
            )
        )
        plot = artifact_pointer_from_ref(
            self.store.write_bytes(
                "remote-cache/uniham-band/band_1.png",
                b"fixture-plot",
                "image/png",
                immutable=True,
            )
        )
        structure = artifact_pointer_from_ref(
            self.store.write_bytes(
                "remote-cache/uniham-band/crystal_1.cif",
                b"data_fixture",
                "chemical/x-cif",
                immutable=True,
            )
        )
        return SimpleNamespace(
            band_data_pointer=band,
            band_plot_pointer=plot,
            structure_pointer=structure,
            result_id="uniham-band-fixture-result",
            plan=build_uniham_band_plan(request),
            host_alias="whu-ext",
        )


def test_precomputed_graphs_to_uniham_gpu_evidence_and_memory(
    tmp_path: Path,
) -> None:
    store = LocalArtifactStore(tmp_path / "project")
    source = artifact_pointer_from_ref(
        store.write_bytes(
            "inputs/zrsipt.cif",
            b"data_zrsipt\n_cell_length_a 4.0\n",
            "chemical/x-cif",
            immutable=True,
        )
    )
    request, non_soc_manifest, soc_manifest = _request(source)
    bundle = PrecomputedUniHamInputBundle(
        catalog_id="uniham-public-example-catalog",
        catalog_version="2026-08-25",
        request=request,
        non_soc_manifest=non_soc_manifest,
        soc_manifest=soc_manifest,
        reviewed_by="hermes-test-suite",
    )
    hypothesis = HypothesisCandidate(
        candidate_id=request.candidate_id,
        material_name="ZrSiPt",
        formula="ZrSiPt",
        hypothesis="A trusted graph pair can feed a fast learned SOC Hamiltonian.",
        mechanism="The graph pair and structure share an exact OpenMX basis identity.",
        database_candidate_ids=("database-zrsipt",),
        parent_structure=source,
        elements=("Pt", "Si", "Zr"),
        dimensionality=2,
        unresolved_claims=("soc_hamiltonian",),
    )
    goal = "generate a learned SOC Hamiltonian without DFT"
    import_task = ProposedModelTask(
        task_id="task-import-precomputed-graphs",
        candidate_id=hypothesis.candidate_id,
        model_id="precomputed-uniham-catalog-v1",
        task_kind=ScientificTaskKind.PRECOMPUTED_ELECTRONIC_INPUT_IMPORT,
        input_kind=ModelTaskInputKind.PARENT_STRUCTURE,
        produced_artifact_kinds=(
            ScientificArtifactKind.NON_SOC_HAMILTONIAN_GRAPH,
            ScientificArtifactKind.SOC_HAMILTONIAN_GRAPH,
        ),
        requested_observables=("precomputed_soc_graph_pair",),
        required_evidence_level=ScientificEvidenceLevel.L1_RETRIEVED,
        rationale="Import a reviewed graph pair without running electronic preprocessing.",
        falsification_rule="Block on any structure, basis, SOC-mode or hash mismatch.",
    )
    uniham_task = ProposedModelTask(
        task_id="task-uniham-soc",
        candidate_id=hypothesis.candidate_id,
        model_id=request.model_id,
        task_kind=ScientificTaskKind.ML_HAMILTONIAN_SOC,
        input_kind=ModelTaskInputKind.PREVIOUS_TASK,
        prerequisite_task_ids=(import_task.task_id,),
        required_input_artifact_kinds=(
            ScientificArtifactKind.NON_SOC_HAMILTONIAN_GRAPH,
            ScientificArtifactKind.SOC_HAMILTONIAN_GRAPH,
        ),
        produced_artifact_kinds=(ScientificArtifactKind.ML_SOC_HAMILTONIAN,),
        required_capability_features=tuple(
            sorted(
                {
                    PhysicsCapabilityFeature.BASIS_IDENTITY_FROZEN,
                    PhysicsCapabilityFeature.HAMILTONIAN_AVAILABLE,
                    PhysicsCapabilityFeature.HAMILTONIAN_GRAPH_INPUT,
                    PhysicsCapabilityFeature.LEARNED_HAMILTONIAN,
                    PhysicsCapabilityFeature.PRECOMPUTED_ELECTRONIC_INPUT_ONLY,
                    PhysicsCapabilityFeature.SOC_EXPLICIT,
                },
                key=str,
            )
        ),
        requested_observables=("soc_hamiltonian",),
        required_evidence_level=ScientificEvidenceLevel.NONE,
        rationale="Generate an engineering Hamiltonian quickly on one pinned GPU.",
        falsification_rule="Block if the exact imported graph pair cannot be consumed.",
    )
    band_task = ProposedModelTask(
        task_id="task-uniham-band-analysis",
        candidate_id=hypothesis.candidate_id,
        model_id="hamgnn-band-cal-v1",
        task_kind=ScientificTaskKind.BAND_ORBITAL_ANALYSIS,
        input_kind=ModelTaskInputKind.PREVIOUS_TASK,
        prerequisite_task_ids=(import_task.task_id, uniham_task.task_id),
        required_input_artifact_kinds=(
            ScientificArtifactKind.ML_SOC_HAMILTONIAN,
            ScientificArtifactKind.NON_SOC_HAMILTONIAN_GRAPH,
            ScientificArtifactKind.SOC_HAMILTONIAN_GRAPH,
        ),
        produced_artifact_kinds=(
            ScientificArtifactKind.FLAT_BAND_ASSESSMENT,
            ScientificArtifactKind.SOC_BAND_STRUCTURE,
        ),
        required_capability_features=(
            PhysicsCapabilityFeature.HAMILTONIAN_AVAILABLE,
            PhysicsCapabilityFeature.K_RESOLVED,
            PhysicsCapabilityFeature.SOC_EXPLICIT,
        ),
        requested_observables=(
            "bandwidth_le_50_mev",
            "flat_band_first_near_fermi",
            "no_first_order_crossing",
        ),
        required_evidence_level=ScientificEvidenceLevel.NONE,
        parameters={
            "contributor_sublattice_connected": True,
            "fermi_energy_ev": 0.0,
            "policy": {
                "degeneracy_tolerance_ev": 0.0001,
                "maximum_bandwidth_ev": 0.05,
                "minimum_tm_ligand_weight_fraction": 0.6,
                "minimum_transition_metal_weight_fraction": 0.2,
                "near_fermi_window_ev": 0.05,
                "require_soc_explicit": True,
                "slope_difference_tolerance_ev_angstrom": 0.001,
            },
        },
        rationale="Postprocess the learned Hamiltonian and apply the 50 meV rule.",
        falsification_rule="Contradict when the nearest band clearly exceeds 50 meV.",
    )
    proposal = DeepSeekModelRouteProposal(
        goal_sha256=canonical_sha256({"goal": goal}),
        provenance=DeepSeekReasoningProvenance(
            prompt_version="uniham-scientific-fixture-v1",
            receipt_sha256="a" * 64,
            live_call=False,
        ),
        route_summary="Import reviewed graphs and execute Uni-HamGNN on L40S.",
        tasks=(import_task, uniham_task, band_task),
    )
    graph_kinds = (
        ScientificArtifactKind.NON_SOC_HAMILTONIAN_GRAPH,
        ScientificArtifactKind.SOC_HAMILTONIAN_GRAPH,
    )
    import_capability = ModelCapability(
        model_id=import_task.model_id,
        availability=CapabilityAvailability.READY,
        weight_status=WeightStatus.NOT_REQUIRED,
        supported_observables=import_task.requested_observables,
        supported_task_kinds=(
            ScientificTaskKind.PRECOMPUTED_ELECTRONIC_INPUT_IMPORT,
        ),
        produced_artifact_kinds=graph_kinds,
        supported_elements=None,
        supported_dimensionalities=(2, 3),
        evidence_ceiling=ScientificEvidenceLevel.L1_RETRIEVED,
        estimated_cost_units=1,
        real_backend=True,
        benchmark_status="VALIDATED",
    )
    features = uniham_task.required_capability_features
    uniham_capability = ModelCapability(
        model_id=request.model_id,
        availability=CapabilityAvailability.READY,
        weight_status=WeightStatus.READY,
        checkpoint_sha256=request.model_pickle.sha256,
        supported_observables=uniham_task.requested_observables,
        supported_task_kinds=(ScientificTaskKind.ML_HAMILTONIAN_SOC,),
        accepted_artifact_kinds=graph_kinds,
        produced_artifact_kinds=(ScientificArtifactKind.ML_SOC_HAMILTONIAN,),
        physics_features=features,
        supported_elements=None,
        supported_dimensionalities=(2, 3),
        evidence_ceiling=ScientificEvidenceLevel.L1_RETRIEVED,
        estimated_cost_units=5,
        real_backend=True,
        benchmark_status="NOT_RUN",
        hamiltonian_applicability=HamiltonianModelApplicability(
            interface="openmx",
            basis_id="openmx-nao26-v1",
            dft_software_version="openmx-3.9-dft-data19",
            training_domain_id="uniham-public-domain-v1",
            training_domain_sha256="8" * 64,
            soc_mode=HamiltonianSocMode.SOC,
            input_mode=HamiltonianInputMode.DUAL_SOC_GRAPH,
            input_origin=HamiltonianInputOrigin.PRECOMPUTED_TRUSTED,
            required_input_artifact_kinds=graph_kinds,
            supported_magnetic_modes=("NONMAGNETIC",),
            overlap_required=False,
        ),
    )
    band_capability = ModelCapability(
        model_id=band_task.model_id,
        availability=CapabilityAvailability.READY,
        weight_status=WeightStatus.NOT_REQUIRED,
        supported_observables=band_task.requested_observables,
        supported_task_kinds=(ScientificTaskKind.BAND_ORBITAL_ANALYSIS,),
        accepted_artifact_kinds=band_task.required_input_artifact_kinds,
        produced_artifact_kinds=band_task.produced_artifact_kinds,
        physics_features=band_task.required_capability_features,
        supported_elements=None,
        supported_dimensionalities=(2, 3),
        evidence_ceiling=ScientificEvidenceLevel.L1_RETRIEVED,
        estimated_cost_units=2,
        real_backend=True,
        benchmark_status="NOT_RUN",
    )
    operation_key = hashlib.sha256(b"fixture-operation").hexdigest()
    remote = _FakeUniHamRemoteClient(store=store, operation_key=operation_key)
    band_remote = _FakeUniHamBandRemoteClient(store)

    def band_request_factory(_binding) -> UniHamBandRequest:
        hamiltonian_payload = b"\x93NUMPY-fixture-hamiltonian"
        hamiltonian = _artifact(
            (
                "stages/agent02/uniham-scientific-run/uniham/"
                f"{operation_key}/worker-output/output/hamiltonian.npy"
            ),
            hashlib.sha256(hamiltonian_payload).hexdigest(),
            len(hamiltonian_payload),
            "application/x-npy",
        )
        return UniHamBandRequest(
            project_id=request.project_id,
            run_id="uniham-scientific-band-run",
            candidate_id=request.candidate_id,
            input_structure=request.input_structure,
            soc_graph=request.soc_graph,
            soc_manifest=soc_manifest,
            hamiltonian=hamiltonian,
            hamiltonian_operation_key=operation_key,
            hamgnn_source_revision=request.hamgnn_source_revision,
            band_calculator_sha256="e" * 64,
        )

    def verify(pointer: ArtifactPointerV1) -> bool:
        return store.exists_with_hash(pointer.uri, pointer.sha256)

    with InspirationMemoryStore(
        tmp_path / "memory.sqlite3",
        artifact_verifier=verify,
    ) as memory:
        service = ScientificValidationLoopService(
            project_id="uniham-scientific-project",
            source_run_id="uniham-scientific-run",
            goal=goal,
            hypotheses=(hypothesis,),
            capabilities=(import_capability, uniham_capability, band_capability),
            policy=ValidationRoutePolicy(require_live_deepseek_reasoning=False),
            artifact_store=store,
            memory_store=memory,
            reasoner=_RouteReasoner(proposal),
        )
        _route, plan = service.propose_and_audit_route(())
        registry = ScientificExecutorRegistry()
        registry.register(
            import_task.model_id,
            PrecomputedUniHamInputImportExecutor(store, lambda _binding: bundle),
        )
        registry.register(
            uniham_task.model_id,
            UniHamScientificExecutor(store, remote, lambda _binding: bundle),
        )
        registry.register(
            band_task.model_id,
            UniHamBandScientificExecutor(store, band_remote, band_request_factory),
        )
        result = execute_scientific_dag(
            plan=plan,
            service=service,
            executors=registry,
        )
        snapshot = memory.snapshot("uniham-scientific-project")

    assert result.all_audited_tasks_succeeded
    assert remote.call_count == 1
    assert band_remote.call_count == 1
    assert result.evidence[0].evidence_level is ScientificEvidenceLevel.L1_RETRIEVED
    assert result.evidence[1].evidence_level is ScientificEvidenceLevel.NONE
    assert result.evidence[1].reason_codes == (
        "UNIHAM_HAMILTONIAN_GENERATED_UNBENCHMARKED",
    )
    assert result.evidence[1].produced_artifacts[0].kind is (
        ScientificArtifactKind.ML_SOC_HAMILTONIAN
    )
    assert result.evidence[2].verdict == "CONTRADICTS"
    assert "BANDWIDTH_EXCEEDS_POLICY" in result.evidence[2].reason_codes
    assert len(snapshot.downstream_outcomes) == 3


def _request(
    source: ArtifactPointerV1,
) -> tuple[UniHamInferenceRequest, UniHamGraphManifest, UniHamGraphManifest]:
    structure = _artifact("inputs/zrsipt/openmx.cif", source.sha256, source.size_bytes)
    model = _artifact("weights/uni-hamgnn_2_1.pkl", "5" * 64, 927_026_099)
    non_soc_graph = _graph_bundle("inputs/zrsipt/non_soc", "1" * 64)
    soc_graph = _graph_bundle("inputs/zrsipt/soc", "2" * 64)
    non_soc_manifest = _manifest(
        structure_sha256=source.sha256,
        graph_sha256=non_soc_graph.graph_data.sha256,
        mode=UniHamSocMode.NON_SOC,
    )
    soc_manifest = _manifest(
        structure_sha256=source.sha256,
        graph_sha256=soc_graph.graph_data.sha256,
        mode=UniHamSocMode.SOC,
    )
    request = UniHamInferenceRequest(
        project_id="uniham-scientific-project",
        run_id="uniham-scientific-run",
        candidate_id="candidate-zrsipt",
        input_structure=structure,
        model_pickle=model,
        non_soc_graph=non_soc_graph,
        soc_graph=soc_graph,
        model_id="uniham-soc-gpu-v1",
        model_source_url="https://zenodo.org/records/17239078",
        model_revision="uni-hamgnn-2-1",
        weights_license="UPSTREAM-REVIEWED",
        hamgnn_source_revision="2fe5debb28711dae72a90ba09f9c44bec39a663c",
        predictor_script_sha256="a" * 64,
        input_trust=UniHamInputTrust(
            trusted_executable_inputs=True,
            reviewed_by="hermes-test-suite",
            reviewed_at=datetime(2026, 8, 25, tzinfo=UTC),
            review_basis="hash-bound upstream fixture for executor contract testing",
        ),
        device="cuda",
        is_mock=False,
    )
    return request, non_soc_manifest, soc_manifest


def _graph_bundle(directory: str, graph_sha256: str) -> UniHamGraphBundle:
    return UniHamGraphBundle(
        root_relative_directory=directory,
        graph_data=_artifact(f"{directory}/graph_data.npz", graph_sha256, 1024),
        manifest=_artifact(
            f"{directory}/hermes-graph-manifest.json",
            "3" * 64 if directory.endswith("non_soc") else "4" * 64,
            512,
            "application/json",
        ),
    )


def _manifest(
    *,
    structure_sha256: str,
    graph_sha256: str,
    mode: UniHamSocMode,
) -> UniHamGraphManifest:
    return UniHamGraphManifest(
        structure_sha256=structure_sha256,
        graph_data_sha256=graph_sha256,
        soc_mode=mode,
        basis_id="openmx-nao26-v1",
        dft_data_version="OpenMX-3.9-DFT_DATA19",
        graph_generator_revision="abcdef1234567890",
    )


def _artifact(
    relative: str,
    sha256: str,
    size_bytes: int | None,
    media_type: str = "application/octet-stream",
) -> UniHamArtifactFile:
    assert size_bytes is not None and size_bytes > 0
    return UniHamArtifactFile(
        artifact_uri=f"artifact://{relative}",
        root_relative_path=relative,
        sha256=sha256,
        size_bytes=size_bytes,
        media_type=media_type,
    )
