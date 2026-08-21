"""Strict, versioned contracts for the inspiration companion capability.

The models in this module describe auditable data products.  They deliberately
do not turn retrieved statements or generated structures into scientific
claims; generated structures always require downstream validation.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import date, datetime
from enum import Enum, StrEnum
from typing import Annotated, Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)

INSPIRATION_INPUT_VERSION = "inspiration-input-v1"
INSPIRATION_QUERY_VERSION = "inspiration-search-query-v1"
INSPIRATION_HIT_VERSION = "inspiration-search-hit-v1"
INSPIRATION_PASSAGE_VERSION = "inspiration-passage-v1"
INSPIRATION_VECTOR_VERSION = "inspiration-passage-vector-v1"
INSPIRATION_EVIDENCE_VERSION = "inspiration-evidence-card-v1"
INSPIRATION_TAG_GRAPH_VERSION = "inspiration-tag-graph-v1"
INSPIRATION_BRIDGE_RULE_VERSION = "inspiration-bridge-rule-v1"
INSPIRATION_BRIDGE_VERSION = "inspiration-bridge-packet-v1"
INSPIRATION_TRANSFORMATION_VERSION = "inspiration-transformation-plan-v1"
INSPIRATION_CANDIDATE_VERSION = "inspiration-candidate-v1"
INSPIRATION_BUNDLE_VERSION = "inspiration-bundle-v1"
INSPIRATION_COST_VERSION = "inspiration-cost-ledger-v1"
INSPIRATION_STAGE_RESULT_VERSION = "inspiration-stage-result-v1"

REQUIRED_STRUCTURE_PASS_CHECK_IDS = (
    "parent_hash_verified",
    "operator_allowed",
    "source_species_present",
    "target_species_valid",
    "requirement_composition",
    "ordered_occupancy",
    "finite_structure",
    "positive_volume",
    "site_count_preserved",
    "lattice_preserved",
    "coordinates_preserved",
    "minimum_distance",
    "canonicalization",
    "canonical_round_trip",
    "dimensionality_and_site_budget",
)
REQUIRED_STRUCTURE_NONFAIL_CHECK_IDS = ("charge_or_oxidation",)


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
Score = Annotated[float, Field(ge=0.0, le=1.0, allow_inf_nan=False)]


class StrictModel(BaseModel):
    """Immutable JSON contract that rejects unknown input fields."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
        allow_inf_nan=False,
        str_strip_whitespace=True,
        validate_default=True,
    )


def _json_default(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json", by_alias=True)
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    raise TypeError(f"value of type {type(value).__name__} is not JSON serializable")


def canonical_json_bytes(value: Any) -> bytes:
    """Serialize a JSON-compatible value in a stable, hashable form."""

    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
        default=_json_default,
    ).encode("utf-8")


def canonical_sha256(value: Any) -> str:
    """Return the SHA-256 of :func:`canonical_json_bytes`."""

    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


_ID_PREFIX = re.compile(r"^[a-z][a-z0-9-]{0,31}$")


def deterministic_id(prefix: str, payload: Any) -> str:
    """Create a stable, compact identifier from a semantic payload."""

    if not _ID_PREFIX.fullmatch(prefix):
        raise ValueError("ID prefix must match ^[a-z][a-z0-9-]{0,31}$")
    return f"{prefix}-{canonical_sha256(payload)[:24]}"


def _duplicates(values: tuple[str, ...]) -> set[str]:
    seen: set[str] = set()
    duplicates: set[str] = set()
    for value in values:
        if value in seen:
            duplicates.add(value)
        else:
            seen.add(value)
    return duplicates


class ArtifactPointerV1(StrictModel):
    uri: Annotated[str, Field(min_length=12, max_length=512)]
    sha256: Sha256
    size_bytes: Annotated[int, Field(ge=0)] | None = None
    media_type: Annotated[str, Field(min_length=1, max_length=128)] | None = None

    @field_validator("uri")
    @classmethod
    def validate_artifact_uri(cls, value: str) -> str:
        prefix = "artifact://"
        if not value.startswith(prefix):
            raise ValueError("artifact URI must start with artifact://")
        path = value.removeprefix(prefix)
        if (
            not path
            or path.startswith("/")
            or "\\" in path
            or "?" in path
            or "#" in path
            or any(part in {"", ".", ".."} for part in path.split("/"))
        ):
            raise ValueError("artifact URI must contain a safe relative path")
        return value


class ComponentSnapshotV1(StrictModel):
    component_id: Identifier
    version: ShortText
    implementation_sha256: Sha256


class ParentCandidateRefV1(StrictModel):
    candidate_id: Identifier
    structure_id: Identifier
    structure_artifact: ArtifactPointerV1
    validation_status: Literal["VALIDATED_PARENT"] = "VALIDATED_PARENT"


