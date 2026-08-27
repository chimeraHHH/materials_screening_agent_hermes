"""Native, replayable Analysis V2 for the flat/narrow-band workflow.

This module is deliberately an evaluator, not a semantic model.  It consumes
complete :class:`ArmExecutionTraceV1` artifacts plus content-addressed final
Gold projections and derives every case metric, aggregate, and lifecycle
attestation deterministically.  No caller supplied score, effect, p-value, or
chain-of-thought field is accepted.

The Gold projection is the smallest temporary seam needed before a native Main
Gold release exists.  Its source and source-verifier references remain explicit;
successful replay here proves exact evaluation of the supplied projection, not
the external authenticity of the opaque upstream Gold artifacts.
"""

from __future__ import annotations

import math
import random
from collections import defaultdict
from collections.abc import Mapping, Sequence
from datetime import datetime
from enum import StrEnum
from typing import Annotated, Literal, TypeVar

from pydantic import Field, field_validator, model_validator

from material_agent.inspiration.models import (
    Identifier,
    Sha256,
    StrictModel,
    canonical_sha256,
    deterministic_id,
)
from material_agent.research.flatband_arm_runtime import (
    ArmExecutionScope,
    ArmExecutionTraceV1,
    assert_arm_execution_trace_exact,
)
from material_agent.research.flatband_contracts import BenchmarkSplit
from material_agent.research.flatband_execution import (
    ExecutionPhase,
    MissingPositionReason,
    ResearchSystemId,
    RunCellStatus,
    SystemConfigV1,
    TerminalRunResultV1,
    Top5ProjectionV1,
)
from material_agent.research.flatband_lifecycle import (
    LOCKED_SECONDARY_FAMILY,
    DevelopmentMetricRowV1,
    FormalVerifierAttestationRefV1,
    LifecycleArtifactType,
    LockedPrimaryResultV1,
    LockedSecondaryHypothesis,
    LockedSecondaryResultV1,
    SecondaryApplicability,
    TypedArtifactRefV1,
    VerifiedPayloadKind,
    build_development_metric_row_v1,
    build_locked_primary_result_v1,
    typed_artifact_ref_v1,
)
from material_agent.research.flatband_metrics import (
    GainKind,
    absolute_ndcg_at_5,
    duplicate_rate_at_5,
    evidence_valid_at_5,
    success_at_5,
)

ModelT = TypeVar("ModelT", bound=StrictModel)

DEVELOPMENT_CASE_COUNT = 60
LOCKED_CASE_COUNT = 60
LOCKED_IID_CASE_COUNT = 30
LOCKED_OOD_CASE_COUNT = 30
BOOTSTRAP_REPLICATES = 50_000
BOOTSTRAP_SEED = 20_260_809
RANDOMIZATION_REPLICATES = 100_000
RANDOMIZATION_SEED = 20_260_809
MAX_EXACT_SIGN_ASSIGNMENTS = 100_000


