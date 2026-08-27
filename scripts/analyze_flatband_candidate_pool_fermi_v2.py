#!/usr/bin/env python3
"""Build a non-selecting Fermi pre-audit for replenishment candidates."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from material_agent.research.flatband_intake import (
    FlatbandCalibrationIntakeManifestV1,
)
from material_agent.research.flatband_intake_bands_v2 import (
    build_fermi_candidate_pool_preaudit_v1,
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--intake-root", type=Path, action="append", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    sources = tuple(
        (
            FlatbandCalibrationIntakeManifestV1.model_validate_json(
                (root / "manifest.json").read_bytes()
            ),
            root,
        )
        for root in args.intake_root
    )
    result = build_fermi_candidate_pool_preaudit_v1(
        intake_sources=sources,
        output_path=args.output,
    )
    print(
        json.dumps(
            {
                "manifest_sha256": result.manifest_sha256,
                "audited_count": len(result.records),
                "unavailable_count": len(result.unavailable_record_ids),
                "algorithmic_positive_count": len(
                    result.algorithmic_positive_record_ids
                ),
                "pilot_execution_authorized": result.pilot_execution_authorized,
                "target_class_assignment_authorized": (
                    result.target_class_assignment_authorized
                ),
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