class InspirationInputV1(StrictModel):
    schema_version: Literal["inspiration-input-v1"] = INSPIRATION_INPUT_VERSION
    project_id: Identifier
    request_id: Identifier
    run_id: Identifier
    requirement_revision: Annotated[int, Field(ge=1)]
    requirement_artifact: ArtifactPointerV1
    parent_candidates: Annotated[
        tuple[ParentCandidateRefV1, ...], Field(min_length=1, max_length=32)
    ]
    policy_artifact: ArtifactPointerV1
    tag_graph_artifact: ArtifactPointerV1
    transformation_registry_artifact: ArtifactPointerV1
    search_fixture_artifact: ArtifactPointerV1 | None = None
    search_adapter: ComponentSnapshotV1
    vectorizer: ComponentSnapshotV1
    llm_adapter: ComponentSnapshotV1 | None = None

    @model_validator(mode="after")
    def validate_parent_identity(self) -> InspirationInputV1:
        candidate_ids = tuple(item.candidate_id for item in self.parent_candidates)
        structure_ids = tuple(item.structure_id for item in self.parent_candidates)
        if _duplicates(candidate_ids):
            raise ValueError("parent candidate IDs must be unique")
        if _duplicates(structure_ids):
            raise ValueError("parent structure IDs must be unique")
        return self


class SearchQueryKind(StrEnum):
    DIRECT = "DIRECT"
    BRIDGE = "BRIDGE"
    COUNTER = "COUNTER"


class SearchQueryV1(StrictModel):
    schema_version: Literal["inspiration-search-query-v1"] = (
        INSPIRATION_QUERY_VERSION
    )
    query_id: Identifier
    kind: SearchQueryKind
    text: Annotated[str, Field(min_length=3, max_length=512)]
    tag_ids: Annotated[tuple[Identifier, ...], Field(min_length=1, max_length=16)]
    bridge_rule_id: Identifier | None = None

    @model_validator(mode="after")
    def validate_query_origin(self) -> SearchQueryV1:
        if _duplicates(self.tag_ids):
            raise ValueError("query tag IDs must be unique")
        if self.kind in {SearchQueryKind.BRIDGE, SearchQueryKind.COUNTER}:
            if self.bridge_rule_id is None:
                raise ValueError("bridge and counter queries require bridge_rule_id")
        elif self.bridge_rule_id is not None:
            raise ValueError("direct query cannot reference a bridge rule")
        return self


class SearchHitV1(StrictModel):
    schema_version: Literal["inspiration-search-hit-v1"] = INSPIRATION_HIT_VERSION
    hit_id: Identifier
    document_id: Identifier
    provider: Identifier
    provider_record_id: ShortText
    query_ids: Annotated[tuple[Identifier, ...], Field(min_length=1, max_length=32)]
    provider_rank: Annotated[int, Field(ge=1, le=10_000)]
    title: Annotated[str, Field(min_length=1, max_length=1_000)]
    authors: Annotated[tuple[ShortText, ...], Field(max_length=256)] = ()
    published_year: Annotated[int, Field(ge=1600, le=2200)] | None = None
    doi: Annotated[str, Field(min_length=6, max_length=256)] | None = None
    arxiv_id: Annotated[str, Field(min_length=1, max_length=64)] | None = None
    canonical_url: Annotated[str, Field(min_length=8, max_length=2_048)] | None = None
    abstract: Annotated[str, Field(min_length=1, max_length=20_000)] | None = None
    keywords: Annotated[tuple[ShortText, ...], Field(max_length=128)] = ()
    raw_response_artifact: ArtifactPointerV1

    @field_validator("doi")
    @classmethod
    def validate_doi(cls, value: str | None) -> str | None:
        if value is not None and (not value.lower().startswith("10.") or "/" not in value):
            raise ValueError("DOI must use its registrant/prefix form")
        return value.lower() if value is not None else None

    @field_validator("canonical_url")
    @classmethod
    def validate_url(cls, value: str | None) -> str | None:
        if value is not None and not value.startswith(("https://", "http://")):
            raise ValueError("canonical_url must be an HTTP(S) URL")
        return value

    @model_validator(mode="after")
    def validate_collections(self) -> SearchHitV1:
        if _duplicates(self.query_ids):
            raise ValueError("query IDs must be unique")
        if _duplicates(self.keywords):
            raise ValueError("keywords must be unique")
        return self


class PassageLocatorKind(StrEnum):
    API_FIELD = "API_FIELD"
    JSON_PATH = "JSON_PATH"
    JSON_LD = "JSON_LD"
    HTML_META = "HTML_META"
    CSS_SELECTOR = "CSS_SELECTOR"
    JATS_XPATH = "JATS_XPATH"


class PassageLocatorV1(StrictModel):
    kind: PassageLocatorKind
    selector: Annotated[str, Field(min_length=1, max_length=512)]
    section_heading: Annotated[str, Field(min_length=1, max_length=512)] | None = None
    start_offset: Annotated[int, Field(ge=0)] | None = None
    end_offset: Annotated[int, Field(ge=1)] | None = None

    @model_validator(mode="after")
    def validate_offsets(self) -> PassageLocatorV1:
        if (self.start_offset is None) != (self.end_offset is None):
            raise ValueError("passage offsets must be supplied together")
        if (
            self.start_offset is not None
            and self.end_offset is not None
            and self.end_offset <= self.start_offset
        ):
            raise ValueError("end_offset must be greater than start_offset")
        return self