def _timestamp(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise ValueError("timestamp must be RFC3339-compatible") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("timestamp must include a UTC offset")
    return parsed


def _round(value: float) -> float:
    return round(float(value), 12)


def _revalidate(value: ModelT, model_type: type[ModelT]) -> ModelT:
    return model_type.model_validate(value.model_dump(mode="python", round_trip=True))


def _assert_addressed(
    value: StrictModel, *, id_field: str, sha_field: str, prefix: str
) -> None:
    semantic = value.model_dump(mode="python", exclude={id_field, sha_field})
    digest = canonical_sha256(semantic)
    if getattr(value, sha_field) != digest:
        raise ValueError(f"{sha_field} does not match semantic content")
    if getattr(value, id_field) != deterministic_id(prefix, {sha_field: digest}):
        raise ValueError(f"{id_field} does not match semantic content")


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


class AnalysisArtifactTypeV2(StrEnum):
    SPLIT_CONTEXT = "SPLIT_CONTEXT"
    LEAKAGE_CONTEXT = "LEAKAGE_CONTEXT"
    GOLD_RELEASE = "GOLD_RELEASE"
    GOLD_FORMAL_VERIFIER = "GOLD_FORMAL_VERIFIER"


class AnalysisArtifactRefV2(StrictModel):
    artifact_type: AnalysisArtifactTypeV2
    artifact_id: Identifier
    artifact_sha256: Sha256


class AnalysisTraceRoleV2(StrEnum):
    B0 = "B0"
    E1 = "E1"
    E2_A = "E2_A"
    E2_B = "E2_B"
    E3 = "E3"
    FUSION = "FUSION"
    FUSION_MINUS_E1 = "FUSION_MINUS_E1"
    FUSION_MINUS_E2 = "FUSION_MINUS_E2"
    FUSION_MINUS_E3 = "FUSION_MINUS_E3"


_DIRECT_ROLE_SYSTEM = {
    AnalysisTraceRoleV2.B0: ResearchSystemId.B0,
    AnalysisTraceRoleV2.E1: ResearchSystemId.E1,
    AnalysisTraceRoleV2.E2_A: ResearchSystemId.E2_A,
    AnalysisTraceRoleV2.E2_B: ResearchSystemId.E2_B,
    AnalysisTraceRoleV2.E3: ResearchSystemId.E3,
    AnalysisTraceRoleV2.FUSION: ResearchSystemId.FUSION,
    AnalysisTraceRoleV2.FUSION_MINUS_E1: ResearchSystemId.FUSION,
    AnalysisTraceRoleV2.FUSION_MINUS_E2: ResearchSystemId.FUSION,
    AnalysisTraceRoleV2.FUSION_MINUS_E3: ResearchSystemId.FUSION,
}

_DEVELOPMENT_ABLATION_ROLES = (
    AnalysisTraceRoleV2.B0,
    AnalysisTraceRoleV2.E1,
    AnalysisTraceRoleV2.E2_A,
    AnalysisTraceRoleV2.E2_B,
    AnalysisTraceRoleV2.E3,
)
_DEVELOPMENT_FUSION_ROLES = (
    AnalysisTraceRoleV2.B0,
    AnalysisTraceRoleV2.FUSION,
)


class AnalysisCaseBindingV2(StrictModel):
    case_id: Identifier
    case_sha256: Sha256
    split: BenchmarkSplit
    leakage_component_id: Identifier


class AnalysisTraceEvidenceV2(StrictModel):
    """Full trace preimage used by the evaluator, including Fusion components."""

    evidence_id: Identifier
    evidence_sha256: Sha256
    role: AnalysisTraceRoleV2
    system_config: SystemConfigV1
    terminal_result: TerminalRunResultV1
    top5_projection: Top5ProjectionV1
    trace: ArmExecutionTraceV1 | None = None
    fusion_component_traces: Annotated[
        tuple[ArmExecutionTraceV1, ...], Field(max_length=4)
    ] = ()
    exact_arm_runtime_replay_required: Literal[True] = True
    formal_terminal_result_exact_replay_required: Literal[True] = True
    chain_of_thought_consumed: Literal[False] = False

    @model_validator(mode="after")
    def validate_evidence(self) -> AnalysisTraceEvidenceV2:
        config = _revalidate(self.system_config, SystemConfigV1)
        terminal = _revalidate(self.terminal_result, TerminalRunResultV1)
        projection = _revalidate(self.top5_projection, Top5ProjectionV1)
        trace = (
            None
            if self.trace is None
            else _revalidate(self.trace, ArmExecutionTraceV1)
        )
        components = tuple(
            _revalidate(item, ArmExecutionTraceV1)
            for item in self.fusion_component_traces
        )
        if config.system_id is not _DIRECT_ROLE_SYSTEM[self.role]:
            raise ValueError("analysis trace role relabels a different system")
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
            raise ValueError("Top-5 projection binds a foreign terminal result")
        if (
            terminal.system_config_id,
            terminal.system_config_sha256,
        ) != (config.config_id, config.config_sha256):
            raise ValueError("terminal result binds a foreign system configuration")
        if terminal.status is RunCellStatus.FAILED:
            if trace is not None or components:
                raise ValueError("FAILED analysis cell cannot carry a ranking trace")
            if any(
                (
                    item.packet_id is not None
                    or item.packet_sha256 is not None
                    or not item.forced_zero
                    or item.fixed_gain != 0
                    or item.missing_reason is not MissingPositionReason.RUN_FAILED
                )
                for item in projection.positions
            ):
                raise ValueError("FAILED analysis cell requires five RUN_FAILED zeroes")
            returned = ()
        else:
            if trace is None:
                raise ValueError("non-failed analysis cell requires an arm trace")
            if (
                terminal.ranking_id,
                terminal.ranking_sha256,
                terminal.cell_id,
                terminal.run_id,
                terminal.case_id,
                terminal.case_sha256,
                terminal.system_config_id,
                terminal.system_config_sha256,
            ) != (
                trace.ranking.ranking_id,
                trace.ranking.ranking_sha256,
                trace.ranking.cell_id,
                trace.ranking.run_id,
                trace.ranking.case_id,
                trace.ranking.case_sha256,
                trace.system_config.config_id,
                trace.system_config.config_sha256,
            ):
                raise ValueError("arm trace binds a foreign terminal cell/ranking")
            if trace.system_config != config:
                raise ValueError("arm trace differs from frozen system configuration")
            if config.system_id is ResearchSystemId.FUSION:
                assert_arm_execution_trace_exact(
                    trace, fusion_component_traces=components
                )
            else:
                if components:
                    raise ValueError("non-Fusion analysis trace cannot carry components")
                assert_arm_execution_trace_exact(trace)
            returned = trace.ranking.positions
        for position, projected in enumerate(projection.positions, start=1):
            if position <= len(returned):
                ranked = returned[position - 1]
                if (
                    projected.packet_id,
                    projected.packet_sha256,
                    projected.forced_zero,
                    projected.missing_reason,
                ) != (ranked.packet_id, ranked.packet_sha256, False, None):
                    raise ValueError("Top-5 present position differs from trace ranking")
            else:
                expected_reason = (
                    MissingPositionReason.RUN_FAILED
                    if terminal.status is RunCellStatus.FAILED
                    else MissingPositionReason.EMPTY_RANKING
                    if not returned
                    else MissingPositionReason.UNDERFILL
                )
                if (
                    projected.packet_id is not None
                    or projected.packet_sha256 is not None
                    or not projected.forced_zero
                    or projected.fixed_gain != 0
                    or projected.missing_reason is not expected_reason
                ):
                    raise ValueError(
                        "Top-5 missing position does not replay from terminal status"
                    )
        _assert_addressed(
            self,
            id_field="evidence_id",
            sha_field="evidence_sha256",
            prefix="analysis-trace-evidence-v2",
        )
        return self


class FinalJudgmentStatusV2(StrEnum):
    ASSESSABLE = "ASSESSABLE"
    SYSTEM_PACKET_INVALID = "SYSTEM_PACKET_INVALID"
    CASE_INVALID = "CASE_INVALID"
    UNRESOLVABLE = "UNRESOLVABLE"


class FinalPositionJudgmentV2(StrictModel):
    judgment_id: Identifier
    judgment_sha256: Sha256
    position: Annotated[int, Field(ge=1, le=5)]
    packet_id: Identifier | None = None
    packet_sha256: Sha256 | None = None
    status: FinalJudgmentStatusV2
    relevance_grade: Annotated[int, Field(ge=0, le=3)]
    evidence_valid: bool
    final_duplicate_cluster_id: Identifier
    missing_reason: MissingPositionReason | None = None
    fixed_position_denominator_included: Literal[True] = True
    raw_annotation_or_cot_included: Literal[False] = False

    @model_validator(mode="after")
    def validate_judgment(self) -> FinalPositionJudgmentV2:
        present = self.packet_id is not None or self.packet_sha256 is not None
        if (self.packet_id is None) != (self.packet_sha256 is None):
            raise ValueError("position packet ID and SHA must be present together")
        if not present:
            if (
                self.missing_reason is None
                or self.status is not FinalJudgmentStatusV2.SYSTEM_PACKET_INVALID
                or self.relevance_grade != 0
                or self.evidence_valid
            ):
                raise ValueError("missing position must be one explicit fixed zero")
        elif self.missing_reason is not None:
            raise ValueError("present position cannot carry a missing reason")
        if self.status is not FinalJudgmentStatusV2.ASSESSABLE and (
            self.relevance_grade != 0 or self.evidence_valid
        ):
            raise ValueError("non-assessable final judgment must contribute zero")
        if self.relevance_grade >= 2 and not self.evidence_valid:
            raise ValueError("grade two or three requires evidence-valid Gold")
        _assert_addressed(
            self,
            id_field="judgment_id",
            sha_field="judgment_sha256",
            prefix="final-position-judgment-v2",
        )
        return self


class AnalysisCellEvidenceV2(StrictModel):
    """One exact trace and the complete final judgment projection for its ranking."""

    cell_evidence_id: Identifier
    cell_evidence_sha256: Sha256
    case_id: Identifier
    case_sha256: Sha256
    role: AnalysisTraceRoleV2
    trace_evidence: AnalysisTraceEvidenceV2
    gold_release_ref: AnalysisArtifactRefV2
    gold_formal_verifier_ref: AnalysisArtifactRefV2
    judgments: Annotated[
        tuple[FinalPositionJudgmentV2, ...], Field(min_length=5, max_length=5)
    ]
    caller_supplied_scores_allowed: Literal[False] = False
    upstream_gold_exact_replay_required: Literal[True] = True

    @model_validator(mode="after")
    def validate_cell(self) -> AnalysisCellEvidenceV2:
        trace_evidence = _revalidate(
            self.trace_evidence, AnalysisTraceEvidenceV2
        )
        if self.gold_release_ref.artifact_type is not AnalysisArtifactTypeV2.GOLD_RELEASE:
            raise ValueError("cell Gold reference has the wrong artifact type")
        if (
            self.gold_formal_verifier_ref.artifact_type
            is not AnalysisArtifactTypeV2.GOLD_FORMAL_VERIFIER
        ):
            raise ValueError("cell Gold verifier reference has the wrong artifact type")
        if (
            self.case_id,
            self.case_sha256,
            self.role,
        ) != (
            trace_evidence.terminal_result.case_id,
            trace_evidence.terminal_result.case_sha256,
            trace_evidence.role,
        ):
            raise ValueError("analysis cell binds a foreign case or role")
        projection = trace_evidence.top5_projection
        judgments = tuple(
            _revalidate(item, FinalPositionJudgmentV2) for item in self.judgments
        )
        if tuple(item.position for item in judgments) != (1, 2, 3, 4, 5):
            raise ValueError("Gold judgments must cover the fixed five positions")
        for index, (judgment, projected) in enumerate(
            zip(judgments, projection.positions), start=1
        ):
            if projected.packet_id is not None:
                if (judgment.packet_id, judgment.packet_sha256) != (
                    projected.packet_id,
                    projected.packet_sha256,
                ):
                    raise ValueError("Gold judgment packet differs from Top-5")
            else:
                reason = projected.missing_reason
                assert reason is not None
                expected_cluster = deterministic_id(
                    "missing-position-cluster-v2",
                    {
                        "terminal_result_sha256": (
                            trace_evidence.terminal_result.terminal_result_sha256
                        ),
                        "position": index,
                        "missing_reason": reason.value,
                    },
                )
                if (
                    judgment.packet_id is not None
                    or judgment.packet_sha256 is not None
                    or judgment.missing_reason is not reason
                    or judgment.final_duplicate_cluster_id != expected_cluster
                ):
                    raise ValueError("missing Gold position does not replay canonically")
        present_judgments = tuple(
            item for item in judgments if item.packet_id is not None
        )
        if any(
            item.status is FinalJudgmentStatusV2.CASE_INVALID
            for item in present_judgments
        ) and not all(
            item.status is FinalJudgmentStatusV2.CASE_INVALID
            for item in present_judgments
        ):
            raise ValueError("CASE_INVALID must cover every returned position in a cell")
        _assert_addressed(
            self,
            id_field="cell_evidence_id",
            sha_field="cell_evidence_sha256",
            prefix="analysis-cell-evidence-v2",
        )
        return self


class CaseArmMetricRowV2(StrictModel):
    row_id: Identifier
    row_sha256: Sha256
    cell_evidence_id: Identifier
    cell_evidence_sha256: Sha256
    case_id: Identifier
    case_sha256: Sha256
    split: BenchmarkSplit
    leakage_component_id: Identifier
    role: AnalysisTraceRoleV2
    case_analysis_included: bool
    andcg_at_5: Annotated[float | None, Field(ge=0.0, le=1.0, allow_inf_nan=False)]
    evidence_valid_at_5: Annotated[
        float | None, Field(ge=0.0, le=1.0, allow_inf_nan=False)
    ]
    duplicate_rate_at_5: Annotated[
        float | None, Field(ge=0.0, le=1.0, allow_inf_nan=False)
    ]
    success_at_5: Literal[0, 1] | None
    unresolvable_position_count: Annotated[int, Field(ge=0, le=5)]
    scores_derived_from_final_positions: Literal[True] = True
    caller_supplied_scores_allowed: Literal[False] = False

    @model_validator(mode="after")
    def validate_row(self) -> CaseArmMetricRowV2:
        metrics = (
            self.andcg_at_5,
            self.evidence_valid_at_5,
            self.duplicate_rate_at_5,
            self.success_at_5,
        )
        if self.case_analysis_included != all(item is not None for item in metrics):
            raise ValueError("case inclusion differs from metric presence")
        _assert_addressed(
            self,
            id_field="row_id",
            sha_field="row_sha256",
            prefix="case-arm-metric-row-v2",
        )
        return self


class AnalysisInputReleaseV2(StrictModel):
    """Complete deterministic Analysis V2 input and case-metric projection."""

    schema_version: Literal["flatband-analysis-input-release-v2"] = (
        "flatband-analysis-input-release-v2"
    )
    release_id: Identifier
    release_sha256: Sha256
    execution_phase: Literal[
        ExecutionPhase.DEVELOPMENT_ABLATIONS,
        ExecutionPhase.DEVELOPMENT_FUSION,
        ExecutionPhase.LOCKED_PRIMARY,
    ]
    split_context_ref: AnalysisArtifactRefV2
    leakage_context_ref: AnalysisArtifactRefV2
    cases: Annotated[
        tuple[AnalysisCaseBindingV2, ...], Field(min_length=60, max_length=60)
    ]
    cells: Annotated[
        tuple[AnalysisCellEvidenceV2, ...], Field(min_length=120, max_length=540)
    ]
    metric_rows: Annotated[
        tuple[CaseArmMetricRowV2, ...], Field(min_length=120, max_length=540)
    ]
    roles: Annotated[
        tuple[AnalysisTraceRoleV2, ...], Field(min_length=2, max_length=9)
    ]
    full_fusion_components: Annotated[
        tuple[ResearchSystemId, ...], Field(max_length=3)
    ] = ()
    assembled_at: Annotated[str, Field(min_length=20, max_length=40)]
    caller_supplied_case_metrics_allowed: Literal[False] = False
    local_semantic_model_used_for_analysis: Literal[False] = False
    chain_of_thought_consumed: Literal[False] = False
    upstream_gold_authenticity_requires_external_replay: Literal[True] = True
    scientific_conclusion: Literal[False] = False

    @field_validator("assembled_at")
    @classmethod
    def validate_time(cls, value: str) -> str:
        _timestamp(value)
        return value

    @model_validator(mode="after")
    def validate_release(self) -> AnalysisInputReleaseV2:
        if self.split_context_ref.artifact_type is not AnalysisArtifactTypeV2.SPLIT_CONTEXT:
            raise ValueError("analysis split reference has the wrong type")
        if self.leakage_context_ref.artifact_type is not AnalysisArtifactTypeV2.LEAKAGE_CONTEXT:
            raise ValueError("analysis leakage reference has the wrong type")
        case_keys = tuple(item.case_id for item in self.cases)
        if case_keys != tuple(sorted(set(case_keys))):
            raise ValueError("Analysis V2 cases must be sorted and unique")
        if len({item.case_sha256 for item in self.cases}) != len(self.cases):
            raise ValueError("Analysis V2 case IDs and hashes must be one-to-one")
        expected_splits = (
            {BenchmarkSplit.DEVELOPMENT: DEVELOPMENT_CASE_COUNT}
            if self.execution_phase is not ExecutionPhase.LOCKED_PRIMARY
            else {
                BenchmarkSplit.LOCKED_IID: LOCKED_IID_CASE_COUNT,
                BenchmarkSplit.LOCKED_OOD: LOCKED_OOD_CASE_COUNT,
            }
        )
        observed_splits = {
            split: sum(item.split is split for item in self.cases)
            for split in BenchmarkSplit
        }
        if {key: value for key, value in observed_splits.items() if value} != expected_splits:
            raise ValueError("Analysis V2 cases differ from the frozen phase split")
        if self.roles != _expected_roles(self.execution_phase, self.full_fusion_components):
            raise ValueError("Analysis V2 roles differ from the frozen phase design")
        expected_keys = {(case.case_id, role) for case in self.cases for role in self.roles}
        cell_keys = tuple((item.case_id, item.role.value) for item in self.cells)
        if cell_keys != tuple(sorted(set(cell_keys))):
            raise ValueError("Analysis V2 cells must be case/role sorted and unique")
        if {(item.case_id, item.role) for item in self.cells} != expected_keys:
            raise ValueError("Analysis V2 cells do not exactly cover case x role")
        for label, identities in (
            (
                "cell evidence",
                tuple(
                    (item.cell_evidence_id, item.cell_evidence_sha256)
                    for item in self.cells
                ),
            ),
            (
                "arm trace",
                tuple(
                    (
                        item.trace_evidence.trace.trace_id,
                        item.trace_evidence.trace.trace_sha256,
                    )
                    for item in self.cells
                    if item.trace_evidence.trace is not None
                ),
            ),
            (
                "Top-5 projection",
                tuple(
                    (
                        item.trace_evidence.top5_projection.projection_id,
                        item.trace_evidence.top5_projection.projection_sha256,
                    )
                    for item in self.cells
                ),
            ),
            (
                "execution cell",
                tuple(
                    (
                        item.trace_evidence.terminal_result.cell_id,
                        item.trace_evidence.terminal_result.terminal_result_id,
                    )
                    for item in self.cells
                ),
            ),
        ):
            if len(set(identities)) != len(identities):
                raise ValueError(f"Analysis V2 repeats a {label} identity")
            if len({item[0] for item in identities}) != len(
                {item[1] for item in identities}
            ):
                raise ValueError(f"Analysis V2 {label} IDs and hashes are not one-to-one")
        judgments = tuple(item for cell in self.cells for item in cell.judgments)
        judgment_pairs = tuple(
            (item.judgment_id, item.judgment_sha256) for item in judgments
        )
        if len(set(judgment_pairs)) != len(judgment_pairs) or len(
            {item[0] for item in judgment_pairs}
        ) != len({item[1] for item in judgment_pairs}):
            raise ValueError("Analysis V2 Gold judgment identities are not one-to-one")
        row_keys = tuple((item.case_id, item.role.value) for item in self.metric_rows)
        if row_keys != tuple(sorted(set(row_keys))):
            raise ValueError("Analysis V2 metric rows must be case/role sorted and unique")
        if {(item.case_id, item.role) for item in self.metric_rows} != expected_keys:
            raise ValueError("Analysis V2 rows do not exactly cover case x role")
        case_by_id = {item.case_id: item for item in self.cases}
        cell_by_key = {(item.case_id, item.role): item for item in self.cells}
        gold_refs = {
            (item.gold_release_ref.artifact_id, item.gold_release_ref.artifact_sha256)
            for item in self.cells
        }
        gold_verifiers = {
            (
                item.gold_formal_verifier_ref.artifact_id,
                item.gold_formal_verifier_ref.artifact_sha256,
            )
            for item in self.cells
        }
        if len(gold_refs) != 1 or len(gold_verifiers) != 1:
            raise ValueError("Analysis V2 must use one Gold release and verifier")
        configs_by_role: dict[AnalysisTraceRoleV2, set[tuple[str, str]]] = defaultdict(set)
        for cell in self.cells:
            case = case_by_id[cell.case_id]
            trace = cell.trace_evidence.trace
            if cell.case_sha256 != case.case_sha256 or (
                trace is not None
                and trace.scope is not _expected_scope(self.execution_phase)
            ):
                raise ValueError("analysis cell drifts from case or phase scope")
            config = cell.trace_evidence.system_config
            configs_by_role[cell.role].add((config.config_id, config.config_sha256))
        if any(len(values) != 1 for values in configs_by_role.values()):
            raise ValueError("one analysis role uses multiple system configurations")
        _assert_fusion_role_configs(self)
        expected_rows = tuple(
            _derive_case_metric(case_by_id[item.case_id], item)
            for item in self.cells
        )
        if self.metric_rows != expected_rows:
            raise ValueError("Analysis V2 metric rows do not replay from traces and Gold")
        for case in self.cases:
            cells = tuple(cell_by_key[(case.case_id, role)] for role in self.roles)
            invalid = tuple(
                any(j.status is FinalJudgmentStatusV2.CASE_INVALID for j in cell.judgments)
                for cell in cells
            )
            if any(invalid) and not all(invalid):
                raise ValueError("CASE_INVALID must be symmetric across every case arm")
        _assert_addressed(
            self,
            id_field="release_id",
            sha_field="release_sha256",
            prefix="analysis-input-release-v2",
        )
        return self


def _expected_scope(phase: ExecutionPhase) -> ArmExecutionScope:
    return (
        ArmExecutionScope.LOCKED
        if phase is ExecutionPhase.LOCKED_PRIMARY
        else ArmExecutionScope.DEVELOPMENT
    )


def _expected_roles(
    phase: ExecutionPhase,
    full_components: tuple[ResearchSystemId, ...],
) -> tuple[AnalysisTraceRoleV2, ...]:
    if phase is ExecutionPhase.DEVELOPMENT_ABLATIONS:
        if full_components:
            raise ValueError("development ablations cannot declare Fusion components")
        return _DEVELOPMENT_ABLATION_ROLES
    if phase is ExecutionPhase.DEVELOPMENT_FUSION:
        if not full_components:
            raise ValueError("development Fusion analysis requires frozen components")
        return _DEVELOPMENT_FUSION_ROLES
    if phase is not ExecutionPhase.LOCKED_PRIMARY:
        raise ValueError("Analysis V2 phase is not registered")
    if not full_components:
        raise ValueError("locked analysis requires full Fusion components")
    roles = [AnalysisTraceRoleV2.B0, AnalysisTraceRoleV2.FUSION]
    if ResearchSystemId.E1 in full_components:
        roles.append(AnalysisTraceRoleV2.FUSION_MINUS_E1)
    if any(item in full_components for item in (ResearchSystemId.E2_A, ResearchSystemId.E2_B)):
        roles.append(AnalysisTraceRoleV2.FUSION_MINUS_E2)
    if ResearchSystemId.E3 in full_components:
        roles.append(AnalysisTraceRoleV2.FUSION_MINUS_E3)
    return tuple(roles)


def _assert_fusion_role_configs(release: AnalysisInputReleaseV2) -> None:
    by_role = {item.role: item.trace_evidence.system_config for item in release.cells}
    if AnalysisTraceRoleV2.FUSION not in by_role:
        return
    full = by_role[AnalysisTraceRoleV2.FUSION]
    if full.fusion_components != release.full_fusion_components:
        raise ValueError("full Fusion trace differs from frozen components")
    removals = {
        AnalysisTraceRoleV2.FUSION_MINUS_E1: {ResearchSystemId.E1},
        AnalysisTraceRoleV2.FUSION_MINUS_E2: {
            item
            for item in full.fusion_components
            if item in {ResearchSystemId.E2_A, ResearchSystemId.E2_B}
        },
        AnalysisTraceRoleV2.FUSION_MINUS_E3: {ResearchSystemId.E3},
    }
    for role, removed in removals.items():
        config = by_role.get(role)
        if config is None:
            continue
        expected = tuple(item for item in full.fusion_components if item not in removed)
        if not expected:
            raise ValueError("an applicable Fusion-minus arm cannot have zero components")
        if config.fusion_components != expected:
            raise ValueError("Fusion-minus config does not remove exactly one intervention")


def _derive_case_metric(
    case: AnalysisCaseBindingV2, cell: AnalysisCellEvidenceV2
) -> CaseArmMetricRowV2:
    invalid = any(
        item.status is FinalJudgmentStatusV2.CASE_INVALID for item in cell.judgments
    )
    grades = tuple(item.relevance_grade for item in cell.judgments)
    evidence = tuple(item.evidence_valid for item in cell.judgments)
    clusters = tuple(item.final_duplicate_cluster_id for item in cell.judgments)
    returned_clusters = tuple(
        item.final_duplicate_cluster_id
        for item in cell.judgments
        if item.packet_id is not None
    )
    values: dict[str, object] = {
        "cell_evidence_id": cell.cell_evidence_id,
        "cell_evidence_sha256": cell.cell_evidence_sha256,
        "case_id": case.case_id,
        "case_sha256": case.case_sha256,
        "split": case.split,
        "leakage_component_id": case.leakage_component_id,
        "role": cell.role,
        "case_analysis_included": not invalid,
        "andcg_at_5": None if invalid else absolute_ndcg_at_5(
            grades=grades,
            strict_hypothesis_cluster_ids=clusters,
            gain_kind=GainKind.LINEAR_PRIMARY,
        ),
        "evidence_valid_at_5": None if invalid else evidence_valid_at_5(evidence),
        "duplicate_rate_at_5": (
            None if invalid else duplicate_rate_at_5(returned_clusters)
        ),
        "success_at_5": None if invalid else success_at_5(
            grades=grades, strict_hypothesis_cluster_ids=clusters
        ),
        "unresolvable_position_count": sum(
            item.status is FinalJudgmentStatusV2.UNRESOLVABLE
            for item in cell.judgments
        ),
    }
    return _build_addressed(
        CaseArmMetricRowV2,
        id_field="row_id",
        sha_field="row_sha256",
        prefix="case-arm-metric-row-v2",
        values=values,
    )


def build_analysis_trace_evidence_v2(
    *,
    role: AnalysisTraceRoleV2,
    system_config: SystemConfigV1,
    terminal_result: TerminalRunResultV1,
    top5_projection: Top5ProjectionV1,
    trace: ArmExecutionTraceV1 | None = None,
    fusion_component_traces: Sequence[ArmExecutionTraceV1] = (),
) -> AnalysisTraceEvidenceV2:
    return _build_addressed(
        AnalysisTraceEvidenceV2,
        id_field="evidence_id",
        sha_field="evidence_sha256",
        prefix="analysis-trace-evidence-v2",
        values={
            "role": role,
            "system_config": _revalidate(system_config, SystemConfigV1),
            "terminal_result": _revalidate(
                terminal_result, TerminalRunResultV1
            ),
            "top5_projection": _revalidate(top5_projection, Top5ProjectionV1),
            "trace": (
                None if trace is None else _revalidate(trace, ArmExecutionTraceV1)
            ),
            "fusion_component_traces": tuple(fusion_component_traces),
        },
    )


def build_final_position_judgment_v2(
    *,
    position: int,
    packet_id: str | None,
    packet_sha256: str | None,
    status: FinalJudgmentStatusV2,
    relevance_grade: int,
    evidence_valid: bool,
    final_duplicate_cluster_id: str,
    missing_reason: MissingPositionReason | None = None,
) -> FinalPositionJudgmentV2:
    return _build_addressed(
        FinalPositionJudgmentV2,
        id_field="judgment_id",
        sha_field="judgment_sha256",
        prefix="final-position-judgment-v2",
        values={
            "position": position,
            "packet_id": packet_id,
            "packet_sha256": packet_sha256,
            "status": status,
            "relevance_grade": relevance_grade,
            "evidence_valid": evidence_valid,
            "final_duplicate_cluster_id": final_duplicate_cluster_id,
            "missing_reason": missing_reason,
        },
    )


def build_analysis_cell_evidence_v2(
    *,
    trace_evidence: AnalysisTraceEvidenceV2,
    gold_release_ref: AnalysisArtifactRefV2,
    gold_formal_verifier_ref: AnalysisArtifactRefV2,
    judgments: Sequence[FinalPositionJudgmentV2],
) -> AnalysisCellEvidenceV2:
    evidence = _revalidate(trace_evidence, AnalysisTraceEvidenceV2)
    present = tuple(sorted(judgments, key=lambda item: item.position))
    expected_present_positions = tuple(
        item.position
        for item in evidence.top5_projection.positions
        if item.packet_id is not None
    )
    if tuple(item.position for item in present) != expected_present_positions:
        raise ValueError("caller judgments must exactly cover returned positions")
    missing = tuple(
        build_final_position_judgment_v2(
            position=position,
            packet_id=None,
            packet_sha256=None,
            status=FinalJudgmentStatusV2.SYSTEM_PACKET_INVALID,
            relevance_grade=0,
            evidence_valid=False,
            final_duplicate_cluster_id=deterministic_id(
                "missing-position-cluster-v2",
                {
                    "terminal_result_sha256": (
                        evidence.terminal_result.terminal_result_sha256
                    ),
                    "position": position,
                    "missing_reason": evidence.top5_projection.positions[
                        position - 1
                    ].missing_reason.value,
                },
            ),
            missing_reason=evidence.top5_projection.positions[
                position - 1
            ].missing_reason,
        )
        for position in range(1, 6)
        if position not in expected_present_positions
    )
    ordered = (*present, *missing)
    return _build_addressed(
        AnalysisCellEvidenceV2,
        id_field="cell_evidence_id",
        sha_field="cell_evidence_sha256",
        prefix="analysis-cell-evidence-v2",
        values={
            "case_id": evidence.terminal_result.case_id,
            "case_sha256": evidence.terminal_result.case_sha256,
            "role": evidence.role,
            "trace_evidence": evidence,
            "gold_release_ref": gold_release_ref,
            "gold_formal_verifier_ref": gold_formal_verifier_ref,
            "judgments": ordered,
        },
    )


def build_analysis_input_release_v2(
    *,
    execution_phase: ExecutionPhase,
    split_context_ref: AnalysisArtifactRefV2,
    leakage_context_ref: AnalysisArtifactRefV2,
    cases: Sequence[AnalysisCaseBindingV2],
    cells: Sequence[AnalysisCellEvidenceV2],
    assembled_at: str,
) -> AnalysisInputReleaseV2:
    """Build a formal evaluator input; case/system scores are never parameters."""

    case_values = tuple(sorted(cases, key=lambda item: item.case_id))
    cell_values = tuple(sorted(cells, key=lambda item: (item.case_id, item.role.value)))
    full_configs = {
        item.trace_evidence.system_config.config_sha256:
        item.trace_evidence.system_config
        for item in cell_values
        if item.role is AnalysisTraceRoleV2.FUSION
    }
    if execution_phase in {
        ExecutionPhase.DEVELOPMENT_FUSION,
        ExecutionPhase.LOCKED_PRIMARY,
    }:
        if len(full_configs) != 1:
            raise ValueError("analysis requires one frozen full Fusion configuration")
        full_components = next(iter(full_configs.values())).fusion_components
    else:
        full_components = ()
    roles = _expected_roles(execution_phase, full_components)
    case_by_id = {item.case_id: item for item in case_values}
    rows = tuple(_derive_case_metric(case_by_id[item.case_id], item) for item in cell_values)
    return _build_addressed(
        AnalysisInputReleaseV2,
        id_field="release_id",
        sha_field="release_sha256",
        prefix="analysis-input-release-v2",
        values={
            "execution_phase": execution_phase,
            "split_context_ref": split_context_ref,
            "leakage_context_ref": leakage_context_ref,
            "cases": case_values,
            "cells": cell_values,
            "metric_rows": rows,
            "roles": roles,
            "full_fusion_components": full_components,
            "assembled_at": assembled_at,
        },
    )


def analysis_input_ref_v2(release: AnalysisInputReleaseV2) -> TypedArtifactRefV1:
    value = _revalidate(release, AnalysisInputReleaseV2)
    return typed_artifact_ref_v1(
        LifecycleArtifactType.ANALYSIS_INPUT_V2,
        value.release_id,
        value.release_sha256,
    )


def _mean(values: Sequence[float | int]) -> float:
    if not values:
        raise ValueError("metric aggregate has an empty valid denominator")
    return _round(sum(float(item) for item in values) / len(values))


def derive_development_metric_rows_v2(
    release: AnalysisInputReleaseV2,
) -> tuple[DevelopmentMetricRowV1, ...]:
    """Derive lifecycle-compatible schema rows from exact case metrics."""

    value = _revalidate(release, AnalysisInputReleaseV2)
    if value.execution_phase is ExecutionPhase.DEVELOPMENT_ABLATIONS:
        roles = _DEVELOPMENT_ABLATION_ROLES
    elif value.execution_phase is ExecutionPhase.DEVELOPMENT_FUSION:
        roles = _DEVELOPMENT_FUSION_ROLES
    else:
        raise ValueError("development rows require a development AnalysisInputV2")
    ref = analysis_input_ref_v2(value)
    case_universe_sha = canonical_sha256(value.cases)
    result: list[DevelopmentMetricRowV1] = []
    for role in roles:
        rows = tuple(item for item in value.metric_rows if item.role is role)
        included = tuple(item for item in rows if item.case_analysis_included)
        role_cells = tuple(item for item in value.cells if item.role is role)
        config = next(
            item.trace_evidence.system_config
            for item in value.cells
            if item.role is role
        )
        system_id = _DIRECT_ROLE_SYSTEM[role]
        result.append(
            build_development_metric_row_v1(
                analysis_input_ref=ref,
                system_id=system_id,
                case_universe_sha256=case_universe_sha,
                andcg_at_5=_mean([item.andcg_at_5 for item in included]),
                evidence_valid_at_5=_mean(
                    [item.evidence_valid_at_5 for item in included]
                ),
                duplicate_rate_at_5=_mean(
                    [item.duplicate_rate_at_5 for item in included]
                ),
                success_at_5=_mean([item.success_at_5 for item in included]),
                physical_request_budget_per_case=sum(
                    item.max_physical_requests for item in config.source_budgets
                ),
                article_body_access_count=sum(
                    item.trace_evidence.terminal_result.article_body_fetch_requests
                    for item in role_cells
                ),
                pdf_access_count=sum(
                    item.trace_evidence.terminal_result.full_pdf_reads
                    for item in role_cells
                ),
                identity_closure_violation_count=0,
                denominator_omission_count=len(rows) - len(included),
            )
        )
    return tuple(result)


def _paired_rows(
    release: AnalysisInputReleaseV2,
    baseline_role: AnalysisTraceRoleV2,
    treatment_role: AnalysisTraceRoleV2,
    *,
    splits: frozenset[BenchmarkSplit],
    metric_field: str = "andcg_at_5",
) -> tuple[tuple[str, str, BenchmarkSplit, float], ...]:
    by_key = {(item.case_id, item.role): item for item in release.metric_rows}
    result: list[tuple[str, str, BenchmarkSplit, float]] = []
    for case in release.cases:
        if case.split not in splits:
            continue
        baseline = by_key[(case.case_id, baseline_role)]
        treatment = by_key[(case.case_id, treatment_role)]
        if baseline.case_analysis_included != treatment.case_analysis_included:
            raise ValueError("paired locked arms differ in case inclusion")
        if not baseline.case_analysis_included:
            continue
        baseline_value = getattr(baseline, metric_field)
        treatment_value = getattr(treatment, metric_field)
        if baseline_value is None or treatment_value is None:
            raise ValueError("included paired locked row is missing its metric")
        result.append(
            (
                case.case_id,
                case.leakage_component_id,
                case.split,
                _round(float(treatment_value) - float(baseline_value)),
            )
        )
    return tuple(result)


def _equal_strata_delta(
    rows: Sequence[tuple[str, str, BenchmarkSplit, float]],
) -> float:
    values = tuple(rows)
    means = []
    for split in (BenchmarkSplit.LOCKED_IID, BenchmarkSplit.LOCKED_OOD):
        split_values = [item[3] for item in values if item[2] is split]
        means.append(_mean(split_values))
    return _round(0.5 * means[0] + 0.5 * means[1])


def _single_split_delta(
    rows: Sequence[tuple[str, str, BenchmarkSplit, float]],
) -> float:
    return _mean([item[3] for item in rows])


def _grouped(
    rows: Sequence[tuple[str, str, BenchmarkSplit, float]],
) -> dict[BenchmarkSplit, dict[str, tuple[float, ...]]]:
    staged: dict[BenchmarkSplit, dict[str, list[float]]] = defaultdict(
        lambda: defaultdict(list)
    )
    component_split: dict[str, BenchmarkSplit] = {}
    for _case_id, component, split, difference in rows:
        prior = component_split.setdefault(component, split)
        if prior is not split:
            raise ValueError("one leakage component crosses locked strata")
        staged[split][component].append(difference)
    return {
        split: {component: tuple(values) for component, values in sorted(groups.items())}
        for split, groups in staged.items()
    }


def _bootstrap_primary(
    rows: Sequence[tuple[str, str, BenchmarkSplit, float]],
) -> tuple[float, float]:
    grouped = _grouped(rows)
    if any(not grouped.get(split) for split in (BenchmarkSplit.LOCKED_IID, BenchmarkSplit.LOCKED_OOD)):
        raise ValueError("locked bootstrap requires valid IID and OOD components")
    # Constant component effects have an exact degenerate bootstrap interval.
    all_values = [value for groups in grouped.values() for values in groups.values() for value in values]
    if len({_round(item) for item in all_values}) == 1:
        value = _round(all_values[0])
        return value, value
    rng = random.Random(BOOTSTRAP_SEED)
    draws: list[float] = []
    for _ in range(BOOTSTRAP_REPLICATES):
        means: list[float] = []
        for split in (BenchmarkSplit.LOCKED_IID, BenchmarkSplit.LOCKED_OOD):
            groups = grouped[split]
            group_ids = tuple(groups)
            sampled = [rng.choice(group_ids) for _ in group_ids]
            differences = [
                rng.choice(groups[group_id])
                for group_id in sampled
                for _ in groups[group_id]
            ]
            means.append(sum(differences) / len(differences))
        draws.append(0.5 * means[0] + 0.5 * means[1])
    draws.sort()
    return _round(_percentile(draws, 0.025)), _round(_percentile(draws, 0.975))


def _percentile(values: Sequence[float], quantile: float) -> float:
    position = (len(values) - 1) * quantile
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return float(values[lower])
    weight = position - lower
    return float(values[lower] * (1.0 - weight) + values[upper] * weight)


def _randomization_p(
    rows: Sequence[tuple[str, str, BenchmarkSplit, float]],
    *,
    equal_strata: bool,
) -> float:
    values = tuple(rows)
    grouped = _grouped(values)
    group_keys = tuple(
        (split, group_id)
        for split in sorted(grouped, key=lambda item: item.value)
        for group_id in grouped[split]
    )
    if not group_keys:
        raise ValueError("randomization test has no leakage components")
    observed = _equal_strata_delta(values) if equal_strata else _single_split_delta(values)

    # With one case per component and one identical positive effect, only the
    # all-positive assignment reaches the observed statistic.  This is an
    # exact closed form, not a performance-changing approximation.
    if (
        all(len(group_values) == 1 for groups in grouped.values() for group_values in groups.values())
        and len({_round(item[3]) for item in values}) == 1
        and observed > 0.0
    ):
        return _round(1.0 / (2 ** len(group_keys)))
    if all(item[3] == 0.0 for item in values):
        return 1.0

    def statistic(signs: Sequence[int]) -> float:
        sign_by_key = dict(zip(group_keys, signs))
        signed = tuple(
            (case_id, component, split, difference * sign_by_key[(split, component)])
            for case_id, component, split, difference in values
        )
        return _equal_strata_delta(signed) if equal_strata else _single_split_delta(signed)

    assignment_count = 2 ** len(group_keys)
    extreme = 0
    if assignment_count <= MAX_EXACT_SIGN_ASSIGNMENTS:
        total = assignment_count
        for mask in range(total):
            signs = tuple(1 if mask & (1 << index) else -1 for index in range(len(group_keys)))
            extreme += statistic(signs) >= observed - 1e-15
        return _round(extreme / total)
    rng = random.Random(RANDOMIZATION_SEED)
    for _ in range(RANDOMIZATION_REPLICATES):
        signs = tuple(1 if rng.getrandbits(1) else -1 for _ in group_keys)
        extreme += statistic(signs) >= observed - 1e-15
    return _round((extreme + 1) / (RANDOMIZATION_REPLICATES + 1))


def _holm_adjusted(
    raw_p: Mapping[LockedSecondaryHypothesis, float],
) -> dict[LockedSecondaryHypothesis, float]:
    ordered = sorted(raw_p, key=lambda item: (raw_p[item], item.value))
    adjusted: dict[LockedSecondaryHypothesis, float] = {}
    running = 0.0
    size = len(LOCKED_SECONDARY_FAMILY)
    for rank, hypothesis in enumerate(ordered, start=1):
        running = max(running, min(1.0, (size - rank + 1) * raw_p[hypothesis]))
        adjusted[hypothesis] = _round(running)
    return adjusted


def derive_locked_result_family_v2(
    release: AnalysisInputReleaseV2,
) -> tuple[LockedPrimaryResultV1, tuple[LockedSecondaryResultV1, ...]]:
    """Derive the complete fixed locked family from actual trace/config roles."""

    value = _revalidate(release, AnalysisInputReleaseV2)
    if value.execution_phase is not ExecutionPhase.LOCKED_PRIMARY:
        raise ValueError("locked result family requires locked AnalysisInputV2")
    primary_rows = _paired_rows(
        value,
        AnalysisTraceRoleV2.B0,
        AnalysisTraceRoleV2.FUSION,
        splits=frozenset({BenchmarkSplit.LOCKED_IID, BenchmarkSplit.LOCKED_OOD}),
    )
    lower, upper = _bootstrap_primary(primary_rows)
    invalid_cases = sum(
        not next(
            item.case_analysis_included
            for item in value.metric_rows
            if item.case_id == case.case_id and item.role is AnalysisTraceRoleV2.B0
        )
        for case in value.cases
    )
    primary_cells = tuple(
        item
        for item in value.metric_rows
        if item.role in {AnalysisTraceRoleV2.B0, AnalysisTraceRoleV2.FUSION}
    )
    unresolvable_rate = _round(
        sum(item.unresolvable_position_count for item in primary_cells)
        / sum(len(cell.judgments) for cell in value.cells if cell.role in {AnalysisTraceRoleV2.B0, AnalysisTraceRoleV2.FUSION})
    )
    primary = build_locked_primary_result_v1(
        equal_iid_ood_andcg_delta=_equal_strata_delta(primary_rows),
        bootstrap_lower=lower,
        bootstrap_upper=upper,
        one_sided_randomization_p=_randomization_p(primary_rows, equal_strata=True),
        success_delta=_equal_strata_delta(
            _paired_rows(
                value,
                AnalysisTraceRoleV2.B0,
                AnalysisTraceRoleV2.FUSION,
                splits=frozenset(
                    {BenchmarkSplit.LOCKED_IID, BenchmarkSplit.LOCKED_OOD}
                ),
                metric_field="success_at_5",
            )
        ),
        evidence_valid_delta=_equal_strata_delta(
            _paired_rows(
                value,
                AnalysisTraceRoleV2.B0,
                AnalysisTraceRoleV2.FUSION,
                splits=frozenset(
                    {BenchmarkSplit.LOCKED_IID, BenchmarkSplit.LOCKED_OOD}
                ),
                metric_field="evidence_valid_at_5",
            )
        ),
        duplicate_rate_delta=_equal_strata_delta(
            _paired_rows(
                value,
                AnalysisTraceRoleV2.B0,
                AnalysisTraceRoleV2.FUSION,
                splits=frozenset(
                    {BenchmarkSplit.LOCKED_IID, BenchmarkSplit.LOCKED_OOD}
                ),
                metric_field="duplicate_rate_at_5",
            )
        ),
        invalid_case_rate=_round(invalid_cases / LOCKED_CASE_COUNT),
        unresolvable_unit_rate=unresolvable_rate,
    )
    comparison_roles: dict[
        LockedSecondaryHypothesis,
        tuple[AnalysisTraceRoleV2, AnalysisTraceRoleV2, frozenset[BenchmarkSplit]] | None,
    ] = {
        LockedSecondaryHypothesis.FUSION_VS_B0_IID: (
            AnalysisTraceRoleV2.B0,
            AnalysisTraceRoleV2.FUSION,
            frozenset({BenchmarkSplit.LOCKED_IID}),
        ),
        LockedSecondaryHypothesis.FUSION_VS_B0_OOD: (
            AnalysisTraceRoleV2.B0,
            AnalysisTraceRoleV2.FUSION,
            frozenset({BenchmarkSplit.LOCKED_OOD}),
        ),
        LockedSecondaryHypothesis.FUSION_VS_FUSION_MINUS_E1: (
            AnalysisTraceRoleV2.FUSION_MINUS_E1,
            AnalysisTraceRoleV2.FUSION,
            frozenset({BenchmarkSplit.LOCKED_IID, BenchmarkSplit.LOCKED_OOD}),
        ) if ResearchSystemId.E1 in value.full_fusion_components else None,
        LockedSecondaryHypothesis.FUSION_VS_FUSION_MINUS_E2: (
            AnalysisTraceRoleV2.FUSION_MINUS_E2,
            AnalysisTraceRoleV2.FUSION,
            frozenset({BenchmarkSplit.LOCKED_IID, BenchmarkSplit.LOCKED_OOD}),
        ) if any(item in value.full_fusion_components for item in (ResearchSystemId.E2_A, ResearchSystemId.E2_B)) else None,
        LockedSecondaryHypothesis.FUSION_VS_FUSION_MINUS_E3: (
            AnalysisTraceRoleV2.FUSION_MINUS_E3,
            AnalysisTraceRoleV2.FUSION,
            frozenset({BenchmarkSplit.LOCKED_IID, BenchmarkSplit.LOCKED_OOD}),
        ) if ResearchSystemId.E3 in value.full_fusion_components else None,
    }
    effects: dict[LockedSecondaryHypothesis, float | None] = {}
    raw_p: dict[LockedSecondaryHypothesis, float] = {}
    for hypothesis in LOCKED_SECONDARY_FAMILY:
        comparison = comparison_roles[hypothesis]
        if comparison is None:
            effects[hypothesis] = None
            raw_p[hypothesis] = 1.0
            continue
        baseline_role, treatment_role, splits = comparison
        rows = _paired_rows(value, baseline_role, treatment_role, splits=splits)
        equal = len(splits) == 2
        effects[hypothesis] = (
            _equal_strata_delta(rows) if equal else _single_split_delta(rows)
        )
        raw_p[hypothesis] = _randomization_p(rows, equal_strata=equal)
    adjusted = _holm_adjusted(raw_p)
    secondary = tuple(
        LockedSecondaryResultV1(
            hypothesis=hypothesis,
            applicability=(
                SecondaryApplicability.NOT_APPLICABLE
                if comparison_roles[hypothesis] is None
                else SecondaryApplicability.APPLICABLE
            ),
            andcg_delta=effects[hypothesis],
            raw_one_sided_p=raw_p[hypothesis],
            holm_adjusted_p=adjusted[hypothesis],
            component_promoted=(
                None
                if hypothesis in {
                    LockedSecondaryHypothesis.FUSION_VS_B0_IID,
                    LockedSecondaryHypothesis.FUSION_VS_B0_OOD,
                }
                else comparison_roles[hypothesis] is not None
            ),
            not_applicable_uses_conservative_p_one=(
                comparison_roles[hypothesis] is None
            ),
        )
        for hypothesis in LOCKED_SECONDARY_FAMILY
    )
    return primary, secondary


ANALYSIS_V2_FORMAL_VERIFIER_REF = typed_artifact_ref_v1(
    LifecycleArtifactType.ANALYSIS_INPUT_V2_FORMAL_VERIFIER,
    "flatband-analysis-v2-deterministic-verifier",
    canonical_sha256(
        {
            "schema": "flatband-analysis-input-release-v2",
            "policy": "exact-arm-trace-gold-position-replay-no-caller-scores-v1",
            "bootstrap_replicates": BOOTSTRAP_REPLICATES,
            "bootstrap_seed": BOOTSTRAP_SEED,
            "randomization_replicates": RANDOMIZATION_REPLICATES,
            "randomization_seed": RANDOMIZATION_SEED,
        }
    ),
)


def build_formal_verifier_attestation_v1(
    release: AnalysisInputReleaseV2,
    *,
    verified_payload_kind: VerifiedPayloadKind,
    attested_at: str,
) -> FormalVerifierAttestationRefV1:
    """Replay Analysis V2 and bind the exact lifecycle payload it derives."""

    value = _revalidate(release, AnalysisInputReleaseV2)
    _timestamp(attested_at)
    if _timestamp(attested_at) <= _timestamp(value.assembled_at):
        raise ValueError("formal verifier attestation must follow analysis assembly")
    if verified_payload_kind is VerifiedPayloadKind.DEVELOPMENT_ABLATION_METRIC_ROWS:
        if value.execution_phase is not ExecutionPhase.DEVELOPMENT_ABLATIONS:
            raise ValueError("ablation attestation requires development ablations")
        payload: object = derive_development_metric_rows_v2(value)
    elif verified_payload_kind is VerifiedPayloadKind.DEVELOPMENT_FUSION_METRIC_ROWS:
        if value.execution_phase is not ExecutionPhase.DEVELOPMENT_FUSION:
            raise ValueError("Fusion attestation requires development Fusion")
        payload = derive_development_metric_rows_v2(value)
    elif verified_payload_kind is VerifiedPayloadKind.LOCKED_RESULT_FAMILY:
        payload = derive_locked_result_family_v2(value)
    else:  # pragma: no cover - defensive against future enum extension
        raise ValueError("Analysis V2 payload kind is not registered")
    values: dict[str, object] = {
        "analysis_input_ref": analysis_input_ref_v2(value),
        "verifier_ref": ANALYSIS_V2_FORMAL_VERIFIER_REF,
        "verified_payload_kind": verified_payload_kind,
        "verified_payload_sha256": canonical_sha256(payload),
        "attested_at": attested_at,
    }
    draft = FormalVerifierAttestationRefV1.model_construct(**values)
    digest = canonical_sha256(
        draft.model_dump(
            mode="python", exclude={"attestation_id", "attestation_sha256"}
        )
    )
    return FormalVerifierAttestationRefV1.model_validate(
        {
            **values,
            "attestation_id": deterministic_id(
                "analysis-attestation-v1", {"attestation_sha256": digest}
            ),
            "attestation_sha256": digest,
        }
    )


def assert_analysis_input_exact_replay_v2(release: AnalysisInputReleaseV2) -> None:
    value = _revalidate(release, AnalysisInputReleaseV2)
    rebuilt = build_analysis_input_release_v2(
        execution_phase=value.execution_phase,
        split_context_ref=value.split_context_ref,
        leakage_context_ref=value.leakage_context_ref,
        cases=value.cases,
        cells=value.cells,
        assembled_at=value.assembled_at,
    )
    if rebuilt != value:
        raise ValueError("AnalysisInputV2 does not exactly replay")


__all__ = [
    "ANALYSIS_V2_FORMAL_VERIFIER_REF",
    "AnalysisArtifactRefV2",
    "AnalysisArtifactTypeV2",
    "AnalysisCaseBindingV2",
    "AnalysisCellEvidenceV2",
    "AnalysisInputReleaseV2",
    "AnalysisTraceEvidenceV2",
    "AnalysisTraceRoleV2",
    "CaseArmMetricRowV2",
    "FinalJudgmentStatusV2",
    "FinalPositionJudgmentV2",
    "analysis_input_ref_v2",
    "assert_analysis_input_exact_replay_v2",
    "build_analysis_cell_evidence_v2",
    "build_analysis_input_release_v2",
    "build_analysis_trace_evidence_v2",
    "build_final_position_judgment_v2",
    "build_formal_verifier_attestation_v1",
    "derive_development_metric_rows_v2",
    "derive_locked_result_family_v2",
]
