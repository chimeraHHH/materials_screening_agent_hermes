"""Evidence-honest Markdown and figure reporting for generic research runs."""

from __future__ import annotations

import gzip
import io
import math
import posixpath
from collections.abc import Mapping
from typing import Any, Literal

import matplotlib
import numpy as np

matplotlib.use("Agg")

from matplotlib.figure import Figure
from matplotlib.lines import Line2D
from pydantic import Field
from pymatgen.analysis.local_env import CrystalNN
from pymatgen.core import Structure
from pymatgen.electronic_structure.plotter import BSPlotter
from pymatgen.vis.structure_vtk import EL_COLORS

from material_agent.inspiration.models import canonical_json_bytes
from material_agent.inspiration.research_graph import (
    DatabaseCandidateV1,
    DatabaseSourceRecordV1,
    MaterialsResearchGraphResultV4,
)
from material_agent.orchestrator.models import StrictModel
from material_agent.retrieval.mp_screening import DeepEndpoint
from material_agent.retrieval.storage import ArtifactRef, LocalArtifactStore


class ResearchReportAssetV1(StrictModel):
    asset_kind: Literal[
        "STRUCTURE_THREE_VIEW",
        "SCALAR_OVERVIEW",
        "BAND_STRUCTURE",
        "BAND_STRUCTURE_DATA",
    ]
    status: Literal["COMPLETE", "NOT_AVAILABLE", "FETCH_FAILED", "RENDER_FAILED"]
    candidate_id: str | None = None
    source_database: str | None = None
    source_material_id: str | None = None
    artifact_uri: str | None = Field(default=None, pattern=r"^artifact://")
    artifact_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    note: str = Field(min_length=1, max_length=1_000)


class ResearchMarkdownReportV3(StrictModel):
    schema_version: Literal["materials-generic-research-report-v3"] = (
        "materials-generic-research-report-v3"
    )
    run_id: str
    markdown_artifact_uri: str = Field(pattern=r"^artifact://")
    markdown_artifact_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    manifest_artifact_uri: str = Field(pattern=r"^artifact://")
    assets: tuple[ResearchReportAssetV1, ...]
    rendered_structure_count: int = Field(ge=0)
    rendered_band_structure_count: int = Field(ge=0)
    scalar_record_count: int = Field(ge=0)


