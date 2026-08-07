"""Promote evidence-closed curated rules into SEARCH_SUPPORTED packets."""

from __future__ import annotations

from dataclasses import dataclass

from material_agent.inspiration.models import (
    BridgePacketV1,
    EvidenceCardV1,
    EvidenceRelation,
    PassageV1,
    SearchHitV1,
    SearchQueryKind,
    SearchQueryV1,
    TagGraphV1,
    deterministic_id,
)
from material_agent.inspiration.validation import validate_search_supported_bridge


@dataclass(frozen=True)
class SkippedBridge:
    bridge_rule_id: str
    reason: str


@dataclass(frozen=True)
class BridgeBuildResult:
    packets: tuple[BridgePacketV1, ...]
    skipped: tuple[SkippedBridge, ...]


def build_search_supported_bridges(
    *,
    graph: TagGraphV1,
    queries: tuple[SearchQueryV1, ...],
    hits: tuple[SearchHitV1, ...],
    passages: tuple[PassageV1, ...],
    evidence_cards: tuple[EvidenceCardV1, ...],
) -> BridgeBuildResult:
    """Build only rules whose SUPPORT cards cover every required mechanism tag."""

    hits_by_id = _index(hits, "hit_id")
    queries_by_id = _index(queries, "query_id")
    cards_by_id = _index(evidence_cards, "evidence_card_id")
    passages_by_id = _index(passages, "passage_id")
    rule_to_card_ids: dict[str, set[str]] = {
        rule.bridge_rule_id: set() for rule in graph.bridge_rules
    }
    rule_to_query_texts: dict[str, set[str]] = {
        rule.bridge_rule_id: set() for rule in graph.bridge_rules
    }

    for query in queries:
        if query.bridge_rule_id is not None:
            if query.bridge_rule_id not in rule_to_query_texts:
                raise ValueError(f"query references unknown bridge rule {query.bridge_rule_id!r}")
            rule_to_query_texts[query.bridge_rule_id].add(query.text)

    for card in evidence_cards:
        card_rule_ids: set[str] = set()
        for passage_id in card.passage_ids:
            passage = passages_by_id.get(passage_id)
            if passage is None:
                raise ValueError(f"card references missing passage {passage_id!r}")
            hit = hits_by_id.get(passage.hit_id)
            if hit is None:
                raise ValueError(f"passage references missing hit {passage.hit_id!r}")
            for query_id in hit.query_ids:
                query = queries_by_id.get(query_id)
                if query is None:
                    raise ValueError(f"hit references missing query {query_id!r}")
                if query.kind in {SearchQueryKind.BRIDGE, SearchQueryKind.COUNTER}:
                    if query.bridge_rule_id is None:
                        raise ValueError("bridge/counter query has no bridge rule")
                    card_rule_ids.add(query.bridge_rule_id)
        if len(card_rule_ids) > 1:
            raise ValueError(f"evidence card {card.evidence_card_id!r} spans bridge rules")
        if card_rule_ids:
            rule_id = next(iter(card_rule_ids))
            if rule_id not in rule_to_card_ids:
                raise ValueError(f"evidence card reaches unknown bridge rule {rule_id!r}")
            rule_to_card_ids[rule_id].add(card.evidence_card_id)

    packets: list[BridgePacketV1] = []
    skipped: list[SkippedBridge] = []
    for rule in sorted(graph.bridge_rules, key=lambda item: item.bridge_rule_id):
        card_ids = tuple(sorted(rule_to_card_ids[rule.bridge_rule_id]))
        query_texts = tuple(sorted(rule_to_query_texts[rule.bridge_rule_id]))
        support_tags = {
            tag_id
            for card_id in card_ids
            if cards_by_id[card_id].relation is EvidenceRelation.SUPPORT
            for tag_id in cards_by_id[card_id].mechanism_tag_ids
        }
        missing = sorted(set(rule.required_evidence_tag_ids) - support_tags)
        if missing:
            skipped.append(
                SkippedBridge(
                    bridge_rule_id=rule.bridge_rule_id,
                    reason=f"required SUPPORT tags missing: {missing!r}",
                )
            )
            continue
        if not query_texts:
            skipped.append(
                SkippedBridge(
                    bridge_rule_id=rule.bridge_rule_id,
                    reason="no executed bridge/counter query",
                )
            )
            continue
        packet_payload = {
            "bridge_rule_id": rule.bridge_rule_id,
            "evidence_card_ids": card_ids,
            "suggested_queries": query_texts,
        }
        packet = BridgePacketV1(
            bridge_packet_id=deterministic_id("bridge", packet_payload),
            bridge_rule_id=rule.bridge_rule_id,
            source_domain_tag_ids=rule.source_domain_tag_ids,
            target_tag_ids=rule.target_tag_ids,
            shared_invariant=rule.shared_invariant,
            transferable_control=rule.transferable_control,
            required_conditions=rule.required_conditions,
            breaking_conditions=rule.breaking_conditions,
            suggested_queries=query_texts,
            evidence_card_ids=card_ids,
        )
        validate_search_supported_bridge(
            packet,
            tag_graph=graph,
            queries=queries,
            hits=hits,
            passages=passages,
            evidence_cards=evidence_cards,
        )
        packets.append(packet)
    return BridgeBuildResult(packets=tuple(packets), skipped=tuple(skipped))


def _index(items, id_attribute: str):
    result = {}
    for item in items:
        item_id = getattr(item, id_attribute)
        if item_id in result:
            raise ValueError(f"duplicate ID {item_id!r}")
        result[item_id] = item
    return result
