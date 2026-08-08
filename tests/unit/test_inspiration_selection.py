from __future__ import annotations

import hashlib
from dataclasses import FrozenInstanceError, fields

import pytest

from material_agent.inspiration import (
    ArtifactPointerV1,
    CandidateRouteRefV1,
    SelectionPolicyV1,
)
from material_agent.inspiration.identity import (
    CandidateProposalInput,
    InspirationIdentityError,
    InternalCandidateIdentity,
    StrictStructureGroupInput,
    merge_run_internal_candidates,
)
from material_agent.inspiration.selection import (
    CandidateDistanceBreakdown,
    DiverseSelectionResult,
    MechanismQuotaStatus,
    SelectionAudit,
    candidate_distance,
    select_diverse_candidates,
    select_diverse_candidates_with_audit,
)


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _artifact(structure_id: str) -> ArtifactPointerV1:
    return ArtifactPointerV1(
        uri=f"artifact://inspiration/structures/{structure_id}.cif",
        sha256=_digest(f"structure:{structure_id}"),
        media_type="chemical/x-cif",
    )


def _proposal(
    structure_id: str,
    *,
    strict_group: str | None = None,
    composition: tuple[tuple[str, float], ...] = (("S", 0.5), ("Ti", 0.5)),
    route_name: str | None = None,
    plan_id: str | None = None,
    parent_id: str = "parent-1",
    parent_family: str = "family-1",
    mechanisms: tuple[str, ...] = ("mechanism-a",),
    bridges: tuple[str, ...] = ("bridge-a",),
    evidence: tuple[str, ...] = ("evidence-a",),
    quality: float = 0.8,
    coverage: float = 0.7,
    run_id: str = "run-identity",
) -> CandidateProposalInput:
    route_name = route_name or f"route-{structure_id}"
    plan_id = plan_id or f"plan-{route_name}"
    return CandidateProposalInput(
        run_id=run_id,
        structure_identity=StrictStructureGroupInput(
            canonical_structure_id=structure_id,
            strict_structure_group_id=strict_group or f"strict-{structure_id}",
            composition_fractions=composition,
        ),
        structure_artifact=_artifact(structure_id),
        route=CandidateRouteRefV1(
            plan_id=plan_id,
            route_sha256=_digest(route_name),
            parent_candidate_id=parent_id,
            mechanism_tag_ids=tuple(sorted(mechanisms)),
            bridge_packet_ids=tuple(sorted(bridges)),
            evidence_card_ids=tuple(sorted(evidence)),
        ),
        parent_family_id=parent_family,
        quality=quality,
        evidence_coverage=coverage,
        next_falsification_step=f"Compute the cheapest test for {route_name}.",
    )


def _merge(
    *proposals: CandidateProposalInput,
    policy: SelectionPolicyV1 | None = None,
) -> tuple[InternalCandidateIdentity, ...]:
    return merge_run_internal_candidates(
        tuple(proposals),
        run_id="run-identity",
        policy_id="inspiration-default-v1",
        selection_policy=policy or SelectionPolicyV1(),
    )


def test_exact_structure_merges_routes_and_exact_duplicate_is_idempotent() -> None:
    first = _proposal(
        "structure-1",
        route_name="route-low",
        plan_id="plan-low",
        parent_id="parent-2",
        parent_family="family-2",
        mechanisms=("mechanism-b",),
        bridges=("bridge-b",),
        evidence=("evidence-b",),
        quality=0.7,
        coverage=0.6,
    )
    representative = _proposal(
        "structure-1",
        route_name="route-high",
        plan_id="plan-high",
        parent_id="parent-1",
        parent_family="family-1",
        mechanisms=("mechanism-a",),
        bridges=("bridge-a",),
        evidence=("evidence-a",),
        quality=0.9,
        coverage=0.8,
    )

    forward = _merge(first, representative, first)
    reordered = _merge(representative, first, first)

    assert forward == reordered
    assert len(forward) == 1
    identity = forward[0]
    candidate = identity.candidate
    assert len(candidate.merged_routes) == 2
    assert tuple(route.route_sha256 for route in candidate.merged_routes) == tuple(
        sorted((_digest("route-low"), _digest("route-high")))
    )
    assert candidate.representative_plan_id == "plan-high"
    assert candidate.parent_candidate_ids == ("parent-1", "parent-2")
    assert candidate.mechanism_tag_ids == ("mechanism-a", "mechanism-b")
    assert candidate.evidence_card_ids == ("evidence-a", "evidence-b")
    assert identity.parent_family_ids == ("family-1", "family-2")
    assert candidate.scores.quality == 0.9
    assert candidate.scores.evidence_coverage == 0.8
    assert candidate.scores.redundancy_penalty == 0.0
    assert candidate.selection_rank is None


