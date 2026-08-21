"""Pure, test-only metric kernels for the Agent02 benchmark contract.

The kernel accepts explicitly synthetic numeric inputs so its formulas and
validation can be frozen before expert DFT reference data exists.  Results
from this module are never scientific evidence and cannot be published as a
model benchmark.
"""

from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
from typing import Any, Literal

from pydantic import Field, field_validator, model_validator

from material_agent.ml_screening.benchmark import BenchmarkMetric
from material_agent.ml_screening.models import Sha256, StrictFrozenModel

BENCHMARK_METRIC_INPUT_VERSION = "agent02-benchmark-metric-input-v1"
BENCHMARK_METRIC_RESULT_VERSION = "agent02-benchmark-metric-result-v1"


class BenchmarkNumericValue(StrictFrozenModel):
    unit: str = Field(min_length=1)
    value: Any

    @field_validator("value")
    @classmethod
    def require_rectangular_finite_numeric_value(cls, value: Any) -> Any:
        _numeric_shape(value)
        _numeric_values(value)
        return value


class SyntheticBenchmarkMetricInput(StrictFrozenModel):
    schema_version: Literal["agent02-benchmark-metric-input-v1"] = (
        BENCHMARK_METRIC_INPUT_VERSION
    )
    case_id: str = Field(min_length=1)
    is_synthetic: Literal[True] = True
    evaluation_status: Literal["TEST_ONLY"] = "TEST_ONLY"
    reference_calculation_level_id: str = Field(min_length=1)
    expected_reference_calculation_level_id: str = Field(min_length=1)
    reference_structure_sha256: Sha256
    prediction_structure_sha256: Sha256
    num_sites: int = Field(ge=1)
    stress_convention: Literal["cartesian_symmetric_3x3_gpa"] = (
        "cartesian_symmetric_3x3_gpa"
    )
    positions_aligned: Literal[True] = True
    reference_energy: BenchmarkNumericValue
    prediction_energy: BenchmarkNumericValue
    reference_forces: BenchmarkNumericValue
    prediction_forces: BenchmarkNumericValue
    reference_stress: BenchmarkNumericValue
    prediction_stress: BenchmarkNumericValue
    reference_lattice: BenchmarkNumericValue
    prediction_lattice: BenchmarkNumericValue
    reference_positions: BenchmarkNumericValue
    prediction_positions: BenchmarkNumericValue

    @model_validator(mode="after")
    def validate_contract(self) -> SyntheticBenchmarkMetricInput:
        if (
            self.reference_calculation_level_id
            != self.expected_reference_calculation_level_id
        ):
            raise ValueError("reference calculation level does not match benchmark")
        if self.reference_structure_sha256 != self.prediction_structure_sha256:
            raise ValueError("reference and prediction structure hashes must match")

        _require_unit(self.reference_energy, "eV/atom", "reference_energy")
        _require_unit(self.prediction_energy, "eV/atom", "prediction_energy")
        _require_shape(self.reference_energy.value, (), "reference_energy")
        _require_shape(self.prediction_energy.value, (), "prediction_energy")

        expected_site_shape = (self.num_sites, 3)
        for name, observation in (
            ("reference_forces", self.reference_forces),
            ("prediction_forces", self.prediction_forces),
        ):
            _require_unit(observation, "eV/angstrom", name)
            _require_shape(observation.value, expected_site_shape, name)

        for name, observation in (
            ("reference_stress", self.reference_stress),
            ("prediction_stress", self.prediction_stress),
        ):
            _require_unit(observation, "GPa", name)
            _require_shape(observation.value, (3, 3), name)
            _require_symmetric(observation.value, name)

        for name, observation in (
            ("reference_lattice", self.reference_lattice),
            ("prediction_lattice", self.prediction_lattice),
        ):
            _require_unit(observation, "angstrom", name)
            _require_shape(observation.value, (3, 3), name)

        for name, observation in (
            ("reference_positions", self.reference_positions),
            ("prediction_positions", self.prediction_positions),
        ):
            _require_unit(observation, "angstrom", name)
            _require_shape(observation.value, expected_site_shape, name)
        return self


