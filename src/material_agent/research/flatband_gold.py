"""Exact-coverage expert-gold closure for the flat/narrow-band benchmark.

The public safe path emits a ``FinalGoldReleaseV2`` artifact but accepts only
the authoritative Frozen/Eligibility/Execution V3 chain.  It binds separate
reviewer manifests/private maps and exposes only pooled identities in final
Gold.  Legacy V1/V2-upstream symbols are compatibility adapters for historical
fixtures and must not be used for a new research release.  No ranking
contribution or system-proposed duplicate group is copied into final Gold.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Annotated, Literal, TypeVar

from pydantic import Field, field_validator, model_validator

from material_agent.inspiration.models import (
    Identifier,
    LongText,
    Sha256,
    StrictModel,
    canonical_sha256,
    deterministic_id,
)
from material_agent.research.flatband_contracts import (
    AdjudicationStatus,
    AnnotationRefV1,
    Assessability,
    BlindingManifestV1,
    BridgeJudgmentV1,
    BridgeVerdict,
    DisagreementField,
    EvidenceJudgmentV1,
    ExpertAdjudicationV1,
    ExpertEvidenceRelation,
    HardFailReason,
    HypothesisPacketV1,
    MechanismFamily,
    RawExpertAnnotationV1,
)
from material_agent.research.flatband_blinding import (
    EvidenceExcerptV1,
    EvidenceExcerptV2,
    PrivateIdentityMapV1,
    PrivateIdentityMapV2,
    ReviewerManifestV1,
    ReviewerManifestV2,
    assert_reviewer_release_exact_coverage,
    assert_reviewer_release_exact_coverage_v2,
    assert_reviewer_release_legacy_v2_upstream_exact_coverage,
)
from material_agent.research.flatband_execution import (
    ExecutionPhase,
    ExecutionReleaseV1,
    ExecutionReleaseV2,
    ExecutionReleaseV3,
)
from material_agent.research.flatband_cases import (
    FrozenCaseReleaseV3,
    PreRunEligibilityReleaseV1,
    PreRunEligibilityReleaseV2,
    PreRunEligibilityReleaseV3,
)
from material_agent.research.flatband_experts import (
    ExpertStudyRegistryV1,
    ExpertStudyRegistryV2,
)


class GoldProvenance(StrEnum):
    AGREED_RAW = "AGREED_RAW"
    ADJUDICATED = "ADJUDICATED"


class DuplicatePartitionProvenance(StrEnum):
    AGREED_REVIEWERS = "AGREED_REVIEWERS"
    ADJUDICATED = "ADJUDICATED"


ModelT = TypeVar("ModelT", bound=StrictModel)


def _timestamp(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:  # pragma: no cover - shared defensive boundary
        raise ValueError("timestamp must be RFC3339-compatible") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("timestamp must include a UTC offset")
    return parsed


def _require_sorted_unique(values: tuple[str, ...], label: str) -> None:
    if values != tuple(sorted(set(values))):
        raise ValueError(f"{label} must be sorted and unique")


def _assert_addressed(
    value: StrictModel, *, id_field: str, sha_field: str, prefix: str
) -> None:
    semantic = value.model_dump(mode="python", exclude={id_field, sha_field})
    expected_sha256 = canonical_sha256(semantic)
    if getattr(value, sha_field) != expected_sha256:
        raise ValueError(f"{sha_field} does not match semantic content")
    if getattr(value, id_field) != deterministic_id(
        prefix, {sha_field: expected_sha256}
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
            id_field: deterministic_id(prefix, {sha_field: digest}),
            sha_field: digest,
        }
    )


def _revalidate(value: object, model_type: type[ModelT]) -> ModelT:
    parsed = model_type.model_validate(value)
    return model_type.model_validate(
        parsed.model_dump(mode="python", round_trip=True)
    )


class AdjudicationRefV1(StrictModel):
    adjudication_id: Identifier
    adjudication_sha256: Sha256
    adjudicator_id: Identifier


class FinalExpertJudgmentV1(StrictModel):
    """One immutable final label for one pooled, blinded packet."""

    schema_version: Literal["flatband-final-expert-judgment-v1"] = (
        "flatband-final-expert-judgment-v1"
    )
    judgment_id: Identifier
    judgment_sha256: Sha256
    reviewer_manifest_id: Identifier
    reviewer_manifest_sha256: Sha256
    expert_registry_id: Identifier
    expert_registry_sha256: Sha256
    blinded_unit_id: Identifier
    case_id: Identifier
    case_sha256: Sha256
    packet_id: Identifier
    packet_sha256: Sha256
    review_round: Annotated[int, Field(ge=1, le=2)]
    annotation_guide_sha256: Sha256
    provenance: GoldProvenance
    raw_annotations: Annotated[
        tuple[AnnotationRefV1, ...], Field(min_length=2, max_length=2)
    ]
    adjudication: AdjudicationRefV1 | None = None
    adjudication_status: AdjudicationStatus | None = None
    final_assessability: Assessability | None = None
    final_relevance_grade: Annotated[int, Field(ge=0, le=3)] | None = None
    final_evidence_valid: bool | None = None
    final_evidence_judgments: Annotated[
        tuple[EvidenceJudgmentV1, ...], Field(max_length=64)
    ] = ()
    final_bridge_judgment: BridgeJudgmentV1 | None = None
    final_mechanism_family: MechanismFamily | None = None
    final_hard_fail_reasons: Annotated[
        tuple[HardFailReason, ...], Field(max_length=16)
    ] = ()
    denominator_included: bool
    metric_relevance_gain: Annotated[int, Field(ge=0, le=3)]
    metric_evidence_gain: Annotated[int, Field(ge=0, le=1)]
    unresolvable: bool
    scientific_conclusion: Literal[False] = False

    @model_validator(mode="after")
    def validate_judgment(self) -> "FinalExpertJudgmentV1":
        raw_keys = tuple(
            (item.reviewer_id, item.annotation_id, item.annotation_sha256)
            for item in self.raw_annotations
        )
        if raw_keys != tuple(sorted(set(raw_keys))) or len(
            {item.reviewer_id for item in self.raw_annotations}
        ) != 2:
            raise ValueError("final judgment requires two reviewer-sorted raw refs")
        evidence_ids = tuple(
            item.evidence_link_id for item in self.final_evidence_judgments
        )
        _require_sorted_unique(evidence_ids, "final evidence judgments")
        hard_fail_values = tuple(item.value for item in self.final_hard_fail_reasons)
        _require_sorted_unique(hard_fail_values, "final hard-fail reasons")

        if self.provenance is GoldProvenance.AGREED_RAW:
            if self.adjudication is not None or self.adjudication_status is not None:
                raise ValueError("agreed raw judgment cannot reference adjudication")
        elif self.adjudication is None or self.adjudication_status is None:
            raise ValueError("adjudicated judgment requires adjudication identity and status")

        candidate_values = (
            self.final_relevance_grade,
            self.final_evidence_valid,
            self.final_bridge_judgment,
            self.final_mechanism_family,
        )
        if self.adjudication_status is AdjudicationStatus.UNRESOLVABLE:
            if (
                self.final_assessability is not None
                or any(value is not None for value in candidate_values)
                or self.final_evidence_judgments
                or self.final_hard_fail_reasons
            ):
                raise ValueError("unresolvable judgment cannot carry final labels")
            if not self.unresolvable:
                raise ValueError("unresolvable adjudication must be reported separately")
        elif self.final_assessability is Assessability.CASE_INVALID:
            if any(value is not None for value in candidate_values):
                raise ValueError("invalid case cannot carry candidate labels")
            if self.final_evidence_judgments or self.final_hard_fail_reasons:
                raise ValueError("invalid case cannot carry candidate reasons")
            if self.unresolvable:
                raise ValueError("invalid case is not an unresolvable packet")
        elif self.final_assessability is Assessability.SYSTEM_PACKET_INVALID:
            if self.final_relevance_grade != 0 or not self.final_hard_fail_reasons:
                raise ValueError("invalid packet requires grade zero and hard fail")
            if (
                self.final_evidence_valid is not None
                or self.final_evidence_judgments
                or self.final_bridge_judgment is not None
                or self.final_mechanism_family is not None
            ):
                raise ValueError("invalid packet cannot carry substantive final labels")
            if self.unresolvable:
                raise ValueError("invalid packet is not an unresolvable adjudication")
        elif self.final_assessability is Assessability.ASSESSABLE:
            if any(value is None for value in candidate_values):
                raise ValueError("assessable final judgment requires all core labels")
            if not self.final_evidence_judgments:
                raise ValueError("assessable final judgment requires evidence judgments")
            if self.final_hard_fail_reasons and self.final_relevance_grade != 0:
                raise ValueError("any final hard fail requires grade zero")
            if self.final_relevance_grade is not None and self.final_relevance_grade >= 2:
                if self.final_evidence_valid is not True or self.final_hard_fail_reasons:
                    raise ValueError("grade two or three requires valid evidence and no hard fail")
                if not any(
                    item.expert_relation is ExpertEvidenceRelation.VALID_SUPPORT
                    for item in self.final_evidence_judgments
                ):
                    raise ValueError("grade two or three requires valid support")
                if (
                    self.final_bridge_judgment is None
                    or self.final_bridge_judgment.overall
                    not in {BridgeVerdict.CORRECT, BridgeVerdict.CONDITIONAL}
                ):
                    raise ValueError("grade two or three requires a valid bridge")
            if self.final_relevance_grade == 3 and (
                self.final_bridge_judgment is None
                or self.final_bridge_judgment.overall is not BridgeVerdict.CORRECT
            ):
                raise ValueError("grade three requires a correct bridge")
            if self.unresolvable:
                raise ValueError("resolved assessment cannot be unresolvable")
        else:
            raise ValueError("final judgment requires assessability or UNRESOLVABLE")

        expected_denominator = self.final_assessability is not Assessability.CASE_INVALID
        if self.denominator_included is not expected_denominator:
            raise ValueError("metric denominator inclusion differs from assessability")
        expected_grade = self.final_relevance_grade or 0
        expected_evidence = int(self.final_evidence_valid is True)
        if self.unresolvable:
            expected_grade = 0
            expected_evidence = 0
        if self.metric_relevance_gain != expected_grade:
            raise ValueError("metric relevance gain differs from final label")
        if self.metric_evidence_gain != expected_evidence:
            raise ValueError("metric evidence gain differs from final label")
        _assert_addressed(
            self,
            id_field="judgment_id",
            sha_field="judgment_sha256",
            prefix="final-expert-judgment",
        )
        return self


class ExpertDuplicateClusterV1(StrictModel):
    cluster_id: Identifier
    blinded_unit_ids: Annotated[
        tuple[Identifier, ...], Field(min_length=1, max_length=100)
    ]

    @model_validator(mode="after")
    def validate_members(self) -> "ExpertDuplicateClusterV1":
        _require_sorted_unique(self.blinded_unit_ids, "duplicate cluster members")
        return self


class DuplicatePartitionRefV1(StrictModel):
    partition_id: Identifier
    partition_sha256: Sha256
    reviewer_id: Identifier


class RawExpertDuplicatePartitionV1(StrictModel):
    """One reviewer's independently sealed, complete partition for one case."""

    schema_version: Literal["flatband-raw-expert-duplicate-partition-v1"] = (
        "flatband-raw-expert-duplicate-partition-v1"
    )
    partition_id: Identifier
    partition_sha256: Sha256
    reviewer_manifest_id: Identifier
    reviewer_manifest_sha256: Sha256
    expert_registry_id: Identifier
    expert_registry_sha256: Sha256
    case_id: Identifier
    case_sha256: Sha256
    review_round: Annotated[int, Field(ge=1, le=2)]
    annotation_guide_sha256: Sha256
    reviewer_id: Identifier
    clusters: Annotated[
        tuple[ExpertDuplicateClusterV1, ...], Field(min_length=1, max_length=100)
    ]
    submitted_at: Annotated[str, Field(min_length=20, max_length=40)]
    sealed: Literal[True] = True
    scientific_conclusion: Literal[False] = False

    @field_validator("submitted_at")
    @classmethod
    def validate_time(cls, value: str) -> str:
        _timestamp(value)
        return value

    @model_validator(mode="after")
    def validate_partition(self) -> "RawExpertDuplicatePartitionV1":
        _validate_duplicate_clusters(case_id=self.case_id, clusters=self.clusters)
        _assert_addressed(
            self,
            id_field="partition_id",
            sha_field="partition_sha256",
            prefix="raw-expert-duplicate",
        )
        return self


class ExpertDuplicatePartitionV1(StrictModel):
    """Final case-level expert partition; never a system packet group."""

    schema_version: Literal["flatband-expert-duplicate-partition-v1"] = (
        "flatband-expert-duplicate-partition-v1"
    )
    partition_id: Identifier
    partition_sha256: Sha256
    reviewer_manifest_id: Identifier
    reviewer_manifest_sha256: Sha256
    expert_registry_id: Identifier
    expert_registry_sha256: Sha256
    case_id: Identifier
    case_sha256: Sha256
    review_round: Annotated[int, Field(ge=1, le=2)]
    annotation_guide_sha256: Sha256
    reviewer_ids: Annotated[tuple[Identifier, ...], Field(min_length=2, max_length=2)]
    reviewer_partitions: Annotated[
        tuple[DuplicatePartitionRefV1, ...], Field(min_length=2, max_length=2)
    ]
    provenance: DuplicatePartitionProvenance
    adjudicator_id: Identifier | None = None
    resolution_reason_code: Identifier | None = None
    rationale: LongText | None = None
    clusters: Annotated[
        tuple[ExpertDuplicateClusterV1, ...], Field(min_length=1, max_length=100)
    ]
    finalized_at: Annotated[str, Field(min_length=20, max_length=40)]
    scientific_conclusion: Literal[False] = False

    @field_validator("finalized_at")
    @classmethod
    def validate_time(cls, value: str) -> str:
        _timestamp(value)
        return value

    @model_validator(mode="after")
    def validate_partition(self) -> "ExpertDuplicatePartitionV1":
        _require_sorted_unique(self.reviewer_ids, "partition reviewers")
        partition_keys = tuple(
            (item.reviewer_id, item.partition_id, item.partition_sha256)
            for item in self.reviewer_partitions
        )
        if partition_keys != tuple(sorted(set(partition_keys))):
            raise ValueError("reviewer partition refs must be reviewer-sorted and unique")
        if tuple(item.reviewer_id for item in self.reviewer_partitions) != self.reviewer_ids:
            raise ValueError("reviewer partition refs must cover both partition reviewers")
        if self.provenance is DuplicatePartitionProvenance.AGREED_REVIEWERS:
            if any(
                value is not None
                for value in (
                    self.adjudicator_id,
                    self.resolution_reason_code,
                    self.rationale,
                )
            ):
                raise ValueError("agreed duplicate partition cannot carry adjudication")
        elif (
            self.adjudicator_id is None
            or self.adjudicator_id in self.reviewer_ids
            or self.resolution_reason_code is None
            or self.rationale is None
        ):
            raise ValueError("adjudicated partition requires complete distinct adjudication")
        _validate_duplicate_clusters(case_id=self.case_id, clusters=self.clusters)
        _assert_addressed(
            self,
            id_field="partition_id",
            sha_field="partition_sha256",
            prefix="expert-duplicate-partition",
        )
        return self


