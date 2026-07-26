from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from material_agent.orchestrator.models import RunStatus
from material_agent.orchestrator.runtime import OrchestratorRuntime
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


@pytest.mark.live_mp
def test_orchestrator_real_mp_restart_release_gate(
    tmp_path: Path, requirement
) -> None:
    api_key = os.environ.get("MP_API_KEY")
    assert api_key, "MP_API_KEY must be set for --run-live-mp"

    project_id = "project-orchestrator-live-release"
    run_id = "run-orchestrator-live-si-o"
    OrchestratorRuntime.create_project(tmp_path, project_id)
    with OrchestratorRuntime.from_workspace(
        tmp_path, project_id
    ) as runtime:
        waiting = runtime.start_run(
            raw_request="structured Requirement input",
            initial_requirement=requirement.model_dump(mode="json"),
            run_id=run_id,
        )
        assert waiting.status is RunStatus.REQUIREMENT_REVIEW
        approval_id = waiting.interrupts[0].value["approval_id"]

    with OrchestratorRuntime.from_workspace(
        tmp_path, project_id
    ) as runtime:
        completed = runtime.approve(
            run_id=run_id,
            approval_id=approval_id,
            decision="approve",
        )
        assert completed.status in {RunStatus.SUCCEEDED, RunStatus.PARTIAL}
        assert completed.stage_statuses["agent01"] in {
            "SUCCEEDED",
            "PARTIAL",
        }
        assert completed.report_uri
        report = runtime.store.read_json(
            f"artifact://reports/{run_id}/report.json"
        )
        native = runtime.store.read_json(
            f"artifact://stages/agent01/{run_id}/stage_result.json"
        )
        assert report["stages"]["agent01"]["native_result_sha256"]
        assert native["schema_version"] == AGENT01_CONTRACT_VERSION
        operation_count = runtime.repository.connection.execute(
            "SELECT COUNT(*) FROM operations WHERE run_id = ?", (run_id,)
        ).fetchone()[0]
        checkpoint_count = runtime.repository.connection.execute(
            "SELECT COUNT(*) FROM checkpoints WHERE thread_id = ?", (run_id,)
        ).fetchone()[0]
        resumed = runtime.resume(run_id=run_id)
        assert resumed == completed
        assert runtime.repository.connection.execute(
            "SELECT COUNT(*) FROM operations WHERE run_id = ?", (run_id,)
        ).fetchone()[0] == operation_count == 1
        assert checkpoint_count > 1

    secret = api_key.encode("utf-8")
    for path in tmp_path.rglob("*"):
        if path.is_file():
            assert secret not in path.read_bytes(), f"secret leaked into {path}"
