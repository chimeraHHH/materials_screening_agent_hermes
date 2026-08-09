from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from material_agent.gateway.mcp_server import (
    GatewayDispatchError,
    GatewayServerSettings,
    GatewayToolDispatcher,
    gateway_tool_manifest,
)
from material_agent.gateway.models import (
    ApproveActionV1,
    InspirationBudgetV1,
    InspirationConstraintsV1,
    InteractionRequiredStateV1,
)
from material_agent.gateway.persistence import SqliteGatewayRepository
from material_agent.integration.hermes_service import (
    HERMES_FIXTURE_CONSTRAINTS,
    HERMES_FIXTURE_GOAL,
    create_hermes_fixture_service,
    create_hermes_inspiration_service,
)
from material_agent.integration.operator_approval import main as operator_main


def _public_arguments(submission_id: str) -> dict[str, object]:
    constraints = InspirationConstraintsV1(
        required_elements=("Se", "Ti"),
        excluded_elements=("Pb",),
        material_classes=("layered transition-metal dichalcogenide",),
        dimensionality="2D",
        target_features=("narrow electronic band",),
        top_k=1,
        require_diverse_routes=True,
        budget=InspirationBudgetV1(
            max_search_requests=8,
            max_unique_documents=4,
            max_passages=4,
            max_model_calls=0,
            max_walltime_seconds=300,
        ),
    )
    return {
        "submission_id": submission_id,
        "goal": "Review the bounded public metadata inspiration request.",
        "constraints": constraints.model_dump(mode="json"),
    }


