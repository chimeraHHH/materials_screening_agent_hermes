"""Append-only expert review and release workflow for curated TagGraphs.

Runtime yield feedback is engineering evidence and can only create an UNKNOWN,
non-applicable proposal.  A graph becomes releasable only after independent,
authorized domain experts record exact ACCEPT decisions against the same
proposal bytes.  Promotion and rollback both create new immutable releases;
historical graphs are never rewritten.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Annotated, Literal

from pydantic import Field, field_validator, model_validator

from material_agent.inspiration.models import (
    ArtifactPointerV1,
    Identifier,
    LongText,
    Sha256,
    StrictModel,
    TagGraphV1,
    canonical_json_bytes,
    canonical_sha256,
    deterministic_id,
)


class ExpertReviewError(ValueError):
    """A proposed graph transition lacks an exact, authorized review closure."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(f"{code}: {message}")


class ExpertDecisionKind(StrEnum):
    ACCEPT = "ACCEPT"
    REJECT = "REJECT"
    REQUEST_CHANGES = "REQUEST_CHANGES"


class TagGraphReleaseKind(StrEnum):
    PROMOTION = "PROMOTION"
    ROLLBACK = "ROLLBACK"


def _require_timestamp(value: str) -> str:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("timestamp must be RFC3339-compatible") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("timestamp must include a UTC offset")
    return value


def _proposal_semantics(
    *,
    base_graph: TagGraphV1,
    proposed_graph: TagGraphV1,
    proposer_id: str,
    rationale: str,
    supporting_evidence: tuple[ArtifactPointerV1, ...],
    counterevidence: tuple[ArtifactPointerV1, ...],
    created_at: str,
    rollback_target_release_id: str | None,
) -> dict[str, object]:
    return {
        "base_graph_id": base_graph.graph_id,
        "base_graph_version": base_graph.graph_version,
        "base_graph_sha256": canonical_sha256(base_graph),
        "counterevidence": counterevidence,
        "created_at": created_at,
        "proposed_graph": proposed_graph,
        "proposer_id": proposer_id,
        "rationale": rationale,
        "rollback_target_release_id": rollback_target_release_id,
        "schema_version": "inspiration-tag-change-proposal-v1",
        "supporting_evidence": supporting_evidence,
    }


class TagGraphChangeProposalV1(StrictModel):
    schema_version: Literal["inspiration-tag-change-proposal-v1"] = (
        "inspiration-tag-change-proposal-v1"
    )
    proposal_id: Identifier
    base_graph_id: Identifier
    base_graph_version: Annotated[str, Field(min_length=1, max_length=512)]
    base_graph_sha256: Sha256
    proposed_graph: TagGraphV1
    proposer_id: Identifier
    rationale: LongText
    supporting_evidence: Annotated[
        tuple[ArtifactPointerV1, ...], Field(min_length=1, max_length=64)
    ]
    counterevidence: Annotated[tuple[ArtifactPointerV1, ...], Field(max_length=64)] = ()
    created_at: Annotated[str, Field(min_length=20, max_length=40)]
    rollback_target_release_id: Identifier | None = None
    expert_status: Literal["UNKNOWN"] = "UNKNOWN"
    runtime_applicable: Literal[False] = False
    scientific_conclusion: Literal[False] = False

    @field_validator("created_at")
    @classmethod
    def validate_created_at(cls, value: str) -> str:
        return _require_timestamp(value)

    @model_validator(mode="after")
    def validate_proposal(self) -> TagGraphChangeProposalV1:
        if self.proposed_graph.graph_id != self.base_graph_id:
            raise ValueError("proposed graph must retain the graph ID")
        if self.proposed_graph.graph_version == self.base_graph_version:
            raise ValueError("proposed graph must use a new graph version")
        for label, pointers in (
            ("supporting evidence", self.supporting_evidence),
            ("counterevidence", self.counterevidence),
        ):
            keys = tuple((item.uri, item.sha256) for item in pointers)
            if keys != tuple(sorted(set(keys))):
                raise ValueError(f"{label} pointers must be sorted and unique")
        expected_id = deterministic_id(
            "tag-change-proposal",
            {
                "base_graph_id": self.base_graph_id,
                "base_graph_sha256": self.base_graph_sha256,
                "base_graph_version": self.base_graph_version,
                "counterevidence": self.counterevidence,
                "created_at": self.created_at,
                "proposed_graph": self.proposed_graph,
                "proposer_id": self.proposer_id,
                "rationale": self.rationale,
                "rollback_target_release_id": self.rollback_target_release_id,
                "schema_version": self.schema_version,
                "supporting_evidence": self.supporting_evidence,
            },
        )
        if self.proposal_id != expected_id:
            raise ValueError("proposal ID does not match its immutable semantics")
        return self


