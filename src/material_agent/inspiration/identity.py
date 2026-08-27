"""Run-internal identity and lineage-preserving candidate consolidation.

This module does not perform database comparison, prior-art search, or any
cross-run judgment.  In particular, it does not pretend to reproduce
``StructureMatcher``: callers must supply the canonical structure ID and the
strict-equivalence group produced by their frozen structure pipeline.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass

from material_agent.inspiration.models import (
    ArtifactPointerV1,
    CandidateRouteRefV1,
    CandidateScoresV1,
    InspirationCandidateV1,
    deterministic_id,
    hypothesis_signature_sha256_for,
)
from material_agent.inspiration.policy import SelectionPolicyV1

_IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_COMPOSITION_COMPONENT_PATTERN = re.compile(r"^[A-Za-z][A-Za-z0-9.+-]{0,31}$")


class InspirationIdentityError(ValueError):
    """Fail-closed run-internal identity error with a stable code."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(f"{code}: {message}")


def _require_identifier(value: str, *, field_name: str) -> None:
    if not isinstance(value, str) or not _IDENTIFIER_PATTERN.fullmatch(value):
        raise InspirationIdentityError(
            "INVALID_IDENTITY_INPUT",
            f"{field_name} must be a bounded identifier",
        )


@dataclass(frozen=True, slots=True)
class StrictStructureGroupInput:
    """Upstream-computed exact and strict structure identity.

    ``strict_structure_group_id`` is an assertion made by a separately pinned
    structure matcher.  This class validates its shape and composition vector,
    but deliberately does not infer crystallographic equivalence.
    """

    canonical_structure_id: str
    strict_structure_group_id: str
    composition_fractions: tuple[tuple[str, float], ...]

    def __post_init__(self) -> None:
        _require_identifier(
            self.canonical_structure_id,
            field_name="canonical_structure_id",
        )
        _require_identifier(
            self.strict_structure_group_id,
            field_name="strict_structure_group_id",
        )
        if not self.composition_fractions:
            raise InspirationIdentityError(
                "INVALID_COMPOSITION_VIEW",
                "composition_fractions must not be empty",
            )

        component_ids: list[str] = []
        total = 0.0
        for item in self.composition_fractions:
            if not isinstance(item, tuple) or len(item) != 2:
                raise InspirationIdentityError(
                    "INVALID_COMPOSITION_VIEW",
                    "each composition entry must be a (component, fraction) tuple",
                )
            component_id, fraction = item
            if (
                not isinstance(component_id, str)
                or not _COMPOSITION_COMPONENT_PATTERN.fullmatch(component_id)
            ):
                raise InspirationIdentityError(
                    "INVALID_COMPOSITION_VIEW",
                    "composition component IDs must be normalized species labels",
                )
            if (
                not isinstance(fraction, float)
                or not math.isfinite(fraction)
                or fraction <= 0.0
                or fraction > 1.0
            ):
                raise InspirationIdentityError(
                    "INVALID_COMPOSITION_VIEW",
                    "composition fractions must be finite floats in (0, 1]",
                )
            component_ids.append(component_id)
            total += fraction

        if tuple(component_ids) != tuple(sorted(component_ids)) or len(
            set(component_ids)
        ) != len(component_ids):
            raise InspirationIdentityError(
                "INVALID_COMPOSITION_VIEW",
                "composition components must be sorted and unique",
            )
        if abs(total - 1.0) > 1e-9:
            raise InspirationIdentityError(
                "INVALID_COMPOSITION_VIEW",
                "composition fractions must sum to one",
            )


@dataclass(frozen=True, slots=True)
class CandidateProposalInput:
    """One validated transformation output entering run-internal identity."""

    run_id: str
    structure_identity: StrictStructureGroupInput
    structure_artifact: ArtifactPointerV1
    route: CandidateRouteRefV1
    parent_family_id: str
    quality: float
    evidence_coverage: float
    next_falsification_step: str

    def __post_init__(self) -> None:
        _require_identifier(self.run_id, field_name="run_id")
        _require_identifier(self.parent_family_id, field_name="parent_family_id")
        if not isinstance(self.structure_identity, StrictStructureGroupInput):
            raise InspirationIdentityError(
                "INVALID_IDENTITY_INPUT",
                "structure_identity must be a StrictStructureGroupInput",
            )
        if not isinstance(self.structure_artifact, ArtifactPointerV1):
            raise InspirationIdentityError(
                "INVALID_IDENTITY_INPUT",
                "structure_artifact must be an ArtifactPointerV1",
            )
        if not isinstance(self.route, CandidateRouteRefV1):
            raise InspirationIdentityError(
                "INVALID_IDENTITY_INPUT",
                "route must be a CandidateRouteRefV1",
            )
        for field_name, value in (
            ("quality", self.quality),
            ("evidence_coverage", self.evidence_coverage),
        ):
            if (
                not isinstance(value, float)
                or not math.isfinite(value)
                or value < 0.0
                or value > 1.0
            ):
                raise InspirationIdentityError(
                    "INVALID_IDENTITY_INPUT",
                    f"{field_name} must be a finite float in [0, 1]",
                )
        if (
            not isinstance(self.next_falsification_step, str)
            or not self.next_falsification_step.strip()
            or len(self.next_falsification_step) > 4_000
        ):
            raise InspirationIdentityError(
                "INVALID_IDENTITY_INPUT",
                "next_falsification_step must be non-empty and bounded",
            )


