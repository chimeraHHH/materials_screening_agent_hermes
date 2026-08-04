from __future__ import annotations

import json
from copy import deepcopy
from typing import Any

import pytest

from material_agent.retrieval.adapters import NomadAdapter
from material_agent.retrieval.models import (
    Decision,
    RetrievalPolicy,
    RetrievalStageInput,
    SourceDatabase,
    StageStatus,
)
from material_agent.retrieval.normalizer import candidate_id_for
from material_agent.retrieval.query import (
    ELECTRON_VOLT_JOULE,
    build_query_plan,
    retrieval_policy_for_source,
)
from material_agent.retrieval.runner import RetrievalStageRunner
from material_agent.retrieval.storage import LocalArtifactStore


class FakeResponse:
    def __init__(self, payload: Any) -> None:
        self.payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> Any:
        return self.payload


class FakeSession:
    def __init__(self, pages: list[dict[str, Any]]) -> None:
        self.pages = list(pages)
        self.get_calls: list[dict[str, Any]] = []
        self.post_calls: list[dict[str, Any]] = []

    def get(self, url: str, **kwargs: Any) -> FakeResponse:
        self.get_calls.append({"url": url, **kwargs})
        return FakeResponse({"info": {"version": "v1, NOMAD test"}})

    def post(self, url: str, **kwargs: Any) -> FakeResponse:
        self.post_calls.append({"url": url, **kwargs})
        if not self.pages:
            raise AssertionError("unexpected NOMAD page request")
        return FakeResponse(self.pages.pop(0))


def _nomad_entry(entry_id: str = "nomad-entry-1") -> dict[str, Any]:
    return {
        "entry_id": entry_id,
        "upload_id": "upload-test",
        "parser_name": "parsers/vasp",
        "publish_time": "2026-07-20T00:00:00Z",
        "archive": {
            "results": {
                "material": {
                    "elements": ["O", "Si"],
                    "chemical_formula_hill": "OSi",
                    "chemical_formula_reduced": "OSi",
                    "topology": [
                        {
                            "label": "original",
                            "atoms_ref": {
                                "labels": ["Si", "O"],
                                "positions": [
                                    [0.0, 0.0, 0.0],
                                    [1.35e-10, 1.35e-10, 1.35e-10],
                                ],
                                "lattice_vectors": [
                                    [5.4e-10, 0.0, 0.0],
                                    [0.0, 5.4e-10, 0.0],
                                    [0.0, 0.0, 5.4e-10],
                                ],
                                "periodic": [True, True, True],
                            },
                        }
                    ],
                },
                "properties": {
                    "electronic": {
                        "band_gap": [
                            {
                                "value": 0.9 * ELECTRON_VOLT_JOULE,
                                "provenance": {"label": "dos"},
                            },
                            {
                                "value": 0.8 * ELECTRON_VOLT_JOULE,
                                "provenance": {"label": "band_structure"},
                            },
                        ]
                    }
                },
                "method": {
                    "method_name": "DFT",
                    "workflow_name": "SinglePoint",
                    "simulation": {"program_name": "VASP"},
                },
            }
        },
    }


def _page(
    entries: list[dict[str, Any]],
    *,
    next_cursor: str | None = None,
) -> dict[str, Any]:
    pagination: dict[str, Any] = {}
    if next_cursor is not None:
        pagination["next_page_after_value"] = next_cursor
    return {"data": entries, "pagination": pagination}


def test_nomad_query_plan_converts_ev_and_keeps_unsupported_hull_local(
    requirement, requirement_hash
) -> None:
    adapter = NomadAdapter(session=FakeSession([]))
    policy = retrieval_policy_for_source(SourceDatabase.NOMAD)

    plan = build_query_plan(
        requirement,
        requirement_hash,
        adapter.metadata(),
        policy,
    )

    assert plan.source_database is SourceDatabase.NOMAD
    assert plan.endpoint == "/entries/archive/query"
    clauses = plan.pushdown_filters["and"]
    band_gap = next(
        clause["results.properties.electronic.band_gap.value"]
        for clause in clauses
        if "results.properties.electronic.band_gap.value" in clause
    )
    assert band_gap["gte"] == pytest.approx(0.5 * ELECTRON_VOLT_JOULE)
    assert band_gap["lte"] == pytest.approx(1.0 * ELECTRON_VOLT_JOULE)
    assert "energy_above_hull" in plan.local_only_constraints
    assert "is_metal" in plan.local_only_constraints


