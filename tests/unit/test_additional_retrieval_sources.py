from __future__ import annotations

from copy import deepcopy
import json
from typing import Any

from material_agent.retrieval.adapters import (
    C2dbAdapter,
    InMemoryMaterialsAdapter,
    Mc3dAdapter,
    NimsSuperconAdapter,
    TopologicalQuantumChemistryAdapter,
)
from material_agent.retrieval.evaluator import evaluate_candidate
from material_agent.retrieval.models import (
    CandidateAuditRecordV2,
    Decision,
    Requirement,
    RetrievalStageInput,
    SourceDatabase,
    SourceMetadata,
    StageStatus,
)
from material_agent.retrieval.query import (
    MULTI_SOURCE_REQUIRED_FIELDS,
    NOMAD_REQUIRED_FIELDS,
    build_query_plan,
    retrieval_policy_for_source,
    select_retrieval_source,
)
from material_agent.retrieval.runner import RetrievalStageRunner
from material_agent.retrieval.storage import LocalArtifactStore


class FakeResponse:
    def __init__(
        self,
        payload: Any = None,
        *,
        text: str = "",
        status_code: int = 200,
        headers: dict[str, str] | None = None,
    ) -> None:
        self._payload = payload
        self.text = text
        self.status_code = status_code
        self.headers = headers or {}

    def json(self) -> Any:
        return deepcopy(self._payload)

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            error = RuntimeError(f"HTTP {self.status_code}")
            error.response = self  # type: ignore[attr-defined]
            raise error


class FakeSession:
    def __init__(self, responses: list[FakeResponse]) -> None:
        self.responses = list(responses)
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def get(self, url: str, **kwargs: Any) -> FakeResponse:
        self.calls.append((url, kwargs))
        if not self.responses:
            raise AssertionError(f"unexpected GET {url}")
        return self.responses.pop(0)


def _metadata() -> SourceMetadata:
    return SourceMetadata(
        database_version="fixture-v1",
        client_version="fixture-client",
        available_fields=MULTI_SOURCE_REQUIRED_FIELDS,
    )


def test_new_source_policies_and_query_plans(requirement) -> None:
    for source in (
        SourceDatabase.MC3D,
        SourceDatabase.C2DB,
        SourceDatabase.TOPOLOGICAL_QUANTUM_CHEMISTRY,
        SourceDatabase.NIMS_SUPERCON,
    ):
        policy = retrieval_policy_for_source(source)
        plan = build_query_plan(
            requirement,
            "a" * 64,
            _metadata(),
            policy,
        )
        assert plan.source_database is source
        assert plan.policy_version == policy.policy_version
        assert plan.query_fingerprint

    mc3d = build_query_plan(
        requirement,
        "a" * 64,
        _metadata(),
        retrieval_policy_for_source(SourceDatabase.MC3D),
    )
    assert "band_gap" in mc3d.local_only_constraints
    assert "energy_above_hull" in mc3d.local_only_constraints


def test_source_selection_requires_an_explicit_user_confirmation(
    requirement,
) -> None:
    assert select_retrieval_source("nomad", requirement) is SourceDatabase.NOMAD
    import pytest

    with pytest.raises(ValueError, match="explicitly confirmed"):
        select_retrieval_source("auto", requirement)
    with pytest.raises(ValueError, match="not registered"):
        select_retrieval_source("atomly", requirement)

    c2db = build_query_plan(
        requirement,
        "a" * 64,
        _metadata(),
        retrieval_policy_for_source(SourceDatabase.C2DB),
    )
    assert c2db.pushdown_filters["band_gap"] == (0.5, 1.0)
    assert c2db.pushdown_filters["energy_above_hull"] == (0.0, 0.05)


