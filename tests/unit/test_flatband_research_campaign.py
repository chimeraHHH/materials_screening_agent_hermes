from __future__ import annotations

import hashlib
from types import SimpleNamespace

import pytest

from material_agent.inspiration.models import canonical_sha256, deterministic_id
from material_agent.research import flatband_lifecycle as lifecycle
from material_agent.research.flatband_campaign import (
    FlatBandCampaignReleaseV1,
    LockedExecutionPlanV1,
    _assert_addressed,
    _assert_fusion_projection_config_v1,
    _assert_locked_execution_chronology_v1,
    _assert_locked_gold_human_inputs_post_unseal_v1,
    _assert_main_gold_expert_annotation_keys_exact_v1,
    _assert_public_authority_structural_links_v1,
    _assert_release_authority_policy_exact_v1,
    _assert_reviewer_decision_keys_exact_v1,
    _build_addressed,
    _execution_groups,
    _main_gold_execution_cells_union_v1,
    build_local_sensitivity_not_run_release_v1,
)
from material_agent.research.flatband_execution import ExecutionPhase, ResearchSystemId
from material_agent.research.flatband_lifecycle import (
    ReleaseAuthorityRoleV1,
    ReleaseControlKindV1,
    build_release_authority_policy_v1,
)


def test_campaign_schema_has_every_nonoptional_full_flow_root() -> None:
    required = {
        "source_catalog_checkpoint",
        "pilot_rounds",
        "main_sampling_policy",
        "main_candidate_pool",
        "main_eligibility",
        "main_frozen_cases",
        "main_pre_budget_closure",
        "main_private_identity_attestation",
        "release_authority_policy",
        "development_ablation_authorization",
        "local_sensitivity_authorization",
        "development_fusion_authorization",
        "locked_fusion_components_authorization",
        "locked_primary_authorization",
        "development_ablation_execution",
        "local_sensitivity_execution",
        "development_fusion_execution",
        "locked_fusion_components_execution",
        "locked_primary_execution",
        "locked_minus_e1_execution",
        "locked_minus_e2_execution",
        "locked_minus_e3_execution",
        "development_ablation_gold",
        "development_ablation_gold_verifier",
        "development_ablation_analysis",
        "development_ablation_analysis_attestation",
        "development_promotion",
        "fusion_configuration",
        "development_fusion_gold",
        "development_fusion_gold_verifier",
        "development_fusion_analysis",
        "development_fusion_analysis_attestation",
        "development_fusion_gate",
        "locked_label_seal",
        "locked_execution_plan",
        "locked_test_authorization",
        "locked_unseal_genesis_ledger",
        "locked_unseal",
        "locked_unseal_committed_ledger",
        "locked_gold",
        "locked_gold_verifier",
        "locked_analysis",
        "locked_analysis_attestation",
        "claim_support",
        "public_projection",
        "scientific_reviewer_identity_attestations",
        "scientific_reviewer_attestations",
        "scientific_review",
        "release_control_attestations",
        "public_release_authorization",
        "public_result",
    }
    fields = FlatBandCampaignReleaseV1.model_fields
    assert required <= set(fields)
    assert all(fields[name].is_required() for name in required)

    forbidden_fragments = {
        "api_key",
        "blind_key",
        "blinding_key",
        "decision_key",
        "caller_score",
        "caller_final",
        "provider_transcript",
        "local_model_output",
    }
    assert "main_gold_expert_annotation_keys" not in fields
    assert "scientific_reviewer_decision_keys" not in fields
    assert not {
        name
        for name in fields
        if any(fragment in name.casefold() for fragment in forbidden_fragments)
    }
    assert fields["chain_of_thought_stored"].default is False
    assert fields["external_expert_identity_attestation"].default == "NOT_PROVIDED"
    assert (
        fields["external_annotation_key_custody_attestation"].default
        == "NOT_PROVIDED"
    )
    assert fields["external_publication_permission_claimed"].default is False


def _authority_keys() -> dict[ReleaseAuthorityRoleV1, bytes]:
    return {
        role: bytes([index]) * 32
        for index, role in enumerate(ReleaseAuthorityRoleV1, start=1)
    }