class PassageV1(StrictModel):
    schema_version: Literal["inspiration-passage-v1"] = INSPIRATION_PASSAGE_VERSION
    passage_id: Identifier
    hit_id: Identifier
    document_id: Identifier
    source_artifact: ArtifactPointerV1
    normalizer: ComponentSnapshotV1
    locator: PassageLocatorV1
    text: Annotated[str, Field(min_length=1, max_length=8_000)]
    char_count: Annotated[int, Field(ge=1, le=8_000)]
    estimated_token_count: Annotated[int, Field(ge=1, le=2_000)]
    normalized_text_sha256: Sha256
    matched_tag_ids: Annotated[tuple[Identifier, ...], Field(max_length=32)] = ()
    lexical_score: Score

    @model_validator(mode="after")
    def validate_passage(self) -> PassageV1:
        if self.char_count != len(self.text):
            raise ValueError("char_count must equal the length of text")
        if _duplicates(self.matched_tag_ids):
            raise ValueError("matched tag IDs must be unique")
        return self


class PassageVectorV1(StrictModel):
    schema_version: Literal["inspiration-passage-vector-v1"] = (
        INSPIRATION_VECTOR_VERSION
    )
    passage_id: Identifier
    normalized_text_sha256: Sha256
    vectorizer: ComponentSnapshotV1
    embedding_input_sha256: Sha256
    dimension: Annotated[int, Field(ge=8, le=16_384)]
    vector_artifact: ArtifactPointerV1
    vector_sha256: Sha256
    cache_key_sha256: Sha256


class EvidenceRelation(StrEnum):
    SUPPORT = "SUPPORT"
    COUNTER = "COUNTER"
    CONTEXT = "CONTEXT"


class EvidenceCardV1(StrictModel):
    schema_version: Literal["inspiration-evidence-card-v1"] = (
        INSPIRATION_EVIDENCE_VERSION
    )
    evidence_card_id: Identifier
    relation: EvidenceRelation
    claim_text: LongText
    mechanism_tag_ids: Annotated[
        tuple[Identifier, ...], Field(min_length=1, max_length=16)
    ]
    applicability_conditions: Annotated[
        tuple[ShortText, ...], Field(min_length=1, max_length=16)
    ]
    counterevidence: Annotated[tuple[ShortText, ...], Field(max_length=16)] = ()
    passage_ids: Annotated[tuple[Identifier, ...], Field(min_length=1, max_length=16)]
    evidence_scope: Literal["SOURCE_ASSERTION"] = "SOURCE_ASSERTION"

    @model_validator(mode="after")
    def validate_references(self) -> EvidenceCardV1:
        for label, values in (
            ("mechanism tag IDs", self.mechanism_tag_ids),
            ("applicability conditions", self.applicability_conditions),
            ("counterevidence", self.counterevidence),
            ("passage IDs", self.passage_ids),
        ):
            if _duplicates(values):
                raise ValueError(f"{label} must be unique")
        return self


class TagKind(StrEnum):
    MATERIAL = "MATERIAL"
    PROPERTY = "PROPERTY"
    MECHANISM = "MECHANISM"
    MOTIF = "MOTIF"
    PROCESS = "PROCESS"
    MEASUREMENT = "MEASUREMENT"
    ANALOGY_DOMAIN = "ANALOGY_DOMAIN"


class TagDefinitionV1(StrictModel):
    tag_id: Identifier
    kind: TagKind
    label: ShortText
    description: LongText
    synonyms: Annotated[tuple[ShortText, ...], Field(max_length=32)] = ()
    query_terms: Annotated[tuple[ShortText, ...], Field(min_length=1, max_length=32)]

    @model_validator(mode="after")
    def validate_terms(self) -> TagDefinitionV1:
        if _duplicates(self.synonyms) or _duplicates(self.query_terms):
            raise ValueError("tag synonyms and query terms must each be unique")
        return self


class TagRelation(StrEnum):
    MECHANISM_SUPPORTS_PROPERTY = "MECHANISM_SUPPORTS_PROPERTY"
    DOMAIN_ANALOGY = "DOMAIN_ANALOGY"
    CONTROL_ACTS_ON_MECHANISM = "CONTROL_ACTS_ON_MECHANISM"
    MEASUREMENT_OBSERVES_PROPERTY = "MEASUREMENT_OBSERVES_PROPERTY"
    CONDITION_BREAKS_MECHANISM = "CONDITION_BREAKS_MECHANISM"


class TagEdgeV1(StrictModel):
    source_tag_id: Identifier
    target_tag_id: Identifier
    relation: TagRelation
    evidence_card_ids: Annotated[tuple[Identifier, ...], Field(max_length=32)] = ()

    @model_validator(mode="after")
    def validate_edge(self) -> TagEdgeV1:
        if self.source_tag_id == self.target_tag_id:
            raise ValueError("tag edge cannot be a self-loop")
        if _duplicates(self.evidence_card_ids):
            raise ValueError("edge evidence card IDs must be unique")
        return self


