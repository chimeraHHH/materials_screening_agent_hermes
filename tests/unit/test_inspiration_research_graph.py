from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import pytest

from material_agent.inspiration.deepseek_agent import (
    DeepSeekAgentReceiptV1,
    DeepSeekAgentResultV1,
    DeepSeekFunctionTool,
)
from material_agent.inspiration.research_graph import (
    CandidateConstraintMatrixRowV1,
    CandidateHypothesisV1,
    CandidateInferenceRowV1,
    CandidateSetV1,
    ConstraintAssessmentV1,
    ConstraintGraphV1,
    ConstraintKind,
    DatabaseCandidateReviewV1,
    DatabaseCandidateV1,
    DatabaseFederationAuditV1,
    DatabaseSourceRecordV1,
    DiscoveryReviewV1,
    EvidenceReviewV1,
    InferenceBasis,
    InferredConstraintAssessmentV1,
    MaterialsResearchDirector,
    QueryFamilyV1,
    ResearchConstraintV1,
    ResearchQueryPlanV1,
    ResearchQueryV1,
    ResearchSynthesisV1,
    ResolvedEvidenceV1,
    ScientificInferenceReviewV1,
    SkepticReviewV1,
    SparseConstraintAssessmentV1,
    SparseSkepticReviewV1,
    VerificationMethod,
    _normalize_candidate_references,
    _normalize_synthesis,
)
from material_agent.orchestrator.models import StrictModel


class SearchArgs(StrictModel):
    query: str


def receipt(role: str) -> DeepSeekAgentReceiptV1:
    digest = (role.encode().hex() + "0" * 64)[:64]
    return DeepSeekAgentReceiptV1(
        prompt_version=f"materials-research-{role}-v1",
        reasoning_effort="high",
        rounds=2,
        tool_calls=(),
        request_sha256_by_round=(digest, digest),
        response_sha256_by_round=(digest, digest),
        transport_attempts_by_round=(1, 1),
        transport_retry_count=0,
        final_response_sha256=digest,
    )


def constraints() -> ConstraintGraphV1:
    specs = (
        ("layered", ConstraintKind.DIMENSIONALITY, VerificationMethod.STRUCTURE),
        (
            "bandwidth",
            ConstraintKind.ELECTRONIC_BANDWIDTH,
            VerificationMethod.BAND_STRUCTURE,
        ),
        ("fermi", ConstraintKind.FERMI_ORDERING, VerificationMethod.BAND_STRUCTURE),
        ("isolation", ConstraintKind.BAND_ISOLATION, VerificationMethod.BAND_STRUCTURE),
        ("orbital", ConstraintKind.ORBITAL_CHARACTER, VerificationMethod.PDOS),
        (
            "oxidation",
            ConstraintKind.OXIDATION_STATE,
            VerificationMethod.OXIDATION_ANALYSIS,
        ),
        (
            "connected",
            ConstraintKind.SUBLATTICE_CONNECTIVITY,
            VerificationMethod.BONDING_TOPOLOGY,
        ),
        ("composition", ConstraintKind.COMPOSITION, VerificationMethod.STRUCTURE),
    )
    return ConstraintGraphV1(
        goal_summary="Find layered transition-metal flat-band hypotheses.",
        constraints=tuple(
            ResearchConstraintV1(
                constraint_id=f"constraint-{name}",
                kind=kind,
                statement=f"Verify {name} requirement.",
                required_verification=(method,),
                threshold_value=50.0 if name == "bandwidth" else None,
                threshold_unit="meV" if name == "bandwidth" else None,
            )
            for name, kind, method in specs
        ),
        prohibited_inferences=("No band or PDOS claim from metadata alone.",),
    )


