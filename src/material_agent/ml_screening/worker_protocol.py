"""Validation helpers for the frozen Agent02 worker v1 boundary.

The artifact root is always supplied as a trusted runtime argument.  It is
deliberately absent from :class:`WorkerRequest`.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import NamedTuple

from material_agent.ml_screening.models import (
    MLNumericArtifactMetadata,
    MLNumericArtifactRef,
    WorkerInputArtifact,
    WorkerProducedArtifact,
    WorkerRequest,
    WorkerResponse,
)


class FileSnapshot(NamedTuple):
    sha256: str
    size_bytes: int


def capture_artifact_tree(artifact_root: Path) -> dict[str, FileSnapshot]:
    root = artifact_root.resolve(strict=True)
    snapshot: dict[str, FileSnapshot] = {}
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            raise ValueError(f"artifact tree contains symlink: {path}")
        if path.is_file():
            relative = path.relative_to(root).as_posix()
            snapshot[relative] = FileSnapshot(
                sha256=_sha256_file(path),
                size_bytes=path.stat().st_size,
            )
    return snapshot


def validate_worker_inputs(
    request: WorkerRequest,
    artifact_root: Path,
) -> list[Path]:
    return [
        _verify_input_artifact(artifact, artifact_root)
        for artifact in request.inputs
    ]


def validate_worker_outputs(
    *,
    request: WorkerRequest,
    response: WorkerResponse,
    artifact_root: Path,
    before_snapshot: dict[str, FileSnapshot] | None = None,
) -> list[MLNumericArtifactRef]:
    """Re-read every worker output and reconstruct numeric metadata."""

    response.validate_against_request(request)
    root = artifact_root.resolve(strict=True)
    sandbox = _resolve_relative(
        root,
        request.output_sandbox_relative_path,
        must_exist=True,
        require_directory=True,
    )
    verified_numeric: list[MLNumericArtifactRef] = []
    total_size = 0
    reported_paths = {
        artifact.root_relative_path
        for artifact in response.produced_artifacts
    }

    for artifact in response.produced_artifacts:
        path = _resolve_relative(root, artifact.root_relative_path)
        if sandbox not in path.parents:
            raise ValueError("worker artifact escaped its output sandbox")
        stat = path.stat()
        if stat.st_size != artifact.size_bytes:
            raise ValueError("worker artifact size does not match response")
        if stat.st_size > request.limits.max_single_artifact_bytes:
            raise ValueError("worker artifact exceeds single-artifact limit")
        total_size += stat.st_size
        if _sha256_file(path) != artifact.sha256:
            raise ValueError("worker artifact hash does not match response")
        if artifact.numeric_metadata is not None:
            verified_numeric.append(
                _verify_numeric_artifact(path, artifact)
            )

    if total_size > request.limits.max_total_output_bytes:
        raise ValueError("worker artifacts exceed total output limit")

    actual_sandbox_files = {
        path.relative_to(root).as_posix()
        for path in sandbox.rglob("*")
        if path.is_file()
    }
    if actual_sandbox_files != reported_paths:
        raise ValueError(
            "worker sandbox contains unreported or missing output files"
        )

    if before_snapshot is not None:
        after_snapshot = capture_artifact_tree(root)
        changed = {
            path
            for path in set(before_snapshot) | set(after_snapshot)
            if before_snapshot.get(path) != after_snapshot.get(path)
        }
        if any(path not in reported_paths for path in changed):
            raise ValueError("worker wrote outside its declared sandbox outputs")
    _validate_result_artifact_references(response, response.produced_artifacts)
    return verified_numeric


def _validate_result_artifact_references(
    response: WorkerResponse,
    produced_artifacts: list[WorkerProducedArtifact],
) -> None:
    result = response.candidate_result
    if result is None or result.execution_identity.is_mock:
        return
    produced = {
        f"artifact://{item.root_relative_path}": item
        for item in produced_artifacts
    }
    for prop in result.ml_properties:
        ref = prop.artifact_ref
        if ref is None:
            continue
        artifact = produced.get(ref.uri)
        if (
            artifact is None
            or artifact.sha256 != ref.sha256
            or artifact.size_bytes != ref.size_bytes
            or artifact.numeric_metadata is None
            or artifact.numeric_metadata.array_key != ref.array_key
            or artifact.numeric_metadata.dtype != ref.dtype
            or artifact.numeric_metadata.shape != ref.shape
        ):
            raise ValueError(
                "worker result numeric reference differs from produced artifact"
            )
    relaxation = result.relaxation_result
    if relaxation is not None and relaxation.output_structure_uri is not None:
        artifact = produced.get(relaxation.output_structure_uri)
        if (
            artifact is None
            or artifact.sha256 != relaxation.output_structure_sha256
            or artifact.media_type != "chemical/x-cif"
        ):
            raise ValueError(
                "worker result structure reference differs from produced artifact"
            )


def parse_worker_stdout(
    stdout: bytes,
    *,
    max_stdout_bytes: int,
) -> WorkerResponse:
    if len(stdout) > max_stdout_bytes:
        raise ValueError("worker stdout exceeds configured limit")
    try:
        text = stdout.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError("worker stdout is not UTF-8") from exc
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError("worker stdout must contain exactly one JSON value") from exc
    if not isinstance(payload, dict):
        raise ValueError("worker stdout JSON must be an object")
    return WorkerResponse.model_validate(payload)


def _verify_input_artifact(
    artifact: WorkerInputArtifact,
    artifact_root: Path,
) -> Path:
    root = artifact_root.resolve(strict=True)
    path = _resolve_relative(root, artifact.root_relative_path)
    stat = path.stat()
    if stat.st_size != artifact.size_bytes:
        raise ValueError("worker input size does not match request")
    if _sha256_file(path) != artifact.sha256:
        raise ValueError("worker input hash does not match request")
    return path


def _verify_numeric_artifact(
    path: Path,
    artifact: WorkerProducedArtifact,
) -> MLNumericArtifactRef:
    metadata = artifact.numeric_metadata
    assert metadata is not None
    rebuilt = _read_npz_metadata(path, metadata.array_key)
    if rebuilt != metadata:
        raise ValueError("worker numeric metadata differs from artifact contents")
    return MLNumericArtifactRef(
        uri=artifact.root_relative_path,
        sha256=artifact.sha256,
        size_bytes=artifact.size_bytes,
        media_type="application/x-npz",
        array_key=rebuilt.array_key,
        dtype=rebuilt.dtype,
        shape=rebuilt.shape,
        allow_pickle=False,
    )


def _read_npz_metadata(
    path: Path,
    expected_array_key: str,
) -> MLNumericArtifactMetadata:
    import numpy

    try:
        with numpy.load(path, allow_pickle=False) as archive:
            if archive.files != [expected_array_key]:
                raise ValueError(
                    "numeric artifact must contain exactly the declared array"
                )
            array = archive[expected_array_key]
            if array.dtype.hasobject:
                raise ValueError("object/pickle numeric arrays are forbidden")
            if array.size == 0:
                raise ValueError("numeric arrays cannot be empty")
            if not numpy.issubdtype(array.dtype, numpy.number):
                raise ValueError("numeric artifacts must contain numeric arrays")
            if not numpy.isfinite(array).all():
                raise ValueError("numeric artifacts must contain finite values")
            return MLNumericArtifactMetadata(
                array_key=expected_array_key,
                dtype=array.dtype.name,
                shape=list(array.shape),
                allow_pickle=False,
            )
    except ValueError:
        raise
    except Exception as exc:
        raise ValueError("numeric artifact could not be read safely") from exc


def _resolve_relative(
    root: Path,
    relative_path: str,
    *,
    must_exist: bool = True,
    require_directory: bool = False,
) -> Path:
    candidate = root.joinpath(*relative_path.split("/"))
    current = root
    for part in relative_path.split("/"):
        current = current / part
        if current.exists() and current.is_symlink():
            raise ValueError("symlinks are forbidden at the worker boundary")
    resolved = candidate.resolve(strict=must_exist)
    if resolved != root and root not in resolved.parents:
        raise ValueError("worker path escapes the trusted artifact root")
    if require_directory and not resolved.is_dir():
        raise ValueError("worker output sandbox must be a directory")
    if not require_directory and not resolved.is_file():
        raise ValueError("worker artifact path must reference a file")
    return resolved


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()
