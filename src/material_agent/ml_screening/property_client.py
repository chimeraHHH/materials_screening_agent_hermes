"""No-shell client and immutable completion flow for property-model workers."""

from __future__ import annotations

import hashlib
import os
import subprocess
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from material_agent.ml_screening.property_execution import (
    PropertyExecutionPlan,
    PropertyPredictionResult,
    PropertyWorkerRequest,
    PropertyWorkerResponse,
    build_property_execution_plan,
)
from material_agent.ml_screening.property_models import (
    PropertyModelFamily,
    PropertyModelRegistry,
    PropertyPredictionRequest,
    select_property_model,
)
from material_agent.ml_screening.worker_protocol import capture_artifact_tree
from material_agent.retrieval.storage import LocalArtifactStore


class PropertyProcessError(RuntimeError):
    pass


@dataclass(frozen=True)
class PropertySubprocessClient:
    """Run a reviewed property adapter in its dedicated Python environment.

    Source roots are deployment configuration, never request data.  A source
    root is passed only for the selected adapter family.
    """

    worker_python: Path
    artifact_root: Path
    ct_uae_source_root: Path | None = None
    worker_script: Path | None = None

    def run(self, request: PropertyWorkerRequest) -> PropertyWorkerResponse:
        root = self.artifact_root.resolve(strict=True)
        # A virtualenv's ``bin/python`` is commonly a symlink to its base
        # interpreter.  Resolving it would bypass ``pyvenv.cfg`` and silently
        # launch the base environment instead of the reviewed worker runtime.
        # Make it absolute without dereferencing that symlink.
        python = self.worker_python.absolute()
        script = (self.worker_script or Path(__file__).with_name("property_worker.py")).resolve(strict=True)
        if not python.is_file() or not os.access(python, os.X_OK) or not script.is_file():
            raise PropertyProcessError("configured property worker is not executable")
        sandbox = root.joinpath(*request.plan.output_sandbox_relative_path.split("/"))
        if sandbox.exists():
            raise PropertyProcessError("property output sandbox must not already exist")
        before = capture_artifact_tree(root)
        command = [str(python), str(script), "--artifact-root", str(root)]
        if (
            request.plan.selected_model.family is PropertyModelFamily.CT_UAE
            and self.ct_uae_source_root is not None
        ):
            command.extend(["--ct-uae-source-root", str(self.ct_uae_source_root.resolve(strict=True))])
        environment = _worker_environment()
        try:
            completed = subprocess.run(
                command,
                input=request.model_dump_json().encode(),
                capture_output=True,
                timeout=request.wall_time_seconds + 5,
                shell=False,
                env=environment,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise PropertyProcessError("property worker timed out") from exc
        if completed.returncode:
            raise PropertyProcessError(f"property worker exited with code {completed.returncode}")
        if len(completed.stdout) > request.max_stdout_bytes:
            raise PropertyProcessError("property worker stdout exceeded its limit")
        try:
            response = PropertyWorkerResponse.model_validate_json(completed.stdout)
        except Exception as exc:
            raise PropertyProcessError(f"property worker response is invalid: {exc}") from exc
        plan = request.plan
        if (
            response.operation_key != plan.operation_key
            or response.model_id != plan.selected_model.model_id
            or response.property_id != plan.selected_capability.property_id
            or response.unit != plan.selected_capability.unit
            or response.is_mock != plan.selected_model.is_mock
        ):
            raise PropertyProcessError("property worker response identity mismatch")
        if response.output is not None:
            output = root.joinpath(*response.output.root_relative_path.split("/"))
            resolved = output.resolve(strict=True)
            if root not in resolved.parents or output.is_symlink() or not output.is_file():
                raise PropertyProcessError("property worker output is outside the artifact root")
            if output.stat().st_size != response.output.size_bytes:
                raise PropertyProcessError("property worker output size mismatch")
            if hashlib.sha256(output.read_bytes()).hexdigest() != response.output.sha256:
                raise PropertyProcessError("property worker output hash mismatch")
        after = capture_artifact_tree(root)
        changed = {path for path in set(before) | set(after) if before.get(path) != after.get(path)}
        allowed = {response.output.root_relative_path} if response.output else set()
        sandbox_prefix = plan.output_sandbox_relative_path + "/"
        if changed != allowed or any(not path.startswith(sandbox_prefix) for path in changed):
            raise PropertyProcessError("property worker wrote outside its declared output")
        return response


def _worker_environment() -> dict[str, str]:
    """Return the minimal, deterministic environment for an ML subprocess."""
    return {
        "PATH": os.environ.get("PATH", ""),
        "TMPDIR": os.environ.get("TMPDIR", "/tmp"),
        "PYTHONNOUSERSITE": "1",
        # The model worker runs in an isolated environment where this project
        # is intentionally not installed.  Expose only the repository source
        # root required by the immutable worker script; do not inherit an
        # arbitrary caller PYTHONPATH.
        "PYTHONPATH": str(Path(__file__).resolve().parents[2]),
        "LANG": "C.UTF-8",
    }


class PropertyPredictionFlowRunner:
    def __init__(self, *, artifact_store: LocalArtifactStore, client: PropertySubprocessClient, registry: PropertyModelRegistry) -> None:
        if artifact_store.root != client.artifact_root.resolve():
            raise ValueError("property store and client artifact roots must match")
        self.store, self.client, self.registry = artifact_store, client, registry

    def execute(self, request: PropertyPredictionRequest, *, created_at: datetime | None = None) -> PropertyPredictionResult:
        selection = select_property_model(request, self.registry)
        plan = build_property_execution_plan(request, selection, artifact_root=self.store.root, created_at=created_at or datetime.now(UTC))
        plan_relative = f"plans/{request.run_id}/stages/ml/property-{plan.operation_key}.json"
        plan_ref = self.store.write_json(plan_relative, plan.model_dump(mode="json"), immutable=True) if not self.store.exists(plan_relative) else self.store.inspect(plan_relative, media_type="application/json")
        frozen = PropertyExecutionPlan.model_validate(self.store.read_json(plan_relative))
        if frozen.request != request or frozen.selected_model != plan.selected_model:
            raise ValueError("existing property plan conflicts with request")
        result_relative = f"stages/agent02/{request.run_id}/property/{plan.operation_key}/result.json"
        complete_relative = f"stages/agent02/{request.run_id}/property/{plan.operation_key}/operation-complete.json"
        if self.store.exists(complete_relative):
            result_ref = self.store.inspect(result_relative, media_type="application/json")
            if self.store.read_json(complete_relative) != {"operation_key": plan.operation_key, "plan_sha256": plan_ref.sha256, "result_sha256": result_ref.sha256}:
                raise ValueError("completed property operation failed integrity")
            return PropertyPredictionResult.model_validate(self.store.read_json(result_relative))
        if self.store.exists(result_relative):
            raise ValueError("property result exists without a completion record")
        response = self.client.run(PropertyWorkerRequest(plan=plan))
        result = PropertyPredictionResult(
            project_id=request.project_id,
            run_id=request.run_id,
            candidate_id=request.candidate_id,
            operation_key=plan.operation_key,
            plan_uri=plan_ref.uri,
            plan_sha256=plan_ref.sha256,
            model_id=response.model_id,
            property_id=response.property_id,
            value=response.prediction,
            unit=response.unit,
            status=response.status,
            is_mock=response.is_mock,
            warnings=[*response.warnings, "Prediction is an unbenchmarked model estimate; it is not a DFT, thermodynamic-stability, or experimental claim."],
            errors=response.errors,
        )
        result_ref = self.store.write_json(result_relative, result.model_dump(mode="json"), immutable=True)
        self.store.write_json(complete_relative, {"operation_key": plan.operation_key, "plan_sha256": plan_ref.sha256, "result_sha256": result_ref.sha256}, immutable=True)
        return result
