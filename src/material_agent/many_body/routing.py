"""Pure deterministic capability matching and routing for Agent04 task 3."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .models import EffectiveModelPackage, InteractionKind, ManyBodyRequest, SolverRoutingDecision, canonical_hash
from .registry import CapabilityRegistry, DEFAULT_REGISTRY
from .validation import ModelFeatures, ValidationStatus, validate_model_package


ROUTING_POLICY_VERSION = "many-body-routing/v1"
CONTROL_FLOW_CLAIMS = frozenset({"workflow_lifecycle", "model_validation", "control_flow"})


@dataclass(frozen=True)
class RoutingPolicy:
    version: str = ROUTING_POLICY_VERSION
    require_approval_for_executable: bool = True


def route_model(
    package: EffectiveModelPackage,
    target_claim: str,
    *,
    registry: CapabilityRegistry = DEFAULT_REGISTRY,
    policy: RoutingPolicy = RoutingPolicy(),
    request: ManyBodyRequest | None = None,
) -> SolverRoutingDecision:
    """Match a validated package without selecting a fallback or executing work."""

    validation = validate_model_package(package)
    request_hash = canonical_hash(
        request if request is not None else {
            "model_package_hash": package.package_hash,
            "target_claim": target_claim,
        }
    )
    base = dict(
        routing_decision_id=f"route_{request_hash[:24]}",
        model_package_hash=package.package_hash,
        request_hash=request_hash,
        policy_version=policy.version,
        registry_snapshot=registry.capabilities[0].registry_snapshot,
    )
    if validation.status is not ValidationStatus.READY or validation.features is None:
        issues = validation.issues
        reason_map = {
            "GRAND_CANONICAL_UNSUPPORTED": "ENSEMBLE_UNSUPPORTED",
            "NONLOCAL_INTERACTION_UNSUPPORTED": "INTERACTION_TERM_UNSUPPORTED",
        }
        return SolverRoutingDecision(
            **base,
            status="BLOCKED" if validation.status is ValidationStatus.BLOCKED_MISSING_INPUT else "NOT_APPLICABLE",
            reason_codes=tuple(dict.fromkeys(reason_map.get(issue.reason_code, issue.reason_code) for issue in issues)),
            field_paths=tuple(dict.fromkeys(issue.field_path for issue in issues)),
            missing_input=tuple(issue.field_path for issue in issues if issue.reason_code.startswith("MISSING_")),
            remediation=tuple(dict.fromkeys(issue.remediation for issue in issues)),
        )

    features = validation.features
    records: dict[str, dict[str, Any]] = {}
    applicable: list[str] = []
    scientific_matches: list[str] = []
    available_scientific: list[str] = []
    simulators: list[str] = []
    for capability in registry.capabilities:
        mismatches = _mismatches(package, features, target_claim, capability)
        is_match = not mismatches
        records[capability.solver_id] = {
            "matched": is_match,
            "registered": capability.registered,
            "executable": capability.executable,
            "is_mock": capability.is_mock,
            "mismatches": mismatches,
        }
        if not is_match:
            continue
        applicable.append(capability.solver_id)
        if capability.is_mock:
            simulators.append(capability.solver_id)
        else:
            scientific_matches.append(capability.solver_id)
            if capability.registered and capability.executable:
                available_scientific.append(capability.solver_id)

    # A simulator is only a READY control route for explicit control claims.
    # It never becomes a scientific recommendation.
    recommended = available_scientific[0] if available_scientific else None
    executable = bool(available_scientific)
    if target_claim in CONTROL_FLOW_CLAIMS and simulators:
        recommended = simulators[0]
        executable = True
    if recommended is None:
        status = "NOT_APPLICABLE"
        reasons = ("NO_EXECUTABLE_MATCH",) if scientific_matches else ("CAPABILITY_MISMATCH",)
        if scientific_matches:
            reasons += ("PLANNED_CAPABILITY_NOT_EXECUTABLE",)
    else:
        status = "READY"
        reasons = ("MOCK_CONTROL_FLOW_ONLY",) if recommended in simulators else ("EXECUTABLE_CAPABILITY_MATCH",)
    if not scientific_matches and not simulators:
        reasons = ("CAPABILITY_MISMATCH",)
    mismatch_items = [item for record in records.values() for item in record["mismatches"]]
    mismatch_codes = tuple(dict.fromkeys(item["reason_code"] for item in mismatch_items))
    mismatch_paths = tuple(dict.fromkeys(item["field_path"] for item in mismatch_items))
    if status != "READY":
        reasons = tuple(dict.fromkeys((*reasons, *mismatch_codes)))
    limitations = tuple(
        dict.fromkeys(
            limitation
            for solver_id in applicable
            for limitation in registry.get(solver_id).limitations
        )
    )
    return SolverRoutingDecision(
        **base,
        status=status,
        executable=executable,
        requires_approval=executable and policy.require_approval_for_executable,
        applicable_solver_ids=tuple(applicable),
        inapplicable_solver_ids=tuple(item.solver_id for item in registry.capabilities if item.solver_id not in applicable),
        recommended_solver_id=recommended,
        scientific_capability_matches=tuple(scientific_matches),
        available_scientific_solvers=tuple(available_scientific),
        control_flow_simulators=tuple(simulators),
        capability_matches=records,
        reason_codes=reasons,
        field_paths=mismatch_paths,
        limitations=limitations,
        remediation=("Implement and explicitly register an approved scientific backend." if scientific_matches else "Change the frozen model/request or wait for a capability covering every unsupported feature." ,),
    )


def _mismatches(package: EffectiveModelPackage, features: ModelFeatures, claim: str, capability) -> list[dict[str, str]]:
    checks = [
        ("model_family", features.model_family in {item.value for item in capability.supported_model_families}, "MODEL_FAMILY_UNSUPPORTED"),
        ("geometry.geometry_type", package.geometry.geometry_type in capability.supported_geometry_types, "GEOMETRY_UNSUPPORTED"),
        ("geometry.dimension", not capability.supported_dimensions or package.geometry.dimension in capability.supported_dimensions, "DIMENSION_UNSUPPORTED"),
        ("one_body.is_complex", (features.complex_hopping and capability.supports_complex_hopping) or (features.real_hopping and capability.supports_real_hopping), "COMPLEX_HOPPING_UNSUPPORTED"),
        ("basis.includes_soc", not features.soc or capability.supports_soc, "SOC_UNSUPPORTED"),
        ("interactions", features.onsite_u and not features.nonlocal_interaction and InteractionKind.ONSITE_HUBBARD_U in capability.supported_interaction_kinds, "INTERACTION_TERM_UNSUPPORTED"),
        ("state_points", features.temperature in capability.supported_temperatures and features.ensemble in capability.supported_ensembles, "FINITE_T_UNSUPPORTED" if features.temperature != "ZERO_T" else "ENSEMBLE_UNSUPPORTED"),
        ("geometry.boundary_condition", package.geometry.boundary_condition in capability.supported_boundaries, "BOUNDARY_UNSUPPORTED"),
    ]
    if not capability.is_mock and claim not in CONTROL_FLOW_CLAIMS:
        checks.append(("requested_claim", claim in capability.supported_observables, "OBSERVABLE_UNSUPPORTED"))
    if capability.max_sites is not None and features.num_sites > capability.max_sites:
        checks.append(("geometry.num_sites", False, "HILBERT_SPACE_LIMIT"))
    if capability.max_active_orbitals is not None and features.num_active_orbitals > capability.max_active_orbitals:
        checks.append(("basis.orbitals", False, "HILBERT_SPACE_LIMIT"))
    return [{"field_path": path, "reason_code": code} for path, ok, code in checks if not ok]
