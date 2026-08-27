"""No-shell client and verified completion flow for Uni-HamGNN."""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from material_agent.ml_screening.uniham_models import (
    UniHamBenchmarkBinding,
    UniHamBenchmarkPolicy,
    UniHamExecutionPlan,
    UniHamInferenceRequest,
    UniHamResult,
    UniHamWorkerRequest,
    UniHamWorkerResponse,
    UniHamWorkerStatus,
)
from material_agent.ml_screening.uniham_planner import build_uniham_plan
from material_agent.ml_screening.worker_protocol import capture_artifact_tree
from material_agent.retrieval.storage import LocalArtifactStore


class UniHamProcessError(RuntimeError):
    def __init__(self, category: str, message: str) -> None:
        super().__init__(message)
        self.category = category


@dataclass(frozen=True)
class UniHamSubprocessClient:
    worker_python: Path
    predictor_script: Path
    artifact_root: Path
    worker_script: Path | None = None
    cuda_visible_devices: str | None = None

    def __post_init__(self) -> None:
        if self.cuda_visible_devices is not None and not re.fullmatch(
            r"\d+", self.cuda_visible_devices
        ):
            raise ValueError(
                "Uni-HamGNN cuda_visible_devices must be one GPU index"
            )

    def run(self, request: UniHamWorkerRequest) -> UniHamWorkerResponse:
        root = self.artifact_root.resolve(strict=True)
        worker_python = self.worker_python.resolve(strict=True)
        if self.predictor_script.is_symlink():
            raise UniHamProcessError(
                "WORKER_CONFIGURATION_ERROR",
                "configured Uni-HamGNN predictor cannot be a symlink",
            )
        predictor = self.predictor_script.resolve(strict=True)
        worker_script = (
            self.worker_script or Path(__file__).with_name("uniham_worker.py")
        ).resolve(strict=True)
        if not worker_python.is_file() or not os.access(worker_python, os.X_OK):
            raise UniHamProcessError(
                "WORKER_CONFIGURATION_ERROR",
                "configured Uni-HamGNN worker Python is not executable",
            )
        if not predictor.is_file():
            raise UniHamProcessError(
                "WORKER_CONFIGURATION_ERROR",
                "configured Uni-HamGNN predictor is not a regular file",
            )
        if _sha256_file(predictor) != request.plan.request.predictor_script_sha256:
            raise UniHamProcessError(
                "WORKER_CONFIGURATION_ERROR",
                "configured Uni-HamGNN predictor hash differs from request",
            )
        if not worker_script.is_file():
            raise UniHamProcessError(
                "WORKER_CONFIGURATION_ERROR", "Uni-HamGNN worker is missing"
            )
        sandbox = root.joinpath(
            *request.plan.output_sandbox_relative_path.split("/")
        )
        sandbox.mkdir(parents=True, exist_ok=True)
        if sandbox.is_symlink() or any(sandbox.iterdir()):
            raise UniHamProcessError(
                "OUTPUT_SANDBOX_CONFLICT",
                "Uni-HamGNN output sandbox must be empty",
            )
        before = capture_artifact_tree(root)
        command = [
            str(worker_python),
            str(worker_script),
            "--artifact-root",
            str(root),
            "--predictor-script",
            str(predictor),
        ]
        environment = {
            "PATH": os.environ.get("PATH", ""),
            "TMPDIR": os.environ.get("TMPDIR", "/tmp"),
            "PYTHONNOUSERSITE": "1",
            "PYTHONDONTWRITEBYTECODE": "1",
            "LANG": os.environ.get("LANG", "C.UTF-8"),
        }
        if request.plan.request.device == "cuda":
            if self.cuda_visible_devices is None:
                raise UniHamProcessError(
                    "WORKER_CONFIGURATION_ERROR",
                    "Uni-HamGNN CUDA request requires an explicit GPU index",
                )
            environment["CUDA_VISIBLE_DEVICES"] = self.cuda_visible_devices
        else:
            environment["CUDA_VISIBLE_DEVICES"] = ""
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
            raise UniHamProcessError(
                "WORKER_TIMEOUT", "Uni-HamGNN worker exceeded its time limit"
            ) from exc
        except OSError as exc:
            raise UniHamProcessError(
                "WORKER_START_FAILED",
                f"Uni-HamGNN worker could not start: {type(exc).__name__}",
            ) from exc
        if process.returncode != 0:
            raise UniHamProcessError(
                "WORKER_PROCESS_FAILED",
                f"Uni-HamGNN worker exited with code {process.returncode}",
            )
        if len(stdout) > request.limits.max_stdout_bytes:
            raise UniHamProcessError(
                "WORKER_STDOUT_LIMIT", "Uni-HamGNN worker stdout is too large"
            )
        try:
            response = UniHamWorkerResponse.model_validate(
                json.loads(stdout.decode("utf-8"))
            )
        except Exception as exc:
            raise UniHamProcessError(
                "INVALID_WORKER_RESPONSE",
                f"Uni-HamGNN response is invalid: {exc}",
            ) from exc
        if response.operation_key != request.plan.operation_key:
            raise UniHamProcessError(
                "INVALID_WORKER_RESPONSE", "Uni-HamGNN operation key mismatch"
            )
        if response.is_mock != request.plan.request.is_mock:
            raise UniHamProcessError(
                "INVALID_WORKER_RESPONSE", "Uni-HamGNN mock marker mismatch"
            )
        if response.status is UniHamWorkerStatus.FAILED:
            raise UniHamProcessError(
                "UNIHAM_INFERENCE_FAILED", "; ".join(response.errors)
            )
        self._validate_outputs(request, response, before)
        return response

    def _validate_outputs(
        self,
        request: UniHamWorkerRequest,
        response: UniHamWorkerResponse,
        before: dict,
    ) -> None:
        root = self.artifact_root.resolve(strict=True)
        sandbox = root.joinpath(
            *request.plan.output_sandbox_relative_path.split("/")
        ).resolve(strict=True)
        reported: set[str] = set()
        total = 0
        for artifact in response.produced_artifacts:
            path = root.joinpath(*artifact.root_relative_path.split("/"))
            if path.is_symlink():
                raise UniHamProcessError(
                    "INVALID_WORKER_RESPONSE", "Uni-HamGNN output is a symlink"
                )
            resolved = path.resolve(strict=True)
            if sandbox not in resolved.parents:
                raise UniHamProcessError(
                    "INVALID_WORKER_RESPONSE", "Uni-HamGNN output escaped sandbox"
                )
            if resolved.stat().st_size != artifact.size_bytes:
                raise UniHamProcessError(
                    "INVALID_WORKER_RESPONSE", "Uni-HamGNN output size mismatch"
                )
            if artifact.size_bytes > request.limits.max_single_artifact_bytes:
                raise UniHamProcessError(
                    "INVALID_WORKER_RESPONSE", "Uni-HamGNN output is too large"
                )
            if _sha256_file(resolved) != artifact.sha256:
                raise UniHamProcessError(
                    "INVALID_WORKER_RESPONSE", "Uni-HamGNN output hash mismatch"
                )
            total += artifact.size_bytes
            reported.add(artifact.root_relative_path)
        if len(reported) != len(response.produced_artifacts):
            raise UniHamProcessError(
                "INVALID_WORKER_RESPONSE", "Uni-HamGNN output paths repeat"
            )
        if total > request.limits.max_total_output_bytes:
            raise UniHamProcessError(
                "INVALID_WORKER_RESPONSE", "Uni-HamGNN outputs are too large"
            )
        actual = {
            path.relative_to(root).as_posix()
            for path in sandbox.rglob("*")
            if path.is_file()
        }
        if actual != reported:
            raise UniHamProcessError(
                "INVALID_WORKER_RESPONSE", "Uni-HamGNN output set mismatch"
            )
        after = capture_artifact_tree(root)
        changed = {
            path
            for path in set(before) | set(after)
            if before.get(path) != after.get(path)
        }
        if changed != reported:
            raise UniHamProcessError(
                "INVALID_WORKER_RESPONSE",
                "Uni-HamGNN worker wrote outside declared outputs",
            )