@dataclass(frozen=True, slots=True)
class InternalCandidateIdentity:
    """Selection metadata kept outside the frozen public candidate contract."""

    run_id: str
    structure_identity: StrictStructureGroupInput
    parent_family_ids: tuple[str, ...]
    candidate: InspirationCandidateV1

    def __post_init__(self) -> None:
        _require_identifier(self.run_id, field_name="run_id")
        if not isinstance(self.structure_identity, StrictStructureGroupInput):
            raise InspirationIdentityError(
                "INVALID_IDENTITY_INPUT",
                "structure_identity must be a StrictStructureGroupInput",
            )
        if not isinstance(self.candidate, InspirationCandidateV1):
            raise InspirationIdentityError(
                "INVALID_IDENTITY_INPUT",
                "candidate must be an InspirationCandidateV1",
            )
        if (
            self.candidate.canonical_structure_id
            != self.structure_identity.canonical_structure_id
        ):
            raise InspirationIdentityError(
                "STRUCTURE_IDENTITY_MISMATCH",
                "candidate canonical structure does not match identity metadata",
            )
        for parent_family_id in self.parent_family_ids:
            _require_identifier(
                parent_family_id,
                field_name="parent_family_id",
            )
        if (
            not self.parent_family_ids
            or tuple(sorted(self.parent_family_ids)) != self.parent_family_ids
            or len(set(self.parent_family_ids)) != len(self.parent_family_ids)
        ):
            raise InspirationIdentityError(
                "INVALID_IDENTITY_INPUT",
                "parent family IDs must be non-empty, sorted, and unique",
            )


def build_candidate_scores(
    *,
    policy_id: str,
    policy: SelectionPolicyV1,
    quality: float,
    evidence_coverage: float,
    redundancy_penalty: float,
) -> CandidateScoresV1:
    """Build scores with exactly the formula frozen by CandidateScoresV1."""

    _require_identifier(policy_id, field_name="policy_id")
    if not isinstance(policy, SelectionPolicyV1):
        raise InspirationIdentityError(
            "INVALID_IDENTITY_INPUT",
            "policy must be a SelectionPolicyV1",
        )
    selection_score = (
        quality * policy.quality_weight
        + evidence_coverage * policy.coverage_weight
        - redundancy_penalty * policy.redundancy_weight
    )
    return CandidateScoresV1(
        policy_id=policy_id,
        quality=quality,
        evidence_coverage=evidence_coverage,
        redundancy_penalty=redundancy_penalty,
        quality_weight=policy.quality_weight,
        coverage_weight=policy.coverage_weight,
        redundancy_weight=policy.redundancy_weight,
        selection_score=selection_score,
    )


