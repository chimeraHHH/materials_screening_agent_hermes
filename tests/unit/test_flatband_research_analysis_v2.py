from __future__ import annotations

import hashlib
from typing import Any, TypeVar

import pytest
from pydantic import ValidationError

from material_agent.inspiration.models import (
    StrictModel,
    canonical_sha256,
    deterministic_id,
)
from material_agent.research.flatband_analysis_v2 import (
    AnalysisArtifactRefV2,
    AnalysisArtifactTypeV2,
    AnalysisCaseBindingV2,
    AnalysisInputReleaseV2,
    AnalysisTraceRoleV2,
    FinalJudgmentStatusV2,
    assert_analysis_input_exact_replay_v2,
    build_analysis_cell_evidence_v2,
    build_analysis_input_release_v2,
    build_analysis_trace_evidence_v2,
    build_final_position_judgment_v2,
    build_formal_verifier_attestation_v1,
    derive_development_metric_rows_v2,
    derive_locked_result_family_v2,
)
from material_agent.research.flatband_arm_runtime import (
    ArmExecutionScope,
    ArmExecutionTraceV1,
    build_arm_execution_trace,
)
from material_agent.research.flatband_contracts import (
    AssertedEvidenceRelation,
    BenchmarkSplit,
    EvidenceSpanRefV1,
    FalsificationPlanV1,
    HypothesisPacketV1,
    MechanismFamily,
)
from material_agent.research.flatband_execution import (
    ExecutionPhase,
    MissingPositionReason,
    ProjectedTop5PositionV1,
    RankingPositionV1,
    ResearchRankingV1,
    ResearchSystemId,
    RunCellStatus,
    SourceBudgetV1,
    SourceReceiptBundleV1,
    SourceVariant,
    SystemConfigV1,
    TerminalRunResultV1,
    Top5ProjectionV1,
    replay_source_usage,
    source_policy_values,
)
from material_agent.research.flatband_lifecycle import (
    VerifiedPayloadKind,
)

ModelT = TypeVar("ModelT", bound=StrictModel)
SHA_A = "a" * 64
SHA_B = "b" * 64
SHA_C = "c" * 64
SHA_D = "d" * 64
BASELINE_TAGS = canonical_sha256("analysis-v2-baseline-tags")
CROSS_TAGS = canonical_sha256("analysis-v2-cross-tags")
GOLD_REF = AnalysisArtifactRefV2(
    artifact_type=AnalysisArtifactTypeV2.GOLD_RELEASE,
    artifact_id="synthetic-final-gold",
    artifact_sha256=canonical_sha256("synthetic-final-gold"),
)
GOLD_VERIFIER_REF = AnalysisArtifactRefV2(
    artifact_type=AnalysisArtifactTypeV2.GOLD_FORMAL_VERIFIER,
    artifact_id="synthetic-gold-verifier",
    artifact_sha256=canonical_sha256("synthetic-gold-verifier"),
)
SPLIT_REF = AnalysisArtifactRefV2(
    artifact_type=AnalysisArtifactTypeV2.SPLIT_CONTEXT,
    artifact_id="synthetic-main-split",
    artifact_sha256=canonical_sha256("synthetic-main-split"),
)
LEAKAGE_REF = AnalysisArtifactRefV2(
    artifact_type=AnalysisArtifactTypeV2.LEAKAGE_CONTEXT,
    artifact_id="synthetic-leakage",
    artifact_sha256=canonical_sha256("synthetic-leakage"),
)


def _identified(
    model_type: type[ModelT],
    *,
    id_field: str,
    sha_field: str,
    prefix: str,
    values: dict[str, Any],
) -> ModelT:
    draft = model_type.model_construct(**values)
    digest = canonical_sha256(
        draft.model_dump(mode="python", exclude={id_field, sha_field})
    )
    return model_type.model_validate(
        {
            **values,
            id_field: deterministic_id(prefix, {sha_field: digest}),
            sha_field: digest,
        }
    )


