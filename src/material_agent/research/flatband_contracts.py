"""Content-addressed draft contracts for the flat/narrow-band benchmark.

These models describe research inputs, blinded hypothesis packets, raw expert
judgments, append-only adjudication, and leakage-safe split manifests.  They do
not replace the frozen production Inspiration V1 contracts.  The draft is not
Pilot-ready until the preregistration's red-team closure items are implemented.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Annotated, Literal

from pydantic import Field, field_validator, model_validator

from material_agent.inspiration.models import (
    Identifier,
    LongText,
    Sha256,
    ShortText,
    StrictModel,
    canonical_sha256,
    deterministic_id,
)


SOURCE_CATALOG_V1_SHA256 = (
    "57c24de8f0cf616205b03ef16231def711f2dfa9fcac86d94beede9c01b0bb1f"
)


class TargetBandClass(StrEnum):
    FB100 = "FB100"
    NB300 = "NB300"


class ObservedBandClass(StrEnum):
    FB100 = "FB100"
    NB300 = "NB300"
    BORDER500 = "BORDER500"
    OUT_OF_SCOPE = "OUT_OF_SCOPE"
    UNQUANTIFIED = "UNQUANTIFIED"


class Dimensionality(StrEnum):
    TWO_D = "2D"
    THREE_D = "3D"


class BandwidthScope(StrEnum):
    FULL_BZ_GRID = "FULL_BZ_GRID"
    WANNIER_GRID = "WANNIER_GRID"
    HSL_FULL_PATH = "HSL_FULL_PATH"
    HSL_LOCAL_WINDOW = "HSL_LOCAL_WINDOW"
    STRUCTURAL_PRIOR_ONLY = "STRUCTURAL_PRIOR_ONLY"
    SOURCE_LABEL_ONLY = "SOURCE_LABEL_ONLY"
    UNKNOWN = "UNKNOWN"


class SocState(StrEnum):
    INCLUDED = "INCLUDED"
    NOT_INCLUDED = "NOT_INCLUDED"
    UNKNOWN = "UNKNOWN"


class MagneticOrder(StrEnum):
    NONMAGNETIC = "NONMAGNETIC"
    FERROMAGNETIC = "FERROMAGNETIC"
    ANTIFERROMAGNETIC = "ANTIFERROMAGNETIC"
    NONCOLLINEAR = "NONCOLLINEAR"
    PARAMAGNETIC_OR_UNKNOWN = "PARAMAGNETIC_OR_UNKNOWN"


class EvidenceClaimType(StrEnum):
    COMPUTED = "COMPUTED"
    PARSED = "PARSED"
    HYPOTHESIS = "HYPOTHESIS"


class MechanismFamily(StrEnum):
    LATTICE_INTERFERENCE = "LATTICE_INTERFERENCE"
    LINE_GRAPH = "LINE_GRAPH"
    ORBITAL_FRUSTRATION_HYBRIDIZATION = "ORBITAL_FRUSTRATION_HYBRIDIZATION"
    SYMMETRY_INDUCED = "SYMMETRY_INDUCED"
    MOIRE_SUPERLATTICE = "MOIRE_SUPERLATTICE"
    CONFINEMENT = "CONFINEMENT"
    CORRELATION_RENORMALIZATION = "CORRELATION_RENORMALIZATION"
    STRAIN_INTERFACE_DEFECT = "STRAIN_INTERFACE_DEFECT"
    ATOMIC_ORBITAL_LOCALIZATION = "ATOMIC_ORBITAL_LOCALIZATION"
    OTHER_OR_UNKNOWN = "OTHER_OR_UNKNOWN"


class BenchmarkSplit(StrEnum):
    PILOT_R1 = "PILOT_R1"
    PILOT_R2 = "PILOT_R2"
    DEVELOPMENT = "DEVELOPMENT"
    LOCKED_IID = "LOCKED_IID"
    LOCKED_OOD = "LOCKED_OOD"


class SplitManifestKind(StrEnum):
    PILOT_R1 = "PILOT_R1"
    PILOT_R2 = "PILOT_R2"
    MAIN_120 = "MAIN_120"


class AssertedEvidenceRelation(StrEnum):
    SUPPORT = "SUPPORT"
    COUNTER = "COUNTER"
    CONTEXT = "CONTEXT"


class ExpertEvidenceRelation(StrEnum):
    VALID_SUPPORT = "VALID_SUPPORT"
    VALID_COUNTER = "VALID_COUNTER"
    CONTEXT_ONLY = "CONTEXT_ONLY"
    UNSUPPORTED = "UNSUPPORTED"
    INSUFFICIENT_PACKET = "INSUFFICIENT_PACKET"


class Assessability(StrEnum):
    ASSESSABLE = "ASSESSABLE"
    SYSTEM_PACKET_INVALID = "SYSTEM_PACKET_INVALID"
    CASE_INVALID = "CASE_INVALID"


class TriStateJudgment(StrEnum):
    PASS = "PASS"
    PARTIAL = "PARTIAL"
    FAIL = "FAIL"
    UNASSESSABLE = "UNASSESSABLE"


class BridgeVerdict(StrEnum):
    CORRECT = "CORRECT"
    CONDITIONAL = "CONDITIONAL"
    INCORRECT = "INCORRECT"
    UNASSESSABLE = "UNASSESSABLE"


class HardFailReason(StrEnum):
    INVALID_STRUCTURE = "INVALID_STRUCTURE"
    DISALLOWED_TRANSFORMATION = "DISALLOWED_TRANSFORMATION"
    HARD_CONSTRAINT_VIOLATION = "HARD_CONSTRAINT_VIOLATION"
    MECHANISM_CONTRADICTED = "MECHANISM_CONTRADICTED"
    NO_USABLE_EVIDENCE = "NO_USABLE_EVIDENCE"
    UNMARKED_INFERENCE = "UNMARKED_INFERENCE"
    MALFORMED_PACKET = "MALFORMED_PACKET"


class AdjudicationStatus(StrEnum):
    RESOLVED = "RESOLVED"
    CASE_INVALID = "CASE_INVALID"
    UNRESOLVABLE = "UNRESOLVABLE"


class ResearchRunOutcome(StrEnum):
    SUCCEEDED = "SUCCEEDED"
    PARTIAL = "PARTIAL"
    FAILED = "FAILED"


class ExpertRole(StrEnum):
    REVIEWER = "REVIEWER"
    ADJUDICATOR = "ADJUDICATOR"


def _require_rfc3339(value: str) -> str:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("timestamp must be RFC3339-compatible") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("timestamp must include a UTC offset")
    return value


def _require_sorted_unique(values: tuple[str, ...], label: str) -> None:
    if values != tuple(sorted(set(values))):
        raise ValueError(f"{label} must be sorted and unique")


def observed_band_class(*, bandwidth_e_v: float | None) -> ObservedBandClass:
    if bandwidth_e_v is None:
        return ObservedBandClass.UNQUANTIFIED
    if bandwidth_e_v <= 0.10:
        return ObservedBandClass.FB100
    if bandwidth_e_v <= 0.30:
        return ObservedBandClass.NB300
    if bandwidth_e_v <= 0.50:
        return ObservedBandClass.BORDER500
    return ObservedBandClass.OUT_OF_SCOPE


class SourceRecordRefV1(StrictModel):
    source_id: Identifier
    source_record_id: Identifier
    canonical_url: Annotated[str, Field(min_length=9, max_length=1_024)]
    source_version: ShortText
    license_expression: ShortText
    accessed_at: Annotated[str, Field(min_length=20, max_length=40)]
    raw_sha256: Sha256 | None = None
    public_redistribution_allowed: bool

    @field_validator("canonical_url")
    @classmethod
    def validate_url(cls, value: str) -> str:
        if not value.startswith("https://"):
            raise ValueError("source URL must use HTTPS")
        return value

    @field_validator("accessed_at")
    @classmethod
    def validate_accessed_at(cls, value: str) -> str:
        return _require_rfc3339(value)


class FlatBandEvidenceV1(StrictModel):
    evidence_id: Identifier
    source_record: SourceRecordRefV1
    claim_type: EvidenceClaimType
    bandwidth_e_v: Annotated[float, Field(ge=0.0, le=100.0)] | None = None
    fermi_distance_e_v: Annotated[float, Field(ge=0.0, le=100.0)] | None = None
    observed_class: ObservedBandClass
    bandwidth_scope: BandwidthScope
    k_sampling: ShortText
    isolation_gap_e_v: Annotated[float, Field(ge=-100.0, le=100.0)] | None = None
    soc_state: SocState
    magnetic_order: MagneticOrder
    spin_channel: Annotated[str, Field(min_length=1, max_length=64)] | None = None
    hubbard_u_e_v: Annotated[float, Field(ge=0.0, le=50.0)] | None = None
    band_tracking_method: ShortText
    evidence_sha256: Sha256
    scientific_conclusion: Literal[False] = False

    @model_validator(mode="after")
    def validate_evidence(self) -> "FlatBandEvidenceV1":
        quantitative = self.bandwidth_e_v is not None
        if quantitative != (self.fermi_distance_e_v is not None):
            raise ValueError("bandwidth and Fermi distance must be present together")
        if self.observed_class is not observed_band_class(
            bandwidth_e_v=self.bandwidth_e_v
        ):
            raise ValueError("observed class differs from the frozen bandwidth strata")
        if self.bandwidth_scope is BandwidthScope.STRUCTURAL_PRIOR_ONLY and quantitative:
            raise ValueError("structural priors cannot carry quantitative bandwidth")
        if self.claim_type is EvidenceClaimType.HYPOTHESIS and quantitative:
            raise ValueError("hypothesis evidence cannot carry computed bandwidth values")
        expected = canonical_sha256(
            self.model_dump(
                mode="python", exclude={"evidence_id", "evidence_sha256"}
            )
        )
        if self.evidence_sha256 != expected:
            raise ValueError("flat-band evidence SHA-256 does not match content")
        if self.evidence_id != deterministic_id(
            "band-evidence", {"evidence_sha256": expected}
        ):
            raise ValueError("flat-band evidence ID does not match its SHA-256")
        return self


class FlatBandBenchmarkCaseV1(StrictModel):
    schema_version: Literal["flatband-benchmark-case-v1"] = (
        "flatband-benchmark-case-v1"
    )
    case_id: Identifier
    case_sha256: Sha256
    source_catalog_sha256: Literal[SOURCE_CATALOG_V1_SHA256] = (
        SOURCE_CATALOG_V1_SHA256
    )
    parent_label: ShortText
    formula: Annotated[str, Field(min_length=1, max_length=128)]
    structure_sha256: Sha256
    source_records: Annotated[
        tuple[SourceRecordRefV1, ...], Field(min_length=1, max_length=16)
    ]
    target_class: TargetBandClass
    target_fermi_distance_max_e_v: Literal[1.0] = 1.0
    dimensionality: Dimensionality
    frozen_request: LongText
    frozen_requirement_sha256: Sha256
    hard_constraints: Annotated[tuple[ShortText, ...], Field(max_length=64)] = ()
    soft_preferences: Annotated[tuple[ShortText, ...], Field(max_length=64)] = ()
    forbidden_transformations: Annotated[
        tuple[Identifier, ...], Field(max_length=64)
    ] = ()
    seed_evidence: Annotated[
        tuple[FlatBandEvidenceV1, ...], Field(max_length=32)
    ] = ()
    primary_mechanism_stratum: MechanismFamily
    leakage_group_ids: Annotated[
        tuple[Identifier, ...], Field(min_length=1, max_length=64)
    ]
    public_release_allowed: bool
    scientific_conclusion: Literal[False] = False

    @model_validator(mode="after")
    def validate_case(self) -> "FlatBandBenchmarkCaseV1":
        source_keys = tuple(
            (item.source_id, item.source_record_id) for item in self.source_records
        )
        if source_keys != tuple(sorted(set(source_keys))):
            raise ValueError("case source records must be key-sorted and unique")
        for label, values in (
            ("hard constraints", self.hard_constraints),
            ("soft preferences", self.soft_preferences),
            ("forbidden transformations", self.forbidden_transformations),
            ("leakage groups", self.leakage_group_ids),
        ):
            _require_sorted_unique(values, label)
        evidence_ids = tuple(item.evidence_id for item in self.seed_evidence)
        if evidence_ids != tuple(sorted(set(evidence_ids))):
            raise ValueError("seed evidence must be evidence-ID sorted and unique")
        semantic = self.model_dump(
            mode="python", exclude={"case_id", "case_sha256"}
        )
        expected_sha256 = canonical_sha256(semantic)
        if self.case_sha256 != expected_sha256:
            raise ValueError("case SHA-256 does not match semantic content")
        if self.case_id != deterministic_id(
            "flatband-case", {"case_sha256": expected_sha256}
        ):
            raise ValueError("case ID does not match case SHA-256")
        return self


class EvidenceSpanRefV1(StrictModel):
    evidence_link_id: Identifier
    source_id: Identifier
    source_record_id: Identifier
    source_url: Annotated[str, Field(min_length=9, max_length=1_024)]
    span_id: Identifier
    span_sha256: Sha256
    asserted_relation: AssertedEvidenceRelation
    claim_summary: ShortText
    private_text_artifact_uri: Annotated[
        str, Field(min_length=12, max_length=512)
    ] | None = None

    @field_validator("source_url")
    @classmethod
    def validate_source_url(cls, value: str) -> str:
        if not value.startswith("https://"):
            raise ValueError("evidence source URL must use HTTPS")
        return value

    @field_validator("private_text_artifact_uri")
    @classmethod
    def validate_private_uri(cls, value: str | None) -> str | None:
        if value is not None and not value.startswith("artifact://"):
            raise ValueError("private evidence text must use an artifact URI")
        return value


class FalsificationPlanV1(StrictModel):
    observable: ShortText
    method: LongText
    pass_condition: LongText
    fail_condition: LongText


class HypothesisPacketV1(StrictModel):
    schema_version: Literal["flatband-hypothesis-packet-v1"] = (
        "flatband-hypothesis-packet-v1"
    )
    packet_id: Identifier
    packet_sha256: Sha256
    case_id: Identifier
    case_sha256: Sha256
    candidate_structure_sha256: Sha256
    strict_structure_group_id: Identifier
    strict_hypothesis_group_id: Identifier
    transformation_operator_id: Identifier
    transformation_summary: LongText
    source_domain: ShortText
    mechanism_family: MechanismFamily
    shared_invariant: LongText
    transfer_principle: LongText
    required_conditions: Annotated[
        tuple[ShortText, ...], Field(min_length=1, max_length=32)
    ]
    breaking_conditions: Annotated[
        tuple[ShortText, ...], Field(min_length=1, max_length=32)
    ]
    evidence_links: Annotated[
        tuple[EvidenceSpanRefV1, ...], Field(min_length=1, max_length=64)
    ]
    contradictions: Annotated[tuple[ShortText, ...], Field(max_length=32)] = ()
    falsification: FalsificationPlanV1
    claim_type: Literal["HYPOTHESIS"] = "HYPOTHESIS"
    validated_material: Literal[False] = False
    scientific_conclusion: Literal[False] = False

    @model_validator(mode="after")
    def validate_packet(self) -> "HypothesisPacketV1":
        for label, values in (
            ("required conditions", self.required_conditions),
            ("breaking conditions", self.breaking_conditions),
            ("contradictions", self.contradictions),
        ):
            _require_sorted_unique(values, label)
        link_ids = tuple(item.evidence_link_id for item in self.evidence_links)
        if link_ids != tuple(sorted(set(link_ids))):
            raise ValueError("evidence links must be link-ID sorted and unique")
        semantic = self.model_dump(
            mode="python", exclude={"packet_id", "packet_sha256"}
        )
        expected_sha256 = canonical_sha256(semantic)
        if self.packet_sha256 != expected_sha256:
            raise ValueError("packet SHA-256 does not match semantic content")
        if self.packet_id != deterministic_id(
            "hypothesis-packet", {"packet_sha256": expected_sha256}
        ):
            raise ValueError("packet ID does not match packet SHA-256")
        return self


class RankedPacketRefV1(StrictModel):
    selection_rank: Annotated[int, Field(ge=1, le=5)]
    packet_id: Identifier
    packet_sha256: Sha256
    strict_hypothesis_group_id: Identifier


class SystemRankingV1(StrictModel):
    schema_version: Literal["flatband-system-ranking-v1"] = (
        "flatband-system-ranking-v1"
    )
    ranking_id: Identifier
    ranking_sha256: Sha256
    system_id: Identifier
    system_config_sha256: Sha256
    run_id: Identifier
    case_id: Identifier
    case_sha256: Sha256
    requested_top_k: Literal[5] = 5
    ranked_packets: Annotated[tuple[RankedPacketRefV1, ...], Field(max_length=5)] = ()
    underfill_reason_codes: Annotated[
        tuple[Identifier, ...], Field(max_length=16)
    ] = ()
    cost_ledger_sha256: Sha256

    @model_validator(mode="after")
    def validate_ranking(self) -> "SystemRankingV1":
        ranks = tuple(item.selection_rank for item in self.ranked_packets)
        if ranks != tuple(range(1, len(self.ranked_packets) + 1)):
            raise ValueError("ranking positions must be contiguous from one")
        packet_ids = tuple(item.packet_id for item in self.ranked_packets)
        if len(packet_ids) != len(set(packet_ids)):
            raise ValueError("a ranking cannot repeat an exact packet ID")
        _require_sorted_unique(self.underfill_reason_codes, "underfill reasons")
        if len(self.ranked_packets) < 5 and not self.underfill_reason_codes:
            raise ValueError("underfilled ranking requires a reason code")
        if len(self.ranked_packets) == 5 and self.underfill_reason_codes:
            raise ValueError("complete ranking cannot carry an underfill reason")
        semantic = self.model_dump(
            mode="python", exclude={"ranking_id", "ranking_sha256"}
        )
        expected_sha256 = canonical_sha256(semantic)
        if self.ranking_sha256 != expected_sha256:
            raise ValueError("ranking SHA-256 does not match semantic content")
        if self.ranking_id != deterministic_id(
            "system-ranking", {"ranking_sha256": expected_sha256}
        ):
            raise ValueError("ranking ID does not match ranking SHA-256")
        return self


class RankingContributionV1(StrictModel):
    system_id: Identifier
    run_id: Identifier
    ranking_id: Identifier
    ranking_sha256: Sha256
    selection_rank: Annotated[int, Field(ge=1, le=5)]


class BlindedEvaluationUnitV1(StrictModel):
    annotation_order: Annotated[int, Field(ge=1, le=100_000)]
    blinded_unit_id: Identifier
    case_id: Identifier
    case_sha256: Sha256
    packet_id: Identifier
    packet_sha256: Sha256
    strict_hypothesis_group_id: Identifier
    contributions: Annotated[
        tuple[RankingContributionV1, ...], Field(min_length=1, max_length=64)
    ]

    @model_validator(mode="after")
    def validate_contributions(self) -> "BlindedEvaluationUnitV1":
        keys = tuple(
            (
                item.system_id,
                item.run_id,
                item.ranking_id,
                item.selection_rank,
            )
            for item in self.contributions
        )
        if keys != tuple(sorted(set(keys))):
            raise ValueError("ranking contributions must be key-sorted and unique")
        return self


class EvidenceJudgmentV1(StrictModel):
    evidence_link_id: Identifier
    expert_relation: ExpertEvidenceRelation
    scope_match: bool
    overclaim: bool
    reason_code: Identifier


class BridgeJudgmentV1(StrictModel):
    source_mechanism: TriStateJudgment
    shared_invariant: TriStateJudgment
    target_mapping: TriStateJudgment
    transferable_control: TriStateJudgment
    required_conditions: TriStateJudgment
    breaking_conditions: TriStateJudgment
    contradiction_handling: TriStateJudgment
    overall: BridgeVerdict

    @model_validator(mode="after")
    def validate_overall(self) -> "BridgeJudgmentV1":
        core = (
            self.source_mechanism,
            self.shared_invariant,
            self.target_mapping,
            self.required_conditions,
        )
        all_values = (
            *core,
            self.transferable_control,
            self.breaking_conditions,
            self.contradiction_handling,
        )
        if self.overall is BridgeVerdict.CORRECT and any(
            value is not TriStateJudgment.PASS for value in core
        ):
            raise ValueError("correct bridge requires every core judgment to pass")
        if self.overall is BridgeVerdict.CORRECT and any(
            value is TriStateJudgment.FAIL for value in all_values
        ):
            raise ValueError("correct bridge cannot contain a failed judgment")
        if self.overall is BridgeVerdict.UNASSESSABLE and any(
            value is not TriStateJudgment.UNASSESSABLE for value in all_values
        ):
            raise ValueError("unassessable bridge requires unassessable subjudgments")
        return self


class RawExpertAnnotationV1(StrictModel):
    schema_version: Literal["flatband-raw-expert-annotation-v1"] = (
        "flatband-raw-expert-annotation-v1"
    )
    annotation_id: Identifier
    annotation_sha256: Sha256
    blinded_unit_id: Identifier
    case_id: Identifier
    case_sha256: Sha256
    packet_id: Identifier
    packet_sha256: Sha256
    reviewer_id: Identifier
    review_round: Annotated[int, Field(ge=1, le=2)]
    annotation_guide_sha256: Sha256
    assessability: Assessability
    relevance_grade: Annotated[int, Field(ge=0, le=3)] | None
    evidence_valid: bool | None
    evidence_judgments: Annotated[
        tuple[EvidenceJudgmentV1, ...], Field(max_length=64)
    ] = ()
    bridge_judgment: BridgeJudgmentV1 | None
    mechanism_family: MechanismFamily | None
    strict_hypothesis_group_id: Identifier | None
    hard_fail_reasons: Annotated[
        tuple[HardFailReason, ...], Field(max_length=16)
    ] = ()
    confidence: Annotated[int, Field(ge=1, le=5)] | None
    rationale: LongText
    started_at: Annotated[str, Field(min_length=20, max_length=40)]
    submitted_at: Annotated[str, Field(min_length=20, max_length=40)]
    sealed: Literal[True] = True
    scientific_conclusion: Literal[False] = False

    @field_validator("started_at", "submitted_at")
    @classmethod
    def validate_timestamp(cls, value: str) -> str:
        return _require_rfc3339(value)

    @model_validator(mode="after")
    def validate_annotation(self) -> "RawExpertAnnotationV1":
        link_ids = tuple(item.evidence_link_id for item in self.evidence_judgments)
        if link_ids != tuple(sorted(set(link_ids))):
            raise ValueError("evidence judgments must be link-ID sorted and unique")
        reason_values = tuple(item.value for item in self.hard_fail_reasons)
        if reason_values != tuple(sorted(set(reason_values))):
            raise ValueError("hard-fail reasons must be enum-value sorted and unique")
        if datetime.fromisoformat(self.submitted_at.replace("Z", "+00:00")) < datetime.fromisoformat(
            self.started_at.replace("Z", "+00:00")
        ):
            raise ValueError("annotation submission precedes start")

        substantive = (
            self.relevance_grade,
            self.evidence_valid,
            self.bridge_judgment,
            self.mechanism_family,
            self.strict_hypothesis_group_id,
            self.confidence,
        )
        if self.assessability is Assessability.CASE_INVALID:
            if any(value is not None for value in substantive):
                raise ValueError("invalid case cannot carry candidate judgments")
            if self.evidence_judgments or self.hard_fail_reasons:
                raise ValueError("invalid case cannot carry candidate reason codes")
        else:
            if any(value is None for value in substantive):
                raise ValueError("assessable packet requires every core judgment")
            if (
                self.assessability is Assessability.ASSESSABLE
                and not self.evidence_judgments
            ):
                raise ValueError("assessable packet requires evidence-link judgments")
            if self.assessability is Assessability.SYSTEM_PACKET_INVALID:
                if self.relevance_grade != 0 or not self.hard_fail_reasons:
                    raise ValueError("invalid packet requires grade zero and hard fail")
            if self.relevance_grade is not None and self.relevance_grade >= 2:
                if self.evidence_valid is not True or self.hard_fail_reasons:
                    raise ValueError("grade two or three requires valid evidence and no hard fail")
                if not any(
                    item.expert_relation is ExpertEvidenceRelation.VALID_SUPPORT
                    for item in self.evidence_judgments
                ):
                    raise ValueError("grade two or three requires valid supporting evidence")
            if self.relevance_grade == 3 and (
                self.bridge_judgment is None
                or self.bridge_judgment.overall is not BridgeVerdict.CORRECT
            ):
                raise ValueError("grade three requires a correct bridge")

        semantic = self.model_dump(
            mode="python", exclude={"annotation_id", "annotation_sha256"}
        )
        expected_sha256 = canonical_sha256(semantic)
        if self.annotation_sha256 != expected_sha256:
            raise ValueError("annotation SHA-256 does not match semantic content")
        if self.annotation_id != deterministic_id(
            "expert-annotation", {"annotation_sha256": expected_sha256}
        ):
            raise ValueError("annotation ID does not match annotation SHA-256")
        return self


class AnnotationRefV1(StrictModel):
    annotation_id: Identifier
    annotation_sha256: Sha256
    reviewer_id: Identifier


class ExpertAdjudicationV1(StrictModel):
    schema_version: Literal["flatband-expert-adjudication-v1"] = (
        "flatband-expert-adjudication-v1"
    )
    adjudication_id: Identifier
    adjudication_sha256: Sha256
    blinded_unit_id: Identifier
    case_id: Identifier
    case_sha256: Sha256
    packet_id: Identifier
    packet_sha256: Sha256
    raw_annotations: Annotated[
        tuple[AnnotationRefV1, ...], Field(min_length=2, max_length=2)
    ]
    adjudicator_id: Identifier
    annotation_guide_sha256: Sha256
    disagreement_fields: Annotated[
        tuple[Identifier, ...], Field(min_length=1, max_length=32)
    ]
    status: AdjudicationStatus
    final_relevance_grade: Annotated[int, Field(ge=0, le=3)] | None
    final_evidence_valid: bool | None
    final_bridge_judgment: BridgeJudgmentV1 | None
    final_mechanism_family: MechanismFamily | None
    final_strict_hypothesis_group_id: Identifier | None
    resolution_reason_code: Identifier
    rationale: LongText
    adjudicated_at: Annotated[str, Field(min_length=20, max_length=40)]
    sealed: Literal[True] = True
    scientific_conclusion: Literal[False] = False

    @field_validator("adjudicated_at")
    @classmethod
    def validate_adjudicated_at(cls, value: str) -> str:
        return _require_rfc3339(value)

    @model_validator(mode="after")
    def validate_adjudication(self) -> "ExpertAdjudicationV1":
        annotation_keys = tuple(
            (item.reviewer_id, item.annotation_id, item.annotation_sha256)
            for item in self.raw_annotations
        )
        if annotation_keys != tuple(sorted(set(annotation_keys))):
            raise ValueError("raw annotations must be reviewer-sorted and unique")
        reviewer_ids = {item.reviewer_id for item in self.raw_annotations}
        if len(reviewer_ids) != 2:
            raise ValueError("adjudication requires two distinct reviewers")
        if self.adjudicator_id in reviewer_ids:
            raise ValueError("adjudicator must differ from both reviewers")
        _require_sorted_unique(self.disagreement_fields, "disagreement fields")
        final_values = (
            self.final_relevance_grade,
            self.final_evidence_valid,
            self.final_bridge_judgment,
            self.final_mechanism_family,
            self.final_strict_hypothesis_group_id,
        )
        if self.status is AdjudicationStatus.RESOLVED:
            if any(value is None for value in final_values):
                raise ValueError("resolved adjudication requires every final judgment")
            if self.final_relevance_grade is not None and self.final_relevance_grade >= 2:
                if self.final_evidence_valid is not True:
                    raise ValueError("resolved grade two or three requires valid evidence")
            if self.final_relevance_grade == 3 and (
                self.final_bridge_judgment is None
                or self.final_bridge_judgment.overall is not BridgeVerdict.CORRECT
            ):
                raise ValueError("resolved grade three requires a correct bridge")
        elif any(value is not None for value in final_values):
            raise ValueError("unresolved or invalid adjudication cannot carry final labels")
        semantic = self.model_dump(
            mode="python", exclude={"adjudication_id", "adjudication_sha256"}
        )
        expected_sha256 = canonical_sha256(semantic)
        if self.adjudication_sha256 != expected_sha256:
            raise ValueError("adjudication SHA-256 does not match semantic content")
        if self.adjudication_id != deterministic_id(
            "expert-adjudication", {"adjudication_sha256": expected_sha256}
        ):
            raise ValueError("adjudication ID does not match adjudication SHA-256")
        return self


class SplitCaseRefV1(StrictModel):
    case_id: Identifier
    case_sha256: Sha256
    split: BenchmarkSplit
    target_class: TargetBandClass
    dimensionality: Dimensionality
    primary_mechanism_stratum: MechanismFamily
    leakage_group_ids: Annotated[
        tuple[Identifier, ...], Field(min_length=1, max_length=64)
    ]

    @model_validator(mode="after")
    def validate_groups(self) -> "SplitCaseRefV1":
        _require_sorted_unique(self.leakage_group_ids, "case leakage groups")
        return self


class BenchmarkSplitManifestV1(StrictModel):
    schema_version: Literal["flatband-split-manifest-v1"] = (
        "flatband-split-manifest-v1"
    )
    manifest_id: Identifier
    manifest_sha256: Sha256
    manifest_kind: SplitManifestKind
    source_catalog_sha256: Literal[SOURCE_CATALOG_V1_SHA256] = (
        SOURCE_CATALOG_V1_SHA256
    )
    split_seed: Annotated[int, Field(ge=0, le=2**63 - 1)]
    cases: Annotated[
        tuple[SplitCaseRefV1, ...], Field(min_length=30, max_length=120)
    ]
    ood_holdout_group_ids: Annotated[
        tuple[Identifier, ...], Field(max_length=64)
    ] = ()

    @model_validator(mode="after")
    def validate_manifest(self) -> "BenchmarkSplitManifestV1":
        case_ids = tuple(item.case_id for item in self.cases)
        if case_ids != tuple(sorted(set(case_ids))):
            raise ValueError("split cases must be case-ID sorted and unique")
        _require_sorted_unique(self.ood_holdout_group_ids, "OOD holdout groups")
        counts = {split: 0 for split in BenchmarkSplit}
        for case in self.cases:
            counts[case.split] += 1
        if self.manifest_kind is SplitManifestKind.PILOT_R1:
            expected = {BenchmarkSplit.PILOT_R1: 30}
        elif self.manifest_kind is SplitManifestKind.PILOT_R2:
            expected = {BenchmarkSplit.PILOT_R2: 30}
        else:
            expected = {
                BenchmarkSplit.DEVELOPMENT: 60,
                BenchmarkSplit.LOCKED_IID: 30,
                BenchmarkSplit.LOCKED_OOD: 30,
            }
        actual = {split: count for split, count in counts.items() if count}
        if actual != expected:
            raise ValueError("split counts differ from the preregistered design")
        if self.manifest_kind is SplitManifestKind.MAIN_120:
            group_to_split: dict[str, BenchmarkSplit] = {}
            for case in self.cases:
                for group_id in case.leakage_group_ids:
                    previous = group_to_split.setdefault(group_id, case.split)
                    if previous is not case.split:
                        raise ValueError("a leakage group crosses benchmark splits")
            holdouts = set(self.ood_holdout_group_ids)
            if not holdouts:
                raise ValueError("main manifest requires frozen OOD holdout groups")
            for case in self.cases:
                overlap = holdouts & set(case.leakage_group_ids)
                if overlap and case.split is not BenchmarkSplit.LOCKED_OOD:
                    raise ValueError("OOD holdout group appears outside locked OOD")
            observed_holdouts = {
                group_id
                for case in self.cases
                if case.split is BenchmarkSplit.LOCKED_OOD
                for group_id in case.leakage_group_ids
                if group_id in holdouts
            }
            if observed_holdouts != holdouts:
                raise ValueError("one or more OOD holdout groups have no OOD case")
        elif self.ood_holdout_group_ids:
            raise ValueError("pilot manifest cannot declare OOD holdout groups")
        semantic = self.model_dump(
            mode="python", exclude={"manifest_id", "manifest_sha256"}
        )
        expected_sha256 = canonical_sha256(semantic)
        if self.manifest_sha256 != expected_sha256:
            raise ValueError("split manifest SHA-256 does not match semantic content")
        if self.manifest_id != deterministic_id(
            "split-manifest", {"manifest_sha256": expected_sha256}
        ):
            raise ValueError("split manifest ID does not match manifest SHA-256")
        return self


class BlindingManifestV1(StrictModel):
    schema_version: Literal["flatband-blinding-manifest-v1"] = (
        "flatband-blinding-manifest-v1"
    )
    manifest_id: Identifier
    manifest_sha256: Sha256
    split_manifest_id: Identifier
    split_manifest_sha256: Sha256
    expert_registry_id: Identifier
    expert_registry_sha256: Sha256
    randomization_seed: Annotated[int, Field(ge=0, le=2**63 - 1)]
    reviewer_ids: Annotated[
        tuple[Identifier, ...], Field(min_length=2, max_length=2)
    ]
    adjudicator_id: Identifier
    units: Annotated[
        tuple[BlindedEvaluationUnitV1, ...], Field(min_length=1, max_length=100_000)
    ]
    system_identity_hidden: Literal[True] = True
    selection_rank_hidden: Literal[True] = True
    peer_annotation_hidden: Literal[True] = True
    identity_map_private: Literal[True] = True
    sealed_at: Annotated[str, Field(min_length=20, max_length=40)]
    scientific_conclusion: Literal[False] = False

    @field_validator("sealed_at")
    @classmethod
    def validate_sealed_at(cls, value: str) -> str:
        return _require_rfc3339(value)

    @model_validator(mode="after")
    def validate_manifest(self) -> "BlindingManifestV1":
        _require_sorted_unique(self.reviewer_ids, "blinded reviewer IDs")
        if self.adjudicator_id in self.reviewer_ids:
            raise ValueError("blinded adjudicator must differ from both reviewers")
        orders = tuple(item.annotation_order for item in self.units)
        if orders != tuple(range(1, len(self.units) + 1)):
            raise ValueError("annotation order must be contiguous from one")
        blind_ids = tuple(item.blinded_unit_id for item in self.units)
        if len(blind_ids) != len(set(blind_ids)):
            raise ValueError("blinded unit IDs must be unique")
        packet_ids = tuple(item.packet_id for item in self.units)
        if len(packet_ids) != len(set(packet_ids)):
            raise ValueError("an exact packet must be pooled into one blinded unit")
        semantic = self.model_dump(
            mode="python", exclude={"manifest_id", "manifest_sha256"}
        )
        expected_sha256 = canonical_sha256(semantic)
        if self.manifest_sha256 != expected_sha256:
            raise ValueError("blinding manifest SHA-256 does not match semantic content")
        if self.manifest_id != deterministic_id(
            "blinding-manifest", {"manifest_sha256": expected_sha256}
        ):
            raise ValueError("blinding manifest ID does not match manifest SHA-256")
        return self


def assert_blinding_covers_rankings(
    manifest: BlindingManifestV1, *rankings: SystemRankingV1
) -> None:
    """Require exact one-to-one coverage of every returned ranking position."""

    manifest = BlindingManifestV1.model_validate(
        manifest.model_dump(mode="python", round_trip=True)
    )
    rankings = tuple(
        SystemRankingV1.model_validate(
            ranking.model_dump(mode="python", round_trip=True)
        )
        for ranking in rankings
    )
    expected: dict[
        tuple[str, str, str, int], tuple[str, str, str, str, str, str]
    ] = {}
    for ranking in rankings:
        for item in ranking.ranked_packets:
            key = (
                ranking.system_id,
                ranking.run_id,
                ranking.ranking_id,
                item.selection_rank,
            )
            if key in expected:
                raise ValueError("ranking contribution identity is not unique")
            expected[key] = (
                ranking.ranking_sha256,
                ranking.case_id,
                ranking.case_sha256,
                item.packet_id,
                item.packet_sha256,
                item.strict_hypothesis_group_id,
            )
    observed: dict[
        tuple[str, str, str, int], tuple[str, str, str, str, str, str]
    ] = {}
    for unit in manifest.units:
        for contribution in unit.contributions:
            key = (
                contribution.system_id,
                contribution.run_id,
                contribution.ranking_id,
                contribution.selection_rank,
            )
            if key in observed:
                raise ValueError("a ranking position appears in multiple blinded units")
            observed[key] = (
                contribution.ranking_sha256,
                unit.case_id,
                unit.case_sha256,
                unit.packet_id,
                unit.packet_sha256,
                unit.strict_hypothesis_group_id,
            )
    if observed != expected:
        raise ValueError("blinding manifest does not exactly cover system rankings")


def assert_manifests_case_disjoint(*manifests: BenchmarkSplitManifestV1) -> None:
    """Reject case or leakage-group reuse across pilot/main manifests."""

    manifests = tuple(
        BenchmarkSplitManifestV1.model_validate(
            manifest.model_dump(mode="python", round_trip=True)
        )
        for manifest in manifests
    )
    case_owner: dict[str, str] = {}
    group_owner: dict[str, str] = {}
    for manifest in manifests:
        for case in manifest.cases:
            previous_case = case_owner.setdefault(case.case_id, manifest.manifest_id)
            if previous_case != manifest.manifest_id:
                raise ValueError("a case appears in more than one split manifest")
            for group_id in case.leakage_group_ids:
                previous_group = group_owner.setdefault(group_id, manifest.manifest_id)
                if previous_group != manifest.manifest_id:
                    raise ValueError("a leakage group appears in more than one manifest")


class SourceRequestAllocationV1(StrictModel):
    source_id: Identifier
    max_physical_requests: Annotated[int, Field(ge=0, le=8)]
    actual_physical_requests: Annotated[int, Field(ge=0, le=8)]
    raw_hits: Annotated[int, Field(ge=0, le=10_000)]
    unique_documents: Annotated[int, Field(ge=0, le=2_000)]
    response_bytes: Annotated[int, Field(ge=0, le=100_000_000)]
    cache_hits: Annotated[int, Field(ge=0, le=1_000)] = 0

    @model_validator(mode="after")
    def validate_requests(self) -> "SourceRequestAllocationV1":
        if self.actual_physical_requests > self.max_physical_requests:
            raise ValueError("actual source requests exceed their frozen allocation")
        return self


class LlmUseRecordV1(StrictModel):
    enabled: bool
    provider: ShortText | None = None
    model: ShortText | None = None
    revision: ShortText | None = None
    prompt_sha256: Sha256 | None = None
    tokenizer_sha256: Sha256 | None = None
    max_calls: Annotated[int, Field(ge=0, le=2)] = 0
    actual_calls: Annotated[int, Field(ge=0, le=2)] = 0
    max_input_tokens: Annotated[int, Field(ge=0, le=12_000)] = 0
    actual_input_tokens: Annotated[int, Field(ge=0, le=12_000)] = 0
    max_output_tokens: Annotated[int, Field(ge=0, le=16_000)] = 0
    actual_output_tokens: Annotated[int, Field(ge=0, le=16_000)] = 0
    metadata_packet_limit: Annotated[int, Field(ge=0, le=20)] = 0

    @model_validator(mode="after")
    def validate_llm(self) -> "LlmUseRecordV1":
        identities = (
            self.provider,
            self.model,
            self.revision,
            self.prompt_sha256,
            self.tokenizer_sha256,
        )
        budgets = (
            self.max_calls,
            self.max_input_tokens,
            self.max_output_tokens,
            self.metadata_packet_limit,
        )
        actuals = (
            self.actual_calls,
            self.actual_input_tokens,
            self.actual_output_tokens,
        )
        if self.enabled:
            if any(value is None for value in identities) or any(value == 0 for value in budgets):
                raise ValueError("enabled LLM requires complete identity and positive budgets")
            if self.actual_calls > self.max_calls:
                raise ValueError("actual LLM calls exceed the frozen budget")
            if self.actual_input_tokens > self.max_input_tokens:
                raise ValueError("actual LLM input exceeds the frozen budget")
            if self.actual_output_tokens > self.max_output_tokens:
                raise ValueError("actual LLM output exceeds the frozen budget")
        elif any(value is not None for value in identities) or any(budgets) or any(actuals):
            raise ValueError("disabled LLM requires empty identity and zero budgets")
        return self


class LocalSemanticModelUseV1(StrictModel):
    enabled: bool
    bundle_sha256: Sha256 | None = None
    tokenizer_sha256: Sha256 | None = None
    model_card_sha256: Sha256 | None = None
    vector_dimension: Annotated[int, Field(ge=0, le=65_536)] = 0
    input_tokens: Annotated[int, Field(ge=0, le=1_000_000)] = 0

    @model_validator(mode="after")
    def validate_local_model(self) -> "LocalSemanticModelUseV1":
        identities = (
            self.bundle_sha256,
            self.tokenizer_sha256,
            self.model_card_sha256,
        )
        if self.enabled:
            if any(value is None for value in identities) or self.vector_dimension == 0:
                raise ValueError("enabled local semantic model requires a pinned identity")
        elif any(value is not None for value in identities) or self.vector_dimension or self.input_tokens:
            raise ValueError("disabled local semantic model requires an empty record")
        return self


class ResearchRunLedgerV1(StrictModel):
    schema_version: Literal["flatband-research-run-ledger-v1"] = (
        "flatband-research-run-ledger-v1"
    )
    ledger_id: Identifier
    ledger_sha256: Sha256
    system_id: Identifier
    system_config_sha256: Sha256
    git_commit: Annotated[str, Field(pattern=r"^[0-9a-f]{40}$")]
    run_id: Identifier
    case_id: Identifier
    case_sha256: Sha256
    source_catalog_sha256: Literal[SOURCE_CATALOG_V1_SHA256] = (
        SOURCE_CATALOG_V1_SHA256
    )
    source_allocations: Annotated[
        tuple[SourceRequestAllocationV1, ...], Field(min_length=1, max_length=4)
    ]
    max_total_physical_requests: Literal[8] = 8
    actual_total_physical_requests: Annotated[int, Field(ge=0, le=8)]
    article_body_fetch_requests: Literal[0] = 0
    full_pdf_reads: Literal[0] = 0
    llm: LlmUseRecordV1
    local_semantic_model: LocalSemanticModelUseV1
    max_walltime_seconds: Annotated[int, Field(ge=1, le=3_600)]
    walltime_ms: Annotated[int, Field(ge=0, le=3_600_000)]
    outcome: ResearchRunOutcome
    ranking_id: Identifier | None = None
    ranking_sha256: Sha256 | None = None
    failure_reason_codes: Annotated[
        tuple[Identifier, ...], Field(max_length=32)
    ] = ()
    ledger_created_at: Annotated[str, Field(min_length=20, max_length=40)]
    scientific_conclusion: Literal[False] = False

    @field_validator("ledger_created_at")
    @classmethod
    def validate_created_at(cls, value: str) -> str:
        return _require_rfc3339(value)

    @model_validator(mode="after")
    def validate_ledger(self) -> "ResearchRunLedgerV1":
        source_ids = tuple(item.source_id for item in self.source_allocations)
        if source_ids != tuple(sorted(set(source_ids))):
            raise ValueError("source allocations must be source-ID sorted and unique")
        if sum(item.max_physical_requests for item in self.source_allocations) != 8:
            raise ValueError("source allocations must close to eight physical requests")
        actual = sum(item.actual_physical_requests for item in self.source_allocations)
        if actual != self.actual_total_physical_requests:
            raise ValueError("actual physical request total does not close")
        _require_sorted_unique(self.failure_reason_codes, "run failure reasons")
        has_ranking = self.ranking_id is not None or self.ranking_sha256 is not None
        if (self.ranking_id is None) != (self.ranking_sha256 is None):
            raise ValueError("ranking ID and SHA-256 must be present together")
        if self.outcome is ResearchRunOutcome.SUCCEEDED:
            if not has_ranking or self.failure_reason_codes:
                raise ValueError("successful run requires ranking and no failure reasons")
        elif self.outcome is ResearchRunOutcome.PARTIAL:
            if not has_ranking or not self.failure_reason_codes:
                raise ValueError("partial run requires ranking and reason codes")
        elif has_ranking or not self.failure_reason_codes:
            raise ValueError("failed run requires reasons and no ranking")
        semantic = self.model_dump(
            mode="python", exclude={"ledger_id", "ledger_sha256"}
        )
        expected_sha256 = canonical_sha256(semantic)
        if self.ledger_sha256 != expected_sha256:
            raise ValueError("run ledger SHA-256 does not match semantic content")
        if self.ledger_id != deterministic_id(
            "research-ledger", {"ledger_sha256": expected_sha256}
        ):
            raise ValueError("run ledger ID does not match ledger SHA-256")
        return self


class ExpertRegistrationV1(StrictModel):
    expert_id: Identifier
    role: ExpertRole
    domain_expertise: Annotated[
        tuple[ShortText, ...], Field(min_length=1, max_length=16)
    ]
    qualification_summary: LongText
    conflict_disclosures: Annotated[
        tuple[ShortText, ...], Field(min_length=1, max_length=16)
    ]
    annotation_guide_sha256: Sha256
    calibration_completed: bool
    confirmation_reference: Identifier
    registered_at: Annotated[str, Field(min_length=20, max_length=40)]

    @field_validator("registered_at")
    @classmethod
    def validate_registered_at(cls, value: str) -> str:
        return _require_rfc3339(value)

    @model_validator(mode="after")
    def validate_registration(self) -> "ExpertRegistrationV1":
        _require_sorted_unique(self.domain_expertise, "expertise entries")
        _require_sorted_unique(self.conflict_disclosures, "conflict disclosures")
        return self


class ExpertRegistryV1(StrictModel):
    schema_version: Literal["flatband-expert-registry-v1"] = (
        "flatband-expert-registry-v1"
    )
    registry_id: Identifier
    registry_sha256: Sha256
    annotation_guide_sha256: Sha256
    calibration_set_sha256: Sha256
    experts: Annotated[
        tuple[ExpertRegistrationV1, ...], Field(min_length=3, max_length=3)
    ]
    public_identity_mode: Literal["PSEUDONYMOUS"] = "PSEUDONYMOUS"

    @model_validator(mode="after")
    def validate_registry(self) -> "ExpertRegistryV1":
        expert_ids = tuple(item.expert_id for item in self.experts)
        if expert_ids != tuple(sorted(set(expert_ids))):
            raise ValueError("experts must be expert-ID sorted and unique")
        roles = [item.role for item in self.experts]
        if roles.count(ExpertRole.REVIEWER) != 2 or roles.count(ExpertRole.ADJUDICATOR) != 1:
            raise ValueError("registry requires two reviewers and one adjudicator")
        if any(item.annotation_guide_sha256 != self.annotation_guide_sha256 for item in self.experts):
            raise ValueError("all experts must register the same annotation guide")
        if not all(item.calibration_completed for item in self.experts):
            raise ValueError("every expert must complete calibration")
        semantic = self.model_dump(
            mode="python", exclude={"registry_id", "registry_sha256"}
        )
        expected_sha256 = canonical_sha256(semantic)
        if self.registry_sha256 != expected_sha256:
            raise ValueError("expert registry SHA-256 does not match semantic content")
        if self.registry_id != deterministic_id(
            "expert-registry", {"registry_sha256": expected_sha256}
        ):
            raise ValueError("expert registry ID does not match registry SHA-256")
        return self
