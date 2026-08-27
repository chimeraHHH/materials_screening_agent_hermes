"""Local-only SPECTER2 ranking for fine-grained literature evidence spans."""

from __future__ import annotations

import hashlib
import json
import math
import os
from collections.abc import Sequence
from pathlib import Path
from typing import Any, Protocol

from material_agent.inspiration.research_graph import FineGrainedEvidenceSpanV1

SPECTER2_BUNDLE_ENV = "SPECTER2_LOCAL_BUNDLE"
SPECTER2_MANIFEST = "specter2-manifest-v1.json"


class Specter2Provider(Protocol):
    model_identity: str

    def encode(self, texts: Sequence[str]) -> Sequence[Sequence[float]]: ...


class Specter2EvidenceRanker:
    def __init__(self, provider: Specter2Provider) -> None:
        if not provider.model_identity.startswith("specter2:"):
            raise ValueError("provider must expose a SPECTER2 identity")
        self.provider = provider

    def rank_spans(
        self,
        query: str,
        spans: tuple[FineGrainedEvidenceSpanV1, ...],
        *,
        max_results: int = 64,
    ) -> tuple[FineGrainedEvidenceSpanV1, ...]:
        if not spans:
            return ()
        texts = [query, *(span.text_excerpt for span in spans)]
        vectors = self.provider.encode(texts)
        if len(vectors) != len(texts):
            raise ValueError("SPECTER2 provider returned the wrong vector count")
        query_vector = _normalized(vectors[0])
        scored = [
            (_cosine(query_vector, _normalized(vector)), index, span)
            for index, (span, vector) in enumerate(zip(spans, vectors[1:]))
        ]
        scored.sort(key=lambda item: (-item[0], item[1]))
        return tuple(item[2] for item in scored[:max_results])


class LocalSpecter2Provider:
    """Load a SHA-manifested SPECTER2 base + proximity adapter offline."""

    def __init__(
        self,
        bundle_path: Path | str,
        *,
        tokenizer: Any | None = None,
        model: Any | None = None,
    ) -> None:
        root = Path(bundle_path).expanduser().resolve(strict=True)
        manifest_path = root / SPECTER2_MANIFEST
        manifest = json.loads(manifest_path.read_text("utf-8"))
        if not isinstance(manifest, dict) or manifest.get("schema_version") != (
            "specter2-local-bundle-v1"
        ):
            raise ValueError("invalid SPECTER2 local manifest")
        files = manifest.get("files")
        if not isinstance(files, dict) or not files:
            raise ValueError("SPECTER2 manifest requires file hashes")
        for relative_name, expected in files.items():
            if not isinstance(relative_name, str) or not isinstance(expected, str):
                raise TypeError("invalid SPECTER2 file manifest")
            relative = Path(relative_name)
            if relative.is_absolute() or ".." in relative.parts:
                raise ValueError("SPECTER2 manifest path escapes bundle")
            observed = hashlib.sha256((root / relative).read_bytes()).hexdigest()
            if observed != expected:
                raise ValueError("SPECTER2 bundle hash mismatch")
        base_dir = root / str(manifest.get("base_model_dir", "base"))
        adapter_dir = root / str(manifest.get("adapter_dir", "proximity"))
        if tokenizer is None or model is None:
            try:
                from adapters import AutoAdapterModel
                from transformers import AutoTokenizer
            except ImportError as exc:
                raise RuntimeError(
                    "SPECTER2 requires local transformers and adapters dependencies"
                ) from exc
            tokenizer = AutoTokenizer.from_pretrained(
                base_dir, local_files_only=True, trust_remote_code=False
            )
            model = AutoAdapterModel.from_pretrained(
                base_dir, local_files_only=True, trust_remote_code=False
            )
            model.load_adapter(
                str(adapter_dir),
                source="hf",
                load_as="specter2_proximity",
                set_active=True,
            )
            model.eval()
        self.tokenizer = tokenizer
        self.model = model
        bundle_hash = hashlib.sha256(
            json.dumps(files, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        self.model_identity = f"specter2:{bundle_hash}"

    def encode(self, texts: Sequence[str]) -> Sequence[Sequence[float]]:
        if not texts or len(texts) > 257:
            raise ValueError("SPECTER2 accepts one to 257 bounded texts")
        bounded = [" ".join(text.split())[:2_000] for text in texts]
        try:
            import torch
        except ImportError as exc:
            raise RuntimeError("SPECTER2 requires torch") from exc
        inputs = self.tokenizer(
            bounded,
            padding=True,
            truncation=True,
            max_length=512,
            return_tensors="pt",
        )
        with torch.no_grad():
            outputs = self.model(**inputs)
        vectors = outputs.last_hidden_state[:, 0, :].detach().cpu().tolist()
        return tuple(tuple(float(value) for value in vector) for vector in vectors)


def specter2_ranker_from_environment(
    environment: dict[str, str] | None = None,
) -> Specter2EvidenceRanker | None:
    selected = dict(os.environ if environment is None else environment)
    path = selected.get(SPECTER2_BUNDLE_ENV, "").strip()
    return Specter2EvidenceRanker(LocalSpecter2Provider(path)) if path else None


def _normalized(values: Sequence[float]) -> tuple[float, ...]:
    vector = tuple(float(value) for value in values)
    if not vector or any(not math.isfinite(value) for value in vector):
        raise ValueError("SPECTER2 vector is empty or non-finite")
    norm = math.sqrt(sum(value * value for value in vector))
    if norm == 0:
        raise ValueError("SPECTER2 vector has zero norm")
    return tuple(value / norm for value in vector)


def _cosine(left: Sequence[float], right: Sequence[float]) -> float:
    if len(left) != len(right):
        raise ValueError("SPECTER2 vector dimensions differ")
    return sum(a * b for a, b in zip(left, right))
