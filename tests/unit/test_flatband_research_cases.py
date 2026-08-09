from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
from typing import Any, Literal, TypeVar

import pytest
from pydantic import ValidationError

import material_agent.research.flatband_cases as flatband_cases_module
import material_agent.research.flatband_source_policy as source_policy_module
from material_agent.inspiration.models import (
    StrictModel,
    canonical_sha256,
    deterministic_id,
)
from material_agent.research.flatband_cases import (
    CandidateEligibilityAssignmentReleaseV3,
    CandidateEligibilityAssignmentV3,
    CandidatePoolReleaseV3,
    CaseSourcePolicyAttestationV2,
    CaseEligibilityReasonCode,
    CaseEligibilityStatus,
    EligibilityDecisionV1,
    EligibilityAdjudicationV3,
    EligibilityRawAuditV3,
    FrozenCaseCandidateV1,
    FrozenCaseReleaseV1,
    FrozenCaseReleaseV2,
    FrozenCaseReleaseV3,
    PilotPreBudgetClosureReleaseV3,
    PreRunEligibilityReleaseV1,
    PreRunEligibilityReleaseV2,
    PreRunEligibilityReleaseV3,
    SourceUseRole,
    _SOURCE_CATALOG_POLICY_V1,
    assert_case_source_policy_v2,
    assert_pre_run_eligibility_ready,
    assert_pre_run_eligibility_ready_v2,
    assert_pre_run_eligibility_ready_v3,
    assert_pre_run_eligibility_sealed_before,
    build_case_source_policy_attestation_v2,
    build_calibration_leakage_context_v3,
    build_candidate_eligibility_assignment_release_v3,
    build_candidate_pool_leakage_context_v3,
    build_candidate_eligibility_assignment_v3,
    build_candidate_pool_release_v3,
    build_eligibility_decision,
    build_eligibility_adjudication_v3,
    build_eligibility_raw_audit_v3,
    build_frozen_case_candidate,
    build_frozen_case_release,
    build_frozen_case_release_v2,
    build_frozen_case_release_v3,
    build_pilot_pre_budget_closure_release_v3,
    build_pre_run_eligibility_release,
    build_pre_run_eligibility_release_v2,
    build_pre_run_eligibility_release_v3,
    frozen_case_slot_id,
)
from material_agent.research.flatband_contracts import (
    BenchmarkSplit,
    BenchmarkSplitManifestV1,
    BenchmarkSplitManifestV2,
    BandwidthScope,
    Dimensionality,
    ExpertRole,
    EvidenceClaimType,
    FlatBandEvidenceV1,
    FlatBandBenchmarkCaseV1,
    MechanismFamily,
    MagneticOrder,
    ObservedBandClass,
    SourceRecordRefV1,
    SplitCaseRefV1,
    SplitCaseRefV2,
    SplitManifestKind,
    SocState,
    TargetBandClass,
    SOURCE_CATALOG_V1_SHA256,
)
from material_agent.research.flatband_experts import (
    CalibrationCompletionV2,
    CalibrationSetManifestV2,
    CalibrationCompletionV1,
    CaseConflictAssessmentV1,
    CaseExpertAssignmentV1,
    ConflictReasonCode,
    ConflictStatus,
    ExpertProfileV1,
    ExpertStudyRegistryV1,
    ExpertStudyRegistryV2,
    PrivateNaturalPersonBindingV2,
    PublicExpertIdentityV2,
    PublicExpertIdentityReleaseV2,
    build_calibration_completion_v2,
    build_calibration_set_manifest_v2,
    build_expert_study_registry_v2,
    build_private_expert_identity_attestation_v2,
    build_public_expert_identity_release_v2,
)
from material_agent.research.flatband_leakage import (
    LeakageAxis,
    LeakageComponentReleaseV3,
    LeakageRoundClosureContextV3,
    LeakageComponentReleaseV1,
    LeakageMembershipV1,
    MechanismLineageAssignmentV3,
    MechanismLineageAssignmentCurationReleaseV3,
    MechanismLineageAssignmentDecisionV3,
    MechanismLineageCurationReleaseV3,
    MechanismLineageCuratorDeclarationV3,
    MechanismLineageReviewDecisionV3,
    MechanismLineageEvidenceRefV3,
    StructureGroupingAlgorithmV2,
    StructureGroupingAssignmentV2,
    StructureGroupingRunV2,
    build_leakage_component_release_v3,
    build_leakage_component_release,
    build_mechanism_lineage_assignment_candidate_universe_v3,
    build_mechanism_lineage_assignment_curation_policy_v3,
    build_mechanism_lineage_assignment_curation_release_v3,
    build_mechanism_lineage_assignment_proposal_v3,
    build_mechanism_lineage_assignment_review_manifest_v3,
    build_mechanism_lineage_assignment_review_v3,
    build_mechanism_lineage_assignment_reviewer_roster_v3,
    build_mechanism_lineage_assignment_v3,
    build_mechanism_lineage_curation_policy_v3,
    build_mechanism_lineage_curation_release_v3,
    build_mechanism_lineage_curator_roster_v3,
    build_mechanism_lineage_definition_v3,
    build_mechanism_lineage_definition_review_v3,
    build_mechanism_lineage_evidence_review_manifest_v3,
    build_mechanism_lineage_registry_v3,
    derive_leakage_group_ids_v3,
    derive_formal_mechanism_lineage_assignments_v3,
    structure_grouping_case_universe_sha256_v2,
)


