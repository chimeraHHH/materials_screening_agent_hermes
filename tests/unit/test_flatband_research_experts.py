from __future__ import annotations

import json
from typing import Any, TypeVar

import pytest
from pydantic import ValidationError
from pymatgen.core import Composition, Lattice, Structure

from material_agent.inspiration.models import (
    StrictModel,
    canonical_sha256,
    deterministic_id,
)
from material_agent.research.flatband_contracts import (
    BenchmarkSplit,
    BenchmarkSplitManifestV1,
    BenchmarkSplitManifestV2,
    Dimensionality,
    ExpertRole,
    FlatBandBenchmarkCaseV1,
    MechanismFamily,
    SourceRecordRefV1,
    SplitCaseRefV1,
    SplitCaseRefV2,
    SplitManifestKind,
    TargetBandClass,
)
from material_agent.research.flatband_derivative_screening import (
    DerivativeClass,
    DerivativeScreeningReleaseV3,
    build_derivative_screening_adjudication_v3,
    build_derivative_screening_assignment_v3,
    build_derivative_screening_policy_v3,
    build_derivative_screening_raw_review_v3,
    build_derivative_screening_release_v3,
    build_derivative_screening_reviewer_roster_v3,
    build_derivative_source_evidence_ref_v3,
)
from material_agent.research.flatband_execution import (
    BudgetManifestV1,
    ResearchSystemId,
    SourceBudgetV1,
    SourceVariant,
    SystemConfigV1,
    source_policy_values,
)
from material_agent.research.flatband_experts import (
    CalibrationCompletionV1,
    CalibrationCompletionV2,
    CalibrationSetManifestV2,
    CaseConflictAssessmentV1,
    CaseExpertAssignmentV1,
    ConflictReasonCode,
    ConflictStatus,
    ExpertProfileV1,
    ExpertStudyRegistryV1,
    ExpertStudyRegistryV2,
    PrivateExpertIdentityCustodianAttestationV2,
    PrivateNaturalPersonBindingV2,
    PublicExpertIdentityReleaseV2,
    assert_calibration_completion_replays_manifest_v2,
    assert_calibration_disjoint_from_benchmarks_v2,
    assert_distinct_natural_person_assignments_v2,
    assert_expert_registry_covers_split,
    assert_expert_registry_covers_split_v2,
    assert_expert_registry_sealed_before_budgets_v2,
    assert_formal_pilot_expert_closure_legacy_v2_upstream,
    assert_legacy_v2_calibration_disjointness_unavailable,
    assert_private_identity_attestation_matches_public_v2,
    build_calibration_completion_v2,
    build_calibration_set_manifest_v2,
    build_expert_study_registry_v2,
    build_private_expert_identity_attestation_v2,
    build_public_expert_identity_release_v2,
)
from material_agent.research.flatband_leakage import (
    LeakageAxis,
    LeakageAxisV3,
    LeakageComponentReleaseV3,
    MechanismLineageAssignmentV3,
    MechanismLineageCuratorDeclarationV3,
    MechanismLineageDefinitionV3,
    MechanismLineageEvidenceRefV3,
    MechanismLineageRegistryV3,
    StructureGroupingAlgorithmV2,
    StructureGroupingAssignmentV2,
    StructureGroupingRunV2,
    build_leakage_component_release_v3,
    build_mechanism_lineage_assignment_v3,
    build_mechanism_lineage_definition_v3,
    build_mechanism_lineage_registry_v3,
    derive_leakage_group_ids_v3,
    structure_grouping_case_universe_sha256_v2,
)
from material_agent.research.flatband_source_policy import (
    SourceUseRole,
    build_case_source_policy_attestation_v2,
)
from material_agent.research.flatband_structure_grouping import (
    PreGroupCandidatePreimageV2,
    StructureDimensionalityV2,
    build_raw_structure_artifact_v2,
    build_structure_grouping_case_input_v2,
    finalize_structure_grouping_release_v2,
    normalize_raw_structure_artifact_v2,
    run_structure_grouping_computation_v2,
    seal_structure_grouping_input_manifest_v2,
)

ModelT = TypeVar("ModelT", bound=StrictModel)
GUIDE_SHA = "a" * 64
CALIBRATION_SET_SHA = "b" * 64


def _identified(
    model_type: type[ModelT],
    *,
    id_field: str,
    sha_field: str,
    prefix: str,
    values: dict[str, Any],
) -> ModelT:
    draft = model_type.model_construct(**values)
    semantic = draft.model_dump(mode="python", exclude={id_field, sha_field})
    digest = canonical_sha256(semantic)
    return model_type.model_validate(
        {
            **values,
            sha_field: digest,
            id_field: deterministic_id(prefix, {sha_field: digest}),
        }
    )


