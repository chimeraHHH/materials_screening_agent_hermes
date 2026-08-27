from __future__ import annotations

import hashlib
import sys
from pathlib import Path

import pytest

from material_agent.ml_screening.uniham_band_models import (
    UniHamBandRequest,
    UniHamBandWorkerRequest,
    build_uniham_band_plan,
)
from material_agent.ml_screening.uniham_band_worker import execute
from material_agent.ml_screening.uniham_models import (
    UniHamArtifactFile,
    UniHamGraphBundle,
    UniHamGraphManifest,
)


def test_band_worker_is_hash_bound_and_recovers_completed_operation(
    tmp_path: Path,
) -> None:
    executable = _fake_band_cal(tmp_path / "fake-band-cal")
    request = _request(tmp_path, executable)
    worker_request = UniHamBandWorkerRequest(
        plan=build_uniham_band_plan(request)
    )

    first = execute(
        worker_request,
        artifact_root=tmp_path,
        band_cal_executable=executable,
    )
    second = execute(
        worker_request,
        artifact_root=tmp_path,
        band_cal_executable=executable,
    )

    assert first.status == "SUCCEEDED"
    assert second.status == "SUCCEEDED"
    assert first.produced_artifacts == second.produced_artifacts
    assert "Recovered" in second.warnings[0]
    assert tuple(
        sorted(Path(item.root_relative_path).suffix for item in first.produced_artifacts)
    ) == (".cif", ".dat", ".json", ".png", ".yaml")


def test_band_worker_rejects_tampered_cached_output(tmp_path: Path) -> None:
    executable = _fake_band_cal(tmp_path / "fake-band-cal")
    request = _request(tmp_path, executable)
    worker_request = UniHamBandWorkerRequest(
        plan=build_uniham_band_plan(request)
    )
    first = execute(
        worker_request,
        artifact_root=tmp_path,
        band_cal_executable=executable,
    )
    dat = next(
        item for item in first.produced_artifacts if item.root_relative_path.endswith(".dat")
    )
    (tmp_path / dat.root_relative_path).write_text("tampered", encoding="utf-8")

    with pytest.raises(ValueError, match="ledger failed integrity"):
        execute(
            worker_request,
            artifact_root=tmp_path,
            band_cal_executable=executable,
        )


def _request(root: Path, executable: Path) -> UniHamBandRequest:
    structure = _write(root, "assets/example/openmx.cif", b"data_example")
    graph = _write(root, "assets/example/soc/graph_data.npz", b"graph")
    manifest_model = UniHamGraphManifest(
        structure_sha256=structure.sha256,
        graph_data_sha256=graph.sha256,
        soc_mode="soc",
        basis_id="openmx-nao26-v1",
        dft_data_version="DFT_DATA19",
        graph_generator_revision="abcdef1234567890",
    )
    manifest = _write(
        root,
        "assets/example/soc/hermes-graph-manifest.json",
        manifest_model.model_dump_json().encode(),
        media_type="application/json",
    )
    hamiltonian_key = "9" * 64
    hamiltonian = _write(
        root,
        (
            "stages/agent02/run-band/uniham/"
            f"{hamiltonian_key}/worker-output/output/hamiltonian.npy"
        ),
        b"\x93NUMPY-hamiltonian",
        media_type="application/x-npy",
    )
    return UniHamBandRequest(
        project_id="project-band",
        run_id="run-band",
        candidate_id="candidate-band",
        input_structure=structure,
        soc_graph=UniHamGraphBundle(
            root_relative_directory="assets/example/soc",
            graph_data=graph,
            manifest=manifest,
        ),
        soc_manifest=manifest_model,
        hamiltonian=hamiltonian,
        hamiltonian_operation_key=hamiltonian_key,
        hamgnn_source_revision="abcdef1234567890",
        band_calculator_sha256=_sha256(executable.read_bytes()),
    )


def _fake_band_cal(path: Path) -> Path:
    path.write_text(
        f"""#!{sys.executable}
import json
import pathlib
import sys

config = pathlib.Path(sys.argv[sys.argv.index('--config') + 1]).read_text()
values = {{}}
for line in config.splitlines():
    key, value = line.split(': ', 1)
    values[key] = value
output = pathlib.Path(json.loads(values['save_dir']))
output.joinpath('band_1.dat').write_text(
    '# k_lable: G X\\n# k_node: 0.0 1.0\\n0.0 -0.1\\n1.0 -0.1\\n'
)
output.joinpath('band_1.png').write_bytes(b'fixture-png')
output.joinpath('crystal_1.cif').write_text('data_fixture\\n')
""",
        encoding="utf-8",
    )
    path.chmod(0o755)
    return path


def _write(
    root: Path,
    relative: str,
    payload: bytes,
    *,
    media_type: str = "application/octet-stream",
) -> UniHamArtifactFile:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)
    return UniHamArtifactFile(
        artifact_uri=f"artifact://{relative}",
        root_relative_path=relative,
        sha256=_sha256(payload),
        size_bytes=len(payload),
        media_type=media_type,
    )


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()
