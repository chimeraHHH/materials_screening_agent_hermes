from __future__ import annotations

import hashlib
from types import SimpleNamespace

import pytest

from material_agent.retrieval.mp_screening import (
    MP_CAPABILITY_CATALOG,
    MappedClause,
    ScreeningIntent,
    UnmappedClause,
    MappingStatus,
    capability_catalog_hash,
    make_spec,
    compile_mp_screening_spec,
)
from material_agent.retrieval.runner import (
    _limit_adaptive_deep_documents,
    _prefilter_adaptive_layered_documents,
    _prefilter_adaptive_summary_documents,
)
from material_agent.retrieval.models import RetrievalPolicy


def test_catalog_hash_and_compilation_are_stable() -> None:
    assert len(MP_CAPABILITY_CATALOG) >= 30
    first = capability_catalog_hash()
    assert first == capability_catalog_hash()
    spec = make_spec(
        requirement_id="req-1",
        requirement_revision=1,
        raw_request_sha256=hashlib.sha256(b"x").hexdigest(),
        mapped_clauses=[MappedClause(
            clause_id="c1", source_text="metal", capability_id="electronic.is_metal",
            intent=ScreeningIntent.HARD, operator="eq", value=True,
        )],
        unmapped_clauses=[UnmappedClause(
            clause_id="u1", source_text="near Fermi", status=MappingStatus.MISSING_THRESHOLD,
            reason="window not supplied",
        )],
    )
    assert spec.catalog_sha256 == first


def test_proxy_hard_constraint_is_rejected() -> None:
    with pytest.raises(ValueError, match="does not support|proxy capabilities"):
        make_spec(
            requirement_id="req-1", requirement_revision=1,
            raw_request_sha256="0" * 64,
            mapped_clauses=[MappedClause(
                clause_id="c1", source_text="vdW", capability_id="proxy.vdw_gap",
                intent=ScreeningIntent.HARD, operator="maximize",
            )],
            unmapped_clauses=[],
        )


def test_deep_budget_requires_explicit_approval() -> None:
    with pytest.raises(ValueError, match="requires approval"):
        make_spec(
            requirement_id="req-1", requirement_revision=1,
            raw_request_sha256="0" * 64, mapped_clauses=[], unmapped_clauses=[],
            deep_screen_limit=51,
        )
    approved = make_spec(
        requirement_id="req-1", requirement_revision=1,
        raw_request_sha256="0" * 64, mapped_clauses=[], unmapped_clauses=[],
        deep_screen_limit=51, deep_screen_approval_required=True,
    )
    assert approved.deep_screen_limit == 51


def test_transition_metal_capability_never_becomes_mp_elements_pushdown() -> None:
    spec = make_spec(
        requirement_id="req-1", requirement_revision=1,
        raw_request_sha256="0" * 64,
        mapped_clauses=[MappedClause(
            clause_id="tm", source_text="transition metal",
            capability_id="composition.has_transition_metal",
            intent=ScreeningIntent.HARD, operator="eq", value=True,
        )],
        unmapped_clauses=[],
    )
    compiled = compile_mp_screening_spec(spec)
    assert "elements" not in compiled.pushdown_filters
    assert compiled.local_clauses[0].capability_id == "composition.has_transition_metal"


def test_transition_metal_summary_prefilter_skips_nonmatching_structures() -> None:
    spec = make_spec(
        requirement_id="req-1", requirement_revision=1,
        raw_request_sha256="0" * 64,
        mapped_clauses=[MappedClause(
            clause_id="tm", source_text="transition metal",
            capability_id="composition.has_transition_metal",
            intent=ScreeningIntent.HARD, operator="eq", value=True,
        )],
        unmapped_clauses=[],
    )

    retained, skipped = _prefilter_adaptive_summary_documents(
        [{"material_id": "mp-as", "elements": ["As"]},
         {"material_id": "mp-fe", "elements": ["Fe", "S"]}],
        spec,
    )

    assert skipped == 1
    assert [item["material_id"] for item in retained] == ["mp-fe"]


def test_deep_budget_limits_structure_processing_in_material_id_order() -> None:
    spec = make_spec(
        requirement_id="req-1", requirement_revision=1,
        raw_request_sha256="0" * 64,
        mapped_clauses=[MappedClause(
            clause_id="bandwidth", source_text="W", capability_id="deep.sampled_bandwidth",
            intent=ScreeningIntent.HARD, operator="lte", value=0.05, unit="eV",
        )],
        unmapped_clauses=[], deep_screen_limit=2,
    )
    selected, skipped = _limit_adaptive_deep_documents(
        [{"material_id": "mp-3"}, {"material_id": "mp-1"}, {"material_id": "mp-2"}], spec,
    )
    assert [item["material_id"] for item in selected] == ["mp-1", "mp-2"]
    assert skipped == 1


def test_layered_prefilter_runs_before_deep_budget(monkeypatch: pytest.MonkeyPatch) -> None:
    from material_agent.retrieval import runner as runner_module

    spec = make_spec(
        requirement_id="req-1", requirement_revision=1,
        raw_request_sha256="0" * 64,
        mapped_clauses=[MappedClause(
            clause_id="layered", source_text="layered", capability_id="deep.layered",
            intent=ScreeningIntent.HARD, operator="eq", value=True,
        ), MappedClause(
            clause_id="bandwidth", source_text="W", capability_id="deep.sampled_bandwidth",
            intent=ScreeningIntent.HARD, operator="lte", value=0.05, unit="eV",
        )],
        unmapped_clauses=[], deep_screen_limit=1,
    )
    monkeypatch.setattr(
        runner_module, "process_structure",
        lambda structure, **kwargs: SimpleNamespace(structure=structure),
    )
    monkeypatch.setattr(
        runner_module, "calculate_dimensionality",
        lambda structure: SimpleNamespace(value=structure["dimension"], method="fixture"),
    )
    retained, audit = _prefilter_adaptive_layered_documents(
        [
            {"material_id": "mp-3", "structure": {"dimension": 3}},
            {"material_id": "mp-2", "structure": {"dimension": 2}},
            {"material_id": "mp-1", "structure": {"dimension": 2}},
        ],
        spec, RetrievalPolicy(),
    )
    selected, skipped = _limit_adaptive_deep_documents(retained, spec)
    assert [item["material_id"] for item in selected] == ["mp-1"]
    assert skipped == 1
    assert [item["structural_dimensionality"] for item in audit] == [2, 2, 3]