def build_generic_research_markdown_report(
    *,
    graph: MaterialsResearchGraphResultV4,
    store: LocalArtifactStore,
    run_id: str,
    materials_project_adapter: Any | None = None,
    c2db_adapter: Any | None = None,
) -> ResearchMarkdownReportV3:
    """Render a sidecar report without turning missing plots into fake evidence."""

    prefix = f"generic_research/{run_id}"
    asset_prefix = f"{prefix}/report_assets_v3"
    data_prefix = f"{prefix}/report_data_v3"
    manifest_uri = f"artifact://{prefix}/report_manifest_v3.json"
    if store.exists(manifest_uri):
        existing = ResearchMarkdownReportV3.model_validate(
            store.read_json(manifest_uri)
        )
        markdown_ref = store.inspect(
            existing.markdown_artifact_uri, media_type="text/markdown"
        )
        if markdown_ref.sha256 != existing.markdown_artifact_sha256:
            raise ValueError("generic research Markdown report hash mismatch")
        for asset in existing.assets:
            if asset.status != "COMPLETE" or asset.artifact_uri is None:
                continue
            inspected = store.inspect(asset.artifact_uri)
            if inspected.sha256 != asset.artifact_sha256:
                raise ValueError(
                    f"generic research report asset hash mismatch: {asset.asset_kind}"
                )
        return existing

    assets: list[ResearchReportAssetV1] = []
    structure_assets: dict[str, ArtifactRef] = {}
    band_assets: dict[str, ArtifactRef] = {}
    band_notes: dict[str, str] = {}

    candidates_by_id = {
        item.database_candidate_id: item for item in graph.database_candidates
    }
    for candidate in graph.database_candidates:
        try:
            structure = Structure.from_str(
                store.read_bytes(candidate.structure_artifact_uri).decode("utf-8"),
                fmt="cif",
            )
            ref = store.write_bytes(
                f"{asset_prefix}/{candidate.database_candidate_id}/"
                "structure_three_view.png",
                render_structure_three_view_png(structure, title=candidate.formula),
                "image/png",
                immutable=True,
            )
            structure_assets[candidate.database_candidate_id] = ref
            assets.append(
                _asset(
                    "STRUCTURE_THREE_VIEW",
                    "COMPLETE",
                    candidate=candidate,
                    ref=ref,
                    note="由候选的规范化 CIF 本地渲染，分别沿 a、b、c 晶轴观察。",
                )
            )
        except Exception as exc:  # noqa: BLE001 - isolate one malformed candidate
            assets.append(
                _asset(
                    "STRUCTURE_THREE_VIEW",
                    "RENDER_FAILED",
                    candidate=candidate,
                    note=f"CIF 三视图渲染失败：{type(exc).__name__}。",
                )
            )

    scalar_rows = _collect_scalar_rows(graph.database_candidates, store)
    if scalar_rows:
        scalar_ref = store.write_bytes(
            f"{asset_prefix}/scalar_properties.png",
            render_scalar_overview_png(scalar_rows),
            "image/png",
            immutable=True,
        )
        assets.append(
            ResearchReportAssetV1(
                asset_kind="SCALAR_OVERVIEW",
                status="COMPLETE",
                artifact_uri=scalar_ref.uri,
                artifact_sha256=scalar_ref.sha256,
                note="形成能、凸包距离与带隙均来自各数据库记录；缺失值未插补。",
            )
        )

    for candidate in graph.database_candidates:
        mp_record = next(
            (
                record
                for record in candidate.source_records
                if record.source_database == "materials_project"
            ),
            None,
        )
        c2db_record = next(
            (
                record
                for record in candidate.source_records
                if record.source_database == "c2db"
            ),
            None,
        )
        if mp_record is not None and materials_project_adapter is not None:
            _fetch_mp_band_asset(
                adapter=materials_project_adapter,
                candidate=candidate,
                source_record=mp_record,
                store=store,
                asset_prefix=asset_prefix,
                data_prefix=data_prefix,
                assets=assets,
                band_assets=band_assets,
                band_notes=band_notes,
            )
            continue
        if c2db_record is not None and c2db_adapter is not None:
            _fetch_c2db_band_asset(
                adapter=c2db_adapter,
                candidate=candidate,
                source_record=c2db_record,
                store=store,
                asset_prefix=asset_prefix,
                data_prefix=data_prefix,
                assets=assets,
                band_assets=band_assets,
                band_notes=band_notes,
            )
            continue
        if mp_record is not None:
            note = "Materials Project 报告适配器不可用，未抓取路径能带。"
            band_notes[candidate.database_candidate_id] = note
            assets.append(
                _asset(
                    "BAND_STRUCTURE",
                    "NOT_AVAILABLE",
                    candidate=candidate,
                    source_record=mp_record,
                    note=note,
                )
            )
            continue
        if c2db_record is not None:
            note = "C2DB 报告适配器不可用，未抓取官方 PBE 能带。"
            band_notes[candidate.database_candidate_id] = note
            assets.append(
                _asset(
                    "BAND_STRUCTURE", "NOT_AVAILABLE", candidate=candidate,
                    source_record=c2db_record, note=note,
                )
            )
            continue
        note = "当前联邦记录没有提供可审计能带对象的数据源。"
        band_notes[candidate.database_candidate_id] = note
        assets.append(
            _asset(
                "BAND_STRUCTURE", "NOT_AVAILABLE", candidate=candidate, note=note,
            )
        )

    markdown_path = f"{prefix}/report-v2.md"
    markdown = _render_markdown(
        graph=graph,
        candidates_by_id=candidates_by_id,
        structure_assets=structure_assets,
        band_assets=band_assets,
        band_notes=band_notes,
        scalar_rows=scalar_rows,
        scalar_asset=next(
            (
                item
                for item in assets
                if item.asset_kind == "SCALAR_OVERVIEW"
                and item.status == "COMPLETE"
            ),
            None,
        ),
        report_path=markdown_path,
        run_id=run_id,
    )
    markdown_ref = store.write_text(
        markdown_path, markdown, "text/markdown", immutable=True
    )
    manifest_payload = {
        "schema_version": "materials-generic-research-report-v3",
        "run_id": run_id,
        "markdown_artifact_uri": markdown_ref.uri,
        "markdown_artifact_sha256": markdown_ref.sha256,
        "manifest_artifact_uri": manifest_uri,
        "assets": [item.model_dump(mode="json") for item in assets],
        "rendered_structure_count": len(structure_assets),
        "rendered_band_structure_count": len(band_assets),
        "scalar_record_count": len(scalar_rows),
    }
    store.write_json(
        manifest_uri.removeprefix("artifact://"), manifest_payload, immutable=True
    )
    return ResearchMarkdownReportV3.model_validate(manifest_payload)