def _validate_duplicate_clusters(
    *, case_id: str, clusters: tuple[ExpertDuplicateClusterV1, ...]
) -> None:
    cluster_keys = tuple((item.blinded_unit_ids, item.cluster_id) for item in clusters)
    if cluster_keys != tuple(sorted(set(cluster_keys))):
        raise ValueError("duplicate clusters must be member-sorted and unique")
    members = tuple(
        unit_id for cluster in clusters for unit_id in cluster.blinded_unit_ids
    )
    if len(members) != len(set(members)):
        raise ValueError("a blinded unit cannot occur in two duplicate clusters")
    for cluster in clusters:
        expected = deterministic_id(
            "expert-duplicate",
            {"case_id": case_id, "blinded_unit_ids": cluster.blinded_unit_ids},
        )
        if cluster.cluster_id != expected:
            raise ValueError("duplicate cluster ID must derive only from expert membership")


def _partition_signature(
    clusters: tuple[ExpertDuplicateClusterV1, ...],
) -> tuple[tuple[str, ...], ...]:
    return tuple(sorted(cluster.blinded_unit_ids for cluster in clusters))


class FinalGoldReleaseV1(StrictModel):
    schema_version: Literal["flatband-final-gold-release-v1"] = (
        "flatband-final-gold-release-v1"
    )
    release_id: Identifier
    release_sha256: Sha256
    reviewer_manifest_id: Identifier
    reviewer_manifest_sha256: Sha256
    expert_registry_id: Identifier
    expert_registry_sha256: Sha256
    review_round: Annotated[int, Field(ge=1, le=2)]
    annotation_guide_sha256: Sha256
    judgments: Annotated[
        tuple[FinalExpertJudgmentV1, ...], Field(min_length=1, max_length=100_000)
    ]
    duplicate_partitions: Annotated[
        tuple[ExpertDuplicatePartitionV1, ...], Field(min_length=1, max_length=10_000)
    ]
    unresolvable_unit_ids: Annotated[tuple[Identifier, ...], Field(max_length=100_000)]
    denominator_unit_count: Annotated[int, Field(ge=1, le=100_000)]
    unresolvable_unit_count: Annotated[int, Field(ge=0, le=100_000)]
    released_at: Annotated[str, Field(min_length=20, max_length=40)]
    scientific_conclusion: Literal[False] = False

    @field_validator("released_at")
    @classmethod
    def validate_time(cls, value: str) -> str:
        _timestamp(value)
        return value

    @model_validator(mode="after")
    def validate_release(self) -> "FinalGoldReleaseV1":
        judgment_ids = tuple(item.blinded_unit_id for item in self.judgments)
        _require_sorted_unique(judgment_ids, "final judgment unit IDs")
        judgments_by_case: dict[str, list[FinalExpertJudgmentV1]] = {}
        for judgment in self.judgments:
            judgments_by_case.setdefault(judgment.case_id, []).append(judgment)
        for case_judgments in judgments_by_case.values():
            invalid_count = sum(
                item.final_assessability is Assessability.CASE_INVALID
                for item in case_judgments
            )
            if invalid_count not in {0, len(case_judgments)}:
                raise ValueError(
                    "CASE_INVALID must exclude every returned packet in the case symmetrically"
                )
        case_ids = tuple(item.case_id for item in self.duplicate_partitions)
        _require_sorted_unique(case_ids, "final duplicate partition cases")
        expected_unresolvable = tuple(
            item.blinded_unit_id for item in self.judgments if item.unresolvable
        )
        if self.unresolvable_unit_ids != expected_unresolvable:
            raise ValueError("unresolvable unit list does not close to final judgments")
        expected_denominator_count = sum(
            item.denominator_included for item in self.judgments
        )
        if self.denominator_unit_count != expected_denominator_count:
            raise ValueError("denominator count does not close to included judgments")
        if self.unresolvable_unit_count != len(expected_unresolvable):
            raise ValueError("unresolvable count does not close")
        _assert_addressed(
            self,
            id_field="release_id",
            sha_field="release_sha256",
            prefix="final-gold-release",
        )
        return self


_DECISION_FIELDS = (
    DisagreementField.ASSESSABILITY,
    DisagreementField.RELEVANCE_GRADE,
    DisagreementField.EVIDENCE_VALID,
    DisagreementField.EVIDENCE_JUDGMENTS,
    DisagreementField.BRIDGE_JUDGMENT,
    DisagreementField.MECHANISM_FAMILY,
    DisagreementField.HARD_FAIL_REASONS,
)


def _raw_value(annotation: RawExpertAnnotationV1, field: DisagreementField) -> object:
    return {
        DisagreementField.ASSESSABILITY: annotation.assessability,
        DisagreementField.RELEVANCE_GRADE: annotation.relevance_grade,
        DisagreementField.EVIDENCE_VALID: annotation.evidence_valid,
        DisagreementField.EVIDENCE_JUDGMENTS: annotation.evidence_judgments,
        DisagreementField.BRIDGE_JUDGMENT: annotation.bridge_judgment,
        DisagreementField.MECHANISM_FAMILY: annotation.mechanism_family,
        DisagreementField.HARD_FAIL_REASONS: annotation.hard_fail_reasons,
    }[field]


def _raw_disagreements(
    first: RawExpertAnnotationV1, second: RawExpertAnnotationV1
) -> tuple[DisagreementField, ...]:
    return tuple(
        sorted(
            (field for field in _DECISION_FIELDS if _raw_value(first, field) != _raw_value(second, field)),
            key=lambda item: item.value,
        )
    )


def _judgment_values(judgment: FinalExpertJudgmentV1) -> tuple[object, ...]:
    return (
        judgment.final_assessability,
        judgment.final_relevance_grade,
        judgment.final_evidence_valid,
        judgment.final_evidence_judgments,
        judgment.final_bridge_judgment,
        judgment.final_mechanism_family,
        judgment.final_hard_fail_reasons,
    )


def _annotation_values(annotation: RawExpertAnnotationV1) -> tuple[object, ...]:
    return tuple(_raw_value(annotation, field) for field in _DECISION_FIELDS)


def _adjudication_values(adjudication: ExpertAdjudicationV1) -> tuple[object, ...]:
    return (
        adjudication.final_assessability,
        adjudication.final_relevance_grade,
        adjudication.final_evidence_valid,
        adjudication.final_evidence_judgments,
        adjudication.final_bridge_judgment,
        adjudication.final_mechanism_family,
        adjudication.final_hard_fail_reasons,
    )


def _assert_evidence_coverage(
    *, packet: HypothesisPacketV1, judgments: tuple[EvidenceJudgmentV1, ...], assessability: Assessability | None
) -> None:
    observed = tuple(item.evidence_link_id for item in judgments)
    expected = tuple(item.evidence_link_id for item in packet.evidence_links)
    if assessability is Assessability.ASSESSABLE and observed != expected:
        raise ValueError("assessable judgment does not exactly cover packet evidence links")
    if assessability is not Assessability.ASSESSABLE and observed not in {(), expected}:
        raise ValueError("non-assessable evidence judgments must be empty or exact")


