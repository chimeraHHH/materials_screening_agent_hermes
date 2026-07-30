"""Report-only Materials Project enrichment and deterministic local rendering."""

from __future__ import annotations

import io
from collections.abc import Mapping
from typing import Any

import matplotlib
import numpy as np

matplotlib.use("Agg")
from matplotlib.figure import Figure
from matplotlib.lines import Line2D
from pymatgen.analysis.local_env import CrystalNN
from pymatgen.vis.structure_vtk import EL_COLORS
from pymatgen.symmetry.analyzer import SpacegroupAnalyzer

from material_agent.retrieval.models import CandidateRecord, MaterialsProjectReportPolicy
from material_agent.retrieval.storage import ArtifactRef, LocalArtifactStore


_SUMMARY_FIELDS = (
    ("Energy Above Hull", "energy_above_hull", "eV/atom"),
    ("Space Group", "symmetry.symbol", None),
    ("Band Gap", "band_gap", "eV"),
    ("Predicted Formation Energy", "formation_energy_per_atom", "eV/atom"),
    ("Magnetic Ordering", "ordering", None),
    ("Total Magnetization", "total_magnetization", "µB"),
    ("Density", "density", "g/cm³"),
    ("Dimensionality", "dimensionality", None),
    ("Possible Oxidation States", "possible_species", None),
)


