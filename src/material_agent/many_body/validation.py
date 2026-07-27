"""Pure, fail-closed validation for Agent04 model packages (task 2).

This module deliberately stops before routing, approval, operation creation, or
backend submission.  Artifact reads are limited to a caller-supplied trusted
root and are never written to.
"""

from __future__ import annotations

import hashlib
import math
import re
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import unquote, urlsplit

from pydantic import ValidationError

from material_agent.many_body.models import (
    EffectiveModelPackage,
    InteractionKind,
    ModelFamily,
    package_content_hash,
)


class ValidationStatus(StrEnum):
    READY = "READY"
    BLOCKED_MISSING_INPUT = "BLOCKED_MISSING_INPUT"
    PERMANENT_FAILED = "PERMANENT_FAILED"
    NOT_APPLICABLE = "NOT_APPLICABLE"


@dataclass(frozen=True)
class ValidationIssue:
    field_path: str
    reason_code: str
    message: str
    remediation: str
    blocking: bool = True


@dataclass(frozen=True)
class ModelFeatures:
    single_band: bool
    multi_orbital: bool
    real_hopping: bool
    complex_hopping: bool
    onsite_u: bool
    nonlocal_interaction: bool
    soc: bool
    temperature: str
    ensemble: str
    boundary_condition: str
    num_sites: int
    num_active_orbitals: int
    num_hopping_terms: int
    model_family: str


@dataclass(frozen=True)
class ValidationResult:
    status: ValidationStatus
    package: EffectiveModelPackage | None
    features: ModelFeatures | None
    issues: tuple[ValidationIssue, ...]

    @property
    def valid(self) -> bool:
        return self.status in (ValidationStatus.READY, ValidationStatus.NOT_APPLICABLE)


_MISSING = object()
_FORBIDDEN_KEY = re.compile(r"(?:shell|command|executable|pickle|expression|python|code)", re.I)
_FORBIDDEN_SUFFIXES = (".pkl", ".pickle", ".npz", ".npy", ".py", ".sh")


def validate_model_package(
    payload: EffectiveModelPackage | Mapping[str, Any],
    *,
    artifact_root: Path | None = None,
    expected_model_id: str | None = None,
    expected_revision: int | None = None,
) -> ValidationResult:
    """Validate a package without creating any control-plane side effects."""

    raw = payload.model_dump(mode="json") if isinstance(payload, EffectiveModelPackage) else payload
    if not isinstance(raw, Mapping):
        return _failed("$", "SCHEMA_INVALID", "model package must be an object", "Provide an EffectiveModelPackage object.")

    issues: list[ValidationIssue] = []
    _collect_safety_issues(raw, "$", issues)
    _collect_missing_inputs(raw, issues)
    if issues:
        return ValidationResult(ValidationStatus.BLOCKED_MISSING_INPUT, None, None, tuple(issues)) if all(
            issue.reason_code.startswith("MISSING_") for issue in issues
        ) else ValidationResult(ValidationStatus.PERMANENT_FAILED, None, None, tuple(issues))

    # Task 1 deliberately keeps the v1 numeric term representation real.  The
    # explicit complex flag is still useful for capability classification.
    one_body_raw = raw.get("one_body")
    if isinstance(one_body_raw, Mapping) and one_body_raw.get("is_complex") is True:
        issue = ValidationIssue("one_body.is_complex", "COMPLEX_HOPPING_UNSUPPORTED", "complex hopping is outside the current MVP capability boundary", "Use real hopping for the MVP or wait for a complex-hopping capability.")
        return ValidationResult(ValidationStatus.NOT_APPLICABLE, None, None, (issue,))

    try:
        package = payload if isinstance(payload, EffectiveModelPackage) else EffectiveModelPackage.model_validate(raw)
    except (ValidationError, ValueError, TypeError) as exc:
        issue = ValidationIssue(
            field_path="$",
            reason_code="SCHEMA_INVALID",
            message="model package does not satisfy the Agent04 domain schema",
            remediation="Fix the reported schema, type, unit, and required-field errors and create a new revision.",
        )
        return ValidationResult(ValidationStatus.PERMANENT_FAILED, None, None, (issue,))

    if not any(term.kind is InteractionKind.ONSITE_HUBBARD_U for term in package.interactions):
        issues.append(ValidationIssue("interactions", "MISSING_HUBBARD_U", "the MVP model requires an onsite Hubbard U term", "Provide at least one onsite Hubbard U with provenance and create a new revision."))
        return ValidationResult(ValidationStatus.BLOCKED_MISSING_INPUT, package, None, tuple(issues))

    if expected_model_id is not None and package.model_id != expected_model_id:
        issues.append(ValidationIssue("model_id", "MODEL_ID_MISMATCH", "package model_id differs from the requested immutable identity", "Load the artifact for the requested model or update the request explicitly."))
    if expected_revision is not None and package.revision != expected_revision:
        issues.append(ValidationIssue("revision", "REVISION_MISMATCH", "package revision differs from the requested immutable revision", "Use the exact requested revision; never replace it with the latest model."))

    _validate_package_integrity(package, raw, artifact_root, issues)
    if issues:
        return ValidationResult(ValidationStatus.PERMANENT_FAILED, package, None, tuple(issues))

    features = _extract_features(package)
    _validate_physical_integrity(package, features, issues)
    if issues:
        return ValidationResult(ValidationStatus.PERMANENT_FAILED, package, features, tuple(issues))

    unsupported = _unsupported_features(package, features)
    if unsupported:
        return ValidationResult(ValidationStatus.NOT_APPLICABLE, package, features, tuple(unsupported))
    return ValidationResult(ValidationStatus.READY, package, features, ())


