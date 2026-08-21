"""Fail-closed lifecycle contracts after formal flat-band metric assembly.

This module models the *control and evidence chain* from development promotion
through a sanitized public benchmark release.  It deliberately does not run a
benchmark and it does not accept locally constructed metric rows as scientific
evidence.  ``DevelopmentMetricRowV1`` is a content-addressed value/schema row;
formal consumers must additionally replay the referenced future
``AnalysisInputV2`` formal-verifier attestation against the real upstream
release.  This module only checks that the opaque, typed attestation binds the
exact row/result payload consumed by each lifecycle release.

The intended one-way chain is::

    AnalysisInputV2 + formal verifier attestation
      -> DevelopmentPromotionReleaseV1
      -> FusionConfigurationReleaseV1
      -> DevelopmentFusionGateReleaseV1
      -> LockedTestAuthorizationReleaseV1
      -> LockedAnnotationUnsealReleaseV1          (one deterministic identity)
      -> ClaimSupportReleaseV1
      -> ScientificReviewReleaseV1
      -> PublicReleaseAuthorizationV1
      -> PublicBenchmarkResultReleaseV1

Every owned row/release is content addressed and has a builder plus an exact
replay helper.  Opaque upstream references remain typed ``ID + SHA-256`` pairs;
their existence/authenticity must be established by their owning verifier.
"""

from __future__ import annotations

import hashlib
import hmac
import re
from collections.abc import Mapping, Sequence
from datetime import datetime
from enum import StrEnum
from typing import Annotated, Literal, TypeVar

from pydantic import Field, field_validator, model_validator

from material_agent.inspiration.models import (
    Identifier,
    Sha256,
    ShortText,
    StrictModel,
    canonical_sha256,
    deterministic_id,
)
from material_agent.research.flatband_execution import ResearchSystemId

ModelT = TypeVar("ModelT", bound=StrictModel)
Score = Annotated[float, Field(ge=0.0, le=1.0, allow_inf_nan=False)]
Delta = Annotated[float, Field(ge=-1.0, le=1.0, allow_inf_nan=False)]
PValue = Annotated[float, Field(ge=0.0, le=1.0, allow_inf_nan=False)]


DEVELOPMENT_CASE_COUNT = 60
LOCKED_IID_CASE_COUNT = 30
LOCKED_OOD_CASE_COUNT = 30
DEVELOPMENT_ANDCG_MIN_DELTA = 0.03
DEVELOPMENT_EVIDENCE_MIN_DELTA = -0.03
DEVELOPMENT_DUPLICATE_MAX_DELTA = 0.02
DEVELOPMENT_SUCCESS_MIN_DELTA = -0.02
E2_B_MIN_INCREMENT_OVER_E2_A = 0.01
LOCKED_PRIMARY_ANDCG_MIN_DELTA = 0.05
LOCKED_SAFETY_MIN_DELTA = -0.05
LOCKED_DUPLICATE_MAX_DELTA = 0.05
LOCKED_INVALID_CASE_MAX_RATE = 0.05
LOCKED_UNRESOLVABLE_MAX_RATE = 0.05


