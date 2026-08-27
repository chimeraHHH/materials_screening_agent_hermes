#!/usr/bin/env python3
"""Select 12 rank-first calibration candidates after Fermi-window pre-audit."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from material_agent.research.flatband_intake import (
    FlatbandCalibrationIntakeManifestV1,
)
from material_agent.research.flatband_intake_bands_v2 import (
    FlatbandFermiCandidatePoolPreauditManifestV1,
    build_fermi_qualified_selection_v2,
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--intake-root", type=Path, action="append", required=True)
    parser.add_argument("--pool-preaudit", type=Path, action="append", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    intakes = tuple(
        FlatbandCalibrationIntakeManifestV1.model_validate_json(
            (root / "manifest.json").read_bytes()
        )
        for root in args.intake_root
    )
    audits = tuple(
        FlatbandFermiCandidatePoolPreauditManifestV1.model_validate_json(
            path.read_bytes()
        )
        for path in args.pool_preaudit
    )
    result = build_fermi_qualified_selection_v2(
        intake_manifests=intakes,
        pool_preaudits=audits,
        output_path=args.output,
    )
    print(
        json.dumps(
            {
                "manifest_sha256": result.manifest_sha256,
                "candidate_count": len(result.candidates),
                "selected_record_ids": result.selected_record_ids,
                "positive_not_selected_count": len(
                    result.positive_not_selected_record_ids
                ),
                "nonpositive_count": len(result.nonpositive_record_ids),
                "unavailable_count": len(result.unavailable_record_ids),
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
