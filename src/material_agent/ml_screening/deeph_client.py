"""No-shell client and verified completion flow for DeepH-pack."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from material_agent.ml_screening.deeph_models import (
    DeepHExecutionPlan,
    DeepHInferenceRequest,
    DeepHProducedArtifact,
    DeepHResult,
    DeepHWorkerRequest,
    DeepHWorkerResponse,
    DeepHWorkerStatus,
)
from material_agent.ml_screening.deeph_planner import build_deeph_plan
from material_agent.ml_screening.worker_protocol import capture_artifact_tree
from material_agent.retrieval.storage import LocalArtifactStore


class DeepHProcessError(RuntimeError):
    def __init__(self, category: str, message: str) -> None:
        super().__init__(message)
        self.category = category


@dataclass(frozen=True)
class DeepHSubprocessClient:
    worker_python: Path
    deeph_executable: Path
    artifact_root: Path
    worker_script: Path | None = None

    def run(self, request: DeepHWorkerRequest) -> DeepHWorkerResponse:
        root = self.artifact_root.resolve(strict=True)
        worker_python = self.worker_python.resolve(strict=True)
        deeph_executable = self.deeph_executable.resolve(strict=True)
        worker_script = (
            self.worker_script
            or Path(__file__).with_name("deeph_worker.py")
        ).resolve(strict=True)
        for name, path in (
            ("worker Python", worker_python),
            ("deeph-inference executable", deeph_executable),
        ):
            if not path.is_file() or not os.access(path, os.X_OK):
                raise DeepHProcessError(
                    "WORKER_CONFIGURATION_ERROR",
                    f"configured DeepH {name} is not executable",
                )
        if not worker_script.is_file():
            raise DeepHProcessError(
                "WORKER_CONFIGURATION_ERROR",
                "DeepH worker script is missing",
            )
        sandbox = root.joinpath(
            *request.plan.output_sandbox_relative_path.split("/")
        )
        sandbox.mkdir(parents=True, exist_ok=True)
        if sandbox.is_symlink() or any(sandbox.iterdir()):
            raise DeepHProcessError(
                "OUTPUT_SANDBOX_CONFLICT",
                "DeepH output sandbox must exist and be empty",
            )
        before = capture_artifact_tree(root)
        command = [
            str(worker_python),
            str(worker_script),
            "--artifact-root",
            str(root),
            "--deeph-executable",
            str(deeph_executable),
        ]
        environment = {
            "PATH": os.environ.get("PATH", ""),
            "TMPDIR": os.environ.get("TMPDIR", "/tmp"),
            "PYTHONNOUSERSITE": "1",
            "LANG": os.environ.get("LANG", "C.UTF-8"),
        }
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
                timeout=request.limits.wall_time_seconds + 5,
            )
        except subprocess.TimeoutExpired as exc:
            process.kill()
            process.communicate()
            raise DeepHProcessError(
                "WORKER_TIMEOUT",
                "DeepH worker exceeded its frozen wall-time limit",
            ) from exc
        except OSError as exc:
            raise DeepHProcessError(
                "WORKER_START_FAILED",
                f"DeepH worker could not start: {type(exc).__name__}",
            ) from exc
        if process.returncode != 0:
            raise DeepHProcessError(
                "WORKER_PROCESS_FAILED",
                f"DeepH worker exited with code {process.returncode}",
            )
        if len(stdout) > request.limits.max_stdout_bytes:
            raise DeepHProcessError(
                "WORKER_STDOUT_LIMIT",
                "DeepH worker stdout exceeded its configured limit",
            )
        try:
            payload = json.loads(stdout.decode("utf-8"))
            response = DeepHWorkerResponse.model_validate(payload)
        except Exception as exc:
            raise DeepHProcessError(
                "INVALID_WORKER_RESPONSE",
                f"DeepH worker response is invalid: {exc}",
            ) from exc
        if response.operation_key != request.plan.operation_key:
            raise DeepHProcessError(
                "INVALID_WORKER_RESPONSE",
                "DeepH worker operation identity differs from plan",
            )
        if response.is_mock != request.plan.request.is_mock:
            raise DeepHProcessError(
                "INVALID_WORKER_RESPONSE",
                "DeepH worker mock marker differs from request",
            )
        if response.status is DeepHWorkerStatus.FAILED:
            raise DeepHProcessError(
                "DEEPH_INFERENCE_FAILED",
                "; ".join(response.errors),
            )
        self._validate_outputs(request, response, before)
        return response

    def _validate_outputs(
        self,
        request: DeepHWorkerRequest,
        response: DeepHWorkerResponse,
        before: dict,
    ) -> None:
        root = self.artifact_root.resolve(strict=True)
        sandbox = root.joinpath(
            *request.plan.output_sandbox_relative_path.split("/")
        ).resolve(strict=True)
        reported: set[str] = set()
        total = 0
        for artifact in response.produced_artifacts:
            path = root.joinpath(
                *artifact.root_relative_path.split("/")
            )
            if path.is_symlink():
                raise DeepHProcessError(
                    "INVALID_WORKER_RESPONSE",
                    "DeepH output cannot be a symlink",
                )
            resolved = path.resolve(strict=True)
            if sandbox not in resolved.parents:
                raise DeepHProcessError(
                    "INVALID_WORKER_RESPONSE",
                    "DeepH output escaped its sandbox",
                )
            if resolved.stat().st_size != artifact.size_bytes:
                raise DeepHProcessError(
                    "INVALID_WORKER_RESPONSE",
                    "DeepH output size differs from response",
                )
            if artifact.size_bytes > (
                request.limits.max_single_artifact_bytes
            ):
                raise DeepHProcessError(
                    "INVALID_WORKER_RESPONSE",
                    "DeepH output exceeds single-artifact limit",
                )
            if _sha256_file(resolved) != artifact.sha256:
                raise DeepHProcessError(
                    "INVALID_WORKER_RESPONSE",
                    "DeepH output hash differs from response",
                )
            total += artifact.size_bytes
            reported.add(artifact.root_relative_path)
        if len(reported) != len(response.produced_artifacts):
            raise DeepHProcessError(
                "INVALID_WORKER_RESPONSE",
                "DeepH response contains duplicate output paths",
            )
        if total > request.limits.max_total_output_bytes:
            raise DeepHProcessError(
                "INVALID_WORKER_RESPONSE",
                "DeepH output exceeds total limit",
            )
        actual = {
            path.relative_to(root).as_posix()
            for path in sandbox.rglob("*")
            if path.is_file()
        }
        if actual != reported:
            raise DeepHProcessError(
                "INVALID_WORKER_RESPONSE",
                "DeepH sandbox has unreported or missing files",
            )
        after = capture_artifact_tree(root)
        changed = {
            path
            for path in set(before) | set(after)
            if before.get(path) != after.get(path)
        }
        if changed != reported:
            raise DeepHProcessError(
                "INVALID_WORKER_RESPONSE",
                "DeepH worker wrote outside its declared outputs",
            )


class DeepHFlowRunner:
    """Persist a DeepH plan/result with an immutable completion ledger."""

    def __init__(
        self,
        *,
        artifact_store: LocalArtifactStore,
        client: DeepHSubprocessClient,
    ) -> None:
        if artifact_store.root != client.artifact_root.resolve():
            raise ValueError("DeepH store and client artifact roots must match")
        self.store = artifact_store
        self.client = client

    def execute(
        self,
        request: DeepHInferenceRequest,
        *,
        created_at: datetime | None = None,
    ) -> DeepHResult:
        plan = build_deeph_plan(
            request,
            artifact_root=self.store.root,
            created_at=created_at or datetime.now(UTC),
        )
        plan_relative = (
            f"plans/{request.run_id}/stages/ml/"
            f"deeph-{plan.operation_key}.json"
        )
        if self.store.exists(plan_relative):
            frozen_plan = DeepHExecutionPlan.model_validate(
                self.store.read_json(plan_relative)
            )
            if frozen_plan.request != request:
                raise ValueError("existing DeepH plan conflicts with request")
            plan = frozen_plan
            plan_ref = self.store.inspect(
                plan_relative, media_type="application/json"
            )
        else:
            plan_ref = self.store.write_json(
                plan_relative,
                plan.model_dump(mode="json"),
                immutable=True,
            )
        result_relative = (
            f"stages/agent02/{request.run_id}/deeph/"
            f"{plan.operation_key}/result.json"
        )
        completion_relative = (
            f"stages/agent02/{request.run_id}/deeph/"
            f"{plan.operation_key}/operation-complete.json"
        )
        if self.store.exists(completion_relative):
            completion = self.store.read_json(completion_relative)
            result_ref = self.store.inspect(
                result_relative, media_type="application/json"
            )
            if (
                completion.get("operation_key") != plan.operation_key
                or completion.get("plan_sha256") != plan_ref.sha256
                or completion.get("result_sha256") != result_ref.sha256
            ):
                raise ValueError("completed DeepH operation failed integrity")
            return DeepHResult.model_validate(
                self.store.read_json(result_relative)
            )
        if self.store.exists(result_relative):
            raise ValueError(
                "DeepH result exists without an authoritative completion record"
            )
        response = self.client.run(DeepHWorkerRequest(plan=plan))
        result = DeepHResult(
            project_id=request.project_id,
            run_id=request.run_id,
            candidate_id=request.candidate_id,
            operation_key=plan.operation_key,
            plan_uri=plan_ref.uri,
            plan_sha256=plan_ref.sha256,
            status=response.status,
            produced_artifacts=response.produced_artifacts,
            is_mock=response.is_mock,
            warnings=[
                *response.warnings,
                "Evidence remains NONE until a separate scientific benchmark "
                "and applicability review are completed.",
            ],
            errors=response.errors,
        )
        result_ref = self.store.write_json(
            result_relative,
            result.model_dump(mode="json"),
            immutable=True,
        )
        self.store.write_json(
            completion_relative,
            {
                "schema_version": "agent02-deeph-operation-complete-v1",
                "operation_key": plan.operation_key,
                "plan_sha256": plan_ref.sha256,
                "result_sha256": result_ref.sha256,
            },
            immutable=True,
        )
        return result


def artifact_from_path(
    path: Path,
    *,
    artifact_root: Path,
    media_type: str = "application/octet-stream",
) -> DeepHProducedArtifact:
    root = artifact_root.resolve(strict=True)
    resolved = path.resolve(strict=True)
    if root not in resolved.parents or not resolved.is_file():
        raise ValueError("DeepH artifact path must be a file below project root")
    return DeepHProducedArtifact(
        root_relative_path=resolved.relative_to(root).as_posix(),
        sha256=_sha256_file(resolved),
        size_bytes=resolved.stat().st_size,
        media_type=media_type,
    )


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()
