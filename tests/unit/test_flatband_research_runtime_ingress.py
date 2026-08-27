from __future__ import annotations

import hashlib
import json

import pytest
from pydantic import ValidationError

from material_agent.inspiration.models import canonical_sha256, deterministic_id
from material_agent.research.flatband_execution import (
    LogicalPageReceiptV1,
    LogicalQueryReceiptV1,
    NormalizedMetadataArtifactV1,
    NormalizedMetadataFieldV1,
    PhysicalHopReceiptV1,
    ResearchRankingV1,
    SourceReceiptBundleV1,
    source_policy_values,
)
from material_agent.research.flatband_runtime_ingress import (
    SUPPORTED_RUNTIME_ARTIFACT_KINDS_V1,
    RuntimeArtifactKind,
    ingest_runtime_artifact_v1,
)

SHA_A = "a" * 64
SHA_B = "b" * 64
SHA_C = "c" * 64
SHA_D = "d" * 64


def _ranking_payload() -> dict[str, object]:
    return {
        "schema_version": "flatband-research-ranking-v1",
        "budget_manifest_id": "budget-001",
        "budget_manifest_sha256": SHA_A,
        "cell_id": "cell-001",
        "run_id": "run-001",
        "case_id": "case-001",
        "case_sha256": SHA_B,
        "system_config_id": "config-001",
        "system_config_sha256": SHA_A,
        "requested_top_k": 5,
        "positions": [],
        "underfill_reason_codes": ["NO_ELIGIBLE_PACKETS"],
        "created_at": "2026-08-10T10:00:00+08:00",
        "scientific_conclusion": False,
    }


def _field_payload(value: str = "bounded metadata") -> dict[str, object]:
    return {
        "schema_version": "flatband-normalized-metadata-field-v1",
        "field_name": "abstract",
        "json_path": "/abstract",
        "value_utf8": value,
        "value_utf8_sha256": hashlib.sha256(value.encode("utf-8")).hexdigest(),
        "value_utf8_bytes": len(value.encode("utf-8")),
        "provenance_scope": "INTERNAL_REPLAY_NOT_EXTERNAL_ATTESTATION",
        "private_storage_required": True,
        "public_release_allowed": False,
    }


def test_runtime_ingress_addresses_ranking_and_metadata_field_from_json() -> None:
    ranking = ingest_runtime_artifact_v1(
        RuntimeArtifactKind.RESEARCH_RANKING,
        json.dumps(_ranking_payload()),
    )
    assert isinstance(ranking, ResearchRankingV1)
    ranking_semantic = ranking.model_dump(
        mode="python", exclude={"ranking_id", "ranking_sha256"}
    )
    assert ranking.ranking_sha256 == canonical_sha256(ranking_semantic)
    assert ranking.ranking_id == deterministic_id(
        "research-ranking", {"ranking_sha256": ranking.ranking_sha256}
    )

    field = ingest_runtime_artifact_v1(
        RuntimeArtifactKind.NORMALIZED_METADATA_FIELD,
        json.dumps(_field_payload(), ensure_ascii=False).encode("utf-8"),
    )
    assert isinstance(field, NormalizedMetadataFieldV1)
    field_semantic = field.model_dump(
        mode="python", exclude={"field_artifact_id", "field_artifact_sha256"}
    )
    assert field.field_artifact_sha256 == canonical_sha256(field_semantic)


