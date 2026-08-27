"""Pure-ML scientific DAG executors for precomputed Uni-HamGNN inputs."""

from __future__ import annotations

from collections.abc import Callable, MutableMapping
from dataclasses import dataclass
from typing import Literal, Protocol

from pydantic import model_validator

from material_agent.inspiration.models import (
    ArtifactPointerV1,
    Identifier,
    Sha256,
    StrictModel,
)
from material_agent.integration.electronic_structure import (
    BandOrbitalProjection,
    FlatBandAnalysisInput,
    FlatBandPolicy,
    ValidationVerdict,
    assess_flat_band,
    parse_hamgnn_band_dat,
)
from material_agent.integration.scientific_loop import (
    ModelExecutionReceipt,
    ScientificArtifactKind,
    ScientificEvidenceLevel,
    ScientificEvidenceVerdict,
    ScientificTaskKind,
    TaskExecutionBinding,
    make_scientific_task_artifact,
)
from material_agent.ml_screening.uniham_band_models import UniHamBandRequest
from material_agent.ml_screening.uniham_band_remote import (
    UniHamBandRemoteClient,
    UniHamBandRemoteResult,
)
from material_agent.ml_screening.uniham_client import audit_uniham_benchmark
from material_agent.ml_screening.uniham_graph_prep_models import (
    NonSCFGraphPrepParameters,
    UniHamGraphPrepRequest,
)
from material_agent.ml_screening.uniham_graph_prep_remote import (
    UniHamGraphPrepRemoteClient,
    UniHamGraphPrepRemoteResult,
)
from material_agent.ml_screening.uniham_models import (
    UniHamBenchmarkPolicy,
    UniHamGraphBundle,
    UniHamGraphManifest,
    UniHamInferenceRequest,
    UniHamSocMode,
    UniHamWorkerLimits,
)
from material_agent.ml_screening.uniham_planner import (
    build_uniham_plan_from_manifests,
)
from material_agent.ml_screening.uniham_remote import (
    UniHamRemoteClient,
    UniHamRemoteExecutionResult,
)
from material_agent.retrieval.models import ArtifactRef
from material_agent.retrieval.storage import LocalArtifactStore


class PrecomputedUniHamInputBundle(StrictModel):
    """Catalog-owned, reviewed graph inputs; Hermes does not generate them."""

    schema_version: Literal["scientific-uniham-input-bundle-v1"] = (
        "scientific-uniham-input-bundle-v1"
    )
    catalog_id: Identifier
    catalog_version: Identifier
    request: UniHamInferenceRequest
    non_soc_manifest: UniHamGraphManifest
    soc_manifest: UniHamGraphManifest
    reviewed_by: Identifier
    scientific_conclusion: Literal[False] = False

    @model_validator(mode="after")
    def validate_bundle(self) -> PrecomputedUniHamInputBundle:
        if self.request.device != "cuda":
            raise ValueError("scientific Uni-HamGNN bundle must target CUDA")
        build_uniham_plan_from_manifests(
            self.request,
            non_soc_manifest=self.non_soc_manifest,
            soc_manifest=self.soc_manifest,
        )
        return self


class PrecomputedUniHamGraphDescriptor(StrictModel):
    schema_version: Literal["scientific-uniham-graph-descriptor-v1"] = (
        "scientific-uniham-graph-descriptor-v1"
    )
    catalog_id: Identifier
    catalog_version: Identifier
    candidate_id: Identifier
    source_structure_sha256: Sha256
    soc_mode: UniHamSocMode
    graph_bundle: UniHamGraphBundle
    manifest: UniHamGraphManifest
    reviewed_by: Identifier
    scientific_conclusion: Literal[False] = False

    @model_validator(mode="after")
    def validate_descriptor(self) -> PrecomputedUniHamGraphDescriptor:
        if self.manifest.soc_mode is not self.soc_mode:
            raise ValueError("precomputed graph descriptor SOC mode mismatch")
        if self.manifest.structure_sha256 != self.source_structure_sha256:
            raise ValueError("precomputed graph descriptor structure mismatch")
        if self.manifest.graph_data_sha256 != self.graph_bundle.graph_data.sha256:
            raise ValueError("precomputed graph descriptor data mismatch")
        return self


