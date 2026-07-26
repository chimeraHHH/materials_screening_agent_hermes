from __future__ import annotations

import json
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

from pymatgen.core import Structure

from material_agent.retrieval import AGENT01_CONTRACT_VERSION
from material_agent.retrieval.models import CandidateAuditRecord, StageResultEnvelope
from material_agent.retrieval.storage import LocalArtifactStore


PROJECT_ROOT = Path(__file__).parents[2]
FIXTURE_ROOT = PROJECT_ROOT / "tests/fixtures"
CONTRACT_FIXTURE_ROOT = FIXTURE_ROOT / "contracts/agent01-v1"


def _generate_fixture(output_root: Path) -> None:
    generator_path = (
        PROJECT_ROOT / "scripts/generate_agent01_contract_fixture.py"
    )
    spec = spec_from_file_location("agent01_contract_fixture_generator", generator_path)
    assert spec is not None and spec.loader is not None
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    module.generate(output_root, FIXTURE_ROOT)


def _generated_files(root: Path) -> dict[Path, bytes]:
    return {
        path.relative_to(root): path.read_bytes()
        for path in root.rglob("*")
        if path.is_file() and path.name != "README.md"
    }


def test_committed_fixture_matches_frozen_contract_and_artifact_hashes() -> None:
    store = LocalArtifactStore(CONTRACT_FIXTURE_ROOT)
    manifest = json.loads(
        (CONTRACT_FIXTURE_ROOT / "fixture_manifest.json").read_text(
            encoding="utf-8"
        )
    )
    assert manifest["contract_version"] == AGENT01_CONTRACT_VERSION

    result_ref = manifest["stage_result"]
    assert store.exists_with_hash(result_ref["uri"], result_ref["sha256"])
    result = StageResultEnvelope.model_validate(store.read_json(result_ref["uri"]))
    assert result.schema_version == AGENT01_CONTRACT_VERSION

    candidates = [
        CandidateAuditRecord.model_validate(item)
        for item in store.read_jsonl(result.candidate_manifest.uri)
    ]
    assert len(candidates) == 1
    candidate = candidates[0]
    assert candidate.schema_version == AGENT01_CONTRACT_VERSION

    for artifact in [*result.output_artifacts, *manifest["schemas"]]:
        artifact_uri = (
            artifact.uri if hasattr(artifact, "uri") else artifact["uri"]
        )
        artifact_sha256 = (
            artifact.sha256
            if hasattr(artifact, "sha256")
            else artifact["sha256"]
        )
        assert store.exists_with_hash(artifact_uri, artifact_sha256)

    assert store.exists_with_hash(
        candidate.structure_source_artifact_uri,
        candidate.structure_source_artifact_sha256,
    )
    assert store.exists_with_hash(
        candidate.structure_artifact_uri,
        candidate.structure_artifact_sha256,
    )
    structure = Structure.from_str(
        store.read_bytes(candidate.structure_artifact_uri).decode("utf-8"),
        fmt="cif",
    )
    assert sorted(str(element) for element in structure.composition.elements) == (
        candidate.elements
    )
    assert len(structure) == candidate.num_sites


def test_contract_fixture_is_reproducible(tmp_path: Path) -> None:
    generated_root = tmp_path / "agent01-v1"
    _generate_fixture(generated_root)
    assert _generated_files(generated_root) == _generated_files(
        CONTRACT_FIXTURE_ROOT
    )
