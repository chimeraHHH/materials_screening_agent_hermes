from __future__ import annotations

import math

import pytest
from pydantic import ValidationError

from material_agent.inspiration import (
    REQUIRED_STRUCTURE_NONFAIL_CHECK_IDS,
    REQUIRED_STRUCTURE_PASS_CHECK_IDS,
    ArtifactPointerV1,
    BridgePacketV1,
    BridgeRuleV1,
    CandidateRouteRefV1,
    CandidateScoresV1,
    ComponentSnapshotV1,
    CostLedgerV1,
    InspirationBundleV1,
    InspirationCandidateV1,
    InspirationOutcome,
    InspirationPolicyV1,
    PassageLocatorKind,
    PassageLocatorV1,
    PassageV1,
    SearchQueryKind,
    SearchQueryV1,
    SubstitutionParametersV1,
    TagDefinitionV1,
    TagGraphV1,
    TagKind,
    TransformationPlanV1,
    TransformationStatus,
    ValidationCheckV1,
    ValidationStatus,
    canonical_json_bytes,
    canonical_sha256,
    deterministic_id,
    hypothesis_signature_sha256_for,
    transformation_route_sha256,
)
from material_agent.inspiration.policy import SearchBudgetV1


def artifact(name: str, digest: str = "a") -> ArtifactPointerV1:
    return ArtifactPointerV1(
        uri=f"artifact://inspiration/{name}",
        sha256=digest * 64,
        media_type="application/json",
    )


def test_search_budget_accepts_bounded_historical_year_window_and_rejects_reverse() -> None:
    budget = SearchBudgetV1(
        publication_year_from=1960,
        publication_year_to=1990,
    )
    assert budget.publication_year_from == 1960
    assert budget.publication_year_to == 1990

    with pytest.raises(ValidationError):
        SearchBudgetV1(
            publication_year_from=1990,
            publication_year_to=1960,
        )


def required_structure_checks() -> tuple[ValidationCheckV1, ...]:
    checks = [
        ValidationCheckV1(
            check_id=check_id,
            status=ValidationStatus.PASS,
            detail=f"{check_id} passed.",
        )
        for check_id in REQUIRED_STRUCTURE_PASS_CHECK_IDS
    ]
    checks.extend(
        ValidationCheckV1(
            check_id=check_id,
            status=ValidationStatus.UNKNOWN,
            detail=f"{check_id} requires downstream evidence.",
        )
        for check_id in REQUIRED_STRUCTURE_NONFAIL_CHECK_IDS
    )
    return tuple(checks)


def all_pass_structure_checks() -> tuple[ValidationCheckV1, ...]:
    return tuple(
        ValidationCheckV1(
            check_id=check_id,
            status=ValidationStatus.PASS,
            detail=f"{check_id} passed.",
        )
        for check_id in (
            *REQUIRED_STRUCTURE_PASS_CHECK_IDS,
            *REQUIRED_STRUCTURE_NONFAIL_CHECK_IDS,
        )
    )


def test_canonical_hash_and_id_are_deterministic() -> None:
    first = {"z": 1, "a": {"β": 2, "a": 3}}
    second = {"a": {"a": 3, "β": 2}, "z": 1}

    assert canonical_json_bytes(first) == canonical_json_bytes(second)
    assert canonical_sha256(first) == canonical_sha256(second)
    assert deterministic_id("query", first) == deterministic_id("query", second)
    assert deterministic_id("query", {**first, "z": 2}) != deterministic_id(
        "query", first
    )

    with pytest.raises(ValueError):
        deterministic_id("Bad Prefix", first)
    with pytest.raises(ValueError):
        canonical_sha256({"score": math.nan})


def test_models_reject_extra_fields_and_unsafe_artifact_uris() -> None:
    with pytest.raises(ValidationError):
        SearchQueryV1(
            query_id="q1",
            kind=SearchQueryKind.DIRECT,
            text="flat band mechanism",
            tag_ids=("flat-band",),
            unexpected=True,
        )

    with pytest.raises(ValidationError):
        CostLedgerV1(raw_documents="1")

    for uri in (
        "/tmp/raw.json",
        "file:///tmp/raw.json",
        "artifact:///absolute.json",
        "artifact://raw/../secret.json",
        "artifact://raw\\secret.json",
    ):
        with pytest.raises(ValidationError):
            ArtifactPointerV1(uri=uri, sha256="a" * 64)


