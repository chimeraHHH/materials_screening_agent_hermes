"""SSH transport for a hash-bound Uni-HamGNN worker on a GPU host."""

from __future__ import annotations

import hashlib
import os
import re
import subprocess
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Literal

from pydantic import Field, model_validator

from material_agent.inspiration.models import (
    ArtifactPointerV1,
    Identifier,
    StrictModel,
    canonical_sha256,
)
from material_agent.ml_screening.uniham_models import (
    UniHamExecutionPlan,
    UniHamGraphManifest,
    UniHamInferenceRequest,
    UniHamProducedArtifact,
    UniHamWorkerLimits,
    UniHamWorkerRequest,
    UniHamWorkerResponse,
    UniHamWorkerStatus,
)
from material_agent.ml_screening.uniham_planner import (
    build_uniham_plan_from_manifests,
)
from material_agent.retrieval.models import ArtifactRef
from material_agent.retrieval.storage import LocalArtifactStore


class UniHamRemoteArtifactCopy(StrictModel):
    remote_artifact: UniHamProducedArtifact
    local_pointer: ArtifactPointerV1

    @model_validator(mode="after")
    def validate_copy(self) -> UniHamRemoteArtifactCopy:
        if (
            self.local_pointer.sha256 != self.remote_artifact.sha256
            or self.local_pointer.size_bytes != self.remote_artifact.size_bytes
        ):
            raise ValueError("local Uni-HamGNN output differs from remote Artifact")
        return self


class UniHamRemoteExecutionResult(StrictModel):
    result_id: Identifier
    host_alias: Identifier
    plan: UniHamExecutionPlan
    plan_pointer: ArtifactPointerV1
    worker_response: UniHamWorkerResponse
    local_artifacts: tuple[UniHamRemoteArtifactCopy, ...] = Field(min_length=3)
    hamiltonian_pointer: ArtifactPointerV1
    is_mock: Literal[False]
    scientific_conclusion: Literal[False] = False

    @model_validator(mode="after")
    def validate_result(self) -> UniHamRemoteExecutionResult:
        if self.worker_response.operation_key != self.plan.operation_key:
            raise ValueError("remote Uni-HamGNN response differs from plan")
        if self.worker_response.status is not UniHamWorkerStatus.SUCCEEDED:
            raise ValueError("remote Uni-HamGNN result requires worker success")
        if self.is_mock != self.worker_response.is_mock:
            raise ValueError("remote Uni-HamGNN mock marker mismatch")
        hamiltonians = tuple(
            item.local_pointer
            for item in self.local_artifacts
            if item.remote_artifact.root_relative_path.endswith(
                "/output/hamiltonian.npy"
            )
        )
        if hamiltonians != (self.hamiltonian_pointer,):
            raise ValueError("remote Uni-HamGNN Hamiltonian linkage is invalid")
        expected = _result_id(
            self.model_dump(mode="python", exclude={"result_id"})
        )
        if self.result_id != expected:
            raise ValueError("remote Uni-HamGNN result ID differs from content")
        return self


