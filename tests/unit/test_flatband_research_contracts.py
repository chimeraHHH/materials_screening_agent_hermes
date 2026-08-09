from __future__ import annotations

from typing import Any, TypeVar

import pytest
from pydantic import ValidationError

from material_agent.inspiration.models import StrictModel, canonical_sha256, deterministic_id
from material_agent.research.flatband_contracts import (
    AdjudicationStatus,
    AnnotationRefV1,
    AssertedEvidenceRelation,
    Assessability,
    BandwidthScope,
    BenchmarkSplit,
    BenchmarkSplitManifestV1,
    BlindedEvaluationUnitV1,
    BlindingManifestV1,
    BridgeJudgmentV1,
    BridgeVerdict,
    Dimensionality,
    EvidenceClaimType,
    EvidenceJudgmentV1,
    EvidenceSpanRefV1,
    ExpertAdjudicationV1,
    ExpertEvidenceRelation,
    ExpertRegistrationV1,
    ExpertRegistryV1,
    ExpertRole,
    FalsificationPlanV1,
    FlatBandBenchmarkCaseV1,
    FlatBandEvidenceV1,
    HardFailReason,
    HypothesisPacketV1,
    MagneticOrder,
    MechanismFamily,
    LocalSemanticModelUseV1,
    LlmUseRecordV1,
    ObservedBandClass,
    RankedPacketRefV1,
    RankingContributionV1,
    RawExpertAnnotationV1,
    ResearchRunLedgerV1,
    ResearchRunOutcome,
    SocState,
    SourceRequestAllocationV1,
    SourceRecordRefV1,
    SplitCaseRefV1,
    SplitManifestKind,
    SystemRankingV1,
    TargetBandClass,
    TriStateJudgment,
    assert_blinding_covers_rankings,
    assert_manifests_case_disjoint,
    observed_band_class,
)


ModelT = TypeVar("ModelT", bound=StrictModel)
SHA_A = "a" * 64
SHA_B = "b" * 64
SHA_C = "c" * 64


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


def _source() -> SourceRecordRefV1:
    return SourceRecordRefV1(
        source_id="cod",
        source_record_id="cod-1000000",
        canonical_url="https://www.crystallography.net/cod/1000000.html",
        source_version="revision-1",
        license_expression="CC0-1.0",
        accessed_at="2026-08-09T12:00:00+08:00",
        raw_sha256=SHA_A,
        public_redistribution_allowed=True,
    )


def _evidence(*, bandwidth: float | None = 0.08) -> FlatBandEvidenceV1:
    values = {
        "source_record": _source(),
        "claim_type": EvidenceClaimType.PARSED,
        "bandwidth_e_v": bandwidth,
        "fermi_distance_e_v": None if bandwidth is None else 0.2,
        "observed_class": observed_band_class(bandwidth_e_v=bandwidth),
        "bandwidth_scope": (
            BandwidthScope.STRUCTURAL_PRIOR_ONLY
            if bandwidth is None
            else BandwidthScope.HSL_FULL_PATH
        ),
        "k_sampling": "source-declared path",
        "soc_state": SocState.NOT_INCLUDED,
        "magnetic_order": MagneticOrder.PARAMAGNETIC_OR_UNKNOWN,
        "band_tracking_method": "source-declared ordering",
    }
    draft = FlatBandEvidenceV1.model_construct(**values)
    digest = canonical_sha256(
        draft.model_dump(
            mode="python", exclude={"evidence_id", "evidence_sha256"}
        )
    )
    return FlatBandEvidenceV1(
        **values,
        evidence_id=deterministic_id("band-evidence", {"evidence_sha256": digest}),
        evidence_sha256=digest,
    )


