"""Strict public and persistence contracts for Materials Gateway v1.

These DTOs deliberately model an inspiration *companion* run instead of adding
another orchestrator stage.  They contain no arbitrary paths, code, stage DAGs,
or open-ended action dictionaries.
"""

from __future__ import annotations

import hashlib
import json
from enum import StrEnum
from typing import Annotated, Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    TypeAdapter,
    field_validator,
    model_validator,
)


MATERIALS_GATEWAY_VERSION = "materials-gateway-v1"

Identifier = Annotated[
    str,
    Field(
        min_length=1,
        max_length=128,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$",
    ),
]
Sha256 = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
ShortText = Annotated[str, Field(min_length=1, max_length=512)]
LongText = Annotated[str, Field(min_length=1, max_length=4_000)]
ChemicalSymbol = Annotated[str, Field(pattern=r"^[A-Z][a-z]?$", max_length=2)]


class StrictGatewayModel(BaseModel):
    """Immutable JSON contract rejecting unknown fields and non-finite values."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
        allow_inf_nan=False,
        str_strip_whitespace=True,
        validate_default=True,
    )


def canonical_json_bytes(value: Any) -> bytes:
    """Serialize a JSON-compatible value deterministically."""

    if isinstance(value, BaseModel):
        value = value.model_dump(mode="json")

    def json_default(item: Any) -> Any:
        if isinstance(item, BaseModel):
            return item.model_dump(mode="json")
        if isinstance(item, StrEnum):
            return item.value
        raise TypeError(f"value of type {type(item).__name__} is not JSON serializable")

    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
        default=json_default,
    ).encode("utf-8")


def canonical_sha256(value: Any) -> str:
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


class InspirationBudgetV1(StrictGatewayModel):
    """Small, bounded Gateway-facing budget rather than a backend stage DAG."""

    max_search_requests: Annotated[int, Field(ge=0, le=64)] = 8
    max_unique_documents: Annotated[int, Field(ge=0, le=2_000)] = 24
    max_passages: Annotated[int, Field(ge=0, le=2_000)] = 36
    max_model_calls: Annotated[int, Field(ge=0, le=64)] = 0
    max_walltime_seconds: Annotated[int, Field(ge=1, le=3_600)] = 300
    allow_full_pdf: Literal[False] = False
    allow_expensive_computation: Literal[False] = False

    @model_validator(mode="after")
    def validate_search_budget(self) -> InspirationBudgetV1:
        if self.max_search_requests == 0 and self.max_unique_documents != 0:
            raise ValueError(
                "zero search requests requires zero unique-document budget"
            )
        if self.max_unique_documents == 0 and self.max_passages != 0:
            raise ValueError(
                "zero unique documents requires zero passage budget"
            )
        return self


class InspirationConstraintsV1(StrictGatewayModel):
    """Bounded intent constraints accepted by ``materials_inspiration_run``."""

    required_elements: Annotated[tuple[ChemicalSymbol, ...], Field(max_length=118)] = ()
    excluded_elements: Annotated[tuple[ChemicalSymbol, ...], Field(max_length=118)] = ()
    material_classes: Annotated[tuple[ShortText, ...], Field(max_length=32)] = ()
    dimensionality: Literal["0D", "1D", "2D", "3D", "mixed", "unspecified"] = (
        "unspecified"
    )
    target_features: Annotated[tuple[ShortText, ...], Field(max_length=32)] = ()
    top_k: Annotated[int, Field(ge=1, le=32)] = 5
    require_diverse_routes: bool = True
    budget: InspirationBudgetV1 = Field(default_factory=InspirationBudgetV1)

    @field_validator(
        "required_elements",
        "excluded_elements",
        "material_classes",
        "target_features",
    )
    @classmethod
    def normalize_set_like_fields(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(set(value)) != len(value):
            raise ValueError("constraint collections must not contain duplicates")
        return tuple(sorted(value))

    @model_validator(mode="after")
    def validate_element_constraints(self) -> InspirationConstraintsV1:
        overlap = set(self.required_elements) & set(self.excluded_elements)
        if overlap:
            raise ValueError(
                "required and excluded elements overlap: "
                + ", ".join(sorted(overlap))
            )
        return self


class InspirationRunRequestV1(StrictGatewayModel):
    schema_version: Literal["materials-gateway-v1"] = MATERIALS_GATEWAY_VERSION
    capability: Literal["inspiration_companion"] = "inspiration_companion"
    submission_id: Identifier
    goal: LongText
    constraints: InspirationConstraintsV1

    @field_validator("goal", mode="before")
    @classmethod
    def normalize_goal(cls, value: Any) -> Any:
        if isinstance(value, str):
            return " ".join(value.split())
        return value


def inspiration_request_sha256(request: InspirationRunRequestV1) -> str:
    """Hash the canonical semantic payload bound to ``submission_id``."""

    return canonical_sha256(
        {
            "schema_version": request.schema_version,
            "capability": request.capability,
            "goal": request.goal,
            "constraints": request.constraints,
        }
    )


def inspiration_run_id(submission_id: str) -> str:
    """Derive the stable companion run ID from the caller's submission ID."""

    digest = hashlib.sha256(submission_id.encode("utf-8")).hexdigest()[:24]
    return f"inspiration-{digest}"


