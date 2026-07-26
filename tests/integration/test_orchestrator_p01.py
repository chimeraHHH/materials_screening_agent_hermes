from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import pytest

from material_agent.orchestrator.identity import (
    DeterministicIdFactory,
    FixedClock,
)
from material_agent.orchestrator.models import (
    CancelOutcome,
    ControlOutcomeType,
    ControlStageOutcome,
    ExternalJobStatus,
    LEGACY_ORCHESTRATOR_CONTRACT_VERSION,
    RunStatus,
    StageCapability,
    StageExecutionContext,
    StageId,
    StageInputValidation,
    StageStatus,
)
from material_agent.orchestrator.runners import (
    FixtureStageRunner,
    StageRunnerRegistry,
)
from material_agent.orchestrator.runtime import OrchestratorRuntime
from material_agent.orchestrator.runtime import CheckpointCompatibilityError
from material_agent.orchestrator.storage import RepositoryConflictError
from material_agent.retrieval.storage import LocalArtifactStore


def _complete_source_run(
    tmp_path: Path,
    requirement,
    fixture_payload,
    *,
    project_id: str,
    run_id: str,
) -> tuple[dict, dict]:
    OrchestratorRuntime.create_project(tmp_path, project_id)
    with OrchestratorRuntime.from_workspace(tmp_path, project_id) as runtime:
        waiting = runtime.start_run(
            raw_request="structured",
            initial_requirement=requirement.model_dump(mode="json"),
            fixture_payload=fixture_payload,
            run_id=run_id,
        )
        completed = runtime.approve(
            run_id=run_id,
            approval_id=waiting.interrupts[0].value["approval_id"],
            decision="approve",
        )
        assert completed.status is RunStatus.SUCCEEDED
        requirement_row = runtime.repository.get_requirement(run_id, 1)
        manifest = runtime.store.inspect(
            f"artifact://stages/agent01/{run_id}/candidate_manifest.jsonl",
            media_type="application/x-ndjson",
        )
        return requirement_row, manifest.model_dump(mode="json")


def _stage_input(source_run_id: str, requirement: dict, manifest: dict) -> dict:
    return {
        "source_run_id": source_run_id,
        "requirement_revision": requirement["revision"],
        "requirement_artifact_uri": requirement["artifact_uri"],
        "requirement_artifact_sha256": requirement["artifact_sha256"],
        "artifacts": {
            "candidate_manifest": {
                "uri": manifest["uri"],
                "sha256": manifest["sha256"],
            }
        },
    }


def test_execution_plan_has_four_routes_and_allow_is_not_selection(
    tmp_path, requirement, fixture_payload
) -> None:
    requirement_row, _manifest = _complete_source_run(
        tmp_path,
        requirement,
        fixture_payload,
        project_id="project-routes",
        run_id="run-routes",
    )
    with OrchestratorRuntime.from_workspace(
        tmp_path, "project-routes"
    ) as runtime:
        plan = runtime.store.read_json(
            "artifact://plans/run-routes/execution_plan.json"
        )

    assert [route["stage"] for route in plan["routes"]] == [
        "retrieval",
        "ml",
        "dft",
        "many_body",
    ]
    assert plan["routes"][0]["disposition"] == "SELECTED"
    assert plan["routes"][1]["disposition"] == "SKIPPED"
    assert "budget permission alone" in plan["routes"][1]["reason"]
    assert requirement_row["revision"] == 1


