"""Deterministic, bounded passage vectors for the inspiration pipeline.

Only selected :class:`~material_agent.inspiration.models.PassageV1` excerpts
are accepted.  The vectorizer has intentionally no document or full-text
entry point: its embedding input is exactly the hit title, the passage's
section heading, the selected passage text, and normalized tags.
"""

from __future__ import annotations

import hashlib
import math
import re
import struct
import unicodedata
from collections.abc import Iterator, Sequence
from dataclasses import dataclass

from material_agent.inspiration.models import (
    ArtifactPointerV1,
    ComponentSnapshotV1,
    PassageV1,
    PassageVectorV1,
    canonical_json_bytes,
    canonical_sha256,
)
from material_agent.inspiration.policy import EmbeddingBudgetV1


SIGNED_HASHING_VECTORIZER_ID = "signed-hashing-v1"
SIGNED_HASHING_VERSION = "1"
VECTOR_ARTIFACT_MEDIA_TYPE = "application/vnd.material-agent.vector-f32le"

_TOKEN_PATTERN = re.compile(r"[^\W_]+", flags=re.UNICODE)
_WHITESPACE_PATTERN = re.compile(r"\s+", flags=re.UNICODE)
_SAFE_ARTIFACT_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")

_SIGNED_HASHING_SPEC = {
    "algorithm": SIGNED_HASHING_VECTORIZER_ID,
    "version": SIGNED_HASHING_VERSION,
    "embedding_input": (
        "canonical-json:title+section-heading+selected-passage+normalized-tags"
    ),
    "source_text_normalization": "unicode-nfkc+collapsed-whitespace",
    "tag_normalization": "unicode-nfkc+collapsed-whitespace+casefold+sort+dedup",
    "token_normalization": "unicode-nfkc+casefold",
    "unicode_database_version": unicodedata.unidata_version,
    "token_pattern": r"[^\W_]+",
    "field_boundaries": "no-cross-field-bigrams",
    "features": ("field-namespaced-unigram", "field-namespaced-bigram"),
    "feature_hash": "sha256",
    "bucket_bytes": "digest[0:8]-little-endian-mod-dimension",
    "sign_bit": "digest[8]-least-significant-bit",
    "normalization_after_hashing": "l2",
    "artifact_encoding": "ieee754-float32-little-endian",
    "artifact_media_type": VECTOR_ARTIFACT_MEDIA_TYPE,
    "cache_key": (
        "canonical-json:signed-hashing-cache-v1+dimension+input-sha+snapshot"
    ),
    "input_token_budget": "sum-of-unigrams-before-bigram-expansion",
}
SIGNED_HASHING_IMPLEMENTATION_SHA256 = canonical_sha256(_SIGNED_HASHING_SPEC)
SIGNED_HASHING_SNAPSHOT = ComponentSnapshotV1(
    component_id=SIGNED_HASHING_VECTORIZER_ID,
    version=SIGNED_HASHING_VERSION,
    implementation_sha256=SIGNED_HASHING_IMPLEMENTATION_SHA256,
)


