from __future__ import annotations

import hashlib
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from material_agent.orchestrator.models import (
    ORCHESTRATOR_CONTRACT_VERSION,
    P01_ORCHESTRATOR_CONTRACT_VERSION,
    RunStatus,
    RuntimeView,
)
from material_agent.orchestrator.runtime import (
    CheckpointCompatibilityError,
    OrchestratorRuntime,
)


class _CountingStore:
    def __init__(self, payload: bytes) -> None:
        self.payload = payload
        self.read_count = 0
        self.read_uris: list[str] = []

    def read_bytes(self, uri: str) -> bytes:
        self.read_count += 1
        self.read_uris.append(uri)
        return self.payload


def _runtime_for_report(
    *,
    payload: bytes,
    checkpoint_schema_version: str = ORCHESTRATOR_CONTRACT_VERSION,
    view_report_uri: str = "artifact://reports/run-report/report.md",
    checkpoint_report_uri: str | None = "artifact://reports/run-report/report.md",
    checkpoint_report_sha256: str | None = None,
) -> tuple[OrchestratorRuntime, _CountingStore, Mock]:
    store = _CountingStore(payload)
    graph = Mock()
    graph.get_state.return_value = SimpleNamespace(
        values={
            "report_uri": checkpoint_report_uri,
            "report_sha256": (
                checkpoint_report_sha256
                if checkpoint_report_sha256 is not None
                else hashlib.sha256(payload).hexdigest()
            ),
        }
    )
    repository = Mock()
    repository.get_run.return_value = {
        "checkpoint_schema_version": checkpoint_schema_version,
    }

    runtime = object.__new__(OrchestratorRuntime)
    runtime.store = store
    runtime.graph = graph
    runtime.repository = repository
    runtime.status = Mock(
        return_value=RuntimeView(
            project_id="project-report",
            run_id="run-report",
            status=RunStatus.SUCCEEDED,
            report_uri=view_report_uri,
        )
    )
    return runtime, store, graph


def test_current_report_is_read_once_and_verified_against_checkpoint() -> None:
    payload = b"# verified report\n"
    runtime, store, graph = _runtime_for_report(payload=payload)

    assert runtime.read_report("run-report") == payload.decode("utf-8")
    assert store.read_count == 1
    assert store.read_uris == ["artifact://reports/run-report/report.md"]
    graph.get_state.assert_called_once_with(
        {"configurable": {"thread_id": "run-report"}}
    )


def test_current_report_rejects_content_tampering_after_one_read() -> None:
    original = b"# original report\n"
    runtime, store, _graph = _runtime_for_report(
        payload=b"# tampered report\n",
        checkpoint_report_sha256=hashlib.sha256(original).hexdigest(),
    )

    with pytest.raises(ValueError, match="failed integrity validation"):
        runtime.read_report("run-report")

    assert store.read_count == 1


def test_current_report_rejects_database_uri_redirection_before_read() -> None:
    runtime, store, graph = _runtime_for_report(
        payload=b"not a report",
        view_report_uri="artifact://project.json",
    )

    with pytest.raises(ValueError, match="unexpected artifact path"):
        runtime.read_report("run-report")

    assert store.read_count == 0
    graph.get_state.assert_not_called()


def test_current_report_rejects_checkpoint_uri_redirection_before_read() -> None:
    runtime, store, _graph = _runtime_for_report(
        payload=b"not a report",
        checkpoint_report_uri="artifact://reports/other-run/report.md",
    )

    with pytest.raises(ValueError, match="checkpoint URI"):
        runtime.read_report("run-report")

    assert store.read_count == 0


@pytest.mark.parametrize("checkpoint_sha256", [None, "not-a-sha256"])
def test_current_report_rejects_missing_or_invalid_checkpoint_sha_before_read(
    checkpoint_sha256: str | None,
) -> None:
    runtime, store, graph = _runtime_for_report(payload=b"# report\n")
    graph.get_state.return_value.values["report_sha256"] = checkpoint_sha256

    with pytest.raises(ValueError, match="SHA-256 is missing or invalid"):
        runtime.read_report("run-report")

    assert store.read_count == 0


def test_completed_p01_report_remains_readable_without_checkpoint_hash() -> None:
    payload = b"# Frozen Orchestrator P0.1 report\n"
    runtime, store, graph = _runtime_for_report(
        payload=payload,
        checkpoint_schema_version=P01_ORCHESTRATOR_CONTRACT_VERSION,
        checkpoint_report_uri=None,
        checkpoint_report_sha256=None,
    )

    assert runtime.read_report("run-report") == payload.decode("utf-8")
    assert store.read_count == 1
    graph.get_state.assert_not_called()


def test_unknown_checkpoint_schema_cannot_bypass_report_integrity() -> None:
    runtime, store, graph = _runtime_for_report(
        payload=b"# unverified report\n",
        checkpoint_schema_version="orchestrator-unknown-v99",
    )

    with pytest.raises(CheckpointCompatibilityError, match="schema is unknown"):
        runtime.read_report("run-report")

    assert store.read_count == 0
    graph.get_state.assert_not_called()
