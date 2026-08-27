from __future__ import annotations

import json
import warnings
from pathlib import Path

import pytest
from pymatgen.core import Structure

from material_agent.inspiration.engine import PymatgenTransformationEngine
from material_agent.inspiration.models import (
    ArtifactPointerV1,
    InspirationInputV1,
    InspirationOutcome,
    ParentCandidateRefV1,
)
from material_agent.inspiration.policy import (
    BridgeSearchPolicyV1,
    EmbeddingBudgetV1,
    FetchBudgetV1,
    InspirationPolicyV1,
    PassageBudgetV1,
    RuntimeBudgetV1,
    SearchBudgetV1,
    SelectionPolicyV1,
    TransformationBudgetV1,
)
from material_agent.inspiration.runner import (
    STRUCTURE_MEDIA_TYPE,
    InspirationRunner,
    InspirationRunnerError,
)
from material_agent.inspiration.search import FixtureSearchAdapter
from material_agent.inspiration.tag_graph import (
    curated_flat_band_tag_graph,
    plan_tag_queries,
)
from material_agent.inspiration.transformations import (
    DEFAULT_SUBSTITUTION_REGISTRY_V1,
    substitution_registry_bytes,
)
from material_agent.inspiration.vectorizer import SIGNED_HASHING_SNAPSHOT
from material_agent.retrieval.storage import LocalArtifactStore

FIXTURE_DIR = Path(__file__).parents[1] / "fixtures" / "inspiration"


def _pointer(reference) -> ArtifactPointerV1:
    return ArtifactPointerV1.model_validate(reference.model_dump(mode="python"))


def _policy() -> InspirationPolicyV1:
    return InspirationPolicyV1(
        policy_id="inspiration-offline-fixture-v1",
        search=SearchBudgetV1(
            max_queries=3,
            max_direct_queries=1,
            max_bridge_queries=1,
            max_counter_queries=1,
            max_raw_hits=3,
            max_unique_documents=1,
        ),
        fetch=FetchBudgetV1(
            max_requests=0,
            max_total_bytes=0,
            max_bytes_per_response=0,
        ),
        passages=PassageBudgetV1(
            min_tokens=16,
            max_tokens=64,
            max_per_hit=1,
            max_total=3,
        ),
        embedding=EmbeddingBudgetV1(
            vector_dimension=32,
            max_passages=3,
            max_input_tokens=1_000,
        ),
        bridge=BridgeSearchPolicyV1(max_bridge_packets=1),
        transformation=TransformationBudgetV1(
            max_plans=2,
            max_plans_per_parent=2,
        ),
        selection=SelectionPolicyV1(
            top_k=1,
            min_mechanisms_when_available=1,
        ),
    )


def _frozen_monotonic_clock() -> float:
    return 100.0


class _StepMonotonicClock:
    def __init__(self, *, step_seconds: float) -> None:
        self.now = 100.0
        self.step_seconds = step_seconds

    def __call__(self) -> float:
        value = self.now
        self.now += self.step_seconds
        return value