def _budgets(variant: SourceVariant) -> tuple[SourceBudgetV1, ...]:
    allocations = {
        SourceVariant.CROSSREF_ONLY: {"crossref": 8},
        SourceVariant.E2_A: {"arxiv": 2, "crossref": 3, "openalex": 3},
    }[variant]
    information = {
        SourceVariant.CROSSREF_ONLY: {"crossref": (4, 4, 100, 100_000, 50, 2)},
        SourceVariant.E2_A: {
            "arxiv": (1, 1, 25, 25_000, 12, 0),
            "crossref": (1, 1, 37, 37_500, 19, 1),
            "openalex": (2, 2, 38, 37_500, 19, 1),
        },
    }[variant]
    return tuple(
        SourceBudgetV1(
            source_id=source_id,
            **source_policy_values(source_id),
            max_physical_requests=count,
            max_logical_queries=information[source_id][0],
            max_pages=information[source_id][1],
            max_records=information[source_id][2],
            max_response_bytes=information[source_id][3],
            max_unique_documents=information[source_id][4],
            max_cache_hits=information[source_id][5],
        )
        for source_id, count in sorted(allocations.items())
    )


def _config(
    system_id: ResearchSystemId,
    *,
    fusion_components: tuple[ResearchSystemId, ...] = (),
) -> SystemConfigV1:
    if system_id is ResearchSystemId.E2_A or ResearchSystemId.E2_A in fusion_components:
        variant = SourceVariant.E2_A
    else:
        variant = SourceVariant.CROSSREF_ONLY
    values: dict[str, object] = {
        "system_id": system_id,
        "source_variant": variant,
        "source_budgets": _budgets(variant),
        "query_plan_sha256": SHA_A,
        "record_projection_sha256": SHA_B,
        "ranking_policy_sha256": SHA_C,
        "cache_policy_sha256": SHA_D,
        "cache_snapshot_sha256": canonical_sha256("analysis-v2-cache"),
        "baseline_tag_graph_sha256": BASELINE_TAGS,
        "cross_domain_tag_graph_sha256": (
            CROSS_TAGS
            if system_id is ResearchSystemId.E3
            or ResearchSystemId.E3 in fusion_components
            else None
        ),
        "llm": None,
        "local_semantic_model": None,
        "fusion_components": fusion_components,
    }
    return _identified(
        SystemConfigV1,
        id_field="config_id",
        sha_field="config_sha256",
        prefix="system-config",
        values=values,
    )


def _packet(case_id: str, case_sha: str, label: str) -> HypothesisPacketV1:
    span = f"Bounded synthetic metadata for {case_id} {label}."
    return _identified(
        HypothesisPacketV1,
        id_field="packet_id",
        sha_field="packet_sha256",
        prefix="hypothesis-packet",
        values={
            "case_id": case_id,
            "case_sha256": case_sha,
            "candidate_structure_sha256": canonical_sha256((case_id, label, "structure")),
            "strict_structure_group_id": f"structure-{case_id}-{label}",
            "strict_hypothesis_group_id": f"hypothesis-{case_id}-{label}",
            "transformation_operator_id": "SUBSTITUTE_EQUIVALENT_SITE_V1",
            "transformation_summary": "A bounded connectivity-preserving substitution.",
            "source_domain": "synthetic contract fixture",
            "mechanism_family": MechanismFamily.LATTICE_INTERFERENCE,
            "source_mechanism": "Destructive interference localizes a mode.",
            "shared_invariant": "Connectivity-preserving destructive interference.",
            "target_mapping": "Map connectivity to the target orbital graph.",
            "transferable_control": "Tune the hopping hierarchy.",
            "transfer_principle": "Preserve connectivity while changing orbital weight.",
            "required_conditions": ("dominant local hopping",),
            "breaking_conditions": ("large symmetry-breaking hopping",),
            "evidence_links": (
                EvidenceSpanRefV1(
                    evidence_link_id=f"evidence-{case_id}-{label}",
                    source_id="crossref",
                    source_record_id=f"record-{case_id}-{label}",
                    source_url="https://doi.org/10.1000/example",
                    span_id=f"span-{case_id}-{label}",
                    span_sha256=hashlib.sha256(span.encode()).hexdigest(),
                    asserted_relation=AssertedEvidenceRelation.SUPPORT,
                    claim_summary="Synthetic metadata supports the contract fixture.",
                ),
            ),
            "falsification": FalsificationPlanV1(
                observable="Tracked-band width",
                method="Compute the preregistered target-band observable.",
                pass_condition="The tracked band meets the frozen threshold.",
                fail_condition="The tracked band exceeds the frozen threshold.",
            ),
        },
    )


