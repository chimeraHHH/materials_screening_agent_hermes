from material_agent.retrieval.models import C2DBConstraints, SourceDatabase
from material_agent.retrieval.source_requirements import compile_source_requirement


def test_source_requirement_maps_supported_and_preserves_unmapped(requirement) -> None:
    compiled = compile_source_requirement(requirement, SourceDatabase.MC3D)
    mapped_ids = {item.constraint_id for item in compiled.mapped_constraints}
    unmapped_ids = {item.constraint_id for item in compiled.unmapped_constraints}

    assert "include_elements" in mapped_ids
    assert "band_gap_ev" in unmapped_ids
    assert "energy_above_hull_ev_atom" in unmapped_ids
    assert compiled.confirmed_by_user is False


def test_source_requirement_maps_c2db_native_condition(requirement) -> None:
    hard = requirement.hard_constraints.model_copy(
        update={
            "source_constraints": requirement.hard_constraints.source_constraints.model_copy(
                update={
                    "c2db": C2DBConstraints(layer_group="p4mm"),
                }
            )
        }
    )
    compiled = compile_source_requirement(
        requirement.model_copy(update={"hard_constraints": hard}),
        SourceDatabase.C2DB,
    )
    assert any(item.field == "c2db_layer_group" for item in compiled.mapped_constraints)
