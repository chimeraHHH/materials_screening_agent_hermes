from __future__ import annotations

import pytest

from material_agent.ml_screening.runtime import (
    is_mps_runtime_failure,
    run_with_mps_fallback,
)


def test_mps_runtime_failure_falls_back_once_to_cpu() -> None:
    calls: list[str] = []
    cache_clears: list[bool] = []

    def run_once(device: str) -> str:
        calls.append(device)
        if device == "mps":
            raise RuntimeError("MPS backend out of memory")
        return "cpu-result"

    result = run_with_mps_fallback(
        requested_device="mps",
        run_once=run_once,
        clear_mps_cache=lambda: cache_clears.append(True),
    )
    assert result.value == "cpu-result"
    assert result.device == "cpu"
    assert result.fallback_warning is not None
    assert calls == ["mps", "cpu"]
    assert cache_clears == [True]


def test_cpu_and_unrelated_errors_never_fallback() -> None:
    with pytest.raises(ValueError, match="bad structure"):
        run_with_mps_fallback(
            requested_device="mps",
            run_once=lambda _device: (_ for _ in ()).throw(
                ValueError("bad structure")
            ),
            clear_mps_cache=lambda: None,
        )
    with pytest.raises(RuntimeError, match="out of memory"):
        run_with_mps_fallback(
            requested_device="cpu",
            run_once=lambda _device: (_ for _ in ()).throw(
                RuntimeError("out of memory")
            ),
            clear_mps_cache=lambda: None,
        )
    with pytest.raises(RuntimeError, match="CUDA out of memory"):
        run_with_mps_fallback(
            requested_device="cuda",
            run_once=lambda _device: (_ for _ in ()).throw(
                RuntimeError("CUDA out of memory")
            ),
            clear_mps_cache=lambda: (_ for _ in ()).throw(
                AssertionError("CUDA must not use the MPS fallback")
            ),
        )


@pytest.mark.parametrize(
    "message",
    ["MPS backend unavailable", "Metal command-buffer failed", "MTL device lost"],
)
def test_mps_runtime_classifier(message: str) -> None:
    assert is_mps_runtime_failure(RuntimeError(message))
