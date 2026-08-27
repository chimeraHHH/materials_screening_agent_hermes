"""Deterministic, side-effect-free Agent02 pre-filter."""

from __future__ import annotations

import math
from typing import TypeVar

from material_agent.ml_screening.models import (
    CandidateProperty,
    MLCandidateInput,
    MLDecision,
    MLRequirementView,
    MLScreeningPolicy,
    NumericRange,
    PreFilterEvaluation,
    PreFilterReasonCode,
)

_UNIT_ALIASES = {
    "ev": "eV",
    "electronvolt": "eV",
    "ev/atom": "eV/atom",
    "ev atom^-1": "eV/atom",
    "ev per atom": "eV/atom",
    "dimensionless": "dimensionless",
    "count": "count",
}
T = TypeVar("T")


def evaluate_pre_filter(
    candidate: MLCandidateInput,
    requirement: MLRequirementView,
    policy: MLScreeningPolicy,
) -> PreFilterEvaluation:
    """Re-evaluate confirmed hard constraints without changing upstream data."""

    reasons: list[PreFilterReasonCode] = []
    matched: list[str] = []
    mismatched: list[str] = []
    missing: list[str] = []

    if candidate.upstream_decision is MLDecision.REJECT:
        return PreFilterEvaluation(
            candidate_id=candidate.candidate_id,
            decision=MLDecision.REJECT,
            reason_codes=[PreFilterReasonCode.UPSTREAM_REJECTED],
        )
    if candidate.upstream_decision is MLDecision.FAILED:
        return PreFilterEvaluation(
            candidate_id=candidate.candidate_id,
            decision=MLDecision.FAILED,
            reason_codes=[PreFilterReasonCode.UPSTREAM_FAILED],
        )

    structure = candidate.source_structure
    if structure.parseable is False or structure.hash_verified is False:
        mismatched.append("structure.valid")
        reasons.append(PreFilterReasonCode.INVALID_STRUCTURE)
    elif structure.parseable is None or structure.hash_verified is None:
        missing.append("structure.valid")
        reasons.append(PreFilterReasonCode.INVALID_STRUCTURE)
    else:
        matched.append("structure.valid")

    required_elements = set(requirement.include_elements)
    if required_elements:
        if required_elements <= set(candidate.elements):
            matched.append("elements.include")
        else:
            mismatched.append("elements.include")
            reasons.append(
                PreFilterReasonCode.HARD_CONSTRAINT_INCLUDE_ELEMENT
            )

    excluded_elements = set(requirement.exclude_elements)
    if excluded_elements:
        if excluded_elements.isdisjoint(candidate.elements):
            matched.append("elements.exclude")
        else:
            mismatched.append("elements.exclude")
            reasons.append(
                PreFilterReasonCode.HARD_CONSTRAINT_EXCLUDE_ELEMENT
            )

    if requirement.max_num_sites is not None:
        if candidate.num_sites is None:
            missing.append("max_num_sites")
            reasons.append(PreFilterReasonCode.MISSING_REQUIRED_PROPERTY)
        elif candidate.num_sites <= requirement.max_num_sites:
            matched.append("max_num_sites")
        else:
            mismatched.append("max_num_sites")
            reasons.append(PreFilterReasonCode.HARD_CONSTRAINT_NUM_SITES)

    _evaluate_numeric_range(
        candidate,
        property_name="band_gap",
        constraint_name="band_gap_ev",
        expected=requirement.band_gap_ev,
        expected_unit="eV",
        tolerance=policy.numeric_tolerance,
        matched=matched,
        mismatched=mismatched,
        missing=missing,
        reasons=reasons,
    )
    _evaluate_numeric_range(
        candidate,
        property_name="energy_above_hull",
        constraint_name="energy_above_hull_ev_atom",
        expected=requirement.energy_above_hull_ev_atom,
        expected_unit="eV/atom",
        tolerance=policy.numeric_tolerance,
        matched=matched,
        mismatched=mismatched,
        missing=missing,
        reasons=reasons,
    )

    if requirement.is_metal is not None:
        prop = _property(candidate, "is_metal")
        if prop is None or prop.value is None:
            missing.append("is_metal")
            reasons.append(PreFilterReasonCode.MISSING_REQUIRED_PROPERTY)
        elif _canonical_unit(prop.unit) != "dimensionless":
            missing.append("is_metal")
            reasons.append(PreFilterReasonCode.INVALID_PROPERTY_UNIT)
        elif not isinstance(prop.value, bool):
            missing.append("is_metal")
            reasons.append(PreFilterReasonCode.INVALID_PROPERTY_VALUE)
        elif prop.value == requirement.is_metal:
            matched.append("is_metal")
        else:
            mismatched.append("is_metal")
            reasons.append(PreFilterReasonCode.HARD_CONSTRAINT_METALLICITY)

    if requirement.dimensionality is not None:
        observed = structure.dimensionality
        if observed is None:
            prop = _property(candidate, "structural_dimensionality")
            if (
                prop is not None
                and isinstance(prop.value, (int, float))
                and not isinstance(prop.value, bool)
            ):
                observed = int(prop.value)
        if observed is None:
            missing.append("dimensionality")
            reasons.append(PreFilterReasonCode.MISSING_REQUIRED_PROPERTY)
        elif observed == requirement.dimensionality:
            matched.append("dimensionality")
        else:
            mismatched.append("dimensionality")
            reasons.append(
                PreFilterReasonCode.HARD_CONSTRAINT_DIMENSIONALITY
            )

    if mismatched:
        decision = MLDecision.REJECT
    elif missing:
        decision = MLDecision.UNCERTAIN
    else:
        decision = MLDecision.PASS
        reasons.append(PreFilterReasonCode.PREFILTER_PASSED)

    return PreFilterEvaluation(
        candidate_id=candidate.candidate_id,
        decision=decision,
        reason_codes=_unique(reasons),
        matched_constraints=matched,
        mismatched_constraints=mismatched,
        missing_constraints=missing,
    )


