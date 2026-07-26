"""Unit and shape normalization for Agent02 numeric outputs.

This module intentionally has no ASE, CHGNet, or Torch import.  The conversion
constant is the value exposed by ``ase.units.GPa`` in eV/angstrom^3.
"""

from __future__ import annotations

import math
from collections.abc import Sequence


ASE_GPA_IN_EV_ANGSTROM3 = 0.006241509125883258


def ase_voigt_to_symmetric_3x3(
    values: Sequence[float],
) -> list[list[float]]:
    """Convert ASE order ``xx, yy, zz, yz, xz, xy`` without changing sign."""

    if len(values) != 6:
        raise ValueError("ASE Voigt stress must contain exactly six values")
    xx, yy, zz, yz, xz, xy = (_finite_float(value) for value in values)
    return [
        [xx, xy, xz],
        [xy, yy, yz],
        [xz, yz, zz],
    ]


def normalize_direct_stress_gpa(
    values: Sequence[Sequence[float]],
) -> list[list[float]]:
    """Validate CHGNet direct-prediction stress, already expressed in GPa."""

    return _finite_symmetric_3x3(values)


def normalize_ase_stress_to_gpa(
    values: Sequence[float] | Sequence[Sequence[float]],
) -> list[list[float]]:
    """Convert ASE eV/angstrom^3 stress to GPa exactly once."""

    if len(values) == 6 and not isinstance(values[0], Sequence):
        matrix = ase_voigt_to_symmetric_3x3(values)  # type: ignore[arg-type]
    else:
        matrix = _finite_symmetric_3x3(  # type: ignore[arg-type]
            values
        )
    converted = [
        [
            component / ASE_GPA_IN_EV_ANGSTROM3
            for component in row
        ]
        for row in matrix
    ]
    return _finite_symmetric_3x3(converted)


def _finite_symmetric_3x3(
    values: Sequence[Sequence[float]],
) -> list[list[float]]:
    if len(values) != 3 or any(len(row) != 3 for row in values):
        raise ValueError("stress must have shape 3x3")
    matrix = [
        [_finite_float(component) for component in row]
        for row in values
    ]
    if any(
        not math.isclose(
            matrix[row][column],
            matrix[column][row],
            rel_tol=0.0,
            abs_tol=1e-12,
        )
        for row in range(3)
        for column in range(3)
    ):
        raise ValueError("stress tensor must be symmetric")
    return matrix


def _finite_float(value: float) -> float:
    if isinstance(value, bool):
        raise ValueError("stress components must be numeric, not bool")
    converted = float(value)
    if not math.isfinite(converted):
        raise ValueError("stress components must be finite")
    return converted