def _case() -> FlatBandBenchmarkCaseV1:
    values = {
        "parent_label": "Open flat-band seed",
        "formula": "TiSe2",
        "structure_sha256": SHA_B,
        "source_records": (_source(),),
        "target_class": TargetBandClass.FB100,
        "dimensionality": Dimensionality.TWO_D,
        "frozen_request": "Propose a mechanism-bounded route toward a near-Fermi flat band.",
        "frozen_requirement_sha256": SHA_C,
        "hard_constraints": ("preserve dimensionality",),
        "soft_preferences": ("prefer explicit falsification",),
        "forbidden_transformations": ("REMOVE_ALL_PARENT_SITES",),
        "seed_evidence": (_evidence(),),
        "primary_mechanism_stratum": MechanismFamily.LATTICE_INTERFERENCE,
        "leakage_group_ids": ("article-doi-group-1", "prototype-group-1"),
        "public_release_allowed": True,
    }
    return _identified(
        FlatBandBenchmarkCaseV1,
        id_field="case_id",
        sha_field="case_sha256",
        prefix="flatband-case",
        values=values,
    )


def _evidence_link() -> EvidenceSpanRefV1:
    return EvidenceSpanRefV1(
        evidence_link_id="evidence-link-1",
        source_id="crossref",
        source_record_id="doi-10.1000-example",
        source_url="https://doi.org/10.1000/example",
        span_id="span-1",
        span_sha256=SHA_A,
        asserted_relation=AssertedEvidenceRelation.SUPPORT,
        claim_summary="The source reports a mechanism-relevant localized mode.",
        private_text_artifact_uri="artifact://private/spans/span-1.txt",
    )


def _packet() -> HypothesisPacketV1:
    case = _case()
    values = {
        "case_id": case.case_id,
        "case_sha256": case.case_sha256,
        "candidate_structure_sha256": SHA_C,
        "strict_structure_group_id": "structure-group-1",
        "strict_hypothesis_group_id": "hypothesis-group-1",
        "transformation_operator_id": "SUBSTITUTE_EQUIVALENT_SITE_V1",
        "transformation_summary": "A bounded substitution preserving the parent topology.",
        "source_domain": "photonic lattices",
        "mechanism_family": MechanismFamily.LATTICE_INTERFERENCE,
        "shared_invariant": "Destructive interference on a connectivity-preserving sublattice.",
        "transfer_principle": "Preserve the connectivity while changing orbital participation.",
        "required_conditions": ("dominant local hopping",),
        "breaking_conditions": ("large symmetry-breaking hopping",),
        "evidence_links": (_evidence_link(),),
        "contradictions": (),
        "falsification": FalsificationPlanV1(
            observable="Tracked-band width",
            method="Calculate a SOC-resolved band structure on the frozen candidate.",
            pass_condition="The tracked manifold satisfies the case bandwidth target.",
            fail_condition="The tracked manifold exceeds the case bandwidth target.",
        ),
    }
    return _identified(
        HypothesisPacketV1,
        id_field="packet_id",
        sha_field="packet_sha256",
        prefix="hypothesis-packet",
        values=values,
    )


def _correct_bridge() -> BridgeJudgmentV1:
    return BridgeJudgmentV1(
        source_mechanism=TriStateJudgment.PASS,
        shared_invariant=TriStateJudgment.PASS,
        target_mapping=TriStateJudgment.PASS,
        transferable_control=TriStateJudgment.PASS,
        required_conditions=TriStateJudgment.PASS,
        breaking_conditions=TriStateJudgment.PASS,
        contradiction_handling=TriStateJudgment.PASS,
        overall=BridgeVerdict.CORRECT,
    )