def test_operator_grant_is_out_of_band_exact_and_one_time(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    project = "hermes-operator-approval"
    settings = GatewayServerSettings(workspace=tmp_path, project_id=project)
    service = create_hermes_fixture_service(settings)
    dispatcher = GatewayToolDispatcher(service)
    started = dispatcher.dispatch(
        "materials_inspiration_run",
        {
            "submission_id": "operator-approval-submission",
            "goal": HERMES_FIXTURE_GOAL,
            "constraints": HERMES_FIXTURE_CONSTRAINTS.model_dump(mode="json"),
        },
    )
    action = {
        "kind": "approve",
        "interaction_id": started["state"]["interaction"]["interaction_id"],
        "confirmed_by_user": True,
    }

    with pytest.raises(GatewayDispatchError, match="operator grant"):
        dispatcher.dispatch(
            "materials_run_act",
            {"run_id": started["run_id"], "action": action},
        )
    assert dispatcher.dispatch(
        "materials_run_get", {"run_id": started["run_id"]}
    ) == started
    assert not (
        tmp_path / project / "stages" / "inspiration" / started["run_id"]
    ).exists()

    assert operator_main(
        [
            "--workspace",
            str(tmp_path),
            "--project",
            project,
            "--run-id",
            started["run_id"],
            "--confirmation-reference",
            "ticket:human-confirmation-001",
            "--service-mode",
            "fixture",
        ]
    ) == 0
    assert "confirmation_reference" in capsys.readouterr().out

    terminal = dispatcher.dispatch(
        "materials_run_act",
        {"run_id": started["run_id"], "action": action},
    )
    assert terminal["state"]["status"] in {"SUCCEEDED", "PARTIAL"}
    with pytest.raises(GatewayDispatchError):
        dispatcher.dispatch(
            "materials_run_act",
            {"run_id": started["run_id"], "action": action},
        )
    with pytest.raises(SystemExit) as replay:
        operator_main(
            [
                "--workspace",
                str(tmp_path),
                "--project",
                project,
                "--run-id",
                started["run_id"],
                "--confirmation-reference",
                "ticket:human-confirmation-002",
                "--service-mode",
                "fixture",
            ]
        )
    assert replay.value.code == 2

    state_root = tmp_path / project / ".gateway"
    gateway_database = state_root / "materials-gateway.sqlite3"
    approval_database = state_root / "operator-approval-grants.sqlite3"
    assert gateway_database.is_file() and not gateway_database.is_symlink()
    assert approval_database.is_file() and not approval_database.is_symlink()
    assert gateway_database != approval_database
    assert tuple(item["name"] for item in gateway_tool_manifest()) == (
        "materials_inspiration_run",
        "materials_run_get",
        "materials_run_act",
        "materials_result_get",
    )
    assert isinstance(service.repository, SqliteGatewayRepository)
    service.repository.close()


def test_operator_can_explicitly_recover_a_grant_stranded_by_process_death(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    project = "hermes-operator-recovery"
    settings = GatewayServerSettings(workspace=tmp_path, project_id=project)
    service = create_hermes_fixture_service(settings)
    dispatcher = GatewayToolDispatcher(service)
    started = dispatcher.dispatch(
        "materials_inspiration_run",
        {
            "submission_id": "operator-recovery-submission",
            "goal": HERMES_FIXTURE_GOAL,
            "constraints": HERMES_FIXTURE_CONSTRAINTS.model_dump(mode="json"),
        },
    )
    run_id = started["run_id"]
    interaction_id = started["state"]["interaction"]["interaction_id"]
    assert operator_main(
        [
            "--workspace",
            str(tmp_path),
            "--project",
            project,
            "--run-id",
            run_id,
            "--confirmation-reference",
            "ticket:initial-human-confirmation",
            "--service-mode",
            "fixture",
        ]
    ) == 0
    capsys.readouterr()

    record = service.repository.get_run(run_id)
    assert record is not None
    assert isinstance(record.state, InteractionRequiredStateV1)
    action = ApproveActionV1(
        interaction_id=interaction_id,
        confirmed_by_user=True,
    )
    service.action_authorizer.authorize_and_consume(
        run_id=run_id,
        interaction=record.state.interaction,
        request_sha256=record.request_sha256,
        action=action,
    )
    assert dispatcher.dispatch("materials_run_get", {"run_id": run_id}) == started

    with pytest.raises(SystemExit) as missing_safety_assertion:
        operator_main(
            [
                "--workspace",
                str(tmp_path),
                "--project",
                project,
                "--run-id",
                run_id,
                "--confirmation-reference",
                "ticket:recovery-human-confirmation",
                "--service-mode",
                "fixture",
                "--recover-consumed-grant",
            ]
        )
    assert missing_safety_assertion.value.code == 2

    assert operator_main(
        [
            "--workspace",
            str(tmp_path),
            "--project",
            project,
            "--run-id",
            run_id,
            "--confirmation-reference",
            "ticket:recovery-human-confirmation",
            "--service-mode",
            "fixture",
            "--recover-consumed-grant",
            "--confirm-original-process-stopped",
        ]
    ) == 0
    capsys.readouterr()
    terminal = dispatcher.dispatch(
        "materials_run_act",
        {
            "run_id": run_id,
            "action": action.model_dump(mode="json"),
        },
    )
    assert terminal["state"]["status"] in {"SUCCEEDED", "PARTIAL"}
    service.repository.close()


@pytest.mark.parametrize(
    ("service_mode", "decision", "reason"),
    (
        ("fixture", "reject", "bounded request was not accepted"),
        ("public", "cancel", None),
    ),
)
def test_operator_reject_and_cancel_are_exact_audited_and_never_execute_runner(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    service_mode: str,
    decision: str,
    reason: str | None,
) -> None:
    project = f"hermes-operator-{service_mode}-{decision}"
    settings = GatewayServerSettings(workspace=tmp_path, project_id=project)
    if service_mode == "fixture":
        service = create_hermes_fixture_service(settings)
        arguments = {
            "submission_id": f"operator-{decision}-fixture-submission",
            "goal": HERMES_FIXTURE_GOAL,
            "constraints": HERMES_FIXTURE_CONSTRAINTS.model_dump(mode="json"),
        }
    else:
        service = create_hermes_inspiration_service(settings)
        arguments = _public_arguments(
            f"operator-{decision}-public-submission"
        )
    dispatcher = GatewayToolDispatcher(service)
    started = dispatcher.dispatch("materials_inspiration_run", arguments)
    run_id = started["run_id"]
    interaction = started["state"]["interaction"]
    interaction_id = interaction["interaction_id"]

    command = [
        "--workspace",
        str(tmp_path),
        "--project",
        project,
        "--run-id",
        run_id,
        "--confirmation-reference",
        f"ticket:{service_mode}-{decision}-human-decision",
        "--service-mode",
        service_mode,
        "--decision",
        decision,
    ]
    if reason is not None:
        command.extend(("--reason", reason))
    assert operator_main(command) == 0
    receipt = json.loads(capsys.readouterr().out)
    assert receipt["decision"] == decision
    assert receipt["execution_manifest_sha256"] == interaction["input_sha256"]
    receipt_action = json.loads(receipt["action_json"])
    assert receipt_action["kind"] == decision
    assert receipt_action["interaction_id"] == interaction_id
    if reason is not None:
        assert receipt_action["reason"] == reason

    stale_action = {
        "kind": decision,
        "interaction_id": "interaction-stale",
        "confirmed_by_user": True,
    }
    if reason is not None:
        stale_action["reason"] = reason
    with pytest.raises(GatewayDispatchError, match="stale"):
        dispatcher.dispatch(
            "materials_run_act",
            {"run_id": run_id, "action": stale_action},
        )

    if decision == "reject":
        wrong_action = {
            "kind": "reject",
            "interaction_id": interaction_id,
            "confirmed_by_user": True,
            "reason": "different unauthorised reason",
        }
    else:
        wrong_action = {
            "kind": "reject",
            "interaction_id": interaction_id,
            "confirmed_by_user": True,
        }
    with pytest.raises(GatewayDispatchError, match="operator grant"):
        dispatcher.dispatch(
            "materials_run_act",
            {"run_id": run_id, "action": wrong_action},
        )
    assert dispatcher.dispatch("materials_run_get", {"run_id": run_id}) == started

    exact_action = {
        "kind": decision,
        "interaction_id": interaction_id,
        "confirmed_by_user": True,
    }
    if reason is not None:
        exact_action["reason"] = reason
    terminal = dispatcher.dispatch(
        "materials_run_act",
        {"run_id": run_id, "action": exact_action},
    )
    assert terminal["state"]["status"] == "CANCELLED"
    expected_reason_fragment = "rejected" if decision == "reject" else "cancelled"
    assert expected_reason_fragment in terminal["state"]["reason"]
    assert not (
        tmp_path / project / "stages" / "inspiration" / run_id
    ).exists()

    approval_database = (
        tmp_path
        / project
        / ".gateway"
        / "operator-approval-grants.sqlite3"
    )
    connection = sqlite3.connect(approval_database)
    row = connection.execute(
        "SELECT action_kind, execution_manifest_sha256, action_json, consumed "
        "FROM one_time_action_grants"
    ).fetchone()
    connection.close()
    assert row is not None
    assert row[0] == decision
    assert row[1] == interaction["input_sha256"]
    assert json.loads(row[2]) == exact_action
    assert row[3] == 1

    with pytest.raises(GatewayDispatchError, match="pending interaction"):
        dispatcher.dispatch(
            "materials_run_act",
            {"run_id": run_id, "action": exact_action},
        )
    service.repository.close()
