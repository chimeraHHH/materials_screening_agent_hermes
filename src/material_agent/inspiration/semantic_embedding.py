"""Provider-neutral contracts for bounded local semantic passage embeddings.

This module deliberately does not ship or download a model.  Production use is
unavailable unless a caller supplies a local provider whose exact bundle,
tokenizer, configuration, dependency lock, and license bytes match a frozen
identity.  The provider entry point accepts only the canonical title, section,
selected-passage, and normalized-tag payload; there is no document, URL, PDF,
or network entry point.

The existing :mod:`material_agent.inspiration.vectorizer` remains the
``signed-hashing-v1`` lexical baseline.  Nothing in this module renames signed
hashing or treats it as a semantic model.
"""

from __future__ import annotations

import hashlib
import math
import re
import struct
import unicodedata
from collections.abc import MutableMapping, Sequence
from dataclasses import asdict, dataclass
from typing import Protocol, runtime_checkable

from material_agent.inspiration.models import canonical_json_bytes, canonical_sha256


SEMANTIC_ADAPTER_SCHEMA_VERSION = "local-semantic-passage-embedding-v1"
SEMANTIC_CACHE_SCHEMA_VERSION = "local-semantic-passage-cache-v1"
SEMANTIC_VECTOR_ARTIFACT_MEDIA_TYPE = (
    "application/vnd.material-agent.semantic-vector-f32le"
)
PRODUCTION_SEMANTIC_UNAVAILABLE_REASON = (
    "no reviewed SHA-pinned local semantic model bundle/provider is registered"
)

_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/+@-]{0,255}$")
_WHITESPACE_PATTERN = re.compile(r"\s+", flags=re.UNICODE)


