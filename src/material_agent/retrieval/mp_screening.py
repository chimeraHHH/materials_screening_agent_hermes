"""Versioned, capability-bound Materials Project adaptive screening contracts.

The LLM may only select capability IDs from :data:`MP_CAPABILITY_CATALOG`.
Endpoint names, field paths, units, evidence semantics, and missing-data policy
are owned by this module and never accepted from model output.
"""

from __future__ import annotations

import hashlib
import json
from enum import StrEnum
from typing import Any, Literal

from pydantic import Field, model_validator

from material_agent.retrieval.models import StrictModel


MP_SCREENING_SPEC_VERSION = "mp-screening-spec-v1"
MP_CAPABILITY_CATALOG_VERSION = "mp-capability-catalog-v1"


class ScreeningIntent(StrEnum):
    HARD = "HARD"
    PREFERENCE = "PREFERENCE"
    REPORT = "REPORT"


class EvidenceKind(StrEnum):
    EXACT = "EXACT"
    DERIVED = "DERIVED"
    PROXY = "PROXY"


class MappingStatus(StrEnum):
    MAPPED = "MAPPED"
    UNSUPPORTED = "UNSUPPORTED"
    AMBIGUOUS = "AMBIGUOUS"
    MISSING_THRESHOLD = "MISSING_THRESHOLD"
    UNSAFE_COMPARATOR = "UNSAFE_COMPARATOR"


class DeepEndpoint(StrEnum):
    ELECTRONIC_STRUCTURE = "electronic_structure"
    BANDSTRUCTURE_UNIFORM = "bandstructure_uniform"
    BANDSTRUCTURE_LINE = "bandstructure_line"
    DOS = "dos"
    OXIDATION_STATES = "oxidation_states"
    ROBOCRYS = "robocrys"
    BONDS = "bonds"
    CHEMENV = "chemenv"


class MPCapability(StrictModel):
    capability_id: str
    description: str
    endpoint: str
    field: str | None = None
    query_parameter: str | None = None
    value_type: Literal["bool", "number", "string", "string_list", "range", "derived", "availability"]
    unit: str | None = None
    operators: list[str] = Field(default_factory=list)
    evidence_kind: EvidenceKind
    intents: list[ScreeningIntent]
    pushdown: bool = False
    deep_endpoint: DeepEndpoint | None = None
    report_only: bool = False
    missing_policy: Literal["uncertain", "ignore", "fail_closed"] = "uncertain"
    caveat: str | None = None


class MappedClause(StrictModel):
    clause_id: str
    source_text: str
    capability_id: str
    intent: ScreeningIntent
    operator: str | None = None
    value: Any = None
    unit: str | None = None
    priority: int = Field(default=100, ge=0, le=1000)


class UnmappedClause(StrictModel):
    clause_id: str
    source_text: str
    status: MappingStatus
    reason: str


class MPScreeningSpec(StrictModel):
    schema_version: Literal["mp-screening-spec-v1"] = MP_SCREENING_SPEC_VERSION
    requirement_id: str
    requirement_revision: int = Field(ge=1)
    raw_request_sha256: str
    catalog_version: str = MP_CAPABILITY_CATALOG_VERSION
    catalog_sha256: str
    mapped_clauses: list[MappedClause] = Field(default_factory=list)
    unmapped_clauses: list[UnmappedClause] = Field(default_factory=list)
    deep_screen_limit: int = Field(default=20, ge=1, le=200)
    deep_screen_approval_required: bool = False
    deep_endpoints: list[DeepEndpoint] = Field(default_factory=list)
    confirmed_by_user: bool = False

    @model_validator(mode="after")
    def validate_catalog_and_limits(self) -> MPScreeningSpec:
        if self.catalog_version == MP_CAPABILITY_CATALOG_VERSION and self.catalog_sha256 != capability_catalog_hash():
            raise ValueError("MP capability catalog hash does not match the active catalog")
        if self.deep_screen_limit > 50 and not self.deep_screen_approval_required:
            raise ValueError("deep_screen_limit above 50 requires approval")
        known = set(MP_CAPABILITY_CATALOG)
        unknown = sorted(
            clause.capability_id
            for clause in self.mapped_clauses
            if clause.capability_id not in known
        )
        if unknown:
            raise ValueError(f"unknown MP capability IDs: {unknown}")
        for clause in self.mapped_clauses:
            capability = MP_CAPABILITY_CATALOG[clause.capability_id]
            if clause.intent not in capability.intents:
                raise ValueError(
                    f"capability {clause.capability_id} does not support {clause.intent}"
                )
            if clause.operator and clause.operator not in capability.operators:
                raise ValueError(
                    f"operator {clause.operator!r} is not allowed for {clause.capability_id}"
                )
            if clause.unit and capability.unit and clause.unit != capability.unit:
                raise ValueError(
                    f"unit for {clause.capability_id} must be {capability.unit!r}"
                )
            if capability.evidence_kind is EvidenceKind.PROXY and clause.intent is ScreeningIntent.HARD:
                raise ValueError("proxy capabilities cannot be hard constraints")
        expected_endpoints = sorted(
            {
                capability.deep_endpoint.value
                for clause in self.mapped_clauses
                if (capability := MP_CAPABILITY_CATALOG[clause.capability_id]).deep_endpoint
            }
        )
        actual_endpoints = sorted(endpoint.value for endpoint in self.deep_endpoints)
        if actual_endpoints != expected_endpoints:
            raise ValueError("deep_endpoints must be derived from mapped capabilities")
        return self

    @property
    def has_deep_hard_constraints(self) -> bool:
        return any(
            clause.intent is ScreeningIntent.HARD
            and MP_CAPABILITY_CATALOG[clause.capability_id].deep_endpoint is not None
            for clause in self.mapped_clauses
        )


