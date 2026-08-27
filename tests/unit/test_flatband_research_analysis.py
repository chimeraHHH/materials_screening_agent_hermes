from __future__ import annotations

import hashlib
import sys
from functools import lru_cache
from typing import Any, TypeVar

import pytest
from pydantic import ValidationError

from material_agent.inspiration.models import (
    StrictModel,
    canonical_sha256,
    deterministic_id,
)
from material_agent.research.flatband_analysis import (
    PILOT_ALPHA_BOOTSTRAP_REPLICATES,
    PILOT_ALPHA_BOOTSTRAP_SEED,
    AnalysisCaseBindingV1,
    AnalysisInputReleaseV1,
    AnalysisPositionV1,
    AnalysisReleaseKind,
    CaseSystemAnalysisRowV1,
    CaseSystemMetricProjectionV1,
    FormalAnalysisPrerequisiteError,
    FormalPilotAgreementGateReleaseV1,
    FormalPilotAgreementReleaseV1,
    PilotAgreementGateDecision,
    PilotAgreementUnitV1,
    RawReviewerLabelRefV1,
    assert_formal_pilot_agreement_exact_closure,
    assert_formal_pilot_agreement_gate_exact_closure,
    build_formal_analysis_input_release,
    build_formal_pilot_agreement_gate,
    build_formal_pilot_agreement_release,
    derive_case_system_metric_projection,
)
from material_agent.research.flatband_blinding import (
    EvidenceAccessPolicy,
    EvidenceExcerptV2,
    EvidenceRedistributionPolicy,
    EvidenceSpanScope,
    EvidenceSpanType,
    PrivateEvidenceMapEntryV1,
    PrivateIdentityMapV1,
    PrivateIdentityMapV2,
    PrivatePositionMapEntryV1,
    assert_reviewer_release_exact_coverage_v2,
    build_reviewer_release_v2,
)
from material_agent.research.flatband_contracts import (
    Assessability,
    BenchmarkSplit,
    BridgeJudgmentV1,
    BridgeVerdict,
    Dimensionality,
    EvidenceJudgmentV1,
    EvidenceReasonCode,
    ExpertEvidenceRelation,
    HardFailReason,
    MechanismFamily,
    RawExpertAnnotationV1,
    TargetBandClass,
    TriStateJudgment,
)
from material_agent.research.flatband_execution import (
    ExecutionPhase,
    MissingPositionReason,
    ResearchSystemId,
    RunCellStatus,
    assemble_execution_release_v3,
    build_execution_matrix_v2,
)
from material_agent.research.flatband_metrics import (
    GainKind,
    absolute_ndcg_at_5,
)

ModelT = TypeVar("ModelT", bound=StrictModel)
SHA_A = "a" * 64
SHA_B = "b" * 64
SHA_C = "c" * 64
SHA_D = "d" * 64
SHA_E = "e" * 64


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
            sha_field: digest,
            id_field: deterministic_id(prefix, {sha_field: digest}),
        }
    )


def _reidentified(
    value: ModelT,
    *,
    id_field: str,
    sha_field: str,
    prefix: str,
    **updates: object,
) -> ModelT:
    values = {
        field_name: getattr(value, field_name)
        for field_name in type(value).model_fields
        if field_name not in {id_field, sha_field}
    }
    values.update(updates)
    return _identified(
        type(value),
        id_field=id_field,
        sha_field=sha_field,
        prefix=prefix,
        values=values,
    )


def _present(
    position: int,
    *,
    packet: str,
    pool: str,
    judgment: str,
    cluster: str,
    grade: int,
    evidence: int = 1,
    bridge: BridgeVerdict = BridgeVerdict.CORRECT,
) -> AnalysisPositionV1:
    return AnalysisPositionV1(
        position=position,
        packet_id=packet,
        packet_sha256=canonical_sha256((packet, "packet")),
        pooled_unit_id=pool,
        gold_judgment_id=judgment,
        gold_judgment_sha256=canonical_sha256((judgment, "Gold")),
        final_duplicate_cluster_id=cluster,
        final_assessability=Assessability.ASSESSABLE,
        final_bridge_verdict=bridge,
        metric_relevance_gain=grade,
        metric_evidence_gain=evidence,
        gold_unit_denominator_included=True,
        forced_zero=False,
    )


def _missing(position: int) -> AnalysisPositionV1:
    return AnalysisPositionV1(
        position=position,
        gold_unit_denominator_included=False,
        forced_zero=True,
        missing_reason=MissingPositionReason.RUN_FAILED,
    )


def _zero_projection() -> CaseSystemMetricProjectionV1:
    return derive_case_system_metric_projection(
        tuple(_missing(position) for position in range(1, 6))
    )


def test_metrics_replay_final_gold_clusters_and_fixed_missing_denominator() -> None:
    positions = (
        _present(
            1,
            packet="packet-a",
            pool="pool-a",
            judgment="gold-a",
            cluster="cluster-shared",
            grade=3,
        ),
        _present(
            2,
            packet="packet-b",
            pool="pool-b",
            judgment="gold-b",
            cluster="cluster-shared",
            grade=3,
        ),
        _present(
            3,
            packet="packet-c",
            pool="pool-c",
            judgment="gold-c",
            cluster="cluster-c",
            grade=2,
            bridge=BridgeVerdict.CONDITIONAL,
        ),
        _missing(4),
        _missing(5),
    )
    projection = derive_case_system_metric_projection(positions)
    assert projection.returned_count == 3
    assert projection.linear_andcg_at_5 == absolute_ndcg_at_5(
        grades=(3, 3, 2),
        strict_hypothesis_cluster_ids=(
            "cluster-shared",
            "cluster-shared",
            "cluster-c",
        ),
        gain_kind=GainKind.LINEAR_PRIMARY,
    )
    assert projection.completion_at_5 == 0.6
    assert projection.evidence_valid_at_5 == 0.6
    assert projection.duplicate_rate_at_5 == 0.5
    assert projection.success_at_5 == 1
    assert projection.strong_success_at_5 == 1
    assert projection.bridge_correct_at_5 == 0.4


