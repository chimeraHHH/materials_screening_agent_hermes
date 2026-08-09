"""Injectable adapter from the Gateway lifecycle to ``InspirationRunner``.

This module intentionally has no MCP dependency and imports no concrete runner
implementation.  Preparing authoritative runner inputs and projecting its rich
result into the bounded Gateway DTO are explicit injected boundaries; the
adapter never infers scientific evidence or candidate meaning on its own.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal, Protocol, runtime_checkable

from material_agent.gateway.errors import AdapterContractError
from material_agent.gateway.models import (
    AnswerActionV1,
    ApprovalInteractionV1,
    ApproveActionV1,
    CancelActionV1,
    CancelledStateV1,
    CompanionTransitionV1,
    GatewayResultRecordV1,
    InspirationRunRequestV1,
    InteractionRequiredStateV1,
    PartialStateV1,
    RejectActionV1,
    ResumeActionV1,
    ResumeInteractionV1,
    RetryActionV1,
    RetryInteractionV1,
    RunActionV1,
    RunStateV1,
    SucceededStateV1,
    canonical_sha256,
    gateway_result_sha256,
    inspiration_report_uri,
    inspiration_request_sha256,
    inspiration_run_id,
)
from material_agent.inspiration.component_identity import (
    execution_identity_snapshots,
)
from material_agent.inspiration.models import (
    ArtifactPointerV1,
    ComponentSnapshotV1,
    InspirationBundleV1,
    InspirationInputV1,
    InspirationStageResultV1,
    TagGraphV1,
)
from material_agent.inspiration.policy import InspirationPolicyV1, SearchExecutionMode


class CompanionAdapterError(AdapterContractError):
    """The injected runner boundary violated the companion contract."""


@dataclass(frozen=True)
class PreparedInspirationRun:
    """Frozen arguments for one call to the injected InspirationRunner."""

    inspiration_input: InspirationInputV1
    policy: InspirationPolicyV1
    tag_graph: TagGraphV1
    target_tag_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.inspiration_input, InspirationInputV1):
            raise TypeError("inspiration_input must be InspirationInputV1")
        if not isinstance(self.policy, InspirationPolicyV1):
            raise TypeError("policy must be InspirationPolicyV1")
        if not isinstance(self.tag_graph, TagGraphV1):
            raise TypeError("tag_graph must be TagGraphV1")
        if not isinstance(self.target_tag_ids, tuple) or any(
            not isinstance(tag_id, str) for tag_id in self.target_tag_ids
        ):
            raise TypeError("target_tag_ids must be a tuple of strings")
        if not self.target_tag_ids:
            raise ValueError("at least one target tag is required")
        if len(set(self.target_tag_ids)) != len(self.target_tag_ids):
            raise ValueError("target tag IDs must be unique")
        known_tag_ids = {tag.tag_id for tag in self.tag_graph.tags}
        if not set(self.target_tag_ids).issubset(known_tag_ids):
            raise ValueError("target tag IDs must exist in the prepared tag graph")


def prepared_execution_manifest_sha256(
    *,
    request: InspirationRunRequestV1,
    prepared: PreparedInspirationRun,
    execution_components: tuple[ComponentSnapshotV1, ...],
) -> str:
    """Hash the complete frozen runner input authorized by an approval."""

    if not isinstance(request, InspirationRunRequestV1):
        raise TypeError("request must be InspirationRunRequestV1")
    if not isinstance(prepared, PreparedInspirationRun):
        raise TypeError("prepared must be PreparedInspirationRun")
    if not isinstance(execution_components, tuple) or not execution_components:
        raise ValueError("at least one execution component snapshot is required")
    if any(
        not isinstance(component, ComponentSnapshotV1)
        for component in execution_components
    ):
        raise TypeError("execution components must be ComponentSnapshotV1 values")
    supplied_component_ids = tuple(
        component.component_id for component in execution_components
    )
    if len(supplied_component_ids) != len(set(supplied_component_ids)):
        raise ValueError("execution component IDs must be unique")
    components_by_id = {
        component.component_id: component for component in execution_identity_snapshots()
    }
    for component in execution_components:
        frozen_identity = components_by_id.get(component.component_id)
        if frozen_identity is not None and frozen_identity != component:
            raise ValueError(
                "runner component conflicts with its content-addressed identity: "
                f"{component.component_id}"
            )
        components_by_id[component.component_id] = component
    ordered_components = tuple(
        components_by_id[component_id]
        for component_id in sorted(components_by_id)
    )
    return canonical_sha256(
        {
            "execution_components": ordered_components,
            "gateway_request_sha256": inspiration_request_sha256(request),
            "inspiration_input": prepared.inspiration_input,
            "policy": prepared.policy,
            "schema_version": "materials-inspiration-execution-manifest-v2",
            "tag_graph": prepared.tag_graph,
            "target_tag_ids": prepared.target_tag_ids,
        }
    )


def requirement_freeze_prompt(
    *,
    request: InspirationRunRequestV1,
    prepared: PreparedInspirationRun,
) -> str:
    """Describe the frozen execution's real access and cost boundary to a user."""

    if not isinstance(request, InspirationRunRequestV1):
        raise TypeError("request must be an InspirationRunRequestV1")
    if not isinstance(prepared, PreparedInspirationRun):
        raise TypeError("prepared must be a PreparedInspirationRun")

    policy = prepared.policy
    adapter_id = prepared.inspiration_input.search_adapter.component_id
    if policy.search_mode is SearchExecutionMode.PUBLIC_METADATA_API:
        provider = (
            "Crossref"
            if adapter_id == "crossref-public-adapter"
            else f"the approved {adapter_id} adapter"
        )
        execution_scope = (
            f"bounded public {provider} metadata/abstract network access"
        )
        body_scope = f"article-body fetch requests={policy.fetch.max_requests}"
    else:
        execution_scope = "offline fixture execution with no public-network access"
        body_scope = (
            "offline body-fixture request budget="
            f"{policy.fetch.max_requests}"
        )

    return (
        "Freeze this bounded inspiration request before execution? Approval "
        f"permits {execution_scope} with at most "
        f"{request.constraints.budget.max_search_requests} physical search "
        f"attempts; {body_scope}, full-PDF reads=0, and internal model "
        f"calls={policy.llm.max_calls}."
    )


