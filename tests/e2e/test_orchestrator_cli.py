from __future__ import annotations

import json
from pathlib import Path

from material_agent.cli import main


def test_orchestrator_cli_project_run_approve_and_report(
    tmp_path: Path, capsys
) -> None:
    fixture_dir = Path(__file__).parents[1] / "fixtures"
    assert (
        main(
            [
                "project",
                "create",
                "--workspace",
                str(tmp_path),
                "--project-id",
                "project-cli-p0",
            ]
        )
        == 0
    )
    capsys.readouterr()
    assert (
        main(
            [
                "run",
                "--workspace",
                str(tmp_path),
                "--project",
                "project-cli-p0",
                "--run-id",
                "run-cli-p0",
                "--requirement-file",
                str(fixture_dir / "requirement.si-o.json"),
                "--fixture",
                str(fixture_dir / "mp-summary.si-o.json"),
            ]
        )
        == 0
    )
    waiting = json.loads(capsys.readouterr().out)
    approval_id = waiting["interrupts"][0]["value"]["approval_id"]

    assert (
        main(
            [
                "approve",
                "--workspace",
                str(tmp_path),
                "--project",
                "project-cli-p0",
                "--run",
                "run-cli-p0",
                "--approval",
                approval_id,
                "--decision",
                "approve",
            ]
        )
        == 0
    )
    completed = json.loads(capsys.readouterr().out)
    assert completed["status"] == "SUCCEEDED"
    assert completed["stage_statuses"] == {"agent01": "SUCCEEDED"}

    assert (
        main(
            [
                "report",
                "--workspace",
                str(tmp_path),
                "--project",
                "project-cli-p0",
                "--run",
                "run-cli-p0",
            ]
        )
        == 0
    )
    assert "# Material Screening Run Report" in capsys.readouterr().out


def test_run_stage_cli_requires_explicit_input_and_reports_blocked(
    tmp_path: Path, capsys
) -> None:
    fixture_dir = Path(__file__).parents[1] / "fixtures"
    project_id = "project-cli-stage"
    source_run = "run-cli-source"
    assert (
        main(
            [
                "project",
                "create",
                "--workspace",
                str(tmp_path),
                "--project-id",
                project_id,
            ]
        )
        == 0
    )
    capsys.readouterr()
    assert (
        main(
            [
                "run",
                "--workspace",
                str(tmp_path),
                "--project",
                project_id,
                "--run-id",
                source_run,
                "--requirement-file",
                str(fixture_dir / "requirement.si-o.json"),
                "--fixture",
                str(fixture_dir / "mp-summary.si-o.json"),
            ]
        )
        == 0
    )
    approval_id = json.loads(capsys.readouterr().out)["interrupts"][0][
        "value"
    ]["approval_id"]
    assert (
        main(
            [
                "approve",
                "--workspace",
                str(tmp_path),
                "--project",
                project_id,
                "--run",
                source_run,
                "--approval",
                approval_id,
                "--decision",
                "approve",
            ]
        )
        == 0
    )
    capsys.readouterr()

    project_root = tmp_path / project_id
    requirement_path = (
        project_root
        / "requirements"
        / source_run
        / "requirement.v1.json"
    )
    import hashlib

    stage_input_path = tmp_path / "ml-stage-input.json"
    stage_input_path.write_text(
        json.dumps(
            {
                "source_run_id": source_run,
                "requirement_revision": 1,
                "requirement_artifact_uri": (
                    f"artifact://requirements/{source_run}/"
                    "requirement.v1.json"
                ),
                "requirement_artifact_sha256": hashlib.sha256(
                    requirement_path.read_bytes()
                ).hexdigest(),
                "artifacts": {},
            }
        ),
        encoding="utf-8",
    )
    assert (
        main(
            [
                "run-stage",
                "ml",
                "--workspace",
                str(tmp_path),
                "--project",
                project_id,
                "--input",
                str(stage_input_path),
                "--run-id",
                "run-cli-ml",
            ]
        )
        == 0
    )
    blocked = json.loads(capsys.readouterr().out)
    assert blocked["status"] == "PAUSED"
    assert blocked["stage_statuses"] == {
        "agent02": "BLOCKED_MISSING_INPUT"
    }
