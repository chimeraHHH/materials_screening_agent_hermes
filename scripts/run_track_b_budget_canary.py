#!/usr/bin/env python3
"""Run one real Track-B development-only budget canary.

The canary measures provider-reported tokens after context compression.  It is
not a quality comparison and never counts as a locked or confirmatory result.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from material_agent.inspiration.models import canonical_json_bytes
from material_agent.inspiration.search import PUBLIC_SEARCH_MAX_RESULTS_ENV
from material_agent.integration.generic_research import (
    GENERIC_RESEARCH_IMPLEMENTATION_REVISION,
    GenericMaterialsResearchService,
    GenericResearchRunRequestV1,
)
from material_agent.integration.track_b_benchmark import (
    TRACK_B_CALIBRATION_PROFILE_ID,
    TRACK_B_MAX_AUTHORITATIVE_SEARCH_CALLS,
    TRACK_B_MAX_CONTRACT_REPAIRS,
    TRACK_B_MAX_NATIVE_SEARCH_CALLS,
    TRACK_B_PUBLIC_SEARCH_MAX_RESULTS,
    TRACK_B_TOTAL_TOKEN_CEILING,
    audit_track_b_graph_tokens,
    track_b_calibration_role_budget,
    track_b_worst_case_token_envelope,
)

DEFAULT_PROMPT = Path("docs/prompts/lieb-fractional-valence-flat-band-v1.md")
OVERLAYS = (
    "FEDERATED_DATABASE",
    "LITERATURE_MECHANISM",
    "STRUCTURE_GENERATION",
)


def exact_overlay_goal(prompt: str, overlay: str) -> str:
    """Return all shared contract sections plus exactly one verbatim overlay."""

    if overlay not in OVERLAYS:
        raise ValueError(f"unsupported Track-B overlay: {overlay}")
    shared_start = prompt.index("## Shared scientific goal")
    required_start = prompt.index("## Required workflow")
    acceptance_start = prompt.index("## Acceptance and output")
    overlays_start = prompt.index("## Parallel route overlays")
    overlay_start = prompt.index(f"- `{overlay}`:", overlays_start)
    next_overlay = prompt.find("\n- `", overlay_start + 1)
    overlay_end = len(prompt) if next_overlay < 0 else next_overlay
    overlay_text = prompt[overlay_start:overlay_end].strip()
    return (
        prompt[shared_start:required_start]
        + prompt[required_start:acceptance_start]
        + prompt[acceptance_start:overlays_start].rstrip()
        + "\n\n"
        + f"## {overlay} overlay\n\n"
        + overlay_text
        + "\n"
    )


def _write_audit(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_bytes(canonical_json_bytes(payload) + b"\n")
    temporary.replace(path)


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()


def _completed_checkpoint_receipts(
    project_root: Path, run_id: str
) -> list[dict[str, Any]]:
    """Recover completed-role usage when a later role aborts the canary."""

    completed: list[dict[str, Any]] = []
    seen_conversations: set[tuple[str, ...]] = set()
    for path in sorted(
        project_root.glob(f"generic_research/{run_id}/roles/*.json")
    ):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            receipt = payload["agent_result"]["receipt"]
        except (KeyError, TypeError, ValueError, json.JSONDecodeError):
            continue
        conversation_key = tuple(receipt.get("request_sha256_by_round", [])) + tuple(
            receipt.get("response_sha256_by_round", [])
        )
        if conversation_key in seen_conversations:
            continue
        seen_conversations.add(conversation_key)
        completed.append(
            {
                "checkpoint": str(path.relative_to(project_root)),
                "prompt_version": receipt.get("prompt_version"),
                "rounds": receipt.get("rounds"),
                "tool_calls": len(receipt.get("tool_calls", [])),
                "prompt_tokens": receipt.get("prompt_tokens"),
                "completion_tokens": receipt.get("completion_tokens"),
                "reasoning_tokens": receipt.get("reasoning_tokens"),
                "total_tokens": receipt.get("total_tokens"),
            }
        )
    return completed


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--prompt", type=Path, default=DEFAULT_PROMPT)
    parser.add_argument("--overlay", choices=OVERLAYS, required=True)
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--project", required=True)
    parser.add_argument("--audit-output", type=Path, required=True)
    parser.add_argument("--submission-id")
    args = parser.parse_args()

    prompt_bytes = args.prompt.read_bytes()
    prompt = prompt_bytes.decode("utf-8")
    goal = exact_overlay_goal(prompt, args.overlay)
    submission_id = args.submission_id or (
        f"track-b-canary-{args.overlay.lower()}-"
        f"{datetime.now(UTC):%Y%m%dT%H%M%S%fZ}"
    )
    environment = dict(os.environ)
    environment[PUBLIC_SEARCH_MAX_RESULTS_ENV] = str(
        TRACK_B_PUBLIC_SEARCH_MAX_RESULTS
    )
    observed_provider_receipts: list[dict[str, Any]] = []

    def record_provider_receipt(role: str, receipt: Any) -> None:
        observed_provider_receipts.append(
            {
                "sequence": len(observed_provider_receipts) + 1,
                "role": role,
                "prompt_version": receipt.prompt_version,
                "rounds": receipt.rounds,
                "tool_calls": len(receipt.tool_calls),
                "prompt_tokens": receipt.prompt_tokens,
                "completion_tokens": receipt.completion_tokens,
                "reasoning_tokens": receipt.reasoning_tokens,
                "total_tokens": receipt.total_tokens,
            }
        )

    service = GenericMaterialsResearchService(
        workspace=args.workspace,
        project_id=args.project,
        environment=environment,
        role_budget_factory=track_b_calibration_role_budget,
        execution_profile_id=TRACK_B_CALIBRATION_PROFILE_ID,
        max_contract_repair_attempts=1,
        max_total_contract_repairs=TRACK_B_MAX_CONTRACT_REPAIRS,
        role_receipt_callback=record_provider_receipt,
    )
    request = GenericResearchRunRequestV1(
        submission_id=submission_id,
        goal=goal,
        publication_year_from=1960,
        publication_year_to=2026,
        reasoning_effort="high",
        max_native_search_calls=TRACK_B_MAX_NATIVE_SEARCH_CALLS,
        max_authoritative_search_calls=TRACK_B_MAX_AUTHORITATIVE_SEARCH_CALLS,
        max_agent_rounds_per_role=4,
    )
    _, expected_request_sha256, expected_run_id, _ = service._identity(request)  # noqa: SLF001
    started_at = _utc_now()
    audit: dict[str, Any] = {
        "schema_version": "track-b-budget-canary-audit-v1",
        "status": "STARTED",
        "started_at": started_at,
        "completed_at": None,
        "development_only": True,
        "confirmatory_benchmark_result": False,
        "gold_scoring_complete": False,
        "scientific_conclusion": False,
        "track_c_included": False,
        "execution_profile_id": TRACK_B_CALIBRATION_PROFILE_ID,
        "implementation_revision": GENERIC_RESEARCH_IMPLEMENTATION_REVISION,
        "common_token_ceiling": TRACK_B_TOTAL_TOKEN_CEILING,
        "declared_worst_case_role_envelope": track_b_worst_case_token_envelope(),
        "overlay": args.overlay,
        "source_prompt": str(args.prompt.resolve()),
        "source_prompt_sha256": hashlib.sha256(prompt_bytes).hexdigest(),
        "exact_goal_sha256": hashlib.sha256(goal.encode("utf-8")).hexdigest(),
        "request": request.model_dump(mode="json"),
        "expected_run_id": expected_run_id,
        "expected_request_sha256": expected_request_sha256,
        "public_search_max_results": TRACK_B_PUBLIC_SEARCH_MAX_RESULTS,
        "max_contract_repairs": TRACK_B_MAX_CONTRACT_REPAIRS,
        "project_root": str(service.project_root),
        "failure": None,
        "budget_receipt": None,
        "role_receipts": [],
        "observed_provider_receipts": [],
        "limitations": [
            "This run tests real provider-token budget compliance only.",
            "It has no expert gold and does not estimate CCRR or FEPR.",
            "It is not a Full-Loop versus No-Feedback comparison.",
        ],
    }
    _write_audit(args.audit_output, audit)

    try:
        result = service.run(request)
        budget_receipt = audit_track_b_graph_tokens(result.research_graph)
    except Exception as exc:  # noqa: BLE001 - terminal audit must survive any canary failure
        completed_receipts = _completed_checkpoint_receipts(
            service.project_root, expected_run_id
        )
        audit.update(
            {
                "status": "FAILED",
                "completed_at": _utc_now(),
                "failure": {
                    "exception_type": type(exc).__name__,
                    "message": str(exc)[:2_000],
                    "category": getattr(exc, "category", None),
                    "role": getattr(exc, "role", None),
                    "attempted_role_usage": getattr(exc, "usage", None),
                },
                "observed_provider_receipts": observed_provider_receipts,
                "observed_provider_receipt_total_tokens": sum(
                    item["total_tokens"]
                    for item in observed_provider_receipts
                    if isinstance(item["total_tokens"], int)
                ),
                "completed_checkpoint_receipts": completed_receipts,
                "completed_checkpoint_total_tokens": sum(
                    item["total_tokens"]
                    for item in completed_receipts
                    if isinstance(item["total_tokens"], int)
                ),
            }
        )
        _write_audit(args.audit_output, audit)
        print(json.dumps(audit, ensure_ascii=False, indent=2, sort_keys=True))
        return 2

    role_receipts = [
        {
            "role": item.role,
            "prompt_version": item.receipt.prompt_version,
            "rounds": item.receipt.rounds,
            "tool_calls": len(item.receipt.tool_calls),
            "prompt_tokens": item.receipt.prompt_tokens,
            "completion_tokens": item.receipt.completion_tokens,
            "reasoning_tokens": item.receipt.reasoning_tokens,
            "total_tokens": item.receipt.total_tokens,
        }
        for item in result.research_graph.roles
    ]
    repair_receipts = [
        {
            "target_role": item.target_role,
            "defect_code": item.defect_code,
            "attempt": item.attempt,
            "status": item.status,
            "rounds": item.receipt.rounds,
            "tool_calls": len(item.receipt.tool_calls),
            "prompt_tokens": item.receipt.prompt_tokens,
            "completion_tokens": item.receipt.completion_tokens,
            "reasoning_tokens": item.receipt.reasoning_tokens,
            "total_tokens": item.receipt.total_tokens,
        }
        for item in result.research_graph.repairs
    ]
    audit.update(
        {
            "status": "SUCCEEDED",
            "completed_at": _utc_now(),
            "run_id": result.run_id,
            "request_sha256": result.request_sha256,
            "research_graph_sha256": result.research_graph_sha256,
            "result_artifact_uri": result.result_artifact_uri,
            "report_artifact_uri": result.report_artifact_uri,
            "report_manifest_artifact_uri": result.report_manifest_artifact_uri,
            "budget_receipt": budget_receipt.model_dump(mode="json"),
            "role_receipts": role_receipts,
            "repair_receipts": repair_receipts,
            "observed_provider_receipts": observed_provider_receipts,
        }
    )
    _write_audit(args.audit_output, audit)
    print(json.dumps(audit, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if budget_receipt.within_common_ceiling else 3


if __name__ == "__main__":
    sys.exit(main())
