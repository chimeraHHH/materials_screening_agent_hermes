from __future__ import annotations

from pathlib import Path

import pytest

from material_agent.research.flatband_ingress import (
    build_source_catalog_checkpoint_release_v1,
    ingest_raw_expert_annotation_v1,
)


def _raw_annotation():
    from test_flatband_research_contracts import _annotation

    return _annotation()


def test_annotation_json_ingress_rebuilds_canonical_identity() -> None:
    original = _raw_annotation()
    payload = original.model_dump(
        mode="json", exclude={"annotation_id", "annotation_sha256"}
    )
    assert ingest_raw_expert_annotation_v1(payload) == original


def test_annotation_ingress_rejects_caller_identity_and_unknown_fields() -> None:
    original = _raw_annotation()
    payload = original.model_dump(
        mode="json", exclude={"annotation_id", "annotation_sha256"}
    )
    with pytest.raises(ValueError, match="cannot choose content identity"):
        ingest_raw_expert_annotation_v1(
            {**payload, "annotation_id": original.annotation_id}
        )
    with pytest.raises(ValueError, match="unknown fields"):
        ingest_raw_expert_annotation_v1({**payload, "hidden_label": "forbidden"})


def test_source_catalog_checkpoint_replays_exact_public_audit() -> None:
    root = Path(__file__).resolve().parents[2]
    artifact_dir = root / "artifacts/experiment/flatband-benchmark-20260809"
    catalog = (artifact_dir / "source_catalog.jsonl").read_bytes()
    schema = (artifact_dir / "source_catalog.schema.json").read_bytes()
    audit = (artifact_dir / "SOURCE_AUDIT.md").read_bytes()
    release = build_source_catalog_checkpoint_release_v1(
        source_catalog_jsonl=catalog,
        source_catalog_schema_json=schema,
        source_audit_markdown=audit,
        audited_at="2026-08-10T12:00:00+08:00",
    )
    assert len(release.rows) == 20
    assert (release.include_count, release.conditional_count, release.exclude_count) == (
        12,
        5,
        3,
    )
    with pytest.raises(ValueError, match="frozen catalog"):
        build_source_catalog_checkpoint_release_v1(
            source_catalog_jsonl=catalog + b"\n",
            source_catalog_schema_json=schema,
            source_audit_markdown=audit,
            audited_at="2026-08-10T12:00:00+08:00",
        )
    with pytest.raises(ValueError, match="frozen audit"):
        build_source_catalog_checkpoint_release_v1(
            source_catalog_jsonl=catalog,
            source_catalog_schema_json=schema,
            source_audit_markdown=audit + b"\n",
            audited_at="2026-08-10T12:00:00+08:00",
        )
