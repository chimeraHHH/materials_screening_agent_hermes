from __future__ import annotations

import json
import math
from pathlib import Path

import pytest
from pydantic import ValidationError

from material_agent.ml_screening.benchmark import (
    BenchmarkMetric,
    canonical_manifest_sha256,
    load_benchmark_manifest,
)
from material_agent.ml_screening.benchmark_metrics import (
    SyntheticBenchmarkMetricInput,
    SyntheticBenchmarkMetricResult,
    canonical_metric_result_sha256,
    compute_synthetic_benchmark_metrics,
)
from material_agent.ml_screening.benchmark_summary import (
    BenchmarkSummaryCaseStatus,
    BenchmarkSummaryStatus,
    SyntheticBenchmarkSummaryCase,
    SyntheticBenchmarkSummaryRequest,
    SyntheticBenchmarkSummaryResult,
    aggregate_synthetic_benchmark,
    render_synthetic_benchmark_summary,
)

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
METRIC_INPUT_PATH = (
    REPOSITORY_ROOT
    / "tests/fixtures/benchmarks/agent02-benchmark-v1-synthetic-metrics.json"
)
MANIFEST_PATH = (
    REPOSITORY_ROOT / "tests/fixtures/benchmarks/agent02-benchmark-v1-si.json"
)


def _metric_result(
    case_id: str,
    *,
    zero_error: bool = False,
    reference_level: str = "synthetic-reference-level-v1",
) -> SyntheticBenchmarkMetricResult:
    payload = json.loads(METRIC_INPUT_PATH.read_text(encoding="utf-8"))
    payload["case_id"] = case_id
    payload["reference_calculation_level_id"] = reference_level
    payload["expected_reference_calculation_level_id"] = reference_level
    if zero_error:
        for name in ("energy", "forces", "stress", "lattice", "positions"):
            payload[f"prediction_{name}"] = payload[f"reference_{name}"]
    return compute_synthetic_benchmark_metrics(
        SyntheticBenchmarkMetricInput.model_validate(payload)
    )


def _complete_case(result: SyntheticBenchmarkMetricResult) -> SyntheticBenchmarkSummaryCase:
    return SyntheticBenchmarkSummaryCase(
        case_id=result.case_id,
        status=BenchmarkSummaryCaseStatus.TEST_ONLY_COMPLETE,
        result=result,
        result_sha256=canonical_metric_result_sha256(result),
    )


def _request(
    cases: list[SyntheticBenchmarkSummaryCase],
    *,
    expected_case_ids: list[str] | None = None,
) -> SyntheticBenchmarkSummaryRequest:
    manifest = load_benchmark_manifest(MANIFEST_PATH)
    return SyntheticBenchmarkSummaryRequest(
        benchmark_id="synthetic-summary-v1",
        manifest_sha256=canonical_manifest_sha256(manifest),
        expected_case_ids=expected_case_ids or [case.case_id for case in cases],
        metrics=manifest.metrics,
        cases=cases,
    )


def test_synthetic_summary_aggregates_two_exact_cases() -> None:
    offset = _metric_result("synthetic-offset")
    zero = _metric_result("synthetic-zero", zero_error=True)
    request = _request([_complete_case(offset), _complete_case(zero)])
    result = aggregate_synthetic_benchmark(request)
    values = {metric.metric: metric.value for metric in result.aggregates}

    assert result.status is BenchmarkSummaryStatus.TEST_ONLY_COMPLETE
    assert result.completed_case_count == 2
    assert result.blocked_case_count == 0
    assert result.failed_case_count == 0
    assert result.is_synthetic is True
    assert result.evaluation_status == "TEST_ONLY"
    assert result.evidence_level == "NONE"
    assert result.scientific_conclusion is False
    assert result.scientific_claims == []
    assert values[BenchmarkMetric.ENERGY_DELTA_EV_ATOM] == pytest.approx(0.05)
    assert values[BenchmarkMetric.FORCE_MAE_EV_ANGSTROM] == pytest.approx(0.025)
    assert values[BenchmarkMetric.FORCE_MAX_EV_ANGSTROM] == pytest.approx(0.2)
    assert values[BenchmarkMetric.STRESS_MAE_GPA] == pytest.approx(1.0 / 3.0)
    assert values[BenchmarkMetric.LATTICE_RELATIVE_ERROR] == pytest.approx(0.005)
    assert values[BenchmarkMetric.STRUCTURE_DRIFT_ANGSTROM] == pytest.approx(0.1)
    assert result.model_dump_json() == aggregate_synthetic_benchmark(request).model_dump_json()


def test_synthetic_summary_supports_frozen_rmse_aggregation() -> None:
    offset = _metric_result("synthetic-offset")
    zero = _metric_result("synthetic-zero", zero_error=True)
    request = _request([_complete_case(offset), _complete_case(zero)])
    payload = request.model_dump(mode="json")
    payload["metrics"][0]["aggregation"] = "RMSE"
    result = aggregate_synthetic_benchmark(
        SyntheticBenchmarkSummaryRequest.model_validate(payload)
    )
    assert result.aggregates[0].value == pytest.approx(0.1 / math.sqrt(2.0))


