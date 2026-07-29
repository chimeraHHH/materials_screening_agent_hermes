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
                    if candidate.decision in {Decision.PASS, Decision.UNCERTAIN}
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
    }


def report_to_markdown(report: dict[str, Any]) -> str:
    funnel = report["funnel"]
    limits = report["limits"]
    query = report["query"]
    source = SourceDatabase(query["source_database"])
    source_label = (
        "Materials Project"
        if source is SourceDatabase.MATERIALS_PROJECT
        else "NOMAD"
    )
    source_id_label = "MP ID" if source is SourceDatabase.MATERIALS_PROJECT else "NOMAD entry ID"
    lines = [
        f"# Agent 01 {source_label} 检索报告",
        "",
        f"- Stage 状态：`{report['status']}`",
        f"- 数据库版本：`{query['database_version']}`",
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
        "```json",
        _pretty_json(query["pushdown_filters"]),
        "```",
        "",
        "## 发布候选",
        "",
        f"| Rank | {source_id_label} | Formula | Decision | Missing evidence |",
        "|---:|---|---|---|---|",
    ]
    for candidate in report["published_candidates"]:
        missing = ", ".join(candidate["missing_evidence"]) or "—"
        lines.append(
            f"| {candidate['rank']} | {candidate['material_id']} | "
            f"{candidate['formula']} | {candidate['decision']} | {missing} |"
        )
    if not report["published_candidates"]:
        lines.append("| — | — | — | — | 无候选 |")
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