@pytest.mark.parametrize(
    ("allow_ml", "expected_disposition", "expected_status"),
    [
        (False, "BLOCKED", "BLOCKED_MISSING_INPUT"),
        (True, "UNAVAILABLE", "CAPABILITY_UNAVAILABLE"),
    ],
)
def test_required_evidence_respects_permission_and_capability(
    tmp_path,
    requirement,
    fixture_payload,
    allow_ml,
    expected_disposition,
    expected_status,
) -> None:
    payload = requirement.model_dump(mode="json")
    payload["scientific_targets"] = [
        {
            "name": "ml stability screening",
            "required_evidence_level": "L2_ML_SCREENED",
        }
    ]
    payload["budget"]["allow_ml"] = allow_ml
    project_id = f"project-required-ml-{str(allow_ml).lower()}"
    OrchestratorRuntime.create_project(tmp_path, project_id)
    with OrchestratorRuntime.from_workspace(
        tmp_path, project_id
    ) as runtime:
        waiting = runtime.start_run(
            raw_request="structured",
            initial_requirement=payload,
            fixture_payload=fixture_payload,
            run_id="run-required-ml",
        )
        failed = runtime.approve(
            run_id="run-required-ml",
            approval_id=waiting.interrupts[0].value["approval_id"],
            decision="approve",
        )
        plan = runtime.store.read_json(
            "artifact://plans/run-required-ml/execution_plan.json"
        )

    assert failed.status is RunStatus.FAILED
    assert failed.stage_statuses == {
        "agent01": "SUCCEEDED",
        "agent02": expected_status,
    }
    assert plan["routes"][1]["required"] is True
    assert plan["routes"][1]["disposition"] == expected_disposition


def test_run_stage_distinguishes_missing_input_and_unavailable_capability(
    tmp_path, requirement, fixture_payload
) -> None:
    requirement_row, manifest = _complete_source_run(
        tmp_path,
        requirement,
        fixture_payload,
        project_id="project-direct",
        run_id="run-source",
    )
    base = _stage_input("run-source", requirement_row, manifest)
    missing = dict(base)
    missing["artifacts"] = {}

    with OrchestratorRuntime.from_workspace(
        tmp_path, "project-direct"
    ) as runtime:
        blocked = runtime.start_stage_run(
            stage=StageId.ML,
            stage_input=missing,
            run_id="run-ml-blocked",
        )
        unavailable = runtime.start_stage_run(
            stage=StageId.ML,
            stage_input=base,
            run_id="run-ml-unavailable",
        )

    assert blocked.status is RunStatus.PAUSED
    assert blocked.stage_statuses == {
        "agent02": StageStatus.BLOCKED_MISSING_INPUT.value
    }
    assert unavailable.status is RunStatus.PAUSED
    assert unavailable.stage_statuses == {
        "agent02": StageStatus.CAPABILITY_UNAVAILABLE.value
    }


def test_fixture_runner_uses_generic_stage_path_without_evidence_uplift(
    tmp_path, requirement, fixture_payload
) -> None:
    project_id = "project-fixture-route"
    requirement_row, manifest = _complete_source_run(
        tmp_path,
        requirement,
        fixture_payload,
        project_id=project_id,
        run_id="run-source",
    )
    project_root = tmp_path / project_id
    capability = StageCapability(
        stage=StageId.ML,
        agent_id="agent02",
        registered=True,
        is_mock=True,
        required_inputs=["requirement", "candidate_manifest"],
    )
    registry = StageRunnerRegistry()
    registry.register(
        StageId.ML,
        lambda _context: FixtureStageRunner(
            capability=capability,
            artifact_store=LocalArtifactStore(project_root),
        ),
        capability,
    )
    with OrchestratorRuntime.from_workspace(
        tmp_path, project_id, runner_registry=registry
    ) as runtime:
        completed = runtime.start_stage_run(
            stage=StageId.ML,
            stage_input=_stage_input(
                "run-source", requirement_row, manifest
            ),
            run_id="run-ml-fixture",
        )
        report = runtime.store.read_json(
            "artifact://reports/run-ml-fixture/report.json"
        )

    assert completed.status is RunStatus.SUCCEEDED
    assert completed.stage_statuses == {"agent02": "SUCCEEDED"}
    assert report["stages"]["agent02"]["is_mock"] is True
    assert "evidence_level" not in report["stages"]["agent02"]


