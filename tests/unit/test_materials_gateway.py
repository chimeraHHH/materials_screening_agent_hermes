from __future__ import annotations

import hashlib
from collections.abc import Callable

import pytest
from pydantic import ValidationError

from material_agent.gateway import (
    ActionAuthorizationError,
    ApprovalInteractionV1,
    ApproveActionV1,
    CandidateSummaryV1,
    CompanionTransitionV1,
    EvidenceReferenceV1,
    FailedStateV1,
    GatewayRepository,
    GatewayResultRecordV1,
    IllegalActionError,
    InMemoryArtifactStore,
    InMemoryGatewayRepository,
    InspirationBundleSummaryV1,
    InspirationCompanionAdapter,
    InspirationRunRequestV1,
    InteractionRequiredStateV1,
    MaterialsGatewayService,
    MaterialsRunActRequestV1,
    MaterialsRunViewV1,
    PartialStateV1,
    ResultIntegrityError,
    ResultUnavailableError,
    RunningStateV1,
    StaleInteractionError,
    SubmissionConflictError,
    SucceededStateV1,
    gateway_result_sha256,
    inspiration_report_uri,
    inspiration_run_id,
)
from material_agent.gateway.models import RunActionV1, RunStateV1


StartFactory = Callable[[str, InspirationRunRequestV1], CompanionTransitionV1 | dict]
ActionFactory = Callable[
    [str, InspirationRunRequestV1, RunStateV1, RunActionV1],
    CompanionTransitionV1 | dict,
]


class _AllowAllTestAuthorizer:
    def authorize_and_consume(self, **_arguments: object) -> None:
        return None


class _InMemoryCompanionAdapter:
    def __init__(
        self,
        *,
        start_factory: StartFactory,
        action_factory: ActionFactory | None = None,
    ) -> None:
        self.start_factory = start_factory
        self.action_factory = action_factory
        self.start_calls = 0
        self.action_calls = 0

    def start(
        self, *, run_id: str, request: InspirationRunRequestV1
    ) -> CompanionTransitionV1 | dict:
        self.start_calls += 1
        return self.start_factory(run_id, request)

    def act(
        self,
        *,
        run_id: str,
        request: InspirationRunRequestV1,
        state: RunStateV1,
        action: RunActionV1,
    ) -> CompanionTransitionV1 | dict:
        self.action_calls += 1
        if self.action_factory is None:
            raise AssertionError("action was not expected")
        return self.action_factory(run_id, request, state, action)


def _constraints(**overrides: object) -> dict[str, object]:
    constraints: dict[str, object] = {
        "required_elements": ["O", "Ti"],
        "excluded_elements": ["Pb"],
        "material_classes": ["transition-metal oxide"],
        "dimensionality": "2D",
        "target_features": ["possible flat band"],
        "top_k": 3,
    }
    constraints.update(overrides)
    return constraints


def _service(
    companion: _InMemoryCompanionAdapter,
    *,
    repository: InMemoryGatewayRepository | None = None,
    artifacts: InMemoryArtifactStore | None = None,
    max_report_bytes: int = 1_000_000,
) -> tuple[
    MaterialsGatewayService,
    InMemoryGatewayRepository,
    InMemoryArtifactStore,
]:
    repository = repository or InMemoryGatewayRepository()
    artifacts = artifacts or InMemoryArtifactStore()
    return (
        MaterialsGatewayService(
            repository=repository,
            companion=companion,
            artifact_reader=artifacts,
            action_authorizer=_AllowAllTestAuthorizer(),
            max_report_bytes=max_report_bytes,
        ),
        repository,
        artifacts,
    )


def _candidate() -> CandidateSummaryV1:
    return CandidateSummaryV1(
        candidate_id="candidate-1",
        parent_candidate_id="parent-1",
        deterministic_transformation="substitute one symmetry-equivalent site",
        shared_invariant="destructive-interference connectivity",
        bridge_domain="line-graph lattice",
        evidence_document_ids=("document-1",),
        evidence_passage_ids=("passage-1",),
        failure_conditions=("symmetry breaking disperses the band",),
        cheapest_falsification_step="inspect a low-cost tight-binding band structure",
    )