def _split() -> BenchmarkSplitManifestV1:
    mechanisms = (
        MechanismFamily.LATTICE_INTERFERENCE,
        MechanismFamily.LINE_GRAPH,
        MechanismFamily.ORBITAL_FRUSTRATION_HYBRIDIZATION,
        MechanismFamily.SYMMETRY_INDUCED,
        MechanismFamily.CONFINEMENT,
    )
    cases = tuple(
        SplitCaseRefV1(
            case_id=f"case-{index:03d}",
            case_sha256=f"{index + 1:064x}",
            split=BenchmarkSplit.PILOT_R1,
            target_class=(
                TargetBandClass.FB100 if index < 15 else TargetBandClass.NB300
            ),
            dimensionality=(
                Dimensionality.TWO_D if index % 2 == 0 else Dimensionality.THREE_D
            ),
            primary_mechanism_stratum=mechanisms[index // 6],
            leakage_group_ids=(f"component-{index:03d}",),
        )
        for index in range(30)
    )
    return _identified(
        BenchmarkSplitManifestV1,
        id_field="manifest_id",
        sha_field="manifest_sha256",
        prefix="split-manifest",
        values={
            "manifest_kind": SplitManifestKind.PILOT_R1,
            "split_seed": 20260809,
            "cases": cases,
            "ood_holdout_families": (),
        },
    )


def _split_v2() -> BenchmarkSplitManifestV2:
    legacy = _split()
    cases = tuple(
        SplitCaseRefV2(
            case_id=item.case_id,
            case_sha256=item.case_sha256,
            split=item.split,
            target_class=item.target_class,
            dimensionality=item.dimensionality,
            primary_mechanism_stratum=item.primary_mechanism_stratum,
            independence_group_ids=item.leakage_group_ids,
        )
        for item in legacy.cases
    )
    return _identified(
        BenchmarkSplitManifestV2,
        id_field="manifest_id",
        sha_field="manifest_sha256",
        prefix="split-manifest-v2",
        values={
            "manifest_kind": SplitManifestKind.PILOT_R1,
            "split_seed": legacy.split_seed,
            "cases": cases,
            "ood_holdout_families": (),
        },
    )


def _profiles(*, include_substitute: bool = False) -> tuple[ExpertProfileV1, ...]:
    values = [
        ExpertProfileV1(
            expert_id="adjudicator-a",
            role=ExpertRole.ADJUDICATOR,
            domain_expertise=("electronic structure", "flat-band physics"),
            qualification_summary="Independent senior adjudicator with flat-band expertise.",
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
    ]
    if include_substitute:
        values.append(
            ExpertProfileV1(
                expert_id="reviewer-c",
                role=ExpertRole.REVIEWER,
                domain_expertise=("topological materials",),
                qualification_summary="Prequalified substitute reviewer.",
            )
        )
    return tuple(sorted(values, key=lambda item: item.expert_id))


def _completion(profile: ExpertProfileV1) -> CalibrationCompletionV1:
    values = {
        "expert_id": profile.expert_id,
        "role": profile.role,
        "annotation_guide_sha256": GUIDE_SHA,
        "calibration_set_sha256": CALIBRATION_SET_SHA,
        "raw_answers_sha256": canonical_sha256(
            {"expert_id": profile.expert_id, "kind": "sealed-calibration-answers"}
        ),
        "calibration_result_sha256": canonical_sha256(
            {"expert_id": profile.expert_id, "kind": "calibration-score"}
        ),
        "completed_at": "2026-08-09T10:00:00+08:00",
    }
    return _identified(
        CalibrationCompletionV1,
        id_field="completion_id",
        sha_field="completion_sha256",
        prefix="calibration-completion",
        values=values,
    )


def _registry(
    *,
    split: BenchmarkSplitManifestV1 | None = None,
    include_substitute: bool = False,
    recused: tuple[str, str] | None = None,
    assignments_use_substitute: bool = False,
) -> ExpertStudyRegistryV1:
    split = split or _split()
    profiles = _profiles(include_substitute=include_substitute)
    completions = tuple(_completion(profile) for profile in profiles)
    conflicts: list[CaseConflictAssessmentV1] = []
    assignments: list[CaseExpertAssignmentV1] = []
    for case in split.cases:
        reviewers = (
            ("reviewer-b", "reviewer-c")
            if assignments_use_substitute
            else ("reviewer-a", "reviewer-b")
        )
        assignments.append(
            CaseExpertAssignmentV1(
                case_id=case.case_id,
                case_sha256=case.case_sha256,
                reviewer_ids=reviewers,
                adjudicator_id="adjudicator-a",
            )
        )
        for profile in profiles:
            is_recused = recused == (case.case_id, profile.expert_id)
            conflicts.append(
                CaseConflictAssessmentV1(
                    case_id=case.case_id,
                    case_sha256=case.case_sha256,
                    expert_id=profile.expert_id,
                    status=(ConflictStatus.RECUSE if is_recused else ConflictStatus.CLEAR),
                    reason_code=(
                        ConflictReasonCode.AUTHOR_OR_RECENT_COLLABORATOR
                        if is_recused
                        else ConflictReasonCode.NO_CONFLICT
                    ),
                    disclosure_sha256=canonical_sha256(
                        {"case_id": case.case_id, "expert_id": profile.expert_id}
                    ),
                    assessed_at="2026-08-09T09:00:00+08:00",
                )
            )
    values = {
        "split_manifest_id": split.manifest_id,
        "split_manifest_sha256": split.manifest_sha256,
        "annotation_guide_sha256": GUIDE_SHA,
        "calibration_set_sha256": CALIBRATION_SET_SHA,
        "profiles": profiles,
        "calibration_completions": completions,
        "conflict_assessments": tuple(
            sorted(conflicts, key=lambda item: (item.case_id, item.expert_id))
        ),
        "assignments": tuple(sorted(assignments, key=lambda item: item.case_id)),
        "registered_at": "2026-08-09T11:00:00+08:00",
    }
    return _identified(
        ExpertStudyRegistryV1,
        id_field="registry_id",
        sha_field="registry_sha256",
        prefix="expert-study-registry",
        values=values,
    )


def _readdress_registry(
    registry: ExpertStudyRegistryV1, **updates: object
) -> ExpertStudyRegistryV1:
    values = registry.model_dump(
        mode="python", exclude={"registry_id", "registry_sha256"}
    )
    values.update(updates)
    digest = canonical_sha256(values)
    return ExpertStudyRegistryV1.model_validate(
        {
            **values,
            "registry_sha256": digest,
            "registry_id": deterministic_id(
                "expert-study-registry", {"registry_sha256": digest}
            ),
        }
    )


def test_expert_registry_is_content_addressed_and_exactly_covers_pilot() -> None:
    split = _split()
    registry = _registry(split=split)

    assert len(registry.assignments) == 30
    assert len(registry.conflict_assessments) == 90
    assert_expert_registry_covers_split(registry=registry, split_manifest=split)


def test_registry_rejects_calibration_guide_drift() -> None:
    registry = _registry()
    completion = registry.calibration_completions[0]
    altered_values = completion.model_dump(
        mode="python", exclude={"completion_id", "completion_sha256"}
    )
    altered_values["annotation_guide_sha256"] = "c" * 64
    altered = _identified(
        CalibrationCompletionV1,
        id_field="completion_id",
        sha_field="completion_sha256",
        prefix="calibration-completion",
        values=altered_values,
    )

    with pytest.raises(ValidationError, match="different guide or set"):
        _readdress_registry(
            registry,
            calibration_completions=(altered, *registry.calibration_completions[1:]),
        )


def test_registry_rejects_calibration_or_conflict_after_registry_seal() -> None:
    registry = _registry()
    completion = registry.calibration_completions[0]
    completion_values = completion.model_dump(
        mode="python", exclude={"completion_id", "completion_sha256"}
    )
    completion_values["completed_at"] = "2026-08-09T12:00:00+08:00"
    future_completion = _identified(
        CalibrationCompletionV1,
        id_field="completion_id",
        sha_field="completion_sha256",
        prefix="calibration-completion",
        values=completion_values,
    )
    with pytest.raises(ValidationError, match="follows registry seal"):
        _readdress_registry(
            registry,
            calibration_completions=(
                future_completion,
                *registry.calibration_completions[1:],
            ),
        )

    future_conflict = registry.conflict_assessments[0].model_copy(
        update={"assessed_at": "2026-08-09T12:00:00+08:00"}
    )
    with pytest.raises(ValidationError, match="conflict assessment follows"):
        _readdress_registry(
            registry,
            conflict_assessments=(
                future_conflict,
                *registry.conflict_assessments[1:],
            ),
        )


def test_registry_rejects_missing_case_expert_conflict_assessment() -> None:
    registry = _registry()
    first = registry.conflict_assessments[0].model_copy(
        update={"expert_id": "expert-unknown"}
    )

    with pytest.raises(ValidationError, match="exactly cover every case x expert"):
        _readdress_registry(
            registry,
            conflict_assessments=(first, *registry.conflict_assessments[1:]),
        )


def test_assigned_recused_expert_blocks_when_no_substitute_exists() -> None:
    split = _split()

    with pytest.raises(ValidationError, match="recused expert cannot be assigned"):
        _registry(
            split=split,
            recused=(split.cases[0].case_id, "reviewer-a"),
        )


def test_prequalified_substitute_can_replace_recused_reviewer() -> None:
    split = _split()
    registry = _registry(
        split=split,
        include_substitute=True,
        recused=(split.cases[0].case_id, "reviewer-a"),
        assignments_use_substitute=True,
    )

    assert registry.assignments[0].reviewer_ids == ("reviewer-b", "reviewer-c")
    assert_expert_registry_covers_split(registry=registry, split_manifest=split)


def test_split_identity_drift_is_rejected_at_closure_boundary() -> None:
    split = _split()
    registry = _registry(split=split)
    changed_split = _identified(
        BenchmarkSplitManifestV1,
        id_field="manifest_id",
        sha_field="manifest_sha256",
        prefix="split-manifest",
        values={
            "manifest_kind": SplitManifestKind.PILOT_R1,
            "split_seed": 20260810,
            "cases": split.cases,
            "ood_holdout_families": (),
        },
    )

    with pytest.raises(ValueError, match="different split manifest"):
        assert_expert_registry_covers_split(
            registry=registry, split_manifest=changed_split
        )


def test_forged_registry_content_address_is_rejected() -> None:
    registry = _registry()
    payload = registry.model_dump(mode="python", round_trip=True)
    payload["registered_at"] = "2026-08-09T11:01:00+08:00"

    with pytest.raises(ValidationError, match="SHA-256 does not match"):
        ExpertStudyRegistryV1.model_validate(payload)


GUIDE_VERSION_V2 = "annotation-guide-v0.2-test"


def _v2_algorithm(axis: LeakageAxis) -> StructureGroupingAlgorithmV2:
    return _identified(
        StructureGroupingAlgorithmV2,
        id_field="algorithm_id",
        sha_field="algorithm_sha256",
        prefix="structure-group-algorithm",
        values={
            "axis": axis,
            "algorithm_name": f"expert-test-{axis.value.casefold()}",
            "algorithm_version": "v2-test",
            "implementation_sha256": canonical_sha256(
                {"implementation": axis.value}
            ),
            "configuration_sha256": canonical_sha256(
                {"configuration": axis.value}
            ),
        },
    )


def _lineage_evidence_ref_v3(
    record: SourceRecordRefV1,
) -> MechanismLineageEvidenceRefV3:
    assert record.raw_sha256 is not None
    return MechanismLineageEvidenceRefV3(
        source_id=record.source_id,
        source_record_id=record.source_record_id,
        source_record_raw_sha256=record.raw_sha256,
    )


def _global_lineage_registry_v3(
    *, taxonomy_version: str = "flatband-global-lineage-test-v3"
) -> MechanismLineageRegistryV3:
    definitions: list[MechanismLineageDefinitionV3] = []
    for index in range(4):
        definitions.append(
            build_mechanism_lineage_definition_v3(
                broad_mechanism_family=MechanismFamily.OTHER_OR_UNKNOWN,
                source_mechanism=f"Calibration source mechanism {index}",
                shared_invariant=f"Calibration shared invariant {index}",
                transfer_route_family=f"Calibration transfer route {index}",
                taxonomy_evidence_refs=(
                    MechanismLineageEvidenceRefV3(
                        source_id="crossref",
                        source_record_id=f"calibration-taxonomy-{index:02d}",
                        source_record_raw_sha256=canonical_sha256(
                            {"calibration-taxonomy": index}
                        ),
                    ),
                ),
            )
        )
    for index, broad in enumerate(tuple(MechanismFamily)):
        definitions.append(
            build_mechanism_lineage_definition_v3(
                broad_mechanism_family=broad,
                source_mechanism=f"Benchmark source mechanism {index}",
                shared_invariant=f"Benchmark shared invariant {index}",
                transfer_route_family=f"Benchmark transfer route {index}",
                taxonomy_evidence_refs=(
                    MechanismLineageEvidenceRefV3(
                        source_id="crossref",
                        source_record_id=f"benchmark-taxonomy-{index:02d}",
                        source_record_raw_sha256=canonical_sha256(
                            {"benchmark-taxonomy": index}
                        ),
                    ),
                ),
            )
        )
    return build_mechanism_lineage_registry_v3(
        taxonomy_version=taxonomy_version,
        curation_policy_sha256=canonical_sha256("lineage-curation-policy"),
        evidence_review_manifest_sha256=canonical_sha256(
            "lineage-evidence-review"
        ),
        curator_roster_sha256=canonical_sha256("lineage-curator-roster"),
        definitions=tuple(definitions),
        sealed_at="2026-08-09T07:10:00+08:00",
    )


def _calibration_lineages_v3(
    registry: MechanismLineageRegistryV3,
) -> tuple[MechanismLineageDefinitionV3, ...]:
    return tuple(
        sorted(
            (
                item
                for item in registry.definitions
                if item.source_mechanism.startswith("Calibration source mechanism")
            ),
            key=lambda item: item.source_mechanism,
        )
    )


def _benchmark_lineages_v3(
    registry: MechanismLineageRegistryV3,
) -> tuple[MechanismLineageDefinitionV3, ...]:
    return tuple(
        sorted(
            (
                item
                for item in registry.definitions
                if item.source_mechanism.startswith("Benchmark source mechanism")
            ),
            key=lambda item: int(item.source_mechanism.rsplit(" ", 1)[1]),
        )
    )


def _v2_calibration_cases(
    algorithms: tuple[StructureGroupingAlgorithmV2, ...],
    registry: MechanismLineageRegistryV3,
) -> tuple[
    tuple[FlatBandBenchmarkCaseV1, ...],
    dict[tuple[LeakageAxis, str], str],
    dict[str, MechanismLineageDefinitionV3],
]:
    formulas = ("XeF2", "KrF2", "NeF2", "ArF2")
    lineages = _calibration_lineages_v3(registry)
    cases: list[FlatBandBenchmarkCaseV1] = []
    group_keys: dict[tuple[LeakageAxis, str], str] = {}
    lineage_by_case: dict[str, MechanismLineageDefinitionV3] = {}
    for index, formula in enumerate(formulas):
        source = SourceRecordRefV1(
            source_id="crossref",
            source_record_id=f"calibration-work-{index:02d}",
            canonical_url=f"https://doi.org/10.9999/calibration-{index:02d}",
            source_version="2026-08-09",
            license_expression="CC0-1.0",
            accessed_at="2026-08-09T07:00:00+08:00",
            raw_sha256=canonical_sha256({"calibration-source": index}),
            public_redistribution_allowed=True,
        )
        structure_groups = tuple(
            (algorithm, f"calibration-{algorithm.axis.value.casefold()}-{index:02d}")
            for algorithm in algorithms
        )
        groups = derive_leakage_group_ids_v3(
            formula=formula,
            primary_mechanism_stratum=MechanismFamily.OTHER_OR_UNKNOWN,
            source_records=(source,),
            structure_groups=structure_groups,
            mechanism_lineage_registry=registry,
            mechanism_lineage_id=lineages[index].lineage_id,
        )
        values = {
            "parent_label": f"calibration parent {index}",
            "formula": formula,
            "structure_sha256": canonical_sha256(
                {"calibration-structure": index}
            ),
            "source_records": (source,),
            "target_class": (
                TargetBandClass.FB100 if index < 2 else TargetBandClass.NB300
            ),
            "dimensionality": (
                Dimensionality.TWO_D if index % 2 == 0 else Dimensionality.THREE_D
            ),
            "frozen_request": f"Calibrate bounded packet judgment {index}",
            "frozen_requirement_sha256": canonical_sha256(
                {"calibration-requirement": index}
            ),
            "hard_constraints": ("preserve dimensionality",),
            "soft_preferences": (),
            "forbidden_transformations": ("delete_parent_sites",),
            "seed_evidence": (),
            "primary_mechanism_stratum": MechanismFamily.OTHER_OR_UNKNOWN,
            "leakage_group_ids": groups,
            "public_release_allowed": True,
        }
        case = _identified(
            FlatBandBenchmarkCaseV1,
            id_field="case_id",
            sha_field="case_sha256",
            prefix="flatband-case",
            values=values,
        )
        cases.append(case)
        lineage_by_case[case.case_id] = lineages[index]
        for algorithm, key in structure_groups:
            group_keys[(algorithm.axis, case.case_id)] = key
    return (
        tuple(sorted(cases, key=lambda item: item.case_id)),
        group_keys,
        lineage_by_case,
    )


def _v2_grouping_artifacts(
    *,
    cases: tuple[FlatBandBenchmarkCaseV1, ...],
    algorithms: tuple[StructureGroupingAlgorithmV2, ...],
    group_keys: dict[tuple[LeakageAxis, str], str],
) -> tuple[
    tuple[StructureGroupingRunV2, ...],
    tuple[StructureGroupingAssignmentV2, ...],
]:
    universe_sha256 = structure_grouping_case_universe_sha256_v2(cases)
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
                "input_case_universe_sha256": universe_sha256,
                "runtime_environment_sha256": canonical_sha256(
                    {"calibration-runtime": algorithm.axis.value}
                ),
                "started_at": "2026-08-09T07:30:00+08:00",
                "completed_at": "2026-08-09T07:31:00+08:00",
            },
        )
        for algorithm in algorithms
    )
    algorithm_by_axis = {item.axis: item for item in algorithms}
    run_by_axis = {item.axis: item for item in runs}
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
                "canonical_group_key": group_keys[(axis, case.case_id)],
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