def test_search_query_bridge_reference_is_explicit() -> None:
    direct = SearchQueryV1(
        query_id="q-direct",
        kind=SearchQueryKind.DIRECT,
        text="flat band destructive interference",
        tag_ids=("flat-band", "destructive-interference"),
    )
    assert direct.bridge_rule_id is None

    with pytest.raises(ValidationError):
        SearchQueryV1(
            query_id="q-bridge",
            kind=SearchQueryKind.BRIDGE,
            text="photonic compact localized state",
            tag_ids=("photonic",),
        )
    with pytest.raises(ValidationError):
        SearchQueryV1(
            query_id="q-direct",
            kind=SearchQueryKind.DIRECT,
            text="flat band mechanism",
            tag_ids=("flat-band",),
            bridge_rule_id="bridge-rule-1",
        )


def test_passage_requires_a_locatable_finite_bounded_excerpt() -> None:
    locator = PassageLocatorV1(
        kind=PassageLocatorKind.JSON_PATH,
        selector="$.results[0].abstract",
    )
    text = "Compact localized states suppress dispersion."
    passage = PassageV1(
        passage_id="passage-1",
        hit_id="hit-1",
        document_id="document-1",
        source_artifact=artifact("raw/openalex.json"),
        normalizer=ComponentSnapshotV1(
            component_id="passage-normalizer",
            version="1",
            implementation_sha256="c" * 64,
        ),
        locator=locator,
        text=text,
        char_count=len(text),
        estimated_token_count=7,
        normalized_text_sha256="b" * 64,
        matched_tag_ids=("compact-localized-state",),
        lexical_score=0.9,
    )
    assert passage.char_count == len(passage.text)

    with pytest.raises(ValidationError):
        PassageV1.model_validate({**passage.model_dump(), "char_count": 1})
    with pytest.raises(ValidationError):
        PassageV1.model_validate({**passage.model_dump(), "lexical_score": math.inf})


def test_bridge_requires_conditions_breakers_queries_and_evidence() -> None:
    valid = {
        "bridge_packet_id": "bridge-1",
        "bridge_rule_id": "bridge-rule-1",
        "source_domain_tag_ids": ("photonic",),
        "target_tag_ids": ("electronic-flat-band",),
        "shared_invariant": "A local interference constraint creates a compact mode.",
        "transferable_control": "Tune connectivity while preserving the local constraint.",
        "required_conditions": ("coherent path amplitudes",),
        "breaking_conditions": ("symmetry-breaking path imbalance",),
        "suggested_queries": ("photonic compact localized mode connectivity",),
        "evidence_card_ids": ("evidence-1",),
    }
    assert BridgePacketV1(**valid).status == "SEARCH_SUPPORTED"

    for field in (
        "required_conditions",
        "breaking_conditions",
        "suggested_queries",
        "evidence_card_ids",
    ):
        with pytest.raises(ValidationError):
            BridgePacketV1(**{**valid, field: ()})


def test_curated_bridge_rule_precedes_search_supported_packet() -> None:
    tags = (
        TagDefinitionV1(
            tag_id="photonic",
            kind=TagKind.ANALOGY_DOMAIN,
            label="Photonic lattice",
            description="Wave-interference analogies in photonic lattices.",
            query_terms=("photonic compact localized state",),
        ),
        TagDefinitionV1(
            tag_id="compact-localized-state",
            kind=TagKind.MECHANISM,
            label="Compact localized state",
            description="A local destructive-interference mechanism.",
            query_terms=("compact localized state",),
        ),
        TagDefinitionV1(
            tag_id="electronic-flat-band",
            kind=TagKind.PROPERTY,
            label="Electronic flat band",
            description="Low-dispersion electronic bands near the target energy.",
            query_terms=("electronic flat band",),
        ),
    )
    rule = BridgeRuleV1(
        bridge_rule_id="bridge-rule-1",
        rule_version="1",
        source_domain_tag_ids=("photonic",),
        target_tag_ids=("electronic-flat-band",),
        required_evidence_tag_ids=("compact-localized-state",),
        suggested_query_tag_ids=("photonic", "compact-localized-state"),
        query_templates=("{source} {mechanism} connectivity",),
        shared_invariant="Destructive path interference confines a wave mode.",
        transferable_control="Preserve connectivity while changing the carrier medium.",
        required_conditions=("coherent competing paths",),
        breaking_conditions=("strong path imbalance",),
    )
    graph = TagGraphV1(
        graph_id="tag-graph-1",
        graph_version="1",
        tags=tags,
        bridge_rules=(rule,),
    )
    assert graph.bridge_rules[0].bridge_rule_id == "bridge-rule-1"

    with pytest.raises(ValidationError, match="unknown tag"):
        TagGraphV1(
            graph_id="tag-graph-1",
            graph_version="1",
            tags=tags,
            bridge_rules=(
                rule.model_copy(update={"target_tag_ids": ("missing-tag",)}),
            ),
        )