class BridgeRuleV1(StrictModel):
    """Curated cross-domain route used to plan searches before evidence exists."""

    schema_version: Literal["inspiration-bridge-rule-v1"] = (
        INSPIRATION_BRIDGE_RULE_VERSION
    )
    bridge_rule_id: Identifier
    rule_version: ShortText
    source_domain_tag_ids: Annotated[
        tuple[Identifier, ...], Field(min_length=1, max_length=8)
    ]
    target_tag_ids: Annotated[tuple[Identifier, ...], Field(min_length=1, max_length=16)]
    required_evidence_tag_ids: Annotated[
        tuple[Identifier, ...], Field(min_length=1, max_length=16)
    ]
    suggested_query_tag_ids: Annotated[
        tuple[Identifier, ...], Field(min_length=1, max_length=16)
    ]
    query_templates: Annotated[
        tuple[ShortText, ...], Field(min_length=1, max_length=12)
    ]
    shared_invariant: LongText
    transferable_control: LongText
    required_conditions: Annotated[
        tuple[ShortText, ...], Field(min_length=1, max_length=16)
    ]
    breaking_conditions: Annotated[
        tuple[ShortText, ...], Field(min_length=1, max_length=16)
    ]
    operator_id: Literal["SUBSTITUTE_EQUIVALENT_SITE_V1"] = (
        "SUBSTITUTE_EQUIVALENT_SITE_V1"
    )

    @model_validator(mode="after")
    def validate_rule(self) -> BridgeRuleV1:
        collections = (
            self.source_domain_tag_ids,
            self.target_tag_ids,
            self.required_evidence_tag_ids,
            self.suggested_query_tag_ids,
            self.query_templates,
            self.required_conditions,
            self.breaking_conditions,
        )
        if any(_duplicates(values) for values in collections):
            raise ValueError("bridge rule collections must not contain duplicates")
        if set(self.source_domain_tag_ids) & set(self.target_tag_ids):
            raise ValueError("source-domain and target tag IDs must be disjoint")
        return self


class TagGraphV1(StrictModel):
    schema_version: Literal["inspiration-tag-graph-v1"] = (
        INSPIRATION_TAG_GRAPH_VERSION
    )
    graph_id: Identifier
    graph_version: ShortText
    curation_status: Literal["CURATED"] = "CURATED"
    tags: Annotated[tuple[TagDefinitionV1, ...], Field(min_length=1, max_length=2_000)]
    edges: Annotated[tuple[TagEdgeV1, ...], Field(max_length=10_000)] = ()
    bridge_rules: Annotated[tuple[BridgeRuleV1, ...], Field(max_length=1_000)] = ()

    @model_validator(mode="after")
    def validate_graph(self) -> TagGraphV1:
        tag_ids = tuple(tag.tag_id for tag in self.tags)
        duplicates = _duplicates(tag_ids)
        if duplicates:
            raise ValueError(f"tag IDs must be unique: {sorted(duplicates)}")
        known = set(tag_ids)
        edge_keys: set[tuple[str, str, str]] = set()
        for edge in self.edges:
            if edge.source_tag_id not in known or edge.target_tag_id not in known:
                raise ValueError("tag edge references an unknown tag")
            key = (edge.source_tag_id, edge.target_tag_id, edge.relation)
            if key in edge_keys:
                raise ValueError("duplicate tag edge")
            edge_keys.add(key)
        rule_ids: set[str] = set()
        for rule in self.bridge_rules:
            if rule.bridge_rule_id in rule_ids:
                raise ValueError("duplicate bridge rule ID")
            rule_ids.add(rule.bridge_rule_id)
            referenced_tags = (
                *rule.source_domain_tag_ids,
                *rule.target_tag_ids,
                *rule.required_evidence_tag_ids,
                *rule.suggested_query_tag_ids,
            )
            if any(tag_id not in known for tag_id in referenced_tags):
                raise ValueError("bridge rule references an unknown tag")
        return self


class BridgePacketV1(StrictModel):
    schema_version: Literal["inspiration-bridge-packet-v1"] = (
        INSPIRATION_BRIDGE_VERSION
    )
    bridge_packet_id: Identifier
    bridge_rule_id: Identifier
    source_domain_tag_ids: Annotated[
        tuple[Identifier, ...], Field(min_length=1, max_length=8)
    ]
    target_tag_ids: Annotated[tuple[Identifier, ...], Field(min_length=1, max_length=16)]
    shared_invariant: LongText
    transferable_control: LongText
    required_conditions: Annotated[
        tuple[ShortText, ...], Field(min_length=1, max_length=16)
    ]
    breaking_conditions: Annotated[
        tuple[ShortText, ...], Field(min_length=1, max_length=16)
    ]
    suggested_queries: Annotated[
        tuple[ShortText, ...], Field(min_length=1, max_length=12)
    ]
    evidence_card_ids: Annotated[
        tuple[Identifier, ...], Field(min_length=1, max_length=32)
    ]
    status: Literal["SEARCH_SUPPORTED"] = "SEARCH_SUPPORTED"

    @model_validator(mode="after")
    def validate_bridge(self) -> BridgePacketV1:
        collections = (
            self.source_domain_tag_ids,
            self.target_tag_ids,
            self.required_conditions,
            self.breaking_conditions,
            self.suggested_queries,
            self.evidence_card_ids,
        )
        if any(_duplicates(values) for values in collections):
            raise ValueError("bridge packet collections must not contain duplicates")
        if set(self.source_domain_tag_ids) & set(self.target_tag_ids):
            raise ValueError("source-domain and target tag IDs must be disjoint")
        return self