def extract_features(package: EffectiveModelPackage) -> ModelFeatures:
    """Return deterministic routing inputs; this does not select a solver."""
    return _extract_features(package)


def _collect_missing_inputs(raw: Mapping[str, Any], issues: list[ValidationIssue]) -> None:
    required = {
        "geometry": "MISSING_GEOMETRY",
        "basis": "MISSING_BASIS",
        "one_body": "MISSING_ONE_BODY",
        "interactions": "MISSING_HUBBARD_U",
        "state_points": "MISSING_STATE_POINT",
        "conventions_version": "MISSING_CONVENTIONS",
        "provenance": "MISSING_PROVENANCE",
    }
    for field, code in required.items():
        value = raw.get(field, _MISSING)
        if value is _MISSING or value is None or value == []:
            issues.append(ValidationIssue(field, code, f"required input '{field}' is missing", f"Provide '{field}' and create a new model revision."))
    geometry = raw.get("geometry")
    if isinstance(geometry, Mapping) and geometry.get("boundary_condition", _MISSING) in (_MISSING, None, ""):
        issues.append(ValidationIssue("geometry.boundary_condition", "MISSING_BOUNDARY_CONDITION", "boundary condition is required", "Declare OPEN or PERIODIC explicitly."))
    for index, state in enumerate(raw.get("state_points", []) if isinstance(raw.get("state_points", []), list) else []):
        if isinstance(state, Mapping) and state.get("ensemble") == "CANONICAL":
            for field in ("n_up", "n_down"):
                if state.get(field, _MISSING) is _MISSING or state.get(field) is None:
                    issues.append(ValidationIssue(f"state_points[{index}].{field}", f"MISSING_{field.upper()}", f"canonical sector requires {field}", f"Provide {field} for this state point."))


def _collect_safety_issues(value: Any, path: str, issues: list[ValidationIssue]) -> None:
    if isinstance(value, Mapping):
        for key, item in value.items():
            key_path = f"{path}.{key}"
            if _FORBIDDEN_KEY.search(str(key)):
                issues.append(ValidationIssue(key_path, "FORBIDDEN_EXECUTABLE_INPUT", "arbitrary code or shell input is forbidden", "Remove executable or expression fields; use frozen schema fields only."))
            _collect_safety_issues(item, key_path, issues)
    elif isinstance(value, list):
        for index, item in enumerate(value):
            _collect_safety_issues(item, f"{path}[{index}]", issues)
    elif isinstance(value, str):
        normalized = value.replace("\\", "/")
        if value.startswith(("/", "\\")) or re.match(r"^[A-Za-z]:/", normalized) or ".." in normalized.split("/"):
            issues.append(ValidationIssue(path, "UNSAFE_PATH", "absolute paths and path traversal are forbidden", "Use a logical artifact URI."))
        if value.lower().endswith(_FORBIDDEN_SUFFIXES):
            issues.append(ValidationIssue(path, "FORBIDDEN_ARTIFACT_FORMAT", "pickle, NumPy binary, and executable artifacts are forbidden", "Use deterministic JSON/JSONL or another explicitly approved format."))
    elif isinstance(value, float) and not math.isfinite(value):
        issues.append(ValidationIssue(path, "NONFINITE_VALUE", "numeric values must be finite", "Replace NaN or infinity with a finite declared value."))


