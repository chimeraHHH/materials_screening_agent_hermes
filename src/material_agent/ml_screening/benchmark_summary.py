"""Deterministic, test-only aggregation for synthetic Agent02 benchmarks."""

from __future__ import annotations

import hashlib
import json
import math
from enum import StrEnum
from typing import Literal

from pydantic import Field, model_validator

from material_agent.ml_screening.benchmark import (
    AGENT02_BENCHMARK_VERSION,
    BenchmarkAggregation,
    BenchmarkMetric,
    BenchmarkMetricSpec,
)
from material_agent.ml_screening.benchmark_metrics import (
    BenchmarkMetricValue,
    SyntheticBenchmarkMetricResult,
    canonical_metric_result_sha256,
)
from material_agent.ml_screening.models import Sha256, StrictFrozenModel


BENCHMARK_SUMMARY_VERSION = "agent02-benchmark-summary-v1"


class BenchmarkSummaryCaseStatus(StrEnum):
    TEST_ONLY_COMPLETE = "TEST_ONLY_COMPLETE"
    BLOCKED = "BLOCKED"
    FAILED = "FAILED"


class BenchmarkSummaryStatus(StrEnum):
    TEST_ONLY_COMPLETE = "TEST_ONLY_COMPLETE"
    PARTIAL = "PARTIAL"
    BLOCKED = "BLOCKED"
    FAILED = "FAILED"


class SyntheticBenchmarkSummaryCase(StrictFrozenModel):
    case_id: str = Field(min_length=1)
    status: BenchmarkSummaryCaseStatus
    result: SyntheticBenchmarkMetricResult | None = None
    result_sha256: Sha256 | None = None
    error_code: str | None = None

    @model_validator(mode="after")
    def validate_case(self) -> SyntheticBenchmarkSummaryCase:
        if self.status is BenchmarkSummaryCaseStatus.TEST_ONLY_COMPLETE:
            if self.result is None or self.result_sha256 is None:
                raise ValueError("complete summary case requires result and result_sha256")
            if self.error_code is not None:
                raise ValueError("complete summary case cannot contain error_code")
            if self.result.case_id != self.case_id:
                raise ValueError("summary case_id does not match metric result")
            if canonical_metric_result_sha256(self.result) != self.result_sha256:
                raise ValueError("summary metric result hash mismatch")
        else:
            if self.result is not None or self.result_sha256 is not None:
                raise ValueError("blocked or failed summary case cannot contain a result")
            if not self.error_code:
                raise ValueError("blocked or failed summary case requires error_code")
        return self


class SyntheticBenchmarkSummaryRequest(StrictFrozenModel):
    schema_version: Literal["agent02-benchmark-summary-v1"] = (
        BENCHMARK_SUMMARY_VERSION
    )
    benchmark_schema_version: Literal["agent02-benchmark-v1"] = (
        AGENT02_BENCHMARK_VERSION
    )
    benchmark_id: str = Field(min_length=1)
    manifest_sha256: Sha256
    is_synthetic: Literal[True] = True
    evaluation_status: Literal["TEST_ONLY"] = "TEST_ONLY"
    expected_case_ids: list[str] = Field(min_length=1)
    metrics: list[BenchmarkMetricSpec] = Field(min_length=1)
    cases: list[SyntheticBenchmarkSummaryCase] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_request(self) -> SyntheticBenchmarkSummaryRequest:
        if self.expected_case_ids != sorted(set(self.expected_case_ids)):
            raise ValueError("expected_case_ids must be sorted and unique")
        case_ids = [case.case_id for case in self.cases]
        if case_ids != self.expected_case_ids:
            raise ValueError("summary cases must match expected_case_ids in order")
        metric_ids = [metric.metric for metric in self.metrics]
        if metric_ids != list(BenchmarkMetric):
            raise ValueError("summary metrics must contain all benchmark metrics in order")
        levels = {
            case.result.reference_calculation_level_id
            for case in self.cases
            if case.result is not None
        }
        if len(levels) > 1:
            raise ValueError("summary cannot mix reference calculation levels")
        return self


class SyntheticBenchmarkSummaryResult(StrictFrozenModel):
    schema_version: Literal["agent02-benchmark-summary-v1"] = (
        BENCHMARK_SUMMARY_VERSION
    )
    benchmark_id: str = Field(min_length=1)
    request_sha256: Sha256
    is_synthetic: Literal[True] = True
    evaluation_status: Literal["TEST_ONLY"] = "TEST_ONLY"
    status: BenchmarkSummaryStatus
    completed_case_count: int = Field(ge=0)
    blocked_case_count: int = Field(ge=0)
    failed_case_count: int = Field(ge=0)
    cases: list[SyntheticBenchmarkSummaryCase] = Field(min_length=1)
    aggregates: list[BenchmarkMetricValue] = Field(default_factory=list)
    evidence_level: Literal["NONE"] = "NONE"
    scientific_conclusion: Literal[False] = False
    scientific_claims: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_result(self) -> SyntheticBenchmarkSummaryResult:
        if self.scientific_claims:
            raise ValueError("synthetic summary cannot contain scientific claims")
        completed_count = sum(
            case.status is BenchmarkSummaryCaseStatus.TEST_ONLY_COMPLETE
            for case in self.cases
        )
        blocked_count = sum(
            case.status is BenchmarkSummaryCaseStatus.BLOCKED
            for case in self.cases
        )
        failed_count = sum(
            case.status is BenchmarkSummaryCaseStatus.FAILED
            for case in self.cases
        )
        if (
            self.completed_case_count,
            self.blocked_case_count,
            self.failed_case_count,
        ) != (completed_count, blocked_count, failed_count):
            raise ValueError("summary case counts do not match cases")
        expected_status = _summary_status(
            completed_count=completed_count,
            blocked_count=blocked_count,
            failed_count=failed_count,
            case_count=len(self.cases),
        )
        if self.status is not expected_status:
            raise ValueError("summary status does not match case statuses")
        aggregate_metrics = [aggregate.metric for aggregate in self.aggregates]
        if self.completed_case_count > 0:
            if aggregate_metrics != list(BenchmarkMetric):
                raise ValueError("summary with completed cases requires all aggregates")
        elif self.aggregates:
            raise ValueError("summary without completed cases cannot contain aggregates")
        return self


