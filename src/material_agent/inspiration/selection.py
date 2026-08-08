"""Deterministic multi-view selection for run-internal inspiration candidates."""

from __future__ import annotations

import math
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum

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


class MechanismQuotaStatus(StrEnum):
    """Internal outcome of the bounded mechanism-coverage requirement."""

    MET = "MET"
    POOL_INSUFFICIENT = "POOL_INSUFFICIENT"
    HARD_QUOTA_INFEASIBLE = "HARD_QUOTA_INFEASIBLE"


@dataclass(frozen=True, slots=True)
class SelectionAudit:
    """Immutable, run-internal metrics for one deterministic selection.

    ``feasible_mechanism_ids`` is one deterministic jointly selectable witness
    for the largest attainable mechanism target no greater than the requested
    target.  It is intentionally not a scientific assessment of a mechanism.
    """

    requested_top_k: int
    pool_candidate_count: int
    selected_candidate_count: int
    requested_mechanism_count: int
    available_mechanism_count: int
    available_mechanism_ids: tuple[str, ...]
    feasible_mechanism_count: int
    feasible_mechanism_ids: tuple[str, ...]
    achieved_mechanism_count: int
    achieved_mechanism_ids: tuple[str, ...]
    pool_multi_route_group_count: int
    selected_multi_route_group_count: int
    pool_distinct_physical_route_count: int
    selected_distinct_physical_route_count: int
    pool_parent_family_count: int
    selected_parent_family_count: int
    pool_exact_duplicate_count: int
    pool_strict_duplicate_count: int
    selected_exact_duplicate_count: int
    selected_strict_duplicate_count: int
    quota_status: MechanismQuotaStatus
    underfill_reasons: tuple[str, ...]

    def __post_init__(self) -> None:
        for field_name, value in (
            ("requested_top_k", self.requested_top_k),
            ("pool_candidate_count", self.pool_candidate_count),
            ("selected_candidate_count", self.selected_candidate_count),
            ("requested_mechanism_count", self.requested_mechanism_count),
            ("available_mechanism_count", self.available_mechanism_count),
            ("feasible_mechanism_count", self.feasible_mechanism_count),
            ("achieved_mechanism_count", self.achieved_mechanism_count),
            ("pool_multi_route_group_count", self.pool_multi_route_group_count),
            (
                "selected_multi_route_group_count",
                self.selected_multi_route_group_count,
            ),
            (
                "pool_distinct_physical_route_count",
                self.pool_distinct_physical_route_count,
            ),
            (
                "selected_distinct_physical_route_count",
                self.selected_distinct_physical_route_count,
            ),
            ("pool_parent_family_count", self.pool_parent_family_count),
            ("selected_parent_family_count", self.selected_parent_family_count),
            ("pool_exact_duplicate_count", self.pool_exact_duplicate_count),
            ("pool_strict_duplicate_count", self.pool_strict_duplicate_count),
            ("selected_exact_duplicate_count", self.selected_exact_duplicate_count),
            (
                "selected_strict_duplicate_count",
                self.selected_strict_duplicate_count,
            ),
        ):
            if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                raise InspirationSelectionError(
                    "INVALID_SELECTION_AUDIT",
                    f"{field_name} must be a non-negative integer",
                )
        for count, values, label in (
            (
                self.available_mechanism_count,
                self.available_mechanism_ids,
                "available mechanisms",
            ),
            (
                self.feasible_mechanism_count,
                self.feasible_mechanism_ids,
                "feasible mechanisms",
            ),
            (
                self.achieved_mechanism_count,
                self.achieved_mechanism_ids,
                "achieved mechanisms",
            ),
        ):
            if count != len(values) or values != tuple(sorted(set(values))):
                raise InspirationSelectionError(
                    "INVALID_SELECTION_AUDIT",
                    f"{label} must be sorted, unique, and match their count",
                )
        if len(set(self.underfill_reasons)) != len(self.underfill_reasons):
            raise InspirationSelectionError(
                "INVALID_SELECTION_AUDIT",
                "underfill reasons must be unique",
            )


@dataclass(frozen=True, slots=True)
class DiverseSelectionResult:
    """Internal selection payload that leaves the public candidate DTO frozen."""

    candidates: tuple[InspirationCandidateV1, ...]
    audit: SelectionAudit


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


