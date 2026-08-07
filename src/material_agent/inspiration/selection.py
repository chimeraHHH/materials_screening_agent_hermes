"""Deterministic multi-view selection for run-internal inspiration candidates."""

from __future__ import annotations

import math
from collections import Counter
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from material_agent.inspiration.identity import (
    InternalCandidateIdentity,
    build_candidate_scores,
)
from material_agent.inspiration.models import InspirationCandidateV1
from material_agent.inspiration.policy import SelectionPolicyV1


class InspirationSelectionError(ValueError):
    """Fail-closed deterministic selection error with a stable code."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(f"{code}: {message}")


@dataclass(frozen=True, slots=True)
class CandidateDistanceBreakdown:
    """Bounded multi-view distance and its policy-weighted total."""

    structure: float
    composition: float
    route: float
    mechanism: float
    weighted_total: float

    def __post_init__(self) -> None:
        for field_name, value in (
            ("structure", self.structure),
            ("composition", self.composition),
            ("route", self.route),
            ("mechanism", self.mechanism),
            ("weighted_total", self.weighted_total),
        ):
            if not math.isfinite(value) or value < 0.0 or value > 1.0:
                raise InspirationSelectionError(
                    "INVALID_DISTANCE",
                    f"{field_name} distance must be finite and in [0, 1]",
                )


def _jaccard_distance(left: set[str], right: set[str]) -> float:
    union = left | right
    if not union:
        return 0.0
    return 1.0 - len(left & right) / len(union)


def _route_features(identity: InternalCandidateIdentity) -> set[str]:
    features: set[str] = set()
    for route in identity.candidate.merged_routes:
        features.add(f"route:{route.route_sha256}")
        features.add(f"parent:{route.parent_candidate_id}")
        features.update(f"bridge:{item}" for item in route.bridge_packet_ids)
    return features


def _composition_distance(
    left: tuple[tuple[str, float], ...],
    right: tuple[tuple[str, float], ...],
) -> float:
    left_map = dict(left)
    right_map = dict(right)
    components = set(left_map) | set(right_map)
    return 0.5 * math.fsum(
        abs(left_map.get(component, 0.0) - right_map.get(component, 0.0))
        for component in components
    )


def candidate_distance(
    left: InternalCandidateIdentity,
    right: InternalCandidateIdentity,
    *,
    policy: SelectionPolicyV1,
) -> CandidateDistanceBreakdown:
    """Compute structure/composition/route/mechanism distance.

    Structure distance is categorical over the caller-supplied strict group;
    no crystallographic equivalence is inferred here.  Composition uses total
    variation distance, while route and mechanism use Jaccard distance.
    """

    if not isinstance(left, InternalCandidateIdentity) or not isinstance(
        right, InternalCandidateIdentity
    ):
        raise InspirationSelectionError(
            "INVALID_SELECTION_INPUT",
            "distance inputs must be InternalCandidateIdentity records",
        )
    if not isinstance(policy, SelectionPolicyV1):
        raise InspirationSelectionError(
            "INVALID_SELECTION_INPUT",
            "policy must be a SelectionPolicyV1",
        )
    if left.run_id != right.run_id:
        raise InspirationSelectionError(
            "RUN_SCOPE_MISMATCH",
            "candidate distance is defined only inside one run",
        )

    structure = float(
        left.structure_identity.strict_structure_group_id
        != right.structure_identity.strict_structure_group_id
    )
    composition = _composition_distance(
        left.structure_identity.composition_fractions,
        right.structure_identity.composition_fractions,
    )
    route = _jaccard_distance(_route_features(left), _route_features(right))
    mechanism = _jaccard_distance(
        set(left.candidate.mechanism_tag_ids),
        set(right.candidate.mechanism_tag_ids),
    )
    weighted_total = (
        structure * policy.structure_distance_weight
        + composition * policy.composition_distance_weight
        + route * policy.route_distance_weight
        + mechanism * policy.mechanism_distance_weight
    )
    return CandidateDistanceBreakdown(
        structure=structure,
        composition=composition,
        route=route,
        mechanism=mechanism,
        weighted_total=min(1.0, max(0.0, weighted_total)),
    )


def _validate_selection_pool(
    identities: tuple[InternalCandidateIdentity, ...],
    *,
    policy: SelectionPolicyV1,
) -> str | None:
    if not identities:
        return None
    for identity in identities:
        if not isinstance(identity, InternalCandidateIdentity):
            raise InspirationSelectionError(
                "INVALID_SELECTION_INPUT",
                "all selection inputs must be InternalCandidateIdentity records",
            )

    run_ids = {identity.run_id for identity in identities}
    if len(run_ids) != 1:
        raise InspirationSelectionError(
            "RUN_SCOPE_MISMATCH",
            "selection cannot combine candidates from multiple runs",
        )
    candidate_ids = tuple(identity.candidate.candidate_id for identity in identities)
    if len(set(candidate_ids)) != len(candidate_ids):
        raise InspirationSelectionError(
            "DUPLICATE_CANDIDATE_ID",
            "selection candidate IDs must be unique",
        )
    canonical_ids = tuple(
        identity.candidate.canonical_structure_id for identity in identities
    )
    if len(set(canonical_ids)) != len(canonical_ids):
        raise InspirationSelectionError(
            "UNMERGED_EXACT_STRUCTURE",
            "exact structures must be merged before diversity selection",
        )

    policy_ids = {identity.candidate.scores.policy_id for identity in identities}
    if len(policy_ids) != 1:
        raise InspirationSelectionError(
            "POLICY_MISMATCH",
            "all candidates must use one frozen policy ID",
        )
    for identity in identities:
        candidate = identity.candidate
        if candidate.selection_rank is not None:
            raise InspirationSelectionError(
                "ALREADY_RANKED",
                "selection accepts only unranked identity candidates",
            )
        scores = candidate.scores
        expected_weights = (
            policy.quality_weight,
            policy.coverage_weight,
            policy.redundancy_weight,
        )
        actual_weights = (
            scores.quality_weight,
            scores.coverage_weight,
            scores.redundancy_weight,
        )
        if any(
            abs(actual - expected) > 1e-12
            for actual, expected in zip(actual_weights, expected_weights, strict=True)
        ):
            raise InspirationSelectionError(
                "POLICY_MISMATCH",
                "candidate score weights differ from the selection policy",
            )
        if scores.redundancy_penalty != 0.0:
            raise InspirationSelectionError(
                "ALREADY_SCORED_FOR_REDUNDANCY",
                "identity candidates must enter selection with zero redundancy",
            )
    return next(iter(policy_ids))


def _eligible(
    identity: InternalCandidateIdentity,
    *,
    selected_strict_groups: set[str],
    parent_family_counts: Counter[str],
    policy: SelectionPolicyV1,
) -> bool:
    strict_group = identity.structure_identity.strict_structure_group_id
    if strict_group in selected_strict_groups:
        return False
    return all(
        parent_family_counts[parent_family_id] < policy.max_per_parent_family
        for parent_family_id in identity.parent_family_ids
    )


def _redundancy_penalty(
    identity: InternalCandidateIdentity,
    selected: Sequence[InternalCandidateIdentity],
    *,
    policy: SelectionPolicyV1,
) -> float:
    if not selected:
        return 0.0
    closest_distance = min(
        candidate_distance(identity, item, policy=policy).weighted_total
        for item in selected
    )
    return min(1.0, max(0.0, 1.0 - closest_distance))


def _mechanism_group_availability(
    identities: Iterable[InternalCandidateIdentity],
) -> dict[str, int]:
    groups_by_mechanism: dict[str, set[str]] = {}
    for identity in identities:
        strict_group = identity.structure_identity.strict_structure_group_id
        for mechanism in identity.candidate.mechanism_tag_ids:
            groups_by_mechanism.setdefault(mechanism, set()).add(strict_group)
    return {
        mechanism: len(strict_groups)
        for mechanism, strict_groups in groups_by_mechanism.items()
    }


def _rank_candidate(
    identity: InternalCandidateIdentity,
    *,
    policy_id: str,
    policy: SelectionPolicyV1,
    redundancy_penalty: float,
    rank: int,
) -> InspirationCandidateV1:
    base_scores = identity.candidate.scores
    scores = build_candidate_scores(
        policy_id=policy_id,
        policy=policy,
        quality=base_scores.quality,
        evidence_coverage=base_scores.evidence_coverage,
        redundancy_penalty=redundancy_penalty,
    )
    payload = identity.candidate.model_dump(mode="python")
    payload["scores"] = scores
    payload["selection_rank"] = rank
    return InspirationCandidateV1.model_validate(payload)


def select_diverse_candidates(
    identities: Sequence[InternalCandidateIdentity],
    *,
    policy: SelectionPolicyV1,
) -> tuple[InspirationCandidateV1, ...]:
    """Greedily select a deterministic, quota-respecting diverse Top-K.

    Candidate quality and evidence coverage form the positive score.  From the
    second selection onward, maximum similarity to the selected set is the
    redundancy penalty.  Until the bounded pool-level mechanism target is
    covered, eligible candidates adding mechanisms are prioritized; rarer
    mechanisms break coverage ties before the MMR score.  Hard quotas are
    never relaxed, so the result may contain fewer than ``top_k`` candidates.
    """

    if not isinstance(policy, SelectionPolicyV1):
        raise InspirationSelectionError(
            "INVALID_SELECTION_INPUT",
            "policy must be a SelectionPolicyV1",
        )
    materialized = tuple(identities)
    policy_id = _validate_selection_pool(materialized, policy=policy)
    if policy_id is None:
        return ()

    all_mechanisms = {
        mechanism
        for identity in materialized
        for mechanism in identity.candidate.mechanism_tag_ids
    }
    mechanism_target = min(
        policy.min_mechanisms_when_available,
        len(all_mechanisms),
        policy.top_k,
    )

    remaining = {
        identity.candidate.candidate_id: identity for identity in materialized
    }
    selected_identities: list[InternalCandidateIdentity] = []
    selected_candidates: list[InspirationCandidateV1] = []
    selected_strict_groups: set[str] = set()
    parent_family_counts: Counter[str] = Counter()
    covered_mechanisms: set[str] = set()

    while remaining and len(selected_candidates) < policy.top_k:
        eligible = tuple(
            identity
            for identity in remaining.values()
            if _eligible(
                identity,
                selected_strict_groups=selected_strict_groups,
                parent_family_counts=parent_family_counts,
                policy=policy,
            )
        )
        if not eligible:
            break

        scored: list[tuple[InternalCandidateIdentity, float, float]] = []
        for identity in eligible:
            redundancy = _redundancy_penalty(
                identity,
                selected_identities,
                policy=policy,
            )
            score = build_candidate_scores(
                policy_id=policy_id,
                policy=policy,
                quality=identity.candidate.scores.quality,
                evidence_coverage=identity.candidate.scores.evidence_coverage,
                redundancy_penalty=redundancy,
            ).selection_score
            scored.append((identity, redundancy, score))

        # The first MMR choice has no redundancy term and therefore remains
        # the highest-scoring candidate.  Coverage is enforced on subsequent
        # choices, which preserves the usual greedy-MMR anchor semantics.
        coverage_needed = (
            bool(selected_candidates)
            and len(covered_mechanisms) < mechanism_target
        )
        coverage_contenders = [
            item
            for item in scored
            if set(item[0].candidate.mechanism_tag_ids) - covered_mechanisms
        ]
        if coverage_needed and coverage_contenders:
            availability = _mechanism_group_availability(
                item[0] for item in coverage_contenders
            )

            def coverage_key(
                item: tuple[InternalCandidateIdentity, float, float],
            ) -> tuple[float, float, float, str]:
                identity, _, score = item
                new_mechanisms = (
                    set(identity.candidate.mechanism_tag_ids)
                    - covered_mechanisms
                )
                rarity = math.fsum(
                    1.0 / availability[mechanism]
                    for mechanism in new_mechanisms
                )
                return (
                    -float(len(new_mechanisms)),
                    -rarity,
                    -score,
                    identity.candidate.candidate_id,
                )

            chosen_identity, chosen_redundancy, _ = min(
                coverage_contenders,
                key=coverage_key,
            )
        else:
            chosen_identity, chosen_redundancy, _ = min(
                scored,
                key=lambda item: (
                    -item[2],
                    item[0].candidate.candidate_id,
                ),
            )

        rank = len(selected_candidates) + 1
        selected_candidates.append(
            _rank_candidate(
                chosen_identity,
                policy_id=policy_id,
                policy=policy,
                redundancy_penalty=chosen_redundancy,
                rank=rank,
            )
        )
        selected_identities.append(chosen_identity)
        selected_strict_groups.add(
            chosen_identity.structure_identity.strict_structure_group_id
        )
        for parent_family_id in chosen_identity.parent_family_ids:
            parent_family_counts[parent_family_id] += 1
        covered_mechanisms.update(chosen_identity.candidate.mechanism_tag_ids)
        del remaining[chosen_identity.candidate.candidate_id]

    return tuple(selected_candidates)
