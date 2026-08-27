"""Draft-preregistered descriptive metrics for flat-band hypotheses."""

from __future__ import annotations

import math
from collections.abc import Sequence
from enum import StrEnum

TOP_K = 5
LINEAR_IDEAL_DCG_AT_5 = sum(3.0 / math.log2(rank + 1) for rank in range(1, 6))
EXPONENTIAL_IDEAL_DCG_AT_5 = sum(7.0 / math.log2(rank + 1) for rank in range(1, 6))


class GainKind(StrEnum):
    LINEAR_PRIMARY = "LINEAR_PRIMARY"
    EXPONENTIAL_SENSITIVITY = "EXPONENTIAL_SENSITIVITY"


def _validate_grades(grades: Sequence[int]) -> tuple[int, ...]:
    values = tuple(grades)
    if len(values) > TOP_K:
        raise ValueError("at most five relevance grades are allowed")
    if any(type(value) is not int or not 0 <= value <= 3 for value in values):
        raise ValueError("relevance grades must be exact integers from zero to three")
    return values


def _validate_cluster_ids(
    cluster_ids: Sequence[str], *, expected_length: int
) -> tuple[str, ...]:
    values = tuple(cluster_ids)
    if len(values) != expected_length:
        raise ValueError("cluster count differs from returned candidate count")
    if any(not isinstance(value, str) or not value for value in values):
        raise ValueError("cluster IDs must be non-empty strings")
    return values


def first_of_cluster_mask(cluster_ids: Sequence[str]) -> tuple[bool, ...]:
    """Return True for the first occurrence of each strict hypothesis cluster."""

    values = _validate_cluster_ids(cluster_ids, expected_length=len(cluster_ids))
    seen: set[str] = set()
    mask: list[bool] = []
    for value in values:
        is_first = value not in seen
        mask.append(is_first)
        seen.add(value)
    return tuple(mask)


def absolute_ndcg_at_5(
    *,
    grades: Sequence[int],
    strict_hypothesis_cluster_ids: Sequence[str],
    gain_kind: GainKind = GainKind.LINEAR_PRIMARY,
) -> float:
    """Compute the preregistered absolute normalized graded utility at rank five.

    Unlike classic query-relative nDCG, the denominator is the fixed ideal of
    five grade-three hypotheses. Missing positions and repeated strict
    hypothesis clusters contribute zero.
    """

    grade_values = _validate_grades(grades)
    clusters = _validate_cluster_ids(
        strict_hypothesis_cluster_ids, expected_length=len(grade_values)
    )
    mask = first_of_cluster_mask(clusters)
    if gain_kind is GainKind.LINEAR_PRIMARY:
        gain = float
        denominator = LINEAR_IDEAL_DCG_AT_5
    elif gain_kind is GainKind.EXPONENTIAL_SENSITIVITY:
        gain = lambda value: float(2**value - 1)
        denominator = EXPONENTIAL_IDEAL_DCG_AT_5
    else:  # pragma: no cover - defensive against non-enum callers
        raise ValueError("unknown gain kind")
    discounted = sum(
        (gain(grade) if keep else 0.0) / math.log2(rank + 1)
        for rank, (grade, keep) in enumerate(zip(grade_values, mask), start=1)
    )
    return round(discounted / denominator, 12)


def success_at_5(
    *, grades: Sequence[int], strict_hypothesis_cluster_ids: Sequence[str]
) -> int:
    grade_values = _validate_grades(grades)
    clusters = _validate_cluster_ids(
        strict_hypothesis_cluster_ids, expected_length=len(grade_values)
    )
    return int(
        any(
            keep and grade >= 2
            for grade, keep in zip(grade_values, first_of_cluster_mask(clusters))
        )
    )


def strong_success_at_5(
    *, grades: Sequence[int], strict_hypothesis_cluster_ids: Sequence[str]
) -> int:
    grade_values = _validate_grades(grades)
    clusters = _validate_cluster_ids(
        strict_hypothesis_cluster_ids, expected_length=len(grade_values)
    )
    return int(
        any(
            keep and grade == 3
            for grade, keep in zip(grade_values, first_of_cluster_mask(clusters))
        )
    )


def completion_at_5(returned_count: int) -> float:
    if type(returned_count) is not int or not 0 <= returned_count <= TOP_K:
        raise ValueError("returned count must be an integer from zero to five")
    return round(returned_count / TOP_K, 12)


def evidence_valid_at_5(evidence_valid: Sequence[bool]) -> float:
    values = tuple(evidence_valid)
    if len(values) > TOP_K or any(type(value) is not bool for value in values):
        raise ValueError("evidence-valid flags must contain at most five booleans")
    return round(sum(values) / TOP_K, 12)


def duplicate_rate_at_5(strict_hypothesis_cluster_ids: Sequence[str]) -> float:
    clusters = _validate_cluster_ids(
        strict_hypothesis_cluster_ids, expected_length=len(strict_hypothesis_cluster_ids)
    )
    if len(clusters) > TOP_K:
        raise ValueError("at most five strict hypothesis cluster IDs are allowed")
    count = len(clusters)
    if count <= 1:
        return 0.0
    return round((count - len(set(clusters))) / (count - 1), 12)
