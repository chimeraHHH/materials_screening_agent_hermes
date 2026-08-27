"""No-shell client and immutable completion flow for ALIGNN property inference."""

from __future__ import annotations

import hashlib
import os
import subprocess
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from material_agent.ml_screening.alignn_models import (
    AlignnExecutionPlan,
    AlignnInferenceRequest,
    AlignnResult,
    AlignnWorkerRequest,
    AlignnWorkerResponse,
)
from material_agent.ml_screening.alignn_planner import build_alignn_plan
from material_agent.ml_screening.worker_protocol import capture_artifact_tree
from material_agent.retrieval.storage import LocalArtifactStore


class AlignnProcessError(RuntimeError):
    pass


@dataclass(frozen=True)
class AlignnSubprocessClient:
    worker_python: Path
    artifact_root: Path
    worker_script: Path | None = None

    def run(self, request: AlignnWorkerRequest) -> AlignnWorkerResponse:
        root = self.artifact_root.resolve(strict=True)
        # Preserve a virtualenv's bin/python symlink. Resolving it would
        # bypass pyvenv.cfg and launch the base interpreter without ALIGNN.
        python = self.worker_python.absolute()
        script = (self.worker_script or Path(__file__).with_name("alignn_worker.py")).resolve(strict=True)
        if not python.is_file() or not os.access(python, os.X_OK) or not script.is_file():
            raise AlignnProcessError("configured ALIGNN worker is not executable")
        sandbox = root.joinpath(*request.plan.output_sandbox_relative_path.split("/"))
        sandbox.mkdir(parents=True, exist_ok=True)
        if sandbox.is_symlink() or any(sandbox.iterdir()):
            raise AlignnProcessError("ALIGNN output sandbox must be empty")
        before = capture_artifact_tree(root)
        command = [str(python), str(script), "--artifact-root", str(root)]
        environment = _worker_environment()
        try:
            completed = subprocess.run(command, input=request.model_dump_json().encode(), capture_output=True, timeout=request.wall_time_seconds + 5, shell=False, env=environment, check=False)
        except subprocess.TimeoutExpired as exc:
            raise AlignnProcessError("ALIGNN worker timed out") from exc
        if completed.returncode:
            raise AlignnProcessError(f"ALIGNN worker exited with code {completed.returncode}")
        if len(completed.stdout) > request.max_stdout_bytes:
            raise AlignnProcessError("ALIGNN worker stdout exceeded its limit")
        try:
            response = AlignnWorkerResponse.model_validate_json(completed.stdout)
        except Exception as exc:
            raise AlignnProcessError(f"ALIGNN worker response is invalid: {exc}") from exc
        if response.operation_key != request.plan.operation_key or response.is_mock != request.plan.request.is_mock:
            raise AlignnProcessError("ALIGNN worker response identity mismatch")
        if response.output:
            output = root.joinpath(*response.output.root_relative_path.split("/"))
            if root not in output.resolve(strict=True).parents or output.is_symlink() or not output.is_file():
                raise AlignnProcessError("ALIGNN worker output is outside the artifact root")
            if output.stat().st_size != response.output.size_bytes:
                raise AlignnProcessError("ALIGNN worker output size mismatch")
            digest = hashlib.sha256(output.read_bytes()).hexdigest()
            if digest != response.output.sha256:
                raise AlignnProcessError("ALIGNN worker output hash mismatch")
        after = capture_artifact_tree(root)
        changed = {
            path for path in set(before) | set(after)
            if before.get(path) != after.get(path)
        }
        allowed = {response.output.root_relative_path} if response.output else set()
        sandbox_prefix = request.plan.output_sandbox_relative_path + "/"
        if changed != allowed or any(not path.startswith(sandbox_prefix) for path in changed):
            raise AlignnProcessError("ALIGNN worker wrote outside its declared output")
        return response


def _worker_environment() -> dict[str, str]:
    """Return a minimal worker environment with only the repository source."""

    return {
        "PATH": os.environ.get("PATH", ""),
        "TMPDIR": os.environ.get("TMPDIR", "/tmp"),
        "PYTHONNOUSERSITE": "1",
        "PYTHONPATH": str(Path(__file__).resolve().parents[2]),
        "LANG": "C.UTF-8",
    }


class AlignnFlowRunner:
    def __init__(self, *, artifact_store: LocalArtifactStore, client: AlignnSubprocessClient) -> None:
        if artifact_store.root != client.artifact_root.resolve():
            raise ValueError("ALIGNN store and client artifact roots must match")
        self.store, self.client = artifact_store, client

    def execute(self, request: AlignnInferenceRequest, *, created_at: datetime | None = None) -> AlignnResult:
        plan = build_alignn_plan(request, artifact_root=self.store.root, created_at=created_at or datetime.now(UTC))
        plan_relative = f"plans/{request.run_id}/stages/ml/alignn-{plan.operation_key}.json"
        plan_ref = self.store.write_json(plan_relative, plan.model_dump(mode="json"), immutable=True) if not self.store.exists(plan_relative) else self.store.inspect(plan_relative, media_type="application/json")
        frozen = AlignnExecutionPlan.model_validate(self.store.read_json(plan_relative))
        if frozen.request != request:
            raise ValueError("existing ALIGNN plan conflicts with request")
        result_relative = f"stages/agent02/{request.run_id}/alignn/{plan.operation_key}/result.json"
        complete_relative = f"stages/agent02/{request.run_id}/alignn/{plan.operation_key}/operation-complete.json"
        if self.store.exists(complete_relative):
            result_ref = self.store.inspect(result_relative, media_type="application/json")
            complete = self.store.read_json(complete_relative)
            if complete != {"operation_key": plan.operation_key, "plan_sha256": plan_ref.sha256, "result_sha256": result_ref.sha256}:
                raise ValueError("completed ALIGNN operation failed integrity")
            return AlignnResult.model_validate(self.store.read_json(result_relative))
        if self.store.exists(result_relative):
            raise ValueError("ALIGNN result exists without a completion record")
        response = self.client.run(AlignnWorkerRequest(plan=plan))
        result = AlignnResult(project_id=request.project_id, run_id=request.run_id, candidate_id=request.candidate_id, operation_key=plan.operation_key, plan_uri=plan_ref.uri, plan_sha256=plan_ref.sha256, target_property=request.target_property, value=response.prediction, unit=request.unit, target_method=request.target_method, status=response.status, is_mock=response.is_mock, warnings=[*response.warnings, "Prediction is not a thermodynamic-stability claim and remains unevaluated until a benchmark and applicability review are frozen."], errors=response.errors)
        result_ref = self.store.write_json(result_relative, result.model_dump(mode="json"), immutable=True)
        self.store.write_json(complete_relative, {"operation_key": plan.operation_key, "plan_sha256": plan_ref.sha256, "result_sha256": result_ref.sha256}, immutable=True)
        return result
