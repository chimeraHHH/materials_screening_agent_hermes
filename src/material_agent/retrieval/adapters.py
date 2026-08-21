"""Materials-source adapters."""

from __future__ import annotations

import csv
import getpass
import html
import importlib.metadata
import io
import json
import math
import os
import re
import subprocess
import time
from collections.abc import Callable, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Protocol
from urllib.parse import urljoin

import requests
from pymatgen.core import Composition, Element, Lattice, Structure

from material_agent.retrieval.models import (
    RetrievalQueryPlan,
    SourceDatabase,
    SourceMetadata,
)
from material_agent.retrieval.mp_screening import DeepEndpoint
from material_agent.retrieval.query import (
    CORE_FIELDS,
    ELECTRON_VOLT_JOULE,
    MULTI_SOURCE_REQUIRED_FIELDS,
    NOMAD_REQUIRED_FIELDS,
    assert_mp_client_contract,
)


class _NomadIncompleteEntry(ValueError):
    """A public NOMAD archive entry that lacks usable canonical material data."""


class MaterialsSourceAdapter(Protocol):
    is_mock: bool
    source_database: SourceDatabase

    def metadata(self) -> SourceMetadata: ...

    def search(self, plan: RetrievalQueryPlan) -> list[dict[str, Any]]: ...

    def resolve_task_metadata(
        self, task_ids: Sequence[str], material_ids: Sequence[str], batch_size: int
    ) -> tuple[dict[str, dict[str, Any]], list[str]]: ...

    def fetch_report_data(self, material_id: str, *, heavy: bool) -> dict[str, Any]: ...

    def fetch_deep_screen_data(
        self, material_id: str, *, endpoints: Sequence[DeepEndpoint]
    ) -> dict[str, Any]: ...