DERIVATIVE_SCREENING_CRITERIA = (
    "check-composition-ratio",
    "check-intercalant-sites",
    "check-ordered-defect-pattern",
    "check-vacancy-parent",
)


def _derivative_screening_person(
    index: int,
) -> MechanismLineageCuratorDeclarationV3:
    return MechanismLineageCuratorDeclarationV3(
        curator_id=f"calibration-derivative-human-{index}",
        opaque_natural_person_ref=f"opaque-calibration-person-{index}",
        natural_person_commitment_sha256=canonical_sha256(
            ("calibration-derivative-person", index)
        ),
        identity_evidence_uri=(
            f"artifact://private/calibration-derivative/person-{index}"
        ),
        identity_evidence_sha256=canonical_sha256(
            ("calibration-derivative-identity", index)
        ),
        institutional_unit=f"independent-calibration-unit-{index}",
        conflict_declaration="No conflict declared for calibration screening.",
    )


def _calibration_derivative_screening_release(
    cases: tuple[FlatBandBenchmarkCaseV1, ...],
) -> DerivativeScreeningReleaseV3:
    policy = build_derivative_screening_policy_v3(
        review_criteria=DERIVATIVE_SCREENING_CRITERIA,
        sealed_at="2026-08-09T07:41:00+08:00",
    )
    roster = build_derivative_screening_reviewer_roster_v3(
        policy=policy,
        reviewers=(
            _derivative_screening_person(1),
            _derivative_screening_person(2),
        ),
        adjudicator=_derivative_screening_person(3),
        independence_review=(
            "Three injective private natural-person bindings were verified."
        ),
        sealed_at="2026-08-09T07:42:00+08:00",
    )
    assignments = tuple(
        build_derivative_screening_assignment_v3(
            policy=policy,
            roster=roster,
            case=case,
            evidence_refs=(
                build_derivative_source_evidence_ref_v3(
                    case=case,
                    source_id=case.source_records[0].source_id,
                    source_record_id=case.source_records[0].source_record_id,
                ),
            ),
            assigned_at="2026-08-09T07:43:00+08:00",
        )
        for case in cases
    )
    reviews = tuple(
        build_derivative_screening_raw_review_v3(
            policy=policy,
            roster=roster,
            assignment=assignment,
            reviewer_id=reviewer.curator_id,
            derivative_class=DerivativeClass.NOT,
            criterion_findings=DERIVATIVE_SCREENING_CRITERIA,
            rationale=(
                "The human reviewer found no registered derivative class in the supplied sources."
            ),
            reviewed_at=(
                "2026-08-09T07:44:00+08:00"
                if reviewer == roster.reviewers[0]
                else "2026-08-09T07:45:00+08:00"
            ),
        )
        for assignment in assignments
        for reviewer in roster.reviewers
    )
    return build_derivative_screening_release_v3(
        policy=policy,
        roster=roster,
        cases=cases,
        assignments=assignments,
        raw_reviews=reviews,
        assembled_at="2026-08-09T07:46:00+08:00",
    )


def _calibration_manifest_v2(
    *,
    registry: MechanismLineageRegistryV3 | None = None,
    algorithms: tuple[StructureGroupingAlgorithmV2, ...] | None = None,
) -> CalibrationSetManifestV2:
    registry = registry or _global_lineage_registry_v3()
    del algorithms
    formulas = ("XeF2", "KrF2", "NeF2", "ArF2")
    lineages = _calibration_lineages_v3(registry)
    inputs = []
    artifacts = []
    for index, formula in enumerate(formulas):
        label = f"calibration-{index:02d}"
        cod = SourceRecordRefV1(
            source_id="cod",
            source_record_id=f"cod-{label}",
            canonical_url=f"https://www.crystallography.net/cod/{9900000 + index}.html",
            source_version="svn-2026-08-09",
            license_expression="CC0-1.0",
            accessed_at="2026-08-09T07:00:00+08:00",
            raw_sha256=canonical_sha256({"calibration-cod": index}),
            public_redistribution_allowed=True,
        )
        crossref = SourceRecordRefV1(
            source_id="crossref",
            source_record_id=f"calibration-work-{index:02d}",
            canonical_url=f"https://doi.org/10.9999/calibration-{index:02d}",
            source_version="2026-08-09",
            license_expression="CC0-1.0",
            accessed_at="2026-08-09T07:00:00+08:00",
            raw_sha256=canonical_sha256({"calibration-source": index}),
            public_redistribution_allowed=True,
        )
        materials_project = SourceRecordRefV1(
            source_id="materials_project_core",
            source_record_id=f"mp-{label}",
            canonical_url=f"https://materialsproject.org/materials/mp-{9900 + index}",
            source_version="api-2026-08-09",
            license_expression="CC-BY-4.0",
            accessed_at="2026-08-09T07:00:00+08:00",
            raw_sha256=canonical_sha256({"calibration-mp": index}),
            public_redistribution_allowed=True,
        )
        dimensionality = (
            StructureDimensionalityV2.TWO_D
            if index % 2 == 0
            else StructureDimensionalityV2.THREE_D
        )
        composition = Composition(formula)
        species = tuple(
            symbol
            for symbol, amount in sorted(composition.get_el_amt_dict().items())
            for _ in range(int(amount))
        )
        coordinates = tuple(
            (
                (0.117 + 0.191 * site + 0.023 * index) % 1.0,
                (0.229 + 0.167 * site + 0.031 * index) % 1.0,
                (
                    0.48 + 0.013 * site
                    if dimensionality is StructureDimensionalityV2.TWO_D
                    else (0.151 + 0.271 * site + 0.019 * index) % 1.0
                ),
            )
            for site in range(len(species))
        )
        lattice = Lattice.from_parameters(
            3.4 + 0.2 * index,
            4.1 + 0.1 * index,
            21.0 if dimensionality is StructureDimensionalityV2.TWO_D else 5.7,
            81.0,
            87.0,
            73.0,
        )
        structure = Structure(lattice, species, coordinates, to_unit_cell=True)
        raw = build_raw_structure_artifact_v2(
            private_artifact_uri=f"artifact://private/calibration/{label}",
            source_id=cod.source_id,
            source_record_id=cod.source_record_id,
            source_record_raw_sha256=cod.raw_sha256,
            artifact_format="PYMATGEN_JSON",
            raw_bytes=json.dumps(
                structure.as_dict(), sort_keys=True, separators=(",", ":")
            ).encode("utf-8"),
        )
        artifact = normalize_raw_structure_artifact_v2(
            raw_artifact=raw,
            dimensionality=dimensionality,
        )
        evidence = artifact.provenance.aperiodic_axis_evidence
        request = f"Calibrate bounded packet judgment {index}"
        inputs.append(
            build_structure_grouping_case_input_v2(
                preimage=PreGroupCandidatePreimageV2(
                    study_phase="CALIBRATION",
                    slot_index=index,
                    priority=0,
                    parent_label=f"calibration parent {index}",
                    formula=formula,
                    structure_artifact_id=artifact.artifact_id,
                    structure_artifact_sha256=artifact.artifact_sha256,
                    structure_sha256=artifact.structure_sha256,
                    source_records=(cod, crossref, materials_project),
                    target_class=(
                        TargetBandClass.FB100
                        if index < 2
                        else TargetBandClass.NB300
                    ),
                    dimensionality=dimensionality,
                    aperiodic_axis=evidence.chosen_axis,
                    aperiodic_axis_evidence=evidence,
                    frozen_request=request,
                    frozen_requirement_sha256=canonical_sha256(
                        {"calibration-requirement": index}
                    ),
                    hard_constraints=("preserve dimensionality",),
                    forbidden_transformations=("delete_parent_sites",),
                    primary_mechanism_stratum=MechanismFamily.OTHER_OR_UNKNOWN,
                    public_release_allowed=True,
                )
            )
        )
        artifacts.append(artifact)
    input_manifest = seal_structure_grouping_input_manifest_v2(
        case_inputs=tuple(inputs),
        structure_artifacts=tuple(artifacts),
        sealed_at="2026-08-09T07:20:00+08:00",
        sealed_monotonic_ns=1,
    )
    computation = run_structure_grouping_computation_v2(
        input_manifest=input_manifest,
        started_at="2026-08-09T07:21:00+08:00",
        completed_at="2026-08-09T07:22:00+08:00",
        started_monotonic_ns=2,
        completed_monotonic_ns=3,
        created_at="2026-08-09T07:23:00+08:00",
    )
    group_by_axis = {
        LeakageAxis.STRUCTURE_PROTOTYPE: {
            key: component.canonical_group_key
            for component in computation.prototype_components
            for key in component.candidate_keys
        },
        LeakageAxis.STRUCTURE_FINGERPRINT: {
            key: component.canonical_group_key
            for component in computation.fingerprint_components
            for key in component.candidate_keys
        },
    }
    cases = []
    lineage_by_case: dict[str, MechanismLineageDefinitionV3] = {}
    for case_input in input_manifest.case_inputs:
        preimage = case_input.preimage
        lineage = lineages[preimage.slot_index]
        groups = derive_leakage_group_ids_v3(
            formula=preimage.formula,
            primary_mechanism_stratum=preimage.primary_mechanism_stratum,
            source_records=preimage.source_records,
            structure_groups=tuple(
                (
                    algorithm,
                    group_by_axis[algorithm.axis][case_input.candidate_key],
                )
                for algorithm in computation.grouping_algorithms
            ),
            mechanism_lineage_registry=registry,
            mechanism_lineage_id=lineage.lineage_id,
        )
        case = _identified(
            FlatBandBenchmarkCaseV1,
            id_field="case_id",
            sha_field="case_sha256",
            prefix="flatband-case",
            values={
                "parent_label": preimage.parent_label,
                "formula": preimage.formula,
                "structure_sha256": preimage.structure_sha256,
                "source_records": preimage.source_records,
                "target_class": preimage.target_class,
                "dimensionality": Dimensionality(preimage.dimensionality.value),
                "frozen_request": preimage.frozen_request,
                "frozen_requirement_sha256": (
                    preimage.frozen_requirement_sha256
                ),
                "hard_constraints": preimage.hard_constraints,
                "soft_preferences": preimage.soft_preferences,
                "forbidden_transformations": preimage.forbidden_transformations,
                "seed_evidence": preimage.seed_evidence,
                "primary_mechanism_stratum": (
                    preimage.primary_mechanism_stratum
                ),
                "leakage_group_ids": groups,
                "public_release_allowed": True,
            },
        )
        cases.append(case)
        lineage_by_case[case.case_id] = lineage
    policies = tuple(
        build_case_source_policy_attestation_v2(
            case=case,
            source_id=record.source_id,
            source_record_id=record.source_record_id,
            usage_roles=(
                (SourceUseRole.CASE_SEED, SourceUseRole.STRUCTURE)
                if record.source_id == "cod"
                else (
                    (SourceUseRole.CASE_SEED,)
                    if record.source_id == "materials_project_core"
                    else (
                        SourceUseRole.IDENTIFIER_RESOLUTION,
                        SourceUseRole.LITERATURE_RETRIEVAL,
                    )
                )
            ),
            record_license_compatibility_sha256=canonical_sha256(
                (case.case_id, record.source_id, "license")
            ),
            record_provenance_token_sha256=canonical_sha256(
                (case.case_id, record.source_id, "provenance")
            ),
            public_fields_release_allowed=True,
            structure_payload_release_allowed=record.source_id == "cod",
        )
        for case in cases
        for record in case.source_records
    )
    structure_release = finalize_structure_grouping_release_v2(
        computation=computation,
        final_cases=tuple(cases),
        source_policy_attestations=policies,
        final_cases_declared_at="2026-08-09T07:30:00+08:00",
        created_at="2026-08-09T07:31:00+08:00",
    )
    lineage_assignments = tuple(
        build_mechanism_lineage_assignment_v3(
            registry=registry,
            case=case,
            lineage_id=lineage_by_case[case.case_id].lineage_id,
            case_evidence_refs=tuple(
                _lineage_evidence_ref_v3(record)
                for record in case.source_records
            ),
            assignment_basis_sha256=canonical_sha256(
                {"calibration-case": case.case_id, "kind": "lineage-assignment"}
            ),
            assigned_at="2026-08-09T07:40:00+08:00",
        )
        for case in cases
    )
    derivative_screening = _calibration_derivative_screening_release(
        tuple(cases)
    )
    return build_calibration_set_manifest_v2(
        cases=tuple(reversed(cases)),
        annotation_guide_version=GUIDE_VERSION_V2,
        annotation_guide_sha256=GUIDE_SHA,
        case_freeze_policy_sha256=canonical_sha256(
            {"calibration-freeze-policy": "v2"}
        ),
        structure_grouping_release=structure_release,
        derivative_screening_release=derivative_screening,
        source_policy_attestations=policies,
        grouping_algorithms=tuple(
            reversed(structure_release.grouping_algorithms)
        ),
        grouping_runs=tuple(reversed(structure_release.grouping_runs)),
        grouping_assignments=tuple(
            reversed(structure_release.grouping_assignments)
        ),
        mechanism_lineage_registry=registry,
        mechanism_lineage_assignments=tuple(reversed(lineage_assignments)),
        frozen_at="2026-08-09T08:00:00+08:00",
    )


