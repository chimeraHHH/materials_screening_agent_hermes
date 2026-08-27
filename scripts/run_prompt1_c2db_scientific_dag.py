#!/usr/bin/env python3
"""Run the historical prompt1 C2DB parents through the ML-only scientific DAG."""

from __future__ import annotations

import argparse
import gzip
import json
from pathlib import Path

from material_agent.inspiration.models import ArtifactPointerV1, canonical_sha256
from material_agent.inspiration.research_memory import InspirationMemoryStore
from material_agent.integration.c2db_scientific import (
    C2DBBandBundle,
    C2DBBandInputImportExecutor,
    C2DBFlatBandAnalysisParameters,
    C2DBFlatBandScientificExecutor,
)
from material_agent.integration.electronic_structure import TwoDStructurePolicy
from material_agent.integration.generic_research import (
    research_secret_resolver_from_environment,
)
from material_agent.integration.scientific_execution import (
    ScientificExecutorRegistry,
    execute_scientific_dag,
)
from material_agent.integration.scientific_executors import (
    LocalTwoDStructureExecutor,
    artifact_pointer_from_ref,
)
from material_agent.integration.scientific_loop import (
    CapabilityAvailability,
    DeepSeekModelRouteProposal,
    DeepSeekReasoningProvenance,
    DeepSeekScientificLoopReasoner,
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

PROMPT1 = """搜索数据库中的过渡金属二维平带材料。要求：
1. 必须是层状材料，有 vdW gap 的材料最优先。
2. 平带必须是费米面附近的第一条能带，贡献这条能带的电子轨道必须由过渡金属元素或过渡金属与配体的杂化态构成。
3. 平带和其他色散较大的能带不能有一阶交点，色散能带不能穿过费米面，但它们可以在高对称点有公共极值点。
4. 平带的定义是带宽 W<=50 meV 的能带。
5. 进行价态分析，过渡金属必须处于常见价态或常见价态的混合。
6. 平带不能由孤立原子或 cluster 形成，必须由互连的子晶格贡献。"""


class _FrozenRouteReasoner:
    def __init__(self, proposal: DeepSeekModelRouteProposal) -> None:
        self.proposal = proposal

    def propose_route(self, **_values: object) -> DeepSeekModelRouteProposal:
        return self.proposal

    def propose_feedback(self, **_values: object):
        raise AssertionError("frozen prompt1 route has no feedback delegate")


class _FrozenRouteLiveFeedbackReasoner(_FrozenRouteReasoner):
    def __init__(
        self,
        proposal: DeepSeekModelRouteProposal,
        delegate: DeepSeekScientificLoopReasoner,
    ) -> None:
        super().__init__(proposal)
        self.delegate = delegate

    def propose_feedback(self, **values: object):
        return self.delegate.propose_feedback(**values)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--research-result", type=Path, required=True)
    parser.add_argument("--source-project-root", type=Path, required=True)
    parser.add_argument("--band-data-root", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--live-deepseek-route", action="store_true")
    parser.add_argument("--live-deepseek-feedback", action="store_true")
    parser.add_argument("--route-only", action="store_true")
    args = parser.parse_args()
    if args.route_only and args.live_deepseek_feedback:
        parser.error("feedback requires execution, not --route-only")

    graph = json.loads(args.research_result.read_text())["research_graph"]
    database_records = {
        item["database_candidate_id"]: item
        for item in graph["database_candidates"]
    }
    store = LocalArtifactStore(args.output_root)
    hypotheses: list[HypothesisCandidate] = []
    bundles: dict[str, C2DBBandBundle] = {}
    transition_metals: set[str] = set()
    all_elements: set[str] = set()
    for band_path in sorted(
        args.band_data_root.glob("db-candidate-*/c2db_pbe_band_structure.json.gz")
    ):
        candidate_id = band_path.parent.name
        record = database_records.get(candidate_id)
        if record is None or record.get("source_database") != "c2db":
            continue
        source_uri = record["structure_artifact_uri"]
        source_relative = source_uri.removeprefix("artifact://")
        source_path = args.source_project_root / source_relative
        if not source_path.is_file():
            raise FileNotFoundError(f"missing source structure for {candidate_id}")
        source = artifact_pointer_from_ref(
            store.write_bytes(
                f"inputs/{candidate_id}/structure.cif",
                source_path.read_bytes(),
                "chemical/x-cif",
                immutable=True,
            )
        )
        if source.sha256 != record["structure_artifact_sha256"]:
            raise ValueError(f"source structure hash mismatch for {candidate_id}")
        band_pointer = artifact_pointer_from_ref(
            store.write_bytes(
                f"inputs/{candidate_id}/c2db_pbe_band_structure.json.gz",
                band_path.read_bytes(),
                "application/gzip",
                immutable=True,
            )
        )
        band_payload = json.loads(gzip.decompress(band_path.read_bytes()))
        trace_names = tuple(
            sorted(
                {
                    str(item["name"])
                    for item in band_payload["plotly"]["data"]
                    if isinstance(item, dict) and item.get("name")
                }
            )
        )
        bundle = C2DBBandBundle(
            material_id=band_payload["material_id"],
            method=band_payload["method"],
            source_url=band_payload["source_url"],
            source_structure=source,
            band_data=band_pointer,
            band_gap_ev=record.get("band_gap_ev"),
            trace_names=trace_names,
        )
        bundles[candidate_id] = bundle
        metals = tuple(sorted(record["transition_metals"]))
        elements = tuple(sorted(record["elements"]))
        transition_metals.update(metals)
        all_elements.update(elements)
        hypotheses.append(
            HypothesisCandidate(
                candidate_id=candidate_id,
                material_name=f"C2DB {record['formula']} ({record['source_material_id']})",
                formula=record["formula"],
                hypothesis=(
                    "The retrieved C2DB parent may satisfy the prompt1 2D flat-band "
                    "constraints and can be screened from its official band array."
                ),
                mechanism=(
                    "A connected transition-metal/ligand sublattice may support a "
                    "near-Fermi narrow band; downloaded data provide a fast falsifier."
                ),
                database_candidate_ids=(candidate_id,),
                parent_structure=source,
                elements=elements,
                transition_metal_elements=metals,
                dimensionality=2,
                unresolved_claims=(
                    "bandwidth_le_50_mev",
                    "dispersive_band_no_fermi_crossing",
                    "flat_band_first_near_fermi",
                    "no_first_order_crossing",
                    "tm_or_ligand_hybrid_orbital_character",
                ),
            )
        )
    if not hypotheses:
        raise ValueError("no C2DB candidates with downloaded band arrays were found")
    hypotheses.sort(key=lambda item: item.candidate_id)
    tasks = tuple(
        task
        for hypothesis in hypotheses
        for task in _tasks(hypothesis, database_records[hypothesis.candidate_id])
    )
    frozen_proposal = DeepSeekModelRouteProposal(
        goal_sha256=canonical_sha256({"goal": PROMPT1}),
        provenance=DeepSeekReasoningProvenance(
            prompt_version="prompt1-c2db-frozen-route-v1",
            receipt_sha256="f" * 64,
            live_call=False,
        ),
        route_summary=(
            "For every C2DB parent, check 2D structure and Pd connectivity, import "
            "the official GPAW/PBE band trace, then apply the exact prompt1 rules."
        ),
        tasks=tasks,
    )
    capabilities = _capabilities(
        elements=tuple(sorted(all_elements)),
        observables=tuple(
            sorted(
                {
                    observable
                    for task in tasks
                    for observable in task.requested_observables
                }
            )
        ),
    )
    live_reasoner = (
        DeepSeekScientificLoopReasoner(
            secret_resolver=research_secret_resolver_from_environment(),
            reasoning_effort="high",
        )
        if args.live_deepseek_route or args.live_deepseek_feedback
        else None
    )
    if args.live_deepseek_route:
        assert live_reasoner is not None
        reasoner = live_reasoner
    elif args.live_deepseek_feedback:
        assert live_reasoner is not None
        reasoner = _FrozenRouteLiveFeedbackReasoner(
            frozen_proposal,
            live_reasoner,
        )
    else:
        reasoner = _FrozenRouteReasoner(frozen_proposal)

    def verify(pointer: ArtifactPointerV1) -> bool:
        return store.exists_with_hash(pointer.uri, pointer.sha256)

    with InspirationMemoryStore(
        args.output_root / "research-memory.sqlite3",
        artifact_verifier=verify,
    ) as memory:
        service = ScientificValidationLoopService(
            project_id="prompt1-c2db-ml-only",
            source_run_id="prompt1-c2db-ml-only-r1",
            goal=PROMPT1,
            hypotheses=tuple(hypotheses),
            capabilities=capabilities,
            policy=ValidationRoutePolicy(
                max_candidates=32,
                max_tasks=128,
                require_live_deepseek_reasoning=args.live_deepseek_route,
            ),
            artifact_store=store,
            memory_store=memory,
            reasoner=reasoner,
        )
        route, plan = service.propose_and_audit_route(())
        if args.route_only:
            _print_route(route, plan)
            return 0
        registry = ScientificExecutorRegistry()
        registry.register("local-two-d-v1", LocalTwoDStructureExecutor(store))
        registry.register(
            "c2db-band-import-v1",
            C2DBBandInputImportExecutor(
                store,
                lambda binding: bundles[binding.candidate_id],
            ),
        )
        registry.register(
            "c2db-flat-band-local-v1",
            C2DBFlatBandScientificExecutor(
                store,
                lambda binding: bundles[binding.candidate_id],
            ),
        )
        result = execute_scientific_dag(
            plan=plan,
            service=service,
            executors=registry,
        )
        feedback = None
        cycle = None
        if args.live_deepseek_feedback:
            feedback, cycle = service.propose_and_audit_feedback(result.evidence)
        snapshot = memory.snapshot("prompt1-c2db-ml-only")
    assessments = []
    for evidence in result.evidence:
        if evidence.model_id != "c2db-flat-band-local-v1":
            continue
        assessment = (
            store.read_json(evidence.result_artifact.uri)["assessment"]
            if evidence.result_artifact is not None
            else None
        )
        assessments.append(
            {
                "candidate_id": evidence.candidate_id,
                "evidence_id": evidence.evidence_id,
                "evidence_level": evidence.evidence_level,
                "reason_codes": evidence.reason_codes,
                "verdict": evidence.verdict,
                "assessment": assessment,
            }
        )
    print(
        json.dumps(
            {
                "all_audited_tasks_succeeded": result.all_audited_tasks_succeeded,
                "assessments": assessments,
                "candidate_count": len(hypotheses),
                "feedback": (
                    {
                        "cycle_id": cycle.cycle_id,
                        "cycle_summary": feedback.cycle_summary,
                        "decisions": [
                            {
                                "action": item.proposal.action,
                                "audit_reason_codes": item.reason_codes,
                                "audit_status": item.status,
                                "candidate_id": item.proposal.candidate_id,
                            }
                            for item in cycle.decisions
                        ],
                        "receipt_sha256": feedback.provenance.receipt_sha256,
                    }
                    if feedback is not None and cycle is not None
                    else None
                ),
                "memory_outcomes": len(snapshot.downstream_outcomes),
                "model_task_plan_id": plan.plan_id,
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


def _tasks(
    hypothesis: HypothesisCandidate,
    database_record: dict[str, object],
) -> tuple[ProposedModelTask, ProposedModelTask, ProposedModelTask]:
    suffix = hypothesis.candidate_id.removeprefix("db-candidate-")
    two_d = ProposedModelTask(
        task_id=f"two-d-{suffix}",
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
            "contributor_elements": sorted(database_record["transition_metals"]),
            "minimum_periodic_void_gap_angstrom": 3.0,
        },
        rationale="Check 2D geometry, periodic void, common valence and connectivity.",
        falsification_rule="Reject a non-2D or disconnected contributor network.",
    )
    imported = ProposedModelTask(
        task_id=f"c2db-import-{suffix}",
        candidate_id=hypothesis.candidate_id,
        model_id="c2db-band-import-v1",
        task_kind=ScientificTaskKind.DATABASE_BAND_DATA_IMPORT,
        input_kind=ModelTaskInputKind.PARENT_STRUCTURE,
        produced_artifact_kinds=(
            ScientificArtifactKind.DATABASE_BAND_STRUCTURE,
        ),
        requested_observables=("retrieved_non_soc_band_structure",),
        required_evidence_level=ScientificEvidenceLevel.L1_RETRIEVED,
        rationale="Import the exact official C2DB GPAW/PBE band array.",
        falsification_rule="Block on source structure or band-data hash mismatch.",
    )
    band = ProposedModelTask(
        task_id=f"flat-band-{suffix}",
        candidate_id=hypothesis.candidate_id,
        model_id="c2db-flat-band-local-v1",
        task_kind=ScientificTaskKind.BAND_ORBITAL_ANALYSIS,
        input_kind=ModelTaskInputKind.PREVIOUS_TASK,
        prerequisite_task_ids=(imported.task_id, two_d.task_id),
        required_input_artifact_kinds=(
            ScientificArtifactKind.DATABASE_BAND_STRUCTURE,
            ScientificArtifactKind.TWO_D_STRUCTURE_ASSESSMENT,
        ),
        produced_artifact_kinds=(
            ScientificArtifactKind.FLAT_BAND_ASSESSMENT,
            ScientificArtifactKind.NON_SOC_BAND_STRUCTURE,
        ),
        requested_observables=(
            "bandwidth_le_50_mev",
            "dispersive_band_no_fermi_crossing",
            "flat_band_first_near_fermi",
            "no_first_order_crossing",
        ),
        required_evidence_level=ScientificEvidenceLevel.L1_RETRIEVED,
        parameters={
            "contributor_sublattice_connected": None,
            "fermi_reference": "MIDGAP_FROM_DATABASE",
            "policy": {
                "degeneracy_tolerance_ev": 0.0001,
                "maximum_bandwidth_ev": 0.05,
                "minimum_tm_ligand_weight_fraction": 0.6,
                "minimum_transition_metal_weight_fraction": 0.2,
                "near_fermi_window_ev": 0.05,
                "require_soc_explicit": False,
                "slope_difference_tolerance_ev_angstrom": 0.001,
            },
            "trace_name": "PBE no SOC",
        },
        rationale="Apply the exact 50 meV and Fermi-crossing rules locally.",
        falsification_rule="Contradict on any resolved hard flat-band failure.",
    )
    return two_d, imported, band


def _capabilities(
    *,
    elements: tuple[str, ...],
    observables: tuple[str, ...],
) -> tuple[ModelCapability, ModelCapability, ModelCapability]:
    band_inputs = (
        ScientificArtifactKind.DATABASE_BAND_STRUCTURE,
        ScientificArtifactKind.TWO_D_STRUCTURE_ASSESSMENT,
    )
    band_outputs = (
        ScientificArtifactKind.FLAT_BAND_ASSESSMENT,
        ScientificArtifactKind.NON_SOC_BAND_STRUCTURE,
    )
    return (
        ModelCapability(
            model_id="local-two-d-v1",
            availability=CapabilityAvailability.READY,
            weight_status=WeightStatus.NOT_REQUIRED,
            supported_observables=("two_dimensional_structure",),
            supported_task_kinds=(ScientificTaskKind.TWO_D_STRUCTURE_CHECK,),
            produced_artifact_kinds=(
                ScientificArtifactKind.TWO_D_STRUCTURE_ASSESSMENT,
            ),
            parameter_contract_id="two-d-structure-policy-v1",
            parameter_schema=TwoDStructurePolicy.model_json_schema(),
            supported_elements=elements,
            supported_dimensionalities=(2,),
            evidence_ceiling=ScientificEvidenceLevel.L1_RETRIEVED,
            estimated_cost_units=1,
            real_backend=True,
            benchmark_status="VALIDATED",
        ),
        ModelCapability(
            model_id="c2db-band-import-v1",
            availability=CapabilityAvailability.READY,
            weight_status=WeightStatus.NOT_REQUIRED,
            supported_observables=("retrieved_non_soc_band_structure",),
            supported_task_kinds=(ScientificTaskKind.DATABASE_BAND_DATA_IMPORT,),
            produced_artifact_kinds=(
                ScientificArtifactKind.DATABASE_BAND_STRUCTURE,
            ),
            physics_features=(
                PhysicsCapabilityFeature.K_RESOLVED,
                PhysicsCapabilityFeature.PRECOMPUTED_ELECTRONIC_INPUT_ONLY,
            ),
            parameter_contract_id="empty-parameters-v1",
            parameter_schema={
                "additionalProperties": False,
                "properties": {},
                "type": "object",
            },
            supported_elements=elements,
            supported_dimensionalities=(2,),
            evidence_ceiling=ScientificEvidenceLevel.L1_RETRIEVED,
            estimated_cost_units=1,
            real_backend=True,
            benchmark_status="VALIDATED",
        ),
        ModelCapability(
            model_id="c2db-flat-band-local-v1",
            availability=CapabilityAvailability.READY,
            weight_status=WeightStatus.NOT_REQUIRED,
            supported_observables=tuple(
                item
                for item in observables
                if item
                not in {
                    "retrieved_non_soc_band_structure",
                    "two_dimensional_structure",
                }
            ),
            supported_task_kinds=(ScientificTaskKind.BAND_ORBITAL_ANALYSIS,),
            accepted_artifact_kinds=band_inputs,
            required_input_artifact_kinds=band_inputs,
            produced_artifact_kinds=band_outputs,
            physics_features=(PhysicsCapabilityFeature.K_RESOLVED,),
            parameter_contract_id="c2db-flat-band-analysis-parameters-v1",
            parameter_schema=C2DBFlatBandAnalysisParameters.model_json_schema(),
            supported_elements=elements,
            supported_dimensionalities=(2,),
            evidence_ceiling=ScientificEvidenceLevel.L1_RETRIEVED,
            estimated_cost_units=1,
            real_backend=True,
            benchmark_status="VALIDATED",
        ),
    )


def _print_route(route, plan) -> None:
    print(
        json.dumps(
            {
                "all_tasks_approved": all(item.status == "APPROVED" for item in plan.tasks),
                "live_deepseek": route.provenance.live_call,
                "model_task_plan_id": plan.plan_id,
                "route_summary": route.route_summary,
                "tasks": [
                    {
                        "audit_reason_codes": item.reason_codes,
                        "audit_status": item.status,
                        "candidate_id": item.proposed_task.candidate_id,
                        "model_id": item.proposed_task.model_id,
                        "task_id": item.proposed_task.task_id,
                        "task_kind": item.proposed_task.task_kind,
                    }
                    for item in plan.tasks
                ],
            },
            indent=2,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    raise SystemExit(main())
