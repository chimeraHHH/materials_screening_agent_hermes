from __future__ import annotations

from collections import defaultdict
from itertools import combinations
import json
from typing import Any, TypeVar

import pytest
from pydantic import ValidationError

from material_agent.inspiration.models import (
    StrictModel,
    canonical_json_bytes,
    canonical_sha256,
    deterministic_id,
)
from material_agent.research.flatband_contracts import (
    BenchmarkSplit,
    BenchmarkSplitManifestV1,
    BenchmarkSplitManifestV2,
    CaseHoldoutMembershipV2,
    Dimensionality,
    FlatBandBenchmarkCaseV1,
    MechanismFamily,
    OodHoldoutAxis,
    OodHoldoutFamilyV1,
    SourceRecordRefV1,
    SplitCaseRefV1,
    SplitCaseRefV2,
    SplitManifestKind,
    TargetBandClass,
    mechanism_holdout_taxonomy_group_id,
)
from material_agent.research.flatband_leakage import (
    LeakageAxis,
    LeakageAxisV3,
    LeakageComponentReleaseV1,
    LeakageComponentReleaseV2,
    LeakageComponentReleaseV3,
    LeakageGroupDefinitionV3,
    LeakageMembershipV1,
    LeakageRoundClosureContextV3,
    LeakageUnsplitCaseUniverseContextV3,
    MechanismLineageAssignmentAdjudicationV3,
    MechanismLineageAssignmentCandidateUniverseV3,
    MechanismLineageAssignmentCurationReleaseV3,
    MechanismLineageAssignmentDecisionV3,
    MechanismLineageAssignmentProposalV3,
    MechanismLineageAssignmentReviewV3,
    MechanismLineageAssignmentReviewerRosterV3,
    MechanismLineageAssignmentV3,
    MechanismLineageCurationReleaseV3,
    MechanismLineageCuratorDeclarationV3,
    MechanismLineageDefinitionV3,
    MechanismLineageEvidenceRefV3,
    MechanismLineageRegistryV3,
    MechanismLineageReviewDecisionV3,
    StructureGroupingAlgorithmV2,
    StructureGroupingAssignmentV2,
    StructureGroupingRunV2,
    assert_leakage_split_closure_v2,
    assert_leakage_split_closure_v3,
    assert_cross_round_leakage_disjoint_v3,
    assert_formal_mechanism_lineage_assignment_curation_v3,
    assert_formal_mechanism_lineage_registry_v3,
    assert_main_leakage_v3,
    assert_pilot_leakage_v2,
    assert_pilot_leakage_v3,
    assert_leakage_split_closure,
    assert_leakage_releases_disjoint,
    build_leakage_component_release,
    build_leakage_component_release_v2,
    build_leakage_component_release_v3,
    build_mechanism_lineage_assignment_v3,
    build_mechanism_lineage_assignment_adjudication_v3,
    build_mechanism_lineage_assignment_candidate_universe_v3,
    build_mechanism_lineage_assignment_curation_policy_v3,
    build_mechanism_lineage_assignment_curation_release_v3,
    build_mechanism_lineage_assignment_proposal_v3,
    build_mechanism_lineage_assignment_review_manifest_v3,
    build_mechanism_lineage_assignment_review_v3,
    build_mechanism_lineage_assignment_reviewer_roster_v3,
    build_mechanism_lineage_curation_policy_v3,
    build_mechanism_lineage_curation_release_v3,
    build_mechanism_lineage_curator_roster_v3,
    build_mechanism_lineage_definition_adjudication_v3,
    build_mechanism_lineage_definition_v3,
    build_mechanism_lineage_definition_review_v3,
    build_mechanism_lineage_evidence_review_manifest_v3,
    build_mechanism_lineage_registry_v3,
    component_assignments,
    component_assignments_v2,
    component_assignments_v3,
    derive_formal_leakage_memberships_for_case_universe_v3,
    derive_formal_mechanism_lineage_assignments_v3,
    derive_leakage_group_ids_v2,
    derive_leakage_group_ids_v3,
    structure_grouping_case_universe_sha256_v2,
)


ModelT = TypeVar("ModelT", bound=StrictModel)
SHA_A = "a" * 64
SHA_B = "b" * 64


def _identified(
    model_type: type[ModelT],
    *,
    id_field: str,
    sha_field: str,
    prefix: str,
    values: dict[str, Any],
) -> ModelT:
    semantic = model_type.model_construct(**values).model_dump(
        mode="python", exclude={id_field, sha_field}
    )
    digest = canonical_sha256(semantic)
    return model_type.model_validate(
        {
            **values,
            sha_field: digest,
            id_field: deterministic_id(prefix, {sha_field: digest}),
        }
    )


def _memberships_for_case(
    *, case_id: str, case_sha256: str, component_index: int, ood: bool = False
) -> tuple[LeakageMembershipV1, ...]:
    groups = {
        LeakageAxis.COMPOSITION_FAMILY: f"composition-{component_index:02d}",
        LeakageAxis.STRUCTURE_PROTOTYPE: f"prototype-{component_index:02d}",
        LeakageAxis.STRUCTURE_FINGERPRINT: f"fingerprint-{component_index:02d}",
        LeakageAxis.ARTICLE_OR_SOURCE_FAMILY: f"article-{component_index:02d}",
        LeakageAxis.MECHANISM_FAMILY: (
            f"ood-holdout-{component_index:02d}"
            if ood
            else f"mechanism-{component_index:02d}"
        ),
    }
    return tuple(
        LeakageMembershipV1(
            case_id=case_id,
            case_sha256=case_sha256,
            axis=axis,
            group_id=group_id,
            provenance_sha256=f"{component_index * 10 + position + 1:064x}",
        )
        for position, (axis, group_id) in enumerate(
            sorted(groups.items(), key=lambda item: item[0].value)
        )
    )


def _pilot() -> tuple[BenchmarkSplitManifestV1, tuple[LeakageMembershipV1, ...]]:
    mechanisms = (
        MechanismFamily.LATTICE_INTERFERENCE,
        MechanismFamily.LINE_GRAPH,
        MechanismFamily.ORBITAL_FRUSTRATION_HYBRIDIZATION,
        MechanismFamily.SYMMETRY_INDUCED,
        MechanismFamily.CONFINEMENT,
    )
    memberships: list[LeakageMembershipV1] = []
    cases: list[SplitCaseRefV1] = []
    for index in range(30):
        case_id = f"pilot-case-{index:02d}"
        case_sha256 = f"{index + 1:064x}"
        case_memberships = _memberships_for_case(
            case_id=case_id,
            case_sha256=case_sha256,
            component_index=index // 3,
        )
        memberships.extend(case_memberships)
        cases.append(
            SplitCaseRefV1(
                case_id=case_id,
                case_sha256=case_sha256,
                split=BenchmarkSplit.PILOT_R1,
                target_class=(
                    TargetBandClass.FB100 if index < 15 else TargetBandClass.NB300
                ),
                dimensionality=(
                    Dimensionality.TWO_D
                    if index % 2 == 0
                    else Dimensionality.THREE_D
                ),
                primary_mechanism_stratum=mechanisms[index // 6],
                leakage_group_ids=tuple(
                    sorted(item.group_id for item in case_memberships)
                ),
            )
        )
    manifest = _identified(
        BenchmarkSplitManifestV1,
        id_field="manifest_id",
        sha_field="manifest_sha256",
        prefix="split-manifest",
        values={
            "manifest_kind": SplitManifestKind.PILOT_R1,
            "split_seed": 73,
            "cases": tuple(cases),
        },
    )
    return manifest, tuple(memberships)


def _main() -> tuple[BenchmarkSplitManifestV1, tuple[LeakageMembershipV1, ...]]:
    specifications = (
        (BenchmarkSplit.DEVELOPMENT, 60, 3, "dev"),
        (BenchmarkSplit.LOCKED_IID, 30, 3, "iid"),
        (BenchmarkSplit.LOCKED_OOD, 30, 3, "ood"),
    )
    cases: list[SplitCaseRefV1] = []
    memberships: list[LeakageMembershipV1] = []
    holdouts: set[str] = set()
    absolute_index = 0
    component_offset = 0
    for split, count, per_component, prefix in specifications:
        for local_index in range(count):
            component_index = component_offset + local_index // per_component
            case_id = f"{prefix}-case-{local_index:02d}"
            case_sha256 = f"{absolute_index + 1:064x}"
            case_memberships = _memberships_for_case(
                case_id=case_id,
                case_sha256=case_sha256,
                component_index=component_index,
                ood=split is BenchmarkSplit.LOCKED_OOD,
            )
            memberships.extend(case_memberships)
            if split is BenchmarkSplit.LOCKED_OOD:
                holdouts.add(f"ood-holdout-{component_index:02d}")
            cases.append(
                SplitCaseRefV1(
                    case_id=case_id,
                    case_sha256=case_sha256,
                    split=split,
                    target_class=(
                        TargetBandClass.FB100
                        if local_index < count // 2
                        else TargetBandClass.NB300
                    ),
                    dimensionality=(
                        Dimensionality.TWO_D
                        if local_index % 2 == 0
                        else Dimensionality.THREE_D
                    ),
                    primary_mechanism_stratum=MechanismFamily.LATTICE_INTERFERENCE,
                    leakage_group_ids=tuple(
                        sorted(item.group_id for item in case_memberships)
                    ),
                )
            )
            absolute_index += 1
        component_offset += count // per_component
    cases.sort(key=lambda item: item.case_id)
    manifest = _identified(
        BenchmarkSplitManifestV1,
        id_field="manifest_id",
        sha_field="manifest_sha256",
        prefix="split-manifest",
        values={
            "manifest_kind": SplitManifestKind.MAIN_120,
            "split_seed": 91,
            "cases": tuple(cases),
            "ood_holdout_families": tuple(
                OodHoldoutFamilyV1(
                    axis=OodHoldoutAxis.MECHANISM_FAMILY,
                    group_id=group_id,
                )
                for group_id in sorted(holdouts)
            ),
        },
    )
    return manifest, tuple(memberships)


V2_MECHANISMS = tuple(MechanismFamily)
V2_FORMULAS = (
    "LiF",
    "NaCl",
    "MgO",
    "AlN",
    "SiC",
    "FeO",
    "CoO",
    "NiO",
    "CuO",
    "ZnO",
)


def _v2_algorithm(axis: LeakageAxis) -> StructureGroupingAlgorithmV2:
    return _identified(
        StructureGroupingAlgorithmV2,
        id_field="algorithm_id",
        sha_field="algorithm_sha256",
        prefix="structure-group-algorithm",
        values={
            "axis": axis,
            "algorithm_name": f"frozen-{axis.value.casefold()}",
            "algorithm_version": "v2-test",
            "implementation_sha256": canonical_sha256(
                {"implementation": axis.value}
            ),
            "configuration_sha256": canonical_sha256(
                {"configuration": axis.value}
            ),
        },
    )


def _v2_source(component_index: int) -> SourceRecordRefV1:
    return SourceRecordRefV1(
        source_id="crossref",
        source_record_id=f"work-{component_index:03d}",
        canonical_url=f"https://doi.org/10.1000/work-{component_index:03d}",
        source_version="2026-08-09",
        license_expression="CC0-1.0",
        accessed_at="2026-08-09T20:00:00+08:00",
        raw_sha256=canonical_sha256({"work": component_index}),
        public_redistribution_allowed=True,
    )


def _v2_group_id(axis: LeakageAxis, preimage: object) -> str:
    canonical_preimage = canonical_json_bytes(preimage).decode("utf-8")
    return deterministic_id(
        "leakage-group",
        {"axis": axis.value, "canonical_preimage": canonical_preimage},
    )


def _v2_mechanism_group(mechanism: MechanismFamily) -> str:
    return _v2_group_id(
        LeakageAxis.MECHANISM_FAMILY,
        {"kind": "mechanism", "mechanism": mechanism.value},
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
                    {"environment": algorithm.axis.value}
                ),
                "started_at": "2026-08-09T20:10:00+08:00",
                "completed_at": "2026-08-09T20:11:00+08:00",
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
                "canonical_group_key": group_keys[(axis, case.case_id)],
            },
        )
        for axis in sorted(
            (
                LeakageAxis.STRUCTURE_FINGERPRINT,
                LeakageAxis.STRUCTURE_PROTOTYPE,
            ),
            key=lambda value: value.value,
        )
        for case in cases
    )
    return runs, assignments


