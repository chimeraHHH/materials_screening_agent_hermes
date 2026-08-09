"""Reviewer-safe, identity-masked projection for the flat-band benchmark.

The reviewer artifact and the private identity map are deliberately different
schemas.  Reviewer artifacts contain bounded evidence text and scientific
proposal fields, but no execution identity.  The private map is the only place
where reviewer-specific blind IDs are joined to execution positions.

This is identity masking, not a claim of perfect blinding: writing style or
scientific content can still make an origin guess possible.  Such guesses are
recorded only after labels are sealed and never enter the grade.
"""

from __future__ import annotations

import hashlib
import hmac
import re
from datetime import datetime
from enum import StrEnum
from typing import Annotated, Any, Literal, TypeVar

from pydantic import Field, field_validator, model_validator

from material_agent.inspiration.models import (
    Identifier,
    LongText,
    Sha256,
    ShortText,
    StrictModel,
    canonical_json_bytes,
    canonical_sha256,
    deterministic_id,
)
from material_agent.research.flatband_contracts import (
    AssertedEvidenceRelation,
    Dimensionality,
    FalsificationPlanV1,
    FlatBandBenchmarkCaseV1,
    HypothesisPacketV1,
    MechanismFamily,
    TargetBandClass,
)
from material_agent.research.flatband_cases import (
    FrozenCaseCandidateV1,
    FrozenCaseReleaseV3,
    PreRunEligibilityReleaseV1,
    PreRunEligibilityReleaseV2,
    PreRunEligibilityReleaseV3,
    assert_pre_run_eligibility_precedes_execution,
    assert_pre_run_eligibility_precedes_execution_v2,
)
from material_agent.research.flatband_execution import (
    EvidenceLinkReceiptV1,
    ExecutionReleaseV1,
    ExecutionReleaseV2,
    ExecutionReleaseV3,
    MetadataRecordReceiptV1,
    MetadataSpanPreimageV1,
    NormalizedMetadataArtifactV1,
    ResearchSystemId,
    RunCellStatus,
)
from material_agent.research.flatband_experts import (
    ExpertStudyRegistryV1,
    ExpertStudyRegistryV2,
    assert_expert_registry_covers_split,
    assert_expert_registry_covers_split_v2,
)


MAX_REVIEWER_EXCERPT_CHARS = 1_200
MASKING_STATEMENT = "IDENTITY_MASKED_NOT_PERFECT_BLINDING"


class EvidenceSpanScope(StrEnum):
    TITLE = "TITLE"
    ABSTRACT = "ABSTRACT"
    KEYWORDS = "KEYWORDS"
    METADATA_DESCRIPTION = "METADATA_DESCRIPTION"
    DATASET_METADATA = "DATASET_METADATA"


class EvidenceSpanType(StrEnum):
    VERBATIM_EXCERPT = "VERBATIM_EXCERPT"
    SOURCE_AUTHORED_METADATA = "SOURCE_AUTHORED_METADATA"


class EvidenceAccessPolicy(StrEnum):
    PUBLIC_SOURCE = "PUBLIC_SOURCE"
    AUTHORIZED_REVIEWER_ONLY = "AUTHORIZED_REVIEWER_ONLY"


class EvidenceRedistributionPolicy(StrEnum):
    PUBLIC_RELEASE_ALLOWED = "PUBLIC_RELEASE_ALLOWED"
    REVIEWER_PACKET_ONLY_NO_REDISTRIBUTION = (
        "REVIEWER_PACKET_ONLY_NO_REDISTRIBUTION"
    )


def _require_timestamp(value: str) -> str:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("timestamp must be RFC3339-compatible") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("timestamp must include a UTC offset")
    return value


def _text_sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _hmac_id(prefix: str, key: bytes, payload: object) -> str:
    digest = hmac.new(
        key,
        canonical_json_bytes({"domain": prefix, "payload": payload}),
        hashlib.sha256,
    ).hexdigest()
    return f"{prefix}-{digest[:24]}"


ModelT = TypeVar("ModelT", bound=StrictModel)


def _revalidate(value: ModelT, model_type: type[ModelT]) -> ModelT:
    return model_type.model_validate(
        value.model_dump(mode="python", round_trip=True)
    )


def _identified(
    model_type: type[ModelT],
    *,
    id_field: str,
    sha_field: str,
    prefix: str,
    values: dict[str, object],
) -> ModelT:
    draft = model_type.model_construct(**values)
    semantic = draft.model_dump(mode="python", exclude={id_field, sha_field})
    digest = canonical_sha256(semantic)
    return model_type.model_validate(
        {
            **values,
            id_field: deterministic_id(prefix, {sha_field: digest}),
            sha_field: digest,
        }
    )


class EvidenceExcerptV1(StrictModel):
    """Private builder input produced by an authorized bounded-span resolver."""

    packet_id: Identifier
    evidence_link_id: Identifier
    source_span_text: Annotated[str, Field(min_length=1, max_length=8_000)]
    source_span_char_count: Annotated[int, Field(ge=1, le=8_000)]
    source_span_sha256: Sha256
    excerpt_start_offset: Annotated[int, Field(ge=0, le=7_999)]
    excerpt_end_offset: Annotated[int, Field(ge=1, le=8_000)]
    span_scope: EvidenceSpanScope
    span_type: EvidenceSpanType
    normalized_work_citation: ShortText
    excerpt: Annotated[
        str, Field(min_length=1, max_length=MAX_REVIEWER_EXCERPT_CHARS)
    ]
    excerpt_char_count: Annotated[
        int, Field(ge=1, le=MAX_REVIEWER_EXCERPT_CHARS)
    ]
    excerpt_sha256: Sha256
    excerpt_truncated: bool
    access_policy: EvidenceAccessPolicy
    redistribution_policy: EvidenceRedistributionPolicy

    @model_validator(mode="after")
    def validate_excerpt(self) -> "EvidenceExcerptV1":
        if self.source_span_char_count != len(self.source_span_text):
            raise ValueError("source span char count does not match source text")
        if self.source_span_sha256 != _text_sha256(self.source_span_text):
            raise ValueError("source span SHA-256 does not match source text")
        if not (
            self.excerpt_start_offset
            < self.excerpt_end_offset
            <= self.source_span_char_count
        ):
            raise ValueError("excerpt offsets fall outside the bounded source span")
        if self.excerpt != self.source_span_text[
            self.excerpt_start_offset : self.excerpt_end_offset
        ]:
            raise ValueError("excerpt is not the declared source-span slice")
        if self.excerpt_char_count != len(self.excerpt):
            raise ValueError("excerpt char count does not match displayed text")
        if self.excerpt_sha256 != _text_sha256(self.excerpt):
            raise ValueError("excerpt SHA-256 does not match displayed text")
        return self


class EvidenceExcerptV2(EvidenceExcerptV1):
    """Position-bound reviewer excerpt backed by the exact execution preimage."""

    schema_version: Literal["flatband-evidence-excerpt-v2"] = (
        "flatband-evidence-excerpt-v2"
    )
    cell_id: Identifier
    evidence_receipt_id: Identifier
    evidence_receipt_sha256: Sha256
    record_receipt_id: Identifier
    record_receipt_sha256: Sha256
    normalized_metadata_artifact_id: Identifier
    normalized_metadata_artifact_sha256: Sha256
    field_artifact_id: Identifier
    field_artifact_sha256: Sha256
    span_preimage_id: Identifier
    span_preimage_sha256: Sha256
    span_field: ShortText
    metadata_json_path: ShortText
    span_start_byte: Annotated[int, Field(ge=0, le=65_535)]
    span_end_byte: Annotated[int, Field(ge=1, le=65_536)]
    span_locator_sha256: Sha256

    @model_validator(mode="after")
    def validate_v2_preimage_shape(self) -> "EvidenceExcerptV2":
        encoded = self.source_span_text.encode("utf-8")
        if self.span_end_byte - self.span_start_byte != len(encoded):
            raise ValueError("V2 excerpt byte offsets do not match its source span")
        return self


class ReviewerEvidenceSpanV1(StrictModel):
    """Bounded evidence visible to a reviewer, with source identity removed."""

    reviewer_evidence_id: Identifier
    content_sha256: Sha256
    span_scope: EvidenceSpanScope
    span_type: EvidenceSpanType
    asserted_relation: AssertedEvidenceRelation
    claim_summary: ShortText
    normalized_work_citation: ShortText
    excerpt: Annotated[
        str, Field(min_length=1, max_length=MAX_REVIEWER_EXCERPT_CHARS)
    ]
    excerpt_char_count: Annotated[
        int, Field(ge=1, le=MAX_REVIEWER_EXCERPT_CHARS)
    ]
    excerpt_truncated: bool
    access_policy: EvidenceAccessPolicy
    redistribution_policy: EvidenceRedistributionPolicy

    @model_validator(mode="after")
    def validate_content(self) -> "ReviewerEvidenceSpanV1":
        if self.excerpt_char_count != len(self.excerpt):
            raise ValueError("reviewer excerpt char count does not match text")
        semantic = self.model_dump(mode="python", exclude={"content_sha256"})
        if self.content_sha256 != canonical_sha256(semantic):
            raise ValueError("reviewer evidence content SHA-256 does not match")
        return self


class ReviewerCaseProjectionV1(StrictModel):
    """Reviewer-safe scientific case context, without source lookup identity.

    Formula is retained because chemical plausibility and transformation
    constraints cannot be judged without composition.  ``parent_label`` is
    intentionally withheld because it commonly contains a database accession
    or another directly searchable source identifier.
    """

    schema_version: Literal["flatband-reviewer-case-projection-v1"] = (
        "flatband-reviewer-case-projection-v1"
    )
    reviewer_case_projection_id: Identifier
    reviewer_case_projection_sha256: Sha256
    blinded_case_id: Identifier
    case_display_order: Annotated[int, Field(ge=1, le=120)]
    parent_label_withheld: Literal[True] = True
    formula: Annotated[str, Field(min_length=1, max_length=128)]
    frozen_request: LongText
    target_class: TargetBandClass
    target_fermi_distance_max_e_v: Literal[1.0] = 1.0
    dimensionality: Dimensionality
    hard_constraints: Annotated[
        tuple[ShortText, ...], Field(min_length=1, max_length=64)
    ]
    soft_preferences: Annotated[tuple[ShortText, ...], Field(max_length=64)] = ()
    forbidden_transformations: Annotated[
        tuple[Identifier, ...], Field(min_length=1, max_length=64)
    ]
    case_mechanism_stratum: MechanismFamily
    scientific_conclusion: Literal[False] = False

    @model_validator(mode="after")
    def validate_projection(self) -> "ReviewerCaseProjectionV1":
        for values, label in (
            (self.hard_constraints, "hard constraints"),
            (self.soft_preferences, "soft preferences"),
            (self.forbidden_transformations, "forbidden transformations"),
        ):
            if values != tuple(sorted(set(values))):
                raise ValueError(f"reviewer {label} must be sorted and unique")
        semantic = self.model_dump(
            mode="python", exclude={"reviewer_case_projection_sha256"}
        )
        if self.reviewer_case_projection_sha256 != canonical_sha256(semantic):
            raise ValueError("reviewer case projection SHA-256 does not match")
        return self


class ReviewerCaseProjectionV2(ReviewerCaseProjectionV1):
    schema_version: Literal["flatband-reviewer-case-projection-v2"] = (
        "flatband-reviewer-case-projection-v2"
    )


class ReviewerHypothesisPacketV1(StrictModel):
    """Physical reviewer projection; execution and proposed-group fields do not exist."""

    schema_version: Literal["flatband-reviewer-hypothesis-packet-v1"] = (
        "flatband-reviewer-hypothesis-packet-v1"
    )
    reviewer_packet_id: Identifier
    reviewer_packet_sha256: Sha256
    blinded_unit_id: Identifier
    blinded_case_id: Identifier
    reviewer_case_projection_id: Identifier
    display_order: Annotated[int, Field(ge=1, le=100_000)]
    candidate_structure_sha256: Sha256
    transformation_operator_id: Identifier
    transformation_summary: LongText
    source_domain: ShortText
    mechanism_family: MechanismFamily
    source_mechanism: LongText
    shared_invariant: LongText
    target_mapping: LongText
    transferable_control: LongText
    transfer_principle: LongText
    required_conditions: Annotated[
        tuple[ShortText, ...], Field(min_length=1, max_length=32)
    ]
    breaking_conditions: Annotated[
        tuple[ShortText, ...], Field(min_length=1, max_length=32)
    ]
    contradictions: Annotated[tuple[ShortText, ...], Field(max_length=32)] = ()
    evidence: Annotated[
        tuple[ReviewerEvidenceSpanV1, ...], Field(min_length=1, max_length=64)
    ]
    falsification: FalsificationPlanV1
    claim_type: Literal["HYPOTHESIS"] = "HYPOTHESIS"
    validated_material: Literal[False] = False
    scientific_conclusion: Literal[False] = False

    @model_validator(mode="after")
    def validate_packet(self) -> "ReviewerHypothesisPacketV1":
        evidence_ids = tuple(item.reviewer_evidence_id for item in self.evidence)
        if len(evidence_ids) != len(set(evidence_ids)):
            raise ValueError("reviewer evidence IDs must be unique within a packet")
        semantic = self.model_dump(
            mode="python", exclude={"reviewer_packet_sha256"}
        )
        if self.reviewer_packet_sha256 != canonical_sha256(semantic):
            raise ValueError("reviewer packet SHA-256 does not match safe content")
        return self


