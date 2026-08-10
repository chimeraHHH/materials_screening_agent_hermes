from __future__ import annotations

from collections.abc import Mapping, Sequence

import pytest
from pydantic import ValidationError

from material_agent.inspiration.models import canonical_sha256
from material_agent.research.flatband_execution import ResearchSystemId
import material_agent.research.flatband_lifecycle as lifecycle


def _sha(label: str) -> str:
    return canonical_sha256(label)


def _ref(
    artifact_type: lifecycle.LifecycleArtifactType, label: str
) -> lifecycle.TypedArtifactRefV1:
    return lifecycle.typed_artifact_ref_v1(artifact_type, label, _sha(label))


def _attestation(
    *,
    analysis_input_ref: lifecycle.TypedArtifactRefV1,
    payload_kind: lifecycle.VerifiedPayloadKind,
    payload: object,
    attested_at: str,
) -> lifecycle.FormalVerifierAttestationRefV1:
    # The lifecycle module intentionally has no producer for this artifact: in
    # production it must come from the AnalysisInputV2 formal verifier.  This
    # helper creates a syntactically exact upstream fixture only.
    return lifecycle._build_addressed(  # noqa: SLF001 - exact fixture seam
        lifecycle.FormalVerifierAttestationRefV1,
        id_field="attestation_id",
        sha_field="attestation_sha256",
        prefix="analysis-attestation-v1",
        values={
            "analysis_input_ref": analysis_input_ref,
            "verifier_ref": _ref(
                lifecycle.LifecycleArtifactType.ANALYSIS_INPUT_V2_FORMAL_VERIFIER,
                "analysis-input-v2-verifier",
            ),
            "verified_payload_kind": payload_kind,
            "verified_payload_sha256": canonical_sha256(payload),
            "attested_at": attested_at,
        },
    )


def _row(
    *,
    analysis_input_ref: lifecycle.TypedArtifactRefV1,
    system_id: ResearchSystemId,
    andcg: float,
    evidence: float = 0.80,
    duplicate: float = 0.10,
    success: float = 0.70,
    universe: str = "development-universe",
    body_count: int = 0,
) -> lifecycle.DevelopmentMetricRowV1:
    return lifecycle.build_development_metric_row_v1(
        analysis_input_ref=analysis_input_ref,
        system_id=system_id,
        case_universe_sha256=_sha(universe),
        andcg_at_5=andcg,
        evidence_valid_at_5=evidence,
        duplicate_rate_at_5=duplicate,
        success_at_5=success,
        physical_request_budget_per_case=8,
        article_body_access_count=body_count,
    )


def _promotion(
    *,
    e1: float = 0.44,
    e2_a: float = 0.44,
    e2_b: float = 0.46,
    e3: float = 0.44,
    e3_body_count: int = 0,
) -> lifecycle.DevelopmentPromotionReleaseV1:
    analysis = _ref(
        lifecycle.LifecycleArtifactType.ANALYSIS_INPUT_V2,
        "development-analysis-input",
    )
    rows = (
        _row(analysis_input_ref=analysis, system_id=ResearchSystemId.B0, andcg=0.40),
        _row(analysis_input_ref=analysis, system_id=ResearchSystemId.E1, andcg=e1),
        _row(analysis_input_ref=analysis, system_id=ResearchSystemId.E2_A, andcg=e2_a),
        _row(analysis_input_ref=analysis, system_id=ResearchSystemId.E2_B, andcg=e2_b),
        _row(
            analysis_input_ref=analysis,
            system_id=ResearchSystemId.E3,
            andcg=e3,
            body_count=e3_body_count,
        ),
    )
    attestation = _attestation(
        analysis_input_ref=analysis,
        payload_kind=lifecycle.VerifiedPayloadKind.DEVELOPMENT_ABLATION_METRIC_ROWS,
        payload=rows,
        attested_at="2026-01-01T00:00:00Z",
    )
    return lifecycle.build_development_promotion_release_v1(
        analysis_input_ref=analysis,
        formal_verifier_attestation=attestation,
        metric_rows=rows,
        assembled_at="2026-01-01T00:01:00Z",
    )


def _fusion_chain(
    *, fusion_andcg: float = 0.46
) -> tuple[
    lifecycle.DevelopmentPromotionReleaseV1,
    lifecycle.FusionConfigurationReleaseV1,
    lifecycle.DevelopmentFusionGateReleaseV1,
]:
    promotion = _promotion()
    configs = tuple(
        lifecycle.SystemConfigurationRefV1(
            system_id=system_id,
            configuration_ref=_ref(
                lifecycle.LifecycleArtifactType.SYSTEM_CONFIGURATION,
                f"config-{system_id.value}",
            ),
        )
        for system_id in (ResearchSystemId.B0, *promotion.promoted_components)
    )
    config = lifecycle.build_fusion_configuration_release_v1(
        promotion_release=promotion,
        component_configurations=configs,
        frozen_at="2026-01-01T00:02:00Z",
    )
    analysis = _ref(
        lifecycle.LifecycleArtifactType.ANALYSIS_INPUT_V2,
        "fusion-analysis-input",
    )
    rows = (
        _row(
            analysis_input_ref=analysis,
            system_id=ResearchSystemId.B0,
            andcg=0.40,
            universe="fusion-universe",
        ),
        _row(
            analysis_input_ref=analysis,
            system_id=ResearchSystemId.FUSION,
            andcg=fusion_andcg,
            universe="fusion-universe",
        ),
    )
    attestation = _attestation(
        analysis_input_ref=analysis,
        payload_kind=lifecycle.VerifiedPayloadKind.DEVELOPMENT_FUSION_METRIC_ROWS,
        payload=rows,
        attested_at="2026-01-01T00:03:00Z",
    )
    gate = lifecycle.build_development_fusion_gate_release_v1(
        promotion_release=promotion,
        fusion_configuration=config,
        analysis_input_ref=analysis,
        formal_verifier_attestation=attestation,
        metric_rows=rows,
        evaluated_at="2026-01-01T00:04:00Z",
    )
    return promotion, config, gate