def _v2_pilot(
    *,
    fake_groups: dict[tuple[int, LeakageAxis], str] | None = None,
    formula_overrides: dict[int, str] | None = None,
) -> dict[str, object]:
    fake_groups = {} if fake_groups is None else fake_groups
    formula_overrides = {} if formula_overrides is None else formula_overrides
    algorithms = tuple(
        _v2_algorithm(axis)
        for axis in sorted(
            (
                LeakageAxis.STRUCTURE_FINGERPRINT,
                LeakageAxis.STRUCTURE_PROTOTYPE,
            ),
            key=lambda value: value.value,
        )
    )
    cases: list[FlatBandBenchmarkCaseV1] = []
    group_keys_by_index: dict[tuple[LeakageAxis, int], str] = {}
    for index in range(30):
        component_index = index // 3
        mechanism = V2_MECHANISMS[component_index]
        formula = formula_overrides.get(index, V2_FORMULAS[component_index])
        source = _v2_source(component_index)
        structure_groups = tuple(
            (
                algorithm,
                f"{algorithm.axis.value.casefold()}-{component_index:02d}",
            )
            for algorithm in algorithms
        )
        for algorithm, key in structure_groups:
            group_keys_by_index[(algorithm.axis, index)] = key
        group_ids = set(
            derive_leakage_group_ids_v2(
                formula=formula,
                primary_mechanism_stratum=mechanism,
                source_records=(source,),
                structure_groups=structure_groups,
            )
        )
        for axis in LeakageAxis:
            replacement = fake_groups.get((index, axis))
            if replacement is None:
                continue
            if axis is LeakageAxis.MECHANISM_FAMILY:
                group_ids.remove(_v2_mechanism_group(mechanism))
            elif axis is LeakageAxis.COMPOSITION_FAMILY:
                alternate = set(
                    derive_leakage_group_ids_v2(
                        formula="XeF2",
                        primary_mechanism_stratum=mechanism,
                        source_records=(source,),
                        structure_groups=structure_groups,
                    )
                )
                group_ids.remove(next(iter(group_ids - alternate)))
            elif axis is LeakageAxis.ARTICLE_OR_SOURCE_FAMILY:
                alternate = set(
                    derive_leakage_group_ids_v2(
                        formula=formula,
                        primary_mechanism_stratum=mechanism,
                        source_records=(_v2_source(999),),
                        structure_groups=structure_groups,
                    )
                )
                group_ids.remove(next(iter(group_ids - alternate)))
            else:
                raise AssertionError("test helper only overrides derived scalar axes")
            group_ids.add(replacement)
        values = {
            "parent_label": f"pilot parent {index}",
            "formula": formula,
            "structure_sha256": canonical_sha256({"structure": index}),
            "source_records": (source,),
            "target_class": (
                TargetBandClass.FB100 if index < 15 else TargetBandClass.NB300
            ),
            "dimensionality": (
                Dimensionality.TWO_D
                if index % 2 == 0
                else Dimensionality.THREE_D
            ),
            "frozen_request": f"Evaluate bounded flat-band case {index}",
            "frozen_requirement_sha256": canonical_sha256(
                {"requirement": index}
            ),
            "hard_constraints": (),
            "soft_preferences": (),
            "forbidden_transformations": (),
            "seed_evidence": (),
            "primary_mechanism_stratum": mechanism,
            "leakage_group_ids": tuple(sorted(group_ids)),
            "public_release_allowed": True,
        }
        cases.append(
            _identified(
                FlatBandBenchmarkCaseV1,
                id_field="case_id",
                sha_field="case_sha256",
                prefix="flatband-case",
                values=values,
            )
        )
    ordered_cases = tuple(sorted(cases, key=lambda item: item.case_id))
    split_cases = tuple(
        SplitCaseRefV1(
            case_id=case.case_id,
            case_sha256=case.case_sha256,
            split=BenchmarkSplit.PILOT_R1,
            target_class=case.target_class,
            dimensionality=case.dimensionality,
            primary_mechanism_stratum=case.primary_mechanism_stratum,
            leakage_group_ids=case.leakage_group_ids,
        )
        for case in ordered_cases
    )
    manifest = _identified(
        BenchmarkSplitManifestV1,
        id_field="manifest_id",
        sha_field="manifest_sha256",
        prefix="split-manifest",
        values={
            "manifest_kind": SplitManifestKind.PILOT_R1,
            "split_seed": 73,
            "cases": split_cases,
        },
    )
    index_by_case_id = {case.case_id: index for index, case in enumerate(cases)}
    group_keys = {
        (axis, case.case_id): group_keys_by_index[(axis, index_by_case_id[case.case_id])]
        for case in ordered_cases
        for axis in (
            LeakageAxis.STRUCTURE_FINGERPRINT,
            LeakageAxis.STRUCTURE_PROTOTYPE,
        )
    }
    runs, assignments = _v2_grouping_artifacts(
        cases=ordered_cases,
        algorithms=algorithms,
        group_keys=group_keys,
    )
    return {
        "cases": ordered_cases,
        "manifest": manifest,
        "algorithms": algorithms,
        "runs": runs,
        "assignments": assignments,
    }


def _v2_main_with_fake_mechanism_independence() -> dict[str, object]:
    algorithms = tuple(
        _v2_algorithm(axis)
        for axis in sorted(
            (
                LeakageAxis.STRUCTURE_FINGERPRINT,
                LeakageAxis.STRUCTURE_PROTOTYPE,
            ),
            key=lambda value: value.value,
        )
    )
    element_symbols = (
        "H",
        "He",
        "Li",
        "Be",
        "B",
        "C",
        "N",
        "O",
        "F",
        "Ne",
        "Na",
        "Mg",
        "Al",
        "Si",
        "P",
        "S",
    )
    formula_pairs = tuple(combinations(element_symbols, 2))
    cases: list[FlatBandBenchmarkCaseV1] = []
    group_keys_by_index: dict[tuple[LeakageAxis, int], str] = {}
    holdout_group_ids: list[str] = []
    split_specs = (
        (BenchmarkSplit.DEVELOPMENT, 0, 60),
        (BenchmarkSplit.LOCKED_IID, 60, 30),
        (BenchmarkSplit.LOCKED_OOD, 90, 30),
    )
    split_by_index: dict[int, tuple[BenchmarkSplit, int, int]] = {}
    for split, start, count in split_specs:
        for local_index in range(count):
            split_by_index[start + local_index] = (split, local_index, count)
    prototype = next(
        item
        for item in algorithms
        if item.axis is LeakageAxis.STRUCTURE_PROTOTYPE
    )
    for index in range(120):
        split, local_index, split_count = split_by_index[index]
        formula = "".join(formula_pairs[index])
        source = _v2_source(index)
        structure_groups = tuple(
            (algorithm, f"{algorithm.axis.value.casefold()}-{index:03d}")
            for algorithm in algorithms
        )
        for algorithm, key in structure_groups:
            group_keys_by_index[(algorithm.axis, index)] = key
        group_ids = set(
            derive_leakage_group_ids_v2(
                formula=formula,
                primary_mechanism_stratum=MechanismFamily.LATTICE_INTERFERENCE,
                source_records=(source,),
                structure_groups=structure_groups,
            )
        )
        group_ids.remove(
            _v2_mechanism_group(MechanismFamily.LATTICE_INTERFERENCE)
        )
        group_ids.add(f"fake-mechanism-independent-{index:03d}")
        if split is BenchmarkSplit.LOCKED_OOD:
            prototype_key = group_keys_by_index[
                (LeakageAxis.STRUCTURE_PROTOTYPE, index)
            ]
            holdout_group_ids.append(
                _v2_group_id(
                    LeakageAxis.STRUCTURE_PROTOTYPE,
                    {
                        "kind": "structure-group",
                        "axis": LeakageAxis.STRUCTURE_PROTOTYPE.value,
                        "algorithm_id": prototype.algorithm_id,
                        "algorithm_sha256": prototype.algorithm_sha256,
                        "canonical_group_key": prototype_key,
                    },
                )
            )
        cases.append(
            _identified(
                FlatBandBenchmarkCaseV1,
                id_field="case_id",
                sha_field="case_sha256",
                prefix="flatband-case",
                values={
                    "parent_label": f"main parent {index}",
                    "formula": formula,
                    "structure_sha256": canonical_sha256({"structure": index}),
                    "source_records": (source,),
                    "target_class": (
                        TargetBandClass.FB100
                        if local_index < split_count // 2
                        else TargetBandClass.NB300
                    ),
                    "dimensionality": (
                        Dimensionality.TWO_D
                        if local_index % 2 == 0
                        else Dimensionality.THREE_D
                    ),
                    "frozen_request": f"Evaluate main flat-band case {index}",
                    "frozen_requirement_sha256": canonical_sha256(
                        {"requirement": index}
                    ),
                    "hard_constraints": (),
                    "soft_preferences": (),
                    "forbidden_transformations": (),
                    "seed_evidence": (),
                    "primary_mechanism_stratum": (
                        MechanismFamily.LATTICE_INTERFERENCE
                    ),
                    "leakage_group_ids": tuple(sorted(group_ids)),
                    "public_release_allowed": True,
                },
            )
        )
    ordered_cases = tuple(sorted(cases, key=lambda item: item.case_id))
    original_index_by_id = {case.case_id: index for index, case in enumerate(cases)}
    split_cases = tuple(
        SplitCaseRefV1(
            case_id=case.case_id,
            case_sha256=case.case_sha256,
            split=split_by_index[original_index_by_id[case.case_id]][0],
            target_class=case.target_class,
            dimensionality=case.dimensionality,
            primary_mechanism_stratum=case.primary_mechanism_stratum,
            leakage_group_ids=case.leakage_group_ids,
        )
        for case in ordered_cases
    )
    manifest = _identified(
        BenchmarkSplitManifestV1,
        id_field="manifest_id",
        sha_field="manifest_sha256",
        prefix="split-manifest",
        values={
            "manifest_kind": SplitManifestKind.MAIN_120,
            "split_seed": 91,
            "cases": split_cases,
            "ood_holdout_families": tuple(
                OodHoldoutFamilyV1(
                    axis=OodHoldoutAxis.STRUCTURE_PROTOTYPE,
                    group_id=group_id,
                )
                for group_id in sorted(holdout_group_ids)
            ),
        },
    )
    group_keys = {
        (axis, case.case_id): group_keys_by_index[
            (axis, original_index_by_id[case.case_id])
        ]
        for case in ordered_cases
        for axis in (
            LeakageAxis.STRUCTURE_FINGERPRINT,
            LeakageAxis.STRUCTURE_PROTOTYPE,
        )
    }
    runs, assignments = _v2_grouping_artifacts(
        cases=ordered_cases,
        algorithms=algorithms,
        group_keys=group_keys,
    )
    return {
        "cases": ordered_cases,
        "manifest": manifest,
        "algorithms": algorithms,
        "runs": runs,
        "assignments": assignments,
    }


