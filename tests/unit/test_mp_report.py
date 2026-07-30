from __future__ import annotations

import json
from pathlib import Path

from material_agent.retrieval.adapters import InMemoryMaterialsAdapter
from material_agent.retrieval.models import Requirement, RetrievalStageInput, StageStatus
from material_agent.retrieval.runner import RetrievalStageRunner
from material_agent.retrieval.storage import LocalArtifactStore


def test_mp_report_adds_rich_artifacts_without_changing_candidate_manifest(
    tmp_path: Path, requirement: Requirement
) -> None:
    fixture = json.loads(
        (Path(__file__).parents[1] / "fixtures" / "mp-summary.si-o.json").read_text()
    )
    document = fixture["documents"][0]
    document.update(
        {
            "formation_energy_per_atom": -1.23,
            "density": 2.65,
            "symmetry": {"symbol": "P6_3/mmc"},
            "ordering": "NM",
            "total_magnetization": 0.0,
            "possible_species": ["Si4+", "O2-"],
            "theoretical": False,
        }
    )
    store = LocalArtifactStore(tmp_path)
    requirement_ref = store.write_json("requirement.json", requirement.model_dump(mode="json"))
    adapter = InMemoryMaterialsAdapter(
        [document],
        database_version="fixture-rich-v1",
        available_fields=list(document),
    )
    result = RetrievalStageRunner(adapter=adapter, artifact_store=store).run(
        requirement,
        RetrievalStageInput(
            project_id="p", run_id="rich", requirement_revision=requirement.revision,
            requirement_artifact_uri=requirement_ref.uri, requirement_hash=requirement_ref.sha256,
            retrieval_policy_version="retrieval-policy-v1", confirmed_by_user=True,
        ),
    )
    assert result.status in {StageStatus.SUCCEEDED, StageStatus.PARTIAL}
    uris = {artifact.uri for artifact in result.output_artifacts}
    assert "artifact://stages/agent01/rich/report_enrichment.jsonl" in uris
    enrichment = store.read_jsonl("stages/agent01/rich/report_enrichment.jsonl")[0]
    assert enrichment["sections"]["crystal_structure"]["status"] == "COMPLETE", enrichment
    assert any(uri.endswith("/crystal_structure.png") for uri in uris)
    assert enrichment["schema_version"] == "agent01-mp-report-v1"
    assert enrichment["sections"]["summary"]["fields"][3]["value"] == -1.23
    assert enrichment["sections"]["experimental"]["status"] == "COMPLETE"
    manifest = store.read_jsonl("stages/agent01/rich/candidate_manifest.jsonl")[0]
    assert manifest["schema_version"] == "agent01-contract-v1"