class UniHamFlowRunner:
    """Persist an immutable Uni-HamGNN plan/result and completion record."""

    def __init__(
        self,
        *,
        artifact_store: LocalArtifactStore,
        client: UniHamSubprocessClient,
        benchmark_policy: UniHamBenchmarkPolicy | None = None,
    ) -> None:
        if artifact_store.root != client.artifact_root.resolve():
            raise ValueError("Uni-HamGNN store and artifact roots must match")
        self.store = artifact_store
        self.client = client
        self.benchmark_policy = benchmark_policy or UniHamBenchmarkPolicy()

    def execute(
        self,
        request: UniHamInferenceRequest,
        *,
        created_at: datetime | None = None,
    ) -> UniHamResult:
        plan = build_uniham_plan(
            request,
            artifact_root=self.store.root,
            created_at=created_at or datetime.now(UTC),
        )
        plan_relative = (
            f"plans/{request.run_id}/stages/ml/"
            f"uniham-{plan.operation_key}.json"
        )
        if self.store.exists(plan_relative):
            frozen = UniHamExecutionPlan.model_validate(
                self.store.read_json(plan_relative)
            )
            if frozen.request != request:
                raise ValueError("existing Uni-HamGNN plan conflicts with request")
            plan = frozen
            plan_ref = self.store.inspect(
                plan_relative, media_type="application/json"
            )
        else:
            plan_ref = self.store.write_json(
                plan_relative, plan.model_dump(mode="json"), immutable=True
            )
        operation_root = (
            f"stages/agent02/{request.run_id}/uniham/{plan.operation_key}"
        )
        result_relative = f"{operation_root}/result.json"
        completion_relative = f"{operation_root}/operation-complete.json"
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
                raise ValueError("completed Uni-HamGNN operation failed integrity")
            return UniHamResult.model_validate(self.store.read_json(result_relative))
        if self.store.exists(result_relative):
            raise ValueError(
                "Uni-HamGNN result exists without a completion record"
            )
        response = self.client.run(UniHamWorkerRequest(plan=plan))
        hamiltonians = [
            item
            for item in response.produced_artifacts
            if item.root_relative_path.endswith("/output/hamiltonian.npy")
        ]
        if len(hamiltonians) != 1:
            raise UniHamProcessError(
                "INVALID_WORKER_RESPONSE",
                "Uni-HamGNN must produce exactly one Hamiltonian artifact",
            )
        benchmark_status, evidence_level, benchmark, benchmark_warning = (
            audit_uniham_benchmark(request, self.benchmark_policy)
        )
        result = UniHamResult(
            project_id=request.project_id,
            run_id=request.run_id,
            candidate_id=request.candidate_id,
            operation_key=plan.operation_key,
            plan_uri=plan_ref.uri,
            plan_sha256=plan_ref.sha256,
            status=response.status,
            hamiltonian_artifact=hamiltonians[0],
            execution_artifacts=response.produced_artifacts,
            model_id=request.model_id,
            hamgnn_source_revision=request.hamgnn_source_revision,
            device=request.device,
            runtime_provenance=response.runtime_provenance,
            is_mock=response.is_mock,
            evidence_level=evidence_level,
            benchmark_status=benchmark_status,
            benchmark=benchmark,
            warnings=[
                *response.warnings,
                benchmark_warning,
            ],
            errors=response.errors,
        )
        result_ref = self.store.write_json(
            result_relative, result.model_dump(mode="json"), immutable=True
        )
        self.store.write_json(
            completion_relative,
            {
                "schema_version": "agent02-uniham-operation-complete-v1",
                "operation_key": plan.operation_key,
                "plan_sha256": plan_ref.sha256,
                "result_sha256": result_ref.sha256,
            },
            immutable=True,
        )
        return result


