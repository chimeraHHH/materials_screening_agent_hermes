from __future__ import annotations

import importlib.util
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
HERMES_ROOT = REPOSITORY_ROOT / "integrations" / "hermes"
VERIFIER_PATH = HERMES_ROOT / "scripts" / "verify_evolution_bundle.py"


def _load_verifier():
    spec = importlib.util.spec_from_file_location("evolution_bundle_verifier", VERIFIER_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_evolution_bundle_is_valid() -> None:
    environment = dict(os.environ)
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    completed = subprocess.run(
        [sys.executable, str(VERIFIER_PATH)],
        cwd=REPOSITORY_ROOT,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    assert completed.stdout == "Hermes evolution bundle valid\n"


def test_evolution_bundle_fails_closed_if_run_tool_is_added(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    copied = tmp_path / "hermes"
    shutil.copytree(HERMES_ROOT, copied, ignore=shutil.ignore_patterns("__pycache__"))
    profile = copied / "profiles" / "materials-inspiration-evolution"
    config = profile / "config.yaml"
    text = config.read_text("utf-8")
    config.write_text(
        text.replace(
            "        - materials_run_get\n",
            "        - materials_inspiration_run\n        - materials_run_get\n",
        ),
        "utf-8",
    )

    verifier = _load_verifier()
    monkeypatch.setattr(verifier, "HERMES_ROOT", copied)
    monkeypatch.setattr(verifier, "PROFILE_ROOT", profile)
    monkeypatch.setattr(
        verifier,
        "SKILL_PATH",
        profile / "skills" / "materials-inspiration-evolution" / "SKILL.md",
    )
    monkeypatch.setattr(verifier, "SOUL_PATH", profile / "SOUL.md")
    monkeypatch.setattr(
        verifier,
        "PROMOTION_GATE_PATH",
        profile
        / "skills"
        / "materials-inspiration-evolution"
        / "references"
        / "promotion-gate.md",
    )
    with pytest.raises(ValueError, match="gained a scientific mutation tool"):
        verifier.verify()


def test_evolution_bundle_requires_skill_write_approval(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    copied = tmp_path / "hermes"
    shutil.copytree(HERMES_ROOT, copied, ignore=shutil.ignore_patterns("__pycache__"))
    profile = copied / "profiles" / "materials-inspiration-evolution"
    config = profile / "config.yaml"
    text = config.read_text("utf-8")
    config.write_text(
        text.replace(
            "skills:\n  creation_nudge_interval: 5\n  write_approval: true",
            "skills:\n  creation_nudge_interval: 5\n  write_approval: false",
        ),
        "utf-8",
    )

    verifier = _load_verifier()
    monkeypatch.setattr(verifier, "HERMES_ROOT", copied)
    monkeypatch.setattr(verifier, "PROFILE_ROOT", profile)
    monkeypatch.setattr(
        verifier,
        "SKILL_PATH",
        profile / "skills" / "materials-inspiration-evolution" / "SKILL.md",
    )
    monkeypatch.setattr(verifier, "SOUL_PATH", profile / "SOUL.md")
    monkeypatch.setattr(
        verifier,
        "PROMOTION_GATE_PATH",
        profile
        / "skills"
        / "materials-inspiration-evolution"
        / "references"
        / "promotion-gate.md",
    )
    with pytest.raises(ValueError, match="approval-gated"):
        verifier.verify()
