from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

from material_agent.cli import main
from material_agent.ml_screening.models import ModelHealthSnapshot
from material_agent.ml_screening.real_resources import real_registry
from material_agent.ml_screening.resources import default_policy
from material_agent.ml_screening.runner import Agent02RunnerAdapter
from material_agent.ml_screening.worker_client import SubprocessWorkerClient
from material_agent.orchestrator.models import (
    ArtifactPointer,
    StageCapability,
    StageExecutionContext,
    StageId,
    StageStatus,
)
from material_agent.retrieval.models import Requirement
from material_agent.retrieval.storage import LocalArtifactStore
from tests.integration.test_orchestrator_p01 import _complete_source_run

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
SOURCE_ROOT = REPOSITORY_ROOT / "src"
STRUCTURE_FIXTURE = REPOSITORY_ROOT / "tests/fixtures/real_ml/si-diamond.cif"


class SimulatedRunnerTermination(BaseException):
    """Model abrupt host-process death between candidate commits."""


class InterruptingWorker:
    is_mock = False

    def __init__(
        self,
        delegate: SubprocessWorkerClient,
        *,
        interrupt_candidate_id: str | None = None,
    ) -> None:
        self.delegate = delegate
        self.interrupt_candidate_id = interrupt_candidate_id
        self.calls: list[str] = []

    def request_for_candidate(self, plan, candidate_id):
        return self.delegate.request_for_candidate(plan, candidate_id)

    def run(self, request):
        self.calls.append(request.candidate_id)
        if request.candidate_id == self.interrupt_candidate_id:
            raise SimulatedRunnerTermination(request.candidate_id)
        return self.delegate.run(request)


@pytest.mark.real_ml
@pytest.mark.slow_real_ml
def test_real_cpu_top5_resumes_after_third_candidate_termination(
    tmp_path: Path,
) -> None:
    worker_python = Path(
        os.environ.get(
            "MATERIAL_AGENT_ML_WORKER_PYTHON",
            REPOSITORY_ROOT / ".venv-agent02/bin/python",
        )
    )
    assert worker_python.is_file(), "dedicated Agent02 Python is missing"
    store, context, health = _real_top5_context(tmp_path, worker_python)
    client = SubprocessWorkerClient(
        python_executable=worker_python,
        artifact_root=store.root,
        package_lock_path=REPOSITORY_ROOT / "requirements-agent02.lock",
        source_root=SOURCE_ROOT,
    )
    interrupted_worker = InterruptingWorker(
        client, interrupt_candidate_id="candidate-3"
    )
    first_adapter = Agent02RunnerAdapter(
        artifact_store=store,
        capability=context.capability,
        worker=interrupted_worker,
        now=lambda: health.tested_at,
    )
    prepared = first_adapter.prepare(context)

    with pytest.raises(SimulatedRunnerTermination, match="candidate-3"):
        first_adapter.start(context, prepared, "a" * 64)
    assert interrupted_worker.calls == ["candidate-1", "candidate-2", "candidate-3"]
    completion_root = (
        store.root / "stages/agent02/run-real-top5/candidate-operations"
    )
    assert len(list(completion_root.glob("*/operation-complete.json"))) == 2
    assert not store.exists(
        "artifact://stages/agent02/run-real-top5/attempt-1/stage-result.json"
    )

    resumed_worker = InterruptingWorker(client)
    resumed_adapter = Agent02RunnerAdapter(
        artifact_store=store,
        capability=context.capability,
        worker=resumed_worker,
        now=lambda: health.tested_at,
    )
    outcome = resumed_adapter.start(context, prepared, "a" * 64)
    assert outcome.status is StageStatus.SUCCEEDED
    assert resumed_worker.calls == ["candidate-3", "candidate-4", "candidate-5"]
    assert outcome.native_result_uri is not None
    envelope = store.read_json(outcome.native_result_uri)
    assert envelope["status"] == "SUCCEEDED"
    assert envelope["candidate_ids"] == [
        "candidate-1",
        "candidate-2",
        "candidate-3",
        "candidate-4",
        "candidate-5",
    ]
    manifest = store.read_jsonl(envelope["candidate_manifest"]["uri"])
    assert len(manifest) == 5
    for record in manifest:
        candidate = record["candidate"]
        assert candidate["decision"] == "PASS"
        assert candidate["evidence_level"] == "L2_ML_SCREENED"
        relaxation = candidate["relaxation_result"]
        assert relaxation["qc_passed"] is True
        assert relaxation["provenance"]["peak_rss_bytes"] > 0
    assert len(list(completion_root.glob("*/operation-complete.json"))) == 5


