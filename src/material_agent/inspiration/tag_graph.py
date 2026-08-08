"""Curated cross-domain tags and deterministic query planning.

The graph is a reviewable registry, not an online-learned ontology.  Feedback
may be recorded beside it, but a model cannot mutate this graph at runtime.
"""

from __future__ import annotations

import string
from dataclasses import dataclass

from material_agent.inspiration.models import (
    BridgeRuleV1,
    SearchQueryKind,
    SearchQueryV1,
    TagDefinitionV1,
    TagEdgeV1,
    TagGraphV1,
    TagKind,
    TagRelation,
    deterministic_id,
)
from material_agent.inspiration.policy import SearchBudgetV1


CURATED_FLAT_BAND_GRAPH_ID = "flat-band-cross-domain-v1"
_ALLOWED_TEMPLATE_FIELDS = {"source", "target", "evidence"}


class QueryPlanningError(ValueError):
    """The frozen tag graph cannot produce a bounded, deterministic query plan."""


@dataclass(frozen=True)
class QueryPlan:
    queries: tuple[SearchQueryV1, ...]
    skipped_rule_ids: tuple[str, ...] = ()


def curated_flat_band_tag_graph() -> TagGraphV1:
    """Return the first reviewed graph for flat/narrow-band inspiration."""

    tags = (
        _tag(
            "electronic-flat-band",
            TagKind.PROPERTY,
            "Electronic flat band",
            "A low-dispersion electronic band; this tag is a search target, not a computed result.",
            "electronic flat band",
            "narrow electronic band",
        ),
        _tag(
            "layered-transition-metal-compound",
            TagKind.MATERIAL,
            "Layered transition-metal compound",
            "Layered compounds containing transition-metal sites.",
            "layered transition metal compound",
            "two-dimensional transition metal material",
        ),
        _tag(
            "compact-localized-state",
            TagKind.MECHANISM,
            "Compact localized state",
            "A mode confined to a finite motif through a local amplitude constraint.",
            "compact localized state",
        ),
        _tag(
            "destructive-interference",
            TagKind.MECHANISM,
            "Destructive interference",
            "Cancellation between propagation paths that suppresses dispersion.",
            "destructive interference flat band",
        ),
        _tag(
            "local-resonance",
            TagKind.MECHANISM,
            "Local resonance",
            "A weakly dispersive local mode hybridized with an extended network.",
            "local resonance flat band",
        ),
        _tag(
            "line-graph-localization",
            TagKind.MECHANISM,
            "Line-graph localization",
            "Connectivity-induced localization associated with line-graph-like networks.",
            "line graph flat band localization",
        ),
        _tag(
            "kagome-connectivity",
            TagKind.MOTIF,
            "Kagome connectivity",
            "Corner-sharing triangular connectivity used as a structural search cue.",
            "kagome connectivity",
        ),
        _tag(
            "photonic-lattice",
            TagKind.ANALOGY_DOMAIN,
            "Photonic lattice",
            "Classical-wave lattices where interference constraints can be directly visualized.",
            "photonic lattice",
        ),
        _tag(
            "acoustic-metamaterial",
            TagKind.ANALOGY_DOMAIN,
            "Acoustic metamaterial",
            "Mass-spring or acoustic networks with controlled local resonances.",
            "acoustic metamaterial",
        ),
        _tag(
            "frustrated-magnetism",
            TagKind.ANALOGY_DOMAIN,
            "Frustrated magnetism",
            "Spin or magnon networks whose connectivity can host localized modes.",
            "frustrated magnetism flat magnon band",
        ),
        _tag(
            "equivalent-site-substitution",
            TagKind.PROCESS,
            "Equivalent-site substitution",
            "A deterministic substitution on a complete equivalence class of ordered sites.",
            "equivalent site substitution",
        ),
        _tag(
            "band-dispersion-signature",
            TagKind.MEASUREMENT,
            "Band-dispersion signature",
            "A computed or measured dispersion signature used only in downstream validation.",
            "band dispersion bandwidth",
        ),
    )
    rules = (
        BridgeRuleV1(
            bridge_rule_id="photonic-interference-to-electronic-flat-band",
            rule_version="1",
            source_domain_tag_ids=("photonic-lattice",),
            target_tag_ids=("electronic-flat-band",),
            required_evidence_tag_ids=(
                "compact-localized-state",
                "destructive-interference",
            ),
            suggested_query_tag_ids=(
                "compact-localized-state",
                "destructive-interference",
                "photonic-lattice",
            ),
            query_templates=("{source} {evidence} connectivity",),
            shared_invariant=(
                "A local amplitude-cancellation constraint confines a mode and suppresses its dispersion."
            ),
            transferable_control=(
                "Preserve the relevant connectivity while perturbing symmetry-equivalent local sites."
            ),
            required_conditions=(
                "coherent competing propagation paths",
                "connectivity that supports a compact localized state",
            ),
            breaking_conditions=(
                "path imbalance or disorder that removes amplitude cancellation",
            ),
        ),
        BridgeRuleV1(
            bridge_rule_id="acoustic-resonance-to-electronic-flat-band",
            rule_version="1",
            source_domain_tag_ids=("acoustic-metamaterial",),
            target_tag_ids=("electronic-flat-band",),
            required_evidence_tag_ids=("local-resonance",),
            suggested_query_tag_ids=("acoustic-metamaterial", "local-resonance"),
            query_templates=("{source} {evidence} weak dispersion",),
            shared_invariant=(
                "A local mode weakly coupled to the extended network remains weakly dispersive."
            ),
            transferable_control=(
                "Tune an equivalent local site while retaining the host connectivity and local-mode separation."
            ),
            required_conditions=(
                "a spectrally identifiable local mode",
                "coupling weaker than the local-mode energy separation",
            ),
            breaking_conditions=(
                "strong hybridization that delocalizes the local mode",
            ),
        ),
        BridgeRuleV1(
            bridge_rule_id="magnon-line-graph-to-electronic-flat-band",
            rule_version="1",
            source_domain_tag_ids=("frustrated-magnetism",),
            target_tag_ids=("electronic-flat-band",),
            required_evidence_tag_ids=("line-graph-localization",),
            suggested_query_tag_ids=(
                "frustrated-magnetism",
                "kagome-connectivity",
                "line-graph-localization",
            ),
            query_templates=("{source} {evidence} {target}",),
            shared_invariant=(
                "Network connectivity admits a localized eigenmode whose destructive overlap cancels hopping."
            ),
            transferable_control=(
                "Retain line-graph-like connectivity while varying symmetry-equivalent site chemistry."
            ),
            required_conditions=(
                "connectivity-equivalent hopping paths",
                "a motif supporting a localized eigenvector",
            ),
            breaking_conditions=(
                "connectivity changes that remove the localized eigenvector",
            ),
        ),
    )
    edges = (
        TagEdgeV1(
            source_tag_id="compact-localized-state",
            target_tag_id="electronic-flat-band",
            relation=TagRelation.MECHANISM_SUPPORTS_PROPERTY,
        ),
        TagEdgeV1(
            source_tag_id="destructive-interference",
            target_tag_id="electronic-flat-band",
            relation=TagRelation.MECHANISM_SUPPORTS_PROPERTY,
        ),
        TagEdgeV1(
            source_tag_id="photonic-lattice",
            target_tag_id="destructive-interference",
            relation=TagRelation.DOMAIN_ANALOGY,
        ),
        TagEdgeV1(
            source_tag_id="acoustic-metamaterial",
            target_tag_id="local-resonance",
            relation=TagRelation.DOMAIN_ANALOGY,
        ),
        TagEdgeV1(
            source_tag_id="frustrated-magnetism",
            target_tag_id="line-graph-localization",
            relation=TagRelation.DOMAIN_ANALOGY,
        ),
        TagEdgeV1(
            source_tag_id="band-dispersion-signature",
            target_tag_id="electronic-flat-band",
            relation=TagRelation.MEASUREMENT_OBSERVES_PROPERTY,
        ),
    )
    return TagGraphV1(
        graph_id=CURATED_FLAT_BAND_GRAPH_ID,
        graph_version="2026-08-08.1",
        tags=tags,
        edges=edges,
        bridge_rules=rules,
    )