def test_projection_rejects_missing_denominator_gap_and_fake_score() -> None:
    values = _zero_projection().model_dump(mode="python")
    missing_position = _missing(5).model_dump(mode="python")
    with pytest.raises(ValidationError):
        AnalysisPositionV1.model_validate(
            {**missing_position, "fixed_position_denominator_included": False}
        )
    with pytest.raises(ValidationError):
        CaseSystemMetricProjectionV1.model_validate(
            {**values, "positions": values["positions"][:-1]}
        )

    gapped = list(values["positions"])
    gapped[4] = _present(
        5,
        packet="packet-gap",
        pool="pool-gap",
        judgment="gold-gap",
        cluster="cluster-gap",
        grade=1,
        evidence=0,
        bridge=BridgeVerdict.INCORRECT,
    ).model_dump(mode="python")
    with pytest.raises(ValidationError, match="contiguous ranking prefix"):
        CaseSystemMetricProjectionV1.model_validate(
            {**values, "positions": tuple(gapped), "returned_count": 1}
        )

    with pytest.raises(ValidationError, match="do not replay"):
        CaseSystemMetricProjectionV1.model_validate(
            {**values, "linear_andcg_at_5": 0.25}
        )


def _locked_cases() -> tuple[AnalysisCaseBindingV1, ...]:
    return tuple(
        AnalysisCaseBindingV1(
            case_id=f"case-{index:03d}",
            case_sha256=canonical_sha256(("case", index)),
            split=(
                BenchmarkSplit.LOCKED_IID
                if index < 30
                else BenchmarkSplit.LOCKED_OOD
            ),
            target_class=(
                TargetBandClass.FB100
                if index % 2 == 0
                else TargetBandClass.NB300
            ),
            dimensionality=(
                Dimensionality.TWO_D
                if index % 2 == 0
                else Dimensionality.THREE_D
            ),
            primary_mechanism_stratum=MechanismFamily.LATTICE_INTERFERENCE,
            leakage_component_id=f"component-{index // 3:03d}",
        )
        for index in range(60)
    )


def _row(
    case: AnalysisCaseBindingV1,
    system: ResearchSystemId,
    *,
    metrics: CaseSystemMetricProjectionV1 | None = None,
    suffix: str | None = None,
) -> CaseSystemAnalysisRowV1:
    token = suffix or f"{case.case_id}-{system.value}"
    return _identified(
        CaseSystemAnalysisRowV1,
        id_field="row_id",
        sha_field="row_sha256",
        prefix="analysis-row-v1",
        values={
            "execution_release_id": "execution-v2",
            "execution_release_sha256": SHA_A,
            "final_gold_release_id": "gold-v2",
            "final_gold_release_sha256": SHA_B,
            "cell_id": f"cell-{token}",
            "cell_sha256": canonical_sha256((token, "cell")),
            "terminal_result_id": f"terminal-{token}",
            "terminal_result_sha256": canonical_sha256((token, "terminal")),
            "top5_projection_id": f"projection-{token}",
            "top5_projection_sha256": canonical_sha256((token, "projection")),
            "case_id": case.case_id,
            "case_sha256": case.case_sha256,
            "split": case.split,
            "leakage_component_id": case.leakage_component_id,
            "system_id": system,
            "system_config_id": f"config-{system.value}",
            "system_config_sha256": canonical_sha256((system.value, "config")),
            "status": (
                RunCellStatus.FAILED
                if metrics is None or metrics.returned_count == 0
                else RunCellStatus.PARTIAL
            ),
            "metrics": metrics or _zero_projection(),
        },
    )


def _locked_release() -> AnalysisInputReleaseV1:
    cases = _locked_cases()
    rows = tuple(
        _row(case, system)
        for case in cases
        for system in (ResearchSystemId.B0, ResearchSystemId.FUSION)
    )
    return _identified(
        AnalysisInputReleaseV1,
        id_field="release_id",
        sha_field="release_sha256",
        prefix="analysis-input-release-v1",
        values={
            "release_kind": AnalysisReleaseKind.CASE_SYSTEM_METRICS,
            "execution_phase": ExecutionPhase.LOCKED_PRIMARY,
            "split_manifest_id": "split-v2",
            "split_manifest_sha256": SHA_C,
            "leakage_release_id": "leakage-v3",
            "leakage_release_sha256": SHA_D,
            "frozen_case_release_id": "frozen-v2",
            "frozen_case_release_sha256": SHA_E,
            "execution_release_id": "execution-v2",
            "execution_release_sha256": SHA_A,
            "final_gold_release_id": "gold-v2",
            "final_gold_release_sha256": SHA_B,
            "gold_exact_closure_inputs_sha256": canonical_sha256("gold-inputs"),
            "analysis_environment_sha256": canonical_sha256("analysis-env"),
            "cases": cases,
            "metric_rows": rows,
            "agreement_units": (),
            "assembled_at": "2026-08-09T21:00:00+08:00",
        },
    )


def _readdress_release(
    release: AnalysisInputReleaseV1, **updates: object
) -> AnalysisInputReleaseV1:
    return _reidentified(
        release,
        id_field="release_id",
        sha_field="release_sha256",
        prefix="analysis-input-release-v1",
        **updates,
    )


def test_locked_schema_exactly_covers_iid30_ood30_and_b0_fusion() -> None:
    release = _locked_release()
    assert len(release.cases) == 60
    assert len(release.metric_rows) == 120
    assert sum(item.split is BenchmarkSplit.LOCKED_IID for item in release.cases) == 30
    assert sum(item.split is BenchmarkSplit.LOCKED_OOD for item in release.cases) == 30

    with pytest.raises(ValidationError, match="exactly cover case x system"):
        _readdress_release(release, metric_rows=release.metric_rows[:-1])


def test_release_rejects_duplicate_case_wrong_split_and_wrong_component() -> None:
    release = _locked_release()
    duplicate_cases = (*release.cases[:-1], release.cases[-2])
    with pytest.raises(ValidationError, match="case-ID sorted and unique"):
        _readdress_release(release, cases=duplicate_cases)

    changed_case = release.cases[0].model_copy(
        update={"split": BenchmarkSplit.DEVELOPMENT}
    )
    with pytest.raises(ValidationError, match="preregistered phase"):
        _readdress_release(release, cases=(changed_case, *release.cases[1:]))

    first = release.metric_rows[0]
    forged_row = _reidentified(
        first,
        id_field="row_id",
        sha_field="row_sha256",
        prefix="analysis-row-v1",
        leakage_component_id="foreign-component",
    )
    with pytest.raises(ValidationError, match="analysis case/component"):
        _readdress_release(
            release, metric_rows=(forged_row, *release.metric_rows[1:])
        )


