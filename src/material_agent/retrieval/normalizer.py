"""Normalize Materials Project documents into internal candidate records."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from material_agent.retrieval.models import (
    CandidateAuditRecord,
    Decision,
    EvidenceLevel,
    PropertyOrigin,
    PropertyValue,
    ProvenanceStatus,
    RetrievalQueryPlan,
)
from material_agent.retrieval.structures import ProcessedStructure


ORIGIN_NAME_ALIASES = {
    "band_gap": ("band_gap", "electronic_structure", "dos"),
    "energy_above_hull": ("energy_above_hull", "energy", "thermo"),
    "is_metal": ("is_metal", "electronic_structure", "dos"),
    "num_sites": ("structure",),
    "structural_dimensionality": ("structure",),
}


def candidate_id_for(project_id: str, material_id: str) -> str:
    value = uuid.uuid5(
        uuid.NAMESPACE_URL,
        f"material-agent://{project_id}/materials_project/{material_id}",
    )
    return f"cand_{value.hex}"


def normalize_candidate(
    document: dict[str, Any],
    *,
    project_id: str,
    query_plan: RetrievalQueryPlan,
    processed_structure: ProcessedStructure,
    source_uri: str,
    structure_uri: str,
    retrieved_at: datetime,
) -> CandidateAuditRecord:
    material_id = str(document["material_id"])
    origins = _origin_map(document.get("origins"))
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
    return CandidateAuditRecord(
        candidate_id=candidate_id_for(project_id, material_id),
        formula=str(document.get("formula_pretty") or processed_structure.reduced_formula),
        source_database_version=query_plan.database_version,
        source_material_id=material_id,
        source_last_updated=_parse_datetime(document.get("last_updated")),
        query_id=query_plan.query_id,
        structure_id=processed_structure.structure_id,
        structure_artifact_uri=structure_uri,
        structure_source_artifact_uri=source_uri,
        reduced_formula=processed_structure.reduced_formula,
        elements=processed_structure.elements,
        num_sites=processed_structure.num_sites,
        properties=properties,
        evidence_level=EvidenceLevel.L1_RETRIEVED,
        decision=Decision.UNCERTAIN,
        data_quality_flags=list(processed_structure.data_quality_flags),
        provenance={
            "source_endpoint": query_plan.endpoint,
            "database_version": query_plan.database_version,
            "query_fingerprint": query_plan.query_fingerprint,
        },
    )


def add_dimensionality_property(
    candidate: CandidateAuditRecord,
    *,
    value: int | None,
    method: str,
    policy_version: str,
    database_version: str,
    retrieved_at: datetime,
) -> CandidateAuditRecord:
    structure_origin = _property_lookup(candidate, "num_sites").origin
    origin = structure_origin.model_copy()
    flags = list(candidate.data_quality_flags)
    if value is None:
        flags.append("DIMENSIONALITY_EVALUATION_FAILED")
    prop = PropertyValue(
        name="structural_dimensionality",
        value=value,
        unit="dimensionless",
        source="derived_from_mp_structure",
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
    candidate: CandidateAuditRecord,
    resolved: dict[str, dict[str, Any]],
) -> CandidateAuditRecord:
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
            properties.append(prop.model_copy(update={"origin": origin, "method": method}))
        else:
            properties.append(prop)
    return candidate.model_copy(update={"properties": properties})


def collect_origin_task_ids(
    candidates: list[CandidateAuditRecord],
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
            for alias in ORIGIN_NAME_ALIASES[name]
            if alias in origins
        ),
        None,
    )
    return PropertyValue(
        name=name,
        value=value,
        unit=unit,
        source="materials_project",
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


def _property_lookup(candidate: CandidateAuditRecord, name: str) -> PropertyValue:
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