def _seed_run(
    root: Path,
    *,
    policy: InspirationPolicyV1 | None = None,
    monotonic_clock=_frozen_monotonic_clock,
):
    store = LocalArtifactStore(root)
    policy = policy or _policy()
    graph = curated_flat_band_tag_graph()
    response_bytes = (FIXTURE_DIR / "openalex-acoustic-flat-band.json").read_bytes()
    parent_bytes = (FIXTURE_DIR / "parent-tis2.cif").read_bytes()
    assert json.loads(
        (FIXTURE_DIR / "substitution-registry.json").read_text(encoding="utf-8")
    ) == DEFAULT_SUBSTITUTION_REGISTRY_V1.model_dump(mode="json")

    requirement_pointer = _pointer(
        store.write_bytes(
            "inputs/requirement.json",
            (FIXTURE_DIR / "requirement.json").read_bytes(),
            media_type="application/json",
            immutable=True,
        )
    )
    policy_pointer = _pointer(
        store.write_json(
            "inputs/policy.json",
            policy.model_dump(mode="json"),
            immutable=True,
        )
    )
    graph_pointer = _pointer(
        store.write_json(
            "inputs/tag_graph.json",
            graph.model_dump(mode="json"),
            immutable=True,
        )
    )
    registry_pointer = _pointer(
        store.write_bytes(
            "inputs/substitution_registry.json",
            substitution_registry_bytes(DEFAULT_SUBSTITUTION_REGISTRY_V1),
            media_type="application/json",
            immutable=True,
        )
    )
    _pointer(
        store.write_bytes(
            "inputs/openalex_fixture.json",
            response_bytes,
            media_type="application/json",
            immutable=True,
        )
    )
    search_fixture_pointer = _pointer(
        store.write_bytes(
            "inputs/search_fixture_manifest.json",
            (FIXTURE_DIR / "search-fixture-manifest.json").read_bytes(),
            media_type="application/json",
            immutable=True,
        )
    )
    parent_pointer = _pointer(
        store.write_bytes(
            "inputs/parent-tis2.cif",
            parent_bytes,
            media_type=STRUCTURE_MEDIA_TYPE,
            immutable=True,
        )
    )

    planned = plan_tag_queries(
        graph,
        target_tag_ids=("electronic-flat-band",),
        budget=policy.search,
    )
    adapter = FixtureSearchAdapter(
        {query.query_id: response_bytes for query in planned.queries}
    )
    inspiration_input = InspirationInputV1(
        project_id="project-fixture",
        request_id="request-fixture",
        run_id="run-fixture",
        requirement_revision=1,
        requirement_artifact=requirement_pointer,
        parent_candidates=(
            ParentCandidateRefV1(
                candidate_id="parent-candidate-tis2",
                structure_id="parent-structure-tis2",
                structure_artifact=parent_pointer,
            ),
        ),
        policy_artifact=policy_pointer,
        tag_graph_artifact=graph_pointer,
        transformation_registry_artifact=registry_pointer,
        search_fixture_artifact=search_fixture_pointer,
        search_adapter=adapter.component,
        vectorizer=SIGNED_HASHING_SNAPSHOT,
    )
    runner = InspirationRunner(
        store=store,
        search_adapter=adapter,
        transformation_engine=PymatgenTransformationEngine(),
        monotonic_clock=monotonic_clock,
    )
    result = runner.run(
        inspiration_input=inspiration_input,
        policy=policy,
        tag_graph=graph,
        target_tag_ids=("electronic-flat-band",),
    )
    return store, runner, result, inspiration_input, policy, graph


def test_runner_records_injected_monotonic_walltime_in_terminal_ledger(
    tmp_path: Path,
) -> None:
    clock = _StepMonotonicClock(step_seconds=0.001)

    store, _, result, _, policy, _ = _seed_run(
        tmp_path / "walltime",
        monotonic_clock=clock,
    )

    ledger = result.bundle.cost_ledger
    persisted = store.read_json(result.stage_result.cost_ledger_artifact.uri)
    assert ledger.walltime_ms > 0
    assert ledger.walltime_ms <= policy.runtime.max_walltime_seconds * 1_000
    assert persisted["walltime_ms"] == ledger.walltime_ms


def test_runner_deadline_stops_mid_search_and_persists_partial_attempts(
    tmp_path: Path,
) -> None:
    policy = _policy().model_copy(
        update={"runtime": RuntimeBudgetV1(max_walltime_seconds=1)}
    )
    clock = _StepMonotonicClock(step_seconds=0.2)

    with pytest.raises(InspirationRunnerError) as raised:
        _seed_run(
            tmp_path / "deadline",
            policy=policy,
            monotonic_clock=clock,
        )

    assert raised.value.code == "WALLTIME_BUDGET_EXCEEDED"
    attempts_path = (
        tmp_path
        / "deadline"
        / "stages"
        / "inspiration"
        / "run-fixture"
        / "search_attempts.jsonl"
    )
    assert attempts_path.is_file()
    assert attempts_path.read_text(encoding="utf-8").strip()


def _artifact_map(result) -> dict[str, tuple[str, int | None]]:
    stage = result.stage_result
    pointers = (
        stage.input_snapshot_artifact,
        stage.policy_artifact,
        stage.bundle_artifact,
        stage.report_artifact,
        stage.cost_ledger_artifact,
        *stage.intermediate_artifacts,
        result.stage_result_artifact,
    )
    return {
        pointer.uri: (pointer.sha256, pointer.size_bytes) for pointer in pointers
    }


