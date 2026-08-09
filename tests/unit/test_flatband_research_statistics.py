from __future__ import annotations

import pytest

from material_agent.research.flatband_metrics import (
    EXPONENTIAL_IDEAL_DCG_AT_5,
    LINEAR_IDEAL_DCG_AT_5,
    GainKind,
    absolute_ndcg_at_5,
    completion_at_5,
    duplicate_rate_at_5,
    evidence_valid_at_5,
    strong_success_at_5,
    success_at_5,
)
from material_agent.research.flatband_statistics import (
    MONTE_CARLO_SIGN_ASSIGNMENTS,
    PairedEstimand,
    PairedCaseScore,
    RatedUnit,
    case_cluster_bootstrap_alpha,
    equal_strata_paired_delta,
    holm_adjust,
    ordinal_krippendorff_alpha,
    paired_estimand_delta,
    paired_group_bootstrap_interval,
    paired_group_randomization_test,
)


def test_absolute_ndcg_uses_fixed_ideal_and_zeroes_repeated_ideas() -> None:
    clusters = ("a", "b", "c", "d", "e")
    assert LINEAR_IDEAL_DCG_AT_5 == pytest.approx(8.8453773566)
    assert EXPONENTIAL_IDEAL_DCG_AT_5 == pytest.approx(20.6392138322)
    assert absolute_ndcg_at_5(grades=(3, 3, 3, 3, 3), strict_hypothesis_cluster_ids=clusters) == 1.0
    repeated = absolute_ndcg_at_5(
        grades=(3, 3, 3, 3, 3),
        strict_hypothesis_cluster_ids=("same",) * 5,
    )
    first_only = absolute_ndcg_at_5(
        grades=(3,), strict_hypothesis_cluster_ids=("same",)
    )
    assert repeated == first_only
    assert absolute_ndcg_at_5(
        grades=(3, 2),
        strict_hypothesis_cluster_ids=("a", "b"),
        gain_kind=GainKind.EXPONENTIAL_SENSITIVITY,
    ) > 0


def test_secondary_metrics_keep_missingness_and_duplicates_separate() -> None:
    assert completion_at_5(2) == 0.4
    assert evidence_valid_at_5((True, False)) == 0.2
    assert duplicate_rate_at_5(("a", "a", "b")) == 0.5
    assert duplicate_rate_at_5(()) == 0.0
    assert success_at_5(grades=(1, 2), strict_hypothesis_cluster_ids=("a", "b")) == 1
    assert strong_success_at_5(grades=(3, 3), strict_hypothesis_cluster_ids=("a", "a")) == 1


def test_ordinal_alpha_matches_reference_vectors_and_degenerate_is_undefined() -> None:
    assert ordinal_krippendorff_alpha(((0, 0), (1, 1), (2, 2), (3, 3))) == 1.0
    assert ordinal_krippendorff_alpha(
        ((0, 1), (1, 1), (2, 3), (3, 2), (0, 0))
    ) == pytest.approx(0.8025806451612904)
    assert ordinal_krippendorff_alpha(((2, 2), (2, 2))) is None


def test_alpha_bootstrap_resamples_whole_cases() -> None:
    units = tuple(
        RatedUnit(
            case_id=f"case-{case}",
            ratings=(grade, grade if case % 3 else min(3, grade + 1)),
        )
        for case in range(10)
        for grade in (0, 1, 2, 3)
    )
    result = case_cluster_bootstrap_alpha(units, replicates=200, seed=11)
    assert result.observed_alpha is not None
    assert result.lower is not None and result.upper is not None
    assert result.valid_replicates + result.undefined_replicates == 200


def test_alpha_bootstrap_counts_all_missing_resamples_as_undefined() -> None:
    units = (
        RatedUnit(case_id="rated", ratings=(0, 1)),
        RatedUnit(case_id="missing-a", ratings=(None, None)),
        RatedUnit(case_id="missing-b", ratings=(None, None)),
    )
    result = case_cluster_bootstrap_alpha(units, replicates=300, seed=13)
    assert result.observed_alpha is not None
    assert result.undefined_replicates > 0
    assert result.valid_replicates + result.undefined_replicates == 300


def _paired_scores() -> tuple[PairedCaseScore, ...]:
    return tuple(
        PairedCaseScore(
            case_id=f"{stratum.lower()}-{index}",
            leakage_group_id=f"{stratum.lower()}-group-{index}",
            stratum=stratum,
            baseline=0.30 + index * 0.01,
            treatment=0.40 + index * 0.01,
        )
        for stratum in ("IID", "OOD")
        for index in range(10)
    )


def test_paired_inference_uses_equal_strata_and_group_level_randomness() -> None:
    scores = _paired_scores()
    assert equal_strata_paired_delta(scores) == pytest.approx(0.10)
    interval = paired_group_bootstrap_interval(scores, replicates=300, seed=17)
    assert interval.observed_delta == pytest.approx(0.10)
    assert interval.lower == pytest.approx(0.10)
    assert interval.upper == pytest.approx(0.10)
    randomization = paired_group_randomization_test(scores, seed=19)
    assert randomization.observed_delta == pytest.approx(0.10)
    assert randomization.exact is False
    assert randomization.replicates == MONTE_CARLO_SIGN_ASSIGNMENTS
    assert 0 < randomization.p_value < 0.05


def test_single_stratum_secondary_estimands_and_exact_randomization() -> None:
    iid_scores = tuple(item for item in _paired_scores() if item.stratum == "IID")
    assert paired_estimand_delta(
        iid_scores, estimand=PairedEstimand.IID_ONLY
    ) == pytest.approx(0.10)
    randomization = paired_group_randomization_test(
        iid_scores,
        seed=23,
        estimand=PairedEstimand.IID_ONLY,
    )
    assert randomization.exact is True
    assert randomization.replicates == 2**10
    assert randomization.p_value == pytest.approx(1 / 2**10)

    ood_scores = tuple(item for item in _paired_scores() if item.stratum == "OOD")
    interval = paired_group_bootstrap_interval(
        ood_scores,
        replicates=200,
        seed=29,
        estimand=PairedEstimand.OOD_ONLY,
    )
    assert interval.observed_delta == pytest.approx(0.10)
    assert interval.lower == pytest.approx(0.10)
    assert interval.upper == pytest.approx(0.10)


def test_paired_inference_rejects_too_few_independent_clusters_by_default() -> None:
    insufficient = tuple(
        PairedCaseScore(
            case_id=f"{stratum.lower()}-{index}",
            leakage_group_id=f"{stratum.lower()}-group-{index}",
            stratum=stratum,
            baseline=0.2,
            treatment=0.3,
        )
        for stratum in ("IID", "OOD")
        for index in range(9)
    )
    with pytest.raises(ValueError, match="requires at least 10 independent"):
        paired_group_bootstrap_interval(insufficient, replicates=10, seed=31)
    with pytest.raises(ValueError, match="requires at least 10 independent"):
        paired_group_randomization_test(insufficient, seed=31)

    relaxed = paired_group_randomization_test(
        insufficient,
        seed=31,
        min_clusters_per_stratum=9,
    )
    assert relaxed.exact is False


def test_holm_adjustment_is_monotone_and_key_stable() -> None:
    adjusted = holm_adjust({"ood": 0.03, "iid": 0.01, "no-e1": 0.04})
    assert tuple(adjusted) == ("iid", "no-e1", "ood")
    assert adjusted["iid"] == pytest.approx(0.03)
    assert adjusted["ood"] >= adjusted["iid"]
    assert adjusted["no-e1"] >= adjusted["ood"]