def assert_final_gold_closure(
    release: FinalGoldReleaseV1,
    *,
    reviewer_manifest: BlindingManifestV1,
    expert_registry: ExpertStudyRegistryV1,
    packets: tuple[HypothesisPacketV1, ...],
    raw_annotations: tuple[RawExpertAnnotationV1, ...],
    adjudications: tuple[ExpertAdjudicationV1, ...],
    raw_duplicate_partitions: tuple[RawExpertDuplicatePartitionV1, ...],
) -> None:
    """Revalidate and close every raw review, adjudication and expert partition.

    ``reviewer_manifest`` is presently the legacy blinding manifest adapter.  The
    release surface intentionally uses only its opaque unit/case/packet identity.
    """

    release = FinalGoldReleaseV1.model_validate(release.model_dump(mode="python", round_trip=True))
    reviewer_manifest = BlindingManifestV1.model_validate(
        reviewer_manifest.model_dump(mode="python", round_trip=True)
    )
    expert_registry = ExpertStudyRegistryV1.model_validate(
        expert_registry.model_dump(mode="python", round_trip=True)
    )
    packets = tuple(HypothesisPacketV1.model_validate(item.model_dump(mode="python", round_trip=True)) for item in packets)
    raw_annotations = tuple(RawExpertAnnotationV1.model_validate(item.model_dump(mode="python", round_trip=True)) for item in raw_annotations)
    adjudications = tuple(ExpertAdjudicationV1.model_validate(item.model_dump(mode="python", round_trip=True)) for item in adjudications)
    raw_duplicate_partitions = tuple(
        RawExpertDuplicatePartitionV1.model_validate(
            item.model_dump(mode="python", round_trip=True)
        )
        for item in raw_duplicate_partitions
    )

    if (release.reviewer_manifest_id, release.reviewer_manifest_sha256) != (
        reviewer_manifest.manifest_id,
        reviewer_manifest.manifest_sha256,
    ):
        raise ValueError("final release references a foreign reviewer manifest")
    if (release.expert_registry_id, release.expert_registry_sha256) != (
        expert_registry.registry_id,
        expert_registry.registry_sha256,
    ):
        raise ValueError("final release references a foreign expert registry")
    if release.annotation_guide_sha256 != expert_registry.annotation_guide_sha256:
        raise ValueError("final release annotation guide drifts from expert registry")
    if (
        reviewer_manifest.split_manifest_id,
        reviewer_manifest.split_manifest_sha256,
        reviewer_manifest.expert_registry_id,
        reviewer_manifest.expert_registry_sha256,
    ) != (
        expert_registry.split_manifest_id,
        expert_registry.split_manifest_sha256,
        expert_registry.registry_id,
        expert_registry.registry_sha256,
    ):
        raise ValueError("reviewer manifest drifts from split or expert registry")
    assignments = {item.case_id: item for item in expert_registry.assignments}

    manifest_units = {item.blinded_unit_id: item for item in reviewer_manifest.units}
    release_units = {item.blinded_unit_id: item for item in release.judgments}
    if set(release_units) != set(manifest_units):
        raise ValueError("final release does not exactly cover reviewer-manifest units")

    packet_index: dict[str, HypothesisPacketV1] = {}
    for packet in packets:
        if packet.packet_id in packet_index:
            raise ValueError("packet inputs contain duplicate packet identity")
        packet_index[packet.packet_id] = packet
    raw_index: dict[str, RawExpertAnnotationV1] = {}
    for annotation in raw_annotations:
        if annotation.annotation_id in raw_index:
            raise ValueError("raw inputs contain duplicate annotation identity")
        raw_index[annotation.annotation_id] = annotation
    adjudication_index: dict[str, ExpertAdjudicationV1] = {}
    for adjudication in adjudications:
        if adjudication.adjudication_id in adjudication_index:
            raise ValueError("adjudication inputs contain duplicate identity")
        adjudication_index[adjudication.adjudication_id] = adjudication

    used_raw: set[str] = set()
    used_adjudications: set[str] = set()
    latest_submission_by_case: dict[str, datetime] = {}
    released_at = _timestamp(release.released_at)
    for blinded_unit_id, judgment in release_units.items():
        unit = manifest_units[blinded_unit_id]
        if (
            judgment.reviewer_manifest_id,
            judgment.reviewer_manifest_sha256,
            judgment.expert_registry_id,
            judgment.expert_registry_sha256,
            judgment.annotation_guide_sha256,
            judgment.review_round,
        ) != (
            release.reviewer_manifest_id,
            release.reviewer_manifest_sha256,
            release.expert_registry_id,
            release.expert_registry_sha256,
            release.annotation_guide_sha256,
            release.review_round,
        ):
            raise ValueError("final judgment drifts from release identities")
        if (
            judgment.case_id,
            judgment.case_sha256,
            judgment.packet_id,
            judgment.packet_sha256,
        ) != (unit.case_id, unit.case_sha256, unit.packet_id, unit.packet_sha256):
            raise ValueError("final judgment references a foreign masked unit")
        packet = packet_index.get(unit.packet_id)
        if packet is None or (packet.packet_sha256, packet.case_id, packet.case_sha256) != (
            unit.packet_sha256,
            unit.case_id,
            unit.case_sha256,
        ):
            raise ValueError("masked unit packet identity is missing or foreign")

        actual_raw: list[RawExpertAnnotationV1] = []
        for ref in judgment.raw_annotations:
            annotation = raw_index.get(ref.annotation_id)
            if annotation is None or (
                annotation.annotation_sha256,
                annotation.reviewer_id,
            ) != (ref.annotation_sha256, ref.reviewer_id):
                raise ValueError("final judgment contains a foreign raw annotation ref")
            actual_raw.append(annotation)
            used_raw.add(annotation.annotation_id)
        first, second = actual_raw
        assignment = assignments.get(unit.case_id)
        if assignment is None or assignment.case_sha256 != unit.case_sha256:
            raise ValueError("masked unit case is absent from expert assignments")
        if tuple(item.reviewer_id for item in actual_raw) != assignment.reviewer_ids:
            raise ValueError("masked unit does not have exactly the registered reviewers")
        for annotation in actual_raw:
            if (
                annotation.blinded_unit_id,
                annotation.case_id,
                annotation.case_sha256,
                annotation.packet_id,
                annotation.packet_sha256,
                annotation.review_round,
                annotation.annotation_guide_sha256,
            ) != (
                unit.blinded_unit_id,
                unit.case_id,
                unit.case_sha256,
                unit.packet_id,
                unit.packet_sha256,
                release.review_round,
                release.annotation_guide_sha256,
            ):
                raise ValueError("raw annotation drifts from unit/guide/round identity")
            if _timestamp(annotation.submitted_at) > released_at:
                raise ValueError("raw annotation was submitted after final release")
            if _timestamp(annotation.started_at) < _timestamp(reviewer_manifest.sealed_at):
                raise ValueError("raw annotation started before reviewer manifest sealing")
            latest_submission_by_case[unit.case_id] = max(
                latest_submission_by_case.get(unit.case_id, _timestamp(annotation.submitted_at)),
                _timestamp(annotation.submitted_at),
            )
            _assert_evidence_coverage(
                packet=packet,
                judgments=annotation.evidence_judgments,
                assessability=annotation.assessability,
            )

        disagreements = _raw_disagreements(first, second)
        if judgment.provenance is GoldProvenance.AGREED_RAW:
            if disagreements:
                raise ValueError("disagreeing raw reviews require adjudication")
            if _judgment_values(judgment) != _annotation_values(first):
                raise ValueError("AGREED_RAW final labels differ from raw agreement")
        else:
            assert judgment.adjudication is not None  # model invariant
            adjudication = adjudication_index.get(judgment.adjudication.adjudication_id)
            if adjudication is None or (
                adjudication.adjudication_sha256,
                adjudication.adjudicator_id,
            ) != (
                judgment.adjudication.adjudication_sha256,
                judgment.adjudication.adjudicator_id,
            ):
                raise ValueError("final judgment contains a foreign adjudication ref")
            used_adjudications.add(adjudication.adjudication_id)
            if not disagreements:
                raise ValueError("agreed raw reviews cannot be routed to adjudication")
            if tuple(adjudication.disagreement_fields) != disagreements:
                raise ValueError("adjudication disagreement fields do not exactly close")
            if tuple(adjudication.raw_annotations) != judgment.raw_annotations:
                raise ValueError("adjudication does not reference the two true raw reviews")
            if (
                adjudication.blinded_unit_id,
                adjudication.case_id,
                adjudication.case_sha256,
                adjudication.packet_id,
                adjudication.packet_sha256,
                adjudication.annotation_guide_sha256,
                adjudication.adjudicator_id,
            ) != (
                unit.blinded_unit_id,
                unit.case_id,
                unit.case_sha256,
                unit.packet_id,
                unit.packet_sha256,
                release.annotation_guide_sha256,
                assignment.adjudicator_id,
            ):
                raise ValueError("adjudication drifts from unit/guide/registered adjudicator")
            adjudicated_at = _timestamp(adjudication.adjudicated_at)
            if adjudicated_at < max(_timestamp(first.submitted_at), _timestamp(second.submitted_at)):
                raise ValueError("adjudication precedes one or both raw submissions")
            if adjudicated_at > released_at:
                raise ValueError("adjudication occurs after final release")
            if judgment.adjudication_status is not adjudication.status:
                raise ValueError("final adjudication status differs from true record")
            if _judgment_values(judgment) != _adjudication_values(adjudication):
                raise ValueError("final labels differ from true adjudication")
            _assert_evidence_coverage(
                packet=packet,
                judgments=adjudication.final_evidence_judgments,
                assessability=adjudication.final_assessability,
            )

    if used_raw != set(raw_index):
        raise ValueError("raw annotation inputs are missing from or orphaned by final release")
    if used_adjudications != set(adjudication_index):
        raise ValueError("adjudication inputs are missing from or orphaned by final release")

    units_by_case: dict[str, set[str]] = {}
    case_sha_by_id: dict[str, str] = {}
    for unit in reviewer_manifest.units:
        units_by_case.setdefault(unit.case_id, set()).add(unit.blinded_unit_id)
        previous = case_sha_by_id.setdefault(unit.case_id, unit.case_sha256)
        if previous != unit.case_sha256:
            raise ValueError("one case ID has multiple SHA-256 identities")
    partitions = {item.case_id: item for item in release.duplicate_partitions}
    if set(partitions) != set(units_by_case):
        raise ValueError("final release does not exactly cover case duplicate partitions")
    raw_partition_index: dict[str, RawExpertDuplicatePartitionV1] = {}
    for raw_partition in raw_duplicate_partitions:
        if raw_partition.partition_id in raw_partition_index:
            raise ValueError("raw duplicate partitions contain duplicate identity")
        raw_partition_index[raw_partition.partition_id] = raw_partition
    used_raw_partitions: set[str] = set()
    for case_id, partition in partitions.items():
        if (
            partition.reviewer_manifest_id,
            partition.reviewer_manifest_sha256,
            partition.expert_registry_id,
            partition.expert_registry_sha256,
            partition.case_sha256,
            partition.review_round,
            partition.annotation_guide_sha256,
            partition.reviewer_ids,
        ) != (
            release.reviewer_manifest_id,
            release.reviewer_manifest_sha256,
            release.expert_registry_id,
            release.expert_registry_sha256,
            case_sha_by_id[case_id],
            release.review_round,
            release.annotation_guide_sha256,
            assignments[case_id].reviewer_ids,
        ):
            raise ValueError("expert duplicate partition drifts from release identities")
        if (
            partition.provenance is DuplicatePartitionProvenance.ADJUDICATED
            and partition.adjudicator_id != assignments[case_id].adjudicator_id
        ):
            raise ValueError("duplicate partition names a fake adjudicator")
        members = {
            unit_id for cluster in partition.clusters for unit_id in cluster.blinded_unit_ids
        }
        if members != units_by_case[case_id]:
            raise ValueError("expert duplicate partition does not exactly cover case units")
        true_raw_partitions: list[RawExpertDuplicatePartitionV1] = []
        for ref in partition.reviewer_partitions:
            raw_partition = raw_partition_index.get(ref.partition_id)
            if raw_partition is None or (
                raw_partition.partition_sha256,
                raw_partition.reviewer_id,
            ) != (ref.partition_sha256, ref.reviewer_id):
                raise ValueError("final duplicate partition contains a foreign raw ref")
            if (
                raw_partition.reviewer_manifest_id,
                raw_partition.reviewer_manifest_sha256,
                raw_partition.expert_registry_id,
                raw_partition.expert_registry_sha256,
                raw_partition.case_id,
                raw_partition.case_sha256,
                raw_partition.review_round,
                raw_partition.annotation_guide_sha256,
            ) != (
                release.reviewer_manifest_id,
                release.reviewer_manifest_sha256,
                release.expert_registry_id,
                release.expert_registry_sha256,
                case_id,
                case_sha_by_id[case_id],
                release.review_round,
                release.annotation_guide_sha256,
            ):
                raise ValueError("raw duplicate partition drifts from release identities")
            raw_members = {
                unit_id
                for cluster in raw_partition.clusters
                for unit_id in cluster.blinded_unit_ids
            }
            if raw_members != units_by_case[case_id]:
                raise ValueError("raw duplicate partition does not exactly cover case units")
            if _timestamp(raw_partition.submitted_at) < latest_submission_by_case[case_id]:
                raise ValueError("duplicate partition was submitted before all unit reviews")
            if _timestamp(raw_partition.submitted_at) > _timestamp(partition.finalized_at):
                raise ValueError("final duplicate partition predates a raw partition")
            true_raw_partitions.append(raw_partition)
            used_raw_partitions.add(raw_partition.partition_id)
        first_signature, second_signature = (
            _partition_signature(item.clusters) for item in true_raw_partitions
        )
        final_signature = _partition_signature(partition.clusters)
        if partition.provenance is DuplicatePartitionProvenance.AGREED_REVIEWERS:
            if first_signature != second_signature or final_signature != first_signature:
                raise ValueError("AGREED_REVIEWERS partition does not close raw agreement")
        elif first_signature == second_signature:
            raise ValueError("identical raw partitions cannot be routed to adjudication")
        if _timestamp(partition.finalized_at) > released_at:
            raise ValueError("duplicate partition was finalized after release")
    if used_raw_partitions != set(raw_partition_index):
        raise ValueError("raw duplicate partitions are missing from or orphaned by release")


class ReviewerArtifactRefV2(StrictModel):
    """Exact public/private artifact pair used by one real reviewer.

    The raw annotation schema predates ``reviewer_packet_sha256``.  V2 therefore
    binds the raw record to the sealed reviewer packet by replaying this artifact
    pair and its reviewer-specific blind ID; it never invents a missing raw field.
    """

    reviewer_id: Identifier
    reviewer_manifest_id: Identifier
    reviewer_manifest_sha256: Sha256
    private_identity_map_id: Identifier
    private_identity_map_sha256: Sha256
    blinded_reviewer_id: Identifier
    blinded_unit_id: Identifier
    reviewer_packet_id: Identifier
    reviewer_packet_sha256: Sha256


class FinalExpertJudgmentV2(StrictModel):
    """Final label for one private pooled unit, after exactly two raw reviews."""

    schema_version: Literal["flatband-final-expert-judgment-v2"] = (
        "flatband-final-expert-judgment-v2"
    )
    judgment_id: Identifier
    judgment_sha256: Sha256
    execution_release_id: Identifier
    execution_release_sha256: Sha256
    expert_registry_id: Identifier
    expert_registry_sha256: Sha256
    pooled_unit_id: Identifier
    case_id: Identifier
    case_sha256: Sha256
    packet_id: Identifier
    packet_sha256: Sha256
    review_round: Annotated[int, Field(ge=1, le=2)]
    annotation_guide_sha256: Sha256
    reviewer_artifacts: Annotated[
        tuple[ReviewerArtifactRefV2, ...], Field(min_length=2, max_length=2)
    ]
    provenance: GoldProvenance
    raw_annotations: Annotated[
        tuple[AnnotationRefV1, ...], Field(min_length=2, max_length=2)
    ]
    adjudication: AdjudicationRefV1 | None = None
    adjudication_status: AdjudicationStatus | None = None
    final_assessability: Assessability | None = None
    final_relevance_grade: Annotated[int, Field(ge=0, le=3)] | None = None
    final_evidence_valid: bool | None = None
    final_evidence_judgments: Annotated[
        tuple[EvidenceJudgmentV1, ...], Field(max_length=64)
    ] = ()
    final_bridge_judgment: BridgeJudgmentV1 | None = None
    final_mechanism_family: MechanismFamily | None = None
    final_hard_fail_reasons: Annotated[
        tuple[HardFailReason, ...], Field(max_length=16)
    ] = ()
    denominator_included: bool
    metric_relevance_gain: Annotated[int, Field(ge=0, le=3)]
    metric_evidence_gain: Annotated[int, Field(ge=0, le=1)]
    unresolvable: bool
    scientific_conclusion: Literal[False] = False

    @model_validator(mode="after")
    def validate_judgment(self) -> "FinalExpertJudgmentV2":
        artifact_ids = tuple(item.reviewer_id for item in self.reviewer_artifacts)
        _require_sorted_unique(artifact_ids, "reviewer artifact reviewer IDs")
        raw_ids = tuple(item.reviewer_id for item in self.raw_annotations)
        _require_sorted_unique(raw_ids, "raw annotation reviewer IDs")
        if artifact_ids != raw_ids:
            raise ValueError("reviewer artifacts and raw refs must name the same reviewers")
        evidence_ids = tuple(
            item.evidence_link_id for item in self.final_evidence_judgments
        )
        _require_sorted_unique(evidence_ids, "final evidence judgments")
        hard_fail_values = tuple(item.value for item in self.final_hard_fail_reasons)
        _require_sorted_unique(hard_fail_values, "final hard-fail reasons")
        if self.provenance is GoldProvenance.AGREED_RAW:
            if self.adjudication is not None or self.adjudication_status is not None:
                raise ValueError("agreed raw judgment cannot reference adjudication")
        elif self.adjudication is None or self.adjudication_status is None:
            raise ValueError("adjudicated judgment requires adjudication identity and status")

        candidate_values = (
            self.final_relevance_grade,
            self.final_evidence_valid,
            self.final_bridge_judgment,
            self.final_mechanism_family,
        )
        if self.adjudication_status is AdjudicationStatus.UNRESOLVABLE:
            if (
                self.final_assessability is not None
                or any(value is not None for value in candidate_values)
                or self.final_evidence_judgments
                or self.final_hard_fail_reasons
                or not self.unresolvable
            ):
                raise ValueError("unresolvable judgment cannot carry final labels")
        elif self.final_assessability is Assessability.CASE_INVALID:
            if (
                any(value is not None for value in candidate_values)
                or self.final_evidence_judgments
                or self.final_hard_fail_reasons
                or self.unresolvable
            ):
                raise ValueError("invalid case cannot carry candidate labels")
        elif self.final_assessability is Assessability.SYSTEM_PACKET_INVALID:
            if self.final_relevance_grade != 0 or not self.final_hard_fail_reasons:
                raise ValueError("invalid packet requires grade zero and hard fail")
            if (
                self.final_evidence_valid is not None
                or self.final_evidence_judgments
                or self.final_bridge_judgment is not None
                or self.final_mechanism_family is not None
                or self.unresolvable
            ):
                raise ValueError("invalid packet cannot carry substantive final labels")
        elif self.final_assessability is Assessability.ASSESSABLE:
            if any(value is None for value in candidate_values):
                raise ValueError("assessable final judgment requires all core labels")
            if not self.final_evidence_judgments:
                raise ValueError("assessable final judgment requires evidence judgments")
            if self.final_hard_fail_reasons and self.final_relevance_grade != 0:
                raise ValueError("any final hard fail requires grade zero")
            if self.final_relevance_grade is not None and self.final_relevance_grade >= 2:
                if self.final_evidence_valid is not True or self.final_hard_fail_reasons:
                    raise ValueError("grade two or three requires valid evidence and no hard fail")
                if not any(
                    item.expert_relation is ExpertEvidenceRelation.VALID_SUPPORT
                    for item in self.final_evidence_judgments
                ):
                    raise ValueError("grade two or three requires valid support")
                if (
                    self.final_bridge_judgment is None
                    or self.final_bridge_judgment.overall
                    not in {BridgeVerdict.CORRECT, BridgeVerdict.CONDITIONAL}
                ):
                    raise ValueError("grade two or three requires a valid bridge")
            if self.final_relevance_grade == 3 and (
                self.final_bridge_judgment is None
                or self.final_bridge_judgment.overall is not BridgeVerdict.CORRECT
            ):
                raise ValueError("grade three requires a correct bridge")
            if self.unresolvable:
                raise ValueError("resolved assessment cannot be unresolvable")
        else:
            raise ValueError("final judgment requires assessability or UNRESOLVABLE")

        expected_denominator = self.final_assessability is not Assessability.CASE_INVALID
        if self.denominator_included is not expected_denominator:
            raise ValueError("metric denominator inclusion differs from assessability")
        expected_grade = 0 if self.unresolvable else (self.final_relevance_grade or 0)
        expected_evidence = 0 if self.unresolvable else int(self.final_evidence_valid is True)
        if self.metric_relevance_gain != expected_grade:
            raise ValueError("metric relevance gain differs from final label")
        if self.metric_evidence_gain != expected_evidence:
            raise ValueError("metric evidence gain differs from final label")
        _assert_addressed(
            self,
            id_field="judgment_id",
            sha_field="judgment_sha256",
            prefix="final-expert-judgment-v2",
        )
        return self


