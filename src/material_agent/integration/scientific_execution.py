"""Bounded execution of an audited scientific ModelTaskPlan DAG."""

from __future__ import annotations

from collections.abc import Callable
from typing import Protocol

from pydantic import model_validator

from material_agent.inspiration.models import Identifier, StrictModel, deterministic_id
from material_agent.integration.scientific_loop import (
    ModelExecutionReceipt,
    ModelTaskPlan,
    RouteAuditStatus,
    ScientificEvidence,
    ScientificEvidenceLevel,
    ScientificEvidenceVerdict,
    ScientificValidationLoopService,
    TaskExecutionBinding,
)


class ScientificTaskExecutor(Protocol):
    def execute(
        self,
        *,
        binding: TaskExecutionBinding,
    ) -> ModelExecutionReceipt: ...


class ScientificExecutorRegistry:
    """Runtime executors keyed by the DeepSeek-selected capability/model ID."""

    def __init__(self) -> None:
        self._executors: dict[str, ScientificTaskExecutor] = {}

    def register(self, model_id: str, executor: ScientificTaskExecutor) -> None:
        if model_id in self._executors:
            raise ValueError("scientific executor model ID is already registered")
        self._executors[model_id] = executor

    def get(self, model_id: str) -> ScientificTaskExecutor | None:
        return self._executors.get(model_id)


class ScientificDAGRunResult(StrictModel):
    schema_version: str = "scientific-dag-run-v1"
    run_result_id: Identifier
    model_task_plan_id: Identifier
    evidence: tuple[ScientificEvidence, ...]
    completed_task_ids: tuple[Identifier, ...]
    blocked_or_failed_task_ids: tuple[Identifier, ...]
    all_audited_tasks_succeeded: bool

    @model_validator(mode="after")
    def validate_identity(self) -> ScientificDAGRunResult:
        expected = deterministic_id(
            "scientific-dag-run",
            self.model_dump(mode="python", exclude={"run_result_id"}),
        )
        if self.run_result_id != expected:
            raise ValueError("scientific DAG run result ID differs from content")
        return self


def execute_scientific_dag(
    *,
    plan: ModelTaskPlan,
    service: ScientificValidationLoopService,
    executors: ScientificExecutorRegistry,
    failure_receipt_factory: Callable[
        [str, tuple[str, ...], tuple[str, ...]], ModelExecutionReceipt
    ]
    | None = None,
) -> ScientificDAGRunResult:
    """Execute tasks in frozen route order and write every result to memory.

    A missing executor or failed prerequisite becomes explicit NONE evidence;
    downstream tasks do not silently reuse the original structure.
    """

    evidence: list[ScientificEvidence] = []
    completed: list[str] = []
    blocked: list[str] = []
    factory = failure_receipt_factory or _blocked_receipt
    for audited in plan.tasks:
        task = audited.proposed_task
        if audited.status is RouteAuditStatus.BLOCKED:
            blocked.append(task.task_id)
            continue
        try:
            binding = service.bind_task_execution(plan, task.task_id, evidence)
        except ValueError:
            receipt = factory(
                task.task_id,
                task.requested_observables,
                ("PREREQUISITE_BINDING_FAILED",),
            )
        else:
            executor = executors.get(task.model_id)
            expected_consumed = tuple(
                item.artifact_id for item in binding.consumed_artifacts
            )
            if executor is None:
                receipt = _receipt_with_consumed_artifacts(
                    factory(
                        task.task_id,
                        task.requested_observables,
                        ("SCIENTIFIC_EXECUTOR_NOT_REGISTERED",),
                    ),
                    expected_consumed,
                )
            else:
                try:
                    receipt = executor.execute(binding=binding)
                    if receipt.task_id != task.task_id:
                        raise ValueError(
                            "scientific executor receipt task ID mismatch"
                        )
                    if receipt.consumed_artifact_ids != expected_consumed:
                        raise ValueError(
                            "scientific executor did not bind exact predecessor "
                            "Artifacts"
                        )
                except Exception as exc:  # noqa: BLE001
                    service.artifact_store.write_json(
                        (
                            f"scientific_loop/{service.source_run_id}/failures/"
                            f"{task.task_id}.json"
                        ),
                        {
                            "exception_type": type(exc).__name__,
                            "reason_code": "SCIENTIFIC_EXECUTOR_FAILED",
                            "scientific_conclusion": False,
                            "task_id": task.task_id,
                        },
                        immutable=True,
                    )
                    receipt = _receipt_with_consumed_artifacts(
                        factory(
                            task.task_id,
                            task.requested_observables,
                            ("SCIENTIFIC_EXECUTOR_FAILED",),
                        ),
                        expected_consumed,
                    )
        record, _snapshot = service.record_execution(plan, receipt)
        evidence.append(record)
        if receipt.status == "SUCCEEDED":
            completed.append(task.task_id)
        else:
            blocked.append(task.task_id)
    values = {
        "schema_version": "scientific-dag-run-v1",
        "model_task_plan_id": plan.plan_id,
        "evidence": tuple(evidence),
        "completed_task_ids": tuple(sorted(completed)),
        "blocked_or_failed_task_ids": tuple(sorted(blocked)),
        "all_audited_tasks_succeeded": not blocked,
    }
    result = ScientificDAGRunResult(
        run_result_id=deterministic_id("scientific-dag-run", values),
        **values,
    )
    service.artifact_store.write_json(
        (
            f"scientific_loop/{service.source_run_id}/dag-runs/"
            f"{result.run_result_id}.json"
        ),
        result.model_dump(mode="json"),
        immutable=True,
    )
    return result


def _blocked_receipt(
    task_id: str,
    tested_claim_ids: tuple[str, ...],
    reasons: tuple[str, ...],
) -> ModelExecutionReceipt:
    return ModelExecutionReceipt(
        task_id=task_id,
        status="BLOCKED",
        verdict=ScientificEvidenceVerdict.INCONCLUSIVE,
        tested_claim_ids=tested_claim_ids,
        evidence_level=ScientificEvidenceLevel.NONE,
        reason_codes=reasons,
        real_execution=False,
    )


def _receipt_with_consumed_artifacts(
    receipt: ModelExecutionReceipt,
    consumed_artifact_ids: tuple[str, ...],
) -> ModelExecutionReceipt:
    payload = receipt.model_dump(mode="python")
    payload["consumed_artifact_ids"] = consumed_artifact_ids
    return ModelExecutionReceipt.model_validate(payload)
