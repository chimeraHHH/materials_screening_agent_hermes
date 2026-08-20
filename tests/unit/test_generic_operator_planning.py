from __future__ import annotations

import hashlib
import warnings
from pathlib import Path

import pytest
from pymatgen.core import Composition, Structure

from material_agent.inspiration.models import ArtifactPointerV1
from material_agent.inspiration.operator_planning import (
    CompileRegisteredSubstitutionArgsV1,
    OperatorPlanningToolState,
    execution_request_from_compiled_plan,
    planning_state_sha256,
)
from material_agent.inspiration.research_graph import (
    CandidateHypothesisV1,
    CandidateSetV1,
    DatabaseCandidateV1,
    DatabaseSourceRecordV1,
    _validate_candidate_transformations,
)
from material_agent.retrieval.storage import LocalArtifactStore
from material_agent.softchem import (
    DEFAULT_SMACT_PRIOR_POLICY_V1,
    DEFAULT_SOFTCHEM_OPERATOR_REGISTRY_V1,
    SMACT_WORKER_LOCK_SHA256,
    ElementStoichiometryV1,
    SmactPriorDecision,
    SmactPriorGateResultV1,
    execute_registered_softchem_operator,
    smact_prior_policy_sha256,
    softchem_registry_bytes,
)


class _PassingPrior:
    def evaluate(self, composition, *, input_formula, policy):
        parsed = composition if isinstance(composition, Composition) else Composition(composition)
        amounts = parsed.reduced_composition.element_composition.get_el_amt_dict()
        stoichiometry = tuple(
            ElementStoichiometryV1(element=symbol, amount=int(amounts[symbol]))
            for symbol in sorted(amounts)
        )
        return SmactPriorGateResultV1(
            decision=SmactPriorDecision.PASS,
            policy_id=policy.policy_id,
            policy_sha256=smact_prior_policy_sha256(policy),
            backend_version=policy.smact_version,
            worker_lock_sha256=SMACT_WORKER_LOCK_SHA256,
            input_formula=input_formula or "TiS2",
            proposed_formula=parsed.reduced_formula,
            reduced_stoichiometry=stoichiometry,
            smact_valid=True,
            reason_codes=("CHARGE_NEUTRALITY_AND_PAULING_PASS",),
        )


def _pointer(uri: str, payload: bytes, media_type: str) -> ArtifactPointerV1:
    return ArtifactPointerV1(
        uri=uri,
        sha256=hashlib.sha256(payload).hexdigest(),
        size_bytes=len(payload),
        media_type=media_type,
    )


def _candidate(store: LocalArtifactStore) -> tuple[DatabaseCandidateV1, bytes]:
    source = (
        Path(__file__).parents[2]
        / "src/material_agent/inspiration/catalogs/flat_band_parent_catalog_v1"
        / "tis2-1t-vacuum-monolayer.cif"
    )
    cif = source.read_bytes()
    structure_ref = store.write_bytes(
        "database/tis2-parent.cif", cif, "chemical/x-cif", immutable=True
    )
    raw = b'{"source":"catalog-fixture"}'
    raw_ref = store.write_bytes(
        "database/tis2-parent.json", raw, "application/json", immutable=True
    )
    source_record = DatabaseSourceRecordV1(
        source_database="c2db",
        source_material_id="TiS2-catalog-fixture",
        source_database_version="catalog-v1",
        query_fingerprint="1" * 64,
        canonical_structure_id="str_" + "2" * 24,
        band_gap_ev=0.0,
        structure_artifact_uri=structure_ref.uri,
        structure_artifact_sha256=structure_ref.sha256,
        raw_response_artifact_uri=raw_ref.uri,
        raw_response_artifact_sha256=raw_ref.sha256,
        license="test fixture",
    )
    return (
        DatabaseCandidateV1(
            database_candidate_id="db-candidate-" + "3" * 24,
            source_database="c2db",
            source_material_id="TiS2-catalog-fixture",
            canonical_structure_id="str_" + "2" * 24,
            source_records=(source_record,),
            formula="TiS2",
            elements=("S", "Ti"),
            transition_metals=("Ti",),
            band_gap_ev=0.0,
            dimensionality=2,
            dimensionality_status="RESOLVED",
            connected_transition_metal_sublattice_proxy=1.0,
            connectivity_status="RESOLVED_PROXY",
            structure_artifact_uri=structure_ref.uri,
            structure_artifact_sha256=structure_ref.sha256,
            raw_response_artifact_uri=raw_ref.uri,
            raw_response_artifact_sha256=raw_ref.sha256,
        ),
        cif,
    )


