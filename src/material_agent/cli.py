"""Command-line entry point for the P0 Orchestrator and standalone Agent 01."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

from material_agent.orchestrator.parser import requirement_parser_from_environment
from material_agent.orchestrator.runtime import OrchestratorRuntime
from material_agent.retrieval.adapters import (
    C2dbAdapter,
    InMemoryMaterialsAdapter,
    MaterialsProjectAdapter,
    Mc3dAdapter,
    NimsSuperconAdapter,
    NomadAdapter,
    TopologicalQuantumChemistryAdapter,
)
from material_agent.retrieval.models import (
    Requirement,
    RetrievalStageInput,
    SourceDatabase,
    StageStatus,
)
from material_agent.retrieval.query import (
    AUTO_SOURCE,
    retrieval_policy_for_source,
    select_retrieval_source,
)
from material_agent.retrieval.runner import RetrievalStageRunner
from material_agent.retrieval.storage import (
    LocalArtifactStore,
    canonical_json_bytes,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="material-agent")
    subparsers = parser.add_subparsers(dest="command", required=True)

    project = subparsers.add_parser("project", help="manage projects")
    project_commands = project.add_subparsers(
        dest="project_command", required=True
    )
    project_create = project_commands.add_parser("create")
    project_create.add_argument("--workspace", type=Path, default=Path("workspace"))
    project_create.add_argument("--project-id")

    requirement = subparsers.add_parser(
        "requirement", help="parse a natural-language Requirement draft"
    )
    requirement_commands = requirement.add_subparsers(
        dest="requirement_command", required=True
    )
    requirement_parse = requirement_commands.add_parser("parse")
    requirement_parse.add_argument("--request", required=True)
    requirement_parse.add_argument("--output", required=True, type=Path)
    requirement_parse.add_argument("--requirement-id")

    run = subparsers.add_parser("run", help="start an Orchestrator P0 run")
    _add_project_arguments(run)
    run_input = run.add_mutually_exclusive_group(required=True)
    run_input.add_argument("--request")
    run_input.add_argument("--requirement-file", type=Path)
    run.add_argument("--fixture", type=Path)
    run.add_argument(
        "--source",
        choices=(*tuple(source.value for source in SourceDatabase), AUTO_SOURCE),
        default=SourceDatabase.MATERIALS_PROJECT.value,
    )
    run.add_argument("--run-id")
    run.add_argument(
        "--mp-report-heavy-limit", type=int, default=None,
        help="Materials Project heavy report assets for top N published candidates (0-200)",
    )

    run_stage = subparsers.add_parser(
        "run-stage", help="start one stage from explicit immutable inputs"
    )
    run_stage.add_argument(
        "stage", choices=("retrieval", "ml", "dft", "many_body")
    )
    _add_project_arguments(run_stage)
    run_stage.add_argument("--input", required=True, type=Path)
    run_stage.add_argument("--run-id")

    status = subparsers.add_parser("status", help="show durable run state")
    _add_project_arguments(status)
    status.add_argument("--run", required=True, dest="run_id")

    respond = subparsers.add_parser(
        "respond", help="answer a clarification or other interaction"
    )
    _add_project_arguments(respond)
    respond.add_argument("--run", required=True, dest="run_id")
    respond.add_argument("--interaction", required=True)
    respond_input = respond.add_mutually_exclusive_group(required=True)
    respond_input.add_argument("--json", dest="response_json")
    respond_input.add_argument("--text", dest="response_text")

    approve = subparsers.add_parser(
        "approve", help="approve or reject a pending Gate"
    )
    _add_project_arguments(approve)
    approve.add_argument("--run", required=True, dest="run_id")
    approve.add_argument("--approval", required=True)
    approve.add_argument(
        "--decision", required=True, choices=("approve", "reject")
    )
    approve.add_argument("--reason")

    resume = subparsers.add_parser(
        "resume", help="continue a non-interrupted checkpoint"
    )
    _add_project_arguments(resume)
    resume.add_argument("--run", required=True, dest="run_id")

    retry = subparsers.add_parser(
        "retry", help="approve retry after a retryable stage failure"
    )
    _add_project_arguments(retry)
    retry.add_argument("--run", required=True, dest="run_id")

    cancel = subparsers.add_parser(
        "cancel", help="cancel a run waiting for user input"
    )
    _add_project_arguments(cancel)
    cancel.add_argument("--run", required=True, dest="run_id")
    cancel.add_argument("--reason")

    report = subparsers.add_parser("report", help="print the final run report")
    _add_project_arguments(report)
    report.add_argument("--run", required=True, dest="run_id")

    retrieval = subparsers.add_parser(
        "retrieval", help="run Agent 01 deterministic material retrieval"
    )
    retrieval.add_argument("--requirement", required=True, type=Path)
    retrieval.add_argument("--output", required=True, type=Path)
    retrieval.add_argument("--project-id", default="project-demo")
    retrieval.add_argument("--run-id", default="run-agent01-demo")
    retrieval.add_argument("--fixture", type=Path)
    retrieval.add_argument(
        "--source",
        choices=(*tuple(source.value for source in SourceDatabase), AUTO_SOURCE),
        default=SourceDatabase.MATERIALS_PROJECT.value,
    )
    retrieval.add_argument("--mp-report-heavy-limit", type=int, default=None)
    arguments = parser.parse_args(argv)

    try:
        if arguments.command == "project":
            return _project_command(arguments)
        if arguments.command == "requirement":
            return _requirement_command(arguments)
        if arguments.command == "run":
            return _start_orchestrator_run(arguments)
        if arguments.command == "run-stage":
            return _start_stage_run(arguments)
        if arguments.command == "status":
            return _runtime_view_command(arguments, "status")
        if arguments.command == "respond":
            return _runtime_view_command(arguments, "respond")
        if arguments.command == "approve":
            return _runtime_view_command(arguments, "approve")
        if arguments.command == "resume":
            return _runtime_view_command(arguments, "resume")
        if arguments.command == "retry":
            return _runtime_view_command(arguments, "retry")
        if arguments.command == "cancel":
            return _runtime_view_command(arguments, "cancel")
        if arguments.command == "report":
            return _report_command(arguments)
        if arguments.command == "retrieval":
            return _run_retrieval(arguments)
    except (FileNotFoundError, KeyError, RuntimeError, ValueError) as exc:
        print(
            json.dumps(
                {
                    "error": type(exc).__name__,
                    "message": str(exc),
                },
                ensure_ascii=False,
            ),
            file=sys.stderr,
        )
        return 1
    return 2


def _add_project_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--workspace", type=Path, default=Path("workspace"))
    parser.add_argument("--project", required=True, dest="project_id")


def _project_command(arguments: argparse.Namespace) -> int:
    if arguments.project_command != "create":
        return 2
    project = OrchestratorRuntime.create_project(
        arguments.workspace, arguments.project_id
    )
    print(json.dumps(project, ensure_ascii=False, indent=2))
    return 0


def _requirement_command(arguments: argparse.Namespace) -> int:
    if arguments.requirement_command != "parse":
        return 2
    raw_request = arguments.request.strip()
    if not raw_request:
        raise ValueError("request cannot be empty")
    requirement_id = arguments.requirement_id or (
        "req-" + hashlib.sha256(raw_request.encode("utf-8")).hexdigest()[:16]
    )
    parsed = requirement_parser_from_environment().parse(
        raw_request, requirement_id
    )
    requirement = Requirement.model_validate(parsed.requirement)
    if requirement.confirmed_by_user:
        raise ValueError("parsed Requirement draft cannot be pre-confirmed")
    payload = requirement.model_dump(mode="json")
    output_path = arguments.output.resolve()
    content = canonical_json_bytes(payload)
    _write_immutable_file(output_path, content)
    print(
        json.dumps(
            {
                "status": (
                    "CLARIFICATION_REQUIRED"
                    if parsed.clarification_questions
                    else "REVIEW_REQUIRED"
                ),
                "requirement_file": str(output_path),
                "requirement_sha256": hashlib.sha256(content).hexdigest(),
                "confirmed_by_user": False,
                "clarification_questions": parsed.clarification_questions,
                "parser": parsed.parser_name,
                "parser_version": parsed.parser_version,
                "llm_audit": (
                    parsed.llm_audit.model_dump(mode="json")
                    if parsed.llm_audit is not None
                    else None
                ),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


def _start_orchestrator_run(arguments: argparse.Namespace) -> int:
    requirement = (
        _read_json_file(arguments.requirement_file)
        if arguments.requirement_file
        else None
    )
    fixture = (
        _read_json_file(arguments.fixture) if arguments.fixture else None
    )
    request = arguments.request or "structured Requirement input"
    with OrchestratorRuntime.from_workspace(
        arguments.workspace, arguments.project_id
    ) as runtime:
        view = runtime.start_run(
            raw_request=request,
            initial_requirement=requirement,
            fixture_payload=fixture,
            run_id=arguments.run_id,
            retrieval_source=arguments.source,
            mp_report_heavy_limit=arguments.mp_report_heavy_limit,
        )
    _print_model(view)
    return 0


def _start_stage_run(arguments: argparse.Namespace) -> int:
    stage_input = _read_json_file(arguments.input)
    with OrchestratorRuntime.from_workspace(
        arguments.workspace, arguments.project_id
    ) as runtime:
        view = runtime.start_stage_run(
            stage=arguments.stage,
            stage_input=stage_input,
            run_id=arguments.run_id,
        )
    _print_model(view)
    return 0


def _runtime_view_command(
    arguments: argparse.Namespace, operation: str
) -> int:
    with OrchestratorRuntime.from_workspace(
        arguments.workspace, arguments.project_id
    ) as runtime:
        if operation == "status":
            view = runtime.status(arguments.run_id)
        elif operation == "respond":
            if arguments.response_text is not None:
                pending = [
                    item
                    for item in runtime.status(arguments.run_id).interrupts
                    if item.interaction_id == arguments.interaction
                ]
                if (
                    len(pending) != 1
                    or pending[0].value.get("interaction_type")
                    != "CLARIFICATION"
                ):
                    raise ValueError(
                        "--text is only valid for a pending CLARIFICATION"
                    )
                response = {"answer": arguments.response_text}
            else:
                response = _parse_json_argument(arguments.response_json)
            view = runtime.respond(
                run_id=arguments.run_id,
                interaction_id=arguments.interaction,
                response=response,
            )
        elif operation == "approve":
            view = runtime.approve(
                run_id=arguments.run_id,
                approval_id=arguments.approval,
                decision=arguments.decision,
                reason=arguments.reason,
            )
        elif operation == "resume":
            view = runtime.resume(run_id=arguments.run_id)
        elif operation == "retry":
            view = runtime.retry(run_id=arguments.run_id)
        elif operation == "cancel":
            view = runtime.cancel(
                run_id=arguments.run_id, reason=arguments.reason
            )
        else:  # pragma: no cover
            raise ValueError(f"unsupported runtime operation: {operation}")
    _print_model(view)
    return 0


def _report_command(arguments: argparse.Namespace) -> int:
    with OrchestratorRuntime.from_workspace(
        arguments.workspace, arguments.project_id
    ) as runtime:
        report = runtime.read_report(arguments.run_id)
    print(report, end="" if report.endswith("\n") else "\n")
    return 0


def _read_json_file(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"JSON file must contain an object: {path}")
    return payload


def _parse_json_argument(value: str) -> dict[str, Any]:
    if value.startswith("@"):
        return _read_json_file(Path(value[1:]))
    payload = json.loads(value)
    if not isinstance(payload, dict):
        raise ValueError("--json must contain a JSON object")
    return payload


def _write_immutable_file(path: Path, content: bytes) -> None:
    if path.exists():
        if path.is_file() and path.read_bytes() == content:
            return
        raise ValueError(f"refusing to overwrite existing output: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with path.open("xb") as output:
            output.write(content)
    except FileExistsError as exc:
        raise ValueError(f"refusing to overwrite existing output: {path}") from exc


def _print_model(value: Any) -> None:
    print(
        json.dumps(
            value.model_dump(mode="json"),
            ensure_ascii=False,
            indent=2,
        )
    )


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
    store = LocalArtifactStore(arguments.output)
    requirement_ref = store.write_bytes(
        f"requirements/requirement.v{requirement.revision}.json",
        requirement_bytes,
        "application/json",
        immutable=True,
    )
    source = select_retrieval_source(arguments.source, requirement)
    if arguments.fixture:
        fixture_payload = json.loads(arguments.fixture.read_text(encoding="utf-8"))
        adapter = InMemoryMaterialsAdapter(
            fixture_payload["documents"],
            database_version=fixture_payload.get(
                "database_version", "fixture-2026-07-25"
            ),
            task_metadata=fixture_payload.get("task_metadata", {}),
            source_database=source,
        )
    else:
        adapter = {
            SourceDatabase.MATERIALS_PROJECT: MaterialsProjectAdapter,
            SourceDatabase.NOMAD: NomadAdapter,
            SourceDatabase.MC3D: Mc3dAdapter,
            SourceDatabase.C2DB: C2dbAdapter,
            SourceDatabase.TOPOLOGICAL_QUANTUM_CHEMISTRY: (
                TopologicalQuantumChemistryAdapter
            ),
            SourceDatabase.NIMS_SUPERCON: NimsSuperconAdapter,
        }[source]()

    policy = retrieval_policy_for_source(
        source,
        mp_report_heavy_limit=arguments.mp_report_heavy_limit,
    )
    stage_input = RetrievalStageInput(
        project_id=arguments.project_id,
        run_id=arguments.run_id,
        requirement_revision=requirement.revision,
        requirement_artifact_uri=requirement_ref.uri,
        requirement_hash=requirement_ref.sha256,
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
