"""Production facade for the private flat-band research workflow.

The lower-level research modules intentionally expose small, independently
replayable artifacts.  This module is the single orchestration surface for one
Pilot round.  It keeps the raw/private roots together, derives reviewer, Gold,
agreement and Gate artifacts through their canonical builders, and finishes by
calling the authoritative V3 top-level verifier.

The bundle is private custody.  In particular, the blind key is never embedded;
only its SHA-256 commitment is stored.  A verifier must be given the key again.
No hidden model reasoning or chain-of-thought is represented here.
"""

from __future__ import annotations

import hashlib
from datetime import datetime
from typing import Annotated, Literal, TypeVar

from pydantic import Field, model_validator

from material_agent.inspiration.models import (
    Identifier,
    Sha256,
    StrictModel,
    canonical_sha256,
    deterministic_id,
)
from material_agent.research.flatband_analysis import (
    FormalPilotAgreementGateReleaseV1,
    FormalPilotAgreementReleaseV1,
    build_formal_pilot_agreement_gate,
    build_formal_pilot_agreement_release,
)
from material_agent.research.flatband_blinding import (
    EvidenceExcerptV2,
    PrivateIdentityMapV2,
    ReviewerManifestV2,
    build_reviewer_release_v2,
)
from material_agent.research.flatband_cases import (
    FrozenCaseReleaseV3,
    PilotPreBudgetClosureReleaseV3,
    PreRunEligibilityReleaseV3,
)
from material_agent.research.flatband_contracts import (
    ExpertAdjudicationV1,
    RawExpertAnnotationV1,
)
from material_agent.research.flatband_execution import ExecutionReleaseV3
from material_agent.research.flatband_experts import (
    CalibrationSetManifestV2,
    ExpertStudyRegistryV2,
    PrivateExpertIdentityCustodianAttestationV2,
    PublicExpertIdentityReleaseV2,
)
from material_agent.research.flatband_gold import (
    DuplicatePartitionAdjudicationV2,
    FinalGoldReleaseV2,
    RawDuplicatePartitionV2,
    build_final_gold_release_v2,
)
from material_agent.research.flatband_ingress import (
    SourceCatalogCheckpointReleaseV1,
)
from material_agent.research.flatband_leakage import (
    LeakageRoundClosureContextV3,
    MechanismLineageAssignmentCurationReleaseV3,
    MechanismLineageCurationReleaseV3,
)
from material_agent.research.flatband_pilot import assert_formal_pilot_closure_v3

ModelT = TypeVar("ModelT", bound=StrictModel)


