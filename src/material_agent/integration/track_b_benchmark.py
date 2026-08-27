"""Scientific Track-B benchmark budget contracts; no engineering Track C."""

from __future__ import annotations

from typing import Literal

from pydantic import Field, model_validator

from material_agent.inspiration.deepseek_agent import DeepSeekAgentBudgetV1
from material_agent.inspiration.models import StrictModel
from material_agent.inspiration.research_graph import MaterialsResearchGraphResultV7

TRACK_B_CALIBRATION_PROFILE_ID = "track-b-calibration-600k-v1"
TRACK_B_TOTAL_TOKEN_CEILING = 600_000
TRACK_B_MAX_NATIVE_SEARCH_CALLS = 4
TRACK_B_MAX_AUTHORITATIVE_SEARCH_CALLS = 6
TRACK_B_PUBLIC_SEARCH_MAX_RESULTS = 5
TRACK_B_MAX_CONTRACT_REPAIRS = 1

_ROLE_TOTAL_TOKEN_CEILINGS = {
    "requirements_analyst": 25_000,
    "query_strategist": 25_000,
    "native_search_scout": 100_000,
    "evidence_researcher": 200_000,
    "database_scout": 40_000,
    "mechanism_chemist": 60_000,
    "skeptic": 50_000,
    "hypothesis_reasoner": 50_000,
    "synthesist": 20_000,
    "contract_repair": 25_000,
}

_ROLE_MAX_COMPLETION_TOKENS_PER_ROUND = {
    "requirements_analyst": 6_000,
    "query_strategist": 6_000,
    "native_search_scout": 8_000,
    "evidence_researcher": 10_000,
    "database_scout": 7_000,
    "mechanism_chemist": 10_000,
    "skeptic": 8_000,
    "hypothesis_reasoner": 10_000,
    "synthesist": 6_000,
    "contract_repair": 5_000,
}

_ROLE_MAX_ROUNDS = {
    "requirements_analyst": 3,
    "query_strategist": 3,
    "native_search_scout": 5,
    "evidence_researcher": 8,
    "database_scout": 4,
    "mechanism_chemist": 4,
    "skeptic": 4,
    "hypothesis_reasoner": 4,
    "synthesist": 3,
    "contract_repair": 3,
}

_ROLE_MAX_TOOL_CALLS = {
    "requirements_analyst": 1,
    "query_strategist": 1,
    "native_search_scout": 12,
    "evidence_researcher": 16,
    "database_scout": 12,
    "mechanism_chemist": 4,
    "skeptic": 4,
    "hypothesis_reasoner": 1,
    "synthesist": 1,
    "contract_repair": 1,
}


def track_b_calibration_role_budget(
    *,
    role: str,
    requested_rounds: int,
    native_search_calls: int,
    authoritative_calls: int,
) -> DeepSeekAgentBudgetV1:
    """Return a role budget whose graph plus at most one repair is <600k."""

    del requested_rounds
    if role not in _ROLE_TOTAL_TOKEN_CEILINGS:
        raise ValueError(f"unknown Track-B benchmark role: {role}")
    if native_search_calls > TRACK_B_MAX_NATIVE_SEARCH_CALLS:
        raise ValueError("Track-B calibration native-search budget drifted")
    if authoritative_calls > TRACK_B_MAX_AUTHORITATIVE_SEARCH_CALLS:
        raise ValueError("Track-B calibration authoritative budget drifted")
    token_ceiling = _ROLE_TOTAL_TOKEN_CEILINGS[role]
    return DeepSeekAgentBudgetV1(
        max_rounds=_ROLE_MAX_ROUNDS[role],
        max_tool_calls=_ROLE_MAX_TOOL_CALLS[role],
        max_tool_result_bytes=128_000,
        max_total_tool_result_bytes=(
            512_000 if role in {"native_search_scout", "evidence_researcher"} else 256_000
        ),
        max_final_response_bytes=256_000,
        max_completion_tokens_per_round=_ROLE_MAX_COMPLETION_TOKENS_PER_ROUND[role],
        max_total_tokens=token_ceiling,
        max_walltime_seconds=600,
    )


def track_b_worst_case_token_envelope() -> int:
    """Main roles once plus the benchmark maximum of one repair conversation."""

    main = sum(
        ceiling
        for role, ceiling in _ROLE_TOTAL_TOKEN_CEILINGS.items()
        if role != "contract_repair"
    )
    return main + TRACK_B_MAX_CONTRACT_REPAIRS * _ROLE_TOTAL_TOKEN_CEILINGS[
        "contract_repair"
    ]


class TrackBBudgetReceiptAuditV1(StrictModel):
    schema_version: Literal["track-b-budget-receipt-audit-v1"] = (
        "track-b-budget-receipt-audit-v1"
    )
    execution_profile_id: Literal["track-b-calibration-600k-v1"] = (
        TRACK_B_CALIBRATION_PROFILE_ID
    )
    total_tokens: int = Field(ge=0)
    main_role_tokens: int = Field(ge=0)
    repair_tokens: int = Field(ge=0)
    role_count: int = Field(ge=9, le=9)
    repair_count: int = Field(ge=0, le=TRACK_B_MAX_CONTRACT_REPAIRS)
    common_token_ceiling: Literal[600000] = TRACK_B_TOTAL_TOKEN_CEILING
    within_common_ceiling: bool
    confirmatory_benchmark_result: Literal[False] = False
    gold_scoring_complete: Literal[False] = False
    scientific_conclusion: Literal[False] = False

    @model_validator(mode="after")
    def validate_totals(self) -> TrackBBudgetReceiptAuditV1:
        if self.total_tokens != self.main_role_tokens + self.repair_tokens:
            raise ValueError("Track-B receipt token totals do not add up")
        if self.within_common_ceiling != (
            self.total_tokens <= self.common_token_ceiling
        ):
            raise ValueError("Track-B budget verdict differs from receipt total")
        return self


def audit_track_b_graph_tokens(
    graph: MaterialsResearchGraphResultV7,
) -> TrackBBudgetReceiptAuditV1:
    """Sum provider receipts without treating a budget pass as a quality result."""

    role_tokens = tuple(item.receipt.total_tokens for item in graph.roles)
    repair_tokens = tuple(item.receipt.total_tokens for item in graph.repairs)
    if any(value is None for value in (*role_tokens, *repair_tokens)):
        raise ValueError("Track-B benchmark requires provider token receipts")
    main = sum(value for value in role_tokens if value is not None)
    repairs = sum(value for value in repair_tokens if value is not None)
    return TrackBBudgetReceiptAuditV1(
        total_tokens=main + repairs,
        main_role_tokens=main,
        repair_tokens=repairs,
        role_count=len(graph.roles),
        repair_count=len(graph.repairs),
        within_common_ceiling=main + repairs <= TRACK_B_TOTAL_TOKEN_CEILING,
    )


__all__ = [
    "TRACK_B_CALIBRATION_PROFILE_ID",
    "TRACK_B_MAX_AUTHORITATIVE_SEARCH_CALLS",
    "TRACK_B_MAX_CONTRACT_REPAIRS",
    "TRACK_B_MAX_NATIVE_SEARCH_CALLS",
    "TRACK_B_PUBLIC_SEARCH_MAX_RESULTS",
    "TRACK_B_TOTAL_TOKEN_CEILING",
    "TrackBBudgetReceiptAuditV1",
    "audit_track_b_graph_tokens",
    "track_b_calibration_role_budget",
    "track_b_worst_case_token_envelope",
]
