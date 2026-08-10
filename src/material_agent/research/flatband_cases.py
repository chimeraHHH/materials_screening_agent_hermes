"""Fail-closed case freezing and pre-run eligibility for the flat-band study.

This module closes the boundary between a content-addressed benchmark case and
the first execution artifact.  It deliberately does not execute retrieval,
models, or a Pilot.  A release is executable only when:

* the complete case objects exactly cover the selected split manifest;
* the split has already been closed against leakage and expert-registry
  releases;
* every replacement was present in a candidate-pool seal made before any
  eligibility decision;
* the formal V3 candidate pool exists before candidate-scoped expert assignment;
* exactly two assigned reviewers independently audit every candidate;
* a distinct adjudicator is used if and only if those audits disagree;
* the deterministic lowest-priority-eligible selection is derived before the
  final split, leakage, expert-study, and frozen-case releases; and
* the eligibility, final frozen, and leakage-union closure releases precede
  every execution budget.

The timestamps and hashes are reproducible protocol evidence, not an external
trusted timestamp or signature.  A future gateway attestation may strengthen
that evidence without weakening these invariants.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from enum import StrEnum
from typing import Annotated, Literal, TypeVar

from pydantic import Field, field_validator, model_validator

from material_agent.inspiration.models import (
    Identifier,
    Sha256,
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
    TargetBandClass,
    SplitCaseRefV1,
    SplitCaseRefV2,
    _require_rfc3339,
)
from material_agent.research.flatband_source_policy import (
    CaseSourcePolicyAttestationV2,
    SourceCatalogDecision,
    SourceUseRole,
    _RECORD_LEVEL_COMPATIBLE_LICENSES,
    _SOURCE_CATALOG_POLICY_V1,
    assert_case_source_policy_v2,
    build_case_source_policy_attestation_v2,
)
from material_agent.research.flatband_structure_grouping import (
    StructureGroupingPrivateEvidenceReleaseV2,
    StructureGroupingUnionReplayReleaseV2,
    assert_structure_grouping_release_exact_replay_v2,
    assert_structure_grouping_union_replay_release_exact_v2,
)
from material_agent.research.flatband_experts import (
    CalibrationCompletionV2,
    CalibrationSetManifestV2,
    CaseConflictAssessmentV1,
    ConflictStatus,
    ExpertStudyRegistryV1,
    ExpertStudyRegistryV2,
    PublicExpertIdentityReleaseV2,
    assert_calibration_completion_replays_manifest_v2,
    assert_expert_registry_covers_split,
    assert_expert_registry_covers_split_v2,
)
from material_agent.research.flatband_leakage import (
    LeakageAxis,
    LeakageComponentReleaseV1,
    LeakageComponentReleaseV3,
    LeakageRoundClosureContextV3,
    LeakageUnsplitCaseUniverseContextV3,
    MechanismLineageAssignmentV3,
    MechanismLineageAssignmentCurationReleaseV3,
    MechanismLineageCurationReleaseV3,
    MechanismLineageRegistryV3,
    StructureGroupingAlgorithmV2,
    StructureGroupingAssignmentV2,
    StructureGroupingRunV2,
    assert_leakage_split_closure,
    assert_leakage_split_closure_v3,
    assert_cross_round_leakage_disjoint_v3,
    assert_formal_mechanism_lineage_assignment_curation_v3,
    assert_formal_mechanism_lineage_registry_v3,
    assert_pilot_leakage_v3,
    derive_leakage_group_ids_v3,
    structure_grouping_case_universe_sha256_v2,
)


ModelT = TypeVar("ModelT", bound=StrictModel)

STRUCTURE_UNION_OWNER_CALIBRATION_V3 = "CALIBRATION"
STRUCTURE_UNION_OWNER_CURRENT_R1_FULL_POOL_V3 = "CURRENT_R1_FULL_POOL"
STRUCTURE_UNION_OWNER_PRIOR_R1_FULL_POOL_V3 = "PRIOR_R1_FULL_POOL"
STRUCTURE_UNION_OWNER_CURRENT_R2_FULL_POOL_V3 = "CURRENT_R2_FULL_POOL"


def _timestamp(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _revalidate(value: ModelT, model_type: type[ModelT]) -> ModelT:
    """Revalidate serialized content, including adversarial ``model_copy`` data."""

    return model_type.model_validate(
        value.model_dump(mode="python", round_trip=True)
    )


def _identity_values(
    model: StrictModel, *, id_field: str, sha_field: str, prefix: str
) -> tuple[str, str]:
    semantic = model.model_dump(mode="python", exclude={id_field, sha_field})
    digest = canonical_sha256(semantic)
    return deterministic_id(prefix, {sha_field: digest}), digest


def _assert_identity(
    model: StrictModel, *, id_field: str, sha_field: str, prefix: str
) -> None:
    identifier, digest = _identity_values(
        model, id_field=id_field, sha_field=sha_field, prefix=prefix
    )
    if getattr(model, sha_field) != digest:
        raise ValueError(f"{sha_field} does not match semantic content")
    if getattr(model, id_field) != identifier:
        raise ValueError(f"{id_field} does not match {sha_field}")


def _build_identified(
    model_type: type[ModelT],
    *,
    id_field: str,
    sha_field: str,
    prefix: str,
    values: dict[str, object],
) -> ModelT:
    draft = model_type.model_construct(**values)
    identifier, digest = _identity_values(
        draft, id_field=id_field, sha_field=sha_field, prefix=prefix
    )
    return model_type.model_validate(
        {**values, id_field: identifier, sha_field: digest}
    )


def _require_sorted_unique(values: tuple[str, ...], label: str) -> None:
    if values != tuple(sorted(set(values))):
        raise ValueError(f"{label} must be sorted and unique")


def _parent_snapshot(case: FlatBandBenchmarkCaseV1) -> str:
    return canonical_sha256(
        {
            "schema_version": "flatband-parent-snapshot-v1",
            "parent_label": case.parent_label,
            "formula": case.formula,
            "structure_sha256": case.structure_sha256,
            "source_records": tuple(
                item.model_dump(mode="python", round_trip=True)
                for item in case.source_records
            ),
        }
    )


def _request_snapshot(case: FlatBandBenchmarkCaseV1) -> str:
    return canonical_sha256(
        {
            "schema_version": "flatband-request-snapshot-v1",
            "frozen_request": case.frozen_request,
            "frozen_requirement_sha256": case.frozen_requirement_sha256,
            "target_class": case.target_class,
            "target_fermi_distance_max_e_v": case.target_fermi_distance_max_e_v,
        }
    )


def _constraint_snapshot(case: FlatBandBenchmarkCaseV1) -> str:
    return canonical_sha256(
        {
            "schema_version": "flatband-constraint-snapshot-v1",
            "hard_constraints": case.hard_constraints,
            "soft_preferences": case.soft_preferences,
        }
    )


def _forbidden_snapshot(case: FlatBandBenchmarkCaseV1) -> str:
    return canonical_sha256(
        {
            "schema_version": "flatband-forbidden-transformations-snapshot-v1",
            "forbidden_transformations": case.forbidden_transformations,
        }
    )


class FrozenCandidateRole(StrEnum):
    PRIMARY = "PRIMARY"
    PREDECLARED_REPLACEMENT = "PREDECLARED_REPLACEMENT"


class CaseEligibilityStatus(StrEnum):
    INCLUDED = "INCLUDED"
    EXCLUDED = "EXCLUDED"


class DerivativeEligibilityClassV3(StrEnum):
    NOT_A_DERIVATIVE = "NOT_A_DERIVATIVE"
    VACANCY = "VACANCY"
    INTERCALATION = "INTERCALATION"
    NON_STOICHIOMETRIC = "NON_STOICHIOMETRIC"
    ORDERED_DEFECT = "ORDERED_DEFECT"


class CaseEligibilityReasonCode(StrEnum):
    MEETS_ALL_PREREGISTERED_CRITERIA = "MEETS_ALL_PREREGISTERED_CRITERIA"
    SOURCE_PROVENANCE_INCOMPLETE = "SOURCE_PROVENANCE_INCOMPLETE"
    PARENT_STRUCTURE_UNVERIFIED = "PARENT_STRUCTURE_UNVERIFIED"
    REQUEST_OR_SCOPE_INCOMPLETE = "REQUEST_OR_SCOPE_INCOMPLETE"
    CONSTRAINTS_INCOMPLETE = "CONSTRAINTS_INCOMPLETE"
    FORBIDDEN_TRANSFORMATIONS_INCOMPLETE = (
        "FORBIDDEN_TRANSFORMATIONS_INCOMPLETE"
    )
    BAND_LABEL_OUT_OF_SCOPE = "BAND_LABEL_OUT_OF_SCOPE"
    LEAKAGE_PROVENANCE_INCOMPLETE = "LEAKAGE_PROVENANCE_INCOMPLETE"
    EXPERT_CONFLICT_OR_ASSIGNMENT_INCOMPLETE = (
        "EXPERT_CONFLICT_OR_ASSIGNMENT_INCOMPLETE"
    )
    DUPLICATE_OR_DEPENDENT_CASE = "DUPLICATE_OR_DEPENDENT_CASE"
    UNSUPPORTED_DERIVATIVE_WITHOUT_PARENT_TRANSFORMATION_LINEAGE = (
        "UNSUPPORTED_DERIVATIVE_WITHOUT_PARENT_TRANSFORMATION_LINEAGE"
    )
    OTHER_PREREGISTERED_FAILURE = "OTHER_PREREGISTERED_FAILURE"


class FrozenCaseCandidateV1(StrictModel):
    """One full case in a predeclared slot, including explicit freeze snapshots."""

    schema_version: Literal["flatband-frozen-case-candidate-v1"] = (
        "flatband-frozen-case-candidate-v1"
    )
    candidate_id: Identifier
    candidate_sha256: Sha256
    slot_id: Identifier
    priority: Annotated[int, Field(ge=0, le=16)]
    role: FrozenCandidateRole
    case: FlatBandBenchmarkCaseV1
    parent_snapshot_sha256: Sha256
    frozen_request_snapshot_sha256: Sha256
    constraint_snapshot_sha256: Sha256
    forbidden_transformations_snapshot_sha256: Sha256
    declared_at: Annotated[str, Field(min_length=20, max_length=40)]
    scientific_conclusion: Literal[False] = False

    @field_validator("declared_at")
    @classmethod
    def validate_declared_at(cls, value: str) -> str:
        return _require_rfc3339(value)

    @model_validator(mode="after")
    def validate_candidate(self) -> "FrozenCaseCandidateV1":
        case = _revalidate(self.case, FlatBandBenchmarkCaseV1)
        expected_role = (
            FrozenCandidateRole.PRIMARY
            if self.priority == 0
            else FrozenCandidateRole.PREDECLARED_REPLACEMENT
        )
        if self.role is not expected_role:
            raise ValueError("candidate role must be determined by slot priority")
        if not case.parent_label.strip() or not case.formula.strip():
            raise ValueError("candidate requires a complete parent identity")
        if not case.frozen_request.strip():
            raise ValueError("candidate requires a complete frozen request")
        if not case.hard_constraints:
            raise ValueError("candidate requires explicit hard constraints")
        if not case.forbidden_transformations:
            raise ValueError("candidate requires explicit forbidden transformations")
        snapshots = (
            (self.parent_snapshot_sha256, _parent_snapshot(case), "parent"),
            (
                self.frozen_request_snapshot_sha256,
                _request_snapshot(case),
                "frozen request",
            ),
            (
                self.constraint_snapshot_sha256,
                _constraint_snapshot(case),
                "constraints",
            ),
            (
                self.forbidden_transformations_snapshot_sha256,
                _forbidden_snapshot(case),
                "forbidden transformations",
            ),
        )
        for observed, expected, label in snapshots:
            if observed != expected:
                raise ValueError(f"{label} snapshot SHA-256 does not replay")
        _assert_identity(
            self,
            id_field="candidate_id",
            sha_field="candidate_sha256",
            prefix="frozen-case-candidate",
        )
        return self


def build_frozen_case_candidate(
    *,
    case: FlatBandBenchmarkCaseV1,
    slot_id: str,
    priority: int,
    declared_at: str,
) -> FrozenCaseCandidateV1:
    """Build a complete primary or predeclared replacement candidate."""

    case = _revalidate(case, FlatBandBenchmarkCaseV1)
    return _build_identified(
        FrozenCaseCandidateV1,
        id_field="candidate_id",
        sha_field="candidate_sha256",
        prefix="frozen-case-candidate",
        values={
            "slot_id": slot_id,
            "priority": priority,
            "role": (
                FrozenCandidateRole.PRIMARY
                if priority == 0
                else FrozenCandidateRole.PREDECLARED_REPLACEMENT
            ),
            "case": case,
            "parent_snapshot_sha256": _parent_snapshot(case),
            "frozen_request_snapshot_sha256": _request_snapshot(case),
            "constraint_snapshot_sha256": _constraint_snapshot(case),
            "forbidden_transformations_snapshot_sha256": _forbidden_snapshot(
                case
            ),
            "declared_at": declared_at,
        },
    )


def build_frozen_case_candidate_v3(
    *,
    structure_grouping_release: StructureGroupingPrivateEvidenceReleaseV2,
    candidate_key: str,
    declared_at: str,
) -> FrozenCaseCandidateV1:
    """Derive a V3 candidate only from an exact post-compute projection."""

    release = _revalidate(
        structure_grouping_release, StructureGroupingPrivateEvidenceReleaseV2
    )
    assert_structure_grouping_release_exact_replay_v2(release)
    projection = {
        item.candidate_key: item for item in release.final_case_projections
    }.get(candidate_key)
    if projection is None:
        raise ValueError("V3 candidate key is absent from the private structure release")
    case = {item.case_id: item for item in release.final_cases}.get(
        projection.final_case_id
    )
    case_input = {
        item.candidate_key: item
        for item in release.computation.input_manifest.case_inputs
    }.get(candidate_key)
    if case is None or case_input is None or (
        projection.final_case_sha256,
        projection.pre_group_slot_key,
        projection.input_id,
        projection.input_sha256,
    ) != (
        case.case_sha256,
        case_input.pre_group_slot_key,
        case_input.input_id,
        case_input.input_sha256,
    ):
        raise ValueError("V3 candidate projection crosswires case, slot, or input")
    if _timestamp(declared_at) < _timestamp(release.created_at):
        raise ValueError("V3 candidate declaration predates the structure release")
    return build_frozen_case_candidate(
        case=case,
        slot_id=projection.pre_group_slot_key,
        priority=case_input.preimage.priority,
        declared_at=declared_at,
    )


def frozen_case_slot_id(primary_case: FlatBandBenchmarkCaseV1) -> str:
    """Return the deterministic slot identity anchored to its primary case."""

    case = _revalidate(primary_case, FlatBandBenchmarkCaseV1)
    return deterministic_id(
        "frozen-case-slot",
        {"primary_case_id": case.case_id, "primary_case_sha256": case.case_sha256},
    )


def _candidate_pool_sha256(
    candidates: tuple[FrozenCaseCandidateV1, ...],
) -> str:
    return canonical_sha256(
        {
            "schema_version": "flatband-frozen-candidate-pool-v1",
            "candidates": tuple(
                item.model_dump(mode="python", round_trip=True)
                for item in candidates
            ),
        }
    )


def _case_matches_ref(
    case: FlatBandBenchmarkCaseV1, reference: SplitCaseRefV1
) -> bool:
    return (
        case.case_id,
        case.case_sha256,
        case.target_class,
        case.dimensionality,
        case.primary_mechanism_stratum,
        case.leakage_group_ids,
    ) == (
        reference.case_id,
        reference.case_sha256,
        reference.target_class,
        reference.dimensionality,
        reference.primary_mechanism_stratum,
        reference.leakage_group_ids,
    )


class FrozenCaseReleaseV1(StrictModel):
    """Full candidate pool plus the selected, split-exact active case universe."""

    schema_version: Literal["flatband-frozen-case-release-v1"] = (
        "flatband-frozen-case-release-v1"
    )
    release_id: Identifier
    release_sha256: Sha256
    split_manifest: BenchmarkSplitManifestV1
    leakage_release_id: Identifier
    leakage_release_sha256: Sha256
    leakage_release_created_at: Annotated[str, Field(min_length=20, max_length=40)]
    expert_registry_id: Identifier
    expert_registry_sha256: Sha256
    expert_registry_registered_at: Annotated[
        str, Field(min_length=20, max_length=40)
    ]
    annotation_guide_sha256: Sha256
    calibration_set_sha256: Sha256
    case_freeze_policy_sha256: Sha256
    eligibility_policy_sha256: Sha256
    candidate_pool_sha256: Sha256
    candidate_pool_sealed_at: Annotated[str, Field(min_length=20, max_length=40)]
    candidates: Annotated[
        tuple[FrozenCaseCandidateV1, ...], Field(min_length=30, max_length=2_040)
    ]
    active_case_ids: Annotated[
        tuple[Identifier, ...], Field(min_length=30, max_length=120)
    ]
    replacement_selection_rule: Literal["LOWEST_PRIORITY_ELIGIBLE_V1"] = (
        "LOWEST_PRIORITY_ELIGIBLE_V1"
    )
    frozen_at: Annotated[str, Field(min_length=20, max_length=40)]
    complete_case_omission_allowed: Literal[False] = False
    post_hoc_replacement_allowed: Literal[False] = False
    scientific_conclusion: Literal[False] = False

    @field_validator(
        "leakage_release_created_at",
        "expert_registry_registered_at",
        "candidate_pool_sealed_at",
        "frozen_at",
    )
    @classmethod
    def validate_timestamp(cls, value: str) -> str:
        return _require_rfc3339(value)

    @model_validator(mode="after")
    def validate_release(self) -> "FrozenCaseReleaseV1":
        manifest = _revalidate(self.split_manifest, BenchmarkSplitManifestV1)
        candidates = tuple(
            _revalidate(item, FrozenCaseCandidateV1) for item in self.candidates
        )
        candidate_order = tuple(
            (item.slot_id, item.priority, item.case.case_id) for item in candidates
        )
        if candidate_order != tuple(sorted(set(candidate_order))):
            raise ValueError("frozen candidates must be slot/priority/case sorted and unique")

        candidate_ids = tuple(item.candidate_id for item in candidates)
        candidate_shas = tuple(item.candidate_sha256 for item in candidates)
        case_ids = tuple(item.case.case_id for item in candidates)
        case_shas = tuple(item.case.case_sha256 for item in candidates)
        for values, label in (
            (candidate_ids, "candidate ID"),
            (candidate_shas, "candidate SHA-256"),
            (case_ids, "case ID"),
            (case_shas, "case SHA-256"),
        ):
            if len(values) != len(set(values)):
                raise ValueError(f"{label} identities must be one-to-one")

        by_slot: dict[str, list[FrozenCaseCandidateV1]] = defaultdict(list)
        for candidate in candidates:
            by_slot[candidate.slot_id].append(candidate)
        for slot_id, slot_candidates in by_slot.items():
            priorities = tuple(item.priority for item in slot_candidates)
            if priorities != tuple(range(len(slot_candidates))):
                raise ValueError("candidate priorities must be contiguous from zero")
            primary = slot_candidates[0]
            expected_slot = frozen_case_slot_id(primary.case)
            if slot_id != expected_slot:
                raise ValueError("slot ID does not match its priority-zero primary case")
            primary_strata = (
                primary.case.target_class,
                primary.case.dimensionality,
                primary.case.primary_mechanism_stratum,
            )
            if any(
                (
                    item.case.target_class,
                    item.case.dimensionality,
                    item.case.primary_mechanism_stratum,
                )
                != primary_strata
                for item in slot_candidates[1:]
            ):
                raise ValueError(
                    "replacement must preserve target, dimensionality, and mechanism strata"
                )

        _require_sorted_unique(self.active_case_ids, "active case IDs")
        manifest_ids = tuple(item.case_id for item in manifest.cases)
        if self.active_case_ids != manifest_ids:
            raise ValueError("active cases must exactly cover split case references")
        candidate_by_case = {item.case.case_id: item for item in candidates}
        active_candidates: list[FrozenCaseCandidateV1] = []
        for reference in manifest.cases:
            candidate = candidate_by_case.get(reference.case_id)
            if candidate is None:
                raise ValueError("split references a case absent from the frozen pool")
            if not _case_matches_ref(candidate.case, reference):
                raise ValueError("full frozen case differs from its split reference")
            active_candidates.append(candidate)
        active_slots = tuple(item.slot_id for item in active_candidates)
        if len(active_slots) != len(set(active_slots)):
            raise ValueError("split selects more than one candidate from a case slot")
        if set(active_slots) != set(by_slot):
            raise ValueError("split must select exactly one case from every frozen slot")

        if self.candidate_pool_sha256 != _candidate_pool_sha256(candidates):
            raise ValueError("candidate-pool SHA-256 does not replay")
        pool_sealed = _timestamp(self.candidate_pool_sealed_at)
        frozen_at = _timestamp(self.frozen_at)
        if any(_timestamp(item.declared_at) > pool_sealed for item in candidates):
            raise ValueError("candidate was declared after the candidate-pool seal")
        if pool_sealed > frozen_at:
            raise ValueError("candidate pool was sealed after the frozen case release")
        if _timestamp(self.leakage_release_created_at) > frozen_at:
            raise ValueError("leakage release follows the frozen case release")
        if _timestamp(self.expert_registry_registered_at) > frozen_at:
            raise ValueError("expert registry follows the frozen case release")
        _assert_identity(
            self,
            id_field="release_id",
            sha_field="release_sha256",
            prefix="frozen-case-release",
        )
        return self


def build_frozen_case_release(
    *,
    split_manifest: BenchmarkSplitManifestV1,
    leakage_release: LeakageComponentReleaseV1,
    expert_registry: ExpertStudyRegistryV1,
    candidates: tuple[FrozenCaseCandidateV1, ...],
    case_freeze_policy_sha256: str,
    eligibility_policy_sha256: str,
    candidate_pool_sealed_at: str,
    frozen_at: str,
) -> FrozenCaseReleaseV1:
    """Build a release after replaying split, leakage, and expert closure."""

    manifest = _revalidate(split_manifest, BenchmarkSplitManifestV1)
    leakage = _revalidate(leakage_release, LeakageComponentReleaseV1)
    registry = _revalidate(expert_registry, ExpertStudyRegistryV1)
    assert_leakage_split_closure(split_manifest=manifest, release=leakage)
    assert_expert_registry_covers_split(
        registry=registry, split_manifest=manifest
    )
    ordered = tuple(
        sorted(
            (_revalidate(item, FrozenCaseCandidateV1) for item in candidates),
            key=lambda item: (item.slot_id, item.priority, item.case.case_id),
        )
    )
    values: dict[str, object] = {
        "split_manifest": manifest,
        "leakage_release_id": leakage.release_id,
        "leakage_release_sha256": leakage.release_sha256,
        "leakage_release_created_at": leakage.created_at,
        "expert_registry_id": registry.registry_id,
        "expert_registry_sha256": registry.registry_sha256,
        "expert_registry_registered_at": registry.registered_at,
        "annotation_guide_sha256": registry.annotation_guide_sha256,
        "calibration_set_sha256": registry.calibration_set_sha256,
        "case_freeze_policy_sha256": case_freeze_policy_sha256,
        "eligibility_policy_sha256": eligibility_policy_sha256,
        "candidate_pool_sha256": _candidate_pool_sha256(ordered),
        "candidate_pool_sealed_at": candidate_pool_sealed_at,
        "candidates": ordered,
        "active_case_ids": tuple(item.case_id for item in manifest.cases),
        "frozen_at": frozen_at,
    }
    return _build_identified(
        FrozenCaseReleaseV1,
        id_field="release_id",
        sha_field="release_sha256",
        prefix="frozen-case-release",
        values=values,
    )


def _case_matches_ref_v2(
    case: FlatBandBenchmarkCaseV1, reference: SplitCaseRefV2
) -> bool:
    """Compare every case field represented by the formal split projection."""

    return (
        case.case_id,
        case.case_sha256,
        case.target_class,
        case.dimensionality,
        case.primary_mechanism_stratum,
        case.leakage_group_ids,
    ) == (
        reference.case_id,
        reference.case_sha256,
        reference.target_class,
        reference.dimensionality,
        reference.primary_mechanism_stratum,
        reference.independence_group_ids,
    )


def _candidate_pool_sha256_v2(
    candidates: tuple[FrozenCaseCandidateV1, ...],
    lineage_assignments: tuple[MechanismLineageAssignmentV3, ...],
    source_policy_attestations: tuple[CaseSourcePolicyAttestationV2, ...],
) -> str:
    """Address cases, lineage, and source policy in one candidate-pool seal."""

    return canonical_sha256(
        {
            "schema_version": "flatband-frozen-candidate-pool-v2",
            "candidates": tuple(
                item.model_dump(mode="python", round_trip=True)
                for item in candidates
            ),
            "mechanism_lineage_assignments": tuple(
                item.model_dump(mode="python", round_trip=True)
                for item in lineage_assignments
            ),
            "source_policy_attestations": tuple(
                item.model_dump(mode="python", round_trip=True)
                for item in source_policy_attestations
            ),
        }
    )


def _candidate_pool_sha256_v3(
    candidates: tuple[FrozenCaseCandidateV1, ...],
    lineage_assignments: tuple[MechanismLineageAssignmentV3, ...],
    source_policy_attestations: tuple[CaseSourcePolicyAttestationV2, ...],
    structure_grouping_release: StructureGroupingPrivateEvidenceReleaseV2,
    grouping_algorithms: tuple[StructureGroupingAlgorithmV2, ...],
    grouping_runs: tuple[StructureGroupingRunV2, ...],
    grouping_assignments: tuple[StructureGroupingAssignmentV2, ...],
) -> str:
    return canonical_sha256(
        {
            "schema_version": "flatband-candidate-pool-preimage-v3",
            "candidates": tuple(
                item.model_dump(mode="python", round_trip=True)
                for item in candidates
            ),
            "mechanism_lineage_assignments": tuple(
                item.model_dump(mode="python", round_trip=True)
                for item in lineage_assignments
            ),
            "source_policy_attestations": tuple(
                item.model_dump(mode="python", round_trip=True)
                for item in source_policy_attestations
            ),
            "structure_grouping_release": structure_grouping_release.model_dump(
                mode="python", round_trip=True
            ),
            "grouping_algorithms": tuple(
                item.model_dump(mode="python", round_trip=True)
                for item in grouping_algorithms
            ),
            "grouping_runs": tuple(
                item.model_dump(mode="python", round_trip=True)
                for item in grouping_runs
            ),
            "grouping_assignments": tuple(
                item.model_dump(mode="python", round_trip=True)
                for item in grouping_assignments
            ),
        }
    )


class FrozenCaseReleaseV2(StrictModel):
    """Unpublished legacy draft for split V2 and leakage V3.

    Unlike the historical V1 release, the V2 artifact embeds and replays the
    exact leakage and expert releases.  Every primary and replacement also has
    a lineage assignment from the one global registry before the candidate-pool
    seal.  This prevents a replacement or mechanism identity from being chosen
    after eligibility is known.
    """

    schema_version: Literal["flatband-frozen-case-release-v2"] = (
        "flatband-frozen-case-release-v2"
    )
    release_id: Identifier
    release_sha256: Sha256
    split_manifest: BenchmarkSplitManifestV2
    leakage_release: LeakageComponentReleaseV3
    expert_registry: ExpertStudyRegistryV2
    case_freeze_policy_sha256: Sha256
    eligibility_policy_sha256: Sha256
    candidate_pool_sha256: Sha256
    candidate_pool_sealed_at: Annotated[str, Field(min_length=20, max_length=40)]
    candidates: Annotated[
        tuple[FrozenCaseCandidateV1, ...], Field(min_length=30, max_length=2_040)
    ]
    candidate_lineage_assignments: Annotated[
        tuple[MechanismLineageAssignmentV3, ...],
        Field(min_length=30, max_length=2_040),
    ]
    source_policy_attestations: Annotated[
        tuple[CaseSourcePolicyAttestationV2, ...],
        Field(min_length=30, max_length=32_640),
    ]
    active_case_ids: Annotated[
        tuple[Identifier, ...], Field(min_length=30, max_length=120)
    ]
    replacement_selection_rule: Literal["LOWEST_PRIORITY_ELIGIBLE_V2"] = (
        "LOWEST_PRIORITY_ELIGIBLE_V2"
    )
    frozen_at: Annotated[str, Field(min_length=20, max_length=40)]
    complete_case_omission_allowed: Literal[False] = False
    post_hoc_replacement_allowed: Literal[False] = False
    global_lineage_registry_required: Literal[True] = True
    external_timestamp_attestation_present: Literal[False] = False
    scientific_conclusion: Literal[False] = False

    @field_validator("candidate_pool_sealed_at", "frozen_at")
    @classmethod
    def validate_timestamp(cls, value: str) -> str:
        return _require_rfc3339(value)

    @model_validator(mode="after")
    def validate_release(self) -> "FrozenCaseReleaseV2":
        manifest = _revalidate(self.split_manifest, BenchmarkSplitManifestV2)
        leakage = _revalidate(self.leakage_release, LeakageComponentReleaseV3)
        expert = _revalidate(self.expert_registry, ExpertStudyRegistryV2)
        candidates = tuple(
            _revalidate(item, FrozenCaseCandidateV1) for item in self.candidates
        )
        lineage_assignments = tuple(
            _revalidate(item, MechanismLineageAssignmentV3)
            for item in self.candidate_lineage_assignments
        )
        source_policies = tuple(
            _revalidate(item, CaseSourcePolicyAttestationV2)
            for item in self.source_policy_attestations
        )
        candidate_order = tuple(
            (item.slot_id, item.priority, item.case.case_id) for item in candidates
        )
        if candidate_order != tuple(sorted(set(candidate_order))):
            raise ValueError(
                "V2 frozen candidates must be slot/priority/case sorted and unique"
            )
        lineage_order = tuple(
            (item.case_id, item.assignment_id) for item in lineage_assignments
        )
        if lineage_order != tuple(sorted(set(lineage_order))):
            raise ValueError(
                "candidate lineage assignments must be case/assignment sorted and unique"
            )
        source_policy_order = tuple(
            (item.case_id, item.source_id, item.source_record_id)
            for item in source_policies
        )
        if source_policy_order != tuple(sorted(set(source_policy_order))):
            raise ValueError(
                "source policy attestations must be case/source sorted and unique"
            )

        for values, label in (
            (tuple(item.candidate_id for item in candidates), "candidate ID"),
            (tuple(item.candidate_sha256 for item in candidates), "candidate SHA-256"),
            (tuple(item.case.case_id for item in candidates), "case ID"),
            (tuple(item.case.case_sha256 for item in candidates), "case SHA-256"),
        ):
            if len(values) != len(set(values)):
                raise ValueError(f"V2 {label} identities must be one-to-one")

        by_slot: dict[str, list[FrozenCaseCandidateV1]] = defaultdict(list)
        candidate_by_case: dict[str, FrozenCaseCandidateV1] = {}
        for candidate in candidates:
            by_slot[candidate.slot_id].append(candidate)
            candidate_by_case[candidate.case.case_id] = candidate
            if candidate.case.source_catalog_sha256 != manifest.source_catalog_sha256:
                raise ValueError(
                    "candidate source catalog SHA-256 differs from split V2"
                )
            if any(record.raw_sha256 is None for record in candidate.case.source_records):
                raise ValueError(
                    "every V2 candidate source requires a frozen raw SHA-256"
                )
        for slot_id, slot_candidates in by_slot.items():
            priorities = tuple(item.priority for item in slot_candidates)
            if priorities != tuple(range(len(slot_candidates))):
                raise ValueError("V2 candidate priorities must be contiguous from zero")
            primary = slot_candidates[0]
            if slot_id != frozen_case_slot_id(primary.case):
                raise ValueError("V2 slot ID differs from its priority-zero primary")
            primary_strata = (
                primary.case.target_class,
                primary.case.dimensionality,
                primary.case.primary_mechanism_stratum,
            )
            if any(
                (
                    item.case.target_class,
                    item.case.dimensionality,
                    item.case.primary_mechanism_stratum,
                )
                != primary_strata
                for item in slot_candidates[1:]
            ):
                raise ValueError(
                    "V2 replacement must preserve target, dimensionality, and mechanism strata"
                )

        _require_sorted_unique(self.active_case_ids, "V2 active case IDs")
        manifest_ids = tuple(item.case_id for item in manifest.cases)
        if self.active_case_ids != manifest_ids:
            raise ValueError("V2 active cases must exactly cover split case references")
        active_candidates: list[FrozenCaseCandidateV1] = []
        for reference in manifest.cases:
            candidate = candidate_by_case.get(reference.case_id)
            if candidate is None:
                raise ValueError("split V2 references a case absent from the frozen pool")
            if not _case_matches_ref_v2(candidate.case, reference):
                raise ValueError("full frozen case differs from its split V2 reference")
            active_candidates.append(candidate)
        active_slots = tuple(item.slot_id for item in active_candidates)
        if len(active_slots) != len(set(active_slots)):
            raise ValueError("split V2 selects more than one candidate from a case slot")
        if set(active_slots) != set(by_slot):
            raise ValueError("split V2 must select exactly one case from every frozen slot")

        assert_leakage_split_closure_v3(
            cases=tuple(item.case for item in active_candidates),
            split_manifest=manifest,
            release=leakage,
        )
        assert_expert_registry_covers_split_v2(
            registry=expert,
            split_manifest=manifest,
        )

        registry = leakage.mechanism_lineage_registry
        definition_by_id = {item.lineage_id: item for item in registry.definitions}
        assignment_by_case: dict[str, MechanismLineageAssignmentV3] = {}
        for assignment in lineage_assignments:
            candidate = candidate_by_case.get(assignment.case_id)
            if candidate is None:
                raise ValueError("candidate lineage assignment references a foreign case")
            if assignment.case_id in assignment_by_case:
                raise ValueError("candidate has duplicate lineage assignments")
            assignment_by_case[assignment.case_id] = assignment
            if (
                assignment.case_sha256,
                assignment.registry_id,
                assignment.registry_sha256,
            ) != (
                candidate.case.case_sha256,
                registry.registry_id,
                registry.registry_sha256,
            ):
                raise ValueError(
                    "candidate lineage assignment crosswires case or global registry"
                )
            definition = definition_by_id.get(assignment.lineage_id)
            if definition is None or assignment.lineage_sha256 != definition.lineage_sha256:
                raise ValueError("candidate lineage assignment uses a foreign definition")
            if (
                definition.broad_mechanism_family
                is not candidate.case.primary_mechanism_stratum
            ):
                raise ValueError(
                    "candidate lineage broad family differs from sampling stratum"
                )
            source_keys = {
                (item.source_id, item.source_record_id, item.raw_sha256)
                for item in candidate.case.source_records
            }
            evidence_keys = {
                (
                    item.source_id,
                    item.source_record_id,
                    item.source_record_raw_sha256,
                )
                for item in assignment.case_evidence_refs
            }
            if not evidence_keys <= source_keys:
                raise ValueError(
                    "candidate lineage evidence is not a subset of frozen case sources"
                )
        if set(assignment_by_case) != set(candidate_by_case):
            raise ValueError(
                "candidate lineage assignments do not exactly cover the frozen pool"
            )

        expected_source_keys = {
            (candidate.case.case_id, record.source_id, record.source_record_id)
            for candidate in candidates
            for record in candidate.case.source_records
        }
        observed_source_keys = {
            (item.case_id, item.source_id, item.source_record_id)
            for item in source_policies
        }
        if observed_source_keys != expected_source_keys:
            raise ValueError(
                "source policy attestations do not exactly cover candidate sources"
            )
        source_policy_by_key = {
            (item.case_id, item.source_id, item.source_record_id): item
            for item in source_policies
        }
        for candidate in candidates:
            case = candidate.case
            record_by_key = {
                (item.source_id, item.source_record_id): item
                for item in case.source_records
            }
            case_policies = tuple(
                source_policy_by_key[(case.case_id, *key)]
                for key in sorted(record_by_key)
            )
            assert_case_source_policy_v2(
                case=case,
                attestations=case_policies,
            )
            for key, record in record_by_key.items():
                policy = source_policy_by_key[(case.case_id, *key)]
                if (
                    policy.case_sha256,
                    policy.source_record_raw_sha256,
                    policy.record_license_expression,
                    policy.public_fields_release_allowed,
                ) != (
                    case.case_sha256,
                    record.raw_sha256,
                    record.license_expression,
                    record.public_redistribution_allowed,
                ):
                    raise ValueError(
                        "source policy attestation differs from its exact case record"
                    )
                if policy.catalog_decision is SourceCatalogDecision.EXCLUDE:
                    raise ValueError("EXCLUDE source cannot enter a frozen case")
            structure_policies = tuple(
                item
                for item in case_policies
                if SourceUseRole.STRUCTURE in item.usage_roles
            )
            if not structure_policies:
                raise ValueError(
                    "every V2 candidate requires a catalog-approved structure source"
                )
            for evidence in case.seed_evidence:
                key = (
                    evidence.source_record.source_id,
                    evidence.source_record.source_record_id,
                )
                if record_by_key.get(key) != evidence.source_record:
                    raise ValueError(
                        "seed evidence source record is not an exact candidate source"
                    )
                if (
                    source_policy_by_key[(case.case_id, *key)].catalog_decision
                    is SourceCatalogDecision.EXCLUDE
                ):
                    raise ValueError("EXCLUDE source cannot enter seed evidence")
            if case.public_release_allowed:
                if not all(
                    item.public_fields_release_allowed for item in case_policies
                ):
                    raise ValueError(
                        "public case contains a source whose fields cannot be released"
                    )
                if not all(
                    item.structure_payload_release_allowed
                    for item in structure_policies
                ):
                    raise ValueError(
                        "public case contains a structure source without release permission"
                    )
        active_lineage = {
            item.case_id: item for item in leakage.mechanism_lineage_assignments
        }
        if {
            case_id: assignment_by_case[case_id] for case_id in manifest_ids
        } != active_lineage:
            raise ValueError(
                "active candidate lineage assignments differ from leakage V3"
            )

        if self.candidate_pool_sha256 != _candidate_pool_sha256_v2(
            candidates, lineage_assignments, source_policies
        ):
            raise ValueError("V2 candidate-pool SHA-256 does not replay")
        pool_sealed = _timestamp(self.candidate_pool_sealed_at)
        registry_sealed = _timestamp(registry.sealed_at)
        for candidate in candidates:
            declared = _timestamp(candidate.declared_at)
            assigned = _timestamp(
                assignment_by_case[candidate.case.case_id].assigned_at
            )
            if registry_sealed >= declared:
                raise ValueError(
                    "global lineage registry was not sealed before every candidate"
                )
            if assigned < declared:
                raise ValueError("lineage assignment predates its frozen candidate")
            if assigned >= pool_sealed:
                raise ValueError(
                    "candidate lineage assignment was not before candidate-pool seal"
                )
            if declared >= pool_sealed:
                raise ValueError("candidate was not declared before candidate-pool seal")

        frozen_at = _timestamp(self.frozen_at)
        if pool_sealed >= frozen_at:
            raise ValueError("candidate pool was not sealed before FrozenCaseReleaseV2")
        if _timestamp(leakage.created_at) >= frozen_at:
            raise ValueError("leakage V3 was not sealed before FrozenCaseReleaseV2")
        if _timestamp(expert.registered_at) >= frozen_at:
            raise ValueError("expert V2 registry was not sealed before FrozenCaseReleaseV2")
        _assert_identity(
            self,
            id_field="release_id",
            sha_field="release_sha256",
            prefix="frozen-case-release-v2",
        )
        return self


def build_frozen_case_release_v2(
    *,
    split_manifest: BenchmarkSplitManifestV2,
    leakage_release: LeakageComponentReleaseV3,
    expert_registry: ExpertStudyRegistryV2,
    candidates: tuple[FrozenCaseCandidateV1, ...],
    candidate_lineage_assignments: tuple[MechanismLineageAssignmentV3, ...],
    source_policy_attestations: tuple[CaseSourcePolicyAttestationV2, ...],
    case_freeze_policy_sha256: str,
    eligibility_policy_sha256: str,
    candidate_pool_sealed_at: str,
    frozen_at: str,
) -> FrozenCaseReleaseV2:
    """Build the self-contained V2 freeze after replaying every upstream seam."""

    manifest = _revalidate(split_manifest, BenchmarkSplitManifestV2)
    leakage = _revalidate(leakage_release, LeakageComponentReleaseV3)
    expert = _revalidate(expert_registry, ExpertStudyRegistryV2)
    ordered_candidates = tuple(
        sorted(
            (_revalidate(item, FrozenCaseCandidateV1) for item in candidates),
            key=lambda item: (item.slot_id, item.priority, item.case.case_id),
        )
    )
    ordered_lineages = tuple(
        sorted(
            (
                _revalidate(item, MechanismLineageAssignmentV3)
                for item in candidate_lineage_assignments
            ),
            key=lambda item: (item.case_id, item.assignment_id),
        )
    )
    ordered_source_policies = tuple(
        sorted(
            (
                _revalidate(item, CaseSourcePolicyAttestationV2)
                for item in source_policy_attestations
            ),
            key=lambda item: (
                item.case_id,
                item.source_id,
                item.source_record_id,
            ),
        )
    )
    values: dict[str, object] = {
        "split_manifest": manifest,
        "leakage_release": leakage,
        "expert_registry": expert,
        "case_freeze_policy_sha256": case_freeze_policy_sha256,
        "eligibility_policy_sha256": eligibility_policy_sha256,
        "candidate_pool_sha256": _candidate_pool_sha256_v2(
            ordered_candidates, ordered_lineages, ordered_source_policies
        ),
        "candidate_pool_sealed_at": candidate_pool_sealed_at,
        "candidates": ordered_candidates,
        "candidate_lineage_assignments": ordered_lineages,
        "source_policy_attestations": ordered_source_policies,
        "active_case_ids": tuple(item.case_id for item in manifest.cases),
        "frozen_at": frozen_at,
    }
    return _build_identified(
        FrozenCaseReleaseV2,
        id_field="release_id",
        sha_field="release_sha256",
        prefix="frozen-case-release-v2",
        values=values,
    )


class EligibilityDecisionV1(StrictModel):
    """One pre-run decision whose scope is necessarily every registered arm."""

    schema_version: Literal["flatband-case-eligibility-decision-v1"] = (
        "flatband-case-eligibility-decision-v1"
    )
    decision_id: Identifier
    decision_sha256: Sha256
    candidate_id: Identifier
    candidate_sha256: Sha256
    slot_id: Identifier
    case_id: Identifier
    case_sha256: Sha256
    status: CaseEligibilityStatus
    reason_codes: Annotated[
        tuple[CaseEligibilityReasonCode, ...], Field(min_length=1, max_length=16)
    ]
    assessor_id: Identifier
    assessment_protocol_sha256: Sha256
    rationale_sha256: Sha256
    applies_to_all_registered_systems: Literal[True] = True
    system_specific_override_allowed: Literal[False] = False
    assessed_at: Annotated[str, Field(min_length=20, max_length=40)]
    scientific_conclusion: Literal[False] = False

    @field_validator("assessed_at")
    @classmethod
    def validate_assessed_at(cls, value: str) -> str:
        return _require_rfc3339(value)

    @model_validator(mode="after")
    def validate_decision(self) -> "EligibilityDecisionV1":
        reason_values = tuple(item.value for item in self.reason_codes)
        if reason_values != tuple(sorted(set(reason_values))):
            raise ValueError("eligibility reasons must be sorted and unique")
        include_reason = CaseEligibilityReasonCode.MEETS_ALL_PREREGISTERED_CRITERIA
        if self.status is CaseEligibilityStatus.INCLUDED:
            if self.reason_codes != (include_reason,):
                raise ValueError("included case requires only the all-criteria reason")
        elif include_reason in self.reason_codes:
            raise ValueError("excluded case cannot claim that all criteria were met")
        _assert_identity(
            self,
            id_field="decision_id",
            sha_field="decision_sha256",
            prefix="case-eligibility-decision",
        )
        return self


def build_eligibility_decision(
    *,
    candidate: FrozenCaseCandidateV1,
    status: CaseEligibilityStatus,
    reason_codes: tuple[CaseEligibilityReasonCode, ...],
    assessor_id: str,
    assessment_protocol_sha256: str,
    rationale_sha256: str,
    assessed_at: str,
) -> EligibilityDecisionV1:
    """Build one globally scoped, content-addressed eligibility decision."""

    candidate = _revalidate(candidate, FrozenCaseCandidateV1)
    ordered_reasons = tuple(sorted(reason_codes, key=lambda item: item.value))
    return _build_identified(
        EligibilityDecisionV1,
        id_field="decision_id",
        sha_field="decision_sha256",
        prefix="case-eligibility-decision",
        values={
            "candidate_id": candidate.candidate_id,
            "candidate_sha256": candidate.candidate_sha256,
            "slot_id": candidate.slot_id,
            "case_id": candidate.case.case_id,
            "case_sha256": candidate.case.case_sha256,
            "status": status,
            "reason_codes": ordered_reasons,
            "assessor_id": assessor_id,
            "assessment_protocol_sha256": assessment_protocol_sha256,
            "rationale_sha256": rationale_sha256,
            "assessed_at": assessed_at,
        },
    )


class ActiveCaseSelectionV1(StrictModel):
    slot_id: Identifier
    selected_candidate_id: Identifier | None = None
    selected_candidate_sha256: Sha256 | None = None
    selected_case_id: Identifier | None = None
    selected_case_sha256: Sha256 | None = None
    selected_priority: Annotated[int, Field(ge=0, le=16)] | None = None
    replacement_activated: bool

    @model_validator(mode="after")
    def validate_selection(self) -> "ActiveCaseSelectionV1":
        values = (
            self.selected_candidate_id,
            self.selected_candidate_sha256,
            self.selected_case_id,
            self.selected_case_sha256,
            self.selected_priority,
        )
        selected = any(item is not None for item in values)
        if selected and any(item is None for item in values):
            raise ValueError("active selection identity must be complete")
        if not selected and self.replacement_activated:
            raise ValueError("empty slot cannot activate a replacement")
        if selected and self.replacement_activated != (self.selected_priority != 0):
            raise ValueError("replacement flag differs from selected priority")
        return self


def _derive_selections(
    frozen_release: FrozenCaseReleaseV1,
    decisions: tuple[EligibilityDecisionV1, ...],
) -> tuple[ActiveCaseSelectionV1, ...]:
    decisions_by_candidate = {item.candidate_id: item for item in decisions}
    by_slot: dict[str, list[FrozenCaseCandidateV1]] = defaultdict(list)
    for candidate in frozen_release.candidates:
        by_slot[candidate.slot_id].append(candidate)
    selections: list[ActiveCaseSelectionV1] = []
    for slot_id in sorted(by_slot):
        selected = next(
            (
                item
                for item in by_slot[slot_id]
                if decisions_by_candidate[item.candidate_id].status
                is CaseEligibilityStatus.INCLUDED
            ),
            None,
        )
        if selected is None:
            selections.append(
                ActiveCaseSelectionV1(
                    slot_id=slot_id,
                    replacement_activated=False,
                )
            )
        else:
            selections.append(
                ActiveCaseSelectionV1(
                    slot_id=slot_id,
                    selected_candidate_id=selected.candidate_id,
                    selected_candidate_sha256=selected.candidate_sha256,
                    selected_case_id=selected.case.case_id,
                    selected_case_sha256=selected.case.case_sha256,
                    selected_priority=selected.priority,
                    replacement_activated=selected.priority > 0,
                )
            )
    return tuple(selections)


class PreRunEligibilityReleaseV1(StrictModel):
    """Exact pre-run eligibility closure for a frozen case release."""

    schema_version: Literal["flatband-pre-run-eligibility-release-v1"] = (
        "flatband-pre-run-eligibility-release-v1"
    )
    release_id: Identifier
    release_sha256: Sha256
    frozen_case_release: FrozenCaseReleaseV1
    decisions: Annotated[
        tuple[EligibilityDecisionV1, ...], Field(min_length=30, max_length=2_040)
    ]
    active_selections: Annotated[
        tuple[ActiveCaseSelectionV1, ...], Field(min_length=30, max_length=120)
    ]
    execution_authorized: bool
    sealed_at: Annotated[str, Field(min_length=20, max_length=40)]
    eligibility_applies_symmetrically_to_all_systems: Literal[True] = True
    eligibility_was_sealed_before_execution_outputs: Literal[True] = True
    complete_case_omission_allowed: Literal[False] = False
    scientific_conclusion: Literal[False] = False

    @field_validator("sealed_at")
    @classmethod
    def validate_sealed_at(cls, value: str) -> str:
        return _require_rfc3339(value)

    @model_validator(mode="after")
    def validate_release(self) -> "PreRunEligibilityReleaseV1":
        frozen = _revalidate(self.frozen_case_release, FrozenCaseReleaseV1)
        decisions = tuple(
            _revalidate(item, EligibilityDecisionV1) for item in self.decisions
        )
        decision_ids = tuple(item.decision_id for item in decisions)
        decision_shas = tuple(item.decision_sha256 for item in decisions)
        candidate_ids = tuple(item.candidate_id for item in decisions)
        if candidate_ids != tuple(sorted(set(candidate_ids))):
            raise ValueError("eligibility decisions must be candidate-ID sorted and unique")
        if len(decision_ids) != len(set(decision_ids)) or len(decision_shas) != len(
            set(decision_shas)
        ):
            raise ValueError("eligibility decision ID/SHA identities must be one-to-one")
        expected_candidates = {item.candidate_id: item for item in frozen.candidates}
        if set(candidate_ids) != set(expected_candidates):
            raise ValueError("eligibility decisions do not exactly cover frozen candidates")
        for decision in decisions:
            candidate = expected_candidates[decision.candidate_id]
            if (
                decision.candidate_sha256,
                decision.slot_id,
                decision.case_id,
                decision.case_sha256,
                decision.assessment_protocol_sha256,
            ) != (
                candidate.candidate_sha256,
                candidate.slot_id,
                candidate.case.case_id,
                candidate.case.case_sha256,
                frozen.eligibility_policy_sha256,
            ):
                raise ValueError("eligibility decision differs from its frozen candidate")

        pool_sealed = _timestamp(frozen.candidate_pool_sealed_at)
        earliest_assessment = min(_timestamp(item.assessed_at) for item in decisions)
        if earliest_assessment <= pool_sealed:
            raise ValueError(
                "candidate pool, including replacements, must precede every assessment"
            )
        sealed_at = _timestamp(self.sealed_at)
        if any(_timestamp(item.assessed_at) > sealed_at for item in decisions):
            raise ValueError("eligibility decision follows the eligibility release seal")
        if _timestamp(frozen.frozen_at) > sealed_at:
            raise ValueError("eligibility release precedes the frozen case release")

        selections = tuple(
            ActiveCaseSelectionV1.model_validate(
                item.model_dump(mode="python", round_trip=True)
            )
            for item in self.active_selections
        )
        expected_selections = _derive_selections(frozen, decisions)
        if selections != expected_selections:
            raise ValueError("active case selections do not replay from eligibility")
        selected_case_ids = tuple(
            item.selected_case_id
            for item in selections
            if item.selected_case_id is not None
        )
        expected_authorized = (
            len(selected_case_ids) == len(selections)
            and set(selected_case_ids) == set(frozen.active_case_ids)
        )
        if self.execution_authorized != expected_authorized:
            raise ValueError("execution authorization differs from replayed selection")
        _assert_identity(
            self,
            id_field="release_id",
            sha_field="release_sha256",
            prefix="pre-run-eligibility-release",
        )
        return self


def build_pre_run_eligibility_release(
    *,
    frozen_case_release: FrozenCaseReleaseV1,
    decisions: tuple[EligibilityDecisionV1, ...],
    sealed_at: str,
) -> PreRunEligibilityReleaseV1:
    """Build and replay exact global eligibility plus deterministic replacement."""

    frozen = _revalidate(frozen_case_release, FrozenCaseReleaseV1)
    ordered = tuple(
        sorted(
            (_revalidate(item, EligibilityDecisionV1) for item in decisions),
            key=lambda item: item.candidate_id,
        )
    )
    expected_candidate_ids = {item.candidate_id for item in frozen.candidates}
    observed_candidate_ids = tuple(item.candidate_id for item in ordered)
    if (
        len(observed_candidate_ids) != len(set(observed_candidate_ids))
        or set(observed_candidate_ids) != expected_candidate_ids
    ):
        raise ValueError(
            "eligibility decisions do not exactly cover frozen candidates"
        )
    selections = _derive_selections(frozen, ordered)
    selected_case_ids = tuple(
        item.selected_case_id
        for item in selections
        if item.selected_case_id is not None
    )
    authorized = (
        len(selected_case_ids) == len(selections)
        and set(selected_case_ids) == set(frozen.active_case_ids)
    )
    return _build_identified(
        PreRunEligibilityReleaseV1,
        id_field="release_id",
        sha_field="release_sha256",
        prefix="pre-run-eligibility-release",
        values={
            "frozen_case_release": frozen,
            "decisions": ordered,
            "active_selections": selections,
            "execution_authorized": authorized,
            "sealed_at": sealed_at,
        },
    )


def assert_pre_run_eligibility_ready(
    release: PreRunEligibilityReleaseV1,
) -> None:
    """Reject a closed audit whose selected cases do not authorize execution."""

    release = _revalidate(release, PreRunEligibilityReleaseV1)
    if not release.execution_authorized:
        raise ValueError(
            "pre-run eligibility does not authorize the frozen split; reseal upstream releases"
        )


def assert_pre_run_eligibility_sealed_before(
    *,
    eligibility_release: PreRunEligibilityReleaseV1,
    first_execution_artifact_at: str,
) -> None:
    """Check the strict temporal boundary before the first execution artifact."""

    release = _revalidate(eligibility_release, PreRunEligibilityReleaseV1)
    assert_pre_run_eligibility_ready(release)
    _require_rfc3339(first_execution_artifact_at)
    if _timestamp(release.sealed_at) >= _timestamp(first_execution_artifact_at):
        raise ValueError("pre-run eligibility was not sealed before execution")


def assert_pre_run_eligibility_precedes_execution(
    *,
    eligibility_release: PreRunEligibilityReleaseV1,
    execution_release: object,
) -> None:
    """Bind eligibility to a real execution release and its earliest budget.

    The import is local so execution may later consume this module without a
    module-import cycle.  Budget freezing is earlier than rankings, receipts,
    packets, or terminal outputs, so requiring a strict order here is stronger
    than merely comparing against the first returned output.
    """

    from material_agent.research.flatband_execution import ExecutionReleaseV1

    eligibility = _revalidate(eligibility_release, PreRunEligibilityReleaseV1)
    execution = ExecutionReleaseV1.model_validate(
        ExecutionReleaseV1.model_validate(execution_release).model_dump(
            mode="python", round_trip=True
        )
    )
    assert_pre_run_eligibility_ready(eligibility)
    frozen_manifest = eligibility.frozen_case_release.split_manifest
    execution_manifest = execution.execution_matrix.split_manifest
    if execution_manifest != frozen_manifest:
        raise ValueError("execution uses a different frozen split manifest")
    selected_cases = {
        item.selected_case_id: item.selected_case_sha256
        for item in eligibility.active_selections
        if item.selected_case_id is not None
    }
    for cell in execution.execution_matrix.cells:
        if selected_cases.get(cell.case_id) != cell.case_sha256:
            raise ValueError("execution cell is absent from global eligible selection")
    if not execution.budget_manifests:
        raise ValueError("execution has no first budget artifact")
    first_budget_at = min(
        execution.budget_manifests,
        key=lambda item: _timestamp(item.frozen_at),
    ).frozen_at
    assert_pre_run_eligibility_sealed_before(
        eligibility_release=eligibility,
        first_execution_artifact_at=first_budget_at,
    )


def _derive_selections_v2(
    frozen_release: FrozenCaseReleaseV2,
    decisions: tuple[EligibilityDecisionV1, ...],
) -> tuple[ActiveCaseSelectionV1, ...]:
    """Reuse the frozen deterministic rule without accepting a V1 release."""

    decisions_by_candidate = {item.candidate_id: item for item in decisions}
    by_slot: dict[str, list[FrozenCaseCandidateV1]] = defaultdict(list)
    for candidate in frozen_release.candidates:
        by_slot[candidate.slot_id].append(candidate)
    selections: list[ActiveCaseSelectionV1] = []
    for slot_id in sorted(by_slot):
        selected = next(
            (
                item
                for item in by_slot[slot_id]
                if decisions_by_candidate[item.candidate_id].status
                is CaseEligibilityStatus.INCLUDED
            ),
            None,
        )
        if selected is None:
            selections.append(
                ActiveCaseSelectionV1(
                    slot_id=slot_id,
                    replacement_activated=False,
                )
            )
        else:
            selections.append(
                ActiveCaseSelectionV1(
                    slot_id=slot_id,
                    selected_candidate_id=selected.candidate_id,
                    selected_candidate_sha256=selected.candidate_sha256,
                    selected_case_id=selected.case.case_id,
                    selected_case_sha256=selected.case.case_sha256,
                    selected_priority=selected.priority,
                    replacement_activated=selected.priority > 0,
                )
            )
    return tuple(selections)


class PreRunEligibilityReleaseV2(StrictModel):
    """Unpublished legacy draft; it is not a formal Pilot authorization."""

    schema_version: Literal["flatband-pre-run-eligibility-release-v2"] = (
        "flatband-pre-run-eligibility-release-v2"
    )
    release_id: Identifier
    release_sha256: Sha256
    frozen_case_release: FrozenCaseReleaseV2
    decisions: Annotated[
        tuple[EligibilityDecisionV1, ...], Field(min_length=30, max_length=2_040)
    ]
    active_selections: Annotated[
        tuple[ActiveCaseSelectionV1, ...], Field(min_length=30, max_length=120)
    ]
    execution_authorized: bool
    sealed_at: Annotated[str, Field(min_length=20, max_length=40)]
    eligibility_applies_symmetrically_to_all_systems: Literal[True] = True
    complete_case_omission_allowed: Literal[False] = False
    external_timestamp_attestation_present: Literal[False] = False
    scientific_conclusion: Literal[False] = False

    @field_validator("sealed_at")
    @classmethod
    def validate_sealed_at(cls, value: str) -> str:
        return _require_rfc3339(value)

    @model_validator(mode="after")
    def validate_release(self) -> "PreRunEligibilityReleaseV2":
        frozen = _revalidate(self.frozen_case_release, FrozenCaseReleaseV2)
        decisions = tuple(
            _revalidate(item, EligibilityDecisionV1) for item in self.decisions
        )
        candidate_ids = tuple(item.candidate_id for item in decisions)
        if candidate_ids != tuple(sorted(set(candidate_ids))):
            raise ValueError(
                "V2 eligibility decisions must be candidate-ID sorted and unique"
            )
        if len({item.decision_id for item in decisions}) != len(decisions) or len(
            {item.decision_sha256 for item in decisions}
        ) != len(decisions):
            raise ValueError("V2 eligibility decision identities must be one-to-one")
        candidate_by_id = {item.candidate_id: item for item in frozen.candidates}
        if set(candidate_ids) != set(candidate_by_id):
            raise ValueError(
                "V2 eligibility decisions do not exactly cover frozen candidates"
            )
        for decision in decisions:
            candidate = candidate_by_id[decision.candidate_id]
            if (
                decision.candidate_sha256,
                decision.slot_id,
                decision.case_id,
                decision.case_sha256,
                decision.assessment_protocol_sha256,
            ) != (
                candidate.candidate_sha256,
                candidate.slot_id,
                candidate.case.case_id,
                candidate.case.case_sha256,
                frozen.eligibility_policy_sha256,
            ):
                raise ValueError(
                    "V2 eligibility decision differs from its frozen candidate"
                )

        pool_sealed = _timestamp(frozen.candidate_pool_sealed_at)
        upstream_cutoff = min(
            _timestamp(frozen.leakage_release.created_at),
            _timestamp(frozen.expert_registry.registered_at),
            _timestamp(frozen.frozen_at),
        )
        sealed_at = _timestamp(self.sealed_at)
        for decision in decisions:
            assessed = _timestamp(decision.assessed_at)
            if assessed <= pool_sealed:
                raise ValueError(
                    "candidate pool, including replacements, must precede every V2 assessment"
                )
            if assessed >= upstream_cutoff:
                raise ValueError(
                    "eligibility selection was not frozen before leakage/expert closure"
                )
            if assessed > sealed_at:
                raise ValueError("V2 eligibility decision follows its release seal")
        if _timestamp(frozen.frozen_at) >= sealed_at:
            raise ValueError(
                "V2 eligibility seal must strictly follow FrozenCaseReleaseV2"
            )

        selections = tuple(
            ActiveCaseSelectionV1.model_validate(
                item.model_dump(mode="python", round_trip=True)
            )
            for item in self.active_selections
        )
        expected_selections = _derive_selections_v2(frozen, decisions)
        if selections != expected_selections:
            raise ValueError("V2 active selections do not replay from eligibility")
        selected = {
            item.selected_case_id: item.selected_case_sha256
            for item in selections
            if item.selected_case_id is not None
        }
        expected = {
            item.case_id: item.case_sha256
            for item in frozen.split_manifest.cases
        }
        expected_authorized = (
            len(selected) == len(selections) and selected == expected
        )
        if self.execution_authorized != expected_authorized:
            raise ValueError(
                "V2 execution authorization differs from replayed selection"
            )
        _assert_identity(
            self,
            id_field="release_id",
            sha_field="release_sha256",
            prefix="pre-run-eligibility-release-v2",
        )
        return self


def build_pre_run_eligibility_release_v2(
    *,
    frozen_case_release: FrozenCaseReleaseV2,
    decisions: tuple[EligibilityDecisionV1, ...],
    sealed_at: str,
) -> PreRunEligibilityReleaseV2:
    frozen = _revalidate(frozen_case_release, FrozenCaseReleaseV2)
    ordered = tuple(
        sorted(
            (_revalidate(item, EligibilityDecisionV1) for item in decisions),
            key=lambda item: item.candidate_id,
        )
    )
    observed_candidate_ids = tuple(item.candidate_id for item in ordered)
    expected_candidate_ids = {
        item.candidate_id for item in frozen.candidates
    }
    if (
        len(observed_candidate_ids) != len(set(observed_candidate_ids))
        or set(observed_candidate_ids) != expected_candidate_ids
    ):
        raise ValueError(
            "V2 eligibility decisions do not exactly cover frozen candidates"
        )
    selections = _derive_selections_v2(frozen, ordered)
    selected = {
        item.selected_case_id: item.selected_case_sha256
        for item in selections
        if item.selected_case_id is not None
    }
    expected = {
        item.case_id: item.case_sha256 for item in frozen.split_manifest.cases
    }
    return _build_identified(
        PreRunEligibilityReleaseV2,
        id_field="release_id",
        sha_field="release_sha256",
        prefix="pre-run-eligibility-release-v2",
        values={
            "frozen_case_release": frozen,
            "decisions": ordered,
            "active_selections": selections,
            "execution_authorized": (
                len(selected) == len(selections) and selected == expected
            ),
            "sealed_at": sealed_at,
        },
    )


def assert_pre_run_eligibility_ready_v2(
    release: PreRunEligibilityReleaseV2,
) -> None:
    value = _revalidate(release, PreRunEligibilityReleaseV2)
    if not value.execution_authorized:
        raise ValueError(
            "V2 pre-run eligibility does not authorize the frozen split"
        )


def assert_pre_run_eligibility_precedes_execution_v2(
    *,
    eligibility_release: PreRunEligibilityReleaseV2,
    execution_release: object,
) -> None:
    """Replay the unpublished V2 draft without granting formal status."""

    from material_agent.research.flatband_execution import ExecutionReleaseV2

    eligibility = _revalidate(
        eligibility_release, PreRunEligibilityReleaseV2
    )
    execution = ExecutionReleaseV2.model_validate(
        ExecutionReleaseV2.model_validate(execution_release).model_dump(
            mode="python", round_trip=True
        )
    )
    assert_pre_run_eligibility_ready_v2(eligibility)
    if execution.execution_matrix.split_manifest != (
        eligibility.frozen_case_release.split_manifest
    ):
        raise ValueError("ExecutionReleaseV2 uses a foreign frozen split V2")
    selected = {
        item.selected_case_id: item.selected_case_sha256
        for item in eligibility.active_selections
        if item.selected_case_id is not None
    }
    if any(
        selected.get(cell.case_id) != cell.case_sha256
        for cell in execution.execution_matrix.cells
    ):
        raise ValueError("execution cell is absent from the V2 eligible selection")
    if not execution.budget_manifests:
        raise ValueError("execution release has no real budget manifests")
    sealed_at = _timestamp(eligibility.sealed_at)
    if any(
        sealed_at >= _timestamp(item.frozen_at)
        for item in execution.budget_manifests
    ):
        raise ValueError(
            "V2 pre-run eligibility was not sealed before every execution budget"
        )


class CandidatePoolReleaseV3(StrictModel):
    """A pre-audit universe of primary and replacement candidates.

    V3 removes the dependency inversion in ``FrozenCaseReleaseV2``: the pool
    has no selected split, leakage component, or split-scoped expert registry.
    It only freezes the complete candidate/source/lineage preimage from which
    later eligibility and selection artifacts are allowed to derive.
    """

    schema_version: Literal["flatband-candidate-pool-release-v3"] = (
        "flatband-candidate-pool-release-v3"
    )
    release_id: Identifier
    release_sha256: Sha256
    source_catalog_sha256: Sha256
    case_freeze_policy_sha256: Sha256
    eligibility_policy_sha256: Sha256
    study_phase: Literal["PILOT_R1", "PILOT_R2"]
    expected_slot_count: Literal[30] = 30
    mechanism_lineage_registry: MechanismLineageRegistryV3
    mechanism_lineage_curation_release: MechanismLineageCurationReleaseV3
    mechanism_lineage_assignment_curation_release: (
        MechanismLineageAssignmentCurationReleaseV3
    )
    candidate_pool_sha256: Sha256
    candidates: Annotated[
        tuple[FrozenCaseCandidateV1, ...], Field(min_length=30, max_length=36)
    ]
    candidate_lineage_assignments: Annotated[
        tuple[MechanismLineageAssignmentV3, ...],
        Field(min_length=30, max_length=36),
    ]
    structure_grouping_release: StructureGroupingPrivateEvidenceReleaseV2
    grouping_algorithms: Annotated[
        tuple[StructureGroupingAlgorithmV2, ...], Field(min_length=2, max_length=2)
    ]
    grouping_runs: Annotated[
        tuple[StructureGroupingRunV2, ...], Field(min_length=2, max_length=2)
    ]
    grouping_assignments: Annotated[
        tuple[StructureGroupingAssignmentV2, ...],
        Field(min_length=60, max_length=72),
    ]
    source_policy_attestations: Annotated[
        tuple[CaseSourcePolicyAttestationV2, ...],
        Field(min_length=30, max_length=576),
    ]
    sealed_at: Annotated[str, Field(min_length=20, max_length=40)]
    replacement_selection_rule: Literal["LOWEST_PRIORITY_ELIGIBLE_V3"] = (
        "LOWEST_PRIORITY_ELIGIBLE_V3"
    )
    include_only_source_policy: Literal[True] = True
    parent_transformation_lineage_axis_available: Literal[False] = False
    unsupported_derivative_eligibility_inclusion_allowed: Literal[False] = False
    unsupported_derivative_exclusion_reason_code: Literal[
        "UNSUPPORTED_DERIVATIVE_WITHOUT_PARENT_TRANSFORMATION_LINEAGE"
    ] = "UNSUPPORTED_DERIVATIVE_WITHOUT_PARENT_TRANSFORMATION_LINEAGE"
    post_hoc_candidate_allowed: Literal[False] = False
    private_custody_required: Literal[True] = True
    public_release_allowed: Literal[False] = False
    external_timestamp_attestation_present: Literal[False] = False
    scientific_conclusion: Literal[False] = False

    @field_validator("sealed_at")
    @classmethod
    def validate_sealed_at(cls, value: str) -> str:
        return _require_rfc3339(value)

    @model_validator(mode="after")
    def validate_release(self) -> "CandidatePoolReleaseV3":
        registry = _revalidate(
            self.mechanism_lineage_registry, MechanismLineageRegistryV3
        )
        curation = _revalidate(
            self.mechanism_lineage_curation_release,
            MechanismLineageCurationReleaseV3,
        )
        assignment_curation = _revalidate(
            self.mechanism_lineage_assignment_curation_release,
            MechanismLineageAssignmentCurationReleaseV3,
        )
        assert_formal_mechanism_lineage_registry_v3(
            registry=registry,
            curation_release=curation,
        )
        candidates = tuple(
            _revalidate(item, FrozenCaseCandidateV1) for item in self.candidates
        )
        lineages = tuple(
            _revalidate(item, MechanismLineageAssignmentV3)
            for item in self.candidate_lineage_assignments
        )
        source_policies = tuple(
            _revalidate(item, CaseSourcePolicyAttestationV2)
            for item in self.source_policy_attestations
        )
        structure_release = _revalidate(
            self.structure_grouping_release,
            StructureGroupingPrivateEvidenceReleaseV2,
        )
        assert_structure_grouping_release_exact_replay_v2(structure_release)
        algorithms = tuple(
            _revalidate(item, StructureGroupingAlgorithmV2)
            for item in self.grouping_algorithms
        )
        runs = tuple(
            _revalidate(item, StructureGroupingRunV2)
            for item in self.grouping_runs
        )
        grouping_assignments = tuple(
            _revalidate(item, StructureGroupingAssignmentV2)
            for item in self.grouping_assignments
        )
        if (
            algorithms,
            runs,
            grouping_assignments,
            source_policies,
        ) != (
            structure_release.grouping_algorithms,
            structure_release.grouping_runs,
            structure_release.grouping_assignments,
            structure_release.source_policy_attestations,
        ):
            raise ValueError(
                "V3 grouping/source artifacts differ from the private structure release projection"
            )

        candidate_order = tuple(
            (item.slot_id, item.priority, item.case.case_id) for item in candidates
        )
        if candidate_order != tuple(sorted(set(candidate_order))):
            raise ValueError(
                "V3 candidates must be slot/priority/case sorted and unique"
            )
        lineage_order = tuple(
            (item.case_id, item.assignment_id) for item in lineages
        )
        if lineage_order != tuple(sorted(set(lineage_order))):
            raise ValueError(
                "V3 candidate lineage assignments must be case/ID sorted and unique"
            )
        source_order = tuple(
            (item.case_id, item.source_id, item.source_record_id)
            for item in source_policies
        )
        if source_order != tuple(sorted(set(source_order))):
            raise ValueError(
                "V3 source policies must be case/source sorted and unique"
            )
        algorithm_axes = tuple(item.axis.value for item in algorithms)
        expected_structure_axes = tuple(
            sorted(
                (
                    LeakageAxis.STRUCTURE_FINGERPRINT.value,
                    LeakageAxis.STRUCTURE_PROTOTYPE.value,
                )
            )
        )
        if algorithm_axes != expected_structure_axes:
            raise ValueError("V3 pool requires one sorted algorithm per structure axis")
        run_axes = tuple(item.axis.value for item in runs)
        if run_axes != expected_structure_axes:
            raise ValueError("V3 pool requires one sorted run per structure axis")
        grouping_order = tuple(
            (item.axis.value, item.case_id, item.assignment_id)
            for item in grouping_assignments
        )
        if grouping_order != tuple(sorted(set(grouping_order))):
            raise ValueError("V3 grouping assignments must be axis/case/ID sorted")
        for values, label in (
            (tuple(item.candidate_id for item in candidates), "candidate ID"),
            (tuple(item.candidate_sha256 for item in candidates), "candidate SHA-256"),
            (tuple(item.case.case_id for item in candidates), "case ID"),
            (tuple(item.case.case_sha256 for item in candidates), "case SHA-256"),
        ):
            if len(values) != len(set(values)):
                raise ValueError(f"V3 {label} identities must be one-to-one")

        candidate_by_case = {item.case.case_id: item for item in candidates}
        release_case_by_id = {
            item.case_id: item for item in structure_release.final_cases
        }
        projection_by_case = {
            item.final_case_id: item
            for item in structure_release.final_case_projections
        }
        input_by_key = {
            item.candidate_key: item
            for item in structure_release.computation.input_manifest.case_inputs
        }
        if set(candidate_by_case) != set(release_case_by_id) or set(
            candidate_by_case
        ) != set(projection_by_case):
            raise ValueError(
                "V3 candidates do not exactly cover the private structure release"
            )
        by_slot: dict[str, list[FrozenCaseCandidateV1]] = defaultdict(list)
        for candidate in candidates:
            by_slot[candidate.slot_id].append(candidate)
            projection = projection_by_case[candidate.case.case_id]
            case_input = input_by_key.get(projection.candidate_key)
            if case_input is None or (
                candidate.case,
                candidate.slot_id,
                candidate.priority,
                projection.final_case_sha256,
                projection.pre_group_slot_key,
                projection.input_id,
                projection.input_sha256,
                case_input.preimage.study_phase,
            ) != (
                release_case_by_id[candidate.case.case_id],
                case_input.pre_group_slot_key,
                case_input.preimage.priority,
                candidate.case.case_sha256,
                case_input.pre_group_slot_key,
                case_input.input_id,
                case_input.input_sha256,
                self.study_phase,
            ):
                raise ValueError(
                    "V3 candidate crosswires private final-case, slot, priority, input, or phase"
                )
            if _timestamp(candidate.declared_at) < _timestamp(
                structure_release.created_at
            ):
                raise ValueError(
                    "V3 candidate declaration predates the private structure release"
                )
            if candidate.case.source_catalog_sha256 != self.source_catalog_sha256:
                raise ValueError("V3 candidate uses a foreign source catalog")
            if any(
                record.raw_sha256 is None
                for record in candidate.case.source_records
            ):
                raise ValueError("every V3 candidate source requires a raw SHA-256")
        for slot_id, slot_candidates in by_slot.items():
            priorities = tuple(item.priority for item in slot_candidates)
            if priorities != tuple(range(len(slot_candidates))):
                raise ValueError("V3 candidate priorities must be contiguous from zero")
            if priorities not in ((0,), (0, 1)):
                raise ValueError("formal Pilot V3 permits at most one replacement per slot")
            primary = slot_candidates[0]
            primary_strata = (
                primary.case.target_class,
                primary.case.dimensionality,
                primary.case.primary_mechanism_stratum,
            )
            if any(
                (
                    item.case.target_class,
                    item.case.dimensionality,
                    item.case.primary_mechanism_stratum,
                )
                != primary_strata
                for item in slot_candidates[1:]
            ):
                raise ValueError(
                    "V3 replacement must preserve target, dimensionality, and mechanism strata"
                )
        if len(by_slot) != self.expected_slot_count:
            raise ValueError("formal Pilot V3 requires exactly 30 predeclared slots")
        if len(candidates) - self.expected_slot_count > 6:
            raise ValueError("formal Pilot V3 permits at most six replacements per round")
        primaries = tuple(items[0].case for items in by_slot.values())
        target_counts = {
            value: sum(item.target_class is value for item in primaries)
            for value in (TargetBandClass.FB100, TargetBandClass.NB300)
        }
        if set(target_counts.values()) != {15}:
            raise ValueError("formal Pilot V3 requires a 15/15 target-class balance")
        dimension_counts = {
            value: sum(item.dimensionality is value for item in primaries)
            for value in (Dimensionality.TWO_D, Dimensionality.THREE_D)
        }
        if set(dimension_counts.values()) != {15}:
            raise ValueError("formal Pilot V3 requires a 15/15 dimensionality balance")
        family_counts: dict[object, int] = defaultdict(int)
        for item in primaries:
            family_counts[item.primary_mechanism_stratum] += 1
        if len(family_counts) < 5 or max(family_counts.values()) > 6:
            raise ValueError(
                "formal Pilot V3 requires at least five mechanism families and at most six slots per family"
            )

        algorithm_by_axis = {item.axis: item for item in algorithms}
        run_by_axis = {item.axis: item for item in runs}
        candidate_cases = tuple(
            sorted((item.case for item in candidates), key=lambda item: item.case_id)
        )
        universe_sha256 = structure_grouping_case_universe_sha256_v2(
            candidate_cases
        )
        for run in runs:
            algorithm = algorithm_by_axis[run.axis]
            if (
                run.algorithm_id,
                run.algorithm_sha256,
                run.input_case_universe_sha256,
            ) != (
                algorithm.algorithm_id,
                algorithm.algorithm_sha256,
                universe_sha256,
            ):
                raise ValueError("V3 grouping run crosswires algorithm or universe")
        grouping_by_key: dict[
            tuple[LeakageAxis, str], StructureGroupingAssignmentV2
        ] = {}
        for assignment in grouping_assignments:
            key = (assignment.axis, assignment.case_id)
            if key in grouping_by_key:
                raise ValueError("duplicate V3 structure assignment")
            candidate = candidate_by_case.get(assignment.case_id)
            if candidate is None:
                raise ValueError("V3 structure assignment references foreign case")
            algorithm = algorithm_by_axis[assignment.axis]
            run = run_by_axis[assignment.axis]
            if (
                assignment.algorithm_id,
                assignment.algorithm_sha256,
                assignment.grouping_run_id,
                assignment.grouping_run_sha256,
                assignment.case_sha256,
                assignment.structure_sha256,
            ) != (
                algorithm.algorithm_id,
                algorithm.algorithm_sha256,
                run.grouping_run_id,
                run.grouping_run_sha256,
                candidate.case.case_sha256,
                candidate.case.structure_sha256,
            ):
                raise ValueError("V3 structure assignment crosswires run or case")
            grouping_by_key[key] = assignment
        expected_grouping_keys = {
            (axis, candidate.case.case_id)
            for axis in algorithm_by_axis
            for candidate in candidates
        }
        if set(grouping_by_key) != expected_grouping_keys:
            raise ValueError(
                "V3 structure assignments do not exactly cover candidate x axis"
            )

        definition_by_id = {
            item.lineage_id: item for item in registry.definitions
        }
        lineage_by_case: dict[str, MechanismLineageAssignmentV3] = {}
        for assignment in lineages:
            candidate = candidate_by_case.get(assignment.case_id)
            if candidate is None:
                raise ValueError("V3 lineage assignment references a foreign candidate")
            if assignment.case_id in lineage_by_case:
                raise ValueError("V3 candidate has duplicate lineage assignments")
            lineage_by_case[assignment.case_id] = assignment
            if (
                assignment.case_sha256,
                assignment.registry_id,
                assignment.registry_sha256,
            ) != (
                candidate.case.case_sha256,
                registry.registry_id,
                registry.registry_sha256,
            ):
                raise ValueError("V3 lineage assignment crosswires case or registry")
            definition = definition_by_id.get(assignment.lineage_id)
            if (
                definition is None
                or definition.lineage_sha256 != assignment.lineage_sha256
            ):
                raise ValueError("V3 lineage assignment uses a foreign definition")
            if (
                definition.broad_mechanism_family
                is not candidate.case.primary_mechanism_stratum
            ):
                raise ValueError("V3 lineage family differs from candidate stratum")
            source_keys = {
                (item.source_id, item.source_record_id, item.raw_sha256)
                for item in candidate.case.source_records
            }
            evidence_keys = {
                (
                    item.source_id,
                    item.source_record_id,
                    item.source_record_raw_sha256,
                )
                for item in assignment.case_evidence_refs
            }
            if not evidence_keys <= source_keys:
                raise ValueError(
                    "V3 lineage evidence is not a subset of candidate sources"
                )
        if set(lineage_by_case) != set(candidate_by_case):
            raise ValueError(
                "V3 lineage assignments do not exactly cover the candidate pool"
            )
        assert_formal_mechanism_lineage_assignment_curation_v3(
            registry=registry,
            definition_curation_release=curation,
            assignment_curation_release=assignment_curation,
            public_assignments=lineages,
        )
        accepted_case_ids = {item.case_id for item in lineages}
        proposal_candidate_projection = {
            (
                item.candidate_id,
                item.candidate_sha256,
                item.case.case_id,
                item.case.case_sha256,
            )
            for item in assignment_curation.candidate_universe.proposals
        }
        accepted_proposals = tuple(
            item
            for item in assignment_curation.candidate_universe.proposals
            if item.case.case_id in accepted_case_ids
        )
        accepted_candidate_projection = {
            (
                item.candidate_id,
                item.candidate_sha256,
                item.case.case_id,
                item.case.case_sha256,
            )
            for item in accepted_proposals
        }
        pool_candidate_projection = {
            (
                item.candidate_id,
                item.candidate_sha256,
                item.case.case_id,
                item.case.case_sha256,
            )
            for item in candidates
        }
        if proposal_candidate_projection != pool_candidate_projection:
            raise ValueError(
                "V3 assignment candidate universe does not exactly cover pool"
            )
        if accepted_candidate_projection != pool_candidate_projection:
            raise ValueError(
                "V3 candidates are not the exact ACCEPT assignment-curation projection"
            )
        accepted_proposal_by_case = {
            item.case.case_id: item for item in accepted_proposals
        }
        if any(
            _timestamp(
                accepted_proposal_by_case[candidate.case.case_id].proposed_at
            )
            <= _timestamp(candidate.declared_at)
            for candidate in candidates
        ):
            raise ValueError(
                "V3 lineage-assignment proposal does not follow candidate declaration"
            )
        for candidate in candidates:
            lineage = lineage_by_case[candidate.case.case_id]
            expected_groups = derive_leakage_group_ids_v3(
                formula=candidate.case.formula,
                primary_mechanism_stratum=(
                    candidate.case.primary_mechanism_stratum
                ),
                source_records=candidate.case.source_records,
                structure_groups=tuple(
                    (
                        algorithm_by_axis[axis],
                        grouping_by_key[(axis, candidate.case.case_id)].canonical_group_key,
                    )
                    for axis in sorted(algorithm_by_axis, key=lambda item: item.value)
                ),
                mechanism_lineage_registry=registry,
                mechanism_lineage_id=lineage.lineage_id,
            )
            if candidate.case.leakage_group_ids != expected_groups:
                raise ValueError(
                    "V3 candidate leakage groups do not replay from pool preimage"
                )

        expected_source_keys = {
            (candidate.case.case_id, record.source_id, record.source_record_id)
            for candidate in candidates
            for record in candidate.case.source_records
        }
        source_policy_by_key = {
            (item.case_id, item.source_id, item.source_record_id): item
            for item in source_policies
        }
        if set(source_policy_by_key) != expected_source_keys:
            raise ValueError(
                "V3 source policies do not exactly cover candidate sources"
            )
        for candidate in candidates:
            case = candidate.case
            case_policies = tuple(
                source_policy_by_key[
                    (case.case_id, record.source_id, record.source_record_id)
                ]
                for record in case.source_records
            )
            assert_case_source_policy_v2(
                case=case,
                attestations=case_policies,
            )
            if any(
                item.catalog_decision is not SourceCatalogDecision.INCLUDE
                for item in case_policies
            ):
                raise ValueError("formal V3 candidate pool is INCLUDE-only")

        if self.candidate_pool_sha256 != _candidate_pool_sha256_v3(
            candidates,
            lineages,
            source_policies,
            structure_release,
            algorithms,
            runs,
            grouping_assignments,
        ):
            raise ValueError("V3 candidate-pool SHA-256 does not replay")
        registry_sealed = _timestamp(registry.sealed_at)
        curation_assembled = _timestamp(curation.assembled_at)
        assignment_curation_assembled = _timestamp(
            assignment_curation.assembled_at
        )
        pool_sealed = _timestamp(self.sealed_at)
        if max(
            registry_sealed,
            curation_assembled,
            assignment_curation_assembled,
        ) >= pool_sealed:
            raise ValueError(
                "formal lineage registry/curations were not before candidate-pool seal"
            )
        for candidate in candidates:
            declared = _timestamp(candidate.declared_at)
            assigned = _timestamp(
                lineage_by_case[candidate.case.case_id].assigned_at
            )
            if registry_sealed >= declared:
                raise ValueError(
                    "global lineage registry was not sealed before V3 candidates"
                )
            if assigned <= declared:
                raise ValueError(
                    "V3 lineage assignment does not strictly follow its candidate"
                )
            if assigned >= pool_sealed or declared >= pool_sealed:
                raise ValueError(
                    "V3 candidate or lineage assignment was not before pool seal"
                )
        _assert_identity(
            self,
            id_field="release_id",
            sha_field="release_sha256",
            prefix="candidate-pool-v3",
        )
        return self


def build_candidate_pool_release_v3(
    *,
    study_phase: Literal["PILOT_R1", "PILOT_R2"],
    candidates: tuple[FrozenCaseCandidateV1, ...],
    mechanism_lineage_registry: MechanismLineageRegistryV3,
    mechanism_lineage_curation_release: MechanismLineageCurationReleaseV3,
    mechanism_lineage_assignment_curation_release: (
        MechanismLineageAssignmentCurationReleaseV3
    ),
    candidate_lineage_assignments: tuple[MechanismLineageAssignmentV3, ...],
    structure_grouping_release: StructureGroupingPrivateEvidenceReleaseV2,
    grouping_algorithms: tuple[StructureGroupingAlgorithmV2, ...],
    grouping_runs: tuple[StructureGroupingRunV2, ...],
    grouping_assignments: tuple[StructureGroupingAssignmentV2, ...],
    source_policy_attestations: tuple[CaseSourcePolicyAttestationV2, ...],
    case_freeze_policy_sha256: str,
    eligibility_policy_sha256: str,
    sealed_at: str,
) -> CandidatePoolReleaseV3:
    """Seal the complete candidate preimage before any eligibility work."""

    registry = _revalidate(
        mechanism_lineage_registry, MechanismLineageRegistryV3
    )
    curation = _revalidate(
        mechanism_lineage_curation_release,
        MechanismLineageCurationReleaseV3,
    )
    assignment_curation = _revalidate(
        mechanism_lineage_assignment_curation_release,
        MechanismLineageAssignmentCurationReleaseV3,
    )
    structure_release = _revalidate(
        structure_grouping_release, StructureGroupingPrivateEvidenceReleaseV2
    )
    assert_structure_grouping_release_exact_replay_v2(structure_release)
    ordered_candidates = tuple(
        sorted(
            (_revalidate(item, FrozenCaseCandidateV1) for item in candidates),
            key=lambda item: (item.slot_id, item.priority, item.case.case_id),
        )
    )
    ordered_lineages = tuple(
        sorted(
            (
                _revalidate(item, MechanismLineageAssignmentV3)
                for item in candidate_lineage_assignments
            ),
            key=lambda item: (item.case_id, item.assignment_id),
        )
    )
    ordered_policies = tuple(
        sorted(
            (
                _revalidate(item, CaseSourcePolicyAttestationV2)
                for item in source_policy_attestations
            ),
            key=lambda item: (
                item.case_id,
                item.source_id,
                item.source_record_id,
            ),
        )
    )
    ordered_algorithms = tuple(
        sorted(
            (
                _revalidate(item, StructureGroupingAlgorithmV2)
                for item in grouping_algorithms
            ),
            key=lambda item: item.axis.value,
        )
    )
    ordered_runs = tuple(
        sorted(
            (_revalidate(item, StructureGroupingRunV2) for item in grouping_runs),
            key=lambda item: item.axis.value,
        )
    )
    ordered_grouping_assignments = tuple(
        sorted(
            (
                _revalidate(item, StructureGroupingAssignmentV2)
                for item in grouping_assignments
            ),
            key=lambda item: (item.axis.value, item.case_id, item.assignment_id),
        )
    )
    source_catalogs = {
        item.case.source_catalog_sha256 for item in ordered_candidates
    }
    if len(source_catalogs) != 1:
        raise ValueError("V3 candidates require one source catalog")
    return _build_identified(
        CandidatePoolReleaseV3,
        id_field="release_id",
        sha_field="release_sha256",
        prefix="candidate-pool-v3",
        values={
            "source_catalog_sha256": next(iter(source_catalogs)),
            "case_freeze_policy_sha256": case_freeze_policy_sha256,
            "eligibility_policy_sha256": eligibility_policy_sha256,
            "study_phase": study_phase,
            "mechanism_lineage_registry": registry,
            "mechanism_lineage_curation_release": curation,
            "mechanism_lineage_assignment_curation_release": (
                assignment_curation
            ),
            "candidate_pool_sha256": _candidate_pool_sha256_v3(
                ordered_candidates,
                ordered_lineages,
                ordered_policies,
                structure_release,
                ordered_algorithms,
                ordered_runs,
                ordered_grouping_assignments,
            ),
            "candidates": ordered_candidates,
            "candidate_lineage_assignments": ordered_lineages,
            "structure_grouping_release": structure_release,
            "grouping_algorithms": ordered_algorithms,
            "grouping_runs": ordered_runs,
            "grouping_assignments": ordered_grouping_assignments,
            "source_policy_attestations": ordered_policies,
            "sealed_at": sealed_at,
        },
    )


class CandidateEligibilityAssignmentV3(StrictModel):
    """Exactly two reviewers and one distinct adjudicator for one candidate."""

    schema_version: Literal["flatband-candidate-eligibility-assignment-v3"] = (
        "flatband-candidate-eligibility-assignment-v3"
    )
    assignment_id: Identifier
    assignment_sha256: Sha256
    candidate_pool_release_id: Identifier
    candidate_pool_release_sha256: Sha256
    candidate_id: Identifier
    candidate_sha256: Sha256
    slot_id: Identifier
    case_id: Identifier
    case_sha256: Sha256
    reviewer_ids: Annotated[
        tuple[Identifier, ...], Field(min_length=2, max_length=2)
    ]
    adjudicator_id: Identifier
    assigned_at: Annotated[str, Field(min_length=20, max_length=40)]
    scientific_conclusion: Literal[False] = False

    @field_validator("assigned_at")
    @classmethod
    def validate_assigned_at(cls, value: str) -> str:
        return _require_rfc3339(value)

    @model_validator(mode="after")
    def validate_assignment(self) -> "CandidateEligibilityAssignmentV3":
        if self.reviewer_ids != tuple(sorted(set(self.reviewer_ids))):
            raise ValueError("V3 eligibility reviewers must be sorted and distinct")
        if self.adjudicator_id in self.reviewer_ids:
            raise ValueError("V3 eligibility adjudicator must be distinct")
        _assert_identity(
            self,
            id_field="assignment_id",
            sha_field="assignment_sha256",
            prefix="eligibility-assignment-v3",
        )
        return self


def build_candidate_eligibility_assignment_v3(
    *,
    candidate_pool_release: CandidatePoolReleaseV3,
    candidate: FrozenCaseCandidateV1,
    reviewer_ids: tuple[str, str],
    adjudicator_id: str,
    assigned_at: str,
) -> CandidateEligibilityAssignmentV3:
    pool = _revalidate(candidate_pool_release, CandidatePoolReleaseV3)
    item = _revalidate(candidate, FrozenCaseCandidateV1)
    exact = {value.candidate_id: value for value in pool.candidates}.get(
        item.candidate_id
    )
    if exact != item:
        raise ValueError("eligibility assignment candidate is absent from V3 pool")
    return _build_identified(
        CandidateEligibilityAssignmentV3,
        id_field="assignment_id",
        sha_field="assignment_sha256",
        prefix="eligibility-assignment-v3",
        values={
            "candidate_pool_release_id": pool.release_id,
            "candidate_pool_release_sha256": pool.release_sha256,
            "candidate_id": item.candidate_id,
            "candidate_sha256": item.candidate_sha256,
            "slot_id": item.slot_id,
            "case_id": item.case.case_id,
            "case_sha256": item.case.case_sha256,
            "reviewer_ids": tuple(sorted(reviewer_ids)),
            "adjudicator_id": adjudicator_id,
            "assigned_at": assigned_at,
        },
    )


def _assert_calibration_disjoint_from_candidate_pool_v3(
    *,
    calibration_manifest: CalibrationSetManifestV2,
    candidate_pool: CandidatePoolReleaseV3,
) -> None:
    """Replay full-pool (including replacement) calibration independence."""

    calibration = _revalidate(
        calibration_manifest, CalibrationSetManifestV2
    )
    pool = _revalidate(candidate_pool, CandidatePoolReleaseV3)
    if calibration.case_freeze_policy_sha256 != pool.case_freeze_policy_sha256:
        raise ValueError("calibration and candidate pool use different freeze policies")
    if calibration.mechanism_lineage_registry != (
        pool.mechanism_lineage_registry
    ):
        raise ValueError(
            "calibration and candidate pool do not share one lineage registry"
        )
    if calibration.grouping_algorithms != pool.grouping_algorithms:
        raise ValueError(
            "calibration and candidate pool use different structure algorithms"
        )
    candidate_cases = tuple(item.case for item in pool.candidates)
    candidate_case_ids = {item.case_id for item in candidate_cases}
    candidate_case_shas = {item.case_sha256 for item in candidate_cases}
    candidate_structure_shas = {
        item.structure_sha256 for item in candidate_cases
    }
    calibration_case_ids = {item.case_id for item in calibration.cases}
    calibration_case_shas = {item.case_sha256 for item in calibration.cases}
    calibration_structure_shas = {
        item.structure_sha256 for item in calibration.cases
    }
    if candidate_case_ids & calibration_case_ids:
        raise ValueError("calibration and V3 candidate pool share a case ID")
    if candidate_case_shas & calibration_case_shas:
        raise ValueError("calibration and V3 candidate pool share a case SHA-256")
    if candidate_structure_shas & calibration_structure_shas:
        raise ValueError("calibration and V3 candidate pool share a structure")

    def source_sets(
        cases: tuple[FlatBandBenchmarkCaseV1, ...],
    ) -> tuple[set[tuple[str, str]], set[str], set[str]]:
        return (
            {
                (record.source_id, record.source_record_id)
                for case in cases
                for record in case.source_records
            },
            {
                str(record.canonical_url)
                for case in cases
                for record in case.source_records
            },
            {
                record.raw_sha256
                for case in cases
                for record in case.source_records
                if record.raw_sha256 is not None
            },
        )

    candidate_sources = source_sets(candidate_cases)
    calibration_sources = source_sets(calibration.cases)
    if candidate_sources[0] & calibration_sources[0]:
        raise ValueError("calibration and V3 candidate pool share a source record")
    if candidate_sources[1] & calibration_sources[1]:
        raise ValueError("calibration and V3 candidate pool share a source URL")
    if candidate_sources[2] & calibration_sources[2]:
        raise ValueError("calibration and V3 candidate pool share raw source content")

    candidate_groups = {
        group_id for case in candidate_cases for group_id in case.leakage_group_ids
    }
    calibration_groups = {
        item.group_id for item in calibration.memberships
    }
    if candidate_groups & calibration_groups:
        raise ValueError(
            "calibration and full V3 candidate pool share a leakage group"
        )
    definition_by_id = {
        item.lineage_id: item
        for item in pool.mechanism_lineage_registry.definitions
    }
    candidate_lineage_ids = {
        item.lineage_id for item in pool.candidate_lineage_assignments
    }
    calibration_lineage_ids = {
        item.lineage_id for item in calibration.mechanism_lineage_assignments
    }
    if candidate_lineage_ids & calibration_lineage_ids:
        raise ValueError(
            "calibration and full V3 candidate pool share a mechanism lineage"
        )
    candidate_lineage_preimages = {
        definition_by_id[item].scientific_preimage_sha256
        for item in candidate_lineage_ids
    }
    calibration_lineage_preimages = {
        definition_by_id[item].scientific_preimage_sha256
        for item in calibration_lineage_ids
    }
    if candidate_lineage_preimages & calibration_lineage_preimages:
        raise ValueError(
            "calibration and candidate pool share a mechanism scientific preimage"
        )


class CandidateEligibilityAssignmentReleaseV3(StrictModel):
    """Candidate-scope registry sealed before every raw eligibility audit."""

    schema_version: Literal[
        "flatband-candidate-eligibility-assignment-release-v3"
    ] = "flatband-candidate-eligibility-assignment-release-v3"
    release_id: Identifier
    release_sha256: Sha256
    candidate_pool_release: CandidatePoolReleaseV3
    public_identity_release: PublicExpertIdentityReleaseV2
    calibration_manifest: CalibrationSetManifestV2
    calibration_completions: Annotated[
        tuple[CalibrationCompletionV2, ...], Field(min_length=3, max_length=12)
    ]
    conflict_assessments: Annotated[
        tuple[CaseConflictAssessmentV1, ...], Field(min_length=90, max_length=24_480)
    ]
    assignments: Annotated[
        tuple[CandidateEligibilityAssignmentV3, ...],
        Field(min_length=30, max_length=2_040),
    ]
    sealed_at: Annotated[str, Field(min_length=20, max_length=40)]
    assignment_precedes_every_audit: Literal[True] = True
    scientific_conclusion: Literal[False] = False

    @field_validator("sealed_at")
    @classmethod
    def validate_sealed_at(cls, value: str) -> str:
        return _require_rfc3339(value)

    @model_validator(mode="after")
    def validate_release(self) -> "CandidateEligibilityAssignmentReleaseV3":
        pool = _revalidate(
            self.candidate_pool_release, CandidatePoolReleaseV3
        )
        identity = _revalidate(
            self.public_identity_release, PublicExpertIdentityReleaseV2
        )
        calibration = _revalidate(
            self.calibration_manifest, CalibrationSetManifestV2
        )
        completions = tuple(
            _revalidate(item, CalibrationCompletionV2)
            for item in self.calibration_completions
        )
        conflicts = tuple(
            _revalidate(item, CaseConflictAssessmentV1)
            for item in self.conflict_assessments
        )
        assignments = tuple(
            _revalidate(item, CandidateEligibilityAssignmentV3)
            for item in self.assignments
        )
        expert_by_id = {item.expert_id: item for item in identity.experts}
        candidate_by_id = {item.candidate_id: item for item in pool.candidates}
        _assert_calibration_disjoint_from_candidate_pool_v3(
            calibration_manifest=calibration,
            candidate_pool=pool,
        )
        completion_experts = tuple(item.expert_id for item in completions)
        if completion_experts != tuple(sorted(set(completion_experts))):
            raise ValueError("V3 calibration completions must be expert sorted")
        if set(completion_experts) != set(expert_by_id):
            raise ValueError("every V3 eligibility expert requires one completion")
        if len({item.completion_id for item in completions}) != len(completions) or len(
            {item.completion_sha256 for item in completions}
        ) != len(completions):
            raise ValueError("V3 calibration completion identities must be one-to-one")
        for completion in completions:
            assert_calibration_completion_replays_manifest_v2(
                completion=completion,
                calibration_manifest=calibration,
            )
            if completion.role is not expert_by_id[completion.expert_id].role:
                raise ValueError("V3 calibration completion uses a foreign role")
        conflict_keys = tuple(
            (item.case_id, item.expert_id) for item in conflicts
        )
        if conflict_keys != tuple(sorted(set(conflict_keys))):
            raise ValueError("V3 eligibility conflicts must be sorted and unique")
        expected_conflicts = {
            (candidate.case.case_id, expert_id)
            for candidate in pool.candidates
            for expert_id in expert_by_id
        }
        conflict_by_key = {
            (item.case_id, item.expert_id): item for item in conflicts
        }
        if set(conflict_by_key) != expected_conflicts:
            raise ValueError(
                "V3 eligibility conflicts must exactly cover candidate x expert"
            )
        candidate_ids = tuple(item.candidate_id for item in assignments)
        if candidate_ids != tuple(sorted(set(candidate_ids))):
            raise ValueError("V3 eligibility assignments must be candidate sorted")
        if set(candidate_ids) != set(candidate_by_id):
            raise ValueError(
                "V3 eligibility assignments do not exactly cover candidates"
            )
        if len({item.assignment_id for item in assignments}) != len(assignments) or len(
            {item.assignment_sha256 for item in assignments}
        ) != len(assignments):
            raise ValueError("V3 eligibility assignment identities must be one-to-one")
        pool_sealed = _timestamp(pool.sealed_at)
        identity_released = _timestamp(identity.released_at)
        sealed_at = _timestamp(self.sealed_at)
        for completion in completions:
            completed = _timestamp(completion.completed_at)
            if completed <= identity_released or completed >= sealed_at:
                raise ValueError(
                    "V3 calibration completion must follow identity release and precede assignment seal"
                )
        for conflict in conflicts:
            assessed = _timestamp(conflict.assessed_at)
            if assessed <= pool_sealed or assessed >= sealed_at:
                raise ValueError(
                    "V3 conflict assessment must follow pool and precede assignment seal"
                )
        for assignment in assignments:
            candidate = candidate_by_id[assignment.candidate_id]
            if (
                assignment.candidate_pool_release_id,
                assignment.candidate_pool_release_sha256,
                assignment.candidate_sha256,
                assignment.slot_id,
                assignment.case_id,
                assignment.case_sha256,
            ) != (
                pool.release_id,
                pool.release_sha256,
                candidate.candidate_sha256,
                candidate.slot_id,
                candidate.case.case_id,
                candidate.case.case_sha256,
            ):
                raise ValueError("V3 eligibility assignment crosswires its candidate")
            assigned = _timestamp(assignment.assigned_at)
            if assigned <= pool_sealed or assigned >= sealed_at:
                raise ValueError(
                    "V3 eligibility assignment must follow pool and precede its seal"
                )
            if any(
                _timestamp(item.completed_at) >= assigned for item in completions
            ):
                raise ValueError(
                    "all V3 expert calibration completions must precede assignment"
                )
            if identity_released >= assigned:
                raise ValueError("public expert identity was not released before assignment")
            for reviewer_id in assignment.reviewer_ids:
                expert = expert_by_id.get(reviewer_id)
                if expert is None or expert.role is not ExpertRole.REVIEWER:
                    raise ValueError("V3 eligibility assignment uses a non-reviewer")
                conflict = conflict_by_key[(assignment.case_id, reviewer_id)]
                if conflict.status is not ConflictStatus.CLEAR:
                    raise ValueError("recused reviewer cannot audit V3 eligibility")
                if _timestamp(conflict.assessed_at) >= assigned:
                    raise ValueError("reviewer conflict was not sealed before assignment")
            adjudicator = expert_by_id.get(assignment.adjudicator_id)
            if adjudicator is None or adjudicator.role is not ExpertRole.ADJUDICATOR:
                raise ValueError("V3 eligibility assignment uses a non-adjudicator")
            conflict = conflict_by_key[
                (assignment.case_id, assignment.adjudicator_id)
            ]
            if conflict.status is not ConflictStatus.CLEAR:
                raise ValueError("recused adjudicator cannot decide V3 eligibility")
            if _timestamp(conflict.assessed_at) >= assigned:
                raise ValueError("adjudicator conflict was not sealed before assignment")
        _assert_identity(
            self,
            id_field="release_id",
            sha_field="release_sha256",
            prefix="eligibility-roster-v3",
        )
        return self


def build_candidate_eligibility_assignment_release_v3(
    *,
    candidate_pool_release: CandidatePoolReleaseV3,
    public_identity_release: PublicExpertIdentityReleaseV2,
    calibration_manifest: CalibrationSetManifestV2,
    calibration_completions: tuple[CalibrationCompletionV2, ...],
    conflict_assessments: tuple[CaseConflictAssessmentV1, ...],
    assignments: tuple[CandidateEligibilityAssignmentV3, ...],
    sealed_at: str,
) -> CandidateEligibilityAssignmentReleaseV3:
    pool = _revalidate(candidate_pool_release, CandidatePoolReleaseV3)
    identity = _revalidate(
        public_identity_release, PublicExpertIdentityReleaseV2
    )
    calibration = _revalidate(calibration_manifest, CalibrationSetManifestV2)
    ordered_completions = tuple(
        sorted(
            (
                _revalidate(item, CalibrationCompletionV2)
                for item in calibration_completions
            ),
            key=lambda item: item.expert_id,
        )
    )
    ordered_conflicts = tuple(
        sorted(
            (
                _revalidate(item, CaseConflictAssessmentV1)
                for item in conflict_assessments
            ),
            key=lambda item: (item.case_id, item.expert_id),
        )
    )
    ordered_assignments = tuple(
        sorted(
            (
                _revalidate(item, CandidateEligibilityAssignmentV3)
                for item in assignments
            ),
            key=lambda item: item.candidate_id,
        )
    )
    return _build_identified(
        CandidateEligibilityAssignmentReleaseV3,
        id_field="release_id",
        sha_field="release_sha256",
        prefix="eligibility-roster-v3",
        values={
            "candidate_pool_release": pool,
            "public_identity_release": identity,
            "calibration_manifest": calibration,
            "calibration_completions": ordered_completions,
            "conflict_assessments": ordered_conflicts,
            "assignments": ordered_assignments,
            "sealed_at": sealed_at,
        },
    )


def _validate_eligibility_outcome_v3(
    *,
    status: CaseEligibilityStatus,
    reason_codes: tuple[CaseEligibilityReasonCode, ...],
) -> None:
    reason_values = tuple(item.value for item in reason_codes)
    if reason_values != tuple(sorted(set(reason_values))):
        raise ValueError("V3 eligibility reasons must be sorted and unique")
    include_reason = CaseEligibilityReasonCode.MEETS_ALL_PREREGISTERED_CRITERIA
    if status is CaseEligibilityStatus.INCLUDED:
        if reason_codes != (include_reason,):
            raise ValueError("included V3 candidate requires only the all-criteria reason")
    elif include_reason in reason_codes:
        raise ValueError("excluded V3 candidate cannot claim all criteria were met")


def _validate_derivative_exclusion_v3(
    *,
    derivative_class: DerivativeEligibilityClassV3,
    status: CaseEligibilityStatus,
    reason_codes: tuple[CaseEligibilityReasonCode, ...],
) -> None:
    reason = (
        CaseEligibilityReasonCode
        .UNSUPPORTED_DERIVATIVE_WITHOUT_PARENT_TRANSFORMATION_LINEAGE
    )
    if derivative_class is DerivativeEligibilityClassV3.NOT_A_DERIVATIVE:
        if reason in reason_codes:
            raise ValueError("non-derivative audit cannot use the derivative exclusion")
        return
    if status is not CaseEligibilityStatus.EXCLUDED or reason not in reason_codes:
        raise ValueError(
            "v0 derivative candidate must fail closed without a parent/transformation lineage axis"
        )


class DerivativeEvidenceRefV3(StrictModel):
    """Private reviewer evidence joined to one frozen candidate source record."""

    source_id: Identifier
    source_record_id: Identifier
    source_record_raw_sha256: Sha256
    private_assessment_artifact_sha256: Sha256
    private_assessment_locator_sha256: Sha256


def _validate_final_derivative_class_v3(
    *,
    raw_classes: tuple[DerivativeEligibilityClassV3, ...],
    final_class: DerivativeEligibilityClassV3,
    status: CaseEligibilityStatus,
    reason_codes: tuple[CaseEligibilityReasonCode, ...],
) -> None:
    derivative_classes = {
        item
        for item in raw_classes
        if item is not DerivativeEligibilityClassV3.NOT_A_DERIVATIVE
    }
    if derivative_classes:
        if final_class not in derivative_classes:
            raise ValueError(
                "V3 final derivative class must preserve a raw derivative finding"
            )
    elif final_class is not DerivativeEligibilityClassV3.NOT_A_DERIVATIVE:
        raise ValueError("V3 final derivative class is absent from both raw audits")
    _validate_derivative_exclusion_v3(
        derivative_class=final_class,
        status=status,
        reason_codes=reason_codes,
    )


class EligibilityRawAuditV3(StrictModel):
    """One assigned reviewer's independent, immutable eligibility audit."""

    schema_version: Literal["flatband-eligibility-raw-audit-v3"] = (
        "flatband-eligibility-raw-audit-v3"
    )
    audit_id: Identifier
    audit_sha256: Sha256
    assignment_release_id: Identifier
    assignment_release_sha256: Sha256
    assignment_id: Identifier
    assignment_sha256: Sha256
    candidate_id: Identifier
    candidate_sha256: Sha256
    slot_id: Identifier
    case_id: Identifier
    case_sha256: Sha256
    reviewer_id: Identifier
    derivative_class: DerivativeEligibilityClassV3
    derivative_evidence_refs: Annotated[
        tuple[DerivativeEvidenceRefV3, ...], Field(min_length=1, max_length=16)
    ]
    status: CaseEligibilityStatus
    reason_codes: Annotated[
        tuple[CaseEligibilityReasonCode, ...], Field(min_length=1, max_length=16)
    ]
    assessment_protocol_sha256: Sha256
    rationale_sha256: Sha256
    audited_at: Annotated[str, Field(min_length=20, max_length=40)]
    independently_completed: Literal[True] = True
    applies_to_all_registered_systems: Literal[True] = True
    system_specific_override_allowed: Literal[False] = False
    scientific_conclusion: Literal[False] = False

    @field_validator("audited_at")
    @classmethod
    def validate_audited_at(cls, value: str) -> str:
        return _require_rfc3339(value)

    @model_validator(mode="after")
    def validate_audit(self) -> "EligibilityRawAuditV3":
        evidence_keys = tuple(
            (
                item.source_id,
                item.source_record_id,
                item.source_record_raw_sha256,
                item.private_assessment_artifact_sha256,
                item.private_assessment_locator_sha256,
            )
            for item in self.derivative_evidence_refs
        )
        if evidence_keys != tuple(sorted(set(evidence_keys))):
            raise ValueError(
                "V3 derivative evidence refs must be sorted and unique"
            )
        for values, label in (
            (
                tuple(item[:3] for item in evidence_keys),
                "candidate source triple",
            ),
            (
                tuple(item[3] for item in evidence_keys),
                "private assessment artifact SHA-256",
            ),
            (
                tuple(item[4] for item in evidence_keys),
                "private assessment locator SHA-256",
            ),
        ):
            if len(values) != len(set(values)):
                raise ValueError(
                    f"V3 derivative evidence {label} values must be one-to-one"
                )
        _validate_eligibility_outcome_v3(
            status=self.status,
            reason_codes=self.reason_codes,
        )
        _validate_derivative_exclusion_v3(
            derivative_class=self.derivative_class,
            status=self.status,
            reason_codes=self.reason_codes,
        )
        _assert_identity(
            self,
            id_field="audit_id",
            sha_field="audit_sha256",
            prefix="eligibility-audit-v3",
        )
        return self


