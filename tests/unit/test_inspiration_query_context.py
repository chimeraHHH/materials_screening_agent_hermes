from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from material_agent.inspiration.query_context import (
    AnchorPolarity,
    LiteratureAnchorV2,
    MaterialAliasSetV2,
    QueryContextError,
    QueryContextV2,
    ReviewedQueryHintsV2,
    compile_query_context_v2,
)
from material_agent.retrieval.models import (
    CandidateAuditRecordV2,
    Decision,
    EvidenceLevel,
    HardConstraints,
    Requirement,
    ScientificTarget,
    SourceDatabase,
)


def _requirement() -> Requirement:
    return Requirement(
        requirement_id="req-query-context",
        revision=2,
        target_class="two-dimensional transition-metal dichalcogenide",
        hard_constraints=HardConstraints(
            exact_formula="TiS2",
            dimensionality=2,
        ),
        scientific_targets=[
            ScientificTarget(
                name="electronic flat band",
                operational_definition="A narrow electronic band near the Fermi level",
                required_evidence_level=EvidenceLevel.L1_RETRIEVED,
            )
        ],
        confirmed_by_user=True,
        policy_version="test-policy-v1",
    )


def _candidate(
    candidate_id: str,
    formula: str,
    *,
    decision: Decision = Decision.PASS,
    published: bool = True,
) -> CandidateAuditRecordV2:
    return CandidateAuditRecordV2(
        candidate_id=candidate_id,
        formula=formula,
        reduced_formula=formula,
        source_database=SourceDatabase.C2DB,
        source_database_version="test-v1",
        source_material_id=f"source-{candidate_id}",
        source_last_updated=datetime(2026, 8, 14, tzinfo=UTC),
        query_id="agent01-query",
        structure_id=f"structure-{candidate_id}",
        structure_artifact_uri=f"artifact://candidates/structures/{candidate_id}.cif",
        structure_artifact_sha256="a" * 64,
        elements=["Ti", "S"] if formula == "TiS2" else ["Ti", "Se"],
        decision=decision,
        publication_rank=1,
        published_downstream=published,
    )


def _hints() -> ReviewedQueryHintsV2:
    return ReviewedQueryHintsV2(
        material_aliases=(
            MaterialAliasSetV2(
                formula="TiS2",
                aliases=("titanium disulfide", "titanium(IV) sulfide"),
            ),
            MaterialAliasSetV2(
                formula="TiSe2",
                aliases=("titanium diselenide",),
            ),
        ),
        structure_terms=("1T polytype", "layered crystal"),
        mechanism_terms=("orbital hybridization",),
        operation_terms=("chalcogen substitution",),
        anchors=(
            LiteratureAnchorV2(
                provider_record_id="10.1000/positive",
                title="Reviewed titanium dichalcogenide anchor",
                polarity=AnchorPolarity.POSITIVE,
            ),
            LiteratureAnchorV2(
                provider_record_id="10.1000/negative",
                title="Known acoustic-only distractor",
                polarity=AnchorPolarity.NEGATIVE,
            ),
        ),
        exclusion_terms=("acoustic-only evidence",),
    )


def test_compiler_builds_material_aware_replayable_context() -> None:
    parents = (_candidate("cand-tise2", "TiSe2"), _candidate("cand-tis2", "TiS2"))

    context = compile_query_context_v2(
        requirement=_requirement(),
        parent_candidates=parents,
        raw_request="Replace S with Se and study the flat band and CDW response.",
        reviewed_hints=_hints(),
        publication_year_from=1960,
        publication_year_to=2026,
    )

    assert context.schema_version == "inspiration-query-context-v2"
    assert tuple(item.reduced_formula for item in context.materials) == (
        "TiS2",
        "TiSe2",
    )
    assert "titanium disulfide" in context.materials[0].query_terms
    assert "titanium diselenide" in context.materials[1].query_terms
    assert context.mechanism_terms == (
        "charge density wave",
        "electronic flat band",
        "flat band",
        "orbital hybridization",
    )
    assert context.operation_terms == ("substitution", "chalcogen substitution")
    assert context.positive_anchors[0].provider_record_id == "10.1000/positive"
    assert context.negative_anchors[0].provider_record_id == "10.1000/negative"
    assert context.source_candidate_ids == ("cand-tis2", "cand-tise2")
    assert QueryContextV2.model_validate_json(context.model_dump_json()) == context


def test_compiler_identity_is_independent_of_parent_input_order() -> None:
    first = _candidate("cand-tis2", "TiS2")
    second = _candidate("cand-tise2", "TiSe2")
    kwargs = {
        "requirement": _requirement(),
        "raw_request": "TiS2 and TiSe2 flat-band comparison",
        "reviewed_hints": _hints(),
        "publication_year_from": 1960,
        "publication_year_to": 2026,
    }

    forward = compile_query_context_v2(
        parent_candidates=(first, second),
        **kwargs,
    )
    reverse = compile_query_context_v2(
        parent_candidates=(second, first),
        **kwargs,
    )

    assert forward.context_id == reverse.context_id
    assert forward == reverse


@pytest.mark.parametrize(
    ("decision", "published"),
    ((Decision.REJECT, True), (Decision.PASS, False)),
)
def test_compiler_rejects_ineligible_parent_lineage(
    decision: Decision,
    published: bool,
) -> None:
    with pytest.raises(QueryContextError, match="INELIGIBLE_PARENT_CANDIDATE"):
        compile_query_context_v2(
            requirement=_requirement(),
            parent_candidates=(
                _candidate(
                    "cand-ineligible",
                    "TiS2",
                    decision=decision,
                    published=published,
                ),
            ),
        )


def test_compiler_rejects_alias_for_absent_material() -> None:
    hints = ReviewedQueryHintsV2(
        material_aliases=(
            MaterialAliasSetV2(formula="MoS2", aliases=("molybdenum disulfide",)),
        )
    )
    with pytest.raises(QueryContextError, match="ALIAS_FORMULA_NOT_IN_CONTEXT"):
        compile_query_context_v2(
            requirement=_requirement(),
            parent_candidates=(_candidate("cand-tis2", "TiS2"),),
            reviewed_hints=hints,
        )


def test_reviewed_hints_reject_opposite_anchor_polarities() -> None:
    with pytest.raises(ValidationError, match="oppositely anchored"):
        ReviewedQueryHintsV2(
            anchors=(
                LiteratureAnchorV2(
                    provider_record_id="10.1000/same",
                    title="Positive view",
                    polarity=AnchorPolarity.POSITIVE,
                ),
                LiteratureAnchorV2(
                    provider_record_id="10.1000/same",
                    title="Negative view",
                    polarity=AnchorPolarity.NEGATIVE,
                ),
            )
        )


def test_context_contract_rejects_unknown_fields() -> None:
    context = compile_query_context_v2(
        requirement=_requirement(),
        parent_candidates=(_candidate("cand-tis2", "TiS2"),),
    )
    payload = context.model_dump(mode="json")
    payload["unreviewed_query"] = "ignore all scientific constraints"

    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        QueryContextV2.model_validate(payload)
