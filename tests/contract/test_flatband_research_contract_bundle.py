from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from collections import Counter
from pathlib import Path
from typing import Any

from material_agent.research.flatband_contracts import RawExpertAnnotationV1
from material_agent.research.flatband_ingress import SOURCE_AUDIT_V1_SHA256


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
ARTIFACT_DIRECTORY = (
    REPOSITORY_ROOT
    / "artifacts"
    / "experiment"
    / "flatband-benchmark-20260809"
)
GENERATOR_PATH = REPOSITORY_ROOT / "scripts" / "generate_flatband_research_contracts.py"

LEGACY_SCHEMA_PATH = ARTIFACT_DIRECTORY / "research_contracts.schema.json"
LEGACY_HASH_PATH = LEGACY_SCHEMA_PATH.with_suffix(".sha256")
LEGACY_EXPECTED_SHA256 = (
    "f7f265b7ec130f88494ff70d807395ecd88362ca5a4c7431057241022e9608ae"
)

PUBLIC_SCHEMA_PATH = ARTIFACT_DIRECTORY / "research_public_protocol.schema.json"
PUBLIC_HASH_PATH = PUBLIC_SCHEMA_PATH.with_suffix(".sha256")
PUBLIC_EXPECTED_SHA256 = (
    "380303adda9cab0929f8fc90ab3b59e5ce9975e3af05c83684bf9997699c184e"
)

PRIVATE_SCHEMA_PATH = ARTIFACT_DIRECTORY / "research_private_custody.schema.json"
PRIVATE_HASH_PATH = PRIVATE_SCHEMA_PATH.with_suffix(".sha256")
PRIVATE_EXPECTED_SHA256 = (
    "4581db7cb6aa7ae1cd82ade26110d72a5d0732c8e0f8e06a59cb8cca406bf781"
)