def build_eligibility_raw_audit_v3(
    *,
    assignment_release: CandidateEligibilityAssignmentReleaseV3,
    candidate_id: str,
    reviewer_id: str,
    derivative_class: DerivativeEligibilityClassV3,
    derivative_evidence_refs: tuple[DerivativeEvidenceRefV3, ...],
    status: CaseEligibilityStatus,
    reason_codes: tuple[CaseEligibilityReasonCode, ...],
    rationale_sha256: str,
    audited_at: str,
) -> EligibilityRawAuditV3:
    release = _revalidate(
        assignment_release, CandidateEligibilityAssignmentReleaseV3
    )
    assignment_by_candidate = {
        item.candidate_id: item for item in release.assignments
    }
    candidate_by_id = {
        item.candidate_id: item
        for item in release.candidate_pool_release.candidates
    }
    assignment = assignment_by_candidate.get(candidate_id)
    if assignment is None:
        raise ValueError("raw eligibility audit references a foreign candidate")
    if reviewer_id not in assignment.reviewer_ids:
        raise ValueError("raw eligibility audit reviewer is not assigned")
    candidate = candidate_by_id[candidate_id]
    ordered_evidence = tuple(
        sorted(
            (
                _revalidate(item, DerivativeEvidenceRefV3)
                for item in derivative_evidence_refs
            ),
            key=lambda item: (
                item.source_id,
                item.source_record_id,
                item.source_record_raw_sha256,
                item.private_assessment_artifact_sha256,
                item.private_assessment_locator_sha256,
            ),
        )
    )
    candidate_source_keys = {
        (item.source_id, item.source_record_id, item.raw_sha256)
        for item in candidate.case.source_records
    }
    if not ordered_evidence or not {
        (item.source_id, item.source_record_id, item.source_record_raw_sha256)
        for item in ordered_evidence
    } <= candidate_source_keys:
        raise ValueError(
            "V3 derivative evidence must be a nonempty exact subset of candidate sources"
        )
    ordered_reasons = tuple(sorted(reason_codes, key=lambda item: item.value))
    return _build_identified(
        EligibilityRawAuditV3,
        id_field="audit_id",
        sha_field="audit_sha256",
        prefix="eligibility-audit-v3",
        values={
            "assignment_release_id": release.release_id,
            "assignment_release_sha256": release.release_sha256,
            "assignment_id": assignment.assignment_id,
            "assignment_sha256": assignment.assignment_sha256,
            "candidate_id": assignment.candidate_id,
            "candidate_sha256": assignment.candidate_sha256,
            "slot_id": assignment.slot_id,
            "case_id": assignment.case_id,
            "case_sha256": assignment.case_sha256,
            "reviewer_id": reviewer_id,
            "derivative_class": derivative_class,
            "derivative_evidence_refs": ordered_evidence,
            "status": status,
            "reason_codes": ordered_reasons,
            "assessment_protocol_sha256": (
                release.candidate_pool_release.eligibility_policy_sha256
            ),
            "rationale_sha256": rationale_sha256,
            "audited_at": audited_at,
        },
    )


