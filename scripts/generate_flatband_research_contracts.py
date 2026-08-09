#!/usr/bin/env python3
"""Generate or verify the content-addressed draft research schema bundle."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from material_agent.research.flatband_contracts import (
    BenchmarkSplitManifestV1,
    BlindingManifestV1,
    ExpertAdjudicationV1,
    ExpertRegistryV1,
    FlatBandBenchmarkCaseV1,
    FlatBandEvidenceV1,
    HypothesisPacketV1,
    RawExpertAnnotationV1,
    ResearchRunLedgerV1,
    SourceRecordRefV1,
    SystemRankingV1,
)


MODELS = (
    SourceRecordRefV1,
    FlatBandEvidenceV1,
    FlatBandBenchmarkCaseV1,
    HypothesisPacketV1,
    SystemRankingV1,
    RawExpertAnnotationV1,
    ExpertAdjudicationV1,
    BenchmarkSplitManifestV1,
    BlindingManifestV1,
    ResearchRunLedgerV1,
    ExpertRegistryV1,
)
REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
OUTPUT_DIRECTORY = (
    REPOSITORY_ROOT / "artifacts" / "experiment" / "flatband-benchmark-20260809"
)
SCHEMA_PATH = OUTPUT_DIRECTORY / "research_contracts.schema.json"
HASH_PATH = OUTPUT_DIRECTORY / "research_contracts.schema.sha256"


def generated_bytes() -> bytes:
    document = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": "urn:materials-screening-agent:flatband-research-contracts:v0-draft",
        "schema_version": "flatband-research-contract-bundle-v0-draft",
        "models": {
            model.__name__: model.model_json_schema(mode="validation") for model in MODELS
        },
    }
    return (
        json.dumps(
            document,
            ensure_ascii=False,
            sort_keys=True,
            indent=2,
            allow_nan=False,
        )
        + "\n"
    ).encode("utf-8")


def hash_bytes(payload: bytes) -> bytes:
    return f"{hashlib.sha256(payload).hexdigest()}  {SCHEMA_PATH.name}\n".encode()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--check",
        action="store_true",
        help="fail unless committed schema and hash exactly match production models",
    )
    arguments = parser.parse_args()
    payload = generated_bytes()
    digest = hash_bytes(payload)
    if arguments.check:
        if not SCHEMA_PATH.is_file() or not HASH_PATH.is_file():
            raise SystemExit("flat-band research contract artifacts are missing")
        if SCHEMA_PATH.read_bytes() != payload or HASH_PATH.read_bytes() != digest:
            raise SystemExit("flat-band research contract artifacts drifted")
        print("Flat-band research contract bundle valid")
        return 0
    OUTPUT_DIRECTORY.mkdir(parents=True, exist_ok=True)
    SCHEMA_PATH.write_bytes(payload)
    HASH_PATH.write_bytes(digest)
    print(hashlib.sha256(payload).hexdigest())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
