"""Stdlib-only, fail-closed remote worker for one Quantum ESPRESSO SCF task."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path, PurePosixPath

PROTOCOL = "hermes-qe-remote-scf-v1"
JOB_PATTERN = re.compile(r"^qe-[0-9a-f]{24}$")
SHA_PATTERN = re.compile(r"^[0-9a-f]{64}$")


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-root", required=True)
    parser.add_argument("--stack-root", required=True)
    parser.add_argument("action", choices=("prepare", "execute"))
    parser.add_argument("--job-id")
    args = parser.parse_args(argv)
    try:
        root = Path(args.run_root).resolve(strict=True)
        stack = Path(args.stack_root).resolve(strict=True)
        if args.action == "prepare":
            request = json.loads(sys.stdin.buffer.read().decode("utf-8"))
            response = prepare(root=root, stack=stack, request=request)
        else:
            response = execute(root=root, stack=stack, job_id=args.job_id)
    except Exception as exc:  # noqa: BLE001
        print(
            f"QE remote worker failed closed: {type(exc).__name__}: {exc}",
            file=sys.stderr,
        )
        return 2
    sys.stdout.write(
        json.dumps(response, sort_keys=True, separators=(",", ":"))
    )
    return 0


def prepare(*, root, stack, request):
    _validate_request(request)
    _validate_stack(stack, request)
    job_id = request["job_id"]
    job = _job_path(root, job_id)
    request_bytes = _canonical_json_bytes(request)
    request_sha = hashlib.sha256(request_bytes).hexdigest()
    request_path = job / "request.json"
    if job.exists():
        if not request_path.is_file() or _sha256_file(request_path) != request_sha:
            raise ValueError("existing QE job differs from the frozen request")
        return {
            "schema_version": PROTOCOL,
            "job_id": job_id,
            "request_sha256": request_sha,
            "status": "ALREADY_PREPARED",
        }
    job.mkdir(mode=0o750)
    for item in request["inputs"]:
        destination = _job_relative(job, item["remote_relative_path"])
        destination.parent.mkdir(parents=True, exist_ok=True)
    (job / "tmp").mkdir()
    _atomic_write(request_path, request_bytes)
    return {
        "schema_version": PROTOCOL,
        "job_id": job_id,
        "request_sha256": request_sha,
        "status": "PREPARED",
    }


def execute(*, root, stack, job_id):
    if not isinstance(job_id, str) or not JOB_PATTERN.fullmatch(job_id):
        raise ValueError("invalid QE remote job ID")
    job = _job_path(root, job_id)
    request_path = job / "request.json"
    request = json.loads(request_path.read_text(encoding="utf-8"))
    _validate_request(request)
    if request["job_id"] != job_id:
        raise ValueError("QE job ID differs from request")
    _validate_stack(stack, request)
    request_sha = _sha256_file(request_path)
    completion_path = job / "completion.json"
    if completion_path.exists():
        completion = json.loads(completion_path.read_text(encoding="utf-8"))
        if completion.get("request_sha256") != request_sha:
            raise ValueError("QE completion request hash mismatch")
        _verify_completion(job, completion)
        return completion
    for item in request["inputs"]:
        path = _job_relative(job, item["remote_relative_path"])
        if not path.is_file() or path.is_symlink():
            raise ValueError("QE input is missing or is a symlink")
        if path.stat().st_size != item["size_bytes"]:
            raise ValueError("QE input size mismatch")
        if _sha256_file(path) != item["sha256"]:
            raise ValueError("QE input hash mismatch")

    executable = stack / "qe-7.6-install/bin/pw.x"
    if _sha256_file(executable) != request["pw_binary_sha256"]:
        raise ValueError("QE pw.x hash differs from the frozen request")
    input_path = _job_relative(job, request["scf_input_relative_path"])
    mpi_processes = request["mpi_processes"]
    command = [str(executable), "-in", str(input_path)]
    if mpi_processes > 1:
        command = ["/usr/bin/mpirun", "-np", str(mpi_processes), *command]
    environment = {
        "PATH": "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
        "LANG": "C.UTF-8",
        "OMP_NUM_THREADS": str(request["omp_threads"]),
        "TMPDIR": str(job / "tmp"),
        "DISPLAY": "",
    }
    started = time.monotonic()
    completed = subprocess.run(
        command,
        cwd=str(job),
        stdin=subprocess.DEVNULL,
        capture_output=True,
        check=False,
        timeout=request["wall_time_seconds"],
        env=environment,
    )
    wall_time = time.monotonic() - started
    stdout_path = job / "pw.out"
    stderr_path = job / "pw.stderr"
    _atomic_write(stdout_path, completed.stdout)
    _atomic_write(stderr_path, completed.stderr)
    job_done = b"JOB DONE." in completed.stdout
    status = "SUCCEEDED" if completed.returncode == 0 and job_done else "FAILED"
    completion = {
        "schema_version": PROTOCOL,
        "job_id": job_id,
        "request_sha256": request_sha,
        "status": status,
        "return_code": completed.returncode,
        "job_done": job_done,
        "wall_time_seconds": wall_time,
        "pw_binary_sha256": request["pw_binary_sha256"],
        "output_artifacts": [
            _artifact(job, stdout_path, "text/plain"),
            _artifact(job, stderr_path, "text/plain"),
        ],
        "scientific_conclusion": False,
    }
    _atomic_write(completion_path, _canonical_json_bytes(completion))
    return completion


def _validate_request(request):
    required = {
        "schema_version",
        "job_id",
        "pw_binary_sha256",
        "scf_input_relative_path",
        "inputs",
        "mpi_processes",
        "omp_threads",
        "wall_time_seconds",
        "is_mock",
    }
    if not isinstance(request, dict) or set(request) != required:
        raise ValueError("QE request fields differ from the frozen protocol")
    if request["schema_version"] != PROTOCOL or request["is_mock"] is not False:
        raise ValueError("QE remote worker accepts real v1 requests only")
    if not JOB_PATTERN.fullmatch(request["job_id"]):
        raise ValueError("invalid QE request job ID")
    if not SHA_PATTERN.fullmatch(request["pw_binary_sha256"]):
        raise ValueError("invalid QE binary SHA-256")
    if not 1 <= request["mpi_processes"] <= 32:
        raise ValueError("QE MPI process count is outside [1, 32]")
    if not 1 <= request["omp_threads"] <= 32:
        raise ValueError("QE OpenMP thread count is outside [1, 32]")
    if not 1 <= request["wall_time_seconds"] <= 86400:
        raise ValueError("QE wall-time limit is invalid")
    if not isinstance(request["inputs"], list) or not request["inputs"]:
        raise ValueError("QE request requires input artifacts")
    paths = []
    for item in request["inputs"]:
        if not isinstance(item, dict) or set(item) != {
            "remote_relative_path",
            "sha256",
            "size_bytes",
        }:
            raise ValueError("QE input manifest fields are invalid")
        _validate_relative(item["remote_relative_path"])
        if not SHA_PATTERN.fullmatch(item["sha256"]):
            raise ValueError("QE input SHA-256 is invalid")
        if not isinstance(item["size_bytes"], int) or item["size_bytes"] <= 0:
            raise ValueError("QE input size is invalid")
        paths.append(item["remote_relative_path"])
    if len(paths) != len(set(paths)):
        raise ValueError("QE input paths must be unique")
    _validate_relative(request["scf_input_relative_path"])
    if request["scf_input_relative_path"] not in paths:
        raise ValueError("QE SCF input is absent from the input manifest")
    if not any(path.startswith("pseudo/") for path in paths):
        raise ValueError("QE request requires pseudopotential artifacts")


def _validate_stack(stack, request):
    executable = (stack / "qe-7.6-install/bin/pw.x").resolve(strict=True)
    if stack not in executable.parents or not executable.is_file():
        raise ValueError("QE stack executable is invalid")
    if not os.access(executable, os.X_OK):
        raise ValueError("QE pw.x is not executable")
    if request["mpi_processes"] > 1 and not Path("/usr/bin/mpirun").is_file():
        raise ValueError("QE MPI request requires /usr/bin/mpirun")


def _job_path(root, job_id):
    job = (root / job_id).resolve()
    if root not in job.parents:
        raise ValueError("QE job path escaped run root")
    return job


def _job_relative(job, value):
    _validate_relative(value)
    path = (job / value).resolve()
    if job not in path.parents:
        raise ValueError("QE relative path escaped job root")
    return path


def _validate_relative(value):
    if not isinstance(value, str) or "\\" in value:
        raise ValueError("QE path must use POSIX separators")
    path = PurePosixPath(value)
    if path.is_absolute() or value in {"", "."}:
        raise ValueError("QE path must be root-relative")
    if any(part in {"", ".", ".."} for part in path.parts):
        raise ValueError("QE path contains traversal or dot segments")
    if value != path.as_posix():
        raise ValueError("QE path is not canonical")


def _artifact(job, path, media_type):
    return {
        "remote_relative_path": path.relative_to(job).as_posix(),
        "sha256": _sha256_file(path),
        "size_bytes": path.stat().st_size,
        "media_type": media_type,
    }


def _verify_completion(job, completion):
    if completion.get("schema_version") != PROTOCOL:
        raise ValueError("QE completion protocol mismatch")
    for item in completion.get("output_artifacts", []):
        path = _job_relative(job, item["remote_relative_path"])
        if (
            not path.is_file()
            or path.stat().st_size != item["size_bytes"]
            or _sha256_file(path) != item["sha256"]
        ):
            raise ValueError("QE completion output Artifact failed verification")


def _atomic_write(path, payload):
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass


def _canonical_json_bytes(value):
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")


def _sha256_file(path):
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


if __name__ == "__main__":
    raise SystemExit(main())