def _timestamp(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise ValueError("timestamp must be RFC3339-compatible") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("timestamp must include a UTC offset")
    return parsed


def _round(value: float) -> float:
    return round(float(value), 12)


def _assert_addressed(
    value: StrictModel,
    *,
    id_field: str,
    sha_field: str,
    prefix: str,
    fixed_id: str | None = None,
) -> None:
    semantic = value.model_dump(mode="python", exclude={id_field, sha_field})
    digest = canonical_sha256(semantic)
    if getattr(value, sha_field) != digest:
        raise ValueError(f"{sha_field} does not match semantic content")
    expected_id = fixed_id or deterministic_id(prefix, {sha_field: digest})
    if getattr(value, id_field) != expected_id:
        raise ValueError(f"{id_field} does not match semantic content")


def _build_addressed(
    model_type: type[ModelT],
    *,
    id_field: str,
    sha_field: str,
    prefix: str,
    values: dict[str, object],
    fixed_id: str | None = None,
) -> ModelT:
    draft = model_type.model_construct(**values)
    digest = canonical_sha256(
        draft.model_dump(mode="python", exclude={id_field, sha_field})
    )
    return model_type.model_validate(
        {
            **values,
            sha_field: digest,
            id_field: fixed_id or deterministic_id(prefix, {sha_field: digest}),
        }
    )


def _revalidate(value: ModelT, model_type: type[ModelT]) -> ModelT:
    return model_type.model_validate(
        value.model_dump(mode="python", round_trip=True)
    )


def _require_after(later: str, earlier: str, label: str) -> None:
    if _timestamp(later) <= _timestamp(earlier):
        raise ValueError(f"{label} must be strictly later")


class LifecycleArtifactType(StrEnum):
    ANALYSIS_INPUT_V2 = "ANALYSIS_INPUT_V2"
    ANALYSIS_INPUT_V2_FORMAL_VERIFIER = "ANALYSIS_INPUT_V2_FORMAL_VERIFIER"
    ANALYSIS_INPUT_V2_FORMAL_VERIFIER_ATTESTATION = (
        "ANALYSIS_INPUT_V2_FORMAL_VERIFIER_ATTESTATION"
    )
    SYSTEM_CONFIGURATION = "SYSTEM_CONFIGURATION"
    BENCHMARK_SPLIT_MANIFEST = "BENCHMARK_SPLIT_MANIFEST"
    PRIVATE_LOCKED_LABEL_CUSTODY = "PRIVATE_LOCKED_LABEL_CUSTODY"
    ANALYSIS_CODE_RELEASE = "ANALYSIS_CODE_RELEASE"
    ANALYSIS_ENVIRONMENT_RELEASE = "ANALYSIS_ENVIRONMENT_RELEASE"
    STATISTICAL_ANALYSIS_PLAN = "STATISTICAL_ANALYSIS_PLAN"
    LOCKED_EXECUTION_RELEASE = "LOCKED_EXECUTION_RELEASE"
    SCIENTIFIC_REVIEWER_IDENTITY_ATTESTATION = (
        "SCIENTIFIC_REVIEWER_IDENTITY_ATTESTATION"
    )
    RELEASE_AUTHORITY_POLICY = "RELEASE_AUTHORITY_POLICY"
    LICENSE_RELEASE_ATTESTATION = "LICENSE_RELEASE_ATTESTATION"
    PRIVACY_RELEASE_ATTESTATION = "PRIVACY_RELEASE_ATTESTATION"
    CUSTODY_RELEASE_ATTESTATION = "CUSTODY_RELEASE_ATTESTATION"
    DEVELOPMENT_PROMOTION_RELEASE = "DEVELOPMENT_PROMOTION_RELEASE"
    FUSION_CONFIGURATION_RELEASE = "FUSION_CONFIGURATION_RELEASE"
    DEVELOPMENT_FUSION_GATE_RELEASE = "DEVELOPMENT_FUSION_GATE_RELEASE"
    LOCKED_LABEL_SEAL = "LOCKED_LABEL_SEAL"
    LOCKED_TEST_AUTHORIZATION = "LOCKED_TEST_AUTHORIZATION"
    LOCKED_ANNOTATION_UNSEAL = "LOCKED_ANNOTATION_UNSEAL"
    LOCKED_UNSEAL_LEDGER = "LOCKED_UNSEAL_LEDGER"
    PROTOCOL_DEVIATION_RELEASE = "PROTOCOL_DEVIATION_RELEASE"
    CLAIM_SUPPORT_RELEASE = "CLAIM_SUPPORT_RELEASE"
    PUBLIC_BENCHMARK_PROJECTION = "PUBLIC_BENCHMARK_PROJECTION"
    SCIENTIFIC_REVIEW_RELEASE = "SCIENTIFIC_REVIEW_RELEASE"
    PUBLIC_RELEASE_AUTHORIZATION = "PUBLIC_RELEASE_AUTHORIZATION"


class TypedArtifactRefV1(StrictModel):
    """Opaque typed reference; this module does not claim the artifact exists."""

    artifact_type: LifecycleArtifactType
    artifact_id: Identifier
    artifact_sha256: Sha256


def typed_artifact_ref_v1(
    artifact_type: LifecycleArtifactType,
    artifact_id: str,
    artifact_sha256: str,
) -> TypedArtifactRefV1:
    return TypedArtifactRefV1(
        artifact_type=artifact_type,
        artifact_id=artifact_id,
        artifact_sha256=artifact_sha256,
    )


def _require_ref_type(
    ref: TypedArtifactRefV1,
    expected: LifecycleArtifactType,
    label: str,
) -> None:
    if ref.artifact_type is not expected:
        raise ValueError(f"{label} must reference {expected.value}")


def _owned_ref(
    value: StrictModel,
    *,
    artifact_type: LifecycleArtifactType,
    id_field: str,
    sha_field: str,
) -> TypedArtifactRefV1:
    return typed_artifact_ref_v1(
        artifact_type,
        getattr(value, id_field),
        getattr(value, sha_field),
    )


class ReleaseAuthorityRoleV1(StrEnum):
    SCIENTIFIC_REVIEWER_IDENTITY = "SCIENTIFIC_REVIEWER_IDENTITY"
    LICENSE_RELEASE = "LICENSE_RELEASE"
    PRIVACY_RELEASE = "PRIVACY_RELEASE"
    CUSTODY_RELEASE = "CUSTODY_RELEASE"


class ReleaseAuthorityBindingV1(StrictModel):
    role: ReleaseAuthorityRoleV1
    authority_id: Identifier
    key_commitment_sha256: Sha256


class ReleaseAuthorityPolicyV1(StrictModel):
    """Pre-result trust anchors; signing keys are never persisted."""

    schema_version: Literal["flatband-release-authority-policy-v1"] = (
        "flatband-release-authority-policy-v1"
    )
    policy_id: Identifier
    policy_sha256: Sha256
    authorities: Annotated[
        tuple[ReleaseAuthorityBindingV1, ...], Field(min_length=4, max_length=4)
    ]
    frozen_at: Annotated[str, Field(min_length=20, max_length=40)]
    external_key_material_included: Literal[False] = False
    self_signed_reference_accepted: Literal[False] = False
    scientific_conclusion: Literal[False] = False

    @field_validator("frozen_at")
    @classmethod
    def validate_time(cls, value: str) -> str:
        _timestamp(value)
        return value

    @model_validator(mode="after")
    def validate_policy(self) -> ReleaseAuthorityPolicyV1:
        roles = tuple(item.role.value for item in self.authorities)
        if roles != tuple(sorted(role.value for role in ReleaseAuthorityRoleV1)):
            raise ValueError("release authority policy must freeze each role exactly once")
        ids = tuple(item.authority_id for item in self.authorities)
        commitments = tuple(item.key_commitment_sha256 for item in self.authorities)
        if len(ids) != len(set(ids)) or len(commitments) != len(set(commitments)):
            raise ValueError("release authorities and key commitments must be injective")
        _assert_addressed(
            self,
            id_field="policy_id",
            sha_field="policy_sha256",
            prefix="release-authority-policy-v1",
        )
        return self


def _require_authority_key(value: bytes) -> bytes:
    if not isinstance(value, bytes) or len(value) < 32:
        raise ValueError("external authority key must contain at least 32 bytes")
    return value


def _authority_binding(
    policy: ReleaseAuthorityPolicyV1,
    role: ReleaseAuthorityRoleV1,
    key: bytes,
) -> ReleaseAuthorityBindingV1:
    value = _revalidate(policy, ReleaseAuthorityPolicyV1)
    secret = _require_authority_key(key)
    binding = next((item for item in value.authorities if item.role is role), None)
    if binding is None or binding.key_commitment_sha256 != hashlib.sha256(secret).hexdigest():
        raise ValueError("authority key differs from the pre-frozen role commitment")
    return binding


def _authority_signature(key: bytes, payload: Mapping[str, object]) -> str:
    return hmac.new(
        _require_authority_key(key),
        canonical_sha256(dict(payload)).encode("ascii"),
        hashlib.sha256,
    ).hexdigest()


def build_release_authority_policy_v1(
    *,
    authorities: Mapping[ReleaseAuthorityRoleV1, tuple[str, bytes]],
    frozen_at: str,
) -> ReleaseAuthorityPolicyV1:
    if set(authorities) != set(ReleaseAuthorityRoleV1):
        raise ValueError("authority policy requires all four frozen roles")
    bindings = tuple(
        sorted(
            (
                ReleaseAuthorityBindingV1(
                    role=role,
                    authority_id=authorities[role][0],
                    key_commitment_sha256=hashlib.sha256(
                        _require_authority_key(authorities[role][1])
                    ).hexdigest(),
                )
                for role in ReleaseAuthorityRoleV1
            ),
            key=lambda item: item.role.value,
        )
    )
    return _build_addressed(
        ReleaseAuthorityPolicyV1,
        id_field="policy_id",
        sha_field="policy_sha256",
        prefix="release-authority-policy-v1",
        values={"authorities": bindings, "frozen_at": frozen_at},
    )


class VerifiedPayloadKind(StrEnum):
    DEVELOPMENT_ABLATION_METRIC_ROWS = "DEVELOPMENT_ABLATION_METRIC_ROWS"
    DEVELOPMENT_FUSION_METRIC_ROWS = "DEVELOPMENT_FUSION_METRIC_ROWS"
    LOCKED_RESULT_FAMILY = "LOCKED_RESULT_FAMILY"


class FormalVerifierAttestationRefV1(StrictModel):
    """Typed address of output emitted by the future AnalysisInputV2 verifier.

    Validation here proves content binding, not signer identity or upstream
    scientific closure.  A formal integration must retrieve this artifact and
    replay the AnalysisInputV2 verifier before treating dependent releases as
    evidence.
    """

    schema_version: Literal[
        "flatband-analysis-input-v2-formal-verifier-attestation-ref-v1"
    ] = "flatband-analysis-input-v2-formal-verifier-attestation-ref-v1"
    attestation_id: Identifier
    attestation_sha256: Sha256
    analysis_input_ref: TypedArtifactRefV1
    verifier_ref: TypedArtifactRefV1
    verified_payload_kind: VerifiedPayloadKind
    verified_payload_sha256: Sha256
    attested_at: Annotated[str, Field(min_length=20, max_length=40)]
    caller_assertion_is_formal_evidence: Literal[False] = False
    upstream_exact_replay_required: Literal[True] = True

    @field_validator("attested_at")
    @classmethod
    def validate_time(cls, value: str) -> str:
        _timestamp(value)
        return value

    @model_validator(mode="after")
    def validate_attestation(self) -> FormalVerifierAttestationRefV1:
        _require_ref_type(
            self.analysis_input_ref,
            LifecycleArtifactType.ANALYSIS_INPUT_V2,
            "formal attestation subject",
        )
        _require_ref_type(
            self.verifier_ref,
            LifecycleArtifactType.ANALYSIS_INPUT_V2_FORMAL_VERIFIER,
            "formal attestation verifier",
        )
        _assert_addressed(
            self,
            id_field="attestation_id",
            sha_field="attestation_sha256",
            prefix="analysis-attestation-v1",
        )
        return self


def _assert_formal_attestation_binding(
    attestation: FormalVerifierAttestationRefV1,
    *,
    analysis_input_ref: TypedArtifactRefV1,
    payload_kind: VerifiedPayloadKind,
    payload: object,
) -> None:
    if attestation.analysis_input_ref != analysis_input_ref:
        raise ValueError("formal verifier attestation binds a foreign AnalysisInputV2")
    if attestation.verified_payload_kind is not payload_kind:
        raise ValueError("formal verifier attestation binds a different payload kind")
    if attestation.verified_payload_sha256 != canonical_sha256(payload):
        raise ValueError("formal verifier attestation does not bind the exact payload")


class DevelopmentMetricRowV1(StrictModel):
    """One development aggregate emitted by, but not replacing, AnalysisInputV2.

    Direct construction or successful schema validation is never sufficient
    evidence.  Promotion additionally requires an exact formal-verifier
    attestation over the complete expected row family.
    """

    schema_version: Literal["flatband-development-metric-row-v1"] = (
        "flatband-development-metric-row-v1"
    )
    row_id: Identifier
    row_sha256: Sha256
    analysis_input_ref: TypedArtifactRefV1
    system_id: ResearchSystemId
    case_universe_sha256: Sha256
    case_count: Literal[60] = DEVELOPMENT_CASE_COUNT
    andcg_at_5: Score
    evidence_valid_at_5: Score
    duplicate_rate_at_5: Score
    success_at_5: Score
    physical_request_budget_per_case: Annotated[int, Field(ge=0, le=8)]
    article_body_access_count: Annotated[int, Field(ge=0)] = 0
    pdf_access_count: Annotated[int, Field(ge=0)] = 0
    identity_closure_violation_count: Annotated[int, Field(ge=0)] = 0
    denominator_omission_count: Annotated[int, Field(ge=0)] = 0
    schema_only_row: Literal[True] = True
    row_alone_is_formal_evidence: Literal[False] = False
    analysis_input_v2_formal_verifier_required: Literal[True] = True
    scientific_conclusion: Literal[False] = False

    @model_validator(mode="after")
    def validate_row(self) -> DevelopmentMetricRowV1:
        _require_ref_type(
            self.analysis_input_ref,
            LifecycleArtifactType.ANALYSIS_INPUT_V2,
            "development metric row",
        )
        if self.system_id not in {
            ResearchSystemId.B0,
            ResearchSystemId.E1,
            ResearchSystemId.E2_A,
            ResearchSystemId.E2_B,
            ResearchSystemId.E3,
            ResearchSystemId.FUSION,
        }:
            raise ValueError("development metric row uses a non-development system")
        _assert_addressed(
            self,
            id_field="row_id",
            sha_field="row_sha256",
            prefix="dev-metric-row-v1",
        )
        return self


def build_development_metric_row_v1(
    *,
    analysis_input_ref: TypedArtifactRefV1,
    system_id: ResearchSystemId,
    case_universe_sha256: str,
    andcg_at_5: float,
    evidence_valid_at_5: float,
    duplicate_rate_at_5: float,
    success_at_5: float,
    physical_request_budget_per_case: int,
    article_body_access_count: int = 0,
    pdf_access_count: int = 0,
    identity_closure_violation_count: int = 0,
    denominator_omission_count: int = 0,
) -> DevelopmentMetricRowV1:
    """Build a schema row; it remains non-evidence until formally attested."""

    ref = _revalidate(analysis_input_ref, TypedArtifactRefV1)
    return _build_addressed(
        DevelopmentMetricRowV1,
        id_field="row_id",
        sha_field="row_sha256",
        prefix="dev-metric-row-v1",
        values={
            "analysis_input_ref": ref,
            "system_id": system_id,
            "case_universe_sha256": case_universe_sha256,
            "andcg_at_5": _round(andcg_at_5),
            "evidence_valid_at_5": _round(evidence_valid_at_5),
            "duplicate_rate_at_5": _round(duplicate_rate_at_5),
            "success_at_5": _round(success_at_5),
            "physical_request_budget_per_case": physical_request_budget_per_case,
            "article_body_access_count": article_body_access_count,
            "pdf_access_count": pdf_access_count,
            "identity_closure_violation_count": identity_closure_violation_count,
            "denominator_omission_count": denominator_omission_count,
        },
    )


def assert_development_metric_row_exact_replay_v1(
    row: DevelopmentMetricRowV1,
) -> None:
    value = _revalidate(row, DevelopmentMetricRowV1)
    expected = build_development_metric_row_v1(
        analysis_input_ref=value.analysis_input_ref,
        system_id=value.system_id,
        case_universe_sha256=value.case_universe_sha256,
        andcg_at_5=value.andcg_at_5,
        evidence_valid_at_5=value.evidence_valid_at_5,
        duplicate_rate_at_5=value.duplicate_rate_at_5,
        success_at_5=value.success_at_5,
        physical_request_budget_per_case=value.physical_request_budget_per_case,
        article_body_access_count=value.article_body_access_count,
        pdf_access_count=value.pdf_access_count,
        identity_closure_violation_count=value.identity_closure_violation_count,
        denominator_omission_count=value.denominator_omission_count,
    )
    if value != expected:
        raise ValueError("development metric row does not exactly replay")


class DevelopmentGateDecisionV1(StrictModel):
    system_id: ResearchSystemId
    baseline_system_id: Literal[ResearchSystemId.B0] = ResearchSystemId.B0
    andcg_delta: Delta
    evidence_valid_delta: Delta
    duplicate_rate_delta: Delta
    success_delta: Delta
    request_budget_delta: Annotated[int, Field(ge=-8, le=8)]
    andcg_guardrail_passed: bool
    evidence_guardrail_passed: bool
    duplicate_guardrail_passed: bool
    success_guardrail_passed: bool
    request_budget_guardrail_passed: bool
    zero_body_pdf_guardrail_passed: bool
    identity_closure_guardrail_passed: bool
    denominator_guardrail_passed: bool
    passed: bool
    observed_guardrails_not_inference: Literal[True] = True


def _derive_gate(
    baseline: DevelopmentMetricRowV1,
    candidate: DevelopmentMetricRowV1,
) -> DevelopmentGateDecisionV1:
    if baseline.system_id is not ResearchSystemId.B0:
        raise ValueError("development Gate baseline must be B0")
    if candidate.system_id is ResearchSystemId.B0:
        raise ValueError("development Gate candidate cannot be B0")
    if (
        baseline.analysis_input_ref,
        baseline.case_universe_sha256,
        baseline.case_count,
    ) != (
        candidate.analysis_input_ref,
        candidate.case_universe_sha256,
        candidate.case_count,
    ):
        raise ValueError("development Gate rows differ in analysis input or denominator")
    andcg_delta = _round(candidate.andcg_at_5 - baseline.andcg_at_5)
    evidence_delta = _round(
        candidate.evidence_valid_at_5 - baseline.evidence_valid_at_5
    )
    duplicate_delta = _round(
        candidate.duplicate_rate_at_5 - baseline.duplicate_rate_at_5
    )
    success_delta = _round(candidate.success_at_5 - baseline.success_at_5)
    request_delta = (
        candidate.physical_request_budget_per_case
        - baseline.physical_request_budget_per_case
    )
    checks = (
        andcg_delta >= DEVELOPMENT_ANDCG_MIN_DELTA,
        evidence_delta >= DEVELOPMENT_EVIDENCE_MIN_DELTA,
        duplicate_delta <= DEVELOPMENT_DUPLICATE_MAX_DELTA,
        success_delta >= DEVELOPMENT_SUCCESS_MIN_DELTA,
        request_delta <= 0,
        candidate.article_body_access_count == 0
        and candidate.pdf_access_count == 0,
        candidate.identity_closure_violation_count == 0,
        candidate.denominator_omission_count == 0,
    )
    return DevelopmentGateDecisionV1(
        system_id=candidate.system_id,
        andcg_delta=andcg_delta,
        evidence_valid_delta=evidence_delta,
        duplicate_rate_delta=duplicate_delta,
        success_delta=success_delta,
        request_budget_delta=request_delta,
        andcg_guardrail_passed=checks[0],
        evidence_guardrail_passed=checks[1],
        duplicate_guardrail_passed=checks[2],
        success_guardrail_passed=checks[3],
        request_budget_guardrail_passed=checks[4],
        zero_body_pdf_guardrail_passed=checks[5],
        identity_closure_guardrail_passed=checks[6],
        denominator_guardrail_passed=checks[7],
        passed=all(checks),
    )


class E2SelectionReason(StrEnum):
    NEITHER_PASSED = "NEITHER_PASSED"
    ONLY_E2_A_PASSED = "ONLY_E2_A_PASSED"
    ONLY_E2_B_PASSED = "ONLY_E2_B_PASSED"
    BOTH_PASSED_E2_A_PARSIMONY = "BOTH_PASSED_E2_A_PARSIMONY"
    BOTH_PASSED_E2_B_PLUS_0_01 = "BOTH_PASSED_E2_B_PLUS_0_01"


_ABLATION_ORDER = (
    ResearchSystemId.B0,
    ResearchSystemId.E1,
    ResearchSystemId.E2_A,
    ResearchSystemId.E2_B,
    ResearchSystemId.E3,
)
_COMPONENT_ORDER = (
    ResearchSystemId.E1,
    ResearchSystemId.E2_A,
    ResearchSystemId.E2_B,
    ResearchSystemId.E3,
)


class DevelopmentPromotionReleaseV1(StrictModel):
    schema_version: Literal["flatband-development-promotion-release-v1"] = (
        "flatband-development-promotion-release-v1"
    )
    release_id: Identifier
    release_sha256: Sha256
    analysis_input_ref: TypedArtifactRefV1
    formal_verifier_attestation: FormalVerifierAttestationRefV1
    metric_rows: Annotated[
        tuple[DevelopmentMetricRowV1, ...], Field(min_length=5, max_length=5)
    ]
    component_gates: Annotated[
        tuple[DevelopmentGateDecisionV1, ...], Field(min_length=4, max_length=4)
    ]
    promoted_components: Annotated[
        tuple[ResearchSystemId, ...], Field(max_length=3)
    ]
    selected_e2_variant: Literal[
        ResearchSystemId.E2_A, ResearchSystemId.E2_B
    ] | None
    e2_selection_reason: E2SelectionReason
    assembled_at: Annotated[str, Field(min_length=20, max_length=40)]
    caller_metric_rows_are_formal_evidence: Literal[False] = False
    upstream_analysis_input_v2_exact_replay_required: Literal[True] = True
    observed_guardrails_not_inference: Literal[True] = True
    scientific_conclusion: Literal[False] = False

    @field_validator("assembled_at")
    @classmethod
    def validate_time(cls, value: str) -> str:
        _timestamp(value)
        return value

    @model_validator(mode="after")
    def validate_release(self) -> DevelopmentPromotionReleaseV1:
        if tuple(item.system_id for item in self.metric_rows) != _ABLATION_ORDER:
            raise ValueError("promotion rows must exactly cover B0/E1/E2-A/E2-B/E3")
        if len({item.row_id for item in self.metric_rows}) != len(self.metric_rows):
            raise ValueError("promotion metric row identities must be unique")
        if any(item.analysis_input_ref != self.analysis_input_ref for item in self.metric_rows):
            raise ValueError("promotion rows bind a foreign AnalysisInputV2")
        if len({item.case_universe_sha256 for item in self.metric_rows}) != 1:
            raise ValueError("promotion rows differ in development case universe")
        _assert_formal_attestation_binding(
            self.formal_verifier_attestation,
            analysis_input_ref=self.analysis_input_ref,
            payload_kind=VerifiedPayloadKind.DEVELOPMENT_ABLATION_METRIC_ROWS,
            payload=self.metric_rows,
        )
        if tuple(item.system_id for item in self.component_gates) != _COMPONENT_ORDER:
            raise ValueError("promotion Gates must cover E1/E2-A/E2-B/E3")
        baseline = self.metric_rows[0]
        expected_gates = tuple(
            _derive_gate(baseline, row) for row in self.metric_rows[1:]
        )
        if self.component_gates != expected_gates:
            raise ValueError("promotion Gate decisions do not replay from metric rows")
        expected_e2, reason = _select_e2(expected_gates, self.metric_rows)
        expected_promoted = tuple(
            item
            for item in (
                ResearchSystemId.E1
                if expected_gates[0].passed
                else None,
                expected_e2,
                ResearchSystemId.E3
                if expected_gates[3].passed
                else None,
            )
            if item is not None
        )
        if (
            self.selected_e2_variant,
            self.e2_selection_reason,
            self.promoted_components,
        ) != (expected_e2, reason, expected_promoted):
            raise ValueError("promotion selection does not replay from preregistered Gates")
        _require_after(
            self.assembled_at,
            self.formal_verifier_attestation.attested_at,
            "promotion assembly",
        )
        _assert_addressed(
            self,
            id_field="release_id",
            sha_field="release_sha256",
            prefix="dev-promotion-v1",
        )
        return self


def _select_e2(
    gates: tuple[DevelopmentGateDecisionV1, ...],
    rows: tuple[DevelopmentMetricRowV1, ...],
) -> tuple[ResearchSystemId | None, E2SelectionReason]:
    e2a_gate, e2b_gate = gates[1], gates[2]
    if not e2a_gate.passed and not e2b_gate.passed:
        return None, E2SelectionReason.NEITHER_PASSED
    if e2a_gate.passed and not e2b_gate.passed:
        return ResearchSystemId.E2_A, E2SelectionReason.ONLY_E2_A_PASSED
    if e2b_gate.passed and not e2a_gate.passed:
        return ResearchSystemId.E2_B, E2SelectionReason.ONLY_E2_B_PASSED
    e2a = rows[2]
    e2b = rows[3]
    if _round(e2b.andcg_at_5 - e2a.andcg_at_5) >= E2_B_MIN_INCREMENT_OVER_E2_A:
        return (
            ResearchSystemId.E2_B,
            E2SelectionReason.BOTH_PASSED_E2_B_PLUS_0_01,
        )
    return (
        ResearchSystemId.E2_A,
        E2SelectionReason.BOTH_PASSED_E2_A_PARSIMONY,
    )


def build_development_promotion_release_v1(
    *,
    analysis_input_ref: TypedArtifactRefV1,
    formal_verifier_attestation: FormalVerifierAttestationRefV1,
    metric_rows: Sequence[DevelopmentMetricRowV1],
    assembled_at: str,
) -> DevelopmentPromotionReleaseV1:
    ref = _revalidate(analysis_input_ref, TypedArtifactRefV1)
    rows_by_system = {
        item.system_id: _revalidate(item, DevelopmentMetricRowV1)
        for item in metric_rows
    }
    if len(rows_by_system) != len(tuple(metric_rows)) or set(rows_by_system) != set(
        _ABLATION_ORDER
    ):
        raise ValueError("promotion requires one row for every ablation system")
    rows = tuple(rows_by_system[item] for item in _ABLATION_ORDER)
    attestation = _revalidate(
        formal_verifier_attestation, FormalVerifierAttestationRefV1
    )
    gates = tuple(_derive_gate(rows[0], item) for item in rows[1:])
    selected_e2, reason = _select_e2(gates, rows)
    promoted = tuple(
        item
        for item in (
            ResearchSystemId.E1 if gates[0].passed else None,
            selected_e2,
            ResearchSystemId.E3 if gates[3].passed else None,
        )
        if item is not None
    )
    return _build_addressed(
        DevelopmentPromotionReleaseV1,
        id_field="release_id",
        sha_field="release_sha256",
        prefix="dev-promotion-v1",
        values={
            "analysis_input_ref": ref,
            "formal_verifier_attestation": attestation,
            "metric_rows": rows,
            "component_gates": gates,
            "promoted_components": promoted,
            "selected_e2_variant": selected_e2,
            "e2_selection_reason": reason,
            "assembled_at": assembled_at,
        },
    )


def assert_development_promotion_release_exact_replay_v1(
    release: DevelopmentPromotionReleaseV1,
) -> None:
    value = _revalidate(release, DevelopmentPromotionReleaseV1)
    expected = build_development_promotion_release_v1(
        analysis_input_ref=value.analysis_input_ref,
        formal_verifier_attestation=value.formal_verifier_attestation,
        metric_rows=value.metric_rows,
        assembled_at=value.assembled_at,
    )
    if value != expected:
        raise ValueError("development promotion release does not exactly replay")


class SystemConfigurationRefV1(StrictModel):
    system_id: ResearchSystemId
    configuration_ref: TypedArtifactRefV1

    @model_validator(mode="after")
    def validate_ref(self) -> SystemConfigurationRefV1:
        _require_ref_type(
            self.configuration_ref,
            LifecycleArtifactType.SYSTEM_CONFIGURATION,
            "system configuration",
        )
        if self.system_id in {ResearchSystemId.E1_LOCAL, ResearchSystemId.FUSION}:
            raise ValueError("component configuration cannot be E1-local or Fusion")
        return self


def _promotion_ref(
    release: DevelopmentPromotionReleaseV1,
) -> TypedArtifactRefV1:
    return _owned_ref(
        release,
        artifact_type=LifecycleArtifactType.DEVELOPMENT_PROMOTION_RELEASE,
        id_field="release_id",
        sha_field="release_sha256",
    )


class FusionConfigurationReleaseV1(StrictModel):
    """The one fixed B0 + all-eligible-component configuration."""

    schema_version: Literal["flatband-fusion-configuration-release-v1"] = (
        "flatband-fusion-configuration-release-v1"
    )
    release_id: Identifier
    release_sha256: Sha256
    promotion_release_ref: TypedArtifactRefV1
    component_configurations: Annotated[
        tuple[SystemConfigurationRefV1, ...], Field(min_length=2, max_length=4)
    ]
    fusion_components: Annotated[
        tuple[ResearchSystemId, ...], Field(min_length=1, max_length=3)
    ]
    selected_e2_variant: Literal[
        ResearchSystemId.E2_A, ResearchSystemId.E2_B
    ] | None
    configuration_lock_sha256: Sha256
    frozen_at: Annotated[str, Field(min_length=20, max_length=40)]
    arbitrary_subset_search_allowed: Literal[False] = False
    development_only_selection: Literal[True] = True
    scientific_conclusion: Literal[False] = False

    @field_validator("frozen_at")
    @classmethod
    def validate_time(cls, value: str) -> str:
        _timestamp(value)
        return value

    @model_validator(mode="after")
    def validate_release(self) -> FusionConfigurationReleaseV1:
        _require_ref_type(
            self.promotion_release_ref,
            LifecycleArtifactType.DEVELOPMENT_PROMOTION_RELEASE,
            "Fusion configuration promotion",
        )
        systems = tuple(item.system_id for item in self.component_configurations)
        if systems != (ResearchSystemId.B0, *self.fusion_components):
            raise ValueError("Fusion configuration must be B0 plus every promoted component")
        if len(systems) != len(set(systems)):
            raise ValueError("Fusion configuration repeats a component")
        e2_values = tuple(
            item
            for item in self.fusion_components
            if item in {ResearchSystemId.E2_A, ResearchSystemId.E2_B}
        )
        expected_e2 = e2_values[0] if len(e2_values) == 1 else None
        if len(e2_values) > 1 or self.selected_e2_variant is not expected_e2:
            raise ValueError("Fusion configuration has an invalid E2 selection")
        if self.configuration_lock_sha256 != canonical_sha256(
            self.component_configurations
        ):
            raise ValueError("Fusion configuration lock does not replay")
        _assert_addressed(
            self,
            id_field="release_id",
            sha_field="release_sha256",
            prefix="fusion-config-v1",
        )
        return self


def build_fusion_configuration_release_v1(
    *,
    promotion_release: DevelopmentPromotionReleaseV1,
    component_configurations: Sequence[SystemConfigurationRefV1],
    frozen_at: str,
) -> FusionConfigurationReleaseV1:
    promotion = _revalidate(
        promotion_release, DevelopmentPromotionReleaseV1
    )
    assert_development_promotion_release_exact_replay_v1(promotion)
    if not promotion.promoted_components:
        raise ValueError("Fusion requires at least one component to pass development")
    by_system = {
        item.system_id: _revalidate(item, SystemConfigurationRefV1)
        for item in component_configurations
    }
    if len(by_system) != len(tuple(component_configurations)):
        raise ValueError("Fusion component configuration IDs must be unique")
    expected_systems = (ResearchSystemId.B0, *promotion.promoted_components)
    if set(by_system) != set(expected_systems):
        raise ValueError("Fusion must contain B0 and every promoted component exactly once")
    configs = tuple(by_system[item] for item in expected_systems)
    _require_after(frozen_at, promotion.assembled_at, "Fusion freeze")
    return _build_addressed(
        FusionConfigurationReleaseV1,
        id_field="release_id",
        sha_field="release_sha256",
        prefix="fusion-config-v1",
        values={
            "promotion_release_ref": _promotion_ref(promotion),
            "component_configurations": configs,
            "fusion_components": promotion.promoted_components,
            "selected_e2_variant": promotion.selected_e2_variant,
            "configuration_lock_sha256": canonical_sha256(configs),
            "frozen_at": frozen_at,
        },
    )


def assert_fusion_configuration_release_exact_replay_v1(
    release: FusionConfigurationReleaseV1,
    *,
    promotion_release: DevelopmentPromotionReleaseV1,
) -> None:
    value = _revalidate(release, FusionConfigurationReleaseV1)
    expected = build_fusion_configuration_release_v1(
        promotion_release=promotion_release,
        component_configurations=value.component_configurations,
        frozen_at=value.frozen_at,
    )
    if value != expected:
        raise ValueError("Fusion configuration does not exactly replay")


def _fusion_configuration_ref(
    release: FusionConfigurationReleaseV1,
) -> TypedArtifactRefV1:
    return _owned_ref(
        release,
        artifact_type=LifecycleArtifactType.FUSION_CONFIGURATION_RELEASE,
        id_field="release_id",
        sha_field="release_sha256",
    )


class DevelopmentFusionGateReleaseV1(StrictModel):
    schema_version: Literal["flatband-development-fusion-gate-release-v1"] = (
        "flatband-development-fusion-gate-release-v1"
    )
    gate_id: Identifier
    gate_sha256: Sha256
    promotion_release_ref: TypedArtifactRefV1
    fusion_configuration_ref: TypedArtifactRefV1
    analysis_input_ref: TypedArtifactRefV1
    formal_verifier_attestation: FormalVerifierAttestationRefV1
    metric_rows: Annotated[
        tuple[DevelopmentMetricRowV1, ...], Field(min_length=2, max_length=2)
    ]
    decision: DevelopmentGateDecisionV1
    passed: bool
    locked_unseal_allowed: bool
    evaluated_at: Annotated[str, Field(min_length=20, max_length=40)]
    caller_metric_rows_are_formal_evidence: Literal[False] = False
    upstream_analysis_input_v2_exact_replay_required: Literal[True] = True
    scientific_conclusion: Literal[False] = False

    @field_validator("evaluated_at")
    @classmethod
    def validate_time(cls, value: str) -> str:
        _timestamp(value)
        return value

    @model_validator(mode="after")
    def validate_gate(self) -> DevelopmentFusionGateReleaseV1:
        _require_ref_type(
            self.promotion_release_ref,
            LifecycleArtifactType.DEVELOPMENT_PROMOTION_RELEASE,
            "Fusion Gate promotion",
        )
        _require_ref_type(
            self.fusion_configuration_ref,
            LifecycleArtifactType.FUSION_CONFIGURATION_RELEASE,
            "Fusion Gate configuration",
        )
        if tuple(item.system_id for item in self.metric_rows) != (
            ResearchSystemId.B0,
            ResearchSystemId.FUSION,
        ):
            raise ValueError("Fusion Gate requires exactly B0 and Fusion rows")
        if any(item.analysis_input_ref != self.analysis_input_ref for item in self.metric_rows):
            raise ValueError("Fusion Gate rows bind a foreign AnalysisInputV2")
        if len({item.case_universe_sha256 for item in self.metric_rows}) != 1:
            raise ValueError("Fusion Gate rows differ in case universe")
        _assert_formal_attestation_binding(
            self.formal_verifier_attestation,
            analysis_input_ref=self.analysis_input_ref,
            payload_kind=VerifiedPayloadKind.DEVELOPMENT_FUSION_METRIC_ROWS,
            payload=self.metric_rows,
        )
        expected = _derive_gate(self.metric_rows[0], self.metric_rows[1])
        if self.decision != expected or (
            self.passed,
            self.locked_unseal_allowed,
        ) != (expected.passed, expected.passed):
            raise ValueError("Fusion Gate result does not replay")
        _require_after(
            self.evaluated_at,
            self.formal_verifier_attestation.attested_at,
            "Fusion Gate evaluation",
        )
        _assert_addressed(
            self,
            id_field="gate_id",
            sha_field="gate_sha256",
            prefix="dev-fusion-gate-v1",
        )
        return self


def build_development_fusion_gate_release_v1(
    *,
    promotion_release: DevelopmentPromotionReleaseV1,
    fusion_configuration: FusionConfigurationReleaseV1,
    analysis_input_ref: TypedArtifactRefV1,
    formal_verifier_attestation: FormalVerifierAttestationRefV1,
    metric_rows: Sequence[DevelopmentMetricRowV1],
    evaluated_at: str,
) -> DevelopmentFusionGateReleaseV1:
    promotion = _revalidate(promotion_release, DevelopmentPromotionReleaseV1)
    config = _revalidate(fusion_configuration, FusionConfigurationReleaseV1)
    assert_fusion_configuration_release_exact_replay_v1(
        config, promotion_release=promotion
    )
    by_system = {
        item.system_id: _revalidate(item, DevelopmentMetricRowV1)
        for item in metric_rows
    }
    if len(by_system) != len(tuple(metric_rows)) or set(by_system) != {
        ResearchSystemId.B0,
        ResearchSystemId.FUSION,
    }:
        raise ValueError("Fusion Gate requires one B0 and one Fusion metric row")
    rows = (by_system[ResearchSystemId.B0], by_system[ResearchSystemId.FUSION])
    decision = _derive_gate(*rows)
    attestation = _revalidate(
        formal_verifier_attestation, FormalVerifierAttestationRefV1
    )
    _require_after(evaluated_at, config.frozen_at, "Fusion Gate evaluation")
    _require_after(evaluated_at, attestation.attested_at, "Fusion Gate evaluation")
    return _build_addressed(
        DevelopmentFusionGateReleaseV1,
        id_field="gate_id",
        sha_field="gate_sha256",
        prefix="dev-fusion-gate-v1",
        values={
            "promotion_release_ref": _promotion_ref(promotion),
            "fusion_configuration_ref": _fusion_configuration_ref(config),
            "analysis_input_ref": _revalidate(
                analysis_input_ref, TypedArtifactRefV1
            ),
            "formal_verifier_attestation": attestation,
            "metric_rows": rows,
            "decision": decision,
            "passed": decision.passed,
            "locked_unseal_allowed": decision.passed,
            "evaluated_at": evaluated_at,
        },
    )


def assert_development_fusion_gate_release_exact_replay_v1(
    release: DevelopmentFusionGateReleaseV1,
    *,
    promotion_release: DevelopmentPromotionReleaseV1,
    fusion_configuration: FusionConfigurationReleaseV1,
) -> None:
    value = _revalidate(release, DevelopmentFusionGateReleaseV1)
    expected = build_development_fusion_gate_release_v1(
        promotion_release=promotion_release,
        fusion_configuration=fusion_configuration,
        analysis_input_ref=value.analysis_input_ref,
        formal_verifier_attestation=value.formal_verifier_attestation,
        metric_rows=value.metric_rows,
        evaluated_at=value.evaluated_at,
    )
    if value != expected:
        raise ValueError("development Fusion Gate does not exactly replay")


def _fusion_gate_ref(
    release: DevelopmentFusionGateReleaseV1,
) -> TypedArtifactRefV1:
    return _owned_ref(
        release,
        artifact_type=LifecycleArtifactType.DEVELOPMENT_FUSION_GATE_RELEASE,
        id_field="gate_id",
        sha_field="gate_sha256",
    )


class LockedLabelSealV1(StrictModel):
    """Private label commitment made before any locked annotation is exposed."""

    schema_version: Literal["flatband-locked-label-seal-v1"] = (
        "flatband-locked-label-seal-v1"
    )
    seal_id: Identifier
    seal_sha256: Sha256
    split_manifest_ref: TypedArtifactRefV1
    private_label_custody_ref: TypedArtifactRefV1
    locked_case_universe_sha256: Sha256
    sealed_label_commitment_sha256: Sha256
    locked_iid_case_count: Literal[30] = LOCKED_IID_CASE_COUNT
    locked_ood_case_count: Literal[30] = LOCKED_OOD_CASE_COUNT
    sealed_at: Annotated[str, Field(min_length=20, max_length=40)]
    labels_visible_to_system_or_model: Literal[False] = False
    private_custody_required: Literal[True] = True
    public_instance_release_allowed: Literal[False] = False
    scientific_conclusion: Literal[False] = False

    @field_validator("sealed_at")
    @classmethod
    def validate_time(cls, value: str) -> str:
        _timestamp(value)
        return value

    @model_validator(mode="after")
    def validate_seal(self) -> LockedLabelSealV1:
        _require_ref_type(
            self.split_manifest_ref,
            LifecycleArtifactType.BENCHMARK_SPLIT_MANIFEST,
            "locked label seal split",
        )
        _require_ref_type(
            self.private_label_custody_ref,
            LifecycleArtifactType.PRIVATE_LOCKED_LABEL_CUSTODY,
            "locked label custody",
        )
        _assert_addressed(
            self,
            id_field="seal_id",
            sha_field="seal_sha256",
            prefix="locked-label-seal-v1",
        )
        return self


def build_locked_label_seal_v1(
    *,
    split_manifest_ref: TypedArtifactRefV1,
    private_label_custody_ref: TypedArtifactRefV1,
    locked_case_universe_sha256: str,
    sealed_label_commitment_sha256: str,
    sealed_at: str,
) -> LockedLabelSealV1:
    return _build_addressed(
        LockedLabelSealV1,
        id_field="seal_id",
        sha_field="seal_sha256",
        prefix="locked-label-seal-v1",
        values={
            "split_manifest_ref": _revalidate(
                split_manifest_ref, TypedArtifactRefV1
            ),
            "private_label_custody_ref": _revalidate(
                private_label_custody_ref, TypedArtifactRefV1
            ),
            "locked_case_universe_sha256": locked_case_universe_sha256,
            "sealed_label_commitment_sha256": sealed_label_commitment_sha256,
            "sealed_at": sealed_at,
        },
    )


def assert_locked_label_seal_exact_replay_v1(seal: LockedLabelSealV1) -> None:
    value = _revalidate(seal, LockedLabelSealV1)
    expected = build_locked_label_seal_v1(
        split_manifest_ref=value.split_manifest_ref,
        private_label_custody_ref=value.private_label_custody_ref,
        locked_case_universe_sha256=value.locked_case_universe_sha256,
        sealed_label_commitment_sha256=value.sealed_label_commitment_sha256,
        sealed_at=value.sealed_at,
    )
    if value != expected:
        raise ValueError("locked label seal does not exactly replay")


def _locked_seal_ref(seal: LockedLabelSealV1) -> TypedArtifactRefV1:
    return _owned_ref(
        seal,
        artifact_type=LifecycleArtifactType.LOCKED_LABEL_SEAL,
        id_field="seal_id",
        sha_field="seal_sha256",
    )


class LockedTestAuthorizationReleaseV1(StrictModel):
    schema_version: Literal["flatband-locked-test-authorization-release-v1"] = (
        "flatband-locked-test-authorization-release-v1"
    )
    authorization_id: Identifier
    authorization_sha256: Sha256
    promotion_release_ref: TypedArtifactRefV1
    fusion_configuration_ref: TypedArtifactRefV1
    fusion_gate_ref: TypedArtifactRefV1
    locked_label_seal_ref: TypedArtifactRefV1
    analysis_code_ref: TypedArtifactRefV1
    analysis_environment_ref: TypedArtifactRefV1
    statistical_analysis_plan_ref: TypedArtifactRefV1
    locked_execution_ref: TypedArtifactRefV1
    configuration_lock_sha256: Sha256
    one_shot_unseal_identity: Identifier
    authorized_at: Annotated[str, Field(min_length=20, max_length=40)]
    locked_labels_seen_before_authorization: Literal[False] = False
    configuration_mutation_after_authorization_allowed: Literal[False] = False
    unseal_count_authorized: Literal[1] = 1
    scientific_conclusion: Literal[False] = False

    @field_validator("authorized_at")
    @classmethod
    def validate_time(cls, value: str) -> str:
        _timestamp(value)
        return value

    @model_validator(mode="after")
    def validate_authorization(self) -> LockedTestAuthorizationReleaseV1:
        for ref, expected, label in (
            (
                self.promotion_release_ref,
                LifecycleArtifactType.DEVELOPMENT_PROMOTION_RELEASE,
                "locked authorization promotion",
            ),
            (
                self.fusion_configuration_ref,
                LifecycleArtifactType.FUSION_CONFIGURATION_RELEASE,
                "locked authorization Fusion configuration",
            ),
            (
                self.fusion_gate_ref,
                LifecycleArtifactType.DEVELOPMENT_FUSION_GATE_RELEASE,
                "locked authorization Fusion Gate",
            ),
            (
                self.locked_label_seal_ref,
                LifecycleArtifactType.LOCKED_LABEL_SEAL,
                "locked authorization label seal",
            ),
            (
                self.analysis_code_ref,
                LifecycleArtifactType.ANALYSIS_CODE_RELEASE,
                "locked authorization analysis code",
            ),
            (
                self.analysis_environment_ref,
                LifecycleArtifactType.ANALYSIS_ENVIRONMENT_RELEASE,
                "locked authorization environment",
            ),
            (
                self.statistical_analysis_plan_ref,
                LifecycleArtifactType.STATISTICAL_ANALYSIS_PLAN,
                "locked authorization statistical plan",
            ),
            (
                self.locked_execution_ref,
                LifecycleArtifactType.LOCKED_EXECUTION_RELEASE,
                "locked authorization execution",
            ),
        ):
            _require_ref_type(ref, expected, label)
        lock_values = (
            self.promotion_release_ref,
            self.fusion_configuration_ref,
            self.fusion_gate_ref,
            self.locked_label_seal_ref,
            self.analysis_code_ref,
            self.analysis_environment_ref,
            self.statistical_analysis_plan_ref,
            self.locked_execution_ref,
        )
        if self.configuration_lock_sha256 != canonical_sha256(lock_values):
            raise ValueError("locked authorization configuration lock does not replay")
        expected_identity = deterministic_id(
            "locked-unseal-v1",
            {
                "locked_label_seal_ref": self.locked_label_seal_ref,
                "configuration_lock_sha256": self.configuration_lock_sha256,
            },
        )
        if self.one_shot_unseal_identity != expected_identity:
            raise ValueError("locked authorization has a drifting unseal identity")
        _assert_addressed(
            self,
            id_field="authorization_id",
            sha_field="authorization_sha256",
            prefix="locked-auth-v1",
        )
        return self


def build_locked_test_authorization_release_v1(
    *,
    promotion_release: DevelopmentPromotionReleaseV1,
    fusion_configuration: FusionConfigurationReleaseV1,
    fusion_gate: DevelopmentFusionGateReleaseV1,
    locked_label_seal: LockedLabelSealV1,
    analysis_code_ref: TypedArtifactRefV1,
    analysis_environment_ref: TypedArtifactRefV1,
    statistical_analysis_plan_ref: TypedArtifactRefV1,
    locked_execution_ref: TypedArtifactRefV1,
    authorized_at: str,
) -> LockedTestAuthorizationReleaseV1:
    promotion = _revalidate(promotion_release, DevelopmentPromotionReleaseV1)
    config = _revalidate(fusion_configuration, FusionConfigurationReleaseV1)
    gate = _revalidate(fusion_gate, DevelopmentFusionGateReleaseV1)
    seal = _revalidate(locked_label_seal, LockedLabelSealV1)
    assert_development_fusion_gate_release_exact_replay_v1(
        gate,
        promotion_release=promotion,
        fusion_configuration=config,
    )
    assert_locked_label_seal_exact_replay_v1(seal)
    if not gate.passed or not gate.locked_unseal_allowed:
        raise ValueError("locked test cannot be authorized before Fusion passes")
    refs = (
        _promotion_ref(promotion),
        _fusion_configuration_ref(config),
        _fusion_gate_ref(gate),
        _locked_seal_ref(seal),
        _revalidate(analysis_code_ref, TypedArtifactRefV1),
        _revalidate(analysis_environment_ref, TypedArtifactRefV1),
        _revalidate(statistical_analysis_plan_ref, TypedArtifactRefV1),
        _revalidate(locked_execution_ref, TypedArtifactRefV1),
    )
    _require_after(authorized_at, config.frozen_at, "locked authorization")
    _require_after(authorized_at, gate.evaluated_at, "locked authorization")
    _require_after(authorized_at, seal.sealed_at, "locked authorization")
    lock_sha = canonical_sha256(refs)
    one_shot = deterministic_id(
        "locked-unseal-v1",
        {
            "locked_label_seal_ref": refs[3],
            "configuration_lock_sha256": lock_sha,
        },
    )
    return _build_addressed(
        LockedTestAuthorizationReleaseV1,
        id_field="authorization_id",
        sha_field="authorization_sha256",
        prefix="locked-auth-v1",
        values={
            "promotion_release_ref": refs[0],
            "fusion_configuration_ref": refs[1],
            "fusion_gate_ref": refs[2],
            "locked_label_seal_ref": refs[3],
            "analysis_code_ref": refs[4],
            "analysis_environment_ref": refs[5],
            "statistical_analysis_plan_ref": refs[6],
            "locked_execution_ref": refs[7],
            "configuration_lock_sha256": lock_sha,
            "one_shot_unseal_identity": one_shot,
            "authorized_at": authorized_at,
        },
    )


def assert_locked_test_authorization_exact_replay_v1(
    release: LockedTestAuthorizationReleaseV1,
    *,
    promotion_release: DevelopmentPromotionReleaseV1,
    fusion_configuration: FusionConfigurationReleaseV1,
    fusion_gate: DevelopmentFusionGateReleaseV1,
    locked_label_seal: LockedLabelSealV1,
) -> None:
    value = _revalidate(release, LockedTestAuthorizationReleaseV1)
    expected = build_locked_test_authorization_release_v1(
        promotion_release=promotion_release,
        fusion_configuration=fusion_configuration,
        fusion_gate=fusion_gate,
        locked_label_seal=locked_label_seal,
        analysis_code_ref=value.analysis_code_ref,
        analysis_environment_ref=value.analysis_environment_ref,
        statistical_analysis_plan_ref=value.statistical_analysis_plan_ref,
        locked_execution_ref=value.locked_execution_ref,
        authorized_at=value.authorized_at,
    )
    if value != expected:
        raise ValueError("locked-test authorization does not exactly replay")


def _locked_authorization_ref(
    release: LockedTestAuthorizationReleaseV1,
) -> TypedArtifactRefV1:
    return _owned_ref(
        release,
        artifact_type=LifecycleArtifactType.LOCKED_TEST_AUTHORIZATION,
        id_field="authorization_id",
        sha_field="authorization_sha256",
    )


class LockedAnnotationUnsealReleaseV1(StrictModel):
    """The only authorized reveal event for one seal/configuration identity."""

    schema_version: Literal["flatband-locked-annotation-unseal-release-v1"] = (
        "flatband-locked-annotation-unseal-release-v1"
    )
    unseal_id: Identifier
    unseal_sha256: Sha256
    authorization_ref: TypedArtifactRefV1
    locked_label_seal_ref: TypedArtifactRefV1
    fusion_configuration_ref: TypedArtifactRefV1
    fusion_gate_ref: TypedArtifactRefV1
    private_label_custody_ref: TypedArtifactRefV1
    sealed_label_commitment_sha256: Sha256
    configuration_lock_sha256: Sha256
    predecessor_ledger_id: Identifier
    predecessor_ledger_sha256: Sha256
    unseal_ordinal: Literal[1] = 1
    unsealed_at: Annotated[str, Field(min_length=20, max_length=40)]
    labels_visible_to_system_or_model: Literal[False] = False
    analysis_only_access: Literal[True] = True
    second_unseal_allowed: Literal[False] = False
    unseal_identity_alone_proves_global_uniqueness: Literal[False] = False
    external_compare_and_swap_attestation: Literal["NOT_PROVIDED"] = (
        "NOT_PROVIDED"
    )
    internal_append_only_ledger_exact_replay_required: Literal[True] = True
    scientific_conclusion: Literal[False] = False

    @field_validator("unsealed_at")
    @classmethod
    def validate_time(cls, value: str) -> str:
        _timestamp(value)
        return value

    @model_validator(mode="after")
    def validate_unseal(self) -> LockedAnnotationUnsealReleaseV1:
        for ref, expected, label in (
            (
                self.authorization_ref,
                LifecycleArtifactType.LOCKED_TEST_AUTHORIZATION,
                "unseal authorization",
            ),
            (
                self.locked_label_seal_ref,
                LifecycleArtifactType.LOCKED_LABEL_SEAL,
                "unseal label seal",
            ),
            (
                self.fusion_configuration_ref,
                LifecycleArtifactType.FUSION_CONFIGURATION_RELEASE,
                "unseal Fusion configuration",
            ),
            (
                self.fusion_gate_ref,
                LifecycleArtifactType.DEVELOPMENT_FUSION_GATE_RELEASE,
                "unseal Fusion Gate",
            ),
            (
                self.private_label_custody_ref,
                LifecycleArtifactType.PRIVATE_LOCKED_LABEL_CUSTODY,
                "unseal private label custody",
            ),
        ):
            _require_ref_type(ref, expected, label)
        expected_identity = deterministic_id(
            "locked-unseal-v1",
            {
                "locked_label_seal_ref": self.locked_label_seal_ref,
                "configuration_lock_sha256": self.configuration_lock_sha256,
            },
        )
        _assert_addressed(
            self,
            id_field="unseal_id",
            sha_field="unseal_sha256",
            prefix="locked-unseal-v1",
            fixed_id=expected_identity,
        )
        return self


class LockedUnsealLedgerReleaseV1(StrictModel):
    """Private append-only internal ledger for the single unseal event.

    The ledger closes replay inside one campaign artifact.  Its explicit
    ``NOT_PROVIDED`` CAS status prevents an internal content-addressed chain
    from being misrepresented as an externally serialized transaction.
    """

    schema_version: Literal["flatband-locked-unseal-ledger-release-v1"] = (
        "flatband-locked-unseal-ledger-release-v1"
    )
    ledger_id: Identifier
    ledger_sha256: Sha256
    authorization_ref: TypedArtifactRefV1
    locked_label_seal_ref: TypedArtifactRefV1
    revision: Annotated[int, Field(ge=0, le=1)]
    predecessor_ledger_sha256: Sha256 | None = None
    unseal_event: LockedAnnotationUnsealReleaseV1 | None = None
    initialized_at: Annotated[str, Field(min_length=20, max_length=40)]
    updated_at: Annotated[str, Field(min_length=20, max_length=40)]
    append_only: Literal[True] = True
    maximum_unseal_events: Literal[1] = 1
    external_compare_and_swap_attestation: Literal["NOT_PROVIDED"] = (
        "NOT_PROVIDED"
    )
    global_uniqueness_claimed: Literal[False] = False
    private_storage_required: Literal[True] = True
    scientific_conclusion: Literal[False] = False

    @field_validator("initialized_at", "updated_at")
    @classmethod
    def validate_time(cls, value: str) -> str:
        _timestamp(value)
        return value

    @model_validator(mode="after")
    def validate_ledger(self) -> LockedUnsealLedgerReleaseV1:
        _require_ref_type(
            self.authorization_ref,
            LifecycleArtifactType.LOCKED_TEST_AUTHORIZATION,
            "unseal ledger authorization",
        )
        _require_ref_type(
            self.locked_label_seal_ref,
            LifecycleArtifactType.LOCKED_LABEL_SEAL,
            "unseal ledger label seal",
        )
        if self.revision == 0:
            if self.predecessor_ledger_sha256 is not None or self.unseal_event is not None:
                raise ValueError("unseal ledger genesis cannot contain an event")
            if self.updated_at != self.initialized_at:
                raise ValueError("unseal ledger genesis timestamps must be identical")
        else:
            if self.predecessor_ledger_sha256 is None or self.unseal_event is None:
                raise ValueError("unseal ledger revision one requires predecessor and event")
            event = _revalidate(
                self.unseal_event, LockedAnnotationUnsealReleaseV1
            )
            if (
                event.authorization_ref,
                event.locked_label_seal_ref,
                event.predecessor_ledger_sha256,
                event.unsealed_at,
            ) != (
                self.authorization_ref,
                self.locked_label_seal_ref,
                self.predecessor_ledger_sha256,
                self.updated_at,
            ):
                raise ValueError("unseal ledger event drifts from its predecessor chain")
            if _timestamp(self.updated_at) <= _timestamp(self.initialized_at):
                raise ValueError("unseal ledger event must follow initialization")
        fixed_id = deterministic_id(
            "locked-unseal-ledger-v1",
            {
                "authorization_ref": self.authorization_ref,
                "locked_label_seal_ref": self.locked_label_seal_ref,
            },
        )
        _assert_addressed(
            self,
            id_field="ledger_id",
            sha_field="ledger_sha256",
            prefix="locked-unseal-ledger-v1",
            fixed_id=fixed_id,
        )
        return self


def build_locked_unseal_ledger_genesis_v1(
    *,
    authorization: LockedTestAuthorizationReleaseV1,
    locked_label_seal: LockedLabelSealV1,
) -> LockedUnsealLedgerReleaseV1:
    auth = _revalidate(authorization, LockedTestAuthorizationReleaseV1)
    seal = _revalidate(locked_label_seal, LockedLabelSealV1)
    if auth.locked_label_seal_ref != _locked_seal_ref(seal):
        raise ValueError("unseal ledger genesis binds a foreign label seal")
    fixed_id = deterministic_id(
        "locked-unseal-ledger-v1",
        {
            "authorization_ref": _locked_authorization_ref(auth),
            "locked_label_seal_ref": _locked_seal_ref(seal),
        },
    )
    return _build_addressed(
        LockedUnsealLedgerReleaseV1,
        id_field="ledger_id",
        sha_field="ledger_sha256",
        prefix="locked-unseal-ledger-v1",
        fixed_id=fixed_id,
        values={
            "authorization_ref": _locked_authorization_ref(auth),
            "locked_label_seal_ref": _locked_seal_ref(seal),
            "revision": 0,
            "initialized_at": auth.authorized_at,
            "updated_at": auth.authorized_at,
        },
    )


def build_locked_annotation_unseal_release_v1(
    *,
    authorization: LockedTestAuthorizationReleaseV1,
    promotion_release: DevelopmentPromotionReleaseV1,
    fusion_configuration: FusionConfigurationReleaseV1,
    fusion_gate: DevelopmentFusionGateReleaseV1,
    locked_label_seal: LockedLabelSealV1,
    prior_ledger: LockedUnsealLedgerReleaseV1,
    unsealed_at: str,
) -> LockedAnnotationUnsealReleaseV1:
    auth = _revalidate(authorization, LockedTestAuthorizationReleaseV1)
    promotion = _revalidate(promotion_release, DevelopmentPromotionReleaseV1)
    config = _revalidate(fusion_configuration, FusionConfigurationReleaseV1)
    gate = _revalidate(fusion_gate, DevelopmentFusionGateReleaseV1)
    seal = _revalidate(locked_label_seal, LockedLabelSealV1)
    ledger = _revalidate(prior_ledger, LockedUnsealLedgerReleaseV1)
    assert_locked_test_authorization_exact_replay_v1(
        auth,
        promotion_release=promotion,
        fusion_configuration=config,
        fusion_gate=gate,
        locked_label_seal=seal,
    )
    expected_genesis = build_locked_unseal_ledger_genesis_v1(
        authorization=auth,
        locked_label_seal=seal,
    )
    if ledger != expected_genesis or ledger.revision != 0:
        raise ValueError("one-shot unseal requires the exact empty predecessor ledger")
    _require_after(unsealed_at, auth.authorized_at, "locked annotation unseal")
    values: dict[str, object] = {
        "authorization_ref": _locked_authorization_ref(auth),
        "locked_label_seal_ref": _locked_seal_ref(seal),
        "fusion_configuration_ref": _fusion_configuration_ref(config),
        "fusion_gate_ref": _fusion_gate_ref(gate),
        "private_label_custody_ref": seal.private_label_custody_ref,
        "sealed_label_commitment_sha256": seal.sealed_label_commitment_sha256,
        "configuration_lock_sha256": auth.configuration_lock_sha256,
        "predecessor_ledger_id": ledger.ledger_id,
        "predecessor_ledger_sha256": ledger.ledger_sha256,
        "unsealed_at": unsealed_at,
    }
    return _build_addressed(
        LockedAnnotationUnsealReleaseV1,
        id_field="unseal_id",
        sha_field="unseal_sha256",
        prefix="locked-unseal-v1",
        fixed_id=auth.one_shot_unseal_identity,
        values=values,
    )


def append_locked_unseal_ledger_v1(
    *,
    prior_ledger: LockedUnsealLedgerReleaseV1,
    unseal: LockedAnnotationUnsealReleaseV1,
) -> LockedUnsealLedgerReleaseV1:
    prior = _revalidate(prior_ledger, LockedUnsealLedgerReleaseV1)
    event = _revalidate(unseal, LockedAnnotationUnsealReleaseV1)
    if prior.revision != 0 or prior.unseal_event is not None:
        raise ValueError("one-shot unseal ledger cannot append a second event")
    if (
        event.authorization_ref,
        event.locked_label_seal_ref,
        event.predecessor_ledger_id,
        event.predecessor_ledger_sha256,
    ) != (
        prior.authorization_ref,
        prior.locked_label_seal_ref,
        prior.ledger_id,
        prior.ledger_sha256,
    ):
        raise ValueError("unseal event does not descend from the supplied ledger")
    return _build_addressed(
        LockedUnsealLedgerReleaseV1,
        id_field="ledger_id",
        sha_field="ledger_sha256",
        prefix="locked-unseal-ledger-v1",
        fixed_id=prior.ledger_id,
        values={
            "authorization_ref": prior.authorization_ref,
            "locked_label_seal_ref": prior.locked_label_seal_ref,
            "revision": 1,
            "predecessor_ledger_sha256": prior.ledger_sha256,
            "unseal_event": event,
            "initialized_at": prior.initialized_at,
            "updated_at": event.unsealed_at,
        },
    )


def assert_locked_unseal_ledger_exact_replay_v1(
    release: LockedUnsealLedgerReleaseV1,
    *,
    authorization: LockedTestAuthorizationReleaseV1,
    locked_label_seal: LockedLabelSealV1,
    prior_ledger: LockedUnsealLedgerReleaseV1 | None = None,
) -> None:
    value = _revalidate(release, LockedUnsealLedgerReleaseV1)
    if value.revision == 0:
        expected = build_locked_unseal_ledger_genesis_v1(
            authorization=authorization,
            locked_label_seal=locked_label_seal,
        )
    else:
        if prior_ledger is None or value.unseal_event is None:
            raise ValueError("revision-one ledger replay requires its genesis")
        expected = append_locked_unseal_ledger_v1(
            prior_ledger=prior_ledger,
            unseal=value.unseal_event,
        )
    if value != expected:
        raise ValueError("locked unseal ledger does not exactly replay")


def assert_locked_annotation_unseal_exact_replay_v1(
    release: LockedAnnotationUnsealReleaseV1,
    *,
    authorization: LockedTestAuthorizationReleaseV1,
    promotion_release: DevelopmentPromotionReleaseV1,
    fusion_configuration: FusionConfigurationReleaseV1,
    fusion_gate: DevelopmentFusionGateReleaseV1,
    locked_label_seal: LockedLabelSealV1,
    prior_ledger: LockedUnsealLedgerReleaseV1,
    committed_ledger: LockedUnsealLedgerReleaseV1,
) -> None:
    value = _revalidate(release, LockedAnnotationUnsealReleaseV1)
    prior = _revalidate(prior_ledger, LockedUnsealLedgerReleaseV1)
    committed = _revalidate(committed_ledger, LockedUnsealLedgerReleaseV1)
    expected = build_locked_annotation_unseal_release_v1(
        authorization=authorization,
        promotion_release=promotion_release,
        fusion_configuration=fusion_configuration,
        fusion_gate=fusion_gate,
        locked_label_seal=locked_label_seal,
        prior_ledger=prior,
        unsealed_at=value.unsealed_at,
    )
    if value != expected:
        raise ValueError("locked annotation unseal does not exactly replay")
    expected_ledger = append_locked_unseal_ledger_v1(
        prior_ledger=prior,
        unseal=value,
    )
    if committed != expected_ledger:
        raise ValueError("committed one-shot ledger does not contain this exact event")


def _unseal_ref(release: LockedAnnotationUnsealReleaseV1) -> TypedArtifactRefV1:
    return _owned_ref(
        release,
        artifact_type=LifecycleArtifactType.LOCKED_ANNOTATION_UNSEAL,
        id_field="unseal_id",
        sha_field="unseal_sha256",
    )


def locked_unseal_ledger_ref_v1(
    release: LockedUnsealLedgerReleaseV1,
) -> TypedArtifactRefV1:
    value = _revalidate(release, LockedUnsealLedgerReleaseV1)
    return _owned_ref(
        value,
        artifact_type=LifecycleArtifactType.LOCKED_UNSEAL_LEDGER,
        id_field="ledger_id",
        sha_field="ledger_sha256",
    )


class ProtocolDeviationSeverity(StrEnum):
    MINOR = "MINOR"
    MAJOR = "MAJOR"
    CRITICAL = "CRITICAL"


class ConfirmatoryValidity(StrEnum):
    VALID_NO_SCORE_EFFECT = "VALID_NO_SCORE_EFFECT"
    INVALIDATED_SCORE_OR_CANDIDATE_CHANGE = "INVALIDATED_SCORE_OR_CANDIDATE_CHANGE"


class ProtocolDeviationReleaseV1(StrictModel):
    schema_version: Literal["flatband-protocol-deviation-release-v1"] = (
        "flatband-protocol-deviation-release-v1"
    )
    release_id: Identifier
    release_sha256: Sha256
    unseal_ref: TypedArtifactRefV1 | None = None
    deviation_code: Identifier
    severity: ProtocolDeviationSeverity
    description: Annotated[str, Field(min_length=1, max_length=2_000)]
    changes_candidates_or_scores: bool
    discovered_after_locked_unseal: bool
    confirmatory_validity: ConfirmatoryValidity
    remediation: Annotated[str, Field(min_length=1, max_length=2_000)]
    disclosed_at: Annotated[str, Field(min_length=20, max_length=40)]
    public_disclosure_required: Literal[True] = True
    deviation_never_silently_repaired: Literal[True] = True
    scientific_conclusion: Literal[False] = False

    @field_validator("disclosed_at")
    @classmethod
    def validate_time(cls, value: str) -> str:
        _timestamp(value)
        return value

    @model_validator(mode="after")
    def validate_deviation(self) -> ProtocolDeviationReleaseV1:
        if self.unseal_ref is not None:
            _require_ref_type(
                self.unseal_ref,
                LifecycleArtifactType.LOCKED_ANNOTATION_UNSEAL,
                "protocol deviation unseal",
            )
        if self.discovered_after_locked_unseal != (self.unseal_ref is not None):
            raise ValueError("deviation timing differs from its unseal reference")
        expected = (
            ConfirmatoryValidity.INVALIDATED_SCORE_OR_CANDIDATE_CHANGE
            if self.discovered_after_locked_unseal
            and self.changes_candidates_or_scores
            else ConfirmatoryValidity.VALID_NO_SCORE_EFFECT
        )
        if self.confirmatory_validity is not expected:
            raise ValueError("protocol deviation validity does not replay")
        _assert_addressed(
            self,
            id_field="release_id",
            sha_field="release_sha256",
            prefix="protocol-deviation-v1",
        )
        return self


def build_protocol_deviation_release_v1(
    *,
    deviation_code: str,
    severity: ProtocolDeviationSeverity,
    description: str,
    changes_candidates_or_scores: bool,
    remediation: str,
    disclosed_at: str,
    unseal: LockedAnnotationUnsealReleaseV1 | None = None,
) -> ProtocolDeviationReleaseV1:
    unseal_ref = None
    if unseal is not None:
        value = _revalidate(unseal, LockedAnnotationUnsealReleaseV1)
        if _timestamp(disclosed_at) <= _timestamp(value.unsealed_at):
            raise ValueError("post-unseal deviation must be disclosed after unseal")
        unseal_ref = _unseal_ref(value)
    invalid = unseal_ref is not None and changes_candidates_or_scores
    return _build_addressed(
        ProtocolDeviationReleaseV1,
        id_field="release_id",
        sha_field="release_sha256",
        prefix="protocol-deviation-v1",
        values={
            "unseal_ref": unseal_ref,
            "deviation_code": deviation_code,
            "severity": severity,
            "description": description,
            "changes_candidates_or_scores": changes_candidates_or_scores,
            "discovered_after_locked_unseal": unseal_ref is not None,
            "confirmatory_validity": (
                ConfirmatoryValidity.INVALIDATED_SCORE_OR_CANDIDATE_CHANGE
                if invalid
                else ConfirmatoryValidity.VALID_NO_SCORE_EFFECT
            ),
            "remediation": remediation,
            "disclosed_at": disclosed_at,
        },
    )


def assert_protocol_deviation_exact_replay_v1(
    release: ProtocolDeviationReleaseV1,
    *,
    unseal: LockedAnnotationUnsealReleaseV1 | None = None,
) -> None:
    value = _revalidate(release, ProtocolDeviationReleaseV1)
    expected = build_protocol_deviation_release_v1(
        deviation_code=value.deviation_code,
        severity=value.severity,
        description=value.description,
        changes_candidates_or_scores=value.changes_candidates_or_scores,
        remediation=value.remediation,
        disclosed_at=value.disclosed_at,
        unseal=unseal,
    )
    if value != expected:
        raise ValueError("protocol deviation release does not exactly replay")


def _deviation_ref(release: ProtocolDeviationReleaseV1) -> TypedArtifactRefV1:
    return _owned_ref(
        release,
        artifact_type=LifecycleArtifactType.PROTOCOL_DEVIATION_RELEASE,
        id_field="release_id",
        sha_field="release_sha256",
    )


class LockedPrimaryResultV1(StrictModel):
    equal_iid_ood_andcg_delta: Delta
    bootstrap_lower: Delta
    bootstrap_upper: Delta
    one_sided_randomization_p: PValue
    success_delta: Delta
    evidence_valid_delta: Delta
    duplicate_rate_delta: Delta
    invalid_case_rate: Score
    unresolvable_unit_rate: Score
    observed_effect_guardrail_passed: bool
    bootstrap_guardrail_passed: bool
    randomization_guardrail_passed: bool
    safety_guardrails_passed: bool
    data_quality_guardrails_passed: bool
    metric_gate_passed: bool
    primary_has_no_holm_adjustment: Literal[True] = True

    @model_validator(mode="after")
    def validate_result(self) -> LockedPrimaryResultV1:
        checks = (
            self.equal_iid_ood_andcg_delta >= LOCKED_PRIMARY_ANDCG_MIN_DELTA,
            self.bootstrap_lower > 0.0
            and self.bootstrap_lower <= self.bootstrap_upper,
            self.one_sided_randomization_p < 0.05,
            self.success_delta >= LOCKED_SAFETY_MIN_DELTA
            and self.evidence_valid_delta >= LOCKED_SAFETY_MIN_DELTA
            and self.duplicate_rate_delta <= LOCKED_DUPLICATE_MAX_DELTA,
            self.invalid_case_rate <= LOCKED_INVALID_CASE_MAX_RATE
            and self.unresolvable_unit_rate <= LOCKED_UNRESOLVABLE_MAX_RATE,
        )
        if (
            self.observed_effect_guardrail_passed,
            self.bootstrap_guardrail_passed,
            self.randomization_guardrail_passed,
            self.safety_guardrails_passed,
            self.data_quality_guardrails_passed,
            self.metric_gate_passed,
        ) != (*checks, all(checks)):
            raise ValueError("locked primary result does not replay")
        return self


def build_locked_primary_result_v1(
    *,
    equal_iid_ood_andcg_delta: float,
    bootstrap_lower: float,
    bootstrap_upper: float,
    one_sided_randomization_p: float,
    success_delta: float,
    evidence_valid_delta: float,
    duplicate_rate_delta: float,
    invalid_case_rate: float,
    unresolvable_unit_rate: float,
) -> LockedPrimaryResultV1:
    values = tuple(
        _round(value)
        for value in (
            equal_iid_ood_andcg_delta,
            bootstrap_lower,
            bootstrap_upper,
            one_sided_randomization_p,
            success_delta,
            evidence_valid_delta,
            duplicate_rate_delta,
            invalid_case_rate,
            unresolvable_unit_rate,
        )
    )
    checks = (
        values[0] >= LOCKED_PRIMARY_ANDCG_MIN_DELTA,
        values[1] > 0.0 and values[1] <= values[2],
        values[3] < 0.05,
        values[4] >= LOCKED_SAFETY_MIN_DELTA
        and values[5] >= LOCKED_SAFETY_MIN_DELTA
        and values[6] <= LOCKED_DUPLICATE_MAX_DELTA,
        values[7] <= LOCKED_INVALID_CASE_MAX_RATE
        and values[8] <= LOCKED_UNRESOLVABLE_MAX_RATE,
    )
    return LockedPrimaryResultV1(
        equal_iid_ood_andcg_delta=values[0],
        bootstrap_lower=values[1],
        bootstrap_upper=values[2],
        one_sided_randomization_p=values[3],
        success_delta=values[4],
        evidence_valid_delta=values[5],
        duplicate_rate_delta=values[6],
        invalid_case_rate=values[7],
        unresolvable_unit_rate=values[8],
        observed_effect_guardrail_passed=checks[0],
        bootstrap_guardrail_passed=checks[1],
        randomization_guardrail_passed=checks[2],
        safety_guardrails_passed=checks[3],
        data_quality_guardrails_passed=checks[4],
        metric_gate_passed=all(checks),
    )


class LockedSecondaryHypothesis(StrEnum):
    FUSION_VS_B0_IID = "FUSION_VS_B0_IID"
    FUSION_VS_B0_OOD = "FUSION_VS_B0_OOD"
    FUSION_VS_FUSION_MINUS_E1 = "FUSION_VS_FUSION_MINUS_E1"
    FUSION_VS_FUSION_MINUS_E2 = "FUSION_VS_FUSION_MINUS_E2"
    FUSION_VS_FUSION_MINUS_E3 = "FUSION_VS_FUSION_MINUS_E3"


LOCKED_SECONDARY_FAMILY = tuple(LockedSecondaryHypothesis)


class SecondaryApplicability(StrEnum):
    APPLICABLE = "APPLICABLE"
    NOT_APPLICABLE = "NOT_APPLICABLE"


class LockedSecondaryResultV1(StrictModel):
    hypothesis: LockedSecondaryHypothesis
    applicability: SecondaryApplicability
    andcg_delta: Delta | None
    raw_one_sided_p: PValue
    holm_adjusted_p: PValue
    component_promoted: bool | None
    not_applicable_uses_conservative_p_one: bool

    @model_validator(mode="after")
    def validate_result(self) -> LockedSecondaryResultV1:
        is_leave_one = self.hypothesis in {
            LockedSecondaryHypothesis.FUSION_VS_FUSION_MINUS_E1,
            LockedSecondaryHypothesis.FUSION_VS_FUSION_MINUS_E2,
            LockedSecondaryHypothesis.FUSION_VS_FUSION_MINUS_E3,
        }
        if not is_leave_one:
            if (
                self.applicability is not SecondaryApplicability.APPLICABLE
                or self.andcg_delta is None
                or self.component_promoted is not None
                or self.not_applicable_uses_conservative_p_one
            ):
                raise ValueError("IID/OOD secondary hypotheses are always applicable")
        elif self.component_promoted is None:
            raise ValueError("leave-one secondary requires component promotion status")
        elif not self.component_promoted:
            if (
                self.applicability is not SecondaryApplicability.NOT_APPLICABLE
                or self.andcg_delta is not None
                or self.raw_one_sided_p != 1.0
                or self.holm_adjusted_p != 1.0
                or not self.not_applicable_uses_conservative_p_one
            ):
                raise ValueError("unpromoted component must be NOT_APPLICABLE with p=1")
        elif (
            self.applicability is not SecondaryApplicability.APPLICABLE
            or self.andcg_delta is None
            or self.not_applicable_uses_conservative_p_one
        ):
            raise ValueError("promoted leave-one secondary must remain applicable")
        if self.holm_adjusted_p < self.raw_one_sided_p:
            raise ValueError("Holm-adjusted p cannot be below its raw p")
        return self


def _component_promoted_for_hypothesis(
    hypothesis: LockedSecondaryHypothesis,
    promoted_components: tuple[ResearchSystemId, ...],
) -> bool | None:
    if hypothesis is LockedSecondaryHypothesis.FUSION_VS_FUSION_MINUS_E1:
        return ResearchSystemId.E1 in promoted_components
    if hypothesis is LockedSecondaryHypothesis.FUSION_VS_FUSION_MINUS_E2:
        return any(
            item in promoted_components
            for item in (ResearchSystemId.E2_A, ResearchSystemId.E2_B)
        )
    if hypothesis is LockedSecondaryHypothesis.FUSION_VS_FUSION_MINUS_E3:
        return ResearchSystemId.E3 in promoted_components
    return None


def _holm_adjusted(raw_p: Mapping[LockedSecondaryHypothesis, float]) -> dict[
    LockedSecondaryHypothesis, float
]:
    ordered = sorted(raw_p, key=lambda item: (raw_p[item], item.value))
    adjusted: dict[LockedSecondaryHypothesis, float] = {}
    running = 0.0
    family_size = len(LOCKED_SECONDARY_FAMILY)
    for rank, hypothesis in enumerate(ordered, start=1):
        candidate = min(1.0, (family_size - rank + 1) * raw_p[hypothesis])
        running = max(running, candidate)
        adjusted[hypothesis] = _round(running)
    return adjusted


def build_locked_secondary_family_v1(
    *,
    promotion_release: DevelopmentPromotionReleaseV1,
    observations: Mapping[
        LockedSecondaryHypothesis, tuple[float, float] | None
    ],
) -> tuple[LockedSecondaryResultV1, ...]:
    promotion = _revalidate(promotion_release, DevelopmentPromotionReleaseV1)
    if set(observations) != set(LOCKED_SECONDARY_FAMILY):
        raise ValueError("locked secondary observations must retain the fixed five-family")
    raw_p: dict[LockedSecondaryHypothesis, float] = {}
    effects: dict[LockedSecondaryHypothesis, float | None] = {}
    applicability: dict[LockedSecondaryHypothesis, bool | None] = {}
    for hypothesis in LOCKED_SECONDARY_FAMILY:
        promoted = _component_promoted_for_hypothesis(
            hypothesis, promotion.promoted_components
        )
        applicability[hypothesis] = promoted
        observation = observations[hypothesis]
        if promoted is False:
            if observation is not None:
                raise ValueError("unpromoted leave-one component cannot supply a test result")
            effects[hypothesis] = None
            raw_p[hypothesis] = 1.0
        else:
            if observation is None:
                raise ValueError("applicable secondary hypothesis requires effect and p")
            effect, p_value = observation
            effects[hypothesis] = _round(effect)
            raw_p[hypothesis] = _round(p_value)
    adjusted = _holm_adjusted(raw_p)
    return tuple(
        LockedSecondaryResultV1(
            hypothesis=hypothesis,
            applicability=(
                SecondaryApplicability.NOT_APPLICABLE
                if applicability[hypothesis] is False
                else SecondaryApplicability.APPLICABLE
            ),
            andcg_delta=effects[hypothesis],
            raw_one_sided_p=raw_p[hypothesis],
            holm_adjusted_p=adjusted[hypothesis],
            component_promoted=applicability[hypothesis],
            not_applicable_uses_conservative_p_one=(
                applicability[hypothesis] is False
            ),
        )
        for hypothesis in LOCKED_SECONDARY_FAMILY
    )


class ClaimKind(StrEnum):
    BENCHMARK_PRIMARY_PASSED = "BENCHMARK_PRIMARY_PASSED"
    BENCHMARK_PRIMARY_NOT_PASSED = "BENCHMARK_PRIMARY_NOT_PASSED"
    DEVELOPMENT_COMPONENT_SELECTION = "DEVELOPMENT_COMPONENT_SELECTION"
    EXPERT_AGREEMENT_RESULT = "EXPERT_AGREEMENT_RESULT"
    PROTOCOL_DEVIATION_DISCLOSURE = "PROTOCOL_DEVIATION_DISCLOSURE"


_CLAIM_STATEMENT_BY_KIND: dict[ClaimKind, str] = {
    ClaimKind.BENCHMARK_PRIMARY_PASSED: (
        "The preregistered benchmark primary criterion passed."
    ),
    ClaimKind.BENCHMARK_PRIMARY_NOT_PASSED: (
        "The preregistered benchmark primary criterion did not pass."
    ),
    ClaimKind.DEVELOPMENT_COMPONENT_SELECTION: (
        "Development data selected the frozen Fusion components."
    ),
    ClaimKind.EXPERT_AGREEMENT_RESULT: (
        "The preregistered expert-agreement Gate result is reported."
    ),
    ClaimKind.PROTOCOL_DEVIATION_DISCLOSURE: (
        "Protocol deviations affecting benchmark interpretation are disclosed."
    ),
}


_FORBIDDEN_CLAIM_PATTERNS = tuple(
    re.compile(pattern, flags=re.IGNORECASE)
    for pattern in (
        r"\bnovel(?:ty)?\b",
        r"\bnew material(?:s)?\b",
        r"\breal material(?:s)?\b",
        r"\bactual material(?:s)?\b",
        r"\bpreviously unknown\b",
        r"\bfirst discover(?:y|ed)\b",
        r"\bexperimentally (?:verified|validated|confirmed|proven)\b",
        r"\bdft[- ]?(?:verified|validated|confirmed|proven)\b",
        r"新材料|新颖性|真实材料|实际材料|首次发现|DFT[ 已]?(?:证实|验证|证明)",
    )
)


def _assert_claim_allowed(statement: str) -> None:
    if any(pattern.search(statement) for pattern in _FORBIDDEN_CLAIM_PATTERNS):
        raise ValueError(
            "claim exceeds allowlist: novelty, real-material, and DFT-proof claims are forbidden"
        )


class SupportedClaimV1(StrictModel):
    claim_kind: ClaimKind
    statement: Annotated[str, Field(min_length=1, max_length=1_000)]
    benchmark_scope_only: Literal[True] = True

    @field_validator("statement")
    @classmethod
    def validate_statement(cls, value: str) -> str:
        _assert_claim_allowed(value)
        return value

    @model_validator(mode="after")
    def validate_frozen_statement(self) -> SupportedClaimV1:
        if self.statement != _CLAIM_STATEMENT_BY_KIND[self.claim_kind]:
            raise ValueError("claim statement must use its frozen public template")
        return self


def supported_claim_v1(claim_kind: ClaimKind) -> SupportedClaimV1:
    """Build one public-safe claim from a frozen code/template pair."""

    return SupportedClaimV1(
        claim_kind=claim_kind,
        statement=_CLAIM_STATEMENT_BY_KIND[claim_kind],
    )


class ClaimSupportReleaseV1(StrictModel):
    schema_version: Literal["flatband-claim-support-release-v1"] = (
        "flatband-claim-support-release-v1"
    )
    release_id: Identifier
    release_sha256: Sha256
    promotion_release_ref: TypedArtifactRefV1
    fusion_configuration_ref: TypedArtifactRefV1
    authorization_ref: TypedArtifactRefV1
    unseal_ref: TypedArtifactRefV1
    analysis_input_ref: TypedArtifactRefV1
    formal_verifier_attestation: FormalVerifierAttestationRefV1
    primary_result: LockedPrimaryResultV1
    secondary_results: Annotated[
        tuple[LockedSecondaryResultV1, ...], Field(min_length=5, max_length=5)
    ]
    protocol_deviation_refs: Annotated[
        tuple[TypedArtifactRefV1, ...], Field(max_length=128)
    ] = ()
    claims: Annotated[tuple[SupportedClaimV1, ...], Field(min_length=1, max_length=16)]
    confirmatory_run_valid: bool
    primary_claim_allowed: bool
    assembled_at: Annotated[str, Field(min_length=20, max_length=40)]
    scientific_review_required: Literal[True] = True
    public_release_allowed_before_review: Literal[False] = False
    caller_results_are_formal_evidence: Literal[False] = False
    upstream_analysis_input_v2_exact_replay_required: Literal[True] = True
    scientific_conclusion: Literal[False] = False

    @field_validator("assembled_at")
    @classmethod
    def validate_time(cls, value: str) -> str:
        _timestamp(value)
        return value

    @model_validator(mode="after")
    def validate_release(self) -> ClaimSupportReleaseV1:
        for ref, expected, label in (
            (
                self.promotion_release_ref,
                LifecycleArtifactType.DEVELOPMENT_PROMOTION_RELEASE,
                "claim promotion",
            ),
            (
                self.fusion_configuration_ref,
                LifecycleArtifactType.FUSION_CONFIGURATION_RELEASE,
                "claim Fusion configuration",
            ),
            (
                self.authorization_ref,
                LifecycleArtifactType.LOCKED_TEST_AUTHORIZATION,
                "claim locked authorization",
            ),
            (
                self.unseal_ref,
                LifecycleArtifactType.LOCKED_ANNOTATION_UNSEAL,
                "claim unseal",
            ),
        ):
            _require_ref_type(ref, expected, label)
        if tuple(item.hypothesis for item in self.secondary_results) != (
            LOCKED_SECONDARY_FAMILY
        ):
            raise ValueError("claim support must retain the fixed five secondary family")
        payload = (self.primary_result, self.secondary_results)
        _assert_formal_attestation_binding(
            self.formal_verifier_attestation,
            analysis_input_ref=self.analysis_input_ref,
            payload_kind=VerifiedPayloadKind.LOCKED_RESULT_FAMILY,
            payload=payload,
        )
        refs = self.protocol_deviation_refs
        if tuple((item.artifact_id, item.artifact_sha256) for item in refs) != tuple(
            sorted({(item.artifact_id, item.artifact_sha256) for item in refs})
        ):
            raise ValueError("protocol-deviation refs must be sorted and unique")
        if any(
            item.artifact_type is not LifecycleArtifactType.PROTOCOL_DEVIATION_RELEASE
            for item in refs
        ):
            raise ValueError("claim support has a non-deviation reference")
        claim_kinds = tuple(item.claim_kind for item in self.claims)
        if len(claim_kinds) != len(set(claim_kinds)):
            raise ValueError("claim support repeats a claim kind")
        expected_primary_kind = (
            ClaimKind.BENCHMARK_PRIMARY_PASSED
            if self.primary_claim_allowed
            else ClaimKind.BENCHMARK_PRIMARY_NOT_PASSED
        )
        if expected_primary_kind not in claim_kinds or (
            ClaimKind.BENCHMARK_PRIMARY_PASSED in claim_kinds
            and not self.primary_claim_allowed
        ):
            raise ValueError("claim text status differs from confirmatory evidence")
        _assert_addressed(
            self,
            id_field="release_id",
            sha_field="release_sha256",
            prefix="claim-support-v1",
        )
        return self


def build_claim_support_release_v1(
    *,
    promotion_release: DevelopmentPromotionReleaseV1,
    fusion_configuration: FusionConfigurationReleaseV1,
    authorization: LockedTestAuthorizationReleaseV1,
    unseal: LockedAnnotationUnsealReleaseV1,
    analysis_input_ref: TypedArtifactRefV1,
    formal_verifier_attestation: FormalVerifierAttestationRefV1,
    primary_result: LockedPrimaryResultV1,
    secondary_results: Sequence[LockedSecondaryResultV1],
    protocol_deviations: Sequence[ProtocolDeviationReleaseV1],
    claims: Sequence[SupportedClaimV1],
    assembled_at: str,
) -> ClaimSupportReleaseV1:
    promotion = _revalidate(promotion_release, DevelopmentPromotionReleaseV1)
    config = _revalidate(fusion_configuration, FusionConfigurationReleaseV1)
    auth = _revalidate(authorization, LockedTestAuthorizationReleaseV1)
    unseal_value = _revalidate(unseal, LockedAnnotationUnsealReleaseV1)
    if (
        auth.promotion_release_ref,
        auth.fusion_configuration_ref,
        unseal_value.authorization_ref,
    ) != (
        _promotion_ref(promotion),
        _fusion_configuration_ref(config),
        _locked_authorization_ref(auth),
    ):
        raise ValueError("claim support chain drifts from promotion/config/authorization")
    primary = _revalidate(primary_result, LockedPrimaryResultV1)
    secondaries = tuple(
        _revalidate(item, LockedSecondaryResultV1) for item in secondary_results
    )
    if tuple(item.hypothesis for item in secondaries) != LOCKED_SECONDARY_FAMILY:
        raise ValueError("claim support requires the fixed five secondary results")
    for item in secondaries:
        expected_promoted = _component_promoted_for_hypothesis(
            item.hypothesis, promotion.promoted_components
        )
        if item.component_promoted is not expected_promoted:
            raise ValueError("secondary applicability drifts from promotion release")
    expected_holm = _holm_adjusted(
        {item.hypothesis: item.raw_one_sided_p for item in secondaries}
    )
    if any(
        item.holm_adjusted_p != expected_holm[item.hypothesis]
        for item in secondaries
    ):
        raise ValueError("secondary Holm family does not exactly replay")
    deviations = tuple(
        sorted(
            (_revalidate(item, ProtocolDeviationReleaseV1) for item in protocol_deviations),
            key=lambda item: (item.release_id, item.release_sha256),
        )
    )
    if len({item.release_id for item in deviations}) != len(deviations):
        raise ValueError("claim support repeats a protocol deviation")
    for deviation in deviations:
        if deviation.unseal_ref is not None and deviation.unseal_ref != _unseal_ref(
            unseal_value
        ):
            raise ValueError("protocol deviation binds a foreign locked unseal")
    confirmatory_valid = not any(
        item.confirmatory_validity
        is ConfirmatoryValidity.INVALIDATED_SCORE_OR_CANDIDATE_CHANGE
        for item in deviations
    )
    primary_allowed = primary.metric_gate_passed and confirmatory_valid
    claim_values = tuple(_revalidate(item, SupportedClaimV1) for item in claims)
    _require_after(assembled_at, unseal_value.unsealed_at, "claim-support assembly")
    attestation = _revalidate(
        formal_verifier_attestation, FormalVerifierAttestationRefV1
    )
    _require_after(assembled_at, attestation.attested_at, "claim-support assembly")
    if deviations:
        _require_after(
            assembled_at,
            max(deviations, key=lambda item: _timestamp(item.disclosed_at)).disclosed_at,
            "claim-support assembly",
        )
    return _build_addressed(
        ClaimSupportReleaseV1,
        id_field="release_id",
        sha_field="release_sha256",
        prefix="claim-support-v1",
        values={
            "promotion_release_ref": _promotion_ref(promotion),
            "fusion_configuration_ref": _fusion_configuration_ref(config),
            "authorization_ref": _locked_authorization_ref(auth),
            "unseal_ref": _unseal_ref(unseal_value),
            "analysis_input_ref": _revalidate(
                analysis_input_ref, TypedArtifactRefV1
            ),
            "formal_verifier_attestation": attestation,
            "primary_result": primary,
            "secondary_results": secondaries,
            "protocol_deviation_refs": tuple(_deviation_ref(item) for item in deviations),
            "claims": claim_values,
            "confirmatory_run_valid": confirmatory_valid,
            "primary_claim_allowed": primary_allowed,
            "assembled_at": assembled_at,
        },
    )


def assert_claim_support_release_exact_replay_v1(
    release: ClaimSupportReleaseV1,
    *,
    promotion_release: DevelopmentPromotionReleaseV1,
    fusion_configuration: FusionConfigurationReleaseV1,
    authorization: LockedTestAuthorizationReleaseV1,
    unseal: LockedAnnotationUnsealReleaseV1,
    protocol_deviations: Sequence[ProtocolDeviationReleaseV1],
) -> None:
    value = _revalidate(release, ClaimSupportReleaseV1)
    expected = build_claim_support_release_v1(
        promotion_release=promotion_release,
        fusion_configuration=fusion_configuration,
        authorization=authorization,
        unseal=unseal,
        analysis_input_ref=value.analysis_input_ref,
        formal_verifier_attestation=value.formal_verifier_attestation,
        primary_result=value.primary_result,
        secondary_results=value.secondary_results,
        protocol_deviations=protocol_deviations,
        claims=value.claims,
        assembled_at=value.assembled_at,
    )
    if value != expected:
        raise ValueError("claim-support release does not exactly replay")


def _claim_support_ref(release: ClaimSupportReleaseV1) -> TypedArtifactRefV1:
    return _owned_ref(
        release,
        artifact_type=LifecycleArtifactType.CLAIM_SUPPORT_RELEASE,
        id_field="release_id",
        sha_field="release_sha256",
    )


class PublicProtocolDeviationSummaryV1(StrictModel):
    deviation_ordinal: Annotated[int, Field(ge=1, le=128)]
    severity: ProtocolDeviationSeverity
    confirmatory_validity: ConfirmatoryValidity
    disclosed: Literal[True] = True


class PublicLimitationCodeV1(StrEnum):
    """Frozen, non-parametric public limitation vocabulary.

    Free-form text is deliberately excluded from the public projection.  A
    presentation layer may render these codes with repository-frozen text.
    """

    BENCHMARK_SCOPE_ONLY = "BENCHMARK_SCOPE_ONLY"
    NO_MATERIAL_DISCOVERY_CLAIM = "NO_MATERIAL_DISCOVERY_CLAIM"
    NO_DFT_OR_EXPERIMENTAL_VALIDATION = "NO_DFT_OR_EXPERIMENTAL_VALIDATION"
    INTERNAL_REPLAY_NOT_PROVIDER_ATTESTED = (
        "INTERNAL_REPLAY_NOT_PROVIDER_ATTESTED"
    )
    NARROW_OR_FLAT_BAND_SCOPE = "NARROW_OR_FLAT_BAND_SCOPE"
    PERFORMANCE_NOT_EVALUATED = "PERFORMANCE_NOT_EVALUATED"


_FORBIDDEN_PUBLIC_FIELD_FRAGMENTS = (
    "rawannotation",
    "rawreview",
    "blindingkey",
    "privateidentity",
    "privateartifacturi",
    "identityevidenceartifacturi",
    "naturalpersoncommitment",
    "expertassignment",
    "providertranscript",
    "structurebytes",
    "apikey",
    "secretkey",
    "custodypayload",
)
_FORBIDDEN_PUBLIC_VALUE_PATTERNS = tuple(
    re.compile(pattern, flags=re.IGNORECASE)
    for pattern in (
        r"private://",
        r"(?:^|/)private(?:/|$)",
        r"-----BEGIN [A-Z ]*PRIVATE KEY-----",
        r"\braw[_ -]?annotation\b",
        r"\braw[_ -]?review\b",
        r"\bblinding[_ -]?key\b",
        r"\bapi[_ -]?key\b",
        r"\bprovider[_ -]?transcript\b",
        r"\bidentity[_ -]?evidence[_ -]?(?:artifact[_ -]?)?uri\b",
    )
)


def _normalized_field(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", value.casefold())


def _assert_public_payload_safe(value: object, *, path: str = "$") -> None:
    """Recursively reject forbidden public keys and secret-bearing values."""

    if isinstance(value, StrictModel):
        _assert_public_payload_safe(value.model_dump(mode="python"), path=path)
        return
    if isinstance(value, Mapping):
        for key, child in value.items():
            normalized = _normalized_field(str(key))
            if any(fragment in normalized for fragment in _FORBIDDEN_PUBLIC_FIELD_FRAGMENTS):
                raise ValueError(f"public projection contains sensitive field at {path}.{key}")
            _assert_public_payload_safe(child, path=f"{path}.{key}")
        return
    if isinstance(value, (tuple, list)):
        for index, child in enumerate(value):
            _assert_public_payload_safe(child, path=f"{path}[{index}]")
        return
    if isinstance(value, str) and any(
        pattern.search(value) for pattern in _FORBIDDEN_PUBLIC_VALUE_PATTERNS
    ):
        raise ValueError(f"public projection contains sensitive value at {path}")


class PublicBenchmarkProjectionV1(StrictModel):
    """Strict aggregate-only projection reviewed before release authorization."""

    schema_version: Literal["flatband-public-benchmark-projection-v1"] = (
        "flatband-public-benchmark-projection-v1"
    )
    projection_id: Identifier
    projection_sha256: Sha256
    claim_support_ref: TypedArtifactRefV1
    promotion_release_ref: TypedArtifactRefV1
    fusion_configuration_ref: TypedArtifactRefV1
    development_case_count: Literal[60] = DEVELOPMENT_CASE_COUNT
    locked_iid_case_count: Literal[30] = LOCKED_IID_CASE_COUNT
    locked_ood_case_count: Literal[30] = LOCKED_OOD_CASE_COUNT
    promoted_components: Annotated[
        tuple[ResearchSystemId, ...], Field(max_length=3)
    ]
    selected_e2_variant: Literal[
        ResearchSystemId.E2_A, ResearchSystemId.E2_B
    ] | None
    fusion_components: Annotated[
        tuple[ResearchSystemId, ...], Field(min_length=1, max_length=3)
    ]
    primary_result: LockedPrimaryResultV1
    secondary_results: Annotated[
        tuple[LockedSecondaryResultV1, ...], Field(min_length=5, max_length=5)
    ]
    claims: Annotated[tuple[SupportedClaimV1, ...], Field(min_length=1, max_length=16)]
    protocol_deviations: Annotated[
        tuple[PublicProtocolDeviationSummaryV1, ...], Field(max_length=128)
    ] = ()
    limitation_codes: Annotated[
        tuple[PublicLimitationCodeV1, ...], Field(min_length=1, max_length=16)
    ]
    confirmatory_run_valid: bool
    primary_claim_allowed: bool
    aggregate_only: Literal[True] = True
    restricted_text_included: Literal[False] = False
    expert_identity_mapping_included: Literal[False] = False
    scientific_conclusion: Literal[False] = False

    @model_validator(mode="after")
    def validate_projection(self) -> PublicBenchmarkProjectionV1:
        _require_ref_type(
            self.claim_support_ref,
            LifecycleArtifactType.CLAIM_SUPPORT_RELEASE,
            "public projection claim support",
        )
        _require_ref_type(
            self.promotion_release_ref,
            LifecycleArtifactType.DEVELOPMENT_PROMOTION_RELEASE,
            "public projection promotion",
        )
        _require_ref_type(
            self.fusion_configuration_ref,
            LifecycleArtifactType.FUSION_CONFIGURATION_RELEASE,
            "public projection Fusion configuration",
        )
        if self.fusion_components != self.promoted_components:
            raise ValueError("public Fusion projection differs from promoted components")
        if tuple(item.hypothesis for item in self.secondary_results) != (
            LOCKED_SECONDARY_FAMILY
        ):
            raise ValueError("public projection drops or changes the secondary family")
        if self.primary_claim_allowed != (
            self.primary_result.metric_gate_passed and self.confirmatory_run_valid
        ):
            raise ValueError("public primary claim status differs from projected evidence")
        claim_kinds = {item.claim_kind for item in self.claims}
        expected_primary = (
            ClaimKind.BENCHMARK_PRIMARY_PASSED
            if self.primary_claim_allowed
            else ClaimKind.BENCHMARK_PRIMARY_NOT_PASSED
        )
        if expected_primary not in claim_kinds:
            raise ValueError("public claim family omits its primary status")
        if self.limitation_codes != tuple(
            sorted(set(self.limitation_codes), key=lambda item: item.value)
        ):
            raise ValueError("public limitation codes must be sorted and unique")
        _assert_public_payload_safe(
            self.model_dump(
                mode="python", exclude={"projection_id", "projection_sha256"}
            )
        )
        _assert_addressed(
            self,
            id_field="projection_id",
            sha_field="projection_sha256",
            prefix="public-projection-v1",
        )
        return self


def build_public_benchmark_projection_v1(
    *,
    claim_support: ClaimSupportReleaseV1,
    promotion_release: DevelopmentPromotionReleaseV1,
    fusion_configuration: FusionConfigurationReleaseV1,
    protocol_deviations: Sequence[ProtocolDeviationReleaseV1],
    limitation_codes: Sequence[PublicLimitationCodeV1],
) -> PublicBenchmarkProjectionV1:
    support = _revalidate(claim_support, ClaimSupportReleaseV1)
    promotion = _revalidate(promotion_release, DevelopmentPromotionReleaseV1)
    config = _revalidate(fusion_configuration, FusionConfigurationReleaseV1)
    try:
        limitation_values = tuple(
            PublicLimitationCodeV1(item) for item in limitation_codes
        )
    except (TypeError, ValueError) as exc:
        raise ValueError("public limitations must use the frozen code vocabulary") from exc
    deviations = tuple(
        sorted(
            (_revalidate(item, ProtocolDeviationReleaseV1) for item in protocol_deviations),
            key=lambda item: (item.release_id, item.release_sha256),
        )
    )
    if (
        support.promotion_release_ref,
        support.fusion_configuration_ref,
        support.protocol_deviation_refs,
    ) != (
        _promotion_ref(promotion),
        _fusion_configuration_ref(config),
        tuple(_deviation_ref(item) for item in deviations),
    ):
        raise ValueError("public projection inputs drift from claim-support closure")
    summaries = tuple(
        PublicProtocolDeviationSummaryV1(
            deviation_ordinal=index,
            severity=item.severity,
            confirmatory_validity=item.confirmatory_validity,
        )
        for index, item in enumerate(deviations, start=1)
    )
    return _build_addressed(
        PublicBenchmarkProjectionV1,
        id_field="projection_id",
        sha_field="projection_sha256",
        prefix="public-projection-v1",
        values={
            "claim_support_ref": _claim_support_ref(support),
            "promotion_release_ref": _promotion_ref(promotion),
            "fusion_configuration_ref": _fusion_configuration_ref(config),
            "promoted_components": promotion.promoted_components,
            "selected_e2_variant": promotion.selected_e2_variant,
            "fusion_components": config.fusion_components,
            "primary_result": support.primary_result,
            "secondary_results": support.secondary_results,
            "claims": support.claims,
            "protocol_deviations": summaries,
            "limitation_codes": tuple(
                sorted(set(limitation_values), key=lambda item: item.value)
            ),
            "confirmatory_run_valid": support.confirmatory_run_valid,
            "primary_claim_allowed": support.primary_claim_allowed,
        },
    )


def assert_public_benchmark_projection_exact_replay_v1(
    projection: PublicBenchmarkProjectionV1,
    *,
    claim_support: ClaimSupportReleaseV1,
    promotion_release: DevelopmentPromotionReleaseV1,
    fusion_configuration: FusionConfigurationReleaseV1,
    protocol_deviations: Sequence[ProtocolDeviationReleaseV1],
) -> None:
    value = _revalidate(projection, PublicBenchmarkProjectionV1)
    expected = build_public_benchmark_projection_v1(
        claim_support=claim_support,
        promotion_release=promotion_release,
        fusion_configuration=fusion_configuration,
        protocol_deviations=protocol_deviations,
        limitation_codes=value.limitation_codes,
    )
    if value != expected:
        raise ValueError("public benchmark projection does not exactly replay")


def _public_projection_ref(
    projection: PublicBenchmarkProjectionV1,
) -> TypedArtifactRefV1:
    return _owned_ref(
        projection,
        artifact_type=LifecycleArtifactType.PUBLIC_BENCHMARK_PROJECTION,
        id_field="projection_id",
        sha_field="projection_sha256",
    )


class ScientificReviewerDecision(StrEnum):
    APPROVE = "APPROVE"
    APPROVE_WITH_LIMITATIONS = "APPROVE_WITH_LIMITATIONS"
    REJECT = "REJECT"


class ScientificReviewerIdentityAttestationV1(StrictModel):
    """Externally keyed identity preimage; no natural-person name is stored."""

    schema_version: Literal[
        "flatband-scientific-reviewer-identity-attestation-v1"
    ] = "flatband-scientific-reviewer-identity-attestation-v1"
    identity_attestation_id: Identifier
    identity_attestation_sha256: Sha256
    authority_policy_id: Identifier
    authority_policy_sha256: Sha256
    authority_id: Identifier
    authority_key_commitment_sha256: Sha256
    reviewer_id: Identifier
    natural_person_commitment_sha256: Sha256
    private_identity_evidence_sha256: Sha256
    reviewer_decision_key_commitment_sha256: Sha256
    issued_at: Annotated[str, Field(min_length=20, max_length=40)]
    signature_hmac_sha256: Sha256
    signature_algorithm: Literal["HMAC-SHA256-PRECOMMITTED"] = (
        "HMAC-SHA256-PRECOMMITTED"
    )
    private_identity_evidence_included: Literal[False] = False
    key_material_included: Literal[False] = False
    external_authority_identity_attestation: Literal["NOT_PROVIDED"] = "NOT_PROVIDED"
    external_key_custody_attestation: Literal["NOT_PROVIDED"] = "NOT_PROVIDED"
    scientific_conclusion: Literal[False] = False

    @field_validator("issued_at")
    @classmethod
    def validate_time(cls, value: str) -> str:
        _timestamp(value)
        return value

    @model_validator(mode="after")
    def validate_identity(self) -> ScientificReviewerIdentityAttestationV1:
        _assert_addressed(
            self,
            id_field="identity_attestation_id",
            sha_field="identity_attestation_sha256",
            prefix="science-reviewer-identity-v1",
        )
        return self


def _reviewer_identity_signature_payload(
    *,
    policy: ReleaseAuthorityPolicyV1,
    binding: ReleaseAuthorityBindingV1,
    reviewer_id: str,
    natural_person_commitment_sha256: str,
    private_identity_evidence_sha256: str,
    reviewer_decision_key_commitment_sha256: str,
    issued_at: str,
) -> dict[str, object]:
    return {
        "signature_schema_version": "flatband-scientific-reviewer-identity-signature-v1",
        "authority_policy_id": policy.policy_id,
        "authority_policy_sha256": policy.policy_sha256,
        "authority_id": binding.authority_id,
        "authority_key_commitment_sha256": binding.key_commitment_sha256,
        "reviewer_id": reviewer_id,
        "natural_person_commitment_sha256": natural_person_commitment_sha256,
        "private_identity_evidence_sha256": private_identity_evidence_sha256,
        "reviewer_decision_key_commitment_sha256": (
            reviewer_decision_key_commitment_sha256
        ),
        "issued_at": issued_at,
    }


def build_scientific_reviewer_identity_attestation_v1(
    *,
    authority_policy: ReleaseAuthorityPolicyV1,
    authority_key: bytes,
    reviewer_id: str,
    natural_person_commitment_sha256: str,
    private_identity_evidence_sha256: str,
    reviewer_decision_key: bytes,
    issued_at: str,
) -> ScientificReviewerIdentityAttestationV1:
    policy = _revalidate(authority_policy, ReleaseAuthorityPolicyV1)
    binding = _authority_binding(
        policy, ReleaseAuthorityRoleV1.SCIENTIFIC_REVIEWER_IDENTITY, authority_key
    )
    _require_after(issued_at, policy.frozen_at, "reviewer identity attestation")
    payload = _reviewer_identity_signature_payload(
        policy=policy,
        binding=binding,
        reviewer_id=reviewer_id,
        natural_person_commitment_sha256=natural_person_commitment_sha256,
        private_identity_evidence_sha256=private_identity_evidence_sha256,
        reviewer_decision_key_commitment_sha256=hashlib.sha256(
            _require_authority_key(reviewer_decision_key)
        ).hexdigest(),
        issued_at=issued_at,
    )
    return _build_addressed(
        ScientificReviewerIdentityAttestationV1,
        id_field="identity_attestation_id",
        sha_field="identity_attestation_sha256",
        prefix="science-reviewer-identity-v1",
        values={
            **{
                key: value
                for key, value in payload.items()
                if key != "signature_schema_version"
            },
            "signature_hmac_sha256": _authority_signature(authority_key, payload),
        },
    )


def assert_scientific_reviewer_identity_attestation_exact_replay_v1(
    attestation: ScientificReviewerIdentityAttestationV1,
    *,
    authority_policy: ReleaseAuthorityPolicyV1,
    authority_key: bytes,
    reviewer_decision_key: bytes,
) -> None:
    value = _revalidate(attestation, ScientificReviewerIdentityAttestationV1)
    expected = build_scientific_reviewer_identity_attestation_v1(
        authority_policy=authority_policy,
        authority_key=authority_key,
        reviewer_id=value.reviewer_id,
        natural_person_commitment_sha256=value.natural_person_commitment_sha256,
        private_identity_evidence_sha256=value.private_identity_evidence_sha256,
        reviewer_decision_key=reviewer_decision_key,
        issued_at=value.issued_at,
    )
    if value != expected:
        raise ValueError("scientific reviewer identity attestation does not replay")


class ReleaseControlKindV1(StrEnum):
    LICENSE = "LICENSE"
    PRIVACY = "PRIVACY"
    CUSTODY = "CUSTODY"


_CONTROL_AUTHORITY_ROLE = {
    ReleaseControlKindV1.LICENSE: ReleaseAuthorityRoleV1.LICENSE_RELEASE,
    ReleaseControlKindV1.PRIVACY: ReleaseAuthorityRoleV1.PRIVACY_RELEASE,
    ReleaseControlKindV1.CUSTODY: ReleaseAuthorityRoleV1.CUSTODY_RELEASE,
}


class ReleaseControlAttestationV1(StrictModel):
    schema_version: Literal["flatband-release-control-attestation-v1"] = (
        "flatband-release-control-attestation-v1"
    )
    attestation_id: Identifier
    attestation_sha256: Sha256
    authority_policy_id: Identifier
    authority_policy_sha256: Sha256
    control_kind: ReleaseControlKindV1
    authority_id: Identifier
    authority_key_commitment_sha256: Sha256
    public_projection_ref: TypedArtifactRefV1
    evidence_artifact_sha256: Sha256
    decision: Literal["APPROVED"] = "APPROVED"
    issued_at: Annotated[str, Field(min_length=20, max_length=40)]
    signature_hmac_sha256: Sha256
    signature_algorithm: Literal["HMAC-SHA256-PRECOMMITTED"] = (
        "HMAC-SHA256-PRECOMMITTED"
    )
    private_evidence_included: Literal[False] = False
    key_material_included: Literal[False] = False
    external_authority_identity_attestation: Literal["NOT_PROVIDED"] = "NOT_PROVIDED"
    external_key_custody_attestation: Literal["NOT_PROVIDED"] = "NOT_PROVIDED"
    scientific_conclusion: Literal[False] = False

    @field_validator("issued_at")
    @classmethod
    def validate_time(cls, value: str) -> str:
        _timestamp(value)
        return value

    @model_validator(mode="after")
    def validate_control(self) -> ReleaseControlAttestationV1:
        _require_ref_type(
            self.public_projection_ref,
            LifecycleArtifactType.PUBLIC_BENCHMARK_PROJECTION,
            "release-control projection",
        )
        _assert_addressed(
            self,
            id_field="attestation_id",
            sha_field="attestation_sha256",
            prefix="release-control-v1",
        )
        return self


def _release_control_signature_payload(
    *,
    policy: ReleaseAuthorityPolicyV1,
    binding: ReleaseAuthorityBindingV1,
    control_kind: ReleaseControlKindV1,
    public_projection_ref: TypedArtifactRefV1,
    evidence_artifact_sha256: str,
    issued_at: str,
) -> dict[str, object]:
    return {
        "signature_schema_version": "flatband-release-control-signature-v1",
        "authority_policy_id": policy.policy_id,
        "authority_policy_sha256": policy.policy_sha256,
        "control_kind": control_kind.value,
        "authority_id": binding.authority_id,
        "authority_key_commitment_sha256": binding.key_commitment_sha256,
        "public_projection_ref": public_projection_ref,
        "evidence_artifact_sha256": evidence_artifact_sha256,
        "decision": "APPROVED",
        "issued_at": issued_at,
    }


def build_release_control_attestation_v1(
    *,
    authority_policy: ReleaseAuthorityPolicyV1,
    authority_key: bytes,
    control_kind: ReleaseControlKindV1,
    public_projection: PublicBenchmarkProjectionV1,
    evidence_artifact_sha256: str,
    issued_at: str,
) -> ReleaseControlAttestationV1:
    policy = _revalidate(authority_policy, ReleaseAuthorityPolicyV1)
    projection = _revalidate(public_projection, PublicBenchmarkProjectionV1)
    binding = _authority_binding(
        policy, _CONTROL_AUTHORITY_ROLE[control_kind], authority_key
    )
    _require_after(issued_at, policy.frozen_at, "release-control attestation")
    projection_ref = _public_projection_ref(projection)
    payload = _release_control_signature_payload(
        policy=policy,
        binding=binding,
        control_kind=control_kind,
        public_projection_ref=projection_ref,
        evidence_artifact_sha256=evidence_artifact_sha256,
        issued_at=issued_at,
    )
    return _build_addressed(
        ReleaseControlAttestationV1,
        id_field="attestation_id",
        sha_field="attestation_sha256",
        prefix="release-control-v1",
        values={
            **{
                key: value
                for key, value in payload.items()
                if key != "signature_schema_version"
            },
            "control_kind": control_kind,
            "public_projection_ref": projection_ref,
            "signature_hmac_sha256": _authority_signature(authority_key, payload),
        },
    )


def assert_release_control_attestation_exact_replay_v1(
    attestation: ReleaseControlAttestationV1,
    *,
    authority_policy: ReleaseAuthorityPolicyV1,
    authority_key: bytes,
    public_projection: PublicBenchmarkProjectionV1,
) -> None:
    value = _revalidate(attestation, ReleaseControlAttestationV1)
    expected = build_release_control_attestation_v1(
        authority_policy=authority_policy,
        authority_key=authority_key,
        control_kind=value.control_kind,
        public_projection=public_projection,
        evidence_artifact_sha256=value.evidence_artifact_sha256,
        issued_at=value.issued_at,
    )
    if value != expected:
        raise ValueError("release-control attestation does not replay")


class ScientificReviewerAttestationV1(StrictModel):
    schema_version: Literal["flatband-scientific-reviewer-attestation-v1"] = (
        "flatband-scientific-reviewer-attestation-v1"
    )
    attestation_id: Identifier
    attestation_sha256: Sha256
    reviewer_id: Identifier
    reviewer_identity_attestation: ScientificReviewerIdentityAttestationV1
    claim_support_ref: TypedArtifactRefV1
    public_projection_ref: TypedArtifactRefV1
    decision: ScientificReviewerDecision
    critical_issue_count: Annotated[int, Field(ge=0, le=100)]
    findings: Annotated[tuple[ShortText, ...], Field(max_length=64)] = ()
    required_limitation_codes: Annotated[
        tuple[PublicLimitationCodeV1, ...], Field(max_length=16)
    ] = ()
    reviewed_at: Annotated[str, Field(min_length=20, max_length=40)]
    review_signature_hmac_sha256: Sha256
    review_signature_algorithm: Literal["HMAC-SHA256-PRECOMMITTED"] = (
        "HMAC-SHA256-PRECOMMITTED"
    )
    reviewer_decision_key_material_included: Literal[False] = False
    benchmark_annotator_disjointness_requires_campaign_replay: Literal[True] = True
    natural_person_independence_requires_signed_identity: Literal[True] = True
    scientific_conclusion: Literal[False] = False

    @field_validator("reviewed_at")
    @classmethod
    def validate_time(cls, value: str) -> str:
        _timestamp(value)
        return value

    @model_validator(mode="after")
    def validate_attestation(self) -> ScientificReviewerAttestationV1:
        identity = _revalidate(
            self.reviewer_identity_attestation,
            ScientificReviewerIdentityAttestationV1,
        )
        if self.reviewer_id != identity.reviewer_id:
            raise ValueError("scientific reviewer ID differs from signed identity")
        _require_ref_type(
            self.claim_support_ref,
            LifecycleArtifactType.CLAIM_SUPPORT_RELEASE,
            "scientific reviewer claim support",
        )
        _require_ref_type(
            self.public_projection_ref,
            LifecycleArtifactType.PUBLIC_BENCHMARK_PROJECTION,
            "scientific reviewer public projection",
        )
        if self.decision is ScientificReviewerDecision.APPROVE and (
            self.critical_issue_count or self.required_limitation_codes
        ):
            raise ValueError("unqualified approval cannot retain required corrections")
        if self.decision is ScientificReviewerDecision.APPROVE_WITH_LIMITATIONS and (
            self.critical_issue_count or not self.required_limitation_codes
        ):
            raise ValueError("approval with limitations requires noncritical limitations")
        if self.decision is ScientificReviewerDecision.REJECT and (
            self.critical_issue_count == 0
        ):
            raise ValueError("scientific rejection requires at least one critical issue")
        if self.required_limitation_codes != tuple(
            sorted(
                set(self.required_limitation_codes), key=lambda item: item.value
            )
        ):
            raise ValueError("required limitation codes must be sorted and unique")
        _assert_addressed(
            self,
            id_field="attestation_id",
            sha_field="attestation_sha256",
            prefix="science-reviewer-v1",
        )
        return self


def _scientific_review_signature_payload(
    *,
    identity: ScientificReviewerIdentityAttestationV1,
    claim_support_ref: TypedArtifactRefV1,
    public_projection_ref: TypedArtifactRefV1,
    decision: ScientificReviewerDecision,
    critical_issue_count: int,
    findings: tuple[str, ...],
    required_limitation_codes: tuple[PublicLimitationCodeV1, ...],
    reviewed_at: str,
) -> dict[str, object]:
    return {
        "signature_schema_version": "flatband-scientific-review-decision-signature-v1",
        "reviewer_identity_attestation_id": identity.identity_attestation_id,
        "reviewer_identity_attestation_sha256": identity.identity_attestation_sha256,
        "reviewer_id": identity.reviewer_id,
        "claim_support_ref": claim_support_ref,
        "public_projection_ref": public_projection_ref,
        "decision": decision.value,
        "critical_issue_count": critical_issue_count,
        "findings": findings,
        "required_limitation_codes": tuple(
            item.value for item in required_limitation_codes
        ),
        "reviewed_at": reviewed_at,
    }


def build_scientific_reviewer_attestation_v1(
    *,
    reviewer_identity_attestation: ScientificReviewerIdentityAttestationV1,
    authority_policy: ReleaseAuthorityPolicyV1,
    reviewer_identity_authority_key: bytes,
    reviewer_decision_key: bytes,
    claim_support: ClaimSupportReleaseV1,
    public_projection: PublicBenchmarkProjectionV1,
    decision: ScientificReviewerDecision,
    critical_issue_count: int,
    findings: Sequence[str],
    required_limitation_codes: Sequence[PublicLimitationCodeV1],
    reviewed_at: str,
) -> ScientificReviewerAttestationV1:
    support = _revalidate(claim_support, ClaimSupportReleaseV1)
    projection = _revalidate(public_projection, PublicBenchmarkProjectionV1)
    identity = _revalidate(
        reviewer_identity_attestation,
        ScientificReviewerIdentityAttestationV1,
    )
    assert_scientific_reviewer_identity_attestation_exact_replay_v1(
        identity,
        authority_policy=authority_policy,
        authority_key=reviewer_identity_authority_key,
        reviewer_decision_key=reviewer_decision_key,
    )
    if projection.claim_support_ref != _claim_support_ref(support):
        raise ValueError("scientific review projection binds foreign claim support")
    if decision is ScientificReviewerDecision.APPROVE_WITH_LIMITATIONS and not set(
        required_limitation_codes
    ).issubset(set(projection.limitation_codes)):
        raise ValueError(
            "required scientific-review limitations are absent from public projection"
        )
    _require_after(reviewed_at, support.assembled_at, "scientific reviewer attestation")
    _require_after(reviewed_at, identity.issued_at, "scientific reviewer attestation")
    decision_key = _require_authority_key(reviewer_decision_key)
    if hashlib.sha256(decision_key).hexdigest() != (
        identity.reviewer_decision_key_commitment_sha256
    ):
        raise ValueError("scientific review decision key differs from signed identity")
    support_ref = _claim_support_ref(support)
    projection_ref = _public_projection_ref(projection)
    finding_values = tuple(findings)
    limitation_values = tuple(
        sorted(set(required_limitation_codes), key=lambda item: item.value)
    )
    signature_payload = _scientific_review_signature_payload(
        identity=identity,
        claim_support_ref=support_ref,
        public_projection_ref=projection_ref,
        decision=decision,
        critical_issue_count=critical_issue_count,
        findings=finding_values,
        required_limitation_codes=limitation_values,
        reviewed_at=reviewed_at,
    )
    return _build_addressed(
        ScientificReviewerAttestationV1,
        id_field="attestation_id",
        sha_field="attestation_sha256",
        prefix="science-reviewer-v1",
        values={
            "reviewer_id": identity.reviewer_id,
            "reviewer_identity_attestation": identity,
            "claim_support_ref": support_ref,
            "public_projection_ref": projection_ref,
            "decision": decision,
            "critical_issue_count": critical_issue_count,
            "findings": finding_values,
            "required_limitation_codes": limitation_values,
            "reviewed_at": reviewed_at,
            "review_signature_hmac_sha256": _authority_signature(
                decision_key, signature_payload
            ),
        },
    )


def assert_scientific_reviewer_attestation_exact_replay_v1(
    attestation: ScientificReviewerAttestationV1,
    *,
    authority_policy: ReleaseAuthorityPolicyV1,
    reviewer_identity_authority_key: bytes,
    reviewer_decision_key: bytes,
    claim_support: ClaimSupportReleaseV1,
    public_projection: PublicBenchmarkProjectionV1,
) -> None:
    value = _revalidate(attestation, ScientificReviewerAttestationV1)
    expected = build_scientific_reviewer_attestation_v1(
        reviewer_identity_attestation=value.reviewer_identity_attestation,
        authority_policy=authority_policy,
        reviewer_identity_authority_key=reviewer_identity_authority_key,
        reviewer_decision_key=reviewer_decision_key,
        claim_support=claim_support,
        public_projection=public_projection,
        decision=value.decision,
        critical_issue_count=value.critical_issue_count,
        findings=value.findings,
        required_limitation_codes=value.required_limitation_codes,
        reviewed_at=value.reviewed_at,
    )
    if value != expected:
        raise ValueError("scientific reviewer attestation does not exactly replay")


class ScientificReviewDecision(StrEnum):
    RELEASE_APPROVED = "RELEASE_APPROVED"
    RELEASE_REJECTED = "RELEASE_REJECTED"


class ScientificReviewReleaseV1(StrictModel):
    schema_version: Literal["flatband-scientific-review-release-v1"] = (
        "flatband-scientific-review-release-v1"
    )
    release_id: Identifier
    release_sha256: Sha256
    claim_support_ref: TypedArtifactRefV1
    public_projection_ref: TypedArtifactRefV1
    reviewer_attestations: Annotated[
        tuple[ScientificReviewerAttestationV1, ...], Field(min_length=2, max_length=5)
    ]
    decision: ScientificReviewDecision
    reviewed_at: Annotated[str, Field(min_length=20, max_length=40)]
    two_independent_reviews_required: Literal[True] = True
    public_release_requires_separate_authorization: Literal[True] = True
    scientific_conclusion: Literal[False] = False

    @field_validator("reviewed_at")
    @classmethod
    def validate_time(cls, value: str) -> str:
        _timestamp(value)
        return value

    @model_validator(mode="after")
    def validate_review(self) -> ScientificReviewReleaseV1:
        _require_ref_type(
            self.claim_support_ref,
            LifecycleArtifactType.CLAIM_SUPPORT_RELEASE,
            "scientific review claim support",
        )
        _require_ref_type(
            self.public_projection_ref,
            LifecycleArtifactType.PUBLIC_BENCHMARK_PROJECTION,
            "scientific review projection",
        )
        reviewer_keys = tuple(
            (item.reviewer_id, item.attestation_id)
            for item in self.reviewer_attestations
        )
        if reviewer_keys != tuple(sorted(set(reviewer_keys))):
            raise ValueError("scientific reviewer attestations must be sorted and unique")
        identity_addresses = tuple(
            (
                item.reviewer_identity_attestation.identity_attestation_id,
                item.reviewer_identity_attestation.identity_attestation_sha256,
            )
            for item in self.reviewer_attestations
        )
        person_commitments = tuple(
            item.reviewer_identity_attestation.natural_person_commitment_sha256
            for item in self.reviewer_attestations
        )
        if len(identity_addresses) != len(set(identity_addresses)) or len(
            person_commitments
        ) != len(set(person_commitments)):
            raise ValueError("scientific reviewers must be distinct natural persons")
        if any(
            (item.claim_support_ref, item.public_projection_ref)
            != (self.claim_support_ref, self.public_projection_ref)
            for item in self.reviewer_attestations
        ):
            raise ValueError("scientific reviewer attestation binds foreign review inputs")
        expected = (
            ScientificReviewDecision.RELEASE_APPROVED
            if all(
                item.decision
                in {
                    ScientificReviewerDecision.APPROVE,
                    ScientificReviewerDecision.APPROVE_WITH_LIMITATIONS,
                }
                for item in self.reviewer_attestations
            )
            else ScientificReviewDecision.RELEASE_REJECTED
        )
        if self.decision is not expected:
            raise ValueError("scientific review decision does not replay")
        if any(
            _timestamp(self.reviewed_at) <= _timestamp(item.reviewed_at)
            for item in self.reviewer_attestations
        ):
            raise ValueError("scientific review release must follow every attestation")
        _assert_addressed(
            self,
            id_field="release_id",
            sha_field="release_sha256",
            prefix="science-review-v1",
        )
        return self


def build_scientific_review_release_v1(
    *,
    claim_support: ClaimSupportReleaseV1,
    public_projection: PublicBenchmarkProjectionV1,
    reviewer_attestations: Sequence[ScientificReviewerAttestationV1],
    authority_policy: ReleaseAuthorityPolicyV1,
    reviewer_identity_authority_key: bytes,
    reviewer_decision_keys: Mapping[str, bytes],
    reviewed_at: str,
) -> ScientificReviewReleaseV1:
    support = _revalidate(claim_support, ClaimSupportReleaseV1)
    projection = _revalidate(public_projection, PublicBenchmarkProjectionV1)
    attestations = tuple(
        sorted(
            (
                _revalidate(item, ScientificReviewerAttestationV1)
                for item in reviewer_attestations
            ),
            key=lambda item: (item.reviewer_id, item.attestation_id),
        )
    )
    if set(reviewer_decision_keys) != {item.reviewer_id for item in attestations}:
        raise ValueError("scientific review requires one decision key per reviewer")
    for item in attestations:
        assert_scientific_reviewer_attestation_exact_replay_v1(
            item,
            authority_policy=authority_policy,
            reviewer_identity_authority_key=reviewer_identity_authority_key,
            reviewer_decision_key=reviewer_decision_keys[item.reviewer_id],
            claim_support=support,
            public_projection=projection,
        )
        _require_after(reviewed_at, item.reviewed_at, "scientific review release")
    decision = (
        ScientificReviewDecision.RELEASE_APPROVED
        if all(
            item.decision
            in {
                ScientificReviewerDecision.APPROVE,
                ScientificReviewerDecision.APPROVE_WITH_LIMITATIONS,
            }
            for item in attestations
        )
        else ScientificReviewDecision.RELEASE_REJECTED
    )
    return _build_addressed(
        ScientificReviewReleaseV1,
        id_field="release_id",
        sha_field="release_sha256",
        prefix="science-review-v1",
        values={
            "claim_support_ref": _claim_support_ref(support),
            "public_projection_ref": _public_projection_ref(projection),
            "reviewer_attestations": attestations,
            "decision": decision,
            "reviewed_at": reviewed_at,
        },
    )


def assert_scientific_review_release_exact_replay_v1(
    release: ScientificReviewReleaseV1,
    *,
    authority_policy: ReleaseAuthorityPolicyV1,
    reviewer_identity_authority_key: bytes,
    reviewer_decision_keys: Mapping[str, bytes],
    claim_support: ClaimSupportReleaseV1,
    public_projection: PublicBenchmarkProjectionV1,
) -> None:
    value = _revalidate(release, ScientificReviewReleaseV1)
    expected = build_scientific_review_release_v1(
        claim_support=claim_support,
        public_projection=public_projection,
        reviewer_attestations=value.reviewer_attestations,
        authority_policy=authority_policy,
        reviewer_identity_authority_key=reviewer_identity_authority_key,
        reviewer_decision_keys=reviewer_decision_keys,
        reviewed_at=value.reviewed_at,
    )
    if value != expected:
        raise ValueError("scientific review release does not exactly replay")


def _scientific_review_ref(
    release: ScientificReviewReleaseV1,
) -> TypedArtifactRefV1:
    return _owned_ref(
        release,
        artifact_type=LifecycleArtifactType.SCIENTIFIC_REVIEW_RELEASE,
        id_field="release_id",
        sha_field="release_sha256",
    )


class PublicReleaseAuthorizationV1(StrictModel):
    schema_version: Literal["flatband-public-release-authorization-v1"] = (
        "flatband-public-release-authorization-v1"
    )
    authorization_id: Identifier
    authorization_sha256: Sha256
    claim_support_ref: TypedArtifactRefV1
    public_projection_ref: TypedArtifactRefV1
    scientific_review_ref: TypedArtifactRefV1
    authority_policy: ReleaseAuthorityPolicyV1
    release_control_attestations: Annotated[
        tuple[ReleaseControlAttestationV1, ...], Field(min_length=3, max_length=3)
    ]
    projection_scan_policy_sha256: Sha256
    authorized_at: Annotated[str, Field(min_length=20, max_length=40)]
    aggregate_projection_only: Literal[True] = True
    sensitive_field_policy_fail_closed: Literal[True] = True
    license_privacy_custody_signatures_exact_replay_required: Literal[True] = True
    authorization_scope: Literal["INTERNAL_PRECOMMITTED_HMAC_POLICY"] = (
        "INTERNAL_PRECOMMITTED_HMAC_POLICY"
    )
    external_authority_identity_attestation: Literal["NOT_PROVIDED"] = "NOT_PROVIDED"
    external_key_custody_attestation: Literal["NOT_PROVIDED"] = "NOT_PROVIDED"
    external_publication_permission_claimed: Literal[False] = False
    authorization_count: Literal[1] = 1
    scientific_conclusion: Literal[False] = False

    @field_validator("authorized_at")
    @classmethod
    def validate_time(cls, value: str) -> str:
        _timestamp(value)
        return value

    @model_validator(mode="after")
    def validate_authorization(self) -> PublicReleaseAuthorizationV1:
        for ref, expected, label in (
            (
                self.claim_support_ref,
                LifecycleArtifactType.CLAIM_SUPPORT_RELEASE,
                "public authorization claim support",
            ),
            (
                self.public_projection_ref,
                LifecycleArtifactType.PUBLIC_BENCHMARK_PROJECTION,
                "public authorization projection",
            ),
            (
                self.scientific_review_ref,
                LifecycleArtifactType.SCIENTIFIC_REVIEW_RELEASE,
                "public authorization scientific review",
            ),
        ):
            _require_ref_type(ref, expected, label)
        _revalidate(self.authority_policy, ReleaseAuthorityPolicyV1)
        controls = tuple(
            _revalidate(item, ReleaseControlAttestationV1)
            for item in self.release_control_attestations
        )
        if tuple(item.control_kind.value for item in controls) != tuple(
            sorted(item.value for item in ReleaseControlKindV1)
        ):
            raise ValueError("public authorization requires license/privacy/custody exactly once")
        if any(
            (
                item.authority_policy_id,
                item.authority_policy_sha256,
                item.public_projection_ref,
            )
            != (
                self.authority_policy.policy_id,
                self.authority_policy.policy_sha256,
                self.public_projection_ref,
            )
            for item in controls
        ):
            raise ValueError("release control attestation binds foreign policy/projection")
        expected_scan = canonical_sha256(
            {
                "policy": "flatband-public-projection-sensitive-field-policy-v1",
                "forbidden_field_fragments": _FORBIDDEN_PUBLIC_FIELD_FRAGMENTS,
                "forbidden_value_patterns": tuple(
                    item.pattern for item in _FORBIDDEN_PUBLIC_VALUE_PATTERNS
                ),
                "projection_sha256": self.public_projection_ref.artifact_sha256,
            }
        )
        if self.projection_scan_policy_sha256 != expected_scan:
            raise ValueError("public projection scan-policy identity does not replay")
        _assert_addressed(
            self,
            id_field="authorization_id",
            sha_field="authorization_sha256",
            prefix="public-auth-v1",
        )
        return self


def _projection_scan_policy_sha256(
    projection: PublicBenchmarkProjectionV1,
) -> str:
    return canonical_sha256(
        {
            "policy": "flatband-public-projection-sensitive-field-policy-v1",
            "forbidden_field_fragments": _FORBIDDEN_PUBLIC_FIELD_FRAGMENTS,
            "forbidden_value_patterns": tuple(
                item.pattern for item in _FORBIDDEN_PUBLIC_VALUE_PATTERNS
            ),
            "projection_sha256": projection.projection_sha256,
        }
    )


def build_public_release_authorization_v1(
    *,
    claim_support: ClaimSupportReleaseV1,
    public_projection: PublicBenchmarkProjectionV1,
    scientific_review: ScientificReviewReleaseV1,
    authority_policy: ReleaseAuthorityPolicyV1,
    release_control_attestations: Sequence[ReleaseControlAttestationV1],
    authority_keys: Mapping[ReleaseAuthorityRoleV1, bytes],
    reviewer_identity_authority_key: bytes,
    reviewer_decision_keys: Mapping[str, bytes],
    authorized_at: str,
) -> PublicReleaseAuthorizationV1:
    support = _revalidate(claim_support, ClaimSupportReleaseV1)
    projection = _revalidate(public_projection, PublicBenchmarkProjectionV1)
    review = _revalidate(scientific_review, ScientificReviewReleaseV1)
    policy = _revalidate(authority_policy, ReleaseAuthorityPolicyV1)
    _require_after(
        support.assembled_at,
        policy.frozen_at,
        "claim support after release-authority freeze",
    )
    assert_scientific_review_release_exact_replay_v1(
        review,
        authority_policy=policy,
        reviewer_identity_authority_key=reviewer_identity_authority_key,
        reviewer_decision_keys=reviewer_decision_keys,
        claim_support=support,
        public_projection=projection,
    )
    if review.decision is not ScientificReviewDecision.RELEASE_APPROVED:
        raise ValueError("public release cannot be authorized after rejected review")
    _assert_public_payload_safe(projection)
    _require_after(authorized_at, review.reviewed_at, "public release authorization")
    controls = tuple(
        sorted(
            (
                _revalidate(item, ReleaseControlAttestationV1)
                for item in release_control_attestations
            ),
            key=lambda item: item.control_kind.value,
        )
    )
    if set(authority_keys) != set(ReleaseAuthorityRoleV1):
        raise ValueError("formal public authorization requires all authority keys")
    for item in controls:
        role = _CONTROL_AUTHORITY_ROLE[item.control_kind]
        assert_release_control_attestation_exact_replay_v1(
            item,
            authority_policy=policy,
            authority_key=authority_keys[role],
            public_projection=projection,
        )
        _require_after(
            item.issued_at,
            support.assembled_at,
            "release-control attestation",
        )
        _require_after(authorized_at, item.issued_at, "public release authorization")
    return _build_addressed(
        PublicReleaseAuthorizationV1,
        id_field="authorization_id",
        sha_field="authorization_sha256",
        prefix="public-auth-v1",
        values={
            "claim_support_ref": _claim_support_ref(support),
            "public_projection_ref": _public_projection_ref(projection),
            "scientific_review_ref": _scientific_review_ref(review),
            "authority_policy": policy,
            "release_control_attestations": controls,
            "projection_scan_policy_sha256": _projection_scan_policy_sha256(
                projection
            ),
            "authorized_at": authorized_at,
        },
    )


def assert_public_release_authorization_exact_replay_v1(
    release: PublicReleaseAuthorizationV1,
    *,
    claim_support: ClaimSupportReleaseV1,
    public_projection: PublicBenchmarkProjectionV1,
    scientific_review: ScientificReviewReleaseV1,
    authority_keys: Mapping[ReleaseAuthorityRoleV1, bytes],
    reviewer_identity_authority_key: bytes,
    reviewer_decision_keys: Mapping[str, bytes],
) -> None:
    value = _revalidate(release, PublicReleaseAuthorizationV1)
    expected = build_public_release_authorization_v1(
        claim_support=claim_support,
        public_projection=public_projection,
        scientific_review=scientific_review,
        authority_policy=value.authority_policy,
        release_control_attestations=value.release_control_attestations,
        authority_keys=authority_keys,
        reviewer_identity_authority_key=reviewer_identity_authority_key,
        reviewer_decision_keys=reviewer_decision_keys,
        authorized_at=value.authorized_at,
    )
    if value != expected:
        raise ValueError("public release authorization does not exactly replay")


def _public_authorization_ref(
    release: PublicReleaseAuthorizationV1,
) -> TypedArtifactRefV1:
    return _owned_ref(
        release,
        artifact_type=LifecycleArtifactType.PUBLIC_RELEASE_AUTHORIZATION,
        id_field="authorization_id",
        sha_field="authorization_sha256",
    )


class PublicBenchmarkResultReleaseV1(StrictModel):
    schema_version: Literal["flatband-public-benchmark-result-release-v1"] = (
        "flatband-public-benchmark-result-release-v1"
    )
    release_id: Identifier
    release_sha256: Sha256
    authorization_ref: TypedArtifactRefV1
    scientific_review_ref: TypedArtifactRefV1
    projection: PublicBenchmarkProjectionV1
    released_at: Annotated[str, Field(min_length=20, max_length=40)]
    sanitized_aggregate_only: Literal[True] = True
    novelty_claim_permitted: Literal[False] = False
    real_material_discovery_claim_permitted: Literal[False] = False
    dft_proof_claim_permitted: Literal[False] = False
    benchmark_scope_claim_only: Literal[True] = True
    authorization_scope: Literal["INTERNAL_PRECOMMITTED_HMAC_POLICY"] = (
        "INTERNAL_PRECOMMITTED_HMAC_POLICY"
    )
    external_authority_identity_attestation: Literal["NOT_PROVIDED"] = "NOT_PROVIDED"
    external_key_custody_attestation: Literal["NOT_PROVIDED"] = "NOT_PROVIDED"
    external_publication_permission_claimed: Literal[False] = False
    scientific_conclusion: Literal[False] = False

    @field_validator("released_at")
    @classmethod
    def validate_time(cls, value: str) -> str:
        _timestamp(value)
        return value

    @model_validator(mode="after")
    def validate_release(self) -> PublicBenchmarkResultReleaseV1:
        _require_ref_type(
            self.authorization_ref,
            LifecycleArtifactType.PUBLIC_RELEASE_AUTHORIZATION,
            "public result authorization",
        )
        _require_ref_type(
            self.scientific_review_ref,
            LifecycleArtifactType.SCIENTIFIC_REVIEW_RELEASE,
            "public result scientific review",
        )
        _assert_public_payload_safe(
            self.model_dump(mode="python", exclude={"release_id", "release_sha256"})
        )
        _assert_addressed(
            self,
            id_field="release_id",
            sha_field="release_sha256",
            prefix="public-result-v1",
        )
        return self


def build_public_benchmark_result_release_v1(
    *,
    authorization: PublicReleaseAuthorizationV1,
    scientific_review: ScientificReviewReleaseV1,
    public_projection: PublicBenchmarkProjectionV1,
    claim_support: ClaimSupportReleaseV1,
    authority_keys: Mapping[ReleaseAuthorityRoleV1, bytes],
    reviewer_identity_authority_key: bytes,
    reviewer_decision_keys: Mapping[str, bytes],
    released_at: str,
) -> PublicBenchmarkResultReleaseV1:
    auth = _revalidate(authorization, PublicReleaseAuthorizationV1)
    review = _revalidate(scientific_review, ScientificReviewReleaseV1)
    projection = _revalidate(public_projection, PublicBenchmarkProjectionV1)
    support = _revalidate(claim_support, ClaimSupportReleaseV1)
    assert_public_release_authorization_exact_replay_v1(
        auth,
        claim_support=support,
        public_projection=projection,
        scientific_review=review,
        authority_keys=authority_keys,
        reviewer_identity_authority_key=reviewer_identity_authority_key,
        reviewer_decision_keys=reviewer_decision_keys,
    )
    if (
        auth.claim_support_ref,
        auth.public_projection_ref,
        auth.scientific_review_ref,
    ) != (
        _claim_support_ref(support),
        _public_projection_ref(projection),
        _scientific_review_ref(review),
    ):
        raise ValueError("public result authorization drifts from reviewed projection")
    _assert_public_payload_safe(projection)
    _require_after(released_at, auth.authorized_at, "public benchmark release")
    return _build_addressed(
        PublicBenchmarkResultReleaseV1,
        id_field="release_id",
        sha_field="release_sha256",
        prefix="public-result-v1",
        values={
            "authorization_ref": _public_authorization_ref(auth),
            "scientific_review_ref": _scientific_review_ref(review),
            "projection": projection,
            "released_at": released_at,
        },
    )


def assert_public_benchmark_result_release_exact_replay_v1(
    release: PublicBenchmarkResultReleaseV1,
    *,
    authorization: PublicReleaseAuthorizationV1,
    scientific_review: ScientificReviewReleaseV1,
    public_projection: PublicBenchmarkProjectionV1,
    claim_support: ClaimSupportReleaseV1,
    authority_keys: Mapping[ReleaseAuthorityRoleV1, bytes],
    reviewer_identity_authority_key: bytes,
    reviewer_decision_keys: Mapping[str, bytes],
) -> None:
    value = _revalidate(release, PublicBenchmarkResultReleaseV1)
    expected = build_public_benchmark_result_release_v1(
        authorization=authorization,
        scientific_review=scientific_review,
        public_projection=public_projection,
        claim_support=claim_support,
        authority_keys=authority_keys,
        reviewer_identity_authority_key=reviewer_identity_authority_key,
        reviewer_decision_keys=reviewer_decision_keys,
        released_at=value.released_at,
    )
    if value != expected:
        raise ValueError("public benchmark result release does not exactly replay")


__all__ = [
    "DEVELOPMENT_ANDCG_MIN_DELTA",
    "DEVELOPMENT_DUPLICATE_MAX_DELTA",
    "DEVELOPMENT_EVIDENCE_MIN_DELTA",
    "DEVELOPMENT_SUCCESS_MIN_DELTA",
    "E2_B_MIN_INCREMENT_OVER_E2_A",
    "LOCKED_SECONDARY_FAMILY",
    "ClaimKind",
    "ClaimSupportReleaseV1",
    "ConfirmatoryValidity",
    "DevelopmentFusionGateReleaseV1",
    "DevelopmentGateDecisionV1",
    "DevelopmentMetricRowV1",
    "DevelopmentPromotionReleaseV1",
    "E2SelectionReason",
    "FormalVerifierAttestationRefV1",
    "FusionConfigurationReleaseV1",
    "LifecycleArtifactType",
    "LockedAnnotationUnsealReleaseV1",
    "LockedLabelSealV1",
    "LockedPrimaryResultV1",
    "LockedSecondaryHypothesis",
    "LockedSecondaryResultV1",
    "LockedTestAuthorizationReleaseV1",
    "LockedUnsealLedgerReleaseV1",
    "ProtocolDeviationReleaseV1",
    "ProtocolDeviationSeverity",
    "PublicBenchmarkProjectionV1",
    "PublicBenchmarkResultReleaseV1",
    "PublicLimitationCodeV1",
    "PublicReleaseAuthorizationV1",
    "ReleaseAuthorityBindingV1",
    "ReleaseAuthorityPolicyV1",
    "ReleaseAuthorityRoleV1",
    "ReleaseControlAttestationV1",
    "ReleaseControlKindV1",
    "ScientificReviewDecision",
    "ScientificReviewReleaseV1",
    "ScientificReviewerAttestationV1",
    "ScientificReviewerDecision",
    "ScientificReviewerIdentityAttestationV1",
    "SecondaryApplicability",
    "SupportedClaimV1",
    "SystemConfigurationRefV1",
    "TypedArtifactRefV1",
    "VerifiedPayloadKind",
    "append_locked_unseal_ledger_v1",
    "assert_claim_support_release_exact_replay_v1",
    "assert_development_fusion_gate_release_exact_replay_v1",
    "assert_development_metric_row_exact_replay_v1",
    "assert_development_promotion_release_exact_replay_v1",
    "assert_fusion_configuration_release_exact_replay_v1",
    "assert_locked_annotation_unseal_exact_replay_v1",
    "assert_locked_label_seal_exact_replay_v1",
    "assert_locked_test_authorization_exact_replay_v1",
    "assert_locked_unseal_ledger_exact_replay_v1",
    "assert_protocol_deviation_exact_replay_v1",
    "assert_public_benchmark_projection_exact_replay_v1",
    "assert_public_benchmark_result_release_exact_replay_v1",
    "assert_public_release_authorization_exact_replay_v1",
    "assert_release_control_attestation_exact_replay_v1",
    "assert_scientific_review_release_exact_replay_v1",
    "assert_scientific_reviewer_attestation_exact_replay_v1",
    "assert_scientific_reviewer_identity_attestation_exact_replay_v1",
    "build_claim_support_release_v1",
    "build_development_fusion_gate_release_v1",
    "build_development_metric_row_v1",
    "build_development_promotion_release_v1",
    "build_fusion_configuration_release_v1",
    "build_locked_annotation_unseal_release_v1",
    "build_locked_label_seal_v1",
    "build_locked_primary_result_v1",
    "build_locked_secondary_family_v1",
    "build_locked_test_authorization_release_v1",
    "build_locked_unseal_ledger_genesis_v1",
    "build_protocol_deviation_release_v1",
    "build_public_benchmark_projection_v1",
    "build_public_benchmark_result_release_v1",
    "build_public_release_authorization_v1",
    "build_release_authority_policy_v1",
    "build_release_control_attestation_v1",
    "build_scientific_review_release_v1",
    "build_scientific_reviewer_attestation_v1",
    "build_scientific_reviewer_identity_attestation_v1",
    "locked_unseal_ledger_ref_v1",
    "supported_claim_v1",
    "typed_artifact_ref_v1",
]
