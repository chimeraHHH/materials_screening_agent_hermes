"""SSH client for hash-bound non-SCF Uni-HamGNN graph preparation."""

from __future__ import annotations

import hashlib
import os
import re
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from pydantic import Field, model_validator

from material_agent.inspiration.models import (
    ArtifactPointerV1,
    Identifier,
    StrictModel,
    deterministic_id,
)
from material_agent.ml_screening.uniham_graph_prep_models import (
    GraphPrepStatus,
    UniHamGraphPrepArtifact,
    UniHamGraphPrepPlan,
    UniHamGraphPrepRequest,
    UniHamGraphPrepToolchain,
    UniHamGraphPrepWorkerLimits,
    UniHamGraphPrepWorkerRequest,
    UniHamGraphPrepWorkerResponse,
    build_graph_prep_plan,
)
from material_agent.ml_screening.uniham_models import (
    UniHamArtifactFile,
    UniHamGraphBundle,
    UniHamGraphManifest,
    UniHamSocMode,
)
from material_agent.retrieval.storage import LocalArtifactStore


class UniHamGraphPrepRemoteResult(StrictModel):
    result_id: Identifier
    host_alias: Identifier
    plan: UniHamGraphPrepPlan
    response: UniHamGraphPrepWorkerResponse
    non_soc_graph: UniHamGraphBundle
    soc_graph: UniHamGraphBundle
    non_soc_manifest: UniHamGraphManifest
    soc_manifest: UniHamGraphManifest
    local_artifacts: tuple[ArtifactPointerV1, ...] = Field(min_length=2)
    self_consistent_dft_invocations: int = 0
    scientific_conclusion: bool = False

    @model_validator(mode="after")
    def validate_result(self) -> UniHamGraphPrepRemoteResult:
        if self.response.status is not GraphPrepStatus.SUCCEEDED:
            raise ValueError("remote graph-prep result requires worker success")
        if self.self_consistent_dft_invocations != 0:
            raise ValueError("ML-only graph prep cannot contain SCF DFT invocations")
        if (
            self.plan.request.input_structure.sha256
            != self.non_soc_manifest.structure_sha256
        ):
            raise ValueError("non-SOC graph structure lineage mismatch")
        if (
            self.plan.request.input_structure.sha256
            != self.soc_manifest.structure_sha256
        ):
            raise ValueError("SOC graph structure lineage mismatch")
        expected = deterministic_id(
            "uniham-graph-prep-remote",
            self.model_dump(mode="python", exclude={"result_id"}),
        )
        if self.result_id != expected:
            raise ValueError("remote graph-prep result ID differs from content")
        return self


