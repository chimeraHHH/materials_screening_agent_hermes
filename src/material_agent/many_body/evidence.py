"""Fail-closed evidence ceiling rules for the task-3 control boundary."""

from __future__ import annotations

from .models import (
    EffectiveModelPackage,
    EvidenceLevel,
    MaterialLinkageStatus,
    ModelDefinitionStatus,
    SolverValidationStatus,
)


def evidence_ceiling(
    package: EffectiveModelPackage,
    *,
    is_mock: bool,
    solver_validation_status: SolverValidationStatus,
    material_linkage_status: MaterialLinkageStatus,
) -> EvidenceLevel:
    """Return the highest permitted level; this function never promotes evidence."""

    if is_mock or package.fixture:
        return EvidenceLevel.L1_RETRIEVED
    if material_linkage_status is MaterialLinkageStatus.NONE or material_linkage_status is MaterialLinkageStatus.PARTIAL_PROVENANCE:
        return EvidenceLevel.L1_RETRIEVED
    if solver_validation_status in (SolverValidationStatus.NUMERICALLY_VALIDATED, SolverValidationStatus.BENCHMARK_VALIDATED):
        return EvidenceLevel.L3_DFT_VALIDATED
    return EvidenceLevel.L1_RETRIEVED


def assert_evidence_allowed(
    package: EffectiveModelPackage,
    *,
    requested_level: EvidenceLevel,
    is_mock: bool,
    solver_validation_status: SolverValidationStatus,
    material_linkage_status: MaterialLinkageStatus,
    model_definition_status: ModelDefinitionStatus = ModelDefinitionStatus.VALIDATED_MODEL,
    backend_id: str | None = None,
    backend_version: str | None = None,
) -> None:
    """Reject direct L4 declarations unless all task-3 prerequisites hold.

    Task 3 intentionally does not provide a path to L4.  Even a hypothetical
    future caller must pass through real solver validation and expert linkage.
    """

    ceiling = evidence_ceiling(
        package,
        is_mock=is_mock,
        solver_validation_status=solver_validation_status,
        material_linkage_status=material_linkage_status,
    )
    if requested_level is EvidenceLevel.L4_MANY_BODY_VALIDATED and (
        is_mock
        or package.fixture
        or (backend_id == "exact-diagonalization" and backend_version is not None and "planned" in backend_version.lower())
        or model_definition_status is not ModelDefinitionStatus.VALIDATED_MODEL
        or solver_validation_status not in (SolverValidationStatus.NUMERICALLY_VALIDATED, SolverValidationStatus.BENCHMARK_VALIDATED)
        or material_linkage_status is not MaterialLinkageStatus.EXPERT_APPROVED
    ):
        raise ValueError("L4_MANY_BODY_VALIDATED is not permitted by the Agent04 evidence ceiling")
    if requested_level.value.startswith("L4") and requested_level is not EvidenceLevel.L4_MANY_BODY_VALIDATED:
        raise ValueError("unsupported L4 evidence declaration")
    if requested_level.value > ceiling.value and requested_level is EvidenceLevel.L5_EXPERT_REVIEWED:
        raise ValueError("requested evidence exceeds the Agent04 ceiling")