def test_offline_runner_persists_auditable_candidate_and_strict_layout(
    tmp_path: Path,
) -> None:
    store, runner, result, _, _, _ = _seed_run(tmp_path / "first")

    assert result.stage_result.outcome is InspirationOutcome.SUCCEEDED
    assert result.stage_result.scientific_conclusion is False
    assert result.bundle.scientific_conclusion is False
    assert len(result.bundle.selected_candidates) == 1
    candidate = result.bundle.selected_candidates[0]
    assert candidate.scientific_conclusion is False
    assert candidate.selection_rank == 1
    assert candidate.mechanism_tag_ids == ("local-resonance",)
    assert "Target property status: `UNKNOWN`" in result.report
    assert "Property status: `UNKNOWN`" in result.report
    assert "novelty" not in result.report.casefold()
    assert "PDF full-text reads: `0`" in result.report
    assert "## Candidate identity and diversity audit" in result.report

    ledger = result.bundle.cost_ledger
    assert ledger.search_requests == 3
    assert ledger.raw_documents == 3
    assert ledger.unique_documents == 1
    assert ledger.fetch_requests == 0
    assert ledger.extracted_passages == 1
    assert ledger.vectorized_passages == 1
    assert ledger.llm_calls == 0
    assert ledger.generated_plans == 1
    assert ledger.candidates_after_internal_dedup == 1
    assert ledger.walltime_ms == 0

    prefix = "stages/inspiration/run-fixture"
    required_paths = (
        "input_snapshot.json",
        "policy.json",
        "query_plans.jsonl",
        "search_attempts.jsonl",
        "search_hits.jsonl",
        "fetch_manifest.jsonl",
        "passages.jsonl",
        "passage_vectors.jsonl",
        "evidence_cards.jsonl",
        "tag_graph.json",
        "bridge_packets.jsonl",
        "transformation_proposals.jsonl",
        "internal_duplicate_groups.jsonl",
        "selection_audit.json",
        "inspiration_bundle.json",
        "cost_ledger.json",
        "report.md",
        "stage_result.json",
    )
    assert all(store.exists(f"{prefix}/{name}") for name in required_paths)
    attempts = store.read_jsonl(f"{prefix}/search_attempts.jsonl")
    assert len(attempts) == 3
    assert all(attempt["outcome"] == "success" for attempt in attempts)
    assert {attempt["query_id"] for attempt in attempts} == {
        query["query_id"]
        for query in store.read_jsonl(f"{prefix}/query_plans.jsonl")
    }
    assert len(store.read_jsonl(f"{prefix}/bridge_packets.jsonl")) == 1
    transformation_records = store.read_jsonl(
        f"{prefix}/transformation_proposals.jsonl"
    )
    assert len(transformation_records) == 1
    real_plan = transformation_records[0]
    assert real_plan["status"] == "STRUCTURE_VALID"
    assert real_plan["output_structure_id"].startswith("str_")
    assert real_plan["output_structure_artifact"]["uri"].startswith(
        f"artifact://{prefix}/structures/str_"
    )
    real_checks = {
        check["check_id"]: check["status"]
        for check in real_plan["validation_checks"]
    }
    assert real_checks["charge_or_oxidation"] == "PASS"
    assert real_checks["equivalent_sites_complete"] == "PASS"
    assert real_checks["retrieval_structure_processing"] == "PASS"
    assert len(store.read_jsonl(f"{prefix}/internal_duplicate_groups.jsonl")) == 1
    selection_audit = store.read_json(f"{prefix}/selection_audit.json")
    assert selection_audit["schema_version"] == "inspiration-selection-audit-v1"
    assert selection_audit["diversity_mode"] == "MMR_ONLY"
    assert selection_audit["selected_candidate_count"] == 1
    assert selection_audit["selected_exact_duplicate_count"] == 0
    assert selection_audit["selected_strict_duplicate_count"] == 0
    assert selection_audit["route_quota_status"] == "MET"
    assert len(list((store.root / prefix / "raw_search").glob("*.json"))) == 3
    assert len(list((store.root / prefix / "vectors").glob("*.f32le"))) == 1
    assert len(list((store.root / prefix / "structures").glob("*.cif"))) == 1
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        real_output = Structure.from_str(
            store.read_bytes(candidate.structure_artifact.uri).decode("utf-8"),
            fmt="cif",
        )
    substituted_species = [
        site.specie
        for site in real_output
        if site.specie.symbol == "Se"
    ]
    assert len(substituted_species) == 2
    assert all(species.oxi_state == -2.0 for species in substituted_species)
    runner.verify_stage_result(result.stage_result)
    assert all(size is not None for _, size in _artifact_map(result).values())