def _locked_chain() -> tuple[
    lifecycle.DevelopmentPromotionReleaseV1,
    lifecycle.FusionConfigurationReleaseV1,
    lifecycle.DevelopmentFusionGateReleaseV1,
    lifecycle.LockedLabelSealV1,
    lifecycle.LockedTestAuthorizationReleaseV1,
    lifecycle.LockedUnsealLedgerReleaseV1,
    lifecycle.LockedAnnotationUnsealReleaseV1,
    lifecycle.LockedUnsealLedgerReleaseV1,
]:
    promotion, config, gate = _fusion_chain()
    seal = lifecycle.build_locked_label_seal_v1(
        split_manifest_ref=_ref(
            lifecycle.LifecycleArtifactType.BENCHMARK_SPLIT_MANIFEST,
            "main-split",
        ),
        private_label_custody_ref=_ref(
            lifecycle.LifecycleArtifactType.PRIVATE_LOCKED_LABEL_CUSTODY,
            "locked-label-custody",
        ),
        locked_case_universe_sha256=_sha("locked-case-universe"),
        sealed_label_commitment_sha256=_sha("locked-label-commitment"),
        sealed_at="2026-01-01T00:02:30Z",
    )
    authorization = lifecycle.build_locked_test_authorization_release_v1(
        promotion_release=promotion,
        fusion_configuration=config,
        fusion_gate=gate,
        locked_label_seal=seal,
        analysis_code_ref=_ref(
            lifecycle.LifecycleArtifactType.ANALYSIS_CODE_RELEASE, "analysis-code"
        ),
        analysis_environment_ref=_ref(
            lifecycle.LifecycleArtifactType.ANALYSIS_ENVIRONMENT_RELEASE,
            "analysis-environment",
        ),
        statistical_analysis_plan_ref=_ref(
            lifecycle.LifecycleArtifactType.STATISTICAL_ANALYSIS_PLAN,
            "statistics-plan",
        ),
        locked_execution_ref=_ref(
            lifecycle.LifecycleArtifactType.LOCKED_EXECUTION_RELEASE,
            "locked-execution",
        ),
        authorized_at="2026-01-01T00:05:00Z",
    )
    genesis = lifecycle.build_locked_unseal_ledger_genesis_v1(
        authorization=authorization,
        locked_label_seal=seal,
    )
    unseal = lifecycle.build_locked_annotation_unseal_release_v1(
        authorization=authorization,
        promotion_release=promotion,
        fusion_configuration=config,
        fusion_gate=gate,
        locked_label_seal=seal,
        prior_ledger=genesis,
        unsealed_at="2026-01-01T00:06:00Z",
    )
    committed = lifecycle.append_locked_unseal_ledger_v1(
        prior_ledger=genesis,
        unseal=unseal,
    )
    return promotion, config, gate, seal, authorization, genesis, unseal, committed


def _claim_support(
    *,
    deviations: Sequence[lifecycle.ProtocolDeviationReleaseV1] = (),
    claims: Sequence[lifecycle.SupportedClaimV1] | None = None,
) -> tuple[
    lifecycle.ClaimSupportReleaseV1,
    lifecycle.DevelopmentPromotionReleaseV1,
    lifecycle.FusionConfigurationReleaseV1,
    lifecycle.LockedTestAuthorizationReleaseV1,
    lifecycle.LockedAnnotationUnsealReleaseV1,
]:
    promotion, config, _, _, authorization, _, unseal, _ = _locked_chain()
    primary = lifecycle.build_locked_primary_result_v1(
        equal_iid_ood_andcg_delta=0.06,
        bootstrap_lower=0.01,
        bootstrap_upper=0.11,
        one_sided_randomization_p=0.02,
        success_delta=0.00,
        evidence_valid_delta=0.00,
        duplicate_rate_delta=0.00,
        invalid_case_rate=0.00,
        unresolvable_unit_rate=0.00,
    )
    observations: Mapping[
        lifecycle.LockedSecondaryHypothesis, tuple[float, float] | None
    ] = {
        hypothesis: (0.03, 0.01 + index * 0.01)
        for index, hypothesis in enumerate(lifecycle.LOCKED_SECONDARY_FAMILY)
    }
    secondaries = lifecycle.build_locked_secondary_family_v1(
        promotion_release=promotion,
        observations=observations,
    )
    analysis = _ref(
        lifecycle.LifecycleArtifactType.ANALYSIS_INPUT_V2,
        "locked-analysis-input",
    )
    attestation = _attestation(
        analysis_input_ref=analysis,
        payload_kind=lifecycle.VerifiedPayloadKind.LOCKED_RESULT_FAMILY,
        payload=(primary, secondaries),
        attested_at="2026-01-01T00:07:00Z",
    )
    if claims is None:
        claims = (
            lifecycle.supported_claim_v1(
                lifecycle.ClaimKind.BENCHMARK_PRIMARY_PASSED
            ),
        )
    support = lifecycle.build_claim_support_release_v1(
        promotion_release=promotion,
        fusion_configuration=config,
        authorization=authorization,
        unseal=unseal,
        analysis_input_ref=analysis,
        formal_verifier_attestation=attestation,
        primary_result=primary,
        secondary_results=secondaries,
        protocol_deviations=deviations,
        claims=claims,
        assembled_at="2026-01-01T00:08:00Z",
    )
    return support, promotion, config, authorization, unseal


