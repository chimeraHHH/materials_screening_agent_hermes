"""PaperQA2-inspired end-to-end benchmark and release gates for research graphs."""

from __future__ import annotations

from typing import Literal

from pydantic import Field, model_validator

from material_agent.inspiration.models import canonical_sha256
from material_agent.inspiration.research_graph import MaterialsResearchGraphResultV7
from material_agent.orchestrator.models import StrictModel


class ResearchBenchmarkCaseV1(StrictModel):
    case_id: str = Field(pattern=r"^benchmark-case-[a-z0-9-]{1,64}$")
    question: str = Field(min_length=10, max_length=4_000)
    gold_dois: tuple[str, ...] = Field(min_length=1, max_length=128)
    required_constraint_kinds: tuple[str, ...] = Field(min_length=1, max_length=32)
    required_conclusion_terms: tuple[str, ...] = Field(min_length=1, max_length=32)
    require_located_fulltext: bool = False
    require_counter_search: bool = True
    require_candidate_second_pass: bool = True

    @model_validator(mode="after")
    def validate_case(self) -> ResearchBenchmarkCaseV1:
        for name, values in (
            ("gold_dois", self.gold_dois),
            ("required_constraint_kinds", self.required_constraint_kinds),
            ("required_conclusion_terms", self.required_conclusion_terms),
        ):
            normalized = tuple(sorted({value.casefold() for value in values}))
            if tuple(value.casefold() for value in values) != normalized:
                raise ValueError(f"{name} must be case-normalized, sorted, and unique")
        return self


class ResearchBenchmarkGoldSetV1(StrictModel):
    schema_version: Literal["materials-research-benchmark-gold-v1"] = (
        "materials-research-benchmark-gold-v1"
    )
    benchmark_id: str = Field(pattern=r"^benchmark-[a-z0-9-]{1,64}$")
    review_status: Literal["SYNTHETIC_TEST_ONLY", "ADJUDICATED"]
    reviewer_ids: tuple[str, ...] = Field(default=(), max_length=16)
    adjudicator_id: str | None = None
    cases: tuple[ResearchBenchmarkCaseV1, ...] = Field(min_length=1, max_length=256)
    content_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def validate_gold(self) -> ResearchBenchmarkGoldSetV1:
        if self.review_status == "ADJUDICATED":
            if len(set(self.reviewer_ids)) < 2 or self.adjudicator_id is None:
                raise ValueError(
                    "adjudicated benchmark requires two reviewers and adjudicator"
                )
            if self.adjudicator_id in self.reviewer_ids:
                raise ValueError("adjudicator must be independent")
        elif self.reviewer_ids or self.adjudicator_id is not None:
            raise ValueError("synthetic benchmark cannot claim expert reviewers")
        case_ids = tuple(case.case_id for case in self.cases)
        if case_ids != tuple(sorted(set(case_ids))):
            raise ValueError("benchmark cases must be ID-sorted and unique")
        expected = canonical_sha256(
            self.model_dump(mode="json", exclude={"content_sha256"})
        )
        if self.content_sha256 != expected:
            raise ValueError("benchmark content SHA-256 mismatch")
        return self


class ResearchBenchmarkPredictionV1(StrictModel):
    case_id: str
    resolved_dois: tuple[str, ...] = ()
    known_evidence_ids: tuple[str, ...] = ()
    cited_evidence_ids: tuple[str, ...] = ()
    located_fulltext_evidence_ids: tuple[str, ...] = ()
    directly_supported_constraint_kinds: tuple[str, ...] = ()
    conclusion: str
    native_lead_count: int = Field(ge=0)
    resolved_native_lead_count: int = Field(ge=0)
    declared_counter_queries: tuple[str, ...] = ()
    executed_counter_queries: tuple[str, ...] = ()
    candidate_second_pass_triggered: bool
    property_verification_complete: bool


