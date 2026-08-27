from __future__ import annotations

import gzip
import json
from pathlib import Path

from material_agent.inspiration.models import ArtifactPointerV1, canonical_sha256
from material_agent.inspiration.research_memory import InspirationMemoryStore
from material_agent.integration.c2db_scientific import (
    C2DBBandBundle,
    C2DBBandInputImportExecutor,
    C2DBFlatBandScientificExecutor,
)
from material_agent.integration.scientific_execution import (
    ScientificExecutorRegistry,
    execute_scientific_dag,
)
from material_agent.integration.scientific_executors import (
    LocalTwoDStructureExecutor,
    artifact_pointer_from_ref,
)
from material_agent.integration.scientific_loop import (
    CapabilityAvailability,
    DeepSeekModelRouteProposal,
    DeepSeekReasoningProvenance,
    HypothesisCandidate,
    ModelCapability,
    ModelTaskInputKind,
    ProposedModelTask,
    ScientificArtifactKind,
    ScientificEvidenceLevel,
    ScientificEvidenceVerdict,
    ScientificTaskKind,
    ScientificValidationLoopService,
    ValidationRoutePolicy,
    WeightStatus,
)
from material_agent.retrieval.storage import LocalArtifactStore

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
MONOLAYER = (
    REPOSITORY_ROOT
    / "src/material_agent/inspiration/catalogs/flat_band_parent_catalog_v1"
    / "tis2-1t-vacuum-monolayer.cif"
)


class _RouteReasoner:
    def __init__(self, proposal: DeepSeekModelRouteProposal) -> None:
        self.proposal = proposal

    def propose_route(self, **_values: object) -> DeepSeekModelRouteProposal:
        return self.proposal

    def propose_feedback(self, **_values: object):
        raise AssertionError("C2DB execution fixture does not request feedback")


def _plotly_band_payload() -> bytes:
    k = [0.0, 1.0, 2.0, 3.0]
    bands = (
        [-1.0, -0.8, -0.9, -0.7],
        [0.01, 0.09, 0.02, 0.08],
        [0.8, 0.9, 1.0, 0.9],
    )
    return json.dumps(
        {
            "material_id": "fixture-tis2",
            "method": "GPAW/PBE",
            "plotly": {
                "data": [
                    {
                        "name": "PBE no SOC",
                        "x": k * len(bands),
                        "y": [value for band in bands for value in band],
                    }
                ],
                "layout": {
                    "xaxis": {
                        "ticktext": ["Γ", "M", "Γ"],
                        "tickvals": [0.0, 2.0, 3.0],
                    },
                    "yaxis": {"title": {"text": "E - E_VBM [eV]"}},
                },
            },
            "source_url": "https://c2db.fysik.dtu.dk/material/fixture-tis2",
        },
        sort_keys=True,
    ).encode()


