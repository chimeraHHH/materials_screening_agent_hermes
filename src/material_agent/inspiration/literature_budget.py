"""Family- and era-reserved budgets for the V2 literature workflow.

The existing runner owns one global raw-hit ceiling.  This module defines the
next contract: every selected logical query receives an explicit hit and
physical-request allowance before execution.  Required material routes and
historical eras therefore cannot be starved by an earlier high-volume query.
"""

from __future__ import annotations

from collections import defaultdict
from enum import StrEnum
from typing import Annotated, Literal

from pydantic import Field, model_validator

from material_agent.inspiration.models import (
    Identifier,
    Sha256,
    StrictModel,
    canonical_sha256,
    deterministic_id,
)

LITERATURE_BUDGET_POLICY_VERSION = "literature-budget-policy-v2"
LITERATURE_BUDGET_PLAN_VERSION = "literature-budget-plan-v2"
LITERATURE_QUERY_CANDIDATE_VERSION = "literature-query-candidate-v2"


class LiteratureBudgetError(ValueError):
    """Fail-closed allocation error with a stable code."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(f"{code}: {message}")


class LiteratureQueryFamily(StrEnum):
    MATERIAL = "MATERIAL"
    MECHANISM = "MECHANISM"
    SOFT_CHEMISTRY = "SOFT_CHEMISTRY"
    HISTORICAL = "HISTORICAL"
    CITATION = "CITATION"
    BRIDGE = "BRIDGE"
    COUNTER = "COUNTER"


class PublicationYearBucketV2(StrictModel):
    bucket_id: Identifier
    year_from: Annotated[int, Field(ge=1600, le=2200)]
    year_to: Annotated[int, Field(ge=1600, le=2200)]

    @model_validator(mode="after")
    def validate_range(self) -> PublicationYearBucketV2:
        if self.year_from > self.year_to:
            raise ValueError("publication year bucket is reversed")
        return self


class FamilyReservationV2(StrictModel):
    family: LiteratureQueryFamily
    min_queries: Annotated[int, Field(ge=0, le=64)]
    hit_weight: Annotated[int, Field(ge=1, le=16)]
    physical_request_weight: Annotated[int, Field(ge=1, le=16)] = 1


class LiteratureBudgetPolicyV2(StrictModel):
    schema_version: Literal["literature-budget-policy-v2"] = (
        LITERATURE_BUDGET_POLICY_VERSION
    )
    max_queries: Annotated[int, Field(ge=1, le=64)] = 12
    max_raw_hits: Annotated[int, Field(ge=1, le=10_000)] = 120
    max_physical_requests: Annotated[int, Field(ge=1, le=256)] = 24
    family_reservations: Annotated[
        tuple[FamilyReservationV2, ...], Field(min_length=1, max_length=16)
    ]
    required_historical_buckets: Annotated[
        tuple[PublicationYearBucketV2, ...], Field(max_length=16)
    ] = ()

    @model_validator(mode="after")
    def validate_policy(self) -> LiteratureBudgetPolicyV2:
        families = tuple(item.family for item in self.family_reservations)
        if len(set(families)) != len(families):
            raise ValueError("family reservations must be unique")
        if sum(item.min_queries for item in self.family_reservations) > self.max_queries:
            raise ValueError("minimum family query reservations exceed max_queries")
        if self.max_raw_hits < self.max_queries:
            raise ValueError("raw-hit budget must cover every selected query")
        if self.max_physical_requests < self.max_queries:
            raise ValueError("physical-request budget must cover every selected query")
        bucket_ids = tuple(item.bucket_id for item in self.required_historical_buckets)
        if len(set(bucket_ids)) != len(bucket_ids):
            raise ValueError("required historical bucket IDs must be unique")
        historical_minimum = next(
            (
                item.min_queries
                for item in self.family_reservations
                if item.family is LiteratureQueryFamily.HISTORICAL
            ),
            0,
        )
        if historical_minimum < len(self.required_historical_buckets):
            raise ValueError("historical query reservation cannot cover required eras")
        return self

    @property
    def policy_sha256(self) -> str:
        return canonical_sha256(self.model_dump(mode="json"))


class LiteratureQueryCandidateV2(StrictModel):
    schema_version: Literal["literature-query-candidate-v2"] = (
        LITERATURE_QUERY_CANDIDATE_VERSION
    )
    candidate_id: Identifier
    provider_id: Identifier
    provider_query_id: Identifier
    family: LiteratureQueryFamily
    year_bucket_id: Identifier | None = None
    publication_year_from: Annotated[int, Field(ge=1600, le=2200)] | None = None
    publication_year_to: Annotated[int, Field(ge=1600, le=2200)] | None = None
    priority: Annotated[int, Field(ge=0, le=10_000)] = 100
    required: bool = False

    @model_validator(mode="after")
    def validate_candidate(self) -> LiteratureQueryCandidateV2:
        years = (self.publication_year_from, self.publication_year_to)
        if self.year_bucket_id is None and any(value is not None for value in years):
            raise ValueError("year-bounded candidates require a bucket ID")
        if self.year_bucket_id is not None and any(value is None for value in years):
            raise ValueError("year bucket candidates require both year boundaries")
        if (
            self.publication_year_from is not None
            and self.publication_year_to is not None
            and self.publication_year_from > self.publication_year_to
        ):
            raise ValueError("candidate publication year range is reversed")
        if self.family is LiteratureQueryFamily.HISTORICAL and self.year_bucket_id is None:
            raise ValueError("historical candidates require a year bucket")
        expected = deterministic_id(
            "lit-query",
            self.model_dump(mode="json", exclude={"candidate_id"}),
        )
        if self.candidate_id != expected:
            raise ValueError("literature candidate ID does not match canonical content")
        return self


class LiteratureTaskAllowanceV2(StrictModel):
    candidate_id: Identifier
    provider_id: Identifier
    provider_query_id: Identifier
    family: LiteratureQueryFamily
    year_bucket_id: Identifier | None = None
    execution_ordinal: Annotated[int, Field(ge=1, le=64)]
    max_raw_hits: Annotated[int, Field(ge=1, le=10_000)]
    max_physical_requests: Annotated[int, Field(ge=1, le=256)]


class LiteratureBudgetPlanV2(StrictModel):
    schema_version: Literal["literature-budget-plan-v2"] = (
        LITERATURE_BUDGET_PLAN_VERSION
    )
    plan_id: Identifier
    policy_sha256: Sha256
    max_queries: Annotated[int, Field(ge=1, le=64)]
    max_raw_hits: Annotated[int, Field(ge=1, le=10_000)]
    max_physical_requests: Annotated[int, Field(ge=1, le=256)]
    selected_candidate_ids: Annotated[
        tuple[Identifier, ...], Field(min_length=1, max_length=64)
    ]
    unselected_candidate_ids: Annotated[tuple[Identifier, ...], Field(max_length=256)]
    allowances: Annotated[
        tuple[LiteratureTaskAllowanceV2, ...], Field(min_length=1, max_length=64)
    ]

    @model_validator(mode="after")
    def validate_plan(self) -> LiteratureBudgetPlanV2:
        if len(set(self.selected_candidate_ids)) != len(self.selected_candidate_ids):
            raise ValueError("selected candidate IDs must be unique")
        if len(set(self.unselected_candidate_ids)) != len(self.unselected_candidate_ids):
            raise ValueError("unselected candidate IDs must be unique")
        if set(self.selected_candidate_ids) & set(self.unselected_candidate_ids):
            raise ValueError("selected and unselected candidates must be disjoint")
        if tuple(item.candidate_id for item in self.allowances) != self.selected_candidate_ids:
            raise ValueError("allowances must follow selected candidate order")
        if tuple(item.execution_ordinal for item in self.allowances) != tuple(
            range(1, len(self.allowances) + 1)
        ):
            raise ValueError("execution ordinals must be contiguous")
        if len(self.allowances) > self.max_queries:
            raise ValueError("allowances exceed max_queries")
        if sum(item.max_raw_hits for item in self.allowances) != self.max_raw_hits:
            raise ValueError("task allowances must reserve the full raw-hit budget")
        if (
            sum(item.max_physical_requests for item in self.allowances)
            != self.max_physical_requests
        ):
            raise ValueError("task allowances must reserve the full physical budget")
        expected = deterministic_id(
            "lit-budget",
            self.model_dump(mode="json", exclude={"plan_id"}),
        )
        if self.plan_id != expected:
            raise ValueError("literature budget plan ID does not match canonical content")
        return self


def default_materials_literature_budget_v2() -> LiteratureBudgetPolicyV2:
    """Return the reviewed first-pass allocation for materials discovery."""

    return LiteratureBudgetPolicyV2(
        family_reservations=(
            FamilyReservationV2(
                family=LiteratureQueryFamily.MATERIAL,
                min_queries=2,
                hit_weight=4,
                physical_request_weight=2,
            ),
            FamilyReservationV2(
                family=LiteratureQueryFamily.MECHANISM,
                min_queries=1,
                hit_weight=3,
                physical_request_weight=2,
            ),
            FamilyReservationV2(
                family=LiteratureQueryFamily.SOFT_CHEMISTRY,
                min_queries=1,
                hit_weight=3,
                physical_request_weight=2,
            ),
            FamilyReservationV2(
                family=LiteratureQueryFamily.HISTORICAL,
                min_queries=4,
                hit_weight=2,
            ),
            FamilyReservationV2(
                family=LiteratureQueryFamily.CITATION,
                min_queries=1,
                hit_weight=2,
            ),
            FamilyReservationV2(
                family=LiteratureQueryFamily.BRIDGE,
                min_queries=1,
                hit_weight=1,
            ),
            FamilyReservationV2(
                family=LiteratureQueryFamily.COUNTER,
                min_queries=0,
                hit_weight=1,
            ),
        ),
        required_historical_buckets=(
            PublicationYearBucketV2(
                bucket_id="era-1960-1979", year_from=1960, year_to=1979
            ),
            PublicationYearBucketV2(
                bucket_id="era-1980-1999", year_from=1980, year_to=1999
            ),
            PublicationYearBucketV2(
                bucket_id="era-2000-2014", year_from=2000, year_to=2014
            ),
            PublicationYearBucketV2(
                bucket_id="era-2015-2026", year_from=2015, year_to=2026
            ),
        ),
    )


def allocate_literature_budget_v2(
    candidates: tuple[LiteratureQueryCandidateV2, ...],
    *,
    policy: LiteratureBudgetPolicyV2,
) -> LiteratureBudgetPlanV2:
    """Select breadth-first routes and assign non-stealable per-task budgets."""

    if not candidates:
        raise LiteratureBudgetError("EMPTY_CANDIDATE_POOL", "candidate pool is empty")
    if len({item.candidate_id for item in candidates}) != len(candidates):
        raise LiteratureBudgetError(
            "DUPLICATE_CANDIDATE_ID", "candidate IDs must be unique"
        )
    ordered = tuple(
        sorted(
            candidates,
            key=lambda item: (
                item.priority,
                item.family.value,
                item.year_bucket_id or "",
                item.candidate_id,
            ),
        )
    )
    by_family: dict[LiteratureQueryFamily, list[LiteratureQueryCandidateV2]] = (
        defaultdict(list)
    )
    for candidate in ordered:
        by_family[candidate.family].append(candidate)

    selected: list[LiteratureQueryCandidateV2] = []
    selected_ids: set[str] = set()
    reservations = {item.family: item for item in policy.family_reservations}
    for reservation in policy.family_reservations:
        family_candidates = _breadth_first_by_year(by_family[reservation.family])
        if len(family_candidates) < reservation.min_queries:
            raise LiteratureBudgetError(
                "MISSING_FAMILY_RESERVATION",
                f"{reservation.family.value} has fewer candidates than its reservation",
            )
        for candidate in family_candidates[: reservation.min_queries]:
            selected.append(candidate)
            selected_ids.add(candidate.candidate_id)

    required = tuple(item for item in ordered if item.required)
    for candidate in required:
        if candidate.candidate_id not in selected_ids:
            selected.append(candidate)
            selected_ids.add(candidate.candidate_id)
    if len(selected) > policy.max_queries:
        raise LiteratureBudgetError(
            "REQUIRED_QUERIES_EXCEED_BUDGET",
            "required and reserved queries exceed max_queries",
        )

    remaining = tuple(item for item in ordered if item.candidate_id not in selected_ids)
    for candidate in _breadth_first_by_family_and_year(remaining):
        if len(selected) >= policy.max_queries:
            break
        selected.append(candidate)
        selected_ids.add(candidate.candidate_id)

    _validate_required_historical_buckets(selected, policy)
    selected = _breadth_first_by_family_and_year(tuple(selected))
    hit_allowances = _weighted_allowances(
        selected,
        total=policy.max_raw_hits,
        weights={family: item.hit_weight for family, item in reservations.items()},
    )
    request_allowances = _weighted_allowances(
        selected,
        total=policy.max_physical_requests,
        weights={
            family: item.physical_request_weight
            for family, item in reservations.items()
        },
    )
    unselected = tuple(
        item.candidate_id for item in ordered if item.candidate_id not in selected_ids
    )
    payload = {
        "schema_version": LITERATURE_BUDGET_PLAN_VERSION,
        "policy_sha256": policy.policy_sha256,
        "max_queries": policy.max_queries,
        "max_raw_hits": policy.max_raw_hits,
        "max_physical_requests": policy.max_physical_requests,
        "selected_candidate_ids": tuple(item.candidate_id for item in selected),
        "unselected_candidate_ids": unselected,
        "allowances": tuple(
            LiteratureTaskAllowanceV2(
                candidate_id=candidate.candidate_id,
                provider_id=candidate.provider_id,
                provider_query_id=candidate.provider_query_id,
                family=candidate.family,
                year_bucket_id=candidate.year_bucket_id,
                execution_ordinal=index + 1,
                max_raw_hits=hit_allowances[index],
                max_physical_requests=request_allowances[index],
            )
            for index, candidate in enumerate(selected)
        ),
    }
    return LiteratureBudgetPlanV2(
        plan_id=deterministic_id("lit-budget", payload),
        **payload,
    )


def make_literature_query_candidate_v2(
    *,
    provider_id: str,
    provider_query_id: str,
    family: LiteratureQueryFamily,
    year_bucket_id: str | None = None,
    publication_year_from: int | None = None,
    publication_year_to: int | None = None,
    priority: int = 100,
    required: bool = False,
) -> LiteratureQueryCandidateV2:
    """Construct a candidate with its canonical identity."""

    payload = {
        "schema_version": LITERATURE_QUERY_CANDIDATE_VERSION,
        "provider_id": provider_id,
        "provider_query_id": provider_query_id,
        "family": family,
        "year_bucket_id": year_bucket_id,
        "publication_year_from": publication_year_from,
        "publication_year_to": publication_year_to,
        "priority": priority,
        "required": required,
    }
    return LiteratureQueryCandidateV2(
        candidate_id=deterministic_id("lit-query", payload),
        **payload,
    )


def _breadth_first_by_year(
    candidates: list[LiteratureQueryCandidateV2],
) -> list[LiteratureQueryCandidateV2]:
    groups: dict[str, list[LiteratureQueryCandidateV2]] = defaultdict(list)
    for candidate in candidates:
        groups[candidate.year_bucket_id or "all-years"].append(candidate)
    return _round_robin(groups)


def _breadth_first_by_family_and_year(
    candidates: tuple[LiteratureQueryCandidateV2, ...] | list[LiteratureQueryCandidateV2],
) -> list[LiteratureQueryCandidateV2]:
    groups: dict[str, list[LiteratureQueryCandidateV2]] = defaultdict(list)
    for candidate in candidates:
        key = f"{candidate.family.value}:{candidate.year_bucket_id or 'all-years'}"
        groups[key].append(candidate)
    return _round_robin(groups)


def _round_robin(
    groups: dict[str, list[LiteratureQueryCandidateV2]],
) -> list[LiteratureQueryCandidateV2]:
    keys = sorted(groups)
    offsets = {key: 0 for key in keys}
    result: list[LiteratureQueryCandidateV2] = []
    while True:
        progressed = False
        for key in keys:
            offset = offsets[key]
            if offset < len(groups[key]):
                result.append(groups[key][offset])
                offsets[key] += 1
                progressed = True
        if not progressed:
            return result


def _weighted_allowances(
    selected: list[LiteratureQueryCandidateV2],
    *,
    total: int,
    weights: dict[LiteratureQueryFamily, int],
) -> list[int]:
    if total < len(selected):
        raise LiteratureBudgetError(
            "INSUFFICIENT_TASK_BUDGET", "every selected task requires one budget unit"
        )
    allowances = [1] * len(selected)
    weighted_indices = [
        index
        for index, candidate in enumerate(selected)
        for _ in range(weights.get(candidate.family, 1))
    ]
    remaining = total - len(selected)
    for offset in range(remaining):
        allowances[weighted_indices[offset % len(weighted_indices)]] += 1
    return allowances


def _validate_required_historical_buckets(
    selected: list[LiteratureQueryCandidateV2],
    policy: LiteratureBudgetPolicyV2,
) -> None:
    selected_buckets = {
        item.year_bucket_id
        for item in selected
        if item.family is LiteratureQueryFamily.HISTORICAL
    }
    for bucket in policy.required_historical_buckets:
        matches = [
            item
            for item in selected
            if item.family is LiteratureQueryFamily.HISTORICAL
            and item.year_bucket_id == bucket.bucket_id
            and item.publication_year_from == bucket.year_from
            and item.publication_year_to == bucket.year_to
        ]
        if bucket.bucket_id not in selected_buckets or not matches:
            raise LiteratureBudgetError(
                "MISSING_HISTORICAL_BUCKET",
                f"required historical bucket {bucket.bucket_id} is not selected",
            )
