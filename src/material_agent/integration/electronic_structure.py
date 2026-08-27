"""Deterministic validators for the electronic-structure scientific DAG.

These functions analyze caller-supplied structures and numerical outputs.  They
do not run DFT, choose a functional, invent magnetic orders, or promote evidence
on their own.  Every threshold is an explicit, frozen input to the validator.
"""

from __future__ import annotations

import json
import math
from collections import deque
from enum import StrEnum
from itertools import pairwise
from typing import Literal

import numpy as np
from pydantic import Field, model_validator
from pymatgen.analysis.local_env import CrystalNN
from pymatgen.core import Element, Structure

from material_agent.inspiration.models import Identifier, StrictModel
from material_agent.retrieval.structures import calculate_dimensionality


class ValidationVerdict(StrEnum):
    PASS = "PASS"
    FAIL = "FAIL"
    INCONCLUSIVE = "INCONCLUSIVE"


class ScalarThresholdDirection(StrEnum):
    MAXIMUM = "MAXIMUM"
    MINIMUM = "MINIMUM"


class SurrogateTriageDisposition(StrEnum):
    REJECT_AT_L2 = "REJECT_AT_L2"
    RETAIN_AT_L2 = "RETAIN_AT_L2"
    ESCALATE_TO_ML_ENSEMBLE = "ESCALATE_TO_ML_ENSEMBLE"
    RETAIN_UNCERTAIN_AT_L2 = "RETAIN_UNCERTAIN_AT_L2"


class SurrogateCriterionEstimate(StrictModel):
    criterion_id: Identifier
    predicted_value: float
    calibrated_absolute_error: float = Field(ge=0)
    threshold: float
    direction: ScalarThresholdDirection

    @model_validator(mode="after")
    def finite_values(self) -> SurrogateCriterionEstimate:
        if not all(
            math.isfinite(value)
            for value in (
                self.predicted_value,
                self.calibrated_absolute_error,
                self.threshold,
            )
        ):
            raise ValueError("surrogate criterion values must be finite")
        return self


class SurrogateProbabilityCriterionEstimate(StrictModel):
    """Calibrated probability for a DeepSeek-selected scientific proposition."""

    criterion_id: Identifier
    positive_probability: float = Field(ge=0, le=1)
    calibrated_absolute_error: float = Field(ge=0, le=1)
    minimum_positive_probability: float = Field(ge=0, le=1)

    @model_validator(mode="after")
    def finite_values(self) -> SurrogateProbabilityCriterionEstimate:
        if not all(
            math.isfinite(value)
            for value in (
                self.positive_probability,
                self.calibrated_absolute_error,
                self.minimum_positive_probability,
            )
        ):
            raise ValueError("surrogate probability values must be finite")
        return self


class SurrogateCandidateEstimate(StrictModel):
    candidate_id: Identifier
    priority_score: float
    in_validated_domain: bool
    criteria: tuple[SurrogateCriterionEstimate, ...] = Field(
        default=(), max_length=128
    )
    probability_criteria: tuple[SurrogateProbabilityCriterionEstimate, ...] = Field(
        default=(), max_length=128
    )

    @model_validator(mode="after")
    def canonical_criteria(self) -> SurrogateCandidateEstimate:
        criterion_ids = tuple(
            item.criterion_id
            for item in (*self.criteria, *self.probability_criteria)
        )
        if criterion_ids != tuple(sorted(set(criterion_ids))):
            raise ValueError("surrogate criteria must be sorted and unique")
        if not criterion_ids:
            raise ValueError("surrogate candidate requires at least one criterion")
        if not math.isfinite(self.priority_score):
            raise ValueError("surrogate priority score must be finite")
        return self


class SurrogatePredictionProvenance(StrictModel):
    model_id: Identifier
    checkpoint_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    benchmark_id: Identifier
    benchmark_report_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    environment_fingerprint_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    device: str = Field(min_length=1, max_length=128)
    real_backend: Literal[True] = True
    is_mock: Literal[False] = False


class SurrogateScientificPrediction(StrictModel):
    estimate: SurrogateCandidateEstimate
    provenance: SurrogatePredictionProvenance
    scientific_conclusion: Literal[False] = False


class SurrogateTriagePolicy(StrictModel):
    ensemble_validation_fraction: float = Field(default=0.10, ge=0, le=1)
    max_ensemble_candidates: int = Field(default=32, ge=0, le=10_000)
    confirm_top_clear_passes: bool = True


class SurrogateCandidateDecision(StrictModel):
    candidate_id: Identifier
    disposition: SurrogateTriageDisposition
    reason_codes: tuple[Identifier, ...]


class SurrogateTriageResult(StrictModel):
    decisions: tuple[SurrogateCandidateDecision, ...]
    ensemble_candidate_ids: tuple[Identifier, ...]
    ensemble_quota: int = Field(ge=0)
    l2_rejected_count: int = Field(ge=0)
    l2_retained_count: int = Field(ge=0)
    deferred_count: int = Field(ge=0)
    scientific_conclusion: Literal[False] = False


