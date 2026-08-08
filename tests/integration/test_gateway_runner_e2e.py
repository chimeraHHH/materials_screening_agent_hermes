from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import sys
from pathlib import Path
from typing import Any

import pytest

from material_agent.gateway.mcp_server import (
    GatewayDispatchError,
    GatewayServerSettings,
    GatewayToolDispatcher,
)
from material_agent.gateway.models import (
    GatewayResultRecordV1,
    gateway_result_sha256,
    inspiration_request_sha256,
)
from material_agent.gateway.persistence import SqliteGatewayRepository
from material_agent.inspiration.models import (
    InspirationInputV1,
    InspirationStageResultV1,
    TransformationPlanV1,
    TransformationStatus,
)
from material_agent.integration.hermes_service import (
    HERMES_FIXTURE_CONSTRAINTS,
    HERMES_FIXTURE_GOAL,
    create_hermes_fixture_service,
)
from material_agent.integration.operator_approval import (
    issue_requirement_freeze_grant,
)


def _arguments(submission_id: str) -> dict[str, Any]:
    return {
        "submission_id": submission_id,
        "goal": HERMES_FIXTURE_GOAL,
        "constraints": HERMES_FIXTURE_CONSTRAINTS.model_dump(mode="json"),
    }


def _close(service) -> None:
    assert isinstance(service.repository, SqliteGatewayRepository)
    service.repository.close()


def _grant_requirement_freeze(
    settings: GatewayServerSettings,
    run_id: str,
    *,
    confirmation_reference: str,
) -> None:
    receipt = issue_requirement_freeze_grant(
        settings=settings,
        run_id=run_id,
        confirmation_reference=confirmation_reference,
    )
    assert receipt["run_id"] == run_id


def _base_result(payload: dict[str, Any]) -> GatewayResultRecordV1:
    record = dict(payload)
    record.pop("result_sha256", None)
    record.pop("verified", None)
    return GatewayResultRecordV1.model_validate_json(json.dumps(record))


def test_gateway_real_runner_persists_across_three_service_lifetimes(
    tmp_path: Path,
) -> None:
    settings = GatewayServerSettings(
        workspace=tmp_path,
        project_id="hermes-real-e2e",
    )

    first_service = create_hermes_fixture_service(settings)
    first = GatewayToolDispatcher(first_service)
    started = first.dispatch(
        "materials_inspiration_run",
        _arguments("hermes-real-submission"),
    )
    assert started["state"]["status"] == "INTERACTION_REQUIRED"
    run_id = started["run_id"]
    interaction_id = started["state"]["interaction"]["interaction_id"]
    _close(first_service)

    second_service = create_hermes_fixture_service(settings)
    second = GatewayToolDispatcher(second_service)
    assert second.dispatch("materials_run_get", {"run_id": run_id}) == started
    _grant_requirement_freeze(
        settings,
        run_id,
        confirmation_reference="test:three-lifetimes-user-confirmation",
    )
    terminal = second.dispatch(
        "materials_run_act",
        {
            "run_id": run_id,
            "action": {
                "kind": "approve",
                "interaction_id": interaction_id,
                "confirmed_by_user": True,
            },
        },
    )
    assert terminal["state"]["status"] in {"SUCCEEDED", "PARTIAL"}
    _close(second_service)

    project_root = tmp_path / "hermes-real-e2e"
    stage_root = project_root / "stages" / "inspiration" / run_id
    stage_result_bytes = (stage_root / "stage_result.json").read_bytes()
    stage = InspirationStageResultV1.model_validate_json(stage_result_bytes)
    assert stage.run_id == run_id
    assert stage.report_artifact.uri == (
        f"artifact://stages/inspiration/{run_id}/report.md"
    )
    report_bytes = (stage_root / "report.md").read_bytes()
    assert hashlib.sha256(report_bytes).hexdigest() == (
        stage.report_artifact.sha256
    )
    assert terminal["state"]["authoritative_sha256"] == (
        stage.report_artifact.sha256
    )

    input_snapshot = InspirationInputV1.model_validate_json(
        (stage_root / "input_snapshot.json").read_bytes()
    )
    requirement_path = project_root / input_snapshot.requirement_artifact.uri.removeprefix(
        "artifact://"
    )
    requirement = json.loads(requirement_path.read_text(encoding="utf-8"))
    assert requirement["gateway_request"]["goal"] == HERMES_FIXTURE_GOAL
    assert requirement["gateway_request"]["constraints"] == (
        HERMES_FIXTURE_CONSTRAINTS.model_dump(mode="json")
    )
    persisted_repository = SqliteGatewayRepository(
        project_root / ".gateway" / "materials-gateway.sqlite3"
    )
    persisted = persisted_repository.get_run(run_id)
    assert persisted is not None
    persisted_result = persisted_repository.get_result(run_id)
    assert persisted_result is not None
    assert persisted.state.result_sha256 == gateway_result_sha256(persisted_result)
    assert requirement["gateway_request_sha256"] == inspiration_request_sha256(
        persisted.request
    )
    persisted_repository.close()

    plans = tuple(
        TransformationPlanV1.model_validate_json(line)
        for line in (stage_root / "transformation_proposals.jsonl")
        .read_bytes()
        .splitlines()
        if line.strip()
    )
    assert len(plans) == 1
    assert plans[0].status is TransformationStatus.STRUCTURE_VALID
    assert plans[0].output_structure_artifact is not None
    output_path = project_root / plans[0].output_structure_artifact.uri.removeprefix(
        "artifact://"
    )
    assert output_path.is_file()
    assert hashlib.sha256(output_path.read_bytes()).hexdigest() == (
        plans[0].output_structure_artifact.sha256
    )

    third_service = create_hermes_fixture_service(settings)
    third = GatewayToolDispatcher(third_service)
    recovered = third.dispatch("materials_run_get", {"run_id": run_id})
    result = third.dispatch("materials_result_get", {"run_id": run_id})
    idempotent = third.dispatch(
        "materials_inspiration_run",
        _arguments("hermes-real-submission"),
    )
    assert recovered == terminal
    assert idempotent == terminal
    assert result["verified"] is True
    assert terminal["state"]["result_sha256"] == result["result_sha256"]
    assert result["result_sha256"] == gateway_result_sha256(_base_result(result))
    assert result["bundle"]["outcome"] == "SUCCEEDED"
    assert len(result["bundle"]["selected_candidates"]) == 1
    assert result["cost_ledger"]["fetched_documents"] == 0
    assert (stage_root / "stage_result.json").read_bytes() == stage_result_bytes
    _close(third_service)