class ReviewerHypothesisPacketV2(ReviewerHypothesisPacketV1):
    schema_version: Literal["flatband-reviewer-hypothesis-packet-v2"] = (
        "flatband-reviewer-hypothesis-packet-v2"
    )


class PrivateEvidenceMapEntryV1(StrictModel):
    reviewer_evidence_id: Identifier
    evidence_link_id: Identifier
    source_id: Identifier
    source_record_id: Identifier
    source_url: Annotated[str, Field(min_length=9, max_length=1_024)]
    span_id: Identifier
    source_span_sha256: Sha256
    private_text_artifact_uri: Annotated[
        str, Field(min_length=12, max_length=512)
    ] | None = None


class PrivateEvidenceMapEntryV2(PrivateEvidenceMapEntryV1):
    schema_version: Literal["flatband-private-evidence-map-entry-v2"] = (
        "flatband-private-evidence-map-entry-v2"
    )
    evidence_receipt_id: Identifier
    evidence_receipt_sha256: Sha256
    record_receipt_id: Identifier
    record_receipt_sha256: Sha256
    normalized_metadata_artifact_id: Identifier
    normalized_metadata_artifact_sha256: Sha256
    field_artifact_id: Identifier
    field_artifact_sha256: Sha256
    span_preimage_id: Identifier
    span_preimage_sha256: Sha256
    span_field: ShortText
    metadata_json_path: ShortText
    span_start_byte: Annotated[int, Field(ge=0, le=65_535)]
    span_end_byte: Annotated[int, Field(ge=1, le=65_536)]
    span_locator_sha256: Sha256


class PrivatePositionMapEntryV1(StrictModel):
    """One private row for one real, non-forced-zero ranking position."""

    pooled_unit_id: Identifier
    reviewer_packet_id: Identifier
    reviewer_packet_sha256: Sha256
    blinded_unit_id: Identifier
    reviewer_case_projection_id: Identifier
    cell_id: Identifier
    system_id: ResearchSystemId
    system_config_id: Identifier
    system_config_sha256: Sha256
    run_id: Identifier
    ranking_id: Identifier
    ranking_sha256: Sha256
    selection_rank: Annotated[int, Field(ge=1, le=5)]
    case_id: Identifier
    case_sha256: Sha256
    packet_id: Identifier
    packet_sha256: Sha256
    system_proposed_structure_group_id: Identifier
    system_proposed_hypothesis_group_id: Identifier
    evidence_map: Annotated[
        tuple[PrivateEvidenceMapEntryV1, ...], Field(min_length=1, max_length=64)
    ]


class PrivatePositionMapEntryV2(PrivatePositionMapEntryV1):
    schema_version: Literal["flatband-private-position-map-entry-v2"] = (
        "flatband-private-position-map-entry-v2"
    )
    evidence_map: Annotated[
        tuple[PrivateEvidenceMapEntryV2, ...], Field(min_length=1, max_length=64)
    ]


class PrivateCaseMapEntryV1(StrictModel):
    """Private join from one reviewer-safe case projection to its frozen case."""

    reviewer_case_projection_id: Identifier
    reviewer_case_projection_sha256: Sha256
    blinded_case_id: Identifier
    frozen_candidate_id: Identifier
    frozen_candidate_sha256: Sha256
    case_id: Identifier
    case_sha256: Sha256


class ReviewerManifestV1(StrictModel):
    """Reviewer artifact.  It is safe to serialize separately from the private map."""

    schema_version: Literal["flatband-reviewer-manifest-v1"] = (
        "flatband-reviewer-manifest-v1"
    )
    manifest_id: Identifier
    manifest_sha256: Sha256
    blinded_study_id: Identifier
    blinded_reviewer_id: Identifier
    renderer_sha256: Sha256
    case_projections: Annotated[
        tuple[ReviewerCaseProjectionV1, ...], Field(min_length=1, max_length=120)
    ]
    packets: Annotated[
        tuple[ReviewerHypothesisPacketV1, ...], Field(max_length=100_000)
    ]
    masking_statement: Literal[MASKING_STATEMENT] = MASKING_STATEMENT
    perfect_blinding_claimed: Literal[False] = False
    private_identity_included: Literal[False] = False
    public_repository_release_allowed: Literal[False] = False
    sealed_at: Annotated[str, Field(min_length=20, max_length=40)]
    scientific_conclusion: Literal[False] = False

    @field_validator("sealed_at")
    @classmethod
    def validate_sealed_at(cls, value: str) -> str:
        return _require_timestamp(value)

    @model_validator(mode="after")
    def validate_manifest(self) -> "ReviewerManifestV1":
        if tuple(item.case_display_order for item in self.case_projections) != tuple(
            range(1, len(self.case_projections) + 1)
        ):
            raise ValueError("reviewer case display order must be contiguous from one")
        projection_ids = tuple(
            item.reviewer_case_projection_id for item in self.case_projections
        )
        if len(projection_ids) != len(set(projection_ids)):
            raise ValueError("reviewer case projection IDs must be unique")
        blinded_case_ids = tuple(item.blinded_case_id for item in self.case_projections)
        if len(blinded_case_ids) != len(set(blinded_case_ids)):
            raise ValueError("reviewer blinded case IDs must be unique")
        if tuple(item.display_order for item in self.packets) != tuple(
            range(1, len(self.packets) + 1)
        ):
            raise ValueError("reviewer display order must be contiguous from one")
        packet_ids = tuple(item.reviewer_packet_id for item in self.packets)
        if len(packet_ids) != len(set(packet_ids)):
            raise ValueError("reviewer packet IDs must be unique")
        blind_ids = tuple(item.blinded_unit_id for item in self.packets)
        if len(blind_ids) != len(set(blind_ids)):
            raise ValueError("reviewer blinded unit IDs must be unique")
        projection_by_id = {
            item.reviewer_case_projection_id: item for item in self.case_projections
        }
        for packet in self.packets:
            projection = projection_by_id.get(packet.reviewer_case_projection_id)
            if projection is None:
                raise ValueError("reviewer packet references a foreign case projection")
            if packet.blinded_case_id != projection.blinded_case_id:
                raise ValueError("reviewer packet differs from its blinded case")
        semantic = self.model_dump(
            mode="python", exclude={"manifest_id", "manifest_sha256"}
        )
        digest = canonical_sha256(semantic)
        if self.manifest_sha256 != digest:
            raise ValueError("reviewer manifest SHA-256 does not match safe content")
        if self.manifest_id != deterministic_id(
            "reviewer-manifest", {"manifest_sha256": digest}
        ):
            raise ValueError("reviewer manifest ID does not match its SHA-256")
        return self


class PrivateIdentityMapV1(StrictModel):
    """Private join table, stored separately and never sent to a reviewer."""

    schema_version: Literal["flatband-private-identity-map-v1"] = (
        "flatband-private-identity-map-v1"
    )
    identity_map_id: Identifier
    identity_map_sha256: Sha256
    reviewer_manifest_id: Identifier
    reviewer_manifest_sha256: Sha256
    execution_release_id: Identifier
    execution_release_sha256: Sha256
    expert_registry_id: Identifier
    expert_registry_sha256: Sha256
    frozen_case_release_id: Identifier
    frozen_case_release_sha256: Sha256
    pre_run_eligibility_release_id: Identifier
    pre_run_eligibility_release_sha256: Sha256
    expert_id: Identifier
    blinded_reviewer_id: Identifier
    case_entries: Annotated[
        tuple[PrivateCaseMapEntryV1, ...], Field(min_length=1, max_length=120)
    ]
    entries: Annotated[
        tuple[PrivatePositionMapEntryV1, ...], Field(max_length=100_000)
    ]
    private_storage_required: Literal[True] = True
    key_material_included: Literal[False] = False
    sealed_at: Annotated[str, Field(min_length=20, max_length=40)]
    scientific_conclusion: Literal[False] = False

    @field_validator("sealed_at")
    @classmethod
    def validate_sealed_at(cls, value: str) -> str:
        return _require_timestamp(value)

    @model_validator(mode="after")
    def validate_map(self) -> "PrivateIdentityMapV1":
        case_keys = tuple(item.case_id for item in self.case_entries)
        if case_keys != tuple(sorted(set(case_keys))):
            raise ValueError("private case entries must be case-ID sorted and unique")
        case_projection_ids = tuple(
            item.reviewer_case_projection_id for item in self.case_entries
        )
        if len(case_projection_ids) != len(set(case_projection_ids)):
            raise ValueError("private case entries repeat a public projection")
        keys = tuple(
            (item.case_id, item.system_id.value, item.selection_rank)
            for item in self.entries
        )
        if keys != tuple(sorted(set(keys))):
            raise ValueError("private position entries must be sorted and unique")
        semantic = self.model_dump(
            mode="python", exclude={"identity_map_id", "identity_map_sha256"}
        )
        digest = canonical_sha256(semantic)
        if self.identity_map_sha256 != digest:
            raise ValueError("private identity map SHA-256 does not match content")
        if self.identity_map_id != deterministic_id(
            "private-identity-map", {"identity_map_sha256": digest}
        ):
            raise ValueError("private identity map ID does not match its SHA-256")
        return self


class ReviewerManifestV2(StrictModel):
    """Formal public reviewer artifact; execution identity has no schema slot."""

    schema_version: Literal["flatband-reviewer-manifest-v2"] = (
        "flatband-reviewer-manifest-v2"
    )
    manifest_id: Identifier
    manifest_sha256: Sha256
    blinded_study_id: Identifier
    blinded_reviewer_id: Identifier
    renderer_sha256: Sha256
    case_projections: Annotated[
        tuple[ReviewerCaseProjectionV2, ...], Field(min_length=1, max_length=120)
    ]
    packets: Annotated[
        tuple[ReviewerHypothesisPacketV2, ...], Field(max_length=100_000)
    ]
    masking_statement: Literal[MASKING_STATEMENT] = MASKING_STATEMENT
    perfect_blinding_claimed: Literal[False] = False
    private_identity_included: Literal[False] = False
    public_repository_release_allowed: Literal[False] = False
    sealed_at: Annotated[str, Field(min_length=20, max_length=40)]
    scientific_conclusion: Literal[False] = False

    @field_validator("sealed_at")
    @classmethod
    def validate_sealed_at(cls, value: str) -> str:
        return _require_timestamp(value)

    @model_validator(mode="after")
    def validate_manifest(self) -> "ReviewerManifestV2":
        if tuple(item.case_display_order for item in self.case_projections) != tuple(
            range(1, len(self.case_projections) + 1)
        ):
            raise ValueError("V2 reviewer case display order must be contiguous")
        projection_ids = tuple(
            item.reviewer_case_projection_id for item in self.case_projections
        )
        if len(projection_ids) != len(set(projection_ids)):
            raise ValueError("V2 reviewer case projection IDs must be unique")
        blinded_case_ids = tuple(item.blinded_case_id for item in self.case_projections)
        if len(blinded_case_ids) != len(set(blinded_case_ids)):
            raise ValueError("V2 reviewer blinded case IDs must be unique")
        if tuple(item.display_order for item in self.packets) != tuple(
            range(1, len(self.packets) + 1)
        ):
            raise ValueError("V2 reviewer packet display order must be contiguous")
        for values, label in (
            (tuple(item.reviewer_packet_id for item in self.packets), "packet"),
            (tuple(item.blinded_unit_id for item in self.packets), "blinded unit"),
        ):
            if len(values) != len(set(values)):
                raise ValueError(f"V2 reviewer {label} identities must be unique")
        projection_by_id = {
            item.reviewer_case_projection_id: item for item in self.case_projections
        }
        for packet in self.packets:
            projection = projection_by_id.get(packet.reviewer_case_projection_id)
            if projection is None or packet.blinded_case_id != projection.blinded_case_id:
                raise ValueError("V2 reviewer packet references a foreign case")
        semantic = self.model_dump(
            mode="python", exclude={"manifest_id", "manifest_sha256"}
        )
        digest = canonical_sha256(semantic)
        if self.manifest_sha256 != digest or self.manifest_id != deterministic_id(
            "reviewer-manifest-v2", {"manifest_sha256": digest}
        ):
            raise ValueError("V2 reviewer manifest identity does not replay")
        return self


