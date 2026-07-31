"""Retrieval report generation."""

from __future__ import annotations

from collections import Counter
from typing import Any

from material_agent.retrieval.models import (
    CandidateRecord,
    Decision,
    RetrievalQueryPlan,
    SourceDatabase,
    StageStatus,
)


def build_report(
    *,
    query_plan: RetrievalQueryPlan,
    candidates: list[CandidateRecord],
    raw_count: int,
    scan_truncated: bool,
    status: StageStatus,
    warnings: list[str],
    exact_duplicate_groups: list[dict[str, object]],
    similarity_clusters: list[dict[str, object]],
    report_enrichment: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    decision_counts = Counter(candidate.decision.value for candidate in candidates)
    reason_counts = Counter(
        reason for candidate in candidates for reason in candidate.decision_reasons
    )
    quality_flag_counts = Counter(
        flag for candidate in candidates for flag in candidate.data_quality_flags
    )
    published = [candidate for candidate in candidates if candidate.published_downstream]
    unresolved_properties = sum(
        1
        for candidate in candidates
        for prop in candidate.properties
        if prop.origin.status.value == "UNRESOLVED"
    )
    return {
        "stage": "agent01",
        "status": status.value,
        "query": {
            "source_database": query_plan.source_database.value,
            "query_id": query_plan.query_id,
            "query_fingerprint": query_plan.query_fingerprint,
            "database_version": query_plan.database_version,
            "client_version": query_plan.client_version,
            "endpoint": query_plan.endpoint,
            "retrieved_at": query_plan.created_at.isoformat(),
            "pushdown_filters": query_plan.pushdown_filters,
            "local_only_constraints": query_plan.local_only_constraints,
            "include_gnome": query_plan.include_gnome,
            "include_deprecated": query_plan.include_deprecated,
        },
        "funnel": {
            "database_returned": raw_count,
            "normalized": len(candidates),
            "failed": decision_counts[Decision.FAILED.value],
            "rejected": decision_counts[Decision.REJECT.value],
            "uncertain": decision_counts[Decision.UNCERTAIN.value],
            "passed": decision_counts[Decision.PASS.value],
            "published_downstream": len(published),
        },
        "limits": {
            "max_records_scanned": query_plan.max_records_scanned,
            "max_candidates_published": query_plan.max_candidates_published,
            "scan_truncated": scan_truncated,
            "publication_truncated": len(
                [
                    candidate
                    for candidate in candidates
                    if candidate.decision is Decision.PASS
                ]
            )
            > query_plan.max_candidates_published,
        },
        "data_quality": {
            "unresolved_property_origins": unresolved_properties,
            "reason_counts": dict(sorted(reason_counts.items())),
            "quality_flag_counts": dict(sorted(quality_flag_counts.items())),
            "exact_duplicate_group_count": len(exact_duplicate_groups),
            "similarity_cluster_count": len(similarity_clusters),
        },
        "warnings": sorted(set(warnings)),
        "evidence_statement": (
            "All database and structure-derived results are at most L1_RETRIEVED. "
            "No result is claimed as ML, DFT, many-body, or experimental validation."
        ),
        "published_candidates": [
            {
                "rank": candidate.publication_rank,
                "candidate_id": candidate.candidate_id,
                "material_id": candidate.source_material_id,
                "formula": candidate.formula,
                "decision": candidate.decision.value,
                "missing_evidence": candidate.missing_evidence,
                "structure_uri": candidate.structure_artifact_uri,
            }
            for candidate in sorted(
                published, key=lambda item: item.publication_rank or 0
            )
        ],
        "materials_project_enrichment": report_enrichment or [],
    }


def report_to_markdown(report: dict[str, Any], workspace_dir: str | None = None) -> str:
    funnel = report["funnel"]
    limits = report["limits"]
    query = report["query"]
    source = SourceDatabase(query["source_database"])
    source_label = {
        SourceDatabase.MATERIALS_PROJECT: "Materials Project",
        SourceDatabase.NOMAD: "NOMAD",
        SourceDatabase.MC3D: "Materials Cloud MC3D",
        SourceDatabase.C2DB: "C2DB",
        SourceDatabase.TOPOLOGICAL_QUANTUM_CHEMISTRY: (
            "Topological Quantum Chemistry"
        ),
        SourceDatabase.NIMS_SUPERCON: "NIMS MDR SuperCon",
    }[source]
    source_id_label = {
        SourceDatabase.MATERIALS_PROJECT: "MP ID",
        SourceDatabase.NOMAD: "NOMAD entry ID",
        SourceDatabase.MC3D: "MC3D ID",
        SourceDatabase.C2DB: "C2DB UID",
        SourceDatabase.TOPOLOGICAL_QUANTUM_CHEMISTRY: "ICSD ID",
        SourceDatabase.NIMS_SUPERCON: "SuperCon record ID",
    }[source]
    lines = [
        f"# Agent 01 {source_label} 检索报告",
        "",
        "**报告概述**：",
        "本报告详细记录了 Agent 01 (Retrieval Agent) 在 Materials Project 数据库中执行的自动化高通量检索与复核结果。基于前期大语言模型（LLM）解析并经过专家审批的约束条件（如带隙 1.5~2.0 eV、剔除有毒元素、结构稳定等），系统初步拉取了原始数据。随后，Agent 01 在本地进行了严格的确定性物理复核、结构规范化与维度分析，成功剔除不符合要求的候选项，最终确认高置信度候选材料通过（PASS）并发布至下游。报告内详细列出了候选材料的基础热力学属性、晶体对称性、能带特征，以及基于本地生成的原生 3D 晶体结构可视化图片。",
        "",
        f"- Stage 状态：`{report['status']}`",
        f"- 数据库版本：`{query['database_version']}`",
        f"- 检索时间：`{query['retrieved_at']}`",
        f"- Query ID：`{query['query_id']}`",
        f"- Query fingerprint：`{query['query_fingerprint']}`",
        f"- GNoME：`{query['include_gnome']}`",
        f"- 扫描是否截断：`{limits['scan_truncated']}`",
        "",
        "## 术语与状态说明 (Glossary & Status Dictionary)",
        "",
        "### 1. 流水线与系统状态词",
        "*   **Stage 状态 (`PARTIAL` / `COMPLETE`)**: 检索阶段的完成状态。`PARTIAL` 通常表示虽然检索成功，但出于配额保护或网络原因，部分重型计算数据（如庞大的电荷密度文件）被主动跳过或截断。",
        "*   **GNoME (`False` / `True`)**: 表示本次检索结果中是否包含了由 Google DeepMind 的 GNoME（Graph Networks for Materials Exploration）项目预测的理论材料。",
        "*   **扫描是否截断 (`False` / `True`)**: 表示数据库返回的候选材料数量是否超过了系统设定的最大处理上限。`False` 表示系统成功获取并处理了所有符合条件的材料。",
        "",
        "### 2. 物理与热力学属性 (Summary)",
        "*   **Energy Above Hull (`e_above_hull`)**: 凸包能量。衡量材料热力学稳定性的核心指标。值为 0 表示材料在热力学基态是绝对稳定的；大于 0 的值表示亚稳态，值越大越容易分解为其他相。",
        "*   **Band Gap**: 带隙。决定材料导电性质的关键物理量，通常以 eV（电子伏特）为单位。",
        "*   **Magnetic Ordering**: 磁序。描述材料内部自旋排列的方式（如 Non-magnetic 非磁性、Ferromagnetic 铁磁性、Antiferromagnetic 反铁磁性等）。",
        "*   **Total Magnetization**: 总磁矩。晶胞内所有原子磁矩的矢量和，通常以玻尔磁子 ($\mu_B$) 为单位。",
        "",
        "### 3. 晶体学对称性 (Symmetry)",
        "*   **Space Group**: 空间群。用于描述晶体内部三维平移和旋转等所有微观对称操作的数学群（自然界共 230 种），通常用国际表符号（如 $Fm\\bar{3}m$）表示。",
        "*   **Crystal System**: 晶系。根据晶胞的几何特征（边长与夹角的关系）分为七大类（如 cubic 立方、tetragonal 四方、orthorhombic 正交、trigonal 三方等）。",
        "*   **Lattice System**: 布拉菲格子系统。在七大晶系基础上进一步考虑了面心、体心等平移对称性特征。",
        "*   **Point Group**: 点群。反映晶体宏观外形的对称性操作集合。",
        "",
        "### 4. 深度计算数据可用性 (Properties Status)",
        "*   **electronic_structure**: 电子结构数据（包含精确的能带和态密度信息）。`COMPLETE` 表示该数据已成功获取并保存在本地。",
        "*   **phonon**: 声子谱数据（用于分析材料的热容、热导率及动力学稳定性）。`NOT_AVAILABLE` 表示该材料目前在数据库中尚无相关的声子计算记录。",
        "*   **phase_stability**: 相稳定性分析的高级数据。`NOT_AVAILABLE` 表示缺乏此项独立记录。",
        "*   **spectra**: 光学或 X 射线吸收/发射光谱数据。",
        "*   `SKIPPED_RANK_LIMIT`: 由于该材料在筛选列表中的排名靠后，为节省计算与带宽资源，系统主动跳过了这些昂贵（Heavy）数据的拉取。",
        "",
        "### 5. 抓取警告 (Warnings)",
        "在请求具体的计算文件时可能发生的错误：",
        "*   **bandstructure: FETCH_FAILED (OSError)**: 能带结构文件抓取失败。通常是因为网络不稳定或远端文件过大导致连接断开。",
        "*   **charge_density: FETCH_FAILED (OSError)**: 电荷密度（CHGCAR）文件抓取失败。这是一个体积通常高达几十 MB 到几 GB 的重型文件，极易因网络原因中断。",
        "*   **dos: FETCH_FAILED (KeyError)**: 态密度 (Density of States) 数据抓取失败。通常是因为 MP 数据库虽然标记了存在该数据，但实际返回的 JSON 结构中缺失了关键字段，导致解析失败。",
        "",
        "---",
        "",
        "## 筛选漏斗",
        "",
        "| 阶段 | 数量 |",
        "|---|---:|",
        f"| 数据库返回 | {funnel['database_returned']} |",
        f"| 成功规范化 | {funnel['normalized']} |",
        f"| FAILED | {funnel['failed']} |",
        f"| REJECT | {funnel['rejected']} |",
        f"| UNCERTAIN | {funnel['uncertain']} |",
        f"| PASS | {funnel['passed']} |",
        f"| 下游发布 | {funnel['published_downstream']} |",
        "",
        "## 查询",
        "",
        "### 本地复核约束",
        "",
    ]
    local_constraints = query.get("local_only_constraints", [])
    if local_constraints:
        lines.extend(f"- `{constraint}`" for constraint in local_constraints)
    else:
        lines.append("- 无（全部约束已下推到数据库查询）")
    if source is SourceDatabase.C2DB and query["database_version"].endswith("undated-live-web"):
        lines.extend(
            [
                "",
                "> C2DB 使用实时 Web 数据；当前接口未提供可固定的数据快照版本。",
                "> 相同查询未来可能因数据库更新而返回不同记录。",
            ]
        )
    lines.extend(
        [
            "",
            "### 远程查询条件",
            "",
            "```json",
            _pretty_json(query["pushdown_filters"]),
            "```",
            "",
            "## 发布候选",
            "",
            f"| Rank | {source_id_label} | Formula | Decision | Missing evidence |",
            "|---:|---|---|---|---|",
        ]
    )
    for candidate in report["published_candidates"]:
        missing = ", ".join(candidate["missing_evidence"]) or "—"
        lines.append(
            f"| {candidate['rank']} | {candidate['material_id']} | "
            f"{candidate['formula']} | {candidate['decision']} | {missing} |"
        )
    if not report["published_candidates"]:
        lines.append("| — | — | — | — | 无候选 |")
    enrichments = report.get("materials_project_enrichment", [])
    if enrichments:
        lines.extend(["", "## Materials Project 候选详情", ""])
        for item in enrichments:
            lines.extend([f"### {item['material_id']}", ""])
            lines.extend(["#### Summary", "", "| 属性 | 数值 | 单位 |", "|---|---|---|"])
            for field in item.get("sections", {}).get("summary", {}).get("fields", []):
                lines.append(f"| {field['label']} | {field['value']} | {field.get('unit') or '—'} |")
            experimental = item.get("sections", {}).get("experimental", {})
            lines.extend(["", "#### Experimentally Observed", "", experimental.get("text", "NOT_AVAILABLE"), ""])
            crystal = item.get("sections", {}).get("crystal_structure", {})
            lines.extend(["#### Crystal Structure", "", f"状态：`{crystal.get('status', 'NOT_AVAILABLE')}`"])
            for asset in item.get("assets", []):
                if asset.get("media_type") == "image/png":
                    import os
                    relative = asset["uri"].removeprefix("artifact://")
                    image_path = os.path.join(workspace_dir, relative) if workspace_dir else relative
                    label = relative.rsplit("/", 1)[-1].removesuffix(".png").replace("_", " ")
                    lines.extend(["", f"![{label}]({image_path})"])
            symmetry = crystal.get("symmetry")
            if symmetry:
                lines.extend(["", "| Symmetry | Value |", "|---|---|"])
                lines.extend(f"| {key.replace('_', ' ')} | {value} |" for key, value in symmetry.items())
            lines.extend(["", "#### Properties", ""])
            for name in ("phase_stability", "electronic_structure", "phonon", "spectra", "heterostructures"):
                section = item.get("sections", {}).get(name, {})
                lines.append(f"- {name}: `{section.get('status', 'NOT_AVAILABLE')}`")
            if item.get("raw_source"):
                lines.append(f"- Compressed raw source: `{item['raw_source']['uri']}`")
            if item.get("warnings"):
                lines.extend(["", "Warnings:"])
                lines.extend(f"- {warning}" for warning in item["warnings"])
            lines.append("")
    lines.extend(
        [
            "",
            "## 证据声明",
            "",
            report["evidence_statement"],
            "",
        ]
    )
    return "\n".join(lines)


def _pretty_json(value: Any) -> str:
    import json

    return json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True)
