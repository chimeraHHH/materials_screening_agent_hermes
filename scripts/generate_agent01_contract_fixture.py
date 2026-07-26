"""Generate the deterministic minimal Agent 01 public-contract fixture."""

from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path

from material_agent.retrieval.evaluator import evaluate_candidate
from material_agent.retrieval.models import (
    AGENT01_CONTRACT_VERSION,
    CandidateAuditRecord,
    Requirement,
    RetrievalPolicy,
    RetrievalStageInput,
    SourceMetadata,
    StageResultEnvelope,
    StageStatus,
)
from material_agent.retrieval.normalizer import (
    add_dimensionality_property,
    apply_task_metadata,
    normalize_candidate,
)
from material_agent.retrieval.query import CORE_FIELDS, build_query_plan
from material_agent.retrieval.ranking import rank_and_publish
from material_agent.retrieval.storage import LocalArtifactStore
from material_agent.retrieval.structures import (
    calculate_dimensionality,
    process_structure,
)


FIXED_TIME = datetime(2026, 7, 26, 0, 0, tzinfo=UTC)


def generate(output_root: Path, fixture_root: Path) -> None:
    store = LocalArtifactStore(output_root)
    requirement = Requirement.model_validate_json(
        (fixture_root / "requirement.si-o.json").read_text(encoding="utf-8")
    )
    source_payload = json.loads(
        (fixture_root / "mp-summary.si-o.json").read_text(encoding="utf-8")
    )
    document = source_payload["documents"][0]
    policy = RetrievalPolicy(retry_base_seconds=0)

    requirement_ref = store.write_json(
        "requirements/requirement.v1.json",
        requirement.model_dump(mode="json"),
    )
    stage_input = RetrievalStageInput(
        project_id="project-contract-fixture",
        run_id="run-contract-fixture",
        requirement_revision=requirement.revision,
        requirement_artifact_uri=requirement_ref.uri,
        requirement_hash=requirement_ref.sha256,
        retrieval_policy_version=policy.policy_version,
        confirmed_by_user=True,
    )
    metadata = SourceMetadata(
        database_version=source_payload["database_version"],
        client_version="fixture-adapter-v1",
        available_fields=sorted(CORE_FIELDS),
    )
    query_plan = build_query_plan(
        requirement,
        requirement_ref.sha256,
        metadata,
        policy,
    ).model_copy(update={"created_at": FIXED_TIME})

    processed = process_structure(
        document["structure"],
        summary_elements=document["elements"],
        summary_num_sites=document["nsites"],
        summary_formula=document["formula_pretty"],
        policy=policy,
    )
    source_ref = store.write_json(
        f"candidates/structures/{processed.structure_id}.source.json",
        processed.source_payload,
    )
    cif_ref = store.write_text(
        f"candidates/structures/{processed.structure_id}.cif",
        processed.cif_text,
        "chemical/x-cif",
    )
    candidate = normalize_candidate(
        document,
        project_id=stage_input.project_id,
        query_plan=query_plan,
        processed_structure=processed,
        source_uri=source_ref.uri,
        source_sha256=source_ref.sha256,
        structure_uri=cif_ref.uri,
        structure_sha256=cif_ref.sha256,
        retrieved_at=FIXED_TIME,
    )
    dimensionality = calculate_dimensionality(processed.structure)
    candidate = add_dimensionality_property(
        candidate,
        value=dimensionality.value,
        method=dimensionality.method,
        policy_version=policy.dimensionality_policy_version,
        database_version=query_plan.database_version,
        retrieved_at=FIXED_TIME,
        warning_messages=dimensionality.warnings,
    )
    candidate = apply_task_metadata(candidate, source_payload["task_metadata"])
    candidate = evaluate_candidate(candidate, requirement, policy)
    candidates, published = rank_and_publish(
        [candidate],
        requirement.ranking_preferences,
        requirement.budget.max_candidates,
    )
    candidate = candidates[0]
    assert published and candidate.published_downstream

    input_ref = store.write_json(
        "stages/agent01/run-contract-fixture/input_snapshot.json",
        {
            "stage_input": stage_input.model_dump(mode="json"),
            "requirement": requirement.model_dump(mode="json"),
        },
    )
    manifest_ref = store.write_jsonl(
        "stages/agent01/run-contract-fixture/candidate_manifest.jsonl",
        [candidate.model_dump(mode="json")],
    )
    result = StageResultEnvelope(
        run_id=stage_input.run_id,
        status=StageStatus.SUCCEEDED,
        input_snapshot_uri=input_ref.uri,
        input_snapshot_sha256=input_ref.sha256,
        output_artifacts=[source_ref, cif_ref, manifest_ref],
        candidate_manifest=manifest_ref,
        candidate_ids=[candidate.candidate_id],
        warnings=["offline contract fixture"],
        metrics={
            "database_returned": 1,
            "normalized": 1,
            "failed": 0,
            "rejected": 0,
            "uncertain": 0,
            "passed": 1,
            "published_downstream": 1,
        },
        provenance={
            "source": "materials_project",
            "database_version": query_plan.database_version,
            "query_id": query_plan.query_id,
            "query_fingerprint": query_plan.query_fingerprint,
            "policy_version": policy.policy_version,
            "is_mock": True,
        },
        started_at=FIXED_TIME,
        finished_at=FIXED_TIME,
    )
    result_ref = store.write_json(
        "stages/agent01/run-contract-fixture/stage_result.json",
        result.model_dump(mode="json"),
    )
    candidate_schema_ref = store.write_json(
        "schemas/candidate-audit-record.schema.json",
        CandidateAuditRecord.model_json_schema(),
    )
    result_schema_ref = store.write_json(
        "schemas/stage-result-envelope.schema.json",
        StageResultEnvelope.model_json_schema(),
    )
    store.write_json(
        "fixture_manifest.json",
        {
            "contract_version": AGENT01_CONTRACT_VERSION,
            "candidate_manifest": manifest_ref.model_dump(mode="json"),
            "stage_result": result_ref.model_dump(mode="json"),
            "schemas": [
                candidate_schema_ref.model_dump(mode="json"),
                result_schema_ref.model_dump(mode="json"),
            ],
        },
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("tests/fixtures/contracts/agent01-v1"),
    )
    parser.add_argument(
        "--fixture-root",
        type=Path,
        default=Path("tests/fixtures"),
    )
    arguments = parser.parse_args()
    generate(arguments.output, arguments.fixture_root)


if __name__ == "__main__":
    main()
