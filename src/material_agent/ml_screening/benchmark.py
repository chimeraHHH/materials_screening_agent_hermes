"""Versioned, evaluation-only contracts for the Agent02 benchmark.

The benchmark contract is deliberately separate from the production Agent02
request/stage contracts.  A dry-run validates immutable structure inputs and
benchmark metadata only; it never invokes CHGNet, DFT, or a scientific metric
evaluator and cannot emit scientific evidence.
"""

from __future__ import annotations

import hashlib
import json
from enum import StrEnum
from pathlib import Path
from typing import Literal, Mapping

from pydantic import Field, model_validator
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


class BenchmarkReference(StrictFrozenModel):
    """Provenance required before a case can be scientifically evaluated."""

    method_id: str = Field(min_length=1)
    code_version: str = Field(min_length=1)
    functional: str = Field(min_length=1)
    dispersion: str | None = None
    spin_policy: str = Field(min_length=1)
    u_j_policy: str = Field(min_length=1)
    artifact: ArtifactRef


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


def run_benchmark_dry_run(
    manifest: BenchmarkManifest,
    structure_paths: Mapping[str, Path],
) -> BenchmarkDryRunResult:
    """Validate a manifest and its immutable structures without inference.

    ``structure_paths`` is explicit by case ID.  The runner never discovers a
    latest artifact and never evaluates or synthesizes reference values.
    """

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
            )
        )

    return BenchmarkDryRunResult(
        benchmark_id=manifest.benchmark_id,
        revision=manifest.revision,
        manifest_sha256=canonical_manifest_sha256(manifest),
        metric_ids=[metric.metric for metric in manifest.metrics],
        cases=results,
    )