BENCHMARK_FORMULAS_V3 = (
    "LiF",
    "NaCl",
    "MgO",
    "BN",
    "SiC",
    "AlN",
    "GaAs",
    "ZnO",
    "FeS",
    "CoO",
)


def _benchmark_v3_fixture(
    *,
    registry: MechanismLineageRegistryV3,
    algorithms: tuple[StructureGroupingAlgorithmV2, ...],
    calibration: CalibrationSetManifestV2 | None = None,
    overlap: str | None = None,
) -> dict[str, object]:
    if overlap not in {
        None,
        "case",
        "composition",
        "source",
        "prototype",
        "fingerprint",
        "lineage",
    }:
        raise AssertionError(f"unsupported overlap fixture: {overlap}")
    if overlap is not None and calibration is None:
        raise AssertionError("overlap fixture requires a calibration manifest")

    lineages = _benchmark_lineages_v3(registry)
    calibration_case = (
        None
        if calibration is None
        else next(
            item
            for item in calibration.cases
            if item.target_class is TargetBandClass.FB100
            and item.dimensionality is Dimensionality.TWO_D
        )
    )
    calibration_lineage = None
    calibration_structure_keys: dict[LeakageAxis, str] = {}
    if calibration is not None:
        calibration_assignment = next(
            item
            for item in calibration.mechanism_lineage_assignments
            if item.case_id == calibration_case.case_id
        )
        calibration_lineage = next(
            item
            for item in registry.definitions
            if item.lineage_id == calibration_assignment.lineage_id
        )
        calibration_structure_keys = {
            item.axis: item.canonical_group_key
            for item in calibration.grouping_assignments
            if item.case_id == calibration_case.case_id
        }

    sources = tuple(
        SourceRecordRefV1(
            source_id="crossref",
            source_record_id=f"benchmark-work-{index:02d}",
            canonical_url=f"https://doi.org/10.8888/benchmark-{index:02d}",
            source_version="2026-08-09",
            license_expression="CC0-1.0",
            accessed_at="2026-08-09T07:00:00+08:00",
            raw_sha256=canonical_sha256({"benchmark-source": index}),
            public_redistribution_allowed=True,
        )
        for index in range(10)
    )
    lineage_overlap_component = next(
        index
        for index, item in enumerate(lineages)
        if item.broad_mechanism_family is MechanismFamily.OTHER_OR_UNKNOWN
    )
    lineage_overlap_index = lineage_overlap_component * 3
    cases: list[FlatBandBenchmarkCaseV1] = []
    group_keys: dict[tuple[LeakageAxis, str], str] = {}
    lineage_by_case: dict[str, MechanismLineageDefinitionV3] = {}
    for index in range(30):
        component = index // 3
        if overlap == "case" and index == 0:
            assert calibration_case is not None
            case = calibration_case
            cases.append(case)
            assert calibration_lineage is not None
            lineage_by_case[case.case_id] = calibration_lineage
            for algorithm in algorithms:
                group_keys[(algorithm.axis, case.case_id)] = (
                    calibration_structure_keys[algorithm.axis]
                )
            continue

        lineage = lineages[component]
        if overlap == "lineage" and index == lineage_overlap_index:
            assert calibration_lineage is not None
            lineage = calibration_lineage
        source = sources[component]
        if overlap == "source" and index == 0:
            assert calibration_case is not None
            source = calibration_case.source_records[0]
        formula = BENCHMARK_FORMULAS_V3[component]
        if overlap == "composition" and index == 0:
            assert calibration_case is not None
            formula = calibration_case.formula
        structure_groups: list[tuple[StructureGroupingAlgorithmV2, str]] = []
        for algorithm in algorithms:
            key = f"benchmark-{algorithm.axis.value.casefold()}-{component:02d}"
            if (
                overlap == "prototype"
                and index == 0
                and algorithm.axis is LeakageAxis.STRUCTURE_PROTOTYPE
            ) or (
                overlap == "fingerprint"
                and index == 0
                and algorithm.axis is LeakageAxis.STRUCTURE_FINGERPRINT
            ):
                key = calibration_structure_keys[algorithm.axis]
            structure_groups.append((algorithm, key))
        groups = derive_leakage_group_ids_v3(
            formula=formula,
            primary_mechanism_stratum=lineage.broad_mechanism_family,
            source_records=(source,),
            structure_groups=tuple(structure_groups),
            mechanism_lineage_registry=registry,
            mechanism_lineage_id=lineage.lineage_id,
        )
        case = _identified(
            FlatBandBenchmarkCaseV1,
            id_field="case_id",
            sha_field="case_sha256",
            prefix="flatband-case",
            values={
                "parent_label": f"benchmark parent {index}",
                "formula": formula,
                "structure_sha256": canonical_sha256(
                    {"benchmark-structure": index}
                ),
                "source_records": (source,),
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
                "frozen_request": f"Evaluate benchmark packet {index}",
                "frozen_requirement_sha256": canonical_sha256(
                    {"benchmark-requirement": index}
                ),
                "hard_constraints": (),
                "soft_preferences": (),
                "forbidden_transformations": (),
                "seed_evidence": (),
                "primary_mechanism_stratum": lineage.broad_mechanism_family,
                "leakage_group_ids": groups,
                "public_release_allowed": True,
            },
        )
        cases.append(case)
        lineage_by_case[case.case_id] = lineage
        for algorithm, key in structure_groups:
            group_keys[(algorithm.axis, case.case_id)] = key

    ordered_cases = tuple(sorted(cases, key=lambda item: item.case_id))
    split = _identified(
        BenchmarkSplitManifestV2,
        id_field="manifest_id",
        sha_field="manifest_sha256",
        prefix="split-manifest-v2",
        values={
            "manifest_kind": SplitManifestKind.PILOT_R1,
            "split_seed": 20260809,
            "cases": tuple(
                SplitCaseRefV2(
                    case_id=case.case_id,
                    case_sha256=case.case_sha256,
                    split=BenchmarkSplit.PILOT_R1,
                    target_class=case.target_class,
                    dimensionality=case.dimensionality,
                    primary_mechanism_stratum=case.primary_mechanism_stratum,
                    independence_group_ids=case.leakage_group_ids,
                )
                for case in ordered_cases
            ),
            "ood_holdout_families": (),
        },
    )
    runs, structure_assignments = _v2_grouping_artifacts(
        cases=ordered_cases,
        algorithms=algorithms,
        group_keys=group_keys,
    )
    lineage_assignments = tuple(
        build_mechanism_lineage_assignment_v3(
            registry=registry,
            case=case,
            lineage_id=lineage_by_case[case.case_id].lineage_id,
            case_evidence_refs=tuple(
                _lineage_evidence_ref_v3(record)
                for record in case.source_records
            ),
            assignment_basis_sha256=canonical_sha256(
                {"benchmark-case": case.case_id, "kind": "lineage-assignment"}
            ),
            assigned_at="2026-08-09T07:45:00+08:00",
        )
        for case in ordered_cases
    )
    release = build_leakage_component_release_v3(
        cases=ordered_cases,
        split_manifest=split,
        grouping_algorithms=algorithms,
        grouping_runs=runs,
        grouping_assignments=structure_assignments,
        mechanism_lineage_registry=registry,
        mechanism_lineage_assignments=lineage_assignments,
        created_at="2026-08-09T09:00:00+08:00",
    )
    return {
        "cases": ordered_cases,
        "split": split,
        "release": release,
        "lineage_assignments": lineage_assignments,
    }


