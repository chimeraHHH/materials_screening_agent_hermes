"""Remote worker for exact-CIF non-SCF Uni-HamGNN graph preparation."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import yaml
from pymatgen.core import Structure
from pymatgen.io.ase import AseAtomsAdaptor

from material_agent.ml_screening.uniham_graph_prep_models import (
    GraphPrepStatus,
    UniHamGraphPrepArtifact,
    UniHamGraphPrepWorkerRequest,
    UniHamGraphPrepWorkerResponse,
)
from material_agent.ml_screening.uniham_models import (
    UniHamGraphManifest,
    UniHamSocMode,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--artifact-root", type=Path, required=True)
    parser.add_argument("--openmx-postprocess", type=Path, required=True)
    parser.add_argument("--read-openmx", type=Path, required=True)
    parser.add_argument("--graph-data-gen", type=Path, required=True)
    parser.add_argument("--dft-data-root", type=Path, required=True)
    parser.add_argument("--hamgnn-source-root", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        request = UniHamGraphPrepWorkerRequest.model_validate_json(
            sys.stdin.buffer.read()
        )
        response = execute_request(
            request=request,
            artifact_root=args.artifact_root,
            openmx_postprocess=args.openmx_postprocess,
            read_openmx=args.read_openmx,
            graph_data_gen=args.graph_data_gen,
            dft_data_root=args.dft_data_root,
            hamgnn_source_root=args.hamgnn_source_root,
        )
    except Exception as exc:  # noqa: BLE001
        print(
            f"UniHam graph-prep worker failed: {type(exc).__name__}: {exc}",
            file=sys.stderr,
        )
        return 2
    sys.stdout.write(response.model_dump_json())
    return 0


def execute_request(
    *,
    request: UniHamGraphPrepWorkerRequest,
    artifact_root: Path,
    openmx_postprocess: Path,
    read_openmx: Path,
    graph_data_gen: Path,
    dft_data_root: Path,
    hamgnn_source_root: Path,
) -> UniHamGraphPrepWorkerResponse:
    root = artifact_root.resolve(strict=True)
    plan = request.plan
    structure_path = _below(root, plan.input_root_relative_path, require_file=True)
    if _sha256_file(structure_path) != plan.request.input_structure.sha256:
        raise ValueError("graph-prep source CIF hash mismatch")
    tools = {
        "openmx_postprocess": openmx_postprocess.resolve(strict=True),
        "read_openmx": read_openmx.resolve(strict=True),
        "graph_data_gen": graph_data_gen.resolve(strict=True),
    }
    if tools["openmx_postprocess"].name != "openmx_postprocess":
        raise ValueError("only the pinned non-SCF OpenMX postprocessor is allowed")
    expected_hashes = {
        "openmx_postprocess": request.toolchain.openmx_postprocess_sha256,
        "read_openmx": request.toolchain.read_openmx_sha256,
        "graph_data_gen": request.toolchain.graph_data_gen_sha256,
    }
    for name, path in tools.items():
        if _sha256_file(path) != expected_hashes[name]:
            raise ValueError(f"graph-prep {name} hash mismatch")
    data_root = dft_data_root.resolve(strict=True)
    if not (data_root / "PAO").is_dir() or not (data_root / "VPS").is_dir():
        raise ValueError("DFT_DATA19 PAO/VPS directories are unavailable")
    if (
        _directory_manifest_sha256(data_root)
        != request.toolchain.dft_data_manifest_sha256
    ):
        raise ValueError("DFT_DATA19 manifest hash mismatch")
    source_root = hamgnn_source_root.resolve(strict=True)
    if not (source_root / "DFT_interfaces/openmx/utils.py").is_file():
        raise ValueError("HamGNN OpenMX interface source is unavailable")

    output = _below(root, plan.output_root_relative_path, require_file=False)
    output.mkdir(parents=True, exist_ok=True)
    if any(output.iterdir()):
        raise ValueError("graph-prep output sandbox must start empty")
    structure = Structure.from_file(structure_path)
    manifests: dict[UniHamSocMode, UniHamGraphManifest] = {}
    started = time.monotonic()
    for mode in (UniHamSocMode.NON_SOC, UniHamSocMode.SOC):
        mode_root = output / mode.value
        mode_root.mkdir()
        dat_path = mode_root / "openmx.dat"
        _write_openmx_input(
            structure=structure,
            destination=dat_path,
            data_root=data_root,
            mode=mode,
            request=request,
            source_root=source_root,
        )
        _run_bounded(
            [str(tools["openmx_postprocess"]), dat_path.name],
            cwd=mode_root,
            timeout=request.limits.wall_time_seconds,
        )
        scfout = mode_root / "overlap.scfout"
        if not scfout.is_file() or scfout.stat().st_size == 0:
            raise ValueError("non-SCF postprocessor did not create overlap.scfout")
        config = {
            "nao_max": request.plan.request.parameters.nao_max,
            "graph_data_save_path": str(mode_root),
            "read_openmx_path": str(tools["read_openmx"]),
            "max_SCF_skip": 1,
            "scfout_paths": str(mode_root),
            "dat_file_name": dat_path.name,
            "std_file_name": None,
            "scfout_file_name": scfout.name,
            "soc_switch": mode is UniHamSocMode.SOC,
        }
        config_path = mode_root / "graph-data-gen.yaml"
        config_path.write_text(yaml.safe_dump(config, sort_keys=True), encoding="utf-8")
        environment = {
            "PATH": os.environ.get("PATH", ""),
            "PYTHONPATH": str(source_root),
            "PYTHONNOUSERSITE": "1",
            "LANG": "C.UTF-8",
            "OMP_NUM_THREADS": "8",
        }
        _run_bounded(
            [str(tools["graph_data_gen"]), "--config", str(config_path)],
            cwd=mode_root,
            timeout=request.limits.wall_time_seconds,
            environment=environment,
        )
        graph = mode_root / "graph_data.npz"
        if not graph.is_file() or graph.stat().st_size == 0:
            raise ValueError("graph_data_gen did not create graph_data.npz")
        manifest = UniHamGraphManifest(
            structure_sha256=plan.request.input_structure.sha256,
            graph_data_sha256=_sha256_file(graph),
            soc_mode=mode,
            basis_id=plan.request.parameters.basis_id,
            dft_data_version=plan.request.dft_data_version,
            graph_generator_revision=plan.request.graph_generator_revision,
            nao_max=plan.request.parameters.nao_max,
        )
        (mode_root / "hermes-graph-manifest.json").write_text(
            manifest.model_dump_json(), encoding="utf-8"
        )
        manifests[mode] = manifest

    summary = {
        "candidate_id": plan.request.candidate_id,
        "elapsed_seconds": time.monotonic() - started,
        "operation_key": plan.operation_key,
        "self_consistent_dft_invocations": 0,
        "stage": "NON_SCF_ATOMIC_BASIS_GRAPH_PREPARATION",
    }
    (output / "execution-summary.json").write_text(
        json.dumps(summary, sort_keys=True, separators=(",", ":")), encoding="utf-8"
    )
    artifacts = tuple(
        _artifact(root, path) for path in sorted(output.rglob("*")) if path.is_file()
    )
    if (
        sum(item.size_bytes for item in artifacts)
        > request.limits.max_total_output_bytes
    ):
        raise ValueError("graph-prep output exceeds total size limit")
    if any(
        item.size_bytes > request.limits.max_single_artifact_bytes for item in artifacts
    ):
        raise ValueError("graph-prep output exceeds single-artifact size limit")
    return UniHamGraphPrepWorkerResponse(
        operation_key=plan.operation_key,
        status=GraphPrepStatus.SUCCEEDED,
        artifacts=artifacts,
        non_soc_manifest=manifests[UniHamSocMode.NON_SOC],
        soc_manifest=manifests[UniHamSocMode.SOC],
        warnings=(
            "H0/overlap preparation is non-self-consistent and is not DFT evidence.",
        ),
        self_consistent_dft_invocations=0,
        real_execution=True,
    )


def _write_openmx_input(
    *,
    structure: Structure,
    destination: Path,
    data_root: Path,
    mode: UniHamSocMode,
    request: UniHamGraphPrepWorkerRequest,
    source_root: Path,
) -> None:
    sys.path.insert(0, str(source_root))
    try:
        from DFT_interfaces.openmx.utils import (  # noqa: PLC0415
            PAO_dict,
            PBE_dict,
            ase_atoms_to_openmxfile,
            spin_set,
        )
    finally:
        sys.path.pop(0)
    symbols = {item.symbol for item in structure.composition.elements}
    unsupported = (
        symbols - set(PAO_dict) | symbols - set(PBE_dict) | symbols - set(spin_set)
    )
    if unsupported:
        raise ValueError(f"OpenMX NAO26 mapping unavailable for {sorted(unsupported)}")
    spin = "nc" if mode is UniHamSocMode.SOC else "off"
    soc = "on" if mode is UniHamSocMode.SOC else "off"
    params = request.plan.request.parameters
    k_grid = " ".join(str(item) for item in params.k_grid)
    basic = "\n".join(
        (
            "System.CurrentDirectory ./",
            "System.Name openmx",
            f"DATA.PATH {data_root}",
            "level.of.stdout 1",
            "level.of.fileout 1",
            "HS.fileout on",
            "scf.XcType GGA-PBE",
            f"scf.SpinPolarization {spin}",
            f"scf.SpinOrbit.Coupling {soc}",
            "scf.partialCoreCorrection on",
            f"scf.ElectronicTemperature {params.electronic_temperature_k:.8f}",
            f"scf.energycutoff {params.energy_cutoff_ry:.8f}",
            "scf.maxIter 1",
            "scf.EigenvalueSolver Band",
            f"scf.Kgrid {k_grid}",
            "MD.Type Nomd",
            "MD.maxIter 1",
            "MO.fileout off",
            "Dos.fileout off",
            "",
        )
    )
    ase_atoms_to_openmxfile(
        AseAtomsAdaptor.get_atoms(structure),
        basic,
        spin_set,
        PAO_dict,
        PBE_dict,
        str(destination),
    )


def _run_bounded(
    command: list[str],
    *,
    cwd: Path,
    timeout: int,
    environment: dict[str, str] | None = None,
) -> None:
    completed = subprocess.run(
        command,
        cwd=cwd,
        env=environment,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        timeout=timeout,
        check=False,
        shell=False,
    )
    if completed.returncode != 0:
        raise RuntimeError(
            f"approved graph-prep command failed with code {completed.returncode}"
        )


def _below(root: Path, relative: str, *, require_file: bool) -> Path:
    path = root.joinpath(*relative.split("/")).resolve()
    if root not in path.parents:
        raise ValueError("graph-prep path escapes artifact root")
    if require_file and (not path.is_file() or path.is_symlink()):
        raise ValueError("graph-prep input is unavailable or a symlink")
    return path


def _directory_manifest_sha256(root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(item for item in root.rglob("*") if item.is_file()):
        relative = path.relative_to(root).as_posix().encode()
        digest.update(relative)
        digest.update(b"\0")
        digest.update(_sha256_file(path).encode())
        digest.update(b"\n")
    return digest.hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _artifact(root: Path, path: Path) -> UniHamGraphPrepArtifact:
    suffix = path.suffix.lower()
    media_type = {
        ".json": "application/json",
        ".npz": "application/x-npz",
        ".cif": "chemical/x-cif",
        ".yaml": "application/yaml",
        ".dat": "text/plain",
        ".scfout": "application/octet-stream",
    }.get(suffix, "application/octet-stream")
    return UniHamGraphPrepArtifact(
        root_relative_path=path.relative_to(root).as_posix(),
        sha256=_sha256_file(path),
        size_bytes=path.stat().st_size,
        media_type=media_type,
    )


if __name__ == "__main__":
    raise SystemExit(main())
