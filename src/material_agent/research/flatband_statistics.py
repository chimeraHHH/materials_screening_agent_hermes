"""Draft-preregistered agreement and paired-inference utilities.

The analysis unit is a leakage group.  Candidate packets within one case and
cases within one leakage group are never treated as independent bootstrap or
randomization units.
"""

from __future__ import annotations

import math
import random
from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from itertools import product
from typing import Literal


MIN_INDEPENDENT_CLUSTERS_PER_STRATUM = 10
MAX_EXACT_SIGN_ASSIGNMENTS = 100_000
MONTE_CARLO_SIGN_ASSIGNMENTS = 100_000


class PairedEstimand(StrEnum):
    PRIMARY_EQUAL_IID_OOD = "PRIMARY_EQUAL_IID_OOD"
    IID_ONLY = "IID_ONLY"
    OOD_ONLY = "OOD_ONLY"


@dataclass(frozen=True, slots=True)
class PairedCaseScore:
    case_id: str
    leakage_group_id: str
    stratum: Literal["IID", "OOD"]
    baseline: float
    treatment: float

    def __post_init__(self) -> None:
        if not self.case_id or not self.leakage_group_id:
            raise ValueError("case and leakage-group IDs are required")
        for label, value in (("baseline", self.baseline), ("treatment", self.treatment)):
            if not isinstance(value, (int, float)) or isinstance(value, bool):
                raise ValueError(f"{label} score must be numeric")
            if not math.isfinite(float(value)) or not 0.0 <= float(value) <= 1.0:
                raise ValueError(f"{label} score must be finite and in [0, 1]")

    @property
    def difference(self) -> float:
        return float(self.treatment) - float(self.baseline)


@dataclass(frozen=True, slots=True)
class BootstrapInterval:
    observed_delta: float
    lower: float
    upper: float
    replicates: int
    seed: int


@dataclass(frozen=True, slots=True)
class RandomizationResult:
    observed_delta: float
    p_value: float
    replicates: int
    exact: bool
    seed: int


@dataclass(frozen=True, slots=True)
class RatedUnit:
    case_id: str
    ratings: tuple[int | None, int | None]

    def __post_init__(self) -> None:
        if not self.case_id:
            raise ValueError("rated unit requires a case ID")
        if any(
            value is not None and (type(value) is not int or not 0 <= value <= 3)
            for value in self.ratings
        ):
            raise ValueError("rated-unit values must be grades zero through three or missing")


@dataclass(frozen=True, slots=True)
class AlphaBootstrapInterval:
    observed_alpha: float | None
    lower: float | None
    upper: float | None
    valid_replicates: int
    undefined_replicates: int
    seed: int


def _validate_paired_scores(
    scores: Sequence[PairedCaseScore],
    *,
    estimand: PairedEstimand = PairedEstimand.PRIMARY_EQUAL_IID_OOD,
    min_clusters_per_stratum: int | None = None,
) -> tuple[PairedCaseScore, ...]:
    values = tuple(scores)
    if not values:
        raise ValueError("paired analysis requires at least one case")
    case_ids = tuple(item.case_id for item in values)
    if len(case_ids) != len(set(case_ids)):
        raise ValueError("paired case IDs must be unique")
    required_strata = _required_strata(estimand)
    strata = {item.stratum for item in values}
    missing_strata = set(required_strata) - strata
    if missing_strata:
        raise ValueError(
            "paired analysis is missing required strata: "
            + ", ".join(sorted(missing_strata))
        )
    group_strata: dict[str, str] = {}
    for item in values:
        previous = group_strata.setdefault(item.leakage_group_id, item.stratum)
        if previous != item.stratum:
            raise ValueError("a leakage group crosses IID and OOD strata")
    if min_clusters_per_stratum is not None:
        if (
            type(min_clusters_per_stratum) is not int
            or min_clusters_per_stratum < 1
        ):
            raise ValueError("minimum clusters per stratum must be a positive integer")
        for stratum in required_strata:
            independent_clusters = {
                item.leakage_group_id
                for item in values
                if item.stratum == stratum
            }
            if len(independent_clusters) < min_clusters_per_stratum:
                raise ValueError(
                    f"{stratum} requires at least {min_clusters_per_stratum} "
                    "independent leakage groups"
                )
    return values


