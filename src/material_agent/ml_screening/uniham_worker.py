"""Python 3.9-compatible stdlib worker for Uni-HamGNN inference.

The heavy HamGNN environment executes this file by absolute path.  This worker
does not import HamGNN, NumPy, Torch, or the main project; it validates the
opaque pickle-bearing inputs and invokes the pinned upstream predictor as a
separate no-shell process.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path, PurePosixPath

PROTOCOL = "agent02-uniham-worker-v1"
GRAPH_MANIFEST = "agent02-uniham-graph-manifest-v1"


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--artifact-root", required=True)
    parser.add_argument("--predictor-script", required=True)
    args = parser.parse_args(argv)
    try:
        payload = json.loads(sys.stdin.buffer.read().decode("utf-8"))
        response = execute(
            payload,
            artifact_root=Path(args.artifact_root),
            predictor_script=Path(args.predictor_script),
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
                (
                    "Uni-HamGNN worker failed closed: "
                    f"{type(exc).__name__}: {exc!s}"
                )
            ],
            "runtime_provenance": None,
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


def execute(payload, *, artifact_root, predictor_script):
    if not isinstance(payload, dict) or payload.get("schema_version") != PROTOCOL:
        raise ValueError("invalid Uni-HamGNN worker protocol")
    if set(payload) != {"schema_version", "plan", "limits"}:
        raise ValueError("Uni-HamGNN worker request has unexpected fields")
    plan = payload["plan"]
    request = plan["request"]
    limits = payload["limits"]
    operation_key = plan["operation_key"]
    if operation_key != _uniham_operation_key(request):
        raise ValueError("Uni-HamGNN operation key differs from request")
    expected_sandbox = (
        f"stages/agent02/{request['run_id']}/uniham/"
        f"{operation_key}/worker-output"
    )
    if plan.get("output_sandbox_relative_path") != expected_sandbox:
        raise ValueError("Uni-HamGNN sandbox differs from operation identity")
    device = request.get("device")
    if device not in {"cpu", "cuda"}:
        raise ValueError("Uni-HamGNN worker received an invalid device")
    if request.get("calculate_mae") is not False:
        raise ValueError("Uni-HamGNN worker v1 forbids unlabeled MAE")
    trust = request.get("input_trust", {})
    if trust.get("trusted_executable_inputs") is not True:
        raise ValueError("pickle-bearing Uni-HamGNN inputs are not trusted")

    root = artifact_root.resolve(strict=True)
    if predictor_script.is_symlink():
        raise ValueError("Uni-HamGNN predictor script cannot be a symlink")
    script = predictor_script.resolve(strict=True)
    if not root.is_dir() or not script.is_file():
        raise ValueError("Uni-HamGNN root or predictor script is invalid")
    if _sha256_file(script) != request.get("predictor_script_sha256"):
        raise ValueError("Uni-HamGNN predictor script hash mismatch")

    artifacts = [
        request["input_structure"],
        request["model_pickle"],
        request["non_soc_graph"]["graph_data"],
        request["non_soc_graph"]["manifest"],
        request["soc_graph"]["graph_data"],
        request["soc_graph"]["manifest"],
    ]
    for artifact in artifacts:
        _validate_input(root, artifact)
    _validate_graph_bundle(
        root,
        request["non_soc_graph"],
        plan["non_soc_manifest"],
        expected_mode="non_soc",
        structure_sha=request["input_structure"]["sha256"],
    )
    _validate_graph_bundle(
        root,
        request["soc_graph"],
        plan["soc_manifest"],
        expected_mode="soc",
        structure_sha=request["input_structure"]["sha256"],
    )

    sandbox = _resolve_relative(
        root, plan["output_sandbox_relative_path"], must_exist=False
    )
    if sandbox.exists():
        if sandbox.is_symlink() or not sandbox.is_dir():
            raise ValueError("Uni-HamGNN output sandbox must be a directory")
        if any(sandbox.iterdir()):
            return _recover_completed_sandbox(
                root=root,
                sandbox=sandbox,
                request=request,
                operation_key=operation_key,
                limits=limits,
            )
    else:
        sandbox.mkdir(parents=True)
    output_dir = sandbox / "output"
    output_dir.mkdir()
    temporary_dir = sandbox / "tmp"
    temporary_dir.mkdir()
    config_path = sandbox / "Input.yaml"
    _write_config(config_path, request=request, root=root, output_dir=output_dir)

    runtime_provenance = _runtime_probe(
        device,
        timeout=min(120, int(limits["wall_time_seconds"])),
    )

    environment = {
        "PATH": os.environ.get("PATH", ""),
        "TMPDIR": str(temporary_dir),
        "PYTHONNOUSERSITE": "1",
        "PYTHONDONTWRITEBYTECODE": "1",
        "LANG": os.environ.get("LANG", "C.UTF-8"),
        "OMP_NUM_THREADS": "1",
    }
    if device == "cuda":
        environment["CUDA_VISIBLE_DEVICES"] = os.environ["CUDA_VISIBLE_DEVICES"]
    else:
        environment["CUDA_VISIBLE_DEVICES"] = ""
    completed = subprocess.run(
        [sys.executable, str(script), "--config", str(config_path)],
        cwd=str(sandbox),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        timeout=int(limits["wall_time_seconds"]),
        check=False,
        env=environment,
    )
    if completed.returncode != 0:
        raise RuntimeError(
            "Uni-HamiltonianPredictor exited with code "
            f"{completed.returncode}; diagnostics withheld"
        )
    hamiltonian = output_dir / "hamiltonian.npy"
    if not hamiltonian.is_file() or hamiltonian.is_symlink():
        raise ValueError("Uni-HamGNN did not produce hamiltonian.npy")
    with hamiltonian.open("rb") as handle:
        if handle.read(6) != b"\x93NUMPY":
            raise ValueError("Uni-HamGNN Hamiltonian is not a NumPy artifact")

    _remove_empty_temporary_tree(temporary_dir)
    summary = sandbox / "execution-summary.json"
    summary.write_text(
        json.dumps(
            {
                "schema_version": "agent02-uniham-execution-summary-v1",
                "operation_key": operation_key,
                "model_id": request["model_id"],
                "hamgnn_source_revision": request["hamgnn_source_revision"],
                "device": device,
                "runtime_provenance": runtime_provenance,
                "soc_inference": True,
                "calculate_mae": False,
                "is_mock": bool(request["is_mock"]),
                "benchmark_status": "NOT_RUN",
                "scientific_conclusion": False,
            },
            sort_keys=True,
            separators=(",", ":"),
        ),
        encoding="utf-8",
    )
    expected = {
        config_path.relative_to(root).as_posix(),
        hamiltonian.relative_to(root).as_posix(),
        summary.relative_to(root).as_posix(),
    }
    produced = _capture_outputs(
        root=root,
        sandbox=sandbox,
        expected=expected,
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
            "Uni-HamGNN completed without a Hermes scientific benchmark.",
            "The Hamiltonian is an unvalidated ML artifact, not a topology claim.",
        ],
        "errors": [],
        "runtime_provenance": runtime_provenance,
    }


def _write_config(path, *, request, root, output_dir):
    path.write_text(
        _config_text(request=request, root=root, output_dir=output_dir),
        encoding="utf-8",
    )


def _config_text(*, request, root, output_dir):
    model = _resolve_relative(root, request["model_pickle"]["root_relative_path"])
    non_soc = _resolve_relative(
        root, request["non_soc_graph"]["root_relative_directory"]
    )
    soc = _resolve_relative(root, request["soc_graph"]["root_relative_directory"])
    lines = [
        f"model_pkl_path: {_yaml_string(str(model))}",
        f"non_soc_data_dir: {_yaml_string(str(non_soc))}",
        f"soc_data_dir: {_yaml_string(str(soc))}",
        f"output_dir: {_yaml_string(str(output_dir))}",
        f"device: '{request['device']}'",
        "calculate_mae: false",
    ]
    return "\n".join(lines) + "\n"


def _yaml_string(value):
    return "'" + value.replace("'", "''") + "'"


def _validate_graph_bundle(
    root, bundle, planned_manifest, *, expected_mode, structure_sha
):
    manifest_path = _resolve_relative(
        root, bundle["manifest"]["root_relative_path"]
    )
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    expected_keys = {
        "schema_version",
        "structure_sha256",
        "graph_data_sha256",
        "soc_mode",
        "interface",
        "basis_id",
        "dft_data_version",
        "graph_generator_revision",
        "nao_max",
    }
    if set(manifest) != expected_keys or manifest != planned_manifest:
        raise ValueError("Uni-HamGNN graph manifest differs from frozen plan")
    if manifest["schema_version"] != GRAPH_MANIFEST:
        raise ValueError("invalid Uni-HamGNN graph manifest version")
    if manifest["soc_mode"] != expected_mode:
        raise ValueError("Uni-HamGNN graph manifest SOC mode mismatch")
    if manifest["structure_sha256"] != structure_sha:
        raise ValueError("Uni-HamGNN graph manifest structure mismatch")
    if manifest["graph_data_sha256"] != bundle["graph_data"]["sha256"]:
        raise ValueError("Uni-HamGNN graph manifest data hash mismatch")
    if manifest["interface"] != "openmx" or manifest["nao_max"] != 26:
        raise ValueError("Uni-HamGNN graph manifest is outside v1 compatibility")


def _validate_input(root, artifact):
    relative = _validate_relative(artifact["root_relative_path"])
    if artifact["artifact_uri"] != f"artifact://{relative}":
        raise ValueError("Uni-HamGNN artifact URI/path mismatch")
    path = _resolve_relative(root, relative)
    if not path.is_file() or path.is_symlink():
        raise ValueError("Uni-HamGNN input artifact must be a regular file")
    if path.stat().st_size != int(artifact["size_bytes"]):
        raise ValueError("Uni-HamGNN input size mismatch")
    if _sha256_file(path) != artifact["sha256"]:
        raise ValueError("Uni-HamGNN input hash mismatch")


def _runtime_probe(device, timeout):
    if device == "cpu":
        return {
            "requested_device": "cpu",
            "observed_device": "cpu",
            "cuda_visible_device_count": 0,
            "cuda_device_name": None,
            "torch_version": None,
            "torch_cuda_version": None,
        }
    visible = os.environ.get("CUDA_VISIBLE_DEVICES")
    if not visible or not visible.isdigit():
        raise ValueError(
            "Uni-HamGNN CUDA worker requires one validated visible GPU index"
        )
    probe = (
        "import json,torch;"
        "print(json.dumps({'available':torch.cuda.is_available(),"
        "'count':torch.cuda.device_count(),"
        "'name':torch.cuda.get_device_name(0) if torch.cuda.is_available() else None,"
        "'torch_version':torch.__version__,"
        "'torch_cuda_version':torch.version.cuda},sort_keys=True))"
    )
    environment = {
        "PATH": os.environ.get("PATH", ""),
        "CUDA_VISIBLE_DEVICES": visible,
        "PYTHONNOUSERSITE": "1",
        "PYTHONDONTWRITEBYTECODE": "1",
        "LANG": os.environ.get("LANG", "C.UTF-8"),
    }
    completed = subprocess.run(
        [sys.executable, "-c", probe],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        timeout=timeout,
        check=False,
        env=environment,
    )
    if completed.returncode != 0:
        raise RuntimeError("Uni-HamGNN CUDA runtime probe failed")
    try:
        observed = json.loads(completed.stdout.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise ValueError("Uni-HamGNN CUDA runtime probe is invalid") from exc
    if observed.get("available") is not True or observed.get("count") != 1:
        raise ValueError("Uni-HamGNN CUDA runtime did not expose exactly one GPU")
    return {
        "requested_device": "cuda",
        "observed_device": "cuda",
        "cuda_visible_device_count": 1,
        "cuda_device_name": observed.get("name"),
        "torch_version": observed.get("torch_version"),
        "torch_cuda_version": observed.get("torch_cuda_version"),
    }


def _capture_outputs(*, root, sandbox, expected, max_single, max_total):
    produced = []
    total = 0
    actual = set()
    for path in sorted(sandbox.rglob("*")):
        if path.is_symlink():
            raise ValueError("Uni-HamGNN output cannot contain symlinks")
        if not path.is_file():
            continue
        relative = path.relative_to(root).as_posix()
        actual.add(relative)
        size = path.stat().st_size
        if size <= 0 or size > max_single:
            raise ValueError("Uni-HamGNN output violates artifact size limits")
        total += size
        produced.append(
            {
                "root_relative_path": relative,
                "sha256": _sha256_file(path),
                "size_bytes": size,
                "media_type": _media_type(path),
            }
        )
    if actual != expected:
        raise ValueError("Uni-HamGNN output allowlist mismatch")
    if total > max_total:
        raise ValueError("Uni-HamGNN outputs exceed total size limit")
    return produced


def _recover_completed_sandbox(*, root, sandbox, request, operation_key, limits):
    config_path = sandbox / "Input.yaml"
    output_dir = sandbox / "output"
    hamiltonian = output_dir / "hamiltonian.npy"
    summary_path = sandbox / "execution-summary.json"
    expected = {
        config_path.relative_to(root).as_posix(),
        hamiltonian.relative_to(root).as_posix(),
        summary_path.relative_to(root).as_posix(),
    }
    if not all(path.is_file() and not path.is_symlink() for path in (
        config_path,
        hamiltonian,
        summary_path,
    )):
        raise ValueError("existing Uni-HamGNN sandbox is incomplete")
    if config_path.read_text(encoding="utf-8") != _config_text(
        request=request,
        root=root,
        output_dir=output_dir,
    ):
        raise ValueError("existing Uni-HamGNN config differs from request")
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    if (
        summary.get("operation_key") != operation_key
        or summary.get("model_id") != request["model_id"]
        or summary.get("device") != request["device"]
        or summary.get("is_mock") != bool(request["is_mock"])
        or summary.get("calculate_mae") is not False
        or summary.get("scientific_conclusion") is not False
    ):
        raise ValueError("existing Uni-HamGNN summary differs from request")
    runtime_provenance = summary.get("runtime_provenance")
    if not isinstance(runtime_provenance, dict):
        raise TypeError("existing Uni-HamGNN runtime provenance is invalid")
    with hamiltonian.open("rb") as handle:
        if handle.read(6) != b"\x93NUMPY":
            raise ValueError("existing Uni-HamGNN Hamiltonian is invalid")
    produced = _capture_outputs(
        root=root,
        sandbox=sandbox,
        expected=expected,
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
            "Uni-HamGNN recovered a hash-verified completed operation.",
            "The Hamiltonian is an unvalidated ML artifact, not a topology claim.",
        ],
        "errors": [],
        "runtime_provenance": runtime_provenance,
    }


def _remove_empty_temporary_tree(temporary_dir):
    """Allow runtime-created empty cache directories, never undeclared files."""

    entries = sorted(
        temporary_dir.rglob("*"),
        key=lambda path: len(path.parts),
        reverse=True,
    )
    if any(path.is_symlink() or not path.is_dir() for path in entries):
        raise ValueError("Uni-HamGNN temporary directory contains undeclared files")
    for directory in entries:
        directory.rmdir()
    temporary_dir.rmdir()


def _resolve_relative(root, relative, must_exist=True):
    relative = _validate_relative(relative)
    candidate = root.joinpath(*relative.split("/"))
    current = root
    for part in relative.split("/"):
        current = current / part
        if current.exists() and current.is_symlink():
            raise ValueError("symlinks are forbidden at the Uni-HamGNN boundary")
    resolved = candidate.resolve(strict=must_exist)
    if resolved != root and root not in resolved.parents:
        raise ValueError("Uni-HamGNN path escapes artifact root")
    return resolved


def _validate_relative(value):
    if not isinstance(value, str) or "\\" in value:
        raise ValueError("Uni-HamGNN paths must use POSIX relative form")
    path = PurePosixPath(value)
    if path.is_absolute() or value in {"", "."}:
        raise ValueError("Uni-HamGNN path must be root-relative")
    if any(part in {"", ".", ".."} for part in path.parts):
        raise ValueError("Uni-HamGNN path traversal is forbidden")
    if path.as_posix() != value:
        raise ValueError("Uni-HamGNN path must be canonical")
    return value


def _media_type(path):
    return {
        ".json": "application/json",
        ".yaml": "application/yaml",
        ".npy": "application/x-npy",
    }.get(path.suffix.casefold(), "application/octet-stream")


def _sha256_file(path):
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def _uniham_operation_key(request):
    payload = {
        "schema_version": "agent02-uniham-operation-v1",
        "request": request,
    }
    encoded = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


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
