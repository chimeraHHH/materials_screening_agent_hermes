"""Compile a user Requirement into a source-native Agent01 Requirement.

The user-facing Requirement is source agnostic.  Once a database is chosen,
this module maps every hard constraint into that database's capability catalog
and preserves anything that cannot be represented as an explicit unmapped
constraint.  Unmapped constraints are never silently dropped.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Literal

from pydantic import Field

from material_agent.retrieval.models import Requirement, SourceDatabase, StrictModel
from material_agent.retrieval.source_capabilities import source_property_coverage


class SourceConstraint(StrictModel):
    constraint_id: str
    source_text: str
    field: str
    operator: str
    value: Any
    unit: str | None = None


class UnmappedSourceConstraint(StrictModel):
    constraint_id: str
    source_text: str
    status: Literal["UNMAPPED", "DEFERRED"] = "UNMAPPED"
    reason: str


class SourceRequirement(StrictModel):
    schema_version: Literal["agent01-source-requirement-v1"] = (
        "agent01-source-requirement-v1"
    )
    source_database: SourceDatabase
    requirement_id: str
    requirement_revision: int = Field(ge=1)
    parent_requirement_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    mapped_constraints: list[SourceConstraint] = Field(default_factory=list)
    unmapped_constraints: list[UnmappedSourceConstraint] = Field(default_factory=list)
    confirmed_by_user: bool = False

    @property
    def fingerprint(self) -> str:
        payload = self.model_dump(mode="json")
        return hashlib.sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
        ).hexdigest()


_COMMON = {
    "exact_formula": ("formula.exact", "eq", None),
    "include_elements": ("elements", "contains_all", None),
    "exclude_elements": ("elements", "contains_none", None),
    "band_gap_ev": ("band_gap", "range", "eV"),
    "energy_above_hull_ev_atom": ("energy_above_hull", "range", "eV/atom"),
    "is_metal": ("is_metal", "eq", "dimensionless"),
    "dimensionality": ("structural_dimensionality", "eq", "dimensionless"),
    "max_num_sites": ("num_sites", "lte", "count"),
}


def compile_source_requirement(
    requirement: Requirement, source: SourceDatabase | str
) -> SourceRequirement:
    """Map a confirmed user Requirement onto one selected source's catalog."""

    selected = SourceDatabase(source)
    parent_hash = hashlib.sha256(
        json.dumps(
            requirement.model_dump(mode="json"),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode()
    ).hexdigest()
    coverage = source_property_coverage(selected)
    available = set(coverage["agent01_native_properties"])
    available.update(coverage["agent01_structure_derived_properties"])
    # These are source query primitives even when a source does not publish a
    # normalized property (for example NOMAD formula/structure filtering).
    available.update({"formula.exact", "elements", "num_sites"})

    mapped: list[SourceConstraint] = []
    unmapped: list[UnmappedSourceConstraint] = []

    def add(
        constraint_id: str,
        source_text: str,
        field: str,
        operator: str,
        value: Any,
        unit: str | None,
    ) -> None:
        if field not in available:
            unmapped.append(
                UnmappedSourceConstraint(
                    constraint_id=constraint_id,
                    source_text=source_text,
                    reason=f"source catalog has no executable field: {field}",
                )
            )
            return
        mapped.append(
            SourceConstraint(
                constraint_id=constraint_id,
                source_text=source_text,
                field=field,
                operator=operator,
                value=value,
                unit=unit,
            )
        )

    hard = requirement.hard_constraints
    for name, (field, operator, unit) in _COMMON.items():
        value = getattr(hard, name)
        if value is None or value == []:
            continue
        if hasattr(value, "model_dump"):
            value = value.model_dump(mode="json")
        add(name, name, field, operator, value, unit)

    source_models = {
        SourceDatabase.MATERIALS_PROJECT: hard.source_constraints.materials_project,
        SourceDatabase.C2DB: hard.source_constraints.c2db,
        SourceDatabase.TOPOLOGICAL_QUANTUM_CHEMISTRY:
        hard.source_constraints.topological_quantum_chemistry,
    }
    selected_model = source_models.get(selected)
    if selected_model is not None:
        for field, value in selected_model.model_dump(exclude_none=True).items():
            mapped_field = {
                "density_g_cm3": "density",
                "volume_a3": "volume",
                "formation_energy_ev_atom": "formation_energy_per_atom",
                "magnetic_ordering": "ordering",
                "layer_group": "c2db_layer_group",
                "magnetic_label": "c2db_magnetic_label",
                "topological_material": "tqc_topological_material_label",
                "topological_classification": "tqc_topological_classification",
                "topological_subclassification": "tqc_topological_subclassification",
                "has_topological_indices": "tqc_has_topological_indices",
                "fermi_crossing_count": "tqc_fermi_crossing_count",
                "line_crossing_label": "tqc_line_crossing_label",
            }.get(field, field)
            operator = "range" if hasattr(value, "get") and {"min", "max"} & set(value) else "eq"
            unit = value.get("unit") if isinstance(value, dict) else None
            normalized = value
            if isinstance(value, dict) and {"min", "max"} & set(value):
                normalized = [value.get("min"), value.get("max")]
            add(
                f"source.{selected.value}.{field}",
                f"source.{selected.value}.{field}",
                mapped_field,
                operator,
                normalized,
                unit,
            )

    # Conditions explicitly targeting another source remain visible rather
    # than being interpreted as if they belonged to the selected database.
    all_models = {
        SourceDatabase.MATERIALS_PROJECT: hard.source_constraints.materials_project,
        SourceDatabase.C2DB: hard.source_constraints.c2db,
        SourceDatabase.TOPOLOGICAL_QUANTUM_CHEMISTRY:
        hard.source_constraints.topological_quantum_chemistry,
    }
    for other, model in all_models.items():
        if other is selected:
            continue
        for field, value in model.model_dump(exclude_none=True).items():
            unmapped.append(
                UnmappedSourceConstraint(
                    constraint_id=f"source.{other.value}.{field}",
                    source_text=f"source.{other.value}.{field}",
                    reason=f"constraint belongs to {other.value}, selected source is {selected.value}",
                )
            )

    return SourceRequirement(
        source_database=selected,
        requirement_id=requirement.requirement_id,
        requirement_revision=requirement.revision,
        parent_requirement_sha256=parent_hash,
        mapped_constraints=mapped,
        unmapped_constraints=unmapped,
        confirmed_by_user=False,
    )
