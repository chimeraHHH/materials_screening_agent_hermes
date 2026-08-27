#!/usr/bin/env python3
"""Select 12 complete calibration records from frozen private intake manifests."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from material_agent.research.flatband_intake import (
    FlatbandCalibrationIntakeManifestV1,
    select_flatband_calibration_records,
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("manifests", nargs="+", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    manifests = tuple(
        FlatbandCalibrationIntakeManifestV1.model_validate_json(path.read_bytes())
        for path in args.manifests
    )
    selection = select_flatband_calibration_records(
        intake_manifests=manifests,
        output_path=args.output,
    )
    print(
        json.dumps(
            {
                "manifest_sha256": selection.manifest_sha256,
                "selected_record_ids": selection.selected_record_ids,
                "partial_record_ids": selection.partial_record_ids,
                "nonselected_record_ids": selection.nonselected_record_ids,
                "pilot_execution_authorized": (
                    selection.pilot_execution_authorized
                ),
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
