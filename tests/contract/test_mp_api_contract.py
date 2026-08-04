from __future__ import annotations

import inspect
import subprocess
from types import SimpleNamespace

import pytest

from mp_api.client.routes.materials.summary import SummaryRester
from mp_api.client.routes.materials.tasks import TaskRester
from mp_api.client import MPRester

from material_agent.retrieval.adapters import MaterialsProjectAdapter
from material_agent.retrieval.query import assert_mp_client_contract, build_query_plan


MP_SECRET = "super-secret-mp-test-key"


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


def test_mp_secret_resolution_prefers_explicit_key_over_environment_and_keychain() -> None:
    def forbidden_runner(*_args, **_kwargs):
        raise AssertionError("Keychain must not be called")

    adapter = MaterialsProjectAdapter(
        api_key="explicit-key",
        environment={"MP_API_KEY": "environment-key"},
        keychain_account="test-account",
        command_runner=forbidden_runner,
    )

    assert adapter._resolve_api_key() == "explicit-key"


def test_mp_secret_resolution_prefers_environment_over_keychain() -> None:
    def forbidden_runner(*_args, **_kwargs):
        raise AssertionError("Keychain must not be called")

    adapter = MaterialsProjectAdapter(
        environment={"MP_API_KEY": "environment-key"},
        keychain_account="test-account",
        command_runner=forbidden_runner,
    )

    assert adapter._resolve_api_key() == "environment-key"


def test_mp_secret_resolution_reads_keychain_without_shell_or_secret_logging() -> None:
    calls: list[tuple[list[str], dict]] = []

    def runner(command: list[str], **kwargs):
        calls.append((command, kwargs))
        return subprocess.CompletedProcess(command, 0, stdout=f"{MP_SECRET}\n", stderr="")

    adapter = MaterialsProjectAdapter(
        environment={},
        keychain_service="material-screening-agent-mp-api",
        keychain_account="test-account",
        command_runner=runner,
    )

    assert adapter._resolve_api_key() == MP_SECRET
    assert calls == [
        (
            [
                "security",
                "find-generic-password",
                "-a",
                "test-account",
                "-s",
                "material-screening-agent-mp-api",
                "-w",
            ],
            {
                "check": False,
                "capture_output": True,
                "text": True,
                "timeout": 5,
            },
        )
    ]


def test_mp_secret_resolution_fails_closed_when_keychain_has_no_credential() -> None:
    adapter = MaterialsProjectAdapter(
        environment={},
        keychain_account="test-account",
        command_runner=lambda command, **_kwargs: subprocess.CompletedProcess(
            command, 44, stdout="", stderr="not found"
        ),
    )

    with pytest.raises(RuntimeError, match="unavailable from the configured secret sources"):
        adapter._resolve_api_key()


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
    assert documents[0]["source_response"]["material_id"] == "mp-1"
    assert set(plan.requested_fields).issuperset(adapter.metadata().available_fields)
    assert "_sort_fields" not in fake_client.materials.summary.kwargs