def test_release_rejects_cross_cell_gold_alias() -> None:
    release = _locked_release()
    case = release.cases[0]
    first_metrics = derive_case_system_metric_projection(
        (
            _present(
                1,
                packet="packet-one",
                pool="pool-one",
                judgment="shared-gold-id",
                cluster="cluster-one",
                grade=3,
            ),
            *tuple(_missing(position) for position in range(2, 6)),
        )
    )
    second_metrics = derive_case_system_metric_projection(
        (
            _present(
                1,
                packet="packet-two",
                pool="pool-two",
                judgment="shared-gold-id",
                cluster="cluster-two",
                grade=2,
                bridge=BridgeVerdict.CONDITIONAL,
            ),
            *tuple(_missing(position) for position in range(2, 6)),
        )
    )
    changed_rows = list(release.metric_rows)
    changed_rows[0] = _row(
        case, ResearchSystemId.B0, metrics=first_metrics, suffix="alias-a"
    )
    changed_rows[1] = _row(
        case, ResearchSystemId.FUSION, metrics=second_metrics, suffix="alias-b"
    )
    with pytest.raises(ValidationError, match="Gold judgment identity aliases"):
        _readdress_release(release, metric_rows=tuple(changed_rows))


def test_pilot_agreement_row_uses_exactly_two_raw_pre_adjudication_labels() -> None:
    labels = (
        RawReviewerLabelRefV1(
            reviewer_id="reviewer-a",
            annotation_id="raw-a",
            annotation_sha256=SHA_A,
            assessability=Assessability.ASSESSABLE,
            relevance_grade=3,
            evidence_valid=True,
            bridge_verdict=BridgeVerdict.CORRECT,
        ),
        RawReviewerLabelRefV1(
            reviewer_id="reviewer-b",
            annotation_id="raw-b",
            annotation_sha256=SHA_B,
            assessability=Assessability.ASSESSABLE,
            relevance_grade=2,
            evidence_valid=True,
            bridge_verdict=BridgeVerdict.CONDITIONAL,
        ),
    )
    unit = PilotAgreementUnitV1(
        case_id="case-pilot",
        case_sha256=SHA_C,
        leakage_component_id="component-pilot",
        pooled_unit_id="pool-pilot",
        packet_id="packet-pilot",
        packet_sha256=SHA_D,
        final_gold_judgment_id="gold-pilot",
        final_gold_judgment_sha256=SHA_E,
        raw_labels=labels,
        grade_alpha_included=True,
    )
    assert unit.grade_alpha_included is True
    with pytest.raises(ValidationError, match="inclusion does not replay"):
        PilotAgreementUnitV1.model_validate(
            {**unit.model_dump(mode="python"), "grade_alpha_included": False}
        )


def test_formal_assembler_stays_no_go_without_v2_eligibility_and_gold_seam() -> None:
    with pytest.raises(
        FormalAnalysisPrerequisiteError,
        match="PreRunEligibilityReleaseV2 and V2 Gold exact inputs",
    ):
        build_formal_analysis_input_release(
            split_manifest=None,  # type: ignore[arg-type]
            leakage_release=None,  # type: ignore[arg-type]
            frozen_case_release=None,  # type: ignore[arg-type]
            pre_run_eligibility_release_v2=None,
            execution_release=None,  # type: ignore[arg-type]
            final_gold_release=None,  # type: ignore[arg-type]
            gold_exact_closure_inputs_v2=None,
            execution_phase=ExecutionPhase.LOCKED_PRIMARY,
            assembled_at="2026-08-09T21:00:00+08:00",
        )


def _formal_bridge() -> BridgeJudgmentV1:
    return BridgeJudgmentV1(
        source_mechanism=TriStateJudgment.PASS,
        shared_invariant=TriStateJudgment.PASS,
        target_mapping=TriStateJudgment.PASS,
        transferable_control=TriStateJudgment.PASS,
        required_conditions=TriStateJudgment.PASS,
        breaking_conditions=TriStateJudgment.PASS,
        contradiction_handling=TriStateJudgment.PASS,
        overall=BridgeVerdict.CORRECT,
    )


def _formal_raw_annotations(
    *,
    maps: tuple[PrivateIdentityMapV2, ...],
    execution: Any,
    annotation_guide_sha256: str,
    review_round: int = 1,
    submitted_on: str = "2026-08-09",
    disagreements: int = 0,
    overrides: dict[
        tuple[str, str], tuple[Assessability, int | None]
    ] | None = None,
) -> tuple[RawExpertAnnotationV1, ...]:
    overrides = {} if overrides is None else overrides
    packet_by_id = {item.packet_id: item for item in execution.hypothesis_packets}
    case_order = {
        case_id: index
        for index, case_id in enumerate(
            sorted({item.case_id for item in execution.execution_matrix.cells})
        )
    }
    values: list[RawExpertAnnotationV1] = []
    for private_map in maps:
        for entry in private_map.entries:
            index = case_order[entry.case_id]
            grade = index % 4
            if private_map.expert_id == "reviewer-b" and index < disagreements:
                grade = (grade + 1) % 4
            assessability, selected_grade = overrides.get(
                (private_map.expert_id, entry.packet_id),
                (Assessability.ASSESSABLE, grade),
            )
            packet = packet_by_id[entry.packet_id]
            if assessability is Assessability.ASSESSABLE:
                evidence_judgments = tuple(
                    EvidenceJudgmentV1(
                        evidence_link_id=item.evidence_link_id,
                        expert_relation=ExpertEvidenceRelation.VALID_SUPPORT,
                        scope_match=True,
                        overclaim=False,
                        reason_code=EvidenceReasonCode.DIRECT_SCOPE_MATCH,
                    )
                    for item in entry.evidence_map
                )
                evidence_valid: bool | None = True
                bridge: BridgeJudgmentV1 | None = _formal_bridge()
                family: MechanismFamily | None = packet.mechanism_family
                hard_fails: tuple[HardFailReason, ...] = ()
                confidence: int | None = 4
            elif assessability is Assessability.SYSTEM_PACKET_INVALID:
                evidence_judgments = ()
                evidence_valid = None
                bridge = None
                family = None
                hard_fails = (HardFailReason.MALFORMED_PACKET,)
                confidence = 4
                selected_grade = 0
            else:
                evidence_judgments = ()
                evidence_valid = None
                bridge = None
                family = None
                hard_fails = ()
                confidence = None
                selected_grade = None
            values.append(
                _identified(
                    RawExpertAnnotationV1,
                    id_field="annotation_id",
                    sha_field="annotation_sha256",
                    prefix="expert-annotation",
                    values={
                        "blinded_unit_id": entry.blinded_unit_id,
                        "case_id": entry.case_id,
                        "case_sha256": entry.case_sha256,
                        "packet_id": entry.packet_id,
                        "packet_sha256": entry.packet_sha256,
                        "reviewer_id": private_map.expert_id,
                        "review_round": review_round,
                        "annotation_guide_sha256": annotation_guide_sha256,
                        "assessability": assessability,
                        "relevance_grade": selected_grade,
                        "evidence_valid": evidence_valid,
                        "evidence_judgments": evidence_judgments,
                        "bridge_judgment": bridge,
                        "mechanism_family": family,
                        "hard_fail_reasons": hard_fails,
                        "confidence": confidence,
                        "rationale": (
                            "Pre-adjudication expert assessment of the bounded packet."
                        ),
                        "started_at": f"{submitted_on}T20:51:00+08:00",
                        "submitted_at": f"{submitted_on}T20:52:00+08:00",
                    },
                )
            )
    return tuple(values)