def test_generic_graph_routes_agent01_output_into_sync_fixture_stage(
    tmp_path, requirement, fixture_payload
) -> None:
    project_id = "project-generic-multistage"
    project = OrchestratorRuntime.create_project(tmp_path, project_id)
    project_root = Path(project["project_root"])
    capability = StageCapability(
        stage=StageId.ML,
        agent_id="agent02",
        registered=True,
        is_mock=True,
        required_inputs=["requirement", "candidate_manifest"],
    )
    registry = StageRunnerRegistry()
    registry.register(
        StageId.ML,
        lambda _context: FixtureStageRunner(
            capability=capability,
            artifact_store=LocalArtifactStore(project_root),
        ),
        capability,
    )
    payload = requirement.model_dump(mode="json")
    payload["scientific_targets"] = [
        {
            "name": "control-flow ML fixture",
            "required_evidence_level": "L2_ML_SCREENED",
        }
    ]
    payload["budget"]["allow_ml"] = True
    with OrchestratorRuntime.from_workspace(
        tmp_path, project_id, runner_registry=registry
    ) as runtime:
        waiting = runtime.start_run(
            raw_request="structured",
            initial_requirement=payload,
            fixture_payload=fixture_payload,
            run_id="run-generic-multistage",
        )
        completed = runtime.approve(
            run_id="run-generic-multistage",
            approval_id=waiting.interrupts[0].value["approval_id"],
            decision="approve",
        )
        report = runtime.store.read_json(
            "artifact://reports/run-generic-multistage/report.json"
        )

    assert completed.status is RunStatus.SUCCEEDED
    assert completed.stage_statuses == {
        "agent01": "SUCCEEDED",
        "agent02": "SUCCEEDED",
    }
    assert report["stages"]["agent02"]["is_mock"] is True
    assert report["stages"]["agent03"]["status"] == "SKIPPED"


@dataclass
class _Backend:
    submits: int = 0
    reconciles: int = 0
    cancels: int = 0
    inconsistent_ref: bool = False
    remaining_running: int = 0
    terminal_status: ExternalJobStatus = ExternalJobStatus.SUCCEEDED


class _ExternalFixtureRunner:
    backend_name = "fixture-external"

    def __init__(
        self,
        capability: StageCapability,
        store: LocalArtifactStore,
        backend: _Backend,
    ) -> None:
        self.capability = capability
        self.store = store
        self.backend = backend

    def validate_input(
        self, _context: StageExecutionContext
    ) -> StageInputValidation:
        return StageInputValidation(valid=True)

    def start(
        self, context: StageExecutionContext, idempotency_key: str
    ) -> ControlStageOutcome:
        self.backend.submits += 1
        return ControlStageOutcome(
            stage=context.stage,
            agent_id=context.agent_id,
            outcome=ControlOutcomeType.WAITING_EXTERNAL,
            status=StageStatus.RUNNING,
            idempotency_key=idempotency_key,
            operation_ref=f"fixture-operation://{idempotency_key}",
            external_job_ref="fixture-job-1",
            external_status=ExternalJobStatus.RUNNING,
            external_status_sequence=1,
            summary={"is_mock": True},
        )

    def reconcile(
        self,
        context: StageExecutionContext,
        external_job_ref: str,
        idempotency_key: str,
    ) -> ControlStageOutcome:
        self.backend.reconciles += 1
        sequence = self.backend.reconciles + 1
        if self.backend.remaining_running > 0:
            self.backend.remaining_running -= 1
            return ControlStageOutcome(
                stage=context.stage,
                agent_id=context.agent_id,
                outcome=ControlOutcomeType.WAITING_EXTERNAL,
                status=StageStatus.RUNNING,
                idempotency_key=idempotency_key,
                operation_ref=f"fixture-operation://{idempotency_key}",
                external_job_ref=external_job_ref,
                external_status=ExternalJobStatus.RUNNING,
                external_status_sequence=sequence,
                summary={"is_mock": True},
            )
        if self.backend.terminal_status is not ExternalJobStatus.SUCCEEDED:
            return ControlStageOutcome(
                stage=context.stage,
                agent_id=context.agent_id,
                outcome=ControlOutcomeType.FAILED,
                status=StageStatus.PERMANENT_FAILED,
                idempotency_key=idempotency_key,
                operation_ref=f"fixture-operation://{idempotency_key}",
                external_job_ref=external_job_ref,
                external_status=self.backend.terminal_status,
                external_status_sequence=sequence,
                summary={"is_mock": True},
            )
        ref = self.store.write_json(
            f"fixtures/external/{context.run_id}/result.json",
            {
                "is_mock": True,
                "scientific_values": [],
                "external_job_ref": external_job_ref,
            },
            immutable=True,
        )
        return ControlStageOutcome(
            stage=context.stage,
            agent_id=context.agent_id,
            outcome=ControlOutcomeType.COMPLETED,
            status=StageStatus.SUCCEEDED,
            idempotency_key=idempotency_key,
            operation_ref=f"fixture-operation://{idempotency_key}",
            external_job_ref=(
                "fixture-job-conflict"
                if self.backend.inconsistent_ref
                else external_job_ref
            ),
            external_status=ExternalJobStatus.SUCCEEDED,
            external_status_sequence=sequence,
            native_result_uri=ref.uri,
            native_result_sha256=ref.sha256,
            summary={"is_mock": True, "scientific_values": []},
        )

    def cancel(
        self, external_job_ref: str, idempotency_key: str
    ) -> CancelOutcome:
        self.backend.cancels += 1
        return CancelOutcome(
            external_job_ref=external_job_ref,
            status=ExternalJobStatus.CANCELLED,
            idempotency_key=idempotency_key,
            message="fixture external job cancelled",
        )


