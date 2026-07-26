"""Rule-based model applicability assessment."""

from __future__ import annotations

from typing import Any

from material_agent.ml_screening.models import (
    ApplicabilityAssessment,
    ApplicabilityCheck,
    ApplicabilityReasonCode,
    ApplicabilityStatus,
    CheckSeverity,
    CheckStatus,
    InorganicCompositionAssessment,
    InorganicReasonCode,
    MLCandidateInput,
    MLModelSpec,
    MLScreeningPolicy,
    ModelHealthSnapshot,
    SmokeTestStatus,
    TrustedMaterialClass,
)


def assess_applicability(
    candidate: MLCandidateInput,
    model: MLModelSpec,
    health: ModelHealthSnapshot,
    policy: MLScreeningPolicy,
) -> ApplicabilityAssessment:
    structure = candidate.source_structure
    checks: list[ApplicabilityCheck] = []

    checks.append(
        _boolean_check(
            "structure_integrity",
            structure.parseable is True and structure.hash_verified is True,
            unknown=(
                structure.parseable is None
                or structure.hash_verified is None
            ),
            observed={
                "parseable": structure.parseable,
                "hash_verified": structure.hash_verified,
            },
            source="Agent01 structure artifact",
            fail_reason=(
                ApplicabilityReasonCode.STRUCTURE_UNPARSEABLE
                if structure.parseable is False
                else ApplicabilityReasonCode.STRUCTURE_HASH_MISMATCH
            ),
        )
    )
    checks.append(
        _boolean_check(
            "periodic_crystal",
            structure.is_periodic is True,
            unknown=structure.is_periodic is None,
            observed=structure.is_periodic,
            source="normalized structure metadata",
            fail_reason=ApplicabilityReasonCode.NON_PERIODIC_STRUCTURE,
        )
    )
    inorganic = classify_inorganic_composition(candidate, policy)
    checks.append(
        ApplicabilityCheck(
            check_id="inorganic_composition",
            status=inorganic.status,
            severity=(
                CheckSeverity.INFO
                if inorganic.status is CheckStatus.PASS
                else (
                    CheckSeverity.BLOCKING
                    if inorganic.status is CheckStatus.FAIL
                    else CheckSeverity.WARNING
                )
            ),
            expected="periodic bulk inorganic composition",
            observed={
                "status": inorganic.status,
                "reason_code": inorganic.reason_code,
            },
            source=inorganic.input_source,
            message=(
                "inorganic composition passed"
                if inorganic.status is CheckStatus.PASS
                else (
                    "composition is explicitly outside the inorganic domain"
                    if inorganic.status is CheckStatus.FAIL
                    else "inorganic composition could not be determined"
                )
            ),
            reason_code=(
                ApplicabilityReasonCode.APPLICABLE
                if inorganic.status is CheckStatus.PASS
                else ApplicabilityReasonCode.NON_INORGANIC_STRUCTURE
            ),
        )
    )

    num_sites = structure.num_sites or candidate.num_sites
    checks.append(
        _comparison_check(
            "num_sites",
            observed=num_sites,
            expected=f"<= {min(policy.max_num_sites, model.max_num_sites_policy)}",
            passed=(
                num_sites is not None
                and num_sites
                <= min(policy.max_num_sites, model.max_num_sites_policy)
            ),
            unknown=num_sites is None,
            source="candidate and model policy",
            fail_reason=ApplicabilityReasonCode.NUM_SITES_EXCEEDED,
        )
    )

    unsupported = sorted(set(candidate.elements) - set(model.supported_elements))
    if not unsupported:
        element_status = CheckStatus.PASS
        element_reason = ApplicabilityReasonCode.APPLICABLE
        element_message = "all elements are in the audited training coverage"
    elif model.element_coverage_complete:
        element_status = CheckStatus.FAIL
        element_reason = ApplicabilityReasonCode.UNSUPPORTED_ELEMENT
        element_message = f"unsupported elements: {unsupported}"
    else:
        element_status = CheckStatus.UNKNOWN
        element_reason = ApplicabilityReasonCode.ELEMENT_COVERAGE_UNKNOWN
        element_message = (
            "training coverage is incomplete for elements: "
            f"{unsupported}"
        )
    checks.append(
        ApplicabilityCheck(
            check_id="element_training_coverage",
            status=element_status,
            severity=(
                CheckSeverity.BLOCKING
                if element_status is CheckStatus.FAIL
                else (
                    CheckSeverity.WARNING
                    if element_status is CheckStatus.UNKNOWN
                    else CheckSeverity.INFO
                )
            ),
            expected=sorted(model.supported_elements),
            observed=sorted(candidate.elements),
            source=model.supported_elements_source,
            message=element_message,
            reason_code=element_reason,
        )
    )

    dimensionality = structure.dimensionality
    if dimensionality is None:
        dimension_status = CheckStatus.UNKNOWN
        dimension_reason = ApplicabilityReasonCode.DIMENSIONALITY_UNKNOWN
        dimension_message = "structural dimensionality is unavailable"
    elif dimensionality in model.supported_dimensionalities and (
        dimensionality == policy.supported_dimensionality
    ):
        dimension_status = CheckStatus.PASS
        dimension_reason = ApplicabilityReasonCode.APPLICABLE
        dimension_message = "structure is a supported 3D bulk crystal"
    else:
        dimension_status = CheckStatus.FAIL
        dimension_reason = (
            ApplicabilityReasonCode.DIMENSIONALITY_NOT_BULK
        )
        dimension_message = (
            f"dimensionality {dimensionality} is outside the v1 bulk domain"
        )
    checks.append(
        ApplicabilityCheck(
            check_id="dimensionality",
            status=dimension_status,
            severity=(
                CheckSeverity.BLOCKING
                if dimension_status is CheckStatus.FAIL
                else (
                    CheckSeverity.WARNING
                    if dimension_status is CheckStatus.UNKNOWN
                    else CheckSeverity.INFO
                )
            ),
            expected=policy.supported_dimensionality,
            observed=dimensionality,
            source="Agent01 dimensionality provenance",
            message=dimension_message,
            reason_code=dimension_reason,
        )
    )

    checks.append(
        _boolean_check(
            "structure_conversion",
            structure.ase_compatible is True,
            unknown=structure.ase_compatible is None,
            observed=structure.ase_compatible,
            source="normalized Pymatgen/ASE compatibility metadata",
            fail_reason=ApplicabilityReasonCode.STRUCTURE_CONVERSION_FAILED,
        )
    )

    numeric_ok = (
        structure.has_finite_values is True
        and structure.positive_volume is True
    )
    numeric_unknown = (
        structure.has_finite_values is None
        or structure.positive_volume is None
    )
    numeric_reason = (
        ApplicabilityReasonCode.INVALID_NUMERIC_STRUCTURE_DATA
        if structure.has_finite_values is False
        else ApplicabilityReasonCode.INVALID_CELL_VOLUME
    )
    checks.append(
        _boolean_check(
            "finite_structure_data",
            numeric_ok,
            unknown=numeric_unknown,
            observed={
                "finite": structure.has_finite_values,
                "positive_volume": structure.positive_volume,
            },
            source="normalized structure validation",
            fail_reason=numeric_reason,
        )
    )

    minimum_distance = structure.minimum_distance_angstrom
    checks.append(
        _comparison_check(
            "minimum_interatomic_distance",
            observed=minimum_distance,
            expected=(
                f">= {policy.minimum_interatomic_distance_angstrom} angstrom"
            ),
            passed=(
                minimum_distance is not None
                and minimum_distance
                >= policy.minimum_interatomic_distance_angstrom
            ),
            unknown=minimum_distance is None,
            source="normalized structure validation",
            fail_reason=ApplicabilityReasonCode.ATOM_OVERLAP,
        )
    )

    health_matches = (
        health.model_id == model.model_id
        and health.checkpoint_sha256 == model.checkpoint_sha256
        and health.package_lock_sha256 == model.package_lock_sha256
        and health.adapter_version == model.adapter_version
        and health.is_mock == model.is_mock
        and health.smoke_test_status is SmokeTestStatus.PASS
    )
    checks.append(
        _boolean_check(
            "model_health",
            health_matches,
            unknown=False,
            observed={
                "status": health.smoke_test_status,
                "model_id": health.model_id,
                "device_policy": health.device_policy,
            },
            source=health.schema_version,
            fail_reason=ApplicabilityReasonCode.MODEL_HEALTH_FAILED,
        )
    )

    statuses = {check.status for check in checks}
    if CheckStatus.FAIL in statuses:
        status = ApplicabilityStatus.NOT_APPLICABLE
    elif CheckStatus.UNKNOWN in statuses:
        status = ApplicabilityStatus.UNKNOWN
    else:
        status = ApplicabilityStatus.APPLICABLE

    reasons = list(
        dict.fromkeys(
            check.reason_code
            for check in checks
            if check.status is not CheckStatus.PASS
        )
    )
    if not reasons:
        reasons = [ApplicabilityReasonCode.APPLICABLE]

    is_real = not model.is_mock and not health.is_mock
    eligible_for_real = (
        status is ApplicabilityStatus.APPLICABLE and is_real
    )
    return ApplicabilityAssessment(
        candidate_id=candidate.candidate_id,
        structure_id=structure.structure_id,
        model_id=model.model_id,
        status=status,
        eligible_for_real_inference=eligible_for_real,
        eligible_for_l2=eligible_for_real,
        inorganic_classification=inorganic,
        checks=checks,
        reasons=reasons,
        warnings=[
            check.message
            for check in checks
            if check.status is CheckStatus.UNKNOWN
        ],
        policy_version=policy.policy_version,
    )