class PrivateIdentityMapV2(StrictModel):
    """Formal private join; exact execution positions exist only here."""

    schema_version: Literal["flatband-private-identity-map-v2"] = (
        "flatband-private-identity-map-v2"
    )
    identity_map_id: Identifier
    identity_map_sha256: Sha256
    reviewer_manifest_id: Identifier
    reviewer_manifest_sha256: Sha256
    execution_release_id: Identifier
    execution_release_sha256: Sha256
    expert_registry_id: Identifier
    expert_registry_sha256: Sha256
    frozen_case_release_id: Identifier
    frozen_case_release_sha256: Sha256
    pre_run_eligibility_release_id: Identifier
    pre_run_eligibility_release_sha256: Sha256
    expert_id: Identifier
    blinded_reviewer_id: Identifier
    case_entries: Annotated[
        tuple[PrivateCaseMapEntryV1, ...], Field(min_length=1, max_length=120)
    ]
    entries: Annotated[
        tuple[PrivatePositionMapEntryV2, ...], Field(max_length=100_000)
    ]
    private_storage_required: Literal[True] = True
    key_material_included: Literal[False] = False
    evidence_preimages_replayed: Literal[True] = True
    sealed_at: Annotated[str, Field(min_length=20, max_length=40)]
    scientific_conclusion: Literal[False] = False

    @field_validator("sealed_at")
    @classmethod
    def validate_sealed_at(cls, value: str) -> str:
        return _require_timestamp(value)

    @model_validator(mode="after")
    def validate_map(self) -> "PrivateIdentityMapV2":
        case_keys = tuple(item.case_id for item in self.case_entries)
        if case_keys != tuple(sorted(set(case_keys))):
            raise ValueError("V2 private case entries must be sorted and unique")
        projection_ids = tuple(
            item.reviewer_case_projection_id for item in self.case_entries
        )
        if len(projection_ids) != len(set(projection_ids)):
            raise ValueError("V2 private case projections must be injective")
        keys = tuple(
            (item.case_id, item.system_id.value, item.selection_rank)
            for item in self.entries
        )
        if keys != tuple(sorted(set(keys))):
            raise ValueError("V2 private positions must be sorted and unique")
        semantic = self.model_dump(
            mode="python", exclude={"identity_map_id", "identity_map_sha256"}
        )
        digest = canonical_sha256(semantic)
        if self.identity_map_sha256 != digest or self.identity_map_id != deterministic_id(
            "private-identity-map-v2", {"identity_map_sha256": digest}
        ):
            raise ValueError("V2 private identity map identity does not replay")
        return self


class PostLabelOriginGuessV1(StrictModel):
    """Independent masking-sensitivity record created only after label sealing."""

    schema_version: Literal["flatband-post-label-origin-guess-v1"] = (
        "flatband-post-label-origin-guess-v1"
    )
    guess_id: Identifier
    guess_sha256: Sha256
    reviewer_manifest_id: Identifier
    reviewer_manifest_sha256: Sha256
    blinded_reviewer_id: Identifier
    blinded_unit_id: Identifier
    sealed_annotation_sha256: Sha256
    guessed_system_id: ResearchSystemId | None = None
    confidence: Annotated[int, Field(ge=0, le=5)]
    submitted_at: Annotated[str, Field(min_length=20, max_length=40)]
    label_was_sealed_first: Literal[True] = True
    excluded_from_grade: Literal[True] = True
    scientific_conclusion: Literal[False] = False

    @field_validator("submitted_at")
    @classmethod
    def validate_submitted_at(cls, value: str) -> str:
        return _require_timestamp(value)

    @model_validator(mode="after")
    def validate_guess(self) -> "PostLabelOriginGuessV1":
        if (self.guessed_system_id is None) != (self.confidence == 0):
            raise ValueError("an abstention requires zero confidence and vice versa")
        semantic = self.model_dump(
            mode="python", exclude={"guess_id", "guess_sha256"}
        )
        digest = canonical_sha256(semantic)
        if self.guess_sha256 != digest:
            raise ValueError("origin guess SHA-256 does not match content")
        if self.guess_id != deterministic_id(
            "origin-guess", {"guess_sha256": digest}
        ):
            raise ValueError("origin guess ID does not match its SHA-256")
        return self


_FORBIDDEN_PUBLIC_KEYS = frozenset(
    {
        "case_id",
        "case_sha256",
        "frozen_candidate_id",
        "frozen_candidate_sha256",
        "system_id",
        "system_config_id",
        "system_config_sha256",
        "run_id",
        "ranking_id",
        "ranking_sha256",
        "selection_rank",
        "contributions",
        "provider",
        "execution_release_id",
        "execution_release_sha256",
        "expert_registry_id",
        "expert_registry_sha256",
        "frozen_case_release_id",
        "frozen_case_release_sha256",
        "pre_run_eligibility_release_id",
        "pre_run_eligibility_release_sha256",
        "expert_id",
        "reviewer_id",
        "adjudicator_id",
        "packet_id",
        "packet_sha256",
        "strict_structure_group_id",
        "strict_hypothesis_group_id",
        "source_id",
        "source_record_id",
        "source_url",
        "private_text_artifact_uri",
        "evidence_receipt_id",
        "evidence_receipt_sha256",
        "record_receipt_id",
        "record_receipt_sha256",
        "normalized_metadata_artifact_id",
        "normalized_metadata_artifact_sha256",
        "field_artifact_id",
        "field_artifact_sha256",
        "span_preimage_id",
        "span_preimage_sha256",
        "span_locator_sha256",
        "span_start_byte",
        "span_end_byte",
    }
)
_DIRECT_LOCATION = re.compile(r"(?:https?://|artifact://|www\.)", re.IGNORECASE)


def _walk_json(value: Any) -> Any:
    if isinstance(value, dict):
        for key, item in value.items():
            yield key, item
            yield from _walk_json(item)
    elif isinstance(value, list):
        for item in value:
            yield None, item
            yield from _walk_json(item)


def _assert_reviewer_manifest_safe(
    manifest: ReviewerManifestV1, *, forbidden_origin_tokens: frozenset[str]
) -> None:
    payload = manifest.model_dump(mode="json", round_trip=True)
    for key, value in _walk_json(payload):
        if key in _FORBIDDEN_PUBLIC_KEYS:
            raise ValueError(f"reviewer manifest contains forbidden private key: {key}")
        if isinstance(value, str):
            if _DIRECT_LOCATION.search(value):
                raise ValueError("reviewer manifest contains a direct URL or private URI")
            lowered = value.casefold()
            for token in forbidden_origin_tokens:
                if token in lowered and re.search(
                    rf"(?<![a-z0-9]){re.escape(token)}(?![a-z0-9])", lowered
                ):
                    raise ValueError(
                        "reviewer manifest text directly discloses an adapter/provider"
                    )


def _origin_tokens(
    release: ExecutionReleaseV1 | ExecutionReleaseV2 | ExecutionReleaseV3,
    eligibility: (
        PreRunEligibilityReleaseV1
        | PreRunEligibilityReleaseV2
        | PreRunEligibilityReleaseV3
    ),
    registry: ExpertStudyRegistryV1 | ExpertStudyRegistryV2,
    *,
    frozen_case_release: FrozenCaseReleaseV3 | None = None,
) -> frozenset[str]:
    tokens: set[str] = set()
    for config in release.execution_matrix.system_configs:
        tokens.update(item.source_id.casefold() for item in config.source_budgets)
        if config.llm is not None:
            tokens.add(config.llm.provider.casefold())
            tokens.add(config.llm.model.casefold())
    frozen = (
        eligibility.frozen_case_release
        if not isinstance(eligibility, PreRunEligibilityReleaseV3)
        else frozen_case_release
    )
    if frozen is None:
        raise ValueError("V3 origin masking requires the exact frozen release")
    tokens.update(
        value.casefold()
        for value in (
            release.release_id,
            release.release_sha256,
            release.execution_matrix.matrix_id,
            release.execution_matrix.matrix_sha256,
            registry.registry_id,
            registry.registry_sha256,
            frozen.release_id,
            frozen.release_sha256,
            eligibility.release_id,
            eligibility.release_sha256,
        )
    )
    candidates = (
        frozen.candidates
        if not isinstance(frozen, FrozenCaseReleaseV3)
        else eligibility.assignment_release.candidate_pool_release.candidates
    )
    for candidate in candidates:
        tokens.update(
            value.casefold()
            for value in (
                candidate.candidate_id,
                candidate.candidate_sha256,
                candidate.case.case_id,
                candidate.case.case_sha256,
                candidate.case.parent_label,
            )
        )
        tokens.update(
            record.source_record_id.casefold()
            for record in candidate.case.source_records
        )
    for cell in release.execution_matrix.cells:
        tokens.update((cell.cell_id.casefold(), cell.cell_sha256.casefold()))
    for budget in release.budget_manifests:
        tokens.update(
            (
                budget.budget_manifest_id.casefold(),
                budget.budget_manifest_sha256.casefold(),
                budget.run_id.casefold(),
            )
        )
    for ranking in release.rankings:
        tokens.update((ranking.ranking_id.casefold(), ranking.ranking_sha256.casefold()))
    for packet in release.hypothesis_packets:
        tokens.update((packet.packet_id.casefold(), packet.packet_sha256.casefold()))
    return frozenset(token for token in tokens if len(token) >= 3)


def _reviewer_evidence(
    *,
    packet: HypothesisPacketV1,
    excerpt_by_key: dict[tuple[str, str], EvidenceExcerptV1],
    expert_id: str,
    blind_key: bytes,
) -> tuple[tuple[ReviewerEvidenceSpanV1, ...], tuple[PrivateEvidenceMapEntryV1, ...]]:
    public: list[ReviewerEvidenceSpanV1] = []
    private: list[PrivateEvidenceMapEntryV1] = []
    for link in packet.evidence_links:
        excerpt = excerpt_by_key[(packet.packet_id, link.evidence_link_id)]
        if excerpt.source_span_sha256 != link.span_sha256:
            raise ValueError("evidence excerpt binds a different source span SHA-256")
        reviewer_evidence_id = _hmac_id(
            "blind-evidence",
            blind_key,
            {
                "expert_id": expert_id,
                "packet_id": packet.packet_id,
                "evidence_link_id": link.evidence_link_id,
            },
        )
        values: dict[str, object] = {
            "reviewer_evidence_id": reviewer_evidence_id,
            "span_scope": excerpt.span_scope,
            "span_type": excerpt.span_type,
            "asserted_relation": link.asserted_relation,
            "claim_summary": link.claim_summary,
            "normalized_work_citation": excerpt.normalized_work_citation,
            "excerpt": excerpt.excerpt,
            "excerpt_char_count": excerpt.excerpt_char_count,
            "excerpt_truncated": excerpt.excerpt_truncated,
            "access_policy": excerpt.access_policy,
            "redistribution_policy": excerpt.redistribution_policy,
        }
        draft = ReviewerEvidenceSpanV1.model_construct(**values)
        values["content_sha256"] = canonical_sha256(
            draft.model_dump(mode="python", exclude={"content_sha256"})
        )
        public.append(ReviewerEvidenceSpanV1.model_validate(values))
        private.append(
            PrivateEvidenceMapEntryV1(
                reviewer_evidence_id=reviewer_evidence_id,
                evidence_link_id=link.evidence_link_id,
                source_id=link.source_id,
                source_record_id=link.source_record_id,
                source_url=link.source_url,
                span_id=link.span_id,
                source_span_sha256=link.span_sha256,
                private_text_artifact_uri=link.private_text_artifact_uri,
            )
        )
    return tuple(public), tuple(private)


def _ordered_packets(
    packets: tuple[HypothesisPacketV1, ...], *, expert_id: str, blind_key: bytes
) -> tuple[HypothesisPacketV1, ...]:
    return tuple(
        sorted(
            packets,
            key=lambda packet: hmac.new(
                blind_key,
                canonical_json_bytes(
                    {
                        "domain": "review-order",
                        "expert_id": expert_id,
                        "packet_id": packet.packet_id,
                        "packet_sha256": packet.packet_sha256,
                    }
                ),
                hashlib.sha256,
            ).digest(),
        )
    )


def _ordered_cases(
    candidates: tuple[FrozenCaseCandidateV1, ...],
    *,
    expert_id: str,
    blind_key: bytes,
) -> tuple[FrozenCaseCandidateV1, ...]:
    return tuple(
        sorted(
            candidates,
            key=lambda candidate: hmac.new(
                blind_key,
                canonical_json_bytes(
                    {
                        "domain": "review-case-order",
                        "expert_id": expert_id,
                        "case_id": candidate.case.case_id,
                        "case_sha256": candidate.case.case_sha256,
                    }
                ),
                hashlib.sha256,
            ).digest(),
        )
    )


def _build_reviewer_case_projection(
    *,
    candidate: FrozenCaseCandidateV1,
    case_display_order: int,
    expert_id: str,
    blind_key: bytes,
) -> ReviewerCaseProjectionV1:
    case = _revalidate(candidate.case, FlatBandBenchmarkCaseV1)
    blinded_case_id = _hmac_id(
        "case-slot",
        blind_key,
        {
            "expert_id": expert_id,
            "case_id": case.case_id,
            "case_sha256": case.case_sha256,
        },
    )
    values: dict[str, object] = {
        "reviewer_case_projection_id": _hmac_id(
            "reviewer-case",
            blind_key,
            {
                "expert_id": expert_id,
                "case_id": case.case_id,
                "case_sha256": case.case_sha256,
            },
        ),
        "blinded_case_id": blinded_case_id,
        "case_display_order": case_display_order,
        "formula": case.formula,
        "frozen_request": case.frozen_request,
        "target_class": case.target_class,
        "target_fermi_distance_max_e_v": case.target_fermi_distance_max_e_v,
        "dimensionality": case.dimensionality,
        "hard_constraints": case.hard_constraints,
        "soft_preferences": case.soft_preferences,
        "forbidden_transformations": case.forbidden_transformations,
        "case_mechanism_stratum": case.primary_mechanism_stratum,
    }
    draft = ReviewerCaseProjectionV1.model_construct(**values)
    values["reviewer_case_projection_sha256"] = canonical_sha256(
        draft.model_dump(
            mode="python", exclude={"reviewer_case_projection_sha256"}
        )
    )
    return ReviewerCaseProjectionV1.model_validate(values)


