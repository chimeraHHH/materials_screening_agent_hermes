from __future__ import annotations

import json

import pytest

from material_agent.dft.bridge_models import BridgeBackendDescriptor
from material_agent.dft.bridge_transport import (
    BridgeConflictError,
    BridgeProtocolError,
    UrllibBridgeTransport,
)
from material_agent.dft.fake_bridge import FakeVASPilotBridgeTransport
from material_agent.dft.protocol import DFTBackend
from material_agent.dft.runner import DFTStageRunner
from material_agent.dft.vaspilot_backend import VASPilotBackend
from material_agent.orchestrator.models import StageStatus
from tests.contract.test_dft_mock_backend import make_request
from tests.unit.test_dft_runner import _setup


def _descriptor() -> BridgeBackendDescriptor:
    return BridgeBackendDescriptor(
        backend_id="mock-dft",
        backend_version="fake-bridge-1.0.0",
        adapter_version="agent03-vaspilot-adapter-v1",
        is_mock=True,
        supported_task_types=("MOCK_TASK",),
    )


def _backend(
    *,
    lose_first_submit_response: bool = False,
) -> tuple[VASPilotBackend, FakeVASPilotBridgeTransport]:
    descriptor = _descriptor()
    transport = FakeVASPilotBridgeTransport(
        descriptor=descriptor,
        lose_first_submit_response=lose_first_submit_response,
    )
    return (
        VASPilotBackend(
            transport=transport,
            descriptor=descriptor,
        ),
        transport,
    )


def test_bridge_health_capability_and_protocol_are_strict() -> None:
    backend, _transport = _backend()

    assert isinstance(backend, DFTBackend)
    assert backend.health().status == "READY"
    assert backend.capability() == _descriptor()


def test_submit_is_server_side_idempotent_and_conflict_is_rejected() -> None:
    backend, transport = _backend()
    request = make_request()

    first = backend.submit(request, "bridge-idempotency-key")
    second = backend.submit(request, "bridge-idempotency-key")

    assert first == second
    assert transport.workflow_count == 1
    changed = request.model_copy(update={"request_id": "dftreq_changed"})
    with pytest.raises(BridgeConflictError):
        backend.submit(changed, "bridge-idempotency-key")
    assert transport.workflow_count == 1


def test_submit_response_loss_recovers_by_idempotency_key() -> None:
    backend, transport = _backend(lose_first_submit_response=True)

    ref = backend.submit(make_request(), "response-lost-after-acceptance")

    assert ref.backend_task_id.startswith("fakewf_")
    assert transport.submit_calls == 1
    assert transport.workflow_count == 1


def test_status_cancel_result_and_artifact_manifest_round_trip() -> None:
    backend, transport = _backend()
    request = make_request()
    ref = backend.submit(request, "lifecycle")

    assert backend.status(ref).value == "QUEUED"
    assert backend.status(ref).value == "RUNNING"
    assert backend.status(ref).value == "SUCCEEDED"
    result = backend.fetch_result(ref)

    assert result.is_mock is True
    assert result.external_job_ref == ref
    assert result.workflow_plan_hash == request.workflow_plan_hash
    assert result.output_artifacts
    assert result.claim_results[0].status.value == "NOT_EVALUATED_MOCK"
    serialized = json.dumps(result.model_dump(mode="json"))
    assert "band_gap" not in serialized
    assert "total_energy" not in serialized
    assert transport.workflow_count == 1

    cancel_backend, _cancel_transport = _backend()
    cancel_ref = cancel_backend.submit(request, "cancel-lifecycle")
    assert cancel_backend.cancel(cancel_ref).value == "CANCEL_CONFIRMED"
    assert cancel_backend.status(cancel_ref).value == "CANCELLED"
    assert (
        cancel_backend.fetch_result(cancel_ref).terminal_status.value
        == "CANCELLED"
    )


class _MutatingTransport:
    def __init__(self, delegate, mutate):
        self.delegate = delegate
        self.mutate = mutate

    def request(self, method, path, body=None):
        payload = self.delegate.request(method, path, body)
        return self.mutate(method, path, payload)


def test_hash_and_artifact_manifest_conflicts_fail_closed() -> None:
    descriptor = _descriptor()
    base = FakeVASPilotBridgeTransport(descriptor=descriptor)

    def corrupt_submit(method, path, payload):
        if method == "POST" and path.endswith("/dft-workflows"):
            payload["workflow_plan_hash"] = "f" * 64
        return payload

    backend = VASPilotBackend(
        transport=_MutatingTransport(base, corrupt_submit),
        descriptor=descriptor,
    )
    with pytest.raises(BridgeProtocolError):
        backend.submit(make_request(), "corrupt-plan-hash")

    good, good_transport = _backend()
    ref = good.submit(make_request(), "corrupt-artifacts")
    for _ in range(3):
        good.status(ref)

    def corrupt_manifest(method, path, payload):
        if path.endswith("/artifacts"):
            payload["task_input_hash"] = "e" * 64
        return payload

    corrupt = VASPilotBackend(
        transport=_MutatingTransport(good_transport, corrupt_manifest),
        descriptor=descriptor,
    )
    with pytest.raises(BridgeProtocolError):
        corrupt.fetch_result(ref)


def test_runner_accepts_protocol_backend_without_enabling_real_dft(
    tmp_path,
) -> None:
    store, original_runner, context = _setup(tmp_path)
    backend, transport = _backend()
    runner = DFTStageRunner(
        artifact_store=store,
        capability=original_runner.capability,
        backend=backend,
        now=original_runner.now,
    )
    prepared = runner.prepare(context)
    waiting = runner.start(
        context,
        prepared,
        "bridge-backed-mock-operation",
    )

    assert waiting.status is StageStatus.RUNNING
    for _ in range(3):
        terminal = runner.reconcile(
            context,
            prepared,
            waiting.external_job_ref or "",
            waiting.idempotency_key,
        )
    assert terminal.status is StageStatus.SUCCEEDED
    assert terminal.summary["is_mock"] is True
    assert transport.workflow_count == 1


def test_http_transport_rejects_unsafe_configuration_and_paths() -> None:
    with pytest.raises(ValueError, match="HTTPS"):
        UrllibBridgeTransport(
            base_url="http://cluster.example/bridge",
            bearer_token="test-token",
        )
    with pytest.raises(ValueError, match="credentials"):
        UrllibBridgeTransport(
            base_url="https://user:pass@cluster.example/bridge",
            bearer_token="test-token",
        )
    with pytest.raises(ValueError, match="token"):
        UrllibBridgeTransport(
            base_url="https://cluster.example/bridge",
            bearer_token="",
        )
    transport = UrllibBridgeTransport(
        base_url="http://127.0.0.1:8934",
        bearer_token="test-token",
    )
    with pytest.raises(ValueError, match="traversal"):
        transport.request("GET", "/integration/../secrets")
