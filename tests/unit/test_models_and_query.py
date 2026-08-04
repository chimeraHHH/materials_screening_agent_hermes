from __future__ import annotations

import math

import pytest
from pydantic import ValidationError

from material_agent.retrieval.models import (
    C2DBConstraints,
    MaterialsProjectConstraints,
    NumericRange,
    Requirement,
    SourceSpecificConstraints,
    SourceDatabase,
    SourceMetadata,
)
from material_agent.retrieval.normalizer import candidate_id_for
from material_agent.retrieval.query import (
    QueryPlanningError,
    build_query_plan,
    validate_requirement_contract,
)


def test_numeric_range_rejects_invalid_bounds() -> None:
    with pytest.raises(ValidationError):
        NumericRange(min=2.0, max=1.0, unit="eV")


@pytest.mark.parametrize("value", [math.nan, math.inf, -math.inf])
def test_numeric_range_rejects_non_finite_bounds(value: float) -> None:
    with pytest.raises(ValidationError):
        NumericRange(min=value, unit="eV")


def test_requirement_rejects_element_conflict(requirement: Requirement) -> None:
    payload = requirement.model_dump(mode="json")
    payload["hard_constraints"]["exclude_elements"] = ["Si"]
    with pytest.raises(ValidationError):
        Requirement.model_validate(payload)


def test_requirement_contract_rejects_invalid_element(requirement: Requirement) -> None:
    hard = requirement.hard_constraints.model_copy(
        update={"include_elements": ["NotAnElement"]}
    )
    changed = requirement.model_copy(update={"hard_constraints": hard})
    with pytest.raises(QueryPlanningError, match="invalid element symbol"):
        validate_requirement_contract(changed)


def test_requirement_contract_rejects_invalid_exact_formula(
    requirement: Requirement,
) -> None:
    hard = requirement.hard_constraints.model_copy(
        update={"exact_formula": "not a chemical formula"}
    )
    changed = requirement.model_copy(update={"hard_constraints": hard})
    with pytest.raises(QueryPlanningError, match="exact_formula is invalid"):
        validate_requirement_contract(changed)


@pytest.mark.parametrize(
    ("field", "unit"),
    [
        ("band_gap_ev", "meV"),
        ("energy_above_hull_ev_atom", "eV"),
    ],
)
def test_requirement_contract_rejects_unsupported_units(
    requirement: Requirement, field: str, unit: str
) -> None:
    current = getattr(requirement.hard_constraints, field)
    hard = requirement.hard_constraints.model_copy(
        update={field: current.model_copy(update={"unit": unit})}
    )
    changed = requirement.model_copy(update={"hard_constraints": hard})
    with pytest.raises(QueryPlanningError, match="unit must be exactly"):
        validate_requirement_contract(changed)


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


def test_exact_formula_is_recorded_as_local_constraint(
    requirement, requirement_hash, adapter, policy
) -> None:
    hard = requirement.hard_constraints.model_copy(update={"exact_formula": "O Si"})
    changed = requirement.model_copy(update={"hard_constraints": hard})
    plan = build_query_plan(changed, requirement_hash, adapter.metadata(), policy)
    assert "exact_formula" in plan.local_only_constraints
    assert "exact_formula" not in plan.pushdown_filters


def test_database_specific_constraints_are_local_and_source_scoped(
    requirement, requirement_hash, adapter, policy
) -> None:
    source_constraints = requirement.hard_constraints.source_constraints.model_copy(
        update={
            "materials_project": MaterialsProjectConstraints(
                density_g_cm3=NumericRange(min=2.0, max=3.0, unit="g/cm^3"),
                is_stable=True,
                crystal_system="cubic",
            )
        }
    )
    hard = requirement.hard_constraints.model_copy(
        update={"source_constraints": source_constraints}
    )
    changed = requirement.model_copy(update={"hard_constraints": hard})
    plan = build_query_plan(changed, requirement_hash, adapter.metadata(), policy)
    assert {
        "source.materials_project.density_g_cm3",
        "source.materials_project.is_stable",
        "source.materials_project.crystal_system",
    } <= set(plan.local_only_constraints)

    c2db_constraints = SourceSpecificConstraints(
        c2db=C2DBConstraints(layer_group="p4mm")
    )
    c2db_requirement = changed.model_copy(
        update={
            "hard_constraints": hard.model_copy(
                update={"source_constraints": c2db_constraints}
            )
        }
    )
    c2db_plan = build_query_plan(
        c2db_requirement,
        requirement_hash,
        adapter.metadata(),
        policy,
    )
    assert "unmapped:source.c2db.layer_group" in c2db_plan.local_only_constraints


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


def test_max_only_nonnegative_range_is_pushed_down(
    requirement, requirement_hash, adapter, policy
) -> None:
    hard = requirement.hard_constraints.model_copy(
        update={"band_gap_ev": NumericRange(max=1.0, unit="eV")}
    )
    changed = requirement.model_copy(update={"hard_constraints": hard})
    plan = build_query_plan(changed, requirement_hash, adapter.metadata(), policy)
    assert plan.pushdown_filters["band_gap"] == (0.0, 1.0)


def test_gnome_requires_explicit_requirement_opt_in(
    requirement, requirement_hash, adapter, policy
) -> None:
    default_plan = build_query_plan(
        requirement, requirement_hash, adapter.metadata(), policy
    )
    source_options = (
        requirement.data_sources.materials_project.model_copy(
            update={"include_gnome": True}
        )
    )
    data_sources = requirement.data_sources.model_copy(
        update={"materials_project": source_options}
    )
    opted_in = requirement.model_copy(update={"data_sources": data_sources})
    opted_in_plan = build_query_plan(
        opted_in, requirement_hash, adapter.metadata(), policy
    )
    assert default_plan.include_gnome is False
    assert opted_in_plan.include_gnome is True


def test_query_fingerprint_is_stable_and_database_version_sensitive(
    requirement, requirement_hash, adapter, policy
) -> None:
    metadata = adapter.metadata()
    first = build_query_plan(requirement, requirement_hash, metadata, policy)
    second = build_query_plan(requirement, requirement_hash, metadata, policy)
    changed_metadata = metadata.model_copy(update={"database_version": "fixture-next"})
    changed = build_query_plan(
        requirement, requirement_hash, changed_metadata, policy
    )
    assert first.query_fingerprint == second.query_fingerprint
    assert first.query_id == second.query_id
    assert first.query_fingerprint != changed.query_fingerprint


def test_candidate_id_is_stable_within_project_and_scoped_between_projects() -> None:
    first = candidate_id_for("project-a", "mp-1")
    assert first == candidate_id_for("project-a", "mp-1")
    assert first != candidate_id_for("project-b", "mp-1")
    assert first != candidate_id_for("project-a", "mp-2")
