from __future__ import annotations

import pytest

from material_agent.gateway.models import (
    InspirationBudgetV1,
    InspirationConstraintsV1,
    InspirationRunRequestV1,
)
from material_agent.inspiration.policy import SearchExecutionMode
from material_agent.integration.request_compiler import (
    HermesInspirationRequestCompiler,
    HermesRequestCompilationError,
)


def _request(
    *,
    goal: str = "Find a bounded flat-band mechanism hypothesis.",
    required_elements: tuple[str, ...] = ("Se", "Ti"),
    excluded_elements: tuple[str, ...] = ("Pb",),
    material_classes: tuple[str, ...] = (
        "layered transition-metal dichalcogenide",
    ),
    dimensionality: str = "2D",
    target_features: tuple[str, ...] = ("electronic flat band",),
    top_k: int = 2,
    require_diverse_routes: bool = True,
    budget: InspirationBudgetV1 | None = None,
) -> InspirationRunRequestV1:
    return InspirationRunRequestV1(
        submission_id="compiler-test-submission",
        goal=goal,
        constraints=InspirationConstraintsV1(
            required_elements=required_elements,
            excluded_elements=excluded_elements,
            material_classes=material_classes,
            dimensionality=dimensionality,
            target_features=target_features,
            top_k=top_k,
            require_diverse_routes=require_diverse_routes,
            budget=budget
            or InspirationBudgetV1(
                max_search_requests=8,
                max_unique_documents=3,
                max_passages=3,
                max_model_calls=0,
                max_walltime_seconds=300,
            ),
        ),
    )


def test_goal_paraphrases_do_not_change_structured_scientific_scope() -> None:
    compiler = HermesInspirationRequestCompiler(max_retries_per_query=1)
    first = compiler.compile(
        _request(goal="Look for flat electronic bands in the bounded catalog.")
    )
    second = compiler.compile(
        _request(
            goal=(
                "Use cross-domain mechanisms to propose a controlled narrow-band "
                "structure for review."
            )
        )
    )

    assert first.policy == second.policy
    assert first.target_tag_ids == second.target_tag_ids == (
        "electronic-flat-band",
    )
    assert first.parent_catalog_entry_id == "operator-parent-tis2-v1"
    assert first.expected_output_elements == ("Se", "Ti")
    assert first.goal_sha256 != second.goal_sha256


@pytest.mark.parametrize(
    "feature",
    (
        "electronic flat band",
        "Electronic Narrow Band",
        "flat-electronic band",
        "narrow electronic band",
    ),
)
def test_strict_reviewed_target_vocabulary_maps_to_one_target(feature: str) -> None:
    compiled = HermesInspirationRequestCompiler().compile(
        _request(target_features=(feature,))
    )

    assert compiled.target_tag_ids == ("electronic-flat-band",)


def test_every_budget_and_selection_field_has_compiled_execution_meaning() -> None:
    budget = InspirationBudgetV1(
        max_search_requests=9,
        max_unique_documents=7,
        max_passages=11,
        max_model_calls=0,
        max_walltime_seconds=420,
    )
    compiled = HermesInspirationRequestCompiler(max_retries_per_query=1).compile(
        _request(
            required_elements=("Ti",),
            excluded_elements=("S",),
            material_classes=("Layered Transition Metal Compound",),
            top_k=4,
            require_diverse_routes=True,
            budget=budget,
        )
    )
    policy = compiled.policy

    assert policy.search_mode is SearchExecutionMode.PUBLIC_METADATA_API
    assert policy.network_access is True
    assert policy.search.max_queries == 3
    assert compiled.physical_search_attempt_limit == 6
    assert compiled.physical_search_attempt_limit <= budget.max_search_requests
    assert policy.search.max_unique_documents == budget.max_unique_documents
    assert policy.search.max_raw_hits == budget.max_unique_documents
    assert policy.passages.max_total == budget.max_passages
    assert policy.embedding.max_passages == budget.max_passages
    assert policy.llm.enabled is False
    assert policy.llm.max_calls == budget.max_model_calls == 0
    assert policy.runtime.max_walltime_seconds == budget.max_walltime_seconds
    assert policy.fetch.max_requests == 0
    assert policy.fetch.allow_pdf_fulltext is False
    assert policy.selection.top_k == 4
    assert policy.selection.min_mechanisms_when_available == 2
    assert policy.transformation.max_plans >= policy.selection.top_k
    assert compiled.normalized_material_classes == (
        "layered transition metal compound",
    )


def test_require_diverse_routes_false_disables_the_mechanism_quota() -> None:
    compiled = HermesInspirationRequestCompiler().compile(
        _request(top_k=5, require_diverse_routes=False)
    )

    assert compiled.policy.selection.top_k == 5
    assert compiled.policy.selection.min_mechanisms_when_available == 1


@pytest.mark.parametrize(
    ("overrides", "error_code"),
    (
        ({"target_features": ("superconductivity",)}, "UNSUPPORTED_TARGET_FEATURE"),
        ({"target_features": ()}, "UNSUPPORTED_TARGET_FEATURE"),
        (
            {"material_classes": ("three-dimensional oxide",)},
            "UNSUPPORTED_MATERIAL_CLASS",
        ),
        ({"dimensionality": "3D"}, "UNSUPPORTED_DIMENSIONALITY"),
        (
            {"required_elements": ("S", "Ti")},
            "UNSATISFIABLE_REQUIRED_ELEMENTS",
        ),
        (
            {"required_elements": ("Ti",), "excluded_elements": ("Se",)},
            "EXCLUDED_OUTPUT_ELEMENT",
        ),
    ),
)
def test_unsupported_scientific_constraints_fail_closed(
    overrides: dict[str, object],
    error_code: str,
) -> None:
    with pytest.raises(HermesRequestCompilationError) as captured:
        HermesInspirationRequestCompiler().compile(_request(**overrides))

    assert captured.value.code == error_code


@pytest.mark.parametrize(
    ("budget", "error_code"),
    (
        (
            InspirationBudgetV1(
                max_search_requests=5,
                max_unique_documents=3,
                max_passages=3,
                max_model_calls=0,
                max_walltime_seconds=300,
            ),
            "INSUFFICIENT_SEARCH_BUDGET",
        ),
        (
            InspirationBudgetV1(
                max_search_requests=8,
                max_unique_documents=2,
                max_passages=3,
                max_model_calls=0,
                max_walltime_seconds=300,
            ),
            "INSUFFICIENT_DOCUMENT_BUDGET",
        ),
        (
            InspirationBudgetV1(
                max_search_requests=8,
                max_unique_documents=3,
                max_passages=2,
                max_model_calls=0,
                max_walltime_seconds=300,
            ),
            "INSUFFICIENT_PASSAGE_BUDGET",
        ),
        (
            InspirationBudgetV1(
                max_search_requests=8,
                max_unique_documents=3,
                max_passages=3,
                max_model_calls=1,
                max_walltime_seconds=300,
            ),
            "UNSUPPORTED_MODEL_BUDGET",
        ),
        (
            InspirationBudgetV1(
                max_search_requests=8,
                max_unique_documents=3,
                max_passages=3,
                max_model_calls=0,
                max_walltime_seconds=179,
            ),
            "INSUFFICIENT_WALLTIME_BUDGET",
        ),
    ),
)
def test_incompatible_public_budgets_fail_closed(
    budget: InspirationBudgetV1,
    error_code: str,
) -> None:
    with pytest.raises(HermesRequestCompilationError) as captured:
        HermesInspirationRequestCompiler().compile(_request(budget=budget))

    assert captured.value.code == error_code
