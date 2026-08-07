from __future__ import annotations

from pathlib import Path

import pytest

from material_agent.inspiration import (
    ArtifactPointerV1,
    SearchQueryKind,
    SearchQueryV1,
)
from material_agent.inspiration.search import (
    CrossrefPublicAdapter,
    parse_crossref_page,
)
from material_agent.retrieval.storage import LocalArtifactStore


@pytest.mark.live_crossref
def test_public_crossref_metadata_release_gate(tmp_path: Path) -> None:
    query = SearchQueryV1(
        query_id="query-live-crossref",
        kind=SearchQueryKind.BRIDGE,
        text=(
            "flat band compact localized states mechanical metamaterial "
            "destructive interference"
        ),
        tag_ids=("compact-localized-state", "destructive-interference"),
        bridge_rule_id="bridge-photonic-interference",
    )
    adapter = CrossrefPublicAdapter(max_results=5, timeout_seconds=30)

    page = adapter.search(query, max_response_bytes=500_000)

    store = LocalArtifactStore(tmp_path)
    raw_ref = store.write_bytes(
        "stages/inspiration/live-crossref/raw_search/query-live-crossref.json",
        page.payload,
        media_type=page.media_type,
        immutable=True,
    )
    raw_artifact = ArtifactPointerV1.model_validate(raw_ref.model_dump(mode="json"))
    parsed = parse_crossref_page(
        query=query,
        payload=page.payload,
        raw_response_artifact=raw_artifact,
        max_hits=5,
    )

    assert page.provider == "crossref"
    assert 0 < len(page.payload) <= 500_000
    assert store.exists_with_hash(raw_ref.uri, raw_ref.sha256)
    assert parsed.hits
    assert any(hit.abstract for hit in parsed.hits)
    assert all(hit.doi for hit in parsed.hits)
    assert all(hit.raw_response_artifact == raw_artifact for hit in parsed.hits)
