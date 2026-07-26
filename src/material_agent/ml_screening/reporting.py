"""Human-readable Agent02 report rendering."""

from __future__ import annotations

from collections import Counter

from material_agent.ml_screening.models import MLCandidateManifestRecord, MLStagePlan


def render_fake_report(
    plan: MLStagePlan,
    records: list[MLCandidateManifestRecord],
) -> str:
    decisions = Counter(record.candidate.decision.value for record in records)
    selections = Counter(
        record.candidate.selection_status.value for record in records
    )
    lines = [
        "# Agent02 Fake Screening Report",
        "",
        "> **TEST FIXTURE / MOCK:** this report contains no real ML inference, "
        "cannot produce L2 evidence, and must never be presented as a "
        "scientific result.",
        "",
        f"- Run: `{plan.run_id}`",
        f"- Contract: `{plan.schema_version}`",
        f"- Model reference: `{plan.model_id}` (fake adapter)",
        f"- Planned inference candidates: {len(plan.inference_candidate_ids)}",
        f"- Approval required: `{str(plan.approval_required).lower()}`",
        "",
        "## Decision counts",
        "",
    ]
    lines.extend(
        f"- `{name}`: {count}" for name, count in sorted(decisions.items())
    )
    lines.extend(["", "## Selection counts", ""])
    lines.extend(
        f"- `{name}`: {count}" for name, count in sorted(selections.items())
    )
    lines.extend(
        [
            "",
            "## Candidate summary",
            "",
            "| Candidate | Selection | Decision | Evidence | Recommended structure |",
            "|---|---|---|---|---|",
        ]
    )
    for record in records:
        candidate = record.candidate
        lines.append(
            "| "
            f"`{candidate.candidate_id}` | "
            f"`{candidate.selection_status}` | "
            f"`{candidate.decision}` | "
            f"`{candidate.evidence_level}` | "
            f"`{candidate.recommended_downstream_structure_id}` |"
        )
    lines.extend(
        [
            "",
            "No value in this report is a formation energy, energy above hull, "
            "thermodynamic-stability proof, magnetic-ground-state result, or "
            "DFT validation.",
            "",
        ]
    )
    return "\n".join(lines)