@lru_cache(maxsize=1)
def _formal_r1_study() -> Any:
    from tests.unit import test_flatband_research_execution as execution_fixture

    return execution_fixture._formal_v3_upstream_fixture()


@lru_cache(maxsize=2)
def _formal_round_fixture(round_number: int) -> dict[str, Any]:
    from tests.unit import test_flatband_research_execution as execution_fixture

    if round_number == 1:
        study = _formal_r1_study()
        phase = ExecutionPhase.PILOT_R1
        day = "2026-08-09"
    elif round_number == 2:
        study = _formal_r2_study()
        phase = ExecutionPhase.PILOT_R2
        day = "2026-08-10"
    else:  # pragma: no cover - private fixture contract
        raise ValueError("formal Pilot fixture supports only rounds 1 and 2")
    matrix = build_execution_matrix_v2(
        study.manifest,
        execution_fixture._pilot_configs(),
        phase=phase,
    )
    budgets = tuple(
        execution_fixture._identified(
            execution_fixture.BudgetManifestV2,
            id_field="budget_manifest_id",
            sha_field="budget_manifest_sha256",
            prefix="budget-manifest-v2",
            values={
                "frozen_case_release_id": study.frozen.release_id,
                "frozen_case_release_sha256": study.frozen.release_sha256,
                "pre_run_eligibility_release_id": study.eligibility.release_id,
                "pre_run_eligibility_release_sha256": (
                    study.eligibility.release_sha256
                ),
                "pre_budget_closure_release_id": study.pre_budget_closure.release_id,
                "pre_budget_closure_release_sha256": (
                    study.pre_budget_closure.release_sha256
                ),
                "execution_matrix_id": matrix.matrix_id,
                "execution_matrix_sha256": matrix.matrix_sha256,
                "cell_id": cell.cell_id,
                "cell_sha256": cell.cell_sha256,
                "run_id": f"formal-v3-r{round_number}-run-{index:03d}",
                "case_id": cell.case_id,
                "case_sha256": cell.case_sha256,
                "system_config": next(
                    item
                    for item in matrix.system_configs
                    if item.config_id == cell.system_config_id
                ),
                "git_commit": "1" * 40,
                "runtime_environment_sha256": execution_fixture.SHA_C,
                "analysis_environment_sha256": execution_fixture.SHA_D,
                "max_walltime_seconds": 300,
                "frozen_at": f"{day}T20:41:00+08:00",
            },
        )
        for index, cell in enumerate(matrix.cells)
    )
    original_receipt_factory = execution_fixture._source_receipt_bundle
    original_span_factory = execution_fixture._span_text

    def later_receipt(*args: Any, **kwargs: Any):
        kwargs.update(
            query_created_at=f"{day}T20:42:10+08:00",
            hop_completed_at=f"{day}T20:42:20+08:00",
            page_completed_at=f"{day}T20:42:30+08:00",
        )
        return original_receipt_factory(*args, **kwargs)

    execution_fixture._source_receipt_bundle = later_receipt
    execution_fixture._span_text = lambda _span_id: (
        "Bounded metadata evidence reports a compact localized interference mode."
    )
    try:
        rankings = []
        terminals = []
        for cell, budget in zip(
            matrix.cells,
            budgets,
            strict=True,
        ):
            ranking = (
                execution_fixture._ranking(
                    budget,
                    count=1,
                    created_at=f"{day}T20:43:00+08:00",
                )
                if cell.system_id is ResearchSystemId.B0
                else None
            )
            if ranking is not None:
                rankings.append(ranking)
            terminals.append(
                execution_fixture._terminal(
                    budget,
                    ranking,
                    status=(
                        RunCellStatus.PARTIAL
                        if ranking is not None
                        else RunCellStatus.FAILED
                    ),
                    completed_at=f"{day}T20:44:00+08:00",
                )
            )
        packets = execution_fixture._packet_artifacts(tuple(rankings))
    finally:
        execution_fixture._source_receipt_bundle = original_receipt_factory
        execution_fixture._span_text = original_span_factory
    execution = assemble_execution_release_v3(
        matrix,
        budgets,
        tuple(rankings),
        tuple(terminals),
        frozen_case_release=study.frozen,
        pre_run_eligibility_release=study.eligibility,
        pre_budget_closure_release=study.pre_budget_closure,
        hypothesis_packets=packets,
        assembled_at=f"{day}T20:45:00+08:00",
    )
    excerpts = tuple(
        EvidenceExcerptV2(
            cell_id=receipt.cell_id,
            packet_id=receipt.packet_id,
            evidence_link_id=receipt.evidence_link_id,
            source_span_text=receipt.span_utf8,
            source_span_char_count=len(receipt.span_utf8),
            source_span_sha256=receipt.span_utf8_sha256,
            excerpt_start_offset=0,
            excerpt_end_offset=len(receipt.span_utf8),
            span_scope=EvidenceSpanScope.ABSTRACT,
            span_type=EvidenceSpanType.VERBATIM_EXCERPT,
            normalized_work_citation="Anonymous normalized work citation (2025)",
            excerpt=receipt.span_utf8,
            excerpt_char_count=len(receipt.span_utf8),
            excerpt_sha256=hashlib.sha256(
                receipt.span_utf8.encode("utf-8")
            ).hexdigest(),
            excerpt_truncated=False,
            access_policy=EvidenceAccessPolicy.AUTHORIZED_REVIEWER_ONLY,
            redistribution_policy=(
                EvidenceRedistributionPolicy.REVIEWER_PACKET_ONLY_NO_REDISTRIBUTION
            ),
            evidence_receipt_id=receipt.evidence_receipt_id,
            evidence_receipt_sha256=receipt.evidence_receipt_sha256,
            record_receipt_id=receipt.record_receipt_id,
            record_receipt_sha256=receipt.record_receipt_sha256,
            normalized_metadata_artifact_id=receipt.normalized_metadata_artifact_id,
            normalized_metadata_artifact_sha256=(
                receipt.normalized_metadata_artifact_sha256
            ),
            field_artifact_id=receipt.field_artifact_id,
            field_artifact_sha256=receipt.field_artifact_sha256,
            span_preimage_id=receipt.span_preimage_id,
            span_preimage_sha256=receipt.span_preimage_sha256,
            span_field=receipt.span_field,
            metadata_json_path=receipt.metadata_json_path,
            span_start_byte=receipt.span_start_byte,
            span_end_byte=receipt.span_end_byte,
            span_locator_sha256=receipt.span_locator_sha256,
        )
        for terminal in execution.terminal_results
        for bundle in terminal.source_receipt_bundles
        for receipt in bundle.evidence_links
    )
    blind_key = (
        f"pilot-reviewer-v3-r{round_number}-key-material-32bytes!!".encode()
    )
    renderer_sha256 = "d" * 64
    manifests, maps = build_reviewer_release_v2(
        frozen_case_release=study.frozen,
        execution_release=execution,
        pre_run_eligibility_release=study.eligibility,
        expert_registry=study.registry,
        evidence_excerpts=excerpts,
        blind_key=blind_key,
        renderer_sha256=renderer_sha256,
        sealed_at=f"{day}T20:46:00+08:00",
    )
    return {
        "study": study,
        "execution": execution,
        "manifests": manifests,
        "maps": maps,
        "excerpts": excerpts,
        "blind_key": blind_key,
        "renderer_sha256": renderer_sha256,
    }


