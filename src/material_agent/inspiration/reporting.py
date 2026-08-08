"""Deterministic, non-scientific reports for inspiration companion runs."""

from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping, Sequence
from enum import Enum

from material_agent.inspiration.models import (
    ArtifactPointerV1,
    BridgePacketV1,
    EvidenceCardV1,
    InspirationBundleV1,
    InspirationInputV1,
    PassageV1,
    SearchHitV1,
    SearchQueryV1,
    TransformationPlanV1,
)
from material_agent.inspiration.search import SearchAttemptRecord


PROPERTY_STATUS_UNKNOWN = "UNKNOWN"
_NOT_RECORDED = "not recorded"
_NONE = "none"


def _value(value: object) -> str:
    if isinstance(value, Enum):
        return str(value.value)
    if isinstance(value, float):
        return format(value, ".12g")
    return str(value)


def _code(value: object | None, *, empty: str = _NOT_RECORDED) -> str:
    """Render untrusted scalar text as a safe, whitespace-normalized code span."""

    if value is None:
        text = empty
    else:
        text = " ".join(_value(value).split()) or empty
    longest_run = max((len(run) for run in re.findall(r"`+", text)), default=0)
    delimiter = "`" * max(1, longest_run + 1)
    padding = " " if text.startswith("`") or text.endswith("`") else ""
    return f"{delimiter}{padding}{text}{padding}{delimiter}"


def _items(values: Sequence[object]) -> str:
    return _code(", ".join(_value(value) for value in values), empty=_NONE)


def _artifact_lines(
    lines: list[str],
    *,
    label: str,
    artifact: ArtifactPointerV1 | None,
) -> None:
    if artifact is None:
        lines.append(f"- {label}: {_code(None)}")
        return
    lines.extend(
        (
            f"- {label} URI: {_code(artifact.uri)}",
            f"- {label} SHA-256: {_code(artifact.sha256)}",
            f"- {label} bytes: {_code(artifact.size_bytes)}",
            f"- {label} media type: {_code(artifact.media_type)}",
        )
    )


def _append_input_provenance(
    lines: list[str], *, inspiration_input: InspirationInputV1
) -> None:
    lines.extend(
        (
            "## Frozen input provenance",
            "",
            f"- Requirement revision: `{inspiration_input.requirement_revision}`",
            "- Search adapter snapshot: "
            f"{_code(inspiration_input.search_adapter.component_id)} / "
            f"{_code(inspiration_input.search_adapter.version)} / "
            f"{_code(inspiration_input.search_adapter.implementation_sha256)}",
            "- Vectorizer snapshot: "
            f"{_code(inspiration_input.vectorizer.component_id)} / "
            f"{_code(inspiration_input.vectorizer.version)} / "
            f"{_code(inspiration_input.vectorizer.implementation_sha256)}",
        )
    )
    _artifact_lines(
        lines,
        label="Approval-bound requirement",
        artifact=inspiration_input.requirement_artifact,
    )
    _artifact_lines(
        lines,
        label="Policy",
        artifact=inspiration_input.policy_artifact,
    )
    _artifact_lines(
        lines,
        label="Tag graph",
        artifact=inspiration_input.tag_graph_artifact,
    )
    _artifact_lines(
        lines,
        label="Transformation registry",
        artifact=inspiration_input.transformation_registry_artifact,
    )
    _artifact_lines(
        lines,
        label="Search fixture manifest",
        artifact=inspiration_input.search_fixture_artifact,
    )
    lines.extend(("", "### Parent catalog entries supplied to the run", ""))
    for parent in inspiration_input.parent_candidates:
        lines.extend(
            (
                f"- Candidate {_code(parent.candidate_id)} / structure "
                f"{_code(parent.structure_id)}",
                f"  - Artifact URI: {_code(parent.structure_artifact.uri)}",
                f"  - Artifact SHA-256: {_code(parent.structure_artifact.sha256)}",
                f"  - Artifact bytes: {_code(parent.structure_artifact.size_bytes)}",
            )
        )
    lines.append("")


