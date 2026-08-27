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
    MAX_RESEARCH_GOAL_CHARACTERS,
    CandidateConstraintMatrixRowV1,
    CandidateHypothesisV1,
    CandidateInferenceRowV1,
    CandidateLiteratureRetrievalV1,
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
    MaterialsResearchGraphResultV7,
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
    _build_inference_context,
    _normalize_candidate_references,
    _normalize_candidate_retrieval_references,
    _normalize_evidence_review_references,
    _normalize_executed_counter_queries,
    _normalize_native_lead_references,
    _normalize_sparse_skeptic_references,
    _normalize_synthesis,
    _validate_query_constraint_refs,
    normalize_research_goal,
    research_goal_forbids_dft,
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


def incomplete_query_plan(graph: ConstraintGraphV1) -> ResearchQueryPlanV1:
    plan = query_plan(graph)
    query = plan.families[1].queries[0].model_copy(
        update={"target_constraint_ids": (graph.constraints[0].constraint_id,)}
    )
    family = plan.families[1].model_copy(update={"queries": (query,)})
    return plan.model_copy(update={"families": (plan.families[0], family)})


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
            document_id="document-" + "9" * 24,
            provider="crossref",
            source_providers=("crossref",),
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
        "requirements_analyst": graph.model_copy(
            update={"constraints": graph.constraints[:-1]}
        ),
        "query_strategist": incomplete_query_plan(graph),
        "native_search_scout": DiscoveryReviewV1(
            useful_lead_ids=("lead-" + "f" * 24,),
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
    repair_outputs: list[StrictModel] = [
        graph,
        plan,
        DiscoveryReviewV1(
            useful_lead_ids=("lead-" + "1" * 24,),
            resolver_queries=("layered transition metal flat band",),
        ),
    ]
    captured_user_payloads: dict[str, Mapping[str, Any]] = {}

    class FakeRunner:
        def __init__(self, role: str, tools: tuple[DeepSeekFunctionTool, ...]) -> None:
            self.role = role
            self.tools = tools

        def run(self, **kwargs: Any) -> DeepSeekAgentResultV1[Any]:
            captured_user_payloads[self.role] = kwargs["user_payload"]
            if self.role == "contract_repair":
                args_model = self.tools[0].arguments_model
                self.tools[0].handler(args_model(sections=("repair_context",)))
                final = repair_outputs.pop(0)
                return DeepSeekAgentResultV1(
                    final=final, receipt=receipt("contract-repair")
                )
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
                    "mechanism_chemist": "mechanism_context",
                    "skeptic": "skeptic_context",
                    "hypothesis_reasoner": "inference_context",
                    "synthesist": "synthesis_context",
                }[self.role]
                args_model = self.tools[0].arguments_model
                self.tools[0].handler(args_model(sections=(section,)))
            return DeepSeekAgentResultV1(
                final=outputs[self.role], receipt=receipt(self.role)
            )

    saved_checkpoint_roles: list[str] = []
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
        checkpoint_save=lambda role, _result: saved_checkpoint_roles.append(role),
    )
    result = director.run(
        "搜索层状过渡金属二维平带材料，并逐项验证费米面、轨道、价态与连通子晶格，"
        "且禁止使用 DFT。"
    )

    assert len(result.roles) == 9
    assert len(result.repairs) == 2
    assert all(item.status == "ACCEPTED" for item in result.repairs)
    assert [item.defect_code for item in result.repairs] == [
        "CONSTRAINT_COVERAGE",
        "QUERY_CONSTRAINT_REFERENCES",
    ]
    assert {
        "requirements_analyst-repaired",
        "query_strategist-repaired",
    }.issubset(saved_checkpoint_roles)
    assert result.deterministic_normalizations == (
        "DROPPED_DANGLING_NATIVE_LEAD_REFERENCES",
        "REMOVED_DFT_NEXT_COMPUTATIONS_FOR_EXPLICIT_NO_DFT_GOAL",
    )
    assert result.synthesis.required_next_computations == ()
    assert set(
        captured_user_payloads["native_search_scout"][
            "validated_research_state"
        ]
    ) == {"goal", "constraints", "query_plan"}
    assert set(
        captured_user_payloads["evidence_researcher"][
            "validated_research_state"
        ]
    ) == {"constraints", "query_plan", "native_leads"}
    assert set(
        captured_user_payloads["database_scout"]["validated_research_state"]
    ) == {"goal", "constraints", "query_plan"}
    assert (
        captured_user_payloads["database_scout"]["state_projection_policy"]
        == "REQUIRED_SECTIONS_ONLY_V1"
    )
    assert captured_user_payloads["hypothesis_reasoner"][
        "available_state_sections"
    ] == ("inference_context",)
    assert captured_user_payloads["mechanism_chemist"][
        "available_state_sections"
    ] == ("mechanism_context",)
    assert captured_user_payloads["skeptic"]["available_state_sections"] == (
        "skeptic_context",
    )
    assert captured_user_payloads["synthesist"]["available_state_sections"] == (
        "synthesis_context",
    )
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


