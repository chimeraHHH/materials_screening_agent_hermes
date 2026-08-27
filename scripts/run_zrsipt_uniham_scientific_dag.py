#!/usr/bin/env python3
"""Real no-DFT ZrSiPt control replay through the scientific DAG."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from material_agent.inspiration.models import ArtifactPointerV1, canonical_sha256
from material_agent.inspiration.research_memory import InspirationMemoryStore
from material_agent.integration.generic_research import (
    research_secret_resolver_from_environment,
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
    DeepSeekScientificLoopReasoner,
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
from material_agent.ml_screening.uniham_band_models import UniHamBandRequest
from material_agent.ml_screening.uniham_band_remote import UniHamBandRemoteClient
from material_agent.ml_screening.uniham_models import (
    UniHamGraphManifest,
    UniHamInferenceRequest,
)
from material_agent.ml_screening.uniham_remote import UniHamRemoteClient
from material_agent.retrieval.storage import LocalArtifactStore


class _FrozenRouteReasoner:
    def __init__(self, proposal: DeepSeekModelRouteProposal) -> None:
        self.proposal = proposal

    def propose_route(self, **_values: object) -> DeepSeekModelRouteProposal:
        return self.proposal

    def propose_feedback(self, **_values: object):
        raise AssertionError("control replay does not run a feedback cycle")


class _FrozenRouteLiveFeedbackReasoner(_FrozenRouteReasoner):
    def __init__(
        self,
        proposal: DeepSeekModelRouteProposal,
        feedback_reasoner: DeepSeekScientificLoopReasoner,
    ) -> None:
        super().__init__(proposal)
        self.feedback_reasoner = feedback_reasoner

    def propose_feedback(self, **values: object):
        return self.feedback_reasoner.propose_feedback(**values)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--uniham-request", type=Path, required=True)
    parser.add_argument("--non-soc-manifest", type=Path, required=True)
    parser.add_argument("--soc-manifest", type=Path, required=True)
    parser.add_argument("--band-request", type=Path, required=True)
    parser.add_argument("--source-cif", type=Path, required=True)
    parser.add_argument("--local-root", type=Path, required=True)
    parser.add_argument("--ssh-host-alias", required=True)
    parser.add_argument("--remote-root", required=True)
    parser.add_argument("--remote-python", required=True)
    parser.add_argument("--remote-uniham-worker", required=True)
    parser.add_argument("--remote-predictor", required=True)
    parser.add_argument("--remote-band-worker", required=True)
    parser.add_argument("--remote-band-cal", required=True)
    parser.add_argument("--cuda-visible-device", required=True)
    parser.add_argument(
        "--live-deepseek",
        action="store_true",
        help="Let DeepSeek create the ML-only route from the registered capabilities.",
    )
    parser.add_argument(
        "--route-only",
        action="store_true",
        help="Audit and print the proposed route without running GPU executors.",
    )
    parser.add_argument(
        "--live-feedback",
        action="store_true",
        help="Run a real DeepSeek evidence-to-memory feedback cycle after the DAG.",
    )
    args = parser.parse_args()
    if args.route_only and args.live_feedback:
        parser.error("--live-feedback requires DAG execution, not --route-only")

    request = UniHamInferenceRequest.model_validate_json(
        args.uniham_request.read_bytes()
    )
    non_soc_manifest = UniHamGraphManifest.model_validate_json(
        args.non_soc_manifest.read_bytes()
    )
    soc_manifest = UniHamGraphManifest.model_validate_json(
        args.soc_manifest.read_bytes()
    )
    band_request = UniHamBandRequest.model_validate_json(
        args.band_request.read_bytes()
    )
    store = LocalArtifactStore(args.local_root)
    source = artifact_pointer_from_ref(
        store.write_bytes(
            "inputs/zrsipt-openmx.cif",
            args.source_cif.read_bytes(),
            "chemical/x-cif",
            immutable=True,
        )
    )
    if source.sha256 != request.input_structure.sha256:
        raise ValueError("local ZrSiPt CIF differs from frozen Uni-HamGNN request")
    bundle = PrecomputedUniHamInputBundle(
        catalog_id="uniham-official-zrsipt-example",
        catalog_version="zenodo-17239078",
        request=request,
        non_soc_manifest=non_soc_manifest,
        soc_manifest=soc_manifest,
        reviewed_by="hermes-local-operator",
    )
    hypothesis = HypothesisCandidate(
        candidate_id=request.candidate_id,
        material_name="ZrSiPt official Uni-HamGNN example",
        formula="ZrSiPt",
        hypothesis="A real learned SOC Hamiltonian can be screened for a narrow band.",
        mechanism="Exact precomputed graph identity enables a fast model replay.",
        database_candidate_ids=("uniham-official-zrsipt",),
        parent_structure=source,
        elements=("Pt", "Si", "Zr"),
        dimensionality=3,
        unresolved_claims=("bandwidth_le_50_mev",),
    )
    goal = "screen the real ZrSiPt Uni-HamGNN example without DFT"
    import_task, uniham_task, band_task = _tasks(hypothesis, request)
    frozen_proposal = DeepSeekModelRouteProposal(
        goal_sha256=canonical_sha256({"goal": goal}),
        provenance=DeepSeekReasoningProvenance(
            prompt_version="zrsipt-real-control-replay-v1",
            receipt_sha256="f" * 64,
            live_call=False,
        ),
        route_summary=(
            "Import the exact official graph pair, recover the real L40S "
            "Hamiltonian and run hash-bound band postprocessing."
        ),
        tasks=(import_task, uniham_task, band_task),
    )
    capabilities = _capabilities(
        request=request,
        non_soc_manifest=non_soc_manifest,
        import_task=import_task,
        uniham_task=uniham_task,
        band_task=band_task,
    )
    live_reasoner = (
        DeepSeekScientificLoopReasoner(
            secret_resolver=research_secret_resolver_from_environment(),
            reasoning_effort="high",
        )
        if args.live_deepseek or args.live_feedback
        else None
    )
    if args.live_deepseek:
        assert live_reasoner is not None
        reasoner = live_reasoner
    elif args.live_feedback:
        assert live_reasoner is not None
        reasoner = _FrozenRouteLiveFeedbackReasoner(
            frozen_proposal,
            live_reasoner,
        )
    else:
        reasoner = _FrozenRouteReasoner(frozen_proposal)
    uniham_client = UniHamRemoteClient(
        artifact_store=store,
        ssh_host_alias=args.ssh_host_alias,
        remote_artifact_root=args.remote_root,
        remote_worker_python=args.remote_python,
        remote_worker_path=args.remote_uniham_worker,
        remote_predictor_path=args.remote_predictor,
        cuda_visible_device=args.cuda_visible_device,
    )
    band_client = UniHamBandRemoteClient(
        artifact_store=store,
        ssh_host_alias=args.ssh_host_alias,
        remote_artifact_root=args.remote_root,
        remote_worker_python=args.remote_python,
        remote_worker_path=args.remote_band_worker,
        remote_band_cal_executable=args.remote_band_cal,
    )

    def verify(pointer: ArtifactPointerV1) -> bool:
        return store.exists_with_hash(pointer.uri, pointer.sha256)

    memory_path = args.local_root / "research-memory.sqlite3"
    with InspirationMemoryStore(
        memory_path,
        artifact_verifier=verify,
    ) as memory:
        service = ScientificValidationLoopService(
            project_id="zrsipt-real-scientific-dag",
            source_run_id="zrsipt-real-scientific-dag-r1",
            goal=goal,
            hypotheses=(hypothesis,),
            capabilities=capabilities,
            policy=ValidationRoutePolicy(
                require_live_deepseek_reasoning=args.live_deepseek,
            ),
            artifact_store=store,
            memory_store=memory,
            reasoner=reasoner,
        )
        route, plan = service.propose_and_audit_route(())
        forbidden = {
            ScientificTaskKind.DFT_NON_SOC,
            ScientificTaskKind.DFT_SOC,
            ScientificTaskKind.EXCHANGE_TC_ESTIMATION,
            ScientificTaskKind.HAMILTONIAN_GRAPH_PREPARATION,
            ScientificTaskKind.MAGNETIC_GROUND_STATE_ANALYSIS,
            ScientificTaskKind.OVERLAP_MATRIX_GENERATION,
        }
        proposed_forbidden = sorted(
            {
                item.task_kind
                for item in route.tasks
                if item.task_kind in forbidden
            },
            key=str,
        )
        if proposed_forbidden:
            raise ValueError(
                "DeepSeek proposed tasks forbidden by the ML-only boundary: "
                + ", ".join(proposed_forbidden)
            )
        if args.route_only:
            print(
                json.dumps(
                    {
                        "all_tasks_approved": all(
                            item.status == "APPROVED" for item in plan.tasks
                        ),
                        "live_deepseek": route.provenance.live_call,
                        "model_task_plan_id": plan.plan_id,
                        "route_summary": route.route_summary,
                        "tasks": [
                            {
                                "audit_reasons": list(audited.reason_codes),
                                "audit_status": audited.status,
                                "model_id": audited.proposed_task.model_id,
                                "task_id": audited.proposed_task.task_id,
                                "task_kind": audited.proposed_task.task_kind,
                            }
                            for audited in plan.tasks
                        ],
                    },
                    indent=2,
                    sort_keys=True,
                )
            )
            return 0
        registry = ScientificExecutorRegistry()
        registry.register(
            import_task.model_id,
            PrecomputedUniHamInputImportExecutor(store, lambda _binding: bundle),
        )
        registry.register(
            uniham_task.model_id,
            UniHamScientificExecutor(store, uniham_client, lambda _binding: bundle),
        )
        registry.register(
            band_task.model_id,
            UniHamBandScientificExecutor(
                store,
                band_client,
                lambda _binding: band_request,
            ),
        )
        result = execute_scientific_dag(
            plan=plan,
            service=service,
            executors=registry,
        )
        feedback = None
        feedback_cycle = None
        if args.live_feedback:
            feedback, feedback_cycle = service.propose_and_audit_feedback(
                result.evidence
            )
        snapshot = memory.snapshot("zrsipt-real-scientific-dag")
    print(
        json.dumps(
            {
                "all_audited_tasks_succeeded": result.all_audited_tasks_succeeded,
                "evidence": [
                    {
                        "evidence_id": item.evidence_id,
                        "evidence_level": item.evidence_level,
                        "reason_codes": item.reason_codes,
                        "task_id": item.task_id,
                        "verdict": item.verdict,
                    }
                    for item in result.evidence
                ],
                "memory_outcomes": len(snapshot.downstream_outcomes),
                "model_task_plan_id": plan.plan_id,
                "feedback": (
                    {
                        "cycle_id": feedback_cycle.cycle_id,
                        "cycle_summary": feedback.cycle_summary,
                        "decisions": [
                            {
                                "action": audited.proposal.action,
                                "audit_reason_codes": audited.reason_codes,
                                "audit_status": audited.status,
                                "candidate_id": audited.proposal.candidate_id,
                                "evidence_ids": audited.proposal.evidence_ids,
                            }
                            for audited in feedback_cycle.decisions
                        ],
                        "live_deepseek": feedback.provenance.live_call,
                        "receipt_sha256": feedback.provenance.receipt_sha256,
                    }
                    if feedback is not None and feedback_cycle is not None
                    else None
                ),
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


def _tasks(
    hypothesis: HypothesisCandidate,
    request: UniHamInferenceRequest,
) -> tuple[ProposedModelTask, ProposedModelTask, ProposedModelTask]:
    graph_kinds = (
        ScientificArtifactKind.NON_SOC_HAMILTONIAN_GRAPH,
        ScientificArtifactKind.SOC_HAMILTONIAN_GRAPH,
    )
    import_task = ProposedModelTask(
        task_id="task-import-precomputed-graphs",
        candidate_id=hypothesis.candidate_id,
        model_id="precomputed-uniham-catalog-v1",
        task_kind=ScientificTaskKind.PRECOMPUTED_ELECTRONIC_INPUT_IMPORT,
        input_kind=ModelTaskInputKind.PARENT_STRUCTURE,
        produced_artifact_kinds=graph_kinds,
        requested_observables=("precomputed_soc_graph_pair",),
        required_evidence_level=ScientificEvidenceLevel.L1_RETRIEVED,
        rationale="Import the exact reviewed official graph pair.",
        falsification_rule="Block on structure, basis, SOC mode or hash mismatch.",
    )
    uniham_features = tuple(
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
    )
    uniham_task = ProposedModelTask(
        task_id="task-uniham-soc",
        candidate_id=hypothesis.candidate_id,
        model_id=request.model_id,
        task_kind=ScientificTaskKind.ML_HAMILTONIAN_SOC,
        input_kind=ModelTaskInputKind.PREVIOUS_TASK,
        prerequisite_task_ids=(import_task.task_id,),
        required_input_artifact_kinds=graph_kinds,
        produced_artifact_kinds=(ScientificArtifactKind.ML_SOC_HAMILTONIAN,),
        required_capability_features=uniham_features,
        requested_observables=("soc_hamiltonian",),
        required_evidence_level=ScientificEvidenceLevel.NONE,
        rationale="Recover the real learned Hamiltonian on the GPU server.",
        falsification_rule="Block on any request or output integrity mismatch.",
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
            *graph_kinds,
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
            "contributor_sublattice_connected": None,
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
        rationale="Calculate ML bands and apply the exact 50 meV rule.",
        falsification_rule="Contradict if the nearest band exceeds 50 meV.",
    )
    return import_task, uniham_task, band_task


def _capabilities(
    *,
    request: UniHamInferenceRequest,
    non_soc_manifest: UniHamGraphManifest,
    import_task: ProposedModelTask,
    uniham_task: ProposedModelTask,
    band_task: ProposedModelTask,
) -> tuple[ModelCapability, ModelCapability, ModelCapability]:
    graph_kinds = import_task.produced_artifact_kinds
    return (
        ModelCapability(
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
        ),
        ModelCapability(
            model_id=uniham_task.model_id,
            availability=CapabilityAvailability.READY,
            weight_status=WeightStatus.READY,
            checkpoint_sha256=request.model_pickle.sha256,
            supported_observables=uniham_task.requested_observables,
            supported_task_kinds=(ScientificTaskKind.ML_HAMILTONIAN_SOC,),
            accepted_artifact_kinds=graph_kinds,
            produced_artifact_kinds=uniham_task.produced_artifact_kinds,
            physics_features=uniham_task.required_capability_features,
            supported_elements=None,
            supported_dimensionalities=(2, 3),
            evidence_ceiling=ScientificEvidenceLevel.L1_RETRIEVED,
            estimated_cost_units=5,
            real_backend=True,
            benchmark_status="NOT_RUN",
            hamiltonian_applicability=HamiltonianModelApplicability(
                interface="openmx",
                basis_id=non_soc_manifest.basis_id,
                dft_software_version="openmx-dft-data19",
                training_domain_id="uniham-public-domain-v1",
                training_domain_sha256="8" * 64,
                soc_mode=HamiltonianSocMode.SOC,
                input_mode=HamiltonianInputMode.DUAL_SOC_GRAPH,
                input_origin=HamiltonianInputOrigin.PRECOMPUTED_TRUSTED,
                required_input_artifact_kinds=graph_kinds,
                supported_magnetic_modes=("NONMAGNETIC",),
                overlap_required=False,
            ),
        ),
        ModelCapability(
            model_id=band_task.model_id,
            availability=CapabilityAvailability.READY,
            weight_status=WeightStatus.NOT_REQUIRED,
            supported_observables=band_task.requested_observables,
            supported_task_kinds=(ScientificTaskKind.BAND_ORBITAL_ANALYSIS,),
            accepted_artifact_kinds=band_task.required_input_artifact_kinds,
            required_input_artifact_kinds=band_task.required_input_artifact_kinds,
            produced_artifact_kinds=band_task.produced_artifact_kinds,
            physics_features=band_task.required_capability_features,
            supported_elements=None,
            supported_dimensionalities=(2, 3),
            evidence_ceiling=ScientificEvidenceLevel.L1_RETRIEVED,
            estimated_cost_units=2,
            real_backend=True,
            benchmark_status="NOT_RUN",
        ),
    )


if __name__ == "__main__":
    raise SystemExit(main())
