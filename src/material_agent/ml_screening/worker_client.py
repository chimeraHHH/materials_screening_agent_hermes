"""Lightweight, no-shell client for the independent Agent02 JSON worker."""

from __future__ import annotations

import hashlib
import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from material_agent.ml_screening.models import (
    MLStagePlan,
    WorkerInputArtifact,
    WorkerLimits,
    WorkerRequest,
    WorkerResponse,
)
from material_agent.ml_screening.worker_protocol import (
    capture_artifact_tree,
    parse_worker_stdout,
    validate_worker_inputs,
    validate_worker_outputs,
)


class Agent02Worker(Protocol):
    @property
    def is_mock(self) -> bool: ...

    def request_for_candidate(
        self, plan: MLStagePlan, candidate_id: str
    ) -> WorkerRequest: ...

    def run(self, request: WorkerRequest) -> WorkerResponse: ...


class WorkerProcessError(RuntimeError):
    """Stable public error raised at the subprocess trust boundary."""

    def __init__(self, category: str, message: str) -> None:
        super().__init__(message)
        self.category = category


@dataclass(frozen=True)
class SubprocessWorkerClient:
    python_executable: Path
    artifact_root: Path
    package_lock_path: Path
    source_root: Path
    module: str = "material_agent.ml_screening.chgnet_worker"
    cuda_visible_devices: str | None = None

    def __post_init__(self) -> None:
        if self.cuda_visible_devices is not None and not re.fullmatch(
            r"\d+", self.cuda_visible_devices
        ):
            raise ValueError(
                "cuda_visible_devices must be one non-negative GPU index"
            )

    @property
    def is_mock(self) -> bool:
        return False

    def request_for_candidate(
        self, plan: MLStagePlan, candidate_id: str
    ) -> WorkerRequest:
        planned = next(
            item
            for item in plan.planned_candidates
            if item.candidate.candidate_id == candidate_id
        )
        structure = planned.candidate.source_structure
        if not structure.uri.startswith("artifact://"):
            raise WorkerProcessError(
                "INPUT_INTEGRITY_ERROR",
                "real worker requires a project-root artifact URI",
            )
        relative = structure.uri.removeprefix("artifact://")
        path = self._root() / relative
        if not path.is_file() or path.is_symlink():
            raise WorkerProcessError(
                "INPUT_INTEGRITY_ERROR",
                "worker structure artifact is missing or is a symlink",
            )
        operation_key = plan.candidate_operation_keys[candidate_id]
        sandbox_relative = (
            "stages/agent02/candidate-operations/"
            f"{operation_key}/worker-output"
        )
        sandbox = self._root() / sandbox_relative
        sandbox.mkdir(parents=True, exist_ok=True)
        if sandbox.is_symlink():
            raise WorkerProcessError(
                "INPUT_INTEGRITY_ERROR",
                "worker output sandbox cannot be a symlink",
            )
        return WorkerRequest(
            expected_handshake=plan.execution_identity,
            plan=plan,
            candidate_id=candidate_id,
            candidate_operation_key=operation_key,
            inputs=[
                WorkerInputArtifact(
                    artifact_uri=structure.uri,
                    root_relative_path=relative,
                    sha256=structure.sha256,
                    size_bytes=path.stat().st_size,
                )
            ],
            output_sandbox_relative_path=sandbox_relative,
            limits=WorkerLimits(
                wall_time_seconds=600,
                max_stdout_bytes=2_000_000,
                max_single_artifact_bytes=100_000_000,
                max_total_output_bytes=250_000_000,
            ),
        )

    def run(self, request: WorkerRequest) -> WorkerResponse:
        root = self._root()
        executable = self.python_executable.resolve(strict=True)
        lock_path = self.package_lock_path.resolve(strict=True)
        source_root = self.source_root.resolve(strict=True)
        if not executable.is_file() or not os.access(executable, os.X_OK):
            raise WorkerProcessError(
                "WORKER_CONFIGURATION_ERROR",
                "configured Agent02 worker Python is not executable",
            )
        if not source_root.is_dir():
            raise WorkerProcessError(
                "WORKER_CONFIGURATION_ERROR",
                "configured Agent02 worker source root is not a directory",
            )
        validate_worker_inputs(request, root)
        before = capture_artifact_tree(root)
        command = [
            str(executable),
            "-m",
            self.module,
            "--artifact-root",
            str(root),
            "--package-lock",
            str(lock_path),
        ]
        environment = {
            "PATH": os.environ.get("PATH", ""),
            "TMPDIR": os.environ.get("TMPDIR", "/tmp"),
            "MPLCONFIGDIR": os.environ.get(
                "MPLCONFIGDIR", "/tmp/material-agent-mpl"
            ),
            "PYTHONNOUSERSITE": "1",
            "PYTHONPATH": str(source_root),
            "LANG": os.environ.get("LANG", "C.UTF-8"),
        }
        if request.plan.device_policy == "cuda":
            if self.cuda_visible_devices is None:
                raise WorkerProcessError(
                    "WORKER_CONFIGURATION_ERROR",
                    "CUDA plan requires an explicit validated GPU index",
                )
            environment["CUDA_VISIBLE_DEVICES"] = self.cuda_visible_devices
        try:
            process = subprocess.Popen(
                command,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                shell=False,
                env=environment,
            )
            stdout, _stderr = process.communicate(
                request.model_dump_json().encode("utf-8"),
                timeout=request.limits.wall_time_seconds,
            )
        except subprocess.TimeoutExpired as exc:
            process.kill()
            process.communicate()
            raise WorkerProcessError(
                "WORKER_TIMEOUT",
                "Agent02 worker exceeded its frozen wall-time limit",
            ) from exc
        except OSError as exc:
            raise WorkerProcessError(
                "WORKER_START_FAILED",
                f"Agent02 worker could not start: {type(exc).__name__}",
            ) from exc
        if len(stdout) > request.limits.max_stdout_bytes:
            raise WorkerProcessError(
                "WORKER_STDOUT_LIMIT",
                "Agent02 worker stdout exceeded its configured limit",
            )
        if process.returncode != 0:
            raise WorkerProcessError(
                "WORKER_PROCESS_FAILED",
                f"Agent02 worker exited with code {process.returncode}; "
                "stderr was withheld from the public control error",
            )
        try:
            response = parse_worker_stdout(
                stdout,
                max_stdout_bytes=request.limits.max_stdout_bytes,
            )
            validate_worker_outputs(
                request=request,
                response=response,
                artifact_root=root,
                before_snapshot=before,
            )
        except Exception as exc:
            raise WorkerProcessError(
                "INVALID_WORKER_RESPONSE",
                f"Agent02 worker response failed validation: {exc}",
            ) from exc
        return response

    def _root(self) -> Path:
        root = self.artifact_root.resolve(strict=True)
        if not root.is_dir():
            raise WorkerProcessError(
                "WORKER_CONFIGURATION_ERROR",
                "Agent02 artifact root is not a directory",
            )
        return root


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()
