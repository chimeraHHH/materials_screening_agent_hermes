#!/usr/bin/env python3
"""Generate or verify the audience-split research schema bundles."""

from __future__ import annotations

import argparse
import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

from pydantic import BaseModel

from material_agent.research.flatband_analysis import (
    FormalPilotAgreementGateReleaseV1,
    FormalPilotAgreementReleaseV1,
)
from material_agent.research.flatband_analysis_v2 import AnalysisInputReleaseV2
from material_agent.research.flatband_arm_runtime import (
    ArmExecutionTraceV1,
    ModelNativeReasoningReceiptV1,
    ModelNativeReasoningResponseV1,
    ModelNativeReasoningWorkItemV1,
)
from material_agent.research.flatband_blinding import (
    EvidenceExcerptV2,
    PostLabelOriginGuessV1,
    PrivateIdentityMapV2,
    ReviewerManifestV2,
)
from material_agent.research.flatband_campaign import (
    FlatBandCampaignReleaseV1,
    LocalSensitivityNotRunReleaseV1,
    LockedExecutionPlanV1,
)
from material_agent.research.flatband_cases import (
    CandidateEligibilityAssignmentReleaseV3,
    CandidatePoolReleaseV3,
    FrozenCaseReleaseV3,
    PilotPreBudgetClosureReleaseV3,
    PreRunEligibilityReleaseV3,
)
from material_agent.research.flatband_contracts import (
    BenchmarkSplitManifestV2,
    ExpertAdjudicationV1,
    FlatBandBenchmarkCaseV1,
    HypothesisPacketV1,
    RawExpertAnnotationV1,
)
from material_agent.research.flatband_derivative_screening import (
    DerivativeScreeningReleaseV3,
)
from material_agent.research.flatband_execution import (
    BudgetManifestV2,
    ExecutionMatrixV2,
    ExecutionReleaseV3,
    ResearchRankingV1,
    SourceReceiptBundleV1,
    TerminalRunResultV1,
    Top5ProjectionV1,
)
from material_agent.research.flatband_experts import (
    CalibrationSetManifestV2,
    ExpertStudyRegistryV2,
    PrivateExpertIdentityCustodianAttestationV2,
    PublicExpertIdentityReleaseV2,
)
from material_agent.research.flatband_gold import (
    DuplicatePartitionAdjudicationV2,
    FinalDuplicatePartitionV2,
    FinalExpertJudgmentV2,
    FinalGoldReleaseV2,
    RawDuplicatePartitionV2,
)
from material_agent.research.flatband_ingress import (
    SourceCatalogCheckpointReleaseV1,
)
from material_agent.research.flatband_leakage import (
    LeakageComponentReleaseV3,
    LeakageRoundClosureContextV3,
    MechanismLineageAssignmentCurationReleaseV3,
    MechanismLineageAssignmentV3,
    MechanismLineageCurationReleaseV3,
    MechanismLineageRegistryV3,
)
from material_agent.research.flatband_lifecycle import (
    ClaimSupportReleaseV1,
    DevelopmentFusionGateReleaseV1,
    DevelopmentPromotionReleaseV1,
    FusionConfigurationReleaseV1,
    LockedAnnotationUnsealReleaseV1,
    LockedLabelSealV1,
    LockedTestAuthorizationReleaseV1,
    LockedUnsealLedgerReleaseV1,
    ProtocolDeviationReleaseV1,
    PublicBenchmarkProjectionV1,
    PublicBenchmarkResultReleaseV1,
    PublicReleaseAuthorizationV1,
    ReleaseAuthorityPolicyV1,
    ReleaseControlAttestationV1,
    ScientificReviewerAttestationV1,
    ScientificReviewerIdentityAttestationV1,
    ScientificReviewReleaseV1,
)
from material_agent.research.flatband_main import (
    MainCandidatePoolReleaseV1,
    MainCapacityPolicyV1,
    MainEligibilityReleaseV1,
    MainFrozenCaseReleaseV1,
    MainPhaseAuthorizationReleaseV1,
    MainPreBudgetClosureReleaseV1,
    MainSamplingPolicyReleaseV1,
    MainStructureUnionReleaseV1,
    MainStructureUnionVerifierAttestationV1,
)
from material_agent.research.flatband_main_execution import (
    MainPhaseExecutionReleaseV1,
)
from material_agent.research.flatband_main_gold import (
    MainDuplicateAdjudicationV1,
    MainExpertAssignmentV1,
    MainFinalDuplicatePartitionV1,
    MainGoldFormalVerifierAttestationV1,
    MainGoldReleaseV1,
    MainLabelAdjudicationV1,
    MainPrivateIdentityMapV1,
    MainRawDuplicatePartitionV1,
    MainRawLabelV1,
    MainReviewerManifestV1,
)
from material_agent.research.flatband_source_policy import (
    CaseSourcePolicyAttestationV2,
)
from material_agent.research.flatband_structure_grouping import (
    StructureGroupingPrivateEvidenceReleaseV2,
    StructureGroupingUnionReplayReleaseV2,
)
from material_agent.research.flatband_workflow import PilotRoundArtifactsV3

