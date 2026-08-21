from __future__ import annotations

import hashlib
import inspect
from typing import TypeVar

import pytest
from pydantic import ValidationError

import material_agent.research.flatband_main_gold as main_gold_module
from material_agent.inspiration.models import (
    StrictModel,
    canonical_sha256,
    deterministic_id,
)
from material_agent.research.flatband_analysis_v2 import (
    AnalysisTraceEvidenceV2,
    AnalysisTraceRoleV2,
    build_analysis_trace_evidence_v2,
)
from material_agent.research.flatband_arm_runtime import ArmExecutionScope
from material_agent.research.flatband_execution import (
    ExecutionPhase,
    ExecutionReleaseV2,
    MissingPositionReason,
    ProjectedTop5PositionV1,
    ResearchSystemId,
    RunCellStatus,
    SourceReceiptBundleV1,
    SystemConfigV1,
    TerminalRunResultV1,
    Top5ProjectionV1,
    replay_source_usage,
)
from material_agent.research.flatband_main_execution import (
    MainPhaseExecutionCellEvidenceV1,
    MainPhaseExecutionReleaseV1,
)
from material_agent.research.flatband_main_gold import (
    MainExpertIdentityCommitmentV1,
    MainExpertRegistryRefV1,
    MainGoldProvenance,
    MainGoldStatus,
    build_analysis_cell_from_main_gold_v1,
    build_main_duplicate_adjudication,
    build_main_expert_assignment,
    build_main_gold_formal_verifier_attestation,
    build_main_gold_release,
    build_main_label_adjudication,
    build_main_raw_duplicate_partition,
    build_main_raw_label,
    build_main_review_unit,
    build_main_reviewer_materials,
)
from tests.unit.test_flatband_research_analysis_v2 import (
    _cell as _analysis_cell,
)
from tests.unit.test_flatband_research_analysis_v2 import (
    _config as _analysis_config,
)
from tests.unit.test_flatband_research_analysis_v2 import (
    _identified as _analysis_identified,
)
from tests.unit.test_flatband_research_analysis_v2 import (
    _trace as _analysis_trace,
)
from tests.unit.test_flatband_research_arm_runtime import (
    _config as _arm_config,
)
from tests.unit.test_flatband_research_arm_runtime import (
    _trace as _arm_trace,
)

KEY = b"main-gold-test-ephemeral-key!!" + b"xx"
EXPERT_ANNOTATION_KEYS = {
    "reviewer-a": b"A" * 32,
    "reviewer-b": b"B" * 32,
    "adjudicator-a": b"C" * 32,
}
GUIDE_SHA = "a" * 64
RENDERER_SHA = "8" * 64
ModelT = TypeVar("ModelT", bound=StrictModel)


