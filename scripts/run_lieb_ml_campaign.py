#!/usr/bin/env python3
"""Run all structure-backed Lieb hypotheses through the no-DFT ML stack."""

from __future__ import annotations

import argparse
import json
import warnings
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pymatgen.core import Structure

from material_agent.inspiration.models import ArtifactPointerV1
from material_agent.inspiration.operator_planning import (
    execution_request_from_compiled_operation_plan,
)
from material_agent.integration.electronic_structure import (
    FlatBandAnalysisInput,
    FlatBandPolicy,
    assess_flat_band,
    parse_hamgnn_band_dat,
)
from material_agent.ml_screening.uniham_band_models import UniHamBandRequest
from material_agent.ml_screening.uniham_band_remote import UniHamBandRemoteClient
from material_agent.ml_screening.uniham_graph_prep_models import (
    NonSCFGraphPrepParameters,
    UniHamGraphPrepRequest,
    UniHamGraphPrepToolchain,
)
from material_agent.ml_screening.uniham_graph_prep_remote import (
    UniHamGraphPrepRemoteClient,
)
from material_agent.ml_screening.uniham_models import (
    UniHamArtifactFile,
    UniHamInferenceRequest,
    UniHamInputTrust,
)
from material_agent.ml_screening.uniham_remote import UniHamRemoteClient
from material_agent.retrieval.storage import LocalArtifactStore
from material_agent.softchem.operations import (
    StructureOperationPlanV2,
    execute_registered_structure_operation,
)


def _args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--route", action="append", required=True, metavar="NAME=RESULT")
    parser.add_argument("--artifact-root", type=Path, required=True)
    parser.add_argument("--workers", type=int, default=3)
    parser.add_argument("--only-sha", action="append", default=[])
    parser.add_argument("--extra-cif", action="append", default=[], metavar="ID=PATH")
    parser.add_argument("--run-suffix", default="v1")
    parser.add_argument("--reviewed-at", default="2026-08-26T09:00:00Z")
    parser.add_argument("--host", default="whu-ext")
    parser.add_argument(
        "--remote-root",
        default="/home/huayiming/Workspace/lab_codex/materials_scientific_stack",
    )
    parser.add_argument(
        "--remote-python",
        default=(
            "/home/huayiming/Workspace/lab_codex/materials_scientific_stack/"
            "unihamgnn/env/py311-modern/bin/python"
        ),
    )
    parser.add_argument(
        "--remote-platform-source",
        default=(
            "/home/huayiming/Workspace/lab_codex/"
            "materials_screening_agent_gpu/platform/src"
        ),
    )
    parser.add_argument("--cuda-visible-device", default="6")
    return parser.parse_args()


def _route_pair(value: str) -> tuple[str, Path]:
    name, separator, raw_path = value.partition("=")
    if not separator or not name or not raw_path:
        raise ValueError("--route must be NAME=RESULT_JSON")
    return name, Path(raw_path).resolve()


def _pointer(reference: Any) -> ArtifactPointerV1:
    return ArtifactPointerV1(
        uri=reference.uri,
        sha256=reference.sha256,
        size_bytes=reference.size_bytes,
        media_type=reference.media_type,
    )


def _source_path(result_path: Path, uri: str) -> Path:
    path = result_path.parents[2] / uri.removeprefix("artifact://")
    return path.resolve(strict=True)


