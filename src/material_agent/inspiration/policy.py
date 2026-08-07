"""Bounded execution policy for the inspiration companion capability."""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Literal

from pydantic import Field, model_validator

from material_agent.inspiration.models import Identifier, Score, ShortText, StrictModel


INSPIRATION_POLICY_VERSION = "inspiration-policy-v1"


class SearchExecutionMode(StrEnum):
    OFFLINE_FIXTURE = "OFFLINE_FIXTURE"
    PUBLIC_METADATA_API = "PUBLIC_METADATA_API"


class SearchBudgetV1(StrictModel):
    max_queries: Annotated[int, Field(ge=1, le=64)] = 12
    max_direct_queries: Annotated[int, Field(ge=0, le=64)] = 6
    max_bridge_queries: Annotated[int, Field(ge=0, le=64)] = 4
    max_counter_queries: Annotated[int, Field(ge=0, le=64)] = 2
    max_raw_hits: Annotated[int, Field(ge=1, le=10_000)] = 120
    max_unique_documents: Annotated[int, Field(ge=1, le=2_000)] = 60

    @model_validator(mode="after")
    def validate_query_budget(self) -> SearchBudgetV1:
        allocated = (
            self.max_direct_queries
            + self.max_bridge_queries
            + self.max_counter_queries
        )
        if allocated > self.max_queries:
            raise ValueError("query-class allocation exceeds max_queries")
        if self.max_unique_documents > self.max_raw_hits:
            raise ValueError("unique-document budget exceeds raw-hit budget")
        return self


class FetchBudgetV1(StrictModel):
    max_requests: Annotated[int, Field(ge=0, le=2_000)] = 20
    max_total_bytes: Annotated[int, Field(ge=0, le=100_000_000)] = 10_000_000
    max_bytes_per_response: Annotated[int, Field(ge=0, le=10_000_000)] = 1_000_000
    timeout_seconds: Annotated[int, Field(ge=1, le=120)] = 20
    max_retries_per_request: Annotated[int, Field(ge=0, le=5)] = 2
    allow_html: bool = True
    allow_jats_xml: bool = True
    allow_pdf_fulltext: Literal[False] = False

    @model_validator(mode="after")
    def validate_fetch_budget(self) -> FetchBudgetV1:
        if self.max_requests == 0 and (
            self.max_total_bytes != 0 or self.max_bytes_per_response != 0
        ):
            raise ValueError("zero fetch requests requires zero byte budgets")
        if self.max_requests > 0 and self.max_bytes_per_response > self.max_total_bytes:
            raise ValueError("per-response byte budget exceeds total fetch byte budget")
        return self


class PassageBudgetV1(StrictModel):
    min_tokens: Annotated[int, Field(ge=16, le=512)] = 80
    max_tokens: Annotated[int, Field(ge=16, le=512)] = 220
    max_per_hit: Annotated[int, Field(ge=1, le=10)] = 3
    max_total: Annotated[int, Field(ge=1, le=2_000)] = 90
    local_dedup_jaccard: Score = 0.85
    claim_cues: Annotated[
        tuple[ShortText, ...], Field(min_length=1, max_length=32)
    ] = (
        "because",
        "demonstrate",
        "find that",
        "indicate",
        "leads to",
        "mechanism",
        "requires",
        "results show",
        "suggest",
        "suppresses",
        "enhances",
        "breaks",
    )

    @model_validator(mode="after")
    def validate_passage_budget(self) -> PassageBudgetV1:
        if self.min_tokens > self.max_tokens:
            raise ValueError("minimum passage tokens exceed maximum passage tokens")
        normalized_cues = tuple(cue.casefold() for cue in self.claim_cues)
        if len(set(normalized_cues)) != len(normalized_cues):
            raise ValueError("passage claim cues must be unique")
        return self


class EmbeddingBudgetV1(StrictModel):
    vectorizer_id: Identifier = "signed-hashing-v1"
    vector_dimension: Annotated[int, Field(ge=8, le=16_384)] = 256
    max_passages: Annotated[int, Field(ge=1, le=2_000)] = 90
    max_input_tokens: Annotated[int, Field(ge=1, le=1_000_000)] = 20_000


class LLMBudgetV1(StrictModel):
    enabled: bool = False
    max_calls: Annotated[int, Field(ge=0, le=64)] = 0
    max_input_tokens: Annotated[int, Field(ge=0, le=1_000_000)] = 0
    max_output_tokens: Annotated[int, Field(ge=0, le=250_000)] = 0

    @model_validator(mode="after")
    def validate_llm_budget(self) -> LLMBudgetV1:
        budgets = (self.max_calls, self.max_input_tokens, self.max_output_tokens)
        if self.enabled and any(value == 0 for value in budgets):
            raise ValueError("enabled LLM requires positive call and token budgets")
        if not self.enabled and any(value != 0 for value in budgets):
            raise ValueError("disabled LLM requires zero call and token budgets")
        return self


