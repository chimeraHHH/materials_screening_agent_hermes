"""Normalize Materials Project documents into internal candidate records."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from material_agent.retrieval.models import (
    CandidateAuditRecord,
    CandidateAuditRecordV2,
    CandidateRecord,
    Decision,
    EvidenceLevel,
    PropertyOrigin,
    PropertyValue,
    ProvenanceStatus,
    RetrievalQueryPlan,
    SourceDatabase,
)
from material_agent.retrieval.structures import ProcessedStructure


ORIGIN_NAME_ALIASES = {
    "band_gap": ("band_gap", "electronic_structure", "dos"),
    "energy_above_hull": ("energy_above_hull", "energy", "thermo"),
    "is_metal": ("is_metal", "electronic_structure", "dos"),
    "num_sites": ("structure",),
    "structural_dimensionality": ("structure",),
    "c2db_layer_group": ("structure",),
    "c2db_magnetic_label": ("structure",),
}

ADAPTIVE_SUMMARY_PROPERTIES = {
    "formation_energy_per_atom": ("formation_energy_per_atom", "eV/atom"),
    "is_stable": ("is_stable", "dimensionless"),
    "equilibrium_reaction_energy_per_atom": ("equilibrium_reaction_energy_per_atom", "eV/atom"),
    "density": ("density", "g/cm^3"),
    "volume": ("volume", "A^3"),
    "is_gap_direct": ("is_gap_direct", "dimensionless"),
    "ordering": ("ordering", "label"),
    "total_magnetization": ("total_magnetization", "muB"),
    "possible_species": ("possible_species", "species"),
    "theoretical": ("theoretical", "dimensionless"),
}


def candidate_id_for(
    project_id: str,
    material_id: str,
    source_database: SourceDatabase | str = SourceDatabase.MATERIALS_PROJECT,
) -> str:
    source = SourceDatabase(source_database)
    value = uuid.uuid5(
        uuid.NAMESPACE_URL,
        f"material-agent://{project_id}/{source.value}/{material_id}",
    )
    return f"cand_{value.hex}"


def normalize_candidate(
    document: dict[str, Any],
    *,
    project_id: str,
    query_plan: RetrievalQueryPlan,
    processed_structure: ProcessedStructure,
    source_uri: str,
    source_sha256: str,
    structure_uri: str,
    structure_sha256: str,
    retrieved_at: datetime,
) -> CandidateRecord:
    material_id = str(document["material_id"])
    origins = _origin_map(document.get("origins"))
    source_provenance = document.get("source_provenance", {})
    properties = [
        _property(
            name="band_gap",
            value=document.get("band_gap"),
            unit="eV",
            origins=origins,
            plan=query_plan,
            retrieved_at=retrieved_at,
        ),
        _property(
            name="energy_above_hull",
            value=document.get("energy_above_hull"),
            unit="eV/atom",
            origins=origins,
            plan=query_plan,
            retrieved_at=retrieved_at,
        ),
        _property(
            name="is_metal",
            value=document.get("is_metal"),
            unit="dimensionless",
            origins=origins,
            plan=query_plan,
            retrieved_at=retrieved_at,
        ),
        _property(
            name="num_sites",
            value=processed_structure.num_sites,
            unit="count",
            origins=origins,
            plan=query_plan,
            retrieved_at=retrieved_at,
        ),
    ]
    if query_plan.policy_version == "retrieval-policy-mp-adaptive-v2":
        for field, (name, unit) in ADAPTIVE_SUMMARY_PROPERTIES.items():
            value = document.get(field)
            if value is not None:
                # PropertyValue is a frozen v1 scalar contract.  MP returns
                # possible_species as a list, so retain it losslessly enough
                # for deterministic display as a canonical label string; the
                # full list remains in the archived raw summary response.
                if field == "possible_species" and isinstance(value, list):
                    value = "; ".join(sorted(str(item) for item in value))
                properties.append(_property(
                    name=name, value=value, unit=unit, origins=origins,
                    plan=query_plan, retrieved_at=retrieved_at,
                ))
    if query_plan.source_database is SourceDatabase.C2DB:
        source_provenance = document.get("source_provenance")
        if isinstance(source_provenance, dict):
            for name, key in (
                ("c2db_layer_group", "layer_group"),
                ("c2db_magnetic_label", "magnetic_label"),
            ):
                value = source_provenance.get(key)
                if isinstance(value, str) and value.strip():
                    properties.append(_property(
                        name=name,
                        value=value.strip(),
                        unit="label",
                        origins=origins,
                        plan=query_plan,
                        retrieved_at=retrieved_at,
                    ))
    if query_plan.source_database is SourceDatabase.TOPOLOGICAL_QUANTUM_CHEMISTRY:
        properties.extend(
            _tqc_topology_properties(
                source_provenance=source_provenance,
                origins=origins,
                plan=query_plan,
                retrieved_at=retrieved_at,
            )
        )
    record_class = (
        CandidateAuditRecord
        if query_plan.source_database is SourceDatabase.MATERIALS_PROJECT
        else CandidateAuditRecordV2
    )
    provenance = {
        "source_endpoint": query_plan.endpoint,
        "database_version": query_plan.database_version,
        "query_fingerprint": query_plan.query_fingerprint,
        "requirement_hash": query_plan.requirement_hash,
        "retrieval_policy_version": query_plan.policy_version,
        "summary_formula": document.get("formula_pretty"),
    }
    if query_plan.source_database is not SourceDatabase.MATERIALS_PROJECT:
        provenance["source_provenance"] = source_provenance
    return record_class(
        candidate_id=candidate_id_for(
            project_id,
            material_id,
            query_plan.source_database,
        ),
        source_database=query_plan.source_database,
        formula=processed_structure.reduced_formula,
        source_database_version=query_plan.database_version,
        source_material_id=material_id,
        source_last_updated=_parse_datetime(document.get("last_updated")),
        query_id=query_plan.query_id,
        structure_id=processed_structure.structure_id,
        structure_artifact_uri=structure_uri,
        structure_artifact_sha256=structure_sha256,
        structure_source_artifact_uri=source_uri,
        structure_source_artifact_sha256=source_sha256,
        reduced_formula=processed_structure.reduced_formula,
        elements=processed_structure.elements,
        num_sites=processed_structure.num_sites,
        properties=properties,
        evidence_level=EvidenceLevel.L1_RETRIEVED,
        decision=Decision.UNCERTAIN,
        data_quality_flags=list(processed_structure.data_quality_flags),
        provenance=provenance,
    )


def add_dimensionality_property(
    candidate: CandidateRecord,
    *,
    value: int | None,
    method: str,
    policy_version: str,
    database_version: str,
    retrieved_at: datetime,
    warning_messages: list[str] | None = None,
) -> CandidateRecord:
    structure_origin = _property_lookup(candidate, "num_sites").origin
    origin = structure_origin.model_copy()
    flags = list(candidate.data_quality_flags)
    if value is None:
        flags.append("DIMENSIONALITY_EVALUATION_FAILED")
    if warning_messages:
        flags.append("DIMENSIONALITY_WARNING")
    prop = PropertyValue(
        name="structural_dimensionality",
        value=value,
        unit="dimensionless",
        source=(
            "derived_from_mp_structure"
            if str(candidate.source_database) == SourceDatabase.MATERIALS_PROJECT.value
            else f"derived_from_{str(candidate.source_database)}_structure"
        ),
        method=method,
        evidence_level=EvidenceLevel.L1_RETRIEVED,
        origin=PropertyOrigin(
            endpoint=origin.endpoint,
            database_version=database_version,
            origin_task_id=origin.origin_task_id,
            run_type=origin.run_type,
            task_type=origin.task_type,
            calc_type=origin.calc_type,
            status=origin.status,
        ),
        retrieved_at=retrieved_at,
        is_derived=True,
        derived_from_structure_id=candidate.structure_id,
        derivation_policy_version=policy_version,
    )
    return candidate.model_copy(
        update={
            "properties": [*candidate.properties, prop],
            "data_quality_flags": sorted(set(flags)),
        }
    )


def apply_task_metadata(
    candidate: CandidateRecord,
    resolved: dict[str, dict[str, Any]],
) -> CandidateRecord:
    properties: list[PropertyValue] = []
    for prop in candidate.properties:
        task_id = prop.origin.origin_task_id
        metadata = resolved.get(task_id or "")
        if metadata:
            run_type = _text(metadata.get("run_type"))
            task_type = _text(metadata.get("task_type"))
            calc_type = _text(metadata.get("calc_type"))
            method = run_type or calc_type or task_type
            status = (
                ProvenanceStatus.RESOLVED if method else ProvenanceStatus.PARTIAL
            )
            origin = prop.origin.model_copy(
                update={
                    "run_type": run_type,
                    "task_type": task_type,
                    "calc_type": calc_type,
                    "status": status,
                }
            )
            properties.append(
                prop.model_copy(update={"origin": origin, "method": method})
            )
        else:
            origin = prop.origin
            if task_id:
                origin = origin.model_copy(
                    update={"status": ProvenanceStatus.UNRESOLVED}
                )
            properties.append(
                prop.model_copy(update={"origin": origin, "method": None})
            )
    return candidate.model_copy(update={"properties": properties})


def collect_origin_task_ids(
    candidates: list[CandidateRecord],
) -> tuple[list[str], list[str]]:
    task_ids = {
        prop.origin.origin_task_id
        for candidate in candidates
        for prop in candidate.properties
        if prop.origin.origin_task_id
    }
    material_ids = {candidate.source_material_id for candidate in candidates}
    return sorted(task_ids), sorted(material_ids)


def _property(
    *,
    name: str,
    value: Any,
    unit: str,
    origins: dict[str, str],
    plan: RetrievalQueryPlan,
    retrieved_at: datetime,
) -> PropertyValue:
    task_id = next(
        (
            origins[alias]
            for alias in ORIGIN_NAME_ALIASES.get(name, (name,))
            if alias in origins
        ),
        None,
    )
    return PropertyValue(
        name=name,
        value=value,
        unit=unit,
        source=plan.source_database.value,
        method=None,
        origin=PropertyOrigin(
            endpoint=plan.endpoint,
            database_version=plan.database_version,
            origin_task_id=task_id,
            status=(
                ProvenanceStatus.PARTIAL
                if task_id
                else ProvenanceStatus.UNRESOLVED
            ),
        ),
        retrieved_at=retrieved_at,
    )


def _tqc_topology_properties(
    *,
    source_provenance: Any,
    origins: dict[str, str],
    plan: RetrievalQueryPlan,
    retrieved_at: datetime,
) -> list[PropertyValue]:
    """Expose a narrow TQC database label without promoting its evidence level."""

    provenance = (
        source_provenance if isinstance(source_provenance, dict) else {}
    )
    classification = provenance.get("topological_classification")
    short_description = (
        classification.get("shortDescription")
        if isinstance(classification, dict)
        else None
    )
    classification_label = (
        str(short_description).strip() if short_description is not None else None
    )
    if not classification_label:
        classification_label = None
    indices = provenance.get("topological_indices")
    index_items = indices.get("items") if isinstance(indices, dict) else None
    soc = provenance.get("soc")
    labeled_topological: bool | None
    if not isinstance(soc, bool) or not isinstance(index_items, list):
        labeled_topological = None
    else:
        labeled_topological = bool(
            classification_label
            and classification_label.casefold() != "trivial"
            and soc
            and index_items
        )
    task_id = origins.get("structure")
    origin = PropertyOrigin(
        endpoint=plan.endpoint,
        database_version=plan.database_version,
        origin_task_id=task_id,
        status=(
            ProvenanceStatus.PARTIAL
            if task_id
            else ProvenanceStatus.UNRESOLVED
        ),
    )
    return [
        PropertyValue(
            name="tqc_topological_classification",
            value=classification_label,
            unit="database_label",
            source=plan.source_database.value,
            method="TQC classification provenance v1",
            evidence_level=EvidenceLevel.L1_RETRIEVED,
            origin=origin,
            retrieved_at=retrieved_at,
        ),
        PropertyValue(
            name="tqc_topological_material_label",
            value=labeled_topological,
            unit="dimensionless",
            source=plan.source_database.value,
            method="TQC classification provenance v1",
            evidence_level=EvidenceLevel.L1_RETRIEVED,
            origin=origin,
            retrieved_at=retrieved_at,
        ),
        *[
            PropertyValue(
                name=name,
                value=value,
                unit=unit,
                source=plan.source_database.value,
                method="TQC crossing metadata provenance v1",
                evidence_level=EvidenceLevel.L1_RETRIEVED,
                origin=origin,
                retrieved_at=retrieved_at,
            )
            for name, key, unit in (
                ("tqc_fermi_crossing_count", "fermi_crossing_count", "count"),
                (
                    "tqc_fermi_crossing_first_conduction",
                    "fermi_crossing_first_conduction",
                    "count",
                ),
                (
                    "tqc_fermi_crossing_last_valence",
                    "fermi_crossing_last_valence",
                    "count",
                ),
                ("tqc_line_crossing_label", "line_crossing_label", "database_label"),
                (
                    "tqc_crossing_type_label",
                    "crossing_type_label",
                    "database_label",
                ),
            )
            if (value := provenance.get(key)) is not None
        ],
    ]
def _origin_map(raw_origins: Any) -> dict[str, str]:
    output: dict[str, str] = {}
    for raw in raw_origins or []:
        if hasattr(raw, "model_dump"):
            raw = raw.model_dump(mode="json")
        if not isinstance(raw, dict):
            continue
        name = raw.get("name")
        task_id = raw.get("task_id")
        if name and task_id:
            output[str(name)] = str(task_id)
    return output


def _property_lookup(candidate: CandidateRecord, name: str) -> PropertyValue:
    return next(prop for prop in candidate.properties if prop.name == name)


def _parse_datetime(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _text(value: Any) -> str | None:
    if value is None:
        return None
    return str(getattr(value, "value", value))
