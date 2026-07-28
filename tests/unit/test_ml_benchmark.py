from __future__ import annotations

import json
from pathlib import Path

import pytest

from material_agent.ml_screening.benchmark import (
    AGENT02_BENCHMARK_VERSION,
    BenchmarkReferenceData,
    BenchmarkReferenceMetadata,
    BenchmarkReferenceObservation,
    BenchmarkReferenceObservable,
    BenchmarkDryRunResult,
    BenchmarkManifest,
    BenchmarkReadinessStatus,
    ReferenceReadinessStatus,
    canonical_manifest_sha256,
    load_benchmark_manifest,
    run_benchmark_dry_run,
    sha256_bytes,
    validate_reference_readiness,
)
REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
MANIFEST_PATH = REPOSITORY_ROOT / "tests/fixtures/benchmarks/agent02-benchmark-v1-si.json"
STRUCTURE_PATH = REPOSITORY_ROOT / "tests/fixtures/real_ml/si-diamond.cif"
CASE_ID = "si-diamond-release-v1"


def test_benchmark_v1_si_dry_run_is_metadata_only() -> None:
    manifest = load_benchmark_manifest(MANIFEST_PATH)
    result = run_benchmark_dry_run(manifest, {CASE_ID: STRUCTURE_PATH})

    assert manifest.schema_version == AGENT02_BENCHMARK_VERSION
    assert result.mode.value == "DRY_RUN"
    assert result.status.value == "DRY_RUN_ONLY"
    assert result.evidence_level == "NONE"
    assert result.scientific_conclusion is False
    assert result.scientific_claims == []
    assert result.reference_readiness is BenchmarkReadinessStatus.BLOCKED
    assert len(result.metric_ids) == 6
    assert result.metric_ids[0].value == "energy_delta_ev_atom"
    assert result.cases[0].reference_evaluated is False
    assert result.cases[0].reference_readiness is ReferenceReadinessStatus.BLOCKED_REFERENCE_MISSING
    assert result.cases[0].parsed_formula == "Si2"
    assert result.cases[0].parsed_elements == ["Si"]
    assert result.cases[0].parsed_num_sites == 2


def test_benchmark_manifest_and_dry_run_json_are_deterministic() -> None:
    first = load_benchmark_manifest(MANIFEST_PATH)
    second = load_benchmark_manifest(MANIFEST_PATH)
    first_result = run_benchmark_dry_run(first, {CASE_ID: STRUCTURE_PATH})
    second_result = run_benchmark_dry_run(second, {CASE_ID: STRUCTURE_PATH})

    assert canonical_manifest_sha256(first) == canonical_manifest_sha256(second)
    assert first_result.model_dump_json() == second_result.model_dump_json()


def test_benchmark_dry_run_rejects_hash_and_size_tampering() -> None:
    payload = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    payload["cases"][0]["structure_artifact"]["sha256"] = "0" * 64
    tampered_hash = BenchmarkManifest.model_validate(payload)
    with pytest.raises(ValueError, match="hash mismatch"):
        run_benchmark_dry_run(tampered_hash, {CASE_ID: STRUCTURE_PATH})

    payload = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    payload["cases"][0]["structure_artifact"]["size_bytes"] += 1
    tampered_size = BenchmarkManifest.model_validate(payload)
    with pytest.raises(ValueError, match="size mismatch"):
        run_benchmark_dry_run(tampered_size, {CASE_ID: STRUCTURE_PATH})


def test_benchmark_dry_run_rejects_metadata_mismatch() -> None:
    payload = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    payload["cases"][0]["formula"] = "Si"
    manifest = BenchmarkManifest.model_validate(payload)
    with pytest.raises(ValueError, match="formula mismatch"):
        run_benchmark_dry_run(manifest, {CASE_ID: STRUCTURE_PATH})


def test_dry_run_contract_rejects_scientific_claims() -> None:
    payload = {
        "benchmark_id": "test",
        "revision": 1,
        "manifest_sha256": "0" * 64,
        "metric_ids": ["energy_delta_ev_atom"],
        "reference_readiness": "BLOCKED",
        "cases": [
            {
                "case_id": CASE_ID,
                "structure_sha256": "0" * 64,
                "parsed_formula": "Si2",
                "parsed_elements": ["Si"],
                "parsed_num_sites": 2,
                "reference_readiness": "BLOCKED_REFERENCE_MISSING",
            }
        ],
        "scientific_claims": ["band_gap_validated"],
    }
    with pytest.raises(ValueError, match="scientific claims"):
        BenchmarkDryRunResult.model_validate(payload)


def test_reference_readiness_accepts_matching_metadata_without_evaluating_metrics(
    tmp_path: Path,
) -> None:
    payload = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    structure_sha256 = payload["cases"][0]["structure_artifact"]["sha256"]
    metadata = BenchmarkReferenceMetadata(
        method_id="dft-reference-v1",
        code_version="vasp-pinned-placeholder",
        functional="PBE",
        dispersion=None,
        spin_policy="non-spin-polarized",
        u_j_policy="none",
    )
    data = BenchmarkReferenceData(
        case_id=CASE_ID,
        structure_sha256=structure_sha256,
        method=metadata,
        observations=[
            BenchmarkReferenceObservation(
                observable=BenchmarkReferenceObservable.ENERGY_EV_ATOM,
                unit="eV/atom",
                value=-5.0,
            )
        ],
    )
    reference_payload = data.model_dump_json().encode("utf-8")
    reference_path = tmp_path / "si-reference.json"
    reference_path.write_bytes(reference_payload)
    payload["cases"][0]["reference"] = {
        **metadata.model_dump(mode="json"),
        "artifact": {
            "uri": "artifact://benchmarks/agent02-benchmark-v1/si-reference.json",
            "sha256": sha256_bytes(reference_payload),
            "size_bytes": len(reference_payload),
            "media_type": "application/json",
        },
    }
    manifest = BenchmarkManifest.model_validate(payload)

    readiness = validate_reference_readiness(manifest, {CASE_ID: reference_path})
    assert readiness.status is BenchmarkReadinessStatus.READY
    assert readiness.cases[0].status is ReferenceReadinessStatus.READY

    result = run_benchmark_dry_run(
        manifest,
        {CASE_ID: STRUCTURE_PATH},
        {CASE_ID: reference_path},
    )
    assert result.reference_readiness is BenchmarkReadinessStatus.READY
    assert result.cases[0].reference_evaluated is False
    assert result.scientific_conclusion is False

    tampered_manifest_payload = json.loads(json.dumps(payload))
    tampered_manifest_payload["cases"][0]["reference"]["method_id"] = "other-method"
    tampered_manifest = BenchmarkManifest.model_validate(tampered_manifest_payload)
    tampered_readiness = validate_reference_readiness(
        tampered_manifest,
        {CASE_ID: reference_path},
    )
    assert (
        tampered_readiness.cases[0].status
        is ReferenceReadinessStatus.BLOCKED_REFERENCE_METADATA
    )


def test_reference_observable_rejects_wrong_unit_and_non_finite_value() -> None:
    with pytest.raises(ValueError, match="requires unit"):
        BenchmarkReferenceObservation(
            observable=BenchmarkReferenceObservable.ENERGY_EV_ATOM,
            unit="Ha",
            value=-5.0,
        )
    with pytest.raises(ValueError, match="finite numbers"):
        BenchmarkReferenceObservation(
            observable=BenchmarkReferenceObservable.ENERGY_EV_ATOM,
            unit="eV/atom",
            value=float("inf"),
        )