class SemanticEmbeddingError(ValueError):
    """Fail-closed semantic embedding error with a stable code."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(f"{code}: {message}")


def _require_sha256(value: str, *, field_name: str) -> None:
    if not isinstance(value, str) or not _SHA256_PATTERN.fullmatch(value):
        raise SemanticEmbeddingError(
            "INVALID_MODEL_IDENTITY",
            f"{field_name} must be a lowercase SHA-256 digest",
        )


def _require_identifier(value: str, *, field_name: str) -> None:
    if not isinstance(value, str) or not _IDENTIFIER_PATTERN.fullmatch(value):
        raise SemanticEmbeddingError(
            "INVALID_MODEL_IDENTITY",
            f"{field_name} must be a bounded identifier",
        )


@dataclass(frozen=True, slots=True)
class LocalSemanticModelIdentityV1:
    """Exact identity of one provider-neutral, locally loaded model bundle."""

    provider_id: str
    model_id: str
    model_revision: str
    tokenizer_sha256: str
    config_sha256: str
    model_bundle_sha256: str
    dependency_lock_sha256: str
    license_id: str
    license_sha256: str
    dimension: int
    local_files_only: bool = True
    synthetic: bool = False
    schema_version: str = SEMANTIC_ADAPTER_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != SEMANTIC_ADAPTER_SCHEMA_VERSION:
            raise SemanticEmbeddingError(
                "INVALID_MODEL_IDENTITY",
                "semantic adapter schema version is not supported",
            )
        for field_name, value in (
            ("provider_id", self.provider_id),
            ("model_id", self.model_id),
            ("model_revision", self.model_revision),
            ("license_id", self.license_id),
        ):
            _require_identifier(value, field_name=field_name)
        for field_name, value in (
            ("tokenizer_sha256", self.tokenizer_sha256),
            ("config_sha256", self.config_sha256),
            ("model_bundle_sha256", self.model_bundle_sha256),
            ("dependency_lock_sha256", self.dependency_lock_sha256),
            ("license_sha256", self.license_sha256),
        ):
            _require_sha256(value, field_name=field_name)
        if (
            not isinstance(self.dimension, int)
            or isinstance(self.dimension, bool)
            or not 8 <= self.dimension <= 16_384
        ):
            raise SemanticEmbeddingError(
                "INVALID_MODEL_IDENTITY",
                "dimension must be an integer between 8 and 16,384",
            )
        if self.local_files_only is not True:
            raise SemanticEmbeddingError(
                "REMOTE_PROVIDER_FORBIDDEN",
                "semantic providers must be local-files-only",
            )
        if not isinstance(self.synthetic, bool):
            raise SemanticEmbeddingError(
                "INVALID_MODEL_IDENTITY",
                "synthetic must be a boolean",
            )

    @property
    def identity_sha256(self) -> str:
        """Canonical identity hash used by cache and result records."""

        return canonical_sha256(asdict(self))


@dataclass(frozen=True, slots=True)
class LocalBundleVerificationV1:
    """Observed local bytes and checks returned by a model provider."""

    tokenizer_sha256: str
    config_sha256: str
    model_bundle_sha256: str
    dependency_lock_sha256: str
    license_sha256: str
    observed_dimension: int
    loaded_from_local_path: bool
    dependency_check_passed: bool
    license_check_passed: bool


@dataclass(frozen=True, slots=True)
class SemanticPassageInputV1:
    """The complete and only semantic-model input surface."""

    title: str
    section_heading: str
    selected_passage: str
    normalized_tags: tuple[str, ...] = ()

    def canonical_bytes(self) -> bytes:
        """Normalize and serialize exactly the four allowed input fields."""

        title = _normalize_bounded_text(
            self.title,
            field_name="title",
            max_length=1_000,
            allow_empty=False,
        )
        section = _normalize_bounded_text(
            self.section_heading,
            field_name="section_heading",
            max_length=512,
            allow_empty=True,
        )
        passage = _normalize_bounded_text(
            self.selected_passage,
            field_name="selected_passage",
            max_length=8_000,
            allow_empty=False,
        )
        tags = _normalize_tags(self.normalized_tags)
        return canonical_json_bytes(
            {
                "normalized_tags": tags,
                "section_heading": section,
                "selected_passage": passage,
                "title": title,
            }
        )


@dataclass(frozen=True, slots=True)
class SemanticEmbeddingCacheEntryV1:
    """Cache payload validated again before every use."""

    cache_key_sha256: str
    model_identity_sha256: str
    embedding_input_sha256: str
    dimension: int
    input_token_count: int
    vector: tuple[float, ...]

    def __post_init__(self) -> None:
        for field_name, value in (
            ("cache_key_sha256", self.cache_key_sha256),
            ("model_identity_sha256", self.model_identity_sha256),
            ("embedding_input_sha256", self.embedding_input_sha256),
        ):
            _require_sha256(value, field_name=field_name)
        if (
            not isinstance(self.dimension, int)
            or isinstance(self.dimension, bool)
            or not 8 <= self.dimension <= 16_384
        ):
            raise SemanticEmbeddingError(
                "INVALID_CACHE_ENTRY",
                "cached dimension is outside the supported range",
            )
        if (
            not isinstance(self.input_token_count, int)
            or isinstance(self.input_token_count, bool)
            or self.input_token_count < 1
        ):
            raise SemanticEmbeddingError(
                "INVALID_CACHE_ENTRY",
                "cached token count must be a positive integer",
            )


@dataclass(frozen=True, slots=True)
class SemanticEmbeddingResultV1:
    """Validated semantic vector plus exact replay/cache provenance."""

    model_identity: LocalSemanticModelIdentityV1
    embedding_input_sha256: str
    input_token_count: int
    dimension: int
    vector: tuple[float, ...]
    vector_sha256: str
    cache_key_sha256: str
    artifact_bytes: bytes
    cache_hit: bool

    def __post_init__(self) -> None:
        if not isinstance(self.model_identity, LocalSemanticModelIdentityV1):
            raise SemanticEmbeddingError(
                "INVALID_SEMANTIC_RESULT",
                "result model identity is invalid",
            )
        for field_name, value in (
            ("embedding_input_sha256", self.embedding_input_sha256),
            ("vector_sha256", self.vector_sha256),
            ("cache_key_sha256", self.cache_key_sha256),
        ):
            _require_sha256(value, field_name=field_name)
        if self.dimension != self.model_identity.dimension:
            raise SemanticEmbeddingError(
                "INVALID_SEMANTIC_RESULT",
                "result dimension differs from its model identity",
            )
        if (
            not isinstance(self.input_token_count, int)
            or isinstance(self.input_token_count, bool)
            or self.input_token_count < 1
        ):
            raise SemanticEmbeddingError(
                "INVALID_SEMANTIC_RESULT",
                "result token count must be a positive integer",
            )
        vector = _validate_l2_vector(
            self.vector,
            dimension=self.dimension,
            error_prefix="RESULT",
            tolerance=1e-5,
        )
        if not isinstance(self.artifact_bytes, bytes):
            raise SemanticEmbeddingError(
                "INVALID_SEMANTIC_RESULT",
                "result artifact must be bytes",
            )
        if len(self.artifact_bytes) != self.dimension * 4:
            raise SemanticEmbeddingError(
                "INVALID_SEMANTIC_RESULT",
                "result artifact length does not match its dimension",
            )
        if hashlib.sha256(self.artifact_bytes).hexdigest() != self.vector_sha256:
            raise SemanticEmbeddingError(
                "INVALID_SEMANTIC_RESULT",
                "result vector hash does not match its artifact bytes",
            )
        decoded = struct.unpack(f"<{self.dimension}f", self.artifact_bytes)
        if decoded != vector:
            raise SemanticEmbeddingError(
                "INVALID_SEMANTIC_RESULT",
                "result vector does not match its float32 artifact bytes",
            )
        if not isinstance(self.cache_hit, bool):
            raise SemanticEmbeddingError(
                "INVALID_SEMANTIC_RESULT",
                "cache_hit must be a boolean",
            )


@runtime_checkable
class LocalSemanticEmbeddingProvider(Protocol):
    """Minimal provider-neutral interface; implementations must stay local."""

    @property
    def identity(self) -> LocalSemanticModelIdentityV1:
        """Return the provider's exact frozen model identity."""

    def verify_local_bundle(self) -> LocalBundleVerificationV1:
        """Return observed hashes/checks for the currently loaded local bundle."""

    def count_tokens(self, canonical_input: bytes) -> int:
        """Count tokens with the tokenizer bound by ``identity``."""

    def embed(self, canonical_input: bytes) -> Sequence[float]:
        """Embed one bounded canonical passage input locally."""


