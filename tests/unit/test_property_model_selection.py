from __future__ import annotations

import hashlib
import sys
from pathlib import Path

from material_agent.ml_screening.models import ArtifactPointer
from material_agent.ml_screening.property_catalog import reviewed_property_model_families
from material_agent.ml_screening.property_models import (
    PropertyCapability,
    PropertyInputKind,
    PropertyModelAvailability,
    PropertyModelFamily,
    PropertyModelRegistry,
    PropertyModelSpec,
    PropertyNeed,
    PropertyPredictionRequest,
    select_property_model,
)
from material_agent.ml_screening.property_execution import build_property_execution_plan
from material_agent.ml_screening.property_client import PropertyPredictionFlowRunner, PropertyProcessError, PropertySubprocessClient
from material_agent.ml_screening.property_worker import execute
from material_agent.retrieval.storage import LocalArtifactStore


def _sha(label: str) -> str:
    return hashlib.sha256(label.encode()).hexdigest()


def _pointer(label: str) -> ArtifactPointer:
    return ArtifactPointer(uri=f"artifact://property-models/{label}", sha256=_sha(label))


def _model(
    model_id: str,
    family: PropertyModelFamily,
    input_kind: PropertyInputKind,
    property_id: str = "band_gap_ev",
    *,
    ready: bool = True,
) -> PropertyModelSpec:
    return PropertyModelSpec(
        model_id=model_id,
        family=family,
        display_name=model_id,
        source_repository=f"https://github.com/example/{model_id}",
        source_revision="a" * 40,
        license="MIT",
        input_kind=input_kind,
        supported_properties=[
            PropertyCapability(
                property_id=property_id,
                unit="eV",
                training_dataset="fixture",
                target_label=property_id,
            )
        ],
        environment_lock=_pointer(f"{model_id}.lock"),
        checkpoint=_pointer(f"{model_id}.checkpoint") if ready else None,
        availability=(PropertyModelAvailability.READY if ready else PropertyModelAvailability.REGISTERED_ONLY),
        limitations=["fixture only"],
    )


def _request(*, structure: bool = True, preferred: list[str] | None = None) -> PropertyPredictionRequest:
    return PropertyPredictionRequest(
        project_id="project",
        run_id="run",
        candidate_id="candidate",
        need=PropertyNeed(property_id="band_gap_ev", unit="eV", preferred_model_ids=preferred or []),
        composition={"O": 2.0, "Si": 1.0},
        input_structure=_pointer("candidate.cif") if structure else None,
    )


def _with_assets(store: LocalArtifactStore, model: PropertyModelSpec) -> PropertyModelSpec:
    lock = store.write_text(f"property-models/{model.model_id}.lock", "fixture lock", immutable=True)
    checkpoint = store.write_text(f"property-models/{model.model_id}.checkpoint", "fixture checkpoint", immutable=True)
    return model.model_copy(
        update={
            "environment_lock": ArtifactPointer(uri=lock.uri, sha256=lock.sha256),
            "checkpoint": ArtifactPointer(uri=checkpoint.uri, sha256=checkpoint.sha256),
        }
    )


def test_catalog_contains_all_user_requested_model_families() -> None:
    assert {item.family for item in reviewed_property_model_families()} == set(PropertyModelFamily)
    crabnet = next(item for item in reviewed_property_model_families() if item.family is PropertyModelFamily.CRABNET)
    assert crabnet.input_kind is PropertyInputKind.COMPOSITION
    assert crabnet.published_properties == ()


def test_selector_uses_user_model_preference_after_property_and_input_checks() -> None:
    crystalformer = _model("crystalformer-bandgap", PropertyModelFamily.CRYSTALFORMER, PropertyInputKind.STRUCTURE_CIF)
    crystalframer = _model("crystalframer-bandgap", PropertyModelFamily.CRYSTALFRAMER, PropertyInputKind.STRUCTURE_CIF)
    selection = select_property_model(
        _request(preferred=[crystalframer.model_id]),
        PropertyModelRegistry(models=[crystalformer, crystalframer]),
    )
    assert selection.selected_model == crystalframer
    assert selection.selected_capability is not None
    assert selection.selected_capability.property_id == "band_gap_ev"


def test_selector_uses_composition_model_when_no_structure_is_available() -> None:
    crystalformer = _model("crystalformer-bandgap", PropertyModelFamily.CRYSTALFORMER, PropertyInputKind.STRUCTURE_CIF)
    crabnet = _model("crabnet-bandgap-head", PropertyModelFamily.CRABNET, PropertyInputKind.COMPOSITION)
    selection = select_property_model(_request(structure=False), PropertyModelRegistry(models=[crystalformer, crabnet]))
    assert selection.selected_model == crabnet
    assert selection.rejected_models[crystalformer.model_id] == "STRUCTURE_INPUT_REQUIRED"


def test_selector_refuses_source_only_registration_without_checkpoint() -> None:
    model = _model("ct-uae-bandgap", PropertyModelFamily.CT_UAE, PropertyInputKind.STRUCTURE_CIF, ready=False)
    selection = select_property_model(_request(), PropertyModelRegistry(models=[model]))
    assert selection.selected_model is None
    assert selection.rejected_models[model.model_id] == "MODEL_ASSETS_NOT_REGISTERED"


