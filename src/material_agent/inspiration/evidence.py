"""Deterministic EvidenceCard construction from selected source passages."""

from __future__ import annotations

import re
from dataclasses import dataclass

from material_agent.inspiration.models import (
    EvidenceCardV1,
    EvidenceRelation,
    PassageV1,
    SearchHitV1,
    SearchQueryKind,
    SearchQueryV1,
    TagGraphV1,
    TagKind,
    deterministic_id,
)


class EvidenceBuildError(ValueError):
    """Input references are ambiguous or do not close over the frozen run."""


@dataclass(frozen=True)
class RuleEvidenceIndex:
    bridge_rule_id: str
    evidence_card_ids: tuple[str, ...]


@dataclass(frozen=True)
class EvidenceBuildResult:
    cards: tuple[EvidenceCardV1, ...]
    by_rule: tuple[RuleEvidenceIndex, ...]
    warnings: tuple[str, ...] = ()


_NEGATION_CUES = (
    "cannot",
    "can't",
    "did not",
    "didn't",
    "does not",
    "doesn't",
    "do not",
    "don't",
    "fails to",
    "failed to",
    "failure to",
    "is not",
    "isn't",
    "lack of",
    "lacks",
    "never",
    "no evidence",
    "not cause",
    "not produce*",
    "not support",
    "rules out",
    "unable to",
    "without evidence",
)
_UNCERTAINTY_CUES = (
    "could",
    "hypothes*",
    "may",
    "might",
    "not established",
    "not yet established",
    "possibly",
    "potentially",
    "remains unclear",
    "suggest",
    "uncertain",
    "unknown",
)
_COUNTER_CUES = (
    "breaks",
    "destroys",
    "disrupts",
    "eliminates",
    "invalidates",
    "path imbalance",
    "precludes",
    "prevents",
    "removes",
)
_SUPPORT_CUES = (
    "arise from",
    "arises from",
    "because",
    "cause",
    "confine",
    "demonstrat*",
    "due to",
    "emerge",
    "enable",
    "generate",
    "host",
    "hybridiz*",
    "induce",
    "lead to",
    "localiz*",
    "preserve",
    "produce*",
    "realize",
    "result from",
    "results from",
    "show",
    "suppress",
    "support",
    "trap",
    "yield",
)
_SENTENCE_BOUNDARY = re.compile(r"(?<=[.!?;])\s+")


def _contains_cue(text: str, cues: tuple[str, ...]) -> bool:
    """Match whole lexical cues; a trailing ``*`` permits a word suffix."""

    for cue in cues:
        prefix = cue.endswith("*")
        literal = cue[:-1] if prefix else cue
        suffix = r"[a-z]*" if prefix else ""
        pattern = rf"(?<![a-z0-9]){re.escape(literal)}{suffix}(?![a-z0-9])"
        if re.search(pattern, text) is not None:
            return True
    return False