def _disjoint_v3_fixture(
    *,
    overlap: str | None = None,
    registry: MechanismLineageRegistryV3 | None = None,
) -> dict[str, object]:
    registry = registry or _global_lineage_registry_v3()
    algorithms = tuple(
        _v2_algorithm(axis)
        for axis in sorted(
            (
                LeakageAxis.STRUCTURE_FINGERPRINT,
                LeakageAxis.STRUCTURE_PROTOTYPE,
            ),
            key=lambda item: item.value,
        )
    )
    calibration = _calibration_manifest_v2(
        registry=registry,
        algorithms=algorithms,
    )
    algorithms = calibration.grouping_algorithms
    benchmark = _benchmark_v3_fixture(
        registry=registry,
        algorithms=algorithms,
        calibration=calibration,
        overlap=overlap,
    )
    return {
        "registry": registry,
        "calibration": calibration,
        **benchmark,
    }


def _private_identity_v2() -> PrivateExpertIdentityCustodianAttestationV2:
    specifications = (
        ("adjudicator-a", ExpertRole.ADJUDICATOR, "subject-01"),
        ("reviewer-a", ExpertRole.REVIEWER, "subject-02"),
        ("reviewer-b", ExpertRole.REVIEWER, "subject-03"),
        ("reviewer-c", ExpertRole.REVIEWER, "subject-04"),
    )
    bindings = tuple(
        PrivateNaturalPersonBindingV2(
            expert_id=expert_id,
            role=role,
            opaque_natural_person_subject_ref=subject_ref,
            natural_person_commitment_sha256=canonical_sha256(
                {"stable-natural-person": subject_ref}
            ),
            identity_evidence_artifact_uri=(
                f"artifact://private/expert-identity/{subject_ref}"
            ),
            identity_evidence_sha256=canonical_sha256(
                {"identity-evidence": subject_ref}
            ),
        )
        for expert_id, role, subject_ref in specifications
    )
    return build_private_expert_identity_attestation_v2(
        custodian_id="identity-custodian-a",
        custodian_policy_sha256=canonical_sha256(
            {"identity-custodian-policy": "v2"}
        ),
        bindings=tuple(reversed(bindings)),
        attested_at="2026-08-09T08:05:00+08:00",
    )


def _v2_registry_fixture(
    *,
    split: BenchmarkSplitManifestV2 | None = None,
    calibration: CalibrationSetManifestV2 | None = None,
) -> dict[str, object]:
    split = split or _split_v2()
    calibration = calibration or _calibration_manifest_v2()
    private = _private_identity_v2()
    public = build_public_expert_identity_release_v2(
        private_attestation=private,
        released_at="2026-08-09T08:06:00+08:00",
    )
    completions = tuple(
        build_calibration_completion_v2(
            expert=expert,
            calibration_manifest=calibration,
            raw_answers_sha256=canonical_sha256(
                {"expert": expert.expert_id, "artifact": "raw-calibration"}
            ),
            calibration_result_sha256=canonical_sha256(
                {"expert": expert.expert_id, "artifact": "calibration-result"}
            ),
            completed_at="2026-08-09T08:20:00+08:00",
        )
        for expert in public.experts
    )
    conflicts = tuple(
        CaseConflictAssessmentV1(
            case_id=case.case_id,
            case_sha256=case.case_sha256,
            expert_id=expert.expert_id,
            status=ConflictStatus.CLEAR,
            reason_code=ConflictReasonCode.NO_CONFLICT,
            disclosure_sha256=canonical_sha256(
                {"case": case.case_id, "expert": expert.expert_id}
            ),
            assessed_at="2026-08-09T08:30:00+08:00",
        )
        for case in split.cases
        for expert in public.experts
    )
    assignments = tuple(
        CaseExpertAssignmentV1(
            case_id=case.case_id,
            case_sha256=case.case_sha256,
            reviewer_ids=("reviewer-a", "reviewer-b"),
            adjudicator_id="adjudicator-a",
        )
        for case in split.cases
    )
    registry = build_expert_study_registry_v2(
        split_manifest=split,
        calibration_manifest=calibration,
        public_identity_release=public,
        calibration_completions=completions,
        conflict_assessments=conflicts,
        assignments=assignments,
        registered_at="2026-08-09T08:40:00+08:00",
    )
    return {
        "split": split,
        "calibration": calibration,
        "private": private,
        "public": public,
        "completions": completions,
        "conflicts": conflicts,
        "assignments": assignments,
        "registry": registry,
    }


def _readdress_v2_registry(
    registry: ExpertStudyRegistryV2, **updates: object
) -> ExpertStudyRegistryV2:
    values = {
        field_name: getattr(registry, field_name)
        for field_name in type(registry).model_fields
        if field_name not in {"registry_id", "registry_sha256"}
    }
    values.update(updates)
    return _identified(
        ExpertStudyRegistryV2,
        id_field="registry_id",
        sha_field="registry_sha256",
        prefix="expert-study-registry-v2",
        values=values,
    )


def _budget_v2(*, frozen_at: str) -> BudgetManifestV1:
    policy = source_policy_values("crossref")
    source_budget = SourceBudgetV1(
        source_id="crossref",
        max_physical_requests=8,
        max_logical_queries=4,
        max_pages=4,
        max_records=100,
        max_response_bytes=100_000,
        max_unique_documents=50,
        max_cache_hits=2,
        **policy,
    )
    config = _identified(
        SystemConfigV1,
        id_field="config_id",
        sha_field="config_sha256",
        prefix="system-config",
        values={
            "system_id": ResearchSystemId.B0,
            "source_variant": SourceVariant.CROSSREF_ONLY,
            "source_budgets": (source_budget,),
            "query_plan_sha256": canonical_sha256("query-plan"),
            "record_projection_sha256": canonical_sha256("record-projection"),
            "ranking_policy_sha256": canonical_sha256("ranking-policy"),
            "cache_policy_sha256": canonical_sha256("cache-policy"),
            "cache_snapshot_sha256": canonical_sha256("cache-snapshot"),
            "baseline_tag_graph_sha256": canonical_sha256("baseline-tags"),
            "fusion_components": (),
        },
    )
    return _identified(
        BudgetManifestV1,
        id_field="budget_manifest_id",
        sha_field="budget_manifest_sha256",
        prefix="budget-manifest",
        values={
            "execution_matrix_id": "matrix-a",
            "execution_matrix_sha256": canonical_sha256("matrix-a"),
            "cell_id": "cell-a",
            "cell_sha256": canonical_sha256("cell-a"),
            "run_id": "run-a",
            "case_id": "case-a",
            "case_sha256": canonical_sha256("case-a"),
            "system_config": config,
            "git_commit": "1" * 40,
            "runtime_environment_sha256": canonical_sha256("runtime"),
            "analysis_environment_sha256": canonical_sha256("analysis"),
            "max_walltime_seconds": 300,
            "frozen_at": frozen_at,
        },
    )


def _rebuild_calibration_with_derivative_screening(
    manifest: CalibrationSetManifestV2,
    screening: DerivativeScreeningReleaseV3,
) -> CalibrationSetManifestV2:
    return build_calibration_set_manifest_v2(
        cases=manifest.cases,
        annotation_guide_version=manifest.annotation_guide_version,
        annotation_guide_sha256=manifest.annotation_guide_sha256,
        case_freeze_policy_sha256=manifest.case_freeze_policy_sha256,
        structure_grouping_release=manifest.structure_grouping_release,
        derivative_screening_release=screening,
        source_policy_attestations=manifest.source_policy_attestations,
        grouping_algorithms=manifest.grouping_algorithms,
        grouping_runs=manifest.grouping_runs,
        grouping_assignments=manifest.grouping_assignments,
        mechanism_lineage_registry=manifest.mechanism_lineage_registry,
        mechanism_lineage_assignments=manifest.mechanism_lineage_assignments,
        frozen_at=manifest.frozen_at,
    )


def test_v2_calibration_manifest_rejects_foreign_derivative_screening() -> None:
    manifest = _calibration_manifest_v2()
    foreign = _calibration_manifest_v2(
        registry=_global_lineage_registry_v3(
            taxonomy_version="foreign-calibration-screening-v3"
        )
    )

    with pytest.raises(
        ValidationError, match="differs from the exact case universe"
    ):
        _rebuild_calibration_with_derivative_screening(
            manifest,
            foreign.derivative_screening_release,
        )


def test_v2_calibration_manifest_rejects_late_derivative_screening() -> None:
    manifest = _calibration_manifest_v2()
    screening = manifest.derivative_screening_release
    late = build_derivative_screening_release_v3(
        policy=screening.policy,
        roster=screening.roster,
        cases=screening.cases,
        assignments=screening.assignments,
        raw_reviews=screening.raw_reviews,
        adjudications=screening.adjudications,
        assembled_at="2026-08-09T08:00:01+08:00",
    )

    with pytest.raises(
        ValidationError, match="not assembled before manifest seal"
    ):
        _rebuild_calibration_with_derivative_screening(manifest, late)


def test_v2_calibration_manifest_rejects_structure_release_at_equal_seal_time() -> None:
    manifest = _calibration_manifest_v2()
    structure = manifest.structure_grouping_release
    equal_time_release = finalize_structure_grouping_release_v2(
        computation=structure.computation,
        final_cases=structure.final_cases,
        source_policy_attestations=structure.source_policy_attestations,
        final_cases_declared_at=structure.final_cases_declared_at,
        created_at=manifest.frozen_at,
    )

    with pytest.raises(
        ValidationError,
        match="structure release was not created before manifest seal",
    ):
        build_calibration_set_manifest_v2(
            cases=manifest.cases,
            annotation_guide_version=manifest.annotation_guide_version,
            annotation_guide_sha256=manifest.annotation_guide_sha256,
            case_freeze_policy_sha256=manifest.case_freeze_policy_sha256,
            structure_grouping_release=equal_time_release,
            derivative_screening_release=manifest.derivative_screening_release,
            source_policy_attestations=equal_time_release.source_policy_attestations,
            grouping_algorithms=equal_time_release.grouping_algorithms,
            grouping_runs=equal_time_release.grouping_runs,
            grouping_assignments=equal_time_release.grouping_assignments,
            mechanism_lineage_registry=manifest.mechanism_lineage_registry,
            mechanism_lineage_assignments=manifest.mechanism_lineage_assignments,
            frozen_at=manifest.frozen_at,
        )


