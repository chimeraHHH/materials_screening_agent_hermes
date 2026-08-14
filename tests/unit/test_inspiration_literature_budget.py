from __future__ import annotations

from copy import deepcopy

import pytest
from pydantic import ValidationError

from material_agent.inspiration.literature_budget import (
    LiteratureBudgetError,
    LiteratureBudgetPlanV2,
    LiteratureQueryFamily,
    allocate_literature_budget_v2,
    default_materials_literature_budget_v2,
    make_literature_query_candidate_v2,
)


def _candidate(
    name: str,
    family: LiteratureQueryFamily,
    *,
    bucket: str | None = None,
    year_from: int | None = None,
    year_to: int | None = None,
    priority: int = 100,
    required: bool = False,
):
    return make_literature_query_candidate_v2(
        provider_id="semantic-scholar",
        provider_query_id=f"provider-{name}",
        family=family,
        year_bucket_id=bucket,
        publication_year_from=year_from,
        publication_year_to=year_to,
        priority=priority,
        required=required,
    )


def _pool():
    return (
        _candidate("material-tis2", LiteratureQueryFamily.MATERIAL, priority=1),
        _candidate("material-tise2", LiteratureQueryFamily.MATERIAL, priority=2),
        _candidate("material-extra", LiteratureQueryFamily.MATERIAL, priority=50),
        _candidate("mechanism", LiteratureQueryFamily.MECHANISM, priority=3),
        _candidate("softchem", LiteratureQueryFamily.SOFT_CHEMISTRY, priority=4),
        _candidate(
            "history-1960",
            LiteratureQueryFamily.HISTORICAL,
            bucket="era-1960-1979",
            year_from=1960,
            year_to=1979,
            priority=5,
            required=True,
        ),
        _candidate(
            "history-1980",
            LiteratureQueryFamily.HISTORICAL,
            bucket="era-1980-1999",
            year_from=1980,
            year_to=1999,
            priority=6,
            required=True,
        ),
        _candidate(
            "history-2000",
            LiteratureQueryFamily.HISTORICAL,
            bucket="era-2000-2014",
            year_from=2000,
            year_to=2014,
            priority=7,
            required=True,
        ),
        _candidate(
            "history-2015",
            LiteratureQueryFamily.HISTORICAL,
            bucket="era-2015-2026",
            year_from=2015,
            year_to=2026,
            priority=8,
            required=True,
        ),
        _candidate("citation", LiteratureQueryFamily.CITATION, priority=9),
        _candidate("bridge", LiteratureQueryFamily.BRIDGE, priority=10),
        _candidate("counter", LiteratureQueryFamily.COUNTER, priority=11),
    )


def test_allocator_reserves_every_family_and_historical_era() -> None:
    policy = default_materials_literature_budget_v2()
    plan = allocate_literature_budget_v2(_pool(), policy=policy)

    families = {item.family for item in plan.allowances}
    assert {
        LiteratureQueryFamily.MATERIAL,
        LiteratureQueryFamily.MECHANISM,
        LiteratureQueryFamily.SOFT_CHEMISTRY,
        LiteratureQueryFamily.HISTORICAL,
        LiteratureQueryFamily.CITATION,
        LiteratureQueryFamily.BRIDGE,
    } <= families
    assert {
        item.year_bucket_id
        for item in plan.allowances
        if item.family is LiteratureQueryFamily.HISTORICAL
    } == {
        "era-1960-1979",
        "era-1980-1999",
        "era-2000-2014",
        "era-2015-2026",
    }
    assert sum(item.max_raw_hits for item in plan.allowances) == 120
    assert sum(item.max_physical_requests for item in plan.allowances) == 24
    assert all(item.max_raw_hits >= 1 for item in plan.allowances)
    assert all(item.max_physical_requests >= 1 for item in plan.allowances)


def test_task_hit_allowances_are_non_stealable_and_weighted() -> None:
    plan = allocate_literature_budget_v2(
        _pool(), policy=default_materials_literature_budget_v2()
    )
    by_family: dict[LiteratureQueryFamily, list[int]] = {}
    for allowance in plan.allowances:
        by_family.setdefault(allowance.family, []).append(allowance.max_raw_hits)

    assert min(by_family[LiteratureQueryFamily.MATERIAL]) > min(
        by_family[LiteratureQueryFamily.BRIDGE]
    )
    assert len({item.candidate_id for item in plan.allowances}) == len(plan.allowances)


def test_allocator_is_deterministic_under_candidate_reordering() -> None:
    policy = default_materials_literature_budget_v2()
    forward = allocate_literature_budget_v2(_pool(), policy=policy)
    reverse = allocate_literature_budget_v2(tuple(reversed(_pool())), policy=policy)

    assert forward == reverse
    assert LiteratureBudgetPlanV2.model_validate_json(forward.model_dump_json()) == forward


def test_missing_historical_era_fails_closed() -> None:
    candidates = tuple(
        item
        for item in _pool()
        if item.year_bucket_id != "era-1960-1979"
    )
    with pytest.raises(
        LiteratureBudgetError,
        match="MISSING_FAMILY_RESERVATION|MISSING_HISTORICAL_BUCKET",
    ):
        allocate_literature_budget_v2(
            candidates,
            policy=default_materials_literature_budget_v2(),
        )


def test_required_queries_cannot_silently_overflow_max_queries() -> None:
    policy = default_materials_literature_budget_v2().model_copy(
        update={"max_queries": 10}
    )
    extra_required = _candidate(
        "required-counter",
        LiteratureQueryFamily.COUNTER,
        priority=0,
        required=True,
    )
    with pytest.raises(LiteratureBudgetError, match="REQUIRED_QUERIES_EXCEED_BUDGET"):
        allocate_literature_budget_v2(
            (*_pool(), extra_required),
            policy=policy,
        )


def test_budget_plan_rejects_unknown_fields() -> None:
    plan = allocate_literature_budget_v2(
        _pool(), policy=default_materials_literature_budget_v2()
    )
    payload = deepcopy(plan.model_dump(mode="json"))
    payload["shared_unbounded_hits"] = True

    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        LiteratureBudgetPlanV2.model_validate(payload)