class MaterialsProjectAdapter:
    """Official mp-api backed adapter with no persisted credentials."""

    is_mock = False
    source_database = SourceDatabase.MATERIALS_PROJECT

    def __init__(
        self,
        api_key: str | None = None,
        *,
        environment: Mapping[str, str] | None = None,
        keychain_service: str = "material-screening-agent-mp-api",
        keychain_account: str | None = None,
        command_runner: Callable[..., subprocess.CompletedProcess[str]] | None = None,
    ) -> None:
        self._api_key = api_key
        self._environment = environment if environment is not None else os.environ
        self._keychain_service = _safe_keychain_label(
            keychain_service, "MP Keychain service"
        )
        self._keychain_account = _safe_keychain_label(
            keychain_account or getpass.getuser(),
            "MP Keychain account",
        )
        self._command_runner = command_runner or subprocess.run

    def _resolve_api_key(self) -> str:
        """Resolve lazily: explicit constructor key, environment, then Keychain."""

        explicit_key = (self._api_key or "").strip()
        if explicit_key:
            return explicit_key
        environment_key = self._environment.get("MP_API_KEY", "").strip()
        if environment_key:
            return environment_key
        try:
            result = self._command_runner(
                [
                    "security",
                    "find-generic-password",
                    "-a",
                    self._keychain_account,
                    "-s",
                    self._keychain_service,
                    "-w",
                ],
                check=False,
                capture_output=True,
                text=True,
                timeout=5,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise RuntimeError(
                "Materials Project API key is unavailable from the configured secret sources"
            ) from exc
        if result.returncode != 0:
            raise RuntimeError(
                "Materials Project API key is unavailable from the configured secret sources"
            )
        keychain_key = result.stdout.strip()
        if not keychain_key:
            raise RuntimeError(
                "Materials Project API key is empty in the configured secret source"
            )
        return keychain_key

    def _make_client(self):
        from mp_api.client import MPRester

        key = self._resolve_api_key()
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
        plain_documents = []
        for document in documents:
            plain = _plain_document(document)
            # Preserve the complete source record for the immutable raw
            # response artifact, even when a field is not yet normalized into
            # the Candidate contract.
            plain_documents.append({**plain, "source_response": dict(plain)})
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

    def fetch_report_data(self, material_id: str, *, heavy: bool) -> dict[str, Any]:
        """Fetch report-only endpoint payloads without affecting screening.

        Endpoint availability differs between MP deployments, so individual
        failures are represented explicitly instead of changing the candidate.
        """
        result: dict[str, Any] = {"material_id": material_id, "endpoints": {}, "errors": {}}
        endpoints = ["dielectric", "oxidation_states"]
        if heavy:
            endpoints += ["electronic_structure", "phonon", "xas", "absorption", "substrates"]
        with self._make_client() as client:
            for endpoint in endpoints:
                try:
                    rester = getattr(client.materials, endpoint)
                    documents = rester.search(
                        material_ids=[material_id], all_fields=True, chunk_size=1, num_chunks=1
                    )
                    result["endpoints"][endpoint] = [_plain_document(item) for item in documents]
                except Exception as exc:  # report enrichment must be non-fatal  # noqa: BLE001
                    result["errors"][endpoint] = type(exc).__name__
            if heavy:
                for key, method_name in (
                    ("bandstructure", "get_bandstructure_by_material_id"),
                    ("dos", "get_dos_by_material_id"),
                    ("phonon_bandstructure", "get_phonon_bandstructure_by_material_id"),
                    ("phonon_dos", "get_phonon_dos_by_material_id"),
                ):
                    try:
                        result[key] = getattr(client, method_name)(material_id)
                    except Exception as exc:  # noqa: BLE001
                        result["errors"][key] = type(exc).__name__
                try:
                    result["charge_density"] = client.get_charge_density_from_material_id(material_id)
                except Exception as exc:  # noqa: BLE001
                    result["errors"]["charge_density"] = type(exc).__name__
        return result

    def fetch_deep_screen_data(
        self, material_id: str, *, endpoints: Sequence[DeepEndpoint]
    ) -> dict[str, Any]:
        """Fetch only the frozen adaptive-screening endpoints for one material.

        Runtime band-structure objects are retained separately for deterministic
        local feature extraction.  ``payloads`` is JSON-serializable evidence
        suitable for immutable artifact storage and contains no credentials.
        """
        result: dict[str, Any] = {
            "material_id": material_id, "payloads": {}, "objects": {}, "errors": {},
        }
        with self._make_client() as client:
            for endpoint in sorted(set(endpoints), key=lambda item: item.value):
                key = endpoint.value
                try:
                    if endpoint is DeepEndpoint.BANDSTRUCTURE_UNIFORM:
                        value = client.get_bandstructure_by_material_id(
                            material_id, line_mode=False
                        )
                        result["objects"][key] = value
                        result["payloads"][key] = _nested_plain(value)
                    elif endpoint is DeepEndpoint.BANDSTRUCTURE_LINE:
                        value = client.get_bandstructure_by_material_id(
                            material_id, line_mode=True
                        )
                        result["objects"][key] = value
                        result["payloads"][key] = _nested_plain(value)
                    elif endpoint is DeepEndpoint.ROBOCRYS:
                        # In mp-api 0.45 the generic ``search`` is keyword
                        # text search; material-ID retrieval is ``search_docs``.
                        values = client.materials.robocrys.search_docs(
                            material_ids=[material_id], all_fields=True,
                            chunk_size=1, num_chunks=1,
                        )
                        result["payloads"][key] = [
                            _plain_document(item) for item in values
                        ]
                    else:
                        rester = getattr(client.materials, key)
                        values = rester.search(
                            material_ids=[material_id], all_fields=True,
                            chunk_size=1, num_chunks=1,
                        )
                        result["payloads"][key] = [
                            _plain_document(item) for item in values
                        ]
                except Exception as exc:  # noqa: BLE001
                    # A missing endpoint is evidence missing for this candidate,
                    # never a reason to silently relax a hard constraint.
                    result["errors"][key] = type(exc).__name__
        return result


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
        # NOMAD's public archive endpoint permits only one new connection per
        # five seconds.  Injected sessions are test doubles, so they must not
        # make unit tests wait.
        self._request_interval_seconds = 5.2 if session is None else 0.0
        self._last_request_monotonic: float | None = None
        self._method_metadata: dict[str, dict[str, Any]] = {}
        self._skipped_incomplete_entries = 0

    def metadata(self) -> SourceMetadata:
        response = self._get(
            f"{self.base_url}/openapi.json",
            timeout=self.timeout_seconds,
        )
        payload = _response_json(response, "NOMAD OpenAPI metadata")
        info = payload.get("info")
        if not isinstance(info, dict) or not isinstance(info.get("version"), str):
            raise ValueError("NOMAD OpenAPI metadata is missing info.version")  # noqa: TRY004
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
            response = self._post(
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
                raise ValueError("NOMAD archive response is missing data[]")  # noqa: TRY004
            for entry in data:
                if not isinstance(entry, dict):
                    raise ValueError("NOMAD archive response contains a non-object entry")  # noqa: TRY004
                # Public archive pagination can include incomplete uploads without
                # ``results.material``.  They cannot yield a canonical structure,
                # but must not make otherwise valid later records disappear.
                # Keep the cursor moving and record their count as provenance.
                try:
                    mapped = self._map_entry(entry)
                    mapped["source_response"] = entry
                    documents.append(mapped)
                except _NomadIncompleteEntry:
                    self._skipped_incomplete_entries += 1
                if len(documents) >= plan.max_records_scanned:
                    break

            pagination_payload = payload.get("pagination")
            if not isinstance(pagination_payload, dict):
                raise ValueError("NOMAD archive response is missing pagination")  # noqa: TRY004
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

    def _wait_for_request_slot(self) -> None:
        if self._last_request_monotonic is not None:
            elapsed = time.monotonic() - self._last_request_monotonic
            if elapsed < self._request_interval_seconds:
                time.sleep(self._request_interval_seconds - elapsed)
        self._last_request_monotonic = time.monotonic()

    def _get(self, url: str, **kwargs: Any) -> requests.Response:
        self._wait_for_request_slot()
        return self.session.get(url, **kwargs)

    def _post(self, url: str, **kwargs: Any) -> requests.Response:
        self._wait_for_request_slot()
        return self.session.post(url, **kwargs)

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
        if self._skipped_incomplete_entries:
            warnings.append(
                "NOMAD skipped "
                f"{self._skipped_incomplete_entries} incomplete archive entries "
                "without a canonical material record"
            )
        return resolved, warnings

    def _map_entry(self, entry: dict[str, Any]) -> dict[str, Any]:
        entry_id = entry.get("entry_id")
        if not isinstance(entry_id, str) or not entry_id:
            raise ValueError("NOMAD archive entry is missing entry_id")
        archive = entry.get("archive")
        results = archive.get("results") if isinstance(archive, dict) else None
        if not isinstance(results, dict):
            raise _NomadIncompleteEntry(
                f"NOMAD entry {entry_id} is missing archive.results"
            )
        material = results.get("material")
        if not isinstance(material, dict):
            raise _NomadIncompleteEntry(
                f"NOMAD entry {entry_id} is missing results.material"
            )
        elements = material.get("elements")
        if not isinstance(elements, list) or not all(
            isinstance(value, str) for value in elements
        ):
            raise _NomadIncompleteEntry(
                f"NOMAD entry {entry_id} has invalid material elements"
            )

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


class Mc3dAdapter:
    """Public MC3D PBE-v1 OPTIMADE structure adapter."""

    is_mock = False
    source_database = SourceDatabase.MC3D

    def __init__(
        self,
        *,
        base_url: str = (
            "https://optimade.materialscloud.org/main/mc3d-pbe-v1"
        ),
        session: requests.Session | None = None,
        timeout_seconds: float = 30.0,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.session = session or requests.Session()
        self.timeout_seconds = timeout_seconds

    def metadata(self) -> SourceMetadata:
        payload = _response_json(
            self.session.get(
                f"{self.base_url}/v1/info", timeout=self.timeout_seconds
            ),
            "MC3D OPTIMADE metadata",
        )
        meta = payload.get("meta")
        api_version = (
            meta.get("api_version") if isinstance(meta, dict) else None
        )
        if not isinstance(api_version, str):
            raise ValueError("MC3D OPTIMADE metadata is missing meta.api_version")  # noqa: TRY004
        return SourceMetadata(
            database_version=f"pbe-v1;optimade:{api_version}",
            client_version=importlib.metadata.version("requests"),
            available_fields=sorted(MULTI_SOURCE_REQUIRED_FIELDS),
        )

    def search(self, plan: RetrievalQueryPlan) -> list[dict[str, Any]]:
        if plan.source_database is not SourceDatabase.MC3D:
            raise ValueError("MC3D adapter received a non-MC3D query plan")
        filters = _optimade_filter(plan.pushdown_filters)
        next_url: str | None = f"{self.base_url}{plan.endpoint}"
        params: dict[str, Any] | None = {
            "filter": filters,
            "page_limit": min(plan.chunk_size, plan.max_records_scanned),
            # Omit response_fields so the OPTIMADE server returns its full
            # advertised attribute set. The normalized structure below is
            # still the only input to deterministic screening.
            "sort": "id",
        }
        documents: list[dict[str, Any]] = []
        seen_urls: set[str] = set()
        while next_url and len(documents) < plan.max_records_scanned:
            if next_url in seen_urls:
                raise ValueError("MC3D OPTIMADE pagination URL repeated")
            seen_urls.add(next_url)
            payload = _response_json(
                self.session.get(
                    next_url,
                    params=params,
                    timeout=self.timeout_seconds,
                ),
                "MC3D OPTIMADE structures",
            )
            params = None
            data = payload.get("data")
            if not isinstance(data, list):
                raise ValueError("MC3D OPTIMADE response is missing data[]")  # noqa: TRY004
            for entry in data:
                if not isinstance(entry, dict):
                    raise ValueError("MC3D OPTIMADE data contains a non-object")  # noqa: TRY004
                mapped = _map_optimade_structure(entry, "mc3d:pbe-v1")
                mapped["source_response"] = entry
                documents.append(mapped)
                if len(documents) >= plan.max_records_scanned:
                    break
            links = payload.get("links")
            raw_next = links.get("next") if isinstance(links, dict) else None
            if isinstance(raw_next, dict):
                raw_next = raw_next.get("href")
            next_url = (
                urljoin(f"{self.base_url}/", raw_next)
                if isinstance(raw_next, str) and raw_next
                else None
            )
        return sorted(documents, key=lambda item: str(item["material_id"]))

    def resolve_task_metadata(
        self, task_ids: Sequence[str], material_ids: Sequence[str], batch_size: int
    ) -> tuple[dict[str, dict[str, Any]], list[str]]:
        del material_ids, batch_size
        return (
            {
                task_id: {
                    "task_id": task_id,
                    "run_type": "PBE",
                    "task_type": "DFT structure relaxation",
                    "calc_type": "Quantum ESPRESSO/SIRIUS",
                }
                for task_id in sorted(set(task_ids))
            },
            [],
        )


class C2dbAdapter:
    """Read-only adapter for the official C2DB search and JSON downloads."""

    is_mock = False
    source_database = SourceDatabase.C2DB

    def __init__(
        self,
        *,
        base_url: str = "https://c2db.fysik.dtu.dk",
        session: requests.Session | None = None,
        timeout_seconds: float = 30.0,
        max_concurrent_downloads: int = 8,
        download_session_factory: Callable[[], Any] | None = None,
    ) -> None:
        if not 1 <= max_concurrent_downloads <= 32:
            raise ValueError("C2DB max_concurrent_downloads must be between 1 and 32")
        self.base_url = base_url.rstrip("/")
        self.session = session or requests.Session()
        self.timeout_seconds = timeout_seconds
        self.max_concurrent_downloads = max_concurrent_downloads
        self.download_session_factory = (
            download_session_factory
            if download_session_factory is not None
            else (requests.Session if session is None else None)
        )

    def metadata(self) -> SourceMetadata:
        response = self.session.get(
            f"{self.base_url}/help", timeout=self.timeout_seconds
        )
        response.raise_for_status()
        if "C2DB UID" not in response.text or "Band gap (PBE)" not in response.text:
            raise ValueError("C2DB help page schema drifted")
        version = response.headers.get("Last-Modified", "undated-live-web")
        return SourceMetadata(
            database_version=f"web:{version}",
            client_version=importlib.metadata.version("requests"),
            available_fields=sorted(MULTI_SOURCE_REQUIRED_FIELDS),
        )

    def search(self, plan: RetrievalQueryPlan) -> list[dict[str, Any]]:
        if plan.source_database is not SourceDatabase.C2DB:
            raise ValueError("C2DB adapter received a non-C2DB query plan")
        params = _c2db_params(plan.pushdown_filters)
        initial = self.session.get(
            f"{self.base_url}/", timeout=self.timeout_seconds
        )
        initial.raise_for_status()
        sid_match = re.search(
            r'hx-get="/table\?sid=([^"&]+)"', initial.text
        )
        if sid_match is None:
            raise ValueError("C2DB landing page is missing search session id")
        page = 0
        sid: str | None = sid_match.group(1)
        rows: list[dict[str, str]] = []
        while len(rows) < plan.max_records_scanned:
            request_params = (
                {**params, "sid": sid, "page": page}
                if page == 0
                else {"sid": sid, "page": page}
            )
            response = self.session.get(
                f"{self.base_url}{plan.endpoint}",
                params=request_params,
                timeout=self.timeout_seconds,
            )
            response.raise_for_status()
            parsed_rows, parsed_sid, has_next = _parse_c2db_table(response.text)
            if parsed_sid != sid:
                raise ValueError("C2DB search session changed during pagination")
            if not parsed_rows:
                break
            for parsed_row in parsed_rows[: plan.max_records_scanned - len(rows)]:
                parsed_row["_table_response"] = response.text
                rows.append(parsed_row)
            if not has_next or len(rows) >= plan.max_records_scanned:
                break
            page += 1

        rows = [
            row
            for row in rows
            if _c2db_row_matches_predownload_filters(
                row, plan.predownload_filters
            )
        ]
        if not rows:
            return []
        workers = min(self.max_concurrent_downloads, len(rows))
        with ThreadPoolExecutor(
            max_workers=workers,
            thread_name_prefix="c2db-download",
        ) as executor:
            documents = list(executor.map(self._material, rows))
        return sorted(documents, key=lambda item: str(item["material_id"]))

    def _material(self, row: dict[str, str]) -> dict[str, Any]:
        uid = row["uid"]
        session = (
            self.download_session_factory()
            if self.download_session_factory is not None
            else self.session
        )
        close_session = session is not self.session
        try:
            response = session.get(
                f"{self.base_url}/material/{uid}/download/json",
                timeout=self.timeout_seconds,
            )
            payload = _response_json(response, f"C2DB material {uid}")
        finally:
            if close_session:
                close = getattr(session, "close", None)
                if callable(close):
                    close()
        atoms = payload.get("1")
        if not isinstance(atoms, dict):
            raise ValueError(f"C2DB material {uid} is missing atoms record '1'")  # noqa: TRY004
        structure = _ase_json_structure(atoms)
        elements = sorted({str(site.specie) for site in structure})
        gap = _optional_float(row.get("band_gap"))
        ehull = _optional_float(row.get("energy_above_hull"))
        return {
            "material_id": uid,
            "formula_pretty": row.get("formula") or structure.composition.formula,
            "elements": elements,
            "nelements": len(elements),
            "nsites": len(structure),
            "structure": structure.as_dict(),
            "band_gap": gap,
            "energy_above_hull": ehull,
            "is_metal": gap == 0.0 if gap is not None else None,
            "deprecated": False,
            "theoretical": True,
            "origins": [
                {"name": name, "task_id": uid}
                for name in ("structure", "band_gap", "energy_above_hull", "is_metal")
            ],
            "last_updated": None,
            "source_provenance": {
                "uid": uid,
                "method": "GPAW/PBE",
                "license": "CC-BY-NC-4.0",
                "layer_group": row.get("layer_group"),
                "magnetic_label": row.get("magnetic"),
            },
            "source_response": {
                "table_row": {
                    key: value
                    for key, value in row.items()
                    if key != "_table_response"
                },
                "table_html": row.get("_table_response"),
                "download_json": payload,
            },
        }

    def resolve_task_metadata(
        self, task_ids: Sequence[str], material_ids: Sequence[str], batch_size: int
    ) -> tuple[dict[str, dict[str, Any]], list[str]]:
        del material_ids, batch_size
        return (
            {
                task_id: {
                    "task_id": task_id,
                    "run_type": "PBE",
                    "task_type": "C2DB high-throughput workflow",
                    "calc_type": "GPAW",
                }
                for task_id in sorted(set(task_ids))
            },
            [],
        )

    def fetch_plotly_bandstructure(self, material_id: str) -> dict[str, Any]:
        """Return the official PBE band plot embedded in a C2DB material page.

        C2DB currently publishes the numerical band traces as a Plotly JSON
        object rather than through the structure-download JSON endpoint.  Keep
        page parsing in the source adapter so report code never depends on HTML
        layout details or confuses a rendered plot with screening evidence.
        """

        if not material_id or any(
            character not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-"
            for character in material_id
        ):
            raise ValueError("C2DB material id is invalid")
        response = self.session.get(
            f"{self.base_url}/material/{material_id}",
            timeout=self.timeout_seconds,
        )
        response.raise_for_status()
        marker = "Plotly.newPlot('bandstructure', graphs, {});"
        plot_end = response.text.find(marker)
        if plot_end < 0:
            raise ValueError("C2DB material page is missing the PBE band plot")
        assignment = response.text.rfind("var graphs = ", 0, plot_end)
        if assignment < 0:
            raise ValueError("C2DB PBE band plot is missing its Plotly data")
        json_start = assignment + len("var graphs = ")
        try:
            graph, _ = json.JSONDecoder().raw_decode(
                response.text[json_start:plot_end]
            )
        except json.JSONDecodeError as exc:
            raise ValueError("C2DB PBE band Plotly data is invalid JSON") from exc
        if not isinstance(graph, dict) or not isinstance(graph.get("data"), list):
            raise TypeError("C2DB PBE band Plotly object is missing data[]")
        return {
            "material_id": material_id,
            "source_url": getattr(
                response, "url", f"{self.base_url}/material/{material_id}"
            ),
            "method": "GPAW/PBE",
            "plotly": graph,
        }


class TopologicalQuantumChemistryAdapter:
    """Versioned public search/detail API adapter for the TQC database."""

    is_mock = False
    source_database = SourceDatabase.TOPOLOGICAL_QUANTUM_CHEMISTRY

    def __init__(
        self,
        *,
        api_base_url: str = "https://www.topologicalquantumchemistry.fr/api",
        session: requests.Session | None = None,
        timeout_seconds: float = 30.0,
    ) -> None:
        self.api_base_url = api_base_url.rstrip("/")
        self.session = session or requests.Session()
        self.timeout_seconds = timeout_seconds
        self._metadata: dict[str, dict[str, Any]] = {}

    def metadata(self) -> SourceMetadata:
        payload = _response_json(
            self.session.get(
                f"{self.api_base_url}/v4/search/_search/",
                params={"page": 0},
                timeout=self.timeout_seconds,
            ),
            "TQC search metadata",
        )
        if not isinstance(payload.get("items"), list):
            raise ValueError("TQC search metadata is missing items[]")  # noqa: TRY004
        return SourceMetadata(
            database_version="api:v4-search;v1-detail",
            client_version=importlib.metadata.version("requests"),
            available_fields=sorted(MULTI_SOURCE_REQUIRED_FIELDS),
        )

    def search(self, plan: RetrievalQueryPlan) -> list[dict[str, Any]]:
        if plan.source_database is not SourceDatabase.TOPOLOGICAL_QUANTUM_CHEMISTRY:
            raise ValueError("TQC adapter received a non-TQC query plan")
        filters = plan.pushdown_filters
        params: dict[str, Any] = {
            "include": " ".join(filters.get("elements", [])),
            "exclude": " ".join(filters.get("exclude_elements", [])),
            "option": "contains",
            "filter": "ALL",
            "sort_direction": "asc",
            "sort_by": "compound_complexity",
        }
        icsd_ids: list[str] = []
        search_items: dict[str, dict[str, Any]] = {}
        page = 0
        total_pages = 1
        while page < total_pages and len(icsd_ids) < plan.max_records_scanned:
            response_payload = _response_json(
                self.session.get(
                    f"{self.api_base_url}/v4/search/_search/",
                    params={**params, "page": page},
                    timeout=self.timeout_seconds,
                ),
                "TQC search",
            )
            items = response_payload.get("items")
            if not isinstance(items, list):
                raise ValueError("TQC search response is missing items[]")  # noqa: TRY004
            total_pages_raw = response_payload.get("totalPages")
            if not isinstance(total_pages_raw, int) or total_pages_raw < 0:
                raise ValueError("TQC search response has invalid totalPages")
            total_pages = total_pages_raw
            for item in items:
                if not isinstance(item, dict):
                    raise ValueError("TQC search response contains a non-object item")  # noqa: TRY004
                ids = item.get("similarICSD")
                if not isinstance(ids, list):
                    raise ValueError("TQC search item is missing similarICSD[]")  # noqa: TRY004
                for value in ids:
                    identifier = str(value)
                    search_items.setdefault(identifier, dict(item))
                    if identifier not in icsd_ids:
                        icsd_ids.append(identifier)
                    if len(icsd_ids) >= plan.max_records_scanned:
                        break
                if len(icsd_ids) >= plan.max_records_scanned:
                    break
            page += 1
        return sorted(
            [
                self._detail(identifier, search_response=search_items.get(identifier))
                for identifier in icsd_ids[: plan.max_records_scanned]
            ],
            key=lambda item: str(item["material_id"]),
        )

    def _detail(
        self,
        icsd_id: str,
        *,
        search_response: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        payload = _response_json(
            self.session.get(
                f"{self.api_base_url}/v1/compounds/icsd={icsd_id}/",
                timeout=self.timeout_seconds,
            ),
            f"TQC ICSD {icsd_id}",
        )
        cif = payload.get("cifContent")
        cif_text = (
            cif.get("modifiedCifContent") if isinstance(cif, dict) else None
        )
        cif_url = payload.get("cifFile")
        if isinstance(cif_text, str):
            cif_text = cif_text.replace("\\'", "'")
        if not isinstance(cif_text, str) or not cif_text.strip():
            cif_text = None
        structure: Structure | None = None
        if cif_text is not None:
            try:
                structure = Structure.from_str(cif_text, fmt="cif")
            except (KeyError, TypeError, ValueError):
                structure = None
        if structure is None and isinstance(cif_url, str):
            response = self.session.get(cif_url, timeout=self.timeout_seconds)
            response.raise_for_status()
            try:
                structure = Structure.from_str(response.text, fmt="cif")
            except (KeyError, TypeError, ValueError):
                structure = None
        if structure is None and cif_text is None and not isinstance(cif_url, str):
            raise ValueError(f"TQC ICSD {icsd_id} is missing CIF content")
        atom_compounds = payload.get("atomCompounds")
        atom_items = (
            atom_compounds.get("items")
            if isinstance(atom_compounds, dict)
            else None
        )
        elements = sorted(
            {
                str(item["atom"]["symbol"])
                for item in atom_items or []
                if isinstance(item, dict)
                and isinstance(item.get("atom"), dict)
                and item["atom"].get("symbol")
            }
        )
        if structure is not None:
            elements = sorted({_element_symbol(site.specie) for site in structure})
        if not elements:
            raise ValueError(f"TQC ICSD {icsd_id} is missing element identities")
        topological = payload.get("topologicalClassification")
        subclass = payload.get("topologicalSubClassification")
        task_id = f"tqc:{payload.get('id', icsd_id)}"
        self._metadata[task_id] = {
            "task_id": task_id,
            "run_type": "TQC band representation analysis",
            "task_type": (
                topological.get("shortDescription")
                if isinstance(topological, dict)
                else None
            ),
            "calc_type": (
                subclass.get("shortDescription")
                if isinstance(subclass, dict)
                else None
            ),
        }
        return {
            "material_id": f"icsd-{icsd_id}",
            "formula_pretty": str(
                payload.get("chemicalFormulaSum")
                or (
                    structure.composition.reduced_formula
                    if structure is not None
                    else "unknown"
                )
            ),
            "elements": elements,
            "nelements": len(elements),
            "nsites": (
                len(structure)
                if structure is not None
                else payload.get("nbrAtoms")
            ),
            "structure": structure.as_dict() if structure is not None else None,
            "band_gap": None,
            "energy_above_hull": None,
            "is_metal": None,
            "deprecated": False,
            "theoretical": True,
            "origins": [{"name": "structure", "task_id": task_id}],
            "last_updated": None,
            "source_provenance": {
                "icsd_id": icsd_id,
                "tqc_compound_id": payload.get("id"),
                "topological_classification": topological,
                "topological_subclassification": subclass,
                "topological_indices": payload.get("indexCompounds"),
                "soc": payload.get("type") == "COMPOUND_SOC",
                # TQC exposes crossing counts/labels but not the underlying
                # energy-vs-k arrays.  Preserve the values as database
                # diagnostics; Agent01 must not infer which band crosses.
                "fermi_crossing_count": _optional_nonnegative_int(
                    payload.get("nbrFermiCrossing")
                ),
                "fermi_crossing_first_conduction": _optional_nonnegative_int(
                    payload.get("nbrFermiCrossingFirstCond")
                ),
                "fermi_crossing_last_valence": _optional_nonnegative_int(
                    payload.get("nbrFermiCrossingLastVal")
                ),
                "line_crossing_label": _optional_scalar_label(
                    payload.get("smLineCrossing")
                ),
                "crossing_type_label": _optional_scalar_label(
                    payload.get("smCrossingType")
                ),
                "structure_parse_status": (
                    "parsed" if structure is not None else "invalid_cif"
                ),
            },
            "source_response": {
                "search_item": search_response,
                "detail": payload,
                "cif_content": cif_text,
            },
        }

    def resolve_task_metadata(
        self, task_ids: Sequence[str], material_ids: Sequence[str], batch_size: int
    ) -> tuple[dict[str, dict[str, Any]], list[str]]:
        del material_ids, batch_size
        resolved = {
            task_id: self._metadata[task_id]
            for task_id in sorted(set(task_ids))
            if task_id in self._metadata
        }
        missing = sorted(set(task_ids) - set(resolved))
        return resolved, (
            [f"TQC method metadata unavailable for {len(missing)} records"]
            if missing
            else []
        )


class NimsSuperconAdapter:
    """Metadata-only SuperCon adapter; records intentionally lack structures."""

    is_mock = False
    source_database = SourceDatabase.NIMS_SUPERCON
    DATASET_ID = "650c4826-f0ca-42e8-8dd9-94025a5307ce"
    DATASET_DOI = "10.48505/nims.4487"
    DATA_FILE_ID = "2347b413-9c15-43b7-90e6-41fe9243b1a5"

    def __init__(
        self,
        *,
        base_url: str = "https://mdr.nims.go.jp",
        session: requests.Session | None = None,
        timeout_seconds: float = 60.0,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.session = session or requests.Session()
        self.timeout_seconds = timeout_seconds

    def metadata(self) -> SourceMetadata:
        response = self.session.get(
            f"{self.base_url}/datasets/{self.DATASET_ID}",
            timeout=self.timeout_seconds,
        )
        response.raise_for_status()
        if self.DATASET_DOI not in response.text:
            raise ValueError("NIMS SuperCon dataset identity changed")
        return SourceMetadata(
            database_version=f"doi:{self.DATASET_DOI};version:240322",
            client_version=importlib.metadata.version("requests"),
            available_fields=sorted(MULTI_SOURCE_REQUIRED_FIELDS),
        )

    def search(self, plan: RetrievalQueryPlan) -> list[dict[str, Any]]:
        if plan.source_database is not SourceDatabase.NIMS_SUPERCON:
            raise ValueError("NIMS adapter received a non-NIMS query plan")
        response = self.session.get(
            f"{self.base_url}/filesets/{self.DATA_FILE_ID}/download",
            timeout=self.timeout_seconds,
        )
        response.raise_for_status()
        reader = csv.reader(io.StringIO(response.text), delimiter="\t")
        try:
            verbose_header = next(reader)
            field_keys = next(reader)
        except StopIteration:
            return []
        if not verbose_header or not field_keys or field_keys[0] != "num":
            raise ValueError("NIMS SuperCon two-row header schema drifted")
        key_index = {key: index for index, key in enumerate(field_keys) if key}
        filters = plan.pushdown_filters
        required = set(filters.get("elements", []))
        excluded = set(filters.get("exclude_elements", []))
        documents: list[dict[str, Any]] = []
        for values in reader:
            def value(key: str, row: list[str] = values) -> str:
                index = key_index.get(key)
                return (
                    str(row[index]).strip()
                    if index is not None and index < len(row)
                    else ""
                )

            elements = {
                value(key)
                for key in (
                    "ma1", "mb1", "mc1", "md1", "me1", "mf1",
                    "mg1", "mh1", "mi1", "mj1", "mo1",
                )
            }
            elements.discard("")
            if required and not required.issubset(elements):
                continue
            if excluded & elements:
                continue
            record_id = value("num")
            if not record_id:
                continue
            documents.append(
                {
                    "material_id": f"supercon-{record_id}",
                    "formula_pretty": (
                        value("element")
                        or value("name")
                        or "unknown"
                    ),
                    "elements": sorted(elements),
                    "nelements": len(elements),
                    "nsites": None,
                    "structure": None,
                    "band_gap": None,
                    "energy_above_hull": None,
                    "is_metal": None,
                    "deprecated": False,
                    "theoretical": False,
                    "origins": [],
                    "last_updated": None,
                    "source_provenance": {
                        "dataset_doi": self.DATASET_DOI,
                        "record_id": record_id,
                        "reference_number": value("refno"),
                        "recommended_tc": value("tc") or None,
                        "tc_unit": value("utc") or None,
                        "license": "CC-BY-4.0",
                        "structure_limitation": (
                            "SuperCon datasheet has no atomic coordinates"
                        ),
                    },
                }
            )
            if len(documents) >= plan.max_records_scanned:
                break
        return sorted(documents, key=lambda item: str(item["material_id"]))

    def resolve_task_metadata(
        self, task_ids: Sequence[str], material_ids: Sequence[str], batch_size: int
    ) -> tuple[dict[str, dict[str, Any]], list[str]]:
        del task_ids, material_ids, batch_size
        return {}, []


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
        report_payloads: dict[str, dict[str, Any]] | None = None,
    ) -> None:
        self.source_database = SourceDatabase(source_database)
        self.documents = [dict(document) for document in documents]
        self.database_version = database_version
        self.task_metadata = task_metadata or {}
        self.honor_filters = honor_filters
        default_fields = (
            CORE_FIELDS
            if self.source_database is SourceDatabase.MATERIALS_PROJECT
            else (
                NOMAD_REQUIRED_FIELDS
                if self.source_database is SourceDatabase.NOMAD
                else MULTI_SOURCE_REQUIRED_FIELDS
            )
        )
        self.available_fields = sorted(available_fields or default_fields)
        self.report_payloads = report_payloads or {}

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

    def fetch_report_data(self, material_id: str, *, heavy: bool) -> dict[str, Any]:
        del heavy
        payload = self.report_payloads.get(material_id, {})
        return {"material_id": material_id, "endpoints": {}, "errors": {}, **payload}

    def fetch_deep_screen_data(
        self, material_id: str, *, endpoints: Sequence[DeepEndpoint]
    ) -> dict[str, Any]:
        payload = self.report_payloads.get(material_id, {})
        deep = payload.get("deep_screen", {}) if isinstance(payload, dict) else {}
        return {
            "material_id": material_id,
            "payloads": dict(deep.get("payloads", {})),
            "objects": dict(deep.get("objects", {})),
            "errors": dict(deep.get("errors", {})),
        }


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
    return (
        filters.get("theoretical") is None
        or document.get("theoretical") == filters["theoretical"]
    )


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
        raise ValueError(f"{operation} returned a non-object response")  # noqa: TRY004
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


def _optimade_filter(filters: dict[str, Any]) -> str:
    clauses: list[str] = []
    required = filters.get("elements", [])
    if required:
        values = ",".join(f'"{value}"' for value in required)
        clauses.append(f"elements HAS ALL {values}")
    excluded = filters.get("exclude_elements", [])
    if excluded:
        values = ",".join(f'"{value}"' for value in excluded)
        clauses.append(f"NOT elements HAS ANY {values}")
    bounds = filters.get("num_sites")
    if isinstance(bounds, (list, tuple)) and len(bounds) == 2:
        clauses.append(f"nsites >= {int(bounds[0])}")
        clauses.append(f"nsites <= {int(bounds[1])}")
    return " AND ".join(clauses)


def _map_optimade_structure(
    entry: dict[str, Any], method: str
) -> dict[str, Any]:
    identifier = entry.get("id")
    attributes = entry.get("attributes")
    if not isinstance(identifier, str) or not identifier:
        raise ValueError("OPTIMADE structure is missing id")
    if not isinstance(attributes, dict):
        raise ValueError(f"OPTIMADE structure {identifier} is missing attributes")  # noqa: TRY004
    lattice = attributes.get("lattice_vectors")
    positions = attributes.get("cartesian_site_positions")
    species = attributes.get("species_at_sites")
    if not isinstance(lattice, list) or not isinstance(positions, list):
        raise ValueError(f"OPTIMADE structure {identifier} is missing coordinates")  # noqa: TRY004
    if not isinstance(species, list) or len(species) != len(positions):
        raise ValueError(f"OPTIMADE structure {identifier} has invalid species")
    try:
        structure = Structure(
            Lattice(lattice),
            [str(value) for value in species],
            positions,
            coords_are_cartesian=True,
            to_unit_cell=False,
        )
    except (TypeError, ValueError) as exc:
        raise ValueError(
            f"OPTIMADE structure {identifier} could not be parsed"
        ) from exc
    elements = attributes.get("elements")
    if not isinstance(elements, list):
        elements = sorted({str(site.specie) for site in structure})
    task_id = f"{method}:{identifier}"
    return {
        "material_id": identifier,
        "formula_pretty": (
            attributes.get("chemical_formula_descriptive")
            or attributes.get("chemical_formula_reduced")
            or structure.composition.reduced_formula
        ),
        "elements": sorted(str(value) for value in elements),
        "nelements": len({str(value) for value in elements}),
        "nsites": len(structure),
        "structure": structure.as_dict(),
        "band_gap": None,
        "energy_above_hull": None,
        "is_metal": None,
        "deprecated": False,
        "theoretical": True,
        "origins": [{"name": "structure", "task_id": task_id}],
        "last_updated": attributes.get("last_modified"),
        "source_provenance": {
            "optimade_id": identifier,
            "optimade_method": method,
        },
    }


def _c2db_params(filters: dict[str, Any]) -> dict[str, str]:
    terms = [str(value) for value in filters.get("elements", [])]
    terms.extend(
        f"{value}=0" for value in filters.get("exclude_elements", [])
    )
    params = {
        "filter": ",".join(terms),
        "from_ehull": "-1000000",
        "to_ehull": "1000000",
        "bg": "gap",
    }
    gap = filters.get("band_gap")
    if isinstance(gap, (list, tuple)) and len(gap) == 2:
        params["from_bg"] = str(gap[0])
        params["to_bg"] = str(gap[1])
    hull = filters.get("energy_above_hull")
    if isinstance(hull, (list, tuple)) and len(hull) == 2:
        params["from_ehull"] = str(hull[0])
        params["to_ehull"] = str(hull[1])
    sites = filters.get("num_sites")
    if isinstance(sites, (list, tuple)) and len(sites) == 2:
        terms.append(f"natoms<={int(sites[1])}")
        params["filter"] = ",".join(terms)
    return params


def _c2db_row_matches_predownload_filters(
    row: Mapping[str, str], filters: Mapping[str, Any]
) -> bool:
    """Apply only exact, listing-derived filters before structure downloads."""

    exact_formula = filters.get("exact_formula")
    if exact_formula is None:
        return True
    row_formula = row.get("formula")
    if not isinstance(exact_formula, str) or not isinstance(row_formula, str):
        raise ValueError("C2DB exact-formula predownload filter is invalid")  # noqa: TRY004
    try:
        expected = Composition(exact_formula).reduced_composition
        observed = Composition(row_formula).reduced_composition
    except ValueError as exc:
        raise ValueError("C2DB table contains an invalid formula") from exc
    return observed == expected


def _parse_c2db_table(
    text: str,
) -> tuple[list[dict[str, str]], str, bool]:
    sid_match = re.search(r'name="sid" value="([^"]+)"', text)
    if sid_match is None:
        raise ValueError("C2DB table response is missing search session id")
    sid = sid_match.group(1)
    rows: list[dict[str, str]] = []
    for raw_row in re.findall(r"<tr[^>]*>(.*?)</tr>", text, flags=re.DOTALL):
        hrefs = re.findall(r"href=/material/([^ >]+)", raw_row)
        if not hrefs:
            continue
        cells = [
            html.unescape(
                re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", cell))
            ).strip()
            for cell in re.findall(
                r'<th scope="row">(.*?)</th>', raw_row, flags=re.DOTALL
            )
        ]
        if len(cells) < 6:
            raise ValueError("C2DB result row has fewer than six columns")
        rows.append(
            {
                "uid": hrefs[0],
                "formula": cells[0],
                "energy_above_hull": cells[1],
                "heat_of_formation": cells[2],
                "band_gap": cells[3],
                "magnetic": cells[4],
                "layer_group": cells[5],
            }
        )
    has_next = not bool(
        re.search(
            r'<li class="page-item disabled">\s*<a class="page-link"\s*'
            rf'hx-get="/table\?sid={re.escape(sid)}&page=\d+"[^>]*title=">"',
            text,
            flags=re.DOTALL,
        )
    )
    return rows, sid, has_next


def _ase_json_structure(atoms: dict[str, Any]) -> Structure:
    numbers = atoms.get("numbers")
    positions = atoms.get("positions")
    cell = atoms.get("cell")
    if not isinstance(numbers, list) or not isinstance(positions, list):
        raise ValueError("C2DB atoms record is missing numbers/positions")  # noqa: TRY004
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


def _optional_float(value: Any) -> float | None:
    if value is None or str(value).strip() == "":
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def _optional_nonnegative_int(value: Any) -> int | None:
    """Accept a finite, integral, non-negative public database count only."""

    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(numeric) or numeric < 0 or not numeric.is_integer():
        return None
    return int(numeric)


def _optional_scalar_label(value: Any) -> str | None:
    """Store a scalar database label without accepting nested API objects."""

    if isinstance(value, str):
        return value.strip() or None
    if isinstance(value, (bool, int)):
        return str(value)
    if isinstance(value, float) and math.isfinite(value):
        return str(value)
    return None


def _element_symbol(specie: Any) -> str:
    """Return a bare element symbol for either pymatgen Element or Species."""

    element = getattr(specie, "element", specie)
    symbol = getattr(element, "symbol", None)
    if not isinstance(symbol, str) or not symbol:
        raise ValueError("structure site has no valid element symbol")
    return symbol


def _safe_keychain_label(value: str, label: str) -> str:
    """Reject control characters before passing a Keychain label to a process."""

    normalized = value.strip()
    if not normalized or any(character in normalized for character in "\r\n\x00"):
        raise ValueError(f"{label} must be a non-empty single-line value")
    return normalized
