"""Curated solver research metadata used by the Agent04 capability registry.

This is deliberately a small, versioned projection of the external research
bundle.  It describes routing and quality-gate metadata only; it does not
install, import, or execute third-party scientific software.
"""

from __future__ import annotations

from dataclasses import dataclass

from .models import canonical_hash

RESEARCH_CATALOG_VERSION = "many-body-solver-research/v1"


@dataclass(frozen=True)
class SolverResearchEntry:
    catalog_id: str
    method_family: str
    project: str
    role: str
    source_url: str
    documentation_url: str
    quality_gates: tuple[str, ...]
    limitations: tuple[str, ...]


RESEARCH_CATALOG: tuple[SolverResearchEntry, ...] = (
    SolverResearchEntry(
        catalog_id="qmc/alf-v2.4",
        method_family="QMC",
        project="ALF",
        role="finite-temperature or projective lattice-model benchmark",
        source_url="https://github.com/ALF-QMC/ALF",
        documentation_url="https://alf.physik.uni-wuerzburg.de/",
        quality_gates=(
            "average_sign",
            "warmup_and_measurement_counts",
            "autocorrelation_or_blocking_analysis",
            "error_bars",
            "random_seed_and_parallel_layout",
        ),
        limitations=(
            "fermion sign or phase problem must be measured and gated",
            "model implementation and equilibration require benchmark cases",
        ),
    ),
    SolverResearchEntry(
        catalog_id="dmrg/tenpy-v1",
        method_family="DMRG",
        project="TeNPy",
        role="one-dimensional or quasi-one-dimensional model benchmark",
        source_url="https://github.com/tenpy/tenpy",
        documentation_url="https://tenpy.readthedocs.io/",
        quality_gates=(
            "bond_dimension_schedule",
            "discarded_weight",
            "energy_variance_or_sweep_convergence",
            "boundary_conditions",
        ),
        limitations=(
            "entanglement growth limits geometry and system width",
            "orbital ordering and truncation error must be reported",
        ),
    ),
    SolverResearchEntry(
        catalog_id="dmft/solid-dmft-triqs4",
        method_family="DMFT",
        project="TRIQS/solid_dmft + TRIQS/cthyb",
        role="primary finite-temperature DFT+DMFT materials workflow",
        source_url="https://github.com/TRIQS/solid_dmft",
        documentation_url="https://triqs.github.io/solid_dmft/",
        quality_gates=(
            "causal_self_energy_and_green_function",
            "converged_density_or_chemical_potential",
            "converged_self_energy_norm",
            "stable_occupancy_and_local_moment",
            "solver_error_or_monte_carlo_statistics",
            "no_unexplained_dependence_on_initial_state",
        ),
        limitations=(
            "TRIQS ecosystem versions must be pinned as a compatible matrix",
            "correlated subspace, U/J, and double-counting policy require expert review",
            "the current Agent04 effective-model schema is not yet a material embedding schema",
        ),
    ),
)


def research_entry(catalog_id: str) -> SolverResearchEntry:
    for entry in RESEARCH_CATALOG:
        if entry.catalog_id == catalog_id:
            return entry
    raise KeyError(catalog_id)


def research_catalog_hash() -> str:
    return canonical_hash(
        {
            "version": RESEARCH_CATALOG_VERSION,
            "entries": [entry.__dict__ for entry in RESEARCH_CATALOG],
        }
    )
