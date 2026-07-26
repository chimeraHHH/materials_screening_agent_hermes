from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from material_agent.retrieval.adapters import InMemoryMaterialsAdapter
from material_agent.retrieval.models import Requirement, RetrievalPolicy


FIXTURE_DIR = Path(__file__).parent / "fixtures"


def pytest_addoption(parser) -> None:
    parser.addoption(
        "--run-live-mp",
        action="store_true",
        default=False,
        help="run opt-in Materials Project API release tests",
    )


def pytest_collection_modifyitems(config, items) -> None:
    if config.getoption("--run-live-mp"):
        return
    marker = pytest.mark.skip(reason="requires explicit --run-live-mp")
    for item in items:
        if "live_mp" in item.keywords:
            item.add_marker(marker)


@pytest.fixture
def requirement() -> Requirement:
    return Requirement.model_validate_json(
        (FIXTURE_DIR / "requirement.si-o.json").read_text(encoding="utf-8")
    )


@pytest.fixture
def fixture_payload() -> dict:
    return json.loads(
        (FIXTURE_DIR / "mp-summary.si-o.json").read_text(encoding="utf-8")
    )


@pytest.fixture
def adapter(fixture_payload: dict) -> InMemoryMaterialsAdapter:
    return InMemoryMaterialsAdapter(
        fixture_payload["documents"],
        database_version=fixture_payload["database_version"],
        task_metadata=fixture_payload["task_metadata"],
    )


@pytest.fixture
def policy() -> RetrievalPolicy:
    return RetrievalPolicy(retry_base_seconds=0)


@pytest.fixture
def requirement_hash(requirement: Requirement) -> str:
    payload = json.dumps(
        requirement.model_dump(mode="json"),
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    return hashlib.sha256(payload).hexdigest()
