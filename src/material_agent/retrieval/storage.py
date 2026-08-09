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


class ArtifactConflictError(ArtifactStoreError):
    """Raised when immutable artifact content would be overwritten."""


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

    def _atomic_write(
        self,
        relative_path: str,
        payload: bytes,
        media_type: str,
        *,
        immutable: bool = False,
    ) -> ArtifactRef:
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
            if immutable:
                raise ArtifactConflictError(
                    f"immutable artifact already exists with different content: "
                    f"artifact://{relative_path}"
                )

        file_descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{destination.name}.", dir=destination.parent
        )
        try:
            with os.fdopen(file_descriptor, "wb") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            if immutable:
                try:
                    # Same-filesystem hard linking is an atomic
                    # create-if-absent operation.  It closes the race where two
                    # worker processes both pass the existence check and then
                    # replace one immutable run artifact with different bytes.
                    os.link(temporary_name, destination)
                except FileExistsError:
                    existing = destination.read_bytes()
                    if hashlib.sha256(existing).hexdigest() != digest:
                        raise ArtifactConflictError(
                            "immutable artifact already exists with different "
                            f"content: artifact://{relative_path}"
                        ) from None
            else:
                os.replace(temporary_name, destination)
            self._fsync_directory(destination.parent)
        finally:
            try:
                os.unlink(temporary_name)
            except FileNotFoundError:
                pass

        return ArtifactRef(
            uri=f"artifact://{relative_path}",
            sha256=digest,
            size_bytes=len(payload),
            media_type=media_type,
        )

    @staticmethod
    def _fsync_directory(directory: Path) -> None:
        """Persist a published directory entry before returning success."""

        flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
        descriptor = os.open(directory, flags)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

    def write_bytes(
        self,
        relative_path: str,
        payload: bytes,
        media_type: str = "application/octet-stream",
        *,
        immutable: bool = False,
    ) -> ArtifactRef:
        return self._atomic_write(
            relative_path, payload, media_type, immutable=immutable
        )

    def write_text(
        self,
        relative_path: str,
        text: str,
        media_type: str = "text/plain",
        *,
        immutable: bool = False,
    ) -> ArtifactRef:
        return self._atomic_write(
            relative_path,
            text.encode("utf-8"),
            media_type,
            immutable=immutable,
        )

    def write_json(
        self, relative_path: str, value: Any, *, immutable: bool = False
    ) -> ArtifactRef:
        return self._atomic_write(
            relative_path,
            canonical_json_bytes(value),
            "application/json",
            immutable=immutable,
        )

    def write_jsonl(
        self,
        relative_path: str,
        values: Iterable[Any],
        *,
        immutable: bool = False,
    ) -> ArtifactRef:
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
        return self._atomic_write(
            relative_path,
            payload,
            "application/x-ndjson",
            immutable=immutable,
        )

    def write_gzip_jsonl(
        self,
        relative_path: str,
        values: Iterable[Any],
        *,
        immutable: bool = False,
    ) -> ArtifactRef:
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
        return self._atomic_write(
            relative_path, payload, "application/gzip", immutable=immutable
        )

    def read_bytes(self, uri_or_relative_path: str) -> bytes:
        relative_path = uri_or_relative_path.removeprefix("artifact://")
        return self._resolve(relative_path).read_bytes()

    def read_json(self, uri_or_relative_path: str) -> Any:
        return json.loads(self.read_bytes(uri_or_relative_path).decode("utf-8"))

    def read_jsonl(self, uri_or_relative_path: str) -> list[Any]:
        text = self.read_bytes(uri_or_relative_path).decode("utf-8")
        return [json.loads(line) for line in text.splitlines() if line.strip()]

    def read_gzip_jsonl(self, uri_or_relative_path: str) -> list[Any]:
        raw = gzip.decompress(self.read_bytes(uri_or_relative_path)).decode("utf-8")
        return [json.loads(line) for line in raw.splitlines() if line.strip()]

    def exists(self, uri_or_relative_path: str) -> bool:
        relative_path = uri_or_relative_path.removeprefix("artifact://")
        return self._resolve(relative_path).is_file()

    def inspect(
        self,
        uri_or_relative_path: str,
        *,
        media_type: str = "application/octet-stream",
    ) -> ArtifactRef:
        relative_path = uri_or_relative_path.removeprefix("artifact://")
        path = self._resolve(relative_path)
        payload = path.read_bytes()
        return ArtifactRef(
            uri=f"artifact://{relative_path}",
            sha256=hashlib.sha256(payload).hexdigest(),
            size_bytes=len(payload),
            media_type=media_type,
        )

    def exists_with_hash(self, uri: str, expected_sha256: str) -> bool:
        path = self._resolve(uri.removeprefix("artifact://"))
        if not path.is_file():
            return False
        return hashlib.sha256(path.read_bytes()).hexdigest() == expected_sha256


def canonical_json_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=_json_default,
    ).encode("utf-8")


def _json_default(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if hasattr(value, "as_dict"):
        return value.as_dict()
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)
