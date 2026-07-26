"""Pure deterministic constraint and evidence evaluation."""

from __future__ import annotations

import math
from typing import Any

from material_agent.retrieval.models import (
    CandidateAuditRecord,
    ConstraintEvaluation,
    ConstraintResult,
    Decision,
    EvidenceLevel,
    NumericRange,
    PropertyValue,
    Requirement,
    RetrievalPolicy,
    ScientificTargetEvaluation,
)


SUPPORTED_TARGET_PROPERTIES = {
    "band_gap": "band_gap",
    "stability": "energy_above_hull",
    "energy_above_hull": "energy_above_hull",
    "dimensionality": "structural_dimensionality",
    "nonmetal": "is_metal",
}


def evaluate_candidate(
    candidate: CandidateAuditRecord,
    requirement: Requirement,
    policy: RetrievalPolicy,
) -> CandidateAuditRecord:
    hard = requirement.hard_constraints
    evaluations: list[ConstraintEvaluation] = []

    if hard.include_elements:
        required = sorted(set(hard.include_elements))
        missing = sorted(set(required) - set(candidate.elements))
        evaluations.append(
            ConstraintEvaluation(
                constraint_id="elements.include",
                constraint_type="include_elements",
                expected=required,
                observed=candidate.elements,
                result=(
                    ConstraintResult.MATCH
                    if not missing
                    else ConstraintResult.MISMATCH
                ),
                reason_code=(
                    "ELEMENTS_INCLUDED" if not missing else "REQUIRED_ELEMENT_MISSING"
                ),
            )
        )

    if hard.exclude_elements:
        excluded = sorted(set(hard.exclude_elements))
        present = sorted(set(excluded) & set(candidate.elements))
        evaluations.append(
            ConstraintEvaluation(
                constraint_id="elements.exclude",
                constraint_type="exclude_elements",
                expected=excluded,
                observed=present,
                result=(
                    ConstraintResult.MATCH
                    if not present
                    else ConstraintResult.MISMATCH
                ),
                reason_code=(
                    "EXCLUDED_ELEMENTS_ABSENT"
                    if not present
                    else "EXCLUDED_ELEMENT_PRESENT"
                ),
            )
        )

    if hard.band_gap_ev is not None:
        evaluations.append(
            _range_evaluation(
                "band_gap_ev",
                "band_gap",
                hard.band_gap_ev,
                _property(candidate, "band_gap"),
                policy.numeric_tolerance,
            )
        )

    if hard.energy_above_hull_ev_atom is not None:
        evaluations.append(
            _range_evaluation(
                "energy_above_hull_ev_atom",
                "energy_above_hull",
                hard.energy_above_hull_ev_atom,
                _property(candidate, "energy_above_hull"),
                policy.numeric_tolerance,
            )
        )

    if hard.is_metal is not None:
        prop = _property(candidate, "is_metal")
        observed = prop.value if prop else None
        if observed is None:
            result = ConstraintResult.MISSING
            reason = "METALLICITY_MISSING"
        elif bool(observed) == hard.is_metal:
            result = ConstraintResult.MATCH
            reason = "METALLICITY_MATCH"
        else:
            result = ConstraintResult.MISMATCH
            reason = "METALLICITY_MISMATCH"
        evaluations.append(
            ConstraintEvaluation(
                constraint_id="is_metal",
                constraint_type="is_metal",
                expected=hard.is_metal,
                observed=observed,
                unit="dimensionless",
                result=result,
                reason_code=reason,
                property_origin=prop.origin if prop else None,
            )
        )

    if hard.max_num_sites is not None:
        observed = candidate.num_sites
        if observed is None:
            result = ConstraintResult.MISSING
            reason = "NUM_SITES_MISSING"
        elif observed <= hard.max_num_sites:
            result = ConstraintResult.MATCH
            reason = "NUM_SITES_WITHIN_LIMIT"
        else:
            result = ConstraintResult.MISMATCH
            reason = "NUM_SITES_EXCEEDED"
        prop = _property(candidate, "num_sites")
        evaluations.append(
            ConstraintEvaluation(
                constraint_id="max_num_sites",
                constraint_type="max_num_sites",
                expected=hard.max_num_sites,
                observed=observed,
                unit="count",
                result=result,
                reason_code=reason,
                property_origin=prop.origin if prop else None,
            )
        )

    if hard.dimensionality is not None:
        prop = _property(candidate, "structural_dimensionality")
        observed = prop.value if prop else None
        if observed is None:
            result = ConstraintResult.MISSING
            reason = "DIMENSIONALITY_EVALUATION_FAILED"
        elif int(observed) == hard.dimensionality:
            result = ConstraintResult.MATCH
            reason = "DIMENSIONALITY_MATCH"
        else:
            result = ConstraintResult.MISMATCH
            reason = "DIMENSIONALITY_MISMATCH"
        evaluations.append(
            ConstraintEvaluation(
                constraint_id="dimensionality",
                constraint_type="dimensionality",
                expected=hard.dimensionality,
                observed=observed,
                unit="dimensionless",
                result=result,
                reason_code=reason,
                property_origin=prop.origin if prop else None,
            )
        )

    target_evaluations = [
        _evaluate_scientific_target(candidate, target)
        for target in requirement.scientific_targets
    ]
    mismatch = [
        item for item in evaluations if item.result is ConstraintResult.MISMATCH
    ]
    missing = [
        item
        for item in evaluations
        if item.result in {ConstraintResult.MISSING, ConstraintResult.ERROR}
    ]
    target_missing = [
        item
        for item in target_evaluations
        if item.result in {ConstraintResult.MISSING, ConstraintResult.ERROR}
    ]

    if mismatch:
        decision = Decision.REJECT
    elif missing or target_missing:
        decision = Decision.UNCERTAIN
    else:
        decision = Decision.PASS

    matched = [
        item.constraint_id
        for item in evaluations
        if item.result is ConstraintResult.MATCH
    ]
    unmatched = [item.constraint_id for item in mismatch]
    missing_evidence = [
        item.constraint_id for item in missing
    ] + [f"scientific_target:{item.name}" for item in target_missing]
    reasons = [item.reason_code for item in evaluations] + [
        item.reason_code for item in target_evaluations
    ]

    return candidate.model_copy(
        update={
            "constraint_evaluations": evaluations,
            "scientific_target_evaluations": target_evaluations,
            "matched_constraints": matched,
            "unmatched_constraints": unmatched,
            "missing_evidence": missing_evidence,
            "decision": decision,
            "decision_reasons": sorted(set(reasons)),
        }
    )


