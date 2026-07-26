from __future__ import annotations

from material_agent.retrieval import AGENT01_CONTRACT_VERSION
from material_agent.retrieval.models import (
    CandidateAuditRecord,
    RetrievalStageContext,
    RetrievalStageInput,
    StageOutcomeType,
    StageResultEnvelope,
    StageStatus,
)
from material_agent.retrieval.runner import RetrievalStageRunner
from material_agent.retrieval.storage import LocalArtifactStore


def test_stage_runner_prepare_start_reconcile_contract(
    tmp_path, requirement, adapter, policy
) -> None:
    store = LocalArtifactStore(tmp_path)
    requirement_ref = store.write_json(
        "requirements/requirement.v1.json",
        requirement.model_dump(mode="json"),
        immutable=True,
    )
    context = RetrievalStageContext(
        requirement=requirement,
        stage_input=RetrievalStageInput(
            project_id="project-contract",
            run_id="run-contract",
            requirement_revision=requirement.revision,
            requirement_artifact_uri=requirement_ref.uri,
            requirement_hash=requirement_ref.sha256,
            retrieval_policy_version=policy.policy_version,
            confirmed_by_user=True,
        ),
    )
    runner = RetrievalStageRunner(
        adapter=adapter,
        artifact_store=store,
        policy=policy,
    )

    validation = runner.validate_input(context)
    plan = runner.prepare(context)
    outcome = runner.start(plan, plan.idempotency_key)

    assert validation.valid is True
    assert validation.requirement_artifact_sha256 == requirement_ref.sha256
    assert outcome.outcome is StageOutcomeType.COMPLETED
    assert outcome.status is StageStatus.SUCCEEDED
    assert outcome.operation_ref is not None
    assert outcome.result is not None
    assert outcome.result.schema_version == AGENT01_CONTRACT_VERSION

    reconciled = runner.reconcile(outcome.operation_ref)
    assert reconciled.outcome is StageOutcomeType.COMPLETED
    assert reconciled.result == outcome.result

    manifest = store.read_jsonl(outcome.result.candidate_manifest.uri)
    candidates = [
        CandidateAuditRecord.model_validate(candidate) for candidate in manifest
    ]
    assert candidates
    assert all(
        candidate.schema_version == AGENT01_CONTRACT_VERSION
        for candidate in candidates
    )


def test_start_rejects_wrong_idempotency_key_without_execution(
    tmp_path, requirement, adapter, policy
) -> None:
    store = LocalArtifactStore(tmp_path)
    requirement_ref = store.write_json(
        "requirements/requirement.v1.json",
        requirement.model_dump(mode="json"),
        immutable=True,
    )
    context = RetrievalStageContext(
        requirement=requirement,
        stage_input=RetrievalStageInput(
            project_id="project-contract",
            run_id="run-contract",
            requirement_revision=requirement.revision,
            requirement_artifact_uri=requirement_ref.uri,
            requirement_hash=requirement_ref.sha256,
            retrieval_policy_version=policy.policy_version,
            confirmed_by_user=True,
        ),
    )
    runner = RetrievalStageRunner(
        adapter=adapter,
        artifact_store=store,
        policy=policy,
    )
    plan = runner.prepare(context)
    outcome = runner.start(plan, "wrong-key")
    assert outcome.outcome is StageOutcomeType.FAILED
    assert outcome.status is StageStatus.PERMANENT_FAILED
    assert outcome.errors[0].category == "IDEMPOTENCY_KEY_MISMATCH"
    assert not (tmp_path / "stages").exists()


def test_frozen_models_publish_versioned_json_schema() -> None:
    result_schema = StageResultEnvelope.model_json_schema()
    candidate_schema = CandidateAuditRecord.model_json_schema()
    assert result_schema["properties"]["schema_version"]["default"] == (
        AGENT01_CONTRACT_VERSION
    )
    assert candidate_schema["properties"]["schema_version"]["default"] == (
        AGENT01_CONTRACT_VERSION
    )
    assert "candidate_manifest" in result_schema["properties"]
    assert "structure_artifact_sha256" in candidate_schema["properties"]


def test_reconcile_rejects_operation_path_escape(
    tmp_path, adapter, policy
) -> None:
    runner = RetrievalStageRunner(
        adapter=adapter,
        artifact_store=LocalArtifactStore(tmp_path),
        policy=policy,
    )
    outcome = runner.reconcile("artifact://../outside-store.json")
    assert outcome.outcome is StageOutcomeType.FAILED
    assert outcome.status is StageStatus.PERMANENT_FAILED
    assert outcome.errors[0].category == "BACKEND_INCONSISTENT"
