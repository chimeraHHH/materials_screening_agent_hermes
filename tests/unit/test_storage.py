from __future__ import annotations

import pytest

from material_agent.retrieval.storage import ArtifactStoreError, LocalArtifactStore


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

