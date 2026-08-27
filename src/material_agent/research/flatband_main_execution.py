"""Formal Main execution bridge for the flat/narrow-band study.

This module is a custody and replay contract, not a benchmark runner.  It joins
one Main phase authorization to one complete :class:`ExecutionReleaseV2`
preimage and exposes one uniform evidence row per authorized case/analysis
role.  Semantic calls are represented only by the visible model-native
receipts inside :class:`ArmExecutionTraceV1`; the legacy terminal LLM counters
must remain zero, and local semantic-model execution is forbidden.

Locked Fusion-minus releases embed the complete locked-primary execution
preimage.  That makes the full-Fusion configuration locally replayable instead
of reopening an opaque parent-reference seam.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import Annotated, Literal, TypeVar

from pydantic import Field, field_validator, model_validator

from material_agent.inspiration.models import (
    Identifier,
    Sha256,
    StrictModel,
    canonical_sha256,
    deterministic_id,
)
from material_agent.research.flatband_analysis_v2 import (
    AnalysisTraceEvidenceV2,
    AnalysisTraceRoleV2,
    build_analysis_trace_evidence_v2,
)
from material_agent.research.flatband_arm_runtime import (
    ArmExecutionScope,
    ArmExecutionTraceV1,
    assert_arm_execution_trace_exact,
)
from material_agent.research.flatband_contracts import BenchmarkSplit
from material_agent.research.flatband_execution import (
    BudgetManifestV1,
    ExecutionPhase,
    ExecutionReleaseV2,
    MissingPositionReason,
    ResearchSystemId,
    RunCellStatus,
    SystemConfigV1,
    TerminalRunResultV1,
    Top5ProjectionV1,
    assemble_execution_release_v2,
)
from material_agent.research.flatband_main import (
    MainPhaseAuthorizationReleaseV1,
    assert_main_phase_authorization_exact_v1,
)

ModelT = TypeVar("ModelT", bound=StrictModel)

_FORMAL_SOURCE_PHASES = frozenset(
    {
        ExecutionPhase.DEVELOPMENT_ABLATIONS,
        ExecutionPhase.DEVELOPMENT_FUSION,
        ExecutionPhase.LOCKED_FUSION_COMPONENTS,
        ExecutionPhase.LOCKED_PRIMARY,
        ExecutionPhase.LOCKED_FUSION_MINUS_E1,
        ExecutionPhase.LOCKED_FUSION_MINUS_E2,
        ExecutionPhase.LOCKED_FUSION_MINUS_E3,
    }
)
_MINUS_PHASES = frozenset(
    {
        ExecutionPhase.LOCKED_FUSION_MINUS_E1,
        ExecutionPhase.LOCKED_FUSION_MINUS_E2,
        ExecutionPhase.LOCKED_FUSION_MINUS_E3,
    }
)
_DIRECT_ROLE = {
    ResearchSystemId.B0: AnalysisTraceRoleV2.B0,
    ResearchSystemId.E1: AnalysisTraceRoleV2.E1,
    ResearchSystemId.E2_A: AnalysisTraceRoleV2.E2_A,
    ResearchSystemId.E2_B: AnalysisTraceRoleV2.E2_B,
    ResearchSystemId.E3: AnalysisTraceRoleV2.E3,
}
_MINUS_ROLE = {
    ExecutionPhase.LOCKED_FUSION_MINUS_E1: (
        AnalysisTraceRoleV2.FUSION_MINUS_E1,
        ResearchSystemId.E1,
    ),
    ExecutionPhase.LOCKED_FUSION_MINUS_E2: (
        AnalysisTraceRoleV2.FUSION_MINUS_E2,
        None,
    ),
    ExecutionPhase.LOCKED_FUSION_MINUS_E3: (
        AnalysisTraceRoleV2.FUSION_MINUS_E3,
        ResearchSystemId.E3,
    ),
}
def _timestamp(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("timestamp must include a UTC offset")
    return parsed


def _revalidate(value: ModelT, model_type: type[ModelT]) -> ModelT:
    return model_type.model_validate(
        value.model_dump(mode="python", round_trip=True)
    )


def _build_addressed(
    model_type: type[ModelT],
    *,
    id_field: str,
    sha_field: str,
    prefix: str,
    values: dict[str, object],
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


def _assert_addressed(
    value: StrictModel, *, id_field: str, sha_field: str, prefix: str
) -> None:
    digest = canonical_sha256(
        value.model_dump(mode="python", exclude={id_field, sha_field})
    )
    if getattr(value, sha_field) != digest or getattr(
        value, id_field
    ) != deterministic_id(prefix, {sha_field: digest}):
        raise ValueError("content-addressed Main execution identity does not replay")


def _assert_execution_release_exact(release: ExecutionReleaseV2) -> None:
    """Deep-replay V2 matrix -> budget -> receipt -> terminal -> Top-5 closure."""

    value = _revalidate(release, ExecutionReleaseV2)
    rebuilt = assemble_execution_release_v2(
        value.execution_matrix,
        value.budget_manifests,
        value.rankings,
        value.terminal_results,
        hypothesis_packets=value.hypothesis_packets,
        assembled_at=value.assembled_at,
    )
    if rebuilt != value:
        raise ValueError("ExecutionReleaseV2 differs from exact assembler replay")


class MainModelNativeUsageReplayV1(StrictModel):
    """Deterministic visible-usage projection; never a caller score.

    A parent Fusion cell stores three instances of this projection: direct
    calls charged to the parent run, inherited component calls charged to
    their own authorized component runs, and the union of calls that influenced
    the final result.  The union is provenance, not a second cost charge.
    """

    exact_provider_calls: Annotated[int, Field(ge=0, le=8)]
    exact_request_utf8_bytes: Annotated[int, Field(ge=0)]
    exact_response_utf8_bytes: Annotated[int, Field(ge=0)]
    unique_metadata_packets: Annotated[int, Field(ge=0, le=80)]
    token_usage_reported_calls: Annotated[int, Field(ge=0, le=8)]
    reported_input_tokens: Annotated[int, Field(ge=0, le=48_000)]
    reported_output_tokens: Annotated[int, Field(ge=0, le=64_000)]
    provider_attestation: Literal["UNAVAILABLE"] = "UNAVAILABLE"
    chain_of_thought_consumed: Literal[False] = False


def _model_usage(
    *traces: ArmExecutionTraceV1 | None,
) -> MainModelNativeUsageReplayV1:
    receipts = tuple(
        receipt
        for trace in traces
        if trace is not None
        for receipt in trace.model_native_receipts
    )
    receipt_keys = tuple(
        (item.receipt_id, item.receipt_sha256) for item in receipts
    )
    if (
        len(receipt_keys) != len(set(receipt_keys))
        or len({key[0] for key in receipt_keys}) != len(receipt_keys)
        or len({key[1] for key in receipt_keys}) != len(receipt_keys)
    ):
        raise ValueError("model-native receipt is reused or aliases another receipt")
    metadata: dict[str, str] = {}
    for receipt in receipts:
        for item in receipt.work_item.metadata_inputs:
            prior = metadata.setdefault(
                item.metadata_packet_id, item.metadata_packet_sha256
            )
            if prior != item.metadata_packet_sha256:
                raise ValueError("model-native metadata packet ID aliases two hashes")
    reported = tuple(
        item.token_usage for item in receipts if item.token_usage is not None
    )
    return MainModelNativeUsageReplayV1(
        exact_provider_calls=sum(item.exact_provider_call_count for item in receipts),
        exact_request_utf8_bytes=sum(
            item.exact_request_utf8_bytes for item in receipts
        ),
        exact_response_utf8_bytes=sum(
            item.exact_response_utf8_bytes for item in receipts
        ),
        unique_metadata_packets=len(metadata),
        token_usage_reported_calls=len(reported),
        reported_input_tokens=sum(item.input_tokens for item in reported),
        reported_output_tokens=sum(item.output_tokens for item in reported),
    )


def _assert_model_inputs_are_terminal_records(
    trace: ArmExecutionTraceV1 | None,
    terminal: TerminalRunResultV1,
) -> None:
    if trace is None:
        return
    bundle_by_id = {
        item.receipt_bundle_id: item for item in terminal.source_receipt_bundles
    }
    for receipt in trace.model_native_receipts:
        for item in receipt.work_item.metadata_inputs:
            bundle = bundle_by_id.get(item.receipt_bundle_id)
            if (
                bundle is None
                or bundle.receipt_bundle_sha256 != item.receipt_bundle_sha256
                or bundle.source_id != item.source_id
            ):
                raise ValueError(
                    "model-native input references a foreign terminal source bundle"
                )
            record_by_id = {
                record.record_receipt_id: record for record in bundle.metadata_records
            }
            record = record_by_id.get(item.record_receipt_id)
            if (
                record is None
                or record.record_receipt_sha256 != item.record_receipt_sha256
                or record.record_identity_sha256 != item.record_identity_sha256
            ):
                raise ValueError(
                    "model-native input references a foreign terminal metadata record"
                )


class MainPhaseExecutionCellEvidenceV1(StrictModel):
    """Uniform Analysis/Gold input for one exact Main case-role cell."""

    schema_version: Literal["flatband-main-phase-execution-cell-evidence-v1"] = (
        "flatband-main-phase-execution-cell-evidence-v1"
    )
    cell_evidence_id: Identifier
    cell_evidence_sha256: Sha256
    analysis_role: AnalysisTraceRoleV2
    source_execution_phase: ExecutionPhase
    source_execution_release_id: Identifier
    source_execution_release_sha256: Sha256
    split: BenchmarkSplit
    case_id: Identifier
    case_sha256: Sha256
    budget_manifest: BudgetManifestV1
    system_config: SystemConfigV1
    terminal_result: TerminalRunResultV1
    top5_projection: Top5ProjectionV1
    arm_trace: ArmExecutionTraceV1 | None = None
    fusion_component_traces: Annotated[
        tuple[ArmExecutionTraceV1, ...], Field(max_length=4)
    ] = ()
    direct_model_native_usage: MainModelNativeUsageReplayV1
    component_model_native_usage: MainModelNativeUsageReplayV1
    total_influence_model_native_usage: MainModelNativeUsageReplayV1
    legacy_terminal_llm_accounting_used: Literal[False] = False
    local_semantic_model_used: Literal[False] = False
    fixed_top5_denominator: Literal[5] = 5
    chain_of_thought_consumed: Literal[False] = False
    scientific_conclusion: Literal[False] = False

    @model_validator(mode="after")
    def validate_cell(self) -> MainPhaseExecutionCellEvidenceV1:
        budget = _revalidate(self.budget_manifest, BudgetManifestV1)
        config = _revalidate(self.system_config, SystemConfigV1)
        terminal = _revalidate(self.terminal_result, TerminalRunResultV1)
        projection = _revalidate(self.top5_projection, Top5ProjectionV1)
        trace = (
            None
            if self.arm_trace is None
            else _revalidate(self.arm_trace, ArmExecutionTraceV1)
        )
        components = tuple(
            _revalidate(item, ArmExecutionTraceV1)
            for item in self.fusion_component_traces
        )
        if config.system_id is ResearchSystemId.E1_LOCAL:
            raise ValueError("E1-local is forbidden from formal Main execution")
        if config.local_semantic_model is not None:
            raise ValueError("formal Main execution cannot freeze a local semantic model")
        if (
            terminal.llm_invocation_receipts
            or terminal.actual_llm_calls
            or terminal.actual_llm_input_tokens
            or terminal.actual_llm_output_tokens
            or terminal.actual_llm_metadata_packets
        ):
            raise ValueError(
                "legacy terminal LLM accounting must be zero; use model-native receipts"
            )
        if (
            terminal.local_model_invocation_receipts
            or terminal.actual_local_model_input_tokens
        ):
            raise ValueError("local semantic-model accounting is forbidden")
        if (budget.system_config, budget.case_id, budget.case_sha256) != (
            config,
            self.case_id,
            self.case_sha256,
        ):
            raise ValueError("cell config/case differs from its frozen budget")
        expected_terminal = (
            budget.budget_manifest_id,
            budget.budget_manifest_sha256,
            budget.cell_id,
            budget.run_id,
            self.case_id,
            self.case_sha256,
            config.config_id,
            config.config_sha256,
        )
        if (
            terminal.budget_manifest_id,
            terminal.budget_manifest_sha256,
            terminal.cell_id,
            terminal.run_id,
            terminal.case_id,
            terminal.case_sha256,
            terminal.system_config_id,
            terminal.system_config_sha256,
        ) != expected_terminal:
            raise ValueError("cell terminal differs from its budget/config/case")
        if (
            projection.cell_id,
            projection.terminal_result_id,
            projection.terminal_result_sha256,
            projection.ranking_id,
            projection.ranking_sha256,
            projection.status,
        ) != (
            terminal.cell_id,
            terminal.terminal_result_id,
            terminal.terminal_result_sha256,
            terminal.ranking_id,
            terminal.ranking_sha256,
            terminal.status,
        ):
            raise ValueError("cell Top-5 projection differs from its terminal")
        if terminal.status is RunCellStatus.FAILED:
            if trace is not None or components:
                raise ValueError("FAILED Main cell must not carry an Arm trace")
            if any(
                item.packet_id is not None
                or item.packet_sha256 is not None
                or not item.forced_zero
                or item.fixed_gain != 0
                or item.missing_reason is not MissingPositionReason.RUN_FAILED
                for item in projection.positions
            ):
                raise ValueError("FAILED Main cell must expose five RUN_FAILED zeroes")
        else:
            if trace is None:
                raise ValueError("non-failed Main cell requires an exact Arm trace")
            if (
                trace.system_config,
                trace.ranking.ranking_id,
                trace.ranking.ranking_sha256,
                trace.ranking.cell_id,
                trace.ranking.run_id,
                trace.ranking.case_id,
                trace.ranking.case_sha256,
            ) != (
                config,
                terminal.ranking_id,
                terminal.ranking_sha256,
                terminal.cell_id,
                terminal.run_id,
                terminal.case_id,
                terminal.case_sha256,
            ):
                raise ValueError("Arm trace differs from terminal ranking/cell")
            if config.system_id is ResearchSystemId.FUSION:
                assert_arm_execution_trace_exact(
                    trace, fusion_component_traces=components
                )
            else:
                if components:
                    raise ValueError("non-Fusion cell cannot carry component traces")
                assert_arm_execution_trace_exact(trace)
        expected_scope = (
            ArmExecutionScope.DEVELOPMENT
            if self.split is BenchmarkSplit.DEVELOPMENT
            else ArmExecutionScope.LOCKED
        )
        if trace is not None and trace.scope is not expected_scope:
            raise ValueError("Arm trace scope differs from Main split")
        if trace is not None:
            if _timestamp(trace.ranking.created_at) > _timestamp(
                terminal.completed_at
            ):
                raise ValueError("terminal completed before its Arm ranking")
            for receipt in trace.model_native_receipts:
                if _timestamp(receipt.started_at) < _timestamp(budget.frozen_at):
                    raise ValueError("model-native call predates the frozen budget")
                if _timestamp(receipt.completed_at) > _timestamp(
                    terminal.completed_at
                ):
                    raise ValueError("model-native call completed after the terminal")
        _assert_model_inputs_are_terminal_records(trace, terminal)
        if self.direct_model_native_usage != _model_usage(trace):
            raise ValueError("direct model-native usage must replay from parent Arm receipts")
        if self.component_model_native_usage != _model_usage(*components):
            raise ValueError("component model-native usage must replay from component Arms")
        if self.total_influence_model_native_usage != _model_usage(
            trace, *components
        ):
            raise ValueError("total model-native usage must include parent and components")
        _assert_addressed(
            self,
            id_field="cell_evidence_id",
            sha_field="cell_evidence_sha256",
            prefix="main-exec-cell-v1",
        )
        return self


class MainPhaseExecutionReleaseV1(StrictModel):
    """One formal Main source phase with complete execution and cell preimages."""

    schema_version: Literal["flatband-main-phase-execution-release-v1"] = (
        "flatband-main-phase-execution-release-v1"
    )
    release_id: Identifier
    release_sha256: Sha256
    phase_authorization: MainPhaseAuthorizationReleaseV1
    source_execution_phase: ExecutionPhase
    execution_release: ExecutionReleaseV2
    locked_primary_execution_preimage: ExecutionReleaseV2 | None = None
    component_execution_release: MainPhaseExecutionReleaseV1 | None = None
    full_fusion_components: Annotated[
        tuple[ResearchSystemId, ...], Field(max_length=3)
    ] = ()
    cells: Annotated[
        tuple[MainPhaseExecutionCellEvidenceV1, ...],
        Field(min_length=60, max_length=300),
    ]
    assembled_at: Annotated[str, Field(min_length=20, max_length=40)]
    component_derivation_only: bool
    analysis_or_gold_denominator_included: bool
    complete_case_role_omission_allowed: Literal[False] = False
    caller_supplied_role_allowed: Literal[False] = False
    caller_supplied_usage_allowed: Literal[False] = False
    e1_local_formal_alias_allowed: Literal[False] = False
    legacy_terminal_llm_path_allowed: Literal[False] = False
    local_semantic_model_allowed: Literal[False] = False
    chain_of_thought_consumed: Literal[False] = False
    scientific_conclusion: Literal[False] = False

    @field_validator("assembled_at")
    @classmethod
    def validate_time(cls, value: str) -> str:
        _timestamp(value)
        return value

    @model_validator(mode="after")
    def validate_release(self) -> MainPhaseExecutionReleaseV1:
        authorization = _revalidate(
            self.phase_authorization, MainPhaseAuthorizationReleaseV1
        )
        execution = _revalidate(self.execution_release, ExecutionReleaseV2)
        _assert_execution_release_exact(execution)
        component_release = (
            None
            if self.component_execution_release is None
            else _revalidate(
                self.component_execution_release, MainPhaseExecutionReleaseV1
            )
        )
        if self.source_execution_phase not in _FORMAL_SOURCE_PHASES:
            raise ValueError("source phase is not a formal Main analysis phase")
        if execution.execution_matrix.phase is not self.source_execution_phase:
            raise ValueError("source phase differs from execution matrix")
        is_component_phase = (
            self.source_execution_phase
            is ExecutionPhase.LOCKED_FUSION_COMPONENTS
        )
        if (
            self.component_derivation_only,
            self.analysis_or_gold_denominator_included,
        ) != (is_component_phase, not is_component_phase):
            raise ValueError(
                "component derivation/denominator flags differ from source phase"
            )
        if self.source_execution_phase in _MINUS_PHASES:
            if authorization.execution_phase is not ExecutionPhase.LOCKED_PRIMARY:
                raise ValueError("Fusion-minus requires locked-primary authorization")
            if self.locked_primary_execution_preimage is None:
                raise ValueError("Fusion-minus requires the full primary preimage")
            primary = _revalidate(
                self.locked_primary_execution_preimage, ExecutionReleaseV2
            )
            _assert_execution_release_exact(primary)
            if primary.execution_matrix.phase is not ExecutionPhase.LOCKED_PRIMARY:
                raise ValueError("Fusion-minus parent is not locked primary")
        else:
            if authorization.execution_phase is not self.source_execution_phase:
                raise ValueError("execution source phase differs from authorization")
            if self.locked_primary_execution_preimage is not None:
                raise ValueError("only Fusion-minus may embed a primary preimage")
        if execution.execution_matrix.split_manifest != (
            authorization.pre_budget_closure_release.frozen_case_release.split_manifest
        ):
            raise ValueError("execution matrix uses a foreign Main split")
        expected_components = _assert_phase_config(
            source_phase=self.source_execution_phase,
            execution=execution,
            primary=(
                None
                if self.locked_primary_execution_preimage is None
                else _revalidate(
                    self.locked_primary_execution_preimage, ExecutionReleaseV2
                )
            ),
        )
        if self.full_fusion_components != expected_components:
            raise ValueError("full Fusion components differ from exact config replay")
        if any(
            _timestamp(item.frozen_at) <= _timestamp(authorization.authorized_at)
            for item in execution.budget_manifests
        ):
            raise ValueError("execution budget predates Main phase authorization")
        expected_roles = _expected_roles_for_source_phase(
            self.source_execution_phase
        )
        expected_keys = {
            (case_id, role)
            for case_id in authorization.authorized_case_ids
            for role in expected_roles
        }
        observed_keys = tuple(
            (item.case_id, item.analysis_role.value) for item in self.cells
        )
        if observed_keys != tuple(sorted(set(observed_keys))):
            raise ValueError("Main execution cells must be case/role sorted and unique")
        if {(item.case_id, item.analysis_role) for item in self.cells} != expected_keys:
            raise ValueError("Main execution cells do not exactly cover case x role")
        for cell in self.cells:
            if (
                cell.source_execution_phase is not self.source_execution_phase
                or cell.source_execution_release_id != execution.release_id
                or cell.source_execution_release_sha256 != execution.release_sha256
            ):
                raise ValueError("cell does not exact-join its complete source release")
        budget_by_cell = {
            item.cell_id: item for item in execution.budget_manifests
        }
        terminal_by_cell = {
            item.cell_id: item for item in execution.terminal_results
        }
        projection_by_cell = {
            item.cell_id: item for item in execution.top5_projections
        }
        if len(self.cells) != len(execution.execution_matrix.cells):
            raise ValueError("unified cells do not exactly cover source matrix cells")
        for cell in self.cells:
            source_cell_id = cell.terminal_result.cell_id
            if (
                budget_by_cell.get(source_cell_id) != cell.budget_manifest
                or terminal_by_cell.get(source_cell_id) != cell.terminal_result
                or projection_by_cell.get(source_cell_id) != cell.top5_projection
                or _role_for_config(
                    self.source_execution_phase, cell.system_config
                )
                is not cell.analysis_role
            ):
                raise ValueError("unified cell differs from source execution preimage")
        trace_keys = tuple(
            (item.arm_trace.trace_id, item.arm_trace.trace_sha256)
            for item in self.cells
            if item.arm_trace is not None
        )
        if len(trace_keys) != len(set(trace_keys)):
            raise ValueError("one Arm trace is reused across Main execution cells")
        expected_component_traces = {
            (trace.trace_id, trace.trace_sha256): trace
            for cell in self.cells
            for trace in cell.fusion_component_traces
        }
        requires_components = self.source_execution_phase in {
            ExecutionPhase.DEVELOPMENT_FUSION,
            ExecutionPhase.LOCKED_PRIMARY,
            *_MINUS_PHASES,
        }
        if requires_components:
            if component_release is None:
                raise ValueError("Fusion phase requires a complete component execution")
            expected_component_phase = (
                ExecutionPhase.DEVELOPMENT_ABLATIONS
                if self.source_execution_phase is ExecutionPhase.DEVELOPMENT_FUSION
                else ExecutionPhase.LOCKED_FUSION_COMPONENTS
            )
            if component_release.source_execution_phase is not expected_component_phase:
                raise ValueError("Fusion uses a foreign component execution phase")
            if component_release.component_execution_release is not None:
                raise ValueError("component execution cannot recursively consume components")
            if component_release.phase_authorization.pre_budget_closure_release != (
                authorization.pre_budget_closure_release
            ):
                raise ValueError("Fusion and components use different Main custody roots")
            component_execution = component_release.execution_release
            if (
                component_execution.git_commit,
                component_execution.runtime_environment_sha256,
                component_execution.analysis_environment_sha256,
            ) != (
                execution.git_commit,
                execution.runtime_environment_sha256,
                execution.analysis_environment_sha256,
            ):
                raise ValueError("component execution changes frozen runtime identity")
            available_component_traces = {
                (cell.arm_trace.trace_id, cell.arm_trace.trace_sha256): cell.arm_trace
                for cell in component_release.cells
                if cell.arm_trace is not None
                and cell.system_config.system_id
                in {
                    ResearchSystemId.E1,
                    ResearchSystemId.E2_A,
                    ResearchSystemId.E2_B,
                    ResearchSystemId.E3,
                }
            }
            for key, trace in expected_component_traces.items():
                if available_component_traces.get(key) != trace:
                    raise ValueError(
                        "Fusion component trace lacks exact authorized cell evidence"
                    )
            if any(
                _timestamp(component_release.assembled_at)
                > _timestamp(cell.budget_manifest.frozen_at)
                for cell in self.cells
                if cell.system_config.system_id is ResearchSystemId.FUSION
            ):
                raise ValueError(
                    "Fusion budget was frozen before component execution sealed"
                )
        elif component_release is not None or expected_component_traces:
            raise ValueError("non-Fusion phase cannot consume a component execution")
        if _timestamp(self.assembled_at) < _timestamp(execution.assembled_at):
            raise ValueError("Main phase release predates its execution release")
        if any(
            _timestamp(trace.assembled_at) > _timestamp(self.assembled_at)
            for cell in self.cells
            for trace in (
                *((cell.arm_trace,) if cell.arm_trace is not None else ()),
                *cell.fusion_component_traces,
            )
        ):
            raise ValueError("Main phase release predates an Arm trace preimage")
        _assert_addressed(
            self,
            id_field="release_id",
            sha_field="release_sha256",
            prefix="main-phase-execution-v1",
        )
        return self


def _expected_roles_for_source_phase(
    phase: ExecutionPhase,
) -> tuple[AnalysisTraceRoleV2, ...]:
    if phase in {
        ExecutionPhase.DEVELOPMENT_ABLATIONS,
        ExecutionPhase.LOCKED_FUSION_COMPONENTS,
    }:
        return (
            *(
                (AnalysisTraceRoleV2.B0,)
                if phase is ExecutionPhase.DEVELOPMENT_ABLATIONS
                else ()
            ),
            AnalysisTraceRoleV2.E1,
            AnalysisTraceRoleV2.E2_A,
            AnalysisTraceRoleV2.E2_B,
            AnalysisTraceRoleV2.E3,
        )
    if phase in {
        ExecutionPhase.DEVELOPMENT_FUSION,
        ExecutionPhase.LOCKED_PRIMARY,
    }:
        return (AnalysisTraceRoleV2.B0, AnalysisTraceRoleV2.FUSION)
    if phase in _MINUS_PHASES:
        return (_MINUS_ROLE[phase][0],)
    raise ValueError("source phase is not a formal Main analysis phase")


def _role_for_config(
    phase: ExecutionPhase,
    config: SystemConfigV1,
) -> AnalysisTraceRoleV2:
    if phase in _MINUS_PHASES:
        if config.system_id is not ResearchSystemId.FUSION:
            raise ValueError("Fusion-minus phase contains a non-Fusion config")
        return _MINUS_ROLE[phase][0]
    if config.system_id is ResearchSystemId.FUSION:
        return AnalysisTraceRoleV2.FUSION
    try:
        return _DIRECT_ROLE[config.system_id]
    except KeyError as exc:
        raise ValueError("formal Main phase contains E1-local or unknown config") from exc


def _full_fusion_components(
    execution: ExecutionReleaseV2,
) -> tuple[ResearchSystemId, ...]:
    configs = tuple(
        item
        for item in execution.execution_matrix.system_configs
        if item.system_id is ResearchSystemId.FUSION
    )
    if len(configs) != 1:
        raise ValueError("Fusion phase must contain exactly one Fusion config")
    components = configs[0].fusion_components
    if (
        ResearchSystemId.E1 not in components
        or ResearchSystemId.E3 not in components
        or sum(
            item in {ResearchSystemId.E2_A, ResearchSystemId.E2_B}
            for item in components
        )
        != 1
        or len(components) != 3
    ):
        raise ValueError("full Fusion must freeze E1, one E2 variant, and E3")
    return components


def _assert_phase_config(
    *,
    source_phase: ExecutionPhase,
    execution: ExecutionReleaseV2,
    primary: ExecutionReleaseV2 | None,
) -> tuple[ResearchSystemId, ...]:
    if source_phase in {
        ExecutionPhase.DEVELOPMENT_ABLATIONS,
        ExecutionPhase.LOCKED_FUSION_COMPONENTS,
    }:
        return ()
    if source_phase in {
        ExecutionPhase.DEVELOPMENT_FUSION,
        ExecutionPhase.LOCKED_PRIMARY,
    }:
        return _full_fusion_components(execution)
    assert primary is not None
    full = _full_fusion_components(primary)
    if execution.execution_matrix.split_manifest != primary.execution_matrix.split_manifest:
        raise ValueError("Fusion-minus and primary use different Main splits")
    if (
        execution.git_commit,
        execution.runtime_environment_sha256,
        execution.analysis_environment_sha256,
    ) != (
        primary.git_commit,
        primary.runtime_environment_sha256,
        primary.analysis_environment_sha256,
    ):
        raise ValueError("Fusion-minus changes the locked runtime identity")
    fusion = tuple(
        item
        for item in execution.execution_matrix.system_configs
        if item.system_id is ResearchSystemId.FUSION
    )
    if len(fusion) != 1:
        raise ValueError("Fusion-minus must contain one Fusion config")
    removed = _MINUS_ROLE[source_phase][1]
    if source_phase is ExecutionPhase.LOCKED_FUSION_MINUS_E2:
        removed_set = {
            item
            for item in full
            if item in {ResearchSystemId.E2_A, ResearchSystemId.E2_B}
        }
    else:
        assert removed is not None
        removed_set = {removed}
    expected = tuple(item for item in full if item not in removed_set)
    if fusion[0].fusion_components != expected:
        raise ValueError("Fusion-minus config does not remove its named component")
    return full


def _trace_map(
    traces: Sequence[ArmExecutionTraceV1], *, label: str
) -> dict[tuple[str, str], ArmExecutionTraceV1]:
    values = tuple(_revalidate(item, ArmExecutionTraceV1) for item in traces)
    keys = tuple((item.trace_id, item.trace_sha256) for item in values)
    if (
        len(keys) != len(set(keys))
        or len({key[0] for key in keys}) != len(keys)
        or len({key[1] for key in keys}) != len(keys)
    ):
        raise ValueError(f"{label} trace identities are repeated or aliased")
    ranking_keys = tuple(
        (item.ranking.ranking_id, item.ranking.ranking_sha256) for item in values
    )
    if len(ranking_keys) != len(set(ranking_keys)):
        raise ValueError(f"{label} repeats one ranking")
    return dict(zip(ranking_keys, values))


def _component_map(
    traces: Sequence[ArmExecutionTraceV1],
) -> dict[tuple[str, str], ArmExecutionTraceV1]:
    values = tuple(_revalidate(item, ArmExecutionTraceV1) for item in traces)
    keys = tuple((item.trace_id, item.trace_sha256) for item in values)
    if (
        len(keys) != len(set(keys))
        or len({key[0] for key in keys}) != len(keys)
        or len({key[1] for key in keys}) != len(keys)
    ):
        raise ValueError("Fusion component trace identities are repeated")
    return dict(zip(keys, values))


def _build_cell(
    *,
    source_phase: ExecutionPhase,
    execution: ExecutionReleaseV2,
    budget: BudgetManifestV1,
    terminal: TerminalRunResultV1,
    projection: Top5ProjectionV1,
    trace: ArmExecutionTraceV1 | None,
    components: tuple[ArmExecutionTraceV1, ...],
) -> MainPhaseExecutionCellEvidenceV1:
    config = budget.system_config
    return _build_addressed(
        MainPhaseExecutionCellEvidenceV1,
        id_field="cell_evidence_id",
        sha_field="cell_evidence_sha256",
        prefix="main-exec-cell-v1",
        values={
            "analysis_role": _role_for_config(source_phase, config),
            "source_execution_phase": source_phase,
            "source_execution_release_id": execution.release_id,
            "source_execution_release_sha256": execution.release_sha256,
            "split": next(
                item.split
                for item in execution.execution_matrix.cells
                if item.cell_id == budget.cell_id
            ),
            "case_id": budget.case_id,
            "case_sha256": budget.case_sha256,
            "budget_manifest": budget,
            "system_config": config,
            "terminal_result": terminal,
            "top5_projection": projection,
            "arm_trace": trace,
            "fusion_component_traces": components,
            "direct_model_native_usage": _model_usage(trace),
            "component_model_native_usage": _model_usage(*components),
            "total_influence_model_native_usage": _model_usage(
                trace, *components
            ),
        },
    )


def build_main_phase_execution_release_v1(
    *,
    phase_authorization: MainPhaseAuthorizationReleaseV1,
    execution_release: ExecutionReleaseV2,
    arm_traces: Sequence[ArmExecutionTraceV1],
    component_execution_release: MainPhaseExecutionReleaseV1 | None = None,
    locked_primary_execution_preimage: ExecutionReleaseV2 | None = None,
    assembled_at: str,
) -> MainPhaseExecutionReleaseV1:
    """Build one phase bridge; roles, cells, usage and Fusion-minus are derived."""

    authorization = _revalidate(
        phase_authorization, MainPhaseAuthorizationReleaseV1
    )
    assert_main_phase_authorization_exact_v1(authorization)
    execution = _revalidate(execution_release, ExecutionReleaseV2)
    _assert_execution_release_exact(execution)
    source_phase = execution.execution_matrix.phase
    if source_phase not in _FORMAL_SOURCE_PHASES:
        raise ValueError("execution is not a formal Main analysis phase")
    primary = (
        None
        if locked_primary_execution_preimage is None
        else _revalidate(
            locked_primary_execution_preimage, ExecutionReleaseV2
        )
    )
    if primary is not None:
        _assert_execution_release_exact(primary)
    full_components = _assert_phase_config(
        source_phase=source_phase,
        execution=execution,
        primary=primary,
    )
    if source_phase in _MINUS_PHASES:
        if authorization.execution_phase is not ExecutionPhase.LOCKED_PRIMARY:
            raise ValueError("Fusion-minus requires locked-primary authorization")
        if primary is None:
            raise ValueError("Fusion-minus requires locked-primary execution preimage")
    elif authorization.execution_phase is not source_phase or primary is not None:
        raise ValueError("authorization/source phase or primary-preimage shape differs")
    if execution.execution_matrix.split_manifest != (
        authorization.pre_budget_closure_release.frozen_case_release.split_manifest
    ):
        raise ValueError("execution uses a foreign Main split")
    if any(
        _timestamp(item.frozen_at) <= _timestamp(authorization.authorized_at)
        for item in execution.budget_manifests
    ):
        raise ValueError("execution budget predates Main phase authorization")

    component_release = (
        None
        if component_execution_release is None
        else _revalidate(
            component_execution_release, MainPhaseExecutionReleaseV1
        )
    )
    requires_components = source_phase in {
        ExecutionPhase.DEVELOPMENT_FUSION,
        ExecutionPhase.LOCKED_PRIMARY,
        *_MINUS_PHASES,
    }
    if requires_components != (component_release is not None):
        raise ValueError(
            "Fusion phases require exactly one complete component execution release"
        )
    if component_release is not None:
        expected_component_phase = (
            ExecutionPhase.DEVELOPMENT_ABLATIONS
            if source_phase is ExecutionPhase.DEVELOPMENT_FUSION
            else ExecutionPhase.LOCKED_FUSION_COMPONENTS
        )
        if component_release.source_execution_phase is not expected_component_phase:
            raise ValueError("Fusion uses a foreign component execution phase")
        component_by_identity = _component_map(
            tuple(
                item.arm_trace
                for item in component_release.cells
                if item.arm_trace is not None
                and item.system_config.system_id
                in {
                    ResearchSystemId.E1,
                    ResearchSystemId.E2_A,
                    ResearchSystemId.E2_B,
                    ResearchSystemId.E3,
                }
            )
        )
    else:
        component_by_identity = {}
    trace_by_ranking = _trace_map(arm_traces, label="selected")
    used_trace_keys: set[tuple[str, str]] = set()
    terminal_by_cell = {item.cell_id: item for item in execution.terminal_results}
    projection_by_cell = {item.cell_id: item for item in execution.top5_projections}
    cells: list[MainPhaseExecutionCellEvidenceV1] = []
    for budget in execution.budget_manifests:
        terminal = terminal_by_cell[budget.cell_id]
        projection = projection_by_cell[budget.cell_id]
        if terminal.ranking_id is None:
            trace = None
            components: tuple[ArmExecutionTraceV1, ...] = ()
        else:
            ranking_key = (terminal.ranking_id, terminal.ranking_sha256)
            trace = trace_by_ranking.get(ranking_key)
            if trace is None:
                raise ValueError("non-failed execution cell omits its Arm trace")
            used_trace_keys.add(ranking_key)
            if trace.system_config.system_id is ResearchSystemId.FUSION:
                refs = tuple(
                    (item.trace_id, item.trace_sha256)
                    for item in trace.fusion_component_trace_refs
                )
                try:
                    components = tuple(component_by_identity[key] for key in refs)
                except KeyError as exc:
                    raise ValueError("Fusion trace omits a full component preimage") from exc
            else:
                components = ()
        cells.append(
            _build_cell(
                source_phase=source_phase,
                execution=execution,
                budget=budget,
                terminal=terminal,
                projection=projection,
                trace=trace,
                components=components,
            )
        )
    if used_trace_keys != set(trace_by_ranking):
        raise ValueError("caller supplied an extra or foreign selected Arm trace")
    cells_tuple = tuple(
        sorted(cells, key=lambda item: (item.case_id, item.analysis_role.value))
    )
    return _build_addressed(
        MainPhaseExecutionReleaseV1,
        id_field="release_id",
        sha_field="release_sha256",
        prefix="main-phase-execution-v1",
        values={
            "phase_authorization": authorization,
            "source_execution_phase": source_phase,
            "execution_release": execution,
            "locked_primary_execution_preimage": primary,
            "component_execution_release": component_release,
            "full_fusion_components": full_components,
            "cells": cells_tuple,
            "assembled_at": assembled_at,
            "component_derivation_only": (
                source_phase is ExecutionPhase.LOCKED_FUSION_COMPONENTS
            ),
            "analysis_or_gold_denominator_included": (
                source_phase is not ExecutionPhase.LOCKED_FUSION_COMPONENTS
            ),
        },
    )


def assert_main_phase_execution_exact_v1(
    release: MainPhaseExecutionReleaseV1,
) -> None:
    """Replay the complete execution preimage and every unified evidence cell."""

    value = _revalidate(release, MainPhaseExecutionReleaseV1)
    rebuilt = build_main_phase_execution_release_v1(
        phase_authorization=value.phase_authorization,
        execution_release=value.execution_release,
        arm_traces=tuple(
            item.arm_trace for item in value.cells if item.arm_trace is not None
        ),
        component_execution_release=value.component_execution_release,
        locked_primary_execution_preimage=(
            value.locked_primary_execution_preimage
        ),
        assembled_at=value.assembled_at,
    )
    if rebuilt != value:
        raise ValueError("Main phase execution differs from canonical replay")


def build_analysis_trace_evidence_index_from_main_v1(
    release: MainPhaseExecutionReleaseV1,
) -> tuple[AnalysisTraceEvidenceV2, ...]:
    """Return Analysis V2 inputs only after exact Main execution replay."""

    value = _revalidate(release, MainPhaseExecutionReleaseV1)
    assert_main_phase_execution_exact_v1(value)
    if value.source_execution_phase is ExecutionPhase.LOCKED_FUSION_COMPONENTS:
        raise ValueError(
            "locked component derivations never enter Analysis/Gold denominators"
        )
    return tuple(
        build_analysis_trace_evidence_v2(
            role=item.analysis_role,
            system_config=item.system_config,
            terminal_result=item.terminal_result,
            top5_projection=item.top5_projection,
            trace=item.arm_trace,
            fusion_component_traces=item.fusion_component_traces,
        )
        for item in value.cells
    )


def main_gold_execution_cells_from_main_v1(
    release: MainPhaseExecutionReleaseV1,
) -> tuple[MainPhaseExecutionCellEvidenceV1, ...]:
    """Return the canonical Gold inputs, including FAILED fixed-zero cells."""

    value = _revalidate(release, MainPhaseExecutionReleaseV1)
    assert_main_phase_execution_exact_v1(value)
    if not value.analysis_or_gold_denominator_included:
        raise ValueError(
            "locked component derivations never enter Analysis/Gold denominators"
        )
    return value.cells


__all__ = [
    "MainModelNativeUsageReplayV1",
    "MainPhaseExecutionCellEvidenceV1",
    "MainPhaseExecutionReleaseV1",
    "assert_main_phase_execution_exact_v1",
    "build_analysis_trace_evidence_index_from_main_v1",
    "build_main_phase_execution_release_v1",
    "main_gold_execution_cells_from_main_v1",
]