class CompiledMPScreening(StrictModel):
    """Deterministic execution view derived from a validated spec."""

    pushdown_filters: dict[str, Any] = Field(default_factory=dict)
    requested_fields: list[str] = Field(default_factory=list)
    local_clauses: list[MappedClause] = Field(default_factory=list)
    proxy_clauses: list[MappedClause] = Field(default_factory=list)
    deep_clauses: list[MappedClause] = Field(default_factory=list)
    deep_endpoints: list[DeepEndpoint] = Field(default_factory=list)


def _cap(
    capability_id: str,
    description: str,
    *,
    field: str | None,
    query_parameter: str | None = None,
    value_type: str = "number",
    unit: str | None = None,
    operators: tuple[str, ...] = ("eq", "lt", "lte", "gt", "gte", "range"),
    evidence_kind: EvidenceKind = EvidenceKind.EXACT,
    intents: tuple[ScreeningIntent, ...] = (ScreeningIntent.HARD, ScreeningIntent.PREFERENCE),
    pushdown: bool = True,
    deep_endpoint: DeepEndpoint | None = None,
    report_only: bool = False,
    caveat: str | None = None,
) -> MPCapability:
    return MPCapability(
        capability_id=capability_id,
        description=description,
        endpoint="/materials/summary" if deep_endpoint is None else deep_endpoint.value,
        field=field,
        query_parameter=query_parameter,
        value_type=value_type,  # type: ignore[arg-type]
        unit=unit,
        operators=list(operators),
        evidence_kind=evidence_kind,
        intents=list(intents),
        pushdown=pushdown,
        deep_endpoint=deep_endpoint,
        report_only=report_only,
        caveat=caveat,
    )