def triage_surrogate_candidates(
    candidates: tuple[SurrogateCandidateEstimate, ...],
    policy: SurrogateTriagePolicy,
) -> SurrogateTriageResult:
    """Use calibrated intervals and spend a bounded second-model GPU quota."""

    candidate_ids = tuple(item.candidate_id for item in candidates)
    if len(candidate_ids) != len(set(candidate_ids)):
        raise ValueError("surrogate triage candidate IDs must be unique")
    if not candidates:
        return SurrogateTriageResult(
            decisions=(),
            ensemble_candidate_ids=(),
            ensemble_quota=0,
            l2_rejected_count=0,
            l2_retained_count=0,
            deferred_count=0,
        )

    states: dict[str, tuple[str, tuple[str, ...]]] = {}
    escalation_pool: list[SurrogateCandidateEstimate] = []
    clear_pass_pool: list[SurrogateCandidateEstimate] = []
    for candidate in candidates:
        criterion_states = tuple(
            _surrogate_criterion_state(item) for item in candidate.criteria
        ) + tuple(
            _surrogate_probability_state(item)
            for item in candidate.probability_criteria
        )
        reasons: set[str] = set()
        if not candidate.in_validated_domain:
            reasons.add("SURROGATE_OUTSIDE_VALIDATED_DOMAIN")
        if "FAIL" in criterion_states and candidate.in_validated_domain:
            reasons.add("SURROGATE_CLEAR_THRESHOLD_FAILURE")
            states[candidate.candidate_id] = ("REJECT", tuple(sorted(reasons)))
        elif not candidate.in_validated_domain or "AMBIGUOUS" in criterion_states:
            if "AMBIGUOUS" in criterion_states:
                reasons.add("SURROGATE_INTERVAL_OVERLAPS_THRESHOLD")
            states[candidate.candidate_id] = (
                "REQUEST_ENSEMBLE",
                tuple(sorted(reasons)),
            )
            escalation_pool.append(candidate)
        else:
            reasons.add("SURROGATE_CLEAR_THRESHOLD_PASS")
            states[candidate.candidate_id] = ("RETAIN", tuple(sorted(reasons)))
            clear_pass_pool.append(candidate)

    quota = min(
        policy.max_ensemble_candidates,
        math.ceil(len(candidates) * policy.ensemble_validation_fraction),
    )
    ranked_escalations = sorted(
        escalation_pool,
        key=lambda item: (-item.priority_score, item.candidate_id),
    )
    selected = ranked_escalations[:quota]
    if policy.confirm_top_clear_passes and len(selected) < quota:
        ranked_clear_passes = sorted(
            clear_pass_pool,
            key=lambda item: (-item.priority_score, item.candidate_id),
        )
        selected.extend(ranked_clear_passes[: quota - len(selected)])
    selected_ids = {item.candidate_id for item in selected}

    decisions: list[SurrogateCandidateDecision] = []
    for candidate in sorted(candidates, key=lambda item: item.candidate_id):
        state, initial_reasons = states[candidate.candidate_id]
        reasons = set(initial_reasons)
        if candidate.candidate_id in selected_ids:
            disposition = SurrogateTriageDisposition.ESCALATE_TO_ML_ENSEMBLE
            reasons.add("SELECTED_WITHIN_ML_ENSEMBLE_BUDGET")
        elif state == "REJECT":
            disposition = SurrogateTriageDisposition.REJECT_AT_L2
        elif state == "RETAIN":
            disposition = SurrogateTriageDisposition.RETAIN_AT_L2
        else:
            disposition = SurrogateTriageDisposition.RETAIN_UNCERTAIN_AT_L2
            reasons.add("ML_ENSEMBLE_BUDGET_EXHAUSTED")
        decisions.append(
            SurrogateCandidateDecision(
                candidate_id=candidate.candidate_id,
                disposition=disposition,
                reason_codes=tuple(sorted(reasons)),
            )
        )
    return SurrogateTriageResult(
        decisions=tuple(decisions),
        ensemble_candidate_ids=tuple(sorted(selected_ids)),
        ensemble_quota=quota,
        l2_rejected_count=sum(
            item.disposition is SurrogateTriageDisposition.REJECT_AT_L2
            for item in decisions
        ),
        l2_retained_count=sum(
            item.disposition is SurrogateTriageDisposition.RETAIN_AT_L2
            for item in decisions
        ),
        deferred_count=sum(
            item.disposition
            is SurrogateTriageDisposition.RETAIN_UNCERTAIN_AT_L2
            for item in decisions
        ),
    )


def _surrogate_criterion_state(
    estimate: SurrogateCriterionEstimate,
) -> Literal["PASS", "FAIL", "AMBIGUOUS"]:
    lower = estimate.predicted_value - estimate.calibrated_absolute_error
    upper = estimate.predicted_value + estimate.calibrated_absolute_error
    if estimate.direction is ScalarThresholdDirection.MAXIMUM:
        if upper <= estimate.threshold:
            return "PASS"
        if lower > estimate.threshold:
            return "FAIL"
    else:
        if lower >= estimate.threshold:
            return "PASS"
        if upper < estimate.threshold:
            return "FAIL"
    return "AMBIGUOUS"


def _surrogate_probability_state(
    estimate: SurrogateProbabilityCriterionEstimate,
) -> Literal["PASS", "FAIL", "AMBIGUOUS"]:
    lower = max(
        0.0,
        estimate.positive_probability - estimate.calibrated_absolute_error,
    )
    upper = min(
        1.0,
        estimate.positive_probability + estimate.calibrated_absolute_error,
    )
    if lower >= estimate.minimum_positive_probability:
        return "PASS"
    if upper < estimate.minimum_positive_probability:
        return "FAIL"
    return "AMBIGUOUS"


class TwoDStructurePolicy(StrictModel):
    minimum_periodic_void_gap_angstrom: float = Field(gt=0, le=100)
    require_dimensionality: Literal[2] = 2
    contributor_elements: tuple[str, ...] = Field(
        min_length=1,
        max_length=64,
        description=(
            "Only the transition-metal elements whose valence and periodically "
            "connected contributor sublattice must be assessed. Do not include "
            "ligands, alkali ions, or every element in the formula."
        ),
    )

    @model_validator(mode="after")
    def canonical_contributors(self) -> TwoDStructurePolicy:
        if self.contributor_elements != tuple(
            sorted(set(self.contributor_elements))
        ):
            raise ValueError("contributor elements must be sorted and unique")
        if any(not Element.is_valid_symbol(item) for item in self.contributor_elements):
            raise ValueError("contributor element symbol is invalid")
        return self


class OxidationAssignment(StrictModel):
    element: str
    observed_states: tuple[float, ...]
    common_states: tuple[float, ...]
    all_observed_states_common: bool
    mixed_valence: bool


class TwoDStructureAssessment(StrictModel):
    dimensionality: int | None
    dimensionality_method: str
    dimensionality_error: str | None
    periodic_void_axis: int
    largest_periodic_void_gap_angstrom: float
    has_periodic_void_gap: bool
    contributor_site_indices: tuple[int, ...]
    contributor_connected: bool | None
    contributor_periodic_edge_count: int | None
    oxidation_assignments: tuple[OxidationAssignment, ...]
    oxidation_assignment_method: Literal[
        "EXPLICIT_OR_BOND_VALENCE_GUESS", "UNRESOLVED"
    ]
    verdict: ValidationVerdict
    reason_codes: tuple[Identifier, ...]
    scientific_conclusion: Literal[False] = False


