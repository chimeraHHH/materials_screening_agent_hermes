from __future__ import annotations

import copy
import json

import requests

from material_agent.retrieval.adapters import InMemoryMaterialsAdapter
from material_agent.retrieval.models import RetrievalPolicy, RetrievalStageInput, StageStatus
from material_agent.retrieval.query import CORE_FIELDS
from material_agent.retrieval.runner import RetrievalStageRunner
from material_agent.retrieval.storage import LocalArtifactStore


class FlakyAdapter(InMemoryMaterialsAdapter):
    def __init__(self, *args, failures: int, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.failures = failures
        self.search_calls = 0

    def search(self, plan):
        self.search_calls += 1
        if self.search_calls <= self.failures:
            raise requests.Timeout("fixture timeout")
        return super().search(plan)


def stage_input(requirement, requirement_hash, policy, *, confirmed=True):
    return RetrievalStageInput(
        project_id="project-integration",
        run_id="run-integration",
        requirement_revision=requirement.revision,
        requirement_artifact_uri="artifact://requirements/requirement.v1.json",
        requirement_hash=requirement_hash,
        retrieval_policy_version=policy.policy_version,
        confirmed_by_user=confirmed,
    )


def test_transient_search_is_retried(
    tmp_path, requirement, requirement_hash, fixture_payload
) -> None:
    adapter = FlakyAdapter(
        fixture_payload["documents"],
        failures=2,
        database_version=fixture_payload["database_version"],
        task_metadata=fixture_payload["task_metadata"],
    )
    policy = RetrievalPolicy(retry_base_seconds=0)
    result = RetrievalStageRunner(
        adapter=adapter,
        artifact_store=LocalArtifactStore(tmp_path),
        policy=policy,
    ).run(requirement, stage_input(requirement, requirement_hash, policy))
    assert result.status is StageStatus.SUCCEEDED
    assert adapter.search_calls == 3


def test_exhausted_transient_search_returns_retryable_failure(
    tmp_path, requirement, requirement_hash, fixture_payload
) -> None:
    adapter = FlakyAdapter(
        fixture_payload["documents"],
        failures=3,
        database_version=fixture_payload["database_version"],
        task_metadata=fixture_payload["task_metadata"],
    )
    policy = RetrievalPolicy(retry_base_seconds=0)
    result = RetrievalStageRunner(
        adapter=adapter,
        artifact_store=LocalArtifactStore(tmp_path),
        policy=policy,
    ).run(requirement, stage_input(requirement, requirement_hash, policy))
    assert result.status is StageStatus.RETRYABLE_FAILED
    assert result.errors[0].category == "TRANSIENT_EXTERNAL"
    assert result.errors[0].retryable is True
    assert adapter.search_calls == 3


def test_unconfirmed_stage_input_blocks_without_search(
    tmp_path, requirement, requirement_hash, fixture_payload
) -> None:
    adapter = FlakyAdapter(
        fixture_payload["documents"],
        failures=0,
        database_version=fixture_payload["database_version"],
        task_metadata=fixture_payload["task_metadata"],
    )
    policy = RetrievalPolicy(retry_base_seconds=0)
    result = RetrievalStageRunner(
        adapter=adapter,
        artifact_store=LocalArtifactStore(tmp_path),
        policy=policy,
    ).run(
        requirement,
        stage_input(requirement, requirement_hash, policy, confirmed=False),
    )
    assert result.status is StageStatus.BLOCKED_MISSING_INPUT
    assert adapter.search_calls == 0


def test_zero_matches_is_success(
    tmp_path, requirement, requirement_hash, fixture_payload
) -> None:
    hard = requirement.hard_constraints.model_copy(
        update={"include_elements": ["C"]}
    )
    changed = requirement.model_copy(update={"hard_constraints": hard})
    adapter = InMemoryMaterialsAdapter(
        fixture_payload["documents"],
        database_version=fixture_payload["database_version"],
        task_metadata=fixture_payload["task_metadata"],
    )
    policy = RetrievalPolicy(retry_base_seconds=0)
    result = RetrievalStageRunner(
        adapter=adapter,
        artifact_store=LocalArtifactStore(tmp_path),
        policy=policy,
    ).run(changed, stage_input(changed, requirement_hash, policy))
    assert result.status is StageStatus.SUCCEEDED
    assert result.metrics["database_returned"] == 0
    assert result.candidate_ids == []


def test_scan_limit_is_reported_as_partial(
    tmp_path, requirement, requirement_hash, fixture_payload
) -> None:
    adapter = InMemoryMaterialsAdapter(
        fixture_payload["documents"],
        database_version=fixture_payload["database_version"],
        task_metadata=fixture_payload["task_metadata"],
        honor_filters=False,
    )
    policy = RetrievalPolicy(
        chunk_size=1,
        max_records_scanned=1,
        retry_base_seconds=0,
    )
    result = RetrievalStageRunner(
        adapter=adapter,
        artifact_store=LocalArtifactStore(tmp_path),
        policy=policy,
    ).run(requirement, stage_input(requirement, requirement_hash, policy))
    assert result.status is StageStatus.PARTIAL
    assert result.metrics["scan_truncated"] is True


def test_exact_duplicate_candidates_are_annotated_not_removed(
    tmp_path, requirement, requirement_hash, fixture_payload
) -> None:
    first = fixture_payload["documents"][0]
    second = copy.deepcopy(first)
    second["material_id"] = "mp-fixture-duplicate"
    adapter = InMemoryMaterialsAdapter(
        [first, second],
        database_version=fixture_payload["database_version"],
        task_metadata=fixture_payload["task_metadata"],
    )
    policy = RetrievalPolicy(retry_base_seconds=0)
    result = RetrievalStageRunner(
        adapter=adapter,
        artifact_store=LocalArtifactStore(tmp_path),
        policy=policy,
    ).run(requirement, stage_input(requirement, requirement_hash, policy))
    assert result.status is StageStatus.SUCCEEDED
    assert len(result.candidate_ids) == 2

    audit_path = (
        tmp_path / "stages" / "agent01" / "run-integration" / "candidate_audit.jsonl"
    )
    records = [json.loads(line) for line in audit_path.read_text().splitlines()]
    assert len(records) == 2
    assert records[0]["exact_duplicate_group_id"]
    assert (
        records[0]["exact_duplicate_group_id"]
        == records[1]["exact_duplicate_group_id"]
    )
    assert records[0]["candidate_id"] != records[1]["candidate_id"]


def test_capability_schema_drift_is_permanent_failure(
    tmp_path, requirement, requirement_hash, fixture_payload
) -> None:
    adapter = InMemoryMaterialsAdapter(
        fixture_payload["documents"],
        database_version=fixture_payload["database_version"],
        task_metadata=fixture_payload["task_metadata"],
        available_fields=[field for field in CORE_FIELDS if field != "structure"],
    )
    policy = RetrievalPolicy(retry_base_seconds=0)
    result = RetrievalStageRunner(
        adapter=adapter,
        artifact_store=LocalArtifactStore(tmp_path),
        policy=policy,
    ).run(requirement, stage_input(requirement, requirement_hash, policy))
    assert result.status is StageStatus.PERMANENT_FAILED
    assert result.errors[0].category == "API_SCHEMA_DRIFT"


def test_resume_refuses_database_version_change(
    tmp_path, requirement, requirement_hash, fixture_payload
) -> None:
    policy = RetrievalPolicy(retry_base_seconds=0)
    first_adapter = InMemoryMaterialsAdapter(
        fixture_payload["documents"],
        database_version="fixture-v1",
        task_metadata=fixture_payload["task_metadata"],
    )
    stage = stage_input(requirement, requirement_hash, policy)
    first = RetrievalStageRunner(
        adapter=first_adapter,
        artifact_store=LocalArtifactStore(tmp_path),
        policy=policy,
    ).run(requirement, stage)
    assert first.status is StageStatus.SUCCEEDED

    changed_adapter = InMemoryMaterialsAdapter(
        fixture_payload["documents"],
        database_version="fixture-v2",
        task_metadata=fixture_payload["task_metadata"],
    )
    resumed = RetrievalStageRunner(
        adapter=changed_adapter,
        artifact_store=LocalArtifactStore(tmp_path),
        policy=policy,
    ).run(requirement, stage)
    assert resumed.status is StageStatus.PERMANENT_FAILED
    assert resumed.errors[0].category == "BACKEND_INCONSISTENT"
