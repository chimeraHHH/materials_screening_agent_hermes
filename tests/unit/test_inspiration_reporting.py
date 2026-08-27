from __future__ import annotations

from material_agent.inspiration.models import (
    REQUIRED_STRUCTURE_NONFAIL_CHECK_IDS,
    REQUIRED_STRUCTURE_PASS_CHECK_IDS,
    ArtifactPointerV1,
    BridgePacketV1,
    CandidateRouteRefV1,
    CandidateScoresV1,
    ComponentSnapshotV1,
    CostLedgerV1,
    EvidenceCardV1,
    EvidenceRelation,
    InspirationBundleV1,
    InspirationCandidateV1,
    InspirationInputV1,
    InspirationOutcome,
    ParentCandidateRefV1,
    PassageLocatorKind,
    PassageLocatorV1,
    PassageV1,
    SearchHitV1,
    SearchQueryKind,
    SearchQueryV1,
    SubstitutionParametersV1,
    TransformationPlanV1,
    TransformationStatus,
    ValidationCheckV1,
    ValidationStatus,
    hypothesis_signature_sha256_for,
    transformation_route_sha256,
)
from material_agent.inspiration.reporting import render_inspiration_report
from material_agent.inspiration.search import SearchAttemptRecord


def _artifact(
    path: str,
    digest: str,
    *,
    media_type: str = "application/json",
) -> ArtifactPointerV1:
    return ArtifactPointerV1(
        uri=f"artifact://runs/reporting/{path}",
        sha256=digest * 64,
        size_bytes=64,
        media_type=media_type,
    )


def _component(name: str, digest: str) -> ComponentSnapshotV1:
    return ComponentSnapshotV1(
        component_id=name,
        version="1",
        implementation_sha256=digest * 64,
    )


