"""Independent SMACT composition-prior JSON worker."""

from __future__ import annotations

import argparse
import hashlib
import sys
from pathlib import Path

from material_agent.softchem.prior import (
    SmactPriorWorkerRequestV1,
    evaluate_smact_composition_prior,
)
from material_agent.softchem.prior_client import (
    SMACT_WORKER_BASE_LOCK_SHA256,
    SMACT_WORKER_EXTRA_LOCK_SHA256,
    SMACT_WORKER_LOCK_SHA256,
    _combined_lock_sha256,
)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-lock", type=Path, required=True)
    parser.add_argument("--package-lock", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        if args.base_lock.is_symlink() or args.package_lock.is_symlink():
            raise ValueError("SMACT package locks cannot be symlinks")
        base_lock_path = args.base_lock.resolve(strict=True)
        lock_path = args.package_lock.resolve(strict=True)
        if (
            not base_lock_path.is_file()
            or not lock_path.is_file()
        ):
            raise ValueError("SMACT package locks are invalid")
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
            raise ValueError("SMACT package locks differ from reviewed release")
        request = SmactPriorWorkerRequestV1.model_validate_json(sys.stdin.buffer.read())
        if request.package_lock_sha256 != lock_sha256:
            raise ValueError("SMACT request package lock identity mismatch")
        result = evaluate_smact_composition_prior(
            request.composition_formula,
            input_formula=request.input_formula,
            policy=request.policy,
        )
        if result.worker_lock_sha256 is not None:
            raise ValueError("local SMACT result cannot claim worker provenance")
    except Exception as exc:
        print(
            f"SMACT prior worker failed: {type(exc).__name__}",
            file=sys.stderr,
        )
        return 2
    sys.stdout.write(result.model_dump_json())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
