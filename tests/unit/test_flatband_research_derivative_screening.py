from __future__ import annotations

from typing import Any, TypeVar

import pytest

from material_agent.inspiration.models import (
    StrictModel,
    canonical_sha256,
    deterministic_id,
)
from material_agent.research.flatband_contracts import (
    Dimensionality,
    FlatBandBenchmarkCaseV1,
    MechanismFamily,
    SourceRecordRefV1,
    TargetBandClass,
)
from material_agent.research.flatband_derivative_screening import (
    DerivativeClass,
    DerivativeScreeningAdjudicationV3,
    DerivativeScreeningDecisionBasis,
    DerivativeScreeningFinalJudgmentV3,
    DerivativeScreeningReleaseV3,
    assert_calibration_derivative_screening_all_not_derivative_v3,
    assert_derivative_screening_release_exact_replay_v3,
    build_derivative_screening_adjudication_v3,
    build_derivative_screening_assignment_v3,
    build_derivative_screening_policy_v3,
    build_derivative_screening_raw_review_v3,
    build_derivative_screening_release_v3,
    build_derivative_screening_reviewer_roster_v3,
    build_derivative_source_evidence_ref_v3,
)
from material_agent.research.flatband_leakage import (
    MechanismLineageCuratorDeclarationV3,
)

ModelT = TypeVar("ModelT", bound=StrictModel)
CRITERIA = (
    "check-composition-ratio",
    "check-intercalant-sites",
    "check-ordered-defect-pattern",
    "check-vacancy-parent",
)


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


def _source(case_index: int, source_index: int) -> SourceRecordRefV1:
    record_id = f"case-{case_index}-source-{source_index}"
    return SourceRecordRefV1(
        source_id="cod",
        source_record_id=record_id,
        canonical_url=f"https://example.org/{record_id}",
        source_version="fixture-v1",
        license_expression="CC0-1.0",
        accessed_at="2026-08-10T00:00:00+00:00",
        raw_sha256=canonical_sha256(("raw-source", case_index, source_index)),
        public_redistribution_allowed=True,
    )


def _case(index: int) -> FlatBandBenchmarkCaseV1:
    values: dict[str, Any] = {
        "parent_label": f"calibration-parent-{index}",
        "formula": "BN",
        "structure_sha256": canonical_sha256(("structure", index)),
        "source_records": (_source(index, 0), _source(index, 1)),
        "target_class": TargetBandClass.FB100,
        "dimensionality": Dimensionality.TWO_D,
        "frozen_request": "Screen this calibration material without inventing evidence.",
        "frozen_requirement_sha256": canonical_sha256(("requirement", index)),
        "hard_constraints": ("ordered occupancy",),
        "forbidden_transformations": ("invent-sites",),
        "primary_mechanism_stratum": MechanismFamily.LATTICE_INTERFERENCE,
        "leakage_group_ids": (f"leakage-group-{index}",),
        "public_release_allowed": False,
    }
    return _identified(
        FlatBandBenchmarkCaseV1,
        id_field="case_id",
        sha_field="case_sha256",
        prefix="flatband-case",
        values=values,
    )


def _person(index: int) -> MechanismLineageCuratorDeclarationV3:
    return MechanismLineageCuratorDeclarationV3(
        curator_id=f"human-{index}",
        opaque_natural_person_ref=f"opaque-natural-person-{index}",
        natural_person_commitment_sha256=canonical_sha256(("person", index)),
        identity_evidence_uri=f"artifact://private/identity/human-{index}",
        identity_evidence_sha256=canonical_sha256(("identity", index)),
        institutional_unit=f"independent-unit-{index}",
        conflict_declaration="No conflict declared for this private screening role.",
    )


