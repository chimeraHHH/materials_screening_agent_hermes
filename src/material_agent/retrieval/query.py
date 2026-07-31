"""Translate a confirmed requirement into an auditable MP query."""

from __future__ import annotations

import hashlib
import inspect
import json
from typing import Any

from pymatgen.core import Element

from material_agent.retrieval.models import (
    Requirement,
    RetrievalPolicy,
    RetrievalQueryPlan,
    SourceDatabase,
    SourceMetadata,
)
from material_agent.retrieval.mp_screening import (
    MPScreeningSpec,
    compile_mp_screening_spec,
)


CORE_FIELDS = [
    "material_id",
    "formula_pretty",
    "chemsys",
    "elements",
    "nelements",
    "nsites",
    "structure",
    "band_gap",
    "energy_above_hull",
    "is_metal",
    "deprecated",
    "theoretical",
    "origins",
    "last_updated",
]

# These fields are optional in the public summary endpoint.  They are never
# required for deterministic screening, but make the human report useful when
# the deployed MP release exposes them.
MP_REPORT_OPTIONAL_FIELDS = [
    "formation_energy_per_atom", "is_stable", "equilibrium_reaction_energy_per_atom",
    "decomposes_to", "density", "symmetry", "cbm", "vbm", "efermi",
    "is_gap_direct", "ordering", "total_magnetization", "possible_species",
    "has_props", "database_IDs",
]

NOMAD_REQUIRED_FIELDS = [
    "entry_id",
    "upload_id",
    "parser_name",
    "results.material.elements",
    "results.material.chemical_formula_hill",
    "results.material.topology.atoms_ref",
    "results.properties.electronic.band_gap",
    "results.method",
]

MULTI_SOURCE_REQUIRED_FIELDS = [
    "material_id",
    "formula_pretty",
    "elements",
    "nsites",
    "structure",
    "band_gap",
    "energy_above_hull",
    "is_metal",
    "origins",
]

ELECTRON_VOLT_JOULE = 1.602176634e-19


class QueryPlanningError(ValueError):
    """Raised when a requirement cannot be translated safely."""


AUTO_SOURCE = "auto"


def select_retrieval_source(
    requested_source: SourceDatabase | str,
    requirement: Requirement,
) -> SourceDatabase:
    """Resolve an explicit source or the conservative automatic choice.

    The choice is based only on frozen Requirement fields.  It deliberately
    does not combine records or use one source to fill another source's
    missing properties.
    """

    if requested_source != AUTO_SOURCE:
        return SourceDatabase(requested_source)

    if requirement.target_class == "topological_flat_band":
        return SourceDatabase.TOPOLOGICAL_QUANTUM_CHEMISTRY
    if requirement.target_class == "fm_2d_semiconductor":
        return SourceDatabase.C2DB
    # The remaining supported hard constraints have the most complete,
    # normalized coverage in Materials Project.  In particular, it is the
    # only default choice that supports the generic hull-energy constraint.
    return SourceDatabase.MATERIALS_PROJECT


def validate_element_symbols(requirement: Requirement) -> None:
    for symbol in (
        requirement.hard_constraints.include_elements
        + requirement.hard_constraints.exclude_elements
    ):
        try:
            Element(symbol)
        except (ValueError, KeyError) as exc:
            raise QueryPlanningError(f"invalid element symbol: {symbol}") from exc


def validate_requirement_contract(requirement: Requirement) -> None:
    """Validate constraints that must fail before any external API call."""

    validate_element_symbols(requirement)
    hard = requirement.hard_constraints
    if hard.band_gap_ev is not None and hard.band_gap_ev.unit != "eV":
        raise QueryPlanningError("band_gap_ev unit must be exactly 'eV'")
    if (
        hard.energy_above_hull_ev_atom is not None
        and hard.energy_above_hull_ev_atom.unit != "eV/atom"
    ):
        raise QueryPlanningError(
            "energy_above_hull_ev_atom unit must be exactly 'eV/atom'"
        )


