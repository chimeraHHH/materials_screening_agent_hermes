from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from material_agent.inspiration.research_graph import (
    ConstraintKind,
    required_constraint_kinds_for_goal,
)
from material_agent.integration.generic_research import (
    GenericMaterialsResearchService,
    GenericResearchRunRequestV1,
)

pytestmark = pytest.mark.live_llm


ORIGINAL_CHINESE_GOAL = """搜索数据库中的过渡金属二维平带材料。要求：
1.必须是层状材料，有vdW gap的材料最优先。
2.平带必须是费米面附近的第一条能带，贡献这条能带的电子轨道必须由过渡金属元素或过渡金属与配体的杂化态构成。
3.平带和其他色散较大的能带不能有一阶交点，色散能带不能穿过费米面，但它们可以在高对称点有公共极值点。
4.平带的定义是：带宽W<=50meV的能带。
5.进行价态分析，过渡金属必须处于常见价态或常见价态的混合。
6.平带不能是由孤立的原子或cluster形成的，必须是互连的子晶格贡献的。"""


def test_live_original_goal_runs_full_generic_research_graph() -> None:
    workspace = Path(tempfile.gettempdir()) / "material-agent-live-generic-r2"
    result = GenericMaterialsResearchService(
        workspace=workspace,
        project_id="live-generic-research",
    ).run(
        GenericResearchRunRequestV1(
            submission_id="live-original-flatband-goal",
            goal=ORIGINAL_CHINESE_GOAL,
            reasoning_effort="high",
            max_native_search_calls=2,
            max_authoritative_search_calls=4,
            max_agent_rounds_per_role=10,
        )
    )

    graph = result.research_graph
    expected = {
        ConstraintKind.DIMENSIONALITY,
        ConstraintKind.ELECTRONIC_BANDWIDTH,
        ConstraintKind.FERMI_ORDERING,
        ConstraintKind.BAND_ISOLATION,
        ConstraintKind.ORBITAL_CHARACTER,
        ConstraintKind.OXIDATION_STATE,
        ConstraintKind.SUBLATTICE_CONNECTIVITY,
        ConstraintKind.COMPOSITION,
    }
    assert required_constraint_kinds_for_goal(ORIGINAL_CHINESE_GOAL) == expected
    assert expected <= {constraint.kind for constraint in graph.constraints.constraints}
    assert len(graph.roles) == 9
    assert all(role.receipt.thinking_mode == "enabled" for role in graph.roles)
    assert all(
        role.receipt.reasoning_content_persisted is False for role in graph.roles
    )
    assert all(role.receipt.tool_calls for role in graph.roles)
    assert graph.synthesis.scientific_conclusion_status == "REASONED_HYPOTHESIS"
    assert graph.synthesis.scientific_conclusion.strip()
    assert result.scientific_conclusion_status == "REASONED_HYPOTHESIS"
    assert result.scientific_conclusion == graph.synthesis.scientific_conclusion
    assert result.property_verification_complete is False
    assert graph.database_federation.enabled_sources == (
        "c2db",
        "mc3d",
        "nomad",
        "materials_project",
    )
    assert graph.database_federation.receipts
    receipts_by_query: dict[int, set[str]] = {}
    for receipt in graph.database_federation.receipts:
        receipts_by_query.setdefault(receipt.query_ordinal, set()).add(
            receipt.source_database
        )
    assert all(
        sources == {"c2db", "mc3d", "nomad", "materials_project"}
        for sources in receipts_by_query.values()
    )
    constraint_count = len(graph.constraints.constraints)
    assert len(graph.inference_review.matrix) == len(graph.candidates.candidates)
    assert all(
        len(row.assessments) == constraint_count
        for row in graph.inference_review.matrix
    )
    assert all(
        assessment.predicted_verdict in {"LIKELY_PASS", "LIKELY_FAIL"}
        for row in graph.inference_review.matrix
        for assessment in row.assessments
    )
    assert result.result_artifact_uri.startswith("artifact://")
    assert '"reasoning_content":' not in result.model_dump_json()
