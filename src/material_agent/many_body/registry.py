"""Static, versioned Agent04 solver capabilities.

The registry is deliberately data-only.  No caller, user preference, or LLM
can mutate a capability after the snapshot is constructed.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .models import (
    ArtifactRef,
    BoundaryCondition,
    EvidenceLevel,
    GeometryType,
    InteractionKind,
    ModelFamily,
    Sha256,
    SolverCapability,
    canonical_hash,
    canonical_json,
)
from .research_catalog import research_catalog_hash, research_entry

REGISTRY_VERSION = "many-body-capability-registry/v1"
REGISTRY_URI = "artifact://registry/many-body-capability-registry-v1.json"


@dataclass(frozen=True)
class CapabilityRegistry:
    version: str
    capabilities: tuple[SolverCapability, ...]
    snapshot_hash: str
    snapshot: str

    def get(self, solver_id: str) -> SolverCapability:
        for capability in self.capabilities:
            if capability.solver_id == solver_id:
                return capability
        raise KeyError(solver_id)


def _capability_payload(capability: SolverCapability) -> dict[str, Any]:
    return capability.model_dump(mode="json", exclude={"registry_snapshot"})


def build_registry() -> CapabilityRegistry:
    """Return the immutable, built-in task-3 registry."""

    common = {
        "supported_model_families": (ModelFamily.SINGLE_BAND_HUBBARD,),
        "supported_geometry_types": (GeometryType.FINITE_GRAPH,),
        "supported_dimensions": (1, 2),
        "supports_real_hopping": True,
        "supports_complex_hopping": False,
        "supports_soc": False,
        "supported_interaction_kinds": (InteractionKind.ONSITE_HUBBARD_U,),
        "supported_ensembles": ("CANONICAL",),
        "supported_temperatures": ("ZERO_T",),
        "supported_boundaries": (BoundaryCondition.OPEN, BoundaryCondition.PERIODIC),
        "max_sites": 4,
        "max_active_orbitals": 4,
    }
    raw = (
        SolverCapability(
            solver_id="mock-many-body/v1",
            backend_id="mock-many-body",
            backend_version="1.0.0",
            method_family="CONTROL_FLOW",
            is_mock=True,
            lifecycle="AVAILABLE",
            registered=True,
            executable=True,
            supported_observables=("workflow_lifecycle", "model_validation"),
            evidence_ceiling=EvidenceLevel.L1_RETRIEVED,
            limitations=(
                "control-flow simulator only",
                "produces no observables or scientific values",
                "must not be used as a scientific solver",
            ),
            registry_snapshot=ArtifactRef(uri=REGISTRY_URI, sha256="0" * 64),
            **common,
        ),
        SolverCapability(
            solver_id="exact-diagonalization/v1-planned",
            backend_id="exact-diagonalization",
            backend_version="1.0.0-planned",
            method_family="ED",
            is_mock=False,
            lifecycle="PLANNED",
            registered=False,
            executable=False,
            supported_observables=(
                "ground_state_energy", "low_lying_energies", "particle_number",
                "total_sz", "site_density", "double_occupancy",
                "spin_correlation_zz", "charge_correlation_connected",
                "spin_structure_factor_zz", "charge_structure_factor",
            ),
            evidence_ceiling=EvidenceLevel.L3_DFT_VALIDATED,
            limitations=(
                "planned capability; no backend is implemented",
                "finite cluster only; no thermodynamic-limit inference",
                "does not itself establish material-level evidence",
            ),
            registry_snapshot=ArtifactRef(uri=REGISTRY_URI, sha256="0" * 64),
            **common,
        ),
        _planned_research_capability(
            solver_id="qmc/alf-v2.4-planned",
            backend_id="alf-qmc",
            catalog_id="qmc/alf-v2.4",
            supported_dimensions=(1, 2),
            supported_boundaries=(BoundaryCondition.OPEN, BoundaryCondition.PERIODIC),
            supported_temperatures=("ZERO_T", "FINITE_T"),
            supported_observables=(
                "ground_state_energy", "energy_density", "site_density",
                "spin_correlation_zz", "charge_correlation_connected",
                "average_sign", "autocorrelation_time", "statistical_error",
            ),
        ),
        _planned_research_capability(
            solver_id="dmrg/tenpy-v1-planned",
            backend_id="tenpy",
            catalog_id="dmrg/tenpy-v1",
            supported_dimensions=(1,),
            supported_boundaries=(BoundaryCondition.OPEN,),
            supported_temperatures=("ZERO_T",),
            supported_observables=(
                "ground_state_energy", "site_density", "double_occupancy",
                "spin_correlation_zz", "charge_correlation_connected",
                "discarded_weight", "energy_variance",
            ),
        ),
        _planned_research_capability(
            solver_id="dmft/solid-dmft-triqs4-planned",
            backend_id="solid-dmft",
            catalog_id="dmft/solid-dmft-triqs4",
            supported_dimensions=(1, 2, 3),
            supported_geometry_types=(GeometryType.PERIODIC_LATTICE,),
            supported_boundaries=(BoundaryCondition.PERIODIC,),
            supported_temperatures=("FINITE_T",),
            supported_ensembles=("GRAND_CANONICAL",),
            supported_observables=(
                "self_energy", "local_green_function", "quasiparticle_weight",
                "occupancy", "local_moment", "spectral_function",
            ),
            max_sites=None,
            max_active_orbitals=None,
        ),
    )
    snapshot = tuple(_capability_payload(item) for item in raw)
    snapshot_hash = canonical_hash({"registry_version": REGISTRY_VERSION, "capabilities": snapshot})
    ref = ArtifactRef(uri=REGISTRY_URI, sha256=snapshot_hash)
    capabilities = tuple(item.model_copy(update={"registry_snapshot": ref}) for item in raw)
    return CapabilityRegistry(REGISTRY_VERSION, capabilities, snapshot_hash, canonical_json({"registry_version": REGISTRY_VERSION, "capabilities": snapshot}))


def _planned_research_capability(
    *,
    solver_id: str,
    backend_id: str,
    catalog_id: str,
    supported_dimensions: tuple[int, ...],
    supported_boundaries: tuple[BoundaryCondition, ...],
    supported_temperatures: tuple[str, ...],
    supported_ensembles: tuple[str, ...] = ("CANONICAL",),
    supported_observables: tuple[str, ...],
    supported_geometry_types: tuple[GeometryType, ...] = (GeometryType.FINITE_GRAPH,),
    max_sites: int | None = 4,
    max_active_orbitals: int | None = 4,
) -> SolverCapability:
    entry = research_entry(catalog_id)
    return SolverCapability(
        solver_id=solver_id,
        backend_id=backend_id,
        backend_version=f"{entry.project} planned",
        method_family=entry.method_family,
        research_catalog_id=entry.catalog_id,
        is_mock=False,
        lifecycle="PLANNED",
        registered=False,
        executable=False,
        supported_model_families=(ModelFamily.SINGLE_BAND_HUBBARD,),
        supported_geometry_types=supported_geometry_types,
        supported_dimensions=supported_dimensions,
        supports_real_hopping=True,
        supports_complex_hopping=False,
        supports_soc=False,
        supported_interaction_kinds=(InteractionKind.ONSITE_HUBBARD_U,),
        supported_ensembles=supported_ensembles,
        supported_temperatures=supported_temperatures,
        supported_boundaries=supported_boundaries,
        supported_observables=supported_observables,
        max_sites=max_sites,
        max_active_orbitals=max_active_orbitals,
        evidence_ceiling=EvidenceLevel.L3_DFT_VALIDATED,
        limitations=entry.limitations + (
            f"research catalog hash: {research_catalog_hash()}",
            "planned capability; no backend is implemented or registered",
            "does not itself establish material-level evidence",
        ),
        registry_snapshot=ArtifactRef(uri=REGISTRY_URI, sha256="0" * 64),
    )


DEFAULT_REGISTRY = build_registry()


def registry_snapshot_hash(registry: CapabilityRegistry = DEFAULT_REGISTRY) -> Sha256:
    return registry.snapshot_hash
