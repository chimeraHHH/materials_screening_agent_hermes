from __future__ import annotations

from datetime import UTC, datetime

import pytest

from material_agent.orchestrator.models import (
    ApprovalStatus,
    RunStatus,
    StageExecutionRecord,
    StageId,
    StageStatus,
)
from material_agent.orchestrator.storage import (
    OrchestratorRepository,
    RepositoryConflictError,
)


def test_repository_keeps_immutable_approval_decisions(tmp_path) -> None:
    repository = OrchestratorRepository(tmp_path / "orchestrator.sqlite3")
    try:
        repository.create_project("project-test", str(tmp_path))
        repository.create_run(
            run_id="run-test",
            project_id="project-test",
            raw_request="request",
        )
        repository.ensure_approval(
            approval_id="approval-test",
            interaction_id="interaction-test",
            run_id="run-test",
            gate_type="REQUIREMENT_CONFIRMATION",
            input_sha256="abc",
            payload={"input": "snapshot"},
        )
        decision = {"decision": "approve"}
        repository.decide_approval(
            "approval-test",
            status=ApprovalStatus.APPROVED,
            decision=decision,
        )
        repository.decide_approval(
            "approval-test",
            status=ApprovalStatus.APPROVED,
            decision=decision,
        )

        with pytest.raises(RepositoryConflictError):
            repository.decide_approval(
                "approval-test",
                status=ApprovalStatus.REJECTED,
                decision={"decision": "reject"},
            )
        repository.update_run("run-test", status=RunStatus.RUNNING)
        assert repository.get_run("run-test")["status"] == "RUNNING"
    finally:
        repository.close()


def test_repository_rejects_stage_plan_and_operation_key_conflicts(
    tmp_path,
) -> None:
    repository = OrchestratorRepository(tmp_path / "orchestrator.sqlite3")
    try:
        repository.create_project("project-test", str(tmp_path))
        repository.create_run(
            run_id="run-test",
            project_id="project-test",
            raw_request="request",
        )
        frozen = StageExecutionRecord(
            run_id="run-test",
            stage=StageId.ML,
            agent_id="agent02",
            attempt=1,
            operation_key="operation-a",
            status=StageStatus.READY,
            plan_uri="artifact://plan.json",
            plan_sha256="plan-a",
            updated_at=datetime(2026, 7, 26, tzinfo=UTC),
        )
        repository.record_stage_attempt(frozen)

        with pytest.raises(
            RepositoryConflictError, match="operation key is immutable"
        ):
            repository.record_stage_attempt(
                frozen.model_copy(update={"operation_key": "operation-b"})
            )
        with pytest.raises(
            RepositoryConflictError, match="plan hash changed"
        ):
            repository.record_stage_attempt(
                frozen.model_copy(update={"plan_sha256": "plan-b"})
            )
        with pytest.raises(
            RepositoryConflictError, match="plan URI changed"
        ):
            repository.record_stage_attempt(
                frozen.model_copy(
                    update={"plan_uri": "artifact://other-plan.json"}
                )
            )

        stored = repository.get_stage_attempt(
            "run-test", StageId.ML, 1
        )
        assert stored["operation_key"] == "operation-a"
        assert stored["plan_sha256"] == "plan-a"
    finally:
        repository.close()
