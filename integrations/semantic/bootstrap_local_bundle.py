#!/usr/bin/env python3
"""Prepare the pinned local-only Sentence Transformers engineering bundle."""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import shutil
import tempfile
from pathlib import Path

from huggingface_hub import hf_hub_download
from sentence_transformers import SentenceTransformer

MODEL_ID = "sentence-transformers/all-MiniLM-L6-v2"
MODEL_REVISION = "1110a243fdf4706b3f48f1d95db1a4f5529b4d41"
SCHEMA_VERSION = "sentence-transformers-local-bundle-v1"
MANIFEST_NAME = "semantic-model-manifest-v1.json"
DEPENDENCIES = (
    "sentence-transformers",
    "torch",
    "transformers",
    "tokenizers",
    "numpy",
    "huggingface-hub",
    "safetensors",
)


def prepare(output: Path) -> Path:
    output = output.expanduser()
    if not output.is_absolute():
        raise ValueError("--output must be an absolute path")
    if output.exists() and any(output.iterdir()):
        raise ValueError("--output must not exist or must be an empty directory")
    output.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix="materials-semantic-bootstrap-") as cache:
        model = SentenceTransformer(
            MODEL_ID,
            revision=MODEL_REVISION,
            cache_folder=cache,
            device="cpu",
            trust_remote_code=False,
        )
        model.save_pretrained(str(output), safe_serialization=True)
        model_card_source = Path(
            hf_hub_download(
                repo_id=MODEL_ID,
                filename="README.md",
                revision=MODEL_REVISION,
                cache_dir=cache,
            )
        )
        shutil.copyfile(model_card_source, output / "MODEL_CARD.md")

    dependency_lines = tuple(
        f"{name}=={importlib.metadata.version(name)}" for name in DEPENDENCIES
    )
    (output / "requirements.lock").write_text(
        "\n".join(dependency_lines) + "\n", encoding="utf-8"
    )
    files = {
        path.relative_to(output).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(output.rglob("*"))
        if path.is_file() and path.name != MANIFEST_NAME
    }
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "provider_id": "sentence-transformers-local",
        "model_id": MODEL_ID,
        "model_revision": MODEL_REVISION,
        "dimension": int(
            (
                model.get_embedding_dimension()
                if hasattr(model, "get_embedding_dimension")
                else model.get_sentence_embedding_dimension()
            )
        ),
        "license_id": "Apache-2.0",
        "sentence_transformers_version": importlib.metadata.version(
            "sentence-transformers"
        ),
        "tokenizer_file": "tokenizer.json",
        "config_file": "config.json",
        "dependency_lock_file": "requirements.lock",
        "license_file": "MODEL_CARD.md",
        "files": files,
    }
    (output / MANIFEST_NAME).write_text(
        json.dumps(manifest, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n",
        encoding="utf-8",
    )
    return output


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    bundle = prepare(args.output)
    print(f"semantic bundle ready: {bundle}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
