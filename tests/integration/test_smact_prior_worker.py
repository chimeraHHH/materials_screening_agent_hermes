from __future__ import annotations

import os
from pathlib import Path

import pytest

from material_agent.softchem import (
    DEFAULT_SMACT_PRIOR_POLICY_V1,
    SMACT_WORKER_LOCK_SHA256,
    SMACT_WORKER_PYTHON_ENV,
    SmactPriorDecision,
    SmactPriorSubprocessClient,
)


@pytest.mark.real_smact
def test_real_smact_worker_pass_reject_and_provenance() -> None:
    worker_value = os.environ.get(SMACT_WORKER_PYTHON_ENV, "").strip()
    if not worker_value:
        pytest.skip(f"requires {SMACT_WORKER_PYTHON_ENV}")
    repository_root = Path(__file__).resolve().parents[2]
    client = SmactPriorSubprocessClient(
        python_executable=Path(worker_value),
        source_root=repository_root / "src",
        base_lock_path=repository_root / "requirements.lock",
        package_lock_path=repository_root / "requirements-smact.lock",
    )

    passing = client.evaluate(
        "TiSe2",
        input_formula="TiS2",
        policy=DEFAULT_SMACT_PRIOR_POLICY_V1,
    )
    rejected = client.evaluate(
        "NaCl2",
        input_formula="NaCl",
        policy=DEFAULT_SMACT_PRIOR_POLICY_V1,
    )

    assert passing.decision is SmactPriorDecision.PASS
    assert passing.worker_lock_sha256 == SMACT_WORKER_LOCK_SHA256
    assert rejected.decision is SmactPriorDecision.REJECT
    assert rejected.worker_lock_sha256 == SMACT_WORKER_LOCK_SHA256
