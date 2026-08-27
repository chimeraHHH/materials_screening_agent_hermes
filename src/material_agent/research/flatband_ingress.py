"""Canonical JSON ingress for private human-authored research decisions.

Human reviewers and adjudicators provide semantic fields only.  They never
choose content IDs or SHA-256 values.  These helpers validate strict Pydantic
schemas, canonicalize ordering where the underlying schema requires it, and
derive the address in one place before the artifacts enter the workflow.

This module does not create labels, infer judgments, or persist credentials.
It is a deterministic boundary around human/model-visible structured input.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from datetime import datetime
from typing import Annotated, Literal, TypeVar

from pydantic import Field, TypeAdapter, ValidationError, model_validator

from material_agent.inspiration.models import (
    Identifier,
    Sha256,
    StrictModel,
    canonical_sha256,
    deterministic_id,
)
from material_agent.research.flatband_contracts import (
    SOURCE_CATALOG_V1_SHA256,
    ExpertAdjudicationV1,
    RawExpertAnnotationV1,
)
from material_agent.research.flatband_gold import (
    DuplicatePartitionAdjudicationV2,
    RawDuplicatePartitionV2,
)
from material_agent.research.flatband_source_policy import (
    SourceCatalogDecision,
    SourceUseRole,
)

ModelT = TypeVar("ModelT", bound=StrictModel)
SOURCE_CATALOG_SCHEMA_V1_SHA256 = (
    "a796757159679a02146ad00f306f8bc6db2642fdd3fc51c82b25aba36936efc7"
)
SOURCE_AUDIT_V1_SHA256 = (
    "5574eaa9f70e223a03991ef7f2e5942c38e1afd38898b54243fd4c66b3290660"
)


def _timestamp(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("timestamp must include a UTC offset")
    return parsed


class SourceCatalogRowCommitmentV1(StrictModel):
    source_id: Identifier
    row_sha256: Sha256
    decision: SourceCatalogDecision
    roles: Annotated[tuple[SourceUseRole, ...], Field(min_length=1, max_length=8)]
    license_expressions: Annotated[
        tuple[str, ...], Field(min_length=1, max_length=16)
    ]

    @model_validator(mode="after")
    def validate_row(self) -> SourceCatalogRowCommitmentV1:
        if tuple(item.value for item in self.roles) != tuple(
            sorted({item.value for item in self.roles})
        ):
            raise ValueError("source catalog roles must be sorted and unique")
        if self.license_expressions != tuple(sorted(set(self.license_expressions))):
            raise ValueError("source catalog licenses must be sorted and unique")
        return self


class SourceCatalogCheckpointReleaseV1(StrictModel):
    """Machine-readable checkpoint of the exact audited 20-row catalog."""

    schema_version: Literal["flatband-source-catalog-checkpoint-v1"] = (
        "flatband-source-catalog-checkpoint-v1"
    )
    checkpoint_id: Identifier
    checkpoint_sha256: Sha256
    source_catalog_sha256: Literal[SOURCE_CATALOG_V1_SHA256] = (
        SOURCE_CATALOG_V1_SHA256
    )
    source_catalog_schema_sha256: Literal[SOURCE_CATALOG_SCHEMA_V1_SHA256] = (
        SOURCE_CATALOG_SCHEMA_V1_SHA256
    )
    source_audit_sha256: Literal[SOURCE_AUDIT_V1_SHA256] = SOURCE_AUDIT_V1_SHA256
    rows: Annotated[
        tuple[SourceCatalogRowCommitmentV1, ...], Field(min_length=20, max_length=20)
    ]
    include_count: Literal[12] = 12
    conditional_count: Literal[5] = 5
    exclude_count: Literal[3] = 3
    audited_at: Annotated[str, Field(min_length=20, max_length=40)]
    public_protocol_artifact: Literal[True] = True
    scientific_conclusion: Literal[False] = False

    @model_validator(mode="after")
    def validate_checkpoint(self) -> SourceCatalogCheckpointReleaseV1:
        _timestamp(self.audited_at)
        source_ids = tuple(item.source_id for item in self.rows)
        if source_ids != tuple(sorted(set(source_ids))):
            raise ValueError("source catalog checkpoint rows must be source-ID sorted")
        counts = {
            decision: sum(item.decision is decision for item in self.rows)
            for decision in SourceCatalogDecision
        }
        if counts != {
            SourceCatalogDecision.INCLUDE: self.include_count,
            SourceCatalogDecision.CONDITIONAL: self.conditional_count,
            SourceCatalogDecision.EXCLUDE: self.exclude_count,
        }:
            raise ValueError("source catalog decision counts do not replay")
        semantic = self.model_dump(
            mode="python", exclude={"checkpoint_id", "checkpoint_sha256"}
        )
        digest = canonical_sha256(semantic)
        if self.checkpoint_sha256 != digest:
            raise ValueError("source catalog checkpoint SHA-256 does not match")
        if self.checkpoint_id != deterministic_id(
            "source-catalog-checkpoint-v1", {"checkpoint_sha256": digest}
        ):
            raise ValueError("source catalog checkpoint ID does not match its SHA-256")
        return self


def _ingest_addressed(
    model_type: type[ModelT],
    payload: Mapping[str, object],
    *,
    id_field: str,
    sha_field: str,
    prefix: str,
) -> ModelT:
    values = dict(payload)
    forbidden = {id_field, sha_field}
    supplied = forbidden & set(values)
    if supplied:
        raise ValueError(
            "ingress payload cannot choose content identity fields: "
            + ",".join(sorted(supplied))
        )
    unknown = set(values) - set(model_type.model_fields)
    if unknown:
        raise ValueError(
            "ingress payload contains unknown fields: "
            + ",".join(sorted(unknown))
        )
    typed_values: dict[str, object] = {}
    for name, field in model_type.model_fields.items():
        if name in forbidden:
            continue
        if name in values:
            adapter = TypeAdapter(field.rebuild_annotation())
            try:
                typed_values[name] = adapter.validate_python(values[name])
            except ValidationError:
                # Strict enums deliberately reject Python strings, while JSON
                # ingestion is allowed to decode their serialized values.
                typed_values[name] = adapter.validate_json(
                    json.dumps(values[name], ensure_ascii=False)
                )
        elif field.is_required():
            raise ValueError(f"ingress payload is missing required field: {name}")
        else:
            typed_values[name] = field.get_default(call_default_factory=True)
    # Field-level validation converts JSON strings to the exact enum/model
    # types used by canonical serialization.  Full cross-field validation still
    # happens only on the addressed returned object below.
    draft = model_type.model_construct(**typed_values)
    semantic = draft.model_dump(
        mode="python", exclude={id_field, sha_field}
    )
    digest = canonical_sha256(semantic)
    return model_type.model_validate(
        {
            **typed_values,
            sha_field: digest,
            id_field: deterministic_id(prefix, {sha_field: digest}),
        }
    )


def ingest_raw_expert_annotation_v1(
    payload: Mapping[str, object],
) -> RawExpertAnnotationV1:
    """Address one sealed reviewer annotation from semantic JSON fields."""

    return _ingest_addressed(
        RawExpertAnnotationV1,
        payload,
        id_field="annotation_id",
        sha_field="annotation_sha256",
        prefix="expert-annotation",
    )


def ingest_expert_adjudication_v1(
    payload: Mapping[str, object],
) -> ExpertAdjudicationV1:
    """Address one sealed label adjudication from semantic JSON fields."""

    return _ingest_addressed(
        ExpertAdjudicationV1,
        payload,
        id_field="adjudication_id",
        sha_field="adjudication_sha256",
        prefix="expert-adjudication",
    )


def ingest_raw_duplicate_partition_v2(
    payload: Mapping[str, object],
) -> RawDuplicatePartitionV2:
    """Address one reviewer's private duplicate partition."""

    return _ingest_addressed(
        RawDuplicatePartitionV2,
        payload,
        id_field="partition_id",
        sha_field="partition_sha256",
        prefix="raw-duplicate-partition-v2",
    )


