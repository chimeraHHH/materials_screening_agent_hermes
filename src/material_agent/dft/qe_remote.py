"""No-shell client and strict contracts for the Hermes QE remote worker."""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Literal

from pydantic import Field, model_validator

from material_agent.dft.qe import QEPWResult, parse_qe_pw_output
from material_agent.inspiration.models import (
    ArtifactPointerV1,
    Identifier,
    StrictModel,
    canonical_sha256,
)
from material_agent.retrieval.storage import LocalArtifactStore

PROTOCOL = "hermes-qe-remote-scf-v1"


class QERemoteInputArtifact(StrictModel):
    pointer: ArtifactPointerV1
    remote_relative_path: str

    @model_validator(mode="after")
    def validate_path(self) -> QERemoteInputArtifact:
        _validate_relative(self.remote_relative_path)
        if self.pointer.size_bytes is None or self.pointer.size_bytes <= 0:
            raise ValueError("QE remote input requires a positive Artifact size")
        return self


class QERemoteSCFRequest(StrictModel):
    schema_version: Literal["hermes-qe-remote-scf-v1"] = PROTOCOL
    job_id: Identifier
    pw_binary_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    scf_input_relative_path: str
    inputs: tuple[QERemoteInputArtifact, ...] = Field(min_length=2, max_length=256)
    mpi_processes: int = Field(default=1, ge=1, le=32)
    omp_threads: int = Field(default=1, ge=1, le=32)
    wall_time_seconds: int = Field(default=3_600, ge=1, le=86_400)
    is_mock: Literal[False] = False

    @model_validator(mode="after")
    def validate_request(self) -> QERemoteSCFRequest:
        paths = tuple(item.remote_relative_path for item in self.inputs)
        if paths != tuple(sorted(set(paths))):
            raise ValueError("QE remote inputs must be path-sorted and unique")
        _validate_relative(self.scf_input_relative_path)
        if self.scf_input_relative_path not in paths:
            raise ValueError("QE remote SCF input is absent from inputs")
        if not any(path.startswith("pseudo/") for path in paths):
            raise ValueError("QE remote request requires pseudopotentials")
        expected = _qe_job_id(
            self.model_dump(mode="python", exclude={"job_id"})
        )
        if self.job_id != expected:
            raise ValueError("QE remote job ID differs from request content")
        return self

    def worker_payload(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "job_id": self.job_id,
            "pw_binary_sha256": self.pw_binary_sha256,
            "scf_input_relative_path": self.scf_input_relative_path,
            "inputs": [
                {
                    "remote_relative_path": item.remote_relative_path,
                    "sha256": item.pointer.sha256,
                    "size_bytes": item.pointer.size_bytes,
                }
                for item in self.inputs
            ],
            "mpi_processes": self.mpi_processes,
            "omp_threads": self.omp_threads,
            "wall_time_seconds": self.wall_time_seconds,
            "is_mock": False,
        }


class QERemoteScientificBinding(StrictModel):
    request: QERemoteSCFRequest
    source_structure_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    method_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    magnetic_configuration_id: Identifier
    soc_explicit: bool


class QERemoteOutputArtifact(StrictModel):
    remote_relative_path: str
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    size_bytes: int = Field(ge=0)
    media_type: str


class QERemoteCompletion(StrictModel):
    schema_version: Literal["hermes-qe-remote-scf-v1"] = PROTOCOL
    job_id: Identifier
    request_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    status: Literal["SUCCEEDED", "FAILED"]
    return_code: int
    job_done: bool
    wall_time_seconds: float = Field(ge=0)
    pw_binary_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    output_artifacts: tuple[QERemoteOutputArtifact, ...]
    scientific_conclusion: Literal[False] = False


