from __future__ import annotations

import hashlib
from typing import Any, TypeVar

import pytest
from pydantic import ValidationError

from material_agent.inspiration.models import (
    StrictModel,
    canonical_sha256,
    deterministic_id,
)
from material_agent.research.flatband_blinding import (
    EvidenceAccessPolicy,
    EvidenceExcerptV1,
    EvidenceExcerptV2,
    EvidenceRedistributionPolicy,
    EvidenceSpanScope,
    EvidenceSpanType,
    MAX_REVIEWER_EXCERPT_CHARS,
    PostLabelOriginGuessV1,
    PrivateIdentityMapV1,
    PrivateIdentityMapV2,
    ReviewerCaseProjectionV1,
    ReviewerEvidenceSpanV1,
    ReviewerHypothesisPacketV1,
    ReviewerManifestV1,
    ReviewerManifestV2,
    assert_reviewer_release_exact_coverage,
    assert_reviewer_release_exact_coverage_v2,
    assert_reviewer_release_legacy_v2_upstream_exact_coverage,
    build_reviewer_release,
    build_reviewer_release_v2,
    build_reviewer_release_legacy_v2_upstream,
)
from material_agent.research.flatband_cases import (
    build_frozen_case_release_v2,
    build_pre_run_eligibility_release_v2,
)
from material_agent.research.flatband_execution import (
    BudgetManifestV1,
    BudgetManifestV2,
    ExecutionPhase,
    ResearchSystemId,
    RunCellStatus,
    TerminalRunResultV1,
    assemble_execution_release_v3,
    assemble_execution_release_v2,
    build_budget_manifest_v2,
    build_execution_matrix_v2,
    replay_source_usage,
)
from material_agent.research.flatband_experts import (
    assert_formal_pilot_expert_closure_legacy_v2_upstream,
    assert_formal_pilot_expert_closure_v3,
    build_private_expert_identity_attestation_v2,
    ExpertRole,
    PrivateNaturalPersonBindingV2,
)


ModelT = TypeVar("ModelT", bound=StrictModel)


def _identified(
    model_type: type[ModelT],
    *,
    id_field: str,
    sha_field: str,
    prefix: str,
    values: dict[str, Any],
) -> ModelT:
    draft = model_type.model_construct(**values)
    digest = canonical_sha256(
        draft.model_dump(mode="python", exclude={id_field, sha_field})
    )
    return model_type.model_validate(
        {
            **values,
            id_field: deterministic_id(prefix, {sha_field: digest}),
            sha_field: digest,
        }
    )


def _reidentified(
    model: ModelT,
    *,
    id_field: str,
    sha_field: str,
    prefix: str,
    **updates: object,
) -> ModelT:
    values = {
        field_name: getattr(model, field_name)
        for field_name in type(model).model_fields
        if field_name not in {id_field, sha_field}
    }
    values.update(updates)
    return _identified(
        type(model),
        id_field=id_field,
        sha_field=sha_field,
        prefix=prefix,
        values=values,
    )


def _rehashed_case_projection(
    projection: ReviewerCaseProjectionV1, **updates: object
) -> ReviewerCaseProjectionV1:
    values = {
        field_name: getattr(projection, field_name)
        for field_name in type(projection).model_fields
        if field_name != "reviewer_case_projection_sha256"
    }
    values.update(updates)
    draft = ReviewerCaseProjectionV1.model_construct(**values)
    return ReviewerCaseProjectionV1.model_validate(
        {
            **values,
            "reviewer_case_projection_sha256": canonical_sha256(
                draft.model_dump(
                    mode="python",
                    exclude={"reviewer_case_projection_sha256"},
                )
            ),
        }
    )


def _excerpt(
    *, packet_id: str, evidence_link_id: str, source_span_text: str
) -> EvidenceExcerptV1:
    text = source_span_text
    return EvidenceExcerptV1(
        packet_id=packet_id,
        evidence_link_id=evidence_link_id,
        source_span_text=source_span_text,
        source_span_char_count=len(source_span_text),
        source_span_sha256=hashlib.sha256(source_span_text.encode()).hexdigest(),
        excerpt_start_offset=0,
        excerpt_end_offset=len(source_span_text),
        span_scope=EvidenceSpanScope.ABSTRACT,
        span_type=EvidenceSpanType.VERBATIM_EXCERPT,
        normalized_work_citation="Anonymous normalized work citation (2025)",
        excerpt=text,
        excerpt_char_count=len(text),
        excerpt_sha256=hashlib.sha256(text.encode("utf-8")).hexdigest(),
        excerpt_truncated=False,
        access_policy=EvidenceAccessPolicy.AUTHORIZED_REVIEWER_ONLY,
        redistribution_policy=(
            EvidenceRedistributionPolicy.REVIEWER_PACKET_ONLY_NO_REDISTRIBUTION
        ),
    )


def _release_fixture():
    # Reuse the already-validated execution/expert test factories instead of
    # maintaining a second 30-case scientific design fixture here.
    import test_flatband_research_execution as execution_fixture
    from test_flatband_research_cases import _study

    study = _study()

    original_packet_factory = execution_fixture._packet
    original_manifest_factory = execution_fixture._pilot_manifest
    original_receipt_factory = execution_fixture._receipt_bundles
    original_span_factory = execution_fixture._span_text
    source_span_text = (
        "Bounded source span for an anonymous work reports a compact "
        "localized interference mode."
    )

    def balanced_pilot_manifest():
        return study.manifest

    def packet_with_verifiable_span(**kwargs: Any):
        packet = original_packet_factory(**kwargs)
        link = packet.evidence_links[0].model_copy(
            update={
                "span_sha256": hashlib.sha256(
                    source_span_text.encode("utf-8")
                ).hexdigest()
            }
        )
        values = packet.model_dump(
            mode="python", exclude={"packet_id", "packet_sha256"}
        )
        values["evidence_links"] = (link,)
        values["falsification"] = packet.falsification
        return _identified(
            type(packet),
            id_field="packet_id",
            sha_field="packet_sha256",
            prefix="hypothesis-packet",
            values=values,
        )

    def receipt_bundles_with_identity(budget: Any):
        return tuple(
            execution_fixture._identified(
                execution_fixture.SourceReceiptBundleV1,
                id_field="receipt_bundle_id",
                sha_field="receipt_bundle_sha256",
                prefix="source-receipt-bundle",
                values={
                    "source_id": item.source_id,
                    "retrieval_identity_sha256": item.retrieval_identity_sha256,
                    "cache_snapshot_sha256": (
                        budget.system_config.cache_snapshot_sha256
                    ),
                    "logical_pages": (),
                    "physical_hops": (),
                },
            )
            for item in budget.system_config.source_budgets
        )

    execution_fixture._pilot_manifest = balanced_pilot_manifest
    execution_fixture._packet = packet_with_verifiable_span
    execution_fixture._receipt_bundles = receipt_bundles_with_identity
    execution_fixture._span_text = lambda _span_id: source_span_text
    try:
        release = execution_fixture._assemble(
            execution_fixture.valid_objects.__wrapped__()
        )
    finally:
        execution_fixture._packet = original_packet_factory
        execution_fixture._pilot_manifest = original_manifest_factory
        execution_fixture._receipt_bundles = original_receipt_factory
        execution_fixture._span_text = original_span_factory
    registry = study.registry
    excerpts = tuple(
        _excerpt(
            packet_id=packet.packet_id,
            evidence_link_id=link.evidence_link_id,
            source_span_text=source_span_text,
        )
        for packet in release.hypothesis_packets
        for link in packet.evidence_links
    )
    return release, study.eligibility, registry, excerpts


