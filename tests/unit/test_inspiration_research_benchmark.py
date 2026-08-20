from __future__ import annotations

import json
from pathlib import Path

from material_agent.inspiration.models import canonical_sha256
from material_agent.inspiration.research_benchmark import (
    ResearchBenchmarkCaseV1,
    ResearchBenchmarkGatePolicyV1,
    ResearchBenchmarkGoldSetV1,
    ResearchBenchmarkPredictionV1,
    evaluate_research_benchmark,
)


def _gold() -> ResearchBenchmarkGoldSetV1:
    case = ResearchBenchmarkCaseV1(
        case_id="benchmark-case-flatband",
        question="Find a layered transition-metal material with a Fermi-level flat band.",
        gold_dois=("10.1000/flat",),
        required_constraint_kinds=(
            "electronic_bandwidth",
            "fermi_ordering",
            "orbital_character",
        ),
        required_conclusion_terms=("42 mev", "tis2"),
        require_located_fulltext=True,
    )
    semantic = {
        "schema_version": "materials-research-benchmark-gold-v1",
        "benchmark_id": "benchmark-flatband-v1",
        "review_status": "SYNTHETIC_TEST_ONLY",
        "reviewer_ids": (),
        "adjudicator_id": None,
        "cases": (case,),
    }
    return ResearchBenchmarkGoldSetV1(
        **semantic,
        content_sha256=canonical_sha256(semantic),
    )


def _passing_prediction() -> ResearchBenchmarkPredictionV1:
    return ResearchBenchmarkPredictionV1(
        case_id="benchmark-case-flatband",
        resolved_dois=("10.1000/flat",),
        known_evidence_ids=("evidence-" + "1" * 24,),
        cited_evidence_ids=("evidence-" + "1" * 24,),
        located_fulltext_evidence_ids=("evidence-" + "1" * 24,),
        directly_supported_constraint_kinds=(
            "electronic_bandwidth",
            "fermi_ordering",
            "orbital_character",
        ),
        conclusion="TiS2 is the lead; the located source reports a 42 meV band.",
        native_lead_count=2,
        resolved_native_lead_count=2,
        declared_counter_queries=("TiS2 instability null result",),
        executed_counter_queries=("TiS2 instability null result",),
        candidate_second_pass_triggered=True,
        property_verification_complete=False,
    )


def test_synthetic_benchmark_passes_engineering_gates_but_not_release_gate() -> None:
    engineering = evaluate_research_benchmark(
        _gold(),
        (_passing_prediction(),),
        policy=ResearchBenchmarkGatePolicyV1(require_adjudicated=False),
    )
    release = evaluate_research_benchmark(_gold(), (_passing_prediction(),))

    assert engineering.release_status == "PASS"
    assert engineering.metrics.doi_recall == 1.0
    assert engineering.metrics.located_citation_rate == 1.0
    assert release.release_status == "FAIL"
    assert release.failed_gates == ("gold_not_adjudicated",)


def test_benchmark_reports_each_failed_quality_gate() -> None:
    prediction = _passing_prediction().model_copy(
        update={
            "resolved_dois": (),
            "cited_evidence_ids": ("evidence-" + "9" * 24,),
            "located_fulltext_evidence_ids": (),
            "directly_supported_constraint_kinds": (),
            "conclusion": "No useful answer.",
            "resolved_native_lead_count": 0,
            "executed_counter_queries": (),
            "candidate_second_pass_triggered": False,
            "property_verification_complete": True,
        }
    )

    result = evaluate_research_benchmark(
        _gold(),
        (prediction,),
        policy=ResearchBenchmarkGatePolicyV1(require_adjudicated=False),
    )

    assert result.release_status == "FAIL"
    assert set(result.failed_gates) == {
        "doi_recall",
        "citation_id_precision",
        "located_citation_rate",
        "direct_constraint_coverage",
        "conclusion_term_coverage",
        "native_lead_resolution_rate",
        "counter_query_execution_rate",
        "candidate_second_pass_rate",
        "property_boundary_rate",
    }


def test_frozen_synthetic_fixture_passes_only_engineering_gate() -> None:
    fixture_root = Path(__file__).parents[2] / "benchmarks" / "materials_research_v1"
    gold = ResearchBenchmarkGoldSetV1.model_validate_json(
        (fixture_root / "synthetic_gold.json").read_text("utf-8")
    )
    predictions = tuple(
        ResearchBenchmarkPredictionV1.model_validate(item)
        for item in json.loads(
            (fixture_root / "synthetic_predictions.json").read_text("utf-8")
        )
    )

    engineering = evaluate_research_benchmark(
        gold,
        predictions,
        policy=ResearchBenchmarkGatePolicyV1(require_adjudicated=False),
    )
    release = evaluate_research_benchmark(gold, predictions)

    assert engineering.release_status == "PASS"
    assert release.release_status == "FAIL"
    assert release.failed_gates == ("gold_not_adjudicated",)
