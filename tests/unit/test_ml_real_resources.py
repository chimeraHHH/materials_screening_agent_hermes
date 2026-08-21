from __future__ import annotations

import hashlib
from importlib import metadata
from pathlib import Path

from material_agent.ml_screening.models import ModelCard
from material_agent.ml_screening.real_resources import (
    AGENT02_PACKAGE_LOCK_SHA256,
    CHGNET_CHECKPOINT_SHA256,
    real_model_card,
    real_model_spec,
    real_registry,
)
from material_agent.ml_screening.resources import sha256_payload

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]


def test_real_resources_are_lightweight_and_hash_bound() -> None:
    lock_bytes = (REPOSITORY_ROOT / "requirements-agent02.lock").read_bytes()
    assert hashlib.sha256(lock_bytes).hexdigest() == AGENT02_PACKAGE_LOCK_SHA256
    card = ModelCard.model_validate_json(
        (
            REPOSITORY_ROOT
            / "config/agent02/chgnet-0.3.0-model-card.json"
        ).read_text(encoding="utf-8")
    )
    model = real_model_spec()
    assert card == real_model_card()
    assert sha256_payload(card) == model.model_card_sha256
    assert model.checkpoint_sha256 == CHGNET_CHECKPOINT_SHA256
    assert not model.is_mock
    assert not real_registry().models[0].is_mock
    for package in ("torch", "chgnet", "ase"):
        try:
            metadata.version(package)
        except metadata.PackageNotFoundError:
            continue
        raise AssertionError(f"heavy package leaked into main environment: {package}")