@pytest.fixture(scope="module")
def reviewer_release():
    release, eligibility, registry, excerpts = _release_fixture()
    manifests, private_maps = build_reviewer_release(
        execution_release=release,
        pre_run_eligibility_release=eligibility,
        expert_registry=registry,
        evidence_excerpts=excerpts,
        blind_key=b"pilot-reviewer-blind-key-v1-32bytes!",
        renderer_sha256="d" * 64,
        sealed_at="2026-08-09T13:00:00+08:00",
    )
    return release, eligibility, registry, excerpts, manifests, private_maps


def _formal_v2_release_fixture(
    *,
    all_systems_failed_case: bool = False,
    two_packets_one_case: bool = False,
    authoritative_v3: bool = False,
) -> dict[str, object]:
    """One real 30-case chain producing V2 reviewer artifacts."""

    import test_flatband_research_cases as cases_fixture
    import test_flatband_research_execution as execution_fixture
    import test_flatband_research_experts as experts_fixture

    if authoritative_v3:
        upstream = cases_fixture._formal_v3_study()
        split = upstream.manifest
        registry = upstream.registry
        frozen = upstream.frozen
        eligibility = upstream.eligibility
        leakage = upstream.leakage
        pre_budget_closure = upstream.pre_budget_closure
    else:
        upstream = experts_fixture._formal_v2_positive_upstream()
        split = upstream["split"]
        registry = upstream["registry"]
        frozen = upstream["frozen"]
        eligibility = upstream["eligibility"]
        leakage = upstream["leakage"]
    if all_systems_failed_case and two_packets_one_case:
        raise ValueError("fixture modes are mutually exclusive")
    zero_packet_case_id = (
        split.cases[0].case_id if all_systems_failed_case else None
    )
    two_packet_case_id = (
        split.cases[0].case_id if two_packets_one_case else None
    )
    original_span_text = execution_fixture._span_text
    execution_fixture._span_text = lambda _span_id: (
        "Exact bounded metadata evidence for an anonymous source."
    )

    matrix = build_execution_matrix_v2(
        split,
        execution_fixture._pilot_configs(),
        phase=ExecutionPhase.PILOT_R1,
    )
    budgets: list[object] = []
    rankings: list[object] = []
    terminals: list[TerminalRunResultV1] = []
    packet_counts: dict[str, int] = {}
    for index, cell in enumerate(matrix.cells):
        base_budget = execution_fixture._budget(matrix, index)
        if authoritative_v3:
            budget = _identified(
                BudgetManifestV2,
                id_field="budget_manifest_id",
                sha_field="budget_manifest_sha256",
                prefix="budget-manifest-v2",
                values={
                    "frozen_case_release_id": frozen.release_id,
                    "frozen_case_release_sha256": frozen.release_sha256,
                    "pre_run_eligibility_release_id": eligibility.release_id,
                    "pre_run_eligibility_release_sha256": (
                        eligibility.release_sha256
                    ),
                    "pre_budget_closure_release_id": (
                        pre_budget_closure.release_id
                    ),
                    "pre_budget_closure_release_sha256": (
                        pre_budget_closure.release_sha256
                    ),
                    "execution_matrix_id": matrix.matrix_id,
                    "execution_matrix_sha256": matrix.matrix_sha256,
                    "cell_id": cell.cell_id,
                    "cell_sha256": cell.cell_sha256,
                    "run_id": base_budget.run_id,
                    "case_id": cell.case_id,
                    "case_sha256": cell.case_sha256,
                    "system_config": base_budget.system_config,
                    "git_commit": base_budget.git_commit,
                    "runtime_environment_sha256": (
                        base_budget.runtime_environment_sha256
                    ),
                    "analysis_environment_sha256": (
                        base_budget.analysis_environment_sha256
                    ),
                    "max_walltime_seconds": base_budget.max_walltime_seconds,
                    "frozen_at": "2026-08-09T21:00:00+08:00",
                },
            )
            if index == 0:
                assert budget == build_budget_manifest_v2(
                    execution_matrix=matrix,
                    cell_id=cell.cell_id,
                    frozen_case_release=frozen,
                    pre_run_eligibility_release=eligibility,
                    pre_budget_closure_release=pre_budget_closure,
                    run_id=base_budget.run_id,
                    git_commit=base_budget.git_commit,
                    runtime_environment_sha256=(
                        base_budget.runtime_environment_sha256
                    ),
                    analysis_environment_sha256=(
                        base_budget.analysis_environment_sha256
                    ),
                    max_walltime_seconds=base_budget.max_walltime_seconds,
                    frozen_at="2026-08-09T21:00:00+08:00",
                )
        else:
            budget = execution_fixture._reidentified(
                base_budget,
                id_field="budget_manifest_id",
                sha_field="budget_manifest_sha256",
                prefix="budget-manifest",
                frozen_at="2026-08-09T21:00:00+08:00",
            )
        target_packets = (
            0
            if cell.case_id == zero_packet_case_id
            else (2 if cell.case_id == two_packet_case_id else 1)
        )
        has_packet = packet_counts.get(cell.case_id, 0) < target_packets
        ranking = (
            execution_fixture._ranking(
                budget,
                count=1,
                created_at="2026-08-09T21:00:02.500000+08:00",
            )
            if has_packet
            else None
        )
        if has_packet:
            packet_counts[cell.case_id] = packet_counts.get(cell.case_id, 0) + 1
        base_bundles = execution_fixture._receipt_bundles(budget)
        if ranking is None:
            bundles = base_bundles
        else:
            packets_for_cell = execution_fixture._packets_for_ranking(ranking)
            bundles = tuple(
                execution_fixture._source_receipt_bundle(
                    budget,
                    source_id=item.source_id,
                    physical_requests=(1 if item.source_id == "crossref" else 0),
                    logical_queries=(1 if item.source_id == "crossref" else 0),
                    pages=(1 if item.source_id == "crossref" else 0),
                    records=(1 if item.source_id == "crossref" else 0),
                    response_bytes=(1_024 if item.source_id == "crossref" else 0),
                    unique_documents=(1 if item.source_id == "crossref" else 0),
                    query_created_at="2026-08-09T21:00:01+08:00",
                    hop_completed_at="2026-08-09T21:00:02+08:00",
                    page_completed_at="2026-08-09T21:00:02+08:00",
                    hypothesis_packets=(
                        packets_for_cell if item.source_id == "crossref" else ()
                    ),
                )
                for item in budget.system_config.source_budgets
            )
            bundles = tuple(sorted(bundles, key=lambda item: item.source_id))
        status = RunCellStatus.PARTIAL if ranking is not None else RunCellStatus.FAILED
        terminal = _identified(
            TerminalRunResultV1,
            id_field="terminal_result_id",
            sha_field="terminal_result_sha256",
            prefix="terminal-result",
            values={
                "budget_manifest_id": budget.budget_manifest_id,
                "budget_manifest_sha256": budget.budget_manifest_sha256,
                "ranking_id": None if ranking is None else ranking.ranking_id,
                "ranking_sha256": None if ranking is None else ranking.ranking_sha256,
                "cell_id": budget.cell_id,
                "run_id": budget.run_id,
                "case_id": budget.case_id,
                "case_sha256": budget.case_sha256,
                "system_config_id": budget.system_config.config_id,
                "system_config_sha256": budget.system_config.config_sha256,
                "git_commit": budget.git_commit,
                "runtime_environment_sha256": budget.runtime_environment_sha256,
                "status": status,
                "source_receipt_bundles": bundles,
                "source_usage": tuple(replay_source_usage(item) for item in bundles),
                "walltime_ms": 1_000,
                "failure_reason_codes": ("RUN_INCOMPLETE",),
                "completed_at": "2026-08-09T21:00:03+08:00",
            },
        )
        budgets.append(budget)
        terminals.append(terminal)
        if ranking is not None:
            rankings.append(ranking)
    assert sum(packet_counts.values()) == (
        30 - int(all_systems_failed_case) + int(two_packets_one_case)
    )
    ranking_tuple = tuple(rankings)
    if authoritative_v3:
        execution = assemble_execution_release_v3(
            matrix,
            tuple(budgets),
            ranking_tuple,
            tuple(terminals),
            frozen_case_release=frozen,
            pre_run_eligibility_release=eligibility,
            pre_budget_closure_release=pre_budget_closure,
            hypothesis_packets=execution_fixture._packet_artifacts(
                ranking_tuple
            ),
            assembled_at="2026-08-09T21:00:04+08:00",
        )
    else:
        execution = assemble_execution_release_v2(
            matrix,
            tuple(budgets),
            ranking_tuple,
            tuple(terminals),
            hypothesis_packets=execution_fixture._packet_artifacts(
                ranking_tuple
            ),
            assembled_at="2026-08-09T21:00:04+08:00",
        )
    execution_fixture._span_text = original_span_text

    if authoritative_v3:
        private = build_private_expert_identity_attestation_v2(
            custodian_id="eligibility-identity-custodian",
            custodian_policy_sha256=canonical_sha256("identity-policy-v3"),
            bindings=tuple(
                PrivateNaturalPersonBindingV2(
                    expert_id=expert_id,
                    role=role,
                    opaque_natural_person_subject_ref=subject_ref,
                    natural_person_commitment_sha256=canonical_sha256(
                        (subject_ref, "natural-person")
                    ),
                    identity_evidence_artifact_uri=(
                        f"artifact://private/eligibility/{subject_ref}"
                    ),
                    identity_evidence_sha256=canonical_sha256(
                        (subject_ref, "identity-evidence")
                    ),
                )
                for expert_id, role, subject_ref in (
                    ("adjudicator-a", ExpertRole.ADJUDICATOR, "person-01"),
                    ("reviewer-a", ExpertRole.REVIEWER, "person-02"),
                    ("reviewer-b", ExpertRole.REVIEWER, "person-03"),
                    ("reviewer-c", ExpertRole.REVIEWER, "person-04"),
                )
            ),
            attested_at="2026-08-09T19:00:00+08:00",
        )
        pool = eligibility.assignment_release.candidate_pool_release
        candidate_by_id = {item.candidate_id: item for item in pool.candidates}
        selected_cases = tuple(
            candidate_by_id[item.selected_candidate_id].case
            for item in eligibility.active_selections
        )
        assert_formal_pilot_expert_closure_v3(
            registry=registry,
            split_manifest=split,
            benchmark_cases=selected_cases,
            private_identity_attestation=private,
            public_identity_release=eligibility.assignment_release.public_identity_release,
            calibration_manifest=eligibility.assignment_release.calibration_manifest,
            frozen_case_release=frozen,
            pre_run_eligibility_release=eligibility,
            execution_release=execution,
            leakage_release=leakage,
        )
    else:
        private = upstream["private"]
        assert_formal_pilot_expert_closure_legacy_v2_upstream(
            registry=registry,
            split_manifest=split,
            benchmark_cases=upstream["cases"],
            private_identity_attestation=upstream["private"],
            public_identity_release=upstream["public"],
            calibration_manifest=upstream["calibration"],
            frozen_case_release=frozen,
            pre_run_eligibility_release=eligibility,
            execution_release=execution,
            leakage_release=leakage,
        )

    packet_by_id = {item.packet_id: item for item in execution.hypothesis_packets}
    excerpts: list[EvidenceExcerptV2] = []
    for terminal in execution.terminal_results:
        for bundle in terminal.source_receipt_bundles:
            for receipt in bundle.evidence_links:
                packet = packet_by_id[receipt.packet_id]
                link = next(
                    item
                    for item in packet.evidence_links
                    if item.evidence_link_id == receipt.evidence_link_id
                )
                text = receipt.span_utf8
                excerpts.append(
                    EvidenceExcerptV2(
                        packet_id=packet.packet_id,
                        evidence_link_id=link.evidence_link_id,
                        cell_id=terminal.cell_id,
                        evidence_receipt_id=receipt.evidence_receipt_id,
                        evidence_receipt_sha256=receipt.evidence_receipt_sha256,
                        record_receipt_id=receipt.record_receipt_id,
                        record_receipt_sha256=receipt.record_receipt_sha256,
                        normalized_metadata_artifact_id=(
                            receipt.normalized_metadata_artifact_id
                        ),
                        normalized_metadata_artifact_sha256=(
                            receipt.normalized_metadata_artifact_sha256
                        ),
                        field_artifact_id=receipt.field_artifact_id,
                        field_artifact_sha256=receipt.field_artifact_sha256,
                        span_preimage_id=receipt.span_preimage_id,
                        span_preimage_sha256=receipt.span_preimage_sha256,
                        span_field=receipt.span_field,
                        metadata_json_path=receipt.metadata_json_path,
                        span_start_byte=receipt.span_start_byte,
                        span_end_byte=receipt.span_end_byte,
                        span_locator_sha256=receipt.span_locator_sha256,
                        source_span_text=text,
                        source_span_char_count=len(text),
                        source_span_sha256=receipt.span_sha256,
                        excerpt_start_offset=0,
                        excerpt_end_offset=len(text),
                        span_scope=EvidenceSpanScope.ABSTRACT,
                        span_type=EvidenceSpanType.VERBATIM_EXCERPT,
                        normalized_work_citation="Anonymous normalized work (2025)",
                        excerpt=text,
                        excerpt_char_count=len(text),
                        excerpt_sha256=hashlib.sha256(text.encode("utf-8")).hexdigest(),
                        excerpt_truncated=False,
                        access_policy=EvidenceAccessPolicy.AUTHORIZED_REVIEWER_ONLY,
                        redistribution_policy=(
                            EvidenceRedistributionPolicy.REVIEWER_PACKET_ONLY_NO_REDISTRIBUTION
                        ),
                    )
                )
    excerpt_tuple = tuple(excerpts)
    builder = (
        build_reviewer_release_v2
        if authoritative_v3
        else build_reviewer_release_legacy_v2_upstream
    )
    builder_inputs: dict[str, object] = {
        "execution_release": execution,
        "pre_run_eligibility_release": eligibility,
        "expert_registry": registry,
        "evidence_excerpts": excerpt_tuple,
        "blind_key": b"pilot-reviewer-blind-key-v2-32bytes!",
        "renderer_sha256": "e" * 64,
        "sealed_at": "2026-08-09T21:00:05+08:00",
    }
    if authoritative_v3:
        builder_inputs["frozen_case_release"] = frozen
    manifests, private_maps = builder(**builder_inputs)
    return {
        "upstream": upstream,
        "private": private,
        "registry": registry,
        "frozen": frozen,
        "leakage": leakage,
        "pre_budget_closure": (
            pre_budget_closure if authoritative_v3 else None
        ),
        "lineage_curation": (
            upstream.lineage_curation if authoritative_v3 else None
        ),
        "lineage_assignment_curation": (
            upstream.assignment_curation if authoritative_v3 else None
        ),
        "leakage_context": (
            upstream.leakage_context if authoritative_v3 else None
        ),
        "eligibility": eligibility,
        "execution": execution,
        "excerpts": excerpt_tuple,
        "manifests": manifests,
        "private_maps": private_maps,
        "blind_key": b"pilot-reviewer-blind-key-v2-32bytes!",
        "renderer_sha256": "e" * 64,
        "all_systems_failed_case_id": zero_packet_case_id,
        "two_packet_case_id": two_packet_case_id,
    }


