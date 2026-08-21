"""Python 3.9-compatible stdlib worker for DeepH-pack inference.

This file is executed by absolute path in the dedicated DeepH environment.  It
must remain independent from the ``material_agent`` package because the
upstream DeepH environment may use Python 3.9 while the main project targets
Python 3.11.
"""

from __future__ import annotations

import argparse
import configparser
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path, PurePosixPath

PROTOCOL = "agent02-deeph-worker-v1"


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--artifact-root", required=True)
    parser.add_argument("--deeph-executable", required=True)
    args = parser.parse_args(argv)

    try:
        payload = json.loads(sys.stdin.buffer.read().decode("utf-8"))
        response = execute(
            payload,
            artifact_root=Path(args.artifact_root),
            deeph_executable=Path(args.deeph_executable),
        )
    except Exception as exc:  # noqa: BLE001
        response = {
            "schema_version": PROTOCOL,
            "operation_key": _operation_key_from_payload(locals().get("payload")),
            "status": "FAILED",
            "produced_artifacts": [],
            "is_mock": _mock_from_payload(locals().get("payload")),
            "warnings": [],
            "errors": [
                f"DeepH worker failed closed: {type(exc).__name__}: {exc!s}"
            ],
        }
    sys.stdout.write(
        json.dumps(
            response,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        )
    )
    return 0


def execute(payload, *, artifact_root, deeph_executable):
    if not isinstance(payload, dict) or payload.get("schema_version") != PROTOCOL:
        raise ValueError("invalid DeepH worker protocol")
    if set(payload) != {"schema_version", "plan", "limits"}:
        raise ValueError("DeepH worker request contains unexpected fields")
    plan = payload["plan"]
    request = plan["request"]
    operation_key = plan["operation_key"]
    limits = payload["limits"]
    if request.get("tasks") != [1, 2, 3, 4]:
        raise ValueError("DeepH worker accepts only frozen tasks [1,2,3,4]")
    if request.get("device") != "cpu":
        raise ValueError("DeepH worker v1 is CPU-only")

    root = artifact_root.resolve(strict=True)
    executable = deeph_executable.resolve(strict=True)
    if not root.is_dir():
        raise ValueError("artifact root must be a directory")
    if not executable.is_file() or not os.access(str(executable), os.X_OK):
        raise ValueError("deeph-inference executable is not executable")

    artifacts = [
        request["input_structure"],
        *request["trained_model"]["files"],
        *request["overlap"]["files"],
    ]
    for artifact in artifacts:
        _validate_input(root, artifact)

    sandbox = _resolve_relative(
        root,
        plan["output_sandbox_relative_path"],
        must_exist=False,
    )
    if sandbox.exists():
        if sandbox.is_symlink() or not sandbox.is_dir():
            raise ValueError("DeepH output sandbox is invalid")
        if any(sandbox.iterdir()):
            raise ValueError("DeepH output sandbox must be empty before execution")
    else:
        sandbox.mkdir(parents=True)
    work_dir = sandbox / "work"
    work_dir.mkdir()
    sparse_config = sandbox / "sparse-calculation.json"
    sparse_config.write_text(
        json.dumps(
            {
                "calc_job": "band",
                "fermi_level": 0.0,
                "k_data": [],
                "which_k": -1,
                "num_band": 1,
                "max_iter": 1,
            },
            sort_keys=True,
            separators=(",", ":"),
        ),
        encoding="utf-8",
    )
    inference_config = sandbox / "deeph-inference.ini"
    _write_inference_config(
        inference_config,
        request=request,
        root=root,
        work_dir=work_dir,
        sparse_config=sparse_config,
    )

    environment = {
        "PATH": os.environ.get("PATH", ""),
        "TMPDIR": os.environ.get("TMPDIR", "/tmp"),
        "PYTHONNOUSERSITE": "1",
        "LANG": os.environ.get("LANG", "C.UTF-8"),
    }
    completed = subprocess.run(
        [str(executable), "--config", str(inference_config)],
        cwd=str(work_dir),
        stdin=subprocess.DEVNULL,
        capture_output=True,
        timeout=int(limits["wall_time_seconds"]),
        check=False,
        env=environment,
    )
    if completed.returncode != 0:
        raise RuntimeError(
            f"deeph-inference exited with code {completed.returncode}; diagnostics withheld"
        )

    metadata_path = sandbox / "execution-summary.json"
    metadata_path.write_text(
        json.dumps(
            {
                "schema_version": "agent02-deeph-execution-summary-v1",
                "operation_key": operation_key,
                "tasks": [1, 2, 3, 4],
                "interface": request["model_compatibility"]["interface"],
                "model_id": request["model_id"],
                "is_mock": bool(request["is_mock"]),
                "benchmark_status": "NOT_RUN",
                "scientific_conclusion": False,
            },
            sort_keys=True,
            separators=(",", ":"),
        ),
        encoding="utf-8",
    )
    produced = _capture_outputs(
        root=root,
        sandbox=sandbox,
        max_single=int(limits["max_single_artifact_bytes"]),
        max_total=int(limits["max_total_output_bytes"]),
    )
    return {
        "schema_version": PROTOCOL,
        "operation_key": operation_key,
        "status": "SUCCEEDED",
        "produced_artifacts": produced,
        "is_mock": bool(request["is_mock"]),
        "warnings": [
            "DeepH flow completed without a scientific benchmark.",
            "Produced files are not DFT validation or a scientific conclusion.",
        ],
        "errors": [],
    }