def test_release_authority_policy_requires_exact_ephemeral_keys() -> None:
    keys = _authority_keys()
    policy = build_release_authority_policy_v1(
        authorities={
            role: (f"authority-{role.value.casefold().replace('_', '-')}", key)
            for role, key in keys.items()
        },
        frozen_at="2026-08-10T00:00:00+08:00",
    )
    _assert_release_authority_policy_exact_v1(policy, keys)

    attacked = dict(keys)
    attacked[ReleaseAuthorityRoleV1.PRIVACY_RELEASE] = b"x" * 32
    with pytest.raises(ValueError, match="does not exactly replay"):
        _assert_release_authority_policy_exact_v1(policy, attacked)


def test_reviewer_decision_keys_reject_missing_extra_and_swapped_maps() -> None:
    keys = {
        "scientific-reviewer-1": b"1" * 32,
        "scientific-reviewer-2": b"2" * 32,
    }
    identities = tuple(
        SimpleNamespace(
            reviewer_id=reviewer_id,
            reviewer_decision_key_commitment_sha256=hashlib.sha256(key).hexdigest(),
        )
        for reviewer_id, key in keys.items()
    )
    _assert_reviewer_decision_keys_exact_v1(identities, keys)

    with pytest.raises(ValueError, match="exactly one decision key"):
        _assert_reviewer_decision_keys_exact_v1(
            identities,
            {"scientific-reviewer-1": keys["scientific-reviewer-1"]},
        )
    with pytest.raises(ValueError, match="exactly one decision key"):
        _assert_reviewer_decision_keys_exact_v1(
            identities,
            {**keys, "scientific-reviewer-3": b"x" * 32},
        )
    with pytest.raises(ValueError, match="differs from signed identity"):
        _assert_reviewer_decision_keys_exact_v1(
            identities,
            {
                "scientific-reviewer-1": keys["scientific-reviewer-2"],
                "scientific-reviewer-2": keys["scientific-reviewer-1"],
            },
        )


def test_main_gold_expert_keys_reject_wrong_phase_and_swapped_keys() -> None:
    key_a = b"a" * 32
    key_b = b"b" * 32

    def gold(phase_label: str) -> SimpleNamespace:
        return SimpleNamespace(
            expert_assignment=SimpleNamespace(
                reviewers=(
                    SimpleNamespace(
                        expert_id=f"{phase_label}-reviewer",
                        annotation_key_commitment_sha256=hashlib.sha256(
                            key_a
                        ).hexdigest(),
                    ),
                ),
                adjudicators=(
                    SimpleNamespace(
                        expert_id=f"{phase_label}-adjudicator",
                        annotation_key_commitment_sha256=hashlib.sha256(
                            key_b
                        ).hexdigest(),
                    ),
                ),
            )
        )

    campaign = FlatBandCampaignReleaseV1.model_construct(
        development_ablation_gold=gold("ablation"),
        development_fusion_gold=gold("fusion"),
        locked_gold=gold("locked"),
    )
    keys = {
        ExecutionPhase.DEVELOPMENT_ABLATIONS: {
            "ablation-reviewer": key_a,
            "ablation-adjudicator": key_b,
        },
        ExecutionPhase.DEVELOPMENT_FUSION: {
            "fusion-reviewer": key_a,
            "fusion-adjudicator": key_b,
        },
        ExecutionPhase.LOCKED_PRIMARY: {
            "locked-reviewer": key_a,
            "locked-adjudicator": key_b,
        },
    }
    _assert_main_gold_expert_annotation_keys_exact_v1(campaign, keys)

    wrong_phase = dict(keys)
    wrong_phase.pop(ExecutionPhase.LOCKED_PRIMARY)
    with pytest.raises(ValueError, match="exactly three phase maps"):
        _assert_main_gold_expert_annotation_keys_exact_v1(campaign, wrong_phase)

    swapped = {phase: dict(values) for phase, values in keys.items()}
    swapped[ExecutionPhase.DEVELOPMENT_FUSION] = {
        "fusion-reviewer": key_b,
        "fusion-adjudicator": key_a,
    }
    with pytest.raises(ValueError, match="differs from frozen commitment"):
        _assert_main_gold_expert_annotation_keys_exact_v1(campaign, swapped)