@pytest.fixture(scope="module")
def reviewer_release_v2() -> dict[str, object]:
    return _formal_v2_release_fixture()


@pytest.fixture(scope="module")
def reviewer_release_formal_v3() -> dict[str, object]:
    return _formal_v2_release_fixture(authoritative_v3=True)


def test_formal_reviewer_v2_artifacts_exact_replay_v3_authoritative_chain(
    reviewer_release_formal_v3: dict[str, object],
) -> None:
    release = reviewer_release_formal_v3
    assert release["execution"].schema_version == "flatband-execution-release-v3"
    assert len(release["manifests"]) == len(release["private_maps"]) == 2
    assert {len(item.case_projections) for item in release["manifests"]} == {30}
    assert {len(item.entries) for item in release["private_maps"]} == {30}
    assert_reviewer_release_exact_coverage_v2(
        frozen_case_release=release["frozen"],
        execution_release=release["execution"],
        pre_run_eligibility_release=release["eligibility"],
        expert_registry=release["registry"],
        reviewer_manifests=release["manifests"],
        private_identity_maps=release["private_maps"],
        evidence_excerpts=release["excerpts"],
        blind_key=release["blind_key"],
        renderer_sha256=release["renderer_sha256"],
    )


def test_formal_v2_reviewer_closes_all_30_cases_with_two_independent_reviews(
    reviewer_release_v2: dict[str, object],
) -> None:
    execution = reviewer_release_v2["execution"]
    manifests = reviewer_release_v2["manifests"]
    private_maps = reviewer_release_v2["private_maps"]
    assert len(execution.hypothesis_packets) == 30
    assert len(manifests) == len(private_maps) == 2
    assert {len(item.case_projections) for item in manifests} == {30}
    assert {len(item.entries) for item in private_maps} == {30}
    assert {
        entry.case_id for private_map in private_maps for entry in private_map.entries
    } == {item.case_id for item in execution.execution_matrix.split_manifest.cases}
    order_by_reviewer = []
    for manifest, private_map in zip(manifests, private_maps, strict=True):
        original = {
            entry.reviewer_packet_id: entry.packet_id for entry in private_map.entries
        }
        order_by_reviewer.append(
            tuple(original[item.reviewer_packet_id] for item in manifest.packets)
        )
    assert order_by_reviewer[0] != order_by_reviewer[1]
    assert_reviewer_release_legacy_v2_upstream_exact_coverage(
        execution_release=execution,
        pre_run_eligibility_release=reviewer_release_v2["eligibility"],
        expert_registry=reviewer_release_v2["registry"],
        reviewer_manifests=manifests,
        private_identity_maps=private_maps,
        evidence_excerpts=reviewer_release_v2["excerpts"],
        blind_key=reviewer_release_v2["blind_key"],
        renderer_sha256=reviewer_release_v2["renderer_sha256"],
    )


