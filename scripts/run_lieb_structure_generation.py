#!/usr/bin/env python3
"""Run an exact Lieb STRUCTURE_GENERATION backend protocol diagnostic.

This runner deliberately does not summarize, truncate, or scientifically extend
the prompt.  It dispatches the same generic-research service used by Hermes, but
does not constitute a Hermes host receipt. DeepSeek remains the only source of
material families, operators, parameters, and conclusions.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

from pydantic import ValidationError

from material_agent.integration.generic_research import (
    GENERIC_RESEARCH_TOOL_NAME,
    GenericMaterialsResearchService,
    GenericResearchRunRequestV1,
)
from material_agent.integration.research_pipeline_mcp import (
    ResearchPipelineDispatcher,
    ResearchPipelineDispatchError,
)

DEFAULT_PROMPT = Path("docs/prompts/lieb-fractional-valence-flat-band-v1.md")


def exact_structure_generation_goal(prompt: str) -> str:
    """Select the three shared sections and exact STRUCTURE_GENERATION bullet."""

    shared_start = prompt.index("## Shared scientific goal")
    required_start = prompt.index("## Required workflow")
    acceptance_start = prompt.index("## Acceptance and output")
    overlays_start = prompt.index("## Parallel route overlays")
    overlay_start = prompt.index("- `STRUCTURE_GENERATION`:", overlays_start)
    overlay_end = prompt.index("\n\n", overlay_start)
    return (
        prompt[shared_start:required_start]
        + prompt[required_start:acceptance_start]
        + prompt[acceptance_start:overlays_start].rstrip()
        + "\n\n"
        + "## STRUCTURE_GENERATION overlay\n\n"
        + prompt[overlay_start:overlay_end]
        + "\n"
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--prompt", type=Path, default=DEFAULT_PROMPT)
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--project", required=True)
    parser.add_argument("--submission-id")
    args = parser.parse_args()

    goal = exact_structure_generation_goal(args.prompt.read_text())
    goal_bytes = goal.encode("utf-8")
    goal_sha256 = hashlib.sha256(goal_bytes).hexdigest()
    submission_id = args.submission_id or (
        f"lieb-structure-{datetime.now(UTC):%Y%m%dT%H%M%S%fZ}"
    )
    service = GenericMaterialsResearchService(
        workspace=args.workspace,
        project_id=args.project,
    )
    goal_ref = service.store.write_bytes(
        "lieb_structure_generation/inputs/exact-goal.md",
        goal_bytes,
        "text/markdown",
        immutable=True,
    )
    arguments = {
        "schema_version": "materials-generic-research-run-v1",
        "submission_id": submission_id,
        "goal": goal,
        "publication_year_from": 1960,
        "publication_year_to": 2026,
        "reasoning_effort": "max",
        "max_native_search_calls": 16,
        "max_authoritative_search_calls": 24,
        "max_agent_rounds_per_role": 30,
    }
    dispatcher = ResearchPipelineDispatcher(object(), service)  # type: ignore[arg-type]
    try:
        result = dispatcher.dispatch(GENERIC_RESEARCH_TOOL_NAME, arguments)
    except ResearchPipelineDispatchError as exc:
        validation_errors: list[dict[str, object]] = []
        try:
            GenericResearchRunRequestV1.model_validate(arguments)
        except ValidationError as validation_exc:
            validation_errors = validation_exc.errors(
                include_url=False,
                include_context=True,
                include_input=False,
            )
        failure_ref = service.store.write_json(
            "lieb_structure_generation/failure-receipt.json",
            {
                "status": "CAPABILITY_GAP",
                "stage": "HERMES_GENERIC_REQUEST_SCHEMA",
                "tool_name": GENERIC_RESEARCH_TOOL_NAME,
                "submission_id": submission_id,
                "project_id": args.project,
                "execution_mode": "ML_DATABASE_LITERATURE_ONLY",
                "dft_requested": False,
                "deepseek_started": False,
                "dispatcher_error": str(exc),
                "validation_errors": validation_errors,
                "goal_artifact_uri": goal_ref.uri,
                "goal_sha256": goal_sha256,
                "goal_characters": len(goal),
                "goal_bytes": len(goal_bytes),
                "requested_budget": {
                    "reasoning_effort": "max",
                    "max_native_search_calls": 16,
                    "max_authoritative_search_calls": 24,
                    "max_agent_rounds_per_role": 30,
                },
                "scientific_candidates": [],
                "operator_results": [],
                "scientific_conclusion": False,
            },
            immutable=True,
        )
        print(
            json.dumps(
                {
                    "status": "CAPABILITY_GAP",
                    "stage": "HERMES_GENERIC_REQUEST_SCHEMA",
                    "failure_artifact_uri": failure_ref.uri,
                    "goal_artifact_uri": goal_ref.uri,
                    "goal_sha256": goal_sha256,
                    "goal_characters": len(goal),
                    "project_root": str(service.project_root),
                    "deepseek_started": False,
                },
                indent=2,
                sort_keys=True,
            )
        )
        return 2

    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