def _report_fixture() -> dict[str, object]:
    inspiration_input = InspirationInputV1(
        project_id="project-report",
        request_id="request-report",
        run_id="run-report",
        requirement_revision=1,
        requirement_artifact=_artifact("requirement.json", "1"),
        parent_candidates=(
            ParentCandidateRefV1(
                candidate_id="parent-1",
                structure_id="parent-structure-1",
                structure_artifact=_artifact(
                    "parent.cif", "2", media_type="chemical/x-cif"
                ),
            ),
        ),
        policy_artifact=_artifact("policy.json", "3"),
        tag_graph_artifact=_artifact("tag-graph.json", "4"),
        transformation_registry_artifact=_artifact("registry.json", "5"),
        search_adapter=_component("crossref-metadata", "6"),
        vectorizer=_component("local-hash-vectorizer", "7"),
    )
    queries = (
        SearchQueryV1(
            query_id="query-bridge",
            kind=SearchQueryKind.BRIDGE,
            text="photonic compact-localized-state connectivity",
            tag_ids=("compact-localized-state", "photonic-domain"),
            bridge_rule_id="bridge-rule-1",
        ),
        SearchQueryV1(
            query_id="query-counter",
            kind=SearchQueryKind.COUNTER,
            text="path imbalance breaks compact localized state",
            tag_ids=("compact-localized-state",),
            bridge_rule_id="bridge-rule-1",
        ),
    )
    hits = (
        SearchHitV1(
            hit_id="hit-bridge",
            document_id="document-1",
            provider="crossref",
            provider_record_id="10.1000/audit",
            query_ids=("query-bridge",),
            provider_rank=1,
            title="Bounded source with ``backticks``\n## injected heading",
            authors=("Example Author",),
            published_year=2025,
            doi="10.1000/audit",
            canonical_url="https://example.org/article",
            raw_response_artifact=_artifact("raw/query-bridge.json", "8"),
        ),
        SearchHitV1(
            hit_id="hit-counter",
            document_id="document-1",
            provider="crossref",
            provider_record_id="10.1000/audit",
            query_ids=("query-counter",),
            provider_rank=2,
            title="Same document from the counter query",
            doi="10.1000/audit",
            canonical_url="https://example.org/article",
            raw_response_artifact=_artifact("raw/query-counter.json", "9"),
        ),
    )
    passage_text = "FULL_PASSAGE_SOURCE_TEXT_MUST_NOT_BE_REPEATED"
    passage = PassageV1(
        passage_id="passage-1",
        hit_id="hit-bridge",
        document_id="document-1",
        source_artifact=hits[0].raw_response_artifact,
        normalizer=_component("passage-normalizer", "a"),
        locator=PassageLocatorV1(
            kind=PassageLocatorKind.JSON_PATH,
            selector="$.message.items[0].abstract",
            section_heading="Abstract",
            start_offset=4,
            end_offset=44,
        ),
        text=passage_text,
        char_count=len(passage_text),
        estimated_token_count=8,
        normalized_text_sha256="b" * 64,
        matched_tag_ids=("compact-localized-state", "destructive-interference"),
        lexical_score=0.875,
    )
    evidence = EvidenceCardV1(
        evidence_card_id="evidence-1",
        relation=EvidenceRelation.SUPPORT,
        claim_text="FULL_EVIDENCE_ASSERTION_MUST_NOT_BE_REPEATED",
        mechanism_tag_ids=("compact-localized-state",),
        applicability_conditions=("bounded source assertion only",),
        counterevidence=("A counter condition remains unresolved.",),
        passage_ids=(passage.passage_id,),
    )
    bridge = BridgePacketV1(
        bridge_packet_id="bridge-1",
        bridge_rule_id="bridge-rule-1",
        source_domain_tag_ids=("photonic-domain",),
        target_tag_ids=("electronic-flat-band",),
        shared_invariant="A connectivity constraint can confine a mode.",
        transferable_control="Preserve connectivity while changing carrier domain.",
        required_conditions=("coherent competing paths",),
        breaking_conditions=("path imbalance",),
        suggested_queries=("photonic connectivity compact mode",),
        evidence_card_ids=(evidence.evidence_card_id,),
    )
    parameters = SubstitutionParametersV1(
        equivalent_site_indices=(0, 2),
        source_species="S",
        target_species="Se",
    )
    route_sha256 = transformation_route_sha256(
        parent_structure_id="parent-structure-1",
        operator_id="SUBSTITUTE_EQUIVALENT_SITE_V1",
        operator_version="1",
        parameters=parameters,
    )
    checks = tuple(
        ValidationCheckV1(
            check_id=check_id,
            status=ValidationStatus.PASS,
            detail=f"{check_id} passed as a structure check.",
        )
        for check_id in REQUIRED_STRUCTURE_PASS_CHECK_IDS
    ) + tuple(
        ValidationCheckV1(
            check_id=check_id,
            status=ValidationStatus.PASS,
            detail=f"{check_id} passed as a structure check.",
        )
        for check_id in REQUIRED_STRUCTURE_NONFAIL_CHECK_IDS
    )
    plan = TransformationPlanV1(
        plan_id="plan-1",
        parent_candidate_id="parent-1",
        parent_structure_id="parent-structure-1",
        parent_structure_artifact=(
            inspiration_input.parent_candidates[0].structure_artifact
        ),
        parameters=parameters,
        preserved_features=("ordered lattice",),
        changed_features=("equivalent-site species",),
        falsification_tests=("run a downstream structure and property calculation",),
        bridge_packet_ids=(bridge.bridge_packet_id,),
        route_sha256=route_sha256,
        status=TransformationStatus.STRUCTURE_VALID,
        validation_checks=checks,
        output_structure_id="proposal-structure-1",
        output_structure_artifact=_artifact(
            "proposal.cif", "c", media_type="chemical/x-cif"
        ),
    )
    route = CandidateRouteRefV1(
        plan_id=plan.plan_id,
        route_sha256=route_sha256,
        parent_candidate_id="parent-1",
        mechanism_tag_ids=("compact-localized-state",),
        bridge_packet_ids=(bridge.bridge_packet_id,),
        evidence_card_ids=(evidence.evidence_card_id,),
    )
    signature = hypothesis_signature_sha256_for(
        canonical_structure_id="proposal-structure-1",
        mechanism_tag_ids=route.mechanism_tag_ids,
        route_sha256s=(route.route_sha256,),
    )
    candidate = InspirationCandidateV1(
        candidate_id="candidate-1",
        canonical_structure_id="proposal-structure-1",
        structure_artifact=plan.output_structure_artifact,
        representative_plan_id=plan.plan_id,
        merged_routes=(route,),
        parent_candidate_ids=("parent-1",),
        mechanism_tag_ids=route.mechanism_tag_ids,
        evidence_card_ids=route.evidence_card_ids,
        hypothesis_signature_sha256=signature,
        scores=CandidateScoresV1(
            policy_id="selection-policy-1",
            quality=0.8,
            evidence_coverage=0.6,
            redundancy_penalty=0.2,
            selection_score=0.52,
        ),
        selection_rank=1,
        next_falsification_step="Run the least-cost downstream calculation.",
    )
    ledger = CostLedgerV1(
        search_requests=3,
        search_response_bytes=1234,
        fetch_requests=0,
        fetch_response_bytes=0,
        raw_documents=2,
        unique_documents=1,
        extracted_passages=1,
        vectorized_passages=1,
        embedding_input_tokens=8,
        llm_calls=0,
        llm_input_tokens=0,
        llm_output_tokens=0,
        generated_plans=1,
        rejected_plans=0,
        candidates_after_internal_dedup=1,
        walltime_ms=123,
    )
    bundle = InspirationBundleV1(
        bundle_id="bundle-report",
        request_id=inspiration_input.request_id,
        run_id=inspiration_input.run_id,
        outcome=InspirationOutcome.SUCCEEDED,
        selected_candidates=(candidate,),
        limitations=("The proposed property remains UNKNOWN.",),
        next_validation_steps=("Run bounded downstream validation.",),
        cost_ledger=ledger,
        lineage_artifacts=(_artifact("evidence.jsonl", "d"),),
    )
    attempts = (
        SearchAttemptRecord(
            query_id="query-bridge",
            attempt_number=1,
            outcome="error",
            error_code="HTTP_ERROR",
            http_status=429,
            retry_delay_seconds=2.0,
            response_bytes=0,
        ),
        SearchAttemptRecord(
            query_id="query-bridge",
            attempt_number=2,
            outcome="success",
            error_code=None,
            http_status=200,
            retry_delay_seconds=0.0,
            response_bytes=700,
            pacing_delay_seconds=2.0,
        ),
        SearchAttemptRecord(
            query_id="query-counter",
            attempt_number=1,
            outcome="success",
            error_code=None,
            http_status=200,
            retry_delay_seconds=0.0,
            response_bytes=534,
            pacing_delay_seconds=1.0,
        ),
    )
    return {
        "inspiration_input": inspiration_input,
        "bundle": bundle,
        "queries": queries,
        "hits": hits,
        "passages": (passage,),
        "evidence_cards": (evidence,),
        "bridge_packets": (bridge,),
        "transformation_plans": (plan,),
        "review_items": ("Review the UNKNOWN property before downstream use.",),
        "warnings": ("BOUNDED_METADATA_ONLY",),
        "search_attempts": attempts,
    }


