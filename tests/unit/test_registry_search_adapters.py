from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from material_agent.inspiration.deepseek_agent import DeepSeekAgentReceiptV1
from material_agent.inspiration.models import ArtifactPointerV1
from material_agent.inspiration.operator_planning import CompileReasonedOperationArgsV3
from material_agent.inspiration.research_graph import (
    DatabaseCandidateV1,
    DatabaseSourceRecordV1,
)
from material_agent.integration.registry_search_adapters import (
    DeepSeekRegistryOperatorProposer,
    RegistryStructureOperatorExecutor,
)
from material_agent.integration.scientific_loop import HypothesisCandidate
from material_agent.orchestrator.llm import LLMProviderError
from material_agent.retrieval.storage import LocalArtifactStore


def _pointer(ref) -> ArtifactPointerV1:
    return ArtifactPointerV1(
        uri=ref.uri,
        sha256=ref.sha256,
        size_bytes=ref.size_bytes,
        media_type=ref.media_type,
    )


def _database_parent(
    store: LocalArtifactStore,
) -> tuple[DatabaseCandidateV1, HypothesisCandidate]:
    source = (
        Path(__file__).parents[2]
        / "src/material_agent/inspiration/catalogs/flat_band_parent_catalog_v1"
        / "tis2-1t-vacuum-monolayer.cif"
    )
    cif = source.read_bytes()
    structure = store.write_bytes(
        "database/tis2.cif", cif, "chemical/x-cif", immutable=True
    )
    raw = store.write_json("database/tis2.json", {"fixture": "TiS2"}, immutable=True)
    database_id = "db-candidate-" + "3" * 24
    source_record = DatabaseSourceRecordV1(
        source_database="c2db",
        source_material_id="TiS2-fixture",
        source_database_version="fixture-v1",
        query_fingerprint="1" * 64,
        canonical_structure_id="str_" + "2" * 24,
        band_gap_ev=0.0,
        structure_artifact_uri=structure.uri,
        structure_artifact_sha256=structure.sha256,
        raw_response_artifact_uri=raw.uri,
        raw_response_artifact_sha256=raw.sha256,
        license="test-only",
    )
    database = DatabaseCandidateV1(
        database_candidate_id=database_id,
        source_database="c2db",
        source_material_id="TiS2-fixture",
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
        structure_artifact_uri=structure.uri,
        structure_artifact_sha256=structure.sha256,
        raw_response_artifact_uri=raw.uri,
        raw_response_artifact_sha256=raw.sha256,
    )
    hypothesis = HypothesisCandidate(
        # Production database-parent projections may use this ID shape even
        # though the legacy compiler tool requires candidate-* in its own field.
        candidate_id=database_id,
        material_name="TiS2 parent",
        formula="TiS2",
        hypothesis="A minimal structural operation may improve the target response.",
        mechanism="Small lattice changes may tune the relevant hopping amplitudes.",
        database_candidate_ids=(database_id,),
        parent_structure=_pointer(structure),
        elements=("S", "Ti"),
        transition_metal_elements=("Ti",),
        dimensionality=2,
        unresolved_claims=("target_property",),
    )
    return database, hypothesis


def _receipt(prompt_version: str) -> DeepSeekAgentReceiptV1:
    return DeepSeekAgentReceiptV1(
        prompt_version=prompt_version,
        reasoning_effort="high",
        rounds=1,
        tool_calls=(),
        request_sha256_by_round=("1" * 64,),
        response_sha256_by_round=("2" * 64,),
        transport_attempts_by_round=(1,),
        transport_retry_count=0,
        prompt_tokens=10,
        completion_tokens=10,
        reasoning_tokens=5,
        total_tokens=20,
        final_response_sha256="3" * 64,
    )


class _FeedbackFixture:
    def model_dump(self, **_values):
        return {"cycle_id": "feedback-fixture", "scientific_conclusion": False}