class ActionKind(StrEnum):
    ANSWER = "answer"
    APPROVE = "approve"
    REJECT = "reject"
    RESUME = "resume"
    RETRY = "retry"
    CANCEL = "cancel"


class ClarificationInteractionV1(StrictGatewayModel):
    kind: Literal["clarification"] = "clarification"
    interaction_id: Identifier
    question: LongText
    allowed_actions: tuple[Literal["answer"], Literal["cancel"]] = (
        "answer",
        "cancel",
    )

    @field_validator("allowed_actions")
    @classmethod
    def validate_allowed_actions(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if value != ("answer", "cancel"):
            raise ValueError("clarification actions are frozen")
        return value


class ApprovalInteractionV1(StrictGatewayModel):
    kind: Literal["approval"] = "approval"
    interaction_id: Identifier
    approval_kind: Literal["requirement_freeze", "expensive_computation"]
    prompt: LongText
    input_sha256: Sha256
    execution_manifest_sha256: Sha256 | None = None
    allowed_actions: tuple[
        Literal["approve"], Literal["reject"], Literal["cancel"]
    ] = ("approve", "reject", "cancel")

    @field_validator("allowed_actions")
    @classmethod
    def validate_allowed_actions(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if value != ("approve", "reject", "cancel"):
            raise ValueError("approval actions are frozen")
        return value

    @model_validator(mode="after")
    def validate_execution_manifest_binding(self) -> ApprovalInteractionV1:
        if (
            self.execution_manifest_sha256 is not None
            and self.execution_manifest_sha256 != self.input_sha256
        ):
            raise ValueError(
                "execution manifest SHA-256 must equal the approved input SHA-256"
            )
        if (
            self.approval_kind != "requirement_freeze"
            and self.execution_manifest_sha256 is not None
        ):
            raise ValueError(
                "execution manifest SHA-256 is only valid for requirement freeze"
            )
        return self


class RetryInteractionV1(StrictGatewayModel):
    kind: Literal["retry"] = "retry"
    interaction_id: Identifier
    prompt: LongText
    sensitive: Literal[True] = True
    allowed_actions: tuple[Literal["retry"], Literal["cancel"]] = (
        "retry",
        "cancel",
    )

    @field_validator("allowed_actions")
    @classmethod
    def validate_allowed_actions(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if value != ("retry", "cancel"):
            raise ValueError("retry actions are frozen")
        return value


class ResumeInteractionV1(StrictGatewayModel):
    kind: Literal["resume"] = "resume"
    interaction_id: Identifier
    prompt: LongText
    allowed_actions: tuple[Literal["resume"], Literal["cancel"]] = (
        "resume",
        "cancel",
    )

    @field_validator("allowed_actions")
    @classmethod
    def validate_allowed_actions(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if value != ("resume", "cancel"):
            raise ValueError("resume actions are frozen")
        return value


InteractionV1 = Annotated[
    ClarificationInteractionV1
    | ApprovalInteractionV1
    | RetryInteractionV1
    | ResumeInteractionV1,
    Field(discriminator="kind"),
]
INTERACTION_ADAPTER = TypeAdapter(InteractionV1)


class AnswerActionV1(StrictGatewayModel):
    kind: Literal["answer"] = "answer"
    interaction_id: Identifier
    answer: LongText


class ApproveActionV1(StrictGatewayModel):
    kind: Literal["approve"] = "approve"
    interaction_id: Identifier
    confirmed_by_user: Literal[True]


class RejectActionV1(StrictGatewayModel):
    kind: Literal["reject"] = "reject"
    interaction_id: Identifier
    confirmed_by_user: Literal[True]
    reason: ShortText | None = None


class ResumeActionV1(StrictGatewayModel):
    kind: Literal["resume"] = "resume"
    interaction_id: Identifier


class RetryActionV1(StrictGatewayModel):
    kind: Literal["retry"] = "retry"
    interaction_id: Identifier
    confirmed_by_user: Literal[True]


class CancelActionV1(StrictGatewayModel):
    kind: Literal["cancel"] = "cancel"
    interaction_id: Identifier
    confirmed_by_user: Literal[True]


RunActionV1 = Annotated[
    AnswerActionV1
    | ApproveActionV1
    | RejectActionV1
    | ResumeActionV1
    | RetryActionV1
    | CancelActionV1,
    Field(discriminator="kind"),
]
RUN_ACTION_ADAPTER = TypeAdapter(RunActionV1)


class MaterialsRunGetRequestV1(StrictGatewayModel):
    schema_version: Literal["materials-gateway-v1"] = MATERIALS_GATEWAY_VERSION
    run_id: Identifier


class MaterialsRunActRequestV1(StrictGatewayModel):
    schema_version: Literal["materials-gateway-v1"] = MATERIALS_GATEWAY_VERSION
    run_id: Identifier
    action: RunActionV1


class MaterialsResultGetRequestV1(StrictGatewayModel):
    schema_version: Literal["materials-gateway-v1"] = MATERIALS_GATEWAY_VERSION
    run_id: Identifier


class RunningStateV1(StrictGatewayModel):
    status: Literal["RUNNING"] = "RUNNING"
    progress_percent: Annotated[int, Field(ge=0, le=100)] = 0
    message: ShortText | None = None


class InteractionRequiredStateV1(StrictGatewayModel):
    status: Literal["INTERACTION_REQUIRED"] = "INTERACTION_REQUIRED"
    interaction: InteractionV1


class SucceededStateV1(StrictGatewayModel):
    status: Literal["SUCCEEDED"] = "SUCCEEDED"
    report_uri: Annotated[str, Field(min_length=12, max_length=512)]
    authoritative_sha256: Sha256
    result_sha256: Sha256

    @field_validator("report_uri")
    @classmethod
    def validate_report_uri(cls, value: str) -> str:
        return validate_artifact_uri(value)


class PartialStateV1(StrictGatewayModel):
    status: Literal["PARTIAL"] = "PARTIAL"
    report_uri: Annotated[str, Field(min_length=12, max_length=512)]
    authoritative_sha256: Sha256
    result_sha256: Sha256
    warnings: Annotated[tuple[ShortText, ...], Field(min_length=1, max_length=32)]

    @field_validator("report_uri")
    @classmethod
    def validate_report_uri(cls, value: str) -> str:
        return validate_artifact_uri(value)


class FailedStateV1(StrictGatewayModel):
    status: Literal["FAILED"] = "FAILED"
    public_error_code: Identifier
    public_message: ShortText
    retryable: bool = False


class CancelledStateV1(StrictGatewayModel):
    status: Literal["CANCELLED"] = "CANCELLED"
    reason: ShortText


RunStateV1 = Annotated[
    RunningStateV1
    | InteractionRequiredStateV1
    | SucceededStateV1
    | PartialStateV1
    | FailedStateV1
    | CancelledStateV1,
    Field(discriminator="status"),
]
RUN_STATE_ADAPTER = TypeAdapter(RunStateV1)


def validate_artifact_uri(value: str) -> str:
    prefix = "artifact://"
    if not value.startswith(prefix):
        raise ValueError("artifact URI must start with artifact://")
    relative = value.removeprefix(prefix)
    if (
        not relative
        or relative.startswith("/")
        or "\\" in relative
        or "?" in relative
        or "#" in relative
        or any(part in {"", ".", ".."} for part in relative.split("/"))
    ):
        raise ValueError("artifact URI must contain a safe relative path")
    return value


class EvidenceReferenceV1(StrictGatewayModel):
    document_id: Identifier
    passage_ids: Annotated[tuple[Identifier, ...], Field(min_length=1, max_length=16)]

    @field_validator("passage_ids")
    @classmethod
    def validate_passage_ids(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(set(value)) != len(value):
            raise ValueError("passage IDs must be unique")
        if value != tuple(sorted(value)):
            raise ValueError("passage IDs must be sorted")
        return value


class ReadableEvidenceV1(StrictGatewayModel):
    """One verified, bounded source excerpt safe to return through the Gateway."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
        allow_inf_nan=False,
        str_strip_whitespace=False,
        validate_default=True,
    )

    document_id: Identifier
    passage_id: Identifier
    source_title: Annotated[str, Field(min_length=1, max_length=1_000)]
    published_year: Annotated[int, Field(ge=1600, le=2200)] | None = None
    doi: Annotated[str, Field(min_length=6, max_length=256)] | None = None
    canonical_url: Annotated[str, Field(min_length=8, max_length=2_048)] | None = None
    relations: Annotated[
        tuple[Literal["SUPPORT", "COUNTER", "CONTEXT"], ...],
        Field(min_length=1, max_length=3),
    ]
    claim_summaries: Annotated[
        tuple[LongText, ...], Field(min_length=1, max_length=8)
    ]
    excerpt: Annotated[str, Field(min_length=1, max_length=1_000)]
    excerpt_truncated: bool
    source_passage_sha256: Sha256

    @field_validator("relations", "claim_summaries")
    @classmethod
    def validate_sorted_unique_values(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if value != tuple(sorted(set(value))):
            raise ValueError("readable evidence values must be sorted and unique")
        return value

    @field_validator("canonical_url")
    @classmethod
    def validate_canonical_url(cls, value: str | None) -> str | None:
        if value is not None and not value.startswith(("https://", "http://")):
            raise ValueError("canonical URL must use HTTP(S)")
        return value


class ReadableEvidenceSetV1(StrictGatewayModel):
    """Deterministically capped evidence excerpts for selected candidates."""

    items: Annotated[tuple[ReadableEvidenceV1, ...], Field(max_length=32)] = ()
    total_available: Annotated[int, Field(ge=0, le=2_048)] = 0
    truncated: bool = False

    @model_validator(mode="after")
    def validate_count(self) -> ReadableEvidenceSetV1:
        passage_ids = tuple(item.passage_id for item in self.items)
        if len(set(passage_ids)) != len(passage_ids):
            raise ValueError("readable evidence passage IDs must be unique")
        if passage_ids != tuple(sorted(passage_ids)):
            raise ValueError("readable evidence must be passage-ID sorted")
        if self.total_available < len(self.items):
            raise ValueError("readable evidence count exceeds total available")
        if self.truncated != (self.total_available > len(self.items)):
            raise ValueError("readable evidence truncation flag is inconsistent")
        return self


class CandidateSummaryV1(StrictGatewayModel):
    candidate_id: Identifier
    parent_candidate_id: Identifier
    deterministic_transformation: LongText
    shared_invariant: LongText
    bridge_domain: ShortText
    evidence_document_ids: Annotated[
        tuple[Identifier, ...], Field(min_length=1, max_length=32)
    ]
    evidence_passage_ids: Annotated[
        tuple[Identifier, ...], Field(min_length=1, max_length=64)
    ]
    structural_status: Literal["STRUCTURE_VALID"] = "STRUCTURE_VALID"
    target_property_status: Literal["UNKNOWN"] = "UNKNOWN"
    failure_conditions: Annotated[
        tuple[ShortText, ...], Field(min_length=1, max_length=16)
    ]
    cheapest_falsification_step: LongText

    @field_validator("evidence_document_ids", "evidence_passage_ids")
    @classmethod
    def validate_evidence_ids(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(set(value)) != len(value):
            raise ValueError("candidate evidence IDs must be unique")
        if value != tuple(sorted(value)):
            raise ValueError("candidate evidence IDs must be sorted")
        return value


class CostLedgerProjectionV1(StrictGatewayModel):
    search_requests: Annotated[int, Field(ge=0)] = 0
    search_response_bytes: Annotated[int, Field(ge=0)] = 0
    fetched_documents: Annotated[int, Field(ge=0)] = 0
    extracted_passages: Annotated[int, Field(ge=0)] = 0
    vectorized_passages: Annotated[int, Field(ge=0)] = 0
    model_calls: Annotated[int, Field(ge=0)] = 0
    input_tokens: Annotated[int, Field(ge=0)] = 0
    output_tokens: Annotated[int, Field(ge=0)] = 0
    walltime_ms: Annotated[int, Field(ge=0)] = 0

    @model_validator(mode="after")
    def validate_counts(self) -> CostLedgerProjectionV1:
        if self.search_requests == 0 and self.search_response_bytes != 0:
            raise ValueError("zero search requests require zero search bytes")
        if self.vectorized_passages > self.extracted_passages:
            raise ValueError("vectorized passages cannot exceed extracted passages")
        if self.model_calls == 0 and (self.input_tokens or self.output_tokens):
            raise ValueError("zero model calls require zero model token counts")
        return self


class InspirationBundleSummaryV1(StrictGatewayModel):
    outcome: Literal["SUCCEEDED", "SCIENTIFIC_NO_MATCH"]
    selected_candidates: Annotated[
        tuple[CandidateSummaryV1, ...], Field(max_length=32)
    ] = ()
    limitations: Annotated[tuple[ShortText, ...], Field(min_length=1, max_length=32)]
    next_validation_steps: Annotated[
        tuple[ShortText, ...], Field(min_length=1, max_length=32)
    ]
    scope_statement: Literal["STRUCTURE_PROPOSALS_REQUIRE_DOWNSTREAM_VALIDATION"] = (
        "STRUCTURE_PROPOSALS_REQUIRE_DOWNSTREAM_VALIDATION"
    )
    scientific_conclusion: Literal[False] = False

    @model_validator(mode="after")
    def validate_outcome(self) -> InspirationBundleSummaryV1:
        if self.outcome == "SUCCEEDED" and not self.selected_candidates:
            raise ValueError("successful bundle requires at least one candidate")
        if self.outcome == "SCIENTIFIC_NO_MATCH" and self.selected_candidates:
            raise ValueError("no-match bundle cannot contain candidates")
        candidate_ids = tuple(item.candidate_id for item in self.selected_candidates)
        if len(set(candidate_ids)) != len(candidate_ids):
            raise ValueError("selected candidate IDs must be unique")
        return self


class ArtifactReferenceV1(StrictGatewayModel):
    """One immutable artifact included in a terminal result closure."""

    uri: Annotated[str, Field(min_length=12, max_length=512)]
    sha256: Sha256
    size_bytes: Annotated[int, Field(ge=0, le=100_000_000)]
    media_type: Annotated[str, Field(min_length=1, max_length=128)]

    @field_validator("uri")
    @classmethod
    def validate_uri(cls, value: str) -> str:
        return validate_artifact_uri(value)


def artifact_closure_sha256(
    *,
    stage_result: ArtifactReferenceV1,
    artifacts: tuple[ArtifactReferenceV1, ...],
) -> str:
    """Hash the complete, ordered terminal artifact pointer set."""

    if not isinstance(stage_result, ArtifactReferenceV1):
        raise TypeError("stage_result must be an ArtifactReferenceV1")
    if not isinstance(artifacts, tuple) or any(
        not isinstance(item, ArtifactReferenceV1) for item in artifacts
    ):
        raise TypeError("artifacts must be a tuple of ArtifactReferenceV1 values")
    return canonical_sha256(
        {
            "artifacts": artifacts,
            "schema_version": "materials-artifact-closure-v1",
            "stage_result": stage_result,
        }
    )


class ArtifactClosureV1(StrictGatewayModel):
    """Bounded full closure used by online terminal-result verification."""

    schema_version: Literal["materials-artifact-closure-v1"] = (
        "materials-artifact-closure-v1"
    )
    stage_result: ArtifactReferenceV1
    artifacts: Annotated[
        tuple[ArtifactReferenceV1, ...], Field(min_length=1, max_length=256)
    ]
    closure_sha256: Sha256

    @model_validator(mode="after")
    def validate_closure(self) -> ArtifactClosureV1:
        uris = tuple(item.uri for item in self.artifacts)
        if uris != tuple(sorted(uris)):
            raise ValueError("artifact closure entries must be URI-sorted")
        if len(set(uris)) != len(uris):
            raise ValueError("artifact closure entries must be unique")
        if self.stage_result.uri in set(uris):
            raise ValueError("stage result must not be duplicated in closure entries")
        expected = artifact_closure_sha256(
            stage_result=self.stage_result,
            artifacts=self.artifacts,
        )
        if self.closure_sha256 != expected:
            raise ValueError("artifact closure SHA-256 does not match its entries")
        return self


class GatewayResultRecordV1(StrictGatewayModel):
    """Persisted bounded result projection prior to artifact verification."""

    schema_version: Literal["materials-gateway-v1"] = MATERIALS_GATEWAY_VERSION
    run_id: Identifier
    report_uri: Annotated[str, Field(min_length=12, max_length=512)]
    authoritative_sha256: Sha256
    bundle: InspirationBundleSummaryV1
    evidence_lineage: Annotated[
        tuple[EvidenceReferenceV1, ...], Field(max_length=256)
    ] = ()
    readable_evidence: ReadableEvidenceSetV1 = Field(
        default_factory=ReadableEvidenceSetV1
    )
    validation_boundaries: Annotated[
        tuple[ShortText, ...], Field(min_length=1, max_length=32)
    ]
    cost_ledger: CostLedgerProjectionV1 = Field(
        default_factory=CostLedgerProjectionV1
    )
    artifact_closure: ArtifactClosureV1 | None = None

    @field_validator("report_uri")
    @classmethod
    def validate_report_uri(cls, value: str) -> str:
        return validate_artifact_uri(value)

    @model_validator(mode="after")
    def validate_evidence_closure(self) -> GatewayResultRecordV1:
        document_ids = tuple(item.document_id for item in self.evidence_lineage)
        if len(set(document_ids)) != len(document_ids):
            raise ValueError("evidence lineage document IDs must be unique")
        available_documents = set(document_ids)
        available_passages = {
            passage_id
            for item in self.evidence_lineage
            for passage_id in item.passage_ids
        }
        for candidate in self.bundle.selected_candidates:
            missing_documents = set(candidate.evidence_document_ids) - available_documents
            missing_passages = set(candidate.evidence_passage_ids) - available_passages
            if missing_documents or missing_passages:
                raise ValueError("candidate evidence does not close over evidence lineage")
        readable_passage_ids = {
            item.passage_id for item in self.readable_evidence.items
        }
        if not readable_passage_ids.issubset(available_passages):
            raise ValueError("readable evidence does not close over evidence lineage")
        if self.artifact_closure is not None:
            report_matches = tuple(
                item
                for item in self.artifact_closure.artifacts
                if item.uri == self.report_uri
            )
            if len(report_matches) != 1:
                raise ValueError("artifact closure must contain the bound report once")
            if report_matches[0].sha256 != self.authoritative_sha256:
                raise ValueError("artifact closure report SHA-256 differs from result")
        return self


def gateway_result_sha256(result: GatewayResultRecordV1) -> str:
    """Hash the canonical terminal result DTO persisted by the Gateway.

    Results created before readable evidence and artifact closure were added did
    not contain those two fields.  Their terminal state already commits to the
    canonical hash of the original field set, so silently injecting default
    values while reading them would make an untampered historical run
    unreadable.  Preserve that exact legacy projection only when both new
    fields are semantically empty.  Any readable evidence or closure switches
    to the complete current projection and therefore remains hash-bound.
    """

    if not isinstance(result, GatewayResultRecordV1):
        raise TypeError("result must be GatewayResultRecordV1")
    if (
        result.artifact_closure is None
        and result.readable_evidence == ReadableEvidenceSetV1()
    ):
        legacy_fields = (
            "schema_version",
            "run_id",
            "report_uri",
            "authoritative_sha256",
            "bundle",
            "evidence_lineage",
            "validation_boundaries",
            "cost_ledger",
        )
        return canonical_sha256(
            {field_name: getattr(result, field_name) for field_name in legacy_fields}
        )
    return canonical_sha256(
        {
            field_name: getattr(result, field_name)
            for field_name in GatewayResultRecordV1.model_fields
        }
    )


class ReadableReportV1(StrictGatewayModel):
    """A verified report prefix with explicit full-content identity and truncation."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
        allow_inf_nan=False,
        str_strip_whitespace=False,
        validate_default=True,
    )

    media_type: Literal["text/markdown"] = "text/markdown"
    content: Annotated[str, Field(min_length=1, max_length=32_000)]
    returned_char_count: Annotated[int, Field(ge=1, le=32_000)]
    original_char_count: Annotated[int, Field(ge=1)]
    original_size_bytes: Annotated[int, Field(ge=1, le=100_000_000)]
    full_content_sha256: Sha256
    truncated: bool

    @model_validator(mode="after")
    def validate_lengths(self) -> ReadableReportV1:
        if self.returned_char_count != len(self.content):
            raise ValueError("returned report character count is inconsistent")
        if self.original_char_count < self.returned_char_count:
            raise ValueError("returned report exceeds its original character count")
        if self.truncated != (self.returned_char_count < self.original_char_count):
            raise ValueError("readable report truncation flag is inconsistent")
        return self


class MaterialsResultViewV1(GatewayResultRecordV1):
    """Hash-verified terminal projection returned by ``materials_result_get``."""

    result_sha256: Sha256
    readable_report: ReadableReportV1
    verified: Literal[True] = True

    @model_validator(mode="after")
    def validate_result_sha256(self) -> MaterialsResultViewV1:
        if gateway_result_sha256(self) != self.result_sha256:
            raise ValueError("canonical result SHA-256 does not match the result view")
        if self.readable_report.full_content_sha256 != self.authoritative_sha256:
            raise ValueError("readable report does not match the authoritative report")
        return self


class CompanionTransitionV1(StrictGatewayModel):
    """One adapter transition; service code applies it atomically once."""

    state: RunStateV1
    result: GatewayResultRecordV1 | None = None

    @model_validator(mode="after")
    def validate_result_reference(self) -> CompanionTransitionV1:
        terminal_with_result = isinstance(self.state, (SucceededStateV1, PartialStateV1))
        if self.result is not None and not terminal_with_result:
            raise ValueError("only successful or partial states may carry a result")
        if self.result is not None:
            if self.result.report_uri != self.state.report_uri:
                raise ValueError("state and result report URIs differ")
            if self.result.authoritative_sha256 != self.state.authoritative_sha256:
                raise ValueError("state and result authoritative SHA-256 differ")
            if gateway_result_sha256(self.result) != self.state.result_sha256:
                raise ValueError("state and canonical result SHA-256 differ")
        return self


class GatewayRunRecordV1(StrictGatewayModel):
    """Internal immutable persistence record with optimistic revision."""

    schema_version: Literal["materials-gateway-v1"] = MATERIALS_GATEWAY_VERSION
    run_id: Identifier
    request: InspirationRunRequestV1
    request_sha256: Sha256
    state: RunStateV1
    revision: Annotated[int, Field(ge=0)] = 0

    @model_validator(mode="after")
    def validate_identity(self) -> GatewayRunRecordV1:
        if self.run_id != inspiration_run_id(self.request.submission_id):
            raise ValueError("run ID is not derived from submission ID")
        if self.request_sha256 != inspiration_request_sha256(self.request):
            raise ValueError("request SHA-256 does not match canonical request")
        return self


class MaterialsRunViewV1(StrictGatewayModel):
    """Safe status projection returned by run/start/action methods."""

    schema_version: Literal["materials-gateway-v1"] = MATERIALS_GATEWAY_VERSION
    run_id: Identifier
    submission_id: Identifier
    capability: Literal["inspiration_companion"] = "inspiration_companion"
    request_sha256: Sha256
    state: RunStateV1


def run_view(record: GatewayRunRecordV1) -> MaterialsRunViewV1:
    return MaterialsRunViewV1(
        run_id=record.run_id,
        submission_id=record.request.submission_id,
        request_sha256=record.request_sha256,
        state=record.state,
    )


def terminal_reference(state: RunStateV1) -> tuple[str, str, str] | None:
    if isinstance(state, (SucceededStateV1, PartialStateV1)):
        return state.report_uri, state.authoritative_sha256, state.result_sha256
    return None


def inspiration_report_uri(run_id: str) -> str:
    """Return the only report URI the Gateway may bind for a companion run."""

    if not isinstance(run_id, str) or not run_id or len(run_id) > 128:
        raise ValueError("run_id is invalid")
    return f"artifact://stages/inspiration/{run_id}/report.md"


PUBLIC_GATEWAY_MODELS: tuple[type[StrictGatewayModel], ...] = (
    InspirationBudgetV1,
    InspirationConstraintsV1,
    InspirationRunRequestV1,
    ClarificationInteractionV1,
    ApprovalInteractionV1,
    RetryInteractionV1,
    ResumeInteractionV1,
    AnswerActionV1,
    ApproveActionV1,
    RejectActionV1,
    ResumeActionV1,
    RetryActionV1,
    CancelActionV1,
    MaterialsRunGetRequestV1,
    MaterialsRunActRequestV1,
    MaterialsResultGetRequestV1,
    RunningStateV1,
    InteractionRequiredStateV1,
    SucceededStateV1,
    PartialStateV1,
    FailedStateV1,
    CancelledStateV1,
    EvidenceReferenceV1,
    ReadableEvidenceV1,
    ReadableEvidenceSetV1,
    ArtifactReferenceV1,
    ArtifactClosureV1,
    CandidateSummaryV1,
    CostLedgerProjectionV1,
    InspirationBundleSummaryV1,
    GatewayResultRecordV1,
    ReadableReportV1,
    MaterialsResultViewV1,
    CompanionTransitionV1,
    GatewayRunRecordV1,
    MaterialsRunViewV1,
)
