"""Versioned lexical/semantic similarity and ranked near-duplicate decisions.

The decision engine is deliberately generic: callers provide already-ranked
records and it greedily keeps the highest-ranked representative.  Hybrid mode
requires validated semantic results from one exact model identity.  Missing
semantic data fails closed unless the caller explicitly requests a recorded
lexical-only fallback.  Every hybrid result includes a lexical-only ablation.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import StrEnum

from material_agent.inspiration.models import canonical_sha256
from material_agent.inspiration.semantic_embedding import SemanticEmbeddingResultV1


HYBRID_SIMILARITY_SCHEMA_VERSION = "lexical-semantic-hybrid-similarity-v1"


class HybridSimilarityError(ValueError):
    """Fail-closed hybrid similarity error with a stable code."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(f"{code}: {message}")


class SimilarityMode(StrEnum):
    LEXICAL_ONLY = "LEXICAL_ONLY"
    HYBRID = "HYBRID"


@dataclass(frozen=True, slots=True)
class HybridSimilarityPolicyV1:
    """Frozen weights and threshold for one near-duplicate decision."""

    lexical_weight: float = 0.45
    semantic_weight: float = 0.55
    near_duplicate_threshold: float = 0.85
    schema_version: str = HYBRID_SIMILARITY_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != HYBRID_SIMILARITY_SCHEMA_VERSION:
            raise HybridSimilarityError(
                "UNSUPPORTED_SIMILARITY_POLICY",
                "hybrid similarity schema version is not supported",
            )
        for field_name, value in (
            ("lexical_weight", self.lexical_weight),
            ("semantic_weight", self.semantic_weight),
            ("near_duplicate_threshold", self.near_duplicate_threshold),
        ):
            if (
                not isinstance(value, float)
                or not math.isfinite(value)
                or value < 0.0
                or value > 1.0
            ):
                raise HybridSimilarityError(
                    "INVALID_SIMILARITY_POLICY",
                    f"{field_name} must be a finite float in [0, 1]",
                )
        if abs(self.lexical_weight + self.semantic_weight - 1.0) > 1e-12:
            raise HybridSimilarityError(
                "INVALID_SIMILARITY_POLICY",
                "lexical and semantic weights must sum to one",
            )

    @property
    def policy_sha256(self) -> str:
        return canonical_sha256(
            {
                "lexical_weight": self.lexical_weight,
                "near_duplicate_threshold": self.near_duplicate_threshold,
                "schema_version": self.schema_version,
                "semantic_weight": self.semantic_weight,
            }
        )


@dataclass(frozen=True, slots=True)
class RankedNearDuplicateCandidate:
    """One pre-ranked record entering deterministic near-deduplication."""

    candidate_id: str
    lexical_features: frozenset[str]
    semantic_embedding: SemanticEmbeddingResultV1 | None = None

    def __post_init__(self) -> None:
        if (
            not isinstance(self.candidate_id, str)
            or not self.candidate_id
            or len(self.candidate_id) > 256
        ):
            raise HybridSimilarityError(
                "INVALID_NEAR_DEDUP_INPUT",
                "candidate_id must be non-empty and bounded",
            )
        if not isinstance(self.lexical_features, frozenset) or any(
            not isinstance(value, str) or not value
            for value in self.lexical_features
        ):
            raise HybridSimilarityError(
                "INVALID_NEAR_DEDUP_INPUT",
                "lexical_features must be a frozenset of non-empty strings",
            )
        if self.semantic_embedding is not None and not isinstance(
            self.semantic_embedding,
            SemanticEmbeddingResultV1,
        ):
            raise HybridSimilarityError(
                "INVALID_NEAR_DEDUP_INPUT",
                "semantic_embedding must be a validated semantic result",
            )


@dataclass(frozen=True, slots=True)
class SimilarityBreakdownV1:
    lexical_similarity: float
    semantic_similarity: float | None
    combined_similarity: float
    mode: SimilarityMode
    policy_sha256: str


@dataclass(frozen=True, slots=True)
class NearDuplicateDropV1:
    candidate_id: str
    representative_candidate_id: str
    similarity: SimilarityBreakdownV1


@dataclass(frozen=True, slots=True)
class RankedNearDedupResultV1:
    """Decision output with explicit mode, fallback, and lexical ablation."""

    requested_mode: SimilarityMode
    applied_mode: SimilarityMode
    kept: tuple[RankedNearDuplicateCandidate, ...]
    dropped: tuple[NearDuplicateDropV1, ...]
    lexical_only_kept_candidate_ids: tuple[str, ...]
    ablation_changed_candidate_ids: tuple[str, ...]
    policy_sha256: str
    fallback_reason: str | None = None


