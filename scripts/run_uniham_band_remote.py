#!/usr/bin/env python3
"""Run or recover learned-Hamiltonian band postprocessing over SSH."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from material_agent.ml_screening.uniham_band_models import UniHamBandRequest
from material_agent.ml_screening.uniham_band_remote import UniHamBandRemoteClient
from material_agent.retrieval.storage import LocalArtifactStore


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--request", type=Path, required=True)
    parser.add_argument("--local-artifact-root", type=Path, required=True)
    parser.add_argument("--ssh-host-alias", required=True)
    parser.add_argument("--remote-artifact-root", required=True)
    parser.add_argument("--remote-worker-python", required=True)
    parser.add_argument("--remote-worker-path", required=True)
    parser.add_argument("--remote-band-cal-executable", required=True)
    args = parser.parse_args()

    request = UniHamBandRequest.model_validate_json(args.request.read_bytes())
    client = UniHamBandRemoteClient(
        artifact_store=LocalArtifactStore(args.local_artifact_root),
        ssh_host_alias=args.ssh_host_alias,
        remote_artifact_root=args.remote_artifact_root,
        remote_worker_python=args.remote_worker_python,
        remote_worker_path=args.remote_worker_path,
        remote_band_cal_executable=args.remote_band_cal_executable,
    )
    result = client.run(request)
    print(json.dumps(result.model_dump(mode="json"), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