def _validate_package_integrity(package: EffectiveModelPackage, raw: Mapping[str, Any], artifact_root: Path | None, issues: list[ValidationIssue]) -> None:
    declared = package.package_hash
    actual = package_content_hash(package)
    if declared != actual:
        issues.append(ValidationIssue("package_hash", "PACKAGE_HASH_MISMATCH", "package hash does not match canonical package content", "Recompute the canonical hash for this immutable revision."))
    refs = _artifact_refs(raw)
    seen: set[str] = set()
    for path, uri, digest in refs:
        if uri in seen:
            continue
        seen.add(uri)
        if artifact_root is None:
            issues.append(ValidationIssue(path, "MISSING_ARTIFACT_ROOT", "artifact content cannot be verified without a trusted artifact root", "Supply the project artifact root for read-only verification."))
            continue
        try:
            artifact_path = _resolve_artifact(uri, artifact_root)
            actual_digest = _sha256_file(artifact_path)
        except (OSError, ValueError) as exc:
            issues.append(ValidationIssue(path, "ARTIFACT_UNAVAILABLE", "declared artifact is missing or unsafe", "Publish the referenced artifact inside the trusted artifact store and keep its URI immutable."))
            continue
        if actual_digest != digest:
            issues.append(ValidationIssue(path, "ARTIFACT_HASH_MISMATCH", "artifact content hash differs from the declared SHA-256", "Restore the immutable artifact or create a new model revision with its actual hash."))
    _validate_revision_linkage(package, issues)


def _validate_revision_linkage(package: EffectiveModelPackage, issues: list[ValidationIssue]) -> None:
    if package.material_linkage is not None and package.material_linkage.status.value != "NONE":
        if package.material_linkage.structure_artifact not in package.artifacts:
            issues.append(ValidationIssue("material_linkage.structure_artifact", "REVISION_ARTIFACT_NOT_DECLARED", "linkage artifact is not part of the package artifact manifest", "Declare the exact immutable artifact reference in artifacts."))
    provenance_ids = {record.provenance_id for record in package.provenance}
    for index, interaction in enumerate(package.interactions):
        if interaction.provenance_id not in provenance_ids:
            issues.append(ValidationIssue(f"interactions[{index}].provenance_id", "PROVENANCE_REFERENCE_MISMATCH", "interaction provenance reference is unresolved", "Add the referenced provenance record in the same model revision."))
    for index, state in enumerate(package.state_points):
        if state.provenance_id not in provenance_ids:
            issues.append(ValidationIssue(f"state_points[{index}].provenance_id", "PROVENANCE_REFERENCE_MISMATCH", "state-point provenance reference is unresolved", "Add the referenced provenance record in the same model revision."))