class ExpertTagDecisionV1(StrictModel):
    schema_version: Literal["inspiration-expert-tag-decision-v1"] = (
        "inspiration-expert-tag-decision-v1"
    )
    decision_id: Identifier
    proposal_id: Identifier
    proposal_sha256: Sha256
    reviewer_id: Identifier
    reviewer_role: Literal["DOMAIN_EXPERT"] = "DOMAIN_EXPERT"
    decision: ExpertDecisionKind
    rationale: LongText
    confirmation_reference: Annotated[str, Field(min_length=3, max_length=256)]
    decided_at: Annotated[str, Field(min_length=20, max_length=40)]
    scientific_conclusion: Literal[False] = False

    @field_validator("decided_at")
    @classmethod
    def validate_decided_at(cls, value: str) -> str:
        return _require_timestamp(value)

    @field_validator("confirmation_reference")
    @classmethod
    def validate_confirmation_reference(cls, value: str) -> str:
        if not value[0].isalnum() or any(
            character not in "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789._:/-"
            for character in value
        ):
            raise ValueError("confirmation reference is not a bounded opaque ID")
        return value

    @model_validator(mode="after")
    def validate_decision_id(self) -> ExpertTagDecisionV1:
        expected_id = deterministic_id(
            "expert-tag-decision",
            {
                "confirmation_reference": self.confirmation_reference,
                "decided_at": self.decided_at,
                "decision": self.decision,
                "proposal_id": self.proposal_id,
                "proposal_sha256": self.proposal_sha256,
                "rationale": self.rationale,
                "reviewer_id": self.reviewer_id,
                "reviewer_role": self.reviewer_role,
                "schema_version": self.schema_version,
            },
        )
        if self.decision_id != expected_id:
            raise ValueError("decision ID does not match its immutable semantics")
        return self


class ReviewedTagGraphReleaseV1(StrictModel):
    schema_version: Literal["inspiration-reviewed-tag-graph-release-v1"] = (
        "inspiration-reviewed-tag-graph-release-v1"
    )
    release_id: Identifier
    release_kind: TagGraphReleaseKind
    graph: TagGraphV1
    graph_sha256: Sha256
    parent_graph_sha256: Sha256
    proposal_id: Identifier
    proposal_sha256: Sha256
    decision_ids: Annotated[tuple[Identifier, ...], Field(min_length=2, max_length=16)]
    reviewer_ids: Annotated[tuple[Identifier, ...], Field(min_length=2, max_length=16)]
    released_at: Annotated[str, Field(min_length=20, max_length=40)]
    rollback_target_release_id: Identifier | None = None
    expert_review_status: Literal["ACCEPTED"] = "ACCEPTED"
    scientific_conclusion: Literal[False] = False

    @field_validator("released_at")
    @classmethod
    def validate_released_at(cls, value: str) -> str:
        return _require_timestamp(value)

    @model_validator(mode="after")
    def validate_release(self) -> ReviewedTagGraphReleaseV1:
        if self.decision_ids != tuple(sorted(set(self.decision_ids))):
            raise ValueError("decision IDs must be sorted and unique")
        if self.reviewer_ids != tuple(sorted(set(self.reviewer_ids))):
            raise ValueError("reviewer IDs must be sorted and unique")
        if len(self.decision_ids) != len(self.reviewer_ids):
            raise ValueError("each release decision must come from one reviewer")
        if (self.release_kind is TagGraphReleaseKind.ROLLBACK) != (
            self.rollback_target_release_id is not None
        ):
            raise ValueError("rollback target must be present only for rollback")
        if self.graph_sha256 != canonical_sha256(self.graph):
            raise ValueError("released graph SHA-256 is inconsistent")
        expected_id = deterministic_id(
            "reviewed-tag-graph-release",
            {
                "decision_ids": self.decision_ids,
                "graph_sha256": self.graph_sha256,
                "parent_graph_sha256": self.parent_graph_sha256,
                "proposal_id": self.proposal_id,
                "proposal_sha256": self.proposal_sha256,
                "release_kind": self.release_kind,
                "released_at": self.released_at,
                "reviewer_ids": self.reviewer_ids,
                "rollback_target_release_id": self.rollback_target_release_id,
                "schema_version": self.schema_version,
            },
        )
        if self.release_id != expected_id:
            raise ValueError("release ID does not match its immutable semantics")
        return self


