"""Fail-closed analysis-input schema for the flat/narrow-band benchmark.

This module deliberately separates two concerns:

* the deterministic, locally replayable projection from five final positions
  to preregistered case-level metrics; and
* the *formal* release assembler, which must replay the complete V3 chain.

The first concern and the V3 Pilot agreement/Gate closure are implemented here.
The final ``AnalysisInputReleaseV1`` assembler still fails closed: its reserved
signature predates the authoritative V3 CandidatePool/Frozen/Eligibility/
PreBudget/Execution chain and it does not yet derive Main rows from the current
V3 Gold closure.  Projecting those artifacts through a legacy V1/V2 bridge would
preserve the wrong scientific end state, so it is explicitly forbidden.

An ``AnalysisInputReleaseV1`` object is therefore a schema/checking artifact,
not formal benchmark evidence by itself.  Formal consumers must call
``assert_formal_analysis_input_exact_closure``; until a native V3 analysis
assembler lands, that function always raises ``FormalAnalysisPrerequisiteError``.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
import hashlib
from typing import Annotated, Literal, NoReturn, TypeVar

from pydantic import Field, field_validator, model_validator

from material_agent.inspiration.models import (
    Identifier,
    Sha256,
    StrictModel,
    canonical_sha256,
    deterministic_id,
)
from material_agent.research.flatband_cases import (
    FrozenCaseReleaseV2,
    FrozenCaseReleaseV3,
    PreRunEligibilityReleaseV2,
    PreRunEligibilityReleaseV3,
    assert_pre_run_eligibility_precedes_execution_v2,
    assert_pre_run_eligibility_precedes_execution_v3,
)
from material_agent.research.flatband_blinding import (
    EvidenceExcerptV2,
    PrivateIdentityMapV2,
    PrivatePositionMapEntryV2,
    ReviewerManifestV2,
    assert_reviewer_release_exact_coverage_v2,
)
from material_agent.research.flatband_contracts import (
    Assessability,
    BenchmarkSplit,
    BenchmarkSplitManifestV2,
    BridgeVerdict,
    Dimensionality,
    MechanismFamily,
    RawExpertAnnotationV1,
    TargetBandClass,
)
from material_agent.research.flatband_execution import (
    ExecutionPhase,
    ExecutionReleaseV2,
    ExecutionReleaseV3,
    MissingPositionReason,
    ResearchSystemId,
    RunCellStatus,
)
from material_agent.research.flatband_experts import (
    ExpertStudyRegistryV2,
    assert_expert_registry_covers_split_v2,
)
from material_agent.research.flatband_gold import FinalGoldReleaseV2
from material_agent.research.flatband_leakage import (
    LeakageComponentReleaseV3,
    LeakageRoundClosureContextV3,
    MechanismLineageAssignmentCurationReleaseV3,
    MechanismLineageCurationReleaseV3,
    assert_cross_round_leakage_disjoint_v3,
    assert_formal_mechanism_lineage_registry_v3,
    assert_leakage_split_closure_v3,
    component_assignments_v3,
    derive_formal_mechanism_lineage_assignments_v3,
)
from material_agent.research.flatband_metrics import (
    GainKind,
    absolute_ndcg_at_5,
    completion_at_5,
    duplicate_rate_at_5,
    evidence_valid_at_5,
    strong_success_at_5,
    success_at_5,
)
from material_agent.research.flatband_statistics import (
    ComponentRatedUnit,
    case_component_bootstrap_alpha,
)


ModelT = TypeVar("ModelT", bound=StrictModel)


class AnalysisReleaseKind(StrEnum):
    PILOT_AGREEMENT = "PILOT_AGREEMENT"
    CASE_SYSTEM_METRICS = "CASE_SYSTEM_METRICS"


class AnalysisClosureStatus(StrEnum):
    """Only the schema-only state is legal until the V2 Gold seam exists."""

    SCHEMA_ONLY_PENDING_V2_GOLD_EXACT_CLOSURE = (
        "SCHEMA_ONLY_PENDING_V2_GOLD_EXACT_CLOSURE"
    )


class FormalAnalysisPrerequisiteError(RuntimeError):
    """The repository cannot yet prove the formal V2 analysis chain."""


def _timestamp(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("timestamp must be RFC3339-compatible") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("timestamp must include a UTC offset")
    return parsed


def _assert_addressed(
    value: StrictModel, *, id_field: str, sha_field: str, prefix: str
) -> None:
    semantic = value.model_dump(mode="python", exclude={id_field, sha_field})
    digest = canonical_sha256(semantic)
    if getattr(value, sha_field) != digest:
        raise ValueError(f"{sha_field} does not match semantic content")
    if getattr(value, id_field) != deterministic_id(
        prefix, {sha_field: digest}
    ):
        raise ValueError(f"{id_field} does not match {sha_field}")


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
            sha_field: digest,
            id_field: deterministic_id(prefix, {sha_field: digest}),
        }
    )


class AnalysisPositionV1(StrictModel):
    """One exact final position; missing execution positions are explicit zeroes."""

    position: Annotated[int, Field(ge=1, le=5)]
    packet_id: Identifier | None = None
    packet_sha256: Sha256 | None = None
    pooled_unit_id: Identifier | None = None
    gold_judgment_id: Identifier | None = None
    gold_judgment_sha256: Sha256 | None = None
    final_duplicate_cluster_id: Identifier | None = None
    final_assessability: Assessability | None = None
    final_bridge_verdict: BridgeVerdict | None = None
    metric_relevance_gain: Annotated[int, Field(ge=0, le=3)] = 0
    metric_evidence_gain: Literal[0, 1] = 0
    fixed_position_denominator_included: Literal[True] = True
    gold_unit_denominator_included: bool
    unresolvable: bool = False
    forced_zero: bool
    missing_reason: MissingPositionReason | None = None

    @model_validator(mode="after")
    def validate_position(self) -> "AnalysisPositionV1":
        identity_values = (
            self.packet_id,
            self.packet_sha256,
            self.pooled_unit_id,
            self.gold_judgment_id,
            self.gold_judgment_sha256,
            self.final_duplicate_cluster_id,
        )
        present = any(value is not None for value in identity_values)
        if present != all(value is not None for value in identity_values):
            raise ValueError("present position requires the complete packet/Gold identity")
        if not present:
            if (
                not self.forced_zero
                or self.missing_reason is None
                or self.final_assessability is not None
                or self.final_bridge_verdict is not None
                or self.metric_relevance_gain != 0
                or self.metric_evidence_gain != 0
                or self.gold_unit_denominator_included
                or self.unresolvable
            ):
                raise ValueError("missing position must be one explicit forced zero")
            return self

        if self.forced_zero or self.missing_reason is not None:
            raise ValueError("present position cannot be an execution-forced zero")
        if self.unresolvable:
            if (
                self.final_assessability is not None
                or self.final_bridge_verdict is not None
                or self.metric_relevance_gain != 0
                or self.metric_evidence_gain != 0
                or not self.gold_unit_denominator_included
            ):
                raise ValueError("unresolvable Gold position must retain a zero denominator row")
            return self
        if self.final_assessability is Assessability.CASE_INVALID:
            if (
                self.final_bridge_verdict is not None
                or self.metric_relevance_gain != 0
                or self.metric_evidence_gain != 0
                or self.gold_unit_denominator_included
            ):
                raise ValueError("CASE_INVALID position cannot carry metric gain")
            return self
        if self.final_assessability is Assessability.SYSTEM_PACKET_INVALID:
            if (
                self.final_bridge_verdict is not None
                or self.metric_relevance_gain != 0
                or self.metric_evidence_gain != 0
                or not self.gold_unit_denominator_included
            ):
                raise ValueError("invalid system packet must be a retained zero")
            return self
        if self.final_assessability is not Assessability.ASSESSABLE:
            raise ValueError("resolved present position requires final assessability")
        if not self.gold_unit_denominator_included:
            raise ValueError("assessable position must remain in the metric denominator")
        if self.final_bridge_verdict is None:
            raise ValueError("assessable position requires a final bridge verdict")
        if self.metric_relevance_gain >= 2 and (
            self.metric_evidence_gain != 1
            or self.final_bridge_verdict
            not in {BridgeVerdict.CORRECT, BridgeVerdict.CONDITIONAL}
        ):
            raise ValueError("grade two or three requires valid evidence and bridge")
        if self.metric_relevance_gain == 3 and (
            self.final_bridge_verdict is not BridgeVerdict.CORRECT
        ):
            raise ValueError("grade three requires a correct bridge")
        return self


class CaseSystemMetricProjectionV1(StrictModel):
    """Metrics that must replay exactly from five final positions."""

    positions: Annotated[
        tuple[AnalysisPositionV1, ...], Field(min_length=5, max_length=5)
    ]
    case_analysis_included: bool
    returned_count: Annotated[int, Field(ge=0, le=5)]
    linear_andcg_at_5: Annotated[
        float | None, Field(ge=0.0, le=1.0, allow_inf_nan=False)
    ]
    exponential_andcg_at_5: Annotated[
        float | None, Field(ge=0.0, le=1.0, allow_inf_nan=False)
    ]
    evidence_valid_at_5: Annotated[
        float | None, Field(ge=0.0, le=1.0, allow_inf_nan=False)
    ]
    completion_at_5: Annotated[
        float | None, Field(ge=0.0, le=1.0, allow_inf_nan=False)
    ]
    duplicate_rate_at_5: Annotated[
        float | None, Field(ge=0.0, le=1.0, allow_inf_nan=False)
    ]
    success_at_5: Literal[0, 1] | None
    strong_success_at_5: Literal[0, 1] | None
    bridge_correct_at_5: Annotated[
        float | None, Field(ge=0.0, le=1.0, allow_inf_nan=False)
    ]

    @model_validator(mode="after")
    def validate_projection(self) -> "CaseSystemMetricProjectionV1":
        if tuple(item.position for item in self.positions) != (1, 2, 3, 4, 5):
            raise ValueError("analysis projection requires positions one through five")
        present = tuple(item for item in self.positions if item.packet_id is not None)
        if tuple(item.position for item in present) != tuple(
            range(1, len(present) + 1)
        ):
            raise ValueError("returned positions must be one contiguous ranking prefix")
        packet_ids = tuple(item.packet_id for item in present)
        if len(packet_ids) != len(set(packet_ids)):
            raise ValueError("one execution ranking cannot repeat an exact packet")
        if self.returned_count != len(present):
            raise ValueError("returned count does not replay from fixed positions")

        invalid_present = tuple(
            item
            for item in present
            if item.final_assessability is Assessability.CASE_INVALID
        )
        if invalid_present and len(invalid_present) != len(present):
            raise ValueError("CASE_INVALID must be symmetric within a returned row")
        metric_values = (
            self.linear_andcg_at_5,
            self.exponential_andcg_at_5,
            self.evidence_valid_at_5,
            self.completion_at_5,
            self.duplicate_rate_at_5,
            self.success_at_5,
            self.strong_success_at_5,
            self.bridge_correct_at_5,
        )
        if not self.case_analysis_included:
            if any(value is not None for value in metric_values):
                raise ValueError("excluded invalid case cannot carry system metrics")
            if present and not invalid_present:
                raise ValueError("excluded row with output requires CASE_INVALID Gold")
            return self
        if invalid_present:
            raise ValueError("CASE_INVALID row cannot be included in analysis")
        if any(value is None for value in metric_values):
            raise ValueError("included case requires every preregistered metric")

        grades = tuple(item.metric_relevance_gain for item in present)
        clusters = tuple(
            item.final_duplicate_cluster_id for item in present
        )
        evidence = tuple(item.metric_evidence_gain == 1 for item in present)
        expected = (
            absolute_ndcg_at_5(
                grades=grades,
                strict_hypothesis_cluster_ids=clusters,
                gain_kind=GainKind.LINEAR_PRIMARY,
            ),
            absolute_ndcg_at_5(
                grades=grades,
                strict_hypothesis_cluster_ids=clusters,
                gain_kind=GainKind.EXPONENTIAL_SENSITIVITY,
            ),
            evidence_valid_at_5(evidence),
            completion_at_5(len(present)),
            duplicate_rate_at_5(clusters),
            success_at_5(
                grades=grades, strict_hypothesis_cluster_ids=clusters
            ),
            strong_success_at_5(
                grades=grades, strict_hypothesis_cluster_ids=clusters
            ),
            round(
                sum(
                    item.final_bridge_verdict is BridgeVerdict.CORRECT
                    for item in present
                )
                / 5,
                12,
            ),
        )
        if metric_values != expected:
            raise ValueError("case/system metrics do not replay from final five positions")
        return self


def derive_case_system_metric_projection(
    positions: tuple[AnalysisPositionV1, ...],
) -> CaseSystemMetricProjectionV1:
    """Compute an included-case metric row without accepting caller scores."""

    values = tuple(
        AnalysisPositionV1.model_validate(
            item.model_dump(mode="python", round_trip=True)
        )
        for item in positions
    )
    present = tuple(item for item in values if item.packet_id is not None)
    if any(
        item.final_assessability is Assessability.CASE_INVALID for item in present
    ):
        raise ValueError(
            "CASE_INVALID is a case-global exclusion; use the formal Gold assembler"
        )
    grades = tuple(item.metric_relevance_gain for item in present)
    clusters = tuple(item.final_duplicate_cluster_id for item in present)
    evidence = tuple(item.metric_evidence_gain == 1 for item in present)
    return CaseSystemMetricProjectionV1(
        positions=values,
        case_analysis_included=True,
        returned_count=len(present),
        linear_andcg_at_5=absolute_ndcg_at_5(
            grades=grades,
            strict_hypothesis_cluster_ids=clusters,
            gain_kind=GainKind.LINEAR_PRIMARY,
        ),
        exponential_andcg_at_5=absolute_ndcg_at_5(
            grades=grades,
            strict_hypothesis_cluster_ids=clusters,
            gain_kind=GainKind.EXPONENTIAL_SENSITIVITY,
        ),
        evidence_valid_at_5=evidence_valid_at_5(evidence),
        completion_at_5=completion_at_5(len(present)),
        duplicate_rate_at_5=duplicate_rate_at_5(clusters),
        success_at_5=success_at_5(
            grades=grades, strict_hypothesis_cluster_ids=clusters
        ),
        strong_success_at_5=strong_success_at_5(
            grades=grades, strict_hypothesis_cluster_ids=clusters
        ),
        bridge_correct_at_5=round(
            sum(
                item.final_bridge_verdict is BridgeVerdict.CORRECT
                for item in present
            )
            / 5,
            12,
        ),
    )


class AnalysisCaseBindingV1(StrictModel):
    case_id: Identifier
    case_sha256: Sha256
    split: BenchmarkSplit
    target_class: TargetBandClass
    dimensionality: Dimensionality
    primary_mechanism_stratum: MechanismFamily
    leakage_component_id: Identifier


class CaseSystemAnalysisRowV1(StrictModel):
    """One exact case/system row; formal metadata is replayed by the verifier."""

    row_id: Identifier
    row_sha256: Sha256
    execution_release_id: Identifier
    execution_release_sha256: Sha256
    final_gold_release_id: Identifier
    final_gold_release_sha256: Sha256
    cell_id: Identifier
    cell_sha256: Sha256
    terminal_result_id: Identifier
    terminal_result_sha256: Sha256
    top5_projection_id: Identifier
    top5_projection_sha256: Sha256
    case_id: Identifier
    case_sha256: Sha256
    split: BenchmarkSplit
    leakage_component_id: Identifier
    system_id: ResearchSystemId
    system_config_id: Identifier
    system_config_sha256: Sha256
    status: RunCellStatus
    metrics: CaseSystemMetricProjectionV1

    @model_validator(mode="after")
    def validate_row(self) -> "CaseSystemAnalysisRowV1":
        if self.status is RunCellStatus.SUCCEEDED and self.metrics.returned_count != 5:
            raise ValueError("successful analysis row requires all five positions")
        if self.status is RunCellStatus.FAILED and self.metrics.returned_count != 0:
            raise ValueError("failed analysis row cannot contain a returned position")
        _assert_addressed(
            self,
            id_field="row_id",
            sha_field="row_sha256",
            prefix="analysis-row-v1",
        )
        return self


class RawReviewerLabelRefV1(StrictModel):
    reviewer_id: Identifier
    annotation_id: Identifier
    annotation_sha256: Sha256
    assessability: Assessability
    relevance_grade: Annotated[int, Field(ge=0, le=3)] | None
    evidence_valid: bool | None
    bridge_verdict: BridgeVerdict | None

    @model_validator(mode="after")
    def validate_label(self) -> "RawReviewerLabelRefV1":
        if self.assessability is Assessability.CASE_INVALID:
            if any(
                value is not None
                for value in (
                    self.relevance_grade,
                    self.evidence_valid,
                    self.bridge_verdict,
                )
            ):
                raise ValueError("raw CASE_INVALID cannot carry candidate labels")
        elif self.assessability is Assessability.SYSTEM_PACKET_INVALID:
            if (
                self.relevance_grade != 0
                or self.evidence_valid is not None
                or self.bridge_verdict is not None
            ):
                raise ValueError("raw invalid packet must retain grade zero only")
        elif any(
            value is None
            for value in (
                self.relevance_grade,
                self.evidence_valid,
                self.bridge_verdict,
            )
        ):
            raise ValueError("raw assessable label requires grade/evidence/bridge")
        return self


class PilotAgreementUnitV1(StrictModel):
    """Two pre-adjudication labels for one exact pooled unit."""

    case_id: Identifier
    case_sha256: Sha256
    leakage_component_id: Identifier
    pooled_unit_id: Identifier
    packet_id: Identifier
    packet_sha256: Sha256
    final_gold_judgment_id: Identifier
    final_gold_judgment_sha256: Sha256
    raw_labels: Annotated[
        tuple[RawReviewerLabelRefV1, ...], Field(min_length=2, max_length=2)
    ]
    grade_alpha_included: bool

    @model_validator(mode="after")
    def validate_unit(self) -> "PilotAgreementUnitV1":
        reviewer_ids = tuple(item.reviewer_id for item in self.raw_labels)
        if reviewer_ids != tuple(sorted(set(reviewer_ids))):
            raise ValueError("Pilot agreement requires two reviewer-sorted labels")
        expected = all(
            item.assessability is Assessability.ASSESSABLE
            for item in self.raw_labels
        )
        if self.grade_alpha_included is not expected:
            raise ValueError("grade-alpha inclusion does not replay from raw assessability")
        return self


PILOT_ALPHA_BOOTSTRAP_REPLICATES = 50_000
PILOT_ALPHA_BOOTSTRAP_SEED = 20_260_809
PILOT_ALPHA_PASS_THRESHOLD = 0.80
PILOT_ALPHA_R1_REVISION_FLOOR = 0.667


class PilotReviewerMapCompatibility(StrEnum):
    """Formal reviewer-map generation consumed by the agreement release."""

    NATIVE_PRIVATE_MAP_V2 = "NATIVE_PRIVATE_MAP_V2"


class PilotAgreementChainStatus(StrEnum):
    FORMAL_EXECUTION_V3_REPLAYED = "FORMAL_EXECUTION_V3_REPLAYED"


class FormalPilotAgreementPrerequisiteError(RuntimeError):
    """ExecutionV3 cannot yet deterministically replay ReviewerManifestV2."""


class PilotAgreementGateDecision(StrEnum):
    R1_PASS_MAIN_ALLOWED = "R1_PASS_MAIN_ALLOWED"
    R1_GUIDE_REVISION_AND_DISJOINT_R2_REQUIRED = (
        "R1_GUIDE_REVISION_AND_DISJOINT_R2_REQUIRED"
    )
    STOP_R1_BELOW_0_667 = "STOP_R1_BELOW_0_667"
    R2_PASS_MAIN_ALLOWED = "R2_PASS_MAIN_ALLOWED"
    STOP_R2_BELOW_0_80 = "STOP_R2_BELOW_0_80"
    ROUND_FAIL_CLOSED = "ROUND_FAIL_CLOSED"


class PilotPrivateMapRefV1(StrictModel):
    reviewer_id: Identifier
    identity_map_id: Identifier
    identity_map_sha256: Sha256
    reviewer_manifest_id: Identifier
    reviewer_manifest_sha256: Sha256


class FormalPilotCaseBindingV1(StrictModel):
    case_id: Identifier
    case_sha256: Sha256
    split: Literal[BenchmarkSplit.PILOT_R1, BenchmarkSplit.PILOT_R2]
    leakage_component_id: Identifier
    selected_candidate_id: Identifier
    selected_candidate_sha256: Sha256
    reviewer_ids: Annotated[
        tuple[Identifier, ...], Field(min_length=2, max_length=2)
    ]

    @model_validator(mode="after")
    def validate_case(self) -> "FormalPilotCaseBindingV1":
        if self.reviewer_ids != tuple(sorted(set(self.reviewer_ids))):
            raise ValueError("formal Pilot case requires two sorted reviewers")
        return self


class FormalPilotRawLabelRefV1(StrictModel):
    reviewer_id: Identifier
    identity_map_id: Identifier
    identity_map_sha256: Sha256
    reviewer_manifest_id: Identifier
    reviewer_manifest_sha256: Sha256
    blinded_unit_id: Identifier
    annotation_id: Identifier
    annotation_sha256: Sha256
    assessability: Assessability
    relevance_grade: Annotated[int, Field(ge=0, le=3)] | None

    @model_validator(mode="after")
    def validate_label(self) -> "FormalPilotRawLabelRefV1":
        if self.assessability is Assessability.CASE_INVALID:
            if self.relevance_grade is not None:
                raise ValueError("CASE_INVALID cannot carry an ordinal grade")
        elif self.assessability is Assessability.SYSTEM_PACKET_INVALID:
            if self.relevance_grade != 0:
                raise ValueError("SYSTEM_PACKET_INVALID must retain grade zero")
        elif self.relevance_grade is None:
            raise ValueError("assessable raw label requires an ordinal grade")
        return self


class FormalPilotAgreementUnitV1(StrictModel):
    """Exactly two pre-adjudication labels for one reviewer-independent unit."""

    case_id: Identifier
    case_sha256: Sha256
    leakage_component_id: Identifier
    pooled_unit_id: Identifier
    packet_id: Identifier
    packet_sha256: Sha256
    raw_labels: Annotated[
        tuple[FormalPilotRawLabelRefV1, ...], Field(min_length=2, max_length=2)
    ]
    ordinal_ratings: tuple[
        Annotated[int, Field(ge=0, le=3)],
        Annotated[int, Field(ge=0, le=3)],
    ] | None
    round_fail_closed: bool

    @model_validator(mode="after")
    def validate_unit(self) -> "FormalPilotAgreementUnitV1":
        reviewers = tuple(item.reviewer_id for item in self.raw_labels)
        if reviewers != tuple(sorted(set(reviewers))):
            raise ValueError("formal Pilot unit requires two reviewer-sorted labels")
        assessments = tuple(item.assessability for item in self.raw_labels)
        if all(item is Assessability.ASSESSABLE for item in assessments):
            expected_ratings = tuple(
                int(item.relevance_grade) for item in self.raw_labels
            )
        elif all(
            item is Assessability.SYSTEM_PACKET_INVALID for item in assessments
        ):
            expected_ratings = (0, 0)
        else:
            expected_ratings = None
        expected_fail = (
            Assessability.CASE_INVALID in assessments
            or expected_ratings is None
        )
        if self.ordinal_ratings != expected_ratings:
            raise ValueError("ordinal ratings do not replay from two raw labels")
        if self.round_fail_closed is not expected_fail:
            raise ValueError("unit fail-closed state does not replay from assessability")
        return self


class FormalPilotAgreementReleaseV1(StrictModel):
    """Formal, content-addressed pre-adjudication Pilot agreement release."""

    schema_version: Literal["flatband-formal-pilot-agreement-release-v1"] = (
        "flatband-formal-pilot-agreement-release-v1"
    )
    release_id: Identifier
    release_sha256: Sha256
    execution_phase: Literal[ExecutionPhase.PILOT_R1, ExecutionPhase.PILOT_R2]
    review_round: Annotated[int, Field(ge=1, le=2)]
    annotation_guide_sha256: Sha256
    split_manifest_id: Identifier
    split_manifest_sha256: Sha256
    leakage_release_id: Identifier
    leakage_release_sha256: Sha256
    lineage_curation_release_id: Identifier
    lineage_curation_release_sha256: Sha256
    lineage_assignment_curation_release: (
        MechanismLineageAssignmentCurationReleaseV3
    )
    frozen_case_release_id: Identifier
    frozen_case_release_sha256: Sha256
    pre_run_eligibility_release_id: Identifier
    pre_run_eligibility_release_sha256: Sha256
    execution_release_id: Identifier
    execution_release_sha256: Sha256
    expert_registry_id: Identifier
    expert_registry_sha256: Sha256
    private_map_refs: Annotated[
        tuple[PilotPrivateMapRefV1, ...], Field(min_length=2, max_length=12)
    ]
    reviewer_replay_inputs_sha256: Sha256
    source_inputs_sha256: Sha256
    cases: Annotated[
        tuple[FormalPilotCaseBindingV1, ...], Field(min_length=30, max_length=30)
    ]
    units: Annotated[
        tuple[FormalPilotAgreementUnitV1, ...], Field(min_length=1, max_length=100_000)
    ]
    round_fail_closed: bool
    assembled_at: Annotated[str, Field(min_length=20, max_length=40)]
    reviewer_map_compatibility: Literal[
        PilotReviewerMapCompatibility.NATIVE_PRIVATE_MAP_V2
    ] = PilotReviewerMapCompatibility.NATIVE_PRIVATE_MAP_V2
    chain_status: Literal[
        PilotAgreementChainStatus.FORMAL_EXECUTION_V3_REPLAYED
    ] = PilotAgreementChainStatus.FORMAL_EXECUTION_V3_REPLAYED
    formal_scientific_release: Literal[True] = True
    formal_execution_v3_required: Literal[True] = True
    legacy_execution_v2_formal_alias_allowed: Literal[False] = False
    native_reviewer_map_v2_required: Literal[True] = True
    deterministic_reviewer_replay_required: Literal[True] = True
    pre_adjudication_raw_annotations_only: Literal[True] = True
    exact_present_unit_coverage_required: Literal[True] = True
    exactly_two_assigned_labels_per_unit: Literal[True] = True
    caller_supplied_rated_units_allowed: Literal[False] = False
    complete_case_omission_allowed: Literal[False] = False
    scientific_conclusion: Literal[False] = False

    @field_validator("assembled_at")
    @classmethod
    def validate_time(cls, value: str) -> str:
        _timestamp(value)
        return value

    @model_validator(mode="after")
    def validate_release(self) -> "FormalPilotAgreementReleaseV1":
        if self.review_round != (
            1 if self.execution_phase is ExecutionPhase.PILOT_R1 else 2
        ):
            raise ValueError("Pilot phase and review round differ")
        case_keys = tuple(item.case_id for item in self.cases)
        if case_keys != tuple(sorted(set(case_keys))):
            raise ValueError("formal Pilot cases must be sorted and exact")
        expected_split = (
            BenchmarkSplit.PILOT_R1
            if self.review_round == 1
            else BenchmarkSplit.PILOT_R2
        )
        if any(item.split is not expected_split for item in self.cases):
            raise ValueError("formal Pilot case belongs to the wrong round split")
        case_by_id = {item.case_id: item for item in self.cases}

        map_reviewers = tuple(item.reviewer_id for item in self.private_map_refs)
        if map_reviewers != tuple(sorted(set(map_reviewers))):
            raise ValueError("private-map refs must be reviewer-sorted and unique")
        map_by_reviewer = {item.reviewer_id: item for item in self.private_map_refs}
        unit_keys = tuple((item.case_id, item.pooled_unit_id) for item in self.units)
        if unit_keys != tuple(sorted(set(unit_keys))):
            raise ValueError("formal Pilot units must be case/pool sorted and unique")
        packet_to_pool: dict[tuple[str, str], str] = {}
        pool_to_packet: dict[str, tuple[str, str]] = {}
        blind_to_pool: dict[tuple[str, str], str] = {}
        for unit in self.units:
            case = case_by_id.get(unit.case_id)
            if case is None or (
                unit.case_sha256,
                unit.leakage_component_id,
            ) != (case.case_sha256, case.leakage_component_id):
                raise ValueError("formal Pilot unit drifts from case/component")
            if tuple(item.reviewer_id for item in unit.raw_labels) != case.reviewer_ids:
                raise ValueError("formal Pilot unit labels differ from assigned reviewers")
            packet_key = (unit.packet_id, unit.packet_sha256)
            if packet_to_pool.setdefault(packet_key, unit.pooled_unit_id) != (
                unit.pooled_unit_id
            ):
                raise ValueError("one packet aliases multiple pooled units")
            if pool_to_packet.setdefault(unit.pooled_unit_id, packet_key) != packet_key:
                raise ValueError("one pooled unit aliases multiple packets")
            for label in unit.raw_labels:
                map_ref = map_by_reviewer.get(label.reviewer_id)
                if map_ref is None or (
                    label.identity_map_id,
                    label.identity_map_sha256,
                    label.reviewer_manifest_id,
                    label.reviewer_manifest_sha256,
                ) != (
                    map_ref.identity_map_id,
                    map_ref.identity_map_sha256,
                    map_ref.reviewer_manifest_id,
                    map_ref.reviewer_manifest_sha256,
                ):
                    raise ValueError("raw label binds a foreign private map")
                blind_key = (label.reviewer_id, label.blinded_unit_id)
                if blind_to_pool.setdefault(blind_key, unit.pooled_unit_id) != (
                    unit.pooled_unit_id
                ):
                    raise ValueError("reviewer blind identity aliases multiple pooled units")

        if self.round_fail_closed is not any(
            item.round_fail_closed for item in self.units
        ):
            raise ValueError("release fail-closed state does not replay from units")
        expected_input_sha = canonical_sha256(
            {
                "split_manifest": (
                    self.split_manifest_id,
                    self.split_manifest_sha256,
                ),
                "leakage_release": (
                    self.leakage_release_id,
                    self.leakage_release_sha256,
                ),
                "lineage_curation_release": (
                    self.lineage_curation_release_id,
                    self.lineage_curation_release_sha256,
                ),
                "lineage_assignment_curation_release": (
                    self.lineage_assignment_curation_release.release_id,
                    self.lineage_assignment_curation_release.release_sha256,
                ),
                "frozen_case_release": (
                    self.frozen_case_release_id,
                    self.frozen_case_release_sha256,
                ),
                "pre_run_eligibility_release": (
                    self.pre_run_eligibility_release_id,
                    self.pre_run_eligibility_release_sha256,
                ),
                "execution_release": (
                    self.execution_release_id,
                    self.execution_release_sha256,
                ),
                "expert_registry": (
                    self.expert_registry_id,
                    self.expert_registry_sha256,
                ),
                "private_maps": tuple(
                    item.model_dump(mode="python") for item in self.private_map_refs
                ),
                "reviewer_replay_inputs_sha256": (
                    self.reviewer_replay_inputs_sha256
                ),
                "raw_annotations": tuple(
                    (
                        label.annotation_id,
                        label.annotation_sha256,
                        label.reviewer_id,
                        label.blinded_unit_id,
                    )
                    for unit in self.units
                    for label in unit.raw_labels
                ),
            }
        )
        if self.source_inputs_sha256 != expected_input_sha:
            raise ValueError("formal Pilot source-input SHA-256 does not replay")
        _assert_addressed(
            self,
            id_field="release_id",
            sha_field="release_sha256",
            prefix="pilot-agreement-release-v1",
        )
        return self


class FormalPilotAgreementGateReleaseV1(StrictModel):
    """Preregistered R1/R2 decision derived only from a formal agreement release."""

    schema_version: Literal["flatband-formal-pilot-agreement-gate-v1"] = (
        "flatband-formal-pilot-agreement-gate-v1"
    )
    gate_id: Identifier
    gate_sha256: Sha256
    agreement_release_id: Identifier
    agreement_release_sha256: Sha256
    leakage_release_id: Identifier
    leakage_release_sha256: Sha256
    lineage_curation_release_id: Identifier
    lineage_curation_release_sha256: Sha256
    lineage_assignment_curation_release_id: Identifier
    lineage_assignment_curation_release_sha256: Sha256
    execution_phase: Literal[ExecutionPhase.PILOT_R1, ExecutionPhase.PILOT_R2]
    review_round: Annotated[int, Field(ge=1, le=2)]
    annotation_guide_sha256: Sha256
    prior_r1_agreement_release_id: Identifier | None = None
    prior_r1_agreement_release_sha256: Sha256 | None = None
    prior_r1_gate_id: Identifier | None = None
    prior_r1_gate_sha256: Sha256 | None = None
    prior_r1_leakage_release_id: Identifier | None = None
    prior_r1_leakage_release_sha256: Sha256 | None = None
    prior_r1_lineage_assignment_curation_release_id: Identifier | None = None
    prior_r1_lineage_assignment_curation_release_sha256: Sha256 | None = None
    r2_cases_and_components_disjoint: bool | None = None
    alpha: Annotated[
        float | None, Field(ge=-1.0, le=1.0, allow_inf_nan=False)
    ]
    bootstrap_lower: Annotated[
        float | None, Field(ge=-1.0, le=1.0, allow_inf_nan=False)
    ]
    bootstrap_upper: Annotated[
        float | None, Field(ge=-1.0, le=1.0, allow_inf_nan=False)
    ]
    bootstrap_replicates: Literal[PILOT_ALPHA_BOOTSTRAP_REPLICATES] = (
        PILOT_ALPHA_BOOTSTRAP_REPLICATES
    )
    bootstrap_seed: Literal[PILOT_ALPHA_BOOTSTRAP_SEED] = PILOT_ALPHA_BOOTSTRAP_SEED
    valid_bootstrap_replicates: Annotated[int, Field(ge=0, le=50_000)]
    undefined_bootstrap_replicates: Annotated[int, Field(ge=0, le=50_000)]
    total_units: Annotated[int, Field(ge=1)]
    rated_units: Annotated[int, Field(ge=0)]
    paired_system_packet_invalid_units: Annotated[int, Field(ge=0)]
    unilateral_system_packet_invalid_units: Annotated[int, Field(ge=0)]
    case_invalid_units: Annotated[int, Field(ge=0)]
    exact_assessability_agreement_rate: Annotated[
        float, Field(ge=0.0, le=1.0, allow_inf_nan=False)
    ]
    exact_grade_agreement_rate: Annotated[
        float, Field(ge=0.0, le=1.0, allow_inf_nan=False)
    ]
    pass_threshold: Literal[PILOT_ALPHA_PASS_THRESHOLD] = PILOT_ALPHA_PASS_THRESHOLD
    r1_revision_floor: Literal[PILOT_ALPHA_R1_REVISION_FLOOR] = (
        PILOT_ALPHA_R1_REVISION_FLOOR
    )
    minimum_exact_agreement_used_as_gate: Literal[False] = False
    round_fail_closed: bool
    decision: PilotAgreementGateDecision
    evaluated_at: Annotated[str, Field(min_length=20, max_length=40)]
    caller_supplied_rated_units_allowed: Literal[False] = False
    scientific_conclusion: Literal[False] = False

    @field_validator("evaluated_at")
    @classmethod
    def validate_time(cls, value: str) -> str:
        _timestamp(value)
        return value

    @model_validator(mode="after")
    def validate_gate(self) -> "FormalPilotAgreementGateReleaseV1":
        if self.review_round != (
            1 if self.execution_phase is ExecutionPhase.PILOT_R1 else 2
        ):
            raise ValueError("Pilot Gate phase and round differ")
        if self.rated_units > self.total_units:
            raise ValueError("rated-unit count exceeds the exact unit denominator")
        if self.round_fail_closed:
            if any(
                value is not None
                for value in (self.alpha, self.bootstrap_lower, self.bootstrap_upper)
            ) or self.valid_bootstrap_replicates or self.undefined_bootstrap_replicates:
                raise ValueError("fail-closed round cannot publish alpha/bootstrap")
            expected = PilotAgreementGateDecision.ROUND_FAIL_CLOSED
        else:
            if self.rated_units != self.total_units:
                raise ValueError("valid Pilot round must rate every exact pooled unit")
            if (
                self.valid_bootstrap_replicates
                + self.undefined_bootstrap_replicates
                != self.bootstrap_replicates
            ):
                raise ValueError("bootstrap replicate accounting is incomplete")
            if self.alpha is None:
                expected = (
                    PilotAgreementGateDecision.STOP_R1_BELOW_0_667
                    if self.review_round == 1
                    else PilotAgreementGateDecision.STOP_R2_BELOW_0_80
                )
            elif self.review_round == 1:
                if self.alpha >= PILOT_ALPHA_PASS_THRESHOLD:
                    expected = PilotAgreementGateDecision.R1_PASS_MAIN_ALLOWED
                elif self.alpha >= PILOT_ALPHA_R1_REVISION_FLOOR:
                    expected = (
                        PilotAgreementGateDecision.R1_GUIDE_REVISION_AND_DISJOINT_R2_REQUIRED
                    )
                else:
                    expected = PilotAgreementGateDecision.STOP_R1_BELOW_0_667
            else:
                expected = (
                    PilotAgreementGateDecision.R2_PASS_MAIN_ALLOWED
                    if self.alpha >= PILOT_ALPHA_PASS_THRESHOLD
                    else PilotAgreementGateDecision.STOP_R2_BELOW_0_80
                )
        if self.decision is not expected:
            raise ValueError("Pilot Gate decision does not replay from alpha and round")

        prior_values = (
            self.prior_r1_agreement_release_id,
            self.prior_r1_agreement_release_sha256,
            self.prior_r1_gate_id,
            self.prior_r1_gate_sha256,
            self.prior_r1_leakage_release_id,
            self.prior_r1_leakage_release_sha256,
            self.prior_r1_lineage_assignment_curation_release_id,
            self.prior_r1_lineage_assignment_curation_release_sha256,
            self.r2_cases_and_components_disjoint,
        )
        if self.review_round == 1:
            if any(item is not None for item in prior_values):
                raise ValueError("R1 Gate cannot reference a prior Pilot round")
        elif any(item is None for item in prior_values) or (
            self.r2_cases_and_components_disjoint is not True
        ):
            raise ValueError("R2 Gate requires a disjoint, fully referenced R1")
        _assert_addressed(
            self,
            id_field="gate_id",
            sha_field="gate_sha256",
            prefix="pilot-agreement-gate-v1",
        )
        return self


_PHASE_SYSTEMS: dict[ExecutionPhase, tuple[ResearchSystemId, ...]] = {
    ExecutionPhase.DEVELOPMENT_ABLATIONS: tuple(
        sorted(
            (
                ResearchSystemId.B0,
                ResearchSystemId.E1,
                ResearchSystemId.E2_A,
                ResearchSystemId.E2_B,
                ResearchSystemId.E3,
            ),
            key=lambda item: item.value,
        )
    ),
    ExecutionPhase.DEVELOPMENT_LOCAL_SENSITIVITY: (ResearchSystemId.E1_LOCAL,),
    ExecutionPhase.DEVELOPMENT_FUSION: (ResearchSystemId.FUSION,),
    ExecutionPhase.LOCKED_PRIMARY: tuple(
        sorted(
            (ResearchSystemId.B0, ResearchSystemId.FUSION),
            key=lambda item: item.value,
        )
    ),
}


class AnalysisInputReleaseV1(StrictModel):
    """Content-addressed schema artifact pending a formal V2 Gold verifier.

    Direct model construction is not scientific evidence.  Formal use requires
    ``assert_formal_analysis_input_exact_closure`` to succeed.
    """

    schema_version: Literal["flatband-analysis-input-release-v1"] = (
        "flatband-analysis-input-release-v1"
    )
    release_id: Identifier
    release_sha256: Sha256
    release_kind: AnalysisReleaseKind
    execution_phase: ExecutionPhase
    split_manifest_id: Identifier
    split_manifest_sha256: Sha256
    leakage_release_id: Identifier
    leakage_release_sha256: Sha256
    frozen_case_release_id: Identifier
    frozen_case_release_sha256: Sha256
    execution_release_id: Identifier
    execution_release_sha256: Sha256
    final_gold_release_id: Identifier
    final_gold_release_sha256: Sha256
    gold_exact_closure_inputs_sha256: Sha256
    analysis_environment_sha256: Sha256
    cases: Annotated[
        tuple[AnalysisCaseBindingV1, ...], Field(min_length=30, max_length=60)
    ]
    metric_rows: Annotated[
        tuple[CaseSystemAnalysisRowV1, ...], Field(max_length=300)
    ] = ()
    agreement_units: Annotated[
        tuple[PilotAgreementUnitV1, ...], Field(max_length=100_000)
    ] = ()
    assembled_at: Annotated[str, Field(min_length=20, max_length=40)]
    closure_status: Literal[
        AnalysisClosureStatus.SCHEMA_ONLY_PENDING_V2_GOLD_EXACT_CLOSURE
    ] = AnalysisClosureStatus.SCHEMA_ONLY_PENDING_V2_GOLD_EXACT_CLOSURE
    formal_execution_v2_required: Literal[True] = True
    legacy_execution_v1_allowed: Literal[False] = False
    caller_supplied_scores_allowed: Literal[False] = False
    caller_supplied_case_bindings_allowed: Literal[False] = False
    complete_case_omission_allowed: Literal[False] = False
    scientific_conclusion: Literal[False] = False

    @field_validator("assembled_at")
    @classmethod
    def validate_time(cls, value: str) -> str:
        _timestamp(value)
        return value

    @model_validator(mode="after")
    def validate_release(self) -> "AnalysisInputReleaseV1":
        case_keys = tuple(item.case_id for item in self.cases)
        if case_keys != tuple(sorted(set(case_keys))):
            raise ValueError("analysis cases must be case-ID sorted and unique")
        case_by_id = {item.case_id: item for item in self.cases}

        if self.execution_phase is ExecutionPhase.LOCKED_PRIMARY:
            expected_splits = {
                BenchmarkSplit.LOCKED_IID: 30,
                BenchmarkSplit.LOCKED_OOD: 30,
            }
        elif self.execution_phase in {
            ExecutionPhase.DEVELOPMENT_ABLATIONS,
            ExecutionPhase.DEVELOPMENT_LOCAL_SENSITIVITY,
            ExecutionPhase.DEVELOPMENT_FUSION,
        }:
            expected_splits = {BenchmarkSplit.DEVELOPMENT: 60}
        elif self.execution_phase is ExecutionPhase.PILOT_R1:
            expected_splits = {BenchmarkSplit.PILOT_R1: 30}
        else:
            expected_splits = {BenchmarkSplit.PILOT_R2: 30}
        observed_splits = {
            split: sum(item.split is split for item in self.cases)
            for split in BenchmarkSplit
        }
        observed_splits = {
            split: count for split, count in observed_splits.items() if count
        }
        if observed_splits != expected_splits:
            raise ValueError("analysis case splits differ from the preregistered phase")

        if self.release_kind is AnalysisReleaseKind.PILOT_AGREEMENT:
            if self.execution_phase not in {
                ExecutionPhase.PILOT_R1,
                ExecutionPhase.PILOT_R2,
            }:
                raise ValueError("Pilot agreement requires a Pilot execution phase")
            if self.metric_rows or not self.agreement_units:
                raise ValueError("Pilot agreement requires raw units and no metric rows")
            unit_keys = tuple(
                (item.case_id, item.pooled_unit_id) for item in self.agreement_units
            )
            if unit_keys != tuple(sorted(set(unit_keys))):
                raise ValueError("Pilot agreement units must be case/pool sorted and unique")
            packet_to_pool: dict[tuple[str, str], str] = {}
            judgment_to_identity: dict[str, tuple[str, str, str, str]] = {}
            for unit in self.agreement_units:
                case = case_by_id.get(unit.case_id)
                if case is None or (
                    unit.case_sha256,
                    unit.leakage_component_id,
                ) != (case.case_sha256, case.leakage_component_id):
                    raise ValueError("agreement unit drifts from its analysis case/component")
                packet_key = (unit.packet_id, unit.packet_sha256)
                prior_pool = packet_to_pool.setdefault(packet_key, unit.pooled_unit_id)
                if prior_pool != unit.pooled_unit_id:
                    raise ValueError("one packet aliases multiple pooled Gold units")
                gold_identity = (
                    unit.final_gold_judgment_sha256,
                    unit.case_id,
                    unit.packet_id,
                    unit.pooled_unit_id,
                )
                prior_gold = judgment_to_identity.setdefault(
                    unit.final_gold_judgment_id, gold_identity
                )
                if prior_gold != gold_identity:
                    raise ValueError("Gold judgment identity aliases another unit")
        else:
            if self.agreement_units:
                raise ValueError("case/system metric release cannot carry raw agreement units")
            expected_systems = _PHASE_SYSTEMS.get(self.execution_phase)
            if expected_systems is None:
                raise ValueError("Pilot metric release is not a formal phase API")
            expected_rows = {
                (case.case_id, system)
                for case in self.cases
                for system in expected_systems
            }
            row_keys = tuple(
                (item.case_id, item.system_id.value) for item in self.metric_rows
            )
            if row_keys != tuple(sorted(set(row_keys))):
                raise ValueError("analysis metric rows must be case/system sorted and unique")
            if {(item.case_id, item.system_id) for item in self.metric_rows} != expected_rows:
                raise ValueError("analysis metric rows do not exactly cover case x system")

            cell_ids: set[str] = set()
            projection_ids: set[str] = set()
            terminal_ids: set[str] = set()
            judgment_to_identity: dict[str, tuple[str, str, str, str]] = {}
            pooled_to_packet: dict[str, tuple[str, str]] = {}
            cluster_to_case: dict[str, str] = {}
            case_invalid: dict[str, bool] = {case_id: False for case_id in case_by_id}
            for row in self.metric_rows:
                case = case_by_id.get(row.case_id)
                if case is None or (
                    row.case_sha256,
                    row.split,
                    row.leakage_component_id,
                ) != (
                    case.case_sha256,
                    case.split,
                    case.leakage_component_id,
                ):
                    raise ValueError("metric row drifts from its analysis case/component")
                if (
                    row.execution_release_id,
                    row.execution_release_sha256,
                    row.final_gold_release_id,
                    row.final_gold_release_sha256,
                ) != (
                    self.execution_release_id,
                    self.execution_release_sha256,
                    self.final_gold_release_id,
                    self.final_gold_release_sha256,
                ):
                    raise ValueError("metric row binds a foreign execution or Gold release")
                for identity, seen, label in (
                    (row.cell_id, cell_ids, "execution cell"),
                    (row.top5_projection_id, projection_ids, "Top-5 projection"),
                    (row.terminal_result_id, terminal_ids, "terminal result"),
                ):
                    if identity in seen:
                        raise ValueError(f"duplicate {label} identity across metric rows")
                    seen.add(identity)
                for position in row.metrics.positions:
                    if position.packet_id is None:
                        continue
                    assert position.packet_sha256 is not None
                    assert position.pooled_unit_id is not None
                    assert position.gold_judgment_id is not None
                    assert position.gold_judgment_sha256 is not None
                    assert position.final_duplicate_cluster_id is not None
                    gold_identity = (
                        position.gold_judgment_sha256,
                        row.case_id,
                        position.packet_id,
                        position.pooled_unit_id,
                    )
                    prior_gold = judgment_to_identity.setdefault(
                        position.gold_judgment_id, gold_identity
                    )
                    if prior_gold != gold_identity:
                        raise ValueError("Gold judgment identity aliases another cell/unit")
                    packet_key = (position.packet_id, position.packet_sha256)
                    prior_packet = pooled_to_packet.setdefault(
                        position.pooled_unit_id, packet_key
                    )
                    if prior_packet != packet_key:
                        raise ValueError("pooled Gold unit aliases multiple packets")
                    prior_case = cluster_to_case.setdefault(
                        position.final_duplicate_cluster_id, row.case_id
                    )
                    if prior_case != row.case_id:
                        raise ValueError("final duplicate cluster crosses cases")
                    if position.final_assessability is Assessability.CASE_INVALID:
                        case_invalid[row.case_id] = True
            for case_id, invalid in case_invalid.items():
                included_flags = {
                    item.metrics.case_analysis_included
                    for item in self.metric_rows
                    if item.case_id == case_id
                }
                if included_flags != {not invalid}:
                    raise ValueError("CASE_INVALID exclusion is not symmetric across systems")

        _assert_addressed(
            self,
            id_field="release_id",
            sha_field="release_sha256",
            prefix="analysis-input-release-v1",
        )
        return self


def _revalidate_model(value: ModelT, model_type: type[ModelT]) -> ModelT:
    return model_type.model_validate(
        value.model_dump(mode="python", round_trip=True)
    )


def _assert_private_evidence_exact(
    entry: PrivatePositionMapEntryV2,
    *,
    packet: object,
    authoritative_receipts: dict[tuple[str, str, str], object],
) -> None:
    links = tuple(getattr(packet, "evidence_links"))
    expected_ids = tuple(sorted(item.evidence_link_id for item in links))
    evidence_by_id = {item.evidence_link_id: item for item in entry.evidence_map}
    if tuple(sorted(evidence_by_id)) != expected_ids:
        raise ValueError("private evidence map does not exactly cover packet evidence")
    for link in links:
        private = evidence_by_id[link.evidence_link_id]
        if (
            private.source_id,
            private.source_record_id,
            private.source_url,
            private.span_id,
            private.source_span_sha256,
            private.private_text_artifact_uri,
        ) != (
            link.source_id,
            link.source_record_id,
            link.source_url,
            link.span_id,
            link.span_sha256,
            link.private_text_artifact_uri,
        ):
            raise ValueError("private evidence mapping differs from execution packet")
        receipt = authoritative_receipts.get(
            (entry.cell_id, entry.packet_id, link.evidence_link_id)
        )
        if receipt is None or (
            private.evidence_receipt_id,
            private.evidence_receipt_sha256,
            private.record_receipt_id,
            private.record_receipt_sha256,
            private.normalized_metadata_artifact_id,
            private.normalized_metadata_artifact_sha256,
            private.field_artifact_id,
            private.field_artifact_sha256,
            private.span_preimage_id,
            private.span_preimage_sha256,
            private.span_field,
            private.metadata_json_path,
            private.span_start_byte,
            private.span_end_byte,
            private.span_locator_sha256,
        ) != (
            receipt.evidence_receipt_id,
            receipt.evidence_receipt_sha256,
            receipt.record_receipt_id,
            receipt.record_receipt_sha256,
            receipt.normalized_metadata_artifact_id,
            receipt.normalized_metadata_artifact_sha256,
            receipt.field_artifact_id,
            receipt.field_artifact_sha256,
            receipt.span_preimage_id,
            receipt.span_preimage_sha256,
            receipt.span_field,
            receipt.metadata_json_path,
            receipt.span_start_byte,
            receipt.span_end_byte,
            receipt.span_locator_sha256,
        ):
            raise ValueError("private V2 evidence preimage differs from execution receipts")


def _precheck_formal_annotation_cover_v1(
    *,
    private_identity_maps: tuple[PrivateIdentityMapV2, ...],
    raw_annotations: tuple[RawExpertAnnotationV1, ...],
) -> None:
    """Reject denominator attacks before replaying the large V3 execution.

    This is only an ordering optimization.  The full reviewer/execution replay
    below remains authoritative and repeats these bindings from source data.
    """

    maps = tuple(
        _revalidate_model(item, PrivateIdentityMapV2)
        for item in private_identity_maps
    )
    annotations = tuple(
        _revalidate_model(item, RawExpertAnnotationV1)
        for item in raw_annotations
    )
    expected: dict[tuple[str, str], tuple[str, str, str, str]] = {}
    for private_map in maps:
        for entry in private_map.entries:
            key = (private_map.expert_id, entry.blinded_unit_id)
            if key in expected:
                raise ValueError("private reviewer maps contain duplicate labels")
            expected[key] = (
                entry.case_id,
                entry.case_sha256,
                entry.packet_id,
                entry.packet_sha256,
            )
    observed: dict[tuple[str, str], RawExpertAnnotationV1] = {}
    annotation_ids: set[str] = set()
    annotation_shas: set[str] = set()
    for annotation in annotations:
        key = (annotation.reviewer_id, annotation.blinded_unit_id)
        if key in observed:
            raise ValueError("one reviewer supplied duplicate labels for a blinded unit")
        if (
            annotation.annotation_id in annotation_ids
            or annotation.annotation_sha256 in annotation_shas
        ):
            raise ValueError("raw annotation IDs and SHA-256 values are not one-to-one")
        observed[key] = annotation
        annotation_ids.add(annotation.annotation_id)
        annotation_shas.add(annotation.annotation_sha256)
    if set(observed) != set(expected):
        raise ValueError(
            "raw annotations do not exactly cover assigned reviewer x present pooled unit"
        )
    if any(
        (
            annotation.case_id,
            annotation.case_sha256,
            annotation.packet_id,
            annotation.packet_sha256,
        )
        != expected[key]
        for key, annotation in observed.items()
    ):
        raise ValueError("raw annotation binds a foreign unit")


def build_formal_pilot_agreement_release(
    *,
    split_manifest: BenchmarkSplitManifestV2,
    leakage_release: LeakageComponentReleaseV3,
    lineage_curation_release: MechanismLineageCurationReleaseV3,
    frozen_case_release: FrozenCaseReleaseV3,
    pre_run_eligibility_release: PreRunEligibilityReleaseV3,
    execution_release: ExecutionReleaseV3,
    expert_registry: ExpertStudyRegistryV2,
    reviewer_manifests: tuple[ReviewerManifestV2, ...],
    private_identity_maps: tuple[PrivateIdentityMapV2, ...],
    evidence_excerpts: tuple[EvidenceExcerptV2, ...],
    blind_key: bytes,
    renderer_sha256: str,
    raw_annotations: tuple[RawExpertAnnotationV1, ...],
    assembled_at: str,
) -> FormalPilotAgreementReleaseV1:
    """Derive the formal V3 agreement denominator without caller-provided rows.

    ReviewerManifestV2 remains the review schema, but every artifact is first
    deterministically rebuilt from ExecutionV3/FrozenV3/EligibilityV3.
    """

    _precheck_formal_annotation_cover_v1(
        private_identity_maps=private_identity_maps,
        raw_annotations=raw_annotations,
    )
    assembled = _timestamp(assembled_at)
    split = _revalidate_model(split_manifest, BenchmarkSplitManifestV2)
    leakage = _revalidate_model(leakage_release, LeakageComponentReleaseV3)
    curation = _revalidate_model(
        lineage_curation_release, MechanismLineageCurationReleaseV3
    )
    maps = tuple(
        _revalidate_model(item, PrivateIdentityMapV2)
        for item in private_identity_maps
    )
    manifests = tuple(
        _revalidate_model(item, ReviewerManifestV2)
        for item in reviewer_manifests
    )
    annotations = tuple(
        _revalidate_model(item, RawExpertAnnotationV1)
        for item in raw_annotations
    )
    excerpts = tuple(
        _revalidate_model(item, EvidenceExcerptV2) for item in evidence_excerpts
    )

    # This must precede any raw-label denominator derivation.  Readdressing a
    # tampered public excerpt/packet cannot survive the deterministic rebuild.
    assert_reviewer_release_exact_coverage_v2(
        frozen_case_release=frozen_case_release,
        execution_release=execution_release,
        pre_run_eligibility_release=pre_run_eligibility_release,
        expert_registry=expert_registry,
        reviewer_manifests=manifests,
        private_identity_maps=maps,
        evidence_excerpts=excerpts,
        blind_key=blind_key,
        renderer_sha256=renderer_sha256,
    )
    # The exact reviewer replay above reconstructs and validates ExecutionV3,
    # FrozenV3, EligibilityV3, and ExpertRegistryV2.  Pydantic returns the same
    # immutable instances here; forcing a second dump-and-validate traversal
    # would repeat the entire 120-budget graph before the dedicated execution
    # chronology verifier below.
    frozen = FrozenCaseReleaseV3.model_validate(frozen_case_release)
    eligibility = PreRunEligibilityReleaseV3.model_validate(
        pre_run_eligibility_release
    )
    execution = ExecutionReleaseV3.model_validate(execution_release)
    registry = ExpertStudyRegistryV2.model_validate(expert_registry)
    assert_formal_mechanism_lineage_registry_v3(
        registry=leakage.mechanism_lineage_registry,
        curation_release=curation,
    )
    pool = (
        frozen.pre_run_eligibility_release.assignment_release
        .candidate_pool_release
    )
    assignment_curation = (
        pool.mechanism_lineage_assignment_curation_release
    )
    if curation != pool.mechanism_lineage_curation_release:
        raise ValueError(
            "formal Pilot agreement uses curation outside the frozen candidate pool"
        )
    if (
        execution.pre_budget_closure_release
        .lineage_assignment_curation_release_id,
        execution.pre_budget_closure_release
        .lineage_assignment_curation_release_sha256,
    ) != (
        pool.mechanism_lineage_assignment_curation_release.release_id,
        pool.mechanism_lineage_assignment_curation_release.release_sha256,
    ):
        raise ValueError(
            "formal Pilot agreement execution crosswires assignment curation"
        )

    phase = execution.execution_matrix.phase
    if phase not in {ExecutionPhase.PILOT_R1, ExecutionPhase.PILOT_R2}:
        raise ValueError("formal Pilot agreement requires a Pilot execution phase")
    review_round = 1 if phase is ExecutionPhase.PILOT_R1 else 2
    expected_split = (
        BenchmarkSplit.PILOT_R1
        if review_round == 1
        else BenchmarkSplit.PILOT_R2
    )
    if len(split.cases) != 30 or any(
        item.split is not expected_split for item in split.cases
    ):
        raise ValueError("formal Pilot agreement requires the exact 30-case round split")
    if (leakage.split_manifest_id, leakage.split_manifest_sha256) != (
        split.manifest_id,
        split.manifest_sha256,
    ):
        raise ValueError("formal Pilot leakage release binds a foreign split")
    if (
        frozen.split_manifest,
        frozen.leakage_release,
        frozen.expert_registry,
    ) != (split, leakage, registry):
        raise ValueError("formal Pilot frozen release drifts from its V3 chain")
    if frozen.pre_run_eligibility_release != eligibility:
        raise ValueError("formal Pilot frozen release embeds foreign eligibility")
    if execution.frozen_case_release != frozen or (
        execution.pre_run_eligibility_release != eligibility
    ):
        raise ValueError("formal Pilot execution binds a foreign V3 case chain")
    if (
        registry.split_manifest_id,
        registry.split_manifest_sha256,
    ) != (split.manifest_id, split.manifest_sha256):
        raise ValueError("formal Pilot expert registry binds a foreign split")
    assert_pre_run_eligibility_precedes_execution_v3(
        frozen_case_release=frozen,
        eligibility_release=eligibility,
        execution_release=execution,
    )

    component_by_case = component_assignments_v3(leakage)
    split_case_by_id = {item.case_id: item for item in split.cases}
    if set(component_by_case) != set(split_case_by_id):
        raise ValueError("LeakageV3 components do not exactly cover Pilot cases")
    assignment_by_case = {item.case_id: item for item in registry.assignments}
    if set(assignment_by_case) != set(split_case_by_id):
        raise ValueError("expert assignments do not exactly cover Pilot cases")
    for case_id, assignment in assignment_by_case.items():
        case = split_case_by_id[case_id]
        if assignment.case_sha256 != case.case_sha256:
            raise ValueError("expert assignment carries a foreign case SHA-256")

    selected_by_case = {
        item.selected_case_id: item
        for item in eligibility.active_selections
        if item.selected_case_id is not None
    }
    if set(selected_by_case) != set(split_case_by_id):
        raise ValueError("eligibility selections do not exactly cover Pilot cases")
    candidate_by_id = {
        item.candidate_id: item
        for item in eligibility.assignment_release.candidate_pool_release.candidates
    }
    for case_id, selection in selected_by_case.items():
        case = split_case_by_id[case_id]
        if selection.selected_case_sha256 != case.case_sha256:
            raise ValueError("active selection differs from frozen split case")
        candidate = candidate_by_id.get(selection.selected_candidate_id)
        if candidate is None or (
            candidate.candidate_sha256,
            candidate.case.case_id,
            candidate.case.case_sha256,
        ) != (
            selection.selected_candidate_sha256,
            case.case_id,
            case.case_sha256,
        ):
            raise ValueError("active selection differs from its frozen candidate")

    assert_leakage_split_closure_v3(
        cases=tuple(candidate_by_id[item.selected_candidate_id].case for item in selected_by_case.values()),
        split_manifest=split,
        release=leakage,
    )

    expected_reviewer_ids = tuple(
        sorted(
            {
                reviewer_id
                for assignment in registry.assignments
                for reviewer_id in assignment.reviewer_ids
            }
        )
    )
    map_ids = tuple(item.identity_map_id for item in maps)
    map_shas = tuple(item.identity_map_sha256 for item in maps)
    map_reviewers = tuple(item.expert_id for item in maps)
    if (
        len(map_ids) != len(set(map_ids))
        or len(map_shas) != len(set(map_shas))
        or len(map_reviewers) != len(set(map_reviewers))
        or set(map_reviewers) != set(expected_reviewer_ids)
    ):
        raise ValueError("private maps must exactly cover assigned reviewers once")
    manifest_pairs = tuple(
        (item.reviewer_manifest_id, item.reviewer_manifest_sha256) for item in maps
    )
    manifest_by_id = {item.manifest_id: item for item in manifests}
    if (
        len(manifest_pairs) != len(set(manifest_pairs))
        or len(manifest_by_id) != len(manifests)
        or set(item[0] for item in manifest_pairs) != set(manifest_by_id)
    ):
        raise ValueError("public manifests and private maps must be one-to-one")
    map_by_reviewer = {item.expert_id: item for item in maps}
    map_seals = {item.sealed_at for item in maps}
    manifest_seals = {item.sealed_at for item in manifests}
    if len(map_seals) != 1 or map_seals != manifest_seals or any(
        _timestamp(item.sealed_at) < _timestamp(execution.assembled_at)
        for item in maps
    ):
        raise ValueError("native V2 reviewer artifacts require one post-execution seal")

    case_entry_by_reviewer_case: dict[tuple[str, str], object] = {}
    for private_map in maps:
        manifest = manifest_by_id[private_map.reviewer_manifest_id]
        if (
            private_map.reviewer_manifest_sha256,
            private_map.blinded_reviewer_id,
        ) != (manifest.manifest_sha256, manifest.blinded_reviewer_id):
            raise ValueError("private V2 map binds a foreign public reviewer manifest")
        if (
            private_map.execution_release_id,
            private_map.execution_release_sha256,
            private_map.expert_registry_id,
            private_map.expert_registry_sha256,
            private_map.frozen_case_release_id,
            private_map.frozen_case_release_sha256,
            private_map.pre_run_eligibility_release_id,
            private_map.pre_run_eligibility_release_sha256,
        ) != (
            execution.release_id,
            execution.release_sha256,
            registry.registry_id,
            registry.registry_sha256,
            frozen.release_id,
            frozen.release_sha256,
            eligibility.release_id,
            eligibility.release_sha256,
        ):
            raise ValueError("private reviewer map binds a foreign V2 artifact")
        assigned_cases = {
            item.case_id
            for item in registry.assignments
            if private_map.expert_id in item.reviewer_ids
        }
        if {item.case_id for item in private_map.case_entries} != assigned_cases:
            raise ValueError("private reviewer map omits or adds an assigned case")
        projection_by_id = {
            item.reviewer_case_projection_id: item
            for item in manifest.case_projections
        }
        if set(projection_by_id) != {
            item.reviewer_case_projection_id for item in private_map.case_entries
        }:
            raise ValueError("public reviewer cases differ from the private case map")
        for case_entry in private_map.case_entries:
            selection = selected_by_case[case_entry.case_id]
            case = split_case_by_id[case_entry.case_id]
            if (
                case_entry.frozen_candidate_id,
                case_entry.frozen_candidate_sha256,
                case_entry.case_sha256,
            ) != (
                selection.selected_candidate_id,
                selection.selected_candidate_sha256,
                case.case_sha256,
            ):
                raise ValueError("private case map differs from active V2 selection")
            projection = projection_by_id[case_entry.reviewer_case_projection_id]
            if (
                case_entry.reviewer_case_projection_sha256,
                case_entry.blinded_case_id,
            ) != (
                projection.reviewer_case_projection_sha256,
                projection.blinded_case_id,
            ):
                raise ValueError("private case map differs from public case projection")
            case_entry_by_reviewer_case[
                (private_map.expert_id, case_entry.case_id)
            ] = case_entry

    cell_by_id = {item.cell_id: item for item in execution.execution_matrix.cells}
    ranking_by_id = {item.ranking_id: item for item in execution.rankings}
    packet_by_id = {item.packet_id: item for item in execution.hypothesis_packets}
    authoritative_evidence: dict[tuple[str, str, str], object] = {}
    for terminal in execution.terminal_results:
        for bundle in terminal.source_receipt_bundles:
            for receipt in bundle.evidence_links:
                key = (
                    receipt.cell_id,
                    receipt.packet_id,
                    receipt.evidence_link_id,
                )
                if key in authoritative_evidence:
                    raise ValueError("execution repeats one evidence-link receipt")
                authoritative_evidence[key] = receipt
    expected_entries: dict[
        tuple[str, str, int], tuple[object, object, object, object]
    ] = {}
    for projection in execution.top5_projections:
        cell = cell_by_id[projection.cell_id]
        ranking = (
            None
            if projection.ranking_id is None
            else ranking_by_id[projection.ranking_id]
        )
        for position in projection.positions:
            if position.packet_id is None:
                continue
            if ranking is None:
                raise ValueError("present Pilot position lacks an execution ranking")
            ranked = ranking.positions[position.position - 1]
            if (ranked.packet_id, ranked.packet_sha256) != (
                position.packet_id,
                position.packet_sha256,
            ):
                raise ValueError("Top-5 projection differs from its ranking")
            packet = packet_by_id[position.packet_id]
            if (
                packet.packet_sha256,
                packet.case_id,
                packet.case_sha256,
            ) != (
                position.packet_sha256,
                cell.case_id,
                cell.case_sha256,
            ):
                raise ValueError("execution packet differs from its Pilot cell")
            for reviewer_id in assignment_by_case[cell.case_id].reviewer_ids:
                key = (reviewer_id, cell.cell_id, position.position)
                if key in expected_entries:
                    raise ValueError("execution contributes a duplicate reviewer position")
                expected_entries[key] = (cell, ranking, position, packet)
    if not expected_entries:
        raise ValueError("formal Pilot agreement requires at least one present pooled unit")

    observed_entries: dict[tuple[str, str, int], PrivatePositionMapEntryV2] = {}
    for private_map in maps:
        manifest = manifest_by_id[private_map.reviewer_manifest_id]
        public_packet_by_id = {
            item.reviewer_packet_id: item for item in manifest.packets
        }
        if set(public_packet_by_id) != {
            item.reviewer_packet_id for item in private_map.entries
        }:
            raise ValueError("public reviewer packets differ from private present units")
        for entry in private_map.entries:
            public_packet = public_packet_by_id[entry.reviewer_packet_id]
            if (
                entry.reviewer_packet_sha256,
                entry.blinded_unit_id,
                entry.reviewer_case_projection_id,
            ) != (
                public_packet.reviewer_packet_sha256,
                public_packet.blinded_unit_id,
                public_packet.reviewer_case_projection_id,
            ):
                raise ValueError("private position differs from public reviewer packet")
            key = (private_map.expert_id, entry.cell_id, entry.selection_rank)
            if key in observed_entries:
                raise ValueError("duplicate private reviewer position")
            observed_entries[key] = entry
    if set(observed_entries) != set(expected_entries):
        raise ValueError(
            "private reviewer positions do not exactly cover every present assigned unit"
        )

    packet_to_pool: dict[tuple[str, str], str] = {}
    pool_to_packet: dict[str, tuple[str, str]] = {}
    reviewer_packet_identity: dict[
        tuple[str, str, str], tuple[str, str, str]
    ] = {}
    entries_by_packet: dict[
        tuple[str, str], dict[str, PrivatePositionMapEntryV2]
    ] = {}
    for key, expected in expected_entries.items():
        reviewer_id, _cell_id, _position = key
        entry = observed_entries[key]
        cell, ranking, position, packet = expected
        case_entry = case_entry_by_reviewer_case[(reviewer_id, cell.case_id)]
        if (
            entry.cell_id,
            entry.system_id,
            entry.system_config_id,
            entry.system_config_sha256,
            entry.run_id,
            entry.ranking_id,
            entry.ranking_sha256,
            entry.selection_rank,
            entry.case_id,
            entry.case_sha256,
            entry.packet_id,
            entry.packet_sha256,
            entry.reviewer_case_projection_id,
            entry.system_proposed_structure_group_id,
            entry.system_proposed_hypothesis_group_id,
        ) != (
            cell.cell_id,
            cell.system_id,
            cell.system_config_id,
            cell.system_config_sha256,
            ranking.run_id,
            ranking.ranking_id,
            ranking.ranking_sha256,
            position.position,
            cell.case_id,
            cell.case_sha256,
            packet.packet_id,
            packet.packet_sha256,
            case_entry.reviewer_case_projection_id,
            packet.strict_structure_group_id,
            packet.strict_hypothesis_group_id,
        ):
            raise ValueError("private reviewer position differs from execution V2")
        _assert_private_evidence_exact(
            entry,
            packet=packet,
            authoritative_receipts=authoritative_evidence,
        )
        packet_key = (packet.packet_id, packet.packet_sha256)
        if packet_to_pool.setdefault(packet_key, entry.pooled_unit_id) != (
            entry.pooled_unit_id
        ):
            raise ValueError("reviewers disagree on pooled-unit identity")
        if pool_to_packet.setdefault(entry.pooled_unit_id, packet_key) != packet_key:
            raise ValueError("pooled-unit identity aliases multiple packets")
        reviewer_key = (reviewer_id, *packet_key)
        public_identity = (
            entry.reviewer_packet_id,
            entry.reviewer_packet_sha256,
            entry.blinded_unit_id,
        )
        if reviewer_packet_identity.setdefault(reviewer_key, public_identity) != (
            public_identity
        ):
            raise ValueError("one reviewer sees inconsistent identity for one packet")
        entries_by_packet.setdefault(packet_key, {})[reviewer_id] = entry

    expected_annotation_keys = {
        (reviewer_id, identity[2])
        for (reviewer_id, _packet_id, _packet_sha), identity in (
            reviewer_packet_identity.items()
        )
    }
    annotation_by_key: dict[tuple[str, str], RawExpertAnnotationV1] = {}
    annotation_ids: dict[str, str] = {}
    annotation_shas: dict[str, str] = {}
    for annotation in annotations:
        key = (annotation.reviewer_id, annotation.blinded_unit_id)
        if key in annotation_by_key:
            raise ValueError("one reviewer supplied duplicate labels for a blinded unit")
        annotation_by_key[key] = annotation
        if annotation_ids.setdefault(
            annotation.annotation_id, annotation.annotation_sha256
        ) != annotation.annotation_sha256 or annotation_shas.setdefault(
            annotation.annotation_sha256, annotation.annotation_id
        ) != annotation.annotation_id:
            raise ValueError("raw annotation IDs and SHA-256 values are not one-to-one")
    if set(annotation_by_key) != expected_annotation_keys:
        raise ValueError(
            "raw annotations do not exactly cover assigned reviewer x present pooled unit"
        )

    units: list[FormalPilotAgreementUnitV1] = []
    for packet_key in sorted(entries_by_packet):
        reviewer_entries = entries_by_packet[packet_key]
        packet = packet_by_id[packet_key[0]]
        reviewers = assignment_by_case[packet.case_id].reviewer_ids
        if set(reviewer_entries) != set(reviewers):
            raise ValueError("pooled unit does not have exactly two assigned reviewers")
        labels: list[FormalPilotRawLabelRefV1] = []
        for reviewer_id in reviewers:
            entry = reviewer_entries[reviewer_id]
            private_map = map_by_reviewer[reviewer_id]
            annotation = annotation_by_key[(reviewer_id, entry.blinded_unit_id)]
            if (
                annotation.case_id,
                annotation.case_sha256,
                annotation.packet_id,
                annotation.packet_sha256,
                annotation.review_round,
                annotation.annotation_guide_sha256,
            ) != (
                packet.case_id,
                packet.case_sha256,
                packet.packet_id,
                packet.packet_sha256,
                review_round,
                registry.annotation_guide_sha256,
            ):
                raise ValueError("raw annotation binds a foreign unit, round, or guide")
            if _timestamp(private_map.sealed_at) >= _timestamp(annotation.started_at):
                raise ValueError("raw annotation did not start after reviewer-map seal")
            if _timestamp(annotation.submitted_at) > assembled:
                raise ValueError("raw annotation follows agreement assembly")
            expected_evidence_ids = tuple(
                sorted(item.evidence_link_id for item in entry.evidence_map)
            )
            observed_evidence_ids = tuple(
                item.evidence_link_id for item in annotation.evidence_judgments
            )
            if (
                annotation.assessability is Assessability.ASSESSABLE
                and observed_evidence_ids != expected_evidence_ids
            ):
                raise ValueError("raw assessable annotation does not judge exact evidence")
            labels.append(
                FormalPilotRawLabelRefV1(
                    reviewer_id=reviewer_id,
                    identity_map_id=private_map.identity_map_id,
                    identity_map_sha256=private_map.identity_map_sha256,
                    reviewer_manifest_id=private_map.reviewer_manifest_id,
                    reviewer_manifest_sha256=(
                        private_map.reviewer_manifest_sha256
                    ),
                    blinded_unit_id=entry.blinded_unit_id,
                    annotation_id=annotation.annotation_id,
                    annotation_sha256=annotation.annotation_sha256,
                    assessability=annotation.assessability,
                    relevance_grade=annotation.relevance_grade,
                )
            )
        ordered_labels = tuple(sorted(labels, key=lambda item: item.reviewer_id))
        assessments = tuple(item.assessability for item in ordered_labels)
        if all(item is Assessability.ASSESSABLE for item in assessments):
            ratings: tuple[int, int] | None = tuple(
                int(item.relevance_grade) for item in ordered_labels
            )
        elif all(
            item is Assessability.SYSTEM_PACKET_INVALID for item in assessments
        ):
            ratings = (0, 0)
        else:
            ratings = None
        units.append(
            FormalPilotAgreementUnitV1(
                case_id=packet.case_id,
                case_sha256=packet.case_sha256,
                leakage_component_id=component_by_case[packet.case_id],
                pooled_unit_id=packet_to_pool[packet_key],
                packet_id=packet.packet_id,
                packet_sha256=packet.packet_sha256,
                raw_labels=ordered_labels,
                ordinal_ratings=ratings,
                round_fail_closed=(
                    Assessability.CASE_INVALID in assessments or ratings is None
                ),
            )
        )
    ordered_units = tuple(
        sorted(units, key=lambda item: (item.case_id, item.pooled_unit_id))
    )
    case_bindings = tuple(
        FormalPilotCaseBindingV1(
            case_id=case.case_id,
            case_sha256=case.case_sha256,
            split=case.split,
            leakage_component_id=component_by_case[case.case_id],
            selected_candidate_id=selected_by_case[
                case.case_id
            ].selected_candidate_id,
            selected_candidate_sha256=selected_by_case[
                case.case_id
            ].selected_candidate_sha256,
            reviewer_ids=assignment_by_case[case.case_id].reviewer_ids,
        )
        for case in split.cases
    )
    map_refs = tuple(
        sorted(
            (
                PilotPrivateMapRefV1(
                    reviewer_id=item.expert_id,
                    identity_map_id=item.identity_map_id,
                    identity_map_sha256=item.identity_map_sha256,
                    reviewer_manifest_id=item.reviewer_manifest_id,
                    reviewer_manifest_sha256=item.reviewer_manifest_sha256,
                )
                for item in maps
            ),
            key=lambda item: item.reviewer_id,
        )
    )
    reviewer_replay_inputs_sha256 = canonical_sha256(
        {
            "evidence_excerpts": tuple(
                item.model_dump(mode="python", round_trip=True) for item in excerpts
            ),
            "blind_key_sha256": hashlib.sha256(blind_key).hexdigest(),
            "renderer_sha256": renderer_sha256,
        }
    )
    source_inputs_sha256 = canonical_sha256(
        {
            "split_manifest": (split.manifest_id, split.manifest_sha256),
            "leakage_release": (leakage.release_id, leakage.release_sha256),
            "lineage_curation_release": (
                curation.release_id,
                curation.release_sha256,
            ),
            "lineage_assignment_curation_release": (
                assignment_curation.release_id,
                assignment_curation.release_sha256,
            ),
            "frozen_case_release": (frozen.release_id, frozen.release_sha256),
            "pre_run_eligibility_release": (
                eligibility.release_id,
                eligibility.release_sha256,
            ),
            "execution_release": (execution.release_id, execution.release_sha256),
            "expert_registry": (registry.registry_id, registry.registry_sha256),
            "private_maps": tuple(item.model_dump(mode="python") for item in map_refs),
            "reviewer_replay_inputs_sha256": reviewer_replay_inputs_sha256,
            "raw_annotations": tuple(
                (
                    label.annotation_id,
                    label.annotation_sha256,
                    label.reviewer_id,
                    label.blinded_unit_id,
                )
                for unit in ordered_units
                for label in unit.raw_labels
            ),
        }
    )
    return _build_addressed(
        FormalPilotAgreementReleaseV1,
        id_field="release_id",
        sha_field="release_sha256",
        prefix="pilot-agreement-release-v1",
        values={
            "execution_phase": phase,
            "review_round": review_round,
            "annotation_guide_sha256": registry.annotation_guide_sha256,
            "split_manifest_id": split.manifest_id,
            "split_manifest_sha256": split.manifest_sha256,
            "leakage_release_id": leakage.release_id,
            "leakage_release_sha256": leakage.release_sha256,
            "lineage_curation_release_id": curation.release_id,
            "lineage_curation_release_sha256": curation.release_sha256,
            "lineage_assignment_curation_release": assignment_curation,
            "frozen_case_release_id": frozen.release_id,
            "frozen_case_release_sha256": frozen.release_sha256,
            "pre_run_eligibility_release_id": eligibility.release_id,
            "pre_run_eligibility_release_sha256": eligibility.release_sha256,
            "execution_release_id": execution.release_id,
            "execution_release_sha256": execution.release_sha256,
            "expert_registry_id": registry.registry_id,
            "expert_registry_sha256": registry.registry_sha256,
            "private_map_refs": map_refs,
            "reviewer_replay_inputs_sha256": reviewer_replay_inputs_sha256,
            "source_inputs_sha256": source_inputs_sha256,
            "cases": case_bindings,
            "units": ordered_units,
            "round_fail_closed": any(item.round_fail_closed for item in ordered_units),
            "assembled_at": assembled_at,
        },
    )


def assert_formal_pilot_agreement_exact_closure(
    release: FormalPilotAgreementReleaseV1,
    *,
    split_manifest: BenchmarkSplitManifestV2,
    leakage_release: LeakageComponentReleaseV3,
    lineage_curation_release: MechanismLineageCurationReleaseV3,
    frozen_case_release: FrozenCaseReleaseV3,
    pre_run_eligibility_release: PreRunEligibilityReleaseV3,
    execution_release: ExecutionReleaseV3,
    expert_registry: ExpertStudyRegistryV2,
    reviewer_manifests: tuple[ReviewerManifestV2, ...],
    private_identity_maps: tuple[PrivateIdentityMapV2, ...],
    evidence_excerpts: tuple[EvidenceExcerptV2, ...],
    blind_key: bytes,
    renderer_sha256: str,
    raw_annotations: tuple[RawExpertAnnotationV1, ...],
) -> None:
    """Rebuild and byte-semantically compare the formal agreement release."""

    value = _revalidate_model(release, FormalPilotAgreementReleaseV1)
    component_by_case = component_assignments_v3(leakage_release)
    split_by_case = {item.case_id: item for item in split_manifest.cases}
    if (
        value.split_manifest_id,
        value.split_manifest_sha256,
        value.leakage_release_id,
        value.leakage_release_sha256,
        value.lineage_curation_release_id,
        value.lineage_curation_release_sha256,
        value.lineage_assignment_curation_release,
    ) != (
        split_manifest.manifest_id,
        split_manifest.manifest_sha256,
        leakage_release.release_id,
        leakage_release.release_sha256,
        lineage_curation_release.release_id,
        lineage_curation_release.release_sha256,
        frozen_case_release.pre_run_eligibility_release.assignment_release
        .candidate_pool_release.mechanism_lineage_assignment_curation_release,
    ) or set(split_by_case) != {item.case_id for item in value.cases} or any(
        (
            item.case_sha256,
            item.split,
            item.leakage_component_id,
        )
        != (
            split_by_case[item.case_id].case_sha256,
            split_by_case[item.case_id].split,
            component_by_case[item.case_id],
        )
        for item in value.cases
    ):
        raise ValueError("formal Pilot agreement does not replay from exact inputs")
    rebuilt = build_formal_pilot_agreement_release(
        split_manifest=split_manifest,
        leakage_release=leakage_release,
        lineage_curation_release=lineage_curation_release,
        frozen_case_release=frozen_case_release,
        pre_run_eligibility_release=pre_run_eligibility_release,
        execution_release=execution_release,
        expert_registry=expert_registry,
        reviewer_manifests=reviewer_manifests,
        private_identity_maps=private_identity_maps,
        evidence_excerpts=evidence_excerpts,
        blind_key=blind_key,
        renderer_sha256=renderer_sha256,
        raw_annotations=raw_annotations,
        assembled_at=value.assembled_at,
    )
    if rebuilt != value:
        raise ValueError("formal Pilot agreement does not replay from exact inputs")


def _pilot_gate_decision(
    *, phase: ExecutionPhase, alpha: float | None, fail_closed: bool
) -> PilotAgreementGateDecision:
    if fail_closed:
        return PilotAgreementGateDecision.ROUND_FAIL_CLOSED
    if phase is ExecutionPhase.PILOT_R1:
        if alpha is not None and alpha >= PILOT_ALPHA_PASS_THRESHOLD:
            return PilotAgreementGateDecision.R1_PASS_MAIN_ALLOWED
        if alpha is not None and alpha >= PILOT_ALPHA_R1_REVISION_FLOOR:
            return (
                PilotAgreementGateDecision.R1_GUIDE_REVISION_AND_DISJOINT_R2_REQUIRED
            )
        return PilotAgreementGateDecision.STOP_R1_BELOW_0_667
    if alpha is not None and alpha >= PILOT_ALPHA_PASS_THRESHOLD:
        return PilotAgreementGateDecision.R2_PASS_MAIN_ALLOWED
    return PilotAgreementGateDecision.STOP_R2_BELOW_0_80


def _assert_agreement_leakage_context(
    *,
    agreement: FormalPilotAgreementReleaseV1,
    context: LeakageRoundClosureContextV3,
    curation: MechanismLineageCurationReleaseV3,
) -> dict[str, str]:
    assert_formal_mechanism_lineage_registry_v3(
        registry=context.release.mechanism_lineage_registry,
        curation_release=curation,
    )
    assert_leakage_split_closure_v3(
        cases=context.cases,
        split_manifest=context.split_manifest,
        release=context.release,
    )
    assignment_curation = _revalidate_model(
        agreement.lineage_assignment_curation_release,
        MechanismLineageAssignmentCurationReleaseV3,
    )
    curated_assignments = derive_formal_mechanism_lineage_assignments_v3(
        registry=context.release.mechanism_lineage_registry,
        definition_curation_release=curation,
        assignment_curation_release=assignment_curation,
    )
    context_case_ids = {item.case_id for item in context.cases}
    selected_curated_assignments = tuple(
        sorted(
            (
                item
                for item in curated_assignments
                if item.case_id in context_case_ids
            ),
            key=lambda item: (item.case_id, item.assignment_id),
        )
    )
    if selected_curated_assignments != context.release.mechanism_lineage_assignments:
        raise ValueError(
            "Pilot LeakageV3 assignments are not the exact private-curation projection"
        )
    if (
        agreement.split_manifest_id,
        agreement.split_manifest_sha256,
        agreement.leakage_release_id,
        agreement.leakage_release_sha256,
        agreement.lineage_curation_release_id,
        agreement.lineage_curation_release_sha256,
    ) != (
        context.split_manifest.manifest_id,
        context.split_manifest.manifest_sha256,
        context.release.release_id,
        context.release.release_sha256,
        curation.release_id,
        curation.release_sha256,
    ):
        raise ValueError("Pilot agreement binds a foreign exact LeakageV3 context")
    split_by_case = {item.case_id: item for item in context.split_manifest.cases}
    component_by_case = component_assignments_v3(context.release)
    if set(split_by_case) != {item.case_id for item in agreement.cases}:
        raise ValueError("Pilot agreement cases differ from exact LeakageV3 context")
    for case in agreement.cases:
        exact = split_by_case[case.case_id]
        if (case.case_sha256, case.split, case.leakage_component_id) != (
            exact.case_sha256,
            exact.split,
            component_by_case[case.case_id],
        ):
            raise ValueError("Pilot agreement carries a caller-derived case/component")
    return component_by_case


def build_formal_pilot_agreement_gate(
    *,
    agreement_release: FormalPilotAgreementReleaseV1,
    leakage_context: LeakageRoundClosureContextV3,
    lineage_curation_release: MechanismLineageCurationReleaseV3,
    evaluated_at: str,
    prior_r1_agreement_release: FormalPilotAgreementReleaseV1 | None = None,
    prior_r1_gate: FormalPilotAgreementGateReleaseV1 | None = None,
    prior_r1_leakage_context: LeakageRoundClosureContextV3 | None = None,
) -> FormalPilotAgreementGateReleaseV1:
    """Apply the fixed Pilot Gate; no threshold, seed, or rated rows are inputs."""

    _timestamp(evaluated_at)
    agreement = _revalidate_model(
        agreement_release, FormalPilotAgreementReleaseV1
    )
    context = _revalidate_model(leakage_context, LeakageRoundClosureContextV3)
    curation = _revalidate_model(
        lineage_curation_release, MechanismLineageCurationReleaseV3
    )
    component_by_case = _assert_agreement_leakage_context(
        agreement=agreement,
        context=context,
        curation=curation,
    )
    if _timestamp(evaluated_at) < _timestamp(agreement.assembled_at):
        raise ValueError("Pilot Gate evaluation precedes agreement assembly")

    prior_release: FormalPilotAgreementReleaseV1 | None = None
    prior_gate: FormalPilotAgreementGateReleaseV1 | None = None
    prior_context: LeakageRoundClosureContextV3 | None = None
    if agreement.review_round == 1:
        if any(
            item is not None
            for item in (
                prior_r1_agreement_release,
                prior_r1_gate,
                prior_r1_leakage_context,
            )
        ):
            raise ValueError("R1 Gate cannot accept prior-round artifacts")
        disjoint: bool | None = None
    else:
        if (
            prior_r1_agreement_release is None
            or prior_r1_gate is None
            or prior_r1_leakage_context is None
        ):
            raise ValueError(
                "R2 Gate requires its exact R1 agreement, Gate, and LeakageV3 context"
            )
        prior_release = _revalidate_model(
            prior_r1_agreement_release, FormalPilotAgreementReleaseV1
        )
        prior_gate = _revalidate_model(
            prior_r1_gate, FormalPilotAgreementGateReleaseV1
        )
        prior_context = _revalidate_model(
            prior_r1_leakage_context, LeakageRoundClosureContextV3
        )
        _assert_agreement_leakage_context(
            agreement=prior_release,
            context=prior_context,
            curation=curation,
        )
        if prior_release.review_round != 1 or prior_gate.review_round != 1:
            raise ValueError("R2 prior artifacts must be R1 releases")
        if (
            prior_gate.agreement_release_id,
            prior_gate.agreement_release_sha256,
            prior_gate.leakage_release_id,
            prior_gate.leakage_release_sha256,
            prior_gate.lineage_curation_release_id,
            prior_gate.lineage_curation_release_sha256,
            prior_gate.lineage_assignment_curation_release_id,
            prior_gate.lineage_assignment_curation_release_sha256,
        ) != (
            prior_release.release_id,
            prior_release.release_sha256,
            prior_context.release.release_id,
            prior_context.release.release_sha256,
            curation.release_id,
            curation.release_sha256,
            prior_release.lineage_assignment_curation_release.release_id,
            prior_release.lineage_assignment_curation_release.release_sha256,
        ):
            raise ValueError("R1 Gate binds a foreign agreement release")
        if prior_gate.decision is not (
            PilotAgreementGateDecision.R1_GUIDE_REVISION_AND_DISJOINT_R2_REQUIRED
        ):
            raise ValueError("R2 is allowed only after the R1 guide-revision branch")
        if agreement.annotation_guide_sha256 == prior_release.annotation_guide_sha256:
            raise ValueError("R2 requires a newly frozen annotation guide")
        assert_cross_round_leakage_disjoint_v3(
            round_contexts=(prior_context, context),
            lineage_curation_release=curation,
        )
        disjoint = True

    total = len(agreement.units)
    paired_invalid = 0
    unilateral_invalid = 0
    case_invalid = 0
    exact_assessability = 0
    exact_grade = 0
    rated: list[ComponentRatedUnit] = []
    for unit in agreement.units:
        assessments = tuple(item.assessability for item in unit.raw_labels)
        exact_assessability += int(assessments[0] is assessments[1])
        invalid_flags = tuple(
            item is Assessability.SYSTEM_PACKET_INVALID for item in assessments
        )
        paired_invalid += int(all(invalid_flags))
        unilateral_invalid += int(sum(invalid_flags) == 1)
        case_invalid += int(Assessability.CASE_INVALID in assessments)
        if unit.ordinal_ratings is not None:
            exact_grade += int(unit.ordinal_ratings[0] == unit.ordinal_ratings[1])
            rated.append(
                ComponentRatedUnit(
                    case_id=unit.case_id,
                    leakage_component_id=component_by_case[unit.case_id],
                    pooled_unit_id=unit.pooled_unit_id,
                    ratings=unit.ordinal_ratings,
                )
            )
    insufficient_components = len(
        {item.leakage_component_id for item in rated}
    ) < 2
    fail_closed = agreement.round_fail_closed or insufficient_components
    if fail_closed:
        alpha = lower = upper = None
        valid_replicates = undefined_replicates = 0
    else:
        interval = case_component_bootstrap_alpha(
            rated,
            replicates=PILOT_ALPHA_BOOTSTRAP_REPLICATES,
            seed=PILOT_ALPHA_BOOTSTRAP_SEED,
        )
        alpha = interval.observed_alpha
        lower = interval.lower
        upper = interval.upper
        valid_replicates = interval.valid_replicates
        undefined_replicates = interval.undefined_replicates
    decision = _pilot_gate_decision(
        phase=agreement.execution_phase,
        alpha=alpha,
        fail_closed=fail_closed,
    )
    return _build_addressed(
        FormalPilotAgreementGateReleaseV1,
        id_field="gate_id",
        sha_field="gate_sha256",
        prefix="pilot-agreement-gate-v1",
        values={
            "agreement_release_id": agreement.release_id,
            "agreement_release_sha256": agreement.release_sha256,
            "leakage_release_id": context.release.release_id,
            "leakage_release_sha256": context.release.release_sha256,
            "lineage_curation_release_id": curation.release_id,
            "lineage_curation_release_sha256": curation.release_sha256,
            "lineage_assignment_curation_release_id": (
                agreement.lineage_assignment_curation_release.release_id
            ),
            "lineage_assignment_curation_release_sha256": (
                agreement.lineage_assignment_curation_release.release_sha256
            ),
            "execution_phase": agreement.execution_phase,
            "review_round": agreement.review_round,
            "annotation_guide_sha256": agreement.annotation_guide_sha256,
            "prior_r1_agreement_release_id": (
                None if prior_release is None else prior_release.release_id
            ),
            "prior_r1_agreement_release_sha256": (
                None if prior_release is None else prior_release.release_sha256
            ),
            "prior_r1_gate_id": None if prior_gate is None else prior_gate.gate_id,
            "prior_r1_gate_sha256": (
                None if prior_gate is None else prior_gate.gate_sha256
            ),
            "prior_r1_leakage_release_id": (
                None if prior_context is None else prior_context.release.release_id
            ),
            "prior_r1_leakage_release_sha256": (
                None if prior_context is None else prior_context.release.release_sha256
            ),
            "prior_r1_lineage_assignment_curation_release_id": (
                None
                if prior_release is None
                else prior_release.lineage_assignment_curation_release.release_id
            ),
            "prior_r1_lineage_assignment_curation_release_sha256": (
                None
                if prior_release is None
                else prior_release.lineage_assignment_curation_release.release_sha256
            ),
            "r2_cases_and_components_disjoint": disjoint,
            "alpha": alpha,
            "bootstrap_lower": lower,
            "bootstrap_upper": upper,
            "valid_bootstrap_replicates": valid_replicates,
            "undefined_bootstrap_replicates": undefined_replicates,
            "total_units": total,
            "rated_units": len(rated),
            "paired_system_packet_invalid_units": paired_invalid,
            "unilateral_system_packet_invalid_units": unilateral_invalid,
            "case_invalid_units": case_invalid,
            "exact_assessability_agreement_rate": round(
                exact_assessability / total, 12
            ),
            "exact_grade_agreement_rate": round(exact_grade / total, 12),
            "round_fail_closed": fail_closed,
            "decision": decision,
            "evaluated_at": evaluated_at,
        },
    )


def assert_formal_pilot_agreement_gate_exact_closure(
    gate: FormalPilotAgreementGateReleaseV1,
    *,
    agreement_release: FormalPilotAgreementReleaseV1,
    leakage_context: LeakageRoundClosureContextV3,
    lineage_curation_release: MechanismLineageCurationReleaseV3,
    prior_r1_agreement_release: FormalPilotAgreementReleaseV1 | None = None,
    prior_r1_gate: FormalPilotAgreementGateReleaseV1 | None = None,
    prior_r1_leakage_context: LeakageRoundClosureContextV3 | None = None,
) -> None:
    value = _revalidate_model(gate, FormalPilotAgreementGateReleaseV1)
    rebuilt = build_formal_pilot_agreement_gate(
        agreement_release=agreement_release,
        leakage_context=leakage_context,
        lineage_curation_release=lineage_curation_release,
        evaluated_at=value.evaluated_at,
        prior_r1_agreement_release=prior_r1_agreement_release,
        prior_r1_gate=prior_r1_gate,
        prior_r1_leakage_context=prior_r1_leakage_context,
    )
    if rebuilt != value:
        raise ValueError("formal Pilot agreement Gate does not exactly replay")


def build_formal_analysis_input_release(
    *,
    split_manifest: BenchmarkSplitManifestV2,
    leakage_release: LeakageComponentReleaseV3,
    frozen_case_release: FrozenCaseReleaseV2,
    pre_run_eligibility_release_v2: PreRunEligibilityReleaseV2,
    execution_release: ExecutionReleaseV2,
    final_gold_release: FinalGoldReleaseV2,
    gold_exact_closure_inputs_v2: object,
    execution_phase: ExecutionPhase,
    assembled_at: str,
) -> NoReturn:
    """Reserved formal API; never falls back to legacy V1 closure.

    There are deliberately no caller-provided rows, scores, strata or component
    assignments in this signature.  Once the V2 eligibility and Gold verifier
    exist, this function will derive all of them from the supplied releases.
    """

    _timestamp(assembled_at)
    if pre_run_eligibility_release_v2 is None or gold_exact_closure_inputs_v2 is None:
        raise FormalAnalysisPrerequisiteError(
            "formal analysis requires PreRunEligibilityReleaseV2 and V2 Gold exact inputs"
        )
    split = BenchmarkSplitManifestV2.model_validate(
        split_manifest.model_dump(mode="python", round_trip=True)
    )
    leakage = LeakageComponentReleaseV3.model_validate(
        leakage_release.model_dump(mode="python", round_trip=True)
    )
    frozen = FrozenCaseReleaseV2.model_validate(
        frozen_case_release.model_dump(mode="python", round_trip=True)
    )
    execution = ExecutionReleaseV2.model_validate(
        execution_release.model_dump(mode="python", round_trip=True)
    )
    eligibility = PreRunEligibilityReleaseV2.model_validate(
        pre_run_eligibility_release_v2.model_dump(mode="python", round_trip=True)
    )
    gold = FinalGoldReleaseV2.model_validate(
        final_gold_release.model_dump(mode="python", round_trip=True)
    )
    if (leakage.split_manifest_id, leakage.split_manifest_sha256) != (
        split.manifest_id,
        split.manifest_sha256,
    ):
        raise ValueError("leakage V3 binds a different split V2")
    if (
        frozen.split_manifest,
        frozen.leakage_release,
    ) != (split, leakage):
        raise ValueError("FrozenCaseReleaseV2 drifts from split or leakage V3")
    if execution.execution_matrix.split_manifest != split:
        raise ValueError("ExecutionReleaseV2 uses a different split V2")
    if eligibility.frozen_case_release != frozen:
        raise ValueError("PreRunEligibilityReleaseV2 uses a different case freeze")
    assert_pre_run_eligibility_precedes_execution_v2(
        eligibility_release=eligibility,
        execution_release=execution,
    )
    if execution.execution_matrix.phase is not execution_phase:
        raise ValueError("requested analysis phase differs from ExecutionReleaseV2")
    if (gold.execution_release_id, gold.execution_release_sha256) != (
        execution.release_id,
        execution.release_sha256,
    ):
        raise ValueError("FinalGoldReleaseV2 binds a different execution release")
    raise FormalAnalysisPrerequisiteError(
        "formal AnalysisInput is NO-GO: the current Gold exact verifier accepts "
        "ExecutionReleaseV1/PreRunEligibilityReleaseV1/ExpertStudyRegistryV1 only; "
        "a legacy V1 bridge is forbidden"
    )


def assert_formal_analysis_input_exact_closure(
    release: AnalysisInputReleaseV1,
    *,
    split_manifest: BenchmarkSplitManifestV2,
    leakage_release: LeakageComponentReleaseV3,
    frozen_case_release: FrozenCaseReleaseV2,
    pre_run_eligibility_release_v2: PreRunEligibilityReleaseV2,
    execution_release: ExecutionReleaseV2,
    final_gold_release: FinalGoldReleaseV2,
    gold_exact_closure_inputs_v2: object,
) -> NoReturn:
    """Reject schema-only artifacts until every formal V2 dependency replays."""

    AnalysisInputReleaseV1.model_validate(
        release.model_dump(mode="python", round_trip=True)
    )
    build_formal_analysis_input_release(
        split_manifest=split_manifest,
        leakage_release=leakage_release,
        frozen_case_release=frozen_case_release,
        pre_run_eligibility_release_v2=pre_run_eligibility_release_v2,
        execution_release=execution_release,
        final_gold_release=final_gold_release,
        gold_exact_closure_inputs_v2=gold_exact_closure_inputs_v2,
        execution_phase=release.execution_phase,
        assembled_at=release.assembled_at,
    )


__all__ = [
    "AnalysisCaseBindingV1",
    "AnalysisClosureStatus",
    "AnalysisInputReleaseV1",
    "AnalysisPositionV1",
    "AnalysisReleaseKind",
    "CaseSystemAnalysisRowV1",
    "CaseSystemMetricProjectionV1",
    "FormalAnalysisPrerequisiteError",
    "FormalPilotAgreementGateReleaseV1",
    "FormalPilotAgreementReleaseV1",
    "FormalPilotAgreementUnitV1",
    "FormalPilotCaseBindingV1",
    "FormalPilotRawLabelRefV1",
    "PILOT_ALPHA_BOOTSTRAP_REPLICATES",
    "PILOT_ALPHA_BOOTSTRAP_SEED",
    "PILOT_ALPHA_PASS_THRESHOLD",
    "PILOT_ALPHA_R1_REVISION_FLOOR",
    "PilotAgreementGateDecision",
    "PilotAgreementUnitV1",
    "PilotPrivateMapRefV1",
    "PilotReviewerMapCompatibility",
    "RawReviewerLabelRefV1",
    "assert_formal_analysis_input_exact_closure",
    "assert_formal_pilot_agreement_exact_closure",
    "assert_formal_pilot_agreement_gate_exact_closure",
    "build_formal_analysis_input_release",
    "build_formal_pilot_agreement_gate",
    "build_formal_pilot_agreement_release",
    "derive_case_system_metric_projection",
]