def _external_registry(
    project_root: Path, backend: _Backend
) -> StageRunnerRegistry:
    capability = StageCapability(
        stage=StageId.DFT,
        agent_id="agent03",
        registered=True,
        is_mock=True,
        required_inputs=["requirement", "candidate_manifest"],
        requires_approval=True,
        supports_external=True,
    )
    registry = StageRunnerRegistry()
    registry.register(
        StageId.DFT,
        lambda _context: _ExternalFixtureRunner(
            capability,
            LocalArtifactStore(project_root),
            backend,
        ),
        capability,
    )
    return registry


def test_external_stage_requires_gate_and_resumes_without_resubmit(
    tmp_path, requirement, fixture_payload
) -> None:
    project_id = "project-external"
    requirement_row, manifest = _complete_source_run(
        tmp_path,
        requirement,
        fixture_payload,
        project_id=project_id,
        run_id="run-source",
    )
    backend = _Backend()
    registry = _external_registry(tmp_path / project_id, backend)
    with OrchestratorRuntime.from_workspace(
        tmp_path, project_id, runner_registry=registry
    ) as runtime:
        approval_view = runtime.start_stage_run(
            stage=StageId.DFT,
            stage_input=_stage_input(
                "run-source", requirement_row, manifest
            ),
            run_id="run-dft",
        )
        assert approval_view.status is RunStatus.WAITING_APPROVAL
        approval = approval_view.interrupts[0].value
        assert approval["interaction_type"] == "EXPENSIVE_BATCH_APPROVAL"
        waiting = runtime.approve(
            run_id="run-dft",
            approval_id=approval["approval_id"],
            decision="approve",
        )
        assert waiting.status is RunStatus.PAUSED
        assert waiting.stage_statuses == {"agent03": "RUNNING"}
        assert backend.submits == 1
        assert backend.reconciles == 0

    with OrchestratorRuntime.from_workspace(
        tmp_path, project_id, runner_registry=registry
    ) as runtime:
        local = runtime.status("run-dft")
        assert local.status is RunStatus.PAUSED
        assert backend.reconciles == 0
        completed = runtime.resume(run_id="run-dft")
        assert completed.status is RunStatus.SUCCEEDED
        assert completed.stage_statuses == {"agent03": "SUCCEEDED"}
        assert backend.submits == 1
        assert backend.reconciles == 1
        assert runtime.resume(run_id="run-dft") == completed
        assert backend.reconciles == 1


