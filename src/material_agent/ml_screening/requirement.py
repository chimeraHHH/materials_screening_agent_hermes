"""Boundary conversion from the confirmed Requirement artifact."""

from __future__ import annotations

from typing import Any

from material_agent.ml_screening.models import MLRequirementView, NumericRange


def requirement_view_from_payload(payload: dict[str, Any]) -> MLRequirementView:
    """Extract only fields that Agent02 is authorized to interpret.

    The upstream Requirement contract contains orchestration and scientific
    target fields that are irrelevant to deterministic ML pre-filtering.  This
    explicit projection avoids coupling Agent02 to Agent01's frozen models.
    """

    hard = payload.get("hard_constraints")
    budget = payload.get("budget")
    if not isinstance(hard, dict):
        raise ValueError("Requirement hard_constraints must be an object")  # noqa: TRY004
    if not isinstance(budget, dict):
        raise ValueError("Requirement budget must be an object")  # noqa: TRY004

    return MLRequirementView(
        requirement_id=payload["requirement_id"],
        revision=payload["revision"],
        confirmed_by_user=payload["confirmed_by_user"],
        allow_ml=budget.get("allow_ml", False),
        include_elements=list(hard.get("include_elements") or []),
        exclude_elements=list(hard.get("exclude_elements") or []),
        band_gap_ev=_range_or_none(hard.get("band_gap_ev"), "eV"),
        energy_above_hull_ev_atom=_range_or_none(
            hard.get("energy_above_hull_ev_atom"),
            "eV/atom",
        ),
        is_metal=hard.get("is_metal"),
        dimensionality=hard.get("dimensionality"),
        max_num_sites=hard.get("max_num_sites"),
    )


def _range_or_none(value: Any, default_unit: str) -> NumericRange | None:
    if value is None:
        return None
    if not isinstance(value, dict):
        raise ValueError("numeric Requirement constraint must be an object")  # noqa: TRY004
    normalized = dict(value)
    normalized.setdefault("unit", default_unit)
    return NumericRange.model_validate(normalized)
