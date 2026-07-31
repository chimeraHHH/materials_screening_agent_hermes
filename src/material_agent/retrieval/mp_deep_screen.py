"""Deterministic feature extraction for bounded MP deep screening.

These functions consume already-fetched pymatgen objects or plain endpoint
payloads. They never call an LLM and return ``None`` when the source evidence
cannot support a feature.
"""

from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass
from math import isfinite
from typing import Any, Mapping

import numpy as np
from pymatgen.analysis.local_env import CrystalNN
from pymatgen.core import Element, Structure


# Frozen for this policy version; do not derive this set at runtime from a
# dependency property whose meaning could change after an environment upgrade.
TRANSITION_METALS: frozenset[str] = frozenset(
    "Sc Ti V Cr Mn Fe Co Ni Cu Zn Y Zr Nb Mo Tc Ru Rh Pd Ag Cd Hf Ta W Re Os Ir Pt Au Hg Rf Db Sg Bh Hs Mt Ds Rg Cn".split()
)


@dataclass(frozen=True)
class DeepFeature:
    name: str
    value: float | int | bool | str | None
    status: str
    method: str
    warning: str | None = None


def transition_metal_elements(elements: list[str] | tuple[str, ...]) -> list[str]:
    return sorted(set(elements) & TRANSITION_METALS)


def sampled_bandwidth_ev(
    bandstructure: Any,
    *,
    energy_window_ev: tuple[float, float] | None,
    target_band_index: int | None = None,
) -> DeepFeature:
    """Measure a band width over the available uniform k mesh.

    A window is mandatory: selecting a band merely because it happens to be
    closest to the Fermi level would silently invent the user's definition of
    "near the Fermi surface".
    """

    if bandstructure is None or energy_window_ev is None:
        return DeepFeature(
            "sampled_bandwidth_ev", None, "MISSING", "uniform_bandstructure",
            "an explicit Fermi-energy window is required",
        )
    bands = getattr(bandstructure, "bands", None)
    efermi = getattr(bandstructure, "efermi", None)
    if not bands or efermi is None:
        return DeepFeature("sampled_bandwidth_ev", None, "MISSING", "uniform_bandstructure")
    lower, upper = energy_window_ev
    candidates: list[tuple[float, int, np.ndarray]] = []
    for spin_bands in bands.values() if isinstance(bands, Mapping) else [bands]:
        array = np.asarray(spin_bands, dtype=float)
        if array.ndim != 2:
            continue
        for index, band in enumerate(array):
            finite = band[np.isfinite(band)]
            if finite.size == 0:
                continue
            relative = finite - float(efermi)
            if target_band_index is not None and index != target_band_index:
                continue
            distance = float(np.min(np.abs(relative)))
            if distance <= upper and distance >= lower:
                candidates.append((distance, index, finite))
    if not candidates:
        return DeepFeature(
            "sampled_bandwidth_ev", None, "MISSING", "uniform_bandstructure",
            "no band intersects the requested Fermi-energy window",
        )
    _, index, selected = min(candidates, key=lambda item: (item[0], item[1]))
    width = float(np.max(selected) - np.min(selected))
    return DeepFeature(
        "sampled_bandwidth_ev", width, "RESOLVED", "uniform_bandstructure",
        f"selected sampled band index {index}",
    )


def line_band_crossing_risk(
    bandstructure: Any,
    *,
    energy_window_ev: tuple[float, float] | None,
) -> DeepFeature:
    """Return a bounded path-only crossing risk score (0 is lowest risk)."""

    if bandstructure is None or energy_window_ev is None:
        return DeepFeature("band_crossing_risk", None, "MISSING", "line_bandstructure")
    bands = getattr(bandstructure, "bands", None)
    efermi = getattr(bandstructure, "efermi", None)
    if not bands or efermi is None:
        return DeepFeature("band_crossing_risk", None, "MISSING", "line_bandstructure")
    lower, upper = energy_window_ev
    risk = 0.0
    inspected = 0
    for spin_bands in bands.values() if isinstance(bands, Mapping) else [bands]:
        array = np.asarray(spin_bands, dtype=float)
        if array.ndim != 2 or array.shape[0] < 2:
            continue
        relative = array - float(efermi)
        for first, second in zip(relative[:-1], relative[1:]):
            near = np.minimum(np.abs(first), np.abs(second))
            mask = np.isfinite(near) & (near <= upper)
            if not np.any(mask):
                continue
            inspected += int(np.count_nonzero(mask))
            crossing = np.signbit(first[mask]) != np.signbit(second[mask])
            close = np.abs(first[mask] - second[mask]) < 0.05
            risk = max(risk, float(np.mean(crossing | close)))
    if inspected == 0:
        return DeepFeature("band_crossing_risk", None, "MISSING", "line_bandstructure")
    return DeepFeature(
        "band_crossing_risk", min(1.0, risk), "RESOLVED", "line_bandstructure",
        "sampled high-symmetry path; not a full Brillouin-zone proof",
    )