def test_external_stage_cancel_calls_backend_once(
    tmp_path, requirement, fixture_payload
) -> None:
    project_id = "project-external-cancel"
    requirement_row, manifest = _complete_source_run(
        tmp_path,
        requirement,
        fixture_payload,
        project_id=project_id,
        run_id="run-source",
    )
    backend = _Backend()
    registry = _external_registry(tmp_path / project_id, backend)
    with OrchestratorRuntime.from_workspace(
        tmp_path, project_id, runner_registry=registry
    ) as runtime:
        approval_view = runtime.start_stage_run(
            stage=StageId.DFT,
            stage_input=_stage_input(
                "run-source", requirement_row, manifest
            ),
            run_id="run-dft-cancel",
        )
        waiting = runtime.approve(
            run_id="run-dft-cancel",
            approval_id=approval_view.interrupts[0].value["approval_id"],
            decision="approve",
        )
        assert waiting.status is RunStatus.PAUSED
        cancelled = runtime.cancel(run_id="run-dft-cancel")
        replayed = runtime.cancel(run_id="run-dft-cancel")

    assert cancelled.status is RunStatus.CANCELLED
    assert replayed == cancelled
    assert cancelled.stage_statuses == {"agent03": "CANCELLED"}
    assert backend.submits == 1
    assert backend.cancels == 1


def test_rejected_expensive_gate_never_submits(
    tmp_path, requirement, fixture_payload
) -> None:
    project_id = "project-external-rejected"
    requirement_row, manifest = _complete_source_run(
        tmp_path,
        requirement,
        fixture_payload,
        project_id=project_id,
        run_id="run-source",
    )
    backend = _Backend()
    registry = _external_registry(tmp_path / project_id, backend)
    with OrchestratorRuntime.from_workspace(
        tmp_path, project_id, runner_registry=registry
    ) as runtime:
        approval_view = runtime.start_stage_run(
            stage=StageId.DFT,
            stage_input=_stage_input(
                "run-source", requirement_row, manifest
            ),
            run_id="run-dft-rejected",
        )
        rejected = runtime.approve(
            run_id="run-dft-rejected",
            approval_id=approval_view.interrupts[0].value["approval_id"],
            decision="reject",
            reason="budget denied",
        )

    assert rejected.status is RunStatus.CANCELLED
    assert rejected.stage_statuses == {"agent03": "CANCELLED"}
    assert backend.submits == 0


def test_external_running_reconcile_remains_paused_before_completion(
    tmp_path, requirement, fixture_payload
) -> None:
    project_id = "project-external-running"
    requirement_row, manifest = _complete_source_run(
        tmp_path,
        requirement,
        fixture_payload,
        project_id=project_id,
        run_id="run-source",
    )
    backend = _Backend(remaining_running=1)
    registry = _external_registry(tmp_path / project_id, backend)
    with OrchestratorRuntime.from_workspace(
        tmp_path, project_id, runner_registry=registry
    ) as runtime:
        approval_view = runtime.start_stage_run(
            stage=StageId.DFT,
            stage_input=_stage_input(
                "run-source", requirement_row, manifest
            ),
            run_id="run-dft-running",
        )
        runtime.approve(
            run_id="run-dft-running",
            approval_id=approval_view.interrupts[0].value["approval_id"],
            decision="approve",
        )
        still_running = runtime.resume(run_id="run-dft-running")
        completed = runtime.resume(run_id="run-dft-running")

    assert still_running.status is RunStatus.PAUSED
    assert still_running.stage_statuses == {"agent03": "RUNNING"}
    assert completed.status is RunStatus.SUCCEEDED
    assert backend.submits == 1
    assert backend.reconciles == 2


