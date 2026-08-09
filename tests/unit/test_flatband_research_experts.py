from __future__ import annotations

import json
from typing import Any, TypeVar

import pytest
from pydantic import ValidationError

from material_agent.inspiration.models import StrictModel, canonical_sha256, deterministic_id
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
from material_agent.research.flatband_execution import (
    BudgetManifestV1,
    ResearchSystemId,
    SourceBudgetV1,
    SourceVariant,
    SystemConfigV1,
    source_policy_values,
)
from material_agent.research.flatband_leakage import (
    LeakageAxis,
    LeakageAxisV3,
    LeakageComponentReleaseV3,
    MechanismLineageAssignmentV3,
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


def _calibration_manifest_v2(
    *,
    registry: MechanismLineageRegistryV3 | None = None,
    algorithms: tuple[StructureGroupingAlgorithmV2, ...] | None = None,
) -> CalibrationSetManifestV2:
    registry = registry or _global_lineage_registry_v3()
    algorithms = algorithms or tuple(
        _v2_algorithm(axis)
        for axis in sorted(
            (
                LeakageAxis.STRUCTURE_FINGERPRINT,
                LeakageAxis.STRUCTURE_PROTOTYPE,
            ),
            key=lambda item: item.value,
        )
    )
    cases, group_keys, lineage_by_case = _v2_calibration_cases(
        algorithms, registry
    )
    runs, assignments = _v2_grouping_artifacts(
        cases=cases,
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
                {"calibration-case": case.case_id, "kind": "lineage-assignment"}
            ),
            assigned_at="2026-08-09T07:40:00+08:00",
        )
        for case in cases
    )
    return build_calibration_set_manifest_v2(
        cases=tuple(reversed(cases)),
        annotation_guide_version=GUIDE_VERSION_V2,
        annotation_guide_sha256=GUIDE_SHA,
        case_freeze_policy_sha256=canonical_sha256(
            {"calibration-freeze-policy": "v2"}
        ),
        grouping_algorithms=tuple(reversed(algorithms)),
        grouping_runs=tuple(reversed(runs)),
        grouping_assignments=tuple(reversed(assignments)),
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


def test_v2_calibration_manifest_replays_full_cases_sources_and_groups() -> None:
    manifest = _calibration_manifest_v2()

    assert len(manifest.cases) == 4
    assert len(manifest.source_records) == 4
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


def test_v2_calibration_manifest_rejects_missing_structure_run_output_and_fake_group() -> None:
    manifest = _calibration_manifest_v2()
    missing = manifest.model_dump(
        mode="python", exclude={"manifest_id", "manifest_sha256"}
    )
    missing["grouping_assignments"] = missing["grouping_assignments"][:-1]
    missing_digest = canonical_sha256(missing)
    with pytest.raises(ValidationError, match="do not exactly cover cases and axes"):
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

    import test_flatband_research_cases as cases_fixture

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
