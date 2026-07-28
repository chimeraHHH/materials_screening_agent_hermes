from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from material_agent.ml_screening.models import WorkerRequest
from material_agent.ml_screening.worker_client import (
    SubprocessWorkerClient,
    WorkerProcessError,
)


FIXTURE_ROOT = (
    Path(__file__).parents[1] / "fixtures/contracts/agent02-v1"
)


def _request_and_client(tmp_path: Path):
    request = WorkerRequest.model_validate_json(
        (
            FIXTURE_ROOT
            / "stages/agent02/run-agent02-contract/attempt-1"
            / "fake-worker-request.json"
        ).read_text(encoding="utf-8")
    )
    source = FIXTURE_ROOT / "upstream/str_contract_si_o.cif"
    destination = tmp_path / request.inputs[0].root_relative_path
    destination.parent.mkdir(parents=True)
    shutil.copyfile(source, destination)
    (tmp_path / request.output_sandbox_relative_path).mkdir(parents=True)
    lock_path = tmp_path / "lock.txt"
    lock_path.write_text("lock", encoding="utf-8")
    client = SubprocessWorkerClient(
        python_executable=Path("/bin/sh"),
        artifact_root=tmp_path,
        package_lock_path=lock_path,
        source_root=Path(__file__).parents[2] / "src",
    )
    return request, client


def test_subprocess_client_never_uses_shell(tmp_path: Path) -> None:
    request, client = _request_and_client(tmp_path)

    class Process:
        returncode = 0

        def communicate(self, _stdin, timeout):
            assert timeout == request.limits.wall_time_seconds
            return b"not-json", b""

    with patch("subprocess.Popen", return_value=Process()) as popen:
        with pytest.raises(WorkerProcessError) as caught:
            client.run(request)
    assert caught.value.category == "INVALID_WORKER_RESPONSE"
    assert popen.call_args.kwargs["shell"] is False
    assert popen.call_args.kwargs["env"]["PYTHONNOUSERSITE"] == "1"
    assert popen.call_args.args[0][0] == str(Path("/bin/sh").resolve())


def test_subprocess_client_kills_timed_out_worker(tmp_path: Path) -> None:
    request, client = _request_and_client(tmp_path)

    class Process:
        returncode = None

        def __init__(self):
            self.communicate_calls = 0
            self.killed = False

        def communicate(self, *_args, **_kwargs):
            self.communicate_calls += 1
            if self.communicate_calls == 1:
                raise subprocess.TimeoutExpired("worker", 1)
            return b"", b""

        def kill(self):
            self.killed = True

    process = Process()
    with patch("subprocess.Popen", return_value=process):
        with pytest.raises(WorkerProcessError) as caught:
            client.run(request)
    assert caught.value.category == "WORKER_TIMEOUT"
    assert process.killed
    assert process.communicate_calls == 2
