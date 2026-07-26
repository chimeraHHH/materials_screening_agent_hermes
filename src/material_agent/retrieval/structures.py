"""Structure validation, canonicalization, and dimensionality analysis."""

from __future__ import annotations

import hashlib
import json
import math
import warnings
from dataclasses import dataclass
from typing import Any

from pymatgen.analysis.dimensionality import get_dimensionality_larsen
from pymatgen.analysis.local_env import CrystalNN
from pymatgen.analysis.structure_matcher import StructureMatcher
from pymatgen.core import Composition, Lattice, Structure
from pymatgen.io.cif import CifWriter

from material_agent.retrieval.models import RetrievalPolicy


CRYSTAL_NN_PARAMETERS = {
    "weighted_cn": False,
    "cation_anion": False,
    "distance_cutoffs": (0.5, 1.0),
    "x_diff_weight": 3.0,
    "porous_adjustment": True,
    "search_cutoff": 7.0,
    "fingerprint_length": None,
}


class StructureValidationError(ValueError):
    """Raised when a structure cannot safely enter downstream stages."""


@dataclass(slots=True)
class ProcessedStructure:
    structure: Structure
    structure_id: str
    canonical_payload: dict[str, Any]
    source_payload: dict[str, Any]
    cif_text: str
    reduced_formula: str
    elements: list[str]
    num_sites: int
    data_quality_flags: list[str]
    data_quality_warnings: list[str]


@dataclass(slots=True)
class DimensionalityResult:
    value: int | None
    method: str
    error: str | None = None
    warnings: list[str] | None = None


def process_structure(
    raw_structure: Any,
    *,
    summary_elements: list[Any] | None,
    summary_num_sites: int | None,
    summary_formula: str | None = None,
    policy: RetrievalPolicy,
) -> ProcessedStructure:
    structure = _coerce_structure(raw_structure)
    _validate_structure(structure)

    canonical = _canonical_structure(structure)
    payload = _canonical_payload(
        canonical,
        policy.canonical_float_digits,
        policy.canonicalization_policy_version,
    )
    serialized = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode(
        "utf-8"
    )
    digest = hashlib.sha256(serialized).hexdigest()
    structure_id = f"str_{digest[:24]}"
    elements = sorted(str(element) for element in canonical.composition.elements)
    flags: list[str] = []

    if summary_elements is not None:
        normalized_summary_elements = sorted(str(value) for value in summary_elements)
        if normalized_summary_elements != elements:
            flags.append("SOURCE_ELEMENT_SET_MISMATCH")
    if summary_num_sites is not None and int(summary_num_sites) != len(canonical):
        flags.append("SOURCE_NSITES_MISMATCH")
    if summary_formula:
        try:
            summary_composition = Composition(summary_formula)
        except Exception:
            flags.append("SOURCE_FORMULA_INVALID")
        else:
            if not _stoichiometry_matches(
                summary_composition, canonical.composition
            ):
                flags.append("SOURCE_FORMULA_MISMATCH")

    cif_text = str(CifWriter(canonical, symprec=None))
    round_trip_warnings = _validate_cif_round_trip(cif_text, canonical)
    if round_trip_warnings:
        flags.append("CIF_ROUND_TRIP_WARNING")

    return ProcessedStructure(
        structure=canonical,
        structure_id=structure_id,
        canonical_payload=payload,
        source_payload=structure.as_dict(),
        cif_text=cif_text,
        reduced_formula=canonical.composition.reduced_formula,
        elements=elements,
        num_sites=len(canonical),
        data_quality_flags=flags,
        data_quality_warnings=round_trip_warnings,
    )


def calculate_dimensionality(structure: Structure) -> DimensionalityResult:
    method = "pymatgen.CrystalNN+get_dimensionality_larsen"
    try:
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            bonded_structure = CrystalNN(
                **CRYSTAL_NN_PARAMETERS
            ).get_bonded_structure(structure)
            value = int(get_dimensionality_larsen(bonded_structure))
        if value not in {0, 1, 2, 3}:
            raise ValueError(f"unexpected dimensionality result: {value}")
        warning_messages = sorted(
            {f"{item.category.__name__}: {item.message}" for item in caught}
        )
        return DimensionalityResult(
            value=value,
            method=method,
            warnings=warning_messages,
        )
    except Exception as exc:
        return DimensionalityResult(
            value=None,
            method=method,
            error=f"{type(exc).__name__}: {exc}",
            warnings=[],
        )


def _coerce_structure(raw_structure: Any) -> Structure:
    if raw_structure is None:
        raise StructureValidationError("structure is missing")
    if isinstance(raw_structure, Structure):
        return raw_structure.copy()
    if isinstance(raw_structure, dict):
        try:
            return Structure.from_dict(raw_structure)
        except Exception as exc:
            raise StructureValidationError(
                f"structure dictionary is invalid: {type(exc).__name__}"
            ) from exc
    raise StructureValidationError(
        f"unsupported structure value: {type(raw_structure).__name__}"
    )