class ReviewerDuplicateClusterV2(StrictModel):
    cluster_id: Identifier
    blinded_unit_ids: Annotated[
        tuple[Identifier, ...], Field(min_length=1, max_length=100)
    ]

    @model_validator(mode="after")
    def validate_members(self) -> "ReviewerDuplicateClusterV2":
        _require_sorted_unique(self.blinded_unit_ids, "reviewer duplicate members")
        return self


class PooledDuplicateClusterV2(StrictModel):
    cluster_id: Identifier
    pooled_unit_ids: Annotated[
        tuple[Identifier, ...], Field(min_length=1, max_length=100)
    ]

    @model_validator(mode="after")
    def validate_members(self) -> "PooledDuplicateClusterV2":
        _require_sorted_unique(self.pooled_unit_ids, "pooled duplicate members")
        return self


class DuplicatePartitionRefV2(StrictModel):
    partition_id: Identifier
    partition_sha256: Sha256
    reviewer_id: Identifier


class DuplicatePartitionAdjudicationRefV2(StrictModel):
    adjudication_id: Identifier
    adjudication_sha256: Sha256
    adjudicator_id: Identifier


class RawDuplicatePartitionV2(StrictModel):
    """One reviewer's sealed partition in that reviewer's own blind namespace."""

    schema_version: Literal["flatband-raw-duplicate-partition-v2"] = (
        "flatband-raw-duplicate-partition-v2"
    )
    partition_id: Identifier
    partition_sha256: Sha256
    reviewer_manifest_id: Identifier
    reviewer_manifest_sha256: Sha256
    private_identity_map_id: Identifier
    private_identity_map_sha256: Sha256
    expert_registry_id: Identifier
    expert_registry_sha256: Sha256
    case_id: Identifier
    case_sha256: Sha256
    review_round: Annotated[int, Field(ge=1, le=2)]
    annotation_guide_sha256: Sha256
    reviewer_id: Identifier
    clusters: Annotated[
        tuple[ReviewerDuplicateClusterV2, ...], Field(min_length=1, max_length=100)
    ]
    submitted_at: Annotated[str, Field(min_length=20, max_length=40)]
    sealed: Literal[True] = True
    scientific_conclusion: Literal[False] = False

    @field_validator("submitted_at")
    @classmethod
    def validate_time(cls, value: str) -> str:
        _timestamp(value)
        return value

    @model_validator(mode="after")
    def validate_partition(self) -> "RawDuplicatePartitionV2":
        keys = tuple((item.blinded_unit_ids, item.cluster_id) for item in self.clusters)
        if keys != tuple(sorted(set(keys))):
            raise ValueError("reviewer duplicate clusters must be member-sorted and unique")
        members = tuple(unit for cluster in self.clusters for unit in cluster.blinded_unit_ids)
        if len(members) != len(set(members)):
            raise ValueError("a reviewer blind ID cannot occur in two clusters")
        for cluster in self.clusters:
            expected = deterministic_id(
                "reviewer-duplicate-v2",
                {
                    "case_id": self.case_id,
                    "reviewer_id": self.reviewer_id,
                    "blinded_unit_ids": cluster.blinded_unit_ids,
                },
            )
            if cluster.cluster_id != expected:
                raise ValueError("raw cluster identity must derive from reviewer membership")
        _assert_addressed(
            self,
            id_field="partition_id",
            sha_field="partition_sha256",
            prefix="raw-duplicate-partition-v2",
        )
        return self


class DuplicatePartitionAdjudicationV2(StrictModel):
    """Content-addressed resolution of one genuine two-reviewer partition dispute."""

    schema_version: Literal["flatband-duplicate-partition-adjudication-v2"] = (
        "flatband-duplicate-partition-adjudication-v2"
    )
    adjudication_id: Identifier
    adjudication_sha256: Sha256
    execution_release_id: Identifier
    execution_release_sha256: Sha256
    expert_registry_id: Identifier
    expert_registry_sha256: Sha256
    case_id: Identifier
    case_sha256: Sha256
    review_round: Annotated[int, Field(ge=1, le=2)]
    annotation_guide_sha256: Sha256
    pooled_unit_ids: Annotated[
        tuple[Identifier, ...], Field(min_length=1, max_length=100)
    ]
    reviewer_partitions: Annotated[
        tuple[DuplicatePartitionRefV2, ...], Field(min_length=2, max_length=2)
    ]
    adjudicator_id: Identifier
    resolution_reason_code: Identifier
    rationale: LongText
    final_clusters: Annotated[
        tuple[PooledDuplicateClusterV2, ...], Field(min_length=1, max_length=100)
    ]
    adjudicated_at: Annotated[str, Field(min_length=20, max_length=40)]
    sealed: Literal[True] = True
    scientific_conclusion: Literal[False] = False

    @field_validator("adjudicated_at")
    @classmethod
    def validate_time(cls, value: str) -> str:
        _timestamp(value)
        return value

    @model_validator(mode="after")
    def validate_adjudication(self) -> "DuplicatePartitionAdjudicationV2":
        _require_sorted_unique(self.pooled_unit_ids, "adjudicated pooled universe")
        ref_keys = tuple(
            (item.reviewer_id, item.partition_id, item.partition_sha256)
            for item in self.reviewer_partitions
        )
        if ref_keys != tuple(sorted(set(ref_keys))):
            raise ValueError("adjudicated raw partition refs must be reviewer-sorted")
        if self.adjudicator_id in {
            item.reviewer_id for item in self.reviewer_partitions
        }:
            raise ValueError("partition adjudicator must differ from both reviewers")
        keys = tuple(
            (item.pooled_unit_ids, item.cluster_id) for item in self.final_clusters
        )
        if keys != tuple(sorted(set(keys))):
            raise ValueError("adjudicated pooled clusters must be member-sorted")
        members = tuple(
            unit for cluster in self.final_clusters for unit in cluster.pooled_unit_ids
        )
        if len(members) != len(set(members)) or set(members) != set(self.pooled_unit_ids):
            raise ValueError("adjudication clusters must partition its exact pool universe")
        for cluster in self.final_clusters:
            expected = deterministic_id(
                "pooled-duplicate-v2",
                {"case_id": self.case_id, "pooled_unit_ids": cluster.pooled_unit_ids},
            )
            if cluster.cluster_id != expected:
                raise ValueError("adjudicated cluster ID must derive from pooled membership")
        _assert_addressed(
            self,
            id_field="adjudication_id",
            sha_field="adjudication_sha256",
            prefix="duplicate-adjudication-v2",
        )
        return self


class FinalDuplicatePartitionV2(StrictModel):
    """Case partition in the only identity namespace permitted in final Gold."""

    schema_version: Literal["flatband-final-duplicate-partition-v2"] = (
        "flatband-final-duplicate-partition-v2"
    )
    partition_id: Identifier
    partition_sha256: Sha256
    execution_release_id: Identifier
    execution_release_sha256: Sha256
    expert_registry_id: Identifier
    expert_registry_sha256: Sha256
    case_id: Identifier
    case_sha256: Sha256
    review_round: Annotated[int, Field(ge=1, le=2)]
    annotation_guide_sha256: Sha256
    reviewer_ids: Annotated[tuple[Identifier, ...], Field(min_length=2, max_length=2)]
    reviewer_partitions: Annotated[
        tuple[DuplicatePartitionRefV2, ...], Field(min_length=2, max_length=2)
    ]
    provenance: DuplicatePartitionProvenance
    adjudication: DuplicatePartitionAdjudicationRefV2 | None = None
    clusters: Annotated[
        tuple[PooledDuplicateClusterV2, ...], Field(min_length=1, max_length=100)
    ]
    finalized_at: Annotated[str, Field(min_length=20, max_length=40)]
    scientific_conclusion: Literal[False] = False

    @field_validator("finalized_at")
    @classmethod
    def validate_time(cls, value: str) -> str:
        _timestamp(value)
        return value

    @model_validator(mode="after")
    def validate_partition(self) -> "FinalDuplicatePartitionV2":
        _require_sorted_unique(self.reviewer_ids, "partition reviewers")
        ref_keys = tuple(
            (item.reviewer_id, item.partition_id, item.partition_sha256)
            for item in self.reviewer_partitions
        )
        if ref_keys != tuple(sorted(set(ref_keys))):
            raise ValueError("reviewer partition refs must be reviewer-sorted and unique")
        if tuple(item.reviewer_id for item in self.reviewer_partitions) != self.reviewer_ids:
            raise ValueError("reviewer partition refs must cover both reviewers")
        if self.provenance is DuplicatePartitionProvenance.AGREED_REVIEWERS:
            if self.adjudication is not None:
                raise ValueError("agreed duplicate partition cannot carry adjudication")
        elif self.adjudication is None or self.adjudication.adjudicator_id in self.reviewer_ids:
            raise ValueError("adjudicated partition requires a distinct adjudication ref")
        keys = tuple((item.pooled_unit_ids, item.cluster_id) for item in self.clusters)
        if keys != tuple(sorted(set(keys))):
            raise ValueError("pooled duplicate clusters must be member-sorted and unique")
        members = tuple(unit for cluster in self.clusters for unit in cluster.pooled_unit_ids)
        if len(members) != len(set(members)):
            raise ValueError("a pooled unit cannot occur in two clusters")
        for cluster in self.clusters:
            expected = deterministic_id(
                "pooled-duplicate-v2",
                {"case_id": self.case_id, "pooled_unit_ids": cluster.pooled_unit_ids},
            )
            if cluster.cluster_id != expected:
                raise ValueError("final cluster identity must derive from pooled membership")
        _assert_addressed(
            self,
            id_field="partition_id",
            sha_field="partition_sha256",
            prefix="final-duplicate-partition-v2",
        )
        return self


class FinalGoldReleaseV2(StrictModel):
    schema_version: Literal["flatband-final-gold-release-v2"] = (
        "flatband-final-gold-release-v2"
    )
    release_id: Identifier
    release_sha256: Sha256
    execution_release_id: Identifier
    execution_release_sha256: Sha256
    expert_registry_id: Identifier
    expert_registry_sha256: Sha256
    review_round: Annotated[int, Field(ge=1, le=2)]
    annotation_guide_sha256: Sha256
    judgments: Annotated[
        tuple[FinalExpertJudgmentV2, ...], Field(min_length=1, max_length=100_000)
    ]
    duplicate_partitions: Annotated[
        tuple[FinalDuplicatePartitionV2, ...], Field(min_length=1, max_length=10_000)
    ]
    unresolvable_unit_ids: Annotated[tuple[Identifier, ...], Field(max_length=100_000)]
    denominator_unit_count: Annotated[int, Field(ge=0, le=100_000)]
    unresolvable_unit_count: Annotated[int, Field(ge=0, le=100_000)]
    released_at: Annotated[str, Field(min_length=20, max_length=40)]
    scientific_conclusion: Literal[False] = False

    @field_validator("released_at")
    @classmethod
    def validate_time(cls, value: str) -> str:
        _timestamp(value)
        return value

    @model_validator(mode="after")
    def validate_release(self) -> "FinalGoldReleaseV2":
        unit_ids = tuple(item.pooled_unit_id for item in self.judgments)
        _require_sorted_unique(unit_ids, "final pooled unit IDs")
        by_case: dict[str, list[FinalExpertJudgmentV2]] = {}
        for judgment in self.judgments:
            by_case.setdefault(judgment.case_id, []).append(judgment)
        for case_judgments in by_case.values():
            invalid = sum(
                item.final_assessability is Assessability.CASE_INVALID
                for item in case_judgments
            )
            if invalid not in {0, len(case_judgments)}:
                raise ValueError("CASE_INVALID must be symmetric across the complete case")
        case_ids = tuple(item.case_id for item in self.duplicate_partitions)
        _require_sorted_unique(case_ids, "final duplicate partition cases")
        expected_unresolvable = tuple(
            item.pooled_unit_id for item in self.judgments if item.unresolvable
        )
        if self.unresolvable_unit_ids != expected_unresolvable:
            raise ValueError("unresolvable unit list does not close")
        if self.denominator_unit_count != sum(
            item.denominator_included for item in self.judgments
        ):
            raise ValueError("denominator count does not close")
        if self.unresolvable_unit_count != len(expected_unresolvable):
            raise ValueError("unresolvable count does not close")
        _assert_addressed(
            self,
            id_field="release_id",
            sha_field="release_sha256",
            prefix="final-gold-release-v2",
        )
        return self