def test_formal_reviewer_entry_rejects_legacy_v2_upstream(
    reviewer_release_v2: dict[str, object],
) -> None:
    with pytest.raises(
        (ValidationError, ValueError), match="flatband-execution-release-v3"
    ):
        build_reviewer_release_v2(
            frozen_case_release=reviewer_release_v2["frozen"],
            execution_release=reviewer_release_v2["execution"],
            pre_run_eligibility_release=reviewer_release_v2["eligibility"],
            expert_registry=reviewer_release_v2["registry"],
            evidence_excerpts=reviewer_release_v2["excerpts"],
            blind_key=reviewer_release_v2["blind_key"],
            renderer_sha256=reviewer_release_v2["renderer_sha256"],
            sealed_at="2026-08-09T21:00:05+08:00",
        )


def test_formal_v2_reviewer_keeps_failed_case_context_and_twenty_forced_zeros() -> None:
    release = _formal_v2_release_fixture(all_systems_failed_case=True)
    execution = release["execution"]
    zero_case_id = release["all_systems_failed_case_id"]
    assert zero_case_id is not None
    assert len(execution.hypothesis_packets) == 29
    cells = {item.cell_id: item for item in execution.execution_matrix.cells}
    failed_case_projections = tuple(
        item
        for item in execution.top5_projections
        if cells[item.cell_id].case_id == zero_case_id
    )
    assert len(failed_case_projections) == 4
    assert sum(
        item.forced_zero
        for projection in failed_case_projections
        for item in projection.positions
    ) == 20
    for manifest, private_map in zip(
        release["manifests"], release["private_maps"], strict=True
    ):
        assert len(manifest.case_projections) == 30
        assert len(manifest.packets) == 29
        assert len(private_map.case_entries) == 30
        assert len(private_map.entries) == 29
        assert zero_case_id in {item.case_id for item in private_map.case_entries}
        assert zero_case_id not in {item.case_id for item in private_map.entries}
    assert_reviewer_release_legacy_v2_upstream_exact_coverage(
        execution_release=execution,
        pre_run_eligibility_release=release["eligibility"],
        expert_registry=release["registry"],
        reviewer_manifests=release["manifests"],
        private_identity_maps=release["private_maps"],
        evidence_excerpts=release["excerpts"],
        blind_key=release["blind_key"],
        renderer_sha256=release["renderer_sha256"],
    )