def test_exact_formula_is_local_for_every_selectable_database(requirement) -> None:
    hard = requirement.hard_constraints.model_copy(update={"exact_formula": "SiO"})
    changed = requirement.model_copy(update={"hard_constraints": hard})
    mp_plan = build_query_plan(
        changed,
        "a" * 64,
        SourceMetadata(
            database_version="mp-fixture",
            client_version="fixture-client",
            available_fields=[
                "material_id",
                "formula_pretty",
                "chemsys",
                "elements",
                "nelements",
                "nsites",
                "structure",
                "band_gap",
                "energy_above_hull",
                "is_metal",
                "deprecated",
                "theoretical",
                "origins",
                "last_updated",
            ],
        ),
        retrieval_policy_for_source(SourceDatabase.MATERIALS_PROJECT),
    )
    assert "exact_formula" in mp_plan.local_only_constraints
    for source in (
        SourceDatabase.C2DB,
        SourceDatabase.NOMAD,
        SourceDatabase.MC3D,
        SourceDatabase.TOPOLOGICAL_QUANTUM_CHEMISTRY,
    ):
        metadata = _metadata()
        if source is SourceDatabase.NOMAD:
            metadata = SourceMetadata(
                database_version="fixture-v1",
                client_version="fixture-client",
                available_fields=NOMAD_REQUIRED_FIELDS,
            )
        plan = build_query_plan(
            changed,
            "a" * 64,
            metadata,
            retrieval_policy_for_source(source),
        )
        assert "exact_formula" in plan.local_only_constraints


def test_mc3d_optimade_adapter_maps_structure(requirement) -> None:
    session = FakeSession(
        [
            FakeResponse({"meta": {"api_version": "1.2.0"}}),
            FakeResponse(
                {
                    "data": [
                        {
                            "id": "mc3d-10",
                            "attributes": {
                                "elements": ["O", "Si"],
                                "nsites": 2,
                                "chemical_formula_reduced": "OSi",
                                "chemical_formula_descriptive": "SiO",
                                "lattice_vectors": [
                                    [4.0, 0.0, 0.0],
                                    [0.0, 4.0, 0.0],
                                    [0.0, 0.0, 4.0],
                                ],
                                "cartesian_site_positions": [
                                    [0.0, 0.0, 0.0],
                                    [2.0, 2.0, 2.0],
                                ],
                                "species_at_sites": ["Si", "O"],
                                "last_modified": "2026-01-01T00:00:00Z",
                            },
                        }
                    ],
                    "links": {"next": None},
                }
            ),
        ]
    )
    adapter = Mc3dAdapter(session=session)
    metadata = adapter.metadata()
    plan = build_query_plan(
        requirement,
        "b" * 64,
        metadata,
        retrieval_policy_for_source(SourceDatabase.MC3D),
    )
    documents = adapter.search(plan)
    assert documents[0]["material_id"] == "mc3d-10"
    assert documents[0]["elements"] == ["O", "Si"]
    assert documents[0]["band_gap"] is None
    assert documents[0]["source_response"]["id"] == "mc3d-10"
    params = session.calls[1][1]["params"]
    assert 'elements HAS ALL "O","Si"' in params["filter"]


def test_c2db_adapter_maps_table_properties_and_ase_json(
    requirement,
) -> None:
    table = """
    <input type="hidden" name="sid" value="9">
    <table><tbody>
    <tr><th scope="row"><a href=/material/2SiO-1>SiO</a></th>
    <th scope="row"><a href=/material/2SiO-1>0.02</a></th>
    <th scope="row"><a href=/material/2SiO-1>-1.2</a></th>
    <th scope="row"><a href=/material/2SiO-1>0.8</a></th>
    <th scope="row"><a href=/material/2SiO-1>No</a></th>
    <th scope="row"><a href=/material/2SiO-1>p1</a></th></tr>
    </tbody></table>
    <li class="page-item disabled"><a class="page-link"
      hx-get="/table?sid=9&page=0" title=">">&gt;</a></li>
    """
    atoms = {
        "1": {
            "numbers": [14, 8],
            "positions": [[0, 0, 5], [1, 1, 5]],
            "cell": [[3, 0, 0], [0, 3, 0], [0, 0, 15]],
            "pbc": [True, True, False],
        }
    }
    session = FakeSession(
        [
            FakeResponse(
                text="C2DB UID Band gap (PBE)",
                headers={"Last-Modified": "fixture-date"},
            ),
            FakeResponse(text='<form hx-get="/table?sid=9">'),
            FakeResponse(text=table),
            FakeResponse(atoms),
        ]
    )
    adapter = C2dbAdapter(session=session)
    plan = build_query_plan(
        requirement,
        "c" * 64,
        adapter.metadata(),
        retrieval_policy_for_source(SourceDatabase.C2DB),
    )
    documents = adapter.search(plan)
    assert documents[0]["material_id"] == "2SiO-1"
    assert documents[0]["band_gap"] == 0.8
    assert documents[0]["energy_above_hull"] == 0.02
    assert documents[0]["is_metal"] is False
    assert documents[0]["nsites"] == 2
    assert documents[0]["source_response"]["table_row"]["uid"] == "2SiO-1"
    assert documents[0]["source_response"]["download_json"] == atoms


