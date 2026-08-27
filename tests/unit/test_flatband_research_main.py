"""Lightweight structural tests for Main custody contracts.

The synthetic objects in this module deliberately bypass expensive scientific
replay with monkeypatches.  They test contract wiring and fail-closed surfaces;
they are not a Main120 scientific positive fixture or benchmark result.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest
from pydantic import ValidationError

import material_agent.research.flatband_main as main_module
from material_agent.inspiration.models import canonical_sha256, deterministic_id
from material_agent.research.flatband_analysis import (
    FormalPilotAgreementGateReleaseV1,
    PilotAgreementGateDecision,
)
from material_agent.research.flatband_contracts import (
    SOURCE_CATALOG_V1_SHA256,
    BenchmarkSplit,
    BenchmarkSplitManifestV2,
    Dimensionality,
    FlatBandBenchmarkCaseV1,
    MechanismFamily,
    SourceRecordRefV1,
    SplitCaseRefV2,
    SplitManifestKind,
    TargetBandClass,
)
from material_agent.research.flatband_execution import (
    ExecutionPhase,
    ResearchSystemId,
)
from material_agent.research.flatband_experts import ExpertStudyRegistryV2
from material_agent.research.flatband_leakage import (
    LeakageComponentReleaseV3,
    LeakageComponentV1,
    LeakageRoundClosureContextV3,
    LeakageUnsplitCaseUniverseContextV3,
    MechanismLineageCurationReleaseV3,
)
from material_agent.research.flatband_main import (
    MainEligibilityEvidenceRefV1,
    MainEligibilityRawAuditV1,
    MainEligibilityStatusV1,
    MainPhaseAuthorizationReleaseV1,
    MainPreBudgetClosureReleaseV1,
    assert_main_candidate_pool_exact_v1,
    assert_main_capacity_policy_exact_v1,
    assert_main_eligibility_exact_v1,
    assert_main_frozen_case_exact_v1,
    assert_main_phase_authorization_exact_v1,
    assert_main_pre_budget_closure_exact_v1,
    assert_main_sampling_policy_exact_v1,
    build_main_candidate_pool_release_v1,
    build_main_capacity_policy_v1,
    build_main_eligibility_raw_audit_v1,
    build_main_eligibility_release_v1,
    build_main_frozen_case_release_v1,
    build_main_phase_authorization_release_v1,
    build_main_pre_budget_closure_release_v1,
    build_main_sampling_policy_release_v1,
)


def _sha(value: object) -> str:
    return canonical_sha256(value)


@pytest.fixture(autouse=True)
def _contract_structure_only(monkeypatch: pytest.MonkeyPatch) -> None:
    """Avoid presenting synthetic structure wiring as scientific replay."""

    monkeypatch.setattr(main_module, "_revalidate", lambda value, _type: value)
    def structural_address(
        model_type: type[Any],
        *,
        id_field: str,
        sha_field: str,
        prefix: str,
        values: dict[str, object],
    ) -> Any:
        draft = model_type.model_construct(**values)
        digest = canonical_sha256(
            draft.model_dump(mode="python", exclude={id_field, sha_field})
        )
        value = model_type.model_construct(
            **values,
            **{
                sha_field: digest,
                id_field: deterministic_id(prefix, {sha_field: digest}),
            },
        )
        if model_type.__module__ == main_module.__name__:
            for validator_name in (
                "validate_policy",
                "validate_release",
                "validate_audit",
                "validate_adjudication",
                "validate_decision",
            ):
                validator = getattr(value, validator_name, None)
                if validator is not None:
                    return validator()
        return value

    monkeypatch.setattr(main_module, "_build_addressed", structural_address)
    monkeypatch.setattr(
        main_module,
        "structure_grouping_case_universe_sha256_v2",
        lambda cases: _sha(tuple((item.case_id, item.case_sha256) for item in cases)),
    )
    monkeypatch.setattr(main_module, "assert_main_leakage_v3", lambda **_kwargs: None)
    monkeypatch.setattr(main_module, "assert_pilot_leakage_v3", lambda **_kwargs: None)
    monkeypatch.setattr(
        main_module, "assert_cross_round_leakage_disjoint_v3", lambda **_kwargs: None
    )
    monkeypatch.setattr(
        main_module, "assert_expert_registry_covers_split_v2", lambda **_kwargs: None
    )


def _source(index: int, *, namespace: str = "main") -> SourceRecordRefV1:
    return SourceRecordRefV1.model_construct(
        source_id="crossref",
        source_record_id=f"{namespace}-source-{index:03d}",
        canonical_url=f"https://doi.org/10.9999/{namespace}-{index:03d}",
        source_version="synthetic-contract-only",
        license_expression="CC0-1.0",
        accessed_at="2026-08-10T00:00:00+08:00",
        raw_sha256=_sha((namespace, index, "raw")),
        public_redistribution_allowed=True,
    )


def _case(index: int, *, namespace: str = "main") -> FlatBandBenchmarkCaseV1:
    source = _source(index, namespace=namespace)
    return FlatBandBenchmarkCaseV1.model_construct(
        case_id=f"{namespace}-case-{index:03d}",
        case_sha256=_sha((namespace, index, "case")),
        source_catalog_sha256=SOURCE_CATALOG_V1_SHA256,
        parent_label=f"Synthetic {namespace} case {index}",
        formula=f"X{index + 1}Y",
        structure_sha256=_sha((namespace, index, "structure")),
        source_records=(source,),
        target_class=(TargetBandClass.FB100 if index % 2 == 0 else TargetBandClass.NB300),
        target_fermi_distance_max_e_v=1.0,
        dimensionality=(Dimensionality.TWO_D if index % 2 == 0 else Dimensionality.THREE_D),
        frozen_request="Synthetic contract fixture; no scientific conclusion",
        frozen_requirement_sha256=_sha((namespace, index, "requirement")),
        hard_constraints=(),
        soft_preferences=(),
        forbidden_transformations=(),
        seed_evidence=(),
        primary_mechanism_stratum=MechanismFamily.LATTICE_INTERFERENCE,
        leakage_group_ids=(f"{namespace}-group-{index // 3:03d}",),
        public_release_allowed=False,
        scientific_conclusion=False,
    )


def _split(cases: tuple[FlatBandBenchmarkCaseV1, ...]) -> BenchmarkSplitManifestV2:
    refs = []
    for index, case in enumerate(cases):
        split = (
            BenchmarkSplit.DEVELOPMENT
            if index < 60
            else BenchmarkSplit.LOCKED_IID
            if index < 90
            else BenchmarkSplit.LOCKED_OOD
        )
        refs.append(
            SplitCaseRefV2.model_construct(
                case_id=case.case_id,
                case_sha256=case.case_sha256,
                split=split,
                target_class=case.target_class,
                dimensionality=case.dimensionality,
                primary_mechanism_stratum=case.primary_mechanism_stratum,
                independence_group_ids=case.leakage_group_ids,
                holdout_memberships=(),
            )
        )
    return BenchmarkSplitManifestV2.model_construct(
        manifest_id="main-split",
        manifest_sha256=_sha("main-split"),
        manifest_kind=SplitManifestKind.MAIN_120,
        source_catalog_sha256=SOURCE_CATALOG_V1_SHA256,
        split_seed=91,
        cases=tuple(refs),
        ood_holdout_families=(),
    )


def _partition(values: tuple[str, ...], count: int) -> tuple[tuple[str, ...], ...]:
    return tuple(
        tuple(values[position::count])
        for position in range(count)
        if values[position::count]
    )


def _leakage(
    split: BenchmarkSplitManifestV2,
    *,
    development_components: int = 20,
    created_at: str = "2026-08-10T00:05:00+08:00",
    release_id: str = "main-leakage",
) -> LeakageComponentReleaseV3:
    by_split = {
        value: tuple(item.case_id for item in split.cases if item.split is value)
        for value in (
            BenchmarkSplit.DEVELOPMENT,
            BenchmarkSplit.LOCKED_IID,
            BenchmarkSplit.LOCKED_OOD,
        )
    }
    components = []
    for benchmark_split, count in (
        (BenchmarkSplit.DEVELOPMENT, development_components),
        (BenchmarkSplit.LOCKED_IID, 10),
        (BenchmarkSplit.LOCKED_OOD, 10),
    ):
        for case_ids in _partition(by_split[benchmark_split], count):
            ordered = tuple(sorted(case_ids))
            components.append(
                LeakageComponentV1(
                    component_id=deterministic_id(
                        "leakage-component", {"case_ids": ordered}
                    ),
                    case_ids=ordered,
                )
            )
    return LeakageComponentReleaseV3.model_construct(
        release_id=release_id,
        release_sha256=_sha(release_id),
        split_manifest_id=split.manifest_id,
        split_manifest_sha256=split.manifest_sha256,
        components=tuple(sorted(components, key=lambda item: item.component_id)),
        created_at=created_at,
    )


def _pilot_context() -> LeakageRoundClosureContextV3:
    cases = tuple(_case(index, namespace="pilot") for index in range(30))
    split = BenchmarkSplitManifestV2.model_construct(
        manifest_id="pilot-split",
        manifest_sha256=_sha("pilot-split"),
        manifest_kind=SplitManifestKind.PILOT_R1,
        source_catalog_sha256=SOURCE_CATALOG_V1_SHA256,
        split_seed=73,
        cases=(),
        ood_holdout_families=(),
    )
    release = LeakageComponentReleaseV3.model_construct(
        release_id="pilot-leakage",
        release_sha256=_sha("pilot-leakage"),
        created_at="2026-08-10T00:00:30+08:00",
    )
    return LeakageRoundClosureContextV3.model_construct(
        cases=cases, split_manifest=split, release=release
    )


def _curation() -> MechanismLineageCurationReleaseV3:
    return MechanismLineageCurationReleaseV3.model_construct(
        release_id="lineage-curation",
        release_sha256=_sha("lineage-curation"),
        assembled_at="2026-08-09T23:59:00+08:00",
    )


def _gate(
    pilot: LeakageRoundClosureContextV3,
    curation: MechanismLineageCurationReleaseV3,
    *,
    decision: PilotAgreementGateDecision = PilotAgreementGateDecision.R1_PASS_MAIN_ALLOWED,
) -> FormalPilotAgreementGateReleaseV1:
    passing = decision is PilotAgreementGateDecision.R1_PASS_MAIN_ALLOWED
    return main_module._build_addressed(
        FormalPilotAgreementGateReleaseV1,
        id_field="gate_id",
        sha_field="gate_sha256",
        prefix="pilot-agreement-gate-v1",
        values={
            "agreement_release_id": "pilot-agreement",
            "agreement_release_sha256": _sha("pilot-agreement"),
            "leakage_release_id": pilot.release.release_id,
            "leakage_release_sha256": pilot.release.release_sha256,
            "lineage_curation_release_id": curation.release_id,
            "lineage_curation_release_sha256": curation.release_sha256,
            "lineage_assignment_curation_release_id": "assignment-curation",
            "lineage_assignment_curation_release_sha256": _sha(
                "assignment-curation"
            ),
            "execution_phase": ExecutionPhase.PILOT_R1,
            "review_round": 1,
            "annotation_guide_sha256": _sha("annotation-guide"),
            "alpha": 0.9 if passing else 0.5,
            "bootstrap_lower": 0.8 if passing else 0.4,
            "bootstrap_upper": 0.95 if passing else 0.6,
            "valid_bootstrap_replicates": 50_000,
            "undefined_bootstrap_replicates": 0,
            "total_units": 60,
            "rated_units": 60,
            "paired_system_packet_invalid_units": 0,
            "unilateral_system_packet_invalid_units": 0,
            "case_invalid_units": 0,
            "exact_assessability_agreement_rate": 1.0,
            "exact_grade_agreement_rate": 1.0,
            "round_fail_closed": False,
            "decision": decision,
            "evaluated_at": "2026-08-10T00:01:00+08:00",
        },
    )


def _registry(split: BenchmarkSplitManifestV2) -> ExpertStudyRegistryV2:
    return ExpertStudyRegistryV2.model_construct(
        registry_id="main-expert-registry",
        registry_sha256=_sha("main-expert-registry"),
        split_manifest_id=split.manifest_id,
        split_manifest_sha256=split.manifest_sha256,
        calibration_manifest_id="calibration-manifest",
        calibration_manifest_sha256=_sha("calibration-manifest"),
        annotation_guide_sha256=_sha("annotation-guide"),
        registered_at="2026-08-10T00:05:10+08:00",
    )


def _calibration_context() -> LeakageUnsplitCaseUniverseContextV3:
    return LeakageUnsplitCaseUniverseContextV3.model_construct(
        universe_id="calibration-universe",
        source_artifact_id="calibration-manifest",
        source_artifact_sha256=_sha("calibration-manifest"),
        cases=(_case(0, namespace="calibration"),),
    )


def _raw_audits(
    cases: tuple[FlatBandBenchmarkCaseV1, ...],
) -> tuple[MainEligibilityRawAuditV1, ...]:
    audits = []
    for case in cases:
        source = case.source_records[0]
        evidence = MainEligibilityEvidenceRefV1(
            source_id=source.source_id,
            source_record_id=source.source_record_id,
            source_record_raw_sha256=source.raw_sha256,
            artifact_id=f"eligibility-evidence-{case.case_id}",
            artifact_sha256=_sha((case.case_id, "eligibility-evidence")),
        )
        for auditor_id in ("auditor-a", "auditor-b"):
            audits.append(
                build_main_eligibility_raw_audit_v1(
                    case=case,
                    auditor_id=auditor_id,
                    auditor_natural_person_commitment_sha256=_sha(
                        (auditor_id, "natural-person")
                    ),
                    evidence_refs=(evidence,),
                    status=MainEligibilityStatusV1.ELIGIBLE,
                    audited_at="2026-08-10T00:03:00+08:00",
                )
            )
    return tuple(audits)


@dataclass(frozen=True)
class _Chain:
    cases: tuple[FlatBandBenchmarkCaseV1, ...]
    pilot: LeakageRoundClosureContextV3
    sampling: Any
    pool: Any
    eligibility: Any
    split: BenchmarkSplitManifestV2
    leakage: LeakageComponentReleaseV3
    frozen: Any
    capacity: Any
    pre_budget: MainPreBudgetClosureReleaseV1
    development: MainPhaseAuthorizationReleaseV1
    local_sensitivity: MainPhaseAuthorizationReleaseV1
    fusion: MainPhaseAuthorizationReleaseV1
    locked: MainPhaseAuthorizationReleaseV1


def _chain(*, development_components: int = 20) -> _Chain:
    cases = tuple(_case(index) for index in range(120))
    pilot = _pilot_context()
    curation = _curation()
    sampling = build_main_sampling_policy_release_v1(
        split_seed=91, frozen_at="2026-08-10T00:00:00+08:00"
    )
    pool = build_main_candidate_pool_release_v1(
        sampling_policy_release=sampling,
        terminal_pilot_gate=_gate(pilot, curation),
        lineage_curation_release=curation,
        cases=cases,
        private_structure_artifact_id="main-structure-artifact",
        private_structure_artifact_sha256=_sha("main-structure-artifact"),
        sealed_at="2026-08-10T00:02:00+08:00",
    )
    eligibility = build_main_eligibility_release_v1(
        candidate_pool_release=pool,
        eligibility_policy_sha256=_sha("main-eligibility-policy"),
        raw_audits=_raw_audits(cases),
        sealed_at="2026-08-10T00:04:00+08:00",
    )
    split = _split(cases)
    leakage = _leakage(split, development_components=development_components)
    frozen = build_main_frozen_case_release_v1(
        eligibility_release=eligibility,
        split_manifest=split,
        leakage_release=leakage,
        expert_registry=_registry(split),
        frozen_at="2026-08-10T00:06:00+08:00",
    )
    calibration = _calibration_context()
    capacity = build_main_capacity_policy_v1(
        authorized_union_candidate_count=151,
        authorized_union_member_count=3,
        verifier_implementation_sha256=_sha("main-union-verifier-code"),
        verifier_runtime_environment_sha256=_sha("main-union-runtime"),
        frozen_at="2026-08-10T00:07:00+08:00",
    )
    main_context = LeakageRoundClosureContextV3.model_construct(
        cases=cases, split_manifest=split, release=leakage
    )
    structure_union = main_module.build_main_structure_union_release_v1(
        capacity_policy=capacity,
        calibration_context=calibration,
        pilot_contexts=(pilot,),
        main_context=main_context,
        private_verifier_evidence_artifact_id="main-union-private-evidence",
        private_verifier_evidence_artifact_sha256=_sha(
            "main-union-private-evidence"
        ),
        output_root_sha256=_sha("main-union-output"),
        verified_at="2026-08-10T00:07:10+08:00",
        created_at="2026-08-10T00:07:20+08:00",
    )
    pre_budget = build_main_pre_budget_closure_release_v1(
        frozen_case_release=frozen,
        calibration_leakage_context=calibration,
        pilot_leakage_contexts=(pilot,),
        main_leakage_context=main_context,
        capacity_policy=capacity,
        structure_union_release=structure_union,
        sealed_at="2026-08-10T00:08:00+08:00",
    )
    development = build_main_phase_authorization_release_v1(
        pre_budget_closure_release=pre_budget,
        execution_phase=ExecutionPhase.DEVELOPMENT_ABLATIONS,
        authorized_at="2026-08-10T00:09:00+08:00",
    )
    local_sensitivity = build_main_phase_authorization_release_v1(
        pre_budget_closure_release=pre_budget,
        execution_phase=ExecutionPhase.DEVELOPMENT_LOCAL_SENSITIVITY,
        authorized_at="2026-08-10T00:09:00+08:00",
    )
    fusion = build_main_phase_authorization_release_v1(
        pre_budget_closure_release=pre_budget,
        execution_phase=ExecutionPhase.DEVELOPMENT_FUSION,
        authorized_at="2026-08-10T00:09:00+08:00",
    )
    locked = build_main_phase_authorization_release_v1(
        pre_budget_closure_release=pre_budget,
        execution_phase=ExecutionPhase.LOCKED_PRIMARY,
        authorized_at="2026-08-10T00:09:00+08:00",
    )
    return _Chain(
        cases=cases,
        pilot=pilot,
        sampling=sampling,
        pool=pool,
        eligibility=eligibility,
        split=split,
        leakage=leakage,
        frozen=frozen,
        capacity=capacity,
        pre_budget=pre_budget,
        development=development,
        local_sensitivity=local_sensitivity,
        fusion=fusion,
        locked=locked,
    )


def _readdress(
    value: Any,
    *,
    id_field: str,
    sha_field: str,
    prefix: str,
    **changes: object,
) -> Any:
    values = {
        name: getattr(value, name)
        for name in type(value).model_fields
        if name not in {id_field, sha_field}
    }
    values.update(changes)
    return main_module._build_addressed(
        type(value),
        id_field=id_field,
        sha_field=sha_field,
        prefix=prefix,
        values=values,
    )


def test_structural_main_chain_replays_without_claiming_scientific_positive() -> None:
    chain = _chain()
    assert_main_sampling_policy_exact_v1(chain.sampling)
    assert_main_candidate_pool_exact_v1(chain.pool)
    assert_main_eligibility_exact_v1(chain.eligibility)
    assert_main_frozen_case_exact_v1(chain.frozen)
    assert_main_capacity_policy_exact_v1(chain.capacity)
    assert_main_pre_budget_closure_exact_v1(chain.pre_budget)
    assert_main_phase_authorization_exact_v1(chain.development)
    assert_main_phase_authorization_exact_v1(chain.local_sensitivity)
    assert_main_phase_authorization_exact_v1(chain.fusion)
    assert_main_phase_authorization_exact_v1(chain.locked)
    assert chain.development.authorized_system_ids == tuple(
        sorted(
            (
                ResearchSystemId.B0,
                ResearchSystemId.E1,
                ResearchSystemId.E2_A,
                ResearchSystemId.E2_B,
                ResearchSystemId.E3,
            ),
            key=lambda item: item.value,
        )
    )
    assert chain.local_sensitivity.authorized_system_ids == (
        ResearchSystemId.E1_LOCAL,
    )
    assert chain.fusion.authorized_system_ids == (
        ResearchSystemId.B0,
        ResearchSystemId.FUSION,
    )
    assert chain.locked.authorized_system_ids == (
        ResearchSystemId.B0,
        ResearchSystemId.FUSION,
    )


def test_main_rejects_foreign_nonpassing_pilot_gate() -> None:
    cases = tuple(_case(index) for index in range(120))
    pilot = _pilot_context()
    curation = _curation()
    sampling = build_main_sampling_policy_release_v1(
        split_seed=91, frozen_at="2026-08-10T00:00:00+08:00"
    )
    with pytest.raises((ValidationError, ValueError), match="PASS Pilot Gate"):
        build_main_candidate_pool_release_v1(
            sampling_policy_release=sampling,
            terminal_pilot_gate=_gate(
                pilot,
                curation,
                decision=PilotAgreementGateDecision.STOP_R1_BELOW_0_667,
            ),
            lineage_curation_release=curation,
            cases=cases,
            private_structure_artifact_id="main-structure-artifact",
            private_structure_artifact_sha256=_sha("main-structure-artifact"),
            sealed_at="2026-08-10T00:02:00+08:00",
        )


def test_main_rejects_passing_gate_from_foreign_curation() -> None:
    cases = tuple(_case(index) for index in range(120))
    pilot = _pilot_context()
    curation = _curation()
    foreign_curation = MechanismLineageCurationReleaseV3.model_construct(
        release_id="foreign-curation",
        release_sha256=_sha("foreign-curation"),
        assembled_at="2026-08-09T23:59:00+08:00",
    )
    sampling = build_main_sampling_policy_release_v1(
        split_seed=91, frozen_at="2026-08-10T00:00:00+08:00"
    )
    with pytest.raises(ValueError, match="foreign Pilot Gate"):
        build_main_candidate_pool_release_v1(
            sampling_policy_release=sampling,
            terminal_pilot_gate=_gate(pilot, foreign_curation),
            lineage_curation_release=curation,
            cases=cases,
            private_structure_artifact_id="main-structure-artifact",
            private_structure_artifact_sha256=_sha("main-structure-artifact"),
            sealed_at="2026-08-10T00:02:00+08:00",
        )


def test_main_rejects_candidate_case_omission() -> None:
    cases = tuple(_case(index) for index in range(120))
    pilot = _pilot_context()
    curation = _curation()
    sampling = build_main_sampling_policy_release_v1(
        split_seed=91, frozen_at="2026-08-10T00:00:00+08:00"
    )
    with pytest.raises((ValidationError, ValueError), match="exactly 120"):
        build_main_candidate_pool_release_v1(
            sampling_policy_release=sampling,
            terminal_pilot_gate=_gate(pilot, curation),
            lineage_curation_release=curation,
            cases=cases[:-1],
            private_structure_artifact_id="main-structure-artifact",
            private_structure_artifact_sha256=_sha("main-structure-artifact"),
            sealed_at="2026-08-10T00:02:00+08:00",
        )


def test_main_rejects_foreign_eligibility_evidence() -> None:
    chain = _chain()
    audits = list(chain.eligibility.raw_audits)
    original = audits[0]
    foreign_ref = MainEligibilityEvidenceRefV1(
        source_id="crossref",
        source_record_id="foreign-record",
        source_record_raw_sha256=_sha("foreign-record"),
        artifact_id="foreign-evidence",
        artifact_sha256=_sha("foreign-evidence"),
    )
    audits[0] = _readdress(
        original,
        id_field="audit_id",
        sha_field="audit_sha256",
        prefix="main-eligibility-raw-audit-v1",
        evidence_refs=(foreign_ref,),
    )
    with pytest.raises(ValueError, match="exact subset of case sources"):
        build_main_eligibility_release_v1(
            candidate_pool_release=chain.pool,
            eligibility_policy_sha256=chain.eligibility.eligibility_policy_sha256,
            raw_audits=tuple(audits),
            sealed_at=chain.eligibility.sealed_at,
        )


def test_main_leakage_component_minimum_is_a_model_surface() -> None:
    with pytest.raises((ValidationError, ValueError), match="20/10/10"):
        _chain(development_components=19)


def test_main_rejects_unaddressed_structure_union_substitution() -> None:
    chain = _chain()
    forged_union = chain.pre_budget.structure_union_release.model_copy(
        update={"output_root_sha256": _sha("foreign-union-output")}
    )
    drifted = chain.pre_budget.model_copy(
        update={"structure_union_release": forged_union}
    )
    with pytest.raises((ValidationError, ValueError)):
        assert_main_pre_budget_closure_exact_v1(
            drifted,
        )


def test_main_rejects_e1_local_in_locked_primary() -> None:
    chain = _chain()
    with pytest.raises(ValueError, match="phase systems|E1-local"):
        _readdress(
            chain.locked,
            id_field="release_id",
            sha_field="release_sha256",
            prefix="main-phase-authorization-v1",
            authorized_system_ids=(ResearchSystemId.E1_LOCAL,),
        )


def test_main_rejects_phase_authorization_at_prebudget_seal() -> None:
    chain = _chain()
    with pytest.raises(ValueError, match="does not follow pre-budget"):
        build_main_phase_authorization_release_v1(
            pre_budget_closure_release=chain.pre_budget,
            execution_phase=ExecutionPhase.DEVELOPMENT_ABLATIONS,
            authorized_at=chain.pre_budget.sealed_at,
        )