def audit_uniham_benchmark(
    request: UniHamInferenceRequest,
    policy: UniHamBenchmarkPolicy,
) -> tuple[
    str,
    str,
    UniHamBenchmarkBinding | None,
    str,
]:
    benchmark = request.benchmark
    if benchmark is None:
        return (
            "NOT_RUN",
            "NONE",
            None,
            "Evidence remains NONE until a separate benchmark and review.",
        )
    metrics = benchmark.metrics
    passed = (
        benchmark.held_out_structure_count >= policy.minimum_held_out_structures
        and (
            not policy.require_two_dimensional_examples
            or 2 in benchmark.dimensionalities
        )
        and metrics.hamiltonian_mae_ev <= policy.maximum_hamiltonian_mae_ev
        and metrics.band_energy_mae_ev <= policy.maximum_band_energy_mae_ev
        and metrics.flat_band_width_mae_ev
        <= policy.maximum_flat_band_width_mae_ev
        and metrics.soc_gap_mae_ev <= policy.maximum_soc_gap_mae_ev
        and metrics.band_ordering_accuracy
        >= policy.minimum_band_ordering_accuracy
        and metrics.crossing_classification_accuracy
        >= policy.minimum_crossing_classification_accuracy
    )
    if not passed:
        return (
            "FAILED",
            "NONE",
            None,
            "Bound benchmark failed the frozen Hermes L2 screening policy.",
        )
    return (
        "VALIDATED",
        "L2_ML_SCREENED",
        benchmark,
        "L2 applies only to the benchmarked Hamiltonian screening domain.",
    )


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()