@dataclass(frozen=True)
class UniHamRemoteClient:
    artifact_store: LocalArtifactStore
    ssh_host_alias: str
    remote_artifact_root: str
    remote_worker_python: str
    remote_worker_path: str
    remote_predictor_path: str
    cuda_visible_device: str
    ssh_executable: str = "/usr/bin/ssh"
    scp_executable: str = "/usr/bin/scp"

    def __post_init__(self) -> None:
        if not re.fullmatch(
            r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}",
            self.ssh_host_alias,
        ):
            raise ValueError("Uni-HamGNN SSH host alias is unsafe")
        if not re.fullmatch(r"\d+", self.cuda_visible_device):
            raise ValueError("Uni-HamGNN remote client requires one GPU index")
        for value in (
            self.remote_artifact_root,
            self.remote_worker_python,
            self.remote_worker_path,
            self.remote_predictor_path,
        ):
            _validate_remote_absolute(value)

    def run(
        self,
        request: UniHamInferenceRequest,
        *,
        non_soc_manifest: UniHamGraphManifest,
        soc_manifest: UniHamGraphManifest,
        limits: UniHamWorkerLimits | None = None,
        created_at: datetime | None = None,
    ) -> UniHamRemoteExecutionResult:
        if request.device != "cuda":
            raise ValueError("GPU remote client requires a CUDA Uni-HamGNN request")
        plan = build_uniham_plan_from_manifests(
            request,
            non_soc_manifest=non_soc_manifest,
            soc_manifest=soc_manifest,
            created_at=created_at or request.input_trust.reviewed_at.astimezone(UTC),
        )
        operation_root = (
            f"scientific_loop/remote/uniham/{request.run_id}/"
            f"{plan.operation_key}"
        )
        plan_pointer = self._freeze_plan(operation_root, plan)
        cached = self._load_completed(operation_root, plan_pointer)
        if cached is not None:
            return cached
        worker_request = UniHamWorkerRequest(
            plan=plan,
            limits=limits or UniHamWorkerLimits(),
        )
        response_bytes = self._run_process(
            (
                self.ssh_executable,
                "-x",
                self.ssh_host_alias,
                "/usr/bin/env",
                f"CUDA_VISIBLE_DEVICES={self.cuda_visible_device}",
                self.remote_worker_python,
                self.remote_worker_path,
                "--artifact-root",
                self.remote_artifact_root,
                "--predictor-script",
                self.remote_predictor_path,
            ),
            stdin=worker_request.model_dump_json().encode("utf-8"),
            timeout=worker_request.limits.wall_time_seconds + 30,
        )
        response = UniHamWorkerResponse.model_validate_json(response_bytes)
        if response.operation_key != plan.operation_key:
            raise ValueError("remote Uni-HamGNN operation key mismatch")
        if response.status is not UniHamWorkerStatus.SUCCEEDED:
            raise RuntimeError("remote Uni-HamGNN worker failed closed")
        if response.is_mock:
            raise ValueError("production Uni-HamGNN remote client rejects mock output")
        local_artifacts = self._fetch_outputs(
            operation_root=operation_root,
            plan=plan,
            response=response,
        )
        hamiltonians = tuple(
            item.local_pointer
            for item in local_artifacts
            if item.remote_artifact.root_relative_path.endswith(
                "/output/hamiltonian.npy"
            )
        )
        if len(hamiltonians) != 1:
            raise ValueError("remote Uni-HamGNN produced an invalid Hamiltonian set")
        values = {
            "host_alias": self.ssh_host_alias,
            "plan": plan,
            "plan_pointer": plan_pointer,
            "worker_response": response,
            "local_artifacts": local_artifacts,
            "hamiltonian_pointer": hamiltonians[0],
            "is_mock": response.is_mock,
            "scientific_conclusion": False,
        }
        normalized = UniHamRemoteExecutionResult.model_construct(
            result_id="uniham-remote-pending",
            **values,
        ).model_dump(mode="python", exclude={"result_id"})
        result = UniHamRemoteExecutionResult(
            result_id=_result_id(normalized),
            **values,
        )
        result_ref = self.artifact_store.write_json(
            f"{operation_root}/result.json",
            result.model_dump(mode="json"),
            immutable=True,
        )
        self.artifact_store.write_json(
            f"{operation_root}/operation-complete.json",
            {
                "operation_key": plan.operation_key,
                "plan_sha256": plan_pointer.sha256,
                "result_sha256": result_ref.sha256,
                "schema_version": "agent02-uniham-remote-complete-v1",
            },
            immutable=True,
        )
        return result

    def _freeze_plan(
        self,
        operation_root: str,
        plan: UniHamExecutionPlan,
    ) -> ArtifactPointerV1:
        relative = f"{operation_root}/plan.json"
        if self.artifact_store.exists(relative):
            frozen = UniHamExecutionPlan.model_validate_json(
                self.artifact_store.read_bytes(relative)
            )
            if frozen != plan:
                raise ValueError("existing remote Uni-HamGNN plan differs")
            reference = self.artifact_store.inspect(
                relative,
                media_type="application/json",
            )
        else:
            reference = self.artifact_store.write_json(
                relative,
                plan.model_dump(mode="json"),
                immutable=True,
            )
        return _pointer(reference)

    def _load_completed(
        self,
        operation_root: str,
        plan_pointer: ArtifactPointerV1,
    ) -> UniHamRemoteExecutionResult | None:
        completion_relative = f"{operation_root}/operation-complete.json"
        result_relative = f"{operation_root}/result.json"
        if not self.artifact_store.exists(completion_relative):
            if self.artifact_store.exists(result_relative):
                raise ValueError("remote Uni-HamGNN result lacks completion ledger")
            return None
        completion = self.artifact_store.read_json(completion_relative)
        result_ref = self.artifact_store.inspect(
            result_relative,
            media_type="application/json",
        )
        if (
            completion.get("plan_sha256") != plan_pointer.sha256
            or completion.get("result_sha256") != result_ref.sha256
        ):
            raise ValueError("remote Uni-HamGNN completion failed integrity")
        result = UniHamRemoteExecutionResult.model_validate_json(
            self.artifact_store.read_bytes(result_relative)
        )
        for item in result.local_artifacts:
            if not self.artifact_store.exists_with_hash(
                item.local_pointer.uri,
                item.local_pointer.sha256,
            ):
                raise ValueError("cached remote Uni-HamGNN output failed integrity")
        return result

    def _fetch_outputs(
        self,
        *,
        operation_root: str,
        plan: UniHamExecutionPlan,
        response: UniHamWorkerResponse,
    ) -> tuple[UniHamRemoteArtifactCopy, ...]:
        prefix = f"{plan.output_sandbox_relative_path}/"
        copies: list[UniHamRemoteArtifactCopy] = []
        with tempfile.TemporaryDirectory(prefix="hermes-uniham-fetch-") as temporary:
            temporary_root = Path(temporary)
            for artifact in response.produced_artifacts:
                if not artifact.root_relative_path.startswith(prefix):
                    raise ValueError("remote Uni-HamGNN output escaped its sandbox")
                suffix = artifact.root_relative_path.removeprefix(prefix)
                _validate_relative(suffix)
                destination = temporary_root.joinpath(*suffix.split("/"))
                destination.parent.mkdir(parents=True, exist_ok=True)
                remote = (
                    f"{self.ssh_host_alias}:{self.remote_artifact_root}/"
                    f"{artifact.root_relative_path}"
                )
                self._run_process(
                    (self.scp_executable, "-q", remote, str(destination)),
                    timeout=300,
                )
                data = destination.read_bytes()
                if (
                    len(data) != artifact.size_bytes
                    or hashlib.sha256(data).hexdigest() != artifact.sha256
                ):
                    raise ValueError("downloaded Uni-HamGNN output failed integrity")
                reference = self.artifact_store.write_bytes(
                    f"{operation_root}/outputs/{suffix}",
                    data,
                    artifact.media_type,
                    immutable=True,
                )
                copies.append(
                    UniHamRemoteArtifactCopy(
                        remote_artifact=artifact,
                        local_pointer=_pointer(reference),
                    )
                )
        return tuple(
            sorted(
                copies,
                key=lambda item: item.remote_artifact.root_relative_path,
            )
        )

    @staticmethod
    def _run_process(
        command: tuple[str, ...],
        *,
        stdin: bytes | None = None,
        timeout: int,
    ) -> bytes:
        completed = subprocess.run(
            command,
            input=stdin,
            capture_output=True,
            check=False,
            timeout=timeout,
            env={
                "PATH": os.environ.get("PATH", ""),
                "LANG": os.environ.get("LANG", "C.UTF-8"),
            },
        )
        if completed.returncode != 0:
            raise RuntimeError(
                "Uni-HamGNN remote transport failed with code "
                f"{completed.returncode}"
            )
        return completed.stdout