@lru_cache(maxsize=1)
def _formal_pilot_fixture() -> dict[str, Any]:
    return _formal_round_fixture(1)


@lru_cache(maxsize=1)
def _formal_r2_study() -> Any:
    r1 = _formal_r1_study()
    cases_fixture = sys.modules.get("_flatband_research_cases_v3_fixture")
    if cases_fixture is None:  # pragma: no cover - fixed fixture load order
        raise RuntimeError("formal V3 cases fixture module is unavailable")
    original_guide_sha256 = cases_fixture.GUIDE_SHA
    revised_guide_sha256 = canonical_sha256("R2 revised annotation guide")
    cases_fixture.GUIDE_SHA = revised_guide_sha256
    try:
        study = cases_fixture._formal_v3_study(
            study_phase="PILOT_R2",
            prior_r1_leakage_context=r1.leakage_context,
            prior_r1_candidate_pool_release=r1.candidate_pool,
        )
    finally:
        cases_fixture.GUIDE_SHA = original_guide_sha256
    assert study.registry.annotation_guide_sha256 == revised_guide_sha256
    return study


@lru_cache(maxsize=1)
def _formal_r2_fixture() -> dict[str, Any]:
    return _formal_round_fixture(2)


@lru_cache(maxsize=1)
def _foreign_lineage_curation() -> Any:
    from tests.unit import test_flatband_research_leakage as leakage_fixture

    study = _formal_pilot_fixture()["study"]
    _registry, curation = leakage_fixture._v3_formal_registry(
        study.leakage.mechanism_lineage_registry.definitions,
        taxonomy_version=study.leakage.mechanism_lineage_registry.taxonomy_version,
    )
    return curation


def _assemble_formal_agreement(
    raw_annotations: tuple[RawExpertAnnotationV1, ...],
    *,
    fixture: dict[str, Any] | None = None,
    lineage_curation_release: Any | None = None,
    assembled_at: str = "2026-08-09T20:53:00+08:00",
) -> FormalPilotAgreementReleaseV1:
    fixture = _formal_pilot_fixture() if fixture is None else fixture
    study = fixture["study"]
    return build_formal_pilot_agreement_release(
        split_manifest=study.manifest,
        leakage_release=study.leakage,
        lineage_curation_release=(
            study.lineage_curation
            if lineage_curation_release is None
            else lineage_curation_release
        ),
        frozen_case_release=study.frozen,
        pre_run_eligibility_release=study.eligibility,
        execution_release=fixture["execution"],
        expert_registry=study.registry,
        reviewer_manifests=fixture["manifests"],
        private_identity_maps=fixture["maps"],
        evidence_excerpts=fixture["excerpts"],
        blind_key=fixture["blind_key"],
        renderer_sha256=fixture["renderer_sha256"],
        raw_annotations=raw_annotations,
        assembled_at=assembled_at,
    )


@lru_cache(maxsize=1)
def _base_raw_and_release() -> tuple[
    tuple[RawExpertAnnotationV1, ...], FormalPilotAgreementReleaseV1
]:
    fixture = _formal_pilot_fixture()
    raw = _formal_raw_annotations(
        maps=fixture["maps"],
        execution=fixture["execution"],
        annotation_guide_sha256=fixture["study"].registry.annotation_guide_sha256,
    )
    return raw, _assemble_formal_agreement(raw)


@lru_cache(maxsize=1)
def _base_gate() -> FormalPilotAgreementGateReleaseV1:
    study = _formal_pilot_fixture()["study"]
    return build_formal_pilot_agreement_gate(
        agreement_release=_base_raw_and_release()[1],
        leakage_context=study.leakage_context,
        lineage_curation_release=study.lineage_curation,
        evaluated_at="2026-08-09T20:54:00+08:00",
    )


@lru_cache(maxsize=1)
def _revision_release_and_gate() -> tuple[
    FormalPilotAgreementReleaseV1, FormalPilotAgreementGateReleaseV1
]:
    fixture = _formal_pilot_fixture()
    raw = _formal_raw_annotations(
        maps=fixture["maps"],
        execution=fixture["execution"],
        annotation_guide_sha256=fixture["study"].registry.annotation_guide_sha256,
        disagreements=8,
    )
    release = _assemble_formal_agreement(raw)
    gate = build_formal_pilot_agreement_gate(
        agreement_release=release,
        leakage_context=fixture["study"].leakage_context,
        lineage_curation_release=fixture["study"].lineage_curation,
        evaluated_at="2026-08-09T20:54:00+08:00",
    )
    return release, gate


