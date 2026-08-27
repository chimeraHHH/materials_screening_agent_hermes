from __future__ import annotations

import json

import pytest

from material_agent.inspiration.hybrid_similarity import (
    HybridSimilarityError,
    HybridSimilarityPolicyV1,
    RankedNearDuplicateCandidate,
    SimilarityMode,
    decide_ranked_near_duplicates,
)
from material_agent.inspiration.semantic_embedding import (
    LocalBundleVerificationV1,
    LocalSemanticEmbeddingAdapter,
    LocalSemanticModelIdentityV1,
    SemanticPassageInputV1,
)


class SyntheticRouteProvider:
    """Synthetic vectors exercise engineering decisions only."""

    def __init__(self) -> None:
        self._identity = LocalSemanticModelIdentityV1(
            provider_id="synthetic-route-provider",
            model_id="synthetic-route-model",
            model_revision="fixture-v1",
            tokenizer_sha256="1" * 64,
            config_sha256="2" * 64,
            model_bundle_sha256="3" * 64,
            dependency_lock_sha256="4" * 64,
            license_id="test-only-license",
            license_sha256="5" * 64,
            dimension=8,
            synthetic=True,
        )

    @property
    def identity(self) -> LocalSemanticModelIdentityV1:
        return self._identity

    def verify_local_bundle(self) -> LocalBundleVerificationV1:
        return LocalBundleVerificationV1(
            tokenizer_sha256=self.identity.tokenizer_sha256,
            config_sha256=self.identity.config_sha256,
            model_bundle_sha256=self.identity.model_bundle_sha256,
            dependency_lock_sha256=self.identity.dependency_lock_sha256,
            license_sha256=self.identity.license_sha256,
            observed_dimension=8,
            loaded_from_local_path=True,
            dependency_check_passed=True,
            license_check_passed=True,
        )

    def count_tokens(self, canonical_input: bytes) -> int:
        return 4

    def embed(self, canonical_input: bytes) -> tuple[float, ...]:
        passage = json.loads(canonical_input)["selected_passage"]
        if passage == "orthogonal route":
            return (0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0)
        return (1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0)


def _embedding(adapter: LocalSemanticEmbeddingAdapter, text: str):
    return adapter.embed_one(
        SemanticPassageInputV1(
            title="Synthetic title",
            section_heading="Mechanism",
            selected_passage=text,
            normalized_tags=("route",),
        ),
        max_input_tokens=10,
    )


def test_hybrid_ranked_near_dedup_changes_a_real_decision_and_records_ablation() -> None:
    adapter = LocalSemanticEmbeddingAdapter(
        provider=SyntheticRouteProvider(),
        allow_synthetic=True,
    )
    candidates = (
        RankedNearDuplicateCandidate(
            candidate_id="anchor",
            lexical_features=frozenset(("local", "resonance")),
            semantic_embedding=_embedding(adapter, "anchor route"),
        ),
        RankedNearDuplicateCandidate(
            candidate_id="cross-domain-near",
            lexical_features=frozenset(("compact", "mode")),
            semantic_embedding=_embedding(adapter, "cross domain route"),
        ),
        RankedNearDuplicateCandidate(
            candidate_id="independent",
            lexical_features=frozenset(("independent", "route")),
            semantic_embedding=_embedding(adapter, "orthogonal route"),
        ),
    )
    policy = HybridSimilarityPolicyV1(
        lexical_weight=0.20,
        semantic_weight=0.80,
        near_duplicate_threshold=0.75,
    )

    result = decide_ranked_near_duplicates(
        candidates,
        policy=policy,
        requested_mode=SimilarityMode.HYBRID,
        max_selected=3,
    )

    assert tuple(candidate.candidate_id for candidate in result.kept) == (
        "anchor",
        "independent",
    )
    assert result.lexical_only_kept_candidate_ids == (
        "anchor",
        "cross-domain-near",
        "independent",
    )
    assert result.ablation_changed_candidate_ids == ("cross-domain-near",)
    assert result.dropped[0].representative_candidate_id == "anchor"
    assert result.dropped[0].similarity.lexical_similarity == 0.0
    assert result.dropped[0].similarity.semantic_similarity == 1.0
    assert result.dropped[0].similarity.combined_similarity == pytest.approx(0.8)


def test_missing_semantic_data_never_silently_falls_back() -> None:
    candidates = (
        RankedNearDuplicateCandidate(
            candidate_id="a",
            lexical_features=frozenset(("same",)),
        ),
        RankedNearDuplicateCandidate(
            candidate_id="b",
            lexical_features=frozenset(("same",)),
        ),
    )
    policy = HybridSimilarityPolicyV1(
        lexical_weight=0.5,
        semantic_weight=0.5,
        near_duplicate_threshold=0.9,
    )

    with pytest.raises(HybridSimilarityError) as unavailable:
        decide_ranked_near_duplicates(
            candidates,
            policy=policy,
            requested_mode=SimilarityMode.HYBRID,
            max_selected=2,
        )
    assert unavailable.value.code == "SEMANTIC_EMBEDDING_UNAVAILABLE"

    fallback = decide_ranked_near_duplicates(
        candidates,
        policy=policy,
        requested_mode=SimilarityMode.HYBRID,
        max_selected=2,
        allow_explicit_lexical_fallback=True,
    )
    assert fallback.applied_mode is SimilarityMode.LEXICAL_ONLY
    assert fallback.fallback_reason == (
        "SEMANTIC_UNAVAILABLE_EXPLICIT_LEXICAL_FALLBACK"
    )
    assert tuple(candidate.candidate_id for candidate in fallback.kept) == ("a",)
    assert fallback.ablation_changed_candidate_ids == ()
