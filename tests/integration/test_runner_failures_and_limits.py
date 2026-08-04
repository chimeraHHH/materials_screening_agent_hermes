from __future__ import annotations

import copy
import json

import pytest
import requests

from material_agent.retrieval.adapters import InMemoryMaterialsAdapter
from material_agent.retrieval.models import (
    RetrievalPolicy,
    RetrievalStageInput,
    SourceDatabase,
    StageStatus,
)
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


class CountingAdapter(InMemoryMaterialsAdapter):
    def __init__(
        self,
        *args,
        metadata_failures: int = 0,
        search_failures: int = 0,
        origin_failures: int = 0,
        **kwargs,
    ) -> None:
        super().__init__(*args, **kwargs)
        self.metadata_failures = metadata_failures
        self.search_failures = search_failures
        self.origin_failures = origin_failures
        self.metadata_calls = 0
        self.search_calls = 0
        self.origin_calls = 0

    def metadata(self):
        self.metadata_calls += 1
        if self.metadata_calls <= self.metadata_failures:
            raise requests.Timeout("metadata timeout")
        return super().metadata()

    def search(self, plan):
        self.search_calls += 1
        if self.search_calls <= self.search_failures:
            raise requests.Timeout("search timeout")
        return super().search(plan)

    def resolve_task_metadata(self, task_ids, material_ids, batch_size):
        self.origin_calls += 1
        if self.origin_calls <= self.origin_failures:
            raise requests.Timeout("origin timeout")
        return super().resolve_task_metadata(task_ids, material_ids, batch_size)


class FailOnceReportStore(LocalArtifactStore):
    def __init__(self, root):
        super().__init__(root)
        self.failed = False

    def write_json(self, relative_path, value, *, immutable=False):
        if relative_path.endswith("/retrieval_report.json") and not self.failed:
            self.failed = True
            raise RuntimeError("injected report failure")
        return super().write_json(
            relative_path, value, immutable=immutable
        )