def _index_unique(values: tuple[StrictModel, ...], field: str, label: str) -> dict[str, StrictModel]:
    result: dict[str, StrictModel] = {}
    for value in values:
        key = getattr(value, field)
        if key in result:
            raise ValueError(f"duplicate {label} identity")
        result[key] = value
    return result


def _pooled_partition_signature(
    clusters: tuple[PooledDuplicateClusterV2, ...],
) -> tuple[tuple[str, ...], ...]:
    return tuple(sorted(item.pooled_unit_ids for item in clusters))


def _pooled_clusters_v2(
    *, case_id: str, signature: tuple[tuple[str, ...], ...]
) -> tuple[PooledDuplicateClusterV2, ...]:
    return tuple(
        PooledDuplicateClusterV2(
            cluster_id=deterministic_id(
                "pooled-duplicate-v2",
                {"case_id": case_id, "pooled_unit_ids": members},
            ),
            pooled_unit_ids=members,
        )
        for members in signature
    )


def _derive_final_gold_release_v2(
    *,
    frozen_case_release: FrozenCaseReleaseV3 | None,
    execution_release: ExecutionReleaseV2 | ExecutionReleaseV3,
    pre_run_eligibility_release: (
        PreRunEligibilityReleaseV2 | PreRunEligibilityReleaseV3
    ),
    reviewer_manifests: tuple[ReviewerManifestV2, ...],
    private_identity_maps: tuple[PrivateIdentityMapV2, ...],
    expert_registry: ExpertStudyRegistryV2,
    evidence_excerpts: tuple[EvidenceExcerptV2, ...],
    blind_key: bytes,
    renderer_sha256: str,
    raw_annotations: tuple[RawExpertAnnotationV1, ...],
    adjudications: tuple[ExpertAdjudicationV1, ...],
    raw_duplicate_partitions: tuple[RawDuplicatePartitionV2, ...],
    released_at: str,
    duplicate_partition_adjudications: tuple[
        DuplicatePartitionAdjudicationV2, ...
    ] = (),
    reviewer_prevalidated: bool = False,
) -> FinalGoldReleaseV2:
    """Derive formal Gold from sealed reviewer decisions without recursion.

    Final labels, provenance, metric counts, pooled cluster identities and
    denominator counts are outputs only.  The caller supplies only the sealed
    raw decisions and the adjudications that are necessary for genuine
    disagreements.
    """

    release_time = _timestamp(released_at)
    if frozen_case_release is None:
        frozen = None
        execution = _revalidate(execution_release, ExecutionReleaseV2)
        eligibility = _revalidate(
            pre_run_eligibility_release, PreRunEligibilityReleaseV2
        )
    else:
        execution = _revalidate(execution_release, ExecutionReleaseV3)
        frozen = execution.frozen_case_release
        eligibility = execution.pre_run_eligibility_release
        if frozen_case_release != frozen:
            raise ValueError("Gold receives a foreign FrozenCaseReleaseV3")
        if pre_run_eligibility_release != eligibility:
            raise ValueError("Gold receives a foreign eligibility V3 release")
    registry = _revalidate(expert_registry, ExpertStudyRegistryV2)
    if frozen is not None:
        if frozen.expert_registry != registry:
            raise ValueError("Gold receives a foreign expert registry")
        registry = frozen.expert_registry
    manifests = tuple(
        _revalidate(item, ReviewerManifestV2) for item in reviewer_manifests
    )
    identity_maps = tuple(
        _revalidate(item, PrivateIdentityMapV2)
        for item in private_identity_maps
    )
    excerpts = tuple(
        _revalidate(item, EvidenceExcerptV2) for item in evidence_excerpts
    )
    raws = tuple(
        _revalidate(item, RawExpertAnnotationV1) for item in raw_annotations
    )
    decisions = tuple(
        _revalidate(item, ExpertAdjudicationV1) for item in adjudications
    )
    raw_partitions = tuple(
        _revalidate(item, RawDuplicatePartitionV2)
        for item in raw_duplicate_partitions
    )
    partition_decisions = tuple(
        _revalidate(item, DuplicatePartitionAdjudicationV2)
        for item in duplicate_partition_adjudications
    )
    phase = execution.execution_matrix.phase
    if phase not in {ExecutionPhase.PILOT_R1, ExecutionPhase.PILOT_R2}:
        raise ValueError("formal Gold builder requires a Pilot execution phase")
    review_round = 1 if phase is ExecutionPhase.PILOT_R1 else 2

    if not reviewer_prevalidated:
        reviewer_inputs = {
            "execution_release": execution,
            "pre_run_eligibility_release": eligibility,
            "expert_registry": registry,
            "reviewer_manifests": manifests,
            "private_identity_maps": identity_maps,
            "evidence_excerpts": excerpts,
            "blind_key": blind_key,
            "renderer_sha256": renderer_sha256,
        }
        if frozen is None:
            assert_reviewer_release_legacy_v2_upstream_exact_coverage(
                **reviewer_inputs
            )
        else:
            assert_reviewer_release_exact_coverage_v2(
                frozen_case_release=frozen,
                **reviewer_inputs,
            )

    manifest_by_id = {item.manifest_id: item for item in manifests}
    if len(manifest_by_id) != len(manifests):
        raise ValueError("Gold builder received duplicate reviewer manifests")
    assignment_by_case = {item.case_id: item for item in registry.assignments}
    pool_meta: dict[str, tuple[str, str, str, str]] = {}
    packet_to_pool: dict[tuple[str, str], str] = {}
    pool_to_packet: dict[str, tuple[str, str]] = {}
    artifact_by_pool_reviewer: dict[
        tuple[str, str], ReviewerArtifactRefV2
    ] = {}
    blind_to_pool: dict[tuple[str, str], str] = {}
    for identity_map in identity_maps:
        manifest = manifest_by_id.get(identity_map.reviewer_manifest_id)
        if manifest is None:
            raise ValueError("Gold builder private map references a foreign manifest")
        public_by_id = {
            item.reviewer_packet_id: item for item in manifest.packets
        }
        for entry in identity_map.entries:
            packet_key = (entry.packet_id, entry.packet_sha256)
            pool_id = entry.pooled_unit_id
            if packet_to_pool.setdefault(packet_key, pool_id) != pool_id:
                raise ValueError("one packet aliases multiple pooled units")
            if pool_to_packet.setdefault(pool_id, packet_key) != packet_key:
                raise ValueError("one pooled unit aliases multiple packets")
            meta = (
                entry.case_id,
                entry.case_sha256,
                entry.packet_id,
                entry.packet_sha256,
            )
            if pool_meta.setdefault(pool_id, meta) != meta:
                raise ValueError("one pooled unit changes case or packet identity")
            public = public_by_id[entry.reviewer_packet_id]
            artifact = ReviewerArtifactRefV2(
                reviewer_id=identity_map.expert_id,
                reviewer_manifest_id=manifest.manifest_id,
                reviewer_manifest_sha256=manifest.manifest_sha256,
                private_identity_map_id=identity_map.identity_map_id,
                private_identity_map_sha256=identity_map.identity_map_sha256,
                blinded_reviewer_id=identity_map.blinded_reviewer_id,
                blinded_unit_id=entry.blinded_unit_id,
                reviewer_packet_id=public.reviewer_packet_id,
                reviewer_packet_sha256=public.reviewer_packet_sha256,
            )
            artifact_key = (pool_id, identity_map.expert_id)
            previous = artifact_by_pool_reviewer.setdefault(
                artifact_key, artifact
            )
            if previous != artifact:
                raise ValueError("repeated positions change a reviewer artifact")
            blind_key_for_reviewer = (
                identity_map.expert_id,
                entry.blinded_unit_id,
            )
            if blind_to_pool.setdefault(
                blind_key_for_reviewer, pool_id
            ) != pool_id:
                raise ValueError("one reviewer blind ID aliases pooled units")

    expected_artifact_keys = {
        (pool_id, reviewer_id)
        for pool_id, meta in pool_meta.items()
        for reviewer_id in assignment_by_case[meta[0]].reviewer_ids
    }
    if set(artifact_by_pool_reviewer) != expected_artifact_keys:
        raise ValueError("every present pool requires both reviewer artifacts")

    raw_by_pool_reviewer: dict[
        tuple[str, str], RawExpertAnnotationV1
    ] = {}
    for raw in raws:
        pool_id = blind_to_pool.get((raw.reviewer_id, raw.blinded_unit_id))
        if pool_id is None:
            raise ValueError("raw annotation references a foreign reviewer blind ID")
        key = (pool_id, raw.reviewer_id)
        if key in raw_by_pool_reviewer:
            raise ValueError("one reviewer supplied duplicate labels for a pool")
        raw_by_pool_reviewer[key] = raw
    if set(raw_by_pool_reviewer) != expected_artifact_keys:
        raise ValueError("sealed raw annotations do not exactly cover present pools")

    adjudication_by_pool: dict[str, ExpertAdjudicationV1] = {}
    for decision in decisions:
        if decision.blinded_unit_id in adjudication_by_pool:
            raise ValueError("duplicate label adjudication for one pooled unit")
        adjudication_by_pool[decision.blinded_unit_id] = decision

    judgments: list[FinalExpertJudgmentV2] = []
    used_adjudications: set[str] = set()
    for pool_id in sorted(pool_meta):
        case_id, case_sha, packet_id, packet_sha = pool_meta[pool_id]
        assignment = assignment_by_case[case_id]
        artifacts = tuple(
            artifact_by_pool_reviewer[(pool_id, reviewer_id)]
            for reviewer_id in assignment.reviewer_ids
        )
        unit_raws = tuple(
            raw_by_pool_reviewer[(pool_id, reviewer_id)]
            for reviewer_id in assignment.reviewer_ids
        )
        raw_refs = tuple(
            AnnotationRefV1(
                annotation_id=item.annotation_id,
                annotation_sha256=item.annotation_sha256,
                reviewer_id=item.reviewer_id,
            )
            for item in unit_raws
        )
        disagreements = _raw_disagreements(*unit_raws)
        decision = adjudication_by_pool.get(pool_id)
        if not disagreements:
            if decision is not None:
                raise ValueError("agreed raw labels cannot consume adjudication")
            provenance = GoldProvenance.AGREED_RAW
            adjudication_ref = None
            adjudication_status = None
            final_values = _annotation_values(unit_raws[0])
            unresolvable = False
        else:
            if decision is None:
                raise ValueError("raw label disagreement requires adjudication")
            provenance = GoldProvenance.ADJUDICATED
            adjudication_ref = AdjudicationRefV1(
                adjudication_id=decision.adjudication_id,
                adjudication_sha256=decision.adjudication_sha256,
                adjudicator_id=decision.adjudicator_id,
            )
            adjudication_status = decision.status
            final_values = _adjudication_values(decision)
            unresolvable = decision.status is AdjudicationStatus.UNRESOLVABLE
            used_adjudications.add(decision.adjudication_id)
        (
            final_assessability,
            final_relevance_grade,
            final_evidence_valid,
            final_evidence_judgments,
            final_bridge_judgment,
            final_mechanism_family,
            final_hard_fail_reasons,
        ) = final_values
        judgments.append(
            _build_addressed(
                FinalExpertJudgmentV2,
                id_field="judgment_id",
                sha_field="judgment_sha256",
                prefix="final-expert-judgment-v2",
                values={
                    "execution_release_id": execution.release_id,
                    "execution_release_sha256": execution.release_sha256,
                    "expert_registry_id": registry.registry_id,
                    "expert_registry_sha256": registry.registry_sha256,
                    "pooled_unit_id": pool_id,
                    "case_id": case_id,
                    "case_sha256": case_sha,
                    "packet_id": packet_id,
                    "packet_sha256": packet_sha,
                    "review_round": review_round,
                    "annotation_guide_sha256": registry.annotation_guide_sha256,
                    "reviewer_artifacts": artifacts,
                    "provenance": provenance,
                    "raw_annotations": raw_refs,
                    "adjudication": adjudication_ref,
                    "adjudication_status": adjudication_status,
                    "final_assessability": final_assessability,
                    "final_relevance_grade": final_relevance_grade,
                    "final_evidence_valid": final_evidence_valid,
                    "final_evidence_judgments": final_evidence_judgments,
                    "final_bridge_judgment": final_bridge_judgment,
                    "final_mechanism_family": final_mechanism_family,
                    "final_hard_fail_reasons": final_hard_fail_reasons,
                    "denominator_included": (
                        final_assessability is not Assessability.CASE_INVALID
                    ),
                    "metric_relevance_gain": (
                        0
                        if unresolvable
                        else (final_relevance_grade or 0)
                    ),
                    "metric_evidence_gain": (
                        0
                        if unresolvable
                        else int(final_evidence_valid is True)
                    ),
                    "unresolvable": unresolvable,
                },
            )
        )
    if used_adjudications != {
        item.adjudication_id for item in decisions
    }:
        raise ValueError("label adjudications contain an unnecessary orphan")

    pools_by_case: dict[str, set[str]] = {}
    for pool_id, meta in pool_meta.items():
        pools_by_case.setdefault(meta[0], set()).add(pool_id)
    raw_partition_by_case_reviewer: dict[
        tuple[str, str], RawDuplicatePartitionV2
    ] = {}
    for partition in raw_partitions:
        key = (partition.case_id, partition.reviewer_id)
        if key in raw_partition_by_case_reviewer:
            raise ValueError("duplicate raw partition for one case/reviewer")
        raw_partition_by_case_reviewer[key] = partition
    expected_partition_keys = {
        (case_id, reviewer_id)
        for case_id in pools_by_case
        for reviewer_id in assignment_by_case[case_id].reviewer_ids
    }
    if set(raw_partition_by_case_reviewer) != expected_partition_keys:
        raise ValueError("raw duplicate partitions do not exactly cover present cases")
    partition_decision_by_case: dict[
        str, DuplicatePartitionAdjudicationV2
    ] = {}
    for decision in partition_decisions:
        if decision.case_id in partition_decision_by_case:
            raise ValueError("duplicate partition adjudication for one case")
        partition_decision_by_case[decision.case_id] = decision

    final_partitions: list[FinalDuplicatePartitionV2] = []
    used_partition_adjudications: set[str] = set()
    for case_id in sorted(pools_by_case):
        assignment = assignment_by_case[case_id]
        case_pools = pools_by_case[case_id]
        reviewer_partitions = tuple(
            raw_partition_by_case_reviewer[(case_id, reviewer_id)]
            for reviewer_id in assignment.reviewer_ids
        )
        partition_refs = tuple(
            DuplicatePartitionRefV2(
                partition_id=item.partition_id,
                partition_sha256=item.partition_sha256,
                reviewer_id=item.reviewer_id,
            )
            for item in reviewer_partitions
        )
        signatures: list[tuple[tuple[str, ...], ...]] = []
        for partition in reviewer_partitions:
            mapped_clusters: list[tuple[str, ...]] = []
            observed: set[str] = set()
            for cluster in partition.clusters:
                mapped_values: list[str] = []
                for blind_id in cluster.blinded_unit_ids:
                    pool_id = blind_to_pool.get(
                        (partition.reviewer_id, blind_id)
                    )
                    if pool_id is None or pool_id not in case_pools:
                        raise ValueError(
                            "raw duplicate partition contains a foreign blind ID"
                        )
                    mapped_values.append(pool_id)
                mapped = tuple(sorted(mapped_values))
                mapped_clusters.append(mapped)
                observed.update(mapped)
            if observed != case_pools:
                raise ValueError(
                    "raw duplicate partition does not cover its exact case pool"
                )
            signatures.append(tuple(sorted(mapped_clusters)))
        decision = partition_decision_by_case.get(case_id)
        if signatures[0] == signatures[1]:
            if decision is not None:
                raise ValueError(
                    "agreed duplicate partitions cannot consume adjudication"
                )
            provenance = DuplicatePartitionProvenance.AGREED_REVIEWERS
            adjudication_ref = None
            clusters = _pooled_clusters_v2(
                case_id=case_id, signature=signatures[0]
            )
            finalized_at = max(
                reviewer_partitions[0].submitted_at,
                reviewer_partitions[1].submitted_at,
                key=_timestamp,
            )
        else:
            if decision is None:
                raise ValueError(
                    "duplicate partition disagreement requires adjudication"
                )
            provenance = DuplicatePartitionProvenance.ADJUDICATED
            adjudication_ref = DuplicatePartitionAdjudicationRefV2(
                adjudication_id=decision.adjudication_id,
                adjudication_sha256=decision.adjudication_sha256,
                adjudicator_id=decision.adjudicator_id,
            )
            clusters = decision.final_clusters
            finalized_at = decision.adjudicated_at
            used_partition_adjudications.add(decision.adjudication_id)
        case_sha = pool_meta[next(iter(case_pools))][1]
        final_partitions.append(
            _build_addressed(
                FinalDuplicatePartitionV2,
                id_field="partition_id",
                sha_field="partition_sha256",
                prefix="final-duplicate-partition-v2",
                values={
                    "execution_release_id": execution.release_id,
                    "execution_release_sha256": execution.release_sha256,
                    "expert_registry_id": registry.registry_id,
                    "expert_registry_sha256": registry.registry_sha256,
                    "case_id": case_id,
                    "case_sha256": case_sha,
                    "review_round": review_round,
                    "annotation_guide_sha256": registry.annotation_guide_sha256,
                    "reviewer_ids": assignment.reviewer_ids,
                    "reviewer_partitions": partition_refs,
                    "provenance": provenance,
                    "adjudication": adjudication_ref,
                    "clusters": clusters,
                    "finalized_at": finalized_at,
                },
            )
        )
    if used_partition_adjudications != {
        item.adjudication_id for item in partition_decisions
    }:
        raise ValueError("partition adjudications contain an unnecessary orphan")

    ordered_judgments = tuple(
        sorted(judgments, key=lambda item: item.pooled_unit_id)
    )
    ordered_partitions = tuple(
        sorted(final_partitions, key=lambda item: item.case_id)
    )
    if any(
        _timestamp(item.submitted_at) > release_time for item in raws
    ) or any(
        _timestamp(item.adjudicated_at) > release_time for item in decisions
    ):
        raise ValueError("Gold release predates a sealed label decision")
    result = _build_addressed(
        FinalGoldReleaseV2,
        id_field="release_id",
        sha_field="release_sha256",
        prefix="final-gold-release-v2",
        values={
            "execution_release_id": execution.release_id,
            "execution_release_sha256": execution.release_sha256,
            "expert_registry_id": registry.registry_id,
            "expert_registry_sha256": registry.registry_sha256,
            "review_round": review_round,
            "annotation_guide_sha256": registry.annotation_guide_sha256,
            "judgments": ordered_judgments,
            "duplicate_partitions": ordered_partitions,
            "unresolvable_unit_ids": tuple(
                item.pooled_unit_id
                for item in ordered_judgments
                if item.unresolvable
            ),
            "denominator_unit_count": sum(
                item.denominator_included for item in ordered_judgments
            ),
            "unresolvable_unit_count": sum(
                item.unresolvable for item in ordered_judgments
            ),
            "released_at": released_at,
        },
    )
    return result


