#!/usr/bin/env python3
"""Continue Prompt1 after all retrieved parents fail, without runtime DFT."""

from __future__ import annotations

import argparse
import json
import re
from datetime import UTC, datetime
from pathlib import Path

from pymatgen.core import Element

from material_agent.inspiration.deepseek_agent import DeepSeekAgentBudgetV1
from material_agent.inspiration.models import ArtifactPointerV1
from material_agent.inspiration.research_graph import DatabaseCandidateV1
from material_agent.inspiration.research_memory import InspirationMemoryStore
from material_agent.integration.electronic_structure import TwoDStructurePolicy
from material_agent.integration.generic_research import (
    research_secret_resolver_from_environment,
)
from material_agent.integration.registry_search_adapters import (
    DeepSeekRegistryOperatorProposer,
    IterativeParentCatalog,
    RegistryStructureOperatorExecutor,
)
from material_agent.integration.scientific_execution import ScientificExecutorRegistry
from material_agent.integration.scientific_executors import LocalTwoDStructureExecutor
from material_agent.integration.scientific_loop import (
    CapabilityAvailability,
    DeepSeekScientificLoopReasoner,
    FeedbackCycleResult,
    HypothesisCandidate,
    ModelCapability,
    ScientificArtifactKind,
    ScientificEvidence,
    ScientificEvidenceLevel,
    ScientificTaskKind,
    ValidationRoutePolicy,
    WeightStatus,
)
from material_agent.integration.scientific_search_loop import (
    ScientificCandidateSearchLoop,
    ScientificSearchBudget,
)
from material_agent.retrieval.storage import LocalArtifactStore

PROMPT1 = """搜索数据库中的过渡金属二维平带材料。要求：
1. 必须是层状材料，有 vdW gap 的材料最优先。
2. 平带必须是费米面附近的第一条能带，贡献这条能带的电子轨道必须由过渡金属元素或过渡金属与配体的杂化态构成。
3. 平带和其他色散较大的能带不能有一阶交点，色散能带不能穿过费米面，但它们可以在高对称点有公共极值点。
4. 平带的定义是带宽 W<=50 meV 的能带。
5. 进行价态分析，过渡金属必须处于常见价态或常见价态的混合。
6. 平带不能由孤立原子或 cluster 形成，必须由互连的子晶格贡献。

当数据库母相被真实数据淘汰后，继续提出最小结构操作并生成新结构。速度优先，
但不得把未知电子性质写成通过；运行时禁止 DFT。"""

