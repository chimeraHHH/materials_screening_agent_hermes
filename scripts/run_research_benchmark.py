"""Evaluate a frozen materials-research benchmark and enforce release gates."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from material_agent.inspiration.research_benchmark import (
    ResearchBenchmarkGatePolicyV1,
    ResearchBenchmarkGoldSetV1,
    ResearchBenchmarkPredictionV1,
    evaluate_research_benchmark,
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("gold", type=Path)
    parser.add_argument("predictions", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument(
        "--allow-synthetic",
        action="store_true",
        help="Run engineering gates without claiming production release readiness.",
    )
    args = parser.parse_args()
    gold = ResearchBenchmarkGoldSetV1.model_validate_json(
        args.gold.read_text("utf-8")
    )
    raw_predictions = json.loads(args.predictions.read_text("utf-8"))
    if not isinstance(raw_predictions, list):
        raise TypeError("predictions file must contain a JSON array")
    predictions = tuple(
        ResearchBenchmarkPredictionV1.model_validate(item)
        for item in raw_predictions
    )
    result = evaluate_research_benchmark(
        gold,
        predictions,
        policy=ResearchBenchmarkGatePolicyV1(
            require_adjudicated=not args.allow_synthetic
        ),
    )
    rendered = json.dumps(
        result.model_dump(mode="json"), ensure_ascii=False, indent=2, sort_keys=True
    )
    if args.output is not None:
        args.output.write_text(rendered + "\n", "utf-8")
    print(rendered)
    return 0 if result.release_status == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
