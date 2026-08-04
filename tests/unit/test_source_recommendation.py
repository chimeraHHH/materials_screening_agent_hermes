from __future__ import annotations

import hashlib
import json

import pytest

from material_agent.retrieval.source_recommendation import (
    RECOMMENDABLE_SOURCES,
    SourceRecommendation,
    recommend_retrieval_source,
)


class FakeProvider:
    def __init__(self, payload):
        self.payload = payload
        self.calls = []

    def structured_generate(self, **kwargs):
        self.calls.append(kwargs)
        return type(
            "Response",
            (),
            {"payload": self.payload, "audit": {"provider": "fake", "request_sha256": "x"}},
        )()


def test_recommendation_is_one_of_the_five_supported_databases(requirement):
    provider = FakeProvider(
        {"source_database": "c2db", "rationale": "2D coverage", "confidence": 0.9}
    )
    result = recommend_retrieval_source(requirement, provider)

    assert result.source_database.value == "c2db"
    assert result.source_database in RECOMMENDABLE_SOURCES
    assert result.requirement_sha256 == hashlib.sha256(
        json.dumps(
            requirement.model_dump(mode="json"),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode()
    ).hexdigest()
    assert provider.calls[0]["user_payload"] == {
        "requirement": requirement.model_dump(mode="json")
    }


@pytest.mark.parametrize("source", ["nims_supercon", "atomly", ["c2db", "nomad"]])
def test_recommendation_rejects_unsupported_or_multiple_sources(requirement, source):
    provider = FakeProvider(
        {"source_database": source, "rationale": "bad", "confidence": 0.5}
    )
    with pytest.raises(ValueError, match="schema validation"):
        recommend_retrieval_source(requirement, provider)


def test_recommendation_requires_confirmed_requirement(requirement):
    draft = requirement.model_copy(update={"confirmed_by_user": False})
    with pytest.raises(ValueError, match="confirmed"):
        recommend_retrieval_source(
            draft,
            FakeProvider(
                {"source_database": "mc3d", "rationale": "structure", "confidence": 0.5}
            ),
        )


def test_source_recommendation_contract_rejects_extra_fields():
    with pytest.raises(ValueError):
        SourceRecommendation(
            source_database="mc3d",
            rationale="structure",
            confidence=0.5,
            requirement_sha256="0" * 64,
            llm_audit={},
            extra="not allowed",
        )