def _result(
    *,
    run_id: str,
    report_uri: str,
    authoritative_sha256: str,
) -> GatewayResultRecordV1:
    return GatewayResultRecordV1(
        run_id=run_id,
        report_uri=report_uri,
        authoritative_sha256=authoritative_sha256,
        bundle=InspirationBundleSummaryV1(
            outcome="SUCCEEDED",
            selected_candidates=(_candidate(),),
            limitations=("target property was not computed",),
            next_validation_steps=("run the cheapest falsification step",),
        ),
        evidence_lineage=(
            EvidenceReferenceV1(
                document_id="document-1",
                passage_ids=("passage-1",),
            ),
        ),
        validation_boundaries=(
            "SEARCH_SUPPORTED is bridge support, not property validation",
            "STRUCTURE_VALID is deterministic structural QC only",
        ),
    )


def test_submission_id_is_idempotent_for_one_canonical_request() -> None:
    companion = _InMemoryCompanionAdapter(
        start_factory=lambda _run_id, _request: CompanionTransitionV1(
            state=RunningStateV1(progress_percent=10, message="searching")
        )
    )
    service, repository, _artifacts = _service(companion)

    first = service.materials_inspiration_run(
        submission_id="submission-flat-band",
        goal="Find   three Pb-free candidates",
        constraints=_constraints(),
    )
    second = service.materials_inspiration_run(
        submission_id="submission-flat-band",
        goal="Find three Pb-free candidates",
        constraints=_constraints(required_elements=["Ti", "O"]),
    )

    assert first == second
    assert first.run_id.startswith("inspiration-")
    assert companion.start_calls == 1
    persisted = repository.get_run(first.run_id)
    assert persisted is not None
    assert persisted.revision == 1
    assert isinstance(repository, GatewayRepository)
    assert isinstance(companion, InspirationCompanionAdapter)


def test_submission_id_conflict_fails_closed_without_second_start() -> None:
    companion = _InMemoryCompanionAdapter(
        start_factory=lambda _run_id, _request: CompanionTransitionV1(
            state=RunningStateV1()
        )
    )
    service, _repository, _artifacts = _service(companion)
    service.materials_inspiration_run(
        submission_id="submission-conflict",
        goal="Find Pb-free candidates",
        constraints=_constraints(),
    )

    with pytest.raises(SubmissionConflictError, match="different request"):
        service.materials_inspiration_run(
            submission_id="submission-conflict",
            goal="Find candidates that allow Pb",
            constraints=_constraints(excluded_elements=[]),
        )

    assert companion.start_calls == 1


def test_gateway_request_is_strict_and_rejects_stage_fields() -> None:
    payload = {
        "submission_id": "submission-strict",
        "goal": "Find candidates",
        "constraints": _constraints(),
        "stage_id": "inspiration",
    }
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        InspirationRunRequestV1.model_validate(payload)

    companion = _InMemoryCompanionAdapter(
        start_factory=lambda _run_id, _request: CompanionTransitionV1(
            state=RunningStateV1()
        )
    )
    service, _repository, _artifacts = _service(companion)
    with pytest.raises(ValidationError):
        service.materials_inspiration_run(
            submission_id="submission-coercion",
            goal="Find candidates",
            constraints=_constraints(top_k="3"),
        )

    payload.pop("stage_id")
    payload["constraints"] = {**_constraints(), "novelty": True}
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        InspirationRunRequestV1.model_validate(payload)


def test_public_state_interaction_and_action_schemas_are_discriminated() -> None:
    action_schema = MaterialsRunActRequestV1.model_json_schema()
    view_schema = MaterialsRunViewV1.model_json_schema()

    assert action_schema["properties"]["action"]["discriminator"][
        "propertyName"
    ] == "kind"
    assert view_schema["properties"]["state"]["discriminator"][
        "propertyName"
    ] == "status"
    interaction_schema = view_schema["$defs"]["InteractionRequiredStateV1"][
        "properties"
    ]["interaction"]
    assert interaction_schema["discriminator"]["propertyName"] == "kind"


def test_run_get_is_read_only_and_never_calls_companion() -> None:
    companion = _InMemoryCompanionAdapter(
        start_factory=lambda _run_id, _request: CompanionTransitionV1(
            state=RunningStateV1(progress_percent=25)
        )
    )
    service, _repository, _artifacts = _service(companion)
    submitted = service.materials_inspiration_run(
        submission_id="submission-status",
        goal="Find candidates",
        constraints=_constraints(),
    )

    assert service.materials_run_get(run_id=submitted.run_id) == submitted
    assert service.materials_run_get(run_id=submitted.run_id) == submitted
    assert companion.start_calls == 1
    assert companion.action_calls == 0