def _annotation(reviewer_id: str = "reviewer-a") -> RawExpertAnnotationV1:
    packet = _packet()
    values = {
        "blinded_unit_id": "blind-unit-1",
        "case_id": packet.case_id,
        "case_sha256": packet.case_sha256,
        "packet_id": packet.packet_id,
        "packet_sha256": packet.packet_sha256,
        "reviewer_id": reviewer_id,
        "review_round": 1,
        "annotation_guide_sha256": SHA_A,
        "assessability": Assessability.ASSESSABLE,
        "relevance_grade": 3,
        "evidence_valid": True,
        "evidence_judgments": (
            EvidenceJudgmentV1(
                evidence_link_id="evidence-link-1",
                expert_relation=ExpertEvidenceRelation.VALID_SUPPORT,
                scope_match=True,
                overclaim=False,
                reason_code="DIRECT_SCOPE_MATCH",
            ),
        ),
        "bridge_judgment": _correct_bridge(),
        "mechanism_family": MechanismFamily.LATTICE_INTERFERENCE,
        "strict_hypothesis_group_id": "hypothesis-group-1",
        "hard_fail_reasons": (),
        "confidence": 4,
        "rationale": "The bounded packet states the invariant, evidence, limits, and falsifier.",
        "started_at": "2026-08-09T12:00:00+08:00",
        "submitted_at": "2026-08-09T12:10:00+08:00",
    }
    return _identified(
        RawExpertAnnotationV1,
        id_field="annotation_id",
        sha_field="annotation_sha256",
        prefix="expert-annotation",
        values=values,
    )


def _run_ledger_values() -> dict[str, Any]:
    case = _case()
    return {
        "system_id": "B0",
        "system_config_sha256": SHA_A,
        "git_commit": "1" * 40,
        "run_id": "run-b0-1",
        "case_id": case.case_id,
        "case_sha256": case.case_sha256,
        "source_allocations": (
            SourceRequestAllocationV1(
                source_id="crossref",
                max_physical_requests=8,
                actual_physical_requests=7,
                raw_hits=80,
                unique_documents=41,
                response_bytes=120_000,
                cache_hits=1,
            ),
        ),
        "actual_total_physical_requests": 7,
        "llm": LlmUseRecordV1(enabled=False),
        "local_semantic_model": LocalSemanticModelUseV1(enabled=False),
        "max_walltime_seconds": 300,
        "walltime_ms": 12_500,
        "outcome": ResearchRunOutcome.SUCCEEDED,
        "ranking_id": "system-ranking-1",
        "ranking_sha256": SHA_B,
        "ledger_created_at": "2026-08-09T12:15:00+08:00",
    }


def _ranking(system_id: str) -> SystemRankingV1:
    packet = _packet()
    values = {
        "system_id": system_id,
        "system_config_sha256": SHA_A,
        "run_id": f"run-{system_id.casefold()}",
        "case_id": packet.case_id,
        "case_sha256": packet.case_sha256,
        "ranked_packets": (
            RankedPacketRefV1(
                selection_rank=1,
                packet_id=packet.packet_id,
                packet_sha256=packet.packet_sha256,
                strict_hypothesis_group_id=packet.strict_hypothesis_group_id,
            ),
        ),
        "underfill_reason_codes": ("NO_MORE_VALID_PACKETS",),
        "cost_ledger_sha256": SHA_B,
    }
    return _identified(
        SystemRankingV1,
        id_field="ranking_id",
        sha_field="ranking_sha256",
        prefix="system-ranking",
        values=values,
    )


def _expert(
    expert_id: str,
    role: ExpertRole,
    *,
    guide_sha256: str = SHA_A,
    calibrated: bool = True,
) -> ExpertRegistrationV1:
    return ExpertRegistrationV1(
        expert_id=expert_id,
        role=role,
        domain_expertise=("electronic-structure", "flat-band-materials"),
        qualification_summary=(
            "Research experience in electronic structures and flat-band materials."
        ),
        conflict_disclosures=("NO_DECLARED_CONFLICT",),
        annotation_guide_sha256=guide_sha256,
        calibration_completed=calibrated,
        confirmation_reference=f"confirmation-{expert_id}",
        registered_at="2026-08-09T12:00:00+08:00",
    )


def _expert_registry_values() -> dict[str, Any]:
    return {
        "annotation_guide_sha256": SHA_A,
        "calibration_set_sha256": SHA_C,
        "experts": (
            _expert("adjudicator-01", ExpertRole.ADJUDICATOR),
            _expert("reviewer-01", ExpertRole.REVIEWER),
            _expert("reviewer-02", ExpertRole.REVIEWER),
        ),
    }


