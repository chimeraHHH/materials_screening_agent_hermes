"""Independent CHGNet worker for the frozen Agent02 JSON protocol.

This module is loaded only by the dedicated Agent02 Python environment.  Its
stdout is reserved for exactly one ``WorkerResponse`` JSON value.
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import io
import math
import platform
import resource
import sys
import time
import zipfile
from datetime import UTC, datetime, timedelta
from importlib import metadata
from pathlib import Path
from typing import Any

import numpy as np
import torch
from chgnet.model.dynamics import StructOptimizer
from chgnet.model.model import CHGNet
from pymatgen.analysis.structure_matcher import StructureMatcher
from pymatgen.core import Structure
from pymatgen.io.cif import CifWriter

from material_agent.ml_screening.evidence import (
    build_structure_lineage,
    decision_for_ml_result,
    evidence_for_ml_result,
    recommended_downstream_structure_id,
)
from material_agent.ml_screening.models import (
    ArtifactPointer,
    EvidenceLevel,
    ExecutionStatus,
    MLCandidateResult,
    MLNumericArtifactMetadata,
    MLNumericArtifactRef,
    MLPropertyValue,
    MLRelaxationResult,
    ModelHealthSnapshot,
    RelaxationStatus,
    SmokeTestStatus,
    WorkerProducedArtifact,
    WorkerRequest,
    WorkerResponse,
    WorkerResponseStatus,
)
from material_agent.ml_screening.numerics import (
    normalize_ase_stress_to_gpa,
    normalize_direct_stress_gpa,
)
from material_agent.ml_screening.real_resources import (
    AGENT02_PACKAGE_LOCK_SHA256,
    CHGNET_ADAPTER_VERSION,
    CHGNET_CHECKPOINT_SHA256,
    CHGNET_MODEL_ID,
    CHGNET_MODEL_NAME,
    CHGNET_PACKAGE_VERSION,
    real_model_spec,
)
from material_agent.ml_screening.resources import (
    default_policy,
    environment_fingerprint_sha256,
)
from material_agent.ml_screening.worker_protocol import validate_worker_inputs


_PACKAGE_NAMES = ("chgnet", "torch", "pymatgen", "ase", "numpy")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--artifact-root", type=Path, required=True)
    parser.add_argument("--package-lock", type=Path, required=True)
    parser.add_argument("--health-structure", type=Path)
    parser.add_argument("--device", choices=("cpu", "mps"), default="cpu")
    args = parser.parse_args(argv)
    try:
        if args.health_structure is not None:
            response = build_health_snapshot(
                package_lock_path=args.package_lock,
                structure_path=args.health_structure,
                device=args.device,
            )
        else:
            request = WorkerRequest.model_validate_json(sys.stdin.buffer.read())
            response = execute_request(
                request=request,
                artifact_root=args.artifact_root,
                package_lock_path=args.package_lock,
            )
    except Exception as exc:
        # The parent maps nonzero exit and this bounded public diagnostic.  A
        # traceback is intentionally not exposed through the protocol.
        print(
            f"Agent02 worker failed: {type(exc).__name__}: {exc}",
            file=sys.stderr,
        )
        return 2
    sys.stdout.write(response.model_dump_json())
    return 0


def build_health_snapshot(
    *,
    package_lock_path: Path,
    structure_path: Path,
    device: str,
) -> ModelHealthSnapshot:
    lock_path = package_lock_path.resolve(strict=True)
    if _sha256_file(lock_path) != AGENT02_PACKAGE_LOCK_SHA256:
        raise ValueError("Agent02 package lock hash mismatch")
    available = _available_devices()
    if device not in available:
        raise ValueError(f"health device is unavailable: {device}")
    checkpoint = _checkpoint_path()
    if _sha256_file(checkpoint) != CHGNET_CHECKPOINT_SHA256:
        raise ValueError("CHGNet checkpoint hash mismatch")
    versions = {name: metadata.version(name) for name in _PACKAGE_NAMES}
    if versions["chgnet"] != CHGNET_PACKAGE_VERSION:
        raise ValueError("installed CHGNet package version differs from registry")
    structure = Structure.from_file(structure_path.resolve(strict=True))
    with contextlib.redirect_stdout(sys.stderr):
        model = CHGNet.load(
            model_name=CHGNET_MODEL_NAME,
            use_device=device,
            verbose=False,
        )
        prediction = model.predict_structure(structure, task="efsm")
    _finite_scalar(prediction["e"], "health static energy")
    _finite_array(prediction["f"], (len(structure), 3), "health forces")
    _finite_array(prediction["s"], (3, 3), "health stress")
    _finite_array(prediction["m"], (len(structure),), "health magmoms")
    tested_at = datetime.now(UTC)
    fingerprint = environment_fingerprint_sha256(
        python_version=platform.python_version(),
        installed_package_versions=versions,
        platform=platform.system(),
        architecture=platform.machine(),
        device_policy=device,
        available_devices=available,
        package_lock_sha256=AGENT02_PACKAGE_LOCK_SHA256,
    )
    return ModelHealthSnapshot(
        model_id=CHGNET_MODEL_ID,
        checkpoint_sha256=CHGNET_CHECKPOINT_SHA256,
        package_lock_sha256=AGENT02_PACKAGE_LOCK_SHA256,
        python_version=platform.python_version(),
        platform=platform.system(),
        architecture=platform.machine(),
        device_policy=device,
        available_devices=available,
        smoke_test_status=SmokeTestStatus.PASS,
        tested_at=tested_at,
        expires_at=tested_at + timedelta(days=1),
        installed_package_versions=versions,
        environment_fingerprint_sha256=fingerprint,
        adapter_version=CHGNET_ADAPTER_VERSION,
        is_mock=False,
    )


def execute_request(
    *,
    request: WorkerRequest,
    artifact_root: Path,
    package_lock_path: Path,
) -> WorkerResponse:
    root = artifact_root.resolve(strict=True)
    lock_path = package_lock_path.resolve(strict=True)
    input_paths = validate_worker_inputs(request, root)
    actual_identity = _execution_identity(request, lock_path)
    if actual_identity != request.expected_handshake:
        raise ValueError("worker environment handshake differs from frozen plan")

    planned = next(
        item
        for item in request.plan.planned_candidates
        if item.candidate.candidate_id == request.candidate_id
    )
    structure_path = next(
        path
        for path, item in zip(input_paths, request.inputs, strict=True)
        if item.artifact_uri == planned.candidate.source_structure.uri
    )
    sandbox = _sandbox_path(root, request.output_sandbox_relative_path)
    result, produced = _run_candidate(
        request=request,
        structure_path=structure_path,
        artifact_root=root,
        sandbox=sandbox,
        actual_identity=actual_identity,
    )
    response = WorkerResponse(
        actual_handshake=actual_identity,
        candidate_id=request.candidate_id,
        candidate_operation_key=request.candidate_operation_key,
        status=WorkerResponseStatus.SUCCEEDED,
        candidate_result=result,
        produced_artifacts=produced,
        warnings=[
            "CHGNet values are MLIP predictions, not formation energies, "
            "convex-hull stability, DFT, or experimental evidence."
        ],
    )
    response.validate_against_request(request)
    return response


def _run_candidate(
    *,
    request: WorkerRequest,
    structure_path: Path,
    artifact_root: Path,
    sandbox: Path,
    actual_identity,
) -> tuple[MLCandidateResult, list[WorkerProducedArtifact]]:
    started = time.monotonic()
    plan = request.plan
    planned = next(
        item
        for item in plan.planned_candidates
        if item.candidate.candidate_id == request.candidate_id
    )
    candidate = planned.candidate
    structure = Structure.from_file(structure_path)
    if len(structure) != candidate.source_structure.num_sites:
        raise ValueError("parsed structure site count differs from frozen input")
    if sorted({str(element) for element in structure.composition.elements}) != sorted(
        candidate.source_structure.elements
    ):
        raise ValueError("parsed structure elements differ from frozen input")

    device = plan.device_policy
    if device not in _available_devices():
        raise ValueError(f"requested device is unavailable: {device}")
    # Third-party initialization writes informational text to stdout.  Keep
    # protocol stdout clean by redirecting it to stderr.
    with contextlib.redirect_stdout(sys.stderr):
        model = CHGNet.load(
            model_name=CHGNET_MODEL_NAME,
            use_device=device,
            verbose=False,
        )
        prediction = model.predict_structure(structure, task="efsm")
        optimizer = StructOptimizer(
            model=model,
            optimizer_class="FIRE",
            use_device=device,
            on_isolated_atoms="error",
        )
        relaxation = optimizer.relax(
            structure,
            fmax=0.1,
            steps=200,
            relax_cell=True,
            ase_filter="FrechetCellFilter",
            verbose=False,
            assign_magmoms=True,
        )

    direct_energy_ev_atom = _finite_scalar(prediction["e"], "static energy")
    direct_forces = _finite_array(prediction["f"], (len(structure), 3), "forces")
    direct_stress_array = _finite_array(prediction["s"], (3, 3), "stress")
    direct_stress = normalize_direct_stress_gpa(
        ((direct_stress_array + direct_stress_array.T) / 2).tolist()
    )
    direct_magmoms = _finite_array(
        prediction["m"], (len(structure),), "site magnetic moments"
    )

    final_structure = relaxation["final_structure"]
    trajectory = relaxation["trajectory"]
    energies = _finite_array(trajectory.energies, None, "energy trajectory")
    forces = _finite_array(trajectory.forces, None, "force trajectory")
    stresses = _finite_array(trajectory.stresses, None, "stress trajectory")
    magmoms = _finite_array(trajectory.magmoms, None, "magmom trajectory")
    if (
        energies.ndim != 1
        or forces.ndim != 3
        or forces.shape[1:] != (len(structure), 3)
        or stresses.ndim != 2
        or stresses.shape[1] != 6
        or magmoms.ndim != 2
        or magmoms.shape[1] != len(structure)
    ):
        raise ValueError("CHGNet relaxation trajectory has invalid dimensions")
    if not (
        len(energies) == len(forces) == len(stresses) == len(magmoms)
    ):
        raise ValueError("CHGNet relaxation trajectory lengths differ")
    if len(final_structure) != len(structure) or final_structure.composition != structure.composition:
        raise ValueError("relaxation changed composition or atom count")

    final_forces = forces[-1]
    initial_max_force = float(np.linalg.norm(direct_forces, axis=1).max())
    final_max_force = float(np.linalg.norm(final_forces, axis=1).max())
    initial_energy_ev = direct_energy_ev_atom * len(structure)
    final_energy_ev = float(energies[-1])
    final_energy_ev_atom = final_energy_ev / len(structure)
    delta_energy_ev_atom = final_energy_ev_atom - direct_energy_ev_atom
    final_stress = normalize_ase_stress_to_gpa(stresses[-1].tolist())
    volume_ratio = float(final_structure.volume / structure.volume)
    structure_match = bool(
        StructureMatcher(
            ltol=0.2,
            stol=0.3,
            angle_tol=5,
            primitive_cell=False,
            scale=True,
            attempt_supercell=False,
        ).fit(structure, final_structure)
    )
    minimum_distance = _minimum_periodic_distance(final_structure)
    hard_complete = minimum_distance >= 0.5
    if not hard_complete:
        raise ValueError("relaxed structure contains atoms closer than 0.5 angstrom")

    converged = final_max_force <= 0.1
    qc_passed = (
        converged
        and delta_energy_ev_atom <= 0.0001
        and 0.8 <= volume_ratio <= 1.2
        and structure_match
    )
    status = (
        RelaxationStatus.CONVERGED
        if converged
        else RelaxationStatus.MAX_STEPS
    )
    execution_status = (
        ExecutionStatus.CONVERGED
        if converged
        else ExecutionStatus.MAX_STEPS
    )
    evidence = evidence_for_ml_result(
        is_mock=False,
        applicability=planned.applicability.status,
        qc_passed=qc_passed,
    )
    decision = decision_for_ml_result(
        is_mock=False,
        applicability=planned.applicability.status,
        qc_passed=qc_passed,
    )

    output_path = sandbox / "relaxed.cif"
    output_path.write_text(str(CifWriter(final_structure)), encoding="utf-8")
    output_hash = _sha256_file(output_path)
    output_structure_id = f"ml_{output_hash[:24]}"
    output_uri = output_path.relative_to(artifact_root).as_posix()
    if artifact_root.joinpath(output_uri) != output_path:
        raise ValueError("worker output path could not be rooted safely")

    forces_path = sandbox / "forces.npz"
    _write_deterministic_npz(forces_path, "forces", direct_forces)
    magmoms_path = sandbox / "site_magnetic_moments.npz"
    _write_deterministic_npz(
        magmoms_path, "site_magnetic_moments", direct_magmoms
    )
    produced = [
        _produced(output_path, artifact_root, "chemical/x-cif"),
        _produced(
            forces_path,
            artifact_root,
            "application/x-npz",
            key="forces",
            array=direct_forces,
        ),
        _produced(
            magmoms_path,
            artifact_root,
            "application/x-npz",
            key="site_magnetic_moments",
            array=direct_magmoms,
        ),
    ]
    produced_by_name = {Path(item.root_relative_path).name: item for item in produced}
    force_ref = _numeric_ref(produced_by_name["forces.npz"])
    magmom_ref = _numeric_ref(produced_by_name["site_magnetic_moments.npz"])

    method = "CHGNet 0.3.0 MPtrj ML interatomic potential"
    properties = [
        MLPropertyValue(
            property_name="mlip_potential_energy",
            value=direct_energy_ev_atom,
            unit="eV/atom",
            evidence_level=evidence,
            method=method,
            model_id=CHGNET_MODEL_ID,
            checkpoint_sha256=CHGNET_CHECKPOINT_SHA256,
            input_structure_id=candidate.source_structure.structure_id,
            is_mock=False,
            provenance={"prediction_api": "predict_structure", "task": "efsm"},
        ),
        MLPropertyValue(
            property_name="maximum_force",
            value=initial_max_force,
            unit="eV/angstrom",
            evidence_level=evidence,
            method=method,
            model_id=CHGNET_MODEL_ID,
            checkpoint_sha256=CHGNET_CHECKPOINT_SHA256,
            input_structure_id=candidate.source_structure.structure_id,
            is_mock=False,
            provenance={"derived_from": "static forces"},
        ),
        MLPropertyValue(
            property_name="forces",
            artifact_ref=force_ref,
            unit="eV/angstrom",
            evidence_level=evidence,
            method=method,
            model_id=CHGNET_MODEL_ID,
            checkpoint_sha256=CHGNET_CHECKPOINT_SHA256,
            input_structure_id=candidate.source_structure.structure_id,
            num_sites=len(structure),
            is_mock=False,
            provenance={"prediction_api": "predict_structure"},
        ),
        MLPropertyValue(
            property_name="stress",
            value=direct_stress,
            unit="GPa",
            evidence_level=evidence,
            method=method,
            model_id=CHGNET_MODEL_ID,
            checkpoint_sha256=CHGNET_CHECKPOINT_SHA256,
            input_structure_id=candidate.source_structure.structure_id,
            is_mock=False,
            provenance={"symmetrized": True, "prediction_api": "predict_structure"},
        ),
        MLPropertyValue(
            property_name="site_magnetic_moments",
            artifact_ref=magmom_ref,
            unit="mu_B",
            evidence_level=evidence,
            method=method,
            model_id=CHGNET_MODEL_ID,
            checkpoint_sha256=CHGNET_CHECKPOINT_SHA256,
            input_structure_id=candidate.source_structure.structure_id,
            num_sites=len(structure),
            is_mock=False,
            provenance={"prediction_api": "predict_structure"},
        ),
        MLPropertyValue(
            property_name="mlip_potential_energy",
            value=final_energy_ev_atom,
            unit="eV/atom",
            evidence_level=evidence,
            method=method,
            model_id=CHGNET_MODEL_ID,
            checkpoint_sha256=CHGNET_CHECKPOINT_SHA256,
            input_structure_id=candidate.source_structure.structure_id,
            output_structure_id=output_structure_id,
            is_mock=False,
            provenance={
                "source": "relaxation trajectory final total potential energy",
                "normalization": "divided by final structure atom count once",
            },
        ),
        MLPropertyValue(
            property_name="maximum_force",
            value=final_max_force,
            unit="eV/angstrom",
            evidence_level=evidence,
            method=method,
            model_id=CHGNET_MODEL_ID,
            checkpoint_sha256=CHGNET_CHECKPOINT_SHA256,
            input_structure_id=candidate.source_structure.structure_id,
            output_structure_id=output_structure_id,
            is_mock=False,
            provenance={"source": "relaxation trajectory final forces"},
        ),
    ]
    relaxation_result = MLRelaxationResult(
        candidate_id=candidate.candidate_id,
        input_structure_id=candidate.source_structure.structure_id,
        input_structure_uri=candidate.source_structure.uri,
        input_structure_sha256=candidate.source_structure.sha256,
        output_structure_id=output_structure_id,
        output_structure_uri=f"artifact://{output_uri}",
        output_structure_sha256=output_hash,
        model_id=CHGNET_MODEL_ID,
        checkpoint_sha256=CHGNET_CHECKPOINT_SHA256,
        adapter_version=CHGNET_ADAPTER_VERSION,
        execution_identity=actual_identity,
        device=device,
        relaxation_profile=plan.relaxation_profile,
        status=status,
        num_steps=min(200, max(0, len(energies) - 1)),
        initial_energy_ev=initial_energy_ev,
        final_energy_ev=final_energy_ev,
        initial_energy_ev_atom=direct_energy_ev_atom,
        final_energy_ev_atom=final_energy_ev_atom,
        delta_energy_ev_atom=delta_energy_ev_atom,
        initial_max_force_ev_angstrom=initial_max_force,
        final_max_force_ev_angstrom=final_max_force,
        final_stress_gpa_3x3=final_stress,
        magmom_summary={
            "maximum_absolute_mu_b": float(np.abs(magmoms[-1]).max()),
            "mean_absolute_mu_b": float(np.abs(magmoms[-1]).mean()),
        },
        structure_drift={
            "volume_ratio": volume_ratio,
            "structure_match": structure_match,
            "minimum_distance_angstrom": minimum_distance,
        },
        qc_passed=qc_passed,
        warnings=[] if qc_passed else ["relaxation did not pass every frozen soft QC"],
        wall_time_seconds=time.monotonic() - started,
        is_mock=False,
        provenance={
            "optimizer": "FIRE",
            "cell_filter": "FrechetCellFilter",
            "fmax_ev_angstrom": 0.1,
            "max_steps": 200,
            "energy_semantics": "MLIP potential energy",
            "peak_rss_bytes": _peak_rss_bytes(),
        },
    )
    lineage = build_structure_lineage(
        candidate=candidate,
        output_structure_id=output_structure_id,
        output_structure=ArtifactPointer(
            uri=f"artifact://{output_uri}", sha256=output_hash
        ),
        model=real_model_spec(),
        policy=default_policy(),
        is_mock=False,
    )
    recommended = recommended_downstream_structure_id(
        candidate=candidate,
        decision=decision,
        evidence_level=evidence,
        output_structure_id=output_structure_id,
    )
    result = MLCandidateResult(
        project_id=plan.project_id,
        run_id=plan.run_id,
        candidate_id=candidate.candidate_id,
        candidate_operation_key=request.candidate_operation_key,
        upstream_manifest_uri=candidate.upstream_manifest_uri,
        upstream_manifest_sha256=candidate.upstream_manifest_sha256,
        source_structure_id=candidate.source_structure.structure_id,
        source_structure_uri=candidate.source_structure.uri,
        source_structure_sha256=candidate.source_structure.sha256,
        pre_filter_decision=planned.pre_filter.decision,
        applicability=planned.applicability,
        selection_status=planned.selection_status,
        execution_status=execution_status,
        decision=decision,
        evidence_level=evidence,
        execution_identity=actual_identity,
        ml_properties=properties,
        relaxation_result=relaxation_result,
        structure_lineage=lineage,
        recommended_downstream_structure_id=recommended,
        warnings=list(relaxation_result.warnings),
    )
    return result, produced


def _execution_identity(request: WorkerRequest, lock_path: Path):
    from material_agent.ml_screening.models import MLExecutionIdentity

    if _sha256_file(lock_path) != AGENT02_PACKAGE_LOCK_SHA256:
        raise ValueError("Agent02 package lock hash mismatch")
    checkpoint = _checkpoint_path()
    if _sha256_file(checkpoint) != CHGNET_CHECKPOINT_SHA256:
        raise ValueError("CHGNet checkpoint hash mismatch")
    versions = {name: metadata.version(name) for name in _PACKAGE_NAMES}
    if versions["chgnet"] != CHGNET_PACKAGE_VERSION:
        raise ValueError("installed CHGNet package version differs from registry")
    available = _available_devices()
    fingerprint = environment_fingerprint_sha256(
        python_version=platform.python_version(),
        installed_package_versions=versions,
        platform=platform.system(),
        architecture=platform.machine(),
        device_policy=request.plan.device_policy,
        available_devices=available,
        package_lock_sha256=AGENT02_PACKAGE_LOCK_SHA256,
    )
    return MLExecutionIdentity(
        model_id=CHGNET_MODEL_ID,
        checkpoint_sha256=CHGNET_CHECKPOINT_SHA256,
        package_lock_sha256=AGENT02_PACKAGE_LOCK_SHA256,
        adapter_version=CHGNET_ADAPTER_VERSION,
        environment_fingerprint_sha256=fingerprint,
        is_mock=False,
    )


def _checkpoint_path() -> Path:
    return (
        Path(metadata.distribution("chgnet").locate_file(""))
        / "chgnet"
        / "pretrained"
        / CHGNET_MODEL_NAME
        / "chgnet_0.3.0_e29f68s314m37.pth.tar"
    )


def _available_devices() -> list[str]:
    devices = ["cpu"]
    if torch.backends.mps.is_available():
        devices.append("mps")
    return devices


def _sandbox_path(root: Path, relative: str) -> Path:
    path = root.joinpath(*relative.split("/")).resolve(strict=True)
    if root not in path.parents or not path.is_dir() or path.is_symlink():
        raise ValueError("worker sandbox is outside the trusted artifact root")
    return path


def _finite_scalar(value: Any, label: str) -> float:
    array = np.asarray(value, dtype=np.float64)
    if array.shape != () or not np.isfinite(array).all():
        raise ValueError(f"{label} must be one finite scalar")
    return float(array)


def _finite_array(
    value: Any, shape: tuple[int, ...] | None, label: str
) -> np.ndarray:
    array = np.asarray(value, dtype=np.float64)
    if shape is not None and array.shape != shape:
        raise ValueError(f"{label} has shape {array.shape}, expected {shape}")
    if array.size == 0 or not np.isfinite(array).all():
        raise ValueError(f"{label} must contain finite values")
    return array


def _minimum_periodic_distance(structure: Structure) -> float:
    if len(structure) < 2:
        return math.inf
    matrix = np.asarray(structure.distance_matrix, dtype=np.float64)
    matrix[matrix == 0] = np.inf
    minimum = float(matrix.min())
    if not math.isfinite(minimum):
        raise ValueError("minimum periodic distance could not be computed")
    return minimum


def _write_deterministic_npz(path: Path, key: str, array: np.ndarray) -> None:
    buffer = io.BytesIO()
    np.lib.format.write_array(buffer, np.asarray(array), allow_pickle=False)
    info = zipfile.ZipInfo(f"{key}.npy", date_time=(1980, 1, 1, 0, 0, 0))
    info.compress_type = zipfile.ZIP_DEFLATED
    info.external_attr = 0o600 << 16
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr(info, buffer.getvalue())


def _produced(
    path: Path,
    root: Path,
    media_type: str,
    *,
    key: str | None = None,
    array: np.ndarray | None = None,
) -> WorkerProducedArtifact:
    numeric = None
    if key is not None and array is not None:
        numeric = MLNumericArtifactMetadata(
            array_key=key,
            dtype=array.dtype.name,
            shape=list(array.shape),
            allow_pickle=False,
        )
    return WorkerProducedArtifact(
        root_relative_path=path.relative_to(root).as_posix(),
        sha256=_sha256_file(path),
        size_bytes=path.stat().st_size,
        media_type=media_type,
        numeric_metadata=numeric,
    )


def _numeric_ref(artifact: WorkerProducedArtifact) -> MLNumericArtifactRef:
    metadata_value = artifact.numeric_metadata
    assert metadata_value is not None
    return MLNumericArtifactRef(
        uri=f"artifact://{artifact.root_relative_path}",
        sha256=artifact.sha256,
        size_bytes=artifact.size_bytes,
        array_key=metadata_value.array_key,
        dtype=metadata_value.dtype,
        shape=metadata_value.shape,
        allow_pickle=False,
    )


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _peak_rss_bytes() -> int:
    """Return the platform-normalized process peak resident-set size."""

    peak = int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
    # macOS reports bytes while Linux and the common BSD test runners report
    # KiB.  Preserve an explicit byte unit in the provenance payload.
    return peak if sys.platform == "darwin" else peak * 1024


if __name__ == "__main__":
    raise SystemExit(main())