def classify_inorganic_composition(
    candidate: MLCandidateInput,
    policy: MLScreeningPolicy,
) -> InorganicCompositionAssessment:
    """Apply the conservative, provenance-bearing v1 composition policy."""

    structure = candidate.source_structure
    trusted = structure.trusted_material_classification
    if trusted is not None:
        provenance = {
            "source": trusted.source,
            "source_classifier_version": trusted.classifier_version,
            "provenance_uri": trusted.provenance_uri,
            "provenance_sha256": trusted.provenance_sha256,
        }
        if trusted.material_class is TrustedMaterialClass.INORGANIC:
            status = CheckStatus.PASS
            reason = InorganicReasonCode.TRUSTED_UPSTREAM_INORGANIC
        elif trusted.material_class in {
            TrustedMaterialClass.ORGANIC,
            TrustedMaterialClass.METAL_ORGANIC,
        }:
            status = CheckStatus.FAIL
            reason = InorganicReasonCode.TRUSTED_UPSTREAM_ORGANIC
        elif trusted.material_class is TrustedMaterialClass.MOLECULAR:
            status = CheckStatus.FAIL
            reason = InorganicReasonCode.EXPLICIT_MOLECULAR_SYSTEM
        else:
            status = CheckStatus.UNKNOWN
            reason = InorganicReasonCode.CLASSIFICATION_INPUT_MISSING
        return InorganicCompositionAssessment(
            classifier_version=policy.inorganic_classifier_version,
            status=status,
            reason_code=reason,
            input_source="trusted upstream material classification",
            input_provenance=provenance,
            used_trusted_upstream_classification=True,
        )

    provenance = {
        "source": "Agent02 normalized composition",
        "elements": ",".join(sorted(candidate.elements)),
        "periodicity": str(structure.is_periodic),
        "legacy_is_inorganic": str(structure.is_inorganic),
    }
    if "C" in candidate.elements:
        return InorganicCompositionAssessment(
            classifier_version=policy.inorganic_classifier_version,
            status=CheckStatus.UNKNOWN,
            reason_code=InorganicReasonCode.AMBIGUOUS_CARBON_COMPOSITION,
            input_source="composition-only conservative rule",
            input_provenance=provenance,
            used_trusted_upstream_classification=False,
        )
    if structure.is_inorganic is False:
        return InorganicCompositionAssessment(
            classifier_version=policy.inorganic_classifier_version,
            status=CheckStatus.FAIL,
            reason_code=(
                InorganicReasonCode.EXPLICIT_NON_INORGANIC_METADATA
            ),
            input_source="normalized structure metadata",
            input_provenance=provenance,
            used_trusted_upstream_classification=False,
        )
    if structure.is_periodic is True:
        return InorganicCompositionAssessment(
            classifier_version=policy.inorganic_classifier_version,
            status=CheckStatus.PASS,
            reason_code=InorganicReasonCode.NO_CARBON_PERIODIC_CRYSTAL,
            input_source="composition-only conservative rule",
            input_provenance=provenance,
            used_trusted_upstream_classification=False,
        )
    return InorganicCompositionAssessment(
        classifier_version=policy.inorganic_classifier_version,
        status=CheckStatus.UNKNOWN,
        reason_code=InorganicReasonCode.CLASSIFICATION_INPUT_MISSING,
        input_source="composition-only conservative rule",
        input_provenance=provenance,
        used_trusted_upstream_classification=False,
    )


