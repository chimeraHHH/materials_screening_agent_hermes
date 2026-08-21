from __future__ import annotations

import warnings
from pathlib import Path

import pytest
from pydantic import ValidationError
from pymatgen.core import Composition, Lattice, Structure
from pymatgen.io.cif import CifWriter

from material_agent.inspiration.operator_planning import (
    CompileRegisteredOperationArgsV2,
    OperatorPlanningToolState,
    compile_registered_structure_operation_plans,
    execution_request_from_compiled_operation_plan,
)
from material_agent.inspiration.research_graph import (
    DatabaseCandidateV1,
    DatabaseSourceRecordV1,
)
from material_agent.retrieval.storage import LocalArtifactStore
from material_agent.softchem import (
    DEFAULT_SOFTCHEM_OPERATOR_REGISTRY_V2,
    SMACT_WORKER_LOCK_SHA256,
    ElementStoichiometryV1,
    SmactPriorDecision,
    SmactPriorGateResultV1,
    execute_registered_structure_operation,
    smact_prior_policy_sha256,
)
from material_agent.softchem.operations import (
    HomogeneousStrainParametersV1,
    RegisteredIntercalationParametersV1,
)


class _PassingSmact:
    def evaluate(self, composition, *, input_formula, policy):
        parsed = (
            composition
            if isinstance(composition, Composition)
            else Composition(composition)
        )
        values = parsed.reduced_composition.get_el_amt_dict()
        return SmactPriorGateResultV1(
            decision=SmactPriorDecision.PASS,
            policy_id=policy.policy_id,
            policy_sha256=smact_prior_policy_sha256(policy),
            backend_version=policy.smact_version,
            worker_lock_sha256=SMACT_WORKER_LOCK_SHA256,
            input_formula=input_formula,
            proposed_formula=parsed.reduced_formula,
            reduced_stoichiometry=tuple(
                ElementStoichiometryV1(element=element, amount=int(values[element]))
                for element in sorted(values)
            ),
            smact_valid=True,
            reason_codes=("CHARGE_NEUTRALITY_AND_PAULING_PASS",),
        )


def _cif_bytes(structure: Structure) -> bytes:
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        text = str(CifWriter(structure, symprec=None, significant_figures=12))
    text = text.replace("\r\n", "\n")
    return (text if text.endswith("\n") else text + "\n").encode()


def _candidate(
    store: LocalArtifactStore,
    *,
    cif: bytes,
    suffix: str = "4",
) -> DatabaseCandidateV1:
    structure = Structure.from_str(cif.decode(), fmt="cif")
    structure_ref = store.write_bytes(
        f"database/parent-{suffix}.cif", cif, "chemical/x-cif", immutable=True
    )
    raw = b'{"source":"fixture"}'
    raw_ref = store.write_bytes(
        f"database/parent-{suffix}.json", raw, "application/json", immutable=True
    )
    source = DatabaseSourceRecordV1(
        source_database="c2db",
        source_material_id=f"fixture-{suffix}",
        source_database_version="fixture-v1",
        query_fingerprint=suffix * 64,
        canonical_structure_id="str_" + suffix * 24,
        structure_artifact_uri=structure_ref.uri,
        structure_artifact_sha256=structure_ref.sha256,
        raw_response_artifact_uri=raw_ref.uri,
        raw_response_artifact_sha256=raw_ref.sha256,
        license="test fixture",
    )
    return DatabaseCandidateV1(
        database_candidate_id="db-candidate-" + suffix * 24,
        source_database="c2db",
        source_material_id=f"fixture-{suffix}",
        canonical_structure_id="str_" + suffix * 24,
        source_records=(source,),
        formula=structure.composition.reduced_formula,
        elements=tuple(
            sorted(
                str(item) for item in structure.composition.element_composition.elements
            )
        ),
        transition_metals=("Ti",),
        dimensionality=2,
        dimensionality_status="RESOLVED",
        connected_transition_metal_sublattice_proxy=1.0,
        connectivity_status="RESOLVED_PROXY",
        structure_artifact_uri=structure_ref.uri,
        structure_artifact_sha256=structure_ref.sha256,
        raw_response_artifact_uri=raw_ref.uri,
        raw_response_artifact_sha256=raw_ref.sha256,
    )


