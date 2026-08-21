"""Phase-generic expert Gold closure for Main flat-band experiments.

This module derives analysis-facing position labels from sealed arm traces and
human expert inputs.  It performs no semantic inference: actual packet labels
come only from two independent raw expert labels (plus a distinct adjudicator
when they disagree), while absent ranking positions are generated locally as
``SYSTEM_PACKET_INVALID`` with zero gain.  Callers never provide a final Gold
surface.

The initial public types in this module are intentionally small.  Review-unit
content has no system, arm-role, or ranking-position field; those identities
remain in the upstream arm trace and are exposed only by the derived analysis
projection.
"""

from __future__ import annotations

import hashlib
import hmac
from collections.abc import Mapping
from datetime import datetime
from enum import StrEnum
from typing import TYPE_CHECKING, Annotated, Literal, TypeVar

from pydantic import Field, field_validator, model_validator

from material_agent.inspiration.models import (
    Identifier,
    Sha256,
    StrictModel,
    canonical_sha256,
    deterministic_id,
)
from material_agent.research.flatband_analysis_v2 import AnalysisTraceRoleV2
from material_agent.research.flatband_arm_runtime import (
    ArmExecutionScope,
    ArmExecutionTraceV1,
)
from material_agent.research.flatband_contracts import HypothesisPacketV1
from material_agent.research.flatband_execution import (
    ExecutionPhase,
    ResearchSystemId,
    RunCellStatus,
)
from material_agent.research.flatband_main_execution import (
    MainPhaseExecutionCellEvidenceV1,
    MainPhaseExecutionReleaseV1,
    main_gold_execution_cells_from_main_v1,
)

if TYPE_CHECKING:
    from material_agent.research.flatband_analysis_v2 import (
        AnalysisCellEvidenceV2,
        FinalPositionJudgmentV2,
    )


ModelT = TypeVar("ModelT", bound=StrictModel)
_MAIN_PHASES = frozenset(
    {
        ExecutionPhase.DEVELOPMENT_ABLATIONS,
        ExecutionPhase.DEVELOPMENT_FUSION,
        ExecutionPhase.LOCKED_PRIMARY,
    }
)


class MainGoldStatus(StrEnum):
    ASSESSABLE = "ASSESSABLE"
    SYSTEM_PACKET_INVALID = "SYSTEM_PACKET_INVALID"
    UNRESOLVABLE = "UNRESOLVABLE"


class MainGoldProvenance(StrEnum):
    AGREED_RAW = "AGREED_RAW"
    ADJUDICATED = "ADJUDICATED"
    SYSTEM_DERIVED_ABSENCE = "SYSTEM_DERIVED_ABSENCE"


class MainDuplicateProvenance(StrEnum):
    AGREED_RAW = "AGREED_RAW"
    ADJUDICATED = "ADJUDICATED"


