from __future__ import annotations

import hashlib

import pytest

from material_agent.retrieval.mp_screening import (
    MP_CAPABILITY_CATALOG,
    MappedClause,
    ScreeningIntent,
    UnmappedClause,
    MappingStatus,
    capability_catalog_hash,
    make_spec,
)


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
