#!/usr/bin/env python3
"""Group every Fermi-qualified positive structure before case construction."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from material_agent.research.flatband_intake import (
    FlatbandCalibrationIntakeManifestV1,
)
from material_agent.research.flatband_intake_bands_v2 import (
    FlatbandFermiQualifiedSelectionManifestV2,
)
from material_agent.research.flatband_intake_structure_audit import (
    build_positive_structure_grouping_preaudit_v1,
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--selection", type=Path, required=True)
    parser.add_argument("--intake-root", type=Path, action="append", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    selection = FlatbandFermiQualifiedSelectionManifestV2.model_validate_json(
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
    result = build_positive_structure_grouping_preaudit_v1(
        selection=selection,
        intake_sources=sources,
        output_path=args.output,
    )
    print(
        json.dumps(
            {
                "manifest_sha256": result.manifest_sha256,
                "positive_record_count": len(result.records),
                "component_count": len(result.components),
                "matched_pair_count": sum(item.fit_anonymous for item in result.pairs),
                "pilot_execution_authorized": result.pilot_execution_authorized,
                "formal_structure_grouping_release": (
                    result.formal_structure_grouping_release
                ),
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