def _build_reviewer_packet(
    *,
    packet: HypothesisPacketV1,
    case_projection: ReviewerCaseProjectionV1,
    display_order: int,
    excerpt_by_key: dict[tuple[str, str], EvidenceExcerptV1],
    expert_id: str,
    blind_key: bytes,
) -> tuple[ReviewerHypothesisPacketV1, tuple[PrivateEvidenceMapEntryV1, ...]]:
    evidence, private_evidence = _reviewer_evidence(
        packet=packet,
        excerpt_by_key=excerpt_by_key,
        expert_id=expert_id,
        blind_key=blind_key,
    )
    values: dict[str, object] = {
        "reviewer_packet_id": _hmac_id(
            "reviewer-packet",
            blind_key,
            {"expert_id": expert_id, "packet_id": packet.packet_id},
        ),
        "blinded_unit_id": _hmac_id(
            "blind-unit",
            blind_key,
            {"expert_id": expert_id, "packet_id": packet.packet_id},
        ),
        "blinded_case_id": case_projection.blinded_case_id,
        "reviewer_case_projection_id": (
            case_projection.reviewer_case_projection_id
        ),
        "display_order": display_order,
        "candidate_structure_sha256": packet.candidate_structure_sha256,
        "transformation_operator_id": packet.transformation_operator_id,
        "transformation_summary": packet.transformation_summary,
        "source_domain": packet.source_domain,
        "mechanism_family": packet.mechanism_family,
        "source_mechanism": packet.source_mechanism,
        "shared_invariant": packet.shared_invariant,
        "target_mapping": packet.target_mapping,
        "transferable_control": packet.transferable_control,
        "transfer_principle": packet.transfer_principle,
        "required_conditions": packet.required_conditions,
        "breaking_conditions": packet.breaking_conditions,
        "contradictions": packet.contradictions,
        "evidence": evidence,
        "falsification": packet.falsification,
    }
    draft = ReviewerHypothesisPacketV1.model_construct(**values)
    values["reviewer_packet_sha256"] = canonical_sha256(
        draft.model_dump(mode="python", exclude={"reviewer_packet_sha256"})
    )
    return ReviewerHypothesisPacketV1.model_validate(values), private_evidence


def build_reviewer_release(
    *,
    execution_release: ExecutionReleaseV1,
    pre_run_eligibility_release: PreRunEligibilityReleaseV1,
    expert_registry: ExpertStudyRegistryV1,
    evidence_excerpts: tuple[EvidenceExcerptV1, ...],
    blind_key: bytes,
    renderer_sha256: str,
    sealed_at: str,
) -> tuple[tuple[ReviewerManifestV1, ...], tuple[PrivateIdentityMapV1, ...]]:
    """Build physically separate reviewer manifests and private identity maps."""

    if not isinstance(blind_key, bytes) or len(blind_key) < 32:
        raise ValueError("blind key must contain at least 32 bytes")
    if not re.fullmatch(r"[0-9a-f]{64}", renderer_sha256):
        raise ValueError("renderer SHA-256 must be lowercase hexadecimal")
    sealed_at = _require_timestamp(sealed_at)
    release = _revalidate(execution_release, ExecutionReleaseV1)
    eligibility = _revalidate(
        pre_run_eligibility_release, PreRunEligibilityReleaseV1
    )
    registry = _revalidate(expert_registry, ExpertStudyRegistryV1)
    excerpts = tuple(_revalidate(item, EvidenceExcerptV1) for item in evidence_excerpts)
    assert_pre_run_eligibility_precedes_execution(
        eligibility_release=eligibility,
        execution_release=release,
    )
    assert_expert_registry_covers_split(
        registry=registry,
        split_manifest=release.execution_matrix.split_manifest,
    )
    frozen = eligibility.frozen_case_release
    if (
        frozen.expert_registry_id,
        frozen.expert_registry_sha256,
    ) != (registry.registry_id, registry.registry_sha256):
        raise ValueError("eligibility binds a different expert registry")
    if datetime.fromisoformat(sealed_at.replace("Z", "+00:00")) < datetime.fromisoformat(
        release.assembled_at.replace("Z", "+00:00")
    ):
        raise ValueError("reviewer release was sealed before execution assembly")

    candidate_by_id = {item.candidate_id: item for item in frozen.candidates}
    active_candidate_by_case: dict[str, FrozenCaseCandidateV1] = {}
    for selection in eligibility.active_selections:
        if selection.selected_candidate_id is None:
            raise ValueError("eligible execution contains an empty case slot")
        candidate = candidate_by_id[selection.selected_candidate_id]
        if (
            selection.selected_candidate_sha256,
            selection.selected_case_id,
            selection.selected_case_sha256,
        ) != (
            candidate.candidate_sha256,
            candidate.case.case_id,
            candidate.case.case_sha256,
        ):
            raise ValueError("active eligibility selection differs from frozen case")
        active_candidate_by_case[candidate.case.case_id] = candidate

    packet_by_id = {item.packet_id: item for item in release.hypothesis_packets}
    cell_by_id = {item.cell_id: item for item in release.execution_matrix.cells}
    ranking_by_id = {item.ranking_id: item for item in release.rankings}
    assignments = {item.case_id: item for item in registry.assignments}

    expected_excerpt_keys = {
        (packet.packet_id, link.evidence_link_id)
        for packet in release.hypothesis_packets
        for link in packet.evidence_links
    }
    excerpt_by_key: dict[tuple[str, str], EvidenceExcerptV1] = {}
    for excerpt in excerpts:
        key = (excerpt.packet_id, excerpt.evidence_link_id)
        if key in excerpt_by_key:
            raise ValueError("duplicate evidence excerpt projection")
        excerpt_by_key[key] = excerpt
    if set(excerpt_by_key) != expected_excerpt_keys:
        raise ValueError("evidence excerpts must exactly cover embedded packet links")

    positions: list[tuple[object, object, object, HypothesisPacketV1]] = []
    for projection in release.top5_projections:
        cell = cell_by_id[projection.cell_id]
        ranking = (
            None
            if projection.ranking_id is None
            else ranking_by_id[projection.ranking_id]
        )
        for item in projection.positions:
            if item.packet_id is None:
                continue
            if ranking is None:
                raise ValueError("present projected position lacks a ranking")
            ranked = ranking.positions[item.position - 1]
            if (ranked.packet_id, ranked.packet_sha256) != (
                item.packet_id,
                item.packet_sha256,
            ):
                raise ValueError("projection differs from its embedded ranking")
            packet = packet_by_id[item.packet_id]
            positions.append((cell, ranking, item, packet))

    reviewer_ids = tuple(
        sorted({expert_id for item in registry.assignments for expert_id in item.reviewer_ids})
    )
    origin_tokens = _origin_tokens(release, eligibility, registry)
    manifests: list[ReviewerManifestV1] = []
    private_maps: list[PrivateIdentityMapV1] = []

    for expert_id in reviewer_ids:
        assigned_case_ids = tuple(
            sorted(
                assignment.case_id
                for assignment in registry.assignments
                if expert_id in assignment.reviewer_ids
            )
        )
        assigned_candidates = tuple(
            active_candidate_by_case[case_id] for case_id in assigned_case_ids
        )
        case_order = _ordered_cases(
            assigned_candidates,
            expert_id=expert_id,
            blind_key=blind_key,
        )
        public_case_projections = tuple(
            _build_reviewer_case_projection(
                candidate=candidate,
                case_display_order=case_display_order,
                expert_id=expert_id,
                blind_key=blind_key,
            )
            for case_display_order, candidate in enumerate(case_order, start=1)
        )
        case_projection_by_original = {
            candidate.case.case_id: projection
            for candidate, projection in zip(
                case_order, public_case_projections, strict=True
            )
        }
        assigned_positions = [
            value
            for value in positions
            if expert_id in assignments[value[0].case_id].reviewer_ids
        ]
        packets_by_id = {value[3].packet_id: value[3] for value in assigned_positions}
        packet_order = _ordered_packets(
            tuple(packets_by_id.values()),
            expert_id=expert_id,
            blind_key=blind_key,
        )
        blinded_reviewer_id = _hmac_id(
            "reviewer-slot", blind_key, {"expert_id": expert_id}
        )
        public_packets: list[ReviewerHypothesisPacketV1] = []
        public_by_original: dict[str, ReviewerHypothesisPacketV1] = {}
        private_evidence_by_original: dict[
            str, tuple[PrivateEvidenceMapEntryV1, ...]
        ] = {}

        for display_order, packet in enumerate(packet_order, start=1):
            public_packet, private_evidence = _build_reviewer_packet(
                packet=packet,
                case_projection=case_projection_by_original[packet.case_id],
                display_order=display_order,
                excerpt_by_key=excerpt_by_key,
                expert_id=expert_id,
                blind_key=blind_key,
            )
            public_packets.append(public_packet)
            public_by_original[packet.packet_id] = public_packet
            private_evidence_by_original[packet.packet_id] = private_evidence

        manifest = _identified(
            ReviewerManifestV1,
            id_field="manifest_id",
            sha_field="manifest_sha256",
            prefix="reviewer-manifest",
            values={
                "blinded_study_id": _hmac_id(
                    "study-slot",
                    blind_key,
                    {
                        "execution_release_sha256": release.release_sha256,
                        "expert_registry_sha256": registry.registry_sha256,
                        "pre_run_eligibility_release_sha256": (
                            eligibility.release_sha256
                        ),
                    },
                ),
                "blinded_reviewer_id": blinded_reviewer_id,
                "renderer_sha256": renderer_sha256,
                "case_projections": public_case_projections,
                "packets": tuple(public_packets),
                "sealed_at": sealed_at,
            },
        )
        _assert_reviewer_manifest_safe(
            manifest, forbidden_origin_tokens=origin_tokens
        )

        private_case_entries = tuple(
            sorted(
                (
                    PrivateCaseMapEntryV1(
                        reviewer_case_projection_id=(
                            case_projection_by_original[
                                candidate.case.case_id
                            ].reviewer_case_projection_id
                        ),
                        reviewer_case_projection_sha256=(
                            case_projection_by_original[
                                candidate.case.case_id
                            ].reviewer_case_projection_sha256
                        ),
                        blinded_case_id=case_projection_by_original[
                            candidate.case.case_id
                        ].blinded_case_id,
                        frozen_candidate_id=candidate.candidate_id,
                        frozen_candidate_sha256=candidate.candidate_sha256,
                        case_id=candidate.case.case_id,
                        case_sha256=candidate.case.case_sha256,
                    )
                    for candidate in assigned_candidates
                ),
                key=lambda item: item.case_id,
            )
        )
        private_entries: list[PrivatePositionMapEntryV1] = []
        for cell, ranking, item, packet in assigned_positions:
            public_packet = public_by_original[packet.packet_id]
            private_entries.append(
                PrivatePositionMapEntryV1(
                    pooled_unit_id=_hmac_id(
                        "pooled-unit",
                        blind_key,
                        {
                            "packet_id": packet.packet_id,
                            "packet_sha256": packet.packet_sha256,
                        },
                    ),
                    reviewer_packet_id=public_packet.reviewer_packet_id,
                    reviewer_packet_sha256=public_packet.reviewer_packet_sha256,
                    blinded_unit_id=public_packet.blinded_unit_id,
                    reviewer_case_projection_id=(
                        public_packet.reviewer_case_projection_id
                    ),
                    cell_id=cell.cell_id,
                    system_id=cell.system_id,
                    system_config_id=cell.system_config_id,
                    system_config_sha256=cell.system_config_sha256,
                    run_id=ranking.run_id,
                    ranking_id=ranking.ranking_id,
                    ranking_sha256=ranking.ranking_sha256,
                    selection_rank=item.position,
                    case_id=packet.case_id,
                    case_sha256=packet.case_sha256,
                    packet_id=packet.packet_id,
                    packet_sha256=packet.packet_sha256,
                    system_proposed_structure_group_id=(
                        packet.strict_structure_group_id
                    ),
                    system_proposed_hypothesis_group_id=(
                        packet.strict_hypothesis_group_id
                    ),
                    evidence_map=private_evidence_by_original[packet.packet_id],
                )
            )
        private_entries.sort(
            key=lambda value: (
                value.case_id,
                value.system_id.value,
                value.selection_rank,
            )
        )
        private_map = _identified(
            PrivateIdentityMapV1,
            id_field="identity_map_id",
            sha_field="identity_map_sha256",
            prefix="private-identity-map",
            values={
                "reviewer_manifest_id": manifest.manifest_id,
                "reviewer_manifest_sha256": manifest.manifest_sha256,
                "execution_release_id": release.release_id,
                "execution_release_sha256": release.release_sha256,
                "expert_registry_id": registry.registry_id,
                "expert_registry_sha256": registry.registry_sha256,
                "frozen_case_release_id": frozen.release_id,
                "frozen_case_release_sha256": frozen.release_sha256,
                "pre_run_eligibility_release_id": eligibility.release_id,
                "pre_run_eligibility_release_sha256": eligibility.release_sha256,
                "expert_id": expert_id,
                "blinded_reviewer_id": blinded_reviewer_id,
                "case_entries": private_case_entries,
                "entries": tuple(private_entries),
                "sealed_at": sealed_at,
            },
        )
        manifests.append(manifest)
        private_maps.append(private_map)

    result = (tuple(manifests), tuple(private_maps))
    assert_reviewer_release_exact_coverage(
        execution_release=release,
        pre_run_eligibility_release=eligibility,
        expert_registry=registry,
        reviewer_manifests=result[0],
        private_identity_maps=result[1],
        evidence_excerpts=excerpts,
        blind_key=blind_key,
        renderer_sha256=renderer_sha256,
    )
    return result


