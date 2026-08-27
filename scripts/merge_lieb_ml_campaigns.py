#!/usr/bin/env python3
"""Merge resumable Lieb ML campaign shards into one final audit report."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from material_agent.retrieval.storage import LocalArtifactStore


def _load(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text())
    root = path.parents[1]
    for item in payload["structure_results"]:
        item["campaign_artifact_root"] = str(root)
        if item["status"] == "SUCCEEDED":
            relative = item["band_plot"]["uri"].removeprefix("artifact://")
            item["local_band_plot_path"] = str((root / relative).resolve())
    return payload


def _report(payload: dict[str, Any]) -> str:
    summary = payload["summary"]
    lines = [
        "# Hermes 三路线 Lieb lattice ML 最终报告",
        "",
        (
            "> 速度优先、无自洽 DFT。电子结果来自 GPU Uni-HamGNN 学习 Hamiltonian；"
            "能带后处理和 50 meV 硬判据在 CPU/本地执行。所有 ML 结果均为未 benchmark "
            "的筛选证据，不是 DFT 或实验验证。"
        ),
        "",
        "## 总结",
        "",
        f"- 唯一结构：{summary['unique_structures']}；ML 完成：{summary['ml_succeeded']}；工程失败：{summary['ml_failed']}。",
        f"- 数据库母相：{summary['database_parents']}；唯一 registry child CIF：{summary['registry_children']}；operator 执行：{summary['operator_executions']}。",
        f"- ML 硬判据 PASS：{summary['ml_pass']}；FAIL：{summary['ml_fail']}。",
        f"- 无 CIF 的文献假设：{summary['no_structure_input']}，明确记为 `NO_STRUCTURE_INPUT`。",
        f"- 当前最窄近费米带：{summary['best_formula']}，W={summary['best_bandwidth_ev']:.6f} eV。",
        "",
        "## 路线覆盖",
        "",
        "| 路线 | hypotheses | 唯一结构 | 无 CIF | registry child | ML PASS | ML FAIL |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for route in payload["route_summary"]:
        lines.append(
            f"| {route['route']} | {route['hypotheses']} | {route['unique_structures']} | "
            f"{route['no_structure_input']} | {route['registry_children']} | "
            f"{route['ml_pass']} | {route['ml_fail']} |"
        )
    lines.extend(
        [
            "",
            "## 结构级结果",
            "",
            "| SHA | 来源 | formula | W (eV) | verdict | 硬失败 |",
            "|---|---|---|---:|---|---|",
        ]
    )
    for item in payload["structure_results"]:
        assessment = item["flat_band_assessment"]
        lines.append(
            f"| `{item['structure_sha256'][:12]}` | {item['source_kind']} | "
            f"{item.get('formula') or ''} | {assessment['target_bandwidth_ev']:.6f} | "
            f"{assessment['verdict']} | {', '.join(assessment['reason_codes'])} |"
        )
    lines.extend(["", "## Registry child 对比", ""])
    for child in payload["registry_children"]:
        child_result = next(
            item
            for item in payload["structure_results"]
            if item["structure_sha256"] == child["child_structure_sha256"]
        )
        assessment = child_result["flat_band_assessment"]
        lines.append(
            f"- `{child['candidate_id']}` / `{child['operator_id']}`：child CIF "
            f"`{child['child_structure_sha256'][:12]}`，W={assessment['target_bandwidth_ev']:.6f} eV，"
            f"{assessment['verdict']}。"
        )
    if payload.get("deepseek_operator_round") is not None:
        operator_round = payload["deepseek_operator_round"]
        lines.extend(
            [
                "",
                "## DeepSeek 反馈轮",
                "",
                f"- 选择恢复：`{operator_round['batch']['selection_recovery']}`。",
                (
                    f"- 编译尝试："
                    f"{operator_round['planning_audit']['compile_attempt_count']}；"
                    f"执行 child：{len(operator_round['generated'])}。"
                ),
                "- DeepSeek 最终选择响应未完成后，只恢复已由其 compiler 工具调用生成并写入不可变 checkpoint 的 plan；mentor 未补写科学参数。",
            ]
        )
    lines.extend(
        [
            "",
            "## 尚未被该模型解决的硬条件",
            "",
            "- 当前 band 输出没有轨道投影，TM 或 TM–配体杂化权重仍未验证。",
            "- 当前输出没有能带态贡献位点的周期 Lieb 连通图，也没有 IPR/局域态排除。",
            "- 形式价态仍需结构/组成 ledger 与混合价态审计；ML band 不直接给价态。",
            "- 未做模型误差校准，故即使点估计低于 50 meV 也不能自动升级为 verified。",
            "",
            "## 能带图",
            "",
        ]
    )
    for item in payload["structure_results"]:
        lines.extend(
            [
                f"### {item.get('formula') or item['structure_sha256'][:12]} ({item['structure_sha256'][:12]})",
                "",
                f"![SOC ML band]({item['local_band_plot_path']})",
                "",
            ]
        )
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--campaign", type=Path, action="append", required=True)
    parser.add_argument("--operator-round", type=Path)
    parser.add_argument("--output-root", type=Path, required=True)
    args = parser.parse_args()
    shards = [_load(path.resolve()) for path in args.campaign]
    base = shards[0]
    result_by_sha: dict[str, dict[str, Any]] = {}
    for shard in shards:
        for item in shard["structure_results"]:
            prior = result_by_sha.get(item["structure_sha256"])
            if prior is None or item["status"] == "SUCCEEDED":
                result_by_sha[item["structure_sha256"]] = item
    results = [result_by_sha[sha] for sha in sorted(result_by_sha)]
    if any(item["status"] != "SUCCEEDED" for item in results):
        raise ValueError("final merge still contains failed ML structures")
    hypotheses = base["hypotheses"]
    for item in hypotheses:
        shas = item["parent_structure_sha256s"]
        item["ml_status"] = (
            "PARENT_ML_SUCCEEDED" if shas else "NO_STRUCTURE_INPUT"
        )
    children = base["registry_children"]
    for item in children:
        item["ml_status"] = "CHILD_ML_SUCCEEDED"
    operator_round = None
    if args.operator_round is not None:
        operator_round = json.loads(args.operator_round.read_text())
        for item in operator_round["generated"]:
            candidate = item["candidate"]
            if candidate is None:
                continue
            sha = candidate["parent_structure"]["sha256"]
            if sha not in result_by_sha:
                raise ValueError("DeepSeek child lacks a merged ML result")
            children.append(
                {
                    "route": "deepseek-resubmission",
                    "candidate_id": candidate["candidate_id"],
                    "plan_id": item["operator_result"]["plan_id"],
                    "operator_id": item["operator_result"]["operator_id"],
                    "compile_prior_decision": "REQUIRES_REVIEW",
                    "execution_status": item["operator_result"]["status"],
                    "child_structure_sha256": sha,
                    "ml_status": "CHILD_ML_SUCCEEDED",
                    "reason_codes": item["operator_result"]["reason_codes"],
                    "selection_recovery": operator_round["batch"][
                        "selection_recovery"
                    ],
                }
            )
    routes = sorted(
        {item["route"] for item in hypotheses}
        | {
            route
            for item in results
            for route in item["route_names"]
        }
    )
    route_summary = []
    for route in routes:
        route_hypotheses = [item for item in hypotheses if item["route"] == route]
        route_shas = {
            sha
            for item in results
            if route in item["route_names"]
            for sha in (item["structure_sha256"],)
        }
        route_results = [result_by_sha[sha] for sha in route_shas]
        route_summary.append(
            {
                "route": route,
                "hypotheses": len(route_hypotheses),
                "unique_structures": len(route_shas),
                "no_structure_input": sum(
                    item["ml_status"] == "NO_STRUCTURE_INPUT"
                    for item in route_hypotheses
                ),
                "registry_children": sum(
                    item["route"] == route for item in children
                ),
                "ml_pass": sum(
                    item["flat_band_assessment"]["verdict"] == "PASS"
                    for item in route_results
                ),
                "ml_fail": sum(
                    item["flat_band_assessment"]["verdict"] == "FAIL"
                    for item in route_results
                ),
            }
        )
    best = min(
        results,
        key=lambda item: item["flat_band_assessment"]["target_bandwidth_ev"],
    )
    payload = {
        "schema_version": "hermes-lieb-ml-final-report-v1",
        "execution_boundary": base["execution_boundary"],
        "hypotheses": hypotheses,
        "registry_children": children,
        "deepseek_operator_round": operator_round,
        "structure_results": results,
        "route_summary": route_summary,
        "summary": {
            "unique_structures": len(results),
            "database_parents": sum(
                item["source_kind"] == "DATABASE_PARENT" for item in results
            ),
            "registry_children": sum(
                item["source_kind"]
                in {"REGISTRY_CHILD", "DEEPSEEK_REGISTRY_CHILD"}
                for item in results
            ),
            "operator_executions": len(children),
            "ml_succeeded": len(results),
            "ml_failed": 0,
            "ml_pass": sum(
                item["flat_band_assessment"]["verdict"] == "PASS"
                for item in results
            ),
            "ml_fail": sum(
                item["flat_band_assessment"]["verdict"] == "FAIL"
                for item in results
            ),
            "no_structure_input": sum(
                item["ml_status"] == "NO_STRUCTURE_INPUT" for item in hypotheses
            ),
            "best_formula": best.get("formula"),
            "best_structure_sha256": best["structure_sha256"],
            "best_bandwidth_ev": best["flat_band_assessment"][
                "target_bandwidth_ev"
            ],
        },
        "scientific_conclusion": False,
    }
    store = LocalArtifactStore(args.output_root)
    result_ref = store.write_json("campaign/result.json", payload, immutable=True)
    report_ref = store.write_text(
        "campaign/report.md",
        _report(payload),
        "text/markdown",
        immutable=True,
    )
    print(
        json.dumps(
            {
                "result_uri": result_ref.uri,
                "report_uri": report_ref.uri,
                **payload["summary"],
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
