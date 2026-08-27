"""Auditable query allocation and metadata-quality engineering signals.

This module deliberately stays below the scientific-claim boundary.  Query
candidates must already come from a curated :class:`TagGraphV1`; this module
only selects and allocates them deterministically.  Metadata quality measures
identity, completeness, and retrievability.  It does not estimate venue
prestige, scientific validity, or cross-domain usefulness.

The records are internal versioned contracts so callers can persist their
canonical JSON as an Artifact.  Every parsed hit remains represented in the
quality audit, including hits excluded from evidence ranking and every reason
for that exclusion.
"""

from __future__ import annotations

import math
from enum import StrEnum
from typing import Annotated, Literal

from pydantic import Field, model_validator

from material_agent.inspiration.models import (
    Identifier,
    Score,
    SearchHitV1,
    SearchQueryKind,
    SearchQueryV1,
    Sha256,
    ShortText,
    StrictModel,
    canonical_sha256,
    deterministic_id,
)

QUERY_CANDIDATE_POOL_SCHEMA_VERSION = "inspiration-query-candidate-pool-v1"
QUERY_ALLOCATION_SCHEMA_VERSION = "inspiration-query-allocation-v1"
METADATA_QUALITY_SCHEMA_VERSION = "inspiration-metadata-quality-v1"
METADATA_QUALITY_POLICY_VERSION = "metadata-completeness-policy-2026-08-09.1"
METADATA_QUALITY_EVALUATION_VERSION = "metadata-quality-engineering-eval-v1"


