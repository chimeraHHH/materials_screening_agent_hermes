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
grouping output) and is deliberately not importable as a formal API.  These
local APIs do not by themselves close downstream CandidatePool, Calibration,
PreBudget, or execution bindings; callers must still treat readiness as
``PILOT_NO_GO`` until those higher-level gates replay this private evidence.

The two structural axes are conservative leakage heuristics, not physical
equivalence claims.  In particular, ordered vacancy/intercalation derivatives
with different anonymous stoichiometry can evade both axes; an eligible study
that admits those transformations must add a separately frozen parent-lineage
edge or preregister their exclusion before Pilot GO.
"""

from __future__ import annotations

import base64
import binascii
import ctypes
import hashlib
import json
import math
import platform
import time
from collections import OrderedDict, defaultdict, deque
from collections.abc import Iterable, Sequence
from datetime import datetime, timezone
from enum import StrEnum
from importlib import metadata, util
from pathlib import Path
from threading import RLock
from typing import Annotated, Literal

import numpy as np
import spglib
from pydantic import Field, field_validator, model_validator
from pymatgen.analysis.structure_matcher import StructureMatcher
from pymatgen.core import Composition, Element, Lattice, Structure

from material_agent.inspiration.models import (
    Identifier,
    Sha256,
    ShortText,
    StrictModel,
    canonical_json_bytes,
    canonical_sha256,
    deterministic_id,
)
from material_agent.research.flatband_contracts import (
    SOURCE_CATALOG_V1_SHA256,
    Dimensionality,
    FlatBandBenchmarkCaseV1,
    FlatBandEvidenceV1,
    MechanismFamily,
    SourceRecordRefV1,
    TargetBandClass,
)
from material_agent.research.flatband_leakage import (
    STRUCTURE_LEAKAGE_AXES,
    LeakageAxis,
    StructureGroupingAlgorithmV2,
    StructureGroupingAssignmentV2,
    StructureGroupingRunV2,
    structure_grouping_case_universe_sha256_v2,
)
from material_agent.research.flatband_source_policy import (
    CaseSourcePolicyAttestationV2,
    SourceUseRole,
    assert_case_source_policy_v2,
)

NORMALIZATION_DECIMAL_PLACES = 12
MAXIMUM_RAW_STRUCTURE_BYTES = 2_000_000
MAXIMUM_MANIFEST_RAW_STRUCTURE_BYTES = 64_000_000
PROVENANCE_SCOPE = "INTERNAL_REPLAY_NOT_EXTERNAL_ATTESTATION"
EXACT_REPLAY_SUCCESS_CACHE_MAX_ENTRIES = 128
FiniteFloat = Annotated[float, Field(allow_inf_nan=False)]
Vector3 = tuple[FiniteFloat, FiniteFloat, FiniteFloat]
Matrix3 = tuple[Vector3, Vector3, Vector3]


_EXACT_REPLAY_CACHE_LOCK = RLock()
_INPUT_REPLAY_SUCCESS_CACHE: OrderedDict[tuple[str, str], None] = OrderedDict()
_COMPUTATION_REPLAY_SUCCESS_CACHE: OrderedDict[tuple[str, str], None] = OrderedDict()
_PRIVATE_RELEASE_REPLAY_SUCCESS_CACHE: OrderedDict[tuple[str, str], None] = (
    OrderedDict()
)
_UNION_REPLAY_SUCCESS_CACHE: OrderedDict[tuple[str, str, str], None] = OrderedDict()


def _exact_replay_cache_hit_v2(
    cache: OrderedDict[tuple[str, ...], None], key: tuple[str, ...]
) -> bool:
    """Return a bounded process-local success memo without weakening validation.

    Callers must first reconstruct the frozen Pydantic object from ``model_dump``
    and bind it to the current runtime.  A semantic mutation therefore either
    fails content-address validation or produces a new SHA and a cache miss.
    """

    with _EXACT_REPLAY_CACHE_LOCK:
        if key not in cache:
            return False
        cache.move_to_end(key)
        return True


def _remember_exact_replay_success_v2(
    cache: OrderedDict[tuple[str, ...], None], key: tuple[str, ...]
) -> None:
    with _EXACT_REPLAY_CACHE_LOCK:
        cache[key] = None
        cache.move_to_end(key)
        while len(cache) > EXACT_REPLAY_SUCCESS_CACHE_MAX_ENTRIES:
            cache.popitem(last=False)


class StructureGroupingReadiness(StrEnum):
    PILOT_NO_GO = "PILOT_NO_GO"


READINESS = StructureGroupingReadiness.PILOT_NO_GO


class StructureDimensionalityV2(StrEnum):
    TWO_D = "2D"
    THREE_D = "3D"


class StructureGroupingParametersV2(StrictModel):
    """Frozen Pilot-V0 parameters; 2D and 3D tolerances are not conflated.

    The 96-candidate ceiling covers only calibration <=12 plus Pilot R1/R2
    <=36 each.  Main benchmark unions require a separately versioned capacity
    decision after a real dry-run; callers must not silently reuse this bound.
    """

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
    matcher_attempt_supercell: Literal[True] = True
    matcher_primitive_reduction_tolerance_angstrom: Literal[0.01] = 0.01
    two_d_matcher_maximum_supercell_site_ratio: Literal[9] = 9
    three_d_matcher_maximum_supercell_site_ratio: Literal[8] = 8
    maximum_compute_site_count: Literal[128] = 128
    maximum_compute_distinct_species: Literal[6] = 6
    maximum_compute_candidate_count: Literal[96] = 96
    matcher_allow_subset: Literal[False] = False
    matcher_all_pairs_connected_components: Literal[True] = True


def _require_rfc3339(value: str) -> str:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("timestamp must include a UTC offset")
    return value


def _now_rfc3339() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def _now_strictly_after_v2(previous: str) -> str:
    """Return a real UTC observation strictly later than ``previous``."""

    previous_time = datetime.fromisoformat(previous.replace("Z", "+00:00"))
    observed = datetime.now(timezone.utc)
    if observed <= previous_time:
        observed = datetime.now(timezone.utc)
    if observed <= previous_time:
        raise ValueError("local UTC clock does not postdate the preceding artifact")
    return observed.isoformat(timespec="microseconds")


def _assert_computation_chronology_v2(
    *,
    manifest: "StructureGroupingInputManifestV2",
    started_at: str,
    started_monotonic_ns: int,
    completed_at: str | None = None,
    completed_monotonic_ns: int | None = None,
    created_at: str | None = None,
) -> None:
    """Require a sealed input before any formal grouping computation."""

    sealed = datetime.fromisoformat(
        _require_rfc3339(manifest.sealed_at).replace("Z", "+00:00")
    )
    started = datetime.fromisoformat(
        _require_rfc3339(started_at).replace("Z", "+00:00")
    )
    if sealed >= started:
        raise ValueError("structure run start must strictly follow the input seal")
    if manifest.sealed_monotonic_ns >= started_monotonic_ns:
        raise ValueError(
            "structure run monotonic start must strictly follow the input seal"
        )

    completed: datetime | None = None
    if completed_at is not None:
        completed = datetime.fromisoformat(
            _require_rfc3339(completed_at).replace("Z", "+00:00")
        )
        if started > completed:
            raise ValueError("structure run completes before it starts")
    if (
        completed_monotonic_ns is not None
        and started_monotonic_ns > completed_monotonic_ns
    ):
        raise ValueError("structure run monotonic clock regressed")
    if created_at is not None:
        created = datetime.fromisoformat(
            _require_rfc3339(created_at).replace("Z", "+00:00")
        )
        if (completed or started) > created:
            raise ValueError("structure computation release predates run completion")


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
    platform_system: ShortText
    platform_machine: ShortText
    pymatgen_version: ShortText
    spglib_version: ShortText
    numpy_version: ShortText
    scipy_version: ShortText
    layer_group_api: Literal["spglib.get_symmetry_layerdataset"] = (
        "spglib.get_symmetry_layerdataset"
    )
    requirements_lock_sha256: Sha256
    spglib_extension_sha256: Sha256
    libsymspg_binary_sha256: Sha256
    libsymspg_linkage: Literal[
        "DYNAMIC_LOADER_RESOLVED", "EMBEDDED_IN_EXTENSION"
    ]
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


def _sha256_file_v2(path: Path, *, label: str) -> str:
    if not path.is_file():
        raise ValueError(f"{label} is unavailable in the local formal runtime")
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _resolved_libsymspg_path_v2() -> Path | None:
    """Resolve the actually loaded libsymspg without persisting its path."""

    system = platform.system()
    candidates: list[Path] = []
    if system == "Darwin":
        process = ctypes.CDLL(None)
        try:
            count = process._dyld_image_count
            image_name = process._dyld_get_image_name
        except AttributeError as exc:
            raise ValueError("Darwin dynamic-loader introspection is unavailable") from exc
        count.restype = ctypes.c_uint32
        image_name.argtypes = [ctypes.c_uint32]
        image_name.restype = ctypes.c_char_p
        for index in range(count()):
            raw_name = image_name(index)
            if raw_name and b"libsymspg" in raw_name:
                candidates.append(Path(raw_name.decode("utf-8")).resolve())
    elif system == "Linux":
        maps = Path("/proc/self/maps")
        if not maps.is_file():
            raise ValueError("Linux loader map is unavailable for libsymspg replay")
        for line in maps.read_text(encoding="utf-8").splitlines():
            if "libsymspg" not in line:
                continue
            path_text = line.split(maxsplit=5)[-1]
            if path_text.startswith("/"):
                candidates.append(Path(path_text).resolve())
    else:
        raise ValueError("formal libsymspg resolution supports Darwin and Linux only")
    unique = tuple(sorted(set(candidates), key=lambda item: str(item)))
    if not unique:
        return None
    hashes = {_sha256_file_v2(item, label="resolved libsymspg") for item in unique}
    if len(hashes) != 1:
        raise ValueError("multiple loaded libsymspg binaries have different content")
    return unique[0]


def _runtime_binary_hashes_v2() -> tuple[str, str, str, str]:
    extension_spec = util.find_spec("spglib._spglib")
    if extension_spec is None or extension_spec.origin is None:
        raise ValueError("spglib extension module cannot be resolved")
    extension_path = Path(extension_spec.origin).resolve()
    extension_sha256 = _sha256_file_v2(
        extension_path, label="spglib extension binary"
    )
    loaded_libsymspg = _resolved_libsymspg_path_v2()
    if loaded_libsymspg is None:
        libsymspg_sha256 = extension_sha256
        linkage = "EMBEDDED_IN_EXTENSION"
    else:
        libsymspg_sha256 = _sha256_file_v2(
            loaded_libsymspg, label="resolved libsymspg binary"
        )
        linkage = "DYNAMIC_LOADER_RESOLVED"
    repository_root = Path(__file__).resolve().parents[3]
    requirements_sha256 = _sha256_file_v2(
        repository_root / "requirements.lock",
        label="requirements.lock",
    )
    return requirements_sha256, extension_sha256, libsymspg_sha256, linkage


def _build_runtime_identity_v2(
    parameters: StructureGroupingParametersV2,
) -> StructureGroupingRuntimeIdentityV2:
    module_sha256 = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    (
        requirements_sha256,
        spglib_extension_sha256,
        libsymspg_binary_sha256,
        libsymspg_linkage,
    ) = _runtime_binary_hashes_v2()
    values: dict[str, object] = {
        "python_implementation": platform.python_implementation(),
        "python_version": platform.python_version(),
        "platform_system": platform.system(),
        "platform_machine": platform.machine(),
        "pymatgen_version": metadata.version("pymatgen"),
        "spglib_version": metadata.version("spglib"),
        "numpy_version": metadata.version("numpy"),
        "scipy_version": metadata.version("scipy"),
        "requirements_lock_sha256": requirements_sha256,
        "spglib_extension_sha256": spglib_extension_sha256,
        "libsymspg_binary_sha256": libsymspg_binary_sha256,
        "libsymspg_linkage": libsymspg_linkage,
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
        slot_preimages: dict[str, tuple[str, int, list[int]]] = {}
        for item in self.case_inputs:
            phase = item.preimage.study_phase
            index = item.preimage.slot_index
            previous = slot_preimages.setdefault(
                item.pre_group_slot_key, (phase, index, [])
            )
            if previous[:2] != (phase, index):
                raise ValueError("one pre-group slot key resolves to conflicting slots")
            previous[2].append(item.preimage.priority)
        slot_identities = tuple(
            sorted((phase, index) for phase, index, _ in slot_preimages.values())
        )
        if len(slot_identities) != len(set(slot_identities)):
            raise ValueError("one logical slot resolves to multiple pre-group slot keys")
        for _, _, priorities in slot_preimages.values():
            if tuple(sorted(priorities)) != tuple(range(len(priorities))):
                raise ValueError("replacement priorities must be contiguous from zero")
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
    """Origin-invariant anonymous orbit role.

    Wyckoff letters are deliberately excluded: an otherwise identical layer can
    receive a different letter after an origin shift.  Site-symmetry symbols and
    orbit multiplicities are invariant under that shift for the frozen spglib
    runtime and are therefore the only orbit identity admitted to a prototype
    signature.
    """

    site_symmetry_symbol: Annotated[str, Field(min_length=1, max_length=32)]
    multiplicity: Annotated[int, Field(ge=1, le=2_048)]


class AnonymousWyckoffRoleV2(StrictModel):
    site_count: Annotated[int, Field(ge=1, le=2_048)]
    orbits: Annotated[tuple[WyckoffOrbitRoleV2, ...], Field(min_length=1)]

    @model_validator(mode="after")
    def validate_role(self) -> "AnonymousWyckoffRoleV2":
        keys = tuple(
            (item.site_symmetry_symbol, item.multiplicity)
            for item in self.orbits
        )
        if keys != tuple(sorted(keys)):
            raise ValueError("anonymous orbit roles must be sorted")
        if sum(item.multiplicity for item in self.orbits) != self.site_count:
            raise ValueError("orbit multiplicities do not cover role sites")
        return self


class SymmetrySignatureV2(StrictModel):
    symmetry_kind: Literal["SPACE_GROUP_3D", "LAYER_GROUP_2D"]
    group_number: Annotated[int, Field(ge=1, le=230)]
    hall_number: Annotated[int, Field(ge=-512, le=530)]
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
                    (orbit.site_symmetry_symbol, orbit.multiplicity)
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
    left_reduced_site_count: Annotated[int, Field(ge=1, le=128)]
    right_reduced_site_count: Annotated[int, Field(ge=1, le=128)]
    anonymous_supercell_factor: Annotated[int, Field(ge=1, le=9)] | None
    matcher_stage: Literal[
        "DIMENSION_MISMATCH",
        "ANONYMOUS_STOICHIOMETRY_MISMATCH",
        "DIRECT_REDUCED_CELL",
        "BOUNDED_SUPERCELL",
    ]
    forward_fit_anonymous: bool
    reverse_fit_anonymous: bool
    fit_anonymous: bool
    matcher_direction_policy: Literal["BIDIRECTIONAL_CONSERVATIVE_OR"] = (
        "BIDIRECTIONAL_CONSERVATIVE_OR"
    )
    scientific_conclusion: Literal[False] = False

    @model_validator(mode="after")
    def validate_pair(self) -> "FingerprintPairEvidenceV2":
        if self.left_candidate_key >= self.right_candidate_key:
            raise ValueError("fingerprint pair keys must be strictly ordered")
        if self.fit_anonymous != (
            self.forward_fit_anonymous or self.reverse_fit_anonymous
        ):
            raise ValueError("fingerprint edge must be the conservative directional OR")
        if self.matcher_stage == "BOUNDED_SUPERCELL":
            if self.anonymous_supercell_factor is None or (
                self.left_reduced_site_count == self.right_reduced_site_count
            ):
                raise ValueError("bounded supercell evidence lacks a nontrivial factor")
        elif self.matcher_stage == "DIRECT_REDUCED_CELL":
            if self.anonymous_supercell_factor != 1 or (
                self.left_reduced_site_count != self.right_reduced_site_count
            ):
                raise ValueError("direct matcher stage requires equal reduced cells")
        elif self.anonymous_supercell_factor is not None or self.fit_anonymous:
            raise ValueError("skipped matcher stages cannot report a fit or factor")
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
        _assert_computation_chronology_v2(
            manifest=self.input_manifest,
            started_at=self.formal_run.started_at,
            started_monotonic_ns=self.formal_run.started_monotonic_ns,
            completed_at=self.formal_run.completed_at,
            completed_monotonic_ns=self.formal_run.completed_monotonic_ns,
            created_at=self.created_at,
        )
        algorithm_axes = tuple(item.axis.value for item in self.grouping_algorithms)
        if algorithm_axes != tuple(
            sorted(axis.value for axis in STRUCTURE_LEAKAGE_AXES)
        ):
            raise ValueError("computation requires both sorted structure algorithms")
        by_algorithm_id = tuple(
            sorted(self.grouping_algorithms, key=lambda item: item.algorithm_id)
        )
        if (
            self.formal_run.algorithm_ids,
            self.formal_run.algorithm_sha256s,
        ) != (
            tuple(item.algorithm_id for item in by_algorithm_id),
            tuple(item.algorithm_sha256 for item in by_algorithm_id),
        ):
            raise ValueError("formal run algorithm identities differ from computation")
        keys = tuple(item.candidate_key for item in self.prototype_evidence)
        expected_keys = tuple(item.candidate_key for item in self.input_manifest.case_inputs)
        if keys != expected_keys:
            raise ValueError("prototype evidence does not exactly cover input candidates")
        input_by_key = {
            item.candidate_key: item for item in self.input_manifest.case_inputs
        }
        for evidence in self.prototype_evidence:
            case_input = input_by_key[evidence.candidate_key]
            if (
                evidence.input_id,
                evidence.input_sha256,
                evidence.structure_sha256,
                evidence.canonicalized_structure.dimensionality,
            ) != (
                case_input.input_id,
                case_input.input_sha256,
                case_input.preimage.structure_sha256,
                case_input.preimage.dimensionality,
            ):
                raise ValueError("prototype evidence is cross-wired to another input")
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
        for pair in self.fingerprint_pair_evidence:
            left = input_by_key[pair.left_candidate_key]
            right = input_by_key[pair.right_candidate_key]
            if (
                pair.left_input_sha256,
                pair.left_structure_sha256,
                pair.right_input_sha256,
                pair.right_structure_sha256,
            ) != (
                left.input_sha256,
                left.preimage.structure_sha256,
                right.input_sha256,
                right.preimage.structure_sha256,
            ):
                raise ValueError("fingerprint pair evidence is cross-wired")
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
            for component in components:
                expected_structures = tuple(
                    input_by_key[key].preimage.structure_sha256
                    for key in component.candidate_keys
                )
                if component.structure_sha256s != expected_structures:
                    raise ValueError(f"{label} component structures are cross-wired")
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
    source_policy_attestations: Annotated[
        tuple[CaseSourcePolicyAttestationV2, ...],
        Field(min_length=1, max_length=32_768),
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
        if len(projections) != len(cases):
            raise ValueError("final projections do not exactly cover final cases")
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
        _assert_source_policy_projection_closure_v2(self)
        _assert_final_projection_closure_v2(self)
        _assert_addressed(
            self,
            id_field="release_id",
            sha_field="release_sha256",
            prefix="structure-private-release-v2",
        )
        return self


class StructureGroupingUnionMemberRefV2(StrictModel):
    schema_version: Literal["flatband-structure-union-member-ref-v2"] = (
        "flatband-structure-union-member-ref-v2"
    )
    member_ref_id: Identifier
    member_ref_sha256: Sha256
    owner_id: Identifier
    member_release_id: Identifier
    member_release_sha256: Sha256
    member_release_created_at: Annotated[str, Field(min_length=20, max_length=40)]
    member_computation_id: Identifier
    member_computation_sha256: Sha256
    member_input_manifest_id: Identifier
    member_input_manifest_sha256: Sha256
    candidate_keys: Annotated[
        tuple[Identifier, ...], Field(min_length=1, max_length=96)
    ]

    @field_validator("member_release_created_at")
    @classmethod
    def validate_time(cls, value: str) -> str:
        return _require_rfc3339(value)

    @model_validator(mode="after")
    def validate_ref(self) -> "StructureGroupingUnionMemberRefV2":
        if self.candidate_keys != tuple(sorted(set(self.candidate_keys))):
            raise ValueError("union member candidate keys must be sorted and unique")
        _assert_addressed(
            self,
            id_field="member_ref_id",
            sha_field="member_ref_sha256",
            prefix="structure-union-member-v2",
        )
        return self


class StructureGroupingUnionCandidateOwnerV2(StrictModel):
    schema_version: Literal["flatband-structure-union-candidate-owner-v2"] = (
        "flatband-structure-union-candidate-owner-v2"
    )
    owner_projection_id: Identifier
    owner_projection_sha256: Sha256
    candidate_key: Identifier
    input_id: Identifier
    input_sha256: Sha256
    structure_sha256: Sha256
    owner_id: Identifier
    member_release_id: Identifier
    member_release_sha256: Sha256

    @model_validator(mode="after")
    def validate_owner(self) -> "StructureGroupingUnionCandidateOwnerV2":
        _assert_addressed(
            self,
            id_field="owner_projection_id",
            sha_field="owner_projection_sha256",
            prefix="structure-union-owner-v2",
        )
        return self


class StructureGroupingUnionReplayReleaseV2(StrictModel):
    """Private durable proof that a fresh raw union has no cross-owner edge."""

    schema_version: Literal["flatband-structure-union-replay-release-v2"] = (
        "flatband-structure-union-replay-release-v2"
    )
    union_release_id: Identifier
    union_release_sha256: Sha256
    member_refs: Annotated[
        tuple[StructureGroupingUnionMemberRefV2, ...],
        Field(min_length=2, max_length=32),
    ]
    candidate_owner_projection: Annotated[
        tuple[StructureGroupingUnionCandidateOwnerV2, ...],
        Field(min_length=2, max_length=96),
    ]
    merged_computation: StructureGroupingComputationReleaseV2
    member_release_set_sha256: Sha256
    candidate_owner_projection_sha256: Sha256
    merged_input_manifest_sha256: Sha256
    merged_input_root_sha256: Sha256
    merged_output_root_sha256: Sha256
    union_input_sealed_at: Annotated[str, Field(min_length=20, max_length=40)]
    cross_owner_prototype_component_ids: tuple[()] = ()
    cross_owner_fingerprint_component_ids: tuple[()] = ()
    cross_owner_prototype_component_count: Literal[0] = 0
    cross_owner_fingerprint_component_count: Literal[0] = 0
    verified_at: Annotated[str, Field(min_length=20, max_length=40)]
    private_custody: Literal[True] = True
    scientific_conclusion: Literal[False] = False

    @field_validator("union_input_sealed_at", "verified_at")
    @classmethod
    def validate_time(cls, value: str) -> str:
        return _require_rfc3339(value)

    @model_validator(mode="after")
    def validate_release(self) -> "StructureGroupingUnionReplayReleaseV2":
        _assert_union_replay_model_closure_v2(self)
        _assert_addressed(
            self,
            id_field="union_release_id",
            sha_field="union_release_sha256",
            prefix="structure-union-replay-v2",
        )
        return self


def _payload_to_structure_v2(payload: NormalizedStructurePayloadV2) -> Structure:
    validated = NormalizedStructurePayloadV2.model_validate(
        payload.model_dump(mode="python", round_trip=True)
    )
    return Structure(
        Lattice(validated.lattice_matrix_angstrom),
        [item.species for item in validated.sites],
        [item.fractional_coordinates for item in validated.sites],
        coords_are_cartesian=False,
        to_unit_cell=True,
    )


def _least_payload_v2(
    payloads: Iterable[NormalizedStructurePayloadV2],
) -> NormalizedStructurePayloadV2:
    candidates = tuple(payloads)
    if not candidates:
        raise ValueError("canonicalization produced no payload candidate")
    return min(
        candidates,
        key=lambda item: canonical_json_bytes(
            item.model_dump(mode="json", round_trip=True)
        ),
    )


def _canonicalize_fractional_origin_v2(
    payload: NormalizedStructurePayloadV2,
    *,
    translated_axes: tuple[int, ...],
) -> NormalizedStructurePayloadV2:
    """Choose the lexicographically least site-anchored periodic origin."""

    structure = _payload_to_structure_v2(payload)
    lattice = np.asarray(structure.lattice.matrix, dtype=float)
    coordinates = np.asarray(structure.frac_coords, dtype=float)
    species = [site.specie.symbol for site in structure]
    candidates: list[NormalizedStructurePayloadV2] = []
    for anchor in coordinates:
        shifted = coordinates.copy()
        for axis in translated_axes:
            shifted[:, axis] = np.mod(shifted[:, axis] - anchor[axis], 1.0)
        candidates.append(
            _normalize_pymatgen_structure_v2(
                Structure(
                    Lattice(lattice),
                    species,
                    shifted,
                    coords_are_cartesian=False,
                    to_unit_cell=True,
                )
            )
        )
    return _least_payload_v2(candidates)


def _canonicalize_structure_v2(
    *,
    case_input: StructureGroupingCaseInputV2,
    artifact: NormalizedStructureArtifactV2,
    parameters: StructureGroupingParametersV2,
) -> CanonicalizedStructureEvidenceV2:
    """Canonicalize 3D origins or rebuild a 2D cell normal to its layer plane."""

    dimensionality = case_input.preimage.dimensionality
    if dimensionality is StructureDimensionalityV2.THREE_D:
        return CanonicalizedStructureEvidenceV2(
            dimensionality=dimensionality,
            original_aperiodic_axis=None,
            canonical_aperiodic_axis=None,
            source_cell_normal_height_angstrom=None,
            layer_thickness_angstrom=None,
            source_vacuum_gap_angstrom=None,
            normalized_vacuum_padding_angstrom=None,
            canonical_payload=_canonicalize_fractional_origin_v2(
                artifact.payload, translated_axes=(0, 1, 2)
            ),
            layer_group_api_used=False,
        )

    axis = case_input.preimage.aperiodic_axis
    if axis is None:
        raise ValueError("2D canonicalization requires a unique Pilot vacuum axis")
    payload = artifact.payload
    lattice = np.asarray(payload.lattice_matrix_angstrom, dtype=float)
    coordinates = np.asarray(
        [item.fractional_coordinates for item in payload.sites], dtype=float
    )
    species = [item.species for item in payload.sites]
    periodic_axes = [value for value in range(3) if value != axis]
    permutation = (*periodic_axes, axis)
    permuted_lattice = lattice[list(permutation)].copy()
    permuted_coordinates = coordinates[:, list(permutation)].copy()
    if float(np.linalg.det(permuted_lattice)) < 0.0:
        permuted_lattice[0] *= -1.0
        permuted_coordinates[:, 0] *= -1.0

    plane_normal = np.cross(permuted_lattice[0], permuted_lattice[1])
    plane_norm = float(np.linalg.norm(plane_normal))
    if plane_norm <= 1.0e-12:
        raise ValueError("2D periodic plane is degenerate")
    plane_normal /= plane_norm
    source_height = float(np.dot(permuted_lattice[2], plane_normal))
    if source_height <= 0.0:
        raise ValueError("2D source cell normal height is non-positive")

    z_values = sorted(float(value % 1.0) for value in permuted_coordinates[:, 2])
    gap_rows = [
        (right - left, right)
        for left, right in zip(z_values, z_values[1:])
    ]
    gap_rows.append((z_values[0] + 1.0 - z_values[-1], z_values[0]))
    maximum_gap = max(item[0] for item in gap_rows)
    cuts = tuple(
        cut
        for gap, cut in gap_rows
        if math.isclose(gap, maximum_gap, rel_tol=0.0, abs_tol=1.0e-12)
    )
    axis_row = case_input.preimage.aperiodic_axis_evidence.axes[axis]
    layer_thickness = source_height * (1.0 - maximum_gap)
    if not math.isclose(
        layer_thickness,
        axis_row.occupied_thickness_angstrom,
        rel_tol=0.0,
        abs_tol=1.0e-8,
    ):
        raise ValueError("2D layer thickness differs from sealed vacuum evidence")
    padding = parameters.two_d_vacuum_padding_angstrom
    canonical_height = layer_thickness + 2.0 * padding
    canonical_lattice = np.asarray(
        (
            permuted_lattice[0],
            permuted_lattice[1],
            plane_normal * canonical_height,
        ),
        dtype=float,
    )
    inverse_canonical_lattice = np.linalg.inv(canonical_lattice)
    candidates: list[NormalizedStructurePayloadV2] = []
    for cut in cuts:
        unwrapped = permuted_coordinates.copy()
        unwrapped[:, 2] = np.mod(unwrapped[:, 2] - cut, 1.0)
        source_relative_cartesian = unwrapped @ permuted_lattice
        canonical_fractional = source_relative_cartesian @ inverse_canonical_lattice
        heights = canonical_fractional[:, 2] * canonical_height
        canonical_fractional[:, 2] = (
            heights - float(np.min(heights)) + padding
        ) / canonical_height
        canonical_fractional[:, :2] = np.mod(canonical_fractional[:, :2], 1.0)
        base = _normalize_pymatgen_structure_v2(
            Structure(
                Lattice(canonical_lattice),
                species,
                canonical_fractional,
                coords_are_cartesian=False,
                to_unit_cell=True,
            )
        )
        candidates.append(
            _canonicalize_fractional_origin_v2(base, translated_axes=(0, 1))
        )
    canonical_payload = _least_payload_v2(candidates)
    return CanonicalizedStructureEvidenceV2(
        dimensionality=dimensionality,
        original_aperiodic_axis=axis,
        canonical_aperiodic_axis=2,
        source_cell_normal_height_angstrom=_quantize(source_height),
        layer_thickness_angstrom=_quantize(layer_thickness),
        source_vacuum_gap_angstrom=axis_row.maximum_cyclic_gap_angstrom,
        normalized_vacuum_padding_angstrom=_quantize(padding),
        canonical_payload=canonical_payload,
        layer_group_api_used=True,
    )


def _spglib_cell_v2(payload: NormalizedStructurePayloadV2) -> tuple[object, ...]:
    structure = _payload_to_structure_v2(payload)
    return (
        np.asarray(structure.lattice.matrix, dtype=float),
        np.asarray(structure.frac_coords, dtype=float),
        np.asarray(structure.atomic_numbers, dtype=int),
    )


def _symmetry_dataset_v2(
    payload: NormalizedStructurePayloadV2,
    *,
    dimensionality: StructureDimensionalityV2,
    symprec: float,
    angle_tolerance_degrees: float,
) -> object:
    cell = _spglib_cell_v2(payload)
    if dimensionality is StructureDimensionalityV2.TWO_D:
        dataset = spglib.get_symmetry_layerdataset(
            cell, aperiodic_dir=2, symprec=symprec
        )
    else:
        dataset = spglib.get_symmetry_dataset(
            cell,
            symprec=symprec,
            angle_tolerance=angle_tolerance_degrees,
        )
    if dataset is None:
        raise ValueError("spglib could not determine symmetry at a frozen tolerance")
    return dataset


def _standardized_payload_from_dataset_v2(dataset: object) -> NormalizedStructurePayloadV2:
    try:
        lattice = np.asarray(dataset.std_lattice, dtype=float)
        positions = np.asarray(dataset.std_positions, dtype=float)
        numbers = tuple(int(value) for value in dataset.std_types)
    except (AttributeError, TypeError, ValueError) as exc:
        raise ValueError("spglib dataset lacks a complete standardized cell") from exc
    try:
        species = [Element.from_Z(value).symbol for value in numbers]
    except ValueError as exc:
        raise ValueError("spglib standardized cell contains a non-element type") from exc
    return _normalize_pymatgen_structure_v2(
        Structure(
            Lattice(lattice),
            species,
            positions,
            coords_are_cartesian=False,
            to_unit_cell=True,
        )
    )


def _anonymous_roles_v2(
    dataset: object,
    payload: NormalizedStructurePayloadV2,
) -> tuple[AnonymousWyckoffRoleV2, ...]:
    species = tuple(item.species for item in payload.sites)
    try:
        equivalent = tuple(int(value) for value in dataset.equivalent_atoms)
        site_symmetries = tuple(str(value) for value in dataset.site_symmetry_symbols)
    except (AttributeError, TypeError, ValueError) as exc:
        raise ValueError("spglib dataset lacks anonymous-orbit evidence") from exc
    if len(species) != len(equivalent) or len(species) != len(site_symmetries):
        raise ValueError("spglib orbit arrays do not cover the standardized cell")
    roles: list[AnonymousWyckoffRoleV2] = []
    for symbol in sorted(set(species)):
        indices = tuple(index for index, value in enumerate(species) if value == symbol)
        by_orbit: dict[int, list[int]] = defaultdict(list)
        for index in indices:
            by_orbit[equivalent[index]].append(index)
        orbits: list[WyckoffOrbitRoleV2] = []
        for orbit_indices in by_orbit.values():
            symbols = {site_symmetries[index] for index in orbit_indices}
            if len(symbols) != 1:
                raise ValueError("one spglib orbit has conflicting site symmetries")
            orbits.append(
                WyckoffOrbitRoleV2(
                    site_symmetry_symbol=next(iter(symbols)),
                    multiplicity=len(orbit_indices),
                )
            )
        roles.append(
            AnonymousWyckoffRoleV2(
                site_count=len(indices),
                orbits=tuple(
                    sorted(
                        orbits,
                        key=lambda item: (
                            item.site_symmetry_symbol,
                            item.multiplicity,
                        ),
                    )
                ),
            )
        )
    return tuple(
        sorted(
            roles,
            key=lambda role: (
                role.site_count,
                tuple(
                    (item.site_symmetry_symbol, item.multiplicity)
                    for item in role.orbits
                ),
            ),
        )
    )


def _threshold_evidence_v2(
    canonicalized: CanonicalizedStructureEvidenceV2,
    *,
    symprec: float,
    angle_tolerance_degrees: float,
) -> PrototypeThresholdEvidenceV2:
    dimensionality = canonicalized.dimensionality
    first = _symmetry_dataset_v2(
        canonicalized.canonical_payload,
        dimensionality=dimensionality,
        symprec=symprec,
        angle_tolerance_degrees=angle_tolerance_degrees,
    )
    standardized = _standardized_payload_from_dataset_v2(first)
    # Dataset orbit arrays refer to the input cell, not ``std_positions``.  A
    # second pass over the standardized cell is required before zipping roles.
    second = _symmetry_dataset_v2(
        standardized,
        dimensionality=dimensionality,
        symprec=symprec,
        angle_tolerance_degrees=angle_tolerance_degrees,
    )
    standardized = _standardized_payload_from_dataset_v2(second)
    third = _symmetry_dataset_v2(
        standardized,
        dimensionality=dimensionality,
        symprec=symprec,
        angle_tolerance_degrees=angle_tolerance_degrees,
    )
    signature = SymmetrySignatureV2(
        symmetry_kind=(
            "LAYER_GROUP_2D"
            if dimensionality is StructureDimensionalityV2.TWO_D
            else "SPACE_GROUP_3D"
        ),
        group_number=int(third.number),
        hall_number=int(third.hall_number),
        international_symbol=str(third.international),
        anonymous_roles=_anonymous_roles_v2(third, standardized),
    )
    return PrototypeThresholdEvidenceV2(
        symprec_angstrom=symprec,
        standardized_payload=standardized,
        signature=signature,
        signature_sha256=canonical_sha256(signature.model_dump(mode="python")),
    )


def _connected_components_v2(
    candidate_keys: tuple[str, ...],
    edges: Iterable[tuple[str, str]],
) -> tuple[tuple[str, ...], ...]:
    neighbours = {key: set() for key in candidate_keys}
    for left, right in edges:
        if left not in neighbours or right not in neighbours or left == right:
            raise ValueError("component edge references an invalid candidate pair")
        neighbours[left].add(right)
        neighbours[right].add(left)
    remaining = set(candidate_keys)
    components: list[tuple[str, ...]] = []
    while remaining:
        root = min(remaining)
        queue = deque((root,))
        connected: set[str] = set()
        while queue:
            value = queue.popleft()
            if value in connected:
                continue
            connected.add(value)
            queue.extend(sorted(neighbours[value] - connected))
        remaining.difference_update(connected)
        components.append(tuple(sorted(connected)))
    return tuple(sorted(components))


def _build_grouping_algorithms_v2(
    manifest: StructureGroupingInputManifestV2,
) -> tuple[StructureGroupingAlgorithmV2, StructureGroupingAlgorithmV2]:
    parameters = manifest.parameters
    specifications = {
        LeakageAxis.STRUCTURE_PROTOTYPE: {
            "algorithm_name": "spglib-anonymous-symmetry-threshold-union",
            "configuration": {
                "three_d_schedule": parameters.three_d_symprec_schedule_angstrom,
                "two_d_schedule": parameters.two_d_layer_symprec_schedule_angstrom,
                "angle_tolerance_degrees": parameters.angle_tolerance_degrees,
                "two_d_vacuum_padding_angstrom": parameters.two_d_vacuum_padding_angstrom,
                "orbit_identity": "site-symmetry-and-multiplicity",
                "component_policy": "cross-threshold-any-signature-connected-component",
            },
        },
        LeakageAxis.STRUCTURE_FINGERPRINT: {
            "algorithm_name": "pymatgen-bidirectional-anonymous-structure-matcher",
            "configuration": {
                "ltol": parameters.matcher_lattice_length_tolerance,
                "stol": parameters.matcher_site_tolerance,
                "angle_tol": parameters.matcher_angle_tolerance_degrees,
                "primitive_cell": parameters.matcher_primitive_cell,
                "scale": parameters.matcher_scale,
                "attempt_supercell": parameters.matcher_attempt_supercell,
                "primitive_reduction_tolerance_angstrom": parameters.matcher_primitive_reduction_tolerance_angstrom,
                "two_d_maximum_supercell_site_ratio": parameters.two_d_matcher_maximum_supercell_site_ratio,
                "three_d_maximum_supercell_site_ratio": parameters.three_d_matcher_maximum_supercell_site_ratio,
                "allow_subset": parameters.matcher_allow_subset,
                "direction_policy": "BIDIRECTIONAL_CONSERVATIVE_OR",
                "component_policy": "all-pairs-connected-component",
            },
        },
    }
    algorithms: list[StructureGroupingAlgorithmV2] = []
    for axis in sorted(STRUCTURE_LEAKAGE_AXES, key=lambda item: item.value):
        spec = specifications[axis]
        values: dict[str, object] = {
            "axis": axis,
            "algorithm_name": spec["algorithm_name"],
            "algorithm_version": "v2",
            "implementation_sha256": manifest.runtime_identity.module_implementation_sha256,
            "configuration_sha256": canonical_sha256(spec["configuration"]),
        }
        algorithms.append(
            StructureGroupingAlgorithmV2.model_validate(
                _build_addressed(
                    StructureGroupingAlgorithmV2,
                    id_field="algorithm_id",
                    sha_field="algorithm_sha256",
                    prefix="structure-group-algorithm",
                    values=values,
                ).model_dump(mode="python", round_trip=True)
            )
        )
    return tuple(algorithms)  # type: ignore[return-value]


def _build_prototype_evidence_v2(
    manifest: StructureGroupingInputManifestV2,
) -> tuple[PrototypeCaseEvidenceV2, ...]:
    artifacts = {item.artifact_id: item for item in manifest.structure_artifacts}
    evidence_rows: list[PrototypeCaseEvidenceV2] = []
    for case_input in manifest.case_inputs:
        artifact = artifacts[case_input.preimage.structure_artifact_id]
        canonicalized = _canonicalize_structure_v2(
            case_input=case_input,
            artifact=artifact,
            parameters=manifest.parameters,
        )
        schedule: tuple[float, ...]
        if case_input.preimage.dimensionality is StructureDimensionalityV2.TWO_D:
            schedule = manifest.parameters.two_d_layer_symprec_schedule_angstrom
        else:
            schedule = manifest.parameters.three_d_symprec_schedule_angstrom
        thresholds = tuple(
            _threshold_evidence_v2(
                canonicalized,
                symprec=value,
                angle_tolerance_degrees=manifest.parameters.angle_tolerance_degrees,
            )
            for value in schedule
        )
        values: dict[str, object] = {
            "candidate_key": case_input.candidate_key,
            "input_id": case_input.input_id,
            "input_sha256": case_input.input_sha256,
            "structure_sha256": case_input.preimage.structure_sha256,
            "canonicalized_structure": canonicalized,
            "threshold_evidence": thresholds,
        }
        evidence_rows.append(
            PrototypeCaseEvidenceV2.model_validate(
                _build_addressed(
                    PrototypeCaseEvidenceV2,
                    id_field="evidence_id",
                    sha_field="evidence_sha256",
                    prefix="prototype-case-evidence",
                    values=values,
                ).model_dump(mode="python", round_trip=True)
            )
        )
    return tuple(evidence_rows)


def _build_prototype_components_v2(
    manifest: StructureGroupingInputManifestV2,
    evidence: tuple[PrototypeCaseEvidenceV2, ...],
) -> tuple[PrototypeComponentEvidenceV2, ...]:
    by_key = {item.candidate_key: item for item in evidence}
    keys = tuple(item.candidate_key for item in manifest.case_inputs)
    dimensions = {
        item.candidate_key: item.preimage.dimensionality
        for item in manifest.case_inputs
    }
    edges: list[tuple[str, str]] = []
    for left_index, left_key in enumerate(keys):
        for right_key in keys[left_index + 1 :]:
            if dimensions[left_key] is not dimensions[right_key]:
                continue
            left_signatures = {
                item.symprec_angstrom: item.signature_sha256
                for item in by_key[left_key].threshold_evidence
            }
            right_signatures = {
                item.symprec_angstrom: item.signature_sha256
                for item in by_key[right_key].threshold_evidence
            }
            if set(left_signatures) != set(right_signatures):
                raise ValueError("prototype schedules differ within one dimensionality")
            if set(left_signatures.values()).intersection(right_signatures.values()):
                edges.append((left_key, right_key))
    input_by_key = {item.candidate_key: item for item in manifest.case_inputs}
    components: list[PrototypeComponentEvidenceV2] = []
    for component_keys in _connected_components_v2(keys, edges):
        structure_sha256s = tuple(
            input_by_key[key].preimage.structure_sha256 for key in component_keys
        )
        values: dict[str, object] = {
            "canonical_group_key": deterministic_id(
                "structure-proto-group",
                {"structure_sha256s": tuple(sorted(structure_sha256s))},
            ),
            "candidate_keys": component_keys,
            "structure_sha256s": structure_sha256s,
        }
        components.append(
            PrototypeComponentEvidenceV2.model_validate(
                _build_addressed(
                    PrototypeComponentEvidenceV2,
                    id_field="component_id",
                    sha_field="component_sha256",
                    prefix="prototype-component-v2",
                    values=values,
                ).model_dump(mode="python", round_trip=True)
            )
        )
    return tuple(sorted(components, key=lambda item: item.candidate_keys))


def _anonymous_species_counts_v2(payload: NormalizedStructurePayloadV2) -> tuple[int, ...]:
    counts: dict[str, int] = defaultdict(int)
    for site in payload.sites:
        counts[site.species] += 1
    return tuple(sorted(counts.values()))


def _anonymous_supercell_ratio_v2(
    left: NormalizedStructurePayloadV2,
    right: NormalizedStructurePayloadV2,
) -> int | None:
    left_counts = _anonymous_species_counts_v2(left)
    right_counts = _anonymous_species_counts_v2(right)
    if len(left_counts) != len(right_counts):
        return None
    smaller, larger = (
        (left_counts, right_counts)
        if sum(left_counts) <= sum(right_counts)
        else (right_counts, left_counts)
    )
    ratios = {
        large // small
        for small, large in zip(smaller, larger)
        if small > 0 and large % small == 0
    }
    if len(ratios) != 1:
        return None
    ratio = next(iter(ratios))
    if any(large != small * ratio for small, large in zip(smaller, larger)):
        return None
    return ratio


def _build_fingerprint_pairs_v2(
    manifest: StructureGroupingInputManifestV2,
    prototype_evidence: tuple[PrototypeCaseEvidenceV2, ...],
) -> tuple[FingerprintPairEvidenceV2, ...]:
    by_key = {item.candidate_key: item for item in prototype_evidence}
    input_by_key = {item.candidate_key: item for item in manifest.case_inputs}
    matcher_kwargs = {
        "ltol": manifest.parameters.matcher_lattice_length_tolerance,
        "stol": manifest.parameters.matcher_site_tolerance,
        "angle_tol": manifest.parameters.matcher_angle_tolerance_degrees,
        "primitive_cell": manifest.parameters.matcher_primitive_cell,
        "scale": manifest.parameters.matcher_scale,
        "allow_subset": manifest.parameters.matcher_allow_subset,
    }
    direct_matcher = StructureMatcher(
        **matcher_kwargs,
        attempt_supercell=False,
    )
    supercell_matcher = StructureMatcher(
        **matcher_kwargs,
        attempt_supercell=True,
    )
    reduced: dict[str, Structure] = {}
    reduced_payloads: dict[str, NormalizedStructurePayloadV2] = {}
    for key, evidence in by_key.items():
        structure = _payload_to_structure_v2(
            evidence.canonicalized_structure.canonical_payload
        )
        try:
            primitive = structure.get_primitive_structure(
                tolerance=(
                    manifest.parameters.matcher_primitive_reduction_tolerance_angstrom
                ),
                use_site_props=False,
                reduce=True,
            )
        except Exception as exc:
            raise ValueError("bounded primitive-cell reduction failed") from exc
        if len(primitive) > manifest.parameters.maximum_compute_site_count:
            raise ValueError("reduced matcher cell exceeds the formal site bound")
        reduced[key] = primitive
        reduced_payloads[key] = _normalize_pymatgen_structure_v2(primitive)
    pairs: list[FingerprintPairEvidenceV2] = []
    keys = tuple(item.candidate_key for item in manifest.case_inputs)
    for left_index, left_key in enumerate(keys):
        for right_key in keys[left_index + 1 :]:
            left_input = input_by_key[left_key]
            right_input = input_by_key[right_key]
            forward = False
            reverse = False
            left_reduced = reduced[left_key]
            right_reduced = reduced[right_key]
            ratio: int | None = None
            if (
                left_input.preimage.dimensionality
                is not right_input.preimage.dimensionality
            ):
                stage = "DIMENSION_MISMATCH"
            else:
                ratio = _anonymous_supercell_ratio_v2(
                    reduced_payloads[left_key], reduced_payloads[right_key]
                )
                if ratio is None:
                    stage = "ANONYMOUS_STOICHIOMETRY_MISMATCH"
                elif ratio == 1:
                    stage = "DIRECT_REDUCED_CELL"
                    matcher = direct_matcher
                else:
                    limit = (
                        manifest.parameters.two_d_matcher_maximum_supercell_site_ratio
                        if left_input.preimage.dimensionality
                        is StructureDimensionalityV2.TWO_D
                        else manifest.parameters.three_d_matcher_maximum_supercell_site_ratio
                    )
                    if ratio > limit:
                        raise ValueError(
                            "potential anonymous supercell ratio exceeds the frozen dimensional bound"
                        )
                    stage = "BOUNDED_SUPERCELL"
                    matcher = supercell_matcher
                if ratio is not None:
                    try:
                        forward = bool(
                            matcher.fit_anonymous(left_reduced, right_reduced)
                        )
                        reverse = bool(
                            matcher.fit_anonymous(right_reduced, left_reduced)
                        )
                    except Exception as exc:
                        raise ValueError(
                            "anonymous structure matcher failed on a bounded pair"
                        ) from exc
            values: dict[str, object] = {
                "left_candidate_key": left_key,
                "left_input_sha256": left_input.input_sha256,
                "left_structure_sha256": left_input.preimage.structure_sha256,
                "right_candidate_key": right_key,
                "right_input_sha256": right_input.input_sha256,
                "right_structure_sha256": right_input.preimage.structure_sha256,
                "left_reduced_site_count": len(left_reduced),
                "right_reduced_site_count": len(right_reduced),
                "anonymous_supercell_factor": ratio,
                "matcher_stage": stage,
                "forward_fit_anonymous": forward,
                "reverse_fit_anonymous": reverse,
                "fit_anonymous": forward or reverse,
            }
            pairs.append(
                FingerprintPairEvidenceV2.model_validate(
                    _build_addressed(
                        FingerprintPairEvidenceV2,
                        id_field="pair_id",
                        sha_field="pair_sha256",
                        prefix="fingerprint-pair-v2",
                        values=values,
                    ).model_dump(mode="python", round_trip=True)
                )
            )
    return tuple(pairs)


def _build_fingerprint_components_v2(
    manifest: StructureGroupingInputManifestV2,
    pairs: tuple[FingerprintPairEvidenceV2, ...],
) -> tuple[FingerprintComponentEvidenceV2, ...]:
    keys = tuple(item.candidate_key for item in manifest.case_inputs)
    components_keys = _connected_components_v2(
        keys,
        (
            (item.left_candidate_key, item.right_candidate_key)
            for item in pairs
            if item.fit_anonymous
        ),
    )
    input_by_key = {item.candidate_key: item for item in manifest.case_inputs}
    components: list[FingerprintComponentEvidenceV2] = []
    for component_keys in components_keys:
        structure_sha256s = tuple(
            input_by_key[key].preimage.structure_sha256 for key in component_keys
        )
        values: dict[str, object] = {
            "canonical_group_key": deterministic_id(
                "structure-fp-group",
                {"structure_sha256s": tuple(sorted(structure_sha256s))},
            ),
            "candidate_keys": component_keys,
            "structure_sha256s": structure_sha256s,
        }
        components.append(
            FingerprintComponentEvidenceV2.model_validate(
                _build_addressed(
                    FingerprintComponentEvidenceV2,
                    id_field="component_id",
                    sha_field="component_sha256",
                    prefix="fingerprint-component-v2",
                    values=values,
                ).model_dump(mode="python", round_trip=True)
            )
        )
    return tuple(sorted(components, key=lambda item: item.candidate_keys))


def _build_output_root_v2(
    prototype_evidence: tuple[PrototypeCaseEvidenceV2, ...],
    prototype_components: tuple[PrototypeComponentEvidenceV2, ...],
    fingerprint_pairs: tuple[FingerprintPairEvidenceV2, ...],
    fingerprint_components: tuple[FingerprintComponentEvidenceV2, ...],
) -> StructureGroupingOutputRootV2:
    values: dict[str, object] = {
        "prototype_evidence_sha256": canonical_sha256(prototype_evidence),
        "prototype_components_sha256": canonical_sha256(prototype_components),
        "fingerprint_pair_matrix_sha256": canonical_sha256(fingerprint_pairs),
        "fingerprint_components_sha256": canonical_sha256(fingerprint_components),
    }
    draft = StructureGroupingOutputRootV2.model_construct(**values)
    digest = canonical_sha256(
        draft.model_dump(mode="python", exclude={"output_root_sha256"})
    )
    return StructureGroupingOutputRootV2(**values, output_root_sha256=digest)


def _build_formal_run_v2(
    manifest: StructureGroupingInputManifestV2,
    algorithms: tuple[StructureGroupingAlgorithmV2, StructureGroupingAlgorithmV2],
    output_root: StructureGroupingOutputRootV2,
    *,
    started_at: str,
    completed_at: str,
    started_monotonic_ns: int,
    completed_monotonic_ns: int,
) -> StructureGroupingFormalRunEvidenceV2:
    by_id = tuple(sorted(algorithms, key=lambda item: item.algorithm_id))
    values: dict[str, object] = {
        "input_manifest_id": manifest.manifest_id,
        "input_manifest_sha256": manifest.manifest_sha256,
        "input_root_sha256": manifest.input_root.input_root_sha256,
        "runtime_id": manifest.runtime_identity.runtime_id,
        "runtime_sha256": manifest.runtime_identity.runtime_sha256,
        "algorithm_ids": tuple(item.algorithm_id for item in by_id),
        "algorithm_sha256s": tuple(item.algorithm_sha256 for item in by_id),
        "output_root_sha256": output_root.output_root_sha256,
        "started_at": started_at,
        "completed_at": completed_at,
        "started_monotonic_ns": started_monotonic_ns,
        "completed_monotonic_ns": completed_monotonic_ns,
    }
    return StructureGroupingFormalRunEvidenceV2.model_validate(
        _build_addressed(
            StructureGroupingFormalRunEvidenceV2,
            id_field="formal_run_id",
            sha_field="formal_run_sha256",
            prefix="structure-formal-run-v2",
            values=values,
        ).model_dump(mode="python", round_trip=True)
    )


def _recompute_grouping_outputs_v2(
    manifest: StructureGroupingInputManifestV2,
) -> tuple[
    tuple[StructureGroupingAlgorithmV2, StructureGroupingAlgorithmV2],
    tuple[PrototypeCaseEvidenceV2, ...],
    tuple[PrototypeComponentEvidenceV2, ...],
    tuple[FingerprintPairEvidenceV2, ...],
    tuple[FingerprintComponentEvidenceV2, ...],
    StructureGroupingOutputRootV2,
]:
    if len(manifest.case_inputs) > manifest.parameters.maximum_compute_candidate_count:
        raise ValueError(
            "formal grouping input exceeds the 96-candidate Pilot V0 compute bound"
        )
    assert_structure_grouping_input_manifest_exact_replay_v2(manifest)
    for artifact in manifest.structure_artifacts:
        if len(artifact.payload.sites) > manifest.parameters.maximum_compute_site_count:
            raise ValueError("formal grouping input exceeds the 128-site compute bound")
        if len({item.species for item in artifact.payload.sites}) > (
            manifest.parameters.maximum_compute_distinct_species
        ):
            raise ValueError("formal grouping input exceeds the six-species compute bound")
    algorithms = _build_grouping_algorithms_v2(manifest)
    prototype = _build_prototype_evidence_v2(manifest)
    prototype_components = _build_prototype_components_v2(manifest, prototype)
    fingerprint_pairs = _build_fingerprint_pairs_v2(manifest, prototype)
    fingerprint_components = _build_fingerprint_components_v2(
        manifest, fingerprint_pairs
    )
    root = _build_output_root_v2(
        prototype,
        prototype_components,
        fingerprint_pairs,
        fingerprint_components,
    )
    return (
        algorithms,
        prototype,
        prototype_components,
        fingerprint_pairs,
        fingerprint_components,
        root,
    )


def _case_input_mechanical_projection_v2(
    case_input: StructureGroupingCaseInputV2,
) -> dict[str, object]:
    preimage = case_input.preimage
    dumped = preimage.model_dump(mode="python", round_trip=True)
    keys = (
        "source_catalog_sha256",
        "parent_label",
        "formula",
        "structure_sha256",
        "source_records",
        "target_class",
        "target_fermi_distance_max_e_v",
        "dimensionality",
        "frozen_request",
        "frozen_requirement_sha256",
        "hard_constraints",
        "soft_preferences",
        "forbidden_transformations",
        "seed_evidence",
        "primary_mechanism_stratum",
        "public_release_allowed",
    )
    projection = {key: dumped[key] for key in keys}
    projection["dimensionality"] = Dimensionality(preimage.dimensionality.value)
    return projection


def _final_case_mechanical_projection_v2(
    case: FlatBandBenchmarkCaseV1,
) -> dict[str, object]:
    return case.model_dump(
        mode="python",
        exclude={
            "schema_version",
            "case_id",
            "case_sha256",
            "leakage_group_ids",
            "scientific_conclusion",
        },
    )


def _component_group_by_candidate_v2(
    components: Sequence[PrototypeComponentEvidenceV2]
    | Sequence[FingerprintComponentEvidenceV2],
) -> dict[str, str]:
    result: dict[str, str] = {}
    for component in components:
        for candidate_key in component.candidate_keys:
            if candidate_key in result:
                raise ValueError("candidate appears in more than one structure component")
            result[candidate_key] = component.canonical_group_key
    return result


def _build_final_projection_outputs_v2(
    *,
    computation: StructureGroupingComputationReleaseV2,
    final_cases: tuple[FlatBandBenchmarkCaseV1, ...],
    final_cases_declared_at: str,
    created_at: str,
) -> tuple[
    tuple[FinalCaseProjectionV2, ...],
    tuple[StructureGroupingRunV2, StructureGroupingRunV2],
    tuple[StructureGroupingAssignmentV2, ...],
    str,
]:
    cases = tuple(
        sorted(
            (
                FlatBandBenchmarkCaseV1.model_validate(
                    item.model_dump(mode="python", round_trip=True)
                )
                for item in final_cases
            ),
            key=lambda item: item.case_id,
        )
    )
    if not cases or len({item.case_id for item in cases}) != len(cases):
        raise ValueError("final cases must be nonempty and identity-unique")
    inputs = computation.input_manifest.case_inputs
    candidates_by_projection: dict[str, list[StructureGroupingCaseInputV2]] = (
        defaultdict(list)
    )
    for case_input in inputs:
        candidates_by_projection[
            canonical_sha256(_case_input_mechanical_projection_v2(case_input))
        ].append(case_input)
    selected: dict[str, FlatBandBenchmarkCaseV1] = {}
    for case in cases:
        projection = _final_case_mechanical_projection_v2(case)
        digest = canonical_sha256(projection)
        matches = tuple(
            item
            for item in candidates_by_projection.get(digest, ())
            if _case_input_mechanical_projection_v2(item) == projection
            and item.candidate_key not in selected
        )
        if len(matches) != 1:
            raise ValueError(
                "final case does not have one unambiguous mechanical pre-group preimage"
            )
        case_input = matches[0]
        selected[case_input.candidate_key] = case
    if set(selected) != {item.candidate_key for item in inputs}:
        raise ValueError("final cases do not exactly project the candidate universe")

    projections: list[FinalCaseProjectionV2] = []
    for candidate_key in sorted(selected):
        case_input = next(item for item in inputs if item.candidate_key == candidate_key)
        case = selected[candidate_key]
        values: dict[str, object] = {
            "candidate_key": candidate_key,
            "pre_group_slot_key": case_input.pre_group_slot_key,
            "input_id": case_input.input_id,
            "input_sha256": case_input.input_sha256,
            "final_case_id": case.case_id,
            "final_case_sha256": case.case_sha256,
            "structure_sha256": case_input.preimage.structure_sha256,
        }
        projections.append(
            FinalCaseProjectionV2.model_validate(
                _build_addressed(
                    FinalCaseProjectionV2,
                    id_field="projection_id",
                    sha_field="projection_sha256",
                    prefix="structure-case-projection",
                    values=values,
                ).model_dump(mode="python", round_trip=True)
            )
        )

    algorithms = computation.grouping_algorithms
    universe_sha256 = structure_grouping_case_universe_sha256_v2(cases)
    runtime_environment_sha256 = canonical_sha256(
        {
            "runtime_sha256": computation.input_manifest.runtime_identity.runtime_sha256,
            "input_root_sha256": computation.input_manifest.input_root.input_root_sha256,
            "output_root_sha256": computation.output_root.output_root_sha256,
            "projection_kind": "POST_COMPUTE_FINAL_CASE_COMPATIBILITY_V2",
        }
    )
    runs: list[StructureGroupingRunV2] = []
    for algorithm in algorithms:
        values = {
            "axis": algorithm.axis,
            "algorithm_id": algorithm.algorithm_id,
            "algorithm_sha256": algorithm.algorithm_sha256,
            "input_case_universe_sha256": universe_sha256,
            "runtime_environment_sha256": runtime_environment_sha256,
            "started_at": final_cases_declared_at,
            "completed_at": created_at,
        }
        runs.append(
            StructureGroupingRunV2.model_validate(
                _build_addressed(
                    StructureGroupingRunV2,
                    id_field="grouping_run_id",
                    sha_field="grouping_run_sha256",
                    prefix="structure-group-run",
                    values=values,
                ).model_dump(mode="python", round_trip=True)
            )
        )
    ordered_runs = tuple(sorted(runs, key=lambda item: item.axis.value))
    run_by_axis = {item.axis: item for item in ordered_runs}
    group_by_axis = {
        LeakageAxis.STRUCTURE_PROTOTYPE: _component_group_by_candidate_v2(
            computation.prototype_components
        ),
        LeakageAxis.STRUCTURE_FINGERPRINT: _component_group_by_candidate_v2(
            computation.fingerprint_components
        ),
    }
    projection_by_case = {item.final_case_id: item for item in projections}
    assignments: list[StructureGroupingAssignmentV2] = []
    for algorithm in algorithms:
        run = run_by_axis[algorithm.axis]
        for case in cases:
            projection = projection_by_case[case.case_id]
            values = {
                "axis": algorithm.axis,
                "algorithm_id": algorithm.algorithm_id,
                "algorithm_sha256": algorithm.algorithm_sha256,
                "grouping_run_id": run.grouping_run_id,
                "grouping_run_sha256": run.grouping_run_sha256,
                "case_id": case.case_id,
                "case_sha256": case.case_sha256,
                "structure_sha256": case.structure_sha256,
                "canonical_group_key": group_by_axis[algorithm.axis][
                    projection.candidate_key
                ],
            }
            assignments.append(
                StructureGroupingAssignmentV2.model_validate(
                    _build_addressed(
                        StructureGroupingAssignmentV2,
                        id_field="assignment_id",
                        sha_field="assignment_sha256",
                        prefix="structure-group-assignment",
                        values=values,
                    ).model_dump(mode="python", round_trip=True)
                )
            )
    ordered_assignments = tuple(
        sorted(assignments, key=lambda item: (item.axis.value, item.case_id))
    )
    assignment_projection_sha256 = canonical_sha256(
        {
            "final_case_projections": tuple(projections),
            "grouping_runs": ordered_runs,
            "grouping_assignments": ordered_assignments,
        }
    )
    return (
        tuple(projections),
        ordered_runs,  # type: ignore[return-value]
        ordered_assignments,
        assignment_projection_sha256,
    )


def _assert_final_projection_closure_v2(
    release: StructureGroupingPrivateEvidenceReleaseV2,
) -> None:
    expected = _build_final_projection_outputs_v2(
        computation=release.computation,
        final_cases=release.final_cases,
        final_cases_declared_at=release.final_cases_declared_at,
        created_at=release.created_at,
    )
    actual = (
        release.final_case_projections,
        release.grouping_runs,
        release.grouping_assignments,
        release.assignment_projection_sha256,
    )
    if actual != expected:
        raise ValueError("final case projection does not mechanically replay")


def _assert_source_policy_projection_closure_v2(
    release: StructureGroupingPrivateEvidenceReleaseV2,
) -> None:
    policies = release.source_policy_attestations
    policy_keys = tuple(
        (item.case_id, item.source_id, item.source_record_id) for item in policies
    )
    if policy_keys != tuple(sorted(set(policy_keys))):
        raise ValueError("source policy attestations must be key-sorted and unique")
    expected_keys = {
        (case.case_id, record.source_id, record.source_record_id)
        for case in release.final_cases
        for record in case.source_records
    }
    if set(policy_keys) != expected_keys:
        raise ValueError("source policy attestations do not exactly cover final cases")
    policy_by_key = {
        (item.case_id, item.source_id, item.source_record_id): item
        for item in policies
    }
    for case in release.final_cases:
        case_policies = tuple(
            item for item in policies if item.case_id == case.case_id
        )
        assert_case_source_policy_v2(case=case, attestations=case_policies)

    input_by_key = {
        item.candidate_key: item
        for item in release.computation.input_manifest.case_inputs
    }
    artifact_by_id = {
        item.artifact_id: item
        for item in release.computation.input_manifest.structure_artifacts
    }
    case_by_id = {item.case_id: item for item in release.final_cases}
    for projection in release.final_case_projections:
        case_input = input_by_key[projection.candidate_key]
        artifact = artifact_by_id[case_input.preimage.structure_artifact_id]
        case = case_by_id[projection.final_case_id]
        provenance = artifact.provenance
        policy = policy_by_key.get(
            (case.case_id, provenance.source_id, provenance.source_record_id)
        )
        if policy is None or (
            policy.case_sha256,
            policy.source_record_raw_sha256,
        ) != (
            case.case_sha256,
            provenance.source_record_raw_sha256,
        ):
            raise ValueError(
                "raw structure provenance does not join its exact source attestation"
            )
        if SourceUseRole.STRUCTURE not in policy.usage_roles:
            raise ValueError("raw structure provenance lacks a STRUCTURE usage role")


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
    cache_key = (validated.manifest_sha256, expected_runtime.runtime_sha256)
    if _exact_replay_cache_hit_v2(_INPUT_REPLAY_SUCCESS_CACHE, cache_key):
        return
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
    _remember_exact_replay_success_v2(_INPUT_REPLAY_SUCCESS_CACHE, cache_key)


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


def assert_structure_grouping_computation_exact_replay_v2(
    computation: StructureGroupingComputationReleaseV2,
) -> None:
    """Recompute every symmetry, matcher, component, and root field from raw input."""

    validated = StructureGroupingComputationReleaseV2.model_validate(
        computation.model_dump(mode="python", round_trip=True)
    )
    # The process-local success memo is safe only after current-runtime/raw
    # replay and a fresh content-address validation of the immutable object.
    assert_structure_grouping_input_manifest_exact_replay_v2(
        validated.input_manifest
    )
    cache_key = (
        validated.computation_sha256,
        validated.input_manifest.runtime_identity.runtime_sha256,
    )
    if _exact_replay_cache_hit_v2(
        _COMPUTATION_REPLAY_SUCCESS_CACHE, cache_key
    ):
        return
    (
        algorithms,
        prototype,
        prototype_components,
        fingerprint_pairs,
        fingerprint_components,
        output_root,
    ) = _recompute_grouping_outputs_v2(validated.input_manifest)
    if validated.grouping_algorithms != algorithms:
        raise ValueError("computed grouping algorithms do not replay exactly")
    if validated.prototype_evidence != prototype:
        raise ValueError("prototype threshold evidence does not replay exactly")
    if validated.prototype_components != prototype_components:
        raise ValueError("prototype components do not replay exactly")
    if validated.fingerprint_pair_evidence != fingerprint_pairs:
        raise ValueError("anonymous fingerprint pair matrix does not replay exactly")
    if validated.fingerprint_components != fingerprint_components:
        raise ValueError("anonymous fingerprint components do not replay exactly")
    if validated.output_root != output_root:
        raise ValueError("structure output root does not replay exactly")
    formal_run = _build_formal_run_v2(
        validated.input_manifest,
        algorithms,
        output_root,
        started_at=validated.formal_run.started_at,
        completed_at=validated.formal_run.completed_at,
        started_monotonic_ns=validated.formal_run.started_monotonic_ns,
        completed_monotonic_ns=validated.formal_run.completed_monotonic_ns,
    )
    if validated.formal_run != formal_run:
        raise ValueError("formal run evidence does not replay exactly")
    values: dict[str, object] = {
        "input_manifest": validated.input_manifest,
        "grouping_algorithms": algorithms,
        "prototype_evidence": prototype,
        "prototype_components": prototype_components,
        "fingerprint_pair_evidence": fingerprint_pairs,
        "fingerprint_components": fingerprint_components,
        "output_root": output_root,
        "formal_run": formal_run,
        "created_at": validated.created_at,
    }
    expected = StructureGroupingComputationReleaseV2.model_validate(
        _build_addressed(
            StructureGroupingComputationReleaseV2,
            id_field="computation_id",
            sha_field="computation_sha256",
            prefix="structure-computation-v2",
            values=values,
        ).model_dump(mode="python", round_trip=True)
    )
    if validated != expected:
        raise ValueError("structure computation release does not replay exactly")
    _remember_exact_replay_success_v2(
        _COMPUTATION_REPLAY_SUCCESS_CACHE, cache_key
    )


def run_structure_grouping_computation_v2(
    *,
    input_manifest: StructureGroupingInputManifestV2,
    started_at: str | None = None,
    completed_at: str | None = None,
    started_monotonic_ns: int | None = None,
    completed_monotonic_ns: int | None = None,
    created_at: str | None = None,
) -> StructureGroupingComputationReleaseV2:
    """Run the frozen local computation over an exact pre-group manifest."""

    manifest = StructureGroupingInputManifestV2.model_validate(
        input_manifest.model_dump(mode="python", round_trip=True)
    )
    actual_started_at = started_at or _now_rfc3339()
    actual_started_ns = (
        time.monotonic_ns()
        if started_monotonic_ns is None
        else started_monotonic_ns
    )
    _assert_computation_chronology_v2(
        manifest=manifest,
        started_at=actual_started_at,
        started_monotonic_ns=actual_started_ns,
        completed_at=completed_at,
        completed_monotonic_ns=completed_monotonic_ns,
        created_at=created_at,
    )
    (
        algorithms,
        prototype,
        prototype_components,
        fingerprint_pairs,
        fingerprint_components,
        output_root,
    ) = _recompute_grouping_outputs_v2(manifest)
    actual_completed_at = completed_at or _now_rfc3339()
    actual_completed_ns = (
        time.monotonic_ns()
        if completed_monotonic_ns is None
        else completed_monotonic_ns
    )
    actual_created_at = created_at or _now_rfc3339()
    _assert_computation_chronology_v2(
        manifest=manifest,
        started_at=actual_started_at,
        started_monotonic_ns=actual_started_ns,
        completed_at=actual_completed_at,
        completed_monotonic_ns=actual_completed_ns,
        created_at=actual_created_at,
    )
    formal_run = _build_formal_run_v2(
        manifest,
        algorithms,
        output_root,
        started_at=actual_started_at,
        completed_at=actual_completed_at,
        started_monotonic_ns=actual_started_ns,
        completed_monotonic_ns=actual_completed_ns,
    )
    values: dict[str, object] = {
        "input_manifest": manifest,
        "grouping_algorithms": algorithms,
        "prototype_evidence": prototype,
        "prototype_components": prototype_components,
        "fingerprint_pair_evidence": fingerprint_pairs,
        "fingerprint_components": fingerprint_components,
        "output_root": output_root,
        "formal_run": formal_run,
        "created_at": actual_created_at,
    }
    release = StructureGroupingComputationReleaseV2.model_validate(
        _build_addressed(
            StructureGroupingComputationReleaseV2,
            id_field="computation_id",
            sha_field="computation_sha256",
            prefix="structure-computation-v2",
            values=values,
        ).model_dump(mode="python", round_trip=True)
    )
    assert_structure_grouping_computation_exact_replay_v2(release)
    return release


def assert_structure_grouping_release_exact_replay_v2(
    release: StructureGroupingPrivateEvidenceReleaseV2,
) -> None:
    """Replay raw computation and the post-compute final-case projection."""

    validated = StructureGroupingPrivateEvidenceReleaseV2.model_validate(
        release.model_dump(mode="python", round_trip=True)
    )
    assert_structure_grouping_computation_exact_replay_v2(validated.computation)
    cache_key = (
        validated.release_sha256,
        validated.computation.input_manifest.runtime_identity.runtime_sha256,
    )
    if _exact_replay_cache_hit_v2(
        _PRIVATE_RELEASE_REPLAY_SUCCESS_CACHE, cache_key
    ):
        return
    _assert_final_projection_closure_v2(validated)
    values: dict[str, object] = {
        "computation": validated.computation,
        "final_cases_declared_at": validated.final_cases_declared_at,
        "created_at": validated.created_at,
        "final_cases": validated.final_cases,
        "source_policy_attestations": validated.source_policy_attestations,
        "final_case_projections": validated.final_case_projections,
        "grouping_algorithms": validated.grouping_algorithms,
        "grouping_runs": validated.grouping_runs,
        "grouping_assignments": validated.grouping_assignments,
        "assignment_projection_sha256": validated.assignment_projection_sha256,
    }
    expected = StructureGroupingPrivateEvidenceReleaseV2.model_validate(
        _build_addressed(
            StructureGroupingPrivateEvidenceReleaseV2,
            id_field="release_id",
            sha_field="release_sha256",
            prefix="structure-private-release-v2",
            values=values,
        ).model_dump(mode="python", round_trip=True)
    )
    if validated != expected:
        raise ValueError("private structure release does not replay exactly")
    _remember_exact_replay_success_v2(
        _PRIVATE_RELEASE_REPLAY_SUCCESS_CACHE, cache_key
    )


def finalize_structure_grouping_release_v2(
    *,
    computation: StructureGroupingComputationReleaseV2,
    final_cases: Iterable[FlatBandBenchmarkCaseV1],
    source_policy_attestations: Iterable[CaseSourcePolicyAttestationV2],
    final_cases_declared_at: str | None = None,
    created_at: str | None = None,
) -> StructureGroupingPrivateEvidenceReleaseV2:
    """Mechanically project selected post-compute cases; no group key is accepted."""

    validated_computation = StructureGroupingComputationReleaseV2.model_validate(
        computation.model_dump(mode="python", round_trip=True)
    )
    assert_structure_grouping_computation_exact_replay_v2(validated_computation)
    cases = tuple(
        sorted(
            (
                FlatBandBenchmarkCaseV1.model_validate(
                    item.model_dump(mode="python", round_trip=True)
                )
                for item in final_cases
            ),
            key=lambda item: item.case_id,
        )
    )
    policies = tuple(
        sorted(
            (
                CaseSourcePolicyAttestationV2.model_validate(
                    item.model_dump(mode="python", round_trip=True)
                )
                for item in source_policy_attestations
            ),
            key=lambda item: (
                item.case_id,
                item.source_id,
                item.source_record_id,
            ),
        )
    )
    declared = final_cases_declared_at or _now_rfc3339()
    created = created_at or _now_rfc3339()
    projections, runs, assignments, projection_sha256 = (
        _build_final_projection_outputs_v2(
            computation=validated_computation,
            final_cases=cases,
            final_cases_declared_at=declared,
            created_at=created,
        )
    )
    values: dict[str, object] = {
        "computation": validated_computation,
        "final_cases_declared_at": declared,
        "created_at": created,
        "final_cases": cases,
        "source_policy_attestations": policies,
        "final_case_projections": projections,
        "grouping_algorithms": validated_computation.grouping_algorithms,
        "grouping_runs": runs,
        "grouping_assignments": assignments,
        "assignment_projection_sha256": projection_sha256,
    }
    release = StructureGroupingPrivateEvidenceReleaseV2.model_validate(
        _build_addressed(
            StructureGroupingPrivateEvidenceReleaseV2,
            id_field="release_id",
            sha_field="release_sha256",
            prefix="structure-private-release-v2",
            values=values,
        ).model_dump(mode="python", round_trip=True)
    )
    assert_structure_grouping_release_exact_replay_v2(release)
    return release


def _cross_owner_component_count_v2(
    components: Sequence[PrototypeComponentEvidenceV2]
    | Sequence[FingerprintComponentEvidenceV2],
    owner_by_candidate: dict[str, str],
) -> int:
    return sum(
        len({owner_by_candidate[key] for key in component.candidate_keys}) > 1
        for component in components
    )


def _assert_union_replay_model_closure_v2(
    release: StructureGroupingUnionReplayReleaseV2,
) -> None:
    member_keys = tuple(
        (item.owner_id, item.member_release_id, item.member_ref_id)
        for item in release.member_refs
    )
    if member_keys != tuple(sorted(member_keys)):
        raise ValueError("union member refs must be owner-sorted")
    if len({item.owner_id for item in release.member_refs}) != len(
        release.member_refs
    ):
        raise ValueError("union replay repeats an owner ID")
    if len({item.member_release_id for item in release.member_refs}) != len(
        release.member_refs
    ):
        raise ValueError("union replay aliases one member release")
    verified = datetime.fromisoformat(release.verified_at.replace("Z", "+00:00"))
    union_sealed = datetime.fromisoformat(
        release.union_input_sealed_at.replace("Z", "+00:00")
    )
    if any(
        union_sealed
        <= datetime.fromisoformat(
            item.member_release_created_at.replace("Z", "+00:00")
        )
        for item in release.member_refs
    ):
        raise ValueError("union input seal does not postdate every member release")
    if release.member_release_set_sha256 != canonical_sha256(release.member_refs):
        raise ValueError("union member release set SHA does not replay")

    projection_keys = tuple(
        (item.candidate_key, item.owner_id, item.owner_projection_id)
        for item in release.candidate_owner_projection
    )
    if projection_keys != tuple(sorted(projection_keys)) or len(
        {item.candidate_key for item in release.candidate_owner_projection}
    ) != len(release.candidate_owner_projection):
        raise ValueError("union candidate-owner projection must be sorted and unique")
    member_by_owner = {item.owner_id: item for item in release.member_refs}
    expected_owner_rows = {
        (candidate_key, item.owner_id, item.member_release_id, item.member_release_sha256)
        for item in release.member_refs
        for candidate_key in item.candidate_keys
    }
    observed_owner_rows = {
        (
            item.candidate_key,
            item.owner_id,
            item.member_release_id,
            item.member_release_sha256,
        )
        for item in release.candidate_owner_projection
    }
    if observed_owner_rows != expected_owner_rows or len(observed_owner_rows) != sum(
        len(item.candidate_keys) for item in release.member_refs
    ):
        raise ValueError("union candidate-owner projection does not cover member candidates")
    for item in release.candidate_owner_projection:
        member = member_by_owner.get(item.owner_id)
        if member is None or (
            item.member_release_id,
            item.member_release_sha256,
        ) != (
            member.member_release_id,
            member.member_release_sha256,
        ):
            raise ValueError("union candidate owner references a foreign member")
    if release.candidate_owner_projection_sha256 != canonical_sha256(
        release.candidate_owner_projection
    ):
        raise ValueError("union candidate-owner projection SHA does not replay")

    computation = release.merged_computation
    input_by_key = {
        item.candidate_key: item for item in computation.input_manifest.case_inputs
    }
    if set(input_by_key) != {
        item.candidate_key for item in release.candidate_owner_projection
    }:
        raise ValueError("merged computation differs from union candidate owners")
    for item in release.candidate_owner_projection:
        case_input = input_by_key[item.candidate_key]
        if (
            item.input_id,
            item.input_sha256,
            item.structure_sha256,
        ) != (
            case_input.input_id,
            case_input.input_sha256,
            case_input.preimage.structure_sha256,
        ):
            raise ValueError("union candidate owner is cross-wired to merged input")
    if (
        release.merged_input_manifest_sha256,
        release.merged_input_root_sha256,
        release.merged_output_root_sha256,
    ) != (
        computation.input_manifest.manifest_sha256,
        computation.input_manifest.input_root.input_root_sha256,
        computation.output_root.output_root_sha256,
    ):
        raise ValueError("union roots differ from the merged computation")
    formal_run = computation.formal_run
    started = datetime.fromisoformat(formal_run.started_at.replace("Z", "+00:00"))
    completed = datetime.fromisoformat(
        formal_run.completed_at.replace("Z", "+00:00")
    )
    computation_created = datetime.fromisoformat(
        computation.created_at.replace("Z", "+00:00")
    )
    if computation.input_manifest.sealed_at != release.union_input_sealed_at:
        raise ValueError("merged manifest seal differs from union release chronology")
    if not (
        union_sealed < started <= completed <= computation_created < verified
    ):
        raise ValueError("union seal/run/verification chronology is inverted")
    if not (
        computation.input_manifest.sealed_monotonic_ns
        < formal_run.started_monotonic_ns
        <= formal_run.completed_monotonic_ns
    ):
        raise ValueError("union monotonic seal/run chronology is inverted")
    owner_by_candidate = {
        item.candidate_key: item.owner_id
        for item in release.candidate_owner_projection
    }
    if _cross_owner_component_count_v2(
        computation.prototype_components, owner_by_candidate
    ) != release.cross_owner_prototype_component_count:
        raise ValueError("union prototype cross-owner count does not replay")
    if _cross_owner_component_count_v2(
        computation.fingerprint_components, owner_by_candidate
    ) != release.cross_owner_fingerprint_component_count:
        raise ValueError("union fingerprint cross-owner count does not replay")


def _build_structure_grouping_union_replay_release_v2(
    *,
    members: Iterable[
        tuple[str, StructureGroupingPrivateEvidenceReleaseV2]
    ],
    union_input_sealed_at: str | None,
    union_input_sealed_monotonic_ns: int | None,
    run_started_at: str | None,
    run_completed_at: str | None,
    run_started_monotonic_ns: int | None,
    run_completed_monotonic_ns: int | None,
    computation_created_at: str | None,
    verified_at: str | None,
) -> StructureGroupingUnionReplayReleaseV2:
    raw_members = tuple(members)
    if len(raw_members) < 2:
        raise ValueError("union replay requires at least two owned member releases")
    if len(raw_members) > 32:
        raise ValueError("union replay accepts at most 32 member releases")
    candidate_count = sum(
        len(release.computation.input_manifest.case_inputs)
        for _owner_id, release in raw_members
    )
    if candidate_count > 96:
        raise ValueError(
            "union replay exceeds the 96-candidate Pilot V0 compute bound"
        )
    ordered_members = tuple(
        sorted(
            (
                (
                    owner_id,
                    StructureGroupingPrivateEvidenceReleaseV2.model_validate(
                        release.model_dump(mode="python", round_trip=True)
                    ),
                )
                for owner_id, release in raw_members
            ),
            key=lambda item: (item[0], item[1].release_id),
        )
    )
    if len({owner for owner, _ in ordered_members}) != len(ordered_members):
        raise ValueError("union replay repeats an owner ID")
    if len({release.release_id for _, release in ordered_members}) != len(
        ordered_members
    ):
        raise ValueError("union replay aliases one member release")

    clock_overrides = (
        union_input_sealed_at,
        union_input_sealed_monotonic_ns,
        run_started_at,
        run_completed_at,
        run_started_monotonic_ns,
        run_completed_monotonic_ns,
        computation_created_at,
        verified_at,
    )
    if any(value is not None for value in clock_overrides) and not all(
        value is not None for value in clock_overrides
    ):
        raise ValueError("union replay frozen clock override must be complete")
    frozen_clock = all(value is not None for value in clock_overrides)
    if frozen_clock:
        assert union_input_sealed_at is not None
        assert union_input_sealed_monotonic_ns is not None
        assert run_started_at is not None
        assert run_completed_at is not None
        assert run_started_monotonic_ns is not None
        assert run_completed_monotonic_ns is not None
        assert computation_created_at is not None
        assert verified_at is not None
        for value in (
            union_input_sealed_at,
            run_started_at,
            run_completed_at,
            computation_created_at,
            verified_at,
        ):
            _require_rfc3339(value)
        frozen_times = tuple(
            datetime.fromisoformat(value.replace("Z", "+00:00"))
            for value in (
                union_input_sealed_at,
                run_started_at,
                run_completed_at,
                computation_created_at,
                verified_at,
            )
        )
        latest_member = max(
            datetime.fromisoformat(release.created_at.replace("Z", "+00:00"))
            for _owner, release in ordered_members
        )
        if not (
            latest_member
            < frozen_times[0]
            < frozen_times[1]
            <= frozen_times[2]
            <= frozen_times[3]
            < frozen_times[4]
        ):
            raise ValueError("union frozen clock chronology is inverted")
        if not (
            union_input_sealed_monotonic_ns
            < run_started_monotonic_ns
            <= run_completed_monotonic_ns
        ):
            raise ValueError("union frozen monotonic clock chronology is inverted")

    for _owner, release in ordered_members:
        assert_structure_grouping_release_exact_replay_v2(release)
    reference_manifest = ordered_members[0][1].computation.input_manifest
    for _owner, release in ordered_members[1:]:
        manifest = release.computation.input_manifest
        if (
            manifest.parameters,
            manifest.runtime_identity,
        ) != (
            reference_manifest.parameters,
            reference_manifest.runtime_identity,
        ):
            raise ValueError("cross-release grouping parameters or runtime differ")

    latest_member_created_at = max(
        (release.created_at for _owner, release in ordered_members),
        key=lambda value: datetime.fromisoformat(value.replace("Z", "+00:00")),
    )
    actual_union_sealed_at = (
        union_input_sealed_at
        if frozen_clock
        else _now_strictly_after_v2(latest_member_created_at)
    )
    actual_union_sealed_monotonic_ns = (
        union_input_sealed_monotonic_ns
        if frozen_clock
        else time.monotonic_ns()
    )
    assert actual_union_sealed_at is not None
    assert actual_union_sealed_monotonic_ns is not None

    member_refs: list[StructureGroupingUnionMemberRefV2] = []
    owner_by_candidate: dict[str, str] = {}
    release_by_candidate: dict[
        str, StructureGroupingPrivateEvidenceReleaseV2
    ] = {}
    case_inputs: list[StructureGroupingCaseInputV2] = []
    artifacts_by_id: dict[str, NormalizedStructureArtifactV2] = {}
    for owner_id, release in ordered_members:
        manifest = release.computation.input_manifest
        candidate_keys = tuple(item.candidate_key for item in manifest.case_inputs)
        ref_values: dict[str, object] = {
            "owner_id": owner_id,
            "member_release_id": release.release_id,
            "member_release_sha256": release.release_sha256,
            "member_release_created_at": release.created_at,
            "member_computation_id": release.computation.computation_id,
            "member_computation_sha256": release.computation.computation_sha256,
            "member_input_manifest_id": manifest.manifest_id,
            "member_input_manifest_sha256": manifest.manifest_sha256,
            "candidate_keys": candidate_keys,
        }
        member_refs.append(
            StructureGroupingUnionMemberRefV2.model_validate(
                _build_addressed(
                    StructureGroupingUnionMemberRefV2,
                    id_field="member_ref_id",
                    sha_field="member_ref_sha256",
                    prefix="structure-union-member-v2",
                    values=ref_values,
                ).model_dump(mode="python", round_trip=True)
            )
        )
        for case_input in manifest.case_inputs:
            previous = owner_by_candidate.setdefault(
                case_input.candidate_key, owner_id
            )
            if previous != owner_id:
                raise ValueError("one pre-group candidate appears under multiple owners")
            release_by_candidate[case_input.candidate_key] = release
            case_inputs.append(case_input)
        for artifact in manifest.structure_artifacts:
            previous_artifact = artifacts_by_id.setdefault(
                artifact.artifact_id, artifact
            )
            if previous_artifact != artifact:
                raise ValueError("one structure artifact ID has conflicting content")

    union_manifest = seal_structure_grouping_input_manifest_v2(
        case_inputs=case_inputs,
        structure_artifacts=artifacts_by_id.values(),
        parameters=reference_manifest.parameters,
        sealed_at=actual_union_sealed_at,
        sealed_monotonic_ns=actual_union_sealed_monotonic_ns,
    )
    actual_run_started_at = (
        run_started_at
        if frozen_clock
        else _now_strictly_after_v2(actual_union_sealed_at)
    )
    actual_run_started_monotonic_ns = (
        run_started_monotonic_ns
        if frozen_clock
        else max(time.monotonic_ns(), actual_union_sealed_monotonic_ns + 1)
    )
    assert actual_run_started_at is not None
    assert actual_run_started_monotonic_ns is not None
    merged_computation = run_structure_grouping_computation_v2(
        input_manifest=union_manifest,
        started_at=actual_run_started_at,
        completed_at=run_completed_at if frozen_clock else None,
        started_monotonic_ns=actual_run_started_monotonic_ns,
        completed_monotonic_ns=(
            run_completed_monotonic_ns if frozen_clock else None
        ),
        created_at=computation_created_at if frozen_clock else None,
    )
    input_by_key = {
        item.candidate_key: item for item in union_manifest.case_inputs
    }
    owner_rows: list[StructureGroupingUnionCandidateOwnerV2] = []
    for candidate_key in sorted(owner_by_candidate):
        case_input = input_by_key[candidate_key]
        member = release_by_candidate[candidate_key]
        owner_values: dict[str, object] = {
            "candidate_key": candidate_key,
            "input_id": case_input.input_id,
            "input_sha256": case_input.input_sha256,
            "structure_sha256": case_input.preimage.structure_sha256,
            "owner_id": owner_by_candidate[candidate_key],
            "member_release_id": member.release_id,
            "member_release_sha256": member.release_sha256,
        }
        owner_rows.append(
            StructureGroupingUnionCandidateOwnerV2.model_validate(
                _build_addressed(
                    StructureGroupingUnionCandidateOwnerV2,
                    id_field="owner_projection_id",
                    sha_field="owner_projection_sha256",
                    prefix="structure-union-owner-v2",
                    values=owner_values,
                ).model_dump(mode="python", round_trip=True)
            )
        )
    prototype_cross_owner = _cross_owner_component_count_v2(
        merged_computation.prototype_components, owner_by_candidate
    )
    fingerprint_cross_owner = _cross_owner_component_count_v2(
        merged_computation.fingerprint_components, owner_by_candidate
    )
    if prototype_cross_owner:
        raise ValueError(
            "cross-release STRUCTURE_PROTOTYPE component detected by raw union replay"
        )
    if fingerprint_cross_owner:
        raise ValueError(
            "cross-release STRUCTURE_FINGERPRINT component detected by raw union replay"
        )
    actual_verified_at = (
        verified_at
        if frozen_clock
        else _now_strictly_after_v2(merged_computation.created_at)
    )
    assert actual_verified_at is not None
    refs = tuple(member_refs)
    projections = tuple(owner_rows)
    values: dict[str, object] = {
        "member_refs": refs,
        "candidate_owner_projection": projections,
        "merged_computation": merged_computation,
        "member_release_set_sha256": canonical_sha256(refs),
        "candidate_owner_projection_sha256": canonical_sha256(projections),
        "merged_input_manifest_sha256": union_manifest.manifest_sha256,
        "merged_input_root_sha256": union_manifest.input_root.input_root_sha256,
        "merged_output_root_sha256": merged_computation.output_root.output_root_sha256,
        "union_input_sealed_at": actual_union_sealed_at,
        "cross_owner_prototype_component_ids": (),
        "cross_owner_fingerprint_component_ids": (),
        "cross_owner_prototype_component_count": 0,
        "cross_owner_fingerprint_component_count": 0,
        "verified_at": actual_verified_at,
    }
    union_release = StructureGroupingUnionReplayReleaseV2.model_validate(
        _build_addressed(
            StructureGroupingUnionReplayReleaseV2,
            id_field="union_release_id",
            sha_field="union_release_sha256",
            prefix="structure-union-replay-v2",
            values=values,
        ).model_dump(mode="python", round_trip=True)
    )
    union_cache_key = (
        union_release.union_release_sha256,
        union_release.merged_computation.input_manifest.runtime_identity.runtime_sha256,
        canonical_sha256(
            tuple(
                sorted(
                    (owner, member.release_id, member.release_sha256)
                    for owner, member in ordered_members
                )
            )
        ),
    )
    _remember_exact_replay_success_v2(
        _UNION_REPLAY_SUCCESS_CACHE, union_cache_key
    )
    return union_release


def build_structure_grouping_union_replay_release_v2(
    *,
    members: Iterable[
        tuple[str, StructureGroupingPrivateEvidenceReleaseV2]
    ],
    union_input_sealed_at: str | None = None,
    union_input_sealed_monotonic_ns: int | None = None,
    run_started_at: str | None = None,
    run_completed_at: str | None = None,
    run_started_monotonic_ns: int | None = None,
    run_completed_monotonic_ns: int | None = None,
    computation_created_at: str | None = None,
    verified_at: str | None = None,
) -> StructureGroupingUnionReplayReleaseV2:
    """Build a durable private zero-cross-owner proof from exact member releases.

    Clock overrides are an all-or-none exact-replay seam.  Normal callers omit
    them and receive actual UTC/monotonic observations; verifiers reuse every
    frozen observation while recalculating all scientific outputs.
    """

    return _build_structure_grouping_union_replay_release_v2(
        members=members,
        union_input_sealed_at=union_input_sealed_at,
        union_input_sealed_monotonic_ns=union_input_sealed_monotonic_ns,
        run_started_at=run_started_at,
        run_completed_at=run_completed_at,
        run_started_monotonic_ns=run_started_monotonic_ns,
        run_completed_monotonic_ns=run_completed_monotonic_ns,
        computation_created_at=computation_created_at,
        verified_at=verified_at,
    )


def assert_structure_grouping_union_replay_release_exact_v2(
    *,
    release: StructureGroupingUnionReplayReleaseV2,
    members: Iterable[
        tuple[str, StructureGroupingPrivateEvidenceReleaseV2]
    ],
) -> None:
    """Rebuild a union proof from the supplied real members and exact-compare."""

    validated = StructureGroupingUnionReplayReleaseV2.model_validate(
        release.model_dump(mode="python", round_trip=True)
    )
    supplied = tuple(
        (
            owner,
            StructureGroupingPrivateEvidenceReleaseV2.model_validate(
                item.model_dump(mode="python", round_trip=True)
            ),
        )
        for owner, item in members
    )
    supplied_keys = tuple(
        sorted((owner, item.release_id, item.release_sha256) for owner, item in supplied)
    )
    expected_keys = tuple(
        sorted(
            (item.owner_id, item.member_release_id, item.member_release_sha256)
            for item in validated.member_refs
        )
    )
    if supplied_keys != expected_keys:
        raise ValueError("union verifier member/owner set is missing, extra, or foreign")
    for _owner, member in supplied:
        assert_structure_grouping_release_exact_replay_v2(member)
    cache_key = (
        validated.union_release_sha256,
        validated.merged_computation.input_manifest.runtime_identity.runtime_sha256,
        canonical_sha256(supplied_keys),
    )
    if _exact_replay_cache_hit_v2(_UNION_REPLAY_SUCCESS_CACHE, cache_key):
        return
    expected = _build_structure_grouping_union_replay_release_v2(
        members=supplied,
        union_input_sealed_at=validated.union_input_sealed_at,
        union_input_sealed_monotonic_ns=(
            validated.merged_computation.input_manifest.sealed_monotonic_ns
        ),
        run_started_at=validated.merged_computation.formal_run.started_at,
        run_completed_at=validated.merged_computation.formal_run.completed_at,
        run_started_monotonic_ns=(
            validated.merged_computation.formal_run.started_monotonic_ns
        ),
        run_completed_monotonic_ns=(
            validated.merged_computation.formal_run.completed_monotonic_ns
        ),
        computation_created_at=validated.merged_computation.created_at,
        verified_at=validated.verified_at,
    )
    if validated != expected:
        raise ValueError("structure union replay release does not replay exactly")
    _remember_exact_replay_success_v2(_UNION_REPLAY_SUCCESS_CACHE, cache_key)


def assert_structure_grouping_releases_disjoint_v2(
    *releases: StructureGroupingPrivateEvidenceReleaseV2,
) -> StructureGroupingUnionReplayReleaseV2:
    """Compatibility wrapper over the canonical content-addressed union builder."""

    members = tuple(
        (
            deterministic_id("structure-union-owner", {"release_id": item.release_id}),
            item,
        )
        for item in releases
    )
    union = build_structure_grouping_union_replay_release_v2(members=members)
    assert_structure_grouping_union_replay_release_exact_v2(
        release=union,
        members=members,
    )
    return union


__all__ = [
    "READINESS",
    "AperiodicAxisEvidenceV2",
    "CanonicalizedStructureEvidenceV2",
    "FinalCaseProjectionV2",
    "FingerprintComponentEvidenceV2",
    "FingerprintPairEvidenceV2",
    "NormalizedStructureArtifactV2",
    "NormalizedStructurePayloadV2",
    "PreGroupCandidatePreimageV2",
    "RawStructureArtifactV2",
    "StructureDimensionalityV2",
    "StructureGroupingCaseInputV2",
    "StructureGroupingComputationReleaseV2",
    "StructureGroupingInputManifestV2",
    "StructureGroupingOutputRootV2",
    "StructureGroupingParametersV2",
    "StructureGroupingPrivateEvidenceReleaseV2",
    "StructureGroupingReadiness",
    "StructureGroupingUnionCandidateOwnerV2",
    "StructureGroupingUnionMemberRefV2",
    "StructureGroupingUnionReplayReleaseV2",
    "assert_structure_grouping_input_manifest_exact_replay_v2",
    "assert_structure_grouping_computation_exact_replay_v2",
    "assert_structure_grouping_release_exact_replay_v2",
    "assert_structure_grouping_releases_disjoint_v2",
    "assert_structure_grouping_union_replay_release_exact_v2",
    "build_aperiodic_axis_evidence_v2",
    "build_raw_structure_artifact_v2",
    "build_structure_grouping_case_input_v2",
    "build_structure_grouping_union_replay_release_v2",
    "finalize_structure_grouping_release_v2",
    "normalize_raw_structure_artifact_v2",
    "run_structure_grouping_computation_v2",
    "seal_structure_grouping_input_manifest_v2",
]
