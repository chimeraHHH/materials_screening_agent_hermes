"""Private human screening for calibration-set structural derivatives.

This leaf contract records an exact, content-addressed human-review procedure.
It does not infer derivative status automatically and none of its artifacts is
a scientific conclusion.  A calibration gate may accept only cases whose
replayed final human judgment is :class:`DerivativeClass.NOT`.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from enum import StrEnum
from typing import Annotated, Iterable, Literal, TypeVar

from pydantic import Field, field_validator, model_validator

from material_agent.inspiration.models import (
    Identifier,
    Sha256,
    ShortText,
    StrictModel,
    canonical_sha256,
    deterministic_id,
)
from material_agent.research.flatband_contracts import FlatBandBenchmarkCaseV1
from material_agent.research.flatband_leakage import (
    MechanismLineageCuratorDeclarationV3,
)


ModelT = TypeVar("ModelT", bound=StrictModel)


def _require_rfc3339(value: str) -> str:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("timestamp must be RFC3339-compatible") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("timestamp must include a UTC offset")
    return value


def _timestamp(value: str) -> datetime:
    return datetime.fromisoformat(_require_rfc3339(value).replace("Z", "+00:00"))


def _build_addressed(
    model_type: type[ModelT],
    *,
    id_field: str,
    sha_field: str,
    prefix: str,
    values: dict[str, object],
) -> ModelT:
    draft = model_type.model_construct(**values)
    digest = canonical_sha256(
        draft.model_dump(mode="python", exclude={id_field, sha_field})
    )
    return model_type.model_validate(
        {
            **values,
            sha_field: digest,
            id_field: deterministic_id(prefix, {sha_field: digest}),
        }
    )


def _assert_addressed(
    model: StrictModel, *, id_field: str, sha_field: str, prefix: str
) -> None:
    semantic = model.model_dump(mode="python", exclude={id_field, sha_field})
    expected_sha256 = canonical_sha256(semantic)
    if getattr(model, sha_field) != expected_sha256:
        raise ValueError(f"{sha_field} does not match semantic content")
    if getattr(model, id_field) != deterministic_id(
        prefix, {sha_field: expected_sha256}
    ):
        raise ValueError(f"{id_field} does not match {sha_field}")


def _revalidate(value: ModelT, model_type: type[ModelT]) -> ModelT:
    return model_type.model_validate(
        value.model_dump(mode="python", round_trip=True)
    )


class DerivativeClass(StrEnum):
    NOT = "NOT"
    VACANCY = "VACANCY"
    INTERCALATION = "INTERCALATION"
    NON_STOICHIOMETRIC = "NON_STOICHIOMETRIC"
    ORDERED_DEFECT = "ORDERED_DEFECT"


class DerivativeSourceEvidenceRefV3(StrictModel):
    """One exact source record selected from one full case."""

    schema_version: Literal["flatband-derivative-source-evidence-ref-v3"] = (
        "flatband-derivative-source-evidence-ref-v3"
    )
    evidence_ref_id: Identifier
    evidence_ref_sha256: Sha256
    case_id: Identifier
    case_sha256: Sha256
    source_id: Identifier
    source_record_id: Identifier
    source_record_raw_sha256: Sha256
    evidence_scope: Literal["HUMAN_DERIVATIVE_SCREENING_SOURCE_RECORD"] = (
        "HUMAN_DERIVATIVE_SCREENING_SOURCE_RECORD"
    )
    scientific_conclusion: Literal[False] = False

    @model_validator(mode="after")
    def validate_ref(self) -> "DerivativeSourceEvidenceRefV3":
        _assert_addressed(
            self,
            id_field="evidence_ref_id",
            sha_field="evidence_ref_sha256",
            prefix="derivative-source-ref-v3",
        )
        return self


class DerivativeScreeningPolicyV3(StrictModel):
    schema_version: Literal["flatband-derivative-screening-policy-v3"] = (
        "flatband-derivative-screening-policy-v3"
    )
    policy_id: Identifier
    policy_sha256: Sha256
    derivative_class_taxonomy: tuple[DerivativeClass, ...]
    review_criteria: Annotated[
        tuple[ShortText, ...], Field(min_length=4, max_length=16)
    ]
    independent_reviewer_count: Literal[2] = 2
    distinct_natural_person_adjudicator_required: Literal[True] = True
    adjudication_required_iff_class_disagreement: Literal[True] = True
    evidence_must_be_nonempty_case_source_subset: Literal[True] = True
    calibration_acceptance_class: Literal[DerivativeClass.NOT] = DerivativeClass.NOT
    human_judgment_only: Literal[True] = True
    automated_truth_claimed: Literal[False] = False
    sealed_at: Annotated[str, Field(min_length=20, max_length=40)]
    private_custody_required: Literal[True] = True
    public_release_allowed: Literal[False] = False
    scientific_conclusion: Literal[False] = False

    @field_validator("sealed_at")
    @classmethod
    def validate_time(cls, value: str) -> str:
        return _require_rfc3339(value)

    @model_validator(mode="after")
    def validate_policy(self) -> "DerivativeScreeningPolicyV3":
        if self.derivative_class_taxonomy != tuple(DerivativeClass):
            raise ValueError("derivative class taxonomy differs from frozen V3 taxonomy")
        if self.review_criteria != tuple(sorted(set(self.review_criteria))):
            raise ValueError("derivative review criteria must be sorted and unique")
        _assert_addressed(
            self,
            id_field="policy_id",
            sha_field="policy_sha256",
            prefix="derivative-policy-v3",
        )
        return self


class DerivativeScreeningReviewerRosterV3(StrictModel):
    schema_version: Literal["flatband-derivative-screening-roster-v3"] = (
        "flatband-derivative-screening-roster-v3"
    )
    roster_id: Identifier
    roster_sha256: Sha256
    policy_id: Identifier
    policy_sha256: Sha256
    reviewers: Annotated[
        tuple[MechanismLineageCuratorDeclarationV3, ...],
        Field(min_length=2, max_length=2),
    ]
    adjudicator: MechanismLineageCuratorDeclarationV3
    independence_review: ShortText
    sealed_at: Annotated[str, Field(min_length=20, max_length=40)]
    private_custody_required: Literal[True] = True
    public_release_allowed: Literal[False] = False
    scientific_conclusion: Literal[False] = False

    @field_validator("sealed_at")
    @classmethod
    def validate_time(cls, value: str) -> str:
        return _require_rfc3339(value)

    @model_validator(mode="after")
    def validate_roster(self) -> "DerivativeScreeningReviewerRosterV3":
        reviewer_ids = tuple(item.curator_id for item in self.reviewers)
        if reviewer_ids != tuple(sorted(set(reviewer_ids))):
            raise ValueError("derivative reviewers must be ID-sorted and unique")
        if self.adjudicator.curator_id in reviewer_ids:
            raise ValueError("derivative adjudicator must be distinct from reviewers")
        people = (*self.reviewers, self.adjudicator)
        for field_name in (
            "opaque_natural_person_ref",
            "natural_person_commitment_sha256",
            "identity_evidence_uri",
            "identity_evidence_sha256",
        ):
            values = tuple(getattr(item, field_name) for item in people)
            if len(values) != len(set(values)):
                raise ValueError(
                    "derivative roster natural-person bindings must be injective"
                )
        _assert_addressed(
            self,
            id_field="roster_id",
            sha_field="roster_sha256",
            prefix="derivative-roster-v3",
        )
        return self


class DerivativeScreeningAssignmentV3(StrictModel):
    schema_version: Literal["flatband-derivative-screening-assignment-v3"] = (
        "flatband-derivative-screening-assignment-v3"
    )
    assignment_id: Identifier
    assignment_sha256: Sha256
    policy_id: Identifier
    policy_sha256: Sha256
    roster_id: Identifier
    roster_sha256: Sha256
    case_id: Identifier
    case_sha256: Sha256
    structure_sha256: Sha256
    evidence_refs: Annotated[
        tuple[DerivativeSourceEvidenceRefV3, ...],
        Field(min_length=1, max_length=16),
    ]
    reviewer_ids: Annotated[tuple[Identifier, ...], Field(min_length=2, max_length=2)]
    adjudicator_id: Identifier
    assigned_at: Annotated[str, Field(min_length=20, max_length=40)]
    human_review_required: Literal[True] = True
    scientific_conclusion: Literal[False] = False

    @field_validator("assigned_at")
    @classmethod
    def validate_time(cls, value: str) -> str:
        return _require_rfc3339(value)

    @model_validator(mode="after")
    def validate_assignment(self) -> "DerivativeScreeningAssignmentV3":
        evidence_keys = tuple(
            (item.source_id, item.source_record_id, item.evidence_ref_id)
            for item in self.evidence_refs
        )
        if evidence_keys != tuple(sorted(set(evidence_keys))):
            raise ValueError("derivative assignment evidence must be sorted and unique")
        if any(
            (item.case_id, item.case_sha256) != (self.case_id, self.case_sha256)
            for item in self.evidence_refs
        ):
            raise ValueError("derivative assignment evidence is cross-wired to another case")
        if self.reviewer_ids != tuple(sorted(set(self.reviewer_ids))):
            raise ValueError("derivative assignment reviewers must be sorted and unique")
        _assert_addressed(
            self,
            id_field="assignment_id",
            sha_field="assignment_sha256",
            prefix="derivative-assignment-v3",
        )
        return self


class DerivativeScreeningRawReviewV3(StrictModel):
    schema_version: Literal["flatband-derivative-screening-raw-review-v3"] = (
        "flatband-derivative-screening-raw-review-v3"
    )
    review_id: Identifier
    review_sha256: Sha256
    policy_id: Identifier
    policy_sha256: Sha256
    roster_id: Identifier
    roster_sha256: Sha256
    assignment_id: Identifier
    assignment_sha256: Sha256
    case_id: Identifier
    case_sha256: Sha256
    reviewer_id: Identifier
    evidence_refs: Annotated[
        tuple[DerivativeSourceEvidenceRefV3, ...],
        Field(min_length=1, max_length=16),
    ]
    derivative_class: DerivativeClass
    criterion_findings: Annotated[
        tuple[ShortText, ...], Field(min_length=4, max_length=16)
    ]
    rationale: ShortText
    reviewed_at: Annotated[str, Field(min_length=20, max_length=40)]
    judgment_origin: Literal["INDEPENDENT_HUMAN_REVIEW"] = (
        "INDEPENDENT_HUMAN_REVIEW"
    )
    automated_truth_claimed: Literal[False] = False
    private_custody_required: Literal[True] = True
    public_release_allowed: Literal[False] = False
    scientific_conclusion: Literal[False] = False

    @field_validator("reviewed_at")
    @classmethod
    def validate_time(cls, value: str) -> str:
        return _require_rfc3339(value)

    @model_validator(mode="after")
    def validate_review(self) -> "DerivativeScreeningRawReviewV3":
        if self.criterion_findings != tuple(sorted(set(self.criterion_findings))):
            raise ValueError("derivative review findings must be sorted and unique")
        _assert_addressed(
            self,
            id_field="review_id",
            sha_field="review_sha256",
            prefix="derivative-review-v3",
        )
        return self


class DerivativeScreeningAdjudicationV3(StrictModel):
    schema_version: Literal["flatband-derivative-screening-adjudication-v3"] = (
        "flatband-derivative-screening-adjudication-v3"
    )
    adjudication_id: Identifier
    adjudication_sha256: Sha256
    policy_id: Identifier
    policy_sha256: Sha256
    roster_id: Identifier
    roster_sha256: Sha256
    assignment_id: Identifier
    assignment_sha256: Sha256
    case_id: Identifier
    case_sha256: Sha256
    review_refs: Annotated[
        tuple[tuple[Identifier, Sha256], ...], Field(min_length=2, max_length=2)
    ]
    adjudicator_id: Identifier
    final_derivative_class: DerivativeClass
    rationale: ShortText
    adjudicated_at: Annotated[str, Field(min_length=20, max_length=40)]
    judgment_origin: Literal["DISTINCT_HUMAN_ADJUDICATION"] = (
        "DISTINCT_HUMAN_ADJUDICATION"
    )
    automated_truth_claimed: Literal[False] = False
    private_custody_required: Literal[True] = True
    public_release_allowed: Literal[False] = False
    scientific_conclusion: Literal[False] = False

    @field_validator("adjudicated_at")
    @classmethod
    def validate_time(cls, value: str) -> str:
        return _require_rfc3339(value)

    @model_validator(mode="after")
    def validate_adjudication(self) -> "DerivativeScreeningAdjudicationV3":
        if self.review_refs != tuple(sorted(set(self.review_refs))):
            raise ValueError("derivative adjudication review refs must be sorted and unique")
        _assert_addressed(
            self,
            id_field="adjudication_id",
            sha_field="adjudication_sha256",
            prefix="derivative-adjudication-v3",
        )
        return self


class DerivativeScreeningDecisionBasis(StrEnum):
    REVIEWER_AGREEMENT = "REVIEWER_AGREEMENT"
    DISTINCT_ADJUDICATION = "DISTINCT_ADJUDICATION"


class DerivativeScreeningFinalJudgmentV3(StrictModel):
    schema_version: Literal["flatband-derivative-screening-final-judgment-v3"] = (
        "flatband-derivative-screening-final-judgment-v3"
    )
    judgment_id: Identifier
    judgment_sha256: Sha256
    assignment_id: Identifier
    assignment_sha256: Sha256
    case_id: Identifier
    case_sha256: Sha256
    review_refs: Annotated[
        tuple[tuple[Identifier, Sha256], ...], Field(min_length=2, max_length=2)
    ]
    adjudication_ref: tuple[Identifier, Sha256] | None = None
    final_derivative_class: DerivativeClass
    decision_basis: DerivativeScreeningDecisionBasis
    resolved_at: Annotated[str, Field(min_length=20, max_length=40)]
    human_judgment_only: Literal[True] = True
    automated_truth_claimed: Literal[False] = False
    private_custody_required: Literal[True] = True
    public_release_allowed: Literal[False] = False
    scientific_conclusion: Literal[False] = False

    @field_validator("resolved_at")
    @classmethod
    def validate_time(cls, value: str) -> str:
        return _require_rfc3339(value)

    @model_validator(mode="after")
    def validate_judgment(self) -> "DerivativeScreeningFinalJudgmentV3":
        if self.review_refs != tuple(sorted(set(self.review_refs))):
            raise ValueError("derivative final review refs must be sorted and unique")
        if (self.adjudication_ref is None) != (
            self.decision_basis is DerivativeScreeningDecisionBasis.REVIEWER_AGREEMENT
        ):
            raise ValueError("derivative final basis differs from adjudication presence")
        _assert_addressed(
            self,
            id_field="judgment_id",
            sha_field="judgment_sha256",
            prefix="derivative-final-v3",
        )
        return self


class DerivativeScreeningReleaseV3(StrictModel):
    """Private exact root for one complete calibration-case universe."""

    schema_version: Literal["flatband-derivative-screening-release-v3"] = (
        "flatband-derivative-screening-release-v3"
    )
    release_id: Identifier
    release_sha256: Sha256
    policy: DerivativeScreeningPolicyV3
    roster: DerivativeScreeningReviewerRosterV3
    case_universe_sha256: Sha256
    cases: Annotated[
        tuple[FlatBandBenchmarkCaseV1, ...], Field(min_length=1, max_length=256)
    ]
    assignments: Annotated[
        tuple[DerivativeScreeningAssignmentV3, ...], Field(min_length=1, max_length=256)
    ]
    raw_reviews: Annotated[
        tuple[DerivativeScreeningRawReviewV3, ...], Field(min_length=2, max_length=512)
    ]
    adjudications: Annotated[
        tuple[DerivativeScreeningAdjudicationV3, ...], Field(max_length=256)
    ] = ()
    final_judgments: Annotated[
        tuple[DerivativeScreeningFinalJudgmentV3, ...],
        Field(min_length=1, max_length=256),
    ]
    assembled_at: Annotated[str, Field(min_length=20, max_length=40)]
    full_case_universe_exactly_covered: Literal[True] = True
    human_screening_not_automated_truth: Literal[True] = True
    private_custody_required: Literal[True] = True
    public_release_allowed: Literal[False] = False
    external_timestamp_attestation_claimed: Literal[False] = False
    scientific_conclusion: Literal[False] = False

    @field_validator("assembled_at")
    @classmethod
    def validate_time(cls, value: str) -> str:
        return _require_rfc3339(value)

    @model_validator(mode="after")
    def validate_release(self) -> "DerivativeScreeningReleaseV3":
        case_ids = tuple(item.case_id for item in self.cases)
        if case_ids != tuple(sorted(set(case_ids))):
            raise ValueError("derivative release cases must be case-ID sorted and unique")
        if self.case_universe_sha256 != canonical_sha256(self.cases):
            raise ValueError("derivative case-universe SHA does not replay")
        assignment_order = tuple(
            (item.case_id, item.assignment_id) for item in self.assignments
        )
        if assignment_order != tuple(sorted(set(assignment_order))) or tuple(
            item.case_id for item in self.assignments
        ) != case_ids:
            raise ValueError("derivative assignments do not exactly cover full cases")
        review_order = tuple(
            (item.case_id, item.reviewer_id, item.review_id)
            for item in self.raw_reviews
        )
        if review_order != tuple(sorted(set(review_order))):
            raise ValueError("derivative raw reviews must be case/reviewer sorted and unique")
        adjudication_order = tuple(
            (item.case_id, item.adjudication_id) for item in self.adjudications
        )
        if adjudication_order != tuple(sorted(set(adjudication_order))) or len(
            {item.case_id for item in self.adjudications}
        ) != len(self.adjudications):
            raise ValueError("derivative adjudications must be case-sorted and unique")
        judgment_order = tuple(
            (item.case_id, item.judgment_id) for item in self.final_judgments
        )
        if judgment_order != tuple(sorted(set(judgment_order))) or tuple(
            item.case_id for item in self.final_judgments
        ) != case_ids:
            raise ValueError("derivative final judgments do not exactly cover full cases")
        if (self.roster.policy_id, self.roster.policy_sha256) != (
            self.policy.policy_id,
            self.policy.policy_sha256,
        ):
            raise ValueError("derivative roster binds a foreign policy")
        _assert_addressed(
            self,
            id_field="release_id",
            sha_field="release_sha256",
            prefix="derivative-release-v3",
        )
        return self


def _evidence_key(ref: DerivativeSourceEvidenceRefV3) -> tuple[str, str, str]:
    return (
        ref.source_id,
        ref.source_record_id,
        ref.source_record_raw_sha256,
    )


def _assert_case_evidence_subset(
    *,
    case: FlatBandBenchmarkCaseV1,
    refs: tuple[DerivativeSourceEvidenceRefV3, ...],
) -> None:
    source_keys = {
        (item.source_id, item.source_record_id, item.raw_sha256)
        for item in case.source_records
        if item.raw_sha256 is not None
    }
    if not refs or any(
        (item.case_id, item.case_sha256) != (case.case_id, case.case_sha256)
        or _evidence_key(item) not in source_keys
        for item in refs
    ):
        raise ValueError("derivative evidence is not a nonempty subset of case sources")


def build_derivative_source_evidence_ref_v3(
    *,
    case: FlatBandBenchmarkCaseV1,
    source_id: str,
    source_record_id: str,
) -> DerivativeSourceEvidenceRefV3:
    full_case = _revalidate(case, FlatBandBenchmarkCaseV1)
    matches = tuple(
        item
        for item in full_case.source_records
        if (item.source_id, item.source_record_id)
        == (source_id, source_record_id)
    )
    if len(matches) != 1 or matches[0].raw_sha256 is None:
        raise ValueError("derivative evidence requires one exact raw-hashed case source")
    record = matches[0]
    return _build_addressed(
        DerivativeSourceEvidenceRefV3,
        id_field="evidence_ref_id",
        sha_field="evidence_ref_sha256",
        prefix="derivative-source-ref-v3",
        values={
            "case_id": full_case.case_id,
            "case_sha256": full_case.case_sha256,
            "source_id": record.source_id,
            "source_record_id": record.source_record_id,
            "source_record_raw_sha256": record.raw_sha256,
        },
    )


def build_derivative_screening_policy_v3(
    *, review_criteria: Iterable[str], sealed_at: str
) -> DerivativeScreeningPolicyV3:
    criteria = tuple(sorted(set(review_criteria)))
    return _build_addressed(
        DerivativeScreeningPolicyV3,
        id_field="policy_id",
        sha_field="policy_sha256",
        prefix="derivative-policy-v3",
        values={
            "derivative_class_taxonomy": tuple(DerivativeClass),
            "review_criteria": criteria,
            "sealed_at": _require_rfc3339(sealed_at),
        },
    )


def build_derivative_screening_reviewer_roster_v3(
    *,
    policy: DerivativeScreeningPolicyV3,
    reviewers: Iterable[MechanismLineageCuratorDeclarationV3],
    adjudicator: MechanismLineageCuratorDeclarationV3,
    independence_review: str,
    sealed_at: str,
) -> DerivativeScreeningReviewerRosterV3:
    policy_value = _revalidate(policy, DerivativeScreeningPolicyV3)
    reviewer_values = tuple(
        sorted(
            (
                MechanismLineageCuratorDeclarationV3.model_validate(
                    item.model_dump(mode="python", round_trip=True)
                )
                for item in reviewers
            ),
            key=lambda item: item.curator_id,
        )
    )
    adjudicator_value = MechanismLineageCuratorDeclarationV3.model_validate(
        adjudicator.model_dump(mode="python", round_trip=True)
    )
    sealed = _require_rfc3339(sealed_at)
    if _timestamp(sealed) <= _timestamp(policy_value.sealed_at):
        raise ValueError("derivative roster must be sealed after policy")
    return _build_addressed(
        DerivativeScreeningReviewerRosterV3,
        id_field="roster_id",
        sha_field="roster_sha256",
        prefix="derivative-roster-v3",
        values={
            "policy_id": policy_value.policy_id,
            "policy_sha256": policy_value.policy_sha256,
            "reviewers": reviewer_values,
            "adjudicator": adjudicator_value,
            "independence_review": independence_review,
            "sealed_at": sealed,
        },
    )


def build_derivative_screening_assignment_v3(
    *,
    policy: DerivativeScreeningPolicyV3,
    roster: DerivativeScreeningReviewerRosterV3,
    case: FlatBandBenchmarkCaseV1,
    evidence_refs: Iterable[DerivativeSourceEvidenceRefV3],
    assigned_at: str,
) -> DerivativeScreeningAssignmentV3:
    policy_value = _revalidate(policy, DerivativeScreeningPolicyV3)
    roster_value = _revalidate(roster, DerivativeScreeningReviewerRosterV3)
    full_case = _revalidate(case, FlatBandBenchmarkCaseV1)
    refs = tuple(
        sorted(
            (
                _revalidate(item, DerivativeSourceEvidenceRefV3)
                for item in evidence_refs
            ),
            key=lambda item: (
                item.source_id,
                item.source_record_id,
                item.evidence_ref_id,
            ),
        )
    )
    if (roster_value.policy_id, roster_value.policy_sha256) != (
        policy_value.policy_id,
        policy_value.policy_sha256,
    ):
        raise ValueError("derivative assignment crosswires policy and roster")
    _assert_case_evidence_subset(case=full_case, refs=refs)
    assigned = _require_rfc3339(assigned_at)
    if _timestamp(assigned) <= _timestamp(roster_value.sealed_at):
        raise ValueError("derivative assignment must follow roster seal")
    return _build_addressed(
        DerivativeScreeningAssignmentV3,
        id_field="assignment_id",
        sha_field="assignment_sha256",
        prefix="derivative-assignment-v3",
        values={
            "policy_id": policy_value.policy_id,
            "policy_sha256": policy_value.policy_sha256,
            "roster_id": roster_value.roster_id,
            "roster_sha256": roster_value.roster_sha256,
            "case_id": full_case.case_id,
            "case_sha256": full_case.case_sha256,
            "structure_sha256": full_case.structure_sha256,
            "evidence_refs": refs,
            "reviewer_ids": tuple(
                item.curator_id for item in roster_value.reviewers
            ),
            "adjudicator_id": roster_value.adjudicator.curator_id,
            "assigned_at": assigned,
        },
    )


def build_derivative_screening_raw_review_v3(
    *,
    policy: DerivativeScreeningPolicyV3,
    roster: DerivativeScreeningReviewerRosterV3,
    assignment: DerivativeScreeningAssignmentV3,
    reviewer_id: str,
    derivative_class: DerivativeClass,
    criterion_findings: Iterable[str],
    rationale: str,
    reviewed_at: str,
) -> DerivativeScreeningRawReviewV3:
    policy_value = _revalidate(policy, DerivativeScreeningPolicyV3)
    roster_value = _revalidate(roster, DerivativeScreeningReviewerRosterV3)
    assignment_value = _revalidate(assignment, DerivativeScreeningAssignmentV3)
    findings = tuple(sorted(set(criterion_findings)))
    if (
        assignment_value.policy_id,
        assignment_value.policy_sha256,
        assignment_value.roster_id,
        assignment_value.roster_sha256,
    ) != (
        policy_value.policy_id,
        policy_value.policy_sha256,
        roster_value.roster_id,
        roster_value.roster_sha256,
    ):
        raise ValueError("derivative raw review crosswires governance")
    if reviewer_id not in {item.curator_id for item in roster_value.reviewers}:
        raise ValueError("derivative raw review comes from outside reviewer roster")
    if set(findings) != set(policy_value.review_criteria):
        raise ValueError("derivative raw review does not cover frozen criteria")
    reviewed = _require_rfc3339(reviewed_at)
    if _timestamp(reviewed) <= _timestamp(assignment_value.assigned_at):
        raise ValueError("derivative raw review must follow assignment")
    return _build_addressed(
        DerivativeScreeningRawReviewV3,
        id_field="review_id",
        sha_field="review_sha256",
        prefix="derivative-review-v3",
        values={
            "policy_id": policy_value.policy_id,
            "policy_sha256": policy_value.policy_sha256,
            "roster_id": roster_value.roster_id,
            "roster_sha256": roster_value.roster_sha256,
            "assignment_id": assignment_value.assignment_id,
            "assignment_sha256": assignment_value.assignment_sha256,
            "case_id": assignment_value.case_id,
            "case_sha256": assignment_value.case_sha256,
            "reviewer_id": reviewer_id,
            "evidence_refs": assignment_value.evidence_refs,
            "derivative_class": derivative_class,
            "criterion_findings": findings,
            "rationale": rationale,
            "reviewed_at": reviewed,
        },
    )


def build_derivative_screening_adjudication_v3(
    *,
    policy: DerivativeScreeningPolicyV3,
    roster: DerivativeScreeningReviewerRosterV3,
    assignment: DerivativeScreeningAssignmentV3,
    reviews: Iterable[DerivativeScreeningRawReviewV3],
    final_derivative_class: DerivativeClass,
    rationale: str,
    adjudicated_at: str,
) -> DerivativeScreeningAdjudicationV3:
    policy_value = _revalidate(policy, DerivativeScreeningPolicyV3)
    roster_value = _revalidate(roster, DerivativeScreeningReviewerRosterV3)
    assignment_value = _revalidate(assignment, DerivativeScreeningAssignmentV3)
    review_values = tuple(
        sorted(
            (_revalidate(item, DerivativeScreeningRawReviewV3) for item in reviews),
            key=lambda item: (item.reviewer_id, item.review_id),
        )
    )
    expected_reviewers = {item.curator_id for item in roster_value.reviewers}
    if len(review_values) != 2 or {
        item.reviewer_id for item in review_values
    } != expected_reviewers:
        raise ValueError("derivative adjudication requires exact dual-reviewer coverage")
    if len({item.derivative_class for item in review_values}) != 2:
        raise ValueError("agreed derivative reviews cannot be adjudicated")
    if any(
        (
            item.policy_id,
            item.policy_sha256,
            item.roster_id,
            item.roster_sha256,
            item.assignment_id,
            item.assignment_sha256,
            item.case_id,
            item.case_sha256,
        )
        != (
            policy_value.policy_id,
            policy_value.policy_sha256,
            roster_value.roster_id,
            roster_value.roster_sha256,
            assignment_value.assignment_id,
            assignment_value.assignment_sha256,
            assignment_value.case_id,
            assignment_value.case_sha256,
        )
        for item in review_values
    ):
        raise ValueError("derivative adjudication crosswires raw reviews")
    adjudicated = _require_rfc3339(adjudicated_at)
    if _timestamp(adjudicated) <= max(
        _timestamp(item.reviewed_at) for item in review_values
    ):
        raise ValueError("derivative adjudication must follow both raw reviews")
    return _build_addressed(
        DerivativeScreeningAdjudicationV3,
        id_field="adjudication_id",
        sha_field="adjudication_sha256",
        prefix="derivative-adjudication-v3",
        values={
            "policy_id": policy_value.policy_id,
            "policy_sha256": policy_value.policy_sha256,
            "roster_id": roster_value.roster_id,
            "roster_sha256": roster_value.roster_sha256,
            "assignment_id": assignment_value.assignment_id,
            "assignment_sha256": assignment_value.assignment_sha256,
            "case_id": assignment_value.case_id,
            "case_sha256": assignment_value.case_sha256,
            "review_refs": tuple(
                sorted((item.review_id, item.review_sha256) for item in review_values)
            ),
            "adjudicator_id": roster_value.adjudicator.curator_id,
            "final_derivative_class": final_derivative_class,
            "rationale": rationale,
            "adjudicated_at": adjudicated,
        },
    )


def _ordered_cases(
    cases: Iterable[FlatBandBenchmarkCaseV1],
) -> tuple[FlatBandBenchmarkCaseV1, ...]:
    ordered = tuple(
        sorted(
            (_revalidate(item, FlatBandBenchmarkCaseV1) for item in cases),
            key=lambda item: item.case_id,
        )
    )
    if not ordered or len({item.case_id for item in ordered}) != len(ordered):
        raise ValueError("derivative screening requires unique nonempty full cases")
    return ordered


def _derive_final_judgments_v3(
    *,
    policy: DerivativeScreeningPolicyV3,
    roster: DerivativeScreeningReviewerRosterV3,
    cases: tuple[FlatBandBenchmarkCaseV1, ...],
    assignments: tuple[DerivativeScreeningAssignmentV3, ...],
    raw_reviews: tuple[DerivativeScreeningRawReviewV3, ...],
    adjudications: tuple[DerivativeScreeningAdjudicationV3, ...],
) -> tuple[DerivativeScreeningFinalJudgmentV3, ...]:
    if (roster.policy_id, roster.policy_sha256) != (
        policy.policy_id,
        policy.policy_sha256,
    ) or _timestamp(roster.sealed_at) <= _timestamp(policy.sealed_at):
        raise ValueError("derivative governance policy/roster closure does not replay")
    case_by_id = {item.case_id: item for item in cases}
    assignment_by_case: dict[str, DerivativeScreeningAssignmentV3] = {}
    expected_reviewer_ids = tuple(item.curator_id for item in roster.reviewers)
    for assignment in assignments:
        case = case_by_id.get(assignment.case_id)
        if case is None or assignment.case_id in assignment_by_case:
            raise ValueError("derivative assignments do not exactly cover case universe")
        if (
            assignment.policy_id,
            assignment.policy_sha256,
            assignment.roster_id,
            assignment.roster_sha256,
            assignment.case_sha256,
            assignment.structure_sha256,
            assignment.reviewer_ids,
            assignment.adjudicator_id,
        ) != (
            policy.policy_id,
            policy.policy_sha256,
            roster.roster_id,
            roster.roster_sha256,
            case.case_sha256,
            case.structure_sha256,
            expected_reviewer_ids,
            roster.adjudicator.curator_id,
        ):
            raise ValueError("derivative assignment crosswires case or governance")
        _assert_case_evidence_subset(case=case, refs=assignment.evidence_refs)
        if _timestamp(assignment.assigned_at) <= _timestamp(roster.sealed_at):
            raise ValueError("derivative assignment predates roster seal")
        assignment_by_case[assignment.case_id] = assignment
    if set(assignment_by_case) != set(case_by_id):
        raise ValueError("derivative assignments do not exactly cover case universe")

    reviews_by_case: dict[str, list[DerivativeScreeningRawReviewV3]] = defaultdict(list)
    criteria = set(policy.review_criteria)
    for review in raw_reviews:
        assignment = assignment_by_case.get(review.case_id)
        if assignment is None:
            raise ValueError("derivative raw review is orphaned from case assignment")
        if (
            review.policy_id,
            review.policy_sha256,
            review.roster_id,
            review.roster_sha256,
            review.assignment_id,
            review.assignment_sha256,
            review.case_sha256,
            review.evidence_refs,
        ) != (
            policy.policy_id,
            policy.policy_sha256,
            roster.roster_id,
            roster.roster_sha256,
            assignment.assignment_id,
            assignment.assignment_sha256,
            assignment.case_sha256,
            assignment.evidence_refs,
        ):
            raise ValueError("derivative raw review crosswires exact assignment evidence")
        if review.reviewer_id not in expected_reviewer_ids:
            raise ValueError("derivative raw review comes from outside reviewer roster")
        if set(review.criterion_findings) != criteria:
            raise ValueError("derivative raw review does not cover frozen criteria")
        if _timestamp(review.reviewed_at) <= _timestamp(assignment.assigned_at):
            raise ValueError("derivative raw review predates assignment")
        reviews_by_case[review.case_id].append(review)
    if set(reviews_by_case) != set(case_by_id):
        raise ValueError("derivative raw reviews do not exactly cover case universe")

    adjudication_by_case: dict[str, DerivativeScreeningAdjudicationV3] = {}
    for adjudication in adjudications:
        if adjudication.case_id in adjudication_by_case:
            raise ValueError("derivative case has multiple adjudications")
        adjudication_by_case[adjudication.case_id] = adjudication

    judgments: list[DerivativeScreeningFinalJudgmentV3] = []
    for case_id in sorted(case_by_id):
        assignment = assignment_by_case[case_id]
        reviews = tuple(
            sorted(
                reviews_by_case[case_id],
                key=lambda item: (item.reviewer_id, item.review_id),
            )
        )
        if len(reviews) != 2 or tuple(
            item.reviewer_id for item in reviews
        ) != expected_reviewer_ids:
            raise ValueError("derivative case requires exactly two assigned raw reviewers")
        review_refs = tuple(
            sorted((item.review_id, item.review_sha256) for item in reviews)
        )
        classes = {item.derivative_class for item in reviews}
        adjudication = adjudication_by_case.get(case_id)
        if len(classes) == 1:
            if adjudication is not None:
                raise ValueError("agreed derivative reviews cannot be adjudicated")
            final_class = next(iter(classes))
            adjudication_ref = None
            basis = DerivativeScreeningDecisionBasis.REVIEWER_AGREEMENT
            resolved_at = max(reviews, key=lambda item: _timestamp(item.reviewed_at)).reviewed_at
        else:
            if adjudication is None:
                raise ValueError("derivative class disagreement requires adjudication")
            if (
                adjudication.policy_id,
                adjudication.policy_sha256,
                adjudication.roster_id,
                adjudication.roster_sha256,
                adjudication.assignment_id,
                adjudication.assignment_sha256,
                adjudication.case_sha256,
                adjudication.review_refs,
                adjudication.adjudicator_id,
            ) != (
                policy.policy_id,
                policy.policy_sha256,
                roster.roster_id,
                roster.roster_sha256,
                assignment.assignment_id,
                assignment.assignment_sha256,
                assignment.case_sha256,
                review_refs,
                roster.adjudicator.curator_id,
            ):
                raise ValueError("derivative adjudication does not bind exact disagreement")
            if _timestamp(adjudication.adjudicated_at) <= max(
                _timestamp(item.reviewed_at) for item in reviews
            ):
                raise ValueError("derivative adjudication does not follow raw reviews")
            final_class = adjudication.final_derivative_class
            adjudication_ref = (
                adjudication.adjudication_id,
                adjudication.adjudication_sha256,
            )
            basis = DerivativeScreeningDecisionBasis.DISTINCT_ADJUDICATION
            resolved_at = adjudication.adjudicated_at
        judgments.append(
            _build_addressed(
                DerivativeScreeningFinalJudgmentV3,
                id_field="judgment_id",
                sha_field="judgment_sha256",
                prefix="derivative-final-v3",
                values={
                    "assignment_id": assignment.assignment_id,
                    "assignment_sha256": assignment.assignment_sha256,
                    "case_id": assignment.case_id,
                    "case_sha256": assignment.case_sha256,
                    "review_refs": review_refs,
                    "adjudication_ref": adjudication_ref,
                    "final_derivative_class": final_class,
                    "decision_basis": basis,
                    "resolved_at": resolved_at,
                },
            )
        )
    if set(adjudication_by_case) - set(case_by_id):
        raise ValueError("derivative adjudication is orphaned from case universe")
    return tuple(judgments)


def _build_derivative_screening_release_v3(
    *,
    policy: DerivativeScreeningPolicyV3,
    roster: DerivativeScreeningReviewerRosterV3,
    cases: Iterable[FlatBandBenchmarkCaseV1],
    assignments: Iterable[DerivativeScreeningAssignmentV3],
    raw_reviews: Iterable[DerivativeScreeningRawReviewV3],
    adjudications: Iterable[DerivativeScreeningAdjudicationV3],
    assembled_at: str,
) -> DerivativeScreeningReleaseV3:
    policy_value = _revalidate(policy, DerivativeScreeningPolicyV3)
    roster_value = _revalidate(roster, DerivativeScreeningReviewerRosterV3)
    case_values = _ordered_cases(cases)
    assignment_values = tuple(
        sorted(
            (_revalidate(item, DerivativeScreeningAssignmentV3) for item in assignments),
            key=lambda item: (item.case_id, item.assignment_id),
        )
    )
    review_values = tuple(
        sorted(
            (_revalidate(item, DerivativeScreeningRawReviewV3) for item in raw_reviews),
            key=lambda item: (item.case_id, item.reviewer_id, item.review_id),
        )
    )
    adjudication_values = tuple(
        sorted(
            (
                _revalidate(item, DerivativeScreeningAdjudicationV3)
                for item in adjudications
            ),
            key=lambda item: (item.case_id, item.adjudication_id),
        )
    )
    judgments = _derive_final_judgments_v3(
        policy=policy_value,
        roster=roster_value,
        cases=case_values,
        assignments=assignment_values,
        raw_reviews=review_values,
        adjudications=adjudication_values,
    )
    assembled = _require_rfc3339(assembled_at)
    if _timestamp(assembled) <= max(
        _timestamp(item.resolved_at) for item in judgments
    ):
        raise ValueError("derivative release must be assembled after all judgments")
    return _build_addressed(
        DerivativeScreeningReleaseV3,
        id_field="release_id",
        sha_field="release_sha256",
        prefix="derivative-release-v3",
        values={
            "policy": policy_value,
            "roster": roster_value,
            "case_universe_sha256": canonical_sha256(case_values),
            "cases": case_values,
            "assignments": assignment_values,
            "raw_reviews": review_values,
            "adjudications": adjudication_values,
            "final_judgments": judgments,
            "assembled_at": assembled,
        },
    )


def build_derivative_screening_release_v3(
    *,
    policy: DerivativeScreeningPolicyV3,
    roster: DerivativeScreeningReviewerRosterV3,
    cases: Iterable[FlatBandBenchmarkCaseV1],
    assignments: Iterable[DerivativeScreeningAssignmentV3],
    raw_reviews: Iterable[DerivativeScreeningRawReviewV3],
    adjudications: Iterable[DerivativeScreeningAdjudicationV3] = (),
    assembled_at: str,
) -> DerivativeScreeningReleaseV3:
    """Build the canonical private release and immediately exact-replay it."""

    release = _build_derivative_screening_release_v3(
        policy=policy,
        roster=roster,
        cases=cases,
        assignments=assignments,
        raw_reviews=raw_reviews,
        adjudications=adjudications,
        assembled_at=assembled_at,
    )
    assert_derivative_screening_release_exact_replay_v3(release)
    return release


def assert_derivative_screening_release_exact_replay_v3(
    release: DerivativeScreeningReleaseV3,
) -> None:
    """Rebuild every final judgment and the complete release address."""

    validated = _revalidate(release, DerivativeScreeningReleaseV3)
    expected = _build_derivative_screening_release_v3(
        policy=validated.policy,
        roster=validated.roster,
        cases=validated.cases,
        assignments=validated.assignments,
        raw_reviews=validated.raw_reviews,
        adjudications=validated.adjudications,
        assembled_at=validated.assembled_at,
    )
    if validated != expected:
        raise ValueError("derivative screening release does not replay exactly")


def assert_calibration_derivative_screening_all_not_derivative_v3(
    release: DerivativeScreeningReleaseV3,
) -> None:
    """Fail closed unless every raw and final human judgment is ``NOT``.

    Adjudication remains part of the immutable audit trail, but it cannot erase a
    reviewer's derivative-risk observation for calibration eligibility.
    """

    assert_derivative_screening_release_exact_replay_v3(release)
    raw_derivative_cases = tuple(
        sorted(
            {
                item.case_id
                for item in release.raw_reviews
                if item.derivative_class is not DerivativeClass.NOT
            }
        )
    )
    final_derivative_cases = tuple(
        sorted(
            {
                item.case_id
                for item in release.final_judgments
                if item.final_derivative_class is not DerivativeClass.NOT
            }
        )
    )
    if raw_derivative_cases or final_derivative_cases:
        raise ValueError(
            "calibration derivative screening contains derivative risk in "
            f"raw review cases={raw_derivative_cases!r} or final judgment "
            f"cases={final_derivative_cases!r}"
        )


__all__ = [
    "DerivativeClass",
    "DerivativeScreeningAdjudicationV3",
    "DerivativeScreeningAssignmentV3",
    "DerivativeScreeningDecisionBasis",
    "DerivativeScreeningFinalJudgmentV3",
    "DerivativeScreeningPolicyV3",
    "DerivativeScreeningRawReviewV3",
    "DerivativeScreeningReleaseV3",
    "DerivativeScreeningReviewerRosterV3",
    "DerivativeSourceEvidenceRefV3",
    "assert_calibration_derivative_screening_all_not_derivative_v3",
    "assert_derivative_screening_release_exact_replay_v3",
    "build_derivative_screening_adjudication_v3",
    "build_derivative_screening_assignment_v3",
    "build_derivative_screening_policy_v3",
    "build_derivative_screening_raw_review_v3",
    "build_derivative_screening_release_v3",
    "build_derivative_screening_reviewer_roster_v3",
    "build_derivative_source_evidence_ref_v3",
]
