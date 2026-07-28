from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

from material_agent.ml_screening.models import ModelHealthSnapshot


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
SOURCE_ROOT = REPOSITORY_ROOT / "src"
STRUCTURE_FIXTURE = REPOSITORY_ROOT / "tests/fixtures/real_ml/si-diamond.cif"


@pytest.mark.mps_ml
def test_target_mac_mps_health_probe() -> None:
    worker_python = Path(
        os.environ.get(
            "MATERIAL_AGENT_ML_WORKER_PYTHON",
            REPOSITORY_ROOT / ".venv-agent02/bin/python",
        )
    )
    assert worker_python.is_file(), "dedicated Agent02 Python is missing"
    probe = subprocess.run(
        [
            str(worker_python),
            "-c",
            (
                "import json, torch; "
                "print(json.dumps({'built': torch.backends.mps.is_built(), "
                "'available': torch.backends.mps.is_available()}))"
            ),
        ],
        check=True,
        capture_output=True,
        text=True,
        env=_environment(),
    )
    state = json.loads(probe.stdout)
    if not state["available"]:
        pytest.skip(
            f"target Mac MPS runtime unavailable (built={state['built']})"
        )
    health_process = subprocess.run(
        [
            str(worker_python),
            "-m",
            "material_agent.ml_screening.chgnet_worker",
            "--artifact-root",
            str(REPOSITORY_ROOT),
            "--package-lock",
            str(REPOSITORY_ROOT / "requirements-agent02.lock"),
            "--health-structure",
            str(STRUCTURE_FIXTURE),
            "--device",
            "mps",
        ],
        check=True,
        capture_output=True,
        env=_environment(),
    )
    health = ModelHealthSnapshot.model_validate_json(health_process.stdout)
    assert health.device_policy == "mps"
    assert "mps" in health.available_devices
    assert health.smoke_test_status.value == "PASS"


def _environment() -> dict[str, str]:
    return {
        "PATH": os.environ.get("PATH", ""),
        "PYTHONPATH": str(SOURCE_ROOT),
        "PYTHONNOUSERSITE": "1",
        "MPLCONFIGDIR": "/tmp/material-agent-mpl",
        "LANG": os.environ.get("LANG", "C.UTF-8"),
    }