def test_tqc_adapter_preserves_topology_as_provenance(
    requirement,
) -> None:
    cif = """
data_test
_cell_length_a 3
_cell_length_b 3
_cell_length_c 3
_cell_angle_alpha 90
_cell_angle_beta 90
_cell_angle_gamma 90
_symmetry_space_group_name_H-M 'P 1'
loop_
_atom_site_type_symbol
_atom_site_label
_atom_site_fract_x
_atom_site_fract_y
_atom_site_fract_z
Bi3+ Bi1 0 0 0
Te2- Te1 0.5 0.5 0.5
"""
    session = FakeSession(
        [
            FakeResponse({"items": [], "totalPages": 0}),
            FakeResponse(
                {
                    "items": [{"similarICSD": [123]}],
                    "totalPages": 1,
                }
            ),
            FakeResponse(
                {
                    "id": 44,
                    "type": "COMPOUND_SOC",
                    "chemicalFormulaSum": "Bi1 Te1",
                    "cifContent": {"modifiedCifContent": cif},
                    "topologicalClassification": {
                        "shortDescription": "TI",
                        "description": "Topological insulator",
                    },
                    "topologicalSubClassification": {
                        "shortDescription": "SEBR"
                    },
                    "indexCompounds": {"items": [{"value": 1}]},
                    "nbrFermiCrossing": 3,
                    "nbrFermiCrossingFirstCond": 1,
                    "nbrFermiCrossingLastVal": 2,
                    "smLineCrossing": True,
                    "smCrossingType": "nodal",
                }
            ),
        ]
    )
    adapter = TopologicalQuantumChemistryAdapter(session=session)
    plan = build_query_plan(
        requirement,
        "d" * 64,
        adapter.metadata(),
        retrieval_policy_for_source(
            SourceDatabase.TOPOLOGICAL_QUANTUM_CHEMISTRY
        ),
    )
    documents = adapter.search(plan)
    assert documents[0]["material_id"] == "icsd-123"
    assert documents[0]["elements"] == ["Bi", "Te"]
    assert documents[0]["band_gap"] is None
    assert (
        documents[0]["source_provenance"]["topological_classification"][
            "shortDescription"
        ]
        == "TI"
    )
    assert documents[0]["source_provenance"]["fermi_crossing_count"] == 3
    assert documents[0]["source_provenance"]["line_crossing_label"] == "True"
    assert documents[0]["source_response"]["detail"]["id"] == 44
    assert "data_test" in documents[0]["source_response"]["cif_content"]


