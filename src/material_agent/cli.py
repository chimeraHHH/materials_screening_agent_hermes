"""Command-line entry point for the standalone Agent 01 stage."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

from material_agent.retrieval.adapters import (
    InMemoryMaterialsAdapter,
    MaterialsProjectAdapter,
)
from material_agent.retrieval.models import (
    Requirement,
    RetrievalPolicy,
    RetrievalStageInput,
    StageStatus,
)
from material_agent.retrieval.runner import RetrievalStageRunner
from material_agent.retrieval.storage import LocalArtifactStore


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="material-agent")
    subparsers = parser.add_subparsers(dest="command", required=True)
    retrieval = subparsers.add_parser(
        "retrieval", help="run Agent 01 deterministic material retrieval"
    )
    retrieval.add_argument("--requirement", required=True, type=Path)
    retrieval.add_argument("--output", required=True, type=Path)
    retrieval.add_argument("--project-id", default="project-demo")
    retrieval.add_argument("--run-id", default="run-agent01-demo")
    retrieval.add_argument("--fixture", type=Path)
    arguments = parser.parse_args(argv)

    if arguments.command == "retrieval":
        return _run_retrieval(arguments)
    return 2


def _run_retrieval(arguments: argparse.Namespace) -> int:
    requirement_payload = json.loads(
        arguments.requirement.read_text(encoding="utf-8")
    )
    requirement = Requirement.model_validate(requirement_payload)
    requirement_bytes = json.dumps(
        requirement.model_dump(mode="json"),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    requirement_hash = hashlib.sha256(requirement_bytes).hexdigest()

    store = LocalArtifactStore(arguments.output)
    requirement_ref = store.write_bytes(
        f"requirements/requirement.v{requirement.revision}.json",
        requirement_bytes,
        "application/json",
    )
    if arguments.fixture:
        fixture_payload = json.loads(arguments.fixture.read_text(encoding="utf-8"))
        adapter = InMemoryMaterialsAdapter(
            fixture_payload["documents"],
            database_version=fixture_payload.get(
                "database_version", "fixture-2026-07-25"
            ),
            task_metadata=fixture_payload.get("task_metadata", {}),
        )
    else:
        adapter = MaterialsProjectAdapter()

    policy = RetrievalPolicy()
    stage_input = RetrievalStageInput(
        project_id=arguments.project_id,
        run_id=arguments.run_id,
        requirement_revision=requirement.revision,
        requirement_artifact_uri=requirement_ref.uri,
        requirement_hash=requirement_hash,
        retrieval_policy_version=policy.policy_version,
        confirmed_by_user=requirement.confirmed_by_user,
    )
    runner = RetrievalStageRunner(
        adapter=adapter,
        artifact_store=store,
        policy=policy,
    )
    result = runner.run(requirement, stage_input)
    print(json.dumps(result.model_dump(mode="json"), ensure_ascii=False, indent=2))
    return (
        0
        if result.status in {StageStatus.SUCCEEDED, StageStatus.PARTIAL}
        else 1
    )


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())

