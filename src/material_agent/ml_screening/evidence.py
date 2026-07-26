"""Pure evidence and structure-lineage decisions."""

from __future__ import annotations

from material_agent.ml_screening.models import (
    ApplicabilityStatus,
    ArtifactPointer,
    EvidenceLevel,
    MLCandidateInput,
    MLDecision,
    MLModelSpec,
    MLScreeningPolicy,
    StructureLineage,
)


def evidence_for_ml_result(
    *,
    is_mock: bool,
    applicability: ApplicabilityStatus,
    qc_passed: bool,
) -> EvidenceLevel:
    if (
        not is_mock
        and applicability is ApplicabilityStatus.APPLICABLE
        and qc_passed
    ):
        return EvidenceLevel.L2_ML_SCREENED
    return EvidenceLevel.L1_RETRIEVED


def decision_for_ml_result(
    *,
    is_mock: bool,
    applicability: ApplicabilityStatus,
    qc_passed: bool,
    runtime_failed: bool = False,
) -> MLDecision:
    if runtime_failed:
        return MLDecision.FAILED
    if (
        not is_mock
        and applicability is ApplicabilityStatus.APPLICABLE
        and qc_passed
    ):
        return MLDecision.PASS
    return MLDecision.UNCERTAIN


def build_structure_lineage(
    *,
    candidate: MLCandidateInput,
    output_structure_id: str,
    output_structure: ArtifactPointer,
    model: MLModelSpec,
    policy: MLScreeningPolicy,
    is_mock: bool,
) -> StructureLineage:
    return StructureLineage(
        structure_id=output_structure_id,
        structure_uri=output_structure.uri,
        structure_sha256=output_structure.sha256,
        parent_structure_id=candidate.source_structure.structure_id,
        model_id=model.model_id,
        checkpoint_sha256=model.checkpoint_sha256,
        policy_version=policy.policy_version,
        is_mock=is_mock,
    )


def recommended_downstream_structure_id(
    *,
    candidate: MLCandidateInput,
    decision: MLDecision,
    evidence_level: EvidenceLevel,
    output_structure_id: str | None,
) -> str:
    if (
        decision is MLDecision.PASS
        and evidence_level is EvidenceLevel.L2_ML_SCREENED
        and output_structure_id is not None
    ):
        return output_structure_id
    return candidate.source_structure.structure_id
