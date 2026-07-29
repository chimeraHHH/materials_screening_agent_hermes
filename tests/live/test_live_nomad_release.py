from __future__ import annotations

import pytest

from material_agent.retrieval.adapters import NomadAdapter
from material_agent.retrieval.models import (
    RetrievalPolicy,
    SourceDatabase,
)
from material_agent.retrieval.query import build_query_plan


@pytest.mark.live_nomad
def test_public_nomad_archive_release_gate(
    requirement, requirement_hash
) -> None:
    adapter = NomadAdapter()
    policy = RetrievalPolicy(
        policy_version="retrieval-policy-nomad-v1",
        source_database=SourceDatabase.NOMAD,
        endpoint="/entries/archive/query",
        chunk_size=1,
        max_records_scanned=1,
    )

    metadata = adapter.metadata()
    plan = build_query_plan(
        requirement,
        requirement_hash,
        metadata,
        policy,
    )
    documents = adapter.search(plan)

    assert metadata.database_version.startswith("api:")
    assert len(documents) == 1
    document = documents[0]
    assert {"O", "Si"}.issubset(document["elements"])
    assert 0.5 <= document["band_gap"] <= 1.0
    assert document["energy_above_hull"] is None
    assert document["structure"] is not None
    assert document["source_provenance"]["entry_id"] == document["material_id"]
