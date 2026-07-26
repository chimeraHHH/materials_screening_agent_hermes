from __future__ import annotations

import pytest

from material_agent.orchestrator.models import ApprovalStatus, RunStatus
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
