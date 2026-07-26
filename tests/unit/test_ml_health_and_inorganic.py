from __future__ import annotations

from datetime import timedelta

import pytest
from pydantic import ValidationError

from material_agent.ml_screening.applicability import (
    classify_inorganic_composition,
)
from material_agent.ml_screening.models import (
    CheckStatus,
    InorganicReasonCode,
    ModelHealthSnapshot,
    TrustedMaterialClass,
    TrustedMaterialClassification,
)
from material_agent.ml_screening.resources import (
    artifact_pointer,
    environment_fingerprint_sha256,
)


def _trusted(material_class: TrustedMaterialClass) -> TrustedMaterialClassification:
    return TrustedMaterialClassification(
        material_class=material_class,
        source="Agent01 normalized material category",
        classifier_version="agent01-material-class-v1",
        provenance_uri="artifact://upstream/material-classification.json",
        provenance_sha256="a" * 64,
    )


def _candidate_with_classification(
    ml_candidate_factory,
    *,
    elements,
    is_inorganic,
    trusted=None,
):
    candidate = ml_candidate_factory(
        elements=elements,
        is_inorganic=is_inorganic,
    )
    payload = candidate.model_dump(mode="json")
    payload["source_structure"]["trusted_material_classification"] = (
        trusted.model_dump(mode="json") if trusted is not None else None
    )
    return type(candidate).model_validate(payload)


def test_no_carbon_periodic_structure_is_conservatively_inorganic(
    ml_candidate_factory,
    ml_policy,
) -> None:
    candidate = ml_candidate_factory(is_inorganic=None)
    result = classify_inorganic_composition(candidate, ml_policy)
    assert result.status is CheckStatus.PASS
    assert (
        result.reason_code
        is InorganicReasonCode.NO_CARBON_PERIODIC_CRYSTAL
    )
    assert result.classifier_version == "inorganic-composition-policy-v1"
    assert result.input_provenance


def test_ambiguous_carbon_without_trusted_classification_is_unknown(
    ml_candidate_factory,
    ml_policy,
) -> None:
    candidate = _candidate_with_classification(
        ml_candidate_factory,
        elements=["C", "O"],
        is_inorganic=True,
    )
    result = classify_inorganic_composition(candidate, ml_policy)
    assert result.status is CheckStatus.UNKNOWN
    assert (
        result.reason_code
        is InorganicReasonCode.AMBIGUOUS_CARBON_COMPOSITION
    )
    assert result.used_trusted_upstream_classification is False


@pytest.mark.parametrize(
    ("material_class", "status", "reason"),
    [
        (
            TrustedMaterialClass.INORGANIC,
            CheckStatus.PASS,
            InorganicReasonCode.TRUSTED_UPSTREAM_INORGANIC,
        ),
        (
            TrustedMaterialClass.ORGANIC,
            CheckStatus.FAIL,
            InorganicReasonCode.TRUSTED_UPSTREAM_ORGANIC,
        ),
        (
            TrustedMaterialClass.METAL_ORGANIC,
            CheckStatus.FAIL,
            InorganicReasonCode.TRUSTED_UPSTREAM_ORGANIC,
        ),
        (
            TrustedMaterialClass.MOLECULAR,
            CheckStatus.FAIL,
            InorganicReasonCode.EXPLICIT_MOLECULAR_SYSTEM,
        ),
    ],
)
def test_trusted_upstream_classification_is_mapped_with_provenance(
    material_class,
    status,
    reason,
    ml_candidate_factory,
    ml_policy,
) -> None:
    candidate = _candidate_with_classification(
        ml_candidate_factory,
        elements=["C", "O"],
        is_inorganic=None,
        trusted=_trusted(material_class),
    )
    result = classify_inorganic_composition(candidate, ml_policy)
    assert result.status is status
    assert result.reason_code is reason
    assert result.used_trusted_upstream_classification is True
    assert result.input_provenance["source_classifier_version"] == (
        "agent01-material-class-v1"
    )


def test_fake_health_has_fixed_validity_and_real_health_semantics_are_separate(
    ml_health,
) -> None:
    assert ml_health.tested_at < ml_health.expires_at
    assert ml_health.installed_package_versions == {
        "material-screening-agent": "0.1.0"
    }
    payload = ml_health.model_dump(mode="json")
    payload["installed_package_versions"]["torch"] = "invented"
    with pytest.raises(ValidationError, match="cannot invent"):
        ModelHealthSnapshot.model_validate(payload)


@pytest.mark.parametrize("offset_days", [-2, 1])
def test_plan_rejects_expired_or_future_health(
    offset_days,
    ml_candidate_factory,
    ml_plan_factory,
    ml_health,
    ml_fixed_time,
) -> None:
    payload = ml_health.model_dump(mode="json")
    tested_at = ml_fixed_time + timedelta(days=offset_days)
    payload["tested_at"] = tested_at.isoformat()
    payload["expires_at"] = (tested_at + timedelta(days=1)).isoformat()
    shifted = ModelHealthSnapshot.model_validate(payload)
    with pytest.raises(ValueError, match="not valid"):
        ml_plan_factory(
            [ml_candidate_factory()],
            health=shifted,
            created_at=ml_fixed_time,
        )


def test_plan_rejects_wrong_expiry_environment_and_installed_version(
    ml_candidate_factory,
    ml_plan_factory,
    ml_health,
) -> None:
    payload = ml_health.model_dump(mode="json")
    payload["expires_at"] = (
        ml_health.expires_at + timedelta(seconds=1)
    ).isoformat()
    wrong_expiry = ModelHealthSnapshot.model_validate(payload)
    with pytest.raises(ValueError, match="validity window"):
        ml_plan_factory([ml_candidate_factory()], health=wrong_expiry)

    payload = ml_health.model_dump(mode="json")
    payload["platform"] = "forged-platform"
    wrong_environment = ModelHealthSnapshot.model_validate(payload)
    with pytest.raises(ValueError, match="fingerprint"):
        ml_plan_factory([ml_candidate_factory()], health=wrong_environment)

    payload = ml_health.model_dump(mode="json")
    payload["installed_package_versions"]["material-screening-agent"] = "9.9"
    payload["environment_fingerprint_sha256"] = environment_fingerprint_sha256(
        python_version=payload["python_version"],
        installed_package_versions=payload["installed_package_versions"],
        platform=payload["platform"],
        architecture=payload["architecture"],
        device_policy=payload["device_policy"],
        available_devices=payload["available_devices"],
        package_lock_sha256=payload["package_lock_sha256"],
    )
    wrong_version = ModelHealthSnapshot.model_validate(payload)
    with pytest.raises(ValueError, match="package version"):
        ml_plan_factory([ml_candidate_factory()], health=wrong_version)


def test_copying_old_health_and_only_changing_timestamps_breaks_hash_binding(
    ml_candidate_factory,
    ml_plan_factory,
    ml_health,
) -> None:
    frozen_ref = artifact_pointer("artifact://fixtures/health.json", ml_health)
    payload = ml_health.model_dump(mode="json")
    payload["tested_at"] = (
        ml_health.tested_at + timedelta(hours=1)
    ).isoformat()
    payload["expires_at"] = (
        ml_health.expires_at + timedelta(hours=1)
    ).isoformat()
    copied = ModelHealthSnapshot.model_validate(payload)
    with pytest.raises(ValueError, match="artifact hash"):
        ml_plan_factory(
            [ml_candidate_factory()],
            health=copied,
            health_artifact=frozen_ref,
        )
