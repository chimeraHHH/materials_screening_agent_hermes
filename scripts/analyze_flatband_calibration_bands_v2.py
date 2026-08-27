#!/usr/bin/env python3
"""Build a Fermi-aligned, non-Gold bandwidth pre-audit."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from material_agent.research.flatband_intake import (
    FlatbandCalibrationIntakeManifestV1,
    FlatbandCalibrationSelectionManifestV1,
)
from material_agent.research.flatband_intake_bands_v2 import (
    build_calibration_fermi_preaudit_v2,
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--selection", type=Path, required=True)
    parser.add_argument("--intake-root", type=Path, action="append", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    selection = FlatbandCalibrationSelectionManifestV1.model_validate_json(
        args.selection.read_bytes()
    )
    sources = tuple(
        (
            FlatbandCalibrationIntakeManifestV1.model_validate_json(
                (root / "manifest.json").read_bytes()
            ),
            root,
        )
        for root in args.intake_root
    )
    result = build_calibration_fermi_preaudit_v2(
        selection=selection,
        intake_sources=sources,
        output_path=args.output,
    )
    print(
        json.dumps(
            {
                "manifest_sha256": result.manifest_sha256,
                "band_found_count": result.band_found_count,
                "no_band_in_window_count": result.no_band_in_window_count,
                "alignment_unavailable_count": result.alignment_unavailable_count,
                "fb100_count": result.fb100_count,
                "nb300_count": result.nb300_count,
                "border500_count": result.border500_count,
                "out_of_scope_count": result.out_of_scope_count,
                "target_class_assignment_authorized": (
                    result.target_class_assignment_authorized
                ),
                "pilot_execution_authorized": result.pilot_execution_authorized,
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