def test_transformation_route_and_status_are_fail_closed() -> None:
    parameters = SubstitutionParametersV1(
        equivalent_site_indices=(0, 2),
        source_species="S",
        target_species="Se",
    )
    route_sha256 = transformation_route_sha256(
        parent_structure_id="structure-parent",
        operator_id="SUBSTITUTE_EQUIVALENT_SITE_V1",
        operator_version="1",
        parameters=parameters,
    )
    common = {
        "plan_id": deterministic_id("plan", {"route_sha256": route_sha256}),
        "parent_candidate_id": "candidate-parent",
        "parent_structure_id": "structure-parent",
        "parent_structure_artifact": artifact("structures/parent.cif"),
        "parameters": parameters,
        "preserved_features": ("ordered periodic lattice",),
        "changed_features": ("equivalent-site species",),
        "falsification_tests": ("relax and inspect minimum distance",),
        "bridge_packet_ids": ("bridge-1",),
        "route_sha256": route_sha256,
    }
    planned = TransformationPlanV1(**common, status=TransformationStatus.PLANNED)
    assert planned.scientific_conclusion is False

    with pytest.raises(ValidationError):
        TransformationPlanV1(
            **{**common, "route_sha256": "f" * 64},
            status=TransformationStatus.PLANNED,
        )
    with pytest.raises(ValidationError):
        TransformationPlanV1(**common, status=TransformationStatus.REJECTED)

    rejected = TransformationPlanV1(
        **common,
        status=TransformationStatus.REJECTED,
        validation_checks=(
            ValidationCheckV1(
                check_id="minimum-distance",
                status=ValidationStatus.FAIL,
                detail="Inter-site distance is below the policy threshold.",
            ),
        ),
    )
    assert rejected.output_structure_artifact is None

    with pytest.raises(ValidationError):
        TransformationPlanV1(
            **common,
            status=TransformationStatus.STRUCTURE_VALID,
            validation_checks=(
                ValidationCheckV1(
                    check_id="minimum-distance",
                    status=ValidationStatus.FAIL,
                    detail="Minimum-distance check failed.",
                ),
            ),
            output_structure_id="structure-output",
            output_structure_artifact=artifact("structures/output.cif", "b"),
        )

    review_required = TransformationPlanV1(
        **common,
        status=TransformationStatus.REQUIRES_REVIEW,
        validation_checks=required_structure_checks(),
        output_structure_id="structure-output",
        output_structure_artifact=artifact("structures/output.cif", "b"),
    )
    assert review_required.status is TransformationStatus.REQUIRES_REVIEW

    with pytest.raises(ValidationError, match="required PASS checks"):
        TransformationPlanV1(
            **common,
            status=TransformationStatus.STRUCTURE_VALID,
            validation_checks=required_structure_checks(),
            output_structure_id="structure-output",
            output_structure_artifact=artifact("structures/output.cif", "b"),
        )

    structure_valid = TransformationPlanV1(
        **common,
        status=TransformationStatus.STRUCTURE_VALID,
        validation_checks=all_pass_structure_checks(),
        output_structure_id="structure-output",
        output_structure_artifact=artifact("structures/output.cif", "b"),
    )
    assert structure_valid.status is TransformationStatus.STRUCTURE_VALID

    with pytest.raises(ValidationError, match="strictly increasing"):
        SubstitutionParametersV1(
            equivalent_site_indices=(2, 0),
            source_species="S",
            target_species="Se",
        )