def render_structure_three_view_png(structure: Structure, *, title: str) -> bytes:
    """Render labelled orthographic projections along the three lattice vectors."""

    fig = Figure(figsize=(12.0, 4.2), dpi=180)
    coordinates = np.asarray(structure.cart_coords, dtype=float)
    lattice = np.asarray(structure.lattice.matrix, dtype=float)
    symbols = [site.specie.symbol for site in structure]
    palette = {
        symbol: tuple(
            component / 255
            for component in EL_COLORS["VESTA"].get(symbol, [128, 128, 128])
        )
        for symbol in sorted(set(symbols))
    }
    bonds = _within_cell_bonds(structure)
    corners = structure.lattice.get_cartesian_coords(
        [[i, j, k] for i in (0, 1) for j in (0, 1) for k in (0, 1)]
    )
    edges = ((0, 1), (0, 2), (0, 4), (1, 3), (1, 5), (2, 3), (2, 6),
             (3, 7), (4, 5), (4, 6), (5, 7), (6, 7))
    for axis_index, axis_name in enumerate(("a", "b", "c")):
        axis = fig.add_subplot(1, 3, axis_index + 1)
        u, v = _projection_basis(lattice, axis_index)
        projected = np.column_stack((coordinates @ u, coordinates @ v))
        projected_corners = np.column_stack((corners @ u, corners @ v))
        for start, end in edges:
            axis.plot(
                projected_corners[[start, end], 0],
                projected_corners[[start, end], 1],
                color="#64748b",
                linewidth=0.8,
                zorder=0,
            )
        for start, end in bonds:
            axis.plot(
                projected[[start, end], 0],
                projected[[start, end], 1],
                color="#94a3b8",
                linewidth=1.2,
                alpha=0.8,
                zorder=1,
            )
        for symbol in sorted(palette):
            indices = [i for i, value in enumerate(symbols) if value == symbol]
            sizes = [
                max(36.0, float(structure[i].specie.atomic_radius or 1.0) * 65)
                for i in indices
            ]
            axis.scatter(
                projected[indices, 0],
                projected[indices, 1],
                s=sizes,
                color=palette[symbol],
                edgecolors="#1f2937",
                linewidths=0.45,
                label=symbol,
                zorder=2,
            )
        axis.set_title(f"View along {axis_name}", fontsize=11, weight="semibold")
        axis.set_aspect("equal", adjustable="datalim")
        axis.margins(0.12)
        axis.set_axis_off()
    fig.suptitle(f"{title} — CIF crystallographic three-view", fontsize=13, weight="bold")
    fig.legend(
        handles=[
            Line2D(
                [0], [0], marker="o", color="w", label=symbol,
                markerfacecolor=palette[symbol], markeredgecolor="#1f2937",
                markersize=7,
            )
            for symbol in sorted(palette)
        ],
        loc="lower center",
        ncol=max(1, len(palette)),
        frameon=False,
    )
    fig.tight_layout(rect=(0, 0.08, 1, 0.92), pad=0.5)
    output = io.BytesIO()
    fig.savefig(output, format="png", dpi=180, facecolor="white")
    return output.getvalue()


def render_scalar_overview_png(rows: list[dict[str, Any]]) -> bytes:
    """Render source-resolved scalar properties without imputing missing values."""

    fig = Figure(figsize=(max(10.0, len(rows) * 1.15), 7.2), dpi=170)
    properties = (
        ("formation_energy_ev_atom", "Formation energy", "eV/atom", "#2563eb"),
        ("energy_above_hull_ev_atom", "Energy above hull", "eV/atom", "#dc2626"),
        ("band_gap_ev", "Band gap", "eV", "#059669"),
    )
    x = np.arange(len(rows), dtype=float)
    labels = [row["label"] for row in rows]
    for index, (key, title, unit, color) in enumerate(properties, start=1):
        axis = fig.add_subplot(3, 1, index)
        values = [row.get(key) for row in rows]
        available_x = [position for position, value in zip(x, values) if value is not None]
        available_y = [float(value) for value in values if value is not None]
        axis.axhline(0, color="#94a3b8", linewidth=0.8)
        if available_x:
            axis.bar(available_x, available_y, color=color, alpha=0.86, width=0.68)
            for position, value in zip(available_x, available_y):
                axis.annotate(
                    f"{value:.3f}",
                    (position, value),
                    xytext=(0, 4 if value >= 0 else -12),
                    textcoords="offset points",
                    ha="center",
                    fontsize=7,
                )
        missing_x = [position for position, value in zip(x, values) if value is None]
        if missing_x:
            axis.scatter(
                missing_x,
                [0.0] * len(missing_x),
                marker="x",
                color="#64748b",
                s=32,
                label="not available",
            )
            axis.legend(loc="upper right", frameon=False, fontsize=7)
        axis.set_ylabel(unit)
        axis.set_title(title, loc="left", fontsize=10, weight="semibold")
        axis.grid(axis="y", color="#e2e8f0", linewidth=0.7)
        axis.set_xlim(-0.6, len(rows) - 0.4)
        if index != len(properties):
            axis.set_xticks([])
        else:
            axis.set_xticks(x, labels, rotation=35, ha="right", fontsize=7)
    fig.suptitle("Federated database scalar properties (source-resolved)", weight="bold")
    fig.tight_layout(rect=(0, 0, 1, 0.96), pad=0.8)
    output = io.BytesIO()
    fig.savefig(output, format="png", dpi=170, facecolor="white")
    return output.getvalue()


