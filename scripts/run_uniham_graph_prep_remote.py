#!/usr/bin/env python3
"""Run the hash-bound child-CIF -> non-SCF Uni-HamGNN graph stage."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from material_agent.inspiration.models import ArtifactPointerV1
from material_agent.ml_screening.uniham_graph_prep_models import (
    NonSCFGraphPrepParameters,
    UniHamGraphPrepRequest,
    UniHamGraphPrepToolchain,
)
from material_agent.ml_screening.uniham_graph_prep_remote import (
    UniHamGraphPrepRemoteClient,
)
from material_agent.retrieval.storage import LocalArtifactStore


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--artifact-root", type=Path, required=True)
    parser.add_argument("--structure-uri", required=True)
    parser.add_argument("--candidate-id", required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--host", required=True)
    parser.add_argument("--remote-root", required=True)
    parser.add_argument("--remote-python", required=True)
    parser.add_argument("--remote-source-root", required=True)
    parser.add_argument("--openmx-postprocess", required=True)
    parser.add_argument("--read-openmx", required=True)
    parser.add_argument("--graph-data-gen", required=True)
    parser.add_argument("--dft-data-root", required=True)
    parser.add_argument("--hamgnn-source-root", required=True)
    parser.add_argument("--openmx-postprocess-sha256", required=True)
    parser.add_argument("--read-openmx-sha256", required=True)
    parser.add_argument("--graph-data-gen-sha256", required=True)
    parser.add_argument("--dft-data-manifest-sha256", required=True)
    parser.add_argument("--hamgnn-revision", required=True)
    parser.add_argument("--graph-generator-revision", required=True)
    args = parser.parse_args()

    store = LocalArtifactStore(args.artifact_root)
    reference = store.inspect(args.structure_uri, media_type="chemical/x-cif")
    pointer = ArtifactPointerV1(
        uri=reference.uri,
        sha256=reference.sha256,
        size_bytes=reference.size_bytes,
        media_type=reference.media_type,
    )
    request = UniHamGraphPrepRequest(
        project_id="hermes-materials",
        run_id=args.run_id,
        candidate_id=args.candidate_id,
        input_structure=pointer,
        parameters=NonSCFGraphPrepParameters(),
        hamgnn_source_revision=args.hamgnn_revision,
        graph_generator_revision=args.graph_generator_revision,
    )
    result = UniHamGraphPrepRemoteClient(
        artifact_store=store,
        ssh_host_alias=args.host,
        remote_artifact_root=args.remote_root,
        remote_worker_python=args.remote_python,
        remote_source_root=args.remote_source_root,
        remote_openmx_postprocess=args.openmx_postprocess,
        remote_read_openmx=args.read_openmx,
        remote_graph_data_gen=args.graph_data_gen,
        remote_dft_data_root=args.dft_data_root,
        remote_hamgnn_source_root=args.hamgnn_source_root,
        toolchain=UniHamGraphPrepToolchain(
            openmx_postprocess_sha256=args.openmx_postprocess_sha256,
            read_openmx_sha256=args.read_openmx_sha256,
            graph_data_gen_sha256=args.graph_data_gen_sha256,
            dft_data_manifest_sha256=args.dft_data_manifest_sha256,
        ),
    ).run(request)
    print(
        json.dumps(
            {
                "result_id": result.result_id,
                "operation_key": result.plan.operation_key,
                "non_soc_graph": result.non_soc_graph.graph_data.model_dump(
                    mode="json"
                ),
                "soc_graph": result.soc_graph.graph_data.model_dump(mode="json"),
                "self_consistent_dft_invocations": (
                    result.self_consistent_dft_invocations
                ),
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