def test_fixture_rejects_incompatible_elements_before_runner_execution(
    tmp_path: Path,
) -> None:
    settings = GatewayServerSettings(tmp_path, "hermes-incompatible")
    service = create_hermes_fixture_service(settings)
    dispatcher = GatewayToolDispatcher(service)
    constraints = HERMES_FIXTURE_CONSTRAINTS.model_dump(mode="json")
    constraints["required_elements"] = ["Pb"]
    constraints["excluded_elements"] = ["Se"]

    rejected = dispatcher.dispatch(
        "materials_inspiration_run",
        {
            "submission_id": "incompatible-elements",
            "goal": HERMES_FIXTURE_GOAL,
            "constraints": constraints,
        },
    )

    assert rejected["state"]["status"] == "FAILED"
    assert rejected["state"]["public_error_code"] == (
        "UNSUPPORTED_FIXTURE_REQUEST"
    )
    assert not (
        tmp_path
        / "hermes-incompatible"
        / "stages"
        / "inspiration"
        / rejected["run_id"]
    ).exists()
    _close(service)


def test_stage_result_tampering_is_rejected_before_gateway_projection(
    tmp_path: Path,
) -> None:
    settings = GatewayServerSettings(tmp_path, "hermes-stage-tamper")
    service = create_hermes_fixture_service(settings)
    dispatcher = GatewayToolDispatcher(service)
    started = dispatcher.dispatch(
        "materials_inspiration_run",
        _arguments("tampered-stage-result"),
    )
    real_runner = service.companion.runner

    class _TamperingRunner:
        def run(self, **arguments: Any):
            result = real_runner.run(**arguments)
            relative = result.stage_result_artifact.uri.removeprefix("artifact://")
            (tmp_path / "hermes-stage-tamper" / relative).write_bytes(b"{}")
            return result

    service.companion.runner = _TamperingRunner()
    _grant_requirement_freeze(
        settings,
        started["run_id"],
        confirmation_reference="test:stage-tamper-user-confirmation",
    )
    terminal = dispatcher.dispatch(
        "materials_run_act",
        {
            "run_id": started["run_id"],
            "action": {
                "kind": "approve",
                "interaction_id": started["state"]["interaction"]["interaction_id"],
                "confirmed_by_user": True,
            },
        },
    )

    assert terminal["state"]["status"] == "FAILED"
    assert terminal["state"]["public_error_code"] == "ADAPTER_CONTRACT_ERROR"
    _close(service)