@dataclass(frozen=True)
class ProjectedInspirationResult:
    """Gateway-safe terminal projection produced by an injected projector."""

    result: GatewayResultRecordV1
    status: Literal["SUCCEEDED", "PARTIAL"] = "SUCCEEDED"
    warnings: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.result, GatewayResultRecordV1):
            raise TypeError("result must be GatewayResultRecordV1")
        if self.status not in {"SUCCEEDED", "PARTIAL"}:
            raise ValueError("projection status must be SUCCEEDED or PARTIAL")
        if not isinstance(self.warnings, tuple) or any(
            not isinstance(warning, str) for warning in self.warnings
        ):
            raise TypeError("projection warnings must be a tuple of strings")
        if self.status == "SUCCEEDED" and self.warnings:
            raise ValueError("successful projection cannot silently drop warnings")
        if self.status == "PARTIAL" and not self.warnings:
            raise ValueError("partial projection requires at least one warning")
        if len(self.warnings) > 32:
            raise ValueError("partial projection warning limit exceeded")
        if any(not warning.strip() or len(warning) > 512 for warning in self.warnings):
            raise ValueError("projection warnings must be bounded non-empty text")


@runtime_checkable
class InspirationRunnerResultLike(Protocol):
    """Structural subset promised by the companion InspirationRunner result."""

    stage_result: InspirationStageResultV1
    stage_result_artifact: ArtifactPointerV1
    bundle: InspirationBundleV1