def test_offline_replay_is_byte_stable_and_input_tampering_fails_closed(
    tmp_path: Path,
) -> None:
    first_store, _, first, _, _, _ = _seed_run(tmp_path / "first")
    second_store, _, second, _, _, _ = _seed_run(tmp_path / "second")

    assert _artifact_map(first) == _artifact_map(second)
    assert first.stage_result_artifact.sha256 == second.stage_result_artifact.sha256
    assert first_store.read_bytes(first.stage_result_artifact.uri) == (
        second_store.read_bytes(second.stage_result_artifact.uri)
    )
    assert first_store.read_bytes(first.stage_result.bundle_artifact.uri) == (
        second_store.read_bytes(second.stage_result.bundle_artifact.uri)
    )
    assert first_store.read_bytes(first.stage_result.report_artifact.uri) == (
        second_store.read_bytes(second.stage_result.report_artifact.uri)
    )

    tampered_root = tmp_path / "tampered"
    store = LocalArtifactStore(tampered_root)
    policy = _policy()
    graph = curated_flat_band_tag_graph()
    response = (FIXTURE_DIR / "openalex-acoustic-flat-band.json").read_bytes()
    parent = (FIXTURE_DIR / "parent-tis2.cif").read_bytes()
    requirement = _pointer(
        store.write_bytes(
            "inputs/requirement.json",
            json.dumps({"revision": 1}).encode(),
            media_type="application/json",
        )
    )
    policy_pointer = _pointer(
        store.write_json("inputs/policy.json", policy.model_dump(mode="json"))
    )
    graph_pointer = _pointer(
        store.write_json("inputs/tag_graph.json", graph.model_dump(mode="json"))
    )
    registry = _pointer(
        store.write_bytes(
            "inputs/registry.json",
            b"{}",
            media_type="application/json",
        )
    )
    _pointer(
        store.write_bytes(
            "inputs/openalex_fixture.json",
            response,
            media_type="application/json",
        )
    )
    search_fixture = _pointer(
        store.write_bytes(
            "inputs/search_fixture_manifest.json",
            (FIXTURE_DIR / "search-fixture-manifest.json").read_bytes(),
            media_type="application/json",
        )
    )
    parent_pointer = _pointer(
        store.write_bytes(
            "inputs/parent.cif",
            parent,
            media_type=STRUCTURE_MEDIA_TYPE,
        )
    )
    planned = plan_tag_queries(
        graph,
        target_tag_ids=("electronic-flat-band",),
        budget=policy.search,
    )
    adapter = FixtureSearchAdapter(
        {query.query_id: response for query in planned.queries}
    )
    inspiration_input = InspirationInputV1(
        project_id="project-tamper",
        request_id="request-tamper",
        run_id="run-tamper",
        requirement_revision=1,
        requirement_artifact=requirement,
        parent_candidates=(
            ParentCandidateRefV1(
                candidate_id="parent-candidate",
                structure_id="parent-structure",
                structure_artifact=parent_pointer,
            ),
        ),
        policy_artifact=policy_pointer,
        tag_graph_artifact=graph_pointer,
        transformation_registry_artifact=registry,
        search_fixture_artifact=search_fixture,
        search_adapter=adapter.component,
        vectorizer=SIGNED_HASHING_SNAPSHOT,
    )
    store.write_bytes(
        "inputs/parent.cif",
        b"tampered",
        media_type=STRUCTURE_MEDIA_TYPE,
    )
    runner = InspirationRunner(
        store=store,
        search_adapter=adapter,
        transformation_engine=PymatgenTransformationEngine(),
    )

    with pytest.raises(InspirationRunnerError) as raised:
        runner.run(
            inspiration_input=inspiration_input,
            policy=policy,
            tag_graph=graph,
            target_tag_ids=("electronic-flat-band",),
        )
    assert raised.value.code == "ARTIFACT_HASH_MISMATCH"