def _append_execution_funnel(
    lines: list[str],
    *,
    bundle: InspirationBundleV1,
    queries: Sequence[SearchQueryV1],
    hits: Sequence[SearchHitV1],
    passages: Sequence[PassageV1],
    evidence_cards: Sequence[EvidenceCardV1],
    bridge_packets: Sequence[BridgePacketV1],
    transformation_plans: Sequence[TransformationPlanV1],
    search_attempts: Sequence[SearchAttemptRecord],
) -> None:
    ledger = bundle.cost_ledger
    collapsed_documents = max(0, ledger.raw_documents - ledger.unique_documents)
    retries = max(0, ledger.search_requests - len(queries))
    lines.extend(
        (
            "## Auditable execution funnel",
            "",
            f"- Logical metadata queries supplied: `{len(queries)}`",
            f"- Provider search attempts (ledger): `{ledger.search_requests}`",
            f"- Retry attempts derived from the ledger: `{retries}`",
            f"- Attempt records attached to this report: `{len(search_attempts)}`",
            f"- Raw search-hit records (ledger): `{ledger.raw_documents}`",
            f"- Search-hit records supplied: `{len(hits)}`",
            f"- Unique documents (ledger): `{ledger.unique_documents}`",
            f"- Duplicate raw-hit documents collapsed: `{collapsed_documents}`",
            f"- Extracted passages (ledger): `{ledger.extracted_passages}`",
            f"- Passage records supplied: `{len(passages)}`",
            f"- Vectorized passages (ledger): `{ledger.vectorized_passages}`",
            f"- Evidence cards supplied: `{len(evidence_cards)}`",
            f"- Search-supported bridge packets supplied: `{len(bridge_packets)}`",
            f"- Generated plans (ledger): `{ledger.generated_plans}`",
            f"- Rejected plans (ledger): `{ledger.rejected_plans}`",
            f"- Transformation records supplied: `{len(transformation_plans)}`",
            "- Candidates after run-internal identity deduplication "
            f"(ledger): `{ledger.candidates_after_internal_dedup}`",
            f"- Selected candidates: `{len(bundle.selected_candidates)}`",
            "- PDF full-text reads: `0`",
            f"- LLM calls: `{ledger.llm_calls}`",
            "",
            "Ledger values are authoritative cost counters. Supplied-record counts are "
            "shown separately so a missing report attachment cannot silently look like "
            "a zero-cost operation.",
            "",
        )
    )


def _append_cost_ledger(lines: list[str], *, bundle: InspirationBundleV1) -> None:
    lines.extend(("## Complete cost ledger", "", "| Field | Value |", "|---|---:|"))
    for field, value in bundle.cost_ledger.model_dump(mode="json").items():
        lines.append(f"| {_code(field)} | {_code(value)} |")
    lines.append("")


