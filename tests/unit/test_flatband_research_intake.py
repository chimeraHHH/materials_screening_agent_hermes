from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from material_agent.research.flatband_contracts import ObservedBandClass
from material_agent.research.flatband_intake import (
    FlatbandCalibrationIntakeManifestV1,
    fetch_c2db_calibration_intake,
    read_struct2flat_calibration_seeds,
    select_flatband_calibration_records,
)
from material_agent.research.flatband_intake_bands import (
    preaudit_c2db_band_page,
)
from material_agent.research.flatband_intake_bands_v2 import (
    preaudit_c2db_band_page_fermi_v2,
)
from material_agent.research.flatband_intake_structures import (
    FlatbandCalibrationStructureCustodyManifestV1,
    seal_selected_calibration_structures,
)


class _FakeResponse:
    def __init__(self, *, url: str, content: bytes, error: bool = False) -> None:
        self.url = url
        self.content = content
        self.text = content.decode("utf-8")
        self._error = error

    def raise_for_status(self) -> None:
        if self._error:
            raise RuntimeError("source unavailable")


class _FakeSession:
    def __init__(self, responses: dict[str, _FakeResponse]) -> None:
        self.responses = responses

    def get(
        self, url: str, *, timeout: float, headers: dict[str, str]
    ) -> _FakeResponse:
        assert timeout > 0
        assert "materials-screening-agent" in headers["User-Agent"]
        return self.responses[url]


def _seed_file(path: Path) -> Path:
    path.write_text(
        "Key\tPredicted Flatness Score\tif_kag\n"
        "1SrI2-3\t1.057973\t0\n"
        "1BeAs2F4O4-1\t1.035987\t1\n",
        "utf-8",
    )
    return path


def _band_page() -> bytes:
    return (
        "<html><script>var graphs = "
        '{"data":[{"x":[0,1],"y":[0.0,0.1]}]};'
        "Plotly.newPlot('bandstructure', graphs, {});</script></html>"
    ).encode()


def _structure_json() -> bytes:
    return json.dumps(
        {
            "1": {
                "numbers": [38, 53, 53],
                "positions": [
                    [0.0, 0.0, 9.8],
                    [0.0, 2.6, 11.6],
                    [2.6, 0.0, 8.0],
                ],
                "cell": [
                    [5.2, 0.0, 0.0],
                    [0.0, 5.2, 0.0],
                    [0.0, 0.0, 20.0],
                ],
                "pbc": [True, True, False],
            }
        }
    ).encode()


def test_calibration_intake_freezes_real_source_bytes_without_authorizing_pilot(
    tmp_path: Path,
) -> None:
    seed_path = _seed_file(tmp_path / "seeds.tsv")
    seeds = read_struct2flat_calibration_seeds(seed_path, max_records=2)
    responses: dict[str, _FakeResponse] = {}
    for seed in seeds:
        base = f"https://c2db.fysik.dtu.dk/material/{seed.source_record_id}"
        responses[base] = _FakeResponse(url=base, content=_band_page())
        responses[f"{base}/download/json"] = _FakeResponse(
            url=f"{base}/download/json",
            content=json.dumps({"1": {"numbers": [38, 53, 53]}}).encode(),
        )
    manifest = fetch_c2db_calibration_intake(
        seeds=seeds,
        seed_file_sha256=hashlib.sha256(seed_path.read_bytes()).hexdigest(),
        output_root=tmp_path / "intake",
        session=_FakeSession(responses),
        retrieved_at="2026-08-26T12:00:00+00:00",
    )

    assert all(item.status == "COMPLETE" for item in manifest.records)
    assert manifest.calibration_only is True
    assert manifest.pilot_execution_authorized is False
    assert (tmp_path / "intake/manifest.json").is_file()
    assert (tmp_path / "intake/manifest.json").stat().st_mode & 0o777 == 0o600
    assert (
        tmp_path / "intake/records/1SrI2-3/structure.json"
    ).stat().st_mode & 0o777 == 0o600
    assert FlatbandCalibrationIntakeManifestV1.model_validate_json(
        (tmp_path / "intake/manifest.json").read_bytes()
    ) == manifest


def test_calibration_intake_preserves_partial_failures_in_denominator(
    tmp_path: Path,
) -> None:
    seed_path = _seed_file(tmp_path / "seeds.tsv")
    seed = read_struct2flat_calibration_seeds(seed_path, max_records=1)[0]
    base = f"https://c2db.fysik.dtu.dk/material/{seed.source_record_id}"
    manifest = fetch_c2db_calibration_intake(
        seeds=(seed,),
        seed_file_sha256=hashlib.sha256(seed_path.read_bytes()).hexdigest(),
        output_root=tmp_path / "intake",
        session=_FakeSession(
            {
                base: _FakeResponse(url=base, content=_band_page()),
                f"{base}/download/json": _FakeResponse(
                    url=f"{base}/download/json", content=b"{}", error=True
                ),
            }
        ),
        retrieved_at="2026-08-26T12:00:00+00:00",
    )

    assert manifest.records[0].status == "PARTIAL"
    assert len(manifest.records[0].artifacts) == 1
    assert manifest.records[0].errors


