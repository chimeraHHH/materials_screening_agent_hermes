from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from material_agent.retrieval.adapters import InMemoryMaterialsAdapter
from material_agent.retrieval.models import Requirement, RetrievalPolicy
from material_agent.ml_screening.models import (
    CandidateProperty,
    EvidenceLevel,
    MLCandidateInput,
    MLDecision,
    MLScreeningRequest,
    StructureRef,
)
from material_agent.ml_screening.planner import build_ml_stage_plan
from material_agent.ml_screening.requirement import requirement_view_from_payload
from material_agent.ml_screening.resources import (
    default_policy as default_ml_policy,
    artifact_pointer as ml_artifact_pointer,
    fake_health_snapshot,
    fake_model_spec,
    fake_registry,
)


FIXTURE_DIR = Path(__file__).parent / "fixtures"


def pytest_addoption(parser) -> None:
    parser.addoption(
        "--run-live-mp",
        action="store_true",
        default=False,
        help="run opt-in Materials Project API release tests",
    )
    parser.addoption(
        "--run-live-nomad",
        action="store_true",
        default=False,
        help="run opt-in public NOMAD API release tests",
    )
    parser.addoption(
        "--run-live-crossref",
        action="store_true",
        default=False,
        help="run opt-in public Crossref metadata API release tests",
    )
    parser.addoption(
        "--run-real-ml",
        action="store_true",
        default=False,
        help="run opt-in Agent02 tests in the independent CHGNet environment",
    )
    parser.addoption(
        "--run-live-llm",
        action="store_true",
        default=False,
        help="run opt-in DeepSeek Stage0 LLM release tests",
    )
    parser.addoption(
        "--run-live-semantic-rag",
        action="store_true",
        default=False,
        help="run opt-in DeepSeek grounded semantic-RAG release tests",
    )


def pytest_collection_modifyitems(config, items) -> None:
    live_marker = pytest.mark.skip(reason="requires explicit --run-live-mp")
    live_nomad_marker = pytest.mark.skip(
        reason="requires explicit --run-live-nomad"
    )
    live_crossref_marker = pytest.mark.skip(
        reason="requires explicit --run-live-crossref"
    )
    real_ml_marker = pytest.mark.skip(reason="requires explicit --run-real-ml")
    live_llm_marker = pytest.mark.skip(
        reason="requires explicit --run-live-llm"
    )
    live_semantic_rag_marker = pytest.mark.skip(
        reason="requires explicit --run-live-semantic-rag"
    )
    for item in items:
        if (
            "live_mp" in item.keywords
            and not config.getoption("--run-live-mp")
        ):
            item.add_marker(live_marker)
        if (
            "live_nomad" in item.keywords
            and not config.getoption("--run-live-nomad")
        ):
            item.add_marker(live_nomad_marker)
        if (
            "live_crossref" in item.keywords
            and not config.getoption("--run-live-crossref")
        ):
            item.add_marker(live_crossref_marker)
        if (
            {"real_ml", "slow_real_ml", "mps_ml"} & set(item.keywords)
            and not config.getoption("--run-real-ml")
        ):
            item.add_marker(real_ml_marker)
        if (
            "live_llm" in item.keywords
            and not config.getoption("--run-live-llm")
        ):
            item.add_marker(live_llm_marker)
        if (
            "live_semantic_rag" in item.keywords
            and not config.getoption("--run-live-semantic-rag")
        ):
            item.add_marker(live_semantic_rag_marker)


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


@pytest.fixture
def ml_requirement(requirement: Requirement):
    return requirement_view_from_payload(requirement.model_dump(mode="json"))


@pytest.fixture
def ml_policy():
    return default_ml_policy()


@pytest.fixture
def ml_model():
    return fake_model_spec()


@pytest.fixture
def ml_registry():
    return fake_registry()


@pytest.fixture
def ml_health():
    return fake_health_snapshot()