def test_pilot_graph_reduces_all_axes_to_ten_deterministic_components() -> None:
    manifest, memberships = _pilot()
    release = build_leakage_component_release(
        split_manifest=manifest,
        memberships=reversed(memberships),
        construction_policy_sha256=SHA_A,
        created_at="2026-08-09T20:30:00+08:00",
    )
    assert len(release.memberships) == 150
    assert len(release.components) == 10
    assert len(component_assignments(release)) == 30

    replay = build_leakage_component_release(
        split_manifest=manifest,
        memberships=memberships,
        construction_policy_sha256=SHA_A,
        created_at="2026-08-09T20:30:00+08:00",
    )
    assert replay == release

    later_duplicate_release = build_leakage_component_release(
        split_manifest=manifest,
        memberships=memberships,
        construction_policy_sha256=SHA_A,
        created_at="2026-08-09T20:31:00+08:00",
    )
    with pytest.raises(ValueError, match="case appears in more than one"):
        assert_leakage_releases_disjoint(release, later_duplicate_release)


def test_missing_axis_and_too_few_components_fail_closed() -> None:
    manifest, memberships = _pilot()
    missing_axis = tuple(
        item
        for item in memberships
        if not (
            item.case_id == "pilot-case-00"
            and item.axis is LeakageAxis.STRUCTURE_FINGERPRINT
        )
    )
    with pytest.raises(ValueError, match="all required leakage axes"):
        build_leakage_component_release(
            split_manifest=manifest,
            memberships=missing_axis,
            construction_policy_sha256=SHA_A,
            created_at="2026-08-09T20:30:00+08:00",
        )

    collapsed = tuple(
        item.model_copy(
            update={"group_id": "one-composition-family"}
        )
        if item.axis is LeakageAxis.COMPOSITION_FAMILY
        else item
        for item in memberships
    )
    collapsed_groups: dict[str, set[str]] = {}
    for item in collapsed:
        collapsed_groups.setdefault(item.case_id, set()).add(item.group_id)
    values = manifest.model_dump(
        mode="python", exclude={"manifest_id", "manifest_sha256"}
    )
    values["ood_holdout_families"] = manifest.ood_holdout_families
    values["cases"] = tuple(
        case.model_copy(
            update={"leakage_group_ids": tuple(sorted(collapsed_groups[case.case_id]))}
        )
        for case in manifest.cases
    )
    collapsed_manifest = _identified(
        BenchmarkSplitManifestV1,
        id_field="manifest_id",
        sha_field="manifest_sha256",
        prefix="split-manifest",
        values=values,
    )
    with pytest.raises(ValueError, match="at least ten independent components"):
        build_leakage_component_release(
            split_manifest=collapsed_manifest,
            memberships=collapsed,
            construction_policy_sha256=SHA_A,
            created_at="2026-08-09T20:30:00+08:00",
        )


def test_main_release_closes_component_minima_and_every_ood_holdout() -> None:
    manifest, memberships = _main()
    release = build_leakage_component_release(
        split_manifest=manifest,
        memberships=memberships,
        construction_policy_sha256=SHA_A,
        created_at="2026-08-09T20:30:00+08:00",
    )
    assert len(release.components) == 40
    assert_leakage_split_closure(split_manifest=manifest, release=release)

    tampered = release.model_dump(mode="python")
    tampered["components"] = tuple(reversed(release.components))
    with pytest.raises(ValidationError, match="component-ID sorted"):
        LeakageComponentReleaseV1.model_validate(tampered)


def test_main_manifest_rejects_an_ood_case_without_holdout_membership() -> None:
    manifest, _ = _main()
    values = manifest.model_dump(
        mode="python", exclude={"manifest_id", "manifest_sha256"}
    )
    values["ood_holdout_families"] = manifest.ood_holdout_families
    ood = next(case for case in manifest.cases if case.split is BenchmarkSplit.LOCKED_OOD)
    values["cases"] = tuple(
        case.model_copy(
            update={
                "leakage_group_ids": tuple(
                    group_id
                    for group_id in case.leakage_group_ids
                    if not group_id.startswith("ood-holdout-")
                )
            }
        )
        if case.case_id == ood.case_id
        else case
        for case in manifest.cases
    )
    with pytest.raises(ValidationError, match="every locked OOD case"):
        _identified(
            BenchmarkSplitManifestV1,
            id_field="manifest_id",
            sha_field="manifest_sha256",
            prefix="split-manifest",
            values=values,
        )


def test_main_manifest_rejects_article_axis_as_an_ood_holdout() -> None:
    manifest, _ = _main()
    values = manifest.model_dump(mode="python", round_trip=True)
    values["ood_holdout_families"][0]["axis"] = "ARTICLE_OR_SOURCE_FAMILY"

    with pytest.raises(ValidationError, match="OodHoldoutAxis"):
        BenchmarkSplitManifestV1.model_validate(values)


def test_v2_pilot_replays_canonical_groups_provenance_and_components() -> None:
    fixture = _v2_pilot()
    release = build_leakage_component_release_v2(
        cases=fixture["cases"],
        split_manifest=fixture["manifest"],
        grouping_algorithms=tuple(reversed(fixture["algorithms"])),
        grouping_runs=tuple(reversed(fixture["runs"])),
        grouping_assignments=tuple(reversed(fixture["assignments"])),
        created_at="2026-08-09T20:30:00+08:00",
    )
    assert isinstance(release, LeakageComponentReleaseV2)
    assert len(release.memberships) == 150
    assert len(release.components) == 10
    assert len(component_assignments_v2(release)) == 30
    assert_pilot_leakage_v2(
        cases=fixture["cases"],
        split_manifest=fixture["manifest"],
        release=release,
    )
    assert_leakage_split_closure_v2(
        cases=fixture["cases"],
        split_manifest=fixture["manifest"],
        release=release,
    )

    replay = build_leakage_component_release_v2(
        cases=tuple(reversed(fixture["cases"])),
        split_manifest=fixture["manifest"],
        grouping_algorithms=fixture["algorithms"],
        grouping_runs=fixture["runs"],
        grouping_assignments=fixture["assignments"],
        created_at="2026-08-09T20:30:00+08:00",
    )
    assert replay == release


def test_v2_rejects_readdressed_membership_provenance_and_future_run() -> None:
    fixture = _v2_pilot()
    release = build_leakage_component_release_v2(
        cases=fixture["cases"],
        split_manifest=fixture["manifest"],
        grouping_algorithms=fixture["algorithms"],
        grouping_runs=fixture["runs"],
        grouping_assignments=fixture["assignments"],
        created_at="2026-08-09T20:30:00+08:00",
    )
    values = release.model_dump(
        mode="python", exclude={"release_id", "release_sha256"}
    )
    values["memberships"][0]["provenance_sha256"] = SHA_B
    forged_sha256 = canonical_sha256(values)
    forged = LeakageComponentReleaseV2.model_validate(
        {
            **values,
            "release_sha256": forged_sha256,
            "release_id": deterministic_id(
                "leakage-release-v2", {"release_sha256": forged_sha256}
            ),
        }
    )
    with pytest.raises(ValueError, match="membership provenance does not replay"):
        assert_pilot_leakage_v2(
            cases=fixture["cases"],
            split_manifest=fixture["manifest"],
            release=forged,
        )

    future_values = release.model_dump(
        mode="python", exclude={"release_id", "release_sha256"}
    )
    future_values["created_at"] = "2026-08-09T20:10:30+08:00"
    future_sha256 = canonical_sha256(future_values)
    with pytest.raises(ValidationError, match="predates a referenced grouping run"):
        LeakageComponentReleaseV2.model_validate(
            {
                **future_values,
                "release_sha256": future_sha256,
                "release_id": deterministic_id(
                    "leakage-release-v2", {"release_sha256": future_sha256}
                ),
            }
        )


@pytest.mark.parametrize(
    ("axis", "formula_overrides", "fake_group"),
    (
        (
            LeakageAxis.COMPOSITION_FAMILY,
            {1: "FLi"},
            "caller-split-same-formula",
        ),
        (
            LeakageAxis.MECHANISM_FAMILY,
            {},
            "caller-split-same-mechanism",
        ),
        (
            LeakageAxis.ARTICLE_OR_SOURCE_FAMILY,
            {},
            "caller-disconnects-shared-source",
        ),
    ),
)
def test_v2_rejects_caller_groups_for_formula_mechanism_and_source(
    axis: LeakageAxis,
    formula_overrides: dict[int, str],
    fake_group: str,
) -> None:
    fixture = _v2_pilot(
        fake_groups={(1, axis): fake_group},
        formula_overrides=formula_overrides,
    )
    with pytest.raises(
        ValueError, match="full case leakage group IDs differ from canonical derivation"
    ):
        build_leakage_component_release_v2(
            cases=fixture["cases"],
            split_manifest=fixture["manifest"],
            grouping_algorithms=fixture["algorithms"],
            grouping_runs=fixture["runs"],
            grouping_assignments=fixture["assignments"],
            created_at="2026-08-09T20:30:00+08:00",
        )