def _pointer(reference: ArtifactRef) -> ArtifactPointerV1:
    return ArtifactPointerV1(
        uri=reference.uri,
        sha256=reference.sha256,
        size_bytes=reference.size_bytes,
        media_type=reference.media_type,
    )


def _result_id(values: object) -> str:
    return f"uniham-remote-{canonical_sha256(values)[:24]}"


def _validate_remote_absolute(value: str) -> None:
    if not re.fullmatch(r"/[A-Za-z0-9._/-]+", value):
        raise ValueError("Uni-HamGNN remote path contains unsafe characters")
    path = PurePosixPath(value)
    if any(part in {"", ".", ".."} for part in path.parts[1:]):
        raise ValueError("Uni-HamGNN remote path contains dot segments")


def _validate_relative(value: str) -> None:
    if "\\" in value:
        raise ValueError("Uni-HamGNN relative path must use POSIX separators")
    path = PurePosixPath(value)
    if path.is_absolute() or value in {"", "."}:
        raise ValueError("Uni-HamGNN relative path must be non-empty")
    if any(part in {"", ".", ".."} for part in path.parts):
        raise ValueError("Uni-HamGNN relative path contains traversal")
    if path.as_posix() != value:
        raise ValueError("Uni-HamGNN relative path is not canonical")