def _fetch_mp_band_asset(
    *,
    adapter: Any,
    candidate: DatabaseCandidateV1,
    source_record: DatabaseSourceRecordV1,
    store: LocalArtifactStore,
    asset_prefix: str,
    data_prefix: str,
    assets: list[ResearchReportAssetV1],
    band_assets: dict[str, ArtifactRef],
    band_notes: dict[str, str],
) -> None:
    try:
        payload = adapter.fetch_deep_screen_data(
            source_record.source_material_id,
            endpoints=(DeepEndpoint.BANDSTRUCTURE_LINE,),
        )
    except Exception as exc:  # noqa: BLE001 - source-specific clients vary
        note = f"Materials Project 路径能带抓取失败：{type(exc).__name__}。"
        band_notes[candidate.database_candidate_id] = note
        assets.append(
            _asset(
                "BAND_STRUCTURE", "FETCH_FAILED", candidate=candidate,
                source_record=source_record, note=note,
            )
        )
        return
    key = DeepEndpoint.BANDSTRUCTURE_LINE.value
    bandstructure = payload.get("objects", {}).get(key)
    if bandstructure is None:
        category = payload.get("errors", {}).get(key, "NOT_RETURNED")
        note = f"Materials Project 未返回可绘制路径能带：{category}。"
        band_notes[candidate.database_candidate_id] = note
        assets.append(
            _asset(
                "BAND_STRUCTURE", "NOT_AVAILABLE", candidate=candidate,
                source_record=source_record, note=note,
            )
        )
        return
    try:
        axis = BSPlotter(bandstructure).get_plot()
        figure = getattr(axis, "figure", axis)
        figure.suptitle(
            f"{candidate.formula} — Materials Project {source_record.source_material_id}",
            fontsize=12,
        )
        output = io.BytesIO()
        figure.savefig(output, format="png", dpi=180, bbox_inches="tight")
        ref = store.write_bytes(
            f"{asset_prefix}/{candidate.database_candidate_id}/"
            "materials_project_band_structure.png",
            output.getvalue(),
            "image/png",
            immutable=True,
        )
        band_assets[candidate.database_candidate_id] = ref
        note = (
            "真实 Materials Project 高对称路径能带；它属于 MP 三维记录，"
            "不自动证明单层平带或 ≤50 meV 带宽。"
        )
        band_notes[candidate.database_candidate_id] = note
        assets.append(
            _asset(
                "BAND_STRUCTURE", "COMPLETE", candidate=candidate,
                source_record=source_record, ref=ref, note=note,
            )
        )
        serializable = payload.get("payloads", {}).get(key)
        if serializable is not None:
            raw_ref = store.write_bytes(
                f"{data_prefix}/{candidate.database_candidate_id}/"
                "materials_project_band_structure.json.gz",
                gzip.compress(canonical_json_bytes(serializable), mtime=0),
                "application/gzip",
                immutable=True,
            )
            assets.append(
                _asset(
                    "BAND_STRUCTURE_DATA", "COMPLETE", candidate=candidate,
                    source_record=source_record, ref=raw_ref,
                    note="绘图所用 Materials Project 路径能带的压缩原始对象。",
                )
            )
    except Exception as exc:  # noqa: BLE001 - plotting libraries vary
        note = f"已获得能带对象，但本地渲染失败：{type(exc).__name__}。"
        band_notes[candidate.database_candidate_id] = note
        assets.append(
            _asset(
                "BAND_STRUCTURE", "RENDER_FAILED", candidate=candidate,
                source_record=source_record, note=note,
            )
        )