def test_report_exposes_complete_audit_lineage_without_source_text_or_claim_upgrade(
) -> None:
    data = _report_fixture()
    report = render_inspiration_report(**data)  # type: ignore[arg-type]

    for heading in (
        "## Auditable execution funnel",
        "## Complete cost ledger",
        "## Search attempt ledger",
        "## Metadata queries",
        "## Document-source lineage",
        "## Selected bounded passages",
        "## Evidence-card lineage",
        "## Search-supported bridge packets",
        "## Transformation lineage",
        "## Candidate proposals",
    ):
        assert heading in report

    for expected in (
        "Provider search attempts (ledger): `3`",
        "Duplicate raw-hit documents collapsed: `1`",
        "`search_response_bytes` | `1234`",
        "`query-bridge`",
        "`429`",
        "`10.1000/audit`",
        "`https://example.org/article`",
        "`artifact://runs/reporting/raw/query-bridge.json`",
        "`$.message.items[0].abstract`",
        "`4:44`",
        f"`{'b' * 64}`",
        "`compact-localized-state, destructive-interference`",
        "A connectivity constraint can confine a mode.",
        "Preserve connectivity while changing carrier domain.",
        "`coherent competing paths`",
        "`path imbalance`",
        "`artifact://runs/reporting/proposal.cif`",
        f"`{'c' * 64}`",
        "Redundancy penalty: `0.2`",
        "Selection score: `0.52`",
        "Merged route lineage",
        "Target property status: `UNKNOWN`",
        "Property status: `UNKNOWN`",
        "Scientific conclusion: `false`",
        "PDF full-text reads: `0`",
        "LLM calls: `0`",
    ):
        assert expected in report

    assert "FULL_PASSAGE_SOURCE_TEXT_MUST_NOT_BE_REPEATED" not in report
    assert "FULL_EVIDENCE_ASSERTION_MUST_NOT_BE_REPEATED" not in report
    assert "\n## injected heading" not in report
    assert (
        "- Title: ```Bounded source with ``backticks`` ## injected heading```"
        in report
    )
    assert "novelty" not in report.casefold()


def test_report_keeps_existing_calls_compatible_when_attempt_details_are_absent(
) -> None:
    data = _report_fixture()
    data.pop("search_attempts")

    report = render_inspiration_report(**data)  # type: ignore[arg-type]

    assert "Attempt records attached to this report: `0`" in report
    assert "No per-attempt records were attached to this report." in report
    assert "Provider search attempts (ledger): `3`" in report