def assess_two_dimensional_structure(
    structure: Structure,
    policy: TwoDStructurePolicy,
) -> TwoDStructureAssessment:
    """Assess dimensionality, periodic void, TM connectivity and valence proxy."""

    if len(structure) == 0:
        raise ValueError("two-dimensional assessment requires at least one site")
    dimensionality = calculate_dimensionality(structure)
    axis, gap = _largest_periodic_center_gap(structure)
    contributor_indices = tuple(
        index
        for index, site in enumerate(structure)
        if site.specie.symbol in policy.contributor_elements
    )
    connected, periodic_edges = _periodic_contributor_connectivity(
        structure, contributor_indices
    )
    oxidation, method = _oxidation_assignments(
        structure, set(policy.contributor_elements)
    )
    reasons: set[str] = set()
    if dimensionality.value != policy.require_dimensionality:
        reasons.add(
            "DIMENSIONALITY_UNRESOLVED"
            if dimensionality.value is None
            else "NOT_TWO_DIMENSIONAL"
        )
    if gap < policy.minimum_periodic_void_gap_angstrom:
        reasons.add("PERIODIC_VOID_GAP_BELOW_POLICY")
    if not contributor_indices:
        reasons.add("CONTRIBUTOR_SUBLATTICE_ABSENT")
    elif connected is not True:
        reasons.add("CONTRIBUTOR_SUBLATTICE_NOT_PERIODIC_CONNECTED")
    if not oxidation:
        reasons.add("OXIDATION_ASSIGNMENT_UNRESOLVED")
    elif any(not item.all_observed_states_common for item in oxidation):
        reasons.add("UNCOMMON_TRANSITION_METAL_VALENCE")
    if not reasons:
        verdict = ValidationVerdict.PASS
        reasons.add("TWO_D_STRUCTURE_POLICY_PASSED")
    elif {
        "DIMENSIONALITY_UNRESOLVED",
        "OXIDATION_ASSIGNMENT_UNRESOLVED",
    } & reasons:
        verdict = ValidationVerdict.INCONCLUSIVE
    else:
        verdict = ValidationVerdict.FAIL
    return TwoDStructureAssessment(
        dimensionality=dimensionality.value,
        dimensionality_method=dimensionality.method,
        dimensionality_error=dimensionality.error,
        periodic_void_axis=axis,
        largest_periodic_void_gap_angstrom=gap,
        has_periodic_void_gap=(
            gap >= policy.minimum_periodic_void_gap_angstrom
        ),
        contributor_site_indices=contributor_indices,
        contributor_connected=connected,
        contributor_periodic_edge_count=periodic_edges,
        oxidation_assignments=oxidation,
        oxidation_assignment_method=method,
        verdict=verdict,
        reason_codes=tuple(sorted(reasons)),
    )


class MagneticEnumerationPolicy(StrictModel):
    magnetic_elements: tuple[str, ...] = Field(min_length=1, max_length=64)
    initial_moment_magnitude_mu_b: float = Field(gt=0, le=20)
    max_configurations: int = Field(default=16, ge=2, le=256)

    @model_validator(mode="after")
    def canonical_elements(self) -> MagneticEnumerationPolicy:
        if self.magnetic_elements != tuple(sorted(set(self.magnetic_elements))):
            raise ValueError("magnetic elements must be sorted and unique")
        if any(not Element.is_valid_symbol(item) for item in self.magnetic_elements):
            raise ValueError("magnetic element symbol is invalid")
        return self


class MagneticConfiguration(StrictModel):
    configuration_id: Identifier
    label: str
    magnetic_site_indices: tuple[int, ...]
    initial_moments_mu_b: tuple[float, ...]


class MagneticEnumeration(StrictModel):
    magnetic_site_indices: tuple[int, ...]
    configurations: tuple[MagneticConfiguration, ...]
    truncated: bool
    scientific_conclusion: Literal[False] = False


def enumerate_collinear_magnetic_configurations(
    structure: Structure,
    policy: MagneticEnumerationPolicy,
) -> MagneticEnumeration:
    indices = tuple(
        index
        for index, site in enumerate(structure)
        if site.specie.symbol in policy.magnetic_elements
    )
    if not indices:
        raise ValueError("no requested magnetic sites exist in the structure")
    patterns: list[tuple[int, ...]] = []
    total = 1 << max(len(indices) - 1, 0)
    for code in range(total):
        pattern = (1,) + tuple(
            1 if code & (1 << (offset - 1)) else -1
            for offset in range(1, len(indices))
        )
        patterns.append(pattern)
    patterns.sort(key=lambda item: (-sum(item), item), reverse=True)
    fm = tuple(1 for _ in indices)
    if fm in patterns:
        patterns.remove(fm)
    ordered = [fm, *patterns]
    selected = ordered[: policy.max_configurations]
    configurations = tuple(
        MagneticConfiguration(
            configuration_id=f"mag-config-{position:03d}",
            label=("FM" if pattern == fm else f"AFM_OR_FERRI_{position:03d}"),
            magnetic_site_indices=indices,
            initial_moments_mu_b=tuple(
                sign * policy.initial_moment_magnitude_mu_b for sign in pattern
            ),
        )
        for position, pattern in enumerate(selected)
    )
    return MagneticEnumeration(
        magnetic_site_indices=indices,
        configurations=configurations,
        truncated=len(ordered) > len(selected),
    )


class DFTTotalEnergyObservation(StrictModel):
    """One self-consistent energy tied to a frozen cell, method and spin seed."""

    result_artifact_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    source_structure_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    method_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    magnetic_configuration_id: Identifier
    total_energy_ry: float
    electronic_converged: bool
    soc_explicit: bool


class MagneticGroundStatePolicy(StrictModel):
    expected_configuration_ids: tuple[Identifier, ...] = Field(
        min_length=2,
        max_length=256,
    )
    degeneracy_tolerance_mev_per_cell: float = Field(gt=0, le=1_000)
    require_all_converged: Literal[True] = True
    require_non_soc_energy_comparison: Literal[True] = True

    @model_validator(mode="after")
    def canonical_configurations(self) -> MagneticGroundStatePolicy:
        if self.expected_configuration_ids != tuple(
            sorted(set(self.expected_configuration_ids))
        ):
            raise ValueError(
                "expected magnetic configurations must be sorted and unique"
            )
        return self


class MagneticEnergyRanking(StrictModel):
    magnetic_configuration_id: Identifier
    total_energy_ry: float
    delta_energy_mev_per_cell: float = Field(ge=0)


