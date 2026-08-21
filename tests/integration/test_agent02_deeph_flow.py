from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

from material_agent.ml_screening.deeph_client import (
    DeepHFlowRunner,
    DeepHProcessError,
    DeepHSubprocessClient,
)
from material_agent.ml_screening.deeph_models import (
    DeepHArtifactBundle,
    DeepHArtifactFile,
    DeepHCompatibility,
    DeepHInferenceRequest,
    DeepHWorkerStatus,
)
from material_agent.retrieval.storage import LocalArtifactStore

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]


def test_deeph_companion_flow_is_cross_process_idempotent_and_fail_closed(
    tmp_path: Path,
) -> None:
    artifact_root = tmp_path / "project"
    artifact_root.mkdir()
    request = _request(artifact_root)
    executable = _fake_deeph_executable(tmp_path)
    runner = DeepHFlowRunner(
        artifact_store=LocalArtifactStore(artifact_root),
        client=DeepHSubprocessClient(
            worker_python=Path(sys.executable),
            deeph_executable=executable,
            artifact_root=artifact_root,
        ),
    )

    first = runner.execute(request)
    assert first.status is DeepHWorkerStatus.SUCCEEDED
    assert first.is_mock
    assert first.evidence_level == "NONE"
    assert first.benchmark_status == "NOT_RUN"
    assert not first.scientific_conclusion
    output_names = {
        Path(item.root_relative_path).name
        for item in first.produced_artifacts
    }
    assert "predicted-hamiltonian.h5" in output_names
    assert "deeph-inference.ini" in output_names
    config_artifact = next(
        item
        for item in first.produced_artifacts
        if item.root_relative_path.endswith("deeph-inference.ini")
    )
    config = (artifact_root / config_artifact.root_relative_path).read_text(
        encoding="utf-8"
    )
    assert "task = [1, 2, 3, 4]" in config
    assert "[1, 2, 3, 4, 5]" not in config

    second = runner.execute(request)
    assert second == first

    result_path = (
        artifact_root
        / f"stages/agent02/{request.run_id}/deeph/"
        / first.operation_key
        / "result.json"
    )
    result_path.write_text("{}", encoding="utf-8")
    with pytest.raises(ValueError, match="failed integrity"):
        runner.execute(request)


def test_deeph_worker_write_outside_declared_sandbox_is_rejected(
    tmp_path: Path,
) -> None:
    artifact_root = tmp_path / "project"
    artifact_root.mkdir()
    request = _request(artifact_root)
    runner = DeepHFlowRunner(
        artifact_store=LocalArtifactStore(artifact_root),
        client=DeepHSubprocessClient(
            worker_python=Path(sys.executable),
            deeph_executable=_fake_deeph_executable(
                tmp_path, write_outside=True
            ),
            artifact_root=artifact_root,
        ),
    )
    with pytest.raises(DeepHProcessError, match="outside"):
        runner.execute(request)


def test_deeph_documented_script_runs_the_same_verified_flow(
    tmp_path: Path,
) -> None:
    artifact_root = tmp_path / "project"
    artifact_root.mkdir()
    request = _request(artifact_root)
    request_path = tmp_path / "deeph-request.json"
    request_path.write_text(request.model_dump_json(), encoding="utf-8")
    completed = subprocess.run(
        [
            sys.executable,
            str(REPOSITORY_ROOT / "scripts/run_agent02_deeph_flow.py"),
            "--request",
            str(request_path),
            "--artifact-root",
            str(artifact_root),
            "--worker-python",
            sys.executable,
            "--deeph-executable",
            str(_fake_deeph_executable(tmp_path)),
        ],
        cwd=REPOSITORY_ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    result = json.loads(completed.stdout)
    assert result["status"] == "SUCCEEDED"
    assert result["is_mock"] is True
    assert result["evidence_level"] == "NONE"
    assert result["benchmark_status"] == "NOT_RUN"
    assert result["scientific_conclusion"] is False


def _request(root: Path) -> DeepHInferenceRequest:
    structure = _write(root, "inputs/structure.cif", b"mock-structure")
    model = _write(root, "inputs/model/model.pkl", b"mock-model")
    overlap = _write(root, "inputs/overlap/overlap.h5", b"mock-overlap")
    compatibility = DeepHCompatibility(
        interface="openmx",
        basis_id="openmx-pa19-s2p2",
        dft_software_version="OpenMX-3.9",
    )
    return DeepHInferenceRequest(
        project_id="project-deeph",
        run_id="run-deeph",
        candidate_id="candidate-1",
        input_structure=structure,
        trained_model=DeepHArtifactBundle(
            root_relative_directory="inputs/model",
            files=[model],
        ),
        overlap=DeepHArtifactBundle(
            root_relative_directory="inputs/overlap",
            files=[overlap],
        ),
        model_compatibility=compatibility,
        overlap_compatibility=compatibility,
        overlap_structure_sha256=structure.sha256,
        model_id="mock-deeph-model",
        deeph_source_revision="0123456789abcdef",
        is_mock=True,
    )


def _write(root: Path, relative: str, payload: bytes) -> DeepHArtifactFile:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)
    return DeepHArtifactFile(
        artifact_uri=f"artifact://{relative}",
        root_relative_path=relative,
        sha256=hashlib.sha256(payload).hexdigest(),
        size_bytes=len(payload),
    )


def _fake_deeph_executable(
    root: Path, *, write_outside: bool = False
) -> Path:
    executable = root / (
        "fake-deeph-inference-escape"
        if write_outside
        else "fake-deeph-inference"
    )
    outside_write = (
        '(work.parents[6] / "escape.txt").write_text("escape", encoding="utf-8")'
        if write_outside
        else ""
    )
    executable.write_text(
        f"""#!{sys.executable}
import argparse
import configparser
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument("--config", required=True)
args = parser.parse_args()
config = configparser.ConfigParser()
config.read(args.config)
work = Path(config["basic"]["work_dir"])
work.mkdir(parents=True, exist_ok=True)
(work / "predicted-hamiltonian.h5").write_bytes(b"mock-hamiltonian")
(work / "flow-complete.txt").write_text("mock DeepH flow only", encoding="utf-8")
{outside_write}
""",
        encoding="utf-8",
    )
    executable.chmod(0o700)
    return executable