def _fixture() -> dict[str, Any]:
    policy = build_derivative_screening_policy_v3(
        review_criteria=reversed(CRITERIA),
        sealed_at="2026-08-10T00:00:00+00:00",
    )
    reviewers = (_person(1), _person(2))
    roster = build_derivative_screening_reviewer_roster_v3(
        policy=policy,
        reviewers=reversed(reviewers),
        adjudicator=_person(3),
        independence_review="Three private natural-person bindings were checked.",
        sealed_at="2026-08-10T00:00:01+00:00",
    )
    cases = (_case(0), _case(1))
    evidence = tuple(
        build_derivative_source_evidence_ref_v3(
            case=case,
            source_id=case.source_records[0].source_id,
            source_record_id=case.source_records[0].source_record_id,
        )
        for case in cases
    )
    assignments = tuple(
        build_derivative_screening_assignment_v3(
            policy=policy,
            roster=roster,
            case=case,
            evidence_refs=(ref,),
            assigned_at="2026-08-10T00:00:02+00:00",
        )
        for case, ref in zip(cases, evidence)
    )
    reviews = []
    requested_classes = (
        (DerivativeClass.NOT, DerivativeClass.NOT),
        (DerivativeClass.NOT, DerivativeClass.VACANCY),
    )
    review_times = (
        ("2026-08-10T00:00:03+00:00", "2026-08-10T00:00:04+00:00"),
        ("2026-08-10T00:00:03.100000+00:00", "2026-08-10T00:00:04.100000+00:00"),
    )
    for assignment, classes, times in zip(
        assignments, requested_classes, review_times
    ):
        for reviewer, derivative_class, reviewed_at in zip(
            roster.reviewers, classes, times
        ):
            reviews.append(
                build_derivative_screening_raw_review_v3(
                    policy=policy,
                    roster=roster,
                    assignment=assignment,
                    reviewer_id=reviewer.curator_id,
                    derivative_class=derivative_class,
                    criterion_findings=CRITERIA,
                    rationale="The reviewer classified only the supplied source record.",
                    reviewed_at=reviewed_at,
                )
            )
    disagreement_reviews = tuple(
        item for item in reviews if item.case_id == assignments[1].case_id
    )
    adjudication = build_derivative_screening_adjudication_v3(
        policy=policy,
        roster=roster,
        assignment=assignments[1],
        reviews=disagreement_reviews,
        final_derivative_class=DerivativeClass.NOT,
        rationale="The distinct adjudicator resolved the class disagreement.",
        adjudicated_at="2026-08-10T00:00:05+00:00",
    )
    release = build_derivative_screening_release_v3(
        policy=policy,
        roster=roster,
        cases=reversed(cases),
        assignments=reversed(assignments),
        raw_reviews=reversed(reviews),
        adjudications=(adjudication,),
        assembled_at="2026-08-10T00:00:06+00:00",
    )
    return {
        "policy": policy,
        "roster": roster,
        "cases": cases,
        "evidence": evidence,
        "assignments": assignments,
        "reviews": tuple(reviews),
        "adjudication": adjudication,
        "release": release,
    }


def test_release_exact_replay_human_agreement_and_adjudication() -> None:
    fixture = _fixture()
    release: DerivativeScreeningReleaseV3 = fixture["release"]

    assert_derivative_screening_release_exact_replay_v3(release)
    assert {item.decision_basis for item in release.final_judgments} == {
        DerivativeScreeningDecisionBasis.REVIEWER_AGREEMENT,
        DerivativeScreeningDecisionBasis.DISTINCT_ADJUDICATION,
    }
    assert all(
        item.final_derivative_class is DerivativeClass.NOT
        for item in release.final_judgments
    )
    assert all(item.scientific_conclusion is False for item in release.raw_reviews)
    assert all(
        item.scientific_conclusion is False for item in release.final_judgments
    )

    rebuilt = build_derivative_screening_release_v3(
        policy=release.policy,
        roster=release.roster,
        cases=reversed(release.cases),
        assignments=reversed(release.assignments),
        raw_reviews=reversed(release.raw_reviews),
        adjudications=reversed(release.adjudications),
        assembled_at=release.assembled_at,
    )
    assert rebuilt == release

    safe_case = next(
        case
        for case in release.cases
        if {
            item.derivative_class
            for item in release.raw_reviews
            if item.case_id == case.case_id
        }
        == {DerivativeClass.NOT}
    )
    safe_release = build_derivative_screening_release_v3(
        policy=release.policy,
        roster=release.roster,
        cases=(safe_case,),
        assignments=tuple(
            item for item in release.assignments if item.case_id == safe_case.case_id
        ),
        raw_reviews=tuple(
            item for item in release.raw_reviews if item.case_id == safe_case.case_id
        ),
        assembled_at=release.assembled_at,
    )
    assert_calibration_derivative_screening_all_not_derivative_v3(safe_release)