class EligibilityAuditRefV3(StrictModel):
    reviewer_id: Identifier
    audit_id: Identifier
    audit_sha256: Sha256
    derivative_class: DerivativeEligibilityClassV3
    status: CaseEligibilityStatus
    reason_codes: Annotated[
        tuple[CaseEligibilityReasonCode, ...], Field(min_length=1, max_length=16)
    ]

    @model_validator(mode="after")
    def validate_ref(self) -> "EligibilityAuditRefV3":
        _validate_eligibility_outcome_v3(
            status=self.status,
            reason_codes=self.reason_codes,
        )
        _validate_derivative_exclusion_v3(
            derivative_class=self.derivative_class,
            status=self.status,
            reason_codes=self.reason_codes,
        )
        return self


def _audit_ref_v3(audit: EligibilityRawAuditV3) -> EligibilityAuditRefV3:
    return EligibilityAuditRefV3(
        reviewer_id=audit.reviewer_id,
        audit_id=audit.audit_id,
        audit_sha256=audit.audit_sha256,
        derivative_class=audit.derivative_class,
        status=audit.status,
        reason_codes=audit.reason_codes,
    )


class EligibilityAdjudicationV3(StrictModel):
    """A distinct adjudicator's resolution, present only for disagreement."""

    schema_version: Literal["flatband-eligibility-adjudication-v3"] = (
        "flatband-eligibility-adjudication-v3"
    )
    adjudication_id: Identifier
    adjudication_sha256: Sha256
    assignment_release_id: Identifier
    assignment_release_sha256: Sha256
    assignment_id: Identifier
    assignment_sha256: Sha256
    candidate_id: Identifier
    candidate_sha256: Sha256
    slot_id: Identifier
    case_id: Identifier
    case_sha256: Sha256
    adjudicator_id: Identifier
    raw_audit_refs: Annotated[
        tuple[EligibilityAuditRefV3, ...], Field(min_length=2, max_length=2)
    ]
    final_derivative_class: DerivativeEligibilityClassV3
    final_status: CaseEligibilityStatus
    final_reason_codes: Annotated[
        tuple[CaseEligibilityReasonCode, ...], Field(min_length=1, max_length=16)
    ]
    assessment_protocol_sha256: Sha256
    rationale_sha256: Sha256
    adjudicated_at: Annotated[str, Field(min_length=20, max_length=40)]
    disagreement_required: Literal[True] = True
    scientific_conclusion: Literal[False] = False

    @field_validator("adjudicated_at")
    @classmethod
    def validate_adjudicated_at(cls, value: str) -> str:
        return _require_rfc3339(value)

    @model_validator(mode="after")
    def validate_adjudication(self) -> "EligibilityAdjudicationV3":
        refs = tuple(
            _revalidate(item, EligibilityAuditRefV3)
            for item in self.raw_audit_refs
        )
        reviewer_ids = tuple(item.reviewer_id for item in refs)
        if reviewer_ids != tuple(sorted(set(reviewer_ids))):
            raise ValueError("adjudication raw-audit refs must be reviewer sorted")
        outcomes = tuple(
            (item.derivative_class, item.status, item.reason_codes)
            for item in refs
        )
        if outcomes[0] == outcomes[1]:
            raise ValueError("consensus raw audits cannot be adjudicated")
        _validate_eligibility_outcome_v3(
            status=self.final_status,
            reason_codes=self.final_reason_codes,
        )
        _validate_final_derivative_class_v3(
            raw_classes=tuple(item.derivative_class for item in refs),
            final_class=self.final_derivative_class,
            status=self.final_status,
            reason_codes=self.final_reason_codes,
        )
        _assert_identity(
            self,
            id_field="adjudication_id",
            sha_field="adjudication_sha256",
            prefix="eligibility-adjudication-v3",
        )
        return self


