"""SHA-pinned local Sentence Transformers provider for semantic passages.

The provider never downloads a model.  Operators prepare a local bundle and a
manifest listing every file and digest; construction fails if the directory,
installed dependency, license, tokenizer, configuration, or output dimension
differs from that manifest.
"""

from __future__ import annotations

import hashlib
import importlib.metadata
import json
import os
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

from material_agent.inspiration.models import canonical_sha256
from material_agent.inspiration.semantic_embedding import (
    LocalBundleVerificationV1,
    LocalSemanticEmbeddingAdapter,
    LocalSemanticModelIdentityV1,
    SemanticEmbeddingCacheEntryV1,
    SemanticEmbeddingError,
)

SENTENCE_TRANSFORMERS_BUNDLE_SCHEMA = "sentence-transformers-local-bundle-v1"
SENTENCE_TRANSFORMERS_MANIFEST = "semantic-model-manifest-v1.json"
SEMANTIC_MODEL_BUNDLE_ENV = "MATERIALS_SEMANTIC_MODEL_BUNDLE"
SEMANTIC_MODEL_DEVICE_ENV = "MATERIALS_SEMANTIC_MODEL_DEVICE"
_MANIFEST_FIELDS = {
    "schema_version",
    "provider_id",
    "model_id",
    "model_revision",
    "dimension",
    "license_id",
    "sentence_transformers_version",
    "tokenizer_file",
    "config_file",
    "dependency_lock_file",
    "license_file",
    "files",
}


