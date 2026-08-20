from __future__ import annotations

import json
from pathlib import Path

from pymatgen.core import Lattice, Structure

from material_agent.inspiration.opencitations import OpenCitationsPublicAdapter
from material_agent.inspiration.research_tools import (
    AuthoritativeLiteratureSearchArgsV1,
    AuthoritativeLiteratureSearchState,
    FederatedCandidateSearchArgsV1,
    FederatedCandidateSearchState,
)
from material_agent.inspiration.search import (
    RawSearchPage,
    SearchAttemptRecord,
)
from material_agent.inspiration.semantic_scholar import SemanticScholarPublicAdapter
from material_agent.retrieval.models import SourceDatabase, SourceMetadata
from material_agent.retrieval.query import (
    CORE_FIELDS,
    MULTI_SOURCE_REQUIRED_FIELDS,
    NOMAD_REQUIRED_FIELDS,
)
from material_agent.retrieval.storage import LocalArtifactStore


class Adapter:
    network_access = False
    component = None

    def search(self, query, **kwargs):
        del kwargs
        payload = json.dumps(
            {
                "status": "ok",
                "message": {
                    "items": [
                        {
                            "DOI": "10.1234/FLAT",
                            "title": ["Layered transition metal material"],
                            "published": {"date-parts": [[2025]]},
                            "URL": "https://doi.org/10.1234/FLAT",
                            "abstract": "<p>A layered material candidate.</p>",
                        }
                    ]
                },
            }
        ).encode()
        return RawSearchPage(
            provider="crossref",
            query_id=query.query_id,
            payload=payload,
            attempts=(
                SearchAttemptRecord(
                    query_id=query.query_id,
                    attempt_number=1,
                    outcome="success",
                    error_code=None,
                    http_status=200,
                    retry_delay_seconds=0,
                    pacing_delay_seconds=0,
                    response_bytes=len(payload),
                ),
            ),
        )


class _SequenceTransport:
    def __init__(self, payloads: list[bytes]) -> None:
        self.payloads = payloads
        self.calls: list[str] = []

    def get(self, url: str, **kwargs: object) -> bytes:
        del kwargs
        self.calls.append(url)
        return self.payloads[len(self.calls) - 1]


def test_authoritative_search_persists_raw_before_returning_evidence(
    tmp_path: Path,
) -> None:
    state = AuthoritativeLiteratureSearchState(
        adapter=Adapter(),
        store=LocalArtifactStore(tmp_path),
        run_id="run-1",
        max_calls=2,
        max_physical_requests=2,
    )
    result = state.as_tool().handler(
        AuthoritativeLiteratureSearchArgsV1(
            query="layered transition metal flat band",
            target_constraint_ids=("constraint-layered",),
            max_hits=5,
        )
    )

    assert result["evidence_scope"] == "METADATA_OR_ABSTRACT_ONLY"
    assert len(state.snapshot()) == 1
    evidence = state.snapshot()[0]
    assert evidence.doi == "10.1234/flat"
    assert evidence.document_id.startswith("document-")
    assert evidence.source_providers == ("crossref",)
    assert evidence.raw_response_uri.startswith("artifact://research/run-1/raw_search/")
    raw_path = tmp_path / evidence.raw_response_uri.removeprefix("artifact://")
    assert raw_path.is_file()
    assert "Layered transition metal material" in raw_path.read_text()


def test_citation_graph_cross_checks_providers_and_closes_native_lead(
    tmp_path: Path,
) -> None:
    paper = {
        "paperId": "s2-citing",
        "externalIds": {"DOI": "10.1000/CITING"},
        "url": "https://doi.org/10.1000/citing",
        "title": "Citing layered flat-band study",
        "abstract": "A metadata-only abstract.",
        "venue": "Test Journal",
        "year": 2025,
        "authors": [{"authorId": "1", "name": "Ada Example"}],
        "fieldsOfStudy": ["Materials Science"],
        "citationCount": 2,
        "referenceCount": 3,
    }
    semantic_transport = _SequenceTransport(
        [json.dumps({"data": [{"citingPaper": paper}]}).encode()]
    )
    opencitations_transport = _SequenceTransport(
        [
            json.dumps(
                [
                    {
                        "citing": "omid:br/1 doi:10.1000/citing",
                        "cited": "omid:br/2 doi:10.1000/anchor",
                    }
                ]
            ).encode(),
            json.dumps(
                [
                    {
                        "id": "doi:10.1000/citing omid:br/1",
                        "title": "Citing layered flat-band study",
                        "author": "Example, Ada",
                        "pub_date": "2025",
                        "venue": "Test Journal",
                        "type": "journal article",
                    }
                ]
            ).encode(),
        ]
    )
    leads = (
        {
            "lead_id": "lead-" + "1" * 24,
            "title": "Citing layered flat-band study",
            "url": "https://doi.org/10.1000/citing",
        },
    )
    state = AuthoritativeLiteratureSearchState(
        adapter=Adapter(),
        store=LocalArtifactStore(tmp_path),
        run_id="graph-run",
        max_calls=2,
        max_physical_requests=4,
        semantic_scholar_adapter=SemanticScholarPublicAdapter(
            transport=semantic_transport
        ),
        opencitations_adapter=OpenCitationsPublicAdapter(
            transport=opencitations_transport
        ),
        native_leads_snapshot=lambda: leads,
    )

    result = state.as_tool().handler(
        AuthoritativeLiteratureSearchArgsV1(
            query="papers citing the anchor flat-band mechanism",
            target_constraint_ids=("constraint-flat-band",),
            max_hits=5,
            route="CITATIONS",
            anchor_paper_id="10.1000/anchor",
        )
    )

    assert result["provider_failures"] == []
    assert len(state.snapshot()) == 1
    evidence = state.snapshot()[0]
    assert evidence.source_providers == ("semantic-scholar", "opencitations")
    assert evidence.source_lead_ids == ("lead-" + "1" * 24,)
    resolution = state.lead_resolutions_snapshot()[0]
    assert resolution.status == "RESOLVED"
    assert resolution.doi == "10.1000/citing"
    assert resolution.document_id == evidence.document_id
    assert resolution.evidence_id == evidence.evidence_id
    assert resolution.resolution_method == "DOI_URL"