def build_eligibility_adjudication_v3(
    *,
    assignment_release: CandidateEligibilityAssignmentReleaseV3,
    raw_audits: tuple[EligibilityRawAuditV3, EligibilityRawAuditV3],
    final_derivative_class: DerivativeEligibilityClassV3,
    final_status: CaseEligibilityStatus,
    final_reason_codes: tuple[CaseEligibilityReasonCode, ...],
    rationale_sha256: str,
    adjudicated_at: str,
) -> EligibilityAdjudicationV3:
    release = _revalidate(
        assignment_release, CandidateEligibilityAssignmentReleaseV3
    )
    audits = tuple(
        sorted(
            (_revalidate(item, EligibilityRawAuditV3) for item in raw_audits),
            key=lambda item: item.reviewer_id,
        )
    )
    candidate_ids = {item.candidate_id for item in audits}
    if len(candidate_ids) != 1:
        raise ValueError("adjudication audits must reference one candidate")
    assignment = {
        item.candidate_id: item for item in release.assignments
    }.get(next(iter(candidate_ids)))
    if assignment is None:
        raise ValueError("adjudication candidate is absent from assignment release")
    ordered_reasons = tuple(
        sorted(final_reason_codes, key=lambda item: item.value)
    )
    return _build_identified(
        EligibilityAdjudicationV3,
        id_field="adjudication_id",
        sha_field="adjudication_sha256",
        prefix="eligibility-adjudication-v3",
        values={
            "assignment_release_id": release.release_id,
            "assignment_release_sha256": release.release_sha256,
            "assignment_id": assignment.assignment_id,
            "assignment_sha256": assignment.assignment_sha256,
            "candidate_id": assignment.candidate_id,
            "candidate_sha256": assignment.candidate_sha256,
            "slot_id": assignment.slot_id,
            "case_id": assignment.case_id,
            "case_sha256": assignment.case_sha256,
            "adjudicator_id": assignment.adjudicator_id,
            "raw_audit_refs": tuple(_audit_ref_v3(item) for item in audits),
            "final_derivative_class": final_derivative_class,
            "final_status": final_status,
            "final_reason_codes": ordered_reasons,
            "assessment_protocol_sha256": (
                release.candidate_pool_release.eligibility_policy_sha256
            ),
            "rationale_sha256": rationale_sha256,
            "adjudicated_at": adjudicated_at,
        },
    )