def _write_inference_config(
    path, *, request, root, work_dir, sparse_config
):
    parser = configparser.ConfigParser()
    trained_model_dir = _resolve_relative(
        root,
        request["trained_model"]["root_relative_directory"],
    )
    overlap_dir = _resolve_relative(
        root,
        request["overlap"]["root_relative_directory"],
    )
    if not trained_model_dir.is_dir() or not overlap_dir.is_dir():
        raise ValueError("DeepH model and overlap roots must be directories")
    parser["basic"] = {
        "work_dir": str(work_dir),
        "OLP_dir": str(overlap_dir),
        "interface": request["model_compatibility"]["interface"],
        "trained_model_dir": str(trained_model_dir),
        "task": "[1, 2, 3, 4]",
        "sparse_calc_config": str(sparse_config),
        "eigen_solver": "dense_py",
        "disable_cuda": "True",
        "device": "cpu",
        "huge_structure": "True",
        "restore_blocks_py": "True",
        "gen_rc_idx": "False",
        "gen_rc_by_idx": "",
        "with_grad": "False",
    }
    parser["interpreter"] = {
        "julia_interpreter": "",
        "python_interpreter": sys.executable,
    }
    parser["graph"] = {
        "radius": "-1.0",
        "create_from_DFT": "True",
    }
    with path.open("w", encoding="utf-8") as handle:
        parser.write(handle)


def _validate_input(root, artifact):
    relative = _validate_relative(artifact["root_relative_path"])
    if artifact["artifact_uri"] != f"artifact://{relative}":
        raise ValueError("DeepH artifact URI/path mismatch")
    path = _resolve_relative(root, relative)
    if not path.is_file():
        raise ValueError("DeepH input artifact must be a file")
    if path.stat().st_size != int(artifact["size_bytes"]):
        raise ValueError("DeepH input size mismatch")
    if _sha256_file(path) != artifact["sha256"]:
        raise ValueError("DeepH input hash mismatch")


def _capture_outputs(*, root, sandbox, max_single, max_total):
    produced = []
    total = 0
    for path in sorted(sandbox.rglob("*")):
        if path.is_symlink():
            raise ValueError("DeepH output cannot contain symlinks")
        if not path.is_file():
            continue
        size = path.stat().st_size
        if size <= 0:
            raise ValueError("DeepH output files cannot be empty")
        if size > max_single:
            raise ValueError("DeepH output exceeds single-artifact limit")
        total += size
        relative = path.relative_to(root).as_posix()
        produced.append(
            {
                "root_relative_path": relative,
                "sha256": _sha256_file(path),
                "size_bytes": size,
                "media_type": _media_type(path),
            }
        )
    if total > max_total:
        raise ValueError("DeepH outputs exceed total limit")
    return produced


def _resolve_relative(root, relative, must_exist=True):
    relative = _validate_relative(relative)
    candidate = root.joinpath(*relative.split("/"))
    current = root
    for part in relative.split("/"):
        current = current / part
        if current.exists() and current.is_symlink():
            raise ValueError("symlinks are forbidden at the DeepH boundary")
    resolved = candidate.resolve(strict=must_exist)
    if resolved != root and root not in resolved.parents:
        raise ValueError("DeepH path escapes artifact root")
    return resolved


def _validate_relative(value):
    if not isinstance(value, str) or "\\" in value:
        raise ValueError("DeepH paths must use POSIX relative form")
    path = PurePosixPath(value)
    if path.is_absolute() or value in {"", "."}:
        raise ValueError("DeepH path must be root-relative")
    if any(part in {"", ".", ".."} for part in path.parts):
        raise ValueError("DeepH path traversal is forbidden")
    if path.as_posix() != value:
        raise ValueError("DeepH path must be canonical")
    return value


def _media_type(path):
    suffix = path.suffix.casefold()
    return {
        ".json": "application/json",
        ".ini": "text/plain",
        ".h5": "application/x-hdf5",
        ".hdf5": "application/x-hdf5",
        ".txt": "text/plain",
    }.get(suffix, "application/octet-stream")


def _sha256_file(path):
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def _operation_key_from_payload(payload):
    try:
        value = payload["plan"]["operation_key"]
        if isinstance(value, str) and len(value) == 64:
            return value
    except Exception:  # noqa: BLE001, S110
        pass
    return "0" * 64


def _mock_from_payload(payload):
    try:
        return bool(payload["plan"]["request"]["is_mock"])
    except Exception:  # noqa: BLE001
        return True


if __name__ == "__main__":
    raise SystemExit(main())
