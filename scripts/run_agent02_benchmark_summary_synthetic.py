#!/usr/bin/env python3
"""Aggregate two deterministic synthetic Agent02 benchmark cases."""

from __future__ import annotations

import argparse
from pathlib import Path

from material_agent.ml_screening.benchmark import (
    canonical_manifest_sha256,
    load_benchmark_manifest,
)
from material_agent.ml_screening.benchmark_metrics import (
    SyntheticBenchmarkMetricInput,
    canonical_metric_result_sha256,
    compute_synthetic_benchmark_metrics,
    load_synthetic_metric_input,
)
from material_agent.ml_screening.benchmark_summary import (
    BenchmarkSummaryCaseStatus,
    SyntheticBenchmarkSummaryCase,
    SyntheticBenchmarkSummaryRequest,
    aggregate_synthetic_benchmark,
    render_synthetic_benchmark_summary,
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--format", choices=("json", "markdown"), default="json")
    args = parser.parse_args()

    manifest = load_benchmark_manifest(args.manifest)
    base = load_synthetic_metric_input(args.input)
    offset_payload = base.model_dump(mode="json")
    offset_payload["case_id"] = "synthetic-offset"
    zero_payload = base.model_dump(mode="json")
    zero_payload["case_id"] = "synthetic-zero"
    for name in ("energy", "forces", "stress", "lattice", "positions"):
        zero_payload[f"prediction_{name}"] = zero_payload[f"reference_{name}"]
    metric_results = [
        compute_synthetic_benchmark_metrics(
            SyntheticBenchmarkMetricInput.model_validate(payload)
        )
        for payload in (offset_payload, zero_payload)
    ]
    cases = [
        SyntheticBenchmarkSummaryCase(
            case_id=result.case_id,
            status=BenchmarkSummaryCaseStatus.TEST_ONLY_COMPLETE,
            result=result,
            result_sha256=canonical_metric_result_sha256(result),
        )
        for result in metric_results
    ]
    request = SyntheticBenchmarkSummaryRequest(
        benchmark_id="synthetic-summary-v1",
        manifest_sha256=canonical_manifest_sha256(manifest),
        expected_case_ids=[case.case_id for case in cases],
        metrics=manifest.metrics,
        cases=cases,
    )
    result = aggregate_synthetic_benchmark(request)
    if args.format == "markdown":
        print(render_synthetic_benchmark_summary(result))
    else:
        print(result.model_dump_json(indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