class ValidationStatus(StrEnum):
    PASS = "PASS"
    FAIL = "FAIL"
    UNKNOWN = "UNKNOWN"


class ValidationCheckV1(StrictModel):
    check_id: Identifier
    status: ValidationStatus
    detail: LongText


class SubstitutionParametersV1(StrictModel):
    equivalent_site_indices: Annotated[
        tuple[Annotated[int, Field(ge=0)], ...], Field(min_length=1, max_length=64)
    ]
    source_species: Annotated[str, Field(min_length=1, max_length=16)]
    target_species: Annotated[str, Field(min_length=1, max_length=16)]

    @model_validator(mode="after")
    def validate_substitution(self) -> SubstitutionParametersV1:
        if len(set(self.equivalent_site_indices)) != len(self.equivalent_site_indices):
            raise ValueError("equivalent site indices must be unique")
        if tuple(sorted(self.equivalent_site_indices)) != self.equivalent_site_indices:
            raise ValueError("equivalent site indices must be strictly increasing")
        if self.source_species == self.target_species:
            raise ValueError("source and target species must differ")
        return self


def transformation_route_sha256(
    *,
    parent_structure_id: str,
    operator_id: str,
    operator_version: str,
    parameters: StrictModel,
) -> str:
    """Hash the semantic route used to produce a structure proposal."""

    return canonical_sha256(
        {
            "parent_structure_id": parent_structure_id,
            "operator_id": operator_id,
            "operator_version": operator_version,
            "parameters": parameters,
        }
    )


class TransformationStatus(StrEnum):
    PLANNED = "PLANNED"
    REJECTED = "REJECTED"
    REQUIRES_REVIEW = "REQUIRES_REVIEW"
    STRUCTURE_VALID = "STRUCTURE_VALID"


class TransformationPlanV1(StrictModel):
    schema_version: Literal["inspiration-transformation-plan-v1"] = (
        INSPIRATION_TRANSFORMATION_VERSION
    )
    plan_id: Identifier
    parent_candidate_id: Identifier
    parent_structure_id: Identifier
    parent_structure_artifact: ArtifactPointerV1
    operator_id: Literal["SUBSTITUTE_EQUIVALENT_SITE_V1"] = (
        "SUBSTITUTE_EQUIVALENT_SITE_V1"
    )
    operator_version: Literal["1"] = "1"
    parameters: SubstitutionParametersV1
    preserved_features: Annotated[
        tuple[ShortText, ...], Field(min_length=1, max_length=16)
    ]
    changed_features: Annotated[
        tuple[ShortText, ...], Field(min_length=1, max_length=16)
    ]
    falsification_tests: Annotated[
        tuple[ShortText, ...], Field(min_length=1, max_length=16)
    ]
    bridge_packet_ids: Annotated[
        tuple[Identifier, ...], Field(min_length=1, max_length=16)
    ]
    route_sha256: Sha256
    status: TransformationStatus
    validation_checks: Annotated[tuple[ValidationCheckV1, ...], Field(max_length=64)] = ()
    output_structure_id: Identifier | None = None
    output_structure_artifact: ArtifactPointerV1 | None = None
    scientific_conclusion: Literal[False] = False

    @model_validator(mode="after")
    def validate_transformation(self) -> TransformationPlanV1:
        route_sha256 = transformation_route_sha256(
            parent_structure_id=self.parent_structure_id,
            operator_id=self.operator_id,
            operator_version=self.operator_version,
            parameters=self.parameters,
        )
        if self.route_sha256 != route_sha256:
            raise ValueError("route_sha256 does not match the transformation route")
        if _duplicates(self.bridge_packet_ids):
            raise ValueError("bridge packet IDs must be unique")

        has_structure_id = self.output_structure_id is not None
        has_structure_artifact = self.output_structure_artifact is not None
        if has_structure_id != has_structure_artifact:
            raise ValueError("output structure ID and artifact must be supplied together")

        check_ids = tuple(check.check_id for check in self.validation_checks)
        if _duplicates(check_ids):
            raise ValueError("validation check IDs must be unique")
        checks = {check.check_id: check.status for check in self.validation_checks}
        statuses = set(checks.values())
        if self.status is TransformationStatus.PLANNED:
            if has_structure_id or self.validation_checks:
                raise ValueError("planned transformation cannot contain execution output")
        elif self.status is TransformationStatus.REJECTED:
            if has_structure_id or ValidationStatus.FAIL not in statuses:
                raise ValueError("rejected transformation requires a failing check and no output")
        elif self.status is TransformationStatus.REQUIRES_REVIEW:
            if not has_structure_id or not self.validation_checks:
                raise ValueError("review-required structure needs output and checks")
            if ValidationStatus.FAIL in statuses:
                raise ValueError("review-required structure cannot contain a failing check")
            missing_pass_checks = [
                check_id
                for check_id in REQUIRED_STRUCTURE_PASS_CHECK_IDS
                if checks.get(check_id) is not ValidationStatus.PASS
            ]
            if missing_pass_checks:
                raise ValueError(
                    "valid structure is missing required PASS checks: "
                    f"{missing_pass_checks}"
                )
            missing_nonfail_checks = [
                check_id
                for check_id in REQUIRED_STRUCTURE_NONFAIL_CHECK_IDS
                if checks.get(check_id)
                not in {ValidationStatus.PASS, ValidationStatus.UNKNOWN}
            ]
            if missing_nonfail_checks:
                raise ValueError(
                    "valid structure is missing required PASS/UNKNOWN checks: "
                    f"{missing_nonfail_checks}"
                )
            if not any(
                checks.get(check_id) is ValidationStatus.UNKNOWN
                for check_id in REQUIRED_STRUCTURE_NONFAIL_CHECK_IDS
            ):
                raise ValueError("review-required structure needs an UNKNOWN check")
        else:
            if not has_structure_id or not self.validation_checks:
                raise ValueError("valid structure requires output and validation checks")
            if ValidationStatus.FAIL in statuses:
                raise ValueError("valid structure cannot contain a failing check")
            required_checks = (
                *REQUIRED_STRUCTURE_PASS_CHECK_IDS,
                *REQUIRED_STRUCTURE_NONFAIL_CHECK_IDS,
            )
            missing_checks = [
                check_id
                for check_id in required_checks
                if checks.get(check_id) is not ValidationStatus.PASS
            ]
            if missing_checks:
                raise ValueError(
                    "valid structure is missing required PASS checks: "
                    f"{missing_checks}"
                )
        return self


