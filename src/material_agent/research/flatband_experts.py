"""Content-addressed expert calibration, conflict, and assignment closure."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Annotated, Literal, TypeVar

from pydantic import Field, field_validator, model_validator

from material_agent.inspiration.models import (
    Identifier,
    LongText,
    Sha256,
    ShortText,
    StrictModel,
    canonical_sha256,
    deterministic_id,
)
from material_agent.research.flatband_contracts import (
    BenchmarkSplitManifestV1,
    BenchmarkSplitManifestV2,
    ExpertRole,
    FlatBandBenchmarkCaseV1,
    SourceRecordRefV1,
    SplitManifestKind,
    _require_rfc3339,
)
from material_agent.research.flatband_derivative_screening import (
    DerivativeScreeningReleaseV3,
    assert_calibration_derivative_screening_all_not_derivative_v3,
    assert_derivative_screening_release_exact_replay_v3,
)
from material_agent.research.flatband_leakage import (
    LeakageComponentV1,
    LeakageComponentReleaseV2,
    LeakageComponentReleaseV3,
    LeakageGroupDefinitionV3,
    LeakageMembershipV3,
    MechanismLineageAssignmentV3,
    MechanismLineageRegistryV3,
    StructureGroupingAlgorithmV2,
    StructureGroupingAssignmentV2,
    StructureGroupingRunV2,
    _components_from_memberships_v3,
    _derive_memberships_v3,
    _validate_lineage_artifacts_v3,
    _validate_structure_grouping_v2,
    assert_leakage_split_closure_v3,
    structure_grouping_case_universe_sha256_v2,
)
from material_agent.research.flatband_source_policy import (
    CaseSourcePolicyAttestationV2,
)
from material_agent.research.flatband_structure_grouping import (
    StructureGroupingPrivateEvidenceReleaseV2,
    assert_structure_grouping_release_exact_replay_v2,
)


ModelT = TypeVar("ModelT", bound=StrictModel)


class ConflictStatus(StrEnum):
    CLEAR = "CLEAR"
    RECUSE = "RECUSE"


class ConflictReasonCode(StrEnum):
    NO_CONFLICT = "NO_CONFLICT"
    AUTHOR_OR_RECENT_COLLABORATOR = "AUTHOR_OR_RECENT_COLLABORATOR"
    SAME_RESEARCH_GROUP_OR_INSTITUTION = "SAME_RESEARCH_GROUP_OR_INSTITUTION"
    DIRECTLY_COMPETING_UNPUBLISHED_WORK = "DIRECTLY_COMPETING_UNPUBLISHED_WORK"
    FINANCIAL_OR_IP_INTEREST = "FINANCIAL_OR_IP_INTEREST"
    OTHER_DECLARED_CONFLICT = "OTHER_DECLARED_CONFLICT"


class ExpertProfileV1(StrictModel):
    expert_id: Identifier
    role: ExpertRole
    domain_expertise: Annotated[
        tuple[ShortText, ...], Field(min_length=1, max_length=16)
    ]
    qualification_summary: LongText
    pseudonymous: Literal[True] = True

    @model_validator(mode="after")
    def validate_profile(self) -> "ExpertProfileV1":
        if self.domain_expertise != tuple(sorted(set(self.domain_expertise))):
            raise ValueError("expertise entries must be sorted and unique")
        return self


class CalibrationCompletionV1(StrictModel):
    schema_version: Literal["flatband-calibration-completion-v1"] = (
        "flatband-calibration-completion-v1"
    )
    completion_id: Identifier
    completion_sha256: Sha256
    expert_id: Identifier
    role: ExpertRole
    annotation_guide_sha256: Sha256
    calibration_set_sha256: Sha256
    raw_answers_sha256: Sha256
    calibration_result_sha256: Sha256
    completed_at: Annotated[str, Field(min_length=20, max_length=40)]
    completed_before_group_discussion: Literal[True] = True
    sealed: Literal[True] = True
    scientific_conclusion: Literal[False] = False

    @field_validator("completed_at")
    @classmethod
    def validate_completed_at(cls, value: str) -> str:
        return _require_rfc3339(value)

    @model_validator(mode="after")
    def validate_completion(self) -> "CalibrationCompletionV1":
        semantic = self.model_dump(
            mode="python", exclude={"completion_id", "completion_sha256"}
        )
        digest = canonical_sha256(semantic)
        if self.completion_sha256 != digest:
            raise ValueError("calibration completion SHA-256 does not match content")
        if self.completion_id != deterministic_id(
            "calibration-completion", {"completion_sha256": digest}
        ):
            raise ValueError("calibration completion ID does not match its SHA-256")
        return self


class CaseConflictAssessmentV1(StrictModel):
    case_id: Identifier
    case_sha256: Sha256
    expert_id: Identifier
    status: ConflictStatus
    reason_code: ConflictReasonCode
    disclosure_sha256: Sha256
    assessed_at: Annotated[str, Field(min_length=20, max_length=40)]
    sealed_before_system_outputs: Literal[True] = True

    @field_validator("assessed_at")
    @classmethod
    def validate_assessed_at(cls, value: str) -> str:
        return _require_rfc3339(value)

    @model_validator(mode="after")
    def validate_conflict(self) -> "CaseConflictAssessmentV1":
        if self.status is ConflictStatus.CLEAR:
            if self.reason_code is not ConflictReasonCode.NO_CONFLICT:
                raise ValueError("clear assessment requires NO_CONFLICT")
        elif self.reason_code is ConflictReasonCode.NO_CONFLICT:
            raise ValueError("recusal requires a concrete conflict reason")
        return self


class CaseExpertAssignmentV1(StrictModel):
    case_id: Identifier
    case_sha256: Sha256
    reviewer_ids: Annotated[
        tuple[Identifier, ...], Field(min_length=2, max_length=2)
    ]
    adjudicator_id: Identifier

    @model_validator(mode="after")
    def validate_assignment(self) -> "CaseExpertAssignmentV1":
        if self.reviewer_ids != tuple(sorted(set(self.reviewer_ids))):
            raise ValueError("reviewer IDs must be sorted and distinct")
        if self.adjudicator_id in self.reviewer_ids:
            raise ValueError("adjudicator must differ from assigned reviewers")
        return self


class ExpertStudyRegistryV1(StrictModel):
    schema_version: Literal["flatband-expert-study-registry-v1"] = (
        "flatband-expert-study-registry-v1"
    )
    registry_id: Identifier
    registry_sha256: Sha256
    split_manifest_id: Identifier
    split_manifest_sha256: Sha256
    annotation_guide_sha256: Sha256
    calibration_set_sha256: Sha256
    profiles: Annotated[
        tuple[ExpertProfileV1, ...], Field(min_length=3, max_length=12)
    ]
    calibration_completions: Annotated[
        tuple[CalibrationCompletionV1, ...], Field(min_length=3, max_length=12)
    ]
    conflict_assessments: Annotated[
        tuple[CaseConflictAssessmentV1, ...], Field(min_length=90, max_length=1_440)
    ]
    assignments: Annotated[
        tuple[CaseExpertAssignmentV1, ...], Field(min_length=30, max_length=120)
    ]
    registered_at: Annotated[str, Field(min_length=20, max_length=40)]
    scientific_conclusion: Literal[False] = False

    @field_validator("registered_at")
    @classmethod
    def validate_registered_at(cls, value: str) -> str:
        return _require_rfc3339(value)

    @model_validator(mode="after")
    def validate_registry(self) -> "ExpertStudyRegistryV1":
        registered_at = datetime.fromisoformat(
            self.registered_at.replace("Z", "+00:00")
        )
        profile_ids = tuple(item.expert_id for item in self.profiles)
        if profile_ids != tuple(sorted(set(profile_ids))):
            raise ValueError("expert profiles must be expert-ID sorted and unique")
        profiles = {item.expert_id: item for item in self.profiles}
        if sum(item.role is ExpertRole.REVIEWER for item in self.profiles) < 2:
            raise ValueError("expert pool requires at least two reviewers")
        if sum(item.role is ExpertRole.ADJUDICATOR for item in self.profiles) < 1:
            raise ValueError("expert pool requires at least one adjudicator")

        completion_ids = tuple(
            item.expert_id for item in self.calibration_completions
        )
        if completion_ids != tuple(sorted(set(completion_ids))):
            raise ValueError("calibration completions must be expert-ID sorted and unique")
        if set(completion_ids) != set(profile_ids):
            raise ValueError("every expert requires exactly one calibration completion")
        for completion in self.calibration_completions:
            profile = profiles[completion.expert_id]
            if completion.role is not profile.role:
                raise ValueError("calibration role differs from expert profile")
            if (
                completion.annotation_guide_sha256 != self.annotation_guide_sha256
                or completion.calibration_set_sha256 != self.calibration_set_sha256
            ):
                raise ValueError("calibration completion binds a different guide or set")
            if datetime.fromisoformat(
                completion.completed_at.replace("Z", "+00:00")
            ) > registered_at:
                raise ValueError("expert calibration completion follows registry seal")

        conflict_keys = tuple(
            (item.case_id, item.expert_id) for item in self.conflict_assessments
        )
        if conflict_keys != tuple(sorted(set(conflict_keys))):
            raise ValueError("conflict assessments must be case/expert sorted and unique")
        assignment_cases = tuple(item.case_id for item in self.assignments)
        if assignment_cases != tuple(sorted(set(assignment_cases))):
            raise ValueError("expert assignments must be case-ID sorted and unique")
        assessment_map = {
            (item.case_id, item.expert_id): item
            for item in self.conflict_assessments
        }
        expected_conflicts = {
            (assignment.case_id, expert_id)
            for assignment in self.assignments
            for expert_id in profile_ids
        }
        if set(assessment_map) != expected_conflicts:
            raise ValueError("conflict map must exactly cover every case x expert")
        if any(
            datetime.fromisoformat(item.assessed_at.replace("Z", "+00:00"))
            > registered_at
            for item in self.conflict_assessments
        ):
            raise ValueError("conflict assessment follows registry seal")

        for assignment in self.assignments:
            for expert_id in (*assignment.reviewer_ids, assignment.adjudicator_id):
                profile = profiles.get(expert_id)
                if profile is None:
                    raise ValueError("expert assignment references an unknown expert")
                expected_role = (
                    ExpertRole.ADJUDICATOR
                    if expert_id == assignment.adjudicator_id
                    else ExpertRole.REVIEWER
                )
                if profile.role is not expected_role:
                    raise ValueError("expert assignment uses the wrong role")
                if assessment_map[(assignment.case_id, expert_id)].status is not ConflictStatus.CLEAR:
                    raise ValueError("a recused expert cannot be assigned to a case")

        semantic = self.model_dump(
            mode="python", exclude={"registry_id", "registry_sha256"}
        )
        digest = canonical_sha256(semantic)
        if self.registry_sha256 != digest:
            raise ValueError("expert registry SHA-256 does not match content")
        if self.registry_id != deterministic_id(
            "expert-study-registry", {"registry_sha256": digest}
        ):
            raise ValueError("expert registry ID does not match its SHA-256")
        return self


def assert_expert_registry_covers_split(
    *,
    registry: ExpertStudyRegistryV1,
    split_manifest: BenchmarkSplitManifestV1,
) -> None:
    """Bind expert assignments and conflict checks to every frozen case."""

    registry = ExpertStudyRegistryV1.model_validate(
        registry.model_dump(mode="python", round_trip=True)
    )
    split_manifest = BenchmarkSplitManifestV1.model_validate(
        split_manifest.model_dump(mode="python", round_trip=True)
    )
    if (
        registry.split_manifest_id,
        registry.split_manifest_sha256,
    ) != (split_manifest.manifest_id, split_manifest.manifest_sha256):
        raise ValueError("expert registry references a different split manifest")
    expected = {item.case_id: item.case_sha256 for item in split_manifest.cases}
    observed = {item.case_id: item.case_sha256 for item in registry.assignments}
    if observed != expected:
        raise ValueError("expert registry does not exactly cover split cases")
    for assessment in registry.conflict_assessments:
        if assessment.case_sha256 != expected.get(assessment.case_id):
            raise ValueError("conflict assessment case identity differs from split")


# V1 remains readable for the content-addressed draft and for consumers that
# have not yet migrated.  A formal Pilot must use the V2 closure below.  V2
# deliberately distinguishes an internally replayable custodian attestation
# from an externally trusted identity signature, and it accepts a typed,
# replayable calibration manifest rather than a caller-supplied set digest.


def _timestamp_v2(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _revalidate_v2(value: ModelT, model_type: type[ModelT]) -> ModelT:
    return model_type.model_validate(
        value.model_dump(mode="python", round_trip=True)
    )


def _identity_values_v2(
    value: StrictModel, *, id_field: str, sha_field: str, prefix: str
) -> tuple[str, str]:
    semantic = value.model_dump(mode="python", exclude={id_field, sha_field})
    digest = canonical_sha256(semantic)
    return deterministic_id(prefix, {sha_field: digest}), digest


def _assert_identity_v2(
    value: StrictModel, *, id_field: str, sha_field: str, prefix: str
) -> None:
    expected_id, expected_sha = _identity_values_v2(
        value, id_field=id_field, sha_field=sha_field, prefix=prefix
    )
    if getattr(value, sha_field) != expected_sha:
        raise ValueError(f"{sha_field} does not match semantic content")
    if getattr(value, id_field) != expected_id:
        raise ValueError(f"{id_field} does not match {sha_field}")


def _build_identified_v2(
    model_type: type[ModelT],
    *,
    id_field: str,
    sha_field: str,
    prefix: str,
    values: dict[str, object],
) -> ModelT:
    draft = model_type.model_construct(**values)
    identifier, digest = _identity_values_v2(
        draft, id_field=id_field, sha_field=sha_field, prefix=prefix
    )
    return model_type.model_validate(
        {**values, id_field: identifier, sha_field: digest}
    )


def _require_sorted_unique_v2(values: tuple[str, ...], label: str) -> None:
    if values != tuple(sorted(set(values))):
        raise ValueError(f"{label} must be sorted and unique")


class CalibrationSourceRecordV2(StrictModel):
    """Exact source-record projection from one frozen calibration case."""

    case_id: Identifier
    case_sha256: Sha256
    source_record: SourceRecordRefV1
    source_record_sha256: Sha256

    @model_validator(mode="after")
    def validate_record(self) -> "CalibrationSourceRecordV2":
        record = _revalidate_v2(self.source_record, SourceRecordRefV1)
        expected = canonical_sha256(
            {
                "schema_version": "flatband-calibration-source-record-v2",
                "case_id": self.case_id,
                "case_sha256": self.case_sha256,
                "source_record": record.model_dump(
                    mode="python", round_trip=True
                ),
            }
        )
        if self.source_record_sha256 != expected:
            raise ValueError("calibration source-record SHA-256 does not replay")
        return self


def _calibration_source_records_v2(
    cases: tuple[FlatBandBenchmarkCaseV1, ...],
) -> tuple[CalibrationSourceRecordV2, ...]:
    values = tuple(
        CalibrationSourceRecordV2(
            case_id=case.case_id,
            case_sha256=case.case_sha256,
            source_record=record,
            source_record_sha256=canonical_sha256(
                {
                    "schema_version": "flatband-calibration-source-record-v2",
                    "case_id": case.case_id,
                    "case_sha256": case.case_sha256,
                    "source_record": record.model_dump(
                        mode="python", round_trip=True
                    ),
                }
            ),
        )
        for case in cases
        for record in case.source_records
    )
    return tuple(
        sorted(
            values,
            key=lambda item: (
                item.case_id,
                item.source_record.source_id,
                item.source_record.source_record_id,
            ),
        )
    )


def _validated_calibration_cases_v2(
    cases: tuple[FlatBandBenchmarkCaseV1, ...],
) -> tuple[FlatBandBenchmarkCaseV1, ...]:
    values = tuple(
        _revalidate_v2(item, FlatBandBenchmarkCaseV1) for item in cases
    )
    case_ids = tuple(item.case_id for item in values)
    case_shas = tuple(item.case_sha256 for item in values)
    if case_ids != tuple(sorted(set(case_ids))):
        raise ValueError("calibration cases must be case-ID sorted and unique")
    if len(case_shas) != len(set(case_shas)):
        raise ValueError("calibration case SHA-256 identities must be unique")
    return values


class CalibrationSetManifestV2(StrictModel):
    """Frozen calibration universe with canonical V3 leakage provenance.

    This is not a benchmark split.  Structure memberships require real,
    succeeded grouping-run records over the exact calibration case universe;
    a caller cannot substitute guessed prototype/fingerprint group strings.
    Composition, mechanism, and source-record families are replayed from the
    full content-addressed cases by the same V2 derivation used by the
    benchmark leakage release.  The calibration schema is V2, but its
    independence graph is deliberately the formal leakage V3 lineage graph;
    the legacy broad ``MechanismFamily`` V2 edge is never accepted here.
    """

    schema_version: Literal["flatband-calibration-set-manifest-v2"] = (
        "flatband-calibration-set-manifest-v2"
    )
    artifact_kind: Literal["CALIBRATION_SET"] = "CALIBRATION_SET"
    manifest_id: Identifier
    manifest_sha256: Sha256
    annotation_guide_version: ShortText
    annotation_guide_sha256: Sha256
    case_freeze_policy_sha256: Sha256
    full_case_universe_sha256: Sha256
    cases: Annotated[
        tuple[FlatBandBenchmarkCaseV1, ...], Field(min_length=1, max_length=12)
    ]
    source_records: Annotated[
        tuple[CalibrationSourceRecordV2, ...], Field(min_length=1, max_length=192)
    ]
    source_policy_attestations: Annotated[
        tuple[CaseSourcePolicyAttestationV2, ...],
        Field(min_length=1, max_length=192),
    ]
    structure_grouping_release: StructureGroupingPrivateEvidenceReleaseV2
    structure_study_phase: Literal["CALIBRATION"] = "CALIBRATION"
    derivative_screening_release: DerivativeScreeningReleaseV3
    grouping_algorithms: Annotated[
        tuple[StructureGroupingAlgorithmV2, ...], Field(min_length=2, max_length=2)
    ]
    grouping_runs: Annotated[
        tuple[StructureGroupingRunV2, ...], Field(min_length=2, max_length=2)
    ]
    grouping_assignments: Annotated[
        tuple[StructureGroupingAssignmentV2, ...], Field(min_length=2, max_length=24)
    ]
    mechanism_lineage_registry: MechanismLineageRegistryV3
    mechanism_lineage_assignments: Annotated[
        tuple[MechanismLineageAssignmentV3, ...], Field(min_length=1, max_length=12)
    ]
    group_definitions: Annotated[
        tuple[LeakageGroupDefinitionV3, ...], Field(min_length=1, max_length=20_000)
    ]
    memberships: Annotated[
        tuple[LeakageMembershipV3, ...], Field(min_length=1, max_length=20_000)
    ]
    components: Annotated[
        tuple[LeakageComponentV1, ...], Field(min_length=1, max_length=12)
    ]
    frozen_at: Annotated[str, Field(min_length=20, max_length=40)]
    benchmark_split_identity_allowed: Literal[False] = False
    caller_supplied_group_strings_allowed: Literal[False] = False
    scientific_conclusion: Literal[False] = False

    @field_validator("frozen_at")
    @classmethod
    def validate_frozen_at(cls, value: str) -> str:
        return _require_rfc3339(value)

    @model_validator(mode="after")
    def validate_manifest(self) -> "CalibrationSetManifestV2":
        cases = _validated_calibration_cases_v2(self.cases)
        structure_release = _revalidate_v2(
            self.structure_grouping_release,
            StructureGroupingPrivateEvidenceReleaseV2,
        )
        assert_structure_grouping_release_exact_replay_v2(structure_release)
        if {
            item.preimage.study_phase
            for item in structure_release.computation.input_manifest.case_inputs
        } != {self.structure_study_phase}:
            raise ValueError(
                "calibration structure inputs require the frozen CALIBRATION phase"
            )
        derivative_screening = _revalidate_v2(
            self.derivative_screening_release,
            DerivativeScreeningReleaseV3,
        )
        assert_derivative_screening_release_exact_replay_v3(
            derivative_screening
        )
        assert_calibration_derivative_screening_all_not_derivative_v3(
            derivative_screening
        )
        if derivative_screening.cases != cases or (
            derivative_screening.case_universe_sha256
            != canonical_sha256(cases)
        ):
            raise ValueError(
                "calibration derivative screening differs from the exact case universe"
            )
        source_policies = tuple(
            _revalidate_v2(item, CaseSourcePolicyAttestationV2)
            for item in self.source_policy_attestations
        )
        policy_order = tuple(
            (item.case_id, item.source_id, item.source_record_id)
            for item in source_policies
        )
        if policy_order != tuple(sorted(set(policy_order))):
            raise ValueError(
                "calibration source policies must be case/source sorted and unique"
            )
        if (
            structure_release.final_cases,
            structure_release.source_policy_attestations,
            structure_release.grouping_algorithms,
            structure_release.grouping_runs,
            structure_release.grouping_assignments,
        ) != (
            cases,
            source_policies,
            self.grouping_algorithms,
            self.grouping_runs,
            self.grouping_assignments,
        ):
            raise ValueError(
                "calibration cases/source/grouping differ from the private structure release projection"
            )
        if self.full_case_universe_sha256 != (
            structure_grouping_case_universe_sha256_v2(cases)
        ):
            raise ValueError("calibration full-case universe SHA-256 does not replay")
        expected_sources = _calibration_source_records_v2(cases)
        observed_sources = tuple(
            _revalidate_v2(item, CalibrationSourceRecordV2)
            for item in self.source_records
        )
        if observed_sources != expected_sources:
            raise ValueError(
                "calibration source records do not exactly replay from frozen cases"
            )

        algorithms, runs, assignments, assignment_by_key = (
            _validate_structure_grouping_v2(
                cases=cases,
                grouping_algorithms=self.grouping_algorithms,
                grouping_runs=self.grouping_runs,
                grouping_assignments=self.grouping_assignments,
            )
        )
        if (
            algorithms,
            runs,
            assignments,
        ) != (
            self.grouping_algorithms,
            self.grouping_runs,
            self.grouping_assignments,
        ):
            raise ValueError("calibration grouping artifacts are not canonical")
        lineage_registry, lineage_values, lineage_by_case, lineage_by_id = (
            _validate_lineage_artifacts_v3(
                cases=cases,
                registry=self.mechanism_lineage_registry,
                assignments=self.mechanism_lineage_assignments,
            )
        )
        if (lineage_registry, lineage_values) != (
            self.mechanism_lineage_registry,
            self.mechanism_lineage_assignments,
        ):
            raise ValueError("calibration lineage artifacts are not canonical")
        definitions, memberships, groups_by_case = _derive_memberships_v3(
            cases=cases,
            algorithms=algorithms,
            structure_assignments=assignment_by_key,
            lineage_registry=lineage_registry,
            lineage_assignments=lineage_by_case,
            lineage_definitions=lineage_by_id,
        )
        if definitions != self.group_definitions:
            raise ValueError(
                "calibration group definitions do not replay from authoritative inputs"
            )
        if memberships != self.memberships:
            raise ValueError(
                "calibration memberships do not replay from authoritative inputs"
            )
        for case in cases:
            if case.leakage_group_ids != groups_by_case[case.case_id]:
                raise ValueError(
                    "calibration case group IDs differ from canonical V3 derivation"
                )
        expected_components = _components_from_memberships_v3(memberships)
        if self.components != expected_components:
            raise ValueError(
                "calibration connected components do not replay from memberships"
            )
        if any(
            _timestamp_v2(run.completed_at) > _timestamp_v2(self.frozen_at)
            for run in runs
        ):
            raise ValueError("calibration manifest predates a grouping run")
        if _timestamp_v2(structure_release.created_at) >= _timestamp_v2(
            self.frozen_at
        ):
            raise ValueError(
                "calibration structure release was not created before manifest seal"
            )
        if _timestamp_v2(derivative_screening.assembled_at) >= _timestamp_v2(
            self.frozen_at
        ):
            raise ValueError(
                "calibration derivative screening was not assembled before manifest seal"
            )
        if _timestamp_v2(lineage_registry.sealed_at) > _timestamp_v2(
            self.frozen_at
        ) or any(
            _timestamp_v2(item.assigned_at) > _timestamp_v2(self.frozen_at)
            for item in lineage_values
        ):
            raise ValueError("calibration manifest predates a lineage artifact")
        _assert_identity_v2(
            self,
            id_field="manifest_id",
            sha_field="manifest_sha256",
            prefix="calibration-set-manifest-v2",
        )
        return self


def build_calibration_set_manifest_v2(
    *,
    cases: tuple[FlatBandBenchmarkCaseV1, ...],
    annotation_guide_version: str,
    annotation_guide_sha256: str,
    case_freeze_policy_sha256: str,
    structure_grouping_release: StructureGroupingPrivateEvidenceReleaseV2,
    derivative_screening_release: DerivativeScreeningReleaseV3,
    source_policy_attestations: tuple[CaseSourcePolicyAttestationV2, ...],
    grouping_algorithms: tuple[StructureGroupingAlgorithmV2, ...],
    grouping_runs: tuple[StructureGroupingRunV2, ...],
    grouping_assignments: tuple[StructureGroupingAssignmentV2, ...],
    mechanism_lineage_registry: MechanismLineageRegistryV3,
    mechanism_lineage_assignments: tuple[MechanismLineageAssignmentV3, ...],
    frozen_at: str,
) -> CalibrationSetManifestV2:
    """Build calibration identity only after replaying every canonical group."""

    full_cases = tuple(
        sorted(
            (
                _revalidate_v2(item, FlatBandBenchmarkCaseV1)
                for item in cases
            ),
            key=lambda item: item.case_id,
        )
    )
    full_cases = _validated_calibration_cases_v2(full_cases)
    structure_release = _revalidate_v2(
        structure_grouping_release, StructureGroupingPrivateEvidenceReleaseV2
    )
    assert_structure_grouping_release_exact_replay_v2(structure_release)
    derivative_screening = _revalidate_v2(
        derivative_screening_release, DerivativeScreeningReleaseV3
    )
    assert_derivative_screening_release_exact_replay_v3(derivative_screening)
    assert_calibration_derivative_screening_all_not_derivative_v3(
        derivative_screening
    )
    ordered_source_policies = tuple(
        sorted(
            (
                _revalidate_v2(item, CaseSourcePolicyAttestationV2)
                for item in source_policy_attestations
            ),
            key=lambda item: (
                item.case_id,
                item.source_id,
                item.source_record_id,
            ),
        )
    )
    algorithms, runs, assignments, assignment_by_key = (
        _validate_structure_grouping_v2(
            cases=full_cases,
            grouping_algorithms=grouping_algorithms,
            grouping_runs=grouping_runs,
            grouping_assignments=grouping_assignments,
        )
    )
    lineage_registry, lineage_values, lineage_by_case, lineage_by_id = (
        _validate_lineage_artifacts_v3(
            cases=full_cases,
            registry=mechanism_lineage_registry,
            assignments=mechanism_lineage_assignments,
        )
    )
    definitions, memberships, groups_by_case = _derive_memberships_v3(
        cases=full_cases,
        algorithms=algorithms,
        structure_assignments=assignment_by_key,
        lineage_registry=lineage_registry,
        lineage_assignments=lineage_by_case,
        lineage_definitions=lineage_by_id,
    )
    for case in full_cases:
        if case.leakage_group_ids != groups_by_case[case.case_id]:
            raise ValueError(
                "calibration case group IDs differ from canonical V3 derivation"
            )
    values: dict[str, object] = {
        "annotation_guide_version": annotation_guide_version,
        "annotation_guide_sha256": annotation_guide_sha256,
        "case_freeze_policy_sha256": case_freeze_policy_sha256,
        "full_case_universe_sha256": (
            structure_grouping_case_universe_sha256_v2(full_cases)
        ),
        "cases": full_cases,
        "source_records": _calibration_source_records_v2(full_cases),
        "source_policy_attestations": ordered_source_policies,
        "structure_grouping_release": structure_release,
        "derivative_screening_release": derivative_screening,
        "grouping_algorithms": algorithms,
        "grouping_runs": runs,
        "grouping_assignments": assignments,
        "mechanism_lineage_registry": lineage_registry,
        "mechanism_lineage_assignments": lineage_values,
        "group_definitions": definitions,
        "memberships": memberships,
        "components": _components_from_memberships_v3(memberships),
        "frozen_at": _require_rfc3339(frozen_at),
    }
    return _build_identified_v2(
        CalibrationSetManifestV2,
        id_field="manifest_id",
        sha_field="manifest_sha256",
        prefix="calibration-set-manifest-v2",
        values=values,
    )


class IdentityAttestationEvidenceLevel(StrEnum):
    """Truthful ceiling for the current non-signed identity evidence."""

    INTERNAL_REPLAYABLE_CUSTODIAN_ATTESTATION = (
        "INTERNAL_REPLAYABLE_CUSTODIAN_ATTESTATION"
    )


class PrivateNaturalPersonBindingV2(StrictModel):
    """Private mapping retained by the identity custodian, never published."""

    expert_id: Identifier
    role: ExpertRole
    opaque_natural_person_subject_ref: Identifier
    natural_person_commitment_sha256: Sha256
    identity_evidence_artifact_uri: Annotated[
        str,
        Field(
            min_length=20,
            max_length=512,
            pattern=r"^artifact://private/[A-Za-z0-9][A-Za-z0-9._:/-]*$",
        ),
    ]
    identity_evidence_sha256: Sha256
    natural_person_verified: Literal[True] = True
    stable_subject_ref_verified: Literal[True] = True


class PrivateExpertIdentityCustodianAttestationV2(StrictModel):
    """Private, content-addressed internal attestation of natural-person identity.

    The custodian is responsible for assigning one stable opaque subject ref
    and one stable commitment to each natural person.  The verifier can
    replay the attestation and detect a reused subject/commitment, but it does
    not claim an external CA, institutional signature, or independently trusted
    identity proof.
    """

    schema_version: Literal[
        "flatband-private-expert-identity-custodian-attestation-v2"
    ] = "flatband-private-expert-identity-custodian-attestation-v2"
    attestation_id: Identifier
    attestation_sha256: Sha256
    custodian_id: Identifier
    custodian_policy_sha256: Sha256
    commitment_scheme: Literal["CUSTODIAN_STABLE_SUBJECT_COMMITMENT_V1"] = (
        "CUSTODIAN_STABLE_SUBJECT_COMMITMENT_V1"
    )
    commitment_key_id: Identifier | None = None
    evidence_level: Literal[
        IdentityAttestationEvidenceLevel.INTERNAL_REPLAYABLE_CUSTODIAN_ATTESTATION
    ] = IdentityAttestationEvidenceLevel.INTERNAL_REPLAYABLE_CUSTODIAN_ATTESTATION
    bindings: Annotated[
        tuple[PrivateNaturalPersonBindingV2, ...],
        Field(min_length=3, max_length=12),
    ]
    attested_at: Annotated[str, Field(min_length=20, max_length=40)]
    direct_identifiers_embedded: Literal[False] = False
    externally_trusted_signature_present: Literal[False] = False
    cryptographic_signature_or_key_required: Literal[False] = False
    identity_evidence_private: Literal[True] = True
    scientific_conclusion: Literal[False] = False

    @field_validator("attested_at")
    @classmethod
    def validate_attested_at(cls, value: str) -> str:
        return _require_rfc3339(value)

    @model_validator(mode="after")
    def validate_attestation(
        self,
    ) -> "PrivateExpertIdentityCustodianAttestationV2":
        bindings = tuple(
            _revalidate_v2(item, PrivateNaturalPersonBindingV2)
            for item in self.bindings
        )
        expert_ids = tuple(item.expert_id for item in bindings)
        subject_refs = tuple(
            item.opaque_natural_person_subject_ref for item in bindings
        )
        commitments = tuple(
            item.natural_person_commitment_sha256 for item in bindings
        )
        evidence_refs = tuple(
            (item.identity_evidence_artifact_uri, item.identity_evidence_sha256)
            for item in bindings
        )
        _require_sorted_unique_v2(expert_ids, "private identity expert IDs")
        if len(subject_refs) != len(set(subject_refs)):
            raise ValueError(
                "one natural person subject is bound to multiple pseudonyms"
            )
        if len(commitments) != len(set(commitments)):
            raise ValueError(
                "one natural-person commitment is bound to multiple pseudonyms"
            )
        if len(evidence_refs) != len(set(evidence_refs)):
            raise ValueError(
                "one private identity record is bound to multiple pseudonyms"
            )
        if set(subject_refs) & set(expert_ids):
            raise ValueError(
                "opaque natural-person subject refs must differ from public pseudonyms"
            )
        if self.custodian_id in set(expert_ids):
            raise ValueError("identity custodian cannot be a registered expert pseudonym")
        if sum(item.role is ExpertRole.REVIEWER for item in bindings) < 2:
            raise ValueError("identity attestation requires at least two reviewers")
        if sum(item.role is ExpertRole.ADJUDICATOR for item in bindings) < 1:
            raise ValueError("identity attestation requires a distinct adjudicator")
        _assert_identity_v2(
            self,
            id_field="attestation_id",
            sha_field="attestation_sha256",
            prefix="expert-id-attestation-v2",
        )
        return self


class PublicExpertIdentityV2(StrictModel):
    expert_id: Identifier
    role: ExpertRole
    pseudonymous: Literal[True] = True


class PublicExpertIdentityReleaseV2(StrictModel):
    """Safe public projection: pseudonyms plus an opaque attestation reference."""

    schema_version: Literal["flatband-public-expert-identity-release-v2"] = (
        "flatband-public-expert-identity-release-v2"
    )
    release_id: Identifier
    release_sha256: Sha256
    identity_attestation_id: Identifier
    identity_attestation_sha256: Sha256
    evidence_level: Literal[
        IdentityAttestationEvidenceLevel.INTERNAL_REPLAYABLE_CUSTODIAN_ATTESTATION
    ] = IdentityAttestationEvidenceLevel.INTERNAL_REPLAYABLE_CUSTODIAN_ATTESTATION
    experts: Annotated[
        tuple[PublicExpertIdentityV2, ...], Field(min_length=3, max_length=12)
    ]
    released_at: Annotated[str, Field(min_length=20, max_length=40)]
    externally_trusted_signature_present: Literal[False] = False
    scientific_conclusion: Literal[False] = False

    @field_validator("released_at")
    @classmethod
    def validate_released_at(cls, value: str) -> str:
        return _require_rfc3339(value)

    @model_validator(mode="after")
    def validate_release(self) -> "PublicExpertIdentityReleaseV2":
        experts = tuple(
            _revalidate_v2(item, PublicExpertIdentityV2)
            for item in self.experts
        )
        expert_ids = tuple(item.expert_id for item in experts)
        _require_sorted_unique_v2(expert_ids, "public identity expert IDs")
        if sum(item.role is ExpertRole.REVIEWER for item in experts) < 2:
            raise ValueError("public identity release requires at least two reviewers")
        if sum(item.role is ExpertRole.ADJUDICATOR for item in experts) < 1:
            raise ValueError("public identity release requires an adjudicator")
        _assert_identity_v2(
            self,
            id_field="release_id",
            sha_field="release_sha256",
            prefix="expert-id-public-v2",
        )
        return self


def build_private_expert_identity_attestation_v2(
    *,
    custodian_id: str,
    custodian_policy_sha256: str,
    commitment_key_id: str | None = None,
    bindings: tuple[PrivateNaturalPersonBindingV2, ...],
    attested_at: str,
) -> PrivateExpertIdentityCustodianAttestationV2:
    """Build the private internal attestation; no signing claim is added."""

    ordered = tuple(
        sorted(
            (
                _revalidate_v2(item, PrivateNaturalPersonBindingV2)
                for item in bindings
            ),
            key=lambda item: item.expert_id,
        )
    )
    return _build_identified_v2(
        PrivateExpertIdentityCustodianAttestationV2,
        id_field="attestation_id",
        sha_field="attestation_sha256",
        prefix="expert-id-attestation-v2",
        values={
            "custodian_id": custodian_id,
            "custodian_policy_sha256": custodian_policy_sha256,
            "commitment_key_id": commitment_key_id,
            "bindings": ordered,
            "attested_at": _require_rfc3339(attested_at),
        },
    )


def build_public_expert_identity_release_v2(
    *,
    private_attestation: PrivateExpertIdentityCustodianAttestationV2,
    released_at: str,
) -> PublicExpertIdentityReleaseV2:
    """Project only pseudonymous role bindings and the attestation address."""

    private = _revalidate_v2(
        private_attestation, PrivateExpertIdentityCustodianAttestationV2
    )
    if _timestamp_v2(released_at) < _timestamp_v2(private.attested_at):
        raise ValueError("public identity release predates private attestation")
    return _build_identified_v2(
        PublicExpertIdentityReleaseV2,
        id_field="release_id",
        sha_field="release_sha256",
        prefix="expert-id-public-v2",
        values={
            "identity_attestation_id": private.attestation_id,
            "identity_attestation_sha256": private.attestation_sha256,
            "experts": tuple(
                PublicExpertIdentityV2(
                    expert_id=item.expert_id,
                    role=item.role,
                )
                for item in private.bindings
            ),
            "released_at": _require_rfc3339(released_at),
        },
    )


def assert_private_identity_attestation_matches_public_v2(
    *,
    private_attestation: PrivateExpertIdentityCustodianAttestationV2,
    public_release: PublicExpertIdentityReleaseV2,
) -> None:
    """Replay the safe projection and reject any private identity-value leak."""

    private = _revalidate_v2(
        private_attestation, PrivateExpertIdentityCustodianAttestationV2
    )
    public = _revalidate_v2(public_release, PublicExpertIdentityReleaseV2)
    if (
        public.identity_attestation_id,
        public.identity_attestation_sha256,
    ) != (private.attestation_id, private.attestation_sha256):
        raise ValueError("public identity release references a foreign attestation")
    expected = tuple(
        PublicExpertIdentityV2(expert_id=item.expert_id, role=item.role)
        for item in private.bindings
    )
    if public.experts != expected:
        raise ValueError("public pseudonymous identities do not replay from attestation")
    if _timestamp_v2(public.released_at) < _timestamp_v2(private.attested_at):
        raise ValueError("public identity release predates private attestation")
    def scalar_strings(value: object) -> set[str]:
        if isinstance(value, str):
            return {value}
        if isinstance(value, dict):
            return {
                item
                for child in value.values()
                for item in scalar_strings(child)
            }
        if isinstance(value, (tuple, list)):
            return {
                item
                for child in value
                for item in scalar_strings(child)
            }
        return set()

    public_values = scalar_strings(public.model_dump(mode="python"))
    private_only_values = tuple(
        value
        for binding in private.bindings
        for value in (
            binding.opaque_natural_person_subject_ref,
            binding.natural_person_commitment_sha256,
            binding.identity_evidence_artifact_uri,
            binding.identity_evidence_sha256,
        )
    ) + tuple(
        value
        for value in (
            private.custodian_id,
            private.commitment_key_id,
            private.custodian_policy_sha256,
        )
        if value is not None
    )
    if set(private_only_values) & public_values:
        raise ValueError("public identity release leaks private identity material")


class CalibrationCompletionV2(StrictModel):
    """One sealed pre-discussion answer set bound to a real V2 manifest."""

    schema_version: Literal["flatband-calibration-completion-v2"] = (
        "flatband-calibration-completion-v2"
    )
    completion_id: Identifier
    completion_sha256: Sha256
    expert_id: Identifier
    role: ExpertRole
    annotation_guide_version: ShortText
    annotation_guide_sha256: Sha256
    calibration_manifest_id: Identifier
    calibration_manifest_sha256: Sha256
    raw_answers_sha256: Sha256
    calibration_result_sha256: Sha256
    completed_at: Annotated[str, Field(min_length=20, max_length=40)]
    completed_before_group_discussion: Literal[True] = True
    sealed: Literal[True] = True
    scientific_conclusion: Literal[False] = False

    @field_validator("completed_at")
    @classmethod
    def validate_completed_at(cls, value: str) -> str:
        return _require_rfc3339(value)

    @model_validator(mode="after")
    def validate_completion(self) -> "CalibrationCompletionV2":
        if self.raw_answers_sha256 in {
            self.calibration_manifest_sha256,
            self.annotation_guide_sha256,
        }:
            raise ValueError("raw answers must be a distinct sealed artifact")
        if self.calibration_result_sha256 in {
            self.calibration_manifest_sha256,
            self.annotation_guide_sha256,
            self.raw_answers_sha256,
        }:
            raise ValueError("calibration result must be a distinct artifact")
        _assert_identity_v2(
            self,
            id_field="completion_id",
            sha_field="completion_sha256",
            prefix="calibration-completion-v2",
        )
        return self


def build_calibration_completion_v2(
    *,
    expert: PublicExpertIdentityV2,
    calibration_manifest: CalibrationSetManifestV2,
    raw_answers_sha256: str,
    calibration_result_sha256: str,
    completed_at: str,
) -> CalibrationCompletionV2:
    expert = _revalidate_v2(expert, PublicExpertIdentityV2)
    manifest = _revalidate_v2(
        calibration_manifest, CalibrationSetManifestV2
    )
    values: dict[str, object] = {
        "expert_id": expert.expert_id,
        "role": expert.role,
        "annotation_guide_version": manifest.annotation_guide_version,
        "annotation_guide_sha256": manifest.annotation_guide_sha256,
        "calibration_manifest_id": manifest.manifest_id,
        "calibration_manifest_sha256": manifest.manifest_sha256,
        "raw_answers_sha256": raw_answers_sha256,
        "calibration_result_sha256": calibration_result_sha256,
        "completed_at": _require_rfc3339(completed_at),
    }
    return _build_identified_v2(
        CalibrationCompletionV2,
        id_field="completion_id",
        sha_field="completion_sha256",
        prefix="calibration-completion-v2",
        values=values,
    )


def assert_calibration_completion_replays_manifest_v2(
    *,
    completion: CalibrationCompletionV2,
    calibration_manifest: CalibrationSetManifestV2,
) -> None:
    completion = _revalidate_v2(completion, CalibrationCompletionV2)
    manifest = _revalidate_v2(
        calibration_manifest, CalibrationSetManifestV2
    )
    if (
        completion.calibration_manifest_id,
        completion.calibration_manifest_sha256,
        completion.annotation_guide_version,
        completion.annotation_guide_sha256,
    ) != (
        manifest.manifest_id,
        manifest.manifest_sha256,
        manifest.annotation_guide_version,
        manifest.annotation_guide_sha256,
    ):
        raise ValueError("calibration completion references a foreign manifest or guide")
    if _timestamp_v2(completion.completed_at) <= _timestamp_v2(manifest.frozen_at):
        raise ValueError("calibration completion does not follow the frozen manifest")


class ExpertStudyRegistryV2(StrictModel):
    """Public Pilot registry using only pseudonyms and opaque identity refs."""

    schema_version: Literal["flatband-expert-study-registry-v2"] = (
        "flatband-expert-study-registry-v2"
    )
    registry_id: Identifier
    registry_sha256: Sha256
    split_manifest_id: Identifier
    split_manifest_sha256: Sha256
    annotation_guide_version: ShortText
    annotation_guide_sha256: Sha256
    calibration_manifest_id: Identifier
    calibration_manifest_sha256: Sha256
    public_identity_release_id: Identifier
    public_identity_release_sha256: Sha256
    identity_attestation_id: Identifier
    identity_attestation_sha256: Sha256
    identity_evidence_level: Literal[
        IdentityAttestationEvidenceLevel.INTERNAL_REPLAYABLE_CUSTODIAN_ATTESTATION
    ] = IdentityAttestationEvidenceLevel.INTERNAL_REPLAYABLE_CUSTODIAN_ATTESTATION
    experts: Annotated[
        tuple[PublicExpertIdentityV2, ...], Field(min_length=3, max_length=12)
    ]
    calibration_completions: Annotated[
        tuple[CalibrationCompletionV2, ...], Field(min_length=3, max_length=12)
    ]
    conflict_assessments: Annotated[
        tuple[CaseConflictAssessmentV1, ...], Field(min_length=90, max_length=1_440)
    ]
    assignments: Annotated[
        tuple[CaseExpertAssignmentV1, ...], Field(min_length=30, max_length=120)
    ]
    registered_at: Annotated[str, Field(min_length=20, max_length=40)]
    sealed: Literal[True] = True
    externally_trusted_identity_signature_present: Literal[False] = False
    scientific_conclusion: Literal[False] = False

    @field_validator("registered_at")
    @classmethod
    def validate_registered_at(cls, value: str) -> str:
        return _require_rfc3339(value)

    @model_validator(mode="after")
    def validate_registry(self) -> "ExpertStudyRegistryV2":
        registered_at = _timestamp_v2(self.registered_at)
        experts = tuple(
            _revalidate_v2(item, PublicExpertIdentityV2)
            for item in self.experts
        )
        expert_ids = tuple(item.expert_id for item in experts)
        _require_sorted_unique_v2(expert_ids, "V2 registry expert IDs")
        expert_by_id = {item.expert_id: item for item in experts}
        if sum(item.role is ExpertRole.REVIEWER for item in experts) < 2:
            raise ValueError("V2 registry requires at least two reviewers")
        if sum(item.role is ExpertRole.ADJUDICATOR for item in experts) < 1:
            raise ValueError("V2 registry requires at least one adjudicator")

        completions = tuple(
            _revalidate_v2(item, CalibrationCompletionV2)
            for item in self.calibration_completions
        )
        completion_ids = tuple(item.expert_id for item in completions)
        _require_sorted_unique_v2(
            completion_ids, "V2 calibration completion expert IDs"
        )
        if set(completion_ids) != set(expert_ids):
            raise ValueError("every V2 expert requires exactly one completion")
        for completion in completions:
            expert = expert_by_id[completion.expert_id]
            if completion.role is not expert.role:
                raise ValueError("V2 calibration role differs from expert role")
            if (
                completion.annotation_guide_version,
                completion.annotation_guide_sha256,
                completion.calibration_manifest_id,
                completion.calibration_manifest_sha256,
            ) != (
                self.annotation_guide_version,
                self.annotation_guide_sha256,
                self.calibration_manifest_id,
                self.calibration_manifest_sha256,
            ):
                raise ValueError(
                    "V2 completion binds a different calibration manifest or guide"
                )
            if _timestamp_v2(completion.completed_at) >= registered_at:
                raise ValueError("V2 calibration completion was not before registry seal")

        conflicts = tuple(
            _revalidate_v2(item, CaseConflictAssessmentV1)
            for item in self.conflict_assessments
        )
        conflict_keys = tuple(
            (item.case_id, item.expert_id) for item in conflicts
        )
        if conflict_keys != tuple(sorted(set(conflict_keys))):
            raise ValueError("V2 conflicts must be case/expert sorted and unique")
        if any(_timestamp_v2(item.assessed_at) >= registered_at for item in conflicts):
            raise ValueError("V2 conflict assessment was not before registry seal")

        assignments = tuple(
            _revalidate_v2(item, CaseExpertAssignmentV1)
            for item in self.assignments
        )
        assignment_cases = tuple(item.case_id for item in assignments)
        _require_sorted_unique_v2(assignment_cases, "V2 assignment case IDs")
        expected_conflicts = {
            (assignment.case_id, expert_id)
            for assignment in assignments
            for expert_id in expert_ids
        }
        conflict_map = {
            (item.case_id, item.expert_id): item for item in conflicts
        }
        if set(conflict_map) != expected_conflicts:
            raise ValueError("V2 conflict map must exactly cover case x expert")
        for assignment in assignments:
            for expert_id in (*assignment.reviewer_ids, assignment.adjudicator_id):
                expert = expert_by_id.get(expert_id)
                if expert is None:
                    raise ValueError("V2 assignment references a foreign expert")
                expected_role = (
                    ExpertRole.ADJUDICATOR
                    if expert_id == assignment.adjudicator_id
                    else ExpertRole.REVIEWER
                )
                if expert.role is not expected_role:
                    raise ValueError("V2 assignment uses the wrong expert role")
                if conflict_map[(assignment.case_id, expert_id)].status is not (
                    ConflictStatus.CLEAR
                ):
                    raise ValueError("a recused V2 expert cannot be assigned")
        _assert_identity_v2(
            self,
            id_field="registry_id",
            sha_field="registry_sha256",
            prefix="expert-study-registry-v2",
        )
        return self


def build_expert_study_registry_v2(
    *,
    split_manifest: BenchmarkSplitManifestV2,
    calibration_manifest: CalibrationSetManifestV2,
    public_identity_release: PublicExpertIdentityReleaseV2,
    calibration_completions: tuple[CalibrationCompletionV2, ...],
    conflict_assessments: tuple[CaseConflictAssessmentV1, ...],
    assignments: tuple[CaseExpertAssignmentV1, ...],
    registered_at: str,
) -> ExpertStudyRegistryV2:
    split = _revalidate_v2(split_manifest, BenchmarkSplitManifestV2)
    calibration = _revalidate_v2(
        calibration_manifest, CalibrationSetManifestV2
    )
    identity = _revalidate_v2(
        public_identity_release, PublicExpertIdentityReleaseV2
    )
    registered = _require_rfc3339(registered_at)
    if _timestamp_v2(identity.released_at) >= _timestamp_v2(registered):
        raise ValueError("public identity release was not before registry seal")
    values: dict[str, object] = {
        "split_manifest_id": split.manifest_id,
        "split_manifest_sha256": split.manifest_sha256,
        "annotation_guide_version": calibration.annotation_guide_version,
        "annotation_guide_sha256": calibration.annotation_guide_sha256,
        "calibration_manifest_id": calibration.manifest_id,
        "calibration_manifest_sha256": calibration.manifest_sha256,
        "public_identity_release_id": identity.release_id,
        "public_identity_release_sha256": identity.release_sha256,
        "identity_attestation_id": identity.identity_attestation_id,
        "identity_attestation_sha256": identity.identity_attestation_sha256,
        "experts": identity.experts,
        "calibration_completions": tuple(
            sorted(
                (
                    _revalidate_v2(item, CalibrationCompletionV2)
                    for item in calibration_completions
                ),
                key=lambda item: item.expert_id,
            )
        ),
        "conflict_assessments": tuple(
            sorted(
                (
                    _revalidate_v2(item, CaseConflictAssessmentV1)
                    for item in conflict_assessments
                ),
                key=lambda item: (item.case_id, item.expert_id),
            )
        ),
        "assignments": tuple(
            sorted(
                (
                    _revalidate_v2(item, CaseExpertAssignmentV1)
                    for item in assignments
                ),
                key=lambda item: item.case_id,
            )
        ),
        "registered_at": registered,
    }
    registry = _build_identified_v2(
        ExpertStudyRegistryV2,
        id_field="registry_id",
        sha_field="registry_sha256",
        prefix="expert-study-registry-v2",
        values=values,
    )
    assert_expert_registry_covers_split_v2(
        registry=registry, split_manifest=split
    )
    for completion in registry.calibration_completions:
        assert_calibration_completion_replays_manifest_v2(
            completion=completion,
            calibration_manifest=calibration,
        )
    return registry


def assert_expert_registry_covers_split_v2(
    *,
    registry: ExpertStudyRegistryV2,
    split_manifest: BenchmarkSplitManifestV2,
) -> None:
    """Replay V2 assignments/conflicts against every exact split case."""

    registry = _revalidate_v2(registry, ExpertStudyRegistryV2)
    split = _revalidate_v2(split_manifest, BenchmarkSplitManifestV2)
    if (registry.split_manifest_id, registry.split_manifest_sha256) != (
        split.manifest_id,
        split.manifest_sha256,
    ):
        raise ValueError("V2 expert registry references a different split")
    expected = {item.case_id: item.case_sha256 for item in split.cases}
    observed = {item.case_id: item.case_sha256 for item in registry.assignments}
    if observed != expected:
        raise ValueError("V2 expert registry does not exactly cover split cases")
    for assessment in registry.conflict_assessments:
        if assessment.case_sha256 != expected.get(assessment.case_id):
            raise ValueError("V2 conflict assessment binds a foreign case identity")


def _source_identity_sets_v2(
    cases: tuple[FlatBandBenchmarkCaseV1, ...],
) -> tuple[set[tuple[str, str]], set[str], set[str]]:
    keys: set[tuple[str, str]] = set()
    urls: set[str] = set()
    raw_shas: set[str] = set()
    for case in cases:
        for record in case.source_records:
            keys.add((record.source_id, record.source_record_id))
            urls.add(record.canonical_url)
            if record.raw_sha256 is not None:
                raw_shas.add(record.raw_sha256)
    return keys, urls, raw_shas


def _lineage_identity_sets_v2(
    *,
    registry: MechanismLineageRegistryV3,
    assignments: tuple[MechanismLineageAssignmentV3, ...],
) -> tuple[set[str], set[str], set[str]]:
    """Return only lineages actually assigned in one case universe."""

    definitions = {item.lineage_id: item for item in registry.definitions}
    lineage_ids = {item.lineage_id for item in assignments}
    lineage_shas = {definitions[item].lineage_sha256 for item in lineage_ids}
    scientific_preimages = {
        definitions[item].scientific_preimage_sha256 for item in lineage_ids
    }
    return lineage_ids, lineage_shas, scientific_preimages


def _component_scientific_signatures_v2(
    *,
    components: tuple[LeakageComponentV1, ...],
    memberships: tuple[LeakageMembershipV3, ...],
) -> set[str]:
    """Address component graph content independently of persisted component IDs."""

    memberships_by_case: dict[str, set[tuple[str, str]]] = {}
    for membership in memberships:
        memberships_by_case.setdefault(membership.case_id, set()).add(
            (membership.axis.value, membership.group_id)
        )
    return {
        canonical_sha256(
            {
                "typed_groups": tuple(
                    sorted(
                        {
                            group
                            for case_id in component.case_ids
                            for group in memberships_by_case[case_id]
                        }
                    )
                )
            }
        )
        for component in components
    }


def assert_calibration_disjoint_from_benchmarks_v2(
    *,
    calibration_manifest: CalibrationSetManifestV2,
    benchmark_case_universes: tuple[
        tuple[FlatBandBenchmarkCaseV1, ...], ...
    ],
    split_manifests: tuple[BenchmarkSplitManifestV2, ...],
    leakage_releases: tuple[LeakageComponentReleaseV3, ...],
) -> None:
    """Replay V3 and prove full-axis calibration/benchmark independence.

    This boundary accepts no caller-provided group strings.  Each benchmark
    universe is replayed through its exact ``BenchmarkSplitManifestV2`` and
    ``LeakageComponentReleaseV3``.  Calibration and benchmark assignments must
    resolve from one global, sealed mechanism-lineage registry.  Only lineage
    definitions actually assigned in each universe participate in the overlap
    test; unused global taxonomy definitions do not create false overlap.
    """

    calibration = _revalidate_v2(
        calibration_manifest, CalibrationSetManifestV2
    )
    if (
        not benchmark_case_universes
        or len(benchmark_case_universes) != len(split_manifests)
        or len(split_manifests) != len(leakage_releases)
    ):
        raise ValueError(
            "calibration isolation requires one-to-one cases, split V2, and leakage V3"
        )
    splits = tuple(
        _revalidate_v2(item, BenchmarkSplitManifestV2)
        for item in split_manifests
    )
    leakages = tuple(
        _revalidate_v2(item, LeakageComponentReleaseV3)
        for item in leakage_releases
    )
    universes = tuple(
        tuple(
            sorted(
                (
                    _revalidate_v2(case, FlatBandBenchmarkCaseV1)
                    for case in universe
                ),
                key=lambda case: case.case_id,
            )
        )
        for universe in benchmark_case_universes
    )
    split_keys = tuple(
        (item.manifest_id, item.manifest_sha256) for item in splits
    )
    if len(split_keys) != len(set(split_keys)):
        raise ValueError("duplicate benchmark split V2 supplied")
    leakage_by_split: dict[tuple[str, str], LeakageComponentReleaseV3] = {}
    for leakage in leakages:
        key = (leakage.split_manifest_id, leakage.split_manifest_sha256)
        if key in leakage_by_split:
            raise ValueError("duplicate leakage V3 release supplied for one split")
        leakage_by_split[key] = leakage
    if set(leakage_by_split) != set(split_keys):
        raise ValueError(
            "benchmark split V2 and leakage V3 releases are not one-to-one"
        )

    calibration_case_ids = {item.case_id for item in calibration.cases}
    calibration_case_shas = {item.case_sha256 for item in calibration.cases}
    calibration_structure_shas = {
        item.structure_sha256 for item in calibration.cases
    }
    calibration_typed_groups = {
        (item.axis.value, item.group_id) for item in calibration.memberships
    }
    calibration_group_preimages = {
        (item.axis.value, item.canonical_preimage)
        for item in calibration.group_definitions
    }
    calibration_components = {item.component_id for item in calibration.components}
    calibration_component_signatures = _component_scientific_signatures_v2(
        components=calibration.components,
        memberships=calibration.memberships,
    )
    calibration_source_keys, calibration_urls, calibration_raw_shas = (
        _source_identity_sets_v2(calibration.cases)
    )
    calibration_algorithms = {
        item.axis: (item.algorithm_id, item.algorithm_sha256)
        for item in calibration.grouping_algorithms
    }
    calibration_lineage_ids, calibration_lineage_shas, calibration_preimages = (
        _lineage_identity_sets_v2(
            registry=calibration.mechanism_lineage_registry,
            assignments=calibration.mechanism_lineage_assignments,
        )
    )

    forbidden_calibration_hashes: set[str] = set()
    for split, all_cases in zip(splits, universes, strict=True):
        leakage = leakage_by_split[(split.manifest_id, split.manifest_sha256)]
        forbidden_calibration_hashes.update(
            {
                split.manifest_sha256,
                leakage.release_sha256,
            }
        )
        assert_leakage_split_closure_v3(
            cases=all_cases,
            split_manifest=split,
            release=leakage,
        )
        if (
            leakage.mechanism_lineage_registry.registry_id,
            leakage.mechanism_lineage_registry.registry_sha256,
        ) != (
            calibration.mechanism_lineage_registry.registry_id,
            calibration.mechanism_lineage_registry.registry_sha256,
        ):
            raise ValueError(
                "calibration and benchmark do not share one global lineage registry"
            )
        benchmark_algorithms = {
            item.axis: (item.algorithm_id, item.algorithm_sha256)
            for item in leakage.grouping_algorithms
        }
        if benchmark_algorithms != calibration_algorithms:
            raise ValueError(
                "calibration and benchmark use different structure grouping algorithms"
            )

        benchmark_case_ids = {item.case_id for item in all_cases}
        benchmark_case_shas = {item.case_sha256 for item in all_cases}
        benchmark_structure_shas = {
            item.structure_sha256 for item in all_cases
        }
        if calibration_case_ids & benchmark_case_ids:
            raise ValueError("calibration and benchmark share a case ID")
        if calibration_case_shas & benchmark_case_shas:
            raise ValueError("calibration and benchmark share a case SHA-256")
        if calibration_structure_shas & benchmark_structure_shas:
            raise ValueError("calibration and benchmark share an exact structure")

        benchmark_source_keys, benchmark_urls, benchmark_raw_shas = (
            _source_identity_sets_v2(all_cases)
        )
        if calibration_source_keys & benchmark_source_keys:
            raise ValueError("calibration and benchmark share a source record")
        if calibration_urls & benchmark_urls:
            raise ValueError("calibration and benchmark share a canonical source URL")
        if calibration_raw_shas & benchmark_raw_shas:
            raise ValueError("calibration and benchmark share raw source content")

        benchmark_lineage_ids, benchmark_lineage_shas, benchmark_preimages = (
            _lineage_identity_sets_v2(
                registry=leakage.mechanism_lineage_registry,
                assignments=leakage.mechanism_lineage_assignments,
            )
        )
        if calibration_preimages & benchmark_preimages:
            raise ValueError(
                "calibration and benchmark share a mechanism scientific preimage"
            )
        if calibration_lineage_ids & benchmark_lineage_ids:
            raise ValueError("calibration and benchmark share a mechanism lineage")
        if calibration_lineage_shas & benchmark_lineage_shas:
            raise ValueError(
                "calibration and benchmark share a mechanism lineage definition"
            )

        benchmark_typed_groups = {
            (item.axis.value, item.group_id) for item in leakage.memberships
        }
        if calibration_typed_groups & benchmark_typed_groups:
            raise ValueError(
                "calibration and benchmark share a canonical leakage family"
            )
        benchmark_group_preimages = {
            (item.axis.value, item.canonical_preimage)
            for item in leakage.group_definitions
        }
        if calibration_group_preimages & benchmark_group_preimages:
            raise ValueError(
                "calibration and benchmark share a leakage scientific preimage"
            )
        benchmark_components = {item.component_id for item in leakage.components}
        if calibration_components & benchmark_components:
            raise ValueError(
                "calibration and benchmark share a connected-component identity"
            )
        benchmark_component_signatures = _component_scientific_signatures_v2(
            components=leakage.components,
            memberships=leakage.memberships,
        )
        if calibration_component_signatures & benchmark_component_signatures:
            raise ValueError(
                "calibration and benchmark share a connected-component graph"
            )

    if calibration.manifest_sha256 in forbidden_calibration_hashes:
        raise ValueError("a split/release SHA cannot stand in for calibration identity")


def assert_legacy_v2_calibration_disjointness_unavailable(
    *,
    calibration_manifest: CalibrationSetManifestV2,
    leakage_releases: tuple[LeakageComponentReleaseV2, ...],
) -> None:
    """Explicitly reject the contradictory broad-mechanism V2 graph."""

    _revalidate_v2(calibration_manifest, CalibrationSetManifestV2)
    if leakage_releases:
        tuple(_revalidate_v2(item, LeakageComponentReleaseV2) for item in leakage_releases)
    raise ValueError(
        "legacy leakage V2 cannot certify formal calibration independence; use split V2 and leakage V3"
    )


def assert_expert_registry_precedes_execution_v2(
    *,
    registry: ExpertStudyRegistryV2,
    execution_releases: tuple[object, ...],
) -> None:
    """Require the registry seal before every real execution budget artifact."""

    from material_agent.research.flatband_execution import ExecutionReleaseV2

    registry = _revalidate_v2(registry, ExpertStudyRegistryV2)
    if not execution_releases:
        raise ValueError("execution timing check requires a real execution release")
    all_budgets: list[object] = []
    for raw_release in execution_releases:
        release = ExecutionReleaseV2.model_validate(
            ExecutionReleaseV2.model_validate(raw_release).model_dump(
                mode="python", round_trip=True
            )
        )
        split = release.execution_matrix.split_manifest
        if (registry.split_manifest_id, registry.split_manifest_sha256) != (
            split.manifest_id,
            split.manifest_sha256,
        ):
            raise ValueError("execution release uses a foreign registry split")
        if not release.budget_manifests:
            raise ValueError("execution release has no budget artifacts")
        all_budgets.extend(release.budget_manifests)
    assert_expert_registry_sealed_before_budgets_v2(
        registry=registry,
        budget_manifests=tuple(all_budgets),
    )


def assert_expert_registry_sealed_before_budgets_v2(
    *,
    registry: ExpertStudyRegistryV2,
    budget_manifests: tuple[object, ...],
) -> None:
    """Direct timing boundary reused by the full ExecutionRelease verifier."""

    from material_agent.research.flatband_execution import (
        BudgetManifestV1,
        BudgetManifestV2,
    )

    registry = _revalidate_v2(registry, ExpertStudyRegistryV2)
    if not budget_manifests:
        raise ValueError("registry timing check requires budget manifests")
    budgets = tuple(
        (
            BudgetManifestV2.model_validate(
                BudgetManifestV2.model_validate(item).model_dump(
                    mode="python", round_trip=True
                )
            )
            if getattr(item, "schema_version", None)
            == "flatband-budget-manifest-v2"
            else BudgetManifestV1.model_validate(
                BudgetManifestV1.model_validate(item).model_dump(
                    mode="python", round_trip=True
                )
            )
        )
        for item in budget_manifests
    )
    if any(
        _timestamp_v2(registry.registered_at) >= _timestamp_v2(item.frozen_at)
        for item in budgets
    ):
        raise ValueError("expert registry was not sealed before every budget")


def assert_distinct_natural_person_assignments_v2(
    *,
    private_identity_attestation: PrivateExpertIdentityCustodianAttestationV2,
    registry: ExpertStudyRegistryV2,
) -> None:
    """Prove every case has two reviewers and a third natural-person adjudicator."""

    private = _revalidate_v2(
        private_identity_attestation,
        PrivateExpertIdentityCustodianAttestationV2,
    )
    registry = _revalidate_v2(registry, ExpertStudyRegistryV2)
    binding_by_expert = {item.expert_id: item for item in private.bindings}
    expected_experts = {
        (item.expert_id, item.role) for item in registry.experts
    }
    observed_experts = {
        (item.expert_id, item.role) for item in private.bindings
    }
    if observed_experts != expected_experts:
        raise ValueError(
            "private natural-person bindings do not exactly cover registry experts"
        )
    for assignment in registry.assignments:
        participant_ids = (
            *assignment.reviewer_ids,
            assignment.adjudicator_id,
        )
        if len(participant_ids) != 3 or len(set(participant_ids)) != 3:
            raise ValueError(
                "each case requires two reviewers and one distinct adjudicator pseudonym"
            )
        participants = tuple(binding_by_expert[item] for item in participant_ids)
        if tuple(item.role for item in participants[:2]) != (
            ExpertRole.REVIEWER,
            ExpertRole.REVIEWER,
        ) or participants[2].role is not ExpertRole.ADJUDICATOR:
            raise ValueError("case assignment roles differ from private identity roles")
        if len(
            {item.opaque_natural_person_subject_ref for item in participants}
        ) != 3 or len(
            {item.natural_person_commitment_sha256 for item in participants}
        ) != 3 or len(
            {
                (
                    item.identity_evidence_artifact_uri,
                    item.identity_evidence_sha256,
                )
                for item in participants
            }
        ) != 3:
            raise ValueError(
                "two reviewers and the adjudicator are not three distinct natural persons"
            )


def assert_formal_pilot_expert_closure_legacy_v2_upstream(
    *,
    registry: ExpertStudyRegistryV2,
    split_manifest: BenchmarkSplitManifestV2,
    benchmark_cases: tuple[FlatBandBenchmarkCaseV1, ...],
    private_identity_attestation: PrivateExpertIdentityCustodianAttestationV2,
    public_identity_release: PublicExpertIdentityReleaseV2,
    calibration_manifest: CalibrationSetManifestV2,
    frozen_case_release: object,
    pre_run_eligibility_release: object,
    execution_release: object,
    leakage_release: LeakageComponentReleaseV3,
) -> None:
    """Legacy/non-formal V2-upstream fixture verifier.

    This entry point deliberately validates the concrete V2 models.  It never
    projects a historical frozen, eligibility, or execution release into V2.
    """

    from material_agent.research.flatband_cases import (
        FrozenCaseReleaseV2,
        PreRunEligibilityReleaseV2,
        assert_pre_run_eligibility_precedes_execution_v2,
    )
    from material_agent.research.flatband_execution import ExecutionReleaseV2

    split = _revalidate_v2(split_manifest, BenchmarkSplitManifestV2)
    if split.manifest_kind not in {
        SplitManifestKind.PILOT_R1,
        SplitManifestKind.PILOT_R2,
    }:
        raise ValueError("formal Pilot expert verifier requires a Pilot split")
    registry = _revalidate_v2(registry, ExpertStudyRegistryV2)
    calibration = _revalidate_v2(
        calibration_manifest, CalibrationSetManifestV2
    )
    frozen_input = FrozenCaseReleaseV2.model_validate(frozen_case_release)
    frozen = FrozenCaseReleaseV2.model_validate(
        frozen_input.model_dump(mode="python", round_trip=True)
    )
    eligibility_input = PreRunEligibilityReleaseV2.model_validate(
        pre_run_eligibility_release
    )
    eligibility = PreRunEligibilityReleaseV2.model_validate(
        eligibility_input.model_dump(mode="python", round_trip=True)
    )
    execution_input = ExecutionReleaseV2.model_validate(execution_release)
    execution = ExecutionReleaseV2.model_validate(
        execution_input.model_dump(mode="python", round_trip=True)
    )
    private = _revalidate_v2(
        private_identity_attestation,
        PrivateExpertIdentityCustodianAttestationV2,
    )
    public = _revalidate_v2(
        public_identity_release, PublicExpertIdentityReleaseV2
    )
    assert_expert_registry_covers_split_v2(
        registry=registry, split_manifest=split
    )
    assert_private_identity_attestation_matches_public_v2(
        private_attestation=private,
        public_release=public,
    )
    assert_distinct_natural_person_assignments_v2(
        private_identity_attestation=private,
        registry=registry,
    )
    if (
        registry.public_identity_release_id,
        registry.public_identity_release_sha256,
        registry.identity_attestation_id,
        registry.identity_attestation_sha256,
        registry.experts,
    ) != (
        public.release_id,
        public.release_sha256,
        public.identity_attestation_id,
        public.identity_attestation_sha256,
        public.experts,
    ):
        raise ValueError("V2 registry does not replay its public identity release")
    if (
        registry.annotation_guide_version,
        registry.annotation_guide_sha256,
        registry.calibration_manifest_id,
        registry.calibration_manifest_sha256,
    ) != (
        calibration.annotation_guide_version,
        calibration.annotation_guide_sha256,
        calibration.manifest_id,
        calibration.manifest_sha256,
    ):
        raise ValueError("V2 registry references a foreign calibration manifest")
    if _timestamp_v2(private.attested_at) >= _timestamp_v2(registry.registered_at):
        raise ValueError("private identity attestation was not before registry seal")
    if _timestamp_v2(public.released_at) >= _timestamp_v2(registry.registered_at):
        raise ValueError("public identity release was not before registry seal")
    for completion in registry.calibration_completions:
        assert_calibration_completion_replays_manifest_v2(
            completion=completion,
            calibration_manifest=calibration,
        )
    assert_calibration_disjoint_from_benchmarks_v2(
        calibration_manifest=calibration,
        benchmark_case_universes=(benchmark_cases,),
        split_manifests=(split,),
        leakage_releases=(leakage_release,),
    )
    leakage = _revalidate_v2(leakage_release, LeakageComponentReleaseV3)
    if frozen.split_manifest != split:
        raise ValueError("FrozenCaseReleaseV2 embeds a foreign split")
    if frozen.leakage_release != leakage:
        raise ValueError("FrozenCaseReleaseV2 embeds a foreign leakage release")
    if frozen.expert_registry != registry:
        raise ValueError("FrozenCaseReleaseV2 embeds a foreign expert registry")
    if eligibility.frozen_case_release != frozen:
        raise ValueError("PreRunEligibilityReleaseV2 embeds a foreign case freeze")
    if execution.execution_matrix.split_manifest != split:
        raise ValueError("ExecutionReleaseV2 embeds a foreign split")

    cases = tuple(
        _revalidate_v2(item, FlatBandBenchmarkCaseV1)
        for item in benchmark_cases
    )
    case_keys = tuple((item.case_id, item.case_sha256) for item in cases)
    if len(case_keys) != len(set(case_keys)):
        raise ValueError("benchmark case universe repeats a case identity")
    split_keys = {
        (item.case_id, item.case_sha256) for item in split.cases
    }
    if set(case_keys) != split_keys:
        raise ValueError("benchmark cases do not exactly cover the Pilot split")
    candidates = {item.candidate_id: item for item in frozen.candidates}
    selected_cases: dict[tuple[str, str], FlatBandBenchmarkCaseV1] = {}
    for selection in eligibility.active_selections:
        if selection.selected_candidate_id is None:
            raise ValueError("formal Pilot contains an empty eligible case slot")
        candidate = candidates.get(selection.selected_candidate_id)
        if candidate is None:
            raise ValueError("eligibility selects a foreign frozen candidate")
        key = (candidate.case.case_id, candidate.case.case_sha256)
        if key in selected_cases:
            raise ValueError("eligible selections alias one benchmark case")
        selected_cases[key] = candidate.case
    if set(selected_cases) != split_keys:
        raise ValueError("eligible selections do not exactly cover the Pilot split")
    case_by_key = {(item.case_id, item.case_sha256): item for item in cases}
    if any(case_by_key[key] != value for key, value in selected_cases.items()):
        raise ValueError("benchmark case content differs from the frozen selection")

    assert_pre_run_eligibility_precedes_execution_v2(
        eligibility_release=eligibility,
        execution_release=execution,
    )
    assert_expert_registry_precedes_execution_v2(
        registry=registry,
        execution_releases=(execution,),
    )


def assert_formal_pilot_expert_closure_v3(
    *,
    registry: ExpertStudyRegistryV2,
    split_manifest: BenchmarkSplitManifestV2,
    benchmark_cases: tuple[FlatBandBenchmarkCaseV1, ...],
    private_identity_attestation: PrivateExpertIdentityCustodianAttestationV2,
    public_identity_release: PublicExpertIdentityReleaseV2,
    calibration_manifest: CalibrationSetManifestV2,
    frozen_case_release: object,
    pre_run_eligibility_release: object,
    execution_release: object,
    leakage_release: LeakageComponentReleaseV3,
) -> None:
    """Replay the identity/expert portion of the authoritative V3 Pilot chain."""

    from material_agent.research.flatband_cases import FrozenCaseReleaseV3
    from material_agent.research.flatband_execution import ExecutionReleaseV3

    execution_input = ExecutionReleaseV3.model_validate(execution_release)
    execution = ExecutionReleaseV3.model_validate(
        execution_input.model_dump(mode="python", round_trip=True)
    )
    frozen = execution.frozen_case_release
    eligibility = execution.pre_run_eligibility_release
    if frozen_case_release != frozen:
        raise ValueError("expert closure receives a foreign FrozenCaseReleaseV3")
    if pre_run_eligibility_release != eligibility:
        raise ValueError("expert closure receives a foreign eligibility V3 release")
    if not isinstance(frozen, FrozenCaseReleaseV3):  # pragma: no cover
        raise ValueError("expert closure requires FrozenCaseReleaseV3")

    split = _revalidate_v2(split_manifest, BenchmarkSplitManifestV2)
    if split.manifest_kind not in {
        SplitManifestKind.PILOT_R1,
        SplitManifestKind.PILOT_R2,
    }:
        raise ValueError("formal Pilot expert verifier requires a Pilot split")
    registry_value = _revalidate_v2(registry, ExpertStudyRegistryV2)
    calibration = _revalidate_v2(
        calibration_manifest, CalibrationSetManifestV2
    )
    private = _revalidate_v2(
        private_identity_attestation,
        PrivateExpertIdentityCustodianAttestationV2,
    )
    public = _revalidate_v2(
        public_identity_release, PublicExpertIdentityReleaseV2
    )
    leakage = _revalidate_v2(leakage_release, LeakageComponentReleaseV3)
    if (
        frozen.split_manifest,
        frozen.leakage_release,
        frozen.expert_registry,
    ) != (split, leakage, registry_value):
        raise ValueError("FrozenCaseReleaseV3 changes split/leakage/expert inputs")
    registry_value = frozen.expert_registry

    assert_expert_registry_covers_split_v2(
        registry=registry_value, split_manifest=split
    )
    assert_private_identity_attestation_matches_public_v2(
        private_attestation=private,
        public_release=public,
    )
    assert_distinct_natural_person_assignments_v2(
        private_identity_attestation=private,
        registry=registry_value,
    )
    if (
        registry_value.public_identity_release_id,
        registry_value.public_identity_release_sha256,
        registry_value.identity_attestation_id,
        registry_value.identity_attestation_sha256,
        registry_value.experts,
    ) != (
        public.release_id,
        public.release_sha256,
        public.identity_attestation_id,
        public.identity_attestation_sha256,
        public.experts,
    ):
        raise ValueError("V3 registry changes its public identity release")
    assignment_release = eligibility.assignment_release
    if (
        assignment_release.public_identity_release,
        assignment_release.calibration_manifest,
        assignment_release.calibration_completions,
    ) != (
        public,
        calibration,
        registry_value.calibration_completions,
    ):
        raise ValueError("eligibility roster changes identity or calibration closure")
    if (
        registry_value.annotation_guide_version,
        registry_value.annotation_guide_sha256,
        registry_value.calibration_manifest_id,
        registry_value.calibration_manifest_sha256,
    ) != (
        calibration.annotation_guide_version,
        calibration.annotation_guide_sha256,
        calibration.manifest_id,
        calibration.manifest_sha256,
    ):
        raise ValueError("V3 registry references a foreign calibration manifest")
    for completion in registry_value.calibration_completions:
        assert_calibration_completion_replays_manifest_v2(
            completion=completion,
            calibration_manifest=calibration,
        )
    assert_calibration_disjoint_from_benchmarks_v2(
        calibration_manifest=calibration,
        benchmark_case_universes=(benchmark_cases,),
        split_manifests=(split,),
        leakage_releases=(leakage,),
    )

    bindings = {item.expert_id: item for item in private.bindings}
    for assignment in assignment_release.assignments:
        participant_ids = (*assignment.reviewer_ids, assignment.adjudicator_id)
        participants = tuple(bindings[item] for item in participant_ids)
        if len({item.opaque_natural_person_subject_ref for item in participants}) != 3:
            raise ValueError(
                "eligibility reviewers and adjudicator are not distinct natural persons"
            )
        if len({item.natural_person_commitment_sha256 for item in participants}) != 3:
            raise ValueError(
                "eligibility assignments alias a natural-person commitment"
            )
        if len(
            {
                (
                    item.identity_evidence_artifact_uri,
                    item.identity_evidence_sha256,
                )
                for item in participants
            }
        ) != 3:
            raise ValueError(
                "eligibility assignments alias a private identity evidence record"
            )

    cases = tuple(
        _revalidate_v2(item, FlatBandBenchmarkCaseV1)
        for item in benchmark_cases
    )
    case_by_key = {
        (item.case_id, item.case_sha256): item for item in cases
    }
    if len(case_by_key) != len(cases):
        raise ValueError("benchmark case universe repeats a case identity")
    split_keys = {(item.case_id, item.case_sha256) for item in split.cases}
    if set(case_by_key) != split_keys:
        raise ValueError("benchmark cases do not exactly cover the Pilot split")
    pool = assignment_release.candidate_pool_release
    candidate_by_id = {item.candidate_id: item for item in pool.candidates}
    eligibility_assignment = {
        item.candidate_id: item for item in assignment_release.assignments
    }
    registry_assignment = {
        item.case_id: item for item in registry_value.assignments
    }
    selected_keys: set[tuple[str, str]] = set()
    for selection in eligibility.active_selections:
        if selection.selected_candidate_id is None:
            raise ValueError("formal V3 Pilot contains an empty eligible slot")
        candidate = candidate_by_id[selection.selected_candidate_id]
        key = (candidate.case.case_id, candidate.case.case_sha256)
        if key in selected_keys or case_by_key.get(key) != candidate.case:
            raise ValueError("V3 selected benchmark case aliases or changes content")
        selected_keys.add(key)
        source_assignment = eligibility_assignment[candidate.candidate_id]
        final_assignment = registry_assignment[candidate.case.case_id]
        if (
            final_assignment.reviewer_ids,
            final_assignment.adjudicator_id,
        ) != (
            source_assignment.reviewer_ids,
            source_assignment.adjudicator_id,
        ):
            raise ValueError("final expert registry changes eligibility assessors")
    if selected_keys != split_keys:
        raise ValueError("V3 eligible selections do not exactly cover the split")

    if _timestamp_v2(private.attested_at) >= _timestamp_v2(
        assignment_release.sealed_at
    ) or _timestamp_v2(public.released_at) >= _timestamp_v2(
        assignment_release.sealed_at
    ):
        raise ValueError("identity closure was not sealed before eligibility roster")
    assert_expert_registry_sealed_before_budgets_v2(
        registry=registry_value,
        budget_manifests=execution.budget_manifests,
    )


__all__ = [
    "CalibrationCompletionV1",
    "CalibrationCompletionV2",
    "CalibrationSetManifestV2",
    "CalibrationSourceRecordV2",
    "CaseConflictAssessmentV1",
    "CaseExpertAssignmentV1",
    "ConflictReasonCode",
    "ConflictStatus",
    "ExpertProfileV1",
    "ExpertStudyRegistryV1",
    "ExpertStudyRegistryV2",
    "IdentityAttestationEvidenceLevel",
    "PrivateExpertIdentityCustodianAttestationV2",
    "PrivateNaturalPersonBindingV2",
    "PublicExpertIdentityReleaseV2",
    "PublicExpertIdentityV2",
    "assert_calibration_completion_replays_manifest_v2",
    "assert_calibration_disjoint_from_benchmarks_v2",
    "assert_distinct_natural_person_assignments_v2",
    "assert_legacy_v2_calibration_disjointness_unavailable",
    "assert_expert_registry_covers_split",
    "assert_expert_registry_covers_split_v2",
    "assert_expert_registry_precedes_execution_v2",
    "assert_expert_registry_sealed_before_budgets_v2",
    "assert_formal_pilot_expert_closure_legacy_v2_upstream",
    "assert_formal_pilot_expert_closure_v3",
    "assert_private_identity_attestation_matches_public_v2",
    "build_calibration_completion_v2",
    "build_calibration_set_manifest_v2",
    "build_expert_study_registry_v2",
    "build_private_expert_identity_attestation_v2",
    "build_public_expert_identity_release_v2",
]