def _append_selection_audit(
    lines: list[str],
    *,
    selection_audit: Mapping[str, object] | None,
) -> None:
    lines.extend(("## Candidate identity and diversity audit", ""))
    if selection_audit is None:
        lines.extend(("- Selection audit was not attached.", ""))
        return

    scalar_fields = (
        ("Diversity mode", "diversity_mode"),
        ("Requested Top-K", "requested_top_k"),
        ("Structure-valid proposal rows", "structure_valid_proposal_count"),
        ("Candidates after exact merge", "post_exact_merge_candidate_count"),
        ("Proposal rows collapsed by exact merge", "exact_merge_reduction_count"),
        ("Selected candidates", "selected_candidate_count"),
        ("Pool physical routes", "pool_distinct_physical_route_count"),
        ("Selected physical routes", "selected_distinct_physical_route_count"),
        (
            "Requested physical-route floor",
            "requested_distinct_physical_route_count",
        ),
        ("Physical-route quota status", "route_quota_status"),
        ("Pool parent families", "pool_parent_family_count"),
        ("Selected parent families", "selected_parent_family_count"),
        ("Requested mechanism count", "requested_mechanism_count"),
        ("Available mechanism count", "available_mechanism_count"),
        ("Jointly feasible mechanism count", "feasible_mechanism_count"),
        ("Achieved mechanism count", "achieved_mechanism_count"),
        ("Mechanism quota status", "quota_status"),
        ("Pool multi-route groups", "pool_multi_route_group_count"),
        ("Selected multi-route groups", "selected_multi_route_group_count"),
        ("Selected exact duplicates", "selected_exact_duplicate_count"),
        ("Selected strict duplicates", "selected_strict_duplicate_count"),
    )
    for label, field in scalar_fields:
        lines.append(f"- {label}: {_code(selection_audit.get(field))}")

    for label, field in (
        ("Available mechanism IDs", "available_mechanism_ids"),
        ("Jointly feasible mechanism IDs", "feasible_mechanism_ids"),
        ("Achieved mechanism IDs", "achieved_mechanism_ids"),
        ("Underfill reasons", "underfill_reasons"),
    ):
        raw = selection_audit.get(field, ())
        values = raw if isinstance(raw, (list, tuple)) else (raw,)
        lines.append(f"- {label}: {_items(values)}")
    lines.extend(
        (
            "",
            "Exact-output merging happens before selection and retains every "
            "hash-distinct physical route. Strict structure groups and parent-family "
            "caps are never relaxed to fill Top-K.",
            "",
        )
    )


def _append_attempts(
    lines: list[str], *, search_attempts: Sequence[SearchAttemptRecord]
) -> None:
    lines.extend(("## Search attempt ledger", ""))
    if not search_attempts:
        lines.extend(
            (
                "No per-attempt records were attached to this report. See the complete "
                "cost ledger for the authoritative aggregate request count.",
                "",
            )
        )
        return
    for ordinal, attempt in enumerate(search_attempts, start=1):
        lines.extend(
            (
                f"### Attempt {ordinal}: {_code(attempt.query_id)} / "
                f"{attempt.attempt_number}",
                "",
                f"- Outcome: {_code(attempt.outcome)}",
                f"- Error code: {_code(attempt.error_code)}",
                f"- HTTP status: {_code(attempt.http_status)}",
                f"- Provider response bytes: `{attempt.response_bytes}`",
                "- Pre-attempt pacing delay (seconds): "
                f"{_code(attempt.pacing_delay_seconds)}",
                "- Post-error retry delay (seconds): "
                f"{_code(attempt.retry_delay_seconds)}",
                "",
            )
        )


def _append_queries(lines: list[str], *, queries: Sequence[SearchQueryV1]) -> None:
    lines.extend(("## Metadata queries", ""))
    if not queries:
        lines.extend(("No metadata query was executed.", ""))
        return
    for query in queries:
        lines.extend(
            (
                f"### {_code(query.query_id)}",
                "",
                f"- Kind: {_code(query.kind)}",
                f"- Text: {_code(query.text)}",
                f"- Normalized tag IDs: {_items(query.tag_ids)}",
                f"- Bridge rule ID: {_code(query.bridge_rule_id)}",
                "",
            )
        )


def _append_documents(lines: list[str], *, hits: Sequence[SearchHitV1]) -> None:
    lines.extend(("## Document-source lineage", ""))
    if not hits:
        lines.extend(("No metadata hit was retained.", ""))
        return
    lines.extend(
        (
            "Each entry is a raw query hit. Repeated document IDs intentionally retain "
            "all query and raw-response lineage after document-level processing "
            "deduplication.",
            "",
        )
    )
    for hit in hits:
        lines.extend(
            (
                f"### {_code(hit.hit_id)} — document {_code(hit.document_id)}",
                "",
                f"- Provider / record: {_code(hit.provider)} / "
                f"{_code(hit.provider_record_id)}",
                f"- Provider rank: `{hit.provider_rank}`",
                f"- Title: {_code(hit.title)}",
                f"- Published year: {_code(hit.published_year)}",
                f"- DOI: {_code(hit.doi)}",
                f"- arXiv ID: {_code(hit.arxiv_id)}",
                f"- Canonical URL: {_code(hit.canonical_url)}",
                f"- Query lineage: {_items(hit.query_ids)}",
            )
        )
        _artifact_lines(
            lines,
            label="Raw response artifact",
            artifact=hit.raw_response_artifact,
        )
        lines.append("")


