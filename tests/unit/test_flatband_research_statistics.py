from __future__ import annotations

from functools import lru_cache
from typing import Any, TypeVar

import pytest

from material_agent.inspiration.models import StrictModel, canonical_sha256, deterministic_id
from material_agent.research.flatband_contracts import (
    Assessability,
    BenchmarkSplit,
    BenchmarkSplitManifestV1,
    Dimensionality,
    MechanismFamily,
    OodHoldoutAxis,
    OodHoldoutFamilyV1,
    SplitCaseRefV1,
    SplitManifestKind,
    TargetBandClass,
)
from material_agent.research.flatband_leakage import (
    LeakageAxis,
    LeakageComponentReleaseV1,
    LeakageMembershipV1,
    build_leakage_component_release,
)

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


ModelT = TypeVar("ModelT", bound=StrictModel)


def _identified(
    model_type: type[ModelT],
    *,
    id_field: str,
    sha_field: str,
    prefix: str,
    values: dict[str, Any],
) -> ModelT:
    draft = model_type.model_construct(**values)
    digest = canonical_sha256(
        draft.model_dump(mode="python", exclude={id_field, sha_field})
    )
    return model_type.model_validate(
        {
            **values,
            sha_field: digest,
            id_field: deterministic_id(prefix, {sha_field: digest}),
        }
    )
from material_agent.research.flatband_statistics import (
    MONTE_CARLO_SIGN_ASSIGNMENTS,
    AssessabilityUnit,
    ComponentRatedUnit,
    PairedEstimand,
    PairedCaseScore,
    RatedUnit,
    assessability_agreement_gate,
    case_cluster_bootstrap_alpha,
    case_component_bootstrap_alpha,
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
            blinded_unit_id=f"blind-{case}-{grade}",
            ratings=(grade, grade if case % 3 else min(3, grade + 1)),
        )
        for case in range(10)
        for grade in (0, 1, 2, 3)
    )
    result = case_cluster_bootstrap_alpha(units, replicates=200, seed=11)
    assert result.observed_alpha is not None
    assert result.lower is not None and result.upper is not None
    assert result.valid_replicates + result.undefined_replicates == 200


def test_component_alpha_bootstrap_resamples_components_then_whole_cases() -> None:
    units = tuple(
        ComponentRatedUnit(
            case_id=f"case-{case}",
            leakage_component_id=f"component-{case // 2}",
            pooled_unit_id=f"pool-{case}-{grade}",
            ratings=(grade, grade if case % 3 else min(3, grade + 1)),
        )
        for case in range(10)
        for grade in (0, 1, 2, 3)
    )
    result = case_component_bootstrap_alpha(units, replicates=200, seed=17)
    assert result.observed_alpha is not None
    assert result.valid_replicates + result.undefined_replicates == 200
    with pytest.raises(ValueError, match="pooled-unit IDs must be unique"):
        case_component_bootstrap_alpha(
            (units[0], units[1].__class__(
                case_id=units[1].case_id,
                leakage_component_id=units[1].leakage_component_id,
                pooled_unit_id=units[0].pooled_unit_id,
                ratings=units[1].ratings,
            )),
            replicates=10,
            seed=17,
        )


def test_formal_alpha_input_rejects_missing_extra_or_duplicate_units() -> None:
    with pytest.raises(ValueError, match="exactly two grades"):
        RatedUnit(case_id="case-a", blinded_unit_id="blind-a", ratings=(0, None))  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="exactly two grades"):
        RatedUnit(case_id="case-a", blinded_unit_id="blind-a", ratings=(0, 0, 0))  # type: ignore[arg-type]

    duplicate = (
        RatedUnit(case_id="case-a", blinded_unit_id="blind-a", ratings=(0, 1)),
        RatedUnit(case_id="case-b", blinded_unit_id="blind-a", ratings=(2, 3)),
    )
    with pytest.raises(ValueError, match="blinded-unit IDs must be unique"):
        case_cluster_bootstrap_alpha(duplicate, replicates=10, seed=13)


