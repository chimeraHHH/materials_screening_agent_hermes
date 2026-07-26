from __future__ import annotations

from datetime import UTC, datetime

from material_agent.retrieval.evaluator import evaluate_candidate
from material_agent.retrieval.models import (
    CandidateAuditRecord,
    Decision,
    EvidenceLevel,
    PropertyOrigin,
    PropertyValue,
    ProvenanceStatus,
    RankingMode,
    RankingPreference,
)
from material_agent.retrieval.ranking import rank_and_publish


def make_candidate(material_id: str, band_gap, hull=0.01) -> CandidateAuditRecord:
    origin = PropertyOrigin(
        endpoint="/materials/summary",
        database_version="fixture",
        origin_task_id="task-1",
        run_type="GGA",
        status=ProvenanceStatus.RESOLVED,
    )
    retrieved = datetime.now(UTC)
    properties = [
        PropertyValue(
            name="band_gap",
            value=band_gap,
            unit="eV",
            source="materials_project",
            method="GGA",
            origin=origin,
            retrieved_at=retrieved,
        ),
        PropertyValue(
            name="energy_above_hull",
            value=hull,
            unit="eV/atom",
            source="materials_project",
            method="GGA",
            origin=origin,
            retrieved_at=retrieved,
        ),
        PropertyValue(
            name="is_metal",
            value=False,
            unit="dimensionless",
            source="materials_project",
            method="GGA",
            origin=origin,
            retrieved_at=retrieved,
        ),
        PropertyValue(
            name="num_sites",
            value=2,
            unit="count",
            source="materials_project",
            method="GGA",
            origin=origin,
            retrieved_at=retrieved,
        ),
    ]
    return CandidateAuditRecord(
        candidate_id=f"cand-{material_id}",
        formula="SiO",
        source_database_version="fixture",
        source_material_id=material_id,
        query_id="query",
        structure_id=f"structure-{material_id}",
        elements=["O", "Si"],
        num_sites=2,
        properties=properties,
        evidence_level=EvidenceLevel.L1_RETRIEVED,
        decision=Decision.UNCERTAIN,
    )


def test_missing_hard_property_is_uncertain(requirement, policy) -> None:
    candidate = evaluate_candidate(
        make_candidate("missing", band_gap=None), requirement, policy
    )
    assert candidate.decision is Decision.UNCERTAIN
    assert "band_gap_ev" in candidate.missing_evidence


def test_known_violation_is_rejected_even_if_another_property_is_missing(
    requirement, policy
) -> None:
    candidate = make_candidate("reject", band_gap=None, hull=0.2)
    evaluated = evaluate_candidate(candidate, requirement, policy)
    assert evaluated.decision is Decision.REJECT
    assert "energy_above_hull_ev_atom" in evaluated.unmatched_constraints
    assert "band_gap_ev" in evaluated.missing_evidence


def test_default_ranking_does_not_add_scientific_preference() -> None:
    first = make_candidate("mp-2", band_gap=0.7, hull=0.001).model_copy(
        update={"decision": Decision.PASS}
    )
    second = make_candidate("mp-1", band_gap=0.9, hull=0.04).model_copy(
        update={"decision": Decision.PASS}
    )
    _, published = rank_and_publish([first, second], [], 10)
    assert [candidate.source_material_id for candidate in published] == ["mp-1", "mp-2"]


def test_explicit_stability_ranking_is_applied() -> None:
    first = make_candidate("mp-2", band_gap=0.7, hull=0.001).model_copy(
        update={"decision": Decision.PASS}
    )
    second = make_candidate("mp-1", band_gap=0.9, hull=0.04).model_copy(
        update={"decision": Decision.PASS}
    )
    preference = RankingPreference(
        property="energy_above_hull", mode=RankingMode.MINIMIZE
    )
    _, published = rank_and_publish([second, first], [preference], 10)
    assert [candidate.source_material_id for candidate in published] == ["mp-2", "mp-1"]

