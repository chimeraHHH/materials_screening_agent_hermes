"""Public service API for creating, advancing, and inspecting workflows."""

from __future__ import annotations

import re
import sqlite3
from contextlib import contextmanager
from copy import deepcopy
from pathlib import Path
from typing import Any

from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.types import Command

from material_agent.orchestrator.graph import OrchestratorGraph
from material_agent.orchestrator.identity import (
    Clock,
    IdFactory,
    SystemClock,
    UUIDIdFactory,
)
from material_agent.orchestrator.models import (
    ApprovalStatus,
    ControlOutcomeType,
    ControlStageOutcome,
    LEGACY_ORCHESTRATOR_CONTRACT_VERSION,
    ORCHESTRATOR_CONTRACT_VERSION,
    P01_ORCHESTRATOR_CONTRACT_VERSION,
    RuntimeInterrupt,
    RuntimeView,
    RunStatus,
    StageId,
    StageStartInput,
    StageStatus,
)
from material_agent.orchestrator.parser import (
    RequirementParser,
    requirement_parser_from_environment,
)
from material_agent.orchestrator.runners import (
    StageRunnerRegistry,
    configure_agent02_production,
)
from material_agent.orchestrator.storage import OrchestratorRepository
from material_agent.retrieval.storage import LocalArtifactStore
from material_agent.retrieval.models import SourceDatabase


_SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
_TERMINAL_RUN_STATUSES = {
    RunStatus.SUCCEEDED,
    RunStatus.PARTIAL,
    RunStatus.FAILED,
    RunStatus.CANCELLED,
}


class CheckpointCompatibilityError(RuntimeError):
    """Raised when an unfinished legacy checkpoint cannot be resumed safely."""


