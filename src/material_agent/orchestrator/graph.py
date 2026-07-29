"""LangGraph control plane for the four-stage material-screening workflow."""

from __future__ import annotations

import hashlib
from copy import deepcopy
from datetime import datetime
from typing import Any

from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt

from material_agent.orchestrator.identity import Clock, SystemClock
from material_agent.orchestrator.models import (
    ApprovalStatus,
    ArtifactPointer,
    ControlError,
    ControlOutcomeType,
    ControlStageOutcome,
    ExecutionPlan,
    ExternalJobRecord,
    InteractionType,
    ORCHESTRATOR_REPORT_VERSION,
    OrchestratorState,
    PendingInteraction,
    PreparedStagePlan,
    RunStatus,
    StageDisposition,
    StageExecutionContext,
    StageExecutionRecord,
    StageId,
    StageInputValidation,
    StageRoute,
    StageStatus,
    STAGE_TO_AGENT,
    effective_stage_approval,
)
from material_agent.orchestrator.parser import (
    OfflineRequirementParser,
    RequirementParser,
)
from material_agent.orchestrator.runners import (
    Agent01RunnerAdapter,
    StageRunner,
    StageRunnerRegistry,
    runner_exception_outcome,
)
from material_agent.orchestrator.storage import (
    OrchestratorRepository,
    RepositoryConflictError,
)
from material_agent.retrieval.adapters import (
    InMemoryMaterialsAdapter,
    MaterialsProjectAdapter,
    NomadAdapter,
)
from material_agent.retrieval.models import (
    EvidenceLevel,
    Requirement,
    RetrievalPolicy,
    SourceDatabase,
)
from material_agent.retrieval.query import retrieval_policy_for_source
from material_agent.retrieval.runner import RetrievalStageRunner
from material_agent.retrieval.storage import (
    LocalArtifactStore,
    canonical_json_bytes,
)


ROUTING_POLICY_VERSION = "orchestrator-routing-policy-v2"
_EVIDENCE_ORDER = {
    EvidenceLevel.L0_PARSED: 0,
    EvidenceLevel.L1_RETRIEVED: 1,
    EvidenceLevel.L2_ML_SCREENED: 2,
    EvidenceLevel.L3_DFT_VALIDATED: 3,
    EvidenceLevel.L4_MANY_BODY_VALIDATED: 4,
    EvidenceLevel.L5_EXPERT_REVIEWED: 5,
}
_STAGE_EVIDENCE = {
    StageId.RETRIEVAL: 1,
    StageId.ML: 2,
    StageId.DFT: 3,
    StageId.MANY_BODY: 4,
}


