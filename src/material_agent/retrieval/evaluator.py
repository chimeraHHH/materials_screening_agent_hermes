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
from material_agent.retrieval.mp_screening import (
    MPScreeningSpec,
    MP_CAPABILITY_CATALOG,
    ScreeningIntent,
)


SUPPORTED_TARGET_PROPERTIES = {
    "band_gap": "band_gap",
    "stability": "energy_above_hull",
    "energy_above_hull": "energy_above_hull",
    "dimensionality": "structural_dimensionality",
    "nonmetal": "is_metal",
}
TQC_TOPOLOGICAL_TARGET = "topological_materials"


def evaluate_candidate(
    candidate: CandidateAuditRecord,
    requirement: Requirement,
    policy: RetrievalPolicy,
    screening_spec: MPScreeningSpec | None = None,
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

    if screening_spec is not None:
        evaluations.extend(_evaluate_mp_clauses(candidate, screening_spec))

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
    target_mismatch = [
        item
        for item in target_evaluations
        if item.result is ConstraintResult.MISMATCH
    ]

    if mismatch or target_mismatch:
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
    unmatched = [item.constraint_id for item in mismatch] + [
        f"scientific_target:{item.name}" for item in target_mismatch
    ]
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


def _evaluate_mp_clauses(
    candidate: CandidateAuditRecord, spec: MPScreeningSpec
) -> list[ConstraintEvaluation]:
    """Evaluate only exact/derived hard clauses with locally available evidence."""
    evaluations: list[ConstraintEvaluation] = []
    aliases = {
        "structure.dimension": "structural_dimensionality",
        "deep.sampled_bandwidth": "sampled_bandwidth_ev",
        "deep.oxidation_common": "oxidation_common",
        "deep.layered": "structural_dimensionality",
    }
    for clause in spec.mapped_clauses:
        capability = MP_CAPABILITY_CATALOG[clause.capability_id]
        if clause.intent is not ScreeningIntent.HARD or capability.evidence_kind.value == "PROXY":
            continue
        property_name = aliases.get(clause.capability_id, capability.field or clause.capability_id)
        prop = _property(candidate, property_name)
        observed = prop.value if prop else None
        if clause.capability_id == "elements.include":
            observed = candidate.elements
        elif clause.capability_id == "elements.exclude":
            observed = candidate.elements
        elif clause.capability_id == "structure.num_sites":
            observed = candidate.num_sites
        result = ConstraintResult.MISSING
        reason = "MP_PROPERTY_MISSING"
        expected = clause.value
        if observed is not None:
            try:
                if clause.operator in {"lte", "gte", "lt", "gt", "eq"} and isinstance(observed, (int, float, bool)):
                    left = float(observed) if not isinstance(observed, bool) else observed
                    right = float(expected) if not isinstance(expected, bool) else expected
                    result = ConstraintResult.MATCH if {
                        "lte": left <= right, "gte": left >= right,
                        "lt": left < right, "gt": left > right,
                        "eq": left == right,
                    }.get(clause.operator, False) else ConstraintResult.MISMATCH
                elif clause.operator == "range" and isinstance(expected, (list, tuple)):
                    low, high = expected
                    result = ConstraintResult.MATCH if (low is None or observed >= low) and (high is None or observed <= high) else ConstraintResult.MISMATCH
                elif clause.operator == "contains_all":
                    result = ConstraintResult.MATCH if set(expected).issubset(set(observed)) else ConstraintResult.MISMATCH
                elif clause.operator == "contains_none":
                    result = ConstraintResult.MATCH if not set(expected) & set(observed) else ConstraintResult.MISMATCH
                elif clause.operator in {"eq", "in", "has"}:
                    values = expected if isinstance(expected, (list, tuple, set)) else [expected]
                    result = ConstraintResult.MATCH if (observed in values if clause.operator == "in" else observed == expected) else ConstraintResult.MISMATCH
                reason = "MP_PROPERTY_MATCH" if result is ConstraintResult.MATCH else "MP_PROPERTY_MISMATCH"
            except (TypeError, ValueError):
                result = ConstraintResult.ERROR
                reason = "MP_PROPERTY_INVALID"
        evaluations.append(ConstraintEvaluation(
            constraint_id=clause.clause_id,
            constraint_type=clause.capability_id,
            expected=expected,
            observed=observed,
            unit=clause.unit or capability.unit,
            result=result,
            reason_code=reason,
            property_origin=prop.origin if prop else None,
        ))
    return evaluations


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
    target_key = (
        str(target.name).casefold().replace("-", "_").replace(" ", "_")
    )
    property_name = (
        "tqc_topological_material_label"
        if target_key == TQC_TOPOLOGICAL_TARGET
        else SUPPORTED_TARGET_PROPERTIES.get(target.name)
    )
    prop = _property(candidate, property_name) if property_name else None
    if not prop or prop.value is None:
        return ScientificTargetEvaluation(
            name=target.name,
            required_evidence_level=target.required_evidence_level,
            result=ConstraintResult.MISSING,
            reason_code="SCIENTIFIC_TARGET_UNSUPPORTED_AT_L1",
        )
    if property_name == "tqc_topological_material_label" and not bool(prop.value):
        return ScientificTargetEvaluation(
            name=target.name,
            required_evidence_level=target.required_evidence_level,
            result=ConstraintResult.MISMATCH,
            reason_code="TQC_TOPOLOGICAL_LABEL_NOT_MATCHED",
            observed_property=property_name,
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