def test_federated_candidate_tool_merges_sources_and_preserves_failures(
    tmp_path: Path,
) -> None:
    structure = Structure(
        Lattice.hexagonal(3.4, 20.0),
        ["Ti", "S", "S"],
        [[0, 0, 0.5], [1 / 3, 2 / 3, 0.55], [2 / 3, 1 / 3, 0.45]],
    )

    class Adapter:
        def __init__(self, source: SourceDatabase, material_id: str) -> None:
            self.source = source
            self.material_id = material_id

        def metadata(self):
            fields = (
                NOMAD_REQUIRED_FIELDS
                if self.source is SourceDatabase.NOMAD
                else sorted(set(CORE_FIELDS + MULTI_SOURCE_REQUIRED_FIELDS))
            )
            return SourceMetadata(
                database_version=f"fixture-{self.source.value}-v1",
                client_version="fixture",
                available_fields=fields,
            )

        def search(self, plan):
            assert plan.source_database is self.source
            return [
                {
                    "material_id": self.material_id,
                    "formula_pretty": "TiS2",
                    "elements": ["S", "Ti"],
                    "nelements": 2,
                    "nsites": 3,
                    "structure": structure.as_dict(),
                    "band_gap": 0.0,
                    "formation_energy_per_atom": -1.2,
                    "energy_above_hull": 0.01,
                    "is_metal": True,
                    "deprecated": False,
                    "theoretical": True,
                    "origins": [{"name": "structure", "task_id": self.material_id}],
                    "last_updated": None,
                    "source_provenance": {"uid": self.material_id},
                    "source_response": {"download_json": {"fixture": True}},
                }
            ]

    class MissingMp:
        def metadata(self):
            raise RuntimeError(
                "Materials Project API key is unavailable from the configured secret sources"
            )

    state = FederatedCandidateSearchState(
        store=LocalArtifactStore(tmp_path),
        run_id="db-run",
        adapters={
            SourceDatabase.C2DB: Adapter(SourceDatabase.C2DB, "TiS2-c2db"),
            SourceDatabase.MC3D: Adapter(SourceDatabase.MC3D, "TiS2-mc3d"),
            SourceDatabase.NOMAD: Adapter(SourceDatabase.NOMAD, "TiS2-nomad"),
            SourceDatabase.MATERIALS_PROJECT: MissingMp(),
        },
        max_calls=1,
    )
    result = state.as_tool().handler(
        FederatedCandidateSearchArgsV1(
            required_elements=("Ti",),
            excluded_elements=(),
            exact_formula="",
            max_candidates=1,
        )
    )
    assert len(result["records"]) == 1
    record = state.snapshot()[0]
    assert record.source_database == "c2db"
    assert {item.source_database for item in record.source_records} == {
        "c2db",
        "mc3d",
        "nomad",
    }
    assert record.transition_metals == ("Ti",)
    assert all(
        item.formation_energy_ev_atom == -1.2
        and item.energy_above_hull_ev_atom == 0.01
        for item in record.source_records
    )
    assert record.flat_band_status == "UNKNOWN"
    assert record.fermi_ordering_status == "UNKNOWN"
    assert record.orbital_character_status == "UNKNOWN"
    assert record.oxidation_state_status == "UNKNOWN"
    assert (
        tmp_path / record.structure_artifact_uri.removeprefix("artifact://")
    ).is_file()
    assert (
        tmp_path / record.raw_response_artifact_uri.removeprefix("artifact://")
    ).is_file()
    audit = state.federation_snapshot()
    assert audit.enabled_sources == (
        "c2db",
        "mc3d",
        "nomad",
        "materials_project",
    )
    assert audit.source_record_count == 3
    assert audit.federated_candidate_count == 1
    assert audit.exact_or_equivalent_merge_count == 2
    assert [item.status for item in audit.receipts] == [
        "SUCCEEDED",
        "SUCCEEDED",
        "SUCCEEDED",
        "UNAVAILABLE_CREDENTIAL",
    ]

    restored = FederatedCandidateSearchState(
        store=LocalArtifactStore(tmp_path),
        run_id="db-run",
        adapters={
            SourceDatabase.C2DB: Adapter(SourceDatabase.C2DB, "TiS2-c2db"),
            SourceDatabase.MC3D: Adapter(SourceDatabase.MC3D, "TiS2-mc3d"),
            SourceDatabase.NOMAD: Adapter(SourceDatabase.NOMAD, "TiS2-nomad"),
            SourceDatabase.MATERIALS_PROJECT: MissingMp(),
        },
        max_calls=1,
    )
    restored.restore_snapshot(state.checkpoint_snapshot())
    assert restored.snapshot() == state.snapshot()
    assert restored.federation_snapshot() == state.federation_snapshot()
