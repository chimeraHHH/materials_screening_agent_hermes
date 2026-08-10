from __future__ import annotations

import inspect
from functools import lru_cache
from typing import Any

import pytest
from pydantic import ValidationError

from material_agent.research.flatband_analysis import (
    build_formal_pilot_agreement_gate,
    build_formal_pilot_agreement_release,
)

from material_agent.research.flatband_cases import (
    FrozenCaseReleaseV3,
    PilotPreBudgetClosureReleaseV3,
    PreRunEligibilityReleaseV3,
)
from material_agent.research.flatband_execution import (
    ExecutionReleaseV2,
    ExecutionReleaseV3,
)
from material_agent.research.flatband_leakage import (
    MechanismLineageAssignmentCurationReleaseV3,
    MechanismLineageCurationReleaseV3,
)
from material_agent.research.flatband_pilot import (
    assert_formal_pilot_closure_v3,
)


SHA_A = "a" * 64
SHA_B = "b" * 64
SHA_C = "c" * 64
SHA_D = "d" * 64
SHA_E = "e" * 64


def _shallow_authoritative_roots() -> dict[str, object]:
    """Address-only roots for testing the non-accepting fast preflight."""

    frozen = FrozenCaseReleaseV3.model_construct(
        release_id="frozen-v3-a", release_sha256=SHA_A
    )
    eligibility = PreRunEligibilityReleaseV3.model_construct(
        release_id="eligibility-v3-a", release_sha256=SHA_B
    )
    curation = MechanismLineageCurationReleaseV3.model_construct(
        release_id="lineage-curation-v3-a", release_sha256=SHA_C
    )
    assignment_curation = (
        MechanismLineageAssignmentCurationReleaseV3.model_construct(
            release_id="lineage-assignment-curation-v3-a",
            release_sha256=SHA_D,
        )
    )
    pre_budget = PilotPreBudgetClosureReleaseV3.model_construct(
        release_id="pre-budget-v3-a",
        release_sha256=SHA_E,
        frozen_case_release=frozen,
        lineage_curation_release=curation,
        lineage_assignment_curation_release_id=(
            assignment_curation.release_id
        ),
        lineage_assignment_curation_release_sha256=(
            assignment_curation.release_sha256
        ),
    )
    execution = ExecutionReleaseV3.model_construct(
        frozen_case_release_id=frozen.release_id,
        frozen_case_release_sha256=frozen.release_sha256,
        pre_run_eligibility_release_id=eligibility.release_id,
        pre_run_eligibility_release_sha256=eligibility.release_sha256,
        pre_budget_closure_release_id=pre_budget.release_id,
        pre_budget_closure_release_sha256=pre_budget.release_sha256,
    )
    return {
        "frozen_case_release": frozen,
        "pre_run_eligibility_release": eligibility,
        "pre_budget_closure_release": pre_budget,
        "execution_release": execution,
        "lineage_curation_release": curation,
        "lineage_assignment_curation_release": assignment_curation,
    }


def _shallow_required_call(**updates: object) -> dict[str, object]:
    values = {
        name: object()
        for name, parameter in inspect.signature(
            assert_formal_pilot_closure_v3
        ).parameters.items()
        if parameter.default is inspect.Parameter.empty
    }
    values.update(_shallow_authoritative_roots())
    values.update(updates)
    return values


@lru_cache(maxsize=1)
def _authoritative_v3_r1_closure() -> dict[str, Any]:
    """Reuse one synthetic authoritative graph from Gold through the Gate."""

    import test_flatband_research_gold as gold_fixture

    fixture = gold_fixture._formal_v2_gold_fixture(authoritative_v3=True)
    frozen = fixture["frozen"]
    eligibility = fixture["eligibility"]
    agreement = build_formal_pilot_agreement_release(
        split_manifest=frozen.split_manifest,
        leakage_release=fixture["leakage"],
        lineage_curation_release=fixture["lineage_curation"],
        frozen_case_release=frozen,
        pre_run_eligibility_release=eligibility,
        execution_release=fixture["execution"],
        expert_registry=fixture["registry"],
        reviewer_manifests=fixture["manifests"],
        private_identity_maps=fixture["private_maps"],
        evidence_excerpts=fixture["excerpts"],
        blind_key=fixture["blind_key"],
        renderer_sha256=fixture["renderer_sha256"],
        raw_annotations=fixture["raw_annotations"],
        assembled_at="2026-08-09T21:06:00+08:00",
    )
    gate = build_formal_pilot_agreement_gate(
        agreement_release=agreement,
        leakage_context=fixture["leakage_context"],
        lineage_curation_release=fixture["lineage_curation"],
        evaluated_at="2026-08-09T21:07:00+08:00",
    )
    inputs = {
        "private_identity_attestation": fixture["private"],
        "public_identity_release": (
            eligibility.assignment_release.public_identity_release
        ),
        "calibration_manifest": (
            eligibility.assignment_release.calibration_manifest
        ),
        "expert_registry": fixture["registry"],
        "lineage_curation_release": fixture["lineage_curation"],
        "lineage_assignment_curation_release": (
            fixture["lineage_assignment_curation"]
        ),
        "frozen_case_release": frozen,
        "pre_run_eligibility_release": eligibility,
        "pre_budget_closure_release": fixture["pre_budget_closure"],
        "execution_release": fixture["execution"],
        "reviewer_manifests": fixture["manifests"],
        "private_identity_maps": fixture["private_maps"],
        "evidence_excerpts": fixture["excerpts"],
        "blind_key": fixture["blind_key"],
        "renderer_sha256": fixture["renderer_sha256"],
        "raw_annotations": fixture["raw_annotations"],
        "adjudications": fixture["adjudications"],
        "raw_duplicate_partitions": fixture["raw_duplicate_partitions"],
        "duplicate_partition_adjudications": (
            fixture["duplicate_partition_adjudications"]
        ),
        "final_gold_release": fixture["gold_release"],
        "agreement_release": agreement,
        "leakage_context": fixture["leakage_context"],
        "agreement_gate": gate,
    }
    return {"fixture": fixture, "agreement": agreement, "gate": gate, "inputs": inputs}