class MagneticGroundStateAssessment(StrictModel):
    source_structure_sha256: str | None = Field(
        default=None,
        pattern=r"^[0-9a-f]{64}$",
    )
    method_sha256: str | None = Field(
        default=None,
        pattern=r"^[0-9a-f]{64}$",
    )
    ranking: tuple[MagneticEnergyRanking, ...]
    missing_configuration_ids: tuple[Identifier, ...]
    unconverged_configuration_ids: tuple[Identifier, ...]
    degenerate_ground_state_configuration_ids: tuple[Identifier, ...]
    selected_ground_state_configuration_id: Identifier | None
    verdict: ValidationVerdict
    reason_codes: tuple[Identifier, ...]
    scientific_conclusion: Literal[False] = False


def assess_magnetic_ground_state(
    observations: tuple[DFTTotalEnergyObservation, ...],
    policy: MagneticGroundStatePolicy,
) -> MagneticGroundStateAssessment:
    """Select a magnetic ground state only from comparable converged SCF runs."""

    if not observations:
        raise ValueError("magnetic ground-state assessment requires DFT energies")
    configuration_ids = tuple(
        item.magnetic_configuration_id for item in observations
    )
    if len(configuration_ids) != len(set(configuration_ids)):
        raise ValueError("magnetic DFT observations contain duplicate configurations")
    if any(not math.isfinite(item.total_energy_ry) for item in observations):
        raise ValueError("magnetic DFT total energies must be finite")

    expected = set(policy.expected_configuration_ids)
    observed = set(configuration_ids)
    missing = tuple(sorted(expected - observed))
    unexpected = observed - expected
    structures = {item.source_structure_sha256 for item in observations}
    methods = {item.method_sha256 for item in observations}
    unconverged = tuple(
        sorted(
            item.magnetic_configuration_id
            for item in observations
            if not item.electronic_converged
        )
    )
    soc_observations = tuple(
        sorted(
            item.magnetic_configuration_id
            for item in observations
            if item.soc_explicit
        )
    )
    reasons: set[str] = set()
    if missing:
        reasons.add("MAGNETIC_CONFIGURATIONS_MISSING")
    if unexpected:
        reasons.add("UNEXPECTED_MAGNETIC_CONFIGURATIONS")
    if len(structures) != 1:
        reasons.add("MAGNETIC_ENERGIES_USE_DIFFERENT_STRUCTURES")
    if len(methods) != 1:
        reasons.add("MAGNETIC_ENERGIES_USE_DIFFERENT_METHODS")
    if unconverged:
        reasons.add("MAGNETIC_ENERGY_NOT_CONVERGED")
    if policy.require_non_soc_energy_comparison and soc_observations:
        reasons.add("MAGNETIC_ENERGY_COMPARISON_MUST_BE_NON_SOC")

    comparable = not reasons
    ranking: tuple[MagneticEnergyRanking, ...] = ()
    degenerate: tuple[str, ...] = ()
    selected: str | None = None
    if comparable:
        ordered = sorted(
            observations,
            key=lambda item: (item.total_energy_ry, item.magnetic_configuration_id),
        )
        minimum = ordered[0].total_energy_ry
        ry_to_mev = 13_605.693122994
        ranking = tuple(
            MagneticEnergyRanking(
                magnetic_configuration_id=item.magnetic_configuration_id,
                total_energy_ry=item.total_energy_ry,
                delta_energy_mev_per_cell=max(
                    0.0,
                    (item.total_energy_ry - minimum) * ry_to_mev,
                ),
            )
            for item in ordered
        )
        degenerate = tuple(
            sorted(
                item.magnetic_configuration_id
                for item in ranking
                if item.delta_energy_mev_per_cell
                <= policy.degeneracy_tolerance_mev_per_cell
            )
        )
        if len(degenerate) == 1:
            selected = degenerate[0]
            reasons.add("UNIQUE_MAGNETIC_GROUND_STATE_IDENTIFIED")
        else:
            reasons.add("MAGNETIC_GROUND_STATE_DEGENERATE_WITHIN_TOLERANCE")

    verdict = (
        ValidationVerdict.PASS
        if selected is not None
        else ValidationVerdict.INCONCLUSIVE
    )
    return MagneticGroundStateAssessment(
        source_structure_sha256=(next(iter(structures)) if len(structures) == 1 else None),
        method_sha256=(next(iter(methods)) if len(methods) == 1 else None),
        ranking=ranking,
        missing_configuration_ids=missing,
        unconverged_configuration_ids=unconverged,
        degenerate_ground_state_configuration_ids=degenerate,
        selected_ground_state_configuration_id=selected,
        verdict=verdict,
        reason_codes=tuple(sorted(reasons)),
    )


class BandOrbitalProjection(StrictModel):
    band_index: int = Field(ge=0)
    transition_metal_weight_fraction: float = Field(ge=0, le=1)
    ligand_weight_fraction: float = Field(ge=0, le=1)
    contributing_site_indices: tuple[int, ...] = Field(min_length=1, max_length=512)

    @model_validator(mode="after")
    def validate_weights(self) -> BandOrbitalProjection:
        if (
            self.transition_metal_weight_fraction
            + self.ligand_weight_fraction
            > 1.0 + 1e-8
        ):
            raise ValueError("orbital projection weights exceed unity")
        if self.contributing_site_indices != tuple(
            sorted(set(self.contributing_site_indices))
        ):
            raise ValueError("contributing site indices must be sorted and unique")
        return self


class ParsedHamGNNBandStructure(StrictModel):
    labels: tuple[str, ...] = Field(min_length=2, max_length=64)
    k_nodes_inv_angstrom: tuple[float, ...] = Field(min_length=2, max_length=64)
    k_distances_inv_angstrom: tuple[float, ...] = Field(min_length=3)
    band_energies_ev: tuple[tuple[float, ...], ...] = Field(min_length=2)

    @model_validator(mode="after")
    def validate_shape(self) -> ParsedHamGNNBandStructure:
        if len(self.labels) != len(self.k_nodes_inv_angstrom):
            raise ValueError("HamGNN band labels and nodes differ")
        size = len(self.k_distances_inv_angstrom)
        if any(len(band) != size for band in self.band_energies_ev):
            raise ValueError("HamGNN bands do not share one k grid")
        return self


