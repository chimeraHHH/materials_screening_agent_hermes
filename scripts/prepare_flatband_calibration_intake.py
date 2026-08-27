#!/usr/bin/env python3
"""Fetch a private, calibration-only C2DB intake from pinned Struct2Flat seeds."""

from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path

import requests

from material_agent.research.flatband_intake import (
    fetch_c2db_calibration_intake,
    read_struct2flat_calibration_seeds,
    sha256_file,
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--seed-file", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--max-records", type=int, default=12)
    parser.add_argument("--start-rank", type=int, default=1)
    parser.add_argument("--timeout-seconds", type=float, default=30.0)
    args = parser.parse_args()

    seeds = read_struct2flat_calibration_seeds(
        args.seed_file,
        max_records=args.max_records,
        start_rank=args.start_rank,
    )
    with requests.Session() as session:
        manifest = fetch_c2db_calibration_intake(
            seeds=seeds,
            seed_file_sha256=sha256_file(args.seed_file),
            output_root=args.output_root,
            session=session,
            retrieved_at=datetime.now(UTC).isoformat(),
            timeout_seconds=args.timeout_seconds,
        )
    print(
        json.dumps(
            {
                "manifest_sha256": manifest.manifest_sha256,
                "record_count": len(manifest.records),
                "complete_count": sum(
                    item.status == "COMPLETE" for item in manifest.records
                ),
                "partial_count": sum(
                    item.status == "PARTIAL" for item in manifest.records
                ),
                "failed_count": sum(
                    item.status == "FAILED" for item in manifest.records
                ),
                "pilot_execution_authorized": (
                    manifest.pilot_execution_authorized
                ),
                "manifest_path": str((args.output_root / "manifest.json").resolve()),
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