def test_assessability_gate_prevents_one_sided_invalid_from_disappearing() -> None:
    units = tuple(
        AssessabilityUnit(
            case_id=f"case-{index}",
            blinded_unit_id=f"blind-{index}",
            assessments=(Assessability.ASSESSABLE, Assessability.ASSESSABLE),
        )
        for index in range(9)
    ) + (
        AssessabilityUnit(
            case_id="case-9",
            blinded_unit_id="blind-9",
            assessments=(Assessability.CASE_INVALID, Assessability.ASSESSABLE),
        ),
    )
    result = assessability_agreement_gate(units, minimum_exact_agreement=0.80)
    assert result.exact_agreement_rate == 0.9
    assert result.unilateral_case_invalid_units == 1
    assert result.case_invalid_units == 1
    assert result.passed is False

    # Grade alpha alone would silently discard a one-sided missing grade and
    # report perfect agreement; the separate Gate makes that state unusable.
    assert ordinal_krippendorff_alpha(((0, 0), (1, 1), (2, 2), (None, 3))) == 1.0


def test_assessability_gate_requires_a_frozen_explicit_threshold() -> None:
    units = (
        AssessabilityUnit(
            case_id="case-1",
            blinded_unit_id="blind-1",
            assessments=(
                Assessability.SYSTEM_PACKET_INVALID,
                Assessability.SYSTEM_PACKET_INVALID,
            ),
        ),
    )
    assert assessability_agreement_gate(
        units, minimum_exact_agreement=1.0
    ).passed is True
    with pytest.raises(ValueError, match="threshold"):
        assessability_agreement_gate(units, minimum_exact_agreement=float("nan"))


@lru_cache(maxsize=1)
def _locked_design() -> tuple[
    BenchmarkSplitManifestV1, LeakageComponentReleaseV1
]:
    cases: list[SplitCaseRefV1] = []
    memberships: list[LeakageMembershipV1] = []
    holdouts: list[OodHoldoutFamilyV1] = []
    specifications = (
        (BenchmarkSplit.DEVELOPMENT, "dev", 60, 20),
        (BenchmarkSplit.LOCKED_IID, "iid", 30, 10),
        (BenchmarkSplit.LOCKED_OOD, "ood", 30, 10),
    )
    component_offset = 0
    case_index = 0
    for split, prefix, case_count, component_count in specifications:
        for local_index in range(case_count):
            component = component_offset + local_index % component_count
            case_id = f"main-{prefix}-{local_index:03d}"
            case_sha = canonical_sha256(("main-case", case_index))
            axis_groups = {
                LeakageAxis.COMPOSITION_FAMILY: f"composition-{component:03d}",
                LeakageAxis.STRUCTURE_PROTOTYPE: f"prototype-{component:03d}",
                LeakageAxis.STRUCTURE_FINGERPRINT: f"fingerprint-{component:03d}",
                LeakageAxis.ARTICLE_OR_SOURCE_FAMILY: f"article-{component:03d}",
                LeakageAxis.MECHANISM_FAMILY: f"mechanism-{component:03d}",
            }
            for axis, group_id in sorted(
                axis_groups.items(), key=lambda item: item[0].value
            ):
                memberships.append(
                    LeakageMembershipV1(
                        case_id=case_id,
                        case_sha256=case_sha,
                        axis=axis,
                        group_id=group_id,
                        provenance_sha256=canonical_sha256(
                            (case_id, axis.value, group_id)
                        ),
                    )
                )
            cases.append(
                SplitCaseRefV1(
                    case_id=case_id,
                    case_sha256=case_sha,
                    split=split,
                    target_class=(
                        TargetBandClass.FB100
                        if local_index % 2 == 0
                        else TargetBandClass.NB300
                    ),
                    dimensionality=(
                        Dimensionality.TWO_D
                        if local_index % 2 == 0
                        else Dimensionality.THREE_D
                    ),
                    primary_mechanism_stratum=MechanismFamily.LATTICE_INTERFERENCE,
                    leakage_group_ids=tuple(sorted(axis_groups.values())),
                )
            )
            case_index += 1
        if split is BenchmarkSplit.LOCKED_OOD:
            holdouts.extend(
                OodHoldoutFamilyV1(
                    axis=OodHoldoutAxis.MECHANISM_FAMILY,
                    group_id=f"mechanism-{component:03d}",
                )
                for component in range(
                    component_offset, component_offset + component_count
                )
            )
        component_offset += component_count
    manifest = _identified(
        BenchmarkSplitManifestV1,
        id_field="manifest_id",
        sha_field="manifest_sha256",
        prefix="split-manifest",
        values={
            "manifest_kind": SplitManifestKind.MAIN_120,
            "split_seed": 20260809,
            "cases": tuple(sorted(cases, key=lambda item: item.case_id)),
            "ood_holdout_families": tuple(
                sorted(holdouts, key=lambda item: (item.axis.value, item.group_id))
            ),
        },
    )
    release = build_leakage_component_release(
        split_manifest=manifest,
        memberships=memberships,
        construction_policy_sha256=canonical_sha256("leakage-policy-v1"),
        created_at="2026-08-09T12:00:00+08:00",
    )
    return manifest, release