def test_reviewer_approve_tamper_fails_even_after_readdressing() -> None:
    from tests.unit import test_flatband_research_lifecycle as lifecycle_fixture

    support, promotion, config, _, _ = lifecycle_fixture._claim_support()
    projection = lifecycle.build_public_benchmark_projection_v1(
        claim_support=support,
        promotion_release=promotion,
        fusion_configuration=config,
        protocol_deviations=(),
        limitation_codes=(lifecycle.PublicLimitationCodeV1.BENCHMARK_SCOPE_ONLY,),
    )
    authority_keys = _authority_keys()
    policy = lifecycle.build_release_authority_policy_v1(
        authorities={
            role: (f"authority-{role.value.casefold().replace('_', '-')}", key)
            for role, key in authority_keys.items()
        },
        frozen_at="2026-01-01T00:07:30Z",
    )
    identity_key = authority_keys[
        lifecycle.ReleaseAuthorityRoleV1.SCIENTIFIC_REVIEWER_IDENTITY
    ]
    decision_key = b"d" * 32
    identity = lifecycle.build_scientific_reviewer_identity_attestation_v1(
        authority_policy=policy,
        authority_key=identity_key,
        reviewer_id="scientific-reviewer-tamper-test",
        natural_person_commitment_sha256="1" * 64,
        private_identity_evidence_sha256="2" * 64,
        reviewer_decision_key=decision_key,
        issued_at="2026-01-01T00:08:10Z",
    )
    signed_rejection = lifecycle.build_scientific_reviewer_attestation_v1(
        reviewer_identity_attestation=identity,
        authority_policy=policy,
        reviewer_identity_authority_key=identity_key,
        reviewer_decision_key=decision_key,
        claim_support=support,
        public_projection=projection,
        decision=lifecycle.ScientificReviewerDecision.REJECT,
        critical_issue_count=1,
        findings=(),
        required_limitation_codes=(),
        reviewed_at="2026-01-01T00:09:00Z",
    )
    attacked_values = {
        name: getattr(signed_rejection, name)
        for name in lifecycle.ScientificReviewerAttestationV1.model_fields
        if name not in {"attestation_id", "attestation_sha256"}
    }
    attacked_values.update(
        {
            "decision": lifecycle.ScientificReviewerDecision.APPROVE,
            "critical_issue_count": 0,
        }
    )
    readdressed_attack = _build_addressed(
        lifecycle.ScientificReviewerAttestationV1,
        id_field="attestation_id",
        sha_field="attestation_sha256",
        prefix="science-reviewer-v1",
        values=attacked_values,
    )
    with pytest.raises(ValueError, match="does not exactly replay"):
        lifecycle.assert_scientific_reviewer_attestation_exact_replay_v1(
            readdressed_attack,
            authority_policy=policy,
            reviewer_identity_authority_key=identity_key,
            reviewer_decision_key=decision_key,
            claim_support=support,
            public_projection=projection,
        )


def _public_authority_contract_fixture(*, overlap_main_person: bool = False):
    """Minimal typed-shape fixture for campaign-owned identity joins."""

    policy = SimpleNamespace(
        policy_id="release-authority-policy",
        policy_sha256="a" * 64,
        frozen_at="2026-08-10T00:00:00+08:00",
    )
    person_a = "1" * 64
    identities = tuple(
        SimpleNamespace(
            reviewer_id=f"scientific-reviewer-{index}",
            identity_attestation_id=f"reviewer-identity-{index}",
            identity_attestation_sha256=str(index) * 64,
            natural_person_commitment_sha256=(person_a if index == 1 else "2" * 64),
            private_identity_evidence_sha256=str(index + 2) * 64,
            reviewer_decision_key_commitment_sha256=str(index + 4) * 64,
            authority_policy_id=policy.policy_id,
            authority_policy_sha256=policy.policy_sha256,
            issued_at=f"2026-08-10T00:02:0{index}+08:00",
        )
        for index in (1, 2)
    )
    reviews = tuple(
        SimpleNamespace(
            reviewer_identity_attestation=identity,
            reviewed_at=f"2026-08-10T00:03:0{index}+08:00",
            findings=(),
        )
        for index, identity in enumerate(identities, start=1)
    )
    controls = tuple(
        SimpleNamespace(
            control_kind=kind,
            authority_policy_id=policy.policy_id,
            authority_policy_sha256=policy.policy_sha256,
            issued_at=f"2026-08-10T00:04:0{index}+08:00",
        )
        for index, kind in enumerate(
            sorted(ReleaseControlKindV1, key=lambda item: item.value), start=1
        )
    )
    return SimpleNamespace(
        release_authority_policy=policy,
        main_pre_budget_closure=SimpleNamespace(
            sealed_at="2026-08-10T00:01:00+08:00"
        ),
        main_private_identity_attestation=SimpleNamespace(
            bindings=(
                SimpleNamespace(
                    natural_person_commitment_sha256=(
                        person_a if overlap_main_person else "f" * 64
                    )
                ),
            )
        ),
        scientific_reviewer_identity_attestations=identities,
        scientific_reviewer_attestations=reviews,
        release_control_attestations=controls,
        public_release_authorization=SimpleNamespace(
            authority_policy=policy,
            release_control_attestations=controls,
            authorized_at="2026-08-10T00:05:00+08:00",
        ),
    )