def parse_hamgnn_band_dat(text: str) -> ParsedHamGNNBandStructure:
    """Parse HamGNN band_cal output and remove duplicated symmetry nodes."""

    labels: tuple[str, ...] | None = None
    nodes: tuple[float, ...] | None = None
    rows: list[tuple[float, float]] = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith("# k_lable:"):
            labels = tuple(line.split(":", 1)[1].split())
            continue
        if line.startswith("# k_node:"):
            nodes = tuple(float(value) for value in line.split(":", 1)[1].split())
            continue
        if line.startswith("#"):
            continue
        fields = line.split()
        if len(fields) != 2:
            raise ValueError("HamGNN band row must have two columns")
        point = (float(fields[0]), float(fields[1]))
        if not all(math.isfinite(value) for value in point):
            raise ValueError("HamGNN band output contains non-finite values")
        rows.append(point)
    if labels is None or nodes is None or not rows:
        raise ValueError("HamGNN band output is missing headers or numerical rows")

    bands: list[list[tuple[float, float]]] = []
    current: list[tuple[float, float]] = []
    previous_k: float | None = None
    tolerance = 1e-8
    for k_distance, energy in rows:
        if previous_k is not None and k_distance < previous_k - tolerance:
            bands.append(current)
            current = []
        if current and abs(k_distance - current[-1][0]) <= tolerance:
            if abs(energy - current[-1][1]) > 1e-5:
                raise ValueError("duplicated HamGNN k point has unequal energies")
        else:
            current.append((k_distance, energy))
        previous_k = k_distance
    if current:
        bands.append(current)
    if len(bands) < 2 or len(bands[0]) < 3:
        raise ValueError("HamGNN band output has insufficient bands or k points")
    reference_k = tuple(point[0] for point in bands[0])
    for band in bands[1:]:
        if len(band) != len(reference_k) or any(
            abs(point[0] - expected) > tolerance
            for point, expected in zip(band, reference_k)
        ):
            raise ValueError("HamGNN bands use inconsistent k grids")
    return ParsedHamGNNBandStructure(
        labels=labels,
        k_nodes_inv_angstrom=nodes,
        k_distances_inv_angstrom=reference_k,
        band_energies_ev=tuple(
            tuple(point[1] for point in band) for band in bands
        ),
    )


def parse_c2db_plotly_band_structure(
    payload: bytes | str,
    *,
    trace_name: str = "PBE no SOC",
) -> ParsedHamGNNBandStructure:
    """Parse one official C2DB Plotly band trace into a strict band matrix.

    C2DB stores all bands in one flattened scatter trace.  The repeated,
    strictly increasing x-coordinate block is the k grid and each following
    block is one band.  Energies are already referenced as published by C2DB;
    this parser does not shift or synthesize them.
    """

    try:
        raw = json.loads(payload)
    except (TypeError, json.JSONDecodeError) as exc:
        raise ValueError("C2DB band payload is not valid JSON") from exc
    if not isinstance(raw, dict):
        raise TypeError("C2DB band payload must be a JSON object")
    plotly = raw.get("plotly")
    if not isinstance(plotly, dict):
        raise TypeError("C2DB band payload lacks a Plotly object")
    traces = plotly.get("data")
    if not isinstance(traces, list):
        raise TypeError("C2DB Plotly payload lacks trace data")
    selected = [
        trace
        for trace in traces
        if isinstance(trace, dict) and trace.get("name") == trace_name
    ]
    if len(selected) != 1:
        raise ValueError("C2DB band trace name must resolve exactly once")
    x_raw = selected[0].get("x")
    y_raw = selected[0].get("y")
    if not isinstance(x_raw, list) or not isinstance(y_raw, list):
        raise TypeError("C2DB band trace requires x and y arrays")
    if len(x_raw) != len(y_raw) or len(x_raw) < 6:
        raise ValueError("C2DB band trace arrays have invalid lengths")
    try:
        x = tuple(float(value) for value in x_raw)
        y = tuple(float(value) for value in y_raw)
    except (TypeError, ValueError) as exc:
        raise ValueError("C2DB band arrays must be numeric") from exc
    if not all(math.isfinite(value) for value in (*x, *y)):
        raise ValueError("C2DB band arrays contain non-finite values")
    period = next(
        (index for index in range(1, len(x)) if x[index] <= x[index - 1]),
        None,
    )
    if period is None or period < 3 or len(x) % period:
        raise ValueError("C2DB flattened band trace has no repeated k grid")
    reference_k = x[:period]
    if any(right <= left for left, right in pairwise(reference_k)):
        raise ValueError("C2DB k grid must be strictly increasing")
    tolerance = 1e-10
    band_count = len(x) // period
    if band_count < 2:
        raise ValueError("C2DB trace contains fewer than two bands")
    for start in range(0, len(x), period):
        if any(
            abs(observed - expected) > tolerance
            for observed, expected in zip(x[start : start + period], reference_k)
        ):
            raise ValueError("C2DB bands do not share one k grid")
    layout = plotly.get("layout")
    xaxis = layout.get("xaxis") if isinstance(layout, dict) else None
    yaxis = layout.get("yaxis") if isinstance(layout, dict) else None
    y_title = yaxis.get("title") if isinstance(yaxis, dict) else None
    y_title_text = y_title.get("text") if isinstance(y_title, dict) else None
    if not isinstance(y_title_text, str) or "VBM" not in y_title_text:
        raise ValueError("C2DB band energy reference is not explicitly VBM")
    labels_raw = xaxis.get("ticktext") if isinstance(xaxis, dict) else None
    nodes_raw = xaxis.get("tickvals") if isinstance(xaxis, dict) else None
    if not isinstance(labels_raw, list) or not isinstance(nodes_raw, list):
        raise TypeError("C2DB Plotly layout lacks high-symmetry ticks")
    if len(labels_raw) != len(nodes_raw) or len(labels_raw) < 2:
        raise ValueError("C2DB high-symmetry labels and nodes differ")
    labels = tuple(str(value) for value in labels_raw)
    try:
        nodes = tuple(float(value) for value in nodes_raw)
    except (TypeError, ValueError) as exc:
        raise ValueError("C2DB high-symmetry nodes must be numeric") from exc
    if not all(math.isfinite(value) for value in nodes):
        raise ValueError("C2DB high-symmetry nodes contain non-finite values")
    if nodes[0] < reference_k[0] - tolerance or nodes[-1] > reference_k[-1] + tolerance:
        raise ValueError("C2DB high-symmetry nodes lie outside the k grid")
    return ParsedHamGNNBandStructure(
        labels=labels,
        k_nodes_inv_angstrom=nodes,
        k_distances_inv_angstrom=reference_k,
        band_energies_ev=tuple(
            y[start : start + period] for start in range(0, len(y), period)
        ),
    )


