#!/usr/bin/env python3
"""Run the Agent02 benchmark metric kernel on explicit synthetic inputs."""

from __future__ import annotations

import argparse
from pathlib import Path

from material_agent.ml_screening.benchmark_metrics import (
    compute_synthetic_benchmark_metrics,
    load_synthetic_metric_input,
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, required=True)
    args = parser.parse_args()
    data = load_synthetic_metric_input(args.input)
    result = compute_synthetic_benchmark_metrics(data)
    print(result.model_dump_json(indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