class BenchmarkMetricValue(StrictFrozenModel):
    metric: BenchmarkMetric
    value: float
    unit: str = Field(min_length=1)
    definition: str = Field(min_length=1)


class SyntheticBenchmarkMetricResult(StrictFrozenModel):
    schema_version: Literal["agent02-benchmark-metric-result-v1"] = (
        BENCHMARK_METRIC_RESULT_VERSION
    )
    case_id: str = Field(min_length=1)
    input_sha256: Sha256
    reference_calculation_level_id: str = Field(min_length=1)
    is_synthetic: Literal[True] = True
    evaluation_status: Literal["TEST_ONLY"] = "TEST_ONLY"
    metrics: list[BenchmarkMetricValue] = Field(min_length=1)
    evidence_level: Literal["NONE"] = "NONE"
    scientific_conclusion: Literal[False] = False
    scientific_claims: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_result(self) -> SyntheticBenchmarkMetricResult:
        if self.scientific_claims:
            raise ValueError("synthetic metric result cannot contain scientific claims")
        metrics = [metric.metric for metric in self.metrics]
        if metrics != list(BenchmarkMetric):
            raise ValueError("synthetic metric result must contain all metrics in order")
        return self


def compute_synthetic_benchmark_metrics(
    data: SyntheticBenchmarkMetricInput,
) -> SyntheticBenchmarkMetricResult:
    reference_energy = float(data.reference_energy.value)
    prediction_energy = float(data.prediction_energy.value)
    reference_forces = _matrix(data.reference_forces.value)
    prediction_forces = _matrix(data.prediction_forces.value)
    reference_stress = _matrix(data.reference_stress.value)
    prediction_stress = _matrix(data.prediction_stress.value)
    reference_lattice = _matrix(data.reference_lattice.value)
    prediction_lattice = _matrix(data.prediction_lattice.value)
    reference_positions = _matrix(data.reference_positions.value)
    prediction_positions = _matrix(data.prediction_positions.value)

    force_differences = [
        [predicted - reference for predicted, reference in zip(predicted_row, reference_row)]
        for predicted_row, reference_row in zip(prediction_forces, reference_forces)
    ]
    force_components = [abs(value) for row in force_differences for value in row]
    force_norms = [_euclidean_norm(row) for row in force_differences]
    stress_components = [
        abs(predicted - reference)
        for predicted_row, reference_row in zip(prediction_stress, reference_stress)
        for predicted, reference in zip(predicted_row, reference_row)
    ]
    lattice_difference = [
        predicted - reference
        for predicted_row, reference_row in zip(prediction_lattice, reference_lattice)
        for predicted, reference in zip(predicted_row, reference_row)
    ]
    lattice_reference = [value for row in reference_lattice for value in row]
    lattice_denominator = _euclidean_norm(lattice_reference)
    if lattice_denominator == 0.0:
        raise ValueError("reference lattice norm must be positive")
    position_norms = [
        _euclidean_norm(
            [predicted - reference for predicted, reference in zip(predicted_row, reference_row)]
        )
        for predicted_row, reference_row in zip(prediction_positions, reference_positions)
    ]

    values = [
        BenchmarkMetricValue(
            metric=BenchmarkMetric.ENERGY_DELTA_EV_ATOM,
            value=prediction_energy - reference_energy,
            unit="eV/atom",
            definition="signed prediction minus reference energy per atom",
        ),
        BenchmarkMetricValue(
            metric=BenchmarkMetric.FORCE_MAE_EV_ANGSTROM,
            value=sum(force_components) / len(force_components),
            unit="eV/angstrom",
            definition="mean absolute error over all Cartesian force components",
        ),
        BenchmarkMetricValue(
            metric=BenchmarkMetric.FORCE_MAX_EV_ANGSTROM,
            value=max(force_norms),
            unit="eV/angstrom",
            definition="maximum per-site Euclidean force-error norm",
        ),
        BenchmarkMetricValue(
            metric=BenchmarkMetric.STRESS_MAE_GPA,
            value=sum(stress_components) / len(stress_components),
            unit="GPa",
            definition="mean absolute error over the Cartesian 3x3 stress tensor",
        ),
        BenchmarkMetricValue(
            metric=BenchmarkMetric.LATTICE_RELATIVE_ERROR,
            value=_euclidean_norm(lattice_difference) / lattice_denominator,
            unit="dimensionless",
            definition="Frobenius norm of lattice difference divided by reference norm",
        ),
        BenchmarkMetricValue(
            metric=BenchmarkMetric.STRUCTURE_DRIFT_ANGSTROM,
            value=max(position_norms),
            unit="angstrom",
            definition="maximum per-site displacement for pre-aligned Cartesian positions",
        ),
    ]
    return SyntheticBenchmarkMetricResult(
        case_id=data.case_id,
        input_sha256=canonical_metric_input_sha256(data),
        reference_calculation_level_id=data.reference_calculation_level_id,
        metrics=values,
    )