class FlatBandAnalysisInput(StrictModel):
    fermi_energy_ev: float
    k_distances_inv_angstrom: tuple[float, ...] = Field(min_length=3)
    high_symmetry_indices: tuple[int, ...] = Field(min_length=2)
    band_energies_ev: tuple[tuple[float, ...], ...] = Field(min_length=2)
    target_band_index: int = Field(ge=0)
    orbital_projections: tuple[BandOrbitalProjection, ...] = ()
    contributor_sublattice_connected: bool | None
    soc_explicit: bool

    @model_validator(mode="after")
    def validate_shapes(self) -> FlatBandAnalysisInput:
        size = len(self.k_distances_inv_angstrom)
        if any(len(band) != size for band in self.band_energies_ev):
            raise ValueError("every band must match the k-distance grid")
        if self.target_band_index >= len(self.band_energies_ev):
            raise ValueError("target band index is outside the band array")
        if self.high_symmetry_indices != tuple(
            sorted(set(self.high_symmetry_indices))
        ):
            raise ValueError("high-symmetry indices must be sorted and unique")
        if self.high_symmetry_indices[0] < 0 or self.high_symmetry_indices[-1] >= size:
            raise ValueError("high-symmetry index is outside the k grid")
        projection_indices = tuple(item.band_index for item in self.orbital_projections)
        if projection_indices != tuple(sorted(set(projection_indices))):
            raise ValueError("orbital projections must be sorted and unique by band")
        return self


class FlatBandPolicy(StrictModel):
    maximum_bandwidth_ev: float = Field(gt=0, le=1)
    near_fermi_window_ev: float = Field(gt=0, le=2)
    degeneracy_tolerance_ev: float = Field(gt=0, le=0.1)
    slope_difference_tolerance_ev_angstrom: float = Field(gt=0, le=10)
    minimum_transition_metal_weight_fraction: float = Field(ge=0, le=1)
    minimum_tm_ligand_weight_fraction: float = Field(ge=0, le=1)
    require_soc_explicit: bool


class FlatBandAssessment(StrictModel):
    target_band_index: int
    target_bandwidth_ev: float
    target_distance_to_fermi_ev: float
    closest_band_indices: tuple[int, ...]
    is_first_near_fermi_band: bool
    no_first_order_crossing: bool
    dispersive_bands_do_not_cross_fermi: bool
    orbital_character_passed: bool | None
    connected_sublattice_passed: bool | None
    soc_requirement_passed: bool
    verdict: ValidationVerdict
    reason_codes: tuple[Identifier, ...]
    scientific_conclusion: Literal[False] = False


def assess_flat_band(
    inputs: FlatBandAnalysisInput,
    policy: FlatBandPolicy,
) -> FlatBandAssessment:
    energies = np.asarray(inputs.band_energies_ev, dtype=float)
    k = np.asarray(inputs.k_distances_inv_angstrom, dtype=float)
    if not np.isfinite(energies).all() or not np.isfinite(k).all():
        raise ValueError("flat-band inputs must be finite")
    if np.any(np.diff(k) <= 0):
        raise ValueError("k-distance grid must be strictly increasing")
    relative = energies - inputs.fermi_energy_ev
    target = relative[inputs.target_band_index]
    bandwidth = float(np.max(target) - np.min(target))
    distances = np.min(np.abs(relative), axis=1)
    closest_distance = float(np.min(distances))
    closest = tuple(
        int(index)
        for index, value in enumerate(distances)
        if value <= closest_distance + policy.degeneracy_tolerance_ev
    )
    first_near = (
        inputs.target_band_index in closest
        and float(distances[inputs.target_band_index])
        <= policy.near_fermi_window_ev
    )
    no_first_order = _no_first_order_target_crossing(
        relative,
        k,
        target_band_index=inputs.target_band_index,
        high_symmetry_indices=set(inputs.high_symmetry_indices),
        energy_tolerance=policy.degeneracy_tolerance_ev,
        slope_tolerance=policy.slope_difference_tolerance_ev_angstrom,
    )
    dispersive_no_crossing = all(
        not _strict_fermi_crossing(band, policy.degeneracy_tolerance_ev)
        for index, band in enumerate(relative)
        if index != inputs.target_band_index
        and float(np.max(band) - np.min(band)) > policy.maximum_bandwidth_ev
    )
    projection = next(
        (
            item
            for item in inputs.orbital_projections
            if item.band_index == inputs.target_band_index
        ),
        None,
    )
    orbital_pass = (
        None
        if projection is None
        else (
            projection.transition_metal_weight_fraction
            >= policy.minimum_transition_metal_weight_fraction
            and projection.transition_metal_weight_fraction
            + projection.ligand_weight_fraction
            >= policy.minimum_tm_ligand_weight_fraction
        )
    )
    connected = inputs.contributor_sublattice_connected
    soc_pass = not policy.require_soc_explicit or inputs.soc_explicit
    resolved_checks = {
        "BANDWIDTH_EXCEEDS_POLICY": bandwidth <= policy.maximum_bandwidth_ev,
        "TARGET_IS_NOT_FIRST_NEAR_FERMI_BAND": first_near,
        "FIRST_ORDER_BAND_CROSSING_DETECTED": no_first_order,
        "DISPERSIVE_BAND_CROSSES_FERMI": dispersive_no_crossing,
        "SOC_EVIDENCE_REQUIRED": soc_pass,
    }
    if orbital_pass is not None:
        resolved_checks["TM_LIGAND_ORBITAL_CHARACTER_FAILED"] = orbital_pass
    if connected is not None:
        resolved_checks["CONTRIBUTOR_SUBLATTICE_NOT_CONNECTED"] = connected
    reasons = {
        reason for reason, passed in resolved_checks.items() if not passed
    }
    unresolved = set()
    if orbital_pass is None:
        unresolved.add("ORBITAL_CHARACTER_UNRESOLVED")
    if connected is None:
        unresolved.add("CONTRIBUTOR_SUBLATTICE_UNRESOLVED")
    if reasons:
        verdict = ValidationVerdict.FAIL
    elif unresolved:
        reasons.update(unresolved)
        verdict = ValidationVerdict.INCONCLUSIVE
    else:
        verdict = ValidationVerdict.PASS
        reasons.add("ALL_FLAT_BAND_CRITERIA_PASSED")
    return FlatBandAssessment(
        target_band_index=inputs.target_band_index,
        target_bandwidth_ev=bandwidth,
        target_distance_to_fermi_ev=float(distances[inputs.target_band_index]),
        closest_band_indices=closest,
        is_first_near_fermi_band=first_near,
        no_first_order_crossing=no_first_order,
        dispersive_bands_do_not_cross_fermi=dispersive_no_crossing,
        orbital_character_passed=orbital_pass,
        connected_sublattice_passed=connected,
        soc_requirement_passed=soc_pass,
        verdict=verdict,
        reason_codes=tuple(sorted(reasons)),
    )


