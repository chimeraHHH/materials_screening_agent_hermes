"""Cross-object, fail-closed validation for inspiration data products.

Pydantic models validate the shape of one object at a time.  The helpers in
this module validate the reference closure that turns a bridge packet into an
auditable, search-supported result.  They are pure: no artifacts are read and
no input object is mutated.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import TypeVar

from material_agent.inspiration.models import (
    BridgePacketV1,
    BridgeRuleV1,
    EvidenceCardV1,
    EvidenceRelation,
    PassageV1,
    SearchHitV1,
    SearchQueryKind,
    SearchQueryV1,
    TagDefinitionV1,
    TagGraphV1,
    TagKind,
)


class InspirationIntegrityError(ValueError):
    """A stable, machine-readable inspiration reference-integrity failure."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(f"{code}: {message}")


_ItemT = TypeVar("_ItemT")


def _index_by_id(
    items: Iterable[_ItemT],
    *,
    id_attribute: str,
    object_label: str,
) -> dict[str, _ItemT]:
    index: dict[str, _ItemT] = {}
    for item in items:
        item_id = getattr(item, id_attribute)
        if item_id in index:
            raise InspirationIntegrityError(
                "DUPLICATE_OBJECT_ID",
                f"duplicate {object_label} ID {item_id!r}",
            )
        index[item_id] = item
    return index


def _require_known_tags(
    tag_ids: Iterable[str],
    *,
    tags_by_id: dict[str, TagDefinitionV1],
    reference_label: str,
) -> None:
    for tag_id in tag_ids:
        if tag_id not in tags_by_id:
            raise InspirationIntegrityError(
                "TAG_NOT_FOUND",
                f"{reference_label} references unknown tag {tag_id!r}",
            )


def _require_by_id(
    index: dict[str, _ItemT],
    item_id: str,
    *,
    code: str,
    object_label: str,
    owner_label: str,
) -> _ItemT:
    try:
        return index[item_id]
    except KeyError as error:
        raise InspirationIntegrityError(
            code,
            f"{owner_label} references missing {object_label} {item_id!r}",
        ) from error


