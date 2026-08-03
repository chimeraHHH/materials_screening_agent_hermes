from __future__ import annotations

from datetime import UTC, datetime

from material_agent.retrieval.models import RetrievalQueryPlan, SourceDatabase
from material_agent.retrieval.normalizer import normalize_candidate
from material_agent.retrieval.query import retrieval_policy_for_source
from material_agent.retrieval.structures import process_structure


def test_adaptive_summary_properties_do_not_require_legacy_origin_aliases() -> None:
    document = {
        "material_id": "mp-adaptive-test", "formula_pretty": "Si",
        "elements": ["Si"], "nsites": 1, "band_gap": 1.0,
        "energy_above_hull": 0.0, "is_metal": False,
        "formation_energy_per_atom": -0.1, "is_stable": True,
        "possible_species": ["Si0+"],
        "structure": {
            "@module": "pymatgen.core.structure", "@class": "Structure",
            "lattice": {"matrix": [[3, 0, 0], [0, 3, 0], [0, 0, 3]]},
            "sites": [{"species": [{"element": "Si", "occu": 1}], "abc": [0, 0, 0], "properties": {}}],
        },
        "origins": [],
    }
    policy = retrieval_policy_for_source(
        SourceDatabase.MATERIALS_PROJECT, adaptive_mp_screening=True
    )
    processed = process_structure(
        document["structure"], summary_elements=document["elements"],
        summary_num_sites=1, summary_formula="Si", policy=policy,
    )
    plan = RetrievalQueryPlan(
        query_id="qry-test", endpoint="/materials/summary", database_version="test",
        requirement_hash="0" * 64, pushdown_filters={}, local_only_constraints=[],
        requested_fields=[], chunk_size=1, num_chunks=1, max_records_scanned=1,
        max_candidates_published=1, query_fingerprint="1" * 64,
        source_database=SourceDatabase.MATERIALS_PROJECT,
        policy_version="retrieval-policy-mp-adaptive-v2",
        include_gnome=False, include_deprecated=False, theoretical_policy=None,
        sort_fields="local:material_id", client_version="test-client",
    )
    candidate = normalize_candidate(
        document, project_id="project", query_plan=plan, processed_structure=processed,
        source_uri="artifact://source", source_sha256="2" * 64,
        structure_uri="artifact://structure", structure_sha256="3" * 64,
        retrieved_at=datetime.now(UTC),
    )
    properties = {item.name: item.value for item in candidate.properties}
    assert {"formation_energy_per_atom", "is_stable", "possible_species"} <= set(properties)
    assert properties["possible_species"] == "Si0+"
