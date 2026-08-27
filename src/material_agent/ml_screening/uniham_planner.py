"""Pure planning and immutable-input validation for Uni-HamGNN."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

from material_agent.ml_screening.uniham_models import (
    UniHamArtifactFile,
    UniHamExecutionPlan,
    UniHamGraphBundle,
    UniHamGraphManifest,
    UniHamInferenceRequest,
    UniHamSocMode,
    uniham_operation_key,
)


def build_uniham_plan(
    request: UniHamInferenceRequest,
    *,
    artifact_root: Path,
    created_at: datetime | None = None,
) -> UniHamExecutionPlan:
    root = artifact_root.resolve(strict=True)
    if not root.is_dir():
        raise ValueError("Uni-HamGNN artifact root must be a directory")
    for artifact in _request_artifacts(request):
        _validate_artifact(artifact, root)
    non_soc = _load_manifest(request.non_soc_graph, root)
    soc = _load_manifest(request.soc_graph, root)
    return build_uniham_plan_from_manifests(
        request,
        non_soc_manifest=non_soc,
        soc_manifest=soc,
        created_at=created_at,
    )


def build_uniham_plan_from_manifests(
    request: UniHamInferenceRequest,
    *,
    non_soc_manifest: UniHamGraphManifest,
    soc_manifest: UniHamGraphManifest,
    created_at: datetime | None = None,
) -> UniHamExecutionPlan:
    """Build a plan for already remote-verified/precomputed input artifacts."""

    _validate_manifest_linkage(request, non_soc_manifest, soc_manifest)
    operation_key = uniham_operation_key(request)
    return UniHamExecutionPlan(
        request=request,
        non_soc_manifest=non_soc_manifest,
        soc_manifest=soc_manifest,
        operation_key=operation_key,
        output_sandbox_relative_path=(
            f"stages/agent02/{request.run_id}/uniham/"
            f"{operation_key}/worker-output"
        ),
        created_at=created_at or datetime.now(UTC),
    )


def _request_artifacts(
    request: UniHamInferenceRequest,
) -> list[UniHamArtifactFile]:
    artifacts = [
        request.input_structure,
        request.model_pickle,
        request.non_soc_graph.graph_data,
        request.non_soc_graph.manifest,
        request.soc_graph.graph_data,
        request.soc_graph.manifest,
    ]
    if request.benchmark is not None:
        artifacts.append(request.benchmark.report_artifact)
    return artifacts


def _load_manifest(
    bundle: UniHamGraphBundle, root: Path
) -> UniHamGraphManifest:
    path = root.joinpath(*bundle.manifest.root_relative_path.split("/"))
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError("Uni-HamGNN graph manifest is not valid JSON") from exc
    return UniHamGraphManifest.model_validate(payload)


def _validate_manifest_linkage(
    request: UniHamInferenceRequest,
    non_soc: UniHamGraphManifest,
    soc: UniHamGraphManifest,
) -> None:
    if non_soc.soc_mode is not UniHamSocMode.NON_SOC:
        raise ValueError("non-SOC bundle manifest has the wrong SOC mode")
    if soc.soc_mode is not UniHamSocMode.SOC:
        raise ValueError("SOC bundle manifest has the wrong SOC mode")
    for manifest, bundle in (
        (non_soc, request.non_soc_graph),
        (soc, request.soc_graph),
    ):
        if manifest.structure_sha256 != request.input_structure.sha256:
            raise ValueError("Uni-HamGNN graph structure hash mismatch")
        if manifest.graph_data_sha256 != bundle.graph_data.sha256:
            raise ValueError("Uni-HamGNN graph-data hash mismatch")
    comparable = (
        "interface",
        "basis_id",
        "dft_data_version",
        "graph_generator_revision",
        "nao_max",
    )
    if any(getattr(non_soc, key) != getattr(soc, key) for key in comparable):
        raise ValueError("non-SOC and SOC graph preprocessing is incompatible")


def _validate_artifact(artifact: UniHamArtifactFile, root: Path) -> None:
    path = root.joinpath(*artifact.root_relative_path.split("/"))
    current = root
    for part in artifact.root_relative_path.split("/"):
        current = current / part
        if current.exists() and current.is_symlink():
            raise ValueError("Uni-HamGNN input paths cannot contain symlinks")
    resolved = path.resolve(strict=True)
    if root not in resolved.parents or not resolved.is_file():
        raise ValueError("Uni-HamGNN input must be a file below project root")
    if resolved.stat().st_size != artifact.size_bytes:
        raise ValueError("Uni-HamGNN input artifact size mismatch")
    if _sha256_file(resolved) != artifact.sha256:
        raise ValueError("Uni-HamGNN input artifact hash mismatch")


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()