def test_public_scientific_reviewer_cannot_be_main_annotator() -> None:
    _assert_public_authority_structural_links_v1(
        _public_authority_contract_fixture()
    )
    with pytest.raises(ValueError, match="overlaps a Main benchmark expert"):
        _assert_public_authority_structural_links_v1(
            _public_authority_contract_fixture(overlap_main_person=True)
        )


def test_public_scientific_review_rejects_free_text_findings() -> None:
    fixture = _public_authority_contract_fixture()
    reviews = list(fixture.scientific_reviewer_attestations)
    reviews[0] = SimpleNamespace(
        **{**reviews[0].__dict__, "findings": ("caller-controlled text",)}
    )
    attacked = SimpleNamespace(
        **{
            **fixture.__dict__,
            "scientific_reviewer_attestations": tuple(reviews),
        }
    )
    with pytest.raises(ValueError, match="empty frozen template"):
        _assert_public_authority_structural_links_v1(attacked)


def test_locked_execution_plan_is_a_required_content_addressed_preimage() -> None:
    fields = FlatBandCampaignReleaseV1.model_fields
    assert fields["locked_execution_plan"].is_required()
    plan_fields = LockedExecutionPlanV1.model_fields
    assert plan_fields["locked_component_authorization"].is_required()
    assert plan_fields["locked_primary_authorization"].is_required()
    assert plan_fields["component_system_configs"].is_required()
    assert plan_fields["analysis_arms"].is_required()
    assert plan_fields["formal_execution_release_count"].default == 5
    assert plan_fields["results_or_labels_embedded"].default is False


def test_gold_aggregates_use_exact_one_one_four_outer_execution_roots() -> None:
    ablation = SimpleNamespace(release_id="ablation-outer")
    development_fusion = SimpleNamespace(release_id="dev-fusion-outer")
    locked = tuple(
        SimpleNamespace(release_id=f"locked-outer-{index}")
        for index in range(4)
    )
    campaign = FlatBandCampaignReleaseV1.model_construct(
        development_ablation_execution=ablation,
        development_fusion_execution=development_fusion,
        locked_primary_execution=locked[0],
        locked_minus_e1_execution=locked[1],
        locked_minus_e2_execution=locked[2],
        locked_minus_e3_execution=locked[3],
    )
    groups = _execution_groups(campaign)
    assert groups == {
        ExecutionPhase.DEVELOPMENT_ABLATIONS: (ablation,),
        ExecutionPhase.DEVELOPMENT_FUSION: (development_fusion,),
        ExecutionPhase.LOCKED_PRIMARY: locked,
    }


def test_campaign_content_address_detects_semantic_tamper_without_fixture() -> None:
    # A complete real object intentionally requires Main120 and is not fabricated
    # by this light contract test.  The address attack can still exercise the
    # campaign-owned identity surface on a deliberately unvalidated construct.
    values = {
        "assembled_at": "2026-08-10T17:00:00+08:00",
        "external_execution_attestation": "NOT_PROVIDED",
        "external_unseal_compare_and_swap_attestation": "NOT_PROVIDED",
        "global_unseal_uniqueness_claimed": False,
        "real_main_structure_output_executed": False,
        "benchmark_performance_claimed": False,
        "scientific_conclusion": False,
    }
    draft = FlatBandCampaignReleaseV1.model_construct(**values)
    digest = canonical_sha256(
        draft.model_dump(
            mode="python", exclude={"campaign_id", "campaign_sha256"}
        )
    )
    addressed = draft.model_copy(
        update={
            "campaign_id": deterministic_id(
                "flatband-campaign-v1", {"campaign_sha256": digest}
            ),
            "campaign_sha256": digest,
        }
    )
    _assert_addressed(
        addressed,
        id_field="campaign_id",
        sha_field="campaign_sha256",
        prefix="flatband-campaign-v1",
    )

    tampered = addressed.model_copy(
        update={"assembled_at": "2026-08-10T17:00:01+08:00"}
    )
    with pytest.raises(ValueError, match="campaign semantic content"):
        _assert_addressed(
            tampered,
            id_field="campaign_id",
            sha_field="campaign_sha256",
            prefix="flatband-campaign-v1",
        )


