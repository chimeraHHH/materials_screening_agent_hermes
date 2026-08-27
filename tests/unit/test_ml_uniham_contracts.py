from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import ValidationError

from material_agent.ml_screening.uniham_client import audit_uniham_benchmark
from material_agent.ml_screening.uniham_models import (
    UniHamArtifactFile,
    UniHamBenchmarkBinding,
    UniHamBenchmarkMetrics,
    UniHamBenchmarkPolicy,
    UniHamGraphBundle,
    UniHamInferenceRequest,
    UniHamInputTrust,
    UniHamProducedArtifact,
    UniHamRuntimeProvenance,
    UniHamWorkerResponse,
)
from material_agent.ml_screening.uniham_planner import (
    build_uniham_plan,
    build_uniham_plan_from_manifests,
)
from material_agent.ml_screening.uniham_remote import UniHamRemoteClient
from material_agent.retrieval.storage import LocalArtifactStore


def test_uniham_plan_binds_dual_graphs_to_structure_and_basis(
    tmp_path: Path,
) -> None:
    request = make_request(tmp_path)
    plan = build_uniham_plan(request, artifact_root=tmp_path)

    assert plan.non_soc_manifest.soc_mode == "non_soc"
    assert plan.soc_manifest.soc_mode == "soc"
    assert plan.non_soc_manifest.basis_id == plan.soc_manifest.basis_id
    assert plan.operation_key in plan.output_sandbox_relative_path


def test_uniham_request_requires_explicit_pickle_trust(tmp_path: Path) -> None:
    request = make_request(tmp_path)
    payload = request.model_dump(mode="json")
    payload["input_trust"]["trusted_executable_inputs"] = False

    with pytest.raises(ValidationError, match="trusted_executable_inputs"):
        UniHamInferenceRequest.model_validate(payload)


def test_uniham_plan_rejects_manifest_tampering(tmp_path: Path) -> None:
    request = make_request(tmp_path)
    path = tmp_path / request.soc_graph.manifest.root_relative_path
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["basis_id"] = "different-basis"
    path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError, match="(size|hash) mismatch"):
        build_uniham_plan(request, artifact_root=tmp_path)


def test_uniham_cuda_runtime_cannot_silently_fall_back_to_cpu() -> None:
    with pytest.raises(ValidationError, match="silently changed"):
        UniHamRuntimeProvenance(
            requested_device="cuda",
            observed_device="cpu",
            cuda_visible_device_count=0,
        )

    runtime = UniHamRuntimeProvenance(
        requested_device="cuda",
        observed_device="cuda",
        cuda_visible_device_count=1,
        cuda_device_name="NVIDIA L40S",
        torch_version="2.5.1+cu124",
        torch_cuda_version="12.4",
    )
    assert runtime.cuda_visible_device_count == 1


def test_uniham_benchmark_can_promote_only_a_real_request_to_l2(
    tmp_path: Path,
) -> None:
    request = make_request(tmp_path, is_mock=False, with_benchmark=True)
    plan = build_uniham_plan(request, artifact_root=tmp_path)
    assert plan.request.benchmark == request.benchmark
    status, evidence, benchmark, _warning = audit_uniham_benchmark(
        request,
        UniHamBenchmarkPolicy(),
    )
    assert status == "VALIDATED"
    assert evidence == "L2_ML_SCREENED"
    assert benchmark == request.benchmark

    assert request.benchmark is not None
    failing = request.model_copy(
        update={
            "benchmark": request.benchmark.model_copy(
                update={
                    "metrics": request.benchmark.metrics.model_copy(
                        update={"flat_band_width_mae_ev": 0.030}
                    )
                }
            )
        }
    )
    status, evidence, benchmark, _warning = audit_uniham_benchmark(
        failing,
        UniHamBenchmarkPolicy(),
    )
    assert status == "FAILED"
    assert evidence == "NONE"
    assert benchmark is None


