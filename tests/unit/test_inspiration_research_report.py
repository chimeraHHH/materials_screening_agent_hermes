from __future__ import annotations

from io import BytesIO
from pathlib import Path

from PIL import Image
from pymatgen.core import Lattice, Structure
from pymatgen.io.cif import CifWriter

from material_agent.inspiration.research_graph import (
    DatabaseCandidateV1,
    DatabaseSourceRecordV1,
)
from material_agent.inspiration.research_report import (
    _collect_scalar_rows,
    render_c2db_plotly_bandstructure_png,
    render_scalar_overview_png,
    render_structure_three_view_png,
)
from material_agent.retrieval.storage import LocalArtifactStore


def _structure() -> Structure:
    return Structure(
        Lattice.hexagonal(3.4, 20.0),
        ["Ti", "S", "S"],
        [[0, 0, 0.5], [1 / 3, 2 / 3, 0.55], [2 / 3, 1 / 3, 0.45]],
    )


def test_three_view_renderer_creates_readable_three_panel_png() -> None:
    payload = render_structure_three_view_png(_structure(), title="TiS2")

    image = Image.open(BytesIO(payload))
    assert image.format == "PNG"
    assert image.width > image.height * 2
    assert image.width >= 1500


def test_scalar_report_uses_source_fields_and_c2db_raw_fallback(
    tmp_path: Path,
) -> None:
    store = LocalArtifactStore(tmp_path)
    cif = store.write_text(
        "research/run/database/c2db/candidate.cif",
        str(CifWriter(_structure())),
        "chemical/x-cif",
        immutable=True,
    )
    raw = store.write_json(
        "research/run/database/c2db/candidate.raw.json",
        {
            "table_row": {
                "heat_of_formation": "-1.250",
                "energy_above_hull": "0.015",
                "band_gap": "0.400",
            }
        },
        immutable=True,
    )
    source = DatabaseSourceRecordV1(
        source_database="c2db",
        source_material_id="TiS2-test",
        source_database_version="fixture-v1",
        query_fingerprint="1" * 64,
        canonical_structure_id="str_" + "2" * 24,
        structure_artifact_uri=cif.uri,
        structure_artifact_sha256=cif.sha256,
        raw_response_artifact_uri=raw.uri,
        raw_response_artifact_sha256=raw.sha256,
        license="CC-BY-NC-4.0",
    )
    candidate = DatabaseCandidateV1(
        database_candidate_id="db-candidate-" + "3" * 24,
        source_database="c2db",
        source_material_id="TiS2-test",
        canonical_structure_id="str_" + "2" * 24,
        source_records=(source,),
        formula="TiS2",
        elements=("S", "Ti"),
        transition_metals=("Ti",),
        dimensionality=2,
        dimensionality_status="RESOLVED",
        connected_transition_metal_sublattice_proxy=1.0,
        connectivity_status="RESOLVED_PROXY",
        structure_artifact_uri=cif.uri,
        structure_artifact_sha256=cif.sha256,
        raw_response_artifact_uri=raw.uri,
        raw_response_artifact_sha256=raw.sha256,
    )

    rows = _collect_scalar_rows((candidate,), store)

    assert rows[0]["formation_energy_ev_atom"] == -1.25
    assert rows[0]["energy_above_hull_ev_atom"] == 0.015
    assert rows[0]["band_gap_ev"] == 0.4
    image = Image.open(BytesIO(render_scalar_overview_png(rows)))
    assert image.format == "PNG"
    assert image.width > 1000


def test_c2db_numeric_band_renderer_uses_official_trace_arrays() -> None:
    payload = render_c2db_plotly_bandstructure_png(
        {
            "data": [
                {"type": "scattergl", "x": [0, 0.5, 1], "y": [-1, -0.2, -1]},
                {"type": "scattergl", "x": [0, 0.5, 1], "y": [1, 1.2, 1]},
            ],
            "layout": {
                "xaxis": {"tickvals": [0, 0.5, 1], "ticktext": ["Γ", "M", "K"]},
                "yaxis": {"range": [-2, 2]},
            },
        },
        title="TiS2 — C2DB",
    )

    image = Image.open(BytesIO(payload))
    assert image.format == "PNG"
    assert image.width > image.height
