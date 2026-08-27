from __future__ import annotations

import base64
import hashlib
import json
import os
import sqlite3
import stat
import subprocess
import sys
from collections.abc import Mapping
from pathlib import Path

import pytest

from integrations.hermes.scripts.verify_completed_inspiration_run import (
    CompletedRunVerificationError,
    _validate_crossref_metadata_envelope,
)
from material_agent.gateway.authorization import RequirementFreezeGrantIssuer
from material_agent.gateway.mcp_server import (
    GatewayServerSettings,
    GatewayToolDispatcher,
)
from material_agent.gateway.models import (
    ApprovalInteractionV1,
    ApproveActionV1,
    InspirationBudgetV1,
    InspirationConstraintsV1,
    canonical_json_bytes,
)
from material_agent.gateway.persistence import SqliteGatewayRepository
from material_agent.inspiration.models import deterministic_id
from material_agent.inspiration.runner import MAX_SEARCH_RESPONSE_BYTES
from material_agent.inspiration.search import SearchAdapterError
from material_agent.integration.hermes_service import (
    HermesFixtureProjector,
    create_hermes_inspiration_service,
)

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
VERIFIER = (
    REPOSITORY_ROOT
    / "integrations"
    / "hermes"
    / "scripts"
    / "verify_completed_inspiration_run.py"
)


class _StaticCrossrefTransport:
    def __init__(self, payload: bytes) -> None:
        self.payload = payload
        self.calls: list[str] = []

    def get(
        self,
        url: str,
        *,
        headers: Mapping[str, str],
        timeout_seconds: float,
        max_response_bytes: int,
        deadline_monotonic: float | None = None,
        max_physical_requests: int | None = None,
    ) -> bytes:
        del headers, timeout_seconds, deadline_monotonic, max_physical_requests
        assert len(self.payload) <= max_response_bytes
        self.calls.append(url)
        return self.payload


