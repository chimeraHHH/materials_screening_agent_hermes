"""C2DB band-data import and local flat-band assessment for the scientific DAG."""

from __future__ import annotations

import gzip
from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal

import numpy as np
from pydantic import Field, model_validator

from material_agent.inspiration.models import ArtifactPointerV1, StrictModel
from material_agent.integration.electronic_structure import (
    BandOrbitalProjection,
    FlatBandAnalysisInput,
    FlatBandPolicy,
    TwoDStructureAssessment,
    ValidationVerdict,
    assess_flat_band,
    parse_c2db_plotly_band_structure,
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
from material_agent.retrieval.storage import LocalArtifactStore


class C2DBBandBundle(StrictModel):
    material_id: str = Field(min_length=1, max_length=128)
    method: str = Field(min_length=1, max_length=128)
    source_url: str = Field(pattern=r"^https://c2db\.fysik\.dtu\.dk/")
    source_structure: ArtifactPointerV1
    band_data: ArtifactPointerV1
    energy_reference: Literal["VBM"] = "VBM"
    band_gap_ev: float | None = Field(default=None, ge=0)
    trace_names: tuple[str, ...] = Field(min_length=1, max_length=8)

    @model_validator(mode="after")
    def canonical_trace_names(self) -> C2DBBandBundle:
        if self.trace_names != tuple(sorted(set(self.trace_names))):
            raise ValueError("C2DB trace names must be sorted and unique")
        return self


class C2DBFlatBandAnalysisParameters(StrictModel):
    fermi_reference: Literal["EXPLICIT", "MIDGAP_FROM_DATABASE"] = (
        "MIDGAP_FROM_DATABASE"
    )
    fermi_energy_ev: float | None = None
    target_band_index: int | None = Field(default=None, ge=0)
    trace_name: str = Field(default="PBE no SOC", min_length=1, max_length=128)
    orbital_projections: tuple[BandOrbitalProjection, ...] = ()
    contributor_sublattice_connected: bool | None = None
    policy: FlatBandPolicy

    @model_validator(mode="after")
    def validate_fermi_reference(self) -> C2DBFlatBandAnalysisParameters:
        if (self.fermi_reference == "EXPLICIT") != (
            self.fermi_energy_ev is not None
        ):
            raise ValueError(
                "explicit C2DB Fermi reference requires exactly one energy"
            )
        return self


@dataclass(frozen=True)
class C2DBBandInputImportExecutor:
    artifact_store: LocalArtifactStore
    bundle_factory: Callable[[TaskExecutionBinding], C2DBBandBundle]
    executor_id: str = "c2db-band-input-import-executor-v1"

    def execute(self, *, binding: TaskExecutionBinding) -> ModelExecutionReceipt:
        task = binding.proposed_task
        if task.task_kind is not ScientificTaskKind.DATABASE_BAND_DATA_IMPORT:
            raise ValueError("C2DB import executor received an incompatible task")
        if task.produced_artifact_kinds != (
            ScientificArtifactKind.DATABASE_BAND_STRUCTURE,
        ):
            raise ValueError("C2DB import executor requires its exact typed output")
        if task.required_evidence_level is not ScientificEvidenceLevel.L1_RETRIEVED:
            raise ValueError("C2DB imported data must retain L1 retrieved evidence")
        bundle = self.bundle_factory(binding)
        self._validate_bundle(binding, bundle)
        base = f"scientific_loop/tasks/{task.task_id}/{binding.binding_id}"
        summary = self.artifact_store.write_json(
            f"{base}/c2db-band-import-summary.json",
            {
                "band_data": bundle.band_data.model_dump(mode="json"),
                "band_gap_ev": bundle.band_gap_ev,
                "energy_reference": bundle.energy_reference,
                "material_id": bundle.material_id,
                "method": bundle.method,
                "scientific_conclusion": False,
                "source_url": bundle.source_url,
                "trace_names": bundle.trace_names,
            },
            immutable=True,
        )
        artifact = make_scientific_task_artifact(
            candidate_id=task.candidate_id,
            producer_task_id=task.task_id,
            kind=ScientificArtifactKind.DATABASE_BAND_STRUCTURE,
            pointer=bundle.band_data,
            is_mock=False,
        )
        return ModelExecutionReceipt(
            task_id=task.task_id,
            status="SUCCEEDED",
            verdict=ScientificEvidenceVerdict.INCONCLUSIVE,
            tested_claim_ids=task.requested_observables,
            evidence_level=ScientificEvidenceLevel.L1_RETRIEVED,
            result_artifact=ArtifactPointerV1(
                uri=summary.uri,
                sha256=summary.sha256,
                size_bytes=summary.size_bytes,
                media_type=summary.media_type,
            ),
            consumed_artifact_ids=tuple(
                item.artifact_id for item in binding.consumed_artifacts
            ),
            produced_artifacts=(artifact,),
            runtime_provenance={
                "band_data_sha256": bundle.band_data.sha256,
                "executor_id": self.executor_id,
                "material_id": bundle.material_id,
                "method": bundle.method,
            },
            reason_codes=("C2DB_BAND_DATA_IMPORTED",),
            real_execution=True,
        )

    def _validate_bundle(
        self,
        binding: TaskExecutionBinding,
        bundle: C2DBBandBundle,
    ) -> None:
        if bundle.source_structure.sha256 != binding.source_structure.sha256:
            raise ValueError("C2DB band bundle structure differs from DAG binding")
        for pointer in (bundle.source_structure, bundle.band_data):
            if not self.artifact_store.exists_with_hash(pointer.uri, pointer.sha256):
                raise ValueError("C2DB bundle Artifact failed integrity")


@dataclass(frozen=True)
class C2DBFlatBandScientificExecutor:
    artifact_store: LocalArtifactStore
    bundle_factory: Callable[[TaskExecutionBinding], C2DBBandBundle]
    executor_id: str = "c2db-flat-band-scientific-executor-v1"
    maximum_decompressed_bytes: int = 64 * 1024 * 1024

    def execute(self, *, binding: TaskExecutionBinding) -> ModelExecutionReceipt:
        task = binding.proposed_task
        if task.task_kind is not ScientificTaskKind.BAND_ORBITAL_ANALYSIS:
            raise ValueError("C2DB band executor received an incompatible task")
        if set(task.produced_artifact_kinds) != {
            ScientificArtifactKind.FLAT_BAND_ASSESSMENT,
            ScientificArtifactKind.NON_SOC_BAND_STRUCTURE,
        }:
            raise ValueError("C2DB band executor requires band and assessment outputs")
        if task.required_evidence_level is not ScientificEvidenceLevel.L1_RETRIEVED:
            raise ValueError("C2DB band assessment must retain L1 retrieved evidence")
        bundle = self.bundle_factory(binding)
        band_artifacts = tuple(
            item
            for item in binding.consumed_artifacts
            if item.kind is ScientificArtifactKind.DATABASE_BAND_STRUCTURE
        )
        if len(band_artifacts) != 1 or band_artifacts[0].pointer != bundle.band_data:
            raise ValueError("C2DB band executor did not consume the exact imported data")
        if bundle.source_structure.sha256 != binding.source_structure.sha256:
            raise ValueError("C2DB band bundle structure differs from DAG binding")
        parameters = C2DBFlatBandAnalysisParameters.model_validate(task.parameters)
        if parameters.trace_name not in bundle.trace_names:
            raise ValueError("requested C2DB trace is absent from the reviewed bundle")
        compressed = self.artifact_store.read_bytes(bundle.band_data.uri)
        try:
            raw = gzip.decompress(compressed)
        except (OSError, EOFError) as exc:
            raise ValueError("C2DB band Artifact is not valid gzip") from exc
        if len(raw) > self.maximum_decompressed_bytes:
            raise ValueError("C2DB band Artifact exceeds decompression budget")
        parsed = parse_c2db_plotly_band_structure(
            raw,
            trace_name=parameters.trace_name,
        )
        fermi_energy_ev = self._fermi_energy(parameters, bundle)
        connected = parameters.contributor_sublattice_connected
        if connected is None:
            connected = self._connected_sublattice(binding)
        target = (
            parameters.target_band_index
            if parameters.target_band_index is not None
            else _nearest_band_index(
                parsed.band_energies_ev,
                fermi_energy_ev,
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
                fermi_energy_ev=fermi_energy_ev,
                k_distances_inv_angstrom=parsed.k_distances_inv_angstrom,
                high_symmetry_indices=high_symmetry_indices,
                band_energies_ev=parsed.band_energies_ev,
                target_band_index=target,
                orbital_projections=parameters.orbital_projections,
                contributor_sublattice_connected=connected,
                soc_explicit=False,
            ),
            parameters.policy,
        )
        base = f"scientific_loop/tasks/{task.task_id}/{binding.binding_id}"
        parsed_ref = self.artifact_store.write_json(
            f"{base}/parsed-c2db-band-structure.json",
            {
                "band_data_sha256": bundle.band_data.sha256,
                "material_id": bundle.material_id,
                "method": bundle.method,
                "parsed": parsed.model_dump(mode="json"),
                "scientific_conclusion": False,
                "source_url": bundle.source_url,
                "trace_name": parameters.trace_name,
            },
            immutable=True,
        )
        assessment_ref = self.artifact_store.write_json(
            f"{base}/flat-band-assessment.json",
            {
                "assessment": assessment.model_dump(mode="json"),
                "database_evidence_only": True,
                "energy_reference": bundle.energy_reference,
                "fermi_energy_ev": fermi_energy_ev,
                "fermi_reference": parameters.fermi_reference,
                "orbital_projection_available": bool(parameters.orbital_projections),
                "scientific_conclusion": False,
            },
            immutable=True,
        )
        parsed_pointer = _pointer(parsed_ref)
        assessment_pointer = _pointer(assessment_ref)
        artifacts = tuple(
            sorted(
                (
                    make_scientific_task_artifact(
                        candidate_id=task.candidate_id,
                        producer_task_id=task.task_id,
                        kind=ScientificArtifactKind.NON_SOC_BAND_STRUCTURE,
                        pointer=parsed_pointer,
                        is_mock=False,
                    ),
                    make_scientific_task_artifact(
                        candidate_id=task.candidate_id,
                        producer_task_id=task.task_id,
                        kind=ScientificArtifactKind.FLAT_BAND_ASSESSMENT,
                        pointer=assessment_pointer,
                        is_mock=False,
                    ),
                ),
                key=lambda item: item.artifact_id,
            )
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
            evidence_level=ScientificEvidenceLevel.L1_RETRIEVED,
            result_artifact=assessment_pointer,
            consumed_artifact_ids=tuple(
                item.artifact_id for item in binding.consumed_artifacts
            ),
            produced_artifacts=artifacts,
            runtime_provenance={
                "band_gap_ev": bundle.band_gap_ev,
                "band_data_sha256": bundle.band_data.sha256,
                "executor_id": self.executor_id,
                "material_id": bundle.material_id,
                "method": bundle.method,
                "trace_name": parameters.trace_name,
            },
            reason_codes=assessment.reason_codes,
            real_execution=True,
        )

    @staticmethod
    def _fermi_energy(
        parameters: C2DBFlatBandAnalysisParameters,
        bundle: C2DBBandBundle,
    ) -> float:
        if parameters.fermi_reference == "EXPLICIT":
            assert parameters.fermi_energy_ev is not None
            return parameters.fermi_energy_ev
        if bundle.energy_reference != "VBM" or bundle.band_gap_ev is None:
            raise ValueError(
                "mid-gap Fermi reference requires a VBM-referenced database gap"
            )
        return bundle.band_gap_ev / 2.0

    def _connected_sublattice(self, binding: TaskExecutionBinding) -> bool | None:
        assessments = tuple(
            item
            for item in binding.consumed_artifacts
            if item.kind is ScientificArtifactKind.TWO_D_STRUCTURE_ASSESSMENT
        )
        if not assessments:
            return None
        if len(assessments) != 1:
            raise ValueError("multiple 2D assessments make connectivity ambiguous")
        assessment = TwoDStructureAssessment.model_validate_json(
            self.artifact_store.read_bytes(assessments[0].pointer.uri)
        )
        return assessment.contributor_connected


def _nearest_band_index(
    bands: tuple[tuple[float, ...], ...],
    fermi_energy_ev: float,
) -> int:
    energies = np.asarray(bands, dtype=float)
    distances = np.min(np.abs(energies - fermi_energy_ev), axis=1)
    return int(np.argmin(distances))


def _pointer(reference) -> ArtifactPointerV1:
    return ArtifactPointerV1(
        uri=reference.uri,
        sha256=reference.sha256,
        size_bytes=reference.size_bytes,
        media_type=reference.media_type,
    )