class CandidateScoresV1(StrictModel):
    policy_id: Identifier
    score_version: Identifier = "inspiration-selection-score-v1"
    quality: Score
    evidence_coverage: Score
    redundancy_penalty: Score
    quality_weight: Score = 0.60
    coverage_weight: Score = 0.15
    redundancy_weight: Score = 0.25
    selection_score: Annotated[
        float, Field(ge=-1.0, le=1.0, allow_inf_nan=False)
    ]

    @model_validator(mode="after")
    def validate_replayable_score(self) -> CandidateScoresV1:
        weight_sum = (
            self.quality_weight + self.coverage_weight + self.redundancy_weight
        )
        if abs(weight_sum - 1.0) > 1e-9:
            raise ValueError("selection score weights must sum to one")
        expected = (
            self.quality * self.quality_weight
            + self.evidence_coverage * self.coverage_weight
            - self.redundancy_penalty * self.redundancy_weight
        )
        if abs(self.selection_score - expected) > 1e-9:
            raise ValueError("selection_score does not match its weighted components")
        return self


class CandidateRouteRefV1(StrictModel):
    plan_id: Identifier
    route_sha256: Sha256
    parent_candidate_id: Identifier
    mechanism_tag_ids: Annotated[
        tuple[Identifier, ...], Field(min_length=1, max_length=32)
    ]
    bridge_packet_ids: Annotated[
        tuple[Identifier, ...], Field(min_length=1, max_length=32)
    ]
    evidence_card_ids: Annotated[
        tuple[Identifier, ...], Field(min_length=1, max_length=64)
    ]

    @model_validator(mode="after")
    def validate_route_lineage(self) -> CandidateRouteRefV1:
        for label, values in (
            ("mechanism tag IDs", self.mechanism_tag_ids),
            ("bridge packet IDs", self.bridge_packet_ids),
            ("evidence card IDs", self.evidence_card_ids),
        ):
            if tuple(sorted(values)) != values or _duplicates(values):
                raise ValueError(f"{label} must be sorted and unique")
        return self


def hypothesis_signature_sha256_for(
    *,
    canonical_structure_id: str,
    mechanism_tag_ids: tuple[str, ...],
    route_sha256s: tuple[str, ...],
) -> str:
    """Hash the canonical structure plus the merged hypothesis routes."""

    return canonical_sha256(
        {
            "canonical_structure_id": canonical_structure_id,
            "mechanism_tag_ids": tuple(sorted(set(mechanism_tag_ids))),
            "route_sha256s": tuple(sorted(set(route_sha256s))),
        }
    )