def _validate_physical_integrity(package: EffectiveModelPackage, features: ModelFeatures, issues: list[ValidationIssue]) -> None:
    sites = set(package.geometry.site_ids)
    orbitals = {orbital.orbital_id: orbital for orbital in package.basis.orbitals}
    for index, orbital in enumerate(package.basis.orbitals):
        if orbital.site_id not in sites:
            issues.append(ValidationIssue(f"basis.orbitals[{index}].site_id", "BASIS_SITE_INDEX_OUT_OF_RANGE", "orbital references an unknown site", "Use a site ID declared by geometry."))
    active_sites = {orbital.site_id for orbital in package.basis.orbitals if orbital.active}
    if active_sites != sites:
        issues.append(ValidationIssue("basis.orbitals", "BASIS_DOFS_INCOMPLETE", "active basis does not cover every geometry site", "Declare the complete active orbital-to-site mapping and its frozen ordering."))
    for index, edge in enumerate(package.geometry.edges):
        if edge.source_site == edge.target_site:
            issues.append(ValidationIssue(f"geometry.edges[{index}]", "INVALID_GEOMETRY_INDEX", "self-loop is not a valid hopping edge", "Represent onsite terms as onsite terms."))
        if package.geometry.boundary_condition.value == "OPEN" and any(edge.wrap):
            issues.append(ValidationIssue(f"geometry.edges[{index}].wrap", "OBC_WRAP_NOT_ALLOWED", "open-boundary edges cannot wrap", "Remove the wrap vector or declare PERIODIC boundary conditions."))
    for index, term in enumerate(package.one_body.onsite_terms):
        if term.site_id not in sites or term.orbital_id not in orbitals or orbitals.get(term.orbital_id, None) and orbitals[term.orbital_id].site_id != term.site_id:
            issues.append(ValidationIssue(f"one_body.onsite_terms[{index}]", "ONSITE_INDEX_MISMATCH", "onsite term site/orbital reference is inconsistent", "Reference an orbital belonging to the declared site."))
    for index, term in enumerate(package.one_body.hopping_terms):
        if term.source_orbital not in orbitals or term.target_orbital not in orbitals:
            issues.append(ValidationIssue(f"one_body.hopping_terms[{index}]", "HOPPING_INDEX_OUT_OF_RANGE", "hopping references an unknown orbital", "Use orbital IDs declared by basis."))
    _validate_hermiticity(package, orbitals, issues)
    for index, term in enumerate(package.interactions):
        if any(site not in sites for site in term.site_ids) or any(orbital not in orbitals for orbital in term.orbital_ids):
            issues.append(ValidationIssue(f"interactions[{index}]", "INTERACTION_INDEX_OUT_OF_RANGE", "interaction references an unknown site or orbital", "Use IDs declared by geometry and basis."))
        if term.kind is InteractionKind.ONSITE_HUBBARD_U:
            if not math.isfinite(term.value):
                issues.append(ValidationIssue(f"interactions[{index}].value", "INVALID_HUBBARD_U", "onsite Hubbard U must be finite", "Declare a finite onsite U in eV."))
            if len(term.orbital_ids) != 1 or term.orbital_ids[0] not in orbitals or orbitals[term.orbital_ids[0]].site_id != term.site_ids[0]:
                issues.append(ValidationIssue(f"interactions[{index}]", "HUBBARD_U_LOCATION_MISMATCH", "onsite U must target one orbital on its declared site", "Align the U site and orbital references."))
    for index, state in enumerate(package.state_points):
        if state.ensemble != "CANONICAL" or state.temperature_definition != "ZERO_T":
            continue
        if state.n_up is None or state.n_down is None or state.n_up > len(orbitals) or state.n_down > len(orbitals):
            issues.append(ValidationIssue(f"state_points[{index}]", "INVALID_PARTICLE_SECTOR", "particle sector is outside the basis range", "Set 0 <= N_up,N_down <= number of active orbitals."))
        if state.n_total is not None and state.n_total > 2 * len(orbitals):
            issues.append(ValidationIssue(f"state_points[{index}].n_total", "INVALID_PARTICLE_SECTOR", "total particle count exceeds the spinful basis", "Set n_total equal to n_up+n_down within the basis range."))


def _validate_hermiticity(package: EffectiveModelPackage, orbitals: Mapping[str, Any], issues: list[ValidationIssue]) -> None:
    terms = package.one_body.hopping_terms
    if not package.one_body.stores_hermitian_conjugate:
        return
    directed = {(term.source_orbital, term.target_orbital): term.value for term in terms}
    tolerance = package.one_body.numerical_zero_tolerance
    for (source, target), value in directed.items():
        reverse = directed.get((target, source))
        if (reverse is not None and not math.isclose(value, reverse, abs_tol=tolerance, rel_tol=0)) or (package.one_body.stores_hermitian_conjugate and reverse is None):
            issues.append(ValidationIssue("one_body.hopping_terms", "HAMILTONIAN_NON_HERMITIAN", "stored hopping terms are not Hermitian", "Provide matching conjugate terms or declare the implicit-conjugate storage convention."))
            return


