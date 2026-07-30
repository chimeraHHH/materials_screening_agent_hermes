from __future__ import annotations

import json
from pathlib import Path

import pytest

from material_agent.cli import main
from material_agent.retrieval.models import RetrievalStageInput, StageStatus
from material_agent.retrieval.runner import RetrievalStageRunner
from material_agent.retrieval.storage import LocalArtifactStore


def test_offline_si_o_e2e_is_auditable_and_idempotent(
    tmp_path, requirement, requirement_hash, adapter, policy
) -> None:
    store = LocalArtifactStore(tmp_path)
    requirement_ref = store.write_json(
        "requirements/requirement.v1.json", requirement.model_dump(mode="json")
    )
    stage_input = RetrievalStageInput(
        project_id="project-test",
        run_id="run-test",
        requirement_revision=1,
        requirement_artifact_uri=requirement_ref.uri,
        requirement_hash=requirement_hash,
        retrieval_policy_version=policy.policy_version,
        confirmed_by_user=True,
    )
    runner = RetrievalStageRunner(
        adapter=adapter,
        artifact_store=store,
        policy=policy,
    )

    first = runner.run(requirement, stage_input)
    second = runner.run(requirement, stage_input)

    assert first == second
    assert first.status is StageStatus.SUCCEEDED
    assert first.metrics["database_returned"] == 1
    assert first.metrics["passed"] == 1
    assert first.metrics["published_downstream"] == 1
    assert len(first.candidate_ids) == 1
    assert first.provenance["is_mock"] is True

    manifest_path = (
        tmp_path
        / "stages"
        / "agent01"
        / "run-test"
        / "candidate_manifest.jsonl"
    )
    manifest = [json.loads(line) for line in manifest_path.read_text().splitlines()]
    assert len(manifest) == 1
    candidate = manifest[0]
    assert candidate["source_material_id"] == "mp-fixture-1"
    assert candidate["decision"] == "PASS"
    assert candidate["evidence_level"] == "L1_RETRIEVED"
    assert candidate["structure_artifact_sha256"]
    assert candidate["structure_source_artifact_sha256"]
    assert (tmp_path / candidate["structure_artifact_uri"].removeprefix("artifact://")).is_file()
    assert first.candidate_manifest is not None
    assert first.candidate_manifest.uri == (
        "artifact://stages/agent01/run-test/candidate_manifest.jsonl"
    )

    report_path = tmp_path / "stages" / "agent01" / "run-test" / "retrieval_report.json"
    report = json.loads(report_path.read_text())
    assert report["limits"]["scan_truncated"] is False
    assert "No result is claimed as ML" in report["evidence_statement"]


def test_offline_cli_writes_run_scoped_manifest(
    tmp_path: Path, capsys
) -> None:
    fixture_dir = Path(__file__).parents[1] / "fixtures"
    exit_code = main(
        [
            "retrieval",
            "--requirement",
            str(fixture_dir / "requirement.si-o.json"),
            "--fixture",
            str(fixture_dir / "mp-summary.si-o.json"),
            "--output",
            str(tmp_path),
            "--project-id",
            "project-cli",
            "--run-id",
            "run-cli",
        ]
    )
    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert exit_code == 0
    assert payload["status"] == "SUCCEEDED"
    assert (
        tmp_path
        / "stages"
        / "agent01"
        / "run-cli"
        / "candidate_manifest.jsonl"
    ).is_file()


@pytest.mark.parametrize(
    "source",
    [
        "nomad",
        "mc3d",
        "c2db",
        "topological_quantum_chemistry",
        "nims_supercon",
    ],
)
def test_offline_cli_selects_one_non_mp_source(
    tmp_path: Path, capsys, source: str
) -> None:
    fixture_dir = Path(__file__).parents[1] / "fixtures"
    exit_code = main(
        [
            "retrieval",
            "--requirement",
            str(fixture_dir / "requirement.si-o.json"),
            "--fixture",
            str(fixture_dir / "mp-summary.si-o.json"),
            "--output",
            str(tmp_path),
            "--project-id",
            f"project-cli-{source}",
            "--run-id",
            f"run-cli-{source}",
            "--source",
            source,
        ]
    )
    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert exit_code == 0
    assert payload["schema_version"] == "agent01-contract-v2"
    query_plan = json.loads(
        (
            tmp_path
            / "stages"
            / "agent01"
            / f"run-cli-{source}"
            / "query_plan.json"
        ).read_text(encoding="utf-8")
    )
    assert query_plan["source_database"] == source
    manifest = json.loads(
        (
            tmp_path
            / "stages"
            / "agent01"
            / f"run-cli-{source}"
            / "candidate_manifest.jsonl"
        ).read_text(encoding="utf-8")
    )
    assert manifest["source_database"] == source