def test_merged_score_is_wholly_traceable_to_the_representative_route() -> None:
    high_quality = _proposal(
        "structure-traceable",
        route_name="route-high-quality",
        plan_id="plan-high-quality",
        quality=0.9,
        coverage=0.2,
    )
    high_coverage = _proposal(
        "structure-traceable",
        route_name="route-high-coverage",
        plan_id="plan-high-coverage",
        quality=0.8,
        coverage=0.95,
    )

    candidate = _merge(high_coverage, high_quality)[0].candidate

    assert candidate.representative_plan_id == "plan-high-quality"
    assert candidate.scores.quality == high_quality.quality
    assert candidate.scores.evidence_coverage == high_quality.evidence_coverage
    assert candidate.next_falsification_step == high_quality.next_falsification_step


def test_conflicting_duplicate_route_and_cross_run_input_fail_closed() -> None:
    proposal = _proposal("structure-1", route_name="same-route")
    conflicting = _proposal(
        "structure-1",
        route_name="same-route",
        quality=0.9,
    )
    with pytest.raises(InspirationIdentityError) as route_error:
        _merge(proposal, conflicting)
    assert route_error.value.code == "ROUTE_IDENTITY_CONFLICT"

    foreign = _proposal("structure-2", run_id="run-other")
    with pytest.raises(InspirationIdentityError) as run_error:
        _merge(proposal, foreign)
    assert run_error.value.code == "RUN_SCOPE_MISMATCH"


def test_strict_group_is_explicit_input_not_an_inferred_structure_match() -> None:
    with pytest.raises(InspirationIdentityError) as composition_error:
        StrictStructureGroupInput(
            canonical_structure_id="structure-invalid",
            strict_structure_group_id="strict-invalid",
            composition_fractions=(("Ti", 0.4), ("S", 0.4)),
        )
    assert composition_error.value.code == "INVALID_COMPOSITION_VIEW"

    first = _proposal("structure-1", strict_group="strict-equivalent")
    second = _proposal(
        "structure-2",
        strict_group="strict-equivalent",
        route_name="route-2",
        parent_family="family-2",
    )
    identities = _merge(first, second)
    assert len(identities) == 2

    selected = select_diverse_candidates(
        identities,
        policy=SelectionPolicyV1(
            top_k=2,
            min_mechanisms_when_available=1,
        ),
    )
    assert len(selected) == 1


def test_multiview_distance_uses_all_frozen_policy_weights() -> None:
    first = _merge(
        _proposal(
            "structure-a",
            strict_group="strict-a",
            route_name="route-a",
            parent_id="parent-shared",
            bridges=("bridge-shared",),
            mechanisms=("mechanism-shared",),
        )
    )[0]
    second = _merge(
        _proposal(
            "structure-b",
            strict_group="strict-b",
            route_name="route-b",
            parent_id="parent-shared",
            bridges=("bridge-shared",),
            mechanisms=("mechanism-shared",),
        )
    )[0]
    disjoint = _merge(
        _proposal(
            "structure-c",
            strict_group="strict-c",
            composition=(("Se", 1.0),),
            route_name="route-c",
            parent_id="parent-other",
            bridges=("bridge-other",),
            mechanisms=("mechanism-other",),
        )
    )[0]
    policy = SelectionPolicyV1()

    partial = candidate_distance(first, second, policy=policy)
    assert partial.structure == 1.0
    assert partial.composition == 0.0
    assert partial.route == pytest.approx(0.5)
    assert partial.mechanism == 0.0
    assert partial.weighted_total == pytest.approx(0.5)

    maximum = candidate_distance(first, disjoint, policy=policy)
    assert maximum.structure == 1.0
    assert maximum.composition == pytest.approx(1.0)
    assert maximum.route == 1.0
    assert maximum.mechanism == 1.0
    assert maximum.weighted_total == pytest.approx(1.0)


