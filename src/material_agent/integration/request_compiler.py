"""Deterministic Gateway-request compiler for the bounded Hermes beta.

Free-form ``goal`` text is retained as approval-bound user rationale, but it is
never parsed to infer scientific scope.  Executable scope comes only from the
strict Gateway constraint fields and the operator-owned vocabulary below.  The
current compiler intentionally targets one operator-owned six-route parent
catalog; requests that the catalog cannot satisfy fail before approval or
network access.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from material_agent.gateway.models import (
    InspirationRunRequestV1,
    canonical_sha256,
)
from material_agent.inspiration.parent_catalog import FLAT_BAND_PARENT_CATALOG_ID
from material_agent.inspiration.policy import (
    BridgeSearchPolicyV1,
    EmbeddingBudgetV1,
    FetchBudgetV1,
    InspirationPolicyV1,
    PassageBudgetV1,
    RuntimeBudgetV1,
    SearchBudgetV1,
    SearchExecutionMode,
    SelectionPolicyV1,
    TransformationBudgetV1,
)

PUBLIC_TARGET_TAG_IDS = ("electronic-flat-band",)
PUBLIC_PARENT_CATALOG_ID = FLAT_BAND_PARENT_CATALOG_ID
PUBLIC_OUTPUT_ELEMENTS = frozenset({"Se", "Ti"})
PUBLIC_LOGICAL_QUERY_COUNT = 4
PUBLIC_MIN_UNIQUE_DOCUMENTS = 4
PUBLIC_MIN_PASSAGES = 4
PUBLIC_MIN_WALLTIME_SECONDS = 180

_TARGET_FEATURE_VOCABULARY = frozenset(
    {
        "electronic flat band",
        "electronic narrow band",
        "flat electronic band",
        "narrow electronic band",
    }
)
_MATERIAL_CLASS_VOCABULARY = frozenset(
    {
        "layered transition metal compound",
        "layered transition metal dichalcogenide",
        "transition metal dichalcogenide",
    }
)


class HermesRequestCompilationError(ValueError):
    """A Gateway request cannot be represented by the bounded public runner."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(f"{code}: {message}")


class CompiledDiversityMode(StrEnum):
    """Reviewable execution meaning for the Gateway diversity preference."""

    MECHANISM_COVERAGE_WHEN_FEASIBLE = "MECHANISM_COVERAGE_WHEN_FEASIBLE"
    MMR_ONLY = "MMR_ONLY"


@dataclass(frozen=True, slots=True)
class CompiledHermesInspirationRequest:
    """Complete deterministic execution meaning derived from one Gateway DTO."""

    policy: InspirationPolicyV1
    target_tag_ids: tuple[str, ...]
    parent_catalog_id: str
    expected_output_elements: tuple[str, ...]
    normalized_material_classes: tuple[str, ...]
    normalized_target_features: tuple[str, ...]
    goal_sha256: str
    max_retries_per_query: int
    physical_search_attempt_limit: int
    diversity_mode: CompiledDiversityMode