def _required_strata(estimand: PairedEstimand) -> tuple[Literal["IID", "OOD"], ...]:
    if estimand is PairedEstimand.PRIMARY_EQUAL_IID_OOD:
        return ("IID", "OOD")
    if estimand is PairedEstimand.IID_ONLY:
        return ("IID",)
    if estimand is PairedEstimand.OOD_ONLY:
        return ("OOD",)
    raise ValueError("unknown paired estimand")


def _combine_stratum_means(
    means: Mapping[str, float], *, estimand: PairedEstimand
) -> float:
    if estimand is PairedEstimand.PRIMARY_EQUAL_IID_OOD:
        return 0.5 * means["IID"] + 0.5 * means["OOD"]
    return means[_required_strata(estimand)[0]]


def paired_estimand_delta(
    scores: Sequence[PairedCaseScore],
    *,
    estimand: PairedEstimand = PairedEstimand.PRIMARY_EQUAL_IID_OOD,
) -> float:
    """Compute the declared primary or single-stratum paired estimand."""

    values = _validate_paired_scores(scores, estimand=estimand)
    requested_strata = _required_strata(estimand)
    means = {
        stratum: sum(item.difference for item in values if item.stratum == stratum)
        / sum(item.stratum == stratum for item in values)
        for stratum in requested_strata
    }
    return _combine_stratum_means(means, estimand=estimand)


def equal_strata_paired_delta(scores: Sequence[PairedCaseScore]) -> float:
    """Compute the primary estimand with equal weight on IID and OOD means."""

    return paired_estimand_delta(
        scores, estimand=PairedEstimand.PRIMARY_EQUAL_IID_OOD
    )


def _percentile(sorted_values: Sequence[float], quantile: float) -> float:
    if not sorted_values or not 0.0 <= quantile <= 1.0:
        raise ValueError("percentile input is invalid")
    position = (len(sorted_values) - 1) * quantile
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return float(sorted_values[lower])
    weight = position - lower
    return float(sorted_values[lower] * (1.0 - weight) + sorted_values[upper] * weight)


def paired_group_bootstrap_interval(
    scores: Sequence[PairedCaseScore],
    *,
    replicates: int = 50_000,
    seed: int,
    confidence: float = 0.95,
    estimand: PairedEstimand = PairedEstimand.PRIMARY_EQUAL_IID_OOD,
    min_clusters_per_stratum: int = MIN_INDEPENDENT_CLUSTERS_PER_STRATUM,
) -> BootstrapInterval:
    values = _validate_paired_scores(
        scores,
        estimand=estimand,
        min_clusters_per_stratum=min_clusters_per_stratum,
    )
    if type(replicates) is not int or replicates < 1:
        raise ValueError("bootstrap replicates must be a positive integer")
    if type(seed) is not int or seed < 0:
        raise ValueError("bootstrap seed must be a non-negative integer")
    if not 0.0 < confidence < 1.0:
        raise ValueError("bootstrap confidence must lie strictly between zero and one")

    requested_strata = _required_strata(estimand)
    by_stratum_group: dict[str, dict[str, tuple[PairedCaseScore, ...]]] = {}
    for stratum in requested_strata:
        grouped: dict[str, list[PairedCaseScore]] = defaultdict(list)
        for item in values:
            if item.stratum == stratum:
                grouped[item.leakage_group_id].append(item)
        by_stratum_group[stratum] = {
            group_id: tuple(items) for group_id, items in sorted(grouped.items())
        }

    rng = random.Random(seed)
    draws: list[float] = []
    for _ in range(replicates):
        stratum_means: dict[str, float] = {}
        for stratum in requested_strata:
            groups = by_stratum_group[stratum]
            group_ids = tuple(groups)
            sampled = [rng.choice(group_ids) for _ in group_ids]
            differences = [
                item.difference for group_id in sampled for item in groups[group_id]
            ]
            stratum_means[stratum] = sum(differences) / len(differences)
        draws.append(_combine_stratum_means(stratum_means, estimand=estimand))

    draws.sort()
    alpha = 1.0 - confidence
    return BootstrapInterval(
        observed_delta=paired_estimand_delta(values, estimand=estimand),
        lower=_percentile(draws, alpha / 2.0),
        upper=_percentile(draws, 1.0 - alpha / 2.0),
        replicates=replicates,
        seed=seed,
    )