class TopologyAnalysisInput(StrictModel):
    soc_explicit: bool
    direct_gap_ev: float
    indirect_gap_ev: float
    wilson_loop_winding: int | None = None
    chern_number: float | None = None
    invariant_method: Literal["WILSON_LOOP", "BERRY_CURVATURE", "BOTH"]
    wannier_hamiltonian_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class TopologyPolicy(StrictModel):
    minimum_direct_gap_ev: float = Field(gt=0, le=5)
    integer_invariant_tolerance: float = Field(gt=0, le=0.25)
    require_insulating_indirect_gap: bool = True


class TopologyAssessment(StrictModel):
    z2_index: int | None
    rounded_chern_number: int | None
    invariant_integer_consistent: bool
    soc_gap_passed: bool
    insulating_gap_passed: bool
    verdict: ValidationVerdict
    reason_codes: tuple[Identifier, ...]
    scientific_conclusion: Literal[False] = False


def assess_topology(
    inputs: TopologyAnalysisInput,
    policy: TopologyPolicy,
) -> TopologyAssessment:
    if not all(math.isfinite(value) for value in (inputs.direct_gap_ev, inputs.indirect_gap_ev)):
        raise ValueError("topology gaps must be finite")
    z2 = (
        abs(inputs.wilson_loop_winding) % 2
        if inputs.wilson_loop_winding is not None
        else None
    )
    rounded_chern = (
        round(inputs.chern_number)
        if inputs.chern_number is not None
        else None
    )
    invariant_consistent = (
        inputs.chern_number is None
        or abs(inputs.chern_number - rounded_chern)
        <= policy.integer_invariant_tolerance
    )
    soc_gap = inputs.soc_explicit and inputs.direct_gap_ev >= policy.minimum_direct_gap_ev
    insulating = (
        not policy.require_insulating_indirect_gap or inputs.indirect_gap_ev > 0
    )
    reasons: set[str] = set()
    if not inputs.soc_explicit:
        reasons.add("SOC_NOT_EXPLICIT")
    if not soc_gap:
        reasons.add("SOC_DIRECT_GAP_BELOW_POLICY")
    if not insulating:
        reasons.add("INDIRECT_GAP_NOT_INSULATING")
    if not invariant_consistent:
        reasons.add("TOPOLOGICAL_INVARIANT_NOT_INTEGER_CONSISTENT")
    if z2 is None and rounded_chern is None:
        reasons.add("TOPOLOGICAL_INVARIANT_MISSING")
    if not reasons:
        verdict = ValidationVerdict.PASS
        reasons.add("TOPOLOGY_VALIDATION_PASSED")
    elif "TOPOLOGICAL_INVARIANT_MISSING" in reasons:
        verdict = ValidationVerdict.INCONCLUSIVE
    else:
        verdict = ValidationVerdict.FAIL
    return TopologyAssessment(
        z2_index=z2,
        rounded_chern_number=rounded_chern,
        invariant_integer_consistent=invariant_consistent,
        soc_gap_passed=soc_gap,
        insulating_gap_passed=insulating,
        verdict=verdict,
        reason_codes=tuple(sorted(reasons)),
    )


class ExchangeTcInput(StrictModel):
    exchange_parameters_mev: tuple[float, ...] = Field(min_length=1, max_length=128)
    coordination_numbers: tuple[int, ...] = Field(min_length=1, max_length=128)
    spin_quantum_number: float = Field(gt=0, le=10)
    fit_rank: int = Field(ge=0)
    fit_parameter_count: int = Field(ge=1)
    fit_rmse_mev: float = Field(ge=0)

    @model_validator(mode="after")
    def validate_exchange_shape(self) -> ExchangeTcInput:
        if len(self.exchange_parameters_mev) != len(self.coordination_numbers):
            raise ValueError("exchange parameters and coordination numbers differ")
        if self.fit_rank > self.fit_parameter_count:
            raise ValueError("exchange fit rank exceeds parameter count")
        return self


class ExchangeTcPolicy(StrictModel):
    maximum_fit_rmse_mev: float = Field(gt=0, le=1_000)
    require_full_rank: bool = True
    method: Literal["MEAN_FIELD_HEISENBERG"] = "MEAN_FIELD_HEISENBERG"


class ExchangeTcAssessment(StrictModel):
    tc_kelvin: float | None
    fit_passed: bool
    method: Literal["MEAN_FIELD_HEISENBERG"]
    verdict: ValidationVerdict
    reason_codes: tuple[Identifier, ...]
    limitations: tuple[str, ...]
    scientific_conclusion: Literal[False] = False


