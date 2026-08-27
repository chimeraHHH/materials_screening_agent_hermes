from __future__ import annotations

from types import SimpleNamespace

from material_agent.inspiration.literature_budget import LiteratureQueryFamily
from material_agent.inspiration.models import ArtifactPointerV1, InspirationOutcome
from material_agent.orchestrator.inspiration_query_composite import (
    InspirationQueryCompositeRequestV3,
    InspirationQueryCompositeResultV3,
    InspirationQueryCompositeStatus,
    build_inspiration_query_composite_v3,
)
from tests.unit.test_orchestrator_inspiration_composite import (
    _FakeInspirationRunner,
    _seed_request,
)


class _ContextualRunner(_FakeInspirationRunner):
    def __init__(self, store):
        super().__init__(store)
        self.contextual_calls = []

    def run_contextual(self, **kwargs):
        self.contextual_calls.append(kwargs)
        inspiration_input = kwargs["inspiration_input"]
        bundle_ref = self.store.write_json(
            f"stages/inspiration/{inspiration_input.run_id}/v3-bundle.json",
            {"outcome": "SUCCEEDED"},
            immutable=True,
        )
        result_ref = self.store.write_json(
            f"stages/inspiration/{inspiration_input.run_id}/v3-result.json",
            {"outcome": "SUCCEEDED"},
            immutable=True,
        )
        bundle_pointer = ArtifactPointerV1.model_validate(
            bundle_ref.model_dump(mode="json")
        )
        return SimpleNamespace(
            stage_result=SimpleNamespace(
                outcome=InspirationOutcome.SUCCEEDED,
                bundle_artifact=bundle_pointer,
            ),
            stage_result_artifact=ArtifactPointerV1.model_validate(
                result_ref.model_dump(mode="json")
            ),
            bundle=SimpleNamespace(
                selected_candidates=(SimpleNamespace(candidate_id="proposal-v3"),)
            ),
        )


def test_v3_graph_passes_frozen_context_memory_and_budget_to_contextual_runner(
    tmp_path,
    requirement,
) -> None:
    store, base_request = _seed_request(tmp_path, requirement)
    runner = _ContextualRunner(store)
    graph = build_inspiration_query_composite_v3(
        store=store,
        runner=runner,
    ).compile()
    request = InspirationQueryCompositeRequestV3(
        base_request=base_request,
        raw_request=(
            "Study electronic flat bands in the Agent01 parent and use chalcogen "
            "substitution while retaining historical literature."
        ),
    )

    state = graph.invoke({"request": request.model_dump(mode="json")})
    result = InspirationQueryCompositeResultV3.model_validate(state["result"])

    assert result.status is InspirationQueryCompositeStatus.SUCCEEDED
    assert result.selected_candidate_ids == ["proposal-v3"]
    assert result.handoff is not None
    assert len(runner.calls) == 0
    assert len(runner.contextual_calls) == 1
    call = runner.contextual_calls[0]
    families = {item.family for item in call["literature_candidates"]}
    assert LiteratureQueryFamily.MATERIAL in families
    assert LiteratureQueryFamily.MECHANISM in families
    assert LiteratureQueryFamily.SOFT_CHEMISTRY in families
    assert LiteratureQueryFamily.HISTORICAL in families
    assert LiteratureQueryFamily.BRIDGE in families
    historical = {
        item.year_bucket_id
        for item in call["literature_budget_plan"].allowances
        if item.family is LiteratureQueryFamily.HISTORICAL
    }
    assert historical == {
        "era-1960-1979",
        "era-1980-1999",
        "era-2000-2014",
        "era-2015-2026",
    }
    for pointer in result.handoff.model_dump(mode="json").values():
        if isinstance(pointer, str):
            continue
        assert store.exists_with_hash(pointer["uri"], pointer["sha256"])


def test_v3_graph_inherits_agent01_hash_failure_and_never_calls_contextual_runner(
    tmp_path,
    requirement,
) -> None:
    store, base_request = _seed_request(
        tmp_path,
        requirement,
        bad_structure_hash=True,
    )
    runner = _ContextualRunner(store)
    graph = build_inspiration_query_composite_v3(
        store=store,
        runner=runner,
    ).compile()
    request = InspirationQueryCompositeRequestV3(
        base_request=base_request,
        raw_request="Study the material parent with historical literature.",
    )

    state = graph.invoke({"request": request.model_dump(mode="json")})
    result = InspirationQueryCompositeResultV3.model_validate(state["result"])

    assert result.status is InspirationQueryCompositeStatus.FAILED
    assert result.error_code == "AGENT01_STRUCTURE_HASH_MISMATCH"
    assert runner.contextual_calls == []
