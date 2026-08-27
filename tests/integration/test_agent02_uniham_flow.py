from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

from material_agent.ml_screening.uniham_client import (
    UniHamFlowRunner,
    UniHamProcessError,
    UniHamSubprocessClient,
)
from material_agent.ml_screening.uniham_models import (
    UniHamWorkerRequest,
    UniHamWorkerStatus,
)
from material_agent.ml_screening.uniham_planner import build_uniham_plan
from material_agent.ml_screening.uniham_worker import execute as execute_worker
from material_agent.retrieval.storage import LocalArtifactStore
from tests.unit.test_ml_uniham_contracts import make_request

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]


def test_uniham_flow_is_cross_process_idempotent_and_unbenchmarked(
    tmp_path: Path,
) -> None:
    root = tmp_path / "project"
    root.mkdir()
    predictor = fake_predictor(tmp_path)
    request = make_request(root, predictor_sha256=sha256_file(predictor))
    runner = make_runner(root, predictor)

    first = runner.execute(request)
    assert first.status is UniHamWorkerStatus.SUCCEEDED
    assert first.hamiltonian_artifact.root_relative_path.endswith(
        "/output/hamiltonian.npy"
    )
    assert first.evidence_level == "NONE"
    assert first.benchmark_status == "NOT_RUN"
    assert not first.scientific_conclusion
    assert first.is_mock
    second = runner.execute(request)
    assert second == first

    result_path = (
        root
        / f"stages/agent02/{request.run_id}/uniham/{first.operation_key}/result.json"
    )
    result_path.write_text("{}", encoding="utf-8")
    with pytest.raises(ValueError, match="failed integrity"):
        runner.execute(request)


def test_uniham_worker_recovers_completed_hash_verified_sandbox(
    tmp_path: Path,
) -> None:
    root = tmp_path / "project"
    root.mkdir()
    predictor = fake_predictor(tmp_path)
    request = make_request(root, predictor_sha256=sha256_file(predictor))
    plan = build_uniham_plan(request, artifact_root=root)
    payload = UniHamWorkerRequest(plan=plan).model_dump(mode="json")

    first = execute_worker(
        payload,
        artifact_root=root,
        predictor_script=predictor,
    )
    second = execute_worker(
        payload,
        artifact_root=root,
        predictor_script=predictor,
    )

    assert first["status"] == second["status"] == "SUCCEEDED"
    assert first["produced_artifacts"] == second["produced_artifacts"]
    assert "recovered" in second["warnings"][0]


def test_uniham_predictor_hash_drift_is_rejected(tmp_path: Path) -> None:
    root = tmp_path / "project"
    root.mkdir()
    predictor = fake_predictor(tmp_path)
    request = make_request(root, predictor_sha256=sha256_file(predictor))
    predictor.write_text("# changed", encoding="utf-8")

    with pytest.raises(UniHamProcessError, match="hash differs"):
        make_runner(root, predictor).execute(request)


def test_uniham_extra_output_is_rejected(tmp_path: Path) -> None:
    root = tmp_path / "project"
    root.mkdir()
    predictor = fake_predictor(tmp_path, extra_output=True)
    request = make_request(root, predictor_sha256=sha256_file(predictor))

    with pytest.raises(UniHamProcessError, match="allowlist mismatch"):
        make_runner(root, predictor).execute(request)


def test_uniham_cuda_request_requires_one_pinned_gpu(tmp_path: Path) -> None:
    root = tmp_path / "project"
    root.mkdir()
    predictor = fake_predictor(tmp_path)
    request = make_request(
        root,
        predictor_sha256=sha256_file(predictor),
    ).model_copy(update={"device": "cuda"})

    with pytest.raises(UniHamProcessError, match="explicit GPU index"):
        make_runner(root, predictor).execute(request)

    with pytest.raises(ValueError, match="one GPU index"):
        UniHamSubprocessClient(
            worker_python=Path(sys.executable),
            predictor_script=predictor,
            artifact_root=root,
            cuda_visible_devices="0,1",
        )


def test_documented_uniham_script_runs_verified_flow(tmp_path: Path) -> None:
    root = tmp_path / "project"
    root.mkdir()
    predictor = fake_predictor(tmp_path)
    request = make_request(root, predictor_sha256=sha256_file(predictor))
    request_path = tmp_path / "uniham-request.json"
    request_path.write_text(request.model_dump_json(), encoding="utf-8")

    completed = subprocess.run(
        [
            sys.executable,
            str(REPOSITORY_ROOT / "scripts/run_agent02_uniham_flow.py"),
            "--request",
            str(request_path),
            "--artifact-root",
            str(root),
            "--worker-python",
            sys.executable,
            "--predictor-script",
            str(predictor),
        ],
        cwd=REPOSITORY_ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    result = json.loads(completed.stdout)
    assert result["status"] == "SUCCEEDED"
    assert result["benchmark_status"] == "NOT_RUN"
    assert result["scientific_conclusion"] is False


def make_runner(root: Path, predictor: Path) -> UniHamFlowRunner:
    return UniHamFlowRunner(
        artifact_store=LocalArtifactStore(root),
        client=UniHamSubprocessClient(
            worker_python=Path(sys.executable),
            predictor_script=predictor,
            artifact_root=root,
        ),
    )


def fake_predictor(root: Path, *, extra_output: bool = False) -> Path:
    script = root / ("fake-uniham-extra.py" if extra_output else "fake-uniham.py")
    extra = (
        '(output / "unexpected.txt").write_text("extra", encoding="utf-8")'
        if extra_output
        else ""
    )
    script.write_text(
        f"""import argparse
import os
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument("--config", required=True)
args = parser.parse_args()
values = {{}}
for line in Path(args.config).read_text(encoding="utf-8").splitlines():
    key, value = line.split(":", 1)
    values[key] = value.strip().strip("'").replace("''", "'")
assert values["device"] == "cpu"
assert values["calculate_mae"] == "false"
output = Path(values["output_dir"])
(Path(os.environ["TMPDIR"]) / "torchinductor_fixture").mkdir()
(output / "hamiltonian.npy").write_bytes(b"\\x93NUMPYfixture")
{extra}
""",
        encoding="utf-8",
    )
    return script


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()
