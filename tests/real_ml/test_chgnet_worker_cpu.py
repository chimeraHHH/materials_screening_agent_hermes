from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from material_agent.ml_screening.models import (
    ArtifactPointer,
    CandidateProperty,
    EvidenceLevel,
    MLCandidateInput,
    MLDecision,
    MLScreeningRequest,
    ModelHealthSnapshot,
    StructureRef,
)
from material_agent.ml_screening.planner import build_ml_stage_plan
from material_agent.ml_screening.real_resources import real_registry
from material_agent.ml_screening.requirement import requirement_view_from_payload
from material_agent.ml_screening.resources import (
    default_policy,
    sha256_payload,
)
from material_agent.ml_screening.worker_client import SubprocessWorkerClient


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
SOURCE_ROOT = REPOSITORY_ROOT / "src"
STRUCTURE_FIXTURE = (
    REPOSITORY_ROOT
    / "tests/fixtures/real_ml/si-diamond.cif"
)


@pytest.mark.real_ml
@pytest.mark.slow_real_ml
def test_real_cpu_worker_produces_valid_l2_artifacts(tmp_path: Path) -> None:
    worker_python = Path(
        os.environ.get(
            "MATERIAL_AGENT_ML_WORKER_PYTHON",
            REPOSITORY_ROOT / ".venv-agent02/bin/python",
        )
    )
    assert worker_python.is_file(), "dedicated Agent02 Python is missing"
    artifact_root = tmp_path / "project"
    structure_path = artifact_root / "candidates/structures/si.cif"
    structure_path.parent.mkdir(parents=True)
    shutil.copyfile(STRUCTURE_FIXTURE, structure_path)
    structure_sha = hashlib.sha256(structure_path.read_bytes()).hexdigest()

    health = _health(worker_python, artifact_root)
    policy = default_policy()
    registry = real_registry()
    requirement_payload = json.loads(
        (REPOSITORY_ROOT / "tests/fixtures/requirement.si-o.json").read_text(
            encoding="utf-8"
        )
    )
    requirement_payload["hard_constraints"]["include_elements"] = ["Si"]
    requirement = requirement_view_from_payload(requirement_payload)
    candidate = MLCandidateInput(
        candidate_id="candidate-real-si",
        upstream_manifest_uri="artifact://upstream/manifest.jsonl",
        upstream_manifest_sha256=hashlib.sha256(b"upstream").hexdigest(),
        formula="Si",
        elements=["Si"],
        num_sites=2,
        publication_rank=1,
        upstream_decision=MLDecision.PASS,
        upstream_evidence_level=EvidenceLevel.L1_RETRIEVED,
        properties=[
            CandidateProperty(name="band_gap", value=0.8, unit="eV"),
            CandidateProperty(
                name="energy_above_hull", value=0.02, unit="eV/atom"
            ),
            CandidateProperty(
                name="is_metal", value=False, unit="dimensionless"
            ),
        ],
        source_structure=StructureRef(
            structure_id="structure-real-si",
            uri="artifact://candidates/structures/si.cif",
            sha256=structure_sha,
            num_sites=2,
            elements=["Si"],
            dimensionality=3,
            is_periodic=True,
            is_inorganic=True,
            parseable=True,
            ase_compatible=True,
            has_finite_values=True,
            positive_volume=True,
            minimum_distance_angstrom=2.0,
            hash_verified=True,
        ),
    )
    plan = build_ml_stage_plan(
        project_id="project-real-ml",
        run_id="run-real-cpu",
        requirement_revision=1,
        attempt=1,
        orchestrator_input_snapshot=_pointer("input", {"run": "real"}),
        requirement_artifact=_pointer("requirement", requirement_payload),
        candidate_manifest_artifact=_pointer("manifest", {"candidate": 1}),
        stage_request_artifact=None,
        policy_artifact=ArtifactPointer(
            uri="artifact://policy.json", sha256=sha256_payload(policy)
        ),
        registry_artifact=ArtifactPointer(
            uri="artifact://registry.json", sha256=sha256_payload(registry)
        ),
        health_artifact=ArtifactPointer(
            uri="artifact://health.json", sha256=sha256_payload(health)
        ),
        requirement=requirement,
        candidates=[candidate],
        request=MLScreeningRequest(),
        policy=policy,
        registry=registry,
        health=health,
        created_at=health.tested_at,
    )
    assert plan.inference_candidate_ids == [candidate.candidate_id]
    client = SubprocessWorkerClient(
        python_executable=worker_python,
        artifact_root=artifact_root,
        package_lock_path=REPOSITORY_ROOT / "requirements-agent02.lock",
        source_root=SOURCE_ROOT,
    )
    response = client.run(client.request_for_candidate(plan, candidate.candidate_id))
    result = response.candidate_result
    assert result is not None
    assert result.decision is MLDecision.PASS, (
        result.relaxation_result.model_dump_json()
        if result.relaxation_result
        else "missing relaxation"
    )
    assert result.evidence_level is EvidenceLevel.L2_ML_SCREENED
    assert result.relaxation_result is not None
    assert result.relaxation_result.qc_passed
    assert result.relaxation_result.device == "cpu"
    assert all(not prop.is_mock for prop in result.ml_properties)
    assert "torch" not in sys.modules
    for artifact in response.produced_artifacts:
        output = artifact_root / artifact.root_relative_path
        assert output.is_file()
        assert hashlib.sha256(output.read_bytes()).hexdigest() == artifact.sha256


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


def _pointer(name: str, payload: object) -> ArtifactPointer:
    return ArtifactPointer(
        uri=f"artifact://{name}.json",
        sha256=sha256_payload(payload),
    )
