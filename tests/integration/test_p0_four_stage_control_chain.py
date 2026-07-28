from __future__ import annotations

from pathlib import Path

from material_agent.orchestrator.models import RunStatus, StageId, StageStatus
from material_agent.orchestrator.runtime import OrchestratorRuntime


def test_four_stage_route_keeps_unregistered_production_capabilities_fail_closed(
    tmp_path, requirement, fixture_payload
) -> None:
    project_id = "project-p0-four-stage-boundary"
    project = OrchestratorRuntime.create_project(tmp_path, project_id)
    project_root = Path(project["project_root"])
    payload = requirement.model_dump(mode="json")
    payload["scientific_targets"] = [
        {
            "name": "P0 four-stage safety boundary",
            "required_evidence_level": "L4_MANY_BODY_VALIDATED",
        }
    ]
    payload["budget"].update(
        {
            "allow_ml": True,
            "allow_dft": True,
            "allow_many_body": True,
        }
    )

    with OrchestratorRuntime.from_workspace(tmp_path, project_id) as runtime:
        reviewing = runtime.start_run(
            raw_request="structured P0 four-stage safety boundary",
            initial_requirement=payload,
            fixture_payload=fixture_payload,
            run_id="run-p0-four-stage-boundary",
        )
        completed = runtime.approve(
            run_id="run-p0-four-stage-boundary",
            approval_id=reviewing.interrupts[0].value["approval_id"],
            decision="approve",
        )
        plan = runtime.store.read_json(
            "artifact://plans/run-p0-four-stage-boundary/execution_plan.json"
        )
        report = runtime.store.read_json(
            "artifact://reports/run-p0-four-stage-boundary/report.json"
        )

    assert [route["stage"] for route in plan["routes"]] == [
        stage.value for stage in StageId
    ]
    assert [route["disposition"] for route in plan["routes"]] == [
        "SELECTED",
        "UNAVAILABLE",
        "UNAVAILABLE",
        "UNAVAILABLE",
    ]
    assert [route["capability"]["registered"] for route in plan["routes"]] == [
        True,
        False,
        False,
        False,
    ]
    assert all(route["capability"]["is_mock"] is False for route in plan["routes"])
    assert completed.status is RunStatus.FAILED
    assert completed.stage_statuses == {
        "agent01": StageStatus.SUCCEEDED.value,
        "agent02": StageStatus.CAPABILITY_UNAVAILABLE.value,
        "agent03": StageStatus.CAPABILITY_UNAVAILABLE.value,
        "agent04": StageStatus.CAPABILITY_UNAVAILABLE.value,
    }
    for agent_id in ("agent02", "agent03", "agent04"):
        assert report["stages"][agent_id]["native_result_uri"] is None
        assert report["stages"][agent_id]["is_mock"] is False
        assert not (project_root / "stages" / agent_id).exists()