PUBLIC_PROTOCOL_ROOT_NAMES = {
    "BenchmarkSplitManifestV2",
    "BudgetManifestV2",
    "CaseSourcePolicyAttestationV2",
    "ExecutionMatrixV2",
    "FormalPilotAgreementGateReleaseV1",
    "LeakageComponentReleaseV3",
    "MainCapacityPolicyV1",
    "MainSamplingPolicyReleaseV1",
    "MechanismLineageAssignmentV3",
    "MechanismLineageRegistryV3",
    "PublicBenchmarkProjectionV1",
    "PublicBenchmarkResultReleaseV1",
    "PublicExpertIdentityReleaseV2",
    "ResearchRankingV1",
    "SourceCatalogCheckpointReleaseV1",
    "Top5ProjectionV1",
}
PRIVATE_CUSTODY_ROOT_NAMES = {
    "AnalysisInputReleaseV2",
    "ArmExecutionTraceV1",
    "CalibrationSetManifestV2",
    "CandidateEligibilityAssignmentReleaseV3",
    "CandidatePoolReleaseV3",
    "ClaimSupportReleaseV1",
    "DerivativeScreeningReleaseV3",
    "DevelopmentFusionGateReleaseV1",
    "DevelopmentPromotionReleaseV1",
    "DuplicatePartitionAdjudicationV2",
    "EvidenceExcerptV2",
    "ExecutionReleaseV3",
    "ExpertAdjudicationV1",
    "ExpertStudyRegistryV2",
    "FinalDuplicatePartitionV2",
    "FinalExpertJudgmentV2",
    "FinalGoldReleaseV2",
    "FlatBandBenchmarkCaseV1",
    "FlatBandCampaignReleaseV1",
    "FormalPilotAgreementReleaseV1",
    "FrozenCaseReleaseV3",
    "FusionConfigurationReleaseV1",
    "HypothesisPacketV1",
    "LeakageRoundClosureContextV3",
    "LocalSensitivityNotRunReleaseV1",
    "LockedAnnotationUnsealReleaseV1",
    "LockedExecutionPlanV1",
    "LockedLabelSealV1",
    "LockedTestAuthorizationReleaseV1",
    "LockedUnsealLedgerReleaseV1",
    "MainCandidatePoolReleaseV1",
    "MainDuplicateAdjudicationV1",
    "MainEligibilityReleaseV1",
    "MainExpertAssignmentV1",
    "MainFinalDuplicatePartitionV1",
    "MainFrozenCaseReleaseV1",
    "MainGoldFormalVerifierAttestationV1",
    "MainGoldReleaseV1",
    "MainLabelAdjudicationV1",
    "MainPhaseAuthorizationReleaseV1",
    "MainPhaseExecutionReleaseV1",
    "MainPreBudgetClosureReleaseV1",
    "MainPrivateIdentityMapV1",
    "MainRawDuplicatePartitionV1",
    "MainRawLabelV1",
    "MainReviewerManifestV1",
    "MainStructureUnionReleaseV1",
    "MainStructureUnionVerifierAttestationV1",
    "MechanismLineageAssignmentCurationReleaseV3",
    "MechanismLineageCurationReleaseV3",
    "ModelNativeReasoningReceiptV1",
    "ModelNativeReasoningResponseV1",
    "ModelNativeReasoningWorkItemV1",
    "PilotPreBudgetClosureReleaseV3",
    "PilotRoundArtifactsV3",
    "PostLabelOriginGuessV1",
    "PreRunEligibilityReleaseV3",
    "PrivateExpertIdentityCustodianAttestationV2",
    "PrivateIdentityMapV2",
    "ProtocolDeviationReleaseV1",
    "PublicReleaseAuthorizationV1",
    "RawDuplicatePartitionV2",
    "RawExpertAnnotationV1",
    "ReleaseAuthorityPolicyV1",
    "ReleaseControlAttestationV1",
    "ReviewerManifestV2",
    "ScientificReviewReleaseV1",
    "ScientificReviewerAttestationV1",
    "ScientificReviewerIdentityAttestationV1",
    "SourceReceiptBundleV1",
    "StructureGroupingPrivateEvidenceReleaseV2",
    "StructureGroupingUnionReplayReleaseV2",
    "TerminalRunResultV1",
}
LEGACY_ROOT_NAMES = {
    "BenchmarkSplitManifestV1",
    "BlindingManifestV1",
    "ExpertAdjudicationV1",
    "ExpertRegistryV1",
    "FlatBandBenchmarkCaseV1",
    "FlatBandEvidenceV1",
    "HypothesisPacketV1",
    "RawExpertAnnotationV1",
    "ResearchRunLedgerV1",
    "SourceRecordRefV1",
    "SystemRankingV1",
}
RETIRED_LEGACY_ROOT_NAMES = {
    "BenchmarkSplitManifestV1",
    "BlindingManifestV1",
    "ExecutionMatrixV1",
    "ExecutionReleaseV1",
    "ExecutionReleaseV2",
    "ExpertDuplicatePartitionV1",
    "ExpertRegistryV1",
    "ExpertStudyRegistryV1",
    "FinalGoldReleaseV1",
    "FrozenCaseReleaseV1",
    "FrozenCaseReleaseV2",
    "LeakageComponentReleaseV1",
    "LeakageComponentReleaseV2",
    "PreRunEligibilityReleaseV1",
    "PreRunEligibilityReleaseV2",
    "PrivateIdentityMapV1",
    "RawExpertDuplicatePartitionV1",
    "ResearchRunLedgerV1",
    "ReviewerManifestV1",
    "SystemRankingV1",
}

NON_ACTIVE_ROOT_NAMES = {
    "AnalysisInputReleaseV1",
    "LocalModelInvocationReceiptV1",
    "PrivateArtifactEnvelopeV1",
    "StructureGroupingComputationReleaseV2",
    "StructureGroupingInputManifestV2",
}
PRIVATE_NESTED_ONLY_ROOT_NAMES = {
    "AnalysisCellEvidenceV2",
    "CaseArmMetricRowV2",
    "LocalModelInvocationReceiptV1",
    "MainPhaseExecutionCellEvidenceV1",
    "MainReviewUnitV1",
    "StructureGroupingComputationReleaseV2",
    "StructureGroupingInputManifestV2",
}
PRIVATE_REQUIRED_NESTED_LEGACY_ROOT_NAMES = {"ExecutionReleaseV2"}