def _timestamp(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:  # pragma: no cover - pydantic reports the field
        raise ValueError("timestamp must be RFC3339-compatible") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("timestamp must include a UTC offset")
    return parsed


def _revalidate(value: ModelT, model_type: type[ModelT]) -> ModelT:
    return model_type.model_validate(value.model_dump(mode="python", round_trip=True))


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


def _blind_key_commitment(blind_key: bytes) -> str:
    if not isinstance(blind_key, bytes) or len(blind_key) < 32:
        raise ValueError("Pilot blind key must contain at least 32 bytes")
    return hashlib.sha256(blind_key).hexdigest()


class PilotRoundArtifactsV3(StrictModel):
    """One complete, private, content-addressed Pilot-round custody bundle."""

    schema_version: Literal["flatband-pilot-round-artifacts-v3"] = (
        "flatband-pilot-round-artifacts-v3"
    )
    bundle_id: Identifier
    bundle_sha256: Sha256
    review_round: Literal[1, 2]
    source_catalog_checkpoint: SourceCatalogCheckpointReleaseV1
    private_identity_attestation: PrivateExpertIdentityCustodianAttestationV2
    public_identity_release: PublicExpertIdentityReleaseV2
    calibration_manifest: CalibrationSetManifestV2
    expert_registry: ExpertStudyRegistryV2
    lineage_curation_release: MechanismLineageCurationReleaseV3
    lineage_assignment_curation_release: (
        MechanismLineageAssignmentCurationReleaseV3
    )
    frozen_case_release: FrozenCaseReleaseV3
    pre_run_eligibility_release: PreRunEligibilityReleaseV3
    pre_budget_closure_release: PilotPreBudgetClosureReleaseV3
    execution_release: ExecutionReleaseV3
    reviewer_manifests: Annotated[
        tuple[ReviewerManifestV2, ...], Field(min_length=1, max_length=16)
    ]
    private_identity_maps: Annotated[
        tuple[PrivateIdentityMapV2, ...], Field(min_length=1, max_length=16)
    ]
    evidence_excerpts: tuple[EvidenceExcerptV2, ...]
    raw_annotations: tuple[RawExpertAnnotationV1, ...]
    adjudications: tuple[ExpertAdjudicationV1, ...]
    raw_duplicate_partitions: tuple[RawDuplicatePartitionV2, ...]
    duplicate_partition_adjudications: tuple[
        DuplicatePartitionAdjudicationV2, ...
    ]
    final_gold_release: FinalGoldReleaseV2
    agreement_release: FormalPilotAgreementReleaseV1
    leakage_context: LeakageRoundClosureContextV3
    agreement_gate: FormalPilotAgreementGateReleaseV1
    blind_key_commitment_sha256: Sha256
    renderer_sha256: Sha256
    assembled_at: Annotated[str, Field(min_length=20, max_length=40)]
    private_custody_required: Literal[True] = True
    public_repository_release_allowed: Literal[False] = False
    blind_key_embedded: Literal[False] = False
    hidden_reasoning_persisted: Literal[False] = False
    scientific_conclusion: Literal[False] = False

    @model_validator(mode="after")
    def validate_bundle(self) -> PilotRoundArtifactsV3:
        _timestamp(self.assembled_at)
        source_checkpoint = _revalidate(
            self.source_catalog_checkpoint, SourceCatalogCheckpointReleaseV1
        )
        execution = _revalidate(self.execution_release, ExecutionReleaseV3)
        if (
            source_checkpoint.source_catalog_sha256
            != execution.frozen_case_release.split_manifest.source_catalog_sha256
        ):
            raise ValueError("Pilot bundle uses a foreign source-audit checkpoint")
        if (
            self.frozen_case_release,
            self.pre_run_eligibility_release,
            self.pre_budget_closure_release,
        ) != (
            execution.frozen_case_release,
            execution.pre_run_eligibility_release,
            execution.pre_budget_closure_release,
        ):
            raise ValueError("Pilot bundle crosswires its V3 execution roots")
        if self.review_round != self.agreement_release.review_round:
            raise ValueError("Pilot bundle round differs from agreement release")
        if self.review_round != self.agreement_gate.review_round:
            raise ValueError("Pilot bundle round differs from agreement Gate")
        if self.review_round != self.final_gold_release.review_round:
            raise ValueError("Pilot bundle round differs from Gold release")
        manifest_ids = tuple(item.manifest_id for item in self.reviewer_manifests)
        map_manifest_ids = tuple(
            item.reviewer_manifest_id for item in self.private_identity_maps
        )
        if tuple(sorted(manifest_ids)) != tuple(sorted(map_manifest_ids)):
            raise ValueError("Pilot bundle reviewer manifests and private maps differ")
        semantic = self.model_dump(
            mode="python", exclude={"bundle_id", "bundle_sha256"}
        )
        expected_sha = canonical_sha256(semantic)
        if self.bundle_sha256 != expected_sha:
            raise ValueError("Pilot bundle SHA-256 does not match semantic content")
        if self.bundle_id != deterministic_id(
            "pilot-round-artifacts-v3", {"bundle_sha256": expected_sha}
        ):
            raise ValueError("Pilot bundle ID does not match bundle SHA-256")
        return self


def _pilot_closure_arguments(
    bundle: PilotRoundArtifactsV3,
) -> dict[str, object]:
    return {
        "private_identity_attestation": bundle.private_identity_attestation,
        "public_identity_release": bundle.public_identity_release,
        "calibration_manifest": bundle.calibration_manifest,
        "expert_registry": bundle.expert_registry,
        "lineage_curation_release": bundle.lineage_curation_release,
        "lineage_assignment_curation_release": (
            bundle.lineage_assignment_curation_release
        ),
        "frozen_case_release": bundle.frozen_case_release,
        "pre_run_eligibility_release": bundle.pre_run_eligibility_release,
        "pre_budget_closure_release": bundle.pre_budget_closure_release,
        "execution_release": bundle.execution_release,
        "reviewer_manifests": bundle.reviewer_manifests,
        "private_identity_maps": bundle.private_identity_maps,
        "evidence_excerpts": bundle.evidence_excerpts,
        "renderer_sha256": bundle.renderer_sha256,
        "raw_annotations": bundle.raw_annotations,
        "adjudications": bundle.adjudications,
        "raw_duplicate_partitions": bundle.raw_duplicate_partitions,
        "duplicate_partition_adjudications": (
            bundle.duplicate_partition_adjudications
        ),
        "final_gold_release": bundle.final_gold_release,
        "agreement_release": bundle.agreement_release,
        "leakage_context": bundle.leakage_context,
        "agreement_gate": bundle.agreement_gate,
    }


def assert_formal_pilot_round_artifacts_v3(
    bundle: PilotRoundArtifactsV3,
    *,
    blind_key: bytes,
    prior_r1_bundle: PilotRoundArtifactsV3 | None = None,
    prior_r1_blind_key: bytes | None = None,
) -> None:
    """Replay the complete bundle; a matching key commitment is not enough."""

    value = _revalidate(bundle, PilotRoundArtifactsV3)
    if value.blind_key_commitment_sha256 != _blind_key_commitment(blind_key):
        raise ValueError("Pilot blind key differs from the private bundle commitment")

    prior_arguments: dict[str, object] = {}
    if value.review_round == 1:
        if prior_r1_bundle is not None or prior_r1_blind_key is not None:
            raise ValueError("Pilot R1 bundle cannot carry a prior R1 bundle")
    else:
        if prior_r1_bundle is None or prior_r1_blind_key is None:
            raise ValueError("Pilot R2 bundle requires a verified prior R1 bundle")
        prior = _revalidate(prior_r1_bundle, PilotRoundArtifactsV3)
        if prior.review_round != 1:
            raise ValueError("Pilot R2 prior bundle is not R1")
        assert_formal_pilot_round_artifacts_v3(
            prior,
            blind_key=prior_r1_blind_key,
        )
        prior_arguments = {
            "prior_r1_agreement_release": prior.agreement_release,
            "prior_r1_gate": prior.agreement_gate,
            "prior_r1_leakage_context": prior.leakage_context,
        }

    assert_formal_pilot_closure_v3(
        **_pilot_closure_arguments(value),
        blind_key=blind_key,
        **prior_arguments,
    )


def assemble_formal_pilot_round_v3(
    *,
    source_catalog_checkpoint: SourceCatalogCheckpointReleaseV1,
    private_identity_attestation: PrivateExpertIdentityCustodianAttestationV2,
    public_identity_release: PublicExpertIdentityReleaseV2,
    calibration_manifest: CalibrationSetManifestV2,
    expert_registry: ExpertStudyRegistryV2,
    lineage_curation_release: MechanismLineageCurationReleaseV3,
    lineage_assignment_curation_release: (
        MechanismLineageAssignmentCurationReleaseV3
    ),
    frozen_case_release: FrozenCaseReleaseV3,
    pre_run_eligibility_release: PreRunEligibilityReleaseV3,
    pre_budget_closure_release: PilotPreBudgetClosureReleaseV3,
    execution_release: ExecutionReleaseV3,
    evidence_excerpts: tuple[EvidenceExcerptV2, ...],
    blind_key: bytes,
    renderer_sha256: str,
    raw_annotations: tuple[RawExpertAnnotationV1, ...],
    adjudications: tuple[ExpertAdjudicationV1, ...],
    raw_duplicate_partitions: tuple[RawDuplicatePartitionV2, ...],
    duplicate_partition_adjudications: tuple[
        DuplicatePartitionAdjudicationV2, ...
    ],
    reviewer_sealed_at: str,
    gold_released_at: str,
    agreement_assembled_at: str,
    gate_evaluated_at: str,
    bundle_assembled_at: str,
    prior_r1_bundle: PilotRoundArtifactsV3 | None = None,
    prior_r1_blind_key: bytes | None = None,
) -> PilotRoundArtifactsV3:
    """Canonical production assembler for one R1 or R2 Pilot round."""

    frozen = _revalidate(frozen_case_release, FrozenCaseReleaseV3)
    source_checkpoint = _revalidate(
        source_catalog_checkpoint, SourceCatalogCheckpointReleaseV1
    )
    eligibility = _revalidate(
        pre_run_eligibility_release, PreRunEligibilityReleaseV3
    )
    pre_budget = _revalidate(
        pre_budget_closure_release, PilotPreBudgetClosureReleaseV3
    )
    execution = _revalidate(execution_release, ExecutionReleaseV3)
    registry = _revalidate(expert_registry, ExpertStudyRegistryV2)

    reviewer_manifests, private_identity_maps = build_reviewer_release_v2(
        frozen_case_release=frozen,
        execution_release=execution,
        pre_run_eligibility_release=eligibility,
        expert_registry=registry,
        evidence_excerpts=evidence_excerpts,
        blind_key=blind_key,
        renderer_sha256=renderer_sha256,
        sealed_at=reviewer_sealed_at,
    )
    gold = build_final_gold_release_v2(
        frozen_case_release=frozen,
        execution_release=execution,
        pre_run_eligibility_release=eligibility,
        reviewer_manifests=reviewer_manifests,
        private_identity_maps=private_identity_maps,
        expert_registry=registry,
        evidence_excerpts=evidence_excerpts,
        blind_key=blind_key,
        renderer_sha256=renderer_sha256,
        raw_annotations=raw_annotations,
        adjudications=adjudications,
        raw_duplicate_partitions=raw_duplicate_partitions,
        duplicate_partition_adjudications=duplicate_partition_adjudications,
        released_at=gold_released_at,
    )
    agreement = build_formal_pilot_agreement_release(
        split_manifest=frozen.split_manifest,
        leakage_release=frozen.leakage_release,
        lineage_curation_release=lineage_curation_release,
        frozen_case_release=frozen,
        pre_run_eligibility_release=eligibility,
        execution_release=execution,
        expert_registry=registry,
        reviewer_manifests=reviewer_manifests,
        private_identity_maps=private_identity_maps,
        evidence_excerpts=evidence_excerpts,
        blind_key=blind_key,
        renderer_sha256=renderer_sha256,
        raw_annotations=raw_annotations,
        assembled_at=agreement_assembled_at,
    )

    prior_agreement = None if prior_r1_bundle is None else prior_r1_bundle.agreement_release
    prior_gate = None if prior_r1_bundle is None else prior_r1_bundle.agreement_gate
    prior_context = None if prior_r1_bundle is None else prior_r1_bundle.leakage_context
    gate = build_formal_pilot_agreement_gate(
        agreement_release=agreement,
        leakage_context=pre_budget.current_leakage_context,
        lineage_curation_release=lineage_curation_release,
        evaluated_at=gate_evaluated_at,
        prior_r1_agreement_release=prior_agreement,
        prior_r1_gate=prior_gate,
        prior_r1_leakage_context=prior_context,
    )

    bundle = _build_addressed(
        PilotRoundArtifactsV3,
        id_field="bundle_id",
        sha_field="bundle_sha256",
        prefix="pilot-round-artifacts-v3",
        values={
            "review_round": agreement.review_round,
            "source_catalog_checkpoint": source_checkpoint,
            "private_identity_attestation": private_identity_attestation,
            "public_identity_release": public_identity_release,
            "calibration_manifest": calibration_manifest,
            "expert_registry": registry,
            "lineage_curation_release": lineage_curation_release,
            "lineage_assignment_curation_release": (
                lineage_assignment_curation_release
            ),
            "frozen_case_release": frozen,
            "pre_run_eligibility_release": eligibility,
            "pre_budget_closure_release": pre_budget,
            "execution_release": execution,
            "reviewer_manifests": reviewer_manifests,
            "private_identity_maps": private_identity_maps,
            "evidence_excerpts": evidence_excerpts,
            "raw_annotations": raw_annotations,
            "adjudications": adjudications,
            "raw_duplicate_partitions": raw_duplicate_partitions,
            "duplicate_partition_adjudications": (
                duplicate_partition_adjudications
            ),
            "final_gold_release": gold,
            "agreement_release": agreement,
            "leakage_context": pre_budget.current_leakage_context,
            "agreement_gate": gate,
            "blind_key_commitment_sha256": _blind_key_commitment(blind_key),
            "renderer_sha256": renderer_sha256,
            "assembled_at": bundle_assembled_at,
        },
    )
    assert_formal_pilot_round_artifacts_v3(
        bundle,
        blind_key=blind_key,
        prior_r1_bundle=prior_r1_bundle,
        prior_r1_blind_key=prior_r1_blind_key,
    )
    return bundle


__all__ = [
    "PilotRoundArtifactsV3",
    "assemble_formal_pilot_round_v3",
    "assert_formal_pilot_round_artifacts_v3",
]