class QERemoteSCFResult(StrictModel):
    result_id: Identifier
    request: QERemoteSCFRequest
    completion: QERemoteCompletion
    local_output_artifacts: tuple[ArtifactPointerV1, ...]
    parsed_pw_result: QEPWResult
    host_alias: Identifier
    is_mock: Literal[False] = False
    scientific_conclusion: Literal[False] = False

    @model_validator(mode="after")
    def validate_result(self) -> QERemoteSCFResult:
        if self.completion.job_id != self.request.job_id:
            raise ValueError("QE completion job differs from request")
        if self.completion.pw_binary_sha256 != self.request.pw_binary_sha256:
            raise ValueError("QE completion binary differs from request")
        expected = canonical_sha256(
            self.model_dump(mode="python", exclude={"result_id"})
        )
        if self.result_id != f"qe-result-{expected[:24]}":
            raise ValueError("QE remote result ID differs from content")
        return self


class QERemoteScientificSCFSummary(StrictModel):
    """Immutable bridge from one scientific DAG node to one real QE run."""

    binding: QERemoteScientificBinding
    remote_result: QERemoteSCFResult
    scientific_conclusion: Literal[False] = False

    @model_validator(mode="after")
    def validate_lineage(self) -> QERemoteScientificSCFSummary:
        if self.binding.request != self.remote_result.request:
            raise ValueError("scientific QE binding differs from executed request")
        return self


def build_qe_remote_scf_request(
    *,
    pw_binary_sha256: str,
    scf_input: ArtifactPointerV1,
    pseudopotentials: dict[str, ArtifactPointerV1],
    mpi_processes: int = 1,
    omp_threads: int = 1,
    wall_time_seconds: int = 3_600,
) -> QERemoteSCFRequest:
    inputs = [
        QERemoteInputArtifact(
            pointer=scf_input,
            remote_relative_path="scf.in",
        )
    ]
    inputs.extend(
        QERemoteInputArtifact(
            pointer=pointer,
            remote_relative_path=f"pseudo/{filename}",
        )
        for filename, pointer in sorted(pseudopotentials.items())
    )
    values = {
        "schema_version": PROTOCOL,
        "pw_binary_sha256": pw_binary_sha256,
        "scf_input_relative_path": "scf.in",
        "inputs": tuple(sorted(inputs, key=lambda item: item.remote_relative_path)),
        "mpi_processes": mpi_processes,
        "omp_threads": omp_threads,
        "wall_time_seconds": wall_time_seconds,
        "is_mock": False,
    }
    return QERemoteSCFRequest(job_id=_qe_job_id(values), **values)