def assert_reviewer_release_exact_coverage(
    *,
    execution_release: ExecutionReleaseV1,
    pre_run_eligibility_release: PreRunEligibilityReleaseV1,
    expert_registry: ExpertStudyRegistryV1,
    reviewer_manifests: tuple[ReviewerManifestV1, ...],
    private_identity_maps: tuple[PrivateIdentityMapV1, ...],
    evidence_excerpts: tuple[EvidenceExcerptV1, ...],
    blind_key: bytes,
    renderer_sha256: str,
) -> None:
    """Replay safe packets and require exact coverage of every present position."""

    if not isinstance(blind_key, bytes) or len(blind_key) < 32:
        raise ValueError("blind key must contain at least 32 bytes")
    if not re.fullmatch(r"[0-9a-f]{64}", renderer_sha256):
        raise ValueError("renderer SHA-256 must be lowercase hexadecimal")
    release = _revalidate(execution_release, ExecutionReleaseV1)
    eligibility = _revalidate(
        pre_run_eligibility_release, PreRunEligibilityReleaseV1
    )
    registry = _revalidate(expert_registry, ExpertStudyRegistryV1)
    manifests = tuple(
        _revalidate(item, ReviewerManifestV1) for item in reviewer_manifests
    )
    maps = tuple(
        _revalidate(item, PrivateIdentityMapV1) for item in private_identity_maps
    )
    excerpts = tuple(
        _revalidate(item, EvidenceExcerptV1) for item in evidence_excerpts
    )
    assert_pre_run_eligibility_precedes_execution(
        eligibility_release=eligibility,
        execution_release=release,
    )
    assert_expert_registry_covers_split(
        registry=registry,
        split_manifest=release.execution_matrix.split_manifest,
    )
    frozen = eligibility.frozen_case_release
    if (
        frozen.expert_registry_id,
        frozen.expert_registry_sha256,
    ) != (registry.registry_id, registry.registry_sha256):
        raise ValueError("eligibility binds a different expert registry")
    candidate_by_id = {item.candidate_id: item for item in frozen.candidates}
    active_candidate_by_case: dict[str, FrozenCaseCandidateV1] = {}
    for selection in eligibility.active_selections:
        if selection.selected_candidate_id is None:
            raise ValueError("eligible execution contains an empty case slot")
        candidate = candidate_by_id[selection.selected_candidate_id]
        if (
            selection.selected_candidate_sha256,
            selection.selected_case_id,
            selection.selected_case_sha256,
        ) != (
            candidate.candidate_sha256,
            candidate.case.case_id,
            candidate.case.case_sha256,
        ):
            raise ValueError("active eligibility selection differs from frozen case")
        active_candidate_by_case[candidate.case.case_id] = candidate

    expected_excerpt_keys = {
        (packet.packet_id, link.evidence_link_id)
        for packet in release.hypothesis_packets
        for link in packet.evidence_links
    }
    excerpt_by_key: dict[tuple[str, str], EvidenceExcerptV1] = {}
    for excerpt in excerpts:
        key = (excerpt.packet_id, excerpt.evidence_link_id)
        if key in excerpt_by_key:
            raise ValueError("duplicate evidence excerpt projection")
        excerpt_by_key[key] = excerpt
    if set(excerpt_by_key) != expected_excerpt_keys:
        raise ValueError("evidence excerpts must exactly cover embedded packet links")

    manifest_by_id = {item.manifest_id: item for item in manifests}
    if len(manifest_by_id) != len(manifests):
        raise ValueError("duplicate reviewer manifest identity")
    map_by_expert = {item.expert_id: item for item in maps}
    if len(map_by_expert) != len(maps):
        raise ValueError("duplicate private identity map for one expert")
    referenced_manifest_ids = tuple(item.reviewer_manifest_id for item in maps)
    if len(referenced_manifest_ids) != len(set(referenced_manifest_ids)):
        raise ValueError("one reviewer manifest is shared by multiple experts")
    if set(referenced_manifest_ids) != set(manifest_by_id):
        raise ValueError("public and private reviewer manifests are not one-to-one")

    reviewer_ids = {
        expert_id
        for assignment in registry.assignments
        for expert_id in assignment.reviewer_ids
    }
    if set(map_by_expert) != reviewer_ids:
        raise ValueError("private maps do not exactly cover assigned reviewers")
    expected_study_id = _hmac_id(
        "study-slot",
        blind_key,
        {
            "execution_release_sha256": release.release_sha256,
            "expert_registry_sha256": registry.registry_sha256,
            "pre_run_eligibility_release_sha256": eligibility.release_sha256,
        },
    )
    origin_tokens = _origin_tokens(release, eligibility, registry)
    blind_reviewer_ids: set[str] = set()
    for manifest in manifests:
        _assert_reviewer_manifest_safe(
            manifest, forbidden_origin_tokens=origin_tokens
        )
        if manifest.blinded_study_id != expected_study_id:
            raise ValueError("reviewer manifest binds a different opaque study")
        if manifest.renderer_sha256 != renderer_sha256:
            raise ValueError("reviewer manifest uses a different renderer")
        if datetime.fromisoformat(
            manifest.sealed_at.replace("Z", "+00:00")
        ) < datetime.fromisoformat(release.assembled_at.replace("Z", "+00:00")):
            raise ValueError("reviewer manifest was sealed before execution assembly")
        if manifest.blinded_reviewer_id in blind_reviewer_ids:
            raise ValueError("reviewer manifests share a blinded reviewer identity")
        blind_reviewer_ids.add(manifest.blinded_reviewer_id)

    assignments = {item.case_id: item for item in registry.assignments}
    cells = {item.cell_id: item for item in release.execution_matrix.cells}
    rankings = {item.ranking_id: item for item in release.rankings}
    packets = {item.packet_id: item for item in release.hypothesis_packets}
    expected: dict[tuple[str, str, int], tuple[object, ...]] = {}
    for projection in release.top5_projections:
        cell = cells[projection.cell_id]
        if projection.ranking_id is None:
            if any(item.packet_id is not None for item in projection.positions):
                raise ValueError("present position has no ranking identity")
            continue
        ranking = rankings[projection.ranking_id]
        for item in projection.positions:
            if item.packet_id is None:
                continue
            for expert_id in assignments[cell.case_id].reviewer_ids:
                expected[(expert_id, cell.cell_id, item.position)] = (
                    cell.system_id,
                    cell.system_config_id,
                    cell.system_config_sha256,
                    ranking.run_id,
                    ranking.ranking_id,
                    ranking.ranking_sha256,
                    cell.case_id,
                    cell.case_sha256,
                    item.packet_id,
                    item.packet_sha256,
                )

    observed: dict[tuple[str, str, int], tuple[object, ...]] = {}
    pooled_by_packet: dict[tuple[str, str], str] = {}
    packet_by_pooled: dict[str, tuple[str, str]] = {}
    for private_map in maps:
        manifest = manifest_by_id.get(private_map.reviewer_manifest_id)
        if manifest is None:
            raise ValueError("private identity map references an absent public manifest")
        if (
            private_map.reviewer_manifest_sha256,
            private_map.blinded_reviewer_id,
            private_map.sealed_at,
        ) != (
            manifest.manifest_sha256,
            manifest.blinded_reviewer_id,
            manifest.sealed_at,
        ):
            raise ValueError("private map does not bind its exact reviewer manifest")
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
            release.release_id,
            release.release_sha256,
            registry.registry_id,
            registry.registry_sha256,
            frozen.release_id,
            frozen.release_sha256,
            eligibility.release_id,
            eligibility.release_sha256,
        ):
            raise ValueError(
                "private map binds a different execution, eligibility, or registry"
            )
        expected_blind_reviewer_id = _hmac_id(
            "reviewer-slot", blind_key, {"expert_id": private_map.expert_id}
        )
        if private_map.blinded_reviewer_id != expected_blind_reviewer_id:
            raise ValueError("private map has a non-keyed reviewer identity")
        assigned_case_ids = tuple(
            sorted(
                assignment.case_id
                for assignment in registry.assignments
                if private_map.expert_id in assignment.reviewer_ids
            )
        )
        if tuple(item.case_id for item in private_map.case_entries) != assigned_case_ids:
            raise ValueError("private case map does not exactly cover assigned cases")
        public_cases = {
            item.reviewer_case_projection_id: item
            for item in manifest.case_projections
        }
        if len(public_cases) != len(manifest.case_projections):
            raise ValueError("reviewer manifest repeats a case projection")
        case_entries_by_public = {
            item.reviewer_case_projection_id: item
            for item in private_map.case_entries
        }
        if set(case_entries_by_public) != set(public_cases):
            raise ValueError(
                "reviewer case projections do not exactly cover assigned cases"
            )
        assigned_candidates = tuple(
            active_candidate_by_case[case_id] for case_id in assigned_case_ids
        )
        expected_case_order = _ordered_cases(
            assigned_candidates,
            expert_id=private_map.expert_id,
            blind_key=blind_key,
        )
        case_projection_by_case: dict[str, ReviewerCaseProjectionV1] = {}
        for case_display_order, candidate in enumerate(expected_case_order, start=1):
            expected_projection = _build_reviewer_case_projection(
                candidate=candidate,
                case_display_order=case_display_order,
                expert_id=private_map.expert_id,
                blind_key=blind_key,
            )
            observed_projection = manifest.case_projections[case_display_order - 1]
            if observed_projection != expected_projection:
                raise ValueError(
                    "reviewer case differs from authoritative safe projection"
                )
            case_entry = case_entries_by_public.get(
                observed_projection.reviewer_case_projection_id
            )
            if case_entry is None or (
                case_entry.reviewer_case_projection_sha256,
                case_entry.blinded_case_id,
                case_entry.frozen_candidate_id,
                case_entry.frozen_candidate_sha256,
                case_entry.case_id,
                case_entry.case_sha256,
            ) != (
                observed_projection.reviewer_case_projection_sha256,
                observed_projection.blinded_case_id,
                candidate.candidate_id,
                candidate.candidate_sha256,
                candidate.case.case_id,
                candidate.case.case_sha256,
            ):
                raise ValueError("private case map differs from frozen case projection")
            case_projection_by_case[candidate.case.case_id] = observed_projection
        public_packets = {item.reviewer_packet_id: item for item in manifest.packets}
        entries_by_public: dict[str, list[PrivatePositionMapEntryV1]] = {}
        referenced_public: set[str] = set()
        for entry in private_map.entries:
            key = (private_map.expert_id, entry.cell_id, entry.selection_rank)
            if key in observed:
                raise ValueError("a reviewer position appears more than once")
            observed[key] = (
                entry.system_id,
                entry.system_config_id,
                entry.system_config_sha256,
                entry.run_id,
                entry.ranking_id,
                entry.ranking_sha256,
                entry.case_id,
                entry.case_sha256,
                entry.packet_id,
                entry.packet_sha256,
            )
            public_packet = public_packets.get(entry.reviewer_packet_id)
            if public_packet is None:
                raise ValueError("private entry references an absent reviewer packet")
            if (
                entry.reviewer_packet_sha256,
                entry.blinded_unit_id,
                entry.reviewer_case_projection_id,
            ) != (
                public_packet.reviewer_packet_sha256,
                public_packet.blinded_unit_id,
                public_packet.reviewer_case_projection_id,
            ):
                raise ValueError("private entry differs from reviewer packet identity")
            case_projection = case_projection_by_case.get(entry.case_id)
            if (
                case_projection is None
                or entry.reviewer_case_projection_id
                != case_projection.reviewer_case_projection_id
                or public_packet.blinded_case_id != case_projection.blinded_case_id
            ):
                raise ValueError("packet references a foreign reviewer case projection")
            private_evidence_ids = {
                item.reviewer_evidence_id for item in entry.evidence_map
            }
            public_evidence_ids = {
                item.reviewer_evidence_id for item in public_packet.evidence
            }
            if private_evidence_ids != public_evidence_ids:
                raise ValueError("private evidence map does not cover reviewer evidence")
            referenced_public.add(entry.reviewer_packet_id)
            entries_by_public.setdefault(entry.reviewer_packet_id, []).append(entry)
            pooled_key = (entry.packet_id, entry.packet_sha256)
            previous = pooled_by_packet.setdefault(pooled_key, entry.pooled_unit_id)
            if previous != entry.pooled_unit_id:
                raise ValueError("reviewers do not share one private pooled unit")
            expected_pool = _hmac_id(
                "pooled-unit",
                blind_key,
                {"packet_id": entry.packet_id, "packet_sha256": entry.packet_sha256},
            )
            if entry.pooled_unit_id != expected_pool:
                raise ValueError("private pooled unit is not keyed to the packet")
            previous_packet = packet_by_pooled.setdefault(
                entry.pooled_unit_id, pooled_key
            )
            if previous_packet != pooled_key:
                raise ValueError("different packets share one private pooled unit")
            packet = packets.get(entry.packet_id)
            if packet is None or packet.packet_sha256 != entry.packet_sha256:
                raise ValueError("private entry references a non-authoritative packet")
            if (
                entry.system_proposed_structure_group_id,
                entry.system_proposed_hypothesis_group_id,
            ) != (
                packet.strict_structure_group_id,
                packet.strict_hypothesis_group_id,
            ):
                raise ValueError("private entry changes system-proposed audit groups")
        if referenced_public != set(public_packets):
            raise ValueError("reviewer manifest contains an unreferenced packet")

        original_by_public: dict[str, HypothesisPacketV1] = {}
        for reviewer_packet_id, entries in entries_by_public.items():
            original_keys = {
                (entry.packet_id, entry.packet_sha256) for entry in entries
            }
            if len(original_keys) != 1:
                raise ValueError("one reviewer packet maps to multiple original packets")
            packet_id, packet_sha256 = next(iter(original_keys))
            packet = packets.get(packet_id)
            if packet is None or packet.packet_sha256 != packet_sha256:
                raise ValueError("reviewer packet maps to a non-authoritative packet")
            original_by_public[reviewer_packet_id] = packet

        expected_order = _ordered_packets(
            tuple(original_by_public.values()),
            expert_id=private_map.expert_id,
            blind_key=blind_key,
        )
        observed_order = tuple(
            original_by_public[item.reviewer_packet_id] for item in manifest.packets
        )
        if observed_order != expected_order:
            raise ValueError("reviewer packet order is not the independent keyed order")
        for display_order, packet in enumerate(expected_order, start=1):
            expected_public, expected_private_evidence = _build_reviewer_packet(
                packet=packet,
                case_projection=case_projection_by_case[packet.case_id],
                display_order=display_order,
                excerpt_by_key=excerpt_by_key,
                expert_id=private_map.expert_id,
                blind_key=blind_key,
            )
            observed_public = manifest.packets[display_order - 1]
            if observed_public != expected_public:
                raise ValueError(
                    "reviewer packet differs from authoritative safe projection"
                )
            for entry in entries_by_public[observed_public.reviewer_packet_id]:
                if entry.evidence_map != expected_private_evidence:
                    raise ValueError(
                        "private evidence identity differs from source links"
                    )

    if observed != expected:
        raise ValueError("private identity maps do not exactly cover assigned positions")