def test_remote_uniham_client_fetches_hash_bound_outputs_and_reuses_cache(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request = make_request(tmp_path / "inputs", is_mock=False).model_copy(
        update={"device": "cuda"}
    )
    local_plan = build_uniham_plan(
        request,
        artifact_root=tmp_path / "inputs",
        created_at=datetime(2026, 8, 25, tzinfo=UTC),
    )
    payloads = {
        "output/hamiltonian.npy": b"\x93NUMPY-real-hamiltonian",
        "output/inference-summary.json": b'{"status":"SUCCEEDED"}',
        "output/runtime-provenance.json": b'{"device":"cuda"}',
    }
    artifacts = [
        UniHamProducedArtifact(
            root_relative_path=(
                f"{local_plan.output_sandbox_relative_path}/{relative}"
            ),
            sha256=hashlib.sha256(payload).hexdigest(),
            size_bytes=len(payload),
            media_type=(
                "application/x-npy"
                if relative.endswith(".npy")
                else "application/json"
            ),
        )
        for relative, payload in payloads.items()
    ]
    response = UniHamWorkerResponse(
        operation_key=local_plan.operation_key,
        status="SUCCEEDED",
        produced_artifacts=artifacts,
        is_mock=False,
        runtime_provenance=UniHamRuntimeProvenance(
            requested_device="cuda",
            observed_device="cuda",
            cuda_visible_device_count=1,
            cuda_device_name="NVIDIA L40S",
            torch_version="2.10.0+cu128",
            torch_cuda_version="12.8",
        ),
    )
    transport_calls: list[tuple[str, ...]] = []

    def fake_process(
        command: tuple[str, ...],
        *,
        stdin: bytes | None = None,
        timeout: int,
    ) -> bytes:
        del timeout
        transport_calls.append(command)
        if command[0] == "/usr/bin/ssh":
            assert stdin is not None
            return response.model_dump_json().encode()
        assert command[0] == "/usr/bin/scp"
        remote_relative = command[-2].split(":/srv/uniham/", maxsplit=1)[1]
        prefix = f"{local_plan.output_sandbox_relative_path}/"
        payload = payloads[remote_relative.removeprefix(prefix)]
        destination = Path(command[-1])
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(payload)
        return b""

    monkeypatch.setattr(
        UniHamRemoteClient,
        "_run_process",
        staticmethod(fake_process),
    )
    client = UniHamRemoteClient(
        artifact_store=LocalArtifactStore(tmp_path / "store"),
        ssh_host_alias="whu-ext",
        remote_artifact_root="/srv/uniham",
        remote_worker_python="/srv/env/bin/python",
        remote_worker_path="/srv/source/uniham_worker.py",
        remote_predictor_path="/srv/source/predict.py",
        cuda_visible_device="6",
    )
    first = client.run(
        request,
        non_soc_manifest=local_plan.non_soc_manifest,
        soc_manifest=local_plan.soc_manifest,
        created_at=local_plan.created_at,
    )
    initial_call_count = len(transport_calls)
    second = client.run(
        request,
        non_soc_manifest=local_plan.non_soc_manifest,
        soc_manifest=local_plan.soc_manifest,
        created_at=local_plan.created_at,
    )

    assert first == second
    assert len(transport_calls) == initial_call_count == 4
    assert first.hamiltonian_pointer.sha256 == hashlib.sha256(
        payloads["output/hamiltonian.npy"]
    ).hexdigest()
    assert first.scientific_conclusion is False


def test_remote_uniham_client_rejects_unsafe_transport_identity(
    tmp_path: Path,
) -> None:
    with pytest.raises(ValueError, match="host alias"):
        UniHamRemoteClient(
            artifact_store=LocalArtifactStore(tmp_path),
            ssh_host_alias="host;touch-pwned",
            remote_artifact_root="/srv/uniham",
            remote_worker_python="/srv/env/bin/python",
            remote_worker_path="/srv/source/uniham_worker.py",
            remote_predictor_path="/srv/source/predict.py",
            cuda_visible_device="0",
        )


def test_remote_plan_builder_rejects_manifest_structure_mismatch(
    tmp_path: Path,
) -> None:
    request = make_request(tmp_path)
    plan = build_uniham_plan(request, artifact_root=tmp_path)
    changed = plan.soc_manifest.model_copy(update={"structure_sha256": "f" * 64})

    with pytest.raises(ValueError, match="structure hash mismatch"):
        build_uniham_plan_from_manifests(
            request,
            non_soc_manifest=plan.non_soc_manifest,
            soc_manifest=changed,
        )


def make_request(
    root: Path,
    *,
    predictor_sha256: str | None = None,
    is_mock: bool = True,
    with_benchmark: bool = False,
) -> UniHamInferenceRequest:
    structure = write_artifact(root, "inputs/structure.cif", b"mock-cif")
    model = write_artifact(root, "inputs/model/universal_model.pkl", b"mock-pickle")
    non_soc = make_graph_bundle(root, structure.sha256, "non_soc")
    soc = make_graph_bundle(root, structure.sha256, "soc")
    benchmark = None
    if with_benchmark:
        report = write_artifact(
            root,
            "inputs/benchmark/report.json",
            b'{"benchmark":"fixture-metrics"}',
            media_type="application/json",
        )
        benchmark = UniHamBenchmarkBinding(
            benchmark_id="uniham-2d-heldout-v1",
            report_artifact=report,
            model_sha256=model.sha256,
            hamgnn_source_revision="0123456789abcdef",
            dataset_id="fixture-heldout-2d",
            dataset_sha256="9" * 64,
            held_out_structure_count=200,
            dimensionalities=(2, 3),
            metrics=UniHamBenchmarkMetrics(
                hamiltonian_mae_ev=0.010,
                band_energy_mae_ev=0.015,
                flat_band_width_mae_ev=0.010,
                soc_gap_mae_ev=0.020,
                band_ordering_accuracy=0.97,
                crossing_classification_accuracy=0.98,
                topology_invariant_accuracy=0.95,
            ),
            reviewed_by="test-suite",
            reviewed_at=datetime(2026, 8, 25, tzinfo=UTC),
        )
    return UniHamInferenceRequest(
        project_id="project-uniham",
        run_id="run-uniham",
        candidate_id="candidate-1",
        input_structure=structure,
        model_pickle=model,
        non_soc_graph=non_soc,
        soc_graph=soc,
        model_id="uni-hamgnn-fixture",
        model_source_url="https://zenodo.org/records/fixture",
        model_revision="fixture-revision-1",
        weights_license="TEST-ONLY",
        hamgnn_source_revision="0123456789abcdef",
        predictor_script_sha256=predictor_sha256 or "1" * 64,
        input_trust=UniHamInputTrust(
            trusted_executable_inputs=True,
            reviewed_by="test-suite",
            reviewed_at=datetime(2026, 8, 22, tzinfo=UTC),
            review_basis="fixture artifacts generated by this test",
        ),
        benchmark=benchmark,
        is_mock=is_mock,
    )


def make_graph_bundle(
    root: Path, structure_sha256: str, mode: str
) -> UniHamGraphBundle:
    directory = f"inputs/graphs/{mode}"
    graph = write_artifact(
        root, f"{directory}/graph_data.npz", f"mock-{mode}".encode()
    )
    manifest_payload = {
        "schema_version": "agent02-uniham-graph-manifest-v1",
        "structure_sha256": structure_sha256,
        "graph_data_sha256": graph.sha256,
        "soc_mode": mode,
        "interface": "openmx",
        "basis_id": "openmx-nao26-v1",
        "dft_data_version": "OpenMX-3.9-DFT_DATA19",
        "graph_generator_revision": "abcdef1234567890",
        "nao_max": 26,
    }
    manifest = write_artifact(
        root,
        f"{directory}/hermes-graph-manifest.json",
        json.dumps(
            manifest_payload, sort_keys=True, separators=(",", ":")
        ).encode(),
        media_type="application/json",
    )
    return UniHamGraphBundle(
        root_relative_directory=directory,
        graph_data=graph,
        manifest=manifest,
    )


def write_artifact(
    root: Path,
    relative: str,
    payload: bytes,
    *,
    media_type: str = "application/octet-stream",
) -> UniHamArtifactFile:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)
    return UniHamArtifactFile(
        artifact_uri=f"artifact://{relative}",
        root_relative_path=relative,
        sha256=hashlib.sha256(payload).hexdigest(),
        size_bytes=len(payload),
        media_type=media_type,
    )