def test_v2_rejects_missing_duplicate_foreign_and_drifted_structure_artifacts() -> None:
    fixture = _v2_pilot()
    common = {
        "cases": fixture["cases"],
        "split_manifest": fixture["manifest"],
        "grouping_algorithms": fixture["algorithms"],
        "grouping_runs": fixture["runs"],
        "created_at": "2026-08-09T20:30:00+08:00",
    }
    with pytest.raises(ValueError, match="do not exactly cover cases and axes"):
        build_leakage_component_release_v2(
            **common,
            grouping_assignments=fixture["assignments"][:-1],
        )
    with pytest.raises(ValueError, match="duplicate structure assignment"):
        build_leakage_component_release_v2(
            **common,
            grouping_assignments=(
                *fixture["assignments"],
                fixture["assignments"][0],
            ),
        )

    first = fixture["assignments"][0]
    foreign_values = first.model_dump(
        mode="python", exclude={"assignment_id", "assignment_sha256"}
    )
    foreign_values.update(
        {
            "case_id": "foreign-case",
            "case_sha256": SHA_A,
            "structure_sha256": SHA_B,
        }
    )
    foreign = _identified(
        StructureGroupingAssignmentV2,
        id_field="assignment_id",
        sha_field="assignment_sha256",
        prefix="structure-group-assignment",
        values=foreign_values,
    )
    with pytest.raises(ValueError, match="foreign case"):
        build_leakage_component_release_v2(
            **common,
            grouping_assignments=(foreign, *fixture["assignments"][1:]),
        )

    algorithm = fixture["algorithms"][0]
    drifted_values = algorithm.model_dump(
        mode="python", exclude={"algorithm_id", "algorithm_sha256"}
    )
    drifted_values["configuration_sha256"] = SHA_B
    drifted = _identified(
        StructureGroupingAlgorithmV2,
        id_field="algorithm_id",
        sha_field="algorithm_sha256",
        prefix="structure-group-algorithm",
        values=drifted_values,
    )
    with pytest.raises(ValueError, match="algorithm drift"):
        build_leakage_component_release_v2(
            **{
                **common,
                "grouping_algorithms": (
                    drifted,
                    *fixture["algorithms"][1:],
                ),
            },
            grouping_assignments=fixture["assignments"],
        )


def test_v2_rejects_main_fake_independence_when_all_mechanisms_are_identical() -> None:
    fixture = _v2_main_with_fake_mechanism_independence()
    with pytest.raises(
        ValueError, match="full case leakage group IDs differ from canonical derivation"
    ):
        build_leakage_component_release_v2(
            cases=fixture["cases"],
            split_manifest=fixture["manifest"],
            grouping_algorithms=fixture["algorithms"],
            grouping_runs=fixture["runs"],
            grouping_assignments=fixture["assignments"],
            created_at="2026-08-09T20:30:00+08:00",
        )


V3_FORMULAS = tuple(
    "".join(pair)
    for pair in combinations(
        ("H", "He", "Li", "Be", "B", "C", "N", "O", "F", "Ne"), 2
    )
)

V3_REVIEW_CRITERIA = tuple(
    sorted(
        (
            "Evidence supports the stated shared invariant",
            "Lineage is finer than the broad sampling taxonomy",
            "Transfer route is scientifically reusable across cases",
        )
    )
)


def _v3_private_person(index: int) -> MechanismLineageCuratorDeclarationV3:
    return MechanismLineageCuratorDeclarationV3(
        curator_id=f"private-lineage-person-{index}",
        opaque_natural_person_ref=f"opaque-human-{index}",
        natural_person_commitment_sha256=canonical_sha256(
            {"natural-person": index}
        ),
        identity_evidence_uri=f"private://lineage-governance/person-{index}",
        identity_evidence_sha256=canonical_sha256(
            {"identity-evidence": index}
        ),
        institutional_unit=f"Independent curation unit {index}",
        conflict_declaration=f"Private conflict declaration {index}",
    )