class UniHamRemoteRunner(Protocol):
    def run(
        self,
        request: UniHamInferenceRequest,
        *,
        non_soc_manifest: UniHamGraphManifest,
        soc_manifest: UniHamGraphManifest,
        limits: UniHamWorkerLimits | None = None,
    ) -> UniHamRemoteExecutionResult: ...


class UniHamBandRemoteRunner(Protocol):
    def run(self, request: UniHamBandRequest) -> UniHamBandRemoteResult: ...


class UniHamGraphPrepRemoteRunner(Protocol):
    def run(self, request: UniHamGraphPrepRequest) -> UniHamGraphPrepRemoteResult: ...


class UniHamFlatBandAnalysisParameters(StrictModel):
    fermi_energy_ev: float = 0.0
    target_band_index: int | None = None
    contributor_sublattice_connected: bool | None = None
    orbital_projections: tuple[BandOrbitalProjection, ...] = ()
    policy: FlatBandPolicy


@dataclass(frozen=True)
class NonSCFUniHamGraphPrepScientificExecutor:
    """Prepare exact-child graph pairs without invoking self-consistent DFT."""

    artifact_store: LocalArtifactStore
    client: UniHamGraphPrepRemoteRunner | UniHamGraphPrepRemoteClient
    request_factory: Callable[[TaskExecutionBinding], UniHamGraphPrepRequest]
    bundle_factory: Callable[
        [TaskExecutionBinding, UniHamGraphPrepRemoteResult],
        PrecomputedUniHamInputBundle,
    ]
    bundle_registry: MutableMapping[str, PrecomputedUniHamInputBundle]
    executor_id: str = "non-scf-uniham-graph-prep-scientific-executor-v1"

    def execute(self, *, binding: TaskExecutionBinding) -> ModelExecutionReceipt:
        task = binding.proposed_task
        if (
            task.task_kind
            is not ScientificTaskKind.NON_SCF_ATOMIC_BASIS_GRAPH_PREPARATION
        ):
            raise ValueError("non-SCF graph-prep executor got an incompatible task")
        if set(task.produced_artifact_kinds) != {
            ScientificArtifactKind.NON_SOC_HAMILTONIAN_GRAPH,
            ScientificArtifactKind.SOC_HAMILTONIAN_GRAPH,
        }:
            raise ValueError("non-SCF graph prep requires both graph outputs")
        if task.required_evidence_level is not ScientificEvidenceLevel.NONE:
            raise ValueError("non-SCF graph preparation cannot publish evidence")
        declared = NonSCFGraphPrepParameters.model_validate(task.parameters)
        request = self.request_factory(binding)
        if request.parameters != declared:
            raise ValueError("graph-prep request differs from DeepSeek task parameters")
        if request.candidate_id != task.candidate_id:
            raise ValueError("graph-prep request candidate differs from DAG binding")
        if request.input_structure.sha256 != binding.source_structure.sha256:
            raise ValueError("graph-prep request structure differs from DAG binding")
        result = self.client.run(request)
        if result.self_consistent_dft_invocations != 0:
            raise ValueError("graph-prep result crossed the ML-only SCF boundary")
        bundle = self.bundle_factory(binding, result)
        self._validate_bundle(binding, result, bundle)
        self.bundle_registry[task.candidate_id] = bundle
        descriptors = (
            PrecomputedUniHamGraphDescriptor(
                catalog_id=f"runtime-non-scf-{result.plan.operation_key[:20]}",
                catalog_version="runtime-non-scf-openmx-v1",
                candidate_id=task.candidate_id,
                source_structure_sha256=binding.source_structure.sha256,
                soc_mode=UniHamSocMode.NON_SOC,
                graph_bundle=result.non_soc_graph,
                manifest=result.non_soc_manifest,
                reviewed_by="hermes-hash-bound-runtime",
            ),
            PrecomputedUniHamGraphDescriptor(
                catalog_id=f"runtime-non-scf-{result.plan.operation_key[:20]}",
                catalog_version="runtime-non-scf-openmx-v1",
                candidate_id=task.candidate_id,
                source_structure_sha256=binding.source_structure.sha256,
                soc_mode=UniHamSocMode.SOC,
                graph_bundle=result.soc_graph,
                manifest=result.soc_manifest,
                reviewed_by="hermes-hash-bound-runtime",
            ),
        )
        produced = []
        base = f"scientific_loop/tasks/{task.task_id}/{binding.binding_id}"
        for descriptor in descriptors:
            ref = self.artifact_store.write_json(
                f"{base}/{descriptor.soc_mode.value}-graph-descriptor.json",
                descriptor.model_dump(mode="json"),
                immutable=True,
            )
            produced.append(
                make_scientific_task_artifact(
                    candidate_id=task.candidate_id,
                    producer_task_id=task.task_id,
                    kind=(
                        ScientificArtifactKind.SOC_HAMILTONIAN_GRAPH
                        if descriptor.soc_mode is UniHamSocMode.SOC
                        else ScientificArtifactKind.NON_SOC_HAMILTONIAN_GRAPH
                    ),
                    pointer=_pointer(ref),
                    is_mock=False,
                )
            )
        summary_ref = self.artifact_store.write_json(
            f"{base}/non-scf-graph-prep-summary.json",
            {
                "candidate_id": task.candidate_id,
                "host_alias": result.host_alias,
                "operation_key": result.plan.operation_key,
                "self_consistent_dft_invocations": 0,
                "source_structure_sha256": binding.source_structure.sha256,
                "scientific_conclusion": False,
            },
            immutable=True,
        )
        return ModelExecutionReceipt(
            task_id=task.task_id,
            status="SUCCEEDED",
            verdict=ScientificEvidenceVerdict.INCONCLUSIVE,
            tested_claim_ids=task.requested_observables,
            evidence_level=ScientificEvidenceLevel.NONE,
            result_artifact=_pointer(summary_ref),
            consumed_artifact_ids=tuple(
                item.artifact_id for item in binding.consumed_artifacts
            ),
            produced_artifacts=tuple(
                sorted(produced, key=lambda item: item.artifact_id)
            ),
            runtime_provenance=dict(
                sorted(
                    {
                        "executor_id": self.executor_id,
                        "graph_prep_operation_key": result.plan.operation_key,
                        "host_alias": result.host_alias,
                        "self_consistent_dft_invocations": 0,
                        "source_structure_sha256": binding.source_structure.sha256,
                    }.items()
                )
            ),
            reason_codes=("NON_SCF_ATOMIC_BASIS_GRAPH_PAIR_PREPARED",),
            real_execution=True,
        )

    @staticmethod
    def _validate_bundle(
        binding: TaskExecutionBinding,
        result: UniHamGraphPrepRemoteResult,
        bundle: PrecomputedUniHamInputBundle,
    ) -> None:
        if bundle.request.input_structure.sha256 != binding.source_structure.sha256:
            raise ValueError("runtime Uni-HamGNN bundle structure mismatch")
        if bundle.request.non_soc_graph != result.non_soc_graph:
            raise ValueError("runtime non-SOC graph bundle mismatch")
        if bundle.request.soc_graph != result.soc_graph:
            raise ValueError("runtime SOC graph bundle mismatch")
        if bundle.non_soc_manifest != result.non_soc_manifest:
            raise ValueError("runtime non-SOC manifest mismatch")
        if bundle.soc_manifest != result.soc_manifest:
            raise ValueError("runtime SOC manifest mismatch")