def _paired_scores() -> tuple[PairedCaseScore, ...]:
    manifest, _ = _locked_design()
    return tuple(
        PairedCaseScore(
            case_id=item.case_id,
            baseline=0.30,
            treatment=0.40,
        )
        for item in manifest.cases
        if item.split in {BenchmarkSplit.LOCKED_IID, BenchmarkSplit.LOCKED_OOD}
    )


def test_paired_inference_uses_equal_strata_and_group_level_randomness() -> None:
    manifest, leakage = _locked_design()
    scores = _paired_scores()
    assert equal_strata_paired_delta(
        scores, split_manifest=manifest, leakage_release=leakage
    ) == pytest.approx(0.10)
    interval = paired_group_bootstrap_interval(
        scores,
        split_manifest=manifest,
        leakage_release=leakage,
        replicates=300,
        seed=17,
    )
    assert interval.observed_delta == pytest.approx(0.10)
    assert interval.lower == pytest.approx(0.10)
    assert interval.upper == pytest.approx(0.10)
    randomization = paired_group_randomization_test(
        scores,
        split_manifest=manifest,
        leakage_release=leakage,
        seed=19,
    )
    assert randomization.observed_delta == pytest.approx(0.10)
    assert randomization.exact is False
    assert randomization.replicates == MONTE_CARLO_SIGN_ASSIGNMENTS
    assert 0 < randomization.p_value < 0.05


def test_single_stratum_secondary_estimands_and_exact_randomization() -> None:
    manifest, leakage = _locked_design()
    iid_case_ids = {
        item.case_id
        for item in manifest.cases
        if item.split is BenchmarkSplit.LOCKED_IID
    }
    iid_scores = tuple(
        item for item in _paired_scores() if item.case_id in iid_case_ids
    )
    assert paired_estimand_delta(
        iid_scores,
        split_manifest=manifest,
        leakage_release=leakage,
        estimand=PairedEstimand.IID_ONLY,
    ) == pytest.approx(0.10)
    randomization = paired_group_randomization_test(
        iid_scores,
        split_manifest=manifest,
        leakage_release=leakage,
        seed=23,
        estimand=PairedEstimand.IID_ONLY,
    )
    assert randomization.exact is True
    assert randomization.replicates == 2**10
    assert randomization.p_value == pytest.approx(1 / 2**10)

    ood_case_ids = {
        item.case_id
        for item in manifest.cases
        if item.split is BenchmarkSplit.LOCKED_OOD
    }
    ood_scores = tuple(
        item for item in _paired_scores() if item.case_id in ood_case_ids
    )
    interval = paired_group_bootstrap_interval(
        ood_scores,
        split_manifest=manifest,
        leakage_release=leakage,
        replicates=200,
        seed=29,
        estimand=PairedEstimand.OOD_ONLY,
    )
    assert interval.observed_delta == pytest.approx(0.10)
    assert interval.lower == pytest.approx(0.10)
    assert interval.upper == pytest.approx(0.10)


def test_paired_inference_rejects_complete_case_omission_or_forgery() -> None:
    manifest, leakage = _locked_design()
    incomplete = _paired_scores()[:-1]
    with pytest.raises(ValueError, match="exactly cover every frozen locked case"):
        paired_group_bootstrap_interval(
            incomplete,
            split_manifest=manifest,
            leakage_release=leakage,
            replicates=10,
            seed=31,
        )

    forged = (
        *_paired_scores()[:-1],
        PairedCaseScore(case_id="forged-case", baseline=0.0, treatment=1.0),
    )
    with pytest.raises(ValueError, match="exactly cover every frozen locked case"):
        paired_group_randomization_test(
            forged,
            split_manifest=manifest,
            leakage_release=leakage,
            seed=31,
        )


def test_holm_adjustment_is_monotone_and_key_stable() -> None:
    adjusted = holm_adjust({"ood": 0.03, "iid": 0.01, "no-e1": 0.04})
    assert tuple(adjusted) == ("iid", "no-e1", "ood")
    assert adjusted["iid"] == pytest.approx(0.03)
    assert adjusted["ood"] >= adjusted["iid"]
    assert adjusted["no-e1"] >= adjusted["ood"]
