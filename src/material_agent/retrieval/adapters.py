"""Materials-source adapters."""

from __future__ import annotations

import importlib.metadata
import math
import os
from collections.abc import Sequence
from typing import Any, Protocol

import requests
from pymatgen.core import Element, Lattice, Structure

from material_agent.retrieval.models import (
    RetrievalQueryPlan,
    SourceDatabase,
    SourceMetadata,
)
from material_agent.retrieval.query import (
    CORE_FIELDS,
    ELECTRON_VOLT_JOULE,
    NOMAD_REQUIRED_FIELDS,
    assert_mp_client_contract,
)


class MaterialsSourceAdapter(Protocol):
    is_mock: bool
    source_database: SourceDatabase

    def metadata(self) -> SourceMetadata: ...

    def search(self, plan: RetrievalQueryPlan) -> list[dict[str, Any]]: ...

    def resolve_task_metadata(
        self, task_ids: Sequence[str], material_ids: Sequence[str], batch_size: int
    ) -> tuple[dict[str, dict[str, Any]], list[str]]: ...


class MaterialsProjectAdapter:
    """Official mp-api backed adapter with no persisted credentials."""

    is_mock = False
    source_database = SourceDatabase.MATERIALS_PROJECT

    def __init__(self, api_key: str | None = None) -> None:
        self._api_key = api_key

    def _make_client(self):
        from mp_api.client import MPRester

        key = self._api_key or os.environ.get("MP_API_KEY")
        if not key:
            raise RuntimeError("MP_API_KEY is required for live Materials Project retrieval")
        return MPRester(
            api_key=key,
            use_document_model=False,
            mute_progress_bars=True,
            notify_db_version=False,
        )

    def metadata(self) -> SourceMetadata:
        with self._make_client() as client:
            assert_mp_client_contract(client.materials.summary.search)
            try:
                database_version = client.db_version
            except AttributeError:
                database_version = client.get_database_version()
            return SourceMetadata(
                database_version=str(database_version or "unknown"),
                client_version=importlib.metadata.version("mp-api"),
                available_fields=sorted(client.materials.summary.available_fields),
            )

    def search(self, plan: RetrievalQueryPlan) -> list[dict[str, Any]]:
        with self._make_client() as client:
            assert_mp_client_contract(client.materials.summary.search)
            documents = client.materials.summary.search(
                **plan.pushdown_filters,
                fields=plan.requested_fields,
                chunk_size=plan.chunk_size,
                num_chunks=plan.num_chunks,
                all_fields=False,
            )
        plain_documents = [_plain_document(document) for document in documents]
        return sorted(
            plain_documents, key=lambda item: str(item.get("material_id", ""))
        )

    def resolve_task_metadata(
        self, task_ids: Sequence[str], material_ids: Sequence[str], batch_size: int
    ) -> tuple[dict[str, dict[str, Any]], list[str]]:
        resolved: dict[str, dict[str, Any]] = {}
        warnings: list[str] = []
        unique_task_ids = sorted(set(task_ids))
        unique_material_ids = sorted(set(material_ids))

        with self._make_client() as client:
            for batch in _batched(unique_task_ids, batch_size):
                try:
                    documents = client.materials.tasks.search(
                        task_ids=list(batch),
                        fields=[
                            "task_id",
                            "run_type",
                            "task_type",
                            "calc_type",
                            "last_updated",
                        ],
                        all_fields=False,
                    )
                    for document in documents:
                        plain = _plain_document(document)
                        task_id = str(plain.get("task_id", ""))
                        if task_id:
                            resolved[task_id] = plain
                except Exception as exc:  # endpoint schema varies by MP release
                    if _is_transient_or_auth_error(exc):
                        raise
                    warnings.append(f"task metadata batch failed: {type(exc).__name__}")

            unresolved = set(unique_task_ids) - set(resolved)
            if unresolved:
                for batch in _batched(unique_material_ids, batch_size):
                    try:
                        documents = client.materials.search(
                            material_ids=list(batch),
                            fields=["material_id", "calc_types", "task_types"],
                            all_fields=False,
                        )
                        for document in documents:
                            plain = _plain_document(document)
                            calc_types = plain.get("calc_types") or {}
                            task_types = plain.get("task_types") or {}
                            for task_id in set(calc_types) | set(task_types):
                                task_id_string = str(task_id)
                                if task_id_string in unresolved:
                                    resolved[task_id_string] = {
                                        "task_id": task_id_string,
                                        "calc_type": _enum_value(calc_types.get(task_id)),
                                        "task_type": _enum_value(task_types.get(task_id)),
                                    }
                    except Exception as exc:
                        if _is_transient_or_auth_error(exc):
                            raise
                        warnings.append(
                            f"core calculation-map batch failed: {type(exc).__name__}"
                        )

            unresolved = set(unique_task_ids) - set(resolved)
            if unresolved:
                for batch in _batched(unique_material_ids, batch_size):
                    try:
                        documents = client.materials.thermo.search(
                            material_ids=list(batch),
                            fields=["material_id", "entries"],
                            all_fields=False,
                        )
                        for document in documents:
                            plain = _plain_document(document)
                            entries = plain.get("entries") or {}
                            entry_values = (
                                entries.values()
                                if isinstance(entries, dict)
                                else entries
                            )
                            for entry in entry_values:
                                entry_plain = _nested_plain(entry)
                                data = _nested_plain(entry_plain.get("data") or {})
                                parameters = _nested_plain(
                                    entry_plain.get("parameters") or {}
                                )
                                task_id = data.get("task_id")
                                if task_id is None:
                                    continue
                                task_id_string = str(task_id)
                                if task_id_string in unresolved:
                                    resolved[task_id_string] = {
                                        "task_id": task_id_string,
                                        "run_type": _enum_value(
                                            parameters.get("run_type")
                                        ),
                                    }
                    except Exception as exc:
                        if _is_transient_or_auth_error(exc):
                            raise
                        warnings.append(
                            f"thermo origin batch failed: {type(exc).__name__}"
                        )

        return resolved, sorted(set(warnings))