def lexical_jaccard_similarity(
    left: frozenset[str],
    right: frozenset[str],
) -> float:
    """Token-set Jaccard with explicit empty-set semantics."""

    if not isinstance(left, frozenset) or not isinstance(right, frozenset):
        raise HybridSimilarityError(
            "INVALID_SIMILARITY_INPUT",
            "lexical features must be frozensets",
        )
    union = left | right
    if not union:
        return 1.0
    return len(left & right) / len(union)


def compare_ranked_candidates(
    left: RankedNearDuplicateCandidate,
    right: RankedNearDuplicateCandidate,
    *,
    policy: HybridSimilarityPolicyV1,
    mode: SimilarityMode,
) -> SimilarityBreakdownV1:
    """Compute one validated lexical-only or hybrid similarity."""

    if not isinstance(left, RankedNearDuplicateCandidate) or not isinstance(
        right,
        RankedNearDuplicateCandidate,
    ):
        raise HybridSimilarityError(
            "INVALID_SIMILARITY_INPUT",
            "similarity inputs must be ranked near-duplicate candidates",
        )
    if not isinstance(policy, HybridSimilarityPolicyV1):
        raise HybridSimilarityError(
            "INVALID_SIMILARITY_POLICY",
            "policy must be HybridSimilarityPolicyV1",
        )
    if not isinstance(mode, SimilarityMode):
        raise HybridSimilarityError(
            "INVALID_SIMILARITY_MODE",
            "mode must be a SimilarityMode",
        )

    lexical = lexical_jaccard_similarity(
        left.lexical_features,
        right.lexical_features,
    )
    if mode is SimilarityMode.LEXICAL_ONLY:
        return SimilarityBreakdownV1(
            lexical_similarity=lexical,
            semantic_similarity=None,
            combined_similarity=lexical,
            mode=mode,
            policy_sha256=policy.policy_sha256,
        )

    left_embedding = left.semantic_embedding
    right_embedding = right.semantic_embedding
    if left_embedding is None or right_embedding is None:
        raise HybridSimilarityError(
            "SEMANTIC_EMBEDDING_UNAVAILABLE",
            "hybrid similarity requires a semantic embedding for every candidate",
        )
    if (
        left_embedding.model_identity.identity_sha256
        != right_embedding.model_identity.identity_sha256
    ):
        raise HybridSimilarityError(
            "SEMANTIC_MODEL_MISMATCH",
            "hybrid similarity cannot combine different model identities",
        )
    if left_embedding.dimension != right_embedding.dimension:
        raise HybridSimilarityError(
            "SEMANTIC_DIMENSION_MISMATCH",
            "hybrid similarity cannot combine different dimensions",
        )
    semantic = math.fsum(
        left_value * right_value
        for left_value, right_value in zip(
            left_embedding.vector,
            right_embedding.vector,
            strict=True,
        )
    )
    if not math.isfinite(semantic):
        raise HybridSimilarityError(
            "NON_FINITE_SEMANTIC_SIMILARITY",
            "semantic cosine similarity is not finite",
        )
    # L2 vectors can have cosine in [-1, 1].  Near-duplicate similarity uses a
    # bounded [0, 1] signal; negative alignment contributes no similarity.
    bounded_semantic = min(1.0, max(0.0, semantic))
    combined = (
        lexical * policy.lexical_weight
        + bounded_semantic * policy.semantic_weight
    )
    return SimilarityBreakdownV1(
        lexical_similarity=lexical,
        semantic_similarity=bounded_semantic,
        combined_similarity=min(1.0, max(0.0, combined)),
        mode=mode,
        policy_sha256=policy.policy_sha256,
    )


