#!/usr/bin/env python3
"""Run generated graph pair -> GPU Uni-HamGNN -> local flat-band validation."""

from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path

from material_agent.integration.electronic_structure import (
    FlatBandAnalysisInput,
    FlatBandPolicy,
    assess_flat_band,
    parse_hamgnn_band_dat,
)
from material_agent.ml_screening.uniham_band_models import UniHamBandRequest
from material_agent.ml_screening.uniham_band_remote import UniHamBandRemoteClient
from material_agent.ml_screening.uniham_graph_prep_remote import (
    UniHamGraphPrepRemoteResult,
)
from material_agent.ml_screening.uniham_models import (
    UniHamArtifactFile,
    UniHamInferenceRequest,
    UniHamInputTrust,
)
from material_agent.ml_screening.uniham_remote import UniHamRemoteClient
from material_agent.retrieval.storage import LocalArtifactStore


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--artifact-root", type=Path, required=True)
    parser.add_argument("--graph-prep-result", required=True)
    parser.add_argument("--inference-run-id", required=True)
    parser.add_argument("--band-run-id", required=True)
    parser.add_argument("--host", required=True)
    parser.add_argument("--remote-root", required=True)
    parser.add_argument("--remote-python", required=True)
    parser.add_argument("--remote-uniham-worker", required=True)
    parser.add_argument("--remote-band-worker", required=True)
    parser.add_argument("--remote-predictor", required=True)
    parser.add_argument("--remote-band-cal", required=True)
    parser.add_argument("--model-path", required=True)
    parser.add_argument("--model-sha256", required=True)
    parser.add_argument("--model-size-bytes", type=int, required=True)
    parser.add_argument("--predictor-sha256", required=True)
    parser.add_argument("--band-cal-sha256", required=True)
    parser.add_argument("--hamgnn-revision", required=True)
    parser.add_argument("--cuda-visible-device", default="6")
    parser.add_argument("--nk", type=int, default=120)
    parser.add_argument(
        "--reviewed-at",
        required=True,
        help="Frozen ISO-8601 timestamp for reproducible request identity.",
    )
    args = parser.parse_args()

    store = LocalArtifactStore(args.artifact_root)
    graph_result = UniHamGraphPrepRemoteResult.model_validate_json(
        store.read_bytes(args.graph_prep_result)
    )
    plan = graph_result.plan
    staged_structure_path = plan.input_root_relative_path
    structure = UniHamArtifactFile(
        artifact_uri=f"artifact://{staged_structure_path}",
        root_relative_path=staged_structure_path,
        sha256=plan.request.input_structure.sha256,
        size_bytes=plan.request.input_structure.size_bytes,
        media_type="chemical/x-cif",
    )
    model = UniHamArtifactFile(
        artifact_uri=f"artifact://{args.model_path}",
        root_relative_path=args.model_path,
        sha256=args.model_sha256,
        size_bytes=args.model_size_bytes,
        media_type="application/octet-stream",
    )
    reviewed_at = datetime.fromisoformat(args.reviewed_at)
    if reviewed_at.tzinfo is None:
        raise ValueError("--reviewed-at must include a timezone")
    inference = UniHamInferenceRequest(
        project_id=plan.request.project_id,
        run_id=args.inference_run_id,
        candidate_id=plan.request.candidate_id,
        input_structure=structure,
        model_pickle=model,
        non_soc_graph=graph_result.non_soc_graph,
        soc_graph=graph_result.soc_graph,
        model_id="uni-hamgnn-soc-2.1-zenodo-17239078",
        model_source_url="https://zenodo.org/records/17239078",
        model_revision="zenodo-record-17239078",
        weights_license="CC-BY-4.0",
        hamgnn_source_revision=args.hamgnn_revision,
        predictor_script_sha256=args.predictor_sha256,
        input_trust=UniHamInputTrust(
            trusted_executable_inputs=True,
            reviewed_by="hermes-runtime-hash-audit",
            reviewed_at=reviewed_at.astimezone(UTC),
            review_basis=(
                "Exact child CIF, non-SCF graph pair, model and executables are "
                "hash-bound; no self-consistent DFT was invoked"
            ),
        ),
        device="cuda",
        calculate_mae=False,
        is_mock=False,
    )
    h_result = UniHamRemoteClient(
        artifact_store=store,
        ssh_host_alias=args.host,
        remote_artifact_root=args.remote_root,
        remote_worker_python=args.remote_python,
        remote_worker_path=args.remote_uniham_worker,
        remote_predictor_path=args.remote_predictor,
        cuda_visible_device=args.cuda_visible_device,
    ).run(
        inference,
        non_soc_manifest=graph_result.non_soc_manifest,
        soc_manifest=graph_result.soc_manifest,
    )
    remote_hamiltonian = next(
        item
        for item in h_result.worker_response.produced_artifacts
        if item.root_relative_path.endswith("/output/hamiltonian.npy")
    )
    band_request = UniHamBandRequest(
        project_id=inference.project_id,
        run_id=args.band_run_id,
        candidate_id=inference.candidate_id,
        input_structure=structure,
        soc_graph=graph_result.soc_graph,
        soc_manifest=graph_result.soc_manifest,
        hamiltonian=UniHamArtifactFile(
            artifact_uri=f"artifact://{remote_hamiltonian.root_relative_path}",
            root_relative_path=remote_hamiltonian.root_relative_path,
            sha256=remote_hamiltonian.sha256,
            size_bytes=remote_hamiltonian.size_bytes,
            media_type=remote_hamiltonian.media_type,
        ),
        hamiltonian_operation_key=h_result.plan.operation_key,
        hamgnn_source_revision=args.hamgnn_revision,
        band_calculator_sha256=args.band_cal_sha256,
        nk=args.nk,
        structure_name="crystal",
        soc_switch=True,
        spin_colinear=False,
        auto_mode=True,
        ham_type="openmx",
        nao_max=26,
        device="cpu",
        is_mock=False,
    )
    band_result = UniHamBandRemoteClient(
        artifact_store=store,
        ssh_host_alias=args.host,
        remote_artifact_root=args.remote_root,
        remote_worker_python=args.remote_python,
        remote_worker_path=args.remote_band_worker,
        remote_band_cal_executable=args.remote_band_cal,
    ).run(band_request)
    parsed = parse_hamgnn_band_dat(
        store.read_bytes(band_result.band_data_pointer.uri).decode("utf-8")
    )
    target = min(
        range(len(parsed.band_energies_ev)),
        key=lambda index: min(abs(value) for value in parsed.band_energies_ev[index]),
    )
    nodes = tuple(
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
            fermi_energy_ev=0.0,
            k_distances_inv_angstrom=parsed.k_distances_inv_angstrom,
            high_symmetry_indices=nodes,
            band_energies_ev=parsed.band_energies_ev,
            target_band_index=target,
            orbital_projections=(),
            contributor_sublattice_connected=None,
            soc_explicit=True,
        ),
        FlatBandPolicy(
            maximum_bandwidth_ev=0.05,
            near_fermi_window_ev=0.05,
            degeneracy_tolerance_ev=0.0001,
            slope_difference_tolerance_ev_angstrom=0.001,
            minimum_transition_metal_weight_fraction=0.2,
            minimum_tm_ligand_weight_fraction=0.6,
            require_soc_explicit=True,
        ),
    )
    payload = {
        "candidate_id": inference.candidate_id,
        "graph_prep_operation_key": plan.operation_key,
        "gpu_hamiltonian_operation_key": h_result.plan.operation_key,
        "band_operation_key": band_result.plan.operation_key,
        "hamiltonian_sha256": h_result.hamiltonian_pointer.sha256,
        "band_data_sha256": band_result.band_data_pointer.sha256,
        "band_plot": band_result.band_plot_pointer.model_dump(mode="json"),
        "flat_band_assessment": assessment.model_dump(mode="json"),
        "self_consistent_dft_invocations": 0,
        "gpu_device_index": args.cuda_visible_device,
        "scientific_conclusion": False,
    }
    reference = store.write_json(
        f"scientific_loop/generated-uniham-e2e/{args.band_run_id}/result.json",
        payload,
        immutable=True,
    )
    print(json.dumps({"result_uri": reference.uri, **payload}, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
