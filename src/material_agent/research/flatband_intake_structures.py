"""Seal selected real C2DB calibration structures into formal private custody.

The output is still pre-case: it contains no mechanism label, expert judgment,
split assignment, Gold label, or Pilot authorization.  It only closes the raw
source-to-normalized-structure identity needed before those reviews can start.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Annotated, Literal

from pydantic import Field, model_validator
from pymatgen.core import Element, Lattice, Structure

from material_agent.inspiration.models import (
    Identifier,
    Sha256,
    StrictModel,
    canonical_sha256,
    deterministic_id,
)
from material_agent.research.flatband_artifact_store import (
    PrivateArtifactEnvelopeV1,
    seal_private_artifact_envelope_v1,
    write_private_artifact_envelope_v1,
)
from material_agent.research.flatband_intake import (
    FlatbandCalibrationIntakeManifestV1,
    FlatbandCalibrationIntakeRecordV1,
)
from material_agent.research.flatband_structure_grouping import (
    StructureDimensionalityV2,
    build_raw_structure_artifact_v2,
    normalize_raw_structure_artifact_v2,
)


class FlatbandCalibrationStructureCustodyRecordV1(StrictModel):
    schema_version: Literal["flatband-calibration-structure-custody-record-v1"] = (
        "flatband-calibration-structure-custody-record-v1"
    )
    c2db_record_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
    source_intake_manifest_sha256: Sha256
    source_structure_response_sha256: Sha256
    normalized_structure_artifact_id: Identifier
    normalized_structure_artifact_sha256: Sha256
    normalized_structure_sha256: Sha256
    envelope_id: Identifier
    envelope_sha256: Sha256
    envelope_relative_path: str = Field(
        pattern=(
            r"^records/[A-Za-z0-9][A-Za-z0-9._-]*/"
            r"normalized-structure\.envelope\.json$"
        )
    )
    envelope_file_sha256: Sha256
    aperiodic_axis: Literal[0, 1, 2] | None
    structure_grouping_axis_ready: bool
    formal_case_status: Literal["NOT_CONSTRUCTED"] = "NOT_CONSTRUCTED"
    expert_annotation_status: Literal["NOT_RUN"] = "NOT_RUN"
    scientific_conclusion: Literal[False] = False

    @model_validator(mode="after")
    def validate_axis(self) -> FlatbandCalibrationStructureCustodyRecordV1:
        if self.structure_grouping_axis_ready != (self.aperiodic_axis is not None):
            raise ValueError("axis readiness differs from normalized structure evidence")
        return self


class FlatbandCalibrationStructureCustodyManifestV1(StrictModel):
    schema_version: Literal["flatband-calibration-structure-custody-manifest-v1"] = (
        "flatband-calibration-structure-custody-manifest-v1"
    )
    manifest_id: Identifier
    manifest_sha256: Sha256
    selection_manifest_sha256: Sha256
    records: Annotated[
        tuple[FlatbandCalibrationStructureCustodyRecordV1, ...],
        Field(min_length=12, max_length=12),
    ]
    calibration_only: Literal[True] = True
    pilot_execution_authorized: Literal[False] = False
    structure_grouping_input_manifest_status: Literal["NOT_CONSTRUCTED"] = (
        "NOT_CONSTRUCTED"
    )
    scientific_conclusion: Literal[False] = False

    @model_validator(mode="after")
    def validate_manifest(self) -> FlatbandCalibrationStructureCustodyManifestV1:
        ids = tuple(item.c2db_record_id for item in self.records)
        if ids != tuple(sorted(set(ids))):
            raise ValueError("structure custody records must be ID-sorted and unique")
        semantic = self.model_dump(
            mode="python", exclude={"manifest_id", "manifest_sha256"}
        )
        digest = canonical_sha256(semantic)
        if self.manifest_sha256 != digest:
            raise ValueError("structure custody manifest SHA-256 does not match")
        if self.manifest_id != deterministic_id(
            "flatband-cal-structure-custody",
            {"manifest_sha256": digest},
        ):
            raise ValueError("structure custody manifest ID does not match")
        return self


def seal_selected_calibration_structures(
    *,
    selection: StrictModel,
    intake_sources: tuple[
        tuple[FlatbandCalibrationIntakeManifestV1, Path], ...
    ],
    output_root: Path,
    sealed_at: str,
) -> tuple[
    FlatbandCalibrationStructureCustodyManifestV1,
    PrivateArtifactEnvelopeV1,
]:
    """Seal exactly the 12 selected source structures in owner-only envelopes."""

    selection_type = type(selection)
    if getattr(selection, "schema_version", None) not in {
        "flatband-calibration-selection-manifest-v1",
        "flatband-fermi-qualified-calibration-selection-manifest-v2",
        "flatband-structure-diverse-calibration-selection-manifest-v3",
    }:
        raise TypeError("structure custody requires a supported selection manifest")
    selected = selection_type.model_validate(
        selection.model_dump(mode="python", round_trip=True)
    )
    source_map: dict[str, tuple[FlatbandCalibrationIntakeManifestV1, Path]] = {}
    record_map: dict[
        str,
        tuple[
            FlatbandCalibrationIntakeManifestV1,
            Path,
            FlatbandCalibrationIntakeRecordV1,
        ],
    ] = {}
    for manifest, root in intake_sources:
        validated = FlatbandCalibrationIntakeManifestV1.model_validate(
            manifest.model_dump(mode="python", round_trip=True)
        )
        if validated.manifest_sha256 in source_map:
            raise ValueError("structure custody receives a repeated intake manifest")
        source_map[validated.manifest_sha256] = (validated, root)
        for record in validated.records:
            if record.c2db_record_id in record_map:
                raise ValueError("structure custody receives a repeated source record")
            record_map[record.c2db_record_id] = (validated, root, record)
    if set(source_map) != set(selected.source_manifest_sha256s):
        raise ValueError("structure custody intake roots differ from selection roots")

    output_root.mkdir(parents=True, exist_ok=True, mode=0o700)
    output_root.chmod(0o700)
    custody_records: list[FlatbandCalibrationStructureCustodyRecordV1] = []
    for record_id in selected.selected_record_ids:
        source = record_map.get(record_id)
        if source is None:
            raise ValueError("selected structure record is absent from intake roots")
        manifest, intake_root, raw_record = source
        artifacts = {
            item.artifact_role: item for item in raw_record.artifacts
        }
        source_artifact = artifacts.get("C2DB_STRUCTURE_JSON")
        if source_artifact is None:
            raise ValueError("selected record has no complete structure source")
        source_path = intake_root / source_artifact.local_relative_path
        source_bytes = source_path.read_bytes()
        if hashlib.sha256(source_bytes).hexdigest() != source_artifact.sha256:
            raise ValueError("selected C2DB structure source hash drifted")
        structure = _structure_from_c2db_json(source_bytes)
        pymatgen_bytes = json.dumps(
            structure.as_dict(),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
        raw = build_raw_structure_artifact_v2(
            private_artifact_uri=(
                "artifact://private/flatband-calibration-20260826/"
                f"{record_id}/structure.pymatgen.json"
            ),
            source_id="c2db",
            source_record_id=record_id,
            source_record_raw_sha256=source_artifact.sha256,
            artifact_format="PYMATGEN_JSON",
            raw_bytes=pymatgen_bytes,
        )
        normalized = normalize_raw_structure_artifact_v2(
            raw_artifact=raw,
            dimensionality=StructureDimensionalityV2.TWO_D,
        )
        envelope = seal_private_artifact_envelope_v1(
            normalized,
            id_field="artifact_id",
            sha_field="artifact_sha256",
            sealed_at=sealed_at,
        )
        relative = f"records/{record_id}/normalized-structure.envelope.json"
        envelope_path = write_private_artifact_envelope_v1(
            output_root / relative,
            envelope,
        )
        (output_root / "records").chmod(0o700)
        envelope_path.parent.chmod(0o700)
        axis = normalized.provenance.aperiodic_axis_evidence.chosen_axis
        custody_records.append(
            FlatbandCalibrationStructureCustodyRecordV1(
                c2db_record_id=record_id,
                source_intake_manifest_sha256=manifest.manifest_sha256,
                source_structure_response_sha256=source_artifact.sha256,
                normalized_structure_artifact_id=normalized.artifact_id,
                normalized_structure_artifact_sha256=normalized.artifact_sha256,
                normalized_structure_sha256=normalized.structure_sha256,
                envelope_id=envelope.envelope_id,
                envelope_sha256=envelope.envelope_sha256,
                envelope_relative_path=relative,
                envelope_file_sha256=hashlib.sha256(
                    envelope_path.read_bytes()
                ).hexdigest(),
                aperiodic_axis=axis,
                structure_grouping_axis_ready=axis is not None,
            )
        )
    values = {
        "selection_manifest_sha256": selected.manifest_sha256,
        "records": tuple(sorted(custody_records, key=lambda item: item.c2db_record_id)),
    }
    draft = FlatbandCalibrationStructureCustodyManifestV1.model_construct(**values)
    digest = canonical_sha256(
        draft.model_dump(
            mode="python", exclude={"manifest_id", "manifest_sha256"}
        )
    )
    custody = FlatbandCalibrationStructureCustodyManifestV1.model_validate(
        {
            **values,
            "manifest_sha256": digest,
            "manifest_id": deterministic_id(
                "flatband-cal-structure-custody",
                {"manifest_sha256": digest},
            ),
        }
    )
    custody_envelope = seal_private_artifact_envelope_v1(
        custody,
        id_field="manifest_id",
        sha_field="manifest_sha256",
        sealed_at=sealed_at,
    )
    write_private_artifact_envelope_v1(
        output_root / "custody-manifest.envelope.json",
        custody_envelope,
    )
    return custody, custody_envelope


def _structure_from_c2db_json(payload: bytes) -> Structure:
    parsed = json.loads(payload)
    if not isinstance(parsed, dict) or not isinstance(parsed.get("1"), dict):
        raise TypeError("C2DB structure JSON is missing atoms record '1'")
    atoms = parsed["1"]
    numbers = atoms.get("numbers")
    positions = atoms.get("positions")
    cell = atoms.get("cell")
    if not isinstance(numbers, list) or not isinstance(positions, list):
        raise TypeError("C2DB atoms record is missing numbers/positions")
    if not isinstance(cell, list) or len(numbers) != len(positions):
        raise ValueError("C2DB atoms record has invalid cell or site count")
    try:
        labels = [str(Element.from_Z(int(value))) for value in numbers]
        return Structure(
            Lattice(cell),
            labels,
            positions,
            coords_are_cartesian=True,
            to_unit_cell=False,
        )
    except (TypeError, ValueError) as exc:
        raise ValueError("C2DB atoms record could not be parsed") from exc


__all__ = [
    "FlatbandCalibrationStructureCustodyManifestV1",
    "FlatbandCalibrationStructureCustodyRecordV1",
    "seal_selected_calibration_structures",
]