def _tis2_cif() -> bytes:
    return (
        Path(__file__).parents[2]
        / "src/material_agent/inspiration/catalogs/flat_band_parent_catalog_v1"
        / "tis2-1t-vacuum-monolayer.cif"
    ).read_bytes()


def test_v2_registry_has_typed_specs_priors_and_validators() -> None:
    registry = DEFAULT_SOFTCHEM_OPERATOR_REGISTRY_V2
    assert len(registry.operators) == 5
    for spec in registry.operators:
        assert spec.parameter_schema_id
        assert spec.prior_ids
        assert spec.validator_ids
        assert spec.accepts_free_coordinates is False
        assert spec.accepts_arbitrary_species is False
        assert spec.accepts_code is False


def test_parameter_models_reject_unregistered_matrix_and_free_intercalation_site() -> (
    None
):
    with pytest.raises(ValidationError, match="registered rule"):
        HomogeneousStrainParametersV1(
            rule_id="biaxial-tensile-2pct-v1",
            deformation_matrix=((1.9, 0.0, 0.0), (0.0, 1.9, 0.0), (0.0, 0.0, 1.0)),
        )
    with pytest.raises(ValidationError, match="registered site"):
        RegisteredIntercalationParametersV1(
            rule_id="li-vdw-hollow-a-v1",
            intercalant="Li",
            site_rule_id="hex-hollow-a-v1",
            insertion_frac_coords=(0.25, 0.25, 0.5),
        )


def test_deepseek_compiler_emits_and_executes_bounded_strain_on_real_cif(
    tmp_path: Path,
) -> None:
    store = LocalArtifactStore(tmp_path / "artifacts")
    cif = _tis2_cif()
    candidate = _candidate(store, cif=cif)
    state = OperatorPlanningToolState(
        store=store, database_candidates_snapshot=lambda: (candidate,)
    )
    compiled = state.as_tool().handler(
        CompileRegisteredOperationArgsV2(
            candidate_id="candidate-strained-tis2",
            database_candidate_id=candidate.database_candidate_id,
            operation_kind="HOMOGENEOUS_STRAIN",
            rule_id="biaxial-tensile-2pct-v1",
        )
    )
    assert compiled["status"] == "COMPILED"
    plan = state.audit_snapshot().plans[0]
    assert plan.operator_id == "APPLY_HOMOGENEOUS_STRAIN_V1"
    parent = Structure.from_str(cif.decode(), fmt="cif")
    result = execute_registered_structure_operation(
        execution_request_from_compiled_operation_plan(plan),
        parent_structure=parent,
        parent_artifact_bytes=cif,
    )
    assert result.plan.status == "STRUCTURE_VALID"
    assert result.output_structure is not None
    assert result.output_structure.composition == parent.composition
    assert result.output_structure.lattice.a == pytest.approx(parent.lattice.a * 1.02)


def test_vacancy_prior_rejects_excessive_complete_class(tmp_path: Path) -> None:
    store = LocalArtifactStore(tmp_path / "artifacts")
    cif = _tis2_cif()
    candidate = _candidate(store, cif=cif)
    state = OperatorPlanningToolState(
        store=store, database_candidates_snapshot=lambda: (candidate,)
    )
    compiled = state.as_tool().handler(
        CompileRegisteredOperationArgsV2(
            candidate_id="candidate-s-vacancy",
            database_candidate_id=candidate.database_candidate_id,
            operation_kind="VACANCY",
            rule_id="vacancy-chalcogen-class-v1",
        )
    )
    assert compiled["status"] == "REJECTED"
    assert compiled["reason_code"] == "OPERATION_PRIOR_REJECTED"
    parent = Structure.from_str(cif.decode(), fmt="cif")
    plan = compile_registered_structure_operation_plans(
        candidate_id="candidate-s-vacancy",
        database_candidate=candidate,
        parent_structure=parent,
        parent_artifact_bytes=cif,
        operation_kind="VACANCY",
        rule_id="vacancy-chalcogen-class-v1",
    )[0]
    result = execute_registered_structure_operation(
        execution_request_from_compiled_operation_plan(plan),
        parent_structure=parent,
        parent_artifact_bytes=cif,
        smact_evaluator=_PassingSmact(),
    )
    assert result.prior.decision == "REJECT"
    assert result.plan.status == "REJECTED"
    assert "VACANCY_FRACTION_EXCEEDS_25_PERCENT" in result.prior.reason_codes