def test_c2db_import_and_local_flat_band_analysis_form_a_typed_dag(
    tmp_path: Path,
) -> None:
    store = LocalArtifactStore(tmp_path / "project")
    source = artifact_pointer_from_ref(
        store.write_bytes(
            "inputs/tis2.cif",
            MONOLAYER.read_bytes(),
            "chemical/x-cif",
            immutable=True,
        )
    )
    band_data = artifact_pointer_from_ref(
        store.write_bytes(
            "inputs/c2db-band.json.gz",
            gzip.compress(_plotly_band_payload()),
            "application/gzip",
            immutable=True,
        )
    )
    bundle = C2DBBandBundle(
        material_id="fixture-tis2",
        method="GPAW/PBE",
        source_url="https://c2db.fysik.dtu.dk/material/fixture-tis2",
        source_structure=source,
        band_data=band_data,
        band_gap_ev=0.2,
        trace_names=("PBE no SOC",),
    )
    hypothesis = HypothesisCandidate(
        candidate_id="candidate-c2db-tis2",
        material_name="C2DB TiS2 fixture",
        formula="TiS2",
        hypothesis="Retrieved non-SOC bands can reject an over-wide near-Fermi band.",
        mechanism="Local deterministic rules operate on the downloaded band array.",
        database_candidate_ids=("fixture-tis2",),
        parent_structure=source,
        elements=("S", "Ti"),
        dimensionality=2,
        unresolved_claims=("bandwidth_le_50_mev",),
    )
    goal = "screen one downloaded C2DB band array without DFT"
    two_d_task = ProposedModelTask(
        task_id="task-two-d",
        candidate_id=hypothesis.candidate_id,
        model_id="local-two-d-v1",
        task_kind=ScientificTaskKind.TWO_D_STRUCTURE_CHECK,
        input_kind=ModelTaskInputKind.PARENT_STRUCTURE,
        produced_artifact_kinds=(
            ScientificArtifactKind.TWO_D_STRUCTURE_ASSESSMENT,
        ),
        requested_observables=("two_dimensional_structure",),
        required_evidence_level=ScientificEvidenceLevel.L1_RETRIEVED,
        parameters={
            "contributor_elements": ["Ti"],
            "minimum_periodic_void_gap_angstrom": 3.0,
        },
        rationale="Check the downloaded structure and contributor network.",
        falsification_rule="Reject a non-2D or disconnected Ti sublattice.",
    )
    import_task = ProposedModelTask(
        task_id="task-c2db-import",
        candidate_id=hypothesis.candidate_id,
        model_id="c2db-band-import-v1",
        task_kind=ScientificTaskKind.DATABASE_BAND_DATA_IMPORT,
        input_kind=ModelTaskInputKind.PARENT_STRUCTURE,
        produced_artifact_kinds=(
            ScientificArtifactKind.DATABASE_BAND_STRUCTURE,
        ),
        requested_observables=("retrieved_non_soc_band_structure",),
        required_evidence_level=ScientificEvidenceLevel.L1_RETRIEVED,
        rationale="Import the exact downloaded official band array.",
        falsification_rule="Block on source-structure or band-data hash mismatch.",
    )
    band_task = ProposedModelTask(
        task_id="task-flat-band",
        candidate_id=hypothesis.candidate_id,
        model_id="c2db-flat-band-local-v1",
        task_kind=ScientificTaskKind.BAND_ORBITAL_ANALYSIS,
        input_kind=ModelTaskInputKind.PREVIOUS_TASK,
        prerequisite_task_ids=(import_task.task_id, two_d_task.task_id),
        required_input_artifact_kinds=(
            ScientificArtifactKind.DATABASE_BAND_STRUCTURE,
            ScientificArtifactKind.TWO_D_STRUCTURE_ASSESSMENT,
        ),
        produced_artifact_kinds=(
            ScientificArtifactKind.FLAT_BAND_ASSESSMENT,
            ScientificArtifactKind.NON_SOC_BAND_STRUCTURE,
        ),
        requested_observables=("bandwidth_le_50_mev",),
        required_evidence_level=ScientificEvidenceLevel.L1_RETRIEVED,
        parameters={
            "contributor_sublattice_connected": None,
            "fermi_reference": "MIDGAP_FROM_DATABASE",
            "policy": {
                "degeneracy_tolerance_ev": 0.0001,
                "maximum_bandwidth_ev": 0.05,
                "minimum_tm_ligand_weight_fraction": 0.6,
                "minimum_transition_metal_weight_fraction": 0.2,
                "near_fermi_window_ev": 0.05,
                "require_soc_explicit": False,
                "slope_difference_tolerance_ev_angstrom": 0.001,
            },
            "trace_name": "PBE no SOC",
        },
        rationale="Apply the exact 50 meV rule to retrieved bands.",
        falsification_rule="Contradict when the nearest band exceeds 50 meV.",
    )
    proposal = DeepSeekModelRouteProposal(
        goal_sha256=canonical_sha256({"goal": goal}),
        provenance=DeepSeekReasoningProvenance(
            prompt_version="c2db-band-dag-fixture-v1",
            receipt_sha256="a" * 64,
            live_call=False,
        ),
        route_summary="Check 2D geometry, import C2DB bands and assess flatness.",
        tasks=(two_d_task, import_task, band_task),
    )
    capabilities = (
        ModelCapability(
            model_id=two_d_task.model_id,
            availability=CapabilityAvailability.READY,
            weight_status=WeightStatus.NOT_REQUIRED,
            supported_observables=two_d_task.requested_observables,
            supported_task_kinds=(ScientificTaskKind.TWO_D_STRUCTURE_CHECK,),
            produced_artifact_kinds=two_d_task.produced_artifact_kinds,
            supported_elements=("S", "Ti"),
            supported_dimensionalities=(2,),
            evidence_ceiling=ScientificEvidenceLevel.L1_RETRIEVED,
            estimated_cost_units=1,
            real_backend=True,
            benchmark_status="VALIDATED",
        ),
        ModelCapability(
            model_id=import_task.model_id,
            availability=CapabilityAvailability.READY,
            weight_status=WeightStatus.NOT_REQUIRED,
            supported_observables=import_task.requested_observables,
            supported_task_kinds=(ScientificTaskKind.DATABASE_BAND_DATA_IMPORT,),
            produced_artifact_kinds=import_task.produced_artifact_kinds,
            supported_elements=("S", "Ti"),
            supported_dimensionalities=(2,),
            evidence_ceiling=ScientificEvidenceLevel.L1_RETRIEVED,
            estimated_cost_units=1,
            real_backend=True,
            benchmark_status="VALIDATED",
        ),
        ModelCapability(
            model_id=band_task.model_id,
            availability=CapabilityAvailability.READY,
            weight_status=WeightStatus.NOT_REQUIRED,
            supported_observables=band_task.requested_observables,
            supported_task_kinds=(ScientificTaskKind.BAND_ORBITAL_ANALYSIS,),
            accepted_artifact_kinds=band_task.required_input_artifact_kinds,
            required_input_artifact_kinds=band_task.required_input_artifact_kinds,
            produced_artifact_kinds=band_task.produced_artifact_kinds,
            supported_elements=("S", "Ti"),
            supported_dimensionalities=(2,),
            evidence_ceiling=ScientificEvidenceLevel.L1_RETRIEVED,
            estimated_cost_units=1,
            real_backend=True,
            benchmark_status="VALIDATED",
        ),
    )

    def verify(pointer: ArtifactPointerV1) -> bool:
        return store.exists_with_hash(pointer.uri, pointer.sha256)

    with InspirationMemoryStore(
        tmp_path / "memory.sqlite3",
        artifact_verifier=verify,
    ) as memory:
        service = ScientificValidationLoopService(
            project_id="c2db-dag-project",
            source_run_id="c2db-dag-run",
            goal=goal,
            hypotheses=(hypothesis,),
            capabilities=capabilities,
            policy=ValidationRoutePolicy(require_live_deepseek_reasoning=False),
            artifact_store=store,
            memory_store=memory,
            reasoner=_RouteReasoner(proposal),
        )
        _route, plan = service.propose_and_audit_route(())
        registry = ScientificExecutorRegistry()
        registry.register(two_d_task.model_id, LocalTwoDStructureExecutor(store))
        registry.register(
            import_task.model_id,
            C2DBBandInputImportExecutor(store, lambda _binding: bundle),
        )
        registry.register(
            band_task.model_id,
            C2DBFlatBandScientificExecutor(store, lambda _binding: bundle),
        )
        result = execute_scientific_dag(
            plan=plan,
            service=service,
            executors=registry,
        )
        snapshot = memory.snapshot("c2db-dag-project")

    assert result.all_audited_tasks_succeeded
    assert len(snapshot.downstream_outcomes) == 3
    assert result.evidence[-1].verdict is ScientificEvidenceVerdict.CONTRADICTS
    assert "BANDWIDTH_EXCEEDS_POLICY" in result.evidence[-1].reason_codes