def test_schema_metric_row_is_addressed_but_explicitly_not_formal_evidence() -> None:
    analysis = _ref(
        lifecycle.LifecycleArtifactType.ANALYSIS_INPUT_V2, "schema-row-analysis"
    )
    row = _row(
        analysis_input_ref=analysis,
        system_id=ResearchSystemId.B0,
        andcg=0.40,
    )
    lifecycle.assert_development_metric_row_exact_replay_v1(row)
    assert row.schema_only_row is True
    assert row.row_alone_is_formal_evidence is False
    assert row.analysis_input_v2_formal_verifier_required is True

    payload = row.model_dump(mode="python")
    payload["andcg_at_5"] = 0.99
    with pytest.raises(ValidationError, match="semantic content"):
        lifecycle.DevelopmentMetricRowV1.model_validate(payload)


def test_promotion_replays_guardrails_and_e2_plus_point_zero_one_rule() -> None:
    release = _promotion(e2_a=0.44, e2_b=0.45, e3=0.44)
    lifecycle.assert_development_promotion_release_exact_replay_v1(release)
    assert release.selected_e2_variant is ResearchSystemId.E2_B
    assert release.e2_selection_reason is (
        lifecycle.E2SelectionReason.BOTH_PASSED_E2_B_PLUS_0_01
    )

    parsimonious = _promotion(e2_a=0.44, e2_b=0.449, e3=0.44)
    assert parsimonious.selected_e2_variant is ResearchSystemId.E2_A
    assert parsimonious.e2_selection_reason is (
        lifecycle.E2SelectionReason.BOTH_PASSED_E2_A_PARSIMONY
    )

    body_violation = _promotion(e3=0.44, e3_body_count=1)
    assert ResearchSystemId.E3 not in body_violation.promoted_components


def test_formal_attestation_rejects_readdressed_caller_metric_row() -> None:
    release = _promotion()
    original = release.metric_rows[1]
    forged = lifecycle.build_development_metric_row_v1(
        analysis_input_ref=original.analysis_input_ref,
        system_id=original.system_id,
        case_universe_sha256=original.case_universe_sha256,
        andcg_at_5=0.99,
        evidence_valid_at_5=original.evidence_valid_at_5,
        duplicate_rate_at_5=original.duplicate_rate_at_5,
        success_at_5=original.success_at_5,
        physical_request_budget_per_case=original.physical_request_budget_per_case,
    )
    attacked = (*release.metric_rows[:1], forged, *release.metric_rows[2:])
    with pytest.raises(ValueError, match="exact payload"):
        lifecycle.build_development_promotion_release_v1(
            analysis_input_ref=release.analysis_input_ref,
            formal_verifier_attestation=release.formal_verifier_attestation,
            metric_rows=attacked,
            assembled_at=release.assembled_at,
        )


def test_fusion_is_all_eligible_components_and_must_pass_before_authorization() -> None:
    promotion, config, gate = _fusion_chain()
    lifecycle.assert_fusion_configuration_release_exact_replay_v1(
        config, promotion_release=promotion
    )
    assert config.fusion_components == promotion.promoted_components
    assert gate.passed is True

    with pytest.raises(ValueError, match="every promoted component"):
        lifecycle.build_fusion_configuration_release_v1(
            promotion_release=promotion,
            component_configurations=config.component_configurations[:-1],
            frozen_at=config.frozen_at,
        )

    _, failing_config, failing_gate = _fusion_chain(fusion_andcg=0.42)
    assert failing_gate.passed is False
    seal = lifecycle.build_locked_label_seal_v1(
        split_manifest_ref=_ref(
            lifecycle.LifecycleArtifactType.BENCHMARK_SPLIT_MANIFEST,
            "failure-split",
        ),
        private_label_custody_ref=_ref(
            lifecycle.LifecycleArtifactType.PRIVATE_LOCKED_LABEL_CUSTODY,
            "failure-labels",
        ),
        locked_case_universe_sha256=_sha("failure-cases"),
        sealed_label_commitment_sha256=_sha("failure-label-commitment"),
        sealed_at="2026-01-01T00:02:30Z",
    )
    with pytest.raises(ValueError, match="before Fusion passes"):
        lifecycle.build_locked_test_authorization_release_v1(
            promotion_release=promotion,
            fusion_configuration=failing_config,
            fusion_gate=failing_gate,
            locked_label_seal=seal,
            analysis_code_ref=_ref(
                lifecycle.LifecycleArtifactType.ANALYSIS_CODE_RELEASE, "fail-code"
            ),
            analysis_environment_ref=_ref(
                lifecycle.LifecycleArtifactType.ANALYSIS_ENVIRONMENT_RELEASE,
                "fail-env",
            ),
            statistical_analysis_plan_ref=_ref(
                lifecycle.LifecycleArtifactType.STATISTICAL_ANALYSIS_PLAN,
                "fail-plan",
            ),
            locked_execution_ref=_ref(
                lifecycle.LifecycleArtifactType.LOCKED_EXECUTION_RELEASE,
                "fail-execution",
            ),
            authorized_at="2026-01-01T00:05:00Z",
        )


