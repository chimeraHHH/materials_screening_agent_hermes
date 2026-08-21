"""Planning and artifact integrity checks for the ALIGNN companion flow."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from pathlib import Path

from material_agent.ml_screening.alignn_models import (
    AlignnExecutionPlan,
    AlignnInferenceRequest,
    alignn_operation_key,
)


def build_alignn_plan(request: AlignnInferenceRequest, *, artifact_root: Path, created_at: datetime | None = None) -> AlignnExecutionPlan:
    root = artifact_root.resolve(strict=True)
    for artifact in (request.input_structure, request.model_archive):
        path = root.joinpath(*artifact.root_relative_path.split("/"))
        if not path.is_file() or path.is_symlink() or path.stat().st_size != artifact.size_bytes:
            raise ValueError("ALIGNN input artifact is missing, unsafe, or has wrong size")
        if _sha256(path) != artifact.sha256:
            raise ValueError("ALIGNN input artifact hash mismatch")
    key = alignn_operation_key(request)
    return AlignnExecutionPlan(request=request, operation_key=key, output_sandbox_relative_path=f"stages/agent02/{request.run_id}/alignn/{key}/worker-output", created_at=created_at or datetime.now(UTC))


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