@lru_cache(maxsize=2)
def _r2_raw_and_release(
    disagreements: int = 0,
) -> tuple[tuple[RawExpertAnnotationV1, ...], FormalPilotAgreementReleaseV1]:
    fixture = _formal_r2_fixture()
    raw = _formal_raw_annotations(
        maps=fixture["maps"],
        execution=fixture["execution"],
        annotation_guide_sha256=fixture["study"].registry.annotation_guide_sha256,
        review_round=2,
        submitted_on="2026-08-10",
        disagreements=disagreements,
    )
    return raw, _assemble_formal_agreement(
        raw,
        fixture=fixture,
        assembled_at="2026-08-10T20:53:00+08:00",
    )


def test_agreement_upstream_replays_canonical_reviewer_v2_from_v3() -> None:
    fixture = _formal_pilot_fixture()
    study = fixture["study"]
    assert_reviewer_release_exact_coverage_v2(
        frozen_case_release=study.frozen,
        execution_release=fixture["execution"],
        pre_run_eligibility_release=study.eligibility,
        expert_registry=study.registry,
        reviewer_manifests=fixture["manifests"],
        private_identity_maps=fixture["maps"],
        evidence_excerpts=fixture["excerpts"],
        blind_key=fixture["blind_key"],
        renderer_sha256=fixture["renderer_sha256"],
    )
    assert len(fixture["execution"].hypothesis_packets) == 30
    assert len(fixture["manifests"]) == len(fixture["maps"]) == 2
    assert sum(len(item.entries) for item in fixture["maps"]) == 60


def test_agreement_upstream_rejects_readdressed_public_excerpt_tamper() -> None:
    fixture = _formal_pilot_fixture()
    study = fixture["study"]
    manifest = fixture["manifests"][0]
    packet = manifest.packets[0]
    span = packet.evidence[0]

    tampered_excerpt = f"{span.excerpt} Reviewer-visible tamper."
    span_values = {
        field_name: getattr(span, field_name)
        for field_name in type(span).model_fields
        if field_name != "content_sha256"
    }
    span_values.update(
        excerpt=tampered_excerpt,
        excerpt_char_count=len(tampered_excerpt),
    )
    span_draft = type(span).model_construct(**span_values)
    forged_span = type(span).model_validate(
        {
            **span_values,
            "content_sha256": canonical_sha256(
                span_draft.model_dump(mode="python", exclude={"content_sha256"})
            ),
        }
    )

    packet_values = {
        field_name: getattr(packet, field_name)
        for field_name in type(packet).model_fields
        if field_name != "reviewer_packet_sha256"
    }
    packet_values["evidence"] = (forged_span, *packet.evidence[1:])
    packet_draft = type(packet).model_construct(**packet_values)
    forged_packet = type(packet).model_validate(
        {
            **packet_values,
            "reviewer_packet_sha256": canonical_sha256(
                packet_draft.model_dump(
                    mode="python", exclude={"reviewer_packet_sha256"}
                )
            ),
        }
    )
    forged_manifest = _reidentified(
        manifest,
        id_field="manifest_id",
        sha_field="manifest_sha256",
        prefix="reviewer-manifest-v2",
        packets=(forged_packet, *manifest.packets[1:]),
    )
    private_map = fixture["maps"][0]
    forged_entries = tuple(
        entry.model_copy(
            update={"reviewer_packet_sha256": forged_packet.reviewer_packet_sha256}
        )
        if entry.reviewer_packet_id == forged_packet.reviewer_packet_id
        else entry
        for entry in private_map.entries
    )
    forged_map = _reidentified(
        private_map,
        id_field="identity_map_id",
        sha_field="identity_map_sha256",
        prefix="private-identity-map-v2",
        reviewer_manifest_id=forged_manifest.manifest_id,
        reviewer_manifest_sha256=forged_manifest.manifest_sha256,
        entries=forged_entries,
    )

    raw = _formal_raw_annotations(
        maps=fixture["maps"],
        execution=fixture["execution"],
        annotation_guide_sha256=study.registry.annotation_guide_sha256,
    )
    with pytest.raises(ValueError, match="exact deterministic replay"):
        build_formal_pilot_agreement_release(
            split_manifest=study.manifest,
            leakage_release=study.leakage,
            lineage_curation_release=study.lineage_curation,
            frozen_case_release=study.frozen,
            pre_run_eligibility_release=study.eligibility,
            execution_release=fixture["execution"],
            expert_registry=study.registry,
            reviewer_manifests=(forged_manifest, *fixture["manifests"][1:]),
            private_identity_maps=(forged_map, *fixture["maps"][1:]),
            evidence_excerpts=fixture["excerpts"],
            blind_key=fixture["blind_key"],
            renderer_sha256=fixture["renderer_sha256"],
            raw_annotations=raw,
            assembled_at="2026-08-09T20:53:00+08:00",
        )


def test_formal_agreement_rejects_foreign_curation_root() -> None:
    fixture = _formal_pilot_fixture()
    raw = _formal_raw_annotations(
        maps=fixture["maps"],
        execution=fixture["execution"],
        annotation_guide_sha256=fixture["study"].registry.annotation_guide_sha256,
    )
    with pytest.raises(ValueError, match="foreign public registry|outside the frozen"):
        _assemble_formal_agreement(
            raw,
            lineage_curation_release=_foreign_lineage_curation(),
        )


def test_formal_pilot_agreement_replays_native_v3_exact_cover() -> None:
    raw, release = _base_raw_and_release()
    fixture = _formal_pilot_fixture()
    study = fixture["study"]
    assert len(release.cases) == 30
    assert len(release.units) == 30
    assert all(len(item.raw_labels) == 2 for item in release.units)
    assert release.native_reviewer_map_v2_required is True
    assert release.caller_supplied_rated_units_allowed is False
    assert_formal_pilot_agreement_exact_closure(
        release,
        split_manifest=study.manifest,
        leakage_release=study.leakage,
        lineage_curation_release=study.lineage_curation,
        frozen_case_release=study.frozen,
        pre_run_eligibility_release=study.eligibility,
        execution_release=fixture["execution"],
        expert_registry=study.registry,
        reviewer_manifests=fixture["manifests"],
        private_identity_maps=fixture["maps"],
        evidence_excerpts=fixture["excerpts"],
        blind_key=fixture["blind_key"],
        renderer_sha256=fixture["renderer_sha256"],
        raw_annotations=raw,
    )


