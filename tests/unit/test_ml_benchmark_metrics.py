from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from material_agent.ml_screening.benchmark import BenchmarkMetric
from material_agent.ml_screening.benchmark_metrics import (
    SyntheticBenchmarkMetricInput,
    SyntheticBenchmarkMetricResult,
    canonical_metric_input_sha256,
    compute_synthetic_benchmark_metrics,
    load_synthetic_metric_input,
)


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
FIXTURE_PATH = (
    REPOSITORY_ROOT
    / "tests/fixtures/benchmarks/agent02-benchmark-v1-synthetic-metrics.json"
)


def _payload() -> dict:
    return json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))


def test_synthetic_metric_kernel_has_exact_known_results() -> None:
    data = load_synthetic_metric_input(FIXTURE_PATH)
    result = compute_synthetic_benchmark_metrics(data)
    values = {metric.metric: metric.value for metric in result.metrics}

    assert result.is_synthetic is True
    assert result.evaluation_status == "TEST_ONLY"
    assert result.evidence_level == "NONE"
    assert result.scientific_conclusion is False
    assert result.scientific_claims == []
    assert values[BenchmarkMetric.ENERGY_DELTA_EV_ATOM] == pytest.approx(0.1)
    assert values[BenchmarkMetric.FORCE_MAE_EV_ANGSTROM] == pytest.approx(0.05)
    assert values[BenchmarkMetric.FORCE_MAX_EV_ANGSTROM] == pytest.approx(0.2)
    assert values[BenchmarkMetric.STRESS_MAE_GPA] == pytest.approx(2.0 / 3.0)
    assert values[BenchmarkMetric.LATTICE_RELATIVE_ERROR] == pytest.approx(0.01)
    assert values[BenchmarkMetric.STRUCTURE_DRIFT_ANGSTROM] == pytest.approx(0.1)
    assert result.input_sha256 == canonical_metric_input_sha256(data)


def test_synthetic_metric_kernel_zero_error_case() -> None:
    payload = _payload()
    for name in ("energy", "forces", "stress", "lattice", "positions"):
        payload[f"prediction_{name}"] = payload[f"reference_{name}"]
    result = compute_synthetic_benchmark_metrics(
        SyntheticBenchmarkMetricInput.model_validate(payload)
    )
    assert all(metric.value == 0.0 for metric in result.metrics)


@pytest.mark.parametrize(
    ("path", "value", "message"),
    [
        (("prediction_energy", "unit"), "Ha", "requires unit eV/atom"),
        (("prediction_forces", "value"), [[0.0, 0.0, 0.0]], "requires shape"),
        (("prediction_stress", "value"), [[0.0] * 3] * 2, "requires shape"),
        (("prediction_lattice", "value"), [[0.0] * 2] * 2, "requires shape"),
        (("prediction_positions", "value"), [[0.0, 0.0]], "requires shape"),
    ],
)
def test_synthetic_metric_input_rejects_units_and_shapes(
    path: tuple[str, str],
    value: object,
    message: str,
) -> None:
    payload = _payload()
    payload[path[0]][path[1]] = value
    with pytest.raises(ValidationError, match=message):
        SyntheticBenchmarkMetricInput.model_validate(payload)


def test_synthetic_metric_input_rejects_nonfinite_and_asymmetric_stress() -> None:
    payload = _payload()
    payload["prediction_energy"]["value"] = float("nan")
    with pytest.raises(ValidationError, match="must be finite"):
        SyntheticBenchmarkMetricInput.model_validate(payload)

    payload = _payload()
    payload["prediction_stress"]["value"][0][1] = 1.0
    with pytest.raises(ValidationError, match="must be symmetric"):
        SyntheticBenchmarkMetricInput.model_validate(payload)


def test_synthetic_metric_input_rejects_identity_and_level_mismatch() -> None:
    payload = _payload()
    payload["prediction_structure_sha256"] = "2" * 64
    with pytest.raises(ValidationError, match="structure hashes must match"):
        SyntheticBenchmarkMetricInput.model_validate(payload)

    payload = _payload()
    payload["reference_calculation_level_id"] = "other-level"
    with pytest.raises(ValidationError, match="calculation level"):
        SyntheticBenchmarkMetricInput.model_validate(payload)


def test_synthetic_metric_result_cannot_claim_scientific_evidence() -> None:
    result = compute_synthetic_benchmark_metrics(load_synthetic_metric_input(FIXTURE_PATH))
    payload = result.model_dump(mode="json")
    payload["scientific_claims"] = ["model_validated"]
    with pytest.raises(ValidationError, match="scientific claims"):
        SyntheticBenchmarkMetricResult.model_validate(payload)
