from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from collections import Counter
from pathlib import Path
from typing import Any

from material_agent.research.flatband_contracts import RawExpertAnnotationV1


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
    "89023a4d07531b598a563e78b0c2b225876981ca9e1d5aea884a493f6aa7be81"
)

PRIVATE_SCHEMA_PATH = ARTIFACT_DIRECTORY / "research_private_custody.schema.json"
PRIVATE_HASH_PATH = PRIVATE_SCHEMA_PATH.with_suffix(".sha256")
PRIVATE_EXPECTED_SHA256 = (
    "97e95c079c82d41afda7cc5deca1229406dc69387a896da6a9c6686c312b194c"
)

PUBLIC_PROTOCOL_ROOT_NAMES = {
    "BenchmarkSplitManifestV2",
    "BudgetManifestV2",
    "CaseSourcePolicyAttestationV2",
    "ExecutionMatrixV2",
    "FormalPilotAgreementGateReleaseV1",
    "LeakageComponentReleaseV3",
    "MechanismLineageAssignmentV3",
    "MechanismLineageRegistryV3",
    "PublicExpertIdentityReleaseV2",
    "ResearchRankingV1",
    "Top5ProjectionV1",
}
PRIVATE_CUSTODY_ROOT_NAMES = {
    "CalibrationSetManifestV2",
    "CandidateEligibilityAssignmentReleaseV3",
    "CandidatePoolReleaseV3",
    "DuplicatePartitionAdjudicationV2",
    "EvidenceExcerptV2",
    "ExecutionReleaseV3",
    "ExpertAdjudicationV1",
    "ExpertStudyRegistryV2",
    "FinalDuplicatePartitionV2",
    "FinalExpertJudgmentV2",
    "FinalGoldReleaseV2",
    "FlatBandBenchmarkCaseV1",
    "FormalPilotAgreementReleaseV1",
    "FrozenCaseReleaseV3",
    "HypothesisPacketV1",
    "LeakageRoundClosureContextV3",
    "MechanismLineageAssignmentCurationReleaseV3",
    "MechanismLineageCurationReleaseV3",
    "PilotPreBudgetClosureReleaseV3",
    "PostLabelOriginGuessV1",
    "PreRunEligibilityReleaseV3",
    "PrivateExpertIdentityCustodianAttestationV2",
    "PrivateIdentityMapV2",
    "RawDuplicatePartitionV2",
    "RawExpertAnnotationV1",
    "ReviewerManifestV2",
    "SourceReceiptBundleV1",
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

PENDING_NO_GO_ROOT_NAMES = {
    "AnalysisInputReleaseV1",
    "StructureGroupingComputationReleaseV2",
    "StructureGroupingInputManifestV2",
    "StructureGroupingPrivateEvidenceReleaseV2",
}

PUBLIC_FORBIDDEN_PROPERTIES = {
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
    "natural_person_commitment_sha256",
    "opaque_natural_person_subject_ref",
    "private_identity_map_id",
    "private_identity_map_sha256",
    "private_text_artifact_uri",
    "private_text_artifact_uri_sha256",
    "qualification_summary",
    "rationale",
    "source_span_text",
    "span_utf8",
    "value_utf8",
}
PRIVATE_REQUIRED_SENSITIVE_PROPERTIES = {
    "blinded_reviewer_id",
    "conflict_assessments",
    "excerpt",
    "identity_evidence_artifact_uri",
    "natural_person_commitment_sha256",
    "opaque_natural_person_subject_ref",
    "private_identity_map_id",
    "private_text_artifact_uri",
    "rationale",
    "span_utf8",
    "value_utf8",
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
    "847cbde880724c12ec06e70df79b2d722d2063431c9ad07ac85c9c437dfa9a23"
)
ANNOTATION_GUIDE_PATH = ARTIFACT_DIRECTORY / "ANNOTATION_GUIDE.md"
ANNOTATION_GUIDE_SHA256 = (
    "1f69b974c3f67be7e185d2ada942132a3f32c33bc8420568ae11162c327af7d5"
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
    assert public["schema_version"] == (
        "flatband-research-public-protocol-bundle-v1"
    )
    assert private["schema_version"] == (
        "flatband-research-private-custody-bundle-v1"
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
    assert PENDING_NO_GO_ROOT_NAMES.isdisjoint(public_titles)


def test_private_bundle_carries_custody_fields_without_authorizing_release() -> None:
    private = _load(PRIVATE_SCHEMA_PATH)
    private_properties = _property_names(private["models"])
    private_titles = _schema_titles(private["models"])
    assert PRIVATE_REQUIRED_SENSITIVE_PROPERTIES <= private_properties
    assert RETIRED_LEGACY_ROOT_NAMES.isdisjoint(private_titles)
    assert PENDING_NO_GO_ROOT_NAMES.isdisjoint(private_titles)
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


def test_preregistration_and_annotation_guide_drafts_are_content_addressed() -> None:
    preregistration = PREREGISTRATION_PATH.read_bytes()
    annotation_guide = ANNOTATION_GUIDE_PATH.read_bytes()
    assert hashlib.sha256(preregistration).hexdigest() == PREREGISTRATION_SHA256
    assert hashlib.sha256(annotation_guide).hexdigest() == ANNOTATION_GUIDE_SHA256
    assert PREREGISTRATION_PATH.with_suffix(".sha256").read_text(
        encoding="utf-8"
    ) == f"{PREREGISTRATION_SHA256}  {PREREGISTRATION_PATH.name}\n"
    assert ANNOTATION_GUIDE_PATH.with_suffix(".sha256").read_text(
        encoding="utf-8"
    ) == f"{ANNOTATION_GUIDE_SHA256}  {ANNOTATION_GUIDE_PATH.name}\n"
