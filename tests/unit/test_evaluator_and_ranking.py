from __future__ import annotations

from datetime import UTC, datetime

from material_agent.retrieval.evaluator import evaluate_candidate
from material_agent.retrieval.models import (
    CandidateAuditRecord,
    Decision,
    EvidenceLevel,
    MaterialsProjectConstraints,
    PropertyOrigin,
    PropertyValue,
    ProvenanceStatus,
    RankingMode,
    RankingPreference,
    SourceSpecificConstraints,
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


def test_include_all_of_and_exclude_none_of_are_enforced(
    requirement, policy
) -> None:
    hard = requirement.hard_constraints.model_copy(
        update={
            "include_elements": ["O", "Si"],
            "exclude_elements": ["C"],
        }
    )
    changed = requirement.model_copy(update={"hard_constraints": hard})
    matching = evaluate_candidate(
        make_candidate("match", 0.7), changed, policy
    )
    missing = evaluate_candidate(
        make_candidate("missing", 0.7).model_copy(update={"elements": ["Si"]}),
        changed,
        policy,
    )
    excluded = evaluate_candidate(
        make_candidate("excluded", 0.7).model_copy(
            update={"elements": ["C", "O", "Si"]}
        ),
        changed,
        policy,
    )
    assert matching.decision is Decision.PASS
    assert missing.decision is Decision.REJECT
    assert excluded.decision is Decision.REJECT


def test_exact_formula_matches_canonical_composition_and_rejects_other_formula(
    requirement, policy
) -> None:
    hard = requirement.hard_constraints.model_copy(update={"exact_formula": "O Si"})
    changed = requirement.model_copy(update={"hard_constraints": hard})
    matching = evaluate_candidate(make_candidate("formula-match", 0.7), changed, policy)
    mismatching = evaluate_candidate(
        make_candidate("formula-mismatch", 0.7).model_copy(
            update={"formula": "SiO2", "reduced_formula": "SiO2"}
        ),
        changed,
        policy,
    )
    assert matching.decision is Decision.PASS
    assert mismatching.decision is Decision.REJECT
    assert "exact_formula" in mismatching.unmatched_constraints


def test_missing_exact_formula_is_uncertain(requirement, policy) -> None:
    hard = requirement.hard_constraints.model_copy(update={"exact_formula": "SiO"})
    changed = requirement.model_copy(update={"hard_constraints": hard})
    candidate = make_candidate("formula-missing", 0.7).model_copy(
        update={"formula": "", "reduced_formula": None}
    )
    evaluated = evaluate_candidate(candidate, changed, policy)
    assert evaluated.decision is Decision.UNCERTAIN
    assert "exact_formula" in evaluated.missing_evidence


def test_materials_project_specific_constraints_are_deterministic(
    requirement, policy
) -> None:
    hard = requirement.hard_constraints.model_copy(
        update={
            "source_constraints": SourceSpecificConstraints(
                materials_project=MaterialsProjectConstraints(
                    density_g_cm3={"min": 2.0, "max": 3.0, "unit": "g/cm^3"},
                    is_stable=True,
                    crystal_system="cubic",
                )
            )
        }
    )
    changed = requirement.model_copy(update={"hard_constraints": hard})
    base = make_candidate("mp-specific", 0.7)
    origin = base.properties[0].origin
    extra = [
        PropertyValue(
            name="density", value=2.5, unit="g/cm^3", source="materials_project",
            method="fixture", origin=origin, retrieved_at=datetime.now(UTC),
        ),
        PropertyValue(
            name="is_stable", value=True, unit="dimensionless", source="materials_project",
            method="fixture", origin=origin, retrieved_at=datetime.now(UTC),
        ),
        PropertyValue(
            name="crystal_system", value="CUBIC", unit="label", source="materials_project",
            method="fixture", origin=origin, retrieved_at=datetime.now(UTC),
        ),
    ]
    assert evaluate_candidate(base.model_copy(update={"properties": [*base.properties, *extra]}), changed, policy).decision is Decision.PASS
    rejected = base.model_copy(update={"properties": [*base.properties, *extra[:1]]})
    assert evaluate_candidate(rejected, changed, policy).decision is Decision.UNCERTAIN


def test_numeric_boundaries_are_closed_with_fixed_tolerance(
    requirement, policy
) -> None:
    within_tolerance = evaluate_candidate(
        make_candidate("edge", 0.5 - 0.5e-8), requirement, policy
    )
    outside_tolerance = evaluate_candidate(
        make_candidate("outside", 0.5 - 2e-8), requirement, policy
    )
    upper_edge = evaluate_candidate(
        make_candidate("upper", 1.0), requirement, policy
    )
    assert within_tolerance.decision is Decision.PASS
    assert outside_tolerance.decision is Decision.REJECT
    assert upper_edge.decision is Decision.PASS


def test_num_sites_and_dimensionality_constraints(requirement, policy) -> None:
    origin = make_candidate("base", 0.7).properties[0].origin
    dimensionality = PropertyValue(
        name="structural_dimensionality",
        value=2,
        unit="dimensionless",
        source="derived_from_mp_structure",
        method="fixture",
        origin=origin,
        retrieved_at=datetime.now(UTC),
        is_derived=True,
        derived_from_structure_id="structure-base",
        derivation_policy_version="fixture",
    )
    candidate = make_candidate("dimensional", 0.7).model_copy(
        update={"properties": [*make_candidate("dimensional", 0.7).properties, dimensionality]}
    )
    hard = requirement.hard_constraints.model_copy(
        update={"max_num_sites": 2, "dimensionality": 2}
    )
    matching = requirement.model_copy(update={"hard_constraints": hard})
    assert evaluate_candidate(candidate, matching, policy).decision is Decision.PASS

    wrong_dimension = matching.model_copy(
        update={
            "hard_constraints": hard.model_copy(update={"dimensionality": 3})
        }
    )
    assert (
        evaluate_candidate(candidate, wrong_dimension, policy).decision
        is Decision.REJECT
    )


def test_failed_dimensionality_evidence_is_uncertain(requirement, policy) -> None:
    hard = requirement.hard_constraints.model_copy(
        update={"dimensionality": 2}
    )
    changed = requirement.model_copy(update={"hard_constraints": hard})
    evaluated = evaluate_candidate(
        make_candidate("missing-dimension", 0.7), changed, policy
    )
    assert evaluated.decision is Decision.UNCERTAIN
    assert "dimensionality" in evaluated.missing_evidence


def test_default_ranking_does_not_add_scientific_preference() -> None:
    first = make_candidate("mp-2", band_gap=0.7, hull=0.001).model_copy(
        update={"decision": Decision.PASS}
    )
    second = make_candidate("mp-1", band_gap=0.9, hull=0.04).model_copy(
        update={"decision": Decision.PASS}
    )
    _, published = rank_and_publish([first, second], [], 10)
    assert [candidate.source_material_id for candidate in published] == ["mp-1", "mp-2"]


def test_uncertain_candidates_are_published_after_pass_candidates() -> None:
    uncertain = make_candidate("mp-uncertain", band_gap=None)
    updated, published = rank_and_publish([uncertain], [], 10)
    assert [candidate.candidate_id for candidate in published] == [
        uncertain.candidate_id
    ]
    assert updated[0].published_downstream is True
    assert updated[0].publication_rank == 1


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


def test_maximize_target_and_lexicographic_ranking() -> None:
    first = make_candidate("mp-1", band_gap=0.9, hull=0.04).model_copy(
        update={"decision": Decision.PASS}
    )
    second = make_candidate("mp-2", band_gap=0.7, hull=0.01).model_copy(
        update={"decision": Decision.PASS}
    )
    maximize = RankingPreference(
        property="band_gap", mode=RankingMode.MAXIMIZE
    )
    _, by_maximum = rank_and_publish([second, first], [maximize], 10)
    assert [item.source_material_id for item in by_maximum] == ["mp-1", "mp-2"]

    target_then_stability = [
        RankingPreference(
            property="band_gap", mode=RankingMode.TARGET, target=0.7
        ),
        RankingPreference(
            property="energy_above_hull", mode=RankingMode.MINIMIZE
        ),
    ]
    third = make_candidate("mp-3", band_gap=0.7, hull=0.03).model_copy(
        update={"decision": Decision.PASS}
    )
    _, by_target = rank_and_publish(
        [first, third, second], target_then_stability, 10
    )
    assert [item.source_material_id for item in by_target] == [
        "mp-2",
        "mp-3",
        "mp-1",
    ]


def test_missing_ranking_property_sorts_after_present_value() -> None:
    present = make_candidate("mp-2", band_gap=0.7).model_copy(
        update={"decision": Decision.PASS}
    )
    missing = make_candidate("mp-1", band_gap=None).model_copy(
        update={"decision": Decision.PASS}
    )
    preference = RankingPreference(
        property="band_gap", mode=RankingMode.MAXIMIZE
    )
    _, published = rank_and_publish([missing, present], [preference], 10)
    assert [item.source_material_id for item in published] == ["mp-2", "mp-1"]
