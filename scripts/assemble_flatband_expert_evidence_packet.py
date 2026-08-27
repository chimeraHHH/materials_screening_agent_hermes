#!/usr/bin/env python3
"""Assemble an offline, read-only evidence packet for Track A calibration."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import shutil
from dataclasses import dataclass
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np

CASES = (
    ("cal-pre-001", 6, "2AsBeO5-1", "AsBeO5"),
    ("cal-pre-002", 7, "2CRbO3-1", "CRbO3"),
    ("cal-pre-003", 11, "1BBe2O5-1", "BBe2O5"),
    ("cal-pre-004", 18, "1MgN8-1", "MgN8"),
    ("cal-pre-005", 19, "1CoRe2O8-1", "CoRe2O8"),
    ("cal-pre-006", 26, "2NiPS3-1", "NiPS3"),
    ("cal-pre-007", 30, "1CoNaAs2S6-1", "CoNaAs2S6"),
    ("cal-pre-008", 35, "1Cu3I4-1", "Cu3I4"),
    ("cal-pre-009", 38, "1NaMo2O2Cl6-1", "NaMo2O2Cl6"),
    ("cal-pre-010", 50, "2MoBr3-1", "MoBr3"),
    ("cal-pre-011", 53, "2CuF-2", "CuF"),
    ("cal-pre-012", 59, "1AgSnF6-1", "AgSnF6"),
)
SELECTION_SHA256 = "e6ac22a244cd6de30a593f3240f1228c30b4e9ac58891682e44fb3d5573b2f2c"
GUIDE_SHA256 = "ea74d6e748e84aabf8bb35ed57e358e6c0ef0030a270960a4f01eb637ceb982a"


@dataclass(frozen=True)
class BandGraph:
    x: np.ndarray
    bands: np.ndarray
    tick_values: tuple[float, ...]
    tick_labels: tuple[str, ...]
    y_title: str


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_band_graph(page: Path) -> BandGraph:
    text = page.read_text(encoding="utf-8")
    marker = "Plotly.newPlot('bandstructure', graphs, {});"
    end = text.find(marker)
    assignment = text.rfind("var graphs = ", 0, end)
    if end < 0 or assignment < 0:
        raise ValueError(f"{page}: missing C2DB Plotly band payload")
    graph, _ = json.JSONDecoder().raw_decode(
        text[assignment + len("var graphs = ") : end]
    )
    traces = [
        item
        for item in graph["data"]
        if isinstance(item, dict) and item.get("name") == "PBE no SOC"
    ]
    if len(traces) != 1:
        raise ValueError(f"{page}: PBE no SOC trace does not resolve once")
    x = np.asarray(traces[0]["x"], dtype=float)
    y = np.asarray(traces[0]["y"], dtype=float)
    boundaries = np.flatnonzero(x[1:] <= x[:-1])
    if not len(boundaries):
        raise ValueError(f"{page}: repeated band grid is missing")
    period = int(boundaries[0] + 1)
    if len(x) % period:
        raise ValueError(f"{page}: band grid shape is invalid")
    layout = graph["layout"]
    x_axis = layout["xaxis"]
    y_title = layout["yaxis"]["title"]["text"]
    return BandGraph(
        x=x[:period],
        bands=y.reshape((-1, period)),
        tick_values=tuple(float(value) for value in x_axis.get("tickvals", ())),
        tick_labels=tuple(str(value) for value in x_axis.get("ticktext", ())),
        y_title=y_title,
    )


def render_band_plot(
    *,
    graph: BandGraph,
    record: dict[str, object],
    case_id: str,
    formula: str,
    output: Path,
) -> None:
    fermi = float(record["fermi_coordinate_in_plot_e_v"])
    target_index = int(record["narrowest_band_index_in_window"])
    fig, axis = plt.subplots(figsize=(8.2, 5.4), constrained_layout=True)
    for index, band in enumerate(graph.bands):
        color = "#b8bec8"
        width = 0.55
        alpha = 0.58
        zorder = 1
        if index == target_index:
            color = "#b42318"
            width = 1.8
            alpha = 1.0
            zorder = 3
        axis.plot(
            graph.x, band, color=color, linewidth=width, alpha=alpha, zorder=zorder
        )
    axis.axhspan(fermi - 1.0, fermi + 1.0, color="#d7ecff", alpha=0.36, zorder=0)
    axis.axhline(fermi, color="#155eef", linewidth=1.4, linestyle="--", zorder=2)
    axis.set_ylim(fermi - 1.25, fermi + 1.25)
    axis.set_xlim(float(graph.x.min()), float(graph.x.max()))
    if graph.tick_values and len(graph.tick_values) == len(graph.tick_labels):
        axis.set_xticks(graph.tick_values, graph.tick_labels)
        for value in graph.tick_values:
            axis.axvline(value, color="#e4e7ec", linewidth=0.7, zorder=0)
    axis.set_xlabel("High-symmetry k path")
    axis.set_ylabel(
        graph.y_title.replace("<i>", "")
        .replace("</i>", "")
        .replace("<sub>", "_")
        .replace("</sub>", "")
    )
    axis.set_title(
        f"{case_id} · {record['c2db_record_id']} · {formula}\n"
        "PBE/no-SOC trace; blue = E_F ± 1 eV; red = mechanical candidate band"
    )
    axis.text(
        0.01,
        0.02,
        (
            f"pre-audit W={float(record['narrowest_bandwidth_e_v']):.4f} eV; "
            f"d(E_F)={float(record['distance_to_fermi_e_v']):.4f} eV; "
            "NOT GOLD"
        ),
        transform=axis.transAxes,
        fontsize=8.5,
        color="#344054",
        bbox={
            "boxstyle": "round,pad=0.3",
            "facecolor": "white",
            "alpha": 0.88,
            "edgecolor": "#d0d5dd",
        },
    )
    axis.grid(axis="y", color="#eaecf0", linewidth=0.6)
    fig.savefig(output, dpi=180)
    plt.close(fig)


def load_preaudits(
    raw_root: Path,
) -> tuple[dict[str, tuple[dict[str, object], str]], tuple[str, ...]]:
    paths = sorted(raw_root.glob("flatband-calibration-*-fermi-preaudit-20260826.json"))
    record_map: dict[str, tuple[dict[str, object], str]] = {}
    manifest_shas: list[str] = []
    for path in paths:
        payload = json.loads(path.read_text(encoding="utf-8"))
        manifest_sha = str(payload["manifest_sha256"])
        manifest_shas.append(manifest_sha)
        for record in payload["records"]:
            record_id = str(record["c2db_record_id"])
            if record_id in record_map:
                raise ValueError(f"duplicate Fermi pre-audit record: {record_id}")
            record_map[record_id] = (record, manifest_sha)
    return record_map, tuple(manifest_shas)


def unique_source(raw_root: Path, record_id: str, name: str) -> Path:
    matches = sorted(
        raw_root.glob(f"flatband-calibration-*/records/{record_id}/{name}")
    )
    if len(matches) != 1:
        raise ValueError(f"{record_id}: expected one {name}, found {len(matches)}")
    return matches[0]


def copy_read_only(source: Path, destination: Path) -> None:
    shutil.copy2(source, destination)
    destination.chmod(0o400)


def write_json_read_only(path: Path, value: object) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    path.chmod(0o400)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--raw-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    raw_root = args.raw_root.resolve()
    output = args.output.resolve()
    if output.exists():
        raise FileExistsError(f"refusing to overwrite frozen packet: {output}")
    output.mkdir(parents=True, mode=0o700)
    records, manifest_shas = load_preaudits(raw_root)
    rows: list[dict[str, object]] = []

    for case_id, rank, record_id, formula in CASES:
        if record_id not in records:
            raise ValueError(f"{record_id}: Fermi pre-audit record is missing")
        preaudit, preaudit_manifest_sha = records[record_id]
        if int(preaudit["source_rank"]) != rank:
            raise ValueError(f"{record_id}: source rank drift")
        band_page = unique_source(raw_root, record_id, "band-page.html")
        structure = band_page.with_name("structure.json")
        if not structure.is_file():
            raise FileNotFoundError(structure)
        envelope = (
            raw_root
            / "flatband-calibration-structure-diverse-custody-20260826"
            / "records"
            / record_id
            / "normalized-structure.envelope.json"
        )
        if not envelope.is_file():
            raise FileNotFoundError(envelope)
        if sha256(band_page) != preaudit["band_page_sha256"]:
            raise ValueError(f"{record_id}: band-page hash drift")

        case_dir = output / case_id
        case_dir.mkdir(mode=0o700)
        copy_read_only(band_page, case_dir / "band-page.audit-only.html")
        copy_read_only(structure, case_dir / "source-structure.json")
        copy_read_only(envelope, case_dir / "normalized-structure.envelope.json")
        plot_path = case_dir / "band-plot.png"
        render_band_plot(
            graph=load_band_graph(band_page),
            record=preaudit,
            case_id=case_id,
            formula=formula,
            output=plot_path,
        )
        plot_path.chmod(0o400)
        summary = {
            "schema_version": "flatband-expert-precase-evidence-summary-v1",
            "calibration_only": True,
            "case_id": case_id,
            "source_rank": rank,
            "c2db_record_id": record_id,
            "formula": formula,
            "fermi_preaudit": preaudit,
            "fermi_preaudit_manifest_sha256": preaudit_manifest_sha,
            "selection_manifest_sha256": SELECTION_SHA256,
            "annotation_guide_sha256": GUIDE_SHA256,
            "target_class_assignment_authorized": False,
            "gold_label_status": "NOT_ANNOTATED",
            "system_outputs_included": False,
            "scientific_conclusion": False,
        }
        write_json_read_only(case_dir / "case-summary.json", summary)
        rows.append(
            {
                "precase_id": case_id,
                "source_rank": rank,
                "c2db_record": record_id,
                "formula": formula,
                "fermi_preaudit_manifest_sha256": preaudit_manifest_sha,
                "band_page_sha256": sha256(case_dir / "band-page.audit-only.html"),
                "source_structure_sha256": sha256(case_dir / "source-structure.json"),
                "normalized_structure_envelope_sha256": sha256(
                    case_dir / "normalized-structure.envelope.json"
                ),
                "band_plot_sha256": sha256(plot_path),
                "case_summary_sha256": sha256(case_dir / "case-summary.json"),
            }
        )
        case_dir.chmod(0o500)

    index_csv = output / "EVIDENCE_INDEX.csv"
    with index_csv.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    index_csv.chmod(0o400)
    packet_manifest = {
        "schema_version": "flatband-expert-evidence-packet-manifest-v1",
        "selection_manifest_sha256": SELECTION_SHA256,
        "annotation_guide_sha256": GUIDE_SHA256,
        "fermi_preaudit_manifest_sha256s": sorted(manifest_shas),
        "case_count": len(rows),
        "evidence_index_sha256": sha256(index_csv),
        "calibration_only": True,
        "pilot_execution_authorized": False,
        "system_outputs_included": False,
        "gold_label_status": "NOT_ANNOTATED",
        "scientific_conclusion": False,
    }
    write_json_read_only(output / "PACKET_MANIFEST.json", packet_manifest)
    output.chmod(0o500)
    print(json.dumps(packet_manifest, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