def _evaluate_numeric_range(
    candidate: MLCandidateInput,
    *,
    property_name: str,
    constraint_name: str,
    expected: NumericRange | None,
    expected_unit: str,
    tolerance: float,
    matched: list[str],
    mismatched: list[str],
    missing: list[str],
    reasons: list[PreFilterReasonCode],
) -> None:
    if expected is None:
        return
    prop = _property(candidate, property_name)
    if prop is None or prop.value is None:
        missing.append(constraint_name)
        reasons.append(PreFilterReasonCode.MISSING_REQUIRED_PROPERTY)
        return
    if _canonical_unit(prop.unit) != expected_unit:
        missing.append(constraint_name)
        reasons.append(PreFilterReasonCode.INVALID_PROPERTY_UNIT)
        return
    if isinstance(prop.value, bool):
        missing.append(constraint_name)
        reasons.append(PreFilterReasonCode.INVALID_PROPERTY_VALUE)
        return
    try:
        observed = float(prop.value)
    except (TypeError, ValueError):
        missing.append(constraint_name)
        reasons.append(PreFilterReasonCode.INVALID_PROPERTY_VALUE)
        return
    if not math.isfinite(observed):
        missing.append(constraint_name)
        reasons.append(PreFilterReasonCode.INVALID_PROPERTY_VALUE)
        return

    lower_ok = expected.min is None or observed + tolerance >= expected.min
    upper_ok = expected.max is None or observed - tolerance <= expected.max
    if lower_ok and upper_ok:
        matched.append(constraint_name)
    else:
        mismatched.append(constraint_name)
        reasons.append(PreFilterReasonCode.HARD_CONSTRAINT_PROPERTY_RANGE)


def _property(
    candidate: MLCandidateInput,
    name: str,
) -> CandidateProperty | None:
    return next(
        (prop for prop in candidate.properties if prop.name == name),
        None,
    )


def _canonical_unit(unit: str) -> str:
    stripped = unit.strip()
    return _UNIT_ALIASES.get(stripped.lower(), stripped)


def _unique(items: list[T]) -> list[T]:
    return list(dict.fromkeys(items))
