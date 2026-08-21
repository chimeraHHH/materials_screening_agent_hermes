from __future__ import annotations

import inspect
from typing import Any, TypeVar

import pytest
from pydantic import ValidationError

from material_agent.inspiration.models import (
    StrictModel,
    canonical_sha256,
    deterministic_id,
)
from material_agent.research.flatband_blinding import build_reviewer_release
from material_agent.research.flatband_contracts import (
    AdjudicationReasonCode,
    AdjudicationStatus,
    AnnotationRefV1,
    AssertedEvidenceRelation,
    Assessability,
    BenchmarkSplit,
    BenchmarkSplitManifestV1,
    BlindedEvaluationUnitV1,
    BlindingManifestV1,
    BridgeJudgmentV1,
    BridgeVerdict,
    Dimensionality,
    DisagreementField,
    EvidenceJudgmentV1,
    EvidenceReasonCode,
    EvidenceSpanRefV1,
    ExpertAdjudicationV1,
    ExpertEvidenceRelation,
    ExpertRole,
    FalsificationPlanV1,
    HypothesisPacketV1,
    MechanismFamily,
    RankingContributionV1,
    RawExpertAnnotationV1,
    SplitCaseRefV1,
    SplitManifestKind,
    TargetBandClass,
    TriStateJudgment,
)
from material_agent.research.flatband_experts import (
    CalibrationCompletionV1,
    CaseConflictAssessmentV1,
    CaseExpertAssignmentV1,
    ConflictReasonCode,
    ConflictStatus,
    ExpertProfileV1,
    ExpertStudyRegistryV1,
)
from material_agent.research.flatband_gold import (
    AdjudicationRefV1,
    DuplicatePartitionAdjudicationV2,
    DuplicatePartitionProvenance,
    DuplicatePartitionRefV1,
    DuplicatePartitionRefV2,
    ExpertDuplicateClusterV1,
    ExpertDuplicatePartitionV1,
    FinalDuplicatePartitionV2,
    FinalExpertJudgmentV1,
    FinalExpertJudgmentV2,
    FinalGoldReleaseV1,
    FinalGoldReleaseV2,
    GoldProvenance,
    PooledDuplicateClusterV2,
    RawDuplicatePartitionV2,
    RawExpertDuplicatePartitionV1,
    ReviewerArtifactRefV2,
    ReviewerDuplicateClusterV2,
    assert_final_gold_closure,
    assert_final_gold_closure_legacy_v1,
    assert_final_gold_closure_legacy_v2_upstream,
    assert_final_gold_closure_v2,
    build_final_gold_release_legacy_v2_upstream,
    build_final_gold_release_v2,
)

ModelT = TypeVar("ModelT", bound=StrictModel)
SHA_A = "a" * 64
SHA_B = "b" * 64
SHA_C = "c" * 64
GUIDE_SHA = SHA_A
CASE_ID = "case-000"
CASE_SHA = f"{1:064x}"


def _identified(
    model_type: type[ModelT],
    *,
    id_field: str,
    sha_field: str,
    prefix: str,
    values: dict[str, Any],
) -> ModelT:
    draft = model_type.model_construct(**values)
    semantic = draft.model_dump(
        mode="python", exclude={id_field, sha_field}, warnings=False
    )
    digest = canonical_sha256(semantic)
    return model_type.model_validate(
        {
            **values,
            sha_field: digest,
            id_field: deterministic_id(prefix, {sha_field: digest}),
        }
    )


def _readdress(
    value: ModelT,
    *,
    id_field: str,
    sha_field: str,
    prefix: str,
    **updates: object,
) -> ModelT:
    values = value.model_dump(mode="python", exclude={id_field, sha_field})
    values.update(updates)
    return _identified(
        type(value),
        id_field=id_field,
        sha_field=sha_field,
        prefix=prefix,
        values=values,
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
            "split_seed": 7,
            "cases": cases,
            "ood_holdout_families": (),
        },
    )


def _completion(profile: ExpertProfileV1) -> CalibrationCompletionV1:
    return _identified(
        CalibrationCompletionV1,
        id_field="completion_id",
        sha_field="completion_sha256",
        prefix="calibration-completion",
        values={
            "expert_id": profile.expert_id,
            "role": profile.role,
            "annotation_guide_sha256": GUIDE_SHA,
            "calibration_set_sha256": SHA_B,
            "raw_answers_sha256": canonical_sha256({"raw": profile.expert_id}),
            "calibration_result_sha256": canonical_sha256({"result": profile.expert_id}),
            "completed_at": "2026-08-09T10:00:00+08:00",
        },
    )


