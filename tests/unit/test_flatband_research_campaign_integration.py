"""Structural integration smoke for the complete private flat-band campaign.

This fixture deliberately isolates only the expensive Pilot/Main120/runner
scientific replay.  Those upstream roots are synthetic custody shapes, not a
benchmark run.  Campaign-owned joins and every Main Gold, Analysis V2,
lifecycle, annotation-HMAC, release-control-HMAC, and reviewer-decision-HMAC
builder/assertion execute unmodified.  This smoke does not prove deep
Pydantic/scientific replay of the synthetic Main roots.
"""

from __future__ import annotations

import hashlib
import importlib
from pathlib import Path
from types import SimpleNamespace
from typing import TypeVar

import pytest

import material_agent.research.flatband_campaign as campaign
import material_agent.research.flatband_lifecycle as lifecycle
import material_agent.research.flatband_main_gold as main_gold
from material_agent.inspiration.models import (
    StrictModel,
    canonical_sha256,
    deterministic_id,
)
from material_agent.research.flatband_analysis_v2 import (
    AnalysisArtifactRefV2,
    AnalysisArtifactTypeV2,
    AnalysisCaseBindingV2,
    AnalysisTraceEvidenceV2,
    AnalysisTraceRoleV2,
    analysis_input_ref_v2,
    build_analysis_input_release_v2,
    build_analysis_trace_evidence_v2,
    build_formal_verifier_attestation_v1,
    derive_development_metric_rows_v2,
    derive_locked_result_family_v2,
)
from material_agent.research.flatband_arm_runtime import ArmExecutionScope
from material_agent.research.flatband_arm_runtime import build_arm_execution_trace
from material_agent.research.flatband_campaign import (
    FlatBandCampaignReleaseV1,
    LocalSensitivityNotRunReleaseV1,
    LockedExecutionPlanV1,
    LockedExecutionPlannedArmV1,
    _assert_identity_registry_join_v1,
    assemble_flatband_campaign_release_v1,
    assert_flatband_campaign_release_exact_v1,
    build_locked_execution_plan_v1,
)
from material_agent.research.flatband_contracts import BenchmarkSplit
from material_agent.research.flatband_execution import (
    BudgetManifestV1,
    ExecutionMatrixV2,
    ExecutionPhase,
    ExecutionReleaseV2,
    RankingPositionV1,
    ResearchRankingV1,
    ResearchSystemId,
    SystemConfigV1,
)
from material_agent.research.flatband_experts import (
    CaseExpertAssignmentV1,
    ExpertRole,
    ExpertStudyRegistryV2,
    PrivateExpertIdentityCustodianAttestationV2,
    PrivateNaturalPersonBindingV2,
)
from material_agent.research.flatband_ingress import (
    SourceCatalogCheckpointReleaseV1,
    build_source_catalog_checkpoint_release_v1,
)
from material_agent.research.flatband_lifecycle import (
    LifecycleArtifactType,
    PublicLimitationCodeV1,
    ReleaseAuthorityRoleV1,
    ReleaseControlKindV1,
    ScientificReviewerDecision,
    SystemConfigurationRefV1,
    VerifiedPayloadKind,
    typed_artifact_ref_v1,
)
from material_agent.research.flatband_main import (
    MainCandidatePoolReleaseV1,
    MainEligibilityReleaseV1,
    MainFrozenCaseReleaseV1,
    MainPhaseAuthorizationReleaseV1,
    MainPreBudgetClosureReleaseV1,
    MainSamplingPolicyReleaseV1,
)
from material_agent.research.flatband_main_execution import (
    MainPhaseExecutionCellEvidenceV1,
    MainPhaseExecutionReleaseV1,
)
from material_agent.research.flatband_main_gold import (
    MainExpertIdentityCommitmentV1,
    MainExpertRegistryRefV1,
    MainGoldStatus,
    build_analysis_cell_from_main_gold_v1,
    build_main_expert_assignment,
    build_main_gold_formal_verifier_attestation,
    build_main_gold_release,
    build_main_raw_duplicate_partition,
    build_main_raw_label,
    build_main_reviewer_materials,
)
from material_agent.research.flatband_workflow import PilotRoundArtifactsV3
import material_agent.research.flatband_workflow as pilot_workflow

import test_flatband_research_analysis_v2 as analysis_fixture
import test_flatband_research_arm_runtime as arm_fixture
import test_flatband_research_main as main_fixture
import test_flatband_research_main_gold as gold_fixture
import test_flatband_research_pilot as pilot_fixture


ModelT = TypeVar("ModelT", bound=StrictModel)
GUIDE_SHA = canonical_sha256("campaign-integration-annotation-guide")
RENDERER_SHA = canonical_sha256("campaign-integration-renderer")
GOLD_KEYS = {
    ExecutionPhase.DEVELOPMENT_ABLATIONS: b"A" * 32,
    ExecutionPhase.DEVELOPMENT_FUSION: b"F" * 32,
    ExecutionPhase.LOCKED_PRIMARY: b"L" * 32,
}
EXPERT_KEYS = {
    "reviewer-a": b"1" * 32,
    "reviewer-b": b"2" * 32,
    "adjudicator-a": b"3" * 32,
}