class EligibilityDecisionV3(StrictModel):
    """A derived decision with no assessor-controlled outcome field."""

    schema_version: Literal["flatband-eligibility-decision-v3"] = (
        "flatband-eligibility-decision-v3"
    )
    decision_id: Identifier
    decision_sha256: Sha256
    assignment_id: Identifier
    assignment_sha256: Sha256
    candidate_id: Identifier
    candidate_sha256: Sha256
    slot_id: Identifier
    case_id: Identifier
    case_sha256: Sha256
    raw_audit_refs: Annotated[
        tuple[EligibilityAuditRefV3, ...], Field(min_length=2, max_length=2)
    ]
    adjudication_id: Identifier | None = None
    adjudication_sha256: Sha256 | None = None
    derivative_class: DerivativeEligibilityClassV3
    status: CaseEligibilityStatus
    reason_codes: Annotated[
        tuple[CaseEligibilityReasonCode, ...], Field(min_length=1, max_length=16)
    ]
    decided_at: Annotated[str, Field(min_length=20, max_length=40)]
    derivation: Literal["TWO_RAW_AUDITS_AND_IFF_DISAGREEMENT_ADJUDICATION_V3"] = (
        "TWO_RAW_AUDITS_AND_IFF_DISAGREEMENT_ADJUDICATION_V3"
    )
    scientific_conclusion: Literal[False] = False

    @field_validator("decided_at")
    @classmethod
    def validate_decided_at(cls, value: str) -> str:
        return _require_rfc3339(value)

    @model_validator(mode="after")
    def validate_decision(self) -> "EligibilityDecisionV3":
        refs = tuple(
            _revalidate(item, EligibilityAuditRefV3)
            for item in self.raw_audit_refs
        )
        reviewer_ids = tuple(item.reviewer_id for item in refs)
        if reviewer_ids != tuple(sorted(set(reviewer_ids))):
            raise ValueError("V3 decision audit refs must be reviewer sorted")
        if (self.adjudication_id is None) != (
            self.adjudication_sha256 is None
        ):
            raise ValueError("V3 adjudication ID/SHA must be present together")
        _validate_eligibility_outcome_v3(
            status=self.status,
            reason_codes=self.reason_codes,
        )
        _validate_final_derivative_class_v3(
            raw_classes=tuple(item.derivative_class for item in refs),
            final_class=self.derivative_class,
            status=self.status,
            reason_codes=self.reason_codes,
        )
        _assert_identity(
            self,
            id_field="decision_id",
            sha_field="decision_sha256",
            prefix="eligibility-decision-v3",
        )
        return self