@dataclass(frozen=True)
class UniHamGraphPrepRemoteClient:
    artifact_store: LocalArtifactStore
    ssh_host_alias: str
    remote_artifact_root: str
    remote_worker_python: str
    remote_source_root: str
    remote_openmx_postprocess: str
    remote_read_openmx: str
    remote_graph_data_gen: str
    remote_dft_data_root: str
    remote_hamgnn_source_root: str
    toolchain: UniHamGraphPrepToolchain
    ssh_executable: str = "/usr/bin/ssh"
    scp_executable: str = "/usr/bin/scp"

    def __post_init__(self) -> None:
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", self.ssh_host_alias):
            raise ValueError("graph-prep SSH host alias is unsafe")
        for value in (
            self.remote_artifact_root,
            self.remote_worker_python,
            self.remote_source_root,
            self.remote_openmx_postprocess,
            self.remote_read_openmx,
            self.remote_graph_data_gen,
            self.remote_dft_data_root,
            self.remote_hamgnn_source_root,
        ):
            _validate_remote_absolute(value)

    def run(
        self,
        request: UniHamGraphPrepRequest,
        *,
        limits: UniHamGraphPrepWorkerLimits | None = None,
    ) -> UniHamGraphPrepRemoteResult:
        plan = build_graph_prep_plan(request)
        local_root = (
            f"scientific_loop/remote/uniham-graph-prep/{request.run_id}/"
            f"{plan.operation_key}"
        )
        cached_path = f"{local_root}/result.json"
        if self.artifact_store.exists(cached_path):
            return UniHamGraphPrepRemoteResult.model_validate_json(
                self.artifact_store.read_bytes(cached_path)
            )
        source_path = self._local_path(request.input_structure)
        remote_input = _remote_join(
            self.remote_artifact_root, plan.input_root_relative_path
        )
        self._run_process(
            (
                self.ssh_executable,
                "-x",
                self.ssh_host_alias,
                "/usr/bin/mkdir",
                "-p",
                str(PurePosixPath(remote_input).parent),
            ),
            timeout=60,
        )
        self._run_process(
            (
                self.scp_executable,
                "-q",
                str(source_path),
                f"{self.ssh_host_alias}:{remote_input}",
            ),
            timeout=300,
        )
        worker_request = UniHamGraphPrepWorkerRequest(
            plan=plan,
            toolchain=self.toolchain,
            limits=limits or UniHamGraphPrepWorkerLimits(),
        )
        stdout = self._run_process(
            (
                self.ssh_executable,
                "-x",
                self.ssh_host_alias,
                "/usr/bin/env",
                f"PYTHONPATH={self.remote_source_root}",
                "PYTHONNOUSERSITE=1",
                self.remote_worker_python,
                "-m",
                "material_agent.ml_screening.uniham_graph_prep_worker",
                "--artifact-root",
                self.remote_artifact_root,
                "--openmx-postprocess",
                self.remote_openmx_postprocess,
                "--read-openmx",
                self.remote_read_openmx,
                "--graph-data-gen",
                self.remote_graph_data_gen,
                "--dft-data-root",
                self.remote_dft_data_root,
                "--hamgnn-source-root",
                self.remote_hamgnn_source_root,
            ),
            stdin=worker_request.model_dump_json().encode(),
            timeout=worker_request.limits.wall_time_seconds + 60,
        )
        if len(stdout) > worker_request.limits.max_stdout_bytes:
            raise ValueError("graph-prep worker stdout exceeds its limit")
        response = UniHamGraphPrepWorkerResponse.model_validate_json(stdout)
        if response.operation_key != plan.operation_key:
            raise ValueError("graph-prep response operation key mismatch")
        if response.status is not GraphPrepStatus.SUCCEEDED:
            raise RuntimeError("remote graph-prep worker failed closed")
        pointers = self._fetch(response.artifacts)
        by_remote = dict(zip(response.artifacts, pointers, strict=True))
        non_soc = self._bundle(
            plan.output_root_relative_path, UniHamSocMode.NON_SOC, by_remote
        )
        soc = self._bundle(plan.output_root_relative_path, UniHamSocMode.SOC, by_remote)
        values = {
            "host_alias": self.ssh_host_alias,
            "plan": plan,
            "response": response,
            "non_soc_graph": non_soc,
            "soc_graph": soc,
            "non_soc_manifest": response.non_soc_manifest,
            "soc_manifest": response.soc_manifest,
            "local_artifacts": tuple(sorted(pointers, key=lambda item: item.uri)),
            "self_consistent_dft_invocations": response.self_consistent_dft_invocations,
            "scientific_conclusion": False,
        }
        result = UniHamGraphPrepRemoteResult(
            result_id=deterministic_id("uniham-graph-prep-remote", values),
            **values,
        )
        self.artifact_store.write_json(
            cached_path, result.model_dump(mode="json"), immutable=True
        )
        return result

    def _fetch(
        self,
        artifacts: tuple[UniHamGraphPrepArtifact, ...],
    ) -> tuple[ArtifactPointerV1, ...]:
        pointers: list[ArtifactPointerV1] = []
        with tempfile.TemporaryDirectory(prefix="hermes-graph-prep-") as temporary:
            temp = Path(temporary)
            for artifact in artifacts:
                remote = _remote_join(
                    self.remote_artifact_root, artifact.root_relative_path
                )
                destination = temp / hashlib.sha256(remote.encode()).hexdigest()
                self._run_process(
                    (
                        self.scp_executable,
                        "-q",
                        f"{self.ssh_host_alias}:{remote}",
                        str(destination),
                    ),
                    timeout=300,
                )
                data = destination.read_bytes()
                if (
                    len(data) != artifact.size_bytes
                    or hashlib.sha256(data).hexdigest() != artifact.sha256
                ):
                    raise ValueError("downloaded graph-prep artifact failed integrity")
                reference = self.artifact_store.write_bytes(
                    artifact.root_relative_path,
                    data,
                    media_type=artifact.media_type,
                    immutable=True,
                )
                pointers.append(
                    ArtifactPointerV1(
                        uri=reference.uri,
                        sha256=reference.sha256,
                        size_bytes=reference.size_bytes,
                        media_type=reference.media_type,
                    )
                )
        return tuple(pointers)

    @staticmethod
    def _bundle(
        output_root: str,
        mode: UniHamSocMode,
        by_remote: dict[UniHamGraphPrepArtifact, ArtifactPointerV1],
    ) -> UniHamGraphBundle:
        graph_entry = next(
            item
            for item in by_remote
            if item.root_relative_path.endswith(f"/{mode.value}/graph_data.npz")
        )
        manifest_entry = next(
            item
            for item in by_remote
            if item.root_relative_path.endswith(
                f"/{mode.value}/hermes-graph-manifest.json"
            )
        )
        graph_pointer = by_remote[graph_entry]
        manifest_pointer = by_remote[manifest_entry]
        directory = f"{output_root}/{mode.value}"
        return UniHamGraphBundle(
            root_relative_directory=directory,
            graph_data=_artifact_file(graph_pointer),
            manifest=_artifact_file(manifest_pointer),
        )

    def _local_path(self, pointer: ArtifactPointerV1) -> Path:
        if not pointer.uri.startswith("artifact://"):
            raise ValueError("graph-prep requires a project artifact structure")
        path = self.artifact_store.root / pointer.uri.removeprefix("artifact://")
        resolved = path.resolve(strict=True)
        root = self.artifact_store.root.resolve(strict=True)
        if (
            root not in resolved.parents
            or not resolved.is_file()
            or resolved.is_symlink()
        ):
            raise ValueError("graph-prep source structure path is unsafe")
        if hashlib.sha256(resolved.read_bytes()).hexdigest() != pointer.sha256:
            raise ValueError("graph-prep source structure failed integrity")
        return resolved

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
            shell=False,
            timeout=timeout,
            env={"PATH": os.environ.get("PATH", "")},
        )
        if completed.returncode != 0:
            diagnostic = completed.stderr.decode("utf-8", errors="replace").strip()
            diagnostic = " ".join(diagnostic.split())[:2_000]
            raise RuntimeError(
                f"remote graph-prep process failed with code {completed.returncode}"
                + (f": {diagnostic}" if diagnostic else "")
            )
        return completed.stdout


def _artifact_file(pointer: ArtifactPointerV1) -> UniHamArtifactFile:
    return UniHamArtifactFile(
        artifact_uri=pointer.uri,
        root_relative_path=pointer.uri.removeprefix("artifact://"),
        sha256=pointer.sha256,
        size_bytes=pointer.size_bytes or 0,
        media_type=pointer.media_type or "application/octet-stream",
    )


def _validate_remote_absolute(value: str) -> None:
    path = PurePosixPath(value)
    if not path.is_absolute() or any(
        part in {"", ".", ".."} for part in path.parts[1:]
    ):
        raise ValueError("graph-prep remote paths must be normalized absolute paths")
    if re.search(r"[\s'\"`$;&|<>]", value):
        raise ValueError("graph-prep remote path contains unsafe characters")


def _remote_join(root: str, relative: str) -> str:
    return (PurePosixPath(root) / PurePosixPath(relative)).as_posix()