def test_calibration_gate_rejects_adjudication_washing_raw_derivative_risk() -> None:
    release: DerivativeScreeningReleaseV3 = _fixture()["release"]

    assert_derivative_screening_release_exact_replay_v3(release)
    assert all(
        item.final_derivative_class is DerivativeClass.NOT
        for item in release.final_judgments
    )
    assert any(
        item.derivative_class is DerivativeClass.VACANCY
        for item in release.raw_reviews
    )
    with pytest.raises(ValueError, match="derivative risk in raw review"):
        assert_calibration_derivative_screening_all_not_derivative_v3(release)


def test_missing_third_foreign_evidence_and_natural_person_alias_fail_closed() -> None:
    fixture = _fixture()
    policy = fixture["policy"]
    roster = fixture["roster"]
    cases = fixture["cases"]
    assignments = fixture["assignments"]
    reviews = fixture["reviews"]
    adjudication = fixture["adjudication"]

    with pytest.raises(ValueError, match="exactly cover case universe"):
        build_derivative_screening_release_v3(
            policy=policy,
            roster=roster,
            cases=cases,
            assignments=(assignments[0],),
            raw_reviews=tuple(
                item for item in reviews if item.case_id == assignments[0].case_id
            ),
            assembled_at="2026-08-10T00:00:06+00:00",
        )

    third = build_derivative_screening_raw_review_v3(
        policy=policy,
        roster=roster,
        assignment=assignments[0],
        reviewer_id=roster.reviewers[0].curator_id,
        derivative_class=DerivativeClass.NOT,
        criterion_findings=CRITERIA,
        rationale="A prohibited third row from an already assigned reviewer.",
        reviewed_at="2026-08-10T00:00:04.500000+00:00",
    )
    with pytest.raises(ValueError, match="exactly two assigned raw reviewers"):
        build_derivative_screening_release_v3(
            policy=policy,
            roster=roster,
            cases=cases,
            assignments=assignments,
            raw_reviews=(*reviews, third),
            adjudications=(adjudication,),
            assembled_at="2026-08-10T00:00:06+00:00",
        )

    with pytest.raises(ValueError, match="nonempty subset of case sources"):
        build_derivative_screening_assignment_v3(
            policy=policy,
            roster=roster,
            case=cases[0],
            evidence_refs=(fixture["evidence"][1],),
            assigned_at="2026-08-10T00:00:02+00:00",
        )

    aliased_adjudicator = roster.reviewers[0].model_copy(
        update={"curator_id": "human-alias"}
    )
    with pytest.raises(ValueError, match="natural-person bindings must be injective"):
        build_derivative_screening_reviewer_roster_v3(
            policy=policy,
            reviewers=roster.reviewers,
            adjudicator=aliased_adjudicator,
            independence_review="This false alias must be rejected.",
            sealed_at="2026-08-10T00:00:01+00:00",
        )


def test_missing_and_orphan_adjudication_fail_closed() -> None:
    fixture = _fixture()
    with pytest.raises(ValueError, match="disagreement requires adjudication"):
        build_derivative_screening_release_v3(
            policy=fixture["policy"],
            roster=fixture["roster"],
            cases=fixture["cases"],
            assignments=fixture["assignments"],
            raw_reviews=fixture["reviews"],
            assembled_at="2026-08-10T00:00:06+00:00",
        )

    adjudication: DerivativeScreeningAdjudicationV3 = fixture["adjudication"]
    orphan_values = adjudication.model_dump(
        mode="python", exclude={"adjudication_id", "adjudication_sha256"}
    )
    orphan_values.update(case_id="foreign-case", case_sha256="f" * 64)
    orphan = _identified(
        DerivativeScreeningAdjudicationV3,
        id_field="adjudication_id",
        sha_field="adjudication_sha256",
        prefix="derivative-adjudication-v3",
        values=orphan_values,
    )
    with pytest.raises(ValueError, match="orphaned from case universe"):
        build_derivative_screening_release_v3(
            policy=fixture["policy"],
            roster=fixture["roster"],
            cases=fixture["cases"],
            assignments=fixture["assignments"],
            raw_reviews=fixture["reviews"],
            adjudications=(adjudication, orphan),
            assembled_at="2026-08-10T00:00:06+00:00",
        )


