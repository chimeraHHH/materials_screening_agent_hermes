from __future__ import annotations

from pathlib import Path

import pytest

from material_agent.gateway.authorization import RequirementFreezeGrantIssuer
from material_agent.gateway.mcp_server import (
    GatewayServerSettings,
    GatewayToolDispatcher,
)
from material_agent.gateway.models import InspirationBudgetV1, InspirationConstraintsV1
from material_agent.gateway.persistence import SqliteGatewayRepository
from material_agent.inspiration import (
    ArtifactPointerV1,
    SearchQueryKind,
    SearchQueryV1,
)
from material_agent.inspiration.search import (
    CrossrefPublicAdapter,
    parse_crossref_page,
)
from material_agent.integration.hermes_service import create_hermes_inspiration_service
from material_agent.retrieval.storage import LocalArtifactStore


@pytest.mark.live_crossref
def test_public_crossref_metadata_release_gate(tmp_path: Path) -> None:
    query = SearchQueryV1(
        query_id="query-live-crossref",
        kind=SearchQueryKind.BRIDGE,
        text=(
            "flat band compact localized states mechanical metamaterial "
            "destructive interference"
        ),
        tag_ids=("compact-localized-state", "destructive-interference"),
        bridge_rule_id="bridge-photonic-interference",
    )
    adapter = CrossrefPublicAdapter(max_results=5, timeout_seconds=30)

    page = adapter.search(query, max_response_bytes=500_000)

    store = LocalArtifactStore(tmp_path)
    raw_ref = store.write_bytes(
        "stages/inspiration/live-crossref/raw_search/query-live-crossref.json",
        page.payload,
        media_type=page.media_type,
        immutable=True,
    )
    raw_artifact = ArtifactPointerV1.model_validate(raw_ref.model_dump(mode="json"))
    parsed = parse_crossref_page(
        query=query,
        payload=page.payload,
        raw_response_artifact=raw_artifact,
        max_hits=5,
    )

    assert page.provider == "crossref"
    assert 0 < len(page.payload) <= 500_000
    assert store.exists_with_hash(raw_ref.uri, raw_ref.sha256)
    assert parsed.hits
    assert any(hit.abstract for hit in parsed.hits)
    assert all(hit.doi for hit in parsed.hits)
    assert all(hit.raw_response_artifact == raw_artifact for hit in parsed.hits)


@pytest.mark.live_crossref
def test_live_crossref_runs_inside_the_approval_bound_gateway_lifecycle(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Exercise real Crossref I/O without requiring a particular paper result."""

    monkeypatch.delenv("MATERIALS_CROSSREF_CONTACT_EMAIL", raising=False)
    service = create_hermes_inspiration_service(
        GatewayServerSettings(tmp_path, "live-crossref-gateway")
    )
    dispatcher = GatewayToolDispatcher(service)
    constraints = InspirationConstraintsV1(
        required_elements=("Se", "Ti"),
        excluded_elements=("Pb",),
        material_classes=("transition metal dichalcogenide",),
        dimensionality="2D",
        target_features=("flat electronic band",),
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
    started = dispatcher.dispatch(
        "materials_inspiration_run",
        {
            "submission_id": "live-crossref-gateway-release",
            "goal": "Find a bounded cross-domain flat-band structure hypothesis.",
            "constraints": constraints.model_dump(mode="json"),
        },
    )
    assert started["state"]["status"] == "INTERACTION_REQUIRED"
    run_id = started["run_id"]
    record = service.repository.get_run(run_id)
    assert record is not None
    prepared = service.companion.preparer.prepare(
        run_id=run_id,
        request=record.request,
    )
    execution_manifest_sha256 = service.companion.execution_manifest_sha256(
        request=record.request,
        prepared=prepared,
    )
    RequirementFreezeGrantIssuer(
        repository=service.repository,
        grant_store=service.action_authorizer,
    ).grant_current(
        run_id=run_id,
        confirmation_reference="test:live-crossref-gateway-release",
        expected_execution_manifest_sha256=execution_manifest_sha256,
    )
    terminal = dispatcher.dispatch(
        "materials_run_act",
        {
            "run_id": run_id,
            "action": {
                "kind": "approve",
                "interaction_id": started["state"]["interaction"]["interaction_id"],
                "confirmed_by_user": True,
            },
        },
    )
    assert terminal["state"]["status"] in {"PARTIAL", "SUCCEEDED"}
    result = dispatcher.dispatch("materials_result_get", {"run_id": run_id})
    assert result["verified"] is True
    assert result["bundle"]["outcome"] in {"SUCCEEDED", "SCIENTIFIC_NO_MATCH"}
    assert result["cost_ledger"]["model_calls"] == 0
    assert result["cost_ledger"]["fetched_documents"] == 0
    assert result["cost_ledger"]["search_response_bytes"] > 0

    prefix = (
        tmp_path
        / "live-crossref-gateway"
        / "stages"
        / "inspiration"
        / run_id
    )
    attempts = tuple(
        line
        for line in (prefix / "search_attempts.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
        if line.strip()
    )
    assert len(attempts) == result["cost_ledger"]["search_requests"]
    assert len(tuple((prefix / "raw_search").glob("*.json"))) == 3
    report = (prefix / "report.md").read_text(encoding="utf-8")
    assert "PDF full-text reads: `0`" in report
    assert "Target property status: `UNKNOWN`" in report

    assert isinstance(service.repository, SqliteGatewayRepository)
    service.repository.close()