def test_band_strata_are_explicit_and_source_labels_do_not_become_truth() -> None:
    assert observed_band_class(bandwidth_e_v=0.10) is ObservedBandClass.FB100
    assert observed_band_class(bandwidth_e_v=0.30) is ObservedBandClass.NB300
    assert observed_band_class(bandwidth_e_v=0.50) is ObservedBandClass.BORDER500
    assert observed_band_class(bandwidth_e_v=0.50001) is ObservedBandClass.OUT_OF_SCOPE
    assert _evidence(bandwidth=None).observed_class is ObservedBandClass.UNQUANTIFIED

    with pytest.raises(ValidationError, match="observed class differs"):
        _evidence().model_copy(update={"observed_class": ObservedBandClass.NB300})
        FlatBandEvidenceV1.model_validate(
            {
                **_evidence().model_dump(mode="python"),
                "observed_class": ObservedBandClass.NB300,
            }
        )


def test_case_and_packet_are_content_addressed_hypotheses() -> None:
    case = _case()
    packet = _packet()
    assert packet.case_id == case.case_id
    assert packet.claim_type == "HYPOTHESIS"
    assert packet.validated_material is False
    assert packet.scientific_conclusion is False

    tampered = packet.model_dump(mode="python")
    tampered["shared_invariant"] = "A different invariant."
    with pytest.raises(ValidationError, match="packet SHA-256"):
        HypothesisPacketV1.model_validate(tampered)


def test_ranking_keeps_rank_identity_and_underfill_fail_closed() -> None:
    packet = _packet()
    values = {
        "system_id": "B0",
        "system_config_sha256": SHA_A,
        "run_id": "run-1",
        "case_id": packet.case_id,
        "case_sha256": packet.case_sha256,
        "ranked_packets": (
            RankedPacketRefV1(
                selection_rank=1,
                packet_id=packet.packet_id,
                packet_sha256=packet.packet_sha256,
                strict_hypothesis_group_id=packet.strict_hypothesis_group_id,
            ),
        ),
        "underfill_reason_codes": ("NO_MORE_VALID_PACKETS",),
        "cost_ledger_sha256": SHA_B,
    }
    ranking = _identified(
        SystemRankingV1,
        id_field="ranking_id",
        sha_field="ranking_sha256",
        prefix="system-ranking",
        values=values,
    )
    assert ranking.requested_top_k == 5
    with pytest.raises(ValidationError, match="underfilled ranking requires"):
        _identified(
            SystemRankingV1,
            id_field="ranking_id",
            sha_field="ranking_sha256",
            prefix="system-ranking",
            values={**values, "underfill_reason_codes": ()},
        )


def test_raw_annotation_is_blind_append_only_and_grade_three_is_not_truth() -> None:
    annotation = _annotation()
    schema_text = str(RawExpertAnnotationV1.model_json_schema())
    assert "system_id" not in schema_text
    assert "selection_rank" not in schema_text
    assert annotation.sealed is True
    assert annotation.scientific_conclusion is False

    payload = annotation.model_dump(mode="python")
    payload["evidence_judgments"] = (
        EvidenceJudgmentV1(
            evidence_link_id="evidence-link-1",
            expert_relation=ExpertEvidenceRelation.CONTEXT_ONLY,
            scope_match=False,
            overclaim=True,
            reason_code="KEYWORD_ONLY",
        ),
    )
    payload["annotation_sha256"] = SHA_A
    with pytest.raises(ValidationError, match="valid supporting evidence"):
        RawExpertAnnotationV1.model_validate(payload)