def _boolean_check(
    check_id: str,
    passed: bool,
    *,
    unknown: bool,
    observed: Any,
    source: str,
    fail_reason: ApplicabilityReasonCode,
) -> ApplicabilityCheck:
    if unknown:
        status = CheckStatus.UNKNOWN
        severity = CheckSeverity.WARNING
        message = f"{check_id} could not be determined"
    elif passed:
        status = CheckStatus.PASS
        severity = CheckSeverity.INFO
        message = f"{check_id} passed"
    else:
        status = CheckStatus.FAIL
        severity = CheckSeverity.BLOCKING
        message = f"{check_id} failed"
    return ApplicabilityCheck(
        check_id=check_id,
        status=status,
        severity=severity,
        expected=True,
        observed=observed,
        source=source,
        message=message,
        reason_code=(
            ApplicabilityReasonCode.APPLICABLE
            if status is CheckStatus.PASS
            else fail_reason
        ),
    )


def _comparison_check(
    check_id: str,
    *,
    observed: Any,
    expected: Any,
    passed: bool,
    unknown: bool,
    source: str,
    fail_reason: ApplicabilityReasonCode,
) -> ApplicabilityCheck:
    if unknown:
        status = CheckStatus.UNKNOWN
        severity = CheckSeverity.WARNING
        message = f"{check_id} could not be determined"
    elif passed:
        status = CheckStatus.PASS
        severity = CheckSeverity.INFO
        message = f"{check_id} passed"
    else:
        status = CheckStatus.FAIL
        severity = CheckSeverity.BLOCKING
        message = f"{check_id} failed"
    return ApplicabilityCheck(
        check_id=check_id,
        status=status,
        severity=severity,
        expected=expected,
        observed=observed,
        source=source,
        message=message,
        reason_code=(
            ApplicabilityReasonCode.APPLICABLE
            if status is CheckStatus.PASS
            else fail_reason
        ),
    )