def test_execution_plan_binds_selected_model_and_verified_structure(tmp_path) -> None:
    store = LocalArtifactStore(tmp_path)
    structure = store.write_text(
        "candidates/structures/example.cif",
        "data_example\n_cell_length_a 3\n",
        "chemical/x-cif",
        immutable=True,
    )
    request = PropertyPredictionRequest(
        project_id="project",
        run_id="run",
        candidate_id="candidate",
        need=PropertyNeed(property_id="band_gap_ev", unit="eV"),
        composition={"O": 2.0, "Si": 1.0},
        input_structure=ArtifactPointer(uri=structure.uri, sha256=structure.sha256),
    )
    model = _with_assets(store, _model("crystalformer-bandgap", PropertyModelFamily.CRYSTALFORMER, PropertyInputKind.STRUCTURE_CIF))
    selection = select_property_model(request, PropertyModelRegistry(models=[model]))
    plan = build_property_execution_plan(request, selection, artifact_root=tmp_path)
    assert plan.input_structure is not None
    assert plan.input_structure.sha256 == structure.sha256
    assert plan.output_sandbox_relative_path.endswith("/worker-output")


def test_execution_plan_refuses_missing_ready_model_assets(tmp_path) -> None:
    request = _request(structure=False)
    model = _model("crabnet-bandgap-head", PropertyModelFamily.CRABNET, PropertyInputKind.COMPOSITION)
    selection = select_property_model(request, PropertyModelRegistry(models=[model]))
    import pytest

    with pytest.raises(ValueError, match="environment lock"):
        build_property_execution_plan(request, selection, artifact_root=tmp_path)


def test_worker_fails_closed_for_unimplemented_model_family(tmp_path) -> None:
    store = LocalArtifactStore(tmp_path)
    structure = store.write_text("candidates/structures/example.cif", "data_example\n", "chemical/x-cif", immutable=True)
    request = PropertyPredictionRequest(
        project_id="project", run_id="run", candidate_id="candidate",
        need=PropertyNeed(property_id="band_gap_ev", unit="eV"),
        composition={"O": 2.0, "Si": 1.0},
        input_structure=ArtifactPointer(uri=structure.uri, sha256=structure.sha256),
    )
    model = _with_assets(store, _model("crystalformer-bandgap", PropertyModelFamily.CRYSTALFORMER, PropertyInputKind.STRUCTURE_CIF))
    plan = build_property_execution_plan(request, select_property_model(request, PropertyModelRegistry(models=[model])), artifact_root=tmp_path)
    from material_agent.ml_screening.property_execution import PropertyWorkerRequest

    response = execute(PropertyWorkerRequest(plan=plan), root=tmp_path, ct_uae_source_root=None)
    assert response.status == "FAILED"
    assert response.errors[0].startswith("ADAPTER_UNAVAILABLE:")


def test_flow_persists_failed_response_and_is_idempotent(tmp_path) -> None:
    class FailingClient:
        artifact_root = tmp_path.resolve()
        calls = 0

        def run(self, worker_request):
            self.calls += 1
            plan = worker_request.plan
            from material_agent.ml_screening.property_execution import PropertyWorkerResponse

            return PropertyWorkerResponse(
                operation_key=plan.operation_key,
                model_id=plan.selected_model.model_id,
                property_id=plan.selected_capability.property_id,
                unit=plan.selected_capability.unit,
                status="FAILED",
                is_mock=False,
                errors=["ADAPTER_UNAVAILABLE: fixture"],
            )

    store = LocalArtifactStore(tmp_path)
    request = _request(structure=False)
    model = _with_assets(store, _model("crabnet-bandgap-head", PropertyModelFamily.CRABNET, PropertyInputKind.COMPOSITION))
    client = FailingClient()
    runner = PropertyPredictionFlowRunner(
        artifact_store=store,
        client=client,
        registry=PropertyModelRegistry(models=[model]),
    )
    first = runner.execute(request)
    second = runner.execute(request)
    assert first.status == "FAILED"
    assert second == first
    assert client.calls == 1
    assert first.plan_uri.startswith("artifact://plans/run/stages/ml/property-")


def test_client_refuses_preexisting_output_sandbox(tmp_path) -> None:
    store = LocalArtifactStore(tmp_path)
    structure = store.write_text("candidates/structures/example.cif", "data_example\n", "chemical/x-cif", immutable=True)
    request = PropertyPredictionRequest(
        project_id="project", run_id="run", candidate_id="candidate",
        need=PropertyNeed(property_id="band_gap_ev", unit="eV"),
        composition={"O": 2.0, "Si": 1.0},
        input_structure=ArtifactPointer(uri=structure.uri, sha256=structure.sha256),
    )
    model = _with_assets(store, _model("crystalformer-bandgap", PropertyModelFamily.CRYSTALFORMER, PropertyInputKind.STRUCTURE_CIF))
    plan = build_property_execution_plan(request, select_property_model(request, PropertyModelRegistry(models=[model])), artifact_root=tmp_path)
    tmp_path.joinpath(*plan.output_sandbox_relative_path.split("/")).mkdir(parents=True)
    from material_agent.ml_screening.property_execution import PropertyWorkerRequest

    client = PropertySubprocessClient(worker_python=Path(sys.executable), artifact_root=tmp_path)
    import pytest

    with pytest.raises(PropertyProcessError, match="must not already exist"):
        client.run(PropertyWorkerRequest(plan=plan))