class OrchestratorGraph:
    """Build generic graph nodes around repository and Artifact Store services."""

    def __init__(
        self,
        *,
        repository: OrchestratorRepository,
        artifact_store: LocalArtifactStore,
        parser: RequirementParser | None = None,
        runner_registry: StageRunnerRegistry | None = None,
        clock: Clock | None = None,
    ) -> None:
        self.repository = repository
        self.store = artifact_store
        self.parser = parser or OfflineRequirementParser()
        self.runners = runner_registry or StageRunnerRegistry()
        self.clock = clock or SystemClock()

    def _now(self) -> str:
        return self.clock.now().isoformat()

    def build(self) -> StateGraph:
        graph = StateGraph(OrchestratorState)
        graph.add_node("select_entry", self.select_entry)
        graph.add_node("parse_requirement", self.parse_requirement)
        graph.add_node("clarification_gate", self.clarification_gate)
        graph.add_node(
            "prepare_requirement_review", self.prepare_requirement_review
        )
        graph.add_node("requirement_review_gate", self.requirement_review_gate)
        graph.add_node("cancel_run", self.cancel_run)
        graph.add_node("freeze_requirement", self.freeze_requirement)
        graph.add_node("initialize_direct_run", self.initialize_direct_run)
        graph.add_node("build_execution_plan", self.build_execution_plan)
        graph.add_node("route_next_stage", self.route_next_stage)
        graph.add_node("validate_stage_input", self.validate_stage_input)
        graph.add_node("prepare_stage_plan", self.prepare_stage_plan)
        graph.add_node(
            "decide_stage_approval", self.decide_stage_approval
        )
        graph.add_node("mark_stage_ready", self.mark_stage_ready)
        graph.add_node(
            "prepare_stage_approval", self.prepare_stage_approval
        )
        graph.add_node("stage_approval_gate", self.stage_approval_gate)
        graph.add_node(
            "execute_or_reconcile_stage", self.execute_or_reconcile_stage
        )
        graph.add_node("validate_stage_result", self.validate_stage_result)
        graph.add_node("record_stage_outcome", self.record_stage_outcome)
        graph.add_node("prepare_retry", self.prepare_retry)
        graph.add_node("retry_gate", self.retry_gate)
        graph.add_node("generate_report", self.generate_report)

        graph.add_edge(START, "select_entry")
        graph.add_conditional_edges(
            "select_entry",
            self.route_entry,
            {
                "request": "parse_requirement",
                "direct": "initialize_direct_run",
            },
        )
        graph.add_conditional_edges(
            "parse_requirement",
            self.route_after_parse,
            {
                "clarify": "clarification_gate",
                "review": "prepare_requirement_review",
            },
        )
        graph.add_conditional_edges(
            "clarification_gate",
            self.route_after_clarification,
            {
                "continue": "prepare_requirement_review",
                "cancel": "cancel_run",
            },
        )
        graph.add_edge("prepare_requirement_review", "requirement_review_gate")
        graph.add_conditional_edges(
            "requirement_review_gate",
            self.route_after_review,
            {
                "approve": "freeze_requirement",
                "revise": "prepare_requirement_review",
                "cancel": "cancel_run",
            },
        )
        graph.add_edge("cancel_run", END)
        graph.add_edge("freeze_requirement", "build_execution_plan")
        graph.add_edge("initialize_direct_run", "build_execution_plan")
        graph.add_edge("build_execution_plan", "route_next_stage")
        graph.add_conditional_edges(
            "route_next_stage",
            self.route_after_stage_selection,
            {
                "report": "generate_report",
                "record": "record_stage_outcome",
                "validate": "validate_stage_input",
            },
        )
        graph.add_conditional_edges(
            "validate_stage_input",
            self.route_after_input_validation,
            {
                "record": "record_stage_outcome",
                "prepare": "prepare_stage_plan",
            },
        )
        graph.add_conditional_edges(
            "prepare_stage_plan",
            self.route_after_stage_plan,
            {
                "record": "record_stage_outcome",
                "decide": "decide_stage_approval",
            },
        )
        graph.add_conditional_edges(
            "decide_stage_approval",
            self.route_after_approval_decision,
            {
                "approval": "prepare_stage_approval",
                "ready": "mark_stage_ready",
            },
        )
        graph.add_edge("prepare_stage_approval", "stage_approval_gate")
        graph.add_conditional_edges(
            "stage_approval_gate",
            self.route_after_stage_approval,
            {
                "approve": "mark_stage_ready",
                "reject": "record_stage_outcome",
            },
        )
        graph.add_edge("mark_stage_ready", "execute_or_reconcile_stage")
        graph.add_edge(
            "execute_or_reconcile_stage", "validate_stage_result"
        )
        graph.add_edge("validate_stage_result", "record_stage_outcome")
        graph.add_conditional_edges(
            "record_stage_outcome",
            self.route_after_stage_record,
            {
                "next": "route_next_stage",
                "retry": "prepare_retry",
                "wait": END,
            },
        )
        graph.add_edge("prepare_retry", "retry_gate")
        graph.add_conditional_edges(
            "retry_gate",
            self.route_after_retry,
            {
                "retry": "validate_stage_input",
                "cancel": "record_stage_outcome",
            },
        )
        graph.add_edge("generate_report", END)
        return graph

    @staticmethod
    def select_entry(_state: OrchestratorState) -> dict[str, Any]:
        return {}

    @staticmethod
    def route_entry(state: OrchestratorState) -> str:
        return "direct" if state.get("run_mode") == "direct-stage" else "request"

    def parse_requirement(self, state: OrchestratorState) -> dict[str, Any]:
        try:
            if state.get("initial_requirement") is not None:
                parsed = self.parser.normalize_structured(
                    state["initial_requirement"] or {},
                    state["requirement_id"],
                )
            else:
                parsed = self.parser.parse(
                    state["raw_request"], state["requirement_id"]
                )
        except Exception as exc:
            self.repository.update_run(
                state["run_id"],
                status=RunStatus.FAILED,
                current_stage="requirement",
            )
            self.repository.append_event(
                event_key=f"{state['run_id']}:requirement:parse-failed",
                run_id=state["run_id"],
                event_type="REQUIREMENT_PARSE_FAILED",
                payload={
                    "parser": self.parser.name,
                    "parser_version": self.parser.version,
                    "error_type": type(exc).__name__,
                },
            )
            raise
        questions = parsed.clarification_questions
        status = (
            RunStatus.CLARIFYING
            if questions
            else RunStatus.REQUIREMENT_REVIEW
        )
        pending = None
        if questions:
            interaction_id = _stable_id(
                "interaction",
                state["run_id"],
                InteractionType.CLARIFICATION.value,
                _sha256(parsed.requirement),
            )
            pending = PendingInteraction(
                interaction_id=interaction_id,
                interaction_type=InteractionType.CLARIFICATION,
                run_id=state["run_id"],
                prompt="需求信息不完整，请补充后继续。",
                payload={
                    "questions": questions,
                    "requirement_draft": parsed.requirement,
                    "response_schema": {
                        "requirement": "完整 Requirement JSON，或使用 changes",
                        "changes": "对当前 Requirement 的递归字段更新",
                        "answer": "自然语言补充说明",
                    },
                },
            ).model_dump(mode="json")
        self.repository.update_run(
            state["run_id"],
            status=status,
            current_stage="requirement",
        )
        self.repository.append_event(
            event_key=(
                f"{state['run_id']}:requirement:parsed:"
                f"{_sha256(parsed.requirement)}"
            ),
            run_id=state["run_id"],
            event_type="REQUIREMENT_PARSED",
            payload={
                "parser": parsed.parser_name,
                "parser_version": parsed.parser_version,
                "clarification_count": len(questions),
                "llm_audit": (
                    parsed.llm_audit.model_dump(mode="json")
                    if parsed.llm_audit is not None
                    else None
                ),
            },
        )
        return {
            "requirement_draft": parsed.requirement,
            "parser_name": parsed.parser_name,
            "parser_version": parsed.parser_version,
            "clarification_questions": questions,
            "pending_interaction": pending,
            "run_status": status.value,
            "current_stage": "requirement",
            "updated_at": self._now(),
        }

    @staticmethod
    def route_after_parse(state: OrchestratorState) -> str:
        return "clarify" if state.get("clarification_questions") else "review"

    def clarification_gate(self, state: OrchestratorState) -> dict[str, Any]:
        pending = deepcopy(state["pending_interaction"])
        while True:
            response = interrupt(pending)
            try:
                _validate_interaction_response(pending, response)
                if str(response.get("decision", "")).lower() == "cancel":
                    self.repository.update_run(
                        state["run_id"],
                        status=RunStatus.CANCELLED,
                        clear_current_stage=True,
                    )
                    return {
                        "clarification_decision": "cancel",
                        "pending_interaction": None,
                        "updated_at": self._now(),
                    }
                if isinstance(response.get("answer"), str):
                    parsed = self.parser.revise_from_text(
                        state["requirement_draft"], response["answer"]
                    )
                    updated = parsed.requirement
                    self.repository.append_event(
                        event_key=(
                            f"{state['run_id']}:requirement:clarified:"
                            f"{_sha256(updated)}"
                        ),
                        run_id=state["run_id"],
                        event_type="REQUIREMENT_CLARIFICATION_PARSED",
                        payload={
                            "parser": parsed.parser_name,
                            "parser_version": parsed.parser_version,
                            "clarification_count": len(
                                parsed.clarification_questions
                            ),
                            "llm_audit": (
                                parsed.llm_audit.model_dump(mode="json")
                                if parsed.llm_audit is not None
                                else None
                            ),
                        },
                    )
                else:
                    updated = self.parser.apply_response(
                        state["requirement_draft"], response
                    )
            except (TypeError, ValueError, KeyError) as exc:
                pending["payload"]["validation_error"] = str(exc)
                continue
            break
        self.repository.update_run(
            state["run_id"],
            status=RunStatus.REQUIREMENT_REVIEW,
            current_stage="requirement",
        )
        return {
            "requirement_draft": updated,
            "clarification_questions": [],
            "clarification_decision": "continue",
            "pending_interaction": None,
            "run_status": RunStatus.REQUIREMENT_REVIEW.value,
            "updated_at": self._now(),
        }

    @staticmethod
    def route_after_clarification(state: OrchestratorState) -> str:
        return (
            "cancel"
            if state.get("clarification_decision") == "cancel"
            else "continue"
        )

    def prepare_requirement_review(
        self, state: OrchestratorState
    ) -> dict[str, Any]:
        requirement = Requirement.model_validate(state["requirement_draft"])
        draft_payload = requirement.model_dump(mode="json")
        draft_hash = _sha256(draft_payload)
        draft_ref = self.store.write_json(
            f"requirements/drafts/{state['run_id']}.{draft_hash[:16]}.json",
            draft_payload,
            immutable=True,
        )
        review_round = state.get("review_round", 0) + 1
        approval_id = _stable_id(
            "approval",
            state["run_id"],
            "REQUIREMENT_CONFIRMATION",
            draft_ref.sha256,
            str(review_round),
        )
        interaction_id = _stable_id(
            "interaction", state["run_id"], approval_id
        )
        pending = PendingInteraction(
            interaction_id=interaction_id,
            interaction_type=InteractionType.REQUIREMENT_CONFIRMATION,
            run_id=state["run_id"],
            approval_id=approval_id,
            prompt="请确认结构化需求。确认后该 revision 将被冻结。",
            payload={
                "approval_id": approval_id,
                "gate_type": "REQUIREMENT_CONFIRMATION",
                "requirement": draft_payload,
                "input_snapshot_uri": draft_ref.uri,
                "input_snapshot_sha256": draft_ref.sha256,
                "allowed_decisions": ["approve", "revise", "cancel"],
            },
        )
        pending_payload = pending.model_dump(mode="json")
        self.repository.ensure_approval(
            approval_id=approval_id,
            interaction_id=interaction_id,
            run_id=state["run_id"],
            gate_type="REQUIREMENT_CONFIRMATION",
            input_sha256=draft_ref.sha256,
            payload=pending_payload,
        )
        self.repository.update_run(
            state["run_id"],
            status=RunStatus.REQUIREMENT_REVIEW,
            current_stage="requirement",
        )
        return {
            "pending_interaction": pending_payload,
            "review_round": review_round,
            "review_decision": None,
            "run_status": RunStatus.REQUIREMENT_REVIEW.value,
            "updated_at": self._now(),
        }

    def requirement_review_gate(
        self, state: OrchestratorState
    ) -> dict[str, Any]:
        pending = deepcopy(state["pending_interaction"])
        while True:
            response = interrupt(pending)
            try:
                _validate_interaction_response(pending, response)
                decision = str(response.get("decision", "")).lower()
                if decision not in {"approve", "revise", "cancel"}:
                    raise ValueError(
                        "decision must be approve, revise, or cancel"
                    )
                updated_requirement = state["requirement_draft"]
                if decision == "revise":
                    updated_requirement = self.parser.apply_response(
                        state["requirement_draft"], response
                    )
            except (TypeError, ValueError, KeyError) as exc:
                pending["payload"]["validation_error"] = str(exc)
                continue
            break
        approval_status = {
            "approve": ApprovalStatus.APPROVED,
            "revise": ApprovalStatus.REVISED,
            "cancel": ApprovalStatus.CANCELLED,
        }[decision]
        self.repository.decide_approval(
            pending["approval_id"],
            status=approval_status,
            decision=response,
        )
        return {
            "review_decision": decision,
            "requirement_draft": updated_requirement,
            "pending_interaction": None,
            "updated_at": self._now(),
        }

    @staticmethod
    def route_after_review(state: OrchestratorState) -> str:
        decision = state.get("review_decision")
        if decision not in {"approve", "revise", "cancel"}:
            raise ValueError("Requirement review has no valid decision")
        return decision

    def cancel_run(self, state: OrchestratorState) -> dict[str, Any]:
        self.repository.update_run(
            state["run_id"],
            status=RunStatus.CANCELLED,
            clear_current_stage=True,
        )
        return {
            "run_status": RunStatus.CANCELLED.value,
            "current_stage": None,
            "pending_interaction": None,
            "updated_at": self._now(),
        }

    def freeze_requirement(self, state: OrchestratorState) -> dict[str, Any]:
        payload = dict(state["requirement_draft"])
        payload["confirmed_by_user"] = True
        requirement = Requirement.model_validate(payload)
        revision = requirement.revision
        requirement_ref = self.store.write_json(
            f"requirements/{state['run_id']}/requirement.v{revision}.json",
            requirement.model_dump(mode="json"),
            immutable=True,
        )
        self.repository.record_requirement(
            run_id=state["run_id"],
            revision=revision,
            artifact_uri=requirement_ref.uri,
            artifact_sha256=requirement_ref.sha256,
        )
        self.repository.update_run(
            state["run_id"],
            status=RunStatus.PLANNED,
            current_stage="planning",
            requirement_revision=revision,
        )
        return {
            "requirement_draft": requirement.model_dump(mode="json"),
            "requirement_revision": revision,
            "requirement_artifact_uri": requirement_ref.uri,
            "requirement_artifact_sha256": requirement_ref.sha256,
            "run_status": RunStatus.PLANNED.value,
            "current_stage": "planning",
            "updated_at": self._now(),
        }

    def initialize_direct_run(
        self, state: OrchestratorState
    ) -> dict[str, Any]:
        direct_input = state.get("direct_stage_input")
        if not isinstance(direct_input, dict):
            raise ValueError("direct-stage run is missing stage input")
        requirement_uri = direct_input["requirement_artifact_uri"]
        requirement_hash = direct_input["requirement_artifact_sha256"]
        if not self.store.exists_with_hash(requirement_uri, requirement_hash):
            raise ValueError("direct-stage Requirement artifact hash mismatch")
        requirement = Requirement.model_validate(
            self.store.read_json(requirement_uri)
        )
        if requirement.revision != direct_input["requirement_revision"]:
            raise ValueError("direct-stage Requirement revision mismatch")
        self.repository.record_requirement(
            run_id=state["run_id"],
            revision=requirement.revision,
            artifact_uri=requirement_uri,
            artifact_sha256=requirement_hash,
        )
        self.repository.update_run(
            state["run_id"],
            status=RunStatus.PLANNED,
            current_stage="planning",
            requirement_revision=requirement.revision,
        )
        return {
            "requirement_draft": requirement.model_dump(mode="json"),
            "requirement_revision": requirement.revision,
            "requirement_artifact_uri": requirement_uri,
            "requirement_artifact_sha256": requirement_hash,
            "run_status": RunStatus.PLANNED.value,
            "current_stage": "planning",
            "updated_at": self._now(),
        }

    def build_execution_plan(
        self, state: OrchestratorState
    ) -> dict[str, Any]:
        requirement = Requirement.model_validate(state["requirement_draft"])
        requirement_pointer = ArtifactPointer(
            uri=state["requirement_artifact_uri"],
            sha256=state["requirement_artifact_sha256"],
        )
        if state.get("run_mode") == "direct-stage":
            routes = self._direct_routes(state, requirement_pointer)
        else:
            routes = self._policy_routes(requirement, requirement_pointer)
        seed = {
            "run_id": state["run_id"],
            "requirement": requirement_pointer.model_dump(mode="json"),
            "policy_version": ROUTING_POLICY_VERSION,
            "routes": [
                route.model_dump(mode="json")
                for route in routes
            ],
        }
        input_snapshot_hash = _sha256(seed)
        plan = ExecutionPlan(
            plan_id=_stable_id("plan", input_snapshot_hash),
            run_id=state["run_id"],
            requirement_revision=state["requirement_revision"],
            requirement_artifact=requirement_pointer,
            routes=routes,
            required_gates=[
                "REQUIREMENT_CONFIRMATION"
                if state.get("run_mode") != "direct-stage"
                else "EXPLICIT_STAGE_INPUT"
            ],
            input_snapshot_sha256=input_snapshot_hash,
            created_at=datetime.fromisoformat(state["created_at"]),
        )
        plan_ref = self.store.write_json(
            f"plans/{state['run_id']}/execution_plan.json",
            plan.model_dump(mode="json"),
            immutable=True,
        )
        self.repository.update_run(
            state["run_id"],
            status=RunStatus.PLANNED,
            current_stage="routing",
        )
        return {
            "execution_plan": plan.model_dump(mode="json"),
            "execution_plan_uri": plan_ref.uri,
            "execution_plan_sha256": plan_ref.sha256,
            "route_cursor": -1,
            "current_route": None,
            "pending_control_outcome": None,
            "current_stage": "routing",
            "updated_at": self._now(),
        }

    def _policy_routes(
        self,
        requirement: Requirement,
        requirement_pointer: ArtifactPointer,
    ) -> list[StageRoute]:
        required_evidence = max(
            (
                _EVIDENCE_ORDER[target.required_evidence_level]
                for target in requirement.scientific_targets
            ),
            default=1,
        )
        permissions = {
            StageId.RETRIEVAL: True,
            StageId.ML: requirement.budget.allow_ml,
            StageId.DFT: requirement.budget.allow_dft,
            StageId.MANY_BODY: requirement.budget.allow_many_body,
        }
        routes: list[StageRoute] = []
        for stage in StageId:
            capability = self.runners.capability(stage)
            required = stage is StageId.RETRIEVAL or (
                required_evidence >= _STAGE_EVIDENCE[stage]
            )
            if not required:
                disposition = StageDisposition.SKIPPED
                reason = (
                    "The confirmed evidence target does not require this stage; "
                    "budget permission alone does not select it."
                )
            elif not permissions[stage]:
                disposition = StageDisposition.BLOCKED
                reason = (
                    "The evidence target requires this stage, but the confirmed "
                    "Requirement does not authorize it."
                )
            elif not capability.registered:
                disposition = StageDisposition.UNAVAILABLE
                reason = capability.unavailable_reason or "capability unavailable"
            else:
                disposition = StageDisposition.SELECTED
                reason = (
                    "Selected by the deterministic evidence-routing policy."
                )
            routes.append(
                StageRoute(
                    stage=stage,
                    agent_id=STAGE_TO_AGENT[stage],
                    required=required,
                    disposition=disposition,
                    reason=reason,
                    required_inputs=list(capability.required_inputs),
                    input_artifacts=(
                        {"requirement": requirement_pointer}
                        if stage is StageId.RETRIEVAL
                        else {}
                    ),
                    required_gates=(
                        ["EXPENSIVE_BATCH_APPROVAL"]
                        if disposition is StageDisposition.SELECTED
                        and capability.requires_approval
                        else []
                    ),
                    capability=capability,
                )
            )
        return routes

    def _direct_routes(
        self,
        state: OrchestratorState,
        requirement_pointer: ArtifactPointer,
    ) -> list[StageRoute]:
        target = StageId(state["direct_stage"])
        payload = state["direct_stage_input"]
        supplied = {
            name: ArtifactPointer.model_validate(pointer)
            for name, pointer in payload.get("artifacts", {}).items()
        }
        supplied["requirement"] = requirement_pointer
        routes: list[StageRoute] = []
        for stage in StageId:
            capability = self.runners.capability(stage)
            if stage is not target:
                disposition = StageDisposition.SKIPPED
                reason = "Explicit run-stage selected another stage."
                inputs: dict[str, ArtifactPointer] = {}
            else:
                missing = [
                    name
                    for name in capability.required_inputs
                    if name not in supplied
                ]
                inputs = supplied
                if missing:
                    disposition = StageDisposition.BLOCKED
                    reason = (
                        "Explicit stage input is missing: " + ", ".join(missing)
                    )
                elif not capability.registered:
                    disposition = StageDisposition.UNAVAILABLE
                    reason = (
                        capability.unavailable_reason
                        or "capability unavailable"
                    )
                else:
                    disposition = StageDisposition.SELECTED
                    reason = "Selected by explicit run-stage input."
            routes.append(
                StageRoute(
                    stage=stage,
                    agent_id=STAGE_TO_AGENT[stage],
                    required=stage is target,
                    disposition=disposition,
                    reason=reason,
                    required_inputs=list(capability.required_inputs),
                    input_artifacts=inputs,
                    required_gates=(
                        ["EXPENSIVE_BATCH_APPROVAL"]
                        if stage is target and capability.requires_approval
                        else []
                    ),
                    capability=capability,
                )
            )
        return routes

    def route_next_stage(
        self, state: OrchestratorState
    ) -> dict[str, Any]:
        plan = ExecutionPlan.model_validate(state["execution_plan"])
        cursor = state.get("route_cursor", -1) + 1
        while cursor < len(plan.routes):
            route = plan.routes[cursor]
            if route.disposition is StageDisposition.SKIPPED:
                cursor += 1
                continue
            pending = None
            if route.disposition in {
                StageDisposition.BLOCKED,
                StageDisposition.UNAVAILABLE,
            }:
                status = (
                    StageStatus.BLOCKED_MISSING_INPUT
                    if route.disposition is StageDisposition.BLOCKED
                    else StageStatus.CAPABILITY_UNAVAILABLE
                )
                pending = self._blocked_outcome(route, status)
            self.repository.update_run(
                state["run_id"],
                status=(
                    RunStatus.RUNNING
                    if state.get("stage_outcomes")
                    else RunStatus.PLANNED
                ),
                current_stage=route.agent_id,
            )
            return {
                "route_cursor": cursor,
                "current_route": route.model_dump(mode="json"),
                "current_stage": route.agent_id,
                "prepared_stage_plan": None,
                "prepared_stage_plan_uri": None,
                "prepared_stage_plan_sha256": None,
                "stage_approval_required": None,
                "stage_approval_decision": None,
                "pending_control_outcome": (
                    pending.model_dump(mode="json") if pending else None
                ),
                "updated_at": self._now(),
            }
        return {
            "route_cursor": cursor,
            "current_route": None,
            "current_stage": None,
            "prepared_stage_plan": None,
            "prepared_stage_plan_uri": None,
            "prepared_stage_plan_sha256": None,
            "stage_approval_required": None,
            "pending_control_outcome": None,
            "updated_at": self._now(),
        }

    @staticmethod
    def route_after_stage_selection(state: OrchestratorState) -> str:
        if state.get("current_route") is None:
            return "report"
        if state.get("pending_control_outcome") is not None:
            return "record"
        return "validate"

    def validate_stage_input(
        self, state: OrchestratorState
    ) -> dict[str, Any]:
        route = StageRoute.model_validate(state["current_route"])
        attempt = self._next_attempt(state, route)
        context = self._stage_context(state, route, attempt)
        self.repository.upsert_stage_run(
            run_id=state["run_id"],
            stage=route.agent_id,
            stage_id=route.stage,
            agent_id=route.agent_id,
            status=StageStatus.VALIDATING_INPUT.value,
            attempt=attempt,
            required=route.required,
            disposition=route.disposition.value,
        )
        missing = [
            name
            for name in route.required_inputs
            if name != "requirement" and name not in context.input_artifacts
        ]
        if missing:
            validation = StageInputValidation(
                valid=False,
                missing_fields=missing,
                remediation=[f"provide explicit artifact '{name}'" for name in missing],
            )
            outcome = self._blocked_outcome(
                route,
                StageStatus.BLOCKED_MISSING_INPUT,
                validation,
            )
            return {
                "stage_input_validation": validation.model_dump(mode="json"),
                "pending_control_outcome": outcome.model_dump(mode="json"),
                "updated_at": self._now(),
            }
        if not route.capability.registered or not self._has_runner(route.stage):
            validation = StageInputValidation(
                valid=False,
                errors=[
                    route.capability.unavailable_reason
                    or "stage capability is unavailable"
                ],
                remediation=[
                    f"register a production runner for {route.stage.value}"
                ],
            )
            outcome = self._blocked_outcome(
                route,
                StageStatus.CAPABILITY_UNAVAILABLE,
                validation,
            )
            return {
                "stage_input_validation": validation.model_dump(mode="json"),
                "pending_control_outcome": outcome.model_dump(mode="json"),
                "updated_at": self._now(),
            }
        runner = self._runner(state, context)
        try:
            validation = runner.validate_input(context)
        except Exception as exc:
            validation = StageInputValidation(
                valid=False,
                errors=[f"input validation failed ({type(exc).__name__})"],
                remediation=["inspect the immutable stage input artifacts"],
            )
        if not validation.valid:
            status = validation.failure_status or (
                StageStatus.BLOCKED_MISSING_INPUT
                if validation.missing_fields
                else StageStatus.PERMANENT_FAILED
            )
            outcome = self._blocked_outcome(route, status, validation)
            return {
                "stage_input_validation": validation.model_dump(mode="json"),
                "pending_control_outcome": outcome.model_dump(mode="json"),
                "updated_at": self._now(),
            }
        return {
            "stage_input_validation": validation.model_dump(mode="json"),
            "pending_control_outcome": None,
            "updated_at": self._now(),
        }

    @staticmethod
    def route_after_input_validation(state: OrchestratorState) -> str:
        if state.get("pending_control_outcome") is not None:
            return "record"
        return "prepare"

    def prepare_stage_plan(
        self, state: OrchestratorState
    ) -> dict[str, Any]:
        route = StageRoute.model_validate(state["current_route"])
        attempt = self._next_attempt(state, route)
        context = self._stage_context(state, route, attempt)
        input_ref = self._write_stage_input_snapshot(context)
        context = context.model_copy(
            update={
                "input_snapshot": ArtifactPointer(
                    uri=input_ref.uri,
                    sha256=input_ref.sha256,
                )
            }
        )
        plan_relative_path = (
            f"plans/{state['run_id']}/stages/{route.stage.value}/"
            f"attempt-{attempt}.json"
        )
        has_frozen_plan = self.store.exists(plan_relative_path)
        try:
            if has_frozen_plan:
                plan_ref = self.store.inspect(
                    plan_relative_path, media_type="application/json"
                )
                prepared = PreparedStagePlan.model_validate(
                    self.store.read_json(plan_relative_path)
                )
            else:
                runner = self._runner(state, context)
                prepared = runner.prepare(context)
        except Exception as exc:
            outcome = (
                self._invalid_stage_plan_outcome(
                    context, input_ref.sha256, exc
                )
                if has_frozen_plan
                else runner_exception_outcome(
                    context,
                    _stable_id(
                        "operation",
                        context.project_id,
                        context.run_id,
                        context.stage.value,
                        str(context.attempt),
                        "prepare",
                        input_ref.sha256,
                    ),
                    exc,
                )
            )
            return {
                "pending_control_outcome": outcome.model_dump(mode="json"),
                "updated_at": self._now(),
            }
        try:
            self._validate_prepared_stage_plan(
                prepared, context, input_ref.sha256
            )
            if not self.store.exists_with_hash(
                prepared.native_plan_uri,
                prepared.native_plan_sha256,
            ):
                raise ValueError("native stage plan failed integrity check")
        except Exception as exc:
            outcome = self._invalid_stage_plan_outcome(
                context, input_ref.sha256, exc
            )
            return {
                "pending_control_outcome": outcome.model_dump(mode="json"),
                "updated_at": self._now(),
            }
        if not self.store.exists(plan_relative_path):
            plan_ref = self.store.write_json(
                plan_relative_path,
                prepared.model_dump(mode="json"),
                immutable=True,
            )
        operation_key = self._stage_operation_key(prepared)
        self.repository.record_stage_attempt(
            StageExecutionRecord(
                run_id=state["run_id"],
                stage=route.stage,
                agent_id=route.agent_id,
                attempt=attempt,
                operation_key=operation_key,
                status=StageStatus.READY,
                plan_uri=plan_ref.uri,
                plan_sha256=plan_ref.sha256,
                updated_at=self.clock.now(),
            )
        )
        plan_refs = dict(state.get("stage_plan_refs", {}))
        plan_refs[route.agent_id] = {
            "uri": plan_ref.uri,
            "sha256": plan_ref.sha256,
        }
        return {
            "prepared_stage_plan": prepared.model_dump(mode="json"),
            "prepared_stage_plan_uri": plan_ref.uri,
            "prepared_stage_plan_sha256": plan_ref.sha256,
            "stage_plan_refs": plan_refs,
            "pending_control_outcome": None,
            "updated_at": self._now(),
        }

    @staticmethod
    def route_after_stage_plan(state: OrchestratorState) -> str:
        return (
            "record"
            if state.get("pending_control_outcome") is not None
            else "decide"
        )

    @staticmethod
    def decide_stage_approval(
        state: OrchestratorState,
    ) -> dict[str, Any]:
        route = StageRoute.model_validate(state["current_route"])
        prepared = PreparedStagePlan.model_validate(
            state["prepared_stage_plan"]
        )
        required = effective_stage_approval(route.capability, prepared)
        return {
            "stage_approval_required": required,
            "stage_approval_decision": None,
        }

    @staticmethod
    def route_after_approval_decision(state: OrchestratorState) -> str:
        return (
            "approval"
            if state.get("stage_approval_required")
            else "ready"
        )

    def mark_stage_ready(
        self, state: OrchestratorState
    ) -> dict[str, Any]:
        route = StageRoute.model_validate(state["current_route"])
        attempt = self._next_attempt(state, route)
        prepared, _plan_ref = self._load_prepared_stage_plan(
            state, route, attempt
        )
        operation_key = self._stage_operation_key(prepared)
        self.repository.upsert_stage_run(
            run_id=state["run_id"],
            stage=route.agent_id,
            stage_id=route.stage,
            agent_id=route.agent_id,
            status=StageStatus.READY.value,
            attempt=attempt,
            operation_key=operation_key,
            required=route.required,
            disposition=route.disposition.value,
        )
        return {"updated_at": self._now()}

    def prepare_stage_approval(
        self, state: OrchestratorState
    ) -> dict[str, Any]:
        route = StageRoute.model_validate(state["current_route"])
        attempt = self._next_attempt(state, route)
        prepared, plan_ref = self._load_prepared_stage_plan(
            state, route, attempt
        )
        gate_type = prepared.gate_type or "EXPENSIVE_BATCH_APPROVAL"
        approval_id = _stable_id(
            "approval",
            state["run_id"],
            route.stage.value,
            gate_type,
            plan_ref.sha256,
        )
        interaction_id = _stable_id(
            "interaction", state["run_id"], approval_id
        )
        pending = PendingInteraction(
            interaction_id=interaction_id,
            interaction_type=InteractionType.EXPENSIVE_BATCH_APPROVAL,
            run_id=state["run_id"],
            approval_id=approval_id,
            prompt=f"是否批准 {route.agent_id} 的昂贵计算批次？",
            payload={
                "approval_id": approval_id,
                "gate_type": gate_type,
                "stage": route.stage.value,
                "agent_id": route.agent_id,
                "stage_plan_uri": plan_ref.uri,
                "stage_plan_sha256": plan_ref.sha256,
                "input_snapshot_uri": prepared.input_snapshot_uri,
                "input_snapshot_sha256": (
                    prepared.input_snapshot_sha256
                ),
                "native_plan_uri": prepared.native_plan_uri,
                "native_plan_sha256": prepared.native_plan_sha256,
                "operation_input_sha256": (
                    prepared.operation_input_sha256
                ),
                "resource_estimate": prepared.resource_estimate,
                "policy_version": prepared.policy_version,
                "risk": prepared.risk_summary,
                "allowed_decisions": ["approve", "reject"],
            },
        )
        pending_payload = pending.model_dump(mode="json")
        self.repository.ensure_approval(
            approval_id=approval_id,
            interaction_id=interaction_id,
            run_id=state["run_id"],
            gate_type=gate_type,
            input_sha256=plan_ref.sha256,
            payload=pending_payload,
        )
        self.repository.upsert_stage_run(
            run_id=state["run_id"],
            stage=route.agent_id,
            stage_id=route.stage,
            agent_id=route.agent_id,
            status=StageStatus.WAITING_APPROVAL.value,
            attempt=attempt,
            required=route.required,
            disposition=route.disposition.value,
        )
        self.repository.update_run(
            state["run_id"],
            status=RunStatus.WAITING_APPROVAL,
            current_stage=route.agent_id,
        )
        return {
            "pending_interaction": pending_payload,
            "stage_approval_decision": None,
            "run_status": RunStatus.WAITING_APPROVAL.value,
            "updated_at": self._now(),
        }

    def stage_approval_gate(
        self, state: OrchestratorState
    ) -> dict[str, Any]:
        pending = deepcopy(state["pending_interaction"])
        while True:
            response = interrupt(pending)
            try:
                _validate_interaction_response(pending, response)
                decision = str(response.get("decision", "")).lower()
                if decision not in {"approve", "reject"}:
                    raise ValueError("decision must be approve or reject")
            except (TypeError, ValueError, KeyError) as exc:
                pending["payload"]["validation_error"] = str(exc)
                continue
            break
        self._validate_stage_approval_snapshot(state, pending)
        self.repository.decide_approval(
            pending["approval_id"],
            status=(
                ApprovalStatus.APPROVED
                if decision == "approve"
                else ApprovalStatus.REJECTED
            ),
            decision=response,
        )
        updates: dict[str, Any] = {
            "stage_approval_decision": decision,
            "pending_interaction": None,
            "updated_at": self._now(),
        }
        if decision == "reject":
            route = StageRoute.model_validate(state["current_route"])
            attempt = self._next_attempt(state, route)
            prepared, _plan_ref = self._load_prepared_stage_plan(
                state, route, attempt
            )
            updates["pending_control_outcome"] = ControlStageOutcome(
                stage=route.stage,
                agent_id=route.agent_id,
                outcome=ControlOutcomeType.COMPLETED,
                status=StageStatus.CANCELLED,
                idempotency_key=self._stage_operation_key(prepared),
                errors=[
                    ControlError(
                        category="USER_REJECTED_STAGE",
                        operation="stage_approval",
                        public_message=(
                            f"User rejected {route.agent_id} expensive batch"
                        ),
                    )
                ],
            ).model_dump(mode="json")
        return updates

    def _validate_stage_approval_snapshot(
        self,
        state: OrchestratorState,
        pending: dict[str, Any],
    ) -> None:
        snapshot = pending["payload"]
        route = StageRoute.model_validate(state["current_route"])
        attempt = self._next_attempt(state, route)
        prepared, plan_ref = self._load_prepared_stage_plan(
            state, route, attempt
        )
        expected = {
            "stage_plan_uri": plan_ref.uri,
            "stage_plan_sha256": plan_ref.sha256,
            "input_snapshot_uri": prepared.input_snapshot_uri,
            "input_snapshot_sha256": prepared.input_snapshot_sha256,
            "native_plan_uri": prepared.native_plan_uri,
            "native_plan_sha256": prepared.native_plan_sha256,
            "operation_input_sha256": prepared.operation_input_sha256,
            "policy_version": prepared.policy_version,
        }
        for key, value in expected.items():
            if snapshot.get(key) != value:
                raise ValueError(
                    f"approved stage snapshot no longer matches {key}"
                )

    @staticmethod
    def route_after_stage_approval(state: OrchestratorState) -> str:
        return (
            "approve"
            if state.get("stage_approval_decision") == "approve"
            else "reject"
        )

    def execute_or_reconcile_stage(
        self, state: OrchestratorState
    ) -> dict[str, Any]:
        route = StageRoute.model_validate(state["current_route"])
        attempt = self._next_attempt(state, route)
        context = self._stage_context(state, route, attempt)
        prepared, plan_ref = self._load_prepared_stage_plan(
            state, route, attempt
        )
        context = context.model_copy(
            update={
                "input_snapshot": ArtifactPointer(
                    uri=prepared.input_snapshot_uri,
                    sha256=prepared.input_snapshot_sha256,
                )
            }
        )
        operation_key = self._stage_operation_key(prepared)
        self.repository.upsert_stage_run(
            run_id=state["run_id"],
            stage=route.agent_id,
            stage_id=route.stage,
            agent_id=route.agent_id,
            status=StageStatus.RUNNING.value,
            attempt=attempt,
            operation_key=operation_key,
            required=route.required,
            disposition=route.disposition.value,
        )
        self.repository.update_run(
            state["run_id"],
            status=RunStatus.RUNNING,
            current_stage=route.agent_id,
        )
        self.repository.record_stage_attempt(
            StageExecutionRecord(
                run_id=state["run_id"],
                stage=route.stage,
                agent_id=route.agent_id,
                attempt=attempt,
                operation_key=operation_key,
                status=StageStatus.RUNNING,
                plan_uri=plan_ref.uri,
                plan_sha256=plan_ref.sha256,
                updated_at=self.clock.now(),
            )
        )
        runner = self._runner(state, context)
        try:
            outcome = runner.start(context, prepared, operation_key)
        except Exception as exc:
            outcome = ControlStageOutcome(
                stage=route.stage,
                agent_id=route.agent_id,
                outcome=ControlOutcomeType.FAILED,
                status=StageStatus.PERMANENT_FAILED,
                idempotency_key=operation_key,
                errors=[
                    ControlError(
                        category="ORCHESTRATOR_RUNNER_EXCEPTION",
                        operation=route.agent_id,
                        public_message=(
                            f"runner failed ({type(exc).__name__})"
                        ),
                    )
                ],
            )
        if outcome.idempotency_key != operation_key:
            outcome = ControlStageOutcome(
                stage=route.stage,
                agent_id=route.agent_id,
                outcome=ControlOutcomeType.FAILED,
                status=StageStatus.PERMANENT_FAILED,
                idempotency_key=operation_key,
                errors=[
                    ControlError(
                        category="BACKEND_INCONSISTENT",
                        operation="start",
                        public_message=(
                            "runner returned a different idempotency key"
                        ),
                    )
                ],
            )
        retry_counters = dict(state.get("retry_counters", {}))
        retry_counters[route.agent_id] = attempt
        return {
            "pending_control_outcome": outcome.model_dump(mode="json"),
            "retry_counters": retry_counters,
            "updated_at": self._now(),
        }

    def reconcile_current_stage(
        self, state: OrchestratorState
    ) -> dict[str, Any]:
        """Called only by explicit Runtime.resume for WaitingExternal."""

        route = StageRoute.model_validate(state["current_route"])
        previous = ControlStageOutcome.model_validate(
            state["stage_outcomes"][route.agent_id]
        )
        if previous.outcome is not ControlOutcomeType.WAITING_EXTERNAL:
            raise ValueError("current stage is not waiting for an external job")
        attempt = state.get("retry_counters", {}).get(route.agent_id, 1)
        context = self._stage_context(state, route, attempt)
        prepared, _plan_ref = self._load_prepared_stage_plan(
            state, route, attempt
        )
        context = context.model_copy(
            update={
                "input_snapshot": ArtifactPointer(
                    uri=prepared.input_snapshot_uri,
                    sha256=prepared.input_snapshot_sha256,
                )
            }
        )
        runner = self._runner(state, context)
        outcome = runner.reconcile(
            context,
            prepared,
            previous.external_job_ref or "",
            previous.idempotency_key,
        )
        try:
            self._validate_external_outcome(
                outcome, context, runner.backend_name
            )
        except RepositoryConflictError as exc:
            outcome = ControlStageOutcome(
                stage=route.stage,
                agent_id=route.agent_id,
                outcome=ControlOutcomeType.FAILED,
                status=StageStatus.PERMANENT_FAILED,
                idempotency_key=previous.idempotency_key,
                operation_ref=previous.operation_ref,
                errors=[
                    ControlError(
                        category="BACKEND_INCONSISTENT",
                        operation="reconcile",
                        public_message=str(exc),
                    )
                ],
            )
        return {
            "pending_control_outcome": outcome.model_dump(mode="json"),
            "updated_at": self._now(),
        }

    def cancel_current_external_stage(
        self, state: OrchestratorState
    ) -> dict[str, Any]:
        """Cancel one known external job through its owning runner."""

        route = StageRoute.model_validate(state["current_route"])
        previous = ControlStageOutcome.model_validate(
            state["stage_outcomes"][route.agent_id]
        )
        if previous.outcome is not ControlOutcomeType.WAITING_EXTERNAL:
            raise ValueError("current stage is not waiting for an external job")
        attempt = state.get("retry_counters", {}).get(route.agent_id, 1)
        context = self._stage_context(state, route, attempt)
        prepared, _plan_ref = self._load_prepared_stage_plan(
            state, route, attempt
        )
        context = context.model_copy(
            update={
                "input_snapshot": ArtifactPointer(
                    uri=prepared.input_snapshot_uri,
                    sha256=prepared.input_snapshot_sha256,
                )
            }
        )
        runner = self._runner(state, context)
        cancel_key = _stable_id(
            "cancel", previous.idempotency_key, previous.external_job_ref or ""
        )
        cancelled = runner.cancel(
            previous.external_job_ref or "", cancel_key
        )
        outcome = ControlStageOutcome(
            stage=route.stage,
            agent_id=route.agent_id,
            outcome=ControlOutcomeType.COMPLETED,
            status=StageStatus.CANCELLED,
            idempotency_key=previous.idempotency_key,
            operation_ref=previous.operation_ref,
            external_job_ref=cancelled.external_job_ref,
            external_status=cancelled.status,
            external_status_sequence=(
                (previous.external_status_sequence or 0) + 1
            ),
            errors=[
                ControlError(
                    category="USER_CANCELLED_EXTERNAL_STAGE",
                    operation="cancel",
                    public_message=cancelled.message or "external stage cancelled",
                )
            ],
        )
        try:
            self._validate_external_outcome(
                outcome, context, runner.backend_name
            )
        except RepositoryConflictError as exc:
            outcome = ControlStageOutcome(
                stage=route.stage,
                agent_id=route.agent_id,
                outcome=ControlOutcomeType.FAILED,
                status=StageStatus.PERMANENT_FAILED,
                idempotency_key=previous.idempotency_key,
                operation_ref=previous.operation_ref,
                errors=[
                    ControlError(
                        category="BACKEND_INCONSISTENT",
                        operation="cancel",
                        public_message=str(exc),
                    )
                ],
            )
        return {
            "pending_control_outcome": outcome.model_dump(mode="json"),
            "updated_at": self._now(),
        }

    def _validate_external_outcome(
        self,
        outcome: ControlStageOutcome,
        context: StageExecutionContext,
        backend_name: str,
    ) -> None:
        if not outcome.external_job_ref or not outcome.external_status:
            return
        self.repository.validate_external_job_record(
            ExternalJobRecord(
                run_id=context.run_id,
                stage=context.stage,
                attempt=context.attempt,
                backend=backend_name,
                external_job_ref=outcome.external_job_ref,
                status=outcome.external_status,
                status_sequence=outcome.external_status_sequence or 0,
                submit_operation_key=outcome.idempotency_key,
                result_uri=outcome.native_result_uri,
                result_sha256=outcome.native_result_sha256,
                updated_at=self.clock.now(),
            )
        )

    def validate_stage_result(
        self, state: OrchestratorState
    ) -> dict[str, Any]:
        route = StageRoute.model_validate(state["current_route"])
        outcome = ControlStageOutcome.model_validate(
            state["pending_control_outcome"]
        )
        errors = list(outcome.errors)
        if outcome.native_result_uri and not self.store.exists_with_hash(
            outcome.native_result_uri,
            outcome.native_result_sha256 or "",
        ):
            errors.append(
                ControlError(
                    category="BACKEND_INCONSISTENT",
                    operation="validate_stage_result",
                    public_message=(
                        "native result artifact is missing or has changed"
                    ),
                )
            )
        if route.capability.is_mock:
            claimed = outcome.summary.get("evidence_level")
            if claimed and claimed != EvidenceLevel.L0_PARSED.value:
                errors.append(
                    ControlError(
                        category="MOCK_EVIDENCE_VIOLATION",
                        operation="validate_stage_result",
                        public_message=(
                            "mock runner cannot raise scientific evidence"
                        ),
                    )
                )
        if errors != outcome.errors:
            outcome = outcome.model_copy(
                update={
                    "outcome": ControlOutcomeType.FAILED,
                    "status": StageStatus.PERMANENT_FAILED,
                    "errors": errors,
                    "native_result_uri": None,
                    "native_result_sha256": None,
                }
            )
        return {
            "pending_control_outcome": outcome.model_dump(mode="json"),
            "updated_at": self._now(),
        }

    def record_stage_outcome(
        self, state: OrchestratorState
    ) -> dict[str, Any]:
        route = StageRoute.model_validate(state["current_route"])
        outcome = ControlStageOutcome.model_validate(
            state["pending_control_outcome"]
        )
        attempt = state.get("retry_counters", {}).get(route.agent_id)
        if attempt is None:
            attempt = self._next_attempt(state, route)
            retry_counters = dict(state.get("retry_counters", {}))
            retry_counters[route.agent_id] = attempt
        else:
            retry_counters = dict(state.get("retry_counters", {}))
        primary_error = outcome.errors[0] if outcome.errors else None
        plan_pointer = state.get("stage_plan_refs", {}).get(route.agent_id)
        external_record = None
        if outcome.external_job_ref and outcome.external_status:
            context = self._stage_context(state, route, attempt)
            runner = self._runner(state, context)
            external_record = ExternalJobRecord(
                run_id=state["run_id"],
                stage=route.stage,
                attempt=attempt,
                backend=runner.backend_name,
                external_job_ref=outcome.external_job_ref,
                status=outcome.external_status,
                status_sequence=outcome.external_status_sequence or 0,
                submit_operation_key=outcome.idempotency_key,
                result_uri=outcome.native_result_uri,
                result_sha256=outcome.native_result_sha256,
                updated_at=self.clock.now(),
            )
            self.repository.validate_external_job_record(external_record)
        control_hash = _sha256(outcome.model_dump(mode="json"))
        control_ref = self.store.write_json(
            (
                f"stages/orchestrator/{state['run_id']}/"
                f"{route.agent_id}.attempt-{attempt}."
                f"{outcome.status.value}.{control_hash[:12]}.json"
            ),
            outcome.model_dump(mode="json"),
            immutable=True,
        )
        self.repository.upsert_stage_run(
            run_id=state["run_id"],
            stage=route.agent_id,
            stage_id=route.stage,
            agent_id=route.agent_id,
            status=outcome.status.value,
            attempt=attempt,
            operation_key=outcome.idempotency_key,
            operation_ref=outcome.operation_ref,
            result_uri=control_ref.uri,
            result_sha256=control_ref.sha256,
            required=route.required,
            disposition=route.disposition.value,
            error=(
                primary_error.model_dump(mode="json")
                if primary_error
                else None
            ),
        )
        self.repository.record_operation(
            idempotency_key=outcome.idempotency_key,
            run_id=state["run_id"],
            stage=route.agent_id,
            status=outcome.status.value,
            operation_ref=outcome.operation_ref,
            payload=outcome.model_dump(mode="json"),
        )
        self.repository.record_stage_attempt(
            StageExecutionRecord(
                run_id=state["run_id"],
                stage=route.stage,
                agent_id=route.agent_id,
                attempt=attempt,
                operation_key=outcome.idempotency_key,
                status=outcome.status,
                plan_uri=(plan_pointer or {}).get("uri"),
                plan_sha256=(plan_pointer or {}).get("sha256"),
                result_uri=outcome.native_result_uri,
                result_sha256=outcome.native_result_sha256,
                error=primary_error,
                updated_at=self.clock.now(),
            )
        )
        if external_record is not None:
            self.repository.record_external_job(external_record)
        stage_outcomes = dict(state.get("stage_outcomes", {}))
        stage_outcomes[route.agent_id] = outcome.model_dump(mode="json")
        stage_statuses = dict(state.get("stage_statuses", {}))
        stage_statuses[route.agent_id] = outcome.status.value
        candidate_ids = list(state.get("candidate_ids", []))
        if route.stage is StageId.RETRIEVAL:
            candidate_ids = list(outcome.summary.get("candidate_ids", []))
        if outcome.outcome is ControlOutcomeType.WAITING_EXTERNAL:
            run_status = RunStatus.PAUSED
        elif outcome.status in {
            StageStatus.RETRYABLE_FAILED,
            StageStatus.BLOCKED_MISSING_INPUT,
            StageStatus.CAPABILITY_UNAVAILABLE,
        }:
            run_status = RunStatus.PAUSED
        else:
            run_status = RunStatus.RUNNING
        self.repository.update_run(
            state["run_id"],
            status=run_status,
            current_stage=route.agent_id,
        )
        return {
            "stage_outcomes": stage_outcomes,
            "stage_statuses": stage_statuses,
            "candidate_ids": candidate_ids,
            "retry_counters": retry_counters,
            "pending_control_outcome": None,
            "run_status": run_status.value,
            "updated_at": self._now(),
        }

    @staticmethod
    def route_after_stage_record(state: OrchestratorState) -> str:
        route = StageRoute.model_validate(state["current_route"])
        outcome = ControlStageOutcome.model_validate(
            state["stage_outcomes"][route.agent_id]
        )
        if outcome.outcome is ControlOutcomeType.WAITING_EXTERNAL:
            return "wait"
        if outcome.status is StageStatus.RETRYABLE_FAILED:
            return "retry"
        return "next"

    def prepare_retry(self, state: OrchestratorState) -> dict[str, Any]:
        route = StageRoute.model_validate(state["current_route"])
        attempt = state.get("retry_counters", {}).get(route.agent_id, 1)
        interaction_id = _stable_id(
            "interaction",
            state["run_id"],
            f"{route.agent_id}-retry",
            str(attempt),
        )
        pending = PendingInteraction(
            interaction_id=interaction_id,
            interaction_type=InteractionType.RETRY_CONFIRMATION,
            run_id=state["run_id"],
            prompt=f"{route.agent_id} 的自动重试已耗尽。是否再次尝试？",
            payload={
                "stage": route.stage.value,
                "agent_id": route.agent_id,
                "attempt": attempt,
                "allowed_decisions": ["retry", "cancel"],
                "errors": state["stage_outcomes"][route.agent_id].get(
                    "errors", []
                ),
            },
        ).model_dump(mode="json")
        self.repository.update_run(
            state["run_id"],
            status=RunStatus.PAUSED,
            current_stage=route.agent_id,
        )
        return {
            "pending_interaction": pending,
            "retry_decision": None,
            "run_status": RunStatus.PAUSED.value,
            "updated_at": self._now(),
        }

    def retry_gate(self, state: OrchestratorState) -> dict[str, Any]:
        pending = deepcopy(state["pending_interaction"])
        while True:
            response = interrupt(pending)
            try:
                _validate_interaction_response(pending, response)
                decision = str(response.get("decision", "")).lower()
                if decision not in {"retry", "cancel"}:
                    raise ValueError("decision must be retry or cancel")
            except (TypeError, ValueError, KeyError) as exc:
                pending["payload"]["validation_error"] = str(exc)
                continue
            break
        updates: dict[str, Any] = {
            "retry_decision": decision,
            "pending_interaction": None,
            "updated_at": self._now(),
        }
        if decision == "cancel":
            route = StageRoute.model_validate(state["current_route"])
            previous = ControlStageOutcome.model_validate(
                state["stage_outcomes"][route.agent_id]
            )
            updates["pending_control_outcome"] = ControlStageOutcome(
                stage=route.stage,
                agent_id=route.agent_id,
                outcome=ControlOutcomeType.COMPLETED,
                status=StageStatus.CANCELLED,
                idempotency_key=previous.idempotency_key,
                operation_ref=previous.operation_ref,
                errors=[
                    ControlError(
                        category="USER_CANCELLED_RETRY",
                        operation="retry",
                        public_message="User cancelled the retry",
                    )
                ],
            ).model_dump(mode="json")
        return updates

    @staticmethod
    def route_after_retry(state: OrchestratorState) -> str:
        return "retry" if state.get("retry_decision") == "retry" else "cancel"

    def generate_report(self, state: OrchestratorState) -> dict[str, Any]:
        plan = ExecutionPlan.model_validate(state["execution_plan"])
        stages: dict[str, dict[str, Any]] = {}
        all_errors: list[dict[str, Any]] = []
        all_warnings: list[str] = []
        valid_results = 0
        required_failure = False
        optional_degraded = False
        resumable_block = False
        user_cancelled_stage = False
        for route in plan.routes:
            payload = state.get("stage_outcomes", {}).get(route.agent_id)
            stage_plan = state.get("stage_plan_refs", {}).get(
                route.agent_id, {}
            )
            if payload:
                outcome = ControlStageOutcome.model_validate(payload)
                status = outcome.status
                summary = outcome.summary
                errors = [
                    error.model_dump(mode="json") for error in outcome.errors
                ]
                if any(
                    error["category"].startswith("USER_CANCELLED")
                    or error["category"] == "USER_REJECTED_STAGE"
                    for error in errors
                ):
                    user_cancelled_stage = True
                if status in {StageStatus.SUCCEEDED, StageStatus.PARTIAL}:
                    valid_results += 1
                if status in {
                    StageStatus.BLOCKED_MISSING_INPUT,
                    StageStatus.CAPABILITY_UNAVAILABLE,
                } and state.get("run_mode") == "direct-stage":
                    resumable_block = True
                elif route.required and status not in {
                    StageStatus.SUCCEEDED,
                    StageStatus.PARTIAL,
                    StageStatus.SKIPPED,
                }:
                    required_failure = True
                elif not route.required and status in {
                    StageStatus.PARTIAL,
                    StageStatus.PERMANENT_FAILED,
                    StageStatus.CANCELLED,
                    StageStatus.CAPABILITY_UNAVAILABLE,
                }:
                    optional_degraded = True
                all_errors.extend(errors)
                all_warnings.extend(summary.get("warnings", []))
                stages[route.agent_id] = {
                    "stage": route.stage.value,
                    "required": route.required,
                    "disposition": route.disposition.value,
                    "status": status.value,
                    "native_result_uri": outcome.native_result_uri,
                    "native_result_sha256": outcome.native_result_sha256,
                    "stage_plan_uri": stage_plan.get("uri"),
                    "stage_plan_sha256": stage_plan.get("sha256"),
                    "operation_ref": outcome.operation_ref,
                    "external_job_ref": outcome.external_job_ref,
                    "candidate_ids": summary.get("candidate_ids", []),
                    "metrics": summary.get("metrics", {}),
                    "warnings": summary.get("warnings", []),
                    "errors": errors,
                    "is_mock": summary.get(
                        "is_mock", route.capability.is_mock
                    ),
                }
            else:
                status = (
                    StageStatus.SKIPPED
                    if route.disposition is StageDisposition.SKIPPED
                    else StageStatus.PENDING
                )
                stages[route.agent_id] = {
                    "stage": route.stage.value,
                    "required": route.required,
                    "disposition": route.disposition.value,
                    "status": status.value,
                    "native_result_uri": None,
                    "native_result_sha256": None,
                    "stage_plan_uri": stage_plan.get("uri"),
                    "stage_plan_sha256": stage_plan.get("sha256"),
                    "operation_ref": None,
                    "external_job_ref": None,
                    "candidate_ids": [],
                    "metrics": {},
                    "warnings": [],
                    "errors": [],
                    "is_mock": route.capability.is_mock,
                }
        if state.get("retry_decision") == "cancel":
            final_status = RunStatus.CANCELLED
        elif user_cancelled_stage and not valid_results:
            final_status = RunStatus.CANCELLED
        elif user_cancelled_stage and valid_results:
            final_status = RunStatus.PARTIAL
        elif resumable_block:
            final_status = RunStatus.PAUSED
        elif required_failure:
            final_status = RunStatus.FAILED
        elif optional_degraded and valid_results:
            final_status = RunStatus.PARTIAL
        elif any(
            stage["status"] == StageStatus.PARTIAL.value
            for stage in stages.values()
        ):
            final_status = RunStatus.PARTIAL
        else:
            final_status = RunStatus.SUCCEEDED
        retrieval_source = SourceDatabase(
            state.get(
                "retrieval_source",
                SourceDatabase.MATERIALS_PROJECT.value,
            )
        )
        retrieval_source_label = (
            "Materials Project"
            if retrieval_source is SourceDatabase.MATERIALS_PROJECT
            else "NOMAD"
        )
        report = {
            "schema_version": ORCHESTRATOR_REPORT_VERSION,
            "project_id": state["project_id"],
            "run_id": state["run_id"],
            "status": final_status.value,
            "requirement": {
                "revision": state.get("requirement_revision"),
                "artifact_uri": state.get("requirement_artifact_uri"),
                "artifact_sha256": state.get(
                    "requirement_artifact_sha256"
                ),
            },
            "execution_plan": {
                "artifact_uri": state.get("execution_plan_uri"),
                "artifact_sha256": state.get("execution_plan_sha256"),
                "routes": [
                    {
                        "stage": route.stage.value,
                        "agent_id": route.agent_id,
                        "required": route.required,
                        "disposition": route.disposition.value,
                        "reason": route.reason,
                    }
                    for route in plan.routes
                ],
            },
            "stages": stages,
            "evidence_statement": (
                f"{retrieval_source_label} retrieval evidence at L1 is preserved. "
                "Fixture/mock runners are control-flow tests only and do not "
                "raise scientific evidence."
            ),
            "generated_at": state["updated_at"],
        }
        json_ref = self.store.write_json(
            f"reports/{state['run_id']}/report.json",
            report,
            immutable=True,
        )
        markdown_ref = self.store.write_text(
            f"reports/{state['run_id']}/report.md",
            _report_markdown(report),
            "text/markdown",
            immutable=True,
        )
        self.repository.update_run(
            state["run_id"],
            status=final_status,
            report_uri=markdown_ref.uri,
            clear_current_stage=True,
        )
        self.repository.append_event(
            event_key=f"{state['run_id']}:run:final:{json_ref.sha256}",
            run_id=state["run_id"],
            event_type="RUN_FINALIZED",
            payload={
                "status": final_status.value,
                "report_uri": markdown_ref.uri,
                "report_sha256": markdown_ref.sha256,
            },
        )
        return {
            "run_status": final_status.value,
            "current_stage": None,
            "report_uri": markdown_ref.uri,
            "report_sha256": markdown_ref.sha256,
            "warnings": all_warnings,
            "errors": all_errors,
            "pending_interaction": None,
            "updated_at": self._now(),
        }

    def _write_stage_input_snapshot(
        self, context: StageExecutionContext
    ) -> ArtifactPointer:
        payload = {
            "schema_version": "orchestrator-stage-input-v2",
            "project_id": context.project_id,
            "run_id": context.run_id,
            "requirement_revision": context.requirement_revision,
            "stage": context.stage.value,
            "agent_id": context.agent_id,
            "attempt": context.attempt,
            "requirement_artifact": (
                context.requirement_artifact.model_dump(mode="json")
            ),
            "input_artifacts": {
                key: value.model_dump(mode="json")
                for key, value in sorted(context.input_artifacts.items())
            },
            "capability": context.capability.model_dump(mode="json"),
        }
        ref = self.store.write_json(
            (
                f"plans/{context.run_id}/stages/{context.stage.value}/"
                f"attempt-{context.attempt}.input.json"
            ),
            payload,
            immutable=True,
        )
        return ArtifactPointer(uri=ref.uri, sha256=ref.sha256)

    def _validate_prepared_stage_plan(
        self,
        prepared: PreparedStagePlan,
        context: StageExecutionContext,
        input_snapshot_sha256: str,
    ) -> None:
        expected = (
            context.project_id,
            context.run_id,
            context.requirement_revision,
            context.stage,
            context.agent_id,
            context.attempt,
            context.input_snapshot.uri if context.input_snapshot else None,
            input_snapshot_sha256,
        )
        actual = (
            prepared.project_id,
            prepared.run_id,
            prepared.requirement_revision,
            prepared.stage,
            prepared.agent_id,
            prepared.attempt,
            prepared.input_snapshot_uri,
            prepared.input_snapshot_sha256,
        )
        if actual != expected:
            raise ValueError(
                "prepared stage plan does not match its execution context"
            )

    def _load_prepared_stage_plan(
        self,
        state: OrchestratorState,
        route: StageRoute,
        attempt: int,
    ) -> tuple[PreparedStagePlan, ArtifactPointer]:
        uri = state.get("prepared_stage_plan_uri")
        sha256 = state.get("prepared_stage_plan_sha256")
        if not uri or not sha256:
            pointer = state.get("stage_plan_refs", {}).get(route.agent_id, {})
            uri = pointer.get("uri")
            sha256 = pointer.get("sha256")
        if not uri or not sha256:
            record = self.repository.get_stage_attempt(
                state["run_id"], route.stage, attempt
            )
            if record is not None:
                uri = record.get("plan_uri")
                sha256 = record.get("plan_sha256")
        if (
            not uri
            or not sha256
            or not self.store.exists_with_hash(uri, sha256)
        ):
            raise ValueError("prepared stage plan failed integrity check")
        prepared = PreparedStagePlan.model_validate(self.store.read_json(uri))
        context = self._stage_context(state, route, attempt).model_copy(
            update={
                "input_snapshot": ArtifactPointer(
                    uri=prepared.input_snapshot_uri,
                    sha256=prepared.input_snapshot_sha256,
                )
            }
        )
        self._validate_prepared_stage_plan(
            prepared, context, prepared.input_snapshot_sha256
        )
        if not self.store.exists_with_hash(
            prepared.input_snapshot_uri,
            prepared.input_snapshot_sha256,
        ):
            raise ValueError("stage input snapshot failed integrity check")
        if not self.store.exists_with_hash(
            prepared.native_plan_uri,
            prepared.native_plan_sha256,
        ):
            raise ValueError("native stage plan failed integrity check")
        return prepared, ArtifactPointer(uri=uri, sha256=sha256)

    @staticmethod
    def _stage_operation_key(prepared: PreparedStagePlan) -> str:
        return _stable_id(
            "operation",
            prepared.project_id,
            prepared.run_id,
            prepared.stage.value,
            str(prepared.attempt),
            prepared.operation_input_sha256,
        )

    def _blocked_outcome(
        self,
        route: StageRoute,
        status: StageStatus,
        validation: StageInputValidation | None = None,
    ) -> ControlStageOutcome:
        category = (validation.error_code if validation else None) or {
            StageStatus.BLOCKED_MISSING_INPUT: "BLOCKED_MISSING_INPUT",
            StageStatus.CAPABILITY_UNAVAILABLE: "CAPABILITY_UNAVAILABLE",
            StageStatus.PERMANENT_FAILED: "INVALID_STAGE_INPUT",
        }[status]
        detail = route.reason
        if validation:
            detail = "; ".join(
                [
                    *validation.errors,
                    *[
                        f"missing {field}"
                        for field in validation.missing_fields
                    ],
                    *validation.remediation,
                ]
            ) or detail
        return ControlStageOutcome(
            stage=route.stage,
            agent_id=route.agent_id,
            outcome=(
                ControlOutcomeType.FAILED
                if status is StageStatus.PERMANENT_FAILED
                else ControlOutcomeType.BLOCKED
            ),
            status=status,
            idempotency_key=_stable_id(
                "operation",
                route.agent_id,
                status.value,
                _sha256(route.model_dump(mode="json")),
            ),
            errors=[
                ControlError(
                    category=category,
                    operation="validate_stage_input",
                    public_message=detail,
                )
            ],
        )

    @staticmethod
    def _invalid_stage_plan_outcome(
        context: StageExecutionContext,
        input_snapshot_sha256: str,
        exc: Exception,
    ) -> ControlStageOutcome:
        return ControlStageOutcome(
            stage=context.stage,
            agent_id=context.agent_id,
            outcome=ControlOutcomeType.FAILED,
            status=StageStatus.PERMANENT_FAILED,
            idempotency_key=_stable_id(
                "operation",
                context.project_id,
                context.run_id,
                context.stage.value,
                str(context.attempt),
                "invalid-plan",
                input_snapshot_sha256,
            ),
            errors=[
                ControlError(
                    category="INVALID_PLAN",
                    operation="prepare_stage_plan",
                    public_message=(
                        "runner produced an invalid stage plan "
                        f"({type(exc).__name__})"
                    ),
                )
            ],
        )

    def _stage_context(
        self,
        state: OrchestratorState,
        route: StageRoute,
        attempt: int,
    ) -> StageExecutionContext:
        inputs = dict(route.input_artifacts)
        for payload in state.get("stage_outcomes", {}).values():
            outcome = ControlStageOutcome.model_validate(payload)
            for name, pointer in outcome.summary.get("artifacts", {}).items():
                inputs.setdefault(name, ArtifactPointer.model_validate(pointer))
        return StageExecutionContext(
            project_id=state["project_id"],
            run_id=state["run_id"],
            stage=route.stage,
            agent_id=route.agent_id,
            attempt=attempt,
            requirement_revision=state["requirement_revision"],
            requirement_artifact=ArtifactPointer(
                uri=state["requirement_artifact_uri"],
                sha256=state["requirement_artifact_sha256"],
            ),
            input_artifacts=inputs,
            capability=route.capability,
        )

    @staticmethod
    def _next_attempt(
        state: OrchestratorState, route: StageRoute
    ) -> int:
        return state.get("retry_counters", {}).get(route.agent_id, 0) + 1

    def _has_runner(self, stage: StageId) -> bool:
        return stage is StageId.RETRIEVAL or self.runners.has_runner(stage)

    def _runner(
        self,
        state: OrchestratorState,
        context: StageExecutionContext,
    ) -> StageRunner:
        if context.stage is StageId.RETRIEVAL:
            policy = retrieval_policy_for_source(
                state.get(
                    "retrieval_source",
                    SourceDatabase.MATERIALS_PROJECT.value,
                )
            )
            return Agent01RunnerAdapter(
                native_runner=self._agent01_runner(state, policy),
                artifact_store=self.store,
                capability=context.capability,
            )
        return self.runners.runner(context)

    def _agent01_runner(
        self, state: OrchestratorState, policy: RetrievalPolicy
    ) -> RetrievalStageRunner:
        fixture_uri = state.get("retrieval_fixture_uri")
        if fixture_uri:
            fixture = self.store.read_json(fixture_uri)
            adapter = InMemoryMaterialsAdapter(
                fixture["documents"],
                database_version=fixture.get(
                    "database_version", "orchestrator-fixture-v1"
                ),
                task_metadata=fixture.get("task_metadata", {}),
                source_database=policy.source_database,
            )
        elif policy.source_database is SourceDatabase.NOMAD:
            adapter = NomadAdapter()
        else:
            adapter = MaterialsProjectAdapter()
        return RetrievalStageRunner(
            adapter=adapter,
            artifact_store=self.store,
            policy=policy,
            clock=self.clock.now,
        )