def test_bundle_keeps_internal_lineage_and_rank_order() -> None:
    routes = (
        CandidateRouteRefV1(
            plan_id="plan-1",
            route_sha256="b" * 64,
            parent_candidate_id="parent-1",
            mechanism_tag_ids=("compact-localized-state",),
            bridge_packet_ids=("bridge-1",),
            evidence_card_ids=("evidence-1",),
        ),
        CandidateRouteRefV1(
            plan_id="plan-2",
            route_sha256="c" * 64,
            parent_candidate_id="parent-1",
            mechanism_tag_ids=("compact-localized-state",),
            bridge_packet_ids=("bridge-1",),
            evidence_card_ids=("evidence-1",),
        ),
    )
    signature = hypothesis_signature_sha256_for(
        canonical_structure_id="structure-1",
        mechanism_tag_ids=("compact-localized-state",),
        route_sha256s=("b" * 64, "c" * 64),
    )
    candidate = InspirationCandidateV1(
        candidate_id="candidate-1",
        canonical_structure_id="structure-1",
        structure_artifact=artifact("structures/candidate-1.cif"),
        representative_plan_id="plan-1",
        merged_routes=routes,
        parent_candidate_ids=("parent-1",),
        mechanism_tag_ids=("compact-localized-state",),
        evidence_card_ids=("evidence-1",),
        hypothesis_signature_sha256=signature,
        scores=CandidateScoresV1(
            policy_id="inspiration-default-v1",
            quality=0.8,
            evidence_coverage=0.7,
            redundancy_penalty=0.1,
            selection_score=0.56,
        ),
        selection_rank=1,
        next_falsification_step="Compute the parent and candidate band structures.",
    )
    ledger = CostLedgerV1(
        raw_documents=3,
        unique_documents=2,
        extracted_passages=2,
        vectorized_passages=2,
        generated_plans=2,
        rejected_plans=0,
        candidates_after_internal_dedup=1,
    )
    bundle = InspirationBundleV1(
        bundle_id="bundle-1",
        request_id="request-1",
        run_id="run-1",
        outcome=InspirationOutcome.SUCCEEDED,
        selected_candidates=(candidate,),
        limitations=("Property behavior has not been calculated.",),
        next_validation_steps=("Run the least-cost band-structure check.",),
        cost_ledger=ledger,
        lineage_artifacts=(artifact("evidence/cards.jsonl"),),
    )
    assert tuple(route.plan_id for route in bundle.selected_candidates[0].merged_routes) == (
        "plan-1",
        "plan-2",
    )
    assert bundle.scientific_conclusion is False

    with pytest.raises(ValidationError, match="weighted components"):
        CandidateScoresV1(
            policy_id="inspiration-default-v1",
            quality=0.8,
            evidence_coverage=0.7,
            redundancy_penalty=0.1,
            selection_score=0.57,
        )

    with pytest.raises(ValidationError):
        InspirationBundleV1.model_validate(
            {
                **bundle.model_dump(),
                "outcome": "SCIENTIFIC_NO_MATCH",
            }
        )
    with pytest.raises(ValidationError):
        InspirationBundleV1.model_validate(
            {**bundle.model_dump(), "scientific_conclusion": True}
        )


def test_default_policy_is_offline_bounded_and_cross_checked() -> None:
    policy = InspirationPolicyV1()
    assert policy.search_mode == "OFFLINE_FIXTURE"
    assert policy.network_access is False
    assert policy.fetch.allow_pdf_fulltext is False
    assert policy.llm.enabled is False
    assert policy.llm.max_calls == 0
    assert policy.bridge.max_graph_hops == 2
    assert policy.bridge.beam_width == 6
    assert policy.selection.top_k == 5

    with pytest.raises(ValidationError):
        InspirationPolicyV1(fetch={"max_requests": 61})
    with pytest.raises(ValidationError):
        InspirationPolicyV1(network_access=True)
    with pytest.raises(ValidationError):
        InspirationPolicyV1(
            llm={
                "enabled": False,
                "max_calls": 1,
                "max_input_tokens": 100,
                "max_output_tokens": 10,
            }
        )
    with pytest.raises(ValidationError):
        InspirationPolicyV1(
            selection={
                "structure_distance_weight": 0.5,
                "composition_distance_weight": 0.2,
                "route_distance_weight": 0.2,
                "mechanism_distance_weight": 0.2,
            }
        )