def build_tag_graph_change_proposal(
    *,
    base_graph: TagGraphV1,
    proposed_graph: TagGraphV1,
    proposer_id: str,
    rationale: str,
    supporting_evidence: tuple[ArtifactPointerV1, ...],
    created_at: str,
    counterevidence: tuple[ArtifactPointerV1, ...] = (),
    rollback_target_release_id: str | None = None,
) -> TagGraphChangeProposalV1:
    """Build one UNKNOWN proposal; this function cannot approve or apply it."""

    if not isinstance(base_graph, TagGraphV1) or not isinstance(
        proposed_graph, TagGraphV1
    ):
        raise ExpertReviewError("INVALID_GRAPH", "base and proposed graphs are required")
    ordered_support = tuple(
        sorted(supporting_evidence, key=lambda item: (item.uri, item.sha256))
    )
    ordered_counter = tuple(
        sorted(counterevidence, key=lambda item: (item.uri, item.sha256))
    )
    semantic = _proposal_semantics(
        base_graph=base_graph,
        proposed_graph=proposed_graph,
        proposer_id=proposer_id,
        rationale=rationale,
        supporting_evidence=ordered_support,
        counterevidence=ordered_counter,
        created_at=created_at,
        rollback_target_release_id=rollback_target_release_id,
    )
    return TagGraphChangeProposalV1(
        proposal_id=deterministic_id("tag-change-proposal", semantic),
        base_graph_id=base_graph.graph_id,
        base_graph_version=base_graph.graph_version,
        base_graph_sha256=canonical_sha256(base_graph),
        proposed_graph=proposed_graph,
        proposer_id=proposer_id,
        rationale=rationale,
        supporting_evidence=ordered_support,
        counterevidence=ordered_counter,
        created_at=created_at,
        rollback_target_release_id=rollback_target_release_id,
    )


def record_expert_tag_decision(
    *,
    proposal: TagGraphChangeProposalV1,
    reviewer_id: str,
    decision: ExpertDecisionKind,
    rationale: str,
    confirmation_reference: str,
    decided_at: str,
) -> ExpertTagDecisionV1:
    proposal_sha256 = canonical_sha256(proposal)
    semantics = {
        "confirmation_reference": confirmation_reference,
        "decided_at": decided_at,
        "decision": decision,
        "proposal_id": proposal.proposal_id,
        "proposal_sha256": proposal_sha256,
        "rationale": rationale,
        "reviewer_id": reviewer_id,
        "reviewer_role": "DOMAIN_EXPERT",
        "schema_version": "inspiration-expert-tag-decision-v1",
    }
    return ExpertTagDecisionV1(
        decision_id=deterministic_id("expert-tag-decision", semantics),
        proposal_id=proposal.proposal_id,
        proposal_sha256=proposal_sha256,
        reviewer_id=reviewer_id,
        decision=decision,
        rationale=rationale,
        confirmation_reference=confirmation_reference,
        decided_at=decided_at,
    )


