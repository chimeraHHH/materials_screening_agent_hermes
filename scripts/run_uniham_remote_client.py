#!/usr/bin/env python3
"""Run or recover one hash-bound Uni-HamGNN operation over SSH."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from material_agent.ml_screening.uniham_models import (
    UniHamGraphManifest,
    UniHamInferenceRequest,
)
from material_agent.ml_screening.uniham_remote import UniHamRemoteClient
from material_agent.retrieval.storage import LocalArtifactStore


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--request", type=Path, required=True)
    parser.add_argument("--non-soc-manifest", type=Path, required=True)
    parser.add_argument("--soc-manifest", type=Path, required=True)
    parser.add_argument("--local-artifact-root", type=Path, required=True)
    parser.add_argument("--ssh-host-alias", required=True)
    parser.add_argument("--remote-artifact-root", required=True)
    parser.add_argument("--remote-worker-python", required=True)
    parser.add_argument("--remote-worker-path", required=True)
    parser.add_argument("--remote-predictor-path", required=True)
    parser.add_argument("--cuda-visible-device", required=True)
    args = parser.parse_args()

    request = UniHamInferenceRequest.model_validate_json(args.request.read_bytes())
    non_soc = UniHamGraphManifest.model_validate_json(
        args.non_soc_manifest.read_bytes()
    )
    soc = UniHamGraphManifest.model_validate_json(args.soc_manifest.read_bytes())
    client = UniHamRemoteClient(
        artifact_store=LocalArtifactStore(args.local_artifact_root),
        ssh_host_alias=args.ssh_host_alias,
        remote_artifact_root=args.remote_artifact_root,
        remote_worker_python=args.remote_worker_python,
        remote_worker_path=args.remote_worker_path,
        remote_predictor_path=args.remote_predictor_path,
        cuda_visible_device=args.cuda_visible_device,
    )
    result = client.run(
        request,
        non_soc_manifest=non_soc,
        soc_manifest=soc,
    )
    print(json.dumps(result.model_dump(mode="json"), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