def test_locked_unseal_is_not_early_repeatable_or_configuration_drifting() -> None:
    promotion, config, gate, seal, authorization, genesis, unseal, committed = (
        _locked_chain()
    )
    lifecycle.assert_locked_annotation_unseal_exact_replay_v1(
        unseal,
        authorization=authorization,
        promotion_release=promotion,
        fusion_configuration=config,
        fusion_gate=gate,
        locked_label_seal=seal,
        prior_ledger=genesis,
        committed_ledger=committed,
    )
    lifecycle.assert_locked_unseal_ledger_exact_replay_v1(
        committed,
        authorization=authorization,
        locked_label_seal=seal,
        prior_ledger=genesis,
    )
    assert unseal.unseal_id == authorization.one_shot_unseal_identity

    with pytest.raises(ValueError, match="strictly later"):
        lifecycle.build_locked_annotation_unseal_release_v1(
            authorization=authorization,
            promotion_release=promotion,
            fusion_configuration=config,
            fusion_gate=gate,
            locked_label_seal=seal,
            prior_ledger=genesis,
            unsealed_at=authorization.authorized_at,
        )
    with pytest.raises(ValueError, match="second event"):
        lifecycle.append_locked_unseal_ledger_v1(
            prior_ledger=committed,
            unseal=unseal,
        )

    drifted_configs = list(config.component_configurations)
    drifted_configs[-1] = lifecycle.SystemConfigurationRefV1(
        system_id=drifted_configs[-1].system_id,
        configuration_ref=_ref(
            lifecycle.LifecycleArtifactType.SYSTEM_CONFIGURATION,
            "drifted-component-config",
        ),
    )
    drifted = lifecycle.build_fusion_configuration_release_v1(
        promotion_release=promotion,
        component_configurations=drifted_configs,
        frozen_at=config.frozen_at,
    )
    with pytest.raises(ValueError, match="does not exactly replay"):
        lifecycle.build_locked_annotation_unseal_release_v1(
            authorization=authorization,
            promotion_release=promotion,
            fusion_configuration=drifted,
            fusion_gate=gate,
            locked_label_seal=seal,
            prior_ledger=genesis,
            unsealed_at="2026-01-01T00:07:00Z",
        )


def test_post_unseal_score_changing_deviation_invalidates_confirmatory_status() -> None:
    _, _, _, _, _, _, unseal, _ = _locked_chain()
    deviation = lifecycle.build_protocol_deviation_release_v1(
        deviation_code="ranking-score-fix",
        severity=lifecycle.ProtocolDeviationSeverity.CRITICAL,
        description="A post-unseal repair would change one score.",
        changes_candidates_or_scores=True,
        remediation="Disclose and invalidate the first confirmatory run.",
        disclosed_at="2026-01-01T00:07:00Z",
        unseal=unseal,
    )
    lifecycle.assert_protocol_deviation_exact_replay_v1(
        deviation, unseal=unseal
    )
    assert deviation.confirmatory_validity is (
        lifecycle.ConfirmatoryValidity.INVALIDATED_SCORE_OR_CANDIDATE_CHANGE
    )


def test_fixed_secondary_family_keeps_unpromoted_component_as_na_p_one() -> None:
    promotion = _promotion(e1=0.44, e2_a=0.44, e2_b=0.46, e3=0.42)
    observations: dict[
        lifecycle.LockedSecondaryHypothesis, tuple[float, float] | None
    ] = {
        lifecycle.LockedSecondaryHypothesis.FUSION_VS_B0_IID: (0.04, 0.01),
        lifecycle.LockedSecondaryHypothesis.FUSION_VS_B0_OOD: (0.04, 0.02),
        lifecycle.LockedSecondaryHypothesis.FUSION_VS_FUSION_MINUS_E1: (
            0.02,
            0.03,
        ),
        lifecycle.LockedSecondaryHypothesis.FUSION_VS_FUSION_MINUS_E2: (
            0.02,
            0.04,
        ),
        lifecycle.LockedSecondaryHypothesis.FUSION_VS_FUSION_MINUS_E3: None,
    }
    family = lifecycle.build_locked_secondary_family_v1(
        promotion_release=promotion,
        observations=observations,
    )
    assert tuple(item.hypothesis for item in family) == lifecycle.LOCKED_SECONDARY_FAMILY
    e3 = family[-1]
    assert e3.applicability is lifecycle.SecondaryApplicability.NOT_APPLICABLE
    assert e3.raw_one_sided_p == e3.holm_adjusted_p == 1.0
    assert e3.andcg_delta is None