def paired_group_randomization_test(
    scores: Sequence[PairedCaseScore],
    *,
    seed: int,
    estimand: PairedEstimand = PairedEstimand.PRIMARY_EQUAL_IID_OOD,
    min_clusters_per_stratum: int = MIN_INDEPENDENT_CLUSTERS_PER_STRATUM,
) -> RandomizationResult:
    values = _validate_paired_scores(
        scores,
        estimand=estimand,
        min_clusters_per_stratum=min_clusters_per_stratum,
    )
    if type(seed) is not int or seed < 0:
        raise ValueError("randomization seed must be a non-negative integer")

    requested_strata = _required_strata(estimand)
    stratum_case_counts = {
        stratum: sum(item.stratum == stratum for item in values)
        for stratum in requested_strata
    }
    stratum_weight = (
        {"IID": 0.5, "OOD": 0.5}
        if estimand is PairedEstimand.PRIMARY_EQUAL_IID_OOD
        else {requested_strata[0]: 1.0}
    )
    group_contributions: dict[str, float] = defaultdict(float)
    for item in values:
        if item.stratum in requested_strata:
            group_contributions[item.leakage_group_id] += (
                stratum_weight[item.stratum]
                * item.difference
                / stratum_case_counts[item.stratum]
            )
    group_ids = tuple(sorted(group_contributions))
    observed = sum(group_contributions.values())
    total_assignments = 2 ** len(group_ids)
    exact = total_assignments <= MAX_EXACT_SIGN_ASSIGNMENTS
    replicates = (
        total_assignments if exact else MONTE_CARLO_SIGN_ASSIGNMENTS
    )

    if exact:
        sign_draws = product((-1.0, 1.0), repeat=len(group_ids))
    else:
        rng = random.Random(seed)
        sign_draws = (
            tuple(rng.choice((-1.0, 1.0)) for _ in group_ids)
            for _ in range(replicates)
        )
    at_least_as_large = 0
    for signs in sign_draws:
        permuted_delta = sum(
            sign * group_contributions[group_id]
            for sign, group_id in zip(signs, group_ids)
        )
        if permuted_delta >= observed - 1e-15:
            at_least_as_large += 1
    p_value = (
        at_least_as_large / replicates
        if exact
        else (at_least_as_large + 1) / (replicates + 1)
    )
    return RandomizationResult(
        observed_delta=observed,
        p_value=p_value,
        replicates=replicates,
        exact=exact,
        seed=seed,
    )


def holm_adjust(p_values: Mapping[str, float]) -> dict[str, float]:
    if not p_values:
        raise ValueError("Holm adjustment requires at least one hypothesis")
    for hypothesis, value in p_values.items():
        if not hypothesis or not isinstance(value, (int, float)) or isinstance(value, bool):
            raise ValueError("Holm hypothesis and p-value are invalid")
        if not math.isfinite(float(value)) or not 0.0 <= float(value) <= 1.0:
            raise ValueError("Holm p-values must be finite and in [0, 1]")
    ordered = sorted(((float(value), key) for key, value in p_values.items()))
    total = len(ordered)
    adjusted: dict[str, float] = {}
    running = 0.0
    for index, (value, key) in enumerate(ordered):
        running = max(running, min(1.0, (total - index) * value))
        adjusted[key] = running
    return {key: adjusted[key] for key in sorted(adjusted)}