def build_query_plan(
    requirement: Requirement,
    requirement_hash: str,
    metadata: SourceMetadata,
    policy: RetrievalPolicy,
    mp_screening_spec: MPScreeningSpec | None = None,
) -> RetrievalQueryPlan:
    if policy.source_database is SourceDatabase.NOMAD:
        return _build_nomad_query_plan(
            requirement,
            requirement_hash,
            metadata,
            policy,
        )
    if policy.source_database is not SourceDatabase.MATERIALS_PROJECT:
        return _build_multi_source_query_plan(
            requirement,
            requirement_hash,
            metadata,
            policy,
        )
    if not requirement.confirmed_by_user:
        raise QueryPlanningError("requirement must be confirmed before retrieval")
    validate_requirement_contract(requirement)

    missing_fields = sorted(set(CORE_FIELDS) - set(metadata.available_fields))
    if missing_fields:
        raise QueryPlanningError(
            f"Materials Project summary endpoint is missing required fields: {missing_fields}"
        )

    hard = requirement.hard_constraints
    filters: dict[str, Any] = {
        "deprecated": policy.include_deprecated,
        "include_gnome": (
            requirement.data_sources.materials_project.include_gnome
            or policy.include_gnome_default
        ),
    }
    local_only: list[str] = []

    compiled_spec = None
    if mp_screening_spec is not None:
        if policy.source_database is not SourceDatabase.MATERIALS_PROJECT:
            raise QueryPlanningError(
                "MP screening spec can only be used with Materials Project"
            )
        if not mp_screening_spec.confirmed_by_user:
            raise QueryPlanningError("MP screening spec must be confirmed")
        compiled_spec = compile_mp_screening_spec(mp_screening_spec)
        filters.update(compiled_spec.pushdown_filters)
        requested_spec_fields = set(compiled_spec.requested_fields)
    else:
        requested_spec_fields = set()

    if hard.include_elements:
        filters["elements"] = sorted(set(hard.include_elements))
    if hard.exclude_elements:
        filters["exclude_elements"] = sorted(set(hard.exclude_elements))
    if hard.is_metal is not None:
        filters["is_metal"] = hard.is_metal
    if hard.max_num_sites is not None:
        filters["num_sites"] = (1, hard.max_num_sites)
    if policy.theoretical is not None:
        filters["theoretical"] = policy.theoretical

    _add_range_filter(
        filters,
        local_only,
        "band_gap",
        hard.band_gap_ev,
        nonnegative=True,
    )
    _add_range_filter(
        filters,
        local_only,
        "energy_above_hull",
        hard.energy_above_hull_ev_atom,
        nonnegative=True,
    )
    if hard.dimensionality is not None:
        local_only.append("dimensionality")

    num_chunks = policy.max_records_scanned // policy.chunk_size
    requested_fields = sorted(
        set(CORE_FIELDS)
        | (set(MP_REPORT_OPTIONAL_FIELDS) & set(metadata.available_fields))
        | (requested_spec_fields & set(metadata.available_fields))
    )
    fingerprint_payload = {
        "database_version": metadata.database_version,
        "client_version": metadata.client_version,
        "endpoint": policy.endpoint,
        "filters": _jsonable(filters),
        "fields": requested_fields,
        "mp_report_policy": policy.mp_report.model_dump(mode="json"),
        "chunk_size": policy.chunk_size,
        "num_chunks": num_chunks,
        "requirement_hash": requirement_hash,
        "policy_version": policy.policy_version,
        "sort_fields": "local:material_id",
    }
    if mp_screening_spec is not None:
        fingerprint_payload["mp_screening_spec"] = mp_screening_spec.model_dump(mode="json")
    fingerprint = hashlib.sha256(
        json.dumps(
            fingerprint_payload, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
    ).hexdigest()

    return RetrievalQueryPlan(
        query_id=f"qry_{fingerprint[:24]}",
        endpoint=policy.endpoint,
        database_version=metadata.database_version,
        requirement_hash=requirement_hash,
        pushdown_filters=filters,
        local_only_constraints=sorted(set(local_only)),
        requested_fields=requested_fields,
        chunk_size=policy.chunk_size,
        num_chunks=num_chunks,
        max_records_scanned=policy.max_records_scanned,
        max_candidates_published=requirement.budget.max_candidates,
        include_gnome=bool(filters["include_gnome"]),
        include_deprecated=bool(filters["deprecated"]),
        theoretical_policy=policy.theoretical,
        sort_fields="local:material_id",
        policy_version=policy.policy_version,
        query_fingerprint=fingerprint,
        client_version=metadata.client_version,
    )


def retrieval_policy_for_source(
    source_database: SourceDatabase | str,
    *,
    mp_report_heavy_limit: int | None = None,
    adaptive_mp_screening: bool = False,
) -> RetrievalPolicy:
    source = SourceDatabase(source_database)
    if source is SourceDatabase.MATERIALS_PROJECT:
        policy = RetrievalPolicy()
        if adaptive_mp_screening:
            policy = policy.model_copy(
                update={
                    "policy_version": "retrieval-policy-mp-adaptive-v2",
                    "adaptive_mp_screening": True,
                }
            )
        if mp_report_heavy_limit is not None:
            policy = policy.model_copy(
                update={
                    "mp_report": policy.mp_report.model_copy(
                        update={
                            **(
                                {"heavy_candidate_limit": mp_report_heavy_limit}
                                if mp_report_heavy_limit is not None else {}
                            ),
                        }
                    )
                }
            )
        return policy
    profiles = {
        SourceDatabase.NOMAD: (
            "retrieval-policy-nomad-v1",
            "/entries/archive/query",
            100,
            5000,
        ),
        SourceDatabase.MC3D: (
            "retrieval-policy-mc3d-v1",
            "/v1/structures",
            100,
            5000,
        ),
        SourceDatabase.C2DB: (
            "retrieval-policy-c2db-v1",
            "/table",
            25,
            200,
        ),
        SourceDatabase.TOPOLOGICAL_QUANTUM_CHEMISTRY: (
            "retrieval-policy-tqc-v1",
            "/api/v4/search/_search/",
            25,
            25,
        ),
        SourceDatabase.NIMS_SUPERCON: (
            "retrieval-policy-nims-supercon-v1",
            "/datasets/650c4826-f0ca-42e8-8dd9-94025a5307ce",
            100,
            1000,
        ),
    }
    policy_version, endpoint, chunk_size, max_records = profiles[source]
    return RetrievalPolicy(
        policy_version=policy_version,
        source_database=source,
        endpoint=endpoint,
        chunk_size=chunk_size,
        max_records_scanned=max_records,
    )


def _build_multi_source_query_plan(
    requirement: Requirement,
    requirement_hash: str,
    metadata: SourceMetadata,
    policy: RetrievalPolicy,
) -> RetrievalQueryPlan:
    if not requirement.confirmed_by_user:
        raise QueryPlanningError("requirement must be confirmed before retrieval")
    validate_requirement_contract(requirement)
    missing_fields = sorted(
        set(MULTI_SOURCE_REQUIRED_FIELDS) - set(metadata.available_fields)
    )
    if missing_fields:
        raise QueryPlanningError(
            f"{policy.source_database.value} is missing required normalized fields: "
            f"{missing_fields}"
        )

    hard = requirement.hard_constraints
    filters: dict[str, Any] = {}
    local_only: list[str] = []
    if hard.include_elements:
        filters["elements"] = sorted(set(hard.include_elements))
    if hard.exclude_elements:
        filters["exclude_elements"] = sorted(set(hard.exclude_elements))
    if hard.max_num_sites is not None:
        filters["num_sites"] = [1, hard.max_num_sites]

    source = policy.source_database
    if source is SourceDatabase.C2DB:
        _add_range_filter(
            filters, local_only, "band_gap", hard.band_gap_ev, nonnegative=True
        )
        _add_range_filter(
            filters,
            local_only,
            "energy_above_hull",
            hard.energy_above_hull_ev_atom,
            nonnegative=True,
        )
        if hard.is_metal is not None:
            local_only.append("is_metal")
    else:
        if hard.band_gap_ev is not None:
            local_only.append("band_gap")
        if hard.energy_above_hull_ev_atom is not None:
            local_only.append("energy_above_hull")
        if hard.is_metal is not None:
            local_only.append("is_metal")
    if hard.dimensionality is not None:
        local_only.append("dimensionality")

    num_chunks = policy.max_records_scanned // policy.chunk_size
    sort_fields = {
        SourceDatabase.MC3D: "remote:id",
        SourceDatabase.C2DB: "remote:uid",
        SourceDatabase.TOPOLOGICAL_QUANTUM_CHEMISTRY: "remote:icsd",
        SourceDatabase.NIMS_SUPERCON: "local:record_id",
    }[source]
    fingerprint_payload = {
        "source_database": source.value,
        "database_version": metadata.database_version,
        "client_version": metadata.client_version,
        "endpoint": policy.endpoint,
        "filters": _jsonable(filters),
        "fields": MULTI_SOURCE_REQUIRED_FIELDS,
        "chunk_size": policy.chunk_size,
        "num_chunks": num_chunks,
        "requirement_hash": requirement_hash,
        "policy_version": policy.policy_version,
        "sort_fields": sort_fields,
    }
    fingerprint = hashlib.sha256(
        json.dumps(
            fingerprint_payload, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
    ).hexdigest()
    return RetrievalQueryPlan(
        query_id=f"qry_{fingerprint[:24]}",
        source_database=source,
        endpoint=policy.endpoint,
        database_version=metadata.database_version,
        requirement_hash=requirement_hash,
        pushdown_filters=filters,
        local_only_constraints=sorted(set(local_only)),
        requested_fields=MULTI_SOURCE_REQUIRED_FIELDS,
        chunk_size=policy.chunk_size,
        num_chunks=num_chunks,
        max_records_scanned=policy.max_records_scanned,
        max_candidates_published=requirement.budget.max_candidates,
        include_gnome=False,
        include_deprecated=False,
        theoretical_policy=None,
        sort_fields=sort_fields,
        policy_version=policy.policy_version,
        query_fingerprint=fingerprint,
        client_version=metadata.client_version,
    )


def _build_nomad_query_plan(
    requirement: Requirement,
    requirement_hash: str,
    metadata: SourceMetadata,
    policy: RetrievalPolicy,
) -> RetrievalQueryPlan:
    if not requirement.confirmed_by_user:
        raise QueryPlanningError("requirement must be confirmed before retrieval")
    validate_requirement_contract(requirement)
    if policy.endpoint != "/entries/archive/query":
        raise QueryPlanningError("NOMAD retrieval endpoint must be /entries/archive/query")

    missing_fields = sorted(
        set(NOMAD_REQUIRED_FIELDS) - set(metadata.available_fields)
    )
    if missing_fields:
        raise QueryPlanningError(
            f"NOMAD archive endpoint is missing required fields: {missing_fields}"
        )

    hard = requirement.hard_constraints
    clauses: list[dict[str, Any]] = []
    local_only: list[str] = []
    if hard.include_elements:
        clauses.append(
            {
                "results.material.elements": {
                    "all": sorted(set(hard.include_elements))
                }
            }
        )
    if hard.exclude_elements:
        clauses.append(
            {
                "not": {
                    "results.material.elements": {
                        "any": sorted(set(hard.exclude_elements))
                    }
                }
            }
        )
    if hard.band_gap_ev is not None:
        bounds: dict[str, float] = {}
        if hard.band_gap_ev.min is not None:
            bounds["gte"] = hard.band_gap_ev.min * ELECTRON_VOLT_JOULE
        if hard.band_gap_ev.max is not None:
            bounds["lte"] = hard.band_gap_ev.max * ELECTRON_VOLT_JOULE
        clauses.append(
            {"results.properties.electronic.band_gap.value": bounds}
        )
    if hard.energy_above_hull_ev_atom is not None:
        local_only.append("energy_above_hull")
    if hard.is_metal is not None:
        local_only.append("is_metal")
    if hard.max_num_sites is not None:
        local_only.append("max_num_sites")
    if hard.dimensionality is not None:
        local_only.append("dimensionality")

    if not clauses:
        nomad_query: dict[str, Any] = {}
    elif len(clauses) == 1:
        nomad_query = clauses[0]
    else:
        nomad_query = {"and": clauses}

    num_chunks = policy.max_records_scanned // policy.chunk_size
    fingerprint_payload = {
        "source_database": SourceDatabase.NOMAD.value,
        "database_version": metadata.database_version,
        "client_version": metadata.client_version,
        "endpoint": policy.endpoint,
        "owner": "public",
        "query": _jsonable(nomad_query),
        "fields": NOMAD_REQUIRED_FIELDS,
        "chunk_size": policy.chunk_size,
        "num_chunks": num_chunks,
        "requirement_hash": requirement_hash,
        "policy_version": policy.policy_version,
        "sort_fields": "remote:entry_id",
    }
    fingerprint = hashlib.sha256(
        json.dumps(
            fingerprint_payload, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
    ).hexdigest()
    return RetrievalQueryPlan(
        query_id=f"qry_{fingerprint[:24]}",
        source_database=SourceDatabase.NOMAD,
        endpoint=policy.endpoint,
        database_version=metadata.database_version,
        requirement_hash=requirement_hash,
        pushdown_filters=nomad_query,
        local_only_constraints=sorted(set(local_only)),
        requested_fields=NOMAD_REQUIRED_FIELDS,
        chunk_size=policy.chunk_size,
        num_chunks=num_chunks,
        max_records_scanned=policy.max_records_scanned,
        max_candidates_published=requirement.budget.max_candidates,
        include_gnome=False,
        include_deprecated=False,
        theoretical_policy=None,
        sort_fields="remote:entry_id",
        policy_version=policy.policy_version,
        query_fingerprint=fingerprint,
        client_version=metadata.client_version,
    )


def assert_mp_client_contract(search_callable: Any) -> None:
    parameters = inspect.signature(search_callable).parameters
    required = {
        "fields",
        "chunk_size",
        "num_chunks",
        "include_gnome",
    }
    missing = sorted(required - set(parameters))
    if missing:
        raise QueryPlanningError(f"mp-api search signature drifted; missing {missing}")


def _add_range_filter(
    filters: dict[str, Any],
    local_only: list[str],
    name: str,
    numeric_range: Any,
    *,
    nonnegative: bool,
) -> None:
    if numeric_range is None:
        return
    if numeric_range.min is not None and numeric_range.max is not None:
        filters[name] = (numeric_range.min, numeric_range.max)
    elif numeric_range.max is not None and nonnegative:
        filters[name] = (0.0, numeric_range.max)
    else:
        local_only.append(name)


def _jsonable(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _jsonable(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_jsonable(item) for item in value]
    if isinstance(value, list):
        return [_jsonable(item) for item in value]
    return value