PUBLIC_PROTOCOL_ROOTS: tuple[type[BaseModel], ...] = (
    BenchmarkSplitManifestV2,
    LeakageComponentReleaseV3,
    MechanismLineageRegistryV3,
    MechanismLineageAssignmentV3,
    CaseSourcePolicyAttestationV2,
    PublicExpertIdentityReleaseV2,
    ExecutionMatrixV2,
    BudgetManifestV2,
    ResearchRankingV1,
    Top5ProjectionV1,
    FormalPilotAgreementGateReleaseV1,
    SourceCatalogCheckpointReleaseV1,
    MainCapacityPolicyV1,
    MainSamplingPolicyReleaseV1,
    PublicBenchmarkProjectionV1,
    PublicBenchmarkResultReleaseV1,
)

PRIVATE_CUSTODY_ROOTS: tuple[type[BaseModel], ...] = (
    FlatBandBenchmarkCaseV1,
    CalibrationSetManifestV2,
    PrivateExpertIdentityCustodianAttestationV2,
    ExpertStudyRegistryV2,
    MechanismLineageCurationReleaseV3,
    MechanismLineageAssignmentCurationReleaseV3,
    LeakageRoundClosureContextV3,
    CandidatePoolReleaseV3,
    CandidateEligibilityAssignmentReleaseV3,
    DerivativeScreeningReleaseV3,
    PreRunEligibilityReleaseV3,
    FrozenCaseReleaseV3,
    PilotPreBudgetClosureReleaseV3,
    HypothesisPacketV1,
    SourceReceiptBundleV1,
    StructureGroupingPrivateEvidenceReleaseV2,
    StructureGroupingUnionReplayReleaseV2,
    TerminalRunResultV1,
    ExecutionReleaseV3,
    EvidenceExcerptV2,
    ReviewerManifestV2,
    PrivateIdentityMapV2,
    PostLabelOriginGuessV1,
    RawExpertAnnotationV1,
    ExpertAdjudicationV1,
    RawDuplicatePartitionV2,
    DuplicatePartitionAdjudicationV2,
    FinalDuplicatePartitionV2,
    FinalExpertJudgmentV2,
    FinalGoldReleaseV2,
    FormalPilotAgreementReleaseV1,
    PilotRoundArtifactsV3,
    MainCandidatePoolReleaseV1,
    MainEligibilityReleaseV1,
    MainFrozenCaseReleaseV1,
    MainStructureUnionVerifierAttestationV1,
    MainStructureUnionReleaseV1,
    MainPreBudgetClosureReleaseV1,
    MainPhaseAuthorizationReleaseV1,
    MainPhaseExecutionReleaseV1,
    MainExpertAssignmentV1,
    MainReviewerManifestV1,
    MainPrivateIdentityMapV1,
    MainRawLabelV1,
    MainLabelAdjudicationV1,
    MainRawDuplicatePartitionV1,
    MainDuplicateAdjudicationV1,
    MainFinalDuplicatePartitionV1,
    MainGoldReleaseV1,
    MainGoldFormalVerifierAttestationV1,
    AnalysisInputReleaseV2,
    ModelNativeReasoningWorkItemV1,
    ModelNativeReasoningResponseV1,
    ModelNativeReasoningReceiptV1,
    ArmExecutionTraceV1,
    DevelopmentPromotionReleaseV1,
    FusionConfigurationReleaseV1,
    DevelopmentFusionGateReleaseV1,
    LockedLabelSealV1,
    LockedTestAuthorizationReleaseV1,
    LockedUnsealLedgerReleaseV1,
    LockedAnnotationUnsealReleaseV1,
    ProtocolDeviationReleaseV1,
    ClaimSupportReleaseV1,
    ReleaseAuthorityPolicyV1,
    ScientificReviewerIdentityAttestationV1,
    ScientificReviewerAttestationV1,
    ScientificReviewReleaseV1,
    ReleaseControlAttestationV1,
    PublicReleaseAuthorizationV1,
    LocalSensitivityNotRunReleaseV1,
    LockedExecutionPlanV1,
    FlatBandCampaignReleaseV1,
)

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
OUTPUT_DIRECTORY = (
    REPOSITORY_ROOT / "artifacts" / "experiment" / "flatband-benchmark-20260809"
)