def _response_bytes() -> bytes:
    abstract = (
        "Local resonance in an acoustic metamaterial produces a weakly "
        "dispersive mode because a resonator couples weakly to an extended "
        "lattice. The local resonance mechanism preserves spectral separation "
        "and suppresses dispersion, which can guide electronic flat band "
        "hypotheses when connectivity and equivalent site chemistry remain "
        "controlled. Strong hybridization breaks localization and broadens the "
        "mode, providing a falsification condition."
    )
    return json.dumps(
        {
            "message": {
                "items-per-page": 1,
                "items": [
                    {
                        "DOI": "10.5555/completed-verifier.1",
                        "URL": "https://doi.org/10.5555/completed-verifier.1",
                        "abstract": f"<jats:p>{abstract}</jats:p>",
                        "author": [{"family": "Verifier", "given": "ReadOnly"}],
                        "published": {"date-parts": [[2026]]},
                        "subject": ["Local resonance", "Electronic flat band"],
                        "title": ["Completed inspiration verifier fixture"],
                    }
                ],
            },
            "status": "ok",
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _arguments(submission_id: str) -> dict[str, object]:
    constraints = InspirationConstraintsV1(
        required_elements=("Se", "Ti"),
        excluded_elements=("Pb",),
        material_classes=("layered transition-metal dichalcogenide",),
        dimensionality="2D",
        target_features=("narrow electronic band",),
        top_k=1,
        require_diverse_routes=True,
        budget=InspirationBudgetV1(
            max_search_requests=8,
            max_unique_documents=4,
            max_passages=4,
            max_model_calls=0,
            max_walltime_seconds=300,
        ),
    )
    return {
        "submission_id": submission_id,
        "goal": "Find a reviewable narrow-band mechanism using bounded public metadata.",
        "constraints": constraints.model_dump(mode="json"),
    }


def _file_snapshot(root: Path) -> dict[str, tuple[int, int, int, int, int, str]]:
    root_metadata = root.lstat()
    snapshot = {
        ".": (
            root_metadata.st_mode,
            root_metadata.st_size,
            root_metadata.st_mtime_ns,
            root_metadata.st_ino,
            root_metadata.st_nlink,
            "",
        )
    }
    for directory, directory_names, file_names in os.walk(root, followlinks=False):
        directory_path = Path(directory)
        for name in directory_names:
            path = directory_path / name
            metadata = path.lstat()
            snapshot[path.relative_to(root).as_posix() + "/"] = (
                metadata.st_mode,
                metadata.st_size,
                metadata.st_mtime_ns,
                metadata.st_ino,
                metadata.st_nlink,
                "",
            )
        for name in file_names:
            path = directory_path / name
            metadata = path.lstat()
            payload_hash = (
                ""
                if stat.S_ISLNK(metadata.st_mode)
                else hashlib.sha256(path.read_bytes()).hexdigest()
            )
            snapshot[path.relative_to(root).as_posix()] = (
                metadata.st_mode,
                metadata.st_size,
                metadata.st_mtime_ns,
                metadata.st_ino,
                metadata.st_nlink,
                payload_hash,
            )
    return snapshot


def _build_workspace(
    tmp_path: Path,
    monkeypatch,
    *,
    complete: bool,
    include_binding: bool = False,
) -> tuple[Path, Path, list[str]] | tuple[Path, Path, list[str], dict[str, str]]:
    monkeypatch.delenv("MATERIALS_CROSSREF_CONTACT_EMAIL", raising=False)
    project_id = "completed-verifier"
    settings = GatewayServerSettings(tmp_path, project_id)
    transport = _StaticCrossrefTransport(_response_bytes())
    service = create_hermes_inspiration_service(
        settings,
        transport=transport,
        sleeper=lambda _seconds: None,
    )
    dispatcher = GatewayToolDispatcher(service)
    started = dispatcher.dispatch(
        "materials_inspiration_run",
        _arguments("completed-verifier-submission"),
    )
    run_id = started["run_id"]
    interaction = ApprovalInteractionV1.model_validate_json(
        json.dumps(started["state"]["interaction"])
    )
    record = service.repository.get_run(run_id)
    assert record is not None
    prepared = service.companion.preparer.prepare(
        run_id=record.run_id,
        request=record.request,
    )
    manifest_sha256 = service.companion.execution_manifest_sha256(
        request=record.request,
        prepared=prepared,
    )
    action = ApproveActionV1(
        interaction_id=interaction.interaction_id,
        confirmed_by_user=True,
    )
    interaction_sha256 = hashlib.sha256(canonical_json_bytes(interaction)).hexdigest()
    action_sha256 = hashlib.sha256(canonical_json_bytes(action)).hexdigest()

    if complete:
        RequirementFreezeGrantIssuer(
            repository=service.repository,
            grant_store=service.action_authorizer,
        ).grant_current(
            run_id=run_id,
            confirmation_reference="test:completed-verifier-user-confirmation",
            expected_execution_manifest_sha256=manifest_sha256,
        )
        terminal = dispatcher.dispatch(
            "materials_run_act",
            {
                "run_id": run_id,
                "action": action.model_dump(mode="json"),
            },
        )
        assert terminal["state"]["status"] in {"PARTIAL", "SUCCEEDED"}
        assert dispatcher.dispatch("materials_result_get", {"run_id": run_id})[
            "verified"
        ]
        assert len(transport.calls) == 4

    assert isinstance(service.repository, SqliteGatewayRepository)
    service.repository.close()
    project_root = tmp_path / project_id
    stage_root = project_root / "stages" / "inspiration" / run_id
    command = [
        sys.executable,
        str(VERIFIER),
        "--workspace",
        str(tmp_path),
        "--project",
        project_id,
        "--run-id",
        run_id,
        "--expected-request-sha256",
        record.request_sha256,
        "--expected-execution-manifest-sha256",
        manifest_sha256,
        "--expected-interaction-id",
        interaction.interaction_id,
        "--expected-interaction-sha256",
        interaction_sha256,
        "--expected-action-sha256",
        action_sha256,
        "--expected-confirmation-reference",
        "test:completed-verifier-user-confirmation",
    ]
    if include_binding:
        return (
            project_root,
            stage_root,
            command,
            {
                "confirmation_reference": "test:completed-verifier-user-confirmation",
                "interaction_id": interaction.interaction_id,
                "prompt": interaction.prompt,
                "request_sha256": record.request_sha256,
                "run_id": run_id,
            },
        )
    return project_root, stage_root, command


def _run(command: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        cwd=REPOSITORY_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )


def _canonical_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _read_json(path: Path) -> dict[str, object]:
    value = json.loads(path.read_bytes())
    assert isinstance(value, dict)
    return value


def _artifact_pointer(
    project_root: Path,
    path: Path,
    *,
    media_type: str,
) -> dict[str, object]:
    payload = path.read_bytes()
    return {
        "media_type": media_type,
        "sha256": hashlib.sha256(payload).hexdigest(),
        "size_bytes": len(payload),
        "uri": f"artifact://{path.relative_to(project_root).as_posix()}",
    }


def _replace_pointer(
    value: object,
    *,
    uri: str,
    replacement: dict[str, object],
) -> object:
    if isinstance(value, dict):
        if value.get("uri") == uri and "sha256" in value:
            return dict(replacement)
        return {
            key: _replace_pointer(item, uri=uri, replacement=replacement)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [
            _replace_pointer(item, uri=uri, replacement=replacement) for item in value
        ]
    return value


def _write_json(path: Path, value: object) -> None:
    path.write_bytes(_canonical_bytes(value))


def _write_jsonl(path: Path, values: list[object]) -> None:
    path.write_bytes(b"".join(_canonical_bytes(value) + b"\n" for value in values))


def _stage_and_bundle(
    stage_root: Path,
) -> tuple[Path, dict[str, object], Path, dict[str, object]]:
    stage_path = stage_root / "stage_result.json"
    stage = _read_json(stage_path)
    bundle_path = stage_root / "inspiration_bundle.json"
    bundle = _read_json(bundle_path)
    return stage_path, stage, bundle_path, bundle


def _rewrite_bundle_and_stage(
    project_root: Path,
    stage_path: Path,
    stage: dict[str, object],
    bundle_path: Path,
    bundle: dict[str, object],
) -> None:
    old_bundle = stage["bundle_artifact"]
    assert isinstance(old_bundle, dict)
    bundle_uri = old_bundle["uri"]
    assert isinstance(bundle_uri, str)
    _write_json(bundle_path, bundle)
    bundle_pointer = _artifact_pointer(
        project_root,
        bundle_path,
        media_type="application/json",
    )
    stage = _replace_pointer(
        stage,
        uri=bundle_uri,
        replacement=bundle_pointer,
    )
    assert isinstance(stage, dict)
    _write_json(stage_path, stage)


def _synchronize_intermediate_bundle_and_stage(
    project_root: Path,
    stage_root: Path,
    artifact_path: Path,
    *,
    media_type: str,
) -> None:
    """Re-hash a tampered intermediate through bundle and stage identities."""

    stage_path, stage, bundle_path, bundle = _stage_and_bundle(stage_root)
    artifact_uri = f"artifact://{artifact_path.relative_to(project_root).as_posix()}"
    replacement = _artifact_pointer(
        project_root,
        artifact_path,
        media_type=media_type,
    )
    stage = _replace_pointer(stage, uri=artifact_uri, replacement=replacement)
    bundle = _replace_pointer(bundle, uri=artifact_uri, replacement=replacement)
    assert isinstance(stage, dict)
    assert isinstance(bundle, dict)
    selected_candidates = bundle["selected_candidates"]
    lineage = bundle["lineage_artifacts"]
    assert isinstance(selected_candidates, list)
    assert isinstance(lineage, list)
    bundle["bundle_id"] = deterministic_id(
        "bundle",
        {
            "request_id": bundle["request_id"],
            "run_id": bundle["run_id"],
            "outcome": bundle["outcome"],
            "candidate_ids": tuple(
                item["candidate_id"]
                for item in selected_candidates
                if isinstance(item, dict)
            ),
            "lineage": tuple(
                (item["uri"], item["sha256"], item["size_bytes"])
                for item in lineage
                if isinstance(item, dict)
            ),
        },
    )
    _rewrite_bundle_and_stage(
        project_root,
        stage_path,
        stage,
        bundle_path,
        bundle,
    )
    rewritten_stage = _read_json(stage_path)
    bundle_pointer = rewritten_stage["bundle_artifact"]
    assert isinstance(bundle_pointer, dict)
    rewritten_stage["result_id"] = deterministic_id(
        "inspiration-result",
        {
            "project_id": rewritten_stage["project_id"],
            "request_id": rewritten_stage["request_id"],
            "run_id": rewritten_stage["run_id"],
            "bundle_sha256": bundle_pointer["sha256"],
        },
    )
    _write_json(stage_path, rewritten_stage)


def test_completed_run_verifier_passes_and_writes_nothing(
    tmp_path: Path,
    monkeypatch,
) -> None:
    project_root, _stage_root, command = _build_workspace(
        tmp_path, monkeypatch, complete=True
    )
    before = _file_snapshot(project_root)
    completed = _run(command)
    after = _file_snapshot(project_root)

    assert completed.returncode == 0, completed.stderr
    result = json.loads(completed.stdout)
    assert result["artifact_closure_verified"] is True
    assert result["no_workspace_writes"] is True
    assert result["candidates"] == 1
    assert result["physical_search_attempts"] == 4
    assert result["fetch_requests"] == 0
    assert result["model_calls"] == 0
    assert before == after


def test_gateway_warning_projection_matches_verifier_contract() -> None:
    warnings = tuple(f"warning-{index:02d}" for index in range(40))
    assert HermesFixtureProjector._bounded_warnings(warnings) == (
        *warnings[:31],
        "9 additional warnings omitted",
    )


def test_completed_run_verifier_rejects_oversized_raw_abstract(
    tmp_path: Path,
    monkeypatch,
) -> None:
    response = json.loads(_response_bytes())
    response["message"]["items"][0]["abstract"] = (
        "<jats:p>"
        + (
            "Local resonance preserves spectral separation while weak coupling "
            "suppresses dispersion and supplies a falsifiable acoustic analogy. " * 400
        )
        + "</jats:p>"
    )
    payload = _canonical_bytes(response)
    assert 20_000 < len(payload) <= MAX_SEARCH_RESPONSE_BYTES
    monkeypatch.setattr(
        sys.modules[__name__],
        "_response_bytes",
        lambda: payload,
    )
    _project_root, stage_root, command = _build_workspace(
        tmp_path, monkeypatch, complete=True
    )
    stage = _read_json(stage_root / "stage_result.json")
    warnings = stage["warnings"]
    assert isinstance(warnings, list)
    truncated = [
        item for item in warnings if str(item).startswith("ABSTRACT_TRUNCATED:")
    ]
    assert len(truncated) == 1
    assert warnings == sorted(set(warnings))

    completed = _run(command)
    assert completed.returncode == 2
    assert "abstract exceeds its bounded string length" in completed.stderr


def test_completed_run_verifier_passes_canonical_network_retry(
    tmp_path: Path,
    monkeypatch,
) -> None:
    original_get = _StaticCrossrefTransport.get
    failed_once = False

    def flaky_get(
        transport: _StaticCrossrefTransport,
        url: str,
        *,
        headers: Mapping[str, str],
        timeout_seconds: float,
        max_response_bytes: int,
        deadline_monotonic: float | None = None,
        max_physical_requests: int | None = None,
    ) -> bytes:
        nonlocal failed_once
        if not failed_once:
            failed_once = True
            raise SearchAdapterError(
                "NETWORK_ERROR",
                "synthetic bounded network failure",
            )
        return original_get(
            transport,
            url,
            headers=headers,
            timeout_seconds=timeout_seconds,
            max_response_bytes=max_response_bytes,
            deadline_monotonic=deadline_monotonic,
            max_physical_requests=max_physical_requests,
        )

    monkeypatch.setattr(_StaticCrossrefTransport, "get", flaky_get)
    _project_root, _stage_root, command = _build_workspace(
        tmp_path, monkeypatch, complete=True
    )

    completed = _run(command)
    assert completed.returncode == 0, completed.stderr
    result = json.loads(completed.stdout)
    assert result["physical_search_attempts"] == 5


def test_completed_run_verifier_rejects_forged_stage_result_id(
    tmp_path: Path,
    monkeypatch,
) -> None:
    _project_root, stage_root, command = _build_workspace(
        tmp_path, monkeypatch, complete=True
    )
    stage_path = stage_root / "stage_result.json"
    stage = _read_json(stage_path)
    stage["result_id"] = "inspiration-result-forged"
    _write_json(stage_path, stage)

    completed = _run(command)
    assert completed.returncode == 2
    assert "stage result ID does not recompute" in completed.stderr


def test_completed_run_verifier_rejects_forged_bundle_id(
    tmp_path: Path,
    monkeypatch,
) -> None:
    project_root, stage_root, command = _build_workspace(
        tmp_path, monkeypatch, complete=True
    )
    stage_path, stage, bundle_path, bundle = _stage_and_bundle(stage_root)
    bundle["bundle_id"] = "bundle-forged"
    _rewrite_bundle_and_stage(
        project_root,
        stage_path,
        stage,
        bundle_path,
        bundle,
    )

    completed = _run(command)
    assert completed.returncode == 2
    assert "bundle ID does not recompute" in completed.stderr


def test_completed_run_verifier_rejects_synchronized_lineage_reordering(
    tmp_path: Path,
    monkeypatch,
) -> None:
    project_root, stage_root, command = _build_workspace(
        tmp_path, monkeypatch, complete=True
    )
    stage_path, stage, bundle_path, bundle = _stage_and_bundle(stage_root)
    intermediates = stage["intermediate_artifacts"]
    lineage = bundle["lineage_artifacts"]
    candidates = bundle["selected_candidates"]
    assert isinstance(intermediates, list)
    assert isinstance(lineage, list)
    assert isinstance(candidates, list)
    intermediates.reverse()
    lineage.reverse()
    bundle["bundle_id"] = deterministic_id(
        "bundle",
        {
            "request_id": bundle["request_id"],
            "run_id": bundle["run_id"],
            "outcome": bundle["outcome"],
            "candidate_ids": tuple(
                candidate["candidate_id"] for candidate in candidates
            ),
            "lineage": tuple(
                (pointer["uri"], pointer["sha256"], pointer["size_bytes"])
                for pointer in lineage
            ),
        },
    )
    old_bundle = stage["bundle_artifact"]
    assert isinstance(old_bundle, dict)
    bundle_uri = old_bundle["uri"]
    assert isinstance(bundle_uri, str)
    _write_json(bundle_path, bundle)
    bundle_pointer = _artifact_pointer(
        project_root,
        bundle_path,
        media_type="application/json",
    )
    stage = _replace_pointer(stage, uri=bundle_uri, replacement=bundle_pointer)
    assert isinstance(stage, dict)
    stage["result_id"] = deterministic_id(
        "inspiration-result",
        {
            "project_id": stage["project_id"],
            "request_id": stage["request_id"],
            "run_id": stage["run_id"],
            "bundle_sha256": bundle_pointer["sha256"],
        },
    )
    _write_json(stage_path, stage)

    completed = _run(command)
    assert completed.returncode == 2
    assert "canonical runner order" in completed.stderr


def test_completed_run_verifier_rejects_pending_run(
    tmp_path: Path,
    monkeypatch,
) -> None:
    _project_root, _stage_root, command = _build_workspace(
        tmp_path, monkeypatch, complete=False
    )
    completed = _run(command)
    assert completed.returncode == 2
    assert "not a result-bearing terminal state" in completed.stderr


def test_completed_run_verifier_rejects_tampered_report(
    tmp_path: Path,
    monkeypatch,
) -> None:
    _project_root, stage_root, command = _build_workspace(
        tmp_path, monkeypatch, complete=True
    )
    report = stage_root / "report.md"
    report.write_bytes(report.read_bytes() + b"\ntampered\n")
    completed = _run(command)
    assert completed.returncode == 2
    assert "Artifact size mismatch" in completed.stderr


def test_completed_run_verifier_rejects_orphan_stage_file(
    tmp_path: Path,
    monkeypatch,
) -> None:
    _project_root, stage_root, command = _build_workspace(
        tmp_path, monkeypatch, complete=True
    )
    (stage_root / "orphan.tmp").write_bytes(b"orphan")
    completed = _run(command)
    assert completed.returncode == 2
    assert "orphan or undeclared file" in completed.stderr


def test_completed_run_verifier_rejects_noncanonical_bundle_uri(
    tmp_path: Path,
    monkeypatch,
) -> None:
    project_root, stage_root, command = _build_workspace(
        tmp_path, monkeypatch, complete=True
    )
    stage_path = stage_root / "stage_result.json"
    stage = _read_json(stage_path)
    bundle_path = stage_root / "inspiration_bundle.json"
    renamed = stage_root / "renamed_bundle.json"
    bundle_path.rename(renamed)
    stage["bundle_artifact"] = _artifact_pointer(
        project_root,
        renamed,
        media_type="application/json",
    )
    _write_json(stage_path, stage)

    completed = _run(command)
    assert completed.returncode == 2
    assert "must use canonical Artifact" in completed.stderr


def test_completed_run_verifier_rejects_symlinked_ancestor(
    tmp_path: Path,
    monkeypatch,
) -> None:
    _project_root, stage_root, command = _build_workspace(
        tmp_path, monkeypatch, complete=True
    )
    raw_search = stage_root / "raw_search"
    moved = stage_root / "raw-search-real"
    raw_search.rename(moved)
    raw_search.symlink_to(moved.name, target_is_directory=True)
    completed = _run(command)
    assert completed.returncode == 2
    assert "is a symlink" in completed.stderr


def test_completed_run_verifier_rejects_uncheckpointed_wal(
    tmp_path: Path,
    monkeypatch,
) -> None:
    project_root, _stage_root, command = _build_workspace(
        tmp_path, monkeypatch, complete=True
    )
    wal = project_root / ".gateway" / "materials-gateway.sqlite3-wal"
    wal.write_bytes(b"not-checkpointed")
    os.chmod(wal, stat.S_IRUSR | stat.S_IWUSR)
    completed = _run(command)
    assert completed.returncode == 2
    assert "uncheckpointed WAL" in completed.stderr


def test_completed_run_verifier_rejects_oversized_raw_response(
    tmp_path: Path,
    monkeypatch,
) -> None:
    project_root, stage_root, command = _build_workspace(
        tmp_path, monkeypatch, complete=True
    )
    stage_path, stage, bundle_path, bundle = _stage_and_bundle(stage_root)
    raw_path = min((stage_root / "raw_search").glob("*.json"))
    raw_uri = f"artifact://{raw_path.relative_to(project_root).as_posix()}"
    payload = raw_path.read_bytes()
    raw_path.write_bytes(
        payload + b" " * (MAX_SEARCH_RESPONSE_BYTES + 1 - len(payload))
    )
    raw_pointer = _artifact_pointer(
        project_root,
        raw_path,
        media_type="application/json",
    )
    stage = _replace_pointer(stage, uri=raw_uri, replacement=raw_pointer)
    bundle = _replace_pointer(bundle, uri=raw_uri, replacement=raw_pointer)
    assert isinstance(stage, dict)
    assert isinstance(bundle, dict)
    _rewrite_bundle_and_stage(
        project_root,
        stage_path,
        stage,
        bundle_path,
        bundle,
    )

    completed = _run(command)
    assert completed.returncode == 2
    assert "raw response exceeds the byte bound" in completed.stderr


def test_completed_run_verifier_rejects_excessive_retry_sequence(
    tmp_path: Path,
    monkeypatch,
) -> None:
    project_root, stage_root, command = _build_workspace(
        tmp_path, monkeypatch, complete=True
    )
    stage_path, stage, bundle_path, bundle = _stage_and_bundle(stage_root)
    attempts_path = stage_root / "search_attempts.jsonl"
    attempts_uri = f"artifact://{attempts_path.relative_to(project_root).as_posix()}"
    attempts = [json.loads(line) for line in attempts_path.read_bytes().splitlines()]
    terminal = dict(attempts[0])
    terminal["attempt_number"] = 3
    transient = {
        "attempt_number": 1,
        "error_code": "NETWORK_ERROR",
        "http_status": None,
        "outcome": "error",
        "pacing_delay_seconds": 0.0,
        "query_id": terminal["query_id"],
        "response_bytes": 0,
        "retry_delay_seconds": 1.0,
    }
    second_transient = dict(transient)
    second_transient["attempt_number"] = 2
    attempts[:1] = [transient, second_transient, terminal]
    _write_jsonl(attempts_path, attempts)
    attempts_pointer = _artifact_pointer(
        project_root,
        attempts_path,
        media_type="application/x-ndjson",
    )
    stage = _replace_pointer(stage, uri=attempts_uri, replacement=attempts_pointer)
    bundle = _replace_pointer(bundle, uri=attempts_uri, replacement=attempts_pointer)
    assert isinstance(stage, dict)
    assert isinstance(bundle, dict)
    _rewrite_bundle_and_stage(
        project_root,
        stage_path,
        stage,
        bundle_path,
        bundle,
    )

    completed = _run(command)
    assert completed.returncode == 2
    assert "exceeds the frozen retry bound" in completed.stderr


def test_completed_run_verifier_rejects_forged_transient_error_code(
    tmp_path: Path,
    monkeypatch,
) -> None:
    project_root, stage_root, command = _build_workspace(
        tmp_path, monkeypatch, complete=True
    )
    stage_path, stage, bundle_path, bundle = _stage_and_bundle(stage_root)
    attempts_path = stage_root / "search_attempts.jsonl"
    attempts_uri = f"artifact://{attempts_path.relative_to(project_root).as_posix()}"
    attempts = [json.loads(line) for line in attempts_path.read_bytes().splitlines()]
    terminal = dict(attempts[0])
    terminal["attempt_number"] = 2
    forged_transient = {
        "attempt_number": 1,
        "error_code": "FORGED_TRANSIENT_CODE",
        "http_status": 503,
        "outcome": "error",
        "pacing_delay_seconds": 0.0,
        "query_id": terminal["query_id"],
        "response_bytes": 0,
        "retry_delay_seconds": 1.0,
    }
    attempts[:1] = [forged_transient, terminal]
    _write_jsonl(attempts_path, attempts)
    attempts_pointer = _artifact_pointer(
        project_root,
        attempts_path,
        media_type="application/x-ndjson",
    )
    stage = _replace_pointer(stage, uri=attempts_uri, replacement=attempts_pointer)
    bundle = _replace_pointer(bundle, uri=attempts_uri, replacement=attempts_pointer)
    assert isinstance(stage, dict)
    assert isinstance(bundle, dict)
    _rewrite_bundle_and_stage(
        project_root,
        stage_path,
        stage,
        bundle_path,
        bundle,
    )

    completed = _run(command)
    assert completed.returncode == 2
    assert "retried a noncanonical transient error" in completed.stderr


def test_completed_run_verifier_rejects_zero_delay_network_retry(
    tmp_path: Path,
    monkeypatch,
) -> None:
    project_root, stage_root, command = _build_workspace(
        tmp_path, monkeypatch, complete=True
    )
    stage_path, stage, bundle_path, bundle = _stage_and_bundle(stage_root)
    attempts_path = stage_root / "search_attempts.jsonl"
    attempts_uri = f"artifact://{attempts_path.relative_to(project_root).as_posix()}"
    attempts = [json.loads(line) for line in attempts_path.read_bytes().splitlines()]
    terminal = dict(attempts[0])
    terminal["attempt_number"] = 2
    zero_delay_network_error = {
        "attempt_number": 1,
        "error_code": "NETWORK_ERROR",
        "http_status": None,
        "outcome": "error",
        "pacing_delay_seconds": 0.0,
        "query_id": terminal["query_id"],
        "response_bytes": 0,
        "retry_delay_seconds": 0.0,
    }
    attempts[:1] = [zero_delay_network_error, terminal]
    _write_jsonl(attempts_path, attempts)
    attempts_pointer = _artifact_pointer(
        project_root,
        attempts_path,
        media_type="application/x-ndjson",
    )
    stage = _replace_pointer(stage, uri=attempts_uri, replacement=attempts_pointer)
    bundle = _replace_pointer(bundle, uri=attempts_uri, replacement=attempts_pointer)
    assert isinstance(stage, dict)
    assert isinstance(bundle, dict)
    _rewrite_bundle_and_stage(
        project_root,
        stage_path,
        stage,
        bundle_path,
        bundle,
    )

    completed = _run(command)
    assert completed.returncode == 2
    assert "retried a noncanonical transient error" in completed.stderr


def test_completed_run_verifier_rejects_cross_query_attempt_reordering(
    tmp_path: Path,
    monkeypatch,
) -> None:
    project_root, stage_root, command = _build_workspace(
        tmp_path, monkeypatch, complete=True
    )
    stage_path, stage, bundle_path, bundle = _stage_and_bundle(stage_root)
    attempts_path = stage_root / "search_attempts.jsonl"
    attempts_uri = f"artifact://{attempts_path.relative_to(project_root).as_posix()}"
    attempts = [json.loads(line) for line in attempts_path.read_bytes().splitlines()]
    assert len(attempts) == 4
    attempts[0], attempts[1] = attempts[1], attempts[0]
    attempts[0]["pacing_delay_seconds"] = 0.0
    _write_jsonl(attempts_path, attempts)
    attempts_pointer = _artifact_pointer(
        project_root,
        attempts_path,
        media_type="application/x-ndjson",
    )
    stage = _replace_pointer(stage, uri=attempts_uri, replacement=attempts_pointer)
    bundle = _replace_pointer(bundle, uri=attempts_uri, replacement=attempts_pointer)
    assert isinstance(stage, dict)
    assert isinstance(bundle, dict)
    _rewrite_bundle_and_stage(
        project_root,
        stage_path,
        stage,
        bundle_path,
        bundle,
    )

    completed = _run(command)
    assert completed.returncode == 2
    assert "not in canonical query-plan order" in completed.stderr


def test_completed_run_verifier_rejects_nonzero_first_pacing_delay(
    tmp_path: Path,
    monkeypatch,
) -> None:
    project_root, stage_root, command = _build_workspace(
        tmp_path, monkeypatch, complete=True
    )
    stage_path, stage, bundle_path, bundle = _stage_and_bundle(stage_root)
    attempts_path = stage_root / "search_attempts.jsonl"
    attempts_uri = f"artifact://{attempts_path.relative_to(project_root).as_posix()}"
    attempts = [json.loads(line) for line in attempts_path.read_bytes().splitlines()]
    attempts[0]["pacing_delay_seconds"] = 5.0
    _write_jsonl(attempts_path, attempts)
    attempts_pointer = _artifact_pointer(
        project_root,
        attempts_path,
        media_type="application/x-ndjson",
    )
    stage = _replace_pointer(stage, uri=attempts_uri, replacement=attempts_pointer)
    bundle = _replace_pointer(bundle, uri=attempts_uri, replacement=attempts_pointer)
    assert isinstance(stage, dict)
    assert isinstance(bundle, dict)
    _rewrite_bundle_and_stage(
        project_root,
        stage_path,
        stage,
        bundle_path,
        bundle,
    )

    completed = _run(command)
    assert completed.returncode == 2
    assert "first Crossref attempt has a nonzero pacing delay" in completed.stderr


@pytest.mark.parametrize(
    ("field_name", "replacement"),
    (
        ("attempt_number", True),
        ("http_status", 200.0),
        ("response_bytes", "as-float"),
    ),
)
def test_completed_run_verifier_rejects_noncanonical_attempt_field_types(
    tmp_path: Path,
    monkeypatch,
    field_name: str,
    replacement: object,
) -> None:
    project_root, stage_root, command = _build_workspace(
        tmp_path, monkeypatch, complete=True
    )
    stage_path, stage, bundle_path, bundle = _stage_and_bundle(stage_root)
    attempts_path = stage_root / "search_attempts.jsonl"
    attempts_uri = f"artifact://{attempts_path.relative_to(project_root).as_posix()}"
    attempts = [json.loads(line) for line in attempts_path.read_bytes().splitlines()]
    if replacement == "as-float":
        attempts[0][field_name] = float(attempts[0][field_name])
    else:
        attempts[0][field_name] = replacement
    _write_jsonl(attempts_path, attempts)
    attempts_pointer = _artifact_pointer(
        project_root,
        attempts_path,
        media_type="application/x-ndjson",
    )
    stage = _replace_pointer(stage, uri=attempts_uri, replacement=attempts_pointer)
    bundle = _replace_pointer(bundle, uri=attempts_uri, replacement=attempts_pointer)
    assert isinstance(stage, dict)
    assert isinstance(bundle, dict)
    _rewrite_bundle_and_stage(
        project_root,
        stage_path,
        stage,
        bundle_path,
        bundle,
    )

    completed = _run(command)
    assert completed.returncode == 2
    assert "do not use canonical production types" in completed.stderr


def test_completed_run_verifier_rejects_wrong_execution_manifest(
    tmp_path: Path,
    monkeypatch,
) -> None:
    _project_root, _stage_root, command = _build_workspace(
        tmp_path, monkeypatch, complete=True
    )
    value_index = command.index("--expected-execution-manifest-sha256") + 1
    command[value_index] = "0" * 64
    completed = _run(command)
    assert completed.returncode == 2
    assert "approved execution manifest" in completed.stderr


def test_completed_run_verifier_rejects_fully_forged_manifest_binding(
    tmp_path: Path,
    monkeypatch,
) -> None:
    built = _build_workspace(
        tmp_path,
        monkeypatch,
        complete=True,
        include_binding=True,
    )
    project_root, _stage_root, command, binding = built
    forged_manifest = hashlib.sha256(b"forged-execution-manifest").hexdigest()
    forged_interaction_id = (
        "interaction-"
        + hashlib.sha256(
            canonical_json_bytes(
                {
                    "kind": "requirement-freeze",
                    "request_sha256": forged_manifest,
                    "run_id": binding["run_id"],
                }
            )
        ).hexdigest()[:24]
    )
    forged_interaction = ApprovalInteractionV1(
        interaction_id=forged_interaction_id,
        approval_kind="requirement_freeze",
        prompt=binding["prompt"],
        input_sha256=forged_manifest,
        execution_manifest_sha256=forged_manifest,
    )
    forged_interaction_sha256 = hashlib.sha256(
        canonical_json_bytes(forged_interaction)
    ).hexdigest()
    forged_action = ApproveActionV1(
        interaction_id=forged_interaction_id,
        confirmed_by_user=True,
    )
    forged_action_json = canonical_json_bytes(forged_action).decode("utf-8")
    forged_action_sha256 = hashlib.sha256(
        forged_action_json.encode("utf-8")
    ).hexdigest()
    forged_grant_id = (
        "grant-"
        + hashlib.sha256(
            canonical_json_bytes(
                {
                    "action_sha256": forged_action_sha256,
                    "confirmation_reference": binding["confirmation_reference"],
                    "interaction_id": forged_interaction_id,
                    "interaction_sha256": forged_interaction_sha256,
                    "request_sha256": binding["request_sha256"],
                    "run_id": binding["run_id"],
                }
            )
        ).hexdigest()[:24]
    )

    database = project_root / ".gateway" / "operator-approval-grants.sqlite3"
    connection = sqlite3.connect(database)
    try:
        connection.execute(
            "UPDATE one_time_action_grants SET grant_id=?, interaction_id=?, "
            "interaction_sha256=?, action_sha256=?, action_json=?, "
            "execution_manifest_sha256=?",
            (
                forged_grant_id,
                forged_interaction_id,
                forged_interaction_sha256,
                forged_action_sha256,
                forged_action_json,
                forged_manifest,
            ),
        )
        connection.commit()
        connection.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchall()
    finally:
        connection.close()

    replacements = {
        "--expected-execution-manifest-sha256": forged_manifest,
        "--expected-interaction-id": forged_interaction_id,
        "--expected-interaction-sha256": forged_interaction_sha256,
        "--expected-action-sha256": forged_action_sha256,
    }
    for option, value in replacements.items():
        command[command.index(option) + 1] = value
    completed = _run(command)
    assert completed.returncode == 2
    assert "current frozen inputs/components" in completed.stderr


@pytest.mark.parametrize(
    ("forged_reference", "expected_message"),
    (
        (" ", "grant confirmation reference is invalid"),
        (
            "test:other-valid-confirmation",
            "grant confirmation reference differs from the expected value",
        ),
    ),
)
def test_completed_run_verifier_rejects_forged_confirmation_reference(
    tmp_path: Path,
    monkeypatch,
    forged_reference: str,
    expected_message: str,
) -> None:
    built = _build_workspace(
        tmp_path,
        monkeypatch,
        complete=True,
        include_binding=True,
    )
    project_root, _stage_root, command, _binding = built
    database = project_root / ".gateway" / "operator-approval-grants.sqlite3"
    connection = sqlite3.connect(database)
    connection.row_factory = sqlite3.Row
    try:
        row = connection.execute(
            "SELECT run_id, interaction_id, interaction_sha256, request_sha256, "
            "action_sha256 FROM one_time_action_grants"
        ).fetchone()
        assert row is not None
        forged_grant_id = (
            "grant-"
            + hashlib.sha256(
                canonical_json_bytes(
                    {
                        "action_sha256": row["action_sha256"],
                        "confirmation_reference": forged_reference,
                        "interaction_id": row["interaction_id"],
                        "interaction_sha256": row["interaction_sha256"],
                        "request_sha256": row["request_sha256"],
                        "run_id": row["run_id"],
                    }
                )
            ).hexdigest()[:24]
        )
        connection.execute(
            "UPDATE one_time_action_grants SET grant_id=?, confirmation_reference=?",
            (forged_grant_id, forged_reference),
        )
        connection.commit()
        connection.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchall()
    finally:
        connection.close()

    completed = _run(command)
    assert completed.returncode == 2
    assert expected_message in completed.stderr


@pytest.mark.parametrize(
    ("table_name", "column_name", "expected_message"),
    (
        ("gateway_runs", "record_json", "Gateway run record is not canonical JSON"),
        (
            "gateway_results",
            "result_json",
            "Gateway result is not canonical JSON",
        ),
    ),
)
def test_completed_run_verifier_rejects_noncanonical_gateway_json(
    tmp_path: Path,
    monkeypatch,
    table_name: str,
    column_name: str,
    expected_message: str,
) -> None:
    project_root, _stage_root, command = _build_workspace(
        tmp_path, monkeypatch, complete=True
    )
    database = project_root / ".gateway" / "materials-gateway.sqlite3"
    connection = sqlite3.connect(database)
    try:
        row = connection.execute(f"SELECT {column_name} FROM {table_name}").fetchone()
        assert row is not None
        pretty = json.dumps(json.loads(row[0]), ensure_ascii=False, indent=2)
        connection.execute(
            f"UPDATE {table_name} SET {column_name}=?",
            (pretty,),
        )
        connection.commit()
        connection.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchall()
    finally:
        connection.close()
    completed = _run(command)
    assert completed.returncode == 2
    assert expected_message in completed.stderr


def test_completed_run_verifier_rejects_empty_orphan_directory(
    tmp_path: Path,
    monkeypatch,
) -> None:
    _project_root, stage_root, command = _build_workspace(
        tmp_path, monkeypatch, complete=True
    )
    (stage_root / "empty-orphan").mkdir()
    completed = _run(command)
    assert completed.returncode == 2
    assert "orphan or undeclared directory" in completed.stderr


@pytest.mark.parametrize(
    ("field_name", "field_value", "expected_message"),
    (
        (
            "full_text",
            "<html><body>an article body that metadata mode must reject</body></html>",
            "forbidden body field",
        ),
        (
            "pdf_base64",
            base64.b64encode(b"%PDF-1.7\nforged").decode("ascii"),
            "forbidden body field",
        ),
        (
            "payload",
            "<html><body>unknown-field article body</body></html>",
            "fields outside the selected metadata schema",
        ),
        (
            "blob",
            base64.b64encode(b"%PDF-1.7\nunknown-field-forged").decode("ascii"),
            "fields outside the selected metadata schema",
        ),
    ),
)
def test_completed_run_verifier_rejects_embedded_body_field(
    tmp_path: Path,
    monkeypatch,
    field_name: str,
    field_value: str,
    expected_message: str,
) -> None:
    response = json.loads(_response_bytes())
    response["message"]["items"][0][field_name] = field_value
    payload = _canonical_bytes(response)
    monkeypatch.setattr(
        sys.modules[__name__],
        "_response_bytes",
        lambda: payload,
    )
    _project_root, _stage_root, command = _build_workspace(
        tmp_path, monkeypatch, complete=True
    )
    completed = _run(command)
    assert completed.returncode == 2
    assert expected_message in completed.stderr


@pytest.mark.parametrize(
    ("suffix", "expected_message"),
    (
        (
            " data:application/pdf;base64,JVBERi0xLjcKZm9yZ2Vk",
            "embeds a PDF payload",
        ),
        (
            " <html><body>forged full document</body></html>",
            "embeds document-level markup",
        ),
    ),
)
def test_completed_run_verifier_rejects_payload_inside_allowed_abstract(
    tmp_path: Path,
    monkeypatch,
    suffix: str,
    expected_message: str,
) -> None:
    response = json.loads(_response_bytes())
    response["message"]["items"][0]["abstract"] += suffix
    payload = _canonical_bytes(response)
    monkeypatch.setattr(
        sys.modules[__name__],
        "_response_bytes",
        lambda: payload,
    )
    _project_root, _stage_root, command = _build_workspace(
        tmp_path, monkeypatch, complete=True
    )
    completed = _run(command)
    assert completed.returncode == 2
    assert expected_message in completed.stderr


def test_completed_run_verifier_rejects_noncanonical_crossref_page_contract(
    tmp_path: Path,
    monkeypatch,
) -> None:
    response = json.loads(_response_bytes())
    response["message"]["items-per-page"] = 2
    payload = _canonical_bytes(response)
    monkeypatch.setattr(
        sys.modules[__name__],
        "_response_bytes",
        lambda: payload,
    )
    _project_root, _stage_root, command = _build_workspace(
        tmp_path, monkeypatch, complete=True
    )
    completed = _run(command)
    assert completed.returncode == 2
    assert "items-per-page must equal the requested rows=1" in completed.stderr


def test_crossref_metadata_contract_rejects_unconsumed_second_item() -> None:
    response = json.loads(_response_bytes())
    response["message"]["items"].append(dict(response["message"]["items"][0]))
    with pytest.raises(
        CompletedRunVerificationError,
        match="items must contain exactly one requested item",
    ):
        _validate_crossref_metadata_envelope(response)


def test_completed_run_verifier_rejects_synchronized_selection_type_tamper(
    tmp_path: Path,
    monkeypatch,
) -> None:
    project_root, stage_root, command = _build_workspace(
        tmp_path, monkeypatch, complete=True
    )
    audit_path = stage_root / "selection_audit.json"
    audit = _read_json(audit_path)
    assert audit["pool_exact_duplicate_count"] == 0
    audit["pool_exact_duplicate_count"] = False
    _write_json(audit_path, audit)
    _synchronize_intermediate_bundle_and_stage(
        project_root,
        stage_root,
        audit_path,
        media_type="application/json",
    )

    completed = _run(command)
    assert completed.returncode == 2
    assert "selection audit does not exactly replay" in completed.stderr


@pytest.mark.parametrize(
    ("field_name", "original", "replacement"),
    (
        ("physical_request_count", 0, False),
        ("selected_passage_count", 1, True),
    ),
)
def test_completed_run_verifier_rejects_synchronized_fetch_type_tamper(
    tmp_path: Path,
    monkeypatch,
    field_name: str,
    original: int,
    replacement: bool,
) -> None:
    project_root, stage_root, command = _build_workspace(
        tmp_path, monkeypatch, complete=True
    )
    manifest_path = stage_root / "fetch_manifest.jsonl"
    rows = [json.loads(line) for line in manifest_path.read_bytes().splitlines()]
    target = next(row for row in rows if row[field_name] == original)
    target[field_name] = replacement
    _write_jsonl(manifest_path, rows)
    _synchronize_intermediate_bundle_and_stage(
        project_root,
        stage_root,
        manifest_path,
        media_type="application/x-ndjson",
    )

    completed = _run(command)
    assert completed.returncode == 2
    assert "fetch manifest row" in completed.stderr
    assert "does not exactly replay" in completed.stderr


def test_completed_run_verifier_rejects_synchronized_embedding_cost_tamper(
    tmp_path: Path,
    monkeypatch,
) -> None:
    project_root, stage_root, command = _build_workspace(
        tmp_path, monkeypatch, complete=True
    )
    stage_path, stage, bundle_path, bundle = _stage_and_bundle(stage_root)
    old_cost = stage["cost_ledger_artifact"]
    assert isinstance(old_cost, dict)
    cost_uri = old_cost["uri"]
    assert isinstance(cost_uri, str)
    cost_path = stage_root / "cost_ledger.json"
    ledger = _read_json(cost_path)
    tokens = ledger["embedding_input_tokens"]
    assert isinstance(tokens, int)
    ledger["embedding_input_tokens"] = tokens + 777
    _write_json(cost_path, ledger)
    cost_pointer = _artifact_pointer(
        project_root,
        cost_path,
        media_type="application/json",
    )
    bundle["cost_ledger"] = ledger
    stage = _replace_pointer(stage, uri=cost_uri, replacement=cost_pointer)
    assert isinstance(stage, dict)
    _rewrite_bundle_and_stage(
        project_root,
        stage_path,
        stage,
        bundle_path,
        bundle,
    )
    completed = _run(command)
    assert completed.returncode == 2
    assert "cost ledger does not exactly replay" in completed.stderr


def test_completed_run_verifier_rejects_declared_hidden_body(
    tmp_path: Path,
    monkeypatch,
) -> None:
    project_root, stage_root, command = _build_workspace(
        tmp_path, monkeypatch, complete=True
    )
    stage_path, stage, bundle_path, bundle = _stage_and_bundle(stage_root)
    hidden_body = stage_root / "fetched_documents" / "hidden.html"
    hidden_body.parent.mkdir()
    hidden_body.write_bytes(b"<html><body>hidden article body</body></html>")
    body_pointer = _artifact_pointer(
        project_root,
        hidden_body,
        media_type="text/html",
    )
    intermediates = stage["intermediate_artifacts"]
    lineage = bundle["lineage_artifacts"]
    assert isinstance(intermediates, list)
    assert isinstance(lineage, list)
    intermediates.append(body_pointer)
    lineage.append(body_pointer)
    _rewrite_bundle_and_stage(
        project_root,
        stage_path,
        stage,
        bundle_path,
        bundle,
    )
    completed = _run(command)
    assert completed.returncode == 2
    assert "fetched-body Artifact" in completed.stderr


def test_completed_run_verifier_rejects_padded_pdf_signature(
    tmp_path: Path,
    monkeypatch,
) -> None:
    project_root, stage_root, command = _build_workspace(
        tmp_path, monkeypatch, complete=True
    )
    stage_path, stage, bundle_path, bundle = _stage_and_bundle(stage_root)
    raw_path = min((stage_root / "raw_search").glob("*.json"))
    raw_uri = f"artifact://{raw_path.relative_to(project_root).as_posix()}"
    raw_path.write_bytes(b"\xef\xbb\xbf \n\t%PDF-1.7\nforged")
    raw_pointer = _artifact_pointer(
        project_root,
        raw_path,
        media_type="application/json",
    )
    stage = _replace_pointer(stage, uri=raw_uri, replacement=raw_pointer)
    bundle = _replace_pointer(bundle, uri=raw_uri, replacement=raw_pointer)
    assert isinstance(stage, dict)
    assert isinstance(bundle, dict)
    _rewrite_bundle_and_stage(
        project_root,
        stage_path,
        stage,
        bundle_path,
        bundle,
    )
    completed = _run(command)
    assert completed.returncode == 2
    assert "PDF signature bytes" in completed.stderr


def test_completed_run_verifier_rejects_synchronized_vector_tamper(
    tmp_path: Path,
    monkeypatch,
) -> None:
    project_root, stage_root, command = _build_workspace(
        tmp_path, monkeypatch, complete=True
    )
    stage_path, stage, bundle_path, bundle = _stage_and_bundle(stage_root)
    vector_path = min((stage_root / "vectors").glob("*.f32le"))
    vector_uri = f"artifact://{vector_path.relative_to(project_root).as_posix()}"
    vector_bytes = bytearray(vector_path.read_bytes())
    vector_bytes[-1] ^= 1
    vector_path.write_bytes(bytes(vector_bytes))
    vector_pointer = _artifact_pointer(
        project_root,
        vector_path,
        media_type="application/vnd.material-agent.vector-f32le",
    )

    manifest_path = stage_root / "passage_vectors.jsonl"
    manifest_uri = f"artifact://{manifest_path.relative_to(project_root).as_posix()}"
    rows = [json.loads(line) for line in manifest_path.read_bytes().splitlines()]
    changed = False
    for row in rows:
        if row["vector_artifact"]["uri"] != vector_uri:
            continue
        row["vector_artifact"] = vector_pointer
        row["vector_sha256"] = vector_pointer["sha256"]
        changed = True
    assert changed
    _write_jsonl(manifest_path, rows)
    manifest_pointer = _artifact_pointer(
        project_root,
        manifest_path,
        media_type="application/x-ndjson",
    )

    for old_uri, replacement in (
        (vector_uri, vector_pointer),
        (manifest_uri, manifest_pointer),
    ):
        stage = _replace_pointer(stage, uri=old_uri, replacement=replacement)
        bundle = _replace_pointer(bundle, uri=old_uri, replacement=replacement)
        assert isinstance(stage, dict)
        assert isinstance(bundle, dict)
    _rewrite_bundle_and_stage(
        project_root,
        stage_path,
        stage,
        bundle_path,
        bundle,
    )
    completed = _run(command)
    assert completed.returncode == 2
    assert "ARTIFACT_HASH_MISMATCH" in completed.stderr


def test_completed_run_verifier_rejects_crosswired_passage_lineage(
    tmp_path: Path,
    monkeypatch,
) -> None:
    project_root, stage_root, command = _build_workspace(
        tmp_path, monkeypatch, complete=True
    )
    stage_path, stage, bundle_path, bundle = _stage_and_bundle(stage_root)
    passage_path = stage_root / "passages.jsonl"
    passage_uri = f"artifact://{passage_path.relative_to(project_root).as_posix()}"
    passages = [json.loads(line) for line in passage_path.read_bytes().splitlines()]
    hits = [
        json.loads(line)
        for line in (stage_root / "search_hits.jsonl").read_bytes().splitlines()
    ]
    assert len(passages) == 1
    original_hit_id = passages[0]["hit_id"]
    replacement_hit = next(hit for hit in hits if hit["hit_id"] != original_hit_id)
    assert replacement_hit["document_id"] == passages[0]["document_id"]
    passages[0]["hit_id"] = replacement_hit["hit_id"]
    _write_jsonl(passage_path, passages)
    passage_pointer = _artifact_pointer(
        project_root,
        passage_path,
        media_type="application/x-ndjson",
    )
    stage = _replace_pointer(stage, uri=passage_uri, replacement=passage_pointer)
    bundle = _replace_pointer(bundle, uri=passage_uri, replacement=passage_pointer)
    assert isinstance(stage, dict)
    assert isinstance(bundle, dict)
    _rewrite_bundle_and_stage(
        project_root,
        stage_path,
        stage,
        bundle_path,
        bundle,
    )
    completed = _run(command)
    assert completed.returncode == 2
    assert "metadata responses do not replay to passages" in completed.stderr


def test_completed_run_verifier_rejects_crosswired_candidate_structure(
    tmp_path: Path,
    monkeypatch,
) -> None:
    project_root, stage_root, command = _build_workspace(
        tmp_path, monkeypatch, complete=True
    )
    stage_path, stage, bundle_path, bundle = _stage_and_bundle(stage_root)
    plans = [
        json.loads(line)
        for line in (stage_root / "transformation_proposals.jsonl")
        .read_bytes()
        .splitlines()
    ]
    candidates = bundle["selected_candidates"]
    assert isinstance(candidates, list) and len(candidates) == 1
    candidate = candidates[0]
    assert isinstance(candidate, dict)
    routes = candidate["merged_routes"]
    assert isinstance(routes, list) and len(routes) == 1
    original_plan_id = candidate["representative_plan_id"]
    replacement_plan = next(
        plan for plan in plans if plan["plan_id"] != original_plan_id
    )
    route = routes[0]
    route["plan_id"] = replacement_plan["plan_id"]
    route["route_sha256"] = replacement_plan["route_sha256"]
    route["parent_candidate_id"] = replacement_plan["parent_candidate_id"]
    route["bridge_packet_ids"] = sorted(replacement_plan["bridge_packet_ids"])
    candidate["representative_plan_id"] = replacement_plan["plan_id"]
    candidate["parent_candidate_ids"] = [replacement_plan["parent_candidate_id"]]
    candidate["hypothesis_signature_sha256"] = hashlib.sha256(
        canonical_json_bytes(
            {
                "canonical_structure_id": candidate["canonical_structure_id"],
                "mechanism_tag_ids": tuple(sorted(set(candidate["mechanism_tag_ids"]))),
                "route_sha256s": (replacement_plan["route_sha256"],),
            }
        )
    ).hexdigest()
    _rewrite_bundle_and_stage(
        project_root,
        stage_path,
        stage,
        bundle_path,
        bundle,
    )
    completed = _run(command)
    assert completed.returncode == 2
    assert "deterministic identity/dedup/MMR replay" in completed.stderr