@runtime_checkable
class CompletedInspirationRunRecovery(Protocol):
    """Optional projector boundary for fail-closed completed-run recovery.

    A production implementation may persist an immutable execution-intent
    binding before the runner starts.  On a later call it may return a fully
    re-verified result only when the exact approved execution manifest and the
    complete authoritative artifact closure still match.  ``None`` means this
    caller bound a pristine run and must execute it once; partial or drifted
    state must raise ``CompanionAdapterError`` instead of returning ``None``.
    """

    def recover_or_bind_completed(
        self,
        *,
        run_id: str,
        request: InspirationRunRequestV1,
        prepared: PreparedInspirationRun,
        execution_manifest_sha256: str,
    ) -> InspirationRunnerResultLike | None: ...


@runtime_checkable
class InspirationRunnerProtocol(Protocol):
    execution_components: tuple[ComponentSnapshotV1, ...]

    def run(
        self,
        *,
        inspiration_input: InspirationInputV1,
        policy: InspirationPolicyV1,
        tag_graph: TagGraphV1,
        target_tag_ids: Sequence[str],
    ) -> InspirationRunnerResultLike: ...


@runtime_checkable
class InspirationRunPreparer(Protocol):
    """Resolve frozen artifacts and policy without expanding Gateway inputs."""

    def prepare(
        self,
        *,
        run_id: str,
        request: InspirationRunRequestV1,
    ) -> PreparedInspirationRun: ...


@runtime_checkable
class InspirationResultProjector(Protocol):
    """Project runner authority into the bounded public Gateway result."""

    def project(
        self,
        *,
        run_id: str,
        request: InspirationRunRequestV1,
        prepared: PreparedInspirationRun,
        runner_result: InspirationRunnerResultLike,
    ) -> ProjectedInspirationResult: ...


