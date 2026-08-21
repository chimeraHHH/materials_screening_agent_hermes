"""Unique top-level closure for the authoritative flat-band Pilot V3 chain.

This module deliberately emits no public artifact containing private identity
or curation records.  The private roots are verifier inputs only; successful
return proves that every public release replays from those roots.
"""

from __future__ import annotations

from datetime import datetime

from material_agent.research.flatband_analysis import (
    FormalPilotAgreementGateReleaseV1,
    FormalPilotAgreementReleaseV1,
    assert_formal_pilot_agreement_exact_closure,
    assert_formal_pilot_agreement_gate_exact_closure,
)
from material_agent.research.flatband_blinding import (
    EvidenceExcerptV2,
    PrivateIdentityMapV2,
    ReviewerManifestV2,
    assert_reviewer_release_exact_coverage_v2,
)
from material_agent.research.flatband_cases import (
    FrozenCaseReleaseV3,
    PilotPreBudgetClosureReleaseV3,
    PreRunEligibilityReleaseV3,
    assert_pre_run_eligibility_precedes_execution_v3,
)
from material_agent.research.flatband_contracts import (
    ExpertAdjudicationV1,
    RawExpertAnnotationV1,
)
from material_agent.research.flatband_execution import ExecutionReleaseV3
from material_agent.research.flatband_experts import (
    CalibrationSetManifestV2,
    ExpertStudyRegistryV2,
    PrivateExpertIdentityCustodianAttestationV2,
    PublicExpertIdentityReleaseV2,
    assert_formal_pilot_expert_closure_v3,
)
from material_agent.research.flatband_gold import (
    DuplicatePartitionAdjudicationV2,
    FinalGoldReleaseV2,
    RawDuplicatePartitionV2,
    assert_final_gold_closure_v2,
)
from material_agent.research.flatband_leakage import (
    LeakageRoundClosureContextV3,
    MechanismLineageAssignmentCurationReleaseV3,
    MechanismLineageCurationReleaseV3,
    assert_formal_mechanism_lineage_assignment_curation_v3,
    assert_formal_mechanism_lineage_registry_v3,
)