def test_seed_parser_rejects_schema_drift(tmp_path: Path) -> None:
    path = tmp_path / "seeds.tsv"
    path.write_text("wrong header\n", "utf-8")
    with pytest.raises(ValueError, match="header drifted"):
        read_struct2flat_calibration_seeds(path, max_records=1)


def test_seed_parser_supports_predeclared_replacement_window(tmp_path: Path) -> None:
    seed_path = _seed_file(tmp_path / "seeds.tsv")
    seeds = read_struct2flat_calibration_seeds(
        seed_path, max_records=1, start_rank=2
    )
    assert seeds[0].source_rank == 2
    assert seeds[0].source_record_id == "1BeAs2F4O4-1"


def test_selection_uses_lowest_complete_rank_and_preserves_partial_union(
    tmp_path: Path,
) -> None:
    seed_path = tmp_path / "seeds.tsv"
    seed_path.write_text(
        "Key\tPredicted Flatness Score\tif_kag\n"
        + "".join(
            f"record-{index:02d}\t{1.0 - index / 100:.2f}\t{index % 2}\n"
            for index in range(1, 14)
        ),
        "utf-8",
    )
    seed_sha = hashlib.sha256(seed_path.read_bytes()).hexdigest()
    first_seeds = read_struct2flat_calibration_seeds(seed_path, max_records=12)
    replacement_seed = read_struct2flat_calibration_seeds(
        seed_path, max_records=1, start_rank=13
    )
    responses: dict[str, _FakeResponse] = {}
    for seed in (*first_seeds, *replacement_seed):
        base = f"https://c2db.fysik.dtu.dk/material/{seed.source_record_id}"
        responses[base] = _FakeResponse(url=base, content=_band_page())
        responses[f"{base}/download/json"] = _FakeResponse(
            url=f"{base}/download/json",
            content=json.dumps({"1": {"numbers": [38, 53, 53]}}).encode(),
            error=seed.source_rank == 3,
        )
    session = _FakeSession(responses)
    first = fetch_c2db_calibration_intake(
        seeds=first_seeds,
        seed_file_sha256=seed_sha,
        output_root=tmp_path / "first",
        session=session,
        retrieved_at="2026-08-26T12:00:00+00:00",
    )
    replacement = fetch_c2db_calibration_intake(
        seeds=replacement_seed,
        seed_file_sha256=seed_sha,
        output_root=tmp_path / "replacement",
        session=session,
        retrieved_at="2026-08-26T12:01:00+00:00",
    )
    selection = select_flatband_calibration_records(
        intake_manifests=(first, replacement),
        output_path=tmp_path / "selection.json",
    )

    assert "record-03" not in selection.selected_record_ids
    assert selection.selected_record_ids[-1] == "record-13"
    assert selection.partial_record_ids == ("record-03",)
    assert selection.pilot_execution_authorized is False
    assert (tmp_path / "selection.json").stat().st_mode & 0o777 == 0o600


def test_selected_structures_are_sealed_in_owner_only_private_custody(
    tmp_path: Path,
) -> None:
    seed_path = tmp_path / "seeds.tsv"
    seed_path.write_text(
        "Key\tPredicted Flatness Score\tif_kag\n"
        + "".join(
            f"record-{index:02d}\t{1.0 - index / 100:.2f}\t{index % 2}\n"
            for index in range(1, 13)
        ),
        "utf-8",
    )
    seeds = read_struct2flat_calibration_seeds(seed_path, max_records=12)
    responses: dict[str, _FakeResponse] = {}
    for seed in seeds:
        base = f"https://c2db.fysik.dtu.dk/material/{seed.source_record_id}"
        responses[base] = _FakeResponse(url=base, content=_band_page())
        responses[f"{base}/download/json"] = _FakeResponse(
            url=f"{base}/download/json", content=_structure_json()
        )
    intake_root = tmp_path / "intake"
    intake = fetch_c2db_calibration_intake(
        seeds=seeds,
        seed_file_sha256=hashlib.sha256(seed_path.read_bytes()).hexdigest(),
        output_root=intake_root,
        session=_FakeSession(responses),
        retrieved_at="2026-08-26T12:00:00+00:00",
    )
    selection = select_flatband_calibration_records(
        intake_manifests=(intake,),
        output_path=tmp_path / "selection.json",
    )
    custody_root = tmp_path / "custody"
    custody, envelope = seal_selected_calibration_structures(
        selection=selection,
        intake_sources=((intake, intake_root),),
        output_root=custody_root,
        sealed_at="2026-08-26T12:05:00+00:00",
    )

    assert len(custody.records) == 12
    assert all(item.structure_grouping_axis_ready for item in custody.records)
    assert custody.pilot_execution_authorized is False
    assert envelope.artifact_id == custody.manifest_id
    assert all(
        path.stat().st_mode & 0o777 == 0o600
        for path in custody_root.rglob("*.json")
    )
    assert all(
        path.stat().st_mode & 0o777 == 0o700
        for path in (custody_root, *custody_root.rglob("*/"))
        if path.is_dir()
    )
    assert FlatbandCalibrationStructureCustodyManifestV1.model_validate(
        custody.model_dump(mode="python", round_trip=True)
    ) == custody