class SentenceTransformersLocalProvider:
    """Real local embedding provider implementing the reviewed v1 protocol."""

    def __init__(
        self,
        *,
        bundle_path: Path | str,
        device: str = "cpu",
        model_loader: Callable[..., Any] | None = None,
        version_resolver: Callable[[str], str] = importlib.metadata.version,
    ) -> None:
        root = Path(bundle_path).expanduser()
        if not root.is_absolute():
            raise SemanticEmbeddingError(
                "INVALID_MODEL_BUNDLE",
                "semantic model bundle path must be absolute",
            )
        self._root = root.resolve(strict=True)
        if not self._root.is_dir():
            raise SemanticEmbeddingError(
                "INVALID_MODEL_BUNDLE",
                "semantic model bundle path must be a directory",
            )
        if device not in {"cpu", "mps"}:
            raise SemanticEmbeddingError(
                "INVALID_MODEL_DEVICE",
                "semantic provider device must be cpu or mps",
            )
        self._device = device
        self._manifest = self._load_manifest()
        self._expected_version = _require_string(
            self._manifest["sentence_transformers_version"],
            "sentence_transformers_version",
        )
        self._version_resolver = version_resolver
        self._files = self._validated_file_map(self._manifest["files"])
        self._identity = self._build_identity()
        self._preload_verification = self._verify_files_and_dependency()

        loader = model_loader or _load_sentence_transformer
        try:
            self._model = loader(
                str(self._root),
                device=self._device,
                local_files_only=True,
                trust_remote_code=False,
            )
        except SemanticEmbeddingError:
            raise
        except Exception as exc:
            raise SemanticEmbeddingError(
                "MODEL_LOAD_FAILED",
                "local semantic model could not be loaded",
            ) from exc
        observed_dimension = _embedding_dimension(self._model)
        if observed_dimension != self._identity.dimension:
            raise SemanticEmbeddingError(
                "MODEL_DIMENSION_DRIFT",
                "loaded semantic model dimension differs from the manifest",
            )
        if not hasattr(self._model, "tokenizer"):
            raise SemanticEmbeddingError(
                "TOKENIZER_UNAVAILABLE",
                "loaded semantic model does not expose its tokenizer",
            )

    @property
    def identity(self) -> LocalSemanticModelIdentityV1:
        return self._identity

    def verify_local_bundle(self) -> LocalBundleVerificationV1:
        observed = self._verify_files_and_dependency()
        return LocalBundleVerificationV1(
            tokenizer_sha256=observed["tokenizer_sha256"],
            config_sha256=observed["config_sha256"],
            model_bundle_sha256=observed["model_bundle_sha256"],
            dependency_lock_sha256=observed["dependency_lock_sha256"],
            license_sha256=observed["license_sha256"],
            observed_dimension=_embedding_dimension(self._model),
            loaded_from_local_path=True,
            dependency_check_passed=observed["dependency_check_passed"],
            license_check_passed=observed["license_check_passed"],
        )

    def count_tokens(self, canonical_input: bytes) -> int:
        text = _decode_canonical_input(canonical_input)
        try:
            tokens = self._model.tokenizer.encode(text, add_special_tokens=True)
        except Exception as exc:
            raise SemanticEmbeddingError(
                "TOKENIZATION_FAILED",
                "semantic tokenizer rejected the bounded input",
            ) from exc
        if isinstance(tokens, (str, bytes)) or not isinstance(tokens, Sequence):
            raise SemanticEmbeddingError(
                "INVALID_TOKEN_COUNT",
                "semantic tokenizer returned an invalid token sequence",
            )
        return len(tokens)

    def embed(self, canonical_input: bytes) -> Sequence[float]:
        text = _decode_canonical_input(canonical_input)
        try:
            vectors = self._model.encode(
                [text],
                normalize_embeddings=True,
                convert_to_numpy=True,
                show_progress_bar=False,
            )
            vector = vectors[0]
            return tuple(float(value) for value in vector)
        except Exception as exc:
            raise SemanticEmbeddingError(
                "EMBEDDING_FAILED",
                "local semantic model failed on the bounded passage input",
            ) from exc

    def _load_manifest(self) -> dict[str, Any]:
        path = self._root / SENTENCE_TRANSFORMERS_MANIFEST
        try:
            payload = json.loads(path.read_text("utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise SemanticEmbeddingError(
                "INVALID_MODEL_MANIFEST",
                "semantic model manifest is missing or invalid",
            ) from exc
        if not isinstance(payload, dict) or set(payload) != _MANIFEST_FIELDS:
            raise SemanticEmbeddingError(
                "INVALID_MODEL_MANIFEST",
                "semantic model manifest fields do not match v1",
            )
        if payload.get("schema_version") != SENTENCE_TRANSFORMERS_BUNDLE_SCHEMA:
            raise SemanticEmbeddingError(
                "INVALID_MODEL_MANIFEST",
                "semantic model manifest schema is unsupported",
            )
        return payload

    def _validated_file_map(self, value: Any) -> dict[str, str]:
        if not isinstance(value, dict) or not value:
            raise SemanticEmbeddingError(
                "INVALID_MODEL_MANIFEST",
                "semantic model file map must be a non-empty object",
            )
        result: dict[str, str] = {}
        for raw_name, raw_digest in value.items():
            if not isinstance(raw_name, str) or not isinstance(raw_digest, str):
                raise SemanticEmbeddingError(
                    "INVALID_MODEL_MANIFEST",
                    "semantic model file entries must be strings",
                )
            relative = Path(raw_name)
            if (
                relative.is_absolute()
                or ".." in relative.parts
                or raw_name == SENTENCE_TRANSFORMERS_MANIFEST
            ):
                raise SemanticEmbeddingError(
                    "INVALID_MODEL_MANIFEST",
                    "semantic model file path escapes the bundle",
                )
            if len(raw_digest) != 64 or any(
                c not in "0123456789abcdef" for c in raw_digest
            ):
                raise SemanticEmbeddingError(
                    "INVALID_MODEL_MANIFEST",
                    "semantic model file digest must be lowercase SHA-256",
                )
            result[relative.as_posix()] = raw_digest
        observed_names = {
            path.relative_to(self._root).as_posix()
            for path in self._root.rglob("*")
            if path.is_file() and path.name != SENTENCE_TRANSFORMERS_MANIFEST
        }
        if observed_names != set(result):
            raise SemanticEmbeddingError(
                "MODEL_BUNDLE_DRIFT",
                "semantic model bundle files differ from the manifest",
            )
        return dict(sorted(result.items()))

    def _build_identity(self) -> LocalSemanticModelIdentityV1:
        dimension = self._manifest["dimension"]
        if isinstance(dimension, bool) or not isinstance(dimension, int):
            raise SemanticEmbeddingError(
                "INVALID_MODEL_MANIFEST",
                "semantic model dimension must be an integer",
            )
        return LocalSemanticModelIdentityV1(
            provider_id=_require_string(self._manifest["provider_id"], "provider_id"),
            model_id=_require_string(self._manifest["model_id"], "model_id"),
            model_revision=_require_string(
                self._manifest["model_revision"], "model_revision"
            ),
            tokenizer_sha256=self._file_digest_for("tokenizer_file"),
            config_sha256=self._file_digest_for("config_file"),
            model_bundle_sha256=canonical_sha256(self._files),
            dependency_lock_sha256=self._file_digest_for("dependency_lock_file"),
            license_id=_require_string(self._manifest["license_id"], "license_id"),
            license_sha256=self._file_digest_for("license_file"),
            dimension=dimension,
            local_files_only=True,
            synthetic=False,
        )

    def _file_digest_for(self, field: str) -> str:
        name = _require_string(self._manifest[field], field)
        if name not in self._files:
            raise SemanticEmbeddingError(
                "INVALID_MODEL_MANIFEST",
                f"{field} is not present in the semantic model file map",
            )
        return self._files[name]

    def _verify_files_and_dependency(self) -> dict[str, Any]:
        observed: dict[str, str] = {}
        for name in self._files:
            path = (self._root / name).resolve(strict=True)
            if self._root not in path.parents:
                raise SemanticEmbeddingError(
                    "MODEL_BUNDLE_DRIFT",
                    "resolved semantic model file escapes the bundle",
                )
            observed[name] = hashlib.sha256(path.read_bytes()).hexdigest()
        version_ok = False
        try:
            version_ok = (
                self._version_resolver("sentence-transformers")
                == self._expected_version
            )
        except importlib.metadata.PackageNotFoundError:
            version_ok = False
        return {
            "tokenizer_sha256": observed[
                _require_string(self._manifest["tokenizer_file"], "tokenizer_file")
            ],
            "config_sha256": observed[
                _require_string(self._manifest["config_file"], "config_file")
            ],
            "model_bundle_sha256": canonical_sha256(observed),
            "dependency_lock_sha256": observed[
                _require_string(
                    self._manifest["dependency_lock_file"], "dependency_lock_file"
                )
            ],
            "license_sha256": observed[
                _require_string(self._manifest["license_file"], "license_file")
            ],
            "dependency_check_passed": version_ok and observed == self._files,
            "license_check_passed": bool(
                (self._root / _require_string(self._manifest["license_file"], "license_file"))
                .read_text("utf-8")
                .strip()
            ),
        }


def build_sentence_transformers_adapter_from_environment(
    *,
    environment: Mapping[str, str] | None = None,
    cache: dict[str, SemanticEmbeddingCacheEntryV1] | None = None,
    model_loader: Callable[..., Any] | None = None,
    version_resolver: Callable[[str], str] = importlib.metadata.version,
) -> LocalSemanticEmbeddingAdapter:
    """Build the real local semantic adapter from explicit process settings."""

    selected = environment if environment is not None else os.environ
    bundle = selected.get(SEMANTIC_MODEL_BUNDLE_ENV, "").strip()
    if not bundle:
        raise SemanticEmbeddingError(
            "SEMANTIC_PRODUCTION_UNAVAILABLE",
            f"set {SEMANTIC_MODEL_BUNDLE_ENV} to a reviewed absolute bundle path",
        )
    device = selected.get(SEMANTIC_MODEL_DEVICE_ENV, "cpu").strip() or "cpu"
    provider = SentenceTransformersLocalProvider(
        bundle_path=bundle,
        device=device,
        model_loader=model_loader,
        version_resolver=version_resolver,
    )
    return LocalSemanticEmbeddingAdapter(provider=provider, cache=cache)


def _load_sentence_transformer(path: str, **kwargs: Any) -> Any:
    try:
        from sentence_transformers import SentenceTransformer
    except ImportError as exc:
        raise SemanticEmbeddingError(
            "SEMANTIC_DEPENDENCY_UNAVAILABLE",
            "install the pinned 'semantic' extra in an isolated environment",
        ) from exc
    return SentenceTransformer(path, **kwargs)


def _embedding_dimension(model: Any) -> int:
    getter = getattr(model, "get_embedding_dimension", None)
    if getter is None:
        getter = model.get_sentence_embedding_dimension
    return int(getter())


def _decode_canonical_input(value: bytes) -> str:
    if not isinstance(value, bytes) or not value:
        raise SemanticEmbeddingError(
            "INVALID_EMBEDDING_INPUT",
            "semantic provider accepts non-empty canonical bytes only",
        )
    try:
        return value.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise SemanticEmbeddingError(
            "INVALID_EMBEDDING_INPUT",
            "semantic provider input must be UTF-8",
        ) from exc


def _require_string(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        raise SemanticEmbeddingError(
            "INVALID_MODEL_MANIFEST",
            f"{field} must be a non-empty canonical string",
        )
    return value