class InspirationCandidateV1(StrictModel):
    schema_version: Literal["inspiration-candidate-v1"] = (
        INSPIRATION_CANDIDATE_VERSION
    )
    candidate_id: Identifier
    canonical_structure_id: Identifier
    structure_artifact: ArtifactPointerV1
    representative_plan_id: Identifier
    merged_routes: Annotated[
        tuple[CandidateRouteRefV1, ...], Field(min_length=1, max_length=128)
    ]
    parent_candidate_ids: Annotated[
        tuple[Identifier, ...], Field(min_length=1, max_length=32)
    ]
    mechanism_tag_ids: Annotated[
        tuple[Identifier, ...], Field(min_length=1, max_length=32)
    ]
    evidence_card_ids: Annotated[
        tuple[Identifier, ...], Field(min_length=1, max_length=64)
    ]
    hypothesis_signature_sha256: Sha256
    scores: CandidateScoresV1
    selection_rank: Annotated[int, Field(ge=1)] | None = None
    next_falsification_step: LongText
    scientific_conclusion: Literal[False] = False

    @model_validator(mode="after")
    def validate_lineage(self) -> InspirationCandidateV1:
        plan_ids = tuple(route.plan_id for route in self.merged_routes)
        route_sha256s = tuple(route.route_sha256 for route in self.merged_routes)
        if self.representative_plan_id not in plan_ids:
            raise ValueError("representative plan must be retained in merged plan lineage")
        if _duplicates(plan_ids) or _duplicates(route_sha256s):
            raise ValueError("merged routes must have unique plan IDs and route hashes")
        if route_sha256s != tuple(sorted(route_sha256s)):
            raise ValueError("merged routes must be sorted by route hash")
        for label, values in (
            ("parent candidate IDs", self.parent_candidate_ids),
            ("mechanism tag IDs", self.mechanism_tag_ids),
            ("evidence card IDs", self.evidence_card_ids),
        ):
            if tuple(sorted(values)) != values or _duplicates(values):
                raise ValueError(f"{label} must be sorted and unique")
        expected_parents = tuple(
            sorted({route.parent_candidate_id for route in self.merged_routes})
        )
        expected_mechanisms = tuple(
            sorted(
                {
                    tag_id
                    for route in self.merged_routes
                    for tag_id in route.mechanism_tag_ids
                }
            )
        )
        expected_evidence = tuple(
            sorted(
                {
                    card_id
                    for route in self.merged_routes
                    for card_id in route.evidence_card_ids
                }
            )
        )
        if self.parent_candidate_ids != expected_parents:
            raise ValueError("parent candidate IDs do not match merged routes")
        if self.mechanism_tag_ids != expected_mechanisms:
            raise ValueError("mechanism tag IDs do not match merged routes")
        if self.evidence_card_ids != expected_evidence:
            raise ValueError("evidence card IDs do not match merged routes")
        expected_signature = hypothesis_signature_sha256_for(
            canonical_structure_id=self.canonical_structure_id,
            mechanism_tag_ids=self.mechanism_tag_ids,
            route_sha256s=route_sha256s,
        )
        if self.hypothesis_signature_sha256 != expected_signature:
            raise ValueError("hypothesis signature does not match candidate lineage")
        return self


class CostLedgerV1(StrictModel):
    schema_version: Literal["inspiration-cost-ledger-v1"] = INSPIRATION_COST_VERSION
    search_requests: Annotated[int, Field(ge=0)] = 0
    search_response_bytes: Annotated[int, Field(ge=0)] = 0
    fetch_requests: Annotated[int, Field(ge=0)] = 0
    fetch_response_bytes: Annotated[int, Field(ge=0)] = 0
    raw_documents: Annotated[int, Field(ge=0)] = 0
    unique_documents: Annotated[int, Field(ge=0)] = 0
    extracted_passages: Annotated[int, Field(ge=0)] = 0
    vectorized_passages: Annotated[int, Field(ge=0)] = 0
    embedding_input_tokens: Annotated[int, Field(ge=0)] = 0
    llm_calls: Annotated[int, Field(ge=0)] = 0
    llm_input_tokens: Annotated[int, Field(ge=0)] = 0
    llm_output_tokens: Annotated[int, Field(ge=0)] = 0
    generated_plans: Annotated[int, Field(ge=0)] = 0
    rejected_plans: Annotated[int, Field(ge=0)] = 0
    candidates_after_internal_dedup: Annotated[int, Field(ge=0)] = 0
    walltime_ms: Annotated[int, Field(ge=0)] = 0

    @model_validator(mode="after")
    def validate_counts(self) -> CostLedgerV1:
        if self.search_requests == 0 and self.search_response_bytes != 0:
            raise ValueError("zero search requests require zero search bytes")
        if self.fetch_requests == 0 and self.fetch_response_bytes != 0:
            raise ValueError("zero fetch requests require zero fetch bytes")
        if self.unique_documents > self.raw_documents:
            raise ValueError("unique document count cannot exceed raw document count")
        if self.raw_documents == 0 and self.extracted_passages != 0:
            raise ValueError("zero documents require zero extracted passages")
        if self.vectorized_passages > self.extracted_passages:
            raise ValueError("vectorized passage count cannot exceed extracted passage count")
        if self.vectorized_passages == 0 and self.embedding_input_tokens != 0:
            raise ValueError("zero vectors require zero embedding input tokens")
        if self.llm_calls == 0 and (self.llm_input_tokens or self.llm_output_tokens):
            raise ValueError("zero LLM calls require zero LLM token counts")
        if self.rejected_plans > self.generated_plans:
            raise ValueError("rejected plan count cannot exceed generated plan count")
        accepted_plans = self.generated_plans - self.rejected_plans
        if self.candidates_after_internal_dedup > accepted_plans:
            raise ValueError("deduplicated candidates cannot exceed accepted plans")
        return self