def _base_score_key(identity: InternalCandidateIdentity) -> tuple[float, str]:
    return (
        -identity.candidate.scores.selection_score,
        identity.candidate.candidate_id,
    )


def _coverage_witness(
    identities: tuple[InternalCandidateIdentity, ...],
    *,
    target: int,
    policy: SelectionPolicyV1,
) -> tuple[InternalCandidateIdentity, ...] | None:
    """Find a deterministic hard-quota-compatible coverage witness.

    A minimal coverage witness never needs a candidate that adds no mechanism,
    so search depth is bounded by ``target`` (at most eight in policy v1).  The
    P3.2 two-mechanism path uses an exact bounded single/pair scan.  Higher
    targets use fail-closed depth-first lookahead with a deterministic node
    ceiling rather than silently degrading a required quota.
    """

    if target <= 0:
        return ()
    ordered = tuple(sorted(identities, key=_base_score_key))
    if not ordered:
        return None

    for identity in ordered:
        if len(identity.candidate.mechanism_tag_ids) >= target:
            return (identity,)
    if target == 1:
        return (ordered[0],)
    if target == 2:
        for left_index, left in enumerate(ordered):
            left_group = left.structure_identity.strict_structure_group_id
            left_families = Counter(left.parent_family_ids)
            for right in ordered[left_index + 1 :]:
                if (
                    right.structure_identity.strict_structure_group_id
                    == left_group
                ):
                    continue
                if any(
                    left_families[parent_family_id]
                    >= policy.max_per_parent_family
                    for parent_family_id in right.parent_family_ids
                ):
                    continue
                mechanisms = set(left.candidate.mechanism_tag_ids) | set(
                    right.candidate.mechanism_tag_ids
                )
                if len(mechanisms) >= target:
                    return (left, right)
        return None

    # TransformationPolicyV1 caps the upstream proposal pool at 1,000 and the
    # mechanism target at eight.  This ceiling keeps the generic lookahead
    # deterministic under adversarial overlap while small reviewed catalogs
    # normally finish after only a few nodes.
    max_nodes = 250_000
    visited_nodes = 0

    def visit(
        start: int,
        chosen: tuple[InternalCandidateIdentity, ...],
        strict_groups: frozenset[str],
        family_counts: Counter[str],
        covered: frozenset[str],
    ) -> tuple[InternalCandidateIdentity, ...] | None:
        nonlocal visited_nodes
        visited_nodes += 1
        if visited_nodes > max_nodes:
            raise InspirationSelectionError(
                "FEASIBILITY_BUDGET_EXCEEDED",
                "mechanism-coverage lookahead exceeded its deterministic node budget",
            )
        if len(covered) >= target:
            return chosen
        if len(chosen) >= policy.top_k:
            return None

        potential = set(covered)
        for identity in ordered[start:]:
            if _eligible(
                identity,
                selected_strict_groups=set(strict_groups),
                parent_family_counts=family_counts,
                policy=policy,
            ):
                potential.update(identity.candidate.mechanism_tag_ids)
        if len(potential) < target:
            return None

        for index in range(start, len(ordered)):
            identity = ordered[index]
            mechanisms = frozenset(identity.candidate.mechanism_tag_ids)
            if not mechanisms - covered:
                continue
            if not _eligible(
                identity,
                selected_strict_groups=set(strict_groups),
                parent_family_counts=family_counts,
                policy=policy,
            ):
                continue
            next_counts = family_counts.copy()
            next_counts.update(identity.parent_family_ids)
            witness = visit(
                index + 1,
                (*chosen, identity),
                strict_groups
                | {identity.structure_identity.strict_structure_group_id},
                next_counts,
                covered | mechanisms,
            )
            if witness is not None:
                return witness
        return None

    return visit(0, (), frozenset(), Counter(), frozenset())


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