def _trace(
    *,
    case_id: str,
    case_sha: str,
    config: SystemConfigV1,
    scope: ArmExecutionScope,
    label: str,
    components: tuple[ArmExecutionTraceV1, ...] = (),
) -> ArmExecutionTraceV1:
    packet = _packet(case_id, case_sha, label)
    ranking = _identified(
        ResearchRankingV1,
        id_field="ranking_id",
        sha_field="ranking_sha256",
        prefix="research-ranking",
        values={
            "budget_manifest_id": f"budget-{case_id}-{label}",
            "budget_manifest_sha256": canonical_sha256((case_id, label, "budget")),
            "cell_id": f"cell-{case_id}-{label}",
            "run_id": f"run-{case_id}-{label}",
            "case_id": case_id,
            "case_sha256": case_sha,
            "system_config_id": config.config_id,
            "system_config_sha256": config.config_sha256,
            "positions": (
                RankingPositionV1(
                    selection_rank=1,
                    packet_id=packet.packet_id,
                    packet_sha256=packet.packet_sha256,
                ),
            ),
            "underfill_reason_codes": ("SYNTHETIC_UNDERFILL",),
            "created_at": "2026-08-10T12:03:00+08:00",
        },
    )
    return build_arm_execution_trace(
        scope=scope,
        system_config=config,
        ranking=ranking,
        hypothesis_packets=(packet,),
        fusion_component_traces=components,
        assembled_at="2026-08-10T12:04:00+08:00",
    )