PUBLIC_FORBIDDEN_PROPERTIES = {
    "annotation_key_commitment_sha256",
    "annotation_key_material_included",
    "annotation_signature_hmac_sha256",
    "blinded_reviewer_id",
    "commitment_key_id",
    "confirmation_reference",
    "conflict_disclosures",
    "custodian_id",
    "custodian_policy_sha256",
    "domain_expertise",
    "excerpt",
    "identity_evidence_artifact_uri",
    "identity_evidence_sha256",
    "identity_evidence_uri",
    "independence_review",
    "key_material_included",
    "natural_person_commitment_sha256",
    "opaque_natural_person_subject_ref",
    "opaque_natural_person_ref",
    "private_artifact_uri",
    "private_identity_evidence_included",
    "private_identity_evidence_sha256",
    "private_identity_map_id",
    "private_identity_map_sha256",
    "private_text_artifact_uri",
    "private_text_artifact_uri_sha256",
    "qualification_summary",
    "raw_bytes_base64",
    "raw_labels",
    "raw_reviews",
    "adjudications",
    "release_control_attestations",
    "rationale",
    "review_signature_hmac_sha256",
    "reviewer_decision_key_commitment_sha256",
    "reviewer_decision_key_material_included",
    "signature_hmac_sha256",
    "source_span_text",
    "span_utf8",
    "value_utf8",
    "visible_request_json_utf8",
    "visible_response_json_utf8",
}
PUBLIC_SAFE_SENSITIVE_NAME_EXCEPTIONS = {
    # These carry no raw payload, signing material, or signature value.
    "canonical_group_key",
    "external_key_custody_attestation",
    "externally_trusted_signature_present",
    "raw_one_sided_p",
    "source_record_raw_sha256",
}
PRIVATE_REQUIRED_SENSITIVE_PROPERTIES = {
    "annotation_key_commitment_sha256",
    "annotation_signature_hmac_sha256",
    "blinded_reviewer_id",
    "conflict_assessments",
    "excerpt",
    "identity_evidence_artifact_uri",
    "identity_evidence_uri",
    "independence_review",
    "natural_person_commitment_sha256",
    "opaque_natural_person_subject_ref",
    "opaque_natural_person_ref",
    "private_artifact_uri",
    "private_identity_evidence_sha256",
    "private_identity_map_id",
    "private_text_artifact_uri",
    "rationale",
    "raw_bytes_base64",
    "raw_reviews",
    "adjudications",
    "review_signature_hmac_sha256",
    "signature_hmac_sha256",
    "span_utf8",
    "value_utf8",
    "visible_request_json_utf8",
    "visible_response_json_utf8",
}

SOURCE_CATALOG_PATH = ARTIFACT_DIRECTORY / "source_catalog.jsonl"
SOURCE_CATALOG_SCHEMA_PATH = ARTIFACT_DIRECTORY / "source_catalog.schema.json"
SOURCE_CATALOG_SHA256 = (
    "57c24de8f0cf616205b03ef16231def711f2dfa9fcac86d94beede9c01b0bb1f"
)
SOURCE_CATALOG_SCHEMA_SHA256 = (
    "a796757159679a02146ad00f306f8bc6db2642fdd3fc51c82b25aba36936efc7"
)
PREREGISTRATION_PATH = ARTIFACT_DIRECTORY / "PREREGISTRATION.md"
PREREGISTRATION_SHA256 = (
    "7906e2fc51f341cd699abcb237c3e2a82a55d0165e22167b2c4828a48f1c22b2"
)
ANNOTATION_GUIDE_PATH = ARTIFACT_DIRECTORY / "ANNOTATION_GUIDE.md"
ANNOTATION_GUIDE_SHA256 = (
    "ea74d6e748e84aabf8bb35ed57e358e6c0ef0030a270960a4f01eb637ceb982a"
)
SOURCE_AUDIT_PATH = ARTIFACT_DIRECTORY / "SOURCE_AUDIT.md"
SOURCE_AUDIT_SHA256 = (
    "5574eaa9f70e223a03991ef7f2e5942c38e1afd38898b54243fd4c66b3290660"
)
PLAN_PATH = ARTIFACT_DIRECTORY / "PLAN.md"
PLAN_SHA256 = "43644ce4a7facfe0e5a5cc0cc6e87aadde172949dff56c269002da0ad67635ac"
READINESS_REVIEW_PATH = ARTIFACT_DIRECTORY / "READINESS_REVIEW.md"
READINESS_REVIEW_SHA256 = (
    "7a442fe4dcc53aa3bd2bd439b4069baf888118177639151c65c7f09b574170c9"
)