def canonical_metric_input_sha256(data: SyntheticBenchmarkMetricInput) -> str:
    payload = json.dumps(
        data.model_dump(mode="json"),
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def canonical_metric_result_sha256(data: SyntheticBenchmarkMetricResult) -> str:
    payload = json.dumps(
        data.model_dump(mode="json"),
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def load_synthetic_metric_input(path: Path) -> SyntheticBenchmarkMetricInput:
    return SyntheticBenchmarkMetricInput.model_validate_json(
        path.read_text(encoding="utf-8")
    )


def _require_unit(observation: BenchmarkNumericValue, unit: str, name: str) -> None:
    if observation.unit != unit:
        raise ValueError(f"{name} requires unit {unit}")


def _require_shape(value: Any, shape: tuple[int, ...], name: str) -> None:
    actual = _numeric_shape(value)
    if actual != shape:
        raise ValueError(f"{name} requires shape {shape}, got {actual}")


def _numeric_shape(value: Any) -> tuple[int, ...]:
    if isinstance(value, bool):
        raise ValueError("benchmark numeric values cannot be bool")  # noqa: TRY004
    if isinstance(value, (int, float)):
        return ()
    if not isinstance(value, (list, tuple)) or not value:
        raise ValueError("benchmark numeric values must be non-empty scalars or arrays")
    child_shapes = [_numeric_shape(item) for item in value]
    if any(shape != child_shapes[0] for shape in child_shapes[1:]):
        raise ValueError("benchmark numeric arrays must be rectangular")
    return (len(value), *child_shapes[0])


def _numeric_values(value: Any) -> list[float]:
    if isinstance(value, bool):
        raise ValueError("benchmark numeric values cannot be bool")  # noqa: TRY004
    if isinstance(value, (int, float)):
        number = float(value)
        if not math.isfinite(number):
            raise ValueError("benchmark numeric values must be finite")
        return [number]
    if isinstance(value, (list, tuple)):
        return [number for item in value for number in _numeric_values(item)]
    raise ValueError("benchmark numeric values must contain only numbers")


def _matrix(value: Any) -> list[list[float]]:
    return [[float(component) for component in row] for row in value]


def _euclidean_norm(values: list[float]) -> float:
    return math.sqrt(sum(value * value for value in values))


def _require_symmetric(value: Any, name: str) -> None:
    matrix = _matrix(value)
    if any(
        not math.isclose(
            matrix[row][column],
            matrix[column][row],
            rel_tol=0.0,
            abs_tol=1e-12,
        )
        for row in range(3)
        for column in range(3)
    ):
        raise ValueError(f"{name} must be symmetric")