def test_runtime_ingress_parses_and_revalidates_nested_metadata_artifact() -> None:
    field = ingest_runtime_artifact_v1(
        RuntimeArtifactKind.NORMALIZED_METADATA_FIELD,
        _field_payload(),
    )
    assert isinstance(field, NormalizedMetadataFieldV1)
    policy = source_policy_values("crossref")
    normalized_sha = canonical_sha256(
        (
            {
                "field_name": field.field_name,
                "json_path": field.json_path,
                "value_utf8": field.value_utf8,
                "value_utf8_sha256": field.value_utf8_sha256,
            },
        )
    )
    artifact_payload = {
        "schema_version": "flatband-normalized-metadata-artifact-v1",
        "source_id": "crossref",
        "budget_manifest_id": "budget-001",
        "budget_manifest_sha256": SHA_A,
        "cell_id": "cell-001",
        "run_id": "run-001",
        "system_config_id": "config-001",
        "system_config_sha256": SHA_B,
        "source_record_id": "record-001",
        "source_url_sha256": SHA_A,
        "source_field_projection_sha256": policy[
            "source_field_projection_sha256"
        ],
        "normalized_metadata_sha256": normalized_sha,
        "fields": [field.model_dump(mode="json")],
        "provenance_scope": "INTERNAL_REPLAY_NOT_EXTERNAL_ATTESTATION",
        "private_storage_required": True,
        "public_release_allowed": False,
        "external_provider_attestation": "NOT_PROVIDED",
    }
    artifact = ingest_runtime_artifact_v1(
        RuntimeArtifactKind.NORMALIZED_METADATA_ARTIFACT,
        json.dumps(artifact_payload),
    )
    assert isinstance(artifact, NormalizedMetadataArtifactV1)
    assert artifact.fields == (field,)

    nested_unknown = json.loads(json.dumps(artifact_payload))
    nested_unknown["fields"][0]["unreviewed_body_text"] = "forbidden"
    with pytest.raises(ValidationError):
        ingest_runtime_artifact_v1(
            RuntimeArtifactKind.NORMALIZED_METADATA_ARTIFACT,
            nested_unknown,
        )