def aggregate_synthetic_benchmark(
    request: SyntheticBenchmarkSummaryRequest,
) -> SyntheticBenchmarkSummaryResult:
    completed = [
        case
        for case in request.cases
        if case.status is BenchmarkSummaryCaseStatus.TEST_ONLY_COMPLETE
    ]
    blocked_count = sum(
        case.status is BenchmarkSummaryCaseStatus.BLOCKED for case in request.cases
    )
    failed_count = sum(
        case.status is BenchmarkSummaryCaseStatus.FAILED for case in request.cases
    )
    status = _summary_status(
        completed_count=len(completed),
        blocked_count=blocked_count,
        failed_count=failed_count,
        case_count=len(request.cases),
    )

    aggregates: list[BenchmarkMetricValue] = []
    if completed:
        result_maps = [
            {metric.metric: metric for metric in case.result.metrics}
            for case in completed
            if case.result is not None
        ]
        for spec in request.metrics:
            metric_values = [result[spec.metric].value for result in result_maps]
            units = {result[spec.metric].unit for result in result_maps}
            if units != {spec.unit}:
                raise ValueError(f"summary metric unit mismatch for {spec.metric.value}")
            aggregates.append(
                BenchmarkMetricValue(
                    metric=spec.metric,
                    value=_aggregate(metric_values, spec.aggregation),
                    unit=spec.unit,
                    definition=(
                        f"{spec.aggregation.value} across completed synthetic cases; "
                        "case-level results remain authoritative"
                    ),
                )
            )

    return SyntheticBenchmarkSummaryResult(
        benchmark_id=request.benchmark_id,
        request_sha256=canonical_summary_request_sha256(request),
        status=status,
        completed_case_count=len(completed),
        blocked_case_count=blocked_count,
        failed_case_count=failed_count,
        cases=request.cases,
        aggregates=aggregates,
    )


def canonical_summary_request_sha256(
    request: SyntheticBenchmarkSummaryRequest,
) -> str:
    payload = json.dumps(
        request.model_dump(mode="json"),
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def render_synthetic_benchmark_summary(
    result: SyntheticBenchmarkSummaryResult,
) -> str:
    lines = [
        "# Agent02 Synthetic Benchmark Summary",
        "",
        "> **TEST ONLY / SYNTHETIC:** no real reference data was used. This "
        "summary is not evidence of model accuracy and cannot expand the "
        "Agent02 applicability domain.",
        "",
        f"- Status: `{result.status.value}`",
        f"- Completed: `{result.completed_case_count}`",
        f"- Blocked: `{result.blocked_case_count}`",
        f"- Failed: `{result.failed_case_count}`",
        "- Evidence: `NONE`",
        "- Scientific conclusion: `false`",
        "",
        "## Aggregates",
        "",
        "| Metric | Value | Unit |",
        "|---|---:|---|",
    ]
    if result.aggregates:
        lines.extend(
            f"| `{metric.metric.value}` | `{metric.value:.12g}` | `{metric.unit}` |"
            for metric in result.aggregates
        )
    else:
        lines.append("| _none_ |  |  |")
    lines.extend(
        [
            "",
            "## Cases",
            "",
            "| Case | Status | Error |",
            "|---|---|---|",
        ]
    )
    lines.extend(
        f"| `{case.case_id}` | `{case.status.value}` | `{case.error_code or ''}` |"
        for case in result.cases
    )
    lines.append("")
    return "\n".join(lines)


def _aggregate(values: list[float], aggregation: BenchmarkAggregation) -> float:
    if aggregation is BenchmarkAggregation.MEAN:
        return sum(values) / len(values)
    if aggregation is BenchmarkAggregation.MAX:
        return max(values)
    if aggregation is BenchmarkAggregation.RMSE:
        return math.sqrt(sum(value * value for value in values) / len(values))
    raise ValueError(f"unsupported aggregation: {aggregation}")


def _summary_status(
    *,
    completed_count: int,
    blocked_count: int,
    failed_count: int,
    case_count: int,
) -> BenchmarkSummaryStatus:
    if completed_count == case_count:
        return BenchmarkSummaryStatus.TEST_ONLY_COMPLETE
    if completed_count:
        return BenchmarkSummaryStatus.PARTIAL
    if failed_count:
        return BenchmarkSummaryStatus.FAILED
    if blocked_count:
        return BenchmarkSummaryStatus.BLOCKED
    raise ValueError("summary requires at least one classified case")
