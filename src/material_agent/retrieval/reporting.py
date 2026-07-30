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


def report_to_markdown(report: dict[str, Any]) -> str:
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
        f"- Stage 状态：`{report['status']}`",
        f"- 数据库版本：`{query['database_version']}`",
        f"- 检索时间：`{query['retrieved_at']}`",
        f"- Query ID：`{query['query_id']}`",
        f"- Query fingerprint：`{query['query_fingerprint']}`",
        f"- GNoME：`{query['include_gnome']}`",
        f"- 扫描是否截断：`{limits['scan_truncated']}`",
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
                    relative = asset["uri"].removeprefix("artifact://")
                    label = relative.rsplit("/", 1)[-1].removesuffix(".png").replace("_", " ")
                    lines.extend(["", f"![{label}]({relative})"])
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
    if report["warnings"]:
        lines.extend(["## 警告", ""])
        lines.extend(f"- {warning}" for warning in report["warnings"])
        lines.append("")
    return "\n".join(lines)


def _pretty_json(value: Any) -> str:
    import json

    return json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True)