def test_selection_is_reorder_stable_and_never_relaxes_hard_quotas() -> None:
    policy = SelectionPolicyV1(
        top_k=5,
        max_per_parent_family=2,
        min_mechanisms_when_available=1,
    )
    identities = _merge(
        _proposal(
            "structure-a",
            strict_group="strict-shared",
            route_name="route-a",
            parent_family="family-limited",
            quality=0.95,
        ),
        _proposal(
            "structure-b",
            strict_group="strict-shared",
            route_name="route-b",
            parent_family="family-other",
            quality=0.94,
        ),
        _proposal(
            "structure-c",
            route_name="route-c",
            parent_family="family-limited",
            quality=0.93,
        ),
        _proposal(
            "structure-d",
            route_name="route-d",
            parent_family="family-limited",
            quality=0.92,
        ),
        policy=policy,
    )

    forward = select_diverse_candidates(identities, policy=policy)
    reverse = select_diverse_candidates(tuple(reversed(identities)), policy=policy)
    assert forward == reverse
    assert len(forward) == 2
    assert tuple(candidate.selection_rank for candidate in forward) == (1, 2)

    metadata = {item.candidate.candidate_id: item for item in identities}
    strict_groups = [
        metadata[candidate.candidate_id].structure_identity.strict_structure_group_id
        for candidate in forward
    ]
    assert len(strict_groups) == len(set(strict_groups))
    limited_family_count = sum(
        "family-limited" in metadata[candidate.candidate_id].parent_family_ids
        for candidate in forward
    )
    assert limited_family_count <= 2

    for candidate in forward:
        scores = candidate.scores
        expected = (
            scores.quality * policy.quality_weight
            + scores.evidence_coverage * policy.coverage_weight
            - scores.redundancy_penalty * policy.redundancy_weight
        )
        assert scores.selection_score == pytest.approx(expected, abs=1e-12)


def test_mechanism_quota_and_mmr_choose_a_diverse_second_candidate() -> None:
    coverage_policy = SelectionPolicyV1(
        top_k=3,
        max_per_parent_family=2,
        min_mechanisms_when_available=2,
    )
    coverage_pool = _merge(
        _proposal(
            "structure-a",
            route_name="route-a",
            parent_family="family-a",
            mechanisms=("mechanism-a",),
            quality=0.95,
        ),
        _proposal(
            "structure-b",
            route_name="route-b",
            parent_family="family-b",
            mechanisms=("mechanism-a",),
            quality=0.94,
        ),
        _proposal(
            "structure-c",
            route_name="route-c",
            parent_family="family-c",
            mechanisms=("mechanism-b",),
            quality=0.30,
        ),
        policy=coverage_policy,
    )
    covered = select_diverse_candidates(coverage_pool, policy=coverage_policy)
    assert covered[0].scores.quality == 0.95
    assert len(
        {
            mechanism
            for candidate in covered
            for mechanism in candidate.mechanism_tag_ids
        }
    ) >= 2

    mmr_policy = SelectionPolicyV1(
        top_k=2,
        max_per_parent_family=2,
        min_mechanisms_when_available=1,
    )
    mmr_pool = _merge(
        _proposal(
            "structure-anchor",
            route_name="route-anchor",
            parent_id="parent-shared",
            parent_family="family-anchor",
            bridges=("bridge-shared",),
            mechanisms=("mechanism-shared",),
            quality=0.95,
            coverage=0.8,
        ),
        _proposal(
            "structure-redundant",
            route_name="route-redundant",
            parent_id="parent-shared",
            parent_family="family-redundant",
            bridges=("bridge-shared",),
            mechanisms=("mechanism-shared",),
            quality=0.90,
            coverage=0.8,
        ),
        _proposal(
            "structure-diverse",
            composition=(("Se", 1.0),),
            route_name="route-diverse",
            parent_id="parent-diverse",
            parent_family="family-diverse",
            bridges=("bridge-diverse",),
            mechanisms=("mechanism-diverse",),
            quality=0.82,
            coverage=0.8,
        ),
        policy=mmr_policy,
    )
    selected = select_diverse_candidates(mmr_pool, policy=mmr_policy)
    assert selected[0].canonical_structure_id == "structure-anchor"
    assert selected[1].canonical_structure_id == "structure-diverse"
    assert selected[1].scores.redundancy_penalty == pytest.approx(0.0)


