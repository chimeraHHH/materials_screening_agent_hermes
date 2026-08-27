"""Pre-case anonymous structure grouping for Fermi-qualified calibration seeds."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Annotated, Literal

from pydantic import Field, model_validator
from pymatgen.analysis.structure_matcher import StructureMatcher

from material_agent.inspiration.models import (
    Sha256,
    StrictModel,
    canonical_sha256,
    deterministic_id,
)
from material_agent.research.flatband_intake import (
    FlatbandCalibrationIntakeManifestV1,
)
from material_agent.research.flatband_intake_bands_v2 import (
    FlatbandFermiQualifiedSelectionManifestV2,
)
from material_agent.research.flatband_intake_structures import (
    _structure_from_c2db_json,
)
from material_agent.research.flatband_structure_grouping import (
    StructureDimensionalityV2,
    StructureGroupingParametersV2,
    _anonymous_supercell_ratio_v2,
    _normalize_pymatgen_structure_v2,
    build_raw_structure_artifact_v2,
    normalize_raw_structure_artifact_v2,
)


class PositiveStructureAuditRecordV1(StrictModel):
    source_rank: Annotated[int, Field(ge=1)]
    c2db_record_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
    source_intake_manifest_sha256: Sha256
    source_structure_response_sha256: Sha256
    normalized_structure_artifact_sha256: Sha256
    normalized_structure_sha256: Sha256
    reduced_site_count: Annotated[int, Field(ge=1, le=128)]
    aperiodic_axis: Literal[0, 1, 2]


class PositiveStructurePairAuditV1(StrictModel):
    left_record_id: str
    right_record_id: str
    anonymous_supercell_factor: Annotated[int, Field(ge=1, le=9)] | None = None
    matcher_stage: Literal[
        "ANONYMOUS_STOICHIOMETRY_MISMATCH",
        "DIRECT_REDUCED_CELL",
        "BOUNDED_SUPERCELL",
    ]
    forward_fit_anonymous: bool = False
    reverse_fit_anonymous: bool = False
    fit_anonymous: bool = False

    @model_validator(mode="after")
    def validate_pair(self) -> PositiveStructurePairAuditV1:
        if self.left_record_id >= self.right_record_id:
            raise ValueError("structure pair IDs must be key-sorted")
        if self.fit_anonymous != (
            self.forward_fit_anonymous or self.reverse_fit_anonymous
        ):
            raise ValueError("structure pair fit summary does not replay")
        if self.matcher_stage == "ANONYMOUS_STOICHIOMETRY_MISMATCH":
            if self.anonymous_supercell_factor is not None or self.fit_anonymous:
                raise ValueError("stoichiometry mismatch cannot carry a fit")
        elif self.anonymous_supercell_factor is None:
            raise ValueError("matcher execution requires a supercell factor")
        return self


class PositiveStructureComponentV1(StrictModel):
    component_id: str = Field(pattern=r"^precase-structure-component-[0-9a-f]{24}$")
    member_record_ids: Annotated[tuple[str, ...], Field(min_length=1, max_length=96)]
    minimum_source_rank: Annotated[int, Field(ge=1)]

    @model_validator(mode="after")
    def validate_component(self) -> PositiveStructureComponentV1:
        if self.member_record_ids != tuple(sorted(set(self.member_record_ids))):
            raise ValueError("structure component members must be sorted and unique")
        expected = deterministic_id(
            "precase-structure-component",
            {"member_record_ids": self.member_record_ids},
        )
        if self.component_id != expected:
            raise ValueError("structure component ID does not address its members")
        return self


class PositiveStructureGroupingPreauditManifestV1(StrictModel):
    schema_version: Literal[
        "flatband-positive-structure-grouping-preaudit-manifest-v1"
    ] = "flatband-positive-structure-grouping-preaudit-manifest-v1"
    manifest_sha256: Sha256
    fermi_selection_manifest_sha256: Sha256
    source_intake_manifest_sha256s: Annotated[
        tuple[Sha256, ...], Field(min_length=1, max_length=32)
    ]
    parameters_sha256: Sha256
    records: Annotated[
        tuple[PositiveStructureAuditRecordV1, ...], Field(min_length=12, max_length=96)
    ]
    pairs: Annotated[
        tuple[PositiveStructurePairAuditV1, ...], Field(min_length=66, max_length=4_560)
    ]
    components: Annotated[
        tuple[PositiveStructureComponentV1, ...], Field(min_length=1, max_length=96)
    ]
    grouping_scope: Literal["PRECASE_ANONYMOUS_STRUCTURE_ONLY"] = (
        "PRECASE_ANONYMOUS_STRUCTURE_ONLY"
    )
    formal_structure_grouping_release: Literal[False] = False
    expert_derivative_screening_complete: Literal[False] = False
    calibration_only: Literal[True] = True
    pilot_execution_authorized: Literal[False] = False
    scientific_conclusion: Literal[False] = False

    @model_validator(mode="after")
    def validate_manifest(self) -> PositiveStructureGroupingPreauditManifestV1:
        if self.source_intake_manifest_sha256s != tuple(
            sorted(set(self.source_intake_manifest_sha256s))
        ):
            raise ValueError("structure pre-audit intake manifests must be unique")
        ranks = tuple(item.source_rank for item in self.records)
        ids = tuple(item.c2db_record_id for item in self.records)
        if ranks != tuple(sorted(set(ranks))) or len(ids) != len(set(ids)):
            raise ValueError("structure pre-audit records must be rank-sorted and unique")
        expected_pair_count = len(ids) * (len(ids) - 1) // 2
        if len(self.pairs) != expected_pair_count:
            raise ValueError("structure pre-audit must retain every candidate pair")
        expected_pairs = tuple(
            (left, right)
            for offset, left in enumerate(sorted(ids))
            for right in sorted(ids)[offset + 1 :]
        )
        observed_pairs = tuple(
            (item.left_record_id, item.right_record_id) for item in self.pairs
        )
        if observed_pairs != expected_pairs:
            raise ValueError("structure pre-audit pair matrix is incomplete")
        component_members = tuple(
            member for component in self.components for member in component.member_record_ids
        )
        if tuple(sorted(component_members)) != tuple(sorted(ids)):
            raise ValueError("structure components do not partition positive records")
        expected = canonical_sha256(
            self.model_dump(mode="python", exclude={"manifest_sha256"})
        )
        if self.manifest_sha256 != expected:
            raise ValueError("structure pre-audit manifest SHA-256 does not match")
        return self


class FlatbandStructureDiverseSelectionManifestV3(StrictModel):
    schema_version: Literal[
        "flatband-structure-diverse-calibration-selection-manifest-v3"
    ] = "flatband-structure-diverse-calibration-selection-manifest-v3"
    manifest_sha256: Sha256
    fermi_selection_manifest_sha256: Sha256
    structure_grouping_preaudit_manifest_sha256: Sha256
    source_intake_manifest_sha256s: Annotated[
        tuple[Sha256, ...], Field(min_length=1, max_length=32)
    ]
    selected_record_ids: Annotated[tuple[str, ...], Field(min_length=12, max_length=12)]
    replacement_record_ids: Annotated[tuple[str, ...], Field(max_length=84)]
    selected_component_ids: Annotated[
        tuple[str, ...], Field(min_length=12, max_length=12)
    ]
    selection_rule: Literal[
        "LOWEST_SOURCE_RANK_ONE_PER_PRECASE_ANONYMOUS_COMPONENT_UNTIL_12"
    ] = "LOWEST_SOURCE_RANK_ONE_PER_PRECASE_ANONYMOUS_COMPONENT_UNTIL_12"
    formal_structure_grouping_required: Literal[True] = True
    expert_target_confirmation_required: Literal[True] = True
    expert_derivative_screening_complete: Literal[False] = False
    calibration_only: Literal[True] = True
    gold_label_status: Literal["NOT_ANNOTATED"] = "NOT_ANNOTATED"
    pilot_execution_authorized: Literal[False] = False
    target_class_assignment_authorized: Literal[False] = False
    scientific_conclusion: Literal[False] = False

    @property
    def source_manifest_sha256s(self) -> tuple[str, ...]:
        """Compatibility projection used by private structure custody."""

        return self.source_intake_manifest_sha256s

    @model_validator(mode="after")
    def validate_selection(self) -> FlatbandStructureDiverseSelectionManifestV3:
        if self.source_intake_manifest_sha256s != tuple(
            sorted(set(self.source_intake_manifest_sha256s))
        ):
            raise ValueError("structure-diverse intake manifests must be unique")
        if len(set(self.selected_record_ids)) != 12:
            raise ValueError("structure-diverse selection IDs must be unique")
        if len(set(self.selected_component_ids)) != 12:
            raise ValueError("structure-diverse component IDs must be unique")
        if self.replacement_record_ids != tuple(sorted(set(self.replacement_record_ids))):
            raise ValueError("replacement IDs must be sorted and unique")
        if set(self.selected_record_ids) & set(self.replacement_record_ids):
            raise ValueError("selected and replacement structure records overlap")
        expected = canonical_sha256(
            self.model_dump(mode="python", exclude={"manifest_sha256"})
        )
        if self.manifest_sha256 != expected:
            raise ValueError("structure-diverse selection SHA-256 does not match")
        return self


def _components(
    record_ids: tuple[str, ...], pairs: tuple[PositiveStructurePairAuditV1, ...]
) -> tuple[tuple[str, ...], ...]:
    parent = {record_id: record_id for record_id in record_ids}

    def find(value: str) -> str:
        while parent[value] != value:
            parent[value] = parent[parent[value]]
            value = parent[value]
        return value

    for pair in pairs:
        if not pair.fit_anonymous:
            continue
        left, right = find(pair.left_record_id), find(pair.right_record_id)
        if left != right:
            parent[max(left, right)] = min(left, right)
    groups: dict[str, list[str]] = {}
    for record_id in record_ids:
        groups.setdefault(find(record_id), []).append(record_id)
    return tuple(sorted(tuple(sorted(items)) for items in groups.values()))


def build_positive_structure_grouping_preaudit_v1(
    *,
    selection: FlatbandFermiQualifiedSelectionManifestV2,
    intake_sources: tuple[
        tuple[FlatbandCalibrationIntakeManifestV1, Path], ...
    ],
    output_path: Path,
) -> PositiveStructureGroupingPreauditManifestV1:
    """Group every algorithmic positive with the frozen anonymous matcher."""

    selected = FlatbandFermiQualifiedSelectionManifestV2.model_validate(
        selection.model_dump(mode="python", round_trip=True)
    )
    parameters = StructureGroupingParametersV2()
    source_map = {}
    record_map = {}
    for manifest, root in intake_sources:
        validated = FlatbandCalibrationIntakeManifestV1.model_validate(
            manifest.model_dump(mode="python", round_trip=True)
        )
        source_map[validated.manifest_sha256] = validated
        for record in validated.records:
            if record.c2db_record_id in record_map:
                raise ValueError("structure pre-audit receives a repeated intake record")
            record_map[record.c2db_record_id] = (validated, root, record)
    if set(source_map) != set(selected.source_intake_manifest_sha256s):
        raise ValueError("structure pre-audit intake roots differ from selection")
    positives = tuple(
        item
        for item in selected.candidates
        if item.qualification_status == "ALGORITHMIC_FB_NB_CANDIDATE"
    )
    structures = {}
    payloads = {}
    records = []
    for candidate in positives:
        manifest, root, source = record_map[candidate.c2db_record_id]
        structure_artifact = next(
            item
            for item in source.artifacts
            if item.artifact_role == "C2DB_STRUCTURE_JSON"
        )
        raw_bytes = (root / structure_artifact.local_relative_path).read_bytes()
        if hashlib.sha256(raw_bytes).hexdigest() != structure_artifact.sha256:
            raise ValueError("structure pre-audit source hash drifted")
        structure = _structure_from_c2db_json(raw_bytes)
        pymatgen_bytes = json.dumps(
            structure.as_dict(),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
        raw = build_raw_structure_artifact_v2(
            private_artifact_uri=(
                "artifact://private/flatband-calibration-preaudit/"
                f"{candidate.c2db_record_id}/structure.pymatgen.json"
            ),
            source_id="c2db",
            source_record_id=candidate.c2db_record_id,
            source_record_raw_sha256=structure_artifact.sha256,
            artifact_format="PYMATGEN_JSON",
            raw_bytes=pymatgen_bytes,
        )
        normalized = normalize_raw_structure_artifact_v2(
            raw_artifact=raw,
            dimensionality=StructureDimensionalityV2.TWO_D,
        )
        normalized_structure = _structure_from_normalized_payload(normalized.payload)
        primitive = normalized_structure.get_primitive_structure(
            tolerance=parameters.matcher_primitive_reduction_tolerance_angstrom,
            use_site_props=False,
            reduce=True,
        )
        structures[candidate.c2db_record_id] = primitive
        payloads[candidate.c2db_record_id] = _normalize_pymatgen_structure_v2(
            primitive
        )
        records.append(
            PositiveStructureAuditRecordV1(
                source_rank=candidate.source_rank,
                c2db_record_id=candidate.c2db_record_id,
                source_intake_manifest_sha256=manifest.manifest_sha256,
                source_structure_response_sha256=structure_artifact.sha256,
                normalized_structure_artifact_sha256=normalized.artifact_sha256,
                normalized_structure_sha256=normalized.structure_sha256,
                reduced_site_count=len(primitive),
                aperiodic_axis=normalized.provenance.aperiodic_axis_evidence.chosen_axis,
            )
        )
    matcher_kwargs = {
        "ltol": parameters.matcher_lattice_length_tolerance,
        "stol": parameters.matcher_site_tolerance,
        "angle_tol": parameters.matcher_angle_tolerance_degrees,
        "primitive_cell": parameters.matcher_primitive_cell,
        "scale": parameters.matcher_scale,
        "allow_subset": parameters.matcher_allow_subset,
    }
    direct = StructureMatcher(**matcher_kwargs, attempt_supercell=False)
    supercell = StructureMatcher(**matcher_kwargs, attempt_supercell=True)
    pairs = []
    ids = tuple(sorted(structures))
    for offset, left in enumerate(ids):
        for right in ids[offset + 1 :]:
            ratio = _anonymous_supercell_ratio_v2(payloads[left], payloads[right])
            if ratio is None:
                stage = "ANONYMOUS_STOICHIOMETRY_MISMATCH"
                forward = reverse = False
            else:
                if ratio > parameters.two_d_matcher_maximum_supercell_site_ratio:
                    raise ValueError("pre-case anonymous supercell ratio exceeds limit")
                stage = "DIRECT_REDUCED_CELL" if ratio == 1 else "BOUNDED_SUPERCELL"
                matcher = direct if ratio == 1 else supercell
                forward = bool(matcher.fit_anonymous(structures[left], structures[right]))
                reverse = bool(matcher.fit_anonymous(structures[right], structures[left]))
            pairs.append(
                PositiveStructurePairAuditV1(
                    left_record_id=left,
                    right_record_id=right,
                    anonymous_supercell_factor=ratio,
                    matcher_stage=stage,
                    forward_fit_anonymous=forward,
                    reverse_fit_anonymous=reverse,
                    fit_anonymous=forward or reverse,
                )
            )
    rank_by_id = {item.c2db_record_id: item.source_rank for item in records}
    components = tuple(
        PositiveStructureComponentV1(
            component_id=deterministic_id(
                "precase-structure-component", {"member_record_ids": members}
            ),
            member_record_ids=members,
            minimum_source_rank=min(rank_by_id[item] for item in members),
        )
        for members in _components(ids, tuple(pairs))
    )
    ordered_components = tuple(
        sorted(components, key=lambda item: (item.minimum_source_rank, item.component_id))
    )
    values = {
        "fermi_selection_manifest_sha256": selected.manifest_sha256,
        "source_intake_manifest_sha256s": selected.source_intake_manifest_sha256s,
        "parameters_sha256": canonical_sha256(parameters.model_dump(mode="python")),
        "records": tuple(sorted(records, key=lambda item: item.source_rank)),
        "pairs": tuple(pairs),
        "components": ordered_components,
    }
    draft = PositiveStructureGroupingPreauditManifestV1.model_construct(
        manifest_sha256="0" * 64, **values
    )
    result = PositiveStructureGroupingPreauditManifestV1.model_validate(
        {
            **values,
            "manifest_sha256": canonical_sha256(
                draft.model_dump(mode="python", exclude={"manifest_sha256"})
            ),
        }
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(
            result.model_dump(mode="json"),
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n",
        "utf-8",
    )
    output_path.chmod(0o600)
    return result


def _structure_from_normalized_payload(payload):
    """Reconstruct a Pymatgen structure without importing private case machinery."""

    from pymatgen.core import Lattice, Structure

    return Structure(
        lattice=Lattice(payload.lattice_matrix_angstrom),
        species=[item.species for item in payload.sites],
        coords=[item.fractional_coordinates for item in payload.sites],
        coords_are_cartesian=False,
        to_unit_cell=True,
        validate_proximity=False,
    )


def build_structure_diverse_selection_v3(
    *,
    selection: FlatbandFermiQualifiedSelectionManifestV2,
    grouping: PositiveStructureGroupingPreauditManifestV1,
    output_path: Path,
) -> FlatbandStructureDiverseSelectionManifestV3:
    """Choose the lowest-ranked representative of the first 12 components."""

    selected = FlatbandFermiQualifiedSelectionManifestV2.model_validate(
        selection.model_dump(mode="python", round_trip=True)
    )
    audit = PositiveStructureGroupingPreauditManifestV1.model_validate(
        grouping.model_dump(mode="python", round_trip=True)
    )
    if audit.fermi_selection_manifest_sha256 != selected.manifest_sha256:
        raise ValueError("structure grouping does not bind the Fermi selection")
    if len(audit.components) < 12:
        raise ValueError("fewer than 12 independent preliminary structure components")
    rank_by_id = {item.c2db_record_id: item.source_rank for item in audit.records}
    chosen_components = audit.components[:12]
    selected_ids = tuple(
        min(component.member_record_ids, key=lambda item: (rank_by_id[item], item))
        for component in chosen_components
    )
    all_ids = tuple(item.c2db_record_id for item in audit.records)
    values = {
        "fermi_selection_manifest_sha256": selected.manifest_sha256,
        "structure_grouping_preaudit_manifest_sha256": audit.manifest_sha256,
        "source_intake_manifest_sha256s": selected.source_intake_manifest_sha256s,
        "selected_record_ids": selected_ids,
        "replacement_record_ids": tuple(sorted(set(all_ids) - set(selected_ids))),
        "selected_component_ids": tuple(
            item.component_id for item in chosen_components
        ),
    }
    draft = FlatbandStructureDiverseSelectionManifestV3.model_construct(
        manifest_sha256="0" * 64, **values
    )
    result = FlatbandStructureDiverseSelectionManifestV3.model_validate(
        {
            **values,
            "manifest_sha256": canonical_sha256(
                draft.model_dump(mode="python", exclude={"manifest_sha256"})
            ),
        }
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(
            result.model_dump(mode="json"),
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n",
        "utf-8",
    )
    output_path.chmod(0o600)
    return result


__all__ = [
    "FlatbandStructureDiverseSelectionManifestV3",
    "PositiveStructureGroupingPreauditManifestV1",
    "build_positive_structure_grouping_preaudit_v1",
    "build_structure_diverse_selection_v3",
]