def query_plan(graph: ConstraintGraphV1) -> ResearchQueryPlanV1:
    ids = tuple(item.constraint_id for item in graph.constraints)
    return ResearchQueryPlanV1(
        families=(
            QueryFamilyV1(
                family_id="family-direct",
                rationale="Direct material search.",
                queries=(
                    ResearchQueryV1(
                        query_id="query-direct",
                        text="layered transition metal flat band",
                        purpose="DIRECT",
                        target_constraint_ids=ids[:4],
                    ),
                ),
            ),
            QueryFamilyV1(
                family_id="family-validation",
                rationale="Orbital and chemistry validation.",
                queries=(
                    ResearchQueryV1(
                        query_id="query-validation",
                        text="transition metal orbital oxidation connected lattice",
                        purpose="CHEMISTRY",
                        target_constraint_ids=ids[4:],
                    ),
                ),
            ),
        )
    )


def test_generic_research_director_separates_evidence_unknowns_from_inference() -> None:
    graph = constraints()
    plan = query_plan(graph)
    leads: list[Mapping[str, Any]] = []
    evidence: list[ResolvedEvidenceV1] = []
    database_candidates: list[DatabaseCandidateV1] = []

    def native_handler(args: SearchArgs) -> Mapping[str, Any]:
        leads.append({"lead_id": "lead-" + "1" * 24, "query": args.query})
        return {"leads": leads}

    def evidence_handler(args: SearchArgs) -> Mapping[str, Any]:
        del args
        item = ResolvedEvidenceV1(
            evidence_id="evidence-" + "2" * 24,
            provider="crossref",
            stable_record_id="10.1234/example",
            title="Layered material metadata",
            published_year=2024,
            doi="10.1234/example",
            canonical_url="https://doi.org/10.1234/example",
            abstract_excerpt="A layered transition-metal material is discussed.",
            raw_response_uri="artifact://research/raw/search.json",
            raw_response_sha256="3" * 64,
            supported_constraint_ids=("constraint-layered",),
        )
        if not evidence:
            evidence.append(item)
        return {"evidence": [item.model_dump(mode="json")]}

    def database_handler(args: SearchArgs) -> Mapping[str, Any]:
        del args
        item = DatabaseCandidateV1(
            database_candidate_id="db-candidate-" + "4" * 24,
            source_database="c2db",
            source_material_id="TiS2-test",
            canonical_structure_id="str_" + "7" * 24,
            source_records=(
                DatabaseSourceRecordV1(
                    source_database="c2db",
                    source_material_id="TiS2-test",
                    source_database_version="fixture-c2db-v1",
                    query_fingerprint="8" * 64,
                    canonical_structure_id="str_" + "7" * 24,
                    band_gap_ev=0.0,
                    structure_artifact_uri="artifact://research/database/candidate.cif",
                    structure_artifact_sha256="5" * 64,
                    raw_response_artifact_uri=(
                        "artifact://research/database/candidate.raw.json"
                    ),
                    raw_response_artifact_sha256="6" * 64,
                    license="CC-BY-NC-4.0",
                ),
            ),
            formula="TiS2",
            elements=("S", "Ti"),
            transition_metals=("Ti",),
            band_gap_ev=0.0,
            dimensionality=2,
            dimensionality_status="RESOLVED",
            connected_transition_metal_sublattice_proxy=1.0,
            connectivity_status="RESOLVED_PROXY",
            structure_artifact_uri="artifact://research/database/candidate.cif",
            structure_artifact_sha256="5" * 64,
            raw_response_artifact_uri="artifact://research/database/candidate.raw.json",
            raw_response_artifact_sha256="6" * 64,
        )
        if not database_candidates:
            database_candidates.append(item)
        return {"records": [item.model_dump(mode="json")]}

    native_tool = DeepSeekFunctionTool(
        name="native_web_search",
        description="Discover unresolved web leads.",
        arguments_model=SearchArgs,
        handler=native_handler,
    )
    evidence_tool = DeepSeekFunctionTool(
        name="authoritative_literature_search",
        description="Resolve literature metadata through accepted providers.",
        arguments_model=SearchArgs,
        handler=evidence_handler,
    )
    database_tool = DeepSeekFunctionTool(
        name="search_federated_materials_candidates",
        description="Search bounded federated structures.",
        arguments_model=SearchArgs,
        handler=database_handler,
    )

    outputs: dict[str, StrictModel] = {
        "requirements_analyst": graph,
        "query_strategist": plan,
        "native_search_scout": DiscoveryReviewV1(
            useful_lead_ids=("lead-" + "1" * 24,),
            resolver_queries=("layered transition metal flat band",),
        ),
        "evidence_researcher": EvidenceReviewV1(
            selected_evidence_ids=("evidence-" + "2" * 24,),
            unresolved_constraint_ids=tuple(
                item.constraint_id for item in graph.constraints[1:]
            ),
            limitations=("Metadata and abstract do not establish electronic bands.",),
        ),
        "database_scout": DatabaseCandidateReviewV1(
            selected_database_candidate_ids=("db-candidate-" + "4" * 24,),
            selection_rationale="Keep the real layered database parent for validation.",
            unresolved_properties=("flat band", "orbital character", "oxidation state"),
        ),
        "mechanism_chemist": CandidateSetV1(
            candidates=(
                CandidateHypothesisV1(
                    candidate_id="candidate-example",
                    material_name="Example layered parent",
                    formula="TX2",
                    hypothesis="Test this connected layered parent as a hypothesis.",
                    mechanism="Transition-metal ligand hybridization may narrow one band.",
                    evidence_ids=("evidence-" + "2" * 24,),
                    database_candidate_ids=("db-candidate-" + "4" * 24,),
                ),
            )
        ),
    }
    outputs["skeptic"] = SparseSkepticReviewV1(
        evidence_backed_assessments=(
            SparseConstraintAssessmentV1(
                candidate_id="candidate-example",
                constraint_id="constraint-layered",
                verdict="PASS",
                evidence_ids=("evidence-" + "2" * 24,),
                rationale="Directly described in resolved abstract metadata.",
            ),
        ),
        global_failure_modes=("Flat-band properties may disappear after relaxation.",),
    )
    unknown_pairs = tuple(
        f"candidate-example:{item.constraint_id}" for item in graph.constraints[1:]
    )
    outputs["hypothesis_reasoner"] = ScientificInferenceReviewV1(
        matrix=(
            CandidateInferenceRowV1(
                candidate_id="candidate-example",
                assessments=tuple(
                    InferredConstraintAssessmentV1(
                        constraint_id=item.constraint_id,
                        predicted_verdict="LIKELY_PASS",
                        probability_pass=0.65,
                        scientific_rationale=(
                            "The connected layered transition-metal lattice is a "
                            "plausible narrow-band platform."
                        ),
                    )
                    for item in graph.constraints
                ),
                overall_promise_score=0.65,
                scientific_hypothesis=(
                    "The connected transition-metal layer may host a ligand-hybridized "
                    "narrow band near the Fermi level."
                ),
                mechanistic_argument=(
                    "Lattice connectivity and orbital interference can suppress dispersion."
                ),
                inference_bases=(InferenceBasis.LATTICE_GEOMETRY,),
                key_assumptions=("The reported parent structure is retained.",),
                decisive_falsifiers=(
                    "A computed band structure violates the target conditions.",
                ),
                supporting_evidence_ids=("evidence-" + "2" * 24,),
                supporting_database_candidate_ids=("db-candidate-" + "4" * 24,),
                highest_information_gain_test="Compute spin-polarized bands and PDOS.",
            ),
        ),
        top_candidate_ids=("candidate-example",),
        cross_candidate_conclusion="Prioritize the sole connected layered candidate.",
    )
    outputs["synthesist"] = ResearchSynthesisV1(
        ranked_candidate_ids=("candidate-example",),
        recommendation="Retain as a hypothesis and compute the missing evidence.",
        scientific_conclusion=(
            "Example layered parent is the leading reasoned hypothesis because its "
            "connected transition-metal lattice can support orbital-interference narrowing."
        ),
        unresolved_hard_constraints=unknown_pairs,
        required_next_computations=("DFT band structure and PDOS",),
    )

    class FakeRunner:
        def __init__(self, role: str, tools: tuple[DeepSeekFunctionTool, ...]) -> None:
            self.role = role
            self.tools = tools

        def run(self, **kwargs: Any) -> DeepSeekAgentResultV1[Any]:
            del kwargs
            if self.role in {
                "native_search_scout",
                "evidence_researcher",
                "database_scout",
            }:
                self.tools[0].handler(
                    SearchArgs(query="layered transition metal flat band")
                )
            else:
                section = {
                    "requirements_analyst": "goal",
                    "query_strategist": "constraints",
                    "mechanism_chemist": "evidence",
                    "skeptic": "candidates",
                    "hypothesis_reasoner": "skeptic_review",
                    "synthesist": "skeptic_review",
                }[self.role]
                args_model = self.tools[0].arguments_model
                self.tools[0].handler(args_model(sections=(section,)))
            return DeepSeekAgentResultV1(
                final=outputs[self.role], receipt=receipt(self.role)
            )

    director = MaterialsResearchDirector(
        runner_factory=lambda role, tools: FakeRunner(role, tools),
        native_search_tool=native_tool,
        native_leads_snapshot=lambda: tuple(leads),
        authoritative_search_tool=evidence_tool,
        evidence_snapshot=lambda: tuple(evidence),
        database_search_tool=database_tool,
        database_candidates_snapshot=lambda: tuple(database_candidates),
        database_federation_snapshot=lambda: DatabaseFederationAuditV1(
            enabled_sources=("c2db", "nomad", "materials_project"),
            source_record_count=1,
            federated_candidate_count=1,
        ),
    )
    result = director.run(
        "搜索层状过渡金属二维平带材料，并逐项验证费米面、轨道、价态与连通子晶格。"
    )

    assert len(result.roles) == 9
    assert {item.kind for item in result.constraints.constraints} >= {
        ConstraintKind.DIMENSIONALITY,
        ConstraintKind.ELECTRONIC_BANDWIDTH,
        ConstraintKind.FERMI_ORDERING,
        ConstraintKind.BAND_ISOLATION,
        ConstraintKind.ORBITAL_CHARACTER,
        ConstraintKind.OXIDATION_STATE,
        ConstraintKind.SUBLATTICE_CONNECTIVITY,
    }
    assert result.resolved_evidence[0].evidence_scope == "METADATA_OR_ABSTRACT_ONLY"
    assert result.database_candidates[0].flat_band_status == "UNKNOWN"
    assert result.database_federation.enabled_sources == (
        "c2db",
        "nomad",
        "materials_project",
    )
    assert result.synthesis.scientific_conclusion_status == "REASONED_HYPOTHESIS"
    assert "leading reasoned hypothesis" in result.synthesis.scientific_conclusion
    assert all(
        assessment.predicted_verdict == "LIKELY_PASS"
        for row in result.inference_review.matrix
        for assessment in row.assessments
    )
    assert len(result.synthesis.unresolved_hard_constraints) == 7