@pytest.mark.real_ml
def test_cli_run_stage_uses_explicit_real_agent02_factory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    requirement,
    fixture_payload,
) -> None:
    """Exercise the default CLI registry with one real, auditable Si result."""

    project_id = "project-real-cli"
    source_run_id = "run-source"
    source_requirement_payload = requirement.model_dump(mode="json")
    source_requirement_payload["hard_constraints"]["include_elements"] = ["Si"]
    requirement_row, _manifest = _complete_source_run(
        tmp_path,
        Requirement.model_validate(source_requirement_payload),
        fixture_payload,
        project_id=project_id,
        run_id=source_run_id,
    )
    worker_python = Path(
        os.environ.get(
            "MATERIAL_AGENT_ML_WORKER_PYTHON",
            REPOSITORY_ROOT / ".venv-agent02/bin/python",
        )
    )
    assert worker_python.is_file(), "dedicated Agent02 Python is missing"
    _store, context, _health = _real_top5_context(
        tmp_path, worker_python, project_id=project_id
    )
    monkeypatch.setenv("MATERIAL_AGENT_ML_WORKER_PYTHON", str(worker_python))
    stage_input = {
        "source_run_id": source_run_id,
        "requirement_revision": requirement_row["revision"],
        "requirement_artifact_uri": requirement_row["artifact_uri"],
        "requirement_artifact_sha256": requirement_row["artifact_sha256"],
        "artifacts": {
            name: {"uri": ref.uri, "sha256": ref.sha256}
            for name, ref in context.input_artifacts.items()
        },
    }
    input_path = tmp_path / "real-ml-stage-input.json"
    input_path.write_text(json.dumps(stage_input), encoding="utf-8")

    assert (
        main(
            [
                "run-stage",
                "ml",
                "--workspace",
                str(tmp_path),
                "--project",
                project_id,
                "--input",
                str(input_path),
                "--run-id",
                "run-real-cli",
            ]
        )
        == 0
    )
    response = json.loads(capsys.readouterr().out)
    assert response["status"] == "SUCCEEDED"
    assert response["stage_statuses"] == {"agent02": "SUCCEEDED"}
    project_root = tmp_path / project_id
    store = LocalArtifactStore(project_root)
    result = store.read_json(
        "artifact://stages/agent02/run-real-cli/attempt-1/stage-result.json"
    )
    assert result["provenance"]["is_mock"] is False
    manifest = store.read_jsonl(result["candidate_manifest"]["uri"])
    assert len(manifest) == 5
    assert all(
        item["candidate"]["evidence_level"] == "L2_ML_SCREENED"
        for item in manifest
    )