@pytest.fixture
def ml_candidate_factory():
    manifest_sha = hashlib.sha256(b"agent01-manifest").hexdigest()

    def make(
        candidate_id: str = "cand_si_o",
        *,
        rank: int | None = 1,
        elements: list[str] | None = None,
        num_sites: int = 6,
        dimensionality: int | None = 3,
        decision: MLDecision = MLDecision.PASS,
        band_gap: float | None = 0.8,
        band_gap_unit: str = "eV",
        energy_above_hull: float | None = 0.02,
        hull_unit: str = "eV/atom",
        parseable: bool | None = True,
        hash_verified: bool | None = True,
        is_periodic: bool | None = True,
        is_inorganic: bool | None = True,
        ase_compatible: bool | None = True,
        has_finite_values: bool | None = True,
        positive_volume: bool | None = True,
        minimum_distance: float | None = 1.5,
    ) -> MLCandidateInput:
        selected_elements = elements or ["O", "Si"]
        structure_sha = hashlib.sha256(
            f"structure:{candidate_id}".encode()
        ).hexdigest()
        properties = [
            CandidateProperty(
                name="is_metal",
                value=False,
                unit="dimensionless",
            ),
        ]
        if band_gap is not None:
            properties.append(
                CandidateProperty(
                    name="band_gap",
                    value=band_gap,
                    unit=band_gap_unit,
                )
            )
        if energy_above_hull is not None:
            properties.append(
                CandidateProperty(
                    name="energy_above_hull",
                    value=energy_above_hull,
                    unit=hull_unit,
                )
            )
        return MLCandidateInput(
            candidate_id=candidate_id,
            upstream_manifest_uri=(
                "artifact://stages/agent01/run/candidate_manifest.jsonl"
            ),
            upstream_manifest_sha256=manifest_sha,
            formula="SiO2",
            elements=selected_elements,
            num_sites=num_sites,
            publication_rank=rank,
            upstream_decision=decision,
            upstream_evidence_level=EvidenceLevel.L1_RETRIEVED,
            properties=properties,
            source_structure=StructureRef(
                structure_id=f"struct_{candidate_id}",
                uri=f"artifact://candidates/structures/{candidate_id}.cif",
                sha256=structure_sha,
                num_sites=num_sites,
                elements=selected_elements,
                dimensionality=dimensionality,
                is_periodic=is_periodic,
                is_inorganic=is_inorganic,
                parseable=parseable,
                ase_compatible=ase_compatible,
                has_finite_values=has_finite_values,
                positive_volume=positive_volume,
                minimum_distance_angstrom=minimum_distance,
                hash_verified=hash_verified,
            ),
        )

    return make


@pytest.fixture
def ml_fixed_time() -> datetime:
    return datetime(2026, 7, 26, tzinfo=UTC)


@pytest.fixture
def ml_plan_factory(
    ml_requirement,
    ml_policy,
    ml_registry,
    ml_health,
    ml_fixed_time,
):
    def make(
        candidates,
        *,
        request: MLScreeningRequest | None = None,
        policy=None,
        registry=None,
        health=None,
        created_at: datetime | None = None,
        health_artifact=None,
        attempt: int = 1,
    ):
        selected_policy = policy or ml_policy
        selected_registry = registry or ml_registry
        selected_health = health or ml_health
        selected_request = request or MLScreeningRequest()
        return build_ml_stage_plan(
            project_id="project-ml-hardening",
            run_id="run-ml-hardening",
            requirement_revision=ml_requirement.revision,
            attempt=attempt,
            orchestrator_input_snapshot=ml_artifact_pointer(
                "artifact://fixtures/input.json",
                {"kind": "input"},
            ),
            requirement_artifact=ml_artifact_pointer(
                "artifact://fixtures/requirement.json",
                ml_requirement,
            ),
            candidate_manifest_artifact=ml_artifact_pointer(
                "artifact://fixtures/manifest.jsonl",
                [candidate.model_dump(mode="json") for candidate in candidates],
            ),
            stage_request_artifact=(
                None
                if selected_request == MLScreeningRequest()
                else ml_artifact_pointer(
                    "artifact://fixtures/request.json",
                    selected_request,
                )
            ),
            policy_artifact=ml_artifact_pointer(
                "artifact://fixtures/policy.json",
                selected_policy,
            ),
            registry_artifact=ml_artifact_pointer(
                "artifact://fixtures/registry.json",
                selected_registry,
            ),
            health_artifact=(
                health_artifact
                or ml_artifact_pointer(
                    "artifact://fixtures/health.json",
                    selected_health,
                )
            ),
            requirement=ml_requirement,
            candidates=candidates,
            request=selected_request,
            policy=selected_policy,
            registry=selected_registry,
            health=selected_health,
            created_at=created_at or ml_fixed_time,
        )

    return make