class LocalSemanticEmbeddingAdapter:
    """Validate one local provider at every input, output, and cache boundary."""

    def __init__(
        self,
        *,
        provider: LocalSemanticEmbeddingProvider,
        cache: MutableMapping[str, SemanticEmbeddingCacheEntryV1] | None = None,
        allow_synthetic: bool = False,
    ) -> None:
        if not isinstance(provider, LocalSemanticEmbeddingProvider):
            raise SemanticEmbeddingError(
                "INVALID_PROVIDER",
                "provider does not implement the local semantic protocol",
            )
        if not isinstance(provider.identity, LocalSemanticModelIdentityV1):
            raise SemanticEmbeddingError(
                "INVALID_PROVIDER",
                "provider identity must be LocalSemanticModelIdentityV1",
            )
        if provider.identity.synthetic and not allow_synthetic:
            raise SemanticEmbeddingError(
                "SYNTHETIC_PROVIDER_FORBIDDEN",
                "synthetic providers require an explicit test-only opt-in",
            )
        if cache is not None and not isinstance(cache, MutableMapping):
            raise SemanticEmbeddingError(
                "INVALID_CACHE",
                "cache must implement MutableMapping",
            )
        self._provider = provider
        self._identity = provider.identity
        self._cache = cache
        self._verify_bundle()

    @property
    def identity(self) -> LocalSemanticModelIdentityV1:
        return self._identity

    def embed_one(
        self,
        semantic_input: SemanticPassageInputV1,
        *,
        max_input_tokens: int,
    ) -> SemanticEmbeddingResultV1:
        """Embed one bounded input and validate token/cache/vector invariants."""

        if not isinstance(semantic_input, SemanticPassageInputV1):
            raise SemanticEmbeddingError(
                "INVALID_EMBEDDING_INPUT",
                "adapter accepts only SemanticPassageInputV1",
            )
        if (
            not isinstance(max_input_tokens, int)
            or isinstance(max_input_tokens, bool)
            or max_input_tokens < 1
        ):
            raise SemanticEmbeddingError(
                "INVALID_TOKEN_BUDGET",
                "max_input_tokens must be a positive integer",
            )

        self._verify_bundle()
        canonical_input = semantic_input.canonical_bytes()
        input_sha256 = hashlib.sha256(canonical_input).hexdigest()
        token_count = self._provider.count_tokens(canonical_input)
        if (
            not isinstance(token_count, int)
            or isinstance(token_count, bool)
            or token_count < 1
        ):
            raise SemanticEmbeddingError(
                "INVALID_TOKEN_COUNT",
                "provider token count must be a positive integer",
            )
        if token_count > max_input_tokens:
            raise SemanticEmbeddingError(
                "MAX_INPUT_TOKENS_EXCEEDED",
                f"semantic input contains {token_count} tokens; budget allows "
                f"{max_input_tokens}",
            )

        cache_key = canonical_sha256(
            {
                "cache_schema": SEMANTIC_CACHE_SCHEMA_VERSION,
                "dimension": self.identity.dimension,
                "embedding_input_sha256": input_sha256,
                "model_identity_sha256": self.identity.identity_sha256,
            }
        )
        if self._cache is not None and cache_key in self._cache:
            entry = self._cache[cache_key]
            vector = self._validate_cache_entry(
                entry,
                expected_cache_key=cache_key,
                expected_input_sha256=input_sha256,
                expected_token_count=token_count,
            )
            return self._build_result(
                vector=vector,
                input_sha256=input_sha256,
                token_count=token_count,
                cache_key=cache_key,
                cache_hit=True,
            )

        vector = _validate_l2_vector(
            self._provider.embed(canonical_input),
            dimension=self.identity.dimension,
            error_prefix="PROVIDER",
        )
        result = self._build_result(
            vector=vector,
            input_sha256=input_sha256,
            token_count=token_count,
            cache_key=cache_key,
            cache_hit=False,
        )
        if self._cache is not None:
            self._cache[cache_key] = SemanticEmbeddingCacheEntryV1(
                cache_key_sha256=cache_key,
                model_identity_sha256=self.identity.identity_sha256,
                embedding_input_sha256=input_sha256,
                dimension=self.identity.dimension,
                input_token_count=token_count,
                vector=result.vector,
            )
        return result

    def embed_many(
        self,
        inputs: Sequence[SemanticPassageInputV1],
        *,
        max_passages: int,
        max_input_tokens: int,
    ) -> tuple[SemanticEmbeddingResultV1, ...]:
        """Embed a bounded sequence while enforcing one aggregate token budget."""

        if isinstance(inputs, (str, bytes)):
            raise SemanticEmbeddingError(
                "INVALID_EMBEDDING_INPUT",
                "inputs must be a sequence of SemanticPassageInputV1 records",
            )
        materialized = tuple(inputs)
        if (
            not isinstance(max_passages, int)
            or isinstance(max_passages, bool)
            or max_passages < 1
        ):
            raise SemanticEmbeddingError(
                "INVALID_PASSAGE_BUDGET",
                "max_passages must be a positive integer",
            )
        if len(materialized) > max_passages:
            raise SemanticEmbeddingError(
                "MAX_PASSAGES_EXCEEDED",
                f"received {len(materialized)} passages; budget allows {max_passages}",
            )

        if (
            not isinstance(max_input_tokens, int)
            or isinstance(max_input_tokens, bool)
            or max_input_tokens < 1
        ):
            raise SemanticEmbeddingError(
                "INVALID_TOKEN_BUDGET",
                "max_input_tokens must be a positive integer",
            )
        remaining = max_input_tokens
        results: list[SemanticEmbeddingResultV1] = []
        for semantic_input in materialized:
            if remaining < 1:
                raise SemanticEmbeddingError(
                    "MAX_INPUT_TOKENS_EXCEEDED",
                    "aggregate semantic input token budget is exhausted",
                )
            result = self.embed_one(
                semantic_input,
                max_input_tokens=remaining,
            )
            results.append(result)
            remaining -= result.input_token_count
        return tuple(results)

    def _verify_bundle(self) -> None:
        if self._provider.identity != self._identity:
            raise SemanticEmbeddingError(
                "PROVIDER_IDENTITY_DRIFT",
                "provider identity changed after adapter construction",
            )
        verification = self._provider.verify_local_bundle()
        if not isinstance(verification, LocalBundleVerificationV1):
            raise SemanticEmbeddingError(
                "INVALID_BUNDLE_VERIFICATION",
                "provider returned an invalid bundle verification record",
            )
        if not verification.loaded_from_local_path:
            raise SemanticEmbeddingError(
                "REMOTE_PROVIDER_FORBIDDEN",
                "semantic model bundle was not loaded from a local path",
            )
        if not verification.dependency_check_passed:
            raise SemanticEmbeddingError(
                "DEPENDENCY_CHECK_FAILED",
                "semantic provider dependency check did not pass",
            )
        if not verification.license_check_passed:
            raise SemanticEmbeddingError(
                "LICENSE_CHECK_FAILED",
                "semantic model license check did not pass",
            )
        expected = self.identity
        observed_pairs = (
            ("tokenizer_sha256", verification.tokenizer_sha256, expected.tokenizer_sha256),
            ("config_sha256", verification.config_sha256, expected.config_sha256),
            (
                "model_bundle_sha256",
                verification.model_bundle_sha256,
                expected.model_bundle_sha256,
            ),
            (
                "dependency_lock_sha256",
                verification.dependency_lock_sha256,
                expected.dependency_lock_sha256,
            ),
            ("license_sha256", verification.license_sha256, expected.license_sha256),
        )
        for label, observed, pinned in observed_pairs:
            if observed != pinned:
                raise SemanticEmbeddingError(
                    "MODEL_BUNDLE_DRIFT",
                    f"observed {label} does not match the pinned identity",
                )
        if verification.observed_dimension != expected.dimension:
            raise SemanticEmbeddingError(
                "MODEL_DIMENSION_DRIFT",
                "observed model dimension does not match the pinned identity",
            )

    def _validate_cache_entry(
        self,
        entry: SemanticEmbeddingCacheEntryV1,
        *,
        expected_cache_key: str,
        expected_input_sha256: str,
        expected_token_count: int,
    ) -> tuple[float, ...]:
        if not isinstance(entry, SemanticEmbeddingCacheEntryV1):
            raise SemanticEmbeddingError(
                "INVALID_CACHE_ENTRY",
                "semantic cache returned an unexpected value type",
            )
        expected_values = (
            ("cache key", entry.cache_key_sha256, expected_cache_key),
            (
                "model identity",
                entry.model_identity_sha256,
                self.identity.identity_sha256,
            ),
            ("embedding input", entry.embedding_input_sha256, expected_input_sha256),
            ("dimension", entry.dimension, self.identity.dimension),
            ("input token count", entry.input_token_count, expected_token_count),
        )
        for label, observed, expected in expected_values:
            if observed != expected:
                raise SemanticEmbeddingError(
                    "CACHE_IDENTITY_MISMATCH",
                    f"cached {label} does not match this embedding request",
                )
        return _validate_l2_vector(
            entry.vector,
            dimension=self.identity.dimension,
            error_prefix="CACHE",
        )

    def _build_result(
        self,
        *,
        vector: tuple[float, ...],
        input_sha256: str,
        token_count: int,
        cache_key: str,
        cache_hit: bool,
    ) -> SemanticEmbeddingResultV1:
        artifact_bytes = struct.pack(f"<{self.identity.dimension}f", *vector)
        float32_vector = struct.unpack(
            f"<{self.identity.dimension}f",
            artifact_bytes,
        )
        validated_float32 = _validate_l2_vector(
            float32_vector,
            dimension=self.identity.dimension,
            error_prefix="FLOAT32",
            tolerance=1e-5,
        )
        return SemanticEmbeddingResultV1(
            model_identity=self.identity,
            embedding_input_sha256=input_sha256,
            input_token_count=token_count,
            dimension=self.identity.dimension,
            vector=validated_float32,
            vector_sha256=hashlib.sha256(artifact_bytes).hexdigest(),
            cache_key_sha256=cache_key,
            artifact_bytes=artifact_bytes,
            cache_hit=cache_hit,
        )