def _v3_formal_registry(
    definitions: tuple[MechanismLineageDefinitionV3, ...],
    *,
    taxonomy_version: str = "flatband-lineage-taxonomy-test-v3",
) -> tuple[MechanismLineageRegistryV3, MechanismLineageCurationReleaseV3]:
    policy = build_mechanism_lineage_curation_policy_v3(
        taxonomy_version=taxonomy_version,
        taxonomy_scope=(
            "Global flat-band mechanism lineage taxonomy curated before case selection"
        ),
        definition_review_criteria=V3_REVIEW_CRITERIA,
        sealed_at="2026-08-09T20:01:00+08:00",
    )
    curators = (_v3_private_person(1), _v3_private_person(2))
    roster = build_mechanism_lineage_curator_roster_v3(
        policy=policy,
        curators=curators,
        adjudicators=(_v3_private_person(3),),
        independence_review=(
            "Private identity evidence binds three injective natural-person commitments"
        ),
        sealed_at="2026-08-09T20:02:00+08:00",
    )
    reviews = tuple(
        build_mechanism_lineage_definition_review_v3(
            policy=policy,
            definition=definition,
            curator_id=curator.curator_id,
            decision=MechanismLineageReviewDecisionV3.INCLUDE,
            criterion_findings=V3_REVIEW_CRITERIA,
            rationale=(
                f"Private independent review supports {definition.lineage_id}"
            ),
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
        sealed_at="2026-08-09T20:12:00+08:00",
    )
    curation = build_mechanism_lineage_curation_release_v3(
        registry=registry,
        policy=policy,
        roster=roster,
        review_manifest=review_manifest,
        assembled_at="2026-08-09T20:12:30+08:00",
    )
    return registry, curation


def _v3_source(component_index: int, *, source_id: str = "crossref") -> SourceRecordRefV1:
    return SourceRecordRefV1(
        source_id=source_id,
        source_record_id=f"work-lineage-{component_index:03d}",
        canonical_url=f"https://doi.org/10.7000/lineage-{component_index:03d}",
        source_version="2026-08-09",
        license_expression="CC0-1.0",
        accessed_at="2026-08-09T20:00:00+08:00",
        raw_sha256=canonical_sha256(
            {"component": component_index, "source": source_id}
        ),
        public_redistribution_allowed=True,
    )


def _v3_evidence_ref(record: SourceRecordRefV1) -> MechanismLineageEvidenceRefV3:
    return MechanismLineageEvidenceRefV3(
        source_id=record.source_id,
        source_record_id=record.source_record_id,
        source_record_raw_sha256=record.raw_sha256,
    )


def _v3_structure_group_id(
    algorithm: StructureGroupingAlgorithmV2, key: str
) -> str:
    axis = {
        LeakageAxis.STRUCTURE_PROTOTYPE: LeakageAxisV3.STRUCTURE_PROTOTYPE,
        LeakageAxis.STRUCTURE_FINGERPRINT: LeakageAxisV3.STRUCTURE_FINGERPRINT,
    }[algorithm.axis]
    preimage = canonical_json_bytes(
        {
            "kind": "structure-group",
            "axis": axis.value,
            "algorithm_id": algorithm.algorithm_id,
            "algorithm_sha256": algorithm.algorithm_sha256,
            "canonical_group_key": key,
        }
    ).decode("utf-8")
    return deterministic_id(
        "leakage-group-v3",
        {"axis": axis.value, "canonical_preimage": preimage},
    )


def _v3_inputs(
    *,
    main: bool,
    broad_mechanism_holdout: bool = False,
    collapse_first_two_components: bool = False,
    extra_unused_lineage: bool = False,
    formal_curation: bool = True,
    singleton_lineages: bool = False,
    pilot_round: int = 1,
    pilot_component_offset: int = 0,
    global_definition_count: int | None = None,
    governance_bundle: dict[str, object] | None = None,
) -> dict[str, object]:
    algorithms = tuple(
        _v2_algorithm(axis)
        for axis in sorted(
            (
                LeakageAxis.STRUCTURE_FINGERPRINT,
                LeakageAxis.STRUCTURE_PROTOTYPE,
            ),
            key=lambda value: value.value,
        )
    )
    if main:
        if pilot_round != 1 or pilot_component_offset or governance_bundle is not None:
            raise ValueError("Pilot-only fixture controls cannot be used for Main")
        split_specs = (
            (BenchmarkSplit.DEVELOPMENT, 60, 0),
            (BenchmarkSplit.LOCKED_IID, 30, 20),
            (BenchmarkSplit.LOCKED_OOD, 30, 30),
        )
        manifest_kind = SplitManifestKind.MAIN_120
        component_count = 40
    else:
        if pilot_round not in {1, 2}:
            raise ValueError("Pilot review round must be 1 or 2")
        pilot_split = (
            BenchmarkSplit.PILOT_R1
            if pilot_round == 1
            else BenchmarkSplit.PILOT_R2
        )
        split_specs = ((pilot_split, 30, pilot_component_offset),)
        manifest_kind = (
            SplitManifestKind.PILOT_R1
            if pilot_round == 1
            else SplitManifestKind.PILOT_R2
        )
        component_count = 30 if singleton_lineages else 10

    selected_component_start = pilot_component_offset if not main else 0
    selected_components = tuple(
        range(selected_component_start, selected_component_start + component_count)
    )
    sources = {
        component: _v3_source(component)
        for component in selected_components
    }
    if governance_bundle is None:
        broad_by_component: dict[int, MechanismFamily] = {}
        definitions = []
        minimum_definition_count = (
            component_count
            if main
            else pilot_component_offset + component_count
        )
        definition_count = (
            global_definition_count
            if global_definition_count is not None
            else minimum_definition_count + int(extra_unused_lineage)
        )
        if definition_count < minimum_definition_count:
            raise ValueError("global registry omits a selected Pilot lineage")
        for component in range(definition_count):
            if not main:
                broad = tuple(MechanismFamily)[component % len(MechanismFamily)]
            elif broad_mechanism_holdout and component in {30, 31}:
                broad = MechanismFamily.LINE_GRAPH
            else:
                broad = MechanismFamily.LATTICE_INTERFERENCE
            broad_by_component[component] = broad
            # Registry taxonomy evidence is global and need not be a material's
            # own source record.
            taxonomy_ref = MechanismLineageEvidenceRefV3(
                source_id="crossref",
                source_record_id=f"taxonomy-reference-{component:03d}",
                source_record_raw_sha256=canonical_sha256(
                    {"taxonomy-reference": component}
                ),
            )
            definitions.append(
                build_mechanism_lineage_definition_v3(
                    broad_mechanism_family=broad,
                    source_mechanism=f"Curated source mechanism lineage {component}",
                    shared_invariant=f"Curated shared invariant lineage {component}",
                    transfer_route_family=f"Curated transfer route family {component}",
                    taxonomy_evidence_refs=(taxonomy_ref,),
                )
            )
        if formal_curation:
            registry, curation = _v3_formal_registry(tuple(definitions))
        else:
            registry = build_mechanism_lineage_registry_v3(
                taxonomy_version="flatband-lineage-taxonomy-test-v3",
                curation_policy_sha256=canonical_sha256("caller-policy-v3"),
                evidence_review_manifest_sha256=canonical_sha256(
                    "caller-review-v3"
                ),
                curator_roster_sha256=canonical_sha256("caller-roster-v3"),
                definitions=tuple(definitions),
                sealed_at="2026-08-09T20:12:00+08:00",
            )
            curation = None
    else:
        registry = governance_bundle["registry"]
        curation = governance_bundle["curation"]
        broad_by_component = governance_bundle["broad_by_component"]
    lineage_by_component = {
        component: next(
            item
            for item in registry.definitions
            if item.broad_mechanism_family is broad_by_component[component]
            and item.source_mechanism
            == f"Curated source mechanism lineage {component}"
        )
        for component in selected_components
    }

    cases: list[FlatBandBenchmarkCaseV1] = []
    split_by_case: dict[str, BenchmarkSplit] = {}
    component_by_case: dict[str, int] = {}
    structure_keys: dict[tuple[LeakageAxis, str], str] = {}
    local_index_by_case: dict[str, int] = {}
    for split, count, component_offset in split_specs:
        for local_index in range(count):
            component = component_offset + (
                local_index if singleton_lineages else local_index // 3
            )
            source = sources[component]
            structure_groups = []
            for algorithm in algorithms:
                key_component = (
                    0
                    if collapse_first_two_components and component == 1
                    else component
                )
                key = f"{algorithm.axis.value.casefold()}-{key_component:03d}"
                structure_groups.append((algorithm, key))
            group_ids = derive_leakage_group_ids_v3(
                formula=V3_FORMULAS[component],
                primary_mechanism_stratum=broad_by_component[component],
                source_records=(source,),
                structure_groups=tuple(structure_groups),
                mechanism_lineage_registry=registry,
                mechanism_lineage_id=lineage_by_component[component].lineage_id,
            )
            values = {
                "parent_label": f"V3 parent {split.value} {local_index}",
                "formula": V3_FORMULAS[component],
                "structure_sha256": canonical_sha256(
                    {"split": split.value, "local_index": local_index}
                ),
                "source_records": (source,),
                "target_class": (
                    TargetBandClass.FB100
                    if local_index < count // 2
                    else TargetBandClass.NB300
                ),
                "dimensionality": (
                    Dimensionality.TWO_D
                    if local_index % 2 == 0
                    else Dimensionality.THREE_D
                ),
                "frozen_request": f"Evaluate formal V3 case {split.value} {local_index}",
                "frozen_requirement_sha256": canonical_sha256(
                    {"V3 requirement": split.value, "local_index": local_index}
                ),
                "hard_constraints": (),
                "soft_preferences": (),
                "forbidden_transformations": (),
                "seed_evidence": (),
                "primary_mechanism_stratum": broad_by_component[component],
                "leakage_group_ids": group_ids,
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
            split_by_case[case.case_id] = split
            component_by_case[case.case_id] = component
            local_index_by_case[case.case_id] = local_index
            for algorithm, key in structure_groups:
                structure_keys[(algorithm.axis, case.case_id)] = key

    ordered_cases = tuple(sorted(cases, key=lambda item: item.case_id))
    holdout_families: set[tuple[OodHoldoutAxis, str]] = set()
    holdout_by_case: dict[str, set[tuple[OodHoldoutAxis, str]]] = defaultdict(set)
    if main:
        prototype = next(
            item
            for item in algorithms
            if item.axis is LeakageAxis.STRUCTURE_PROTOTYPE
        )
        for case in ordered_cases:
            if split_by_case[case.case_id] is not BenchmarkSplit.LOCKED_OOD:
                continue
            component = component_by_case[case.case_id]
            if broad_mechanism_holdout and component in {30, 31}:
                key = (
                    OodHoldoutAxis.MECHANISM_FAMILY,
                    mechanism_holdout_taxonomy_group_id(
                        MechanismFamily.LINE_GRAPH
                    ),
                )
            else:
                key = (
                    OodHoldoutAxis.STRUCTURE_PROTOTYPE,
                    _v3_structure_group_id(
                        prototype,
                        structure_keys[
                            (LeakageAxis.STRUCTURE_PROTOTYPE, case.case_id)
                        ],
                    ),
                )
            holdout_families.add(key)
            holdout_by_case[case.case_id].add(key)

    split_cases = tuple(
        SplitCaseRefV2(
            case_id=case.case_id,
            case_sha256=case.case_sha256,
            split=split_by_case[case.case_id],
            target_class=case.target_class,
            dimensionality=case.dimensionality,
            primary_mechanism_stratum=case.primary_mechanism_stratum,
            independence_group_ids=case.leakage_group_ids,
            holdout_memberships=tuple(
                CaseHoldoutMembershipV2(axis=axis, group_id=group_id)
                for axis, group_id in sorted(
                    holdout_by_case[case.case_id],
                    key=lambda item: (item[0].value, item[1]),
                )
            ),
        )
        for case in ordered_cases
    )
    manifest = _identified(
        BenchmarkSplitManifestV2,
        id_field="manifest_id",
        sha_field="manifest_sha256",
        prefix="split-manifest-v2",
        values={
            "manifest_kind": manifest_kind,
            "split_seed": 91 if main else 72 + pilot_round,
            "cases": split_cases,
            "ood_holdout_families": tuple(
                OodHoldoutFamilyV1(axis=axis, group_id=group_id)
                for axis, group_id in sorted(
                    holdout_families,
                    key=lambda item: (item[0].value, item[1]),
                )
            ),
        },
    )
    runs, structure_assignments = _v2_grouping_artifacts(
        cases=ordered_cases,
        algorithms=algorithms,
        group_keys=structure_keys,
    )
    lineage_assignments = tuple(
        sorted(
            (
                build_mechanism_lineage_assignment_v3(
                    registry=registry,
                    case=case,
                    lineage_id=lineage_by_component[
                        component_by_case[case.case_id]
                    ].lineage_id,
                    case_evidence_refs=(
                        _v3_evidence_ref(
                            sources[component_by_case[case.case_id]]
                        ),
                    ),
                    assignment_basis_sha256=canonical_sha256(
                        {
                            "case_id": case.case_id,
                            "lineage": lineage_by_component[
                                component_by_case[case.case_id]
                            ].lineage_id,
                        }
                    ),
                    assigned_at="2026-08-09T20:13:00+08:00",
                )
                for case in ordered_cases
            ),
            key=lambda item: (item.case_id, item.assignment_id),
        )
    )
    return {
        "cases": ordered_cases,
        "manifest": manifest,
        "algorithms": algorithms,
        "runs": runs,
        "structure_assignments": structure_assignments,
        "registry": registry,
        "curation": curation,
        "lineage_assignments": lineage_assignments,
        "component_by_case": component_by_case,
        "broad_by_component": broad_by_component,
    }


V3_ASSIGNMENT_REVIEW_CRITERIA = tuple(
    sorted(
        (
            "All candidate source records support the proposed lineage assignment",
            "Fine-grained lineage matches the case mechanism evidence",
            "Proposed lineage is not merely the broad sampling family",
        )
    )
)


def _v3_assignment_curation(
    fixture: dict[str, object],
    *,
    decisions_by_case: dict[
        str,
        tuple[
            MechanismLineageAssignmentDecisionV3,
            MechanismLineageAssignmentDecisionV3,
        ],
    ]
    | None = None,
) -> dict[str, object]:
    registry = fixture["registry"]
    definition_curation = fixture["curation"]
    cases = fixture["cases"]
    assignment_by_case = {
        item.case_id: item for item in fixture["lineage_assignments"]
    }
    policy = build_mechanism_lineage_assignment_curation_policy_v3(
        registry=registry,
        definition_curation_release=definition_curation,
        assignment_review_criteria=V3_ASSIGNMENT_REVIEW_CRITERIA,
        sealed_at="2026-08-09T20:12:31+08:00",
    )
    reviewers = (_v3_private_person(11), _v3_private_person(12))
    roster = build_mechanism_lineage_assignment_reviewer_roster_v3(
        policy=policy,
        reviewers=reviewers,
        adjudicators=(_v3_private_person(13),),
        independence_review=(
            "Private identity evidence binds three assignment-level natural persons"
        ),
        sealed_at="2026-08-09T20:12:32+08:00",
    )
    proposals = tuple(
        build_mechanism_lineage_assignment_proposal_v3(
            candidate_id=deterministic_id(
                "test-candidate-v3", {"case_id": case.case_id}
            ),
            candidate_sha256=canonical_sha256(
                {"test_candidate_case_sha256": case.case_sha256}
            ),
            case=case,
            registry=registry,
            definition_curation_release=definition_curation,
            lineage_id=assignment_by_case[case.case_id].lineage_id,
            assignment_basis_sha256=(
                assignment_by_case[case.case_id].assignment_basis_sha256
            ),
            proposed_at="2026-08-09T20:12:34+08:00",
        )
        for case in cases
    )
    universe = build_mechanism_lineage_assignment_candidate_universe_v3(
        registry=registry,
        definition_curation_release=definition_curation,
        policy=policy,
        roster=roster,
        proposals=proposals,
        sealed_at="2026-08-09T20:12:40+08:00",
    )
    decisions = decisions_by_case or {}
    reviews = tuple(
        build_mechanism_lineage_assignment_review_v3(
            policy=policy,
            roster=roster,
            candidate_universe=universe,
            proposal=proposal,
            reviewer_id=reviewer.curator_id,
            decision=decisions.get(
                proposal.case.case_id,
                (
                    MechanismLineageAssignmentDecisionV3.ACCEPT,
                    MechanismLineageAssignmentDecisionV3.ACCEPT,
                ),
            )[reviewer_index],
            criterion_findings=V3_ASSIGNMENT_REVIEW_CRITERIA,
            rationale=(
                f"Private exact assignment review for {proposal.proposal_id}"
            ),
            reviewed_at="2026-08-09T20:12:50+08:00",
        )
        for proposal in universe.proposals
        for reviewer_index, reviewer in enumerate(reviewers)
    )
    reviews_by_proposal = {
        proposal.proposal_id: tuple(
            item for item in reviews if item.proposal_id == proposal.proposal_id
        )
        for proposal in universe.proposals
    }
    adjudications = tuple(
        build_mechanism_lineage_assignment_adjudication_v3(
            policy=policy,
            roster=roster,
            candidate_universe=universe,
            proposal=proposal,
            reviews=reviews_by_proposal[proposal.proposal_id],
            adjudicator_id=roster.adjudicators[0].curator_id,
            final_decision=MechanismLineageAssignmentDecisionV3.ACCEPT,
            rationale="Independent assignment adjudication after divergent reviews",
            adjudicated_at="2026-08-09T20:12:52+08:00",
        )
        for proposal in universe.proposals
        if len(
            {
                item.decision
                for item in reviews_by_proposal[proposal.proposal_id]
            }
        )
        == 2
    )
    manifest = build_mechanism_lineage_assignment_review_manifest_v3(
        policy=policy,
        roster=roster,
        candidate_universe=universe,
        reviews=reviews,
        adjudications=adjudications,
        sealed_at="2026-08-09T20:12:55+08:00",
    )
    release = build_mechanism_lineage_assignment_curation_release_v3(
        registry=registry,
        definition_curation_release=definition_curation,
        policy=policy,
        roster=roster,
        candidate_universe=universe,
        review_manifest=manifest,
        assembled_at="2026-08-09T20:13:00+08:00",
    )
    public_assignments = derive_formal_mechanism_lineage_assignments_v3(
        registry=registry,
        definition_curation_release=definition_curation,
        assignment_curation_release=release,
    )
    return {
        "policy": policy,
        "roster": roster,
        "proposals": universe.proposals,
        "universe": universe,
        "reviews": manifest.reviews,
        "adjudications": manifest.adjudications,
        "manifest": manifest,
        "release": release,
        "public_assignments": public_assignments,
    }


def _build_v3_release(fixture: dict[str, object]) -> LeakageComponentReleaseV3:
    return build_leakage_component_release_v3(
        cases=fixture["cases"],
        split_manifest=fixture["manifest"],
        grouping_algorithms=fixture["algorithms"],
        grouping_runs=fixture["runs"],
        grouping_assignments=fixture["structure_assignments"],
        mechanism_lineage_registry=fixture["registry"],
        mechanism_lineage_assignments=fixture["lineage_assignments"],
        created_at="2026-08-09T20:30:00+08:00",
    )


def test_v3_pilot_replays_ten_real_components_without_broad_mechanism_edges() -> None:
    fixture = _v3_inputs(main=False)
    release = _build_v3_release(fixture)
    assert len(release.components) == 10
    assert len(component_assignments_v3(release)) == 30
    assert LeakageAxisV3.MECHANISM_LINEAGE in {
        item.axis for item in release.group_definitions
    }
    assert not {
        mechanism_holdout_taxonomy_group_id(value) for value in MechanismFamily
    } & {item.group_id for item in release.group_definitions}
    assert_pilot_leakage_v3(
        cases=fixture["cases"],
        split_manifest=fixture["manifest"],
        release=release,
        lineage_curation_release=fixture["curation"],
    )

    global_registry_fixture = _v3_inputs(main=False, extra_unused_lineage=True)
    global_registry_release = _build_v3_release(global_registry_fixture)
    used_lineages = {
        item.lineage_id
        for item in global_registry_release.mechanism_lineage_assignments
    }
    assert len(global_registry_release.mechanism_lineage_registry.definitions) == 11
    assert len(used_lineages) == 10


def test_v3_main_allows_one_broad_family_and_meets_honest_20_10_10() -> None:
    fixture = _v3_inputs(main=True)
    release = _build_v3_release(fixture)
    assert {
        item.primary_mechanism_stratum for item in fixture["cases"]
    } == {MechanismFamily.LATTICE_INTERFERENCE}
    split_by_case = {
        item.case_id: item.split for item in fixture["manifest"].cases
    }
    component_counts = {
        split: sum(
            split_by_case[component.case_ids[0]] is split
            for component in release.components
        )
        for split in (
            BenchmarkSplit.DEVELOPMENT,
            BenchmarkSplit.LOCKED_IID,
            BenchmarkSplit.LOCKED_OOD,
        )
    }
    assert component_counts == {
        BenchmarkSplit.DEVELOPMENT: 20,
        BenchmarkSplit.LOCKED_IID: 10,
        BenchmarkSplit.LOCKED_OOD: 10,
    }
    assert_main_leakage_v3(
        cases=fixture["cases"],
        split_manifest=fixture["manifest"],
        release=release,
        lineage_curation_release=fixture["curation"],
    )


def test_v3_broad_holdout_is_taxonomy_only_and_does_not_merge_ood_cases() -> None:
    fixture = _v3_inputs(main=True, broad_mechanism_holdout=True)
    release = _build_v3_release(fixture)
    manifest = fixture["manifest"]
    broad = next(
        item
        for item in manifest.ood_holdout_families
        if item.axis is OodHoldoutAxis.MECHANISM_FAMILY
    )
    broad_cases = tuple(
        item
        for item in manifest.cases
        if any(
            membership.axis is OodHoldoutAxis.MECHANISM_FAMILY
            for membership in item.holdout_memberships
        )
    )
    assert len(broad_cases) == 6
    assert broad.group_id not in {item.group_id for item in release.group_definitions}
    assert len(
        {
            component_assignments_v3(release)[item.case_id]
            for item in broad_cases
        }
    ) == 2
    assert_main_leakage_v3(
        cases=fixture["cases"],
        split_manifest=manifest,
        release=release,
        lineage_curation_release=fixture["curation"],
    )


def test_v3_rejects_fake_lineage_identity_duplicate_preimage_and_whitespace_alias() -> None:
    fixture = _v3_inputs(main=False)
    registry = fixture["registry"]
    case = fixture["cases"][0]
    algorithms = fixture["algorithms"]
    assignments = {
        (item.axis, item.case_id): item
        for item in fixture["structure_assignments"]
    }
    with pytest.raises(ValueError, match="outside registry"):
        derive_leakage_group_ids_v3(
            formula=case.formula,
            primary_mechanism_stratum=case.primary_mechanism_stratum,
            source_records=case.source_records,
            structure_groups=tuple(
                (
                    algorithm,
                    assignments[(algorithm.axis, case.case_id)].canonical_group_key,
                )
                for algorithm in algorithms
            ),
            mechanism_lineage_registry=registry,
            mechanism_lineage_id="caller-minted-lineage",
        )

    definition = registry.definitions[0]
    forged = definition.model_copy(update={"lineage_id": "caller-alias"})
    with pytest.raises(ValidationError, match="lineage_id"):
        MechanismLineageDefinitionV3.model_validate(
            forged.model_dump(mode="python", round_trip=True)
        )
    with pytest.raises(ValueError, match="whitespace-canonical"):
        build_mechanism_lineage_definition_v3(
            broad_mechanism_family=MechanismFamily.LATTICE_INTERFERENCE,
            source_mechanism="same  mechanism",
            shared_invariant="same invariant",
            transfer_route_family="same route",
            taxonomy_evidence_refs=definition.taxonomy_evidence_refs,
        )
    with pytest.raises(ValidationError, match="sorted and unique"):
        build_mechanism_lineage_registry_v3(
            taxonomy_version="duplicate-test",
            curation_policy_sha256=SHA_A,
            evidence_review_manifest_sha256=SHA_A,
            curator_roster_sha256=SHA_A,
            definitions=(definition, definition),
            sealed_at="2026-08-09T20:12:00+08:00",
        )
    same_science_new_citation = build_mechanism_lineage_definition_v3(
        broad_mechanism_family=definition.broad_mechanism_family,
        source_mechanism=definition.source_mechanism,
        shared_invariant=definition.shared_invariant,
        transfer_route_family=definition.transfer_route_family,
        taxonomy_evidence_refs=(
            MechanismLineageEvidenceRefV3(
                source_id="crossref",
                source_record_id="alternate-taxonomy-citation",
                source_record_raw_sha256=SHA_B,
            ),
        ),
    )
    assert same_science_new_citation.lineage_id != definition.lineage_id
    assert (
        same_science_new_citation.scientific_preimage_sha256
        == definition.scientific_preimage_sha256
    )
    with pytest.raises(ValidationError, match="duplicates a mechanism scientific preimage"):
        build_mechanism_lineage_registry_v3(
            taxonomy_version="duplicate-science-test",
            curation_policy_sha256=SHA_A,
            evidence_review_manifest_sha256=SHA_A,
            curator_roster_sha256=SHA_A,
            definitions=(definition, same_science_new_citation),
            sealed_at="2026-08-09T20:12:00+08:00",
        )

    true_assignment = next(
        item
        for item in fixture["lineage_assignments"]
        if item.case_id == case.case_id
    )
    with pytest.raises(ValueError, match="not a subset of case sources"):
        build_mechanism_lineage_assignment_v3(
            registry=registry,
            case=case,
            lineage_id=true_assignment.lineage_id,
            case_evidence_refs=(
                MechanismLineageEvidenceRefV3(
                    source_id="openalex",
                    source_record_id="foreign-work",
                    source_record_raw_sha256=SHA_B,
                ),
            ),
            assignment_basis_sha256=SHA_A,
            assigned_at="2026-08-09T20:13:00+08:00",
        )


def test_v3_rejects_readdressed_foreign_assignment_and_fake_component_count() -> None:
    fixture = _v3_inputs(main=False)
    original = fixture["lineage_assignments"][0]
    values = original.model_dump(
        mode="python", exclude={"assignment_id", "assignment_sha256"}
    )
    values["case_evidence_refs"] = original.case_evidence_refs
    values.update({"lineage_id": "foreign-lineage", "lineage_sha256": SHA_B})
    foreign = _identified(
        MechanismLineageAssignmentV3,
        id_field="assignment_id",
        sha_field="assignment_sha256",
        prefix="mechanism-lineage-assignment-v3",
        values=values,
    )
    with pytest.raises(ValueError, match="foreign definition"):
        build_leakage_component_release_v3(
            cases=fixture["cases"],
            split_manifest=fixture["manifest"],
            grouping_algorithms=fixture["algorithms"],
            grouping_runs=fixture["runs"],
            grouping_assignments=fixture["structure_assignments"],
            mechanism_lineage_registry=fixture["registry"],
            mechanism_lineage_assignments=(
                foreign,
                *fixture["lineage_assignments"][1:],
            ),
            created_at="2026-08-09T20:30:00+08:00",
        )

    collapsed = _v3_inputs(main=False, collapse_first_two_components=True)
    with pytest.raises(ValueError, match="at least ten components"):
        _build_v3_release(collapsed)


def test_split_v2_rejects_holdout_omission_taxonomy_edge_and_hidden_dev_hit() -> None:
    structure_fixture = _v3_inputs(main=True)
    manifest = structure_fixture["manifest"]
    cases = list(manifest.cases)
    ood_index = next(
        index
        for index, case in enumerate(cases)
        if case.split is BenchmarkSplit.LOCKED_OOD
    )
    cases[ood_index] = cases[ood_index].model_copy(
        update={"holdout_memberships": ()}
    )
    with pytest.raises(ValidationError, match="do not replay"):
        _identified(
            BenchmarkSplitManifestV2,
            id_field="manifest_id",
            sha_field="manifest_sha256",
            prefix="split-manifest-v2",
            values={
                **manifest.model_dump(
                    mode="python", exclude={"manifest_id", "manifest_sha256", "cases"}
                ),
                "cases": tuple(cases),
                "ood_holdout_families": manifest.ood_holdout_families,
            },
        )

    first = manifest.cases[0]
    broad_id = mechanism_holdout_taxonomy_group_id(
        first.primary_mechanism_stratum
    )
    with pytest.raises(ValidationError, match="cannot be an independence graph edge"):
        SplitCaseRefV2.model_validate(
            first.model_copy(
                update={
                    "independence_group_ids": tuple(
                        sorted((*first.independence_group_ids, broad_id))
                    )
                }
            ).model_dump(mode="python", round_trip=True)
        )

    broad_fixture = _v3_inputs(main=True, broad_mechanism_holdout=True)
    broad_manifest = broad_fixture["manifest"]
    altered = list(broad_manifest.cases)
    dev_index = next(
        index
        for index, case in enumerate(altered)
        if case.split is BenchmarkSplit.DEVELOPMENT
    )
    altered[dev_index] = altered[dev_index].model_copy(
        update={"primary_mechanism_stratum": MechanismFamily.LINE_GRAPH}
    )
    with pytest.raises(ValidationError, match="do not replay"):
        _identified(
            BenchmarkSplitManifestV2,
            id_field="manifest_id",
            sha_field="manifest_sha256",
            prefix="split-manifest-v2",
            values={
                **broad_manifest.model_dump(
                    mode="python", exclude={"manifest_id", "manifest_sha256", "cases"}
                ),
                "cases": tuple(altered),
                "ood_holdout_families": broad_manifest.ood_holdout_families,
            },
        )


def test_v3_cross_source_doi_alias_without_canonical_work_registry_is_no_go() -> None:
    fixture = _v3_inputs(main=False)
    case = fixture["cases"][0]
    crossref = case.source_records[0]
    openalex = SourceRecordRefV1(
        source_id="openalex",
        source_record_id="W123456789",
        canonical_url=crossref.canonical_url,
        source_version="2026-08-09",
        license_expression="CC0-1.0",
        accessed_at="2026-08-09T20:00:00+08:00",
        raw_sha256=canonical_sha256("openalex-alias"),
        public_redistribution_allowed=True,
    )
    assignment_by_key = {
        (item.axis, item.case_id): item
        for item in fixture["structure_assignments"]
    }
    lineage_id = next(
        item.lineage_id
        for item in fixture["registry"].definitions
        if item.broad_mechanism_family is case.primary_mechanism_stratum
    )
    with pytest.raises(ValueError, match="formal V3 is NO-GO"):
        derive_leakage_group_ids_v3(
            formula=case.formula,
            primary_mechanism_stratum=case.primary_mechanism_stratum,
            source_records=(crossref, openalex),
            structure_groups=tuple(
                (
                    algorithm,
                    assignment_by_key[
                        (algorithm.axis, case.case_id)
                    ].canonical_group_key,
                )
                for algorithm in fixture["algorithms"]
            ),
            mechanism_lineage_registry=fixture["registry"],
            mechanism_lineage_id=lineage_id,
        )


def test_v3_formal_global_registry_allows_thirty_scientific_singletons() -> None:
    fixture = _v3_inputs(main=False, singleton_lineages=True)
    release = _build_v3_release(fixture)

    assert len(release.components) == 30
    assert len(
        {item.lineage_id for item in release.mechanism_lineage_assignments}
    ) == 30
    assert_formal_mechanism_lineage_registry_v3(
        registry=fixture["registry"],
        curation_release=fixture["curation"],
    )
    assert_pilot_leakage_v3(
        cases=fixture["cases"],
        split_manifest=fixture["manifest"],
        release=release,
        lineage_curation_release=fixture["curation"],
    )

    public_registry = json.dumps(
        fixture["registry"].model_dump(mode="json"), sort_keys=True
    )
    assert "private-lineage-person" not in public_registry
    assert "rationale" not in public_registry
    assert fixture["curation"].private_custody_required is True
    assert fixture["curation"].public_release_allowed is False


def test_v3_assignment_curation_projects_exact_thirty_and_rejects_alternate() -> None:
    fixture = _v3_inputs(main=False, singleton_lineages=True)
    assignment_curation = _v3_assignment_curation(fixture)
    public_assignments = assignment_curation["public_assignments"]

    assert len(assignment_curation["universe"].proposals) == 30
    assert len(assignment_curation["reviews"]) == 60
    assert len(public_assignments) == 30
    assert public_assignments == fixture["lineage_assignments"]
    assert assignment_curation["release"].private_custody_required is True
    assert assignment_curation["release"].public_release_allowed is False
    assert_formal_mechanism_lineage_assignment_curation_v3(
        registry=fixture["registry"],
        definition_curation_release=fixture["curation"],
        assignment_curation_release=assignment_curation["release"],
        public_assignments=public_assignments,
    )

    proposal = assignment_curation["universe"].proposals[0]
    alternate = next(
        item
        for item in fixture["registry"].definitions
        if item.lineage_id != proposal.lineage_id
        and item.broad_mechanism_family
        is proposal.case.primary_mechanism_stratum
    )
    forged = build_mechanism_lineage_assignment_v3(
        registry=fixture["registry"],
        case=proposal.case,
        lineage_id=alternate.lineage_id,
        case_evidence_refs=proposal.case_evidence_refs,
        assignment_basis_sha256=proposal.assignment_basis_sha256,
        assigned_at=assignment_curation["release"].assembled_at,
    )
    forged_public = tuple(
        sorted(
            (
                forged if item.case_id == forged.case_id else item
                for item in public_assignments
            ),
            key=lambda item: (item.case_id, item.assignment_id),
        )
    )
    with pytest.raises(ValueError, match="exact accepted curation projection"):
        assert_formal_mechanism_lineage_assignment_curation_v3(
            registry=fixture["registry"],
            definition_curation_release=fixture["curation"],
            assignment_curation_release=assignment_curation["release"],
            public_assignments=forged_public,
        )


def test_v3_assignment_curation_exact_cover_adjudication_and_rejection() -> None:
    fixture = _v3_inputs(main=False, singleton_lineages=True)
    first_case_id = fixture["cases"][0].case_id
    second_case_id = fixture["cases"][1].case_id
    assignment_curation = _v3_assignment_curation(
        fixture,
        decisions_by_case={
            first_case_id: (
                MechanismLineageAssignmentDecisionV3.REJECT,
                MechanismLineageAssignmentDecisionV3.REJECT,
            ),
            second_case_id: (
                MechanismLineageAssignmentDecisionV3.ACCEPT,
                MechanismLineageAssignmentDecisionV3.REJECT,
            ),
        },
    )
    public_assignments = assignment_curation["public_assignments"]
    assert len(assignment_curation["universe"].proposals) == 30
    assert len(public_assignments) == 29
    assert first_case_id not in {item.case_id for item in public_assignments}
    assert len(assignment_curation["adjudications"]) == 1
    assert_formal_mechanism_lineage_assignment_curation_v3(
        registry=fixture["registry"],
        definition_curation_release=fixture["curation"],
        assignment_curation_release=assignment_curation["release"],
        public_assignments=public_assignments,
    )

    with pytest.raises(ValueError, match="exactly two reviewers"):
        build_mechanism_lineage_assignment_review_manifest_v3(
            policy=assignment_curation["policy"],
            roster=assignment_curation["roster"],
            candidate_universe=assignment_curation["universe"],
            reviews=assignment_curation["reviews"][:-1],
            adjudications=assignment_curation["adjudications"],
            sealed_at="2026-08-09T20:12:55+08:00",
        )
    with pytest.raises(ValueError, match="require adjudication"):
        build_mechanism_lineage_assignment_review_manifest_v3(
            policy=assignment_curation["policy"],
            roster=assignment_curation["roster"],
            candidate_universe=assignment_curation["universe"],
            reviews=assignment_curation["reviews"],
            adjudications=(),
            sealed_at="2026-08-09T20:12:55+08:00",
        )
    with pytest.raises(ValueError, match="exact accepted curation projection"):
        assert_formal_mechanism_lineage_assignment_curation_v3(
            registry=fixture["registry"],
            definition_curation_release=fixture["curation"],
            assignment_curation_release=assignment_curation["release"],
            public_assignments=fixture["lineage_assignments"],
        )
    for proposal in assignment_curation["universe"].proposals:
        expected_evidence = {
            (item.source_id, item.source_record_id, item.raw_sha256)
            for item in proposal.case.source_records
        }
        observed_evidence = {
            (
                item.source_id,
                item.source_record_id,
                item.source_record_raw_sha256,
            )
            for item in proposal.case_evidence_refs
        }
        assert observed_evidence == expected_evidence


def test_v3_assignment_curation_requires_three_people_and_strict_timing() -> None:
    fixture = _v3_inputs(main=False)
    assignment_curation = _v3_assignment_curation(fixture)
    roster = assignment_curation["roster"]
    duplicate_person = roster.reviewers[0].model_copy(
        update={"curator_id": "assignment-adjudicator-alias"}
    )
    with pytest.raises(
        ValidationError, match="natural-person bindings must be injective"
    ):
        build_mechanism_lineage_assignment_reviewer_roster_v3(
            policy=assignment_curation["policy"],
            reviewers=roster.reviewers,
            adjudicators=(duplicate_person,),
            independence_review="Pseudonym-only attempt",
            sealed_at=roster.sealed_at,
        )

    proposal = assignment_curation["universe"].proposals[0]
    with pytest.raises(ValueError, match="does not follow universe seal"):
        build_mechanism_lineage_assignment_review_v3(
            policy=assignment_curation["policy"],
            roster=roster,
            candidate_universe=assignment_curation["universe"],
            proposal=proposal,
            reviewer_id=roster.reviewers[0].curator_id,
            decision=MechanismLineageAssignmentDecisionV3.ACCEPT,
            criterion_findings=V3_ASSIGNMENT_REVIEW_CRITERIA,
            rationale="Improperly early assignment review",
            reviewed_at=assignment_curation["universe"].sealed_at,
        )


def test_v3_rejects_thirty_caller_minted_lineages_with_only_opaque_hashes() -> None:
    caller_fixture = _v3_inputs(
        main=False,
        singleton_lineages=True,
        formal_curation=False,
    )
    caller_release = _build_v3_release(caller_fixture)
    trusted_fixture = _v3_inputs(main=False, singleton_lineages=True)

    with pytest.raises(ValueError, match="foreign public registry"):
        assert_pilot_leakage_v3(
            cases=caller_fixture["cases"],
            split_manifest=caller_fixture["manifest"],
            release=caller_release,
            lineage_curation_release=trusted_fixture["curation"],
        )


def test_v3_private_roster_requires_three_injective_natural_person_bindings() -> None:
    fixture = _v3_inputs(main=False)
    curation = fixture["curation"]
    duplicate_person = curation.roster.curators[0].model_copy(
        update={"curator_id": "different-pseudonym-same-person"}
    )

    with pytest.raises(ValidationError, match="natural-person bindings must be injective"):
        build_mechanism_lineage_curator_roster_v3(
            policy=curation.policy,
            curators=curation.roster.curators,
            adjudicators=(duplicate_person,),
            independence_review="Attempted pseudonym-only independence claim",
            sealed_at=curation.roster.sealed_at,
        )


def test_v3_private_curation_rejects_every_temporal_inversion() -> None:
    fixture = _v3_inputs(main=False)
    trusted = fixture["curation"]
    definition = fixture["registry"].definitions[0]
    curators = trusted.roster.curators

    early_reviews = tuple(
        build_mechanism_lineage_definition_review_v3(
            policy=trusted.policy,
            definition=definition,
            curator_id=curator.curator_id,
            decision=MechanismLineageReviewDecisionV3.INCLUDE,
            criterion_findings=V3_REVIEW_CRITERIA,
            rationale="Raw review improperly predates the sealed roster",
            reviewed_at="2026-08-09T20:01:30+08:00",
        )
        for curator in curators
    )
    early_manifest = build_mechanism_lineage_evidence_review_manifest_v3(
        policy=trusted.policy,
        roster=trusted.roster,
        reviews=early_reviews,
        sealed_at="2026-08-09T20:06:00+08:00",
    )
    early_registry = build_mechanism_lineage_registry_v3(
        taxonomy_version=trusted.policy.taxonomy_version,
        curation_policy_sha256=trusted.policy.policy_sha256,
        evidence_review_manifest_sha256=early_manifest.manifest_sha256,
        curator_roster_sha256=trusted.roster.roster_sha256,
        definitions=(definition,),
        sealed_at="2026-08-09T20:12:00+08:00",
    )
    with pytest.raises(ValueError, match="raw review does not follow"):
        build_mechanism_lineage_curation_release_v3(
            registry=early_registry,
            policy=trusted.policy,
            roster=trusted.roster,
            review_manifest=early_manifest,
            assembled_at="2026-08-09T20:12:30+08:00",
        )

    disagreed_reviews = tuple(
        build_mechanism_lineage_definition_review_v3(
            policy=trusted.policy,
            definition=definition,
            curator_id=curator.curator_id,
            decision=(
                MechanismLineageReviewDecisionV3.INCLUDE
                if index == 0
                else MechanismLineageReviewDecisionV3.REJECT
            ),
            criterion_findings=V3_REVIEW_CRITERIA,
            rationale="Independent raw review before adjudication",
            reviewed_at="2026-08-09T20:04:00+08:00",
        )
        for index, curator in enumerate(curators)
    )
    early_adjudication = build_mechanism_lineage_definition_adjudication_v3(
        policy=trusted.policy,
        definition=definition,
        reviews=disagreed_reviews,
        adjudicator_id=trusted.roster.adjudicators[0].curator_id,
        final_decision=MechanismLineageReviewDecisionV3.INCLUDE,
        rationale="Improper adjudication timestamp before both raw reviews",
        adjudicated_at="2026-08-09T20:03:00+08:00",
    )
    adjudication_manifest = build_mechanism_lineage_evidence_review_manifest_v3(
        policy=trusted.policy,
        roster=trusted.roster,
        reviews=disagreed_reviews,
        adjudications=(early_adjudication,),
        sealed_at="2026-08-09T20:06:00+08:00",
    )
    adjudication_registry = build_mechanism_lineage_registry_v3(
        taxonomy_version=trusted.policy.taxonomy_version,
        curation_policy_sha256=trusted.policy.policy_sha256,
        evidence_review_manifest_sha256=adjudication_manifest.manifest_sha256,
        curator_roster_sha256=trusted.roster.roster_sha256,
        definitions=(definition,),
        sealed_at="2026-08-09T20:12:00+08:00",
    )
    with pytest.raises(ValueError, match="adjudication does not follow"):
        build_mechanism_lineage_curation_release_v3(
            registry=adjudication_registry,
            policy=trusted.policy,
            roster=trusted.roster,
            review_manifest=adjudication_manifest,
            assembled_at="2026-08-09T20:12:30+08:00",
        )

    with pytest.raises(ValueError, match="assembly predates registry"):
        build_mechanism_lineage_curation_release_v3(
            registry=fixture["registry"],
            policy=trusted.policy,
            roster=trusted.roster,
            review_manifest=trusted.review_manifest,
            assembled_at="2026-08-09T20:07:00+08:00",
        )


def test_v3_cross_round_recomputes_union_graph_and_candidate_api_derives_edges() -> None:
    r1 = _v3_inputs(main=False, global_definition_count=20)
    r2 = _v3_inputs(
        main=False,
        pilot_round=2,
        pilot_component_offset=10,
        governance_bundle=r1,
    )
    r1_release = _build_v3_release(r1)
    r2_release = _build_v3_release(r2)

    assert_cross_round_leakage_disjoint_v3(
        round_contexts=(
            LeakageRoundClosureContextV3(
                cases=r1["cases"],
                split_manifest=r1["manifest"],
                release=r1_release,
            ),
            LeakageRoundClosureContextV3(
                cases=r2["cases"],
                split_manifest=r2["manifest"],
                release=r2_release,
            ),
        ),
        lineage_curation_release=r1["curation"],
    )
    candidate_memberships = derive_formal_leakage_memberships_for_case_universe_v3(
        cases=r1["cases"],
        grouping_algorithms=r1["algorithms"],
        grouping_runs=r1["runs"],
        grouping_assignments=r1["structure_assignments"],
        mechanism_lineage_registry=r1["registry"],
        mechanism_lineage_curation_release=r1["curation"],
        mechanism_lineage_assignments=r1["lineage_assignments"],
    )
    assert candidate_memberships == r1_release.memberships
    assert_cross_round_leakage_disjoint_v3(
        round_contexts=(
            LeakageRoundClosureContextV3(
                cases=r1["cases"],
                split_manifest=r1["manifest"],
                release=r1_release,
            ),
        ),
        lineage_curation_release=r1["curation"],
        additional_case_universes=(
            LeakageUnsplitCaseUniverseContextV3(
                universe_id="pilot-r2-candidate-pool-test",
                source_artifact_id="candidate-pool-v3-test",
                source_artifact_sha256=canonical_sha256("candidate-pool-v3-test"),
                cases=r2["cases"],
                grouping_algorithms=r2["algorithms"],
                grouping_runs=r2["runs"],
                grouping_assignments=r2["structure_assignments"],
                mechanism_lineage_registry=r2["registry"],
                mechanism_lineage_assignments=r2["lineage_assignments"],
            ),
        ),
    )

    overlapping_r2 = _v3_inputs(
        main=False,
        pilot_round=2,
        pilot_component_offset=0,
        governance_bundle=r1,
    )
    with pytest.raises(ValueError, match="union component crosses benchmark universes"):
        assert_cross_round_leakage_disjoint_v3(
            round_contexts=(
                LeakageRoundClosureContextV3(
                    cases=r1["cases"],
                    split_manifest=r1["manifest"],
                    release=r1_release,
                ),
                LeakageRoundClosureContextV3(
                    cases=overlapping_r2["cases"],
                    split_manifest=overlapping_r2["manifest"],
                    release=_build_v3_release(overlapping_r2),
                ),
            ),
            lineage_curation_release=r1["curation"],
        )


def test_v3_cross_round_rejects_readdressed_self_consistent_fake_groups() -> None:
    r1 = _v3_inputs(main=False, global_definition_count=20)
    overlapping_r2 = _v3_inputs(
        main=False,
        pilot_round=2,
        pilot_component_offset=0,
        governance_bundle=r1,
    )
    r1_release = _build_v3_release(r1)
    true_r2_release = _build_v3_release(overlapping_r2)
    component_index_by_case = {
        case_id: index
        for index, component in enumerate(true_r2_release.components)
        for case_id in component.case_ids
    }
    algorithm_by_axis = {
        {
            LeakageAxis.STRUCTURE_PROTOTYPE: LeakageAxisV3.STRUCTURE_PROTOTYPE,
            LeakageAxis.STRUCTURE_FINGERPRINT: LeakageAxisV3.STRUCTURE_FINGERPRINT,
        }[item.axis]: item
        for item in true_r2_release.grouping_algorithms
    }
    fake_definitions: dict[tuple[LeakageAxisV3, int], LeakageGroupDefinitionV3] = {}
    fake_memberships = []
    for membership in true_r2_release.memberships:
        component_index = component_index_by_case[membership.case_id]
        key = (membership.axis, component_index)
        definition = fake_definitions.get(key)
        if definition is None:
            if membership.axis is LeakageAxisV3.COMPOSITION_FAMILY:
                preimage_value = {
                    "kind": "composition",
                    "stoichiometry": ((f"Forged{component_index}", 1),),
                }
            elif membership.axis is LeakageAxisV3.ARTICLE_OR_SOURCE_FAMILY:
                preimage_value = {
                    "kind": "source-record",
                    "source_id": "forged-source",
                    "source_record_id": f"forged-{component_index}",
                }
            elif membership.axis in {
                LeakageAxisV3.STRUCTURE_PROTOTYPE,
                LeakageAxisV3.STRUCTURE_FINGERPRINT,
            }:
                algorithm = algorithm_by_axis[membership.axis]
                preimage_value = {
                    "kind": "structure-group",
                    "axis": membership.axis.value,
                    "algorithm_id": algorithm.algorithm_id,
                    "algorithm_sha256": algorithm.algorithm_sha256,
                    "canonical_group_key": f"forged-{component_index}",
                }
            else:
                registry = true_r2_release.mechanism_lineage_registry
                fake_lineage_sha = canonical_sha256(
                    {"forged-lineage": component_index}
                )
                preimage_value = {
                    "kind": "mechanism-lineage",
                    "registry_id": registry.registry_id,
                    "registry_sha256": registry.registry_sha256,
                    "lineage_id": f"forged-lineage-{component_index}",
                    "lineage_sha256": fake_lineage_sha,
                    "scientific_preimage_sha256": canonical_sha256(
                        {"forged-science": component_index}
                    ),
                }
            preimage = canonical_json_bytes(preimage_value).decode("utf-8")
            definition_sha256 = canonical_sha256(
                {"axis": membership.axis, "canonical_preimage": preimage}
            )
            definition = LeakageGroupDefinitionV3(
                group_id=deterministic_id(
                    "leakage-group-v3",
                    {
                        "axis": membership.axis.value,
                        "canonical_preimage": preimage,
                    },
                ),
                definition_sha256=definition_sha256,
                axis=membership.axis,
                canonical_preimage=preimage,
            )
            fake_definitions[key] = definition
        fake_memberships.append(
            membership.model_copy(
                update={
                    "group_id": definition.group_id,
                    "group_definition_sha256": definition.definition_sha256,
                    "provenance_sha256": canonical_sha256(
                        {
                            "forged-membership": membership.case_id,
                            "axis": membership.axis.value,
                        }
                    ),
                }
            )
        )
    forged_values = {
        field_name: getattr(true_r2_release, field_name)
        for field_name in type(true_r2_release).model_fields
        if field_name not in {"release_id", "release_sha256"}
    }
    forged_values["group_definitions"] = tuple(
        sorted(
            fake_definitions.values(),
            key=lambda item: (item.axis.value, item.group_id),
        )
    )
    forged_values["memberships"] = tuple(fake_memberships)
    forged_release = _identified(
        LeakageComponentReleaseV3,
        id_field="release_id",
        sha_field="release_sha256",
        prefix="leakage-release-v3",
        values=forged_values,
    )

    with pytest.raises(ValueError, match="group definitions do not replay"):
        assert_cross_round_leakage_disjoint_v3(
            round_contexts=(
                LeakageRoundClosureContextV3(
                    cases=r1["cases"],
                    split_manifest=r1["manifest"],
                    release=r1_release,
                ),
                LeakageRoundClosureContextV3(
                    cases=overlapping_r2["cases"],
                    split_manifest=overlapping_r2["manifest"],
                    release=forged_release,
                ),
            ),
            lineage_curation_release=r1["curation"],
        )