def _derive_decisions_v3(
    *,
    assignment_release: CandidateEligibilityAssignmentReleaseV3,
    raw_audits: tuple[EligibilityRawAuditV3, ...],
    adjudications: tuple[EligibilityAdjudicationV3, ...],
    sealed_at: str,
) -> tuple[EligibilityDecisionV3, ...]:
    release = _revalidate(
        assignment_release, CandidateEligibilityAssignmentReleaseV3
    )
    audits = tuple(
        _revalidate(item, EligibilityRawAuditV3) for item in raw_audits
    )
    adjudications = tuple(
        _revalidate(item, EligibilityAdjudicationV3) for item in adjudications
    )
    assignment_by_candidate = {
        item.candidate_id: item for item in release.assignments
    }
    candidate_by_id = {
        item.candidate_id: item
        for item in release.candidate_pool_release.candidates
    }
    expected_audit_keys = {
        (assignment.candidate_id, reviewer_id)
        for assignment in release.assignments
        for reviewer_id in assignment.reviewer_ids
    }
    audit_by_key: dict[tuple[str, str], EligibilityRawAuditV3] = {}
    for audit in audits:
        key = (audit.candidate_id, audit.reviewer_id)
        if key in audit_by_key:
            raise ValueError("duplicate V3 raw audit for candidate/reviewer")
        audit_by_key[key] = audit
    if set(audit_by_key) != expected_audit_keys:
        raise ValueError(
            "V3 raw audits must exactly cover candidate x assigned reviewer"
        )
    if len({item.audit_id for item in audits}) != len(audits) or len(
        {item.audit_sha256 for item in audits}
    ) != len(audits):
        raise ValueError("V3 raw audit identities must be one-to-one")
    assignment_sealed = _timestamp(release.sealed_at)
    eligibility_sealed = _timestamp(_require_rfc3339(sealed_at))
    disagreement_candidates: set[str] = set()
    for candidate_id, assignment in assignment_by_candidate.items():
        candidate_audits = tuple(
            audit_by_key[(candidate_id, reviewer_id)]
            for reviewer_id in assignment.reviewer_ids
        )
        for audit in candidate_audits:
            if (
                audit.assignment_release_id,
                audit.assignment_release_sha256,
                audit.assignment_id,
                audit.assignment_sha256,
                audit.candidate_sha256,
                audit.slot_id,
                audit.case_id,
                audit.case_sha256,
                audit.assessment_protocol_sha256,
            ) != (
                release.release_id,
                release.release_sha256,
                assignment.assignment_id,
                assignment.assignment_sha256,
                assignment.candidate_sha256,
                assignment.slot_id,
                assignment.case_id,
                assignment.case_sha256,
                release.candidate_pool_release.eligibility_policy_sha256,
            ):
                raise ValueError("V3 raw audit crosswires assignment or candidate")
            candidate_source_keys = {
                (item.source_id, item.source_record_id, item.raw_sha256)
                for item in candidate_by_id[candidate_id].case.source_records
            }
            evidence_source_keys = {
                (
                    item.source_id,
                    item.source_record_id,
                    item.source_record_raw_sha256,
                )
                for item in audit.derivative_evidence_refs
            }
            if not evidence_source_keys or not (
                evidence_source_keys <= candidate_source_keys
            ):
                raise ValueError(
                    "V3 derivative evidence does not join an exact candidate source subset"
                )
            audited = _timestamp(audit.audited_at)
            if audited <= assignment_sealed or audited >= eligibility_sealed:
                raise ValueError(
                    "V3 raw audit must follow assignment seal and precede eligibility seal"
                )
        outcomes = {
            (item.derivative_class, item.status, item.reason_codes)
            for item in candidate_audits
        }
        if len(outcomes) != 1:
            disagreement_candidates.add(candidate_id)

    adjudication_by_candidate: dict[str, EligibilityAdjudicationV3] = {}
    for adjudication in adjudications:
        if adjudication.candidate_id in adjudication_by_candidate:
            raise ValueError("duplicate V3 adjudication for candidate")
        adjudication_by_candidate[adjudication.candidate_id] = adjudication
    if set(adjudication_by_candidate) != disagreement_candidates:
        raise ValueError(
            "V3 adjudications must exactly cover and only cover disagreements"
        )
    if len({item.adjudication_id for item in adjudications}) != len(adjudications) or len(
        {item.adjudication_sha256 for item in adjudications}
    ) != len(adjudications):
        raise ValueError("V3 adjudication identities must be one-to-one")

    decisions: list[EligibilityDecisionV3] = []
    for candidate_id in sorted(assignment_by_candidate):
        assignment = assignment_by_candidate[candidate_id]
        candidate_audits = tuple(
            audit_by_key[(candidate_id, reviewer_id)]
            for reviewer_id in assignment.reviewer_ids
        )
        refs = tuple(_audit_ref_v3(item) for item in candidate_audits)
        adjudication = adjudication_by_candidate.get(candidate_id)
        if adjudication is None:
            derivative_class = candidate_audits[0].derivative_class
            status = candidate_audits[0].status
            reasons = candidate_audits[0].reason_codes
            decided_at = max(
                candidate_audits, key=lambda item: _timestamp(item.audited_at)
            ).audited_at
            adjudication_id = None
            adjudication_sha256 = None
        else:
            if (
                adjudication.assignment_release_id,
                adjudication.assignment_release_sha256,
                adjudication.assignment_id,
                adjudication.assignment_sha256,
                adjudication.candidate_sha256,
                adjudication.slot_id,
                adjudication.case_id,
                adjudication.case_sha256,
                adjudication.adjudicator_id,
                adjudication.raw_audit_refs,
                adjudication.assessment_protocol_sha256,
            ) != (
                release.release_id,
                release.release_sha256,
                assignment.assignment_id,
                assignment.assignment_sha256,
                assignment.candidate_sha256,
                assignment.slot_id,
                assignment.case_id,
                assignment.case_sha256,
                assignment.adjudicator_id,
                refs,
                release.candidate_pool_release.eligibility_policy_sha256,
            ):
                raise ValueError("V3 adjudication crosswires assignment or raw audits")
            adjudicated = _timestamp(adjudication.adjudicated_at)
            if adjudicated <= max(
                _timestamp(item.audited_at) for item in candidate_audits
            ) or adjudicated >= eligibility_sealed:
                raise ValueError(
                    "V3 adjudication must follow both audits and precede eligibility seal"
                )
            status = adjudication.final_status
            reasons = adjudication.final_reason_codes
            derivative_class = adjudication.final_derivative_class
            decided_at = adjudication.adjudicated_at
            adjudication_id = adjudication.adjudication_id
            adjudication_sha256 = adjudication.adjudication_sha256
        decisions.append(
            _build_identified(
                EligibilityDecisionV3,
                id_field="decision_id",
                sha_field="decision_sha256",
                prefix="eligibility-decision-v3",
                values={
                    "assignment_id": assignment.assignment_id,
                    "assignment_sha256": assignment.assignment_sha256,
                    "candidate_id": assignment.candidate_id,
                    "candidate_sha256": assignment.candidate_sha256,
                    "slot_id": assignment.slot_id,
                    "case_id": assignment.case_id,
                    "case_sha256": assignment.case_sha256,
                    "raw_audit_refs": refs,
                    "adjudication_id": adjudication_id,
                    "adjudication_sha256": adjudication_sha256,
                    "derivative_class": derivative_class,
                    "status": status,
                    "reason_codes": reasons,
                    "decided_at": decided_at,
                },
            )
        )
    return tuple(decisions)