def _validate_interaction_response(
    pending: dict[str, Any], response: Any
) -> None:
    if not isinstance(response, dict):
        raise TypeError("interaction response must be a JSON object")
    supplied = response.get("interaction_id")
    if supplied != pending["interaction_id"]:
        raise ValueError("interaction_id does not match the pending interaction")
    approval_id = pending.get("approval_id")
    if approval_id and response.get("approval_id") != approval_id:
        raise ValueError("approval_id does not match the pending approval")


def _stable_id(prefix: str, *parts: str) -> str:
    payload = ":".join(parts).encode("utf-8")
    return f"{prefix}_{hashlib.sha256(payload).hexdigest()[:24]}"


def _sha256(payload: Any) -> str:
    return hashlib.sha256(canonical_json_bytes(payload)).hexdigest()


def _report_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Material Screening Run Report",
        "",
        f"- Run: `{report['run_id']}`",
        f"- Status: `{report['status']}`",
        f"- Requirement revision: `{report['requirement']['revision']}`",
        "",
        "## Stage summary",
        "",
    ]
    for agent_id, stage in report["stages"].items():
        lines.append(
            f"- `{agent_id}` ({stage['stage']}): `{stage['status']}` "
            f"[{stage['disposition']}]"
        )
    retrieval = report["stages"]["agent01"]
    lines.extend(
        [
            "",
            "## Retrieval metrics",
            "",
        ]
    )
    if retrieval["metrics"]:
        lines.extend(
            f"- `{key}`: `{value}`"
            for key, value in sorted(retrieval["metrics"].items())
        )
    else:
        lines.append("- No metrics were produced.")
    lines.extend(
        [
            "",
            "## Evidence statement",
            "",
            report["evidence_statement"],
            "",
        ]
    )
    warnings = [
        warning
        for stage in report["stages"].values()
        for warning in stage["warnings"]
    ]
    errors = [
        error
        for stage in report["stages"].values()
        for error in stage["errors"]
    ]
    if warnings:
        lines.extend(
            ["## Warnings", "", *[f"- {warning}" for warning in warnings], ""]
        )
    if errors:
        lines.extend(
            [
                "## Errors",
                "",
                *[
                    f"- `{error['category']}`: {error['public_message']}"
                    for error in errors
                ],
                "",
            ]
        )
    return "\n".join(lines)