class _CompilingFakeAgent:
    operation_kind = "HOMOGENEOUS_STRAIN"
    last_compiled = None

    def __init__(self, **values) -> None:
        self.tool = values["tools"][0]

    def run(self, *, user_payload, prompt_version, final_model, **_values):
        hypothesis = user_payload["hypotheses"][0]
        compiler_binding = user_payload["compiler_parent_bindings"][0]
        database_id = hypothesis["database_candidate_ids"][0]
        common = {
            "candidate_id": compiler_binding["compiler_candidate_id"],
            "database_candidate_id": database_id,
            "operation_kind": self.operation_kind,
            "scientific_rationale": (
                "Apply a small evidence-directed perturbation to the current frontier."
            ),
            "expected_mechanism": "Tune orbital hopping without changing composition.",
            "chemical_prior_rationale": "Composition remains unchanged by this operation.",
            "decisive_falsification_test": "Reject if the next real ML screen fails.",
        }
        if self.operation_kind == "HOMOGENEOUS_STRAIN":
            common.update(
                {
                    "normal_strain_x_percent": 0.5,
                    "normal_strain_y_percent": 0.5,
                    "normal_strain_z_percent": 0.0,
                    "shear_strain_xy_percent": 0.0,
                    "shear_strain_xz_percent": 0.0,
                    "shear_strain_yz_percent": 0.0,
                }
            )
        else:
            common.update(
                {
                    "carrier_type": "ELECTRON",
                    "carriers_per_primitive_cell": 0.1,
                    "sample_charge_states": (0.0, 0.05, 0.1),
                }
            )
        compiled = self.tool.handler(CompileReasonedOperationArgsV3(**common))
        type(self).last_compiled = compiled
        selected = tuple(sorted(item["plan_id"] for item in compiled["plans"]))
        return SimpleNamespace(
            final=final_model(
                selected_plan_ids=selected,
                rationale="Select the newly compiled bounded operation for execution.",
            ),
            receipt=_receipt(prompt_version),
        )


class _ConditionCompilingFakeAgent(_CompilingFakeAgent):
    operation_kind = "CARRIER_DOPING"


class _BudgetFailingCompilingAgent(_CompilingFakeAgent):
    def run(self, **values):
        super().run(**values)
        raise LLMProviderError(
            "BUDGET_EXHAUSTED",
            "fixture exhausted after successful compiler call",
            retryable=False,
        )


class _MustNotRunAgent:
    def __init__(self, **_values) -> None:
        raise AssertionError("checkpoint recovery must not call DeepSeek again")


def _propose(proposer, hypothesis):
    return proposer.propose_operators(
        goal="Find a structure satisfying the target property without DFT.",
        iteration=1,
        hypotheses=(hypothesis,),
        evidence=(),
        feedback=_FeedbackFixture(),
        memory_snapshot_id="memory-fixture",
        remaining_candidate_slots=4,
        remaining_cost_units=10,
    )


def test_registry_adapters_execute_cif_and_register_child_as_next_parent(
    tmp_path: Path,
) -> None:
    store = LocalArtifactStore(tmp_path / "artifacts")
    database, hypothesis = _database_parent(store)
    database_candidates = [database]
    proposer = DeepSeekRegistryOperatorProposer(
        store=store,
        database_candidates=database_candidates,
        secret_resolver=object(),
        agent_factory=_CompilingFakeAgent,
    )
    executor = RegistryStructureOperatorExecutor.from_proposer(proposer)

    first_batch = _propose(proposer, hypothesis)
    first = executor.execute(proposal=first_batch.proposals[0], parent=hypothesis)

    assert proposer.last_user_payload_bytes is not None
    assert proposer.last_user_payload_bytes > 0
    assert proposer.checkpoint_uris
    assert all(store.exists(uri) for uri in proposer.checkpoint_uris)
    compact_plan = _CompilingFakeAgent.last_compiled["plans"][0]
    assert "parameters" not in compact_plan
    assert "operator_spec" not in compact_plan
    assert compact_plan["creates_structure_cif"] is True
    assert first.real_execution is True
    assert first.candidate is not None
    assert first.operator_result.status == "STRUCTURE_VALID"
    assert store.exists_with_hash(
        first.candidate.parent_structure.uri,
        first.candidate.parent_structure.sha256,
    )
    assert first.candidate.database_candidate_ids == (
        proposer.parent_catalog.generated_candidates[0].database_candidate_id,
    )

    second_batch = _propose(proposer, first.candidate)
    second_plan = next(
        item
        for item in proposer.planning_audit.plans
        if item.operator_spec.operator_spec_id
        == second_batch.proposals[0].operator_spec_id
    )
    assert second_batch.proposals[0].parent_candidate_id == first.candidate.candidate_id
    assert second_plan.parent_candidate_id == first.candidate.database_candidate_ids[0]


