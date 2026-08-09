"""Replayable local structure grouping for the flat-band benchmark.

Formal work uses three separate content-addressed stages:

``seal_structure_grouping_input_manifest_v2``
    Seals stable pre-group candidate keys, declared dimensionality, periodic-axis
    identity, and canonical structure payloads.  It contains no final case ID,
    case SHA, leakage group, or caller-supplied group key.
``run_structure_grouping_computation_v2``
    Computes prototype and anonymous-fingerprint evidence from that manifest.
``finalize_structure_grouping_release_v2``
    Runs only after computation completion and projects computed groups to final
    cases and the legacy-compatible V2 assignment records.

The removed one-step V1 draft was circular (final case SHA included its own
grouping output) and is deliberately not importable as a formal API.  Until all
models, local algorithms, replay verifiers, and adversarial tests below are
implemented, callers must treat this module as ``PILOT_NO_GO``.
"""

from __future__ import annotations

import base64
import binascii
from collections import defaultdict, deque
from datetime import datetime, timezone
from enum import StrEnum
import hashlib
from importlib import metadata
import json
import math
from pathlib import Path
import platform
import time
from typing import Annotated, Iterable, Literal, Sequence

import numpy as np
from pydantic import Field, field_validator, model_validator
from pymatgen.analysis.structure_matcher import StructureMatcher
from pymatgen.core import Composition, Element, Lattice, Structure
from pymatgen.symmetry.analyzer import SpacegroupAnalyzer
import spglib

from material_agent.inspiration.models import (
    Identifier,
    Sha256,
    ShortText,
    StrictModel,
    canonical_sha256,
    deterministic_id,
)
from material_agent.research.flatband_contracts import (
    Dimensionality,
    FlatBandEvidenceV1,
    FlatBandBenchmarkCaseV1,
    MechanismFamily,
    SOURCE_CATALOG_V1_SHA256,
    SourceRecordRefV1,
    TargetBandClass,
)
from material_agent.research.flatband_leakage import (
    LeakageAxis,
    STRUCTURE_LEAKAGE_AXES,
    StructureGroupingAlgorithmV2,
    StructureGroupingAssignmentV2,
    StructureGroupingRunV2,
    structure_grouping_case_universe_sha256_v2,
)


NORMALIZATION_DECIMAL_PLACES = 12
MAXIMUM_RAW_STRUCTURE_BYTES = 2_000_000
MAXIMUM_MANIFEST_RAW_STRUCTURE_BYTES = 64_000_000
PROVENANCE_SCOPE = "INTERNAL_REPLAY_NOT_EXTERNAL_ATTESTATION"
FiniteFloat = Annotated[float, Field(allow_inf_nan=False)]
Vector3 = tuple[FiniteFloat, FiniteFloat, FiniteFloat]
Matrix3 = tuple[Vector3, Vector3, Vector3]


class StructureGroupingReadiness(StrEnum):
    PILOT_NO_GO = "PILOT_NO_GO"


READINESS = StructureGroupingReadiness.PILOT_NO_GO


class StructureDimensionalityV2(StrEnum):
    TWO_D = "2D"
    THREE_D = "3D"


class StructureGroupingParametersV2(StrictModel):
    """Frozen scientific parameters; 2D and 3D tolerances are not conflated."""

    schema_version: Literal["flatband-structure-grouping-parameters-v2"] = (
        "flatband-structure-grouping-parameters-v2"
    )
    three_d_symprec_schedule_angstrom: tuple[
        Literal[0.01], Literal[0.05], Literal[0.1]
    ] = (0.01, 0.05, 0.1)
    two_d_layer_symprec_schedule_angstrom: tuple[
        Literal[0.05], Literal[0.1]
    ] = (0.05, 0.1)
    angle_tolerance_degrees: float = Field(
        default=5.0, ge=0.0, le=180.0, allow_inf_nan=False
    )
    matcher_lattice_length_tolerance: float = Field(
        default=0.2, gt=0.0, allow_inf_nan=False
    )
    matcher_site_tolerance: float = Field(
        default=0.3, gt=0.0, allow_inf_nan=False
    )
    matcher_angle_tolerance_degrees: float = Field(
        default=5.0, ge=0.0, le=180.0, allow_inf_nan=False
    )
    normalization_decimal_places: Literal[12] = 12
    two_d_vacuum_padding_angstrom: float = Field(
        default=15.0, gt=0.0, allow_inf_nan=False
    )
    two_d_minimum_source_vacuum_gap_angstrom: Literal[8.0] = 8.0
    prototype_threshold_union_for_leakage: Literal[True] = True
    matcher_primitive_cell: Literal[True] = True
    matcher_scale: Literal[True] = True
    matcher_attempt_supercell: Literal[False] = False
    matcher_allow_subset: Literal[False] = False
    matcher_all_pairs_connected_components: Literal[True] = True


def _require_rfc3339(value: str) -> str:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("timestamp must include a UTC offset")
    return value


def _now_rfc3339() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def _assert_addressed(
    value: StrictModel, *, id_field: str, sha_field: str, prefix: str
) -> None:
    semantic = value.model_dump(mode="python", exclude={id_field, sha_field})
    digest = canonical_sha256(semantic)
    if getattr(value, sha_field) != digest:
        raise ValueError(f"{sha_field} does not match semantic content")
    if getattr(value, id_field) != deterministic_id(prefix, {sha_field: digest}):
        raise ValueError(f"{id_field} does not match {sha_field}")


def _build_addressed(
    model_type: type[StrictModel],
    *,
    id_field: str,
    sha_field: str,
    prefix: str,
    values: dict[str, object],
) -> StrictModel:
    draft = model_type.model_construct(**values)
    digest = canonical_sha256(
        draft.model_dump(mode="python", exclude={id_field, sha_field})
    )
    return model_type.model_validate(
        {
            **values,
            sha_field: digest,
            id_field: deterministic_id(prefix, {sha_field: digest}),
        }
    )


def _quantize(value: float) -> float:
    if not math.isfinite(value):
        raise ValueError("structure contains a non-finite value")
    result = round(float(value), NORMALIZATION_DECIMAL_PLACES)
    return 0.0 if result == 0.0 else result


def _wrap_fractional(value: float) -> float:
    result = _quantize(float(value) % 1.0)
    return 0.0 if result == 1.0 else result


def _determinant(matrix: Matrix3) -> float:
    (a, b, c), (d, e, f), (g, h, i) = matrix
    return a * (e * i - f * h) - b * (d * i - f * g) + c * (d * h - e * g)