ModelT = TypeVar("ModelT", bound=StrictModel)
SHA_A = "a" * 64
SHA_B = "b" * 64
SHA_C = "c" * 64
SHA_D = "d" * 64
GUIDE_SHA = canonical_sha256("annotation-guide-v1")
CALIBRATION_SHA = canonical_sha256("disjoint-calibration-set-v1")
CASE_POLICY_SHA = canonical_sha256("case-freeze-policy-v1")
ELIGIBILITY_POLICY_SHA = canonical_sha256("pre-run-eligibility-policy-v1")
V3_LINEAGE_REVIEW_CRITERIA = tuple(
    sorted(
        (
            "Evidence supports the stated shared invariant",
            "Lineage is finer than the broad sampling taxonomy",
            "Transfer route is scientifically reusable across cases",
        )
    )
)
V3_ASSIGNMENT_REVIEW_CRITERIA = tuple(
    sorted(
        (
            "All candidate source records support the proposed lineage assignment",
            "Fine-grained lineage matches the case mechanism evidence",
            "Proposed lineage is not merely the broad sampling family",
        )
    )
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


def _reidentified(
    model: ModelT,
    *,
    id_field: str,
    sha_field: str,
    prefix: str,
    **changes: Any,
) -> ModelT:
    values = {
        field_name: getattr(model, field_name)
        for field_name in type(model).model_fields
        if field_name not in {id_field, sha_field}
    }
    values.update(changes)
    return _identified(
        type(model),
        id_field=id_field,
        sha_field=sha_field,
        prefix=prefix,
        values=values,
    )


def _reidentified_case(
    case: FlatBandBenchmarkCaseV1, **updates: object
) -> FlatBandBenchmarkCaseV1:
    values = {
        field_name: getattr(case, field_name)
        for field_name in type(case).model_fields
        if field_name not in {"case_id", "case_sha256"}
    }
    values.update(updates)
    return _identified(
        FlatBandBenchmarkCaseV1,
        id_field="case_id",
        sha_field="case_sha256",
        prefix="flatband-case",
        values=values,
    )


def _mechanism(index: int) -> MechanismFamily:
    return (
        MechanismFamily.LATTICE_INTERFERENCE,
        MechanismFamily.LINE_GRAPH,
        MechanismFamily.ORBITAL_FRUSTRATION_HYBRIDIZATION,
        MechanismFamily.SYMMETRY_INDUCED,
        MechanismFamily.CONFINEMENT,
    )[index // 6]


def _leakage_groups(index: int) -> dict[LeakageAxis, str]:
    component = index // 3
    return {
        LeakageAxis.ARTICLE_OR_SOURCE_FAMILY: f"article-{component:02d}",
        LeakageAxis.COMPOSITION_FAMILY: f"composition-{component:02d}",
        LeakageAxis.MECHANISM_FAMILY: f"mechanism-route-{component:02d}",
        LeakageAxis.STRUCTURE_FINGERPRINT: f"fingerprint-{component:02d}",
        LeakageAxis.STRUCTURE_PROTOTYPE: f"prototype-{component:02d}",
    }


def _case(index: int, *, variant: str = "primary") -> FlatBandBenchmarkCaseV1:
    request = (
        "Propose a mechanism-bounded near-Fermi flat-band route while preserving "
        f"the frozen parent and all hard constraints for case {index}."
    )
    source = SourceRecordRefV1(
        source_id="cod",
        source_record_id=f"cod-{index:04d}-{variant}",
        canonical_url=f"https://example.org/cod/{index:04d}/{variant}",
        source_version="snapshot-2026-08-09",
        license_expression="CC0-1.0",
        accessed_at="2026-08-09T07:00:00+08:00",
        raw_sha256=canonical_sha256(("source", index, variant)),
        public_redistribution_allowed=True,
    )
    values = {
        "parent_label": f"parent-{index:03d}-{variant}",
        "formula": f"X{index + 1}Y2",
        "structure_sha256": canonical_sha256(("structure", index, variant)),
        "source_records": (source,),
        "target_class": (
            TargetBandClass.FB100 if index < 15 else TargetBandClass.NB300
        ),
        "dimensionality": (
            Dimensionality.TWO_D if index % 2 == 0 else Dimensionality.THREE_D
        ),
        "frozen_request": request,
        "frozen_requirement_sha256": canonical_sha256(
            {"frozen_request": request}
        ),
        "hard_constraints": (
            "candidate must preserve the frozen parent dimensionality",
            "tracked band must remain within one electron volt of the Fermi level",
        ),
        "soft_preferences": ("prefer an experimentally controllable parameter",),
        "forbidden_transformations": (
            "change-dimensionality",
            "replace-parent-structure",
        ),
        "seed_evidence": (),
        "primary_mechanism_stratum": _mechanism(index),
        "leakage_group_ids": tuple(sorted(_leakage_groups(index).values())),
        "public_release_allowed": True,
    }
    return _identified(
        FlatBandBenchmarkCaseV1,
        id_field="case_id",
        sha_field="case_sha256",
        prefix="flatband-case",
        values=values,
    )


def _manifest(cases: tuple[FlatBandBenchmarkCaseV1, ...]) -> BenchmarkSplitManifestV1:
    refs = tuple(
        sorted(
            (
                SplitCaseRefV1(
                    case_id=case.case_id,
                    case_sha256=case.case_sha256,
                    split=BenchmarkSplit.PILOT_R1,
                    target_class=case.target_class,
                    dimensionality=case.dimensionality,
                    primary_mechanism_stratum=case.primary_mechanism_stratum,
                    leakage_group_ids=case.leakage_group_ids,
                )
                for case in cases
            ),
            key=lambda item: item.case_id,
        )
    )
    return _identified(
        BenchmarkSplitManifestV1,
        id_field="manifest_id",
        sha_field="manifest_sha256",
        prefix="split-manifest",
        values={
            "manifest_kind": SplitManifestKind.PILOT_R1,
            "split_seed": 20260809,
            "cases": refs,
            "ood_holdout_families": (),
        },
    )


def _leakage_release(
    manifest: BenchmarkSplitManifestV1,
    cases: tuple[FlatBandBenchmarkCaseV1, ...],
) -> LeakageComponentReleaseV1:
    case_by_id = {item.case_id: item for item in cases}
    memberships = tuple(
        LeakageMembershipV1(
            case_id=ref.case_id,
            case_sha256=ref.case_sha256,
            axis=axis,
            group_id=group_id,
            provenance_sha256=canonical_sha256(
                (ref.case_id, axis.value, group_id, "grouping-provenance-v1")
            ),
        )
        for ref in manifest.cases
        for axis, group_id in sorted(
            _leakage_groups(
                int(case_by_id[ref.case_id].formula[1:-2]) - 1
            ).items(),
            key=lambda item: item[0].value,
        )
    )
    return build_leakage_component_release(
        split_manifest=manifest,
        memberships=memberships,
        construction_policy_sha256=canonical_sha256(
            "five-axis-leakage-construction-v1"
        ),
        created_at="2026-08-09T10:15:00+08:00",
    )


def _profiles() -> tuple[ExpertProfileV1, ...]:
    return (
        ExpertProfileV1(
            expert_id="adjudicator-a",
            role=ExpertRole.ADJUDICATOR,
            domain_expertise=("electronic structure", "flat-band physics"),
            qualification_summary="Independent adjudicator qualified in flat-band physics.",
        ),
        ExpertProfileV1(
            expert_id="reviewer-a",
            role=ExpertRole.REVIEWER,
            domain_expertise=("band structures", "materials physics"),
            qualification_summary="Reviewer qualified in first-principles band analysis.",
        ),
        ExpertProfileV1(
            expert_id="reviewer-b",
            role=ExpertRole.REVIEWER,
            domain_expertise=("correlated materials", "flat-band physics"),
            qualification_summary="Reviewer qualified in narrow-band mechanisms.",
        ),
    )


def _completion(profile: ExpertProfileV1) -> CalibrationCompletionV1:
    return _identified(
        CalibrationCompletionV1,
        id_field="completion_id",
        sha_field="completion_sha256",
        prefix="calibration-completion",
        values={
            "expert_id": profile.expert_id,
            "role": profile.role,
            "annotation_guide_sha256": GUIDE_SHA,
            "calibration_set_sha256": CALIBRATION_SHA,
            "raw_answers_sha256": canonical_sha256(
                (profile.expert_id, "raw-calibration-answers")
            ),
            "calibration_result_sha256": canonical_sha256(
                (profile.expert_id, "calibration-result")
            ),
            "completed_at": "2026-08-09T09:00:00+08:00",
        },
    )


def _registry(manifest: BenchmarkSplitManifestV1) -> ExpertStudyRegistryV1:
    profiles = _profiles()
    assignments = tuple(
        CaseExpertAssignmentV1(
            case_id=case.case_id,
            case_sha256=case.case_sha256,
            reviewer_ids=("reviewer-a", "reviewer-b"),
            adjudicator_id="adjudicator-a",
        )
        for case in manifest.cases
    )
    conflicts = tuple(
        CaseConflictAssessmentV1(
            case_id=case.case_id,
            case_sha256=case.case_sha256,
            expert_id=profile.expert_id,
            status=ConflictStatus.CLEAR,
            reason_code=ConflictReasonCode.NO_CONFLICT,
            disclosure_sha256=canonical_sha256(
                (case.case_id, profile.expert_id, "conflict-disclosure")
            ),
            assessed_at="2026-08-09T09:20:00+08:00",
        )
        for case in manifest.cases
        for profile in profiles
    )
    return _identified(
        ExpertStudyRegistryV1,
        id_field="registry_id",
        sha_field="registry_sha256",
        prefix="expert-study-registry",
        values={
            "split_manifest_id": manifest.manifest_id,
            "split_manifest_sha256": manifest.manifest_sha256,
            "annotation_guide_sha256": GUIDE_SHA,
            "calibration_set_sha256": CALIBRATION_SHA,
            "profiles": profiles,
            "calibration_completions": tuple(
                _completion(profile) for profile in profiles
            ),
            "conflict_assessments": conflicts,
            "assignments": assignments,
            "registered_at": "2026-08-09T10:20:00+08:00",
        },
    )


@dataclass(frozen=True)
class _Study:
    primary_cases: tuple[FlatBandBenchmarkCaseV1, ...]
    active_cases: tuple[FlatBandBenchmarkCaseV1, ...]
    manifest: BenchmarkSplitManifestV1
    leakage: LeakageComponentReleaseV1
    registry: ExpertStudyRegistryV1
    candidates: tuple[FrozenCaseCandidateV1, ...]
    frozen: FrozenCaseReleaseV1
    decisions: tuple[EligibilityDecisionV1, ...]
    eligibility: PreRunEligibilityReleaseV1


def _study(
    *,
    activate_replacement: bool = False,
    exclude_first_without_replacement: bool = False,
    candidate_pool_sealed_at: str = "2026-08-09T09:30:00+08:00",
) -> _Study:
    primary = tuple(_case(index) for index in range(30))
    candidates = [
        build_frozen_case_candidate(
            case=case,
            slot_id=frozen_case_slot_id(case),
            priority=0,
            declared_at="2026-08-09T08:00:00+08:00",
        )
        for case in primary
    ]
    active = list(primary)
    excluded_ids: set[str] = set()
    if activate_replacement:
        replacement = _case(0, variant="replacement")
        candidates.append(
            build_frozen_case_candidate(
                case=replacement,
                slot_id=frozen_case_slot_id(primary[0]),
                priority=1,
                declared_at="2026-08-09T08:30:00+08:00",
            )
        )
        active[0] = replacement
        excluded_ids.add(primary[0].case_id)
    elif exclude_first_without_replacement:
        excluded_ids.add(primary[0].case_id)

    active_cases = tuple(active)
    manifest = _manifest(active_cases)
    leakage = _leakage_release(manifest, active_cases)
    registry = _registry(manifest)
    frozen = build_frozen_case_release(
        split_manifest=manifest,
        leakage_release=leakage,
        expert_registry=registry,
        candidates=tuple(candidates),
        case_freeze_policy_sha256=CASE_POLICY_SHA,
        eligibility_policy_sha256=ELIGIBILITY_POLICY_SHA,
        candidate_pool_sealed_at=candidate_pool_sealed_at,
        frozen_at="2026-08-09T10:30:00+08:00",
    )
    decisions = tuple(
        build_eligibility_decision(
            candidate=candidate,
            status=(
                CaseEligibilityStatus.EXCLUDED
                if candidate.case.case_id in excluded_ids
                else CaseEligibilityStatus.INCLUDED
            ),
            reason_codes=(
                (CaseEligibilityReasonCode.PARENT_STRUCTURE_UNVERIFIED,)
                if candidate.case.case_id in excluded_ids
                else (
                    CaseEligibilityReasonCode.MEETS_ALL_PREREGISTERED_CRITERIA,
                )
            ),
            assessor_id="eligibility-curator-a",
            assessment_protocol_sha256=ELIGIBILITY_POLICY_SHA,
            rationale_sha256=canonical_sha256(
                (candidate.case.case_id, "eligibility-rationale")
            ),
            assessed_at="2026-08-09T10:00:00+08:00",
        )
        for candidate in frozen.candidates
    )
    eligibility = build_pre_run_eligibility_release(
        frozen_case_release=frozen,
        decisions=decisions,
        sealed_at="2026-08-09T11:00:00+08:00",
    )
    return _Study(
        primary_cases=primary,
        active_cases=active_cases,
        manifest=manifest,
        leakage=leakage,
        registry=registry,
        candidates=frozen.candidates,
        frozen=frozen,
        decisions=decisions,
        eligibility=eligibility,
    )


@pytest.fixture(scope="module")
def study() -> _Study:
    return _study()


def test_frozen_release_exactly_closes_pilot_cases_and_upstream_refs(
    study: _Study,
) -> None:
    assert len(study.frozen.active_case_ids) == 30
    assert len(study.frozen.candidates) == 30
    assert study.frozen.active_case_ids == tuple(
        item.case_id for item in study.manifest.cases
    )
    assert study.frozen.leakage_release_id == study.leakage.release_id
    assert study.frozen.expert_registry_id == study.registry.registry_id
    assert study.frozen.annotation_guide_sha256 == GUIDE_SHA
    assert study.frozen.calibration_set_sha256 == CALIBRATION_SHA
    assert study.eligibility.execution_authorized is True
    assert_pre_run_eligibility_ready(study.eligibility)
    assert_pre_run_eligibility_sealed_before(
        eligibility_release=study.eligibility,
        first_execution_artifact_at="2026-08-09T12:00:00+08:00",
    )


def test_predeclared_replacement_is_selected_only_after_primary_exclusion() -> None:
    replaced = _study(activate_replacement=True)
    activated = tuple(
        item for item in replaced.eligibility.active_selections if item.replacement_activated
    )
    assert len(activated) == 1
    assert activated[0].selected_priority == 1
    assert activated[0].selected_case_id == replaced.active_cases[0].case_id
    assert replaced.eligibility.execution_authorized is True


def test_frozen_release_rejects_missing_full_case() -> None:
    replaced = _study(activate_replacement=True)
    omitted_case_id = replaced.active_cases[1].case_id
    incomplete_pool = tuple(
        item for item in replaced.candidates if item.case.case_id != omitted_case_id
    )
    assert len(incomplete_pool) == 30
    with pytest.raises(
        (ValidationError, ValueError), match="absent from the frozen pool|every frozen slot"
    ):
        build_frozen_case_release(
            split_manifest=replaced.manifest,
            leakage_release=replaced.leakage,
            expert_registry=replaced.registry,
            candidates=incomplete_pool,
            case_freeze_policy_sha256=CASE_POLICY_SHA,
            eligibility_policy_sha256=ELIGIBILITY_POLICY_SHA,
            candidate_pool_sealed_at="2026-08-09T09:30:00+08:00",
            frozen_at="2026-08-09T10:30:00+08:00",
        )


@pytest.mark.parametrize(
    ("field", "forged"),
    (("case_id", "forged-case-alias"), ("case_sha256", SHA_D)),
)
def test_frozen_release_revalidates_case_id_and_sha_aliases(
    study: _Study, field: str, forged: str
) -> None:
    first = study.candidates[0]
    forged_case = first.case.model_copy(update={field: forged})
    forged_candidate = first.model_copy(update={"case": forged_case})
    with pytest.raises((ValidationError, ValueError), match="case (ID|SHA-256)"):
        build_frozen_case_release(
            split_manifest=study.manifest,
            leakage_release=study.leakage,
            expert_registry=study.registry,
            candidates=(forged_candidate, *study.candidates[1:]),
            case_freeze_policy_sha256=CASE_POLICY_SHA,
            eligibility_policy_sha256=ELIGIBILITY_POLICY_SHA,
            candidate_pool_sealed_at="2026-08-09T09:30:00+08:00",
            frozen_at="2026-08-09T10:30:00+08:00",
        )


def test_candidate_rejects_incomplete_constraints_and_forbidden_set() -> None:
    case = _case(0)
    without_constraints = _reidentified_case(case, hard_constraints=())
    without_forbidden = _reidentified_case(case, forbidden_transformations=())
    with pytest.raises(ValueError, match="hard constraints"):
        build_frozen_case_candidate(
            case=without_constraints,
            slot_id=frozen_case_slot_id(without_constraints),
            priority=0,
            declared_at="2026-08-09T08:00:00+08:00",
        )
    with pytest.raises(ValueError, match="forbidden transformations"):
        build_frozen_case_candidate(
            case=without_forbidden,
            slot_id=frozen_case_slot_id(without_forbidden),
            priority=0,
            declared_at="2026-08-09T08:00:00+08:00",
        )


def test_eligibility_release_rejects_missing_candidate_decision(study: _Study) -> None:
    with pytest.raises(
        (ValidationError, ValueError), match="exactly cover frozen candidates"
    ):
        build_pre_run_eligibility_release(
            frozen_case_release=study.frozen,
            decisions=study.decisions[:-1],
            sealed_at="2026-08-09T11:00:00+08:00",
        )


def test_candidate_pool_seal_must_precede_every_eligibility_assessment() -> None:
    with pytest.raises(
        (ValidationError, ValueError), match="must precede every assessment"
    ):
        _study(candidate_pool_sealed_at="2026-08-09T10:00:00+08:00")


def test_excluded_case_without_predeclared_replacement_is_not_executable() -> None:
    excluded = _study(exclude_first_without_replacement=True)
    assert excluded.eligibility.execution_authorized is False
    with pytest.raises(ValueError, match="does not authorize"):
        assert_pre_run_eligibility_ready(excluded.eligibility)


def test_post_hoc_eligibility_after_execution_is_rejected(study: _Study) -> None:
    with pytest.raises(ValueError, match="not sealed before execution"):
        assert_pre_run_eligibility_sealed_before(
            eligibility_release=study.eligibility,
            first_execution_artifact_at="2026-08-09T10:59:59+08:00",
        )


def test_system_specific_exclusion_cannot_enter_decision_schema(study: _Study) -> None:
    payload = study.decisions[0].model_dump(mode="python", round_trip=True)
    payload["system_id"] = "B0"
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        EligibilityDecisionV1.model_validate(payload)

    asymmetric = study.decisions[0].model_copy(
        update={"applies_to_all_registered_systems": False}
    )
    with pytest.raises(ValidationError):
        build_pre_run_eligibility_release(
            frozen_case_release=study.frozen,
            decisions=(asymmetric, *study.decisions[1:]),
            sealed_at="2026-08-09T11:00:00+08:00",
        )


def test_content_addressing_rejects_forged_release_sha(study: _Study) -> None:
    forged = study.eligibility.model_copy(update={"release_sha256": SHA_A})
    with pytest.raises((ValidationError, ValueError), match="release_sha256"):
        PreRunEligibilityReleaseV1.model_validate(
            forged.model_dump(mode="python", round_trip=True)
        )


def test_frozen_release_schema_rejects_single_system_case_exclusion() -> None:
    assert "system_id" not in EligibilityDecisionV1.model_fields
    assert "system_ids" not in EligibilityDecisionV1.model_fields
    assert "excluded_system_ids" not in FrozenCaseReleaseV1.model_fields
    assert "excluded_system_ids" not in PreRunEligibilityReleaseV1.model_fields


def _v2_structure_algorithm(axis: LeakageAxis) -> StructureGroupingAlgorithmV2:
    return _identified(
        StructureGroupingAlgorithmV2,
        id_field="algorithm_id",
        sha_field="algorithm_sha256",
        prefix="structure-group-algorithm",
        values={
            "axis": axis,
            "algorithm_name": f"frozen-{axis.value.casefold()}",
            "algorithm_version": "formal-case-v2-test",
            "implementation_sha256": canonical_sha256((axis.value, "impl")),
            "configuration_sha256": canonical_sha256((axis.value, "config")),
        },
    )


def _v2_grouping_artifacts_local(
    *,
    cases: tuple[FlatBandBenchmarkCaseV1, ...],
    algorithms: tuple[StructureGroupingAlgorithmV2, ...],
    keys: dict[tuple[LeakageAxis, str], str],
    started_at: str = "2026-08-09T20:10:00+08:00",
    completed_at: str = "2026-08-09T20:11:00+08:00",
) -> tuple[
    tuple[StructureGroupingRunV2, ...],
    tuple[StructureGroupingAssignmentV2, ...],
]:
    universe_sha = structure_grouping_case_universe_sha256_v2(cases)
    runs = tuple(
        _identified(
            StructureGroupingRunV2,
            id_field="grouping_run_id",
            sha_field="grouping_run_sha256",
            prefix="structure-group-run",
            values={
                "axis": algorithm.axis,
                "algorithm_id": algorithm.algorithm_id,
                "algorithm_sha256": algorithm.algorithm_sha256,
                "input_case_universe_sha256": universe_sha,
                "runtime_environment_sha256": canonical_sha256(
                    (algorithm.axis.value, "runtime")
                ),
                "started_at": started_at,
                "completed_at": completed_at,
            },
        )
        for algorithm in algorithms
    )
    run_by_axis = {item.axis: item for item in runs}
    algorithm_by_axis = {item.axis: item for item in algorithms}
    assignments = tuple(
        _identified(
            StructureGroupingAssignmentV2,
            id_field="assignment_id",
            sha_field="assignment_sha256",
            prefix="structure-group-assignment",
            values={
                "axis": axis,
                "algorithm_id": algorithm_by_axis[axis].algorithm_id,
                "algorithm_sha256": algorithm_by_axis[axis].algorithm_sha256,
                "grouping_run_id": run_by_axis[axis].grouping_run_id,
                "grouping_run_sha256": run_by_axis[axis].grouping_run_sha256,
                "case_id": case.case_id,
                "case_sha256": case.case_sha256,
                "structure_sha256": case.structure_sha256,
                "canonical_group_key": keys[(axis, case.case_id)],
            },
        )
        for axis in sorted(
            (
                LeakageAxis.STRUCTURE_FINGERPRINT,
                LeakageAxis.STRUCTURE_PROTOTYPE,
            ),
            key=lambda item: item.value,
        )
        for case in cases
    )
    return runs, assignments


def _v2_expert_registry(
    manifest: BenchmarkSplitManifestV2,
) -> ExpertStudyRegistryV2:
    experts = tuple(
        sorted(
            (
                PublicExpertIdentityV2(
                    expert_id="adjudicator-a", role=ExpertRole.ADJUDICATOR
                ),
                PublicExpertIdentityV2(
                    expert_id="reviewer-a", role=ExpertRole.REVIEWER
                ),
                PublicExpertIdentityV2(
                    expert_id="reviewer-b", role=ExpertRole.REVIEWER
                ),
            ),
            key=lambda item: item.expert_id,
        )
    )
    calibration_manifest_id = "calibration-manifest-a"
    calibration_manifest_sha = canonical_sha256("calibration-manifest-a")
    guide_version = "annotation-guide-v2"
    completions = tuple(
        _identified(
            CalibrationCompletionV2,
            id_field="completion_id",
            sha_field="completion_sha256",
            prefix="calibration-completion-v2",
            values={
                "expert_id": expert.expert_id,
                "role": expert.role,
                "annotation_guide_version": guide_version,
                "annotation_guide_sha256": GUIDE_SHA,
                "calibration_manifest_id": calibration_manifest_id,
                "calibration_manifest_sha256": calibration_manifest_sha,
                "raw_answers_sha256": canonical_sha256(
                    (expert.expert_id, "calibration-raw")
                ),
                "calibration_result_sha256": canonical_sha256(
                    (expert.expert_id, "calibration-result")
                ),
                "completed_at": "2026-08-09T19:30:00+08:00",
            },
        )
        for expert in experts
    )
    conflicts = tuple(
        CaseConflictAssessmentV1(
            case_id=case.case_id,
            case_sha256=case.case_sha256,
            expert_id=expert.expert_id,
            status=ConflictStatus.CLEAR,
            reason_code=ConflictReasonCode.NO_CONFLICT,
            disclosure_sha256=canonical_sha256(
                (case.case_id, expert.expert_id, "v2-conflict")
            ),
            assessed_at="2026-08-09T20:25:00+08:00",
        )
        for case in manifest.cases
        for expert in experts
    )
    assignments = tuple(
        CaseExpertAssignmentV1(
            case_id=case.case_id,
            case_sha256=case.case_sha256,
            reviewer_ids=("reviewer-a", "reviewer-b"),
            adjudicator_id="adjudicator-a",
        )
        for case in manifest.cases
    )
    return _identified(
        ExpertStudyRegistryV2,
        id_field="registry_id",
        sha_field="registry_sha256",
        prefix="expert-study-registry-v2",
        values={
            "split_manifest_id": manifest.manifest_id,
            "split_manifest_sha256": manifest.manifest_sha256,
            "annotation_guide_version": guide_version,
            "annotation_guide_sha256": GUIDE_SHA,
            "calibration_manifest_id": calibration_manifest_id,
            "calibration_manifest_sha256": calibration_manifest_sha,
            "public_identity_release_id": "public-expert-identity-a",
            "public_identity_release_sha256": canonical_sha256(
                "public-expert-identity-a"
            ),
            "identity_attestation_id": "private-expert-attestation-a",
            "identity_attestation_sha256": canonical_sha256(
                "private-expert-attestation-a"
            ),
            "experts": experts,
            "calibration_completions": completions,
            "conflict_assessments": conflicts,
            "assignments": assignments,
            "registered_at": "2026-08-09T20:35:00+08:00",
        },
    )


@dataclass(frozen=True)
class _FormalV2Study:
    manifest: BenchmarkSplitManifestV2
    leakage: LeakageComponentReleaseV3
    lineage_curation: MechanismLineageCurationReleaseV3
    assignment_curation: MechanismLineageAssignmentCurationReleaseV3
    registry: ExpertStudyRegistryV2
    candidates: tuple[FrozenCaseCandidateV1, ...]
    lineages: tuple[MechanismLineageAssignmentV3, ...]
    source_policies: tuple[CaseSourcePolicyAttestationV2, ...]
    frozen: FrozenCaseReleaseV2
    decisions: tuple[EligibilityDecisionV1, ...]
    eligibility: PreRunEligibilityReleaseV2
    primary_case_id: str
    replacement_case_id: str


def _v3_private_lineage_person(
    index: int,
) -> MechanismLineageCuratorDeclarationV3:
    return MechanismLineageCuratorDeclarationV3(
        curator_id=f"private-lineage-person-{index}",
        opaque_natural_person_ref=f"opaque-lineage-human-{index}",
        natural_person_commitment_sha256=canonical_sha256(
            {"natural-person": index}
        ),
        identity_evidence_uri=(
            f"private://lineage-governance/person-{index}"
        ),
        identity_evidence_sha256=canonical_sha256(
            {"identity-evidence": index}
        ),
        institutional_unit=f"Independent curation unit {index}",
        conflict_declaration=f"Private conflict declaration {index}",
    )


def _formal_lineage_registry_and_curation(
    definitions: tuple[Any, ...],
) -> tuple[Any, MechanismLineageCurationReleaseV3]:
    """Build the real private governance preimage used by the formal fixture."""

    taxonomy_version = "formal-pilot-global-lineage-v3"
    policy = build_mechanism_lineage_curation_policy_v3(
        taxonomy_version=taxonomy_version,
        taxonomy_scope=(
            "Global Pilot R1/R2 mechanism taxonomy fixed before candidates"
        ),
        definition_review_criteria=V3_LINEAGE_REVIEW_CRITERIA,
        sealed_at="2026-08-09T20:01:00+08:00",
    )
    curators = (
        _v3_private_lineage_person(1),
        _v3_private_lineage_person(2),
    )
    roster = build_mechanism_lineage_curator_roster_v3(
        policy=policy,
        curators=curators,
        adjudicators=(_v3_private_lineage_person(3),),
        independence_review=(
            "Private evidence binds three injective natural-person commitments"
        ),
        sealed_at="2026-08-09T20:02:00+08:00",
    )
    reviews = tuple(
        build_mechanism_lineage_definition_review_v3(
            policy=policy,
            definition=definition,
            curator_id=curator.curator_id,
            decision=MechanismLineageReviewDecisionV3.INCLUDE,
            criterion_findings=V3_LINEAGE_REVIEW_CRITERIA,
            rationale=f"Independent private review supports {definition.lineage_id}",
            reviewed_at="2026-08-09T20:04:00+08:00",
        )
        for definition in definitions
        for curator in curators
    )
    review_manifest = build_mechanism_lineage_evidence_review_manifest_v3(
        policy=policy,
        roster=roster,
        reviews=reviews,
        sealed_at="2026-08-09T20:06:00+08:00",
    )
    registry = build_mechanism_lineage_registry_v3(
        taxonomy_version=taxonomy_version,
        curation_policy_sha256=policy.policy_sha256,
        evidence_review_manifest_sha256=review_manifest.manifest_sha256,
        curator_roster_sha256=roster.roster_sha256,
        definitions=definitions,
        sealed_at="2026-08-09T20:08:00+08:00",
    )
    curation = build_mechanism_lineage_curation_release_v3(
        registry=registry,
        policy=policy,
        roster=roster,
        review_manifest=review_manifest,
        assembled_at="2026-08-09T20:08:30+08:00",
    )
    return registry, curation


def _formal_lineage_assignment_curation(
    *,
    candidates: tuple[FrozenCaseCandidateV1, ...],
    registry: Any,
    definition_curation: MechanismLineageCurationReleaseV3,
    definition_by_family: dict[MechanismFamily, Any],
    rejected_case_id: str | None = None,
) -> tuple[
    MechanismLineageAssignmentCurationReleaseV3,
    tuple[MechanismLineageAssignmentV3, ...],
]:
    policy = build_mechanism_lineage_assignment_curation_policy_v3(
        registry=registry,
        definition_curation_release=definition_curation,
        assignment_review_criteria=V3_ASSIGNMENT_REVIEW_CRITERIA,
        sealed_at="2026-08-09T20:09:00+08:00",
    )
    reviewers = (
        _v3_private_lineage_person(11),
        _v3_private_lineage_person(12),
    )
    roster = build_mechanism_lineage_assignment_reviewer_roster_v3(
        policy=policy,
        reviewers=reviewers,
        adjudicators=(_v3_private_lineage_person(13),),
        independence_review=(
            "Private evidence binds three assignment-review natural persons"
        ),
        sealed_at="2026-08-09T20:09:30+08:00",
    )
    proposals = tuple(
        build_mechanism_lineage_assignment_proposal_v3(
            candidate_id=candidate.candidate_id,
            candidate_sha256=candidate.candidate_sha256,
            case=candidate.case,
            registry=registry,
            definition_curation_release=definition_curation,
            lineage_id=definition_by_family[
                candidate.case.primary_mechanism_stratum
            ].lineage_id,
            assignment_basis_sha256=canonical_sha256(
                (candidate.case.case_id, "lineage-basis")
            ),
            proposed_at="2026-08-09T20:12:40+08:00",
        )
        for candidate in candidates
    )
    universe = build_mechanism_lineage_assignment_candidate_universe_v3(
        registry=registry,
        definition_curation_release=definition_curation,
        policy=policy,
        roster=roster,
        proposals=proposals,
        sealed_at="2026-08-09T20:12:45+08:00",
    )
    reviews = tuple(
        build_mechanism_lineage_assignment_review_v3(
            policy=policy,
            roster=roster,
            candidate_universe=universe,
            proposal=proposal,
            reviewer_id=reviewer.curator_id,
            decision=(
                MechanismLineageAssignmentDecisionV3.REJECT
                if proposal.case.case_id == rejected_case_id
                else MechanismLineageAssignmentDecisionV3.ACCEPT
            ),
            criterion_findings=V3_ASSIGNMENT_REVIEW_CRITERIA,
            rationale=f"Independent exact review for {proposal.proposal_id}",
            reviewed_at="2026-08-09T20:12:50+08:00",
        )
        for proposal in universe.proposals
        for reviewer in reviewers
    )
    review_manifest = build_mechanism_lineage_assignment_review_manifest_v3(
        policy=policy,
        roster=roster,
        candidate_universe=universe,
        reviews=reviews,
        sealed_at="2026-08-09T20:12:55+08:00",
    )
    release = build_mechanism_lineage_assignment_curation_release_v3(
        registry=registry,
        definition_curation_release=definition_curation,
        policy=policy,
        roster=roster,
        candidate_universe=universe,
        review_manifest=review_manifest,
        assembled_at="2026-08-09T20:13:00+08:00",
    )
    assignments = derive_formal_mechanism_lineage_assignments_v3(
        registry=registry,
        definition_curation_release=definition_curation,
        assignment_curation_release=release,
    )
    return release, assignments


def _formal_v2_study(
    *,
    study_phase: BenchmarkSplit = BenchmarkSplit.PILOT_R1,
    round_variant: Literal["r1", "r2"] | None = None,
) -> _FormalV2Study:
    round_variant = (
        ("r1" if study_phase is BenchmarkSplit.PILOT_R1 else "r2")
        if round_variant is None
        else round_variant
    )
    algorithms = tuple(
        _v2_structure_algorithm(axis)
        for axis in sorted(
            (
                LeakageAxis.STRUCTURE_FINGERPRINT,
                LeakageAxis.STRUCTURE_PROTOTYPE,
            ),
            key=lambda item: item.value,
        )
    )
    formulas_by_round = {
        "r1": (
            "HHe",
            "HLi",
            "HBe",
            "HB",
            "HC",
            "HN",
            "HO",
            "HF",
            "HNe",
            "HeLi",
        ),
        "r2": (
            "LiBe",
            "LiB",
            "LiC",
            "LiN",
            "LiO",
            "LiF",
            "LiNe",
            "BeB",
            "BeC",
            "BeN",
        ),
    }
    formulas = formulas_by_round[round_variant]
    definitions = tuple(
        build_mechanism_lineage_definition_v3(
            broad_mechanism_family=tuple(MechanismFamily)[component % 10],
            source_mechanism=f"Frozen mechanism lineage {component}",
            shared_invariant=f"Frozen shared invariant {component}",
            transfer_route_family=f"Frozen transfer route {component}",
            taxonomy_evidence_refs=(
                MechanismLineageEvidenceRefV3(
                    source_id="crossref",
                    source_record_id=f"taxonomy-{component:02d}",
                    source_record_raw_sha256=canonical_sha256(
                        ("taxonomy", component)
                    ),
                ),
            ),
        )
        for component in range(21)
    )
    lineage_registry, lineage_curation = (
        _formal_lineage_registry_and_curation(definitions)
    )
    definition_offset = 0 if round_variant == "r1" else 10
    definition_by_family = {
        tuple(MechanismFamily)[component]: definitions[
            definition_offset + component
        ]
        for component in range(10)
    }

    def make_case(index: int, variant: str) -> FlatBandBenchmarkCaseV1:
        component = index // 3
        mechanism = tuple(MechanismFamily)[component]
        cod = SourceRecordRefV1(
            source_id="cod",
            source_record_id=f"cod-{round_variant}-component-{component:02d}",
            canonical_url=(
                "https://www.crystallography.net/cod/"
                f"{round_variant}-{component:07d}.html"
            ),
            source_version="svn-2026-08-09",
            license_expression="CC0-1.0",
            accessed_at="2026-08-09T20:00:00+08:00",
            raw_sha256=canonical_sha256(("cod", round_variant, component)),
            public_redistribution_allowed=True,
        )
        crossref = SourceRecordRefV1(
            source_id="crossref",
            source_record_id=(
                f"crossref-formal-{round_variant}-{component:02d}"
            ),
            canonical_url=(
                f"https://doi.org/10.7000/formal-{round_variant}-{component:02d}"
            ),
            source_version="REST-v1-2026-08-09",
            license_expression="CC0-1.0",
            accessed_at="2026-08-09T20:00:00+08:00",
            raw_sha256=canonical_sha256(
                ("crossref", round_variant, component)
            ),
            public_redistribution_allowed=True,
        )
        structure_groups = tuple(
            (
                algorithm,
                f"{round_variant}-{algorithm.axis.value.casefold()}-{component:02d}",
            )
            for algorithm in algorithms
        )
        groups = derive_leakage_group_ids_v3(
            formula=formulas[component],
            primary_mechanism_stratum=mechanism,
            source_records=(cod, crossref),
            structure_groups=structure_groups,
            mechanism_lineage_registry=lineage_registry,
            mechanism_lineage_id=definition_by_family[mechanism].lineage_id,
        )
        request = f"Evaluate formal case {index} variant {variant}"
        return _identified(
            FlatBandBenchmarkCaseV1,
            id_field="case_id",
            sha_field="case_sha256",
            prefix="flatband-case",
            values={
                "parent_label": (
                    f"formal-{round_variant}-parent-{index:02d}-{variant}"
                ),
                "formula": formulas[component],
                "structure_sha256": canonical_sha256(
                    ("structure", round_variant, index, variant)
                ),
                "source_records": (cod, crossref),
                "target_class": (
                    TargetBandClass.FB100
                    if index < 15
                    else TargetBandClass.NB300
                ),
                "dimensionality": (
                    Dimensionality.TWO_D
                    if index % 2 == 0
                    else Dimensionality.THREE_D
                ),
                "frozen_request": request,
                "frozen_requirement_sha256": canonical_sha256(request),
                "hard_constraints": ("preserve-parent-structure",),
                "soft_preferences": (),
                "forbidden_transformations": ("replace-parent-structure",),
                "seed_evidence": (),
                "primary_mechanism_stratum": mechanism,
                "leakage_group_ids": groups,
                "public_release_allowed": True,
            },
        )

    primary_cases = [make_case(index, "primary") for index in range(30)]
    replacement = make_case(0, "replacement")
    active_cases = tuple(sorted((replacement, *primary_cases[1:]), key=lambda x: x.case_id))
    candidates = tuple(
        [
            build_frozen_case_candidate(
                case=primary_cases[0],
                slot_id=frozen_case_slot_id(primary_cases[0]),
                priority=0,
                declared_at="2026-08-09T20:12:20+08:00",
            ),
            build_frozen_case_candidate(
                case=replacement,
                slot_id=frozen_case_slot_id(primary_cases[0]),
                priority=1,
                declared_at="2026-08-09T20:12:30+08:00",
            ),
            *(
                build_frozen_case_candidate(
                    case=case,
                    slot_id=frozen_case_slot_id(case),
                    priority=0,
                    declared_at="2026-08-09T20:12:20+08:00",
                )
                for case in primary_cases[1:]
            ),
        ]
    )
    manifest = _identified(
        BenchmarkSplitManifestV2,
        id_field="manifest_id",
        sha_field="manifest_sha256",
        prefix="split-manifest-v2",
        values={
            "manifest_kind": SplitManifestKind(study_phase.value),
            "split_seed": 20260809,
            "cases": tuple(
                SplitCaseRefV2(
                    case_id=case.case_id,
                    case_sha256=case.case_sha256,
                    split=study_phase,
                    target_class=case.target_class,
                    dimensionality=case.dimensionality,
                    primary_mechanism_stratum=case.primary_mechanism_stratum,
                    independence_group_ids=case.leakage_group_ids,
                )
                for case in active_cases
            ),
            "ood_holdout_families": (),
        },
    )
    group_keys = {
        (algorithm.axis, case.case_id): (
            f"{round_variant}-{algorithm.axis.value.casefold()}-"
            f"{(0 if case.case_id == replacement.case_id else primary_cases.index(case) // 3):02d}"
        )
        for algorithm in algorithms
        for case in active_cases
    }
    runs, grouping_assignments = _v2_grouping_artifacts_local(
        cases=active_cases,
        algorithms=algorithms,
        keys=group_keys,
    )
    assignment_curation, lineages = _formal_lineage_assignment_curation(
        candidates=candidates,
        registry=lineage_registry,
        definition_curation=lineage_curation,
        definition_by_family=definition_by_family,
    )
    active_ids = {case.case_id for case in active_cases}
    leakage = build_leakage_component_release_v3(
        cases=active_cases,
        split_manifest=manifest,
        grouping_algorithms=algorithms,
        grouping_runs=runs,
        grouping_assignments=grouping_assignments,
        mechanism_lineage_registry=lineage_registry,
        mechanism_lineage_assignments=tuple(
            item for item in lineages if item.case_id in active_ids
        ),
        created_at=(
            "2026-08-09T20:30:00+08:00"
            if study_phase is BenchmarkSplit.PILOT_R1
            else "2026-08-09T20:30:01+08:00"
        ),
    )
    registry = _v2_expert_registry(manifest)
    source_policies = tuple(
        build_case_source_policy_attestation_v2(
            case=candidate.case,
            source_id=record.source_id,
            source_record_id=record.source_record_id,
            usage_roles=(
                (SourceUseRole.CASE_SEED, SourceUseRole.STRUCTURE)
                if record.source_id == "cod"
                else (
                    SourceUseRole.IDENTIFIER_RESOLUTION,
                    SourceUseRole.LITERATURE_RETRIEVAL,
                )
            ),
            record_license_compatibility_sha256=canonical_sha256(
                (candidate.case.case_id, record.source_id, "license")
            ),
            record_provenance_token_sha256=canonical_sha256(
                (candidate.case.case_id, record.source_id, "provenance")
            ),
            public_fields_release_allowed=True,
            structure_payload_release_allowed=record.source_id == "cod",
        )
        for candidate in candidates
        for record in candidate.case.source_records
    )
    frozen = build_frozen_case_release_v2(
        split_manifest=manifest,
        leakage_release=leakage,
        expert_registry=registry,
        candidates=candidates,
        candidate_lineage_assignments=lineages,
        source_policy_attestations=source_policies,
        case_freeze_policy_sha256=CASE_POLICY_SHA,
        eligibility_policy_sha256=ELIGIBILITY_POLICY_SHA,
        candidate_pool_sealed_at="2026-08-09T20:14:00+08:00",
        frozen_at="2026-08-09T20:40:00+08:00",
    )
    decisions = tuple(
        build_eligibility_decision(
            candidate=candidate,
            status=(
                CaseEligibilityStatus.EXCLUDED
                if candidate.case.case_id == primary_cases[0].case_id
                else CaseEligibilityStatus.INCLUDED
            ),
            reason_codes=(
                (CaseEligibilityReasonCode.PARENT_STRUCTURE_UNVERIFIED,)
                if candidate.case.case_id == primary_cases[0].case_id
                else (CaseEligibilityReasonCode.MEETS_ALL_PREREGISTERED_CRITERIA,)
            ),
            assessor_id="eligibility-curator-a",
            assessment_protocol_sha256=ELIGIBILITY_POLICY_SHA,
            rationale_sha256=canonical_sha256(
                (candidate.case.case_id, "eligibility-rationale-v2")
            ),
            assessed_at="2026-08-09T20:20:00+08:00",
        )
        for candidate in frozen.candidates
    )
    eligibility = build_pre_run_eligibility_release_v2(
        frozen_case_release=frozen,
        decisions=decisions,
        sealed_at="2026-08-09T20:45:00+08:00",
    )
    return _FormalV2Study(
        manifest=manifest,
        leakage=leakage,
        lineage_curation=lineage_curation,
        assignment_curation=assignment_curation,
        registry=registry,
        candidates=frozen.candidates,
        lineages=frozen.candidate_lineage_assignments,
        source_policies=frozen.source_policy_attestations,
        frozen=frozen,
        decisions=decisions,
        eligibility=eligibility,
        primary_case_id=primary_cases[0].case_id,
        replacement_case_id=replacement.case_id,
    )


@pytest.fixture(scope="module")
def formal_v2() -> _FormalV2Study:
    return _formal_v2_study()


@dataclass(frozen=True)
class _FormalV3Study:
    candidate_pool: CandidatePoolReleaseV3
    lineage_curation: MechanismLineageCurationReleaseV3
    assignment_curation: MechanismLineageAssignmentCurationReleaseV3
    public_identity: PublicExpertIdentityReleaseV2
    calibration: CalibrationSetManifestV2
    assignment_release: CandidateEligibilityAssignmentReleaseV3
    raw_audits: tuple[EligibilityRawAuditV3, ...]
    adjudications: tuple[EligibilityAdjudicationV3, ...]
    eligibility: PreRunEligibilityReleaseV3
    manifest: BenchmarkSplitManifestV2
    leakage: LeakageComponentReleaseV3
    registry: ExpertStudyRegistryV2
    frozen: FrozenCaseReleaseV3
    leakage_context: LeakageRoundClosureContextV3
    pre_budget_closure: PilotPreBudgetClosureReleaseV3
    primary_case_id: str
    replacement_case_id: str


def _formal_v3_study(
    *,
    study_phase: Literal["PILOT_R1", "PILOT_R2"] = "PILOT_R1",
    round_variant: Literal["r1", "r2"] | None = None,
    prior_r1_leakage_context: LeakageRoundClosureContextV3 | None = None,
    prior_r1_candidate_pool_release: CandidatePoolReleaseV3 | None = None,
) -> _FormalV3Study:
    resolved_variant = (
        ("r1" if study_phase == "PILOT_R1" else "r2")
        if round_variant is None
        else round_variant
    )
    base = _formal_v2_study(
        study_phase=BenchmarkSplit(study_phase),
        round_variant=resolved_variant,
    )
    registry = base.leakage.mechanism_lineage_registry
    algorithms = base.leakage.grouping_algorithms
    formulas = (
        (
            "HHe",
            "HLi",
            "HBe",
            "HB",
            "HC",
            "HN",
            "HO",
            "HF",
            "HNe",
            "HeLi",
        )
        if resolved_variant == "r1"
        else (
            "LiBe",
            "LiB",
            "LiC",
            "LiN",
            "LiO",
            "LiF",
            "LiNe",
            "BeB",
            "BeC",
            "BeN",
        )
    )
    component_by_formula = {
        formula: index for index, formula in enumerate(formulas)
    }
    candidate_cases = tuple(
        sorted(
            (item.case for item in base.candidates),
            key=lambda item: item.case_id,
        )
    )
    pool_keys = {
        (algorithm.axis, case.case_id): (
            f"{resolved_variant}-{algorithm.axis.value.casefold()}-"
            f"{component_by_formula[case.formula]:02d}"
        )
        for algorithm in algorithms
        for case in candidate_cases
    }
    pool_runs, pool_grouping = _v2_grouping_artifacts_local(
        cases=candidate_cases,
        algorithms=algorithms,
        keys=pool_keys,
        started_at="2026-08-09T20:13:10+08:00",
        completed_at="2026-08-09T20:13:20+08:00",
    )
    pool = build_candidate_pool_release_v3(
        study_phase=study_phase,
        candidates=base.candidates,
        mechanism_lineage_registry=registry,
        mechanism_lineage_curation_release=base.lineage_curation,
        mechanism_lineage_assignment_curation_release=(
            base.assignment_curation
        ),
        candidate_lineage_assignments=base.lineages,
        grouping_algorithms=algorithms,
        grouping_runs=pool_runs,
        grouping_assignments=pool_grouping,
        source_policy_attestations=base.source_policies,
        case_freeze_policy_sha256=CASE_POLICY_SHA,
        eligibility_policy_sha256=ELIGIBILITY_POLICY_SHA,
        sealed_at="2026-08-09T20:14:00+08:00",
    )

    calibration_definition = next(
        item
        for item in registry.definitions
        if item.source_mechanism == "Frozen mechanism lineage 20"
    )
    calibration_cod = SourceRecordRefV1(
        source_id="cod",
        source_record_id="cod-calibration-disjoint",
        canonical_url="https://www.crystallography.net/cod/9999999.html",
        source_version="svn-2026-08-09",
        license_expression="CC0-1.0",
        accessed_at="2026-08-09T20:00:00+08:00",
        raw_sha256=canonical_sha256("calibration-cod-disjoint"),
        public_redistribution_allowed=True,
    )
    calibration_crossref = SourceRecordRefV1(
        source_id="crossref",
        source_record_id="crossref-calibration-disjoint",
        canonical_url="https://doi.org/10.7000/calibration-disjoint",
        source_version="REST-v1-2026-08-09",
        license_expression="CC0-1.0",
        accessed_at="2026-08-09T20:00:00+08:00",
        raw_sha256=canonical_sha256("calibration-crossref-disjoint"),
        public_redistribution_allowed=True,
    )
    calibration_keys = {
        algorithm.axis: f"calibration-{algorithm.axis.value.casefold()}"
        for algorithm in algorithms
    }
    calibration_groups = derive_leakage_group_ids_v3(
        formula="NaCl",
        primary_mechanism_stratum=(
            calibration_definition.broad_mechanism_family
        ),
        source_records=(calibration_cod, calibration_crossref),
        structure_groups=tuple(
            (algorithm, calibration_keys[algorithm.axis])
            for algorithm in algorithms
        ),
        mechanism_lineage_registry=registry,
        mechanism_lineage_id=calibration_definition.lineage_id,
    )
    calibration_case = _identified(
        FlatBandBenchmarkCaseV1,
        id_field="case_id",
        sha_field="case_sha256",
        prefix="flatband-case",
        values={
            "parent_label": "formal-calibration-disjoint",
            "formula": "NaCl",
            "structure_sha256": canonical_sha256("calibration-structure"),
            "source_records": (calibration_cod, calibration_crossref),
            "target_class": TargetBandClass.FB100,
            "dimensionality": Dimensionality.TWO_D,
            "frozen_request": "Disjoint eligibility calibration case",
            "frozen_requirement_sha256": canonical_sha256(
                "Disjoint eligibility calibration case"
            ),
            "hard_constraints": ("preserve-parent-structure",),
            "soft_preferences": (),
            "forbidden_transformations": ("replace-parent-structure",),
            "seed_evidence": (),
            "primary_mechanism_stratum": (
                calibration_definition.broad_mechanism_family
            ),
            "leakage_group_ids": calibration_groups,
            "public_release_allowed": True,
        },
    )
    calibration_lineage = build_mechanism_lineage_assignment_v3(
        registry=registry,
        case=calibration_case,
        lineage_id=calibration_definition.lineage_id,
        case_evidence_refs=tuple(
            MechanismLineageEvidenceRefV3(
                source_id=record.source_id,
                source_record_id=record.source_record_id,
                source_record_raw_sha256=record.raw_sha256,
            )
            for record in calibration_case.source_records
        ),
        assignment_basis_sha256=canonical_sha256(
            "calibration-lineage-basis"
        ),
        assigned_at="2026-08-09T20:12:01+08:00",
    )
    calibration_runs, calibration_grouping = _v2_grouping_artifacts_local(
        cases=(calibration_case,),
        algorithms=algorithms,
        keys={
            (algorithm.axis, calibration_case.case_id): calibration_keys[
                algorithm.axis
            ]
            for algorithm in algorithms
        },
    )
    calibration = build_calibration_set_manifest_v2(
        cases=(calibration_case,),
        annotation_guide_version="annotation-guide-v3",
        annotation_guide_sha256=GUIDE_SHA,
        case_freeze_policy_sha256=CASE_POLICY_SHA,
        grouping_algorithms=algorithms,
        grouping_runs=calibration_runs,
        grouping_assignments=calibration_grouping,
        mechanism_lineage_registry=registry,
        mechanism_lineage_assignments=(calibration_lineage,),
        frozen_at="2026-08-09T20:12:05+08:00",
    )
    bindings = tuple(
        PrivateNaturalPersonBindingV2(
            expert_id=expert_id,
            role=role,
            opaque_natural_person_subject_ref=subject_ref,
            natural_person_commitment_sha256=canonical_sha256(
                (subject_ref, "natural-person")
            ),
            identity_evidence_artifact_uri=(
                f"artifact://private/eligibility/{subject_ref}"
            ),
            identity_evidence_sha256=canonical_sha256(
                (subject_ref, "identity-evidence")
            ),
        )
        for expert_id, role, subject_ref in (
            ("adjudicator-a", ExpertRole.ADJUDICATOR, "person-01"),
            ("reviewer-a", ExpertRole.REVIEWER, "person-02"),
            ("reviewer-b", ExpertRole.REVIEWER, "person-03"),
            ("reviewer-c", ExpertRole.REVIEWER, "person-04"),
        )
    )
    private_identity = build_private_expert_identity_attestation_v2(
        custodian_id="eligibility-identity-custodian",
        custodian_policy_sha256=canonical_sha256("identity-policy-v3"),
        bindings=bindings,
        attested_at="2026-08-09T19:00:00+08:00",
    )
    public_identity = build_public_expert_identity_release_v2(
        private_attestation=private_identity,
        released_at="2026-08-09T19:01:00+08:00",
    )
    completions = tuple(
        build_calibration_completion_v2(
            expert=expert,
            calibration_manifest=calibration,
            raw_answers_sha256=canonical_sha256(
                (expert.expert_id, "eligibility-calibration-raw")
            ),
            calibration_result_sha256=canonical_sha256(
                (expert.expert_id, "eligibility-calibration-result")
            ),
            completed_at="2026-08-09T20:12:10+08:00",
        )
        for expert in public_identity.experts
    )
    conflicts = tuple(
        CaseConflictAssessmentV1(
            case_id=candidate.case.case_id,
            case_sha256=candidate.case.case_sha256,
            expert_id=expert.expert_id,
            status=ConflictStatus.CLEAR,
            reason_code=ConflictReasonCode.NO_CONFLICT,
            disclosure_sha256=canonical_sha256(
                (candidate.case.case_id, expert.expert_id, "eligibility-coi")
            ),
            assessed_at="2026-08-09T20:15:00+08:00",
        )
        for candidate in pool.candidates
        for expert in public_identity.experts
    )
    assignments = tuple(
        _identified(
            CandidateEligibilityAssignmentV3,
            id_field="assignment_id",
            sha_field="assignment_sha256",
            prefix="eligibility-assignment-v3",
            values={
                "candidate_pool_release_id": pool.release_id,
                "candidate_pool_release_sha256": pool.release_sha256,
                "candidate_id": candidate.candidate_id,
                "candidate_sha256": candidate.candidate_sha256,
                "slot_id": candidate.slot_id,
                "case_id": candidate.case.case_id,
                "case_sha256": candidate.case.case_sha256,
                "reviewer_ids": ("reviewer-a", "reviewer-b"),
                "adjudicator_id": "adjudicator-a",
                "assigned_at": "2026-08-09T20:16:00+08:00",
            },
        )
        for candidate in sorted(pool.candidates, key=lambda item: item.candidate_id)
    )
    assignment_release = build_candidate_eligibility_assignment_release_v3(
        candidate_pool_release=pool,
        public_identity_release=public_identity,
        calibration_manifest=calibration,
        calibration_completions=completions,
        conflict_assessments=conflicts,
        assignments=assignments,
        sealed_at="2026-08-09T20:17:00+08:00",
    )
    assignment_by_candidate = {
        item.candidate_id: item for item in assignment_release.assignments
    }
    primary_candidate = next(
        item for item in pool.candidates if item.case.case_id == base.primary_case_id
    )
    raw_audits = tuple(
        _identified(
            EligibilityRawAuditV3,
            id_field="audit_id",
            sha_field="audit_sha256",
            prefix="eligibility-audit-v3",
            values={
                "assignment_release_id": assignment_release.release_id,
                "assignment_release_sha256": assignment_release.release_sha256,
                "assignment_id": assignment.assignment_id,
                "assignment_sha256": assignment.assignment_sha256,
                "candidate_id": assignment.candidate_id,
                "candidate_sha256": assignment.candidate_sha256,
                "slot_id": assignment.slot_id,
                "case_id": assignment.case_id,
                "case_sha256": assignment.case_sha256,
                "reviewer_id": reviewer_id,
                "status": (
                    CaseEligibilityStatus.EXCLUDED
                    if assignment.candidate_id == primary_candidate.candidate_id
                    and reviewer_id == "reviewer-a"
                    else CaseEligibilityStatus.INCLUDED
                ),
                "reason_codes": (
                    (CaseEligibilityReasonCode.PARENT_STRUCTURE_UNVERIFIED,)
                    if assignment.candidate_id == primary_candidate.candidate_id
                    and reviewer_id == "reviewer-a"
                    else (
                        CaseEligibilityReasonCode.MEETS_ALL_PREREGISTERED_CRITERIA,
                    )
                ),
                "assessment_protocol_sha256": ELIGIBILITY_POLICY_SHA,
                "rationale_sha256": canonical_sha256(
                    (assignment.candidate_id, reviewer_id, "raw-audit")
                ),
                "audited_at": (
                    "2026-08-09T20:18:00+08:00"
                    if reviewer_id == "reviewer-a"
                    else "2026-08-09T20:18:30+08:00"
                ),
            },
        )
        for assignment in assignment_release.assignments
        for reviewer_id in assignment.reviewer_ids
    )
    primary_audits = tuple(
        item
        for item in raw_audits
        if item.candidate_id == primary_candidate.candidate_id
    )
    adjudication = build_eligibility_adjudication_v3(
        assignment_release=assignment_release,
        raw_audits=(primary_audits[0], primary_audits[1]),
        final_status=CaseEligibilityStatus.EXCLUDED,
        final_reason_codes=(
            CaseEligibilityReasonCode.PARENT_STRUCTURE_UNVERIFIED,
        ),
        rationale_sha256=canonical_sha256("primary-adjudication"),
        adjudicated_at="2026-08-09T20:19:00+08:00",
    )
    eligibility = build_pre_run_eligibility_release_v3(
        assignment_release=assignment_release,
        raw_audits=raw_audits,
        adjudications=(adjudication,),
        sealed_at="2026-08-09T20:20:00+08:00",
    )
    selected_case_ids = {
        item.selected_case_id for item in eligibility.active_selections
    }
    final_conflicts = tuple(
        item for item in conflicts if item.case_id in selected_case_ids
    )
    assignment_by_case = {
        item.case_id: item for item in assignment_release.assignments
    }
    final_assignments = tuple(
        CaseExpertAssignmentV1(
            case_id=case.case_id,
            case_sha256=case.case_sha256,
            reviewer_ids=assignment_by_case[case.case_id].reviewer_ids,
            adjudicator_id=assignment_by_case[case.case_id].adjudicator_id,
        )
        for case in base.manifest.cases
    )
    final_registry = build_expert_study_registry_v2(
        split_manifest=base.manifest,
        calibration_manifest=calibration,
        public_identity_release=public_identity,
        calibration_completions=completions,
        conflict_assessments=final_conflicts,
        assignments=final_assignments,
        registered_at="2026-08-09T20:35:00+08:00",
    )
    frozen = build_frozen_case_release_v3(
        pre_run_eligibility_release=eligibility,
        split_manifest=base.manifest,
        leakage_release=base.leakage,
        expert_registry=final_registry,
        frozen_at="2026-08-09T20:40:00+08:00",
    )
    selected_cases = tuple(
        sorted(
            (
                candidate.case
                for candidate in pool.candidates
                if candidate.case.case_id in selected_case_ids
            ),
            key=lambda item: item.case_id,
        )
    )
    leakage_context = LeakageRoundClosureContextV3(
        cases=selected_cases,
        split_manifest=base.manifest,
        release=base.leakage,
    )
    pool_context = build_candidate_pool_leakage_context_v3(pool)
    calibration_context = build_calibration_leakage_context_v3(calibration)
    prior_pool_context = (
        None
        if prior_r1_candidate_pool_release is None
        else build_candidate_pool_leakage_context_v3(
            prior_r1_candidate_pool_release
        )
    )
    pre_budget_closure = build_pilot_pre_budget_closure_release_v3(
        frozen_case_release=frozen,
        current_leakage_context=leakage_context,
        current_candidate_pool_context=pool_context,
        calibration_context=calibration_context,
        prior_r1_leakage_context=prior_r1_leakage_context,
        prior_r1_candidate_pool_release=prior_r1_candidate_pool_release,
        prior_r1_candidate_pool_context=prior_pool_context,
        sealed_at="2026-08-09T20:40:30+08:00",
    )
    return _FormalV3Study(
        candidate_pool=pool,
        lineage_curation=base.lineage_curation,
        assignment_curation=base.assignment_curation,
        public_identity=public_identity,
        calibration=calibration,
        assignment_release=assignment_release,
        raw_audits=eligibility.raw_audits,
        adjudications=eligibility.adjudications,
        eligibility=eligibility,
        manifest=base.manifest,
        leakage=base.leakage,
        registry=final_registry,
        frozen=frozen,
        leakage_context=leakage_context,
        pre_budget_closure=pre_budget_closure,
        primary_case_id=base.primary_case_id,
        replacement_case_id=base.replacement_case_id,
    )


@pytest.fixture(scope="module")
def formal_v3() -> _FormalV3Study:
    return _formal_v3_study()


def test_formal_v3_two_audits_adjudication_and_30_case_replacement(
    formal_v3: _FormalV3Study,
) -> None:
    assert len(formal_v3.candidate_pool.candidates) == 31
    assert len({item.slot_id for item in formal_v3.candidate_pool.candidates}) == 30
    assert len(formal_v3.raw_audits) == 62
    assert len(formal_v3.adjudications) == 1
    assert len(formal_v3.frozen.active_case_ids) == 30
    selected = {
        item.selected_case_id for item in formal_v3.eligibility.active_selections
    }
    assert formal_v3.primary_case_id not in selected
    assert formal_v3.replacement_case_id in selected
    assert_pre_run_eligibility_ready_v3(formal_v3.eligibility)


def test_formal_v3_same_chain_supports_pilot_r2_without_v2_fallback() -> None:
    r1 = _formal_v3_study()
    r2 = _formal_v3_study(
        study_phase="PILOT_R2",
        prior_r1_leakage_context=r1.leakage_context,
        prior_r1_candidate_pool_release=r1.candidate_pool,
    )
    assert r2.candidate_pool.study_phase == "PILOT_R2"
    assert all(item.split is BenchmarkSplit.PILOT_R2 for item in r2.manifest.cases)
    assert r2.frozen.pre_run_eligibility_release == r2.eligibility
    assert r2.pre_budget_closure.cross_round_union_verified is True
    assert (
        r2.pre_budget_closure.prior_r1_leakage_context
        == r1.leakage_context
    )


@pytest.mark.parametrize("mutation", ("missing", "third", "same-reviewer", "late"))
def test_formal_v3_rejects_non_exact_or_late_raw_audits(
    formal_v3: _FormalV3Study,
    mutation: str,
) -> None:
    audits = list(formal_v3.raw_audits)
    if mutation == "missing":
        audits.pop()
    elif mutation == "third":
        audits.append(
            _reidentified(
                audits[0],
                id_field="audit_id",
                sha_field="audit_sha256",
                prefix="eligibility-audit-v3",
                reviewer_id="reviewer-foreign",
            )
        )
    elif mutation == "same-reviewer":
        audits[1] = _reidentified(
            audits[1],
            id_field="audit_id",
            sha_field="audit_sha256",
            prefix="eligibility-audit-v3",
            reviewer_id=audits[0].reviewer_id,
        )
    else:
        audits[0] = _reidentified(
            audits[0],
            id_field="audit_id",
            sha_field="audit_sha256",
            prefix="eligibility-audit-v3",
            audited_at="2026-08-09T20:20:00+08:00",
        )
    with pytest.raises(
        (ValidationError, ValueError),
        match="exactly cover|duplicate|must follow assignment seal",
    ):
        build_pre_run_eligibility_release_v3(
            assignment_release=formal_v3.assignment_release,
            raw_audits=tuple(audits),
            adjudications=formal_v3.adjudications,
            sealed_at="2026-08-09T20:20:00+08:00",
        )


@pytest.mark.parametrize("mutation", ("missing", "orphan"))
def test_formal_v3_adjudication_exists_iff_raw_audits_disagree(
    formal_v3: _FormalV3Study,
    mutation: str,
) -> None:
    adjudications: tuple[EligibilityAdjudicationV3, ...]
    if mutation == "missing":
        adjudications = ()
    else:
        consensus_assignment = next(
            item
            for item in formal_v3.assignment_release.assignments
            if item.case_id != formal_v3.primary_case_id
        )
        forged = _reidentified(
            formal_v3.adjudications[0],
            id_field="adjudication_id",
            sha_field="adjudication_sha256",
            prefix="eligibility-adjudication-v3",
            assignment_id=consensus_assignment.assignment_id,
            assignment_sha256=consensus_assignment.assignment_sha256,
            candidate_id=consensus_assignment.candidate_id,
            candidate_sha256=consensus_assignment.candidate_sha256,
            slot_id=consensus_assignment.slot_id,
            case_id=consensus_assignment.case_id,
            case_sha256=consensus_assignment.case_sha256,
        )
        adjudications = (forged,)
    with pytest.raises((ValidationError, ValueError), match="exactly cover|consensus"):
        build_pre_run_eligibility_release_v3(
            assignment_release=formal_v3.assignment_release,
            raw_audits=formal_v3.raw_audits,
            adjudications=adjudications,
            sealed_at="2026-08-09T20:20:00+08:00",
        )


def test_formal_v3_allows_independent_third_adjudication_outcome(
    formal_v3: _FormalV3Study,
) -> None:
    primary_audits = tuple(
        item
        for item in formal_v3.raw_audits
        if item.case_id == formal_v3.primary_case_id
    )
    third_outcome = build_eligibility_adjudication_v3(
        assignment_release=formal_v3.assignment_release,
        raw_audits=(primary_audits[0], primary_audits[1]),
        final_status=CaseEligibilityStatus.EXCLUDED,
        final_reason_codes=(
            CaseEligibilityReasonCode.CONSTRAINTS_INCOMPLETE,
            CaseEligibilityReasonCode.PARENT_STRUCTURE_UNVERIFIED,
        ),
        rationale_sha256=canonical_sha256("independent-third-outcome"),
        adjudicated_at="2026-08-09T20:19:00+08:00",
    )
    release = build_pre_run_eligibility_release_v3(
        assignment_release=formal_v3.assignment_release,
        raw_audits=formal_v3.raw_audits,
        adjudications=(third_outcome,),
        sealed_at="2026-08-09T20:20:00+08:00",
    )
    decision = next(
        item for item in release.decisions if item.case_id == formal_v3.primary_case_id
    )
    assert decision.reason_codes == third_outcome.final_reason_codes


def test_formal_v3_rejects_post_selection_expert_assignment_rewrite(
    formal_v3: _FormalV3Study,
) -> None:
    assignments = list(formal_v3.registry.assignments)
    original = assignments[0]
    assignments[0] = CaseExpertAssignmentV1(
        case_id=original.case_id,
        case_sha256=original.case_sha256,
        reviewer_ids=("reviewer-a", "reviewer-c"),
        adjudicator_id=original.adjudicator_id,
    )
    rewritten = build_expert_study_registry_v2(
        split_manifest=formal_v3.manifest,
        calibration_manifest=formal_v3.calibration,
        public_identity_release=formal_v3.public_identity,
        calibration_completions=(
            formal_v3.assignment_release.calibration_completions
        ),
        conflict_assessments=formal_v3.registry.conflict_assessments,
        assignments=tuple(assignments),
        registered_at=formal_v3.registry.registered_at,
    )
    with pytest.raises(ValueError, match="assignment differs"):
        build_frozen_case_release_v3(
            pre_run_eligibility_release=formal_v3.eligibility,
            split_manifest=formal_v3.manifest,
            leakage_release=formal_v3.leakage,
            expert_registry=rewritten,
            frozen_at=formal_v3.frozen.frozen_at,
        )


def test_formal_v3_rejects_post_selection_conflict_rewrite(
    formal_v3: _FormalV3Study,
) -> None:
    conflicts = list(formal_v3.registry.conflict_assessments)
    original = conflicts[0]
    conflicts[0] = CaseConflictAssessmentV1(
        case_id=original.case_id,
        case_sha256=original.case_sha256,
        expert_id=original.expert_id,
        status=original.status,
        reason_code=original.reason_code,
        disclosure_sha256=canonical_sha256("post-selection-coi-rewrite"),
        assessed_at=original.assessed_at,
    )
    rewritten = build_expert_study_registry_v2(
        split_manifest=formal_v3.manifest,
        calibration_manifest=formal_v3.calibration,
        public_identity_release=formal_v3.public_identity,
        calibration_completions=(
            formal_v3.assignment_release.calibration_completions
        ),
        conflict_assessments=tuple(conflicts),
        assignments=formal_v3.registry.assignments,
        registered_at=formal_v3.registry.registered_at,
    )
    with pytest.raises(ValueError, match="rewrites selected-case conflict"):
        build_frozen_case_release_v3(
            pre_run_eligibility_release=formal_v3.eligibility,
            split_manifest=formal_v3.manifest,
            leakage_release=formal_v3.leakage,
            expert_registry=rewritten,
            frozen_at=formal_v3.frozen.frozen_at,
        )


def test_formal_v3_rejects_forged_nonpool_replacement_selection(
    formal_v3: _FormalV3Study,
) -> None:
    selections = list(formal_v3.eligibility.active_selections)
    selections[0] = selections[0].model_copy(
        update={
            "selected_candidate_id": "foreign-candidate",
            "selected_candidate_sha256": SHA_A,
            "selected_case_id": "foreign-case",
            "selected_case_sha256": SHA_B,
        }
    )
    with pytest.raises((ValidationError, ValueError), match="do not replay"):
        _reidentified(
            formal_v3.eligibility,
            id_field="release_id",
            sha_field="release_sha256",
            prefix="pre-run-eligibility-v3",
            active_selections=tuple(selections),
        )


def test_formal_v3_rejects_missing_full_pool_structure_assignment(
    formal_v3: _FormalV3Study,
) -> None:
    pool = formal_v3.candidate_pool
    with pytest.raises((ValidationError, ValueError), match="exactly cover"):
        _reidentified(
            pool,
            id_field="release_id",
            sha_field="release_sha256",
            prefix="candidate-pool-v3",
            grouping_assignments=pool.grouping_assignments[:-1],
        )


def test_formal_v3_candidate_pool_replays_private_assignment_curation(
    formal_v3: _FormalV3Study,
) -> None:
    pool = formal_v3.candidate_pool
    assert pool.private_custody_required is True
    assert pool.public_release_allowed is False
    assert (
        pool.mechanism_lineage_assignment_curation_release
        == formal_v3.pre_budget_closure.frozen_case_release
        .pre_run_eligibility_release.assignment_release.candidate_pool_release
        .mechanism_lineage_assignment_curation_release
    )
    assert (
        formal_v3.pre_budget_closure
        .lineage_assignment_curation_release_sha256
        == pool.mechanism_lineage_assignment_curation_release.release_sha256
    )


def test_formal_v3_rejects_rejected_assignment_proposal_in_pool(
    formal_v3: _FormalV3Study,
) -> None:
    pool = formal_v3.candidate_pool
    lineage_by_case = {
        item.case_id: item for item in pool.candidate_lineage_assignments
    }
    definition_by_id = {
        item.lineage_id: item
        for item in pool.mechanism_lineage_registry.definitions
    }
    definition_by_family = {
        candidate.case.primary_mechanism_stratum: definition_by_id[
            lineage_by_case[candidate.case.case_id].lineage_id
        ]
        for candidate in pool.candidates
    }
    rejected_curation, _ = _formal_lineage_assignment_curation(
        candidates=pool.candidates,
        registry=pool.mechanism_lineage_registry,
        definition_curation=pool.mechanism_lineage_curation_release,
        definition_by_family=definition_by_family,
        rejected_case_id=pool.candidates[0].case.case_id,
    )
    with pytest.raises(
        (ValidationError, ValueError),
        match="exact accepted curation projection",
    ):
        build_candidate_pool_release_v3(
            study_phase=pool.study_phase,
            candidates=pool.candidates,
            mechanism_lineage_registry=pool.mechanism_lineage_registry,
            mechanism_lineage_curation_release=(
                pool.mechanism_lineage_curation_release
            ),
            mechanism_lineage_assignment_curation_release=(
                rejected_curation
            ),
            candidate_lineage_assignments=pool.candidate_lineage_assignments,
            grouping_algorithms=pool.grouping_algorithms,
            grouping_runs=pool.grouping_runs,
            grouping_assignments=pool.grouping_assignments,
            source_policy_attestations=pool.source_policy_attestations,
            case_freeze_policy_sha256=pool.case_freeze_policy_sha256,
            eligibility_policy_sha256=pool.eligibility_policy_sha256,
            sealed_at=pool.sealed_at,
        )


def test_formal_v3_grouping_run_must_follow_all_candidate_declarations(
    formal_v3: _FormalV3Study,
) -> None:
    pool = formal_v3.candidate_pool
    keys = {
        (item.axis, item.case_id): item.canonical_group_key
        for item in pool.grouping_assignments
    }
    early_runs, early_assignments = _v2_grouping_artifacts_local(
        cases=tuple(
            sorted(
                (item.case for item in pool.candidates),
                key=lambda item: item.case_id,
            )
        ),
        algorithms=pool.grouping_algorithms,
        keys=keys,
        started_at="2026-08-09T20:12:25+08:00",
        completed_at="2026-08-09T20:13:20+08:00",
    )
    with pytest.raises(
        (ValidationError, ValueError), match="follow all candidate declarations"
    ):
        build_candidate_pool_release_v3(
            study_phase=pool.study_phase,
            candidates=pool.candidates,
            mechanism_lineage_registry=pool.mechanism_lineage_registry,
            mechanism_lineage_curation_release=(
                pool.mechanism_lineage_curation_release
            ),
            mechanism_lineage_assignment_curation_release=(
                pool.mechanism_lineage_assignment_curation_release
            ),
            candidate_lineage_assignments=pool.candidate_lineage_assignments,
            grouping_algorithms=pool.grouping_algorithms,
            grouping_runs=early_runs,
            grouping_assignments=early_assignments,
            source_policy_attestations=pool.source_policy_attestations,
            case_freeze_policy_sha256=pool.case_freeze_policy_sha256,
            eligibility_policy_sha256=pool.eligibility_policy_sha256,
            sealed_at=pool.sealed_at,
        )


@pytest.mark.parametrize("universe", ("candidate", "calibration"))
def test_formal_v3_prebudget_rejects_nonexact_unsplit_context(
    formal_v3: _FormalV3Study,
    universe: str,
) -> None:
    closure = formal_v3.pre_budget_closure
    pool_context = closure.current_candidate_pool_context
    calibration_context = closure.calibration_context
    if universe == "candidate":
        pool_context = pool_context.model_copy(
            update={"cases": pool_context.cases[:-1]}
        )
    else:
        calibration_context = calibration_context.model_copy(
            update={"source_artifact_sha256": SHA_D}
        )
    with pytest.raises(
        (ValidationError, ValueError), match="context differs from"
    ):
        build_pilot_pre_budget_closure_release_v3(
            frozen_case_release=formal_v3.frozen,
            current_leakage_context=formal_v3.leakage_context,
            current_candidate_pool_context=pool_context,
            calibration_context=calibration_context,
            sealed_at=closure.sealed_at,
        )


def test_formal_v3_r2_rejects_missing_prior_pool_prebudget() -> None:
    r1 = _formal_v3_study()
    with pytest.raises(
        (ValidationError, ValueError), match="exact prior R1 leakage and pool"
    ):
        _formal_v3_study(
            study_phase="PILOT_R2",
            prior_r1_leakage_context=r1.leakage_context,
        )


def test_formal_v3_r2_prebudget_rejects_overlapping_full_candidate_pool() -> None:
    r1 = _formal_v3_study()
    with pytest.raises(
        (ValidationError, ValueError),
        match="appears in more than one|crosses benchmark universes",
    ):
        _formal_v3_study(
            study_phase="PILOT_R2",
            round_variant="r1",
            prior_r1_leakage_context=r1.leakage_context,
            prior_r1_candidate_pool_release=r1.candidate_pool,
        )


def test_frozen_case_v2_replays_source_lineage_replacement_and_eligibility(
    formal_v2: _FormalV2Study,
) -> None:
    assert len(formal_v2.frozen.active_case_ids) == 30
    assert len(formal_v2.frozen.candidates) == 31
    assert len(formal_v2.frozen.source_policy_attestations) == 62
    activated = tuple(
        item
        for item in formal_v2.eligibility.active_selections
        if item.replacement_activated
    )
    assert len(activated) == 1
    assert activated[0].selected_case_id == formal_v2.replacement_case_id
    assert formal_v2.eligibility.execution_authorized is True
    assert_pre_run_eligibility_ready_v2(formal_v2.eligibility)


def test_v1_frozen_and_eligibility_cannot_alias_v2(
    study: _Study,
) -> None:
    with pytest.raises(ValidationError):
        FrozenCaseReleaseV2.model_validate(
            study.frozen.model_dump(mode="python", round_trip=True)
        )
    with pytest.raises(ValidationError):
        PreRunEligibilityReleaseV2.model_validate(
            study.eligibility.model_dump(mode="python", round_trip=True)
        )


@pytest.mark.parametrize("mutation", ["missing", "duplicate"])
def test_frozen_case_v2_rejects_missing_or_duplicate_candidate(
    formal_v2: _FormalV2Study, mutation: str
) -> None:
    candidates = list(formal_v2.candidates)
    if mutation == "missing":
        candidates.pop()
    else:
        candidates.append(candidates[0])
    with pytest.raises((ValidationError, ValueError)):
        build_frozen_case_release_v2(
            split_manifest=formal_v2.manifest,
            leakage_release=formal_v2.leakage,
            expert_registry=formal_v2.registry,
            candidates=tuple(candidates),
            candidate_lineage_assignments=formal_v2.lineages,
            source_policy_attestations=formal_v2.source_policies,
            case_freeze_policy_sha256=CASE_POLICY_SHA,
            eligibility_policy_sha256=ELIGIBILITY_POLICY_SHA,
            candidate_pool_sealed_at="2026-08-09T20:14:00+08:00",
            frozen_at="2026-08-09T20:40:00+08:00",
        )


def test_source_policy_leaf_is_an_exact_cases_reexport_with_schema_parity() -> None:
    names = (
        "CaseSourcePolicyAttestationV2",
        "SourceCatalogDecision",
        "SourceUseRole",
        "_SOURCE_CATALOG_POLICY_V1",
        "assert_case_source_policy_v2",
        "build_case_source_policy_attestation_v2",
    )
    for name in names:
        assert name in flatband_cases_module.__all__
        assert name in source_policy_module.__all__
        assert getattr(flatband_cases_module, name) is getattr(
            source_policy_module, name
        )

    schema = CaseSourcePolicyAttestationV2.model_json_schema()
    assert schema == (
        source_policy_module.CaseSourcePolicyAttestationV2.model_json_schema()
    )
    canonical_schema = json.dumps(
        schema, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    assert hashlib.sha256(canonical_schema).hexdigest() == (
        "88cd02b83efab63303594ad1344fb7b9d2ba16cc4897e9f1bb7c6a163e0492a0"
    )


def test_source_policy_include_row_accepts_only_catalog_roles() -> None:
    case = _case(0)
    record = case.source_records[0]
    policy = build_case_source_policy_attestation_v2(
        case=case,
        source_id=record.source_id,
        source_record_id=record.source_record_id,
        usage_roles=(SourceUseRole.STRUCTURE, SourceUseRole.CASE_SEED),
        record_license_compatibility_sha256=SHA_A,
        record_provenance_token_sha256=SHA_B,
        public_fields_release_allowed=True,
        structure_payload_release_allowed=True,
    )
    assert policy.usage_roles == (
        SourceUseRole.CASE_SEED,
        SourceUseRole.STRUCTURE,
    )
    assert_case_source_policy_v2(case=case, attestations=(policy,))

    with pytest.raises((ValidationError, ValueError), match="usage role"):
        build_case_source_policy_attestation_v2(
            case=case,
            source_id=record.source_id,
            source_record_id=record.source_record_id,
            usage_roles=(SourceUseRole.LITERATURE_RETRIEVAL,),
            record_license_compatibility_sha256=SHA_A,
            record_provenance_token_sha256=SHA_B,
            public_fields_release_allowed=True,
            structure_payload_release_allowed=False,
        )


def test_source_policy_rejects_conditional_and_excluded_rows() -> None:
    base = _case(0)
    for source_id, license_expression, message in (
        ("nomad", "CC-BY-4.0", "INCLUDE-only"),
        ("icsd", "LicenseRef-ICSD-Restricted", "EXCLUDE"),
    ):
        source = SourceRecordRefV1(
            source_id=source_id,
            source_record_id=f"{source_id}-record-a",
            canonical_url=f"https://example.org/{source_id}/record-a",
            source_version="2026-08-09",
            license_expression=license_expression,
            accessed_at="2026-08-09T20:00:00+08:00",
            raw_sha256=canonical_sha256((source_id, "record-a")),
            public_redistribution_allowed=False,
        )
        case = _reidentified_case(
            base,
            source_records=(source,),
            public_release_allowed=False,
        )
        with pytest.raises((ValidationError, ValueError), match=message):
            build_case_source_policy_attestation_v2(
                case=case,
                source_id=source.source_id,
                source_record_id=source.source_record_id,
                usage_roles=(SourceUseRole.STRUCTURE,),
                record_license_compatibility_sha256=SHA_A,
                record_provenance_token_sha256=SHA_B,
                public_fields_release_allowed=False,
                structure_payload_release_allowed=False,
                conditional_gate_token_sha256=SHA_C,
            )


def test_executable_source_policy_matches_all_twenty_frozen_catalog_rows() -> None:
    catalog_path = (
        Path(__file__).resolve().parents[2]
        / "artifacts/experiment/flatband-benchmark-20260809/source_catalog.jsonl"
    )
    raw = catalog_path.read_bytes()
    assert hashlib.sha256(raw).hexdigest() == SOURCE_CATALOG_V1_SHA256
    rows = tuple(json.loads(line) for line in raw.decode("utf-8").splitlines())
    assert len(rows) == 20
    assert set(_SOURCE_CATALOG_POLICY_V1) == {
        item["source_id"] for item in rows
    }
    for row in rows:
        row_sha, decision, roles, licenses = _SOURCE_CATALOG_POLICY_V1[
            row["source_id"]
        ]
        assert row_sha == canonical_sha256(row)
        assert decision.value == row["decision"]
        assert {item.value for item in roles} == set(row["roles"])
        assert licenses == {
            item["expression"] for item in row["licenses"]
        }


def test_source_policy_rejects_seed_evidence_from_a_foreign_record() -> None:
    case = _case(0)
    foreign = SourceRecordRefV1(
        source_id="crossref",
        source_record_id="foreign-evidence-record",
        canonical_url="https://doi.org/10.7000/foreign-evidence",
        source_version="REST-v1-2026-08-09",
        license_expression="CC0-1.0",
        accessed_at="2026-08-09T20:00:00+08:00",
        raw_sha256=canonical_sha256("foreign-evidence-record"),
        public_redistribution_allowed=True,
    )
    evidence = _identified(
        FlatBandEvidenceV1,
        id_field="evidence_id",
        sha_field="evidence_sha256",
        prefix="band-evidence",
        values={
            "source_record": foreign,
            "claim_type": EvidenceClaimType.PARSED,
            "bandwidth_e_v": None,
            "fermi_distance_e_v": None,
            "observed_class": ObservedBandClass.UNQUANTIFIED,
            "bandwidth_scope": BandwidthScope.SOURCE_LABEL_ONLY,
            "k_sampling": "not reported",
            "isolation_gap_e_v": None,
            "soc_state": SocState.UNKNOWN,
            "magnetic_order": MagneticOrder.PARAMAGNETIC_OR_UNKNOWN,
            "spin_channel": None,
            "hubbard_u_e_v": None,
            "band_tracking_method": "source label only",
        },
    )
    case = _reidentified_case(case, seed_evidence=(evidence,))
    record = case.source_records[0]
    policy = build_case_source_policy_attestation_v2(
        case=case,
        source_id=record.source_id,
        source_record_id=record.source_record_id,
        usage_roles=(SourceUseRole.CASE_SEED, SourceUseRole.STRUCTURE),
        record_license_compatibility_sha256=SHA_A,
        record_provenance_token_sha256=SHA_B,
        public_fields_release_allowed=True,
        structure_payload_release_allowed=True,
    )
    with pytest.raises(ValueError, match="seed evidence source record"):
        assert_case_source_policy_v2(case=case, attestations=(policy,))


def test_frozen_case_v2_rejects_source_policy_and_content_hash_tampering(
    formal_v2: _FormalV2Study,
) -> None:
    policy = formal_v2.source_policies[0]
    forged_policy = policy.model_copy(
        update={"source_catalog_row_sha256": SHA_D}
    )
    payload = formal_v2.frozen.model_dump(mode="python", round_trip=True)
    payload["source_policy_attestations"] = (
        forged_policy,
        *formal_v2.source_policies[1:],
    )
    with pytest.raises((ValidationError, ValueError), match="catalog row|attestation"):
        FrozenCaseReleaseV2.model_validate(payload)
    forged_release = formal_v2.frozen.model_copy(update={"release_sha256": SHA_A})
    with pytest.raises((ValidationError, ValueError), match="release_sha256"):
        FrozenCaseReleaseV2.model_validate(
            forged_release.model_dump(mode="python", round_trip=True)
        )


@pytest.mark.parametrize("upstream", ["expert", "leakage"])
def test_frozen_case_v2_rejects_registry_and_leakage_crosswire(
    formal_v2: _FormalV2Study, upstream: str
) -> None:
    expert = formal_v2.registry
    leakage = formal_v2.leakage
    if upstream == "expert":
        values = {
            name: getattr(expert, name)
            for name in type(expert).model_fields
            if name not in {"registry_id", "registry_sha256"}
        }
        values["split_manifest_sha256"] = SHA_D
        expert = _identified(
            ExpertStudyRegistryV2,
            id_field="registry_id",
            sha_field="registry_sha256",
            prefix="expert-study-registry-v2",
            values=values,
        )
    else:
        values = {
            name: getattr(leakage, name)
            for name in type(leakage).model_fields
            if name not in {"release_id", "release_sha256"}
        }
        values["split_manifest_sha256"] = SHA_D
        leakage = _identified(
            LeakageComponentReleaseV3,
            id_field="release_id",
            sha_field="release_sha256",
            prefix="leakage-release-v3",
            values=values,
        )
    with pytest.raises((ValidationError, ValueError), match="different split|different split V2"):
        build_frozen_case_release_v2(
            split_manifest=formal_v2.manifest,
            leakage_release=leakage,
            expert_registry=expert,
            candidates=formal_v2.candidates,
            candidate_lineage_assignments=formal_v2.lineages,
            source_policy_attestations=formal_v2.source_policies,
            case_freeze_policy_sha256=CASE_POLICY_SHA,
            eligibility_policy_sha256=ELIGIBILITY_POLICY_SHA,
            candidate_pool_sealed_at="2026-08-09T20:14:00+08:00",
            frozen_at="2026-08-09T20:40:00+08:00",
        )


def test_pre_run_v2_rejects_missing_decision_and_post_upstream_assessment(
    formal_v2: _FormalV2Study,
) -> None:
    with pytest.raises((ValidationError, ValueError), match="exactly cover"):
        build_pre_run_eligibility_release_v2(
            frozen_case_release=formal_v2.frozen,
            decisions=formal_v2.decisions[:-1],
            sealed_at="2026-08-09T20:45:00+08:00",
        )
    late = build_eligibility_decision(
        candidate=formal_v2.candidates[0],
        status=CaseEligibilityStatus.INCLUDED,
        reason_codes=(CaseEligibilityReasonCode.MEETS_ALL_PREREGISTERED_CRITERIA,),
        assessor_id="eligibility-curator-a",
        assessment_protocol_sha256=ELIGIBILITY_POLICY_SHA,
        rationale_sha256=canonical_sha256("late-eligibility"),
        assessed_at="2026-08-09T20:36:00+08:00",
    )
    decisions = tuple(
        late if item.candidate_id == late.candidate_id else item
        for item in formal_v2.decisions
    )
    with pytest.raises((ValidationError, ValueError), match="before leakage/expert"):
        build_pre_run_eligibility_release_v2(
            frozen_case_release=formal_v2.frozen,
            decisions=decisions,
            sealed_at="2026-08-09T20:45:00+08:00",
        )