@pytest.mark.parametrize(
    "statement",
    (
        "We discovered a novel material.",
        "This is a real material discovered by the benchmark.",
        "The target property is DFT-verified.",
        "这是首次发现的新材料，并由DFT证实。",
    ),
)
def test_claim_allowlist_blocks_novelty_material_and_dft_proof(statement: str) -> None:
    with pytest.raises(ValidationError, match="claim exceeds allowlist"):
        lifecycle.SupportedClaimV1(
            claim_kind=lifecycle.ClaimKind.BENCHMARK_PRIMARY_PASSED,
            statement=statement,
        )


def test_public_projection_rejects_free_text_limitation_injection() -> None:
    support, promotion, config, _, _ = _claim_support()
    with pytest.raises(ValueError, match="frozen code vocabulary"):
        lifecycle.build_public_benchmark_projection_v1(
            claim_support=support,
            promotion_release=promotion,
            fusion_configuration=config,
            protocol_deviations=(),
            limitation_codes=(  # type: ignore[arg-type]
                "Reviewer Alice labelled pooled packet X as grade 3",
            ),
        )


def test_public_deviation_projection_drops_private_caller_code_and_text() -> None:
    _, _, _, _, _, _, unseal, _ = _locked_chain()
    deviation = lifecycle.build_protocol_deviation_release_v1(
        deviation_code="Alice_private_label_packet_X_grade_3",
        severity=lifecycle.ProtocolDeviationSeverity.MINOR,
        description="Reviewer Alice labelled one private packet.",
        changes_candidates_or_scores=False,
        remediation="Retain this detail only in private custody.",
        disclosed_at="2026-01-01T00:07:00Z",
        unseal=unseal,
    )
    support, promotion, config, _, _ = _claim_support(deviations=(deviation,))
    projection = lifecycle.build_public_benchmark_projection_v1(
        claim_support=support,
        promotion_release=promotion,
        fusion_configuration=config,
        protocol_deviations=(deviation,),
        limitation_codes=(lifecycle.PublicLimitationCodeV1.BENCHMARK_SCOPE_ONLY,),
    )
    public_text = str(projection.model_dump(mode="json"))
    assert "Alice" not in public_text
    assert "packet_X" not in public_text
    assert projection.protocol_deviations[0].deviation_ordinal == 1


def _release_authority_context() -> tuple[
    lifecycle.ReleaseAuthorityPolicyV1,
    dict[lifecycle.ReleaseAuthorityRoleV1, bytes],
]:
    keys = {
        role: _sha(f"release-authority-key-{role.value}").encode("ascii")
        for role in lifecycle.ReleaseAuthorityRoleV1
    }
    policy = lifecycle.build_release_authority_policy_v1(
        authorities={
            role: (f"release-authority-{role.value.lower()}", key)
            for role, key in keys.items()
        },
        frozen_at="2026-01-01T00:07:00Z",
    )
    return policy, keys


def _scientific_review_for_projection(
    *,
    support: lifecycle.ClaimSupportReleaseV1,
    projection: lifecycle.PublicBenchmarkProjectionV1,
    policy: lifecycle.ReleaseAuthorityPolicyV1,
    keys: Mapping[lifecycle.ReleaseAuthorityRoleV1, bytes],
) -> tuple[
    tuple[lifecycle.ScientificReviewerIdentityAttestationV1, ...],
    dict[str, bytes],
    lifecycle.ScientificReviewReleaseV1,
]:
    identity_key = keys[
        lifecycle.ReleaseAuthorityRoleV1.SCIENTIFIC_REVIEWER_IDENTITY
    ]
    reviewer_decision_keys = {
        f"scientific-reviewer-{index}": _sha(
            f"scientific-reviewer-decision-key-{index}"
        ).encode("ascii")
        for index in (1, 2)
    }
    identities = tuple(
        lifecycle.build_scientific_reviewer_identity_attestation_v1(
            authority_policy=policy,
            authority_key=identity_key,
            reviewer_id=f"scientific-reviewer-{index}",
            natural_person_commitment_sha256=_sha(
                f"scientific-reviewer-natural-person-{index}"
            ),
            private_identity_evidence_sha256=_sha(
                f"scientific-reviewer-private-evidence-{index}"
            ),
            reviewer_decision_key=reviewer_decision_keys[
                f"scientific-reviewer-{index}"
            ],
            issued_at=f"2026-01-01T00:08:{index:02d}Z",
        )
        for index in (1, 2)
    )
    reviewers = tuple(
        lifecycle.build_scientific_reviewer_attestation_v1(
            reviewer_identity_attestation=identity,
            authority_policy=policy,
            reviewer_identity_authority_key=identity_key,
            reviewer_decision_key=reviewer_decision_keys[identity.reviewer_id],
            claim_support=support,
            public_projection=projection,
            decision=(
                lifecycle.ScientificReviewerDecision.APPROVE
                if index == 1
                else lifecycle.ScientificReviewerDecision.APPROVE_WITH_LIMITATIONS
            ),
            critical_issue_count=0,
            findings=() if index == 1 else ("Scope wording was checked.",),
            required_limitation_codes=(
                ()
                if index == 1
                else (lifecycle.PublicLimitationCodeV1.BENCHMARK_SCOPE_ONLY,)
            ),
            reviewed_at=f"2026-01-01T00:09:{index:02d}Z",
        )
        for index, identity in enumerate(identities, start=1)
    )
    review = lifecycle.build_scientific_review_release_v1(
        claim_support=support,
        public_projection=projection,
        reviewer_attestations=reviewers,
        authority_policy=policy,
        reviewer_identity_authority_key=identity_key,
        reviewer_decision_keys=reviewer_decision_keys,
        reviewed_at="2026-01-01T00:10:00Z",
    )
    return identities, reviewer_decision_keys, review


