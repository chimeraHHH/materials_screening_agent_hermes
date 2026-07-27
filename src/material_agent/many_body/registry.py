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

    common = dict(
        supported_model_families=(ModelFamily.SINGLE_BAND_HUBBARD,),
        supported_geometry_types=(GeometryType.FINITE_GRAPH,),
        supported_dimensions=(1, 2),
        supports_real_hopping=True,
        supports_complex_hopping=False,
        supports_soc=False,
        supported_interaction_kinds=(InteractionKind.ONSITE_HUBBARD_U,),
        supported_ensembles=("CANONICAL",),
        supported_temperatures=("ZERO_T",),
        supported_boundaries=(BoundaryCondition.OPEN, BoundaryCondition.PERIODIC),
        max_sites=4,
        max_active_orbitals=4,
    )
    raw = (
        SolverCapability(
            solver_id="mock-many-body/v1",
            backend_id="mock-many-body",
            backend_version="1.0.0",
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
    )
    snapshot = tuple(_capability_payload(item) for item in raw)
    snapshot_hash = canonical_hash({"registry_version": REGISTRY_VERSION, "capabilities": snapshot})
    ref = ArtifactRef(uri=REGISTRY_URI, sha256=snapshot_hash)
    capabilities = tuple(item.model_copy(update={"registry_snapshot": ref}) for item in raw)
    return CapabilityRegistry(REGISTRY_VERSION, capabilities, snapshot_hash, canonical_json({"registry_version": REGISTRY_VERSION, "capabilities": snapshot}))


DEFAULT_REGISTRY = build_registry()


def registry_snapshot_hash(registry: CapabilityRegistry = DEFAULT_REGISTRY) -> Sha256:
    return registry.snapshot_hash