class ResearchBenchmarkMetricsV1(StrictModel):
    doi_recall: float = Field(ge=0, le=1)
    citation_id_precision: float = Field(ge=0, le=1)
    located_citation_rate: float = Field(ge=0, le=1)
    direct_constraint_coverage: float = Field(ge=0, le=1)
    conclusion_term_coverage: float = Field(ge=0, le=1)
    native_lead_resolution_rate: float = Field(ge=0, le=1)
    counter_query_execution_rate: float = Field(ge=0, le=1)
    candidate_second_pass_rate: float = Field(ge=0, le=1)
    property_boundary_rate: float = Field(ge=0, le=1)


class ResearchBenchmarkGatePolicyV1(StrictModel):
    require_adjudicated: bool = True
    min_doi_recall: float = Field(default=0.8, ge=0, le=1)
    min_citation_id_precision: float = Field(default=1.0, ge=0, le=1)
    min_located_citation_rate: float = Field(default=0.5, ge=0, le=1)
    min_direct_constraint_coverage: float = Field(default=0.8, ge=0, le=1)
    min_conclusion_term_coverage: float = Field(default=0.8, ge=0, le=1)
    min_native_lead_resolution_rate: float = Field(default=0.5, ge=0, le=1)
    min_counter_query_execution_rate: float = Field(default=1.0, ge=0, le=1)
    min_candidate_second_pass_rate: float = Field(default=1.0, ge=0, le=1)
    min_property_boundary_rate: float = Field(default=1.0, ge=0, le=1)


class ResearchBenchmarkResultV1(StrictModel):
    schema_version: Literal["materials-research-benchmark-result-v1"] = (
        "materials-research-benchmark-result-v1"
    )
    benchmark_id: str
    gold_sha256: str
    case_count: int = Field(ge=1)
    metrics: ResearchBenchmarkMetricsV1
    release_status: Literal["PASS", "FAIL"]
    failed_gates: tuple[str, ...] = ()
    scientific_conclusion: Literal[False] = False


def prediction_from_graph(
    case_id: str, graph: MaterialsResearchGraphResultV7
) -> ResearchBenchmarkPredictionV1:
    known = tuple(sorted(item.evidence_id for item in graph.resolved_evidence))
    cited = {
        evidence_id
        for candidate in graph.candidates.candidates
        for evidence_id in candidate.evidence_ids
    }
    supported_kinds = {
        constraint.kind.value.casefold()
        for constraint in graph.constraints.constraints
        if any(
            assessment.constraint_id == constraint.constraint_id
            and assessment.verdict.value != "UNKNOWN"
            for row in graph.skeptic_review.matrix
            for assessment in row.assessments
        )
    }
    return ResearchBenchmarkPredictionV1(
        case_id=case_id,
        resolved_dois=tuple(
            sorted({item.doi for item in graph.resolved_evidence if item.doi})
        ),
        known_evidence_ids=known,
        cited_evidence_ids=tuple(sorted(cited)),
        located_fulltext_evidence_ids=tuple(
            sorted(
                item.evidence_id
                for item in graph.resolved_evidence
                if item.full_text_spans
            )
        ),
        directly_supported_constraint_kinds=tuple(sorted(supported_kinds)),
        conclusion=graph.synthesis.scientific_conclusion,
        native_lead_count=len(graph.lead_evidence_resolutions),
        resolved_native_lead_count=sum(
            item.status == "RESOLVED" for item in graph.lead_evidence_resolutions
        ),
        declared_counter_queries=graph.skeptic_review.counter_evidence_queries,
        executed_counter_queries=graph.executed_counter_queries,
        candidate_second_pass_triggered=(
            graph.candidate_literature_retrieval.triggered
        ),
        property_verification_complete=graph.synthesis.property_verification_complete,
    )