def _real_top5_context(
    tmp_path: Path, worker_python: Path, *, project_id: str = "project"
) -> tuple[LocalArtifactStore, StageExecutionContext, ModelHealthSnapshot]:
    store = LocalArtifactStore(tmp_path / project_id)
    structure_ref = store.write_bytes(
        "candidates/structures/si.cif",
        STRUCTURE_FIXTURE.read_bytes(),
        media_type="chemical/x-cif",
        immutable=True,
    )
    health = _health(worker_python, store.root)
    requirement_payload = json.loads(
        (REPOSITORY_ROOT / "tests/fixtures/requirement.si-o.json").read_text(
            encoding="utf-8"
        )
    )
    requirement_payload["hard_constraints"]["include_elements"] = ["Si"]
    requirement_ref = store.write_json(
        "requirements/requirement.v1.json", requirement_payload, immutable=True
    )
    policy_ref = store.write_json(
        "policies/ml-screening-policy-v1.json", default_policy(), immutable=True
    )
    registry_ref = store.write_json(
        "models/model-registry-v1.json", real_registry(), immutable=True
    )
    health_ref = store.write_json("models/health.json", health, immutable=True)
    manifest_ref = store.write_jsonl(
        "upstream/candidate-manifest.jsonl",
        [
            {
                "schema_version": "agent01-contract-v1",
                "candidate_id": f"candidate-{index}",
                "decision": "PASS",
                "publication_rank": index,
                "formula": "Si",
                "elements": ["Si"],
                "num_sites": 2,
                "properties": [
                    {"name": "band_gap", "value": 0.8, "unit": "eV"},
                    {
                        "name": "energy_above_hull",
                        "value": 0.02,
                        "unit": "eV/atom",
                    },
                    {
                        "name": "is_metal",
                        "value": False,
                        "unit": "dimensionless",
                    },
                    {
                        "name": "structural_dimensionality",
                        "value": 3,
                        "unit": "dimensionless",
                    },
                ],
                "structure_id": "structure-real-si",
                "structure_artifact_uri": structure_ref.uri,
                "structure_artifact_sha256": structure_ref.sha256,
            }
            for index in range(1, 6)
        ],
        immutable=True,
    )
    snapshot_ref = store.write_json(
        "inputs/run-real-top5.json", {"manifest": manifest_ref.sha256}, immutable=True
    )
    capability = StageCapability(
        stage=StageId.ML,
        agent_id="agent02",
        registered=True,
        is_mock=False,
        required_inputs=["requirement", "candidate_manifest"],
    )
    context = StageExecutionContext(
        project_id=project_id,
        run_id="run-real-top5",
        stage=StageId.ML,
        agent_id="agent02",
        attempt=1,
        requirement_revision=1,
        requirement_artifact=ArtifactPointer(
            uri=requirement_ref.uri, sha256=requirement_ref.sha256
        ),
        input_artifacts={
            "candidate_manifest": ArtifactPointer(
                uri=manifest_ref.uri, sha256=manifest_ref.sha256
            ),
            "policy": ArtifactPointer(uri=policy_ref.uri, sha256=policy_ref.sha256),
            "registry": ArtifactPointer(
                uri=registry_ref.uri, sha256=registry_ref.sha256
            ),
            "health": ArtifactPointer(uri=health_ref.uri, sha256=health_ref.sha256),
        },
        capability=capability,
        input_snapshot=ArtifactPointer(
            uri=snapshot_ref.uri, sha256=snapshot_ref.sha256
        ),
    )
    return store, context, health


def _health(worker_python: Path, artifact_root: Path) -> ModelHealthSnapshot:
    process = subprocess.run(
        [
            str(worker_python),
            "-m",
            "material_agent.ml_screening.chgnet_worker",
            "--artifact-root",
            str(artifact_root),
            "--package-lock",
            str(REPOSITORY_ROOT / "requirements-agent02.lock"),
            "--health-structure",
            str(STRUCTURE_FIXTURE),
            "--device",
            "cpu",
        ],
        check=True,
        capture_output=True,
        env={
            "PATH": os.environ.get("PATH", ""),
            "PYTHONPATH": str(SOURCE_ROOT),
            "PYTHONNOUSERSITE": "1",
            "MPLCONFIGDIR": "/tmp/material-agent-mpl",
            "LANG": os.environ.get("LANG", "C.UTF-8"),
        },
    )
    return ModelHealthSnapshot.model_validate_json(process.stdout)
