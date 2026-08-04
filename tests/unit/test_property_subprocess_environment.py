from __future__ import annotations

from material_agent.ml_screening.property_client import _worker_environment


def test_property_worker_environment_uses_only_repository_source_root(monkeypatch) -> None:
    monkeypatch.setenv("PYTHONPATH", "/untrusted/caller/path")
    environment = _worker_environment()

    assert environment["PYTHONPATH"].endswith("/src")
    assert environment["PYTHONPATH"] != "/untrusted/caller/path"
    assert environment["PYTHONNOUSERSITE"] == "1"
