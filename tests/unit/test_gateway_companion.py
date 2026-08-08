from __future__ import annotations

import hashlib
from dataclasses import dataclass, replace
from typing import Any

import pytest
from pydantic import ValidationError

from material_agent.gateway.companion import (
    CompanionAdapterError,
    OfflineInspirationCompanionAdapter,
    PreparedInspirationRun,
    ProjectedInspirationResult,
    prepared_execution_manifest_sha256,
    requirement_freeze_prompt,
)
from material_agent.gateway.models import (
    AnswerActionV1,
    ApprovalInteractionV1,
    ApproveActionV1,
    CancelActionV1,
    CancelledStateV1,
    GatewayResultRecordV1,
    InspirationBundleSummaryV1,
    InspirationConstraintsV1,
    InspirationRunRequestV1,
    InteractionRequiredStateV1,
    PartialStateV1,
    RejectActionV1,
    SucceededStateV1,
    gateway_result_sha256,
    inspiration_report_uri,
    inspiration_run_id,
)
from material_agent.inspiration.models import (
    ArtifactPointerV1,
    ComponentSnapshotV1,
    CostLedgerV1,
    InspirationBundleV1,
    InspirationInputV1,
    InspirationOutcome,
    InspirationStageResultV1,
    ParentCandidateRefV1,
    TagDefinitionV1,
    TagGraphV1,
    TagKind,
)
from material_agent.inspiration.policy import InspirationPolicyV1


def _artifact(name: str, *, payload: bytes | None = None) -> ArtifactPointerV1:
    content = payload if payload is not None else name.encode("utf-8")
    return ArtifactPointerV1(
        uri=f"artifact://fixtures/{name}",
        sha256=hashlib.sha256(content).hexdigest(),
        size_bytes=len(content),
    )


def _request(submission_id: str = "submission-companion") -> InspirationRunRequestV1:
    return InspirationRunRequestV1(
        submission_id=submission_id,
        goal="Find a bounded set of structurally valid hypotheses",
        constraints=InspirationConstraintsV1(),
    )


def _prepared(run_id: str) -> PreparedInspirationRun:
    component_sha = hashlib.sha256(b"component").hexdigest()
    inspiration_input = InspirationInputV1(
        project_id="project-companion",
        request_id="request-companion",
        run_id=run_id,
        requirement_revision=1,
        requirement_artifact=_artifact("requirement.json"),
        parent_candidates=(
            ParentCandidateRefV1(
                candidate_id="parent-1",
                structure_id="structure-parent-1",
                structure_artifact=_artifact("parent-1.cif"),
            ),
        ),
        policy_artifact=_artifact("policy.json"),
        tag_graph_artifact=_artifact("tag-graph.json"),
        transformation_registry_artifact=_artifact("transformations.json"),
        search_adapter=ComponentSnapshotV1(
            component_id="offline-search",
            version="1",
            implementation_sha256=component_sha,
        ),
        vectorizer=ComponentSnapshotV1(
            component_id="signed-hashing",
            version="1",
            implementation_sha256=component_sha,
        ),
    )
    graph = TagGraphV1(
        graph_id="graph-companion",
        graph_version="1",
        tags=(
            TagDefinitionV1(
                tag_id="target-flat-band",
                kind=TagKind.PROPERTY,
                label="flat band",
                description="A bounded target used by the offline fixture.",
                query_terms=("flat band",),
            ),
        ),
    )
    return PreparedInspirationRun(
        inspiration_input=inspiration_input,
        policy=InspirationPolicyV1(),
        tag_graph=graph,
        target_tag_ids=("target-flat-band",),
    )


@dataclass(frozen=True)
class _RunnerResult:
    stage_result: InspirationStageResultV1
    stage_result_artifact: ArtifactPointerV1
    bundle: InspirationBundleV1


