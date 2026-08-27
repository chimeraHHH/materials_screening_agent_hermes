from __future__ import annotations

import hashlib
import sys
from pathlib import Path

import pytest

from material_agent.ml_screening.alignn_client import (
    AlignnFlowRunner,
    AlignnProcessError,
    AlignnSubprocessClient,
)
from material_agent.ml_screening.alignn_models import (
    AlignnArtifact,
    AlignnInferenceRequest,
)
from material_agent.ml_screening.alignn_planner import build_alignn_plan
from material_agent.retrieval.storage import LocalArtifactStore


def test_alignn_companion_flow_is_idempotent_and_preserves_evidence_ceiling(tmp_path: Path) -> None:
    root = tmp_path / "project"
    root.mkdir()
    request = _request(root)
    runner = AlignnFlowRunner(artifact_store=LocalArtifactStore(root), client=AlignnSubprocessClient(worker_python=Path(sys.executable), artifact_root=root, worker_script=_fake_worker(tmp_path)))
    first = runner.execute(request)
    assert first.status == "SUCCEEDED"
    assert first.value == 1.25
    assert first.unit == "eV"
    assert first.evidence_level == "NONE"
    assert not first.scientific_conclusion
    assert runner.execute(request) == first


def test_alignn_plan_rejects_model_tampering_and_client_rejects_escape(tmp_path: Path) -> None:
    root = tmp_path / "project"
    root.mkdir()
    request = _request(root)
    (root / request.model_archive.root_relative_path).write_bytes(b"changed")
    with pytest.raises(ValueError, match="wrong size|hash mismatch"):
        build_alignn_plan(request, artifact_root=root)
    request = _request(root)
    runner = AlignnFlowRunner(artifact_store=LocalArtifactStore(root), client=AlignnSubprocessClient(worker_python=Path(sys.executable), artifact_root=root, worker_script=_fake_worker(tmp_path, escape=True)))
    with pytest.raises(AlignnProcessError, match="outside"):
        runner.execute(request)


def _request(root: Path) -> AlignnInferenceRequest:
    structure = _write(root, "inputs/structure.cif", b"mock cif", "chemical/x-cif")
    model = _write(root, "inputs/model.zip", b"mock model", "application/zip")
    return AlignnInferenceRequest(project_id="project-alignn", run_id="run-alignn", candidate_id="candidate-1", input_structure=structure, model_archive=model, alignn_version="2025.4.1", environment_lock_sha256="0" * 64, source_revision="0123456789abcdef", is_mock=True)


def _write(root: Path, relative: str, payload: bytes, media_type: str) -> AlignnArtifact:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)
    return AlignnArtifact(artifact_uri=f"artifact://{relative}", root_relative_path=relative, sha256=hashlib.sha256(payload).hexdigest(), size_bytes=len(payload), media_type=media_type)


def _fake_worker(root: Path, *, escape: bool = False) -> Path:
    path = root / ("fake-alignn-escape.py" if escape else "fake-alignn.py")
    outside = "(parser_root / 'inputs' / 'escape.json').write_text('bad')" if escape else ""
    path.write_text(f'''import hashlib, json, sys
from pathlib import Path
parser_root = Path(sys.argv[sys.argv.index("--artifact-root") + 1])
payload = json.loads(sys.stdin.read())
sandbox = parser_root / payload["plan"]["output_sandbox_relative_path"]
output = sandbox / "prediction.json"
output.write_text('{{"value":1.25}}')
{outside}
data = output.read_bytes()
print(json.dumps({{"schema_version":"agent02-alignn-worker-v1","operation_key":payload["plan"]["operation_key"],"status":"SUCCEEDED","prediction":1.25,"output":{{"artifact_uri":"artifact://" + output.relative_to(parser_root).as_posix(),"root_relative_path":output.relative_to(parser_root).as_posix(),"sha256":hashlib.sha256(data).hexdigest(),"size_bytes":len(data),"media_type":"application/json"}},"is_mock":payload["plan"]["request"]["is_mock"],"warnings":[],"errors":[]}}))
''', encoding="utf-8")
    return path