def test_v2_calibration_manifest_rejects_raw_derivative_adjudicated_to_not() -> None:
    manifest = _calibration_manifest_v2()
    screening = manifest.derivative_screening_release
    assignment = screening.assignments[0]
    target_reviews = tuple(
        item
        for item in screening.raw_reviews
        if item.case_id == assignment.case_id
    )
    assert len(target_reviews) == 2
    vacancy_review = build_derivative_screening_raw_review_v3(
        policy=screening.policy,
        roster=screening.roster,
        assignment=assignment,
        reviewer_id=target_reviews[1].reviewer_id,
        derivative_class=DerivativeClass.VACANCY,
        criterion_findings=DERIVATIVE_SCREENING_CRITERIA,
        rationale=(
            "The human reviewer identified a possible vacancy-parent derivative."
        ),
        reviewed_at=target_reviews[1].reviewed_at,
    )
    changed_reviews = tuple(
        vacancy_review if item.review_id == target_reviews[1].review_id else item
        for item in screening.raw_reviews
    )
    disagreement = tuple(
        item for item in changed_reviews if item.case_id == assignment.case_id
    )
    adjudication = build_derivative_screening_adjudication_v3(
        policy=screening.policy,
        roster=screening.roster,
        assignment=assignment,
        reviews=disagreement,
        final_derivative_class=DerivativeClass.NOT,
        rationale=(
            "The distinct adjudicator resolved the evidence to NOT while preserving "
            "the raw vacancy-risk observation."
        ),
        adjudicated_at="2026-08-09T07:45:30+08:00",
    )
    adjudicated_to_not = build_derivative_screening_release_v3(
        policy=screening.policy,
        roster=screening.roster,
        cases=screening.cases,
        assignments=screening.assignments,
        raw_reviews=changed_reviews,
        adjudications=(adjudication,),
        assembled_at="2026-08-09T07:47:00+08:00",
    )
    assert all(
        item.final_derivative_class is DerivativeClass.NOT
        for item in adjudicated_to_not.final_judgments
    )

    with pytest.raises(ValueError, match="contains derivative risk"):
        _rebuild_calibration_with_derivative_screening(
            manifest,
            adjudicated_to_not,
        )


def test_v2_calibration_manifest_replays_full_cases_sources_and_groups() -> None:
    manifest = _calibration_manifest_v2()

    assert len(manifest.cases) == 4
    assert len(manifest.source_records) == 12
    assert len(manifest.grouping_assignments) == 8
    assert {
        item.axis for item in manifest.memberships
    } == set(LeakageAxisV3)
    assert all(item.source_record.raw_sha256 for item in manifest.source_records)

    payload = manifest.model_dump(
        mode="python", exclude={"manifest_id", "manifest_sha256"}
    )
    payload["source_records"][0]["source_record_sha256"] = "f" * 64
    digest = canonical_sha256(payload)
    with pytest.raises(
        ValidationError, match="calibration source-record SHA-256 does not replay"
    ):
        CalibrationSetManifestV2.model_validate(
            {
                **payload,
                "manifest_sha256": digest,
                "manifest_id": deterministic_id(
                    "calibration-set-manifest-v2",
                    {"manifest_sha256": digest},
                ),
            }
        )


def test_v2_calibration_manifest_rejects_foreign_private_structure_release() -> None:
    manifest = _calibration_manifest_v2()
    foreign = _calibration_manifest_v2(
        registry=_global_lineage_registry_v3(
            taxonomy_version="foreign-calibration-lineage-v3"
        )
    )
    payload = manifest.model_dump(
        mode="python", exclude={"manifest_id", "manifest_sha256"}
    )
    payload["structure_grouping_release"] = (
        foreign.structure_grouping_release.model_dump(
            mode="python", round_trip=True
        )
    )
    digest = canonical_sha256(payload)
    with pytest.raises(
        ValidationError, match="differ from the private structure release projection"
    ):
        CalibrationSetManifestV2.model_validate(
            {
                **payload,
                "manifest_sha256": digest,
                "manifest_id": deterministic_id(
                    "calibration-set-manifest-v2",
                    {"manifest_sha256": digest},
                ),
            }
        )


def test_v2_calibration_manifest_rejects_compatibility_projection_drift() -> None:
    manifest = _calibration_manifest_v2()
    first = manifest.grouping_assignments[0]
    values = first.model_dump(
        mode="python", exclude={"assignment_id", "assignment_sha256"}
    )
    values["canonical_group_key"] = "foreign-compatibility-group"
    forged_assignment = _identified(
        StructureGroupingAssignmentV2,
        id_field="assignment_id",
        sha_field="assignment_sha256",
        prefix="structure-group-assignment",
        values=values,
    )
    payload = manifest.model_dump(
        mode="python", exclude={"manifest_id", "manifest_sha256"}
    )
    payload["grouping_assignments"] = (
        forged_assignment.model_dump(mode="python", round_trip=True),
        *payload["grouping_assignments"][1:],
    )
    digest = canonical_sha256(payload)
    with pytest.raises(
        ValidationError, match="differ from the private structure release projection"
    ):
        CalibrationSetManifestV2.model_validate(
            {
                **payload,
                "manifest_sha256": digest,
                "manifest_id": deterministic_id(
                    "calibration-set-manifest-v2",
                    {"manifest_sha256": digest},
                ),
            }
        )


def test_v2_calibration_rejects_structure_role_on_wrong_source_record() -> None:
    manifest = _calibration_manifest_v2()
    case = manifest.cases[0]
    changed = []
    for policy in manifest.source_policy_attestations:
        if policy.case_id != case.case_id:
            changed.append(policy)
            continue
        record = next(
            item
            for item in case.source_records
            if (
                item.source_id,
                item.source_record_id,
            ) == (policy.source_id, policy.source_record_id)
        )
        if record.source_id == "cod":
            roles = (SourceUseRole.CASE_SEED,)
            payload_allowed = False
        elif record.source_id == "materials_project_core":
            roles = (SourceUseRole.CASE_SEED, SourceUseRole.STRUCTURE)
            payload_allowed = True
        else:
            roles = (
                SourceUseRole.IDENTIFIER_RESOLUTION,
                SourceUseRole.LITERATURE_RETRIEVAL,
            )
            payload_allowed = False
        changed.append(
            build_case_source_policy_attestation_v2(
                case=case,
                source_id=record.source_id,
                source_record_id=record.source_record_id,
                usage_roles=roles,
                record_license_compatibility_sha256=canonical_sha256(
                    (case.case_id, record.source_id, "wrong-role-license")
                ),
                record_provenance_token_sha256=canonical_sha256(
                    (case.case_id, record.source_id, "wrong-role-provenance")
                ),
                public_fields_release_allowed=True,
                structure_payload_release_allowed=payload_allowed,
            )
        )
    with pytest.raises(
        (ValidationError, ValueError), match="raw structure provenance lacks a STRUCTURE"
    ):
        finalize_structure_grouping_release_v2(
            computation=manifest.structure_grouping_release.computation,
            final_cases=manifest.cases,
            source_policy_attestations=tuple(changed),
            final_cases_declared_at=(
                manifest.structure_grouping_release.final_cases_declared_at
            ),
            created_at=manifest.structure_grouping_release.created_at,
        )


def test_v2_calibration_manifest_rejects_missing_structure_run_output_and_fake_group() -> None:
    manifest = _calibration_manifest_v2()
    missing = manifest.model_dump(
        mode="python", exclude={"manifest_id", "manifest_sha256"}
    )
    missing["grouping_assignments"] = missing["grouping_assignments"][:-1]
    missing_digest = canonical_sha256(missing)
    with pytest.raises(
        ValidationError, match="differ from the private structure release projection"
    ):
        CalibrationSetManifestV2.model_validate(
            {
                **missing,
                "manifest_sha256": missing_digest,
                "manifest_id": deterministic_id(
                    "calibration-set-manifest-v2",
                    {"manifest_sha256": missing_digest},
                ),
            }
        )

    forged = manifest.model_dump(
        mode="python", exclude={"manifest_id", "manifest_sha256"}
    )
    first = forged["memberships"][0]
    replacement = next(
        item
        for item in forged["memberships"][1:]
        if item["axis"] == first["axis"]
    )
    first["group_id"] = replacement["group_id"]
    first["group_definition_sha256"] = replacement["group_definition_sha256"]
    forged_digest = canonical_sha256(forged)
    with pytest.raises(
        ValidationError, match="memberships do not replay from authoritative inputs"
    ):
        CalibrationSetManifestV2.model_validate(
            {
                **forged,
                "manifest_sha256": forged_digest,
                "manifest_id": deterministic_id(
                    "calibration-set-manifest-v2",
                    {"manifest_sha256": forged_digest},
                ),
            }
        )


@pytest.mark.parametrize(
    ("attack", "message"),
    (
        ("missing", "do not exactly cover full cases"),
        ("duplicate", "exactly one lineage assignment per case"),
        ("foreign_case", "do not exactly cover full cases"),
        ("foreign_evidence", "not a subset of assigned case sources"),
    ),
)
def test_v2_calibration_manifest_rejects_noncanonical_lineage_assignment_coverage(
    attack: str,
    message: str,
) -> None:
    manifest = _calibration_manifest_v2()
    payload = manifest.model_dump(
        mode="python", exclude={"manifest_id", "manifest_sha256"}
    )
    assignments = list(manifest.mechanism_lineage_assignments)
    if attack == "missing":
        assignments = assignments[:-1]
    elif attack == "duplicate":
        assignments.append(assignments[0])
    else:
        first = assignments[0]
        values = first.model_dump(
            mode="python", exclude={"assignment_id", "assignment_sha256"}
        )
        values["case_evidence_refs"] = first.case_evidence_refs
        if attack == "foreign_case":
            values.update(
                {
                    "case_id": "foreign-calibration-case",
                    "case_sha256": "f" * 64,
                }
            )
        else:
            values["case_evidence_refs"] = (
                MechanismLineageEvidenceRefV3(
                    source_id="crossref",
                    source_record_id="foreign-evidence-record",
                    source_record_raw_sha256="f" * 64,
                ),
            )
        assignments[0] = _identified(
            MechanismLineageAssignmentV3,
            id_field="assignment_id",
            sha_field="assignment_sha256",
            prefix="mechanism-lineage-assignment-v3",
            values=values,
        )
    payload["mechanism_lineage_assignments"] = tuple(
        item.model_dump(mode="python", round_trip=True) for item in assignments
    )
    digest = canonical_sha256(payload)
    with pytest.raises(ValidationError, match=message):
        CalibrationSetManifestV2.model_validate(
            {
                **payload,
                "manifest_sha256": digest,
                "manifest_id": deterministic_id(
                    "calibration-set-manifest-v2",
                    {"manifest_sha256": digest},
                ),
            }
        )