def _validate_structure(structure: Structure) -> None:
    if len(structure) == 0:
        raise StructureValidationError("structure has no sites")
    matrix = structure.lattice.matrix
    if not all(math.isfinite(float(value)) for row in matrix for value in row):
        raise StructureValidationError("lattice contains non-finite values")
    if not math.isfinite(float(structure.volume)) or structure.volume <= 0:
        raise StructureValidationError("lattice volume must be positive and finite")
    for site in structure:
        if not all(math.isfinite(float(value)) for value in site.frac_coords):
            raise StructureValidationError("site coordinates contain non-finite values")
        occupancy = sum(float(value) for value in site.species.values())
        if occupancy <= 0 or occupancy > 1.0 + 1e-8:
            raise StructureValidationError(f"invalid site occupancy: {occupancy}")


def _canonical_structure(structure: Structure) -> Structure:
    sites: list[tuple[str, Any, list[float]]] = []
    for site in structure:
        species_key = json.dumps(
            {str(key): float(value) for key, value in site.species.items()},
            sort_keys=True,
            separators=(",", ":"),
        )
        coordinates = [float(value) % 1.0 for value in site.frac_coords]
        sites.append((species_key, site.species, coordinates))

    sites.sort(
        key=lambda item: (
            item[0],
            round(item[2][0], 12),
            round(item[2][1], 12),
            round(item[2][2], 12),
        )
    )
    return Structure(
        Lattice(structure.lattice.matrix.copy()),
        [item[1] for item in sites],
        [item[2] for item in sites],
        coords_are_cartesian=False,
        to_unit_cell=True,
    )


def _canonical_payload(
    structure: Structure, digits: int, policy_version: str
) -> dict[str, Any]:
    return {
        "policy_version": policy_version,
        "lattice": [
            [_normalize_float(value, digits) for value in row]
            for row in structure.lattice.matrix
        ],
        "sites": [
            {
                "species": {
                    str(key): _normalize_float(value, digits)
                    for key, value in sorted(
                        site.species.items(), key=lambda item: str(item[0])
                    )
                },
                "abc": [
                    _normalize_float(float(value) % 1.0, digits)
                    for value in site.frac_coords
                ],
            }
            for site in structure
        ],
    }


def _normalize_float(value: float, digits: int) -> float:
    rounded = round(float(value), digits)
    return 0.0 if rounded == 0 else rounded


def _composition_matches(first: Composition, second: Composition) -> bool:
    first_values = first.get_el_amt_dict()
    second_values = second.get_el_amt_dict()
    elements = set(first_values) | set(second_values)
    return all(
        math.isclose(
            float(first_values.get(element, 0.0)),
            float(second_values.get(element, 0.0)),
            rel_tol=0,
            abs_tol=1e-8,
        )
        for element in elements
    )


def _stoichiometry_matches(first: Composition, second: Composition) -> bool:
    first_values = first.fractional_composition.get_el_amt_dict()
    second_values = second.fractional_composition.get_el_amt_dict()
    elements = set(first_values) | set(second_values)
    return all(
        math.isclose(
            float(first_values.get(element, 0.0)),
            float(second_values.get(element, 0.0)),
            rel_tol=0,
            abs_tol=1e-8,
        )
        for element in elements
    )


def _validate_cif_round_trip(cif_text: str, expected: Structure) -> list[str]:
    try:
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            restored = Structure.from_str(cif_text, fmt="cif")
    except Exception as exc:
        raise StructureValidationError(
            f"canonical CIF cannot be read: {type(exc).__name__}"
        ) from exc
    if len(restored) != len(expected):
        raise StructureValidationError("canonical CIF changed the site count")
    if not _composition_matches(restored.composition, expected.composition):
        raise StructureValidationError("canonical CIF changed the composition")
    for restored_value, expected_value in zip(
        (*restored.lattice.abc, *restored.lattice.angles),
        (*expected.lattice.abc, *expected.lattice.angles),
        strict=True,
    ):
        if not math.isclose(
            float(restored_value),
            float(expected_value),
            rel_tol=1e-9,
            abs_tol=1e-6,
        ):
            raise StructureValidationError(
                "canonical CIF changed the lattice parameters"
            )
    matcher = StructureMatcher(
        ltol=1e-5,
        stol=1e-3,
        angle_tol=1e-4,
        primitive_cell=False,
        scale=False,
        attempt_supercell=False,
        allow_subset=False,
    )
    if not matcher.fit(expected, restored):
        raise StructureValidationError("canonical CIF changed the atomic structure")
    return sorted(
        {f"{item.category.__name__}: {item.message}" for item in caught}
    )
