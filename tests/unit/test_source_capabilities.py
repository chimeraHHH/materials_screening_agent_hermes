from material_agent.retrieval.models import SourceDatabase
from material_agent.retrieval.source_capabilities import source_property_coverage


def test_c2db_coverage_keeps_flat_band_evidence_out_of_agent01() -> None:
    coverage = source_property_coverage(SourceDatabase.C2DB)
    assert coverage["agent01_native_properties"]["band_gap"]["method"] == "C2DB table; GPAW/PBE"
    assert "flat_band_bandwidth" in coverage["not_judged_at_agent01"]
    assert "structural_dimensionality" in coverage["agent01_structure_derived_properties"]


def test_mp_coverage_includes_simple_summary_constraints() -> None:
    coverage = source_property_coverage(SourceDatabase.MATERIALS_PROJECT)
    assert "density" in coverage["agent01_native_properties"]
    assert "crystal_system" in coverage["agent01_native_properties"]


def test_nims_does_not_claim_structure_derived_properties() -> None:
    coverage = source_property_coverage(SourceDatabase.NIMS_SUPERCON)
    assert coverage["agent01_structure_derived_properties"] == {}
    assert "structural_dimensionality" in coverage["not_judged_at_agent01"]


def test_tqc_crossing_metadata_is_diagnostic_not_flat_band_evidence() -> None:
    coverage = source_property_coverage(
        SourceDatabase.TOPOLOGICAL_QUANTUM_CHEMISTRY
    )
    assert (
        coverage["agent01_native_properties"]["tqc_fermi_crossing_count"]["kind"]
        == "DATABASE_DIAGNOSTIC"
    )
    assert "flat_band_bandwidth" in coverage["not_judged_at_agent01"]
