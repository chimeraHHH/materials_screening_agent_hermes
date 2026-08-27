#!/usr/bin/env python3
"""Select 12 preliminary structure-diverse calibration candidates."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from material_agent.research.flatband_intake_bands_v2 import (
    FlatbandFermiQualifiedSelectionManifestV2,
)
from material_agent.research.flatband_intake_structure_audit import (
    PositiveStructureGroupingPreauditManifestV1,
    build_structure_diverse_selection_v3,
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--selection", type=Path, required=True)
    parser.add_argument("--structure-preaudit", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    selection = FlatbandFermiQualifiedSelectionManifestV2.model_validate_json(
        args.selection.read_bytes()
    )
    grouping = PositiveStructureGroupingPreauditManifestV1.model_validate_json(
        args.structure_preaudit.read_bytes()
    )
    result = build_structure_diverse_selection_v3(
        selection=selection,
        grouping=grouping,
        output_path=args.output,
    )
    print(
        json.dumps(
            {
                "manifest_sha256": result.manifest_sha256,
                "selected_record_ids": result.selected_record_ids,
                "replacement_count": len(result.replacement_record_ids),
                "pilot_execution_authorized": result.pilot_execution_authorized,
                "formal_structure_grouping_required": (
                    result.formal_structure_grouping_required
                ),
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
