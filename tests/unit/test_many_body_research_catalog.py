from material_agent.many_body.research_catalog import (
    RESEARCH_CATALOG,
    RESEARCH_CATALOG_VERSION,
    research_catalog_hash,
    research_entry,
)


def test_research_catalog_is_deterministic_and_covers_priority_methods() -> None:
    assert RESEARCH_CATALOG_VERSION == "many-body-solver-research/v1"
    assert research_catalog_hash() == research_catalog_hash()
    assert {entry.method_family for entry in RESEARCH_CATALOG} == {"QMC", "DMRG", "DMFT"}
    assert all(entry.source_url.startswith("https://") for entry in RESEARCH_CATALOG)
    assert all(entry.documentation_url.startswith("https://") for entry in RESEARCH_CATALOG)


def test_research_entries_expose_method_specific_quality_gates() -> None:
    assert "average_sign" in research_entry("qmc/alf-v2.4").quality_gates
    assert "discarded_weight" in research_entry("dmrg/tenpy-v1").quality_gates
    assert "converged_self_energy_norm" in research_entry("dmft/solid-dmft-triqs4").quality_gates