def test_action_requires_current_interaction_and_explicit_user_confirmation() -> None:
    approval = ApprovalInteractionV1(
        interaction_id="interaction-freeze",
        approval_kind="requirement_freeze",
        prompt="Freeze this bounded requirement?",
        input_sha256="a" * 64,
    )

    def apply_action(
        _run_id: str,
        _request: InspirationRunRequestV1,
        state: RunStateV1,
        action: RunActionV1,
    ) -> CompanionTransitionV1:
        assert isinstance(state, InteractionRequiredStateV1)
        assert isinstance(action, ApproveActionV1)
        assert action.confirmed_by_user is True
        return CompanionTransitionV1(
            state=RunningStateV1(progress_percent=30, message="requirement frozen")
        )

    companion = _InMemoryCompanionAdapter(
        start_factory=lambda _run_id, _request: CompanionTransitionV1(
            state=InteractionRequiredStateV1(interaction=approval)
        ),
        action_factory=apply_action,
    )
    service, _repository, _artifacts = _service(companion)
    submitted = service.materials_inspiration_run(
        submission_id="submission-approval",
        goal="Find candidates",
        constraints=_constraints(),
    )

    with pytest.raises(ValidationError):
        service.materials_run_act(
            run_id=submitted.run_id,
            action={
                "kind": "approve",
                "interaction_id": "interaction-freeze",
            },
        )
    with pytest.raises(IllegalActionError):
        service.materials_run_act(
            run_id=submitted.run_id,
            action={
                "kind": "answer",
                "interaction_id": "interaction-freeze",
                "answer": "yes",
            },
        )
    with pytest.raises(StaleInteractionError):
        service.materials_run_act(
            run_id=submitted.run_id,
            action={
                "kind": "approve",
                "interaction_id": "interaction-old",
                "confirmed_by_user": True,
            },
        )

    advanced = service.materials_run_act(
        run_id=submitted.run_id,
        action={
            "kind": "approve",
            "interaction_id": "interaction-freeze",
            "confirmed_by_user": True,
        },
    )
    assert isinstance(advanced.state, RunningStateV1)
    assert companion.action_calls == 1

    with pytest.raises(ValidationError):
        ApprovalInteractionV1(
            interaction_id="interaction-tampered-actions",
            approval_kind="requirement_freeze",
            prompt="Freeze this requirement?",
            input_sha256="b" * 64,
            allowed_actions=("cancel", "approve", "reject"),
        )

    with pytest.raises(StaleInteractionError):
        service.materials_run_act(
            run_id=submitted.run_id,
            action={
                "kind": "approve",
                "interaction_id": "interaction-freeze",
                "confirmed_by_user": True,
            },
        )
    assert companion.action_calls == 1


def test_service_without_injected_authorizer_denies_before_companion() -> None:
    approval = ApprovalInteractionV1(
        interaction_id="interaction-default-deny",
        approval_kind="requirement_freeze",
        prompt="Freeze this bounded requirement?",
        input_sha256="a" * 64,
    )
    companion = _InMemoryCompanionAdapter(
        start_factory=lambda _run_id, _request: CompanionTransitionV1(
            state=InteractionRequiredStateV1(interaction=approval)
        ),
        action_factory=lambda *_arguments: (_ for _ in ()).throw(
            AssertionError("companion action must not run")
        ),
    )
    repository = InMemoryGatewayRepository()
    service = MaterialsGatewayService(
        repository=repository,
        companion=companion,
        artifact_reader=InMemoryArtifactStore(),
    )
    started = service.materials_inspiration_run(
        submission_id="submission-default-deny",
        goal="Find candidates",
        constraints=_constraints(),
    )

    with pytest.raises(ActionAuthorizationError):
        service.materials_run_act(
            run_id=started.run_id,
            action={
                "kind": "approve",
                "interaction_id": approval.interaction_id,
                "confirmed_by_user": True,
            },
        )

    assert service.materials_run_get(run_id=started.run_id) == started
    assert companion.action_calls == 0