def test_v2_calibration_is_disjoint_from_replayed_split_v2_leakage_v3() -> None:
    fixture = _disjoint_v3_fixture()
    assert isinstance(fixture["release"], LeakageComponentReleaseV3)

    assert_calibration_disjoint_from_benchmarks_v2(
        calibration_manifest=fixture["calibration"],
        benchmark_case_universes=(fixture["cases"],),
        split_manifests=(fixture["split"],),
        leakage_releases=(fixture["release"],),
    )


@pytest.mark.parametrize(
    ("overlap", "message"),
    (
        ("case", "share a case ID"),
        ("composition", "share a canonical leakage family"),
        ("source", "share a source record"),
        ("prototype", "share a canonical leakage family"),
        ("fingerprint", "share a canonical leakage family"),
        ("lineage", "share a mechanism scientific preimage"),
    ),
)
def test_v2_calibration_disjointness_rejects_every_replayed_overlap_axis(
    overlap: str,
    message: str,
) -> None:
    fixture = _disjoint_v3_fixture(overlap=overlap)

    with pytest.raises(ValueError, match=message):
        assert_calibration_disjoint_from_benchmarks_v2(
            calibration_manifest=fixture["calibration"],
            benchmark_case_universes=(fixture["cases"],),
            split_manifests=(fixture["split"],),
            leakage_releases=(fixture["release"],),
        )


def test_v2_calibration_disjointness_rejects_foreign_global_lineage_registry() -> None:
    fixture = _disjoint_v3_fixture()
    calibration = fixture["calibration"]
    assert isinstance(calibration, CalibrationSetManifestV2)
    foreign_registry = _global_lineage_registry_v3(
        taxonomy_version="foreign-global-lineage-test-v3"
    )
    foreign_benchmark = _benchmark_v3_fixture(
        registry=foreign_registry,
        algorithms=calibration.grouping_algorithms,
    )

    with pytest.raises(ValueError, match="one global lineage registry"):
        assert_calibration_disjoint_from_benchmarks_v2(
            calibration_manifest=calibration,
            benchmark_case_universes=(foreign_benchmark["cases"],),
            split_manifests=(foreign_benchmark["split"],),
            leakage_releases=(foreign_benchmark["release"],),
        )


def test_legacy_leakage_v2_cannot_certify_formal_calibration_independence() -> None:
    with pytest.raises(ValueError, match="legacy leakage V2 cannot certify"):
        assert_legacy_v2_calibration_disjointness_unavailable(
            calibration_manifest=_calibration_manifest_v2(),
            leakage_releases=(),
        )


def test_formal_pilot_v2_rejects_legacy_release_chain_without_projection() -> None:
    scientific = _disjoint_v3_fixture()
    expert = _v2_registry_fixture(
        split=scientific["split"],
        calibration=scientific["calibration"],
    )
    common = {
        "registry": expert["registry"],
        "split_manifest": scientific["split"],
        "benchmark_cases": scientific["cases"],
        "private_identity_attestation": expert["private"],
        "public_identity_release": expert["public"],
        "calibration_manifest": scientific["calibration"],
        "leakage_release": scientific["release"],
    }

    with pytest.raises(ValueError, match="flatband-frozen-case-release-v2"):
        assert_formal_pilot_expert_closure_legacy_v2_upstream(
            **common,
            frozen_case_release={
                "schema_version": "flatband-frozen-case-release-v1"
            },
            pre_run_eligibility_release={
                "schema_version": "flatband-pre-run-eligibility-release-v1"
            },
            execution_release={
                "schema_version": "flatband-execution-release-v1"
            },
        )