def test_synthesis_join_is_completed_deterministically() -> None:
    candidate = CandidateSetV1(
        candidates=(
            CandidateHypothesisV1(
                candidate_id="candidate-x",
                material_name="X",
                hypothesis="A bounded hypothesis.",
                mechanism="A connected-lattice mechanism to test.",
            ),
        )
    )
    review = SkepticReviewV1(
        matrix=(
            CandidateConstraintMatrixRowV1(
                candidate_id="candidate-x",
                assessments=(
                    ConstraintAssessmentV1(
                        constraint_id="constraint-x",
                        verdict="UNKNOWN",
                        rationale="No direct evidence.",
                        next_verification="Calculate the requested property.",
                    ),
                ),
            ),
        ),
        global_failure_modes=("The hypothesis may fail.",),
    )
    synthesis = ResearchSynthesisV1(
        ranked_candidate_ids=("candidate-x",),
        recommendation="Keep the candidate as a hypothesis.",
        scientific_conclusion="Candidate X is the leading falsifiable hypothesis.",
        unresolved_hard_constraints=(),
    )
    normalized, changes = _normalize_synthesis(synthesis, candidate, review)
    assert normalized.unresolved_hard_constraints == ("candidate-x:constraint-x",)
    assert changes == ("CANONICALIZED_UNKNOWN_CONSTRAINT_JOIN",)