def build_production_semantic_adapter(
    *,
    provider: LocalSemanticEmbeddingProvider | None = None,
    cache: MutableMapping[str, SemanticEmbeddingCacheEntryV1] | None = None,
) -> LocalSemanticEmbeddingAdapter:
    """Build production mode only from an explicitly supplied real provider.

    The repository intentionally registers no default bundle. A missing
    provider is therefore a stable, explicit unavailable state rather than a
    download, remote API fallback, or substitution of signed hashing. The
    optional SHA-pinned Sentence Transformers implementation is constructed
    explicitly by ``sentence_transformers_provider``.
    """

    if provider is None:
        raise SemanticEmbeddingError(
            "SEMANTIC_PRODUCTION_UNAVAILABLE",
            PRODUCTION_SEMANTIC_UNAVAILABLE_REASON,
        )
    return LocalSemanticEmbeddingAdapter(
        provider=provider,
        cache=cache,
        allow_synthetic=False,
    )


def _normalize_bounded_text(
    value: str,
    *,
    field_name: str,
    max_length: int,
    allow_empty: bool,
) -> str:
    if not isinstance(value, str):
        raise SemanticEmbeddingError(
            "INVALID_EMBEDDING_INPUT",
            f"{field_name} must be a string",
        )
    normalized = _WHITESPACE_PATTERN.sub(
        " ", unicodedata.normalize("NFKC", value)
    ).strip()
    if not normalized and not allow_empty:
        raise SemanticEmbeddingError(
            "INVALID_EMBEDDING_INPUT",
            f"{field_name} must not be empty",
        )
    if len(normalized) > max_length:
        raise SemanticEmbeddingError(
            "INVALID_EMBEDDING_INPUT",
            f"{field_name} exceeds its {max_length}-character limit",
        )
    return normalized