def _walk(value: Any):
    yield value
    if isinstance(value, dict):
        for child in value.values():
            yield from _walk(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk(child)


def _load(path: Path) -> dict[str, Any]:
    return json.loads(path.read_bytes())


def _property_names(value: Any) -> set[str]:
    names: set[str] = set()
    for node in _walk(value):
        if isinstance(node, dict) and isinstance(node.get("properties"), dict):
            names.update(node["properties"])
    return names


def _schema_titles(value: Any) -> set[str]:
    return {
        node["title"]
        for node in _walk(value)
        if isinstance(node, dict) and isinstance(node.get("title"), str)
    }


def _root_properties(schema: dict[str, Any]) -> dict[str, Any]:
    properties = schema.get("properties")
    if isinstance(properties, dict):
        return properties
    reference = schema.get("$ref")
    if isinstance(reference, str) and reference.startswith("#/$defs/"):
        name = reference.removeprefix("#/$defs/")
        resolved = schema.get("$defs", {}).get(name, {}).get("properties")
        if isinstance(resolved, dict):
            return resolved
    raise AssertionError("root schema does not expose resolvable properties")


def _assert_content_addressed(
    schema_path: Path, hash_path: Path, expected_sha256: str
) -> dict[str, Any]:
    payload = schema_path.read_bytes()
    assert hashlib.sha256(payload).hexdigest() == expected_sha256
    assert hash_path.read_text(encoding="utf-8") == (
        f"{expected_sha256}  {schema_path.name}\n"
    )
    return json.loads(payload)


def _assert_strict_object_schemas(models: dict[str, Any]) -> None:
    for node in _walk(models):
        if isinstance(node, dict) and node.get("type") == "object":
            assert node.get("additionalProperties") is False


def test_active_research_contract_bundles_match_generator() -> None:
    result = subprocess.run(
        [sys.executable, str(GENERATOR_PATH), "--check"],
        cwd=REPOSITORY_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_active_bundles_are_content_addressed_and_root_disjoint() -> None:
    public = _assert_content_addressed(
        PUBLIC_SCHEMA_PATH, PUBLIC_HASH_PATH, PUBLIC_EXPECTED_SHA256
    )
    private = _assert_content_addressed(
        PRIVATE_SCHEMA_PATH, PRIVATE_HASH_PATH, PRIVATE_EXPECTED_SHA256
    )
    assert public["audience"] == "PUBLIC_PROTOCOL"
    assert private["audience"] == "PRIVATE_CUSTODY"
    assert public["protocol_readiness"] == private["protocol_readiness"] == (
        "PILOT_NO_GO"
    )
    assert public["contract_implementation_status"] == (
        private["contract_implementation_status"]
    ) == "FULL_FLOW_CONTRACT_IMPLEMENTED"
    assert public["real_execution_status"] == private["real_execution_status"] == (
        "NOT_RUN"
    )
    assert public["semantic_reasoning_policy"] == (
        private["semantic_reasoning_policy"]
    ) == "MODEL_NATIVE_ONLY_LOCAL_SEMANTIC_MODEL_PROHIBITED"
    assert public["schema_version"] == (
        "flatband-research-public-protocol-bundle-v2"
    )
    assert public["$id"] == (
        "urn:materials-screening-agent:flatband-research-public-protocol:v2"
    )
    assert private["schema_version"] == (
        "flatband-research-private-custody-bundle-v3"
    )
    assert private["$id"] == (
        "urn:materials-screening-agent:flatband-research-private-custody:v3"
    )
    assert public["instance_release_policy"] == (
        "PUBLIC_INSTANCES_REQUIRE_PHASE_APPROPRIATE_RELEASE_AUTHORIZATION"
    )
    assert private["instance_release_policy"] == (
        "SCHEMA_DEFINITION_MAY_BE_PUBLIC;PRIVATE_INSTANCES_MUST_NOT_BE_PUBLISHED"
    )
    assert public["root_set_policy"] == private["root_set_policy"] == (
        "EXPLICIT_DISJOINT_ALLOWLIST_FAIL_CLOSED"
    )
    assert set(public["models"]) == PUBLIC_PROTOCOL_ROOT_NAMES
    assert set(private["models"]) == PRIVATE_CUSTODY_ROOT_NAMES
    assert len(public["models"]) == 16
    assert len(private["models"]) == 73
    assert set(public["root_allowlist"]) == PUBLIC_PROTOCOL_ROOT_NAMES
    assert set(private["root_allowlist"]) == PRIVATE_CUSTODY_ROOT_NAMES
    assert public["root_allowlist"] == sorted(PUBLIC_PROTOCOL_ROOT_NAMES)
    assert private["root_allowlist"] == sorted(PRIVATE_CUSTODY_ROOT_NAMES)
    assert PUBLIC_PROTOCOL_ROOT_NAMES.isdisjoint(PRIVATE_CUSTODY_ROOT_NAMES)


def test_active_bundle_object_schemas_are_recursively_strict() -> None:
    _assert_strict_object_schemas(_load(PUBLIC_SCHEMA_PATH)["models"])
    _assert_strict_object_schemas(_load(PRIVATE_SCHEMA_PATH)["models"])


def test_public_bundle_excludes_sensitive_fields_private_roots_and_legacy() -> None:
    public_models = _load(PUBLIC_SCHEMA_PATH)["models"]
    public_properties = _property_names(public_models)
    public_titles = _schema_titles(public_models)
    assert not (PUBLIC_FORBIDDEN_PROPERTIES & public_properties)
    assert PRIVATE_CUSTODY_ROOT_NAMES.isdisjoint(public_titles)
    assert RETIRED_LEGACY_ROOT_NAMES.isdisjoint(public_titles)
    assert NON_ACTIVE_ROOT_NAMES.isdisjoint(public_titles)
    sensitive_looking_names = {
        name
        for name in public_properties
        if any(
            fragment in name.casefold()
            for fragment in ("private", "signature", "key", "raw", "hmac")
        )
    }
    assert sensitive_looking_names == PUBLIC_SAFE_SENSITIVE_NAME_EXCEPTIONS


def test_private_bundle_carries_custody_fields_without_authorizing_release() -> None:
    private = _load(PRIVATE_SCHEMA_PATH)
    private_properties = _property_names(private["models"])
    private_titles = _schema_titles(private["models"])
    assert PRIVATE_REQUIRED_SENSITIVE_PROPERTIES <= private_properties
    assert RETIRED_LEGACY_ROOT_NAMES.isdisjoint(private["models"])
    assert RETIRED_LEGACY_ROOT_NAMES & private_titles == (
        PRIVATE_REQUIRED_NESTED_LEGACY_ROOT_NAMES
    )
    assert NON_ACTIVE_ROOT_NAMES.isdisjoint(private["models"])
    assert PRIVATE_NESTED_ONLY_ROOT_NAMES <= private_titles
    assert "AnalysisInputReleaseV1" not in private_titles
    reviewer_manifest = private["models"]["ReviewerManifestV2"]
    release_flag_schemas = [
        node["properties"]["public_repository_release_allowed"]
        for node in _walk(reviewer_manifest)
        if isinstance(node, dict)
        and isinstance(node.get("properties"), dict)
        and "public_repository_release_allowed" in node["properties"]
    ]
    assert release_flag_schemas
    assert all(item.get("const") is False for item in release_flag_schemas)

    derivative = private["models"]["DerivativeScreeningReleaseV3"]["properties"]
    assert derivative["private_custody_required"]["const"] is True
    assert derivative["public_release_allowed"]["const"] is False
    assert derivative["human_screening_not_automated_truth"]["const"] is True
    assert derivative["scientific_conclusion"]["const"] is False

    for root_name in (
        "StructureGroupingPrivateEvidenceReleaseV2",
        "StructureGroupingUnionReplayReleaseV2",
    ):
        properties = private["models"][root_name]["properties"]
        assert properties["private_custody"]["const"] is True
        assert properties["scientific_conclusion"]["const"] is False

    structure_root = private["models"]["StructureGroupingPrivateEvidenceReleaseV2"]
    raw_artifact_schemas = [
        node
        for node in _walk(structure_root)
        if isinstance(node, dict) and node.get("title") == "RawStructureArtifactV2"
    ]
    assert len(raw_artifact_schemas) == 1
    raw_properties = raw_artifact_schemas[0]["properties"]
    assert raw_properties["private_custody"]["const"] is True
    assert raw_properties["public_release_allowed"]["const"] is False
    assert raw_properties["scientific_conclusion"]["const"] is False


def test_public_expert_identity_is_pseudonymous_by_contract() -> None:
    public_model = _load(PUBLIC_SCHEMA_PATH)["models"][
        "PublicExpertIdentityReleaseV2"
    ]
    identity_schemas = [
        node
        for node in _walk(public_model)
        if isinstance(node, dict) and node.get("title") == "PublicExpertIdentityV2"
    ]
    assert len(identity_schemas) == 1
    assert identity_schemas[0]["properties"]["pseudonymous"]["const"] is True
    properties = public_model["properties"]
    assert properties["externally_trusted_signature_present"]["const"] is False


def test_public_full_flow_surface_is_aggregate_and_discloses_evidence_ceiling() -> None:
    public_models = _load(PUBLIC_SCHEMA_PATH)["models"]
    source_checkpoint = public_models["SourceCatalogCheckpointReleaseV1"][
        "properties"
    ]
    assert source_checkpoint["source_audit_sha256"]["const"] == SOURCE_AUDIT_SHA256
    assert source_checkpoint["public_protocol_artifact"]["const"] is True
    assert source_checkpoint["scientific_conclusion"]["const"] is False

    projection = public_models["PublicBenchmarkProjectionV1"]["properties"]
    assert projection["aggregate_only"]["const"] is True
    assert projection["restricted_text_included"]["const"] is False
    assert projection["expert_identity_mapping_included"]["const"] is False
    assert projection["scientific_conclusion"]["const"] is False

    result = public_models["PublicBenchmarkResultReleaseV1"]["properties"]
    assert result["sanitized_aggregate_only"]["const"] is True
    assert result["novelty_claim_permitted"]["const"] is False
    assert result["real_material_discovery_claim_permitted"]["const"] is False
    assert result["dft_proof_claim_permitted"]["const"] is False
    assert result["authorization_scope"]["const"] == (
        "INTERNAL_PRECOMMITTED_HMAC_POLICY"
    )
    assert result["external_authority_identity_attestation"]["const"] == (
        "NOT_PROVIDED"
    )
    assert result["external_key_custody_attestation"]["const"] == "NOT_PROVIDED"
    assert result["external_publication_permission_claimed"]["const"] is False
    assert result["scientific_conclusion"]["const"] is False


def test_private_full_flow_roots_fail_closed_without_external_authority() -> None:
    private_models = _load(PRIVATE_SCHEMA_PATH)["models"]

    pilot_round = _root_properties(private_models["PilotRoundArtifactsV3"])
    assert pilot_round["private_custody_required"]["const"] is True
    assert pilot_round["public_repository_release_allowed"]["const"] is False
    assert pilot_round["blind_key_embedded"]["const"] is False
    assert pilot_round["hidden_reasoning_persisted"]["const"] is False

    local_not_run = _root_properties(
        private_models["LocalSensitivityNotRunReleaseV1"]
    )
    assert local_not_run["decision"]["const"] == "NOT_RUN_USER_PROHIBITED"
    assert local_not_run["execution_release_count"]["const"] == 0
    assert local_not_run["output_artifact_count"]["const"] == 0
    assert local_not_run["local_model_invocation_count"]["const"] == 0
    assert local_not_run["promotion_or_locked_use_allowed"]["const"] is False

    main_execution = _root_properties(
        private_models["MainPhaseExecutionReleaseV1"]
    )
    assert main_execution["e1_local_formal_alias_allowed"]["const"] is False
    assert main_execution["legacy_terminal_llm_path_allowed"]["const"] is False
    assert main_execution["local_semantic_model_allowed"]["const"] is False
    assert main_execution["chain_of_thought_consumed"]["const"] is False

    analysis = _root_properties(private_models["AnalysisInputReleaseV2"])
    assert analysis["caller_supplied_case_metrics_allowed"]["const"] is False
    assert analysis["local_semantic_model_used_for_analysis"]["const"] is False
    assert analysis["chain_of_thought_consumed"]["const"] is False

    campaign = _root_properties(private_models["FlatBandCampaignReleaseV1"])
    assert campaign["private_custody_required"]["const"] is True
    assert campaign["public_repository_release_allowed"]["const"] is False
    assert campaign["ephemeral_key_material_embedded"]["const"] is False
    assert campaign["local_semantic_model_campaign_path_allowed"]["const"] is False
    assert campaign["external_execution_attestation"]["const"] == "NOT_PROVIDED"
    assert campaign["external_expert_identity_attestation"]["const"] == (
        "NOT_PROVIDED"
    )
    assert campaign["external_annotation_key_custody_attestation"]["const"] == (
        "NOT_PROVIDED"
    )
    assert campaign["external_publication_permission_claimed"]["const"] is False
    assert campaign["real_main_structure_output_executed"]["const"] is False
    assert campaign["benchmark_performance_claimed"]["const"] is False
    assert campaign["scientific_conclusion"]["const"] is False


def test_private_hmac_roots_store_signatures_but_never_signing_keys() -> None:
    private_models = _load(PRIVATE_SCHEMA_PATH)["models"]
    signature_models = (
        ("MainRawLabelV1", "annotation_signature_algorithm"),
        ("MainLabelAdjudicationV1", "annotation_signature_algorithm"),
        ("MainRawDuplicatePartitionV1", "annotation_signature_algorithm"),
        ("MainDuplicateAdjudicationV1", "annotation_signature_algorithm"),
        ("ScientificReviewerIdentityAttestationV1", "signature_algorithm"),
        ("ScientificReviewerAttestationV1", "review_signature_algorithm"),
        ("ReleaseControlAttestationV1", "signature_algorithm"),
    )
    for model_name, algorithm_field in signature_models:
        properties = private_models[model_name]["properties"]
        assert properties[algorithm_field]["const"] == "HMAC-SHA256-PRECOMMITTED"
        key_flags = {
            key: schema["const"]
            for key, schema in properties.items()
            if key.endswith("key_material_included")
        }
        assert key_flags
        assert set(key_flags.values()) == {False}

    gold = private_models["MainGoldReleaseV1"]["properties"]
    assert gold["annotation_authentication_scope"]["const"] == (
        "INTERNAL_PRECOMMITTED_HMAC_POLICY"
    )
    assert gold["key_material_included"]["const"] is False
    assert gold["external_expert_identity_attestation"]["const"] == "NOT_PROVIDED"
    assert gold["external_annotation_key_custody_attestation"]["const"] == (
        "NOT_PROVIDED"
    )


def test_legacy_v0_bundle_is_immutable_and_not_an_active_output() -> None:
    legacy = _assert_content_addressed(
        LEGACY_SCHEMA_PATH, LEGACY_HASH_PATH, LEGACY_EXPECTED_SHA256
    )
    assert legacy["schema_version"] == "flatband-research-contract-bundle-v0-draft"
    assert set(legacy["models"]) == LEGACY_ROOT_NAMES
    _assert_strict_object_schemas(legacy["models"])
    assert LEGACY_SCHEMA_PATH not in {PUBLIC_SCHEMA_PATH, PRIVATE_SCHEMA_PATH}
    assert LEGACY_HASH_PATH not in {PUBLIC_HASH_PATH, PRIVATE_HASH_PATH}


def test_blinded_annotation_contract_cannot_encode_system_or_rank() -> None:
    schema_text = json.dumps(
        RawExpertAnnotationV1.model_json_schema(), sort_keys=True
    ).casefold()
    assert "system_id" not in schema_text
    assert "selection_rank" not in schema_text
    assert "scientific_conclusion" in schema_text


def test_source_catalog_identity_and_decisions_are_frozen() -> None:
    catalog_bytes = SOURCE_CATALOG_PATH.read_bytes()
    schema_bytes = SOURCE_CATALOG_SCHEMA_PATH.read_bytes()
    assert hashlib.sha256(catalog_bytes).hexdigest() == SOURCE_CATALOG_SHA256
    assert hashlib.sha256(schema_bytes).hexdigest() == SOURCE_CATALOG_SCHEMA_SHA256
    assert SOURCE_CATALOG_PATH.with_suffix(".sha256").read_text(
        encoding="utf-8"
    ) == f"{SOURCE_CATALOG_SHA256}  {SOURCE_CATALOG_PATH.name}\n"
    assert SOURCE_CATALOG_SCHEMA_PATH.with_suffix(".sha256").read_text(
        encoding="utf-8"
    ) == f"{SOURCE_CATALOG_SCHEMA_SHA256}  {SOURCE_CATALOG_SCHEMA_PATH.name}\n"
    rows = [
        json.loads(line)
        for line in catalog_bytes.decode("utf-8").splitlines()
        if line
    ]
    assert len(rows) == 20
    assert len({row["source_id"] for row in rows}) == 20
    assert Counter(row["decision"] for row in rows) == {
        "INCLUDE": 12,
        "CONDITIONAL": 5,
        "EXCLUDE": 3,
    }
    assert all(row["licenses"] for row in rows)
    standard_spdx_expressions = {
        "CC-BY-4.0",
        "CC-BY-SA-4.0",
        "CC0-1.0",
        "MIT",
    }
    assert all(
        license_item["expression"] in standard_spdx_expressions
        or license_item["expression"].startswith("LicenseRef-")
        for row in rows
        for license_item in row["licenses"]
    )
    assert all(
        url.startswith("https://")
        for row in rows
        for url in row["official_urls"]
    )


def test_protocol_documents_are_content_addressed() -> None:
    preregistration = PREREGISTRATION_PATH.read_bytes()
    annotation_guide = ANNOTATION_GUIDE_PATH.read_bytes()
    source_audit = SOURCE_AUDIT_PATH.read_bytes()
    plan = PLAN_PATH.read_bytes()
    readiness_review = READINESS_REVIEW_PATH.read_bytes()
    assert hashlib.sha256(preregistration).hexdigest() == PREREGISTRATION_SHA256
    assert hashlib.sha256(annotation_guide).hexdigest() == ANNOTATION_GUIDE_SHA256
    assert hashlib.sha256(source_audit).hexdigest() == SOURCE_AUDIT_SHA256
    assert SOURCE_AUDIT_V1_SHA256 == SOURCE_AUDIT_SHA256
    assert hashlib.sha256(plan).hexdigest() == PLAN_SHA256
    assert hashlib.sha256(readiness_review).hexdigest() == READINESS_REVIEW_SHA256
    assert PREREGISTRATION_PATH.with_suffix(".sha256").read_text(
        encoding="utf-8"
    ) == f"{PREREGISTRATION_SHA256}  {PREREGISTRATION_PATH.name}\n"
    assert ANNOTATION_GUIDE_PATH.with_suffix(".sha256").read_text(
        encoding="utf-8"
    ) == f"{ANNOTATION_GUIDE_SHA256}  {ANNOTATION_GUIDE_PATH.name}\n"
    assert SOURCE_AUDIT_PATH.with_suffix(".sha256").read_text(
        encoding="utf-8"
    ) == f"{SOURCE_AUDIT_SHA256}  {SOURCE_AUDIT_PATH.name}\n"
    assert PLAN_PATH.with_suffix(".sha256").read_text(
        encoding="utf-8"
    ) == f"{PLAN_SHA256}  {PLAN_PATH.name}\n"
    assert READINESS_REVIEW_PATH.with_suffix(".sha256").read_text(
        encoding="utf-8"
    ) == f"{READINESS_REVIEW_SHA256}  {READINESS_REVIEW_PATH.name}\n"
    "key_material_included",
    "private_identity_evidence_included",
    "private_identity_evidence_sha256",
    "release_control_attestations",
    "review_signature_hmac_sha256",
    "reviewer_decision_key_commitment_sha256",
    "reviewer_decision_key_material_included",
    "signature_hmac_sha256",
    "private_identity_evidence_sha256",
    "review_signature_hmac_sha256",
    "signature_hmac_sha256",