MP_CAPABILITY_CATALOG: dict[str, MPCapability] = {
    "elements.include": _cap("elements.include", "required elements", field="elements", query_parameter="elements", value_type="string_list", operators=("contains_all",)),
    "elements.exclude": _cap("elements.exclude", "excluded elements", field="elements", query_parameter="exclude_elements", value_type="string_list", operators=("contains_none",)),
    "formula.exact": _cap("formula.exact", "exact formula", field="formula_pretty", query_parameter="formula", value_type="string", operators=("eq",)),
    "structure.num_sites": _cap("structure.num_sites", "number of sites", field="nsites", query_parameter="num_sites", unit="count", operators=("lte", "gte", "range")),
    "structure.crystal_system": _cap("structure.crystal_system", "crystal system", field="symmetry.crystal_system", query_parameter="crystal_system", value_type="string", operators=("eq", "in")),
    "structure.spacegroup_number": _cap("structure.spacegroup_number", "space group number", field="symmetry.number", query_parameter="spacegroup_number", value_type="number", unit="count", operators=("eq", "in")),
    "structure.density": _cap("structure.density", "mass density", field="density", query_parameter="density", unit="g/cm^3", operators=("lte", "gte", "range")),
    "structure.volume": _cap("structure.volume", "unit-cell volume", field="volume", query_parameter="volume", unit="A^3", operators=("lte", "gte", "range")),
    "structure.dimension": _cap("structure.dimension", "derived structure dimensionality", field=None, value_type="derived", unit="dimensionless", operators=("eq",), evidence_kind=EvidenceKind.DERIVED, pushdown=False),
    "thermo.energy_above_hull": _cap("thermo.energy_above_hull", "energy above hull", field="energy_above_hull", query_parameter="energy_above_hull", unit="eV/atom", operators=("lte", "gte", "range")),
    "thermo.formation_energy": _cap("thermo.formation_energy", "formation energy per atom", field="formation_energy_per_atom", query_parameter="formation_energy", unit="eV/atom", operators=("lte", "gte", "range")),
    "thermo.is_stable": _cap("thermo.is_stable", "on convex hull", field="is_stable", query_parameter="is_stable", value_type="bool", operators=("eq",)),
    "thermo.equilibrium_reaction_energy": _cap("thermo.equilibrium_reaction_energy", "equilibrium reaction energy", field="equilibrium_reaction_energy_per_atom", query_parameter="equilibrium_reaction_energy", unit="eV/atom", operators=("lte", "gte", "range")),
    "electronic.band_gap": _cap("electronic.band_gap", "band gap", field="band_gap", query_parameter="band_gap", unit="eV", operators=("lte", "gte", "range")),
    "electronic.is_metal": _cap("electronic.is_metal", "metallicity", field="is_metal", query_parameter="is_metal", value_type="bool", operators=("eq",)),
    "electronic.is_gap_direct": _cap("electronic.is_gap_direct", "direct band gap", field="is_gap_direct", query_parameter="is_gap_direct", value_type="bool", operators=("eq",)),
    "magnetism.ordering": _cap("magnetism.ordering", "calculated magnetic ordering", field="ordering", query_parameter="magnetic_ordering", value_type="string", operators=("eq", "in"), caveat="calculated ordering is not universally the true ground state"),
    "magnetism.total_magnetization": _cap("magnetism.total_magnetization", "total magnetization", field="total_magnetization", query_parameter="total_magnetization", unit="muB", operators=("lte", "gte", "range")),
    "magnetism.num_sites": _cap("magnetism.num_sites", "magnetic site count", field="num_magnetic_sites", query_parameter="num_magnetic_sites", unit="count", operators=("lte", "gte", "range")),
    "mechanical.bulk_modulus": _cap("mechanical.bulk_modulus", "VRH bulk modulus", field="k_vrh", query_parameter="k_vrh", unit="GPa", operators=("lte", "gte", "range")),
    "mechanical.shear_modulus": _cap("mechanical.shear_modulus", "VRH shear modulus", field="g_vrh", query_parameter="g_vrh", unit="GPa", operators=("lte", "gte", "range")),
    "mechanical.poisson_ratio": _cap("mechanical.poisson_ratio", "Poisson ratio", field="poisson_ratio", query_parameter="poisson_ratio", unit="dimensionless", operators=("lte", "gte", "range")),
    "mechanical.anisotropy": _cap("mechanical.anisotropy", "elastic anisotropy", field="elastic_anisotropy", query_parameter="elastic_anisotropy", unit="dimensionless", operators=("lte", "gte", "range")),
    "dielectric.total": _cap("dielectric.total", "total dielectric constant", field="e_total", query_parameter="e_total", unit="dimensionless", operators=("lte", "gte", "range")),
    "dielectric.electronic": _cap("dielectric.electronic", "electronic dielectric constant", field="e_electronic", query_parameter="e_electronic", unit="dimensionless", operators=("lte", "gte", "range")),
    "dielectric.ionic": _cap("dielectric.ionic", "ionic dielectric constant", field="e_ionic", query_parameter="e_ionic", unit="dimensionless", operators=("lte", "gte", "range")),
    "dielectric.refractive_index": _cap("dielectric.refractive_index", "refractive index", field="n", query_parameter="n", unit="dimensionless", operators=("lte", "gte", "range")),
    "surface.weighted_work_function": _cap("surface.weighted_work_function", "weighted work function", field="weighted_work_function", query_parameter="weighted_work_function", unit="eV", operators=("lte", "gte", "range")),
    "surface.reconstructed": _cap("surface.reconstructed", "has reconstructed surfaces", field="has_reconstructed", query_parameter="has_reconstructed", value_type="bool", operators=("eq",)),
    "composition.possible_species": _cap("composition.possible_species", "possible oxidation species", field="possible_species", query_parameter="possible_species", value_type="string_list", operators=("contains",), caveat="possible species is not a guaranteed charge assignment"),
    "availability.has_props": _cap("availability.has_props", "property availability", field="has_props", query_parameter="has_props", value_type="availability", operators=("has",), evidence_kind=EvidenceKind.EXACT, intents=(ScreeningIntent.HARD, ScreeningIntent.PREFERENCE)),
    "deep.sampled_bandwidth": _cap("deep.sampled_bandwidth", "uniform-grid sampled band width", field=None, value_type="derived", unit="eV", operators=("lte", "range"), evidence_kind=EvidenceKind.DERIVED, pushdown=False, deep_endpoint=DeepEndpoint.BANDSTRUCTURE_UNIFORM),
    "deep.oxidation_common": _cap("deep.oxidation_common", "transition-metal common valence", field=None, value_type="derived", unit="dimensionless", operators=("eq",), evidence_kind=EvidenceKind.DERIVED, pushdown=False, deep_endpoint=DeepEndpoint.OXIDATION_STATES),
    "deep.layered": _cap("deep.layered", "layered structure", field=None, value_type="derived", unit="dimensionless", operators=("eq",), evidence_kind=EvidenceKind.DERIVED, pushdown=False),
    "proxy.vdw_gap": _cap("proxy.vdw_gap", "van der Waals gap proxy", field=None, value_type="derived", unit="dimensionless", operators=("maximize",), evidence_kind=EvidenceKind.PROXY, intents=(ScreeningIntent.PREFERENCE,), pushdown=False, deep_endpoint=DeepEndpoint.ROBOCRYS),
    "proxy.band_crossing": _cap("proxy.band_crossing", "band crossing risk proxy", field=None, value_type="derived", unit="dimensionless", operators=("minimize",), evidence_kind=EvidenceKind.PROXY, intents=(ScreeningIntent.PREFERENCE,), pushdown=False, deep_endpoint=DeepEndpoint.BANDSTRUCTURE_LINE),
    "proxy.connected_sublattice": _cap("proxy.connected_sublattice", "periodic contributor connectivity proxy", field=None, value_type="derived", unit="dimensionless", operators=("maximize",), evidence_kind=EvidenceKind.PROXY, intents=(ScreeningIntent.PREFERENCE,), pushdown=False, deep_endpoint=DeepEndpoint.BONDS),
}