def common_transition_metal_valence(
    oxidation_payload: Mapping[str, Any] | None,
    *,
    elements: list[str] | tuple[str, ...],
) -> DeepFeature:
    """Check MP oxidation-state candidates against frozen pymatgen valences."""

    if not oxidation_payload:
        return DeepFeature("oxidation_common", None, "MISSING", "mp_oxidation_states")
    common: dict[str, set[int]] = {}
    for symbol in transition_metal_elements(elements):
        try:
            common[symbol] = set(int(value) for value in Element(symbol).common_oxidation_states)
        except (TypeError, ValueError):
            common[symbol] = set()
    possible = oxidation_payload.get("possible_valences") or oxidation_payload.get("possible_species")
    if not isinstance(possible, Mapping):
        return DeepFeature("oxidation_common", None, "MISSING", "mp_oxidation_states")
    observed: dict[str, set[int]] = defaultdict(set)
    for symbol, values in possible.items():
        if symbol not in common:
            continue
        if isinstance(values, (int, float, str)):
            values = [values]
        if isinstance(values, list):
            for value in values:
                try:
                    observed[symbol].add(int(float(str(value).rstrip("+-"))))
                except (TypeError, ValueError):
                    pass
    if any(not observed.get(symbol) for symbol in common):
        return DeepFeature("oxidation_common", None, "MISSING", "mp_oxidation_states")
    matched = all(values & common[symbol] for symbol, values in observed.items())
    return DeepFeature(
        "oxidation_common", bool(matched), "RESOLVED", "mp_oxidation_states",
        "mixed valence accepted when every observed TM valence is common",
    )


def periodic_connectivity_score(
    structure: Structure | None,
    *,
    contributor_elements: set[str] | None = None,
) -> DeepFeature:
    """Score whether contributor atoms form a periodic connected subgraph."""

    if structure is None:
        return DeepFeature("connected_sublattice", None, "MISSING", "crystalnn_periodic_graph")
    contributors = contributor_elements or set(structure.symbol_set)
    try:
        graph = CrystalNN().get_bonded_structure(structure)
    except Exception as exc:
        return DeepFeature("connected_sublattice", None, "MISSING", "crystalnn_periodic_graph", type(exc).__name__)
    nodes = {index for index, site in enumerate(structure) if site.specie.symbol in contributors}
    if not nodes:
        return DeepFeature("connected_sublattice", None, "MISSING", "crystalnn_periodic_graph")
    adjacency: dict[int, set[int]] = {index: set() for index in nodes}
    periodic_edges = 0
    for index in nodes:
        for neighbor in graph.get_connected_sites(index):
            if neighbor.index in nodes:
                adjacency[index].add(neighbor.index)
                if any(int(value) != 0 for value in neighbor.jimage):
                    periodic_edges += 1
    seen: set[int] = set()
    components = 0
    for root in nodes:
        if root in seen:
            continue
        components += 1
        queue = deque([root])
        seen.add(root)
        while queue:
            current = queue.popleft()
            for neighbor in adjacency[current]:
                if neighbor not in seen:
                    seen.add(neighbor)
                    queue.append(neighbor)
    score = 1.0 if components == 1 and periodic_edges > 0 else 0.0
    return DeepFeature(
        "connected_sublattice", score, "RESOLVED", "crystalnn_periodic_graph",
        "periodic edge count and connected components are structure proxies",
    )