class HermesInspirationRequestCompiler:
    """Compile only the reviewed flat/narrow electronic-band request family."""

    def __init__(self, *, max_retries_per_query: int = 1) -> None:
        if not isinstance(max_retries_per_query, int) or isinstance(
            max_retries_per_query, bool
        ):
            raise TypeError("max_retries_per_query must be an integer")
        if not 0 <= max_retries_per_query <= 5:
            raise ValueError("max_retries_per_query must be between zero and five")
        self.max_retries_per_query = max_retries_per_query

    def compile(
        self,
        request: InspirationRunRequestV1,
    ) -> CompiledHermesInspirationRequest:
        if not isinstance(request, InspirationRunRequestV1):
            raise TypeError("request must be an InspirationRunRequestV1")
        constraints = request.constraints

        normalized_targets = self._compile_vocabulary(
            constraints.target_features,
            vocabulary=_TARGET_FEATURE_VOCABULARY,
            field_name="target_features",
            required=True,
            error_code="UNSUPPORTED_TARGET_FEATURE",
        )
        normalized_classes = self._compile_vocabulary(
            constraints.material_classes,
            vocabulary=_MATERIAL_CLASS_VOCABULARY,
            field_name="material_classes",
            required=False,
            error_code="UNSUPPORTED_MATERIAL_CLASS",
        )
        if constraints.dimensionality != "2D":
            raise HermesRequestCompilationError(
                "UNSUPPORTED_DIMENSIONALITY",
                "the pinned parent catalog supports only explicit 2D requests",
            )

        required = set(constraints.required_elements)
        excluded = set(constraints.excluded_elements)
        unsupported_required = sorted(required - PUBLIC_OUTPUT_ELEMENTS)
        if unsupported_required:
            raise HermesRequestCompilationError(
                "UNSATISFIABLE_REQUIRED_ELEMENTS",
                "the pinned output route cannot satisfy all required elements",
            )
        if excluded & PUBLIC_OUTPUT_ELEMENTS:
            raise HermesRequestCompilationError(
                "EXCLUDED_OUTPUT_ELEMENT",
                "an excluded element is intrinsic to the pinned output route",
            )

        budget = constraints.budget
        physical_attempt_limit = PUBLIC_LOGICAL_QUERY_COUNT * (
            1 + self.max_retries_per_query
        )
        if budget.max_search_requests < physical_attempt_limit:
            raise HermesRequestCompilationError(
                "INSUFFICIENT_SEARCH_BUDGET",
                "search budget cannot cover the bounded logical queries and retries",
            )
        if budget.max_unique_documents < PUBLIC_MIN_UNIQUE_DOCUMENTS:
            raise HermesRequestCompilationError(
                "INSUFFICIENT_DOCUMENT_BUDGET",
                "public metadata mode requires room for one result per logical query",
            )
        if budget.max_passages < PUBLIC_MIN_PASSAGES:
            raise HermesRequestCompilationError(
                "INSUFFICIENT_PASSAGE_BUDGET",
                "public metadata mode requires room for one passage per logical query",
            )
        if budget.max_model_calls != 0:
            raise HermesRequestCompilationError(
                "UNSUPPORTED_MODEL_BUDGET",
                "this compiler supports no internal model calls",
            )
        if budget.max_walltime_seconds < PUBLIC_MIN_WALLTIME_SECONDS:
            raise HermesRequestCompilationError(
                "INSUFFICIENT_WALLTIME_BUDGET",
                "walltime budget is below the bounded public-search safety floor",
            )
        if budget.allow_full_pdf or budget.allow_expensive_computation:
            raise HermesRequestCompilationError(
                "UNSUPPORTED_EXPENSIVE_ACCESS",
                "full PDF and expensive-computation permissions are unsupported",
            )

        max_plans = max(6, constraints.top_k)
        mechanism_coverage_enabled = (
            constraints.require_diverse_routes and constraints.top_k >= 2
        )
        min_mechanisms = 2 if mechanism_coverage_enabled else 1
        diversity_mode = (
            CompiledDiversityMode.MECHANISM_COVERAGE_WHEN_FEASIBLE
            if mechanism_coverage_enabled
            else CompiledDiversityMode.MMR_ONLY
        )
        policy = InspirationPolicyV1(
            policy_id="hermes-public-flat-band-v1",
            search_mode=SearchExecutionMode.PUBLIC_METADATA_API,
            network_access=True,
            search=SearchBudgetV1(
                max_queries=PUBLIC_LOGICAL_QUERY_COUNT,
                max_physical_requests=physical_attempt_limit,
                max_direct_queries=1,
                max_bridge_queries=3,
                max_counter_queries=0,
                max_raw_hits=budget.max_unique_documents,
                max_unique_documents=budget.max_unique_documents,
            ),
            fetch=FetchBudgetV1(
                max_requests=0,
                max_total_bytes=0,
                max_bytes_per_response=0,
            ),
            passages=PassageBudgetV1(
                min_tokens=16,
                max_tokens=64,
                max_per_hit=1,
                max_total=budget.max_passages,
            ),
            embedding=EmbeddingBudgetV1(
                vector_dimension=32,
                max_passages=budget.max_passages,
                max_input_tokens=min(1_000_000, budget.max_passages * 64),
            ),
            bridge=BridgeSearchPolicyV1(max_bridge_packets=3),
            transformation=TransformationBudgetV1(
                max_plans=max_plans,
                max_plans_per_parent=1,
            ),
            selection=SelectionPolicyV1(
                top_k=constraints.top_k,
                max_per_parent_family=1,
                min_mechanisms_when_available=min_mechanisms,
            ),
            runtime=RuntimeBudgetV1(
                max_walltime_seconds=budget.max_walltime_seconds
            ),
        )
        return CompiledHermesInspirationRequest(
            policy=policy,
            target_tag_ids=PUBLIC_TARGET_TAG_IDS,
            parent_catalog_id=PUBLIC_PARENT_CATALOG_ID,
            expected_output_elements=tuple(sorted(PUBLIC_OUTPUT_ELEMENTS)),
            normalized_material_classes=normalized_classes,
            normalized_target_features=normalized_targets,
            goal_sha256=canonical_sha256({"goal": request.goal}),
            max_retries_per_query=self.max_retries_per_query,
            physical_search_attempt_limit=physical_attempt_limit,
            diversity_mode=diversity_mode,
        )

    @staticmethod
    def _compile_vocabulary(
        values: tuple[str, ...],
        *,
        vocabulary: frozenset[str],
        field_name: str,
        required: bool,
        error_code: str,
    ) -> tuple[str, ...]:
        normalized = tuple(sorted({_normalize_term(value) for value in values}))
        if required and not normalized:
            raise HermesRequestCompilationError(
                error_code,
                f"{field_name} must explicitly select the supported target",
            )
        unsupported = tuple(value for value in normalized if value not in vocabulary)
        if unsupported:
            raise HermesRequestCompilationError(
                error_code,
                f"{field_name} contains values outside the operator vocabulary",
            )
        return normalized


def _normalize_term(value: str) -> str:
    """Apply only reviewable spelling normalization, never fuzzy matching."""

    return " ".join(value.casefold().replace("-", " ").split())