class NomadAdapter:
    """Public, read-only NOMAD archive API adapter."""

    is_mock = False
    source_database = SourceDatabase.NOMAD

    def __init__(
        self,
        *,
        base_url: str = "https://nomad-lab.eu/prod/v1/api/v1",
        session: requests.Session | None = None,
        timeout_seconds: float = 30.0,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.session = session or requests.Session()
        self.timeout_seconds = timeout_seconds
        self._method_metadata: dict[str, dict[str, Any]] = {}

    def metadata(self) -> SourceMetadata:
        response = self.session.get(
            f"{self.base_url}/openapi.json",
            timeout=self.timeout_seconds,
        )
        payload = _response_json(response, "NOMAD OpenAPI metadata")
        info = payload.get("info")
        if not isinstance(info, dict) or not isinstance(info.get("version"), str):
            raise ValueError("NOMAD OpenAPI metadata is missing info.version")
        return SourceMetadata(
            database_version=f"api:{info['version']}",
            client_version=importlib.metadata.version("requests"),
            available_fields=sorted(NOMAD_REQUIRED_FIELDS),
        )

    def search(self, plan: RetrievalQueryPlan) -> list[dict[str, Any]]:
        if plan.source_database is not SourceDatabase.NOMAD:
            raise ValueError("NOMAD adapter received a non-NOMAD query plan")

        documents: list[dict[str, Any]] = []
        page_after_value: str | None = None
        seen_cursors: set[str] = set()
        while len(documents) < plan.max_records_scanned:
            page_size = min(
                plan.chunk_size,
                plan.max_records_scanned - len(documents),
            )
            pagination: dict[str, Any] = {
                "page_size": page_size,
                "order_by": "entry_id",
                "order": "asc",
            }
            if page_after_value is not None:
                pagination["page_after_value"] = page_after_value
            response = self.session.post(
                f"{self.base_url}{plan.endpoint}",
                json={
                    "owner": "public",
                    "query": plan.pushdown_filters,
                    "pagination": pagination,
                    "required": _nomad_required_archive(),
                },
                timeout=self.timeout_seconds,
            )
            payload = _response_json(response, "NOMAD archive query")
            data = payload.get("data")
            if not isinstance(data, list):
                raise ValueError("NOMAD archive response is missing data[]")
            for entry in data:
                if not isinstance(entry, dict):
                    raise ValueError("NOMAD archive response contains a non-object entry")
                documents.append(self._map_entry(entry))
                if len(documents) >= plan.max_records_scanned:
                    break

            pagination_payload = payload.get("pagination")
            if not isinstance(pagination_payload, dict):
                raise ValueError("NOMAD archive response is missing pagination")
            next_cursor = pagination_payload.get("next_page_after_value")
            if next_cursor is None:
                break
            if not isinstance(next_cursor, str) or not next_cursor:
                raise ValueError("NOMAD pagination cursor is invalid")
            if next_cursor in seen_cursors:
                raise ValueError("NOMAD pagination cursor repeated")
            seen_cursors.add(next_cursor)
            page_after_value = next_cursor
            if not data:
                raise ValueError("NOMAD returned an empty page with a next cursor")

        return sorted(
            documents,
            key=lambda item: str(item.get("material_id", "")),
        )

    def resolve_task_metadata(
        self, task_ids: Sequence[str], material_ids: Sequence[str], batch_size: int
    ) -> tuple[dict[str, dict[str, Any]], list[str]]:
        del material_ids, batch_size
        resolved = {
            task_id: dict(self._method_metadata[task_id])
            for task_id in sorted(set(task_ids))
            if task_id in self._method_metadata
        }
        missing = sorted(set(task_ids) - set(resolved))
        warnings = (
            [f"NOMAD method metadata unavailable for {len(missing)} entries"]
            if missing
            else []
        )
        return resolved, warnings

    def _map_entry(self, entry: dict[str, Any]) -> dict[str, Any]:
        entry_id = entry.get("entry_id")
        if not isinstance(entry_id, str) or not entry_id:
            raise ValueError("NOMAD archive entry is missing entry_id")
        archive = entry.get("archive")
        results = archive.get("results") if isinstance(archive, dict) else None
        if not isinstance(results, dict):
            raise ValueError(f"NOMAD entry {entry_id} is missing archive.results")
        material = results.get("material")
        if not isinstance(material, dict):
            raise ValueError(f"NOMAD entry {entry_id} is missing results.material")
        elements = material.get("elements")
        if not isinstance(elements, list) or not all(
            isinstance(value, str) for value in elements
        ):
            raise ValueError(f"NOMAD entry {entry_id} has invalid material elements")

        structure = _nomad_structure(material.get("topology"))
        nsites = (
            len(structure.get("sites", []))
            if isinstance(structure, dict)
            else None
        )
        properties = results.get("properties")
        electronic = (
            properties.get("electronic")
            if isinstance(properties, dict)
            else None
        )
        band_gap, gap_provenance = _nomad_band_gap(electronic)
        method = results.get("method")
        method = method if isinstance(method, dict) else {}
        simulation = method.get("simulation")
        simulation = simulation if isinstance(simulation, dict) else {}
        method_name = _optional_text(method.get("method_name"))
        workflow_name = _optional_text(method.get("workflow_name"))
        program_name = _optional_text(simulation.get("program_name"))
        self._method_metadata[entry_id] = {
            "task_id": entry_id,
            "run_type": method_name,
            "task_type": workflow_name,
            "calc_type": program_name,
        }

        origins = [
            {"name": "structure", "task_id": entry_id},
        ]
        if band_gap is not None:
            origins.extend(
                [
                    {"name": "band_gap", "task_id": entry_id},
                    {"name": "is_metal", "task_id": entry_id},
                ]
            )
        formula = (
            material.get("chemical_formula_hill")
            or material.get("chemical_formula_reduced")
            or "unknown"
        )
        return {
            "material_id": entry_id,
            "formula_pretty": str(formula),
            "chemsys": "-".join(sorted(set(elements))),
            "elements": sorted(set(elements)),
            "nelements": len(set(elements)),
            "nsites": nsites,
            "structure": structure,
            "band_gap": band_gap,
            "energy_above_hull": None,
            "is_metal": False if band_gap is not None and band_gap > 0 else None,
            "deprecated": False,
            "theoretical": bool(method_name or program_name),
            "origins": origins,
            "last_updated": entry.get("publish_time"),
            "source_provenance": {
                "entry_id": entry_id,
                "upload_id": entry.get("upload_id"),
                "parser_name": entry.get("parser_name"),
                "method_name": method_name,
                "workflow_name": workflow_name,
                "program_name": program_name,
                "band_gap_selection": gap_provenance,
                "nomad_energy_unit": "joule",
            },
        }


class InMemoryMaterialsAdapter:
    """Offline adapter used by fixtures and deterministic E2E tests."""

    is_mock = True

    def __init__(
        self,
        documents: Sequence[dict[str, Any]],
        *,
        database_version: str = "fixture-2026-07-25",
        task_metadata: dict[str, dict[str, Any]] | None = None,
        honor_filters: bool = True,
        available_fields: Sequence[str] | None = None,
        source_database: SourceDatabase | str = SourceDatabase.MATERIALS_PROJECT,
    ) -> None:
        self.source_database = SourceDatabase(source_database)
        self.documents = [dict(document) for document in documents]
        self.database_version = database_version
        self.task_metadata = task_metadata or {}
        self.honor_filters = honor_filters
        default_fields = (
            CORE_FIELDS
            if self.source_database is SourceDatabase.MATERIALS_PROJECT
            else NOMAD_REQUIRED_FIELDS
        )
        self.available_fields = sorted(available_fields or default_fields)

    def metadata(self) -> SourceMetadata:
        return SourceMetadata(
            database_version=self.database_version,
            client_version="fixture-adapter-v1",
            available_fields=self.available_fields,
        )

    def search(self, plan: RetrievalQueryPlan) -> list[dict[str, Any]]:
        if plan.source_database is not self.source_database:
            raise ValueError("fixture adapter source does not match query plan")
        documents = self.documents
        if self.honor_filters:
            documents = [
                document
                for document in documents
                if _matches_pushdown(document, plan.pushdown_filters)
            ]
        documents = sorted(documents, key=lambda item: str(item.get("material_id", "")))
        return [dict(document) for document in documents[: plan.max_records_scanned]]

    def resolve_task_metadata(
        self, task_ids: Sequence[str], material_ids: Sequence[str], batch_size: int
    ) -> tuple[dict[str, dict[str, Any]], list[str]]:
        del material_ids, batch_size
        return (
            {
                task_id: dict(self.task_metadata[task_id])
                for task_id in task_ids
                if task_id in self.task_metadata
            },
            [],
        )


def _plain_document(document: Any) -> dict[str, Any]:
    if isinstance(document, dict):
        return document
    if hasattr(document, "model_dump"):
        return document.model_dump(mode="json")
    raise TypeError(f"unsupported Materials Project document type: {type(document)!r}")


def _nested_plain(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if hasattr(value, "as_dict"):
        return value.as_dict()
    return {}


def _batched(values: Sequence[str], size: int):
    for index in range(0, len(values), size):
        yield values[index : index + size]


def _enum_value(value: Any) -> str | None:
    if value is None:
        return None
    return str(getattr(value, "value", value))


def _matches_pushdown(document: dict[str, Any], filters: dict[str, Any]) -> bool:
    elements = {str(value) for value in document.get("elements", [])}
    required = set(filters.get("elements", []))
    if required and not required.issubset(elements):
        return False
    if elements & set(filters.get("exclude_elements", [])):
        return False
    if "is_metal" in filters and document.get("is_metal") != filters["is_metal"]:
        return False
    if "num_sites" in filters and not _in_range(
        document.get("nsites"), filters["num_sites"]
    ):
        return False
    if "band_gap" in filters and not _in_range(
        document.get("band_gap"), filters["band_gap"]
    ):
        return False
    if "energy_above_hull" in filters and not _in_range(
        document.get("energy_above_hull"), filters["energy_above_hull"]
    ):
        return False
    if document.get("deprecated") != filters.get("deprecated", False):
        return False
    if filters.get("theoretical") is not None:
        if document.get("theoretical") != filters["theoretical"]:
            return False
    return True


def _in_range(value: Any, bounds: Sequence[float]) -> bool:
    if value is None:
        return False
    return float(bounds[0]) <= float(value) <= float(bounds[1])


def _is_transient_or_auth_error(exc: Exception) -> bool:
    status_code = getattr(exc, "status_code", None)
    response = getattr(exc, "response", None)
    if response is not None:
        status_code = getattr(response, "status_code", status_code)
    if status_code in {401, 403, 429}:
        return True
    if isinstance(status_code, int) and status_code >= 500:
        return True
    text = str(exc).lower()
    return any(
        token in text
        for token in ("timeout", "rate limit", "temporar", "unauthorized", "forbidden")
    )


def _response_json(response: requests.Response, operation: str) -> dict[str, Any]:
    response.raise_for_status()
    try:
        payload = response.json()
    except ValueError as exc:
        raise ValueError(f"{operation} returned invalid JSON") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"{operation} returned a non-object response")
    return payload


def _nomad_required_archive() -> dict[str, Any]:
    return {
        "resolve-inplace": True,
        "results": {
            "material": {
                "elements": "*",
                "chemical_formula_hill": "*",
                "chemical_formula_reduced": "*",
                "topology": "include-resolved",
            },
            "properties": {"electronic": {"band_gap": "*"}},
            "method": "*",
        },
    }


def _nomad_structure(raw_topology: Any) -> dict[str, Any] | None:
    if not isinstance(raw_topology, list):
        return None
    topologies = [item for item in raw_topology if isinstance(item, dict)]
    topologies.sort(
        key=lambda item: (
            0 if str(item.get("label", "")).lower() == "original" else 1,
            str(item.get("system_id", "")),
        )
    )
    for topology in topologies:
        atoms = topology.get("atoms_ref")
        if not isinstance(atoms, dict):
            continue
        periodic = atoms.get("periodic")
        if periodic is not None and (
            not isinstance(periodic, list) or not all(periodic)
        ):
            continue
        lattice = atoms.get("lattice_vectors")
        positions = atoms.get("positions")
        labels = atoms.get("labels")
        species = atoms.get("species")
        if not isinstance(lattice, list) or not isinstance(positions, list):
            continue
        if not isinstance(labels, list):
            if not isinstance(species, list):
                continue
            try:
                labels = [str(Element.from_Z(int(value))) for value in species]
            except (TypeError, ValueError):
                continue
        if len(labels) != len(positions):
            continue
        try:
            lattice_angstrom = [
                [float(value) * 1.0e10 for value in row] for row in lattice
            ]
            positions_angstrom = [
                [float(value) * 1.0e10 for value in row] for row in positions
            ]
            structure = Structure(
                Lattice(lattice_angstrom),
                labels,
                positions_angstrom,
                coords_are_cartesian=True,
                to_unit_cell=False,
            )
        except (TypeError, ValueError):
            continue
        return structure.as_dict()
    return None


def _nomad_band_gap(
    electronic: Any,
) -> tuple[float | None, dict[str, Any] | None]:
    if not isinstance(electronic, dict):
        return None, None
    raw_gaps = electronic.get("band_gap")
    if not isinstance(raw_gaps, list):
        return None, None
    candidates: list[tuple[float, dict[str, Any]]] = []
    for item in raw_gaps:
        if not isinstance(item, dict):
            continue
        raw_value = item.get("value")
        if isinstance(raw_value, bool):
            continue
        try:
            value_joule = float(raw_value)
        except (TypeError, ValueError):
            continue
        if not math.isfinite(value_joule) or value_joule < 0:
            continue
        provenance = item.get("provenance")
        candidates.append(
            (
                value_joule,
                provenance if isinstance(provenance, dict) else {},
            )
        )
    if not candidates:
        return None, None
    value_joule, provenance = min(candidates, key=lambda item: item[0])
    return (
        value_joule / ELECTRON_VOLT_JOULE,
        {
            "policy": "minimum_nonnegative_reported_gap-v1",
            "reported_gap_count": len(candidates),
            "selected_provenance": provenance,
        },
    )


def _optional_text(value: Any) -> str | None:
    return str(value) if value is not None else None
