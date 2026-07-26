from __future__ import annotations

import inspect
from types import SimpleNamespace

from mp_api.client.routes.materials.summary import SummaryRester
from mp_api.client.routes.materials.tasks import TaskRester
from mp_api.client import MPRester

from material_agent.retrieval.adapters import MaterialsProjectAdapter
from material_agent.retrieval.query import assert_mp_client_contract, build_query_plan


def test_locked_summary_client_has_required_public_contract() -> None:
    assert_mp_client_contract(SummaryRester.search)
    parameters = inspect.signature(SummaryRester.search).parameters
    assert "_sort_fields" not in parameters


def test_locked_task_client_can_resolve_origin_metadata() -> None:
    parameters = inspect.signature(TaskRester.search).parameters
    assert "task_ids" in parameters
    assert "fields" in parameters


def test_locked_client_exposes_database_version_fallback() -> None:
    assert callable(MPRester.get_database_version)


def test_live_adapter_uses_only_supported_summary_arguments(
    monkeypatch, requirement, requirement_hash, adapter, policy
) -> None:
    plan = build_query_plan(
        requirement, requirement_hash, adapter.metadata(), policy
    )

    class FakeSummary:
        def __init__(self) -> None:
            self.kwargs = None

        def search(
            self,
            *,
            fields,
            chunk_size,
            num_chunks,
            all_fields,
            include_gnome=True,
            **kwargs,
        ):
            self.kwargs = {
                "fields": fields,
                "chunk_size": chunk_size,
                "num_chunks": num_chunks,
                "all_fields": all_fields,
                "include_gnome": include_gnome,
                **kwargs,
            }
            return [{"material_id": "mp-2"}, {"material_id": "mp-1"}]

    class FakeClient:
        def __init__(self) -> None:
            self.materials = SimpleNamespace(summary=FakeSummary())

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, traceback):
            return False

    fake_client = FakeClient()
    live_adapter = MaterialsProjectAdapter(api_key="not-used")
    monkeypatch.setattr(live_adapter, "_make_client", lambda: fake_client)
    documents = live_adapter.search(plan)

    assert [document["material_id"] for document in documents] == ["mp-1", "mp-2"]
    assert "_sort_fields" not in fake_client.materials.summary.kwargs