def capability_catalog_hash() -> str:
    payload = {
        key: value.model_dump(mode="json")
        for key, value in sorted(MP_CAPABILITY_CATALOG.items())
    }
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def compile_mp_screening_spec(spec: MPScreeningSpec) -> CompiledMPScreening:
    filters: dict[str, Any] = {}
    requested_fields = {
        "material_id", "formula_pretty", "elements", "nsites", "structure",
        "origins", "last_updated",
    }
    local: list[MappedClause] = []
    proxy: list[MappedClause] = []
    deep: list[MappedClause] = []
    for clause in spec.mapped_clauses:
        capability = MP_CAPABILITY_CATALOG[clause.capability_id]
        if capability.field:
            requested_fields.add(capability.field)
        if capability.deep_endpoint:
            deep.append(clause)
        elif capability.evidence_kind is EvidenceKind.PROXY:
            proxy.append(clause)
        else:
            local.append(clause)
        if capability.pushdown and capability.query_parameter and clause.intent is ScreeningIntent.HARD:
            if clause.operator == "eq":
                filters[capability.query_parameter] = clause.value
            elif clause.operator in {"lte", "gte", "range"}:
                value = clause.value
                if clause.operator == "range":
                    filters[capability.query_parameter] = tuple(value)
                elif clause.operator == "lte":
                    filters[capability.query_parameter] = (0.0, value)
                else:
                    filters[capability.query_parameter] = (value, None)
            elif clause.operator == "contains_all":
                filters[capability.query_parameter] = sorted(set(clause.value))
            elif clause.operator == "contains_none":
                filters[capability.query_parameter] = sorted(set(clause.value))
            elif clause.operator in {"in", "has"}:
                filters[capability.query_parameter] = clause.value
    return CompiledMPScreening(
        pushdown_filters=filters,
        requested_fields=sorted(requested_fields),
        local_clauses=local,
        proxy_clauses=proxy,
        deep_clauses=deep,
        deep_endpoints=spec.deep_endpoints,
    )


def make_spec(
    *,
    requirement_id: str,
    requirement_revision: int,
    raw_request_sha256: str,
    mapped_clauses: list[MappedClause],
    unmapped_clauses: list[UnmappedClause],
    deep_screen_limit: int = 20,
    deep_screen_approval_required: bool = False,
    confirmed_by_user: bool = False,
) -> MPScreeningSpec:
    endpoints = sorted(
        {
            capability.deep_endpoint
            for clause in mapped_clauses
            if (capability := MP_CAPABILITY_CATALOG[clause.capability_id]).deep_endpoint
        },
        key=lambda item: item.value,
    )
    return MPScreeningSpec(
        requirement_id=requirement_id,
        requirement_revision=requirement_revision,
        raw_request_sha256=raw_request_sha256,
        catalog_sha256=capability_catalog_hash(),
        mapped_clauses=mapped_clauses,
        unmapped_clauses=unmapped_clauses,
        deep_screen_limit=deep_screen_limit,
        deep_screen_approval_required=deep_screen_approval_required,
        deep_endpoints=endpoints,
        confirmed_by_user=confirmed_by_user,
    )
