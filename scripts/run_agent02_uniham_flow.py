#!/usr/bin/env python3
"""Run the Agent02 Uni-HamGNN companion from immutable local inputs."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from material_agent.ml_screening.uniham_client import (
    UniHamFlowRunner,
    UniHamSubprocessClient,
)
from material_agent.ml_screening.uniham_models import UniHamInferenceRequest
from material_agent.retrieval.storage import LocalArtifactStore


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--request", type=Path, required=True)
    parser.add_argument("--artifact-root", type=Path, required=True)
    parser.add_argument("--worker-python", type=Path, required=True)
    parser.add_argument("--predictor-script", type=Path, required=True)
    parser.add_argument("--cuda-visible-device")
    args = parser.parse_args()

    request = UniHamInferenceRequest.model_validate_json(
        args.request.read_text(encoding="utf-8")
    )
    store = LocalArtifactStore(args.artifact_root)
    runner = UniHamFlowRunner(
        artifact_store=store,
        client=UniHamSubprocessClient(
            worker_python=args.worker_python,
            predictor_script=args.predictor_script,
            artifact_root=store.root,
            cuda_visible_devices=args.cuda_visible_device,
        ),
    )
    result = runner.execute(request)
    print(
        json.dumps(
            result.model_dump(mode="json"),
            sort_keys=True,
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