def _release_controls_for_projection(
    *,
    projection: lifecycle.PublicBenchmarkProjectionV1,
    policy: lifecycle.ReleaseAuthorityPolicyV1,
    keys: Mapping[lifecycle.ReleaseAuthorityRoleV1, bytes],
    issued_at: str = "2026-01-01T00:10:30Z",
) -> tuple[lifecycle.ReleaseControlAttestationV1, ...]:
    role_by_control = {
        lifecycle.ReleaseControlKindV1.LICENSE: (
            lifecycle.ReleaseAuthorityRoleV1.LICENSE_RELEASE
        ),
        lifecycle.ReleaseControlKindV1.PRIVACY: (
            lifecycle.ReleaseAuthorityRoleV1.PRIVACY_RELEASE
        ),
        lifecycle.ReleaseControlKindV1.CUSTODY: (
            lifecycle.ReleaseAuthorityRoleV1.CUSTODY_RELEASE
        ),
    }
    return tuple(
        lifecycle.build_release_control_attestation_v1(
            authority_policy=policy,
            authority_key=keys[role_by_control[control_kind]],
            control_kind=control_kind,
            public_projection=projection,
            evidence_artifact_sha256=_sha(
                f"release-control-evidence-{control_kind.value}"
            ),
            issued_at=issued_at,
        )
        for control_kind in lifecycle.ReleaseControlKindV1
    )


def test_full_scientific_review_and_public_release_exactly_replay() -> None:
    support, promotion, config, _, _ = _claim_support()
    projection = lifecycle.build_public_benchmark_projection_v1(
        claim_support=support,
        promotion_release=promotion,
        fusion_configuration=config,
        protocol_deviations=(),
        limitation_codes=(
            lifecycle.PublicLimitationCodeV1.BENCHMARK_SCOPE_ONLY,
            lifecycle.PublicLimitationCodeV1.NO_MATERIAL_DISCOVERY_CLAIM,
        ),
    )
    policy, keys = _release_authority_context()
    identities, reviewer_decision_keys, review = _scientific_review_for_projection(
        support=support,
        projection=projection,
        policy=policy,
        keys=keys,
    )
    controls = _release_controls_for_projection(
        projection=projection,
        policy=policy,
        keys=keys,
    )
    identity_key = keys[
        lifecycle.ReleaseAuthorityRoleV1.SCIENTIFIC_REVIEWER_IDENTITY
    ]
    for identity in identities:
        lifecycle.assert_scientific_reviewer_identity_attestation_exact_replay_v1(
            identity,
            authority_policy=policy,
            authority_key=identity_key,
            reviewer_decision_key=reviewer_decision_keys[identity.reviewer_id],
        )
    for control in controls:
        role = {
            lifecycle.ReleaseControlKindV1.LICENSE: (
                lifecycle.ReleaseAuthorityRoleV1.LICENSE_RELEASE
            ),
            lifecycle.ReleaseControlKindV1.PRIVACY: (
                lifecycle.ReleaseAuthorityRoleV1.PRIVACY_RELEASE
            ),
            lifecycle.ReleaseControlKindV1.CUSTODY: (
                lifecycle.ReleaseAuthorityRoleV1.CUSTODY_RELEASE
            ),
        }[control.control_kind]
        lifecycle.assert_release_control_attestation_exact_replay_v1(
            control,
            authority_policy=policy,
            authority_key=keys[role],
            public_projection=projection,
        )
    lifecycle.assert_scientific_review_release_exact_replay_v1(
        review,
        authority_policy=policy,
        reviewer_identity_authority_key=identity_key,
        reviewer_decision_keys=reviewer_decision_keys,
        claim_support=support,
        public_projection=projection,
    )
    authorization = lifecycle.build_public_release_authorization_v1(
        claim_support=support,
        public_projection=projection,
        scientific_review=review,
        authority_policy=policy,
        release_control_attestations=controls,
        authority_keys=keys,
        reviewer_identity_authority_key=identity_key,
        reviewer_decision_keys=reviewer_decision_keys,
        authorized_at="2026-01-01T00:11:00Z",
    )
    lifecycle.assert_public_release_authorization_exact_replay_v1(
        authorization,
        claim_support=support,
        public_projection=projection,
        scientific_review=review,
        authority_keys=keys,
        reviewer_identity_authority_key=identity_key,
        reviewer_decision_keys=reviewer_decision_keys,
    )
    result = lifecycle.build_public_benchmark_result_release_v1(
        authorization=authorization,
        scientific_review=review,
        public_projection=projection,
        claim_support=support,
        authority_keys=keys,
        reviewer_identity_authority_key=identity_key,
        reviewer_decision_keys=reviewer_decision_keys,
        released_at="2026-01-01T00:12:00Z",
    )
    lifecycle.assert_public_benchmark_result_release_exact_replay_v1(
        result,
        authorization=authorization,
        scientific_review=review,
        public_projection=projection,
        claim_support=support,
        authority_keys=keys,
        reviewer_identity_authority_key=identity_key,
        reviewer_decision_keys=reviewer_decision_keys,
    )
    assert review.decision is lifecycle.ScientificReviewDecision.RELEASE_APPROVED
    assert result.novelty_claim_permitted is False
    assert result.real_material_discovery_claim_permitted is False
    assert result.dft_proof_claim_permitted is False
    assert result.scientific_conclusion is False