def test_condition_plan_never_becomes_a_generated_cif(tmp_path: Path) -> None:
    store = LocalArtifactStore(tmp_path / "artifacts")
    database, hypothesis = _database_parent(store)
    proposer = DeepSeekRegistryOperatorProposer(
        store=store,
        database_candidates=[database],
        secret_resolver=object(),
        agent_factory=_ConditionCompilingFakeAgent,
    )
    executor = RegistryStructureOperatorExecutor.from_proposer(proposer)

    batch = _propose(proposer, hypothesis)
    result = executor.execute(proposal=batch.proposals[0], parent=hypothesis)

    assert result.real_execution is True
    assert result.candidate is None
    assert result.operator_result.output_structure is None
    assert result.operator_result.status == "REJECTED"
    assert result.operator_result.reason_codes == (
        "CONDITION_ONLY_PLAN_NO_STRUCTURE_ARTIFACT",
    )
    assert proposer.parent_catalog.generated_candidates == ()


def test_budget_recovery_keeps_only_live_compiled_structure_plans(
    tmp_path: Path,
) -> None:
    store = LocalArtifactStore(tmp_path / "artifacts")
    database, hypothesis = _database_parent(store)
    proposer = DeepSeekRegistryOperatorProposer(
        store=store,
        database_candidates=[database],
        secret_resolver=object(),
        agent_factory=_BudgetFailingCompilingAgent,
    )

    batch = _propose(proposer, hypothesis)

    assert len(batch.proposals) == 1
    assert batch.provenance.live_call is False
    assert batch.selection_recovery == (
        "ALL_NEW_COMPILED_STRUCTURE_PLANS_AFTER_REASONING_FAILURE"
    )
    assert "deterministic recovery" in batch.rationale


def test_immutable_checkpoint_recovers_without_repeating_reasoning(
    tmp_path: Path,
) -> None:
    store = LocalArtifactStore(tmp_path / "artifacts")
    database, hypothesis = _database_parent(store)
    first = DeepSeekRegistryOperatorProposer(
        store=store,
        database_candidates=[database],
        secret_resolver=object(),
        agent_factory=_CompilingFakeAgent,
    )
    original = _propose(first, hypothesis)
    checkpoint_uri = first.checkpoint_uris[-1]

    recovered = DeepSeekRegistryOperatorProposer(
        store=store,
        database_candidates=[database],
        secret_resolver=object(),
        agent_factory=_MustNotRunAgent,
    )
    recovered.recover_checkpoint(checkpoint_uri)
    batch = _propose(recovered, hypothesis)

    assert tuple(item.operator_spec_id for item in batch.proposals) == tuple(
        item.operator_spec_id for item in original.proposals
    )
    assert batch.provenance.live_call is False
    assert batch.selection_recovery == (
        "ALL_NEW_COMPILED_STRUCTURE_PLANS_AFTER_REASONING_FAILURE"
    )
    assert "immutable checkpoint" in batch.rationale