def _append_passages(lines: list[str], *, passages: Sequence[PassageV1]) -> None:
    lines.extend(("## Selected bounded passages", ""))
    if not passages:
        lines.extend(("No passage passed the bounded extraction gate.", ""))
        return
    lines.extend(
        (
            "Passage text is deliberately omitted from this report. Locator, exact "
            "normalized-text hash, bounded size, tags, and source artifact are "
            "retained for audit without duplicating source text.",
            "",
        )
    )
    for passage in passages:
        locator = passage.locator
        offset = (
            f"{locator.start_offset}:{locator.end_offset}"
            if locator.start_offset is not None
            else _NOT_RECORDED
        )
        lines.extend(
            (
                f"### {_code(passage.passage_id)}",
                "",
                f"- Hit / document: {_code(passage.hit_id)} / "
                f"{_code(passage.document_id)}",
                f"- Locator kind: {_code(locator.kind)}",
                f"- Locator selector: {_code(locator.selector)}",
                f"- Section heading: {_code(locator.section_heading)}",
                f"- Character offsets: {_code(offset)}",
                f"- Normalized text SHA-256: {_code(passage.normalized_text_sha256)}",
                f"- Matched tag IDs: {_items(passage.matched_tag_ids)}",
                f"- Character count: `{passage.char_count}`",
                f"- Estimated token count: `{passage.estimated_token_count}`",
                f"- Lexical score: {_code(passage.lexical_score)}",
                "- Normalizer: "
                f"{_code(passage.normalizer.component_id)} version "
                f"{_code(passage.normalizer.version)} / "
                f"{_code(passage.normalizer.implementation_sha256)}",
            )
        )
        _artifact_lines(
            lines,
            label="Source artifact",
            artifact=passage.source_artifact,
        )
        lines.append("")


def _append_evidence_cards(
    lines: list[str], *, evidence_cards: Sequence[EvidenceCardV1]
) -> None:
    lines.extend(("## Evidence-card lineage", ""))
    if not evidence_cards:
        lines.extend(("No EvidenceCard was created.", ""))
        return
    lines.extend(
        (
            "EvidenceCards preserve bounded source assertions; they are not validated "
            "property findings. Assertion text is omitted here and represented by an "
            "exact UTF-8 hash plus its authoritative passage lineage.",
            "",
        )
    )
    for card in evidence_cards:
        claim_sha256 = hashlib.sha256(card.claim_text.encode("utf-8")).hexdigest()
        lines.extend(
            (
                f"### {_code(card.evidence_card_id)}",
                "",
                f"- Relation / scope: {_code(card.relation)} / "
                f"{_code(card.evidence_scope)}",
                f"- Assertion text SHA-256: {_code(claim_sha256)}",
                f"- Assertion character count: `{len(card.claim_text)}`",
                f"- Mechanism tag IDs: {_items(card.mechanism_tag_ids)}",
                f"- Applicability conditions: {_items(card.applicability_conditions)}",
                f"- Counterevidence entries: `{len(card.counterevidence)}`",
                f"- Passage lineage: {_items(card.passage_ids)}",
                "",
            )
        )