def test_result_get_verifies_terminal_report_uri_and_authoritative_sha() -> None:
    report = b"# bounded inspiration report\n"
    report_uri = inspiration_report_uri(
        inspiration_run_id("submission-verified")
    )
    report_sha256 = hashlib.sha256(report).hexdigest()
    artifacts = InMemoryArtifactStore()
    artifacts.put_bytes(report_uri, report)

    def completed(run_id: str, _request: InspirationRunRequestV1) -> CompanionTransitionV1:
        result = _result(
            run_id=run_id,
            report_uri=report_uri,
            authoritative_sha256=report_sha256,
        )
        return CompanionTransitionV1(
            state=SucceededStateV1(
                report_uri=report_uri,
                authoritative_sha256=report_sha256,
                result_sha256=gateway_result_sha256(result),
            ),
            result=result,
        )

    companion = _InMemoryCompanionAdapter(start_factory=completed)
    service, _repository, _artifacts = _service(companion, artifacts=artifacts)
    submitted = service.materials_inspiration_run(
        submission_id="submission-verified",
        goal="Find candidates",
        constraints=_constraints(),
    )

    result = service.materials_result_get(run_id=submitted.run_id)

    assert result.verified is True
    assert result.report_uri == report_uri
    assert result.authoritative_sha256 == report_sha256
    assert result.result_sha256 == gateway_result_sha256(result)
    assert result.bundle.scientific_conclusion is False
    assert result.bundle.selected_candidates[0].target_property_status == "UNKNOWN"


def test_result_get_rejects_tampered_report_bytes() -> None:
    original = b"# original report\n"
    report_uri = inspiration_report_uri(
        inspiration_run_id("submission-tampered")
    )
    expected_sha256 = hashlib.sha256(original).hexdigest()
    artifacts = InMemoryArtifactStore()
    artifacts.put_bytes(report_uri, b"# tampered report\n")

    def completed(run_id: str, _request: InspirationRunRequestV1) -> CompanionTransitionV1:
        result = _result(
            run_id=run_id,
            report_uri=report_uri,
            authoritative_sha256=expected_sha256,
        )
        return CompanionTransitionV1(
            state=SucceededStateV1(
                report_uri=report_uri,
                authoritative_sha256=expected_sha256,
                result_sha256=gateway_result_sha256(result),
            ),
            result=result,
        )

    service, _repository, _artifacts = _service(
        _InMemoryCompanionAdapter(start_factory=completed),
        artifacts=artifacts,
    )
    submitted = service.materials_inspiration_run(
        submission_id="submission-tampered",
        goal="Find candidates",
        constraints=_constraints(),
    )

    with pytest.raises(ResultIntegrityError, match="SHA-256"):
        service.materials_result_get(run_id=submitted.run_id)


def test_result_get_rejects_schema_valid_structured_result_tampering() -> None:
    report = b"# intact report\n"
    submission_id = "submission-result-json-tampered"
    report_uri = inspiration_report_uri(inspiration_run_id(submission_id))
    report_sha256 = hashlib.sha256(report).hexdigest()
    artifacts = InMemoryArtifactStore()
    artifacts.put_bytes(report_uri, report)

    def completed(run_id: str, _request: InspirationRunRequestV1):
        result = _result(
            run_id=run_id,
            report_uri=report_uri,
            authoritative_sha256=report_sha256,
        )
        return CompanionTransitionV1(
            state=SucceededStateV1(
                report_uri=report_uri,
                authoritative_sha256=report_sha256,
                result_sha256=gateway_result_sha256(result),
            ),
            result=result,
        )

    service, repository, _artifacts = _service(
        _InMemoryCompanionAdapter(start_factory=completed),
        artifacts=artifacts,
    )
    submitted = service.materials_inspiration_run(
        submission_id=submission_id,
        goal="Find candidates",
        constraints=_constraints(),
    )
    committed = repository._results[submitted.run_id]
    repository._results[submitted.run_id] = committed.model_copy(
        update={
            "validation_boundaries": (
                "This remains valid DTO content but was changed after commit.",
            )
        }
    )

    with pytest.raises(ResultIntegrityError, match="run binding"):
        service.materials_result_get(run_id=submitted.run_id)


