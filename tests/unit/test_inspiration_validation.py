from __future__ import annotations

from dataclasses import dataclass, replace

import pytest

from material_agent.inspiration import (
    ArtifactPointerV1,
    BridgePacketV1,
    BridgeRuleV1,
    ComponentSnapshotV1,
    EvidenceCardV1,
    EvidenceRelation,
    InspirationIntegrityError,
    PassageLocatorKind,
    PassageLocatorV1,
    PassageV1,
    SearchHitV1,
    SearchQueryKind,
    SearchQueryV1,
    TagDefinitionV1,
    TagGraphV1,
    TagKind,
    validate_search_supported_bridge,
)


def artifact(name: str, digest: str) -> ArtifactPointerV1:
    return ArtifactPointerV1(
        uri=f"artifact://inspiration/{name}",
        sha256=digest * 64,
        media_type="application/json",
    )


NORMALIZER = ComponentSnapshotV1(
    component_id="passage-normalizer",
    version="1",
    implementation_sha256="9" * 64,
)


@dataclass(frozen=True)
class BridgeFixture:
    packet: BridgePacketV1
    graph: TagGraphV1
    queries: tuple[SearchQueryV1, ...]
    hits: tuple[SearchHitV1, ...]
    passages: tuple[PassageV1, ...]
    cards: tuple[EvidenceCardV1, ...]

    def validate(self) -> None:
        validate_search_supported_bridge(
            self.packet,
            tag_graph=self.graph,
            queries=self.queries,
            hits=self.hits,
            passages=self.passages,
            evidence_cards=self.cards,
        )


