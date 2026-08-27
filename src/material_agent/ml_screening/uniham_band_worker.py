"""Strict companion worker for HamGNN band_cal postprocessing."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

try:
    from material_agent.ml_screening.uniham_band_models import (
        UniHamBandProducedArtifact,
        UniHamBandRequest,
        UniHamBandWorkerRequest,
        UniHamBandWorkerResponse,
        UniHamBandWorkerStatus,
    )
    from material_agent.ml_screening.uniham_models import (
        UniHamGraphManifest,
        UniHamRuntimeProvenance,
    )
except ModuleNotFoundError:  # pragma: no cover - standalone GPU companion
    from uniham_band_models import (  # type: ignore[no-redef]
        UniHamBandProducedArtifact,
        UniHamBandRequest,
        UniHamBandWorkerRequest,
        UniHamBandWorkerResponse,
        UniHamBandWorkerStatus,
    )
    from uniham_models import (  # type: ignore[no-redef]
        UniHamGraphManifest,
        UniHamRuntimeProvenance,
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--artifact-root", type=Path, required=True)
    parser.add_argument("--band-cal-executable", type=Path, required=True)
    args = parser.parse_args()
    request: UniHamBandWorkerRequest | None = None
    try:
        request = UniHamBandWorkerRequest.model_validate_json(sys.stdin.buffer.read())
        response = execute(
            request,
            artifact_root=args.artifact_root,
            band_cal_executable=args.band_cal_executable,
        )
    except Exception as exc:  # noqa: BLE001
        operation_key = (
            request.plan.operation_key if request is not None else "0" * 64
        )
        response = UniHamBandWorkerResponse(
            operation_key=operation_key,
            status=UniHamBandWorkerStatus.FAILED,
            errors=(f"{type(exc).__name__}: {exc}",),
        )
    sys.stdout.write(response.model_dump_json())
    return 0 if response.status is UniHamBandWorkerStatus.SUCCEEDED else 1


def execute(
    worker_request: UniHamBandWorkerRequest,
    *,
    artifact_root: Path,
    band_cal_executable: Path,
) -> UniHamBandWorkerResponse:
    root = artifact_root.resolve(strict=True)
    if not root.is_dir():
        raise ValueError("band artifact root must be a directory")
    executable = band_cal_executable.resolve(strict=True)
    if not executable.is_file() or not os.access(executable, os.X_OK):
        raise ValueError("band_cal executable is unavailable")
    request = worker_request.plan.request
    if _sha256_file(executable) != request.band_calculator_sha256:
        raise ValueError("band_cal executable hash mismatch")
    for artifact in (
        request.input_structure,
        request.soc_graph.graph_data,
        request.soc_graph.manifest,
        request.hamiltonian,
    ):
        _validate_artifact(root, artifact.root_relative_path, artifact.sha256)
    manifest_path = _inside(root, request.soc_graph.manifest.root_relative_path)
    observed_manifest = UniHamGraphManifest.model_validate_json(
        manifest_path.read_bytes()
    )
    if observed_manifest != request.soc_manifest:
        raise ValueError("band graph manifest file differs from frozen request")

    sandbox = _inside(root, worker_request.plan.output_sandbox_relative_path)
    recovered = _recover(worker_request, sandbox)
    if recovered is not None:
        return recovered
    if sandbox.exists():
        raise ValueError("band output sandbox exists without a valid completion")
    output = sandbox / "output"
    work = sandbox / "work"
    output.mkdir(parents=True)
    work.mkdir()
    (work / "matplotlib").mkdir()
    (work / "tmp").mkdir()
    config_path = sandbox / "band-cal.yaml"
    config_path.write_text(
        _render_config(
            request=request,
            root=root,
            output=output,
        ),
        encoding="utf-8",
    )
    completed = subprocess.run(
        (str(executable), "--config", str(config_path)),
        cwd=sandbox,
        capture_output=True,
        check=False,
        timeout=worker_request.limits.wall_time_seconds,
        env={
            "LANG": os.environ.get("LANG", "C.UTF-8"),
            "MPLCONFIGDIR": str(work / "matplotlib"),
            "PATH": str(executable.parent),
            "TMPDIR": str(work / "tmp"),
        },
    )
    if len(completed.stdout) + len(completed.stderr) > (
        worker_request.limits.max_stdout_bytes
    ):
        raise ValueError("band_cal output exceeded its log budget")
    if completed.returncode != 0:
        raise RuntimeError(f"band_cal failed with code {completed.returncode}")
    _validate_scientific_outputs(output)
    shutil.rmtree(work)
    artifacts = _collect_artifacts(worker_request, sandbox, include_summary=False)
    summary_path = sandbox / "execution-summary.json"
    summary_path.write_text(
        json.dumps(
            {
                "band_calculator_sha256": request.band_calculator_sha256,
                "operation_key": worker_request.plan.operation_key,
                "output_artifacts": [
                    item.model_dump(mode="json") for item in artifacts
                ],
                "scientific_conclusion": False,
                "stderr_sha256": hashlib.sha256(completed.stderr).hexdigest(),
                "stdout_sha256": hashlib.sha256(completed.stdout).hexdigest(),
            },
            sort_keys=True,
            separators=(",", ":"),
        ),
        encoding="utf-8",
    )
    artifacts = _collect_artifacts(worker_request, sandbox, include_summary=True)
    return UniHamBandWorkerResponse(
        operation_key=worker_request.plan.operation_key,
        status=UniHamBandWorkerStatus.SUCCEEDED,
        produced_artifacts=artifacts,
        runtime_provenance=_cpu_runtime(),
        warnings=(
            "Band energies are learned-Hamiltonian outputs, not DFT validation.",
            "No orbital projector or topological invariant is produced.",
        ),
    )


def _recover(
    worker_request: UniHamBandWorkerRequest,
    sandbox: Path,
) -> UniHamBandWorkerResponse | None:
    summary_path = sandbox / "execution-summary.json"
    if not summary_path.is_file():
        return None
    payload = json.loads(summary_path.read_text(encoding="utf-8"))
    if payload.get("operation_key") != worker_request.plan.operation_key:
        raise ValueError("cached band operation key mismatch")
    artifacts = _collect_artifacts(worker_request, sandbox, include_summary=True)
    expected_without_summary = {
        item.root_relative_path: (item.sha256, item.size_bytes)
        for item in artifacts
        if not item.root_relative_path.endswith("/execution-summary.json")
    }
    recorded = {
        item["root_relative_path"]: (item["sha256"], item["size_bytes"])
        for item in payload.get("output_artifacts", [])
    }
    if expected_without_summary != recorded:
        raise ValueError("cached band output ledger failed integrity")
    return UniHamBandWorkerResponse(
        operation_key=worker_request.plan.operation_key,
        status=UniHamBandWorkerStatus.SUCCEEDED,
        produced_artifacts=artifacts,
        runtime_provenance=_cpu_runtime(),
        warnings=(
            "Recovered a hash-verified completed band operation.",
            "Band energies are learned-Hamiltonian outputs, not DFT validation.",
            "No orbital projector or topological invariant is produced.",
        ),
    )


def _render_config(
    *,
    request: UniHamBandRequest,
    root: Path,
    output: Path,
) -> str:
    graph_path = _inside(root, request.soc_graph.graph_data.root_relative_path)
    hamiltonian_path = _inside(root, request.hamiltonian.root_relative_path)
    values = {
        "Ham_type": request.ham_type,
        "auto_mode": request.auto_mode,
        "graph_data_path": str(graph_path),
        "hamiltonian_path": str(hamiltonian_path),
        "nao_max": request.nao_max,
        "nk": request.nk,
        "save_dir": str(output),
        "soc_switch": request.soc_switch,
        "spin_colinear": request.spin_colinear,
        "strcture_name": request.structure_name,
    }
    lines = []
    for key in sorted(values):
        value = values[key]
        if isinstance(value, bool):
            encoded = "true" if value else "false"
        elif isinstance(value, str):
            encoded = json.dumps(value)
        else:
            encoded = str(value)
        lines.append(f"{key}: {encoded}")
    return "\n".join(lines) + "\n"


def _validate_scientific_outputs(output: Path) -> None:
    entries = tuple(output.iterdir())
    if any(not item.is_file() or item.is_symlink() for item in entries):
        raise ValueError("band output must contain only regular files")
    files = tuple(sorted(entries))
    suffixes = tuple(sorted(item.suffix for item in files))
    if suffixes != (".cif", ".dat", ".png"):
        raise ValueError("band_cal must produce exactly one CIF, DAT and PNG")


def _collect_artifacts(
    worker_request: UniHamBandWorkerRequest,
    sandbox: Path,
    *,
    include_summary: bool,
) -> tuple[UniHamBandProducedArtifact, ...]:
    paths = [sandbox / "band-cal.yaml", *sorted((sandbox / "output").iterdir())]
    if include_summary:
        paths.append(sandbox / "execution-summary.json")
    artifacts = []
    total = 0
    for path in paths:
        if not path.is_file() or path.is_symlink():
            raise ValueError("band output set contains an invalid file")
        size = path.stat().st_size
        if size <= 0 or size > worker_request.limits.max_single_artifact_bytes:
            raise ValueError("band output violates single-artifact budget")
        total += size
        media_type = {
            ".cif": "chemical/x-cif",
            ".dat": "text/plain",
            ".json": "application/json",
            ".png": "image/png",
            ".yaml": "application/yaml",
        }[path.suffix]
        artifacts.append(
            UniHamBandProducedArtifact(
                root_relative_path=path.relative_to(
                    sandbox.parents[
                        len(
                            worker_request.plan.output_sandbox_relative_path.split(
                                "/"
                            )
                        )
                        - 1
                    ]
                ).as_posix(),
                sha256=_sha256_file(path),
                size_bytes=size,
                media_type=media_type,
            )
        )
    if total > worker_request.limits.max_total_output_bytes:
        raise ValueError("band outputs exceed total artifact budget")
    return tuple(sorted(artifacts, key=lambda item: item.root_relative_path))


def _validate_artifact(root: Path, relative: str, sha256: str) -> None:
    path = _inside(root, relative)
    if not path.is_file() or path.is_symlink():
        raise ValueError("band input Artifact is unavailable")
    if _sha256_file(path) != sha256:
        raise ValueError("band input Artifact hash mismatch")


def _inside(root: Path, relative: str) -> Path:
    path = root.joinpath(*relative.split("/"))
    current = root
    for part in relative.split("/"):
        current /= part
        if current.exists() and current.is_symlink():
            raise ValueError("band paths cannot contain symlinks")
    resolved = path.resolve(strict=False)
    if root not in resolved.parents:
        raise ValueError("band path escaped Artifact root")
    return resolved


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _cpu_runtime() -> UniHamRuntimeProvenance:
    return UniHamRuntimeProvenance(
        requested_device="cpu",
        observed_device="cpu",
        cuda_visible_device_count=0,
    )


if __name__ == "__main__":
    raise SystemExit(main())