def test_formal_pilot_agreement_rejects_easy_subset_and_third_rating() -> None:
    raw, _release = _base_raw_and_release()
    with pytest.raises(ValueError, match="do not exactly cover"):
        _assemble_formal_agreement(raw[:-1])
    with pytest.raises(ValueError, match="duplicate labels"):
        _assemble_formal_agreement((*raw, raw[0]))

    third = _reidentified(
        raw[0],
        id_field="annotation_id",
        sha_field="annotation_sha256",
        prefix="expert-annotation",
        reviewer_id="reviewer-c",
    )
    with pytest.raises(ValueError, match="do not exactly cover"):
        _assemble_formal_agreement((*raw, third))


def test_formal_pilot_agreement_rejects_legacy_v1_reviewer_map() -> None:
    raw, _release = _base_raw_and_release()
    fixture = _formal_pilot_fixture()
    v2 = fixture["maps"][0]
    positions = tuple(
        PrivatePositionMapEntryV1(
            **{
                field_name: getattr(entry, field_name)
                for field_name in PrivatePositionMapEntryV1.model_fields
                if field_name != "evidence_map"
            },
            evidence_map=tuple(
                PrivateEvidenceMapEntryV1(
                    **{
                        field_name: getattr(evidence, field_name)
                        for field_name in PrivateEvidenceMapEntryV1.model_fields
                    }
                )
                for evidence in entry.evidence_map
            ),
        )
        for entry in v2.entries
    )
    legacy = _identified(
        PrivateIdentityMapV1,
        id_field="identity_map_id",
        sha_field="identity_map_sha256",
        prefix="private-identity-map",
        values={
            field_name: getattr(v2, field_name)
            for field_name in PrivateIdentityMapV1.model_fields
            if field_name
            not in {"schema_version", "identity_map_id", "identity_map_sha256", "entries"}
        }
        | {"entries": positions},
    )
    with pytest.raises(ValidationError, match="PrivateIdentityMapV2"):
        build_formal_pilot_agreement_release(
            split_manifest=fixture["study"].manifest,
            leakage_release=fixture["study"].leakage,
            lineage_curation_release=fixture["study"].lineage_curation,
            frozen_case_release=fixture["study"].frozen,
            pre_run_eligibility_release=fixture["study"].eligibility,
            execution_release=fixture["execution"],
            expert_registry=fixture["study"].registry,
            reviewer_manifests=fixture["manifests"],
            private_identity_maps=(legacy, fixture["maps"][1]),  # type: ignore[arg-type]
            evidence_excerpts=fixture["excerpts"],
            blind_key=fixture["blind_key"],
            renderer_sha256=fixture["renderer_sha256"],
            raw_annotations=raw,
            assembled_at="2026-08-09T20:53:00+08:00",
        )


def test_formal_pilot_agreement_rejects_fake_case_component_and_rated_rows() -> None:
    raw, release = _base_raw_and_release()
    fake_case = _reidentified(
        raw[0],
        id_field="annotation_id",
        sha_field="annotation_sha256",
        prefix="expert-annotation",
        case_id="caller-fake-case",
        case_sha256=canonical_sha256("caller-fake-case"),
    )
    with pytest.raises(ValueError, match="foreign unit"):
        _assemble_formal_agreement((fake_case, *raw[1:]))

    first_case = release.cases[0]
    fake_component = first_case.model_copy(
        update={"leakage_component_id": "caller-fake-component"}
    )
    changed_units = tuple(
        item.model_copy(
            update={"leakage_component_id": "caller-fake-component"}
        )
        if item.case_id == first_case.case_id
        else item
        for item in release.units
    )
    forged_release = _identified(
        FormalPilotAgreementReleaseV1,
        id_field="release_id",
        sha_field="release_sha256",
        prefix="pilot-agreement-release-v1",
        values={
            **{
                field_name: getattr(release, field_name)
                for field_name in type(release).model_fields
                if field_name not in {"release_id", "release_sha256"}
            },
            "cases": tuple(
                fake_component if item.case_id == first_case.case_id else item
                for item in release.cases
            ),
            "units": changed_units,
        },
    )
    fixture = _formal_pilot_fixture()
    study = fixture["study"]
    with pytest.raises(ValueError, match="does not replay"):
        assert_formal_pilot_agreement_exact_closure(
            forged_release,
            split_manifest=study.manifest,
            leakage_release=study.leakage,
            lineage_curation_release=study.lineage_curation,
            frozen_case_release=study.frozen,
            pre_run_eligibility_release=study.eligibility,
            execution_release=fixture["execution"],
            expert_registry=study.registry,
            reviewer_manifests=fixture["manifests"],
            private_identity_maps=fixture["maps"],
            evidence_excerpts=fixture["excerpts"],
            blind_key=fixture["blind_key"],
            renderer_sha256=fixture["renderer_sha256"],
            raw_annotations=raw,
        )
    with pytest.raises(TypeError):
        build_formal_pilot_agreement_gate(  # type: ignore[call-arg]
            agreement_release=release,
            leakage_context=study.leakage_context,
            lineage_curation_release=study.lineage_curation,
            evaluated_at="2026-08-09T20:54:00+08:00",
            rated_units=(),
        )


def test_paired_system_invalid_is_grade_zero_but_unilateral_or_case_invalid_fails() -> None:
    fixture = _formal_pilot_fixture()
    guide = fixture["study"].registry.annotation_guide_sha256
    first_packet = fixture["maps"][0].entries[0].packet_id
    paired_raw = _formal_raw_annotations(
        maps=fixture["maps"],
        execution=fixture["execution"],
        annotation_guide_sha256=guide,
        overrides={
            ("reviewer-a", first_packet): (
                Assessability.SYSTEM_PACKET_INVALID,
                0,
            ),
            ("reviewer-b", first_packet): (
                Assessability.SYSTEM_PACKET_INVALID,
                0,
            ),
        },
    )
    paired_release = _assemble_formal_agreement(paired_raw)
    paired_unit = next(
        item for item in paired_release.units if item.packet_id == first_packet
    )
    assert paired_unit.ordinal_ratings == (0, 0)
    assert paired_release.round_fail_closed is False
    paired_gate = build_formal_pilot_agreement_gate(
        agreement_release=paired_release,
        leakage_context=fixture["study"].leakage_context,
        lineage_curation_release=fixture["study"].lineage_curation,
        evaluated_at="2026-08-09T20:54:00+08:00",
    )
    assert paired_gate.paired_system_packet_invalid_units == 1
    assert paired_gate.rated_units == paired_gate.total_units

    unilateral_raw = _formal_raw_annotations(
        maps=fixture["maps"],
        execution=fixture["execution"],
        annotation_guide_sha256=guide,
        overrides={
            ("reviewer-a", first_packet): (
                Assessability.SYSTEM_PACKET_INVALID,
                0,
            )
        },
    )
    unilateral_release = _assemble_formal_agreement(unilateral_raw)
    unilateral_gate = build_formal_pilot_agreement_gate(
        agreement_release=unilateral_release,
        leakage_context=fixture["study"].leakage_context,
        lineage_curation_release=fixture["study"].lineage_curation,
        evaluated_at="2026-08-09T20:54:00+08:00",
    )
    assert unilateral_gate.decision is PilotAgreementGateDecision.ROUND_FAIL_CLOSED
    assert unilateral_gate.alpha is None

    case_invalid_raw = _formal_raw_annotations(
        maps=fixture["maps"],
        execution=fixture["execution"],
        annotation_guide_sha256=guide,
        overrides={
            ("reviewer-a", first_packet): (Assessability.CASE_INVALID, None)
        },
    )
    case_invalid_release = _assemble_formal_agreement(case_invalid_raw)
    case_invalid_gate = build_formal_pilot_agreement_gate(
        agreement_release=case_invalid_release,
        leakage_context=fixture["study"].leakage_context,
        lineage_curation_release=fixture["study"].lineage_curation,
        evaluated_at="2026-08-09T20:54:00+08:00",
    )
    assert case_invalid_gate.decision is PilotAgreementGateDecision.ROUND_FAIL_CLOSED
    assert case_invalid_gate.case_invalid_units == 1


