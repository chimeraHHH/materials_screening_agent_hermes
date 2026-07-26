from __future__ import annotations

from material_agent.ml_screening.models import (
    MLDecision,
    MLRequirementView,
    NumericRange,
    PreFilterReasonCode,
)
from material_agent.ml_screening.prefilter import evaluate_pre_filter


def test_prefilter_passes_confirmed_si_o_candidate(
    ml_candidate_factory,
    ml_requirement,
    ml_policy,
) -> None:
    result = evaluate_pre_filter(
        ml_candidate_factory(),
        ml_requirement,
        ml_policy,
    )
    assert result.decision is MLDecision.PASS
    assert PreFilterReasonCode.PREFILTER_PASSED in result.reason_codes
    assert not result.mismatched_constraints
    assert not result.missing_constraints


def test_prefilter_rejects_missing_included_element(
    ml_candidate_factory,
    ml_requirement,
    ml_policy,
) -> None:
    requirement = ml_requirement.model_copy(
        update={"include_elements": ["Fe"]}
    )
    result = evaluate_pre_filter(
        ml_candidate_factory(),
        requirement,
        ml_policy,
    )
    assert result.decision is MLDecision.REJECT
    assert (
        PreFilterReasonCode.HARD_CONSTRAINT_INCLUDE_ELEMENT
        in result.reason_codes
    )


def test_prefilter_rejects_excluded_element(
    ml_candidate_factory,
    ml_requirement,
    ml_policy,
) -> None:
    requirement = ml_requirement.model_copy(
        update={"exclude_elements": ["Si"]}
    )
    result = evaluate_pre_filter(
        ml_candidate_factory(),
        requirement,
        ml_policy,
    )
    assert result.decision is MLDecision.REJECT
    assert (
        PreFilterReasonCode.HARD_CONSTRAINT_EXCLUDE_ELEMENT
        in result.reason_codes
    )


def test_prefilter_rejects_out_of_range_property(
    ml_candidate_factory,
    ml_requirement,
    ml_policy,
) -> None:
    result = evaluate_pre_filter(
        ml_candidate_factory(band_gap=1.5),
        ml_requirement,
        ml_policy,
    )
    assert result.decision is MLDecision.REJECT
    assert (
        PreFilterReasonCode.HARD_CONSTRAINT_PROPERTY_RANGE
        in result.reason_codes
    )


def test_prefilter_marks_missing_property_uncertain(
    ml_candidate_factory,
    ml_requirement,
    ml_policy,
) -> None:
    result = evaluate_pre_filter(
        ml_candidate_factory(band_gap=None),
        ml_requirement,
        ml_policy,
    )
    assert result.decision is MLDecision.UNCERTAIN
    assert (
        PreFilterReasonCode.MISSING_REQUIRED_PROPERTY
        in result.reason_codes
    )


def test_prefilter_marks_wrong_unit_uncertain(
    ml_candidate_factory,
    ml_requirement,
    ml_policy,
) -> None:
    result = evaluate_pre_filter(
        ml_candidate_factory(hull_unit="J/mol"),
        ml_requirement,
        ml_policy,
    )
    assert result.decision is MLDecision.UNCERTAIN
    assert PreFilterReasonCode.INVALID_PROPERTY_UNIT in result.reason_codes


def test_prefilter_never_reverses_upstream_reject(
    ml_candidate_factory,
    ml_requirement,
    ml_policy,
) -> None:
    result = evaluate_pre_filter(
        ml_candidate_factory(decision=MLDecision.REJECT),
        ml_requirement,
        ml_policy,
    )
    assert result.decision is MLDecision.REJECT
    assert result.reason_codes == [PreFilterReasonCode.UPSTREAM_REJECTED]


def test_prefilter_rejects_invalid_structure(
    ml_candidate_factory,
    ml_requirement,
    ml_policy,
) -> None:
    result = evaluate_pre_filter(
        ml_candidate_factory(parseable=False),
        ml_requirement,
        ml_policy,
    )
    assert result.decision is MLDecision.REJECT
    assert PreFilterReasonCode.INVALID_STRUCTURE in result.reason_codes


def test_prefilter_respects_numeric_tolerance(
    ml_candidate_factory,
    ml_policy,
) -> None:
    requirement = MLRequirementView(
        requirement_id="req",
        revision=1,
        confirmed_by_user=True,
        allow_ml=True,
        band_gap_ev=NumericRange(min=0.5, max=1.0, unit="eV"),
    )
    candidate = ml_candidate_factory(band_gap=1.0 + 0.5e-8)
    result = evaluate_pre_filter(candidate, requirement, ml_policy)
    assert result.decision is MLDecision.PASS