class StatusError(RuntimeError):
    def __init__(self, status_code: int, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code


class MPRestError(RuntimeError):
    """Minimal provider-shaped error used to test mp-api retry handling."""


class MPRestFlakySearchAdapter(CountingAdapter):
    def __init__(self, *args, failures: int, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.failures = failures

    def search(self, plan):
        self.search_calls += 1
        if self.search_calls <= self.failures:
            raise MPRestError("provider request temporarily unavailable")
        return InMemoryMaterialsAdapter.search(self, plan)


class StatusFlakySearchAdapter(CountingAdapter):
    def __init__(self, *args, status_code: int, failures: int, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.status_code = status_code
        self.failures = failures

    def search(self, plan):
        self.search_calls += 1
        if self.search_calls <= self.failures:
            raise StatusError(self.status_code, "temporary fixture failure")
        return InMemoryMaterialsAdapter.search(self, plan)


class AuthenticationFailureAdapter(CountingAdapter):
    def metadata(self):
        self.metadata_calls += 1
        raise StatusError(401, "unauthorized: super-secret-fixture-token")


def stage_input(store, requirement, policy, *, confirmed=True):
    requirement_ref = store.write_json(
        f"requirements/requirement.v{requirement.revision}.json",
        requirement.model_dump(mode="json"),
        immutable=True,
    )
    return RetrievalStageInput(
        project_id="project-integration",
        run_id="run-integration",
        requirement_revision=requirement.revision,
        requirement_artifact_uri=requirement_ref.uri,
        requirement_hash=requirement_ref.sha256,
        retrieval_policy_version=policy.policy_version,
        confirmed_by_user=confirmed,
    )


def test_transient_search_is_retried(tmp_path, requirement, fixture_payload) -> None:
    adapter = FlakyAdapter(
        fixture_payload["documents"],
        failures=2,
        database_version=fixture_payload["database_version"],
        task_metadata=fixture_payload["task_metadata"],
    )
    policy = RetrievalPolicy(retry_base_seconds=0)
    store = LocalArtifactStore(tmp_path)
    result = RetrievalStageRunner(
        adapter=adapter,
        artifact_store=store,
        policy=policy,
    ).run(requirement, stage_input(store, requirement, policy))
    assert result.status is StageStatus.SUCCEEDED
    assert adapter.search_calls == 3


def test_exhausted_transient_search_returns_retryable_failure(
    tmp_path, requirement, fixture_payload
) -> None:
    adapter = FlakyAdapter(
        fixture_payload["documents"],
        failures=3,
        database_version=fixture_payload["database_version"],
        task_metadata=fixture_payload["task_metadata"],
    )
    policy = RetrievalPolicy(retry_base_seconds=0)
    store = LocalArtifactStore(tmp_path)
    result = RetrievalStageRunner(
        adapter=adapter,
        artifact_store=store,
        policy=policy,
    ).run(requirement, stage_input(store, requirement, policy))
    assert result.status is StageStatus.RETRYABLE_FAILED
    assert result.errors[0].category == "TRANSIENT_EXTERNAL"
    assert result.errors[0].retryable is True
    assert adapter.search_calls == 3


def test_mp_rest_error_is_retried(tmp_path, requirement, fixture_payload) -> None:
    adapter = MPRestFlakySearchAdapter(
        fixture_payload["documents"],
        failures=2,
        database_version=fixture_payload["database_version"],
        task_metadata=fixture_payload["task_metadata"],
    )
    policy = RetrievalPolicy(retry_base_seconds=0)
    store = LocalArtifactStore(tmp_path)
    result = RetrievalStageRunner(
        adapter=adapter,
        artifact_store=store,
        policy=policy,
    ).run(requirement, stage_input(store, requirement, policy))
    assert result.status is StageStatus.SUCCEEDED
    assert adapter.search_calls == 3


def test_non_mp_prepare_failure_preserves_source_evidence_boundary(
    tmp_path, requirement, fixture_payload
) -> None:
    adapter = CountingAdapter(
        fixture_payload["documents"],
        source_database=SourceDatabase.TOPOLOGICAL_QUANTUM_CHEMISTRY,
        metadata_failures=3,
        database_version=fixture_payload["database_version"],
        task_metadata=fixture_payload["task_metadata"],
    )
    policy = RetrievalPolicy(
        source_database=SourceDatabase.TOPOLOGICAL_QUANTUM_CHEMISTRY,
        retry_base_seconds=0,
    )
    store = LocalArtifactStore(tmp_path)
    result = RetrievalStageRunner(
        adapter=adapter,
        artifact_store=store,
        policy=policy,
    ).run(requirement, stage_input(store, requirement, policy))

    assert result.status is StageStatus.RETRYABLE_FAILED
    assert adapter.metadata_calls == 3
    coverage = store.read_json(
        "stages/agent01/run-integration/source_property_coverage.json"
    )
    assert coverage["source_database"] == "topological_quantum_chemistry"
    assert "flat_band_bandwidth" in coverage["not_judged_at_agent01"]
    assert [artifact.uri for artifact in result.output_artifacts] == [
        "artifact://stages/agent01/run-integration/source_property_coverage.json"
    ]


def test_unconfirmed_stage_input_blocks_without_search(
    tmp_path, requirement, fixture_payload
) -> None:
    adapter = FlakyAdapter(
        fixture_payload["documents"],
        failures=0,
        database_version=fixture_payload["database_version"],
        task_metadata=fixture_payload["task_metadata"],
    )
    policy = RetrievalPolicy(retry_base_seconds=0)
    store = LocalArtifactStore(tmp_path)
    result = RetrievalStageRunner(
        adapter=adapter,
        artifact_store=store,
        policy=policy,
    ).run(
        requirement,
        stage_input(store, requirement, policy, confirmed=False),
    )
    assert result.status is StageStatus.BLOCKED_MISSING_INPUT
    assert adapter.search_calls == 0


def test_zero_matches_is_success(tmp_path, requirement, fixture_payload) -> None:
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
    store = LocalArtifactStore(tmp_path)
    result = RetrievalStageRunner(
        adapter=adapter,
        artifact_store=store,
        policy=policy,
    ).run(changed, stage_input(store, changed, policy))
    assert result.status is StageStatus.SUCCEEDED
    assert result.metrics["database_returned"] == 0
    assert result.candidate_ids == []


def test_scan_limit_is_reported_as_partial(
    tmp_path, requirement, fixture_payload
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
    store = LocalArtifactStore(tmp_path)
    result = RetrievalStageRunner(
        adapter=adapter,
        artifact_store=store,
        policy=policy,
    ).run(requirement, stage_input(store, requirement, policy))
    assert result.status is StageStatus.PARTIAL
    assert result.metrics["scan_truncated"] is True


def test_exact_duplicate_candidates_are_annotated_not_removed(
    tmp_path, requirement, fixture_payload
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
    store = LocalArtifactStore(tmp_path)
    result = RetrievalStageRunner(
        adapter=adapter,
        artifact_store=store,
        policy=policy,
    ).run(requirement, stage_input(store, requirement, policy))
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
    tmp_path, requirement, fixture_payload
) -> None:
    adapter = InMemoryMaterialsAdapter(
        fixture_payload["documents"],
        database_version=fixture_payload["database_version"],
        task_metadata=fixture_payload["task_metadata"],
        available_fields=[field for field in CORE_FIELDS if field != "structure"],
    )
    policy = RetrievalPolicy(retry_base_seconds=0)
    store = LocalArtifactStore(tmp_path)
    result = RetrievalStageRunner(
        adapter=adapter,
        artifact_store=store,
        policy=policy,
    ).run(requirement, stage_input(store, requirement, policy))
    assert result.status is StageStatus.PERMANENT_FAILED
    assert result.errors[0].category == "API_SCHEMA_DRIFT"


def test_resume_refuses_database_version_change(
    tmp_path, requirement, fixture_payload
) -> None:
    policy = RetrievalPolicy(retry_base_seconds=0)
    first_adapter = InMemoryMaterialsAdapter(
        fixture_payload["documents"],
        database_version="fixture-v1",
        task_metadata=fixture_payload["task_metadata"],
    )
    store = LocalArtifactStore(tmp_path)
    stage = stage_input(store, requirement, policy)
    first = RetrievalStageRunner(
        adapter=first_adapter,
        artifact_store=store,
        policy=policy,
    ).run(requirement, stage)
    assert first.status is StageStatus.SUCCEEDED
    operation_dir = (
        tmp_path / "stages" / "agent01" / "run-integration" / "operations"
    )
    next(operation_dir.glob("*.result.json")).unlink()

    changed_adapter = InMemoryMaterialsAdapter(
        fixture_payload["documents"],
        database_version="fixture-v2",
        task_metadata=fixture_payload["task_metadata"],
    )
    resumed = RetrievalStageRunner(
        adapter=changed_adapter,
        artifact_store=store,
        policy=policy,
    ).run(requirement, stage)
    assert resumed.status is StageStatus.PERMANENT_FAILED
    assert resumed.errors[0].category == "BACKEND_INCONSISTENT"


@pytest.mark.parametrize(
    "case",
    ["missing", "path_escape", "hash", "content", "unit", "element"],
)
def test_invalid_requirement_boundary_makes_no_external_calls(
    tmp_path, requirement, fixture_payload, case
) -> None:
    adapter = CountingAdapter(
        fixture_payload["documents"],
        database_version=fixture_payload["database_version"],
        task_metadata=fixture_payload["task_metadata"],
    )
    policy = RetrievalPolicy(retry_base_seconds=0)
    store = LocalArtifactStore(tmp_path)
    runner_requirement = requirement

    if case == "missing":
        stage = RetrievalStageInput(
            project_id="project-integration",
            run_id="run-integration",
            requirement_revision=requirement.revision,
            requirement_artifact_uri="artifact://requirements/missing.json",
            requirement_hash="0" * 64,
            retrieval_policy_version=policy.policy_version,
            confirmed_by_user=True,
        )
    elif case == "path_escape":
        stage = RetrievalStageInput(
            project_id="project-integration",
            run_id="run-integration",
            requirement_revision=requirement.revision,
            requirement_artifact_uri="artifact://../outside-store.json",
            requirement_hash="0" * 64,
            retrieval_policy_version=policy.policy_version,
            confirmed_by_user=True,
        )
    else:
        stage = stage_input(store, requirement, policy)
        if case == "hash":
            stage = stage.model_copy(update={"requirement_hash": "0" * 64})
        elif case == "content":
            hard = requirement.hard_constraints.model_copy(
                update={"include_elements": ["C"]}
            )
            runner_requirement = requirement.model_copy(
                update={"hard_constraints": hard}
            )
        elif case == "unit":
            numeric_range = requirement.hard_constraints.band_gap_ev.model_copy(
                update={"unit": "meV"}
            )
            hard = requirement.hard_constraints.model_copy(
                update={"band_gap_ev": numeric_range}
            )
            runner_requirement = requirement.model_copy(
                update={"hard_constraints": hard}
            )
            invalid_ref = store.write_json(
                "requirements/invalid-unit.json",
                runner_requirement.model_dump(mode="json"),
                immutable=True,
            )
            stage = stage.model_copy(
                update={
                    "requirement_artifact_uri": invalid_ref.uri,
                    "requirement_hash": invalid_ref.sha256,
                }
            )
        elif case == "element":
            hard = requirement.hard_constraints.model_copy(
                update={"include_elements": ["NotAnElement"]}
            )
            runner_requirement = requirement.model_copy(
                update={"hard_constraints": hard}
            )
            invalid_ref = store.write_json(
                "requirements/invalid-element.json",
                runner_requirement.model_dump(mode="json"),
                immutable=True,
            )
            stage = stage.model_copy(
                update={
                    "requirement_artifact_uri": invalid_ref.uri,
                    "requirement_hash": invalid_ref.sha256,
                }
            )

    result = RetrievalStageRunner(
        adapter=adapter, artifact_store=store, policy=policy
    ).run(runner_requirement, stage)
    assert result.status in {
        StageStatus.BLOCKED_MISSING_INPUT,
        StageStatus.PERMANENT_FAILED,
    }
    assert adapter.metadata_calls == 0
    assert adapter.search_calls == 0
    assert adapter.origin_calls == 0


def test_metadata_and_origin_transient_failures_are_retried(
    tmp_path, requirement, fixture_payload
) -> None:
    adapter = CountingAdapter(
        fixture_payload["documents"],
        database_version=fixture_payload["database_version"],
        task_metadata=fixture_payload["task_metadata"],
        metadata_failures=2,
        origin_failures=2,
    )
    policy = RetrievalPolicy(retry_base_seconds=0)
    store = LocalArtifactStore(tmp_path)
    result = RetrievalStageRunner(
        adapter=adapter, artifact_store=store, policy=policy
    ).run(requirement, stage_input(store, requirement, policy))
    assert result.status is StageStatus.SUCCEEDED
    assert adapter.metadata_calls == 3
    assert adapter.search_calls == 1
    assert adapter.origin_calls == 3


def test_completed_operation_with_damaged_artifact_stops_without_query(
    tmp_path, requirement, fixture_payload
) -> None:
    adapter = CountingAdapter(
        fixture_payload["documents"],
        database_version=fixture_payload["database_version"],
        task_metadata=fixture_payload["task_metadata"],
    )
    policy = RetrievalPolicy(retry_base_seconds=0)
    store = LocalArtifactStore(tmp_path)
    stage = stage_input(store, requirement, policy)
    runner = RetrievalStageRunner(
        adapter=adapter, artifact_store=store, policy=policy
    )
    first = runner.run(requirement, stage)
    assert first.status is StageStatus.SUCCEEDED
    calls_after_first = (
        adapter.metadata_calls,
        adapter.search_calls,
        adapter.origin_calls,
    )
    manifest = (
        tmp_path
        / "stages"
        / "agent01"
        / "run-integration"
        / "candidate_manifest.jsonl"
    )
    manifest.write_text("{}\n", encoding="utf-8")

    resumed = runner.run(requirement, stage)
    assert resumed.status is StageStatus.PERMANENT_FAILED
    assert resumed.errors[0].category == "BACKEND_INCONSISTENT"
    assert (
        adapter.metadata_calls,
        adapter.search_calls,
        adapter.origin_calls,
    ) == calls_after_first


def test_report_interruption_resumes_from_raw_checkpoint_without_search(
    tmp_path, requirement, fixture_payload
) -> None:
    adapter = CountingAdapter(
        fixture_payload["documents"],
        database_version=fixture_payload["database_version"],
        task_metadata=fixture_payload["task_metadata"],
    )
    policy = RetrievalPolicy(retry_base_seconds=0)
    store = FailOnceReportStore(tmp_path)
    stage = stage_input(store, requirement, policy)
    runner = RetrievalStageRunner(
        adapter=adapter, artifact_store=store, policy=policy
    )
    with pytest.raises(RuntimeError, match="injected report failure"):
        runner.run(requirement, stage)
    assert adapter.search_calls == 1

    resumed = runner.run(requirement, stage)
    assert resumed.status is StageStatus.SUCCEEDED
    assert adapter.search_calls == 1


def test_duplicate_material_id_is_normalized_once(
    tmp_path, requirement, fixture_payload
) -> None:
    duplicate = copy.deepcopy(fixture_payload["documents"][0])
    adapter = CountingAdapter(
        [fixture_payload["documents"][0], duplicate],
        database_version=fixture_payload["database_version"],
        task_metadata=fixture_payload["task_metadata"],
        honor_filters=False,
    )
    policy = RetrievalPolicy(retry_base_seconds=0)
    store = LocalArtifactStore(tmp_path)
    result = RetrievalStageRunner(
        adapter=adapter, artifact_store=store, policy=policy
    ).run(requirement, stage_input(store, requirement, policy))
    assert result.metrics["database_returned"] == 2
    assert result.metrics["normalized"] == 1
    assert len(result.candidate_ids) == 1
    assert any("duplicate material IDs" in warning for warning in result.warnings)


def test_origin_resolution_records_unresolved_tasks_explicitly(
    tmp_path, requirement, fixture_payload
) -> None:
    adapter = CountingAdapter(
        fixture_payload["documents"],
        database_version=fixture_payload["database_version"],
        task_metadata={},
    )
    policy = RetrievalPolicy(retry_base_seconds=0)
    store = LocalArtifactStore(tmp_path)
    result = RetrievalStageRunner(
        adapter=adapter, artifact_store=store, policy=policy
    ).run(requirement, stage_input(store, requirement, policy))
    origin_path = (
        tmp_path
        / "stages"
        / "agent01"
        / "run-integration"
        / "origin_resolution.jsonl"
    )
    records = [json.loads(line) for line in origin_path.read_text().splitlines()]
    assert records
    assert {record["status"] for record in records} == {"UNRESOLVED"}
    assert result.status is StageStatus.PARTIAL


@pytest.mark.parametrize("status_code", [429, 503])
def test_retryable_http_statuses_are_retried(
    tmp_path, requirement, fixture_payload, status_code
) -> None:
    adapter = StatusFlakySearchAdapter(
        fixture_payload["documents"],
        database_version=fixture_payload["database_version"],
        task_metadata=fixture_payload["task_metadata"],
        status_code=status_code,
        failures=2,
    )
    policy = RetrievalPolicy(retry_base_seconds=0)
    store = LocalArtifactStore(tmp_path)
    result = RetrievalStageRunner(
        adapter=adapter, artifact_store=store, policy=policy
    ).run(requirement, stage_input(store, requirement, policy))
    assert result.status is StageStatus.SUCCEEDED
    assert adapter.search_calls == 3


def test_authentication_failure_is_permanent_and_redacted(
    tmp_path, requirement, fixture_payload
) -> None:
    adapter = AuthenticationFailureAdapter(
        fixture_payload["documents"],
        database_version=fixture_payload["database_version"],
        task_metadata=fixture_payload["task_metadata"],
    )
    policy = RetrievalPolicy(retry_base_seconds=0)
    store = LocalArtifactStore(tmp_path)
    result = RetrievalStageRunner(
        adapter=adapter, artifact_store=store, policy=policy
    ).run(requirement, stage_input(store, requirement, policy))
    serialized = json.dumps(result.model_dump(mode="json"))
    assert result.status is StageStatus.PERMANENT_FAILED
    assert result.errors[0].category == "PERMANENT_CONFIGURATION"
    assert adapter.metadata_calls == 1
    assert "super-secret-fixture-token" not in serialized


def test_corrupt_operation_record_stops_without_external_calls(
    tmp_path, requirement, fixture_payload
) -> None:
    adapter = CountingAdapter(
        fixture_payload["documents"],
        database_version=fixture_payload["database_version"],
        task_metadata=fixture_payload["task_metadata"],
    )
    policy = RetrievalPolicy(retry_base_seconds=0)
    store = LocalArtifactStore(tmp_path)
    stage = stage_input(store, requirement, policy)
    runner = RetrievalStageRunner(
        adapter=adapter, artifact_store=store, policy=policy
    )
    assert runner.run(requirement, stage).status is StageStatus.SUCCEEDED
    calls_after_first = (adapter.metadata_calls, adapter.search_calls)
    operation = next(
        (
            tmp_path
            / "stages"
            / "agent01"
            / "run-integration"
            / "operations"
        ).glob("*.result.json")
    )
    operation.write_text("{not-json", encoding="utf-8")

    resumed = runner.run(requirement, stage)
    assert resumed.status is StageStatus.PERMANENT_FAILED
    assert resumed.errors[0].category == "BACKEND_INCONSISTENT"
    assert (adapter.metadata_calls, adapter.search_calls) == calls_after_first


def test_invalid_structure_fails_candidate_but_preserves_audit(
    tmp_path, requirement, fixture_payload
) -> None:
    document = copy.deepcopy(fixture_payload["documents"][0])
    document["structure"] = None
    adapter = CountingAdapter(
        [document],
        database_version=fixture_payload["database_version"],
        task_metadata=fixture_payload["task_metadata"],
        honor_filters=False,
    )
    policy = RetrievalPolicy(retry_base_seconds=0)
    store = LocalArtifactStore(tmp_path)
    result = RetrievalStageRunner(
        adapter=adapter, artifact_store=store, policy=policy
    ).run(requirement, stage_input(store, requirement, policy))
    audit_path = (
        tmp_path
        / "stages"
        / "agent01"
        / "run-integration"
        / "candidate_audit.jsonl"
    )
    audit = json.loads(audit_path.read_text().splitlines()[0])
    assert result.status is StageStatus.PARTIAL
    assert audit["decision"] == "FAILED"
    assert audit["decision_reasons"] == ["STRUCTURE_INVALID"]


def test_structure_is_formula_truth_source(
    tmp_path, requirement, fixture_payload
) -> None:
    document = copy.deepcopy(fixture_payload["documents"][0])
    document["formula_pretty"] = "C"
    adapter = CountingAdapter(
        [document],
        database_version=fixture_payload["database_version"],
        task_metadata=fixture_payload["task_metadata"],
        honor_filters=False,
    )
    policy = RetrievalPolicy(retry_base_seconds=0)
    store = LocalArtifactStore(tmp_path)
    result = RetrievalStageRunner(
        adapter=adapter, artifact_store=store, policy=policy
    ).run(requirement, stage_input(store, requirement, policy))
    manifest = store.read_jsonl(result.candidate_manifest.uri)
    assert manifest[0]["formula"] != "C"
    assert "SOURCE_FORMULA_MISMATCH" in manifest[0]["data_quality_flags"]
    assert manifest[0]["provenance"]["summary_formula"] == "C"