def test_formal_pilot_r1_gate_freezes_alpha_bootstrap_and_thresholds() -> None:
    base_release = _base_raw_and_release()[1]
    gate = _base_gate()
    assert gate.alpha == 1.0
    assert gate.decision is PilotAgreementGateDecision.R1_PASS_MAIN_ALLOWED
    assert gate.bootstrap_replicates == PILOT_ALPHA_BOOTSTRAP_REPLICATES
    assert gate.bootstrap_seed == PILOT_ALPHA_BOOTSTRAP_SEED
    assert gate.minimum_exact_agreement_used_as_gate is False
    assert_formal_pilot_agreement_gate_exact_closure(
        gate,
        agreement_release=base_release,
        leakage_context=_formal_pilot_fixture()["study"].leakage_context,
        lineage_curation_release=(
            _formal_pilot_fixture()["study"].lineage_curation
        ),
    )

    revision_release, revision_gate = _revision_release_and_gate()
    assert 0.667 <= revision_gate.alpha < 0.80  # type: ignore[operator]
    assert revision_gate.decision is (
        PilotAgreementGateDecision.R1_GUIDE_REVISION_AND_DISJOINT_R2_REQUIRED
    )
    fixture = _formal_pilot_fixture()
    below_raw = _formal_raw_annotations(
        maps=fixture["maps"],
        execution=fixture["execution"],
        annotation_guide_sha256=fixture["study"].registry.annotation_guide_sha256,
        disagreements=9,
    )
    below_gate = build_formal_pilot_agreement_gate(
        agreement_release=_assemble_formal_agreement(below_raw),
        leakage_context=fixture["study"].leakage_context,
        lineage_curation_release=fixture["study"].lineage_curation,
        evaluated_at="2026-08-09T20:54:00+08:00",
    )
    assert below_gate.alpha < 0.667  # type: ignore[operator]
    assert below_gate.decision is PilotAgreementGateDecision.STOP_R1_BELOW_0_667
    assert revision_release.release_id != base_release.release_id


def test_formal_pilot_r2_requires_revised_guide_and_disjoint_cases_components() -> None:
    revision_release, revision_gate = _revision_release_and_gate()
    r1_study = _formal_pilot_fixture()["study"]
    _r2_raw, r2 = _r2_raw_and_release()
    r2_study = _formal_r2_fixture()["study"]

    assert r2.execution_phase is ExecutionPhase.PILOT_R2
    assert r2.annotation_guide_sha256 != revision_release.annotation_guide_sha256
    assert {item.case_id for item in r2.cases}.isdisjoint(
        item.case_id for item in revision_release.cases
    )
    assert {item.leakage_component_id for item in r2.cases}.isdisjoint(
        item.leakage_component_id for item in revision_release.cases
    )
    assert r2_study.lineage_curation == r1_study.lineage_curation
    assert (
        revision_release.lineage_assignment_curation_release
        == r1_study.assignment_curation
    )
    assert (
        r2.lineage_assignment_curation_release
        == r2_study.assignment_curation
    )
    assert (
        r2.lineage_assignment_curation_release.release_id
        != revision_release.lineage_assignment_curation_release.release_id
    )
    assert r2_study.pre_budget_closure.cross_round_union_verified is True
    assert (
        r2_study.pre_budget_closure.prior_r1_leakage_context
        == r1_study.leakage_context
    )

    r2_gate = build_formal_pilot_agreement_gate(
        agreement_release=r2,
        leakage_context=r2_study.leakage_context,
        lineage_curation_release=r2_study.lineage_curation,
        prior_r1_agreement_release=revision_release,
        prior_r1_gate=revision_gate,
        prior_r1_leakage_context=r1_study.leakage_context,
        evaluated_at="2026-08-10T20:54:00+08:00",
    )
    assert r2_gate.alpha == 1.0
    assert r2_gate.decision is PilotAgreementGateDecision.R2_PASS_MAIN_ALLOWED
    assert r2_gate.r2_cases_and_components_disjoint is True
    assert_formal_pilot_agreement_gate_exact_closure(
        r2_gate,
        agreement_release=r2,
        leakage_context=r2_study.leakage_context,
        lineage_curation_release=r2_study.lineage_curation,
        prior_r1_agreement_release=revision_release,
        prior_r1_gate=revision_gate,
        prior_r1_leakage_context=r1_study.leakage_context,
    )

    forged_assignment_root = r2.model_copy(
        update={
            "lineage_assignment_curation_release": (
                revision_release.lineage_assignment_curation_release
            )
        }
    )
    with pytest.raises(ValueError, match="SHA-256|private-curation projection"):
        build_formal_pilot_agreement_gate(
            agreement_release=forged_assignment_root,
            leakage_context=r2_study.leakage_context,
            lineage_curation_release=r2_study.lineage_curation,
            prior_r1_agreement_release=revision_release,
            prior_r1_gate=revision_gate,
            prior_r1_leakage_context=r1_study.leakage_context,
            evaluated_at="2026-08-10T20:54:00+08:00",
        )