@dataclass(frozen=True)
class BundleSpec:
    audience: str
    schema_version: str
    schema_id: str
    instance_release_policy: str
    roots: tuple[type[BaseModel], ...]
    schema_path: Path

    @property
    def hash_path(self) -> Path:
        return self.schema_path.with_suffix(".sha256")


BUNDLE_SPECS = (
    BundleSpec(
        audience="PUBLIC_PROTOCOL",
        schema_version="flatband-research-public-protocol-bundle-v2",
        schema_id=(
            "urn:materials-screening-agent:flatband-research-public-protocol:v2"
        ),
        instance_release_policy=(
            "PUBLIC_INSTANCES_REQUIRE_PHASE_APPROPRIATE_RELEASE_AUTHORIZATION"
        ),
        roots=PUBLIC_PROTOCOL_ROOTS,
        schema_path=OUTPUT_DIRECTORY / "research_public_protocol.schema.json",
    ),
    BundleSpec(
        audience="PRIVATE_CUSTODY",
        schema_version="flatband-research-private-custody-bundle-v3",
        schema_id=(
            "urn:materials-screening-agent:flatband-research-private-custody:v3"
        ),
        instance_release_policy=(
            "SCHEMA_DEFINITION_MAY_BE_PUBLIC;PRIVATE_INSTANCES_MUST_NOT_BE_PUBLISHED"
        ),
        roots=PRIVATE_CUSTODY_ROOTS,
        schema_path=OUTPUT_DIRECTORY / "research_private_custody.schema.json",
    ),
)


def _assert_disjoint_roots() -> None:
    public = {model.__name__ for model in PUBLIC_PROTOCOL_ROOTS}
    private = {model.__name__ for model in PRIVATE_CUSTODY_ROOTS}
    overlap = public & private
    if overlap:
        raise RuntimeError(
            "public and private research root allowlists overlap: "
            + ", ".join(sorted(overlap))
        )


def generated_bytes(spec: BundleSpec) -> bytes:
    """Return one deterministic audience bundle without touching the filesystem."""

    _assert_disjoint_roots()
    root_names = tuple(sorted(model.__name__ for model in spec.roots))
    if len(root_names) != len(set(root_names)):
        raise RuntimeError(f"{spec.audience} root allowlist contains duplicates")
    document = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": spec.schema_id,
        "schema_version": spec.schema_version,
        "audience": spec.audience,
        "protocol_readiness": "PILOT_NO_GO",
        "contract_implementation_status": "FULL_FLOW_CONTRACT_IMPLEMENTED",
        "real_execution_status": "NOT_RUN",
        "semantic_reasoning_policy": (
            "MODEL_NATIVE_ONLY_LOCAL_SEMANTIC_MODEL_PROHIBITED"
        ),
        "instance_release_policy": spec.instance_release_policy,
        "root_set_policy": "EXPLICIT_DISJOINT_ALLOWLIST_FAIL_CLOSED",
        "root_allowlist": root_names,
        "models": {
            model.__name__: model.model_json_schema(mode="validation")
            for model in spec.roots
        },
    }
    return (
        json.dumps(
            document,
            ensure_ascii=False,
            sort_keys=True,
            indent=2,
            allow_nan=False,
        )
        + "\n"
    ).encode("utf-8")


def hash_bytes(payload: bytes, schema_path: Path) -> bytes:
    return (
        f"{hashlib.sha256(payload).hexdigest()}  {schema_path.name}\n".encode()
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--check",
        action="store_true",
        help="fail unless both active audience bundles match production models",
    )
    arguments = parser.parse_args()
    generated = tuple(
        (
            spec,
            generated_bytes(spec),
        )
        for spec in BUNDLE_SPECS
    )
    if arguments.check:
        for spec, payload in generated:
            digest = hash_bytes(payload, spec.schema_path)
            if not spec.schema_path.is_file() or not spec.hash_path.is_file():
                raise SystemExit(
                    f"{spec.audience} research contract artifacts are missing"
                )
            if (
                spec.schema_path.read_bytes() != payload
                or spec.hash_path.read_bytes() != digest
            ):
                raise SystemExit(
                    f"{spec.audience} research contract artifacts drifted"
                )
        print("Audience-split flat-band research contract bundles valid")
        return 0

    OUTPUT_DIRECTORY.mkdir(parents=True, exist_ok=True)
    for spec, payload in generated:
        digest = hash_bytes(payload, spec.schema_path)
        spec.schema_path.write_bytes(payload)
        spec.hash_path.write_bytes(digest)
        print(f"{spec.audience} {hashlib.sha256(payload).hexdigest()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