def test_formal_v2_public_schema_physically_omits_execution_and_preimages() -> None:
    forbidden = {
        "case_id",
        "system_id",
        "run_id",
        "ranking_id",
        "selection_rank",
        "packet_id",
        "evidence_receipt_id",
        "record_receipt_id",
        "normalized_metadata_artifact_id",
        "field_artifact_id",
        "span_preimage_id",
        "span_locator_sha256",
    }
    assert not (forbidden & _schema_property_names(ReviewerManifestV2.model_json_schema()))
    assert forbidden & _schema_property_names(PrivateIdentityMapV2.model_json_schema())


def test_formal_v2_reviewer_rejects_missing_foreign_and_duplicate_maps(
    reviewer_release_v2: dict[str, object],
) -> None:
    manifests = reviewer_release_v2["manifests"]
    private_maps = reviewer_release_v2["private_maps"]
    common = {
        "execution_release": reviewer_release_v2["execution"],
        "pre_run_eligibility_release": reviewer_release_v2["eligibility"],
        "expert_registry": reviewer_release_v2["registry"],
        "reviewer_manifests": manifests,
        "evidence_excerpts": reviewer_release_v2["excerpts"],
        "blind_key": reviewer_release_v2["blind_key"],
        "renderer_sha256": reviewer_release_v2["renderer_sha256"],
    }
    missing = _reidentified(
        private_maps[0],
        id_field="identity_map_id",
        sha_field="identity_map_sha256",
        prefix="private-identity-map-v2",
        entries=private_maps[0].entries[:-1],
    )
    with pytest.raises(ValueError, match="exact deterministic replay"):
        assert_reviewer_release_legacy_v2_upstream_exact_coverage(
            **common, private_identity_maps=(missing, private_maps[1])
        )
    foreign = _reidentified(
        private_maps[0],
        id_field="identity_map_id",
        sha_field="identity_map_sha256",
        prefix="private-identity-map-v2",
        execution_release_id="execution-release-v2-foreign",
    )
    with pytest.raises(ValueError, match="exact deterministic replay"):
        assert_reviewer_release_legacy_v2_upstream_exact_coverage(
            **common, private_identity_maps=(foreign, private_maps[1])
        )
    with pytest.raises(ValueError, match="repeats a private reviewer"):
        assert_reviewer_release_legacy_v2_upstream_exact_coverage(
            **common, private_identity_maps=(private_maps[0], private_maps[0])
        )


def test_formal_v2_reviewer_rejects_missing_evidence_and_v1_projection(
    reviewer_release_v2: dict[str, object],
) -> None:
    common = {
        "execution_release": reviewer_release_v2["execution"],
        "pre_run_eligibility_release": reviewer_release_v2["eligibility"],
        "expert_registry": reviewer_release_v2["registry"],
        "reviewer_manifests": reviewer_release_v2["manifests"],
        "private_identity_maps": reviewer_release_v2["private_maps"],
        "blind_key": reviewer_release_v2["blind_key"],
        "renderer_sha256": reviewer_release_v2["renderer_sha256"],
    }
    with pytest.raises(ValueError, match="exactly cover execution evidence"):
        assert_reviewer_release_legacy_v2_upstream_exact_coverage(
            **common, evidence_excerpts=reviewer_release_v2["excerpts"][:-1]
        )
    with pytest.raises(ValidationError, match="flatband-reviewer-manifest-v2"):
        assert_reviewer_release_legacy_v2_upstream_exact_coverage(
            **{
                **common,
                "reviewer_manifests": (
                    {"schema_version": "flatband-reviewer-manifest-v1"},
                ),
                "evidence_excerpts": reviewer_release_v2["excerpts"],
            }
        )

    execution = reviewer_release_v2["execution"]
    terminal_index = next(
        index
        for index, terminal in enumerate(execution.terminal_results)
        if any(bundle.evidence_links for bundle in terminal.source_receipt_bundles)
    )
    terminal = execution.terminal_results[terminal_index]
    bundle_index = next(
        index
        for index, bundle in enumerate(terminal.source_receipt_bundles)
        if bundle.evidence_links
    )
    bundle = terminal.source_receipt_bundles[bundle_index]
    receipt = bundle.evidence_links[0]
    bad_locator = "0" * 64 if receipt.span_locator_sha256 != "0" * 64 else "1" * 64
    forged_receipt = receipt.model_copy(
        update={"span_locator_sha256": bad_locator}
    )
    forged_bundle = bundle.model_copy(
        update={
            "evidence_links": (forged_receipt, *bundle.evidence_links[1:])
        }
    )
    forged_terminal = terminal.model_copy(
        update={
            "source_receipt_bundles": (
                *terminal.source_receipt_bundles[:bundle_index],
                forged_bundle,
                *terminal.source_receipt_bundles[bundle_index + 1 :],
            )
        }
    )
    forged_execution = execution.model_copy(
        update={
            "terminal_results": (
                *execution.terminal_results[:terminal_index],
                forged_terminal,
                *execution.terminal_results[terminal_index + 1 :],
            )
        }
    )
    with pytest.raises(ValidationError, match="span locator"):
        assert_reviewer_release_legacy_v2_upstream_exact_coverage(
            **{
                **common,
                "execution_release": forged_execution,
                "evidence_excerpts": reviewer_release_v2["excerpts"],
            }
        )


def _schema_property_names(schema: object) -> set[str]:
    names: set[str] = set()
    if isinstance(schema, dict):
        properties = schema.get("properties")
        if isinstance(properties, dict):
            names.update(properties)
        for value in schema.values():
            names.update(_schema_property_names(value))
    elif isinstance(schema, list):
        for value in schema:
            names.update(_schema_property_names(value))
    return names