def enrich_published_candidates(
    *,
    candidates: list[CandidateRecord],
    structures: Mapping[str, Any],
    summaries: Mapping[str, dict[str, Any]],
    adapter: Any,
    store: LocalArtifactStore,
    stage_prefix: str,
    policy: MaterialsProjectReportPolicy,
) -> tuple[list[dict[str, Any]], list[ArtifactRef], list[str], bool]:
    """Create additive report evidence only for published MP candidates.

    A failed optional endpoint marks the report stage partial but never changes
    deterministic candidate decisions, ranking, or the public manifest.
    """
    records: list[dict[str, Any]] = []
    artifacts: list[ArtifactRef] = []
    warnings: list[str] = []
    partial = False
    used_bytes = 0
    published = sorted(
        (item for item in candidates if item.published_downstream),
        key=lambda item: item.publication_rank or 0,
    )
    for index, candidate in enumerate(published):
        candidate_id = candidate.candidate_id
        structure = structures.get(candidate_id)
        entry: dict[str, Any] = {
            "schema_version": "agent01-mp-report-v1",
            "candidate_id": candidate_id,
            "material_id": candidate.source_material_id,
            "heavy_status": "SKIPPED_RANK_LIMIT" if index >= policy.heavy_candidate_limit else "PENDING",
            "sections": {},
            "assets": [],
            "warnings": [],
        }
        if structure is None:
            entry["sections"]["crystal_structure"] = {"status": "NOT_AVAILABLE"}
            entry["warnings"].append("processed structure is unavailable")
            partial = True
        else:
            try:
                symmetry = _structure_summary(structure)
                entry["sections"]["crystal_structure"] = {"status": "COMPLETE", **symmetry}
                png = render_structure_png(structure)
                if len(png) > policy.single_object_byte_limit or used_bytes + len(png) > policy.total_byte_limit:
                    entry["sections"]["crystal_structure"]["image_status"] = "SKIPPED_BYTE_LIMIT"
                    partial = True
                else:
                    ref = store.write_bytes(
                        f"{stage_prefix}/report_assets/{candidate_id}/crystal_structure.png",
                        png, "image/png", immutable=True,
                    )
                    used_bytes += ref.size_bytes
                    artifacts.append(ref)
                    entry["assets"].append(ref.model_dump(mode="json"))
                    entry["sections"]["crystal_structure"]["image_status"] = "COMPLETE"
            except Exception as exc:
                entry["sections"]["crystal_structure"] = {"status": "RENDER_FAILED"}
                entry["warnings"].append(f"structure rendering failed: {type(exc).__name__}")
                partial = True

        # Summary values are already acquired in the bounded summary search.
        summary = _candidate_summary(candidate, summaries.get(candidate.source_material_id, {}))
        entry["sections"]["summary"] = {"status": "COMPLETE", "fields": summary}
        entry["sections"]["experimental"] = _experimental_statement(
            candidate, summaries.get(candidate.source_material_id, {})
        )
        heavy = index < policy.heavy_candidate_limit
        try:
            payload = adapter.fetch_report_data(candidate.source_material_id, heavy=heavy)
            raw_ref, consumed, raw_status = _write_raw_payload(
                store, stage_prefix, candidate_id, payload, policy, used_bytes
            )
            if raw_ref is not None:
                artifacts.append(raw_ref)
                entry["raw_source"] = raw_ref.model_dump(mode="json")
                used_bytes += consumed
            else:
                entry["raw_source_status"] = raw_status
                partial = partial or raw_status == "SKIPPED_BYTE_LIMIT"
            endpoints = payload.get("endpoints", {}) if isinstance(payload, dict) else {}
            errors = payload.get("errors", {}) if isinstance(payload, dict) else {}
            entry["sections"]["phase_stability"] = _endpoint_section(summary, endpoints.get("thermo"))
            entry["sections"]["electronic_structure"] = _endpoint_section(summary, endpoints.get("electronic_structure"), heavy)
            entry["sections"]["phonon"] = _endpoint_section({}, endpoints.get("phonon"), heavy)
            entry["sections"]["spectra"] = _endpoint_section({}, {key: endpoints.get(key) for key in ("xas", "absorption")}, heavy)
            entry["sections"]["heterostructures"] = _endpoint_section({}, endpoints.get("substrates"), heavy)
            if heavy:
                for key, renderer, filename, section in (
                    ("bandstructure", render_bandstructure_png, "electronic_bandstructure.png", "electronic_structure"),
                    ("dos", render_dos_png, "electronic_dos.png", "electronic_structure"),
                    ("phonon_bandstructure", render_phonon_bandstructure_png, "phonon_bandstructure.png", "phonon"),
                    ("phonon_dos", render_phonon_dos_png, "phonon_dos.png", "phonon"),
                ):
                    if payload.get(key) is None:
                        continue
                    try:
                        image = renderer(payload[key])
                        ref = store.write_bytes(f"{stage_prefix}/report_assets/{candidate_id}/{filename}", image, "image/png", immutable=True)
                        artifacts.append(ref)
                        entry["assets"].append(ref.model_dump(mode="json"))
                        entry["sections"][section]["image_status"] = "COMPLETE"
                    except Exception as exc:
                        entry["warnings"].append(f"{key} rendering failed: {type(exc).__name__}")
                        partial = True
            if heavy and payload.get("charge_density") is not None:
                try:
                    charge_png = render_charge_density_png(payload["charge_density"])
                    if len(charge_png) > policy.single_object_byte_limit or used_bytes + len(charge_png) > policy.total_byte_limit:
                        entry["sections"]["charge_density"] = {"status": "SKIPPED_BYTE_LIMIT"}
                        partial = True
                    else:
                        charge_ref = store.write_bytes(
                            f"{stage_prefix}/report_assets/{candidate_id}/charge_density_slices.png",
                            charge_png, "image/png", immutable=True,
                        )
                        used_bytes += charge_ref.size_bytes
                        artifacts.append(charge_ref)
                        entry["assets"].append(charge_ref.model_dump(mode="json"))
                        entry["sections"]["charge_density"] = {"status": "COMPLETE", "normalization": "mp-pyrho electrons/Å³", "slices": ["a=0.5", "b=0.5", "c=0.5"]}
                except Exception as exc:
                    entry["sections"]["charge_density"] = {"status": "RENDER_FAILED"}
                    entry["warnings"].append(f"charge density rendering failed: {type(exc).__name__}")
                    partial = True
            elif heavy:
                entry["sections"]["charge_density"] = {"status": "NOT_AVAILABLE"}
            for endpoint, error in sorted(errors.items()):
                entry["warnings"].append(f"{endpoint}: FETCH_FAILED ({error})")
                partial = True
        except Exception as exc:
            entry["warnings"].append(f"report enrichment failed: {type(exc).__name__}")
            entry["sections"]["properties"] = {"status": "FETCH_FAILED"}
            partial = True
        if entry["warnings"]:
            warnings.extend(f"{candidate.source_material_id}: {warning}" for warning in entry["warnings"])
        records.append(entry)
    return records, artifacts, warnings, partial


def _candidate_summary(candidate: CandidateRecord, source_summary: Mapping[str, Any]) -> list[dict[str, Any]]:
    values = {item.name: item.value for item in candidate.properties}
    result = []
    for label, key, unit in _SUMMARY_FIELDS:
        value = values.get(key, _nested_value(source_summary, key))
        result.append({"label": label, "value": value if value is not None else "NOT_AVAILABLE", "unit": unit})
    return result


def _nested_value(value: Mapping[str, Any], dotted_key: str) -> Any:
    current: Any = value
    for part in dotted_key.split("."):
        if not isinstance(current, Mapping):
            return None
        current = current.get(part)
    return current


