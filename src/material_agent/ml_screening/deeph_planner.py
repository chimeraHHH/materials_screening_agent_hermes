"""Pure planning and immutable input validation for the DeepH companion."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from pathlib import Path

from material_agent.ml_screening.deeph_models import (
    DeepHArtifactFile,
    DeepHExecutionPlan,
    DeepHInferenceRequest,
    deeph_operation_key,
)


def build_deeph_plan(
    request: DeepHInferenceRequest,
    *,
    artifact_root: Path,
    created_at: datetime | None = None,
) -> DeepHExecutionPlan:
    root = artifact_root.resolve(strict=True)
    if not root.is_dir():
        raise ValueError("DeepH artifact root must be a directory")
    _validate_directory(request.trained_model.root_relative_directory, root)
    _validate_directory(request.overlap.root_relative_directory, root)
    for artifact in _request_artifacts(request):
        _validate_artifact(artifact, root)
    operation_key = deeph_operation_key(request)
    return DeepHExecutionPlan(
        request=request,
        operation_key=operation_key,
        output_sandbox_relative_path=(
            f"stages/agent02/{request.run_id}/deeph/"
            f"{operation_key}/worker-output"
        ),
        created_at=created_at or datetime.now(UTC),
    )


def _request_artifacts(
    request: DeepHInferenceRequest,
) -> list[DeepHArtifactFile]:
    return [
        request.input_structure,
        *request.trained_model.files,
        *request.overlap.files,
    ]


def _validate_artifact(artifact: DeepHArtifactFile, root: Path) -> None:
    path = root.joinpath(*artifact.root_relative_path.split("/"))
    current = root
    for part in artifact.root_relative_path.split("/"):
        current = current / part
        if current.exists() and current.is_symlink():
            raise ValueError("DeepH input paths cannot contain symlinks")
    resolved = path.resolve(strict=True)
    if root not in resolved.parents:
        raise ValueError("DeepH input path escapes artifact root")
    if not resolved.is_file():
        raise ValueError("DeepH input artifact must be a file")
    stat = resolved.stat()
    if stat.st_size != artifact.size_bytes:
        raise ValueError("DeepH input artifact size mismatch")
    if _sha256_file(resolved) != artifact.sha256:
        raise ValueError("DeepH input artifact hash mismatch")


def _validate_directory(relative: str, root: Path) -> None:
    path = root.joinpath(*relative.split("/"))
    current = root
    for part in relative.split("/"):
        current = current / part
        if current.exists() and current.is_symlink():
            raise ValueError("DeepH input directories cannot contain symlinks")
    resolved = path.resolve(strict=True)
    if root not in resolved.parents or not resolved.is_dir():
        raise ValueError("DeepH bundle root must be a directory below project root")


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()