class RawStructureArtifactV2(StrictModel):
    """Bounded private source bytes used as the only parser preimage."""

    schema_version: Literal["flatband-raw-structure-artifact-v2"] = (
        "flatband-raw-structure-artifact-v2"
    )
    artifact_id: Identifier
    artifact_sha256: Sha256
    private_artifact_uri: Annotated[
        str,
        Field(
            min_length=20,
            max_length=512,
            pattern=r"^artifact://private/[A-Za-z0-9][A-Za-z0-9._:/-]*$",
        ),
    ]
    source_id: Identifier
    source_record_id: Identifier
    source_record_raw_sha256: Sha256
    artifact_format: Literal["CIF", "POSCAR", "PYMATGEN_JSON"]
    byte_encoding: Literal["UTF-8"] = "UTF-8"
    raw_bytes_base64: Annotated[
        str, Field(min_length=4, max_length=2_666_668)
    ]
    raw_byte_count: Annotated[int, Field(ge=1, le=MAXIMUM_RAW_STRUCTURE_BYTES)]
    raw_bytes_sha256: Sha256
    private_custody: Literal[True] = True
    public_release_allowed: Literal[False] = False
    provenance_scope: Literal["INTERNAL_REPLAY_NOT_EXTERNAL_ATTESTATION"] = (
        PROVENANCE_SCOPE
    )
    scientific_conclusion: Literal[False] = False

    @model_validator(mode="after")
    def validate_raw_artifact(self) -> "RawStructureArtifactV2":
        suffix = self.private_artifact_uri.removeprefix("artifact://private/")
        if "\\" in suffix or any(part in {"", ".", ".."} for part in suffix.split("/")):
            raise ValueError("private artifact URI is not a safe opaque path")
        try:
            raw = base64.b64decode(self.raw_bytes_base64, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise ValueError("raw structure bytes are not canonical base64") from exc
        if base64.b64encode(raw).decode("ascii") != self.raw_bytes_base64:
            raise ValueError("raw structure bytes use a non-canonical base64 form")
        if len(raw) != self.raw_byte_count:
            raise ValueError("raw structure byte count differs from embedded bytes")
        if hashlib.sha256(raw).hexdigest() != self.raw_bytes_sha256:
            raise ValueError("raw structure SHA-256 differs from embedded bytes")
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ValueError("raw structure artifact must be strict UTF-8") from exc
        if "\x00" in text:
            raise ValueError("raw structure artifact cannot contain NUL bytes")
        _assert_addressed(
            self,
            id_field="artifact_id",
            sha_field="artifact_sha256",
            prefix="raw-structure-v2",
        )
        return self

    def raw_bytes(self) -> bytes:
        """Return validated bytes; callers must keep them in private custody."""

        return base64.b64decode(self.raw_bytes_base64, validate=True)


def build_raw_structure_artifact_v2(
    *,
    private_artifact_uri: str,
    source_id: str,
    source_record_id: str,
    source_record_raw_sha256: str,
    artifact_format: Literal["CIF", "POSCAR", "PYMATGEN_JSON"],
    raw_bytes: bytes,
) -> RawStructureArtifactV2:
    """Seal exact private text bytes and their audited source selector."""

    if not isinstance(raw_bytes, bytes):
        raise TypeError("raw_bytes must be bytes")
    if not 1 <= len(raw_bytes) <= MAXIMUM_RAW_STRUCTURE_BYTES:
        raise ValueError("raw structure artifact exceeds the frozen byte bound")
    values: dict[str, object] = {
        "private_artifact_uri": private_artifact_uri,
        "source_id": source_id,
        "source_record_id": source_record_id,
        "source_record_raw_sha256": source_record_raw_sha256,
        "artifact_format": artifact_format,
        "raw_bytes_base64": base64.b64encode(raw_bytes).decode("ascii"),
        "raw_byte_count": len(raw_bytes),
        "raw_bytes_sha256": hashlib.sha256(raw_bytes).hexdigest(),
    }
    return RawStructureArtifactV2.model_validate(
        _build_addressed(
            RawStructureArtifactV2,
            id_field="artifact_id",
            sha_field="artifact_sha256",
            prefix="raw-structure-v2",
            values=values,
        ).model_dump(mode="python", round_trip=True)
    )


class NormalizedOrderedSiteV2(StrictModel):
    species: ShortText
    fractional_coordinates: Vector3

    @model_validator(mode="after")
    def validate_site(self) -> "NormalizedOrderedSiteV2":
        if any(value < 0.0 or value >= 1.0 for value in self.fractional_coordinates):
            raise ValueError("fractional coordinates must be in [0, 1)")
        if tuple(_quantize(value) for value in self.fractional_coordinates) != (
            self.fractional_coordinates
        ):
            raise ValueError("site coordinates differ from fixed normalization")
        return self


class NormalizedStructurePayloadV2(StrictModel):
    schema_version: Literal["flatband-normalized-structure-payload-v2"] = (
        "flatband-normalized-structure-payload-v2"
    )
    coordinate_system: Literal["fractional"] = "fractional"
    coordinate_decimal_places: Literal[12] = 12
    ordered_occupancy_only: Literal[True] = True
    lattice_matrix_angstrom: Matrix3
    sites: Annotated[
        tuple[NormalizedOrderedSiteV2, ...], Field(min_length=1, max_length=2_048)
    ]

    @model_validator(mode="after")
    def validate_payload(self) -> "NormalizedStructurePayloadV2":
        flattened = tuple(value for row in self.lattice_matrix_angstrom for value in row)
        if tuple(_quantize(value) for value in flattened) != flattened:
            raise ValueError("lattice differs from fixed normalization")
        if _determinant(self.lattice_matrix_angstrom) <= 1.0e-10:
            raise ValueError("lattice must have positive nonzero handed volume")
        keys = tuple((item.species, item.fractional_coordinates) for item in self.sites)
        if keys != tuple(sorted(set(keys))):
            raise ValueError("normalized sites must be sorted and unique")
        coordinates = tuple(item.fractional_coordinates for item in self.sites)
        if len(coordinates) != len(set(coordinates)):
            raise ValueError("different species cannot occupy the same periodic site")
        return self


class AxisVacuumGapEvidenceV2(StrictModel):
    axis: Literal[0, 1, 2]
    crystallographic_height_angstrom: FiniteFloat
    maximum_cyclic_gap_fraction: FiniteFloat
    maximum_cyclic_gap_angstrom: FiniteFloat
    occupied_thickness_angstrom: FiniteFloat
    passes_minimum_gap: bool

    @model_validator(mode="after")
    def validate_axis(self) -> "AxisVacuumGapEvidenceV2":
        if self.crystallographic_height_angstrom <= 0.0:
            raise ValueError("crystallographic height must be positive")
        if not 0.0 <= self.maximum_cyclic_gap_fraction <= 1.0:
            raise ValueError("cyclic gap fraction must lie in [0, 1]")
        expected_gap = (
            self.crystallographic_height_angstrom
            * self.maximum_cyclic_gap_fraction
        )
        if not math.isclose(
            self.maximum_cyclic_gap_angstrom,
            expected_gap,
            rel_tol=0.0,
            abs_tol=1.0e-8,
        ):
            raise ValueError("cyclic gap length differs from height times fraction")
        expected_thickness = (
            self.crystallographic_height_angstrom
            - self.maximum_cyclic_gap_angstrom
        )
        if not math.isclose(
            self.occupied_thickness_angstrom,
            expected_thickness,
            rel_tol=0.0,
            abs_tol=1.0e-8,
        ):
            raise ValueError("occupied thickness differs from height minus cyclic gap")
        if self.passes_minimum_gap != (self.maximum_cyclic_gap_angstrom >= 8.0):
            raise ValueError("vacuum-gap threshold decision differs from frozen 8 A rule")
        return self


class AperiodicAxisEvidenceV2(StrictModel):
    schema_version: Literal["flatband-aperiodic-axis-evidence-v2"] = (
        "flatband-aperiodic-axis-evidence-v2"
    )
    evidence_id: Identifier
    evidence_sha256: Sha256
    algorithm: Literal["crystallographic-height-max-cyclic-gap-v1"] = (
        "crystallographic-height-max-cyclic-gap-v1"
    )
    dimensionality: StructureDimensionalityV2
    minimum_gap_angstrom: Literal[8.0] = 8.0
    axes: tuple[AxisVacuumGapEvidenceV2, AxisVacuumGapEvidenceV2, AxisVacuumGapEvidenceV2]
    candidate_axes: tuple[Literal[0, 1, 2], ...]
    chosen_axis: Literal[0, 1, 2] | None
    provenance_scope: Literal["INTERNAL_REPLAY_NOT_EXTERNAL_ATTESTATION"] = (
        PROVENANCE_SCOPE
    )

    @model_validator(mode="after")
    def validate_evidence(self) -> "AperiodicAxisEvidenceV2":
        if tuple(item.axis for item in self.axes) != (0, 1, 2):
            raise ValueError("aperiodic-axis evidence must cover axes 0, 1, 2")
        expected = tuple(item.axis for item in self.axes if item.passes_minimum_gap)
        if self.candidate_axes != expected:
            raise ValueError("candidate axes differ from the three-axis computation")
        if self.dimensionality is StructureDimensionalityV2.TWO_D:
            if len(expected) != 1 or self.chosen_axis != expected[0]:
                raise ValueError("2D structure must have exactly one geometric vacuum axis")
        elif expected or self.chosen_axis is not None:
            raise ValueError("3D structure must have zero geometric vacuum axes")
        _assert_addressed(
            self,
            id_field="evidence_id",
            sha_field="evidence_sha256",
            prefix="aperiodic-axis-evidence",
        )
        return self


def canonical_structure_payload_sha256_v2(
    payload: NormalizedStructurePayloadV2,
) -> str:
    validated = NormalizedStructurePayloadV2.model_validate(
        payload.model_dump(mode="python", round_trip=True)
    )
    return canonical_sha256(validated.model_dump(mode="python", round_trip=True))


def _normalize_pymatgen_structure_v2(structure: Structure) -> NormalizedStructurePayloadV2:
    if len(structure) < 1 or len(structure) > 2_048:
        raise ValueError("structure site count lies outside the frozen bound")
    lattice = np.asarray(structure.lattice.matrix, dtype=float).copy()
    fractional = np.asarray(structure.frac_coords, dtype=float).copy()
    if not np.isfinite(lattice).all() or not np.isfinite(fractional).all():
        raise ValueError("structure contains non-finite lattice or coordinates")
    determinant = float(np.linalg.det(lattice))
    if abs(determinant) <= 1.0e-10:
        raise ValueError("structure lattice has zero volume")
    if determinant < 0.0:
        lattice[0] *= -1.0
        fractional[:, 0] *= -1.0

    normalized_sites: list[NormalizedOrderedSiteV2] = []
    for index, site in enumerate(structure):
        if not site.is_ordered or len(site.species) != 1:
            raise ValueError("structure grouping accepts ordered occupancy only")
        specie, occupancy = next(iter(site.species.items()))
        if not math.isclose(float(occupancy), 1.0, rel_tol=0.0, abs_tol=1.0e-12):
            raise ValueError("ordered site occupancy must equal one")
        try:
            symbol = Element(specie.symbol).symbol
        except (AttributeError, ValueError) as exc:
            raise ValueError("structure contains a non-element species") from exc
        coordinates = tuple(_wrap_fractional(value) for value in fractional[index])
        normalized_sites.append(
            NormalizedOrderedSiteV2(
                species=symbol,
                fractional_coordinates=coordinates,
            )
        )
    ordered_sites = tuple(
        sorted(
            normalized_sites,
            key=lambda item: (item.species, item.fractional_coordinates),
        )
    )
    return NormalizedStructurePayloadV2(
        lattice_matrix_angstrom=tuple(
            tuple(_quantize(value) for value in row) for row in lattice
        ),
        sites=ordered_sites,
    )


def _parse_raw_structure_payload_v2(
    raw_artifact: RawStructureArtifactV2,
) -> NormalizedStructurePayloadV2:
    validated = RawStructureArtifactV2.model_validate(
        raw_artifact.model_dump(mode="python", round_trip=True)
    )
    text = validated.raw_bytes().decode("utf-8")
    try:
        if validated.artifact_format == "CIF":
            structure = Structure.from_str(text, fmt="cif")
        elif validated.artifact_format == "POSCAR":
            structure = Structure.from_str(text, fmt="poscar")
        else:
            decoded = json.loads(text)
            if not isinstance(decoded, dict):
                raise ValueError("pymatgen JSON structure must be an object")
            structure = Structure.from_dict(decoded)
    except Exception as exc:
        raise ValueError("raw structure artifact cannot be deterministically parsed") from exc
    return _normalize_pymatgen_structure_v2(structure)


def _build_aperiodic_axis_evidence_v2(
    payload: NormalizedStructurePayloadV2,
    dimensionality: StructureDimensionalityV2,
) -> AperiodicAxisEvidenceV2:
    validated = NormalizedStructurePayloadV2.model_validate(
        payload.model_dump(mode="python", round_trip=True)
    )
    lattice = np.asarray(validated.lattice_matrix_angstrom, dtype=float)
    volume = abs(float(np.linalg.det(lattice)))
    axis_rows: list[AxisVacuumGapEvidenceV2] = []
    for axis in range(3):
        periodic = tuple(index for index in range(3) if index != axis)
        cross_norm = float(np.linalg.norm(np.cross(lattice[periodic[0]], lattice[periodic[1]])))
        if cross_norm <= 1.0e-12:
            raise ValueError("lattice has a degenerate crystallographic plane")
        height = volume / cross_norm
        positions = sorted(
            float(site.fractional_coordinates[axis]) for site in validated.sites
        )
        gaps = [right - left for left, right in zip(positions, positions[1:])]
        gaps.append(positions[0] + 1.0 - positions[-1])
        gap_fraction = max(gaps)
        gap_angstrom = height * gap_fraction
        thickness = height - gap_angstrom
        axis_rows.append(
            AxisVacuumGapEvidenceV2(
                axis=axis,
                crystallographic_height_angstrom=_quantize(height),
                maximum_cyclic_gap_fraction=_quantize(gap_fraction),
                maximum_cyclic_gap_angstrom=_quantize(gap_angstrom),
                occupied_thickness_angstrom=_quantize(max(0.0, thickness)),
                passes_minimum_gap=gap_angstrom >= 8.0,
            )
        )
    candidates = tuple(item.axis for item in axis_rows if item.passes_minimum_gap)
    chosen = candidates[0] if dimensionality is StructureDimensionalityV2.TWO_D and len(candidates) == 1 else None
    values: dict[str, object] = {
        "dimensionality": dimensionality,
        "axes": tuple(axis_rows),
        "candidate_axes": candidates,
        "chosen_axis": chosen,
    }
    return AperiodicAxisEvidenceV2.model_validate(
        _build_addressed(
            AperiodicAxisEvidenceV2,
            id_field="evidence_id",
            sha_field="evidence_sha256",
            prefix="aperiodic-axis-evidence",
            values=values,
        ).model_dump(mode="python", round_trip=True)
    )


def build_aperiodic_axis_evidence_v2(
    *,
    payload: NormalizedStructurePayloadV2,
    dimensionality: StructureDimensionalityV2,
) -> AperiodicAxisEvidenceV2:
    return _build_aperiodic_axis_evidence_v2(payload, dimensionality)


class StructureSourceArtifactProvenanceV2(StrictModel):
    raw_artifact_id: Identifier
    raw_artifact_sha256: Sha256
    private_artifact_uri: Annotated[str, Field(min_length=20, max_length=512)]
    source_id: Identifier
    source_record_id: Identifier
    source_record_raw_sha256: Sha256
    source_artifact_sha256: Sha256
    source_artifact_byte_count: Annotated[
        int, Field(ge=1, le=MAXIMUM_RAW_STRUCTURE_BYTES)
    ]
    source_artifact_format: Annotated[str, Field(min_length=1, max_length=32)]
    parser_name: ShortText
    parser_version: ShortText
    declared_dimensionality: StructureDimensionalityV2
    aperiodic_axis_evidence: AperiodicAxisEvidenceV2
    provenance_scope: Literal["INTERNAL_REPLAY_NOT_EXTERNAL_ATTESTATION"] = (
        PROVENANCE_SCOPE
    )


class NormalizedStructureArtifactV2(StrictModel):
    schema_version: Literal["flatband-normalized-structure-artifact-v2"] = (
        "flatband-normalized-structure-artifact-v2"
    )
    artifact_id: Identifier
    artifact_sha256: Sha256
    structure_sha256: Sha256
    raw_artifact: RawStructureArtifactV2
    provenance: StructureSourceArtifactProvenanceV2
    payload: NormalizedStructurePayloadV2
    private_custody: Literal[True] = True
    scientific_conclusion: Literal[False] = False

    @model_validator(mode="after")
    def validate_artifact(self) -> "NormalizedStructureArtifactV2":
        replayed_payload = _parse_raw_structure_payload_v2(self.raw_artifact)
        if replayed_payload != self.payload:
            raise ValueError("normalized payload does not replay from exact raw bytes")
        replayed_axis = _build_aperiodic_axis_evidence_v2(
            replayed_payload, self.provenance.declared_dimensionality
        )
        expected_provenance = StructureSourceArtifactProvenanceV2(
            raw_artifact_id=self.raw_artifact.artifact_id,
            raw_artifact_sha256=self.raw_artifact.artifact_sha256,
            private_artifact_uri=self.raw_artifact.private_artifact_uri,
            source_id=self.raw_artifact.source_id,
            source_record_id=self.raw_artifact.source_record_id,
            source_record_raw_sha256=self.raw_artifact.source_record_raw_sha256,
            source_artifact_sha256=self.raw_artifact.raw_bytes_sha256,
            source_artifact_byte_count=self.raw_artifact.raw_byte_count,
            source_artifact_format=self.raw_artifact.artifact_format,
            parser_name="pymatgen.Structure.from_str/from_dict",
            parser_version=metadata.version("pymatgen"),
            declared_dimensionality=self.provenance.declared_dimensionality,
            aperiodic_axis_evidence=replayed_axis,
        )
        if self.provenance != expected_provenance:
            raise ValueError("structure provenance does not replay from raw custody")
        if self.structure_sha256 != canonical_structure_payload_sha256_v2(self.payload):
            raise ValueError("structure SHA does not address normalized payload")
        _assert_addressed(
            self,
            id_field="artifact_id",
            sha_field="artifact_sha256",
            prefix="normalized-structure-v2",
        )
        return self


def normalize_raw_structure_artifact_v2(
    *,
    raw_artifact: RawStructureArtifactV2,
    dimensionality: StructureDimensionalityV2,
) -> NormalizedStructureArtifactV2:
    """Parse, normalize, and replay one exact raw private structure artifact."""

    raw = RawStructureArtifactV2.model_validate(
        raw_artifact.model_dump(mode="python", round_trip=True)
    )
    payload = _parse_raw_structure_payload_v2(raw)
    axis = _build_aperiodic_axis_evidence_v2(payload, dimensionality)
    provenance = StructureSourceArtifactProvenanceV2(
        raw_artifact_id=raw.artifact_id,
        raw_artifact_sha256=raw.artifact_sha256,
        private_artifact_uri=raw.private_artifact_uri,
        source_id=raw.source_id,
        source_record_id=raw.source_record_id,
        source_record_raw_sha256=raw.source_record_raw_sha256,
        source_artifact_sha256=raw.raw_bytes_sha256,
        source_artifact_byte_count=raw.raw_byte_count,
        source_artifact_format=raw.artifact_format,
        parser_name="pymatgen.Structure.from_str/from_dict",
        parser_version=metadata.version("pymatgen"),
        declared_dimensionality=dimensionality,
        aperiodic_axis_evidence=axis,
    )
    values: dict[str, object] = {
        "structure_sha256": canonical_structure_payload_sha256_v2(payload),
        "raw_artifact": raw,
        "provenance": provenance,
        "payload": payload,
    }
    return NormalizedStructureArtifactV2.model_validate(
        _build_addressed(
            NormalizedStructureArtifactV2,
            id_field="artifact_id",
            sha_field="artifact_sha256",
            prefix="normalized-structure-v2",
            values=values,
        ).model_dump(mode="python", round_trip=True)
    )


def _composition_key_v2(formula: str) -> tuple[tuple[str, float], ...]:
    try:
        composition = Composition(formula)
    except Exception as exc:
        raise ValueError("case formula cannot be parsed as an elemental composition") from exc
    amounts: dict[str, float] = {}
    for species, amount in composition.items():
        try:
            symbol = Element(species.symbol).symbol
        except (AttributeError, ValueError) as exc:
            raise ValueError("case formula contains a non-element species") from exc
        amounts[symbol] = amounts.get(symbol, 0.0) + float(amount)
    total = sum(amounts.values())
    if total <= 0.0:
        raise ValueError("case formula has no positive elemental composition")
    return tuple(
        sorted((symbol, _quantize(amount / total)) for symbol, amount in amounts.items())
    )


def _payload_composition_key_v2(
    payload: NormalizedStructurePayloadV2,
) -> tuple[tuple[str, float], ...]:
    counts: dict[str, int] = defaultdict(int)
    for site in payload.sites:
        counts[site.species] += 1
    total = len(payload.sites)
    return tuple(
        sorted((symbol, _quantize(count / total)) for symbol, count in counts.items())
    )


class PreGroupCandidatePreimageV2(StrictModel):
    """Complete candidate preimage excluding all derived/final group fields."""

    study_phase: Identifier
    slot_index: Annotated[int, Field(ge=0, le=10_000)]
    priority: Annotated[int, Field(ge=0, le=10_000)]
    source_catalog_sha256: Literal[SOURCE_CATALOG_V1_SHA256] = (
        SOURCE_CATALOG_V1_SHA256
    )
    parent_label: ShortText
    formula: Annotated[str, Field(min_length=1, max_length=128)]
    structure_artifact_id: Identifier
    structure_artifact_sha256: Sha256
    structure_sha256: Sha256
    source_records: Annotated[
        tuple[SourceRecordRefV1, ...], Field(min_length=1, max_length=16)
    ]
    target_class: TargetBandClass
    target_fermi_distance_max_e_v: Literal[1.0] = 1.0
    dimensionality: StructureDimensionalityV2
    aperiodic_axis: Literal[0, 1, 2] | None
    aperiodic_axis_evidence: AperiodicAxisEvidenceV2
    frozen_request: Annotated[str, Field(min_length=1, max_length=4_000)]
    frozen_requirement_sha256: Sha256
    hard_constraints: Annotated[
        tuple[ShortText, ...], Field(min_length=1, max_length=64)
    ]
    soft_preferences: Annotated[tuple[ShortText, ...], Field(max_length=64)] = ()
    forbidden_transformations: Annotated[
        tuple[Identifier, ...], Field(min_length=1, max_length=64)
    ]
    seed_evidence: Annotated[
        tuple[FlatBandEvidenceV1, ...], Field(max_length=32)
    ] = ()
    primary_mechanism_stratum: MechanismFamily
    public_release_allowed: bool

    @model_validator(mode="after")
    def validate_preimage(self) -> "PreGroupCandidatePreimageV2":
        if self.dimensionality is StructureDimensionalityV2.TWO_D:
            if self.aperiodic_axis is None:
                raise ValueError("2D input requires replayable aperiodic-axis evidence")
            if self.aperiodic_axis != self.aperiodic_axis_evidence.chosen_axis:
                raise ValueError("declared aperiodic axis differs from embedded evidence")
        elif self.aperiodic_axis is not None:
            raise ValueError("3D input cannot declare an aperiodic axis")
        if self.aperiodic_axis_evidence.dimensionality is not self.dimensionality:
            raise ValueError("axis evidence dimensionality differs from case preimage")
        if any(item.raw_sha256 is None for item in self.source_records):
            raise ValueError("every pre-group source record requires a raw SHA")
        for values, label in (
            (self.hard_constraints, "hard constraints"),
            (self.soft_preferences, "soft preferences"),
            (self.forbidden_transformations, "forbidden transformations"),
        ):
            if values != tuple(sorted(set(values))):
                raise ValueError(f"{label} must be sorted and unique")
        keys = tuple((item.source_id, item.source_record_id) for item in self.source_records)
        if keys != tuple(sorted(set(keys))):
            raise ValueError("source records must be sorted and unique")
        evidence_ids = tuple(item.evidence_id for item in self.seed_evidence)
        if evidence_ids != tuple(sorted(set(evidence_ids))):
            raise ValueError("seed evidence must be evidence-ID sorted and unique")
        return self


class StructureGroupingCaseInputV2(StrictModel):
    """Pre-group identity; intentionally excludes every final-case/group field."""

    schema_version: Literal["flatband-structure-grouping-case-input-v2"] = (
        "flatband-structure-grouping-case-input-v2"
    )
    input_id: Identifier
    input_sha256: Sha256
    candidate_key: Identifier
    pre_group_slot_key: Identifier
    preimage: PreGroupCandidatePreimageV2

    @model_validator(mode="after")
    def validate_input(self) -> "StructureGroupingCaseInputV2":
        preimage_sha256 = canonical_sha256(self.preimage.model_dump(mode="python"))
        expected_candidate = deterministic_id(
            "pre-group-candidate",
            {
                "pre_group_slot_key": self.pre_group_slot_key,
                "priority": self.preimage.priority,
                "preimage_sha256": preimage_sha256,
            },
        )
        if self.candidate_key != expected_candidate:
            raise ValueError("candidate key does not address complete pre-group preimage")
        expected_slot = deterministic_id(
            "pre-group-slot",
            {
                "study_phase": self.preimage.study_phase,
                "slot_index": self.preimage.slot_index,
            },
        )
        if self.pre_group_slot_key != expected_slot:
            raise ValueError("pre-group slot key does not address candidate preimage")
        _assert_addressed(
            self,
            id_field="input_id",
            sha_field="input_sha256",
            prefix="structure-case-input",
        )
        return self


class StructureGroupingRuntimeIdentityV2(StrictModel):
    schema_version: Literal["flatband-structure-grouping-runtime-v2"] = (
        "flatband-structure-grouping-runtime-v2"
    )
    runtime_id: Identifier
    runtime_sha256: Sha256
    python_implementation: ShortText
    python_version: ShortText
    pymatgen_version: ShortText
    spglib_version: ShortText
    numpy_version: ShortText
    scipy_version: ShortText
    layer_group_api: Literal["spglib.get_symmetry_layerdataset"] = (
        "spglib.get_symmetry_layerdataset"
    )
    module_implementation_sha256: Sha256
    parameters_sha256: Sha256

    @model_validator(mode="after")
    def validate_runtime(self) -> "StructureGroupingRuntimeIdentityV2":
        _assert_addressed(
            self,
            id_field="runtime_id",
            sha_field="runtime_sha256",
            prefix="structure-runtime-v2",
        )
        return self


def _build_runtime_identity_v2(
    parameters: StructureGroupingParametersV2,
) -> StructureGroupingRuntimeIdentityV2:
    module_sha256 = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    values: dict[str, object] = {
        "python_implementation": platform.python_implementation(),
        "python_version": platform.python_version(),
        "pymatgen_version": metadata.version("pymatgen"),
        "spglib_version": metadata.version("spglib"),
        "numpy_version": metadata.version("numpy"),
        "scipy_version": metadata.version("scipy"),
        "module_implementation_sha256": module_sha256,
        "parameters_sha256": canonical_sha256(
            parameters.model_dump(mode="python", round_trip=True)
        ),
    }
    return StructureGroupingRuntimeIdentityV2.model_validate(
        _build_addressed(
            StructureGroupingRuntimeIdentityV2,
            id_field="runtime_id",
            sha_field="runtime_sha256",
            prefix="structure-runtime-v2",
            values=values,
        ).model_dump(mode="python", round_trip=True)
    )


class StructureGroupingInputRootV2(StrictModel):
    schema_version: Literal["flatband-structure-grouping-input-root-v2"] = (
        "flatband-structure-grouping-input-root-v2"
    )
    ordered_case_inputs_sha256: Sha256
    ordered_raw_structure_artifacts_sha256: Sha256
    ordered_structure_artifacts_sha256: Sha256
    ordered_payloads_sha256: Sha256
    runtime_sha256: Sha256
    input_root_sha256: Sha256

    @model_validator(mode="after")
    def validate_root(self) -> "StructureGroupingInputRootV2":
        if self.input_root_sha256 != canonical_sha256(
            self.model_dump(mode="python", exclude={"input_root_sha256"})
        ):
            raise ValueError("input root does not bind exact pre-group inputs")
        return self


def _build_input_root_v2(
    case_inputs: Sequence[StructureGroupingCaseInputV2],
    structure_artifacts: Sequence[NormalizedStructureArtifactV2],
    runtime_identity: StructureGroupingRuntimeIdentityV2,
) -> StructureGroupingInputRootV2:
    raw_artifacts = tuple(item.raw_artifact for item in structure_artifacts)
    values: dict[str, object] = {
        "ordered_case_inputs_sha256": canonical_sha256(tuple(case_inputs)),
        "ordered_raw_structure_artifacts_sha256": canonical_sha256(raw_artifacts),
        "ordered_structure_artifacts_sha256": canonical_sha256(
            tuple(structure_artifacts)
        ),
        "ordered_payloads_sha256": canonical_sha256(
            tuple(item.payload for item in structure_artifacts)
        ),
        "runtime_sha256": runtime_identity.runtime_sha256,
    }
    draft = StructureGroupingInputRootV2.model_construct(**values)
    digest = canonical_sha256(
        draft.model_dump(mode="python", exclude={"input_root_sha256"})
    )
    return StructureGroupingInputRootV2(**values, input_root_sha256=digest)


class StructureGroupingInputManifestV2(StrictModel):
    schema_version: Literal["flatband-structure-grouping-input-manifest-v2"] = (
        "flatband-structure-grouping-input-manifest-v2"
    )
    manifest_id: Identifier
    manifest_sha256: Sha256
    sealed_at: Annotated[str, Field(min_length=20, max_length=40)]
    sealed_monotonic_ns: Annotated[int, Field(ge=0)]
    parameters: StructureGroupingParametersV2
    runtime_identity: StructureGroupingRuntimeIdentityV2
    case_inputs: Annotated[
        tuple[StructureGroupingCaseInputV2, ...], Field(min_length=1, max_length=2_048)
    ]
    structure_artifacts: Annotated[
        tuple[NormalizedStructureArtifactV2, ...], Field(min_length=1, max_length=2_048)
    ]
    input_root: StructureGroupingInputRootV2
    contains_final_case_identity: Literal[False] = False
    contains_grouping_output: Literal[False] = False
    provenance_scope: Literal["INTERNAL_REPLAY_NOT_EXTERNAL_ATTESTATION"] = (
        PROVENANCE_SCOPE
    )
    scientific_conclusion: Literal[False] = False

    @field_validator("sealed_at")
    @classmethod
    def validate_time(cls, value: str) -> str:
        return _require_rfc3339(value)

    @model_validator(mode="after")
    def validate_manifest(self) -> "StructureGroupingInputManifestV2":
        keys = tuple(item.candidate_key for item in self.case_inputs)
        if keys != tuple(sorted(set(keys))):
            raise ValueError("case inputs must be candidate-key sorted and unique")
        artifact_keys = tuple(item.artifact_id for item in self.structure_artifacts)
        if artifact_keys != tuple(sorted(set(artifact_keys))):
            raise ValueError("structure artifacts must be artifact-ID sorted and unique")
        artifacts = {item.artifact_id: item for item in self.structure_artifacts}
        uri_content: dict[str, tuple[str, int]] = {}
        for item in self.case_inputs:
            artifact = artifacts.get(item.preimage.structure_artifact_id)
            if artifact is None or (
                artifact.artifact_sha256,
                artifact.structure_sha256,
            ) != (
                item.preimage.structure_artifact_sha256,
                item.preimage.structure_sha256,
            ):
                raise ValueError("case input does not bind its exact structure artifact")
            source = next(
                (
                    record
                    for record in item.preimage.source_records
                    if (
                        record.source_id,
                        record.source_record_id,
                        record.raw_sha256,
                    )
                    == (
                        artifact.provenance.source_id,
                        artifact.provenance.source_record_id,
                        artifact.provenance.source_record_raw_sha256,
                    )
                ),
                None,
            )
            if source is None:
                raise ValueError("structure provenance is not bound to a source record")
            if artifact.provenance.aperiodic_axis_evidence != (
                item.preimage.aperiodic_axis_evidence
            ):
                raise ValueError("aperiodic-axis evidence differs across input artifacts")
            if artifact.provenance.declared_dimensionality is not item.preimage.dimensionality:
                raise ValueError("artifact dimensionality differs from candidate preimage")
            replayed_axis = _build_aperiodic_axis_evidence_v2(
                artifact.payload, item.preimage.dimensionality
            )
            if replayed_axis != item.preimage.aperiodic_axis_evidence:
                raise ValueError("aperiodic-axis evidence does not replay from payload")
            if _composition_key_v2(item.preimage.formula) != _payload_composition_key_v2(
                artifact.payload
            ):
                raise ValueError("case formula differs from normalized structure composition")
            raw = artifact.raw_artifact
            previous = uri_content.setdefault(
                raw.private_artifact_uri,
                (raw.raw_bytes_sha256, raw.raw_byte_count),
            )
            if previous != (raw.raw_bytes_sha256, raw.raw_byte_count):
                raise ValueError("one private artifact URI resolves to conflicting bytes")
        if set(artifacts) != {
            item.preimage.structure_artifact_id for item in self.case_inputs
        }:
            raise ValueError("manifest contains an unreferenced structure artifact")
        if sum(item.raw_artifact.raw_byte_count for item in self.structure_artifacts) > (
            MAXIMUM_MANIFEST_RAW_STRUCTURE_BYTES
        ):
            raise ValueError("manifest raw structure bytes exceed the aggregate bound")
        if self.input_root != _build_input_root_v2(
            self.case_inputs, self.structure_artifacts, self.runtime_identity
        ):
            raise ValueError("manifest input root differs from exact inputs")
        _assert_addressed(
            self,
            id_field="manifest_id",
            sha_field="manifest_sha256",
            prefix="structure-input-manifest",
        )
        return self


class WyckoffOrbitRoleV2(StrictModel):
    wyckoff_letter: Annotated[str, Field(min_length=1, max_length=8)]
    multiplicity: Annotated[int, Field(ge=1, le=2_048)]


class AnonymousWyckoffRoleV2(StrictModel):
    site_count: Annotated[int, Field(ge=1, le=2_048)]
    orbits: Annotated[tuple[WyckoffOrbitRoleV2, ...], Field(min_length=1)]

    @model_validator(mode="after")
    def validate_role(self) -> "AnonymousWyckoffRoleV2":
        keys = tuple((item.wyckoff_letter, item.multiplicity) for item in self.orbits)
        if keys != tuple(sorted(keys)):
            raise ValueError("Wyckoff orbits must be sorted")
        if sum(item.multiplicity for item in self.orbits) != self.site_count:
            raise ValueError("Wyckoff multiplicities do not cover role sites")
        return self


class SymmetrySignatureV2(StrictModel):
    symmetry_kind: Literal["SPACE_GROUP_3D", "LAYER_GROUP_2D"]
    group_number: Annotated[int, Field(ge=1, le=230)]
    hall_number: Annotated[int, Field(ge=-512, le=512)]
    international_symbol: ShortText
    anonymous_roles: Annotated[
        tuple[AnonymousWyckoffRoleV2, ...], Field(min_length=1, max_length=256)
    ]

    @model_validator(mode="after")
    def validate_signature(self) -> "SymmetrySignatureV2":
        if self.symmetry_kind == "LAYER_GROUP_2D" and self.group_number > 80:
            raise ValueError("layer-group number must be in 1..80")
        keys = tuple(
            (
                role.site_count,
                tuple(
                    (orbit.wyckoff_letter, orbit.multiplicity)
                    for orbit in role.orbits
                ),
            )
            for role in self.anonymous_roles
        )
        if keys != tuple(sorted(keys)):
            raise ValueError("anonymous roles must be sorted")
        return self


class PrototypeThresholdEvidenceV2(StrictModel):
    symprec_angstrom: FiniteFloat
    standardized_payload: NormalizedStructurePayloadV2
    signature: SymmetrySignatureV2
    signature_sha256: Sha256

    @model_validator(mode="after")
    def validate_threshold(self) -> "PrototypeThresholdEvidenceV2":
        if self.signature_sha256 != canonical_sha256(
            self.signature.model_dump(mode="python")
        ):
            raise ValueError("signature SHA does not address explicit symmetry signature")
        return self


class CanonicalizedStructureEvidenceV2(StrictModel):
    dimensionality: StructureDimensionalityV2
    original_aperiodic_axis: Literal[0, 1, 2] | None
    canonical_aperiodic_axis: Literal[2] | None
    source_cell_normal_height_angstrom: FiniteFloat | None
    layer_thickness_angstrom: FiniteFloat | None
    source_vacuum_gap_angstrom: FiniteFloat | None
    normalized_vacuum_padding_angstrom: FiniteFloat | None
    canonical_payload: NormalizedStructurePayloadV2
    layer_group_api_used: bool
    layer_group_fallback_used: Literal[False] = False


class PrototypeCaseEvidenceV2(StrictModel):
    schema_version: Literal["flatband-prototype-case-evidence-v2"] = (
        "flatband-prototype-case-evidence-v2"
    )
    evidence_id: Identifier
    evidence_sha256: Sha256
    candidate_key: Identifier
    input_id: Identifier
    input_sha256: Sha256
    structure_sha256: Sha256
    canonicalized_structure: CanonicalizedStructureEvidenceV2
    threshold_evidence: Annotated[
        tuple[PrototypeThresholdEvidenceV2, ...], Field(min_length=2, max_length=3)
    ]
    threshold_schedule_is_conservative_union: Literal[True] = True
    threshold_schedule_not_claimed_optimal: Literal[True] = True
    scientific_conclusion: Literal[False] = False

    @model_validator(mode="after")
    def validate_evidence(self) -> "PrototypeCaseEvidenceV2":
        thresholds = tuple(item.symprec_angstrom for item in self.threshold_evidence)
        if thresholds != tuple(sorted(set(thresholds))):
            raise ValueError("prototype threshold evidence must be sorted and unique")
        _assert_addressed(
            self,
            id_field="evidence_id",
            sha_field="evidence_sha256",
            prefix="prototype-case-evidence",
        )
        return self


class PrototypeComponentEvidenceV2(StrictModel):
    schema_version: Literal["flatband-prototype-component-v2"] = (
        "flatband-prototype-component-v2"
    )
    component_id: Identifier
    component_sha256: Sha256
    canonical_group_key: Identifier
    candidate_keys: Annotated[tuple[Identifier, ...], Field(min_length=1)]
    structure_sha256s: Annotated[tuple[Sha256, ...], Field(min_length=1)]
    scientific_conclusion: Literal[False] = False

    @model_validator(mode="after")
    def validate_component(self) -> "PrototypeComponentEvidenceV2":
        if self.candidate_keys != tuple(sorted(set(self.candidate_keys))):
            raise ValueError("prototype component candidate keys must be sorted")
        if len(self.candidate_keys) != len(self.structure_sha256s):
            raise ValueError("prototype component projections differ in length")
        expected = deterministic_id(
            "structure-proto-group",
            {"structure_sha256s": tuple(sorted(self.structure_sha256s))},
        )
        if self.canonical_group_key != expected:
            raise ValueError("prototype group key differs from explicit component")
        _assert_addressed(
            self,
            id_field="component_id",
            sha_field="component_sha256",
            prefix="prototype-component-v2",
        )
        return self


class FingerprintPairEvidenceV2(StrictModel):
    schema_version: Literal["flatband-fingerprint-pair-v2"] = (
        "flatband-fingerprint-pair-v2"
    )
    pair_id: Identifier
    pair_sha256: Sha256
    left_candidate_key: Identifier
    left_input_sha256: Sha256
    left_structure_sha256: Sha256
    right_candidate_key: Identifier
    right_input_sha256: Sha256
    right_structure_sha256: Sha256
    fit_anonymous: bool
    scientific_conclusion: Literal[False] = False

    @model_validator(mode="after")
    def validate_pair(self) -> "FingerprintPairEvidenceV2":
        if self.left_candidate_key >= self.right_candidate_key:
            raise ValueError("fingerprint pair keys must be strictly ordered")
        _assert_addressed(
            self,
            id_field="pair_id",
            sha_field="pair_sha256",
            prefix="fingerprint-pair-v2",
        )
        return self


class FingerprintComponentEvidenceV2(StrictModel):
    schema_version: Literal["flatband-fingerprint-component-v2"] = (
        "flatband-fingerprint-component-v2"
    )
    component_id: Identifier
    component_sha256: Sha256
    canonical_group_key: Identifier
    candidate_keys: Annotated[tuple[Identifier, ...], Field(min_length=1)]
    structure_sha256s: Annotated[tuple[Sha256, ...], Field(min_length=1)]
    scientific_conclusion: Literal[False] = False

    @model_validator(mode="after")
    def validate_component(self) -> "FingerprintComponentEvidenceV2":
        if self.candidate_keys != tuple(sorted(set(self.candidate_keys))):
            raise ValueError("fingerprint component candidate keys must be sorted")
        if len(self.candidate_keys) != len(self.structure_sha256s):
            raise ValueError("fingerprint component projections differ in length")
        expected = deterministic_id(
            "structure-fp-group",
            {"structure_sha256s": tuple(sorted(self.structure_sha256s))},
        )
        if self.canonical_group_key != expected:
            raise ValueError("fingerprint group key differs from explicit component")
        _assert_addressed(
            self,
            id_field="component_id",
            sha_field="component_sha256",
            prefix="fingerprint-component-v2",
        )
        return self


class StructureGroupingOutputRootV2(StrictModel):
    schema_version: Literal["flatband-structure-grouping-output-root-v2"] = (
        "flatband-structure-grouping-output-root-v2"
    )
    prototype_evidence_sha256: Sha256
    prototype_components_sha256: Sha256
    fingerprint_pair_matrix_sha256: Sha256
    fingerprint_components_sha256: Sha256
    output_root_sha256: Sha256

    @model_validator(mode="after")
    def validate_root(self) -> "StructureGroupingOutputRootV2":
        if self.output_root_sha256 != canonical_sha256(
            self.model_dump(mode="python", exclude={"output_root_sha256"})
        ):
            raise ValueError("output root does not match exact computation outputs")
        return self


class StructureGroupingFormalRunEvidenceV2(StrictModel):
    schema_version: Literal["flatband-structure-grouping-formal-run-v2"] = (
        "flatband-structure-grouping-formal-run-v2"
    )
    formal_run_id: Identifier
    formal_run_sha256: Sha256
    input_manifest_id: Identifier
    input_manifest_sha256: Sha256
    input_root_sha256: Sha256
    runtime_id: Identifier
    runtime_sha256: Sha256
    algorithm_ids: tuple[Identifier, Identifier]
    algorithm_sha256s: tuple[Sha256, Sha256]
    output_root_sha256: Sha256
    started_at: Annotated[str, Field(min_length=20, max_length=40)]
    completed_at: Annotated[str, Field(min_length=20, max_length=40)]
    started_monotonic_ns: Annotated[int, Field(ge=0)]
    completed_monotonic_ns: Annotated[int, Field(ge=0)]
    clock_source: Literal["local-python-utc-and-monotonic"] = (
        "local-python-utc-and-monotonic"
    )
    contains_final_case_identity: Literal[False] = False
    scientific_conclusion: Literal[False] = False

    @field_validator("started_at", "completed_at")
    @classmethod
    def validate_time(cls, value: str) -> str:
        return _require_rfc3339(value)

    @model_validator(mode="after")
    def validate_run(self) -> "StructureGroupingFormalRunEvidenceV2":
        if datetime.fromisoformat(self.completed_at) < datetime.fromisoformat(
            self.started_at
        ):
            raise ValueError("formal run completes before it starts")
        if self.completed_monotonic_ns < self.started_monotonic_ns:
            raise ValueError("formal run monotonic clock regressed")
        if self.algorithm_ids != tuple(sorted(set(self.algorithm_ids))):
            raise ValueError("formal run algorithms must be sorted and unique")
        _assert_addressed(
            self,
            id_field="formal_run_id",
            sha_field="formal_run_sha256",
            prefix="structure-formal-run-v2",
        )
        return self


class StructureGroupingComputationReleaseV2(StrictModel):
    schema_version: Literal["flatband-structure-grouping-computation-v2"] = (
        "flatband-structure-grouping-computation-v2"
    )
    computation_id: Identifier
    computation_sha256: Sha256
    input_manifest: StructureGroupingInputManifestV2
    grouping_algorithms: tuple[StructureGroupingAlgorithmV2, StructureGroupingAlgorithmV2]
    prototype_evidence: Annotated[
        tuple[PrototypeCaseEvidenceV2, ...], Field(min_length=1, max_length=2_048)
    ]
    prototype_components: Annotated[
        tuple[PrototypeComponentEvidenceV2, ...], Field(min_length=1, max_length=2_048)
    ]
    fingerprint_pair_evidence: tuple[FingerprintPairEvidenceV2, ...]
    fingerprint_components: Annotated[
        tuple[FingerprintComponentEvidenceV2, ...], Field(min_length=1, max_length=2_048)
    ]
    output_root: StructureGroupingOutputRootV2
    formal_run: StructureGroupingFormalRunEvidenceV2
    created_at: Annotated[str, Field(min_length=20, max_length=40)]
    status: Literal["SUCCEEDED"] = "SUCCEEDED"
    contains_final_case_identity: Literal[False] = False
    scientific_conclusion: Literal[False] = False

    @field_validator("created_at")
    @classmethod
    def validate_created_at(cls, value: str) -> str:
        return _require_rfc3339(value)

    @model_validator(mode="after")
    def validate_computation(self) -> "StructureGroupingComputationReleaseV2":
        keys = tuple(item.candidate_key for item in self.prototype_evidence)
        expected_keys = tuple(item.candidate_key for item in self.input_manifest.case_inputs)
        if keys != expected_keys:
            raise ValueError("prototype evidence does not exactly cover input candidates")
        pair_keys = tuple(
            (item.left_candidate_key, item.right_candidate_key)
            for item in self.fingerprint_pair_evidence
        )
        expected_pairs = tuple(
            (expected_keys[left], expected_keys[right])
            for left in range(len(expected_keys))
            for right in range(left + 1, len(expected_keys))
        )
        if pair_keys != expected_pairs:
            raise ValueError("fingerprint pair evidence is not a complete matrix")
        for components, label in (
            (self.prototype_components, "prototype"),
            (self.fingerprint_components, "fingerprint"),
        ):
            flattened = tuple(
                key for component in components for key in component.candidate_keys
            )
            if tuple(sorted(flattened)) != expected_keys or len(flattened) != len(
                set(flattened)
            ):
                raise ValueError(f"{label} components do not partition candidates")
        if self.output_root != _build_output_root_v2(
            self.prototype_evidence,
            self.prototype_components,
            self.fingerprint_pair_evidence,
            self.fingerprint_components,
        ):
            raise ValueError("computation output root differs from explicit outputs")
        if self.formal_run != _build_formal_run_v2(
            self.input_manifest,
            self.grouping_algorithms,
            self.output_root,
            started_at=self.formal_run.started_at,
            completed_at=self.formal_run.completed_at,
            started_monotonic_ns=self.formal_run.started_monotonic_ns,
            completed_monotonic_ns=self.formal_run.completed_monotonic_ns,
        ):
            raise ValueError("formal run does not bind exact input and output roots")
        if datetime.fromisoformat(self.created_at) < datetime.fromisoformat(
            self.formal_run.completed_at
        ):
            raise ValueError("computation release predates run completion")
        _assert_addressed(
            self,
            id_field="computation_id",
            sha_field="computation_sha256",
            prefix="structure-computation-v2",
        )
        return self


class FinalCaseProjectionV2(StrictModel):
    schema_version: Literal["flatband-structure-final-case-projection-v2"] = (
        "flatband-structure-final-case-projection-v2"
    )
    projection_id: Identifier
    projection_sha256: Sha256
    candidate_key: Identifier
    pre_group_slot_key: Identifier
    input_id: Identifier
    input_sha256: Sha256
    final_case_id: Identifier
    final_case_sha256: Sha256
    structure_sha256: Sha256

    @model_validator(mode="after")
    def validate_projection(self) -> "FinalCaseProjectionV2":
        _assert_addressed(
            self,
            id_field="projection_id",
            sha_field="projection_sha256",
            prefix="structure-case-projection",
        )
        return self


class StructureGroupingPrivateEvidenceReleaseV2(StrictModel):
    schema_version: Literal["flatband-structure-grouping-private-release-v2"] = (
        "flatband-structure-grouping-private-release-v2"
    )
    release_id: Identifier
    release_sha256: Sha256
    computation: StructureGroupingComputationReleaseV2
    final_cases_declared_at: Annotated[str, Field(min_length=20, max_length=40)]
    created_at: Annotated[str, Field(min_length=20, max_length=40)]
    final_cases: Annotated[
        tuple[FlatBandBenchmarkCaseV1, ...], Field(min_length=1, max_length=2_048)
    ]
    final_case_projections: Annotated[
        tuple[FinalCaseProjectionV2, ...], Field(min_length=1, max_length=2_048)
    ]
    grouping_algorithms: tuple[StructureGroupingAlgorithmV2, StructureGroupingAlgorithmV2]
    grouping_runs: tuple[StructureGroupingRunV2, StructureGroupingRunV2]
    grouping_assignments: Annotated[
        tuple[StructureGroupingAssignmentV2, ...], Field(min_length=2, max_length=4_096)
    ]
    assignment_projection_sha256: Sha256
    v2_case_universe_is_post_run_compatibility_projection: Literal[True] = True
    caller_supplied_group_keys_allowed: Literal[False] = False
    private_custody: Literal[True] = True
    scientific_conclusion: Literal[False] = False

    @field_validator("final_cases_declared_at", "created_at")
    @classmethod
    def validate_time(cls, value: str) -> str:
        return _require_rfc3339(value)

    @model_validator(mode="after")
    def validate_release(self) -> "StructureGroupingPrivateEvidenceReleaseV2":
        completed = datetime.fromisoformat(self.computation.formal_run.completed_at)
        declared = datetime.fromisoformat(self.final_cases_declared_at)
        created = datetime.fromisoformat(self.created_at)
        if not completed < declared <= created:
            raise ValueError("final cases must be declared after computation completion")
        cases = tuple(item.case_id for item in self.final_cases)
        if cases != tuple(sorted(set(cases))):
            raise ValueError("final cases must be case-ID sorted and unique")
        projections = tuple(item.candidate_key for item in self.final_case_projections)
        expected_candidates = tuple(
            item.candidate_key for item in self.computation.input_manifest.case_inputs
        )
        if projections != expected_candidates:
            raise ValueError("final projections do not exactly cover pre-group candidates")
        axes = tuple(item.axis.value for item in self.grouping_algorithms)
        if axes != tuple(sorted(axis.value for axis in STRUCTURE_LEAKAGE_AXES)):
            raise ValueError("final release requires both sorted algorithms")
        if self.grouping_algorithms != self.computation.grouping_algorithms:
            raise ValueError("final algorithms differ from computed algorithms")
        if tuple(item.axis.value for item in self.grouping_runs) != axes:
            raise ValueError("final release requires both sorted compatibility runs")
        assignment_keys = tuple(
            (item.axis.value, item.case_id) for item in self.grouping_assignments
        )
        if assignment_keys != tuple(
            sorted(
                (axis.value, case_id)
                for axis in STRUCTURE_LEAKAGE_AXES
                for case_id in cases
            )
        ):
            raise ValueError("assignments do not exactly cover final cases and axes")
        if self.assignment_projection_sha256 != canonical_sha256(
            {
                "final_case_projections": self.final_case_projections,
                "grouping_runs": self.grouping_runs,
                "grouping_assignments": self.grouping_assignments,
            }
        ):
            raise ValueError("assignment projection SHA differs from exact projection")
        _assert_addressed(
            self,
            id_field="release_id",
            sha_field="release_sha256",
            prefix="structure-private-release-v2",
        )
        return self


def _not_ready() -> None:
    raise RuntimeError(
        "formal structure grouping V2 is being rebuilt; readiness is PILOT_NO_GO"
    )


def build_structure_grouping_case_input_v2(
    *, preimage: PreGroupCandidatePreimageV2
) -> StructureGroupingCaseInputV2:
    validated = PreGroupCandidatePreimageV2.model_validate(
        preimage.model_dump(mode="python", round_trip=True)
    )
    pre_group_slot_key = deterministic_id(
        "pre-group-slot",
        {
            "study_phase": validated.study_phase,
            "slot_index": validated.slot_index,
        },
    )
    candidate_key = deterministic_id(
        "pre-group-candidate",
        {
            "pre_group_slot_key": pre_group_slot_key,
            "priority": validated.priority,
            "preimage_sha256": canonical_sha256(
                validated.model_dump(mode="python", round_trip=True)
            ),
        },
    )
    values: dict[str, object] = {
        "candidate_key": candidate_key,
        "pre_group_slot_key": pre_group_slot_key,
        "preimage": validated,
    }
    return StructureGroupingCaseInputV2.model_validate(
        _build_addressed(
            StructureGroupingCaseInputV2,
            id_field="input_id",
            sha_field="input_sha256",
            prefix="structure-case-input",
            values=values,
        ).model_dump(mode="python", round_trip=True)
    )


def assert_structure_grouping_input_manifest_exact_replay_v2(
    manifest: StructureGroupingInputManifestV2,
) -> None:
    """Reparse every raw byte string and exact-compare the sealed local input."""

    validated = StructureGroupingInputManifestV2.model_validate(
        manifest.model_dump(mode="python", round_trip=True)
    )
    expected_runtime = _build_runtime_identity_v2(validated.parameters)
    if validated.runtime_identity != expected_runtime:
        raise ValueError("sealed structure runtime differs from the current local runtime")
    replayed_artifacts: list[NormalizedStructureArtifactV2] = []
    for artifact in validated.structure_artifacts:
        replayed = normalize_raw_structure_artifact_v2(
            raw_artifact=artifact.raw_artifact,
            dimensionality=artifact.provenance.declared_dimensionality,
        )
        if replayed != artifact:
            raise ValueError("normalized structure artifact differs from raw parser replay")
        replayed_artifacts.append(replayed)
    expected_root = _build_input_root_v2(
        validated.case_inputs,
        tuple(replayed_artifacts),
        expected_runtime,
    )
    if validated.input_root != expected_root:
        raise ValueError("sealed input root differs from exact raw parser replay")


def seal_structure_grouping_input_manifest_v2(
    *,
    case_inputs: Iterable[StructureGroupingCaseInputV2],
    structure_artifacts: Iterable[NormalizedStructureArtifactV2],
    parameters: StructureGroupingParametersV2 | None = None,
    sealed_at: str | None = None,
    sealed_monotonic_ns: int | None = None,
) -> StructureGroupingInputManifestV2:
    """Seal the non-circular raw/normalized input before grouping can run."""

    frozen_parameters = StructureGroupingParametersV2.model_validate(
        (parameters or StructureGroupingParametersV2()).model_dump(
            mode="python", round_trip=True
        )
    )
    ordered_inputs = tuple(
        sorted(
            (
                StructureGroupingCaseInputV2.model_validate(
                    item.model_dump(mode="python", round_trip=True)
                )
                for item in case_inputs
            ),
            key=lambda item: item.candidate_key,
        )
    )
    ordered_artifacts = tuple(
        sorted(
            (
                NormalizedStructureArtifactV2.model_validate(
                    item.model_dump(mode="python", round_trip=True)
                )
                for item in structure_artifacts
            ),
            key=lambda item: item.artifact_id,
        )
    )
    if not ordered_inputs or not ordered_artifacts:
        raise ValueError("structure input seal requires cases and artifacts")
    runtime = _build_runtime_identity_v2(frozen_parameters)
    root = _build_input_root_v2(ordered_inputs, ordered_artifacts, runtime)
    values: dict[str, object] = {
        "sealed_at": sealed_at or _now_rfc3339(),
        "sealed_monotonic_ns": (
            time.monotonic_ns()
            if sealed_monotonic_ns is None
            else sealed_monotonic_ns
        ),
        "parameters": frozen_parameters,
        "runtime_identity": runtime,
        "case_inputs": ordered_inputs,
        "structure_artifacts": ordered_artifacts,
        "input_root": root,
    }
    sealed = StructureGroupingInputManifestV2.model_validate(
        _build_addressed(
            StructureGroupingInputManifestV2,
            id_field="manifest_id",
            sha_field="manifest_sha256",
            prefix="structure-input-manifest",
            values=values,
        ).model_dump(mode="python", round_trip=True)
    )
    assert_structure_grouping_input_manifest_exact_replay_v2(sealed)
    return sealed


def run_structure_grouping_computation_v2(*args: object, **kwargs: object) -> None:
    _not_ready()


def finalize_structure_grouping_release_v2(*args: object, **kwargs: object) -> None:
    _not_ready()


__all__ = [
    "READINESS",
    "AperiodicAxisEvidenceV2",
    "NormalizedStructureArtifactV2",
    "NormalizedStructurePayloadV2",
    "PreGroupCandidatePreimageV2",
    "RawStructureArtifactV2",
    "StructureDimensionalityV2",
    "StructureGroupingCaseInputV2",
    "StructureGroupingInputManifestV2",
    "StructureGroupingParametersV2",
    "StructureGroupingReadiness",
    "assert_structure_grouping_input_manifest_exact_replay_v2",
    "build_aperiodic_axis_evidence_v2",
    "build_raw_structure_artifact_v2",
    "build_structure_grouping_case_input_v2",
    "finalize_structure_grouping_release_v2",
    "normalize_raw_structure_artifact_v2",
    "run_structure_grouping_computation_v2",
    "seal_structure_grouping_input_manifest_v2",
]