def _formal_v2_positive_upstream() -> dict[str, object]:
    """Build one globally disjoint calibration/benchmark V2 upstream chain."""

    from tests.unit import test_flatband_research_cases as cases_fixture

    base = cases_fixture._formal_v2_study()
    calibration_registry = _global_lineage_registry_v3()
    calibration_definitions = tuple(
        item
        for item in calibration_registry.definitions
        if item.source_mechanism.startswith("Calibration source mechanism")
    )
    lineage_registry = build_mechanism_lineage_registry_v3(
        taxonomy_version="formal-positive-global-lineage-v3",
        curation_policy_sha256=canonical_sha256("formal-positive-lineage-policy"),
        evidence_review_manifest_sha256=canonical_sha256(
            "formal-positive-lineage-evidence"
        ),
        curator_roster_sha256=canonical_sha256("formal-positive-lineage-curators"),
        definitions=(
            *base.leakage.mechanism_lineage_registry.definitions,
            *calibration_definitions,
        ),
        sealed_at="2026-08-09T07:10:00+08:00",
    )
    old_case_by_id = {item.case.case_id: item.case for item in base.candidates}
    old_lineage_by_case = {
        item.case_id: item for item in base.leakage.mechanism_lineage_assignments
    }
    old_group_keys = {
        (item.axis, item.case_id): item.canonical_group_key
        for item in base.leakage.grouping_assignments
    }
    old_active_cases = tuple(
        old_case_by_id[item.case_id] for item in base.manifest.cases
    )
    new_by_old: dict[str, FlatBandBenchmarkCaseV1] = {}
    new_group_keys: dict[tuple[LeakageAxis, str], str] = {}
    for old_case in old_active_cases:
        structure_groups = tuple(
            (
                algorithm,
                old_group_keys[(algorithm.axis, old_case.case_id)],
            )
            for algorithm in base.leakage.grouping_algorithms
        )
        groups = derive_leakage_group_ids_v3(
            formula=old_case.formula,
            primary_mechanism_stratum=old_case.primary_mechanism_stratum,
            source_records=old_case.source_records,
            structure_groups=structure_groups,
            mechanism_lineage_registry=lineage_registry,
            mechanism_lineage_id=old_lineage_by_case[old_case.case_id].lineage_id,
        )
        values = old_case.model_dump(
            mode="python", exclude={"case_id", "case_sha256"}
        )
        values["source_records"] = old_case.source_records
        values["leakage_group_ids"] = groups
        new_case = _identified(
            FlatBandBenchmarkCaseV1,
            id_field="case_id",
            sha_field="case_sha256",
            prefix="flatband-case",
            values=values,
        )
        new_by_old[old_case.case_id] = new_case
        for algorithm, key in structure_groups:
            new_group_keys[(algorithm.axis, new_case.case_id)] = key
    cases = tuple(sorted(new_by_old.values(), key=lambda item: item.case_id))
    split = _identified(
        BenchmarkSplitManifestV2,
        id_field="manifest_id",
        sha_field="manifest_sha256",
        prefix="split-manifest-v2",
        values={
            "manifest_kind": SplitManifestKind.PILOT_R1,
            "split_seed": base.manifest.split_seed,
            "cases": tuple(
                sorted(
                    (
                        SplitCaseRefV2(
                            case_id=case.case_id,
                            case_sha256=case.case_sha256,
                            split=BenchmarkSplit.PILOT_R1,
                            target_class=case.target_class,
                            dimensionality=case.dimensionality,
                            primary_mechanism_stratum=(
                                case.primary_mechanism_stratum
                            ),
                            independence_group_ids=case.leakage_group_ids,
                        )
                        for case in cases
                    ),
                    key=lambda item: item.case_id,
                )
            ),
            "ood_holdout_families": (),
        },
    )
    grouping_runs, grouping_assignments = (
        cases_fixture._v2_grouping_artifacts_local(
            cases=cases,
            algorithms=base.leakage.grouping_algorithms,
            keys=new_group_keys,
        )
    )
    lineage_assignments = tuple(
        sorted(
            (
                build_mechanism_lineage_assignment_v3(
                    registry=lineage_registry,
                    case=case,
                    lineage_id=old_lineage_by_case[old_id].lineage_id,
                    case_evidence_refs=tuple(
                        MechanismLineageEvidenceRefV3(
                            source_id=record.source_id,
                            source_record_id=record.source_record_id,
                            source_record_raw_sha256=record.raw_sha256,
                        )
                        for record in case.source_records
                    ),
                    assignment_basis_sha256=canonical_sha256(
                        (case.case_id, "formal-positive-lineage")
                    ),
                    assigned_at="2026-08-09T20:13:00+08:00",
                )
                for old_id, case in new_by_old.items()
            ),
            key=lambda item: (item.case_id, item.assignment_id),
        )
    )
    leakage = build_leakage_component_release_v3(
        cases=cases,
        split_manifest=split,
        grouping_algorithms=base.leakage.grouping_algorithms,
        grouping_runs=grouping_runs,
        grouping_assignments=grouping_assignments,
        mechanism_lineage_registry=lineage_registry,
        mechanism_lineage_assignments=lineage_assignments,
        created_at=base.leakage.created_at,
    )
    calibration = _calibration_manifest_v2(
        registry=lineage_registry,
        algorithms=base.leakage.grouping_algorithms,
    )
    expert = _v2_registry_fixture(split=split, calibration=calibration)
    registry = _readdress_v2_registry(
        expert["registry"], registered_at="2026-08-09T20:35:00+08:00"
    )
    candidates = tuple(
        cases_fixture.build_frozen_case_candidate(
            case=case,
            slot_id=cases_fixture.frozen_case_slot_id(case),
            priority=0,
            declared_at="2026-08-09T20:12:20+08:00",
        )
        for case in cases
    )
    source_policies = tuple(
        cases_fixture.build_case_source_policy_attestation_v2(
            case=candidate.case,
            source_id=record.source_id,
            source_record_id=record.source_record_id,
            usage_roles=(
                (
                    cases_fixture.SourceUseRole.CASE_SEED,
                    cases_fixture.SourceUseRole.STRUCTURE,
                )
                if record.source_id == "cod"
                else (
                    cases_fixture.SourceUseRole.IDENTIFIER_RESOLUTION,
                    cases_fixture.SourceUseRole.LITERATURE_RETRIEVAL,
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
    frozen = cases_fixture.build_frozen_case_release_v2(
        split_manifest=split,
        leakage_release=leakage,
        expert_registry=registry,
        candidates=candidates,
        candidate_lineage_assignments=lineage_assignments,
        source_policy_attestations=source_policies,
        case_freeze_policy_sha256=canonical_sha256("formal-positive-case-freeze"),
        eligibility_policy_sha256=canonical_sha256("formal-positive-eligibility"),
        candidate_pool_sealed_at="2026-08-09T20:14:00+08:00",
        frozen_at="2026-08-09T20:40:00+08:00",
    )
    decisions = tuple(
        cases_fixture.build_eligibility_decision(
            candidate=candidate,
            status=cases_fixture.CaseEligibilityStatus.INCLUDED,
            reason_codes=(
                cases_fixture.CaseEligibilityReasonCode.MEETS_ALL_PREREGISTERED_CRITERIA,
            ),
            assessor_id="eligibility-assessor-a",
            assessment_protocol_sha256=frozen.eligibility_policy_sha256,
            rationale_sha256=canonical_sha256(
                (candidate.case.case_id, "formal-positive-eligibility")
            ),
            assessed_at="2026-08-09T20:20:00+08:00",
        )
        for candidate in candidates
    )
    eligibility = cases_fixture.build_pre_run_eligibility_release_v2(
        frozen_case_release=frozen,
        decisions=decisions,
        sealed_at="2026-08-09T20:45:00+08:00",
    )
    return {
        "cases": cases,
        "split": split,
        "leakage": leakage,
        "calibration": calibration,
        "private": expert["private"],
        "public": expert["public"],
        "registry": registry,
        "frozen": frozen,
        "eligibility": eligibility,
    }


def test_v2_identity_and_registry_positive_closure_is_pseudonymous() -> None:
    fixture = _v2_registry_fixture()
    private = fixture["private"]
    public = fixture["public"]
    registry = fixture["registry"]
    calibration = fixture["calibration"]
    split = fixture["split"]
    assert isinstance(private, PrivateExpertIdentityCustodianAttestationV2)
    assert isinstance(public, PublicExpertIdentityReleaseV2)
    assert isinstance(registry, ExpertStudyRegistryV2)
    assert isinstance(calibration, CalibrationSetManifestV2)
    assert isinstance(split, BenchmarkSplitManifestV2)
    assert private.commitment_key_id is None
    assert private.cryptographic_signature_or_key_required is False
    assert private.externally_trusted_signature_present is False

    assert_private_identity_attestation_matches_public_v2(
        private_attestation=private,
        public_release=public,
    )
    assert_expert_registry_covers_split_v2(
        registry=registry,
        split_manifest=split,
    )
    for completion in registry.calibration_completions:
        assert_calibration_completion_replays_manifest_v2(
            completion=completion,
            calibration_manifest=calibration,
        )
    assert_distinct_natural_person_assignments_v2(
        private_identity_attestation=private,
        registry=registry,
    )


def test_v2_registry_assignment_rejects_two_pseudonyms_for_one_natural_person() -> None:
    fixture = _v2_registry_fixture()
    private = fixture["private"]
    bindings = list(private.bindings)
    bindings[1] = bindings[1].model_copy(
        update={
            "opaque_natural_person_subject_ref": (
                bindings[0].opaque_natural_person_subject_ref
            )
        }
    )
    forged = private.model_copy(update={"bindings": tuple(bindings)})
    with pytest.raises(
        ValidationError, match="one natural person subject is bound"
    ):
        assert_distinct_natural_person_assignments_v2(
            private_identity_attestation=forged,
            registry=fixture["registry"],
        )


def test_v2_private_attestation_rejects_same_person_under_multiple_pseudonyms() -> None:
    private = _private_identity_v2()
    bindings = list(private.bindings)
    bindings[1] = bindings[1].model_copy(
        update={
            "opaque_natural_person_subject_ref": (
                bindings[0].opaque_natural_person_subject_ref
            )
        }
    )
    with pytest.raises(
        ValidationError, match="one natural person subject is bound"
    ):
        build_private_expert_identity_attestation_v2(
            custodian_id=private.custodian_id,
            custodian_policy_sha256=private.custodian_policy_sha256,
            commitment_key_id=private.commitment_key_id,
            bindings=tuple(bindings),
            attested_at=private.attested_at,
        )

    bindings = list(private.bindings)
    bindings[1] = bindings[1].model_copy(
        update={
            "natural_person_commitment_sha256": (
                bindings[0].natural_person_commitment_sha256
            )
        }
    )
    with pytest.raises(
        ValidationError, match="one natural-person commitment is bound"
    ):
        build_private_expert_identity_attestation_v2(
            custodian_id=private.custodian_id,
            custodian_policy_sha256=private.custodian_policy_sha256,
            commitment_key_id=private.commitment_key_id,
            bindings=tuple(bindings),
            attested_at=private.attested_at,
        )


def test_v2_assignment_rejects_adjudicator_as_reviewer() -> None:
    with pytest.raises(ValidationError, match="adjudicator must differ"):
        CaseExpertAssignmentV1(
            case_id="case-a",
            case_sha256="a" * 64,
            reviewer_ids=("adjudicator-a", "reviewer-a"),
            adjudicator_id="adjudicator-a",
        )


def test_v2_public_schemas_and_payload_do_not_contain_private_identity_fields() -> None:
    fixture = _v2_registry_fixture()
    private = fixture["private"]
    public = fixture["public"]
    registry = fixture["registry"]
    assert isinstance(private, PrivateExpertIdentityCustodianAttestationV2)
    assert isinstance(public, PublicExpertIdentityReleaseV2)
    assert isinstance(registry, ExpertStudyRegistryV2)

    public_schema = json.dumps(
        PublicExpertIdentityReleaseV2.model_json_schema(), sort_keys=True
    )
    registry_schema = json.dumps(
        ExpertStudyRegistryV2.model_json_schema(), sort_keys=True
    )
    for forbidden in (
        "opaque_natural_person_subject_ref",
        "natural_person_commitment_sha256",
        "identity_evidence_artifact_uri",
        "identity_evidence_sha256",
        "custodian_id",
        "commitment_key_id",
    ):
        assert forbidden not in public_schema
        assert forbidden not in registry_schema
    serialized = public.model_dump_json() + registry.model_dump_json()
    for binding in private.bindings:
        assert binding.opaque_natural_person_subject_ref not in serialized
        assert binding.natural_person_commitment_sha256 not in serialized
        assert binding.identity_evidence_artifact_uri not in serialized
        assert binding.identity_evidence_sha256 not in serialized


def test_v2_completion_rejects_split_sha_or_foreign_manifest_as_calibration() -> None:
    fixture = _v2_registry_fixture()
    split = fixture["split"]
    calibration = fixture["calibration"]
    completion = fixture["completions"][0]
    assert isinstance(split, BenchmarkSplitManifestV2)
    assert isinstance(calibration, CalibrationSetManifestV2)
    assert isinstance(completion, CalibrationCompletionV2)
    values = completion.model_dump(
        mode="python", exclude={"completion_id", "completion_sha256"}
    )
    values.update(
        {
            "calibration_manifest_id": split.manifest_id,
            "calibration_manifest_sha256": split.manifest_sha256,
        }
    )
    forged = _identified(
        CalibrationCompletionV2,
        id_field="completion_id",
        sha_field="completion_sha256",
        prefix="calibration-completion-v2",
        values=values,
    )
    with pytest.raises(ValueError, match="foreign manifest or guide"):
        assert_calibration_completion_replays_manifest_v2(
            completion=forged,
            calibration_manifest=calibration,
        )


@pytest.mark.parametrize("attack", ("missing", "duplicate", "foreign"))
def test_v2_registry_rejects_missing_duplicate_or_foreign_completion(
    attack: str,
) -> None:
    fixture = _v2_registry_fixture()
    registry = fixture["registry"]
    completions = list(fixture["completions"])
    assert isinstance(registry, ExpertStudyRegistryV2)
    if attack == "missing":
        completions = completions[:-1]
    elif attack == "duplicate":
        completions.append(completions[-1])
    else:
        values = completions[-1].model_dump(
            mode="python", exclude={"completion_id", "completion_sha256"}
        )
        values["expert_id"] = "foreign-reviewer"
        completions[-1] = _identified(
            CalibrationCompletionV2,
            id_field="completion_id",
            sha_field="completion_sha256",
            prefix="calibration-completion-v2",
            values=values,
        )
    with pytest.raises(
        ValidationError,
        match="completion expert IDs|exactly one completion",
    ):
        _readdress_v2_registry(
            registry,
            calibration_completions=tuple(
                sorted(completions, key=lambda item: item.expert_id)
            ),
        )


def test_v2_calibration_and_conflict_assessments_must_precede_registry_seal() -> None:
    fixture = _v2_registry_fixture()
    registry = fixture["registry"]
    assert isinstance(registry, ExpertStudyRegistryV2)
    completion = registry.calibration_completions[0]
    completion_values = completion.model_dump(
        mode="python", exclude={"completion_id", "completion_sha256"}
    )
    completion_values["completed_at"] = registry.registered_at
    late_completion = _identified(
        CalibrationCompletionV2,
        id_field="completion_id",
        sha_field="completion_sha256",
        prefix="calibration-completion-v2",
        values=completion_values,
    )
    with pytest.raises(ValidationError, match="not before registry seal"):
        _readdress_v2_registry(
            registry,
            calibration_completions=(
                late_completion,
                *registry.calibration_completions[1:],
            ),
        )

    conflict = registry.conflict_assessments[0].model_copy(
        update={"assessed_at": registry.registered_at}
    )
    with pytest.raises(ValidationError, match="not before registry seal"):
        _readdress_v2_registry(
            registry,
            conflict_assessments=(conflict, *registry.conflict_assessments[1:]),
        )


def test_v2_public_identity_release_must_precede_registry_seal() -> None:
    fixture = _v2_registry_fixture()
    registry = fixture["registry"]
    private = fixture["private"]
    assert isinstance(registry, ExpertStudyRegistryV2)
    assert isinstance(private, PrivateExpertIdentityCustodianAttestationV2)
    late_public = build_public_expert_identity_release_v2(
        private_attestation=private,
        released_at=registry.registered_at,
    )

    with pytest.raises(ValueError, match="public identity release was not before"):
        build_expert_study_registry_v2(
            split_manifest=fixture["split"],
            calibration_manifest=fixture["calibration"],
            public_identity_release=late_public,
            calibration_completions=fixture["completions"],
            conflict_assessments=fixture["conflicts"],
            assignments=fixture["assignments"],
            registered_at=registry.registered_at,
        )


def test_v2_registry_seal_must_precede_every_execution_budget() -> None:
    fixture = _v2_registry_fixture()
    registry = fixture["registry"]
    assert isinstance(registry, ExpertStudyRegistryV2)
    future = _budget_v2(frozen_at="2026-08-09T08:41:00+08:00")
    assert_expert_registry_sealed_before_budgets_v2(
        registry=registry,
        budget_manifests=(future,),
    )

    late = _budget_v2(frozen_at=registry.registered_at)
    with pytest.raises(ValueError, match="not sealed before every budget"):
        assert_expert_registry_sealed_before_budgets_v2(
            registry=registry,
            budget_manifests=(future, late),
        )