def test_top5_pool_merges_routes_and_has_zero_exact_or_strict_duplicates() -> None:
    policy = SelectionPolicyV1(
        top_k=5,
        max_per_parent_family=2,
        min_mechanisms_when_available=2,
    )
    route_a1 = _proposal(
        "structure-a",
        strict_group="strict-a",
        route_name="route-a1",
        plan_id="plan-a1",
        parent_id="parent-a1",
        parent_family="family-a",
        mechanisms=("mechanism-a",),
        quality=0.99,
        coverage=0.9,
    )
    route_a2 = _proposal(
        "structure-a",
        strict_group="strict-a",
        route_name="route-a2",
        plan_id="plan-a2",
        parent_id="parent-a2",
        parent_family="family-b",
        mechanisms=("mechanism-a",),
        quality=0.80,
        coverage=0.8,
    )
    proposals = (
        route_a1,
        route_a2,
        route_a1,
        _proposal(
            "structure-b",
            strict_group="strict-b",
            route_name="route-b",
            parent_family="family-a",
            mechanisms=("mechanism-a",),
            quality=0.98,
        ),
        _proposal(
            "structure-c",
            strict_group="strict-shared",
            route_name="route-c",
            parent_family="family-c",
            mechanisms=("mechanism-a",),
            quality=0.97,
        ),
        _proposal(
            "structure-d",
            strict_group="strict-shared",
            route_name="route-d",
            parent_family="family-d",
            mechanisms=("mechanism-b",),
            quality=0.96,
        ),
        _proposal(
            "structure-e",
            strict_group="strict-e",
            route_name="route-e",
            parent_family="family-e",
            mechanisms=("mechanism-b",),
            quality=0.95,
        ),
        _proposal(
            "structure-f",
            strict_group="strict-f",
            route_name="route-f",
            parent_family="family-f",
            mechanisms=("mechanism-a",),
            quality=0.94,
        ),
    )
    identities = _merge(*proposals, policy=policy)

    forward = select_diverse_candidates_with_audit(identities, policy=policy)
    reverse = select_diverse_candidates_with_audit(
        tuple(reversed(identities)),
        policy=policy,
    )

    assert len(proposals) == 8
    assert len(identities) == 6
    merged = next(
        identity
        for identity in identities
        if identity.candidate.canonical_structure_id == "structure-a"
    )
    assert tuple(route.plan_id for route in merged.candidate.merged_routes) == tuple(
        route.plan_id
        for route in sorted(
            (route_a1.route, route_a2.route),
            key=lambda route: route.route_sha256,
        )
    )
    assert merged.parent_family_ids == ("family-a", "family-b")
    assert forward == reverse
    assert len(forward.candidates) == 5
    assert forward.audit.quota_status is MechanismQuotaStatus.MET
    assert forward.audit.available_mechanism_ids == (
        "mechanism-a",
        "mechanism-b",
    )
    assert forward.audit.feasible_mechanism_count == 2
    assert forward.audit.achieved_mechanism_count == 2
    assert forward.audit.pool_multi_route_group_count == 1
    assert forward.audit.selected_multi_route_group_count == 1
    assert forward.audit.pool_distinct_physical_route_count == 7
    assert forward.audit.selected_distinct_physical_route_count == 6
    assert forward.audit.pool_parent_family_count == 6
    assert forward.audit.selected_parent_family_count == 5
    assert forward.audit.pool_exact_duplicate_count == 0
    assert forward.audit.pool_strict_duplicate_count == 1
    assert forward.audit.selected_exact_duplicate_count == 0
    assert forward.audit.selected_strict_duplicate_count == 0
    assert forward.audit.underfill_reasons == ()
    with pytest.raises(FrozenInstanceError):
        forward.audit.selected_candidate_count = 4  # type: ignore[misc]