@dataclass(frozen=True)
class QERemoteClient:
    artifact_store: LocalArtifactStore
    ssh_host_alias: str
    remote_worker_path: str
    remote_stack_root: str
    remote_run_root: str
    ssh_executable: str = "/usr/bin/ssh"
    scp_executable: str = "/usr/bin/scp"
    remote_python: str = "/usr/bin/python3"

    def __post_init__(self) -> None:
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", self.ssh_host_alias):
            raise ValueError("QE SSH host alias is unsafe")
        for value in (
            self.remote_worker_path,
            self.remote_stack_root,
            self.remote_run_root,
            self.remote_python,
        ):
            _validate_remote_absolute(value)

    def run_scf(self, request: QERemoteSCFRequest) -> QERemoteSCFResult:
        for item in request.inputs:
            if not self.artifact_store.exists_with_hash(
                item.pointer.uri,
                item.pointer.sha256,
            ):
                raise ValueError("QE local input Artifact failed hash verification")
        payload = json.dumps(
            request.worker_payload(),
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        prepare = self._ssh(("prepare",), stdin=payload)
        prepared = json.loads(prepare.decode("utf-8"))
        if prepared.get("job_id") != request.job_id:
            raise ValueError("QE remote prepare returned a different job")
        for item in request.inputs:
            source = self.artifact_store.root.joinpath(
                *item.pointer.uri.removeprefix("artifact://").split("/")
            ).resolve(strict=True)
            target = (
                f"{self.ssh_host_alias}:{self.remote_run_root}/"
                f"{request.job_id}/{item.remote_relative_path}"
            )
            self._run_process((self.scp_executable, "-q", str(source), target))
        completion_payload = self._ssh(
            ("execute", "--job-id", request.job_id),
            timeout=request.wall_time_seconds + 30,
        )
        completion = QERemoteCompletion.model_validate_json(completion_payload)
        if completion.job_id != request.job_id:
            raise ValueError("QE remote completion returned a different job")
        local_outputs: list[ArtifactPointerV1] = []
        stdout_text: str | None = None
        with tempfile.TemporaryDirectory(prefix="hermes-qe-fetch-") as temporary:
            temporary_root = Path(temporary)
            for artifact in completion.output_artifacts:
                _validate_relative(artifact.remote_relative_path)
                destination = temporary_root / PurePosixPath(
                    artifact.remote_relative_path
                ).name
                remote = (
                    f"{self.ssh_host_alias}:{self.remote_run_root}/"
                    f"{request.job_id}/{artifact.remote_relative_path}"
                )
                self._run_process(
                    (self.scp_executable, "-q", remote, str(destination))
                )
                data = destination.read_bytes()
                if (
                    len(data) != artifact.size_bytes
                    or hashlib.sha256(data).hexdigest() != artifact.sha256
                ):
                    raise ValueError("QE downloaded output Artifact failed integrity")
                reference = self.artifact_store.write_bytes(
                    (
                        f"scientific_loop/remote/qe/{request.job_id}/"
                        f"{artifact.remote_relative_path}"
                    ),
                    data,
                    artifact.media_type,
                    immutable=True,
                )
                local_outputs.append(
                    ArtifactPointerV1(
                        uri=reference.uri,
                        sha256=reference.sha256,
                        size_bytes=reference.size_bytes,
                        media_type=reference.media_type,
                    )
                )
                if artifact.remote_relative_path == "pw.out":
                    stdout_text = data.decode("utf-8")
        if stdout_text is None:
            raise ValueError("QE completion did not publish pw.out")
        parsed = parse_qe_pw_output(stdout_text)
        values = {
            "request": request,
            "completion": completion,
            "local_output_artifacts": tuple(local_outputs),
            "parsed_pw_result": parsed,
            "host_alias": self.ssh_host_alias,
            "is_mock": False,
            "scientific_conclusion": False,
        }
        digest = canonical_sha256(values)
        return QERemoteSCFResult(
            result_id=f"qe-result-{digest[:24]}",
            **values,
        )

    def _ssh(
        self,
        trailing: tuple[str, ...],
        *,
        stdin: bytes | None = None,
        timeout: int = 120,
    ) -> bytes:
        command = (
            self.ssh_executable,
            "-x",
            self.ssh_host_alias,
            self.remote_python,
            self.remote_worker_path,
            "--run-root",
            self.remote_run_root,
            "--stack-root",
            self.remote_stack_root,
            *trailing,
        )
        return self._run_process(command, stdin=stdin, timeout=timeout)

    @staticmethod
    def _run_process(
        command: tuple[str, ...],
        *,
        stdin: bytes | None = None,
        timeout: int = 120,
    ) -> bytes:
        environment = {
            "PATH": os.environ.get("PATH", ""),
            "LANG": os.environ.get("LANG", "C.UTF-8"),
        }
        completed = subprocess.run(
            command,
            input=stdin,
            capture_output=True,
            check=False,
            timeout=timeout,
            env=environment,
        )
        if completed.returncode != 0:
            raise RuntimeError(
                f"QE remote transport failed with code {completed.returncode}"
            )
        return completed.stdout


def _qe_job_id(values: object) -> str:
    return f"qe-{canonical_sha256(values)[:24]}"


def _validate_relative(value: str) -> None:
    if "\\" in value:
        raise ValueError("QE relative path must use POSIX separators")
    path = PurePosixPath(value)
    if path.is_absolute() or value in {"", "."}:
        raise ValueError("QE path must be non-empty and relative")
    if any(part in {"", ".", ".."} for part in path.parts):
        raise ValueError("QE relative path contains traversal")
    if path.as_posix() != value:
        raise ValueError("QE relative path is not canonical")


def _validate_remote_absolute(value: str) -> None:
    if not re.fullmatch(r"/[A-Za-z0-9._/-]+", value):
        raise ValueError("QE remote path contains unsafe characters")
    path = PurePosixPath(value)
    if any(part in {"", ".", ".."} for part in path.parts[1:]):
        raise ValueError("QE remote path contains dot segments")