def test_registered_gap_intercalation_executes_with_smact_receipt(
    tmp_path: Path,
) -> None:
    store = LocalArtifactStore(tmp_path / "artifacts")
    cif = _tis2_cif()
    candidate = _candidate(store, cif=cif)
    state = OperatorPlanningToolState(
        store=store, database_candidates_snapshot=lambda: (candidate,)
    )
    compiled = state.as_tool().handler(
        CompileRegisteredOperationArgsV2(
            candidate_id="candidate-li-tis2",
            database_candidate_id=candidate.database_candidate_id,
            operation_kind="INTERCALATION",
            rule_id="li-vdw-hollow-a-v1",
        )
    )
    assert compiled["status"] == "COMPILED"
    plan = state.audit_snapshot().plans[0]
    parent = Structure.from_str(cif.decode(), fmt="cif")
    result = execute_registered_structure_operation(
        execution_request_from_compiled_operation_plan(plan),
        parent_structure=parent,
        parent_artifact_bytes=cif,
        smact_evaluator=_PassingSmact(),
    )
    assert result.plan.status == "REQUIRES_REVIEW"
    assert result.output_structure is not None
    assert result.output_structure.composition["Li"] == 1
    assert result.prior.smact_decision == "PASS"
    assert "HOST_REDOX_ASSIGNMENT_REQUIRED" in result.prior.reason_codes


def test_layer_slide_requires_multilayer_and_compiles_derived_partition(
    tmp_path: Path,
) -> None:
    store = LocalArtifactStore(tmp_path / "artifacts")
    mono = _candidate(store, cif=_tis2_cif(), suffix="6")
    mono_state = OperatorPlanningToolState(
        store=store, database_candidates_snapshot=lambda: (mono,)
    )
    rejected = mono_state.as_tool().handler(
        CompileRegisteredOperationArgsV2(
            candidate_id="candidate-slide-mono",
            database_candidate_id=mono.database_candidate_id,
            operation_kind="LAYER_SLIDE",
            rule_id="slide-a-to-b-v1",
        )
    )
    assert rejected["reason_code"] == "NO_MULTILAYER_PARTITION"

    bilayer = Structure(
        Lattice.hexagonal(3.4, 20.0),
        ("Ti", "S", "S", "Ti", "S", "S"),
        (
            (0, 0, 0.2),
            (1 / 3, 2 / 3, 0.15),
            (2 / 3, 1 / 3, 0.25),
            (0, 0, 0.7),
            (1 / 3, 2 / 3, 0.65),
            (2 / 3, 1 / 3, 0.75),
        ),
    )
    cif = _cif_bytes(bilayer)
    candidate = _candidate(store, cif=cif, suffix="7")
    state = OperatorPlanningToolState(
        store=store, database_candidates_snapshot=lambda: (candidate,)
    )
    compiled = state.as_tool().handler(
        CompileRegisteredOperationArgsV2(
            candidate_id="candidate-slide-bilayer",
            database_candidate_id=candidate.database_candidate_id,
            operation_kind="LAYER_SLIDE",
            rule_id="slide-a-to-b-v1",
        )
    )
    assert compiled["status"] == "COMPILED"
    plan = state.audit_snapshot().plans[0]
    assert plan.operator_id == "SLIDE_REGISTERED_LAYER_V1"
    assert len(plan.parameters.layer_site_indices) == 3
    result = execute_registered_structure_operation(
        execution_request_from_compiled_operation_plan(plan),
        parent_structure=Structure.from_str(cif.decode(), fmt="cif"),
        parent_artifact_bytes=cif,
    )
    assert result.plan.status == "REQUIRES_REVIEW"
    assert result.output_structure is not None
    assert result.output_structure.composition == bilayer.composition
