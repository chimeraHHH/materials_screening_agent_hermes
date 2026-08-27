"""SSH client for hash-bound learned-Hamiltonian band postprocessing."""

from __future__ import annotations

import hashlib
import os
import re
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Literal

from pydantic import Field, model_validator

from material_agent.inspiration.models import (
    ArtifactPointerV1,
    Identifier,
    StrictModel,
    canonical_sha256,
)
from material_agent.ml_screening.uniham_band_models import (
    UniHamBandExecutionPlan,
    UniHamBandProducedArtifact,
    UniHamBandRequest,
    UniHamBandWorkerLimits,
    UniHamBandWorkerRequest,
    UniHamBandWorkerResponse,
    UniHamBandWorkerStatus,
    build_uniham_band_plan,
)
from material_agent.retrieval.models import ArtifactRef
from material_agent.retrieval.storage import LocalArtifactStore


class UniHamBandRemoteArtifactCopy(StrictModel):
    remote_artifact: UniHamBandProducedArtifact
    local_pointer: ArtifactPointerV1

    @model_validator(mode="after")
    def validate_copy(self) -> UniHamBandRemoteArtifactCopy:
        if (
            self.remote_artifact.sha256 != self.local_pointer.sha256
            or self.remote_artifact.size_bytes != self.local_pointer.size_bytes
        ):
            raise ValueError("local band Artifact differs from remote output")
        return self


class UniHamBandRemoteResult(StrictModel):
    result_id: Identifier
    host_alias: Identifier
    plan: UniHamBandExecutionPlan
    plan_pointer: ArtifactPointerV1
    worker_response: UniHamBandWorkerResponse
    local_artifacts: tuple[UniHamBandRemoteArtifactCopy, ...] = Field(min_length=5)
    band_data_pointer: ArtifactPointerV1
    band_plot_pointer: ArtifactPointerV1
    structure_pointer: ArtifactPointerV1
    is_mock: Literal[False] = False
    scientific_conclusion: Literal[False] = False

    @model_validator(mode="after")
    def validate_result(self) -> UniHamBandRemoteResult:
        if self.worker_response.operation_key != self.plan.operation_key:
            raise ValueError("remote band response differs from plan")
        if self.worker_response.status is not UniHamBandWorkerStatus.SUCCEEDED:
            raise ValueError("remote band result requires worker success")
        pointers = {
            Path(item.remote_artifact.root_relative_path).suffix: item.local_pointer
            for item in self.local_artifacts
        }
        if (
            pointers.get(".dat") != self.band_data_pointer
            or pointers.get(".png") != self.band_plot_pointer
            or pointers.get(".cif") != self.structure_pointer
        ):
            raise ValueError("remote band result output linkage is invalid")
        expected = _result_id(
            self.model_dump(mode="python", exclude={"result_id"})
        )
        if self.result_id != expected:
            raise ValueError("remote band result ID differs from content")
        return self