def test_reviewer_schemas_physically_omit_execution_and_private_identity() -> None:
    forbidden = {
        "case_id",
        "case_sha256",
        "system_id",
        "system_config_id",
        "system_config_sha256",
        "run_id",
        "ranking_id",
        "ranking_sha256",
        "selection_rank",
        "contributions",
        "provider",
        "expert_id",
        "reviewer_id",
        "packet_id",
        "packet_sha256",
        "strict_structure_group_id",
        "strict_hypothesis_group_id",
        "source_id",
        "source_record_id",
        "source_url",
        "private_text_artifact_uri",
    }
    for model in (
        ReviewerEvidenceSpanV1,
        ReviewerCaseProjectionV1,
        ReviewerHypothesisPacketV1,
        ReviewerManifestV1,
    ):
        assert not (forbidden & _schema_property_names(model.model_json_schema()))
    assert "formula" in ReviewerCaseProjectionV1.model_fields
    assert "parent_label" not in ReviewerCaseProjectionV1.model_fields
    assert "system_id" in _schema_property_names(
        PrivateIdentityMapV1.model_json_schema()
    )


def test_excerpt_is_bounded_hashed_and_extra_forbidden() -> None:
    excerpt = _excerpt(
        packet_id="packet-a",
        evidence_link_id="evidence-a",
        source_span_text="A bounded source span.",
    )
    with pytest.raises(ValidationError, match="excerpt SHA-256"):
        EvidenceExcerptV1.model_validate(
            {**excerpt.model_dump(mode="python"), "excerpt_sha256": "b" * 64}
        )
    with pytest.raises(ValidationError, match="at most 1200 characters"):
        EvidenceExcerptV1.model_validate(
            {
                **excerpt.model_dump(mode="python"),
                "excerpt": "x" * (MAX_REVIEWER_EXCERPT_CHARS + 1),
                "excerpt_char_count": MAX_REVIEWER_EXCERPT_CHARS + 1,
                "excerpt_sha256": hashlib.sha256(
                    ("x" * (MAX_REVIEWER_EXCERPT_CHARS + 1)).encode()
                ).hexdigest(),
            }
        )
    with pytest.raises(ValidationError, match="Extra inputs"):
        EvidenceExcerptV1.model_validate(
            {**excerpt.model_dump(mode="python"), "source_url": "https://example.test"}
        )


def test_build_is_deterministic_keyed_separate_and_exact(
    reviewer_release: Any,
) -> None:
    release, eligibility, registry, excerpts, manifests, private_maps = reviewer_release
    assert len(manifests) == len(private_maps) == 2
    assert {len(item.case_projections) for item in manifests} == {30}
    assert {len(item.case_entries) for item in private_maps} == {30}
    assert all(
        projection.hard_constraints and projection.forbidden_transformations
        for manifest in manifests
        for projection in manifest.case_projections
    )
    assert all(
        packet.reviewer_case_projection_id
        in {
            projection.reviewer_case_projection_id
            for projection in manifest.case_projections
        }
        for manifest in manifests
        for packet in manifest.packets
    )
    assert all(
        private_map.reviewer_manifest_sha256 == manifest.manifest_sha256
        for manifest, private_map in zip(manifests, private_maps, strict=True)
    )
    present_positions = sum(
        item.packet_id is not None
        for projection in release.top5_projections
        for item in projection.positions
    )
    assert {len(private_map.entries) for private_map in private_maps} == {
        present_positions
    }
    assert all(
        item.packet_id is None
        for projection in release.top5_projections
        for item in projection.positions
        if item.forced_zero
    )
    first_pool = {
        (entry.packet_id, entry.packet_sha256): entry.pooled_unit_id
        for entry in private_maps[0].entries
    }
    second_pool = {
        (entry.packet_id, entry.packet_sha256): entry.pooled_unit_id
        for entry in private_maps[1].entries
    }
    assert first_pool == second_pool
    assert {
        packet.blinded_unit_id for packet in manifests[0].packets
    }.isdisjoint({packet.blinded_unit_id for packet in manifests[1].packets})

    rebuilt = build_reviewer_release(
        execution_release=release,
        pre_run_eligibility_release=eligibility,
        expert_registry=registry,
        evidence_excerpts=excerpts,
        blind_key=b"pilot-reviewer-blind-key-v1-32bytes!",
        renderer_sha256="d" * 64,
        sealed_at="2026-08-09T13:00:00+08:00",
    )
    assert rebuilt == (manifests, private_maps)
    assert_reviewer_release_exact_coverage(
        execution_release=release,
        pre_run_eligibility_release=eligibility,
        expert_registry=registry,
        reviewer_manifests=manifests,
        private_identity_maps=private_maps,
        evidence_excerpts=excerpts,
        blind_key=b"pilot-reviewer-blind-key-v1-32bytes!",
        renderer_sha256="d" * 64,
    )


def test_reviewers_have_independent_orders_and_no_key_material(
    reviewer_release: Any,
) -> None:
    _release, _eligibility, _registry, _excerpts, manifests, private_maps = reviewer_release
    order_by_expert = []
    for manifest, private_map in zip(manifests, private_maps, strict=True):
        original_by_reviewer_packet = {
            item.reviewer_packet_id: item.packet_id for item in private_map.entries
        }
        order_by_expert.append(
            tuple(
                original_by_reviewer_packet[item.reviewer_packet_id]
                for item in manifest.packets
            )
        )
    assert order_by_expert[0] != order_by_expert[1]
    for manifest in manifests:
        serialized = manifest.model_dump_json()
        assert "pilot-reviewer-blind-key" not in serialized
        assert "https://" not in serialized
        assert "artifact://" not in serialized
        assert "crossref" not in serialized.casefold()
    assert all(not item.key_material_included for item in private_maps)


def test_build_rejects_url_adapter_leak_and_evidence_omission() -> None:
    release, eligibility, registry, excerpts = _release_fixture()
    first = excerpts[0]
    leaking_text = "Crossref record at https://doi.org/10.1000/example"
    leaking = first.model_copy(
        update={"normalized_work_citation": leaking_text}
    )
    with pytest.raises(ValueError, match="direct URL|adapter/provider"):
        build_reviewer_release(
            execution_release=release,
            pre_run_eligibility_release=eligibility,
            expert_registry=registry,
            evidence_excerpts=(leaking, *excerpts[1:]),
            blind_key=b"pilot-reviewer-blind-key-v1-32bytes!",
            renderer_sha256="d" * 64,
            sealed_at="2026-08-09T13:00:00+08:00",
        )
    with pytest.raises(ValueError, match="exactly cover"):
        build_reviewer_release(
            execution_release=release,
            pre_run_eligibility_release=eligibility,
            expert_registry=registry,
            evidence_excerpts=excerpts[:-1],
            blind_key=b"pilot-reviewer-blind-key-v1-32bytes!",
            renderer_sha256="d" * 64,
            sealed_at="2026-08-09T13:00:00+08:00",
        )