def build_final_gold_release_v2(
    *,
    frozen_case_release: FrozenCaseReleaseV3,
    execution_release: ExecutionReleaseV3,
    pre_run_eligibility_release: PreRunEligibilityReleaseV3,
    reviewer_manifests: tuple[ReviewerManifestV2, ...],
    private_identity_maps: tuple[PrivateIdentityMapV2, ...],
    expert_registry: ExpertStudyRegistryV2,
    evidence_excerpts: tuple[EvidenceExcerptV2, ...],
    blind_key: bytes,
    renderer_sha256: str,
    raw_annotations: tuple[RawExpertAnnotationV1, ...],
    adjudications: tuple[ExpertAdjudicationV1, ...],
    raw_duplicate_partitions: tuple[RawDuplicatePartitionV2, ...],
    released_at: str,
    duplicate_partition_adjudications: tuple[
        DuplicatePartitionAdjudicationV2, ...
    ] = (),
) -> FinalGoldReleaseV2:
    """Derive V2 Gold only from the authoritative V3 formal chain.

    Final labels, provenance, metric counts, pooled cluster identities and
    denominator counts are outputs only.  The caller supplies only the sealed
    raw decisions and adjudications required for genuine disagreements.
    """

    result = _derive_final_gold_release_v2(
        frozen_case_release=frozen_case_release,
        execution_release=execution_release,
        pre_run_eligibility_release=pre_run_eligibility_release,
        reviewer_manifests=reviewer_manifests,
        private_identity_maps=private_identity_maps,
        expert_registry=expert_registry,
        evidence_excerpts=evidence_excerpts,
        blind_key=blind_key,
        renderer_sha256=renderer_sha256,
        raw_annotations=raw_annotations,
        adjudications=adjudications,
        raw_duplicate_partitions=raw_duplicate_partitions,
        duplicate_partition_adjudications=duplicate_partition_adjudications,
        released_at=released_at,
    )
    assert_final_gold_closure_v2(
        result,
        frozen_case_release=frozen_case_release,
        execution_release=execution_release,
        pre_run_eligibility_release=pre_run_eligibility_release,
        reviewer_manifests=reviewer_manifests,
        private_identity_maps=private_identity_maps,
        expert_registry=expert_registry,
        evidence_excerpts=evidence_excerpts,
        blind_key=blind_key,
        renderer_sha256=renderer_sha256,
        raw_annotations=raw_annotations,
        adjudications=adjudications,
        raw_duplicate_partitions=raw_duplicate_partitions,
        duplicate_partition_adjudications=duplicate_partition_adjudications,
    )
    return result