def _append_bridges(
    lines: list[str], *, bridge_packets: Sequence[BridgePacketV1]
) -> None:
    lines.extend(("## Search-supported bridge packets", ""))
    if not bridge_packets:
        lines.extend(("No curated bridge rule reached SEARCH_SUPPORTED status.", ""))
        return
    lines.extend(
        (
            "The following invariant and control fields come from curated bridge "
            "rules. SEARCH_SUPPORTED means bounded source-assertion lineage exists; "
            "it is not a validated material-property conclusion.",
            "",
        )
    )
    for packet in bridge_packets:
        lines.extend(
            (
                f"### {_code(packet.bridge_packet_id)}",
                "",
                f"- Bridge rule ID: {_code(packet.bridge_rule_id)}",
                f"- Status: {_code(packet.status)}",
                f"- Source-domain tag IDs: {_items(packet.source_domain_tag_ids)}",
                f"- Target tag IDs: {_items(packet.target_tag_ids)}",
                f"- Shared invariant: {_code(packet.shared_invariant)}",
                f"- Transferable control: {_code(packet.transferable_control)}",
                f"- Required conditions: {_items(packet.required_conditions)}",
                f"- Breaking conditions: {_items(packet.breaking_conditions)}",
                f"- Suggested query records: {_items(packet.suggested_queries)}",
                f"- EvidenceCard lineage: {_items(packet.evidence_card_ids)}",
                "",
            )
        )


def _append_transformations(
    lines: list[str], *, transformation_plans: Sequence[TransformationPlanV1]
) -> None:
    lines.extend(("## Transformation lineage", ""))
    if not transformation_plans:
        lines.extend(("No transformation record was generated.", ""))
        return
    for plan in transformation_plans:
        parameters = plan.parameters
        lines.extend(
            (
                f"### {_code(plan.plan_id)}",
                "",
                f"- Status: {_code(plan.status)}",
                "- Scientific conclusion: "
                f"{_code(str(plan.scientific_conclusion).lower())}",
                f"- Parent candidate ID: {_code(plan.parent_candidate_id)}",
                f"- Parent structure ID: {_code(plan.parent_structure_id)}",
                f"- Operator / version: {_code(plan.operator_id)} / "
                f"{_code(plan.operator_version)}",
                f"- Route SHA-256: {_code(plan.route_sha256)}",
                "- Equivalent-site indices: "
                f"{_items(parameters.equivalent_site_indices)}",
                f"- Species substitution: {_code(parameters.source_species)} → "
                f"{_code(parameters.target_species)}",
                f"- Preserved features: {_items(plan.preserved_features)}",
                f"- Changed features: {_items(plan.changed_features)}",
                f"- Falsification tests: {_items(plan.falsification_tests)}",
                f"- BridgePacket lineage: {_items(plan.bridge_packet_ids)}",
                f"- Output structure ID: {_code(plan.output_structure_id)}",
            )
        )
        _artifact_lines(
            lines,
            label="Parent structure artifact",
            artifact=plan.parent_structure_artifact,
        )
        _artifact_lines(
            lines,
            label="Output structure artifact",
            artifact=plan.output_structure_artifact,
        )
        if plan.validation_checks:
            lines.extend(("- Validation checks:", ""))
            for check in plan.validation_checks:
                lines.append(
                    f"  - {_code(check.check_id)}: {_code(check.status)} — "
                    f"{_code(check.detail)}"
                )
        else:
            lines.append("- Validation checks: `none recorded`")
        lines.append("")


