from __future__ import annotations

from pathlib import Path

from material_agent.orchestrator.models import StageId
from material_agent.orchestrator.runners import (
    StageRunnerRegistry,
    configure_agent02_production,
)

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]


def test_agent02_factory_stays_unregistered_without_explicit_worker(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.delenv("MATERIAL_AGENT_ML_WORKER_PYTHON", raising=False)
    registry = StageRunnerRegistry()
    configure_agent02_production(registry, project_root=tmp_path)
    capability = registry.capability(StageId.ML)
    assert not capability.registered
    assert not registry.has_runner(StageId.ML)
    assert "MATERIAL_AGENT_ML_WORKER_PYTHON" in (capability.unavailable_reason or "")


def test_agent02_factory_rejects_invalid_worker_configuration(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.setenv(
        "MATERIAL_AGENT_ML_WORKER_PYTHON", "/does/not/exist/python"
    )
    registry = StageRunnerRegistry()
    configure_agent02_production(registry, project_root=tmp_path)
    capability = registry.capability(StageId.ML)
    assert not capability.registered
    assert "configuration invalid" in (capability.unavailable_reason or "")


def test_agent02_factory_registers_only_with_a_valid_executable(
    monkeypatch, tmp_path: Path
) -> None:
    executable = tmp_path / "agent02-python"
    executable.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    executable.chmod(0o700)
    monkeypatch.setenv("MATERIAL_AGENT_ML_WORKER_PYTHON", str(executable))
    registry = StageRunnerRegistry()
    configure_agent02_production(registry, project_root=tmp_path)
    capability = registry.capability(StageId.ML)
    assert capability.registered
    assert not capability.is_mock
    assert not capability.supports_external
    assert registry.has_runner(StageId.ML)