@pytest.mark.parametrize(
    "terminal_status",
    [ExternalJobStatus.FAILED, ExternalJobStatus.TIMEOUT],
)
def test_external_failure_and_timeout_are_terminal_without_resubmit(
    tmp_path, requirement, fixture_payload, terminal_status
) -> None:
    project_id = f"project-external-{terminal_status.value.lower()}"
    requirement_row, manifest = _complete_source_run(
        tmp_path,
        requirement,
        fixture_payload,
        project_id=project_id,
        run_id="run-source",
    )
    backend = _Backend(terminal_status=terminal_status)
    registry = _external_registry(tmp_path / project_id, backend)
    with OrchestratorRuntime.from_workspace(
        tmp_path, project_id, runner_registry=registry
    ) as runtime:
        approval_view = runtime.start_stage_run(
            stage=StageId.DFT,
            stage_input=_stage_input(
                "run-source", requirement_row, manifest
            ),
            run_id="run-dft-terminal",
        )
        runtime.approve(
            run_id="run-dft-terminal",
            approval_id=approval_view.interrupts[0].value["approval_id"],
            decision="approve",
        )
        terminal = runtime.resume(run_id="run-dft-terminal")
        external = runtime.repository.get_external_job(
            "run-dft-terminal", StageId.DFT
        )

    assert terminal.status is RunStatus.FAILED
    assert terminal.stage_statuses == {"agent03": "PERMANENT_FAILED"}
    assert external["status"] == terminal_status.value
    assert backend.submits == 1
    assert backend.reconciles == 1


def test_external_identity_change_fails_closed(
    tmp_path, requirement, fixture_payload
) -> None:
    project_id = "project-external-inconsistent"
    requirement_row, manifest = _complete_source_run(
        tmp_path,
        requirement,
        fixture_payload,
        project_id=project_id,
        run_id="run-source",
    )
    backend = _Backend(inconsistent_ref=True)
    registry = _external_registry(tmp_path / project_id, backend)
    with OrchestratorRuntime.from_workspace(
        tmp_path, project_id, runner_registry=registry
    ) as runtime:
        approval_view = runtime.start_stage_run(
            stage=StageId.DFT,
            stage_input=_stage_input(
                "run-source", requirement_row, manifest
            ),
            run_id="run-dft-inconsistent",
        )
        runtime.approve(
            run_id="run-dft-inconsistent",
            approval_id=approval_view.interrupts[0].value["approval_id"],
            decision="approve",
        )
        failed = runtime.resume(run_id="run-dft-inconsistent")
        report = runtime.store.read_json(
            "artifact://reports/run-dft-inconsistent/report.json"
        )

    assert failed.status is RunStatus.FAILED
    assert failed.stage_statuses == {"agent03": "PERMANENT_FAILED"}
    assert report["stages"]["agent03"]["errors"][0]["category"] == (
        "BACKEND_INCONSISTENT"
    )


def test_legacy_unfinished_checkpoint_is_readable_but_not_resumable(
    tmp_path,
) -> None:
    project_id = "project-legacy-checkpoint"
    OrchestratorRuntime.create_project(tmp_path, project_id)
    with OrchestratorRuntime.from_workspace(
        tmp_path, project_id
    ) as runtime:
        runtime.repository.create_run(
            run_id="run-legacy",
            project_id=project_id,
            raw_request="legacy",
            status=RunStatus.PAUSED,
            checkpoint_schema_version=(
                LEGACY_ORCHESTRATOR_CONTRACT_VERSION
            ),
        )
        local = runtime.status("run-legacy")
        assert local.status is RunStatus.PAUSED
        assert "read-only" in local.warnings[0]
        with pytest.raises(
            CheckpointCompatibilityError, match="cannot be resumed"
        ):
            runtime.resume(run_id="run-legacy")


def test_project_lock_rejects_concurrent_advancement(
    tmp_path, requirement
) -> None:
    import fcntl

    project_id = "project-lock"
    OrchestratorRuntime.create_project(tmp_path, project_id)
    with OrchestratorRuntime.from_workspace(
        tmp_path, project_id
    ) as runtime:
        lock_path = runtime.project_root / "state" / "locks" / "project.lock"
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        with lock_path.open("a+b") as handle:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            with pytest.raises(RuntimeError, match="project .* advanced"):
                runtime.start_run(
                    raw_request="structured",
                    initial_requirement=requirement.model_dump(mode="json"),
                    run_id="run-locked",
                )
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        assert runtime.repository.get_run("run-locked") is None


