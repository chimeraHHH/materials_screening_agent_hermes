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
    state = _mps_state(worker_python)
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


@pytest.mark.mps_ml
def test_mps_relaxation_matches_cpu_within_release_tolerance() -> None:
    worker_python = Path(
        os.environ.get(
            "MATERIAL_AGENT_ML_WORKER_PYTHON",
            REPOSITORY_ROOT / ".venv-agent02/bin/python",
        )
    )
    assert worker_python.is_file(), "dedicated Agent02 Python is missing"
    state = _mps_state(worker_python)
    if not state["available"]:
        pytest.skip(
            f"target Mac MPS runtime unavailable (built={state['built']})"
        )
    cpu = _relaxation_summary(worker_python, "cpu")
    mps = _relaxation_summary(worker_python, "mps")
    assert mps["static_energy_ev_atom"] == pytest.approx(
        cpu["static_energy_ev_atom"], abs=2e-4
    )
    assert mps["final_energy_ev"] == pytest.approx(
        cpu["final_energy_ev"], abs=2e-3
    )
    assert mps["final_volume_angstrom3"] == pytest.approx(
        cpu["final_volume_angstrom3"], rel=2e-3
    )
    assert cpu["final_max_force_ev_angstrom"] <= 0.1
    assert mps["final_max_force_ev_angstrom"] <= 0.1


def _mps_state(worker_python: Path) -> dict[str, bool]:
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
    return json.loads(probe.stdout)


def _relaxation_summary(worker_python: Path, device: str) -> dict[str, float]:
    script = """
import json
import sys
import numpy as np
from pymatgen.core import Structure
from material_agent.ml_screening.chgnet_worker import _run_chgnet

structure = Structure.from_file(sys.argv[2])
prediction, relaxation = _run_chgnet(structure, sys.argv[1])
trajectory = relaxation['trajectory']
forces = np.asarray(trajectory.forces[-1], dtype=float)
print(json.dumps({
    'static_energy_ev_atom': float(np.asarray(prediction['e'])),
    'final_energy_ev': float(np.asarray(trajectory.energies[-1])),
    'final_volume_angstrom3': float(relaxation['final_structure'].volume),
    'final_max_force_ev_angstrom': float(np.linalg.norm(forces, axis=1).max()),
}))
"""
    process = subprocess.run(
        [str(worker_python), "-c", script, device, str(STRUCTURE_FIXTURE)],
        check=True,
        capture_output=True,
        text=True,
        env=_environment(),
    )
    return json.loads(process.stdout)


def _environment() -> dict[str, str]:
    return {
        "PATH": os.environ.get("PATH", ""),
        "PYTHONPATH": str(SOURCE_ROOT),
        "PYTHONNOUSERSITE": "1",
        "MPLCONFIGDIR": "/tmp/material-agent-mpl",
        "LANG": os.environ.get("LANG", "C.UTF-8"),
    }
