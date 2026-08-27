"""Light structural tests for the Main execution bridge.

The fixture deliberately consists entirely of failed synthetic cells and
monkeypatches the expensive upstream scientific replay.  It proves wiring,
exact coverage and fail-closed accounting surfaces only; it is not a Main120
benchmark run or a scientific positive result.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

import material_agent.research.flatband_main_execution as execution_bridge
from material_agent.inspiration.models import canonical_sha256, deterministic_id
from material_agent.research.flatband_analysis_v2 import AnalysisTraceRoleV2
from material_agent.research.flatband_arm_runtime import ArmExecutionTraceV1
from material_agent.research.flatband_contracts import (
    BenchmarkSplit,
    BenchmarkSplitManifestV2,
)
from material_agent.research.flatband_execution import (
    BudgetManifestV1,
    ExecutionCellV1,
    ExecutionMatrixV2,
    ExecutionPhase,
    ExecutionReleaseV2,
    MissingPositionReason,
    ProjectedTop5PositionV1,
    ResearchSystemId,
    RunCellStatus,
    SystemConfigV1,
    TerminalRunResultV1,
    Top5ProjectionV1,
)
from material_agent.research.flatband_main import MainPhaseAuthorizationReleaseV1
from material_agent.research.flatband_main_execution import (
    MainPhaseExecutionCellEvidenceV1,
    MainPhaseExecutionReleaseV1,
    assert_main_phase_execution_exact_v1,
    build_analysis_trace_evidence_index_from_main_v1,
    build_main_phase_execution_release_v1,
    main_gold_execution_cells_from_main_v1,
)


def _sha(value: object) -> str:
    return canonical_sha256(value)


@pytest.fixture(autouse=True)
def _structural_only(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        execution_bridge, "_revalidate", lambda value, _model_type: value
    )
    monkeypatch.setattr(
        execution_bridge, "_assert_execution_release_exact", lambda _value: None
    )
    monkeypatch.setattr(
        execution_bridge,
        "assert_main_phase_authorization_exact_v1",
        lambda _value: None,
    )

    def structural_address(
        model_type: type[Any],
        *,
        id_field: str,
        sha_field: str,
        prefix: str,
        values: dict[str, object],
    ) -> Any:
        draft = model_type.model_construct(**values)
        digest = canonical_sha256(
            draft.model_dump(mode="python", exclude={id_field, sha_field})
        )
        value = model_type.model_construct(
            **values,
            **{
                id_field: deterministic_id(prefix, {sha_field: digest}),
                sha_field: digest,
            },
        )
        if model_type is MainPhaseExecutionCellEvidenceV1:
            return value.validate_cell()
        if model_type is MainPhaseExecutionReleaseV1:
            return value.validate_release()
        return value

    monkeypatch.setattr(execution_bridge, "_build_addressed", structural_address)


def _config(system_id: ResearchSystemId) -> SystemConfigV1:
    components = (
        (
            ResearchSystemId.E1,
            ResearchSystemId.E2_A,
            ResearchSystemId.E3,
        )
        if system_id is ResearchSystemId.FUSION
        else ()
    )
    return SystemConfigV1.model_construct(
        config_id=f"config-{system_id.value.replace('-', '').lower()}",
        config_sha256=_sha(("config", system_id.value)),
        system_id=system_id,
        fusion_components=components,
        local_semantic_model=None,
    )


def _all_failed_phase(
    phase: ExecutionPhase = ExecutionPhase.DEVELOPMENT_FUSION,
    *,
    pre_budget: object | None = None,
) -> tuple[
    MainPhaseAuthorizationReleaseV1, ExecutionReleaseV2
]:
    system_ids = (
        (
            ResearchSystemId.B0,
            ResearchSystemId.E1,
            ResearchSystemId.E2_A,
            ResearchSystemId.E2_B,
            ResearchSystemId.E3,
        )
        if phase is ExecutionPhase.DEVELOPMENT_ABLATIONS
        else (ResearchSystemId.B0, ResearchSystemId.FUSION)
    )
    configs = tuple(_config(item) for item in system_ids)
    if pre_budget is None:
        manifest = BenchmarkSplitManifestV2.model_construct(
            manifest_id="synthetic-main-manifest",
            manifest_sha256=_sha("synthetic-main-manifest"),
            cases=(),
        )
        frozen = type("Frozen", (), {"split_manifest": manifest})()
        pre_budget = type(
            "PreBudget", (), {"frozen_case_release": frozen}
        )()
    else:
        manifest = pre_budget.frozen_case_release.split_manifest  # type: ignore[attr-defined]
    early = phase is ExecutionPhase.DEVELOPMENT_ABLATIONS
    authorized_at = (
        "2026-08-10T00:00:00+08:00"
        if early
        else "2026-08-10T00:01:00+08:00"
    )
    frozen_at = (
        "2026-08-10T00:00:10+08:00"
        if early
        else "2026-08-10T00:02:00+08:00"
    )
    completed_at = (
        "2026-08-10T00:00:20+08:00"
        if early
        else "2026-08-10T00:03:00+08:00"
    )
    execution_assembled_at = (
        "2026-08-10T00:00:30+08:00"
        if early
        else "2026-08-10T00:04:00+08:00"
    )
    case_ids = tuple(f"case-{index:03d}" for index in range(60))
    matrix_cells: list[ExecutionCellV1] = []
    budgets: list[BudgetManifestV1] = []
    terminals: list[TerminalRunResultV1] = []
    projections: list[Top5ProjectionV1] = []
    for case_id in case_ids:
        case_sha = _sha((case_id, "case"))
        for config in configs:
            cell_id = f"cell-{case_id}-{config.system_id.value.lower()}"
            cell_sha = _sha((cell_id, "cell"))
            budget_id = f"budget-{cell_id}"
            budget_sha = _sha((budget_id, "budget"))
            run_id = f"run-{cell_id}"
            matrix_cells.append(
                ExecutionCellV1.model_construct(
                    cell_id=cell_id,
                    cell_sha256=cell_sha,
                    split=BenchmarkSplit.DEVELOPMENT,
                    case_id=case_id,
                    case_sha256=case_sha,
                    system_id=config.system_id,
                    system_config_id=config.config_id,
                    system_config_sha256=config.config_sha256,
                )
            )
            budgets.append(
                BudgetManifestV1.model_construct(
                    budget_manifest_id=budget_id,
                    budget_manifest_sha256=budget_sha,
                    cell_id=cell_id,
                    cell_sha256=cell_sha,
                    run_id=run_id,
                    case_id=case_id,
                    case_sha256=case_sha,
                    system_config=config,
                    frozen_at=frozen_at,
                )
            )
            terminal_id = f"terminal-{cell_id}"
            terminal_sha = _sha((terminal_id, "terminal"))
            terminal = TerminalRunResultV1.model_construct(
                terminal_result_id=terminal_id,
                terminal_result_sha256=terminal_sha,
                budget_manifest_id=budget_id,
                budget_manifest_sha256=budget_sha,
                ranking_id=None,
                ranking_sha256=None,
                cell_id=cell_id,
                run_id=run_id,
                case_id=case_id,
                case_sha256=case_sha,
                system_config_id=config.config_id,
                system_config_sha256=config.config_sha256,
                status=RunCellStatus.FAILED,
                source_receipt_bundles=(),
                source_usage=(),
                llm_invocation_receipts=(),
                local_model_invocation_receipts=(),
                actual_llm_calls=0,
                actual_llm_input_tokens=0,
                actual_llm_output_tokens=0,
                actual_llm_metadata_packets=0,
                actual_local_model_input_tokens=0,
                completed_at=completed_at,
            )
            terminals.append(terminal)
            projections.append(
                Top5ProjectionV1.model_construct(
                    projection_id=f"projection-{cell_id}",
                    projection_sha256=_sha((cell_id, "projection")),
                    cell_id=cell_id,
                    terminal_result_id=terminal_id,
                    terminal_result_sha256=terminal_sha,
                    ranking_id=None,
                    ranking_sha256=None,
                    status=RunCellStatus.FAILED,
                    positions=tuple(
                        ProjectedTop5PositionV1(
                            position=position,
                            forced_zero=True,
                            fixed_gain=0,
                            missing_reason=MissingPositionReason.RUN_FAILED,
                        )
                        for position in range(1, 6)
                    ),
                )
            )
    matrix = ExecutionMatrixV2.model_construct(
        matrix_id=f"{phase.value.lower()}-matrix",
        matrix_sha256=_sha((phase.value, "matrix")),
        phase=phase,
        split_manifest=manifest,
        system_configs=configs,
        cells=tuple(matrix_cells),
    )
    release = ExecutionReleaseV2.model_construct(
        release_id=f"{phase.value.lower()}-execution",
        release_sha256=_sha((phase.value, "execution")),
        git_commit="a" * 40,
        runtime_environment_sha256=_sha("runtime"),
        analysis_environment_sha256=_sha("analysis"),
        execution_matrix=matrix,
        budget_manifests=tuple(budgets),
        rankings=(),
        hypothesis_packets=(),
        terminal_results=tuple(terminals),
        top5_projections=tuple(projections),
        assembled_at=execution_assembled_at,
    )
    authorization = MainPhaseAuthorizationReleaseV1.model_construct(
        release_id=f"{phase.value.lower()}-authorization",
        release_sha256=_sha((phase.value, "authorization")),
        pre_budget_closure_release=pre_budget,
        execution_phase=phase,
        authorized_case_ids=case_ids,
        authorized_at=authorized_at,
    )
    return authorization, release


def _all_failed_development_fusion() -> tuple[
    MainPhaseAuthorizationReleaseV1,
    ExecutionReleaseV2,
    MainPhaseExecutionReleaseV1,
]:
    component_authorization, component_execution = _all_failed_phase(
        ExecutionPhase.DEVELOPMENT_ABLATIONS
    )
    component_release = build_main_phase_execution_release_v1(
        phase_authorization=component_authorization,
        execution_release=component_execution,
        arm_traces=(),
        assembled_at="2026-08-10T00:00:40+08:00",
    )
    authorization, execution = _all_failed_phase(
        pre_budget=component_authorization.pre_budget_closure_release
    )
    return authorization, execution, component_release


def test_all_failed_phase_has_exact_unified_zero_cells(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    authorization, execution, component_release = _all_failed_development_fusion()
    release = build_main_phase_execution_release_v1(
        phase_authorization=authorization,
        execution_release=execution,
        arm_traces=(),
        component_execution_release=component_release,
        assembled_at="2026-08-10T00:05:00+08:00",
    )
    assert len(release.cells) == 120
    assert {item.analysis_role for item in release.cells} == {
        AnalysisTraceRoleV2.B0,
        AnalysisTraceRoleV2.FUSION,
    }
    assert all(item.arm_trace is None for item in release.cells)
    assert all(
        position.missing_reason is MissingPositionReason.RUN_FAILED
        for item in release.cells
        for position in item.top5_projection.positions
    )
    assert_main_phase_execution_exact_v1(release)
    assert main_gold_execution_cells_from_main_v1(release) == release.cells

    calls: list[dict[str, object]] = []

    def capture(**kwargs: object) -> object:
        calls.append(kwargs)
        return kwargs

    monkeypatch.setattr(
        execution_bridge, "build_analysis_trace_evidence_v2", capture
    )
    index = build_analysis_trace_evidence_index_from_main_v1(release)
    assert len(index) == 120
    assert all(item["trace"] is None for item in index)


def test_extra_arm_trace_and_legacy_llm_accounting_fail_closed() -> None:
    authorization, execution, component_release = _all_failed_development_fusion()
    with pytest.raises(ValueError, match="require exactly one complete component"):
        build_main_phase_execution_release_v1(
            phase_authorization=authorization,
            execution_release=execution,
            arm_traces=(),
            assembled_at="2026-08-10T00:05:00+08:00",
        )
    extra = ArmExecutionTraceV1.model_construct(
        trace_id="extra-trace",
        trace_sha256=_sha("extra-trace"),
        ranking=type(
            "Ranking",
            (),
            {"ranking_id": "extra-ranking", "ranking_sha256": _sha("extra-ranking")},
        )(),
    )
    with pytest.raises(ValueError, match="extra or foreign"):
        build_main_phase_execution_release_v1(
            phase_authorization=authorization,
            execution_release=execution,
            arm_traces=(extra,),
            component_execution_release=component_release,
            assembled_at="2026-08-10T00:05:00+08:00",
        )

    first = execution.terminal_results[0].model_copy(
        update={"actual_llm_calls": 1}
    )
    drifted = execution.model_copy(
        update={"terminal_results": (first, *execution.terminal_results[1:])}
    )
    with pytest.raises(ValueError, match="legacy terminal LLM accounting"):
        build_main_phase_execution_release_v1(
            phase_authorization=authorization,
            execution_release=drifted,
            arm_traces=(),
            component_execution_release=component_release,
            assembled_at="2026-08-10T00:05:00+08:00",
        )


def test_source_release_ref_drift_is_rejected() -> None:
    authorization, execution, component_release = _all_failed_development_fusion()
    release = build_main_phase_execution_release_v1(
        phase_authorization=authorization,
        execution_release=execution,
        arm_traces=(),
        component_execution_release=component_release,
        assembled_at="2026-08-10T00:05:00+08:00",
    )
    cells = list(release.cells)
    cells[0] = cells[0].model_copy(
        update={"source_execution_release_id": "foreign-execution"}
    )
    with pytest.raises(ValueError, match="exact-join"):
        execution_bridge._build_addressed(
            MainPhaseExecutionReleaseV1,
            id_field="release_id",
            sha_field="release_sha256",
            prefix="main-phase-execution-v1",
            values={
                "phase_authorization": release.phase_authorization,
                "source_execution_phase": release.source_execution_phase,
                "execution_release": release.execution_release,
                "locked_primary_execution_preimage": None,
                "component_execution_release": release.component_execution_release,
                "full_fusion_components": release.full_fusion_components,
                "cells": tuple(cells),
                "assembled_at": release.assembled_at,
                "component_derivation_only": False,
                "analysis_or_gold_denominator_included": True,
            },
        )


def test_model_native_metadata_must_join_terminal_record_preimage() -> None:
    record = SimpleNamespace(
        record_receipt_id="record-1",
        record_receipt_sha256=_sha("record-1"),
        record_identity_sha256=_sha("identity-1"),
    )
    bundle = SimpleNamespace(
        receipt_bundle_id="bundle-1",
        receipt_bundle_sha256=_sha("bundle-1"),
        source_id="crossref",
        metadata_records=(record,),
    )
    terminal = SimpleNamespace(source_receipt_bundles=(bundle,))
    metadata_input = SimpleNamespace(
        receipt_bundle_id=bundle.receipt_bundle_id,
        receipt_bundle_sha256=bundle.receipt_bundle_sha256,
        source_id=bundle.source_id,
        record_receipt_id=record.record_receipt_id,
        record_receipt_sha256=record.record_receipt_sha256,
        record_identity_sha256=record.record_identity_sha256,
    )
    trace = SimpleNamespace(
        model_native_receipts=(
            SimpleNamespace(
                work_item=SimpleNamespace(metadata_inputs=(metadata_input,))
            ),
        )
    )
    execution_bridge._assert_model_inputs_are_terminal_records(trace, terminal)

    drifted = SimpleNamespace(
        **{
            **metadata_input.__dict__,
            "record_receipt_sha256": _sha("foreign-record"),
        }
    )
    tampered_trace = SimpleNamespace(
        model_native_receipts=(
            SimpleNamespace(work_item=SimpleNamespace(metadata_inputs=(drifted,))),
        )
    )
    with pytest.raises(ValueError, match="foreign terminal metadata record"):
        execution_bridge._assert_model_inputs_are_terminal_records(
            tampered_trace, terminal
        )
