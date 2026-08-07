"""Deterministic, non-scientific reports for inspiration companion runs."""

from __future__ import annotations

from collections.abc import Sequence

from material_agent.inspiration.models import (
    BridgePacketV1,
    EvidenceCardV1,
    InspirationBundleV1,
    InspirationInputV1,
    PassageV1,
    SearchHitV1,
    SearchQueryV1,
    TransformationPlanV1,
)


PROPERTY_STATUS_UNKNOWN = "UNKNOWN"


def render_inspiration_report(
    *,
    inspiration_input: InspirationInputV1,
    bundle: InspirationBundleV1,
    queries: Sequence[SearchQueryV1],
    hits: Sequence[SearchHitV1],
    passages: Sequence[PassageV1],
    evidence_cards: Sequence[EvidenceCardV1],
    bridge_packets: Sequence[BridgePacketV1],
    transformation_plans: Sequence[TransformationPlanV1],
    review_items: Sequence[str],
    warnings: Sequence[str],
) -> str:
    """Render a stable Markdown summary without upgrading evidence claims."""

    lines = [
        "# Inspiration run report",
        "",
        f"- Project: `{inspiration_input.project_id}`",
        f"- Request: `{inspiration_input.request_id}`",
        f"- Run: `{inspiration_input.run_id}`",
        f"- Outcome: `{bundle.outcome.value}`",
        f"- Scientific conclusion: `{str(bundle.scientific_conclusion).lower()}`",
        f"- Target property status: `{PROPERTY_STATUS_UNKNOWN}`",
        "",
        "The target property was not computed in this stage. Every structure is a "
        "proposal that requires downstream validation.",
        "",
        "## Bounded evidence and execution",
        "",
        f"- Executed metadata queries: `{len(queries)}`",
        f"- Metadata hits: `{len(hits)}`",
        f"- Selected passages: `{len(passages)}`",
        f"- Evidence cards: `{len(evidence_cards)}`",
        f"- Search-supported bridge packets: `{len(bridge_packets)}`",
        f"- Transformation records: `{len(transformation_plans)}`",
        f"- Selected candidates: `{len(bundle.selected_candidates)}`",
        "- PDF full-text reads: `0`",
        "- LLM calls: `0`",
        "",
        "Only bounded metadata passages were vectorized. Source text remains "
        "untrusted data and was not used as an instruction surface.",
        "",
        "## Candidate hypotheses",
        "",
    ]
    if bundle.selected_candidates:
        for candidate in bundle.selected_candidates:
            mechanisms = ", ".join(candidate.mechanism_tag_ids)
            lines.extend(
                (
                    f"### {candidate.selection_rank}. `{candidate.candidate_id}`",
                    "",
                    f"- Canonical structure: `{candidate.canonical_structure_id}`",
                    f"- Mechanism tags: `{mechanisms}`",
                    f"- Property status: `{PROPERTY_STATUS_UNKNOWN}`",
                    f"- Scientific conclusion: "
                    f"`{str(candidate.scientific_conclusion).lower()}`",
                    f"- Next falsification step: {candidate.next_falsification_step}",
                    "",
                )
            )
    else:
        lines.extend(("No candidate passed the structure-selection gate.", ""))

    lines.extend(("## Review items", ""))
    if review_items:
        lines.extend(f"- {item}" for item in review_items)
    else:
        lines.append("- No manual structure review item was raised.")

    lines.extend(("", "## Limitations", ""))
    lines.extend(f"- {limitation}" for limitation in bundle.limitations)
    if warnings:
        lines.extend(("", "## Warnings", ""))
        lines.extend(f"- `{warning}`" for warning in warnings)
    lines.append("")
    return "\n".join(lines)