def test_inference_prediction_is_not_an_unknown_evidence_verdict() -> None:
    with pytest.raises(ValueError, match="LIKELY_PASS"):
        InferredConstraintAssessmentV1(
            constraint_id="constraint-x",
            predicted_verdict="LIKELY_PASS",
            probability_pass=0.2,
            scientific_rationale="A deliberately inconsistent prediction.",
        )

    prediction = InferredConstraintAssessmentV1(
        constraint_id="constraint-x",
        predicted_verdict="LIKELY_FAIL",
        probability_pass=0.2,
        scientific_rationale="The expected orbital overlap is too dispersive.",
    )
    assert prediction.predicted_verdict == "LIKELY_FAIL"


def test_stale_checkpoint_evidence_references_are_dropped() -> None:
    candidates = CandidateSetV1(
        candidates=(
            CandidateHypothesisV1(
                candidate_id="candidate-stale",
                material_name="Stale parent",
                hypothesis="Retain only as an unsupported hypothesis.",
                mechanism="A mechanism requiring fresh verification.",
                evidence_ids=("evidence-" + "1" * 24,),
                database_candidate_ids=("db-candidate-" + "2" * 24,),
            ),
        )
    )
    normalized, changes = _normalize_candidate_references(candidates, (), ())
    assert normalized.candidates[0].evidence_ids == ()
    assert normalized.candidates[0].database_candidate_ids == ()
    assert changes == (
        "DROPPED_STALE_LITERATURE_REFERENCES",
        "DROPPED_STALE_DATABASE_REFERENCES",
    )