def test_partial_summary_preserves_blocked_case_and_report_warning() -> None:
    complete = _complete_case(_metric_result("synthetic-offset"))
    blocked = SyntheticBenchmarkSummaryCase(
        case_id="synthetic-zero",
        status=BenchmarkSummaryCaseStatus.BLOCKED,
        error_code="REFERENCE_DATA_MISSING",
    )
    result = aggregate_synthetic_benchmark(_request([complete, blocked]))
    report = render_synthetic_benchmark_summary(result)

    assert result.status is BenchmarkSummaryStatus.PARTIAL
    assert result.completed_case_count == 1
    assert result.blocked_case_count == 1
    assert result.cases[1].error_code == "REFERENCE_DATA_MISSING"
    assert "TEST ONLY / SYNTHETIC" in report
    assert "not evidence of model accuracy" in report
    assert "REFERENCE_DATA_MISSING" in report


@pytest.mark.parametrize(
    ("case_status", "expected_status"),
    [
        (BenchmarkSummaryCaseStatus.BLOCKED, BenchmarkSummaryStatus.BLOCKED),
        (BenchmarkSummaryCaseStatus.FAILED, BenchmarkSummaryStatus.FAILED),
    ],
)
def test_summary_without_completed_cases_has_no_aggregates(
    case_status: BenchmarkSummaryCaseStatus,
    expected_status: BenchmarkSummaryStatus,
) -> None:
    case = SyntheticBenchmarkSummaryCase(
        case_id="synthetic-only",
        status=case_status,
        error_code="SYNTHETIC_FAILURE",
    )
    result = aggregate_synthetic_benchmark(_request([case]))
    assert result.status is expected_status
    assert result.aggregates == []


def test_failed_status_precedes_blocked_when_no_case_completed() -> None:
    cases = [
        SyntheticBenchmarkSummaryCase(
            case_id="synthetic-blocked",
            status=BenchmarkSummaryCaseStatus.BLOCKED,
            error_code="REFERENCE_DATA_MISSING",
        ),
        SyntheticBenchmarkSummaryCase(
            case_id="synthetic-failed",
            status=BenchmarkSummaryCaseStatus.FAILED,
            error_code="SYNTHETIC_FAILURE",
        ),
    ]
    result = aggregate_synthetic_benchmark(_request(cases))
    assert result.status is BenchmarkSummaryStatus.FAILED
    assert result.blocked_case_count == 1
    assert result.failed_case_count == 1


def test_summary_rejects_missing_duplicate_and_mixed_level_cases() -> None:
    offset = _complete_case(_metric_result("synthetic-offset"))
    with pytest.raises(ValidationError, match="expected_case_ids"):
        _request(
            [offset],
            expected_case_ids=["synthetic-offset", "synthetic-zero"],
        )

    request = _request([offset])
    payload = request.model_dump(mode="json")
    payload["expected_case_ids"] = ["synthetic-offset", "synthetic-offset"]
    payload["cases"] = [payload["cases"][0], payload["cases"][0]]
    with pytest.raises(ValidationError, match="sorted and unique"):
        SyntheticBenchmarkSummaryRequest.model_validate(payload)

    other = _complete_case(
        _metric_result(
            "synthetic-zero",
            zero_error=True,
            reference_level="other-synthetic-level",
        )
    )
    with pytest.raises(ValidationError, match="mix reference calculation levels"):
        _request([offset, other])


def test_summary_rejects_result_hash_tampering_and_scientific_claims() -> None:
    result = _metric_result("synthetic-offset")
    with pytest.raises(ValidationError, match="hash mismatch"):
        SyntheticBenchmarkSummaryCase(
            case_id=result.case_id,
            status=BenchmarkSummaryCaseStatus.TEST_ONLY_COMPLETE,
            result=result,
            result_sha256="0" * 64,
        )

    summary = aggregate_synthetic_benchmark(_request([_complete_case(result)]))
    payload = summary.model_dump(mode="json")
    payload["scientific_claims"] = ["model_validated"]
    with pytest.raises(ValidationError, match="scientific claims"):
        SyntheticBenchmarkSummaryResult.model_validate(payload)


def test_summary_result_rejects_tampered_counts_and_status() -> None:
    summary = aggregate_synthetic_benchmark(
        _request([_complete_case(_metric_result("synthetic-offset"))])
    )
    payload = summary.model_dump(mode="json")
    payload["completed_case_count"] = 0
    payload["blocked_case_count"] = 1
    with pytest.raises(ValidationError, match="counts do not match"):
        SyntheticBenchmarkSummaryResult.model_validate(payload)

    payload = summary.model_dump(mode="json")
    payload["status"] = "PARTIAL"
    with pytest.raises(ValidationError, match="status does not match"):
        SyntheticBenchmarkSummaryResult.model_validate(payload)