def _cell(
    *,
    role: AnalysisTraceRoleV2,
    trace: ArmExecutionTraceV1,
    grade: int,
    components: tuple[ArmExecutionTraceV1, ...] = (),
):
    bundles = tuple(
        _identified(
            SourceReceiptBundleV1,
            id_field="receipt_bundle_id",
            sha_field="receipt_bundle_sha256",
            prefix="source-receipt-bundle",
            values={
                "source_id": budget.source_id,
                "retrieval_identity_sha256": canonical_sha256(
                    (trace.ranking.cell_id, budget.source_id, "retrieval")
                ),
                "cache_snapshot_sha256": trace.system_config.cache_snapshot_sha256,
                "budget_manifest_id": trace.ranking.budget_manifest_id,
                "budget_manifest_sha256": trace.ranking.budget_manifest_sha256,
                "cell_id": trace.ranking.cell_id,
                "run_id": trace.ranking.run_id,
                "system_config_id": trace.system_config.config_id,
                "system_config_sha256": trace.system_config.config_sha256,
            },
        )
        for budget in trace.system_config.source_budgets
    )
    terminal = _identified(
        TerminalRunResultV1,
        id_field="terminal_result_id",
        sha_field="terminal_result_sha256",
        prefix="terminal-result",
        values={
            "budget_manifest_id": trace.ranking.budget_manifest_id,
            "budget_manifest_sha256": trace.ranking.budget_manifest_sha256,
            "ranking_id": trace.ranking.ranking_id,
            "ranking_sha256": trace.ranking.ranking_sha256,
            "cell_id": trace.ranking.cell_id,
            "run_id": trace.ranking.run_id,
            "case_id": trace.ranking.case_id,
            "case_sha256": trace.ranking.case_sha256,
            "system_config_id": trace.system_config.config_id,
            "system_config_sha256": trace.system_config.config_sha256,
            "git_commit": "1" * 40,
            "runtime_environment_sha256": canonical_sha256("analysis-v2-runtime"),
            "status": RunCellStatus.PARTIAL,
            "source_receipt_bundles": bundles,
            "source_usage": tuple(replay_source_usage(item) for item in bundles),
            "walltime_ms": 1_000,
            "failure_reason_codes": ("SYNTHETIC_UNDERFILL",),
            "completed_at": "2026-08-10T12:05:00+08:00",
        },
    )
    projected = tuple(
        ProjectedTop5PositionV1(
            position=position,
            packet_id=(
                trace.ranking.positions[position - 1].packet_id
                if position <= len(trace.ranking.positions)
                else None
            ),
            packet_sha256=(
                trace.ranking.positions[position - 1].packet_sha256
                if position <= len(trace.ranking.positions)
                else None
            ),
            forced_zero=position > len(trace.ranking.positions),
            fixed_gain=0 if position > len(trace.ranking.positions) else None,
            missing_reason=(
                MissingPositionReason.UNDERFILL
                if position > len(trace.ranking.positions)
                else None
            ),
        )
        for position in range(1, 6)
    )
    top5 = _identified(
        Top5ProjectionV1,
        id_field="projection_id",
        sha_field="projection_sha256",
        prefix="top5-projection",
        values={
            "cell_id": trace.ranking.cell_id,
            "terminal_result_id": terminal.terminal_result_id,
            "terminal_result_sha256": terminal.terminal_result_sha256,
            "ranking_id": trace.ranking.ranking_id,
            "ranking_sha256": trace.ranking.ranking_sha256,
            "status": RunCellStatus.PARTIAL,
            "positions": projected,
        },
    )
    evidence = build_analysis_trace_evidence_v2(
        role=role,
        system_config=trace.system_config,
        terminal_result=terminal,
        trace=trace,
        top5_projection=top5,
        fusion_component_traces=components,
    )
    ranked = trace.ranking.positions[0]
    judgment = build_final_position_judgment_v2(
        position=1,
        packet_id=ranked.packet_id,
        packet_sha256=ranked.packet_sha256,
        status=FinalJudgmentStatusV2.ASSESSABLE,
        relevance_grade=grade,
        evidence_valid=grade >= 2,
        final_duplicate_cluster_id=f"cluster-{trace.ranking.case_id}-{role.value}",
    )
    return build_analysis_cell_evidence_v2(
        trace_evidence=evidence,
        gold_release_ref=GOLD_REF,
        gold_formal_verifier_ref=GOLD_VERIFIER_REF,
        judgments=(judgment,),
    )


@pytest.fixture(scope="module")
def development_release() -> AnalysisInputReleaseV2:
    b0_config = _config(ResearchSystemId.B0)
    e2_config = _config(ResearchSystemId.E2_A)
    fusion_config = _config(
        ResearchSystemId.FUSION,
        fusion_components=(ResearchSystemId.E2_A,),
    )
    cases = []
    cells = []
    for index in range(60):
        case_id = f"dev-case-{index:02d}"
        case_sha = canonical_sha256(case_id)
        cases.append(
            AnalysisCaseBindingV2(
                case_id=case_id,
                case_sha256=case_sha,
                split=BenchmarkSplit.DEVELOPMENT,
                leakage_component_id=f"dev-component-{index:02d}",
            )
        )
        b0 = _trace(
            case_id=case_id,
            case_sha=case_sha,
            config=b0_config,
            scope=ArmExecutionScope.DEVELOPMENT,
            label="b0",
        )
        component = _trace(
            case_id=case_id,
            case_sha=case_sha,
            config=e2_config,
            scope=ArmExecutionScope.DEVELOPMENT,
            label="e2a",
        )
        fusion = _trace(
            case_id=case_id,
            case_sha=case_sha,
            config=fusion_config,
            scope=ArmExecutionScope.DEVELOPMENT,
            label="fusion",
            components=(component,),
        )
        cells.extend(
            (
                _cell(role=AnalysisTraceRoleV2.B0, trace=b0, grade=0),
                _cell(
                    role=AnalysisTraceRoleV2.FUSION,
                    trace=fusion,
                    grade=3,
                    components=(component,),
                ),
            )
        )
    return build_analysis_input_release_v2(
        execution_phase=ExecutionPhase.DEVELOPMENT_FUSION,
        split_context_ref=SPLIT_REF,
        leakage_context_ref=LEAKAGE_REF,
        cases=cases,
        cells=cells,
        assembled_at="2026-08-10T13:00:00+08:00",
    )