def test_private_exact_coverage_rejects_omission_and_public_hash_tamper(
    reviewer_release: Any,
) -> None:
    release, eligibility, registry, _excerpts, manifests, private_maps = reviewer_release
    omitted = private_maps[0].model_copy(update={"entries": private_maps[0].entries[:-1]})
    with pytest.raises(ValidationError, match="identity map SHA-256"):
        assert_reviewer_release_exact_coverage(
            execution_release=release,
            pre_run_eligibility_release=eligibility,
            expert_registry=registry,
            reviewer_manifests=manifests,
            private_identity_maps=(omitted, private_maps[1]),
            evidence_excerpts=_excerpts,
            blind_key=b"pilot-reviewer-blind-key-v1-32bytes!",
            renderer_sha256="d" * 64,
        )
    tampered_manifest = manifests[0].model_copy(
        update={"renderer_sha256": "e" * 64}
    )
    with pytest.raises(ValidationError, match="manifest SHA-256"):
        ReviewerManifestV1.model_validate(
            tampered_manifest.model_dump(mode="python", round_trip=True)
        )


def test_readdressed_scientific_content_cannot_bypass_authoritative_replay(
    reviewer_release: Any,
) -> None:
    release, eligibility, registry, excerpts, manifests, private_maps = reviewer_release
    original_packet = manifests[0].packets[0]
    packet_values = {
        field_name: getattr(original_packet, field_name)
        for field_name in type(original_packet).model_fields
        if field_name != "reviewer_packet_sha256"
    }
    packet_values["target_mapping"] = "A forged but source-neutral target mapping."
    draft = ReviewerHypothesisPacketV1.model_construct(**packet_values)
    forged_packet = ReviewerHypothesisPacketV1.model_validate(
        {
            **packet_values,
            "reviewer_packet_sha256": canonical_sha256(
                draft.model_dump(
                    mode="python", exclude={"reviewer_packet_sha256"}
                )
            ),
        }
    )
    forged_manifest = _reidentified(
        manifests[0],
        id_field="manifest_id",
        sha_field="manifest_sha256",
        prefix="reviewer-manifest",
        packets=(forged_packet, *manifests[0].packets[1:]),
    )
    changed_entries = tuple(
        entry.model_copy(
            update={"reviewer_packet_sha256": forged_packet.reviewer_packet_sha256}
        )
        if entry.reviewer_packet_id == forged_packet.reviewer_packet_id
        else entry
        for entry in private_maps[0].entries
    )
    forged_map = _reidentified(
        private_maps[0],
        id_field="identity_map_id",
        sha_field="identity_map_sha256",
        prefix="private-identity-map",
        reviewer_manifest_id=forged_manifest.manifest_id,
        reviewer_manifest_sha256=forged_manifest.manifest_sha256,
        entries=changed_entries,
    )
    with pytest.raises(ValueError, match="authoritative safe projection"):
        assert_reviewer_release_exact_coverage(
            execution_release=release,
            pre_run_eligibility_release=eligibility,
            expert_registry=registry,
            reviewer_manifests=(forged_manifest, manifests[1]),
            private_identity_maps=(forged_map, private_maps[1]),
            evidence_excerpts=excerpts,
            blind_key=b"pilot-reviewer-blind-key-v1-32bytes!",
            renderer_sha256="d" * 64,
        )


def test_readdressing_cannot_merge_pools_or_add_unmapped_public_manifest(
    reviewer_release: Any,
) -> None:
    release, eligibility, registry, excerpts, manifests, private_maps = reviewer_release
    entries = list(private_maps[0].entries)
    assert entries[0].packet_id != entries[1].packet_id
    entries[1] = entries[1].model_copy(
        update={"pooled_unit_id": entries[0].pooled_unit_id}
    )
    forged_map = _reidentified(
        private_maps[0],
        id_field="identity_map_id",
        sha_field="identity_map_sha256",
        prefix="private-identity-map",
        entries=tuple(entries),
    )
    with pytest.raises(ValueError, match="pooled unit"):
        assert_reviewer_release_exact_coverage(
            execution_release=release,
            pre_run_eligibility_release=eligibility,
            expert_registry=registry,
            reviewer_manifests=manifests,
            private_identity_maps=(forged_map, private_maps[1]),
            evidence_excerpts=excerpts,
            blind_key=b"pilot-reviewer-blind-key-v1-32bytes!",
            renderer_sha256="d" * 64,
        )

    extra_manifest = _reidentified(
        manifests[0],
        id_field="manifest_id",
        sha_field="manifest_sha256",
        prefix="reviewer-manifest",
        sealed_at="2026-08-09T13:00:01+08:00",
    )
    with pytest.raises(ValueError, match="one-to-one"):
        assert_reviewer_release_exact_coverage(
            execution_release=release,
            pre_run_eligibility_release=eligibility,
            expert_registry=registry,
            reviewer_manifests=(*manifests, extra_manifest),
            private_identity_maps=private_maps,
            evidence_excerpts=excerpts,
            blind_key=b"pilot-reviewer-blind-key-v1-32bytes!",
            renderer_sha256="d" * 64,
        )


def test_authoritative_replay_rejects_tampered_case_constraints(
    reviewer_release: Any,
) -> None:
    release, eligibility, registry, excerpts, manifests, private_maps = reviewer_release
    original = manifests[0].case_projections[0]
    forged_projection = _rehashed_case_projection(
        original,
        hard_constraints=tuple(
            sorted((*original.hard_constraints[1:], "forged relaxed constraint"))
        ),
    )
    forged_manifest = _reidentified(
        manifests[0],
        id_field="manifest_id",
        sha_field="manifest_sha256",
        prefix="reviewer-manifest",
        case_projections=(forged_projection, *manifests[0].case_projections[1:]),
    )
    forged_case_entries = tuple(
        entry.model_copy(
            update={
                "reviewer_case_projection_sha256": (
                    forged_projection.reviewer_case_projection_sha256
                )
            }
        )
        if entry.reviewer_case_projection_id
        == forged_projection.reviewer_case_projection_id
        else entry
        for entry in private_maps[0].case_entries
    )
    forged_map = _reidentified(
        private_maps[0],
        id_field="identity_map_id",
        sha_field="identity_map_sha256",
        prefix="private-identity-map",
        reviewer_manifest_id=forged_manifest.manifest_id,
        reviewer_manifest_sha256=forged_manifest.manifest_sha256,
        case_entries=forged_case_entries,
    )
    with pytest.raises(ValueError, match="authoritative safe projection"):
        assert_reviewer_release_exact_coverage(
            execution_release=release,
            pre_run_eligibility_release=eligibility,
            expert_registry=registry,
            reviewer_manifests=(forged_manifest, manifests[1]),
            private_identity_maps=(forged_map, private_maps[1]),
            evidence_excerpts=excerpts,
            blind_key=b"pilot-reviewer-blind-key-v1-32bytes!",
            renderer_sha256="d" * 64,
        )


