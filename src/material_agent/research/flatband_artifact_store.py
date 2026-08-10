"""Atomic private-custody storage for formal flat-band artifacts.

Schema definitions may live in the public repository; active private instances
must not.  This module writes a content-addressed envelope only to an explicit
caller-provided path, with mode ``0600`` and atomic replacement.  It refuses to
serialize blind keys, credentials, tokens or hidden reasoning fields.
"""

from __future__ import annotations

from datetime import datetime
import json
import os
from pathlib import Path
import re
import stat
import tempfile
from typing import Annotated, Literal, TypeVar

from pydantic import Field, model_validator

from material_agent.inspiration.models import (
    Identifier,
    Sha256,
    StrictModel,
    canonical_sha256,
    deterministic_id,
)


ModelT = TypeVar("ModelT", bound=StrictModel)
_FORBIDDEN_SECRET_KEYS = frozenset(
    {
        "api_key",
        "blind_key",
        "credential",
        "hidden_reasoning",
        "password",
        "private_key",
        "secret",
        "token",
    }
)
_FORBIDDEN_NORMALIZED_SECRET_FRAGMENTS = frozenset(
    {
        "annotationkey",
        "annotationkeys",
        "apikey",
        "apikeys",
        "authoritykey",
        "authoritykeys",
        "decisionkey",
        "decisionkeys",
        "decryptionkey",
        "decryptionkeys",
        "encryptionkey",
        "encryptionkeys",
        "ephemeralblindingkey",
        "ephemeralblindingkeys",
        "hmackey",
        "hmackeys",
        "keymaterial",
        "privatekey",
        "privatekeys",
        "secretkey",
        "secretkeys",
        "signingkey",
        "signingkeys",
    }
)
_SAFE_KEY_DIGEST_SUFFIXES = (
    "keycommitmentsha256",
    "signaturehmacsha256",
)
_SAFE_FALSE_KEY_METADATA_SUFFIXES = (
    "keyembedded",
    "keymaterialembedded",
    "keymaterialincluded",
)
_SAFE_TRUE_KEY_METADATA_SUFFIXES = (
    "keysverifiednotstored",
    "keyverifiednotstored",
)
_SAFE_UNAVAILABLE_KEY_METADATA_SUFFIXES = ("keycustodyattestation",)
_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")