@dataclass(frozen=True)
class UniHamBandRemoteClient:
    artifact_store: LocalArtifactStore
    ssh_host_alias: str
    remote_artifact_root: str
    remote_worker_python: str
    remote_worker_path: str
    remote_band_cal_executable: str
    ssh_executable: str = "/usr/bin/ssh"
    scp_executable: str = "/usr/bin/scp"

    def __post_init__(self) -> None:
        if not re.fullmatch(
            r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", self.ssh_host_alias
        ):
            raise ValueError("band SSH host alias is unsafe")
        for value in (
            self.remote_artifact_root,
            self.remote_worker_python,
            self.remote_worker_path,
            self.remote_band_cal_executable,
        ):
            _validate_remote_absolute(value)

    def run(
        self,
        request: UniHamBandRequest,
        *,
        limits: UniHamBandWorkerLimits | None = None,
    ) -> UniHamBandRemoteResult:
        plan = build_uniham_band_plan(request)
        local_root = (
            f"scientific_loop/remote/uniham-band/{request.run_id}/"
            f"{plan.operation_key}"
        )
        plan_pointer = self._freeze_plan(local_root, plan)
        cached = self._load_completed(local_root, plan_pointer)
        if cached is not None:
            return cached
        worker_request = UniHamBandWorkerRequest(
            plan=plan,
            limits=limits or UniHamBandWorkerLimits(),
        )
        response_bytes = self._run_process(
            (
                self.ssh_executable,
                "-x",
                self.ssh_host_alias,
                self.remote_worker_python,
                self.remote_worker_path,
                "--artifact-root",
                self.remote_artifact_root,
                "--band-cal-executable",
                self.remote_band_cal_executable,
            ),
            stdin=worker_request.model_dump_json().encode(),
            timeout=worker_request.limits.wall_time_seconds + 30,
        )
        response = UniHamBandWorkerResponse.model_validate_json(response_bytes)
        if response.status is not UniHamBandWorkerStatus.SUCCEEDED:
            raise RuntimeError("remote learned-Hamiltonian band worker failed closed")
        copies = self._fetch(local_root, plan, response)
        pointers = {
            Path(item.remote_artifact.root_relative_path).suffix: item.local_pointer
            for item in copies
        }
        values = {
            "host_alias": self.ssh_host_alias,
            "plan": plan,
            "plan_pointer": plan_pointer,
            "worker_response": response,
            "local_artifacts": copies,
            "band_data_pointer": pointers[".dat"],
            "band_plot_pointer": pointers[".png"],
            "structure_pointer": pointers[".cif"],
            "is_mock": False,
            "scientific_conclusion": False,
        }
        normalized = UniHamBandRemoteResult.model_construct(
            result_id="uniham-band-remote-pending",
            **values,
        ).model_dump(mode="python", exclude={"result_id"})
        result = UniHamBandRemoteResult(
            result_id=_result_id(normalized),
            **values,
        )
        result_ref = self.artifact_store.write_json(
            f"{local_root}/result.json",
            result.model_dump(mode="json"),
            immutable=True,
        )
        self.artifact_store.write_json(
            f"{local_root}/operation-complete.json",
            {
                "operation_key": plan.operation_key,
                "plan_sha256": plan_pointer.sha256,
                "result_sha256": result_ref.sha256,
                "schema_version": "agent02-uniham-band-remote-complete-v1",
            },
            immutable=True,
        )
        return result

    def _freeze_plan(
        self,
        local_root: str,
        plan: UniHamBandExecutionPlan,
    ) -> ArtifactPointerV1:
        relative = f"{local_root}/plan.json"
        if self.artifact_store.exists(relative):
            existing = UniHamBandExecutionPlan.model_validate_json(
                self.artifact_store.read_bytes(relative)
            )
            if existing != plan:
                raise ValueError("existing remote band plan differs")
            reference = self.artifact_store.inspect(
                relative, media_type="application/json"
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
        local_root: str,
        plan_pointer: ArtifactPointerV1,
    ) -> UniHamBandRemoteResult | None:
        completion_path = f"{local_root}/operation-complete.json"
        result_path = f"{local_root}/result.json"
        if not self.artifact_store.exists(completion_path):
            if self.artifact_store.exists(result_path):
                raise ValueError("remote band result lacks completion ledger")
            return None
        completion = self.artifact_store.read_json(completion_path)
        result_ref = self.artifact_store.inspect(
            result_path, media_type="application/json"
        )
        if (
            completion.get("plan_sha256") != plan_pointer.sha256
            or completion.get("result_sha256") != result_ref.sha256
        ):
            raise ValueError("remote band completion failed integrity")
        result = UniHamBandRemoteResult.model_validate_json(
            self.artifact_store.read_bytes(result_path)
        )
        for item in result.local_artifacts:
            if not self.artifact_store.exists_with_hash(
                item.local_pointer.uri,
                item.local_pointer.sha256,
            ):
                raise ValueError("cached remote band Artifact failed integrity")
        return result

    def _fetch(
        self,
        local_root: str,
        plan: UniHamBandExecutionPlan,
        response: UniHamBandWorkerResponse,
    ) -> tuple[UniHamBandRemoteArtifactCopy, ...]:
        prefix = f"{plan.output_sandbox_relative_path}/"
        copies = []
        with tempfile.TemporaryDirectory(prefix="hermes-uniham-band-fetch-") as temp:
            temporary = Path(temp)
            for artifact in response.produced_artifacts:
                if not artifact.root_relative_path.startswith(prefix):
                    raise ValueError("remote band Artifact escaped its sandbox")
                suffix = artifact.root_relative_path.removeprefix(prefix)
                _validate_relative(suffix)
                destination = temporary.joinpath(*suffix.split("/"))
                destination.parent.mkdir(parents=True, exist_ok=True)
                remote = (
                    f"{self.ssh_host_alias}:{self.remote_artifact_root}/"
                    f"{artifact.root_relative_path}"
                )
                self._run_process(
                    (self.scp_executable, "-q", remote, str(destination)),
                    timeout=300,
                )
                payload = destination.read_bytes()
                if (
                    len(payload) != artifact.size_bytes
                    or hashlib.sha256(payload).hexdigest() != artifact.sha256
                ):
                    raise ValueError("downloaded band Artifact failed integrity")
                reference = self.artifact_store.write_bytes(
                    f"{local_root}/outputs/{suffix}",
                    payload,
                    artifact.media_type,
                    immutable=True,
                )
                copies.append(
                    UniHamBandRemoteArtifactCopy(
                        remote_artifact=artifact,
                        local_pointer=_pointer(reference),
                    )
                )
        return tuple(
            sorted(copies, key=lambda item: item.remote_artifact.root_relative_path)
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
                f"Uni-HamGNN band remote transport failed with code "
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
    return f"uniham-band-{canonical_sha256(values)[:24]}"


def _validate_remote_absolute(value: str) -> None:
    if not re.fullmatch(r"/[A-Za-z0-9._/-]+", value):
        raise ValueError("band remote path contains unsafe characters")
    if any(part in {"", ".", ".."} for part in PurePosixPath(value).parts[1:]):
        raise ValueError("band remote path contains dot segments")


def _validate_relative(value: str) -> None:
    path = PurePosixPath(value)
    if (
        "\\" in value
        or path.is_absolute()
        or value in {"", "."}
        or any(part in {"", ".", ".."} for part in path.parts)
        or path.as_posix() != value
    ):
        raise ValueError("band relative path is unsafe")
