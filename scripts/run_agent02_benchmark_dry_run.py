#!/usr/bin/env python3
"""Run the metadata-only Agent02 benchmark-v1 Si dry-run."""

from __future__ import annotations

import argparse
from pathlib import Path

from material_agent.ml_screening.benchmark import (
    load_benchmark_manifest,
    run_benchmark_dry_run,
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--case-id", required=True)
    parser.add_argument("--structure", type=Path, required=True)
    args = parser.parse_args()
    manifest = load_benchmark_manifest(args.manifest)
    result = run_benchmark_dry_run(
        manifest,
        structure_paths={args.case_id: args.structure},
    )
    print(result.model_dump_json(indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
