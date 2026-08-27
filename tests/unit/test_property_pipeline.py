from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from material_agent.ml_screening.alignn_models import (
    AlignnArtifact,
    AlignnInferenceRequest,
    AlignnProperty,
    AlignnResult,
)
from material_agent.ml_screening.models import ArtifactPointer
from material_agent.ml_screening.property_execution import PropertyPredictionResult
from material_agent.ml_screening.property_models import (
    PropertyCapability,
    PropertyInputKind,
    PropertyModelAvailability,
    PropertyModelFamily,
    PropertyModelRegistry,
    PropertyModelSpec,
    PropertyNeed,
    PropertyPredictionRequest,
)
from material_agent.ml_screening.property_pipeline import (
    PostRelaxationPropertyChain,
    PropertyPredictionChainRequest,
)
from material_agent.retrieval.storage import LocalArtifactStore


def _sha(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _ct_registry(store: LocalArtifactStore) -> PropertyModelRegistry:
    lock = store.write_text("models/ct.lock", "lock", immutable=True)
    checkpoint = store.write_text("models/ct.tar", "checkpoint", immutable=True)
    return PropertyModelRegistry(
        models=[
            PropertyModelSpec(
                model_id="ct-uae-fixture",
                family=PropertyModelFamily.CT_UAE,
                display_name="ct-UAE fixture",
                source_repository="https://github.com/example/ct-uae-fixture",
                source_revision="a" * 40,
                license="MIT",
                input_kind=PropertyInputKind.STRUCTURE_CIF,
                supported_properties=[
                    PropertyCapability(
                        property_id="band_gap_ev",
                        unit="eV",
                        training_dataset="fixture",
                        target_label="fixture band gap",
                    )
                ],
                environment_lock=ArtifactPointer(uri=lock.uri, sha256=lock.sha256),
                checkpoint=ArtifactPointer(uri=checkpoint.uri, sha256=checkpoint.sha256),
                availability=PropertyModelAvailability.READY,
                limitations=["fixture only"],
            )
        ]
    )


class _PropertyFlow:
    def __init__(self, store: LocalArtifactStore) -> None:
        self.store = store
        self.calls = 0

    def execute(self, request):
        self.calls += 1
        return PropertyPredictionResult(
            project_id=request.project_id,
            run_id=request.run_id,
            candidate_id=request.candidate_id,
            operation_key="1" * 64,
            plan_uri="artifact://plans/ct.json",
            plan_sha256="2" * 64,
            model_id="ct-uae-fixture",
            property_id="band_gap_ev",
            value=0.12,
            unit="eV",
            status="SUCCEEDED",
            is_mock=True,
        )


class _AlignnFlow:
    def __init__(self, store: LocalArtifactStore) -> None:
        self.store = store
        self.calls = 0

    def execute(self, request):
        self.calls += 1
        return AlignnResult(
            project_id=request.project_id,
            run_id=request.run_id,
            candidate_id=request.candidate_id,
            operation_key="3" * 64,
            plan_uri="artifact://plans/alignn.json",
            plan_sha256="4" * 64,
            target_property=AlignnProperty.JARVIS_OPTB88VDW_BAND_GAP,
            value=0.18,
            unit="eV",
            target_method="JARVIS-DFT OptB88-vdW band gap",
            status="SUCCEEDED",
            is_mock=True,
        )


def _request(tmp_path: Path) -> tuple[LocalArtifactStore, PropertyPredictionChainRequest]:
    store = LocalArtifactStore(tmp_path)
    structure = store.write_text("relaxed/matterSim.cif", "relaxed structure", "chemical/x-cif", immutable=True)
    relaxed = ArtifactPointer(uri=structure.uri, sha256=structure.sha256)
    alignn_model = store.write_bytes("models/alignn.zip", b"zip", "application/zip", immutable=True)
    alignn_artifact = AlignnArtifact(
        artifact_uri=structure.uri,
        root_relative_path="relaxed/matterSim.cif",
        sha256=structure.sha256,
        size_bytes=structure.size_bytes,
        media_type="chemical/x-cif",
    )
    alignn_archive = AlignnArtifact(
        artifact_uri=alignn_model.uri,
        root_relative_path="models/alignn.zip",
        sha256=alignn_model.sha256,
        size_bytes=alignn_model.size_bytes,
        media_type="application/zip",
    )
    ct_request = PropertyPredictionRequest(
        project_id="project",
        run_id="run",
        candidate_id="candidate",
        need=PropertyNeed(property_id="band_gap_ev", unit="eV"),
        composition={"Fe": 1.0, "Se": 1.0},
        input_structure=relaxed,
    )
    alignn_request = AlignnInferenceRequest(
        project_id="project",
        run_id="run",
        candidate_id="candidate",
        input_structure=alignn_artifact,
        model_archive=alignn_archive,
        alignn_version="2025.4.1",
        environment_lock_sha256="5" * 64,
        source_revision="0123456789abcdef",
        is_mock=True,
    )
    return store, PropertyPredictionChainRequest(
        project_id="project",
        run_id="run",
        candidate_id="candidate",
        relaxation_method="MatterSim",
        relaxed_structure=relaxed,
        ct_uae_request=ct_request,
        ct_uae_registry=_ct_registry(store),
        alignn_request=alignn_request,
    )


def test_post_relaxation_chain_runs_both_models_from_same_cif(tmp_path: Path) -> None:
    store, request = _request(tmp_path)
    ct_flow = _PropertyFlow(store)
    alignn_flow = _AlignnFlow(store)

    result = PostRelaxationPropertyChain(store, ct_flow, alignn_flow).execute(request)

    assert result.status == "SUCCEEDED"
    assert result.relaxation_method == "MatterSim"
    assert result.ct_uae is not None and result.ct_uae.value == 0.12
    assert result.alignn is not None and result.alignn.value == 0.18
    assert ct_flow.calls == 1
    assert alignn_flow.calls == 1
    assert result.scientific_conclusion is False
    again = PostRelaxationPropertyChain(store, ct_flow, alignn_flow).execute(request)
    assert again == result
    assert ct_flow.calls == 1
    assert alignn_flow.calls == 1


def test_chain_rejects_mismatched_relaxed_structure(tmp_path: Path) -> None:
    store, request = _request(tmp_path)
    other = store.write_text("relaxed/other.cif", "other", "chemical/x-cif", immutable=True)
    with pytest.raises(ValueError, match="declared relaxed structure"):
        payload = request.model_dump(mode="json")
        payload["ct_uae_request"]["input_structure"] = {
            "uri": other.uri,
            "sha256": other.sha256,
        }
        PropertyPredictionChainRequest.model_validate(payload)
