from __future__ import annotations

import math

import pytest
from pydantic import ValidationError

from material_agent.dft.models import (
    ArtifactRef, ClaimResult, ClaimStatus, DFTTaskSpec, ExecutionMode,
    ResourceEstimate, TaskType,
)


def test_artifact_refs_are_logical_and_hashes_are_strict() -> None:
    ref = ArtifactRef(uri="artifact://structures/s.cif", sha256="a" * 64)
    assert ref.sha256 == "a" * 64
    for uri in ("/tmp/s.cif", "../s.cif", "C:\\s.cif", "not-a-uri"):
        with pytest.raises(ValidationError):
            ArtifactRef(uri=uri, sha256="a" * 64)
    with pytest.raises(ValidationError):
        ArtifactRef(uri="artifact://structures/s.cif", sha256="not-a-hash")


def test_mock_task_and_claim_cannot_claim_real_science() -> None:
    with pytest.raises(ValidationError):
        DFTTaskSpec(
            task_id="task", task_type=TaskType.MOCK_TASK, candidate_id="c",
            input_structure=ArtifactRef(uri="artifact://s", sha256="a" * 64),
            resource_spec=ResourceEstimate(resource_class="TRIVIAL"),
            task_input_hash="b" * 64, is_mock=False,
        )
    with pytest.raises(ValidationError):
        ClaimResult(
            claim_id="claim", candidate_id="c", claim_type="band_gap_computed",
            status=ClaimStatus.VALIDATED, evidence_level="L3_DFT_VALIDATED", is_mock=True,
        )


def test_nan_is_rejected_by_strict_contract() -> None:
    with pytest.raises(ValidationError):
        ResourceEstimate(resource_class="SMALL", scientific_cost_values=math.nan)
