from __future__ import annotations

import json
from pathlib import Path

from material_agent.cli import main


ACCEPTANCE_REQUEST = (
    "从 Materials Project 中寻找同时包含 Si 和 O、带隙为 0.5–1.0 eV、"
    "energy above hull 不超过 0.05 eV/atom 的非金属材料。"
)


def test_requirement_parse_cli_writes_immutable_unconfirmed_draft(
    tmp_path: Path, capsys
) -> None:
    output = tmp_path / "requirements" / "draft.json"

    assert (
        main(
            [
                "requirement",
                "parse",
                "--request",
                ACCEPTANCE_REQUEST,
                "--output",
                str(output),
                "--requirement-id",
                "req-cli-stage0",
            ]
        )
        == 0
    )
    result = json.loads(capsys.readouterr().out)
    payload = json.loads(output.read_text(encoding="utf-8"))

    assert result["status"] == "REVIEW_REQUIRED"
    assert result["clarification_questions"] == []
    assert payload["requirement_id"] == "req-cli-stage0"
    assert payload["confirmed_by_user"] is False
    assert payload["hard_constraints"]["include_elements"] == ["O", "Si"]

    assert (
        main(
            [
                "requirement",
                "parse",
                "--request",
                ACCEPTANCE_REQUEST,
                "--output",
                str(output),
                "--requirement-id",
                "req-cli-stage0",
            ]
        )
        == 0
    )
    capsys.readouterr()

    assert (
        main(
            [
                "requirement",
                "parse",
                "--request",
                "帮我寻找一些有趣的材料",
                "--output",
                str(output),
            ]
        )
        == 1
    )
    assert "refusing to overwrite" in capsys.readouterr().err

    fixture_dir = Path(__file__).parents[1] / "fixtures"
    assert (
        main(
            [
                "project",
                "create",
                "--workspace",
                str(tmp_path),
                "--project-id",
                "project-cli-stage0",
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
                "project-cli-stage0",
                "--run-id",
                "run-cli-stage0",
                "--requirement-file",
                str(output),
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
                "project-cli-stage0",
                "--run",
                "run-cli-stage0",
                "--approval",
                approval_id,
                "--decision",
                "approve",
            ]
        )
        == 0
    )
    capsys.readouterr()
    frozen = json.loads(
        (
            tmp_path
            / "project-cli-stage0"
            / "requirements"
            / "run-cli-stage0"
            / "requirement.v1.json"
        ).read_text(encoding="utf-8")
    )
    assert frozen["confirmed_by_user"] is True
    assert frozen["hard_constraints"] == payload["hard_constraints"]


def test_orchestrator_cli_repeats_natural_language_clarification(
    tmp_path: Path, capsys
) -> None:
    fixture_dir = Path(__file__).parents[1] / "fixtures"
    project_id = "project-cli-clarify"
    run_id = "run-cli-clarify"
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
                run_id,
                "--request",
                "帮我找一些材料",
                "--fixture",
                str(fixture_dir / "mp-summary.si-o.json"),
            ]
        )
        == 0
    )
    clarifying = json.loads(capsys.readouterr().out)
    interaction_id = clarifying["interrupts"][0]["interaction_id"]

    assert (
        main(
            [
                "respond",
                "--workspace",
                str(tmp_path),
                "--project",
                project_id,
                "--run",
                run_id,
                "--interaction",
                interaction_id,
                "--text",
                "要求同时包含 Si 和 O。",
            ]
        )
        == 0
    )
    second_round = json.loads(capsys.readouterr().out)
    assert second_round["status"] == "CLARIFYING"
    second_interrupt = second_round["interrupts"][0]
    assert second_interrupt["interaction_id"] != interaction_id
    assert second_interrupt["value"]["payload"]["round"] == 2
    assert (
        second_interrupt["value"]["payload"]["requirement_draft"][
            "hard_constraints"
        ]["include_elements"]
        == ["O", "Si"]
    )

    assert (
        main(
            [
                "respond",
                "--workspace",
                str(tmp_path),
                "--project",
                project_id,
                "--run",
                run_id,
                "--interaction",
                second_interrupt["interaction_id"],
                "--text",
                (
                    "要求非金属；带隙为 0.5 到 1.0 eV，energy above hull "
                    "不超过 0.05 eV/atom。"
                ),
            ]
        )
        == 0
    )
    reviewing = json.loads(capsys.readouterr().out)
    assert reviewing["status"] == "REQUIREMENT_REVIEW"
    requirement = reviewing["interrupts"][0]["value"]["payload"]["requirement"]
    assert requirement["hard_constraints"]["include_elements"] == ["O", "Si"]


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

    assert (
        main(
            [
                "research-advice",
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
    advice = json.loads(capsys.readouterr().out)
    assert advice["mode"] == "offline"
    assert advice["scientific_conclusion"] is False
    assert all(action["execution_allowed"] is False for action in advice["snapshot"]["actions"])


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