def test_development_metrics_and_attestation_are_derived_not_supplied(
    development_release: AnalysisInputReleaseV2,
) -> None:
    assert_analysis_input_exact_replay_v2(development_release)
    rows = derive_development_metric_rows_v2(development_release)
    assert tuple(item.system_id for item in rows) == (
        ResearchSystemId.B0,
        ResearchSystemId.FUSION,
    )
    assert rows[0].andcg_at_5 == 0.0
    assert rows[1].andcg_at_5 > rows[0].andcg_at_5
    assert rows[0].schema_only_row
    assert not rows[0].row_alone_is_formal_evidence
    attestation = build_formal_verifier_attestation_v1(
        development_release,
        verified_payload_kind=VerifiedPayloadKind.DEVELOPMENT_FUSION_METRIC_ROWS,
        attested_at="2026-08-10T13:01:00+08:00",
    )
    assert attestation.verified_payload_sha256 == canonical_sha256(rows)
    assert attestation.upstream_exact_replay_required


def test_fixed_five_denominator_and_score_tamper_fail_closed(
    development_release: AnalysisInputReleaseV2,
) -> None:
    first = development_release.cells[0]
    assert len(first.judgments) == 5
    assert first.judgments[1].packet_id is None
    assert first.judgments[1].relevance_grade == 0
    attacked = development_release.metric_rows[0].model_copy(
        update={"andcg_at_5": 1.0}
    )
    with pytest.raises(ValidationError, match="semantic content"):
        type(attacked).model_validate(
            attacked.model_dump(mode="python", round_trip=True)
        )

    trace_evidence = first.trace_evidence
    projection = trace_evidence.top5_projection
    foreign_position = projection.positions[0].model_copy(
        update={
            "packet_id": "foreign-packet",
            "packet_sha256": canonical_sha256("foreign-packet"),
        }
    )
    foreign_projection = _identified(
        Top5ProjectionV1,
        id_field="projection_id",
        sha_field="projection_sha256",
        prefix="top5-projection",
        values={
            **projection.model_dump(
                mode="python", exclude={"projection_id", "projection_sha256"}
            ),
            "positions": (foreign_position, *projection.positions[1:]),
        },
    )
    with pytest.raises(ValidationError, match="differs from trace ranking"):
        build_analysis_trace_evidence_v2(
            role=trace_evidence.role,
            system_config=trace_evidence.system_config,
            terminal_result=trace_evidence.terminal_result,
            trace=trace_evidence.trace,
            top5_projection=foreign_projection,
            fusion_component_traces=trace_evidence.fusion_component_traces,
        )