@dataclass(frozen=True)
class PrecomputedUniHamInputImportExecutor:
    artifact_store: LocalArtifactStore
    bundle_resolver: Callable[[TaskExecutionBinding], PrecomputedUniHamInputBundle]
    executor_id: str = "precomputed-uniham-input-import-executor-v1"

    def execute(self, *, binding: TaskExecutionBinding) -> ModelExecutionReceipt:
        task = binding.proposed_task
        if task.task_kind is not ScientificTaskKind.PRECOMPUTED_ELECTRONIC_INPUT_IMPORT:
            raise ValueError("precomputed-input executor got an incompatible task")
        expected = {
            ScientificArtifactKind.NON_SOC_HAMILTONIAN_GRAPH,
            ScientificArtifactKind.SOC_HAMILTONIAN_GRAPH,
        }
        if set(task.produced_artifact_kinds) != expected:
            raise ValueError("precomputed-input executor requires both graph outputs")
        if task.required_evidence_level not in {
            ScientificEvidenceLevel.L0_PARSED,
            ScientificEvidenceLevel.L1_RETRIEVED,
        }:
            raise ValueError("precomputed input import is capped at L1 evidence")

        bundle = self.bundle_resolver(binding)
        self._validate_lineage(binding, bundle)
        descriptors = (
            self._descriptor(bundle, UniHamSocMode.NON_SOC),
            self._descriptor(bundle, UniHamSocMode.SOC),
        )
        produced = []
        descriptor_pointers: list[ArtifactPointerV1] = []
        base = f"scientific_loop/tasks/{task.task_id}/{binding.binding_id}"
        for descriptor in descriptors:
            reference = self.artifact_store.write_json(
                f"{base}/{descriptor.soc_mode.value}-graph-descriptor.json",
                descriptor.model_dump(mode="json"),
                immutable=True,
            )
            pointer = _pointer(reference)
            descriptor_pointers.append(pointer)
            kind = (
                ScientificArtifactKind.SOC_HAMILTONIAN_GRAPH
                if descriptor.soc_mode is UniHamSocMode.SOC
                else ScientificArtifactKind.NON_SOC_HAMILTONIAN_GRAPH
            )
            produced.append(
                make_scientific_task_artifact(
                    candidate_id=task.candidate_id,
                    producer_task_id=task.task_id,
                    kind=kind,
                    pointer=pointer,
                    is_mock=False,
                )
            )
        summary_ref = self.artifact_store.write_json(
            f"{base}/precomputed-input-import-summary.json",
            {
                "catalog_id": bundle.catalog_id,
                "catalog_version": bundle.catalog_version,
                "candidate_id": task.candidate_id,
                "descriptor_sha256s": sorted(
                    item.sha256 for item in descriptor_pointers
                ),
                "executor_id": self.executor_id,
                "scientific_conclusion": False,
            },
            immutable=True,
        )
        return ModelExecutionReceipt(
            task_id=task.task_id,
            status="SUCCEEDED",
            verdict=ScientificEvidenceVerdict.INCONCLUSIVE,
            tested_claim_ids=task.requested_observables,
            evidence_level=task.required_evidence_level,
            result_artifact=_pointer(summary_ref),
            consumed_artifact_ids=tuple(
                item.artifact_id for item in binding.consumed_artifacts
            ),
            produced_artifacts=tuple(
                sorted(produced, key=lambda item: item.artifact_id)
            ),
            runtime_provenance=dict(
                sorted(
                    {
                        "catalog_id": bundle.catalog_id,
                        "catalog_version": bundle.catalog_version,
                        "executor_id": self.executor_id,
                        "source_structure_sha256": binding.source_structure.sha256,
                    }.items()
                )
            ),
            reason_codes=("PRECOMPUTED_GRAPH_INPUTS_IMPORTED",),
            real_execution=True,
        )

    @staticmethod
    def _validate_lineage(
        binding: TaskExecutionBinding,
        bundle: PrecomputedUniHamInputBundle,
    ) -> None:
        task = binding.proposed_task
        request = bundle.request
        if request.candidate_id != task.candidate_id:
            raise ValueError("precomputed input candidate differs from DAG binding")
        if request.input_structure.sha256 != binding.source_structure.sha256:
            raise ValueError("precomputed input structure differs from DAG binding")

    @staticmethod
    def _descriptor(
        bundle: PrecomputedUniHamInputBundle,
        mode: UniHamSocMode,
    ) -> PrecomputedUniHamGraphDescriptor:
        if mode is UniHamSocMode.SOC:
            graph = bundle.request.soc_graph
            manifest = bundle.soc_manifest
        else:
            graph = bundle.request.non_soc_graph
            manifest = bundle.non_soc_manifest
        return PrecomputedUniHamGraphDescriptor(
            catalog_id=bundle.catalog_id,
            catalog_version=bundle.catalog_version,
            candidate_id=bundle.request.candidate_id,
            source_structure_sha256=bundle.request.input_structure.sha256,
            soc_mode=mode,
            graph_bundle=graph,
            manifest=manifest,
            reviewed_by=bundle.reviewed_by,
        )