def test_restarted_result_read_rejects_report_tampering(tmp_path: Path) -> None:
    settings = GatewayServerSettings(tmp_path, "hermes-report-tamper")
    service = create_hermes_fixture_service(settings)
    dispatcher = GatewayToolDispatcher(service)
    started = dispatcher.dispatch(
        "materials_inspiration_run",
        _arguments("tampered-report"),
    )
    _grant_requirement_freeze(
        settings,
        started["run_id"],
        confirmation_reference="test:report-tamper-user-confirmation",
    )
    terminal = dispatcher.dispatch(
        "materials_run_act",
        {
            "run_id": started["run_id"],
            "action": {
                "kind": "approve",
                "interaction_id": started["state"]["interaction"]["interaction_id"],
                "confirmed_by_user": True,
            },
        },
    )
    assert terminal["state"]["status"] in {"SUCCEEDED", "PARTIAL"}
    report_path = (
        tmp_path
        / "hermes-report-tamper"
        / "stages"
        / "inspiration"
        / started["run_id"]
        / "report.md"
    )
    report_path.write_bytes(b"tampered report\n")
    _close(service)

    restarted = create_hermes_fixture_service(settings)
    with pytest.raises(GatewayDispatchError, match="SHA-256"):
        GatewayToolDispatcher(restarted).dispatch(
            "materials_result_get",
            {"run_id": started["run_id"]},
        )
    _close(restarted)


@pytest.mark.skipif(
    importlib.util.find_spec("mcp") is None,
    reason="actual stdio runs in the combined Gateway MCP environment",
)
def test_real_runner_through_mcp_stdio_survives_process_restart(
    tmp_path: Path,
) -> None:
    import anyio

    anyio.run(_real_stdio_restart_scenario, tmp_path)


async def _real_stdio_restart_scenario(workspace: Path) -> None:
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    repository_root = Path(__file__).resolve().parents[2]
    environment = dict(os.environ)
    python_path = [str(repository_root / "src")]
    if environment.get("PYTHONPATH"):
        python_path.append(environment["PYTHONPATH"])
    environment["PYTHONPATH"] = os.pathsep.join(python_path)
    parameters = StdioServerParameters(
        command=sys.executable,
        args=[
            "-m",
            "material_agent.integration.mcp_server",
            "--workspace",
            str(workspace),
            "--project",
            "hermes-real-stdio",
            "--service-factory",
            (
                "material_agent.integration.hermes_service:"
                "create_hermes_fixture_service"
            ),
        ],
        cwd=str(repository_root),
        env=environment,
    )

    with open(os.devnull, "w", encoding="utf-8") as error_log:
        async with stdio_client(parameters, errlog=error_log) as streams:
            async with ClientSession(*streams) as session:
                await session.initialize()
                listed = await session.list_tools()
                assert tuple(tool.name for tool in listed.tools) == (
                    "materials_inspiration_run",
                    "materials_run_get",
                    "materials_run_act",
                    "materials_result_get",
                )
                started = await session.call_tool(
                    "materials_inspiration_run",
                    _arguments("real-stdio-submission"),
                )
                assert not started.isError
                started_body = started.structuredContent or {}
                ungranted = await session.call_tool(
                    "materials_run_act",
                    {
                        "run_id": started_body["run_id"],
                        "action": {
                            "kind": "approve",
                            "interaction_id": started_body["state"]["interaction"][
                                "interaction_id"
                            ],
                            "confirmed_by_user": True,
                        },
                    },
                )
                assert ungranted.isError
                _grant_requirement_freeze(
                    GatewayServerSettings(
                        workspace=workspace,
                        project_id="hermes-real-stdio",
                    ),
                    started_body["run_id"],
                    confirmation_reference="test:mcp-stdio-user-confirmation",
                )
                terminal = await session.call_tool(
                    "materials_run_act",
                    {
                        "run_id": started_body["run_id"],
                        "action": {
                            "kind": "approve",
                            "interaction_id": started_body["state"]["interaction"][
                                "interaction_id"
                            ],
                            "confirmed_by_user": True,
                        },
                    },
                )
                assert not terminal.isError
                terminal_body = terminal.structuredContent or {}
                assert terminal_body["state"]["status"] in {
                    "SUCCEEDED",
                    "PARTIAL",
                }
                run_id = started_body["run_id"]

    with open(os.devnull, "w", encoding="utf-8") as error_log:
        async with stdio_client(parameters, errlog=error_log) as streams:
            async with ClientSession(*streams) as session:
                await session.initialize()
                recovered = await session.call_tool(
                    "materials_run_get",
                    {"run_id": run_id},
                )
                result = await session.call_tool(
                    "materials_result_get",
                    {"run_id": run_id},
                )
                assert not recovered.isError
                assert recovered.structuredContent == terminal_body
                assert not result.isError
                result_body = result.structuredContent or {}
                assert result_body["verified"] is True
                assert (
                    terminal_body["state"]["result_sha256"]
                    == result_body["result_sha256"]
                )
                assert result_body["result_sha256"] == gateway_result_sha256(
                    _base_result(result_body)
                )
                assert result_body["bundle"]["outcome"] == "SUCCEEDED"
                assert len(result_body["bundle"]["selected_candidates"]) == 1
                assert result_body["cost_ledger"]["fetched_documents"] == 0
