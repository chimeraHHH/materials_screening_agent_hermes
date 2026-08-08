from __future__ import annotations

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
    InteractionRequiredStateV1,
)
from material_agent.gateway.persistence import SqliteGatewayRepository
from material_agent.integration.hermes_service import (
    HERMES_FIXTURE_CONSTRAINTS,
    HERMES_FIXTURE_GOAL,
    create_hermes_fixture_service,
)
from material_agent.integration.operator_approval import main as operator_main


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