def _timestamp(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise ValueError("timestamp must be RFC3339-compatible") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("timestamp must include a UTC offset")
    return parsed


def _revalidate(value: object, model_type: type[ModelT]) -> ModelT:
    parsed = model_type.model_validate(value)
    return model_type.model_validate(
        parsed.model_dump(mode="python", round_trip=True)
    )


def _identity_values(
    model: StrictModel, *, id_field: str, sha_field: str, prefix: str
) -> tuple[str, str]:
    semantic = model.model_dump(mode="python", exclude={id_field, sha_field})
    digest = canonical_sha256(semantic)
    return deterministic_id(prefix, {sha_field: digest}), digest


def _assert_identity(
    model: StrictModel, *, id_field: str, sha_field: str, prefix: str
) -> None:
    identifier, digest = _identity_values(
        model, id_field=id_field, sha_field=sha_field, prefix=prefix
    )
    if getattr(model, sha_field) != digest:
        raise ValueError(f"{sha_field} does not match semantic content")
    if getattr(model, id_field) != identifier:
        raise ValueError(f"{id_field} does not match {sha_field}")


def _build_identified(
    model_type: type[ModelT],
    *,
    id_field: str,
    sha_field: str,
    prefix: str,
    values: dict[str, object],
) -> ModelT:
    draft = model_type.model_construct(**values)
    identifier, digest = _identity_values(
        draft, id_field=id_field, sha_field=sha_field, prefix=prefix
    )
    return model_type.model_validate(
        {**values, id_field: identifier, sha_field: digest}
    )


def _validate_label_surface(
    status: MainGoldStatus,
    relevance_grade: int,
    evidence_gain: int,
) -> None:
    if status is MainGoldStatus.ASSESSABLE:
        if relevance_grade >= 2 and evidence_gain != 1:
            raise ValueError("grade two or three requires evidence-valid Gold")
        return
    if relevance_grade != 0 or evidence_gain != 0:
        raise ValueError("invalid or unresolvable labels must have zero gains")


def _require_blinding_key(value: bytes) -> bytes:
    if not isinstance(value, bytes) or len(value) < 32:
        raise ValueError("ephemeral blinding key must contain at least 32 bytes")
    return value


def _blinded_id(prefix: str, key: bytes, payload: object) -> str:
    preimage = canonical_sha256(payload).encode("ascii")
    digest = hmac.new(key, preimage, hashlib.sha256).hexdigest()
    return deterministic_id(prefix, {"hmac_sha256": digest})


def _require_annotation_key(value: bytes) -> bytes:
    if not isinstance(value, bytes) or len(value) < 32:
        raise ValueError("expert annotation key must contain at least 32 bytes")
    return value


def _annotation_signature_payload(
    model: StrictModel,
    *,
    id_field: str,
    sha_field: str,
    signature_field: str,
    signature_schema_version: str,
) -> dict[str, object]:
    """Return the complete non-address/non-signature semantic preimage."""

    return {
        "signature_schema_version": signature_schema_version,
        "semantic_payload": model.model_dump(
            mode="python",
            exclude={id_field, sha_field, signature_field},
        ),
    }


def _annotation_signature(
    key: bytes,
    model: StrictModel,
    *,
    id_field: str,
    sha_field: str,
    signature_field: str,
    signature_schema_version: str,
) -> str:
    payload = _annotation_signature_payload(
        model,
        id_field=id_field,
        sha_field=sha_field,
        signature_field=signature_field,
        signature_schema_version=signature_schema_version,
    )
    return hmac.new(
        _require_annotation_key(key),
        canonical_sha256(payload).encode("ascii"),
        hashlib.sha256,
    ).hexdigest()


def _build_signed_identified(
    model_type: type[ModelT],
    *,
    id_field: str,
    sha_field: str,
    prefix: str,
    signature_field: str,
    signature_schema_version: str,
    annotation_key: bytes,
    values: dict[str, object],
) -> ModelT:
    draft = model_type.model_construct(
        **values,
        **{signature_field: "0" * 64},
    )
    signature = _annotation_signature(
        annotation_key,
        draft,
        id_field=id_field,
        sha_field=sha_field,
        signature_field=signature_field,
        signature_schema_version=signature_schema_version,
    )
    return _build_identified(
        model_type,
        id_field=id_field,
        sha_field=sha_field,
        prefix=prefix,
        values={**values, signature_field: signature},
    )


def _assert_annotation_signature(
    model: StrictModel,
    *,
    annotation_key: bytes,
    key_commitment_sha256: str,
    id_field: str,
    sha_field: str,
    signature_field: str,
    signature_schema_version: str,
    signer_label: str,
) -> None:
    key = _require_annotation_key(annotation_key)
    if hashlib.sha256(key).hexdigest() != key_commitment_sha256:
        raise ValueError(
            f"{signer_label} annotation key differs from frozen commitment"
        )
    expected = _annotation_signature(
        key,
        model,
        id_field=id_field,
        sha_field=sha_field,
        signature_field=signature_field,
        signature_schema_version=signature_schema_version,
    )
    if not hmac.compare_digest(str(getattr(model, signature_field)), expected):
        raise ValueError(f"{signer_label} annotation signature does not verify")


def _assert_manifest_field_safety(value: object) -> None:
    forbidden = frozenset(
        {
            "system_id",
            "system_config_id",
            "system_config_sha256",
            "selection_rank",
            "run_id",
            "cell_id",
            "role",
        }
    )
    if isinstance(value, dict):
        for key, nested in value.items():
            if str(key) in forbidden:
                raise ValueError("public reviewer manifest exposes execution identity")
            _assert_manifest_field_safety(nested)
    elif isinstance(value, (list, tuple)):
        for nested in value:
            _assert_manifest_field_safety(nested)


class MainReviewUnitV1(StrictModel):
    """One content-addressed packet in a system/rank-free review namespace."""

    schema_version: Literal["flatband-main-review-unit-v1"] = (
        "flatband-main-review-unit-v1"
    )
    review_unit_id: Identifier
    review_unit_sha256: Sha256
    pooled_unit_id: Identifier
    case_id: Identifier
    case_sha256: Sha256
    packet: HypothesisPacketV1
    chain_of_thought_stored: Literal[False] = False
    scientific_conclusion: Literal[False] = False

    @model_validator(mode="after")
    def validate_unit(self) -> MainReviewUnitV1:
        packet = _revalidate(self.packet, HypothesisPacketV1)
        if (self.case_id, self.case_sha256) != (
            packet.case_id,
            packet.case_sha256,
        ):
            raise ValueError("review unit packet belongs to another case")
        expected_pool = deterministic_id(
            "main-pooled-unit-v1",
            {
                "case_id": packet.case_id,
                "case_sha256": packet.case_sha256,
                "packet_id": packet.packet_id,
                "packet_sha256": packet.packet_sha256,
            },
        )
        if self.pooled_unit_id != expected_pool:
            raise ValueError("pooled unit identity does not derive from packet")
        _assert_identity(
            self,
            id_field="review_unit_id",
            sha_field="review_unit_sha256",
            prefix="main-review-unit-v1",
        )
        return self


class MainExpertIdentityCommitmentV1(StrictModel):
    expert_id: Identifier
    natural_person_commitment_sha256: Sha256
    annotation_key_commitment_sha256: Sha256


class MainExpertRegistryRefV1(StrictModel):
    registry_id: Identifier
    registry_sha256: Sha256


class MainExpertAssignmentV1(StrictModel):
    """Frozen Main assignment bound to an upstream expert-registry artifact."""

    schema_version: Literal["flatband-main-expert-assignment-v1"] = (
        "flatband-main-expert-assignment-v1"
    )
    assignment_id: Identifier
    assignment_sha256: Sha256
    phase: ExecutionPhase
    expert_registry: MainExpertRegistryRefV1
    reviewers: Annotated[
        tuple[MainExpertIdentityCommitmentV1, ...], Field(min_length=2, max_length=2)
    ]
    adjudicators: Annotated[
        tuple[MainExpertIdentityCommitmentV1, ...], Field(min_length=1, max_length=12)
    ]
    sealed_at: Annotated[str, Field(min_length=20, max_length=40)]
    assignments_frozen_before_review: Literal[True] = True
    scientific_conclusion: Literal[False] = False

    @field_validator("sealed_at")
    @classmethod
    def validate_time(cls, value: str) -> str:
        _timestamp(value)
        return value

    @model_validator(mode="after")
    def validate_assignment(self) -> MainExpertAssignmentV1:
        if self.phase not in _MAIN_PHASES:
            raise ValueError("expert assignment uses a non-Main phase")
        for values, label in (
            (self.reviewers, "reviewers"),
            (self.adjudicators, "adjudicators"),
        ):
            ids = tuple(item.expert_id for item in values)
            if ids != tuple(sorted(set(ids))):
                raise ValueError(f"{label} must be expert-ID sorted and unique")
            commitments = tuple(
                item.natural_person_commitment_sha256 for item in values
            )
            if len(commitments) != len(set(commitments)):
                raise ValueError(f"{label} natural-person commitments must be injective")
            annotation_commitments = tuple(
                item.annotation_key_commitment_sha256 for item in values
            )
            if len(annotation_commitments) != len(set(annotation_commitments)):
                raise ValueError(f"{label} annotation-key commitments must be injective")
        all_people = tuple(
            item.natural_person_commitment_sha256
            for item in (*self.reviewers, *self.adjudicators)
        )
        if len(all_people) != len(set(all_people)):
            raise ValueError("reviewer and adjudicator natural persons must be distinct")
        all_annotation_keys = tuple(
            item.annotation_key_commitment_sha256
            for item in (*self.reviewers, *self.adjudicators)
        )
        if len(all_annotation_keys) != len(set(all_annotation_keys)):
            raise ValueError(
                "reviewer and adjudicator annotation-key commitments must be distinct"
            )
        _assert_identity(
            self,
            id_field="assignment_id",
            sha_field="assignment_sha256",
            prefix="main-expert-assignment-v1",
        )
        return self


class MainReviewerPacketV1(StrictModel):
    """Reviewer-visible packet with only blind ordering/identity metadata."""

    display_order: Annotated[int, Field(ge=1, le=100_000)]
    blinded_unit_id: Identifier
    packet: HypothesisPacketV1

    @model_validator(mode="after")
    def validate_packet(self) -> MainReviewerPacketV1:
        _revalidate(self.packet, HypothesisPacketV1)
        _assert_manifest_field_safety(self.model_dump(mode="python"))
        return self


class MainReviewerManifestV1(StrictModel):
    """Public reviewer surface; execution identity has no schema slot."""

    schema_version: Literal["flatband-main-reviewer-manifest-v1"] = (
        "flatband-main-reviewer-manifest-v1"
    )
    manifest_id: Identifier
    manifest_sha256: Sha256
    blinded_reviewer_id: Identifier
    renderer_sha256: Sha256
    packets: Annotated[
        tuple[MainReviewerPacketV1, ...], Field(max_length=100_000)
    ] = ()
    sealed_at: Annotated[str, Field(min_length=20, max_length=40)]
    private_identity_included: Literal[False] = False
    public_repository_release_allowed: Literal[False] = False
    chain_of_thought_stored: Literal[False] = False
    scientific_conclusion: Literal[False] = False

    @field_validator("sealed_at")
    @classmethod
    def validate_time(cls, value: str) -> str:
        _timestamp(value)
        return value

    @model_validator(mode="after")
    def validate_manifest(self) -> MainReviewerManifestV1:
        if tuple(item.display_order for item in self.packets) != tuple(
            range(1, len(self.packets) + 1)
        ):
            raise ValueError("reviewer packet display order must be contiguous")
        blind_ids = tuple(item.blinded_unit_id for item in self.packets)
        if len(blind_ids) != len(set(blind_ids)):
            raise ValueError("reviewer manifest repeats a blinded unit")
        _assert_manifest_field_safety(
            self.model_dump(
                mode="python", exclude={"manifest_id", "manifest_sha256"}
            )
        )
        _assert_identity(
            self,
            id_field="manifest_id",
            sha_field="manifest_sha256",
            prefix="main-reviewer-manifest-v1",
        )
        return self


class MainPositionContributionV1(StrictModel):
    trace_id: Identifier
    trace_sha256: Sha256
    ranking_id: Identifier
    ranking_sha256: Sha256
    system_id: ResearchSystemId
    cell_id: Identifier
    run_id: Identifier
    selection_rank: Annotated[int, Field(ge=1, le=5)]


class MainPrivateUnitMapEntryV1(StrictModel):
    blinded_unit_id: Identifier
    review_unit_id: Identifier
    review_unit_sha256: Sha256
    pooled_unit_id: Identifier
    case_id: Identifier
    case_sha256: Sha256
    packet_id: Identifier
    packet_sha256: Sha256
    contributions: Annotated[
        tuple[MainPositionContributionV1, ...], Field(min_length=1, max_length=64)
    ]

    @model_validator(mode="after")
    def validate_entry(self) -> MainPrivateUnitMapEntryV1:
        keys = tuple(
            (
                item.system_id.value,
                item.cell_id,
                item.selection_rank,
                item.trace_id,
            )
            for item in self.contributions
        )
        if keys != tuple(sorted(set(keys))):
            raise ValueError("private contributions must be sorted and unique")
        return self


class MainPrivateIdentityMapV1(StrictModel):
    """Private join from reviewer blind IDs to actual arm positions."""

    schema_version: Literal["flatband-main-private-identity-map-v1"] = (
        "flatband-main-private-identity-map-v1"
    )
    identity_map_id: Identifier
    identity_map_sha256: Sha256
    phase: ExecutionPhase
    expert_assignment_id: Identifier
    expert_assignment_sha256: Sha256
    reviewer_manifest_id: Identifier
    reviewer_manifest_sha256: Sha256
    reviewer_id: Identifier
    reviewer_person_commitment_sha256: Sha256
    reviewer_annotation_key_commitment_sha256: Sha256
    assignment_role: Literal["REVIEWER"] = "REVIEWER"
    blinded_reviewer_id: Identifier
    blinding_commitment_sha256: Sha256
    entries: Annotated[
        tuple[MainPrivateUnitMapEntryV1, ...], Field(max_length=100_000)
    ] = ()
    sealed_at: Annotated[str, Field(min_length=20, max_length=40)]
    private_storage_required: Literal[True] = True
    key_material_included: Literal[False] = False
    chain_of_thought_stored: Literal[False] = False
    scientific_conclusion: Literal[False] = False

    @field_validator("sealed_at")
    @classmethod
    def validate_time(cls, value: str) -> str:
        _timestamp(value)
        return value

    @model_validator(mode="after")
    def validate_map(self) -> MainPrivateIdentityMapV1:
        blind_ids = tuple(item.blinded_unit_id for item in self.entries)
        if blind_ids != tuple(sorted(set(blind_ids))):
            raise ValueError("private entries must be blind-ID sorted and unique")
        pooled_ids = tuple(item.pooled_unit_id for item in self.entries)
        if len(pooled_ids) != len(set(pooled_ids)):
            raise ValueError("private map must be injective onto pooled units")
        _assert_identity(
            self,
            id_field="identity_map_id",
            sha_field="identity_map_sha256",
            prefix="main-private-identity-map-v1",
        )
        return self


class MainRawLabelV1(StrictModel):
    """One sealed human label.  There is deliberately no rationale/CoT field."""

    schema_version: Literal["flatband-main-raw-label-v1"] = (
        "flatband-main-raw-label-v1"
    )
    label_id: Identifier
    label_sha256: Sha256
    phase: ExecutionPhase
    expert_assignment_id: Identifier
    expert_assignment_sha256: Sha256
    reviewer_manifest_id: Identifier
    reviewer_manifest_sha256: Sha256
    private_identity_map_id: Identifier
    private_identity_map_sha256: Sha256
    review_unit_id: Identifier
    review_unit_sha256: Sha256
    pooled_unit_id: Identifier
    blinded_unit_id: Identifier
    reviewer_id: Identifier
    reviewer_person_commitment_sha256: Sha256
    reviewer_annotation_key_commitment_sha256: Sha256
    annotation_guide_sha256: Sha256
    status: Literal[
        MainGoldStatus.ASSESSABLE,
        MainGoldStatus.SYSTEM_PACKET_INVALID,
    ]
    relevance_grade: Annotated[int, Field(ge=0, le=3)]
    evidence_gain: Annotated[int, Field(ge=0, le=1)]
    submitted_at: Annotated[str, Field(min_length=20, max_length=40)]
    annotation_signature_hmac_sha256: Sha256
    annotation_signature_algorithm: Literal["HMAC-SHA256-PRECOMMITTED"] = (
        "HMAC-SHA256-PRECOMMITTED"
    )
    annotation_key_material_included: Literal[False] = False
    sealed: Literal[True] = True
    independent_pre_discussion: Literal[True] = True
    chain_of_thought_stored: Literal[False] = False
    scientific_conclusion: Literal[False] = False

    @field_validator("submitted_at")
    @classmethod
    def validate_time(cls, value: str) -> str:
        _timestamp(value)
        return value

    @model_validator(mode="after")
    def validate_label(self) -> MainRawLabelV1:
        if self.phase not in _MAIN_PHASES:
            raise ValueError("raw Main label uses a non-Main execution phase")
        _validate_label_surface(
            MainGoldStatus(self.status), self.relevance_grade, self.evidence_gain
        )
        _assert_identity(
            self,
            id_field="label_id",
            sha_field="label_sha256",
            prefix="main-raw-label-v1",
        )
        return self


class MainRawLabelRefV1(StrictModel):
    reviewer_id: Identifier
    reviewer_person_commitment_sha256: Sha256
    label_id: Identifier
    label_sha256: Sha256


class MainLabelAdjudicationV1(StrictModel):
    """A distinct expert's resolution of one genuine two-label disagreement."""

    schema_version: Literal["flatband-main-label-adjudication-v1"] = (
        "flatband-main-label-adjudication-v1"
    )
    adjudication_id: Identifier
    adjudication_sha256: Sha256
    phase: ExecutionPhase
    expert_assignment_id: Identifier
    expert_assignment_sha256: Sha256
    review_unit_id: Identifier
    review_unit_sha256: Sha256
    pooled_unit_id: Identifier
    raw_labels: Annotated[
        tuple[MainRawLabelRefV1, ...], Field(min_length=2, max_length=2)
    ]
    adjudicator_id: Identifier
    adjudicator_person_commitment_sha256: Sha256
    adjudicator_annotation_key_commitment_sha256: Sha256
    resolution_reason_code: Identifier
    final_status: MainGoldStatus
    final_relevance_grade: Annotated[int, Field(ge=0, le=3)]
    final_evidence_gain: Annotated[int, Field(ge=0, le=1)]
    adjudicated_at: Annotated[str, Field(min_length=20, max_length=40)]
    annotation_signature_hmac_sha256: Sha256
    annotation_signature_algorithm: Literal["HMAC-SHA256-PRECOMMITTED"] = (
        "HMAC-SHA256-PRECOMMITTED"
    )
    annotation_key_material_included: Literal[False] = False
    sealed: Literal[True] = True
    chain_of_thought_stored: Literal[False] = False
    scientific_conclusion: Literal[False] = False

    @field_validator("adjudicated_at")
    @classmethod
    def validate_time(cls, value: str) -> str:
        _timestamp(value)
        return value

    @model_validator(mode="after")
    def validate_adjudication(self) -> MainLabelAdjudicationV1:
        if self.phase not in _MAIN_PHASES:
            raise ValueError("Main adjudication uses a non-Main phase")
        keys = tuple(
            (item.reviewer_id, item.label_id, item.label_sha256)
            for item in self.raw_labels
        )
        if keys != tuple(sorted(set(keys))) or len(
            {item.reviewer_id for item in self.raw_labels}
        ) != 2:
            raise ValueError("adjudication requires two reviewer-sorted raw refs")
        if self.adjudicator_id in {item.reviewer_id for item in self.raw_labels}:
            raise ValueError("adjudicator must differ from both reviewers")
        if self.adjudicator_person_commitment_sha256 in {
            item.reviewer_person_commitment_sha256 for item in self.raw_labels
        }:
            raise ValueError("adjudicator natural person must differ from reviewers")
        _validate_label_surface(
            self.final_status,
            self.final_relevance_grade,
            self.final_evidence_gain,
        )
        _assert_identity(
            self,
            id_field="adjudication_id",
            sha_field="adjudication_sha256",
            prefix="main-label-adjudication-v1",
        )
        return self


class MainReviewerDuplicateClusterV1(StrictModel):
    cluster_id: Identifier
    blinded_unit_ids: Annotated[
        tuple[Identifier, ...], Field(min_length=1, max_length=100_000)
    ]

    @model_validator(mode="after")
    def validate_cluster(self) -> MainReviewerDuplicateClusterV1:
        if self.blinded_unit_ids != tuple(sorted(set(self.blinded_unit_ids))):
            raise ValueError("reviewer duplicate members must be sorted and unique")
        return self


class MainRawDuplicatePartitionV1(StrictModel):
    schema_version: Literal["flatband-main-raw-duplicate-partition-v1"] = (
        "flatband-main-raw-duplicate-partition-v1"
    )
    partition_id: Identifier
    partition_sha256: Sha256
    phase: ExecutionPhase
    expert_assignment_id: Identifier
    expert_assignment_sha256: Sha256
    reviewer_manifest_id: Identifier
    reviewer_manifest_sha256: Sha256
    private_identity_map_id: Identifier
    private_identity_map_sha256: Sha256
    reviewer_id: Identifier
    reviewer_person_commitment_sha256: Sha256
    reviewer_annotation_key_commitment_sha256: Sha256
    case_id: Identifier
    case_sha256: Sha256
    annotation_guide_sha256: Sha256
    clusters: Annotated[
        tuple[MainReviewerDuplicateClusterV1, ...], Field(min_length=1)
    ]
    submitted_at: Annotated[str, Field(min_length=20, max_length=40)]
    annotation_signature_hmac_sha256: Sha256
    annotation_signature_algorithm: Literal["HMAC-SHA256-PRECOMMITTED"] = (
        "HMAC-SHA256-PRECOMMITTED"
    )
    annotation_key_material_included: Literal[False] = False
    sealed: Literal[True] = True
    independent_pre_discussion: Literal[True] = True
    chain_of_thought_stored: Literal[False] = False
    scientific_conclusion: Literal[False] = False

    @field_validator("submitted_at")
    @classmethod
    def validate_time(cls, value: str) -> str:
        _timestamp(value)
        return value

    @model_validator(mode="after")
    def validate_partition(self) -> MainRawDuplicatePartitionV1:
        keys = tuple(
            (item.blinded_unit_ids, item.cluster_id) for item in self.clusters
        )
        if keys != tuple(sorted(set(keys))):
            raise ValueError("raw duplicate clusters must be member-sorted")
        members = tuple(
            unit for cluster in self.clusters for unit in cluster.blinded_unit_ids
        )
        if len(members) != len(set(members)):
            raise ValueError("a blinded unit occurs in multiple raw clusters")
        for cluster in self.clusters:
            expected = deterministic_id(
                "main-reviewer-dup-cluster-v1",
                {
                    "case_id": self.case_id,
                    "reviewer_id": self.reviewer_id,
                    "blinded_unit_ids": cluster.blinded_unit_ids,
                },
            )
            if cluster.cluster_id != expected:
                raise ValueError("reviewer duplicate cluster ID does not replay")
        _assert_identity(
            self,
            id_field="partition_id",
            sha_field="partition_sha256",
            prefix="main-raw-duplicate-partition-v1",
        )
        return self


class MainDuplicatePartitionRefV1(StrictModel):
    reviewer_id: Identifier
    reviewer_person_commitment_sha256: Sha256
    partition_id: Identifier
    partition_sha256: Sha256


class MainPooledDuplicateClusterV1(StrictModel):
    cluster_id: Identifier
    pooled_unit_ids: Annotated[
        tuple[Identifier, ...], Field(min_length=1, max_length=100_000)
    ]

    @model_validator(mode="after")
    def validate_cluster(self) -> MainPooledDuplicateClusterV1:
        if self.pooled_unit_ids != tuple(sorted(set(self.pooled_unit_ids))):
            raise ValueError("pooled duplicate members must be sorted and unique")
        return self


class MainDuplicateAdjudicationV1(StrictModel):
    schema_version: Literal["flatband-main-duplicate-adjudication-v1"] = (
        "flatband-main-duplicate-adjudication-v1"
    )
    adjudication_id: Identifier
    adjudication_sha256: Sha256
    phase: ExecutionPhase
    expert_assignment_id: Identifier
    expert_assignment_sha256: Sha256
    case_id: Identifier
    case_sha256: Sha256
    raw_partitions: Annotated[
        tuple[MainDuplicatePartitionRefV1, ...], Field(min_length=2, max_length=2)
    ]
    adjudicator_id: Identifier
    adjudicator_person_commitment_sha256: Sha256
    adjudicator_annotation_key_commitment_sha256: Sha256
    resolution_reason_code: Identifier
    final_clusters: Annotated[
        tuple[MainPooledDuplicateClusterV1, ...], Field(min_length=1)
    ]
    adjudicated_at: Annotated[str, Field(min_length=20, max_length=40)]
    annotation_signature_hmac_sha256: Sha256
    annotation_signature_algorithm: Literal["HMAC-SHA256-PRECOMMITTED"] = (
        "HMAC-SHA256-PRECOMMITTED"
    )
    annotation_key_material_included: Literal[False] = False
    sealed: Literal[True] = True
    chain_of_thought_stored: Literal[False] = False
    scientific_conclusion: Literal[False] = False

    @field_validator("adjudicated_at")
    @classmethod
    def validate_time(cls, value: str) -> str:
        _timestamp(value)
        return value

    @model_validator(mode="after")
    def validate_adjudication(self) -> MainDuplicateAdjudicationV1:
        refs = tuple(
            (
                item.reviewer_id,
                item.reviewer_person_commitment_sha256,
                item.partition_id,
                item.partition_sha256,
            )
            for item in self.raw_partitions
        )
        if refs != tuple(sorted(set(refs))):
            raise ValueError("duplicate partition refs must be reviewer-sorted")
        if self.adjudicator_id in {item.reviewer_id for item in self.raw_partitions}:
            raise ValueError("duplicate adjudicator must differ from reviewers")
        if self.adjudicator_person_commitment_sha256 in {
            item.reviewer_person_commitment_sha256 for item in self.raw_partitions
        }:
            raise ValueError("duplicate adjudicator natural person must be distinct")
        keys = tuple(
            (item.pooled_unit_ids, item.cluster_id) for item in self.final_clusters
        )
        if keys != tuple(sorted(set(keys))):
            raise ValueError("adjudicated duplicate clusters must be member-sorted")
        members = tuple(
            unit for cluster in self.final_clusters for unit in cluster.pooled_unit_ids
        )
        if len(members) != len(set(members)):
            raise ValueError("adjudicated duplicate clusters overlap")
        for cluster in self.final_clusters:
            expected = deterministic_id(
                "main-pooled-duplicate-cluster-v1",
                {
                    "phase": self.phase.value,
                    "case_id": self.case_id,
                    "pooled_unit_ids": cluster.pooled_unit_ids,
                },
            )
            if cluster.cluster_id != expected:
                raise ValueError("adjudicated pooled cluster ID does not replay")
        _assert_identity(
            self,
            id_field="adjudication_id",
            sha_field="adjudication_sha256",
            prefix="main-duplicate-adjudication-v1",
        )
        return self


class MainDuplicateAdjudicationRefV1(StrictModel):
    adjudication_id: Identifier
    adjudication_sha256: Sha256
    adjudicator_id: Identifier
    adjudicator_person_commitment_sha256: Sha256


class MainFinalDuplicatePartitionV1(StrictModel):
    schema_version: Literal["flatband-main-final-duplicate-partition-v1"] = (
        "flatband-main-final-duplicate-partition-v1"
    )
    partition_id: Identifier
    partition_sha256: Sha256
    phase: ExecutionPhase
    case_id: Identifier
    case_sha256: Sha256
    raw_partitions: Annotated[
        tuple[MainDuplicatePartitionRefV1, ...], Field(min_length=2, max_length=2)
    ]
    provenance: MainDuplicateProvenance
    adjudication: MainDuplicateAdjudicationRefV1 | None = None
    clusters: Annotated[
        tuple[MainPooledDuplicateClusterV1, ...], Field(min_length=1)
    ]
    finalized_at: Annotated[str, Field(min_length=20, max_length=40)]
    scientific_conclusion: Literal[False] = False

    @field_validator("finalized_at")
    @classmethod
    def validate_time(cls, value: str) -> str:
        _timestamp(value)
        return value

    @model_validator(mode="after")
    def validate_partition(self) -> MainFinalDuplicatePartitionV1:
        if self.provenance is MainDuplicateProvenance.AGREED_RAW:
            if self.adjudication is not None:
                raise ValueError("agreed duplicate partition cannot be adjudicated")
        elif self.adjudication is None:
            raise ValueError("disputed duplicate partition requires adjudication")
        members = tuple(
            unit for cluster in self.clusters for unit in cluster.pooled_unit_ids
        )
        if len(members) != len(set(members)):
            raise ValueError("final duplicate clusters overlap")
        for cluster in self.clusters:
            expected = deterministic_id(
                "main-pooled-duplicate-cluster-v1",
                {
                    "phase": self.phase.value,
                    "case_id": self.case_id,
                    "pooled_unit_ids": cluster.pooled_unit_ids,
                },
            )
            if cluster.cluster_id != expected:
                raise ValueError("final pooled cluster ID does not replay")
        _assert_identity(
            self,
            id_field="partition_id",
            sha_field="partition_sha256",
            prefix="main-final-dup-partition-v1",
        )
        return self


class MainArmTraceRefV1(StrictModel):
    trace_id: Identifier
    trace_sha256: Sha256
    system_id: ResearchSystemId
    ranking_id: Identifier
    ranking_sha256: Sha256
    cell_id: Identifier
    run_id: Identifier
    case_id: Identifier
    case_sha256: Sha256


class MainPhaseExecutionRefV1(StrictModel):
    release_id: Identifier
    release_sha256: Sha256
    source_execution_phase: ExecutionPhase


class MainExecutionCellRefV1(StrictModel):
    execution_release_id: Identifier
    execution_release_sha256: Sha256
    cell_evidence_id: Identifier
    cell_evidence_sha256: Sha256
    source_execution_phase: ExecutionPhase
    analysis_role: AnalysisTraceRoleV2
    system_id: ResearchSystemId
    terminal_result_id: Identifier
    terminal_result_sha256: Sha256
    top5_projection_id: Identifier
    top5_projection_sha256: Sha256
    trace_id: Identifier | None = None
    trace_sha256: Sha256 | None = None
    cell_id: Identifier
    run_id: Identifier
    case_id: Identifier
    case_sha256: Sha256

    @model_validator(mode="after")
    def validate_ref(self) -> MainExecutionCellRefV1:
        if (self.trace_id is None) != (self.trace_sha256 is None):
            raise ValueError("execution-cell trace identity must be both present or absent")
        return self


class MainGoldPositionProjectionV1(StrictModel):
    """Minimal analysis input for exactly one of five frozen cell positions."""

    schema_version: Literal["flatband-main-gold-position-projection-v1"] = (
        "flatband-main-gold-position-projection-v1"
    )
    projection_id: Identifier
    projection_sha256: Sha256
    phase: ExecutionPhase
    execution_cell_evidence_id: Identifier
    execution_cell_evidence_sha256: Sha256
    trace_id: Identifier | None = None
    trace_sha256: Sha256 | None = None
    cell_id: Identifier
    run_id: Identifier
    case_id: Identifier
    case_sha256: Sha256
    system_id: ResearchSystemId
    selection_rank: Annotated[int, Field(ge=1, le=5)]
    pooled_unit_id: Identifier | None = None
    packet_id: Identifier | None = None
    packet_sha256: Sha256 | None = None
    status: MainGoldStatus
    relevance_grade: Annotated[int, Field(ge=0, le=3)]
    evidence_gain: Annotated[int, Field(ge=0, le=1)]
    final_duplicate_cluster_id: Identifier
    provenance: MainGoldProvenance
    absence_reason_codes: Annotated[
        tuple[Identifier, ...], Field(max_length=16)
    ] = ()
    scientific_conclusion: Literal[False] = False

    @model_validator(mode="after")
    def validate_projection(self) -> MainGoldPositionProjectionV1:
        if (self.trace_id is None) != (self.trace_sha256 is None):
            raise ValueError("Gold position trace identity must be both present or absent")
        _validate_label_surface(
            self.status, self.relevance_grade, self.evidence_gain
        )
        packet_values = (self.pooled_unit_id, self.packet_id, self.packet_sha256)
        if self.provenance is MainGoldProvenance.SYSTEM_DERIVED_ABSENCE:
            if any(value is not None for value in packet_values):
                raise ValueError("absent projection cannot carry packet identity")
            if self.status is not MainGoldStatus.SYSTEM_PACKET_INVALID:
                raise ValueError("absent projection must be SYSTEM_PACKET_INVALID")
            if not self.absence_reason_codes:
                raise ValueError("absent projection requires frozen reason codes")
        else:
            if any(value is None for value in packet_values):
                raise ValueError("present projection requires packet identity")
            if self.trace_id is None:
                raise ValueError("present projection requires its exact Arm trace")
            if self.absence_reason_codes:
                raise ValueError("present projection cannot carry absence reasons")
        if self.absence_reason_codes != tuple(
            sorted(set(self.absence_reason_codes))
        ):
            raise ValueError("absence reason codes must be sorted and unique")
        _assert_identity(
            self,
            id_field="projection_id",
            sha_field="projection_sha256",
            prefix="main-gold-position-v1",
        )
        return self


class MainGoldReleaseV1(StrictModel):
    """Private, content-addressed expert inputs and their derived projections."""

    schema_version: Literal["flatband-main-gold-release-v1"] = (
        "flatband-main-gold-release-v1"
    )
    release_id: Identifier
    release_sha256: Sha256
    phase: ExecutionPhase
    annotation_guide_sha256: Sha256
    blinding_commitment_sha256: Sha256
    expert_assignment: MainExpertAssignmentV1
    phase_execution_releases: Annotated[
        tuple[MainPhaseExecutionRefV1, ...], Field(min_length=1, max_length=4)
    ]
    execution_cells: Annotated[
        tuple[MainExecutionCellRefV1, ...], Field(min_length=1, max_length=540)
    ]
    arm_traces: Annotated[tuple[MainArmTraceRefV1, ...], Field(max_length=540)] = ()
    review_units: Annotated[tuple[MainReviewUnitV1, ...], Field(max_length=100_000)]
    reviewer_manifests: Annotated[
        tuple[MainReviewerManifestV1, ...], Field(min_length=2, max_length=2)
    ]
    private_identity_maps: Annotated[
        tuple[MainPrivateIdentityMapV1, ...], Field(min_length=2, max_length=2)
    ]
    raw_labels: Annotated[tuple[MainRawLabelV1, ...], Field(max_length=200_000)]
    adjudications: Annotated[
        tuple[MainLabelAdjudicationV1, ...], Field(max_length=100_000)
    ]
    raw_duplicate_partitions: Annotated[
        tuple[MainRawDuplicatePartitionV1, ...], Field(max_length=20_000)
    ] = ()
    duplicate_adjudications: Annotated[
        tuple[MainDuplicateAdjudicationV1, ...], Field(max_length=10_000)
    ] = ()
    final_duplicate_partitions: Annotated[
        tuple[MainFinalDuplicatePartitionV1, ...], Field(max_length=10_000)
    ] = ()
    position_projections: Annotated[
        tuple[MainGoldPositionProjectionV1, ...], Field(min_length=5)
    ]
    released_at: Annotated[str, Field(min_length=20, max_length=40)]
    private_storage_required: Literal[True] = True
    caller_final_surface_accepted: Literal[False] = False
    key_material_included: Literal[False] = False
    annotation_authentication_scope: Literal[
        "INTERNAL_PRECOMMITTED_HMAC_POLICY"
    ] = "INTERNAL_PRECOMMITTED_HMAC_POLICY"
    external_expert_identity_attestation: Literal["NOT_PROVIDED"] = (
        "NOT_PROVIDED"
    )
    external_annotation_key_custody_attestation: Literal["NOT_PROVIDED"] = (
        "NOT_PROVIDED"
    )
    chain_of_thought_stored: Literal[False] = False
    scientific_conclusion: Literal[False] = False

    @field_validator("released_at")
    @classmethod
    def validate_time(cls, value: str) -> str:
        _timestamp(value)
        return value

    @model_validator(mode="after")
    def validate_release(self) -> MainGoldReleaseV1:
        if self.phase not in _MAIN_PHASES:
            raise ValueError("Gold release uses a non-Main phase")
        assignment = _revalidate(self.expert_assignment, MainExpertAssignmentV1)
        if assignment.phase is not self.phase:
            raise ValueError("Gold release expert assignment uses another phase")
        release_keys = tuple(
            (item.source_execution_phase.value, item.release_id)
            for item in self.phase_execution_releases
        )
        if release_keys != tuple(sorted(set(release_keys))):
            raise ValueError("phase execution refs must be phase sorted and unique")
        cell_keys = tuple(
            (item.case_id, item.analysis_role.value, item.cell_id)
            for item in self.execution_cells
        )
        if cell_keys != tuple(sorted(set(cell_keys))):
            raise ValueError("execution cell refs must be case/role/cell sorted and unique")
        trace_keys = tuple((item.case_id, item.system_id.value, item.cell_id) for item in self.arm_traces)
        if trace_keys != tuple(sorted(set(trace_keys))):
            raise ValueError("arm trace refs must be case/system/cell sorted and unique")
        expected_trace_addresses = {
            (item.trace_id, item.trace_sha256)
            for item in self.execution_cells
            if item.trace_id is not None
        }
        observed_trace_addresses = {
            (item.trace_id, item.trace_sha256) for item in self.arm_traces
        }
        if observed_trace_addresses != expected_trace_addresses:
            raise ValueError("arm trace refs differ from non-failed execution cells")
        release_addresses = {
            (item.release_id, item.release_sha256)
            for item in self.phase_execution_releases
        }
        if any(
            (item.execution_release_id, item.execution_release_sha256)
            not in release_addresses
            for item in self.execution_cells
        ):
            raise ValueError("Gold execution cell binds a foreign phase release")
        unit_keys = tuple(item.pooled_unit_id for item in self.review_units)
        if unit_keys != tuple(sorted(set(unit_keys))):
            raise ValueError("review units must be pooled-unit sorted and unique")
        manifest_ids = tuple(item.manifest_id for item in self.reviewer_manifests)
        if len(manifest_ids) != len(set(manifest_ids)):
            raise ValueError("Gold release repeats a reviewer manifest")
        map_reviewers = tuple(item.reviewer_id for item in self.private_identity_maps)
        if map_reviewers != tuple(sorted(set(map_reviewers))):
            raise ValueError("private maps must be reviewer-sorted and distinct")
        manifest_by_id = {item.manifest_id: item for item in self.reviewer_manifests}
        for identity_map in self.private_identity_maps:
            manifest = manifest_by_id.get(identity_map.reviewer_manifest_id)
            if manifest is None or manifest.manifest_sha256 != identity_map.reviewer_manifest_sha256:
                raise ValueError("private identity map binds a foreign manifest")
            if identity_map.blinding_commitment_sha256 != self.blinding_commitment_sha256:
                raise ValueError("private map binds another blinding commitment")
            if (
                identity_map.expert_assignment_id,
                identity_map.expert_assignment_sha256,
            ) != (assignment.assignment_id, assignment.assignment_sha256):
                raise ValueError("private map binds another expert assignment")
        label_keys = tuple(
            (item.pooled_unit_id, item.reviewer_id) for item in self.raw_labels
        )
        if label_keys != tuple(sorted(set(label_keys))):
            raise ValueError("raw labels must be pooled-unit/reviewer sorted")
        adjudication_keys = tuple(item.pooled_unit_id for item in self.adjudications)
        if adjudication_keys != tuple(sorted(set(adjudication_keys))):
            raise ValueError("adjudications must be pooled-unit sorted and unique")
        raw_partition_keys = tuple(
            (item.case_id, item.reviewer_id)
            for item in self.raw_duplicate_partitions
        )
        if raw_partition_keys != tuple(sorted(set(raw_partition_keys))):
            raise ValueError("raw duplicate partitions must be case/reviewer sorted")
        duplicate_decision_cases = tuple(
            item.case_id for item in self.duplicate_adjudications
        )
        if duplicate_decision_cases != tuple(
            sorted(set(duplicate_decision_cases))
        ):
            raise ValueError("duplicate adjudications must be case sorted and unique")
        final_partition_cases = tuple(
            item.case_id for item in self.final_duplicate_partitions
        )
        if final_partition_cases != tuple(sorted(set(final_partition_cases))):
            raise ValueError("final duplicate partitions must be case sorted and unique")
        position_keys = tuple(
            (item.case_id, item.system_id.value, item.cell_id, item.selection_rank)
            for item in self.position_projections
        )
        if position_keys != tuple(sorted(set(position_keys))):
            raise ValueError("Gold positions must be canonically sorted and unique")
        expected = {
            (item.case_id, item.system_id.value, item.cell_id, rank)
            for item in self.execution_cells
            for rank in range(1, 6)
        }
        if set(position_keys) != expected:
            raise ValueError("Gold release must expose exactly five positions per cell")
        release_time = _timestamp(self.released_at)
        if any(_timestamp(item.submitted_at) >= release_time for item in self.raw_labels):
            raise ValueError("raw label must predate Gold release")
        if any(_timestamp(item.adjudicated_at) >= release_time for item in self.adjudications):
            raise ValueError("adjudication must predate Gold release")
        if any(
            _timestamp(item.submitted_at) >= release_time
            for item in self.raw_duplicate_partitions
        ):
            raise ValueError("raw duplicate partition must predate Gold release")
        if any(
            _timestamp(item.adjudicated_at) >= release_time
            for item in self.duplicate_adjudications
        ):
            raise ValueError("duplicate adjudication must predate Gold release")
        _assert_identity(
            self,
            id_field="release_id",
            sha_field="release_sha256",
            prefix="main-gold-release-v1",
        )
        return self


class MainGoldFormalVerifierAttestationV1(StrictModel):
    """Content address emitted only after exact Gold/key replay succeeds."""

    schema_version: Literal["flatband-main-gold-formal-verifier-v1"] = (
        "flatband-main-gold-formal-verifier-v1"
    )
    attestation_id: Identifier
    attestation_sha256: Sha256
    gold_release_id: Identifier
    gold_release_sha256: Sha256
    phase: ExecutionPhase
    blinding_commitment_sha256: Sha256
    phase_execution_releases: Annotated[
        tuple[MainPhaseExecutionRefV1, ...], Field(min_length=1, max_length=4)
    ]
    execution_cells: Annotated[
        tuple[MainExecutionCellRefV1, ...], Field(min_length=1, max_length=540)
    ]
    arm_traces: Annotated[tuple[MainArmTraceRefV1, ...], Field(max_length=540)] = ()
    verified_at: Annotated[str, Field(min_length=20, max_length=40)]
    exact_gold_replay_verified: Literal[True] = True
    exact_duplicate_partition_replay_verified: Literal[True] = True
    ephemeral_key_verified_not_stored: Literal[True] = True
    expert_annotation_signatures_verified: Literal[True] = True
    expert_annotation_keys_verified_not_stored: Literal[True] = True
    annotation_authentication_scope: Literal[
        "INTERNAL_PRECOMMITTED_HMAC_POLICY"
    ] = "INTERNAL_PRECOMMITTED_HMAC_POLICY"
    external_expert_identity_attestation: Literal["NOT_PROVIDED"] = (
        "NOT_PROVIDED"
    )
    external_annotation_key_custody_attestation: Literal["NOT_PROVIDED"] = (
        "NOT_PROVIDED"
    )
    raw_annotation_or_cot_included: Literal[False] = False
    scientific_conclusion: Literal[False] = False

    @field_validator("verified_at")
    @classmethod
    def validate_time(cls, value: str) -> str:
        _timestamp(value)
        return value

    @model_validator(mode="after")
    def validate_attestation(self) -> MainGoldFormalVerifierAttestationV1:
        _assert_identity(
            self,
            id_field="attestation_id",
            sha_field="attestation_sha256",
            prefix="main-gold-verifier-v1",
        )
        return self


def build_main_review_unit(packet: HypothesisPacketV1) -> MainReviewUnitV1:
    value = _revalidate(packet, HypothesisPacketV1)
    pooled = deterministic_id(
        "main-pooled-unit-v1",
        {
            "case_id": value.case_id,
            "case_sha256": value.case_sha256,
            "packet_id": value.packet_id,
            "packet_sha256": value.packet_sha256,
        },
    )
    return _build_identified(
        MainReviewUnitV1,
        id_field="review_unit_id",
        sha_field="review_unit_sha256",
        prefix="main-review-unit-v1",
        values={
            "pooled_unit_id": pooled,
            "case_id": value.case_id,
            "case_sha256": value.case_sha256,
            "packet": value,
        },
    )


def build_main_expert_assignment(
    *,
    phase: ExecutionPhase,
    expert_registry: MainExpertRegistryRefV1,
    reviewers: tuple[MainExpertIdentityCommitmentV1, MainExpertIdentityCommitmentV1],
    adjudicators: tuple[MainExpertIdentityCommitmentV1, ...],
    sealed_at: str,
) -> MainExpertAssignmentV1:
    return _build_identified(
        MainExpertAssignmentV1,
        id_field="assignment_id",
        sha_field="assignment_sha256",
        prefix="main-expert-assignment-v1",
        values={
            "phase": phase,
            "expert_registry": _revalidate(
                expert_registry, MainExpertRegistryRefV1
            ),
            "reviewers": tuple(sorted(reviewers, key=lambda item: item.expert_id)),
            "adjudicators": tuple(
                sorted(adjudicators, key=lambda item: item.expert_id)
            ),
            "sealed_at": sealed_at,
        },
    )


def _validated_expert_annotation_keys(
    assignment: MainExpertAssignmentV1,
    expert_annotation_keys: Mapping[str, bytes],
) -> dict[str, bytes]:
    experts = {
        item.expert_id: item for item in (*assignment.reviewers, *assignment.adjudicators)
    }
    if set(expert_annotation_keys) != set(experts):
        raise ValueError(
            "expert annotation keys must exactly cover the frozen assignment"
        )
    keys: dict[str, bytes] = {}
    for expert_id, identity in experts.items():
        key = _require_annotation_key(expert_annotation_keys[expert_id])
        if hashlib.sha256(key).hexdigest() != (
            identity.annotation_key_commitment_sha256
        ):
            raise ValueError(
                f"expert annotation key differs from frozen commitment: {expert_id}"
            )
        keys[expert_id] = key
    return keys


def _canonical_main_traces(
    phase: ExecutionPhase,
    arm_traces: tuple[ArmExecutionTraceV1, ...],
) -> tuple[ArmExecutionTraceV1, ...]:
    traces = tuple(
        sorted(
            (_revalidate(item, ArmExecutionTraceV1) for item in arm_traces),
            key=lambda item: (
                item.ranking.case_id,
                item.system_config.system_id.value,
                item.ranking.cell_id,
                item.trace_id,
            ),
        )
    )
    for trace in traces:
        _validate_phase_trace(phase, trace)
    cells = tuple(item.ranking.cell_id for item in traces)
    if len(cells) != len(set(cells)):
        raise ValueError("Main expert review cannot receive duplicate cells")
    trace_ids = tuple(item.trace_id for item in traces)
    if len(trace_ids) != len(set(trace_ids)):
        raise ValueError("Main expert review cannot receive duplicate traces")
    return traces


_GOLD_SOURCE_PHASES = {
    ExecutionPhase.DEVELOPMENT_ABLATIONS: (
        ExecutionPhase.DEVELOPMENT_ABLATIONS,
    ),
    ExecutionPhase.DEVELOPMENT_FUSION: (
        ExecutionPhase.DEVELOPMENT_FUSION,
    ),
    ExecutionPhase.LOCKED_PRIMARY: tuple(
        sorted(
            (
                ExecutionPhase.LOCKED_PRIMARY,
                ExecutionPhase.LOCKED_FUSION_MINUS_E1,
                ExecutionPhase.LOCKED_FUSION_MINUS_E2,
                ExecutionPhase.LOCKED_FUSION_MINUS_E3,
            ),
            key=lambda item: item.value,
        )
    ),
}


def _canonical_gold_execution_inputs(
    phase: ExecutionPhase,
    phase_execution_releases: tuple[MainPhaseExecutionReleaseV1, ...],
) -> tuple[
    tuple[MainPhaseExecutionReleaseV1, ...],
    tuple[MainPhaseExecutionCellEvidenceV1, ...],
    tuple[ArmExecutionTraceV1, ...],
]:
    if phase not in _GOLD_SOURCE_PHASES:
        raise ValueError("Gold aggregate uses a non-formal Main phase")
    releases = tuple(
        sorted(
            (
                _revalidate(item, MainPhaseExecutionReleaseV1)
                for item in phase_execution_releases
            ),
            key=lambda item: item.source_execution_phase.value,
        )
    )
    source_phases = tuple(item.source_execution_phase for item in releases)
    if source_phases != _GOLD_SOURCE_PHASES[phase]:
        raise ValueError("Gold aggregate execution phases differ from frozen design")
    cells = tuple(
        sorted(
            (
                cell
                for release in releases
                for cell in main_gold_execution_cells_from_main_v1(release)
            ),
            key=lambda item: (item.case_id, item.analysis_role.value, item.cell_evidence_id),
        )
    )
    cell_keys = tuple((item.case_id, item.analysis_role) for item in cells)
    if len(cell_keys) != len(set(cell_keys)):
        raise ValueError("Gold aggregate repeats one case/analysis role")
    traces = _canonical_main_traces(
        phase,
        tuple(item.arm_trace for item in cells if item.arm_trace is not None),
    )
    return releases, cells, traces


def _review_units_and_contributions(
    traces: tuple[ArmExecutionTraceV1, ...],
) -> tuple[
    tuple[MainReviewUnitV1, ...],
    dict[str, tuple[MainPositionContributionV1, ...]],
]:
    packet_by_key: dict[tuple[str, str], HypothesisPacketV1] = {}
    contributions_by_packet: dict[
        tuple[str, str], list[MainPositionContributionV1]
    ] = {}
    for trace in traces:
        packet_index = {
            (item.packet_id, item.packet_sha256): item
            for item in trace.hypothesis_packets
        }
        for position in trace.ranking.positions:
            key = (position.packet_id, position.packet_sha256)
            packet = packet_index.get(key)
            if packet is None:
                raise ValueError("trace ranking position lacks its actual packet")
            prior = packet_by_key.setdefault(key, packet)
            if prior != packet:
                raise ValueError("one packet identity aliases different content")
            contributions_by_packet.setdefault(key, []).append(
                MainPositionContributionV1(
                    trace_id=trace.trace_id,
                    trace_sha256=trace.trace_sha256,
                    ranking_id=trace.ranking.ranking_id,
                    ranking_sha256=trace.ranking.ranking_sha256,
                    system_id=trace.system_config.system_id,
                    cell_id=trace.ranking.cell_id,
                    run_id=trace.ranking.run_id,
                    selection_rank=position.selection_rank,
                )
            )
    units = tuple(
        sorted(
            (build_main_review_unit(item) for item in packet_by_key.values()),
            key=lambda item: item.pooled_unit_id,
        )
    )
    canonical: dict[str, tuple[MainPositionContributionV1, ...]] = {}
    for unit in units:
        values = contributions_by_packet[
            (unit.packet.packet_id, unit.packet.packet_sha256)
        ]
        canonical[unit.pooled_unit_id] = tuple(
            sorted(
                values,
                key=lambda item: (
                    item.system_id.value,
                    item.cell_id,
                    item.selection_rank,
                    item.trace_id,
                ),
            )
        )
    return units, canonical


def build_main_reviewer_materials(
    *,
    phase: ExecutionPhase,
    arm_traces: tuple[ArmExecutionTraceV1, ...],
    expert_assignment: MainExpertAssignmentV1,
    ephemeral_blinding_key: bytes,
    renderer_sha256: str,
    sealed_at: str,
) -> tuple[
    tuple[MainReviewerManifestV1, MainReviewerManifestV1],
    tuple[MainPrivateIdentityMapV1, MainPrivateIdentityMapV1],
]:
    """Build public manifests/private joins without retaining key material."""

    key = _require_blinding_key(ephemeral_blinding_key)
    assignment = _revalidate(expert_assignment, MainExpertAssignmentV1)
    if assignment.phase is not phase:
        raise ValueError("reviewer materials bind another expert assignment phase")
    if _timestamp(sealed_at) <= _timestamp(assignment.sealed_at):
        raise ValueError("reviewer materials predate frozen expert assignment")
    reviewers = assignment.reviewers
    traces = _canonical_main_traces(phase, arm_traces)
    units, contributions = _review_units_and_contributions(traces)
    commitment = hashlib.sha256(key).hexdigest()
    manifests: list[MainReviewerManifestV1] = []
    maps: list[MainPrivateIdentityMapV1] = []
    for reviewer in reviewers:
        reviewer_id = reviewer.expert_id
        blinded_reviewer = _blinded_id(
            "main-blinded-reviewer-v1",
            key,
            {"phase": phase.value, "reviewer_id": reviewer_id},
        )
        ordered_units = tuple(
            sorted(
                units,
                key=lambda item: _blinded_id(
                    "main-display-order-v1",
                    key,
                    {
                        "phase": phase.value,
                        "reviewer_id": reviewer_id,
                        "pooled_unit_id": item.pooled_unit_id,
                    },
                ),
            )
        )
        blind_by_pool = {
            item.pooled_unit_id: _blinded_id(
                "main-blinded-unit-v1",
                key,
                {
                    "phase": phase.value,
                    "reviewer_id": reviewer_id,
                    "pooled_unit_id": item.pooled_unit_id,
                    "packet_sha256": item.packet.packet_sha256,
                },
            )
            for item in ordered_units
        }
        packets = tuple(
            MainReviewerPacketV1(
                display_order=index,
                blinded_unit_id=blind_by_pool[item.pooled_unit_id],
                packet=item.packet,
            )
            for index, item in enumerate(ordered_units, start=1)
        )
        manifest = _build_identified(
            MainReviewerManifestV1,
            id_field="manifest_id",
            sha_field="manifest_sha256",
            prefix="main-reviewer-manifest-v1",
            values={
                "blinded_reviewer_id": blinded_reviewer,
                "renderer_sha256": renderer_sha256,
                "packets": packets,
                "sealed_at": sealed_at,
            },
        )
        entries = tuple(
            sorted(
                (
                    MainPrivateUnitMapEntryV1(
                        blinded_unit_id=blind_by_pool[item.pooled_unit_id],
                        review_unit_id=item.review_unit_id,
                        review_unit_sha256=item.review_unit_sha256,
                        pooled_unit_id=item.pooled_unit_id,
                        case_id=item.case_id,
                        case_sha256=item.case_sha256,
                        packet_id=item.packet.packet_id,
                        packet_sha256=item.packet.packet_sha256,
                        contributions=contributions[item.pooled_unit_id],
                    )
                    for item in units
                ),
                key=lambda item: item.blinded_unit_id,
            )
        )
        identity_map = _build_identified(
            MainPrivateIdentityMapV1,
            id_field="identity_map_id",
            sha_field="identity_map_sha256",
            prefix="main-private-identity-map-v1",
            values={
                "phase": phase,
                "expert_assignment_id": assignment.assignment_id,
                "expert_assignment_sha256": assignment.assignment_sha256,
                "reviewer_manifest_id": manifest.manifest_id,
                "reviewer_manifest_sha256": manifest.manifest_sha256,
                "reviewer_id": reviewer_id,
                "reviewer_person_commitment_sha256": (
                    reviewer.natural_person_commitment_sha256
                ),
                "reviewer_annotation_key_commitment_sha256": (
                    reviewer.annotation_key_commitment_sha256
                ),
                "blinded_reviewer_id": blinded_reviewer,
                "blinding_commitment_sha256": commitment,
                "entries": entries,
                "sealed_at": sealed_at,
            },
        )
        manifests.append(manifest)
        maps.append(identity_map)
    return (
        (manifests[0], manifests[1]),
        (maps[0], maps[1]),
    )


def build_main_raw_label(
    reviewer_manifest: MainReviewerManifestV1,
    private_identity_map: MainPrivateIdentityMapV1,
    *,
    blinded_unit_id: str,
    annotation_guide_sha256: str,
    status: Literal[
        MainGoldStatus.ASSESSABLE,
        MainGoldStatus.SYSTEM_PACKET_INVALID,
    ],
    relevance_grade: int,
    evidence_gain: int,
    submitted_at: str,
    reviewer_annotation_key: bytes,
) -> MainRawLabelV1:
    manifest = _revalidate(reviewer_manifest, MainReviewerManifestV1)
    identity_map = _revalidate(private_identity_map, MainPrivateIdentityMapV1)
    if (
        identity_map.reviewer_manifest_id,
        identity_map.reviewer_manifest_sha256,
        identity_map.blinded_reviewer_id,
    ) != (
        manifest.manifest_id,
        manifest.manifest_sha256,
        manifest.blinded_reviewer_id,
    ):
        raise ValueError("raw label manifest differs from its private identity map")
    public_ids = {item.blinded_unit_id for item in manifest.packets}
    entry = next(
        (item for item in identity_map.entries if item.blinded_unit_id == blinded_unit_id),
        None,
    )
    if blinded_unit_id not in public_ids or entry is None:
        raise ValueError("raw label blind ID is absent from reviewer materials")
    if _timestamp(submitted_at) <= _timestamp(identity_map.sealed_at):
        raise ValueError("raw label must follow reviewer-material seal")
    key = _require_annotation_key(reviewer_annotation_key)
    if hashlib.sha256(key).hexdigest() != (
        identity_map.reviewer_annotation_key_commitment_sha256
    ):
        raise ValueError("raw-label annotation key differs from reviewer commitment")
    return _build_signed_identified(
        MainRawLabelV1,
        id_field="label_id",
        sha_field="label_sha256",
        prefix="main-raw-label-v1",
        signature_field="annotation_signature_hmac_sha256",
        signature_schema_version="flatband-main-raw-label-signature-v1",
        annotation_key=key,
        values={
            "phase": identity_map.phase,
            "expert_assignment_id": identity_map.expert_assignment_id,
            "expert_assignment_sha256": identity_map.expert_assignment_sha256,
            "reviewer_manifest_id": manifest.manifest_id,
            "reviewer_manifest_sha256": manifest.manifest_sha256,
            "private_identity_map_id": identity_map.identity_map_id,
            "private_identity_map_sha256": identity_map.identity_map_sha256,
            "review_unit_id": entry.review_unit_id,
            "review_unit_sha256": entry.review_unit_sha256,
            "pooled_unit_id": entry.pooled_unit_id,
            "blinded_unit_id": entry.blinded_unit_id,
            "reviewer_id": identity_map.reviewer_id,
            "reviewer_person_commitment_sha256": (
                identity_map.reviewer_person_commitment_sha256
            ),
            "reviewer_annotation_key_commitment_sha256": (
                identity_map.reviewer_annotation_key_commitment_sha256
            ),
            "annotation_guide_sha256": annotation_guide_sha256,
            "status": status,
            "relevance_grade": relevance_grade,
            "evidence_gain": evidence_gain,
            "submitted_at": submitted_at,
        },
    )


def build_main_label_adjudication(
    review_unit: MainReviewUnitV1,
    raw_labels: tuple[MainRawLabelV1, MainRawLabelV1],
    *,
    expert_assignment: MainExpertAssignmentV1,
    adjudicator_id: str,
    resolution_reason_code: str,
    final_status: MainGoldStatus,
    final_relevance_grade: int,
    final_evidence_gain: int,
    adjudicated_at: str,
    adjudicator_annotation_key: bytes,
) -> MainLabelAdjudicationV1:
    unit = _revalidate(review_unit, MainReviewUnitV1)
    assignment = _revalidate(expert_assignment, MainExpertAssignmentV1)
    labels = tuple(
        sorted(
            (_revalidate(item, MainRawLabelV1) for item in raw_labels),
            key=lambda item: item.reviewer_id,
        )
    )
    if len(labels) != 2:
        raise ValueError("label adjudication requires exactly two raw labels")
    if len({(item.status, item.relevance_grade, item.evidence_gain) for item in labels}) == 1:
        raise ValueError("agreement must not be adjudicated")
    if any(
        (item.review_unit_id, item.review_unit_sha256, item.pooled_unit_id)
        != (unit.review_unit_id, unit.review_unit_sha256, unit.pooled_unit_id)
        for item in labels
    ):
        raise ValueError("adjudication raw label belongs to another review unit")
    if labels[0].phase is not labels[1].phase:
        raise ValueError("adjudication raw labels use different phases")
    if labels[0].phase is not assignment.phase or any(
        (item.expert_assignment_id, item.expert_assignment_sha256)
        != (assignment.assignment_id, assignment.assignment_sha256)
        for item in labels
    ):
        raise ValueError("adjudication labels bind another expert assignment")
    adjudicator = next(
        (item for item in assignment.adjudicators if item.expert_id == adjudicator_id),
        None,
    )
    if adjudicator is None:
        raise ValueError("adjudicator is not in the frozen expert assignment")
    key = _require_annotation_key(adjudicator_annotation_key)
    if hashlib.sha256(key).hexdigest() != (
        adjudicator.annotation_key_commitment_sha256
    ):
        raise ValueError(
            "label-adjudication key differs from adjudicator commitment"
        )
    if _timestamp(adjudicated_at) <= max(
        _timestamp(item.submitted_at) for item in labels
    ):
        raise ValueError("adjudication must follow both sealed raw labels")
    refs = tuple(
        MainRawLabelRefV1(
            reviewer_id=item.reviewer_id,
            reviewer_person_commitment_sha256=(
                item.reviewer_person_commitment_sha256
            ),
            label_id=item.label_id,
            label_sha256=item.label_sha256,
        )
        for item in labels
    )
    return _build_signed_identified(
        MainLabelAdjudicationV1,
        id_field="adjudication_id",
        sha_field="adjudication_sha256",
        prefix="main-label-adjudication-v1",
        signature_field="annotation_signature_hmac_sha256",
        signature_schema_version="flatband-main-label-adjudication-signature-v1",
        annotation_key=key,
        values={
            "phase": labels[0].phase,
            "expert_assignment_id": assignment.assignment_id,
            "expert_assignment_sha256": assignment.assignment_sha256,
            "review_unit_id": unit.review_unit_id,
            "review_unit_sha256": unit.review_unit_sha256,
            "pooled_unit_id": unit.pooled_unit_id,
            "raw_labels": refs,
            "adjudicator_id": adjudicator_id,
            "adjudicator_person_commitment_sha256": (
                adjudicator.natural_person_commitment_sha256
            ),
            "adjudicator_annotation_key_commitment_sha256": (
                adjudicator.annotation_key_commitment_sha256
            ),
            "resolution_reason_code": resolution_reason_code,
            "final_status": final_status,
            "final_relevance_grade": final_relevance_grade,
            "final_evidence_gain": final_evidence_gain,
            "adjudicated_at": adjudicated_at,
        },
    )


def _canonical_pooled_clusters(
    *,
    phase: ExecutionPhase,
    case_id: str,
    member_groups: tuple[tuple[str, ...], ...],
) -> tuple[MainPooledDuplicateClusterV1, ...]:
    groups = tuple(
        sorted(tuple(sorted(set(group))) for group in member_groups)
    )
    if not groups or any(not group for group in groups):
        raise ValueError("duplicate partition requires non-empty clusters")
    members = tuple(item for group in groups for item in group)
    if len(members) != len(set(members)):
        raise ValueError("duplicate clusters must be a disjoint partition")
    return tuple(
        MainPooledDuplicateClusterV1(
            cluster_id=deterministic_id(
                "main-pooled-duplicate-cluster-v1",
                {
                    "phase": phase.value,
                    "case_id": case_id,
                    "pooled_unit_ids": group,
                },
            ),
            pooled_unit_ids=group,
        )
        for group in groups
    )


def build_main_raw_duplicate_partition(
    reviewer_manifest: MainReviewerManifestV1,
    private_identity_map: MainPrivateIdentityMapV1,
    *,
    case_id: str,
    case_sha256: str,
    blinded_clusters: tuple[tuple[str, ...], ...],
    annotation_guide_sha256: str,
    submitted_at: str,
    reviewer_annotation_key: bytes,
) -> MainRawDuplicatePartitionV1:
    manifest = _revalidate(reviewer_manifest, MainReviewerManifestV1)
    identity_map = _revalidate(private_identity_map, MainPrivateIdentityMapV1)
    if (
        identity_map.reviewer_manifest_id,
        identity_map.reviewer_manifest_sha256,
    ) != (manifest.manifest_id, manifest.manifest_sha256):
        raise ValueError("duplicate partition map binds another manifest")
    if _timestamp(submitted_at) <= _timestamp(identity_map.sealed_at):
        raise ValueError("raw duplicate partition predates reviewer material seal")
    key = _require_annotation_key(reviewer_annotation_key)
    if hashlib.sha256(key).hexdigest() != (
        identity_map.reviewer_annotation_key_commitment_sha256
    ):
        raise ValueError(
            "raw-duplicate annotation key differs from reviewer commitment"
        )
    expected_entries = tuple(
        item
        for item in identity_map.entries
        if (item.case_id, item.case_sha256) == (case_id, case_sha256)
    )
    expected_blinds = {item.blinded_unit_id for item in expected_entries}
    if not expected_blinds:
        raise ValueError("cannot partition a case with no present review units")
    groups = tuple(
        sorted(tuple(sorted(set(group))) for group in blinded_clusters)
    )
    supplied = tuple(item for group in groups for item in group)
    if len(supplied) != len(set(supplied)) or set(supplied) != expected_blinds:
        raise ValueError("raw duplicate clusters must exactly partition the case pool")
    clusters = tuple(
        MainReviewerDuplicateClusterV1(
            cluster_id=deterministic_id(
                "main-reviewer-dup-cluster-v1",
                {
                    "case_id": case_id,
                    "reviewer_id": identity_map.reviewer_id,
                    "blinded_unit_ids": group,
                },
            ),
            blinded_unit_ids=group,
        )
        for group in groups
    )
    return _build_signed_identified(
        MainRawDuplicatePartitionV1,
        id_field="partition_id",
        sha_field="partition_sha256",
        prefix="main-raw-duplicate-partition-v1",
        signature_field="annotation_signature_hmac_sha256",
        signature_schema_version=(
            "flatband-main-raw-duplicate-partition-signature-v1"
        ),
        annotation_key=key,
        values={
            "phase": identity_map.phase,
            "expert_assignment_id": identity_map.expert_assignment_id,
            "expert_assignment_sha256": identity_map.expert_assignment_sha256,
            "reviewer_manifest_id": manifest.manifest_id,
            "reviewer_manifest_sha256": manifest.manifest_sha256,
            "private_identity_map_id": identity_map.identity_map_id,
            "private_identity_map_sha256": identity_map.identity_map_sha256,
            "reviewer_id": identity_map.reviewer_id,
            "reviewer_person_commitment_sha256": (
                identity_map.reviewer_person_commitment_sha256
            ),
            "reviewer_annotation_key_commitment_sha256": (
                identity_map.reviewer_annotation_key_commitment_sha256
            ),
            "case_id": case_id,
            "case_sha256": case_sha256,
            "annotation_guide_sha256": annotation_guide_sha256,
            "clusters": clusters,
            "submitted_at": submitted_at,
        },
    )


def _translate_raw_duplicate_partition(
    partition: MainRawDuplicatePartitionV1,
    identity_map: MainPrivateIdentityMapV1,
) -> tuple[MainPooledDuplicateClusterV1, ...]:
    value = _revalidate(partition, MainRawDuplicatePartitionV1)
    mapping = _revalidate(identity_map, MainPrivateIdentityMapV1)
    if (
        value.private_identity_map_id,
        value.private_identity_map_sha256,
        value.reviewer_id,
        value.reviewer_person_commitment_sha256,
    ) != (
        mapping.identity_map_id,
        mapping.identity_map_sha256,
        mapping.reviewer_id,
        mapping.reviewer_person_commitment_sha256,
    ):
        raise ValueError("raw duplicate partition binds another private map")
    by_blind = {
        item.blinded_unit_id: item
        for item in mapping.entries
        if (item.case_id, item.case_sha256)
        == (value.case_id, value.case_sha256)
    }
    groups: list[tuple[str, ...]] = []
    for cluster in value.clusters:
        try:
            groups.append(
                tuple(sorted(by_blind[item].pooled_unit_id for item in cluster.blinded_unit_ids))
            )
        except KeyError as exc:
            raise ValueError("duplicate partition contains a foreign blind ID") from exc
    expected = {item.pooled_unit_id for item in by_blind.values()}
    observed = {item for group in groups for item in group}
    if observed != expected:
        raise ValueError("translated duplicate partition does not cover the case pool")
    return _canonical_pooled_clusters(
        phase=value.phase,
        case_id=value.case_id,
        member_groups=tuple(groups),
    )


def build_main_duplicate_adjudication(
    raw_partitions: tuple[
        MainRawDuplicatePartitionV1, MainRawDuplicatePartitionV1
    ],
    private_identity_maps: tuple[
        MainPrivateIdentityMapV1, MainPrivateIdentityMapV1
    ],
    *,
    expert_assignment: MainExpertAssignmentV1,
    adjudicator_id: str,
    final_pooled_clusters: tuple[tuple[str, ...], ...],
    resolution_reason_code: str,
    adjudicated_at: str,
    adjudicator_annotation_key: bytes,
) -> MainDuplicateAdjudicationV1:
    assignment = _revalidate(expert_assignment, MainExpertAssignmentV1)
    partitions = tuple(
        sorted(
            (_revalidate(item, MainRawDuplicatePartitionV1) for item in raw_partitions),
            key=lambda item: item.reviewer_id,
        )
    )
    maps = {
        item.reviewer_id: _revalidate(item, MainPrivateIdentityMapV1)
        for item in private_identity_maps
    }
    if len(maps) != 2 or tuple(item.reviewer_id for item in partitions) != tuple(sorted(maps)):
        raise ValueError("duplicate adjudication requires both reviewer maps")
    if len({(item.phase, item.case_id, item.case_sha256) for item in partitions}) != 1:
        raise ValueError("duplicate adjudication partitions bind different case/phase")
    if any(
        (item.expert_assignment_id, item.expert_assignment_sha256)
        != (assignment.assignment_id, assignment.assignment_sha256)
        for item in partitions
    ):
        raise ValueError("duplicate partitions bind another expert assignment")
    translated = tuple(
        _translate_raw_duplicate_partition(item, maps[item.reviewer_id])
        for item in partitions
    )
    if translated[0] == translated[1]:
        raise ValueError("agreed duplicate partitions must not be adjudicated")
    adjudicator = next(
        (item for item in assignment.adjudicators if item.expert_id == adjudicator_id),
        None,
    )
    if adjudicator is None:
        raise ValueError("duplicate adjudicator is outside frozen assignment")
    key = _require_annotation_key(adjudicator_annotation_key)
    if hashlib.sha256(key).hexdigest() != (
        adjudicator.annotation_key_commitment_sha256
    ):
        raise ValueError(
            "duplicate-adjudication key differs from adjudicator commitment"
        )
    if _timestamp(adjudicated_at) <= max(
        _timestamp(item.submitted_at) for item in partitions
    ):
        raise ValueError("duplicate adjudication must follow both raw partitions")
    phase = partitions[0].phase
    case_id = partitions[0].case_id
    final_clusters = _canonical_pooled_clusters(
        phase=phase,
        case_id=case_id,
        member_groups=final_pooled_clusters,
    )
    expected_universe = {
        item for cluster in translated[0] for item in cluster.pooled_unit_ids
    }
    final_universe = {
        item for cluster in final_clusters for item in cluster.pooled_unit_ids
    }
    if final_universe != expected_universe:
        raise ValueError("duplicate adjudication must partition the exact pool")
    refs = tuple(
        MainDuplicatePartitionRefV1(
            reviewer_id=item.reviewer_id,
            reviewer_person_commitment_sha256=(
                item.reviewer_person_commitment_sha256
            ),
            partition_id=item.partition_id,
            partition_sha256=item.partition_sha256,
        )
        for item in partitions
    )
    return _build_signed_identified(
        MainDuplicateAdjudicationV1,
        id_field="adjudication_id",
        sha_field="adjudication_sha256",
        prefix="main-duplicate-adjudication-v1",
        signature_field="annotation_signature_hmac_sha256",
        signature_schema_version=(
            "flatband-main-duplicate-adjudication-signature-v1"
        ),
        annotation_key=key,
        values={
            "phase": phase,
            "expert_assignment_id": assignment.assignment_id,
            "expert_assignment_sha256": assignment.assignment_sha256,
            "case_id": case_id,
            "case_sha256": partitions[0].case_sha256,
            "raw_partitions": refs,
            "adjudicator_id": adjudicator.expert_id,
            "adjudicator_person_commitment_sha256": (
                adjudicator.natural_person_commitment_sha256
            ),
            "adjudicator_annotation_key_commitment_sha256": (
                adjudicator.annotation_key_commitment_sha256
            ),
            "resolution_reason_code": resolution_reason_code,
            "final_clusters": final_clusters,
            "adjudicated_at": adjudicated_at,
        },
    )


def _validate_phase_trace(phase: ExecutionPhase, trace: ArmExecutionTraceV1) -> None:
    if phase not in _MAIN_PHASES:
        raise ValueError("only Main development/fusion/locked phases are accepted")
    system = trace.system_config.system_id
    if phase is ExecutionPhase.DEVELOPMENT_ABLATIONS:
        if trace.scope is not ArmExecutionScope.DEVELOPMENT or system in {
            ResearchSystemId.FUSION,
            ResearchSystemId.E1_LOCAL,
        }:
            raise ValueError("development ablations require isolated development arms")
    elif phase is ExecutionPhase.DEVELOPMENT_FUSION:
        if trace.scope is not ArmExecutionScope.DEVELOPMENT or system not in {
            ResearchSystemId.B0,
            ResearchSystemId.FUSION,
        }:
            raise ValueError("development Fusion requires development B0/Fusion traces")
    elif trace.scope is not ArmExecutionScope.LOCKED or system not in {
        ResearchSystemId.B0,
        ResearchSystemId.FUSION,
    }:
        raise ValueError("locked primary requires locked B0/Fusion-family traces")


def build_main_gold_release(
    *,
    phase: ExecutionPhase,
    phase_execution_releases: tuple[MainPhaseExecutionReleaseV1, ...],
    expert_assignment: MainExpertAssignmentV1,
    expert_annotation_keys: Mapping[str, bytes],
    ephemeral_blinding_key: bytes,
    renderer_sha256: str,
    blinding_sealed_at: str,
    annotation_guide_sha256: str,
    raw_labels: tuple[MainRawLabelV1, ...],
    adjudications: tuple[MainLabelAdjudicationV1, ...] = (),
    raw_duplicate_partitions: tuple[MainRawDuplicatePartitionV1, ...] = (),
    duplicate_adjudications: tuple[MainDuplicateAdjudicationV1, ...] = (),
    released_at: str,
) -> MainGoldReleaseV1:
    """Derive final positions; no caller-supplied final position is accepted."""

    assignment = _revalidate(expert_assignment, MainExpertAssignmentV1)
    if assignment.phase is not phase:
        raise ValueError("Gold builder receives another-phase expert assignment")
    annotation_keys = _validated_expert_annotation_keys(
        assignment, expert_annotation_keys
    )
    execution_releases, execution_cells, traces = _canonical_gold_execution_inputs(
        phase, phase_execution_releases
    )
    latest_execution = max(
        _timestamp(item.assembled_at) for item in execution_releases
    )
    if _timestamp(blinding_sealed_at) <= latest_execution:
        raise ValueError("reviewer blinding must follow every phase execution release")
    if _timestamp(released_at) <= _timestamp(blinding_sealed_at):
        raise ValueError("Main Gold release must follow reviewer-material sealing")
    manifests, identity_maps = build_main_reviewer_materials(
        phase=phase,
        arm_traces=traces,
        expert_assignment=assignment,
        ephemeral_blinding_key=ephemeral_blinding_key,
        renderer_sha256=renderer_sha256,
        sealed_at=blinding_sealed_at,
    )
    units, _contributions = _review_units_and_contributions(traces)
    unit_by_packet = {
        (item.packet.packet_id, item.packet.packet_sha256): item for item in units
    }
    unit_by_pool = {item.pooled_unit_id: item for item in units}
    map_by_reviewer = {item.reviewer_id: item for item in identity_maps}
    manifest_by_id = {item.manifest_id: item for item in manifests}

    labels = tuple(
        sorted(
            (_revalidate(item, MainRawLabelV1) for item in raw_labels),
            key=lambda item: (item.pooled_unit_id, item.reviewer_id),
        )
    )
    labels_by_pool: dict[str, list[MainRawLabelV1]] = {}
    for label in labels:
        unit = unit_by_pool.get(label.pooled_unit_id)
        if unit is None or (
            label.review_unit_id,
            label.review_unit_sha256,
        ) != (unit.review_unit_id, unit.review_unit_sha256):
            raise ValueError("raw label does not bind an actual review unit")
        if label.phase is not phase or label.annotation_guide_sha256 != annotation_guide_sha256:
            raise ValueError("raw label binds another phase or annotation guide")
        if (
            label.expert_assignment_id,
            label.expert_assignment_sha256,
        ) != (assignment.assignment_id, assignment.assignment_sha256):
            raise ValueError("raw label binds another expert assignment")
        identity_map = map_by_reviewer.get(label.reviewer_id)
        if identity_map is None or (
            label.private_identity_map_id,
            label.private_identity_map_sha256,
        ) != (
            identity_map.identity_map_id,
            identity_map.identity_map_sha256,
        ):
            raise ValueError("raw label does not bind its canonical private map")
        manifest = manifest_by_id.get(label.reviewer_manifest_id)
        if manifest is None or (
            label.reviewer_manifest_sha256,
            manifest.manifest_id,
            manifest.manifest_sha256,
        ) != (
            manifest.manifest_sha256,
            identity_map.reviewer_manifest_id,
            identity_map.reviewer_manifest_sha256,
        ):
            raise ValueError("raw label does not bind its canonical public manifest")
        entry = next(
            (
                item
                for item in identity_map.entries
                if item.blinded_unit_id == label.blinded_unit_id
            ),
            None,
        )
        if entry is None or (
            entry.review_unit_id,
            entry.review_unit_sha256,
            entry.pooled_unit_id,
        ) != (
            label.review_unit_id,
            label.review_unit_sha256,
            label.pooled_unit_id,
        ):
            raise ValueError("raw label blind identity does not exact-join to its packet")
        if (
            label.reviewer_person_commitment_sha256
            != identity_map.reviewer_person_commitment_sha256
        ):
            raise ValueError("raw label natural-person commitment differs from map")
        if label.reviewer_annotation_key_commitment_sha256 != (
            identity_map.reviewer_annotation_key_commitment_sha256
        ):
            raise ValueError("raw label annotation-key commitment differs from map")
        _assert_annotation_signature(
            label,
            annotation_key=annotation_keys[label.reviewer_id],
            key_commitment_sha256=(
                label.reviewer_annotation_key_commitment_sha256
            ),
            id_field="label_id",
            sha_field="label_sha256",
            signature_field="annotation_signature_hmac_sha256",
            signature_schema_version="flatband-main-raw-label-signature-v1",
            signer_label="raw-label reviewer",
        )
        if _timestamp(label.submitted_at) <= _timestamp(blinding_sealed_at):
            raise ValueError("raw label predates reviewer-manifest seal")
        labels_by_pool.setdefault(label.pooled_unit_id, []).append(label)
    if set(labels_by_pool) != set(unit_by_pool):
        raise ValueError("raw labels must exactly cover every present packet")
    if any(
        len(items) != 2 or len({item.reviewer_id for item in items}) != 2
        for items in labels_by_pool.values()
    ):
        raise ValueError("each present packet requires exactly two independent labels")

    decisions = tuple(
        sorted(
            (_revalidate(item, MainLabelAdjudicationV1) for item in adjudications),
            key=lambda item: item.pooled_unit_id,
        )
    )
    if len({item.pooled_unit_id for item in decisions}) != len(decisions):
        raise ValueError("at most one adjudication is permitted per packet")
    decision_by_pool = {item.pooled_unit_id: item for item in decisions}
    final_by_pool: dict[str, tuple[MainGoldStatus, int, int, MainGoldProvenance]] = {}
    for pooled_id, pair in labels_by_pool.items():
        pair = sorted(pair, key=lambda item: item.reviewer_id)
        surfaces = {
            (item.status, item.relevance_grade, item.evidence_gain) for item in pair
        }
        decision = decision_by_pool.get(pooled_id)
        if len(surfaces) == 1:
            if decision is not None:
                raise ValueError("agreed labels must not carry adjudication")
            item = pair[0]
            final_by_pool[pooled_id] = (
                MainGoldStatus(item.status),
                item.relevance_grade,
                item.evidence_gain,
                MainGoldProvenance.AGREED_RAW,
            )
            continue
        if decision is None:
            raise ValueError("every genuine label disagreement requires adjudication")
        unit = unit_by_pool[pooled_id]
        expected_refs = tuple(
            MainRawLabelRefV1(
                reviewer_id=item.reviewer_id,
                reviewer_person_commitment_sha256=(
                    item.reviewer_person_commitment_sha256
                ),
                label_id=item.label_id,
                label_sha256=item.label_sha256,
            )
            for item in pair
        )
        if (
            decision.phase is not phase
            or (
                decision.expert_assignment_id,
                decision.expert_assignment_sha256,
            ) != (assignment.assignment_id, assignment.assignment_sha256)
            or decision.review_unit_id != unit.review_unit_id
            or decision.review_unit_sha256 != unit.review_unit_sha256
            or decision.raw_labels != expected_refs
            or decision.adjudicator_id in {item.reviewer_id for item in pair}
        ):
            raise ValueError("adjudication does not exactly resolve its two raw labels")
        assigned_adjudicator = next(
            (
                item
                for item in assignment.adjudicators
                if item.expert_id == decision.adjudicator_id
            ),
            None,
        )
        if assigned_adjudicator is None or (
            decision.adjudicator_person_commitment_sha256
            != assigned_adjudicator.natural_person_commitment_sha256
        ):
            raise ValueError("adjudication is not bound to an assigned natural person")
        if decision.adjudicator_annotation_key_commitment_sha256 != (
            assigned_adjudicator.annotation_key_commitment_sha256
        ):
            raise ValueError(
                "adjudication annotation-key commitment differs from assignment"
            )
        _assert_annotation_signature(
            decision,
            annotation_key=annotation_keys[decision.adjudicator_id],
            key_commitment_sha256=(
                decision.adjudicator_annotation_key_commitment_sha256
            ),
            id_field="adjudication_id",
            sha_field="adjudication_sha256",
            signature_field="annotation_signature_hmac_sha256",
            signature_schema_version=(
                "flatband-main-label-adjudication-signature-v1"
            ),
            signer_label="label adjudicator",
        )
        if _timestamp(decision.adjudicated_at) <= max(
            _timestamp(item.submitted_at) for item in pair
        ):
            raise ValueError("adjudication does not follow both raw labels")
        final_by_pool[pooled_id] = (
            decision.final_status,
            decision.final_relevance_grade,
            decision.final_evidence_gain,
            MainGoldProvenance.ADJUDICATED,
        )
    if set(decision_by_pool) != {
        key
        for key, pair in labels_by_pool.items()
        if len({(item.status, item.relevance_grade, item.evidence_gain) for item in pair}) > 1
    }:
        raise ValueError("adjudications are missing or orphaned")

    raw_partitions = tuple(
        sorted(
            (
                _revalidate(item, MainRawDuplicatePartitionV1)
                for item in raw_duplicate_partitions
            ),
            key=lambda item: (item.case_id, item.reviewer_id),
        )
    )
    present_cases: dict[tuple[str, str], set[str]] = {}
    for unit in units:
        present_cases.setdefault((unit.case_id, unit.case_sha256), set()).add(
            unit.pooled_unit_id
        )
    expected_partition_keys = {
        (case_id, identity_map.reviewer_id)
        for case_id, _case_sha in present_cases
        for identity_map in identity_maps
    }
    observed_partition_keys = {
        (item.case_id, item.reviewer_id) for item in raw_partitions
    }
    if observed_partition_keys != expected_partition_keys or len(
        observed_partition_keys
    ) != len(raw_partitions):
        raise ValueError("raw duplicate partitions must cover each present case x reviewer")
    translated_by_key: dict[
        tuple[str, str], tuple[MainPooledDuplicateClusterV1, ...]
    ] = {}
    for partition in raw_partitions:
        identity_map = map_by_reviewer.get(partition.reviewer_id)
        if identity_map is None:
            raise ValueError("raw duplicate partition uses a foreign reviewer")
        if (
            partition.phase is not phase
            or partition.annotation_guide_sha256 != annotation_guide_sha256
            or (
                partition.expert_assignment_id,
                partition.expert_assignment_sha256,
            )
            != (assignment.assignment_id, assignment.assignment_sha256)
            or (
                partition.reviewer_person_commitment_sha256
                != identity_map.reviewer_person_commitment_sha256
            )
            or (
                partition.reviewer_annotation_key_commitment_sha256
                != identity_map.reviewer_annotation_key_commitment_sha256
            )
        ):
            raise ValueError("raw duplicate partition binds foreign frozen inputs")
        _assert_annotation_signature(
            partition,
            annotation_key=annotation_keys[partition.reviewer_id],
            key_commitment_sha256=(
                partition.reviewer_annotation_key_commitment_sha256
            ),
            id_field="partition_id",
            sha_field="partition_sha256",
            signature_field="annotation_signature_hmac_sha256",
            signature_schema_version=(
                "flatband-main-raw-duplicate-partition-signature-v1"
            ),
            signer_label="raw-duplicate reviewer",
        )
        if _timestamp(partition.submitted_at) <= _timestamp(blinding_sealed_at):
            raise ValueError("raw duplicate partition predates reviewer material seal")
        translated = _translate_raw_duplicate_partition(partition, identity_map)
        expected_pool = present_cases.get((partition.case_id, partition.case_sha256))
        if expected_pool is None:
            raise ValueError("raw duplicate partition binds a foreign case")
        if {
            item for cluster in translated for item in cluster.pooled_unit_ids
        } != expected_pool:
            raise ValueError("duplicate partition differs from actual case pool")
        translated_by_key[(partition.case_id, partition.reviewer_id)] = translated

    duplicate_decisions = tuple(
        sorted(
            (
                _revalidate(item, MainDuplicateAdjudicationV1)
                for item in duplicate_adjudications
            ),
            key=lambda item: item.case_id,
        )
    )
    if len({item.case_id for item in duplicate_decisions}) != len(
        duplicate_decisions
    ):
        raise ValueError("at most one duplicate adjudication is permitted per case")
    duplicate_decision_by_case = {
        item.case_id: item for item in duplicate_decisions
    }
    human_input_timestamps = tuple(
        item.submitted_at for item in labels
    ) + tuple(
        item.adjudicated_at for item in decisions
    ) + tuple(
        item.submitted_at for item in raw_partitions
    ) + tuple(
        item.adjudicated_at for item in duplicate_decisions
    )
    if human_input_timestamps and _timestamp(released_at) <= max(
        _timestamp(item) for item in human_input_timestamps
    ):
        raise ValueError("Main Gold release must follow every signed human input")
    partitions_by_case: dict[str, list[MainRawDuplicatePartitionV1]] = {}
    for partition in raw_partitions:
        partitions_by_case.setdefault(partition.case_id, []).append(partition)
    final_partitions: list[MainFinalDuplicatePartitionV1] = []
    cluster_by_pool: dict[str, str] = {}
    disputed_cases: set[str] = set()
    for (case_id, case_sha), expected_pool in sorted(present_cases.items()):
        pair = sorted(partitions_by_case[case_id], key=lambda item: item.reviewer_id)
        refs = tuple(
            MainDuplicatePartitionRefV1(
                reviewer_id=item.reviewer_id,
                reviewer_person_commitment_sha256=(
                    item.reviewer_person_commitment_sha256
                ),
                partition_id=item.partition_id,
                partition_sha256=item.partition_sha256,
            )
            for item in pair
        )
        translated = tuple(
            translated_by_key[(case_id, item.reviewer_id)] for item in pair
        )
        decision = duplicate_decision_by_case.get(case_id)
        if translated[0] == translated[1]:
            if decision is not None:
                raise ValueError("agreed duplicate partitions must not be adjudicated")
            clusters = translated[0]
            provenance = MainDuplicateProvenance.AGREED_RAW
            adjudication_ref = None
        else:
            disputed_cases.add(case_id)
            if decision is None:
                raise ValueError("each duplicate-partition disagreement requires adjudication")
            assigned_adjudicator = next(
                (
                    item
                    for item in assignment.adjudicators
                    if item.expert_id == decision.adjudicator_id
                ),
                None,
            )
            if (
                decision.phase is not phase
                or (
                    decision.expert_assignment_id,
                    decision.expert_assignment_sha256,
                )
                != (assignment.assignment_id, assignment.assignment_sha256)
                or (decision.case_id, decision.case_sha256) != (case_id, case_sha)
                or decision.raw_partitions != refs
                or assigned_adjudicator is None
                or decision.adjudicator_person_commitment_sha256
                != assigned_adjudicator.natural_person_commitment_sha256
                or decision.adjudicator_annotation_key_commitment_sha256
                != assigned_adjudicator.annotation_key_commitment_sha256
                or _timestamp(decision.adjudicated_at)
                <= max(_timestamp(item.submitted_at) for item in pair)
            ):
                raise ValueError("duplicate adjudication does not exactly resolve raw partitions")
            _assert_annotation_signature(
                decision,
                annotation_key=annotation_keys[decision.adjudicator_id],
                key_commitment_sha256=(
                    decision.adjudicator_annotation_key_commitment_sha256
                ),
                id_field="adjudication_id",
                sha_field="adjudication_sha256",
                signature_field="annotation_signature_hmac_sha256",
                signature_schema_version=(
                    "flatband-main-duplicate-adjudication-signature-v1"
                ),
                signer_label="duplicate adjudicator",
            )
            clusters = tuple(decision.final_clusters)
            if clusters != _canonical_pooled_clusters(
                phase=phase,
                case_id=case_id,
                member_groups=tuple(item.pooled_unit_ids for item in clusters),
            ):
                raise ValueError("duplicate adjudication final clusters are non-canonical")
            provenance = MainDuplicateProvenance.ADJUDICATED
            adjudication_ref = MainDuplicateAdjudicationRefV1(
                adjudication_id=decision.adjudication_id,
                adjudication_sha256=decision.adjudication_sha256,
                adjudicator_id=decision.adjudicator_id,
                adjudicator_person_commitment_sha256=(
                    decision.adjudicator_person_commitment_sha256
                ),
            )
        cluster_universe = {
            item for cluster in clusters for item in cluster.pooled_unit_ids
        }
        if cluster_universe != expected_pool:
            raise ValueError("final duplicate partition differs from actual case pool")
        for cluster in clusters:
            for pooled_id in cluster.pooled_unit_ids:
                if pooled_id in cluster_by_pool:
                    raise ValueError("pooled unit appears in multiple final partitions")
                cluster_by_pool[pooled_id] = cluster.cluster_id
        final_partitions.append(
            _build_identified(
                MainFinalDuplicatePartitionV1,
                id_field="partition_id",
                sha_field="partition_sha256",
                prefix="main-final-dup-partition-v1",
                values={
                    "phase": phase,
                    "case_id": case_id,
                    "case_sha256": case_sha,
                    "raw_partitions": refs,
                    "provenance": provenance,
                    "adjudication": adjudication_ref,
                    "clusters": clusters,
                    "finalized_at": released_at,
                },
            )
        )
    if set(duplicate_decision_by_case) != disputed_cases:
        raise ValueError("duplicate adjudications are missing or orphaned")
    if set(cluster_by_pool) != set(unit_by_pool):
        raise ValueError("final duplicate clusters must cover every present packet")

    execution_release_refs = tuple(
        MainPhaseExecutionRefV1(
            release_id=item.release_id,
            release_sha256=item.release_sha256,
            source_execution_phase=item.source_execution_phase,
        )
        for item in execution_releases
    )
    execution_cell_refs: list[MainExecutionCellRefV1] = []
    trace_refs: list[MainArmTraceRefV1] = []
    projections: list[MainGoldPositionProjectionV1] = []
    outer_release_by_inner = {
        (
            item.execution_release.release_id,
            item.execution_release.release_sha256,
        ): item
        for item in execution_releases
    }
    for cell in execution_cells:
        terminal = cell.terminal_result
        top5 = cell.top5_projection
        trace = cell.arm_trace
        outer_release = outer_release_by_inner.get(
            (
                cell.source_execution_release_id,
                cell.source_execution_release_sha256,
            )
        )
        if outer_release is None:
            raise ValueError("Gold execution cell lacks its outer Main phase release")
        execution_cell_refs.append(
            MainExecutionCellRefV1(
                execution_release_id=outer_release.release_id,
                execution_release_sha256=outer_release.release_sha256,
                cell_evidence_id=cell.cell_evidence_id,
                cell_evidence_sha256=cell.cell_evidence_sha256,
                source_execution_phase=cell.source_execution_phase,
                analysis_role=cell.analysis_role,
                system_id=cell.system_config.system_id,
                terminal_result_id=terminal.terminal_result_id,
                terminal_result_sha256=terminal.terminal_result_sha256,
                top5_projection_id=top5.projection_id,
                top5_projection_sha256=top5.projection_sha256,
                trace_id=None if trace is None else trace.trace_id,
                trace_sha256=None if trace is None else trace.trace_sha256,
                cell_id=terminal.cell_id,
                run_id=terminal.run_id,
                case_id=terminal.case_id,
                case_sha256=terminal.case_sha256,
            )
        )
        if trace is not None:
            ranking = trace.ranking
            trace_refs.append(
                MainArmTraceRefV1(
                    trace_id=trace.trace_id,
                    trace_sha256=trace.trace_sha256,
                    system_id=trace.system_config.system_id,
                    ranking_id=ranking.ranking_id,
                    ranking_sha256=ranking.ranking_sha256,
                    cell_id=ranking.cell_id,
                    run_id=ranking.run_id,
                    case_id=ranking.case_id,
                    case_sha256=ranking.case_sha256,
                )
            )
            position_by_rank = {
                item.selection_rank: item for item in ranking.positions
            }
        else:
            position_by_rank = {}
        for rank in range(1, 6):
            position = position_by_rank.get(rank)
            if position is None:
                top5_position = top5.positions[rank - 1]
                if top5_position.packet_id is not None:
                    raise ValueError("Gold execution cell omits a returned packet trace")
                reason = top5_position.missing_reason
                if reason is None:
                    raise ValueError("Gold absent position lacks a frozen reason")
                reasons = (reason.value,)
                values: dict[str, object] = {
                    "phase": phase,
                    "execution_cell_evidence_id": cell.cell_evidence_id,
                    "execution_cell_evidence_sha256": cell.cell_evidence_sha256,
                    "trace_id": None if trace is None else trace.trace_id,
                    "trace_sha256": None if trace is None else trace.trace_sha256,
                    "cell_id": terminal.cell_id,
                    "run_id": terminal.run_id,
                    "case_id": terminal.case_id,
                    "case_sha256": terminal.case_sha256,
                    "system_id": cell.system_config.system_id,
                    "selection_rank": rank,
                    "status": MainGoldStatus.SYSTEM_PACKET_INVALID,
                    "relevance_grade": 0,
                    "evidence_gain": 0,
                    "final_duplicate_cluster_id": deterministic_id(
                        "main-missing-position-cluster-v1",
                        {
                            "execution_cell_evidence_sha256": cell.cell_evidence_sha256,
                            "selection_rank": rank,
                            "absence_reason_codes": reasons,
                        },
                    ),
                    "provenance": MainGoldProvenance.SYSTEM_DERIVED_ABSENCE,
                    "absence_reason_codes": reasons,
                }
            else:
                projected = top5.positions[rank - 1]
                if (projected.packet_id, projected.packet_sha256) != (
                    position.packet_id,
                    position.packet_sha256,
                ):
                    raise ValueError("Gold trace ranking differs from exact Top-5")
                unit = unit_by_packet[(position.packet_id, position.packet_sha256)]
                status, grade, evidence, provenance = final_by_pool[unit.pooled_unit_id]
                values = {
                    "phase": phase,
                    "execution_cell_evidence_id": cell.cell_evidence_id,
                    "execution_cell_evidence_sha256": cell.cell_evidence_sha256,
                    "trace_id": trace.trace_id,
                    "trace_sha256": trace.trace_sha256,
                    "cell_id": terminal.cell_id,
                    "run_id": terminal.run_id,
                    "case_id": terminal.case_id,
                    "case_sha256": terminal.case_sha256,
                    "system_id": cell.system_config.system_id,
                    "selection_rank": rank,
                    "pooled_unit_id": unit.pooled_unit_id,
                    "packet_id": unit.packet.packet_id,
                    "packet_sha256": unit.packet.packet_sha256,
                    "status": status,
                    "relevance_grade": grade,
                    "evidence_gain": evidence,
                    "final_duplicate_cluster_id": cluster_by_pool[
                        unit.pooled_unit_id
                    ],
                    "provenance": provenance,
                }
            projections.append(
                _build_identified(
                    MainGoldPositionProjectionV1,
                    id_field="projection_id",
                    sha_field="projection_sha256",
                    prefix="main-gold-position-v1",
                    values=values,
                )
            )
    return _build_identified(
        MainGoldReleaseV1,
        id_field="release_id",
        sha_field="release_sha256",
        prefix="main-gold-release-v1",
        values={
            "phase": phase,
            "annotation_guide_sha256": annotation_guide_sha256,
            "blinding_commitment_sha256": identity_maps[0].blinding_commitment_sha256,
            "expert_assignment": assignment,
            "phase_execution_releases": execution_release_refs,
            "execution_cells": tuple(execution_cell_refs),
            "arm_traces": tuple(trace_refs),
            "review_units": units,
            "reviewer_manifests": manifests,
            "private_identity_maps": identity_maps,
            "raw_labels": labels,
            "adjudications": decisions,
            "raw_duplicate_partitions": raw_partitions,
            "duplicate_adjudications": duplicate_decisions,
            "final_duplicate_partitions": tuple(final_partitions),
            "position_projections": tuple(
                sorted(
                    projections,
                    key=lambda item: (
                        item.case_id,
                        item.system_id.value,
                        item.cell_id,
                        item.selection_rank,
                    ),
                )
            ),
            "released_at": released_at,
        },
    )


def assert_main_gold_release_exact(
    release: MainGoldReleaseV1,
    *,
    phase_execution_releases: tuple[MainPhaseExecutionReleaseV1, ...],
    expert_annotation_keys: Mapping[str, bytes],
    ephemeral_blinding_key: bytes,
) -> None:
    value = _revalidate(release, MainGoldReleaseV1)
    key = _require_blinding_key(ephemeral_blinding_key)
    if hashlib.sha256(key).hexdigest() != value.blinding_commitment_sha256:
        raise ValueError("ephemeral blinding key differs from release commitment")
    renderer_values = {item.renderer_sha256 for item in value.reviewer_manifests}
    seal_values = {item.sealed_at for item in value.reviewer_manifests}
    if len(renderer_values) != 1 or len(seal_values) != 1:
        raise ValueError("reviewer manifests do not share one renderer/seal")
    rebuilt = build_main_gold_release(
        phase=value.phase,
        phase_execution_releases=phase_execution_releases,
        expert_assignment=value.expert_assignment,
        expert_annotation_keys=expert_annotation_keys,
        ephemeral_blinding_key=key,
        renderer_sha256=next(iter(renderer_values)),
        blinding_sealed_at=next(iter(seal_values)),
        annotation_guide_sha256=value.annotation_guide_sha256,
        raw_labels=value.raw_labels,
        adjudications=value.adjudications,
        raw_duplicate_partitions=value.raw_duplicate_partitions,
        duplicate_adjudications=value.duplicate_adjudications,
        released_at=value.released_at,
    )
    if rebuilt != value:
        raise ValueError("Main Gold release differs from exact canonical replay")


def build_main_gold_formal_verifier_attestation(
    release: MainGoldReleaseV1,
    *,
    phase_execution_releases: tuple[MainPhaseExecutionReleaseV1, ...],
    expert_annotation_keys: Mapping[str, bytes],
    ephemeral_blinding_key: bytes,
    verified_at: str,
) -> MainGoldFormalVerifierAttestationV1:
    value = _revalidate(release, MainGoldReleaseV1)
    assert_main_gold_release_exact(
        value,
        phase_execution_releases=phase_execution_releases,
        expert_annotation_keys=expert_annotation_keys,
        ephemeral_blinding_key=ephemeral_blinding_key,
    )
    if _timestamp(verified_at) <= _timestamp(value.released_at):
        raise ValueError("formal verification must follow Gold release")
    return _build_identified(
        MainGoldFormalVerifierAttestationV1,
        id_field="attestation_id",
        sha_field="attestation_sha256",
        prefix="main-gold-verifier-v1",
        values={
            "gold_release_id": value.release_id,
            "gold_release_sha256": value.release_sha256,
            "phase": value.phase,
            "blinding_commitment_sha256": value.blinding_commitment_sha256,
            "phase_execution_releases": value.phase_execution_releases,
            "execution_cells": value.execution_cells,
            "arm_traces": value.arm_traces,
            "verified_at": verified_at,
        },
    )


def build_analysis_judgments_from_main_gold_v1(
    release: MainGoldReleaseV1,
    *,
    trace_evidence: object,
) -> tuple[FinalPositionJudgmentV2, ...]:
    """Canonical present-position Gold -> Analysis V2 adapter.

    ``trace_evidence`` is validated as ``AnalysisTraceEvidenceV2`` locally to
    avoid a module-level dependency cycle.  Analysis V2 itself derives missing
    fixed-zero judgments from the terminal/top-5 preimage; this adapter returns
    only present judgments, whose duplicate clusters come only from Main Gold.
    """

    from material_agent.research.flatband_analysis_v2 import (
        AnalysisTraceEvidenceV2,
        FinalJudgmentStatusV2,
        build_final_position_judgment_v2,
    )

    value = _revalidate(release, MainGoldReleaseV1)
    evidence = AnalysisTraceEvidenceV2.model_validate(trace_evidence)
    terminal = evidence.terminal_result
    top5 = evidence.top5_projection
    matching_refs = tuple(
        item
        for item in value.execution_cells
        if (
            item.cell_id,
            item.run_id,
            item.case_id,
            item.case_sha256,
            item.system_id,
            item.analysis_role,
        )
        == (
            terminal.cell_id,
            terminal.run_id,
            terminal.case_id,
            terminal.case_sha256,
            evidence.system_config.system_id,
            evidence.role,
        )
    )
    if len(matching_refs) != 1:
        raise ValueError("Analysis trace evidence does not identify one Gold execution cell")
    cell_ref = matching_refs[0]
    if (
        terminal.terminal_result_id,
        terminal.terminal_result_sha256,
        top5.projection_id,
        top5.projection_sha256,
    ) != (
        cell_ref.terminal_result_id,
        cell_ref.terminal_result_sha256,
        cell_ref.top5_projection_id,
        cell_ref.top5_projection_sha256,
    ):
        raise ValueError("Analysis terminal/Top-5 differs from Main Gold execution cell")
    if (terminal.ranking_id, terminal.ranking_sha256) != (
        None if cell_ref.trace_id is None else terminal.ranking_id,
        None if cell_ref.trace_sha256 is None else terminal.ranking_sha256,
    ):
        # A FAILED cell has no ranking identity; a present trace necessarily
        # binds the terminal ranking through MainExecution exact replay.
        raise ValueError("Analysis terminal ranking presence differs from Gold cell")
    if evidence.trace is None:
        if cell_ref.trace_id is not None or terminal.status is not RunCellStatus.FAILED:
            raise ValueError("Analysis failed trace shape differs from Main Gold cell")
    elif (
        evidence.trace.trace_id,
        evidence.trace.trace_sha256,
    ) != (cell_ref.trace_id, cell_ref.trace_sha256):
        raise ValueError("Analysis trace preimage differs from Main Gold execution cell")
    if terminal.ranking_id is not None and (
        terminal.ranking_id,
        terminal.ranking_sha256,
    ) != (
        evidence.trace.ranking.ranking_id,
        evidence.trace.ranking.ranking_sha256,
    ):
        raise ValueError("Analysis terminal ranking differs from Main Gold trace")
    projections = tuple(
        sorted(
            (
                item
                for item in value.position_projections
                if item.execution_cell_evidence_id == cell_ref.cell_evidence_id
            ),
            key=lambda item: item.selection_rank,
        )
    )
    if tuple(item.selection_rank for item in projections) != (1, 2, 3, 4, 5):
        raise ValueError("Main Gold cell does not have exactly five positions")
    judgments: list[FinalPositionJudgmentV2] = []
    for gold, projected in zip(projections, top5.positions):
        if projected.position != gold.selection_rank:
            raise ValueError("Main Gold and Analysis Top-5 positions are misaligned")
        if projected.packet_id is None:
            if gold.packet_id is not None or gold.packet_sha256 is not None:
                raise ValueError("Analysis marks an actual Gold packet as missing")
            reason = projected.missing_reason
            if reason is None:
                raise ValueError("Analysis missing position lacks a reason")
            if (
                gold.status is not MainGoldStatus.SYSTEM_PACKET_INVALID
                or gold.relevance_grade != 0
                or gold.evidence_gain != 0
            ):
                raise ValueError("Main Gold missing position is not a fixed zero")
            continue
        else:
            if (gold.packet_id, gold.packet_sha256) != (
                projected.packet_id,
                projected.packet_sha256,
            ):
                raise ValueError("Analysis Top-5 packet differs from Main Gold")
            duplicate_cluster_id = gold.final_duplicate_cluster_id
        judgments.append(
            build_final_position_judgment_v2(
                position=gold.selection_rank,
                packet_id=gold.packet_id,
                packet_sha256=gold.packet_sha256,
                status=FinalJudgmentStatusV2(gold.status.value),
                relevance_grade=gold.relevance_grade,
                evidence_valid=bool(gold.evidence_gain),
                final_duplicate_cluster_id=duplicate_cluster_id,
                missing_reason=None,
            )
        )
    return tuple(judgments)


def build_analysis_cell_from_main_gold_v1(
    release: MainGoldReleaseV1,
    formal_verifier: MainGoldFormalVerifierAttestationV1,
    *,
    trace_evidence: object,
) -> AnalysisCellEvidenceV2:
    """Build one Analysis V2 cell with exact Gold/verifier artifact refs."""

    from material_agent.research.flatband_analysis_v2 import (
        AnalysisArtifactRefV2,
        AnalysisArtifactTypeV2,
        AnalysisTraceEvidenceV2,
        build_analysis_cell_evidence_v2,
    )

    value = _revalidate(release, MainGoldReleaseV1)
    verifier = _revalidate(
        formal_verifier, MainGoldFormalVerifierAttestationV1
    )
    if (
        verifier.gold_release_id,
        verifier.gold_release_sha256,
        verifier.phase,
        verifier.blinding_commitment_sha256,
    ) != (
        value.release_id,
        value.release_sha256,
        value.phase,
        value.blinding_commitment_sha256,
    ):
        raise ValueError("formal verifier attests another Main Gold release")
    if (
        verifier.phase_execution_releases,
        verifier.execution_cells,
        verifier.arm_traces,
    ) != (
        value.phase_execution_releases,
        value.execution_cells,
        value.arm_traces,
    ):
        raise ValueError("formal verifier execution closure differs from Main Gold")
    evidence = AnalysisTraceEvidenceV2.model_validate(trace_evidence)
    present = build_analysis_judgments_from_main_gold_v1(
        value, trace_evidence=evidence
    )
    return build_analysis_cell_evidence_v2(
        trace_evidence=evidence,
        gold_release_ref=AnalysisArtifactRefV2(
            artifact_type=AnalysisArtifactTypeV2.GOLD_RELEASE,
            artifact_id=value.release_id,
            artifact_sha256=value.release_sha256,
        ),
        gold_formal_verifier_ref=AnalysisArtifactRefV2(
            artifact_type=AnalysisArtifactTypeV2.GOLD_FORMAL_VERIFIER,
            artifact_id=verifier.attestation_id,
            artifact_sha256=verifier.attestation_sha256,
        ),
        judgments=present,
    )


__all__ = [
    "MainArmTraceRefV1",
    "MainDuplicateAdjudicationRefV1",
    "MainDuplicateAdjudicationV1",
    "MainDuplicatePartitionRefV1",
    "MainDuplicateProvenance",
    "MainExecutionCellRefV1",
    "MainExpertAssignmentV1",
    "MainExpertIdentityCommitmentV1",
    "MainExpertRegistryRefV1",
    "MainFinalDuplicatePartitionV1",
    "MainGoldFormalVerifierAttestationV1",
    "MainGoldPositionProjectionV1",
    "MainGoldProvenance",
    "MainGoldReleaseV1",
    "MainGoldStatus",
    "MainLabelAdjudicationV1",
    "MainPhaseExecutionRefV1",
    "MainPooledDuplicateClusterV1",
    "MainPositionContributionV1",
    "MainPrivateIdentityMapV1",
    "MainPrivateUnitMapEntryV1",
    "MainRawDuplicatePartitionV1",
    "MainRawLabelRefV1",
    "MainRawLabelV1",
    "MainReviewUnitV1",
    "MainReviewerDuplicateClusterV1",
    "MainReviewerManifestV1",
    "MainReviewerPacketV1",
    "assert_main_gold_release_exact",
    "build_analysis_cell_from_main_gold_v1",
    "build_analysis_judgments_from_main_gold_v1",
    "build_main_duplicate_adjudication",
    "build_main_expert_assignment",
    "build_main_gold_formal_verifier_attestation",
    "build_main_gold_release",
    "build_main_label_adjudication",
    "build_main_raw_duplicate_partition",
    "build_main_raw_label",
    "build_main_review_unit",
    "build_main_reviewer_materials",
]