class OfflineInspirationCompanionAdapter:
    """Approval-gated, dependency-injected companion adapter.

    ``start`` freezes and hashes the complete prepared execution manifest but
    never executes the runner.  Only an exact, separately authorized ``approve``
    action whose manifest still matches can call the injected runner.  Rejection
    and cancellation are terminal and do not invoke scientific code.
    """

    def __init__(
        self,
        *,
        runner: InspirationRunnerProtocol,
        preparer: InspirationRunPreparer,
        projector: InspirationResultProjector,
    ) -> None:
        self.runner = runner
        self.preparer = preparer
        self.projector = projector

    def start(
        self,
        *,
        run_id: str,
        request: InspirationRunRequestV1,
    ) -> CompanionTransitionV1:
        self._validate_run_identity(run_id, request)
        prepared = self._prepare(run_id=run_id, request=request)
        execution_manifest_sha256 = self.execution_manifest_sha256(
            request=request,
            prepared=prepared,
        )
        interaction_id = self._interaction_id(
            run_id=run_id,
            request_sha256=execution_manifest_sha256,
            kind="requirement-freeze",
        )
        return CompanionTransitionV1(
            state=InteractionRequiredStateV1(
                interaction=ApprovalInteractionV1(
                    interaction_id=interaction_id,
                    approval_kind="requirement_freeze",
                    prompt=requirement_freeze_prompt(
                        request=request,
                        prepared=prepared,
                    ),
                    input_sha256=execution_manifest_sha256,
                    execution_manifest_sha256=execution_manifest_sha256,
                )
            )
        )

    def act(
        self,
        *,
        run_id: str,
        request: InspirationRunRequestV1,
        state: RunStateV1,
        action: RunActionV1,
    ) -> CompanionTransitionV1:
        self._validate_run_identity(run_id, request)
        if not isinstance(state, InteractionRequiredStateV1):
            raise CompanionAdapterError("companion action requires an interaction")
        interaction = state.interaction
        if action.interaction_id != interaction.interaction_id:
            raise CompanionAdapterError("companion interaction is stale")

        if isinstance(action, CancelActionV1):
            return CompanionTransitionV1(
                state=CancelledStateV1(reason="inspiration run cancelled by user")
            )

        if isinstance(interaction, ApprovalInteractionV1):
            if isinstance(action, ApproveActionV1):
                return self._execute(
                    run_id=run_id,
                    request=request,
                    expected_execution_manifest_sha256=interaction.input_sha256,
                )
            if isinstance(action, RejectActionV1):
                return CompanionTransitionV1(
                    state=CancelledStateV1(
                        reason="inspiration requirement freeze rejected by user"
                    )
                )
            raise CompanionAdapterError(
                "approval interaction accepts only approve, reject, or cancel"
            )

        if isinstance(interaction, ResumeInteractionV1):
            if not isinstance(action, ResumeActionV1):
                raise CompanionAdapterError(
                    "resume interaction accepts only resume or cancel"
                )
            return self._execute(run_id=run_id, request=request)

        if isinstance(interaction, RetryInteractionV1):
            if not isinstance(action, RetryActionV1):
                raise CompanionAdapterError(
                    "retry interaction accepts only retry or cancel"
                )
            return self._execute(run_id=run_id, request=request)

        if isinstance(action, AnswerActionV1):
            raise CompanionAdapterError(
                "this offline adapter did not advertise a clarification handler"
            )
        raise CompanionAdapterError("unsupported companion interaction/action pair")

    def _execute(
        self,
        *,
        run_id: str,
        request: InspirationRunRequestV1,
        expected_execution_manifest_sha256: str | None = None,
    ) -> CompanionTransitionV1:
        prepared = self._prepare(run_id=run_id, request=request)
        observed_execution_manifest_sha256 = prepared_execution_manifest_sha256(
            request=request,
            prepared=prepared,
            execution_components=self._execution_components(),
        )
        if (
            expected_execution_manifest_sha256 is not None
            and observed_execution_manifest_sha256
            != expected_execution_manifest_sha256
        ):
            raise CompanionAdapterError(
                "prepared execution manifest changed after approval"
            )

        runner_result: InspirationRunnerResultLike | None = None
        if (
            expected_execution_manifest_sha256 is not None
            and isinstance(self.projector, CompletedInspirationRunRecovery)
        ):
            runner_result = self.projector.recover_or_bind_completed(
                run_id=run_id,
                request=request,
                prepared=prepared,
                execution_manifest_sha256=(
                    observed_execution_manifest_sha256
                ),
            )
        if runner_result is None:
            runner_result = self.runner.run(
                inspiration_input=prepared.inspiration_input,
                policy=prepared.policy,
                tag_graph=prepared.tag_graph,
                target_tag_ids=prepared.target_tag_ids,
            )
        if not isinstance(runner_result, InspirationRunnerResultLike):
            raise CompanionAdapterError("runner returned an invalid result object")
        stage_result = runner_result.stage_result
        stage_result_artifact = runner_result.stage_result_artifact
        bundle = runner_result.bundle
        if (
            not isinstance(stage_result, InspirationStageResultV1)
            or not isinstance(stage_result_artifact, ArtifactPointerV1)
            or not isinstance(bundle, InspirationBundleV1)
        ):
            raise CompanionAdapterError("runner returned invalid result contracts")
        if stage_result.run_id != run_id or bundle.run_id != run_id:
            raise CompanionAdapterError("runner result belongs to another run")
        if stage_result.project_id != prepared.inspiration_input.project_id:
            raise CompanionAdapterError("runner result belongs to another project")
        if (
            stage_result.request_id != prepared.inspiration_input.request_id
            or bundle.request_id != prepared.inspiration_input.request_id
        ):
            raise CompanionAdapterError("runner result belongs to another request")
        if stage_result.outcome != bundle.outcome:
            raise CompanionAdapterError("runner stage and bundle outcomes differ")
        expected_stage_result_uri = (
            f"artifact://stages/inspiration/{run_id}/stage_result.json"
        )
        if stage_result_artifact.uri != expected_stage_result_uri:
            raise CompanionAdapterError(
                "runner stage-result URI is not bound to its run"
            )

        projection = self.projector.project(
            run_id=run_id,
            request=request,
            prepared=prepared,
            runner_result=runner_result,
        )
        if not isinstance(projection, ProjectedInspirationResult):
            raise CompanionAdapterError("projector returned an invalid projection")
        result = projection.result
        expected_uri = inspiration_report_uri(run_id)
        if result.run_id != run_id:
            raise CompanionAdapterError("projected result belongs to another run")
        if result.bundle.outcome != bundle.outcome.value:
            raise CompanionAdapterError("runner and projected bundle outcomes differ")
        runner_candidate_ids = tuple(
            candidate.candidate_id for candidate in bundle.selected_candidates
        )
        projected_candidate_ids = tuple(
            candidate.candidate_id for candidate in result.bundle.selected_candidates
        )
        if projected_candidate_ids != runner_candidate_ids:
            raise CompanionAdapterError("runner and projected candidate identities differ")
        if result.report_uri != expected_uri:
            raise CompanionAdapterError("projected report URI is not bound to its run")
        if stage_result.report_artifact.uri != result.report_uri:
            raise CompanionAdapterError("runner and projected report URIs differ")
        if stage_result.report_artifact.sha256 != result.authoritative_sha256:
            raise CompanionAdapterError("runner and projected report hashes differ")
        result_sha256 = gateway_result_sha256(result)

        if projection.status == "PARTIAL":
            state: RunStateV1 = PartialStateV1(
                report_uri=result.report_uri,
                authoritative_sha256=result.authoritative_sha256,
                result_sha256=result_sha256,
                warnings=projection.warnings,
            )
        else:
            state = SucceededStateV1(
                report_uri=result.report_uri,
                authoritative_sha256=result.authoritative_sha256,
                result_sha256=result_sha256,
            )
        return CompanionTransitionV1(state=state, result=result)

    def execution_manifest_sha256(
        self,
        *,
        request: InspirationRunRequestV1,
        prepared: PreparedInspirationRun,
    ) -> str:
        return prepared_execution_manifest_sha256(
            request=request,
            prepared=prepared,
            execution_components=self._execution_components(),
        )

    def _execution_components(self) -> tuple[ComponentSnapshotV1, ...]:
        components = getattr(self.runner, "execution_components", None)
        if not isinstance(components, tuple) or not components:
            raise CompanionAdapterError(
                "runner does not expose frozen execution component snapshots"
            )
        if any(
            not isinstance(component, ComponentSnapshotV1)
            for component in components
        ):
            raise CompanionAdapterError(
                "runner execution component snapshots are invalid"
            )
        component_ids = tuple(component.component_id for component in components)
        if len(component_ids) != len(set(component_ids)):
            raise CompanionAdapterError(
                "runner execution component snapshot IDs are not unique"
            )
        return tuple(sorted(components, key=lambda component: component.component_id))

    def _prepare(
        self,
        *,
        run_id: str,
        request: InspirationRunRequestV1,
    ) -> PreparedInspirationRun:
        prepared = self.preparer.prepare(run_id=run_id, request=request)
        if not isinstance(prepared, PreparedInspirationRun):
            raise CompanionAdapterError("preparer returned an invalid run payload")
        if prepared.inspiration_input.run_id != run_id:
            raise CompanionAdapterError("prepared runner input belongs to another run")
        return prepared

    @staticmethod
    def _validate_run_identity(
        run_id: str,
        request: InspirationRunRequestV1,
    ) -> None:
        if run_id != inspiration_run_id(request.submission_id):
            raise CompanionAdapterError("run ID does not match submission identity")

    @staticmethod
    def _interaction_id(
        *,
        run_id: str,
        request_sha256: str,
        kind: str,
    ) -> str:
        digest = canonical_sha256(
            {
                "run_id": run_id,
                "request_sha256": request_sha256,
                "kind": kind,
            }
        )[:24]
        return f"interaction-{digest}"
