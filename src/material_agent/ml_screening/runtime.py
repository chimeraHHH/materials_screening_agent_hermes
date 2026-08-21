"""Small, dependency-free runtime device-fallback policy for Agent02."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class DeviceExecution:
    value: Any
    device: str
    fallback_warning: str | None = None


def run_with_mps_fallback(
    *,
    requested_device: str,
    run_once: Callable[[str], Any],
    clear_mps_cache: Callable[[], None],
) -> DeviceExecution:
    """Run once on the requested device, with one MPS-only CPU fallback.

    Input validation, model/checkpoint identity and output validation stay
    outside this helper.  A CPU failure is never retried and an MPS failure
    with an unrelated error message is not silently reclassified.
    """

    try:
        return DeviceExecution(
            value=run_once(requested_device), device=requested_device
        )
    except Exception as exc:
        if requested_device != "mps" or not is_mps_runtime_failure(exc):
            raise
        clear_mps_cache()
        return DeviceExecution(
            value=run_once("cpu"),
            device="cpu",
            fallback_warning=(
                "MPS runtime failed; one explicit CPU fallback completed: "
                f"{type(exc).__name__}"
            ),
        )


def is_mps_runtime_failure(exc: BaseException) -> bool:
    message = f"{type(exc).__name__}: {exc}".casefold()
    return any(
        marker in message
        for marker in (
            "mps",
            "metal",
            "mtl",
            "gpu",
            "out of memory",
        )
    )