class InspirationOutcome(StrEnum):
    SUCCEEDED = "SUCCEEDED"
    SCIENTIFIC_NO_MATCH = "SCIENTIFIC_NO_MATCH"


class InspirationBundleV1(StrictModel):
    schema_version: Literal["inspiration-bundle-v1"] = INSPIRATION_BUNDLE_VERSION
    bundle_id: Identifier
    request_id: Identifier
    run_id: Identifier
    outcome: InspirationOutcome
    selected_candidates: Annotated[
        tuple[InspirationCandidateV1, ...], Field(max_length=32)
    ] = ()
    limitations: Annotated[tuple[ShortText, ...], Field(min_length=1, max_length=32)]
    next_validation_steps: Annotated[
        tuple[ShortText, ...], Field(min_length=1, max_length=32)
    ]
    cost_ledger: CostLedgerV1
    lineage_artifacts: Annotated[
        tuple[ArtifactPointerV1, ...], Field(min_length=1, max_length=256)
    ]
    scope_statement: Literal["STRUCTURE_PROPOSALS_REQUIRE_DOWNSTREAM_VALIDATION"] = (
        "STRUCTURE_PROPOSALS_REQUIRE_DOWNSTREAM_VALIDATION"
    )
    scientific_conclusion: Literal[False] = False

    @model_validator(mode="after")
    def validate_outcome(self) -> InspirationBundleV1:
        if self.outcome is InspirationOutcome.SUCCEEDED and not self.selected_candidates:
            raise ValueError("successful bundle requires at least one selected candidate")
        if (
            self.outcome is InspirationOutcome.SCIENTIFIC_NO_MATCH
            and self.selected_candidates
        ):
            raise ValueError("no-match bundle cannot contain selected candidates")

        candidate_ids = tuple(item.candidate_id for item in self.selected_candidates)
        if _duplicates(candidate_ids):
            raise ValueError("selected candidate IDs must be unique")
        ranks = tuple(item.selection_rank for item in self.selected_candidates)
        if ranks and ranks != tuple(range(1, len(ranks) + 1)):
            raise ValueError("selected candidates must have contiguous ranks starting at one")
        artifact_uris = tuple(item.uri for item in self.lineage_artifacts)
        if _duplicates(artifact_uris):
            raise ValueError("lineage artifact URIs must be unique")
        return self


class InspirationStageResultV1(StrictModel):
    schema_version: Literal["inspiration-stage-result-v1"] = (
        INSPIRATION_STAGE_RESULT_VERSION
    )
    result_id: Identifier
    project_id: Identifier
    request_id: Identifier
    run_id: Identifier
    outcome: InspirationOutcome
    input_snapshot_artifact: ArtifactPointerV1
    policy_artifact: ArtifactPointerV1
    bundle_artifact: ArtifactPointerV1
    report_artifact: ArtifactPointerV1
    cost_ledger_artifact: ArtifactPointerV1
    intermediate_artifacts: Annotated[
        tuple[ArtifactPointerV1, ...], Field(min_length=1, max_length=256)
    ]
    warnings: Annotated[tuple[ShortText, ...], Field(max_length=64)] = ()
    scientific_conclusion: Literal[False] = False

    @model_validator(mode="after")
    def validate_artifacts(self) -> InspirationStageResultV1:
        all_artifacts = (
            self.input_snapshot_artifact,
            self.policy_artifact,
            self.bundle_artifact,
            self.report_artifact,
            self.cost_ledger_artifact,
            *self.intermediate_artifacts,
        )
        uris = tuple(item.uri for item in all_artifacts)
        if _duplicates(uris):
            raise ValueError("stage result artifact URIs must be unique")
        return self


PUBLIC_CONTRACT_MODELS: tuple[type[StrictModel], ...] = (
    ArtifactPointerV1,
    ComponentSnapshotV1,
    ParentCandidateRefV1,
    InspirationInputV1,
    SearchQueryV1,
    SearchHitV1,
    PassageLocatorV1,
    PassageV1,
    PassageVectorV1,
    EvidenceCardV1,
    TagDefinitionV1,
    TagEdgeV1,
    BridgeRuleV1,
    TagGraphV1,
    BridgePacketV1,
    ValidationCheckV1,
    SubstitutionParametersV1,
    TransformationPlanV1,
    CandidateScoresV1,
    CandidateRouteRefV1,
    InspirationCandidateV1,
    CostLedgerV1,
    InspirationBundleV1,
    InspirationStageResultV1,
)