def test_explicit_no_dft_goal_strips_dft_next_computations_with_audit() -> None:
    candidate = CandidateSetV1(
        candidates=(
            CandidateHypothesisV1(
                candidate_id="candidate-no-dft",
                material_name="No-DFT candidate",
                hypothesis="A bounded hypothesis.",
                mechanism="A mechanism requiring ML validation.",
            ),
        )
    )
    review = SkepticReviewV1(
        matrix=(
            CandidateConstraintMatrixRowV1(
                candidate_id="candidate-no-dft",
                assessments=(
                    ConstraintAssessmentV1(
                        constraint_id="constraint-band",
                        verdict="UNKNOWN",
                        rationale="No direct evidence.",
                        next_verification="Run an allowed ML band check.",
                    ),
                ),
            ),
        ),
        global_failure_modes=("The hypothesis may fail.",),
    )
    synthesis = ResearchSynthesisV1(
        ranked_candidate_ids=("candidate-no-dft",),
        recommendation="Retain only as a hypothesis.",
        scientific_conclusion="This remains a falsifiable hypothesis.",
        unresolved_hard_constraints=("candidate-no-dft:constraint-band",),
        required_next_computations=(
            "Run DFT band structure and projected density of states.",
            "Run the permitted learned-Hamiltonian band check.",
        ),
    )

    normalized, changes = _normalize_synthesis(
        synthesis,
        candidate,
        review,
        goal="Use database, literature, and ML only; no DFT or DFT fallback.",
    )

    assert normalized.required_next_computations == (
        "Run the permitted learned-Hamiltonian band check.",
    )
    assert changes == (
        "REMOVED_DFT_NEXT_COMPUTATIONS_FOR_EXPLICIT_NO_DFT_GOAL",
    )
    assert research_goal_forbids_dft("Do not use DFT; keep the route ML-only.")
    assert research_goal_forbids_dft("本任务不需要 DFT，只允许机器学习。")


def test_dft_next_computation_is_preserved_without_an_explicit_ban() -> None:
    candidate = CandidateSetV1(
        candidates=(
            CandidateHypothesisV1(
                candidate_id="candidate-dft-allowed",
                material_name="DFT-allowed candidate",
                hypothesis="A bounded hypothesis.",
                mechanism="A mechanism requiring validation.",
            ),
        )
    )
    review = SkepticReviewV1(
        matrix=(
            CandidateConstraintMatrixRowV1(
                candidate_id="candidate-dft-allowed",
                assessments=(
                    ConstraintAssessmentV1(
                        constraint_id="constraint-band",
                        verdict="FAIL",
                        rationale="Direct evidence falsifies the constraint.",
                    ),
                ),
            ),
        ),
        global_failure_modes=("The hypothesis may fail.",),
    )
    synthesis = ResearchSynthesisV1(
        ranked_candidate_ids=("candidate-dft-allowed",),
        recommendation="Retain only as a hypothesis.",
        scientific_conclusion="This remains a falsifiable hypothesis.",
        required_next_computations=("Run DFT band structure.",),
    )

    normalized, changes = _normalize_synthesis(
        synthesis,
        candidate,
        review,
        goal="Compare ML and DFT validation routes.",
    )

    assert normalized.required_next_computations == ("Run DFT band structure.",)
    assert changes == ()


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