def _registry(split: BenchmarkSplitManifestV1) -> ExpertStudyRegistryV1:
    profiles = tuple(
        sorted(
            (
                ExpertProfileV1(
                    expert_id="adjudicator-a",
                    role=ExpertRole.ADJUDICATOR,
                    domain_expertise=("flat-band physics",),
                    qualification_summary="Independent flat-band adjudicator.",
                ),
                ExpertProfileV1(
                    expert_id="reviewer-a",
                    role=ExpertRole.REVIEWER,
                    domain_expertise=("band structures",),
                    qualification_summary="Electronic-structure reviewer.",
                ),
                ExpertProfileV1(
                    expert_id="reviewer-b",
                    role=ExpertRole.REVIEWER,
                    domain_expertise=("materials physics",),
                    qualification_summary="Flat-band materials reviewer.",
                ),
            ),
            key=lambda item: item.expert_id,
        )
    )
    conflicts = tuple(
        CaseConflictAssessmentV1(
            case_id=case.case_id,
            case_sha256=case.case_sha256,
            expert_id=profile.expert_id,
            status=ConflictStatus.CLEAR,
            reason_code=ConflictReasonCode.NO_CONFLICT,
            disclosure_sha256=canonical_sha256(
                {"case": case.case_id, "expert": profile.expert_id}
            ),
            assessed_at="2026-08-09T09:00:00+08:00",
        )
        for case in split.cases
        for profile in profiles
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
    return _identified(
        ExpertStudyRegistryV1,
        id_field="registry_id",
        sha_field="registry_sha256",
        prefix="expert-study-registry",
        values={
            "split_manifest_id": split.manifest_id,
            "split_manifest_sha256": split.manifest_sha256,
            "annotation_guide_sha256": GUIDE_SHA,
            "calibration_set_sha256": SHA_B,
            "profiles": profiles,
            "calibration_completions": tuple(_completion(item) for item in profiles),
            "conflict_assessments": conflicts,
            "assignments": assignments,
            "registered_at": "2026-08-09T11:00:00+08:00",
        },
    )


def _packet(index: int) -> HypothesisPacketV1:
    link_id = f"evidence-link-{index}"
    values = {
        "case_id": CASE_ID,
        "case_sha256": CASE_SHA,
        "candidate_structure_sha256": f"{100 + index:064x}",
        "strict_structure_group_id": f"system-structure-group-{index}",
        "strict_hypothesis_group_id": f"system-proposed-group-{index}",
        "transformation_operator_id": "SUBSTITUTE_EQUIVALENT_SITE_V1",
        "transformation_summary": "Bounded substitution preserving parent topology.",
        "source_domain": "photonic lattices",
        "mechanism_family": MechanismFamily.LATTICE_INTERFERENCE,
        "source_mechanism": "Compact localization by destructive interference.",
        "shared_invariant": "Connectivity-constrained destructive interference.",
        "target_mapping": f"Map source sites onto target orbitals for candidate {index}.",
        "transferable_control": "Tune the symmetry-compatible hopping hierarchy.",
        "transfer_principle": "Preserve connectivity while changing orbital participation.",
        "required_conditions": ("dominant local hopping",),
        "breaking_conditions": ("large symmetry-breaking hopping",),
        "evidence_links": (
            EvidenceSpanRefV1(
                evidence_link_id=link_id,
                source_id="crossref",
                source_record_id=f"doi-record-{index}",
                source_url=f"https://doi.org/10.1000/example{index}",
                span_id=f"span-{index}",
                span_sha256=f"{200 + index:064x}",
                asserted_relation=AssertedEvidenceRelation.SUPPORT,
                claim_summary="The bounded excerpt supports the source mechanism.",
                private_text_artifact_uri=f"artifact://private/span-{index}.txt",
            ),
        ),
        "contradictions": (),
        "falsification": FalsificationPlanV1(
            observable="Tracked-band width",
            method="Calculate the frozen candidate band structure.",
            pass_condition="The tracked manifold meets the frozen bandwidth target.",
            fail_condition="The tracked manifold exceeds the target.",
        ),
    }
    return _identified(
        HypothesisPacketV1,
        id_field="packet_id",
        sha_field="packet_sha256",
        prefix="hypothesis-packet",
        values=values,
    )


def _bridge() -> BridgeJudgmentV1:
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


def _evidence_judgment(index: int) -> tuple[EvidenceJudgmentV1, ...]:
    return (
        EvidenceJudgmentV1(
            evidence_link_id=f"evidence-link-{index}",
            expert_relation=ExpertEvidenceRelation.VALID_SUPPORT,
            scope_match=True,
            overclaim=False,
            reason_code=EvidenceReasonCode.DIRECT_SCOPE_MATCH,
        ),
    )


def _raw(packet: HypothesisPacketV1, index: int, reviewer: str, grade: int) -> RawExpertAnnotationV1:
    return _identified(
        RawExpertAnnotationV1,
        id_field="annotation_id",
        sha_field="annotation_sha256",
        prefix="expert-annotation",
        values={
            "blinded_unit_id": f"blind-{index:03d}",
            "case_id": CASE_ID,
            "case_sha256": CASE_SHA,
            "packet_id": packet.packet_id,
            "packet_sha256": packet.packet_sha256,
            "reviewer_id": reviewer,
            "review_round": 1,
            "annotation_guide_sha256": GUIDE_SHA,
            "assessability": Assessability.ASSESSABLE,
            "relevance_grade": grade,
            "evidence_valid": True,
            "evidence_judgments": _evidence_judgment(index),
            "bridge_judgment": _bridge(),
            "mechanism_family": MechanismFamily.LATTICE_INTERFERENCE,
            "hard_fail_reasons": (),
            "confidence": 4,
            "rationale": "The packet supplies a bounded mechanism, support and falsifier.",
            "started_at": "2026-08-09T12:00:00+08:00",
            "submitted_at": "2026-08-09T12:10:00+08:00",
        },
    )


def _refs(*annotations: RawExpertAnnotationV1) -> tuple[AnnotationRefV1, ...]:
    return tuple(
        AnnotationRefV1(
            annotation_id=item.annotation_id,
            annotation_sha256=item.annotation_sha256,
            reviewer_id=item.reviewer_id,
        )
        for item in annotations
    )


def _adjudication(
    packet: HypothesisPacketV1,
    first: RawExpertAnnotationV1,
    second: RawExpertAnnotationV1,
    *,
    status: AdjudicationStatus = AdjudicationStatus.RESOLVED,
    adjudicator_id: str = "adjudicator-a",
) -> ExpertAdjudicationV1:
    unresolved = status is AdjudicationStatus.UNRESOLVABLE
    return _identified(
        ExpertAdjudicationV1,
        id_field="adjudication_id",
        sha_field="adjudication_sha256",
        prefix="expert-adjudication",
        values={
            "blinded_unit_id": first.blinded_unit_id,
            "case_id": CASE_ID,
            "case_sha256": CASE_SHA,
            "packet_id": packet.packet_id,
            "packet_sha256": packet.packet_sha256,
            "raw_annotations": _refs(first, second),
            "adjudicator_id": adjudicator_id,
            "annotation_guide_sha256": GUIDE_SHA,
            "disagreement_fields": (DisagreementField.RELEVANCE_GRADE,),
            "status": status,
            "final_assessability": None if unresolved else Assessability.ASSESSABLE,
            "final_relevance_grade": None if unresolved else 3,
            "final_evidence_valid": None if unresolved else True,
            "final_evidence_judgments": () if unresolved else _evidence_judgment(1),
            "final_bridge_judgment": None if unresolved else _bridge(),
            "final_mechanism_family": (
                None if unresolved else MechanismFamily.LATTICE_INTERFERENCE
            ),
            "final_hard_fail_reasons": (),
            "resolution_reason_code": (
                AdjudicationReasonCode.UNRESOLVABLE_EVIDENCE
                if unresolved
                else AdjudicationReasonCode.PHYSICS_RATIONALE
            ),
            "rationale": "The distinct adjudicator applied the frozen guide.",
            "adjudicated_at": "2026-08-09T12:30:00+08:00",
        },
    )


def _judgment(
    *,
    manifest: BlindingManifestV1,
    registry: ExpertStudyRegistryV1,
    packet: HypothesisPacketV1,
    index: int,
    raw: tuple[RawExpertAnnotationV1, RawExpertAnnotationV1],
    adjudication: ExpertAdjudicationV1 | None,
) -> FinalExpertJudgmentV1:
    source = raw[0] if adjudication is None else adjudication
    prefix = "" if adjudication is None else "final_"

    def value(name: str) -> object:
        return getattr(source, f"{prefix}{name}")

    unresolved = adjudication is not None and adjudication.status is AdjudicationStatus.UNRESOLVABLE
    grade = value("relevance_grade")
    evidence_valid = value("evidence_valid")
    values = {
        "reviewer_manifest_id": manifest.manifest_id,
        "reviewer_manifest_sha256": manifest.manifest_sha256,
        "expert_registry_id": registry.registry_id,
        "expert_registry_sha256": registry.registry_sha256,
        "blinded_unit_id": f"blind-{index:03d}",
        "case_id": CASE_ID,
        "case_sha256": CASE_SHA,
        "packet_id": packet.packet_id,
        "packet_sha256": packet.packet_sha256,
        "review_round": 1,
        "annotation_guide_sha256": GUIDE_SHA,
        "provenance": (
            GoldProvenance.AGREED_RAW
            if adjudication is None
            else GoldProvenance.ADJUDICATED
        ),
        "raw_annotations": _refs(*raw),
        "adjudication": (
            None
            if adjudication is None
            else AdjudicationRefV1(
                adjudication_id=adjudication.adjudication_id,
                adjudication_sha256=adjudication.adjudication_sha256,
                adjudicator_id=adjudication.adjudicator_id,
            )
        ),
        "adjudication_status": None if adjudication is None else adjudication.status,
        "final_assessability": value("assessability"),
        "final_relevance_grade": grade,
        "final_evidence_valid": evidence_valid,
        "final_evidence_judgments": value("evidence_judgments"),
        "final_bridge_judgment": value("bridge_judgment"),
        "final_mechanism_family": value("mechanism_family"),
        "final_hard_fail_reasons": value("hard_fail_reasons"),
        "denominator_included": True,
        "metric_relevance_gain": 0 if unresolved else (grade or 0),
        "metric_evidence_gain": 0 if unresolved else int(evidence_valid is True),
        "unresolvable": unresolved,
    }
    return _identified(
        FinalExpertJudgmentV1,
        id_field="judgment_id",
        sha_field="judgment_sha256",
        prefix="final-expert-judgment",
        values=values,
    )


def _fixture() -> dict[str, object]:
    split = _split()
    registry = _registry(split)
    packets = (_packet(1), _packet(2))
    raw = (
        _raw(packets[0], 1, "reviewer-a", 3),
        _raw(packets[0], 1, "reviewer-b", 2),
        _raw(packets[1], 2, "reviewer-a", 3),
        _raw(packets[1], 2, "reviewer-b", 3),
    )
    units = tuple(
        BlindedEvaluationUnitV1(
            annotation_order=index,
            blinded_unit_id=f"blind-{index:03d}",
            case_id=CASE_ID,
            case_sha256=CASE_SHA,
            packet_id=packet.packet_id,
            packet_sha256=packet.packet_sha256,
            strict_hypothesis_group_id=f"system-proposed-group-{index}",
            contributions=(
                RankingContributionV1(
                    system_id="B0",
                    run_id="run-b0",
                    ranking_id="ranking-b0",
                    ranking_sha256=SHA_C,
                    selection_rank=index,
                ),
            ),
        )
        for index, packet in enumerate(packets, start=1)
    )
    manifest = _identified(
        BlindingManifestV1,
        id_field="manifest_id",
        sha_field="manifest_sha256",
        prefix="blinding-manifest",
        values={
            "split_manifest_id": split.manifest_id,
            "split_manifest_sha256": split.manifest_sha256,
            "expert_registry_id": registry.registry_id,
            "expert_registry_sha256": registry.registry_sha256,
            "randomization_seed": 17,
            "reviewer_ids": ("reviewer-a", "reviewer-b"),
            "adjudicator_id": "adjudicator-a",
            "units": units,
            "sealed_at": "2026-08-09T11:30:00+08:00",
        },
    )
    adjudication = _adjudication(packets[0], raw[0], raw[1])
    judgments = (
        _judgment(
            manifest=manifest,
            registry=registry,
            packet=packets[0],
            index=1,
            raw=(raw[0], raw[1]),
            adjudication=adjudication,
        ),
        _judgment(
            manifest=manifest,
            registry=registry,
            packet=packets[1],
            index=2,
            raw=(raw[2], raw[3]),
            adjudication=None,
        ),
    )
    members = ("blind-001", "blind-002")
    cluster = ExpertDuplicateClusterV1(
        cluster_id=deterministic_id(
            "expert-duplicate", {"case_id": CASE_ID, "blinded_unit_ids": members}
        ),
        blinded_unit_ids=members,
    )
    raw_partition_a = _identified(
        RawExpertDuplicatePartitionV1,
        id_field="partition_id",
        sha_field="partition_sha256",
        prefix="raw-expert-duplicate",
        values={
            "reviewer_manifest_id": manifest.manifest_id,
            "reviewer_manifest_sha256": manifest.manifest_sha256,
            "expert_registry_id": registry.registry_id,
            "expert_registry_sha256": registry.registry_sha256,
            "case_id": CASE_ID,
            "case_sha256": CASE_SHA,
            "review_round": 1,
            "annotation_guide_sha256": GUIDE_SHA,
            "reviewer_id": "reviewer-a",
            "clusters": (cluster,),
            "submitted_at": "2026-08-09T12:20:00+08:00",
        },
    )
    singleton_clusters = tuple(
        ExpertDuplicateClusterV1(
            cluster_id=deterministic_id(
                "expert-duplicate",
                {"case_id": CASE_ID, "blinded_unit_ids": (unit_id,)},
            ),
            blinded_unit_ids=(unit_id,),
        )
        for unit_id in members
    )
    raw_partition_b = _identified(
        RawExpertDuplicatePartitionV1,
        id_field="partition_id",
        sha_field="partition_sha256",
        prefix="raw-expert-duplicate",
        values={
            "reviewer_manifest_id": manifest.manifest_id,
            "reviewer_manifest_sha256": manifest.manifest_sha256,
            "expert_registry_id": registry.registry_id,
            "expert_registry_sha256": registry.registry_sha256,
            "case_id": CASE_ID,
            "case_sha256": CASE_SHA,
            "review_round": 1,
            "annotation_guide_sha256": GUIDE_SHA,
            "reviewer_id": "reviewer-b",
            "clusters": singleton_clusters,
            "submitted_at": "2026-08-09T12:20:00+08:00",
        },
    )
    partition = _identified(
        ExpertDuplicatePartitionV1,
        id_field="partition_id",
        sha_field="partition_sha256",
        prefix="expert-duplicate-partition",
        values={
            "reviewer_manifest_id": manifest.manifest_id,
            "reviewer_manifest_sha256": manifest.manifest_sha256,
            "expert_registry_id": registry.registry_id,
            "expert_registry_sha256": registry.registry_sha256,
            "case_id": CASE_ID,
            "case_sha256": CASE_SHA,
            "review_round": 1,
            "annotation_guide_sha256": GUIDE_SHA,
            "reviewer_ids": ("reviewer-a", "reviewer-b"),
            "reviewer_partitions": (
                DuplicatePartitionRefV1(
                    partition_id=raw_partition_a.partition_id,
                    partition_sha256=raw_partition_a.partition_sha256,
                    reviewer_id=raw_partition_a.reviewer_id,
                ),
                DuplicatePartitionRefV1(
                    partition_id=raw_partition_b.partition_id,
                    partition_sha256=raw_partition_b.partition_sha256,
                    reviewer_id=raw_partition_b.reviewer_id,
                ),
            ),
            "provenance": DuplicatePartitionProvenance.ADJUDICATED,
            "adjudicator_id": "adjudicator-a",
            "resolution_reason_code": "PHYSICS_ROUTE_EQUIVALENCE",
            "rationale": "The adjudicator resolved whether the physical routes duplicate.",
            "clusters": (cluster,),
            "finalized_at": "2026-08-09T12:40:00+08:00",
        },
    )
    release = _identified(
        FinalGoldReleaseV1,
        id_field="release_id",
        sha_field="release_sha256",
        prefix="final-gold-release",
        values={
            "reviewer_manifest_id": manifest.manifest_id,
            "reviewer_manifest_sha256": manifest.manifest_sha256,
            "expert_registry_id": registry.registry_id,
            "expert_registry_sha256": registry.registry_sha256,
            "review_round": 1,
            "annotation_guide_sha256": GUIDE_SHA,
            "judgments": judgments,
            "duplicate_partitions": (partition,),
            "unresolvable_unit_ids": (),
            "denominator_unit_count": 2,
            "unresolvable_unit_count": 0,
            "released_at": "2026-08-09T13:00:00+08:00",
        },
    )
    return {
        "split": split,
        "registry": registry,
        "packets": packets,
        "raw": raw,
        "manifest": manifest,
        "adjudications": (adjudication,),
        "partition": partition,
        "raw_duplicate_partitions": (raw_partition_a, raw_partition_b),
        "release": release,
    }


def _verify(fixture: dict[str, object], release: FinalGoldReleaseV1 | None = None, **updates: object) -> None:
    assert_final_gold_closure(
        release or fixture["release"],  # type: ignore[arg-type]
        reviewer_manifest=fixture["manifest"],  # type: ignore[arg-type]
        expert_registry=fixture["registry"],  # type: ignore[arg-type]
        packets=fixture["packets"],  # type: ignore[arg-type]
        raw_annotations=updates.get("raw_annotations", fixture["raw"]),  # type: ignore[arg-type]
        adjudications=updates.get("adjudications", fixture["adjudications"]),  # type: ignore[arg-type]
        raw_duplicate_partitions=updates.get(
            "raw_duplicate_partitions", fixture["raw_duplicate_partitions"]
        ),  # type: ignore[arg-type]
    )


def test_final_gold_closes_agreed_and_adjudicated_units_and_expert_partition() -> None:
    fixture = _fixture()
    _verify(fixture)
    release = fixture["release"]
    assert isinstance(release, FinalGoldReleaseV1)
    assert {item.provenance for item in release.judgments} == {
        GoldProvenance.AGREED_RAW,
        GoldProvenance.ADJUDICATED,
    }
    assert release.duplicate_partitions[0].clusters[0].blinded_unit_ids == (
        "blind-001",
        "blind-002",
    )


def test_missing_masked_unit_is_not_complete_case_deleted() -> None:
    fixture = _fixture()
    release = fixture["release"]
    assert isinstance(release, FinalGoldReleaseV1)
    partial = _readdress(
        release,
        id_field="release_id",
        sha_field="release_sha256",
        prefix="final-gold-release",
        judgments=(release.judgments[0],),
        denominator_unit_count=1,
    )
    with pytest.raises(ValueError, match="exactly cover reviewer-manifest units"):
        _verify(fixture, partial)


def test_foreign_raw_reference_is_rejected_even_when_release_is_rehashed() -> None:
    fixture = _fixture()
    release = fixture["release"]
    assert isinstance(release, FinalGoldReleaseV1)
    first = release.judgments[0]
    forged_refs = (
        first.raw_annotations[0].model_copy(update={"annotation_sha256": SHA_C}),
        first.raw_annotations[1],
    )
    forged = _readdress(
        first,
        id_field="judgment_id",
        sha_field="judgment_sha256",
        prefix="final-expert-judgment",
        raw_annotations=forged_refs,
    )
    altered = _readdress(
        release,
        id_field="release_id",
        sha_field="release_sha256",
        prefix="final-gold-release",
        judgments=(forged, release.judgments[1]),
    )
    with pytest.raises(ValueError, match="foreign raw annotation ref"):
        _verify(fixture, altered)


def test_fake_adjudicator_cannot_resolve_a_disputed_unit() -> None:
    fixture = _fixture()
    release = fixture["release"]
    packet = fixture["packets"][0]
    raw = fixture["raw"]
    assert isinstance(release, FinalGoldReleaseV1)
    fake = _adjudication(packet, raw[0], raw[1], adjudicator_id="fake-adjudicator")  # type: ignore[arg-type]
    first = release.judgments[0]
    forged = _readdress(
        first,
        id_field="judgment_id",
        sha_field="judgment_sha256",
        prefix="final-expert-judgment",
        adjudication=AdjudicationRefV1(
            adjudication_id=fake.adjudication_id,
            adjudication_sha256=fake.adjudication_sha256,
            adjudicator_id=fake.adjudicator_id,
        ),
    )
    altered = _readdress(
        release,
        id_field="release_id",
        sha_field="release_sha256",
        prefix="final-gold-release",
        judgments=(forged, release.judgments[1]),
    )
    with pytest.raises(ValueError, match="registered adjudicator"):
        _verify(fixture, altered, adjudications=(fake,))


def test_guide_drift_is_rejected_at_cross_object_boundary() -> None:
    fixture = _fixture()
    release = fixture["release"]
    assert isinstance(release, FinalGoldReleaseV1)
    drifted = _readdress(
        release,
        id_field="release_id",
        sha_field="release_sha256",
        prefix="final-gold-release",
        annotation_guide_sha256=SHA_C,
    )
    with pytest.raises(ValueError, match="guide drifts"):
        _verify(fixture, drifted)


def test_partial_case_partition_is_rejected() -> None:
    fixture = _fixture()
    release = fixture["release"]
    partition = fixture["partition"]
    assert isinstance(release, FinalGoldReleaseV1)
    assert isinstance(partition, ExpertDuplicatePartitionV1)
    members = ("blind-001",)
    partial_cluster = ExpertDuplicateClusterV1(
        cluster_id=deterministic_id(
            "expert-duplicate", {"case_id": CASE_ID, "blinded_unit_ids": members}
        ),
        blinded_unit_ids=members,
    )
    partial = _readdress(
        partition,
        id_field="partition_id",
        sha_field="partition_sha256",
        prefix="expert-duplicate-partition",
        clusters=(partial_cluster,),
    )
    altered = _readdress(
        release,
        id_field="release_id",
        sha_field="release_sha256",
        prefix="final-gold-release",
        duplicate_partitions=(partial,),
    )
    with pytest.raises(ValueError, match="exactly cover case units"):
        _verify(fixture, altered)


def test_final_partition_must_replay_both_true_raw_partitions() -> None:
    fixture = _fixture()
    raw_partitions = fixture["raw_duplicate_partitions"]
    with pytest.raises(ValueError, match="foreign raw ref"):
        _verify(fixture, raw_duplicate_partitions=(raw_partitions[0],))  # type: ignore[index]


def test_system_proposed_group_cannot_be_used_as_expert_cluster_id() -> None:
    fixture = _fixture()
    partition = fixture["partition"]
    assert isinstance(partition, ExpertDuplicatePartitionV1)
    leaked = ExpertDuplicateClusterV1(
        cluster_id="system-proposed-group-1",
        blinded_unit_ids=("blind-001", "blind-002"),
    )
    with pytest.raises(ValidationError, match="derive only from expert membership"):
        _readdress(
            partition,
            id_field="partition_id",
            sha_field="partition_sha256",
            prefix="expert-duplicate-partition",
            clusters=(leaked,),
        )


def test_unresolvable_is_retained_with_zero_gains_and_separate_count() -> None:
    fixture = _fixture()
    release = fixture["release"]
    packet = fixture["packets"][0]
    raw = fixture["raw"]
    manifest = fixture["manifest"]
    registry = fixture["registry"]
    assert isinstance(release, FinalGoldReleaseV1)
    unresolved_adjudication = _adjudication(
        packet, raw[0], raw[1], status=AdjudicationStatus.UNRESOLVABLE  # type: ignore[arg-type]
    )
    unresolved = _judgment(
        manifest=manifest,  # type: ignore[arg-type]
        registry=registry,  # type: ignore[arg-type]
        packet=packet,  # type: ignore[arg-type]
        index=1,
        raw=(raw[0], raw[1]),  # type: ignore[arg-type]
        adjudication=unresolved_adjudication,
    )
    altered = _readdress(
        release,
        id_field="release_id",
        sha_field="release_sha256",
        prefix="final-gold-release",
        judgments=(unresolved, release.judgments[1]),
        unresolvable_unit_ids=("blind-001",),
        unresolvable_unit_count=1,
    )
    _verify(fixture, altered, adjudications=(unresolved_adjudication,))
    assert unresolved.metric_relevance_gain == 0
    assert unresolved.metric_evidence_gain == 0
    assert unresolved.denominator_included is True


def _pooled_cluster_v2(case_id: str, *unit_ids: str) -> PooledDuplicateClusterV2:
    members = tuple(sorted(unit_ids))
    return PooledDuplicateClusterV2(
        cluster_id=deterministic_id(
            "pooled-duplicate-v2",
            {"case_id": case_id, "pooled_unit_ids": members},
        ),
        pooled_unit_ids=members,
    )


def _formal_v2_gold_fixture(
    *,
    all_systems_failed_case: bool = False,
    partition_disagreement: bool = False,
    authoritative_v3: bool = False,
) -> dict[str, object]:
    from tests.unit.test_flatband_research_blinding import _formal_v2_release_fixture

    upstream = _formal_v2_release_fixture(
        all_systems_failed_case=all_systems_failed_case,
        two_packets_one_case=partition_disagreement,
        authoritative_v3=authoritative_v3,
    )
    execution = upstream["execution"]
    registry = upstream["registry"]
    manifests = upstream["manifests"]
    identity_maps = upstream["private_maps"]
    manifest_by_id = {item.manifest_id: item for item in manifests}
    map_by_reviewer = {item.expert_id: item for item in identity_maps}
    assignment_by_case = {item.case_id: item for item in registry.assignments}
    packet_by_id = {item.packet_id: item for item in execution.hypothesis_packets}
    entry_by_pool_reviewer = {
        (entry.pooled_unit_id, identity_map.expert_id): entry
        for identity_map in identity_maps
        for entry in identity_map.entries
    }
    pool_meta = {
        entry.pooled_unit_id: entry
        for entry in identity_maps[0].entries
    }
    first_pool = min(pool_meta)
    raw_annotations: list[RawExpertAnnotationV1] = []
    adjudications: list[ExpertAdjudicationV1] = []
    for pool_id in sorted(pool_meta):
        meta = pool_meta[pool_id]
        packet = packet_by_id[meta.packet_id]
        assignment = assignment_by_case[meta.case_id]
        evidence = tuple(
            EvidenceJudgmentV1(
                evidence_link_id=link.evidence_link_id,
                expert_relation=ExpertEvidenceRelation.VALID_SUPPORT,
                scope_match=True,
                overclaim=False,
                reason_code=EvidenceReasonCode.DIRECT_SCOPE_MATCH,
            )
            for link in packet.evidence_links
        )
        pool_raws: list[RawExpertAnnotationV1] = []
        for reviewer_index, reviewer_id in enumerate(assignment.reviewer_ids):
            identity_map = map_by_reviewer[reviewer_id]
            entry = entry_by_pool_reviewer[(pool_id, reviewer_id)]
            grade = 2 if pool_id == first_pool and reviewer_index == 1 else 3
            pool_raws.append(
                _identified(
                    RawExpertAnnotationV1,
                    id_field="annotation_id",
                    sha_field="annotation_sha256",
                    prefix="expert-annotation",
                    values={
                        "blinded_unit_id": entry.blinded_unit_id,
                        "case_id": meta.case_id,
                        "case_sha256": meta.case_sha256,
                        "packet_id": meta.packet_id,
                        "packet_sha256": meta.packet_sha256,
                        "reviewer_id": reviewer_id,
                        "review_round": 1,
                        "annotation_guide_sha256": registry.annotation_guide_sha256,
                        "assessability": Assessability.ASSESSABLE,
                        "relevance_grade": grade,
                        "evidence_valid": True,
                        "evidence_judgments": evidence,
                        "bridge_judgment": _bridge(),
                        "mechanism_family": packet.mechanism_family,
                        "hard_fail_reasons": (),
                        "confidence": 4,
                        "rationale": "The bounded packet supports the frozen judgment.",
                        "started_at": "2026-08-09T21:01:00+08:00",
                        "submitted_at": "2026-08-09T21:02:00+08:00",
                    },
                )
            )
        pool_raws_tuple = tuple(pool_raws)
        raw_annotations.extend(pool_raws_tuple)
        adjudication = None
        if pool_id == first_pool:
            adjudication = _identified(
                ExpertAdjudicationV1,
                id_field="adjudication_id",
                sha_field="adjudication_sha256",
                prefix="expert-adjudication",
                values={
                    "blinded_unit_id": pool_id,
                    "case_id": meta.case_id,
                    "case_sha256": meta.case_sha256,
                    "packet_id": meta.packet_id,
                    "packet_sha256": meta.packet_sha256,
                    "raw_annotations": _refs(*pool_raws_tuple),
                    "adjudicator_id": assignment.adjudicator_id,
                    "annotation_guide_sha256": registry.annotation_guide_sha256,
                    "disagreement_fields": (DisagreementField.RELEVANCE_GRADE,),
                    "status": AdjudicationStatus.RESOLVED,
                    "final_assessability": Assessability.ASSESSABLE,
                    "final_relevance_grade": 3,
                    "final_evidence_valid": True,
                    "final_evidence_judgments": evidence,
                    "final_bridge_judgment": _bridge(),
                    "final_mechanism_family": packet.mechanism_family,
                    "final_hard_fail_reasons": (),
                    "resolution_reason_code": AdjudicationReasonCode.PHYSICS_RATIONALE,
                    "rationale": "The independent adjudicator replayed both labels.",
                    "adjudicated_at": "2026-08-09T21:02:30+08:00",
                },
            )
            adjudications.append(adjudication)

    raw_partitions: list[RawDuplicatePartitionV2] = []
    pools_by_case: dict[str, list[str]] = {}
    for pool_id, meta in pool_meta.items():
        pools_by_case.setdefault(meta.case_id, []).append(pool_id)
    for values in pools_by_case.values():
        values.sort()
    partition_adjudications: list[DuplicatePartitionAdjudicationV2] = []
    disagreement_case_id = upstream["two_packet_case_id"]
    for case_id in sorted(pools_by_case):
        pool_ids = tuple(pools_by_case[case_id])
        meta = pool_meta[pool_ids[0]]
        assignment = assignment_by_case[case_id]
        case_raw_partitions: list[RawDuplicatePartitionV2] = []
        for reviewer_index, reviewer_id in enumerate(assignment.reviewer_ids):
            identity_map = map_by_reviewer[reviewer_id]
            manifest = manifest_by_id[identity_map.reviewer_manifest_id]
            reviewer_blinds = tuple(
                entry_by_pool_reviewer[
                    (pool_id, reviewer_id)
                ].blinded_unit_id
                for pool_id in pool_ids
            )
            cluster_memberships = (
                (tuple(sorted(reviewer_blinds)),)
                if (
                    partition_disagreement
                    and case_id == disagreement_case_id
                    and reviewer_index == 1
                )
                else tuple((item,) for item in reviewer_blinds)
            )
            clusters = tuple(
                sorted(
                    (
                        ReviewerDuplicateClusterV2(
                            cluster_id=deterministic_id(
                                "reviewer-duplicate-v2",
                                {
                                    "case_id": case_id,
                                    "reviewer_id": reviewer_id,
                                    "blinded_unit_ids": members,
                                },
                            ),
                            blinded_unit_ids=members,
                        )
                        for members in cluster_memberships
                    ),
                    key=lambda item: (
                        item.blinded_unit_ids,
                        item.cluster_id,
                    ),
                )
            )
            raw_partition = _identified(
                RawDuplicatePartitionV2,
                id_field="partition_id",
                sha_field="partition_sha256",
                prefix="raw-duplicate-partition-v2",
                values={
                    "reviewer_manifest_id": manifest.manifest_id,
                    "reviewer_manifest_sha256": manifest.manifest_sha256,
                    "private_identity_map_id": identity_map.identity_map_id,
                    "private_identity_map_sha256": identity_map.identity_map_sha256,
                    "expert_registry_id": registry.registry_id,
                    "expert_registry_sha256": registry.registry_sha256,
                    "case_id": case_id,
                    "case_sha256": meta.case_sha256,
                    "review_round": 1,
                    "annotation_guide_sha256": registry.annotation_guide_sha256,
                    "reviewer_id": reviewer_id,
                    "clusters": clusters,
                    "submitted_at": "2026-08-09T21:03:00+08:00",
                },
            )
            raw_partitions.append(raw_partition)
            case_raw_partitions.append(raw_partition)
        if partition_disagreement and case_id == disagreement_case_id:
            partition_refs = tuple(
                DuplicatePartitionRefV2(
                    partition_id=item.partition_id,
                    partition_sha256=item.partition_sha256,
                    reviewer_id=item.reviewer_id,
                )
                for item in case_raw_partitions
            )
            partition_adjudications.append(
                _identified(
                    DuplicatePartitionAdjudicationV2,
                    id_field="adjudication_id",
                    sha_field="adjudication_sha256",
                    prefix="duplicate-adjudication-v2",
                    values={
                        "execution_release_id": execution.release_id,
                        "execution_release_sha256": execution.release_sha256,
                        "expert_registry_id": registry.registry_id,
                        "expert_registry_sha256": registry.registry_sha256,
                        "case_id": case_id,
                        "case_sha256": meta.case_sha256,
                        "review_round": 1,
                        "annotation_guide_sha256": (
                            registry.annotation_guide_sha256
                        ),
                        "pooled_unit_ids": pool_ids,
                        "reviewer_partitions": partition_refs,
                        "adjudicator_id": assignment.adjudicator_id,
                        "resolution_reason_code": (
                            "MECHANISM_IDENTITY_REVIEW"
                        ),
                        "rationale": (
                            "The adjudicator replayed both sealed partitions."
                        ),
                        "final_clusters": tuple(
                            _pooled_cluster_v2(case_id, pool_id)
                            for pool_id in pool_ids
                        ),
                        "adjudicated_at": (
                            "2026-08-09T21:03:30+08:00"
                        ),
                    },
                )
            )
    builder = (
        build_final_gold_release_v2
        if authoritative_v3
        else build_final_gold_release_legacy_v2_upstream
    )
    builder_inputs: dict[str, object] = {
        "execution_release": execution,
        "pre_run_eligibility_release": upstream["eligibility"],
        "reviewer_manifests": manifests,
        "private_identity_maps": identity_maps,
        "expert_registry": registry,
        "evidence_excerpts": upstream["excerpts"],
        "blind_key": upstream["blind_key"],
        "renderer_sha256": upstream["renderer_sha256"],
        "raw_annotations": tuple(raw_annotations),
        "adjudications": tuple(adjudications),
        "raw_duplicate_partitions": tuple(raw_partitions),
        "duplicate_partition_adjudications": tuple(partition_adjudications),
        "released_at": "2026-08-09T21:05:00+08:00",
    }
    if authoritative_v3:
        builder_inputs["frozen_case_release"] = upstream["frozen"]
    release = builder(
        **builder_inputs
    )
    return {
        **upstream,
        "gold_release": release,
        "raw_annotations": tuple(raw_annotations),
        "adjudications": tuple(adjudications),
        "raw_duplicate_partitions": tuple(raw_partitions),
        "duplicate_partition_adjudications": tuple(partition_adjudications),
    }


@pytest.fixture(scope="module")
def formal_v2_gold() -> dict[str, object]:
    return _formal_v2_gold_fixture()


def _verify_formal_v2_gold(fixture: dict[str, object], release: FinalGoldReleaseV2 | None = None, **updates: object) -> None:
    authoritative_v3 = (
        fixture["execution"].schema_version == "flatband-execution-release-v3"
    )
    verifier = (
        assert_final_gold_closure_v2
        if authoritative_v3
        else assert_final_gold_closure_legacy_v2_upstream
    )
    verifier_inputs: dict[str, object] = {
        "execution_release": fixture["execution"],
        "pre_run_eligibility_release": fixture["eligibility"],
        "reviewer_manifests": fixture["manifests"],
        "private_identity_maps": fixture["private_maps"],
        "expert_registry": fixture["registry"],
        "evidence_excerpts": fixture["excerpts"],
        "blind_key": fixture["blind_key"],
        "renderer_sha256": fixture["renderer_sha256"],
        "raw_annotations": updates.get(
            "raw_annotations", fixture["raw_annotations"]
        ),
        "adjudications": updates.get(
            "adjudications", fixture["adjudications"]
        ),
        "raw_duplicate_partitions": updates.get(
            "raw_duplicate_partitions", fixture["raw_duplicate_partitions"]
        ),
        "duplicate_partition_adjudications": updates.get(
            "duplicate_partition_adjudications",
            fixture["duplicate_partition_adjudications"],
        ),
    }
    if authoritative_v3:
        verifier_inputs["frozen_case_release"] = fixture["frozen"]
    verifier(
        fixture["gold_release"] if release is None else release,
        **verifier_inputs,
    )


def _rebuild_formal_v2_gold(
    fixture: dict[str, object], **updates: object
) -> FinalGoldReleaseV2:
    authoritative_v3 = (
        fixture["execution"].schema_version == "flatband-execution-release-v3"
    )
    builder = (
        build_final_gold_release_v2
        if authoritative_v3
        else build_final_gold_release_legacy_v2_upstream
    )
    inputs: dict[str, object] = {
        "execution_release": fixture["execution"],
        "pre_run_eligibility_release": fixture["eligibility"],
        "reviewer_manifests": fixture["manifests"],
        "private_identity_maps": fixture["private_maps"],
        "expert_registry": fixture["registry"],
        "evidence_excerpts": fixture["excerpts"],
        "blind_key": fixture["blind_key"],
        "renderer_sha256": fixture["renderer_sha256"],
        "raw_annotations": updates.get(
            "raw_annotations", fixture["raw_annotations"]
        ),
        "adjudications": updates.get(
            "adjudications", fixture["adjudications"]
        ),
        "raw_duplicate_partitions": updates.get(
            "raw_duplicate_partitions", fixture["raw_duplicate_partitions"]
        ),
        "duplicate_partition_adjudications": updates.get(
            "duplicate_partition_adjudications",
            fixture["duplicate_partition_adjudications"],
        ),
        "released_at": updates.get(
            "released_at", fixture["gold_release"].released_at
        ),
    }
    if authoritative_v3:
        inputs["frozen_case_release"] = fixture["frozen"]
    return builder(**inputs)


def test_formal_v2_gold_closes_30_units_raw_adjudication_and_partitions(
    formal_v2_gold: dict[str, object],
) -> None:
    release = formal_v2_gold["gold_release"]
    assert len(release.judgments) == 30
    assert len(formal_v2_gold["raw_annotations"]) == 60
    assert len(formal_v2_gold["adjudications"]) == 1
    assert len(release.duplicate_partitions) == 30
    assert len(formal_v2_gold["raw_duplicate_partitions"]) == 60
    _verify_formal_v2_gold(formal_v2_gold)
    assert _rebuild_formal_v2_gold(formal_v2_gold) == release


def test_formal_gold_v2_artifact_exact_replays_v3_authoritative_chain() -> None:
    fixture = _formal_v2_gold_fixture(authoritative_v3=True)
    release = fixture["gold_release"]
    assert fixture["execution"].schema_version == "flatband-execution-release-v3"
    assert len(release.judgments) == 30
    assert len(release.duplicate_partitions) == 30
    _verify_formal_v2_gold(fixture)


def test_canonical_gold_builder_has_no_caller_final_label_or_count_surface() -> None:
    forbidden = {
        "judgments",
        "final_labels",
        "duplicate_partitions",
        "final_clusters",
        "cluster_ids",
        "denominator_unit_count",
        "unresolvable_unit_count",
    }
    assert not (
        forbidden
        & set(inspect.signature(build_final_gold_release_v2).parameters)
    )


def test_formal_gold_entry_rejects_legacy_v2_upstream(
    formal_v2_gold: dict[str, object],
) -> None:
    with pytest.raises(ValidationError) as error:
        build_final_gold_release_v2(
            frozen_case_release=formal_v2_gold["frozen"],
            execution_release=formal_v2_gold["execution"],
            pre_run_eligibility_release=formal_v2_gold["eligibility"],
            reviewer_manifests=formal_v2_gold["manifests"],
            private_identity_maps=formal_v2_gold["private_maps"],
            expert_registry=formal_v2_gold["registry"],
            evidence_excerpts=formal_v2_gold["excerpts"],
            blind_key=formal_v2_gold["blind_key"],
            renderer_sha256=formal_v2_gold["renderer_sha256"],
            raw_annotations=formal_v2_gold["raw_annotations"],
            adjudications=formal_v2_gold["adjudications"],
            raw_duplicate_partitions=formal_v2_gold[
                "raw_duplicate_partitions"
            ],
            duplicate_partition_adjudications=formal_v2_gold[
                "duplicate_partition_adjudications"
            ],
            released_at=formal_v2_gold["gold_release"].released_at,
        )
    assert any(
        item["type"] == "model_type"
        and item.get("ctx", {}).get("class_name") == "ExecutionReleaseV3"
        for item in error.value.errors()
    )


def test_canonical_gold_builder_rejects_missing_required_adjudication(
    formal_v2_gold: dict[str, object],
) -> None:
    with pytest.raises(ValueError, match="requires adjudication"):
        _rebuild_formal_v2_gold(formal_v2_gold, adjudications=())


def test_formal_gold_exact_replay_rejects_caller_chosen_finalization_time(
    formal_v2_gold: dict[str, object],
) -> None:
    release = formal_v2_gold["gold_release"]
    partition = release.duplicate_partitions[0]
    forged_partition = _readdress(
        partition,
        id_field="partition_id",
        sha_field="partition_sha256",
        prefix="final-duplicate-partition-v2",
        finalized_at=release.released_at,
    )
    forged_release = _readdress(
        release,
        id_field="release_id",
        sha_field="release_sha256",
        prefix="final-gold-release-v2",
        duplicate_partitions=(
            forged_partition,
            *release.duplicate_partitions[1:],
        ),
    )
    with pytest.raises(ValueError, match="exact canonical replay"):
        _verify_formal_v2_gold(formal_v2_gold, forged_release)


def test_canonical_gold_builder_rejects_unnecessary_label_adjudication(
    formal_v2_gold: dict[str, object],
) -> None:
    agreed = next(
        item
        for item in formal_v2_gold["gold_release"].judgments
        if item.provenance is GoldProvenance.AGREED_RAW
    )
    assignment = next(
        item
        for item in formal_v2_gold["registry"].assignments
        if item.case_id == agreed.case_id
    )
    unnecessary = _readdress(
        formal_v2_gold["adjudications"][0],
        id_field="adjudication_id",
        sha_field="adjudication_sha256",
        prefix="expert-adjudication",
        blinded_unit_id=agreed.pooled_unit_id,
        case_id=agreed.case_id,
        case_sha256=agreed.case_sha256,
        packet_id=agreed.packet_id,
        packet_sha256=agreed.packet_sha256,
        raw_annotations=agreed.raw_annotations,
        adjudicator_id=assignment.adjudicator_id,
    )
    with pytest.raises(ValueError, match="agreed raw labels cannot consume"):
        _rebuild_formal_v2_gold(
            formal_v2_gold,
            adjudications=(
                *formal_v2_gold["adjudications"],
                unnecessary,
            ),
        )


def test_canonical_gold_omits_all_systems_failed_case_from_present_pools() -> None:
    fixture = _formal_v2_gold_fixture(all_systems_failed_case=True)
    release = fixture["gold_release"]
    zero_case_id = fixture["all_systems_failed_case_id"]
    assert len(release.judgments) == 29
    assert len(release.duplicate_partitions) == 29
    assert zero_case_id not in {item.case_id for item in release.judgments}
    assert zero_case_id not in {
        item.case_id for item in release.duplicate_partitions
    }
    assert release.denominator_unit_count == 29
    assert _rebuild_formal_v2_gold(fixture) == release


def test_canonical_gold_derives_partition_adjudication_only_for_disagreement() -> None:
    fixture = _formal_v2_gold_fixture(partition_disagreement=True)
    release = fixture["gold_release"]
    assert len(release.judgments) == 31
    assert len(release.duplicate_partitions) == 30
    assert sum(
        item.provenance is DuplicatePartitionProvenance.ADJUDICATED
        for item in release.duplicate_partitions
    ) == 1
    assert len(fixture["duplicate_partition_adjudications"]) == 1
    assert _rebuild_formal_v2_gold(fixture) == release
    with pytest.raises(
        ValueError, match="partition disagreement requires adjudication"
    ):
        _rebuild_formal_v2_gold(
            fixture, duplicate_partition_adjudications=()
        )


def test_formal_v2_gold_rejects_missing_unit_and_orphan_raw(
    formal_v2_gold: dict[str, object],
) -> None:
    release = formal_v2_gold["gold_release"]
    missing = _readdress(
        release,
        id_field="release_id",
        sha_field="release_sha256",
        prefix="final-gold-release-v2",
        judgments=release.judgments[:-1],
        denominator_unit_count=release.denominator_unit_count - 1,
    )
    with pytest.raises(ValueError, match="exactly cover unique present pooled units"):
        _verify_formal_v2_gold(formal_v2_gold, missing)
    raw = formal_v2_gold["raw_annotations"][0]
    orphan = _readdress(
        raw,
        id_field="annotation_id",
        sha_field="annotation_sha256",
        prefix="expert-annotation",
        rationale="An additional sealed annotation that no Gold judgment references.",
    )
    with pytest.raises(ValueError, match="orphaned by Gold"):
        _verify_formal_v2_gold(
            formal_v2_gold,
            raw_annotations=(*formal_v2_gold["raw_annotations"], orphan),
        )


def test_formal_v2_gold_rejects_foreign_raw_and_missing_partition(
    formal_v2_gold: dict[str, object],
) -> None:
    raw = formal_v2_gold["raw_annotations"][0]
    foreign = _readdress(
        raw,
        id_field="annotation_id",
        sha_field="annotation_sha256",
        prefix="expert-annotation",
        packet_id="hypothesis-packet-foreign",
    )
    with pytest.raises(ValueError, match="foreign raw annotation ref"):
        _verify_formal_v2_gold(
            formal_v2_gold,
            raw_annotations=(foreign, *formal_v2_gold["raw_annotations"][1:]),
        )
    with pytest.raises(ValueError, match="foreign raw ref|orphaned by Gold"):
        _verify_formal_v2_gold(
            formal_v2_gold,
            raw_duplicate_partitions=formal_v2_gold["raw_duplicate_partitions"][:-1],
        )


def test_formal_v2_gold_rejects_private_alias_duplicate_reviewer_and_v1() -> None:
    fixture = _formal_v2_gold_fixture()
    first_map, second_map = fixture["private_maps"]
    aliased_entries = list(first_map.entries)
    aliased_entries[1] = aliased_entries[1].model_copy(
        update={"pooled_unit_id": aliased_entries[0].pooled_unit_id}
    )
    aliased = _readdress(
        first_map,
        id_field="identity_map_id",
        sha_field="identity_map_sha256",
        prefix="private-identity-map-v2",
        entries=tuple(aliased_entries),
    )
    fixture["private_maps"] = (aliased, second_map)
    with pytest.raises(ValueError, match="exact deterministic replay"):
        _verify_formal_v2_gold(fixture)
    fixture = _formal_v2_gold_fixture()
    fixture["private_maps"] = (
        fixture["private_maps"][0],
        fixture["private_maps"][0],
    )
    with pytest.raises(ValueError, match="repeats a private reviewer"):
        _verify_formal_v2_gold(fixture)
    fixture = _formal_v2_gold_fixture()
    fixture["manifests"] = (
        {"schema_version": "flatband-reviewer-manifest-v1"},
    )
    with pytest.raises(ValidationError, match="flatband-reviewer-manifest-v2"):
        _verify_formal_v2_gold(fixture)


def test_v2_30_case_execution_rejects_one_unit_gold_subset() -> None:
    """A valid 30-case execution cannot be silently reduced at Gold closure."""

    import sys
    import types

    from tests.unit import test_flatband_research_cases as cases_fixture
    from tests.unit import test_flatband_research_execution as execution_fixture
    from tests.unit import test_flatband_research_experts as experts_fixture

    tests_package = sys.modules.setdefault("tests", types.ModuleType("tests"))
    unit_package = sys.modules.setdefault("tests.unit", types.ModuleType("tests.unit"))
    tests_package.unit = unit_package
    sys.modules.setdefault(
        "tests.unit.test_flatband_research_execution", execution_fixture
    )
    sys.modules.setdefault("tests.unit.test_flatband_research_experts", experts_fixture)
    sys.modules.setdefault("tests.unit.test_flatband_research_cases", cases_fixture)
    from tests.unit.test_flatband_research_blinding import _release_fixture

    execution, eligibility, registry, excerpts = _release_fixture()
    manifests, identity_maps = build_reviewer_release(
        execution_release=execution,
        pre_run_eligibility_release=eligibility,
        expert_registry=registry,
        evidence_excerpts=excerpts,
        blind_key=b"pilot-reviewer-blind-key-v1-32bytes!",
        renderer_sha256="d" * 64,
        sealed_at="2026-08-09T13:00:00+08:00",
    )
    assert len(execution.execution_matrix.split_manifest.cases) == 30
    first_map = identity_maps[0]
    first_entry = first_map.entries[0]
    assignment = next(
        item for item in registry.assignments if item.case_id == first_entry.case_id
    )
    artifact_refs: list[ReviewerArtifactRefV2] = []
    raw_refs: list[AnnotationRefV1] = []
    for reviewer_id in assignment.reviewer_ids:
        identity_map = next(item for item in identity_maps if item.expert_id == reviewer_id)
        manifest = next(
            item for item in manifests if item.manifest_id == identity_map.reviewer_manifest_id
        )
        entry = next(
            item for item in identity_map.entries if item.pooled_unit_id == first_entry.pooled_unit_id
        )
        public_packet = next(
            item for item in manifest.packets if item.reviewer_packet_id == entry.reviewer_packet_id
        )
        artifact_refs.append(
            ReviewerArtifactRefV2(
                reviewer_id=reviewer_id,
                reviewer_manifest_id=manifest.manifest_id,
                reviewer_manifest_sha256=manifest.manifest_sha256,
                private_identity_map_id=identity_map.identity_map_id,
                private_identity_map_sha256=identity_map.identity_map_sha256,
                blinded_reviewer_id=identity_map.blinded_reviewer_id,
                blinded_unit_id=entry.blinded_unit_id,
                reviewer_packet_id=public_packet.reviewer_packet_id,
                reviewer_packet_sha256=public_packet.reviewer_packet_sha256,
            )
        )
        raw_refs.append(
            AnnotationRefV1(
                annotation_id=f"raw-placeholder-{reviewer_id}",
                annotation_sha256=canonical_sha256((reviewer_id, entry.blinded_unit_id)),
                reviewer_id=reviewer_id,
            )
        )
    packet = next(
        item for item in execution.hypothesis_packets if item.packet_id == first_entry.packet_id
    )
    evidence = tuple(
        EvidenceJudgmentV1(
            evidence_link_id=item.evidence_link_id,
            expert_relation=ExpertEvidenceRelation.VALID_SUPPORT,
            scope_match=True,
            overclaim=False,
            reason_code=EvidenceReasonCode.DIRECT_SCOPE_MATCH,
        )
        for item in packet.evidence_links
    )
    judgment = _identified(
        FinalExpertJudgmentV2,
        id_field="judgment_id",
        sha_field="judgment_sha256",
        prefix="final-expert-judgment-v2",
        values={
            "execution_release_id": execution.release_id,
            "execution_release_sha256": execution.release_sha256,
            "expert_registry_id": registry.registry_id,
            "expert_registry_sha256": registry.registry_sha256,
            "pooled_unit_id": first_entry.pooled_unit_id,
            "case_id": first_entry.case_id,
            "case_sha256": first_entry.case_sha256,
            "packet_id": first_entry.packet_id,
            "packet_sha256": first_entry.packet_sha256,
            "review_round": 1,
            "annotation_guide_sha256": registry.annotation_guide_sha256,
            "reviewer_artifacts": tuple(artifact_refs),
            "provenance": GoldProvenance.AGREED_RAW,
            "raw_annotations": tuple(raw_refs),
            "final_assessability": Assessability.ASSESSABLE,
            "final_relevance_grade": 3,
            "final_evidence_valid": True,
            "final_evidence_judgments": evidence,
            "final_bridge_judgment": _bridge(),
            "final_mechanism_family": packet.mechanism_family,
            "final_hard_fail_reasons": (),
            "denominator_included": True,
            "metric_relevance_gain": 3,
            "metric_evidence_gain": 1,
            "unresolvable": False,
        },
    )
    partition_refs = tuple(
        DuplicatePartitionRefV2(
            partition_id=f"raw-partition-placeholder-{reviewer_id}",
            partition_sha256=canonical_sha256(("partition", reviewer_id)),
            reviewer_id=reviewer_id,
        )
        for reviewer_id in assignment.reviewer_ids
    )
    final_partition = _identified(
        FinalDuplicatePartitionV2,
        id_field="partition_id",
        sha_field="partition_sha256",
        prefix="final-duplicate-partition-v2",
        values={
            "execution_release_id": execution.release_id,
            "execution_release_sha256": execution.release_sha256,
            "expert_registry_id": registry.registry_id,
            "expert_registry_sha256": registry.registry_sha256,
            "case_id": first_entry.case_id,
            "case_sha256": first_entry.case_sha256,
            "review_round": 1,
            "annotation_guide_sha256": registry.annotation_guide_sha256,
            "reviewer_ids": assignment.reviewer_ids,
            "reviewer_partitions": partition_refs,
            "provenance": DuplicatePartitionProvenance.AGREED_REVIEWERS,
            "clusters": (_pooled_cluster_v2(first_entry.case_id, first_entry.pooled_unit_id),),
            "finalized_at": "2026-08-09T13:04:00+08:00",
        },
    )
    release = _identified(
        FinalGoldReleaseV2,
        id_field="release_id",
        sha_field="release_sha256",
        prefix="final-gold-release-v2",
        values={
            "execution_release_id": execution.release_id,
            "execution_release_sha256": execution.release_sha256,
            "expert_registry_id": registry.registry_id,
            "expert_registry_sha256": registry.registry_sha256,
            "review_round": 1,
            "annotation_guide_sha256": registry.annotation_guide_sha256,
            "judgments": (judgment,),
            "duplicate_partitions": (final_partition,),
            "unresolvable_unit_ids": (),
            "denominator_unit_count": 1,
            "unresolvable_unit_count": 0,
            "released_at": "2026-08-09T13:05:00+08:00",
        },
    )
    with pytest.raises(ValueError, match="exactly cover unique present pooled units"):
        assert_final_gold_closure_legacy_v1(
            release,
            execution_release=execution,
            pre_run_eligibility_release=eligibility,
            reviewer_manifests=manifests,
            private_identity_maps=identity_maps,
            expert_registry=registry,
            evidence_excerpts=excerpts,
            blind_key=b"pilot-reviewer-blind-key-v1-32bytes!",
            renderer_sha256="d" * 64,
            raw_annotations=(),
            adjudications=(),
            raw_duplicate_partitions=(),
        )


def test_duplicate_partition_adjudication_v2_is_independently_addressed() -> None:
    reviewer_refs = (
        DuplicatePartitionRefV2(
            partition_id="raw-partition-a",
            partition_sha256=SHA_A,
            reviewer_id="reviewer-a",
        ),
        DuplicatePartitionRefV2(
            partition_id="raw-partition-b",
            partition_sha256=SHA_B,
            reviewer_id="reviewer-b",
        ),
    )
    adjudication = _identified(
        DuplicatePartitionAdjudicationV2,
        id_field="adjudication_id",
        sha_field="adjudication_sha256",
        prefix="duplicate-adjudication-v2",
        values={
            "execution_release_id": "execution-release-a",
            "execution_release_sha256": SHA_A,
            "expert_registry_id": "registry-a",
            "expert_registry_sha256": SHA_B,
            "case_id": CASE_ID,
            "case_sha256": CASE_SHA,
            "review_round": 1,
            "annotation_guide_sha256": GUIDE_SHA,
            "pooled_unit_ids": ("pool-a", "pool-b"),
            "reviewer_partitions": reviewer_refs,
            "adjudicator_id": "adjudicator-a",
            "resolution_reason_code": "MECHANISM_IDENTITY_REVIEW",
            "rationale": "The assigned adjudicator replayed both sealed partitions.",
            "final_clusters": (
                _pooled_cluster_v2(CASE_ID, "pool-a", "pool-b"),
            ),
            "adjudicated_at": "2026-08-09T13:04:00+08:00",
        },
    )
    tampered = adjudication.model_copy(
        update={"final_clusters": (_pooled_cluster_v2(CASE_ID, "pool-a"),)}
    )
    with pytest.raises(ValidationError, match="pool universe"):
        DuplicatePartitionAdjudicationV2.model_validate(tampered.model_dump(mode="python"))