def _runner_result(prepared: PreparedInspirationRun) -> _RunnerResult:
    run_id = prepared.inspiration_input.run_id
    report_payload = b"# deterministic companion report\n"
    report = ArtifactPointerV1(
        uri=inspiration_report_uri(run_id),
        sha256=hashlib.sha256(report_payload).hexdigest(),
        size_bytes=len(report_payload),
        media_type="text/markdown",
    )
    bundle = InspirationBundleV1(
        bundle_id="bundle-companion",
        request_id=prepared.inspiration_input.request_id,
        run_id=run_id,
        outcome=InspirationOutcome.SCIENTIFIC_NO_MATCH,
        limitations=("The bounded offline fixture contained no supported route.",),
        next_validation_steps=("Expand the curated fixture before drawing conclusions.",),
        cost_ledger=CostLedgerV1(),
        lineage_artifacts=(_artifact("search-lineage.json"),),
    )
    stage_result = InspirationStageResultV1(
        result_id="stage-result-companion",
        project_id=prepared.inspiration_input.project_id,
        request_id=prepared.inspiration_input.request_id,
        run_id=run_id,
        outcome=InspirationOutcome.SCIENTIFIC_NO_MATCH,
        input_snapshot_artifact=_artifact("input-snapshot.json"),
        policy_artifact=_artifact("effective-policy.json"),
        bundle_artifact=_artifact("bundle.json"),
        report_artifact=report,
        cost_ledger_artifact=_artifact("cost-ledger.json"),
        intermediate_artifacts=(_artifact("queries.json"),),
    )
    return _RunnerResult(
        stage_result=stage_result,
        stage_result_artifact=ArtifactPointerV1(
            uri=f"artifact://stages/inspiration/{run_id}/stage_result.json",
            sha256=hashlib.sha256(b"stage result").hexdigest(),
            size_bytes=len(b"stage result"),
            media_type="application/json",
        ),
        bundle=bundle,
    )


def _projection(runner_result: _RunnerResult) -> ProjectedInspirationResult:
    stage_result = runner_result.stage_result
    return ProjectedInspirationResult(
        result=GatewayResultRecordV1(
            run_id=stage_result.run_id,
            report_uri=stage_result.report_artifact.uri,
            authoritative_sha256=stage_result.report_artifact.sha256,
            bundle=InspirationBundleSummaryV1(
                outcome="SCIENTIFIC_NO_MATCH",
                limitations=("No fixture-supported candidate was found.",),
                next_validation_steps=("Curate additional bounded evidence.",),
            ),
            validation_boundaries=(
                "Absence from an offline fixture is not evidence of scientific absence.",
            ),
        )
    )


class _Preparer:
    def __init__(self, prepared: PreparedInspirationRun) -> None:
        self.prepared = prepared
        self.calls: list[tuple[str, InspirationRunRequestV1]] = []

    def prepare(
        self,
        *,
        run_id: str,
        request: InspirationRunRequestV1,
    ) -> PreparedInspirationRun:
        self.calls.append((run_id, request))
        return self.prepared


class _Runner:
    def __init__(self, result: _RunnerResult) -> None:
        self.result = result
        self.execution_components = (
            ComponentSnapshotV1(
                component_id="test-transformation-engine",
                version="1",
                implementation_sha256=hashlib.sha256(
                    b"test-transformation-engine-v1"
                ).hexdigest(),
            ),
        )
        self.calls: list[dict[str, Any]] = []

    def run(self, **arguments: Any) -> _RunnerResult:
        self.calls.append(arguments)
        return self.result


class _Projector:
    def __init__(self, projection: ProjectedInspirationResult) -> None:
        self.projection = projection
        self.calls: list[dict[str, Any]] = []

    def project(self, **arguments: Any) -> ProjectedInspirationResult:
        self.calls.append(arguments)
        return self.projection


def _adapter_components(
    *,
    runner_result: _RunnerResult | None = None,
    projection: ProjectedInspirationResult | None = None,
) -> tuple[
    InspirationRunRequestV1,
    OfflineInspirationCompanionAdapter,
    _Preparer,
    _Runner,
    _Projector,
]:
    request = _request()
    run_id = inspiration_run_id(request.submission_id)
    prepared = _prepared(run_id)
    actual_runner_result = runner_result or _runner_result(prepared)
    actual_projection = projection or _projection(actual_runner_result)
    preparer = _Preparer(prepared)
    runner = _Runner(actual_runner_result)
    projector = _Projector(actual_projection)
    adapter = OfflineInspirationCompanionAdapter(
        runner=runner,
        preparer=preparer,
        projector=projector,
    )
    return request, adapter, preparer, runner, projector