def _append_candidates(lines: list[str], *, bundle: InspirationBundleV1) -> None:
    lines.extend(("## Candidate proposals", ""))
    if not bundle.selected_candidates:
        lines.extend(("No candidate passed the structure-selection gate.", ""))
        return
    for candidate in bundle.selected_candidates:
        scores = candidate.scores
        lines.extend(
            (
                f"### {candidate.selection_rank}. {_code(candidate.candidate_id)}",
                "",
                f"- Canonical structure: {_code(candidate.canonical_structure_id)}",
                f"- Representative plan ID: {_code(candidate.representative_plan_id)}",
                f"- Parent candidate IDs: {_items(candidate.parent_candidate_ids)}",
                f"- Mechanism tags: {_items(candidate.mechanism_tag_ids)}",
                f"- EvidenceCard lineage: {_items(candidate.evidence_card_ids)}",
                "- Hypothesis identity signature SHA-256: "
                f"{_code(candidate.hypothesis_signature_sha256)}",
                f"- Property status: `{PROPERTY_STATUS_UNKNOWN}`",
                "- Scientific conclusion: "
                f"{_code(str(candidate.scientific_conclusion).lower())}",
                f"- Score policy / version: {_code(scores.policy_id)} / "
                f"{_code(scores.score_version)}",
                f"- Quality score: {_code(scores.quality)}",
                f"- Evidence-coverage score: {_code(scores.evidence_coverage)}",
                f"- Redundancy penalty: {_code(scores.redundancy_penalty)}",
                f"- Score weights (quality / coverage / redundancy): "
                f"{_code(scores.quality_weight)} / {_code(scores.coverage_weight)} / "
                f"{_code(scores.redundancy_weight)}",
                f"- Selection score: {_code(scores.selection_score)}",
                "- Next falsification step: "
                f"{_code(candidate.next_falsification_step)}",
            )
        )
        _artifact_lines(
            lines,
            label="Structure artifact",
            artifact=candidate.structure_artifact,
        )
        lines.extend(("- Merged route lineage:", ""))
        for route in candidate.merged_routes:
            lines.extend(
                (
                    f"  - Plan {_code(route.plan_id)} / route "
                    f"{_code(route.route_sha256)}",
                    f"    - Parent candidate: {_code(route.parent_candidate_id)}",
                    f"    - Mechanism tags: {_items(route.mechanism_tag_ids)}",
                    f"    - Bridge packets: {_items(route.bridge_packet_ids)}",
                    f"    - Evidence cards: {_items(route.evidence_card_ids)}",
                )
            )
        lines.append("")


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
    search_attempts: Sequence[SearchAttemptRecord] = (),
    selection_audit: Mapping[str, object] | None = None,
) -> str:
    """Render a stable Markdown audit report without upgrading evidence claims."""

    lines = [
        "# Inspiration run report",
        "",
        f"- Project: {_code(inspiration_input.project_id)}",
        f"- Request: {_code(inspiration_input.request_id)}",
        f"- Run: {_code(inspiration_input.run_id)}",
        f"- Outcome: {_code(bundle.outcome)}",
        "- Scientific conclusion: "
        f"{_code(str(bundle.scientific_conclusion).lower())}",
        f"- Target property status: `{PROPERTY_STATUS_UNKNOWN}`",
        "",
        "The target property was not computed in this stage. Every structure is a "
        "proposal that requires downstream validation. Metadata source assertions, "
        "bridge-rule matches, structure checks, and ranking scores do not establish "
        "material-property validity.",
        "",
        "Only bounded metadata passages were vectorized. Source text remains "
        "untrusted data and was not used as an instruction surface.",
        "",
    ]
    _append_input_provenance(lines, inspiration_input=inspiration_input)
    _append_execution_funnel(
        lines,
        bundle=bundle,
        queries=queries,
        hits=hits,
        passages=passages,
        evidence_cards=evidence_cards,
        bridge_packets=bridge_packets,
        transformation_plans=transformation_plans,
        search_attempts=search_attempts,
    )
    _append_cost_ledger(lines, bundle=bundle)
    _append_selection_audit(lines, selection_audit=selection_audit)
    _append_attempts(lines, search_attempts=search_attempts)
    _append_queries(lines, queries=queries)
    _append_documents(lines, hits=hits)
    _append_passages(lines, passages=passages)
    _append_evidence_cards(lines, evidence_cards=evidence_cards)
    _append_bridges(lines, bridge_packets=bridge_packets)
    _append_transformations(lines, transformation_plans=transformation_plans)
    _append_candidates(lines, bundle=bundle)

    lines.extend(("## Review items", ""))
    if review_items:
        lines.extend(f"- {_code(item)}" for item in review_items)
    else:
        lines.append("- No manual structure review item was raised.")

    lines.extend(("", "## Next validation steps", ""))
    lines.extend(f"- {_code(step)}" for step in bundle.next_validation_steps)

    lines.extend(("", "## Limitations", ""))
    lines.extend(f"- {_code(limitation)}" for limitation in bundle.limitations)
    if warnings:
        lines.extend(("", "## Warnings", ""))
        lines.extend(f"- {_code(warning)}" for warning in warnings)
    lines.append("")
    return "\n".join(lines)
