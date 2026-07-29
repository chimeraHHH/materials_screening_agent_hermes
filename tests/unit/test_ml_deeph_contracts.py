from __future__ import annotations

import hashlib
from pathlib import Path

import pytest
from pydantic import ValidationError

from material_agent.ml_screening.deeph_models import (
    DeepHArtifactBundle,
    DeepHArtifactFile,
    DeepHCompatibility,
    DeepHInferenceRequest,
)
from material_agent.ml_screening.deeph_handoff import (
    deeph_request_from_chgnet_result,
)
from material_agent.ml_screening.deeph_planner import build_deeph_plan
from material_agent.ml_screening.adapters import (
    FakeMLModelAdapter,
    FakeMLWorker,
)
from material_agent.ml_screening.models import (
    MLCandidateResult,
)
from material_agent.ml_screening.real_resources import (
    AGENT02_PACKAGE_LOCK_SHA256,
    CHGNET_ADAPTER_VERSION,
    CHGNET_CHECKPOINT_SHA256,
)
from material_agent.retrieval.storage import LocalArtifactStore


def test_deeph_request_rejects_incompatible_basis_and_extra_fields(
    tmp_path: Path,
) -> None:
    request = _request(tmp_path)
    payload = request.model_dump(mode="json")
    payload["model_compatibility"]["basis_id"] = "different-basis"
    with pytest.raises(ValidationError, match="same interface"):
        DeepHInferenceRequest.model_validate(payload)

    payload = request.model_dump(mode="json")
    payload["benchmark_override"] = True
    with pytest.raises(ValidationError, match="Extra inputs"):
        DeepHInferenceRequest.model_validate(payload)


def test_deeph_contract_rejects_traversal_and_task_five(
    tmp_path: Path,
) -> None:
    request = _request(tmp_path)
    artifact = request.input_structure.model_dump(mode="json")
    artifact["root_relative_path"] = "../structure.cif"
    artifact["artifact_uri"] = "artifact://../structure.cif"
    with pytest.raises(ValidationError, match="traversal"):
        DeepHArtifactFile.model_validate(artifact)

    payload = request.model_dump(mode="json")
    payload["tasks"] = [1, 2, 3, 4, 5]
    with pytest.raises(ValidationError):
        DeepHInferenceRequest.model_validate(payload)


def test_deeph_plan_detects_hash_tampering_and_symlinks(
    tmp_path: Path,
) -> None:
    request = _request(tmp_path)
    plan = build_deeph_plan(request, artifact_root=tmp_path)
    assert plan.request == request
    assert len(plan.operation_key) == 64

    model_path = tmp_path / request.trained_model.files[0].root_relative_path
    model_path.write_bytes(b"tampered")
    with pytest.raises(ValueError, match="size mismatch|hash mismatch"):
        build_deeph_plan(request, artifact_root=tmp_path)

    model_path.write_bytes(b"model")
    overlap_path = tmp_path / request.overlap.files[0].root_relative_path
    overlap_path.unlink()
    target = tmp_path / "outside-overlap.h5"
    target.write_bytes(b"overlap")
    overlap_path.symlink_to(target)
    with pytest.raises(ValueError, match="symlink"):
        build_deeph_plan(request, artifact_root=tmp_path)


