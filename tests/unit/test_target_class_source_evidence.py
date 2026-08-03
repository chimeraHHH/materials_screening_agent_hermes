from __future__ import annotations

from material_agent.retrieval.evaluator import evaluate_candidate
from material_agent.retrieval.models import (
    Decision,
    Requirement,
    RetrievalPolicy,
    SourceDatabase,
)
from tests.unit.test_evaluator_and_ranking import make_candidate


def test_flat_band_target_stays_uncertain_when_nomad_lacks_its_electronic_evidence() -> None:
    candidate = make_candidate("nomad", band_gap=0.8).model_copy(
        update={"source_database": SourceDatabase.NOMAD}
    )
    requirement = Requirement.model_validate(
        {
            "requirement_id": "req-flat-band-source-evidence",
            "revision": 1,
            "target_class": "topological_flat_band",
            "hard_constraints": {"include_elements": [], "exclude_elements": [], "band_gap_ev": None, "energy_above_hull_ev_atom": None, "is_metal": None, "dimensionality": 2, "max_num_sites": None},
            "scientific_targets": [], "ranking_preferences": [],
            "budget": {"max_candidates": 1, "allow_ml": True, "allow_dft": False, "allow_many_body": False},
            "data_sources": {"materials_project": {"include_gnome": False}},
            "confirmed_by_user": True, "policy_version": "requirement-policy-v1",
        }
    )
    nomad_policy = RetrievalPolicy(
        policy_version="retrieval-policy-nomad-v1", source_database=SourceDatabase.NOMAD,
        endpoint="/entries/archive/query", chunk_size=1, max_records_scanned=1,
    )

    evaluated = evaluate_candidate(candidate, requirement, nomad_policy)

    assert evaluated.decision is Decision.UNCERTAIN
    assert "target_class_evidence:flat_band_bandwidth" in evaluated.missing_evidence
    assert "target_class_evidence:projected_orbital_weight" in evaluated.missing_evidence