def test_release_authority_attestations_reject_wrong_keys() -> None:
    support, promotion, config, _, _ = _claim_support()
    projection = lifecycle.build_public_benchmark_projection_v1(
        claim_support=support,
        promotion_release=promotion,
        fusion_configuration=config,
        protocol_deviations=(),
        limitation_codes=(lifecycle.PublicLimitationCodeV1.BENCHMARK_SCOPE_ONLY,),
    )
    policy, keys = _release_authority_context()
    identities, reviewer_decision_keys, _ = _scientific_review_for_projection(
        support=support,
        projection=projection,
        policy=policy,
        keys=keys,
    )
    controls = _release_controls_for_projection(
        projection=projection,
        policy=policy,
        keys=keys,
    )
    wrong_key = _sha("foreign-release-authority-key").encode("ascii")

    with pytest.raises(ValueError, match="pre-frozen role commitment"):
        lifecycle.assert_scientific_reviewer_identity_attestation_exact_replay_v1(
            identities[0],
            authority_policy=policy,
            authority_key=wrong_key,
            reviewer_decision_key=reviewer_decision_keys[
                identities[0].reviewer_id
            ],
        )
    with pytest.raises(ValueError, match="pre-frozen role commitment"):
        lifecycle.assert_release_control_attestation_exact_replay_v1(
            controls[0],
            authority_policy=policy,
            authority_key=wrong_key,
            public_projection=projection,
        )


def test_scientific_review_rejects_wrong_reviewer_decision_key() -> None:
    support, promotion, config, _, _ = _claim_support()
    projection = lifecycle.build_public_benchmark_projection_v1(
        claim_support=support,
        promotion_release=promotion,
        fusion_configuration=config,
        protocol_deviations=(),
        limitation_codes=(lifecycle.PublicLimitationCodeV1.BENCHMARK_SCOPE_ONLY,),
    )
    policy, keys = _release_authority_context()
    identities, _, _ = _scientific_review_for_projection(
        support=support,
        projection=projection,
        policy=policy,
        keys=keys,
    )

    with pytest.raises(
        ValueError, match="scientific reviewer identity attestation does not replay"
    ):
        lifecycle.build_scientific_reviewer_attestation_v1(
            reviewer_identity_attestation=identities[0],
            authority_policy=policy,
            reviewer_identity_authority_key=keys[
                lifecycle.ReleaseAuthorityRoleV1.SCIENTIFIC_REVIEWER_IDENTITY
            ],
            reviewer_decision_key=_sha("wrong-reviewer-decision-key").encode(
                "ascii"
            ),
            claim_support=support,
            public_projection=projection,
            decision=lifecycle.ScientificReviewerDecision.APPROVE,
            critical_issue_count=0,
            findings=(),
            required_limitation_codes=(),
            reviewed_at="2026-01-01T00:09:30Z",
        )


def test_scientific_review_rejects_readdressed_approve_tampering() -> None:
    support, promotion, config, _, _ = _claim_support()
    projection = lifecycle.build_public_benchmark_projection_v1(
        claim_support=support,
        promotion_release=promotion,
        fusion_configuration=config,
        protocol_deviations=(),
        limitation_codes=(lifecycle.PublicLimitationCodeV1.BENCHMARK_SCOPE_ONLY,),
    )
    policy, keys = _release_authority_context()
    _, reviewer_decision_keys, review = _scientific_review_for_projection(
        support=support,
        projection=projection,
        policy=policy,
        keys=keys,
    )
    signed_with_limitations = review.reviewer_attestations[1]
    payload = {
        field_name: getattr(signed_with_limitations, field_name)
        for field_name in type(signed_with_limitations).model_fields
        if field_name not in {"attestation_id", "attestation_sha256"}
    }
    payload["decision"] = lifecycle.ScientificReviewerDecision.APPROVE
    payload["required_limitation_codes"] = ()
    readdressed_tamper = lifecycle._build_addressed(  # noqa: SLF001
        lifecycle.ScientificReviewerAttestationV1,
        id_field="attestation_id",
        sha_field="attestation_sha256",
        prefix="science-reviewer-v1",
        values=payload,
    )

    with pytest.raises(
        ValueError, match="scientific reviewer attestation does not exactly replay"
    ):
        lifecycle.assert_scientific_reviewer_attestation_exact_replay_v1(
            readdressed_tamper,
            authority_policy=policy,
            reviewer_identity_authority_key=keys[
                lifecycle.ReleaseAuthorityRoleV1.SCIENTIFIC_REVIEWER_IDENTITY
            ],
            reviewer_decision_key=reviewer_decision_keys[
                readdressed_tamper.reviewer_id
            ],
            claim_support=support,
            public_projection=projection,
        )