def test_invalid_system_packet_must_be_zero_with_a_reason() -> None:
    packet = _packet()
    values = {
        "blinded_unit_id": "blind-invalid-1",
        "case_id": packet.case_id,
        "case_sha256": packet.case_sha256,
        "packet_id": packet.packet_id,
        "packet_sha256": packet.packet_sha256,
        "reviewer_id": "reviewer-a",
        "review_round": 1,
        "annotation_guide_sha256": SHA_A,
        "assessability": Assessability.SYSTEM_PACKET_INVALID,
        "relevance_grade": 0,
        "evidence_valid": False,
        "bridge_judgment": BridgeJudgmentV1(
            source_mechanism=TriStateJudgment.UNASSESSABLE,
            shared_invariant=TriStateJudgment.UNASSESSABLE,
            target_mapping=TriStateJudgment.UNASSESSABLE,
            transferable_control=TriStateJudgment.UNASSESSABLE,
            required_conditions=TriStateJudgment.UNASSESSABLE,
            breaking_conditions=TriStateJudgment.UNASSESSABLE,
            contradiction_handling=TriStateJudgment.UNASSESSABLE,
            overall=BridgeVerdict.UNASSESSABLE,
        ),
        "mechanism_family": MechanismFamily.OTHER_OR_UNKNOWN,
        "strict_hypothesis_group_id": "invalid-packet-cluster",
        "hard_fail_reasons": (HardFailReason.MALFORMED_PACKET,),
        "confidence": 5,
        "rationale": "The packet is malformed and receives a frozen zero.",
        "started_at": "2026-08-09T12:00:00+08:00",
        "submitted_at": "2026-08-09T12:01:00+08:00",
    }
    annotation = _identified(
        RawExpertAnnotationV1,
        id_field="annotation_id",
        sha_field="annotation_sha256",
        prefix="expert-annotation",
        values=values,
    )
    assert annotation.relevance_grade == 0


def test_adjudication_binds_two_raw_reviews_and_distinct_adjudicator() -> None:
    first = _annotation("reviewer-a")
    second = _annotation("reviewer-b")
    values = {
        "blinded_unit_id": first.blinded_unit_id,
        "case_id": first.case_id,
        "case_sha256": first.case_sha256,
        "packet_id": first.packet_id,
        "packet_sha256": first.packet_sha256,
        "raw_annotations": tuple(
            sorted(
                (
                    AnnotationRefV1(
                        annotation_id=item.annotation_id,
                        annotation_sha256=item.annotation_sha256,
                        reviewer_id=item.reviewer_id,
                    )
                    for item in (first, second)
                ),
                key=lambda item: item.reviewer_id,
            )
        ),
        "adjudicator_id": "adjudicator-c",
        "annotation_guide_sha256": SHA_A,
        "disagreement_fields": ("relevance_grade",),
        "status": AdjudicationStatus.RESOLVED,
        "final_relevance_grade": 3,
        "final_evidence_valid": True,
        "final_bridge_judgment": _correct_bridge(),
        "final_mechanism_family": MechanismFamily.LATTICE_INTERFERENCE,
        "final_strict_hypothesis_group_id": "hypothesis-group-1",
        "resolution_reason_code": "PHYSICS_RATIONALE",
        "rationale": "The adjudicator resolved the disagreement without system identity.",
        "adjudicated_at": "2026-08-09T13:00:00+08:00",
    }
    adjudication = _identified(
        ExpertAdjudicationV1,
        id_field="adjudication_id",
        sha_field="adjudication_sha256",
        prefix="expert-adjudication",
        values=values,
    )
    assert adjudication.status is AdjudicationStatus.RESOLVED
    with pytest.raises(ValidationError, match="adjudicator must differ"):
        _identified(
            ExpertAdjudicationV1,
            id_field="adjudication_id",
            sha_field="adjudication_sha256",
            prefix="expert-adjudication",
            values={**values, "adjudicator_id": "reviewer-a"},
        )


def _pilot_manifest(prefix: str, kind: SplitManifestKind) -> BenchmarkSplitManifestV1:
    split = BenchmarkSplit.PILOT_R1 if kind is SplitManifestKind.PILOT_R1 else BenchmarkSplit.PILOT_R2
    cases = tuple(
        SplitCaseRefV1(
            case_id=f"{prefix}-case-{index:02d}",
            case_sha256=f"{index + 1:064x}",
            split=split,
            target_class=TargetBandClass.FB100 if index < 15 else TargetBandClass.NB300,
            dimensionality=Dimensionality.TWO_D if index % 2 == 0 else Dimensionality.THREE_D,
            primary_mechanism_stratum=MechanismFamily.LATTICE_INTERFERENCE,
            leakage_group_ids=(f"{prefix}-group-{index:02d}",),
        )
        for index in range(30)
    )
    values = {"manifest_kind": kind, "split_seed": 7, "cases": cases}
    return _identified(
        BenchmarkSplitManifestV1,
        id_field="manifest_id",
        sha_field="manifest_sha256",
        prefix="split-manifest",
        values=values,
    )