def test_adapter_transition_rejects_mismatched_canonical_result_hash() -> None:
    report_sha256 = hashlib.sha256(b"report").hexdigest()

    def mismatched_hash(run_id: str, _request: InspirationRunRequestV1):
        report_uri = inspiration_report_uri(run_id)
        result = _result(
            run_id=run_id,
            report_uri=report_uri,
            authoritative_sha256=report_sha256,
        )
        return {
            "state": {
                "status": "SUCCEEDED",
                "report_uri": report_uri,
                "authoritative_sha256": report_sha256,
                "result_sha256": "0" * 64,
            },
            "result": result.model_dump(mode="json"),
        }

    service, repository, _artifacts = _service(
        _InMemoryCompanionAdapter(start_factory=mismatched_hash)
    )
    view = service.materials_inspiration_run(
        submission_id="submission-result-hash-mismatch",
        goal="Find candidates",
        constraints=_constraints(),
    )

    assert isinstance(view.state, FailedStateV1)
    assert view.state.public_error_code == "ADAPTER_CONTRACT_ERROR"
    assert repository.get_result(view.run_id) is None


def test_adapter_transition_records_uri_mismatch_and_incomplete_result_as_failed() -> None:
    report_sha256 = hashlib.sha256(b"report").hexdigest()

    def mismatched_transition(run_id: str, _request: InspirationRunRequestV1):
        result = _result(
            run_id=run_id,
            report_uri="artifact://inspiration/result/report.md",
            authoritative_sha256=report_sha256,
        )
        return {
            "state": {
                "status": "SUCCEEDED",
                "report_uri": "artifact://inspiration/state/report.md",
                "authoritative_sha256": report_sha256,
                "result_sha256": gateway_result_sha256(result),
            },
            "result": result.model_dump(mode="json"),
        }

    mismatched = _InMemoryCompanionAdapter(
        start_factory=mismatched_transition
    )
    service, _repository, _artifacts = _service(mismatched)
    mismatched_view = service.materials_inspiration_run(
        submission_id="submission-uri-mismatch",
        goal="Find candidates",
        constraints=_constraints(),
    )
    assert isinstance(mismatched_view.state, FailedStateV1)
    assert mismatched_view.state.public_error_code == "ADAPTER_CONTRACT_ERROR"

    incomplete = _InMemoryCompanionAdapter(
        start_factory=lambda _run_id, _request: CompanionTransitionV1(
            state=SucceededStateV1(
                report_uri="artifact://inspiration/incomplete/report.md",
                authoritative_sha256=report_sha256,
                result_sha256="0" * 64,
            )
        )
    )
    service, _repository, _artifacts = _service(incomplete)
    incomplete_view = service.materials_inspiration_run(
        submission_id="submission-incomplete",
        goal="Find candidates",
        constraints=_constraints(),
    )
    assert isinstance(incomplete_view.state, FailedStateV1)
    assert incomplete_view.state.public_error_code == "ADAPTER_CONTRACT_ERROR"


def test_adapter_execution_failure_is_persisted_and_not_retried_implicitly() -> None:
    def fail_start(_run_id: str, _request: InspirationRunRequestV1):
        raise RuntimeError("private backend detail")

    companion = _InMemoryCompanionAdapter(start_factory=fail_start)
    service, _repository, _artifacts = _service(companion)

    first = service.materials_inspiration_run(
        submission_id="submission-start-failure",
        goal="Find candidates",
        constraints=_constraints(),
    )
    second = service.materials_inspiration_run(
        submission_id="submission-start-failure",
        goal="Find candidates",
        constraints=_constraints(),
    )

    assert first == second
    assert isinstance(first.state, FailedStateV1)
    assert first.state.public_error_code == "ADAPTER_EXECUTION_ERROR"
    assert "private backend detail" not in first.state.public_message
    assert companion.start_calls == 1


def test_terminal_report_uri_must_be_bound_to_the_exact_run() -> None:
    report_sha256 = hashlib.sha256(b"report").hexdigest()
    wrong_uri = "artifact://stages/inspiration/other-run/report.md"

    def wrong_path(run_id: str, _request: InspirationRunRequestV1):
        result = _result(
            run_id=run_id,
            report_uri=wrong_uri,
            authoritative_sha256=report_sha256,
        )
        return CompanionTransitionV1(
            state=SucceededStateV1(
                report_uri=wrong_uri,
                authoritative_sha256=report_sha256,
                result_sha256=gateway_result_sha256(result),
            ),
            result=result,
        )

    service, _repository, _artifacts = _service(
        _InMemoryCompanionAdapter(start_factory=wrong_path)
    )
    view = service.materials_inspiration_run(
        submission_id="submission-wrong-report-path",
        goal="Find candidates",
        constraints=_constraints(),
    )

    assert isinstance(view.state, FailedStateV1)
    assert view.state.public_error_code == "ADAPTER_CONTRACT_ERROR"