def _assert_final_gold_closure_common(
    release: FinalGoldReleaseV2,
    *,
    frozen_case_release: object | None,
    execution_release: object,
    pre_run_eligibility_release: object,
    reviewer_manifests: tuple[object, ...],
    private_identity_maps: tuple[object, ...],
    expert_registry: object,
    evidence_excerpts: tuple[object, ...],
    blind_key: bytes,
    renderer_sha256: str,
    raw_annotations: tuple[RawExpertAnnotationV1, ...],
    adjudications: tuple[ExpertAdjudicationV1, ...],
    raw_duplicate_partitions: tuple[RawDuplicatePartitionV2, ...],
    duplicate_partition_adjudications: tuple[
        DuplicatePartitionAdjudicationV2, ...
    ] = (),
    formal_v2: bool,
) -> None:
    """Replay the complete execution-to-Gold chain and reject every orphan.

    This verifier intentionally starts with the reviewer-release exact verifier.
    ``RawExpertAnnotationV1`` has no reviewer-packet SHA field, so its packet
    binding is established by the exact sealed manifest/private-map replay,
    reviewer-specific blind ID, authoritative packet identity and timestamps.
    Adjudications use the private ``pooled_unit_id`` as their unit identity.
    """

    release = FinalGoldReleaseV2.model_validate(
        release.model_dump(mode="python", round_trip=True)
    )
    if formal_v2:
        if frozen_case_release is None:
            frozen = None
            execution_input = ExecutionReleaseV2.model_validate(
                execution_release
            )
            execution = ExecutionReleaseV2.model_validate(
                execution_input.model_dump(mode="python", round_trip=True)
            )
            eligibility_input = PreRunEligibilityReleaseV2.model_validate(
                pre_run_eligibility_release
            )
            eligibility = PreRunEligibilityReleaseV2.model_validate(
                eligibility_input.model_dump(mode="python", round_trip=True)
            )
        else:
            execution_input = ExecutionReleaseV3.model_validate(
                execution_release
            )
            execution = ExecutionReleaseV3.model_validate(
                execution_input.model_dump(mode="python", round_trip=True)
            )
            frozen = execution.frozen_case_release
            eligibility = execution.pre_run_eligibility_release
            if frozen_case_release != frozen:
                raise ValueError("Gold verifier receives a foreign frozen V3")
            if pre_run_eligibility_release != eligibility:
                raise ValueError(
                    "Gold verifier receives a foreign eligibility V3"
                )
        registry_input = ExpertStudyRegistryV2.model_validate(expert_registry)
        registry = ExpertStudyRegistryV2.model_validate(
            registry_input.model_dump(mode="python", round_trip=True)
        )
        manifests = tuple(
            ReviewerManifestV2.model_validate(
                ReviewerManifestV2.model_validate(item).model_dump(
                    mode="python", round_trip=True
                )
            )
            for item in reviewer_manifests
        )
        identity_maps = tuple(
            PrivateIdentityMapV2.model_validate(
                PrivateIdentityMapV2.model_validate(item).model_dump(
                    mode="python", round_trip=True
                )
            )
            for item in private_identity_maps
        )
        excerpts = tuple(
            EvidenceExcerptV2.model_validate(
                EvidenceExcerptV2.model_validate(item).model_dump(
                    mode="python", round_trip=True
                )
            )
            for item in evidence_excerpts
        )
    else:
        execution = ExecutionReleaseV1.model_validate(execution_release)
        eligibility = PreRunEligibilityReleaseV1.model_validate(
            pre_run_eligibility_release
        )
        registry = ExpertStudyRegistryV1.model_validate(expert_registry)
        manifests = tuple(
            ReviewerManifestV1.model_validate(item) for item in reviewer_manifests
        )
        identity_maps = tuple(
            PrivateIdentityMapV1.model_validate(item) for item in private_identity_maps
        )
        excerpts = tuple(
            EvidenceExcerptV1.model_validate(item) for item in evidence_excerpts
        )
    raws = tuple(
        RawExpertAnnotationV1.model_validate(item.model_dump(mode="python", round_trip=True))
        for item in raw_annotations
    )
    decisions = tuple(
        ExpertAdjudicationV1.model_validate(item.model_dump(mode="python", round_trip=True))
        for item in adjudications
    )
    raw_partitions = tuple(
        RawDuplicatePartitionV2.model_validate(item.model_dump(mode="python", round_trip=True))
        for item in raw_duplicate_partitions
    )
    partition_decisions = tuple(
        DuplicatePartitionAdjudicationV2.model_validate(
            item.model_dump(mode="python", round_trip=True)
        )
        for item in duplicate_partition_adjudications
    )

    # First invariant: independently replay the safe projection and its exact
    # coverage before trusting a Gold-provided reference.
    if formal_v2:
        reviewer_inputs = {
            "execution_release": execution,
            "pre_run_eligibility_release": eligibility,
            "expert_registry": registry,
            "reviewer_manifests": manifests,
            "private_identity_maps": identity_maps,
            "evidence_excerpts": excerpts,
            "blind_key": blind_key,
            "renderer_sha256": renderer_sha256,
        }
        if frozen is None:
            assert_reviewer_release_legacy_v2_upstream_exact_coverage(
                **reviewer_inputs
            )
        else:
            assert_reviewer_release_exact_coverage_v2(
                frozen_case_release=frozen,
                **reviewer_inputs,
            )
    else:
        assert_reviewer_release_exact_coverage(
            execution_release=execution,
            pre_run_eligibility_release=eligibility,
            expert_registry=registry,
            reviewer_manifests=manifests,
            private_identity_maps=identity_maps,
            evidence_excerpts=excerpts,
            blind_key=blind_key,
            renderer_sha256=renderer_sha256,
        )

    if (
        release.execution_release_id,
        release.execution_release_sha256,
        release.expert_registry_id,
        release.expert_registry_sha256,
        release.annotation_guide_sha256,
    ) != (
        execution.release_id,
        execution.release_sha256,
        registry.registry_id,
        registry.registry_sha256,
        registry.annotation_guide_sha256,
    ):
        raise ValueError("Gold release drifts from execution or expert registry")

    manifest_by_id = {item.manifest_id: item for item in manifests}
    if len(manifest_by_id) != len(manifests):
        raise ValueError("duplicate reviewer manifest identity")
    map_by_reviewer = {item.expert_id: item for item in identity_maps}
    if len(map_by_reviewer) != len(identity_maps):
        raise ValueError("duplicate private identity map reviewer")
    assignment_by_case = {item.case_id: item for item in registry.assignments}
    packet_by_id = {item.packet_id: item for item in execution.hypothesis_packets}
    expected_packet_keys = {
        (position.packet_id, position.packet_sha256)
        for projection in execution.top5_projections
        for position in projection.positions
        if position.packet_id is not None
    }

    pool_by_packet: dict[tuple[str, str], str] = {}
    packet_by_pool: dict[str, tuple[str, str]] = {}
    pool_meta: dict[str, tuple[str, str, str, str]] = {}
    artifact_by_pool_reviewer: dict[tuple[str, str], ReviewerArtifactRefV2] = {}
    blind_to_pool_by_reviewer: dict[tuple[str, str], str] = {}
    for identity_map in identity_maps:
        manifest = manifest_by_id[identity_map.reviewer_manifest_id]
        public_by_id = {item.reviewer_packet_id: item for item in manifest.packets}
        for entry in identity_map.entries:
            packet_key = (entry.packet_id, entry.packet_sha256)
            prior_pool = pool_by_packet.setdefault(packet_key, entry.pooled_unit_id)
            if prior_pool != entry.pooled_unit_id:
                raise ValueError("one packet aliases multiple pooled units")
            prior_packet = packet_by_pool.setdefault(entry.pooled_unit_id, packet_key)
            if prior_packet != packet_key:
                raise ValueError("one pooled unit aliases multiple packets")
            meta = (entry.case_id, entry.case_sha256, entry.packet_id, entry.packet_sha256)
            if pool_meta.setdefault(entry.pooled_unit_id, meta) != meta:
                raise ValueError("pooled unit changes authoritative case or packet")
            public_packet = public_by_id[entry.reviewer_packet_id]
            ref = ReviewerArtifactRefV2(
                reviewer_id=identity_map.expert_id,
                reviewer_manifest_id=manifest.manifest_id,
                reviewer_manifest_sha256=manifest.manifest_sha256,
                private_identity_map_id=identity_map.identity_map_id,
                private_identity_map_sha256=identity_map.identity_map_sha256,
                blinded_reviewer_id=identity_map.blinded_reviewer_id,
                blinded_unit_id=entry.blinded_unit_id,
                reviewer_packet_id=public_packet.reviewer_packet_id,
                reviewer_packet_sha256=public_packet.reviewer_packet_sha256,
            )
            key = (entry.pooled_unit_id, identity_map.expert_id)
            if key in artifact_by_pool_reviewer and artifact_by_pool_reviewer[key] != ref:
                raise ValueError("repeated execution positions change reviewer artifacts")
            artifact_by_pool_reviewer[key] = ref
            blind_key_for_reviewer = (identity_map.expert_id, entry.blinded_unit_id)
            prior_blind_pool = blind_to_pool_by_reviewer.setdefault(
                blind_key_for_reviewer, entry.pooled_unit_id
            )
            if prior_blind_pool != entry.pooled_unit_id:
                raise ValueError("one reviewer blind ID aliases multiple pooled units")

    if set(pool_by_packet) != expected_packet_keys:
        raise ValueError("pooled units do not exactly cover execution present positions")
    expected_pools = set(packet_by_pool)
    expected_artifact_keys = {
        (pool_id, reviewer_id)
        for pool_id, meta in pool_meta.items()
        for reviewer_id in assignment_by_case[meta[0]].reviewer_ids
    }
    if set(artifact_by_pool_reviewer) != expected_artifact_keys:
        raise ValueError("every pooled unit requires both assigned reviewer artifacts")

    judgment_by_pool = {item.pooled_unit_id: item for item in release.judgments}
    if len(judgment_by_pool) != len(release.judgments) or set(judgment_by_pool) != expected_pools:
        raise ValueError("Gold judgments must exactly cover unique present pooled units")
    raw_by_id = _index_unique(raws, "annotation_id", "raw annotation")
    adjudication_by_id = _index_unique(decisions, "adjudication_id", "adjudication")
    used_raw: set[str] = set()
    used_adjudication: set[str] = set()
    latest_raw_by_case_reviewer: dict[tuple[str, str], datetime] = {}
    release_time = _timestamp(release.released_at)

    for pool_id, judgment in judgment_by_pool.items():
        case_id, case_sha, packet_id, packet_sha = pool_meta[pool_id]
        assignment = assignment_by_case[case_id]
        expected_artifacts = tuple(
            artifact_by_pool_reviewer[(pool_id, reviewer_id)]
            for reviewer_id in assignment.reviewer_ids
        )
        if (
            judgment.execution_release_id,
            judgment.execution_release_sha256,
            judgment.expert_registry_id,
            judgment.expert_registry_sha256,
            judgment.case_id,
            judgment.case_sha256,
            judgment.packet_id,
            judgment.packet_sha256,
            judgment.review_round,
            judgment.annotation_guide_sha256,
            judgment.reviewer_artifacts,
        ) != (
            execution.release_id,
            execution.release_sha256,
            registry.registry_id,
            registry.registry_sha256,
            case_id,
            case_sha,
            packet_id,
            packet_sha,
            release.review_round,
            release.annotation_guide_sha256,
            expected_artifacts,
        ):
            raise ValueError("final judgment drifts from pooled reviewer artifacts")
        true_raws: list[RawExpertAnnotationV1] = []
        for ref, artifact in zip(judgment.raw_annotations, expected_artifacts, strict=True):
            raw = raw_by_id.get(ref.annotation_id)
            if raw is None or (
                raw.annotation_sha256, raw.reviewer_id
            ) != (ref.annotation_sha256, ref.reviewer_id):
                raise ValueError("final judgment contains a foreign raw annotation ref")
            if (
                raw.reviewer_id,
                raw.blinded_unit_id,
                raw.case_id,
                raw.case_sha256,
                raw.packet_id,
                raw.packet_sha256,
                raw.review_round,
                raw.annotation_guide_sha256,
            ) != (
                artifact.reviewer_id,
                artifact.blinded_unit_id,
                case_id,
                case_sha,
                packet_id,
                packet_sha,
                release.review_round,
                release.annotation_guide_sha256,
            ):
                raise ValueError("raw annotation is not bound to its sealed reviewer packet")
            manifest = manifest_by_id[artifact.reviewer_manifest_id]
            if _timestamp(raw.started_at) < _timestamp(manifest.sealed_at):
                raise ValueError("raw annotation predates its reviewer manifest seal")
            if _timestamp(raw.submitted_at) > release_time:
                raise ValueError("raw annotation postdates Gold release")
            _assert_evidence_coverage(
                packet=packet_by_id[packet_id],
                judgments=raw.evidence_judgments,
                assessability=raw.assessability,
            )
            latest_key = (case_id, raw.reviewer_id)
            latest_raw_by_case_reviewer[latest_key] = max(
                latest_raw_by_case_reviewer.get(latest_key, _timestamp(raw.submitted_at)),
                _timestamp(raw.submitted_at),
            )
            true_raws.append(raw)
            used_raw.add(raw.annotation_id)

        disagreements = _raw_disagreements(*true_raws)
        if not disagreements:
            if judgment.provenance is not GoldProvenance.AGREED_RAW:
                raise ValueError("identical raw labels cannot be adjudicated")
            if _judgment_values(judgment) != _annotation_values(true_raws[0]):
                raise ValueError("agreed final label differs from raw agreement")
        else:
            if judgment.provenance is not GoldProvenance.ADJUDICATED or judgment.adjudication is None:
                raise ValueError("raw disagreement requires adjudication")
            decision = adjudication_by_id.get(judgment.adjudication.adjudication_id)
            if decision is None or (
                decision.adjudication_sha256,
                decision.adjudicator_id,
            ) != (
                judgment.adjudication.adjudication_sha256,
                judgment.adjudication.adjudicator_id,
            ):
                raise ValueError("final judgment contains a foreign adjudication ref")
            if (
                decision.blinded_unit_id,
                decision.case_id,
                decision.case_sha256,
                decision.packet_id,
                decision.packet_sha256,
                decision.raw_annotations,
                decision.adjudicator_id,
                decision.annotation_guide_sha256,
                decision.disagreement_fields,
                decision.status,
            ) != (
                pool_id,
                case_id,
                case_sha,
                packet_id,
                packet_sha,
                judgment.raw_annotations,
                assignment.adjudicator_id,
                release.annotation_guide_sha256,
                disagreements,
                judgment.adjudication_status,
            ):
                raise ValueError("adjudication does not replay the exact pooled dispute")
            if _timestamp(decision.adjudicated_at) < max(
                _timestamp(item.submitted_at) for item in true_raws
            ) or _timestamp(decision.adjudicated_at) > release_time:
                raise ValueError("adjudication timestamp is outside the sealed review interval")
            if _judgment_values(judgment) != _adjudication_values(decision):
                raise ValueError("final label differs from adjudication")
            _assert_evidence_coverage(
                packet=packet_by_id[packet_id],
                judgments=decision.final_evidence_judgments,
                assessability=decision.final_assessability,
            )
            used_adjudication.add(decision.adjudication_id)

    if used_raw != set(raw_by_id):
        raise ValueError("raw annotations are missing from or orphaned by Gold")
    if used_adjudication != set(adjudication_by_id):
        raise ValueError("adjudications are missing from or orphaned by Gold")

    pools_by_case: dict[str, set[str]] = {}
    for pool_id, meta in pool_meta.items():
        pools_by_case.setdefault(meta[0], set()).add(pool_id)
    final_partition_by_case = {item.case_id: item for item in release.duplicate_partitions}
    if len(final_partition_by_case) != len(release.duplicate_partitions) or set(final_partition_by_case) != set(pools_by_case):
        raise ValueError("final duplicate partitions must exactly cover cases with present pools")
    raw_partition_by_id = _index_unique(raw_partitions, "partition_id", "raw duplicate partition")
    partition_decision_by_id = _index_unique(
        partition_decisions, "adjudication_id", "duplicate partition adjudication"
    )
    used_raw_partitions: set[str] = set()
    used_partition_decisions: set[str] = set()
    for case_id, final_partition in final_partition_by_case.items():
        assignment = assignment_by_case[case_id]
        case_sha = next(iter(pool_meta[pool_id][1] for pool_id in pools_by_case[case_id]))
        if (
            final_partition.execution_release_id,
            final_partition.execution_release_sha256,
            final_partition.expert_registry_id,
            final_partition.expert_registry_sha256,
            final_partition.case_sha256,
            final_partition.review_round,
            final_partition.annotation_guide_sha256,
            final_partition.reviewer_ids,
        ) != (
            execution.release_id,
            execution.release_sha256,
            registry.registry_id,
            registry.registry_sha256,
            case_sha,
            release.review_round,
            release.annotation_guide_sha256,
            assignment.reviewer_ids,
        ):
            raise ValueError("final duplicate partition drifts from study identities")
        raw_signatures: list[tuple[tuple[str, ...], ...]] = []
        true_raw_partition_refs: list[DuplicatePartitionRefV2] = []
        latest_partition_submission: datetime | None = None
        for ref in final_partition.reviewer_partitions:
            raw_partition = raw_partition_by_id.get(ref.partition_id)
            if raw_partition is None or (
                raw_partition.partition_sha256, raw_partition.reviewer_id
            ) != (ref.partition_sha256, ref.reviewer_id):
                raise ValueError("final duplicate partition contains a foreign raw ref")
            reviewer_map = map_by_reviewer[ref.reviewer_id]
            manifest = manifest_by_id[reviewer_map.reviewer_manifest_id]
            if (
                raw_partition.reviewer_manifest_id,
                raw_partition.reviewer_manifest_sha256,
                raw_partition.private_identity_map_id,
                raw_partition.private_identity_map_sha256,
                raw_partition.expert_registry_id,
                raw_partition.expert_registry_sha256,
                raw_partition.case_id,
                raw_partition.case_sha256,
                raw_partition.review_round,
                raw_partition.annotation_guide_sha256,
            ) != (
                manifest.manifest_id,
                manifest.manifest_sha256,
                reviewer_map.identity_map_id,
                reviewer_map.identity_map_sha256,
                registry.registry_id,
                registry.registry_sha256,
                case_id,
                case_sha,
                release.review_round,
                release.annotation_guide_sha256,
            ):
                raise ValueError("raw duplicate partition drifts from reviewer artifacts")
            mapped_clusters: list[tuple[str, ...]] = []
            observed_blinds: set[str] = set()
            for cluster in raw_partition.clusters:
                mapped: list[str] = []
                for blind_id in cluster.blinded_unit_ids:
                    pool_id = blind_to_pool_by_reviewer.get((ref.reviewer_id, blind_id))
                    if pool_id is None or pool_id not in pools_by_case[case_id]:
                        raise ValueError("raw duplicate partition contains a foreign blind ID")
                    observed_blinds.add(blind_id)
                    mapped.append(pool_id)
                mapped_clusters.append(tuple(sorted(mapped)))
            expected_blinds = {
                artifact_by_pool_reviewer[(pool_id, ref.reviewer_id)].blinded_unit_id
                for pool_id in pools_by_case[case_id]
            }
            if observed_blinds != expected_blinds:
                raise ValueError("raw duplicate partition does not exactly cover reviewer blinds")
            signature = tuple(sorted(mapped_clusters))
            if len({unit for cluster in signature for unit in cluster}) != len(pools_by_case[case_id]):
                raise ValueError("mapped raw duplicate partition is not a pooled partition")
            raw_signatures.append(signature)
            if _timestamp(raw_partition.submitted_at) < latest_raw_by_case_reviewer[(case_id, ref.reviewer_id)]:
                raise ValueError("duplicate partition predates the reviewer's unit labels")
            if _timestamp(raw_partition.submitted_at) > _timestamp(final_partition.finalized_at):
                raise ValueError("final duplicate partition predates a raw partition")
            used_raw_partitions.add(raw_partition.partition_id)
            true_raw_partition_refs.append(ref)
            submitted = _timestamp(raw_partition.submitted_at)
            latest_partition_submission = (
                submitted
                if latest_partition_submission is None
                else max(latest_partition_submission, submitted)
            )
        final_signature = _pooled_partition_signature(final_partition.clusters)
        if {unit for cluster in final_signature for unit in cluster} != pools_by_case[case_id]:
            raise ValueError("final duplicate partition does not exactly cover pooled units")
        if final_partition.provenance is DuplicatePartitionProvenance.AGREED_REVIEWERS:
            if raw_signatures[0] != raw_signatures[1] or final_signature != raw_signatures[0]:
                raise ValueError("AGREED_REVIEWERS does not replay mapped raw agreement")
        else:
            if raw_signatures[0] == raw_signatures[1]:
                raise ValueError("identical raw partitions cannot be adjudicated")
            if final_partition.adjudication is None:
                raise ValueError("partition disagreement lacks an adjudication ref")
            decision = partition_decision_by_id.get(
                final_partition.adjudication.adjudication_id
            )
            if decision is None or (
                decision.adjudication_sha256,
                decision.adjudicator_id,
            ) != (
                final_partition.adjudication.adjudication_sha256,
                final_partition.adjudication.adjudicator_id,
            ):
                raise ValueError("final partition contains a foreign adjudication ref")
            if (
                decision.execution_release_id,
                decision.execution_release_sha256,
                decision.expert_registry_id,
                decision.expert_registry_sha256,
                decision.case_id,
                decision.case_sha256,
                decision.review_round,
                decision.annotation_guide_sha256,
                decision.pooled_unit_ids,
                decision.reviewer_partitions,
                decision.adjudicator_id,
                decision.final_clusters,
            ) != (
                execution.release_id,
                execution.release_sha256,
                registry.registry_id,
                registry.registry_sha256,
                case_id,
                case_sha,
                release.review_round,
                release.annotation_guide_sha256,
                tuple(sorted(pools_by_case[case_id])),
                tuple(true_raw_partition_refs),
                assignment.adjudicator_id,
                final_partition.clusters,
            ):
                raise ValueError("partition adjudication does not replay the exact dispute")
            decision_time = _timestamp(decision.adjudicated_at)
            if (
                latest_partition_submission is None
                or decision_time < latest_partition_submission
                or decision_time > _timestamp(final_partition.finalized_at)
                or decision_time > release_time
            ):
                raise ValueError("partition adjudication time is outside its sealed interval")
            used_partition_decisions.add(decision.adjudication_id)
        if _timestamp(final_partition.finalized_at) > release_time:
            raise ValueError("final duplicate partition postdates Gold release")
    if used_raw_partitions != set(raw_partition_by_id):
        raise ValueError("raw duplicate partitions are missing from or orphaned by Gold")
    if used_partition_decisions != set(partition_decision_by_id):
        raise ValueError(
            "duplicate partition adjudications are missing from or orphaned by Gold"
        )