def _range_evaluation(
    constraint_id: str,
    property_name: str,
    expected: NumericRange,
    prop: PropertyValue | None,
    tolerance: float,
) -> ConstraintEvaluation:
    observed = prop.value if prop else None
    if observed is None or isinstance(observed, bool):
        result = ConstraintResult.MISSING
        reason = f"{property_name.upper()}_MISSING"
    else:
        try:
            numeric = float(observed)
        except (TypeError, ValueError):
            result = ConstraintResult.ERROR
            reason = f"{property_name.upper()}_INVALID"
        else:
            if not math.isfinite(numeric):
                result = ConstraintResult.MISSING
                reason = f"{property_name.upper()}_MISSING"
            else:
                above_min = expected.min is None or numeric + tolerance >= expected.min
                below_max = expected.max is None or numeric - tolerance <= expected.max
                result = (
                    ConstraintResult.MATCH
                    if above_min and below_max
                    else ConstraintResult.MISMATCH
                )
                reason = (
                    f"{property_name.upper()}_IN_RANGE"
                    if result is ConstraintResult.MATCH
                    else f"{property_name.upper()}_OUT_OF_RANGE"
                )
    return ConstraintEvaluation(
        constraint_id=constraint_id,
        constraint_type=property_name,
        expected=expected.model_dump(mode="json"),
        observed=observed,
        unit=expected.unit,
        result=result,
        reason_code=reason,
        property_origin=prop.origin if prop else None,
    )


def _evaluate_scientific_target(
    candidate: CandidateAuditRecord, target: Any
) -> ScientificTargetEvaluation:
    if target.required_evidence_level is not EvidenceLevel.L1_RETRIEVED:
        return ScientificTargetEvaluation(
            name=target.name,
            required_evidence_level=target.required_evidence_level,
            result=ConstraintResult.MISSING,
            reason_code="SCIENTIFIC_TARGET_REQUIRES_DOWNSTREAM_EVIDENCE",
        )
    property_name = SUPPORTED_TARGET_PROPERTIES.get(target.name)
    prop = _property(candidate, property_name) if property_name else None
    if not prop or prop.value is None:
        return ScientificTargetEvaluation(
            name=target.name,
            required_evidence_level=target.required_evidence_level,
            result=ConstraintResult.MISSING,
            reason_code="SCIENTIFIC_TARGET_UNSUPPORTED_AT_L1",
        )
    return ScientificTargetEvaluation(
        name=target.name,
        required_evidence_level=target.required_evidence_level,
        result=ConstraintResult.MATCH,
        reason_code="SCIENTIFIC_TARGET_EVIDENCE_PRESENT",
        observed_property=property_name,
    )


def _property(
    candidate: CandidateAuditRecord, name: str | None
) -> PropertyValue | None:
    if name is None:
        return None
    return next((prop for prop in candidate.properties if prop.name == name), None)