def test_failed_terminal_without_ranking_becomes_five_run_failed_zeroes() -> None:
    config = _config(ResearchSystemId.B0)
    case_id = "failed-case"
    case_sha = canonical_sha256(case_id)
    budget_id = "failed-budget"
    budget_sha = canonical_sha256(budget_id)
    cell_id = "failed-cell"
    run_id = "failed-run"
    bundles = tuple(
        _identified(
            SourceReceiptBundleV1,
            id_field="receipt_bundle_id",
            sha_field="receipt_bundle_sha256",
            prefix="source-receipt-bundle",
            values={
                "source_id": budget.source_id,
                "retrieval_identity_sha256": canonical_sha256(
                    (cell_id, budget.source_id, "retrieval")
                ),
                "cache_snapshot_sha256": config.cache_snapshot_sha256,
                "budget_manifest_id": budget_id,
                "budget_manifest_sha256": budget_sha,
                "cell_id": cell_id,
                "run_id": run_id,
                "system_config_id": config.config_id,
                "system_config_sha256": config.config_sha256,
            },
        )
        for budget in config.source_budgets
    )
    terminal = _identified(
        TerminalRunResultV1,
        id_field="terminal_result_id",
        sha_field="terminal_result_sha256",
        prefix="terminal-result",
        values={
            "budget_manifest_id": budget_id,
            "budget_manifest_sha256": budget_sha,
            "ranking_id": None,
            "ranking_sha256": None,
            "cell_id": cell_id,
            "run_id": run_id,
            "case_id": case_id,
            "case_sha256": case_sha,
            "system_config_id": config.config_id,
            "system_config_sha256": config.config_sha256,
            "git_commit": "1" * 40,
            "runtime_environment_sha256": canonical_sha256("analysis-v2-runtime"),
            "status": RunCellStatus.FAILED,
            "source_receipt_bundles": bundles,
            "source_usage": tuple(replay_source_usage(item) for item in bundles),
            "walltime_ms": 1_000,
            "failure_reason_codes": ("SYNTHETIC_FAILURE",),
            "completed_at": "2026-08-10T12:05:00+08:00",
        },
    )
    positions = tuple(
        ProjectedTop5PositionV1(
            position=position,
            forced_zero=True,
            fixed_gain=0,
            missing_reason=MissingPositionReason.RUN_FAILED,
        )
        for position in range(1, 6)
    )
    top5 = _identified(
        Top5ProjectionV1,
        id_field="projection_id",
        sha_field="projection_sha256",
        prefix="top5-projection",
        values={
            "cell_id": cell_id,
            "terminal_result_id": terminal.terminal_result_id,
            "terminal_result_sha256": terminal.terminal_result_sha256,
            "ranking_id": None,
            "ranking_sha256": None,
            "status": RunCellStatus.FAILED,
            "positions": positions,
        },
    )
    trace_evidence = build_analysis_trace_evidence_v2(
        role=AnalysisTraceRoleV2.B0,
        system_config=config,
        terminal_result=terminal,
        top5_projection=top5,
        trace=None,
    )
    cell = build_analysis_cell_evidence_v2(
        trace_evidence=trace_evidence,
        gold_release_ref=GOLD_REF,
        gold_formal_verifier_ref=GOLD_VERIFIER_REF,
        judgments=(),
    )
    assert len(cell.judgments) == 5
    assert all(
        item.missing_reason is MissingPositionReason.RUN_FAILED
        and item.relevance_grade == 0
        and not item.evidence_valid
        for item in cell.judgments
    )



