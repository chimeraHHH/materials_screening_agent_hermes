"""Versioned custody contracts for the formal flat-band Main120 study.

These contracts freeze Main inputs and phase permissions.  They do not run a
benchmark, publish expert labels, or reinterpret the Pilot V0 structure
builder's 96-candidate ceiling.  Main structure capacity is represented by a
separately addressed policy and opaque external artifact roots.
"""

from __future__ import annotations

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
from material_agent.research.flatband_analysis import (
    FormalPilotAgreementGateReleaseV1,
    PilotAgreementGateDecision,
)
from material_agent.research.flatband_contracts import (
    SOURCE_CATALOG_V1_SHA256,
    BenchmarkSplit,
    BenchmarkSplitManifestV2,
    FlatBandBenchmarkCaseV1,
    SplitManifestKind,
)
from material_agent.research.flatband_execution import (
    ExecutionPhase,
    ResearchSystemId,
)
from material_agent.research.flatband_experts import (
    ExpertStudyRegistryV2,
    assert_expert_registry_covers_split_v2,
)
from material_agent.research.flatband_leakage import (
    LeakageComponentReleaseV3,
    LeakageRoundClosureContextV3,
    LeakageUnsplitCaseUniverseContextV3,
    MechanismLineageCurationReleaseV3,
    assert_cross_round_leakage_disjoint_v3,
    assert_main_leakage_v3,
    assert_pilot_leakage_v3,
    structure_grouping_case_universe_sha256_v2,
)

ModelT = TypeVar("ModelT", bound=StrictModel)


