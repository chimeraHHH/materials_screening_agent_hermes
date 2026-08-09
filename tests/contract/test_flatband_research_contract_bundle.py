from __future__ import annotations

import hashlib
import json
from collections import Counter
from pathlib import Path
from typing import Any

from material_agent.research.flatband_contracts import RawExpertAnnotationV1


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
SCHEMA_PATH = (
    REPOSITORY_ROOT
    / "artifacts"
    / "experiment"
    / "flatband-benchmark-20260809"
    / "research_contracts.schema.json"
)
HASH_PATH = SCHEMA_PATH.with_suffix(".sha256")
EXPECTED_SHA256 = "f7f265b7ec130f88494ff70d807395ecd88362ca5a4c7431057241022e9608ae"
SOURCE_CATALOG_PATH = SCHEMA_PATH.with_name("source_catalog.jsonl")
SOURCE_CATALOG_SCHEMA_PATH = SCHEMA_PATH.with_name("source_catalog.schema.json")
SOURCE_CATALOG_SHA256 = (
    "57c24de8f0cf616205b03ef16231def711f2dfa9fcac86d94beede9c01b0bb1f"
)
SOURCE_CATALOG_SCHEMA_SHA256 = (
    "fedca7626203ee337f9d98291dccd0a427d4fd2bd1f42656f9522eb75bd8ff6d"
)
PREREGISTRATION_PATH = SCHEMA_PATH.with_name("PREREGISTRATION.md")
PREREGISTRATION_SHA256 = (
    "91f5e9d9cc20cf6e7344406a3867df0847b6db2b2c24de55d3d73548ac2df899"
)
ANNOTATION_GUIDE_PATH = SCHEMA_PATH.with_name("ANNOTATION_GUIDE.md")
ANNOTATION_GUIDE_SHA256 = (
    "131780990d87754f1e29dc9d2c6f61c13677113fb2c989977a2df78eb26be76f"
)


def _walk(value: Any):
    yield value
    if isinstance(value, dict):
        for child in value.values():
            yield from _walk(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk(child)


def test_flatband_research_contract_draft_is_content_addressed_and_strict() -> None:
    payload = SCHEMA_PATH.read_bytes()
    assert hashlib.sha256(payload).hexdigest() == EXPECTED_SHA256
    assert HASH_PATH.read_text(encoding="utf-8") == (
        f"{EXPECTED_SHA256}  {SCHEMA_PATH.name}\n"
    )
    document = json.loads(payload)
    assert document["schema_version"] == "flatband-research-contract-bundle-v0-draft"
    assert set(document["models"]) == {
        "BenchmarkSplitManifestV1",
        "BlindingManifestV1",
        "ExpertAdjudicationV1",
        "ExpertRegistryV1",
        "FlatBandBenchmarkCaseV1",
        "FlatBandEvidenceV1",
        "HypothesisPacketV1",
        "RawExpertAnnotationV1",
        "ResearchRunLedgerV1",
        "SourceRecordRefV1",
        "SystemRankingV1",
    }
    for node in _walk(document["models"]):
        if isinstance(node, dict) and node.get("type") == "object":
            assert node.get("additionalProperties") is False


def test_blinded_annotation_contract_cannot_encode_system_or_rank() -> None:
    schema_text = json.dumps(
        RawExpertAnnotationV1.model_json_schema(), sort_keys=True
    ).casefold()
    assert "system_id" not in schema_text
    assert "selection_rank" not in schema_text
    assert "scientific_conclusion" in schema_text


def test_source_catalog_identity_and_decisions_are_frozen() -> None:
    catalog_bytes = SOURCE_CATALOG_PATH.read_bytes()
    schema_bytes = SOURCE_CATALOG_SCHEMA_PATH.read_bytes()
    assert hashlib.sha256(catalog_bytes).hexdigest() == SOURCE_CATALOG_SHA256
    assert hashlib.sha256(schema_bytes).hexdigest() == SOURCE_CATALOG_SCHEMA_SHA256
    rows = [
        json.loads(line)
        for line in catalog_bytes.decode("utf-8").splitlines()
        if line
    ]
    assert len(rows) == 20
    assert len({row["source_id"] for row in rows}) == 20
    assert Counter(row["decision"] for row in rows) == {
        "INCLUDE": 12,
        "CONDITIONAL": 5,
        "EXCLUDE": 3,
    }
    assert all(row["licenses"] for row in rows)
    assert all(
        url.startswith("https://")
        for row in rows
        for url in row["official_urls"]
    )


def test_preregistration_and_annotation_guide_drafts_are_content_addressed() -> None:
    preregistration = PREREGISTRATION_PATH.read_bytes()
    annotation_guide = ANNOTATION_GUIDE_PATH.read_bytes()
    assert hashlib.sha256(preregistration).hexdigest() == PREREGISTRATION_SHA256
    assert hashlib.sha256(annotation_guide).hexdigest() == ANNOTATION_GUIDE_SHA256
    assert PREREGISTRATION_PATH.with_suffix(".sha256").read_text(
        encoding="utf-8"
    ) == f"{PREREGISTRATION_SHA256}  {PREREGISTRATION_PATH.name}\n"
    assert ANNOTATION_GUIDE_PATH.with_suffix(".sha256").read_text(
        encoding="utf-8"
    ) == f"{ANNOTATION_GUIDE_SHA256}  {ANNOTATION_GUIDE_PATH.name}\n"
    assert b"DRAFT_PENDING_EXPERT_MODEL_AND_SPLIT_IDENTITIES" in preregistration
    assert (
        b"PILOT_NO_GO_PENDING_REVIEWER_PROJECTION_AND_GOLD_CLOSURE"
        in annotation_guide
    )