def ingest_duplicate_partition_adjudication_v2(
    payload: Mapping[str, object],
) -> DuplicatePartitionAdjudicationV2:
    """Address one genuine two-reviewer duplicate-partition dispute."""

    return _ingest_addressed(
        DuplicatePartitionAdjudicationV2,
        payload,
        id_field="adjudication_id",
        sha_field="adjudication_sha256",
        prefix="duplicate-adjudication-v2",
    )


def build_source_catalog_checkpoint_release_v1(
    *,
    source_catalog_jsonl: bytes,
    source_catalog_schema_json: bytes,
    source_audit_markdown: bytes,
    audited_at: str,
) -> SourceCatalogCheckpointReleaseV1:
    """Parse and freeze the exact public source-audit machine artifacts."""

    if hashlib.sha256(source_catalog_jsonl).hexdigest() != SOURCE_CATALOG_V1_SHA256:
        raise ValueError("source catalog bytes differ from the frozen catalog")
    if (
        hashlib.sha256(source_catalog_schema_json).hexdigest()
        != SOURCE_CATALOG_SCHEMA_V1_SHA256
    ):
        raise ValueError("source catalog schema bytes differ from the frozen schema")
    if hashlib.sha256(source_audit_markdown).hexdigest() != SOURCE_AUDIT_V1_SHA256:
        raise ValueError("source audit bytes differ from the frozen audit")
    try:
        decoded = source_catalog_jsonl.decode("utf-8")
        raw_rows = tuple(
            json.loads(line) for line in decoded.splitlines() if line.strip()
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("source catalog is not canonical UTF-8 JSONL") from exc
    rows: list[SourceCatalogRowCommitmentV1] = []
    for raw in raw_rows:
        if not isinstance(raw, dict) or raw.get("schema_version") != (
            "flatband-source-catalog-row-v1"
        ):
            raise ValueError("source catalog contains an unknown row schema")
        licenses = raw.get("licenses")
        if not isinstance(licenses, list):
            raise ValueError("source catalog row has no license list")  # noqa: TRY004
        rows.append(
            SourceCatalogRowCommitmentV1(
                source_id=raw.get("source_id"),
                row_sha256=canonical_sha256(raw),
                decision=SourceCatalogDecision(raw.get("decision")),
                roles=tuple(
                    sorted(
                        (SourceUseRole(item) for item in raw.get("roles", ())),
                        key=lambda item: item.value,
                    )
                ),
                license_expressions=tuple(
                    sorted(
                        {
                            str(item["expression"])
                            for item in licenses
                            if isinstance(item, dict) and "expression" in item
                        }
                    )
                ),
            )
        )
    rows.sort(key=lambda item: item.source_id)
    values: dict[str, object] = {
        "source_audit_sha256": SOURCE_AUDIT_V1_SHA256,
        "rows": tuple(rows),
        "audited_at": audited_at,
    }
    draft = SourceCatalogCheckpointReleaseV1.model_construct(**values)
    digest = canonical_sha256(
        draft.model_dump(
            mode="python", exclude={"checkpoint_id", "checkpoint_sha256"}
        )
    )
    return SourceCatalogCheckpointReleaseV1.model_validate(
        {
            **values,
            "checkpoint_sha256": digest,
            "checkpoint_id": deterministic_id(
                "source-catalog-checkpoint-v1",
                {"checkpoint_sha256": digest},
            ),
        }
    )


__all__ = [
    "SOURCE_AUDIT_V1_SHA256",
    "SOURCE_CATALOG_SCHEMA_V1_SHA256",
    "SourceCatalogCheckpointReleaseV1",
    "SourceCatalogRowCommitmentV1",
    "build_source_catalog_checkpoint_release_v1",
    "ingest_duplicate_partition_adjudication_v2",
    "ingest_expert_adjudication_v1",
    "ingest_raw_duplicate_partition_v2",
    "ingest_raw_expert_annotation_v1",
]
