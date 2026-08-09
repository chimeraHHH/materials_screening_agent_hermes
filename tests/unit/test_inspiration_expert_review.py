from __future__ import annotations

import hashlib

import pytest

from material_agent.inspiration.expert_review import (
    ExpertDecisionKind,
    ExpertReviewError,
    TagGraphReleaseKind,
    build_tag_graph_change_proposal,
    record_expert_tag_decision,
    release_reviewed_tag_graph,
)
from material_agent.inspiration.models import ArtifactPointerV1, TagGraphV1
from material_agent.inspiration.tag_graph import curated_flat_band_tag_graph


def _evidence(name: str = "review.json") -> ArtifactPointerV1:
    payload = f"expert-review-evidence:{name}".encode()
    return ArtifactPointerV1(
        uri=f"artifact://expert-review/{name}",
        sha256=hashlib.sha256(payload).hexdigest(),
        size_bytes=len(payload),
        media_type="application/json",
    )


def _changed_graph(
    base: TagGraphV1,
    *,
    version: str,
    tag_index: int,
    suffix: str,
) -> TagGraphV1:
    tags = list(base.tags)
    tag = tags[tag_index]
    tags[tag_index] = tag.model_copy(
        update={"description": f"{tag.description} Reviewed change {suffix}."}
    )
    return TagGraphV1(
        graph_id=base.graph_id,
        graph_version=version,
        tags=tuple(tags),
        edges=base.edges,
        bridge_rules=base.bridge_rules,
    )


def _proposal(
    base: TagGraphV1,
    proposed: TagGraphV1,
    *,
    timestamp: str = "2026-08-09T10:00:00+08:00",
    rollback_target_release_id: str | None = None,
):
    return build_tag_graph_change_proposal(
        base_graph=base,
        proposed_graph=proposed,
        proposer_id="tag-curator-1",
        rationale="Add a reviewed cross-domain search definition.",
        supporting_evidence=(_evidence(),),
        created_at=timestamp,
        rollback_target_release_id=rollback_target_release_id,
    )


def _decisions(proposal, *, prefix: str = "promotion"):
    return tuple(
        record_expert_tag_decision(
            proposal=proposal,
            reviewer_id=reviewer_id,
            decision=ExpertDecisionKind.ACCEPT,
            rationale="The proposed definition is bounded and scientifically useful.",
            confirmation_reference=f"expert:{prefix}:{reviewer_id}",
            decided_at=f"2026-08-09T1{index}:00:00+08:00",
        )
        for index, reviewer_id in enumerate(("expert-a", "expert-b"), start=1)
    )


def _release(base: TagGraphV1, proposed: TagGraphV1, *, suffix: str):
    proposal = _proposal(base, proposed)
    decisions = _decisions(proposal, prefix=suffix)
    return release_reviewed_tag_graph(
        current_graph=base,
        proposal=proposal,
        decisions=decisions,
        authorized_reviewer_ids=frozenset({"expert-a", "expert-b"}),
        released_at="2026-08-09T14:00:00+08:00",
    )


def test_two_independent_authorized_accepts_create_an_immutable_release() -> None:
    base = curated_flat_band_tag_graph()
    proposed = _changed_graph(
        base,
        version="2026-08-09.2",
        tag_index=0,
        suffix="promotion",
    )
    proposal = _proposal(base, proposed)

    assert proposal.expert_status == "UNKNOWN"
    assert proposal.runtime_applicable is False
    assert proposal.scientific_conclusion is False

    release = release_reviewed_tag_graph(
        current_graph=base,
        proposal=proposal,
        decisions=_decisions(proposal),
        authorized_reviewer_ids=frozenset({"expert-a", "expert-b"}),
        released_at="2026-08-09T14:00:00+08:00",
    )

    assert release.release_kind is TagGraphReleaseKind.PROMOTION
    assert release.graph == proposed
    assert release.reviewer_ids == ("expert-a", "expert-b")
    assert release.expert_review_status == "ACCEPTED"
    assert release.scientific_conclusion is False