def test_nomad_adapter_paginates_and_maps_si_units_to_agent01_units(
    requirement, requirement_hash
) -> None:
    second = deepcopy(_nomad_entry("nomad-entry-2"))
    session = FakeSession(
        [
            _page([_nomad_entry()], next_cursor="cursor-1"),
            _page([second]),
        ]
    )
    adapter = NomadAdapter(session=session)
    policy = RetrievalPolicy(
        policy_version="retrieval-policy-nomad-v1",
        source_database=SourceDatabase.NOMAD,
        endpoint="/entries/archive/query",
        chunk_size=1,
        max_records_scanned=2,
        retry_base_seconds=0,
    )
    plan = build_query_plan(
        requirement,
        requirement_hash,
        adapter.metadata(),
        policy,
    )

    documents = adapter.search(plan)
    resolved, warnings = adapter.resolve_task_metadata(
        ["nomad-entry-1", "nomad-entry-2"],
        ["nomad-entry-1", "nomad-entry-2"],
        100,
    )

    assert [item["material_id"] for item in documents] == [
        "nomad-entry-1",
        "nomad-entry-2",
    ]
    assert documents[0]["band_gap"] == pytest.approx(0.8)
    assert documents[0]["energy_above_hull"] is None
    assert documents[0]["is_metal"] is False
    assert documents[0]["structure"]["lattice"]["matrix"][0][0] == pytest.approx(
        5.4
    )
    assert documents[0]["source_response"]["entry_id"] == "nomad-entry-1"
    assert documents[0]["source_response"]["archive"]["results"]["method"][
        "simulation"
    ]["program_name"] == "VASP"
    assert len(documents[0]["structure"]["sites"]) == 2
    assert session.post_calls[1]["json"]["pagination"]["page_after_value"] == (
        "cursor-1"
    )
    assert resolved["nomad-entry-1"]["run_type"] == "DFT"
    assert resolved["nomad-entry-1"]["calc_type"] == "VASP"
    assert warnings == []


def test_nomad_adapter_skips_incomplete_archive_entries_and_keeps_paginating(
    requirement, requirement_hash
) -> None:
    incomplete = _nomad_entry("nomad-incomplete")
    incomplete["archive"]["results"].pop("material")
    session = FakeSession(
        [
            _page([incomplete], next_cursor="cursor-1"),
            _page([_nomad_entry("nomad-valid")]),
        ]
    )
    adapter = NomadAdapter(session=session)
    policy = RetrievalPolicy(
        policy_version="retrieval-policy-nomad-v1",
        source_database=SourceDatabase.NOMAD,
        endpoint="/entries/archive/query",
        chunk_size=1,
        max_records_scanned=1,
        retry_base_seconds=0,
    )
    plan = build_query_plan(
        requirement,
        requirement_hash,
        adapter.metadata(),
        policy,
    )

    documents = adapter.search(plan)
    _, warnings = adapter.resolve_task_metadata(["nomad-valid"], ["nomad-valid"], 1)

    assert [item["material_id"] for item in documents] == ["nomad-valid"]
    assert warnings == [
        "NOMAD skipped 1 incomplete archive entries without a canonical material record"
    ]


def test_nomad_runner_emits_v2_uncertain_record_without_fabricating_hull(
    tmp_path, requirement
) -> None:
    session = FakeSession([_page([_nomad_entry()])])
    adapter = NomadAdapter(session=session)
    policy = retrieval_policy_for_source(SourceDatabase.NOMAD).model_copy(
        update={"retry_base_seconds": 0}
    )
    store = LocalArtifactStore(tmp_path)
    requirement_ref = store.write_json(
        "requirements/requirement.v1.json",
        requirement.model_dump(mode="json"),
    )
    stage_input = RetrievalStageInput(
        project_id="project-nomad",
        run_id="run-nomad",
        requirement_revision=requirement.revision,
        requirement_artifact_uri=requirement_ref.uri,
        requirement_hash=requirement_ref.sha256,
        retrieval_policy_version=policy.policy_version,
        confirmed_by_user=True,
    )

    result = RetrievalStageRunner(
        adapter=adapter,
        artifact_store=store,
        policy=policy,
    ).run(requirement, stage_input)

    assert result.schema_version == "agent01-contract-v2"
    assert result.status is StageStatus.SUCCEEDED
    coverage = json.loads(
        (
            tmp_path
            / "stages"
            / "agent01"
            / "run-nomad"
            / "source_property_coverage.json"
        ).read_text(encoding="utf-8")
    )
    assert coverage["source_database"] == "nomad"
    assert "flat_band_bandwidth" in coverage["not_judged_at_agent01"]
    assert coverage["agent01_native_properties"]["band_gap"]["method"] == (
        "NOMAD parsed archive; method varies by entry"
    )
    manifest_path = (
        tmp_path
        / "stages"
        / "agent01"
        / "run-nomad"
        / "candidate_manifest.jsonl"
    )
    # Missing NOMAD hull evidence is preserved for downstream review rather
    # than being treated as an Agent01 rejection.
    manifest = [
        json.loads(line)
        for line in manifest_path.read_text(encoding="utf-8").splitlines()
        if line
    ]
    assert len(manifest) == 1
    assert manifest[0]["decision"] == "UNCERTAIN"
    assert manifest[0]["published_downstream"] is True
    assert result.candidate_ids == [manifest[0]["candidate_id"]]


def test_nomad_adapter_rejects_repeated_pagination_cursor(
    requirement, requirement_hash
) -> None:
    session = FakeSession(
        [
            _page([_nomad_entry()], next_cursor="same"),
            _page([_nomad_entry("nomad-entry-2")], next_cursor="same"),
        ]
    )
    adapter = NomadAdapter(session=session)
    policy = RetrievalPolicy(
        policy_version="retrieval-policy-nomad-v1",
        source_database=SourceDatabase.NOMAD,
        endpoint="/entries/archive/query",
        chunk_size=1,
        max_records_scanned=3,
        retry_base_seconds=0,
    )
    plan = build_query_plan(
        requirement,
        requirement_hash,
        adapter.metadata(),
        policy,
    )

    with pytest.raises(ValueError, match="cursor repeated"):
        adapter.search(plan)