def _collect(
    routes: list[tuple[str, Path]], store: LocalArtifactStore
) -> tuple[dict[str, dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    structures: dict[str, dict[str, Any]] = {}
    hypotheses: list[dict[str, Any]] = []
    children: list[dict[str, Any]] = []
    for route_name, result_path in routes:
        payload = json.loads(result_path.read_text())
        graph = payload["research_graph"]
        database = {
            item["database_candidate_id"]: item
            for item in graph["database_candidates"]
        }
        candidates = graph["candidates"]["candidates"]
        plan_by_candidate = {
            binding["candidate_id"]: next(
                plan
                for plan in graph["transformation_audit"]["plans"]
                if plan["plan_id"] == binding["plan_id"]
            )
            for binding in graph["transformation_audit"]["bindings"]
        }
        for candidate in candidates:
            mapped: list[str] = []
            for database_id in candidate["database_candidate_ids"]:
                parent = database.get(database_id)
                if parent is None or not parent.get("structure_artifact_uri"):
                    continue
                source = _source_path(result_path, parent["structure_artifact_uri"])
                payload_bytes = source.read_bytes()
                expected = parent["structure_artifact_sha256"]
                reference = store.write_bytes(
                    f"inputs/parents/{expected}.cif",
                    payload_bytes,
                    "chemical/x-cif",
                    immutable=True,
                )
                if reference.sha256 != expected:
                    raise ValueError("source CIF differs from research graph hash")
                structures.setdefault(
                    expected,
                    {
                        "structure_sha256": expected,
                        "pointer": _pointer(reference),
                        "source_kind": "DATABASE_PARENT",
                        "source_database": parent["source_database"],
                        "source_material_id": parent["source_material_id"],
                        "formula": parent["formula"],
                        "route_names": set(),
                        "hypothesis_ids": set(),
                    },
                )
                structures[expected]["route_names"].add(route_name)
                structures[expected]["hypothesis_ids"].add(candidate["candidate_id"])
                mapped.append(expected)
            hypotheses.append(
                {
                    "route": route_name,
                    "candidate_id": candidate["candidate_id"],
                    "formula": candidate.get("formula"),
                    "parent_structure_sha256s": sorted(set(mapped)),
                    "ml_status": "PENDING" if mapped else "NO_STRUCTURE_INPUT",
                    "operator_plan_id": (
                        plan_by_candidate.get(candidate["candidate_id"], {}).get("plan_id")
                    ),
                }
            )
        for candidate_id, plan_payload in plan_by_candidate.items():
            plan = StructureOperationPlanV2.model_validate_json(
                json.dumps(plan_payload, sort_keys=True)
            )
            parent_bytes = _source_path(
                result_path, plan.parent_structure_artifact.uri
            ).read_bytes()
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                parent = Structure.from_str(parent_bytes.decode(), fmt="cif")
            execution = execute_registered_structure_operation(
                execution_request_from_compiled_operation_plan(plan),
                parent_structure=parent,
                parent_artifact_bytes=parent_bytes,
            )
            child_record = {
                "route": route_name,
                "candidate_id": candidate_id,
                "plan_id": plan.plan_id,
                "operator_id": plan.operator_id,
                "compile_prior_decision": str(plan.compile_prior_decision),
                "execution_status": execution.plan.status.value,
                "reason_codes": [
                    check.check_id
                    for check in execution.plan.validation_checks
                    if check.status != "PASS"
                ],
            }
            if execution.artifact_bytes is not None:
                assert execution.plan.output_structure_artifact is not None
                expected = execution.plan.output_structure_artifact.sha256
                reference = store.write_bytes(
                    f"inputs/children/{expected}.cif",
                    execution.artifact_bytes,
                    "chemical/x-cif",
                    immutable=True,
                )
                if reference.sha256 != expected:
                    raise ValueError("child CIF differs from registry execution hash")
                structures.setdefault(
                    expected,
                    {
                        "structure_sha256": expected,
                        "pointer": _pointer(reference),
                        "source_kind": "REGISTRY_CHILD",
                        "source_database": None,
                        "source_material_id": plan.plan_id,
                        "formula": execution.output_structure.composition.reduced_formula,
                        "route_names": {route_name},
                        "hypothesis_ids": {candidate_id},
                    },
                )
                child_record["child_structure_sha256"] = expected
                child_record["ml_status"] = "PENDING"
            else:
                child_record["child_structure_sha256"] = None
                child_record["ml_status"] = "NO_CHILD_CIF"
            children.append(child_record)
    return structures, hypotheses, children


def _assess(store: LocalArtifactStore, uri: str) -> dict[str, Any]:
    parsed = parse_hamgnn_band_dat(store.read_bytes(uri).decode())
    target = min(
        range(len(parsed.band_energies_ev)),
        key=lambda band: min(abs(value) for value in parsed.band_energies_ev[band]),
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
    result = assess_flat_band(
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
    return result.model_dump(mode="json")


def _run_one(
    record: dict[str, Any],
    *,
    store: LocalArtifactStore,
    args: argparse.Namespace,
    reviewed_at: datetime,
) -> dict[str, Any]:
    sha = record["structure_sha256"]
    candidate_id = f"lieb-{sha[:20]}"
    common_root = args.remote_root
    platform = args.remote_platform_source
    graph_client = UniHamGraphPrepRemoteClient(
        artifact_store=store,
        ssh_host_alias=args.host,
        remote_artifact_root=common_root,
        remote_worker_python=args.remote_python,
        remote_source_root=platform,
        remote_openmx_postprocess=(
            f"{common_root}/openmx-postprocess-gcc/openmx_postprocess"
        ),
        remote_read_openmx=f"{common_root}/openmx-postprocess-gcc/read_openmx",
        remote_graph_data_gen=(
            f"{common_root}/unihamgnn/env/py311-modern/bin/graph_data_gen"
        ),
        remote_dft_data_root=(
            f"{common_root}/openmx-data19/deb-extract/deb-extract.tmp/"
            "usr/share/openmx/DFT_DATA19"
        ),
        remote_hamgnn_source_root=f"{common_root}/unihamgnn/source/HamGNN",
        toolchain=UniHamGraphPrepToolchain(
            openmx_postprocess_sha256=(
                "8eb7369aa2d09b0cb6bf01175f77985f2ecde80c6734cf2b214f9e2ea8636ec0"
            ),
            read_openmx_sha256=(
                "ea94b97b0f118ee4107858344396eb9a04fdfd8b199cc695f1389e5a4615878f"
            ),
            graph_data_gen_sha256=(
                "fd4637f762ea9e38552ed5d9dad226e59bc1ed7e5c55767bbff25695b8c098e4"
            ),
            dft_data_manifest_sha256=(
                "c8a601011ca970ea4296a21444ade0569dd0d679bb8caa5ab552bb8298cfce0a"
            ),
        ),
    )
    graph = graph_client.run(
        UniHamGraphPrepRequest(
            project_id="hermes-lieb-ml",
            run_id=f"lieb-graph-{sha[:20]}-{args.run_suffix}",
            candidate_id=candidate_id,
            input_structure=record["pointer"],
            parameters=NonSCFGraphPrepParameters(),
            hamgnn_source_revision="2fe5debb28711dae72a90ba09f9c44bec39a663c",
            graph_generator_revision="2fe5debb28711dae72a90ba09f9c44bec39a663c",
        )
    )
    staged = graph.plan.input_root_relative_path
    structure = UniHamArtifactFile(
        artifact_uri=f"artifact://{staged}",
        root_relative_path=staged,
        sha256=sha,
        size_bytes=record["pointer"].size_bytes,
        media_type="chemical/x-cif",
    )
    inference = UniHamInferenceRequest(
        project_id="hermes-lieb-ml",
        run_id=f"lieb-uniham-{sha[:20]}-{args.run_suffix}",
        candidate_id=candidate_id,
        input_structure=structure,
        model_pickle=UniHamArtifactFile(
            artifact_uri=(
                "artifact://unihamgnn/assets/weights/uni-hamgnn_2_1.pkl"
            ),
            root_relative_path="unihamgnn/assets/weights/uni-hamgnn_2_1.pkl",
            sha256=(
                "5d0257a54dd1c026c08fa29adc537cf0023def1560361da964d289102ec36b06"
            ),
            size_bytes=927026099,
            media_type="application/octet-stream",
        ),
        non_soc_graph=graph.non_soc_graph,
        soc_graph=graph.soc_graph,
        model_id="uni-hamgnn-soc-2.1-zenodo-17239078",
        model_source_url="https://zenodo.org/records/17239078",
        model_revision="zenodo-record-17239078",
        weights_license="CC-BY-4.0",
        hamgnn_source_revision="2fe5debb28711dae72a90ba09f9c44bec39a663c",
        predictor_script_sha256=(
            "ab28414046eed80d0e501752c7c99f1f767d53bfbc4aa958a35485d8afe84e7e"
        ),
        input_trust=UniHamInputTrust(
            trusted_executable_inputs=True,
            reviewed_by="hermes-runtime-hash-audit",
            reviewed_at=reviewed_at,
            review_basis=(
                "Exact candidate CIF and runtime non-SCF graph pair are hash-bound; "
                "self-consistent DFT is forbidden"
            ),
        ),
        device="cuda",
        calculate_mae=False,
        is_mock=False,
    )
    h_result = UniHamRemoteClient(
        artifact_store=store,
        ssh_host_alias=args.host,
        remote_artifact_root=common_root,
        remote_worker_python=args.remote_python,
        remote_worker_path=f"{platform}/material_agent/ml_screening/uniham_worker.py",
        remote_predictor_path=(
            f"{common_root}/unihamgnn/source/HamGNN/Uni-HamGNN/"
            "Uni-HamiltonianPredictor.py"
        ),
        cuda_visible_device=args.cuda_visible_device,
    ).run(
        inference,
        non_soc_manifest=graph.non_soc_manifest,
        soc_manifest=graph.soc_manifest,
    )
    remote_h = next(
        item
        for item in h_result.worker_response.produced_artifacts
        if item.root_relative_path.endswith("/output/hamiltonian.npy")
    )
    band = UniHamBandRemoteClient(
        artifact_store=store,
        ssh_host_alias=args.host,
        remote_artifact_root=common_root,
        remote_worker_python=args.remote_python,
        remote_worker_path=f"{platform}/material_agent/ml_screening/uniham_band_worker.py",
        remote_band_cal_executable=(
            f"{common_root}/unihamgnn/env/py311-modern/bin/band_cal"
        ),
    ).run(
        UniHamBandRequest(
            project_id="hermes-lieb-ml",
            run_id=f"lieb-band-{sha[:20]}-{args.run_suffix}",
            candidate_id=candidate_id,
            input_structure=structure,
            soc_graph=graph.soc_graph,
            soc_manifest=graph.soc_manifest,
            hamiltonian=UniHamArtifactFile(
                artifact_uri=f"artifact://{remote_h.root_relative_path}",
                root_relative_path=remote_h.root_relative_path,
                sha256=remote_h.sha256,
                size_bytes=remote_h.size_bytes,
                media_type=remote_h.media_type,
            ),
            hamiltonian_operation_key=h_result.plan.operation_key,
            hamgnn_source_revision="2fe5debb28711dae72a90ba09f9c44bec39a663c",
            band_calculator_sha256=(
                "e3c42110285408a2ed7848574d9afd18298f244cf1a37d7c3e90aa8c726a76fc"
            ),
            nk=120,
            structure_name="crystal",
            soc_switch=True,
            spin_colinear=False,
            auto_mode=True,
            ham_type="openmx",
            nao_max=26,
            device="cpu",
            is_mock=False,
        )
    )
    return {
        "status": "SUCCEEDED",
        "structure_sha256": sha,
        "source_kind": record["source_kind"],
        "formula": record["formula"],
        "route_names": sorted(record["route_names"]),
        "hypothesis_ids": sorted(record["hypothesis_ids"]),
        "self_consistent_dft_invocations": graph.self_consistent_dft_invocations,
        "graph_prep_operation_key": graph.plan.operation_key,
        "gpu_hamiltonian_operation_key": h_result.plan.operation_key,
        "gpu_device_index": args.cuda_visible_device,
        "gpu_device_name": h_result.worker_response.runtime_provenance.cuda_device_name,
        "hamiltonian_sha256": h_result.hamiltonian_pointer.sha256,
        "band_operation_key": band.plan.operation_key,
        "band_data": band.band_data_pointer.model_dump(mode="json"),
        "band_plot": band.band_plot_pointer.model_dump(mode="json"),
        "flat_band_assessment": _assess(store, band.band_data_pointer.uri),
        "evidence_level": "NONE_UNBENCHMARKED_ML",
        "scientific_conclusion": False,
    }


def _markdown(payload: dict[str, Any], root: Path) -> str:
    lines = [
        "# Hermes Lieb hypotheses ML-only campaign",
        "",
        (
            "> Uni-HamGNN learned-Hamiltonian screening; no self-consistent DFT. "
            "Orbital projection, connected Lieb graph and calibrated uncertainty "
            "remain unresolved."
        ),
        "",
        "## Summary",
        "",
        f"- Hypotheses: {len(payload['hypotheses'])}",
        f"- Unique structures attempted: {len(payload['structure_results'])}",
        f"- ML succeeded: {payload['summary']['ml_succeeded']}",
        f"- ML failed: {payload['summary']['ml_failed']}",
        f"- No structure input: {payload['summary']['no_structure_input']}",
        f"- Registry children generated: {payload['summary']['children_generated']}",
        "",
        "## Structure-level ML results",
        "",
        "| SHA | kind | formula | ML | W (eV) | verdict | reasons |",
        "|---|---|---|---|---:|---|---|",
    ]
    for result in payload["structure_results"]:
        if result["status"] != "SUCCEEDED":
            lines.append(
                f"| `{result['structure_sha256'][:12]}` | {result['source_kind']} | "
                f"{result.get('formula') or ''} | FAILED |  |  | {result['error']} |"
            )
            continue
        assessment = result["flat_band_assessment"]
        lines.append(
            f"| `{result['structure_sha256'][:12]}` | {result['source_kind']} | "
            f"{result.get('formula') or ''} | SUCCEEDED | "
            f"{assessment['target_bandwidth_ev']:.6f} | {assessment['verdict']} | "
            f"{', '.join(assessment['reason_codes']) or 'none'} |"
        )
    lines.extend(["", "## Band plots", ""])
    for result in payload["structure_results"]:
        if result["status"] != "SUCCEEDED":
            continue
        relative = result["band_plot"]["uri"].removeprefix("artifact://")
        absolute = root / relative
        lines.extend(
            [
                f"### {result.get('formula') or result['structure_sha256'][:12]}",
                "",
                f"![SOC ML band]({absolute})",
                "",
            ]
        )
    lines.extend(["## Hypothesis coverage", ""])
    for item in payload["hypotheses"]:
        lines.append(
            f"- `{item['candidate_id']}` ({item['route']}): {item['ml_status']}"
        )
    return "\n".join(lines) + "\n"


def main() -> int:
    args = _args()
    if not 1 <= args.workers <= 8:
        raise ValueError("--workers must be within [1, 8]")
    routes = [_route_pair(value) for value in args.route]
    store = LocalArtifactStore(args.artifact_root)
    structures, hypotheses, children = _collect(routes, store)
    for value in args.extra_cif:
        candidate_id, separator, raw_path = value.partition("=")
        if not separator or not candidate_id or not raw_path:
            raise ValueError("--extra-cif must be ID=PATH")
        source = Path(raw_path).resolve(strict=True)
        payload = source.read_bytes()
        reference = store.write_bytes(
            f"inputs/extra/{candidate_id}.cif",
            payload,
            "chemical/x-cif",
            immutable=True,
        )
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            structure = Structure.from_str(payload.decode(), fmt="cif")
        structures.setdefault(
            reference.sha256,
            {
                "structure_sha256": reference.sha256,
                "pointer": _pointer(reference),
                "source_kind": "DEEPSEEK_REGISTRY_CHILD",
                "source_database": None,
                "source_material_id": candidate_id,
                "formula": structure.composition.reduced_formula,
                "route_names": {"deepseek-resubmission"},
                "hypothesis_ids": {candidate_id},
            },
        )
    if args.only_sha:
        selected = set(args.only_sha)
        unknown = selected.difference(structures)
        if unknown:
            raise ValueError(f"--only-sha did not match structures: {sorted(unknown)}")
        structures = {sha: structures[sha] for sha in sorted(selected)}
    reviewed_at = datetime.fromisoformat(args.reviewed_at)
    if reviewed_at.tzinfo is None:
        raise ValueError("--reviewed-at must include timezone")
    reviewed_at = reviewed_at.astimezone(UTC)
    results: dict[str, dict[str, Any]] = {}
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {
            pool.submit(
                _run_one,
                record,
                store=store,
                args=args,
                reviewed_at=reviewed_at,
            ): sha
            for sha, record in structures.items()
        }
        for future in as_completed(futures):
            sha = futures[future]
            try:
                results[sha] = future.result()
            except Exception as exc:  # noqa: BLE001
                record = structures[sha]
                results[sha] = {
                    "status": "FAILED",
                    "structure_sha256": sha,
                    "source_kind": record["source_kind"],
                    "formula": record["formula"],
                    "route_names": sorted(record["route_names"]),
                    "hypothesis_ids": sorted(record["hypothesis_ids"]),
                    "error": f"{type(exc).__name__}: {exc}",
                    "scientific_conclusion": False,
                }
    for item in hypotheses:
        if not item["parent_structure_sha256s"]:
            continue
        if not all(sha in results for sha in item["parent_structure_sha256s"]):
            item["ml_status"] = "NOT_SELECTED_IN_THIS_RUN"
            continue
        statuses = [results[sha]["status"] for sha in item["parent_structure_sha256s"]]
        item["ml_status"] = (
            "PARENT_ML_SUCCEEDED"
            if all(status == "SUCCEEDED" for status in statuses)
            else "PARENT_ML_FAILED"
        )
    for item in children:
        sha = item.get("child_structure_sha256")
        if sha and sha in results:
            item["ml_status"] = (
                "CHILD_ML_SUCCEEDED"
                if results[sha]["status"] == "SUCCEEDED"
                else "CHILD_ML_FAILED"
            )
        elif sha:
            item["ml_status"] = "NOT_SELECTED_IN_THIS_RUN"
    ordered = [results[sha] for sha in sorted(results)]
    payload = {
        "schema_version": "hermes-lieb-ml-campaign-v1",
        "routes": [name for name, _path in routes],
        "execution_boundary": {
            "self_consistent_dft_allowed": False,
            "graph_preparation": "CPU_NON_SCF_ATOMIC_BASIS",
            "hamiltonian_inference": "GPU_UNIHAMGNN_L40S",
            "band_postprocessing": "CPU_BAND_CAL",
            "local_validator": "DETERMINISTIC_50_MEV_AND_CROSSING_RULES",
        },
        "hypotheses": hypotheses,
        "registry_children": children,
        "structure_results": ordered,
        "summary": {
            "ml_succeeded": sum(item["status"] == "SUCCEEDED" for item in ordered),
            "ml_failed": sum(item["status"] == "FAILED" for item in ordered),
            "no_structure_input": sum(
                item["ml_status"] == "NO_STRUCTURE_INPUT" for item in hypotheses
            ),
            "children_generated": sum(
                item.get("child_structure_sha256") is not None for item in children
            ),
        },
        "scientific_conclusion": False,
    }
    store.write_json("campaign/result.json", payload, immutable=True)
    store.write_text(
        "campaign/report.md",
        _markdown(payload, store.root),
        "text/markdown",
        immutable=True,
    )
    print(json.dumps(payload["summary"], indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