def validate_search_supported_bridge(
    packet: BridgePacketV1,
    *,
    tag_graph: TagGraphV1,
    queries: Iterable[SearchQueryV1],
    hits: Iterable[SearchHitV1],
    passages: Iterable[PassageV1],
    evidence_cards: Iterable[EvidenceCardV1],
) -> None:
    """Validate the complete evidence closure for one ``SEARCH_SUPPORTED`` bridge.

    A packet is accepted only when its curated rule exists, its copied rule
    semantics have not changed, and its evidence closes through passages and
    hits to at least one same-rule bridge query.  Counter-query evidence may be
    included, but every reachable BRIDGE or COUNTER query must reference the
    packet's rule.

    Raises:
        InspirationIntegrityError: if any reference or cross-object invariant
            is missing, ambiguous, or inconsistent.
    """

    tags_by_id = _index_by_id(
        tag_graph.tags,
        id_attribute="tag_id",
        object_label="tag",
    )
    rules_by_id = _index_by_id(
        tag_graph.bridge_rules,
        id_attribute="bridge_rule_id",
        object_label="bridge rule",
    )
    queries_by_id = _index_by_id(
        queries,
        id_attribute="query_id",
        object_label="search query",
    )
    hits_by_id = _index_by_id(
        hits,
        id_attribute="hit_id",
        object_label="search hit",
    )
    passages_by_id = _index_by_id(
        passages,
        id_attribute="passage_id",
        object_label="passage",
    )
    cards_by_id = _index_by_id(
        evidence_cards,
        id_attribute="evidence_card_id",
        object_label="evidence card",
    )

    rule = _require_by_id(
        rules_by_id,
        packet.bridge_rule_id,
        code="BRIDGE_RULE_NOT_FOUND",
        object_label="bridge rule",
        owner_label=f"bridge packet {packet.bridge_packet_id!r}",
    )

    _validate_packet_matches_rule(packet, rule)
    _validate_rule_tags(rule, tags_by_id=tags_by_id)

    support_cards: list[EvidenceCardV1] = []
    support_bridge_query_ids: set[str] = set()

    for evidence_card_id in packet.evidence_card_ids:
        card = _require_by_id(
            cards_by_id,
            evidence_card_id,
            code="EVIDENCE_CARD_NOT_FOUND",
            object_label="evidence card",
            owner_label=f"bridge packet {packet.bridge_packet_id!r}",
        )
        _require_known_tags(
            card.mechanism_tag_ids,
            tags_by_id=tags_by_id,
            reference_label=f"evidence card {card.evidence_card_id!r}",
        )
        for tag_id in card.mechanism_tag_ids:
            if tags_by_id[tag_id].kind is not TagKind.MECHANISM:
                raise InspirationIntegrityError(
                    "MECHANISM_TAG_KIND_INVALID",
                    f"evidence card mechanism tag {tag_id!r} must have kind MECHANISM",
                )
        if card.relation is EvidenceRelation.SUPPORT:
            support_cards.append(card)

        for passage_id in card.passage_ids:
            passage = _require_by_id(
                passages_by_id,
                passage_id,
                code="PASSAGE_NOT_FOUND",
                object_label="passage",
                owner_label=f"evidence card {card.evidence_card_id!r}",
            )
            _require_known_tags(
                passage.matched_tag_ids,
                tags_by_id=tags_by_id,
                reference_label=f"passage {passage.passage_id!r}",
            )
            hit = _require_by_id(
                hits_by_id,
                passage.hit_id,
                code="SEARCH_HIT_NOT_FOUND",
                object_label="search hit",
                owner_label=f"passage {passage.passage_id!r}",
            )
            if passage.document_id != hit.document_id:
                raise InspirationIntegrityError(
                    "DOCUMENT_ID_MISMATCH",
                    f"passage {passage.passage_id!r} names document "
                    f"{passage.document_id!r}, but hit {hit.hit_id!r} names "
                    f"{hit.document_id!r}",
                )

            for query_id in hit.query_ids:
                query = _require_by_id(
                    queries_by_id,
                    query_id,
                    code="SEARCH_QUERY_NOT_FOUND",
                    object_label="search query",
                    owner_label=f"search hit {hit.hit_id!r}",
                )
                _require_known_tags(
                    query.tag_ids,
                    tags_by_id=tags_by_id,
                    reference_label=f"search query {query.query_id!r}",
                )
                if query.kind in {
                    SearchQueryKind.BRIDGE,
                    SearchQueryKind.COUNTER,
                } and query.bridge_rule_id != rule.bridge_rule_id:
                    raise InspirationIntegrityError(
                        "SUPPORTING_QUERY_RULE_MISMATCH",
                        f"search query {query.query_id!r} references rule "
                        f"{query.bridge_rule_id!r}, expected {rule.bridge_rule_id!r}",
                    )
                if (
                    card.relation is EvidenceRelation.SUPPORT
                    and query.kind is SearchQueryKind.BRIDGE
                ):
                    support_bridge_query_ids.add(query.query_id)

    if not support_cards:
        raise InspirationIntegrityError(
            "SUPPORT_EVIDENCE_REQUIRED",
            f"bridge packet {packet.bridge_packet_id!r} has no SUPPORT evidence card",
        )

    covered_mechanism_tags = {
        tag_id for card in support_cards for tag_id in card.mechanism_tag_ids
    }
    missing_required_tags = sorted(
        set(rule.required_evidence_tag_ids) - covered_mechanism_tags
    )
    if missing_required_tags:
        raise InspirationIntegrityError(
            "REQUIRED_EVIDENCE_TAGS_MISSING",
            f"SUPPORT evidence does not cover required tags {missing_required_tags!r}",
        )

    if not support_bridge_query_ids:
        raise InspirationIntegrityError(
            "SUPPORTING_BRIDGE_QUERY_REQUIRED",
            f"bridge packet {packet.bridge_packet_id!r} has no SUPPORT evidence "
            "reachable from a BRIDGE query",
        )


def _validate_packet_matches_rule(
    packet: BridgePacketV1,
    rule: BridgeRuleV1,
) -> None:
    for field_name in (
        "source_domain_tag_ids",
        "target_tag_ids",
        "shared_invariant",
        "transferable_control",
        "required_conditions",
        "breaking_conditions",
    ):
        if getattr(packet, field_name) != getattr(rule, field_name):
            raise InspirationIntegrityError(
                "BRIDGE_RULE_FIELD_MISMATCH",
                f"bridge packet field {field_name!r} does not match rule "
                f"{rule.bridge_rule_id!r}",
            )


def _validate_rule_tags(
    rule: BridgeRuleV1,
    *,
    tags_by_id: dict[str, TagDefinitionV1],
) -> None:
    for reference_label, tag_ids in (
        ("bridge rule source domains", rule.source_domain_tag_ids),
        ("bridge rule targets", rule.target_tag_ids),
        ("bridge rule required evidence", rule.required_evidence_tag_ids),
        ("bridge rule suggested query tags", rule.suggested_query_tag_ids),
    ):
        _require_known_tags(
            tag_ids,
            tags_by_id=tags_by_id,
            reference_label=reference_label,
        )

    for tag_id in rule.source_domain_tag_ids:
        if tags_by_id[tag_id].kind is not TagKind.ANALOGY_DOMAIN:
            raise InspirationIntegrityError(
                "SOURCE_TAG_KIND_INVALID",
                f"bridge rule source tag {tag_id!r} must have kind ANALOGY_DOMAIN",
            )
    for tag_id in rule.required_evidence_tag_ids:
        if tags_by_id[tag_id].kind is not TagKind.MECHANISM:
            raise InspirationIntegrityError(
                "REQUIRED_EVIDENCE_TAG_KIND_INVALID",
                f"bridge rule required evidence tag {tag_id!r} must have kind MECHANISM",
            )
