"""Versioned, evaluation-only contracts for the Agent02 benchmark.

The benchmark contract is deliberately separate from the production Agent02
request/stage contracts.  A dry-run validates immutable structure inputs and
benchmark metadata only; it never invokes CHGNet, DFT, or a scientific metric
evaluator and cannot emit scientific evidence.
"""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Mapping
from enum import StrEnum
from pathlib import Path
from typing import Any, Literal

from pydantic import Field, field_validator, model_validator
from pymatgen.core import Structure

from material_agent.ml_screening.models import ArtifactRef, Sha256, StrictFrozenModel

AGENT02_BENCHMARK_VERSION = "agent02-benchmark-v1"


class BenchmarkSplit(StrEnum):
    CALIBRATION = "CALIBRATION"
    VALIDATION = "VALIDATION"
    TEST = "TEST"
    OOD = "OOD"


class BenchmarkRunMode(StrEnum):
    DRY_RUN = "DRY_RUN"


class BenchmarkRunStatus(StrEnum):
    DRY_RUN_ONLY = "DRY_RUN_ONLY"


class ReferenceReadinessStatus(StrEnum):
    READY = "READY"
    BLOCKED_REFERENCE_MISSING = "BLOCKED_REFERENCE_MISSING"
    BLOCKED_REFERENCE_INTEGRITY = "BLOCKED_REFERENCE_INTEGRITY"
    BLOCKED_REFERENCE_SCHEMA = "BLOCKED_REFERENCE_SCHEMA"
    BLOCKED_REFERENCE_METADATA = "BLOCKED_REFERENCE_METADATA"


class BenchmarkReadinessStatus(StrEnum):
    READY = "READY"
    BLOCKED = "BLOCKED"


class BenchmarkMetric(StrEnum):
    ENERGY_DELTA_EV_ATOM = "energy_delta_ev_atom"
    FORCE_MAE_EV_ANGSTROM = "force_mae_ev_angstrom"
    FORCE_MAX_EV_ANGSTROM = "force_max_ev_angstrom"
    STRESS_MAE_GPA = "stress_mae_gpa"
    LATTICE_RELATIVE_ERROR = "lattice_relative_error"
    STRUCTURE_DRIFT_ANGSTROM = "structure_drift_angstrom"


class BenchmarkAggregation(StrEnum):
    MEAN = "MEAN"
    MAX = "MAX"
    RMSE = "RMSE"


class BenchmarkMetricSpec(StrictFrozenModel):
    metric: BenchmarkMetric
    unit: str = Field(min_length=1)
    aggregation: BenchmarkAggregation
    description: str = Field(min_length=1)


class BenchmarkReferenceMetadata(StrictFrozenModel):
    """Method provenance required before a case can be scientifically evaluated."""

    method_id: str = Field(min_length=1)
    code_version: str = Field(min_length=1)
    functional: str = Field(min_length=1)
    dispersion: str | None = None
    spin_policy: str = Field(min_length=1)
    u_j_policy: str = Field(min_length=1)


class BenchmarkReference(BenchmarkReferenceMetadata):
    """Reference artifact pointer plus the approved method metadata."""

    artifact: ArtifactRef


class BenchmarkReferenceObservable(StrEnum):
    ENERGY_EV_ATOM = "energy_ev_atom"
    FORCES_EV_ANGSTROM = "forces_ev_angstrom"
    STRESS_GPA = "stress_gpa"
    LATTICE_ANGSTROM = "lattice_angstrom"


_REFERENCE_OBSERVABLE_UNITS = {
    BenchmarkReferenceObservable.ENERGY_EV_ATOM: "eV/atom",
    BenchmarkReferenceObservable.FORCES_EV_ANGSTROM: "eV/angstrom",
    BenchmarkReferenceObservable.STRESS_GPA: "GPa",
    BenchmarkReferenceObservable.LATTICE_ANGSTROM: "angstrom",
}


def _validate_finite_numeric_tree(value: Any) -> Any:
    if isinstance(value, bool) or value is None:
        raise ValueError("reference observable values must be finite numbers")
    if isinstance(value, (list, tuple)):
        return [_validate_finite_numeric_tree(item) for item in value]
    try:
        numeric = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("reference observable values must be finite numbers") from exc
    if math.isnan(numeric) or numeric in (float("inf"), float("-inf")):
        raise ValueError("reference observable values must be finite numbers")
    return value


class BenchmarkReferenceObservation(StrictFrozenModel):
    observable: BenchmarkReferenceObservable
    unit: str = Field(min_length=1)
    value: Any

    @field_validator("value")
    @classmethod
    def validate_value(cls, value: Any) -> Any:
        return _validate_finite_numeric_tree(value)

    @model_validator(mode="after")
    def validate_unit(self) -> BenchmarkReferenceObservation:
        expected = _REFERENCE_OBSERVABLE_UNITS[self.observable]
        if self.unit != expected:
            raise ValueError(
                f"{self.observable.value} requires unit {expected}"
            )
        return self


