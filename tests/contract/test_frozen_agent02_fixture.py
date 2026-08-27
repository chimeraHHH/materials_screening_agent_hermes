from __future__ import annotations

import json
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

from material_agent.ml_screening.models import (
    AGENT02_CONTRACT_VERSION,
    EvidenceLevel,
    MLCandidateManifestRecord,
    MLStagePlan,
    MLStageResultEnvelope,
    WorkerRequest,
)
from material_agent.ml_screening.worker_protocol import validate_worker_inputs
from material_agent.retrieval.storage import LocalArtifactStore

PROJECT_ROOT = Path(__file__).parents[2]
FIXTURE_ROOT = PROJECT_ROOT / "tests/fixtures"
CONTRACT_ROOT = FIXTURE_ROOT / "contracts/agent02-v1"


def _generate_fixture(output_root: Path) -> None:
    generator_path = (
        PROJECT_ROOT / "scripts/generate_agent02_contract_fixture.py"
    )
    spec = spec_from_file_location(
        "agent02_contract_fixture_generator",
        generator_path,
    )
    assert spec is not None and spec.loader is not None
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    module.generate(output_root, FIXTURE_ROOT)


def _generated_files(root: Path) -> dict[Path, bytes]:
    return {
        path.relative_to(root): path.read_bytes()
        for path in root.rglob("*")
        if path.is_file()
    }


def test_committed_agent02_fixture_is_valid_and_never_claims_l2() -> None:
    store = LocalArtifactStore(CONTRACT_ROOT)
    fixture = json.loads(
        (CONTRACT_ROOT / "fixture_manifest.json").read_text(encoding="utf-8")
    )
    assert fixture["contract_version"] == AGENT02_CONTRACT_VERSION
    for key in (
        "model_card",
        "policy",
        "registry",
        "health",
        "native_plan",
        "candidate_manifest",
        "report",
        "worker_request",
        "worker_response",
        "stage_result",
    ):
        ref = fixture[key]
        assert store.exists_with_hash(ref["uri"], ref["sha256"])
    for ref in fixture["schemas"]:
        assert store.exists_with_hash(ref["uri"], ref["sha256"])

    plan = MLStagePlan.model_validate(
        store.read_json(fixture["native_plan"]["uri"])
    )
    assert plan.inference_candidate_ids == ["cand_contract_si_o"]
    worker_request = WorkerRequest.model_validate(
        store.read_json(fixture["worker_request"]["uri"])
    )
    validated_inputs = validate_worker_inputs(worker_request, CONTRACT_ROOT)
    assert validated_inputs[0].name == "str_contract_si_o.cif"
    result = MLStageResultEnvelope.model_validate(
        store.read_json(fixture["stage_result"]["uri"])
    )
    records = [
        MLCandidateManifestRecord.model_validate(item)
        for item in store.read_jsonl(result.candidate_manifest.uri)
    ]
    assert len(records) == 1
    candidate = records[0].candidate
    assert candidate.evidence_level is EvidenceLevel.L1_RETRIEVED
    assert all(prop.is_mock for prop in candidate.ml_properties)
    assert candidate.recommended_downstream_structure_id == (
        candidate.source_structure_id
    )
    report = store.read_bytes(fixture["report"]["uri"]).decode("utf-8")
    assert "TEST FIXTURE / MOCK" in report


def test_agent02_contract_fixture_is_reproducible(tmp_path: Path) -> None:
    generated = tmp_path / "agent02-v1"
    _generate_fixture(generated)
    assert _generated_files(generated) == _generated_files(CONTRACT_ROOT)