def test_tqc_adapter_never_resolves_more_than_frozen_scan_limit(requirement) -> None:
    cif = """
data_test
_cell_length_a 3
_cell_length_b 3
_cell_length_c 3
_cell_angle_alpha 90
_cell_angle_beta 90
_cell_angle_gamma 90
_symmetry_space_group_name_H-M 'P 1'
loop_
_atom_site_type_symbol
_atom_site_label
_atom_site_fract_x
_atom_site_fract_y
_atom_site_fract_z
Bi Bi1 0 0 0
Te Te1 0.5 0.5 0.5
"""
    detail = lambda identifier: {
        "id": identifier,
        "chemicalFormulaSum": "Bi1 Te1",
        "cifContent": {"modifiedCifContent": cif},
        "topologicalClassification": {"shortDescription": "TI"},
        "topologicalSubClassification": {"shortDescription": "SEBR"},
        "indexCompounds": {"items": [{"value": 1}]},
        "type": "COMPOUND_SOC",
    }
    session = FakeSession(
        [
            FakeResponse({"items": [], "totalPages": 0}),
            FakeResponse(
                {
                    "items": [
                        {"similarICSD": [101, 102]},
                        {"similarICSD": [103, 104]},
                    ],
                    "totalPages": 1,
                }
            ),
            FakeResponse(detail(1)),
            FakeResponse(detail(2)),
        ]
    )
    adapter = TopologicalQuantumChemistryAdapter(session=session)
    plan = build_query_plan(
        requirement,
        "d" * 64,
        adapter.metadata(),
        retrieval_policy_for_source(
            SourceDatabase.TOPOLOGICAL_QUANTUM_CHEMISTRY
        ),
    ).model_copy(update={"max_records_scanned": 2})

    documents = adapter.search(plan)

    assert [document["material_id"] for document in documents] == [
        "icsd-101",
        "icsd-102",
    ]
    assert len(session.calls) == 4


def test_tqc_runner_evaluates_explicit_topological_database_label(
    tmp_path, requirement
) -> None:
    cif = """
data_test
_cell_length_a 3
_cell_length_b 3
_cell_length_c 3
_cell_angle_alpha 90
_cell_angle_beta 90
_cell_angle_gamma 90
_symmetry_space_group_name_H-M 'P 1'
loop_
_atom_site_type_symbol
_atom_site_label
_atom_site_fract_x
_atom_site_fract_y
_atom_site_fract_z
Bi Bi1 0 0 0
Te Te1 0.5 0.5 0.5
"""
    payload = requirement.model_dump(mode="json")
    payload["hard_constraints"] = {
        "exact_formula": None,
        "include_elements": ["Bi", "Te"],
        "exclude_elements": [],
        "band_gap_ev": None,
        "energy_above_hull_ev_atom": None,
        "is_metal": None,
        "dimensionality": None,
        "max_num_sites": None,
    }
    payload["scientific_targets"] = [
        {
            "name": "Topological materials",
            "operational_definition": (
                "non-trivial TQC classification with SOC and a topological index"
            ),
            "required_evidence_level": "L1_RETRIEVED",
        }
    ]
    tqc_requirement = Requirement.model_validate(payload)
    session = FakeSession(
        [
            FakeResponse({"items": [], "totalPages": 0}),
            FakeResponse({"items": [{"similarICSD": [123]}], "totalPages": 1}),
            FakeResponse(
                {
                    "id": 44,
                    "type": "COMPOUND_SOC",
                    "chemicalFormulaSum": "Bi1 Te1",
                    "cifContent": {"modifiedCifContent": cif},
                    "topologicalClassification": {"shortDescription": "TI"},
                    "topologicalSubClassification": {"shortDescription": "SEBR"},
                    "indexCompounds": {"items": [{"value": 1}]},
                    "nbrFermiCrossing": 2,
                    "smLineCrossing": False,
                    "smCrossingType": "none",
                }
            ),
        ]
    )
    store = LocalArtifactStore(tmp_path)
    requirement_ref = store.write_json(
        "requirements/requirement.v1.json",
        tqc_requirement.model_dump(mode="json"),
        immutable=True,
    )
    policy = retrieval_policy_for_source(
        SourceDatabase.TOPOLOGICAL_QUANTUM_CHEMISTRY
    )
    result = RetrievalStageRunner(
        adapter=TopologicalQuantumChemistryAdapter(session=session),
        artifact_store=store,
        policy=policy,
    ).run(
        tqc_requirement,
        RetrievalStageInput(
            project_id="tqc-test",
            run_id="tqc-topology-pass",
            requirement_revision=1,
            requirement_artifact_uri=requirement_ref.uri,
            requirement_hash=requirement_ref.sha256,
            retrieval_policy_version=policy.policy_version,
            confirmed_by_user=True,
        ),
    )

    assert result.status is StageStatus.SUCCEEDED
    assert result.metrics["passed"] == 1
    audit = json.loads(
        store.read_bytes(
            "artifact://stages/agent01/tqc-topology-pass/candidate_audit.jsonl"
        ).decode("utf-8")
    )
    assert audit["elements"] == ["Bi", "Te"]
    assert audit["decision"] == "PASS"
    crossing_properties = {
        property_["name"]: property_["value"]
        for property_ in audit["properties"]
        if property_["name"].startswith("tqc_")
    }
    assert crossing_properties["tqc_fermi_crossing_count"] == 2
    assert crossing_properties["tqc_line_crossing_label"] == "False"
    assert crossing_properties["tqc_crossing_type_label"] == "none"
    assert audit["scientific_target_evaluations"] == [
        {
            "name": "Topological materials",
            "required_evidence_level": "L1_RETRIEVED",
            "result": "MATCH",
            "reason_code": "SCIENTIFIC_TARGET_EVIDENCE_PRESENT",
            "observed_property": "tqc_topological_material_label",
        }
    ]
    candidate = CandidateAuditRecordV2.model_validate(audit)
    trivial = evaluate_candidate(
        candidate.model_copy(
            update={
                "properties": [
                    prop.model_copy(update={"value": False})
                    if prop.name == "tqc_topological_material_label"
                    else prop
                    for prop in candidate.properties
                ]
            }
        ),
        tqc_requirement,
        policy,
    )
    assert trivial.decision is Decision.REJECT
    assert trivial.scientific_target_evaluations[0].reason_code == (
        "TQC_TOPOLOGICAL_LABEL_NOT_MATCHED"
    )