def _derive_selections_v3(
    *,
    candidate_pool: CandidatePoolReleaseV3,
    decisions: tuple[EligibilityDecisionV3, ...],
) -> tuple[ActiveCaseSelectionV1, ...]:
    decision_by_candidate = {item.candidate_id: item for item in decisions}
    if set(decision_by_candidate) != {
        item.candidate_id for item in candidate_pool.candidates
    }:
        raise ValueError("V3 decisions do not exactly cover candidate pool")
    by_slot: dict[str, list[FrozenCaseCandidateV1]] = defaultdict(list)
    for candidate in candidate_pool.candidates:
        by_slot[candidate.slot_id].append(candidate)
    selections: list[ActiveCaseSelectionV1] = []
    for slot_id in sorted(by_slot):
        selected = next(
            (
                item
                for item in by_slot[slot_id]
                if decision_by_candidate[item.candidate_id].status
                is CaseEligibilityStatus.INCLUDED
            ),
            None,
        )
        if selected is None:
            selections.append(
                ActiveCaseSelectionV1(
                    slot_id=slot_id,
                    replacement_activated=False,
                )
            )
        else:
            selections.append(
                ActiveCaseSelectionV1(
                    slot_id=slot_id,
                    selected_candidate_id=selected.candidate_id,
                    selected_candidate_sha256=selected.candidate_sha256,
                    selected_case_id=selected.case.case_id,
                    selected_case_sha256=selected.case.case_sha256,
                    selected_priority=selected.priority,
                    replacement_activated=selected.priority > 0,
                )
            )
    return tuple(selections)


class PreRunEligibilityReleaseV3(StrictModel):
    """The only formal Pilot selection seal; all selection is rederived."""

    schema_version: Literal["flatband-pre-run-eligibility-release-v3"] = (
        "flatband-pre-run-eligibility-release-v3"
    )
    release_id: Identifier
    release_sha256: Sha256
    assignment_release: CandidateEligibilityAssignmentReleaseV3
    raw_audits: Annotated[
        tuple[EligibilityRawAuditV3, ...], Field(min_length=60, max_length=4_080)
    ]
    adjudications: Annotated[
        tuple[EligibilityAdjudicationV3, ...], Field(max_length=2_040)
    ]
    decisions: Annotated[
        tuple[EligibilityDecisionV3, ...], Field(min_length=30, max_length=2_040)
    ]
    unsupported_derivative_exclusion_candidate_ids: Annotated[
        tuple[Identifier, ...], Field(max_length=36)
    ]
    active_selections: Annotated[
        tuple[ActiveCaseSelectionV1, ...], Field(min_length=30, max_length=30)
    ]
    execution_authorized: bool
    sealed_at: Annotated[str, Field(min_length=20, max_length=40)]
    exact_two_independent_audits_per_candidate: Literal[True] = True
    adjudication_if_and_only_if_disagreement: Literal[True] = True
    complete_case_omission_allowed: Literal[False] = False
    scientific_conclusion: Literal[False] = False

    @field_validator("sealed_at")
    @classmethod
    def validate_sealed_at(cls, value: str) -> str:
        return _require_rfc3339(value)

    @model_validator(mode="after")
    def validate_release(self) -> "PreRunEligibilityReleaseV3":
        assignments = _revalidate(
            self.assignment_release, CandidateEligibilityAssignmentReleaseV3
        )
        audits = tuple(
            _revalidate(item, EligibilityRawAuditV3) for item in self.raw_audits
        )
        adjudications = tuple(
            _revalidate(item, EligibilityAdjudicationV3)
            for item in self.adjudications
        )
        decisions = tuple(
            _revalidate(item, EligibilityDecisionV3) for item in self.decisions
        )
        audit_order = tuple(
            (item.candidate_id, item.reviewer_id, item.audit_id) for item in audits
        )
        if audit_order != tuple(sorted(audit_order)):
            raise ValueError("V3 raw audits must be candidate/reviewer/ID sorted")
        adjudication_order = tuple(
            (item.candidate_id, item.adjudication_id) for item in adjudications
        )
        if adjudication_order != tuple(sorted(adjudication_order)):
            raise ValueError("V3 adjudications must be candidate/ID sorted")
        expected_decisions = _derive_decisions_v3(
            assignment_release=assignments,
            raw_audits=audits,
            adjudications=adjudications,
            sealed_at=self.sealed_at,
        )
        if decisions != expected_decisions:
            raise ValueError("V3 decisions do not uniquely replay from raw audits")
        derivative_exclusion_candidate_ids = tuple(
            sorted(
                {
                    decision.candidate_id
                    for decision in decisions
                    if decision.derivative_class
                    is not DerivativeEligibilityClassV3.NOT_A_DERIVATIVE
                }
            )
        )
        if self.unsupported_derivative_exclusion_candidate_ids != (
            derivative_exclusion_candidate_ids
        ):
            raise ValueError(
                "V3 derivative exclusion ledger does not exactly replay from raw audits"
            )
        pool = assignments.candidate_pool_release
        selections = tuple(
            ActiveCaseSelectionV1.model_validate(
                item.model_dump(mode="python", round_trip=True)
            )
            for item in self.active_selections
        )
        expected_selections = _derive_selections_v3(
            candidate_pool=pool,
            decisions=decisions,
        )
        if selections != expected_selections:
            raise ValueError("V3 active selections do not replay from decisions")
        expected_authorized = (
            len(selections) == pool.expected_slot_count
            and all(item.selected_candidate_id is not None for item in selections)
        )
        if self.execution_authorized != expected_authorized:
            raise ValueError("V3 execution authorization does not replay")
        _assert_identity(
            self,
            id_field="release_id",
            sha_field="release_sha256",
            prefix="pre-run-eligibility-v3",
        )
        return self


def build_pre_run_eligibility_release_v3(
    *,
    assignment_release: CandidateEligibilityAssignmentReleaseV3,
    raw_audits: tuple[EligibilityRawAuditV3, ...],
    adjudications: tuple[EligibilityAdjudicationV3, ...],
    sealed_at: str,
) -> PreRunEligibilityReleaseV3:
    """Build selection only from the two-audit/iff-adjudication preimage."""

    assignments = _revalidate(
        assignment_release, CandidateEligibilityAssignmentReleaseV3
    )
    audits = tuple(
        sorted(
            (_revalidate(item, EligibilityRawAuditV3) for item in raw_audits),
            key=lambda item: (item.candidate_id, item.reviewer_id, item.audit_id),
        )
    )
    ordered_adjudications = tuple(
        sorted(
            (
                _revalidate(item, EligibilityAdjudicationV3)
                for item in adjudications
            ),
            key=lambda item: (item.candidate_id, item.adjudication_id),
        )
    )
    decisions = _derive_decisions_v3(
        assignment_release=assignments,
        raw_audits=audits,
        adjudications=ordered_adjudications,
        sealed_at=sealed_at,
    )
    selections = _derive_selections_v3(
        candidate_pool=assignments.candidate_pool_release,
        decisions=decisions,
    )
    derivative_exclusion_candidate_ids = tuple(
        sorted(
            {
                decision.candidate_id
                for decision in decisions
                if decision.derivative_class
                is not DerivativeEligibilityClassV3.NOT_A_DERIVATIVE
            }
        )
    )
    authorized = (
        len(selections) == assignments.candidate_pool_release.expected_slot_count
        and all(item.selected_candidate_id is not None for item in selections)
    )
    return _build_identified(
        PreRunEligibilityReleaseV3,
        id_field="release_id",
        sha_field="release_sha256",
        prefix="pre-run-eligibility-v3",
        values={
            "assignment_release": assignments,
            "raw_audits": audits,
            "adjudications": ordered_adjudications,
            "decisions": decisions,
            "unsupported_derivative_exclusion_candidate_ids": (
                derivative_exclusion_candidate_ids
            ),
            "active_selections": selections,
            "execution_authorized": authorized,
            "sealed_at": sealed_at,
        },
    )


def assert_pre_run_eligibility_ready_v3(
    release: PreRunEligibilityReleaseV3,
) -> None:
    value = _revalidate(release, PreRunEligibilityReleaseV3)
    if not value.execution_authorized:
        raise ValueError("V3 eligibility leaves one or more Pilot slots unselected")


def _selected_candidates_v3(
    release: PreRunEligibilityReleaseV3,
) -> tuple[FrozenCaseCandidateV1, ...]:
    eligibility = _revalidate(release, PreRunEligibilityReleaseV3)
    assert_pre_run_eligibility_ready_v3(eligibility)
    pool = eligibility.assignment_release.candidate_pool_release
    candidate_by_id = {item.candidate_id: item for item in pool.candidates}
    selected: list[FrozenCaseCandidateV1] = []
    for selection in eligibility.active_selections:
        if selection.selected_candidate_id is None:
            raise ValueError("formal V3 eligibility contains an empty slot")
        candidate = candidate_by_id.get(selection.selected_candidate_id)
        if candidate is None or (
            candidate.candidate_sha256,
            candidate.slot_id,
            candidate.case.case_id,
            candidate.case.case_sha256,
            candidate.priority,
        ) != (
            selection.selected_candidate_sha256,
            selection.slot_id,
            selection.selected_case_id,
            selection.selected_case_sha256,
            selection.selected_priority,
        ):
            raise ValueError("V3 selected candidate does not replay from pool")
        selected.append(candidate)
    if len(selected) != 30 or len({item.slot_id for item in selected}) != 30:
        raise ValueError("formal V3 selection must contain one case in 30 slots")
    return tuple(selected)


class FrozenCaseReleaseV3(StrictModel):
    """Post-selection split/leakage/expert closure for the formal Pilot."""

    schema_version: Literal["flatband-frozen-case-release-v3"] = (
        "flatband-frozen-case-release-v3"
    )
    release_id: Identifier
    release_sha256: Sha256
    pre_run_eligibility_release: PreRunEligibilityReleaseV3
    split_manifest: BenchmarkSplitManifestV2
    leakage_release: LeakageComponentReleaseV3
    expert_registry: ExpertStudyRegistryV2
    active_case_ids: Annotated[
        tuple[Identifier, ...], Field(min_length=30, max_length=30)
    ]
    frozen_at: Annotated[str, Field(min_length=20, max_length=40)]
    eligibility_precedes_split_closure: Literal[True] = True
    complete_case_omission_allowed: Literal[False] = False
    post_hoc_replacement_allowed: Literal[False] = False
    legacy_v1_v2_formal_alias_allowed: Literal[False] = False
    scientific_conclusion: Literal[False] = False

    @field_validator("frozen_at")
    @classmethod
    def validate_frozen_at(cls, value: str) -> str:
        return _require_rfc3339(value)

    @model_validator(mode="after")
    def validate_release(self) -> "FrozenCaseReleaseV3":
        eligibility = _revalidate(
            self.pre_run_eligibility_release, PreRunEligibilityReleaseV3
        )
        manifest = _revalidate(self.split_manifest, BenchmarkSplitManifestV2)
        leakage = _revalidate(self.leakage_release, LeakageComponentReleaseV3)
        expert = _revalidate(self.expert_registry, ExpertStudyRegistryV2)
        assert_pre_run_eligibility_ready_v3(eligibility)
        selected_candidates = _selected_candidates_v3(eligibility)
        selected_by_case = {
            item.case.case_id: item for item in selected_candidates
        }
        _require_sorted_unique(self.active_case_ids, "V3 active case IDs")
        manifest_ids = tuple(item.case_id for item in manifest.cases)
        if self.active_case_ids != manifest_ids:
            raise ValueError("V3 active cases do not exactly cover split V2")
        if set(manifest_ids) != set(selected_by_case):
            raise ValueError("split V2 differs from V3 eligible selections")
        full_cases: list[FlatBandBenchmarkCaseV1] = []
        for reference in manifest.cases:
            candidate = selected_by_case[reference.case_id]
            if not _case_matches_ref_v2(candidate.case, reference):
                raise ValueError("V3 selected case differs from split projection")
            expected_split = BenchmarkSplit(
                eligibility.assignment_release.candidate_pool_release.study_phase
            )
            if reference.split is not expected_split:
                raise ValueError("formal V3 Pilot case differs from pool phase")
            full_cases.append(candidate.case)
        pool = eligibility.assignment_release.candidate_pool_release
        if manifest.source_catalog_sha256 != pool.source_catalog_sha256:
            raise ValueError("V3 split uses a foreign source catalog")
        if leakage.mechanism_lineage_registry != pool.mechanism_lineage_registry:
            raise ValueError("V3 leakage uses a foreign lineage registry")
        assert_leakage_split_closure_v3(
            cases=tuple(full_cases),
            split_manifest=manifest,
            release=leakage,
        )
        assert_expert_registry_covers_split_v2(
            registry=expert,
            split_manifest=manifest,
        )
        active_lineages = {
            item.case_id: item
            for item in pool.candidate_lineage_assignments
            if item.case_id in selected_by_case
        }
        leakage_lineages = {
            item.case_id: item for item in leakage.mechanism_lineage_assignments
        }
        if active_lineages != leakage_lineages:
            raise ValueError(
                "V3 leakage lineage assignments differ from selected pool records"
            )
        active_grouping = {
            (item.axis, item.case_id): (
                item.algorithm_id,
                item.algorithm_sha256,
                item.case_sha256,
                item.structure_sha256,
                item.canonical_group_key,
            )
            for item in pool.grouping_assignments
            if item.case_id in selected_by_case
        }
        leakage_grouping = {
            (item.axis, item.case_id): (
                item.algorithm_id,
                item.algorithm_sha256,
                item.case_sha256,
                item.structure_sha256,
                item.canonical_group_key,
            )
            for item in leakage.grouping_assignments
        }
        if active_grouping != leakage_grouping:
            raise ValueError(
                "V3 leakage structure assignments differ from selected pool records"
            )
        if pool.grouping_algorithms != leakage.grouping_algorithms:
            raise ValueError("V3 leakage changes a pool grouping algorithm")
        identity = eligibility.assignment_release.public_identity_release
        if (
            expert.public_identity_release_id,
            expert.public_identity_release_sha256,
            expert.identity_attestation_id,
            expert.identity_attestation_sha256,
            expert.experts,
        ) != (
            identity.release_id,
            identity.release_sha256,
            identity.identity_attestation_id,
            identity.identity_attestation_sha256,
            identity.experts,
        ):
            raise ValueError("V3 expert registry uses a foreign identity roster")
        eligibility_assignment_by_case = {
            item.case_id: item
            for item in eligibility.assignment_release.assignments
            if item.case_id in selected_by_case
        }
        expert_assignment_by_case = {
            item.case_id: item for item in expert.assignments
        }
        if set(eligibility_assignment_by_case) != set(expert_assignment_by_case):
            raise ValueError(
                "V3 final expert assignments do not cover eligible selected cases"
            )
        for case_id, assignment in eligibility_assignment_by_case.items():
            final_assignment = expert_assignment_by_case[case_id]
            if (
                final_assignment.case_sha256,
                final_assignment.reviewer_ids,
                final_assignment.adjudicator_id,
            ) != (
                assignment.case_sha256,
                assignment.reviewer_ids,
                assignment.adjudicator_id,
            ):
                raise ValueError(
                    "V3 final expert assignment differs from eligibility assignment"
                )
        eligibility_conflicts = {
            (item.case_id, item.expert_id): item
            for item in eligibility.assignment_release.conflict_assessments
            if item.case_id in selected_by_case
        }
        final_conflicts = {
            (item.case_id, item.expert_id): item
            for item in expert.conflict_assessments
        }
        if eligibility_conflicts != final_conflicts:
            raise ValueError(
                "V3 final expert registry rewrites selected-case conflict records"
            )
        calibration = eligibility.assignment_release.calibration_manifest
        if (
            expert.annotation_guide_version,
            expert.annotation_guide_sha256,
            expert.calibration_manifest_id,
            expert.calibration_manifest_sha256,
            expert.calibration_completions,
        ) != (
            calibration.annotation_guide_version,
            calibration.annotation_guide_sha256,
            calibration.manifest_id,
            calibration.manifest_sha256,
            eligibility.assignment_release.calibration_completions,
        ):
            raise ValueError("V3 expert registry changes calibration closure")
        eligibility_sealed = _timestamp(eligibility.sealed_at)
        leakage_created = _timestamp(leakage.created_at)
        expert_registered = _timestamp(expert.registered_at)
        frozen_at = _timestamp(self.frozen_at)
        if eligibility_sealed >= min(leakage_created, expert_registered):
            raise ValueError(
                "V3 eligibility was not sealed before leakage/expert closure"
            )
        if max(leakage_created, expert_registered) >= frozen_at:
            raise ValueError("V3 leakage/expert closure was not before frozen release")
        _assert_identity(
            self,
            id_field="release_id",
            sha_field="release_sha256",
            prefix="frozen-case-release-v3",
        )
        return self


def build_frozen_case_release_v3(
    *,
    pre_run_eligibility_release: PreRunEligibilityReleaseV3,
    split_manifest: BenchmarkSplitManifestV2,
    leakage_release: LeakageComponentReleaseV3,
    expert_registry: ExpertStudyRegistryV2,
    frozen_at: str,
) -> FrozenCaseReleaseV3:
    eligibility = _revalidate(
        pre_run_eligibility_release, PreRunEligibilityReleaseV3
    )
    manifest = _revalidate(split_manifest, BenchmarkSplitManifestV2)
    leakage = _revalidate(leakage_release, LeakageComponentReleaseV3)
    expert = _revalidate(expert_registry, ExpertStudyRegistryV2)
    return _build_identified(
        FrozenCaseReleaseV3,
        id_field="release_id",
        sha_field="release_sha256",
        prefix="frozen-case-release-v3",
        values={
            "pre_run_eligibility_release": eligibility,
            "split_manifest": manifest,
            "leakage_release": leakage,
            "expert_registry": expert,
            "active_case_ids": tuple(item.case_id for item in manifest.cases),
            "frozen_at": frozen_at,
        },
    )


def assert_frozen_case_ready_v3(release: FrozenCaseReleaseV3) -> None:
    _revalidate(release, FrozenCaseReleaseV3)