class InspirationVectorizationError(ValueError):
    """Fail-closed vectorization error with a stable machine-readable code."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(f"{code}: {message}")


@dataclass(frozen=True, slots=True)
class PassageVectorizationRequest:
    """One selected passage and its bounded metadata-only context."""

    passage: PassageV1
    title: str
    normalized_tags: tuple[str, ...]
    vector_artifact: ArtifactPointerV1 | str


@dataclass(frozen=True, slots=True)
class PassageVectorizationResult:
    """The replayable contract record and the bytes that must be persisted."""

    passage_vector: PassageVectorV1
    artifact_bytes: bytes
    embedding_input_bytes: bytes
    input_token_count: int


@dataclass(frozen=True, slots=True)
class _PreparedEmbeddingInput:
    canonical_bytes: bytes
    token_fields: tuple[tuple[str, tuple[str, ...]], ...]
    token_count: int


def tokenize_signed_hashing_v1(text: str) -> tuple[str, ...]:
    """Tokenize text deterministically for signed hashing v1.

    Unicode is normalized with NFKC and case-folded before sequences of
    Unicode letters or numbers are selected.  Punctuation and underscores are
    boundaries.  The result is independent of locale and process hash seeds.
    """

    if not isinstance(text, str):
        raise InspirationVectorizationError(
            "INVALID_TEXT_TYPE",
            "signed hashing input must be a string",
        )
    normalized = unicodedata.normalize("NFKC", text).casefold()
    return tuple(_TOKEN_PATTERN.findall(normalized))


def build_embedding_input_bytes(
    passage: PassageV1,
    *,
    title: str,
    normalized_tags: Sequence[str],
) -> bytes:
    """Return the canonical, deliberately passage-only embedding input.

    The section heading is derived from ``passage.locator`` so callers cannot
    substitute an unrelated body of text.  Tags are NFKC/casefold normalized,
    deduplicated, and sorted, making their input order semantically irrelevant.
    """

    return _prepare_embedding_input(
        passage,
        title=title,
        normalized_tags=normalized_tags,
    ).canonical_bytes


def vectorize_selected_passages(
    requests: Sequence[PassageVectorizationRequest],
    *,
    budget: EmbeddingBudgetV1,
    vectorizer: ComponentSnapshotV1 = SIGNED_HASHING_SNAPSHOT,
) -> tuple[PassageVectorizationResult, ...]:
    """Vectorize selected passages within one frozen embedding budget.

    Results are returned in ``passage_id`` order so replay is independent of
    request ordering.  The total input-token count is computed with this
    vectorizer's tokenizer rather than trusting an upstream estimate.
    """

    _validate_vectorizer(budget=budget, vectorizer=vectorizer)
    materialized = tuple(requests)
    if len(materialized) > budget.max_passages:
        raise InspirationVectorizationError(
            "MAX_PASSAGES_EXCEEDED",
            f"received {len(materialized)} passages; budget allows "
            f"{budget.max_passages}",
        )

    for request in materialized:
        if not isinstance(request, PassageVectorizationRequest):
            raise InspirationVectorizationError(
                "INVALID_REQUEST",
                "each item must be a PassageVectorizationRequest",
            )
        if not isinstance(request.passage, PassageV1):
            raise InspirationVectorizationError(
                "INVALID_PASSAGE",
                "vectorization accepts only a validated PassageV1 excerpt",
            )

    passage_ids = tuple(request.passage.passage_id for request in materialized)
    if len(set(passage_ids)) != len(passage_ids):
        raise InspirationVectorizationError(
            "DUPLICATE_PASSAGE_ID",
            "each vectorization request must reference a unique passage_id",
        )

    ordered = tuple(sorted(materialized, key=lambda request: request.passage.passage_id))
    prepared = tuple(
        _prepare_embedding_input(
            request.passage,
            title=request.title,
            normalized_tags=request.normalized_tags,
        )
        for request in ordered
    )
    total_input_tokens = sum(item.token_count for item in prepared)
    if total_input_tokens > budget.max_input_tokens:
        raise InspirationVectorizationError(
            "MAX_INPUT_TOKENS_EXCEEDED",
            f"embedding inputs contain {total_input_tokens} tokens; budget "
            f"allows {budget.max_input_tokens}",
        )

    return tuple(
        _vectorize_prepared_passage(
            request,
            embedding_input=embedding_input,
            dimension=budget.vector_dimension,
            vectorizer=vectorizer,
        )
        for request, embedding_input in zip(ordered, prepared, strict=True)
    )


def _normalize_bounded_text(
    value: str,
    *,
    field_name: str,
    max_length: int,
    allow_empty: bool,
) -> str:
    if not isinstance(value, str):
        raise InspirationVectorizationError(
            "INVALID_EMBEDDING_INPUT",
            f"{field_name} must be a string",
        )
    normalized = _WHITESPACE_PATTERN.sub(
        " ", unicodedata.normalize("NFKC", value)
    ).strip()
    if not normalized and not allow_empty:
        raise InspirationVectorizationError(
            "INVALID_EMBEDDING_INPUT",
            f"{field_name} must not be empty",
        )
    if len(normalized) > max_length:
        raise InspirationVectorizationError(
            "INVALID_EMBEDDING_INPUT",
            f"{field_name} exceeds its {max_length}-character limit",
        )
    return normalized


def _normalize_tags(normalized_tags: Sequence[str]) -> tuple[str, ...]:
    if isinstance(normalized_tags, (str, bytes)):
        raise InspirationVectorizationError(
            "INVALID_EMBEDDING_INPUT",
            "normalized_tags must be a sequence of tag strings",
        )
    materialized = tuple(normalized_tags)
    if len(materialized) > 32:
        raise InspirationVectorizationError(
            "INVALID_EMBEDDING_INPUT",
            "normalized_tags exceeds the 32-tag passage limit",
        )
    canonical: list[str] = []
    for tag in materialized:
        normalized = _normalize_bounded_text(
            tag,
            field_name="normalized tag",
            max_length=128,
            allow_empty=False,
        ).casefold()
        canonical.append(normalized)
    return tuple(sorted(set(canonical)))


def _prepare_embedding_input(
    passage: PassageV1,
    *,
    title: str,
    normalized_tags: Sequence[str],
) -> _PreparedEmbeddingInput:
    if not isinstance(passage, PassageV1):
        raise InspirationVectorizationError(
            "INVALID_PASSAGE",
            "vectorization accepts only a validated PassageV1 excerpt",
        )

    canonical_title = _normalize_bounded_text(
        title,
        field_name="title",
        max_length=1_000,
        allow_empty=False,
    )
    canonical_section = _normalize_bounded_text(
        passage.locator.section_heading or "",
        field_name="section heading",
        max_length=512,
        allow_empty=True,
    )
    canonical_passage = _normalize_bounded_text(
        passage.text,
        field_name="selected passage",
        max_length=8_000,
        allow_empty=False,
    )
    canonical_tags = _normalize_tags(normalized_tags)

    payload = {
        "normalized_tags": canonical_tags,
        "section_heading": canonical_section,
        "selected_passage": canonical_passage,
        "title": canonical_title,
    }
    canonical_bytes = canonical_json_bytes(payload)

    token_fields: list[tuple[str, tuple[str, ...]]] = [
        ("title", tokenize_signed_hashing_v1(canonical_title)),
        ("section", tokenize_signed_hashing_v1(canonical_section)),
        ("passage", tokenize_signed_hashing_v1(canonical_passage)),
    ]
    token_fields.extend(
        ("tag", tokenize_signed_hashing_v1(tag)) for tag in canonical_tags
    )
    token_count = sum(len(tokens) for _, tokens in token_fields)
    if token_count == 0:
        raise InspirationVectorizationError(
            "NO_EMBEDDING_TOKENS",
            "the bounded embedding input produced no signed-hashing tokens",
        )
    return _PreparedEmbeddingInput(
        canonical_bytes=canonical_bytes,
        token_fields=tuple(token_fields),
        token_count=token_count,
    )


def _validate_vectorizer(
    *,
    budget: EmbeddingBudgetV1,
    vectorizer: ComponentSnapshotV1,
) -> None:
    if budget.vectorizer_id != SIGNED_HASHING_VECTORIZER_ID:
        raise InspirationVectorizationError(
            "UNSUPPORTED_VECTORIZER",
            f"expected budget vectorizer_id {SIGNED_HASHING_VECTORIZER_ID!r}",
        )
    if vectorizer != SIGNED_HASHING_SNAPSHOT:
        raise InspirationVectorizationError(
            "VECTORIZER_SNAPSHOT_MISMATCH",
            "signed-hashing-v1 requires its exact frozen component snapshot",
        )


def _iter_features(
    token_fields: tuple[tuple[str, tuple[str, ...]], ...],
) -> Iterator[bytes]:
    for namespace, tokens in token_fields:
        for token in tokens:
            yield f"{namespace}\x1funigram\x1f{token}".encode("utf-8")
        for left, right in zip(tokens, tokens[1:]):
            yield f"{namespace}\x1fbigram\x1f{left}\x1f{right}".encode("utf-8")


def _signed_hash_artifact_bytes(
    embedding_input: _PreparedEmbeddingInput,
    *,
    dimension: int,
) -> bytes:
    accumulator = [0] * dimension
    for feature in _iter_features(embedding_input.token_fields):
        digest = hashlib.sha256(feature).digest()
        index = int.from_bytes(digest[:8], byteorder="little") % dimension
        sign = 1 if digest[8] & 1 == 0 else -1
        accumulator[index] += sign

    norm = math.sqrt(math.fsum(value * value for value in accumulator))
    if not math.isfinite(norm) or norm == 0.0:
        raise InspirationVectorizationError(
            "ZERO_VECTOR",
            "signed feature hashing produced a non-normalizable vector",
        )
    normalized = tuple(value / norm for value in accumulator)
    if not all(math.isfinite(value) for value in normalized):
        raise InspirationVectorizationError(
            "NON_FINITE_VECTOR",
            "signed feature hashing produced a non-finite value",
        )
    return struct.pack(f"<{dimension}f", *normalized)


def _resolve_vector_artifact(
    target: ArtifactPointerV1 | str,
    *,
    vector_sha256: str,
    size_bytes: int,
) -> ArtifactPointerV1:
    if isinstance(target, str):
        if not _SAFE_ARTIFACT_NAME.fullmatch(target):
            raise InspirationVectorizationError(
                "UNSAFE_ARTIFACT_NAME",
                "vector artifact name must be a safe relative basename",
            )
        return ArtifactPointerV1(
            uri=f"artifact://inspiration/vectors/{target}",
            sha256=vector_sha256,
            size_bytes=size_bytes,
            media_type=VECTOR_ARTIFACT_MEDIA_TYPE,
        )
    if not isinstance(target, ArtifactPointerV1):
        raise InspirationVectorizationError(
            "INVALID_ARTIFACT_TARGET",
            "vector_artifact must be an ArtifactPointerV1 or safe relative name",
        )
    if target.sha256 != vector_sha256:
        raise InspirationVectorizationError(
            "ARTIFACT_HASH_MISMATCH",
            "provided vector artifact SHA-256 does not match generated bytes",
        )
    if target.size_bytes is not None and target.size_bytes != size_bytes:
        raise InspirationVectorizationError(
            "ARTIFACT_SIZE_MISMATCH",
            "provided vector artifact size does not match generated bytes",
        )
    if (
        target.media_type is not None
        and target.media_type != VECTOR_ARTIFACT_MEDIA_TYPE
    ):
        raise InspirationVectorizationError(
            "ARTIFACT_MEDIA_TYPE_MISMATCH",
            f"vector artifacts must use {VECTOR_ARTIFACT_MEDIA_TYPE!r}",
        )
    return ArtifactPointerV1(
        uri=target.uri,
        sha256=vector_sha256,
        size_bytes=size_bytes,
        media_type=VECTOR_ARTIFACT_MEDIA_TYPE,
    )


def _vectorize_prepared_passage(
    request: PassageVectorizationRequest,
    *,
    embedding_input: _PreparedEmbeddingInput,
    dimension: int,
    vectorizer: ComponentSnapshotV1,
) -> PassageVectorizationResult:
    artifact_bytes = _signed_hash_artifact_bytes(
        embedding_input,
        dimension=dimension,
    )
    embedding_input_sha256 = hashlib.sha256(
        embedding_input.canonical_bytes
    ).hexdigest()
    vector_sha256 = hashlib.sha256(artifact_bytes).hexdigest()
    vector_artifact = _resolve_vector_artifact(
        request.vector_artifact,
        vector_sha256=vector_sha256,
        size_bytes=len(artifact_bytes),
    )
    cache_key_sha256 = canonical_sha256(
        {
            "cache_schema": "signed-hashing-cache-v1",
            "dimension": dimension,
            "embedding_input_sha256": embedding_input_sha256,
            "vectorizer": vectorizer,
        }
    )
    passage_vector = PassageVectorV1(
        passage_id=request.passage.passage_id,
        normalized_text_sha256=request.passage.normalized_text_sha256,
        vectorizer=vectorizer,
        embedding_input_sha256=embedding_input_sha256,
        dimension=dimension,
        vector_artifact=vector_artifact,
        vector_sha256=vector_sha256,
        cache_key_sha256=cache_key_sha256,
    )
    return PassageVectorizationResult(
        passage_vector=passage_vector,
        artifact_bytes=artifact_bytes,
        embedding_input_bytes=embedding_input.canonical_bytes,
        input_token_count=embedding_input.token_count,
    )