def merge_run_internal_candidates(
    proposals: tuple[CandidateProposalInput, ...],
    *,
    run_id: str,
    policy_id: str,
    selection_policy: SelectionPolicyV1,
) -> tuple[InternalCandidateIdentity, ...]:
    """Merge exact structures and duplicate routes inside exactly one run.

    Exact duplicate proposal records are idempotent.  A reused route hash or
    plan ID with conflicting lineage fails closed because the frozen candidate
    contract cannot preserve two meanings for one route identity.
    """

    _require_identifier(run_id, field_name="run_id")
    _require_identifier(policy_id, field_name="policy_id")
    if not isinstance(selection_policy, SelectionPolicyV1):
        raise InspirationIdentityError(
            "INVALID_IDENTITY_INPUT",
            "selection_policy must be a SelectionPolicyV1",
        )
    materialized = tuple(proposals)
    for proposal in materialized:
        if not isinstance(proposal, CandidateProposalInput):
            raise InspirationIdentityError(
                "INVALID_IDENTITY_INPUT",
                "all proposals must be CandidateProposalInput records",
            )
        if proposal.run_id != run_id:
            raise InspirationIdentityError(
                "RUN_SCOPE_MISMATCH",
                "run-internal identity cannot combine records from another run",
            )

    route_owners: dict[str, CandidateProposalInput] = {}
    plan_owners: dict[str, CandidateProposalInput] = {}
    for proposal in materialized:
        existing_route = route_owners.get(proposal.route.route_sha256)
        if existing_route is not None and existing_route != proposal:
            raise InspirationIdentityError(
                "ROUTE_IDENTITY_CONFLICT",
                "one route hash has conflicting proposal lineage",
            )
        route_owners[proposal.route.route_sha256] = proposal

        existing_plan = plan_owners.get(proposal.route.plan_id)
        if existing_plan is not None and existing_plan != proposal:
            raise InspirationIdentityError(
                "PLAN_IDENTITY_CONFLICT",
                "one plan ID has conflicting proposal lineage",
            )
        plan_owners[proposal.route.plan_id] = proposal

    groups: dict[str, list[CandidateProposalInput]] = {}
    for proposal in materialized:
        groups.setdefault(
            proposal.structure_identity.canonical_structure_id,
            [],
        ).append(proposal)

    merged: list[InternalCandidateIdentity] = []
    for canonical_structure_id in sorted(groups):
        group = groups[canonical_structure_id]
        reference = group[0]
        for proposal in group[1:]:
            if proposal.structure_identity != reference.structure_identity:
                raise InspirationIdentityError(
                    "STRUCTURE_IDENTITY_CONFLICT",
                    "one canonical structure ID has conflicting strict-group or "
                    "composition metadata",
                )
            if proposal.structure_artifact != reference.structure_artifact:
                raise InspirationIdentityError(
                    "STRUCTURE_ARTIFACT_CONFLICT",
                    "one canonical structure ID has conflicting artifacts",
                )

        unique_by_route = {
            proposal.route.route_sha256: proposal for proposal in group
        }
        unique_proposals = tuple(unique_by_route.values())
        if len(unique_proposals) > 128:
            raise InspirationIdentityError(
                "TOO_MANY_MERGED_ROUTES",
                "one exact structure exceeds the 128-route candidate limit",
            )
        routes = tuple(
            sorted(
                (proposal.route for proposal in unique_proposals),
                key=lambda route: route.route_sha256,
            )
        )
        representative = min(
            unique_proposals,
            key=lambda proposal: (
                -proposal.quality,
                -proposal.evidence_coverage,
                proposal.route.route_sha256,
                proposal.route.plan_id,
            ),
        )
        parent_candidate_ids = tuple(
            sorted({route.parent_candidate_id for route in routes})
        )
        mechanism_tag_ids = tuple(
            sorted(
                {
                    tag_id
                    for route in routes
                    for tag_id in route.mechanism_tag_ids
                }
            )
        )
        evidence_card_ids = tuple(
            sorted(
                {
                    evidence_card_id
                    for route in routes
                    for evidence_card_id in route.evidence_card_ids
                }
            )
        )
        route_sha256s = tuple(route.route_sha256 for route in routes)
        # Keep the score auditable to the same route that supplies the
        # representative plan and falsification step.  Taking independent
        # maxima here would fabricate a quality/coverage pair that no
        # transformation proposal actually received.
        quality = representative.quality
        evidence_coverage = representative.evidence_coverage
        scores = build_candidate_scores(
            policy_id=policy_id,
            policy=selection_policy,
            quality=quality,
            evidence_coverage=evidence_coverage,
            redundancy_penalty=0.0,
        )
        candidate = InspirationCandidateV1(
            candidate_id=deterministic_id(
                "candidate",
                {
                    "canonical_structure_id": canonical_structure_id,
                    "identity_scope": "run-internal-v1",
                    "run_id": run_id,
                },
            ),
            canonical_structure_id=canonical_structure_id,
            structure_artifact=reference.structure_artifact,
            representative_plan_id=representative.route.plan_id,
            merged_routes=routes,
            parent_candidate_ids=parent_candidate_ids,
            mechanism_tag_ids=mechanism_tag_ids,
            evidence_card_ids=evidence_card_ids,
            hypothesis_signature_sha256=hypothesis_signature_sha256_for(
                canonical_structure_id=canonical_structure_id,
                mechanism_tag_ids=mechanism_tag_ids,
                route_sha256s=route_sha256s,
            ),
            scores=scores,
            next_falsification_step=representative.next_falsification_step,
        )
        merged.append(
            InternalCandidateIdentity(
                run_id=run_id,
                structure_identity=reference.structure_identity,
                parent_family_ids=tuple(
                    sorted(
                        {
                            proposal.parent_family_id
                            for proposal in unique_proposals
                        }
                    )
                ),
                candidate=candidate,
            )
        )

    return tuple(sorted(merged, key=lambda item: item.candidate.candidate_id))
