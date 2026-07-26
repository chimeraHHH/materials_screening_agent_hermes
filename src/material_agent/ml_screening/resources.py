"""Deterministic Step-1 policy and fake contract resources."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta
from typing import Any

from material_agent.ml_screening.models import (
    AGENT02_WORKER_PROTOCOL_VERSION,
    ArtifactPointer,
    MLModelRegistry,
    MLModelSpec,
    MLScreeningPolicy,
    MLTask,
    ModelCard,
    ModelHealthSnapshot,
    SmokeTestStatus,
)


def canonical_json_bytes(payload: Any) -> bytes:
    if hasattr(payload, "model_dump"):
        payload = payload.model_dump(mode="json")
    return json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")


def sha256_payload(payload: Any) -> str:
    return hashlib.sha256(canonical_json_bytes(payload)).hexdigest()


def environment_fingerprint_payload(
    *,
    python_version: str,
    installed_package_versions: dict[str, str],
    platform: str,
    architecture: str,
    device_policy: str,
    available_devices: list[str],
    package_lock_sha256: str,
) -> dict[str, Any]:
    return {
        "schema_version": "agent02-environment-fingerprint-v1",
        "python_version": python_version,
        "installed_package_versions": dict(
            sorted(installed_package_versions.items())
        ),
        "platform": platform,
        "architecture": architecture,
        "device_policy": device_policy,
        "available_devices": sorted(available_devices),
        "package_lock_sha256": package_lock_sha256,
    }


def environment_fingerprint_sha256(**kwargs: Any) -> str:
    return sha256_payload(environment_fingerprint_payload(**kwargs))


def artifact_pointer(uri: str, payload: Any) -> ArtifactPointer:
    return ArtifactPointer(uri=uri, sha256=sha256_payload(payload))


def candidate_operation_key(
    *,
    project_id: str,
    run_id: str,
    candidate_id: str,
    input_structure_sha256: str,
    model_id: str,
    checkpoint_sha256: str,
    package_lock_sha256: str,
    environment_fingerprint_sha256_value: str,
    adapter_version: str,
    worker_protocol_version: str,
    device_policy: str,
    policy_sha256: str,
    relaxation_profile: str,
    requested_tasks: list[MLTask],
) -> str:
    return sha256_payload(
        {
            "schema_version": "agent02-candidate-operation-v1",
            "project_id": project_id,
            "run_id": run_id,
            "candidate_id": candidate_id,
            "input_structure_sha256": input_structure_sha256,
            "model_id": model_id,
            "checkpoint_sha256": checkpoint_sha256,
            "package_lock_sha256": package_lock_sha256,
            "environment_fingerprint_sha256": (
                environment_fingerprint_sha256_value
            ),
            "adapter_version": adapter_version,
            "worker_protocol_version": worker_protocol_version,
            "device_policy": device_policy,
            "policy_sha256": policy_sha256,
            "relaxation_profile": relaxation_profile,
            "requested_tasks": sorted(
                task.value if isinstance(task, MLTask) else str(task)
                for task in requested_tasks
            ),
        }
    )


def default_policy() -> MLScreeningPolicy:
    return MLScreeningPolicy()


def fake_model_card() -> ModelCard:
    return ModelCard(
        model_id="chgnet-mptrj-0.3.0",
        display_name="Fake CHGNet contract adapter",
        summary=(
            "A deterministic test double for Agent02 contract tests. "
            "It is not the CHGNet model and produces no scientific evidence."
        ),
        intended_use=[
            "Agent02 native contract tests",
            "deterministic planning and report fixtures",
        ],
        out_of_scope=[
            "scientific inference",
            "L2 evidence",
            "production fallback",
        ],
        training_data="None; deterministic fixture only.",
        limitations=[
            "All values are synthetic.",
            "It must never be registered as a production capability.",
        ],
        evidence_constraints=[
            "Every property carries is_mock=true.",
            "Evidence is capped at L1_RETRIEVED.",
        ],
        sources=["material-screening-ml-agent-plan.md#8.1"],
        is_mock=True,
    )


def fake_model_spec() -> MLModelSpec:
    card = fake_model_card()
    return MLModelSpec(
        model_id=card.model_id,
        adapter_type="fake-contract-adapter",
        adapter_version="agent02-fake-adapter-v1",
        package_name="material-screening-agent",
        package_version="0.1.0",
        checkpoint_name="fake-no-checkpoint",
        checkpoint_artifact_uri=(
            "artifact://contracts/agent02-v1/fake-checkpoint.txt"
        ),
        checkpoint_sha256=hashlib.sha256(
            b"agent02-fake-checkpoint-v1"
        ).hexdigest(),
        package_lock_uri="artifact://contracts/agent02-v1/fake-lock.txt",
        package_lock_sha256=hashlib.sha256(
            b"agent02-fake-package-lock-v1"
        ).hexdigest(),
        license="Test fixture only",
        training_dataset="None; deterministic fixture only",
        training_method="No training",
        supported_tasks=[
            MLTask.STATIC_PREDICTION,
            MLTask.STRUCTURE_RELAXATION,
        ],
        supported_properties=[
            "mlip_potential_energy",
            "maximum_force",
            "stress",
            "site_magnetic_moments",
        ],
        supported_elements=["Fe", "O", "Si"],
        supported_elements_source=(
            "agent02-v1 contract fixture; not a production training-domain "
            "claim"
        ),
        element_coverage_complete=True,
        supported_dimensionalities=[3],
        input_requirements=[
            "periodic 3D inorganic structure",
            "at most 100 sites",
        ],
        max_num_sites_policy=100,
        supported_devices=["fixture"],
        native_uncertainty=False,
        known_limitations=card.limitations,
        model_card_uri=(
            "artifact://contracts/agent02-v1/fake-model-card.json"
        ),
        model_card_sha256=sha256_payload(card),
        is_mock=True,
    )


def fake_registry() -> MLModelRegistry:
    return MLModelRegistry(models=[fake_model_spec()])


def fake_health_snapshot() -> ModelHealthSnapshot:
    model = fake_model_spec()
    tested_at = datetime(2026, 7, 25, 12, 0, tzinfo=UTC)
    installed_versions = {"material-screening-agent": "0.1.0"}
    fingerprint = environment_fingerprint_sha256(
        python_version="3.11-fixture",
        installed_package_versions=installed_versions,
        platform="fixture",
        architecture="fixture",
        device_policy="fixture",
        available_devices=["fixture"],
        package_lock_sha256=model.package_lock_sha256,
    )
    return ModelHealthSnapshot(
        model_id=model.model_id,
        checkpoint_sha256=model.checkpoint_sha256,
        package_lock_sha256=model.package_lock_sha256,
        worker_protocol_version=AGENT02_WORKER_PROTOCOL_VERSION,
        python_version="3.11-fixture",
        platform="fixture",
        architecture="fixture",
        device_policy="fixture",
        available_devices=["fixture"],
        smoke_test_status=SmokeTestStatus.PASS,
        parity_metrics=None,
        tested_at=tested_at,
        expires_at=tested_at + timedelta(days=1),
        installed_package_versions=installed_versions,
        environment_fingerprint_sha256=fingerprint,
        adapter_version=model.adapter_version,
        is_mock=True,
    )