def test_pilot_split_is_exact_and_rounds_are_case_and_group_disjoint() -> None:
    round_one = _pilot_manifest("r1", SplitManifestKind.PILOT_R1)
    round_two = _pilot_manifest("r2", SplitManifestKind.PILOT_R2)
    assert len(round_one.cases) == 30
    assert_manifests_case_disjoint(round_one, round_two)

    overlapping_values = round_two.model_dump(
        mode="python", exclude={"manifest_id", "manifest_sha256"}
    )
    overlapping_values["cases"] = (
        round_two.cases[0].model_copy(
            update={"leakage_group_ids": round_one.cases[0].leakage_group_ids}
        ),
        *round_two.cases[1:],
    )
    overlapping = _identified(
        BenchmarkSplitManifestV1,
        id_field="manifest_id",
        sha_field="manifest_sha256",
        prefix="split-manifest",
        values=overlapping_values,
    )
    with pytest.raises(ValueError, match="leakage group appears"):
        assert_manifests_case_disjoint(round_one, overlapping)


def test_research_run_ledger_closes_requests_and_forbids_body_or_pdf_reads() -> None:
    ledger = _identified(
        ResearchRunLedgerV1,
        id_field="ledger_id",
        sha_field="ledger_sha256",
        prefix="research-ledger",
        values=_run_ledger_values(),
    )
    assert ledger.max_total_physical_requests == 8
    assert ledger.actual_total_physical_requests == 7
    assert ledger.article_body_fetch_requests == 0
    assert ledger.full_pdf_reads == 0
    assert ledger.llm.enabled is False
    assert ledger.scientific_conclusion is False

    invalid_body_read = ledger.model_dump(mode="python")
    invalid_body_read["article_body_fetch_requests"] = 1
    with pytest.raises(ValidationError, match="Input should be 0"):
        ResearchRunLedgerV1.model_validate(invalid_body_read)

    invalid_allocation = {
        **_run_ledger_values(),
        "source_allocations": (
            SourceRequestAllocationV1(
                source_id="crossref",
                max_physical_requests=7,
                actual_physical_requests=7,
                raw_hits=80,
                unique_documents=41,
                response_bytes=120_000,
            ),
        ),
    }
    with pytest.raises(ValidationError, match="close to eight"):
        _identified(
            ResearchRunLedgerV1,
            id_field="ledger_id",
            sha_field="ledger_sha256",
            prefix="research-ledger",
            values=invalid_allocation,
        )


def test_llm_and_local_semantic_use_require_pinned_identity_and_budget() -> None:
    llm = LlmUseRecordV1(
        enabled=True,
        provider="openai",
        model="reasoning-model",
        revision="frozen-revision",
        prompt_sha256=SHA_A,
        tokenizer_sha256=SHA_B,
        max_calls=2,
        actual_calls=1,
        max_input_tokens=12_000,
        actual_input_tokens=2_000,
        max_output_tokens=4_000,
        actual_output_tokens=700,
        metadata_packet_limit=20,
    )
    assert llm.actual_calls == 1

    with pytest.raises(ValidationError, match="complete identity"):
        LlmUseRecordV1(
            enabled=True,
            provider="openai",
            model="reasoning-model",
            revision="frozen-revision",
            prompt_sha256=SHA_A,
            max_calls=2,
            max_input_tokens=12_000,
            max_output_tokens=4_000,
            metadata_packet_limit=20,
        )

    semantic = LocalSemanticModelUseV1(
        enabled=True,
        bundle_sha256=SHA_A,
        tokenizer_sha256=SHA_B,
        model_card_sha256=SHA_C,
        vector_dimension=768,
        input_tokens=5_000,
    )
    assert semantic.vector_dimension == 768
    with pytest.raises(ValidationError, match="empty record"):
        LocalSemanticModelUseV1(enabled=False, input_tokens=1)