def decide_ranked_near_duplicates(
    candidates: tuple[RankedNearDuplicateCandidate, ...],
    *,
    policy: HybridSimilarityPolicyV1,
    requested_mode: SimilarityMode,
    max_selected: int,
    allow_explicit_lexical_fallback: bool = False,
) -> RankedNearDedupResultV1:
    """Keep ranked representatives and record a lexical-only ablation.

    Candidate order is authoritative.  A candidate is dropped when its maximum
    similarity to an already-kept representative reaches the frozen threshold.
    Ties select the earliest kept representative, then its ID.  If hybrid data
    are missing, fallback happens only with the explicit flag and is recorded.
    """

    if not isinstance(candidates, tuple) or any(
        not isinstance(candidate, RankedNearDuplicateCandidate)
        for candidate in candidates
    ):
        raise HybridSimilarityError(
            "INVALID_NEAR_DEDUP_INPUT",
            "candidates must be a tuple of ranked near-duplicate records",
        )
    candidate_ids = tuple(candidate.candidate_id for candidate in candidates)
    if len(candidate_ids) != len(set(candidate_ids)):
        raise HybridSimilarityError(
            "DUPLICATE_CANDIDATE_ID",
            "ranked near-dedup candidate IDs must be unique",
        )
    if (
        not isinstance(max_selected, int)
        or isinstance(max_selected, bool)
        or max_selected < 1
    ):
        raise HybridSimilarityError(
            "INVALID_NEAR_DEDUP_LIMIT",
            "max_selected must be a positive integer",
        )
    if not isinstance(requested_mode, SimilarityMode):
        raise HybridSimilarityError(
            "INVALID_SIMILARITY_MODE",
            "requested_mode must be a SimilarityMode",
        )

    fallback_reason: str | None = None
    applied_mode = requested_mode
    if requested_mode is SimilarityMode.HYBRID and any(
        candidate.semantic_embedding is None for candidate in candidates
    ):
        if not allow_explicit_lexical_fallback:
            raise HybridSimilarityError(
                "SEMANTIC_EMBEDDING_UNAVAILABLE",
                "hybrid near-dedup requires semantic embeddings for every candidate",
            )
        applied_mode = SimilarityMode.LEXICAL_ONLY
        fallback_reason = "SEMANTIC_UNAVAILABLE_EXPLICIT_LEXICAL_FALLBACK"
    if applied_mode is SimilarityMode.HYBRID:
        _validate_semantic_pool(candidates)

    kept, dropped = _decide(
        candidates,
        policy=policy,
        mode=applied_mode,
        max_selected=max_selected,
    )
    lexical_kept, _ = _decide(
        candidates,
        policy=policy,
        mode=SimilarityMode.LEXICAL_ONLY,
        max_selected=max_selected,
    )
    kept_ids = tuple(candidate.candidate_id for candidate in kept)
    lexical_ids = tuple(candidate.candidate_id for candidate in lexical_kept)
    changed = tuple(
        candidate_id
        for candidate_id in candidate_ids
        if (candidate_id in kept_ids) != (candidate_id in lexical_ids)
    )
    return RankedNearDedupResultV1(
        requested_mode=requested_mode,
        applied_mode=applied_mode,
        kept=kept,
        dropped=dropped,
        lexical_only_kept_candidate_ids=lexical_ids,
        ablation_changed_candidate_ids=changed,
        policy_sha256=policy.policy_sha256,
        fallback_reason=fallback_reason,
    )


def _validate_semantic_pool(
    candidates: tuple[RankedNearDuplicateCandidate, ...],
) -> None:
    embeddings = tuple(
        candidate.semantic_embedding
        for candidate in candidates
        if candidate.semantic_embedding is not None
    )
    if not embeddings:
        return
    identity_hashes = {
        embedding.model_identity.identity_sha256 for embedding in embeddings
    }
    if len(identity_hashes) != 1:
        raise HybridSimilarityError(
            "SEMANTIC_MODEL_MISMATCH",
            "hybrid near-dedup requires one exact model identity",
        )
    dimensions = {embedding.dimension for embedding in embeddings}
    if len(dimensions) != 1:
        raise HybridSimilarityError(
            "SEMANTIC_DIMENSION_MISMATCH",
            "hybrid near-dedup requires one semantic dimension",
        )


def _decide(
    candidates: tuple[RankedNearDuplicateCandidate, ...],
    *,
    policy: HybridSimilarityPolicyV1,
    mode: SimilarityMode,
    max_selected: int,
) -> tuple[
    tuple[RankedNearDuplicateCandidate, ...],
    tuple[NearDuplicateDropV1, ...],
]:
    kept: list[RankedNearDuplicateCandidate] = []
    dropped: list[NearDuplicateDropV1] = []
    for candidate in candidates:
        if len(kept) >= max_selected:
            break
        comparisons = tuple(
            (
                representative,
                compare_ranked_candidates(
                    candidate,
                    representative,
                    policy=policy,
                    mode=mode,
                ),
                index,
            )
            for index, representative in enumerate(kept)
        )
        nearest = min(
            comparisons,
            key=lambda item: (
                -item[1].combined_similarity,
                item[2],
                item[0].candidate_id,
            ),
            default=None,
        )
        if (
            nearest is not None
            and nearest[1].combined_similarity
            >= policy.near_duplicate_threshold
        ):
            dropped.append(
                NearDuplicateDropV1(
                    candidate_id=candidate.candidate_id,
                    representative_candidate_id=nearest[0].candidate_id,
                    similarity=nearest[1],
                )
            )
            continue
        kept.append(candidate)
    return tuple(kept), tuple(dropped)
