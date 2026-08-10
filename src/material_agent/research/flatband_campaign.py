"""Authoritative private full-flow closure for the flat/narrow-band study.

The campaign artifact is an orchestration contract, not a benchmark result and
not a semantic model.  It joins the audited source checkpoint, terminal Pilot
round, frozen Main120 custody, five Main authorizations, seven formal execution
releases plus the explicitly disabled local-sensitivity branch, human Gold,
Analysis V2, development promotion, Fusion, locked-test custody, scientific
review, and the final aggregate-only public projection.

Raw human and runner inputs remain owned by their domain builders.  The
assembler in this module therefore accepts already-built *exact* artifacts at
those seams, then calls every owning formal verifier and rejects any derived
surface that cannot be replayed.  Ephemeral Pilot/Main-Gold blinding keys,
three phase-scoped expert-annotation key maps, the four precommitted
release-authority HMAC keys, and the two reviewer-specific decision keys are
arguments to formal replay only; they are never persisted in this artifact.
Likewise, model chain-of-thought, provider transcripts, credentials, and local
semantic-model outputs are not campaign fields.

``MainPhaseExecutionReleaseV1`` is the mandatory execution bridge; Fusion
releases recursively embed the exact component-phase release in
``component_execution_release``.  A bare tuple of arm traces is never an
accepted substitute.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Annotated, Literal, TypeVar

from pydantic import Field, field_validator, model_validator

from material_agent.inspiration.models import (
    Identifier,
    Sha256,
    StrictModel,
    canonical_sha256,
    deterministic_id,
)
from material_agent.research.flatband_analysis_v2 import (
    AnalysisArtifactRefV2,
    AnalysisArtifactTypeV2,
    AnalysisInputReleaseV2,
    AnalysisTraceRoleV2,
    analysis_input_ref_v2,
    assert_analysis_input_exact_replay_v2,
    build_formal_verifier_attestation_v1,
    derive_development_metric_rows_v2,
    derive_locked_result_family_v2,
)
from material_agent.research.flatband_execution import (
    ExecutionPhase,
    ResearchSystemId,
    SystemConfigV1,
)
from material_agent.research.flatband_experts import (
    PrivateExpertIdentityCustodianAttestationV2,
    assert_distinct_natural_person_assignments_v2,
)
from material_agent.research.flatband_ingress import (
    SourceCatalogCheckpointReleaseV1,
)
from material_agent.research.flatband_lifecycle import (
    ClaimSupportReleaseV1,
    DevelopmentFusionGateReleaseV1,
    DevelopmentPromotionReleaseV1,
    FormalVerifierAttestationRefV1,
    FusionConfigurationReleaseV1,
    LifecycleArtifactType,
    LockedAnnotationUnsealReleaseV1,
    LockedLabelSealV1,
    LockedTestAuthorizationReleaseV1,
    LockedUnsealLedgerReleaseV1,
    ProtocolDeviationReleaseV1,
    PublicBenchmarkProjectionV1,
    PublicBenchmarkResultReleaseV1,
    PublicLimitationCodeV1,
    PublicReleaseAuthorizationV1,
    ReleaseAuthorityPolicyV1,
    ReleaseAuthorityRoleV1,
    ReleaseControlAttestationV1,
    ReleaseControlKindV1,
    ScientificReviewReleaseV1,
    ScientificReviewerAttestationV1,
    ScientificReviewerIdentityAttestationV1,
    VerifiedPayloadKind,
    append_locked_unseal_ledger_v1,
    assert_claim_support_release_exact_replay_v1,
    assert_development_fusion_gate_release_exact_replay_v1,
    assert_development_promotion_release_exact_replay_v1,
    assert_fusion_configuration_release_exact_replay_v1,
    assert_locked_annotation_unseal_exact_replay_v1,
    assert_locked_label_seal_exact_replay_v1,
    assert_locked_test_authorization_exact_replay_v1,
    assert_public_benchmark_projection_exact_replay_v1,
    assert_public_benchmark_result_release_exact_replay_v1,
    assert_public_release_authorization_exact_replay_v1,
    assert_protocol_deviation_exact_replay_v1,
    assert_release_control_attestation_exact_replay_v1,
    assert_scientific_review_release_exact_replay_v1,
    assert_scientific_reviewer_attestation_exact_replay_v1,
    assert_scientific_reviewer_identity_attestation_exact_replay_v1,
    build_release_authority_policy_v1,
    build_locked_unseal_ledger_genesis_v1,
)
from material_agent.research.flatband_main import (
    MainCandidatePoolReleaseV1,
    MainEligibilityReleaseV1,
    MainFrozenCaseReleaseV1,
    MainPhaseAuthorizationReleaseV1,
    MainPreBudgetClosureReleaseV1,
    MainSamplingPolicyReleaseV1,
    assert_main_candidate_pool_exact_v1,
    assert_main_eligibility_exact_v1,
    assert_main_frozen_case_exact_v1,
    assert_main_phase_authorization_exact_v1,
    assert_main_pre_budget_closure_exact_v1,
    assert_main_sampling_policy_exact_v1,
)
from material_agent.research.flatband_main_gold import (
    MainGoldFormalVerifierAttestationV1,
    MainGoldReleaseV1,
    assert_main_gold_release_exact,
    build_analysis_cell_from_main_gold_v1,
    build_main_gold_formal_verifier_attestation,
)
from material_agent.research.flatband_workflow import (
    PilotRoundArtifactsV3,
    assert_formal_pilot_round_artifacts_v3,
)


from material_agent.research.flatband_main_execution import (
    MainPhaseExecutionReleaseV1,
    assert_main_phase_execution_exact_v1,
    build_analysis_trace_evidence_index_from_main_v1,
    main_gold_execution_cells_from_main_v1,
)


ModelT = TypeVar("ModelT", bound=StrictModel)

_FORMAL_PHASE_ORDER = (
    ExecutionPhase.DEVELOPMENT_ABLATIONS,
    ExecutionPhase.DEVELOPMENT_LOCAL_SENSITIVITY,
    ExecutionPhase.DEVELOPMENT_FUSION,
    ExecutionPhase.LOCKED_FUSION_COMPONENTS,
    ExecutionPhase.LOCKED_PRIMARY,
)
_FORMAL_EXECUTION_SOURCE_PHASE_ORDER = (
    ExecutionPhase.DEVELOPMENT_ABLATIONS,
    ExecutionPhase.DEVELOPMENT_FUSION,
    ExecutionPhase.LOCKED_FUSION_COMPONENTS,
    ExecutionPhase.LOCKED_PRIMARY,
    ExecutionPhase.LOCKED_FUSION_MINUS_E1,
    ExecutionPhase.LOCKED_FUSION_MINUS_E2,
    ExecutionPhase.LOCKED_FUSION_MINUS_E3,
)
_GOLD_PHASE_ORDER = (
    ExecutionPhase.DEVELOPMENT_ABLATIONS,
    ExecutionPhase.DEVELOPMENT_FUSION,
    ExecutionPhase.LOCKED_PRIMARY,
)
_IMPLEMENTATION_ONLY_PUBLIC_LIMITATIONS = frozenset(PublicLimitationCodeV1)
_LOCKED_PLAN_ROLE_ORDER = (
    AnalysisTraceRoleV2.B0,
    AnalysisTraceRoleV2.FUSION,
    AnalysisTraceRoleV2.FUSION_MINUS_E1,
    AnalysisTraceRoleV2.FUSION_MINUS_E2,
    AnalysisTraceRoleV2.FUSION_MINUS_E3,
)
_LOCKED_PLAN_SOURCE_PHASE = {
    AnalysisTraceRoleV2.B0: ExecutionPhase.LOCKED_PRIMARY,
    AnalysisTraceRoleV2.FUSION: ExecutionPhase.LOCKED_PRIMARY,
    AnalysisTraceRoleV2.FUSION_MINUS_E1: (
        ExecutionPhase.LOCKED_FUSION_MINUS_E1
    ),
    AnalysisTraceRoleV2.FUSION_MINUS_E2: (
        ExecutionPhase.LOCKED_FUSION_MINUS_E2
    ),
    AnalysisTraceRoleV2.FUSION_MINUS_E3: (
        ExecutionPhase.LOCKED_FUSION_MINUS_E3
    ),
}
_LOCKED_COMPONENT_SYSTEM_ORDER = tuple(
    sorted(
        (
            ResearchSystemId.E1,
            ResearchSystemId.E2_A,
            ResearchSystemId.E2_B,
            ResearchSystemId.E3,
        ),
        key=lambda item: item.value,
    )
)


def _timestamp(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("timestamp must be RFC3339-compatible") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("timestamp must include a UTC offset")
    return parsed


def _revalidate(value: ModelT, model_type: type[ModelT]) -> ModelT:
    return model_type.model_validate(
        value.model_dump(mode="python", round_trip=True)
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
            id_field: deterministic_id(prefix, {sha_field: digest}),
            sha_field: digest,
        }
    )


def _assert_addressed(
    value: StrictModel, *, id_field: str, sha_field: str, prefix: str
) -> None:
    semantic = value.model_dump(mode="python", exclude={id_field, sha_field})
    digest = canonical_sha256(semantic)
    if getattr(value, sha_field) != digest:
        raise ValueError(f"{sha_field} does not match campaign semantic content")
    if getattr(value, id_field) != deterministic_id(prefix, {sha_field: digest}):
        raise ValueError(f"{id_field} does not match campaign semantic content")


def _require_after(later: str, earlier: str, label: str) -> None:
    if _timestamp(later) <= _timestamp(earlier):
        raise ValueError(f"{label} must be strictly later")


class LocalSensitivityNotRunReleaseV1(StrictModel):
    """Deterministic closure of the user-prohibited E1-local branch."""

    schema_version: Literal["flatband-local-sensitivity-not-run-v1"] = (
        "flatband-local-sensitivity-not-run-v1"
    )
    release_id: Identifier
    release_sha256: Sha256
    phase_authorization: MainPhaseAuthorizationReleaseV1
    decision: Literal["NOT_RUN_USER_PROHIBITED"] = "NOT_RUN_USER_PROHIBITED"
    decided_at: Annotated[str, Field(min_length=20, max_length=40)]
    execution_release_count: Literal[0] = 0
    output_artifact_count: Literal[0] = 0
    local_model_invocation_count: Literal[0] = 0
    promotion_or_locked_use_allowed: Literal[False] = False
    scientific_conclusion: Literal[False] = False

    @field_validator("decided_at")
    @classmethod
    def validate_decided_at(cls, value: str) -> str:
        _timestamp(value)
        return value

    @model_validator(mode="after")
    def validate_release(self) -> "LocalSensitivityNotRunReleaseV1":
        if (
            self.phase_authorization.execution_phase
            is not ExecutionPhase.DEVELOPMENT_LOCAL_SENSITIVITY
        ):
            raise ValueError("local not-run release binds a non-local authorization")
        _require_after(
            self.decided_at,
            self.phase_authorization.authorized_at,
            "local-sensitivity not-run decision",
        )
        _assert_addressed(
            self,
            id_field="release_id",
            sha_field="release_sha256",
            prefix="local-sensitivity-not-run-v1",
        )
        return self


def build_local_sensitivity_not_run_release_v1(
    *,
    phase_authorization: MainPhaseAuthorizationReleaseV1,
    decided_at: str,
) -> LocalSensitivityNotRunReleaseV1:
    authorization = _revalidate(
        phase_authorization, MainPhaseAuthorizationReleaseV1
    )
    assert_main_phase_authorization_exact_v1(authorization)
    return _build_addressed(
        LocalSensitivityNotRunReleaseV1,
        id_field="release_id",
        sha_field="release_sha256",
        prefix="local-sensitivity-not-run-v1",
        values={
            "phase_authorization": authorization,
            "decided_at": decided_at,
        },
    )


def assert_local_sensitivity_not_run_exact_v1(
    release: LocalSensitivityNotRunReleaseV1,
) -> None:
    value = _revalidate(release, LocalSensitivityNotRunReleaseV1)
    expected = build_local_sensitivity_not_run_release_v1(
        phase_authorization=value.phase_authorization,
        decided_at=value.decided_at,
    )
    if value != expected:
        raise ValueError("local-sensitivity not-run release does not exactly replay")


class LockedExecutionPlannedArmV1(StrictModel):
    """One preregistered locked comparison role and its full configuration."""

    role: AnalysisTraceRoleV2
    source_execution_phase: ExecutionPhase
    system_config: SystemConfigV1

    @model_validator(mode="after")
    def validate_arm(self) -> "LockedExecutionPlannedArmV1":
        expected_phase = _LOCKED_PLAN_SOURCE_PHASE.get(self.role)
        if expected_phase is None or self.source_execution_phase is not expected_phase:
            raise ValueError("locked plan role/source phase is not preregistered")
        expected_system = (
            ResearchSystemId.B0
            if self.role is AnalysisTraceRoleV2.B0
            else ResearchSystemId.FUSION
        )
        if self.system_config.system_id is not expected_system:
            raise ValueError("locked plan role relabels another SystemConfig")
        if self.system_config.local_semantic_model is not None:
            raise ValueError("locked plan cannot contain a local semantic model")
        return self


class LockedExecutionPlanV1(StrictModel):
    """Content-addressed pre-run plan referenced by lifecycle authorization.

    The lifecycle layer historically accepted an opaque
    ``LOCKED_EXECUTION_RELEASE`` reference.  This campaign-owned preimage gives
    that reference an exact meaning before any locked budget is frozen: one
    component-derivation release, one B0/full-Fusion release, and the three
    fixed leave-one releases over the same 60 locked cases.
    """

    schema_version: Literal["flatband-locked-execution-plan-v1"] = (
        "flatband-locked-execution-plan-v1"
    )
    plan_id: Identifier
    plan_sha256: Sha256
    locked_component_authorization: MainPhaseAuthorizationReleaseV1
    locked_primary_authorization: MainPhaseAuthorizationReleaseV1
    fusion_configuration: FusionConfigurationReleaseV1
    locked_case_ids: Annotated[
        tuple[Identifier, ...], Field(min_length=60, max_length=60)
    ]
    component_system_configs: Annotated[
        tuple[SystemConfigV1, ...], Field(min_length=4, max_length=4)
    ]
    analysis_arms: Annotated[
        tuple[LockedExecutionPlannedArmV1, ...], Field(min_length=5, max_length=5)
    ]
    frozen_at: Annotated[str, Field(min_length=20, max_length=40)]
    formal_execution_release_count: Literal[5] = 5
    component_cells_are_comparison_arms: Literal[False] = False
    local_sensitivity_or_e1_local_allowed: Literal[False] = False
    arbitrary_locked_role_allowed: Literal[False] = False
    results_or_labels_embedded: Literal[False] = False
    scientific_conclusion: Literal[False] = False

    @field_validator("frozen_at")
    @classmethod
    def validate_frozen_at(cls, value: str) -> str:
        _timestamp(value)
        return value

    @model_validator(mode="after")
    def validate_plan(self) -> "LockedExecutionPlanV1":
        component_auth = self.locked_component_authorization
        primary_auth = self.locked_primary_authorization
        if (
            component_auth.execution_phase
            is not ExecutionPhase.LOCKED_FUSION_COMPONENTS
            or primary_auth.execution_phase is not ExecutionPhase.LOCKED_PRIMARY
        ):
            raise ValueError("locked plan uses the wrong Main phase authorizations")
        if component_auth.pre_budget_closure_release != (
            primary_auth.pre_budget_closure_release
        ):
            raise ValueError("locked plan authorizations use different Main custody")
        if (
            self.locked_case_ids != component_auth.authorized_case_ids
            or self.locked_case_ids != primary_auth.authorized_case_ids
        ):
            raise ValueError("locked plan does not exactly freeze the authorized cases")
        component_systems = tuple(
            item.system_id for item in self.component_system_configs
        )
        if component_systems != _LOCKED_COMPONENT_SYSTEM_ORDER:
            raise ValueError("locked plan component configs are missing or reordered")
        if any(
            item.local_semantic_model is not None
            or item.system_id is ResearchSystemId.E1_LOCAL
            for item in self.component_system_configs
        ):
            raise ValueError("locked component plan cannot contain E1-local")
        roles = tuple(item.role for item in self.analysis_arms)
        if roles != _LOCKED_PLAN_ROLE_ORDER:
            raise ValueError("locked plan must retain the fixed five role family")
        if len(
            {
                (item.system_config.config_id, item.system_config.config_sha256)
                for item in self.analysis_arms
            }
        ) != len(self.analysis_arms):
            raise ValueError("locked planned roles alias one SystemConfig identity")

        full_components = self.fusion_configuration.fusion_components
        if (
            len(full_components) != 3
            or ResearchSystemId.E1 not in full_components
            or ResearchSystemId.E3 not in full_components
            or sum(
                item in {ResearchSystemId.E2_A, ResearchSystemId.E2_B}
                for item in full_components
            )
            != 1
        ):
            raise ValueError("locked full Fusion requires E1, one E2, and E3")
        by_role = {item.role: item.system_config for item in self.analysis_arms}
        expected_components = {
            AnalysisTraceRoleV2.FUSION: full_components,
            AnalysisTraceRoleV2.FUSION_MINUS_E1: tuple(
                item for item in full_components if item is not ResearchSystemId.E1
            ),
            AnalysisTraceRoleV2.FUSION_MINUS_E2: tuple(
                item
                for item in full_components
                if item not in {ResearchSystemId.E2_A, ResearchSystemId.E2_B}
            ),
            AnalysisTraceRoleV2.FUSION_MINUS_E3: tuple(
                item for item in full_components if item is not ResearchSystemId.E3
            ),
        }
        for role, components in expected_components.items():
            if by_role[role].fusion_components != components:
                raise ValueError("locked plan Fusion-minus configuration drifts")
        _require_after(
            self.frozen_at,
            max(
                component_auth.authorized_at,
                primary_auth.authorized_at,
                self.fusion_configuration.frozen_at,
                key=_timestamp,
            ),
            "locked execution plan freeze",
        )
        _assert_addressed(
            self,
            id_field="plan_id",
            sha_field="plan_sha256",
            prefix="locked-execution-plan-v1",
        )
        return self


def build_locked_execution_plan_v1(
    *,
    locked_component_authorization: MainPhaseAuthorizationReleaseV1,
    locked_primary_authorization: MainPhaseAuthorizationReleaseV1,
    fusion_configuration: FusionConfigurationReleaseV1,
    component_system_configs: Sequence[SystemConfigV1],
    analysis_arms: Sequence[LockedExecutionPlannedArmV1],
    frozen_at: str,
) -> LockedExecutionPlanV1:
    component_auth = _revalidate(
        locked_component_authorization, MainPhaseAuthorizationReleaseV1
    )
    primary_auth = _revalidate(
        locked_primary_authorization, MainPhaseAuthorizationReleaseV1
    )
    assert_main_phase_authorization_exact_v1(component_auth)
    assert_main_phase_authorization_exact_v1(primary_auth)
    config = _revalidate(fusion_configuration, FusionConfigurationReleaseV1)
    components = tuple(
        sorted(
            (_revalidate(item, SystemConfigV1) for item in component_system_configs),
            key=lambda item: item.system_id.value,
        )
    )
    role_order = {role: index for index, role in enumerate(_LOCKED_PLAN_ROLE_ORDER)}
    arms = tuple(
        sorted(
            (
                _revalidate(item, LockedExecutionPlannedArmV1)
                for item in analysis_arms
            ),
            key=lambda item: role_order.get(item.role, len(role_order)),
        )
    )
    return _build_addressed(
        LockedExecutionPlanV1,
        id_field="plan_id",
        sha_field="plan_sha256",
        prefix="locked-execution-plan-v1",
        values={
            "locked_component_authorization": component_auth,
            "locked_primary_authorization": primary_auth,
            "fusion_configuration": config,
            "locked_case_ids": primary_auth.authorized_case_ids,
            "component_system_configs": components,
            "analysis_arms": arms,
            "frozen_at": frozen_at,
        },
    )


def assert_locked_execution_plan_exact_v1(plan: LockedExecutionPlanV1) -> None:
    value = _revalidate(plan, LockedExecutionPlanV1)
    expected = build_locked_execution_plan_v1(
        locked_component_authorization=value.locked_component_authorization,
        locked_primary_authorization=value.locked_primary_authorization,
        fusion_configuration=value.fusion_configuration,
        component_system_configs=value.component_system_configs,
        analysis_arms=value.analysis_arms,
        frozen_at=value.frozen_at,
    )
    if value != expected:
        raise ValueError("locked execution plan does not exactly replay")


class FlatBandCampaignReleaseV1(StrictModel):
    """Private, content-addressed root of the complete formal campaign.

    Successful Pydantic validation is structural only.  Scientific/formal use
    additionally requires :func:`assert_flatband_campaign_release_exact_v1`
    with the ephemeral Pilot and Gold keys.
    """

    schema_version: Literal["flatband-campaign-release-v1"] = (
        "flatband-campaign-release-v1"
    )
    campaign_id: Identifier
    campaign_sha256: Sha256

    source_catalog_checkpoint: SourceCatalogCheckpointReleaseV1
    pilot_rounds: Annotated[
        tuple[PilotRoundArtifactsV3, ...], Field(min_length=1, max_length=2)
    ]

    main_sampling_policy: MainSamplingPolicyReleaseV1
    main_candidate_pool: MainCandidatePoolReleaseV1
    main_eligibility: MainEligibilityReleaseV1
    main_frozen_cases: MainFrozenCaseReleaseV1
    main_pre_budget_closure: MainPreBudgetClosureReleaseV1
    main_private_identity_attestation: PrivateExpertIdentityCustodianAttestationV2
    release_authority_policy: ReleaseAuthorityPolicyV1

    development_ablation_authorization: MainPhaseAuthorizationReleaseV1
    local_sensitivity_authorization: MainPhaseAuthorizationReleaseV1
    development_fusion_authorization: MainPhaseAuthorizationReleaseV1
    locked_fusion_components_authorization: MainPhaseAuthorizationReleaseV1
    locked_primary_authorization: MainPhaseAuthorizationReleaseV1
    development_ablation_execution: MainPhaseExecutionReleaseV1
    local_sensitivity_execution: LocalSensitivityNotRunReleaseV1
    development_fusion_execution: MainPhaseExecutionReleaseV1
    locked_fusion_components_execution: MainPhaseExecutionReleaseV1
    locked_primary_execution: MainPhaseExecutionReleaseV1
    locked_minus_e1_execution: MainPhaseExecutionReleaseV1
    locked_minus_e2_execution: MainPhaseExecutionReleaseV1
    locked_minus_e3_execution: MainPhaseExecutionReleaseV1

    development_ablation_gold: MainGoldReleaseV1
    development_ablation_gold_verifier: MainGoldFormalVerifierAttestationV1
    development_ablation_analysis: AnalysisInputReleaseV2
    development_ablation_analysis_attestation: FormalVerifierAttestationRefV1
    development_promotion: DevelopmentPromotionReleaseV1

    fusion_configuration: FusionConfigurationReleaseV1
    development_fusion_gold: MainGoldReleaseV1
    development_fusion_gold_verifier: MainGoldFormalVerifierAttestationV1
    development_fusion_analysis: AnalysisInputReleaseV2
    development_fusion_analysis_attestation: FormalVerifierAttestationRefV1
    development_fusion_gate: DevelopmentFusionGateReleaseV1

    locked_label_seal: LockedLabelSealV1
    locked_execution_plan: LockedExecutionPlanV1
    locked_test_authorization: LockedTestAuthorizationReleaseV1
    locked_unseal_genesis_ledger: LockedUnsealLedgerReleaseV1
    locked_unseal: LockedAnnotationUnsealReleaseV1
    locked_unseal_committed_ledger: LockedUnsealLedgerReleaseV1
    locked_gold: MainGoldReleaseV1
    locked_gold_verifier: MainGoldFormalVerifierAttestationV1
    locked_analysis: AnalysisInputReleaseV2
    locked_analysis_attestation: FormalVerifierAttestationRefV1

    protocol_deviations: Annotated[
        tuple[ProtocolDeviationReleaseV1, ...], Field(max_length=128)
    ] = ()
    claim_support: ClaimSupportReleaseV1
    public_projection: PublicBenchmarkProjectionV1
    scientific_reviewer_identity_attestations: Annotated[
        tuple[ScientificReviewerIdentityAttestationV1, ...],
        Field(min_length=2, max_length=2),
    ]
    scientific_reviewer_attestations: Annotated[
        tuple[ScientificReviewerAttestationV1, ...],
        Field(min_length=2, max_length=2),
    ]
    scientific_review: ScientificReviewReleaseV1
    release_control_attestations: Annotated[
        tuple[ReleaseControlAttestationV1, ...],
        Field(min_length=3, max_length=3),
    ]
    public_release_authorization: PublicReleaseAuthorizationV1
    public_result: PublicBenchmarkResultReleaseV1

    assembled_at: Annotated[str, Field(min_length=20, max_length=40)]
    private_custody_required: Literal[True] = True
    public_repository_release_allowed: Literal[False] = False
    ephemeral_key_material_embedded: Literal[False] = False
    chain_of_thought_stored: Literal[False] = False
    local_semantic_model_campaign_path_allowed: Literal[False] = False
    external_execution_attestation: Literal["NOT_PROVIDED"] = "NOT_PROVIDED"
    external_expert_identity_attestation: Literal["NOT_PROVIDED"] = (
        "NOT_PROVIDED"
    )
    external_annotation_key_custody_attestation: Literal["NOT_PROVIDED"] = (
        "NOT_PROVIDED"
    )
    external_unseal_compare_and_swap_attestation: Literal["NOT_PROVIDED"] = (
        "NOT_PROVIDED"
    )
    external_publication_permission_claimed: Literal[False] = False
    global_unseal_uniqueness_claimed: Literal[False] = False
    real_main_structure_output_executed: Literal[False] = False
    benchmark_performance_claimed: Literal[False] = False
    scientific_conclusion: Literal[False] = False

    @field_validator("assembled_at")
    @classmethod
    def validate_time(cls, value: str) -> str:
        _timestamp(value)
        return value

    @model_validator(mode="after")
    def validate_campaign(self) -> "FlatBandCampaignReleaseV1":
        _assert_campaign_structural_links_v1(self)
        _assert_addressed(
            self,
            id_field="campaign_id",
            sha_field="campaign_sha256",
            prefix="flatband-campaign-v1",
        )
        return self


def _phase_authorizations(
    value: FlatBandCampaignReleaseV1,
) -> tuple[MainPhaseAuthorizationReleaseV1, ...]:
    return (
        value.development_ablation_authorization,
        value.local_sensitivity_authorization,
        value.development_fusion_authorization,
        value.locked_fusion_components_authorization,
        value.locked_primary_authorization,
    )


def _phase_executions(
    value: FlatBandCampaignReleaseV1,
) -> tuple[MainPhaseExecutionReleaseV1, ...]:
    return (
        value.development_ablation_execution,
        value.development_fusion_execution,
        value.locked_fusion_components_execution,
        value.locked_primary_execution,
        value.locked_minus_e1_execution,
        value.locked_minus_e2_execution,
        value.locked_minus_e3_execution,
    )


def _locked_parent_executions(
    value: FlatBandCampaignReleaseV1,
) -> tuple[MainPhaseExecutionReleaseV1, ...]:
    return (
        value.locked_primary_execution,
        value.locked_minus_e1_execution,
        value.locked_minus_e2_execution,
        value.locked_minus_e3_execution,
    )


def _assert_component_execution_links_v1(
    value: FlatBandCampaignReleaseV1,
) -> None:
    """Bind every Fusion trace to the one preregistered component release."""

    development_components = value.development_ablation_execution
    if (
        development_components.component_execution_release is not None
        or value.locked_fusion_components_execution.component_execution_release
        is not None
    ):
        raise ValueError("component execution release cannot recurse")
    if value.development_fusion_execution.component_execution_release != (
        development_components
    ):
        raise ValueError(
            "development Fusion does not reuse the exact ablation execution"
        )

    locked_components = value.locked_fusion_components_execution
    locked_parents = _locked_parent_executions(value)
    if any(
        item.component_execution_release != locked_components
        for item in locked_parents
    ):
        raise ValueError(
            "locked primary/minus releases do not share one component execution"
        )
    primary_preimage = value.locked_primary_execution.execution_release
    if any(
        item.locked_primary_execution_preimage != primary_preimage
        for item in locked_parents[1:]
    ):
        raise ValueError("locked Fusion-minus release embeds another primary preimage")

    plan = value.locked_execution_plan
    actual_component_configs = tuple(
        sorted(
            locked_components.execution_release.execution_matrix.system_configs,
            key=lambda item: item.system_id.value,
        )
    )
    if actual_component_configs != plan.component_system_configs:
        raise ValueError(
            "locked component execution configs differ from the authorized plan"
        )

    actual_arms: dict[
        AnalysisTraceRoleV2, tuple[ExecutionPhase, SystemConfigV1]
    ] = {}
    for release in locked_parents:
        for cell in release.cells:
            observed = (release.source_execution_phase, cell.system_config)
            prior = actual_arms.setdefault(cell.analysis_role, observed)
            if prior != observed:
                raise ValueError("locked role uses multiple execution configurations")
    planned_arms = tuple(
        LockedExecutionPlannedArmV1(
            role=role,
            source_execution_phase=phase,
            system_config=config,
        )
        for role in _LOCKED_PLAN_ROLE_ORDER
        for phase, config in (actual_arms.get(role, (None, None)),)
        if phase is not None and config is not None
    )
    if planned_arms != plan.analysis_arms:
        raise ValueError("locked execution role/config preimages differ from the plan")


def _locked_execution_timestamp_rows_v1(
    value: FlatBandCampaignReleaseV1,
) -> tuple[tuple[str, str], ...]:
    """Enumerate every timestamped locked runner artifact for the unseal cut."""

    releases = (
        value.locked_fusion_components_execution,
        *_locked_parent_executions(value),
    )
    rows: list[tuple[str, str]] = []
    for release in releases:
        execution = release.execution_release
        rows.append(("ExecutionReleaseV2 assembly", execution.assembled_at))
        rows.append(("Main phase execution assembly", release.assembled_at))
        rows.extend(
            ("locked budget freeze", item.frozen_at)
            for item in execution.budget_manifests
        )
        rows.extend(
            ("locked ranking", item.created_at) for item in execution.rankings
        )
        rows.extend(
            ("locked terminal", item.completed_at)
            for item in execution.terminal_results
        )
        for terminal in execution.terminal_results:
            for bundle in terminal.source_receipt_bundles:
                rows.extend(
                    ("locked logical-query receipt", item.created_at)
                    for item in bundle.logical_queries
                )
                rows.extend(
                    ("locked logical-page receipt", item.completed_at)
                    for item in bundle.logical_pages
                )
                rows.extend(
                    ("locked physical-hop receipt", item.completed_at)
                    for item in bundle.physical_hops
                )
        for cell in release.cells:
            traces = (
                *((cell.arm_trace,) if cell.arm_trace is not None else ()),
                *cell.fusion_component_traces,
            )
            for trace in traces:
                rows.append(("locked Arm trace", trace.assembled_at))
                for receipt in trace.model_native_receipts:
                    rows.append(("locked model-native receipt start", receipt.started_at))
                    rows.append(
                        ("locked model-native receipt completion", receipt.completed_at)
                    )
    return tuple(rows)


def _assert_locked_execution_chronology_v1(
    value: FlatBandCampaignReleaseV1,
) -> None:
    """Enforce authorization -> components -> parents -> one-shot unseal."""

    authorized_at = value.locked_test_authorization.authorized_at
    unsealed_at = value.locked_unseal.unsealed_at
    _require_after(
        authorized_at,
        value.locked_execution_plan.frozen_at,
        "locked-test lifecycle authorization",
    )
    releases = (
        value.locked_fusion_components_execution,
        *_locked_parent_executions(value),
    )
    if any(
        _timestamp(budget.frozen_at) <= _timestamp(authorized_at)
        for release in releases
        for budget in release.execution_release.budget_manifests
    ):
        raise ValueError("locked budget must follow lifecycle authorization")

    component = value.locked_fusion_components_execution
    parent_budgets = tuple(
        budget
        for release in _locked_parent_executions(value)
        for budget in release.execution_release.budget_manifests
    )
    if not parent_budgets:
        raise ValueError("locked parent execution has no budgets")
    first_parent_budget_at = min(
        (item.frozen_at for item in parent_budgets), key=_timestamp
    )
    if _timestamp(component.assembled_at) >= _timestamp(first_parent_budget_at):
        raise ValueError("locked component release must precede every parent budget")
    if any(
        _timestamp(cell.arm_trace.assembled_at)
        >= _timestamp(first_parent_budget_at)
        for cell in component.cells
        if cell.arm_trace is not None
    ):
        raise ValueError("locked component trace must precede every parent budget")

    for label, timestamp in _locked_execution_timestamp_rows_v1(value):
        if _timestamp(timestamp) >= _timestamp(unsealed_at):
            raise ValueError(f"{label} must precede locked-label unseal")


def _assert_locked_gold_human_inputs_post_unseal_v1(
    value: FlatBandCampaignReleaseV1,
) -> None:
    """Forbid signed locked human judgments before the one-shot unseal."""

    unsealed_at = value.locked_unseal.unsealed_at
    gold = value.locked_gold
    rows = (
        *(
            ("locked raw label", item.submitted_at)
            for item in gold.raw_labels
        ),
        *(
            ("locked raw duplicate partition", item.submitted_at)
            for item in gold.raw_duplicate_partitions
        ),
        *(
            ("locked label adjudication", item.adjudicated_at)
            for item in gold.adjudications
        ),
        *(
            ("locked duplicate adjudication", item.adjudicated_at)
            for item in gold.duplicate_adjudications
        ),
    )
    for label, timestamp in rows:
        _require_after(timestamp, unsealed_at, label)


def _assert_public_authority_structural_links_v1(
    value: FlatBandCampaignReleaseV1,
) -> None:
    """Close the public trust chain without persisting any signing key."""

    policy = value.release_authority_policy
    if value.public_release_authorization.authority_policy != policy:
        raise ValueError("public authorization embeds a foreign authority policy")
    _require_after(
        value.main_pre_budget_closure.sealed_at,
        policy.frozen_at,
        "Main pre-budget closure",
    )

    identities = value.scientific_reviewer_identity_attestations
    reviews = value.scientific_reviewer_attestations
    if tuple(item.reviewer_identity_attestation for item in reviews) != identities:
        raise ValueError(
            "scientific reviews differ from the exact signed identity preimages"
        )
    reviewer_ids = tuple(item.reviewer_id for item in identities)
    identity_keys = tuple(
        (
            item.reviewer_id,
            item.identity_attestation_id,
            item.identity_attestation_sha256,
        )
        for item in identities
    )
    person_commitments = tuple(
        item.natural_person_commitment_sha256 for item in identities
    )
    private_evidence_roots = tuple(
        item.private_identity_evidence_sha256 for item in identities
    )
    decision_key_commitments = tuple(
        item.reviewer_decision_key_commitment_sha256 for item in identities
    )
    if (
        len(reviewer_ids) != len(set(reviewer_ids))
        or len(identity_keys) != len(set(identity_keys))
        or len(person_commitments) != len(set(person_commitments))
        or len(private_evidence_roots) != len(set(private_evidence_roots))
        or len(decision_key_commitments) != len(set(decision_key_commitments))
    ):
        raise ValueError(
            "scientific reviewer identities, persons, evidence roots, and decision "
            "key commitments must be distinct"
        )
    main_person_commitments = {
        item.natural_person_commitment_sha256
        for item in value.main_private_identity_attestation.bindings
    }
    if not set(person_commitments).isdisjoint(main_person_commitments):
        raise ValueError(
            "scientific reviewer natural person overlaps a Main benchmark expert"
        )
    for identity, review in zip(identities, reviews):
        if (
            identity.authority_policy_id,
            identity.authority_policy_sha256,
        ) != (policy.policy_id, policy.policy_sha256):
            raise ValueError("scientific reviewer identity binds a foreign policy")
        _require_after(
            review.reviewed_at,
            identity.issued_at,
            "scientific reviewer attestation",
        )
        if review.findings:
            raise ValueError(
                "campaign scientific-review findings must use the empty frozen template"
            )

    controls = value.release_control_attestations
    if controls != value.public_release_authorization.release_control_attestations:
        raise ValueError(
            "public authorization differs from the exact release-control preimages"
        )
    if tuple(item.control_kind for item in controls) != tuple(
        sorted(ReleaseControlKindV1, key=lambda item: item.value)
    ):
        raise ValueError("campaign requires license/privacy/custody controls exactly once")
    for control in controls:
        if (
            control.authority_policy_id,
            control.authority_policy_sha256,
        ) != (policy.policy_id, policy.policy_sha256):
            raise ValueError("release-control attestation binds a foreign policy")
        _require_after(
            value.public_release_authorization.authorized_at,
            control.issued_at,
            "public release authorization",
        )


def _assert_release_authority_policy_exact_v1(
    policy: ReleaseAuthorityPolicyV1,
    authority_keys: Mapping[ReleaseAuthorityRoleV1, bytes],
) -> None:
    """Replay the four key commitments from caller-held ephemeral keys."""

    if set(authority_keys) != set(ReleaseAuthorityRoleV1):
        raise ValueError("release authority replay requires exactly four role keys")
    authority_ids = {item.role: item.authority_id for item in policy.authorities}
    expected = build_release_authority_policy_v1(
        authorities={
            role: (authority_ids[role], authority_keys[role])
            for role in ReleaseAuthorityRoleV1
        },
        frozen_at=policy.frozen_at,
    )
    if expected != policy:
        raise ValueError("release authority policy does not exactly replay")


def _assert_reviewer_decision_keys_exact_v1(
    identities: Sequence[ScientificReviewerIdentityAttestationV1],
    reviewer_decision_keys: Mapping[str, bytes],
) -> None:
    reviewer_ids = {item.reviewer_id for item in identities}
    if set(reviewer_decision_keys) != reviewer_ids:
        raise ValueError(
            "scientific review replay requires exactly one decision key per reviewer"
        )
    for identity in identities:
        key = reviewer_decision_keys[identity.reviewer_id]
        if not isinstance(key, bytes) or len(key) < 32:
            raise ValueError("scientific reviewer decision key is invalid")
        if hashlib.sha256(key).hexdigest() != (
            identity.reviewer_decision_key_commitment_sha256
        ):
            raise ValueError(
                "scientific reviewer decision key differs from signed identity"
            )


def _assert_main_gold_expert_annotation_keys_exact_v1(
    value: FlatBandCampaignReleaseV1,
    phase_keys: Mapping[ExecutionPhase, Mapping[str, bytes]],
) -> None:
    """Bind each ephemeral expert key to its phase-frozen Gold assignment."""

    if set(phase_keys) != set(_GOLD_PHASE_ORDER):
        raise ValueError(
            "Main Gold expert annotation keys require exactly three phase maps"
        )
    golds = {
        ExecutionPhase.DEVELOPMENT_ABLATIONS: value.development_ablation_gold,
        ExecutionPhase.DEVELOPMENT_FUSION: value.development_fusion_gold,
        ExecutionPhase.LOCKED_PRIMARY: value.locked_gold,
    }
    for phase, gold in golds.items():
        participants = (
            *gold.expert_assignment.reviewers,
            *gold.expert_assignment.adjudicators,
        )
        commitments = {
            item.expert_id: item.annotation_key_commitment_sha256
            for item in participants
        }
        keys = phase_keys[phase]
        if set(keys) != set(commitments):
            raise ValueError(
                "Main Gold expert annotation keys differ from the phase assignment"
            )
        for expert_id, commitment in commitments.items():
            key = keys[expert_id]
            if not isinstance(key, bytes) or len(key) < 32:
                raise ValueError("Main Gold expert annotation key is invalid")
            if hashlib.sha256(key).hexdigest() != commitment:
                raise ValueError(
                    "Main Gold expert annotation key differs from frozen commitment"
                )


def _assert_campaign_structural_links_v1(
    value: FlatBandCampaignReleaseV1,
) -> None:
    rounds = tuple(item.review_round for item in value.pilot_rounds)
    if rounds not in {(1,), (1, 2)}:
        raise ValueError("campaign Pilot rounds must be terminal R1 or ordered R1/R2")
    if any(
        item.source_catalog_checkpoint != value.source_catalog_checkpoint
        for item in value.pilot_rounds
    ):
        raise ValueError("campaign Pilot rounds use a foreign source checkpoint")
    terminal_pilot = value.pilot_rounds[-1]
    if value.main_candidate_pool.terminal_pilot_gate != terminal_pilot.agreement_gate:
        raise ValueError("Main custody does not bind the terminal Pilot Gate")
    if (
        value.main_candidate_pool.sampling_policy_release
        != value.main_sampling_policy
        or value.main_eligibility.candidate_pool_release
        != value.main_candidate_pool
        or value.main_frozen_cases.eligibility_release != value.main_eligibility
        or value.main_pre_budget_closure.frozen_case_release
        != value.main_frozen_cases
    ):
        raise ValueError("campaign Main custody chain is cross-wired")

    authorizations = _phase_authorizations(value)
    if tuple(item.execution_phase for item in authorizations) != _FORMAL_PHASE_ORDER:
        raise ValueError("campaign must retain all five formal Main authorizations")
    if any(
        item.pre_budget_closure_release != value.main_pre_budget_closure
        for item in authorizations
    ):
        raise ValueError("Main phase authorization binds a foreign pre-budget closure")
    if (
        value.local_sensitivity_execution.phase_authorization
        != value.local_sensitivity_authorization
    ):
        raise ValueError("local-sensitivity not-run closure binds a foreign authorization")
    executions = _phase_executions(value)
    if tuple(item.source_execution_phase for item in executions) != (
        _FORMAL_EXECUTION_SOURCE_PHASE_ORDER
    ):
        raise ValueError("campaign execution roots must cover primary and three minus phases")
    expected_execution_authorizations = (
        value.development_ablation_authorization,
        value.development_fusion_authorization,
        value.locked_fusion_components_authorization,
        value.locked_primary_authorization,
        value.locked_primary_authorization,
        value.locked_primary_authorization,
        value.locked_primary_authorization,
    )
    if any(
        execution.phase_authorization != authorization
        for execution, authorization in zip(
            executions, expected_execution_authorizations
        )
    ):
        raise ValueError("campaign execution release uses a foreign authorization")
    component_execution = value.locked_fusion_components_execution
    if component_execution.phase_authorization != (
        value.locked_fusion_components_authorization
    ):
        raise ValueError("locked component execution uses a foreign authorization")
    if component_execution.execution_release.execution_matrix.phase is not (
        ExecutionPhase.LOCKED_FUSION_COMPONENTS
    ):
        raise ValueError("locked component execution uses a foreign source phase")
    if (
        value.locked_execution_plan.locked_component_authorization
        != value.locked_fusion_components_authorization
        or value.locked_execution_plan.locked_primary_authorization
        != value.locked_primary_authorization
        or value.locked_execution_plan.fusion_configuration
        != value.fusion_configuration
    ):
        raise ValueError("locked execution plan is cross-wired")
    locked_plan_ref = value.locked_test_authorization.locked_execution_ref
    if (
        locked_plan_ref.artifact_type
        is not LifecycleArtifactType.LOCKED_EXECUTION_RELEASE
        or locked_plan_ref.artifact_id != value.locked_execution_plan.plan_id
        or locked_plan_ref.artifact_sha256
        != value.locked_execution_plan.plan_sha256
    ):
        raise ValueError(
            "locked-test authorization does not reference the exact execution plan"
        )
    _assert_component_execution_links_v1(value)
    _assert_locked_execution_chronology_v1(value)

    golds = (
        value.development_ablation_gold,
        value.development_fusion_gold,
        value.locked_gold,
    )
    analyses = (
        value.development_ablation_analysis,
        value.development_fusion_analysis,
        value.locked_analysis,
    )
    gold_verifiers = (
        value.development_ablation_gold_verifier,
        value.development_fusion_gold_verifier,
        value.locked_gold_verifier,
    )
    if tuple(item.phase for item in golds) != _GOLD_PHASE_ORDER:
        raise ValueError("campaign Gold releases differ from the three formal phases")
    if tuple(item.execution_phase for item in analyses) != _GOLD_PHASE_ORDER:
        raise ValueError("campaign Analysis releases differ from the three formal phases")
    if tuple(item.phase for item in gold_verifiers) != _GOLD_PHASE_ORDER:
        raise ValueError("campaign Gold verifiers differ from the three formal phases")
    for gold, attestation in zip(golds, gold_verifiers):
        if (attestation.gold_release_id, attestation.gold_release_sha256) != (
            gold.release_id,
            gold.release_sha256,
        ):
            raise ValueError("Main Gold formal verifier binds a foreign Gold release")
        _require_after(attestation.verified_at, gold.released_at, "Gold verification")

    analysis_attestations = (
        value.development_ablation_analysis_attestation,
        value.development_fusion_analysis_attestation,
        value.locked_analysis_attestation,
    )
    expected_payloads = (
        VerifiedPayloadKind.DEVELOPMENT_ABLATION_METRIC_ROWS,
        VerifiedPayloadKind.DEVELOPMENT_FUSION_METRIC_ROWS,
        VerifiedPayloadKind.LOCKED_RESULT_FAMILY,
    )
    for analysis, attestation, payload_kind, corresponding_gold in zip(
        analyses, analysis_attestations, expected_payloads, golds
    ):
        if (
            attestation.analysis_input_ref != analysis_input_ref_v2(analysis)
            or attestation.verified_payload_kind is not payload_kind
        ):
            raise ValueError("Analysis attestation binds a foreign release/payload")
        _require_after(
            analysis.assembled_at,
            corresponding_gold.released_at,
            "Analysis assembly",
        )
        _require_after(
            attestation.attested_at,
            analysis.assembled_at,
            "Analysis formal attestation",
        )

    _require_after(
        value.development_fusion_authorization.authorized_at,
        value.fusion_configuration.frozen_at,
        "development Fusion authorization",
    )
    _require_after(
        value.locked_test_authorization.authorized_at,
        value.development_fusion_gate.evaluated_at,
        "locked-test authorization",
    )
    _require_after(
        value.locked_unseal.unsealed_at,
        value.locked_test_authorization.authorized_at,
        "locked unseal",
    )
    _require_after(
        value.locked_gold.released_at,
        value.locked_unseal.unsealed_at,
        "locked Gold release",
    )
    _assert_locked_gold_human_inputs_post_unseal_v1(value)

    if value.locked_unseal_genesis_ledger.revision != 0:
        raise ValueError("campaign must embed the exact unseal genesis ledger")
    if value.locked_unseal_committed_ledger.revision != 1:
        raise ValueError("campaign must embed the exact committed unseal revision one")
    if value.locked_unseal_committed_ledger.unseal_event != value.locked_unseal:
        raise ValueError("committed unseal ledger differs from the one unseal event")
    if value.locked_unseal_genesis_ledger.external_compare_and_swap_attestation != "NOT_PROVIDED":
        raise ValueError("campaign cannot claim an external unseal CAS attestation")
    if value.locked_unseal_committed_ledger.external_compare_and_swap_attestation != "NOT_PROVIDED":
        raise ValueError("campaign cannot claim an external unseal CAS attestation")
    if (
        value.scientific_reviewer_attestations
        != value.scientific_review.reviewer_attestations
    ):
        raise ValueError("scientific review differs from the exact two attestations")
    if value.public_result.projection != value.public_projection:
        raise ValueError("public result differs from the reviewed public projection")
    if set(value.public_projection.limitation_codes) != (
        _IMPLEMENTATION_ONLY_PUBLIC_LIMITATIONS
    ):
        raise ValueError(
            "implementation-only campaign must disclose the complete frozen "
            "scope/provider/performance limitation family"
        )
    deviations = tuple(
        (item.release_id, item.release_sha256) for item in value.protocol_deviations
    )
    if deviations != tuple(sorted(set(deviations))):
        raise ValueError("campaign protocol deviations must be sorted and unique")
    _assert_public_authority_structural_links_v1(value)
    _require_after(value.assembled_at, value.public_result.released_at, "campaign assembly")


def _assert_identity_registry_join_v1(value: FlatBandCampaignReleaseV1) -> None:
    registry = value.main_frozen_cases.expert_registry
    private = value.main_private_identity_attestation
    assert_distinct_natural_person_assignments_v2(
        private_identity_attestation=private,
        registry=registry,
    )
    binding_by_expert = {item.expert_id: item for item in private.bindings}
    if (
        registry.identity_attestation_id,
        registry.identity_attestation_sha256,
    ) != (private.attestation_id, private.attestation_sha256):
        raise ValueError("Main registry binds a foreign private identity attestation")
    registry_assignments = {item.case_id: item for item in registry.assignments}
    authorization_by_phase = {
        ExecutionPhase.DEVELOPMENT_ABLATIONS: (
            value.development_ablation_authorization
        ),
        ExecutionPhase.DEVELOPMENT_FUSION: value.development_fusion_authorization,
        ExecutionPhase.LOCKED_PRIMARY: value.locked_primary_authorization,
    }
    for gold in (
        value.development_ablation_gold,
        value.development_fusion_gold,
        value.locked_gold,
    ):
        assignment = gold.expert_assignment
        if (
            assignment.expert_registry.registry_id,
            assignment.expert_registry.registry_sha256,
        ) != (registry.registry_id, registry.registry_sha256):
            raise ValueError("Main Gold assignment embeds a foreign expert registry")
        reviewer_ids = tuple(item.expert_id for item in assignment.reviewers)
        adjudicator_ids = tuple(item.expert_id for item in assignment.adjudicators)
        phase_case_ids = authorization_by_phase[gold.phase].authorized_case_ids
        for case_id in phase_case_ids:
            registered = registry_assignments.get(case_id)
            if registered is None:
                raise ValueError("Main Gold includes a case outside the expert registry")
            if tuple(registered.reviewer_ids) != reviewer_ids:
                raise ValueError("Main Gold reviewer pair differs from registry assignment")
            if registered.adjudicator_id not in adjudicator_ids:
                raise ValueError("Main Gold adjudicator differs from registry assignment")
        expected_adjudicators = tuple(
            sorted(
                {
                    registry_assignments[case_id].adjudicator_id
                    for case_id in phase_case_ids
                }
            )
        )
        if adjudicator_ids != expected_adjudicators:
            raise ValueError(
                "Main Gold adjudicator set does not exactly cover phase assignments"
            )
        for participant in (*assignment.reviewers, *assignment.adjudicators):
            binding = binding_by_expert.get(participant.expert_id)
            if binding is None or (
                participant.natural_person_commitment_sha256
                != binding.natural_person_commitment_sha256
            ):
                raise ValueError("Main Gold natural-person commitment is not exact")


def _assert_formal_attestation_exact_v1(
    analysis: AnalysisInputReleaseV2,
    attestation: FormalVerifierAttestationRefV1,
    payload_kind: VerifiedPayloadKind,
) -> None:
    expected = build_formal_verifier_attestation_v1(
        analysis,
        verified_payload_kind=payload_kind,
        attested_at=attestation.attested_at,
    )
    if expected != attestation:
        raise ValueError("Analysis V2 formal attestation does not exactly replay")


def _assert_analysis_main_context_v1(
    *,
    analysis: AnalysisInputReleaseV2,
    authorization: MainPhaseAuthorizationReleaseV1,
    frozen: MainFrozenCaseReleaseV1,
) -> None:
    manifest = frozen.split_manifest
    leakage = frozen.leakage_release
    if analysis.split_context_ref != AnalysisArtifactRefV2(
        artifact_type=AnalysisArtifactTypeV2.SPLIT_CONTEXT,
        artifact_id=manifest.manifest_id,
        artifact_sha256=manifest.manifest_sha256,
    ):
        raise ValueError("Analysis split context does not bind the frozen Main manifest")
    if analysis.leakage_context_ref != AnalysisArtifactRefV2(
        artifact_type=AnalysisArtifactTypeV2.LEAKAGE_CONTEXT,
        artifact_id=leakage.release_id,
        artifact_sha256=leakage.release_sha256,
    ):
        raise ValueError("Analysis leakage context does not bind frozen Main leakage")
    component_by_case: dict[str, str] = {}
    for component in leakage.components:
        for case_id in component.case_ids:
            if case_id in component_by_case:
                raise ValueError("one Main case occurs in multiple leakage components")
            component_by_case[case_id] = component.component_id
    manifest_by_case = {item.case_id: item for item in manifest.cases}
    expected = tuple(
        (
            case_id,
            manifest_by_case[case_id].case_sha256,
            manifest_by_case[case_id].split,
            component_by_case[case_id],
        )
        for case_id in authorization.authorized_case_ids
    )
    observed = tuple(
        (item.case_id, item.case_sha256, item.split, item.leakage_component_id)
        for item in analysis.cases
    )
    if observed != expected:
        raise ValueError("Analysis cases do not exactly project the authorized Main cases")


def _config_invariant_surface(config: SystemConfigV1) -> dict[str, object]:
    """Fields that no preregistered intervention is allowed to change."""

    return config.model_dump(
        mode="python",
        exclude={
            "config_id",
            "config_sha256",
            "system_id",
            "source_variant",
            "source_budgets",
            "cross_domain_tag_graph_sha256",
            "llm",
            "local_semantic_model",
            "fusion_components",
        },
    )


def _one_config_per_role(
    analysis: AnalysisInputReleaseV2,
) -> dict[AnalysisTraceRoleV2, SystemConfigV1]:
    result: dict[AnalysisTraceRoleV2, SystemConfigV1] = {}
    for cell in analysis.cells:
        config = cell.trace_evidence.system_config
        prior = result.setdefault(cell.role, config)
        if prior != config:
            raise ValueError("one Analysis role uses multiple SystemConfig preimages")
    if set(result) != set(analysis.roles):
        raise ValueError("Analysis role/config surface is incomplete")
    return result


def _assert_fusion_projection_config_v1(
    *,
    config: SystemConfigV1,
    components: tuple[ResearchSystemId, ...],
    isolated: Mapping[ResearchSystemId, SystemConfigV1],
) -> None:
    if config.system_id is not ResearchSystemId.FUSION:
        raise ValueError("Fusion projection relabels a non-Fusion SystemConfig")
    if config.fusion_components != components:
        raise ValueError("Fusion SystemConfig components drift from frozen selection")
    if ResearchSystemId.E1_LOCAL in components or config.local_semantic_model is not None:
        raise ValueError("E1-local is forbidden from Fusion/promotion/locked paths")
    baseline = isolated[ResearchSystemId.B0]
    if _config_invariant_surface(config) != _config_invariant_surface(baseline):
        raise ValueError(
            "Fusion prompt/query/projection/ranking/cache/TagGraph base hashes drift"
        )
    e2 = next(
        (
            item
            for item in components
            if item in {ResearchSystemId.E2_A, ResearchSystemId.E2_B}
        ),
        None,
    )
    source_config = baseline if e2 is None else isolated[e2]
    expected_llm = (
        isolated[ResearchSystemId.E1].llm
        if ResearchSystemId.E1 in components
        else None
    )
    expected_graph = (
        isolated[ResearchSystemId.E3].cross_domain_tag_graph_sha256
        if ResearchSystemId.E3 in components
        else None
    )
    if (
        config.source_variant,
        config.source_budgets,
        config.llm,
        config.cross_domain_tag_graph_sha256,
        config.baseline_tag_graph_sha256,
    ) != (
        source_config.source_variant,
        source_config.source_budgets,
        expected_llm,
        expected_graph,
        baseline.baseline_tag_graph_sha256,
    ):
        raise ValueError(
            "Fusion SystemConfig is not the exact preregistered intervention projection"
        )


def _assert_configuration_lineage_v1(value: FlatBandCampaignReleaseV1) -> None:
    ablation = _one_config_per_role(value.development_ablation_analysis)
    direct_roles = {
        AnalysisTraceRoleV2.B0: ResearchSystemId.B0,
        AnalysisTraceRoleV2.E1: ResearchSystemId.E1,
        AnalysisTraceRoleV2.E2_A: ResearchSystemId.E2_A,
        AnalysisTraceRoleV2.E2_B: ResearchSystemId.E2_B,
        AnalysisTraceRoleV2.E3: ResearchSystemId.E3,
    }
    if set(ablation) != set(direct_roles):
        raise ValueError("development ablation configs do not cover B0/E1/E2-A/E2-B/E3")
    isolated = {system: ablation[role] for role, system in direct_roles.items()}
    baseline_surface = _config_invariant_surface(isolated[ResearchSystemId.B0])
    for system, config in isolated.items():
        if config.system_id is not system:
            raise ValueError("ablation role relabels another SystemConfig")
        if config.local_semantic_model is not None:
            raise ValueError("formal ablation path cannot contain a local semantic model")
        if _config_invariant_surface(config) != baseline_surface:
            raise ValueError(
                "isolated arm changes a non-preregistered query/prompt/cache/hash field"
            )
    if value.locked_execution_plan.component_system_configs != tuple(
        isolated[item] for item in _LOCKED_COMPONENT_SYSTEM_ORDER
    ):
        raise ValueError(
            "locked component execution plan drifts from development ablation configs"
        )

    expected_component_systems = (
        ResearchSystemId.B0,
        *value.development_promotion.promoted_components,
    )
    component_refs = value.fusion_configuration.component_configurations
    if tuple(item.system_id for item in component_refs) != expected_component_systems:
        raise ValueError("Fusion configuration omits or substitutes a promoted component")
    for item in component_refs:
        actual = isolated[item.system_id]
        if (
            item.configuration_ref.artifact_type
            is not LifecycleArtifactType.SYSTEM_CONFIGURATION
            or item.configuration_ref.artifact_id != actual.config_id
            or item.configuration_ref.artifact_sha256 != actual.config_sha256
        ):
            raise ValueError(
                "Fusion component ref does not point to the actual ablation SystemConfig"
            )

    development = _one_config_per_role(value.development_fusion_analysis)
    if set(development) != {
        AnalysisTraceRoleV2.B0,
        AnalysisTraceRoleV2.FUSION,
    }:
        raise ValueError("development Fusion must compare exact B0 and full Fusion")
    if development[AnalysisTraceRoleV2.B0] != isolated[ResearchSystemId.B0]:
        raise ValueError("development Fusion substituted the frozen B0 configuration")
    full = development[AnalysisTraceRoleV2.FUSION]
    components = value.fusion_configuration.fusion_components
    if (
        len(components) != 3
        or ResearchSystemId.E1 not in components
        or ResearchSystemId.E3 not in components
        or sum(
            item in {ResearchSystemId.E2_A, ResearchSystemId.E2_B}
            for item in components
        )
        != 1
    ):
        raise ValueError("full-flow campaign requires promoted E1, one E2, and E3")
    _assert_fusion_projection_config_v1(
        config=full,
        components=components,
        isolated=isolated,
    )

    locked = _one_config_per_role(value.locked_analysis)
    expected_locked_roles = {
        AnalysisTraceRoleV2.B0,
        AnalysisTraceRoleV2.FUSION,
        AnalysisTraceRoleV2.FUSION_MINUS_E1,
        AnalysisTraceRoleV2.FUSION_MINUS_E2,
        AnalysisTraceRoleV2.FUSION_MINUS_E3,
    }
    if set(locked) != expected_locked_roles:
        raise ValueError("locked Analysis must retain the fixed five role family")
    planned_by_role = {
        item.role: item.system_config
        for item in value.locked_execution_plan.analysis_arms
    }
    if locked != planned_by_role:
        raise ValueError("locked Analysis configurations differ from execution plan")
    if locked[AnalysisTraceRoleV2.B0] != isolated[ResearchSystemId.B0]:
        raise ValueError("locked test substituted the frozen B0 configuration")
    if locked[AnalysisTraceRoleV2.FUSION] != full:
        raise ValueError("locked full Fusion differs from development-frozen config")
    e2_components = {
        item
        for item in components
        if item in {ResearchSystemId.E2_A, ResearchSystemId.E2_B}
    }
    removals = {
        AnalysisTraceRoleV2.FUSION_MINUS_E1: {ResearchSystemId.E1},
        AnalysisTraceRoleV2.FUSION_MINUS_E2: e2_components,
        AnalysisTraceRoleV2.FUSION_MINUS_E3: {ResearchSystemId.E3},
    }
    for role, removed in removals.items():
        minus_components = tuple(item for item in components if item not in removed)
        if not removed or not minus_components:
            raise ValueError(
                "fixed locked minus hypothesis lacks its promoted component/config"
            )
        _assert_fusion_projection_config_v1(
            config=locked[role],
            components=minus_components,
            isolated=isolated,
        )


def assert_flatband_campaign_release_exact_v1(
    release: FlatBandCampaignReleaseV1,
    *,
    pilot_blinding_keys: Mapping[int, bytes],
    main_gold_blinding_keys: Mapping[ExecutionPhase, bytes],
    main_gold_expert_annotation_keys: Mapping[
        ExecutionPhase, Mapping[str, bytes]
    ],
    release_authority_keys: Mapping[ReleaseAuthorityRoleV1, bytes],
    scientific_reviewer_decision_keys: Mapping[str, bytes],
) -> None:
    """Replay the complete campaign without running a model or benchmark."""

    value = _revalidate(release, FlatBandCampaignReleaseV1)
    if set(pilot_blinding_keys) != {item.review_round for item in value.pilot_rounds}:
        raise ValueError("Pilot formal replay requires exactly one key per stored round")
    if set(main_gold_blinding_keys) != set(_GOLD_PHASE_ORDER):
        raise ValueError("Main Gold replay requires exactly three phase blinding keys")
    _assert_main_gold_expert_annotation_keys_exact_v1(
        value,
        main_gold_expert_annotation_keys,
    )
    _assert_release_authority_policy_exact_v1(
        value.release_authority_policy,
        release_authority_keys,
    )
    _assert_reviewer_decision_keys_exact_v1(
        value.scientific_reviewer_identity_attestations,
        scientific_reviewer_decision_keys,
    )

    prior: PilotRoundArtifactsV3 | None = None
    for pilot in value.pilot_rounds:
        assert_formal_pilot_round_artifacts_v3(
            pilot,
            blind_key=pilot_blinding_keys[pilot.review_round],
            prior_r1_bundle=prior,
            prior_r1_blind_key=(
                None if prior is None else pilot_blinding_keys[prior.review_round]
            ),
        )
        prior = pilot

    assert_main_sampling_policy_exact_v1(value.main_sampling_policy)
    assert_main_candidate_pool_exact_v1(value.main_candidate_pool)
    assert_main_eligibility_exact_v1(value.main_eligibility)
    assert_main_frozen_case_exact_v1(value.main_frozen_cases)
    assert_main_pre_budget_closure_exact_v1(value.main_pre_budget_closure)
    _assert_identity_registry_join_v1(value)

    for authorization in _phase_authorizations(value):
        assert_main_phase_authorization_exact_v1(authorization)
    assert_local_sensitivity_not_run_exact_v1(value.local_sensitivity_execution)
    assert_locked_execution_plan_exact_v1(value.locked_execution_plan)
    executions = _phase_executions(value)
    if tuple(item.source_execution_phase for item in executions) != (
        _FORMAL_EXECUTION_SOURCE_PHASE_ORDER
    ):
        raise ValueError("campaign formal execution phases are missing or reordered")
    expected_authorizations = (
        value.development_ablation_authorization,
        value.development_fusion_authorization,
        value.locked_fusion_components_authorization,
        value.locked_primary_authorization,
        value.locked_primary_authorization,
        value.locked_primary_authorization,
        value.locked_primary_authorization,
    )
    for execution, authorization in zip(executions, expected_authorizations):
        if execution.phase_authorization != authorization:
            raise ValueError("Main execution release binds a foreign phase authorization")
        assert_main_phase_execution_exact_v1(execution)
    _assert_component_execution_links_v1(value)
    _assert_locked_execution_chronology_v1(value)

    _assert_downstream_exact_v1(
        value,
        main_gold_blinding_keys,
        main_gold_expert_annotation_keys,
        release_authority_keys,
        scientific_reviewer_decision_keys,
    )
    _assert_addressed(
        value,
        id_field="campaign_id",
        sha_field="campaign_sha256",
        prefix="flatband-campaign-v1",
    )


def _execution_groups(
    value: FlatBandCampaignReleaseV1,
) -> dict[ExecutionPhase, tuple[MainPhaseExecutionReleaseV1, ...]]:
    return {
        ExecutionPhase.DEVELOPMENT_ABLATIONS: (
            value.development_ablation_execution,
        ),
        ExecutionPhase.DEVELOPMENT_FUSION: (
            value.development_fusion_execution,
        ),
        ExecutionPhase.LOCKED_PRIMARY: (
            value.locked_primary_execution,
            value.locked_minus_e1_execution,
            value.locked_minus_e2_execution,
            value.locked_minus_e3_execution,
        ),
    }


def _analysis_evidence_union_v1(
    releases: Sequence[MainPhaseExecutionReleaseV1],
) -> tuple[object, ...]:
    evidence = tuple(
        item
        for release in releases
        for item in build_analysis_trace_evidence_index_from_main_v1(release)
    )
    keys = tuple(
        (
            item.terminal_result.case_id,
            item.role.value,
            item.terminal_result.cell_id,
        )
        for item in evidence
    )
    if len(keys) != len(set(keys)):
        raise ValueError("Main execution union repeats one case/role/cell")
    return tuple(
        sorted(
            evidence,
            key=lambda item: (
                item.terminal_result.case_id,
                item.role.value,
                item.terminal_result.cell_id,
            ),
        )
    )


def _main_gold_execution_cells_union_v1(
    releases: Sequence[MainPhaseExecutionReleaseV1],
) -> tuple[object, ...]:
    """Return canonical Gold inputs, retaining every FAILED denominator cell."""

    cells = tuple(
        cell
        for release in releases
        for cell in main_gold_execution_cells_from_main_v1(release)
    )
    keys = tuple(
        (cell.case_id, cell.analysis_role.value, cell.terminal_result.cell_id)
        for cell in cells
    )
    if len(keys) != len(set(keys)):
        raise ValueError("Gold execution union repeats one case/role/cell")
    return tuple(
        sorted(
            cells,
            key=lambda cell: (
                cell.case_id,
                cell.analysis_role.value,
                cell.terminal_result.cell_id,
            ),
        )
    )


def _assert_downstream_exact_v1(
    value: FlatBandCampaignReleaseV1,
    gold_keys: Mapping[ExecutionPhase, bytes],
    gold_expert_annotation_keys: Mapping[
        ExecutionPhase, Mapping[str, bytes]
    ],
    release_authority_keys: Mapping[ReleaseAuthorityRoleV1, bytes],
    reviewer_decision_keys: Mapping[str, bytes],
) -> None:
    executions = _execution_groups(value)
    golds = {
        ExecutionPhase.DEVELOPMENT_ABLATIONS: value.development_ablation_gold,
        ExecutionPhase.DEVELOPMENT_FUSION: value.development_fusion_gold,
        ExecutionPhase.LOCKED_PRIMARY: value.locked_gold,
    }
    gold_verifiers = {
        ExecutionPhase.DEVELOPMENT_ABLATIONS: value.development_ablation_gold_verifier,
        ExecutionPhase.DEVELOPMENT_FUSION: value.development_fusion_gold_verifier,
        ExecutionPhase.LOCKED_PRIMARY: value.locked_gold_verifier,
    }
    analyses = {
        ExecutionPhase.DEVELOPMENT_ABLATIONS: value.development_ablation_analysis,
        ExecutionPhase.DEVELOPMENT_FUSION: value.development_fusion_analysis,
        ExecutionPhase.LOCKED_PRIMARY: value.locked_analysis,
    }
    analysis_authorizations = {
        ExecutionPhase.DEVELOPMENT_ABLATIONS: (
            value.development_ablation_authorization
        ),
        ExecutionPhase.DEVELOPMENT_FUSION: value.development_fusion_authorization,
        ExecutionPhase.LOCKED_PRIMARY: value.locked_primary_authorization,
    }
    for phase in _GOLD_PHASE_ORDER:
        latest_execution_at = max(
            (item.assembled_at for item in executions[phase]), key=_timestamp
        )
        _require_after(
            golds[phase].released_at,
            latest_execution_at,
            "Main Gold release",
        )
        _assert_analysis_main_context_v1(
            analysis=analyses[phase],
            authorization=analysis_authorizations[phase],
            frozen=value.main_frozen_cases,
        )
        evidence_union = _analysis_evidence_union_v1(executions[phase])
        observed_evidence = tuple(
            sorted(
                (item.trace_evidence for item in analyses[phase].cells),
                key=lambda item: (
                    item.terminal_result.case_id,
                    item.role.value,
                    item.terminal_result.cell_id,
                ),
            )
        )
        if observed_evidence != evidence_union:
            raise ValueError(
                "Analysis cells do not exactly cover the unified Main execution union"
            )
        assert_main_gold_release_exact(
            golds[phase],
            phase_execution_releases=executions[phase],
            expert_annotation_keys=gold_expert_annotation_keys[phase],
            ephemeral_blinding_key=gold_keys[phase],
        )
        expected_gold_attestation = build_main_gold_formal_verifier_attestation(
            golds[phase],
            phase_execution_releases=executions[phase],
            expert_annotation_keys=gold_expert_annotation_keys[phase],
            ephemeral_blinding_key=gold_keys[phase],
            verified_at=gold_verifiers[phase].verified_at,
        )
        if expected_gold_attestation != gold_verifiers[phase]:
            raise ValueError("Main Gold formal verifier does not exactly replay")
        assert_analysis_input_exact_replay_v2(analyses[phase])
        for cell in analyses[phase].cells:
            expected_cell = build_analysis_cell_from_main_gold_v1(
                golds[phase],
                gold_verifiers[phase],
                trace_evidence=cell.trace_evidence,
            )
            if expected_cell != cell:
                raise ValueError(
                    "Analysis cell does not exactly derive from Main Gold; "
                    "caller judgments or duplicate clusters are forbidden"
                )

    _assert_configuration_lineage_v1(value)

    _assert_formal_attestation_exact_v1(
        value.development_ablation_analysis,
        value.development_ablation_analysis_attestation,
        VerifiedPayloadKind.DEVELOPMENT_ABLATION_METRIC_ROWS,
    )
    ablation_rows = derive_development_metric_rows_v2(
        value.development_ablation_analysis
    )
    if tuple(ablation_rows) != value.development_promotion.metric_rows:
        raise ValueError("promotion rows do not derive from ablation Analysis V2")
    assert_development_promotion_release_exact_replay_v1(
        value.development_promotion
    )
    assert_fusion_configuration_release_exact_replay_v1(
        value.fusion_configuration,
        promotion_release=value.development_promotion,
    )

    _assert_formal_attestation_exact_v1(
        value.development_fusion_analysis,
        value.development_fusion_analysis_attestation,
        VerifiedPayloadKind.DEVELOPMENT_FUSION_METRIC_ROWS,
    )
    fusion_rows = derive_development_metric_rows_v2(
        value.development_fusion_analysis
    )
    if tuple(fusion_rows) != value.development_fusion_gate.metric_rows:
        raise ValueError("Fusion Gate rows do not derive from Analysis V2")
    assert_development_fusion_gate_release_exact_replay_v1(
        value.development_fusion_gate,
        promotion_release=value.development_promotion,
        fusion_configuration=value.fusion_configuration,
    )

    assert_locked_label_seal_exact_replay_v1(value.locked_label_seal)
    assert_locked_test_authorization_exact_replay_v1(
        value.locked_test_authorization,
        promotion_release=value.development_promotion,
        fusion_configuration=value.fusion_configuration,
        fusion_gate=value.development_fusion_gate,
        locked_label_seal=value.locked_label_seal,
    )
    expected_genesis = build_locked_unseal_ledger_genesis_v1(
        authorization=value.locked_test_authorization,
        locked_label_seal=value.locked_label_seal,
    )
    if expected_genesis != value.locked_unseal_genesis_ledger:
        raise ValueError("locked unseal genesis ledger does not replay")
    expected_committed = append_locked_unseal_ledger_v1(
        prior_ledger=value.locked_unseal_genesis_ledger,
        unseal=value.locked_unseal,
    )
    if expected_committed != value.locked_unseal_committed_ledger:
        raise ValueError("locked unseal committed ledger does not replay")
    assert_locked_annotation_unseal_exact_replay_v1(
        value.locked_unseal,
        authorization=value.locked_test_authorization,
        promotion_release=value.development_promotion,
        fusion_configuration=value.fusion_configuration,
        fusion_gate=value.development_fusion_gate,
        locked_label_seal=value.locked_label_seal,
        prior_ledger=value.locked_unseal_genesis_ledger,
        committed_ledger=value.locked_unseal_committed_ledger,
    )

    _assert_formal_attestation_exact_v1(
        value.locked_analysis,
        value.locked_analysis_attestation,
        VerifiedPayloadKind.LOCKED_RESULT_FAMILY,
    )
    primary, secondary = derive_locked_result_family_v2(value.locked_analysis)
    if (primary, secondary) != (
        value.claim_support.primary_result,
        value.claim_support.secondary_results,
    ):
        raise ValueError("locked claim results do not derive from Analysis V2")
    for deviation in value.protocol_deviations:
        assert_protocol_deviation_exact_replay_v1(
            deviation,
            unseal=(value.locked_unseal if deviation.unseal_ref is not None else None),
        )
    assert_claim_support_release_exact_replay_v1(
        value.claim_support,
        promotion_release=value.development_promotion,
        fusion_configuration=value.fusion_configuration,
        authorization=value.locked_test_authorization,
        unseal=value.locked_unseal,
        protocol_deviations=value.protocol_deviations,
    )
    assert_public_benchmark_projection_exact_replay_v1(
        value.public_projection,
        claim_support=value.claim_support,
        promotion_release=value.development_promotion,
        fusion_configuration=value.fusion_configuration,
        protocol_deviations=value.protocol_deviations,
    )
    reviewer_identity_key = release_authority_keys[
        ReleaseAuthorityRoleV1.SCIENTIFIC_REVIEWER_IDENTITY
    ]
    for identity in value.scientific_reviewer_identity_attestations:
        assert_scientific_reviewer_identity_attestation_exact_replay_v1(
            identity,
            authority_policy=value.release_authority_policy,
            authority_key=reviewer_identity_key,
            reviewer_decision_key=reviewer_decision_keys[identity.reviewer_id],
        )
    for review in value.scientific_reviewer_attestations:
        assert_scientific_reviewer_attestation_exact_replay_v1(
            review,
            authority_policy=value.release_authority_policy,
            reviewer_identity_authority_key=reviewer_identity_key,
            reviewer_decision_key=reviewer_decision_keys[review.reviewer_id],
            claim_support=value.claim_support,
            public_projection=value.public_projection,
        )
    assert_scientific_review_release_exact_replay_v1(
        value.scientific_review,
        authority_policy=value.release_authority_policy,
        reviewer_identity_authority_key=reviewer_identity_key,
        reviewer_decision_keys=reviewer_decision_keys,
        claim_support=value.claim_support,
        public_projection=value.public_projection,
    )
    control_role = {
        ReleaseControlKindV1.LICENSE: ReleaseAuthorityRoleV1.LICENSE_RELEASE,
        ReleaseControlKindV1.PRIVACY: ReleaseAuthorityRoleV1.PRIVACY_RELEASE,
        ReleaseControlKindV1.CUSTODY: ReleaseAuthorityRoleV1.CUSTODY_RELEASE,
    }
    for control in value.release_control_attestations:
        assert_release_control_attestation_exact_replay_v1(
            control,
            authority_policy=value.release_authority_policy,
            authority_key=release_authority_keys[control_role[control.control_kind]],
            public_projection=value.public_projection,
        )
    assert_public_release_authorization_exact_replay_v1(
        value.public_release_authorization,
        claim_support=value.claim_support,
        public_projection=value.public_projection,
        scientific_review=value.scientific_review,
        authority_keys=release_authority_keys,
        reviewer_identity_authority_key=reviewer_identity_key,
        reviewer_decision_keys=reviewer_decision_keys,
    )
    assert_public_benchmark_result_release_exact_replay_v1(
        value.public_result,
        authorization=value.public_release_authorization,
        scientific_review=value.scientific_review,
        public_projection=value.public_projection,
        claim_support=value.claim_support,
        authority_keys=release_authority_keys,
        reviewer_identity_authority_key=reviewer_identity_key,
        reviewer_decision_keys=reviewer_decision_keys,
    )


def assemble_flatband_campaign_release_v1(
    *,
    source_catalog_checkpoint: SourceCatalogCheckpointReleaseV1,
    pilot_rounds: Sequence[PilotRoundArtifactsV3],
    main_sampling_policy: MainSamplingPolicyReleaseV1,
    main_candidate_pool: MainCandidatePoolReleaseV1,
    main_eligibility: MainEligibilityReleaseV1,
    main_frozen_cases: MainFrozenCaseReleaseV1,
    main_pre_budget_closure: MainPreBudgetClosureReleaseV1,
    main_private_identity_attestation: PrivateExpertIdentityCustodianAttestationV2,
    release_authority_policy: ReleaseAuthorityPolicyV1,
    development_ablation_authorization: MainPhaseAuthorizationReleaseV1,
    local_sensitivity_authorization: MainPhaseAuthorizationReleaseV1,
    development_fusion_authorization: MainPhaseAuthorizationReleaseV1,
    locked_fusion_components_authorization: MainPhaseAuthorizationReleaseV1,
    locked_primary_authorization: MainPhaseAuthorizationReleaseV1,
    development_ablation_execution: MainPhaseExecutionReleaseV1,
    local_sensitivity_not_run_at: str,
    development_fusion_execution: MainPhaseExecutionReleaseV1,
    locked_fusion_components_execution: MainPhaseExecutionReleaseV1,
    locked_primary_execution: MainPhaseExecutionReleaseV1,
    locked_minus_e1_execution: MainPhaseExecutionReleaseV1,
    locked_minus_e2_execution: MainPhaseExecutionReleaseV1,
    locked_minus_e3_execution: MainPhaseExecutionReleaseV1,
    development_ablation_gold: MainGoldReleaseV1,
    development_ablation_gold_verifier: MainGoldFormalVerifierAttestationV1,
    development_ablation_analysis: AnalysisInputReleaseV2,
    development_ablation_analysis_attestation: FormalVerifierAttestationRefV1,
    development_promotion: DevelopmentPromotionReleaseV1,
    fusion_configuration: FusionConfigurationReleaseV1,
    development_fusion_gold: MainGoldReleaseV1,
    development_fusion_gold_verifier: MainGoldFormalVerifierAttestationV1,
    development_fusion_analysis: AnalysisInputReleaseV2,
    development_fusion_analysis_attestation: FormalVerifierAttestationRefV1,
    development_fusion_gate: DevelopmentFusionGateReleaseV1,
    locked_label_seal: LockedLabelSealV1,
    locked_execution_plan: LockedExecutionPlanV1,
    locked_test_authorization: LockedTestAuthorizationReleaseV1,
    locked_unseal_genesis_ledger: LockedUnsealLedgerReleaseV1,
    locked_unseal: LockedAnnotationUnsealReleaseV1,
    locked_unseal_committed_ledger: LockedUnsealLedgerReleaseV1,
    locked_gold: MainGoldReleaseV1,
    locked_gold_verifier: MainGoldFormalVerifierAttestationV1,
    locked_analysis: AnalysisInputReleaseV2,
    locked_analysis_attestation: FormalVerifierAttestationRefV1,
    protocol_deviations: Sequence[ProtocolDeviationReleaseV1],
    claim_support: ClaimSupportReleaseV1,
    public_projection: PublicBenchmarkProjectionV1,
    scientific_reviewer_identity_attestations: Sequence[
        ScientificReviewerIdentityAttestationV1
    ],
    scientific_reviewer_attestations: Sequence[ScientificReviewerAttestationV1],
    scientific_review: ScientificReviewReleaseV1,
    release_control_attestations: Sequence[ReleaseControlAttestationV1],
    public_release_authorization: PublicReleaseAuthorizationV1,
    public_result: PublicBenchmarkResultReleaseV1,
    assembled_at: str,
    pilot_blinding_keys: Mapping[int, bytes],
    main_gold_blinding_keys: Mapping[ExecutionPhase, bytes],
    main_gold_expert_annotation_keys: Mapping[
        ExecutionPhase, Mapping[str, bytes]
    ],
    release_authority_keys: Mapping[ReleaseAuthorityRoleV1, bytes],
    scientific_reviewer_decision_keys: Mapping[str, bytes],
) -> FlatBandCampaignReleaseV1:
    """Assemble and immediately formally replay one complete private campaign."""

    values: dict[str, object] = {
        name: item
        for name, item in locals().items()
        if name
        not in {
            "pilot_blinding_keys",
            "main_gold_blinding_keys",
            "main_gold_expert_annotation_keys",
            "release_authority_keys",
            "scientific_reviewer_decision_keys",
        }
    }
    values["local_sensitivity_execution"] = (
        build_local_sensitivity_not_run_release_v1(
            phase_authorization=local_sensitivity_authorization,
            decided_at=local_sensitivity_not_run_at,
        )
    )
    values.pop("local_sensitivity_not_run_at", None)
    values["pilot_rounds"] = tuple(
        sorted(pilot_rounds, key=lambda item: item.review_round)
    )
    values["protocol_deviations"] = tuple(
        sorted(protocol_deviations, key=lambda item: (item.release_id, item.release_sha256))
    )
    values["scientific_reviewer_attestations"] = tuple(
        sorted(
            scientific_reviewer_attestations,
            key=lambda item: (item.reviewer_id, item.attestation_id),
        )
    )
    values["scientific_reviewer_identity_attestations"] = tuple(
        sorted(
            scientific_reviewer_identity_attestations,
            key=lambda item: (
                item.reviewer_id,
                item.identity_attestation_id,
            ),
        )
    )
    values["release_control_attestations"] = tuple(
        sorted(
            release_control_attestations,
            key=lambda item: item.control_kind.value,
        )
    )
    release = _build_addressed(
        FlatBandCampaignReleaseV1,
        id_field="campaign_id",
        sha_field="campaign_sha256",
        prefix="flatband-campaign-v1",
        values=values,
    )
    assert_flatband_campaign_release_exact_v1(
        release,
        pilot_blinding_keys=pilot_blinding_keys,
        main_gold_blinding_keys=main_gold_blinding_keys,
        main_gold_expert_annotation_keys=main_gold_expert_annotation_keys,
        release_authority_keys=release_authority_keys,
        scientific_reviewer_decision_keys=scientific_reviewer_decision_keys,
    )
    return release


__all__ = [
    "FlatBandCampaignReleaseV1",
    "LockedExecutionPlanV1",
    "LockedExecutionPlannedArmV1",
    "LocalSensitivityNotRunReleaseV1",
    "assemble_flatband_campaign_release_v1",
    "assert_flatband_campaign_release_exact_v1",
    "assert_locked_execution_plan_exact_v1",
    "assert_local_sensitivity_not_run_exact_v1",
    "build_locked_execution_plan_v1",
    "build_local_sensitivity_not_run_release_v1",
]
