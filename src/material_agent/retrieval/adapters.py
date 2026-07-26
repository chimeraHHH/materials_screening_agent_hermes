"""Materials-source adapters."""

from __future__ import annotations

import importlib.metadata
import os
from collections.abc import Sequence
from typing import Any, Protocol

from material_agent.retrieval.models import RetrievalQueryPlan, SourceMetadata
from material_agent.retrieval.query import CORE_FIELDS, assert_mp_client_contract


class MaterialsSourceAdapter(Protocol):
    is_mock: bool

    def metadata(self) -> SourceMetadata: ...

    def search(self, plan: RetrievalQueryPlan) -> list[dict[str, Any]]: ...

    def resolve_task_metadata(
        self, task_ids: Sequence[str], material_ids: Sequence[str], batch_size: int
    ) -> tuple[dict[str, dict[str, Any]], list[str]]: ...


class MaterialsProjectAdapter:
    """Official mp-api backed adapter with no persisted credentials."""

    is_mock = False

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
    ) -> None:
        self.documents = [dict(document) for document in documents]
        self.database_version = database_version
        self.task_metadata = task_metadata or {}
        self.honor_filters = honor_filters
        self.available_fields = sorted(available_fields or CORE_FIELDS)

    def metadata(self) -> SourceMetadata:
        return SourceMetadata(
            database_version=self.database_version,
            client_version="fixture-adapter-v1",
            available_fields=self.available_fields,
        )

    def search(self, plan: RetrievalQueryPlan) -> list[dict[str, Any]]:
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
