"""No-shell client for the independent SMACT prior worker."""

from __future__ import annotations

import hashlib
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path

from pymatgen.core import Composition

from material_agent.softchem.prior import (
    SmactPriorGateResultV1,
    SmactPriorPolicyV1,
    SmactPriorWorkerRequestV1,
    smact_prior_policy_sha256,
)


SMACT_WORKER_BASE_LOCK_SHA256 = (
    "bdb5e87aab01d446aa6889dd6f753145cddc8f386df0282d4e80b61abbd3a03c"
)
SMACT_WORKER_EXTRA_LOCK_SHA256 = (
    "c120e909afdce57d78df31895fdd782b0288546f1a4fa6f3d6a8c7e68193be11"
)
SMACT_WORKER_LOCK_SHA256 = (
    "0c118c7ec3f842b027aa0297c9f57f077c381088578bd9ffc0184b4aedd7a83f"
)
SMACT_WORKER_PYTHON_ENV = "MATERIAL_AGENT_SMACT_WORKER_PYTHON"


class SmactPriorProcessError(RuntimeError):
    def __init__(self, category: str, message: str) -> None:
        super().__init__(message)
        self.category = category


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _combined_lock_sha256(base_sha256: str, extra_sha256: str) -> str:
    return hashlib.sha256(f"{base_sha256}:{extra_sha256}".encode("ascii")).hexdigest()


@dataclass(frozen=True, slots=True)
class SmactPriorSubprocessClient:
    python_executable: Path
    source_root: Path
    base_lock_path: Path
    package_lock_path: Path
    module: str = "material_agent.softchem.smact_worker"
    timeout_seconds: int = 30
    max_stdout_bytes: int = 262_144

    def evaluate(
        self,
        composition: Composition | str,
        *,
        input_formula: str | None,
        policy: SmactPriorPolicyV1,
    ) -> SmactPriorGateResultV1:
        executable = self.python_executable
        if not executable.is_absolute():
            raise SmactPriorProcessError(
                "SMACT_WORKER_CONFIGURATION_ERROR",
                "configured SMACT worker Python must be absolute",
            )
        if (
            not executable.is_file()
            or executable.is_symlink()
            or not os.access(executable, os.X_OK)
        ):
            raise SmactPriorProcessError(
                "SMACT_WORKER_CONFIGURATION_ERROR",
                "configured SMACT worker Python is not executable",
            )
        if self.source_root.is_symlink():
            raise SmactPriorProcessError(
                "SMACT_WORKER_CONFIGURATION_ERROR",
                "configured SMACT worker source root is invalid",
            )
        source_root = self.source_root.resolve(strict=True)
        if not source_root.is_dir():
            raise SmactPriorProcessError(
                "SMACT_WORKER_CONFIGURATION_ERROR",
                "configured SMACT worker source root is invalid",
            )
        if self.base_lock_path.is_symlink() or self.package_lock_path.is_symlink():
            raise SmactPriorProcessError(
                "SMACT_WORKER_CONFIGURATION_ERROR",
                "configured SMACT worker locks are invalid",
            )
        base_lock_path = self.base_lock_path.resolve(strict=True)
        lock_path = self.package_lock_path.resolve(strict=True)
        if not base_lock_path.is_file() or not lock_path.is_file():
            raise SmactPriorProcessError(
                "SMACT_WORKER_CONFIGURATION_ERROR",
                "configured SMACT worker locks are invalid",
            )
        base_lock_sha256 = _sha256_file(base_lock_path)
        extra_lock_sha256 = _sha256_file(lock_path)
        lock_sha256 = _combined_lock_sha256(
            base_lock_sha256,
            extra_lock_sha256,
        )
        if (
            base_lock_sha256 != SMACT_WORKER_BASE_LOCK_SHA256
            or extra_lock_sha256 != SMACT_WORKER_EXTRA_LOCK_SHA256
            or lock_sha256 != SMACT_WORKER_LOCK_SHA256
        ):
            raise SmactPriorProcessError(
                "SMACT_WORKER_LOCK_MISMATCH",
                "SMACT worker lock differs from the reviewed release",
            )

        parsed = (
            composition
            if isinstance(composition, Composition)
            else Composition(composition)
        )
        formula = parsed.reduced_formula
        request = SmactPriorWorkerRequestV1(
            composition_formula=formula,
            input_formula=input_formula or formula,
            policy=policy,
            package_lock_sha256=lock_sha256,
        )
        command = [
            str(executable),
            "-m",
            self.module,
            "--package-lock",
            str(lock_path),
            "--base-lock",
            str(base_lock_path),
        ]
        environment = {
            "PATH": os.environ.get("PATH", ""),
            "TMPDIR": os.environ.get("TMPDIR", "/tmp"),
            "MPLCONFIGDIR": os.environ.get(
                "MPLCONFIGDIR", "/tmp/material-agent-smact-mpl"
            ),
            "PYTHONNOUSERSITE": "1",
            "PYTHONPATH": str(source_root),
            "LANG": os.environ.get("LANG", "C.UTF-8"),
        }
        try:
            process = subprocess.run(
                command,
                input=request.model_dump_json().encode("utf-8"),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=self.timeout_seconds,
                check=False,
                shell=False,
                env=environment,
            )
        except subprocess.TimeoutExpired as exc:
            raise SmactPriorProcessError(
                "SMACT_WORKER_TIMEOUT",
                "SMACT worker exceeded its frozen wall-time limit",
            ) from exc
        except OSError as exc:
            raise SmactPriorProcessError(
                "SMACT_WORKER_START_FAILED",
                f"SMACT worker could not start: {type(exc).__name__}",
            ) from exc
        if len(process.stdout) > self.max_stdout_bytes:
            raise SmactPriorProcessError(
                "SMACT_WORKER_STDOUT_LIMIT",
                "SMACT worker stdout exceeded its configured limit",
            )
        if process.returncode != 0:
            raise SmactPriorProcessError(
                "SMACT_WORKER_PROCESS_FAILED",
                f"SMACT worker exited with code {process.returncode}; stderr withheld",
            )
        try:
            result = SmactPriorGateResultV1.model_validate_json(process.stdout)
        except Exception as exc:
            raise SmactPriorProcessError(
                "SMACT_WORKER_INVALID_RESPONSE",
                "SMACT worker returned an invalid response",
            ) from exc
        if (
            result.policy_sha256 != smact_prior_policy_sha256(policy)
            or result.input_formula != request.input_formula
            or result.proposed_formula != formula
            or result.backend_version != policy.smact_version
            or result.worker_lock_sha256 is not None
        ):
            raise SmactPriorProcessError(
                "SMACT_WORKER_IDENTITY_MISMATCH",
                "SMACT worker response differs from the frozen request",
            )
        payload = result.model_dump(mode="python")
        payload["worker_lock_sha256"] = lock_sha256
        return SmactPriorGateResultV1.model_validate(payload)


def smact_prior_evaluator_from_environment() -> SmactPriorSubprocessClient | None:
    value = os.environ.get(SMACT_WORKER_PYTHON_ENV, "").strip()
    if not value:
        return None
    python_path = Path(value)
    if not python_path.is_absolute():
        raise SmactPriorProcessError(
            "SMACT_WORKER_CONFIGURATION_ERROR",
            f"{SMACT_WORKER_PYTHON_ENV} must be an absolute path",
        )
    repository_root = Path(__file__).resolve().parents[3]
    return SmactPriorSubprocessClient(
        python_executable=python_path,
        source_root=repository_root / "src",
        base_lock_path=repository_root / "requirements.lock",
        package_lock_path=repository_root / "requirements-smact.lock",
    )
