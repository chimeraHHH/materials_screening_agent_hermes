"""Content-addressed, path-guarded local artifact storage."""

from __future__ import annotations

import gzip
import hashlib
import json
import os
import tempfile
from pathlib import Path
from typing import Any, Iterable

from material_agent.retrieval.models import ArtifactRef


class ArtifactStoreError(RuntimeError):
    """Raised when an artifact operation violates storage invariants."""


class LocalArtifactStore:
    """Write artifacts atomically beneath a single project root."""

    def __init__(self, root: Path | str) -> None:
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def _resolve(self, relative_path: str) -> Path:
        if not relative_path or relative_path.startswith("artifact://"):
            raise ArtifactStoreError("a non-empty relative path is required")
        candidate = (self.root / relative_path).resolve()
        if candidate != self.root and self.root not in candidate.parents:
            raise ArtifactStoreError(f"artifact path escapes store root: {relative_path}")
        return candidate

    def _atomic_write(self, relative_path: str, payload: bytes, media_type: str) -> ArtifactRef:
        destination = self._resolve(relative_path)
        destination.parent.mkdir(parents=True, exist_ok=True)
        digest = hashlib.sha256(payload).hexdigest()

        if destination.exists():
            existing = destination.read_bytes()
            if hashlib.sha256(existing).hexdigest() == digest:
                return ArtifactRef(
                    uri=f"artifact://{relative_path}",
                    sha256=digest,
                    size_bytes=len(payload),
                    media_type=media_type,
                )

        file_descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{destination.name}.", dir=destination.parent
        )
        try:
            with os.fdopen(file_descriptor, "wb") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary_name, destination)
        except Exception:
            try:
                os.unlink(temporary_name)
            except FileNotFoundError:
                pass
            raise

        return ArtifactRef(
            uri=f"artifact://{relative_path}",
            sha256=digest,
            size_bytes=len(payload),
            media_type=media_type,
        )

    def write_bytes(
        self, relative_path: str, payload: bytes, media_type: str = "application/octet-stream"
    ) -> ArtifactRef:
        return self._atomic_write(relative_path, payload, media_type)

    def write_text(
        self, relative_path: str, text: str, media_type: str = "text/plain"
    ) -> ArtifactRef:
        return self._atomic_write(relative_path, text.encode("utf-8"), media_type)

    def write_json(self, relative_path: str, value: Any) -> ArtifactRef:
        payload = json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=_json_default,
        ).encode("utf-8")
        return self._atomic_write(relative_path, payload, "application/json")

    def write_jsonl(self, relative_path: str, values: Iterable[Any]) -> ArtifactRef:
        lines = [
            json.dumps(
                value,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                default=_json_default,
            )
            for value in values
        ]
        payload = (("\n".join(lines) + "\n") if lines else "").encode("utf-8")
        return self._atomic_write(relative_path, payload, "application/x-ndjson")

    def write_gzip_jsonl(self, relative_path: str, values: Iterable[Any]) -> ArtifactRef:
        lines = [
            json.dumps(
                value,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                default=_json_default,
            )
            for value in values
        ]
        raw = (("\n".join(lines) + "\n") if lines else "").encode("utf-8")
        payload = gzip.compress(raw, mtime=0)
        return self._atomic_write(relative_path, payload, "application/gzip")

    def read_json(self, uri_or_relative_path: str) -> Any:
        relative_path = uri_or_relative_path.removeprefix("artifact://")
        return json.loads(self._resolve(relative_path).read_text(encoding="utf-8"))

    def exists_with_hash(self, uri: str, expected_sha256: str) -> bool:
        path = self._resolve(uri.removeprefix("artifact://"))
        if not path.is_file():
            return False
        return hashlib.sha256(path.read_bytes()).hexdigest() == expected_sha256


def _json_default(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if hasattr(value, "as_dict"):
        return value.as_dict()
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)