def test_stale_unauthorized_duplicate_and_nonaccepting_reviews_fail_closed() -> None:
    base = curated_flat_band_tag_graph()
    proposed = _changed_graph(
        base,
        version="2026-08-09.2",
        tag_index=0,
        suffix="failures",
    )
    proposal = _proposal(base, proposed)
    accepts = _decisions(proposal)
    changed_base = _changed_graph(
        base,
        version="2026-08-09.concurrent",
        tag_index=1,
        suffix="concurrent",
    )

    with pytest.raises(ExpertReviewError, match="STALE_BASE_GRAPH"):
        release_reviewed_tag_graph(
            current_graph=changed_base,
            proposal=proposal,
            decisions=accepts,
            authorized_reviewer_ids=frozenset({"expert-a", "expert-b"}),
            released_at="2026-08-09T14:00:00+08:00",
        )
    with pytest.raises(ExpertReviewError, match="UNAUTHORIZED_REVIEWER"):
        release_reviewed_tag_graph(
            current_graph=base,
            proposal=proposal,
            decisions=accepts,
            authorized_reviewer_ids=frozenset({"expert-a"}),
            released_at="2026-08-09T14:00:00+08:00",
        )
    with pytest.raises(ExpertReviewError, match="DUPLICATE_REVIEWER"):
        release_reviewed_tag_graph(
            current_graph=base,
            proposal=proposal,
            decisions=(accepts[0], accepts[0]),
            authorized_reviewer_ids=frozenset({"expert-a", "expert-b"}),
            released_at="2026-08-09T14:00:00+08:00",
        )
    forged = accepts[1].model_copy(update={"proposal_sha256": "0" * 64})
    with pytest.raises(ExpertReviewError, match="INVALID_REVIEW_CLOSURE"):
        release_reviewed_tag_graph(
            current_graph=base,
            proposal=proposal,
            decisions=(accepts[0], forged),
            authorized_reviewer_ids=frozenset({"expert-a", "expert-b"}),
            released_at="2026-08-09T14:00:00+08:00",
        )

    rejected = record_expert_tag_decision(
        proposal=proposal,
        reviewer_id="expert-b",
        decision=ExpertDecisionKind.REJECT,
        rationale="The analogy is not sufficiently constrained.",
        confirmation_reference="expert:reject:expert-b",
        decided_at="2026-08-09T13:30:00+08:00",
    )
    with pytest.raises(ExpertReviewError, match="EXPERT_REJECTED"):
        release_reviewed_tag_graph(
            current_graph=base,
            proposal=proposal,
            decisions=(accepts[0], rejected),
            authorized_reviewer_ids=frozenset({"expert-a", "expert-b"}),
            released_at="2026-08-09T14:00:00+08:00",
        )


def test_rollback_requires_the_exact_historical_release_and_new_expert_review() -> None:
    graph_v1 = curated_flat_band_tag_graph()
    graph_v2 = _changed_graph(
        graph_v1,
        version="2026-08-09.2",
        tag_index=0,
        suffix="v2",
    )
    release_v2 = _release(graph_v1, graph_v2, suffix="v2")
    graph_v3 = _changed_graph(
        graph_v2,
        version="2026-08-09.3",
        tag_index=1,
        suffix="v3",
    )
    release_v3 = _release(graph_v2, graph_v3, suffix="v3")

    rollback_graph = TagGraphV1(
        graph_id=release_v2.graph.graph_id,
        graph_version="2026-08-09.4-rollback-v2",
        tags=release_v2.graph.tags,
        edges=release_v2.graph.edges,
        bridge_rules=release_v2.graph.bridge_rules,
    )
    rollback = _proposal(
        release_v3.graph,
        rollback_graph,
        timestamp="2026-08-09T15:00:00+08:00",
        rollback_target_release_id=release_v2.release_id,
    )
    rollback_decisions = _decisions(rollback, prefix="rollback")

    with pytest.raises(ExpertReviewError, match="ROLLBACK_TARGET_MISMATCH"):
        release_reviewed_tag_graph(
            current_graph=release_v3.graph,
            proposal=rollback,
            decisions=rollback_decisions,
            authorized_reviewer_ids=frozenset({"expert-a", "expert-b"}),
            released_at="2026-08-09T16:00:00+08:00",
        )

    rolled_back = release_reviewed_tag_graph(
        current_graph=release_v3.graph,
        proposal=rollback,
        decisions=rollback_decisions,
        authorized_reviewer_ids=frozenset({"expert-a", "expert-b"}),
        released_at="2026-08-09T16:00:00+08:00",
        rollback_target_release=release_v2,
    )
    assert rolled_back.release_kind is TagGraphReleaseKind.ROLLBACK
    assert rolled_back.rollback_target_release_id == release_v2.release_id
    assert rolled_back.parent_graph_sha256 == release_v3.graph_sha256