def _fetch_c2db_band_asset(
    *,
    adapter: Any,
    candidate: DatabaseCandidateV1,
    source_record: DatabaseSourceRecordV1,
    store: LocalArtifactStore,
    asset_prefix: str,
    data_prefix: str,
    assets: list[ResearchReportAssetV1],
    band_assets: dict[str, ArtifactRef],
    band_notes: dict[str, str],
) -> None:
    try:
        payload = adapter.fetch_plotly_bandstructure(source_record.source_material_id)
    except Exception as exc:  # noqa: BLE001 - source-specific clients vary
        note = f"C2DB 官方 PBE 能带抓取失败：{type(exc).__name__}。"
        band_notes[candidate.database_candidate_id] = note
        assets.append(
            _asset(
                "BAND_STRUCTURE", "FETCH_FAILED", candidate=candidate,
                source_record=source_record, note=note,
            )
        )
        return
    try:
        image = render_c2db_plotly_bandstructure_png(
            payload["plotly"],
            title=f"{candidate.formula} — C2DB {source_record.source_material_id}",
        )
        ref = store.write_bytes(
            f"{asset_prefix}/{candidate.database_candidate_id}/"
            "c2db_pbe_band_structure.png",
            image,
            "image/png",
            immutable=True,
        )
        raw_ref = store.write_bytes(
            f"{data_prefix}/{candidate.database_candidate_id}/"
            "c2db_pbe_band_structure.json.gz",
            gzip.compress(canonical_json_bytes(payload), mtime=0),
            "application/gzip",
            immutable=True,
        )
        band_assets[candidate.database_candidate_id] = ref
        note = (
            "真实 C2DB GPAW/PBE 高对称路径能带；原始 Plotly 数值已压缩保存。"
            "图本身不自动证明目标平带带宽 ≤50 meV，仍需程序化逐带分析。"
        )
        band_notes[candidate.database_candidate_id] = note
        assets.extend(
            [
                _asset(
                    "BAND_STRUCTURE", "COMPLETE", candidate=candidate,
                    source_record=source_record, ref=ref, note=note,
                ),
                _asset(
                    "BAND_STRUCTURE_DATA", "COMPLETE", candidate=candidate,
                    source_record=source_record, ref=raw_ref,
                    note="绘图所用 C2DB GPAW/PBE Plotly 数值的压缩原始对象。",
                ),
            ]
        )
    except Exception as exc:  # noqa: BLE001 - plotting libraries vary
        note = f"已获得 C2DB 能带数值，但本地渲染失败：{type(exc).__name__}。"
        band_notes[candidate.database_candidate_id] = note
        assets.append(
            _asset(
                "BAND_STRUCTURE", "RENDER_FAILED", candidate=candidate,
                source_record=source_record, note=note,
            )
        )


def render_c2db_plotly_bandstructure_png(
    graph: Mapping[str, Any], *, title: str
) -> bytes:
    """Render only numeric line traces from the official C2DB Plotly object."""

    traces = graph.get("data")
    layout = graph.get("layout")
    if not isinstance(traces, list) or not isinstance(layout, Mapping):
        raise TypeError("C2DB Plotly object is missing data or layout")
    fig = Figure(figsize=(7.2, 5.4), dpi=180)
    axis = fig.add_subplot(111)
    plotted = 0
    for trace in traces:
        if not isinstance(trace, Mapping):
            continue
        x = trace.get("x")
        y = trace.get("y")
        if not isinstance(x, list) or not isinstance(y, list) or len(x) != len(y):
            continue
        try:
            x_values = np.asarray(x, dtype=float)
            y_values = np.asarray(y, dtype=float)
        except (TypeError, ValueError):
            continue
        if not len(x_values) or not np.all(np.isfinite(x_values)) or not np.all(
            np.isfinite(y_values)
        ):
            continue
        color = trace.get("line", {}).get("color") if isinstance(trace.get("line"), Mapping) else None
        axis.plot(
            x_values,
            y_values,
            color=color if isinstance(color, str) else "#2563eb",
            linewidth=0.75,
            alpha=0.88,
        )
        plotted += 1
    if not plotted:
        raise ValueError("C2DB Plotly object contains no numeric band traces")
    xaxis = layout.get("xaxis") if isinstance(layout.get("xaxis"), Mapping) else {}
    yaxis = layout.get("yaxis") if isinstance(layout.get("yaxis"), Mapping) else {}
    ticks = xaxis.get("tickvals")
    tick_labels = xaxis.get("ticktext")
    if isinstance(ticks, list) and isinstance(tick_labels, list) and len(ticks) == len(tick_labels):
        numeric_ticks = [float(value) for value in ticks]
        axis.set_xticks(numeric_ticks, [str(value) for value in tick_labels])
        for value in numeric_ticks:
            axis.axvline(value, color="#cbd5e1", linewidth=0.7, zorder=0)
    x_range = xaxis.get("range")
    y_range = yaxis.get("range")
    if isinstance(x_range, list) and len(x_range) == 2:
        axis.set_xlim(float(x_range[0]), float(x_range[1]))
    if isinstance(y_range, list) and len(y_range) == 2:
        axis.set_ylim(float(y_range[0]), float(y_range[1]))
    axis.axhline(0.0, color="#dc2626", linestyle="--", linewidth=0.9)
    axis.set_xlabel("k-path")
    axis.set_ylabel("Energy referenced by C2DB [eV]")
    axis.set_title(title, fontsize=11, weight="bold")
    axis.grid(axis="y", color="#e2e8f0", linewidth=0.6)
    fig.tight_layout(pad=0.8)
    output = io.BytesIO()
    fig.savefig(output, format="png", dpi=180, facecolor="white")
    return output.getvalue()


