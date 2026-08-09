from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from material_agent.gateway.authorization import RequirementFreezeGrantIssuer
from material_agent.gateway.mcp_server import GatewayToolDispatcher
from material_agent.gateway.models import InteractionRequiredStateV1
from material_agent.integration.hermes_service import (
    HERMES_FIXTURE_CONSTRAINTS,
    HERMES_FIXTURE_GOAL,
    GatewayServerSettings,
)
from material_agent.integration.queued_gateway import (
    create_queued_hermes_fixture_service,
    worker_from_service,
)


def test_legacy_worker_compatibility_keeps_mcp_act_off_runner_thread(
    tmp_path: Path,
) -> None:
    settings = GatewayServerSettings(tmp_path, "queued-factory")
    service = create_queued_hermes_fixture_service(settings)
    dispatcher = GatewayToolDispatcher(service)
    started = dispatcher.dispatch(
        "materials_inspiration_run",
        {
            "submission_id": "queued-factory-submission",
            "goal": HERMES_FIXTURE_GOAL,
            "constraints": HERMES_FIXTURE_CONSTRAINTS.model_dump(mode="json"),
        },
    )
    record = service.repository.get_run(started["run_id"])
    assert record is not None
    assert isinstance(record.state, InteractionRequiredStateV1)
    interaction = record.state.interaction
    RequirementFreezeGrantIssuer(
        repository=service.repository,
        grant_store=service.grant_store,
    ).grant_current(
        run_id=record.run_id,
        confirmation_reference="test:queued-factory-approval",
        expected_execution_manifest_sha256=interaction.input_sha256,
    )
    queued = dispatcher.dispatch(
        "materials_run_act",
        {
            "run_id": record.run_id,
            "action": {
                "kind": "approve",
                "interaction_id": interaction.interaction_id,
                "confirmed_by_user": True,
            },
        },
    )
    assert queued["state"]["status"] == "RUNNING"
    stage_result = (
        tmp_path
        / "queued-factory"
        / "stages"
        / "inspiration"
        / record.run_id
        / "stage_result.json"
    )
    assert not stage_result.exists()

    completed_job = worker_from_service(service).run_once(
        worker_id="queued-factory-worker",
        lease_seconds=3_600,
    )
    assert completed_job is not None
    terminal = dispatcher.dispatch("materials_run_get", {"run_id": record.run_id})
    assert terminal["state"]["status"] in {"SUCCEEDED", "PARTIAL"}
    assert stage_result.is_file()
    result = dispatcher.dispatch("materials_result_get", {"run_id": record.run_id})
    assert result["verified"] is True


def test_supervised_fixture_child_runs_in_real_subprocess(tmp_path: Path) -> None:
    settings = GatewayServerSettings(tmp_path, "queued-supervised-fixture")
    service = create_queued_hermes_fixture_service(settings)
    dispatcher = GatewayToolDispatcher(service)
    started = dispatcher.dispatch(
        "materials_inspiration_run",
        {
            "submission_id": "queued-supervised-submission",
            "goal": HERMES_FIXTURE_GOAL,
            "constraints": HERMES_FIXTURE_CONSTRAINTS.model_dump(mode="json"),
        },
    )
    record = service.repository.get_run(started["run_id"])
    assert record is not None and isinstance(
        record.state, InteractionRequiredStateV1
    )
    interaction = record.state.interaction
    RequirementFreezeGrantIssuer(
        repository=service.repository,
        grant_store=service.grant_store,
    ).grant_current(
        run_id=record.run_id,
        confirmation_reference="test:queued-supervised-approval",
        expected_execution_manifest_sha256=interaction.input_sha256,
    )
    dispatcher.dispatch(
        "materials_run_act",
        {
            "run_id": record.run_id,
            "action": {
                "kind": "approve",
                "interaction_id": interaction.interaction_id,
                "confirmed_by_user": True,
            },
        },
    )

    worker_process = subprocess.run(
        (
            sys.executable,
            "-m",
            "material_agent.integration.queued_gateway",
            "--workspace",
            str(tmp_path),
            "--project",
            settings.project_id,
            "--service-mode",
            "fixture",
            "--lease-seconds",
            "30",
            "--heartbeat-seconds",
            "1",
            "--once",
        ),
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert worker_process.returncode == 0, worker_process.stderr
    worker_result = json.loads(worker_process.stdout)
    assert worker_result["status"] == "SUCCEEDED"
    terminal = dispatcher.dispatch("materials_run_get", {"run_id": record.run_id})
    assert terminal["state"]["status"] in {"SUCCEEDED", "PARTIAL"}
    result = dispatcher.dispatch("materials_result_get", {"run_id": record.run_id})
    assert result["verified"] is True
    protocol_root = (
        service.action_queue.database_path.parent / "gateway-action-supervisor"
    )
    assert not tuple(protocol_root.rglob("*.json"))