def bridge_fixture() -> BridgeFixture:
    tags = (
        TagDefinitionV1(
            tag_id="photonic-domain",
            kind=TagKind.ANALOGY_DOMAIN,
            label="Photonic lattice",
            description="Wave-interference analogies in photonic lattices.",
            query_terms=("photonic compact localized state",),
        ),
        TagDefinitionV1(
            tag_id="electronic-flat-band",
            kind=TagKind.PROPERTY,
            label="Electronic flat band",
            description="Low-dispersion electronic bands near the target energy.",
            query_terms=("electronic flat band",),
        ),
        TagDefinitionV1(
            tag_id="compact-localized-state",
            kind=TagKind.MECHANISM,
            label="Compact localized state",
            description="A local destructive-interference mechanism.",
            query_terms=("compact localized state",),
        ),
        TagDefinitionV1(
            tag_id="path-imbalance",
            kind=TagKind.MECHANISM,
            label="Path imbalance",
            description="An imbalance that can break destructive interference.",
            query_terms=("path imbalance",),
        ),
    )
    rule = BridgeRuleV1(
        bridge_rule_id="bridge-rule-1",
        rule_version="1",
        source_domain_tag_ids=("photonic-domain",),
        target_tag_ids=("electronic-flat-band",),
        required_evidence_tag_ids=("compact-localized-state",),
        suggested_query_tag_ids=(
            "photonic-domain",
            "compact-localized-state",
        ),
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
    queries = (
        SearchQueryV1(
            query_id="query-bridge",
            kind=SearchQueryKind.BRIDGE,
            text="photonic compact localized state connectivity",
            tag_ids=("photonic-domain", "compact-localized-state"),
            bridge_rule_id=rule.bridge_rule_id,
        ),
        SearchQueryV1(
            query_id="query-counter",
            kind=SearchQueryKind.COUNTER,
            text="photonic path imbalance compact localized state",
            tag_ids=("photonic-domain", "path-imbalance"),
            bridge_rule_id=rule.bridge_rule_id,
        ),
    )
    hits = (
        SearchHitV1(
            hit_id="hit-support",
            document_id="document-support",
            provider="fixture",
            provider_record_id="fixture-support",
            query_ids=("query-bridge",),
            provider_rank=1,
            title="Compact localized states in a photonic lattice",
            raw_response_artifact=artifact("raw/support.json", "a"),
        ),
        SearchHitV1(
            hit_id="hit-counter",
            document_id="document-counter",
            provider="fixture",
            provider_record_id="fixture-counter",
            query_ids=("query-counter",),
            provider_rank=1,
            title="Path imbalance destroys compact localization",
            raw_response_artifact=artifact("raw/counter.json", "b"),
        ),
    )
    support_text = "Competing paths create a compact localized photonic mode."
    counter_text = "Strong path imbalance destroys the localized mode."
    passages = (
        PassageV1(
            passage_id="passage-support",
            hit_id="hit-support",
            document_id="document-support",
            source_artifact=artifact("raw/support.json", "a"),
            normalizer=NORMALIZER,
            locator=PassageLocatorV1(
                kind=PassageLocatorKind.JSON_PATH,
                selector="$.abstract",
            ),
            text=support_text,
            char_count=len(support_text),
            estimated_token_count=9,
            normalized_text_sha256="c" * 64,
            matched_tag_ids=("compact-localized-state",),
            lexical_score=0.9,
        ),
        PassageV1(
            passage_id="passage-counter",
            hit_id="hit-counter",
            document_id="document-counter",
            source_artifact=artifact("raw/counter.json", "b"),
            normalizer=NORMALIZER,
            locator=PassageLocatorV1(
                kind=PassageLocatorKind.JSON_PATH,
                selector="$.abstract",
            ),
            text=counter_text,
            char_count=len(counter_text),
            estimated_token_count=8,
            normalized_text_sha256="d" * 64,
            matched_tag_ids=("path-imbalance",),
            lexical_score=0.8,
        ),
    )
    cards = (
        EvidenceCardV1(
            evidence_card_id="evidence-support",
            relation=EvidenceRelation.SUPPORT,
            claim_text="Destructive interference can confine a photonic mode.",
            mechanism_tag_ids=("compact-localized-state",),
            applicability_conditions=("coherent competing paths",),
            passage_ids=("passage-support",),
        ),
        EvidenceCardV1(
            evidence_card_id="evidence-counter",
            relation=EvidenceRelation.COUNTER,
            claim_text="Path imbalance breaks compact localization.",
            mechanism_tag_ids=("path-imbalance",),
            applicability_conditions=("strong path imbalance",),
            passage_ids=("passage-counter",),
        ),
    )
    packet = BridgePacketV1(
        bridge_packet_id="bridge-packet-1",
        bridge_rule_id=rule.bridge_rule_id,
        source_domain_tag_ids=rule.source_domain_tag_ids,
        target_tag_ids=rule.target_tag_ids,
        shared_invariant=rule.shared_invariant,
        transferable_control=rule.transferable_control,
        required_conditions=rule.required_conditions,
        breaking_conditions=rule.breaking_conditions,
        suggested_queries=tuple(query.text for query in queries),
        evidence_card_ids=tuple(card.evidence_card_id for card in cards),
    )
    return BridgeFixture(packet, graph, queries, hits, passages, cards)


def test_search_supported_bridge_accepts_a_closed_same_rule_evidence_chain() -> None:
    bridge_fixture().validate()


@pytest.mark.parametrize(
    ("field_name", "tampered_value"),
    [
        ("shared_invariant", "A changed invariant."),
        ("required_conditions", ("a changed condition",)),
        ("target_tag_ids", ("compact-localized-state",)),
    ],
)
def test_packet_cannot_tamper_with_curated_rule_fields(
    field_name: str,
    tampered_value: object,
) -> None:
    fixture = bridge_fixture()
    packet = fixture.packet.model_copy(update={field_name: tampered_value})

    with pytest.raises(InspirationIntegrityError) as raised:
        replace(fixture, packet=packet).validate()

    assert raised.value.code == "BRIDGE_RULE_FIELD_MISMATCH"


def test_packet_rule_must_exist_in_the_frozen_graph() -> None:
    fixture = bridge_fixture()
    packet = fixture.packet.model_copy(update={"bridge_rule_id": "missing-rule"})

    with pytest.raises(InspirationIntegrityError) as raised:
        replace(fixture, packet=packet).validate()

    assert raised.value.code == "BRIDGE_RULE_NOT_FOUND"


def test_source_tags_must_be_analogy_domains() -> None:
    fixture = bridge_fixture()
    source = fixture.graph.tags[0].model_copy(update={"kind": TagKind.MECHANISM})
    graph = fixture.graph.model_copy(
        update={"tags": (source, *fixture.graph.tags[1:])}
    )

    with pytest.raises(InspirationIntegrityError) as raised:
        replace(fixture, graph=graph).validate()

    assert raised.value.code == "SOURCE_TAG_KIND_INVALID"


@pytest.mark.parametrize("removed_tag", ["electronic-flat-band", "compact-localized-state"])
def test_rule_target_and_required_evidence_tags_must_exist(
    removed_tag: str,
) -> None:
    fixture = bridge_fixture()
    graph = fixture.graph.model_copy(
        update={
            "tags": tuple(tag for tag in fixture.graph.tags if tag.tag_id != removed_tag)
        }
    )

    with pytest.raises(InspirationIntegrityError) as raised:
        replace(fixture, graph=graph).validate()

    assert raised.value.code == "TAG_NOT_FOUND"


@pytest.mark.parametrize("query_index", [0, 1])
def test_reachable_bridge_and_counter_queries_must_reference_the_same_rule(
    query_index: int,
) -> None:
    fixture = bridge_fixture()
    queries = list(fixture.queries)
    queries[query_index] = queries[query_index].model_copy(
        update={"bridge_rule_id": "other-rule"}
    )

    with pytest.raises(InspirationIntegrityError) as raised:
        replace(fixture, queries=tuple(queries)).validate()

    assert raised.value.code == "SUPPORTING_QUERY_RULE_MISMATCH"


@pytest.mark.parametrize(
    ("mutation", "expected_code"),
    [
        (
            lambda fixture: replace(
                fixture,
                packet=fixture.packet.model_copy(
                    update={"evidence_card_ids": ("missing-card",)}
                ),
            ),
            "EVIDENCE_CARD_NOT_FOUND",
        ),
        (
            lambda fixture: replace(
                fixture,
                cards=(
                    fixture.cards[0].model_copy(
                        update={"passage_ids": ("missing-passage",)}
                    ),
                    fixture.cards[1],
                ),
            ),
            "PASSAGE_NOT_FOUND",
        ),
        (
            lambda fixture: replace(
                fixture,
                passages=(
                    fixture.passages[0].model_copy(update={"hit_id": "missing-hit"}),
                    fixture.passages[1],
                ),
            ),
            "SEARCH_HIT_NOT_FOUND",
        ),
        (
            lambda fixture: replace(
                fixture,
                hits=(
                    fixture.hits[0].model_copy(
                        update={"query_ids": ("missing-query",)}
                    ),
                    fixture.hits[1],
                ),
            ),
            "SEARCH_QUERY_NOT_FOUND",
        ),
    ],
)
def test_orphaned_reference_in_packet_closure_is_rejected(
    mutation,
    expected_code: str,
) -> None:
    tampered = mutation(bridge_fixture())

    with pytest.raises(InspirationIntegrityError) as raised:
        tampered.validate()

    assert raised.value.code == expected_code


def test_passage_and_hit_document_identity_must_match() -> None:
    fixture = bridge_fixture()
    passages = (
        fixture.passages[0].model_copy(update={"document_id": "tampered-document"}),
        fixture.passages[1],
    )

    with pytest.raises(InspirationIntegrityError) as raised:
        replace(fixture, passages=passages).validate()

    assert raised.value.code == "DOCUMENT_ID_MISMATCH"


def test_search_supported_packet_requires_support_evidence() -> None:
    fixture = bridge_fixture()
    cards = (
        fixture.cards[0].model_copy(update={"relation": EvidenceRelation.CONTEXT}),
        fixture.cards[1],
    )

    with pytest.raises(InspirationIntegrityError) as raised:
        replace(fixture, cards=cards).validate()

    assert raised.value.code == "SUPPORT_EVIDENCE_REQUIRED"


def test_support_evidence_must_cover_every_required_evidence_tag() -> None:
    fixture = bridge_fixture()
    cards = (
        fixture.cards[0].model_copy(
            update={"mechanism_tag_ids": ("path-imbalance",)}
        ),
        fixture.cards[1],
    )

    with pytest.raises(InspirationIntegrityError) as raised:
        replace(fixture, cards=cards).validate()

    assert raised.value.code == "REQUIRED_EVIDENCE_TAGS_MISSING"


def test_support_evidence_must_close_to_a_bridge_query() -> None:
    fixture = bridge_fixture()
    direct_query = fixture.queries[0].model_copy(
        update={"kind": SearchQueryKind.DIRECT, "bridge_rule_id": None}
    )

    with pytest.raises(InspirationIntegrityError) as raised:
        replace(fixture, queries=(direct_query, fixture.queries[1])).validate()

    assert raised.value.code == "SUPPORTING_BRIDGE_QUERY_REQUIRED"


def test_duplicate_object_ids_are_rejected_as_ambiguous() -> None:
    fixture = bridge_fixture()

    with pytest.raises(InspirationIntegrityError) as raised:
        replace(fixture, queries=(*fixture.queries, fixture.queries[0])).validate()

    assert raised.value.code == "DUPLICATE_OBJECT_ID"