def _timestamp(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("timestamp must include a UTC offset")
    return parsed


def _revalidate(value: ModelT, model_type: type[ModelT]) -> ModelT:
    return model_type.model_validate(
        model_type.model_validate(value).model_dump(mode="python", round_trip=True)
    )


def _build_addressed(
    model_type: type[ModelT],
    *,
    id_field: str,
    sha_field: str,
    prefix: str,
    values: dict[str, object],
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


def _assert_addressed(
    value: StrictModel,
    *,
    id_field: str,
    sha_field: str,
    prefix: str,
) -> None:
    digest = canonical_sha256(
        value.model_dump(mode="python", exclude={id_field, sha_field})
    )
    if getattr(value, sha_field) != digest or getattr(value, id_field) != deterministic_id(
        prefix, {sha_field: digest}
    ):
        raise ValueError("content-addressed Main identity does not replay")


def _require_sorted_unique(values: tuple[str, ...], label: str) -> None:
    if values != tuple(sorted(set(values))):
        raise ValueError(f"{label} must be sorted and unique")


def _pilot_gate_passes(gate: FormalPilotAgreementGateReleaseV1) -> bool:
    return gate.decision in {
        PilotAgreementGateDecision.R1_PASS_MAIN_ALLOWED,
        PilotAgreementGateDecision.R2_PASS_MAIN_ALLOWED,
    }


class MainSamplingPolicyReleaseV1(StrictModel):
    """Preregistered Main120 quotas frozen before case custody."""

    schema_version: Literal["flatband-main-sampling-policy-release-v1"] = (
        "flatband-main-sampling-policy-release-v1"
    )
    release_id: Identifier
    release_sha256: Sha256
    source_catalog_sha256: Literal[SOURCE_CATALOG_V1_SHA256] = (
        SOURCE_CATALOG_V1_SHA256
    )
    split_seed: Annotated[int, Field(ge=0, le=2**63 - 1)]
    development_case_count: Literal[60] = 60
    locked_iid_case_count: Literal[30] = 30
    locked_ood_case_count: Literal[30] = 30
    development_minimum_component_count: Literal[20] = 20
    locked_iid_minimum_component_count: Literal[10] = 10
    locked_ood_minimum_component_count: Literal[10] = 10
    target_balance_per_split_required: Literal[True] = True
    dimensionality_balance_per_split_required: Literal[True] = True
    full_axis_pilot_main_disjointness_required: Literal[True] = True
    frozen_ood_holdout_family_required: Literal[True] = True
    component_may_cross_split: Literal[False] = False
    frozen_at: Annotated[str, Field(min_length=20, max_length=40)]
    scientific_conclusion: Literal[False] = False

    @field_validator("frozen_at")
    @classmethod
    def validate_time(cls, value: str) -> str:
        _timestamp(value)
        return value

    @model_validator(mode="after")
    def validate_release(self) -> MainSamplingPolicyReleaseV1:
        _assert_addressed(
            self,
            id_field="release_id",
            sha_field="release_sha256",
            prefix="main-sampling-policy-v1",
        )
        return self


def build_main_sampling_policy_release_v1(
    *, split_seed: int, frozen_at: str
) -> MainSamplingPolicyReleaseV1:
    return _build_addressed(
        MainSamplingPolicyReleaseV1,
        id_field="release_id",
        sha_field="release_sha256",
        prefix="main-sampling-policy-v1",
        values={"split_seed": split_seed, "frozen_at": frozen_at},
    )


def assert_main_sampling_policy_exact_v1(
    release: MainSamplingPolicyReleaseV1,
) -> None:
    value = _revalidate(release, MainSamplingPolicyReleaseV1)
    rebuilt = build_main_sampling_policy_release_v1(
        split_seed=value.split_seed, frozen_at=value.frozen_at
    )
    if rebuilt != value:
        raise ValueError("Main sampling policy does not replay exactly")


class MainCapacityPolicyV1(StrictModel):
    """Main-only capacity decision; never aliases Pilot V0's 96-case cap."""

    schema_version: Literal["flatband-main-capacity-policy-v1"] = (
        "flatband-main-capacity-policy-v1"
    )
    policy_id: Identifier
    policy_sha256: Sha256
    main_case_count: Literal[120] = 120
    authorized_union_candidate_count: Annotated[int, Field(ge=120, le=2_048)]
    authorized_union_member_count: Annotated[int, Field(ge=3, le=32)]
    complete_pair_count: Annotated[int, Field(ge=7_140)]
    verifier_implementation_sha256: Sha256
    verifier_runtime_environment_sha256: Sha256
    frozen_at: Annotated[str, Field(min_length=20, max_length=40)]
    pilot_v0_candidate_cap: Literal[96] = 96
    pilot_v0_capacity_reuse_allowed: Literal[False] = False
    separately_addressed_main_union_release_required: Literal[True] = True
    performance_or_benchmark_result_claimed: Literal[False] = False
    scientific_conclusion: Literal[False] = False

    @field_validator("frozen_at")
    @classmethod
    def validate_time(cls, value: str) -> str:
        _timestamp(value)
        return value

    @model_validator(mode="after")
    def validate_policy(self) -> MainCapacityPolicyV1:
        expected_pairs = (
            self.authorized_union_candidate_count
            * (self.authorized_union_candidate_count - 1)
            // 2
        )
        if self.complete_pair_count != expected_pairs:
            raise ValueError("Main capacity pair count does not cover the full union")
        if self.authorized_union_candidate_count <= self.pilot_v0_candidate_cap:
            raise ValueError("Main capacity cannot alias the Pilot V0 ceiling")
        _assert_addressed(
            self,
            id_field="policy_id",
            sha_field="policy_sha256",
            prefix="main-capacity-policy-v1",
        )
        return self


def build_main_capacity_policy_v1(
    *,
    authorized_union_candidate_count: int,
    authorized_union_member_count: int,
    verifier_implementation_sha256: str,
    verifier_runtime_environment_sha256: str,
    frozen_at: str,
) -> MainCapacityPolicyV1:
    return _build_addressed(
        MainCapacityPolicyV1,
        id_field="policy_id",
        sha_field="policy_sha256",
        prefix="main-capacity-policy-v1",
        values={
            "authorized_union_candidate_count": authorized_union_candidate_count,
            "authorized_union_member_count": authorized_union_member_count,
            "complete_pair_count": (
                authorized_union_candidate_count
                * (authorized_union_candidate_count - 1)
                // 2
            ),
            "verifier_implementation_sha256": verifier_implementation_sha256,
            "verifier_runtime_environment_sha256": (
                verifier_runtime_environment_sha256
            ),
            "frozen_at": frozen_at,
        },
    )


def assert_main_capacity_policy_exact_v1(policy: MainCapacityPolicyV1) -> None:
    value = _revalidate(policy, MainCapacityPolicyV1)
    rebuilt = build_main_capacity_policy_v1(
        authorized_union_candidate_count=value.authorized_union_candidate_count,
        authorized_union_member_count=value.authorized_union_member_count,
        verifier_implementation_sha256=value.verifier_implementation_sha256,
        verifier_runtime_environment_sha256=(
            value.verifier_runtime_environment_sha256
        ),
        frozen_at=value.frozen_at,
    )
    if rebuilt != value:
        raise ValueError("Main capacity policy does not replay exactly")


class MainStructureUnionOwnerV1(StrEnum):
    CALIBRATION = "CALIBRATION"
    PILOT_R1 = "PILOT_R1"
    PILOT_R2 = "PILOT_R2"
    MAIN_FULL_POOL = "MAIN_FULL_POOL"


class MainStructureCaseRefV1(StrictModel):
    case_id: Identifier
    case_sha256: Sha256
    structure_sha256: Sha256


class MainStructureUnionMemberV1(StrictModel):
    owner: MainStructureUnionOwnerV1
    source_artifact_id: Identifier
    source_artifact_sha256: Sha256
    case_universe_sha256: Sha256
    cases: Annotated[
        tuple[MainStructureCaseRefV1, ...], Field(min_length=1, max_length=2_048)
    ]

    @model_validator(mode="after")
    def validate_member(self) -> MainStructureUnionMemberV1:
        keys = tuple(item.case_id for item in self.cases)
        _require_sorted_unique(keys, "Main structure-union member cases")
        if self.case_universe_sha256 != canonical_sha256(
            tuple(
                (item.case_id, item.case_sha256, item.structure_sha256)
                for item in self.cases
            )
        ):
            raise ValueError("Main structure-union member root does not replay")
        return self


class MainStructureUnionVerifierAttestationV1(StrictModel):
    """Typed result of the separately versioned Main structure verifier."""

    schema_version: Literal[
        "flatband-main-structure-union-verifier-attestation-v1"
    ] = "flatband-main-structure-union-verifier-attestation-v1"
    attestation_id: Identifier
    attestation_sha256: Sha256
    verifier_implementation_sha256: Sha256
    verifier_runtime_environment_sha256: Sha256
    private_verifier_evidence_artifact_id: Identifier
    private_verifier_evidence_artifact_sha256: Sha256
    input_root_sha256: Sha256
    output_root_sha256: Sha256
    complete_pair_count: Annotated[int, Field(ge=7_140)]
    violation_count: Literal[0] = 0
    verified_at: Annotated[str, Field(min_length=20, max_length=40)]
    external_execution_attestation: Literal["NOT_PROVIDED"] = "NOT_PROVIDED"
    exact_scientific_replay_by_registered_verifier_required: Literal[True] = True
    caller_boolean_is_evidence: Literal[False] = False
    scientific_conclusion: Literal[False] = False

    @field_validator("verified_at")
    @classmethod
    def validate_time(cls, value: str) -> str:
        _timestamp(value)
        return value

    @model_validator(mode="after")
    def validate_attestation(self) -> MainStructureUnionVerifierAttestationV1:
        _assert_addressed(
            self,
            id_field="attestation_id",
            sha_field="attestation_sha256",
            prefix="main-structure-attest-v1",
        )
        return self


class MainStructureUnionReleaseV1(StrictModel):
    schema_version: Literal["flatband-main-structure-union-release-v1"] = (
        "flatband-main-structure-union-release-v1"
    )
    release_id: Identifier
    release_sha256: Sha256
    capacity_policy: MainCapacityPolicyV1
    members: Annotated[
        tuple[MainStructureUnionMemberV1, ...], Field(min_length=3, max_length=32)
    ]
    candidate_count: Annotated[int, Field(ge=120, le=2_048)]
    input_root_sha256: Sha256
    output_root_sha256: Sha256
    verifier_attestation: MainStructureUnionVerifierAttestationV1
    created_at: Annotated[str, Field(min_length=20, max_length=40)]
    private_custody_required: Literal[True] = True
    external_provider_attestation_provided: Literal[False] = False
    scientific_output_replayed_by_this_contract_module: Literal[False] = False
    scientific_conclusion: Literal[False] = False

    @field_validator("created_at")
    @classmethod
    def validate_time(cls, value: str) -> str:
        _timestamp(value)
        return value

    @model_validator(mode="after")
    def validate_release(self) -> MainStructureUnionReleaseV1:
        capacity = _revalidate(self.capacity_policy, MainCapacityPolicyV1)
        members = tuple(
            _revalidate(item, MainStructureUnionMemberV1) for item in self.members
        )
        owner_order = tuple(item.owner for item in members)
        expected_prefix = (
            MainStructureUnionOwnerV1.CALIBRATION,
            MainStructureUnionOwnerV1.PILOT_R1,
        )
        if owner_order[:2] != expected_prefix or owner_order[-1] is not (
            MainStructureUnionOwnerV1.MAIN_FULL_POOL
        ):
            raise ValueError("Main structure-union owners are not canonically ordered")
        if owner_order not in {
            (
                MainStructureUnionOwnerV1.CALIBRATION,
                MainStructureUnionOwnerV1.PILOT_R1,
                MainStructureUnionOwnerV1.MAIN_FULL_POOL,
            ),
            (
                MainStructureUnionOwnerV1.CALIBRATION,
                MainStructureUnionOwnerV1.PILOT_R1,
                MainStructureUnionOwnerV1.PILOT_R2,
                MainStructureUnionOwnerV1.MAIN_FULL_POOL,
            ),
        }:
            raise ValueError("Main structure-union owner set is invalid")
        case_keys = tuple(
            (item.case_id, item.case_sha256)
            for member in members
            for item in member.cases
        )
        if len(case_keys) != len(set(case_keys)):
            raise ValueError("Main structure-union owners overlap or alias cases")
        if self.candidate_count != len(case_keys):
            raise ValueError("Main structure-union candidate count does not replay")
        if (
            self.candidate_count,
            len(members),
        ) != (
            capacity.authorized_union_candidate_count,
            capacity.authorized_union_member_count,
        ):
            raise ValueError("Main structure union exceeds or underfills capacity")
        expected_input = canonical_sha256(members)
        if self.input_root_sha256 != expected_input:
            raise ValueError("Main structure-union input root does not replay")
        attestation = _revalidate(
            self.verifier_attestation,
            MainStructureUnionVerifierAttestationV1,
        )
        if (
            attestation.verifier_implementation_sha256,
            attestation.verifier_runtime_environment_sha256,
            attestation.input_root_sha256,
            attestation.output_root_sha256,
            attestation.complete_pair_count,
        ) != (
            capacity.verifier_implementation_sha256,
            capacity.verifier_runtime_environment_sha256,
            self.input_root_sha256,
            self.output_root_sha256,
            capacity.complete_pair_count,
        ):
            raise ValueError("Main structure-union attestation drifts from policy/release")
        if not (
            _timestamp(capacity.frozen_at)
            < _timestamp(attestation.verified_at)
            <= _timestamp(self.created_at)
        ):
            raise ValueError("Main structure-union verification chronology is invalid")
        _assert_addressed(
            self,
            id_field="release_id",
            sha_field="release_sha256",
            prefix="main-structure-union-release-v1",
        )
        return self


def _main_structure_member_v1(
    owner: MainStructureUnionOwnerV1,
    *,
    source_artifact_id: str,
    source_artifact_sha256: str,
    cases: tuple[FlatBandBenchmarkCaseV1, ...],
) -> MainStructureUnionMemberV1:
    refs = tuple(
        MainStructureCaseRefV1(
            case_id=item.case_id,
            case_sha256=item.case_sha256,
            structure_sha256=item.structure_sha256,
        )
        for item in sorted(cases, key=lambda item: item.case_id)
    )
    return MainStructureUnionMemberV1(
        owner=owner,
        source_artifact_id=source_artifact_id,
        source_artifact_sha256=source_artifact_sha256,
        case_universe_sha256=canonical_sha256(
            tuple(
                (item.case_id, item.case_sha256, item.structure_sha256)
                for item in refs
            )
        ),
        cases=refs,
    )


def build_main_structure_union_release_v1(
    *,
    capacity_policy: MainCapacityPolicyV1,
    calibration_context: LeakageUnsplitCaseUniverseContextV3,
    pilot_contexts: tuple[LeakageRoundClosureContextV3, ...],
    main_context: LeakageRoundClosureContextV3,
    private_verifier_evidence_artifact_id: str,
    private_verifier_evidence_artifact_sha256: str,
    output_root_sha256: str,
    verified_at: str,
    created_at: str,
) -> MainStructureUnionReleaseV1:
    capacity = _revalidate(capacity_policy, MainCapacityPolicyV1)
    calibration = _revalidate(
        calibration_context, LeakageUnsplitCaseUniverseContextV3
    )
    pilots = tuple(
        _revalidate(item, LeakageRoundClosureContextV3) for item in pilot_contexts
    )
    main = _revalidate(main_context, LeakageRoundClosureContextV3)
    if tuple(item.split_manifest.manifest_kind for item in pilots) not in {
        (SplitManifestKind.PILOT_R1,),
        (SplitManifestKind.PILOT_R1, SplitManifestKind.PILOT_R2),
    }:
        raise ValueError("Main structure union requires terminal ordered Pilot contexts")
    members = [
        _main_structure_member_v1(
            MainStructureUnionOwnerV1.CALIBRATION,
            source_artifact_id=calibration.source_artifact_id,
            source_artifact_sha256=calibration.source_artifact_sha256,
            cases=calibration.cases,
        )
    ]
    for index, context in enumerate(pilots, start=1):
        members.append(
            _main_structure_member_v1(
                (
                    MainStructureUnionOwnerV1.PILOT_R1
                    if index == 1
                    else MainStructureUnionOwnerV1.PILOT_R2
                ),
                source_artifact_id=context.release.release_id,
                source_artifact_sha256=context.release.release_sha256,
                cases=context.cases,
            )
        )
    members.append(
        _main_structure_member_v1(
            MainStructureUnionOwnerV1.MAIN_FULL_POOL,
            source_artifact_id=main.release.release_id,
            source_artifact_sha256=main.release.release_sha256,
            cases=main.cases,
        )
    )
    member_values = tuple(members)
    input_root = canonical_sha256(member_values)
    attestation = _build_addressed(
        MainStructureUnionVerifierAttestationV1,
        id_field="attestation_id",
        sha_field="attestation_sha256",
        prefix="main-structure-attest-v1",
        values={
            "verifier_implementation_sha256": (
                capacity.verifier_implementation_sha256
            ),
            "verifier_runtime_environment_sha256": (
                capacity.verifier_runtime_environment_sha256
            ),
            "private_verifier_evidence_artifact_id": (
                private_verifier_evidence_artifact_id
            ),
            "private_verifier_evidence_artifact_sha256": (
                private_verifier_evidence_artifact_sha256
            ),
            "input_root_sha256": input_root,
            "output_root_sha256": output_root_sha256,
            "complete_pair_count": capacity.complete_pair_count,
            "verified_at": verified_at,
        },
    )
    return _build_addressed(
        MainStructureUnionReleaseV1,
        id_field="release_id",
        sha_field="release_sha256",
        prefix="main-structure-union-release-v1",
        values={
            "capacity_policy": capacity,
            "members": member_values,
            "candidate_count": sum(len(item.cases) for item in member_values),
            "input_root_sha256": input_root,
            "output_root_sha256": output_root_sha256,
            "verifier_attestation": attestation,
            "created_at": created_at,
        },
    )


def assert_main_structure_union_release_exact_v1(
    release: MainStructureUnionReleaseV1,
    *,
    calibration_context: LeakageUnsplitCaseUniverseContextV3,
    pilot_contexts: tuple[LeakageRoundClosureContextV3, ...],
    main_context: LeakageRoundClosureContextV3,
) -> None:
    value = _revalidate(release, MainStructureUnionReleaseV1)
    rebuilt = build_main_structure_union_release_v1(
        capacity_policy=value.capacity_policy,
        calibration_context=calibration_context,
        pilot_contexts=pilot_contexts,
        main_context=main_context,
        private_verifier_evidence_artifact_id=(
            value.verifier_attestation.private_verifier_evidence_artifact_id
        ),
        private_verifier_evidence_artifact_sha256=(
            value.verifier_attestation.private_verifier_evidence_artifact_sha256
        ),
        output_root_sha256=value.output_root_sha256,
        verified_at=value.verifier_attestation.verified_at,
        created_at=value.created_at,
    )
    if rebuilt != value:
        raise ValueError("Main structure-union release does not replay exactly")


class MainCandidatePoolReleaseV1(StrictModel):
    """Exact 120-case Main custody root created only after a passing Pilot Gate."""

    schema_version: Literal["flatband-main-candidate-pool-release-v1"] = (
        "flatband-main-candidate-pool-release-v1"
    )
    release_id: Identifier
    release_sha256: Sha256
    source_catalog_sha256: Literal[SOURCE_CATALOG_V1_SHA256] = (
        SOURCE_CATALOG_V1_SHA256
    )
    split_seed: Annotated[int, Field(ge=0, le=2**63 - 1)]
    sampling_policy_release: MainSamplingPolicyReleaseV1
    terminal_pilot_gate: FormalPilotAgreementGateReleaseV1
    lineage_curation_release: MechanismLineageCurationReleaseV3
    cases: Annotated[
        tuple[FlatBandBenchmarkCaseV1, ...], Field(min_length=120, max_length=120)
    ]
    case_universe_sha256: Sha256
    private_structure_artifact_id: Identifier
    private_structure_artifact_sha256: Sha256
    sealed_at: Annotated[str, Field(min_length=20, max_length=40)]
    exact_case_count: Literal[120] = 120
    replacements_allowed: Literal[False] = False
    post_hoc_cases_allowed: Literal[False] = False
    pilot_candidate_pool_type_reused: Literal[False] = False
    private_custody_required: Literal[True] = True
    public_release_allowed: Literal[False] = False
    scientific_conclusion: Literal[False] = False

    @field_validator("sealed_at")
    @classmethod
    def validate_time(cls, value: str) -> str:
        _timestamp(value)
        return value

    @model_validator(mode="after")
    def validate_release(self) -> MainCandidatePoolReleaseV1:
        gate = _revalidate(
            self.terminal_pilot_gate, FormalPilotAgreementGateReleaseV1
        )
        sampling = _revalidate(
            self.sampling_policy_release, MainSamplingPolicyReleaseV1
        )
        if not _pilot_gate_passes(gate):
            raise ValueError("Main candidate custody requires an R1/R2 PASS Pilot Gate")
        if (
            gate.lineage_curation_release_id,
            gate.lineage_curation_release_sha256,
        ) != (
            self.lineage_curation_release.release_id,
            self.lineage_curation_release.release_sha256,
        ):
            raise ValueError("Main candidate custody receives a foreign Pilot Gate")
        if (
            self.split_seed,
            self.source_catalog_sha256,
        ) != (sampling.split_seed, sampling.source_catalog_sha256):
            raise ValueError("Main candidate custody differs from sampling policy")
        cases = tuple(_revalidate(item, FlatBandBenchmarkCaseV1) for item in self.cases)
        if len(cases) != 120:
            raise ValueError("Main candidate custody requires exactly 120 cases")
        case_ids = tuple(item.case_id for item in cases)
        case_shas = tuple(item.case_sha256 for item in cases)
        _require_sorted_unique(case_ids, "Main candidate case IDs")
        if len(case_shas) != len(set(case_shas)):
            raise ValueError("Main candidate case SHA-256 identities must be unique")
        if any(item.source_catalog_sha256 != self.source_catalog_sha256 for item in cases):
            raise ValueError("Main candidate uses a foreign source catalog")
        expected_universe = structure_grouping_case_universe_sha256_v2(cases)
        if self.case_universe_sha256 != expected_universe:
            raise ValueError("Main candidate universe SHA-256 does not replay")
        if _timestamp(gate.evaluated_at) >= _timestamp(self.sealed_at):
            raise ValueError("Main candidate pool was not sealed after Pilot PASS")
        if _timestamp(sampling.frozen_at) >= _timestamp(gate.evaluated_at):
            raise ValueError("Main sampling policy was not frozen before Pilot PASS")
        _assert_addressed(
            self,
            id_field="release_id",
            sha_field="release_sha256",
            prefix="main-candidate-pool-v1",
        )
        return self


def build_main_candidate_pool_release_v1(
    *,
    sampling_policy_release: MainSamplingPolicyReleaseV1,
    terminal_pilot_gate: FormalPilotAgreementGateReleaseV1,
    lineage_curation_release: MechanismLineageCurationReleaseV3,
    cases: tuple[FlatBandBenchmarkCaseV1, ...],
    private_structure_artifact_id: str,
    private_structure_artifact_sha256: str,
    sealed_at: str,
) -> MainCandidatePoolReleaseV1:
    sampling = _revalidate(
        sampling_policy_release, MainSamplingPolicyReleaseV1
    )
    ordered_cases = tuple(
        sorted(
            (_revalidate(item, FlatBandBenchmarkCaseV1) for item in cases),
            key=lambda item: item.case_id,
        )
    )
    return _build_addressed(
        MainCandidatePoolReleaseV1,
        id_field="release_id",
        sha_field="release_sha256",
        prefix="main-candidate-pool-v1",
        values={
            "split_seed": sampling.split_seed,
            "sampling_policy_release": sampling,
            "terminal_pilot_gate": _revalidate(
                terminal_pilot_gate, FormalPilotAgreementGateReleaseV1
            ),
            "lineage_curation_release": _revalidate(
                lineage_curation_release, MechanismLineageCurationReleaseV3
            ),
            "cases": ordered_cases,
            "case_universe_sha256": structure_grouping_case_universe_sha256_v2(
                ordered_cases
            ),
            "private_structure_artifact_id": private_structure_artifact_id,
            "private_structure_artifact_sha256": private_structure_artifact_sha256,
            "sealed_at": sealed_at,
        },
    )


def assert_main_candidate_pool_exact_v1(
    release: MainCandidatePoolReleaseV1,
) -> None:
    value = _revalidate(release, MainCandidatePoolReleaseV1)
    rebuilt = build_main_candidate_pool_release_v1(
        sampling_policy_release=value.sampling_policy_release,
        terminal_pilot_gate=value.terminal_pilot_gate,
        lineage_curation_release=value.lineage_curation_release,
        cases=value.cases,
        private_structure_artifact_id=value.private_structure_artifact_id,
        private_structure_artifact_sha256=value.private_structure_artifact_sha256,
        sealed_at=value.sealed_at,
    )
    if rebuilt != value:
        raise ValueError("Main candidate pool does not replay exactly")


class MainEligibilityStatusV1(StrEnum):
    ELIGIBLE = "ELIGIBLE"
    EXCLUDE = "EXCLUDE"
    UNCERTAIN = "UNCERTAIN"


class MainEligibilityEvidenceRefV1(StrictModel):
    source_id: Identifier
    source_record_id: Identifier
    source_record_raw_sha256: Sha256
    artifact_id: Identifier
    artifact_sha256: Sha256


class MainEligibilityArtifactRefV1(StrictModel):
    artifact_id: Identifier
    artifact_sha256: Sha256


class MainEligibilityRawAuditV1(StrictModel):
    schema_version: Literal["flatband-main-eligibility-raw-audit-v1"] = (
        "flatband-main-eligibility-raw-audit-v1"
    )
    audit_id: Identifier
    audit_sha256: Sha256
    case_id: Identifier
    case_sha256: Sha256
    auditor_id: Identifier
    auditor_natural_person_commitment_sha256: Sha256
    evidence_refs: Annotated[
        tuple[MainEligibilityEvidenceRefV1, ...], Field(min_length=1, max_length=16)
    ]
    status: MainEligibilityStatusV1
    audited_at: Annotated[str, Field(min_length=20, max_length=40)]
    independent_pre_discussion_audit: Literal[True] = True
    scientific_conclusion: Literal[False] = False

    @field_validator("audited_at")
    @classmethod
    def validate_time(cls, value: str) -> str:
        _timestamp(value)
        return value

    @model_validator(mode="after")
    def validate_audit(self) -> MainEligibilityRawAuditV1:
        if not self.evidence_refs:
            raise ValueError("Main eligibility raw audit requires source evidence")
        keys = tuple(
            (
                item.source_id,
                item.source_record_id,
                item.source_record_raw_sha256,
                item.artifact_id,
                item.artifact_sha256,
            )
            for item in self.evidence_refs
        )
        if keys != tuple(sorted(set(keys))):
            raise ValueError("Main eligibility evidence refs must be sorted and unique")
        _assert_addressed(
            self,
            id_field="audit_id",
            sha_field="audit_sha256",
            prefix="main-eligibility-raw-audit-v1",
        )
        return self


class MainEligibilityAdjudicationV1(StrictModel):
    schema_version: Literal["flatband-main-eligibility-adjudication-v1"] = (
        "flatband-main-eligibility-adjudication-v1"
    )
    adjudication_id: Identifier
    adjudication_sha256: Sha256
    case_id: Identifier
    case_sha256: Sha256
    raw_audit_refs: tuple[
        MainEligibilityArtifactRefV1, MainEligibilityArtifactRefV1
    ]
    raw_statuses: tuple[MainEligibilityStatusV1, MainEligibilityStatusV1]
    adjudicator_id: Identifier
    adjudicator_natural_person_commitment_sha256: Sha256
    final_status: MainEligibilityStatusV1
    rationale_sha256: Sha256
    adjudicated_at: Annotated[str, Field(min_length=20, max_length=40)]
    scientific_conclusion: Literal[False] = False

    @field_validator("adjudicated_at")
    @classmethod
    def validate_time(cls, value: str) -> str:
        _timestamp(value)
        return value

    @model_validator(mode="after")
    def validate_adjudication(self) -> MainEligibilityAdjudicationV1:
        if self.raw_statuses[0] is self.raw_statuses[1]:
            raise ValueError("consensus Main eligibility audits cannot be adjudicated")
        keys = tuple((item.artifact_id, item.artifact_sha256) for item in self.raw_audit_refs)
        if keys != tuple(sorted(set(keys))):
            raise ValueError("Main eligibility adjudication requires two sorted raw refs")
        _assert_addressed(
            self,
            id_field="adjudication_id",
            sha_field="adjudication_sha256",
            prefix="main-eligibility-adjudication-v1",
        )
        return self


class MainEligibilityDecisionV1(StrictModel):
    decision_id: Identifier
    decision_sha256: Sha256
    case_id: Identifier
    case_sha256: Sha256
    raw_audit_refs: tuple[
        MainEligibilityArtifactRefV1, MainEligibilityArtifactRefV1
    ]
    adjudication_ref: MainEligibilityArtifactRefV1 | None = None
    final_status: MainEligibilityStatusV1
    decided_at: Annotated[str, Field(min_length=20, max_length=40)]
    scientific_conclusion: Literal[False] = False

    @field_validator("decided_at")
    @classmethod
    def validate_time(cls, value: str) -> str:
        _timestamp(value)
        return value

    @model_validator(mode="after")
    def validate_decision(self) -> MainEligibilityDecisionV1:
        _assert_addressed(
            self,
            id_field="decision_id",
            sha_field="decision_sha256",
            prefix="main-eligibility-decision-v1",
        )
        return self


def build_main_eligibility_raw_audit_v1(
    *,
    case: FlatBandBenchmarkCaseV1,
    auditor_id: str,
    auditor_natural_person_commitment_sha256: str,
    evidence_refs: tuple[MainEligibilityEvidenceRefV1, ...],
    status: MainEligibilityStatusV1,
    audited_at: str,
) -> MainEligibilityRawAuditV1:
    full_case = _revalidate(case, FlatBandBenchmarkCaseV1)
    ordered_refs = tuple(
        sorted(
            evidence_refs,
            key=lambda item: (
                item.source_id,
                item.source_record_id,
                item.source_record_raw_sha256,
                item.artifact_id,
                item.artifact_sha256,
            ),
        )
    )
    return _build_addressed(
        MainEligibilityRawAuditV1,
        id_field="audit_id",
        sha_field="audit_sha256",
        prefix="main-eligibility-raw-audit-v1",
        values={
            "case_id": full_case.case_id,
            "case_sha256": full_case.case_sha256,
            "auditor_id": auditor_id,
            "auditor_natural_person_commitment_sha256": (
                auditor_natural_person_commitment_sha256
            ),
            "evidence_refs": ordered_refs,
            "status": status,
            "audited_at": audited_at,
        },
    )


def build_main_eligibility_adjudication_v1(
    *,
    raw_audits: tuple[MainEligibilityRawAuditV1, MainEligibilityRawAuditV1],
    adjudicator_id: str,
    adjudicator_natural_person_commitment_sha256: str,
    final_status: MainEligibilityStatusV1,
    rationale_sha256: str,
    adjudicated_at: str,
) -> MainEligibilityAdjudicationV1:
    audits = tuple(
        sorted(
            (_revalidate(item, MainEligibilityRawAuditV1) for item in raw_audits),
            key=lambda item: (item.auditor_id, item.audit_id),
        )
    )
    if len({(item.case_id, item.case_sha256) for item in audits}) != 1:
        raise ValueError("Main eligibility adjudication crosswires cases")
    if len({item.auditor_id for item in audits}) != 2 or len(
        {item.auditor_natural_person_commitment_sha256 for item in audits}
    ) != 2:
        raise ValueError("Main eligibility requires two independent raw auditors")
    if adjudicator_id in {item.auditor_id for item in audits} or (
        adjudicator_natural_person_commitment_sha256
        in {item.auditor_natural_person_commitment_sha256 for item in audits}
    ):
        raise ValueError("Main eligibility adjudicator must be a distinct natural person")
    latest_audit = max(_timestamp(item.audited_at) for item in audits)
    if latest_audit >= _timestamp(adjudicated_at):
        raise ValueError("Main eligibility adjudication does not follow both audits")
    return _build_addressed(
        MainEligibilityAdjudicationV1,
        id_field="adjudication_id",
        sha_field="adjudication_sha256",
        prefix="main-eligibility-adjudication-v1",
        values={
            "case_id": audits[0].case_id,
            "case_sha256": audits[0].case_sha256,
            "raw_audit_refs": tuple(
                MainEligibilityArtifactRefV1(
                    artifact_id=item.audit_id,
                    artifact_sha256=item.audit_sha256,
                )
                for item in audits
            ),
            "raw_statuses": tuple(item.status for item in audits),
            "adjudicator_id": adjudicator_id,
            "adjudicator_natural_person_commitment_sha256": (
                adjudicator_natural_person_commitment_sha256
            ),
            "final_status": final_status,
            "rationale_sha256": rationale_sha256,
            "adjudicated_at": adjudicated_at,
        },
    )


def _derive_main_eligibility_decisions_v1(
    *,
    pool: MainCandidatePoolReleaseV1,
    raw_audits: tuple[MainEligibilityRawAuditV1, ...],
    adjudications: tuple[MainEligibilityAdjudicationV1, ...],
    decided_at: str,
) -> tuple[MainEligibilityDecisionV1, ...]:
    cases = {item.case_id: item for item in pool.cases}
    audits_by_case: dict[str, list[MainEligibilityRawAuditV1]] = {}
    for raw in raw_audits:
        audit = _revalidate(raw, MainEligibilityRawAuditV1)
        case = cases.get(audit.case_id)
        if case is None or case.case_sha256 != audit.case_sha256:
            raise ValueError("Main eligibility raw audit binds a foreign case")
        case_sources = {
            (item.source_id, item.source_record_id, item.raw_sha256)
            for item in case.source_records
            if item.raw_sha256 is not None
        }
        evidence_sources = {
            (
                item.source_id,
                item.source_record_id,
                item.source_record_raw_sha256,
            )
            for item in audit.evidence_refs
        }
        if not evidence_sources or not evidence_sources <= case_sources:
            raise ValueError(
                "Main eligibility evidence is not an exact subset of case sources"
            )
        audits_by_case.setdefault(audit.case_id, []).append(audit)
    if set(audits_by_case) != set(cases):
        raise ValueError("Main eligibility raw audits do not exactly cover all cases")
    adjudication_by_case = {
        item.case_id: _revalidate(item, MainEligibilityAdjudicationV1)
        for item in adjudications
    }
    if len(adjudication_by_case) != len(adjudications):
        raise ValueError("duplicate Main eligibility adjudication")
    decisions: list[MainEligibilityDecisionV1] = []
    for case_id in sorted(cases):
        audits = tuple(sorted(audits_by_case[case_id], key=lambda item: item.auditor_id))
        if len(audits) != 2 or len({item.auditor_id for item in audits}) != 2 or len(
            {item.auditor_natural_person_commitment_sha256 for item in audits}
        ) != 2:
            raise ValueError("each Main case requires exactly two independent raw auditors")
        if any(_timestamp(item.audited_at) <= _timestamp(pool.sealed_at) for item in audits):
            raise ValueError("Main eligibility audit does not follow candidate custody")
        statuses = {item.status for item in audits}
        adjudication = adjudication_by_case.get(case_id)
        if len(statuses) == 1:
            if adjudication is not None:
                raise ValueError("consensus Main eligibility audits cannot be adjudicated")
            final = audits[0].status
            adjudication_ref = None
            latest_decision_input = max(_timestamp(item.audited_at) for item in audits)
        else:
            if adjudication is None:
                raise ValueError("Main eligibility disagreement lacks adjudication")
            expected_refs = tuple(
                MainEligibilityArtifactRefV1(
                    artifact_id=item.audit_id, artifact_sha256=item.audit_sha256
                )
                for item in audits
            )
            if (
                adjudication.case_sha256,
                adjudication.raw_audit_refs,
                set(adjudication.raw_statuses),
            ) != (cases[case_id].case_sha256, expected_refs, statuses):
                raise ValueError("Main eligibility adjudication binds foreign raw audits")
            if adjudication.adjudicator_id in {item.auditor_id for item in audits} or (
                adjudication.adjudicator_natural_person_commitment_sha256
                in {
                    item.auditor_natural_person_commitment_sha256
                    for item in audits
                }
            ):
                raise ValueError(
                    "Main eligibility adjudicator is not a distinct natural person"
                )
            if _timestamp(adjudication.adjudicated_at) <= max(
                _timestamp(item.audited_at) for item in audits
            ):
                raise ValueError(
                    "Main eligibility adjudication does not follow both raw audits"
                )
            final = adjudication.final_status
            adjudication_ref = MainEligibilityArtifactRefV1(
                artifact_id=adjudication.adjudication_id,
                artifact_sha256=adjudication.adjudication_sha256,
            )
            latest_decision_input = _timestamp(adjudication.adjudicated_at)
        if latest_decision_input > _timestamp(decided_at):
            raise ValueError("Main eligibility release predates a decision input")
        refs = tuple(
            MainEligibilityArtifactRefV1(
                artifact_id=item.audit_id, artifact_sha256=item.audit_sha256
            )
            for item in audits
        )
        decisions.append(
            _build_addressed(
                MainEligibilityDecisionV1,
                id_field="decision_id",
                sha_field="decision_sha256",
                prefix="main-eligibility-decision-v1",
                values={
                    "case_id": case_id,
                    "case_sha256": cases[case_id].case_sha256,
                    "raw_audit_refs": refs,
                    "adjudication_ref": adjudication_ref,
                    "final_status": final,
                    "decided_at": decided_at,
                },
            )
        )
    if set(adjudication_by_case) != {
        item.case_id for item in decisions if item.adjudication_ref is not None
    }:
        raise ValueError("orphan Main eligibility adjudication")
    return tuple(decisions)


class MainEligibilityReleaseV1(StrictModel):
    """Exact two-auditor/adjudication closure over all 120 Main cases."""

    schema_version: Literal["flatband-main-eligibility-release-v1"] = (
        "flatband-main-eligibility-release-v1"
    )
    release_id: Identifier
    release_sha256: Sha256
    candidate_pool_release: MainCandidatePoolReleaseV1
    eligibility_policy_sha256: Sha256
    raw_audits: Annotated[
        tuple[MainEligibilityRawAuditV1, ...], Field(min_length=240, max_length=240)
    ]
    adjudications: Annotated[
        tuple[MainEligibilityAdjudicationV1, ...], Field(max_length=120)
    ] = ()
    decisions: Annotated[
        tuple[MainEligibilityDecisionV1, ...], Field(min_length=120, max_length=120)
    ]
    eligible_case_ids: Annotated[tuple[Identifier, ...], Field(max_length=120)]
    eligible_case_universe_sha256: Sha256
    sealed_at: Annotated[str, Field(min_length=20, max_length=40)]
    execution_authorized: bool
    caller_supplied_case_omission_allowed: Literal[False] = False
    caller_supplied_final_decision_allowed: Literal[False] = False
    pilot_eligibility_type_reused: Literal[False] = False
    scientific_conclusion: Literal[False] = False

    @field_validator("sealed_at")
    @classmethod
    def validate_time(cls, value: str) -> str:
        _timestamp(value)
        return value

    @model_validator(mode="after")
    def validate_release(self) -> MainEligibilityReleaseV1:
        pool = _revalidate(self.candidate_pool_release, MainCandidatePoolReleaseV1)
        raw = tuple(_revalidate(item, MainEligibilityRawAuditV1) for item in self.raw_audits)
        adjudications = tuple(
            _revalidate(item, MainEligibilityAdjudicationV1)
            for item in self.adjudications
        )
        if len(raw) != 240:
            raise ValueError("Main eligibility requires exactly 240 raw audits")
        raw_keys = tuple(
            (item.case_id, item.auditor_id, item.audit_id) for item in raw
        )
        if raw_keys != tuple(sorted(set(raw_keys))):
            raise ValueError("Main eligibility raw audits must be canonically sorted")
        adjudication_keys = tuple(
            (item.case_id, item.adjudication_id) for item in adjudications
        )
        if adjudication_keys != tuple(sorted(set(adjudication_keys))):
            raise ValueError("Main eligibility adjudications must be canonically sorted")
        rebuilt = _derive_main_eligibility_decisions_v1(
            pool=pool,
            raw_audits=raw,
            adjudications=adjudications,
            decided_at=self.sealed_at,
        )
        if self.decisions != rebuilt:
            raise ValueError("Main eligibility decisions do not replay from raw audits")
        if len(self.decisions) != 120:
            raise ValueError("Main eligibility decisions do not exactly cover 120 cases")
        if tuple(item.case_id for item in self.decisions) != tuple(
            sorted(item.case_id for item in self.decisions)
        ):
            raise ValueError("Main eligibility decisions must be case-sorted")
        eligible = tuple(
            item.case_id
            for item in rebuilt
            if item.final_status is MainEligibilityStatusV1.ELIGIBLE
        )
        if self.eligible_case_ids != eligible:
            raise ValueError("Main eligible IDs are not derived from final decisions")
        expected_authorized = len(eligible) == len(pool.cases)
        if self.execution_authorized != expected_authorized:
            raise ValueError("Main eligibility authorization does not replay")
        if self.eligible_case_universe_sha256 != pool.case_universe_sha256:
            raise ValueError("Main eligibility binds a foreign case universe")
        if _timestamp(pool.sealed_at) >= _timestamp(self.sealed_at):
            raise ValueError("Main eligibility was not sealed after candidate custody")
        _assert_addressed(
            self,
            id_field="release_id",
            sha_field="release_sha256",
            prefix="main-eligibility-v1",
        )
        return self


def build_main_eligibility_release_v1(
    *,
    candidate_pool_release: MainCandidatePoolReleaseV1,
    eligibility_policy_sha256: str,
    raw_audits: tuple[MainEligibilityRawAuditV1, ...],
    adjudications: tuple[MainEligibilityAdjudicationV1, ...] = (),
    sealed_at: str,
) -> MainEligibilityReleaseV1:
    pool = _revalidate(candidate_pool_release, MainCandidatePoolReleaseV1)
    raw = tuple(
        sorted(
            (_revalidate(item, MainEligibilityRawAuditV1) for item in raw_audits),
            key=lambda item: (item.case_id, item.auditor_id, item.audit_id),
        )
    )
    adjudicated = tuple(
        sorted(
            (
                _revalidate(item, MainEligibilityAdjudicationV1)
                for item in adjudications
            ),
            key=lambda item: (item.case_id, item.adjudication_id),
        )
    )
    decisions = _derive_main_eligibility_decisions_v1(
        pool=pool,
        raw_audits=raw,
        adjudications=adjudicated,
        decided_at=sealed_at,
    )
    eligible = tuple(
        item.case_id
        for item in decisions
        if item.final_status is MainEligibilityStatusV1.ELIGIBLE
    )
    return _build_addressed(
        MainEligibilityReleaseV1,
        id_field="release_id",
        sha_field="release_sha256",
        prefix="main-eligibility-v1",
        values={
            "candidate_pool_release": pool,
            "eligibility_policy_sha256": eligibility_policy_sha256,
            "raw_audits": raw,
            "adjudications": adjudicated,
            "decisions": decisions,
            "eligible_case_ids": eligible,
            "eligible_case_universe_sha256": pool.case_universe_sha256,
            "sealed_at": sealed_at,
            "execution_authorized": len(eligible) == len(pool.cases),
        },
    )


def assert_main_eligibility_exact_v1(release: MainEligibilityReleaseV1) -> None:
    value = _revalidate(release, MainEligibilityReleaseV1)
    rebuilt = build_main_eligibility_release_v1(
        candidate_pool_release=value.candidate_pool_release,
        eligibility_policy_sha256=value.eligibility_policy_sha256,
        raw_audits=value.raw_audits,
        adjudications=value.adjudications,
        sealed_at=value.sealed_at,
    )
    if rebuilt != value:
        raise ValueError("Main eligibility release does not replay exactly")


class MainFrozenCaseReleaseV1(StrictModel):
    """Exact Main120 split/leakage/expert freeze; no Pilot frozen type is reused."""

    schema_version: Literal["flatband-main-frozen-case-release-v1"] = (
        "flatband-main-frozen-case-release-v1"
    )
    release_id: Identifier
    release_sha256: Sha256
    eligibility_release: MainEligibilityReleaseV1
    split_manifest: BenchmarkSplitManifestV2
    leakage_release: LeakageComponentReleaseV3
    expert_registry: ExpertStudyRegistryV2
    active_case_ids: Annotated[
        tuple[Identifier, ...], Field(min_length=120, max_length=120)
    ]
    development_component_count: Annotated[int, Field(ge=20, le=60)]
    locked_iid_component_count: Annotated[int, Field(ge=10, le=30)]
    locked_ood_component_count: Annotated[int, Field(ge=10, le=30)]
    frozen_at: Annotated[str, Field(min_length=20, max_length=40)]
    exact_split_counts: Literal["60/30/30"] = "60/30/30"
    minimum_component_counts: Literal["20/10/10"] = "20/10/10"
    complete_case_omission_allowed: Literal[False] = False
    pilot_frozen_type_reused: Literal[False] = False
    scientific_conclusion: Literal[False] = False

    @field_validator("frozen_at")
    @classmethod
    def validate_time(cls, value: str) -> str:
        _timestamp(value)
        return value

    @model_validator(mode="after")
    def validate_release(self) -> MainFrozenCaseReleaseV1:
        eligibility = _revalidate(
            self.eligibility_release, MainEligibilityReleaseV1
        )
        if not eligibility.execution_authorized:
            raise ValueError("Main frozen release requires all 120 cases eligible")
        pool = eligibility.candidate_pool_release
        split = _revalidate(self.split_manifest, BenchmarkSplitManifestV2)
        leakage = _revalidate(self.leakage_release, LeakageComponentReleaseV3)
        registry = _revalidate(self.expert_registry, ExpertStudyRegistryV2)
        if split.manifest_kind is not SplitManifestKind.MAIN_120:
            raise ValueError("Main frozen release requires MAIN_120")
        if split.split_seed != pool.split_seed:
            raise ValueError("Main split seed differs from candidate custody")
        pool_by_id = {item.case_id: item for item in pool.cases}
        manifest_ids = tuple(item.case_id for item in split.cases)
        if self.active_case_ids != manifest_ids or set(manifest_ids) != set(pool_by_id):
            raise ValueError("Main frozen cases do not exactly cover candidate custody")
        for ref in split.cases:
            case = pool_by_id[ref.case_id]
            if (
                ref.case_sha256,
                ref.target_class,
                ref.dimensionality,
                ref.primary_mechanism_stratum,
                ref.independence_group_ids,
            ) != (
                case.case_sha256,
                case.target_class,
                case.dimensionality,
                case.primary_mechanism_stratum,
                case.leakage_group_ids,
            ):
                raise ValueError("Main split projection differs from the exact case")
        assert_main_leakage_v3(
            cases=pool.cases,
            split_manifest=split,
            release=leakage,
            lineage_curation_release=pool.lineage_curation_release,
        )
        assert_expert_registry_covers_split_v2(
            registry=registry, split_manifest=split
        )
        split_by_case = {item.case_id: item.split for item in split.cases}
        counts = {
            benchmark_split: sum(
                split_by_case[component.case_ids[0]] is benchmark_split
                for component in leakage.components
            )
            for benchmark_split in (
                BenchmarkSplit.DEVELOPMENT,
                BenchmarkSplit.LOCKED_IID,
                BenchmarkSplit.LOCKED_OOD,
            )
        }
        observed = (
            self.development_component_count,
            self.locked_iid_component_count,
            self.locked_ood_component_count,
        )
        expected = (
            counts[BenchmarkSplit.DEVELOPMENT],
            counts[BenchmarkSplit.LOCKED_IID],
            counts[BenchmarkSplit.LOCKED_OOD],
        )
        if observed != expected:
            raise ValueError("Main component-count projection does not replay")
        if observed[0] < 20 or observed[1] < 10 or observed[2] < 10:
            raise ValueError("Main leakage does not meet the 20/10/10 component minima")
        if _timestamp(eligibility.sealed_at) >= min(
            _timestamp(leakage.created_at), _timestamp(registry.registered_at)
        ):
            raise ValueError("Main eligibility was not sealed before leakage/expert closure")
        if max(
            _timestamp(leakage.created_at), _timestamp(registry.registered_at)
        ) >= _timestamp(self.frozen_at):
            raise ValueError("Main frozen release predates leakage/expert closure")
        _assert_addressed(
            self,
            id_field="release_id",
            sha_field="release_sha256",
            prefix="main-frozen-case-v1",
        )
        return self


def _component_counts(
    split: BenchmarkSplitManifestV2,
    leakage: LeakageComponentReleaseV3,
) -> tuple[int, int, int]:
    split_by_case = {item.case_id: item.split for item in split.cases}
    return tuple(
        sum(
            split_by_case[component.case_ids[0]] is benchmark_split
            for component in leakage.components
        )
        for benchmark_split in (
            BenchmarkSplit.DEVELOPMENT,
            BenchmarkSplit.LOCKED_IID,
            BenchmarkSplit.LOCKED_OOD,
        )
    )


def build_main_frozen_case_release_v1(
    *,
    eligibility_release: MainEligibilityReleaseV1,
    split_manifest: BenchmarkSplitManifestV2,
    leakage_release: LeakageComponentReleaseV3,
    expert_registry: ExpertStudyRegistryV2,
    frozen_at: str,
) -> MainFrozenCaseReleaseV1:
    eligibility = _revalidate(eligibility_release, MainEligibilityReleaseV1)
    split = _revalidate(split_manifest, BenchmarkSplitManifestV2)
    leakage = _revalidate(leakage_release, LeakageComponentReleaseV3)
    counts = _component_counts(split, leakage)
    return _build_addressed(
        MainFrozenCaseReleaseV1,
        id_field="release_id",
        sha_field="release_sha256",
        prefix="main-frozen-case-v1",
        values={
            "eligibility_release": eligibility,
            "split_manifest": split,
            "leakage_release": leakage,
            "expert_registry": _revalidate(expert_registry, ExpertStudyRegistryV2),
            "active_case_ids": tuple(item.case_id for item in split.cases),
            "development_component_count": counts[0],
            "locked_iid_component_count": counts[1],
            "locked_ood_component_count": counts[2],
            "frozen_at": frozen_at,
        },
    )


def assert_main_frozen_case_exact_v1(release: MainFrozenCaseReleaseV1) -> None:
    value = _revalidate(release, MainFrozenCaseReleaseV1)
    rebuilt = build_main_frozen_case_release_v1(
        eligibility_release=value.eligibility_release,
        split_manifest=value.split_manifest,
        leakage_release=value.leakage_release,
        expert_registry=value.expert_registry,
        frozen_at=value.frozen_at,
    )
    if rebuilt != value:
        raise ValueError("Main frozen release does not replay exactly")


def _validate_terminal_pilot_contexts_v1(
    *,
    gate: FormalPilotAgreementGateReleaseV1,
    contexts: tuple[LeakageRoundClosureContextV3, ...],
    curation: MechanismLineageCurationReleaseV3,
) -> tuple[LeakageRoundClosureContextV3, ...]:
    value = _revalidate(gate, FormalPilotAgreementGateReleaseV1)
    ordered = tuple(
        _revalidate(item, LeakageRoundClosureContextV3) for item in contexts
    )
    if not _pilot_gate_passes(value):
        raise ValueError("Main pre-budget closure requires a terminal Pilot PASS")
    kinds = tuple(item.split_manifest.manifest_kind for item in ordered)
    if value.decision is PilotAgreementGateDecision.R1_PASS_MAIN_ALLOWED:
        if kinds != (SplitManifestKind.PILOT_R1,):
            raise ValueError("R1 PASS requires its exact single R1 leakage context")
    elif kinds != (SplitManifestKind.PILOT_R1, SplitManifestKind.PILOT_R2):
        raise ValueError("R2 PASS requires ordered R1 and R2 leakage contexts")
    current = ordered[-1]
    if (
        value.leakage_release_id,
        value.leakage_release_sha256,
        value.lineage_curation_release_id,
        value.lineage_curation_release_sha256,
    ) != (
        current.release.release_id,
        current.release.release_sha256,
        curation.release_id,
        curation.release_sha256,
    ):
        raise ValueError("terminal Pilot Gate binds foreign leakage/curation roots")
    if len(ordered) == 2 and (
        value.prior_r1_leakage_release_id,
        value.prior_r1_leakage_release_sha256,
        value.r2_cases_and_components_disjoint,
    ) != (
        ordered[0].release.release_id,
        ordered[0].release.release_sha256,
        True,
    ):
        raise ValueError("R2 PASS binds a foreign or non-disjoint R1 context")
    for context in ordered:
        assert_pilot_leakage_v3(
            cases=context.cases,
            split_manifest=context.split_manifest,
            release=context.release,
            lineage_curation_release=curation,
        )
    return ordered


class MainPreBudgetClosureReleaseV1(StrictModel):
    """All-axis calibration/Pilot/Main and external structure-union Gate."""

    schema_version: Literal["flatband-main-pre-budget-closure-release-v1"] = (
        "flatband-main-pre-budget-closure-release-v1"
    )
    release_id: Identifier
    release_sha256: Sha256
    frozen_case_release: MainFrozenCaseReleaseV1
    calibration_leakage_context: LeakageUnsplitCaseUniverseContextV3
    pilot_leakage_contexts: Annotated[
        tuple[LeakageRoundClosureContextV3, ...], Field(min_length=1, max_length=2)
    ]
    main_leakage_context: LeakageRoundClosureContextV3
    capacity_policy: MainCapacityPolicyV1
    structure_union_release: MainStructureUnionReleaseV1
    main_structure_artifact_id: Identifier
    main_structure_artifact_sha256: Sha256
    sealed_at: Annotated[str, Field(min_length=20, max_length=40)]
    full_axis_calibration_pilot_main_disjoint: Literal[True] = True
    caller_structure_union_boolean_accepted: Literal[False] = False
    registered_structure_union_attestation_required: Literal[True] = True
    pilot_v0_structure_union_reused: Literal[False] = False
    sealed_before_every_budget: Literal[True] = True
    private_custody_required: Literal[True] = True
    scientific_conclusion: Literal[False] = False

    @field_validator("sealed_at")
    @classmethod
    def validate_time(cls, value: str) -> str:
        _timestamp(value)
        return value

    @model_validator(mode="after")
    def validate_release(self) -> MainPreBudgetClosureReleaseV1:
        frozen = _revalidate(self.frozen_case_release, MainFrozenCaseReleaseV1)
        pool = frozen.eligibility_release.candidate_pool_release
        calibration = _revalidate(
            self.calibration_leakage_context,
            LeakageUnsplitCaseUniverseContextV3,
        )
        main = _revalidate(
            self.main_leakage_context, LeakageRoundClosureContextV3
        )
        capacity = _revalidate(self.capacity_policy, MainCapacityPolicyV1)
        union = _revalidate(
            self.structure_union_release, MainStructureUnionReleaseV1
        )
        pilots = _validate_terminal_pilot_contexts_v1(
            gate=pool.terminal_pilot_gate,
            contexts=self.pilot_leakage_contexts,
            curation=pool.lineage_curation_release,
        )
        if (main.cases, main.split_manifest, main.release) != (
            pool.cases,
            frozen.split_manifest,
            frozen.leakage_release,
        ):
            raise ValueError("Main leakage context differs from the frozen Main root")
        assert_main_leakage_v3(
            cases=main.cases,
            split_manifest=main.split_manifest,
            release=main.release,
            lineage_curation_release=pool.lineage_curation_release,
        )
        assert_cross_round_leakage_disjoint_v3(
            round_contexts=(*pilots, main),
            lineage_curation_release=pool.lineage_curation_release,
            additional_case_universes=(calibration,),
        )
        expected_candidate_count = (
            len(calibration.cases)
            + sum(len(item.cases) for item in pilots)
            + len(main.cases)
        )
        expected_member_count = len(pilots) + 2
        if (
            capacity.authorized_union_candidate_count,
            capacity.authorized_union_member_count,
        ) != (expected_candidate_count, expected_member_count):
            raise ValueError("Main capacity does not exactly cover all leakage owners")
        if (
            self.main_structure_artifact_id,
            self.main_structure_artifact_sha256,
        ) != (
            pool.private_structure_artifact_id,
            pool.private_structure_artifact_sha256,
        ):
            raise ValueError("Main pre-budget closure binds a foreign structure root")
        if union.capacity_policy != capacity:
            raise ValueError("Main pre-budget closure crosswires union capacity")
        assert_main_structure_union_release_exact_v1(
            union,
            calibration_context=calibration,
            pilot_contexts=pilots,
            main_context=main,
        )
        registry = frozen.expert_registry
        if (
            registry.calibration_manifest_id,
            registry.calibration_manifest_sha256,
        ) != (
            calibration.source_artifact_id,
            calibration.source_artifact_sha256,
        ):
            raise ValueError("Main expert registry binds a foreign calibration context")
        latest_context_time = max(
            _timestamp(item.release.created_at) for item in (*pilots, main)
        )
        if max(_timestamp(frozen.frozen_at), latest_context_time) >= _timestamp(
            capacity.frozen_at
        ):
            raise ValueError("Main capacity decision predates frozen leakage contexts")
        if max(
            _timestamp(capacity.frozen_at),
            _timestamp(union.created_at),
        ) >= _timestamp(self.sealed_at):
            raise ValueError("Main pre-budget seal does not follow capacity/union release")
        _assert_addressed(
            self,
            id_field="release_id",
            sha_field="release_sha256",
            prefix="main-pre-budget-closure-v1",
        )
        return self


def build_main_pre_budget_closure_release_v1(
    *,
    frozen_case_release: MainFrozenCaseReleaseV1,
    calibration_leakage_context: LeakageUnsplitCaseUniverseContextV3,
    pilot_leakage_contexts: tuple[LeakageRoundClosureContextV3, ...],
    main_leakage_context: LeakageRoundClosureContextV3,
    capacity_policy: MainCapacityPolicyV1,
    structure_union_release: MainStructureUnionReleaseV1,
    sealed_at: str,
) -> MainPreBudgetClosureReleaseV1:
    frozen = _revalidate(frozen_case_release, MainFrozenCaseReleaseV1)
    pool = frozen.eligibility_release.candidate_pool_release
    capacity = _revalidate(capacity_policy, MainCapacityPolicyV1)
    union = _revalidate(
        structure_union_release, MainStructureUnionReleaseV1
    )
    return _build_addressed(
        MainPreBudgetClosureReleaseV1,
        id_field="release_id",
        sha_field="release_sha256",
        prefix="main-pre-budget-closure-v1",
        values={
            "frozen_case_release": frozen,
            "calibration_leakage_context": _revalidate(
                calibration_leakage_context,
                LeakageUnsplitCaseUniverseContextV3,
            ),
            "pilot_leakage_contexts": tuple(
                _revalidate(item, LeakageRoundClosureContextV3)
                for item in pilot_leakage_contexts
            ),
            "main_leakage_context": _revalidate(
                main_leakage_context, LeakageRoundClosureContextV3
            ),
            "capacity_policy": capacity,
            "structure_union_release": union,
            "main_structure_artifact_id": pool.private_structure_artifact_id,
            "main_structure_artifact_sha256": pool.private_structure_artifact_sha256,
            "sealed_at": sealed_at,
        },
    )


def assert_main_pre_budget_closure_exact_v1(
    release: MainPreBudgetClosureReleaseV1,
) -> None:
    value = _revalidate(release, MainPreBudgetClosureReleaseV1)
    rebuilt = build_main_pre_budget_closure_release_v1(
        frozen_case_release=value.frozen_case_release,
        calibration_leakage_context=value.calibration_leakage_context,
        pilot_leakage_contexts=value.pilot_leakage_contexts,
        main_leakage_context=value.main_leakage_context,
        capacity_policy=value.capacity_policy,
        structure_union_release=value.structure_union_release,
        sealed_at=value.sealed_at,
    )
    if rebuilt != value:
        raise ValueError("Main pre-budget closure does not replay exactly")


_MAIN_PHASE_DESIGN: dict[
    ExecutionPhase, tuple[tuple[ResearchSystemId, ...], tuple[BenchmarkSplit, ...]]
] = {
    ExecutionPhase.DEVELOPMENT_ABLATIONS: (
        tuple(
            sorted(
                (
                    ResearchSystemId.B0,
                    ResearchSystemId.E1,
                    ResearchSystemId.E2_A,
                    ResearchSystemId.E2_B,
                    ResearchSystemId.E3,
                ),
                key=lambda item: item.value,
            )
        ),
        (BenchmarkSplit.DEVELOPMENT,),
    ),
    ExecutionPhase.DEVELOPMENT_LOCAL_SENSITIVITY: (
        (ResearchSystemId.E1_LOCAL,),
        (BenchmarkSplit.DEVELOPMENT,),
    ),
    ExecutionPhase.DEVELOPMENT_FUSION: (
        tuple(
            sorted(
                (ResearchSystemId.B0, ResearchSystemId.FUSION),
                key=lambda item: item.value,
            )
        ),
        (BenchmarkSplit.DEVELOPMENT,),
    ),
    ExecutionPhase.LOCKED_FUSION_COMPONENTS: (
        tuple(
            sorted(
                (
                    ResearchSystemId.E1,
                    ResearchSystemId.E2_A,
                    ResearchSystemId.E2_B,
                    ResearchSystemId.E3,
                ),
                key=lambda item: item.value,
            )
        ),
        tuple(
            sorted(
                (BenchmarkSplit.LOCKED_IID, BenchmarkSplit.LOCKED_OOD),
                key=lambda item: item.value,
            )
        ),
    ),
    ExecutionPhase.LOCKED_PRIMARY: (
        tuple(
            sorted(
                (ResearchSystemId.B0, ResearchSystemId.FUSION),
                key=lambda item: item.value,
            )
        ),
        tuple(sorted((BenchmarkSplit.LOCKED_IID, BenchmarkSplit.LOCKED_OOD), key=lambda item: item.value)),
    ),
}


class MainPhaseAuthorizationReleaseV1(StrictModel):
    """Exact phase/case/system permission derived from one Main frozen root."""

    schema_version: Literal["flatband-main-phase-authorization-release-v1"] = (
        "flatband-main-phase-authorization-release-v1"
    )
    release_id: Identifier
    release_sha256: Sha256
    pre_budget_closure_release: MainPreBudgetClosureReleaseV1
    execution_phase: Literal[
        ExecutionPhase.DEVELOPMENT_ABLATIONS,
        ExecutionPhase.DEVELOPMENT_LOCAL_SENSITIVITY,
        ExecutionPhase.DEVELOPMENT_FUSION,
        ExecutionPhase.LOCKED_FUSION_COMPONENTS,
        ExecutionPhase.LOCKED_PRIMARY,
    ]
    authorized_system_ids: Annotated[
        tuple[ResearchSystemId, ...], Field(min_length=1, max_length=5)
    ]
    authorized_splits: Annotated[
        tuple[BenchmarkSplit, ...], Field(min_length=1, max_length=2)
    ]
    authorized_case_ids: Annotated[
        tuple[Identifier, ...], Field(min_length=60, max_length=60)
    ]
    authorized_at: Annotated[str, Field(min_length=20, max_length=40)]
    e1_local_primary_allowed: Literal[False] = False
    main_primary_scope_contains_e1_local: Literal[False] = False
    locked_component_cells_are_comparison_arms: Literal[False] = False
    execution_result_embedded: Literal[False] = False
    scientific_conclusion: Literal[False] = False

    @field_validator("authorized_at")
    @classmethod
    def validate_time(cls, value: str) -> str:
        _timestamp(value)
        return value

    @model_validator(mode="after")
    def validate_release(self) -> MainPhaseAuthorizationReleaseV1:
        pre_budget = _revalidate(
            self.pre_budget_closure_release,
            MainPreBudgetClosureReleaseV1,
        )
        frozen = pre_budget.frozen_case_release
        expected_systems, expected_splits = _MAIN_PHASE_DESIGN[self.execution_phase]
        if self.authorized_system_ids != expected_systems:
            raise ValueError("Main phase systems differ from the frozen design")
        if self.authorized_splits != expected_splits:
            raise ValueError("Main phase splits differ from the frozen design")
        expected_cases = tuple(
            item.case_id
            for item in frozen.split_manifest.cases
            if item.split in expected_splits
        )
        if self.authorized_case_ids != expected_cases:
            raise ValueError("Main phase does not exactly cover its split cases")
        if (
            self.execution_phase is ExecutionPhase.LOCKED_PRIMARY
            and ResearchSystemId.E1_LOCAL in self.authorized_system_ids
        ):
            raise ValueError("E1-local is forbidden from Main primary analysis")
        if _timestamp(pre_budget.sealed_at) >= _timestamp(self.authorized_at):
            raise ValueError("Main phase authorization does not follow pre-budget closure")
        _assert_addressed(
            self,
            id_field="release_id",
            sha_field="release_sha256",
            prefix="main-phase-authorization-v1",
        )
        return self


def build_main_phase_authorization_release_v1(
    *,
    pre_budget_closure_release: MainPreBudgetClosureReleaseV1,
    execution_phase: ExecutionPhase,
    authorized_at: str,
) -> MainPhaseAuthorizationReleaseV1:
    pre_budget = _revalidate(
        pre_budget_closure_release, MainPreBudgetClosureReleaseV1
    )
    frozen = pre_budget.frozen_case_release
    if execution_phase not in _MAIN_PHASE_DESIGN:
        raise ValueError("Pilot execution phases cannot use Main authorization")
    systems, splits = _MAIN_PHASE_DESIGN[execution_phase]
    return _build_addressed(
        MainPhaseAuthorizationReleaseV1,
        id_field="release_id",
        sha_field="release_sha256",
        prefix="main-phase-authorization-v1",
        values={
            "pre_budget_closure_release": pre_budget,
            "execution_phase": execution_phase,
            "authorized_system_ids": systems,
            "authorized_splits": splits,
            "authorized_case_ids": tuple(
                item.case_id
                for item in frozen.split_manifest.cases
                if item.split in splits
            ),
            "authorized_at": authorized_at,
        },
    )


def assert_main_phase_authorization_exact_v1(
    release: MainPhaseAuthorizationReleaseV1,
) -> None:
    value = _revalidate(release, MainPhaseAuthorizationReleaseV1)
    rebuilt = build_main_phase_authorization_release_v1(
        pre_budget_closure_release=value.pre_budget_closure_release,
        execution_phase=value.execution_phase,
        authorized_at=value.authorized_at,
    )
    if rebuilt != value:
        raise ValueError("Main phase authorization does not replay exactly")


__all__ = [
    "MainCandidatePoolReleaseV1",
    "MainCapacityPolicyV1",
    "MainEligibilityAdjudicationV1",
    "MainEligibilityArtifactRefV1",
    "MainEligibilityDecisionV1",
    "MainEligibilityEvidenceRefV1",
    "MainEligibilityRawAuditV1",
    "MainEligibilityReleaseV1",
    "MainEligibilityStatusV1",
    "MainFrozenCaseReleaseV1",
    "MainPhaseAuthorizationReleaseV1",
    "MainPreBudgetClosureReleaseV1",
    "MainSamplingPolicyReleaseV1",
    "MainStructureCaseRefV1",
    "MainStructureUnionMemberV1",
    "MainStructureUnionOwnerV1",
    "MainStructureUnionReleaseV1",
    "MainStructureUnionVerifierAttestationV1",
    "assert_main_candidate_pool_exact_v1",
    "assert_main_capacity_policy_exact_v1",
    "assert_main_eligibility_exact_v1",
    "assert_main_frozen_case_exact_v1",
    "assert_main_phase_authorization_exact_v1",
    "assert_main_pre_budget_closure_exact_v1",
    "assert_main_sampling_policy_exact_v1",
    "assert_main_structure_union_release_exact_v1",
    "build_main_candidate_pool_release_v1",
    "build_main_capacity_policy_v1",
    "build_main_eligibility_adjudication_v1",
    "build_main_eligibility_raw_audit_v1",
    "build_main_eligibility_release_v1",
    "build_main_frozen_case_release_v1",
    "build_main_phase_authorization_release_v1",
    "build_main_pre_budget_closure_release_v1",
    "build_main_sampling_policy_release_v1",
    "build_main_structure_union_release_v1",
]