def test_runtime_ingress_builds_complete_network_receipt_chain() -> None:
    policy = source_policy_values("crossref")
    binding = {
        "budget_manifest_id": "budget-001",
        "budget_manifest_sha256": SHA_A,
        "cell_id": "cell-001",
        "run_id": "run-001",
        "system_config_id": "config-001",
        "system_config_sha256": SHA_B,
    }
    query_identity = canonical_sha256(
        {
            "source_id": "crossref",
            "query_plan_sha256": SHA_C,
            "source_catalog_row_sha256": policy["source_catalog_row_sha256"],
            "request_path_class": policy["request_path_class"],
            "source_field_projection_sha256": policy[
                "source_field_projection_sha256"
            ],
            "normalized_query_sha256": SHA_A,
            "filter_sha256": SHA_B,
            "sort_sha256": SHA_D,
        }
    )
    logical_query_id = deterministic_id(
        "logical-query",
        {
            "source_id": "crossref",
            "query_plan_sha256": SHA_C,
            "query_identity_sha256": query_identity,
        },
    )
    query = ingest_runtime_artifact_v1(
        RuntimeArtifactKind.LOGICAL_QUERY_RECEIPT,
        {
            "schema_version": "flatband-logical-query-receipt-v1",
            "source_id": "crossref",
            **binding,
            "logical_query_id": logical_query_id,
            "query_plan_sha256": SHA_C,
            "source_catalog_sha256": policy["source_catalog_sha256"],
            "source_catalog_row_sha256": policy["source_catalog_row_sha256"],
            "request_path_class": policy["request_path_class"],
            "request_path_template_sha256": policy[
                "request_path_template_sha256"
            ],
            "source_field_projection_sha256": policy[
                "source_field_projection_sha256"
            ],
            "normalized_query_sha256": SHA_A,
            "filter_sha256": SHA_B,
            "sort_sha256": SHA_D,
            "query_identity_sha256": query_identity,
            "created_at": "2026-08-10T10:00:00+08:00",
            "external_provider_attestation": "NOT_PROVIDED",
        },
    )
    assert isinstance(query, LogicalQueryReceiptV1)

    request_query_sha256 = canonical_sha256(
        {
            "query_identity_sha256": query_identity,
            "page_number": 1,
            "cursor_sha256": None,
        }
    )
    request_path_sha256 = canonical_sha256(
        {
            "request_path_template_sha256": policy[
                "request_path_template_sha256"
            ],
            "path_parameters_sha256": SHA_C,
        }
    )
    request_identity_sha256 = canonical_sha256(
        {
            "source_id": "crossref",
            "request_method": "GET",
            "request_host": "api.crossref.org",
            "request_path_sha256": request_path_sha256,
            "request_query_sha256": request_query_sha256,
            "data_class": "PUBLIC_BIBLIOGRAPHIC_METADATA",
        }
    )
    page = ingest_runtime_artifact_v1(
        RuntimeArtifactKind.LOGICAL_PAGE_RECEIPT,
        {
            "schema_version": "flatband-logical-page-receipt-v1",
            "source_id": "crossref",
            **binding,
            "logical_query_id": logical_query_id,
            "query_receipt_id": query.query_receipt_id,
            "query_receipt_sha256": query.query_receipt_sha256,
            "query_plan_sha256": SHA_C,
            "query_identity_sha256": query_identity,
            "page_number": 1,
            "cursor_sha256": None,
            "request_method": "GET",
            "request_host": "api.crossref.org",
            "request_path_class": policy["request_path_class"],
            "request_path_template_sha256": policy[
                "request_path_template_sha256"
            ],
            "path_parameters_sha256": SHA_C,
            "request_path_sha256": request_path_sha256,
            "request_query_sha256": request_query_sha256,
            "data_class": "PUBLIC_BIBLIOGRAPHIC_METADATA",
            "request_identity_sha256": request_identity_sha256,
            "cache_disposition": "NETWORK_FETCH",
            "cache_key_sha256": SHA_D,
            "cache_entry_id": None,
            "cache_entry_sha256": None,
            "response_status_code": 200,
            "response_media_type": "application/json",
            "response_identity_sha256": SHA_B,
            "response_bytes": 128,
            "record_projection_sha256": SHA_A,
            "source_field_projection_sha256": policy[
                "source_field_projection_sha256"
            ],
            "record_identity_sha256s": [],
            "document_identity_sha256s": [],
            "completed_at": "2026-08-10T10:02:00+08:00",
        },
    )
    assert isinstance(page, LogicalPageReceiptV1)

    hop = ingest_runtime_artifact_v1(
        RuntimeArtifactKind.PHYSICAL_HOP_RECEIPT,
        {
            "schema_version": "flatband-physical-hop-receipt-v1",
            "source_id": "crossref",
            **binding,
            "logical_page_receipt_id": page.page_receipt_id,
            "logical_page_receipt_sha256": page.page_receipt_sha256,
            "hop_index": 1,
            "request_method": "GET",
            "request_host": "api.crossref.org",
            "request_path_class": policy["request_path_class"],
            "request_path_template_sha256": policy[
                "request_path_template_sha256"
            ],
            "path_parameters_sha256": SHA_C,
            "request_path_sha256": request_path_sha256,
            "request_query_sha256": request_query_sha256,
            "data_class": "PUBLIC_BIBLIOGRAPHIC_METADATA",
            "request_identity_sha256": request_identity_sha256,
            "response_status_code": 200,
            "response_media_type": "application/json",
            "response_identity_sha256": SHA_B,
            "response_bytes": 128,
            "redirect_target_request_sha256": None,
            "completed_at": "2026-08-10T10:01:00+08:00",
        },
    )
    assert isinstance(hop, PhysicalHopReceiptV1)

    bundle = ingest_runtime_artifact_v1(
        RuntimeArtifactKind.SOURCE_RECEIPT_BUNDLE,
        {
            "schema_version": "flatband-source-receipt-bundle-v1",
            "source_id": "crossref",
            "retrieval_identity_sha256": policy["retrieval_identity_sha256"],
            "cache_snapshot_sha256": SHA_D,
            **binding,
            "source_catalog_sha256": policy["source_catalog_sha256"],
            "provenance_scope": "INTERNAL_REPLAY_NOT_EXTERNAL_ATTESTATION",
            "cache_inventory_entries": [],
            "logical_queries": [query.model_dump(mode="json")],
            "logical_pages": [page.model_dump(mode="json")],
            "metadata_records": [],
            "normalized_metadata_artifacts": [],
            "metadata_span_preimages": [],
            "evidence_links": [],
            "physical_hops": [hop.model_dump(mode="json")],
        },
    )
    assert isinstance(bundle, SourceReceiptBundleV1)
    assert bundle.logical_queries == (query,)
    assert bundle.logical_pages == (page,)
    assert bundle.physical_hops == (hop,)