def test_expert_registry_requires_two_reviewers_one_adjudicator_and_one_guide() -> None:
    registry = _identified(
        ExpertRegistryV1,
        id_field="registry_id",
        sha_field="registry_sha256",
        prefix="expert-registry",
        values=_expert_registry_values(),
    )
    assert [expert.role for expert in registry.experts].count(
        ExpertRole.REVIEWER
    ) == 2
    assert registry.public_identity_mode == "PSEUDONYMOUS"

    mismatched_guide = {
        **_expert_registry_values(),
        "experts": (
            _expert("adjudicator-01", ExpertRole.ADJUDICATOR),
            _expert("reviewer-01", ExpertRole.REVIEWER),
            _expert("reviewer-02", ExpertRole.REVIEWER, guide_sha256=SHA_B),
        ),
    }
    with pytest.raises(ValidationError, match="same annotation guide"):
        _identified(
            ExpertRegistryV1,
            id_field="registry_id",
            sha_field="registry_sha256",
            prefix="expert-registry",
            values=mismatched_guide,
        )

    uncalibrated = {
        **_expert_registry_values(),
        "experts": (
            _expert("adjudicator-01", ExpertRole.ADJUDICATOR),
            _expert("reviewer-01", ExpertRole.REVIEWER),
            _expert("reviewer-02", ExpertRole.REVIEWER, calibrated=False),
        ),
    }
    with pytest.raises(ValidationError, match="complete calibration"):
        _identified(
            ExpertRegistryV1,
            id_field="registry_id",
            sha_field="registry_sha256",
            prefix="expert-registry",
            values=uncalibrated,
        )


def test_private_blinding_manifest_pools_exact_packets_and_covers_every_rank() -> None:
    rankings = (_ranking("B0"), _ranking("E1"))
    packet = _packet()
    contributions = tuple(
        RankingContributionV1(
            system_id=ranking.system_id,
            run_id=ranking.run_id,
            ranking_id=ranking.ranking_id,
            ranking_sha256=ranking.ranking_sha256,
            selection_rank=1,
        )
        for ranking in rankings
    )
    unit = BlindedEvaluationUnitV1(
        annotation_order=1,
        blinded_unit_id="blind-opaque-0001",
        case_id=packet.case_id,
        case_sha256=packet.case_sha256,
        packet_id=packet.packet_id,
        packet_sha256=packet.packet_sha256,
        strict_hypothesis_group_id=packet.strict_hypothesis_group_id,
        contributions=contributions,
    )
    values = {
        "split_manifest_id": "split-manifest-pilot-r1",
        "split_manifest_sha256": SHA_A,
        "expert_registry_id": "expert-registry-1",
        "expert_registry_sha256": SHA_B,
        "randomization_seed": 73,
        "reviewer_ids": ("reviewer-01", "reviewer-02"),
        "adjudicator_id": "adjudicator-01",
        "units": (unit,),
        "sealed_at": "2026-08-09T12:20:00+08:00",
    }
    manifest = _identified(
        BlindingManifestV1,
        id_field="manifest_id",
        sha_field="manifest_sha256",
        prefix="blinding-manifest",
        values=values,
    )
    assert manifest.identity_map_private is True
    assert len(manifest.units) == 1
    assert len(manifest.units[0].contributions) == 2
    assert_blinding_covers_rankings(manifest, *rankings)

    with pytest.raises(ValueError, match="exactly cover"):
        assert_blinding_covers_rankings(manifest, rankings[0])

    duplicate_unit = unit.model_copy(
        update={"annotation_order": 2, "blinded_unit_id": "blind-opaque-0002"}
    )
    with pytest.raises(ValidationError, match="pooled into one"):
        _identified(
            BlindingManifestV1,
            id_field="manifest_id",
            sha_field="manifest_sha256",
            prefix="blinding-manifest",
            values={**values, "units": (unit, duplicate_unit)},
        )