def select_diverse_candidates_with_audit(
    identities: Sequence[InternalCandidateIdentity],
    *,
    policy: SelectionPolicyV1,
) -> DiverseSelectionResult:
    """Select a deterministic diverse Top-K and return internal audit metrics.

    Candidate quality and evidence coverage form the positive score.  From the
    second selection onward, maximum similarity to the selected set is the
    redundancy penalty.  A bounded feasibility lookahead first reserves a
    jointly selectable mechanism-coverage witness.  This prevents a high-score
    anchor from consuming a parent-family quota needed by a feasible mechanism
    pair.  Hard quotas are never relaxed, so the result may contain fewer than
    ``top_k`` candidates and the audit explains why.
    """

    if not isinstance(policy, SelectionPolicyV1):
        raise InspirationSelectionError(
            "INVALID_SELECTION_INPUT",
            "policy must be a SelectionPolicyV1",
        )
    materialized = tuple(identities)
    policy_id = _validate_selection_pool(materialized, policy=policy)
    all_mechanisms = tuple(
        sorted(
            {
                mechanism
                for identity in materialized
                for mechanism in identity.candidate.mechanism_tag_ids
            }
        )
    )
    requested_mechanisms = policy.min_mechanisms_when_available

    if policy_id is None:
        audit = SelectionAudit(
            requested_top_k=policy.top_k,
            pool_candidate_count=0,
            selected_candidate_count=0,
            requested_mechanism_count=requested_mechanisms,
            available_mechanism_count=0,
            available_mechanism_ids=(),
            feasible_mechanism_count=0,
            feasible_mechanism_ids=(),
            achieved_mechanism_count=0,
            achieved_mechanism_ids=(),
            pool_multi_route_group_count=0,
            selected_multi_route_group_count=0,
            pool_distinct_physical_route_count=0,
            selected_distinct_physical_route_count=0,
            pool_parent_family_count=0,
            selected_parent_family_count=0,
            pool_exact_duplicate_count=0,
            pool_strict_duplicate_count=0,
            selected_exact_duplicate_count=0,
            selected_strict_duplicate_count=0,
            quota_status=MechanismQuotaStatus.POOL_INSUFFICIENT,
            underfill_reasons=(
                "CANDIDATE_POOL_BELOW_TOP_K",
                "MECHANISM_POOL_BELOW_REQUESTED",
            ),
        )
        return DiverseSelectionResult(candidates=(), audit=audit)

    desired_target = min(requested_mechanisms, len(all_mechanisms))
    feasible_target = desired_target
    coverage_seed: tuple[InternalCandidateIdentity, ...] = ()
    while feasible_target > 0:
        witness = _coverage_witness(
            materialized,
            target=feasible_target,
            policy=policy,
        )
        if witness is not None:
            coverage_seed = witness
            break
        feasible_target -= 1

    feasible_mechanisms = tuple(
        sorted(
            {
                mechanism
                for identity in coverage_seed
                for mechanism in identity.candidate.mechanism_tag_ids
            }
        )[:feasible_target]
    )

    remaining = {
        identity.candidate.candidate_id: identity for identity in materialized
    }
    selected_identities: list[InternalCandidateIdentity] = []
    selected_candidates: list[InspirationCandidateV1] = []
    selected_strict_groups: set[str] = set()
    parent_family_counts: Counter[str] = Counter()

    def select_identity(identity: InternalCandidateIdentity) -> None:
        redundancy = _redundancy_penalty(
            identity,
            selected_identities,
            policy=policy,
        )
        selected_candidates.append(
            _rank_candidate(
                identity,
                policy_id=policy_id,
                policy=policy,
                redundancy_penalty=redundancy,
                rank=len(selected_candidates) + 1,
            )
        )
        selected_identities.append(identity)
        selected_strict_groups.add(
            identity.structure_identity.strict_structure_group_id
        )
        parent_family_counts.update(identity.parent_family_ids)
        del remaining[identity.candidate.candidate_id]

    # Every member belongs to one jointly feasible witness.  Their stable base
    # order is part of the feasibility search, and ranking them first preserves
    # the mechanism floor before ordinary MMR can consume a hard quota.
    for identity in coverage_seed:
        select_identity(identity)

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

        chosen_identity, _, _ = min(
            scored,
            key=lambda item: (
                -item[2],
                item[0].candidate.candidate_id,
            ),
        )
        select_identity(chosen_identity)

    achieved_mechanisms = tuple(
        sorted(
            {
                mechanism
                for candidate in selected_candidates
                for mechanism in candidate.mechanism_tag_ids
            }
        )
    )
    if len(all_mechanisms) < requested_mechanisms:
        quota_status = MechanismQuotaStatus.POOL_INSUFFICIENT
    elif feasible_target < requested_mechanisms:
        quota_status = MechanismQuotaStatus.HARD_QUOTA_INFEASIBLE
    else:
        quota_status = MechanismQuotaStatus.MET
        if len(achieved_mechanisms) < requested_mechanisms:
            raise InspirationSelectionError(
                "MECHANISM_QUOTA_NOT_MET",
                "selection lost a mechanism-coverage witness after feasibility",
            )

    selected_ids = {identity.candidate.candidate_id for identity in selected_identities}
    unselected = tuple(
        identity
        for identity in materialized
        if identity.candidate.candidate_id not in selected_ids
    )
    underfill_reasons: list[str] = []
    if len(selected_candidates) < policy.top_k:
        if len(materialized) < policy.top_k:
            underfill_reasons.append("CANDIDATE_POOL_BELOW_TOP_K")
        if any(
            identity.structure_identity.strict_structure_group_id
            in selected_strict_groups
            for identity in unselected
        ):
            underfill_reasons.append("STRICT_STRUCTURE_GROUP_LIMIT")
        if any(
            any(
                parent_family_counts[parent_family_id]
                >= policy.max_per_parent_family
                for parent_family_id in identity.parent_family_ids
            )
            for identity in unselected
        ):
            underfill_reasons.append("PARENT_FAMILY_LIMIT")
    if quota_status is MechanismQuotaStatus.POOL_INSUFFICIENT:
        underfill_reasons.append("MECHANISM_POOL_BELOW_REQUESTED")
    elif quota_status is MechanismQuotaStatus.HARD_QUOTA_INFEASIBLE:
        underfill_reasons.append("MECHANISM_TARGET_INFEASIBLE_UNDER_HARD_QUOTAS")

    selected_canonical_ids = tuple(
        candidate.canonical_structure_id for candidate in selected_candidates
    )
    pool_canonical_ids = tuple(
        identity.candidate.canonical_structure_id for identity in materialized
    )
    pool_strict_ids = tuple(
        identity.structure_identity.strict_structure_group_id
        for identity in materialized
    )
    selected_strict_ids = tuple(
        identity.structure_identity.strict_structure_group_id
        for identity in selected_identities
    )
    audit = SelectionAudit(
        requested_top_k=policy.top_k,
        pool_candidate_count=len(materialized),
        selected_candidate_count=len(selected_candidates),
        requested_mechanism_count=requested_mechanisms,
        available_mechanism_count=len(all_mechanisms),
        available_mechanism_ids=all_mechanisms,
        feasible_mechanism_count=len(feasible_mechanisms),
        feasible_mechanism_ids=feasible_mechanisms,
        achieved_mechanism_count=len(achieved_mechanisms),
        achieved_mechanism_ids=achieved_mechanisms,
        pool_multi_route_group_count=sum(
            len(identity.candidate.merged_routes) > 1 for identity in materialized
        ),
        selected_multi_route_group_count=sum(
            len(candidate.merged_routes) > 1 for candidate in selected_candidates
        ),
        pool_distinct_physical_route_count=len(
            {
                route.route_sha256
                for identity in materialized
                for route in identity.candidate.merged_routes
            }
        ),
        selected_distinct_physical_route_count=len(
            {
                route.route_sha256
                for candidate in selected_candidates
                for route in candidate.merged_routes
            }
        ),
        pool_parent_family_count=len(
            {
                parent_family_id
                for identity in materialized
                for parent_family_id in identity.parent_family_ids
            }
        ),
        selected_parent_family_count=len(
            {
                parent_family_id
                for identity in selected_identities
                for parent_family_id in identity.parent_family_ids
            }
        ),
        pool_exact_duplicate_count=(
            len(pool_canonical_ids) - len(set(pool_canonical_ids))
        ),
        pool_strict_duplicate_count=(
            len(pool_strict_ids) - len(set(pool_strict_ids))
        ),
        selected_exact_duplicate_count=(
            len(selected_canonical_ids) - len(set(selected_canonical_ids))
        ),
        selected_strict_duplicate_count=(
            len(selected_strict_ids) - len(set(selected_strict_ids))
        ),
        quota_status=quota_status,
        underfill_reasons=tuple(underfill_reasons),
    )
    return DiverseSelectionResult(candidates=tuple(selected_candidates), audit=audit)


def select_diverse_candidates(
    identities: Sequence[InternalCandidateIdentity],
    *,
    policy: SelectionPolicyV1,
) -> tuple[InspirationCandidateV1, ...]:
    """Backward-compatible candidate-only wrapper for audited selection."""

    return select_diverse_candidates_with_audit(identities, policy=policy).candidates