def test_candidate_evidence_must_close_over_result_lineage() -> None:
    baseline = _result(
        run_id="inspiration-lineage",
        report_uri="artifact://stages/inspiration/inspiration-lineage/report.md",
        authoritative_sha256="a" * 64,
    )

    with pytest.raises(ValidationError, match="does not close"):
        GatewayResultRecordV1(
            run_id=baseline.run_id,
            report_uri=baseline.report_uri,
            authoritative_sha256=baseline.authoritative_sha256,
            bundle=baseline.bundle,
            evidence_lineage=(),
            validation_boundaries=baseline.validation_boundaries,
        )


def test_terminal_result_cannot_exceed_requested_top_k() -> None:
    report_sha256 = hashlib.sha256(b"report").hexdigest()

    def too_many(run_id: str, _request: InspirationRunRequestV1):
        report_uri = inspiration_report_uri(run_id)
        second = _candidate().model_copy(update={"candidate_id": "candidate-2"})
        result = GatewayResultRecordV1(
            run_id=run_id,
            report_uri=report_uri,
            authoritative_sha256=report_sha256,
            bundle=InspirationBundleSummaryV1(
                outcome="SUCCEEDED",
                selected_candidates=(_candidate(), second),
                limitations=("target property was not computed",),
                next_validation_steps=("run a low-cost falsification",),
            ),
            evidence_lineage=(
                EvidenceReferenceV1(
                    document_id="document-1",
                    passage_ids=("passage-1",),
                ),
            ),
            validation_boundaries=("structure proposals require validation",),
        )
        return CompanionTransitionV1(
            state=SucceededStateV1(
                report_uri=report_uri,
                authoritative_sha256=report_sha256,
                result_sha256=gateway_result_sha256(result),
            ),
            result=result,
        )

    service, _repository, _artifacts = _service(
        _InMemoryCompanionAdapter(start_factory=too_many)
    )
    view = service.materials_inspiration_run(
        submission_id="submission-top-k",
        goal="Find at most one candidate",
        constraints=_constraints(top_k=1),
    )

    assert isinstance(view.state, FailedStateV1)
    assert view.state.public_error_code == "ADAPTER_CONTRACT_ERROR"


def test_result_get_rejects_nonterminal_run_and_accepts_partial_result() -> None:
    running_service, _repository, _artifacts = _service(
        _InMemoryCompanionAdapter(
            start_factory=lambda _run_id, _request: CompanionTransitionV1(
                state=RunningStateV1()
            )
        )
    )
    running = running_service.materials_inspiration_run(
        submission_id="submission-running",
        goal="Find candidates",
        constraints=_constraints(),
    )
    with pytest.raises(ResultUnavailableError, match="not SUCCEEDED or PARTIAL"):
        running_service.materials_result_get(run_id=running.run_id)

    report = b"# partial report\n"
    report_uri = inspiration_report_uri(
        inspiration_run_id("submission-partial")
    )
    report_sha256 = hashlib.sha256(report).hexdigest()
    artifacts = InMemoryArtifactStore()
    artifacts.put_bytes(report_uri, report)

    def partial(run_id: str, _request: InspirationRunRequestV1) -> CompanionTransitionV1:
        result = _result(
            run_id=run_id,
            report_uri=report_uri,
            authoritative_sha256=report_sha256,
        )
        return CompanionTransitionV1(
            state=PartialStateV1(
                report_uri=report_uri,
                authoritative_sha256=report_sha256,
                result_sha256=gateway_result_sha256(result),
                warnings=("one planned output failed",),
            ),
            result=result,
        )

    partial_service, _repository, _artifacts = _service(
        _InMemoryCompanionAdapter(start_factory=partial),
        artifacts=artifacts,
    )
    submitted = partial_service.materials_inspiration_run(
        submission_id="submission-partial",
        goal="Find candidates",
        constraints=_constraints(),
    )
    result = partial_service.materials_result_get(run_id=submitted.run_id)
    assert result.verified is True