def test_director_rejects_incomplete_constraint_matrix() -> None:
    graph = constraints()
    candidate = CandidateSetV1(
        candidates=(
            CandidateHypothesisV1(
                candidate_id="candidate-x",
                material_name="X",
                hypothesis="A bounded hypothesis.",
                mechanism="A possible connected-lattice mechanism.",
            ),
        )
    )
    review = SkepticReviewV1(
        matrix=(
            CandidateConstraintMatrixRowV1(
                candidate_id="candidate-x",
                assessments=(
                    ConstraintAssessmentV1(
                        constraint_id="constraint-layered",
                        verdict="UNKNOWN",
                        rationale="Missing evidence.",
                        next_verification="Inspect structure.",
                    ),
                ),
            ),
        ),
        global_failure_modes=("Missing constraints",),
    )
    from material_agent.inspiration.research_graph import _validate_complete_matrix

    with pytest.raises(ValueError, match="every constraint"):
        _validate_complete_matrix(review, candidate, graph, (), ())


def test_goal_coverage_requires_all_explicit_flat_band_families() -> None:
    from material_agent.inspiration.research_graph import (
        _validate_goal_constraint_coverage,
    )

    goal = (
        "搜索过渡金属层状二维材料，平带必须是费米面附近第一条能带，W<=50meV，"
        "不能和色散带有交点，轨道来自金属配体杂化，检查价态和互连子晶格而非cluster。"
    )
    _validate_goal_constraint_coverage(goal, constraints())
    incomplete = constraints().model_copy(
        update={"constraints": constraints().constraints[:-1]}
    )
    with pytest.raises(ValueError, match="COMPOSITION"):
        _validate_goal_constraint_coverage(goal, incomplete)