def test_fusion_projection_rejects_query_hash_injection() -> None:
    from tests.unit import test_flatband_research_analysis_v2 as analysis_fixture

    baseline = analysis_fixture._config(ResearchSystemId.B0)
    e2 = analysis_fixture._config(ResearchSystemId.E2_A)
    e3 = analysis_fixture._config(ResearchSystemId.E3)
    components = (ResearchSystemId.E2_A, ResearchSystemId.E3)
    full = analysis_fixture._config(
        ResearchSystemId.FUSION,
        fusion_components=components,
    )
    isolated = {
        ResearchSystemId.B0: baseline,
        ResearchSystemId.E2_A: e2,
        ResearchSystemId.E3: e3,
    }
    _assert_fusion_projection_config_v1(
        config=full,
        components=components,
        isolated=isolated,
    )

    injected = full.model_copy(update={"query_plan_sha256": "f" * 64})
    with pytest.raises(ValueError, match="prompt/query"):
        _assert_fusion_projection_config_v1(
            config=injected,
            components=components,
            isolated=isolated,
        )


def test_local_sensitivity_rejects_structure_only_authorization_fixture(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A Main test double must not become a campaign authorization preimage.

    ``test_flatband_research_main`` deliberately bypasses scientific replay to
    test its own wiring.  The campaign boundary is intentionally stricter: it
    must deep-revalidate that object before it can emit even a zero-output
    NOT_RUN closure.
    """

    from tests.unit import test_flatband_research_main as main_fixture

    main_fixture._contract_structure_only.__wrapped__(monkeypatch)
    chain = main_fixture._chain()
    with pytest.raises(ValueError):
        build_local_sensitivity_not_run_release_v1(
            phase_authorization=chain.local_sensitivity,
            decided_at="2026-08-10T00:10:00+08:00",
        )


def test_local_sensitivity_contract_is_zero_output_and_non_promotable() -> None:
    fields = FlatBandCampaignReleaseV1.model_fields
    assert fields["local_sensitivity_execution"].is_required()

    from material_agent.research.flatband_campaign import (
        LocalSensitivityNotRunReleaseV1,
    )

    local_fields = LocalSensitivityNotRunReleaseV1.model_fields
    assert local_fields["decision"].default == "NOT_RUN_USER_PROHIBITED"
    assert local_fields["execution_release_count"].default == 0
    assert local_fields["output_artifact_count"].default == 0
    assert local_fields["local_model_invocation_count"].default == 0
    assert local_fields["promotion_or_locked_use_allowed"].default is False


def _chronology_contract_fixture(
    *,
    component_budget_at: str = "2026-08-10T00:06:00+08:00",
    primary_assembled_at: str = "2026-08-10T00:09:00+08:00",
) -> FlatBandCampaignReleaseV1:
    """Minimal unvalidated shape for attacks on the campaign-owned time cut."""

    def execution(*, budget_at: str, assembled_at: str) -> SimpleNamespace:
        return SimpleNamespace(
            budget_manifests=(SimpleNamespace(frozen_at=budget_at),),
            rankings=(),
            terminal_results=(),
            assembled_at=assembled_at,
        )

    component = SimpleNamespace(
        execution_release=execution(
            budget_at=component_budget_at,
            assembled_at="2026-08-10T00:06:15+08:00",
        ),
        assembled_at="2026-08-10T00:06:30+08:00",
        cells=(),
    )

    def parent(*, assembled_at: str) -> SimpleNamespace:
        return SimpleNamespace(
            execution_release=execution(
                budget_at="2026-08-10T00:07:00+08:00",
                assembled_at="2026-08-10T00:08:00+08:00",
            ),
            assembled_at=assembled_at,
            cells=(),
        )

    return FlatBandCampaignReleaseV1.model_construct(
        locked_execution_plan=SimpleNamespace(
            frozen_at="2026-08-10T00:04:00+08:00"
        ),
        locked_test_authorization=SimpleNamespace(
            authorized_at="2026-08-10T00:05:00+08:00"
        ),
        locked_unseal=SimpleNamespace(
            unsealed_at="2026-08-10T00:10:00+08:00"
        ),
        locked_fusion_components_execution=component,
        locked_primary_execution=parent(assembled_at=primary_assembled_at),
        locked_minus_e1_execution=parent(
            assembled_at="2026-08-10T00:09:00+08:00"
        ),
        locked_minus_e2_execution=parent(
            assembled_at="2026-08-10T00:09:00+08:00"
        ),
        locked_minus_e3_execution=parent(
            assembled_at="2026-08-10T00:09:00+08:00"
        ),
    )


def test_locked_chronology_rejects_pre_authorization_budget() -> None:
    attacked = _chronology_contract_fixture(
        component_budget_at="2026-08-10T00:04:59+08:00"
    )
    with pytest.raises(ValueError, match="budget must follow lifecycle authorization"):
        _assert_locked_execution_chronology_v1(attacked)


def test_locked_chronology_rejects_post_unseal_execution_artifact() -> None:
    attacked = _chronology_contract_fixture(
        primary_assembled_at="2026-08-10T00:10:01+08:00"
    )
    with pytest.raises(ValueError, match="must precede locked-label unseal"):
        _assert_locked_execution_chronology_v1(attacked)


def test_locked_chronology_rejects_valid_signed_label_before_unseal() -> None:
    from tests.unit import test_flatband_research_main_gold as gold_fixture

    trace = gold_fixture._arm_trace(
        gold_fixture._arm_config(ResearchSystemId.B0)
    )
    assignment = gold_fixture._assignment()
    manifests, identity_maps = gold_fixture._materials(trace, assignment)
    identity_map = identity_maps[0]
    label = gold_fixture.build_main_raw_label(
        manifests[0],
        identity_map,
        blinded_unit_id=identity_map.entries[0].blinded_unit_id,
        annotation_guide_sha256=gold_fixture.GUIDE_SHA,
        status=gold_fixture.MainGoldStatus.ASSESSABLE,
        relevance_grade=2,
        evidence_gain=1,
        submitted_at="2026-08-10T13:00:00+08:00",
        reviewer_annotation_key=gold_fixture.EXPERT_ANNOTATION_KEYS[
            identity_map.reviewer_id
        ],
    )
    assert label.annotation_signature_hmac_sha256 != "0" * 64

    attacked = SimpleNamespace(
        locked_unseal=SimpleNamespace(
            unsealed_at="2026-08-10T13:00:01+08:00"
        ),
        locked_gold=SimpleNamespace(
            raw_labels=(label,),
            raw_duplicate_partitions=(),
            adjudications=(),
            duplicate_adjudications=(),
        ),
    )
    with pytest.raises(ValueError, match="locked raw label must be strictly later"):
        _assert_locked_gold_human_inputs_post_unseal_v1(attacked)

    allowed = SimpleNamespace(
        locked_unseal=SimpleNamespace(
            unsealed_at="2026-08-10T12:59:59+08:00"
        ),
        locked_gold=attacked.locked_gold,
    )
    _assert_locked_gold_human_inputs_post_unseal_v1(allowed)


def test_gold_input_union_retains_failed_cells(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The bridge's structural FAILED fixture must remain in Gold custody."""

    from tests.unit import test_flatband_research_main_execution as execution_fixture

    execution_fixture._structural_only.__wrapped__(monkeypatch)
    authorization, execution, components = (
        execution_fixture._all_failed_development_fusion()
    )
    release = execution_fixture.build_main_phase_execution_release_v1(
        phase_authorization=authorization,
        execution_release=execution,
        arm_traces=(),
        component_execution_release=components,
        assembled_at="2026-08-10T00:05:00+08:00",
    )
    cells = _main_gold_execution_cells_union_v1((release,))
    assert len(cells) == 120
    assert all(item.arm_trace is None for item in cells)