def ordinal_krippendorff_alpha(
    units: Sequence[Sequence[int | None]],
) -> float | None:
    """Compute Krippendorff alpha using the original ordinal distance.

    Units with fewer than two non-missing ratings do not contribute.  ``None``
    is returned when expected disagreement is zero; this state must never be
    interpreted as perfect agreement.
    """

    prepared: list[tuple[int, ...]] = []
    for unit in units:
        ratings = tuple(value for value in unit if value is not None)
        if any(type(value) is not int or not 0 <= value <= 3 for value in ratings):
            raise ValueError("ordinal ratings must be integers zero through three or missing")
        if len(ratings) >= 2:
            prepared.append(ratings)
    if not prepared:
        raise ValueError("alpha requires at least one unit with two ratings")

    categories = tuple(sorted({value for unit in prepared for value in unit}))
    coincidence: dict[tuple[int, int], float] = defaultdict(float)
    for ratings in prepared:
        counts = Counter(ratings)
        denominator = len(ratings) - 1
        for left in categories:
            for right in categories:
                if left == right:
                    pairs = counts[left] * (counts[left] - 1)
                else:
                    pairs = counts[left] * counts[right]
                coincidence[left, right] += pairs / denominator

    marginals = {
        category: sum(coincidence[category, other] for other in categories)
        for category in categories
    }
    total = sum(marginals.values())
    if total <= 1.0:
        raise ValueError("alpha requires at least two coincidences")

    def ordinal_distance(left: int, right: int) -> float:
        if left == right:
            return 0.0
        low, high = sorted((left, right))
        between = sum(
            count for category, count in marginals.items() if low <= category <= high
        )
        return (between - (marginals[low] + marginals[high]) / 2.0) ** 2

    observed = sum(
        coincidence[left, right] * ordinal_distance(left, right)
        for left in categories
        for right in categories
    )
    expected = 0.0
    for left in categories:
        for right in categories:
            if left == right:
                expected_pairs = marginals[left] * (marginals[left] - 1.0) / (total - 1.0)
            else:
                expected_pairs = marginals[left] * marginals[right] / (total - 1.0)
            expected += expected_pairs * ordinal_distance(left, right)
    if expected == 0.0:
        return None
    return 1.0 - observed / expected


def case_cluster_bootstrap_alpha(
    units: Sequence[RatedUnit],
    *,
    replicates: int = 50_000,
    seed: int,
    confidence: float = 0.95,
) -> AlphaBootstrapInterval:
    """Bootstrap ordinal alpha by resampling whole cases, not packets."""

    values = tuple(units)
    if not values:
        raise ValueError("alpha bootstrap requires rated units")
    if type(replicates) is not int or replicates < 1:
        raise ValueError("alpha bootstrap replicates must be positive")
    if type(seed) is not int or seed < 0:
        raise ValueError("alpha bootstrap seed must be non-negative")
    if not 0.0 < confidence < 1.0:
        raise ValueError("alpha bootstrap confidence must lie in (0, 1)")

    grouped: dict[str, list[RatedUnit]] = defaultdict(list)
    for unit in values:
        grouped[unit.case_id].append(unit)
    case_ids = tuple(sorted(grouped))
    if len(case_ids) < 2:
        raise ValueError("alpha bootstrap requires at least two cases")
    observed = ordinal_krippendorff_alpha([unit.ratings for unit in values])
    rng = random.Random(seed)
    draws: list[float] = []
    undefined = 0
    for _ in range(replicates):
        sampled_case_ids = [rng.choice(case_ids) for _ in case_ids]
        sampled_ratings = [
            unit.ratings
            for case_id in sampled_case_ids
            for unit in grouped[case_id]
        ]
        try:
            alpha = ordinal_krippendorff_alpha(sampled_ratings)
        except ValueError as exc:
            if str(exc) != "alpha requires at least one unit with two ratings":
                raise
            alpha = None
        if alpha is None:
            undefined += 1
        else:
            draws.append(alpha)
    if not draws:
        return AlphaBootstrapInterval(
            observed_alpha=observed,
            lower=None,
            upper=None,
            valid_replicates=0,
            undefined_replicates=undefined,
            seed=seed,
        )
    draws.sort()
    alpha_tail = 1.0 - confidence
    return AlphaBootstrapInterval(
        observed_alpha=observed,
        lower=_percentile(draws, alpha_tail / 2.0),
        upper=_percentile(draws, 1.0 - alpha_tail / 2.0),
        valid_replicates=len(draws),
        undefined_replicates=undefined,
        seed=seed,
    )