def test_mechanism_floor_true_and_false_have_explicit_selection_semantics() -> None:
    diverse_policy = SelectionPolicyV1(
        top_k=2,
        max_per_parent_family=2,
        min_mechanisms_when_available=2,
    )
    mmr_only_policy = SelectionPolicyV1(
        top_k=2,
        max_per_parent_family=2,
        min_mechanisms_when_available=1,
    )
    anchor_route = _proposal(
        "semantic-anchor",
        route_name="semantic-route-1",
        plan_id="semantic-plan-1",
        parent_id="shared-parent",
        parent_family="family-a",
        bridges=("shared-bridge",),
        mechanisms=("mechanism-a",),
        quality=1.0,
        coverage=1.0,
    )
    proposals = (
        anchor_route,
        _proposal(
            "semantic-anchor",
            route_name="semantic-route-2",
            plan_id="semantic-plan-2",
            parent_id="shared-parent",
            parent_family="family-a",
            bridges=("shared-bridge",),
            mechanisms=("mechanism-a",),
            quality=0.9,
            coverage=1.0,
        ),
        anchor_route,
        _proposal(
            "semantic-near",
            route_name="semantic-near-route",
            parent_id="shared-parent",
            parent_family="family-b",
            bridges=("shared-bridge",),
            mechanisms=("mechanism-a",),
            quality=0.99,
            coverage=1.0,
        ),
        _proposal(
            "semantic-a3",
            route_name="semantic-a3-route",
            parent_id="shared-parent",
            parent_family="family-c",
            bridges=("shared-bridge",),
            mechanisms=("mechanism-a",),
            quality=0.98,
            coverage=1.0,
        ),
        _proposal(
            "semantic-b",
            route_name="semantic-b-route",
            parent_id="b-parent",
            parent_family="family-d",
            bridges=("b-bridge",),
            mechanisms=("mechanism-b",),
            quality=0.1,
            coverage=1.0,
        ),
    )
    diverse = select_diverse_candidates_with_audit(
        _merge(*proposals, policy=diverse_policy),
        policy=diverse_policy,
    )
    mmr_only = select_diverse_candidates_with_audit(
        _merge(*proposals, policy=mmr_only_policy),
        policy=mmr_only_policy,
    )

    assert diverse.audit.requested_mechanism_count == 2
    assert diverse.audit.achieved_mechanism_ids == (
        "mechanism-a",
        "mechanism-b",
    )
    assert tuple(
        candidate.canonical_structure_id for candidate in diverse.candidates
    ) == ("semantic-anchor", "semantic-b")
    assert mmr_only.audit.requested_mechanism_count == 1
    assert mmr_only.audit.achieved_mechanism_ids == ("mechanism-a",)
    assert tuple(
        candidate.canonical_structure_id for candidate in mmr_only.candidates
    ) == ("semantic-anchor", "semantic-near")
    assert mmr_only.candidates[1].scores.redundancy_penalty > 0.0


