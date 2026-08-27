from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path

import pytest

from material_agent.inspiration.models import canonical_sha256
from material_agent.inspiration.semantic_embedding import (
    LocalSemanticEmbeddingAdapter,
    SemanticEmbeddingError,
    SemanticPassageInputV1,
)
from material_agent.inspiration.sentence_transformers_provider import (
    SEMANTIC_MODEL_BUNDLE_ENV,
    SENTENCE_TRANSFORMERS_BUNDLE_SCHEMA,
    SENTENCE_TRANSFORMERS_MANIFEST,
    SentenceTransformersLocalProvider,
    build_sentence_transformers_adapter_from_environment,
)


class _Tokenizer:
    def encode(self, text: str, *, add_special_tokens: bool) -> list[int]:
        assert add_special_tokens is True
        return list(range(max(1, len(text.split()))))


class _Model:
    tokenizer = _Tokenizer()

    def get_sentence_embedding_dimension(self) -> int:
        return 8

    def encode(self, texts: list[str], **kwargs: object) -> list[list[float]]:
        assert len(texts) == 1
        assert kwargs == {
            "normalize_embeddings": True,
            "convert_to_numpy": True,
            "show_progress_bar": False,
        }
        value = 1.0 / math.sqrt(8.0)
        return [[value] * 8]


def _bundle(tmp_path: Path) -> Path:
    root = tmp_path / "semantic-bundle"
    root.mkdir(parents=True)
    content = {
        "tokenizer.json": b"tokenizer-v1",
        "config.json": b"config-v1",
        "requirements.lock": b"sentence-transformers==5.6.1\n",
        "LICENSE": b"Apache License 2.0\n",
        "model.safetensors": b"model-weights-v1",
    }
    files: dict[str, str] = {}
    for name, body in content.items():
        (root / name).write_bytes(body)
        files[name] = hashlib.sha256(body).hexdigest()
    manifest = {
        "schema_version": SENTENCE_TRANSFORMERS_BUNDLE_SCHEMA,
        "provider_id": "sentence-transformers-local",
        "model_id": "reviewed-materials-passage-model",
        "model_revision": "revision-1",
        "dimension": 8,
        "license_id": "Apache-2.0",
        "sentence_transformers_version": "5.6.1",
        "tokenizer_file": "tokenizer.json",
        "config_file": "config.json",
        "dependency_lock_file": "requirements.lock",
        "license_file": "LICENSE",
        "files": files,
    }
    (root / SENTENCE_TRANSFORMERS_MANIFEST).write_text(
        json.dumps(manifest, sort_keys=True), "utf-8"
    )
    return root


def _loader(calls: list[tuple[str, dict[str, object]]]):
    def load(path: str, **kwargs: object) -> _Model:
        calls.append((path, kwargs))
        return _Model()

    return load


def test_real_local_provider_binds_bundle_dependency_and_model_output(
    tmp_path: Path,
) -> None:
    root = _bundle(tmp_path)
    calls: list[tuple[str, dict[str, object]]] = []
    provider = SentenceTransformersLocalProvider(
        bundle_path=root,
        model_loader=_loader(calls),
        version_resolver=lambda name: "5.6.1",
    )
    adapter = LocalSemanticEmbeddingAdapter(provider=provider)
    result = adapter.embed_one(
        SemanticPassageInputV1(
            title="Localized electronic state",
            section_heading="Results",
            selected_passage="A local motif creates a narrow band.",
            normalized_tags=("flat-band",),
        ),
        max_input_tokens=100,
    )

    assert calls == [
        (
            str(root),
            {
                "device": "cpu",
                "local_files_only": True,
                "trust_remote_code": False,
            },
        )
    ]
    assert result.dimension == 8
    assert result.model_identity.synthetic is False
    assert result.model_identity.model_bundle_sha256 == canonical_sha256(
        json.loads((root / SENTENCE_TRANSFORMERS_MANIFEST).read_text("utf-8"))["files"]
    )


def test_local_provider_rejects_bundle_drift_and_dependency_drift(tmp_path: Path) -> None:
    root = _bundle(tmp_path)
    (root / "unexpected.bin").write_bytes(b"drift")
    with pytest.raises(SemanticEmbeddingError) as files:
        SentenceTransformersLocalProvider(
            bundle_path=root,
            model_loader=lambda *args, **kwargs: _Model(),
            version_resolver=lambda name: "5.6.1",
        )
    assert files.value.code == "MODEL_BUNDLE_DRIFT"

    root = _bundle(tmp_path / "second")
    provider = SentenceTransformersLocalProvider(
        bundle_path=root,
        model_loader=lambda *args, **kwargs: _Model(),
        version_resolver=lambda name: "5.5.0",
    )
    with pytest.raises(SemanticEmbeddingError) as dependency:
        LocalSemanticEmbeddingAdapter(provider=provider)
    assert dependency.value.code == "DEPENDENCY_CHECK_FAILED"


def test_local_provider_rejects_relative_path(tmp_path: Path) -> None:
    with pytest.raises(SemanticEmbeddingError) as error:
        SentenceTransformersLocalProvider(bundle_path=Path("relative-model"))
    assert error.value.code == "INVALID_MODEL_BUNDLE"


def test_environment_factory_is_explicit_and_never_downloads(tmp_path: Path) -> None:
    root = _bundle(tmp_path)
    calls: list[tuple[str, dict[str, object]]] = []
    adapter = build_sentence_transformers_adapter_from_environment(
        environment={SEMANTIC_MODEL_BUNDLE_ENV: str(root)},
        model_loader=_loader(calls),
        version_resolver=lambda name: "5.6.1",
    )
    assert adapter.identity.model_id == "reviewed-materials-passage-model"
    assert calls[0][1]["local_files_only"] is True
    assert calls[0][1]["trust_remote_code"] is False

    with pytest.raises(SemanticEmbeddingError) as missing:
        build_sentence_transformers_adapter_from_environment(environment={})
    assert missing.value.code == "SEMANTIC_PRODUCTION_UNAVAILABLE"