def _experimental_statement(candidate: CandidateRecord, source_summary: Mapping[str, Any]) -> dict[str, Any]:
    theoretical = next((p.value for p in candidate.properties if p.name == "theoretical"), None)
    if theoretical is None:
        theoretical = source_summary.get("theoretical")
    if theoretical is False:
        return {"status": "COMPLETE", "text": "Materials Project provenance marks this record as matched to an experimental database; this does not validate individual computed properties experimentally."}
    return {"status": "NOT_AVAILABLE", "text": "No matched experimental-database provenance was returned by Materials Project."}


def _structure_summary(structure: Any) -> dict[str, Any]:
    analyzer = SpacegroupAnalyzer(structure, symprec=0.1, angle_tolerance=5.0)
    conventional = analyzer.get_conventional_standard_structure()
    dataset = analyzer.get_symmetry_dataset()
    wyckoffs = list(dataset.wyckoffs)
    hall_number = dataset.hall_number
    return {
        "derived_with": {"symprec_angstrom": 0.1, "angle_tolerance_degrees": 5.0},
        "lattice_conventional": conventional.lattice.matrix.tolist(),
        "lattice_parameters": {"a": conventional.lattice.a, "b": conventional.lattice.b, "c": conventional.lattice.c, "alpha": conventional.lattice.alpha, "beta": conventional.lattice.beta, "gamma": conventional.lattice.gamma},
        "number_of_atoms_retrieved": len(structure),
        "number_of_atoms_conventional": len(conventional),
        "wyckoff_positions": wyckoffs,
        "symmetry": {"crystal_system": analyzer.get_crystal_system(), "lattice_system": analyzer.get_lattice_type(), "hall_number": hall_number, "international_number": analyzer.get_space_group_number(), "symbol": analyzer.get_space_group_symbol(), "point_group": analyzer.get_point_group_symbol()},
    }


def render_structure_png(structure: Any) -> bytes:
    """Render a labelled, VESTA-coloured conventional-cell structure view."""
    fig = Figure(figsize=(8.2, 6.0), dpi=180)
    axis = fig.add_subplot(111, projection="3d")
    coordinates = structure.cart_coords
    symbols = [site.specie.symbol for site in structure]
    palette = {
        symbol: tuple(component / 255 for component in EL_COLORS["VESTA"].get(symbol, [128, 128, 128]))
        for symbol in sorted(set(symbols))
    }
    radii = {
        symbol: max(45.0, float(structure[site_index].specie.atomic_radius or 1.0) * 85)
        for site_index, symbol in enumerate(symbols)
    }
    for symbol in sorted(palette):
        indices = [index for index, item in enumerate(symbols) if item == symbol]
        axis.scatter(
            coordinates[indices, 0], coordinates[indices, 1], coordinates[indices, 2],
            color=palette[symbol], s=radii[symbol], label=symbol, depthshade=True,
            edgecolors="#1f2937", linewidths=0.45,
        )
    corners = structure.lattice.get_cartesian_coords([[i, j, k] for i in (0, 1) for j in (0, 1) for k in (0, 1)])
    for start, end in ((0,1),(0,2),(0,4),(1,3),(1,5),(2,3),(2,6),(3,7),(4,5),(4,6),(5,7),(6,7)):
        axis.plot(*zip(corners[start], corners[end]), color="#4b5563", linewidth=0.7)
    # CrystalNN is deliberately best-effort: a bonding failure must not hide a structure.
    try:
        bonded = CrystalNN().get_bonded_structure(structure)
        seen_bonds: set[tuple[int, int, tuple[int, int, int]]] = set()
        for site_index in range(len(structure)):
            for neighbor in bonded.get_connected_sites(site_index):
                image = tuple(int(value) for value in neighbor.jimage)
                reverse = (neighbor.index, site_index, tuple(-value for value in image))
                key = (site_index, neighbor.index, image)
                if key in seen_bonds or reverse in seen_bonds:
                    continue
                seen_bonds.add(key)
                # Keep periodic bonds readable in a single-cell figure: use the
                # corresponding atom in the displayed cell instead of expanding
                # plot limits to a neighbouring periodic image.
                neighbor_coordinate = coordinates[neighbor.index]
                axis.plot(
                    [coordinates[site_index, 0], neighbor_coordinate[0]],
                    [coordinates[site_index, 1], neighbor_coordinate[1]],
                    [coordinates[site_index, 2], neighbor_coordinate[2]],
                    color="#64748b", linewidth=1.2, alpha=0.72, zorder=0,
                )
    except Exception:
        pass
    # Label only symmetry-inequivalent sites; per-atom labels obscure dense cells.
    try:
        analyzer = SpacegroupAnalyzer(structure, symprec=0.1, angle_tolerance=5.0)
        symmetrized = analyzer.get_symmetrized_structure()
        for group_index, sites in enumerate(symmetrized.equivalent_sites, start=1):
            site = sites[0]
            coordinate = site.coords
            axis.text(*coordinate, f"{site.specie.symbol}{group_index}", fontsize=7, color="#111827")
    except Exception:
        pass
    axis.view_init(elev=18, azim=38)
    axis.set_axis_off()
    axis.legend(
        handles=[Line2D([0], [0], marker="o", color="w", label=symbol,
                        markerfacecolor=palette[symbol], markeredgecolor="#1f2937", markersize=8)
                 for symbol in sorted(palette)],
        title="Element", loc="upper left", bbox_to_anchor=(0.02, 0.98),
        frameon=True, framealpha=0.92, fontsize=8, title_fontsize=8,
    )
    fig.tight_layout(pad=0.2)
    output = io.BytesIO()
    fig.savefig(output, format="png", dpi=160, transparent=False)
    return output.getvalue()


