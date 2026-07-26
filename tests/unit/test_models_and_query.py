from __future__ import annotations

import pytest
from pydantic import ValidationError

from material_agent.retrieval.models import NumericRange, Requirement
from material_agent.retrieval.query import QueryPlanningError, build_query_plan


def test_numeric_range_rejects_invalid_bounds() -> None:
    with pytest.raises(ValidationError):
        NumericRange(min=2.0, max=1.0, unit="eV")


def test_requirement_rejects_element_conflict(requirement: Requirement) -> None:
    payload = requirement.model_dump(mode="json")
    payload["hard_constraints"]["exclude_elements"] = ["Si"]
    with pytest.raises(ValidationError):
        Requirement.model_validate(payload)


def test_query_plan_maps_confirmed_constraints(
    requirement, requirement_hash, adapter, policy
) -> None:
    plan = build_query_plan(
        requirement, requirement_hash, adapter.metadata(), policy
    )
    assert plan.pushdown_filters["elements"] == ["O", "Si"]
    assert plan.pushdown_filters["band_gap"] == (0.5, 1.0)
    assert plan.pushdown_filters["energy_above_hull"] == (0.0, 0.05)
    assert plan.pushdown_filters["is_metal"] is False
    assert plan.pushdown_filters["deprecated"] is False
    assert plan.include_gnome is False
    assert plan.max_records_scanned == 5000
    assert plan.num_chunks == 10


def test_query_plan_rejects_unconfirmed_requirement(
    requirement, requirement_hash, adapter, policy
) -> None:
    unconfirmed = requirement.model_copy(update={"confirmed_by_user": False})
    with pytest.raises(QueryPlanningError):
        build_query_plan(
            unconfirmed, requirement_hash, adapter.metadata(), policy
        )


def test_min_only_range_stays_local(
    requirement, requirement_hash, adapter, policy
) -> None:
    hard = requirement.hard_constraints.model_copy(
        update={"band_gap_ev": NumericRange(min=0.5, unit="eV")}
    )
    changed = requirement.model_copy(update={"hard_constraints": hard})
    plan = build_query_plan(changed, requirement_hash, adapter.metadata(), policy)
    assert "band_gap" not in plan.pushdown_filters
    assert "band_gap" in plan.local_only_constraints