def test_start_is_deterministic_and_does_not_execute_the_runner() -> None:
    request, adapter, preparer, runner, projector = _adapter_components()
    run_id = inspiration_run_id(request.submission_id)

    first = adapter.start(run_id=run_id, request=request)
    second = adapter.start(run_id=run_id, request=request)

    assert first == second
    assert isinstance(first.state, InteractionRequiredStateV1)
    assert isinstance(first.state.interaction, ApprovalInteractionV1)
    assert first.state.interaction.approval_kind == "requirement_freeze"
    assert first.state.interaction.prompt == requirement_freeze_prompt(
        request=request,
        prepared=preparer.prepared,
    )
    assert "offline fixture execution with no public-network access" in (
        first.state.interaction.prompt
    )
    assert "offline body-fixture request budget=20" in (
        first.state.interaction.prompt
    )
    assert "full-PDF reads=0" in first.state.interaction.prompt
    assert "internal model calls=0" in first.state.interaction.prompt
    assert first.state.interaction.input_sha256 == prepared_execution_manifest_sha256(
        request=request,
        prepared=preparer.prepared,
        execution_components=runner.execution_components,
    )
    assert len(preparer.calls) == 2
    assert runner.calls == []
    assert projector.calls == []

    with pytest.raises(CompanionAdapterError, match="submission identity"):
        adapter.start(run_id="inspiration-wrong", request=request)


def test_requirement_freeze_prompt_rejects_wrong_input_types() -> None:
    request = _request()
    prepared = _prepared(inspiration_run_id(request.submission_id))

    with pytest.raises(TypeError, match="request must"):
        requirement_freeze_prompt(request=object(), prepared=prepared)  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="prepared must"):
        requirement_freeze_prompt(request=request, prepared=object())  # type: ignore[arg-type]


def test_only_exact_confirmed_approval_executes_once() -> None:
    request, adapter, preparer, runner, projector = _adapter_components()
    run_id = inspiration_run_id(request.submission_id)
    started = adapter.start(run_id=run_id, request=request)
    assert isinstance(started.state, InteractionRequiredStateV1)
    interaction_id = started.state.interaction.interaction_id

    terminal = adapter.act(
        run_id=run_id,
        request=request,
        state=started.state,
        action=ApproveActionV1(
            interaction_id=interaction_id,
            confirmed_by_user=True,
        ),
    )

    assert isinstance(terminal.state, SucceededStateV1)
    assert terminal.result is not None
    assert terminal.result.run_id == run_id
    assert terminal.state.result_sha256 == gateway_result_sha256(terminal.result)
    assert len(preparer.calls) == 2
    assert len(runner.calls) == len(projector.calls) == 1
    assert runner.calls[0]["inspiration_input"] == preparer.prepared.inspiration_input
    assert runner.calls[0]["target_tag_ids"] == ("target-flat-band",)

    with pytest.raises(ValidationError):
        ApproveActionV1.model_validate(
            {
                "interaction_id": interaction_id,
                "confirmed_by_user": False,
            }
        )


def test_approved_execution_manifest_drift_fails_before_runner() -> None:
    request, adapter, preparer, runner, projector = _adapter_components()
    run_id = inspiration_run_id(request.submission_id)
    started = adapter.start(run_id=run_id, request=request)
    assert isinstance(started.state, InteractionRequiredStateV1)
    preparer.prepared = replace(
        preparer.prepared,
        inspiration_input=preparer.prepared.inspiration_input.model_copy(
            update={"request_id": "request-changed-after-approval"}
        ),
    )

    with pytest.raises(CompanionAdapterError, match="manifest changed"):
        adapter.act(
            run_id=run_id,
            request=request,
            state=started.state,
            action=ApproveActionV1(
                interaction_id=started.state.interaction.interaction_id,
                confirmed_by_user=True,
            ),
        )

    assert runner.calls == []
    assert projector.calls == []


def test_transformation_engine_snapshot_drift_fails_before_runner() -> None:
    request, adapter, _preparer, runner, projector = _adapter_components()
    run_id = inspiration_run_id(request.submission_id)
    started = adapter.start(run_id=run_id, request=request)
    assert isinstance(started.state, InteractionRequiredStateV1)
    runner.execution_components = (
        ComponentSnapshotV1(
            component_id="test-transformation-engine",
            version="2",
            implementation_sha256=hashlib.sha256(
                b"changed-transformation-engine-v2"
            ).hexdigest(),
        ),
    )

    with pytest.raises(CompanionAdapterError, match="manifest changed"):
        adapter.act(
            run_id=run_id,
            request=request,
            state=started.state,
            action=ApproveActionV1(
                interaction_id=started.state.interaction.interaction_id,
                confirmed_by_user=True,
            ),
        )

    assert runner.calls == []
    assert projector.calls == []