def plan_tag_queries(
    graph: TagGraphV1,
    *,
    target_tag_ids: tuple[str, ...],
    budget: SearchBudgetV1,
) -> QueryPlan:
    """Plan direct, bridge, and counter queries within explicit class budgets."""

    tags = {tag.tag_id: tag for tag in graph.tags}
    unknown_targets = sorted(set(target_tag_ids) - set(tags))
    if unknown_targets:
        raise QueryPlanningError(f"unknown target tags: {unknown_targets!r}")
    if len(set(target_tag_ids)) != len(target_tag_ids) or not target_tag_ids:
        raise QueryPlanningError("target tag IDs must be non-empty and unique")

    queries: list[SearchQueryV1] = []
    seen_payloads: set[tuple[str, str, str | None]] = set()

    direct_terms = [tags[tag_id].query_terms[0] for tag_id in target_tag_ids]
    if budget.max_direct_queries:
        _append_query(
            queries,
            seen_payloads=seen_payloads,
            kind=SearchQueryKind.DIRECT,
            text=" ".join(direct_terms),
            tag_ids=tuple(sorted(target_tag_ids)),
            bridge_rule_id=None,
        )

    matching_rules = sorted(
        (
            rule
            for rule in graph.bridge_rules
            if set(rule.target_tag_ids) & set(target_tag_ids)
        ),
        key=lambda rule: rule.bridge_rule_id,
    )
    skipped: list[str] = []
    bridge_count = 0
    counter_count = 0
    for rule in matching_rules:
        if bridge_count >= budget.max_bridge_queries:
            skipped.append(rule.bridge_rule_id)
            continue
        rendered = _render_rule_query(rule, tags)
        _append_query(
            queries,
            seen_payloads=seen_payloads,
            kind=SearchQueryKind.BRIDGE,
            text=rendered,
            tag_ids=tuple(sorted(rule.suggested_query_tag_ids)),
            bridge_rule_id=rule.bridge_rule_id,
        )
        bridge_count += 1
        if counter_count < budget.max_counter_queries:
            counter_text = f"{rendered} failure {rule.breaking_conditions[0]}"
            _append_query(
                queries,
                seen_payloads=seen_payloads,
                kind=SearchQueryKind.COUNTER,
                text=counter_text[:512],
                tag_ids=tuple(sorted(rule.suggested_query_tag_ids)),
                bridge_rule_id=rule.bridge_rule_id,
            )
            counter_count += 1

    if len(queries) > budget.max_queries:
        queries = queries[: budget.max_queries]
    if not queries:
        raise QueryPlanningError("query budget allocates no executable query class")
    return QueryPlan(queries=tuple(queries), skipped_rule_ids=tuple(skipped))