def test_superseded_cross_source_evidence_ids_are_dropped_at_every_join() -> None:
    review = EvidenceReviewV1(
        selected_evidence_ids=("evidence-" + "1" * 24,),
        rejected_evidence_ids=("evidence-" + "2" * 24,),
        limitations=("Canonical merging may supersede tool-returned identities.",),
    )
    normalized_review, review_changes = _normalize_evidence_review_references(
        review, ()
    )
    assert normalized_review.selected_evidence_ids == ()
    assert normalized_review.rejected_evidence_ids == ()
    assert review_changes == ("DROPPED_SUPERSEDED_EVIDENCE_REVIEW_REFERENCES",)

    retrieval = CandidateLiteratureRetrievalV1(
        triggered=True,
        queries=("TiS2 flat band",),
        new_evidence_ids=("evidence-" + "1" * 24,),
    )
    normalized_retrieval, retrieval_changes = (
        _normalize_candidate_retrieval_references(retrieval, ())
    )
    assert normalized_retrieval.new_evidence_ids == ()
    assert retrieval_changes == (
        "DROPPED_SUPERSEDED_CANDIDATE_RETRIEVAL_REFERENCES",
    )

    skeptic = SparseSkepticReviewV1(
        evidence_backed_assessments=(
            SparseConstraintAssessmentV1(
                candidate_id="candidate-stale",
                constraint_id="constraint-layered",
                verdict="PASS",
                evidence_ids=("evidence-" + "1" * 24,),
                rationale="The now-superseded record originally supported this claim.",
            ),
        ),
        global_failure_modes=("Canonical identity can change during federation.",),
    )
    normalized_skeptic, skeptic_changes = _normalize_sparse_skeptic_references(
        skeptic, (), ()
    )
    assert normalized_skeptic.evidence_backed_assessments == ()
    assert skeptic_changes == ("DROPPED_SUPERSEDED_SKEPTIC_REFERENCES",)


def test_only_successfully_executed_counter_queries_survive_normalization() -> None:
    review = SparseSkepticReviewV1(
        counter_evidence_queries=("query executed", "query failed"),
        global_failure_modes=("Counter-search providers may fail independently.",),
    )

    normalized, changes = _normalize_executed_counter_queries(
        review, ("query executed",)
    )

    assert normalized.counter_evidence_queries == ("query executed",)
    assert changes == ("DROPPED_UNEXECUTED_COUNTER_QUERIES",)


def test_inference_context_fairly_bounds_large_candidate_evidence() -> None:
    graph = constraints()
    evidence = tuple(
        ResolvedEvidenceV1(
            evidence_id=f"evidence-{index:024x}",
            document_id=f"document-{index:024x}",
            provider="crossref",
            source_providers=("crossref",),
            stable_record_id=f"10.1000/{index}",
            title="T" * 800,
            abstract_excerpt="A" * 2_000,
            raw_response_uri=f"artifact://research/raw/{index}.json",
            raw_response_sha256=f"{index % 16:x}" * 64,
        )
        for index in range(40)
    )
    candidate_set = CandidateSetV1(
        candidates=tuple(
            CandidateHypothesisV1(
                candidate_id=f"candidate-{name}",
                material_name=name,
                hypothesis="A bounded scientific hypothesis.",
                mechanism="A testable orbital mechanism.",
                evidence_ids=tuple(item.evidence_id for item in evidence[start::2]),
            )
            for name, start in (("even", 0), ("odd", 1))
        )
    )
    skeptic = SkepticReviewV1(
        matrix=tuple(
            CandidateConstraintMatrixRowV1(
                candidate_id=candidate.candidate_id,
                assessments=tuple(
                    ConstraintAssessmentV1(
                        constraint_id=constraint.constraint_id,
                        verdict="UNKNOWN",
                        rationale="Direct evidence is incomplete.",
                        next_verification="Run the required calculation.",
                    )
                    for constraint in graph.constraints
                ),
            )
            for candidate in candidate_set.candidates
        ),
        global_failure_modes=("The proposed mechanism may not survive relaxation.",),
    )

    context = _build_inference_context(
        "Find a layered transition-metal flat-band material.",
        graph,
        candidate_set,
        skeptic,
        evidence,
        (),
    )

    projected = context["candidate_evidence"]
    assert len(projected) == 32
    assert sum(int(item["evidence_id"].split("-")[-1], 16) % 2 == 0 for item in projected) == 16
    assert all(len(item["title"]) == 400 for item in projected)
    assert all(len(item["abstract_excerpt"]) == 1_000 for item in projected)


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