@pytest.fixture(autouse=True)
def _main_execution_structural_seam(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep this module focused on Gold, not the independent Main120 replay.

    ``test_flatband_research_main_execution.py`` covers the upstream phase
    bridge itself.  The synthetic phase releases below intentionally omit its
    very large Main120 custody preimage.  Only that foreign-model deep
    revalidation and adapter are isolated here: MainGold models, builders,
    exact replay/formal verifier, FAILED-position derivation, and the Analysis
    adapter all execute unmodified.
    """

    original_revalidate = main_gold_module._revalidate

    def layered_revalidate(value: object, model_type: type[ModelT]) -> ModelT:
        if model_type is MainPhaseExecutionReleaseV1:
            if not isinstance(value, MainPhaseExecutionReleaseV1):
                raise TypeError("expected a Main phase execution release")
            return value  # type: ignore[return-value]
        return original_revalidate(value, model_type)

    def execution_cells(
        release: MainPhaseExecutionReleaseV1,
    ) -> tuple[MainPhaseExecutionCellEvidenceV1, ...]:
        if not release.analysis_or_gold_denominator_included:
            raise ValueError("component derivations cannot enter Gold")
        return release.cells

    monkeypatch.setattr(main_gold_module, "_revalidate", layered_revalidate)
    monkeypatch.setattr(
        main_gold_module,
        "main_gold_execution_cells_from_main_v1",
        execution_cells,
    )


def _sha(value: object) -> str:
    return canonical_sha256(value)


def _readdress_with_update(
    value: ModelT,
    *,
    id_field: str,
    sha_field: str,
    prefix: str,
    update: dict[str, object],
) -> ModelT:
    tampered = value.model_copy(update=update)
    identifier, digest = main_gold_module._identity_values(
        tampered,
        id_field=id_field,
        sha_field=sha_field,
        prefix=prefix,
    )
    return type(value).model_validate(
        {
            **tampered.model_dump(mode="python", round_trip=True),
            id_field: identifier,
            sha_field: digest,
        }
    )


def _assignment(phase: ExecutionPhase = ExecutionPhase.DEVELOPMENT_ABLATIONS):
    return build_main_expert_assignment(
        phase=phase,
        expert_registry=MainExpertRegistryRefV1(
            registry_id="main-expert-registry",
            registry_sha256="9" * 64,
        ),
        reviewers=(
            MainExpertIdentityCommitmentV1(
                expert_id="reviewer-a",
                natural_person_commitment_sha256="1" * 64,
                annotation_key_commitment_sha256=hashlib.sha256(
                    EXPERT_ANNOTATION_KEYS["reviewer-a"]
                ).hexdigest(),
            ),
            MainExpertIdentityCommitmentV1(
                expert_id="reviewer-b",
                natural_person_commitment_sha256="2" * 64,
                annotation_key_commitment_sha256=hashlib.sha256(
                    EXPERT_ANNOTATION_KEYS["reviewer-b"]
                ).hexdigest(),
            ),
        ),
        adjudicators=(
            MainExpertIdentityCommitmentV1(
                expert_id="adjudicator-a",
                natural_person_commitment_sha256="3" * 64,
                annotation_key_commitment_sha256=hashlib.sha256(
                    EXPERT_ANNOTATION_KEYS["adjudicator-a"]
                ).hexdigest(),
            ),
        ),
        sealed_at="2026-08-10T12:04:30+08:00",
    )


def _materials(trace_or_traces, assignment):
    traces = (
        trace_or_traces
        if isinstance(trace_or_traces, tuple)
        else (trace_or_traces,)
    )
    return build_main_reviewer_materials(
        phase=assignment.phase,
        arm_traces=traces,
        expert_assignment=assignment,
        ephemeral_blinding_key=KEY,
        renderer_sha256=RENDERER_SHA,
        sealed_at="2026-08-10T12:05:00+08:00",
    )


def _failed_trace_evidence(
    *,
    case_id: str,
    case_sha: str,
    role: AnalysisTraceRoleV2,
    config: SystemConfigV1,
    label: str,
) -> AnalysisTraceEvidenceV2:
    cell_id = f"cell-{case_id}-{label}"
    run_id = f"run-{case_id}-{label}"
    budget_id = f"budget-{case_id}-{label}"
    budget_sha = _sha((case_id, label, "budget"))
    bundles = tuple(
        _analysis_identified(
            SourceReceiptBundleV1,
            id_field="receipt_bundle_id",
            sha_field="receipt_bundle_sha256",
            prefix="source-receipt-bundle",
            values={
                "source_id": budget.source_id,
                "retrieval_identity_sha256": _sha(
                    (cell_id, budget.source_id, "retrieval")
                ),
                "cache_snapshot_sha256": config.cache_snapshot_sha256,
                "budget_manifest_id": budget_id,
                "budget_manifest_sha256": budget_sha,
                "cell_id": cell_id,
                "run_id": run_id,
                "system_config_id": config.config_id,
                "system_config_sha256": config.config_sha256,
            },
        )
        for budget in config.source_budgets
    )
    terminal = _analysis_identified(
        TerminalRunResultV1,
        id_field="terminal_result_id",
        sha_field="terminal_result_sha256",
        prefix="terminal-result",
        values={
            "budget_manifest_id": budget_id,
            "budget_manifest_sha256": budget_sha,
            "ranking_id": None,
            "ranking_sha256": None,
            "cell_id": cell_id,
            "run_id": run_id,
            "case_id": case_id,
            "case_sha256": case_sha,
            "system_config_id": config.config_id,
            "system_config_sha256": config.config_sha256,
            "git_commit": "1" * 40,
            "runtime_environment_sha256": _sha("main-gold-runtime"),
            "status": RunCellStatus.FAILED,
            "source_receipt_bundles": bundles,
            "source_usage": tuple(replay_source_usage(item) for item in bundles),
            "walltime_ms": 1,
            "failure_reason_codes": ("SYNTHETIC_RUN_FAILED",),
            "completed_at": "2026-08-10T12:03:30+08:00",
        },
    )
    top5 = _analysis_identified(
        Top5ProjectionV1,
        id_field="projection_id",
        sha_field="projection_sha256",
        prefix="top5-projection",
        values={
            "cell_id": cell_id,
            "terminal_result_id": terminal.terminal_result_id,
            "terminal_result_sha256": terminal.terminal_result_sha256,
            "ranking_id": None,
            "ranking_sha256": None,
            "status": RunCellStatus.FAILED,
            "positions": tuple(
                ProjectedTop5PositionV1(
                    position=position,
                    forced_zero=True,
                    fixed_gain=0,
                    missing_reason=MissingPositionReason.RUN_FAILED,
                )
                for position in range(1, 6)
            ),
        },
    )
    return build_analysis_trace_evidence_v2(
        role=role,
        system_config=config,
        terminal_result=terminal,
        top5_projection=top5,
    )


def _phase_execution_release(
    trace_evidence: tuple[AnalysisTraceEvidenceV2, ...],
    *,
    label: str,
    source_phase: ExecutionPhase = ExecutionPhase.DEVELOPMENT_ABLATIONS,
) -> MainPhaseExecutionReleaseV1:
    inner_id = f"inner-execution-{label}"
    inner_sha = _sha((label, "inner-execution"))
    inner = ExecutionReleaseV2.model_construct(
        release_id=inner_id,
        release_sha256=inner_sha,
    )
    cells = []
    for evidence in trace_evidence:
        terminal = evidence.terminal_result
        digest = _sha(
            (
                label,
                evidence.role.value,
                terminal.case_id,
                terminal.cell_id,
                evidence.evidence_sha256,
            )
        )
        cells.append(
            MainPhaseExecutionCellEvidenceV1.model_construct(
                cell_evidence_id=deterministic_id(
                    "main-exec-cell-v1", {"cell_evidence_sha256": digest}
                ),
                cell_evidence_sha256=digest,
                analysis_role=evidence.role,
                source_execution_phase=source_phase,
                source_execution_release_id=inner_id,
                source_execution_release_sha256=inner_sha,
                case_id=terminal.case_id,
                case_sha256=terminal.case_sha256,
                system_config=evidence.system_config,
                terminal_result=terminal,
                top5_projection=evidence.top5_projection,
                arm_trace=evidence.trace,
                fusion_component_traces=evidence.fusion_component_traces,
            )
        )
    cells_tuple = tuple(
        sorted(cells, key=lambda item: (item.case_id, item.analysis_role.value))
    )
    outer_sha = _sha(
        (
            label,
            source_phase.value,
            tuple(item.cell_evidence_sha256 for item in cells_tuple),
        )
    )
    return MainPhaseExecutionReleaseV1.model_construct(
        release_id=deterministic_id(
            "main-phase-execution-v1", {"release_sha256": outer_sha}
        ),
        release_sha256=outer_sha,
        source_execution_phase=source_phase,
        execution_release=inner,
        cells=cells_tuple,
        assembled_at="2026-08-10T12:04:40+08:00",
        component_derivation_only=False,
        analysis_or_gold_denominator_included=True,
    )


def _success_evidence(
    *,
    case_id: str,
    case_sha: str,
    role: AnalysisTraceRoleV2,
    system_id: ResearchSystemId,
    label: str,
):
    trace = _analysis_trace(
        case_id=case_id,
        case_sha=case_sha,
        config=_analysis_config(system_id),
        scope=ArmExecutionScope.DEVELOPMENT,
        label=label,
    )
    evidence = _analysis_cell(role=role, trace=trace, grade=2).trace_evidence
    return trace, evidence


def _all_failed_phase_release(
    *, label: str = "all-failed"
) -> tuple[MainPhaseExecutionReleaseV1, tuple[AnalysisTraceEvidenceV2, ...]]:
    case_id = f"{label}-case"
    case_sha = _sha(case_id)
    evidence = tuple(
        _failed_trace_evidence(
            case_id=case_id,
            case_sha=case_sha,
            role=role,
            config=_arm_config(system_id),
            label=f"{system_id.value.lower()}-failed",
        )
        for role, system_id in (
            (AnalysisTraceRoleV2.B0, ResearchSystemId.B0),
            (AnalysisTraceRoleV2.E1, ResearchSystemId.E1),
            (AnalysisTraceRoleV2.E2_A, ResearchSystemId.E2_A),
            (AnalysisTraceRoleV2.E2_B, ResearchSystemId.E2_B),
            (AnalysisTraceRoleV2.E3, ResearchSystemId.E3),
        )
    )
    return _phase_execution_release(evidence, label=label), evidence


def _packet_by_blind(manifest):
    return {item.blinded_unit_id: item for item in manifest.packets}


def _no_forbidden_manifest_fields(value: object) -> None:
    forbidden = {
        "system_id",
        "system_config_id",
        "system_config_sha256",
        "selection_rank",
        "run_id",
        "cell_id",
        "role",
    }
    if isinstance(value, dict):
        assert not (set(value) & forbidden)
        for nested in value.values():
            _no_forbidden_manifest_fields(nested)
    elif isinstance(value, list):
        for nested in value:
            _no_forbidden_manifest_fields(nested)


def test_main_gold_mixed_failed_blinding_labels_duplicates_and_exact_replay() -> None:
    case_id = "mixed-gold-case"
    case_sha = _sha(case_id)
    b0_trace, b0_evidence = _success_evidence(
        case_id=case_id,
        case_sha=case_sha,
        role=AnalysisTraceRoleV2.B0,
        system_id=ResearchSystemId.B0,
        label="b0",
    )
    e2a_trace, e2a_evidence = _success_evidence(
        case_id=case_id,
        case_sha=case_sha,
        role=AnalysisTraceRoleV2.E2_A,
        system_id=ResearchSystemId.E2_A,
        label="e2a",
    )
    failed_evidence = (
        _failed_trace_evidence(
            case_id=case_id,
            case_sha=case_sha,
            role=role,
            config=_arm_config(system_id),
            label=f"{system_id.value.lower()}-failed",
        )
        for role, system_id in (
            (AnalysisTraceRoleV2.E1, ResearchSystemId.E1),
            (AnalysisTraceRoleV2.E2_B, ResearchSystemId.E2_B),
            (AnalysisTraceRoleV2.E3, ResearchSystemId.E3),
        )
    )
    phase_release = _phase_execution_release(
        (b0_evidence, e2a_evidence, *failed_evidence),
        label="mixed-success-failed",
    )
    traces = (b0_trace, e2a_trace)
    assignment = _assignment()
    manifests, identity_maps = _materials(traces, assignment)
    for manifest in manifests:
        _no_forbidden_manifest_fields(manifest.model_dump(mode="json"))
        assert "blinding_commitment_sha256" not in manifest.model_dump(mode="json")
    assert all(item.key_material_included is False for item in identity_maps)

    pooled_ids = tuple(
        sorted(item.pooled_unit_id for item in identity_maps[0].entries)
    )
    raw_labels = []
    for manifest, identity_map in zip(manifests, identity_maps):
        packets = _packet_by_blind(manifest)
        for entry in identity_map.entries:
            assert entry.blinded_unit_id in packets
            if entry.pooled_unit_id == pooled_ids[0]:
                status, grade, evidence = MainGoldStatus.ASSESSABLE, 2, 1
            elif identity_map.reviewer_id == "reviewer-a":
                status, grade, evidence = MainGoldStatus.ASSESSABLE, 1, 0
            else:
                status, grade, evidence = (
                    MainGoldStatus.SYSTEM_PACKET_INVALID,
                    0,
                    0,
                )
            raw_labels.append(
                build_main_raw_label(
                    manifest,
                    identity_map,
                    blinded_unit_id=entry.blinded_unit_id,
                    annotation_guide_sha256=GUIDE_SHA,
                    status=status,
                    relevance_grade=grade,
                    evidence_gain=evidence,
                    submitted_at="2026-08-10T13:00:00+08:00",
                    reviewer_annotation_key=EXPERT_ANNOTATION_KEYS[
                        identity_map.reviewer_id
                    ],
                )
            )

    labels_by_pool = {
        pooled_id: tuple(
            item for item in raw_labels if item.pooled_unit_id == pooled_id
        )
        for pooled_id in pooled_ids
    }
    disputed_unit = next(
        item
        for item in (
            entry for identity_map in identity_maps for entry in identity_map.entries
        )
        if item.pooled_unit_id == pooled_ids[1]
    )
    review_unit = next(
        # Canonical public review unit can be reconstructed from its packet.
        build_main_review_unit(packet)
        for trace in traces
        for packet in trace.hypothesis_packets
        if packet.packet_id == disputed_unit.packet_id
    )
    label_adjudication = build_main_label_adjudication(
        review_unit,
        labels_by_pool[pooled_ids[1]],
        expert_assignment=assignment,
        adjudicator_id="adjudicator-a",
        resolution_reason_code="RESOLVED_VISIBLE_LABEL_CONFLICT",
        final_status=MainGoldStatus.ASSESSABLE,
        final_relevance_grade=1,
        final_evidence_gain=0,
        adjudicated_at="2026-08-10T13:02:00+08:00",
        adjudicator_annotation_key=EXPERT_ANNOTATION_KEYS["adjudicator-a"],
    )

    raw_partitions = []
    for index, (manifest, identity_map) in enumerate(
        zip(manifests, identity_maps)
    ):
        blind_ids = tuple(
            item.blinded_unit_id
            for item in identity_map.entries
            if item.case_id == case_id
        )
        groups = (blind_ids,) if index == 0 else tuple((item,) for item in blind_ids)
        raw_partitions.append(
            build_main_raw_duplicate_partition(
                manifest,
                identity_map,
                case_id=case_id,
                case_sha256=case_sha,
                blinded_clusters=groups,
                annotation_guide_sha256=GUIDE_SHA,
                submitted_at="2026-08-10T13:01:00+08:00",
                reviewer_annotation_key=EXPERT_ANNOTATION_KEYS[
                    identity_map.reviewer_id
                ],
            )
        )
    duplicate_adjudication = build_main_duplicate_adjudication(
        (raw_partitions[0], raw_partitions[1]),
        (identity_maps[0], identity_maps[1]),
        expert_assignment=assignment,
        adjudicator_id="adjudicator-a",
        final_pooled_clusters=(pooled_ids,),
        resolution_reason_code="RESOLVED_PARTITION_CONFLICT",
        adjudicated_at="2026-08-10T13:03:00+08:00",
        adjudicator_annotation_key=EXPERT_ANNOTATION_KEYS["adjudicator-a"],
    )
    gold_inputs = {
        "phase": ExecutionPhase.DEVELOPMENT_ABLATIONS,
        "phase_execution_releases": (phase_release,),
        "expert_assignment": assignment,
        "expert_annotation_keys": EXPERT_ANNOTATION_KEYS,
        "ephemeral_blinding_key": KEY,
        "renderer_sha256": RENDERER_SHA,
        "blinding_sealed_at": "2026-08-10T12:05:00+08:00",
        "annotation_guide_sha256": GUIDE_SHA,
        "raw_labels": tuple(raw_labels),
        "adjudications": (label_adjudication,),
        "raw_duplicate_partitions": tuple(raw_partitions),
        "duplicate_adjudications": (duplicate_adjudication,),
        "released_at": "2026-08-10T14:00:00+08:00",
    }
    release = build_main_gold_release(**gold_inputs)
    present = tuple(
        item for item in release.position_projections if item.packet_id is not None
    )
    missing = tuple(
        item for item in release.position_projections if item.packet_id is None
    )
    assert len(release.execution_cells) == 5
    assert len(release.position_projections) == 25
    assert len(present) == 2
    assert len(missing) == 23
    assert sum(item.trace_id is None for item in release.execution_cells) == 3
    failed_cell_ids = {
        item.cell_id for item in release.execution_cells if item.trace_id is None
    }
    failed_positions = tuple(
        item
        for item in release.position_projections
        if item.cell_id in failed_cell_ids
    )
    assert len(failed_positions) == 15
    assert all(
        item.absence_reason_codes == (MissingPositionReason.RUN_FAILED.value,)
        for item in failed_positions
    )
    assert present[0].final_duplicate_cluster_id == present[1].final_duplicate_cluster_id
    assert {item.provenance for item in present} == {
        MainGoldProvenance.AGREED_RAW,
        MainGoldProvenance.ADJUDICATED,
    }
    assert all(
        item.status is MainGoldStatus.SYSTEM_PACKET_INVALID
        and item.relevance_grade == 0
        and item.evidence_gain == 0
        for item in missing
    )
    verifier = build_main_gold_formal_verifier_attestation(
        release,
        phase_execution_releases=(phase_release,),
        expert_annotation_keys=EXPERT_ANNOTATION_KEYS,
        ephemeral_blinding_key=KEY,
        verified_at="2026-08-10T14:01:00+08:00",
    )
    assert verifier.exact_duplicate_partition_replay_verified is True
    assert verifier.expert_annotation_signatures_verified is True
    assert release.annotation_authentication_scope == (
        "INTERNAL_PRECOMMITTED_HMAC_POLICY"
    )
    assert release.external_expert_identity_attestation == "NOT_PROVIDED"
    assert release.external_annotation_key_custody_attestation == "NOT_PROVIDED"
    artifact_dump = release.model_dump(mode="python")
    assert all(
        secret not in repr(artifact_dump).encode("utf-8")
        for secret in EXPERT_ANNOTATION_KEYS.values()
    )

    agreed_label = labels_by_pool[pooled_ids[0]][0]
    agreed_map = next(
        item for item in identity_maps if item.reviewer_id == agreed_label.reviewer_id
    )
    agreed_manifest = next(
        item
        for item in manifests
        if item.manifest_id == agreed_label.reviewer_manifest_id
    )
    future_signed_label = build_main_raw_label(
        agreed_manifest,
        agreed_map,
        blinded_unit_id=agreed_label.blinded_unit_id,
        annotation_guide_sha256=GUIDE_SHA,
        status=agreed_label.status,
        relevance_grade=agreed_label.relevance_grade,
        evidence_gain=agreed_label.evidence_gain,
        submitted_at="2026-08-10T14:00:01+08:00",
        reviewer_annotation_key=EXPERT_ANNOTATION_KEYS[agreed_label.reviewer_id],
    )
    with pytest.raises(ValueError, match="follow every signed human input"):
        build_main_gold_release(
            **{
                **gold_inputs,
                "raw_labels": tuple(
                    future_signed_label if item is agreed_label else item
                    for item in raw_labels
                ),
            }
        )

    future_signed_adjudication = build_main_label_adjudication(
        review_unit,
        labels_by_pool[pooled_ids[1]],
        expert_assignment=assignment,
        adjudicator_id="adjudicator-a",
        resolution_reason_code="RESOLVED_VISIBLE_LABEL_CONFLICT",
        final_status=MainGoldStatus.ASSESSABLE,
        final_relevance_grade=1,
        final_evidence_gain=0,
        adjudicated_at="2026-08-10T14:00:01+08:00",
        adjudicator_annotation_key=EXPERT_ANNOTATION_KEYS["adjudicator-a"],
    )
    with pytest.raises(ValueError, match="follow every signed human input"):
        build_main_gold_release(
            **{
                **gold_inputs,
                "adjudications": (future_signed_adjudication,),
            }
        )

    forged_label = _readdress_with_update(
        raw_labels[0],
        id_field="label_id",
        sha_field="label_sha256",
        prefix="main-raw-label-v1",
        update={"submitted_at": "2026-08-10T13:00:01+08:00"},
    )
    with pytest.raises(ValueError, match="signature"):
        build_main_gold_release(
            **{
                **gold_inputs,
                "raw_labels": (forged_label, *tuple(raw_labels[1:])),
            }
        )

    forged_label_adjudication = _readdress_with_update(
        label_adjudication,
        id_field="adjudication_id",
        sha_field="adjudication_sha256",
        prefix="main-label-adjudication-v1",
        update={"resolution_reason_code": "FORGED_LABEL_DECISION"},
    )
    with pytest.raises(ValueError, match="signature"):
        build_main_gold_release(
            **{
                **gold_inputs,
                "adjudications": (forged_label_adjudication,),
            }
        )

    forged_raw_partition = _readdress_with_update(
        raw_partitions[0],
        id_field="partition_id",
        sha_field="partition_sha256",
        prefix="main-raw-duplicate-partition-v1",
        update={"submitted_at": "2026-08-10T13:01:01+08:00"},
    )
    with pytest.raises(ValueError, match="signature"):
        build_main_gold_release(
            **{
                **gold_inputs,
                "raw_duplicate_partitions": (
                    forged_raw_partition,
                    raw_partitions[1],
                ),
            }
        )

    forged_duplicate_adjudication = _readdress_with_update(
        duplicate_adjudication,
        id_field="adjudication_id",
        sha_field="adjudication_sha256",
        prefix="main-duplicate-adjudication-v1",
        update={"resolution_reason_code": "FORGED_DUPLICATE_DECISION"},
    )
    with pytest.raises(ValueError, match="signature"):
        build_main_gold_release(
            **{
                **gold_inputs,
                "duplicate_adjudications": (forged_duplicate_adjudication,),
            }
        )

    for invalid_keys in (
        {
            key: value
            for key, value in EXPERT_ANNOTATION_KEYS.items()
            if key != "reviewer-b"
        },
        {**EXPERT_ANNOTATION_KEYS, "foreign-expert": b"D" * 32},
    ):
        with pytest.raises(ValueError, match="exactly cover"):
            build_main_gold_release(
                **{**gold_inputs, "expert_annotation_keys": invalid_keys}
            )
    with pytest.raises(ValueError, match="frozen commitment"):
        build_main_gold_formal_verifier_attestation(
            release,
            phase_execution_releases=(phase_release,),
            expert_annotation_keys={
                **EXPERT_ANNOTATION_KEYS,
                "reviewer-a": b"Z" * 32,
            },
            ephemeral_blinding_key=KEY,
            verified_at="2026-08-10T14:01:00+08:00",
        )

    with pytest.raises(ValueError, match="key differs"):
        build_main_gold_formal_verifier_attestation(
            release,
            phase_execution_releases=(phase_release,),
            expert_annotation_keys=EXPERT_ANNOTATION_KEYS,
            ephemeral_blinding_key=b"wrong-main-gold-key-material-0000",
            verified_at="2026-08-10T14:01:00+08:00",
        )
    assert "position_projections" not in inspect.signature(
        build_main_gold_release
    ).parameters


def test_identity_alias_missing_adjudication_and_partial_partition_fail_closed() -> None:
    with pytest.raises(ValidationError, match="natural-person commitments"):
        build_main_expert_assignment(
            phase=ExecutionPhase.DEVELOPMENT_ABLATIONS,
            expert_registry=MainExpertRegistryRefV1(
                registry_id="main-expert-registry",
                registry_sha256="9" * 64,
            ),
            reviewers=(
                MainExpertIdentityCommitmentV1(
                    expert_id="alias-a",
                    natural_person_commitment_sha256="1" * 64,
                    annotation_key_commitment_sha256="4" * 64,
                ),
                MainExpertIdentityCommitmentV1(
                    expert_id="alias-b",
                    natural_person_commitment_sha256="1" * 64,
                    annotation_key_commitment_sha256="5" * 64,
                ),
            ),
            adjudicators=(
                MainExpertIdentityCommitmentV1(
                    expert_id="adjudicator-a",
                    natural_person_commitment_sha256="3" * 64,
                    annotation_key_commitment_sha256="6" * 64,
                ),
            ),
            sealed_at="2026-08-10T12:04:30+08:00",
        )

    trace = _arm_trace(_arm_config(ResearchSystemId.B0))
    assignment = _assignment()
    manifests, maps = _materials(trace, assignment)
    with pytest.raises(ValueError, match="exactly partition"):
        build_main_raw_duplicate_partition(
            manifests[0],
            maps[0],
            case_id=trace.ranking.case_id,
            case_sha256=trace.ranking.case_sha256,
            blinded_clusters=((maps[0].entries[0].blinded_unit_id,),),
            annotation_guide_sha256=GUIDE_SHA,
            submitted_at="2026-08-10T13:01:00+08:00",
            reviewer_annotation_key=EXPERT_ANNOTATION_KEYS["reviewer-a"],
        )


def test_gold_to_analysis_adapter_derives_present_cluster_and_analysis_missing_zeroes() -> None:
    case_id = "adapter-case"
    case_sha = "b" * 64
    trace = _analysis_trace(
        case_id=case_id,
        case_sha=case_sha,
        config=_analysis_config(ResearchSystemId.B0),
        scope=ArmExecutionScope.DEVELOPMENT,
        label="b0",
    )
    trace_evidence = _analysis_cell(
        role=AnalysisTraceRoleV2.B0,
        trace=trace,
        grade=2,
    ).trace_evidence
    phase_release = _phase_execution_release(
        (trace_evidence,), label="analysis-adapter"
    )
    assignment = _assignment()
    manifests, maps = _materials(trace, assignment)
    labels = tuple(
        build_main_raw_label(
            manifest,
            identity_map,
            blinded_unit_id=manifest.packets[0].blinded_unit_id,
            annotation_guide_sha256=GUIDE_SHA,
            status=MainGoldStatus.ASSESSABLE,
            relevance_grade=2,
            evidence_gain=1,
            submitted_at="2026-08-10T13:00:00+08:00",
            reviewer_annotation_key=EXPERT_ANNOTATION_KEYS[
                identity_map.reviewer_id
            ],
        )
        for manifest, identity_map in zip(manifests, maps)
    )
    partitions = tuple(
        build_main_raw_duplicate_partition(
            manifest,
            identity_map,
            case_id=case_id,
            case_sha256=case_sha,
            blinded_clusters=((manifest.packets[0].blinded_unit_id,),),
            annotation_guide_sha256=GUIDE_SHA,
            submitted_at="2026-08-10T13:01:00+08:00",
            reviewer_annotation_key=EXPERT_ANNOTATION_KEYS[
                identity_map.reviewer_id
            ],
        )
        for manifest, identity_map in zip(manifests, maps)
    )
    release = build_main_gold_release(
        phase=ExecutionPhase.DEVELOPMENT_ABLATIONS,
        phase_execution_releases=(phase_release,),
        expert_assignment=assignment,
        expert_annotation_keys=EXPERT_ANNOTATION_KEYS,
        ephemeral_blinding_key=KEY,
        renderer_sha256=RENDERER_SHA,
        blinding_sealed_at="2026-08-10T12:05:00+08:00",
        annotation_guide_sha256=GUIDE_SHA,
        raw_labels=labels,
        raw_duplicate_partitions=partitions,
        released_at="2026-08-10T14:00:00+08:00",
    )
    verifier = build_main_gold_formal_verifier_attestation(
        release,
        phase_execution_releases=(phase_release,),
        expert_annotation_keys=EXPERT_ANNOTATION_KEYS,
        ephemeral_blinding_key=KEY,
        verified_at="2026-08-10T14:01:00+08:00",
    )
    cell = build_analysis_cell_from_main_gold_v1(
        release,
        verifier,
        trace_evidence=trace_evidence,
    )
    assert len(cell.judgments) == 5
    assert cell.judgments[0].final_duplicate_cluster_id == (
        release.position_projections[0].final_duplicate_cluster_id
    )
    assert all(
        item.packet_id is None and item.relevance_grade == 0
        for item in cell.judgments[1:]
    )


def test_all_failed_phase_needs_no_review_and_replays_five_zeroes_per_cell() -> None:
    phase_release, trace_evidence = _all_failed_phase_release()
    assignment = _assignment()
    release = build_main_gold_release(
        phase=ExecutionPhase.DEVELOPMENT_ABLATIONS,
        phase_execution_releases=(phase_release,),
        expert_assignment=assignment,
        expert_annotation_keys=EXPERT_ANNOTATION_KEYS,
        ephemeral_blinding_key=KEY,
        renderer_sha256=RENDERER_SHA,
        blinding_sealed_at="2026-08-10T12:05:00+08:00",
        annotation_guide_sha256=GUIDE_SHA,
        raw_labels=(),
        raw_duplicate_partitions=(),
        released_at="2026-08-10T14:00:00+08:00",
    )
    assert len(release.execution_cells) == 5
    assert release.arm_traces == ()
    assert release.review_units == ()
    assert release.raw_labels == ()
    assert release.raw_duplicate_partitions == ()
    assert release.final_duplicate_partitions == ()
    assert len(release.position_projections) == 25
    assert all(
        item.status is MainGoldStatus.SYSTEM_PACKET_INVALID
        and item.relevance_grade == 0
        and item.evidence_gain == 0
        and item.provenance is MainGoldProvenance.SYSTEM_DERIVED_ABSENCE
        and item.absence_reason_codes == (MissingPositionReason.RUN_FAILED.value,)
        for item in release.position_projections
    )

    verifier = build_main_gold_formal_verifier_attestation(
        release,
        phase_execution_releases=(phase_release,),
        expert_annotation_keys=EXPERT_ANNOTATION_KEYS,
        ephemeral_blinding_key=KEY,
        verified_at="2026-08-10T14:01:00+08:00",
    )
    assert verifier.exact_gold_replay_verified is True
    for evidence in trace_evidence:
        cell = build_analysis_cell_from_main_gold_v1(
            release,
            verifier,
            trace_evidence=evidence,
        )
        assert len(cell.judgments) == 5
        assert all(
            item.packet_id is None
            and item.relevance_grade == 0
            and item.missing_reason is MissingPositionReason.RUN_FAILED
            for item in cell.judgments
        )


def test_omitted_or_foreign_phase_execution_release_fails_closed() -> None:
    phase_release, _trace_evidence = _all_failed_phase_release(
        label="execution-closure"
    )
    assignment = _assignment()
    common = {
        "phase": ExecutionPhase.DEVELOPMENT_ABLATIONS,
        "expert_assignment": assignment,
        "expert_annotation_keys": EXPERT_ANNOTATION_KEYS,
        "ephemeral_blinding_key": KEY,
        "renderer_sha256": RENDERER_SHA,
        "blinding_sealed_at": "2026-08-10T12:05:00+08:00",
        "annotation_guide_sha256": GUIDE_SHA,
        "raw_labels": (),
        "raw_duplicate_partitions": (),
        "released_at": "2026-08-10T14:00:00+08:00",
    }
    with pytest.raises(ValueError, match="execution phases differ"):
        build_main_gold_release(
            phase_execution_releases=(),
            **common,
        )

    wrong_phase = phase_release.model_copy(
        update={"source_execution_phase": ExecutionPhase.DEVELOPMENT_FUSION}
    )
    with pytest.raises(ValueError, match="execution phases differ"):
        build_main_gold_release(
            phase_execution_releases=(wrong_phase,),
            **common,
        )

    foreign_inner = phase_release.execution_release.model_copy(
        update={
            "release_id": "foreign-inner-execution",
            "release_sha256": _sha("foreign-inner-execution"),
        }
    )
    foreign_release = phase_release.model_copy(
        update={"execution_release": foreign_inner}
    )
    with pytest.raises(ValueError, match="lacks its outer Main phase release"):
        build_main_gold_release(
            phase_execution_releases=(foreign_release,),
            **common,
        )

    release = build_main_gold_release(
        phase_execution_releases=(phase_release,),
        **common,
    )
    with pytest.raises(ValueError, match="execution phases differ"):
        build_main_gold_formal_verifier_attestation(
            release,
            phase_execution_releases=(),
            expert_annotation_keys=EXPERT_ANNOTATION_KEYS,
            ephemeral_blinding_key=KEY,
            verified_at="2026-08-10T14:01:00+08:00",
        )
    with pytest.raises(ValueError, match="lacks its outer Main phase release"):
        build_main_gold_formal_verifier_attestation(
            release,
            phase_execution_releases=(foreign_release,),
            expert_annotation_keys=EXPERT_ANNOTATION_KEYS,
            ephemeral_blinding_key=KEY,
            verified_at="2026-08-10T14:01:00+08:00",
        )
