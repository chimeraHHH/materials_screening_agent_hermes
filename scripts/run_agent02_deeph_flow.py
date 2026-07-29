#!/usr/bin/env python3
"""Run the Agent02 DeepH companion flow from explicit immutable inputs."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from material_agent.ml_screening.deeph_client import (
    DeepHFlowRunner,
    DeepHSubprocessClient,
)
from material_agent.ml_screening.deeph_models import DeepHInferenceRequest
from material_agent.retrieval.storage import LocalArtifactStore


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--request", type=Path, required=True)
    parser.add_argument("--artifact-root", type=Path, required=True)
    parser.add_argument("--worker-python", type=Path, required=True)
    parser.add_argument("--deeph-executable", type=Path, required=True)
    args = parser.parse_args()

    request = DeepHInferenceRequest.model_validate_json(
        args.request.read_text(encoding="utf-8")
    )
    store = LocalArtifactStore(args.artifact_root)
    runner = DeepHFlowRunner(
        artifact_store=store,
        client=DeepHSubprocessClient(
            worker_python=args.worker_python,
            deeph_executable=args.deeph_executable,
            artifact_root=store.root,
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