def estimate_exchange_tc(
    inputs: ExchangeTcInput,
    policy: ExchangeTcPolicy,
) -> ExchangeTcAssessment:
    full_rank = inputs.fit_rank == inputs.fit_parameter_count
    fit_passed = inputs.fit_rmse_mev <= policy.maximum_fit_rmse_mev and (
        full_rank or not policy.require_full_rank
    )
    effective_exchange_mev = sum(
        value * coordination
        for value, coordination in zip(
            inputs.exchange_parameters_mev,
            inputs.coordination_numbers,
            strict=True,
        )
    )
    # Mean-field Heisenberg estimate: k_B T_c = 2/3 S(S+1) sum_j J_0j.
    boltzmann_mev_per_k = 0.08617333262
    tc = (
        (2.0 / 3.0)
        * inputs.spin_quantum_number
        * (inputs.spin_quantum_number + 1.0)
        * effective_exchange_mev
        / boltzmann_mev_per_k
        if fit_passed and effective_exchange_mev > 0
        else None
    )
    reasons: set[str] = set()
    if inputs.fit_rmse_mev > policy.maximum_fit_rmse_mev:
        reasons.add("EXCHANGE_FIT_RMSE_EXCEEDS_POLICY")
    if policy.require_full_rank and not full_rank:
        reasons.add("EXCHANGE_FIT_RANK_DEFICIENT")
    if effective_exchange_mev <= 0:
        reasons.add("FERROMAGNETIC_MEAN_FIELD_TC_NOT_POSITIVE")
    if not reasons:
        verdict = ValidationVerdict.PASS
        reasons.add("MEAN_FIELD_TC_ESTIMATE_AVAILABLE")
    elif not fit_passed:
        verdict = ValidationVerdict.INCONCLUSIVE
    else:
        verdict = ValidationVerdict.FAIL
    return ExchangeTcAssessment(
        tc_kelvin=tc,
        fit_passed=fit_passed,
        method=policy.method,
        verdict=verdict,
        reason_codes=tuple(sorted(reasons)),
        limitations=(
            "Mean-field Heisenberg Tc is an upper-biased screening estimate.",
            "A converged finite-size Monte Carlo or equivalent calculation is required for a stronger Tc claim.",
        ),
    )


def _largest_periodic_center_gap(structure: Structure) -> tuple[int, float]:
    reciprocal = structure.lattice.reciprocal_lattice_crystallographic
    best_axis = 0
    best_gap = -1.0
    for axis in range(3):
        values = sorted(float(site.frac_coords[axis]) % 1.0 for site in structure)
        gaps = [right - left for left, right in pairwise(values)]
        gaps.append(values[0] + 1.0 - values[-1])
        plane_spacing = 1.0 / float(reciprocal.abc[axis])
        gap = max(gaps) * plane_spacing
        if gap > best_gap:
            best_axis = axis
            best_gap = gap
    return best_axis, float(best_gap)


def _periodic_contributor_connectivity(
    structure: Structure,
    indices: tuple[int, ...],
) -> tuple[bool | None, int | None]:
    if not indices:
        return None, None
    try:
        graph = CrystalNN().get_bonded_structure(structure)
    except Exception:  # noqa: BLE001
        return None, None
    nodes = set(indices)
    adjacency = {index: set() for index in indices}
    periodic_edges = 0
    for index in indices:
        for neighbor in graph.get_connected_sites(index):
            if neighbor.index in nodes:
                adjacency[index].add(neighbor.index)
                if any(int(value) != 0 for value in neighbor.jimage):
                    periodic_edges += 1
                continue
            # A transition-metal network is often ligand-mediated (TM-X-TM),
            # so project one ligand hop while preserving periodic-edge evidence.
            for second in graph.get_connected_sites(neighbor.index):
                if second.index not in nodes:
                    continue
                adjacency[index].add(second.index)
                if any(int(value) != 0 for value in neighbor.jimage) or any(
                    int(value) != 0 for value in second.jimage
                ):
                    periodic_edges += 1
    seen: set[int] = set()
    queue = deque([indices[0]])
    seen.add(indices[0])
    while queue:
        current = queue.popleft()
        for neighbor in adjacency[current]:
            if neighbor not in seen:
                seen.add(neighbor)
                queue.append(neighbor)
    return seen == nodes and periodic_edges > 0, periodic_edges


def _oxidation_assignments(
    structure: Structure,
    contributor_elements: set[str],
) -> tuple[
    tuple[OxidationAssignment, ...],
    Literal["EXPLICIT_OR_BOND_VALENCE_GUESS", "UNRESOLVED"],
]:
    charged = structure.copy()
    try:
        if any(getattr(site.specie, "oxi_state", None) is None for site in charged):
            charged.add_oxidation_state_by_guess()
    except (ValueError, TypeError):
        return (), "UNRESOLVED"
    values: dict[str, set[float]] = {}
    for site in charged:
        symbol = site.specie.symbol
        if symbol not in contributor_elements:
            continue
        oxidation = getattr(site.specie, "oxi_state", None)
        if oxidation is None or not math.isfinite(float(oxidation)):
            return (), "UNRESOLVED"
        values.setdefault(symbol, set()).add(float(oxidation))
    assignments = []
    for symbol in sorted(values):
        observed = tuple(sorted(values[symbol]))
        common = tuple(float(value) for value in Element(symbol).common_oxidation_states)
        assignments.append(
            OxidationAssignment(
                element=symbol,
                observed_states=observed,
                common_states=tuple(sorted(common)),
                all_observed_states_common=all(
                    any(math.isclose(value, allowed, abs_tol=1e-8) for allowed in common)
                    for value in observed
                ),
                mixed_valence=len(observed) > 1,
            )
        )
    return tuple(assignments), (
        "EXPLICIT_OR_BOND_VALENCE_GUESS" if assignments else "UNRESOLVED"
    )


def _strict_fermi_crossing(band: np.ndarray, tolerance: float) -> bool:
    for left, right in pairwise(band):
        if (left < -tolerance and right > tolerance) or (
            left > tolerance and right < -tolerance
        ):
            return True
    return False


def _no_first_order_target_crossing(
    energies: np.ndarray,
    k: np.ndarray,
    *,
    target_band_index: int,
    high_symmetry_indices: set[int],
    energy_tolerance: float,
    slope_tolerance: float,
) -> bool:
    target = energies[target_band_index]
    target_slope = np.gradient(target, k)
    for index, other in enumerate(energies):
        if index == target_band_index:
            continue
        difference = target - other
        other_slope = np.gradient(other, k)
        if any(
            left * right < -(energy_tolerance**2)
            for left, right in pairwise(difference)
        ):
            return False
        for k_index, value in enumerate(difference):
            if abs(float(value)) > energy_tolerance:
                continue
            shared_extremum = (
                k_index in high_symmetry_indices
                and abs(float(target_slope[k_index])) <= slope_tolerance
                and abs(float(other_slope[k_index])) <= slope_tolerance
            )
            if not shared_extremum and abs(
                float(target_slope[k_index] - other_slope[k_index])
            ) > slope_tolerance:
                return False
    return True