@dataclass(frozen=True)
class UniHamScientificExecutor:
    artifact_store: LocalArtifactStore
    client: UniHamRemoteRunner | UniHamRemoteClient
    bundle_resolver: Callable[[TaskExecutionBinding], PrecomputedUniHamInputBundle]
    benchmark_policy: UniHamBenchmarkPolicy = UniHamBenchmarkPolicy()
    worker_limits: UniHamWorkerLimits = UniHamWorkerLimits()
    executor_id: str = "uniham-remote-scientific-executor-v1"

    def execute(self, *, binding: TaskExecutionBinding) -> ModelExecutionReceipt:
        task = binding.proposed_task
        if task.task_kind is not ScientificTaskKind.ML_HAMILTONIAN_SOC:
            raise ValueError("Uni-HamGNN executor requires a SOC Hamiltonian task")
        if task.produced_artifact_kinds != (ScientificArtifactKind.ML_SOC_HAMILTONIAN,):
            raise ValueError("Uni-HamGNN executor requires one SOC Hamiltonian output")
        bundle = self.bundle_resolver(binding)
        self._validate_binding(binding, bundle)
        benchmark_status, benchmark_evidence, _benchmark, warning = (
            audit_uniham_benchmark(bundle.request, self.benchmark_policy)
        )
        if benchmark_evidence == "NONE":
            if task.required_evidence_level is not ScientificEvidenceLevel.NONE:
                raise ValueError(
                    "unbenchmarked Uni-HamGNN task must request NONE evidence"
                )
            evidence_level = ScientificEvidenceLevel.NONE
        else:
            if task.required_evidence_level not in {
                ScientificEvidenceLevel.NONE,
                ScientificEvidenceLevel.L0_PARSED,
                ScientificEvidenceLevel.L1_RETRIEVED,
                ScientificEvidenceLevel.L2_ML_SCREENED,
            }:
                raise ValueError("Uni-HamGNN task exceeds the ML evidence ceiling")
            evidence_level = task.required_evidence_level

        result = self.client.run(
            bundle.request,
            non_soc_manifest=bundle.non_soc_manifest,
            soc_manifest=bundle.soc_manifest,
            limits=self.worker_limits,
        )
        if result.hamiltonian_pointer.sha256 not in {
            item.local_pointer.sha256 for item in result.local_artifacts
        }:
            raise ValueError("Uni-HamGNN Hamiltonian is not in the frozen output set")
        base = f"scientific_loop/tasks/{task.task_id}/{binding.binding_id}"
        summary_ref = self.artifact_store.write_json(
            f"{base}/uniham-execution-summary.json",
            {
                "benchmark_status": benchmark_status,
                "executor_id": self.executor_id,
                "hamiltonian_sha256": result.hamiltonian_pointer.sha256,
                "remote_result_id": result.result_id,
                "scientific_conclusion": False,
                "warning": warning,
            },
            immutable=True,
        )
        artifact = make_scientific_task_artifact(
            candidate_id=task.candidate_id,
            producer_task_id=task.task_id,
            kind=ScientificArtifactKind.ML_SOC_HAMILTONIAN,
            pointer=result.hamiltonian_pointer,
            is_mock=False,
        )
        reason = (
            "UNIHAM_HAMILTONIAN_GENERATED_BENCHMARKED"
            if benchmark_status == "VALIDATED"
            else "UNIHAM_HAMILTONIAN_GENERATED_UNBENCHMARKED"
        )
        runtime = result.worker_response.runtime_provenance
        return ModelExecutionReceipt(
            task_id=task.task_id,
            status="SUCCEEDED",
            verdict=ScientificEvidenceVerdict.INCONCLUSIVE,
            tested_claim_ids=task.requested_observables,
            evidence_level=evidence_level,
            result_artifact=_pointer(summary_ref),
            consumed_artifact_ids=tuple(
                item.artifact_id for item in binding.consumed_artifacts
            ),
            produced_artifacts=(artifact,),
            runtime_provenance=dict(
                sorted(
                    {
                        "benchmark_status": benchmark_status,
                        "cuda_device_name": (
                            runtime.cuda_device_name if runtime is not None else None
                        ),
                        "executor_id": self.executor_id,
                        "hamgnn_source_revision": bundle.request.hamgnn_source_revision,
                        "model_id": bundle.request.model_id,
                        "operation_key": result.plan.operation_key,
                        "remote_host_alias": result.host_alias,
                        "source_structure_sha256": binding.source_structure.sha256,
                    }.items()
                )
            ),
            reason_codes=(reason,),
            real_execution=True,
        )

    def _validate_binding(
        self,
        binding: TaskExecutionBinding,
        bundle: PrecomputedUniHamInputBundle,
    ) -> None:
        task = binding.proposed_task
        request = bundle.request
        if request.candidate_id != task.candidate_id:
            raise ValueError("Uni-HamGNN candidate differs from DAG binding")
        if request.model_id != task.model_id:
            raise ValueError("Uni-HamGNN model differs from audited capability")
        if request.input_structure.sha256 != binding.source_structure.sha256:
            raise ValueError("Uni-HamGNN structure differs from DAG binding")
        descriptors: dict[UniHamSocMode, PrecomputedUniHamGraphDescriptor] = {}
        for item in binding.consumed_artifacts:
            if item.kind not in {
                ScientificArtifactKind.NON_SOC_HAMILTONIAN_GRAPH,
                ScientificArtifactKind.SOC_HAMILTONIAN_GRAPH,
            }:
                continue
            if not self.artifact_store.exists_with_hash(
                item.pointer.uri,
                item.pointer.sha256,
            ):
                raise ValueError("precomputed graph descriptor failed integrity")
            descriptor = PrecomputedUniHamGraphDescriptor.model_validate_json(
                self.artifact_store.read_bytes(item.pointer.uri)
            )
            descriptors[descriptor.soc_mode] = descriptor
        if set(descriptors) != {UniHamSocMode.NON_SOC, UniHamSocMode.SOC}:
            raise ValueError("Uni-HamGNN requires both imported graph descriptors")
        expected = {
            UniHamSocMode.NON_SOC: bundle.request.non_soc_graph,
            UniHamSocMode.SOC: bundle.request.soc_graph,
        }
        if any(
            descriptors[mode].graph_bundle != graph for mode, graph in expected.items()
        ):
            raise ValueError("imported graph descriptors differ from execution bundle")


