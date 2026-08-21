"""Strict, content-addressed JSON ingress for flat-band runtime artifacts.

Runtime producers submit semantic JSON only.  They cannot choose the root
artifact's content ID or SHA-256.  This module parses every field against the
real frozen contract, derives the address from canonical semantic content,
and then runs the complete target-model validators.  It performs no network
access, model inference, or scientific judgment.

Nested addressed artifacts are references/pre-existing artifacts and retain
their own IDs and hashes.  Their :class:`~material_agent.inspiration.models.StrictModel`
validators are replayed by their containing model.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import TypeAlias

from pydantic import TypeAdapter

from material_agent.inspiration.models import (
    StrictModel,
    canonical_sha256,
    deterministic_id,
)
from material_agent.research.flatband_arm_runtime import (
    ArmExecutionTraceV1,
    ModelNativeReasoningReceiptV1,
    ModelNativeReasoningResponseV1,
    ModelNativeReasoningWorkItemV1,
)
from material_agent.research.flatband_execution import (
    CacheInventoryEntryV1,
    EvidenceLinkReceiptV1,
    LlmInvocationReceiptV1,
    LocalModelInvocationReceiptV1,
    LogicalPageReceiptV1,
    LogicalQueryReceiptV1,
    MetadataRecordReceiptV1,
    MetadataSpanPreimageV1,
    NormalizedMetadataArtifactV1,
    NormalizedMetadataFieldV1,
    PhysicalHopReceiptV1,
    ResearchRankingV1,
    SourceReceiptBundleV1,
    SystemConfigV1,
    TerminalRunResultV1,
    Top5ProjectionV1,
)

RuntimeArtifactV1: TypeAlias = (
    SystemConfigV1
    | ResearchRankingV1
    | PhysicalHopReceiptV1
    | CacheInventoryEntryV1
    | LogicalQueryReceiptV1
    | LogicalPageReceiptV1
    | NormalizedMetadataFieldV1
    | NormalizedMetadataArtifactV1
    | MetadataRecordReceiptV1
    | MetadataSpanPreimageV1
    | EvidenceLinkReceiptV1
    | SourceReceiptBundleV1
    | LlmInvocationReceiptV1
    | LocalModelInvocationReceiptV1
    | TerminalRunResultV1
    | Top5ProjectionV1
    | ModelNativeReasoningWorkItemV1
    | ModelNativeReasoningResponseV1
    | ModelNativeReasoningReceiptV1
    | ArmExecutionTraceV1
)


class RuntimeArtifactKind(StrEnum):
    """Whitelist of addressed runtime roots accepted at production ingress.

    Values intentionally equal the corresponding frozen ``schema_version``.
    A payload cannot therefore claim one kind while carrying another schema.
    """

    SYSTEM_CONFIG = "flatband-system-config-v1"
    RESEARCH_RANKING = "flatband-research-ranking-v1"
    PHYSICAL_HOP_RECEIPT = "flatband-physical-hop-receipt-v1"
    CACHE_INVENTORY_ENTRY = "flatband-cache-inventory-entry-v1"
    LOGICAL_QUERY_RECEIPT = "flatband-logical-query-receipt-v1"
    LOGICAL_PAGE_RECEIPT = "flatband-logical-page-receipt-v1"
    NORMALIZED_METADATA_FIELD = "flatband-normalized-metadata-field-v1"
    NORMALIZED_METADATA_ARTIFACT = "flatband-normalized-metadata-artifact-v1"
    METADATA_RECORD_RECEIPT = "flatband-metadata-record-receipt-v1"
    METADATA_SPAN_PREIMAGE = "flatband-metadata-span-preimage-v1"
    EVIDENCE_LINK_RECEIPT = "flatband-evidence-link-receipt-v1"
    SOURCE_RECEIPT_BUNDLE = "flatband-source-receipt-bundle-v1"
    LLM_INVOCATION_RECEIPT = "flatband-llm-invocation-receipt-v1"
    LOCAL_MODEL_INVOCATION_RECEIPT = (
        "flatband-local-model-invocation-receipt-v1"
    )
    TERMINAL_RUN_RESULT = "flatband-terminal-run-result-v1"
    TOP5_PROJECTION = "flatband-top5-projection-v1"
    MODEL_NATIVE_WORK_ITEM = "flatband-model-native-work-item-v1"
    MODEL_NATIVE_RESPONSE = "flatband-model-native-response-v1"
    MODEL_NATIVE_RECEIPT = "flatband-model-native-receipt-v1"
    ARM_EXECUTION_TRACE = "flatband-arm-execution-trace-v1"


@dataclass(frozen=True, slots=True)
class _IngressSpec:
    model_type: type[StrictModel]
    id_field: str
    sha_field: str
    prefix: str


_SPEC_BY_KIND: dict[RuntimeArtifactKind, _IngressSpec] = {
    RuntimeArtifactKind.SYSTEM_CONFIG: _IngressSpec(
        SystemConfigV1,
        "config_id",
        "config_sha256",
        "system-config",
    ),
    RuntimeArtifactKind.RESEARCH_RANKING: _IngressSpec(
        ResearchRankingV1,
        "ranking_id",
        "ranking_sha256",
        "research-ranking",
    ),
    RuntimeArtifactKind.PHYSICAL_HOP_RECEIPT: _IngressSpec(
        PhysicalHopReceiptV1,
        "hop_receipt_id",
        "hop_receipt_sha256",
        "physical-hop-receipt",
    ),
    RuntimeArtifactKind.CACHE_INVENTORY_ENTRY: _IngressSpec(
        CacheInventoryEntryV1,
        "cache_entry_id",
        "cache_entry_sha256",
        "cache-inventory-entry",
    ),
    RuntimeArtifactKind.LOGICAL_QUERY_RECEIPT: _IngressSpec(
        LogicalQueryReceiptV1,
        "query_receipt_id",
        "query_receipt_sha256",
        "logical-query-receipt",
    ),
    RuntimeArtifactKind.LOGICAL_PAGE_RECEIPT: _IngressSpec(
        LogicalPageReceiptV1,
        "page_receipt_id",
        "page_receipt_sha256",
        "logical-page-receipt",
    ),
    RuntimeArtifactKind.NORMALIZED_METADATA_FIELD: _IngressSpec(
        NormalizedMetadataFieldV1,
        "field_artifact_id",
        "field_artifact_sha256",
        "metadata-field-artifact",
    ),
    RuntimeArtifactKind.NORMALIZED_METADATA_ARTIFACT: _IngressSpec(
        NormalizedMetadataArtifactV1,
        "artifact_id",
        "artifact_sha256",
        "normalized-metadata-artifact",
    ),
    RuntimeArtifactKind.METADATA_RECORD_RECEIPT: _IngressSpec(
        MetadataRecordReceiptV1,
        "record_receipt_id",
        "record_receipt_sha256",
        "metadata-record-receipt",
    ),
    RuntimeArtifactKind.METADATA_SPAN_PREIMAGE: _IngressSpec(
        MetadataSpanPreimageV1,
        "span_preimage_id",
        "span_preimage_sha256",
        "metadata-span-preimage",
    ),
    RuntimeArtifactKind.EVIDENCE_LINK_RECEIPT: _IngressSpec(
        EvidenceLinkReceiptV1,
        "evidence_receipt_id",
        "evidence_receipt_sha256",
        "evidence-link-receipt",
    ),
    RuntimeArtifactKind.SOURCE_RECEIPT_BUNDLE: _IngressSpec(
        SourceReceiptBundleV1,
        "receipt_bundle_id",
        "receipt_bundle_sha256",
        "source-receipt-bundle",
    ),
    RuntimeArtifactKind.LLM_INVOCATION_RECEIPT: _IngressSpec(
        LlmInvocationReceiptV1,
        "invocation_id",
        "invocation_sha256",
        "llm-invocation-receipt",
    ),
    RuntimeArtifactKind.LOCAL_MODEL_INVOCATION_RECEIPT: _IngressSpec(
        LocalModelInvocationReceiptV1,
        "invocation_id",
        "invocation_sha256",
        "local-model-invocation-receipt",
    ),
    RuntimeArtifactKind.TERMINAL_RUN_RESULT: _IngressSpec(
        TerminalRunResultV1,
        "terminal_result_id",
        "terminal_result_sha256",
        "terminal-result",
    ),
    RuntimeArtifactKind.TOP5_PROJECTION: _IngressSpec(
        Top5ProjectionV1,
        "projection_id",
        "projection_sha256",
        "top5-projection",
    ),
    RuntimeArtifactKind.MODEL_NATIVE_WORK_ITEM: _IngressSpec(
        ModelNativeReasoningWorkItemV1,
        "work_item_id",
        "work_item_sha256",
        "model-native-work-item",
    ),
    RuntimeArtifactKind.MODEL_NATIVE_RESPONSE: _IngressSpec(
        ModelNativeReasoningResponseV1,
        "response_id",
        "response_sha256",
        "model-native-response",
    ),
    RuntimeArtifactKind.MODEL_NATIVE_RECEIPT: _IngressSpec(
        ModelNativeReasoningReceiptV1,
        "receipt_id",
        "receipt_sha256",
        "model-native-receipt",
    ),
    RuntimeArtifactKind.ARM_EXECUTION_TRACE: _IngressSpec(
        ArmExecutionTraceV1,
        "trace_id",
        "trace_sha256",
        "arm-execution-trace",
    ),
}

SUPPORTED_RUNTIME_ARTIFACT_KINDS_V1: tuple[str, ...] = tuple(
    item.value for item in RuntimeArtifactKind
)


def _reject_json_constant(value: str) -> object:
    raise ValueError(f"non-finite JSON number is forbidden: {value}")


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON object key is forbidden: {key}")
        result[key] = value
    return result


def _parse_json_object(
    payload: str | bytes | bytearray | Mapping[str, object],
) -> dict[str, object]:
    """Return a plain JSON object, rejecting model instances and extensions."""

    if isinstance(payload, Mapping):
        # Round-tripping without a custom encoder proves that every value is
        # ordinary JSON data.  In particular, unvalidated ``model_copy``
        # instances cannot cross this boundary as trusted typed objects.
        try:
            encoded = json.dumps(
                dict(payload),
                ensure_ascii=False,
                allow_nan=False,
                separators=(",", ":"),
            )
        except (TypeError, ValueError) as exc:
            raise TypeError("runtime ingress payload must contain plain JSON values") from exc
    elif isinstance(payload, str):
        encoded = payload
    elif isinstance(payload, (bytes, bytearray)):
        try:
            encoded = bytes(payload).decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ValueError("runtime ingress payload must be valid UTF-8 JSON") from exc
    else:
        raise TypeError("runtime ingress payload must be a JSON object or JSON text")

    try:
        document = json.loads(
            encoded,
            object_pairs_hook=_unique_object,
            parse_constant=_reject_json_constant,
        )
    except json.JSONDecodeError as exc:
        raise ValueError("runtime ingress payload is not valid JSON") from exc
    if not isinstance(document, dict):
        raise ValueError("runtime ingress payload root must be a JSON object")  # noqa: TRY004
    return document


def _ingest_with_spec(
    spec: _IngressSpec,
    document: dict[str, object],
) -> RuntimeArtifactV1:
    forbidden = {spec.id_field, spec.sha_field}
    supplied = forbidden & set(document)
    if supplied:
        raise ValueError(
            "runtime ingress payload cannot choose content identity fields: "
            + ",".join(sorted(supplied))
        )

    model_fields = spec.model_type.model_fields
    unknown = set(document) - set(model_fields)
    if unknown:
        raise ValueError(
            "runtime ingress payload contains unknown fields: "
            + ",".join(sorted(unknown))
        )

    typed_values: dict[str, object] = {}
    for field_name, field in model_fields.items():
        if field_name in forbidden:
            continue
        if field_name not in document:
            if field.is_required():
                raise ValueError(
                    f"runtime ingress payload is missing required field: {field_name}"
                )
            typed_values[field_name] = field.get_default(call_default_factory=True)
            continue

        # JSON-mode validation admits the serialized forms of strict enums and
        # tuples while retaining all nested StrictModel constraints.
        field_json = json.dumps(
            document[field_name],
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
        )
        typed_values[field_name] = TypeAdapter(
            field.rebuild_annotation()
        ).validate_json(field_json)

    draft = spec.model_type.model_construct(**typed_values)
    semantic = draft.model_dump(
        mode="python", exclude={spec.id_field, spec.sha_field}
    )
    digest = canonical_sha256(semantic)
    addressed = {
        **typed_values,
        spec.sha_field: digest,
        spec.id_field: deterministic_id(
            spec.prefix, {spec.sha_field: digest}
        ),
    }
    # This is the trust boundary: every field and every nested artifact is
    # revalidated, including instances that could have originated via
    # Pydantic's non-validating ``model_copy``.
    return spec.model_type.model_validate(addressed)


def ingest_runtime_artifact_v1(
    kind: RuntimeArtifactKind | str,
    payload: str | bytes | bytearray | Mapping[str, object],
) -> RuntimeArtifactV1:
    """Parse, content-address, and fully validate one runtime artifact.

    ``kind`` is a closed whitelist whose values equal the target contract's
    ``schema_version``.  ``payload`` must contain semantic JSON fields only;
    the root ID and SHA fields are rejected rather than ignored.
    """

    try:
        normalized_kind = RuntimeArtifactKind(kind)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"unsupported runtime artifact kind: {kind!r}") from exc
    document = _parse_json_object(payload)
    return _ingest_with_spec(_SPEC_BY_KIND[normalized_kind], document)