EvidencePreimageV2 = tuple[
    EvidenceLinkReceiptV1,
    MetadataRecordReceiptV1,
    NormalizedMetadataArtifactV1,
    MetadataSpanPreimageV1,
]


def _build_reviewer_case_projection_v2(
    *,
    candidate: FrozenCaseCandidateV1,
    case_display_order: int,
    expert_id: str,
    blind_key: bytes,
) -> ReviewerCaseProjectionV2:
    case = _revalidate(candidate.case, FlatBandBenchmarkCaseV1)
    values: dict[str, object] = {
        "reviewer_case_projection_id": _hmac_id(
            "reviewer-case-v2",
            blind_key,
            {
                "expert_id": expert_id,
                "case_id": case.case_id,
                "case_sha256": case.case_sha256,
            },
        ),
        "blinded_case_id": _hmac_id(
            "case-slot-v2",
            blind_key,
            {
                "expert_id": expert_id,
                "case_id": case.case_id,
                "case_sha256": case.case_sha256,
            },
        ),
        "case_display_order": case_display_order,
        "formula": case.formula,
        "frozen_request": case.frozen_request,
        "target_class": case.target_class,
        "target_fermi_distance_max_e_v": case.target_fermi_distance_max_e_v,
        "dimensionality": case.dimensionality,
        "hard_constraints": case.hard_constraints,
        "soft_preferences": case.soft_preferences,
        "forbidden_transformations": case.forbidden_transformations,
        "case_mechanism_stratum": case.primary_mechanism_stratum,
    }
    draft = ReviewerCaseProjectionV2.model_construct(**values)
    values["reviewer_case_projection_sha256"] = canonical_sha256(
        draft.model_dump(
            mode="python", exclude={"reviewer_case_projection_sha256"}
        )
    )
    return ReviewerCaseProjectionV2.model_validate(values)


def _index_execution_evidence_v2(
    release: ExecutionReleaseV2 | ExecutionReleaseV3,
) -> dict[tuple[str, str, str], EvidencePreimageV2]:
    """Index and replay every exact execution evidence preimage by cell/packet/link."""

    observed: dict[tuple[str, str, str], EvidencePreimageV2] = {}
    receipt_ids: dict[str, str] = {}
    receipt_shas: dict[str, str] = {}
    for terminal in release.terminal_results:
        for bundle in terminal.source_receipt_bundles:
            records = {item.record_receipt_id: item for item in bundle.metadata_records}
            artifacts = {
                item.artifact_id: item for item in bundle.normalized_metadata_artifacts
            }
            preimages = {
                item.span_preimage_id: item for item in bundle.metadata_span_preimages
            }
            for receipt in bundle.evidence_links:
                key = (terminal.cell_id, receipt.packet_id, receipt.evidence_link_id)
                if key in observed:
                    raise ValueError("execution repeats a cell/packet/evidence receipt")
                old_sha = receipt_ids.setdefault(
                    receipt.evidence_receipt_id, receipt.evidence_receipt_sha256
                )
                old_id = receipt_shas.setdefault(
                    receipt.evidence_receipt_sha256, receipt.evidence_receipt_id
                )
                if old_sha != receipt.evidence_receipt_sha256 or old_id != receipt.evidence_receipt_id:
                    raise ValueError("execution evidence receipt identity is not injective")
                record = records.get(receipt.record_receipt_id)
                artifact = artifacts.get(receipt.normalized_metadata_artifact_id)
                preimage = preimages.get(receipt.span_preimage_id)
                if record is None or artifact is None or preimage is None:
                    raise ValueError("execution evidence receipt has a missing preimage link")
                if (
                    receipt.cell_id,
                    receipt.record_receipt_sha256,
                    receipt.normalized_metadata_artifact_sha256,
                    receipt.span_preimage_sha256,
                ) != (
                    terminal.cell_id,
                    record.record_receipt_sha256,
                    artifact.artifact_sha256,
                    preimage.span_preimage_sha256,
                ):
                    raise ValueError("execution evidence receipt binds a foreign preimage")
                field = next(
                    (
                        item
                        for item in artifact.fields
                        if item.field_artifact_id == receipt.field_artifact_id
                    ),
                    None,
                )
                if field is None or (
                    receipt.field_artifact_sha256,
                    receipt.span_field,
                    receipt.metadata_json_path,
                    receipt.span_start_byte,
                    receipt.span_end_byte,
                    receipt.span_utf8,
                    receipt.span_utf8_sha256,
                    receipt.span_id,
                ) != (
                    field.field_artifact_sha256,
                    preimage.field_name,
                    preimage.json_path,
                    preimage.start_byte,
                    preimage.end_byte,
                    preimage.span_utf8,
                    preimage.span_utf8_sha256,
                    preimage.span_id,
                ):
                    raise ValueError("execution evidence bytes do not replay from metadata")
                observed[key] = (receipt, record, artifact, preimage)

    cells = {item.cell_id: item for item in release.execution_matrix.cells}
    rankings = {item.ranking_id: item for item in release.rankings}
    packets = {item.packet_id: item for item in release.hypothesis_packets}
    expected: set[tuple[str, str, str]] = set()
    for projection in release.top5_projections:
        if projection.ranking_id is None:
            continue
        ranking = rankings[projection.ranking_id]
        if ranking.cell_id != projection.cell_id:
            raise ValueError("top-five projection uses a foreign ranking")
        for position in projection.positions:
            if position.packet_id is None:
                continue
            packet = packets[position.packet_id]
            if packet.case_id != cells[projection.cell_id].case_id:
                raise ValueError("ranked packet belongs to a foreign case")
            expected.update(
                (projection.cell_id, packet.packet_id, link.evidence_link_id)
                for link in packet.evidence_links
            )
    if set(observed) != expected:
        raise ValueError("execution evidence preimages do not exactly cover present packets")
    return observed


def _validate_evidence_excerpts_v2(
    *,
    release: ExecutionReleaseV2 | ExecutionReleaseV3,
    evidence_excerpts: tuple[EvidenceExcerptV2, ...],
) -> tuple[
    dict[tuple[str, str, str], EvidenceExcerptV2],
    dict[tuple[str, str, str], EvidencePreimageV2],
]:
    authoritative = _index_execution_evidence_v2(release)
    excerpts: dict[tuple[str, str, str], EvidenceExcerptV2] = {}
    packets = {item.packet_id: item for item in release.hypothesis_packets}
    for raw in evidence_excerpts:
        excerpt = _revalidate(raw, EvidenceExcerptV2)
        key = (excerpt.cell_id, excerpt.packet_id, excerpt.evidence_link_id)
        if key in excerpts:
            raise ValueError("duplicate V2 reviewer evidence excerpt")
        evidence = authoritative.get(key)
        if evidence is None:
            raise ValueError("V2 reviewer excerpt references a foreign execution receipt")
        receipt, record, artifact, preimage = evidence
        packet = packets[excerpt.packet_id]
        link = next(
            item
            for item in packet.evidence_links
            if item.evidence_link_id == excerpt.evidence_link_id
        )
        if (
            excerpt.evidence_receipt_id,
            excerpt.evidence_receipt_sha256,
            excerpt.record_receipt_id,
            excerpt.record_receipt_sha256,
            excerpt.normalized_metadata_artifact_id,
            excerpt.normalized_metadata_artifact_sha256,
            excerpt.field_artifact_id,
            excerpt.field_artifact_sha256,
            excerpt.span_preimage_id,
            excerpt.span_preimage_sha256,
            excerpt.span_field,
            excerpt.metadata_json_path,
            excerpt.span_start_byte,
            excerpt.span_end_byte,
            excerpt.span_locator_sha256,
            excerpt.source_span_text,
            excerpt.source_span_sha256,
        ) != (
            receipt.evidence_receipt_id,
            receipt.evidence_receipt_sha256,
            record.record_receipt_id,
            record.record_receipt_sha256,
            artifact.artifact_id,
            artifact.artifact_sha256,
            receipt.field_artifact_id,
            receipt.field_artifact_sha256,
            preimage.span_preimage_id,
            preimage.span_preimage_sha256,
            receipt.span_field,
            receipt.metadata_json_path,
            receipt.span_start_byte,
            receipt.span_end_byte,
            receipt.span_locator_sha256,
            receipt.span_utf8,
            link.span_sha256,
        ):
            raise ValueError("V2 reviewer excerpt differs from exact execution preimage")
        excerpts[key] = excerpt
    if set(excerpts) != set(authoritative):
        raise ValueError("V2 reviewer excerpts do not exactly cover execution evidence")

    # A pooled packet has one public rendering.  Repeated execution positions
    # must therefore carry byte-identical safe excerpt projections.
    public_projection_by_link: dict[tuple[str, str], tuple[object, ...]] = {}
    for (_cell_id, packet_id, link_id), excerpt in excerpts.items():
        safe_projection = (
            excerpt.source_span_text,
            excerpt.source_span_sha256,
            excerpt.excerpt_start_offset,
            excerpt.excerpt_end_offset,
            excerpt.span_scope,
            excerpt.span_type,
            excerpt.normalized_work_citation,
            excerpt.excerpt,
            excerpt.excerpt_sha256,
            excerpt.excerpt_truncated,
            excerpt.access_policy,
            excerpt.redistribution_policy,
        )
        previous = public_projection_by_link.setdefault(
            (packet_id, link_id), safe_projection
        )
        if previous != safe_projection:
            raise ValueError("one pooled packet has conflicting public evidence excerpts")
    return excerpts, authoritative


