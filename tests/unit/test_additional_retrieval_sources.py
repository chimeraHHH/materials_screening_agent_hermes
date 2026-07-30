from __future__ import annotations

from copy import deepcopy
from typing import Any

from material_agent.retrieval.adapters import (
    C2dbAdapter,
    InMemoryMaterialsAdapter,
    Mc3dAdapter,
    NimsSuperconAdapter,
    TopologicalQuantumChemistryAdapter,
)
from material_agent.retrieval.models import (
    RetrievalStageInput,
    SourceDatabase,
    SourceMetadata,
)
from material_agent.retrieval.query import (
    MULTI_SOURCE_REQUIRED_FIELDS,
    build_query_plan,
    retrieval_policy_for_source,
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

    c2db = build_query_plan(
        requirement,
        "a" * 64,
        _metadata(),
        retrieval_policy_for_source(SourceDatabase.C2DB),
    )
    assert c2db.pushdown_filters["band_gap"] == (0.5, 1.0)
    assert c2db.pushdown_filters["energy_above_hull"] == (0.0, 0.05)


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
Si Si1 0 0 0
O O1 0.5 0.5 0.5
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
                    "chemicalFormulaSum": "Si1 O1",
                    "cifContent": {"modifiedCifContent": cif},
                    "topologicalClassification": {
                        "shortDescription": "TI",
                        "description": "Topological insulator",
                    },
                    "topologicalSubClassification": {
                        "shortDescription": "SEBR"
                    },
                    "indexCompounds": {"items": [{"value": 1}]},
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
    assert documents[0]["band_gap"] is None
    assert (
        documents[0]["source_provenance"]["topological_classification"][
            "shortDescription"
        ]
        == "TI"
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