def _timestamp(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("timestamp must include a UTC offset")
    return parsed


def _normalized_path_token(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", value.casefold())


def _is_safe_key_metadata_field(field: str, value: object) -> bool:
    normalized = _normalized_path_token(field)
    if normalized.endswith(_SAFE_KEY_DIGEST_SUFFIXES):
        return isinstance(value, str) and _SHA256_PATTERN.fullmatch(value) is not None
    if normalized.endswith(_SAFE_FALSE_KEY_METADATA_SUFFIXES):
        return value is False
    if normalized.endswith(_SAFE_TRUE_KEY_METADATA_SUFFIXES):
        return value is True
    if normalized.endswith(_SAFE_UNAVAILABLE_KEY_METADATA_SUFFIXES):
        return value == "NOT_PROVIDED"
    return False


def _secret_paths(value: object, path: tuple[str, ...] = ()) -> tuple[str, ...]:
    hits: list[str] = []
    if isinstance(value, dict):
        for key, nested in value.items():
            normalized = str(key).lower()
            current_path = (*path, str(key))
            normalized_path = _normalized_path_token(".".join(current_path))
            safe_metadata = _is_safe_key_metadata_field(str(key), nested)
            if not safe_metadata and (
                normalized in _FORBIDDEN_SECRET_KEYS
                or any(
                    normalized.endswith("_" + suffix)
                    for suffix in _FORBIDDEN_SECRET_KEYS
                )
                or any(
                    fragment in normalized_path
                    for fragment in _FORBIDDEN_NORMALIZED_SECRET_FRAGMENTS
                )
            ):
                hits.append(".".join((*path, str(key))))
            hits.extend(_secret_paths(nested, current_path))
    elif isinstance(value, (list, tuple)):
        for index, nested in enumerate(value):
            hits.extend(_secret_paths(nested, (*path, str(index))))
    return tuple(hits)


def _absolute_unresolved_path(path: str | Path) -> Path:
    expanded = Path(path).expanduser()
    return expanded if expanded.is_absolute() else Path.cwd() / expanded


def _assert_no_symlink_components(path: Path) -> None:
    """Reject every existing symlink component before resolving ``path``."""

    current = Path(path.anchor)
    for part in path.parts[1:]:
        current = current / part
        try:
            mode = current.lstat().st_mode
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(mode):
            raise ValueError("private artifact path cannot traverse a symlink")


class PrivateArtifactEnvelopeV1(StrictModel):
    schema_version: Literal["flatband-private-artifact-envelope-v1"] = (
        "flatband-private-artifact-envelope-v1"
    )
    envelope_id: Identifier
    envelope_sha256: Sha256
    artifact_model: Annotated[str, Field(min_length=3, max_length=256)]
    artifact_schema_version: Annotated[str, Field(min_length=3, max_length=128)]
    artifact_id_field: Annotated[str, Field(min_length=2, max_length=64)]
    artifact_sha_field: Annotated[str, Field(min_length=2, max_length=64)]
    artifact_id: Identifier
    artifact_sha256: Sha256
    payload_sha256: Sha256
    payload: dict[str, object]
    sealed_at: Annotated[str, Field(min_length=20, max_length=40)]
    audience: Literal["PRIVATE_CUSTODY"] = "PRIVATE_CUSTODY"
    filesystem_mode: Literal["0600"] = "0600"
    public_repository_release_allowed: Literal[False] = False
    credentials_embedded: Literal[False] = False
    hidden_reasoning_embedded: Literal[False] = False
    scientific_conclusion: Literal[False] = False

    @model_validator(mode="after")
    def validate_envelope(self) -> "PrivateArtifactEnvelopeV1":
        _timestamp(self.sealed_at)
        if _secret_paths(self.payload):
            raise ValueError("private artifact payload contains a forbidden secret field")
        payload_sha = canonical_sha256(self.payload)
        if self.payload_sha256 != payload_sha:
            raise ValueError("private artifact payload SHA-256 does not match")
        if self.payload.get(self.artifact_id_field) != self.artifact_id or (
            self.payload.get(self.artifact_sha_field) != self.artifact_sha256
        ):
            raise ValueError("private envelope identity differs from its payload")
        semantic = self.model_dump(
            mode="python", exclude={"envelope_id", "envelope_sha256"}
        )
        digest = canonical_sha256(semantic)
        if self.envelope_sha256 != digest:
            raise ValueError("private envelope SHA-256 does not match")
        if self.envelope_id != deterministic_id(
            "private-artifact-envelope-v1", {"envelope_sha256": digest}
        ):
            raise ValueError("private envelope ID does not match its SHA-256")
        return self


def seal_private_artifact_envelope_v1(
    artifact: StrictModel,
    *,
    id_field: str,
    sha_field: str,
    sealed_at: str,
) -> PrivateArtifactEnvelopeV1:
    """Seal one already-addressed model without accepting raw credentials."""

    value = type(artifact).model_validate(
        artifact.model_dump(mode="python", round_trip=True)
    )
    payload = value.model_dump(mode="json", round_trip=True)
    secret_paths = _secret_paths(payload)
    if secret_paths:
        raise ValueError(
            "private artifact payload contains forbidden secret fields: "
            + ",".join(secret_paths)
        )
    artifact_id = getattr(value, id_field, None)
    artifact_sha = getattr(value, sha_field, None)
    if not isinstance(artifact_id, str) or not isinstance(artifact_sha, str):
        raise ValueError("artifact lacks the requested content identity fields")
    schema_version = getattr(value, "schema_version", None)
    if not isinstance(schema_version, str):
        raise ValueError("artifact has no explicit schema version")
    values: dict[str, object] = {
        "artifact_model": f"{type(value).__module__}.{type(value).__qualname__}",
        "artifact_schema_version": schema_version,
        "artifact_id_field": id_field,
        "artifact_sha_field": sha_field,
        "artifact_id": artifact_id,
        "artifact_sha256": artifact_sha,
        "payload_sha256": canonical_sha256(payload),
        "payload": payload,
        "sealed_at": sealed_at,
    }
    draft = PrivateArtifactEnvelopeV1.model_construct(**values)
    digest = canonical_sha256(
        draft.model_dump(
            mode="python", exclude={"envelope_id", "envelope_sha256"}
        )
    )
    return PrivateArtifactEnvelopeV1.model_validate(
        {
            **values,
            "envelope_sha256": digest,
            "envelope_id": deterministic_id(
                "private-artifact-envelope-v1",
                {"envelope_sha256": digest},
            ),
        }
    )


def write_private_artifact_envelope_v1(
    path: str | Path,
    envelope: PrivateArtifactEnvelopeV1,
    *,
    overwrite: bool = False,
) -> Path:
    """Write one envelope atomically; active instances stay outside Git by policy."""

    value = PrivateArtifactEnvelopeV1.model_validate(
        envelope.model_dump(mode="python", round_trip=True)
    )
    unresolved_target = _absolute_unresolved_path(path)
    _assert_no_symlink_components(unresolved_target)
    target = unresolved_target.resolve()
    if target.suffix != ".json":
        raise ValueError("private artifact path must end in .json")
    if target.exists() and not overwrite:
        raise FileExistsError(target)
    target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    serialized = json.dumps(
        value.model_dump(mode="json", round_trip=True),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8") + b"\n"
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{target.name}.", suffix=".tmp", dir=target.parent
    )
    temporary = Path(temporary_name)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "wb", closefd=True) as stream:
            stream.write(serialized)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, target)
        os.chmod(target, 0o600)
    except BaseException:
        try:
            os.close(descriptor)
        except OSError:
            pass
        temporary.unlink(missing_ok=True)
        raise
    return target


def read_private_artifact_envelope_v1(
    path: str | Path,
    *,
    model_type: type[ModelT],
) -> tuple[PrivateArtifactEnvelopeV1, ModelT]:
    """Read, replay and type-check one private envelope."""

    unresolved_target = _absolute_unresolved_path(path)
    _assert_no_symlink_components(unresolved_target)
    target = unresolved_target.resolve(strict=True)
    mode = target.stat().st_mode & 0o777
    if mode & 0o077:
        raise PermissionError("private artifact file is readable outside its owner")
    envelope = PrivateArtifactEnvelopeV1.model_validate_json(
        target.read_text(encoding="utf-8")
    )
    expected_model = f"{model_type.__module__}.{model_type.__qualname__}"
    if envelope.artifact_model != expected_model:
        raise ValueError("private envelope contains a different artifact model")
    artifact = model_type.model_validate_json(
        json.dumps(
            envelope.payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    )
    if (
        getattr(artifact, envelope.artifact_id_field),
        getattr(artifact, envelope.artifact_sha_field),
    ) != (envelope.artifact_id, envelope.artifact_sha256):
        raise ValueError("private artifact identity does not replay")
    return envelope, artifact


__all__ = [
    "PrivateArtifactEnvelopeV1",
    "read_private_artifact_envelope_v1",
    "seal_private_artifact_envelope_v1",
    "write_private_artifact_envelope_v1",
]