class BridgeSearchPolicyV1(StrictModel):
    max_graph_hops: Annotated[int, Field(ge=1, le=2)] = 2
    beam_width: Annotated[int, Field(ge=1, le=6)] = 6
    max_bridge_packets: Annotated[int, Field(ge=1, le=128)] = 24


class TransformationBudgetV1(StrictModel):
    registry_id: Identifier = "inspiration-substitution-registry-v1"
    allowed_operator_ids: tuple[Literal["SUBSTITUTE_EQUIVALENT_SITE_V1"], ...] = (
        "SUBSTITUTE_EQUIVALENT_SITE_V1",
    )
    max_plans: Annotated[int, Field(ge=1, le=1_000)] = 64
    max_plans_per_parent: Annotated[int, Field(ge=1, le=128)] = 16
    max_sites_per_structure: Annotated[int, Field(ge=1, le=2_000)] = 256

    @model_validator(mode="after")
    def validate_operators(self) -> TransformationBudgetV1:
        if not self.allowed_operator_ids:
            raise ValueError("at least one transformation operator is required")
        if len(set(self.allowed_operator_ids)) != len(self.allowed_operator_ids):
            raise ValueError("allowed transformation operators must be unique")
        if self.max_plans_per_parent > self.max_plans:
            raise ValueError("per-parent plan budget exceeds total plan budget")
        return self


class SelectionPolicyV1(StrictModel):
    top_k: Annotated[int, Field(ge=1, le=32)] = 5
    max_per_strict_structure_group: Literal[1] = 1
    max_per_parent_family: Annotated[int, Field(ge=1, le=8)] = 2
    min_mechanisms_when_available: Annotated[int, Field(ge=1, le=8)] = 2

    structure_distance_weight: Score = 0.40
    composition_distance_weight: Score = 0.20
    route_distance_weight: Score = 0.20
    mechanism_distance_weight: Score = 0.20

    quality_weight: Score = 0.60
    coverage_weight: Score = 0.15
    redundancy_weight: Score = 0.25

    @model_validator(mode="after")
    def validate_weights(self) -> SelectionPolicyV1:
        distance_sum = (
            self.structure_distance_weight
            + self.composition_distance_weight
            + self.route_distance_weight
            + self.mechanism_distance_weight
        )
        objective_sum = (
            self.quality_weight + self.coverage_weight + self.redundancy_weight
        )
        if abs(distance_sum - 1.0) > 1e-9:
            raise ValueError("candidate distance weights must sum to one")
        if abs(objective_sum - 1.0) > 1e-9:
            raise ValueError("selection objective weights must sum to one")
        if self.min_mechanisms_when_available > self.top_k:
            raise ValueError("mechanism coverage quota exceeds top_k")
        return self


class RuntimeBudgetV1(StrictModel):
    max_walltime_seconds: Annotated[int, Field(ge=1, le=86_400)] = 900


class InspirationPolicyV1(StrictModel):
    schema_version: Literal["inspiration-policy-v1"] = INSPIRATION_POLICY_VERSION
    policy_id: Identifier = "inspiration-default-v1"
    search_mode: SearchExecutionMode = SearchExecutionMode.OFFLINE_FIXTURE
    network_access: bool = False
    search: SearchBudgetV1 = Field(default_factory=SearchBudgetV1)
    fetch: FetchBudgetV1 = Field(default_factory=FetchBudgetV1)
    passages: PassageBudgetV1 = Field(default_factory=PassageBudgetV1)
    embedding: EmbeddingBudgetV1 = Field(default_factory=EmbeddingBudgetV1)
    llm: LLMBudgetV1 = Field(default_factory=LLMBudgetV1)
    bridge: BridgeSearchPolicyV1 = Field(default_factory=BridgeSearchPolicyV1)
    transformation: TransformationBudgetV1 = Field(
        default_factory=TransformationBudgetV1
    )
    selection: SelectionPolicyV1 = Field(default_factory=SelectionPolicyV1)
    runtime: RuntimeBudgetV1 = Field(default_factory=RuntimeBudgetV1)

    @model_validator(mode="after")
    def validate_cross_budget_limits(self) -> InspirationPolicyV1:
        if self.search_mode is SearchExecutionMode.OFFLINE_FIXTURE:
            if self.network_access:
                raise ValueError("offline fixture mode cannot enable network access")
        elif not self.network_access:
            raise ValueError("public metadata API mode requires network access")
        if self.fetch.max_requests > self.search.max_unique_documents:
            raise ValueError("fetch requests exceed unique-document budget")
        if self.embedding.max_passages > self.passages.max_total:
            raise ValueError("embedding passage budget exceeds extracted-passage budget")
        if self.selection.top_k > self.transformation.max_plans:
            raise ValueError("top_k exceeds transformation plan budget")
        return self


PUBLIC_POLICY_MODELS: tuple[type[StrictModel], ...] = (
    SearchBudgetV1,
    FetchBudgetV1,
    PassageBudgetV1,
    EmbeddingBudgetV1,
    LLMBudgetV1,
    BridgeSearchPolicyV1,
    TransformationBudgetV1,
    SelectionPolicyV1,
    RuntimeBudgetV1,
    InspirationPolicyV1,
)