def test_interaction_and_approval_replays_are_idempotent(
    tmp_path, requirement, fixture_payload
) -> None:
    project_id = "project-idempotent"
    OrchestratorRuntime.create_project(tmp_path, project_id)
    with OrchestratorRuntime.from_workspace(
        tmp_path, project_id
    ) as runtime:
        clarifying = runtime.start_run(
            raw_request="帮我找材料",
            fixture_payload=fixture_payload,
            run_id="run-idempotent",
        )
        interaction = clarifying.interrupts[0]
        response = {
            "requirement": requirement.model_dump(mode="json"),
        }
        reviewing = runtime.respond(
            run_id="run-idempotent",
            interaction_id=interaction.interaction_id,
            response=response,
        )
        replayed = runtime.respond(
            run_id="run-idempotent",
            interaction_id=interaction.interaction_id,
            response=response,
        )
        assert replayed == reviewing
        with pytest.raises(
            RepositoryConflictError,
            match="interaction was already answered differently",
        ):
            runtime.respond(
                run_id="run-idempotent",
                interaction_id=interaction.interaction_id,
                response={"decision": "cancel"},
            )

        approval_id = reviewing.interrupts[0].value["approval_id"]
        completed = runtime.approve(
            run_id="run-idempotent",
            approval_id=approval_id,
            decision="approve",
            reason="confirmed",
        )
        assert (
            runtime.approve(
                run_id="run-idempotent",
                approval_id=approval_id,
                decision="approve",
                reason="confirmed",
            )
            == completed
        )
        with pytest.raises(
            ValueError, match="different details"
        ):
            runtime.approve(
                run_id="run-idempotent",
                approval_id=approval_id,
                decision="approve",
                reason="changed",
            )


def test_approval_rejects_tampered_input_snapshot(
    tmp_path, requirement
) -> None:
    project_id = "project-approval-integrity"
    OrchestratorRuntime.create_project(tmp_path, project_id)
    with OrchestratorRuntime.from_workspace(
        tmp_path, project_id
    ) as runtime:
        waiting = runtime.start_run(
            raw_request="structured",
            initial_requirement=requirement.model_dump(mode="json"),
            run_id="run-approval-integrity",
        )
        approval_id = waiting.interrupts[0].value["approval_id"]
        approval = runtime.repository.get_approval(approval_id)
        snapshot_uri = approval["payload"]["payload"]["input_snapshot_uri"]
        snapshot_path = runtime.project_root / snapshot_uri.removeprefix(
            "artifact://"
        )
        snapshot_path.write_text("{}", encoding="utf-8")

        with pytest.raises(ValueError, match="snapshot failed integrity"):
            runtime.approve(
                run_id="run-approval-integrity",
                approval_id=approval_id,
                decision="approve",
            )


def test_fixed_clock_and_ids_freeze_report_and_scientific_artifacts(
    tmp_path, requirement, fixture_payload
) -> None:
    fixed = FixedClock(datetime(2026, 7, 26, tzinfo=UTC))
    outputs: list[tuple[bytes, bytes]] = []
    for workspace_name in ("workspace-a", "workspace-b"):
        workspace = tmp_path / workspace_name
        ids = DeterministicIdFactory("same-seed")
        OrchestratorRuntime.create_project(
            workspace,
            "project-deterministic",
            clock=fixed,
            id_factory=ids,
        )
        with OrchestratorRuntime.from_workspace(
            workspace,
            "project-deterministic",
            clock=fixed,
            id_factory=ids,
        ) as runtime:
            waiting = runtime.start_run(
                raw_request="structured",
                initial_requirement=requirement.model_dump(mode="json"),
                fixture_payload=fixture_payload,
                run_id="run-deterministic",
            )
            runtime.approve(
                run_id="run-deterministic",
                approval_id=waiting.interrupts[0].value["approval_id"],
                decision="approve",
            )
            outputs.append(
                (
                    runtime.store.read_bytes(
                        "artifact://reports/run-deterministic/report.json"
                    ),
                    runtime.store.read_bytes(
                        "artifact://stages/agent01/run-deterministic/"
                        "candidate_manifest.jsonl"
                    ),
                )
            )

    assert outputs[0] == outputs[1]