def test_exact_replay_rejects_fully_readdressed_final_judgment() -> None:
    release: DerivativeScreeningReleaseV3 = _fixture()["release"]
    original = next(
        item
        for item in release.final_judgments
        if item.decision_basis is DerivativeScreeningDecisionBasis.REVIEWER_AGREEMENT
    )
    judgment_values = original.model_dump(
        mode="python", exclude={"judgment_id", "judgment_sha256"}
    )
    judgment_values["final_derivative_class"] = DerivativeClass.VACANCY
    forged_judgment = _identified(
        DerivativeScreeningFinalJudgmentV3,
        id_field="judgment_id",
        sha_field="judgment_sha256",
        prefix="derivative-final-v3",
        values=judgment_values,
    )
    forged_judgments = tuple(
        sorted(
            (
                forged_judgment if item.judgment_id == original.judgment_id else item
                for item in release.final_judgments
            ),
            key=lambda item: (item.case_id, item.judgment_id),
        )
    )
    release_values = release.model_dump(
        mode="python", exclude={"release_id", "release_sha256"}
    )
    release_values["final_judgments"] = forged_judgments
    forged_release = _identified(
        DerivativeScreeningReleaseV3,
        id_field="release_id",
        sha_field="release_sha256",
        prefix="derivative-release-v3",
        values=release_values,
    )

    assert forged_release.release_id != release.release_id
    with pytest.raises(ValueError, match="does not replay exactly"):
        assert_derivative_screening_release_exact_replay_v3(forged_release)


def test_valid_derivative_release_replays_but_calibration_gate_rejects() -> None:
    fixture = _fixture()
    policy = fixture["policy"]
    roster = fixture["roster"]
    case = fixture["cases"][0]
    assignment = fixture["assignments"][0]
    reviews = tuple(
        build_derivative_screening_raw_review_v3(
            policy=policy,
            roster=roster,
            assignment=assignment,
            reviewer_id=reviewer.curator_id,
            derivative_class=DerivativeClass.ORDERED_DEFECT,
            criterion_findings=CRITERIA,
            rationale="The human reviewer classified an ordered defect derivative.",
            reviewed_at=reviewed_at,
        )
        for reviewer, reviewed_at in zip(
            roster.reviewers,
            (
                "2026-08-10T00:00:03+00:00",
                "2026-08-10T00:00:04+00:00",
            ),
        )
    )
    release = build_derivative_screening_release_v3(
        policy=policy,
        roster=roster,
        cases=(case,),
        assignments=(assignment,),
        raw_reviews=reviews,
        assembled_at="2026-08-10T00:00:06+00:00",
    )

    assert_derivative_screening_release_exact_replay_v3(release)
    with pytest.raises(ValueError, match="contains derivative risk"):
        assert_calibration_derivative_screening_all_not_derivative_v3(release)


def test_strict_chronology_and_adjudication_iff_disagreement() -> None:
    fixture = _fixture()
    policy = fixture["policy"]
    roster = fixture["roster"]
    assignment = fixture["assignments"][0]
    agreed_reviews = tuple(
        item for item in fixture["reviews"] if item.case_id == assignment.case_id
    )

    with pytest.raises(ValueError, match="sealed after policy"):
        build_derivative_screening_reviewer_roster_v3(
            policy=policy,
            reviewers=roster.reviewers,
            adjudicator=roster.adjudicator,
            independence_review="Chronology attack.",
            sealed_at=policy.sealed_at,
        )
    with pytest.raises(ValueError, match="must follow assignment"):
        build_derivative_screening_raw_review_v3(
            policy=policy,
            roster=roster,
            assignment=assignment,
            reviewer_id=roster.reviewers[0].curator_id,
            derivative_class=DerivativeClass.NOT,
            criterion_findings=CRITERIA,
            rationale="Chronology attack.",
            reviewed_at=assignment.assigned_at,
        )
    with pytest.raises(ValueError, match="agreed derivative reviews cannot be adjudicated"):
        build_derivative_screening_adjudication_v3(
            policy=policy,
            roster=roster,
            assignment=assignment,
            reviews=agreed_reviews,
            final_derivative_class=DerivativeClass.NOT,
            rationale="Unnecessary adjudication attack.",
            adjudicated_at="2026-08-10T00:00:05+00:00",
        )
    with pytest.raises(ValueError, match="assembled after all judgments"):
        build_derivative_screening_release_v3(
            policy=policy,
            roster=roster,
            cases=fixture["cases"],
            assignments=fixture["assignments"],
            raw_reviews=fixture["reviews"],
            adjudications=(fixture["adjudication"],),
            assembled_at="2026-08-10T00:00:05+00:00",
        )