class RetrievalQualityError(ValueError):
    """A curated retrieval or quality-audit invariant was violated."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(f"{code}: {message}")


class QueryCandidateOrigin(StrEnum):
    """Reviewable origin of one candidate; no runtime-generated class exists."""

    CURATED_TAG_TERMS = "CURATED_TAG_TERMS"
    FROZEN_CONTEXT_COMPILER = "FROZEN_CONTEXT_COMPILER"
    REVIEWED_BRIDGE_TEMPLATE = "REVIEWED_BRIDGE_TEMPLATE"
    REVIEWED_BREAKING_CONDITION = "REVIEWED_BREAKING_CONDITION"


class QueryCandidateV1(StrictModel):
    """One version-bound candidate derived only from reviewed graph fields."""

    candidate_id: Identifier
    pool_version: ShortText
    kind: SearchQueryKind
    text: Annotated[str, Field(min_length=3, max_length=512)]
    tag_ids: Annotated[tuple[Identifier, ...], Field(min_length=1, max_length=16)]
    bridge_rule_id: Identifier | None = None
    family_id: Identifier
    origin: QueryCandidateOrigin
    review_basis: ShortText

    @model_validator(mode="after")
    def validate_origin(self) -> QueryCandidateV1:
        if len(set(self.tag_ids)) != len(self.tag_ids):
            raise ValueError("query candidate tag IDs must be unique")
        if self.kind is SearchQueryKind.DIRECT:
            if self.bridge_rule_id is not None:
                raise ValueError("direct candidates cannot reference a bridge rule")
            if self.origin not in {
                QueryCandidateOrigin.CURATED_TAG_TERMS,
                QueryCandidateOrigin.FROZEN_CONTEXT_COMPILER,
            }:
                raise ValueError(
                    "direct candidates must use curated terms or a frozen context compiler"
                )
        else:
            if self.bridge_rule_id is None:
                raise ValueError("bridge and counter candidates require a bridge rule")
            expected_origin = (
                QueryCandidateOrigin.REVIEWED_BRIDGE_TEMPLATE
                if self.kind is SearchQueryKind.BRIDGE
                else QueryCandidateOrigin.REVIEWED_BREAKING_CONDITION
            )
            if self.origin is not expected_origin:
                raise ValueError("candidate origin does not match its query class")
        expected_id = deterministic_id(
            "qcandidate",
            {
                "pool_version": self.pool_version,
                "kind": self.kind.value,
                "text": self.text,
                "tag_ids": self.tag_ids,
                "bridge_rule_id": self.bridge_rule_id,
                "family_id": self.family_id,
                "origin": self.origin.value,
                "review_basis": self.review_basis,
            },
        )
        if self.candidate_id != expected_id:
            raise ValueError("query candidate ID does not match canonical content")
        return self

    def to_search_query(self) -> SearchQueryV1:
        """Project to the frozen public query contract.

        The query identity intentionally follows the existing public contract;
        the richer candidate identity remains available in the allocation audit.
        """

        payload = {
            "kind": self.kind.value,
            "text": self.text,
            "tag_ids": self.tag_ids,
            "bridge_rule_id": self.bridge_rule_id,
        }
        return SearchQueryV1(
            query_id=deterministic_id("query", payload),
            kind=self.kind,
            text=self.text,
            tag_ids=self.tag_ids,
            bridge_rule_id=self.bridge_rule_id,
        )


class CuratedQueryCandidatePoolV1(StrictModel):
    """A complete, versioned pool before any bounded selection is applied."""

    schema_version: Literal["inspiration-query-candidate-pool-v1"] = (
        QUERY_CANDIDATE_POOL_SCHEMA_VERSION
    )
    pool_version: ShortText
    graph_id: Identifier
    graph_version: ShortText
    target_tag_ids: Annotated[
        tuple[Identifier, ...], Field(min_length=1, max_length=16)
    ]
    candidates: Annotated[
        tuple[QueryCandidateV1, ...], Field(min_length=1, max_length=256)
    ]

    @model_validator(mode="after")
    def validate_pool(self) -> CuratedQueryCandidatePoolV1:
        if len(set(self.target_tag_ids)) != len(self.target_tag_ids):
            raise ValueError("pool target tag IDs must be unique")
        candidate_ids = tuple(item.candidate_id for item in self.candidates)
        if len(set(candidate_ids)) != len(candidate_ids):
            raise ValueError("query candidate IDs must be unique")
        if any(item.pool_version != self.pool_version for item in self.candidates):
            raise ValueError("candidate pool versions must match their container")
        return self


class PhysicalQueryAllowanceV1(StrictModel):
    """Reserved physical HTTP slots for one planned query."""

    query_id: Identifier
    candidate_id: Identifier
    kind: SearchQueryKind
    execution_ordinal: Annotated[int, Field(ge=1, le=64)]
    max_physical_requests: Annotated[int, Field(ge=1, le=64)]


class QueryAllocationAuditV1(StrictModel):
    """Replayable selection and physical-request allocation decision."""

    schema_version: Literal["inspiration-query-allocation-v1"] = (
        QUERY_ALLOCATION_SCHEMA_VERSION
    )
    pool_version: ShortText
    pool_sha256: Sha256
    max_queries: Annotated[int, Field(ge=1, le=64)]
    max_physical_requests: Annotated[int, Field(ge=1, le=64)]
    selected_candidate_ids: Annotated[
        tuple[Identifier, ...], Field(min_length=1, max_length=64)
    ]
    unselected_candidate_ids: Annotated[
        tuple[Identifier, ...], Field(max_length=256)
    ] = ()
    allowances: Annotated[
        tuple[PhysicalQueryAllowanceV1, ...], Field(min_length=1, max_length=64)
    ]

    @model_validator(mode="after")
    def validate_allocation(self) -> QueryAllocationAuditV1:
        if len(set(self.selected_candidate_ids)) != len(self.selected_candidate_ids):
            raise ValueError("selected candidate IDs must be unique")
        if len(set(self.unselected_candidate_ids)) != len(
            self.unselected_candidate_ids
        ):
            raise ValueError("unselected candidate IDs must be unique")
        if set(self.selected_candidate_ids) & set(self.unselected_candidate_ids):
            raise ValueError("selected and unselected candidate IDs must be disjoint")
        if len(self.allowances) != len(self.selected_candidate_ids):
            raise ValueError("every selected candidate requires one allowance")
        if len(self.allowances) > self.max_queries:
            raise ValueError("query allocation exceeds max_queries")
        if tuple(item.candidate_id for item in self.allowances) != (
            self.selected_candidate_ids
        ):
            raise ValueError("allowances must follow selected-candidate order")
        if tuple(item.execution_ordinal for item in self.allowances) != tuple(
            range(1, len(self.allowances) + 1)
        ):
            raise ValueError("allowance ordinals must be contiguous")
        if len({item.query_id for item in self.allowances}) != len(self.allowances):
            raise ValueError("allocated query IDs must be unique")
        if (
            sum(item.max_physical_requests for item in self.allowances)
            != self.max_physical_requests
        ):
            raise ValueError("allowances must reserve the full physical request budget")
        return self

    @property
    def audit_sha256(self) -> str:
        return canonical_sha256(self.model_dump(mode="json"))


class MetadataIdentityKind(StrEnum):
    DOI = "DOI"
    ARXIV = "ARXIV"
    CANONICAL_URL = "CANONICAL_URL"
    PROVIDER_RECORD_ONLY = "PROVIDER_RECORD_ONLY"


class MetadataQualityFeaturesV1(StrictModel):
    """Observable metadata-only features; none is a scientific quality claim."""

    identity_kind: MetadataIdentityKind
    has_abstract: bool
    abstract_char_count: Annotated[int, Field(ge=0, le=20_000)]
    author_count: Annotated[int, Field(ge=0, le=256)]
    has_published_year: bool
    keyword_count: Annotated[int, Field(ge=0, le=128)]
    has_https_canonical_url: bool
    provider_rank: Annotated[int, Field(ge=1, le=10_000)]
    identity_score: Score
    abstract_score: Score
    bibliographic_completeness_score: Score
    retrievability_score: Score
    source_quality_score: Score


class MetadataHitAuditV1(StrictModel):
    """One retained parsed hit and its evidence-ranking decision."""

    hit_id: Identifier
    document_id: Identifier
    provider: Identifier
    raw_response_uri: Annotated[str, Field(min_length=12, max_length=512)]
    raw_response_sha256: Sha256
    retained: Literal[True] = True
    eligible_for_evidence_ranking: bool
    rejection_reasons: Annotated[tuple[ShortText, ...], Field(max_length=8)] = ()
    quality_rank: Annotated[int, Field(ge=1, le=10_000)]
    features: MetadataQualityFeaturesV1

    @model_validator(mode="after")
    def validate_decision(self) -> MetadataHitAuditV1:
        if len(set(self.rejection_reasons)) != len(self.rejection_reasons):
            raise ValueError("metadata rejection reasons must be unique")
        if self.eligible_for_evidence_ranking == bool(self.rejection_reasons):
            raise ValueError("metadata eligibility must exactly match rejection reasons")
        return self


class MetadataQualityAuditV1(StrictModel):
    """Run-local audit retaining every input hit and exclusion reason."""

    schema_version: Literal["inspiration-metadata-quality-v1"] = (
        METADATA_QUALITY_SCHEMA_VERSION
    )
    policy_version: Literal["metadata-completeness-policy-2026-08-09.1"] = (
        METADATA_QUALITY_POLICY_VERSION
    )
    require_abstract: bool
    min_source_quality_score: Score
    raw_hit_count: Annotated[int, Field(ge=0, le=10_000)]
    eligible_hit_count: Annotated[int, Field(ge=0, le=10_000)]
    rejected_hit_count: Annotated[int, Field(ge=0, le=10_000)]
    ranked_hit_ids: Annotated[tuple[Identifier, ...], Field(max_length=10_000)]
    entries: Annotated[tuple[MetadataHitAuditV1, ...], Field(max_length=10_000)]
    scientific_conclusion: Literal[False] = False

    @model_validator(mode="after")
    def validate_closure(self) -> MetadataQualityAuditV1:
        if self.raw_hit_count != len(self.entries):
            raise ValueError("metadata audit raw-hit count does not close")
        if self.ranked_hit_ids != tuple(item.hit_id for item in self.entries):
            raise ValueError("metadata entries must follow ranked-hit order")
        if len(set(self.ranked_hit_ids)) != len(self.ranked_hit_ids):
            raise ValueError("metadata audit hit IDs must be unique")
        eligible = sum(item.eligible_for_evidence_ranking for item in self.entries)
        if self.eligible_hit_count != eligible:
            raise ValueError("metadata audit eligible-hit count does not close")
        if self.rejected_hit_count != self.raw_hit_count - eligible:
            raise ValueError("metadata audit rejected-hit count does not close")
        if not all(item.retained for item in self.entries):
            raise ValueError("metadata audit cannot drop a parsed hit")
        return self

    @property
    def audit_sha256(self) -> str:
        return canonical_sha256(self.model_dump(mode="json"))


class MetadataQualityEngineeringEvaluationV1(StrictModel):
    """Fixture-only engineering check, never an expert recall result."""

    schema_version: Literal["metadata-quality-engineering-eval-v1"] = (
        METADATA_QUALITY_EVALUATION_VERSION
    )
    fixture_id: Identifier
    expected_eligible_hit_ids: Annotated[
        tuple[Identifier, ...], Field(max_length=10_000)
    ]
    predicted_eligible_hit_ids: Annotated[
        tuple[Identifier, ...], Field(max_length=10_000)
    ]
    true_positive_count: Annotated[int, Field(ge=0, le=10_000)]
    false_positive_count: Annotated[int, Field(ge=0, le=10_000)]
    false_negative_count: Annotated[int, Field(ge=0, le=10_000)]
    fixture_recall: Score
    fixture_precision: Score
    synthetic_fixture_only: Literal[True] = True
    scientific_conclusion: Literal[False] = False
    external_gates: tuple[Literal[
        "EXPERT_ADJUDICATED_GOLD_REQUIRED",
        "MULTI_SOURCE_RECALL_REQUIRED",
    ], Literal[
        "EXPERT_ADJUDICATED_GOLD_REQUIRED",
        "MULTI_SOURCE_RECALL_REQUIRED",
    ]] = (
        "EXPERT_ADJUDICATED_GOLD_REQUIRED",
        "MULTI_SOURCE_RECALL_REQUIRED",
    )


def make_query_candidate(
    *,
    pool_version: str,
    kind: SearchQueryKind,
    text: str,
    tag_ids: tuple[str, ...],
    bridge_rule_id: str | None,
    family_id: str,
    origin: QueryCandidateOrigin,
    review_basis: str,
) -> QueryCandidateV1:
    """Build one candidate with a content-derived identity."""

    normalized_text = " ".join(text.split())
    normalized_tag_ids = tuple(sorted(tag_ids))
    payload = {
        "pool_version": pool_version,
        "kind": kind.value,
        "text": normalized_text,
        "tag_ids": normalized_tag_ids,
        "bridge_rule_id": bridge_rule_id,
        "family_id": family_id,
        "origin": origin.value,
        "review_basis": review_basis,
    }
    return QueryCandidateV1(
        candidate_id=deterministic_id("qcandidate", payload),
        pool_version=pool_version,
        kind=kind,
        text=normalized_text,
        tag_ids=normalized_tag_ids,
        bridge_rule_id=bridge_rule_id,
        family_id=family_id,
        origin=origin,
        review_basis=review_basis,
    )


def select_and_allocate_query_candidates(
    pool: CuratedQueryCandidatePoolV1,
    *,
    max_queries: int,
    max_physical_requests: int,
    class_limits: dict[SearchQueryKind, int],
) -> tuple[tuple[SearchQueryV1, ...], QueryAllocationAuditV1]:
    """Select breadth-first by family and reserve physical slots round-robin.

    Query execution stays ordered DIRECT, BRIDGE, COUNTER so reviewed bridge
    breadth is established before counter queries.  Extra physical request
    slots (redirects/retries) are nevertheless distributed round-robin across
    the three classes and then across families inside each class.  A query can
    therefore never consume a slot reserved for another query.
    """

    if type(max_queries) is not int or not 1 <= max_queries <= 64:
        raise RetrievalQualityError("INVALID_QUERY_BUDGET", "max_queries is invalid")
    if type(max_physical_requests) is not int or not 1 <= max_physical_requests <= 64:
        raise RetrievalQualityError(
            "INVALID_PHYSICAL_BUDGET", "max_physical_requests is invalid"
        )
    expected_kinds = set(SearchQueryKind)
    if set(class_limits) != expected_kinds or any(
        type(value) is not int or not 0 <= value <= 64
        for value in class_limits.values()
    ):
        raise RetrievalQualityError(
            "INVALID_CLASS_BUDGET", "class limits must cover every query class"
        )
    if sum(class_limits.values()) > max_queries:
        raise RetrievalQualityError(
            "INVALID_CLASS_BUDGET", "class limits exceed max_queries"
        )

    selected: list[QueryCandidateV1] = []
    for kind in SearchQueryKind:
        kind_candidates = tuple(item for item in pool.candidates if item.kind is kind)
        if kind is SearchQueryKind.COUNTER:
            selected_bridge_rule_ids = {
                item.bridge_rule_id
                for item in selected
                if item.kind is SearchQueryKind.BRIDGE
            }
            kind_candidates = tuple(
                item
                for item in kind_candidates
                if item.bridge_rule_id in selected_bridge_rule_ids
            )
        selected.extend(
            _breadth_first_by_family(kind_candidates)[: class_limits[kind]]
        )
    selected = selected[:max_queries]
    if not selected:
        raise RetrievalQualityError(
            "EMPTY_QUERY_ALLOCATION", "no curated candidate fits the class budget"
        )
    if max_physical_requests < len(selected):
        raise RetrievalQualityError(
            "INSUFFICIENT_PHYSICAL_BUDGET",
            "every selected query requires at least one physical request slot",
        )

    queries = tuple(item.to_search_query() for item in selected)
    allowances = [1] * len(selected)
    by_kind: dict[SearchQueryKind, tuple[int, ...]] = {
        kind: tuple(index for index, item in enumerate(selected) if item.kind is kind)
        for kind in SearchQueryKind
    }
    kind_offsets = {kind: 0 for kind in SearchQueryKind}
    remaining = max_physical_requests - len(selected)
    active_kinds = tuple(kind for kind in SearchQueryKind if by_kind[kind])
    while remaining:
        for kind in active_kinds:
            if remaining == 0:
                break
            members = by_kind[kind]
            member_index = members[kind_offsets[kind] % len(members)]
            allowances[member_index] += 1
            kind_offsets[kind] += 1
            remaining -= 1

    selected_ids = tuple(item.candidate_id for item in selected)
    selected_id_set = set(selected_ids)
    audit = QueryAllocationAuditV1(
        pool_version=pool.pool_version,
        pool_sha256=canonical_sha256(pool.model_dump(mode="json")),
        max_queries=max_queries,
        max_physical_requests=max_physical_requests,
        selected_candidate_ids=selected_ids,
        unselected_candidate_ids=tuple(
            item.candidate_id
            for item in pool.candidates
            if item.candidate_id not in selected_id_set
        ),
        allowances=tuple(
            PhysicalQueryAllowanceV1(
                query_id=query.query_id,
                candidate_id=candidate.candidate_id,
                kind=query.kind,
                execution_ordinal=index + 1,
                max_physical_requests=allowance,
            )
            for index, (candidate, query, allowance) in enumerate(
                zip(selected, queries, allowances, strict=True)
            )
        ),
    )
    return queries, audit


def audit_metadata_hits(
    hits: tuple[SearchHitV1, ...],
    *,
    require_abstract: bool,
    min_source_quality_score: float = 0.0,
) -> MetadataQualityAuditV1:
    """Rank parsed metadata and retain an explicit decision for every hit."""

    if not isinstance(require_abstract, bool):
        raise RetrievalQualityError(
            "INVALID_QUALITY_POLICY", "require_abstract must be a boolean"
        )
    if (
        not isinstance(min_source_quality_score, (int, float))
        or isinstance(min_source_quality_score, bool)
        or not math.isfinite(min_source_quality_score)
        or not 0.0 <= min_source_quality_score <= 1.0
    ):
        raise RetrievalQualityError(
            "INVALID_QUALITY_POLICY", "quality threshold must be finite in [0, 1]"
        )
    if any(not isinstance(hit, SearchHitV1) for hit in hits):
        raise RetrievalQualityError(
            "INVALID_METADATA_HIT", "all metadata inputs must be SearchHitV1"
        )
    hit_ids = tuple(hit.hit_id for hit in hits)
    if len(set(hit_ids)) != len(hit_ids):
        raise RetrievalQualityError(
            "DUPLICATE_METADATA_HIT", "metadata hit IDs must be unique"
        )

    provisional: list[
        tuple[SearchHitV1, MetadataQualityFeaturesV1, tuple[str, ...]]
    ] = []
    threshold = float(min_source_quality_score)
    for hit in hits:
        features = _metadata_features(hit)
        reasons: list[str] = []
        if features.identity_kind is MetadataIdentityKind.PROVIDER_RECORD_ONLY:
            reasons.append("PERSISTENT_IDENTITY_MISSING")
        if require_abstract and not features.has_abstract:
            reasons.append("ABSTRACT_REQUIRED_IN_METADATA_ONLY_MODE")
        if features.source_quality_score < threshold:
            reasons.append("SOURCE_QUALITY_BELOW_FROZEN_THRESHOLD")
        provisional.append((hit, features, tuple(reasons)))

    ordered = sorted(
        provisional,
        key=lambda item: (
            bool(item[2]),
            -item[1].source_quality_score,
            item[1].provider_rank,
            item[0].provider,
            item[0].document_id,
            item[0].hit_id,
        ),
    )
    entries = tuple(
        MetadataHitAuditV1(
            hit_id=hit.hit_id,
            document_id=hit.document_id,
            provider=hit.provider,
            raw_response_uri=hit.raw_response_artifact.uri,
            raw_response_sha256=hit.raw_response_artifact.sha256,
            eligible_for_evidence_ranking=not reasons,
            rejection_reasons=reasons,
            quality_rank=rank,
            features=features,
        )
        for rank, (hit, features, reasons) in enumerate(ordered, start=1)
    )
    eligible_count = sum(item.eligible_for_evidence_ranking for item in entries)
    return MetadataQualityAuditV1(
        require_abstract=require_abstract,
        min_source_quality_score=threshold,
        raw_hit_count=len(entries),
        eligible_hit_count=eligible_count,
        rejected_hit_count=len(entries) - eligible_count,
        ranked_hit_ids=tuple(item.hit_id for item in entries),
        entries=entries,
    )


def evaluate_metadata_quality_fixture(
    audit: MetadataQualityAuditV1,
    *,
    fixture_id: str,
    expected_eligible_hit_ids: tuple[str, ...],
) -> MetadataQualityEngineeringEvaluationV1:
    """Score a synthetic fixture while preserving the external-evidence gate."""

    known_ids = set(audit.ranked_hit_ids)
    expected = set(expected_eligible_hit_ids)
    if len(expected) != len(expected_eligible_hit_ids) or not expected <= known_ids:
        raise RetrievalQualityError(
            "INVALID_FIXTURE_LABELS",
            "fixture labels must be unique IDs present in the metadata audit",
        )
    predicted = {
        item.hit_id for item in audit.entries if item.eligible_for_evidence_ranking
    }
    true_positives = len(expected & predicted)
    false_positives = len(predicted - expected)
    false_negatives = len(expected - predicted)
    recall = true_positives / len(expected) if expected else 1.0
    precision = true_positives / len(predicted) if predicted else float(not expected)
    return MetadataQualityEngineeringEvaluationV1(
        fixture_id=fixture_id,
        expected_eligible_hit_ids=tuple(sorted(expected)),
        predicted_eligible_hit_ids=tuple(sorted(predicted)),
        true_positive_count=true_positives,
        false_positive_count=false_positives,
        false_negative_count=false_negatives,
        fixture_recall=recall,
        fixture_precision=precision,
    )


def _breadth_first_by_family(
    candidates: tuple[QueryCandidateV1, ...],
) -> tuple[QueryCandidateV1, ...]:
    """Round-robin candidates across reviewed families without randomness."""

    family_order = tuple(dict.fromkeys(item.family_id for item in candidates))
    by_family = {
        family_id: tuple(item for item in candidates if item.family_id == family_id)
        for family_id in family_order
    }
    offsets = {family_id: 0 for family_id in family_order}
    output: list[QueryCandidateV1] = []
    while len(output) < len(candidates):
        for family_id in family_order:
            offset = offsets[family_id]
            family = by_family[family_id]
            if offset < len(family):
                output.append(family[offset])
                offsets[family_id] += 1
    return tuple(output)


def _metadata_features(hit: SearchHitV1) -> MetadataQualityFeaturesV1:
    if hit.doi is not None:
        identity_kind = MetadataIdentityKind.DOI
        identity_score = 1.0
    elif hit.arxiv_id is not None:
        identity_kind = MetadataIdentityKind.ARXIV
        identity_score = 0.95
    elif hit.canonical_url is not None:
        identity_kind = MetadataIdentityKind.CANONICAL_URL
        identity_score = 0.70
    else:
        identity_kind = MetadataIdentityKind.PROVIDER_RECORD_ONLY
        identity_score = 0.35

    abstract_chars = len(hit.abstract) if hit.abstract is not None else 0
    # Presence and bounded length are useful for metadata-only extraction; this
    # is not a statement about the correctness of the abstract.
    abstract_score = 0.0 if not abstract_chars else min(1.0, 0.5 + abstract_chars / 2_000)
    bibliographic_score = (
        float(bool(hit.authors))
        + float(hit.published_year is not None)
        + float(bool(hit.keywords))
    ) / 3.0
    https_url = bool(
        hit.canonical_url is not None
        and hit.canonical_url.casefold().startswith("https://")
    )
    retrievability_score = float(https_url)
    quality = (
        0.35 * identity_score
        + 0.35 * abstract_score
        + 0.20 * bibliographic_score
        + 0.10 * retrievability_score
    )
    return MetadataQualityFeaturesV1(
        identity_kind=identity_kind,
        has_abstract=hit.abstract is not None,
        abstract_char_count=abstract_chars,
        author_count=len(hit.authors),
        has_published_year=hit.published_year is not None,
        keyword_count=len(hit.keywords),
        has_https_canonical_url=https_url,
        provider_rank=hit.provider_rank,
        identity_score=identity_score,
        abstract_score=round(abstract_score, 9),
        bibliographic_completeness_score=round(bibliographic_score, 9),
        retrievability_score=retrievability_score,
        source_quality_score=round(quality, 9),
    )