def build_candidate_pool_leakage_context_v3(
    candidate_pool_release: CandidatePoolReleaseV3,
) -> LeakageUnsplitCaseUniverseContextV3:
    """Project the exact full candidate/replacement universe for union replay."""

    pool = _revalidate(candidate_pool_release, CandidatePoolReleaseV3)
    return LeakageUnsplitCaseUniverseContextV3(
        universe_id=deterministic_id(
            "candidate-pool-universe-v3",
            {
                "source_artifact_id": pool.release_id,
                "source_artifact_sha256": pool.release_sha256,
            },
        ),
        source_artifact_id=pool.release_id,
        source_artifact_sha256=pool.release_sha256,
        cases=tuple(
            sorted((item.case for item in pool.candidates), key=lambda item: item.case_id)
        ),
        grouping_algorithms=pool.grouping_algorithms,
        grouping_runs=pool.grouping_runs,
        grouping_assignments=pool.grouping_assignments,
        mechanism_lineage_registry=pool.mechanism_lineage_registry,
        mechanism_lineage_assignments=pool.candidate_lineage_assignments,
    )


def build_calibration_leakage_context_v3(
    calibration_manifest: CalibrationSetManifestV2,
) -> LeakageUnsplitCaseUniverseContextV3:
    """Project the exact authoritative calibration universe for union replay."""

    calibration = _revalidate(calibration_manifest, CalibrationSetManifestV2)
    return LeakageUnsplitCaseUniverseContextV3(
        universe_id=deterministic_id(
            "calibration-universe-v3",
            {
                "source_artifact_id": calibration.manifest_id,
                "source_artifact_sha256": calibration.manifest_sha256,
            },
        ),
        source_artifact_id=calibration.manifest_id,
        source_artifact_sha256=calibration.manifest_sha256,
        cases=calibration.cases,
        grouping_algorithms=calibration.grouping_algorithms,
        grouping_runs=calibration.grouping_runs,
        grouping_assignments=calibration.grouping_assignments,
        mechanism_lineage_registry=calibration.mechanism_lineage_registry,
        mechanism_lineage_assignments=(
            calibration.mechanism_lineage_assignments
        ),
    )


def _structure_union_members_v3(
    *,
    pool: CandidatePoolReleaseV3,
    calibration: CalibrationSetManifestV2,
    prior_r1_pool: CandidatePoolReleaseV3 | None,
) -> tuple[tuple[str, StructureGroupingPrivateEvidenceReleaseV2], ...]:
    if pool.study_phase == "PILOT_R1":
        if prior_r1_pool is not None:
            raise ValueError("R1 structure union cannot carry a prior pool")
        return (
            (
                STRUCTURE_UNION_OWNER_CALIBRATION_V3,
                calibration.structure_grouping_release,
            ),
            (
                STRUCTURE_UNION_OWNER_CURRENT_R1_FULL_POOL_V3,
                pool.structure_grouping_release,
            ),
        )
    if prior_r1_pool is None or prior_r1_pool.study_phase != "PILOT_R1":
        raise ValueError("R2 structure union requires the exact prior R1 pool")
    return (
        (
            STRUCTURE_UNION_OWNER_CALIBRATION_V3,
            calibration.structure_grouping_release,
        ),
        (
            STRUCTURE_UNION_OWNER_PRIOR_R1_FULL_POOL_V3,
            prior_r1_pool.structure_grouping_release,
        ),
        (
            STRUCTURE_UNION_OWNER_CURRENT_R2_FULL_POOL_V3,
            pool.structure_grouping_release,
        ),
    )


class PilotPreBudgetClosureReleaseV3(StrictModel):
    """Leakage-union authorization sealed before any Pilot execution budget.

    R1 explicitly has no prior round.  R2 embeds the exact R1 leakage context
    and recomputes the typed union graph before it can authorize a budget.
    """

    schema_version: Literal["flatband-pilot-pre-budget-closure-v3"] = (
        "flatband-pilot-pre-budget-closure-v3"
    )
    release_id: Identifier
    release_sha256: Sha256
    study_phase: Literal["PILOT_R1", "PILOT_R2"]
    frozen_case_release: FrozenCaseReleaseV3
    current_leakage_context: LeakageRoundClosureContextV3
    current_candidate_pool_context: LeakageUnsplitCaseUniverseContextV3
    calibration_context: LeakageUnsplitCaseUniverseContextV3
    structure_union_replay_release: StructureGroupingUnionReplayReleaseV2
    structure_union_release_id: Identifier
    structure_union_release_sha256: Sha256
    structure_union_member_set_sha256: Sha256
    structure_union_owner_projection_sha256: Sha256
    structure_union_merged_input_root_sha256: Sha256
    structure_union_merged_output_root_sha256: Sha256
    lineage_curation_release: MechanismLineageCurationReleaseV3
    lineage_assignment_curation_release_id: Identifier
    lineage_assignment_curation_release_sha256: Sha256
    prior_r1_leakage_context: LeakageRoundClosureContextV3 | None = None
    prior_r1_candidate_pool_release: CandidatePoolReleaseV3 | None = None
    prior_r1_candidate_pool_context: (
        LeakageUnsplitCaseUniverseContextV3 | None
    ) = None
    candidate_and_calibration_union_verified: Literal[True] = True
    cross_round_union_verified: bool
    sealed_at: Annotated[str, Field(min_length=20, max_length=40)]
    sealed_before_every_budget: Literal[True] = True
    private_custody_required: Literal[True] = True
    public_release_allowed: Literal[False] = False
    external_timestamp_attestation_present: Literal[False] = False
    scientific_conclusion: Literal[False] = False

    @field_validator("sealed_at")
    @classmethod
    def validate_sealed_at(cls, value: str) -> str:
        return _require_rfc3339(value)

    @model_validator(mode="after")
    def validate_release(self) -> "PilotPreBudgetClosureReleaseV3":
        frozen = _revalidate(self.frozen_case_release, FrozenCaseReleaseV3)
        context = _revalidate(
            self.current_leakage_context, LeakageRoundClosureContextV3
        )
        pool_context = _revalidate(
            self.current_candidate_pool_context,
            LeakageUnsplitCaseUniverseContextV3,
        )
        calibration_context = _revalidate(
            self.calibration_context,
            LeakageUnsplitCaseUniverseContextV3,
        )
        curation = _revalidate(
            self.lineage_curation_release,
            MechanismLineageCurationReleaseV3,
        )
        pool = (
            frozen.pre_run_eligibility_release.assignment_release
            .candidate_pool_release
        )
        calibration = (
            frozen.pre_run_eligibility_release.assignment_release
            .calibration_manifest
        )
        prior_pool_value = (
            None
            if self.prior_r1_candidate_pool_release is None
            else _revalidate(
                self.prior_r1_candidate_pool_release,
                CandidatePoolReleaseV3,
            )
        )
        union = _revalidate(
            self.structure_union_replay_release,
            StructureGroupingUnionReplayReleaseV2,
        )
        union_members = _structure_union_members_v3(
            pool=pool,
            calibration=calibration,
            prior_r1_pool=prior_pool_value,
        )
        assert_structure_grouping_union_replay_release_exact_v2(
            release=union,
            members=union_members,
        )
        if (
            self.structure_union_release_id,
            self.structure_union_release_sha256,
            self.structure_union_member_set_sha256,
            self.structure_union_owner_projection_sha256,
            self.structure_union_merged_input_root_sha256,
            self.structure_union_merged_output_root_sha256,
        ) != (
            union.union_release_id,
            union.union_release_sha256,
            union.member_release_set_sha256,
            union.candidate_owner_projection_sha256,
            union.merged_input_root_sha256,
            union.merged_output_root_sha256,
        ):
            raise ValueError("pre-budget closure crosswires structure union roots")
        if len(union.candidate_owner_projection) > 84:
            raise ValueError("formal Pilot raw structure union exceeds 84 candidates")
        if self.study_phase != pool.study_phase:
            raise ValueError("pre-budget closure phase differs from candidate pool")
        if curation != pool.mechanism_lineage_curation_release:
            raise ValueError("pre-budget closure uses foreign lineage curation")
        assignment_curation = (
            pool.mechanism_lineage_assignment_curation_release
        )
        if (
            self.lineage_assignment_curation_release_id,
            self.lineage_assignment_curation_release_sha256,
        ) != (
            assignment_curation.release_id,
            assignment_curation.release_sha256,
        ):
            raise ValueError(
                "pre-budget closure uses foreign assignment curation"
            )
        if pool_context != build_candidate_pool_leakage_context_v3(pool):
            raise ValueError(
                "pre-budget closure candidate context differs from exact pool"
            )
        if calibration_context != build_calibration_leakage_context_v3(
            calibration
        ):
            raise ValueError(
                "pre-budget closure calibration context differs from manifest"
            )
        selected_cases = tuple(
            sorted(
                (item.case for item in _selected_candidates_v3(
                    frozen.pre_run_eligibility_release
                )),
                key=lambda item: item.case_id,
            )
        )
        if (
            context.cases,
            context.split_manifest,
            context.release,
        ) != (
            selected_cases,
            frozen.split_manifest,
            frozen.leakage_release,
        ):
            raise ValueError(
                "pre-budget closure current leakage context differs from FrozenCaseV3"
            )
        assert_pilot_leakage_v3(
            cases=context.cases,
            split_manifest=context.split_manifest,
            release=context.release,
            lineage_curation_release=curation,
        )
        sealed_at = _timestamp(self.sealed_at)
        if sealed_at <= _timestamp(frozen.frozen_at):
            raise ValueError("pre-budget closure was not sealed after FrozenCaseV3")
        owner_seals = [
            _timestamp(pool.sealed_at),
            _timestamp(calibration.frozen_at),
            _timestamp(frozen.frozen_at),
        ]
        if prior_pool_value is not None:
            owner_seals.append(_timestamp(prior_pool_value.sealed_at))
        if _timestamp(union.union_input_sealed_at) <= max(owner_seals):
            raise ValueError(
                "structure union input was not sealed after every owner context"
            )
        if _timestamp(union.verified_at) >= sealed_at:
            raise ValueError("structure union was not verified before pre-budget seal")
        if self.study_phase == "PILOT_R1":
            if any(
                item is not None
                for item in (
                    self.prior_r1_leakage_context,
                    self.prior_r1_candidate_pool_release,
                    self.prior_r1_candidate_pool_context,
                )
            ):
                raise ValueError("R1 pre-budget closure cannot carry a prior round")
            if self.cross_round_union_verified:
                raise ValueError("R1 cannot claim a cross-round union")
            assert_cross_round_leakage_disjoint_v3(
                round_contexts=(),
                lineage_curation_release=curation,
                additional_case_universes=(
                    pool_context,
                    calibration_context,
                ),
            )
        else:
            if any(
                item is None
                for item in (
                    self.prior_r1_leakage_context,
                    self.prior_r1_candidate_pool_release,
                    self.prior_r1_candidate_pool_context,
                )
            ):
                raise ValueError(
                    "R2 pre-budget closure requires exact prior R1 leakage and pool"
                )
            prior = _revalidate(
                self.prior_r1_leakage_context,
                LeakageRoundClosureContextV3,
            )
            prior_pool = _revalidate(
                self.prior_r1_candidate_pool_release,
                CandidatePoolReleaseV3,
            )
            prior_pool_context = _revalidate(
                self.prior_r1_candidate_pool_context,
                LeakageUnsplitCaseUniverseContextV3,
            )
            if prior_pool.study_phase != "PILOT_R1":
                raise ValueError("R2 prior candidate pool is not Pilot R1")
            if (
                prior_pool.mechanism_lineage_curation_release != curation
                or prior_pool_context
                != build_candidate_pool_leakage_context_v3(prior_pool)
            ):
                raise ValueError(
                    "R2 prior candidate context does not replay its exact pool"
                )
            prior_pool_cases = {
                (item.case_id, item.case_sha256)
                for item in prior_pool_context.cases
            }
            if not {
                (item.case_id, item.case_sha256) for item in prior.cases
            } <= prior_pool_cases:
                raise ValueError(
                    "R2 prior selected leakage is outside its candidate pool"
                )
            if any(
                item.split is not BenchmarkSplit.PILOT_R1
                for item in prior.split_manifest.cases
            ):
                raise ValueError("R2 prior leakage context is not Pilot R1")
            if _timestamp(prior.release.created_at) >= _timestamp(
                context.release.created_at
            ):
                raise ValueError("R2 leakage release does not follow prior R1")
            assert_cross_round_leakage_disjoint_v3(
                round_contexts=(prior, context),
                lineage_curation_release=curation,
            )
            assert_cross_round_leakage_disjoint_v3(
                round_contexts=(),
                lineage_curation_release=curation,
                additional_case_universes=(
                    prior_pool_context,
                    pool_context,
                    calibration_context,
                ),
            )
            if not self.cross_round_union_verified:
                raise ValueError("R2 must record the replayed cross-round union")
        _assert_identity(
            self,
            id_field="release_id",
            sha_field="release_sha256",
            prefix="pilot-pre-budget-closure-v3",
        )
        return self


def build_pilot_pre_budget_closure_release_v3(
    *,
    frozen_case_release: FrozenCaseReleaseV3,
    current_leakage_context: LeakageRoundClosureContextV3,
    current_candidate_pool_context: LeakageUnsplitCaseUniverseContextV3,
    calibration_context: LeakageUnsplitCaseUniverseContextV3,
    structure_union_replay_release: StructureGroupingUnionReplayReleaseV2,
    sealed_at: str,
    prior_r1_leakage_context: LeakageRoundClosureContextV3 | None = None,
    prior_r1_candidate_pool_release: CandidatePoolReleaseV3 | None = None,
    prior_r1_candidate_pool_context: (
        LeakageUnsplitCaseUniverseContextV3 | None
    ) = None,
) -> PilotPreBudgetClosureReleaseV3:
    frozen = _revalidate(frozen_case_release, FrozenCaseReleaseV3)
    context = _revalidate(
        current_leakage_context, LeakageRoundClosureContextV3
    )
    prior = (
        None
        if prior_r1_leakage_context is None
        else _revalidate(
            prior_r1_leakage_context, LeakageRoundClosureContextV3
        )
    )
    pool_context = _revalidate(
        current_candidate_pool_context,
        LeakageUnsplitCaseUniverseContextV3,
    )
    calibration_value = _revalidate(
        calibration_context,
        LeakageUnsplitCaseUniverseContextV3,
    )
    prior_pool = (
        None
        if prior_r1_candidate_pool_release is None
        else _revalidate(
            prior_r1_candidate_pool_release, CandidatePoolReleaseV3
        )
    )
    prior_pool_context = (
        None
        if prior_r1_candidate_pool_context is None
        else _revalidate(
            prior_r1_candidate_pool_context,
            LeakageUnsplitCaseUniverseContextV3,
        )
    )
    pool = (
        frozen.pre_run_eligibility_release.assignment_release
        .candidate_pool_release
    )
    calibration = (
        frozen.pre_run_eligibility_release.assignment_release
        .calibration_manifest
    )
    union = _revalidate(
        structure_union_replay_release,
        StructureGroupingUnionReplayReleaseV2,
    )
    members = _structure_union_members_v3(
        pool=pool,
        calibration=calibration,
        prior_r1_pool=prior_pool,
    )
    assert_structure_grouping_union_replay_release_exact_v2(
        release=union,
        members=members,
    )
    return _build_identified(
        PilotPreBudgetClosureReleaseV3,
        id_field="release_id",
        sha_field="release_sha256",
        prefix="pilot-pre-budget-closure-v3",
        values={
            "study_phase": pool.study_phase,
            "frozen_case_release": frozen,
            "current_leakage_context": context,
            "current_candidate_pool_context": pool_context,
            "calibration_context": calibration_value,
            "structure_union_replay_release": union,
            "structure_union_release_id": union.union_release_id,
            "structure_union_release_sha256": union.union_release_sha256,
            "structure_union_member_set_sha256": (
                union.member_release_set_sha256
            ),
            "structure_union_owner_projection_sha256": (
                union.candidate_owner_projection_sha256
            ),
            "structure_union_merged_input_root_sha256": (
                union.merged_input_root_sha256
            ),
            "structure_union_merged_output_root_sha256": (
                union.merged_output_root_sha256
            ),
            "lineage_curation_release": (
                pool.mechanism_lineage_curation_release
            ),
            "lineage_assignment_curation_release_id": (
                pool.mechanism_lineage_assignment_curation_release.release_id
            ),
            "lineage_assignment_curation_release_sha256": (
                pool.mechanism_lineage_assignment_curation_release.release_sha256
            ),
            "prior_r1_leakage_context": prior,
            "prior_r1_candidate_pool_release": prior_pool,
            "prior_r1_candidate_pool_context": prior_pool_context,
            "candidate_and_calibration_union_verified": True,
            "cross_round_union_verified": prior is not None,
            "sealed_at": sealed_at,
        },
    )


def assert_pre_run_eligibility_precedes_execution_v3(
    *,
    frozen_case_release: FrozenCaseReleaseV3,
    eligibility_release: PreRunEligibilityReleaseV3,
    execution_release: object,
) -> None:
    """Accept only the exact formal V3 execution chain (never V1/V2)."""

    from material_agent.research.flatband_execution import ExecutionReleaseV3

    frozen = _revalidate(frozen_case_release, FrozenCaseReleaseV3)
    eligibility = _revalidate(eligibility_release, PreRunEligibilityReleaseV3)
    execution = ExecutionReleaseV3.model_validate(
        ExecutionReleaseV3.model_validate(execution_release).model_dump(
            mode="python", round_trip=True
        )
    )
    if frozen.pre_run_eligibility_release != eligibility:
        raise ValueError("FrozenCaseReleaseV3 embeds a different eligibility release")
    if execution.frozen_case_release != frozen or (
        execution.pre_run_eligibility_release != eligibility
    ):
        raise ValueError("ExecutionReleaseV3 embeds a foreign formal case chain")
    if execution.pre_budget_closure_release.frozen_case_release != frozen:
        raise ValueError("ExecutionReleaseV3 embeds a foreign pre-budget closure")
    if any(
        _timestamp(item.frozen_at)
        <= max(
            _timestamp(frozen.frozen_at),
            _timestamp(eligibility.sealed_at),
            _timestamp(execution.pre_budget_closure_release.sealed_at),
        )
        for item in execution.budget_manifests
    ):
        raise ValueError("V3 budget does not strictly follow all formal seals")


__all__ = [
    "ActiveCaseSelectionV1",
    "CandidateEligibilityAssignmentReleaseV3",
    "CandidateEligibilityAssignmentV3",
    "CandidatePoolReleaseV3",
    "CaseSourcePolicyAttestationV2",
    "CaseEligibilityReasonCode",
    "CaseEligibilityStatus",
    "DerivativeEvidenceRefV3",
    "DerivativeEligibilityClassV3",
    "EligibilityDecisionV1",
    "EligibilityDecisionV3",
    "EligibilityAdjudicationV3",
    "EligibilityAuditRefV3",
    "EligibilityRawAuditV3",
    "FrozenCandidateRole",
    "FrozenCaseCandidateV1",
    "FrozenCaseReleaseV1",
    "FrozenCaseReleaseV2",
    "FrozenCaseReleaseV3",
    "PilotPreBudgetClosureReleaseV3",
    "PreRunEligibilityReleaseV1",
    "PreRunEligibilityReleaseV2",
    "PreRunEligibilityReleaseV3",
    "SourceCatalogDecision",
    "SourceUseRole",
    "STRUCTURE_UNION_OWNER_CALIBRATION_V3",
    "STRUCTURE_UNION_OWNER_CURRENT_R1_FULL_POOL_V3",
    "STRUCTURE_UNION_OWNER_CURRENT_R2_FULL_POOL_V3",
    "STRUCTURE_UNION_OWNER_PRIOR_R1_FULL_POOL_V3",
    "_RECORD_LEVEL_COMPATIBLE_LICENSES",
    "_SOURCE_CATALOG_POLICY_V1",
    "assert_case_source_policy_v2",
    "assert_pre_run_eligibility_precedes_execution",
    "assert_pre_run_eligibility_precedes_execution_v2",
    "assert_pre_run_eligibility_precedes_execution_v3",
    "assert_pre_run_eligibility_ready",
    "assert_pre_run_eligibility_ready_v2",
    "assert_pre_run_eligibility_ready_v3",
    "assert_frozen_case_ready_v3",
    "assert_pre_run_eligibility_sealed_before",
    "build_case_source_policy_attestation_v2",
    "build_calibration_leakage_context_v3",
    "build_candidate_eligibility_assignment_release_v3",
    "build_candidate_eligibility_assignment_v3",
    "build_candidate_pool_leakage_context_v3",
    "build_candidate_pool_release_v3",
    "build_eligibility_decision",
    "build_eligibility_adjudication_v3",
    "build_eligibility_raw_audit_v3",
    "build_frozen_case_candidate",
    "build_frozen_case_candidate_v3",
    "build_frozen_case_release",
    "build_frozen_case_release_v2",
    "build_frozen_case_release_v3",
    "build_pilot_pre_budget_closure_release_v3",
    "build_pre_run_eligibility_release",
    "build_pre_run_eligibility_release_v2",
    "build_pre_run_eligibility_release_v3",
    "frozen_case_slot_id",
]
