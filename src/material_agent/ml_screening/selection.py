"""Stable Agent02 candidate selection."""

from __future__ import annotations

from material_agent.ml_screening.models import (
    ApplicabilityAssessment,
    ApplicabilityStatus,
    MLCandidateInput,
    MLDecision,
    MLScreeningPolicy,
    MLScreeningRequest,
    PlannedCandidate,
    PreFilterEvaluation,
    SelectionMode,
    SelectionStatus,
)


def select_candidates(
    candidates: list[MLCandidateInput],
    pre_filters: dict[str, PreFilterEvaluation],
    applicability: dict[str, ApplicabilityAssessment],
    request: MLScreeningRequest,
    policy: MLScreeningPolicy,
) -> list[PlannedCandidate]:
    """Freeze deterministic execution and non-selection decisions."""

    candidate_ids = {candidate.candidate_id for candidate in candidates}
    requested_ids = set(request.requested_candidate_ids or [])
    unknown = sorted(requested_ids - candidate_ids)
    if unknown:
        raise ValueError(f"requested candidate IDs are not in manifest: {unknown}")

    ordered = sorted(candidates, key=_upstream_order)
    executable = [
        candidate
        for candidate in ordered
        if (
            pre_filters[candidate.candidate_id].decision is MLDecision.PASS
            and applicability[candidate.candidate_id].status
            is ApplicabilityStatus.APPLICABLE
            and (
                request.selection_mode is SelectionMode.POLICY_TOP_N
                or candidate.candidate_id in requested_ids
            )
        )
    ]
    limit = (
        min(request.max_candidates, policy.automatic_max_candidates)
        if request.selection_mode is SelectionMode.POLICY_TOP_N
        else request.max_candidates
    )
    selected_ids = {
        candidate.candidate_id for candidate in executable[:limit]
    }

    planned: list[PlannedCandidate] = []
    for candidate in ordered:
        candidate_id = candidate.candidate_id
        pre_filter = pre_filters[candidate_id]
        assessment = applicability[candidate_id]
        if (
            request.selection_mode is SelectionMode.EXPLICIT_IDS
            and candidate_id not in requested_ids
        ):
            status = SelectionStatus.NOT_REQUESTED
        elif pre_filter.decision in {
            MLDecision.REJECT,
            MLDecision.FAILED,
        }:
            status = SelectionStatus.PREFILTER_REJECTED
        elif pre_filter.decision is MLDecision.UNCERTAIN:
            status = SelectionStatus.PREFILTER_UNCERTAIN
        elif assessment.status is ApplicabilityStatus.NOT_APPLICABLE:
            status = SelectionStatus.NOT_APPLICABLE
        elif assessment.status is ApplicabilityStatus.UNKNOWN:
            status = SelectionStatus.APPLICABILITY_UNKNOWN
        elif candidate_id in selected_ids:
            status = SelectionStatus.SELECTED
        else:
            status = SelectionStatus.NOT_SELECTED_BUDGET
        planned.append(
            PlannedCandidate(
                candidate=candidate,
                pre_filter=pre_filter,
                applicability=assessment,
                selection_status=status,
            )
        )
    return planned


def _upstream_order(candidate: MLCandidateInput) -> tuple[bool, int, str]:
    return (
        candidate.publication_rank is None,
        candidate.publication_rank or 0,
        candidate.candidate_id,
    )