def _extract_features(package: EffectiveModelPackage) -> ModelFeatures:
    active = tuple(orbital for orbital in package.basis.orbitals if orbital.active)
    nonlocal_kinds = {InteractionKind.NONLOCAL_DENSITY_V, InteractionKind.DENSITY_DENSITY}
    return ModelFeatures(
        single_band=package.model_family is ModelFamily.SINGLE_BAND_HUBBARD and len(active) == package.geometry.num_sites,
        multi_orbital=len(active) > package.geometry.num_sites,
        real_hopping=not package.one_body.is_complex,
        complex_hopping=package.one_body.is_complex,
        onsite_u=any(term.kind is InteractionKind.ONSITE_HUBBARD_U for term in package.interactions),
        nonlocal_interaction=any(term.kind in nonlocal_kinds for term in package.interactions),
        soc=package.basis.includes_soc,
        temperature="ZERO_T" if all(state.temperature_definition == "ZERO_T" for state in package.state_points) else "FINITE_T",
        ensemble="CANONICAL" if all(state.ensemble == "CANONICAL" for state in package.state_points) else "GRAND_CANONICAL",
        boundary_condition=package.geometry.boundary_condition.value,
        num_sites=package.geometry.num_sites,
        num_active_orbitals=len(active),
        num_hopping_terms=len(package.one_body.hopping_terms),
        model_family=package.model_family.value,
    )


def _unsupported_features(package: EffectiveModelPackage, features: ModelFeatures) -> list[ValidationIssue]:
    unsupported: list[ValidationIssue] = []
    checks = ((package.model_family is not ModelFamily.SINGLE_BAND_HUBBARD, "MULTI_ORBITAL_UNSUPPORTED", "model_family"), (features.soc, "SOC_UNSUPPORTED", "basis.includes_soc"), (features.complex_hopping, "COMPLEX_HOPPING_UNSUPPORTED", "one_body.is_complex"), (features.multi_orbital, "MULTI_ORBITAL_UNSUPPORTED", "basis.orbitals"), (features.nonlocal_interaction, "NONLOCAL_INTERACTION_UNSUPPORTED", "interactions"), (features.temperature != "ZERO_T", "FINITE_T_UNSUPPORTED", "state_points"), (features.ensemble != "CANONICAL", "GRAND_CANONICAL_UNSUPPORTED", "state_points"))
    for present, code, path in checks:
        if present:
            unsupported.append(ValidationIssue(path, code, "model is schema-valid but outside the current MVP capability boundary", "Use a supported zero-temperature canonical single-band real-hopping onsite-U model or wait for a later capability."))
    return unsupported


def _artifact_refs(value: Any, path: str = "$") -> list[tuple[str, str, str]]:
    found: list[tuple[str, str, str]] = []
    if isinstance(value, Mapping):
        if set(value) >= {"uri", "sha256"} and isinstance(value.get("uri"), str) and isinstance(value.get("sha256"), str):
            found.append((path, value["uri"], value["sha256"]))
        for key, item in value.items():
            found.extend(_artifact_refs(item, f"{path}.{key}"))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            found.extend(_artifact_refs(item, f"{path}[{index}]"))
    return found


def _resolve_artifact(uri: str, root: Path) -> Path:
    parsed = urlsplit(uri)
    if parsed.scheme != "artifact" or not parsed.netloc:
        raise ValueError("only artifact:// URIs can be resolved")
    relative = Path(unquote(parsed.netloc + parsed.path))
    if relative.is_absolute() or ".." in relative.parts or any(part in {"", "."} for part in relative.parts):
        raise ValueError("unsafe artifact URI")
    trusted = root.resolve(strict=True)
    unresolved = trusted / relative
    current = trusted
    for part in relative.parts:
        current = current / part
        if current.is_symlink():
            raise ValueError("symlinks are forbidden at the artifact boundary")
    candidate = unresolved.resolve(strict=True)
    if candidate != trusted and trusted not in candidate.parents:
        raise ValueError("artifact escaped trusted root")
    if not candidate.is_file():
        raise ValueError("artifact must be a regular file")
    return candidate


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _failed(path: str, code: str, message: str, remediation: str) -> ValidationResult:
    return ValidationResult(ValidationStatus.PERMANENT_FAILED, None, None, (ValidationIssue(path, code, message, remediation),))