def build_evidence_cards(
    *,
    graph: TagGraphV1,
    queries: tuple[SearchQueryV1, ...],
    hits: tuple[SearchHitV1, ...],
    passages: tuple[PassageV1, ...],
) -> EvidenceBuildResult:
    """Build source-scoped cards without asking an LLM to rewrite claims."""

    tags = {tag.tag_id: tag for tag in graph.tags}
    rules = {rule.bridge_rule_id: rule for rule in graph.bridge_rules}
    query_index = _unique_index(queries, "query_id", "query")
    hit_index = _unique_index(hits, "hit_id", "hit")
    _unique_index(passages, "passage_id", "passage")

    cards: list[EvidenceCardV1] = []
    cards_by_rule: dict[str, list[str]] = {rule_id: [] for rule_id in rules}
    warnings: list[str] = []
    for passage in sorted(passages, key=lambda item: item.passage_id):
        try:
            hit = hit_index[passage.hit_id]
        except KeyError as error:
            raise EvidenceBuildError(
                f"passage {passage.passage_id!r} references missing hit {passage.hit_id!r}"
            ) from error
        if passage.document_id != hit.document_id:
            raise EvidenceBuildError(
                f"passage {passage.passage_id!r} and hit {hit.hit_id!r} disagree on document"
            )
        hit_queries: list[SearchQueryV1] = []
        for query_id in hit.query_ids:
            try:
                hit_queries.append(query_index[query_id])
            except KeyError as error:
                raise EvidenceBuildError(
                    f"hit {hit.hit_id!r} references missing query {query_id!r}"
                ) from error
        rule_ids = {
            query.bridge_rule_id
            for query in hit_queries
            if query.bridge_rule_id is not None
        }
        if len(rule_ids) > 1:
            raise EvidenceBuildError(
                f"hit {hit.hit_id!r} mixes multiple bridge rules"
            )
        unknown_tags = sorted(set(passage.matched_tag_ids) - set(tags))
        if unknown_tags:
            raise EvidenceBuildError(
                f"passage {passage.passage_id!r} has unknown tags {unknown_tags!r}"
            )
        mechanism_tags = tuple(
            sorted(
                tag_id
                for tag_id in passage.matched_tag_ids
                if tags[tag_id].kind is TagKind.MECHANISM
            )
        )
        if not mechanism_tags:
            warnings.append(f"NO_MECHANISM_TAG:{passage.passage_id}")
            continue

        bridge_rule_id = next(iter(rule_ids), None)
        if bridge_rule_id is None:
            relation = EvidenceRelation.CONTEXT
            conditions = ("source context only; cross-domain transfer is not established",)
        else:
            try:
                rule = rules[bridge_rule_id]
            except KeyError as error:
                raise EvidenceBuildError(
                    f"query references missing bridge rule {bridge_rule_id!r}"
                ) from error
            kinds = {query.kind for query in hit_queries}
            if SearchQueryKind.BRIDGE in kinds and SearchQueryKind.COUNTER in kinds:
                raise EvidenceBuildError(
                    f"hit {hit.hit_id!r} mixes support and counter query kinds"
                )
            relation = classify_evidence_relation(
                passage.text,
                counter_query=SearchQueryKind.COUNTER in kinds,
            )
            if relation is EvidenceRelation.COUNTER:
                conditions = rule.breaking_conditions
            elif relation is EvidenceRelation.SUPPORT:
                conditions = rule.required_conditions
            else:
                conditions = (
                    ("source mentions the mechanism, but the deterministic relation "
                    "gate found no unambiguous support or counter assertion"),
                )
                warnings.append(f"RELATION_UNCERTAIN:{passage.passage_id}")

        payload = {
            "passage_id": passage.passage_id,
            "normalized_text_sha256": passage.normalized_text_sha256,
            "bridge_rule_id": bridge_rule_id,
            "relation": relation.value,
            "mechanism_tag_ids": mechanism_tags,
        }
        card = EvidenceCardV1(
            evidence_card_id=deterministic_id("evidence", payload),
            relation=relation,
            claim_text=passage.text[:4_000],
            mechanism_tag_ids=mechanism_tags,
            applicability_conditions=conditions,
            counterevidence=(passage.text[:512],)
            if relation is EvidenceRelation.COUNTER
            else (),
            passage_ids=(passage.passage_id,),
        )
        cards.append(card)
        if bridge_rule_id is not None:
            cards_by_rule[bridge_rule_id].append(card.evidence_card_id)

    return EvidenceBuildResult(
        cards=tuple(cards),
        by_rule=tuple(
            RuleEvidenceIndex(
                bridge_rule_id=rule_id,
                evidence_card_ids=tuple(sorted(card_ids)),
            )
            for rule_id, card_ids in sorted(cards_by_rule.items())
            if card_ids
        ),
        warnings=tuple(warnings),
    )


def classify_evidence_relation(
    text: str,
    *,
    counter_query: bool,
) -> EvidenceRelation:
    """Classify only explicit source assertions and fail closed on ambiguity.

    Search-query intent is not evidence semantics.  A bridge result therefore
    becomes ``SUPPORT`` only when its selected passage contains an affirmative
    relation cue without negation or epistemic hedging.  Counter queries likewise
    require an explicit breaking/negative assertion.  This intentionally
    conservative lexical gate is not a substitute for expert review or NLI; its
    purpose is to prevent keyword matches from being promoted automatically.
    """

    if not isinstance(text, str) or not text.strip():
        raise EvidenceBuildError("evidence relation text must be non-empty")
    if not isinstance(counter_query, bool):
        raise TypeError("counter_query must be a boolean")

    sentences = tuple(
        sentence.casefold().strip()
        for sentence in _SENTENCE_BOUNDARY.split(text)
        if sentence.strip()
    )
    explicit_support = False
    explicit_counter = False
    for sentence in sentences:
        uncertain = _contains_cue(sentence, _UNCERTAINTY_CUES)
        negated = _contains_cue(sentence, _NEGATION_CUES)
        counter = negated or _contains_cue(sentence, _COUNTER_CUES)
        support = _contains_cue(sentence, _SUPPORT_CUES)
        # A negated or hedged causal verb is never affirmative support.  Separate
        # sentences may legitimately state an observed mechanism and then its
        # breaking condition; retain the affirmative assertion while the bridge
        # packet continues to expose the curated breaking conditions.
        explicit_support = explicit_support or (support and not counter and not uncertain)
        explicit_counter = explicit_counter or (counter and not uncertain)

    if counter_query:
        return (
            EvidenceRelation.COUNTER
            if explicit_counter
            else EvidenceRelation.CONTEXT
        )
    if explicit_support:
        return EvidenceRelation.SUPPORT
    if explicit_counter:
        return EvidenceRelation.COUNTER
    return EvidenceRelation.CONTEXT


def _unique_index(items, id_attribute: str, label: str):
    result = {}
    for item in items:
        item_id = getattr(item, id_attribute)
        if item_id in result:
            raise EvidenceBuildError(f"duplicate {label} ID {item_id!r}")
        result[item_id] = item
    return result
