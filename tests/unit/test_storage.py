from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

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


def test_concurrent_immutable_writers_cannot_overwrite_each_other(tmp_path) -> None:
    barrier = Barrier(2)

    def publish(payload: bytes) -> tuple[str, str]:
        store = LocalArtifactStore(tmp_path)
        barrier.wait()
        try:
            reference = store.write_bytes(
                "runs/shared/result.json",
                payload,
                media_type="application/json",
                immutable=True,
            )
        except ArtifactConflictError:
            return "conflict", ""
        return "published", reference.sha256

    payloads = (b'{"worker":1}', b'{"worker":2}')
    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = tuple(executor.map(publish, payloads))

    assert sorted(item[0] for item in outcomes) == ["conflict", "published"]
    assert (tmp_path / "runs" / "shared" / "result.json").read_bytes() in payloads


def test_concurrent_same_content_immutable_writers_are_idempotent(tmp_path) -> None:
    barrier = Barrier(2)
    payload = b'{"same":true}'

    def publish(_index: int) -> str:
        store = LocalArtifactStore(tmp_path)
        barrier.wait()
        return store.write_bytes(
            "runs/shared/same.json",
            payload,
            media_type="application/json",
            immutable=True,
        ).sha256

    with ThreadPoolExecutor(max_workers=2) as executor:
        digests = tuple(executor.map(publish, range(2)))

    assert len(set(digests)) == 1
    assert (tmp_path / "runs" / "shared" / "same.json").read_bytes() == payload


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
