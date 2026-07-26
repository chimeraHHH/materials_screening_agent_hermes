from __future__ import annotations

from material_agent.ml_screening.evidence import (
    build_structure_lineage,
    decision_for_ml_result,
    evidence_for_ml_result,
    recommended_downstream_structure_id,
)
from material_agent.ml_screening.models import (
    ApplicabilityStatus,
    ArtifactPointer,
    EvidenceLevel,
    MLDecision,
)


def test_only_real_applicable_qc_pass_can_reach_l2() -> None:
    assert evidence_for_ml_result(
        is_mock=False,
        applicability=ApplicabilityStatus.APPLICABLE,
        qc_passed=True,
    ) is EvidenceLevel.L2_ML_SCREENED
    for is_mock, applicability, qc_passed in [
        (True, ApplicabilityStatus.APPLICABLE, True),
        (False, ApplicabilityStatus.UNKNOWN, True),
        (False, ApplicabilityStatus.NOT_APPLICABLE, True),
        (False, ApplicabilityStatus.APPLICABLE, False),
    ]:
        assert evidence_for_ml_result(
            is_mock=is_mock,
            applicability=applicability,
            qc_passed=qc_passed,
        ) is EvidenceLevel.L1_RETRIEVED


def test_ml_decision_is_conservative() -> None:
    assert decision_for_ml_result(
        is_mock=False,
        applicability=ApplicabilityStatus.APPLICABLE,
        qc_passed=True,
    ) is MLDecision.PASS
    assert decision_for_ml_result(
        is_mock=True,
        applicability=ApplicabilityStatus.APPLICABLE,
        qc_passed=True,
    ) is MLDecision.UNCERTAIN
    assert decision_for_ml_result(
        is_mock=False,
        applicability=ApplicabilityStatus.APPLICABLE,
        qc_passed=True,
        runtime_failed=True,
    ) is MLDecision.FAILED


def test_lineage_preserves_parent_and_model_provenance(
    ml_candidate_factory,
    ml_model,
    ml_policy,
) -> None:
    candidate = ml_candidate_factory()
    output = ArtifactPointer(
        uri="artifact://candidates/structures/relaxed.ml.json",
        sha256="a" * 64,
    )
    lineage = build_structure_lineage(
        candidate=candidate,
        output_structure_id="struct_ml_relaxed",
        output_structure=output,
        model=ml_model,
        policy=ml_policy,
        is_mock=True,
    )
    assert lineage.parent_structure_id == (
        candidate.source_structure.structure_id
    )
    assert lineage.structure_id == "struct_ml_relaxed"
    assert lineage.model_id == ml_model.model_id
    assert lineage.checkpoint_sha256 == ml_model.checkpoint_sha256
    assert lineage.is_mock is True


def test_downstream_uses_ml_structure_only_for_real_l2_pass(
    ml_candidate_factory,
) -> None:
    candidate = ml_candidate_factory()
    assert recommended_downstream_structure_id(
        candidate=candidate,
        decision=MLDecision.PASS,
        evidence_level=EvidenceLevel.L2_ML_SCREENED,
        output_structure_id="struct_ml",
    ) == "struct_ml"
    assert recommended_downstream_structure_id(
        candidate=candidate,
        decision=MLDecision.UNCERTAIN,
        evidence_level=EvidenceLevel.L1_RETRIEVED,
        output_structure_id="struct_mock",
    ) == candidate.source_structure.structure_id