def test_top_level_pilot_closure_has_no_optional_current_chain_instance() -> None:
    signature = inspect.signature(assert_formal_pilot_closure_v3)
    optional = {
        "prior_r1_agreement_release",
        "prior_r1_gate",
        "prior_r1_leakage_context",
    }
    assert {
        name
        for name, parameter in signature.parameters.items()
        if parameter.default is not inspect.Parameter.empty
    } == optional
    with pytest.raises(TypeError, match="required keyword-only argument"):
        assert_formal_pilot_closure_v3()


def test_top_level_rejects_legacy_execution_before_deep_replay() -> None:
    legacy = ExecutionReleaseV2.model_construct()
    with pytest.raises(
        ValueError, match="execution release must use flatband-execution-release-v3"
    ):
        assert_formal_pilot_closure_v3(
            **_shallow_required_call(execution_release=legacy)
        )


@pytest.mark.parametrize(
    ("field", "replacement", "message"),
    (
        (
            "frozen_case_release",
            FrozenCaseReleaseV3.model_construct(
                release_id="foreign-frozen-v3",
                release_sha256="f" * 64,
            ),
            "foreign FrozenCaseReleaseV3",
        ),
        (
            "pre_budget_closure_release",
            PilotPreBudgetClosureReleaseV3.model_construct(
                release_id="foreign-pre-budget-v3",
                release_sha256="f" * 64,
            ),
            "foreign pre-budget closure",
        ),
        (
            "lineage_curation_release",
            MechanismLineageCurationReleaseV3.model_construct(
                release_id="foreign-lineage-curation-v3",
                release_sha256="f" * 64,
            ),
            "foreign lineage curation",
        ),
        (
            "lineage_assignment_curation_release",
            MechanismLineageAssignmentCurationReleaseV3.model_construct(
                release_id="foreign-lineage-assignment-curation-v3",
                release_sha256="f" * 64,
            ),
            "foreign lineage-assignment curation",
        ),
    ),
)
def test_top_level_rejects_foreign_authoritative_root_before_deep_replay(
    field: str, replacement: object, message: str
) -> None:
    with pytest.raises(ValueError, match=message):
        assert_formal_pilot_closure_v3(
            **_shallow_required_call(**{field: replacement})
        )


def test_top_level_exactly_replays_synthetic_authoritative_v3_r1_closure() -> None:
    closure = _authoritative_v3_r1_closure()
    fixture = closure["fixture"]

    assert fixture["execution"].schema_version == "flatband-execution-release-v3"
    assert closure["agreement"].review_round == 1
    assert closure["gate"].review_round == 1
    assert len(fixture["gold_release"].judgments) == 30
    assert_formal_pilot_closure_v3(**closure["inputs"])


def test_top_level_rejects_model_copy_foreign_calibration_derivative_root() -> None:
    inputs = _authoritative_v3_r1_closure()["inputs"]
    calibration = inputs["calibration_manifest"]
    screening = calibration.derivative_screening_release
    foreign_screening = screening.model_copy(
        update={
            "release_id": "foreign-calibration-derivative-release-v3",
            "release_sha256": "f" * 64,
        }
    )
    foreign_calibration = calibration.model_copy(
        update={"derivative_screening_release": foreign_screening}
    )

    with pytest.raises(
        (ValidationError, ValueError),
        match="derivative|calibration.*SHA-256|identity",
    ):
        assert_formal_pilot_closure_v3(
            **{**inputs, "calibration_manifest": foreign_calibration}
        )


def test_top_level_rejects_model_copy_foreign_structure_union_root() -> None:
    inputs = _authoritative_v3_r1_closure()["inputs"]
    pre_budget = inputs["pre_budget_closure_release"]
    union = pre_budget.structure_union_replay_release
    foreign_union = union.model_copy(
        update={
            "union_release_id": "foreign-structure-union-release-v2",
            "union_release_sha256": "f" * 64,
        }
    )
    foreign_pre_budget = pre_budget.model_copy(
        update={"structure_union_replay_release": foreign_union}
    )
    foreign_execution = inputs["execution_release"].model_copy(
        update={"pre_budget_closure_release": foreign_pre_budget}
    )

    with pytest.raises(
        (ValidationError, ValueError),
        match="structure union|union.*SHA-256|union release|pre-budget",
    ):
        assert_formal_pilot_closure_v3(
            **{
                **inputs,
                "pre_budget_closure_release": foreign_pre_budget,
                "execution_release": foreign_execution,
            }
        )
