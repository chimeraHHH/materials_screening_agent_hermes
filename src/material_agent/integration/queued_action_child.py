"""Fixed subprocess boundary for one supervised Gateway action.

The parent worker owns the queue lease and Gateway commit.  This child receives
only a bounded canonical action envelope, runs ``companion.act`` under a
run-scoped POSIX lock and process-local alarm, and writes one bounded canonical
transition.  It never claims, checkpoints, or commits a queue job.
"""

from __future__ import annotations

import argparse
import json
import os
import signal
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from material_agent.gateway.errors import AdapterContractError
from material_agent.gateway.models import (
    INTERACTION_ADAPTER,
    RUN_ACTION_ADAPTER,
    InspirationRunRequestV1,
    InteractionRequiredStateV1,
    canonical_json_bytes,
    inspiration_request_sha256,
    inspiration_run_id,
)
from material_agent.gateway.service import (
    GatewayActionWorkerError,
    _RunScopedProcessLock,
)
from material_agent.integration.hermes_service import (
    GatewayServerSettings,
    create_hermes_fixture_service,
    create_hermes_inspiration_service,
)

_MAX_PROTOCOL_BYTES = 1_000_000
_INPUT_SCHEMA = "materials-gateway-supervised-action-input-v1"
_OUTPUT_SCHEMA = "materials-gateway-supervised-action-output-v1"


class SupervisedActionChildError(RuntimeError):
    """The fixed child protocol or local execution boundary is invalid."""


def _read_input(path: Path) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file():
        raise SupervisedActionChildError("supervised action input is unsafe")
    if path.stat().st_size > _MAX_PROTOCOL_BYTES:
        raise SupervisedActionChildError("supervised action input exceeds its limit")
    try:
        value = json.loads(path.read_bytes())
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SupervisedActionChildError("supervised action input is invalid") from exc
    expected = {
        "action",
        "execution_manifest_sha256",
        "interaction",
        "request",
        "request_sha256",
        "run_id",
        "schema_version",
        "timeout_ms",
    }
    if not isinstance(value, dict) or set(value) != expected:
        raise SupervisedActionChildError("supervised action input fields differ")
    if value["schema_version"] != _INPUT_SCHEMA:
        raise SupervisedActionChildError("supervised action input version differs")
    timeout_ms = value["timeout_ms"]
    if not isinstance(timeout_ms, int) or not 1 <= timeout_ms <= 3_600_000:
        raise SupervisedActionChildError("supervised action timeout is invalid")
    return value


def _write_output(path: Path, value: dict[str, Any]) -> None:
    if path.parent.is_symlink() or path.is_symlink():
        raise SupervisedActionChildError("supervised action output path is unsafe")
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(path.parent, 0o700)
    payload = canonical_json_bytes(value)
    if len(payload) > _MAX_PROTOCOL_BYTES:
        raise SupervisedActionChildError("supervised action output exceeds its limit")
    descriptor = os.open(
        path,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
        0o600,
    )
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
    except Exception:
        try:
            path.unlink()
        except OSError:
            pass
        raise
    directory = os.open(path.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(directory)
    finally:
        os.close(directory)


def _service(settings: GatewayServerSettings, mode: str):
    if mode == "fixture":
        return create_hermes_fixture_service(settings)
    if mode == "public":
        return create_hermes_inspiration_service(settings)
    raise SupervisedActionChildError("supervised action service mode is invalid")


def _execute(args: argparse.Namespace) -> dict[str, Any]:
    value = _read_input(args.input)
    try:
        args.input.unlink()
        directory = os.open(
            args.input.parent,
            os.O_RDONLY | getattr(os, "O_DIRECTORY", 0),
        )
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    except OSError as exc:
        raise SupervisedActionChildError(
            "supervised action input could not be retired"
        ) from exc
    try:
        request = InspirationRunRequestV1.model_validate_json(
            canonical_json_bytes(value["request"]), strict=True
        )
        interaction = INTERACTION_ADAPTER.validate_json(
            canonical_json_bytes(value["interaction"]), strict=True
        )
        action = RUN_ACTION_ADAPTER.validate_json(
            canonical_json_bytes(value["action"]), strict=True
        )
    except (TypeError, ValueError) as exc:
        raise SupervisedActionChildError(
            "supervised action contracts are invalid"
        ) from exc
    if (
        inspiration_run_id(request.submission_id) != value["run_id"]
        or inspiration_request_sha256(request) != value["request_sha256"]
        or action.interaction_id != interaction.interaction_id
    ):
        raise SupervisedActionChildError("supervised action identity differs")
    manifest = value["execution_manifest_sha256"]
    observed_manifest = getattr(interaction, "execution_manifest_sha256", None)
    if manifest != observed_manifest:
        raise SupervisedActionChildError(
            "supervised action execution manifest differs"
        )

    service = _service(
        GatewayServerSettings(args.workspace, args.project),
        args.service_mode,
    )
    lock_root = service.action_authorizer.database_path.parent
    signal.setitimer(signal.ITIMER_REAL, value["timeout_ms"] / 1_000)
    try:
        with _RunScopedProcessLock(lock_root, value["run_id"]):
            raw = service.companion.act(
                run_id=value["run_id"],
                request=request,
                state=InteractionRequiredStateV1(interaction=interaction),
                action=action,
            )
            transition = service._validate_transition(
                raw,
                run_id=value["run_id"],
                request=request,
                previous_interaction_id=interaction.interaction_id,
            )
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
    return {
        "schema_version": _OUTPUT_SCHEMA,
        "status": "TRANSITION",
        "transition": transition.model_dump(mode="json"),
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Execute one supervised Gateway action")
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--project", required=True)
    parser.add_argument("--service-mode", choices=("fixture", "public"), required=True)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        output = _execute(args)
    except GatewayActionWorkerError as exc:
        code = "RUN_LOCK_BUSY" if "another live process" in str(exc) else "WORKER_BOUNDARY_ERROR"
        output = {
            "schema_version": _OUTPUT_SCHEMA,
            "status": "ERROR",
            "error_code": code,
        }
    except AdapterContractError:
        output = {
            "schema_version": _OUTPUT_SCHEMA,
            "status": "ERROR",
            "error_code": "ADAPTER_CONTRACT_ERROR",
        }
    except SupervisedActionChildError:
        output = {
            "schema_version": _OUTPUT_SCHEMA,
            "status": "ERROR",
            "error_code": "CHILD_PROTOCOL_ERROR",
        }
    except Exception:  # noqa: BLE001
        output = {
            "schema_version": _OUTPUT_SCHEMA,
            "status": "ERROR",
            "error_code": "ADAPTER_EXECUTION_ERROR",
        }
    try:
        _write_output(args.output, output)
    except (OSError, SupervisedActionChildError):
        return 2
    return 0 if output["status"] == "TRANSITION" else 1


if __name__ == "__main__":
    raise SystemExit(main())