@pytest.fixture(scope="module")
def locked_release() -> AnalysisInputReleaseV2:
    b0_config = _config(ResearchSystemId.B0)
    e2_config = _config(ResearchSystemId.E2_A)
    e3_config = _config(ResearchSystemId.E3)
    full_config = _config(
        ResearchSystemId.FUSION,
        fusion_components=(ResearchSystemId.E2_A, ResearchSystemId.E3),
    )
    minus_e2_config = _config(
        ResearchSystemId.FUSION,
        fusion_components=(ResearchSystemId.E3,),
    )
    minus_e3_config = _config(
        ResearchSystemId.FUSION,
        fusion_components=(ResearchSystemId.E2_A,),
    )
    cases = []
    cells = []
    for index in range(60):
        case_id = f"locked-case-{index:02d}"
        case_sha = canonical_sha256(case_id)
        split = BenchmarkSplit.LOCKED_IID if index < 30 else BenchmarkSplit.LOCKED_OOD
        cases.append(
            AnalysisCaseBindingV2(
                case_id=case_id,
                case_sha256=case_sha,
                split=split,
                leakage_component_id=f"locked-component-{index:02d}",
            )
        )
        b0 = _trace(
            case_id=case_id,
            case_sha=case_sha,
            config=b0_config,
            scope=ArmExecutionScope.LOCKED,
            label="b0",
        )
        e2 = _trace(
            case_id=case_id,
            case_sha=case_sha,
            config=e2_config,
            scope=ArmExecutionScope.LOCKED,
            label="component-e2",
        )
        e3 = _trace(
            case_id=case_id,
            case_sha=case_sha,
            config=e3_config,
            scope=ArmExecutionScope.LOCKED,
            label="component-e3",
        )
        full = _trace(
            case_id=case_id,
            case_sha=case_sha,
            config=full_config,
            scope=ArmExecutionScope.LOCKED,
            label="full",
            components=(e2, e3),
        )
        minus_e2 = _trace(
            case_id=case_id,
            case_sha=case_sha,
            config=minus_e2_config,
            scope=ArmExecutionScope.LOCKED,
            label="minus-e2",
            components=(e3,),
        )
        minus_e3 = _trace(
            case_id=case_id,
            case_sha=case_sha,
            config=minus_e3_config,
            scope=ArmExecutionScope.LOCKED,
            label="minus-e3",
            components=(e2,),
        )
        cells.extend(
            (
                _cell(role=AnalysisTraceRoleV2.B0, trace=b0, grade=0),
                _cell(
                    role=AnalysisTraceRoleV2.FUSION,
                    trace=full,
                    grade=3,
                    components=(e2, e3),
                ),
                _cell(
                    role=AnalysisTraceRoleV2.FUSION_MINUS_E2,
                    trace=minus_e2,
                    grade=2,
                    components=(e3,),
                ),
                _cell(
                    role=AnalysisTraceRoleV2.FUSION_MINUS_E3,
                    trace=minus_e3,
                    grade=2,
                    components=(e2,),
                ),
            )
        )
    return build_analysis_input_release_v2(
        execution_phase=ExecutionPhase.LOCKED_PRIMARY,
        split_context_ref=SPLIT_REF,
        leakage_context_ref=LEAKAGE_REF,
        cases=cases,
        cells=cells,
        assembled_at="2026-08-10T14:00:00+08:00",
    )


def test_locked_fixed_family_is_derived_from_full_and_minus_traces(
    locked_release: AnalysisInputReleaseV2,
) -> None:
    primary, secondary = derive_locked_result_family_v2(locked_release)
    assert primary.equal_iid_ood_andcg_delta > 0.0
    assert primary.bootstrap_lower == primary.bootstrap_upper
    assert len(secondary) == 5
    assert secondary[2].applicability.value == "NOT_APPLICABLE"
    assert secondary[2].raw_one_sided_p == 1.0
    assert secondary[3].andcg_delta is not None
    attestation = build_formal_verifier_attestation_v1(
        locked_release,
        verified_payload_kind=VerifiedPayloadKind.LOCKED_RESULT_FAMILY,
        attested_at="2026-08-10T14:01:00+08:00",
    )
    assert attestation.verified_payload_sha256 == canonical_sha256(
        (primary, secondary)
    )


def test_locked_minus_role_cannot_relabel_full_fusion_trace(
    locked_release: AnalysisInputReleaseV2,
) -> None:
    full_cell = next(
        item
        for item in locked_release.cells
        if item.role is AnalysisTraceRoleV2.FUSION
    )
    attacked_trace = full_cell.trace_evidence.model_copy(
        update={"role": AnalysisTraceRoleV2.FUSION_MINUS_E2}
    )
    with pytest.raises(ValidationError, match="semantic content"):
        type(attacked_trace).model_validate(
            attacked_trace.model_dump(mode="python", round_trip=True)
        )