def test_deepseek_tool_plan_executes_on_real_layered_cif(tmp_path: Path) -> None:
    store = LocalArtifactStore(tmp_path / "artifacts")
    candidate, cif = _candidate(store)
    state = OperatorPlanningToolState(
        store=store,
        database_candidates_snapshot=lambda: (candidate,),
    )
    result = state.as_tool().handler(
        CompileRegisteredSubstitutionArgsV1(
            candidate_id="candidate-tise2",
            database_candidate_id=candidate.database_candidate_id,
            substitution_rule_id="s-to-se-isovalent-v1",
        )
    )

    assert result["status"] == "COMPILED"
    audit = state.audit_snapshot()
    assert audit.registry_status == "HASH_PINNED"
    assert audit.compile_attempt_count == 1
    assert len(audit.plans) == 1
    plan = audit.plans[0]
    assert plan.parameters.equivalent_site_indices == (1, 2)

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        parent = Structure.from_str(cif.decode("utf-8"), fmt="cif")
    request = execution_request_from_compiled_plan(plan, parent_structure=parent)
    registry_payload = softchem_registry_bytes(
        DEFAULT_SOFTCHEM_OPERATOR_REGISTRY_V1
    )
    execution = execute_registered_softchem_operator(
        request,
        parent_structure=parent,
        parent_artifact_bytes=cif,
        operator_registry=DEFAULT_SOFTCHEM_OPERATOR_REGISTRY_V1,
        operator_registry_artifact=_pointer(
            "artifact://softchem/registry/operators-v1.json",
            registry_payload,
            "application/json",
        ),
        prior_policy=DEFAULT_SMACT_PRIOR_POLICY_V1,
        prior_evaluator=_PassingPrior(),
    )

    assert execution.plan.status == "STRUCTURE_VALID"
    assert execution.output_structure is not None
    assert execution.output_structure.composition.reduced_formula == "TiSe2"


def test_candidate_must_reference_only_compiled_plan_ids(tmp_path: Path) -> None:
    store = LocalArtifactStore(tmp_path / "artifacts")
    candidate, _ = _candidate(store)
    state = OperatorPlanningToolState(
        store=store,
        database_candidates_snapshot=lambda: (candidate,),
    )
    state.as_tool().handler(
        CompileRegisteredSubstitutionArgsV1(
            candidate_id="candidate-tise2",
            database_candidate_id=candidate.database_candidate_id,
            substitution_rule_id="s-to-se-isovalent-v1",
        )
    )
    plan_id = state.audit_snapshot().plans[0].plan_id
    valid = CandidateSetV1(
        candidates=(
            CandidateHypothesisV1(
                candidate_id="candidate-tise2",
                material_name="TiSe2 substitution hypothesis",
                formula="TiSe2",
                hypothesis="Replace the complete ligand equivalence class.",
                mechanism="Ligand substitution may tune transition-metal hybridization.",
                database_candidate_ids=(candidate.database_candidate_id,),
                proposed_registered_transformations=(plan_id,),
            ),
        )
    )
    _validate_candidate_transformations(valid, state.audit_snapshot())

    invented = valid.model_copy(
        update={
            "candidates": (
                valid.candidates[0].model_copy(
                    update={"proposed_registered_transformations": ("free-text-swap",)}
                ),
            )
        }
    )
    with pytest.raises(ValueError, match="uncompiled"):
        _validate_candidate_transformations(invented, state.audit_snapshot())


def test_unregistered_or_inapplicable_route_is_audited_not_compiled(
    tmp_path: Path,
) -> None:
    store = LocalArtifactStore(tmp_path / "artifacts")
    candidate, _ = _candidate(store)
    state = OperatorPlanningToolState(
        store=store,
        database_candidates_snapshot=lambda: (candidate,),
    )
    result = state.as_tool().handler(
        CompileRegisteredSubstitutionArgsV1(
            candidate_id="candidate-invalid",
            database_candidate_id=candidate.database_candidate_id,
            substitution_rule_id="se-to-s-isovalent-v1",
        )
    )

    assert result["status"] == "REJECTED"
    assert result["reason_code"] == "NO_COMPLETE_EQUIVALENCE_CLASS"
    assert not state.audit_snapshot().plans
    assert len(state.audit_snapshot().rejections) == 1


def test_operator_planning_checkpoint_replays_exact_registry_bound_state(
    tmp_path: Path,
) -> None:
    store = LocalArtifactStore(tmp_path / "artifacts")
    candidate, _ = _candidate(store)
    original = OperatorPlanningToolState(
        store=store,
        database_candidates_snapshot=lambda: (candidate,),
    )
    original.as_tool().handler(
        CompileRegisteredSubstitutionArgsV1(
            candidate_id="candidate-tise2",
            database_candidate_id=candidate.database_candidate_id,
            substitution_rule_id="s-to-se-isovalent-v1",
        )
    )

    replay = OperatorPlanningToolState(
        store=store,
        database_candidates_snapshot=lambda: (candidate,),
    )
    replay.restore_snapshot(original.checkpoint_snapshot())

    assert replay.audit_snapshot() == original.audit_snapshot()
    assert planning_state_sha256(replay) == planning_state_sha256(original)

    tampered = dict(original.checkpoint_snapshot())
    tampered["operator_registry_sha256"] = "0" * 64
    fresh = OperatorPlanningToolState(
        store=store,
        database_candidates_snapshot=lambda: (candidate,),
    )
    with pytest.raises(ValueError, match="registry hash mismatch"):
        fresh.restore_snapshot(tampered)