def _normalize_tags(tags: Sequence[str]) -> tuple[str, ...]:
    if isinstance(tags, (str, bytes)):
        raise SemanticEmbeddingError(
            "INVALID_EMBEDDING_INPUT",
            "normalized_tags must be a sequence of strings",
        )
    materialized = tuple(tags)
    if len(materialized) > 32:
        raise SemanticEmbeddingError(
            "INVALID_EMBEDDING_INPUT",
            "normalized_tags exceeds the 32-tag limit",
        )
    normalized = tuple(
        _normalize_bounded_text(
            value,
            field_name="normalized tag",
            max_length=128,
            allow_empty=False,
        ).casefold()
        for value in materialized
    )
    return tuple(sorted(set(normalized)))


def _validate_l2_vector(
    values: Sequence[float],
    *,
    dimension: int,
    error_prefix: str,
    tolerance: float = 1e-6,
) -> tuple[float, ...]:
    if isinstance(values, (str, bytes)):
        raise SemanticEmbeddingError(
            f"{error_prefix}_INVALID_VECTOR",
            "embedding vector must be a sequence of floats",
        )
    try:
        vector = tuple(values)
    except TypeError as exc:
        raise SemanticEmbeddingError(
            f"{error_prefix}_INVALID_VECTOR",
            "embedding vector must be a finite sequence",
        ) from exc
    if len(vector) != dimension:
        raise SemanticEmbeddingError(
            f"{error_prefix}_DIMENSION_MISMATCH",
            f"embedding has dimension {len(vector)}; expected {dimension}",
        )
    if any(
        not isinstance(value, float) or not math.isfinite(value)
        for value in vector
    ):
        raise SemanticEmbeddingError(
            f"{error_prefix}_NON_FINITE_VECTOR",
            "embedding values must be finite floats",
        )
    norm = math.sqrt(math.fsum(value * value for value in vector))
    if not math.isfinite(norm) or abs(norm - 1.0) > tolerance:
        raise SemanticEmbeddingError(
            f"{error_prefix}_NOT_L2_NORMALIZED",
            f"embedding L2 norm {norm!r} is outside tolerance {tolerance}",
        )
    return vector