def test_deeph_handoff_uses_only_verified_real_chgnet_relaxed_structure(
    tmp_path: Path,
    ml_candidate_factory,
    ml_plan_factory,
    ml_model,
    ml_health,
    ml_policy,
) -> None:
    candidate_input = ml_candidate_factory("candidate-real-chgnet")
    plan = ml_plan_factory([candidate_input])
    record = FakeMLWorker(
        adapter=FakeMLModelAdapter(model=ml_model, health=ml_health),
        policy=ml_policy,
    ).generate_artifacts(plan).manifest[0]
    store = LocalArtifactStore(tmp_path)
    relaxed_ref = store.write_bytes(
        "candidates/structures/chgnet-relaxed.cif",
        b"real-chgnet-relaxed-structure",
        media_type="chemical/x-cif",
        immutable=True,
    )
    payload = record.candidate.model_dump(mode="json")
    identity = payload["execution_identity"]
    identity.update(
        {
            "checkpoint_sha256": CHGNET_CHECKPOINT_SHA256,
            "package_lock_sha256": AGENT02_PACKAGE_LOCK_SHA256,
            "adapter_version": CHGNET_ADAPTER_VERSION,
            "is_mock": False,
        }
    )
    payload["execution_status"] = "CONVERGED"
    payload["decision"] = "PASS"
    payload["evidence_level"] = "L2_ML_SCREENED"
    payload["applicability"]["eligible_for_real_inference"] = True
    payload["applicability"]["eligible_for_l2"] = True
    relaxation = payload["relaxation_result"]
    relaxation.update(
        {
            "output_structure_uri": relaxed_ref.uri,
            "output_structure_sha256": relaxed_ref.sha256,
            "checkpoint_sha256": CHGNET_CHECKPOINT_SHA256,
            "adapter_version": CHGNET_ADAPTER_VERSION,
            "execution_identity": identity,
            "is_mock": False,
        }
    )
    lineage = payload["structure_lineage"]
    lineage.update(
        {
            "structure_uri": relaxed_ref.uri,
            "structure_sha256": relaxed_ref.sha256,
            "checkpoint_sha256": CHGNET_CHECKPOINT_SHA256,
            "is_mock": False,
        }
    )
    for prop in payload["ml_properties"]:
        prop.update(
            {
                "checkpoint_sha256": CHGNET_CHECKPOINT_SHA256,
                "evidence_level": "L2_ML_SCREENED",
                "is_mock": False,
                "output_structure_id": relaxation["output_structure_id"],
            }
        )
    payload["recommended_downstream_structure_id"] = relaxation[
        "output_structure_id"
    ]
    candidate = MLCandidateResult.model_validate(payload)

    model = _write(tmp_path, "inputs/model/checkpoint.pkl", b"model")
    overlap = _write(tmp_path, "inputs/overlap/overlap.h5", b"overlap")
    compatibility = DeepHCompatibility(
        interface="openmx",
        basis_id="openmx-pa19-s2p2",
        dft_software_version="OpenMX-3.9",
    )
    request = deeph_request_from_chgnet_result(
        candidate,
        artifact_store=store,
        trained_model=DeepHArtifactBundle(
            root_relative_directory="inputs/model",
            files=[model],
        ),
        overlap=DeepHArtifactBundle(
            root_relative_directory="inputs/overlap",
            files=[overlap],
        ),
        model_compatibility=compatibility,
        overlap_compatibility=compatibility,
        overlap_structure_sha256=relaxed_ref.sha256,
        deeph_model_id="mock-deeph-model",
        deeph_source_revision="0123456789abcdef",
        is_mock=True,
    )
    assert request.input_structure.sha256 == relaxed_ref.sha256
    assert request.overlap_structure_sha256 == relaxed_ref.sha256

    with pytest.raises(ValueError, match="overlap must be calculated"):
        deeph_request_from_chgnet_result(
            candidate,
            artifact_store=store,
            trained_model=request.trained_model,
            overlap=request.overlap,
            model_compatibility=compatibility,
            overlap_compatibility=compatibility,
            overlap_structure_sha256="0" * 64,
            deeph_model_id="mock-deeph-model",
            deeph_source_revision="0123456789abcdef",
            is_mock=True,
        )


def _request(root: Path) -> DeepHInferenceRequest:
    structure = _write(root, "inputs/structure.cif", b"structure")
    model = _write(root, "inputs/model/checkpoint.pkl", b"model")
    overlap = _write(root, "inputs/overlap/overlap.h5", b"overlap")
    compatibility = DeepHCompatibility(
        interface="openmx",
        basis_id="openmx-pa19-s2p2",
        dft_software_version="OpenMX-3.9",
    )
    return DeepHInferenceRequest(
        project_id="project-deeph",
        run_id="run-deeph",
        candidate_id="candidate-1",
        input_structure=structure,
        trained_model=DeepHArtifactBundle(
            root_relative_directory="inputs/model",
            files=[model],
        ),
        overlap=DeepHArtifactBundle(
            root_relative_directory="inputs/overlap",
            files=[overlap],
        ),
        model_compatibility=compatibility,
        overlap_compatibility=compatibility,
        overlap_structure_sha256=structure.sha256,
        model_id="deeph-test-model",
        deeph_source_revision="0123456789abcdef",
        is_mock=True,
    )


def _write(root: Path, relative: str, payload: bytes) -> DeepHArtifactFile:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)
    return DeepHArtifactFile(
        artifact_uri=f"artifact://{relative}",
        root_relative_path=relative,
        sha256=hashlib.sha256(payload).hexdigest(),
        size_bytes=len(payload),
    )