def test_nims_supercon_adapter_is_explicitly_structureless(
    requirement,
) -> None:
    header = (
        "data number\treference number\tcommon formula of materials\t"
        "chemical formula\telement name of materials\tcomposition\tunit of Tc\t"
        "Tc recommended\n"
        "num\trefno\tname\telement\tma1\tma2\tutc\ttc\n"
        "7\tref-1\tSiO\tSi1O1\tSi\t1\tK\t20\n"
    )
    session = FakeSession(
        [
            FakeResponse(text=f"dataset {NimsSuperconAdapter.DATASET_DOI}"),
            FakeResponse(text=header),
        ]
    )
    adapter = NimsSuperconAdapter(session=session)
    requirement = requirement.model_copy(deep=True)
    requirement.hard_constraints.include_elements = ["Si"]
    requirement.hard_constraints.exclude_elements = []
    plan = build_query_plan(
        requirement,
        "e" * 64,
        adapter.metadata(),
        retrieval_policy_for_source(SourceDatabase.NIMS_SUPERCON),
    )
    documents = adapter.search(plan)
    assert documents[0]["material_id"] == "supercon-7"
    assert documents[0]["structure"] is None
    assert documents[0]["source_provenance"]["recommended_tc"] == "20"


def test_nims_structureless_records_never_publish_downstream(
    tmp_path, requirement
) -> None:
    source = SourceDatabase.NIMS_SUPERCON
    policy = retrieval_policy_for_source(source)
    store = LocalArtifactStore(tmp_path)
    requirement_ref = store.write_json(
        "requirements/requirement.v1.json",
        requirement.model_dump(mode="json"),
    )
    adapter = InMemoryMaterialsAdapter(
        [
            {
                "material_id": "supercon-7",
                "formula_pretty": "SiO",
                "elements": ["O", "Si"],
                "nsites": None,
                "structure": None,
                "band_gap": None,
                "energy_above_hull": None,
                "is_metal": None,
                "deprecated": False,
                "theoretical": False,
                "origins": [],
            }
        ],
        source_database=source,
    )
    result = RetrievalStageRunner(
        adapter=adapter,
        artifact_store=store,
        policy=policy,
    ).run(
        requirement,
        RetrievalStageInput(
            project_id="project-nims",
            run_id="run-nims",
            requirement_revision=requirement.revision,
            requirement_artifact_uri=requirement_ref.uri,
            requirement_hash=requirement_ref.sha256,
            retrieval_policy_version=policy.policy_version,
            confirmed_by_user=True,
        ),
    )
    assert result.candidate_ids == []
    assert result.metrics["failed"] == 1
    assert result.metrics["published_downstream"] == 0