def assert_final_gold_closure_v2(
    release: FinalGoldReleaseV2,
    *,
    frozen_case_release: FrozenCaseReleaseV3,
    execution_release: ExecutionReleaseV3,
    pre_run_eligibility_release: PreRunEligibilityReleaseV3,
    reviewer_manifests: tuple[ReviewerManifestV2, ...],
    private_identity_maps: tuple[PrivateIdentityMapV2, ...],
    expert_registry: ExpertStudyRegistryV2,
    evidence_excerpts: tuple[EvidenceExcerptV2, ...],
    blind_key: bytes,
    renderer_sha256: str,
    raw_annotations: tuple[RawExpertAnnotationV1, ...],
    adjudications: tuple[ExpertAdjudicationV1, ...],
    raw_duplicate_partitions: tuple[RawDuplicatePartitionV2, ...],
    duplicate_partition_adjudications: tuple[
        DuplicatePartitionAdjudicationV2, ...
    ] = (),
) -> None:
    """Exact-replay V2 Gold from the authoritative V3 formal chain."""

    value = _revalidate(release, FinalGoldReleaseV2)
    _assert_final_gold_closure_common(
        value,
        frozen_case_release=frozen_case_release,
        execution_release=execution_release,
        pre_run_eligibility_release=pre_run_eligibility_release,
        reviewer_manifests=reviewer_manifests,
        private_identity_maps=private_identity_maps,
        expert_registry=expert_registry,
        evidence_excerpts=evidence_excerpts,
        blind_key=blind_key,
        renderer_sha256=renderer_sha256,
        raw_annotations=raw_annotations,
        adjudications=adjudications,
        raw_duplicate_partitions=raw_duplicate_partitions,
        duplicate_partition_adjudications=duplicate_partition_adjudications,
        formal_v2=True,
    )
    rebuilt = _derive_final_gold_release_v2(
        frozen_case_release=frozen_case_release,
        execution_release=execution_release,
        pre_run_eligibility_release=pre_run_eligibility_release,
        reviewer_manifests=reviewer_manifests,
        private_identity_maps=private_identity_maps,
        expert_registry=expert_registry,
        evidence_excerpts=evidence_excerpts,
        blind_key=blind_key,
        renderer_sha256=renderer_sha256,
        raw_annotations=raw_annotations,
        adjudications=adjudications,
        raw_duplicate_partitions=raw_duplicate_partitions,
        duplicate_partition_adjudications=(
            duplicate_partition_adjudications
        ),
        released_at=value.released_at,
        reviewer_prevalidated=True,
    )
    if rebuilt != value:
        raise ValueError("formal Gold release fails exact canonical replay")


def build_final_gold_release_legacy_v2_upstream(
    *,
    execution_release: ExecutionReleaseV2,
    pre_run_eligibility_release: PreRunEligibilityReleaseV2,
    reviewer_manifests: tuple[ReviewerManifestV2, ...],
    private_identity_maps: tuple[PrivateIdentityMapV2, ...],
    expert_registry: ExpertStudyRegistryV2,
    evidence_excerpts: tuple[EvidenceExcerptV2, ...],
    blind_key: bytes,
    renderer_sha256: str,
    raw_annotations: tuple[RawExpertAnnotationV1, ...],
    adjudications: tuple[ExpertAdjudicationV1, ...],
    raw_duplicate_partitions: tuple[RawDuplicatePartitionV2, ...],
    released_at: str,
    duplicate_partition_adjudications: tuple[
        DuplicatePartitionAdjudicationV2, ...
    ] = (),
) -> FinalGoldReleaseV2:
    """Legacy/non-formal V2-upstream fixture builder; never a Pilot root."""

    result = _derive_final_gold_release_v2(
        frozen_case_release=None,
        execution_release=execution_release,
        pre_run_eligibility_release=pre_run_eligibility_release,
        reviewer_manifests=reviewer_manifests,
        private_identity_maps=private_identity_maps,
        expert_registry=expert_registry,
        evidence_excerpts=evidence_excerpts,
        blind_key=blind_key,
        renderer_sha256=renderer_sha256,
        raw_annotations=raw_annotations,
        adjudications=adjudications,
        raw_duplicate_partitions=raw_duplicate_partitions,
        duplicate_partition_adjudications=duplicate_partition_adjudications,
        released_at=released_at,
    )
    assert_final_gold_closure_legacy_v2_upstream(
        result,
        execution_release=execution_release,
        pre_run_eligibility_release=pre_run_eligibility_release,
        reviewer_manifests=reviewer_manifests,
        private_identity_maps=private_identity_maps,
        expert_registry=expert_registry,
        evidence_excerpts=evidence_excerpts,
        blind_key=blind_key,
        renderer_sha256=renderer_sha256,
        raw_annotations=raw_annotations,
        adjudications=adjudications,
        raw_duplicate_partitions=raw_duplicate_partitions,
        duplicate_partition_adjudications=duplicate_partition_adjudications,
    )
    return result


def assert_final_gold_closure_legacy_v2_upstream(
    release: FinalGoldReleaseV2,
    *,
    execution_release: ExecutionReleaseV2,
    pre_run_eligibility_release: PreRunEligibilityReleaseV2,
    reviewer_manifests: tuple[ReviewerManifestV2, ...],
    private_identity_maps: tuple[PrivateIdentityMapV2, ...],
    expert_registry: ExpertStudyRegistryV2,
    evidence_excerpts: tuple[EvidenceExcerptV2, ...],
    blind_key: bytes,
    renderer_sha256: str,
    raw_annotations: tuple[RawExpertAnnotationV1, ...],
    adjudications: tuple[ExpertAdjudicationV1, ...],
    raw_duplicate_partitions: tuple[RawDuplicatePartitionV2, ...],
    duplicate_partition_adjudications: tuple[
        DuplicatePartitionAdjudicationV2, ...
    ] = (),
) -> None:
    """Exact replay for historical V2-upstream fixtures only."""

    value = _revalidate(release, FinalGoldReleaseV2)
    _assert_final_gold_closure_common(
        value,
        frozen_case_release=None,
        execution_release=execution_release,
        pre_run_eligibility_release=pre_run_eligibility_release,
        reviewer_manifests=reviewer_manifests,
        private_identity_maps=private_identity_maps,
        expert_registry=expert_registry,
        evidence_excerpts=evidence_excerpts,
        blind_key=blind_key,
        renderer_sha256=renderer_sha256,
        raw_annotations=raw_annotations,
        adjudications=adjudications,
        raw_duplicate_partitions=raw_duplicate_partitions,
        duplicate_partition_adjudications=duplicate_partition_adjudications,
        formal_v2=True,
    )
    rebuilt = _derive_final_gold_release_v2(
        frozen_case_release=None,
        execution_release=execution_release,
        pre_run_eligibility_release=pre_run_eligibility_release,
        reviewer_manifests=reviewer_manifests,
        private_identity_maps=private_identity_maps,
        expert_registry=expert_registry,
        evidence_excerpts=evidence_excerpts,
        blind_key=blind_key,
        renderer_sha256=renderer_sha256,
        raw_annotations=raw_annotations,
        adjudications=adjudications,
        raw_duplicate_partitions=raw_duplicate_partitions,
        duplicate_partition_adjudications=(
            duplicate_partition_adjudications
        ),
        released_at=value.released_at,
        reviewer_prevalidated=True,
    )
    if rebuilt != value:
        raise ValueError("legacy V2-upstream Gold fails exact canonical replay")


def assert_final_gold_closure_legacy_v1(
    release: FinalGoldReleaseV2,
    *,
    execution_release: ExecutionReleaseV1,
    pre_run_eligibility_release: PreRunEligibilityReleaseV1,
    reviewer_manifests: tuple[ReviewerManifestV1, ...],
    private_identity_maps: tuple[PrivateIdentityMapV1, ...],
    expert_registry: ExpertStudyRegistryV1,
    evidence_excerpts: tuple[EvidenceExcerptV1, ...],
    blind_key: bytes,
    renderer_sha256: str,
    raw_annotations: tuple[RawExpertAnnotationV1, ...],
    adjudications: tuple[ExpertAdjudicationV1, ...],
    raw_duplicate_partitions: tuple[RawDuplicatePartitionV2, ...],
    duplicate_partition_adjudications: tuple[
        DuplicatePartitionAdjudicationV2, ...
    ] = (),
) -> None:
    """Deprecated compatibility verifier for historical V1 reviewer fixtures."""

    _assert_final_gold_closure_common(
        release,
        frozen_case_release=None,
        execution_release=execution_release,
        pre_run_eligibility_release=pre_run_eligibility_release,
        reviewer_manifests=reviewer_manifests,
        private_identity_maps=private_identity_maps,
        expert_registry=expert_registry,
        evidence_excerpts=evidence_excerpts,
        blind_key=blind_key,
        renderer_sha256=renderer_sha256,
        raw_annotations=raw_annotations,
        adjudications=adjudications,
        raw_duplicate_partitions=raw_duplicate_partitions,
        duplicate_partition_adjudications=duplicate_partition_adjudications,
        formal_v2=False,
    )


__all__ = [
    "AdjudicationRefV1",
    "DuplicatePartitionProvenance",
    "DuplicatePartitionRefV1",
    "ExpertDuplicateClusterV1",
    "ExpertDuplicatePartitionV1",
    "FinalExpertJudgmentV1",
    "FinalGoldReleaseV1",
    "DuplicatePartitionRefV2",
    "DuplicatePartitionAdjudicationRefV2",
    "DuplicatePartitionAdjudicationV2",
    "FinalDuplicatePartitionV2",
    "FinalExpertJudgmentV2",
    "FinalGoldReleaseV2",
    "GoldProvenance",
    "PooledDuplicateClusterV2",
    "RawDuplicatePartitionV2",
    "RawExpertDuplicatePartitionV1",
    "ReviewerArtifactRefV2",
    "ReviewerDuplicateClusterV2",
    "assert_final_gold_closure",
    "assert_final_gold_closure_legacy_v1",
    "assert_final_gold_closure_legacy_v2_upstream",
    "assert_final_gold_closure_v2",
    "build_final_gold_release_legacy_v2_upstream",
    "build_final_gold_release_v2",
]