def _public_reviewer_packet_v2(
    *,
    packet: HypothesisPacketV1,
    case_projection: ReviewerCaseProjectionV2,
    display_order: int,
    excerpt_by_packet_link: dict[tuple[str, str], EvidenceExcerptV2],
    expert_id: str,
    blind_key: bytes,
) -> ReviewerHypothesisPacketV2:
    evidence: list[ReviewerEvidenceSpanV1] = []
    for link in packet.evidence_links:
        excerpt = excerpt_by_packet_link[(packet.packet_id, link.evidence_link_id)]
        values: dict[str, object] = {
            "reviewer_evidence_id": _hmac_id(
                "blind-evidence-v2",
                blind_key,
                {
                    "expert_id": expert_id,
                    "packet_id": packet.packet_id,
                    "evidence_link_id": link.evidence_link_id,
                },
            ),
            "span_scope": excerpt.span_scope,
            "span_type": excerpt.span_type,
            "asserted_relation": link.asserted_relation,
            "claim_summary": link.claim_summary,
            "normalized_work_citation": excerpt.normalized_work_citation,
            "excerpt": excerpt.excerpt,
            "excerpt_char_count": excerpt.excerpt_char_count,
            "excerpt_truncated": excerpt.excerpt_truncated,
            "access_policy": excerpt.access_policy,
            "redistribution_policy": excerpt.redistribution_policy,
        }
        draft = ReviewerEvidenceSpanV1.model_construct(**values)
        values["content_sha256"] = canonical_sha256(
            draft.model_dump(mode="python", exclude={"content_sha256"})
        )
        evidence.append(ReviewerEvidenceSpanV1.model_validate(values))
    values = {
        "reviewer_packet_id": _hmac_id(
            "reviewer-packet-v2",
            blind_key,
            {"expert_id": expert_id, "packet_id": packet.packet_id},
        ),
        "blinded_unit_id": _hmac_id(
            "blind-unit-v2",
            blind_key,
            {"expert_id": expert_id, "packet_id": packet.packet_id},
        ),
        "blinded_case_id": case_projection.blinded_case_id,
        "reviewer_case_projection_id": case_projection.reviewer_case_projection_id,
        "display_order": display_order,
        "candidate_structure_sha256": packet.candidate_structure_sha256,
        "transformation_operator_id": packet.transformation_operator_id,
        "transformation_summary": packet.transformation_summary,
        "source_domain": packet.source_domain,
        "mechanism_family": packet.mechanism_family,
        "source_mechanism": packet.source_mechanism,
        "shared_invariant": packet.shared_invariant,
        "target_mapping": packet.target_mapping,
        "transferable_control": packet.transferable_control,
        "transfer_principle": packet.transfer_principle,
        "required_conditions": packet.required_conditions,
        "breaking_conditions": packet.breaking_conditions,
        "contradictions": packet.contradictions,
        "evidence": tuple(evidence),
        "falsification": packet.falsification,
    }
    draft = ReviewerHypothesisPacketV2.model_construct(**values)
    values["reviewer_packet_sha256"] = canonical_sha256(
        draft.model_dump(mode="python", exclude={"reviewer_packet_sha256"})
    )
    return ReviewerHypothesisPacketV2.model_validate(values)


def _private_evidence_v2(
    *,
    packet: HypothesisPacketV1,
    cell_id: str,
    excerpts: dict[tuple[str, str, str], EvidenceExcerptV2],
    expert_id: str,
    blind_key: bytes,
) -> tuple[PrivateEvidenceMapEntryV2, ...]:
    values: list[PrivateEvidenceMapEntryV2] = []
    for link in packet.evidence_links:
        excerpt = excerpts[(cell_id, packet.packet_id, link.evidence_link_id)]
        values.append(
            PrivateEvidenceMapEntryV2(
                reviewer_evidence_id=_hmac_id(
                    "blind-evidence-v2",
                    blind_key,
                    {
                        "expert_id": expert_id,
                        "packet_id": packet.packet_id,
                        "evidence_link_id": link.evidence_link_id,
                    },
                ),
                evidence_link_id=link.evidence_link_id,
                source_id=link.source_id,
                source_record_id=link.source_record_id,
                source_url=link.source_url,
                span_id=link.span_id,
                source_span_sha256=link.span_sha256,
                private_text_artifact_uri=link.private_text_artifact_uri,
                evidence_receipt_id=excerpt.evidence_receipt_id,
                evidence_receipt_sha256=excerpt.evidence_receipt_sha256,
                record_receipt_id=excerpt.record_receipt_id,
                record_receipt_sha256=excerpt.record_receipt_sha256,
                normalized_metadata_artifact_id=(
                    excerpt.normalized_metadata_artifact_id
                ),
                normalized_metadata_artifact_sha256=(
                    excerpt.normalized_metadata_artifact_sha256
                ),
                field_artifact_id=excerpt.field_artifact_id,
                field_artifact_sha256=excerpt.field_artifact_sha256,
                span_preimage_id=excerpt.span_preimage_id,
                span_preimage_sha256=excerpt.span_preimage_sha256,
                span_field=excerpt.span_field,
                metadata_json_path=excerpt.metadata_json_path,
                span_start_byte=excerpt.span_start_byte,
                span_end_byte=excerpt.span_end_byte,
                span_locator_sha256=excerpt.span_locator_sha256,
            )
        )
    return tuple(values)


def _build_reviewer_release_v2_core(
    *,
    execution_release: ExecutionReleaseV2 | ExecutionReleaseV3,
    pre_run_eligibility_release: (
        PreRunEligibilityReleaseV2 | PreRunEligibilityReleaseV3
    ),
    frozen_case_release: FrozenCaseReleaseV3 | None,
    expert_registry: ExpertStudyRegistryV2,
    evidence_excerpts: tuple[EvidenceExcerptV2, ...],
    blind_key: bytes,
    renderer_sha256: str,
    sealed_at: str,
) -> tuple[tuple[ReviewerManifestV2, ...], tuple[PrivateIdentityMapV2, ...]]:
    if not isinstance(blind_key, bytes) or len(blind_key) < 32:
        raise ValueError("V2 blind key must contain at least 32 bytes")
    if not re.fullmatch(r"[0-9a-f]{64}", renderer_sha256):
        raise ValueError("V2 renderer SHA-256 must be lowercase hexadecimal")
    sealed_at = _require_timestamp(sealed_at)
    registry = _revalidate(expert_registry, ExpertStudyRegistryV2)
    if frozen_case_release is None:
        release = _revalidate(execution_release, ExecutionReleaseV2)
        eligibility = _revalidate(
            pre_run_eligibility_release, PreRunEligibilityReleaseV2
        )
        frozen = eligibility.frozen_case_release
        candidates = frozen.candidates
        assert_pre_run_eligibility_precedes_execution_v2(
            eligibility_release=eligibility, execution_release=release
        )
    else:
        release = _revalidate(execution_release, ExecutionReleaseV3)
        frozen = release.frozen_case_release
        eligibility = release.pre_run_eligibility_release
        if frozen_case_release != frozen:
            raise ValueError("reviewer receives a foreign FrozenCaseReleaseV3")
        if pre_run_eligibility_release != eligibility:
            raise ValueError(
                "reviewer receives a foreign PreRunEligibilityReleaseV3"
            )
        candidates = (
            eligibility.assignment_release.candidate_pool_release.candidates
        )
    assert_expert_registry_covers_split_v2(
        registry=registry, split_manifest=release.execution_matrix.split_manifest
    )
    if frozen.expert_registry != registry:
        raise ValueError("reviewer chain binds a foreign expert registry")
    if isinstance(frozen, FrozenCaseReleaseV3):
        registry = frozen.expert_registry
    if len(release.execution_matrix.split_manifest.cases) != 30:
        raise ValueError("formal V2 reviewer release requires the 30-case Pilot")
    if datetime.fromisoformat(sealed_at.replace("Z", "+00:00")) < datetime.fromisoformat(
        release.assembled_at.replace("Z", "+00:00")
    ):
        raise ValueError("V2 reviewer release was sealed before execution assembly")

    assignments = {item.case_id: item for item in registry.assignments}
    if any(
        len(item.reviewer_ids) != 2 or len(set(item.reviewer_ids)) != 2
        for item in assignments.values()
    ):
        raise ValueError("every formal Pilot case requires two distinct reviewers")
    cells = {item.cell_id: item for item in release.execution_matrix.cells}
    rankings = {item.ranking_id: item for item in release.rankings}
    packets = {item.packet_id: item for item in release.hypothesis_packets}
    positions: list[tuple[object, object, object, HypothesisPacketV1]] = []
    present_case_ids: set[str] = set()
    for projection in release.top5_projections:
        ranking = (
            None if projection.ranking_id is None else rankings[projection.ranking_id]
        )
        for position in projection.positions:
            if position.packet_id is None:
                continue
            if ranking is None:
                raise ValueError("present V2 reviewer position lacks a ranking")
            ranked = ranking.positions[position.position - 1]
            if (ranked.packet_id, ranked.packet_sha256) != (
                position.packet_id,
                position.packet_sha256,
            ):
                raise ValueError("V2 projection differs from its ranking")
            packet = packets[position.packet_id]
            cell = cells[projection.cell_id]
            positions.append((cell, ranking, position, packet))
            present_case_ids.add(cell.case_id)
    expected_case_ids = {
        item.case_id for item in release.execution_matrix.split_manifest.cases
    }
    projections_by_case: dict[str, list[Any]] = {
        case_id: [] for case_id in expected_case_ids
    }
    for projection in release.top5_projections:
        projections_by_case[cells[projection.cell_id].case_id].append(projection)
    for case_id in expected_case_ids - present_case_ids:
        case_projections = projections_by_case[case_id]
        if not case_projections or any(
            item.status is not RunCellStatus.FAILED for item in case_projections
        ):
            raise ValueError(
                "a zero-packet reviewer case requires every system run to fail"
            )
        if any(
            position.packet_id is not None
            or not position.forced_zero
            or position.fixed_gain != 0
            for item in case_projections
            for position in item.positions
        ):
            raise ValueError(
                "an all-systems-failed reviewer case requires exact forced-zero positions"
            )

    excerpts, _authoritative = _validate_evidence_excerpts_v2(
        release=release, evidence_excerpts=evidence_excerpts
    )
    excerpt_by_packet_link: dict[tuple[str, str], EvidenceExcerptV2] = {}
    for (_cell_id, packet_id, link_id), excerpt in sorted(excerpts.items()):
        excerpt_by_packet_link.setdefault((packet_id, link_id), excerpt)

    candidate_by_id = {item.candidate_id: item for item in candidates}
    active_candidate_by_case: dict[str, FrozenCaseCandidateV1] = {}
    for selection in eligibility.active_selections:
        if selection.selected_candidate_id is None:
            raise ValueError("V2 reviewer release contains an empty case slot")
        candidate = candidate_by_id.get(selection.selected_candidate_id)
        if candidate is None or (
            selection.selected_candidate_sha256,
            selection.selected_case_id,
            selection.selected_case_sha256,
        ) != (
            candidate.candidate_sha256,
            candidate.case.case_id,
            candidate.case.case_sha256,
        ):
            raise ValueError("V2 active selection differs from its frozen candidate")
        if candidate.case.case_id in active_candidate_by_case:
            raise ValueError("V2 active selections alias one case")
        active_candidate_by_case[candidate.case.case_id] = candidate
    if set(active_candidate_by_case) != expected_case_ids:
        raise ValueError("V2 active selections do not exactly cover the Pilot")

    reviewer_ids = tuple(
        sorted(
            {
                expert_id
                for assignment in registry.assignments
                for expert_id in assignment.reviewer_ids
            }
        )
    )
    origin_tokens = _origin_tokens(
        release,
        eligibility,
        registry,
        frozen_case_release=(
            frozen if isinstance(frozen, FrozenCaseReleaseV3) else None
        ),
    )
    manifests: list[ReviewerManifestV2] = []
    private_maps: list[PrivateIdentityMapV2] = []
    for expert_id in reviewer_ids:
        assigned_case_ids = tuple(
            sorted(
                case_id
                for case_id, assignment in assignments.items()
                if expert_id in assignment.reviewer_ids
            )
        )
        assigned_candidates = tuple(
            active_candidate_by_case[case_id] for case_id in assigned_case_ids
        )
        case_order = _ordered_cases(
            assigned_candidates, expert_id=expert_id, blind_key=blind_key
        )
        public_cases = tuple(
            _build_reviewer_case_projection_v2(
                candidate=candidate,
                case_display_order=index,
                expert_id=expert_id,
                blind_key=blind_key,
            )
            for index, candidate in enumerate(case_order, start=1)
        )
        public_case_by_original = {
            candidate.case.case_id: projection
            for candidate, projection in zip(case_order, public_cases, strict=True)
        }
        assigned_positions = [
            value
            for value in positions
            if expert_id in assignments[value[0].case_id].reviewer_ids
        ]
        unique_packets = {value[3].packet_id: value[3] for value in assigned_positions}
        packet_order = _ordered_packets(
            tuple(unique_packets.values()), expert_id=expert_id, blind_key=blind_key
        )
        public_packets = tuple(
            _public_reviewer_packet_v2(
                packet=packet,
                case_projection=public_case_by_original[packet.case_id],
                display_order=index,
                excerpt_by_packet_link=excerpt_by_packet_link,
                expert_id=expert_id,
                blind_key=blind_key,
            )
            for index, packet in enumerate(packet_order, start=1)
        )
        public_packet_by_original = {
            original.packet_id: public
            for original, public in zip(packet_order, public_packets, strict=True)
        }
        blinded_reviewer_id = _hmac_id(
            "reviewer-slot-v2", blind_key, {"expert_id": expert_id}
        )
        manifest = _identified(
            ReviewerManifestV2,
            id_field="manifest_id",
            sha_field="manifest_sha256",
            prefix="reviewer-manifest-v2",
            values={
                "blinded_study_id": _hmac_id(
                    "study-slot-v2",
                    blind_key,
                    {
                        "execution_release_sha256": release.release_sha256,
                        "expert_registry_sha256": registry.registry_sha256,
                        "pre_run_eligibility_release_sha256": eligibility.release_sha256,
                    },
                ),
                "blinded_reviewer_id": blinded_reviewer_id,
                "renderer_sha256": renderer_sha256,
                "case_projections": public_cases,
                "packets": public_packets,
                "sealed_at": sealed_at,
            },
        )
        _assert_reviewer_manifest_safe(
            manifest, forbidden_origin_tokens=origin_tokens
        )
        private_case_entries = tuple(
            sorted(
                (
                    PrivateCaseMapEntryV1(
                        reviewer_case_projection_id=public_case_by_original[
                            candidate.case.case_id
                        ].reviewer_case_projection_id,
                        reviewer_case_projection_sha256=public_case_by_original[
                            candidate.case.case_id
                        ].reviewer_case_projection_sha256,
                        blinded_case_id=public_case_by_original[
                            candidate.case.case_id
                        ].blinded_case_id,
                        frozen_candidate_id=candidate.candidate_id,
                        frozen_candidate_sha256=candidate.candidate_sha256,
                        case_id=candidate.case.case_id,
                        case_sha256=candidate.case.case_sha256,
                    )
                    for candidate in assigned_candidates
                ),
                key=lambda item: item.case_id,
            )
        )
        private_entries: list[PrivatePositionMapEntryV2] = []
        for cell, ranking, position, packet in assigned_positions:
            public_packet = public_packet_by_original[packet.packet_id]
            private_entries.append(
                PrivatePositionMapEntryV2(
                    pooled_unit_id=_hmac_id(
                        "pooled-unit-v2",
                        blind_key,
                        {
                            "packet_id": packet.packet_id,
                            "packet_sha256": packet.packet_sha256,
                        },
                    ),
                    reviewer_packet_id=public_packet.reviewer_packet_id,
                    reviewer_packet_sha256=public_packet.reviewer_packet_sha256,
                    blinded_unit_id=public_packet.blinded_unit_id,
                    reviewer_case_projection_id=(
                        public_packet.reviewer_case_projection_id
                    ),
                    cell_id=cell.cell_id,
                    system_id=cell.system_id,
                    system_config_id=cell.system_config_id,
                    system_config_sha256=cell.system_config_sha256,
                    run_id=ranking.run_id,
                    ranking_id=ranking.ranking_id,
                    ranking_sha256=ranking.ranking_sha256,
                    selection_rank=position.position,
                    case_id=packet.case_id,
                    case_sha256=packet.case_sha256,
                    packet_id=packet.packet_id,
                    packet_sha256=packet.packet_sha256,
                    system_proposed_structure_group_id=(
                        packet.strict_structure_group_id
                    ),
                    system_proposed_hypothesis_group_id=(
                        packet.strict_hypothesis_group_id
                    ),
                    evidence_map=_private_evidence_v2(
                        packet=packet,
                        cell_id=cell.cell_id,
                        excerpts=excerpts,
                        expert_id=expert_id,
                        blind_key=blind_key,
                    ),
                )
            )
        private_entries.sort(
            key=lambda item: (
                item.case_id,
                item.system_id.value,
                item.selection_rank,
            )
        )
        private_map = _identified(
            PrivateIdentityMapV2,
            id_field="identity_map_id",
            sha_field="identity_map_sha256",
            prefix="private-identity-map-v2",
            values={
                "reviewer_manifest_id": manifest.manifest_id,
                "reviewer_manifest_sha256": manifest.manifest_sha256,
                "execution_release_id": release.release_id,
                "execution_release_sha256": release.release_sha256,
                "expert_registry_id": registry.registry_id,
                "expert_registry_sha256": registry.registry_sha256,
                "frozen_case_release_id": frozen.release_id,
                "frozen_case_release_sha256": frozen.release_sha256,
                "pre_run_eligibility_release_id": eligibility.release_id,
                "pre_run_eligibility_release_sha256": eligibility.release_sha256,
                "expert_id": expert_id,
                "blinded_reviewer_id": blinded_reviewer_id,
                "case_entries": private_case_entries,
                "entries": tuple(private_entries),
                "sealed_at": sealed_at,
            },
        )
        manifests.append(manifest)
        private_maps.append(private_map)
    return tuple(manifests), tuple(private_maps)