def test_contract_repair_fails_closed_after_two_invalid_attempts() -> None:
    graph = constraints()
    invalid_plan = incomplete_query_plan(graph)
    tool = DeepSeekFunctionTool(
        name="fixture_tool",
        description="Fixture-only state reader.",
        arguments_model=SearchArgs,
        handler=lambda args: {"query": args.query},
    )

    class InvalidRepairRunner:
        def run(self, **kwargs: Any) -> DeepSeekAgentResultV1[Any]:
            del kwargs
            return DeepSeekAgentResultV1(
                final=invalid_plan,
                receipt=receipt("contract-repair-invalid"),
            )

    director = MaterialsResearchDirector(
        runner_factory=lambda _role, _tools: InvalidRepairRunner(),
        native_search_tool=tool,
        native_leads_snapshot=lambda: (),
        authoritative_search_tool=tool,
        evidence_snapshot=lambda: (),
        database_search_tool=tool,
        database_candidates_snapshot=lambda: (),
        database_federation_snapshot=lambda: DatabaseFederationAuditV1(),
    )
    repairs = []
    with pytest.raises(ValueError, match="contract repair exhausted"):
        director._repair_invalid_role_output(
            target_role="query_strategist",
            defect_code="QUERY_CONSTRAINT_REFERENCES",
            final_model=ResearchQueryPlanV1,
            value=invalid_plan,
            validator=lambda item: _validate_query_constraint_refs(item, graph),
            authoritative_context={"constraints": graph.model_dump(mode="json")},
            repairs=repairs,
        )
    assert [item.status for item in repairs] == ["REJECTED", "REJECTED"]


def test_contract_repair_profile_can_fail_closed_after_one_attempt() -> None:
    graph = constraints()
    invalid_plan = incomplete_query_plan(graph)
    tool = DeepSeekFunctionTool(
        name="fixture_tool",
        description="Fixture-only state reader.",
        arguments_model=SearchArgs,
        handler=lambda args: {"query": args.query},
    )

    class InvalidRepairRunner:
        def run(self, **kwargs: Any) -> DeepSeekAgentResultV1[Any]:
            del kwargs
            return DeepSeekAgentResultV1(
                final=invalid_plan,
                receipt=receipt("contract-repair-invalid"),
            )

    director = MaterialsResearchDirector(
        runner_factory=lambda _role, _tools: InvalidRepairRunner(),
        native_search_tool=tool,
        native_leads_snapshot=lambda: (),
        authoritative_search_tool=tool,
        evidence_snapshot=lambda: (),
        database_search_tool=tool,
        database_candidates_snapshot=lambda: (),
        database_federation_snapshot=lambda: DatabaseFederationAuditV1(),
        max_contract_repair_attempts=1,
    )
    repairs = []
    with pytest.raises(ValueError, match="contract repair exhausted"):
        director._repair_invalid_role_output(
            target_role="query_strategist",
            defect_code="QUERY_CONSTRAINT_REFERENCES",
            final_model=ResearchQueryPlanV1,
            value=invalid_plan,
            validator=lambda item: _validate_query_constraint_refs(item, graph),
            authoritative_context={"constraints": graph.model_dump(mode="json")},
            repairs=repairs,
        )
    assert [item.status for item in repairs] == ["REJECTED"]


def test_native_lead_normalization_drops_only_dangling_review_references() -> None:
    review = DiscoveryReviewV1(
        useful_lead_ids=("lead-known", "lead-dangling"),
        rejected_lead_ids=("lead-rejected",),
        resolver_queries=("fractional valence Lieb lattice",),
    )
    normalized, changes = _normalize_native_lead_references(
        review,
        (
            {"lead_id": "lead-known", "title": "known"},
            {"lead_id": "lead-rejected", "title": "rejected"},
        ),
    )

    assert normalized.useful_lead_ids == ("lead-known",)
    assert normalized.rejected_lead_ids == ("lead-rejected",)
    assert changes == ("DROPPED_DANGLING_NATIVE_LEAD_REFERENCES",)


def test_lead_resolution_ledger_capacity_covers_large_federated_runs() -> None:
    schema = MaterialsResearchGraphResultV7.model_json_schema()
    assert schema["properties"]["lead_evidence_resolutions"]["maxItems"] == 1_024


def test_research_goal_capacity_matches_outer_hermes_contract() -> None:
    complete_goal = "fractional-valence Lieb-lattice constraint " * 140

    assert len(complete_goal) > 4_000
    assert normalize_research_goal(complete_goal) == complete_goal.strip()

    with pytest.raises(ValueError, match=str(MAX_RESEARCH_GOAL_CHARACTERS)):
        normalize_research_goal("x" * (MAX_RESEARCH_GOAL_CHARACTERS + 1))