def evaluate_research_benchmark(
    gold: ResearchBenchmarkGoldSetV1,
    predictions: tuple[ResearchBenchmarkPredictionV1, ...],
    *,
    policy: ResearchBenchmarkGatePolicyV1 | None = None,
) -> ResearchBenchmarkResultV1:
    selected_policy = policy or ResearchBenchmarkGatePolicyV1()
    by_id = {item.case_id: item for item in predictions}
    if set(by_id) != {item.case_id for item in gold.cases}:
        raise ValueError("predictions must cover every benchmark case exactly once")
    counts = {
        "gold_doi": 0,
        "found_doi": 0,
        "citations": 0,
        "valid_citations": 0,
        "located_required": 0,
        "located_present": 0,
        "constraints": 0,
        "supported_constraints": 0,
        "terms": 0,
        "matched_terms": 0,
        "leads": 0,
        "resolved_leads": 0,
        "counter_declared": 0,
        "counter_executed": 0,
        "candidate_required": 0,
        "candidate_triggered": 0,
        "boundary_total": len(gold.cases),
        "boundary_kept": 0,
    }
    for case in gold.cases:
        prediction = by_id[case.case_id]
        gold_dois = set(case.gold_dois)
        counts["gold_doi"] += len(gold_dois)
        counts["found_doi"] += len(gold_dois & set(prediction.resolved_dois))
        counts["citations"] += len(prediction.cited_evidence_ids)
        counts["valid_citations"] += len(
            set(prediction.cited_evidence_ids) & set(prediction.known_evidence_ids)
        )
        if case.require_located_fulltext:
            counts["located_required"] += 1
            counts["located_present"] += bool(
                set(prediction.cited_evidence_ids)
                & set(prediction.located_fulltext_evidence_ids)
            )
        required_kinds = set(case.required_constraint_kinds)
        counts["constraints"] += len(required_kinds)
        counts["supported_constraints"] += len(
            required_kinds & set(prediction.directly_supported_constraint_kinds)
        )
        conclusion = prediction.conclusion.casefold()
        counts["terms"] += len(case.required_conclusion_terms)
        counts["matched_terms"] += sum(
            term in conclusion for term in case.required_conclusion_terms
        )
        counts["leads"] += prediction.native_lead_count
        counts["resolved_leads"] += prediction.resolved_native_lead_count
        if case.require_counter_search:
            declared = {
                " ".join(item.split()) for item in prediction.declared_counter_queries
            }
            executed = {
                " ".join(item.split()) for item in prediction.executed_counter_queries
            }
            counts["counter_declared"] += len(declared)
            counts["counter_executed"] += len(declared & executed)
        if case.require_candidate_second_pass:
            counts["candidate_required"] += 1
            counts["candidate_triggered"] += prediction.candidate_second_pass_triggered
        counts["boundary_kept"] += not prediction.property_verification_complete

    metrics = ResearchBenchmarkMetricsV1(
        doi_recall=_ratio(counts["found_doi"], counts["gold_doi"]),
        citation_id_precision=_ratio(
            counts["valid_citations"], counts["citations"], empty=1.0
        ),
        located_citation_rate=_ratio(
            counts["located_present"], counts["located_required"], empty=1.0
        ),
        direct_constraint_coverage=_ratio(
            counts["supported_constraints"], counts["constraints"]
        ),
        conclusion_term_coverage=_ratio(counts["matched_terms"], counts["terms"]),
        native_lead_resolution_rate=_ratio(
            counts["resolved_leads"], counts["leads"], empty=1.0
        ),
        counter_query_execution_rate=_ratio(
            counts["counter_executed"], counts["counter_declared"], empty=0.0
        ),
        candidate_second_pass_rate=_ratio(
            counts["candidate_triggered"], counts["candidate_required"], empty=1.0
        ),
        property_boundary_rate=_ratio(
            counts["boundary_kept"], counts["boundary_total"]
        ),
    )
    thresholds = {
        name.removeprefix("min_"): value
        for name, value in selected_policy.model_dump().items()
        if name.startswith("min_")
    }
    failed = [
        name
        for name, threshold in thresholds.items()
        if getattr(metrics, name) < threshold
    ]
    if selected_policy.require_adjudicated and gold.review_status != "ADJUDICATED":
        failed.insert(0, "gold_not_adjudicated")
    return ResearchBenchmarkResultV1(
        benchmark_id=gold.benchmark_id,
        gold_sha256=gold.content_sha256,
        case_count=len(gold.cases),
        metrics=metrics,
        release_status="FAIL" if failed else "PASS",
        failed_gates=tuple(failed),
    )


def _ratio(numerator: int, denominator: int, *, empty: float = 0.0) -> float:
    return round(empty if denominator == 0 else numerator / denominator, 8)