def build_reviewer_release_v2(
    *,
    frozen_case_release: FrozenCaseReleaseV3,
    execution_release: ExecutionReleaseV3,
    pre_run_eligibility_release: PreRunEligibilityReleaseV3,
    expert_registry: ExpertStudyRegistryV2,
    evidence_excerpts: tuple[EvidenceExcerptV2, ...],
    blind_key: bytes,
    renderer_sha256: str,
    sealed_at: str,
) -> tuple[tuple[ReviewerManifestV2, ...], tuple[PrivateIdentityMapV2, ...]]:
    """Build V2 reviewer artifacts from the unique formal V3 upstream."""

    result = _build_reviewer_release_v2_core(
        frozen_case_release=frozen_case_release,
        execution_release=execution_release,
        pre_run_eligibility_release=pre_run_eligibility_release,
        expert_registry=expert_registry,
        evidence_excerpts=evidence_excerpts,
        blind_key=blind_key,
        renderer_sha256=renderer_sha256,
        sealed_at=sealed_at,
    )
    assert_reviewer_release_exact_coverage_v2(
        frozen_case_release=frozen_case_release,
        execution_release=execution_release,
        pre_run_eligibility_release=pre_run_eligibility_release,
        expert_registry=expert_registry,
        reviewer_manifests=result[0],
        private_identity_maps=result[1],
        evidence_excerpts=evidence_excerpts,
        blind_key=blind_key,
        renderer_sha256=renderer_sha256,
    )
    return result


def assert_reviewer_release_exact_coverage_v2(
    *,
    frozen_case_release: FrozenCaseReleaseV3,
    execution_release: ExecutionReleaseV3,
    pre_run_eligibility_release: PreRunEligibilityReleaseV3,
    expert_registry: ExpertStudyRegistryV2,
    reviewer_manifests: tuple[ReviewerManifestV2, ...],
    private_identity_maps: tuple[PrivateIdentityMapV2, ...],
    evidence_excerpts: tuple[EvidenceExcerptV2, ...],
    blind_key: bytes,
    renderer_sha256: str,
) -> None:
    """Exact-replay V2 reviewer artifacts from V3 authoritative inputs."""

    _assert_reviewer_release_exact_coverage_v2_common(
        frozen_case_release=frozen_case_release,
        execution_release=execution_release,
        pre_run_eligibility_release=pre_run_eligibility_release,
        expert_registry=expert_registry,
        reviewer_manifests=reviewer_manifests,
        private_identity_maps=private_identity_maps,
        evidence_excerpts=evidence_excerpts,
        blind_key=blind_key,
        renderer_sha256=renderer_sha256,
    )


def _assert_reviewer_release_exact_coverage_v2_common(
    *,
    frozen_case_release: FrozenCaseReleaseV3 | None,
    execution_release: ExecutionReleaseV2 | ExecutionReleaseV3,
    pre_run_eligibility_release: (
        PreRunEligibilityReleaseV2 | PreRunEligibilityReleaseV3
    ),
    expert_registry: ExpertStudyRegistryV2,
    reviewer_manifests: tuple[ReviewerManifestV2, ...],
    private_identity_maps: tuple[PrivateIdentityMapV2, ...],
    evidence_excerpts: tuple[EvidenceExcerptV2, ...],
    blind_key: bytes,
    renderer_sha256: str,
) -> None:

    manifests = tuple(
        ReviewerManifestV2.model_validate(item) for item in reviewer_manifests
    )
    maps = tuple(PrivateIdentityMapV2.model_validate(item) for item in private_identity_maps)
    if not manifests or not maps:
        raise ValueError("formal V2 reviewer release cannot be empty")
    if len({item.manifest_id for item in manifests}) != len(manifests):
        raise ValueError("formal V2 reviewer release repeats a public reviewer")
    if len({item.expert_id for item in maps}) != len(maps):
        raise ValueError("formal V2 reviewer release repeats a private reviewer")
    if len({item.reviewer_manifest_id for item in maps}) != len(maps):
        raise ValueError("formal V2 private maps alias one public reviewer")
    seals = {item.sealed_at for item in (*manifests, *maps)}
    if len(seals) != 1:
        raise ValueError("formal V2 reviewer artifacts do not share one seal")
    expected = _build_reviewer_release_v2_core(
        frozen_case_release=frozen_case_release,
        execution_release=execution_release,
        pre_run_eligibility_release=pre_run_eligibility_release,
        expert_registry=expert_registry,
        evidence_excerpts=evidence_excerpts,
        blind_key=blind_key,
        renderer_sha256=renderer_sha256,
        sealed_at=next(iter(seals)),
    )
    if (manifests, maps) != expected:
        raise ValueError("formal V2 reviewer artifacts fail exact deterministic replay")


def build_reviewer_release_legacy_v2_upstream(
    *,
    execution_release: ExecutionReleaseV2,
    pre_run_eligibility_release: PreRunEligibilityReleaseV2,
    expert_registry: ExpertStudyRegistryV2,
    evidence_excerpts: tuple[EvidenceExcerptV2, ...],
    blind_key: bytes,
    renderer_sha256: str,
    sealed_at: str,
) -> tuple[tuple[ReviewerManifestV2, ...], tuple[PrivateIdentityMapV2, ...]]:
    """Legacy/non-formal V2-upstream fixture path; never a Pilot root."""

    result = _build_reviewer_release_v2_core(
        frozen_case_release=None,
        execution_release=execution_release,
        pre_run_eligibility_release=pre_run_eligibility_release,
        expert_registry=expert_registry,
        evidence_excerpts=evidence_excerpts,
        blind_key=blind_key,
        renderer_sha256=renderer_sha256,
        sealed_at=sealed_at,
    )
    assert_reviewer_release_legacy_v2_upstream_exact_coverage(
        execution_release=execution_release,
        pre_run_eligibility_release=pre_run_eligibility_release,
        expert_registry=expert_registry,
        reviewer_manifests=result[0],
        private_identity_maps=result[1],
        evidence_excerpts=evidence_excerpts,
        blind_key=blind_key,
        renderer_sha256=renderer_sha256,
    )
    return result


def assert_reviewer_release_legacy_v2_upstream_exact_coverage(
    *,
    execution_release: ExecutionReleaseV2,
    pre_run_eligibility_release: PreRunEligibilityReleaseV2,
    expert_registry: ExpertStudyRegistryV2,
    reviewer_manifests: tuple[ReviewerManifestV2, ...],
    private_identity_maps: tuple[PrivateIdentityMapV2, ...],
    evidence_excerpts: tuple[EvidenceExcerptV2, ...],
    blind_key: bytes,
    renderer_sha256: str,
) -> None:
    """Exact replay for historical V2-upstream fixtures only."""

    _assert_reviewer_release_exact_coverage_v2_common(
        frozen_case_release=None,
        execution_release=execution_release,
        pre_run_eligibility_release=pre_run_eligibility_release,
        expert_registry=expert_registry,
        reviewer_manifests=reviewer_manifests,
        private_identity_maps=private_identity_maps,
        evidence_excerpts=evidence_excerpts,
        blind_key=blind_key,
        renderer_sha256=renderer_sha256,
    )


__all__ = [
    "EvidenceAccessPolicy",
    "EvidenceExcerptV1",
    "EvidenceExcerptV2",
    "EvidenceRedistributionPolicy",
    "EvidenceSpanScope",
    "EvidenceSpanType",
    "MAX_REVIEWER_EXCERPT_CHARS",
    "PostLabelOriginGuessV1",
    "PrivateCaseMapEntryV1",
    "PrivateIdentityMapV1",
    "PrivateIdentityMapV2",
    "PrivatePositionMapEntryV1",
    "PrivatePositionMapEntryV2",
    "PrivateEvidenceMapEntryV2",
    "ReviewerEvidenceSpanV1",
    "ReviewerCaseProjectionV1",
    "ReviewerCaseProjectionV2",
    "ReviewerHypothesisPacketV1",
    "ReviewerHypothesisPacketV2",
    "ReviewerManifestV1",
    "ReviewerManifestV2",
    "assert_reviewer_release_exact_coverage",
    "assert_reviewer_release_exact_coverage_v2",
    "assert_reviewer_release_legacy_v2_upstream_exact_coverage",
    "build_reviewer_release",
    "build_reviewer_release_legacy_v2_upstream",
    "build_reviewer_release_v2",
]