def _timestamp(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("timestamp must include a UTC offset")
    return parsed


def _require_schema_version(value: object, expected: str, label: str) -> None:
    """Cheap rejection only; successful closure still requires full replay."""

    if getattr(value, "schema_version", None) != expected:
        raise ValueError(f"{label} must use {expected}")


def _artifact_ref(value: object, id_field: str, sha_field: str) -> tuple[object, object]:
    try:
        return getattr(value, id_field), getattr(value, sha_field)
    except AttributeError as exc:
        raise ValueError(f"artifact is missing {id_field}/{sha_field}") from exc


def _assert_authoritative_v3_root_preflight(
    *,
    frozen_case_release: FrozenCaseReleaseV3,
    pre_run_eligibility_release: PreRunEligibilityReleaseV3,
    pre_budget_closure_release: PilotPreBudgetClosureReleaseV3,
    execution_release: ExecutionReleaseV3,
    lineage_curation_release: MechanismLineageCurationReleaseV3,
    lineage_assignment_curation_release: (
        MechanismLineageAssignmentCurationReleaseV3
    ),
) -> None:
    """Reject obvious legacy/foreign roots before the expensive exact replay.

    This is deliberately not an acceptance path.  It checks only schema and
    opaque identity references; the public verifier below still reconstructs
    every nested Pydantic object and invokes every scientific exact verifier.
    """

    for value, expected, label in (
        (
            execution_release,
            "flatband-execution-release-v3",
            "execution release",
        ),
        (
            frozen_case_release,
            "flatband-frozen-case-release-v3",
            "frozen-case release",
        ),
        (
            pre_run_eligibility_release,
            "flatband-pre-run-eligibility-release-v3",
            "eligibility release",
        ),
        (
            pre_budget_closure_release,
            "flatband-pilot-pre-budget-closure-v3",
            "pre-budget closure",
        ),
        (
            lineage_curation_release,
            "flatband-mechanism-lineage-curation-release-v3",
            "lineage-definition curation",
        ),
        (
            lineage_assignment_curation_release,
            "flatband-mechanism-lineage-assignment-curation-release-v3",
            "lineage-assignment curation",
        ),
    ):
        _require_schema_version(value, expected, label)

    frozen_ref = _artifact_ref(
        frozen_case_release, "release_id", "release_sha256"
    )
    eligibility_ref = _artifact_ref(
        pre_run_eligibility_release, "release_id", "release_sha256"
    )
    pre_budget_ref = _artifact_ref(
        pre_budget_closure_release, "release_id", "release_sha256"
    )
    if (
        execution_release.frozen_case_release_id,
        execution_release.frozen_case_release_sha256,
    ) != frozen_ref:
        raise ValueError("Pilot closure receives a foreign FrozenCaseReleaseV3")
    if (
        execution_release.pre_run_eligibility_release_id,
        execution_release.pre_run_eligibility_release_sha256,
    ) != eligibility_ref:
        raise ValueError("Pilot closure receives a foreign eligibility V3 release")
    if (
        execution_release.pre_budget_closure_release_id,
        execution_release.pre_budget_closure_release_sha256,
    ) != pre_budget_ref:
        raise ValueError("Pilot closure receives a foreign pre-budget closure")

    embedded_frozen = pre_budget_closure_release.frozen_case_release
    if _artifact_ref(embedded_frozen, "release_id", "release_sha256") != frozen_ref:
        raise ValueError("pre-budget closure embeds a foreign FrozenCaseReleaseV3")
    if _artifact_ref(
        pre_budget_closure_release.lineage_curation_release,
        "release_id",
        "release_sha256",
    ) != _artifact_ref(
        lineage_curation_release, "release_id", "release_sha256"
    ):
        raise ValueError("pre-budget closure binds foreign lineage curation")
    if (
        pre_budget_closure_release.lineage_assignment_curation_release_id,
        pre_budget_closure_release.lineage_assignment_curation_release_sha256,
    ) != _artifact_ref(
        lineage_assignment_curation_release,
        "release_id",
        "release_sha256",
    ):
        raise ValueError(
            "pre-budget closure binds foreign lineage-assignment curation"
        )


def assert_formal_pilot_closure_v3(
    *,
    private_identity_attestation: PrivateExpertIdentityCustodianAttestationV2,
    public_identity_release: PublicExpertIdentityReleaseV2,
    calibration_manifest: CalibrationSetManifestV2,
    expert_registry: ExpertStudyRegistryV2,
    lineage_curation_release: MechanismLineageCurationReleaseV3,
    lineage_assignment_curation_release: (
        MechanismLineageAssignmentCurationReleaseV3
    ),
    frozen_case_release: FrozenCaseReleaseV3,
    pre_run_eligibility_release: PreRunEligibilityReleaseV3,
    pre_budget_closure_release: PilotPreBudgetClosureReleaseV3,
    execution_release: ExecutionReleaseV3,
    reviewer_manifests: tuple[ReviewerManifestV2, ...],
    private_identity_maps: tuple[PrivateIdentityMapV2, ...],
    evidence_excerpts: tuple[EvidenceExcerptV2, ...],
    blind_key: bytes,
    renderer_sha256: str,
    raw_annotations: tuple[RawExpertAnnotationV1, ...],
    adjudications: tuple[ExpertAdjudicationV1, ...],
    raw_duplicate_partitions: tuple[RawDuplicatePartitionV2, ...],
    duplicate_partition_adjudications: tuple[
        DuplicatePartitionAdjudicationV2, ...
    ],
    final_gold_release: FinalGoldReleaseV2,
    agreement_release: FormalPilotAgreementReleaseV1,
    leakage_context: LeakageRoundClosureContextV3,
    agreement_gate: FormalPilotAgreementGateReleaseV1,
    prior_r1_agreement_release: FormalPilotAgreementReleaseV1 | None = None,
    prior_r1_gate: FormalPilotAgreementGateReleaseV1 | None = None,
    prior_r1_leakage_context: LeakageRoundClosureContextV3 | None = None,
) -> None:
    """Fail closed unless the complete V3 Pilot chain exactly replays.

    All parameters before the optional prior-R1 triplet are required.  The
    Gate verifier enforces that the triplet is absent for R1 and complete for
    R2, so no round can be silently projected into another.
    """

    _assert_authoritative_v3_root_preflight(
        frozen_case_release=frozen_case_release,
        pre_run_eligibility_release=pre_run_eligibility_release,
        pre_budget_closure_release=pre_budget_closure_release,
        execution_release=execution_release,
        lineage_curation_release=lineage_curation_release,
        lineage_assignment_curation_release=(
            lineage_assignment_curation_release
        ),
    )

    execution_input = ExecutionReleaseV3.model_validate(execution_release)
    execution = ExecutionReleaseV3.model_validate(
        execution_input.model_dump(mode="python", round_trip=True)
    )
    frozen = execution.frozen_case_release
    eligibility = execution.pre_run_eligibility_release
    pre_budget = execution.pre_budget_closure_release
    if frozen_case_release != frozen:
        raise ValueError("Pilot closure receives a foreign FrozenCaseReleaseV3")
    if pre_run_eligibility_release != eligibility:
        raise ValueError("Pilot closure receives a foreign eligibility V3 release")
    if pre_budget_closure_release != pre_budget:
        raise ValueError("Pilot closure receives a foreign pre-budget closure")
    assert_pre_run_eligibility_precedes_execution_v3(
        frozen_case_release=frozen,
        eligibility_release=eligibility,
        execution_release=execution,
    )

    split = frozen.split_manifest
    leakage = frozen.leakage_release
    registry = frozen.expert_registry
    if expert_registry != registry:
        raise ValueError("Pilot closure receives a foreign expert registry")
    pool = eligibility.assignment_release.candidate_pool_release
    curation = MechanismLineageCurationReleaseV3.model_validate(
        lineage_curation_release.model_dump(mode="python", round_trip=True)
    )
    assert_formal_mechanism_lineage_registry_v3(
        registry=pool.mechanism_lineage_registry,
        curation_release=curation,
    )
    if curation != pool.mechanism_lineage_curation_release:
        raise ValueError("Pilot closure receives a foreign lineage curation root")
    assignment_curation = MechanismLineageAssignmentCurationReleaseV3.model_validate(
        lineage_assignment_curation_release.model_dump(
            mode="python", round_trip=True
        )
    )
    if assignment_curation != (
        pool.mechanism_lineage_assignment_curation_release
    ):
        raise ValueError(
            "Pilot closure receives a foreign lineage-assignment curation root"
        )
    assert_formal_mechanism_lineage_assignment_curation_v3(
        registry=pool.mechanism_lineage_registry,
        definition_curation_release=curation,
        assignment_curation_release=assignment_curation,
        public_assignments=pool.candidate_lineage_assignments,
    )
    if (
        pre_budget.lineage_assignment_curation_release_id,
        pre_budget.lineage_assignment_curation_release_sha256,
    ) != (
        assignment_curation.release_id,
        assignment_curation.release_sha256,
    ):
        raise ValueError(
            "pre-budget closure does not bind the exact assignment curation"
        )
    if leakage.mechanism_lineage_registry != pool.mechanism_lineage_registry:
        raise ValueError("Pilot leakage changes the curated lineage registry")
    pool_sealed = _timestamp(pool.sealed_at)
    if any(
        timestamp >= pool_sealed
        for timestamp in (
            _timestamp(curation.assembled_at),
            _timestamp(assignment_curation.assembled_at),
            _timestamp(pool.mechanism_lineage_registry.sealed_at),
        )
    ):
        raise ValueError(
            "lineage curations and public registry must precede CandidatePool seal"
        )

    candidate_by_id = {item.candidate_id: item for item in pool.candidates}
    selected_cases = tuple(
        candidate_by_id[item.selected_candidate_id].case
        for item in eligibility.active_selections
        if item.selected_candidate_id is not None
    )
    if len(selected_cases) != 30:
        raise ValueError("formal Pilot closure requires all 30 selected cases")
    assert_formal_pilot_expert_closure_v3(
        registry=registry,
        split_manifest=split,
        benchmark_cases=selected_cases,
        private_identity_attestation=private_identity_attestation,
        public_identity_release=public_identity_release,
        calibration_manifest=calibration_manifest,
        frozen_case_release=frozen,
        pre_run_eligibility_release=eligibility,
        execution_release=execution,
        leakage_release=leakage,
    )

    reviewer_inputs = {
        "frozen_case_release": frozen,
        "execution_release": execution,
        "pre_run_eligibility_release": eligibility,
        "expert_registry": registry,
        "reviewer_manifests": reviewer_manifests,
        "private_identity_maps": private_identity_maps,
        "evidence_excerpts": evidence_excerpts,
        "blind_key": blind_key,
        "renderer_sha256": renderer_sha256,
    }
    assert_reviewer_release_exact_coverage_v2(**reviewer_inputs)

    gold_inputs = {
        **reviewer_inputs,
        "raw_annotations": raw_annotations,
        "adjudications": adjudications,
        "raw_duplicate_partitions": raw_duplicate_partitions,
        "duplicate_partition_adjudications": (
            duplicate_partition_adjudications
        ),
    }
    assert_final_gold_closure_v2(final_gold_release, **gold_inputs)

    agreement_inputs = {
        "split_manifest": split,
        "leakage_release": leakage,
        "lineage_curation_release": curation,
        **reviewer_inputs,
        "raw_annotations": raw_annotations,
    }
    assert_formal_pilot_agreement_exact_closure(
        agreement_release,
        **agreement_inputs,
    )
    agreement_units = {
        (
            item.case_id,
            item.pooled_unit_id,
            item.packet_id,
            item.packet_sha256,
        )
        for item in agreement_release.units
    }
    gold_units = {
        (
            item.case_id,
            item.pooled_unit_id,
            item.packet_id,
            item.packet_sha256,
        )
        for item in final_gold_release.judgments
    }
    if agreement_units != gold_units:
        raise ValueError(
            "Agreement and Gold do not exactly cover the same present pooled units"
        )

    context = LeakageRoundClosureContextV3.model_validate(
        leakage_context.model_dump(mode="python", round_trip=True)
    )
    if context != pre_budget.current_leakage_context:
        raise ValueError("Gate context differs from the pre-budget leakage seal")
    if pre_budget.prior_r1_leakage_context != prior_r1_leakage_context:
        raise ValueError("prior-R1 leakage differs between pre-budget and Gate inputs")
    if (
        context.cases,
        context.split_manifest,
        context.release,
    ) != (tuple(sorted(selected_cases, key=lambda item: item.case_id)), split, leakage):
        raise ValueError("Gate leakage context differs from the exact Pilot round")
    assert_formal_pilot_agreement_gate_exact_closure(
        agreement_gate,
        agreement_release=agreement_release,
        leakage_context=context,
        lineage_curation_release=curation,
        prior_r1_agreement_release=prior_r1_agreement_release,
        prior_r1_gate=prior_r1_gate,
        prior_r1_leakage_context=prior_r1_leakage_context,
    )


__all__ = ["assert_formal_pilot_closure_v3"]