def test_reject_cancel_stale_and_wrong_action_pairs_never_execute() -> None:
    request, adapter, _preparer, runner, _projector = _adapter_components()
    run_id = inspiration_run_id(request.submission_id)
    started = adapter.start(run_id=run_id, request=request)
    assert isinstance(started.state, InteractionRequiredStateV1)
    interaction_id = started.state.interaction.interaction_id

    rejected = adapter.act(
        run_id=run_id,
        request=request,
        state=started.state,
        action=RejectActionV1(
            interaction_id=interaction_id,
            confirmed_by_user=True,
        ),
    )
    cancelled = adapter.act(
        run_id=run_id,
        request=request,
        state=started.state,
        action=CancelActionV1(
            interaction_id=interaction_id,
            confirmed_by_user=True,
        ),
    )

    assert isinstance(rejected.state, CancelledStateV1)
    assert isinstance(cancelled.state, CancelledStateV1)
    assert runner.calls == []

    with pytest.raises(CompanionAdapterError, match="stale"):
        adapter.act(
            run_id=run_id,
            request=request,
            state=started.state,
            action=ApproveActionV1(
                interaction_id="interaction-stale",
                confirmed_by_user=True,
            ),
        )
    with pytest.raises(CompanionAdapterError, match="accepts only"):
        adapter.act(
            run_id=run_id,
            request=request,
            state=started.state,
            action=AnswerActionV1(
                interaction_id=interaction_id,
                answer="yes",
            ),
        )
    assert runner.calls == []


def test_partial_projection_preserves_explicit_warnings() -> None:
    request = _request()
    prepared = _prepared(inspiration_run_id(request.submission_id))
    runner_result = _runner_result(prepared)
    partial_projection = replace(
        _projection(runner_result),
        status="PARTIAL",
        warnings=("One bounded retrieval branch was unavailable.",),
    )
    _request_value, adapter, _preparer, _runner, _projector = _adapter_components(
        runner_result=runner_result,
        projection=partial_projection,
    )
    started = adapter.start(
        run_id=prepared.inspiration_input.run_id,
        request=request,
    )
    assert isinstance(started.state, InteractionRequiredStateV1)

    terminal = adapter.act(
        run_id=prepared.inspiration_input.run_id,
        request=request,
        state=started.state,
        action=ApproveActionV1(
            interaction_id=started.state.interaction.interaction_id,
            confirmed_by_user=True,
        ),
    )

    assert isinstance(terminal.state, PartialStateV1)
    assert terminal.result is not None
    assert terminal.state.result_sha256 == gateway_result_sha256(terminal.result)
    assert terminal.state.warnings == partial_projection.warnings


def test_runner_and_projection_identity_or_report_drift_is_rejected() -> None:
    request = _request()
    run_id = inspiration_run_id(request.submission_id)
    prepared = _prepared(run_id)
    valid_runner_result = _runner_result(prepared)

    wrong_request_result = replace(
        valid_runner_result,
        stage_result=valid_runner_result.stage_result.model_copy(
            update={"request_id": "request-other"}
        ),
    )
    _request_value, adapter, _preparer, _runner, _projector = _adapter_components(
        runner_result=wrong_request_result,
    )
    started = adapter.start(run_id=run_id, request=request)
    assert isinstance(started.state, InteractionRequiredStateV1)
    with pytest.raises(CompanionAdapterError, match="another request"):
        adapter.act(
            run_id=run_id,
            request=request,
            state=started.state,
            action=ApproveActionV1(
                interaction_id=started.state.interaction.interaction_id,
                confirmed_by_user=True,
            ),
        )

    valid_projection = _projection(valid_runner_result)
    wrong_report_projection = replace(
        valid_projection,
        result=valid_projection.result.model_copy(
            update={"report_uri": "artifact://fixtures/wrong-report.md"}
        ),
    )
    _request_value, adapter, _preparer, _runner, _projector = _adapter_components(
        runner_result=valid_runner_result,
        projection=wrong_report_projection,
    )
    started = adapter.start(run_id=run_id, request=request)
    assert isinstance(started.state, InteractionRequiredStateV1)
    with pytest.raises(CompanionAdapterError, match="not bound"):
        adapter.act(
            run_id=run_id,
            request=request,
            state=started.state,
            action=ApproveActionV1(
                interaction_id=started.state.interaction.interaction_id,
                confirmed_by_user=True,
            ),
        )


def test_prepared_target_tags_must_close_over_the_graph() -> None:
    prepared = _prepared(inspiration_run_id("submission-tags"))

    with pytest.raises(ValueError, match="exist in the prepared tag graph"):
        replace(prepared, target_tag_ids=("unknown-tag",))