@dataclass(frozen=True)
class UniHamBandScientificExecutor:
    """Calculate and assess ML bands without claiming missing orbital evidence."""

    artifact_store: LocalArtifactStore
    client: UniHamBandRemoteRunner | UniHamBandRemoteClient
    request_factory: Callable[[TaskExecutionBinding], UniHamBandRequest]
    executor_id: str = "uniham-band-scientific-executor-v1"

    def execute(self, *, binding: TaskExecutionBinding) -> ModelExecutionReceipt:
        task = binding.proposed_task
        if task.task_kind is not ScientificTaskKind.BAND_ORBITAL_ANALYSIS:
            raise ValueError("Uni-HamGNN band executor got an incompatible task")
        if set(task.produced_artifact_kinds) != {
            ScientificArtifactKind.SOC_BAND_STRUCTURE,
            ScientificArtifactKind.FLAT_BAND_ASSESSMENT,
        }:
            raise ValueError(
                "Uni-HamGNN band executor requires band and assessment outputs"
            )
        if task.required_evidence_level is not ScientificEvidenceLevel.NONE:
            raise ValueError("unbenchmarked Uni-HamGNN band analysis is capped at NONE")
        request = self.request_factory(binding)
        self._validate_request(binding, request)
        parameters = UniHamFlatBandAnalysisParameters.model_validate(task.parameters)
        result = self.client.run(request)
        if not self.artifact_store.exists_with_hash(
            result.band_data_pointer.uri,
            result.band_data_pointer.sha256,
        ):
            raise ValueError("learned band data failed local integrity")
        parsed = parse_hamgnn_band_dat(
            self.artifact_store.read_bytes(result.band_data_pointer.uri).decode("utf-8")
        )
        target_band_index = (
            parameters.target_band_index
            if parameters.target_band_index is not None
            else _nearest_band_index(
                parsed.band_energies_ev, parameters.fermi_energy_ev
            )
        )
        high_symmetry_indices = tuple(
            sorted(
                {
                    min(
                        range(len(parsed.k_distances_inv_angstrom)),
                        key=lambda index: abs(
                            parsed.k_distances_inv_angstrom[index] - node
                        ),
                    )
                    for node in parsed.k_nodes_inv_angstrom
                }
            )
        )
        assessment = assess_flat_band(
            FlatBandAnalysisInput(
                fermi_energy_ev=parameters.fermi_energy_ev,
                k_distances_inv_angstrom=parsed.k_distances_inv_angstrom,
                high_symmetry_indices=high_symmetry_indices,
                band_energies_ev=parsed.band_energies_ev,
                target_band_index=target_band_index,
                orbital_projections=parameters.orbital_projections,
                contributor_sublattice_connected=(
                    parameters.contributor_sublattice_connected
                ),
                soc_explicit=True,
            ),
            parameters.policy,
        )
        base = f"scientific_loop/tasks/{task.task_id}/{binding.binding_id}"
        parsed_ref = self.artifact_store.write_json(
            f"{base}/parsed-soc-band-structure.json",
            {
                "band_data_sha256": result.band_data_pointer.sha256,
                "band_plot": result.band_plot_pointer.model_dump(mode="json"),
                "parsed": parsed.model_dump(mode="json"),
                "scientific_conclusion": False,
            },
            immutable=True,
        )
        assessment_ref = self.artifact_store.write_json(
            f"{base}/flat-band-assessment.json",
            {
                "assessment": assessment.model_dump(mode="json"),
                "band_result_id": result.result_id,
                "orbital_projection_available": bool(parameters.orbital_projections),
                "scientific_conclusion": False,
            },
            immutable=True,
        )
        artifacts = (
            make_scientific_task_artifact(
                candidate_id=task.candidate_id,
                producer_task_id=task.task_id,
                kind=ScientificArtifactKind.SOC_BAND_STRUCTURE,
                pointer=_pointer(parsed_ref),
                is_mock=False,
            ),
            make_scientific_task_artifact(
                candidate_id=task.candidate_id,
                producer_task_id=task.task_id,
                kind=ScientificArtifactKind.FLAT_BAND_ASSESSMENT,
                pointer=_pointer(assessment_ref),
                is_mock=False,
            ),
        )
        verdict = (
            ScientificEvidenceVerdict.CONTRADICTS
            if assessment.verdict is ValidationVerdict.FAIL
            else ScientificEvidenceVerdict.INCONCLUSIVE
        )
        return ModelExecutionReceipt(
            task_id=task.task_id,
            status="SUCCEEDED",
            verdict=verdict,
            tested_claim_ids=task.requested_observables,
            evidence_level=ScientificEvidenceLevel.NONE,
            result_artifact=_pointer(assessment_ref),
            consumed_artifact_ids=tuple(
                item.artifact_id for item in binding.consumed_artifacts
            ),
            produced_artifacts=tuple(
                sorted(artifacts, key=lambda item: item.artifact_id)
            ),
            runtime_provenance=dict(
                sorted(
                    {
                        "band_calculator_sha256": request.band_calculator_sha256,
                        "band_operation_key": result.plan.operation_key,
                        "executor_id": self.executor_id,
                        "hamiltonian_operation_key": (
                            request.hamiltonian_operation_key
                        ),
                        "host_alias": result.host_alias,
                        "nk": request.nk,
                        "source_structure_sha256": binding.source_structure.sha256,
                    }.items()
                )
            ),
            reason_codes=assessment.reason_codes,
            real_execution=True,
        )

    def _validate_request(
        self,
        binding: TaskExecutionBinding,
        request: UniHamBandRequest,
    ) -> None:
        task = binding.proposed_task
        if request.candidate_id != task.candidate_id:
            raise ValueError("band request candidate differs from DAG binding")
        if request.input_structure.sha256 != binding.source_structure.sha256:
            raise ValueError("band request structure differs from DAG binding")
        hamiltonians = tuple(
            item.pointer
            for item in binding.consumed_artifacts
            if item.kind is ScientificArtifactKind.ML_SOC_HAMILTONIAN
        )
        if len(hamiltonians) != 1:
            raise ValueError("band task requires exactly one learned SOC Hamiltonian")
        if (
            hamiltonians[0].sha256 != request.hamiltonian.sha256
            or hamiltonians[0].size_bytes != request.hamiltonian.size_bytes
        ):
            raise ValueError("band request Hamiltonian differs from DAG Artifact")
        soc_graphs = tuple(
            item.pointer
            for item in binding.consumed_artifacts
            if item.kind is ScientificArtifactKind.SOC_HAMILTONIAN_GRAPH
        )
        if len(soc_graphs) != 1 or not self.artifact_store.exists_with_hash(
            soc_graphs[0].uri,
            soc_graphs[0].sha256,
        ):
            raise ValueError("band task requires one intact SOC graph descriptor")
        descriptor = PrecomputedUniHamGraphDescriptor.model_validate_json(
            self.artifact_store.read_bytes(soc_graphs[0].uri)
        )
        if (
            descriptor.graph_bundle != request.soc_graph
            or descriptor.manifest != request.soc_manifest
        ):
            raise ValueError("band request graph differs from imported DAG Artifact")


def _nearest_band_index(
    bands: tuple[tuple[float, ...], ...],
    fermi_energy_ev: float,
) -> int:
    return min(
        range(len(bands)),
        key=lambda index: (
            min(abs(energy - fermi_energy_ev) for energy in bands[index]),
            max(bands[index]) - min(bands[index]),
            index,
        ),
    )


def _pointer(reference: ArtifactRef) -> ArtifactPointerV1:
    return ArtifactPointerV1(
        uri=reference.uri,
        sha256=reference.sha256,
        size_bytes=reference.size_bytes,
        media_type=reference.media_type,
    )
