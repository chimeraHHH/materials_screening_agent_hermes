from __future__ import annotations

import pytest
from pydantic import ValidationError

from material_agent.dft.bridge_models import (
    BridgeBackendDescriptor,
    BridgeSubmitRequest,
    BridgeWorkflowRecord,
)
from material_agent.dft.models import canonical_hash
from tests.contract.test_dft_mock_backend import make_request


def _descriptor() -> BridgeBackendDescriptor:
    return BridgeBackendDescriptor(
        backend_id="mock-dft",
        backend_version="fake-bridge-1.0.0",
        adapter_version="agent03-vaspilot-adapter-v1",
        is_mock=True,
        supported_task_types=("MOCK_TASK",),
    )


def test_submit_contract_binds_exact_request_hash_and_forbids_extra() -> None:
    request = make_request()
    payload = BridgeSubmitRequest(
        idempotency_key="contract-key",
        request_sha256=canonical_hash(request),
        request=request,
    ).model_dump(mode="json")

    assert BridgeSubmitRequest.model_validate(payload).request == request
    with pytest.raises(ValidationError, match="request_sha256"):
        BridgeSubmitRequest.model_validate(
            {**payload, "request_sha256": "f" * 64}
        )
    with pytest.raises(ValidationError, match="extra"):
        BridgeSubmitRequest.model_validate(
            {**payload, "unapproved_parameter": True}
        )


def test_terminal_workflow_requires_a_matching_result() -> None:
    request = make_request()
    payload = {
        "descriptor": _descriptor().model_dump(mode="json"),
        "workflow_id": "fakewf_contract",
        "idempotency_key": "contract-key",
        "request_id": request.request_id,
        "request_sha256": canonical_hash(request),
        "workflow_plan_hash": request.workflow_plan_hash,
        "upstream_snapshot_hash": request.upstream_snapshot_hash,
        "task_input_hash": request.task_specs[0].task_input_hash,
        "status": "SUCCEEDED",
        "raw_status": "fake:SUCCEEDED",
        "is_mock": True,
    }

    with pytest.raises(ValidationError, match="terminal"):
        BridgeWorkflowRecord.model_validate(payload)


def test_production_descriptor_cannot_be_used_as_fake_fixture() -> None:
    from material_agent.dft.fake_bridge import (
        FakeVASPilotBridgeTransport,
    )

    descriptor = _descriptor().model_copy(update={"is_mock": False})
    with pytest.raises(ValueError, match="is_mock=true"):
        FakeVASPilotBridgeTransport(descriptor=descriptor)