def release_reviewed_tag_graph(
    *,
    current_graph: TagGraphV1,
    proposal: TagGraphChangeProposalV1,
    decisions: tuple[ExpertTagDecisionV1, ...],
    authorized_reviewer_ids: frozenset[str],
    released_at: str,
    minimum_independent_accepts: int = 2,
    rollback_target_release: ReviewedTagGraphReleaseV1 | None = None,
) -> ReviewedTagGraphReleaseV1:
    """Promote or roll back only an exact proposal accepted by independent experts."""

    if not isinstance(current_graph, TagGraphV1):
        raise ExpertReviewError("INVALID_GRAPH", "current graph is invalid")
    if not isinstance(proposal, TagGraphChangeProposalV1):
        raise ExpertReviewError("INVALID_PROPOSAL", "proposal is invalid")
    if not isinstance(decisions, tuple) or any(
        not isinstance(item, ExpertTagDecisionV1) for item in decisions
    ):
        raise ExpertReviewError("INVALID_REVIEWS", "decisions must be a strict tuple")
    try:
        current_graph = TagGraphV1.model_validate_json(
            canonical_json_bytes(current_graph)
        )
        proposal = TagGraphChangeProposalV1.model_validate_json(
            canonical_json_bytes(proposal)
        )
        decisions = tuple(
            ExpertTagDecisionV1.model_validate_json(canonical_json_bytes(item))
            for item in decisions
        )
        if rollback_target_release is not None:
            rollback_target_release = ReviewedTagGraphReleaseV1.model_validate_json(
                canonical_json_bytes(rollback_target_release)
            )
    except (TypeError, ValueError) as exc:
        raise ExpertReviewError(
            "INVALID_REVIEW_CLOSURE", "review inputs fail strict revalidation"
        ) from exc
    if not 2 <= minimum_independent_accepts <= 16:
        raise ExpertReviewError(
            "INVALID_REVIEW_POLICY", "independent accept count must be 2..16"
        )
    current_sha256 = canonical_sha256(current_graph)
    if (
        proposal.base_graph_id != current_graph.graph_id
        or proposal.base_graph_version != current_graph.graph_version
        or proposal.base_graph_sha256 != current_sha256
    ):
        raise ExpertReviewError(
            "STALE_BASE_GRAPH", "proposal is not based on the current graph bytes"
        )
    if canonical_sha256(proposal.proposed_graph) == current_sha256:
        raise ExpertReviewError("NO_GRAPH_CHANGE", "proposal does not change the graph")
    if proposal.rollback_target_release_id is None:
        if rollback_target_release is not None:
            raise ExpertReviewError(
                "UNEXPECTED_ROLLBACK_TARGET",
                "promotion proposal cannot name a rollback target release",
            )
    else:
        if (
            rollback_target_release is None
            or rollback_target_release.release_id
            != proposal.rollback_target_release_id
        ):
            raise ExpertReviewError(
                "ROLLBACK_TARGET_MISMATCH",
                "rollback proposal lacks its exact historical release",
            )
        proposed_semantics = proposal.proposed_graph.model_dump(mode="json")
        target_semantics = rollback_target_release.graph.model_dump(mode="json")
        proposed_semantics.pop("graph_version")
        target_semantics.pop("graph_version")
        if proposed_semantics != target_semantics:
            raise ExpertReviewError(
                "ROLLBACK_CONTENT_MISMATCH",
                "rollback graph differs from the named historical release",
            )
    if not decisions:
        raise ExpertReviewError("MISSING_REVIEWS", "proposal has no expert decisions")
    proposal_sha256 = canonical_sha256(proposal)
    reviewer_ids = tuple(item.reviewer_id for item in decisions)
    if len(set(reviewer_ids)) != len(reviewer_ids):
        raise ExpertReviewError(
            "DUPLICATE_REVIEWER", "each reviewer may decide a proposal only once"
        )
    unauthorized = sorted(set(reviewer_ids) - authorized_reviewer_ids)
    if unauthorized:
        raise ExpertReviewError(
            "UNAUTHORIZED_REVIEWER", f"unauthorized reviewers: {unauthorized!r}"
        )
    for item in decisions:
        if (
            item.proposal_id != proposal.proposal_id
            or item.proposal_sha256 != proposal_sha256
        ):
            raise ExpertReviewError(
                "REVIEW_BINDING_MISMATCH", "expert decision references another proposal"
            )
    if any(item.decision is ExpertDecisionKind.REJECT for item in decisions):
        raise ExpertReviewError("EXPERT_REJECTED", "an expert rejected the proposal")
    if any(
        item.decision is ExpertDecisionKind.REQUEST_CHANGES for item in decisions
    ):
        raise ExpertReviewError(
            "CHANGES_REQUESTED", "an expert requested proposal changes"
        )
    accepts = tuple(
        item for item in decisions if item.decision is ExpertDecisionKind.ACCEPT
    )
    if len(accepts) < minimum_independent_accepts:
        raise ExpertReviewError(
            "INSUFFICIENT_ACCEPTS",
            "proposal lacks the required independent expert accepts",
        )
    ordered_decisions = tuple(sorted(accepts, key=lambda item: item.decision_id))
    decision_ids = tuple(item.decision_id for item in ordered_decisions)
    accepted_reviewer_ids = tuple(sorted(item.reviewer_id for item in ordered_decisions))
    release_kind = (
        TagGraphReleaseKind.ROLLBACK
        if proposal.rollback_target_release_id is not None
        else TagGraphReleaseKind.PROMOTION
    )
    graph_sha256 = canonical_sha256(proposal.proposed_graph)
    release_semantics = {
        "decision_ids": decision_ids,
        "graph_sha256": graph_sha256,
        "parent_graph_sha256": current_sha256,
        "proposal_id": proposal.proposal_id,
        "proposal_sha256": proposal_sha256,
        "release_kind": release_kind,
        "released_at": released_at,
        "reviewer_ids": accepted_reviewer_ids,
        "rollback_target_release_id": proposal.rollback_target_release_id,
        "schema_version": "inspiration-reviewed-tag-graph-release-v1",
    }
    return ReviewedTagGraphReleaseV1(
        release_id=deterministic_id(
            "reviewed-tag-graph-release", release_semantics
        ),
        release_kind=release_kind,
        graph=proposal.proposed_graph,
        graph_sha256=graph_sha256,
        parent_graph_sha256=current_sha256,
        proposal_id=proposal.proposal_id,
        proposal_sha256=proposal_sha256,
        decision_ids=decision_ids,
        reviewer_ids=accepted_reviewer_ids,
        released_at=released_at,
        rollback_target_release_id=proposal.rollback_target_release_id,
    )