class BenchmarkReferenceData(StrictFrozenModel):
    """Content contract for a future, expert-approved reference artifact."""

    schema_version: Literal["agent02-benchmark-reference-v1"] = (
        "agent02-benchmark-reference-v1"
    )
    case_id: str = Field(min_length=1)
    structure_sha256: Sha256
    method: BenchmarkReferenceMetadata
    observations: list[BenchmarkReferenceObservation] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_observables(self) -> BenchmarkReferenceData:
        names = [observation.observable for observation in self.observations]
        if len(names) != len(set(names)):
            raise ValueError("reference observables must be unique")
        return self


class BenchmarkReadinessCase(StrictFrozenModel):
    case_id: str = Field(min_length=1)
    status: ReferenceReadinessStatus
    missing_fields: list[str] = Field(default_factory=list)
    detail: str = Field(min_length=1)


class BenchmarkReadinessResult(StrictFrozenModel):
    schema_version: Literal["agent02-benchmark-v1"] = AGENT02_BENCHMARK_VERSION
    benchmark_id: str = Field(min_length=1)
    status: BenchmarkReadinessStatus
    cases: list[BenchmarkReadinessCase] = Field(min_length=1)


class BenchmarkCase(StrictFrozenModel):
    case_id: str = Field(min_length=1)
    split: BenchmarkSplit
    structure_artifact: ArtifactRef
    structure_id: str = Field(min_length=1)
    formula: str = Field(min_length=1)
    elements: list[str] = Field(min_length=1)
    num_sites: int = Field(ge=1)
    dimensionality: Literal[0, 1, 2, 3]
    reference: BenchmarkReference | None = None

    @model_validator(mode="after")
    def validate_elements(self) -> BenchmarkCase:
        if self.elements != sorted(set(self.elements)):
            raise ValueError("benchmark elements must be sorted and unique")
        return self


class BenchmarkManifest(StrictFrozenModel):
    schema_version: Literal["agent02-benchmark-v1"] = AGENT02_BENCHMARK_VERSION
    benchmark_id: str = Field(min_length=1)
    revision: int = Field(ge=1)
    description: str = Field(min_length=1)
    metrics: list[BenchmarkMetricSpec] = Field(min_length=1)
    cases: list[BenchmarkCase] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_case_ids(self) -> BenchmarkManifest:
        case_ids = [case.case_id for case in self.cases]
        if len(case_ids) != len(set(case_ids)):
            raise ValueError("benchmark case_id values must be unique")
        metric_ids = [metric.metric for metric in self.metrics]
        if len(metric_ids) != len(set(metric_ids)):
            raise ValueError("benchmark metric values must be unique")
        return self


class BenchmarkDryRunCase(StrictFrozenModel):
    case_id: str = Field(min_length=1)
    status: BenchmarkRunStatus = BenchmarkRunStatus.DRY_RUN_ONLY
    structure_sha256: Sha256
    parsed_formula: str = Field(min_length=1)
    parsed_elements: list[str] = Field(min_length=1)
    parsed_num_sites: int = Field(ge=1)
    reference_readiness: ReferenceReadinessStatus
    reference_evaluated: Literal[False] = False


class BenchmarkDryRunResult(StrictFrozenModel):
    schema_version: Literal["agent02-benchmark-v1"] = AGENT02_BENCHMARK_VERSION
    benchmark_id: str = Field(min_length=1)
    revision: int = Field(ge=1)
    mode: BenchmarkRunMode = BenchmarkRunMode.DRY_RUN
    status: BenchmarkRunStatus = BenchmarkRunStatus.DRY_RUN_ONLY
    manifest_sha256: Sha256
    metric_ids: list[BenchmarkMetric] = Field(min_length=1)
    cases: list[BenchmarkDryRunCase] = Field(min_length=1)
    reference_readiness: BenchmarkReadinessStatus
    evidence_level: Literal["NONE"] = "NONE"
    scientific_conclusion: Literal[False] = False
    scientific_claims: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def forbid_scientific_claims(self) -> BenchmarkDryRunResult:
        if self.scientific_claims:
            raise ValueError("benchmark dry-run cannot contain scientific claims")
        return self


def sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def canonical_manifest_sha256(manifest: BenchmarkManifest) -> str:
    payload = json.dumps(
        manifest.model_dump(mode="json"),
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return sha256_bytes(payload)


def load_benchmark_manifest(path: Path) -> BenchmarkManifest:
    return BenchmarkManifest.model_validate_json(path.read_text(encoding="utf-8"))


def validate_reference_readiness(
    manifest: BenchmarkManifest,
    reference_paths: Mapping[str, Path],
) -> BenchmarkReadinessResult:
    """Validate expert reference artifacts without evaluating model metrics."""

    results: list[BenchmarkReadinessCase] = []
    for case in manifest.cases:
        reference = case.reference
        if reference is None:
            results.append(
                BenchmarkReadinessCase(
                    case_id=case.case_id,
                    status=ReferenceReadinessStatus.BLOCKED_REFERENCE_MISSING,
                    missing_fields=["reference", "reference_artifact"],
                    detail="case has no expert-approved reference artifact",
                )
            )
            continue
        path = reference_paths.get(case.case_id)
        if path is None or not path.is_file():
            results.append(
                BenchmarkReadinessCase(
                    case_id=case.case_id,
                    status=ReferenceReadinessStatus.BLOCKED_REFERENCE_MISSING,
                    missing_fields=["reference_path"],
                    detail="reference artifact path is missing",
                )
            )
            continue
        payload = path.read_bytes()
        digest = sha256_bytes(payload)
        if digest != reference.artifact.sha256 or len(payload) != reference.artifact.size_bytes:
            results.append(
                BenchmarkReadinessCase(
                    case_id=case.case_id,
                    status=ReferenceReadinessStatus.BLOCKED_REFERENCE_INTEGRITY,
                    detail="reference artifact hash or size does not match the manifest",
                )
            )
            continue
        try:
            data = BenchmarkReferenceData.model_validate_json(payload)
        except Exception as exc:  # noqa: BLE001
            results.append(
                BenchmarkReadinessCase(
                    case_id=case.case_id,
                    status=ReferenceReadinessStatus.BLOCKED_REFERENCE_SCHEMA,
                    detail=f"reference artifact schema is invalid: {exc}",
                )
            )
            continue
        if data.case_id != case.case_id or data.structure_sha256 != case.structure_artifact.sha256:
            results.append(
                BenchmarkReadinessCase(
                    case_id=case.case_id,
                    status=ReferenceReadinessStatus.BLOCKED_REFERENCE_METADATA,
                    detail="reference case or structure hash does not match the manifest",
                )
            )
            continue
        if data.method != BenchmarkReferenceMetadata.model_validate(
            reference.model_dump(exclude={"artifact"})
        ):
            results.append(
                BenchmarkReadinessCase(
                    case_id=case.case_id,
                    status=ReferenceReadinessStatus.BLOCKED_REFERENCE_METADATA,
                    detail="reference method metadata does not match the manifest",
                )
            )
            continue
        results.append(
            BenchmarkReadinessCase(
                case_id=case.case_id,
                status=ReferenceReadinessStatus.READY,
                detail="reference artifact is structurally ready; metrics not evaluated",
            )
        )

    overall = (
        BenchmarkReadinessStatus.READY
        if all(case.status is ReferenceReadinessStatus.READY for case in results)
        else BenchmarkReadinessStatus.BLOCKED
    )
    return BenchmarkReadinessResult(
        benchmark_id=manifest.benchmark_id,
        status=overall,
        cases=results,
    )


def run_benchmark_dry_run(
    manifest: BenchmarkManifest,
    structure_paths: Mapping[str, Path],
    reference_paths: Mapping[str, Path] | None = None,
) -> BenchmarkDryRunResult:
    """Validate a manifest and its immutable structures without inference.

    ``structure_paths`` is explicit by case ID.  The runner never discovers a
    latest artifact and never evaluates or synthesizes reference values.
    """

    readiness = validate_reference_readiness(manifest, reference_paths or {})
    readiness_by_case = {case.case_id: case for case in readiness.cases}
    results: list[BenchmarkDryRunCase] = []
    for case in manifest.cases:
        path = structure_paths.get(case.case_id)
        if path is None:
            raise ValueError(f"missing structure path for benchmark case {case.case_id}")
        if not path.is_file():
            raise ValueError(f"benchmark structure is not a file: {path}")
        payload = path.read_bytes()
        digest = sha256_bytes(payload)
        if digest != case.structure_artifact.sha256:
            raise ValueError(f"structure hash mismatch for benchmark case {case.case_id}")
        if len(payload) != case.structure_artifact.size_bytes:
            raise ValueError(f"structure size mismatch for benchmark case {case.case_id}")

        try:
            structure = Structure.from_file(path)
        except Exception as exc:  # pragma: no cover - exact parser errors vary
            raise ValueError(f"failed to parse benchmark structure {case.case_id}") from exc

        parsed_formula = structure.formula
        parsed_elements = sorted({str(site.specie.symbol) for site in structure})
        if parsed_formula != case.formula:
            raise ValueError(f"structure formula mismatch for benchmark case {case.case_id}")
        if parsed_elements != case.elements:
            raise ValueError(f"structure elements mismatch for benchmark case {case.case_id}")
        if len(structure) != case.num_sites:
            raise ValueError(f"structure site-count mismatch for benchmark case {case.case_id}")

        results.append(
            BenchmarkDryRunCase(
                case_id=case.case_id,
                structure_sha256=digest,
                parsed_formula=parsed_formula,
                parsed_elements=parsed_elements,
                parsed_num_sites=len(structure),
                reference_readiness=readiness_by_case[case.case_id].status,
            )
        )

    return BenchmarkDryRunResult(
        benchmark_id=manifest.benchmark_id,
        revision=manifest.revision,
        manifest_sha256=canonical_manifest_sha256(manifest),
        metric_ids=[metric.metric for metric in manifest.metrics],
        cases=results,
        reference_readiness=readiness.status,
    )
