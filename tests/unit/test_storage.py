from __future__ import annotations

import pytest

from material_agent.retrieval.storage import (
    ArtifactConflictError,
    ArtifactStoreError,
    LocalArtifactStore,
)


def test_store_is_idempotent_and_hashes_content(tmp_path) -> None:
    store = LocalArtifactStore(tmp_path)
    first = store.write_json("a/value.json", {"answer": 42})
    second = store.write_json("a/value.json", {"answer": 42})
    assert first == second
    assert store.exists_with_hash(first.uri, first.sha256)


def test_store_rejects_path_escape(tmp_path) -> None:
    store = LocalArtifactStore(tmp_path)
    with pytest.raises(ArtifactStoreError):
        store.write_text("../escape.txt", "no")


def test_store_refuses_to_overwrite_immutable_artifact(tmp_path) -> None:
    store = LocalArtifactStore(tmp_path)
    original = store.write_json("immutable/value.json", {"revision": 1}, immutable=True)
    with pytest.raises(ArtifactConflictError):
        store.write_json("immutable/value.json", {"revision": 2}, immutable=True)
    assert store.exists_with_hash(original.uri, original.sha256)
    assert store.read_json(original.uri) == {"revision": 1}


def test_gzip_jsonl_round_trip_and_inspection(tmp_path) -> None:
    store = LocalArtifactStore(tmp_path)
    ref = store.write_gzip_jsonl(
        "raw/batch.jsonl.gz",
        [{"material_id": "mp-1"}, {"material_id": "mp-2"}],
        immutable=True,
    )
    assert store.inspect(ref.uri, media_type="application/gzip") == ref
    assert store.read_gzip_jsonl(ref.uri) == [
        {"material_id": "mp-1"},
        {"material_id": "mp-2"},
    ]