_RUN_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--research-result", type=Path, required=True)
    parser.add_argument("--artifact-root", type=Path, required=True)
    parser.add_argument("--source-run-id")
    parser.add_argument("--max-iterations", type=int, default=3)
    parser.add_argument("--max-candidates", type=int, default=12)
    parser.add_argument("--max-cost-units", type=int, default=128)
    parser.add_argument("--max-plans-per-reasoning", type=int, default=4)
    parser.add_argument("--resume-operator-checkpoint")
    args = parser.parse_args()

    store = LocalArtifactStore(args.artifact_root)
    source_run_id = args.source_run_id or _fresh_run_id()
    if not _RUN_ID.fullmatch(source_run_id):
        raise ValueError("source run ID must be path-safe and at most 128 characters")
    run_root = store.root / "scientific_loop" / source_run_id
    if run_root.exists():
        raise ValueError(
            f"source run ID already exists; choose a fresh ID: {source_run_id}"
        )
    research_payload = json.loads(args.research_result.read_text())
    research_input_ref = store.write_json(
        f"scientific_loop/{source_run_id}/inputs/research-result.json",
        research_payload,
        immutable=True,
    )
    hypotheses, database_candidates = _load_parents(
        research_result_payload=research_payload,
        store=store,
    )
    evidence = tuple(
        ScientificEvidence.model_validate_json(path.read_bytes())
        for path in sorted(
            (
                args.artifact_root / "scientific_loop/prompt1-c2db-ml-only-r1/evidence"
            ).glob("*.json")
        )
    )
    feedback_paths = sorted(
        (args.artifact_root / "scientific_loop/prompt1-c2db-ml-only-r1/cycles").glob(
            "*.json"
        )
    )
    if not feedback_paths or not evidence:
        raise ValueError("the completed Prompt1 parent-screening evidence is required")
    feedback = FeedbackCycleResult.model_validate_json(feedback_paths[-1].read_bytes())

    secret_resolver = research_secret_resolver_from_environment()
    catalog = IterativeParentCatalog(
        store=store,
        database_candidates=database_candidates,
    )
    operator_budget = DeepSeekAgentBudgetV1(
        max_rounds=4,
        max_tool_calls=12,
        max_completion_tokens_per_round=32_768,
        max_total_tokens=100_000,
        max_walltime_seconds=900,
    )
    proposer = DeepSeekRegistryOperatorProposer(
        store=store,
        database_candidates=database_candidates,
        parent_catalog=catalog,
        secret_resolver=secret_resolver,
        budget=operator_budget,
        reasoning_effort="high",
        max_plans_per_reasoning=args.max_plans_per_reasoning,
    )
    if args.resume_operator_checkpoint:
        proposer.recover_checkpoint(args.resume_operator_checkpoint)
    operator_executor = RegistryStructureOperatorExecutor.from_proposer(proposer)
    inspection_budget = DeepSeekAgentBudgetV1(
        max_rounds=4,
        max_tool_calls=2,
        max_completion_tokens_per_round=32_768,
        max_total_tokens=80_000,
        max_walltime_seconds=900,
    )
    scientific_reasoner = DeepSeekScientificLoopReasoner(
        secret_resolver=secret_resolver,
        budget=inspection_budget,
        reasoning_effort="high",
    )
    capabilities = (_local_structure_capability(),)
    scientific_executors = ScientificExecutorRegistry()
    scientific_executors.register("local-two-d-v1", LocalTwoDStructureExecutor(store))

    def verify(pointer: ArtifactPointerV1) -> bool:
        return store.exists_with_hash(pointer.uri, pointer.sha256)

    memory_path = args.artifact_root / "research-memory.sqlite3"
    with InspirationMemoryStore(memory_path, artifact_verifier=verify) as memory:
        loop = ScientificCandidateSearchLoop(
            project_id="prompt1-c2db-ml-only",
            source_run_id=source_run_id,
            goal=PROMPT1,
            capabilities=capabilities,
            route_policy=ValidationRoutePolicy(
                max_candidates=args.max_candidates,
                max_tasks=64,
                max_cost_units=args.max_cost_units,
                require_live_deepseek_reasoning=True,
            ),
            search_budget=ScientificSearchBudget(
                max_iterations=args.max_iterations,
                max_candidates=args.max_candidates,
                max_cost_units=args.max_cost_units,
            ),
            artifact_store=store,
            memory_store=memory,
            scientific_reasoner=scientific_reasoner,
            operator_proposer=proposer,
            operator_executor=operator_executor,
            scientific_executors=scientific_executors,
        )
        result = loop.run(
            hypotheses=hypotheses,
            evidence=evidence,
            feedback=feedback,
        )
        planning_audit = proposer.planning_audit
        audit_ref = store.write_json(
            f"scientific_loop/{source_run_id}/operator-planning-audit.json",
            planning_audit.model_dump(mode="json"),
            immutable=True,
        )
        checkpoint_manifest_ref = store.write_json(
            f"scientific_loop/{source_run_id}/operator-checkpoint-manifest.json",
            {
                "source_run_id": source_run_id,
                "checkpoint_uris": proposer.checkpoint_uris,
                "final_operator_planning_audit_uri": audit_ref.uri,
                "research_input_uri": research_input_ref.uri,
                "last_compressed_reasoning_payload_bytes": (
                    proposer.last_user_payload_bytes
                ),
                "scientific_conclusion": False,
            },
            immutable=True,
        )

    print(
        json.dumps(
            {
                "search_id": result.search_id,
                "stop_reason": result.stop_reason,
                "final_action": result.final_state.action,
                "all_candidate_ids": result.all_candidate_ids,
                "generated_structure_candidate_ids": (
                    result.final_state.generated_structure_candidate_ids
                ),
                "ml_screening_candidate_ids": (
                    result.final_state.ml_screening_candidate_ids
                ),
                "capability_gap_claim_ids": result.capability_gap_claim_ids,
                "iterations": len(result.iterations),
                "total_cost_units": result.total_cost_units,
                "operator_planning_audit_uri": audit_ref.uri,
                "operator_checkpoint_manifest_uri": checkpoint_manifest_ref.uri,
                "operator_checkpoint_count": len(proposer.checkpoint_uris),
                "research_input_uri": research_input_ref.uri,
                "last_compressed_reasoning_payload_bytes": (
                    proposer.last_user_payload_bytes
                ),
                "source_run_id": source_run_id,
                "execution_mode": "ML_ONLY",
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


def _fresh_run_id() -> str:
    return f"prompt1-iterative-{datetime.now(UTC):%Y%m%dT%H%M%S%fZ}"


def _load_parents(
    *, research_result_payload: object, store: LocalArtifactStore
) -> tuple[tuple[HypothesisCandidate, ...], tuple[DatabaseCandidateV1, ...]]:
    if not isinstance(research_result_payload, dict):
        raise TypeError("research result must be a JSON object")
    graph = research_result_payload["research_graph"]
    records = {
        item["database_candidate_id"]: DatabaseCandidateV1.model_validate(item)
        for item in graph["database_candidates"]
        if item["source_database"] == "c2db"
    }
    hypotheses: list[HypothesisCandidate] = []
    rebound: list[DatabaseCandidateV1] = []
    for candidate_id, record in sorted(records.items()):
        relative = f"inputs/{candidate_id}/structure.cif"
        if not store.exists(relative):
            continue
        ref = store.inspect(relative, media_type="chemical/x-cif")
        if ref.sha256 != record.structure_artifact_sha256:
            raise ValueError(f"Prompt1 parent structure hash drift: {candidate_id}")
        pointer = ArtifactPointerV1(
            uri=ref.uri,
            sha256=ref.sha256,
            size_bytes=ref.size_bytes,
            media_type=ref.media_type,
        )
        rebound.append(
            record.model_copy(update={"structure_artifact_uri": pointer.uri})
        )
        hypotheses.append(
            HypothesisCandidate(
                candidate_id=candidate_id,
                material_name=f"C2DB {record.formula} ({record.source_material_id})",
                formula=record.formula,
                hypothesis=(
                    "This retrieved parent failed Prompt1, but a dynamically reasoned "
                    "minimal structural operation may create a useful descendant."
                ),
                mechanism=(
                    "A symmetry-aware local change may alter hopping while preserving "
                    "the periodic transition-metal/ligand network."
                ),
                database_candidate_ids=(candidate_id,),
                parent_structure=pointer,
                elements=record.elements,
                transition_metal_elements=record.transition_metals,
                dimensionality=2,
                unresolved_claims=tuple(
                    sorted(
                        {
                            "bandwidth_le_50_mev",
                            "dispersive_band_no_fermi_crossing",
                            "flat_band_first_near_fermi",
                            "no_first_order_crossing",
                            "tm_or_ligand_hybrid_orbital_character",
                            "two_dimensional_structure",
                        }
                    )
                ),
            )
        )
    if not hypotheses:
        raise ValueError("no Prompt1 C2DB parents were rebound to the artifact store")
    return tuple(hypotheses), tuple(rebound)


def _local_structure_capability() -> ModelCapability:
    return ModelCapability(
        model_id="local-two-d-v1",
        availability=CapabilityAvailability.READY,
        weight_status=WeightStatus.NOT_REQUIRED,
        supported_observables=("two_dimensional_structure",),
        supported_task_kinds=(ScientificTaskKind.TWO_D_STRUCTURE_CHECK,),
        produced_artifact_kinds=(ScientificArtifactKind.TWO_D_STRUCTURE_ASSESSMENT,),
        parameter_contract_id="two-d-structure-policy-v1",
        parameter_schema=TwoDStructurePolicy.model_json_schema(),
        supported_elements=tuple(sorted(item.symbol for item in Element)),
        supported_dimensionalities=(2,),
        evidence_ceiling=ScientificEvidenceLevel.L1_RETRIEVED,
        estimated_cost_units=1,
        real_backend=True,
        benchmark_status="VALIDATED",
    )


if __name__ == "__main__":
    raise SystemExit(main())