@pytest.fixture
def upstream_scientific_fixture_seam(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Isolate only synthetic Pilot/Main/Execution scientific preimages.

    The Main and execution unit suites own deep replay of these very large
    roots.  This cross-module smoke keeps their typed instances but bypasses
    only their deep revalidation/assertions.  Campaign structural assertions,
    Gold/Analysis/lifecycle builders and exact replay, and all HMAC checks are
    intentionally not patched.
    """

    main_fixture._contract_structure_only.__wrapped__(monkeypatch)

    # The authoritative Pilot fixture is intentionally reused only as an
    # upstream typed preimage.  Avoid its recursive scientific replay here;
    # dedicated Pilot tests own that replay.  Builders still create addressed
    # artifacts, and Campaign still checks every cross-module join.
    for module_name in (
        "material_agent.research.flatband_analysis",
        "material_agent.research.flatband_blinding",
        "material_agent.research.flatband_cases",
        "material_agent.research.flatband_execution",
        "material_agent.research.flatband_experts",
        "material_agent.research.flatband_gold",
        "material_agent.research.flatband_leakage",
        "material_agent.research.flatband_pilot",
        "material_agent.research.flatband_source_policy",
        "material_agent.research.flatband_structure_grouping",
        "material_agent.research.flatband_workflow",
    ):
        module = importlib.import_module(module_name)
        for helper_name in ("_revalidate", "_revalidate_v2", "_revalidate_v3"):
            if hasattr(module, helper_name):
                monkeypatch.setattr(
                    module, helper_name, lambda value, _model_type: value
                )
    for test_module_name in (
        "test_flatband_research_blinding",
        "test_flatband_research_gold",
    ):
        test_module = importlib.import_module(test_module_name)
        for helper_name in tuple(vars(test_module)):
            if helper_name.startswith("assert_formal_pilot_"):
                monkeypatch.setattr(
                    test_module, helper_name, lambda *_a, **_k: None
                )

    original_campaign_revalidate = campaign._revalidate
    original_campaign_build_addressed = campaign._build_addressed
    upstream_types = {
        PilotRoundArtifactsV3,
        MainSamplingPolicyReleaseV1,
        MainCandidatePoolReleaseV1,
        MainEligibilityReleaseV1,
        MainFrozenCaseReleaseV1,
        MainPreBudgetClosureReleaseV1,
        MainPhaseAuthorizationReleaseV1,
        MainPhaseExecutionReleaseV1,
    }

    def layered_campaign_revalidate(
        value: object, model_type: type[ModelT]
    ) -> ModelT:
        if model_type in upstream_types:
            return value  # type: ignore[return-value]
        if model_type is FlatBandCampaignReleaseV1:
            # Preserve the real Campaign model validator while avoiding only
            # a JSON round-trip through its synthetic upstream Main roots.
            return value.validate_campaign()  # type: ignore[union-attr,return-value]
        if model_type is LocalSensitivityNotRunReleaseV1:
            # The outer Campaign-owned closure still validates phase,
            # chronology, and address.  Only nested synthetic Main
            # authorization JSON re-parsing is isolated by this seam.
            return value.validate_release()  # type: ignore[union-attr,return-value]
        if model_type is LockedExecutionPlanV1:
            # Retain the complete Campaign-owned plan validation and exact
            # rebuild; isolate only its nested synthetic Main authorization
            # dump/parse cycle.
            return value.validate_plan()  # type: ignore[union-attr,return-value]
        return original_campaign_revalidate(value, model_type)

    def layered_campaign_build_addressed(
        model_type: type[ModelT],
        *,
        id_field: str,
        sha_field: str,
        prefix: str,
        values: dict[str, object],
    ) -> ModelT:
        if model_type is not FlatBandCampaignReleaseV1:
            return original_campaign_build_addressed(
                model_type,
                id_field=id_field,
                sha_field=sha_field,
                prefix=prefix,
                values=values,
            )
        # Envelope-only upstream seam: recursive Pydantic validation would
        # rerun the intentionally synthetic Main/Pilot scientific graph.  The
        # real Campaign model validator below still executes every
        # Campaign-owned structural join and its exact content address.
        draft = model_type.model_construct(**values)
        digest = canonical_sha256(
            draft.model_dump(
                mode="python",
                exclude={id_field, sha_field},
                warnings=False,
            )
        )
        envelope = model_type.model_construct(
            **values,
            **{
                id_field: deterministic_id(prefix, {sha_field: digest}),
                sha_field: digest,
            },
        )
        return envelope.validate_campaign()  # type: ignore[return-value]

    monkeypatch.setattr(campaign, "_revalidate", layered_campaign_revalidate)
    monkeypatch.setattr(
        campaign, "_build_addressed", layered_campaign_build_addressed
    )
    monkeypatch.setattr(
        campaign, "assert_formal_pilot_round_artifacts_v3", lambda *_a, **_k: None
    )
    for name in (
        "assert_main_sampling_policy_exact_v1",
        "assert_main_candidate_pool_exact_v1",
        "assert_main_eligibility_exact_v1",
        "assert_main_frozen_case_exact_v1",
        "assert_main_pre_budget_closure_exact_v1",
        "assert_main_phase_authorization_exact_v1",
        "assert_main_phase_execution_exact_v1",
    ):
        monkeypatch.setattr(campaign, name, lambda *_a, **_k: None)
    monkeypatch.setattr(
        campaign,
        "assert_distinct_natural_person_assignments_v2",
        lambda *_a, **_k: None,
    )

    original_gold_revalidate = main_gold._revalidate

    def layered_gold_revalidate(
        value: object, model_type: type[ModelT]
    ) -> ModelT:
        if model_type is MainPhaseExecutionReleaseV1:
            return value  # type: ignore[return-value]
        return original_gold_revalidate(value, model_type)

    def execution_cells(
        release: MainPhaseExecutionReleaseV1,
    ) -> tuple[MainPhaseExecutionCellEvidenceV1, ...]:
        if not release.analysis_or_gold_denominator_included:
            raise ValueError("component derivations cannot enter Gold")
        return release.cells

    def analysis_evidence(
        release: MainPhaseExecutionReleaseV1,
    ) -> tuple[AnalysisTraceEvidenceV2, ...]:
        return tuple(
            build_analysis_trace_evidence_v2(
                role=item.analysis_role,
                system_config=item.system_config,
                terminal_result=item.terminal_result,
                top5_projection=item.top5_projection,
                trace=item.arm_trace,
                fusion_component_traces=item.fusion_component_traces,
            )
            for item in release.cells
        )

    monkeypatch.setattr(main_gold, "_revalidate", layered_gold_revalidate)
    monkeypatch.setattr(
        main_gold, "main_gold_execution_cells_from_main_v1", execution_cells
    )
    monkeypatch.setattr(
        campaign, "main_gold_execution_cells_from_main_v1", execution_cells
    )
    monkeypatch.setattr(
        campaign, "build_analysis_trace_evidence_index_from_main_v1", analysis_evidence
    )


def _sha(value: object) -> str:
    return canonical_sha256(value)


def _private_identity() -> PrivateExpertIdentityCustodianAttestationV2:
    bindings = tuple(
        PrivateNaturalPersonBindingV2(
            expert_id=expert_id,
            role=role,
            opaque_natural_person_subject_ref=f"person-{index}",
            natural_person_commitment_sha256=_sha(("person", index)),
            identity_evidence_artifact_uri=(
                f"artifact://private/campaign-integration/person-{index}"
            ),
            identity_evidence_sha256=_sha(("identity-evidence", index)),
        )
        for index, (expert_id, role) in enumerate(
            (
                ("adjudicator-a", ExpertRole.ADJUDICATOR),
                ("reviewer-a", ExpertRole.REVIEWER),
                ("reviewer-b", ExpertRole.REVIEWER),
            ),
            start=1,
        )
    )
    semantic = {
        "custodian_id": "campaign-integration-custodian",
        "custodian_policy_sha256": _sha("campaign-integration-custodian-policy"),
        "bindings": bindings,
        "attested_at": "2026-08-10T07:00:00+08:00",
    }
    draft = PrivateExpertIdentityCustodianAttestationV2.model_construct(**semantic)
    digest = canonical_sha256(
        draft.model_dump(
            mode="python", exclude={"attestation_id", "attestation_sha256"}
        )
    )
    return PrivateExpertIdentityCustodianAttestationV2.model_validate(
        {
            **semantic,
            "attestation_id": deterministic_id(
                "expert-id-attestation-v2", {"attestation_sha256": digest}
            ),
            "attestation_sha256": digest,
        }
    )


def _registry(
    chain: object,
    private: PrivateExpertIdentityCustodianAttestationV2,
) -> ExpertStudyRegistryV2:
    assignments = tuple(
        CaseExpertAssignmentV1(
            case_id=item.case_id,
            case_sha256=item.case_sha256,
            reviewer_ids=("reviewer-a", "reviewer-b"),
            adjudicator_id="adjudicator-a",
        )
        for item in chain.split.cases
    )
    # The full calibration/conflict graph is an upstream Main fixture seam;
    # Campaign itself consumes only this exact address, assignments, and
    # identity-attestation join.
    return ExpertStudyRegistryV2.model_construct(
        registry_id="campaign-integration-main-registry",
        registry_sha256=_sha("campaign-integration-main-registry"),
        split_manifest_id=chain.split.manifest_id,
        split_manifest_sha256=chain.split.manifest_sha256,
        annotation_guide_version="campaign-integration-v1",
        annotation_guide_sha256=GUIDE_SHA,
        calibration_manifest_id="campaign-integration-calibration",
        calibration_manifest_sha256=_sha("campaign-integration-calibration"),
        public_identity_release_id="campaign-integration-public-identities",
        public_identity_release_sha256=_sha("campaign-integration-public-identities"),
        identity_attestation_id=private.attestation_id,
        identity_attestation_sha256=private.attestation_sha256,
        experts=(),
        calibration_completions=(),
        conflict_assessments=(),
        assignments=assignments,
        registered_at="2026-08-10T07:30:00+08:00",
    )


def _phase_authorization(
    pre_budget: MainPreBudgetClosureReleaseV1,
    phase: ExecutionPhase,
    *,
    authorized_at: str,
) -> MainPhaseAuthorizationReleaseV1:
    return main_fixture.build_main_phase_authorization_release_v1(
        pre_budget_closure_release=pre_budget,
        execution_phase=phase,
        authorized_at=authorized_at,
    )


def _cell_from_evidence(
    evidence: AnalysisTraceEvidenceV2,
    *,
    source_phase: ExecutionPhase,
    inner_id: str,
    inner_sha: str,
) -> MainPhaseExecutionCellEvidenceV1:
    digest = _sha(
        (
            source_phase.value,
            evidence.role.value,
            evidence.terminal_result.cell_id,
            evidence.evidence_sha256,
        )
    )
    return MainPhaseExecutionCellEvidenceV1.model_construct(
        cell_evidence_id=deterministic_id(
            "main-exec-cell-v1", {"cell_evidence_sha256": digest}
        ),
        cell_evidence_sha256=digest,
        analysis_role=evidence.role,
        source_execution_phase=source_phase,
        source_execution_release_id=inner_id,
        source_execution_release_sha256=inner_sha,
        case_id=evidence.terminal_result.case_id,
        case_sha256=evidence.terminal_result.case_sha256,
        system_config=evidence.system_config,
        terminal_result=evidence.terminal_result,
        top5_projection=evidence.top5_projection,
        arm_trace=evidence.trace,
        fusion_component_traces=evidence.fusion_component_traces,
    )


def _phase_execution(
    *,
    label: str,
    source_phase: ExecutionPhase,
    authorization: MainPhaseAuthorizationReleaseV1,
    evidence: tuple[AnalysisTraceEvidenceV2, ...],
    system_configs: tuple[SystemConfigV1, ...],
    budget_at: str,
    inner_at: str,
    outer_at: str,
    component_release: MainPhaseExecutionReleaseV1 | None = None,
    locked_primary_preimage: ExecutionReleaseV2 | None = None,
    component_derivation_only: bool = False,
) -> MainPhaseExecutionReleaseV1:
    inner_id = f"campaign-integration-{label}-inner"
    inner_sha = _sha((label, "inner"))
    matrix = ExecutionMatrixV2.model_construct(
        matrix_id=f"campaign-integration-{label}-matrix",
        matrix_sha256=_sha((label, "matrix")),
        phase=source_phase,
        split_manifest=authorization.pre_budget_closure_release.frozen_case_release.split_manifest,
        system_configs=system_configs,
        cells=(),
    )
    budget = BudgetManifestV1.model_construct(
        budget_manifest_id=f"campaign-integration-{label}-budget",
        budget_manifest_sha256=_sha((label, "budget")),
        frozen_at=budget_at,
    )
    inner = ExecutionReleaseV2.model_construct(
        release_id=inner_id,
        release_sha256=inner_sha,
        execution_matrix=matrix,
        budget_manifests=(budget,),
        rankings=(),
        terminal_results=(),
        assembled_at=inner_at,
    )
    cells = tuple(
        sorted(
            (
                _cell_from_evidence(
                    item,
                    source_phase=source_phase,
                    inner_id=inner_id,
                    inner_sha=inner_sha,
                )
                for item in evidence
            ),
            key=lambda item: (item.case_id, item.analysis_role.value),
        )
    )
    digest = _sha((label, tuple(item.cell_evidence_sha256 for item in cells)))
    return MainPhaseExecutionReleaseV1.model_construct(
        release_id=deterministic_id(
            "main-phase-execution-v1", {"release_sha256": digest}
        ),
        release_sha256=digest,
        phase_authorization=authorization,
        source_execution_phase=source_phase,
        execution_release=inner,
        locked_primary_execution_preimage=locked_primary_preimage,
        component_execution_release=component_release,
        full_fusion_components=(
            ()
            if component_release is None
            else next(
                (
                    item.system_config.fusion_components
                    for item in cells
                    if item.analysis_role is AnalysisTraceRoleV2.FUSION
                ),
                (),
            )
        ),
        cells=cells,
        assembled_at=outer_at,
        component_derivation_only=component_derivation_only,
        analysis_or_gold_denominator_included=not component_derivation_only,
    )


def _success_evidence(
    *,
    case_id: str,
    case_sha: str,
    role: AnalysisTraceRoleV2,
    config: SystemConfigV1,
    label: str,
    scope: ArmExecutionScope,
    components: tuple[object, ...] = (),
) -> AnalysisTraceEvidenceV2:
    if config.llm is None:
        trace = analysis_fixture._trace(
            case_id=case_id,
            case_sha=case_sha,
            config=config,
            scope=scope,
            label=label,
            components=components,
        )
    else:
        packet = analysis_fixture._packet(case_id, case_sha, label)
        ranking = analysis_fixture._identified(
            ResearchRankingV1,
            id_field="ranking_id",
            sha_field="ranking_sha256",
            prefix="research-ranking",
            values={
                "budget_manifest_id": f"budget-{case_id}-{label}",
                "budget_manifest_sha256": _sha((case_id, label, "budget")),
                "cell_id": f"cell-{case_id}-{label}",
                "run_id": f"run-{case_id}-{label}",
                "case_id": case_id,
                "case_sha256": case_sha,
                "system_config_id": config.config_id,
                "system_config_sha256": config.config_sha256,
                "positions": (
                    RankingPositionV1(
                        selection_rank=1,
                        packet_id=packet.packet_id,
                        packet_sha256=packet.packet_sha256,
                    ),
                ),
                "underfill_reason_codes": ("SYNTHETIC_UNDERFILL",),
                "created_at": "2026-08-10T12:03:00+08:00",
            },
        )
        receipt = arm_fixture._model_receipt(
            config=config,
            ranking=ranking,
            packets=(packet,),
        )
        trace = build_arm_execution_trace(
            scope=scope,
            system_config=config,
            ranking=ranking,
            hypothesis_packets=(packet,),
            model_native_receipts=(receipt,),
            fusion_component_traces=components,
            assembled_at="2026-08-10T12:04:00+08:00",
        )
    return analysis_fixture._cell(
        role=role,
        trace=trace,
        grade=0,
        components=components,
    ).trace_evidence


def _failed_evidence(
    *,
    case_id: str,
    case_sha: str,
    role: AnalysisTraceRoleV2,
    config: SystemConfigV1,
    label: str,
) -> AnalysisTraceEvidenceV2:
    return gold_fixture._failed_trace_evidence(
        case_id=case_id,
        case_sha=case_sha,
        role=role,
        config=config,
        label=label,
    )


def _main_context(terminal_pilot_gate):
    chain = main_fixture._chain()
    private = _private_identity()
    registry = _registry(chain, private)
    pool = chain.pool.model_copy(
        update={"terminal_pilot_gate": terminal_pilot_gate}
    )
    eligibility = chain.eligibility.model_copy(
        update={"candidate_pool_release": pool}
    )
    frozen = chain.frozen.model_copy(
        update={
            "eligibility_release": eligibility,
            "expert_registry": registry,
        }
    )
    pre_budget = chain.pre_budget.model_copy(update={"frozen_case_release": frozen})
    chain = SimpleNamespace(
        **{
            **chain.__dict__,
            "pool": pool,
            "eligibility": eligibility,
            "frozen": frozen,
            "pre_budget": pre_budget,
        }
    )
    authorizations = {
        ExecutionPhase.DEVELOPMENT_ABLATIONS: _phase_authorization(
            pre_budget,
            ExecutionPhase.DEVELOPMENT_ABLATIONS,
            authorized_at="2026-08-10T08:10:00+08:00",
        ),
        ExecutionPhase.DEVELOPMENT_LOCAL_SENSITIVITY: _phase_authorization(
            pre_budget,
            ExecutionPhase.DEVELOPMENT_LOCAL_SENSITIVITY,
            authorized_at="2026-08-10T08:10:00+08:00",
        ),
        ExecutionPhase.DEVELOPMENT_FUSION: _phase_authorization(
            pre_budget,
            ExecutionPhase.DEVELOPMENT_FUSION,
            authorized_at="2026-08-10T12:19:00+08:00",
        ),
        ExecutionPhase.LOCKED_FUSION_COMPONENTS: _phase_authorization(
            pre_budget,
            ExecutionPhase.LOCKED_FUSION_COMPONENTS,
            authorized_at="2026-08-10T12:33:00+08:00",
        ),
        ExecutionPhase.LOCKED_PRIMARY: _phase_authorization(
            pre_budget,
            ExecutionPhase.LOCKED_PRIMARY,
            authorized_at="2026-08-10T12:33:00+08:00",
        ),
    }
    return chain, private, registry, frozen, pre_budget, authorizations


def _source_and_pilot() -> tuple[
    SourceCatalogCheckpointReleaseV1, PilotRoundArtifactsV3, bytes
]:
    root = Path(__file__).resolve().parents[2]
    artifact_dir = root / "artifacts/experiment/flatband-benchmark-20260809"
    checkpoint = build_source_catalog_checkpoint_release_v1(
        source_catalog_jsonl=(artifact_dir / "source_catalog.jsonl").read_bytes(),
        source_catalog_schema_json=(
            artifact_dir / "source_catalog.schema.json"
        ).read_bytes(),
        source_audit_markdown=(artifact_dir / "SOURCE_AUDIT.md").read_bytes(),
        audited_at="2026-08-09T07:00:00+08:00",
    )
    closure = pilot_fixture._authoritative_v3_r1_closure()
    inputs = closure["inputs"]
    fixture = closure["fixture"]
    blind_key = fixture["blind_key"]
    bundle = pilot_workflow._build_addressed(
        PilotRoundArtifactsV3,
        id_field="bundle_id",
        sha_field="bundle_sha256",
        prefix="pilot-round-artifacts-v3",
        values={
            "review_round": 1,
            "source_catalog_checkpoint": checkpoint,
            "private_identity_attestation": inputs[
                "private_identity_attestation"
            ],
            "public_identity_release": inputs["public_identity_release"],
            "calibration_manifest": inputs["calibration_manifest"],
            "expert_registry": inputs["expert_registry"],
            "lineage_curation_release": inputs["lineage_curation_release"],
            "lineage_assignment_curation_release": inputs[
                "lineage_assignment_curation_release"
            ],
            "frozen_case_release": inputs["frozen_case_release"],
            "pre_run_eligibility_release": inputs[
                "pre_run_eligibility_release"
            ],
            "pre_budget_closure_release": inputs["pre_budget_closure_release"],
            "execution_release": inputs["execution_release"],
            "reviewer_manifests": inputs["reviewer_manifests"],
            "private_identity_maps": inputs["private_identity_maps"],
            "evidence_excerpts": inputs["evidence_excerpts"],
            "raw_annotations": inputs["raw_annotations"],
            "adjudications": inputs["adjudications"],
            "raw_duplicate_partitions": inputs["raw_duplicate_partitions"],
            "duplicate_partition_adjudications": inputs[
                "duplicate_partition_adjudications"
            ],
            "final_gold_release": inputs["final_gold_release"],
            "agreement_release": closure["agreement"],
            "leakage_context": inputs["leakage_context"],
            "agreement_gate": closure["gate"],
            "blind_key_commitment_sha256": hashlib.sha256(blind_key).hexdigest(),
            "renderer_sha256": fixture["renderer_sha256"],
            "assembled_at": "2026-08-09T21:08:00+08:00",
        },
    )
    return checkpoint, bundle, blind_key


def _configs() -> dict[str, SystemConfigV1]:
    isolated = {
        item.value: arm_fixture._config(item)
        for item in (
            ResearchSystemId.B0,
            ResearchSystemId.E1,
            ResearchSystemId.E2_A,
            ResearchSystemId.E2_B,
            ResearchSystemId.E3,
        )
    }
    components = (
        ResearchSystemId.E1,
        ResearchSystemId.E2_A,
        ResearchSystemId.E3,
    )
    isolated.update(
        {
            "full": arm_fixture._config(
                ResearchSystemId.FUSION, fusion_components=components
            ),
            "minus-e1": arm_fixture._config(
                ResearchSystemId.FUSION,
                fusion_components=(ResearchSystemId.E2_A, ResearchSystemId.E3),
            ),
            "minus-e2": arm_fixture._config(
                ResearchSystemId.FUSION,
                fusion_components=(ResearchSystemId.E1, ResearchSystemId.E3),
            ),
            "minus-e3": arm_fixture._config(
                ResearchSystemId.FUSION,
                fusion_components=(ResearchSystemId.E1, ResearchSystemId.E2_A),
            ),
        }
    )
    return isolated


def _execution_chain(chain, authorizations, configs):
    development_cases = tuple(chain.cases[:60])
    locked_cases = tuple(chain.cases[60:])
    ablation_evidence = []
    ablation_traces: dict[tuple[str, ResearchSystemId], object] = {}
    ablation_roles = (
        (AnalysisTraceRoleV2.B0, ResearchSystemId.B0),
        (AnalysisTraceRoleV2.E1, ResearchSystemId.E1),
        (AnalysisTraceRoleV2.E2_A, ResearchSystemId.E2_A),
        (AnalysisTraceRoleV2.E2_B, ResearchSystemId.E2_B),
        (AnalysisTraceRoleV2.E3, ResearchSystemId.E3),
    )
    for case_index, case in enumerate(development_cases):
        for role, system_id in ablation_roles:
            if system_id is ResearchSystemId.B0 or case_index >= 10:
                evidence = _failed_evidence(
                    case_id=case.case_id,
                    case_sha=case.case_sha256,
                    role=role,
                    config=configs[system_id.value],
                    label=(
                        f"ablation-{case.case_id}-{system_id.value.lower()}-failed"
                    ),
                )
            else:
                evidence = _success_evidence(
                    case_id=case.case_id,
                    case_sha=case.case_sha256,
                    role=role,
                    config=configs[system_id.value],
                    label=f"ablation-{case.case_id}-{system_id.value.lower()}",
                    scope=ArmExecutionScope.DEVELOPMENT,
                )
                ablation_traces[(case.case_id, system_id)] = evidence.trace
            ablation_evidence.append(evidence)
    ablation = _phase_execution(
        label="development-ablation",
        source_phase=ExecutionPhase.DEVELOPMENT_ABLATIONS,
        authorization=authorizations[ExecutionPhase.DEVELOPMENT_ABLATIONS],
        evidence=tuple(ablation_evidence),
        system_configs=tuple(
            configs[item.value] for _, item in ablation_roles
        ),
        budget_at="2026-08-10T12:04:30+08:00",
        inner_at="2026-08-10T12:05:10+08:00",
        outer_at="2026-08-10T12:05:20+08:00",
    )

    fusion_evidence = []
    for case_index, case in enumerate(development_cases):
        b0 = _failed_evidence(
            case_id=case.case_id,
            case_sha=case.case_sha256,
            role=AnalysisTraceRoleV2.B0,
            config=configs[ResearchSystemId.B0.value],
            label=f"development-fusion-{case.case_id}-b0-failed",
        )
        if case_index < 6:
            components = tuple(
                ablation_traces[(case.case_id, system_id)]
                for system_id in (
                    ResearchSystemId.E1,
                    ResearchSystemId.E2_A,
                    ResearchSystemId.E3,
                )
            )
            full = _success_evidence(
                case_id=case.case_id,
                case_sha=case.case_sha256,
                role=AnalysisTraceRoleV2.FUSION,
                config=configs["full"],
                label=f"development-fusion-{case.case_id}-full",
                scope=ArmExecutionScope.DEVELOPMENT,
                components=components,
            )
        else:
            full = _failed_evidence(
                case_id=case.case_id,
                case_sha=case.case_sha256,
                role=AnalysisTraceRoleV2.FUSION,
                config=configs["full"],
                label=f"development-fusion-{case.case_id}-full-failed",
            )
        fusion_evidence.extend((b0, full))
    fusion = _phase_execution(
        label="development-fusion",
        source_phase=ExecutionPhase.DEVELOPMENT_FUSION,
        authorization=authorizations[ExecutionPhase.DEVELOPMENT_FUSION],
        evidence=tuple(fusion_evidence),
        system_configs=(configs[ResearchSystemId.B0.value], configs["full"]),
        budget_at="2026-08-10T12:19:30+08:00",
        inner_at="2026-08-10T12:20:10+08:00",
        outer_at="2026-08-10T12:20:20+08:00",
        component_release=ablation,
    )

    locked_component_evidence = []
    locked_traces: dict[tuple[str, ResearchSystemId], object] = {}
    component_systems = (
        ResearchSystemId.E1,
        ResearchSystemId.E2_A,
        ResearchSystemId.E2_B,
        ResearchSystemId.E3,
    )
    direct_role = {
        ResearchSystemId.E1: AnalysisTraceRoleV2.E1,
        ResearchSystemId.E2_A: AnalysisTraceRoleV2.E2_A,
        ResearchSystemId.E2_B: AnalysisTraceRoleV2.E2_B,
        ResearchSystemId.E3: AnalysisTraceRoleV2.E3,
    }
    for case_index, case in enumerate(locked_cases):
        for system_id in component_systems:
            if case_index < 4:
                item = _success_evidence(
                    case_id=case.case_id,
                    case_sha=case.case_sha256,
                    role=direct_role[system_id],
                    config=configs[system_id.value],
                    label=(
                        f"locked-component-{case.case_id}-{system_id.value.lower()}"
                    ),
                    scope=ArmExecutionScope.LOCKED,
                )
                locked_traces[(case.case_id, system_id)] = item.trace
            else:
                item = _failed_evidence(
                    case_id=case.case_id,
                    case_sha=case.case_sha256,
                    role=direct_role[system_id],
                    config=configs[system_id.value],
                    label=(
                        f"locked-component-{case.case_id}-{system_id.value.lower()}-failed"
                    ),
                )
            locked_component_evidence.append(item)
    locked_components = _phase_execution(
        label="locked-components",
        source_phase=ExecutionPhase.LOCKED_FUSION_COMPONENTS,
        authorization=authorizations[ExecutionPhase.LOCKED_FUSION_COMPONENTS],
        evidence=tuple(locked_component_evidence),
        system_configs=tuple(configs[item.value] for item in component_systems),
        budget_at="2026-08-10T12:35:30+08:00",
        inner_at="2026-08-10T12:36:30+08:00",
        outer_at="2026-08-10T12:37:00+08:00",
        component_derivation_only=True,
    )

    primary_evidence = []
    minus_evidence = {
        "minus-e1": [],
        "minus-e2": [],
        "minus-e3": [],
    }
    minus_spec = {
        "minus-e1": (
            AnalysisTraceRoleV2.FUSION_MINUS_E1,
            (ResearchSystemId.E2_A, ResearchSystemId.E3),
        ),
        "minus-e2": (
            AnalysisTraceRoleV2.FUSION_MINUS_E2,
            (ResearchSystemId.E1, ResearchSystemId.E3),
        ),
        "minus-e3": (
            AnalysisTraceRoleV2.FUSION_MINUS_E3,
            (ResearchSystemId.E1, ResearchSystemId.E2_A),
        ),
    }
    for case_index, case in enumerate(locked_cases):
        b0 = _failed_evidence(
            case_id=case.case_id,
            case_sha=case.case_sha256,
            role=AnalysisTraceRoleV2.B0,
            config=configs[ResearchSystemId.B0.value],
            label=f"locked-{case.case_id}-b0-failed",
        )
        if case_index < 4:
            full_components = tuple(
                locked_traces[(case.case_id, item)]
                for item in (
                    ResearchSystemId.E1,
                    ResearchSystemId.E2_A,
                    ResearchSystemId.E3,
                )
            )
            full = _success_evidence(
                case_id=case.case_id,
                case_sha=case.case_sha256,
                role=AnalysisTraceRoleV2.FUSION,
                config=configs["full"],
                label=f"locked-{case.case_id}-full",
                scope=ArmExecutionScope.LOCKED,
                components=full_components,
            )
        else:
            full = _failed_evidence(
                case_id=case.case_id,
                case_sha=case.case_sha256,
                role=AnalysisTraceRoleV2.FUSION,
                config=configs["full"],
                label=f"locked-{case.case_id}-full-failed",
            )
        primary_evidence.extend((b0, full))
        for label, (role, systems) in minus_spec.items():
            if case_index < 4:
                components = tuple(
                    locked_traces[(case.case_id, item)] for item in systems
                )
                item = _success_evidence(
                    case_id=case.case_id,
                    case_sha=case.case_sha256,
                    role=role,
                    config=configs[label],
                    label=f"locked-{case.case_id}-{label}",
                    scope=ArmExecutionScope.LOCKED,
                    components=components,
                )
            else:
                item = _failed_evidence(
                    case_id=case.case_id,
                    case_sha=case.case_sha256,
                    role=role,
                    config=configs[label],
                    label=f"locked-{case.case_id}-{label}-failed",
                )
            minus_evidence[label].append(item)
    primary = _phase_execution(
        label="locked-primary",
        source_phase=ExecutionPhase.LOCKED_PRIMARY,
        authorization=authorizations[ExecutionPhase.LOCKED_PRIMARY],
        evidence=tuple(primary_evidence),
        system_configs=(configs[ResearchSystemId.B0.value], configs["full"]),
        budget_at="2026-08-10T12:38:00+08:00",
        inner_at="2026-08-10T12:39:00+08:00",
        outer_at="2026-08-10T12:40:00+08:00",
        component_release=locked_components,
    )
    phase_by_minus = {
        "minus-e1": ExecutionPhase.LOCKED_FUSION_MINUS_E1,
        "minus-e2": ExecutionPhase.LOCKED_FUSION_MINUS_E2,
        "minus-e3": ExecutionPhase.LOCKED_FUSION_MINUS_E3,
    }
    minus_releases = {
        label: _phase_execution(
            label=f"locked-{label}",
            source_phase=phase_by_minus[label],
            authorization=authorizations[ExecutionPhase.LOCKED_PRIMARY],
            evidence=tuple(values),
            system_configs=(configs[label],),
            budget_at="2026-08-10T12:38:00+08:00",
            inner_at="2026-08-10T12:39:00+08:00",
            outer_at="2026-08-10T12:40:00+08:00",
            component_release=locked_components,
            locked_primary_preimage=primary.execution_release,
        )
        for label, values in minus_evidence.items()
    }
    return ablation, fusion, locked_components, primary, minus_releases


def _assignment(
    phase: ExecutionPhase,
    registry: ExpertStudyRegistryV2,
    private: PrivateExpertIdentityCustodianAttestationV2,
    *,
    sealed_at: str,
):
    binding = {item.expert_id: item for item in private.bindings}

    def identity(expert_id: str) -> MainExpertIdentityCommitmentV1:
        return MainExpertIdentityCommitmentV1(
            expert_id=expert_id,
            natural_person_commitment_sha256=(
                binding[expert_id].natural_person_commitment_sha256
            ),
            annotation_key_commitment_sha256=hashlib.sha256(
                EXPERT_KEYS[expert_id]
            ).hexdigest(),
        )

    return build_main_expert_assignment(
        phase=phase,
        expert_registry=MainExpertRegistryRefV1(
            registry_id=registry.registry_id,
            registry_sha256=registry.registry_sha256,
        ),
        reviewers=(identity("reviewer-a"), identity("reviewer-b")),
        adjudicators=(identity("adjudicator-a"),),
        sealed_at=sealed_at,
    )


def _gold_analysis(
    *,
    phase: ExecutionPhase,
    releases: tuple[MainPhaseExecutionReleaseV1, ...],
    assignment,
    grades: dict[str, int],
    frozen,
    blinding_at: str,
    submitted_at: str,
    gold_at: str,
    verified_at: str,
    analysis_at: str,
):
    traces = tuple(
        item.arm_trace
        for release in releases
        for item in release.cells
        if item.arm_trace is not None
    )
    manifests, identity_maps = build_main_reviewer_materials(
        phase=phase,
        arm_traces=traces,
        expert_assignment=assignment,
        ephemeral_blinding_key=GOLD_KEYS[phase],
        renderer_sha256=RENDERER_SHA,
        sealed_at=blinding_at,
    )
    raw_labels = tuple(
        build_main_raw_label(
            manifest,
            identity_map,
            blinded_unit_id=entry.blinded_unit_id,
            annotation_guide_sha256=GUIDE_SHA,
            status=MainGoldStatus.ASSESSABLE,
            relevance_grade=grades[entry.contributions[0].cell_id],
            evidence_gain=int(grades[entry.contributions[0].cell_id] > 0),
            submitted_at=submitted_at,
            reviewer_annotation_key=EXPERT_KEYS[identity_map.reviewer_id],
        )
        for manifest, identity_map in zip(manifests, identity_maps)
        for entry in identity_map.entries
    )
    raw_partitions = tuple(
        build_main_raw_duplicate_partition(
            manifest,
            identity_map,
            case_id=case_id,
            case_sha256=next(
                item.case_sha256
                for item in identity_map.entries
                if item.case_id == case_id
            ),
            blinded_clusters=tuple(
                (item.blinded_unit_id,)
                for item in identity_map.entries
                if item.case_id == case_id
            ),
            annotation_guide_sha256=GUIDE_SHA,
            submitted_at=submitted_at,
            reviewer_annotation_key=EXPERT_KEYS[identity_map.reviewer_id],
        )
        for manifest, identity_map in zip(manifests, identity_maps)
        for case_id in sorted({item.case_id for item in identity_map.entries})
    )
    gold = build_main_gold_release(
        phase=phase,
        phase_execution_releases=releases,
        expert_assignment=assignment,
        expert_annotation_keys=EXPERT_KEYS,
        ephemeral_blinding_key=GOLD_KEYS[phase],
        renderer_sha256=RENDERER_SHA,
        blinding_sealed_at=blinding_at,
        annotation_guide_sha256=GUIDE_SHA,
        raw_labels=raw_labels,
        raw_duplicate_partitions=raw_partitions,
        released_at=gold_at,
    )
    verifier = build_main_gold_formal_verifier_attestation(
        gold,
        phase_execution_releases=releases,
        expert_annotation_keys=EXPERT_KEYS,
        ephemeral_blinding_key=GOLD_KEYS[phase],
        verified_at=verified_at,
    )
    evidence = tuple(
        build_analysis_trace_evidence_v2(
            role=item.analysis_role,
            system_config=item.system_config,
            terminal_result=item.terminal_result,
            top5_projection=item.top5_projection,
            trace=item.arm_trace,
            fusion_component_traces=item.fusion_component_traces,
        )
        for release in releases
        for item in release.cells
    )
    cells = tuple(
        build_analysis_cell_from_main_gold_v1(
            gold,
            verifier,
            trace_evidence=item,
        )
        for item in evidence
    )
    split_by_case = {item.case_id: item for item in frozen.split_manifest.cases}
    component_by_case = {
        case_id: component.component_id
        for component in frozen.leakage_release.components
        for case_id in component.case_ids
    }
    case_ids = tuple(
        sorted({item.terminal_result.case_id for item in evidence})
    )
    cases = tuple(
        AnalysisCaseBindingV2(
            case_id=case_id,
            case_sha256=split_by_case[case_id].case_sha256,
            split=split_by_case[case_id].split,
            leakage_component_id=component_by_case[case_id],
        )
        for case_id in case_ids
    )
    analysis = build_analysis_input_release_v2(
        execution_phase=phase,
        split_context_ref=AnalysisArtifactRefV2(
            artifact_type=AnalysisArtifactTypeV2.SPLIT_CONTEXT,
            artifact_id=frozen.split_manifest.manifest_id,
            artifact_sha256=frozen.split_manifest.manifest_sha256,
        ),
        leakage_context_ref=AnalysisArtifactRefV2(
            artifact_type=AnalysisArtifactTypeV2.LEAKAGE_CONTEXT,
            artifact_id=frozen.leakage_release.release_id,
            artifact_sha256=frozen.leakage_release.release_sha256,
        ),
        cases=cases,
        cells=cells,
        assembled_at=analysis_at,
    )
    return gold, verifier, analysis


def _grade_map(releases: tuple[MainPhaseExecutionReleaseV1, ...]) -> dict[str, int]:
    grades = {
        AnalysisTraceRoleV2.B0: 0,
        AnalysisTraceRoleV2.E1: 3,
        AnalysisTraceRoleV2.E2_A: 2,
        AnalysisTraceRoleV2.E2_B: 2,
        AnalysisTraceRoleV2.E3: 3,
        AnalysisTraceRoleV2.FUSION: 3,
        AnalysisTraceRoleV2.FUSION_MINUS_E1: 2,
        AnalysisTraceRoleV2.FUSION_MINUS_E2: 2,
        AnalysisTraceRoleV2.FUSION_MINUS_E3: 2,
    }
    return {
        item.terminal_result.cell_id: grades[item.analysis_role]
        for release in releases
        for item in release.cells
        if item.arm_trace is not None
    }


def test_complete_campaign_assembler_and_exact_replay_smoke(
    upstream_scientific_fixture_seam: None,
) -> None:
    source_checkpoint, pilot, pilot_key = _source_and_pilot()
    chain, private, registry, frozen, pre_budget, authorizations = _main_context(
        pilot.agreement_gate
    )
    configs = _configs()
    ablation, fusion, locked_components, primary, minus = _execution_chain(
        chain, authorizations, configs
    )

    ablation_assignment = _assignment(
        ExecutionPhase.DEVELOPMENT_ABLATIONS,
        registry,
        private,
        sealed_at="2026-08-10T12:05:30+08:00",
    )
    ablation_gold, ablation_verifier, ablation_analysis = _gold_analysis(
        phase=ExecutionPhase.DEVELOPMENT_ABLATIONS,
        releases=(ablation,),
        assignment=ablation_assignment,
        grades=_grade_map((ablation,)),
        frozen=frozen,
        blinding_at="2026-08-10T12:06:00+08:00",
        submitted_at="2026-08-10T12:07:00+08:00",
        gold_at="2026-08-10T12:10:00+08:00",
        verified_at="2026-08-10T12:11:00+08:00",
        analysis_at="2026-08-10T12:15:00+08:00",
    )
    ablation_attestation = build_formal_verifier_attestation_v1(
        ablation_analysis,
        verified_payload_kind=VerifiedPayloadKind.DEVELOPMENT_ABLATION_METRIC_ROWS,
        attested_at="2026-08-10T12:16:00+08:00",
    )
    promotion = lifecycle.build_development_promotion_release_v1(
        analysis_input_ref=analysis_input_ref_v2(ablation_analysis),
        formal_verifier_attestation=ablation_attestation,
        metric_rows=derive_development_metric_rows_v2(ablation_analysis),
        assembled_at="2026-08-10T12:17:00+08:00",
    )
    assert promotion.promoted_components == (
        ResearchSystemId.E1,
        ResearchSystemId.E2_A,
        ResearchSystemId.E3,
    )
    component_configurations = tuple(
        SystemConfigurationRefV1(
            system_id=system_id,
            configuration_ref=typed_artifact_ref_v1(
                LifecycleArtifactType.SYSTEM_CONFIGURATION,
                configs[system_id.value].config_id,
                configs[system_id.value].config_sha256,
            ),
        )
        for system_id in (ResearchSystemId.B0, *promotion.promoted_components)
    )
    fusion_configuration = lifecycle.build_fusion_configuration_release_v1(
        promotion_release=promotion,
        component_configurations=component_configurations,
        frozen_at="2026-08-10T12:18:00+08:00",
    )

    fusion_assignment = _assignment(
        ExecutionPhase.DEVELOPMENT_FUSION,
        registry,
        private,
        sealed_at="2026-08-10T12:20:30+08:00",
    )
    fusion_gold, fusion_verifier, fusion_analysis = _gold_analysis(
        phase=ExecutionPhase.DEVELOPMENT_FUSION,
        releases=(fusion,),
        assignment=fusion_assignment,
        grades=_grade_map((fusion,)),
        frozen=frozen,
        blinding_at="2026-08-10T12:21:00+08:00",
        submitted_at="2026-08-10T12:22:00+08:00",
        gold_at="2026-08-10T12:25:00+08:00",
        verified_at="2026-08-10T12:26:00+08:00",
        analysis_at="2026-08-10T12:30:00+08:00",
    )
    fusion_attestation = build_formal_verifier_attestation_v1(
        fusion_analysis,
        verified_payload_kind=VerifiedPayloadKind.DEVELOPMENT_FUSION_METRIC_ROWS,
        attested_at="2026-08-10T12:31:00+08:00",
    )
    fusion_gate = lifecycle.build_development_fusion_gate_release_v1(
        promotion_release=promotion,
        fusion_configuration=fusion_configuration,
        analysis_input_ref=analysis_input_ref_v2(fusion_analysis),
        formal_verifier_attestation=fusion_attestation,
        metric_rows=derive_development_metric_rows_v2(fusion_analysis),
        evaluated_at="2026-08-10T12:32:00+08:00",
    )
    assert fusion_gate.passed is True

    plan_arms = (
        LockedExecutionPlannedArmV1(
            role=AnalysisTraceRoleV2.B0,
            source_execution_phase=ExecutionPhase.LOCKED_PRIMARY,
            system_config=configs[ResearchSystemId.B0.value],
        ),
        LockedExecutionPlannedArmV1(
            role=AnalysisTraceRoleV2.FUSION,
            source_execution_phase=ExecutionPhase.LOCKED_PRIMARY,
            system_config=configs["full"],
        ),
        LockedExecutionPlannedArmV1(
            role=AnalysisTraceRoleV2.FUSION_MINUS_E1,
            source_execution_phase=ExecutionPhase.LOCKED_FUSION_MINUS_E1,
            system_config=configs["minus-e1"],
        ),
        LockedExecutionPlannedArmV1(
            role=AnalysisTraceRoleV2.FUSION_MINUS_E2,
            source_execution_phase=ExecutionPhase.LOCKED_FUSION_MINUS_E2,
            system_config=configs["minus-e2"],
        ),
        LockedExecutionPlannedArmV1(
            role=AnalysisTraceRoleV2.FUSION_MINUS_E3,
            source_execution_phase=ExecutionPhase.LOCKED_FUSION_MINUS_E3,
            system_config=configs["minus-e3"],
        ),
    )
    locked_plan = build_locked_execution_plan_v1(
        locked_component_authorization=authorizations[
            ExecutionPhase.LOCKED_FUSION_COMPONENTS
        ],
        locked_primary_authorization=authorizations[ExecutionPhase.LOCKED_PRIMARY],
        fusion_configuration=fusion_configuration,
        component_system_configs=tuple(
            configs[item.value]
            for item in (
                ResearchSystemId.E1,
                ResearchSystemId.E2_A,
                ResearchSystemId.E2_B,
                ResearchSystemId.E3,
            )
        ),
        analysis_arms=plan_arms,
        frozen_at="2026-08-10T12:34:00+08:00",
    )
    label_seal = lifecycle.build_locked_label_seal_v1(
        split_manifest_ref=typed_artifact_ref_v1(
            LifecycleArtifactType.BENCHMARK_SPLIT_MANIFEST,
            frozen.split_manifest.manifest_id,
            frozen.split_manifest.manifest_sha256,
        ),
        private_label_custody_ref=typed_artifact_ref_v1(
            LifecycleArtifactType.PRIVATE_LOCKED_LABEL_CUSTODY,
            "campaign-integration-private-locked-labels",
            _sha("campaign-integration-private-locked-labels"),
        ),
        locked_case_universe_sha256=_sha(tuple(item.case_id for item in chain.cases[60:])),
        sealed_label_commitment_sha256=_sha("campaign-integration-locked-labels"),
        sealed_at="2026-08-10T12:00:00+08:00",
    )
    locked_authorization = lifecycle.build_locked_test_authorization_release_v1(
        promotion_release=promotion,
        fusion_configuration=fusion_configuration,
        fusion_gate=fusion_gate,
        locked_label_seal=label_seal,
        analysis_code_ref=typed_artifact_ref_v1(
            LifecycleArtifactType.ANALYSIS_CODE_RELEASE,
            "campaign-integration-analysis-code",
            _sha("campaign-integration-analysis-code"),
        ),
        analysis_environment_ref=typed_artifact_ref_v1(
            LifecycleArtifactType.ANALYSIS_ENVIRONMENT_RELEASE,
            "campaign-integration-analysis-environment",
            _sha("campaign-integration-analysis-environment"),
        ),
        statistical_analysis_plan_ref=typed_artifact_ref_v1(
            LifecycleArtifactType.STATISTICAL_ANALYSIS_PLAN,
            "campaign-integration-statistical-plan",
            _sha("campaign-integration-statistical-plan"),
        ),
        locked_execution_ref=typed_artifact_ref_v1(
            LifecycleArtifactType.LOCKED_EXECUTION_RELEASE,
            locked_plan.plan_id,
            locked_plan.plan_sha256,
        ),
        authorized_at="2026-08-10T12:35:00+08:00",
    )
    genesis = lifecycle.build_locked_unseal_ledger_genesis_v1(
        authorization=locked_authorization,
        locked_label_seal=label_seal,
    )
    unseal = lifecycle.build_locked_annotation_unseal_release_v1(
        authorization=locked_authorization,
        promotion_release=promotion,
        fusion_configuration=fusion_configuration,
        fusion_gate=fusion_gate,
        locked_label_seal=label_seal,
        prior_ledger=genesis,
        unsealed_at="2026-08-10T12:42:00+08:00",
    )
    committed = lifecycle.append_locked_unseal_ledger_v1(
        prior_ledger=genesis,
        unseal=unseal,
    )

    locked_releases = (
        primary,
        minus["minus-e1"],
        minus["minus-e2"],
        minus["minus-e3"],
    )
    locked_assignment = _assignment(
        ExecutionPhase.LOCKED_PRIMARY,
        registry,
        private,
        sealed_at="2026-08-10T12:42:30+08:00",
    )
    locked_gold, locked_verifier, locked_analysis = _gold_analysis(
        phase=ExecutionPhase.LOCKED_PRIMARY,
        releases=locked_releases,
        assignment=locked_assignment,
        grades=_grade_map(locked_releases),
        frozen=frozen,
        blinding_at="2026-08-10T12:43:00+08:00",
        submitted_at="2026-08-10T12:44:00+08:00",
        gold_at="2026-08-10T12:47:00+08:00",
        verified_at="2026-08-10T12:48:00+08:00",
        analysis_at="2026-08-10T12:50:00+08:00",
    )
    locked_attestation = build_formal_verifier_attestation_v1(
        locked_analysis,
        verified_payload_kind=VerifiedPayloadKind.LOCKED_RESULT_FAMILY,
        attested_at="2026-08-10T12:51:00+08:00",
    )
    primary_result, secondary_results = derive_locked_result_family_v2(
        locked_analysis
    )
    claim_kind = (
        lifecycle.ClaimKind.BENCHMARK_PRIMARY_PASSED
        if primary_result.metric_gate_passed
        else lifecycle.ClaimKind.BENCHMARK_PRIMARY_NOT_PASSED
    )
    claim_support = lifecycle.build_claim_support_release_v1(
        promotion_release=promotion,
        fusion_configuration=fusion_configuration,
        authorization=locked_authorization,
        unseal=unseal,
        analysis_input_ref=analysis_input_ref_v2(locked_analysis),
        formal_verifier_attestation=locked_attestation,
        primary_result=primary_result,
        secondary_results=secondary_results,
        protocol_deviations=(),
        claims=(lifecycle.supported_claim_v1(claim_kind),),
        assembled_at="2026-08-10T12:52:00+08:00",
    )
    public_projection = lifecycle.build_public_benchmark_projection_v1(
        claim_support=claim_support,
        promotion_release=promotion,
        fusion_configuration=fusion_configuration,
        protocol_deviations=(),
        limitation_codes=tuple(PublicLimitationCodeV1),
    )

    authority_keys = {
        role: _sha(("campaign-authority-key", role.value)).encode("ascii")
        for role in ReleaseAuthorityRoleV1
    }
    authority_policy = lifecycle.build_release_authority_policy_v1(
        authorities={
            role: (f"campaign-authority-{role.value.lower()}", key)
            for role, key in authority_keys.items()
        },
        frozen_at="2026-08-09T23:00:00+08:00",
    )
    identity_key = authority_keys[ReleaseAuthorityRoleV1.SCIENTIFIC_REVIEWER_IDENTITY]
    reviewer_decision_keys = {
        f"scientific-reviewer-{index}": _sha(("reviewer-decision", index)).encode(
            "ascii"
        )
        for index in (1, 2)
    }
    reviewer_identities = tuple(
        lifecycle.build_scientific_reviewer_identity_attestation_v1(
            authority_policy=authority_policy,
            authority_key=identity_key,
            reviewer_id=f"scientific-reviewer-{index}",
            natural_person_commitment_sha256=_sha(("scientific-person", index)),
            private_identity_evidence_sha256=_sha(
                ("scientific-private-evidence", index)
            ),
            reviewer_decision_key=reviewer_decision_keys[
                f"scientific-reviewer-{index}"
            ],
            issued_at=f"2026-08-10T12:53:0{index}+08:00",
        )
        for index in (1, 2)
    )
    reviewer_attestations = tuple(
        lifecycle.build_scientific_reviewer_attestation_v1(
            reviewer_identity_attestation=identity,
            authority_policy=authority_policy,
            reviewer_identity_authority_key=identity_key,
            reviewer_decision_key=reviewer_decision_keys[identity.reviewer_id],
            claim_support=claim_support,
            public_projection=public_projection,
            decision=ScientificReviewerDecision.APPROVE,
            critical_issue_count=0,
            findings=(),
            required_limitation_codes=(),
            reviewed_at=f"2026-08-10T12:54:0{index}+08:00",
        )
        for index, identity in enumerate(reviewer_identities, start=1)
    )
    scientific_review = lifecycle.build_scientific_review_release_v1(
        claim_support=claim_support,
        public_projection=public_projection,
        reviewer_attestations=reviewer_attestations,
        authority_policy=authority_policy,
        reviewer_identity_authority_key=identity_key,
        reviewer_decision_keys=reviewer_decision_keys,
        reviewed_at="2026-08-10T12:55:00+08:00",
    )
    control_role = {
        ReleaseControlKindV1.LICENSE: ReleaseAuthorityRoleV1.LICENSE_RELEASE,
        ReleaseControlKindV1.PRIVACY: ReleaseAuthorityRoleV1.PRIVACY_RELEASE,
        ReleaseControlKindV1.CUSTODY: ReleaseAuthorityRoleV1.CUSTODY_RELEASE,
    }
    controls = tuple(
        lifecycle.build_release_control_attestation_v1(
            authority_policy=authority_policy,
            authority_key=authority_keys[control_role[kind]],
            control_kind=kind,
            public_projection=public_projection,
            evidence_artifact_sha256=_sha(("release-control", kind.value)),
            issued_at="2026-08-10T12:56:00+08:00",
        )
        for kind in sorted(ReleaseControlKindV1, key=lambda item: item.value)
    )
    public_authorization = lifecycle.build_public_release_authorization_v1(
        claim_support=claim_support,
        public_projection=public_projection,
        scientific_review=scientific_review,
        authority_policy=authority_policy,
        release_control_attestations=controls,
        authority_keys=authority_keys,
        reviewer_identity_authority_key=identity_key,
        reviewer_decision_keys=reviewer_decision_keys,
        authorized_at="2026-08-10T12:57:00+08:00",
    )
    public_result = lifecycle.build_public_benchmark_result_release_v1(
        authorization=public_authorization,
        scientific_review=scientific_review,
        public_projection=public_projection,
        claim_support=claim_support,
        authority_keys=authority_keys,
        reviewer_identity_authority_key=identity_key,
        reviewer_decision_keys=reviewer_decision_keys,
        released_at="2026-08-10T12:58:00+08:00",
    )

    gold_expert_keys = {
        phase: EXPERT_KEYS
        for phase in (
            ExecutionPhase.DEVELOPMENT_ABLATIONS,
            ExecutionPhase.DEVELOPMENT_FUSION,
            ExecutionPhase.LOCKED_PRIMARY,
        )
    }
    release = assemble_flatband_campaign_release_v1(
        source_catalog_checkpoint=source_checkpoint,
        pilot_rounds=(pilot,),
        main_sampling_policy=chain.sampling,
        main_candidate_pool=chain.pool,
        main_eligibility=chain.eligibility,
        main_frozen_cases=frozen,
        main_pre_budget_closure=pre_budget,
        main_private_identity_attestation=private,
        release_authority_policy=authority_policy,
        development_ablation_authorization=authorizations[
            ExecutionPhase.DEVELOPMENT_ABLATIONS
        ],
        local_sensitivity_authorization=authorizations[
            ExecutionPhase.DEVELOPMENT_LOCAL_SENSITIVITY
        ],
        development_fusion_authorization=authorizations[
            ExecutionPhase.DEVELOPMENT_FUSION
        ],
        locked_fusion_components_authorization=authorizations[
            ExecutionPhase.LOCKED_FUSION_COMPONENTS
        ],
        locked_primary_authorization=authorizations[ExecutionPhase.LOCKED_PRIMARY],
        development_ablation_execution=ablation,
        local_sensitivity_not_run_at="2026-08-10T08:11:00+08:00",
        development_fusion_execution=fusion,
        locked_fusion_components_execution=locked_components,
        locked_primary_execution=primary,
        locked_minus_e1_execution=minus["minus-e1"],
        locked_minus_e2_execution=minus["minus-e2"],
        locked_minus_e3_execution=minus["minus-e3"],
        development_ablation_gold=ablation_gold,
        development_ablation_gold_verifier=ablation_verifier,
        development_ablation_analysis=ablation_analysis,
        development_ablation_analysis_attestation=ablation_attestation,
        development_promotion=promotion,
        fusion_configuration=fusion_configuration,
        development_fusion_gold=fusion_gold,
        development_fusion_gold_verifier=fusion_verifier,
        development_fusion_analysis=fusion_analysis,
        development_fusion_analysis_attestation=fusion_attestation,
        development_fusion_gate=fusion_gate,
        locked_label_seal=label_seal,
        locked_execution_plan=locked_plan,
        locked_test_authorization=locked_authorization,
        locked_unseal_genesis_ledger=genesis,
        locked_unseal=unseal,
        locked_unseal_committed_ledger=committed,
        locked_gold=locked_gold,
        locked_gold_verifier=locked_verifier,
        locked_analysis=locked_analysis,
        locked_analysis_attestation=locked_attestation,
        protocol_deviations=(),
        claim_support=claim_support,
        public_projection=public_projection,
        scientific_reviewer_identity_attestations=reviewer_identities,
        scientific_reviewer_attestations=reviewer_attestations,
        scientific_review=scientific_review,
        release_control_attestations=controls,
        public_release_authorization=public_authorization,
        public_result=public_result,
        assembled_at="2026-08-10T13:00:00+08:00",
        pilot_blinding_keys={1: pilot_key},
        main_gold_blinding_keys=GOLD_KEYS,
        main_gold_expert_annotation_keys=gold_expert_keys,
        release_authority_keys=authority_keys,
        scientific_reviewer_decision_keys=reviewer_decision_keys,
    )

    # Regression for the typed-ref/full-preimage join: these different model
    # types are intentionally unequal, while their exact address must join.
    assert ablation_assignment.expert_registry != registry
    _assert_identity_registry_join_v1(release)
    assert len(
        (
            release.development_ablation_execution,
            release.development_fusion_execution,
            release.locked_fusion_components_execution,
            release.locked_primary_execution,
            release.locked_minus_e1_execution,
            release.locked_minus_e2_execution,
            release.locked_minus_e3_execution,
        )
    ) == 7
    assert any(
        item.arm_trace is None
        for item in release.development_ablation_execution.cells
    )
    assert (
        release.development_ablation_gold_verifier.expert_annotation_signatures_verified
    )
    assert release.public_result.scientific_conclusion is False

    assert_flatband_campaign_release_exact_v1(
        release,
        pilot_blinding_keys={1: pilot_key},
        main_gold_blinding_keys=GOLD_KEYS,
        main_gold_expert_annotation_keys=gold_expert_keys,
        release_authority_keys=authority_keys,
        scientific_reviewer_decision_keys=reviewer_decision_keys,
    )
