from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from material_agent.retrieval.adapters import MaterialsProjectAdapter
from material_agent.retrieval.models import (
    AGENT01_CONTRACT_VERSION,
    CandidateAuditRecord,
    RetrievalPolicy,
    RetrievalStageInput,
    StageStatus,
)
from material_agent.retrieval.runner import RetrievalStageRunner
from material_agent.retrieval.storage import LocalArtifactStore


@pytest.mark.live_mp
def test_fixed_si_o_release_gate(tmp_path: Path, requirement) -> None:
    api_key = os.environ.get("MP_API_KEY")
    assert api_key, "MP_API_KEY must be set for --run-live-mp"

    store = LocalArtifactStore(tmp_path)
    requirement_ref = store.write_json(
        "requirements/requirement.v1.json",
        requirement.model_dump(mode="json"),
        immutable=True,
    )
    policy = RetrievalPolicy(retry_base_seconds=1)
    stage_input = RetrievalStageInput(
        project_id="project-live-release",
        run_id="run-live-si-o-release",
        requirement_revision=requirement.revision,
        requirement_artifact_uri=requirement_ref.uri,
        requirement_hash=requirement_ref.sha256,
        retrieval_policy_version=policy.policy_version,
        confirmed_by_user=True,
    )
    runner = RetrievalStageRunner(
        adapter=MaterialsProjectAdapter(),
        artifact_store=store,
        policy=policy,
    )

    first = runner.run(requirement, stage_input)
    second = runner.run(requirement, stage_input)

    assert first == second
    assert first.status is StageStatus.SUCCEEDED
    assert first.schema_version == AGENT01_CONTRACT_VERSION
    assert not first.errors
    assert first.metrics["scan_truncated"] is False
    assert first.candidate_manifest is not None

    manifest = [
        json.loads(line)
        for line in store.read_bytes(first.candidate_manifest.uri)
        .decode("utf-8")
        .splitlines()
        if line.strip()
    ]
    assert manifest
    for candidate_payload in manifest:
        candidate_model = CandidateAuditRecord.model_validate(candidate_payload)
        assert candidate_model.schema_version == AGENT01_CONTRACT_VERSION
        candidate = candidate_model.model_dump(mode="json")
        properties = {
            prop["name"]: prop for prop in candidate["properties"]
        }
        assert {"Si", "O"}.issubset(candidate["elements"])
        assert 0.5 <= properties["band_gap"]["value"] <= 1.0
        assert properties["energy_above_hull"]["value"] <= 0.05
        assert properties["is_metal"]["value"] is False
        assert candidate["evidence_level"] == "L1_RETRIEVED"
        assert candidate["structure_artifact_sha256"]
        assert store.exists_with_hash(
            candidate["structure_artifact_uri"],
            candidate["structure_artifact_sha256"],
        )
        for prop in properties.values():
            assert prop["unit"]
            assert prop["origin"]["database_version"]
            assert prop["origin"]["status"]

    secret = api_key.encode("utf-8")
    for path in tmp_path.rglob("*"):
        if path.is_file():
            assert secret not in path.read_bytes(), f"secret leaked into {path}"