def test_band_preaudit_is_algorithmic_and_cannot_assign_target_class() -> None:
    graph = {
        "data": [
            {
                "name": "PBE no SOC",
                "x": [0.0, 0.5, 1.0, 0.0, 0.5, 1.0],
                "y": [-0.8, -0.75, -0.7, -0.02, 0.01, 0.03],
            }
        ],
        "layout": {"yaxis": {"title": {"text": "E - E<sub>F</sub> [eV]"}}},
    }
    page = (
        "<script>var graphs = "
        + json.dumps(graph)
        + ";Plotly.newPlot('bandstructure', graphs, {});</script>"
    ).encode()
    record = preaudit_c2db_band_page(
        page,
        source_rank=1,
        c2db_record_id="record-01",
        source_intake_manifest_sha256="a" * 64,
        band_page_sha256=hashlib.sha256(page).hexdigest(),
    )

    assert record.algorithmic_bandwidth_stratum is ObservedBandClass.FB100
    assert record.narrowest_band_index_in_window == 1
    assert record.target_class_assignment_authorized is False
    assert record.gold_label_status == "NOT_ANNOTATED"


def _fermi_band_page(
    *,
    reference: str,
    bands: list[list[float]],
    pbe_fermi: float | None = None,
    pbe_vbm: float | None = None,
) -> bytes:
    x = [0.0, 0.5, 1.0] * len(bands)
    graph = {
        "data": [
            {
                "name": "PBE no SOC",
                "x": x,
                "y": [value for band in bands for value in band],
            }
        ],
        "layout": {
            "yaxis": {"title": {"text": f"E - E<sub>{reference}</sub> [eV]"}}
        },
    }
    rows = ""
    if pbe_fermi is not None:
        rows += (
            '<td class="w-50">Fermi level wrt. vacuum (PBE) [eV]</td>'
            f'<td class="w-50">{pbe_fermi}</td>'
        )
    if pbe_vbm is not None:
        rows += (
            '<td class="w-50">VBM wrt. vacuum (PBE) [eV]</td>'
            f'<td class="w-50">{pbe_vbm}</td>'
        )
    return (
        rows
        + "<script>var graphs = "
        + json.dumps(graph)
        + ";Plotly.newPlot('bandstructure', graphs, {});</script>"
    ).encode()


def test_fermi_preaudit_rejects_vbm_flat_band_outside_true_fermi_window() -> None:
    page = _fermi_band_page(
        reference="VBM",
        bands=[[0.0, 0.05, 0.02], [3.9, 4.0, 4.1]],
        pbe_fermi=-4.6,
        pbe_vbm=-6.6,
    )

    record = preaudit_c2db_band_page_fermi_v2(
        page,
        source_rank=1,
        c2db_record_id="record-01",
        source_intake_manifest_sha256="a" * 64,
        band_page_sha256=hashlib.sha256(page).hexdigest(),
    )

    assert record.fermi_alignment_method == "PBE_VACUUM_TABLE_DIFFERENCE"
    assert record.fermi_coordinate_in_plot_e_v == pytest.approx(2.0)
    assert record.window_status == "NO_BAND_IN_FERMI_WINDOW"
    assert record.algorithmic_bandwidth_stratum is None
    assert record.target_class_assignment_authorized is False


def test_fermi_preaudit_keeps_direct_fermi_trace_non_gold() -> None:
    page = _fermi_band_page(
        reference="F",
        bands=[[-0.4, -0.35, -0.38], [1.5, 1.6, 1.55]],
    )

    record = preaudit_c2db_band_page_fermi_v2(
        page,
        source_rank=2,
        c2db_record_id="record-02",
        source_intake_manifest_sha256="b" * 64,
        band_page_sha256=hashlib.sha256(page).hexdigest(),
    )

    assert record.fermi_alignment_method == "PLOT_FERMI_ZERO"
    assert record.window_status == "BAND_FOUND_IN_FERMI_WINDOW"
    assert record.narrowest_bandwidth_e_v == pytest.approx(0.05)
    assert record.algorithmic_bandwidth_stratum is ObservedBandClass.FB100
    assert record.gold_label_status == "NOT_ANNOTATED"