def render_charge_density_png(charge_density: Any) -> bytes:
    """Render three middle planes after official mp-pyrho normalization."""
    from pyrho.charge_density import ChargeDensity

    normalized = ChargeDensity.from_pmg(charge_density).normalized_data["total"]
    fig = Figure(figsize=(10, 3.2), dpi=160)
    for axis_index, label in enumerate(("a = 0.5", "b = 0.5", "c = 0.5")):
        axis = fig.add_subplot(1, 3, axis_index + 1)
        middle = normalized.shape[axis_index] // 2
        plane = np.take(normalized, middle, axis=axis_index)
        image = axis.imshow(plane.T, origin="lower", cmap="magma", aspect="auto")
        axis.set_title(label)
        axis.set_axis_off()
        fig.colorbar(image, ax=axis, fraction=0.046, pad=0.04, label="e⁻/Å³")
    fig.tight_layout(pad=0.4)
    output = io.BytesIO()
    fig.savefig(output, format="png", dpi=160)
    return output.getvalue()


def _plotter_png(plotter: Any) -> bytes:
    axis = plotter.get_plot()
    figure = getattr(axis, "figure", axis)
    output = io.BytesIO()
    figure.savefig(output, format="png", dpi=160, bbox_inches="tight")
    return output.getvalue()


def render_bandstructure_png(bandstructure: Any) -> bytes:
    from pymatgen.electronic_structure.plotter import BSPlotter
    return _plotter_png(BSPlotter(bandstructure))


def render_dos_png(dos: Any) -> bytes:
    from pymatgen.electronic_structure.plotter import DosPlotter
    plotter = DosPlotter()
    plotter.add_dos("total", dos)
    return _plotter_png(plotter)


def render_phonon_bandstructure_png(bandstructure: Any) -> bytes:
    from pymatgen.phonon.plotter import PhononBSPlotter
    return _plotter_png(PhononBSPlotter(bandstructure))


def render_phonon_dos_png(dos: Any) -> bytes:
    from pymatgen.phonon.plotter import PhononDosPlotter
    plotter = PhononDosPlotter()
    plotter.add_dos("total", dos)
    return _plotter_png(plotter)


def _endpoint_section(summary: Any, endpoint: Any, heavy: bool = True) -> dict[str, Any]:
    if not heavy:
        return {"status": "SKIPPED_RANK_LIMIT"}
    if endpoint is None:
        return {"status": "NOT_AVAILABLE", "summary": summary}
    return {"status": "COMPLETE", "summary": summary, "record_count": len(endpoint) if isinstance(endpoint, list) else 1}


def _write_raw_payload(store: LocalArtifactStore, stage_prefix: str, candidate_id: str, payload: Any, policy: MaterialsProjectReportPolicy, used_bytes: int) -> tuple[ArtifactRef | None, int, str]:
    import gzip
    import json
    serializable = dict(payload) if isinstance(payload, dict) else payload
    if isinstance(serializable, dict) and "charge_density" in serializable:
        charge = serializable["charge_density"]
        serializable = {
            **serializable,
            "charge_density": charge.as_dict() if hasattr(charge, "as_dict") else str(charge),
        }
    if isinstance(serializable, dict):
        serializable = {
            key: value.as_dict() if hasattr(value, "as_dict") else value
            for key, value in serializable.items()
        }
    raw = json.dumps(serializable, ensure_ascii=False, sort_keys=True, default=str, separators=(",", ":")).encode("utf-8")
    compressed = gzip.compress(raw, mtime=0)
    if len(compressed) > policy.single_object_byte_limit or used_bytes + len(compressed) > policy.total_byte_limit:
        return None, 0, "SKIPPED_BYTE_LIMIT"
    return store.write_bytes(f"{stage_prefix}/report_data/{candidate_id}/materials_project.json.gz", compressed, "application/gzip", immutable=True), len(compressed), "COMPLETE"