def _tag(
    tag_id: str,
    kind: TagKind,
    label: str,
    description: str,
    *query_terms: str,
) -> TagDefinitionV1:
    return TagDefinitionV1(
        tag_id=tag_id,
        kind=kind,
        label=label,
        description=description,
        query_terms=tuple(query_terms),
    )


def _render_rule_query(
    rule: BridgeRuleV1,
    tags: dict[str, TagDefinitionV1],
) -> str:
    template = rule.query_templates[0]
    fields = {
        field_name
        for _, field_name, _, _ in string.Formatter().parse(template)
        if field_name is not None
    }
    unknown_fields = sorted(fields - _ALLOWED_TEMPLATE_FIELDS)
    if unknown_fields:
        raise QueryPlanningError(
            f"bridge rule {rule.bridge_rule_id!r} uses unknown placeholders "
            f"{unknown_fields!r}"
        )
    values = {
        "source": " ".join(tags[tag_id].query_terms[0] for tag_id in rule.source_domain_tag_ids),
        "target": " ".join(tags[tag_id].query_terms[0] for tag_id in rule.target_tag_ids),
        "evidence": " ".join(
            tags[tag_id].query_terms[0] for tag_id in rule.required_evidence_tag_ids
        ),
    }
    rendered = " ".join(template.format_map(values).split())
    if len(rendered) < 3 or len(rendered) > 512:
        raise QueryPlanningError(
            f"bridge rule {rule.bridge_rule_id!r} produced an invalid query length"
        )
    return rendered


def _append_query(
    queries: list[SearchQueryV1],
    *,
    seen_payloads: set[tuple[str, str, str | None]],
    kind: SearchQueryKind,
    text: str,
    tag_ids: tuple[str, ...],
    bridge_rule_id: str | None,
) -> None:
    key = (kind.value, text.casefold(), bridge_rule_id)
    if key in seen_payloads:
        return
    seen_payloads.add(key)
    payload = {
        "kind": kind.value,
        "text": text,
        "tag_ids": tag_ids,
        "bridge_rule_id": bridge_rule_id,
    }
    queries.append(
        SearchQueryV1(
            query_id=deterministic_id("query", payload),
            kind=kind,
            text=text,
            tag_ids=tag_ids,
            bridge_rule_id=bridge_rule_id,
        )
    )