def _collect_scalar_rows(
    candidates: tuple[DatabaseCandidateV1, ...], store: LocalArtifactStore
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for candidate in candidates:
        for record in candidate.source_records:
            raw: Mapping[str, Any] = {}
            try:
                value = store.read_json(record.raw_response_artifact_uri)
                if isinstance(value, Mapping):
                    raw = value
            except (OSError, ValueError):
                pass
            table = raw.get("table_row") if isinstance(raw.get("table_row"), Mapping) else {}
            formation = record.formation_energy_ev_atom
            if formation is None:
                formation = _optional_float(raw.get("formation_energy_per_atom"))
            if formation is None:
                # C2DB labels this database value as heat of formation.
                formation = _optional_float(table.get("heat_of_formation"))
            hull = record.energy_above_hull_ev_atom
            if hull is None:
                hull = _optional_float(raw.get("energy_above_hull"))
            if hull is None:
                hull = _optional_float(table.get("energy_above_hull"))
            gap = record.band_gap_ev
            if gap is None:
                gap = _optional_float(raw.get("band_gap"))
            if gap is None:
                gap = _optional_float(table.get("band_gap"))
            rows.append(
                {
                    "database_candidate_id": candidate.database_candidate_id,
                    "formula": candidate.formula,
                    "source_database": record.source_database,
                    "source_material_id": record.source_material_id,
                    "label": f"{candidate.formula}\n{record.source_database}",
                    "formation_energy_ev_atom": formation,
                    "energy_above_hull_ev_atom": hull,
                    "band_gap_ev": gap,
                }
            )
    return rows


def _render_markdown(
    *,
    graph: MaterialsResearchGraphResultV4,
    candidates_by_id: Mapping[str, DatabaseCandidateV1],
    structure_assets: Mapping[str, ArtifactRef],
    band_assets: Mapping[str, ArtifactRef],
    band_notes: Mapping[str, str],
    scalar_rows: list[dict[str, Any]],
    scalar_asset: ResearchReportAssetV1 | None,
    report_path: str,
    run_id: str,
) -> str:
    lines = [
        "---",
        f"run_id: {run_id}",
        "schema_version: materials-generic-research-report-v3",
        "conclusion_status: REASONED_HYPOTHESIS",
        "property_verification_complete: false",
        "---",
        "",
        "# 过渡金属二维平带材料：联邦数据库研究报告",
        "",
        "> 证据边界：结构图由数据库 CIF 直接渲染；形成能、凸包距离和带隙只展示数据库真实字段；能带只展示实际返回的 band-structure 对象。缺失项不会由模型补画。",
        "",
        "## 科学结论",
        "",
        graph.synthesis.scientific_conclusion,
        "",
        "结论状态：`REASONED_HYPOTHESIS`；性质验证完成：`false`。",
        "",
        "## 文献证据与原生线索闭环",
        "",
        "| Evidence ID | Document ID | DOI/arXiv/记录 ID | 来源 | 标题 |",
        "|---|---|---|---|---|",
        *[
            (
                f"| `{item.evidence_id}` | `{item.document_id}` | "
                f"`{item.doi or item.arxiv_id or item.stable_record_id}` | "
                f"{', '.join(f'`{provider}`' for provider in item.source_providers)} | "
                f"{_escape_table(item.title)} |"
            )
            for item in graph.resolved_evidence
        ],
        "",
        "| Lead ID | 状态 | DOI | Document ID | Evidence ID | 解析方法 |",
        "|---|---|---|---|---|---|",
        *[
            (
                f"| `{item.lead_id}` | `{item.status}` | "
                f"{f'`{item.doi}`' if item.doi else 'N/A'} | "
                f"{f'`{item.document_id}`' if item.document_id else 'N/A'} | "
                f"{f'`{item.evidence_id}`' if item.evidence_id else 'N/A'} | "
                f"`{item.resolution_method}` |"
            )
            for item in graph.lead_evidence_resolutions
        ],
        "",
        "### 开放全文定位摘录",
        "",
        *[
            line
            for evidence in graph.resolved_evidence
            for span in evidence.full_text_spans[:8]
            for line in (
                f"- `{evidence.evidence_id}` · {span.locator}",
                f"  > {span.text_excerpt}",
                (
                    "  "
                    + " · ".join(
                        link
                        for link in (
                            f"[PDF]({_relative_link(span.pdf_artifact_uri, report_path)})"
                            if span.pdf_artifact_uri
                            else "",
                            f"[GROBID TEI]({_relative_link(span.tei_artifact_uri, report_path)})"
                            if span.tei_artifact_uri
                            else "",
                        )
                        if link
                    )
                ),
                "",
            )
        ],
        "### 文献图像、图注与原始来源",
        "",
        *[
            line
            for evidence in graph.resolved_evidence
            for figure in evidence.literature_figures
            for line in (
                (
                    f"![{_escape_table(figure.label or figure.figure_id)}]"
                    f"({_relative_link(figure.image_artifact_uri, report_path)})"
                    if figure.image_artifact_uri
                    else ""
                ),
                f"**{figure.label or 'Figure'}** — {figure.caption}",
                (
                    f"[原始 OA PDF · page {figure.page_number or 'N/A'}]"
                    f"({_relative_link(figure.source_pdf_artifact_uri, report_path)})"
                ),
                "",
            )
            if line
        ],
        "## 数据库标量性质总览",
        "",
    ]
    if scalar_asset and scalar_asset.artifact_uri:
        lines.extend(
            [
                f"![联邦数据库形成能、凸包距离和带隙]({_relative_link(scalar_asset.artifact_uri, report_path)})",
                "",
            ]
        )
    lines.extend(
        [
            "| 化学式 | 数据源 | 材料 ID | 形成能 (eV/atom) | 凸包距离 (eV/atom) | 带隙 (eV) |",
            "|---|---|---|---:|---:|---:|",
        ]
    )
    for row in scalar_rows:
        lines.append(
            "| {formula} | {source_database} | `{source_material_id}` | {formation} | {hull} | {gap} |".format(
                **row,
                formation=_number_or_na(row["formation_energy_ev_atom"]),
                hull=_number_or_na(row["energy_above_hull_ev_atom"]),
                gap=_number_or_na(row["band_gap_ev"]),
            )
        )

    inference_by_candidate = {
        row.candidate_id: row for row in graph.inference_review.matrix
    }
    skeptic_by_candidate = {
        row.candidate_id: row for row in graph.skeptic_review.matrix
    }
    hypothesis_by_id = {
        candidate.candidate_id: candidate for candidate in graph.candidates.candidates
    }
    constraint_names = {
        item.constraint_id: item.statement for item in graph.constraints.constraints
    }
    lines.extend(["", "## 候选详情", ""])
    for rank, candidate_id in enumerate(graph.synthesis.ranked_candidate_ids, start=1):
        hypothesis = hypothesis_by_id[candidate_id]
        database_id = hypothesis.database_candidate_ids[0]
        candidate = candidates_by_id[database_id]
        inference = inference_by_candidate[candidate_id]
        skeptic = skeptic_by_candidate[candidate_id]
        lines.extend(
            [
                f"### {rank}. {hypothesis.material_name}",
                "",
                f"- 联邦候选 ID：`{database_id}`",
                f"- 推理优先级分数：{inference.overall_promise_score:.2f}",
                f"- 数据源：{', '.join(f'`{r.source_database}:{r.source_material_id}`' for r in candidate.source_records)}",
                f"- 结构维度：{candidate.dimensionality if candidate.dimensionality is not None else 'N/A'}；过渡金属连通代理：{_number_or_na(candidate.connected_transition_metal_sublattice_proxy)}",
                "",
            ]
        )
        structure_ref = structure_assets.get(database_id)
        if structure_ref:
            lines.extend(
                [
                    f"![{candidate.formula} CIF 三视图]({_relative_link(structure_ref.uri, report_path)})",
                    "",
                    f"[下载规范化 CIF]({_relative_link(candidate.structure_artifact_uri, report_path)})",
                    "",
                ]
            )
        band_ref = band_assets.get(database_id)
        lines.extend(["#### 能带", ""])
        if band_ref:
            lines.extend(
                [
                    f"![{candidate.formula} 真实能带]({_relative_link(band_ref.uri, report_path)})",
                    "",
                ]
            )
        lines.extend([band_notes.get(database_id, "未获得可审计能带数据。"), ""])
        evidence = {item.constraint_id: item for item in skeptic.assessments}
        predicted = {item.constraint_id: item for item in inference.assessments}
        lines.extend(
            [
                "#### 逐约束证据与科学推断",
                "",
                "| 约束 | 证据判定 | 推理预测 | P(pass) |",
                "|---|---|---|---:|",
            ]
        )
        for constraint_id, constraint_name in constraint_names.items():
            evidence_item = evidence[constraint_id]
            predicted_item = predicted[constraint_id]
            lines.append(
                f"| {_escape_table(constraint_name)} | `{evidence_item.verdict.value}` | `{predicted_item.predicted_verdict.value}` | {predicted_item.probability_pass:.2f} |"
            )
        lines.extend(
            [
                "",
                f"推理假设：{inference.scientific_hypothesis}",
                "",
                f"最高信息增益验证：{inference.highest_information_gain_test}",
                "",
            ]
        )
    lines.extend(
        [
            "## 必需的下一步计算",
            "",
            *[f"- {item}" for item in graph.synthesis.required_next_computations],
            "",
            "## 图表可解释性说明",
            "",
            "- `×` 表示数据库没有返回该标量，不代表数值为零。",
            "- Materials Project 能带对应其数据库体相/计算任务；若目标是剥离后的二维单层，仍需对报告中的 CIF 建立真空层后重新进行自洽 DFT、SOC/磁性设置、路径能带与 PDOS 计算。",
            "- 形成能不能单独证明动力学稳定，凸包距离也不能证明平带；二者只作为候选可合成性/热力学优先级证据。",
            "",
        ]
    )
    return "\n".join(lines)


def _within_cell_bonds(structure: Structure) -> tuple[tuple[int, int], ...]:
    try:
        bonded = CrystalNN().get_bonded_structure(structure)
    except Exception:  # noqa: BLE001 - bonding is best-effort visualization
        return ()
    values: set[tuple[int, int]] = set()
    for start in range(len(structure)):
        for neighbor in bonded.get_connected_sites(start):
            if tuple(int(value) for value in neighbor.jimage) != (0, 0, 0):
                continue
            values.add(tuple(sorted((start, int(neighbor.index)))))
    return tuple(sorted(values))


def _projection_basis(lattice: np.ndarray, axis_index: int) -> tuple[np.ndarray, np.ndarray]:
    normal = lattice[axis_index] / np.linalg.norm(lattice[axis_index])
    reference = lattice[(axis_index + 1) % 3]
    u = reference - np.dot(reference, normal) * normal
    if np.linalg.norm(u) < 1e-8:
        reference = np.eye(3)[int(np.argmin(np.abs(normal)))]
        u = reference - np.dot(reference, normal) * normal
    u = u / np.linalg.norm(u)
    v = np.cross(normal, u)
    v = v / np.linalg.norm(v)
    return u, v


def _asset(
    kind: Literal["STRUCTURE_THREE_VIEW", "BAND_STRUCTURE", "BAND_STRUCTURE_DATA"],
    status: Literal["COMPLETE", "NOT_AVAILABLE", "FETCH_FAILED", "RENDER_FAILED"],
    *,
    candidate: DatabaseCandidateV1,
    note: str,
    source_record: DatabaseSourceRecordV1 | None = None,
    ref: ArtifactRef | None = None,
) -> ResearchReportAssetV1:
    return ResearchReportAssetV1(
        asset_kind=kind,
        status=status,
        candidate_id=candidate.database_candidate_id,
        source_database=(source_record.source_database if source_record else None),
        source_material_id=(source_record.source_material_id if source_record else None),
        artifact_uri=(ref.uri if ref else None),
        artifact_sha256=(ref.sha256 if ref else None),
        note=note,
    )


def _optional_float(value: Any) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def _number_or_na(value: Any) -> str:
    parsed = _optional_float(value)
    return "N/A" if parsed is None else f"{parsed:.4f}"


def _relative_link(uri: str, report_path: str) -> str:
    target = uri.removeprefix("artifact://")
    return posixpath.relpath(target, posixpath.dirname(report_path))


def _escape_table(value: str) -> str:
    return value.replace("|", "\\|").replace("\n", " ")