def test_feasibility_lookahead_preserves_a_reachable_mechanism_pair() -> None:
    policy = SelectionPolicyV1(
        top_k=2,
        max_per_parent_family=1,
        min_mechanisms_when_available=2,
    )
    identities = _merge(
        _proposal(
            "structure-anchor-a",
            route_name="route-anchor-a",
            parent_family="family-x",
            mechanisms=("mechanism-a",),
            quality=1.0,
            coverage=1.0,
        ),
        _proposal(
            "structure-option-b",
            route_name="route-option-b",
            parent_family="family-x",
            mechanisms=("mechanism-b",),
            quality=0.9,
            coverage=1.0,
        ),
        _proposal(
            "structure-backup-a",
            route_name="route-backup-a",
            parent_family="family-y",
            mechanisms=("mechanism-a",),
            quality=0.8,
            coverage=1.0,
        ),
        policy=policy,
    )

    result = select_diverse_candidates_with_audit(identities, policy=policy)

    assert tuple(
        candidate.canonical_structure_id for candidate in result.candidates
    ) == ("structure-option-b", "structure-backup-a")
    assert result.audit.feasible_mechanism_ids == (
        "mechanism-a",
        "mechanism-b",
    )
    assert result.audit.achieved_mechanism_ids == result.audit.feasible_mechanism_ids
    assert result.audit.quota_status is MechanismQuotaStatus.MET


def test_single_mechanism_pool_reports_pool_insufficient() -> None:
    policy = SelectionPolicyV1(
        top_k=5,
        max_per_parent_family=2,
        min_mechanisms_when_available=2,
    )
    identities = _merge(
        *(
            _proposal(
                f"single-mechanism-{index}",
                route_name=f"single-route-{index}",
                parent_family=f"single-family-{index}",
                mechanisms=("mechanism-a",),
                quality=1.0 - index / 10.0,
            )
            for index in range(3)
        ),
        policy=policy,
    )

    result = select_diverse_candidates_with_audit(identities, policy=policy)

    assert len(result.candidates) == 3
    assert result.audit.available_mechanism_ids == ("mechanism-a",)
    assert result.audit.feasible_mechanism_ids == ("mechanism-a",)
    assert result.audit.achieved_mechanism_ids == ("mechanism-a",)
    assert result.audit.quota_status is MechanismQuotaStatus.POOL_INSUFFICIENT
    assert result.audit.underfill_reasons == (
        "CANDIDATE_POOL_BELOW_TOP_K",
        "MECHANISM_POOL_BELOW_REQUESTED",
    )


def test_jointly_infeasible_mechanisms_report_the_hard_quota() -> None:
    policy = SelectionPolicyV1(
        top_k=2,
        max_per_parent_family=1,
        min_mechanisms_when_available=2,
    )
    identities = _merge(
        _proposal(
            "infeasible-a",
            route_name="infeasible-route-a",
            parent_family="family-only",
            mechanisms=("mechanism-a",),
            quality=0.9,
        ),
        _proposal(
            "infeasible-b",
            route_name="infeasible-route-b",
            parent_family="family-only",
            mechanisms=("mechanism-b",),
            quality=0.8,
        ),
        policy=policy,
    )

    result = select_diverse_candidates_with_audit(identities, policy=policy)

    assert len(result.candidates) == 1
    assert result.audit.available_mechanism_count == 2
    assert result.audit.feasible_mechanism_count == 1
    assert result.audit.achieved_mechanism_count == 1
    assert result.audit.quota_status is MechanismQuotaStatus.HARD_QUOTA_INFEASIBLE
    assert result.audit.underfill_reasons == (
        "PARENT_FAMILY_LIMIT",
        "MECHANISM_TARGET_INFEASIBLE_UNDER_HARD_QUOTAS",
    )


def test_internal_identity_and_selection_models_have_no_prohibited_claim_field() -> None:
    record_types = (
        StrictStructureGroupInput,
        CandidateProposalInput,
        InternalCandidateIdentity,
        CandidateDistanceBreakdown,
        SelectionAudit,
        DiverseSelectionResult,
    )
    field_names = {
        field.name.casefold()
        for record_type in record_types
        for field in fields(record_type)
    }
    assert all("novel" not in field_name for field_name in field_names)
    assert all("prior_art" not in field_name for field_name in field_names)