def test_packet_cannot_reference_a_foreign_case_projection(
    reviewer_release: Any,
) -> None:
    release, eligibility, registry, excerpts, manifests, private_maps = reviewer_release
    original_packet = manifests[0].packets[0]
    foreign_projection = next(
        item
        for item in manifests[0].case_projections
        if item.reviewer_case_projection_id
        != original_packet.reviewer_case_projection_id
    )
    packet_values = {
        field_name: getattr(original_packet, field_name)
        for field_name in type(original_packet).model_fields
        if field_name != "reviewer_packet_sha256"
    }
    packet_values.update(
        {
            "reviewer_case_projection_id": (
                foreign_projection.reviewer_case_projection_id
            ),
            "blinded_case_id": foreign_projection.blinded_case_id,
        }
    )
    packet_draft = ReviewerHypothesisPacketV1.model_construct(**packet_values)
    forged_packet = ReviewerHypothesisPacketV1.model_validate(
        {
            **packet_values,
            "reviewer_packet_sha256": canonical_sha256(
                packet_draft.model_dump(
                    mode="python", exclude={"reviewer_packet_sha256"}
                )
            ),
        }
    )
    forged_manifest = _reidentified(
        manifests[0],
        id_field="manifest_id",
        sha_field="manifest_sha256",
        prefix="reviewer-manifest",
        packets=(forged_packet, *manifests[0].packets[1:]),
    )
    forged_entries = tuple(
        entry.model_copy(
            update={
                "reviewer_packet_sha256": forged_packet.reviewer_packet_sha256,
                "reviewer_case_projection_id": (
                    foreign_projection.reviewer_case_projection_id
                ),
            }
        )
        if entry.reviewer_packet_id == forged_packet.reviewer_packet_id
        else entry
        for entry in private_maps[0].entries
    )
    forged_map = _reidentified(
        private_maps[0],
        id_field="identity_map_id",
        sha_field="identity_map_sha256",
        prefix="private-identity-map",
        reviewer_manifest_id=forged_manifest.manifest_id,
        reviewer_manifest_sha256=forged_manifest.manifest_sha256,
        entries=forged_entries,
    )
    with pytest.raises(ValueError, match="foreign reviewer case projection"):
        assert_reviewer_release_exact_coverage(
            execution_release=release,
            pre_run_eligibility_release=eligibility,
            expert_registry=registry,
            reviewer_manifests=(forged_manifest, manifests[1]),
            private_identity_maps=(forged_map, private_maps[1]),
            evidence_excerpts=excerpts,
            blind_key=b"pilot-reviewer-blind-key-v1-32bytes!",
            renderer_sha256="d" * 64,
        )


def test_build_rejects_eligibility_sealed_at_first_execution_budget() -> None:
    release, eligibility, registry, excerpts = _release_fixture()
    late_eligibility = _reidentified(
        eligibility,
        id_field="release_id",
        sha_field="release_sha256",
        prefix="pre-run-eligibility-release",
        sealed_at="2026-08-09T12:00:00+08:00",
    )
    with pytest.raises(ValueError, match="not sealed before execution"):
        build_reviewer_release(
            execution_release=release,
            pre_run_eligibility_release=late_eligibility,
            expert_registry=registry,
            evidence_excerpts=excerpts,
            blind_key=b"pilot-reviewer-blind-key-v1-32bytes!",
            renderer_sha256="d" * 64,
            sealed_at="2026-08-09T13:00:00+08:00",
        )


def test_manifest_cannot_omit_or_add_assigned_case_projection(
    reviewer_release: Any,
) -> None:
    release, eligibility, registry, excerpts, manifests, private_maps = reviewer_release
    omitted_projection = manifests[0].case_projections[-1]
    assert omitted_projection.reviewer_case_projection_id not in {
        item.reviewer_case_projection_id for item in manifests[0].packets
    }
    missing_manifest = _reidentified(
        manifests[0],
        id_field="manifest_id",
        sha_field="manifest_sha256",
        prefix="reviewer-manifest",
        case_projections=manifests[0].case_projections[:-1],
    )
    missing_map = _reidentified(
        private_maps[0],
        id_field="identity_map_id",
        sha_field="identity_map_sha256",
        prefix="private-identity-map",
        reviewer_manifest_id=missing_manifest.manifest_id,
        reviewer_manifest_sha256=missing_manifest.manifest_sha256,
        case_entries=tuple(
            item
            for item in private_maps[0].case_entries
            if item.reviewer_case_projection_id
            != omitted_projection.reviewer_case_projection_id
        ),
    )
    with pytest.raises(ValueError, match="exactly cover assigned cases"):
        assert_reviewer_release_exact_coverage(
            execution_release=release,
            pre_run_eligibility_release=eligibility,
            expert_registry=registry,
            reviewer_manifests=(missing_manifest, manifests[1]),
            private_identity_maps=(missing_map, private_maps[1]),
            evidence_excerpts=excerpts,
            blind_key=b"pilot-reviewer-blind-key-v1-32bytes!",
            renderer_sha256="d" * 64,
        )

    foreign_projection = _rehashed_case_projection(
        omitted_projection,
        reviewer_case_projection_id="reviewer-case-foreign",
        blinded_case_id="case-slot-foreign",
        case_display_order=31,
        formula="ForeignFormula",
    )
    extra_manifest = _reidentified(
        manifests[0],
        id_field="manifest_id",
        sha_field="manifest_sha256",
        prefix="reviewer-manifest",
        case_projections=(*manifests[0].case_projections, foreign_projection),
    )
    extra_map = _reidentified(
        private_maps[0],
        id_field="identity_map_id",
        sha_field="identity_map_sha256",
        prefix="private-identity-map",
        reviewer_manifest_id=extra_manifest.manifest_id,
        reviewer_manifest_sha256=extra_manifest.manifest_sha256,
    )
    with pytest.raises(ValueError, match="exactly cover assigned cases"):
        assert_reviewer_release_exact_coverage(
            execution_release=release,
            pre_run_eligibility_release=eligibility,
            expert_registry=registry,
            reviewer_manifests=(extra_manifest, manifests[1]),
            private_identity_maps=(extra_map, private_maps[1]),
            evidence_excerpts=excerpts,
            blind_key=b"pilot-reviewer-blind-key-v1-32bytes!",
            renderer_sha256="d" * 64,
        )


def test_post_label_origin_guess_is_independent_and_never_part_of_grade() -> None:
    guess = _identified(
        PostLabelOriginGuessV1,
        id_field="guess_id",
        sha_field="guess_sha256",
        prefix="origin-guess",
        values={
            "reviewer_manifest_id": "reviewer-manifest-a",
            "reviewer_manifest_sha256": "a" * 64,
            "blinded_reviewer_id": "reviewer-slot-a",
            "blinded_unit_id": "blind-unit-a",
            "sealed_annotation_sha256": "b" * 64,
            "guessed_system_id": ResearchSystemId.E1,
            "confidence": 3,
            "submitted_at": "2026-08-09T14:00:00+08:00",
        },
    )
    assert guess.label_was_sealed_first
    assert guess.excluded_from_grade
    assert "guessed_system_id" not in ReviewerManifestV1.model_fields
