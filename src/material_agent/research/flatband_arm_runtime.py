"""Auditable, model-native runtime contracts for flat-band research arms.

This module is an execution *contract*, not an executor.  It performs no
network access and does not run either a hosted or local model.  A runner may
use the builders below to seal the exact visible JSON exchanged with a model,
then join that exchange to the existing research ranking and packet contracts.

The contract deliberately stores no chain-of-thought.  It records only the
strict visible request/response JSON, exact UTF-8 byte counts, content hashes,
one-call receipts, and optional provider-reported token counts.  ``UNAVAILABLE``
is the only provider-attestation value because no signed provider evidence is
available in the current workflow.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from datetime import datetime
from enum import StrEnum
from typing import Annotated, Literal, TypeVar

from pydantic import Field, field_validator, model_validator

from material_agent.inspiration.models import (
    Identifier,
    Sha256,
    StrictModel,
    canonical_sha256,
    deterministic_id,
)
from material_agent.research.flatband_contracts import HypothesisPacketV1
from material_agent.research.flatband_execution import (
    LlmExecutionIdentityV1,
    LocalModelInvocationReceiptV1,
    MetadataModelInputRefV1,
    ResearchRankingV1,
    ResearchSystemId,
    SystemConfigV1,
)


class ModelNativeResponseStatus(StrEnum):
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"


class ArmExecutionScope(StrEnum):
    PILOT = "PILOT"
    DEVELOPMENT = "DEVELOPMENT"
    PROMOTION = "PROMOTION"
    LOCKED = "LOCKED"
    LOCAL_SENSITIVITY = "LOCAL_SENSITIVITY"


class ArmDerivationKind(StrEnum):
    B0_BASELINE_LEXICAL_TAG = "B0_BASELINE_LEXICAL_TAG"
    E1_MODEL_NATIVE_REASONING = "E1_MODEL_NATIVE_REASONING"
    E1_LOCAL_SENSITIVITY = "E1_LOCAL_SENSITIVITY"
    E2_A_MULTI_SOURCE = "E2_A_MULTI_SOURCE"
    E2_B_MULTI_SOURCE = "E2_B_MULTI_SOURCE"
    E3_CROSS_DOMAIN_TAG_GRAPH = "E3_CROSS_DOMAIN_TAG_GRAPH"
    FUSION_FROZEN_COMPONENTS = "FUSION_FROZEN_COMPONENTS"


_DERIVATION_BY_SYSTEM = {
    ResearchSystemId.B0: ArmDerivationKind.B0_BASELINE_LEXICAL_TAG,
    ResearchSystemId.E1: ArmDerivationKind.E1_MODEL_NATIVE_REASONING,
    ResearchSystemId.E1_LOCAL: ArmDerivationKind.E1_LOCAL_SENSITIVITY,
    ResearchSystemId.E2_A: ArmDerivationKind.E2_A_MULTI_SOURCE,
    ResearchSystemId.E2_B: ArmDerivationKind.E2_B_MULTI_SOURCE,
    ResearchSystemId.E3: ArmDerivationKind.E3_CROSS_DOMAIN_TAG_GRAPH,
    ResearchSystemId.FUSION: ArmDerivationKind.FUSION_FROZEN_COMPONENTS,
}

_FORBIDDEN_REASONING_KEYS = frozenset(
    {
        "analysis",
        "chainofthought",
        "cot",
        "hiddenreasoning",
        "internalmonologue",
        "reasoningtrace",
        "scratchpad",
    }
)
_MAX_VISIBLE_JSON_BYTES = 1_000_000


def _require_timestamp(value: str) -> str:
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise ValueError("timestamp must be RFC3339-compatible") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("timestamp must include a UTC offset")
    return value


def _timestamp(value: str) -> datetime:
    return datetime.fromisoformat(value)


def _utf8_sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _reject_duplicate_pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"strict JSON repeats object key: {key}")
        result[key] = value
    return result


def _reject_json_constant(value: str) -> object:
    raise ValueError(f"strict JSON forbids non-finite constant: {value}")


def _strict_json_object(value: str, *, label: str) -> dict[str, object]:
    encoded = value.encode("utf-8")
    if not encoded or len(encoded) > _MAX_VISIBLE_JSON_BYTES:
        raise ValueError(f"{label} exceeds its exact UTF-8 byte bound")
    try:
        parsed = json.loads(
            value,
            object_pairs_hook=_reject_duplicate_pairs,
            parse_constant=_reject_json_constant,
        )
    except (json.JSONDecodeError, UnicodeError) as exc:
        raise ValueError(f"{label} must be strict UTF-8 JSON") from exc
    if not isinstance(parsed, dict):
        raise ValueError(f"{label} must be a top-level JSON object")  # noqa: TRY004
    _assert_no_reasoning_trace(parsed)
    return parsed


def _assert_no_reasoning_trace(value: object) -> None:
    if isinstance(value, dict):
        for key, nested in value.items():
            normalized = re.sub(r"[^a-z0-9]", "", key.lower())
            if normalized in _FORBIDDEN_REASONING_KEYS:
                raise ValueError("visible JSON must not store chain-of-thought fields")
            _assert_no_reasoning_trace(nested)
    elif isinstance(value, list):
        for nested in value:
            _assert_no_reasoning_trace(nested)


def _canonical_json_text(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def _require_exact_keys(
    value: Mapping[str, object], expected: frozenset[str], *, label: str
) -> None:
    if frozenset(value) != expected:
        raise ValueError(f"{label} keys differ from the frozen strict-JSON schema")


ModelT = TypeVar("ModelT", bound=StrictModel)


def _revalidate(value: ModelT, model_type: type[ModelT]) -> ModelT:
    """Revalidate serialized content, including adversarial ``model_copy`` data."""

    return model_type.model_validate(
        value.model_dump(mode="python", round_trip=True)
    )


def _identity_values(
    model: StrictModel, *, id_field: str, sha_field: str, prefix: str
) -> tuple[str, str]:
    semantic = model.model_dump(mode="python", exclude={id_field, sha_field})
    digest = canonical_sha256(semantic)
    return deterministic_id(prefix, {sha_field: digest}), digest


def _assert_identity(
    model: StrictModel, *, id_field: str, sha_field: str, prefix: str
) -> None:
    identifier, digest = _identity_values(
        model, id_field=id_field, sha_field=sha_field, prefix=prefix
    )
    if getattr(model, sha_field) != digest:
        raise ValueError(f"{sha_field} does not match semantic content")
    if getattr(model, id_field) != identifier:
        raise ValueError(f"{id_field} does not match {sha_field}")


def _build_identified(
    model_type: type[ModelT],
    *,
    id_field: str,
    sha_field: str,
    prefix: str,
    values: dict[str, object],
) -> ModelT:
    draft = model_type.model_construct(**values)
    identifier, digest = _identity_values(
        draft, id_field=id_field, sha_field=sha_field, prefix=prefix
    )
    return model_type.model_validate(
        {**values, id_field: identifier, sha_field: digest}
    )


class ModelNativeRankedOutputV1(StrictModel):
    """One visible model output; the complete packet is the output preimage."""

    selection_rank: Annotated[int, Field(ge=1, le=5)]
    packet: HypothesisPacketV1

    @model_validator(mode="after")
    def validate_output(self) -> ModelNativeRankedOutputV1:
        _revalidate(self.packet, HypothesisPacketV1)
        return self


class ModelNativeReasoningWorkItemV1(StrictModel):
    """Exact visible request envelope for one model-native reasoning call."""

    schema_version: Literal["flatband-model-native-work-item-v1"] = (
        "flatband-model-native-work-item-v1"
    )
    work_item_id: Identifier
    work_item_sha256: Sha256
    budget_manifest_id: Identifier
    budget_manifest_sha256: Sha256
    cell_id: Identifier
    run_id: Identifier
    case_id: Identifier
    case_sha256: Sha256
    system_config_id: Identifier
    system_config_sha256: Sha256
    call_index: Annotated[int, Field(ge=1, le=2)]
    model_identity: LlmExecutionIdentityV1
    metadata_inputs: Annotated[
        tuple[MetadataModelInputRefV1, ...], Field(min_length=1, max_length=20)
    ]
    visible_request_json_utf8: Annotated[
        str, Field(min_length=2, max_length=_MAX_VISIBLE_JSON_BYTES)
    ]
    visible_request_json_sha256: Sha256
    exact_request_utf8_bytes: Annotated[
        int, Field(ge=2, le=_MAX_VISIBLE_JSON_BYTES)
    ]
    created_at: Annotated[str, Field(min_length=20, max_length=40)]
    chain_of_thought_stored: Literal[False] = False
    scientific_conclusion: Literal[False] = False

    @field_validator("visible_request_json_utf8", mode="before")
    @classmethod
    def reject_edge_whitespace(cls, value: object) -> object:
        if isinstance(value, str) and value != value.strip():
            raise ValueError("visible request JSON cannot have stripped edge bytes")
        return value

    @field_validator("created_at")
    @classmethod
    def validate_created_at(cls, value: str) -> str:
        return _require_timestamp(value)

    @model_validator(mode="after")
    def validate_work_item(self) -> ModelNativeReasoningWorkItemV1:
        model_identity = _revalidate(
            self.model_identity, LlmExecutionIdentityV1
        )
        inputs = tuple(
            _revalidate(item, MetadataModelInputRefV1)
            for item in self.metadata_inputs
        )
        keys = tuple((item.source_id, item.record_receipt_id) for item in inputs)
        if keys != tuple(sorted(set(keys))):
            raise ValueError("model metadata inputs must be source/record sorted and unique")

        encoded = self.visible_request_json_utf8.encode("utf-8")
        if self.exact_request_utf8_bytes != len(encoded):
            raise ValueError("request byte count differs from exact visible UTF-8 JSON")
        if self.visible_request_json_sha256 != _utf8_sha256(
            self.visible_request_json_utf8
        ):
            raise ValueError("request SHA differs from exact visible UTF-8 JSON")
        document = _strict_json_object(
            self.visible_request_json_utf8, label="visible request"
        )
        _require_exact_keys(
            document,
            frozenset({"schema_version", "binding", "task"}),
            label="visible request",
        )
        if document["schema_version"] != "flatband-model-native-visible-request-v1":
            raise ValueError("visible request schema version is not frozen")
        binding = document["binding"]
        if not isinstance(binding, dict) or not isinstance(document["task"], dict):
            raise ValueError("visible request binding and task must be JSON objects")  # noqa: TRY004
        expected_binding = {
            "budget_manifest_id": self.budget_manifest_id,
            "budget_manifest_sha256": self.budget_manifest_sha256,
            "call_index": self.call_index,
            "case_id": self.case_id,
            "case_sha256": self.case_sha256,
            "cell_id": self.cell_id,
            "metadata_inputs": [item.model_dump(mode="json") for item in inputs],
            "model_identity": model_identity.model_dump(mode="json"),
            "output_schema_sha256": model_identity.output_schema_sha256,
            "prompt_sha256": model_identity.prompt_sha256,
            "run_id": self.run_id,
            "system_config_id": self.system_config_id,
            "system_config_sha256": self.system_config_sha256,
        }
        if binding != expected_binding:
            raise ValueError(
                "visible request does not exactly bind model/prompt/schema/metadata inputs"
            )
        _assert_identity(
            self,
            id_field="work_item_id",
            sha_field="work_item_sha256",
            prefix="model-native-work-item",
        )
        return self


class ModelNativeReasoningResponseV1(StrictModel):
    """Exact visible strict-JSON response; no hidden reasoning is retained."""

    schema_version: Literal["flatband-model-native-response-v1"] = (
        "flatband-model-native-response-v1"
    )
    response_id: Identifier
    response_sha256: Sha256
    work_item_id: Identifier
    work_item_sha256: Sha256
    call_index: Annotated[int, Field(ge=1, le=2)]
    status: ModelNativeResponseStatus
    ranked_outputs: Annotated[
        tuple[ModelNativeRankedOutputV1, ...], Field(max_length=5)
    ] = ()
    failure_reason_code: Identifier | None = None
    visible_response_json_utf8: Annotated[
        str, Field(min_length=2, max_length=_MAX_VISIBLE_JSON_BYTES)
    ]
    visible_response_json_sha256: Sha256
    exact_response_utf8_bytes: Annotated[
        int, Field(ge=2, le=_MAX_VISIBLE_JSON_BYTES)
    ]
    completed_at: Annotated[str, Field(min_length=20, max_length=40)]
    chain_of_thought_stored: Literal[False] = False
    scientific_conclusion: Literal[False] = False

    @field_validator("visible_response_json_utf8", mode="before")
    @classmethod
    def reject_edge_whitespace(cls, value: object) -> object:
        if isinstance(value, str) and value != value.strip():
            raise ValueError("visible response JSON cannot have stripped edge bytes")
        return value

    @field_validator("completed_at")
    @classmethod
    def validate_completed_at(cls, value: str) -> str:
        return _require_timestamp(value)

    @model_validator(mode="after")
    def validate_response(self) -> ModelNativeReasoningResponseV1:
        outputs = tuple(
            _revalidate(item, ModelNativeRankedOutputV1)
            for item in self.ranked_outputs
        )
        ranks = tuple(item.selection_rank for item in outputs)
        if ranks != tuple(sorted(set(ranks))):
            raise ValueError("model output ranks must be sorted and unique")
        packet_ids = tuple(item.packet.packet_id for item in outputs)
        if len(packet_ids) != len(set(packet_ids)):
            raise ValueError("one model response cannot repeat a packet")
        if self.status is ModelNativeResponseStatus.SUCCEEDED:
            if not outputs or self.failure_reason_code is not None:
                raise ValueError("successful model response requires outputs and no failure")
        elif outputs or self.failure_reason_code is None:
            raise ValueError("failed model response requires a reason and no outputs")

        encoded = self.visible_response_json_utf8.encode("utf-8")
        if self.exact_response_utf8_bytes != len(encoded):
            raise ValueError("response byte count differs from exact visible UTF-8 JSON")
        if self.visible_response_json_sha256 != _utf8_sha256(
            self.visible_response_json_utf8
        ):
            raise ValueError("response SHA differs from exact visible UTF-8 JSON")
        document = _strict_json_object(
            self.visible_response_json_utf8, label="visible response"
        )
        _require_exact_keys(
            document,
            frozenset(
                {
                    "schema_version",
                    "work_item_id",
                    "work_item_sha256",
                    "call_index",
                    "status",
                    "ranked_outputs",
                    "failure_reason_code",
                }
            ),
            label="visible response",
        )
        expected = {
            "schema_version": "flatband-model-native-visible-response-v1",
            "work_item_id": self.work_item_id,
            "work_item_sha256": self.work_item_sha256,
            "call_index": self.call_index,
            "status": self.status.value,
            "ranked_outputs": [item.model_dump(mode="json") for item in outputs],
            "failure_reason_code": self.failure_reason_code,
        }
        if document != expected:
            raise ValueError("visible response differs from its typed strict-JSON output")
        _assert_identity(
            self,
            id_field="response_id",
            sha_field="response_sha256",
            prefix="model-native-response",
        )
        return self


class ModelNativeTokenUsageV1(StrictModel):
    """Optional usage only; byte counts and call count remain mandatory."""

    source: Literal["PROVIDER_REPORTED"] = "PROVIDER_REPORTED"
    input_tokens: Annotated[int, Field(ge=0, le=12_000)]
    output_tokens: Annotated[int, Field(ge=0, le=16_000)]


class ModelNativeReasoningReceiptV1(StrictModel):
    """One internally replayable call, with honest unavailable attestation."""

    schema_version: Literal["flatband-model-native-receipt-v1"] = (
        "flatband-model-native-receipt-v1"
    )
    receipt_id: Identifier
    receipt_sha256: Sha256
    work_item: ModelNativeReasoningWorkItemV1
    response: ModelNativeReasoningResponseV1
    exact_provider_call_count: Literal[1] = 1
    exact_request_utf8_bytes: Annotated[
        int, Field(ge=2, le=_MAX_VISIBLE_JSON_BYTES)
    ]
    exact_response_utf8_bytes: Annotated[
        int, Field(ge=2, le=_MAX_VISIBLE_JSON_BYTES)
    ]
    exact_request_json_sha256: Sha256
    exact_response_json_sha256: Sha256
    token_usage: ModelNativeTokenUsageV1 | None = None
    started_at: Annotated[str, Field(min_length=20, max_length=40)]
    completed_at: Annotated[str, Field(min_length=20, max_length=40)]
    provider_attestation: Literal["UNAVAILABLE"] = "UNAVAILABLE"
    chain_of_thought_stored: Literal[False] = False
    scientific_conclusion: Literal[False] = False

    @field_validator("started_at", "completed_at")
    @classmethod
    def validate_timestamp(cls, value: str) -> str:
        return _require_timestamp(value)

    @model_validator(mode="after")
    def validate_receipt(self) -> ModelNativeReasoningReceiptV1:
        work = _revalidate(self.work_item, ModelNativeReasoningWorkItemV1)
        response = _revalidate(self.response, ModelNativeReasoningResponseV1)
        if (
            response.work_item_id,
            response.work_item_sha256,
            response.call_index,
        ) != (work.work_item_id, work.work_item_sha256, work.call_index):
            raise ValueError("model response binds a foreign work item")
        if (
            self.exact_request_utf8_bytes,
            self.exact_request_json_sha256,
            self.exact_response_utf8_bytes,
            self.exact_response_json_sha256,
        ) != (
            work.exact_request_utf8_bytes,
            work.visible_request_json_sha256,
            response.exact_response_utf8_bytes,
            response.visible_response_json_sha256,
        ):
            raise ValueError("receipt byte/hash accounting does not replay exact visible JSON")
        if _timestamp(self.started_at) < _timestamp(work.created_at):
            raise ValueError("model call starts before its work item exists")
        if _timestamp(self.completed_at) < _timestamp(self.started_at):
            raise ValueError("model call completes before it starts")
        if self.completed_at != response.completed_at:
            raise ValueError("receipt completion must exactly equal response completion")
        if self.token_usage is not None:
            _revalidate(self.token_usage, ModelNativeTokenUsageV1)
        _assert_identity(
            self,
            id_field="receipt_id",
            sha_field="receipt_sha256",
            prefix="model-native-receipt",
        )
        return self


class ArtifactIdentityRefV1(StrictModel):
    artifact_id: Identifier
    artifact_sha256: Sha256


class ArmComponentTraceRefV1(StrictModel):
    system_id: ResearchSystemId
    trace_id: Identifier
    trace_sha256: Sha256
    system_config_id: Identifier
    system_config_sha256: Sha256
    ranking_id: Identifier
    ranking_sha256: Sha256
    case_id: Identifier
    case_sha256: Sha256
    scope: ArmExecutionScope
    assembled_at: Annotated[str, Field(min_length=20, max_length=40)]

    @field_validator("assembled_at")
    @classmethod
    def validate_assembled_at(cls, value: str) -> str:
        return _require_timestamp(value)

    @model_validator(mode="after")
    def validate_component(self) -> ArmComponentTraceRefV1:
        if self.system_id not in {
            ResearchSystemId.E1,
            ResearchSystemId.E2_A,
            ResearchSystemId.E2_B,
            ResearchSystemId.E3,
        }:
            raise ValueError("Fusion component must be an isolated non-local arm")
        if self.scope not in {
            ArmExecutionScope.DEVELOPMENT,
            ArmExecutionScope.LOCKED,
        }:
            raise ValueError(
                "Fusion components must come from development or locked traces"
            )
        return self


class RankedPacketDerivationV1(StrictModel):
    """One exact derivation row for one final ranking position."""

    selection_rank: Annotated[int, Field(ge=1, le=5)]
    packet_id: Identifier
    packet_sha256: Sha256
    derivation_kind: ArmDerivationKind
    source_ids: Annotated[tuple[Identifier, ...], Field(min_length=1, max_length=4)]
    baseline_tag_graph_sha256: Sha256
    cross_domain_tag_graph_sha256: Sha256 | None = None
    model_native_receipt: ArtifactIdentityRefV1 | None = None
    local_model_invocations: Annotated[
        tuple[ArtifactIdentityRefV1, ...], Field(max_length=128)
    ] = ()
    fusion_component_traces: Annotated[
        tuple[ArtifactIdentityRefV1, ...], Field(max_length=4)
    ] = ()


class ArmExecutionTraceV1(StrictModel):
    """Complete derivation closure for one arm/case ranking.

    The trace covers every ranking position with one full
    :class:`HypothesisPacketV1`.  E1 (and a Fusion containing E1) must use the
    model-native receipt path.  ``E1-local`` is accepted only as a separately
    named local-sensitivity trace and can never be a promotion or locked trace.
    """

    schema_version: Literal["flatband-arm-execution-trace-v1"] = (
        "flatband-arm-execution-trace-v1"
    )
    trace_id: Identifier
    trace_sha256: Sha256
    scope: ArmExecutionScope
    system_config: SystemConfigV1
    ranking: ResearchRankingV1
    hypothesis_packets: Annotated[
        tuple[HypothesisPacketV1, ...], Field(max_length=5)
    ] = ()
    model_native_receipts: Annotated[
        tuple[ModelNativeReasoningReceiptV1, ...], Field(max_length=2)
    ] = ()
    local_model_invocation_receipts: Annotated[
        tuple[LocalModelInvocationReceiptV1, ...], Field(max_length=128)
    ] = ()
    fusion_component_trace_refs: Annotated[
        tuple[ArmComponentTraceRefV1, ...], Field(max_length=4)
    ] = ()
    derivations: Annotated[
        tuple[RankedPacketDerivationV1, ...], Field(max_length=5)
    ] = ()
    assembled_at: Annotated[str, Field(min_length=20, max_length=40)]
    provider_attestation: Literal["UNAVAILABLE"] = "UNAVAILABLE"
    chain_of_thought_stored: Literal[False] = False
    scientific_conclusion: Literal[False] = False

    @field_validator("assembled_at")
    @classmethod
    def validate_assembled_at(cls, value: str) -> str:
        return _require_timestamp(value)

    @model_validator(mode="after")
    def validate_trace(self) -> ArmExecutionTraceV1:
        config = _revalidate(self.system_config, SystemConfigV1)
        ranking = _revalidate(self.ranking, ResearchRankingV1)
        packets = tuple(
            _revalidate(item, HypothesisPacketV1)
            for item in self.hypothesis_packets
        )
        model_receipts = tuple(
            _revalidate(item, ModelNativeReasoningReceiptV1)
            for item in self.model_native_receipts
        )
        local_receipts = tuple(
            _revalidate(item, LocalModelInvocationReceiptV1)
            for item in self.local_model_invocation_receipts
        )
        component_refs = tuple(
            _revalidate(item, ArmComponentTraceRefV1)
            for item in self.fusion_component_trace_refs
        )
        derivations = tuple(
            _revalidate(item, RankedPacketDerivationV1)
            for item in self.derivations
        )

        if (
            ranking.system_config_id,
            ranking.system_config_sha256,
        ) != (config.config_id, config.config_sha256):
            raise ValueError("arm ranking binds a foreign system config")
        if config.system_id is ResearchSystemId.E1_LOCAL:
            if self.scope is not ArmExecutionScope.LOCAL_SENSITIVITY:
                raise ValueError("E1-local cannot enter Pilot, promotion, or locked flow")
        elif self.scope is ArmExecutionScope.LOCAL_SENSITIVITY:
            raise ValueError("local-sensitivity scope is reserved for E1-local")
        # A locked Fusion is replayed from same-case locked traces for its
        # selected E1/E2/E3 components.  Those isolated traces are derivation
        # inputs, not additional locked comparisons.  E1-local was rejected
        # above and can therefore never enter this path.

        packet_keys = tuple((item.packet_id, item.packet_sha256) for item in packets)
        if packet_keys != tuple(sorted(set(packet_keys))):
            raise ValueError("trace packets must be ID-sorted and unique")
        if len({item.packet_id for item in packets}) != len(
            {item.packet_sha256 for item in packets}
        ):
            raise ValueError("trace packet IDs and hashes must be one-to-one")
        for packet in packets:
            if (packet.case_id, packet.case_sha256) != (
                ranking.case_id,
                ranking.case_sha256,
            ):
                raise ValueError("trace packet belongs to another case")
        expected_packet_keys = {
            (item.packet_id, item.packet_sha256) for item in ranking.positions
        }
        if set(packet_keys) != expected_packet_keys:
            raise ValueError("trace packets do not exactly cover ranked positions")

        source_ids = tuple(item.source_id for item in config.source_budgets)
        source_set = set(source_ids)
        if source_ids != tuple(sorted(set(source_ids))):
            raise ValueError("arm source budgets are not canonically sorted")
        cross_domain_required = (
            config.system_id is ResearchSystemId.E3
            or (
                config.system_id is ResearchSystemId.FUSION
                and ResearchSystemId.E3 in config.fusion_components
            )
        )
        if cross_domain_required:
            if config.cross_domain_tag_graph_sha256 is None:
                raise ValueError("cross-domain arm is missing its frozen TagGraph")
            if (
                config.cross_domain_tag_graph_sha256
                == config.baseline_tag_graph_sha256
            ):
                raise ValueError("baseline and cross-domain TagGraphs must be isolated")
        elif config.cross_domain_tag_graph_sha256 is not None:
            raise ValueError("baseline arm cannot consume a cross-domain TagGraph")

        model_enabled = config.system_id is ResearchSystemId.E1 or (
            config.system_id is ResearchSystemId.FUSION
            and ResearchSystemId.E1 in config.fusion_components
        )
        if model_enabled and (config.llm is None or not model_receipts):
            raise ValueError("E1 intervention requires model-native reasoning receipts")
        if not model_enabled and model_receipts:
            raise ValueError("non-E1 arm cannot report model-native reasoning")
        if config.system_id is ResearchSystemId.E1 and (
            config.local_semantic_model is not None or local_receipts
        ):
            raise ValueError("E1 is model-native and cannot use a local model")

        if tuple(item.work_item.call_index for item in model_receipts) != tuple(
            range(1, len(model_receipts) + 1)
        ):
            raise ValueError("model-native call indexes must be contiguous from one")
        output_by_rank: dict[int, tuple[ModelNativeReasoningReceiptV1, HypothesisPacketV1]] = {}
        metadata_identity: dict[str, str] = {}
        for receipt in model_receipts:
            work = receipt.work_item
            if (
                work.budget_manifest_id,
                work.budget_manifest_sha256,
                work.cell_id,
                work.run_id,
                work.case_id,
                work.case_sha256,
                work.system_config_id,
                work.system_config_sha256,
            ) != (
                ranking.budget_manifest_id,
                ranking.budget_manifest_sha256,
                ranking.cell_id,
                ranking.run_id,
                ranking.case_id,
                ranking.case_sha256,
                config.config_id,
                config.config_sha256,
            ):
                raise ValueError("model-native receipt binds another ranking cell/run")
            if work.model_identity != config.llm:
                raise ValueError("model-native receipt uses a foreign model identity")
            if _timestamp(receipt.completed_at) > _timestamp(ranking.created_at):
                raise ValueError("model-native response completes after ranking creation")
            for metadata_input in work.metadata_inputs:
                if metadata_input.source_id not in source_set:
                    raise ValueError("model-native input comes from a source outside the arm")
                prior = metadata_identity.setdefault(
                    metadata_input.metadata_packet_id,
                    metadata_input.metadata_packet_sha256,
                )
                if prior != metadata_input.metadata_packet_sha256:
                    raise ValueError("one metadata packet ID aliases multiple hashes")
            for output in receipt.response.ranked_outputs:
                if output.selection_rank in output_by_rank:
                    raise ValueError("model responses overlap one final ranking position")
                output_by_rank[output.selection_rank] = (receipt, output.packet)
        if model_enabled:
            assert config.llm is not None
            if len(model_receipts) > config.llm.max_calls:
                raise ValueError("model-native call count exceeds frozen model identity")
            if len(metadata_identity) > config.llm.metadata_packet_limit:
                raise ValueError("model-native metadata inputs exceed the frozen cap")
            reported_usage = tuple(
                item.token_usage
                for item in model_receipts
                if item.token_usage is not None
            )
            if sum(item.input_tokens for item in reported_usage) > config.llm.max_input_tokens:
                raise ValueError("reported model-native input tokens exceed frozen cap")
            if sum(item.output_tokens for item in reported_usage) > config.llm.max_output_tokens:
                raise ValueError("reported model-native output tokens exceed frozen cap")
            expected_outputs = {
                item.selection_rank: (item.packet_id, item.packet_sha256)
                for item in ranking.positions
            }
            observed_outputs = {
                rank: (packet.packet_id, packet.packet_sha256)
                for rank, (_receipt, packet) in output_by_rank.items()
            }
            if observed_outputs != expected_outputs:
                raise ValueError(
                    "model-native visible outputs do not exactly cover final ranking"
                )

        if config.system_id is ResearchSystemId.E1_LOCAL:
            if config.local_semantic_model is None or not local_receipts:
                raise ValueError("E1-local sensitivity requires local-model receipts")
        elif local_receipts:
            raise ValueError("non-E1-local arm cannot report local-model invocations")
        if tuple(item.invocation_index for item in local_receipts) != tuple(
            range(1, len(local_receipts) + 1)
        ):
            raise ValueError("local-model invocation indexes must be contiguous from one")
        for receipt in local_receipts:
            if (
                receipt.budget_manifest_id,
                receipt.budget_manifest_sha256,
                receipt.cell_id,
                receipt.run_id,
                receipt.system_config_id,
                receipt.system_config_sha256,
            ) != (
                ranking.budget_manifest_id,
                ranking.budget_manifest_sha256,
                ranking.cell_id,
                ranking.run_id,
                config.config_id,
                config.config_sha256,
            ):
                raise ValueError("local-model receipt binds another ranking cell/run")
            local = config.local_semantic_model
            if local is None or (
                receipt.bundle_sha256,
                receipt.tokenizer_sha256,
                receipt.model_card_sha256,
                receipt.license_manifest_sha256,
                receipt.vector_dimension,
            ) != (
                local.bundle_sha256,
                local.tokenizer_sha256,
                local.model_card_sha256,
                local.license_manifest_sha256,
                local.vector_dimension,
            ):
                raise ValueError("local-model receipt uses a foreign model identity")
            if _timestamp(receipt.completed_at) > _timestamp(ranking.created_at):
                raise ValueError("local-model invocation completes after ranking creation")
            if any(item.source_id not in source_set for item in receipt.metadata_inputs):
                raise ValueError("local-model input comes from a source outside the arm")

        component_systems = tuple(item.system_id for item in component_refs)
        if config.system_id is ResearchSystemId.FUSION:
            if component_systems != config.fusion_components:
                raise ValueError("Fusion refs do not exactly cover frozen components")
            for ref in component_refs:
                if ref.scope is not self.scope:
                    raise ValueError(
                        "Fusion parent scope must exactly match every component scope"
                    )
                if (ref.case_id, ref.case_sha256) != (
                    ranking.case_id,
                    ranking.case_sha256,
                ):
                    raise ValueError("Fusion component trace belongs to another case")
                if _timestamp(ref.assembled_at) > _timestamp(self.assembled_at):
                    raise ValueError("Fusion was assembled before a component trace")
        elif component_refs:
            raise ValueError("non-Fusion arm cannot carry component traces")

        expected_derivations = _derive_position_rows(
            config=config,
            ranking=ranking,
            model_output_by_rank=output_by_rank,
            local_receipts=local_receipts,
            component_refs=component_refs,
        )
        if derivations != expected_derivations:
            raise ValueError("ranked derivation rows do not exactly replay the arm")
        if _timestamp(self.assembled_at) < _timestamp(ranking.created_at):
            raise ValueError("arm trace was assembled before its ranking")
        _assert_identity(
            self,
            id_field="trace_id",
            sha_field="trace_sha256",
            prefix="arm-execution-trace",
        )
        return self


def _derive_position_rows(
    *,
    config: SystemConfigV1,
    ranking: ResearchRankingV1,
    model_output_by_rank: Mapping[
        int, tuple[ModelNativeReasoningReceiptV1, HypothesisPacketV1]
    ],
    local_receipts: tuple[LocalModelInvocationReceiptV1, ...],
    component_refs: tuple[ArmComponentTraceRefV1, ...],
) -> tuple[RankedPacketDerivationV1, ...]:
    source_ids = tuple(item.source_id for item in config.source_budgets)
    local_refs = tuple(
        ArtifactIdentityRefV1(
            artifact_id=item.invocation_id,
            artifact_sha256=item.invocation_sha256,
        )
        for item in local_receipts
    )
    component_addresses = tuple(
        ArtifactIdentityRefV1(
            artifact_id=item.trace_id,
            artifact_sha256=item.trace_sha256,
        )
        for item in component_refs
    )
    rows: list[RankedPacketDerivationV1] = []
    for position in ranking.positions:
        receipt_and_packet = model_output_by_rank.get(position.selection_rank)
        model_ref = (
            None
            if receipt_and_packet is None
            else ArtifactIdentityRefV1(
                artifact_id=receipt_and_packet[0].receipt_id,
                artifact_sha256=receipt_and_packet[0].receipt_sha256,
            )
        )
        rows.append(
            RankedPacketDerivationV1(
                selection_rank=position.selection_rank,
                packet_id=position.packet_id,
                packet_sha256=position.packet_sha256,
                derivation_kind=_DERIVATION_BY_SYSTEM[config.system_id],
                source_ids=source_ids,
                baseline_tag_graph_sha256=config.baseline_tag_graph_sha256,
                cross_domain_tag_graph_sha256=(
                    config.cross_domain_tag_graph_sha256
                    if config.system_id is ResearchSystemId.E3
                    or (
                        config.system_id is ResearchSystemId.FUSION
                        and ResearchSystemId.E3 in config.fusion_components
                    )
                    else None
                ),
                model_native_receipt=model_ref,
                local_model_invocations=local_refs,
                fusion_component_traces=component_addresses,
            )
        )
    return tuple(rows)


def build_model_native_reasoning_work_item(
    *,
    budget_manifest_id: str,
    budget_manifest_sha256: str,
    cell_id: str,
    run_id: str,
    case_id: str,
    case_sha256: str,
    system_config: SystemConfigV1,
    call_index: int,
    metadata_inputs: tuple[MetadataModelInputRefV1, ...],
    task: Mapping[str, object],
    created_at: str,
) -> ModelNativeReasoningWorkItemV1:
    """Seal a strict visible request without invoking any model."""

    config = _revalidate(system_config, SystemConfigV1)
    if config.llm is None:
        raise ValueError("model-native work item requires an LLM-enabled config")
    inputs = tuple(
        sorted(
            (_revalidate(item, MetadataModelInputRefV1) for item in metadata_inputs),
            key=lambda item: (item.source_id, item.record_receipt_id),
        )
    )
    if not inputs:
        raise ValueError("model-native work item requires metadata inputs")
    task_object = json.loads(_canonical_json_text(dict(task)))
    if not isinstance(task_object, dict):
        raise ValueError("model-native task must be a JSON object")  # noqa: TRY004
    _assert_no_reasoning_trace(task_object)
    binding = {
        "budget_manifest_id": budget_manifest_id,
        "budget_manifest_sha256": budget_manifest_sha256,
        "call_index": call_index,
        "case_id": case_id,
        "case_sha256": case_sha256,
        "cell_id": cell_id,
        "metadata_inputs": [item.model_dump(mode="json") for item in inputs],
        "model_identity": config.llm.model_dump(mode="json"),
        "output_schema_sha256": config.llm.output_schema_sha256,
        "prompt_sha256": config.llm.prompt_sha256,
        "run_id": run_id,
        "system_config_id": config.config_id,
        "system_config_sha256": config.config_sha256,
    }
    visible = _canonical_json_text(
        {
            "schema_version": "flatband-model-native-visible-request-v1",
            "binding": binding,
            "task": task_object,
        }
    )
    return _build_identified(
        ModelNativeReasoningWorkItemV1,
        id_field="work_item_id",
        sha_field="work_item_sha256",
        prefix="model-native-work-item",
        values={
            "budget_manifest_id": budget_manifest_id,
            "budget_manifest_sha256": budget_manifest_sha256,
            "cell_id": cell_id,
            "run_id": run_id,
            "case_id": case_id,
            "case_sha256": case_sha256,
            "system_config_id": config.config_id,
            "system_config_sha256": config.config_sha256,
            "call_index": call_index,
            "model_identity": config.llm,
            "metadata_inputs": inputs,
            "visible_request_json_utf8": visible,
            "visible_request_json_sha256": _utf8_sha256(visible),
            "exact_request_utf8_bytes": len(visible.encode("utf-8")),
            "created_at": created_at,
        },
    )


def encode_model_native_reasoning_response(
    work_item: ModelNativeReasoningWorkItemV1,
    *,
    status: ModelNativeResponseStatus,
    ranked_outputs: tuple[ModelNativeRankedOutputV1, ...] = (),
    failure_reason_code: str | None = None,
) -> str:
    """Return the strict visible JSON a runner is allowed to persist."""

    work = _revalidate(work_item, ModelNativeReasoningWorkItemV1)
    outputs = tuple(
        _revalidate(item, ModelNativeRankedOutputV1) for item in ranked_outputs
    )
    document = {
        "schema_version": "flatband-model-native-visible-response-v1",
        "work_item_id": work.work_item_id,
        "work_item_sha256": work.work_item_sha256,
        "call_index": work.call_index,
        "status": status.value,
        "ranked_outputs": [item.model_dump(mode="json") for item in outputs],
        "failure_reason_code": failure_reason_code,
    }
    _assert_no_reasoning_trace(document)
    return _canonical_json_text(document)


def _parse_ranked_output(value: object) -> ModelNativeRankedOutputV1:
    try:
        encoded = json.dumps(value, ensure_ascii=False, allow_nan=False)
        return ModelNativeRankedOutputV1.model_validate_json(encoded)
    except (TypeError, ValueError) as exc:
        raise ValueError("visible response ranked output is not schema-valid") from exc


def build_model_native_reasoning_response(
    work_item: ModelNativeReasoningWorkItemV1,
    *,
    visible_response_json_utf8: str,
    completed_at: str,
) -> ModelNativeReasoningResponseV1:
    """Parse and seal one externally produced visible response JSON string."""

    work = _revalidate(work_item, ModelNativeReasoningWorkItemV1)
    document = _strict_json_object(
        visible_response_json_utf8, label="visible response"
    )
    _require_exact_keys(
        document,
        frozenset(
            {
                "schema_version",
                "work_item_id",
                "work_item_sha256",
                "call_index",
                "status",
                "ranked_outputs",
                "failure_reason_code",
            }
        ),
        label="visible response",
    )
    try:
        status = ModelNativeResponseStatus(str(document["status"]))
    except ValueError as exc:
        raise ValueError("visible response status is not registered") from exc
    raw_outputs = document["ranked_outputs"]
    if not isinstance(raw_outputs, list):
        raise ValueError("visible response ranked_outputs must be a JSON array")  # noqa: TRY004
    outputs = tuple(_parse_ranked_output(item) for item in raw_outputs)
    reason = document["failure_reason_code"]
    if reason is not None and not isinstance(reason, str):
        raise ValueError("visible response failure reason must be a string or null")
    return _build_identified(
        ModelNativeReasoningResponseV1,
        id_field="response_id",
        sha_field="response_sha256",
        prefix="model-native-response",
        values={
            "work_item_id": work.work_item_id,
            "work_item_sha256": work.work_item_sha256,
            "call_index": work.call_index,
            "status": status,
            "ranked_outputs": outputs,
            "failure_reason_code": reason,
            "visible_response_json_utf8": visible_response_json_utf8,
            "visible_response_json_sha256": _utf8_sha256(
                visible_response_json_utf8
            ),
            "exact_response_utf8_bytes": len(
                visible_response_json_utf8.encode("utf-8")
            ),
            "completed_at": completed_at,
        },
    )


def build_model_native_reasoning_receipt(
    work_item: ModelNativeReasoningWorkItemV1,
    response: ModelNativeReasoningResponseV1,
    *,
    started_at: str,
    completed_at: str,
    token_usage: ModelNativeTokenUsageV1 | None = None,
) -> ModelNativeReasoningReceiptV1:
    """Seal one exact call receipt; no network/model invocation occurs here."""

    work = _revalidate(work_item, ModelNativeReasoningWorkItemV1)
    parsed = _revalidate(response, ModelNativeReasoningResponseV1)
    usage = (
        None
        if token_usage is None
        else _revalidate(token_usage, ModelNativeTokenUsageV1)
    )
    return _build_identified(
        ModelNativeReasoningReceiptV1,
        id_field="receipt_id",
        sha_field="receipt_sha256",
        prefix="model-native-receipt",
        values={
            "work_item": work,
            "response": parsed,
            "exact_request_utf8_bytes": work.exact_request_utf8_bytes,
            "exact_response_utf8_bytes": parsed.exact_response_utf8_bytes,
            "exact_request_json_sha256": work.visible_request_json_sha256,
            "exact_response_json_sha256": parsed.visible_response_json_sha256,
            "token_usage": usage,
            "started_at": started_at,
            "completed_at": completed_at,
        },
    )


def _component_ref(trace: ArmExecutionTraceV1) -> ArmComponentTraceRefV1:
    validated = _revalidate(trace, ArmExecutionTraceV1)
    return ArmComponentTraceRefV1(
        system_id=validated.system_config.system_id,
        trace_id=validated.trace_id,
        trace_sha256=validated.trace_sha256,
        system_config_id=validated.system_config.config_id,
        system_config_sha256=validated.system_config.config_sha256,
        ranking_id=validated.ranking.ranking_id,
        ranking_sha256=validated.ranking.ranking_sha256,
        case_id=validated.ranking.case_id,
        case_sha256=validated.ranking.case_sha256,
        scope=validated.scope,
        assembled_at=validated.assembled_at,
    )


def build_arm_execution_trace(
    *,
    scope: ArmExecutionScope,
    system_config: SystemConfigV1,
    ranking: ResearchRankingV1,
    hypothesis_packets: tuple[HypothesisPacketV1, ...],
    model_native_receipts: tuple[ModelNativeReasoningReceiptV1, ...] = (),
    local_model_invocation_receipts: tuple[LocalModelInvocationReceiptV1, ...] = (),
    fusion_component_traces: tuple[ArmExecutionTraceV1, ...] = (),
    assembled_at: str,
) -> ArmExecutionTraceV1:
    """Assemble one complete arm trace from already-existing artifacts."""

    config = _revalidate(system_config, SystemConfigV1)
    ranked = _revalidate(ranking, ResearchRankingV1)
    packets = tuple(
        sorted(
            (_revalidate(item, HypothesisPacketV1) for item in hypothesis_packets),
            key=lambda item: item.packet_id,
        )
    )
    model_receipts = tuple(
        sorted(
            (
                _revalidate(item, ModelNativeReasoningReceiptV1)
                for item in model_native_receipts
            ),
            key=lambda item: item.work_item.call_index,
        )
    )
    local_receipts = tuple(
        sorted(
            (
                _revalidate(item, LocalModelInvocationReceiptV1)
                for item in local_model_invocation_receipts
            ),
            key=lambda item: item.invocation_index,
        )
    )
    components = tuple(
        sorted(
            (_component_ref(item) for item in fusion_component_traces),
            key=lambda item: item.system_id.value,
        )
    )
    output_by_rank = {
        output.selection_rank: (receipt, output.packet)
        for receipt in model_receipts
        for output in receipt.response.ranked_outputs
    }
    derivations = _derive_position_rows(
        config=config,
        ranking=ranked,
        model_output_by_rank=output_by_rank,
        local_receipts=local_receipts,
        component_refs=components,
    )
    return _build_identified(
        ArmExecutionTraceV1,
        id_field="trace_id",
        sha_field="trace_sha256",
        prefix="arm-execution-trace",
        values={
            "scope": scope,
            "system_config": config,
            "ranking": ranked,
            "hypothesis_packets": packets,
            "model_native_receipts": model_receipts,
            "local_model_invocation_receipts": local_receipts,
            "fusion_component_trace_refs": components,
            "derivations": derivations,
            "assembled_at": assembled_at,
        },
    )


def assert_arm_execution_trace_exact(
    trace: ArmExecutionTraceV1,
    *,
    fusion_component_traces: tuple[ArmExecutionTraceV1, ...] = (),
) -> None:
    """Deep-replay an arm trace, including full Fusion component artifacts."""

    validated = _revalidate(trace, ArmExecutionTraceV1)
    if validated.system_config.system_id is ResearchSystemId.FUSION:
        if not fusion_component_traces:
            raise ValueError("exact Fusion replay requires full component traces")
        for component in fusion_component_traces:
            assert_arm_execution_trace_exact(component)
    elif fusion_component_traces:
        raise ValueError("non-Fusion exact replay cannot receive component traces")
    rebuilt = build_arm_execution_trace(
        scope=validated.scope,
        system_config=validated.system_config,
        ranking=validated.ranking,
        hypothesis_packets=validated.hypothesis_packets,
        model_native_receipts=validated.model_native_receipts,
        local_model_invocation_receipts=validated.local_model_invocation_receipts,
        fusion_component_traces=fusion_component_traces,
        assembled_at=validated.assembled_at,
    )
    if rebuilt != validated:
        raise ValueError("arm execution trace differs from exact builder replay")


__all__ = [
    "ArmComponentTraceRefV1",
    "ArmDerivationKind",
    "ArmExecutionScope",
    "ArmExecutionTraceV1",
    "ArtifactIdentityRefV1",
    "ModelNativeRankedOutputV1",
    "ModelNativeReasoningReceiptV1",
    "ModelNativeReasoningResponseV1",
    "ModelNativeReasoningWorkItemV1",
    "ModelNativeResponseStatus",
    "ModelNativeTokenUsageV1",
    "RankedPacketDerivationV1",
    "assert_arm_execution_trace_exact",
    "build_arm_execution_trace",
    "build_model_native_reasoning_receipt",
    "build_model_native_reasoning_response",
    "build_model_native_reasoning_work_item",
    "encode_model_native_reasoning_response",
]