def test_runtime_ingress_fails_closed_on_identity_unknown_kind_and_model_copy() -> None:
    ranking_payload = _ranking_payload()
    with pytest.raises(ValueError, match="cannot choose content identity"):
        ingest_runtime_artifact_v1(
            RuntimeArtifactKind.RESEARCH_RANKING,
            {**ranking_payload, "ranking_id": "caller-chosen"},
        )
    with pytest.raises(ValueError, match="unknown fields"):
        ingest_runtime_artifact_v1(
            RuntimeArtifactKind.RESEARCH_RANKING,
            {**ranking_payload, "novel_score": 0.99},
        )
    with pytest.raises(ValueError, match="unsupported runtime artifact kind"):
        ingest_runtime_artifact_v1("flatband-unregistered-runtime-v1", ranking_payload)

    wrong_kind = _field_payload()
    wrong_kind["schema_version"] = "flatband-research-ranking-v1"
    with pytest.raises(ValidationError):
        ingest_runtime_artifact_v1(
            RuntimeArtifactKind.NORMALIZED_METADATA_FIELD,
            wrong_kind,
        )

    ranking = ingest_runtime_artifact_v1(
        RuntimeArtifactKind.RESEARCH_RANKING,
        ranking_payload,
    )
    assert isinstance(ranking, ResearchRankingV1)
    forged = ranking.model_copy(update={"underfill_reason_codes": ()})
    with pytest.raises(TypeError, match="JSON object or JSON text"):
        ingest_runtime_artifact_v1(
            RuntimeArtifactKind.RESEARCH_RANKING,
            forged,  # type: ignore[arg-type]
        )
    forged_json = forged.model_dump_json(
        exclude={"ranking_id", "ranking_sha256"}
    )
    with pytest.raises(ValidationError):
        ingest_runtime_artifact_v1(
            RuntimeArtifactKind.RESEARCH_RANKING,
            forged_json,
        )


def test_runtime_ingress_whitelist_covers_execution_and_model_native_roots() -> None:
    assert set(SUPPORTED_RUNTIME_ARTIFACT_KINDS_V1) == {
        "flatband-system-config-v1",
        "flatband-research-ranking-v1",
        "flatband-physical-hop-receipt-v1",
        "flatband-cache-inventory-entry-v1",
        "flatband-logical-query-receipt-v1",
        "flatband-logical-page-receipt-v1",
        "flatband-normalized-metadata-field-v1",
        "flatband-normalized-metadata-artifact-v1",
        "flatband-metadata-record-receipt-v1",
        "flatband-metadata-span-preimage-v1",
        "flatband-evidence-link-receipt-v1",
        "flatband-source-receipt-bundle-v1",
        "flatband-llm-invocation-receipt-v1",
        "flatband-local-model-invocation-receipt-v1",
        "flatband-terminal-run-result-v1",
        "flatband-top5-projection-v1",
        "flatband-model-native-work-item-v1",
        "flatband-model-native-response-v1",
        "flatband-model-native-receipt-v1",
        "flatband-arm-execution-trace-v1",
    }