def test_release_control_attestation_rejects_foreign_projection() -> None:
    support, promotion, config, _, _ = _claim_support()
    original_projection = lifecycle.build_public_benchmark_projection_v1(
        claim_support=support,
        promotion_release=promotion,
        fusion_configuration=config,
        protocol_deviations=(),
        limitation_codes=(lifecycle.PublicLimitationCodeV1.BENCHMARK_SCOPE_ONLY,),
    )
    foreign_projection = lifecycle.build_public_benchmark_projection_v1(
        claim_support=support,
        promotion_release=promotion,
        fusion_configuration=config,
        protocol_deviations=(),
        limitation_codes=(
            lifecycle.PublicLimitationCodeV1.BENCHMARK_SCOPE_ONLY,
            lifecycle.PublicLimitationCodeV1.NO_MATERIAL_DISCOVERY_CLAIM,
        ),
    )
    policy, keys = _release_authority_context()
    _, reviewer_decision_keys, foreign_review = _scientific_review_for_projection(
        support=support,
        projection=foreign_projection,
        policy=policy,
        keys=keys,
    )
    original_controls = _release_controls_for_projection(
        projection=original_projection,
        policy=policy,
        keys=keys,
    )

    with pytest.raises(ValueError, match="release-control attestation does not replay"):
        lifecycle.build_public_release_authorization_v1(
            claim_support=support,
            public_projection=foreign_projection,
            scientific_review=foreign_review,
            authority_policy=policy,
            release_control_attestations=original_controls,
            authority_keys=keys,
            reviewer_identity_authority_key=keys[
                lifecycle.ReleaseAuthorityRoleV1.SCIENTIFIC_REVIEWER_IDENTITY
            ],
            reviewer_decision_keys=reviewer_decision_keys,
            authorized_at="2026-01-01T00:11:00Z",
        )


def test_scientific_review_rejects_reviewed_before_signed_identity() -> None:
    support, promotion, config, _, _ = _claim_support()
    projection = lifecycle.build_public_benchmark_projection_v1(
        claim_support=support,
        promotion_release=promotion,
        fusion_configuration=config,
        protocol_deviations=(),
        limitation_codes=(lifecycle.PublicLimitationCodeV1.BENCHMARK_SCOPE_ONLY,),
    )
    policy, keys = _release_authority_context()
    identity_key = keys[
        lifecycle.ReleaseAuthorityRoleV1.SCIENTIFIC_REVIEWER_IDENTITY
    ]
    identity = lifecycle.build_scientific_reviewer_identity_attestation_v1(
        authority_policy=policy,
        authority_key=identity_key,
        reviewer_id="late-identity-reviewer",
        natural_person_commitment_sha256=_sha("late-identity-natural-person"),
        private_identity_evidence_sha256=_sha("late-identity-private-evidence"),
        reviewer_decision_key=_sha("late-identity-decision-key").encode("ascii"),
        issued_at="2026-01-01T00:09:30Z",
    )

    with pytest.raises(
        ValueError, match="scientific reviewer attestation must be strictly later"
    ):
        lifecycle.build_scientific_reviewer_attestation_v1(
            reviewer_identity_attestation=identity,
            authority_policy=policy,
            reviewer_identity_authority_key=identity_key,
            reviewer_decision_key=_sha("late-identity-decision-key").encode(
                "ascii"
            ),
            claim_support=support,
            public_projection=projection,
            decision=lifecycle.ScientificReviewerDecision.APPROVE,
            critical_issue_count=0,
            findings=(),
            required_limitation_codes=(),
            reviewed_at="2026-01-01T00:09:00Z",
        )


def test_public_authorization_rejects_control_before_claim_support() -> None:
    support, promotion, config, _, _ = _claim_support()
    projection = lifecycle.build_public_benchmark_projection_v1(
        claim_support=support,
        promotion_release=promotion,
        fusion_configuration=config,
        protocol_deviations=(),
        limitation_codes=(lifecycle.PublicLimitationCodeV1.BENCHMARK_SCOPE_ONLY,),
    )
    policy, keys = _release_authority_context()
    _, reviewer_decision_keys, review = _scientific_review_for_projection(
        support=support,
        projection=projection,
        policy=policy,
        keys=keys,
    )
    premature_controls = _release_controls_for_projection(
        projection=projection,
        policy=policy,
        keys=keys,
        issued_at="2026-01-01T00:07:30Z",
    )

    with pytest.raises(ValueError, match="release-control attestation must be strictly later"):
        lifecycle.build_public_release_authorization_v1(
            claim_support=support,
            public_projection=projection,
            scientific_review=review,
            authority_policy=policy,
            release_control_attestations=premature_controls,
            authority_keys=keys,
            reviewer_identity_authority_key=keys[
                lifecycle.ReleaseAuthorityRoleV1.SCIENTIFIC_REVIEWER_IDENTITY
            ],
            reviewer_decision_keys=reviewer_decision_keys,
            authorized_at="2026-01-01T00:11:00Z",
        )
