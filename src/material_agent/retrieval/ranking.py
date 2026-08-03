"""Deterministic, explainable candidate ranking."""

from __future__ import annotations

from typing import Any

from material_agent.retrieval.models import (
    CandidateAuditRecord,
    Decision,
    RankingMode,
    RankingPreference,
)


DECISION_ORDER = {
    Decision.PASS: 0,
    Decision.UNCERTAIN: 1,
    Decision.REJECT: 2,
    Decision.FAILED: 3,
}


def rank_and_publish(
    candidates: list[CandidateAuditRecord],
    preferences: list[RankingPreference],
    max_candidates: int,
) -> tuple[list[CandidateAuditRecord], list[CandidateAuditRecord]]:
    # Database evidence gaps are downstream work, not a rejection.  Preserve
    # the distinction in the immutable record and rank PASS before UNCERTAIN;
    # only explicit mismatch/failure is blocked from downstream publication.
    eligible = [
        candidate
        for candidate in candidates
        if candidate.decision in {Decision.PASS, Decision.UNCERTAIN}
    ]
    eligible.sort(key=lambda candidate: ranking_key(candidate, preferences))
    published_ids = {
        candidate.candidate_id for candidate in eligible[:max_candidates]
    }
    rank_by_id = {
        candidate.candidate_id: index
        for index, candidate in enumerate(eligible[:max_candidates], start=1)
    }

    updated = [
        candidate.model_copy(
            update={
                "published_downstream": candidate.candidate_id in published_ids,
                "publication_rank": rank_by_id.get(candidate.candidate_id),
            }
        )
        for candidate in candidates
    ]
    published = sorted(
        [candidate for candidate in updated if candidate.published_downstream],
        key=lambda candidate: candidate.publication_rank or 0,
    )
    return updated, published


def ranking_key(
    candidate: CandidateAuditRecord, preferences: list[RankingPreference]
) -> tuple[Any, ...]:
    key: list[Any] = [DECISION_ORDER[candidate.decision]]
    for preference in preferences:
        value = _property_value(candidate, preference.property)
        if value is None:
            key.extend((1, 0.0))
            continue
        numeric = float(value)
        if preference.mode is RankingMode.MINIMIZE:
            score = numeric
        elif preference.mode is RankingMode.MAXIMIZE:
            score = -numeric
        else:
            score = abs(numeric - float(preference.target))
        key.extend((0, score))
    key.extend(
        (
            len(candidate.missing_evidence),
            candidate.source_material_id,
        )
    )
    return tuple(key)


def _property_value(candidate: CandidateAuditRecord, name: str) -> float | int | None:
    property_name = "num_sites" if name == "num_sites" else name
    for prop in candidate.properties:
        if prop.name == property_name:
            if isinstance(prop.value, bool) or prop.value is None:
                return None
            if isinstance(prop.value, (float, int)):
                return prop.value
    return None
