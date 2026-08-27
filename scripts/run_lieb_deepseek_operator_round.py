#!/usr/bin/env python3
"""Let DeepSeek resubmit registry-executable operators from real Lieb ML failures."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from material_agent.inspiration.deepseek_agent import DeepSeekAgentBudgetV1
from material_agent.inspiration.models import (
    ArtifactPointerV1,
    deterministic_id,
)
from material_agent.inspiration.research_graph import DatabaseCandidateV1
from material_agent.integration.generic_research import (
    research_secret_resolver_from_environment,
)
from material_agent.integration.registry_search_adapters import (
    DeepSeekRegistryOperatorProposer,
    IterativeParentCatalog,
    RegistryStructureOperatorExecutor,
)
from material_agent.integration.scientific_loop import (
    HypothesisCandidate,
    ScientificEvidence,
    ScientificEvidenceLevel,
    ScientificEvidenceVerdict,
    ScientificTaskKind,
)
from material_agent.retrieval.storage import LocalArtifactStore

GOAL = """晶格中由分数价态过渡金属组成周期互连的 Lieb lattice；平带位于费米面或其边缘，
带宽 W<=50 meV。除三条 Lieb 能带外没有其他能带穿过费米面，平带与色散带没有一阶交点。
当前母相已经由真实 ML Hamiltonian/能带淘汰。请根据各自失败向量提出最小、可由动态 registry
编译并生成 child CIF 的结构操作；运行时禁止 DFT。不要提出 condition-only 操作，不要宣称编译即验证。"""


class _Feedback:
    def model_dump(self, **_values: object) -> dict[str, object]:
        return {
            "cycle_id": "lieb-parent-ml-negative-feedback-v1",
            "decisions": (),
            "scientific_conclusion": False,
        }


def _pointer(reference: Any) -> ArtifactPointerV1:
    return ArtifactPointerV1(
        uri=reference.uri,
        sha256=reference.sha256,
        size_bytes=reference.size_bytes,
        media_type=reference.media_type,
    )


def _source_path(result_path: Path, uri: str) -> Path:
    return (result_path.parents[2] / uri.removeprefix("artifact://")).resolve()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--research-result", type=Path, action="append", required=True)
    parser.add_argument("--ml-result", type=Path, required=True)
    parser.add_argument("--artifact-root", type=Path, required=True)
    parser.add_argument("--frontier-size", type=int, default=6)
    parser.add_argument("--max-plans", type=int, default=4)
    args = parser.parse_args()
    store = LocalArtifactStore(args.artifact_root)
    ml_payload = json.loads(args.ml_result.read_text())
    ml_by_sha = {
        item["structure_sha256"]: item
        for item in ml_payload["structure_results"]
        if item["source_kind"] == "DATABASE_PARENT"
    }
    catalog_by_id: dict[str, tuple[DatabaseCandidateV1, Path]] = {}
    narratives: dict[str, list[str]] = {}
    for raw_path in args.research_result:
        result_path = raw_path.resolve()
        payload = json.loads(result_path.read_text())
        graph = payload["research_graph"]
        for raw in graph["database_candidates"]:
            sha = raw.get("structure_artifact_sha256")
            if sha not in ml_by_sha:
                continue
            record = DatabaseCandidateV1.model_validate_json(json.dumps(raw))
            catalog_by_id.setdefault(
                record.database_candidate_id,
                (record, _source_path(result_path, record.structure_artifact_uri)),
            )
        for candidate in graph["candidates"]["candidates"]:
            for database_id in candidate["database_candidate_ids"]:
                narratives.setdefault(database_id, []).append(candidate["hypothesis"])
    ranked = sorted(
        catalog_by_id.items(),
        key=lambda item: ml_by_sha[item[1][0].structure_artifact_sha256][
            "flat_band_assessment"
        ]["target_bandwidth_ev"],
    )[: args.frontier_size]
    databases: list[DatabaseCandidateV1] = []
    hypotheses: list[HypothesisCandidate] = []
    evidence: list[ScientificEvidence] = []
    for database_id, (record, source_path) in ranked:
        payload = source_path.read_bytes()
        reference = store.write_bytes(
            f"inputs/parents/{database_id}.cif",
            payload,
            "chemical/x-cif",
            immutable=True,
        )
        if reference.sha256 != record.structure_artifact_sha256:
            raise ValueError("DeepSeek frontier CIF hash differs from database record")
        rebound = record.model_copy(
            update={"structure_artifact_uri": reference.uri}
        )
        databases.append(rebound)
        ml = ml_by_sha[record.structure_artifact_sha256]
        assessment = ml["flat_band_assessment"]
        reasons = tuple(sorted(set(assessment["reason_codes"])))
        combined_narrative = " ".join(narratives.get(database_id, ()))
        hypotheses.append(
            HypothesisCandidate(
                candidate_id=database_id,
                material_name=(
                    f"{record.formula} {record.source_database}:"
                    f"{record.source_material_id}"
                ),
                formula=record.formula,
                hypothesis=(
                    f"The parent failed real ML screening with W="
                    f"{assessment['target_bandwidth_ev']:.6f} eV and reason codes "
                    f"{', '.join(reasons)}. DeepSeek may propose a minimal structural "
                    "descendant; the child must be screened afresh."
                ),
                mechanism=(
                    (combined_narrative[:3_500] or "Evidence-directed hopping tuning.")
                ),
                database_candidate_ids=(database_id,),
                parent_structure=_pointer(reference),
                elements=record.elements,
                transition_metal_elements=record.transition_metals,
                dimensionality=record.dimensionality,
                unresolved_claims=tuple(
                    sorted(
                        {
                            "bandwidth_le_50_mev",
                            "dispersive_band_no_fermi_crossing",
                            "fractional_common_tm_valence",
                            "lieb_three_band_isolation",
                            "no_first_order_crossing",
                            "periodic_tm_lieb_connectivity",
                        }
                    )
                ),
                priority=max(
                    1,
                    1_000 - int(assessment["target_bandwidth_ev"] * 1_000),
                ),
            )
        )
        result_ref = store.write_json(
            f"evidence/{database_id}.json",
            {
                "source_structure_sha256": record.structure_artifact_sha256,
                "assessment": assessment,
                "gpu_device_name": ml["gpu_device_name"],
                "self_consistent_dft_invocations": ml[
                    "self_consistent_dft_invocations"
                ],
                "scientific_conclusion": False,
            },
            immutable=True,
        )
        values = {
            "schema_version": "scientific-validation-loop-v1",
            "candidate_id": database_id,
            "model_task_plan_id": "model-task-plan-lieb-parent-ml-v1",
            "task_id": f"task-band-{record.structure_artifact_sha256[:20]}",
            "model_id": "uni-hamgnn-soc-2.1-zenodo-17239078",
            "task_kind": ScientificTaskKind.BAND_ORBITAL_ANALYSIS,
            "execution_status": "SUCCEEDED",
            "verdict": ScientificEvidenceVerdict.CONTRADICTS,
            "tested_claim_ids": (
                "bandwidth_le_50_mev",
                "dispersive_band_no_fermi_crossing",
                "no_first_order_crossing",
            ),
            "evidence_level": ScientificEvidenceLevel.NONE,
            "result_artifact": _pointer(result_ref),
            "consumed_artifact_ids": (),
            "produced_artifacts": (),
            "runtime_provenance": dict(
                sorted(
                    {
                        "gpu_device_name": ml["gpu_device_name"],
                        "hamiltonian_sha256": ml["hamiltonian_sha256"],
                        "self_consistent_dft_invocations": 0,
                    }.items()
                )
            ),
            "reason_codes": reasons,
            "real_execution": True,
            "scientific_conclusion": False,
        }
        evidence.append(
            ScientificEvidence(
                evidence_id=deterministic_id("scientific-evidence", values),
                **values,
            )
        )
    catalog = IterativeParentCatalog(
        store=store,
        database_candidates=tuple(databases),
    )
    proposer = DeepSeekRegistryOperatorProposer(
        store=store,
        database_candidates=tuple(databases),
        parent_catalog=catalog,
        secret_resolver=research_secret_resolver_from_environment(),
        reasoning_effort="high",
        max_plans_per_reasoning=args.max_plans,
        budget=DeepSeekAgentBudgetV1(
            max_rounds=8,
            max_tool_calls=24,
            max_completion_tokens_per_round=32_768,
            max_total_tokens=160_000,
            max_walltime_seconds=900,
        ),
    )
    batch = proposer.propose_operators(
        goal=GOAL,
        iteration=1,
        hypotheses=tuple(hypotheses),
        evidence=tuple(evidence),
        feedback=_Feedback(),  # type: ignore[arg-type]
        memory_snapshot_id="lieb-parent-ml-final-snapshot-v1",
        remaining_candidate_slots=args.max_plans,
        remaining_cost_units=args.max_plans,
    )
    parent_by_id = {item.candidate_id: item for item in hypotheses}
    executor = RegistryStructureOperatorExecutor.from_proposer(proposer)
    generated = []
    for proposal in batch.proposals:
        result = executor.execute(
            proposal=proposal,
            parent=parent_by_id[proposal.parent_candidate_id],
        )
        generated.append(
            {
                "proposal": proposal.model_dump(mode="json"),
                "operator_result": result.operator_result.model_dump(mode="json"),
                "candidate": (
                    result.candidate.model_dump(mode="json")
                    if result.candidate is not None
                    else None
                ),
                "runtime_provenance": result.runtime_provenance,
            }
        )
    final = {
        "schema_version": "hermes-lieb-deepseek-operator-round-v1",
        "goal": GOAL,
        "frontier_candidate_ids": [item.candidate_id for item in hypotheses],
        "evidence_ids": [item.evidence_id for item in evidence],
        "batch": batch.model_dump(mode="json"),
        "planning_audit": proposer.planning_audit.model_dump(mode="json"),
        "generated": generated,
        "checkpoint_uris": proposer.checkpoint_uris,
        "last_user_payload_bytes": proposer.last_user_payload_bytes,
        "scientific_conclusion": False,
    }
    result_ref = store.write_json("operator_round/result.json", final, immutable=True)
    print(
        json.dumps(
            {
                "result_uri": result_ref.uri,
                "proposals": len(batch.proposals),
                "generated_child_cifs": sum(
                    item["candidate"] is not None for item in generated
                ),
                "selected_plan_ids": [
                    item["operator_result"]["plan_id"] for item in generated
                ],
                "checkpoint_uris": proposer.checkpoint_uris,
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