class OrchestratorRuntime:
    """Own one project's graph, checkpointer, business DB, and artifacts."""

    def __init__(
        self,
        project_root: Path | str,
        *,
        parser: RequirementParser | None = None,
        runner_registry: StageRunnerRegistry | None = None,
        clock: Clock | None = None,
        id_factory: IdFactory | None = None,
    ) -> None:
        self.clock = clock or SystemClock()
        self.id_factory = id_factory or UUIDIdFactory()
        self.project_root = Path(project_root).resolve()
        self.store = LocalArtifactStore(self.project_root)
        if not self.store.exists("project.json"):
            raise FileNotFoundError(
                f"project.json does not exist below {self.project_root}"
            )
        project = self.store.read_json("project.json")
        self.project_id = _validate_id(project["project_id"], "project_id")
        self.database_path = self.project_root / "state" / "orchestrator.sqlite3"
        self.repository = OrchestratorRepository(self.database_path)
        self.repository.create_project(
            self.project_id, str(self.project_root)
        )
        self._checkpoint_connection = sqlite3.connect(
            self.database_path, check_same_thread=False
        )
        serializer = JsonPlusSerializer(
            pickle_fallback=False,
            allowed_msgpack_modules=(),
        )
        self.checkpointer = SqliteSaver(
            self._checkpoint_connection, serde=serializer
        )
        selected_registry = runner_registry or StageRunnerRegistry()
        if runner_registry is None:
            configure_agent02_production(
                selected_registry, project_root=self.project_root
            )
        selected_parser = parser or requirement_parser_from_environment()
        self.orchestrator = OrchestratorGraph(
            repository=self.repository,
            artifact_store=self.store,
            parser=selected_parser,
            runner_registry=selected_registry,
            clock=self.clock,
        )
        self.graph = self.orchestrator.build().compile(
            checkpointer=self.checkpointer,
            name="material-screening-orchestrator-p0.2",
        )

    def __enter__(self) -> OrchestratorRuntime:
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()

    def close(self) -> None:
        self.repository.close()
        self._checkpoint_connection.close()

    @classmethod
    def create_project(
        cls,
        workspace_root: Path | str,
        project_id: str | None = None,
        *,
        clock: Clock | None = None,
        id_factory: IdFactory | None = None,
    ) -> dict[str, Any]:
        active_clock = clock or SystemClock()
        active_ids = id_factory or UUIDIdFactory()
        workspace = Path(workspace_root).resolve()
        selected_id = _validate_id(
            project_id or active_ids.new_id("project"),
            "project_id",
        )
        project_root = (workspace / selected_id).resolve()
        if workspace not in project_root.parents:
            raise ValueError("project path escapes workspace root")
        store = LocalArtifactStore(project_root)
        if store.exists("project.json"):
            project = store.read_json("project.json")
            if project.get("project_id") != selected_id:
                raise ValueError("existing project.json has a different ID")
        else:
            project = {
                "schema_version": ORCHESTRATOR_CONTRACT_VERSION,
                "project_id": selected_id,
                "created_at": active_clock.now().isoformat(),
            }
            store.write_json("project.json", project, immutable=True)
        repository = OrchestratorRepository(
            project_root / "state" / "orchestrator.sqlite3"
        )
        try:
            repository.create_project(selected_id, str(project_root))
        finally:
            repository.close()
        return {**project, "project_root": str(project_root)}

    @classmethod
    def from_workspace(
        cls,
        workspace_root: Path | str,
        project_id: str,
        *,
        parser: RequirementParser | None = None,
        runner_registry: StageRunnerRegistry | None = None,
        clock: Clock | None = None,
        id_factory: IdFactory | None = None,
    ) -> OrchestratorRuntime:
        selected_id = _validate_id(project_id, "project_id")
        workspace = Path(workspace_root).resolve()
        project_root = (workspace / selected_id).resolve()
        if workspace not in project_root.parents:
            raise ValueError("project path escapes workspace root")
        return cls(
            project_root,
            parser=parser,
            runner_registry=runner_registry,
            clock=clock,
            id_factory=id_factory,
        )

    def start_run(
        self,
        *,
        raw_request: str,
        initial_requirement: dict[str, Any] | None = None,
        fixture_payload: dict[str, Any] | None = None,
        run_id: str | None = None,
        retrieval_source: SourceDatabase | str = SourceDatabase.MATERIALS_PROJECT,
    ) -> RuntimeView:
        selected_source = SourceDatabase(retrieval_source)
        selected_run_id = _validate_id(
            run_id or self.id_factory.new_id("run"), "run_id"
        )
        if not raw_request.strip() and initial_requirement is None:
            raise ValueError("raw_request or initial_requirement is required")
        if self.repository.get_run(selected_run_id) is not None:
            raise ValueError(f"run already exists: {selected_run_id}")
        created_at = self.clock.now().isoformat()
        with self._project_lock():
            self.repository.create_run(
                run_id=selected_run_id,
                project_id=self.project_id,
                raw_request=raw_request or "structured Requirement input",
            )
            fixture_uri = None
            if fixture_payload is not None:
                fixture_ref = self.store.write_json(
                    f"inputs/{selected_run_id}/materials_fixture.json",
                    fixture_payload,
                    immutable=True,
                )
                fixture_uri = fixture_ref.uri
            initial_state = self._initial_state(
                run_id=selected_run_id,
                raw_request=raw_request or "structured Requirement input",
                initial_requirement=initial_requirement,
                created_at=created_at,
                fixture_uri=fixture_uri,
                retrieval_source=selected_source,
            )
            self.graph.invoke(
                initial_state,
                config=self._config(selected_run_id),
                durability="sync",
            )
        return self.status(selected_run_id)

    def start_stage_run(
        self,
        *,
        stage: StageId | str,
        stage_input: dict[str, Any],
        run_id: str | None = None,
    ) -> RuntimeView:
        selected_stage = StageId(stage)
        parsed_input = StageStartInput.model_validate(stage_input)
        selected_run_id = _validate_id(
            run_id or self.id_factory.new_id("run"), "run_id"
        )
        _validate_id(parsed_input.source_run_id, "source_run_id")
        if self.repository.get_run(selected_run_id) is not None:
            raise ValueError(f"run already exists: {selected_run_id}")
        source = self.repository.get_run(parsed_input.source_run_id)
        if source is None:
            raise KeyError(f"unknown source run: {parsed_input.source_run_id}")
        requirement = self.repository.get_requirement(
            parsed_input.source_run_id,
            parsed_input.requirement_revision,
        )
        if requirement is None:
            raise ValueError(
                "source run does not own the requested Requirement revision"
            )
        expected = (
            requirement["artifact_uri"],
            requirement["artifact_sha256"],
        )
        supplied = (
            parsed_input.requirement_artifact_uri,
            parsed_input.requirement_artifact_sha256,
        )
        if supplied != expected:
            raise ValueError(
                "stage input Requirement reference differs from source run"
            )
        if not self.store.exists_with_hash(*supplied):
            raise ValueError("stage input Requirement artifact failed integrity check")
        for name, pointer in parsed_input.artifacts.items():
            if not self.store.exists_with_hash(pointer.uri, pointer.sha256):
                raise ValueError(
                    f"stage input artifact failed integrity check: {name}"
                )
        created_at = self.clock.now().isoformat()
        raw_request = (
            f"explicit run-stage {selected_stage.value} "
            f"from {parsed_input.source_run_id}"
        )
        with self._project_lock():
            self.repository.create_run(
                run_id=selected_run_id,
                project_id=self.project_id,
                raw_request=raw_request,
                run_mode="direct-stage",
            )
            initial_state = self._initial_state(
                run_id=selected_run_id,
                raw_request=raw_request,
                initial_requirement=None,
                created_at=created_at,
                fixture_uri=None,
                retrieval_source=SourceDatabase.MATERIALS_PROJECT,
            )
            initial_state.update(
                {
                    "run_mode": "direct-stage",
                    "direct_stage": selected_stage.value,
                    "direct_stage_input": parsed_input.model_dump(mode="json"),
                }
            )
            self.graph.invoke(
                initial_state,
                config=self._config(selected_run_id),
                durability="sync",
            )
        return self.status(selected_run_id)

    def status(self, run_id: str) -> RuntimeView:
        """Read local durable state only; never contact a stage backend."""

        selected_run_id = _validate_id(run_id, "run_id")
        row = self.repository.get_run(selected_run_id)
        if row is None:
            raise KeyError(f"unknown run: {selected_run_id}")
        if row["checkpoint_schema_version"] != ORCHESTRATOR_CONTRACT_VERSION:
            return RuntimeView(
                project_id=self.project_id,
                run_id=selected_run_id,
                status=RunStatus(row["status"]),
                current_stage=row["current_stage"],
                requirement_revision=row["requirement_revision"],
                report_uri=row["report_uri"],
                stage_statuses=row["stage_statuses"],
                warnings=[
                    "Legacy checkpoint is read-only under Orchestrator P0.2."
                ],
            )
        snapshot = self.graph.get_state(self._config(selected_run_id))
        values = snapshot.values or {}
        interruptions = [
            RuntimeInterrupt(
                interaction_id=(
                    interruption.value.get("interaction_id", interruption.id)
                    if isinstance(interruption.value, dict)
                    else interruption.id
                ),
                value=(
                    interruption.value
                    if isinstance(interruption.value, dict)
                    else {"value": interruption.value}
                ),
            )
            for interruption in snapshot.interrupts
        ]
        return RuntimeView(
            project_id=self.project_id,
            run_id=selected_run_id,
            status=RunStatus(row["status"]),
            current_stage=row["current_stage"],
            requirement_revision=row["requirement_revision"],
            report_uri=row["report_uri"],
            interrupts=interruptions,
            stage_statuses=row["stage_statuses"],
            candidate_ids=list(values.get("candidate_ids", [])),
            warnings=list(values.get("warnings", [])),
            errors=list(values.get("errors", [])),
        )

    def respond(
        self,
        *,
        run_id: str,
        interaction_id: str,
        response: dict[str, Any],
    ) -> RuntimeView:
        selected_run_id = _validate_id(run_id, "run_id")
        self._assert_checkpoint_compatible(selected_run_id)
        payload = deepcopy(response)
        payload["interaction_id"] = interaction_id
        existing_response = self.repository.get_interaction_response(
            interaction_id
        )
        if existing_response is not None:
            self.repository.record_interaction_response(
                interaction_id=interaction_id,
                run_id=selected_run_id,
                response=payload,
            )
            return self.status(selected_run_id)
        graph_interrupt = self._find_interrupt(
            selected_run_id, interaction_id
        )
        payload["interaction_id"] = graph_interrupt.value["interaction_id"]
        with self._project_lock():
            self.graph.invoke(
                Command(resume=payload),
                config=self._config(selected_run_id),
                durability="sync",
            )
            self.repository.record_interaction_response(
                interaction_id=interaction_id,
                run_id=selected_run_id,
                response=payload,
            )
        return self.status(selected_run_id)

    def approve(
        self,
        *,
        run_id: str,
        approval_id: str,
        decision: str,
        reason: str | None = None,
    ) -> RuntimeView:
        selected_run_id = _validate_id(run_id, "run_id")
        self._assert_checkpoint_compatible(selected_run_id)
        approval = self.repository.get_approval(approval_id)
        if approval is None or approval["run_id"] != selected_run_id:
            raise KeyError(f"unknown approval for run: {approval_id}")
        snapshot = approval["payload"].get("payload", {})
        snapshot_uri = snapshot.get("stage_plan_uri") or snapshot.get(
            "input_snapshot_uri"
        )
        snapshot_sha256 = snapshot.get("stage_plan_sha256") or snapshot.get(
            "input_snapshot_sha256"
        )
        if (
            snapshot_sha256 != approval["input_sha256"]
            or not snapshot_uri
            or not self.store.exists_with_hash(snapshot_uri, snapshot_sha256)
        ):
            raise ValueError("approval input snapshot failed integrity check")
        normalized = decision.lower()
        if normalized not in {"approve", "reject"}:
            raise ValueError("decision must be approve or reject")
        desired = (
            ApprovalStatus.APPROVED
            if normalized == "approve"
            else (
                ApprovalStatus.REJECTED
                if approval["gate_type"] == "EXPENSIVE_BATCH_APPROVAL"
                else ApprovalStatus.CANCELLED
            )
        )
        if approval["status"] != ApprovalStatus.PENDING.value:
            if approval["status"] == desired.value:
                actual = approval.get("decision") or {}
                expected_decision = (
                    normalized
                    if approval["gate_type"] == "EXPENSIVE_BATCH_APPROVAL"
                    else ("approve" if normalized == "approve" else "cancel")
                )
                if (
                    actual.get("decision") != expected_decision
                    or actual.get("reason") != reason
                ):
                    raise ValueError(
                        "approval was already decided with different details"
                    )
                return self.status(selected_run_id)
            raise ValueError(f"approval is already {approval['status']}")
        graph_decision = (
            normalized
            if approval["gate_type"] == "EXPENSIVE_BATCH_APPROVAL"
            else ("approve" if normalized == "approve" else "cancel")
        )
        return self.respond(
            run_id=selected_run_id,
            interaction_id=approval["interaction_id"],
            response={
                "approval_id": approval_id,
                "decision": graph_decision,
                "reason": reason,
            },
        )

    def retry(self, *, run_id: str) -> RuntimeView:
        view = self.status(run_id)
        pending = _single_pending(view)
        if pending.value.get("interaction_type") != "RETRY_CONFIRMATION":
            raise ValueError("run is not waiting for retry confirmation")
        return self.respond(
            run_id=run_id,
            interaction_id=pending.interaction_id,
            response={"decision": "retry"},
        )

    def cancel(self, *, run_id: str, reason: str | None = None) -> RuntimeView:
        selected_run_id = _validate_id(run_id, "run_id")
        self._assert_checkpoint_compatible(selected_run_id)
        current_view = self.status(selected_run_id)
        if current_view.status in _TERMINAL_RUN_STATUSES and (
            current_view.status is RunStatus.CANCELLED
            or StageStatus.CANCELLED.value
            in current_view.stage_statuses.values()
        ):
            return current_view
        snapshot = self.graph.get_state(self._config(selected_run_id))
        values = snapshot.values or {}
        if self._is_waiting_external(values):
            with self._project_lock():
                updates = self.orchestrator.cancel_current_external_stage(values)
                self.graph.update_state(
                    self._config(selected_run_id),
                    updates,
                    as_node="execute_or_reconcile_stage",
                )
                self.graph.invoke(
                    None,
                    config=self._config(selected_run_id),
                    durability="sync",
                )
            return self.status(selected_run_id)
        view = self.status(selected_run_id)
        pending = _single_pending(view)
        interaction_type = pending.value.get("interaction_type")
        if interaction_type in {
            "REQUIREMENT_CONFIRMATION",
            "EXPENSIVE_BATCH_APPROVAL",
        }:
            approval_id = pending.value.get("approval_id")
            if not approval_id:
                raise ValueError("pending approval has no approval_id")
            return self.approve(
                run_id=selected_run_id,
                approval_id=approval_id,
                decision="reject",
                reason=reason,
            )
        return self.respond(
            run_id=selected_run_id,
            interaction_id=pending.interaction_id,
            response={"decision": "cancel", "reason": reason},
        )

    def resume(self, *, run_id: str) -> RuntimeView:
        selected_run_id = _validate_id(run_id, "run_id")
        self._assert_checkpoint_compatible(selected_run_id)
        snapshot = self.graph.get_state(self._config(selected_run_id))
        if snapshot.interrupts:
            raise ValueError(
                "run is waiting for user input; use respond, approve, or retry"
            )
        values = snapshot.values or {}
        with self._project_lock():
            if self._is_waiting_external(values):
                updates = self.orchestrator.reconcile_current_stage(values)
                self.graph.update_state(
                    self._config(selected_run_id),
                    updates,
                    as_node="execute_or_reconcile_stage",
                )
                self.graph.invoke(
                    None,
                    config=self._config(selected_run_id),
                    durability="sync",
                )
            elif snapshot.next:
                self.graph.invoke(
                    None,
                    config=self._config(selected_run_id),
                    durability="sync",
                )
        return self.status(selected_run_id)

    def read_report(self, run_id: str) -> str:
        view = self.status(run_id)
        if view.report_uri is None:
            raise ValueError("run has no report yet")
        return self.store.read_bytes(view.report_uri).decode("utf-8")

    def _initial_state(
        self,
        *,
        run_id: str,
        raw_request: str,
        initial_requirement: dict[str, Any] | None,
        created_at: str,
        fixture_uri: str | None,
        retrieval_source: SourceDatabase,
    ) -> dict[str, Any]:
        return {
            "schema_version": ORCHESTRATOR_CONTRACT_VERSION,
            "project_id": self.project_id,
            "run_id": run_id,
            "thread_id": run_id,
            "run_mode": "request",
            "direct_stage": None,
            "direct_stage_input": None,
            "raw_request": raw_request,
            "requirement_id": self.id_factory.new_id("req"),
            "initial_requirement": deepcopy(initial_requirement),
            "run_status": RunStatus.INTAKE.value,
            "current_stage": "requirement",
            "stage_statuses": {},
            "stage_outcomes": {},
            "stage_plan_refs": {},
            "prepared_stage_plan": None,
            "prepared_stage_plan_uri": None,
            "prepared_stage_plan_sha256": None,
            "stage_approval_required": None,
            "candidate_ids": [],
            "pending_interaction": None,
            "retrieval_fixture_uri": fixture_uri,
            "retrieval_source": retrieval_source.value,
            "retry_counters": {},
            "warnings": [],
            "errors": [],
            "created_at": created_at,
            "updated_at": created_at,
        }

    def _find_interrupt(self, run_id: str, interaction_id: str):
        snapshot = self.graph.get_state(self._config(run_id))
        for graph_interrupt in snapshot.interrupts:
            if not isinstance(graph_interrupt.value, dict):
                continue
            if graph_interrupt.value.get("interaction_id") == interaction_id:
                return graph_interrupt
        raise KeyError(f"no pending interaction: {interaction_id}")

    def _assert_checkpoint_compatible(self, run_id: str) -> None:
        row = self.repository.get_run(run_id)
        if row is None:
            raise KeyError(f"unknown run: {run_id}")
        version = row["checkpoint_schema_version"]
        if version == ORCHESTRATOR_CONTRACT_VERSION:
            return
        if RunStatus(row["status"]) in _TERMINAL_RUN_STATUSES:
            raise CheckpointCompatibilityError(
                "legacy completed runs are read-only; start a new P0.2 run"
            )
        known_version = version or LEGACY_ORCHESTRATOR_CONTRACT_VERSION
        if known_version == P01_ORCHESTRATOR_CONTRACT_VERSION:
            label = "unfinished orchestrator-p0.1-v2"
        else:
            label = f"unfinished {known_version}"
        raise CheckpointCompatibilityError(
            f"{label} checkpoint cannot be resumed by Orchestrator P0.2; preserve its "
            "artifacts and start a new run"
        )

    @staticmethod
    def _is_waiting_external(values: dict[str, Any]) -> bool:
        current = values.get("current_route")
        if not isinstance(current, dict):
            return False
        agent_id = current.get("agent_id")
        payload = values.get("stage_outcomes", {}).get(agent_id)
        if not isinstance(payload, dict):
            return False
        try:
            outcome = ControlStageOutcome.model_validate(payload)
        except ValueError:
            return False
        return outcome.outcome is ControlOutcomeType.WAITING_EXTERNAL

    @contextmanager
    def _project_lock(self):
        import fcntl

        lock_path = self.project_root / "state" / "locks" / "project.lock"
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        with lock_path.open("a+b") as handle:
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise RuntimeError(
                    f"project {self.project_id} is already being advanced "
                    "by another process"
                ) from exc
            try:
                yield
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    @staticmethod
    def _config(run_id: str) -> dict[str, dict[str, str]]:
        return {"configurable": {"thread_id": run_id}}


def _validate_id(value: str, label: str) -> str:
    if not _SAFE_ID.fullmatch(value):
        raise ValueError(
            f"{label} must contain only letters, digits, '.', '_' or '-' "
            "and be at most 64 characters"
        )
    return value


def _single_pending(view: RuntimeView) -> RuntimeInterrupt:
    if len(view.interrupts) != 1:
        raise ValueError("run does not have exactly one pending interaction")
    return view.interrupts[0]
