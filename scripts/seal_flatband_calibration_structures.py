#!/usr/bin/env python3
"""Seal selected C2DB calibration structures into formal private custody."""

from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path

from material_agent.research.flatband_intake import (
    FlatbandCalibrationIntakeManifestV1,
    FlatbandCalibrationSelectionManifestV1,
)
from material_agent.research.flatband_intake_bands_v2 import (
    FlatbandFermiQualifiedSelectionManifestV2,
)
from material_agent.research.flatband_intake_structure_audit import (
    FlatbandStructureDiverseSelectionManifestV3,
)
from material_agent.research.flatband_intake_structures import (
    seal_selected_calibration_structures,
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--selection", type=Path, required=True)
    parser.add_argument("--intake-root", type=Path, action="append", required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    args = parser.parse_args()

    selection_bytes = args.selection.read_bytes()
    selection_payload = json.loads(selection_bytes)
    schema_version = selection_payload.get("schema_version")
    if schema_version == "flatband-fermi-qualified-calibration-selection-manifest-v2":
        selection = FlatbandFermiQualifiedSelectionManifestV2.model_validate_json(
            selection_bytes
        )
    elif schema_version == "flatband-structure-diverse-calibration-selection-manifest-v3":
        selection = FlatbandStructureDiverseSelectionManifestV3.model_validate_json(
            selection_bytes
        )
    else:
        selection = FlatbandCalibrationSelectionManifestV1.model_validate_json(
            selection_bytes
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
    custody, envelope = seal_selected_calibration_structures(
        selection=selection,
        intake_sources=sources,
        output_root=args.output_root,
        sealed_at=datetime.now(UTC).isoformat(),
    )
    print(
        json.dumps(
            {
                "manifest_id": custody.manifest_id,
                "manifest_sha256": custody.manifest_sha256,
                "envelope_id": envelope.envelope_id,
                "envelope_sha256": envelope.envelope_sha256,
                "record_count": len(custody.records),
                "axis_ready_count": sum(
                    item.structure_grouping_axis_ready
                    for item in custody.records
                ),
                "pilot_execution_authorized": custody.pilot_execution_authorized,
                "output_root": str(args.output_root.resolve()),
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
