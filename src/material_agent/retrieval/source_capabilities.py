"""Frozen Agent01 property coverage by public database source.

This catalog says what Agent01 may judge from a source's returned records or
from deterministic calculations on its returned structure.  It is deliberately
not a promise that every individual record has the property: missing evidence
continues through the existing ``UNCERTAIN`` path.
"""

from __future__ import annotations

from typing import Any

from material_agent.retrieval.models import SourceDatabase

_STRUCTURE_DERIVED = {
    "structural_dimensionality": {
        "kind": "DERIVED",
        "method": "pymatgen.CrystalNN+get_dimensionality_larsen",
        "requires": ["canonical_structure"],
    },
}


def source_property_coverage(source: SourceDatabase | str) -> dict[str, Any]:
    """Return a JSON-safe, source-specific Agent01 evidence boundary."""
    database = SourceDatabase(source)
    native: dict[str, dict[str, Any]] = {}
    unavailable = [
        "flat_band_bandwidth",
        "first_band_in_fermi_window",
        "projected_orbital_weight",
        "band_crossing_topology",
        "transition_metal_oxidation_state",
        "flat_band_contributor_connectivity",
        "layered_vdw_gap",
    ]
    if database is SourceDatabase.MATERIALS_PROJECT:
        native = {
            "band_gap": {"kind": "DATABASE", "method": "MP summary"},
            "energy_above_hull": {"kind": "DATABASE", "method": "MP summary"},
            "is_metal": {"kind": "DATABASE", "method": "MP summary"},
            "density": {"kind": "DATABASE", "method": "MP summary"},
            "volume": {"kind": "DATABASE", "method": "MP summary"},
            "formation_energy_per_atom": {"kind": "DATABASE", "method": "MP summary"},
            "is_stable": {"kind": "DATABASE", "method": "MP summary"},
            "crystal_system": {"kind": "DATABASE", "method": "MP summary symmetry"},
            "spacegroup_number": {"kind": "DATABASE", "method": "MP summary symmetry"},
            "is_gap_direct": {"kind": "DATABASE", "method": "MP summary"},
            "ordering": {"kind": "DATABASE", "method": "MP summary"},
        }
        unavailable = ["first_band_in_fermi_window", "projected_orbital_weight"]
    elif database is SourceDatabase.C2DB:
        native = {
            "band_gap": {"kind": "DATABASE", "method": "C2DB table; GPAW/PBE"},
            "energy_above_hull": {"kind": "DATABASE", "method": "C2DB table; GPAW/PBE"},
            "is_metal": {"kind": "DERIVED", "method": "C2DB PBE band gap equals zero"},
            "c2db_layer_group": {"kind": "DATABASE", "method": "C2DB table"},
            "c2db_magnetic_label": {"kind": "DATABASE", "method": "C2DB table"},
        }
    elif database is SourceDatabase.NOMAD:
        native = {
            "band_gap": {"kind": "DATABASE", "method": "NOMAD parsed archive; method varies by entry"},
        }
    elif database is SourceDatabase.TOPOLOGICAL_QUANTUM_CHEMISTRY:
        native = {
            "tqc_topological_material_label": {"kind": "DATABASE_LABEL", "method": "TQC classification/SOC/index"},
            "tqc_fermi_crossing_count": {
                "kind": "DATABASE_DIAGNOSTIC",
                "method": "TQC nbrFermiCrossing; band attribution unavailable",
            },
            "tqc_line_crossing_label": {
                "kind": "DATABASE_DIAGNOSTIC",
                "method": "TQC smLineCrossing/smCrossingType; band attribution unavailable",
            },
            "tqc_topological_subclassification": {
                "kind": "DATABASE_LABEL", "method": "TQC classification/SOC/index"
            },
            "tqc_has_topological_indices": {
                "kind": "DERIVED", "method": "presence of TQC indexCompounds.items"
            },
            "tqc_soc": {"kind": "DATABASE_LABEL", "method": "TQC compound type"},
        }
    elif database is SourceDatabase.MC3D:
        native = {}
    elif database is SourceDatabase.NIMS_SUPERCON:
        native = {}
        unavailable.append("structural_dimensionality")

    derived = {} if database is SourceDatabase.NIMS_SUPERCON else _STRUCTURE_DERIVED
    return {
        "schema_version": "agent01-source-property-coverage-v1",
        "source_database": database.value,
        "agent01_native_properties": native,
        "agent01_structure_derived_properties": derived,
        "not_judged_at_agent01": unavailable,
        "rule": (
            "A property is judged only when this source returns the required "
            "evidence or a declared deterministic structure calculation resolves it; "
            "otherwise it remains missing and is not inferred from another database."
        ),
    }
