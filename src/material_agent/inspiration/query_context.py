"""Deterministic material-aware context for literature-query planning.

The compiler is deliberately upstream of search.  It converts a confirmed
retrieval requirement, published Agent01 candidates, and explicitly reviewed
hints into one immutable, replayable context.  It does not call an LLM, infer a
scientific claim, or execute a provider query.
"""

from __future__ import annotations

import re
from collections import defaultdict
from enum import StrEnum
from typing import Annotated, Literal

from pydantic import Field, model_validator
from pymatgen.core import Composition

from material_agent.inspiration.models import (
    Identifier,
    LongText,
    ShortText,
    StrictModel,
    deterministic_id,
)
from material_agent.retrieval.models import (
    CandidateAuditRecord,
    CandidateAuditRecordV2,
    Decision,
    EvidenceLevel,
    Requirement,
)


QUERY_CONTEXT_SCHEMA_VERSION = "inspiration-query-context-v2"


class QueryContextError(ValueError):
    """Fail-closed context-compilation error with a stable code."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(f"{code}: {message}")


class AnchorPolarity(StrEnum):
    POSITIVE = "POSITIVE"
    NEGATIVE = "NEGATIVE"


class MaterialAliasSetV2(StrictModel):
    """Human-reviewed aliases for one chemically equivalent formula."""

    formula: ShortText
    aliases: Annotated[tuple[ShortText, ...], Field(min_length=1, max_length=32)]

    @model_validator(mode="after")
    def validate_aliases(self) -> MaterialAliasSetV2:
        if len({_text_key(item) for item in self.aliases}) != len(self.aliases):
            raise ValueError("material aliases must be unique")
        return self


class LiteratureAnchorV2(StrictModel):
    """A user- or expert-reviewed paper anchor; resolution occurs downstream."""

    provider_record_id: ShortText
    title: Annotated[str, Field(min_length=1, max_length=1_000)]
    polarity: AnchorPolarity


class ReviewedQueryHintsV2(StrictModel):
    """Only reviewed free-form additions accepted by the deterministic compiler."""

    material_aliases: Annotated[
        tuple[MaterialAliasSetV2, ...], Field(max_length=64)
    ] = ()
    structure_terms: Annotated[tuple[ShortText, ...], Field(max_length=32)] = ()
    mechanism_terms: Annotated[tuple[ShortText, ...], Field(max_length=32)] = ()
    operation_terms: Annotated[tuple[ShortText, ...], Field(max_length=32)] = ()
    anchors: Annotated[tuple[LiteratureAnchorV2, ...], Field(max_length=64)] = ()
    exclusion_terms: Annotated[tuple[ShortText, ...], Field(max_length=64)] = ()

    @model_validator(mode="after")
    def validate_reviewed_hints(self) -> ReviewedQueryHintsV2:
        for field_name in (
            "structure_terms",
            "mechanism_terms",
            "operation_terms",
            "exclusion_terms",
        ):
            values = getattr(self, field_name)
            if len({_text_key(item) for item in values}) != len(values):
                raise ValueError(f"{field_name} must be unique")
        alias_formulas = tuple(
            _canonical_formula(item.formula)[0] for item in self.material_aliases
        )
        if len(set(alias_formulas)) != len(alias_formulas):
            raise ValueError("material alias formula groups must be unique")
        anchor_keys = tuple(
            _text_key(item.provider_record_id) for item in self.anchors
        )
        if len(set(anchor_keys)) != len(anchor_keys):
            raise ValueError("one paper cannot be both duplicated or oppositely anchored")
        return self


class MaterialQueryContextV2(StrictModel):
    reduced_formula: ShortText
    element_symbols: Annotated[
        tuple[Identifier, ...], Field(min_length=1, max_length=118)
    ]
    query_terms: Annotated[tuple[ShortText, ...], Field(min_length=1, max_length=64)]
    candidate_ids: Annotated[tuple[Identifier, ...], Field(max_length=32)] = ()
    source_databases: Annotated[tuple[Identifier, ...], Field(max_length=16)] = ()

    @model_validator(mode="after")
    def validate_material(self) -> MaterialQueryContextV2:
        canonical, elements = _canonical_formula(self.reduced_formula)
        if canonical != self.reduced_formula or elements != self.element_symbols:
            raise ValueError("material formula or element symbols are not canonical")
        if len({_text_key(item) for item in self.query_terms}) != len(
            self.query_terms
        ):
            raise ValueError("material query terms must be unique")
        if len(set(self.candidate_ids)) != len(self.candidate_ids):
            raise ValueError("material candidate IDs must be unique")
        if len(set(self.source_databases)) != len(self.source_databases):
            raise ValueError("material source databases must be unique")
        return self


class ScientificTargetContextV2(StrictModel):
    name: ShortText
    operational_definition: LongText | None = None
    required_evidence_level: EvidenceLevel


class QueryContextV2(StrictModel):
    """Frozen, material-aware input to the future query-family planner."""

    schema_version: Literal["inspiration-query-context-v2"] = (
        QUERY_CONTEXT_SCHEMA_VERSION
    )
    context_id: Identifier
    requirement_id: Identifier
    requirement_revision: Annotated[int, Field(ge=1)]
    target_class: ShortText
    user_goal: LongText | None = None
    materials: Annotated[
        tuple[MaterialQueryContextV2, ...], Field(min_length=1, max_length=64)
    ]
    scientific_targets: Annotated[
        tuple[ScientificTargetContextV2, ...], Field(max_length=32)
    ] = ()
    mechanism_terms: Annotated[tuple[ShortText, ...], Field(max_length=32)] = ()
    operation_terms: Annotated[tuple[ShortText, ...], Field(max_length=32)] = ()
    structure_terms: Annotated[tuple[ShortText, ...], Field(max_length=32)] = ()
    positive_anchors: Annotated[
        tuple[LiteratureAnchorV2, ...], Field(max_length=32)
    ] = ()
    negative_anchors: Annotated[
        tuple[LiteratureAnchorV2, ...], Field(max_length=32)
    ] = ()
    exclusion_terms: Annotated[tuple[ShortText, ...], Field(max_length=64)] = ()
    publication_year_from: Annotated[int, Field(ge=1600, le=2200)] | None = None
    publication_year_to: Annotated[int, Field(ge=1600, le=2200)] | None = None
    source_candidate_ids: Annotated[
        tuple[Identifier, ...], Field(min_length=1, max_length=32)
    ]

    @model_validator(mode="after")
    def validate_context(self) -> QueryContextV2:
        if (
            self.publication_year_from is not None
            and self.publication_year_to is not None
            and self.publication_year_from > self.publication_year_to
        ):
            raise ValueError("publication year range is reversed")
        if len(set(self.source_candidate_ids)) != len(self.source_candidate_ids):
            raise ValueError("source candidate IDs must be unique")
        material_ids = {
            candidate_id
            for material in self.materials
            for candidate_id in material.candidate_ids
        }
        if material_ids != set(self.source_candidate_ids):
            raise ValueError("material lineage must cover every source candidate exactly")
        expected = deterministic_id(
            "query-context",
            self.model_dump(mode="json", exclude={"context_id"}),
        )
        if self.context_id != expected:
            raise ValueError("context ID does not match canonical content")
        return self


_MECHANISM_TERMS = (
    ("charge density wave", "charge density wave"),
    ("cdw", "charge density wave"),
    ("electronic flat band", "electronic flat band"),
    ("flat band", "flat band"),
    ("band structure", "band structure"),
    ("electronic structure", "electronic structure"),
    ("superconduct", "superconductivity"),
    ("topolog", "electronic topology"),
    ("correlat", "electronic correlation"),
    ("磁性", "magnetism"),
    ("超导", "superconductivity"),
    ("电荷密度波", "charge density wave"),
    ("平带", "flat band"),
)

_OPERATION_TERMS = (
    ("substitution", "substitution"),
    ("replace", "substitution"),
    ("doping", "doping"),
    ("dope", "doping"),
    ("intercalat", "intercalation"),
    ("vacancy", "vacancy engineering"),
    ("strain", "strain engineering"),
    ("替位", "substitution"),
    ("替换", "substitution"),
    ("掺杂", "doping"),
    ("插层", "intercalation"),
    ("空位", "vacancy engineering"),
    ("应变", "strain engineering"),
)


def compile_query_context_v2(
    *,
    requirement: Requirement,
    parent_candidates: tuple[CandidateAuditRecord | CandidateAuditRecordV2, ...],
    raw_request: str | None = None,
    reviewed_hints: ReviewedQueryHintsV2 | None = None,
    publication_year_from: int | None = None,
    publication_year_to: int | None = None,
) -> QueryContextV2:
    """Compile one deterministic context from explicit, bounded inputs."""

    if not isinstance(requirement, Requirement):
        raise QueryContextError("INVALID_REQUIREMENT", "requirement is not validated")
    if not parent_candidates:
        raise QueryContextError(
            "NO_PARENT_CANDIDATES", "at least one Agent01 parent is required"
        )
    if len(parent_candidates) > 32:
        raise QueryContextError(
            "TOO_MANY_PARENT_CANDIDATES", "at most 32 Agent01 parents are accepted"
        )
    hints = reviewed_hints or ReviewedQueryHintsV2()
    goal = _normalize_optional_text(raw_request, max_length=4_000)

    candidates = tuple(sorted(parent_candidates, key=lambda item: item.candidate_id))
    for candidate in candidates:
        if not isinstance(candidate, (CandidateAuditRecord, CandidateAuditRecordV2)):
            raise QueryContextError(
                "INVALID_PARENT_CANDIDATE", "parent candidate is not validated"
            )
        if not candidate.published_downstream or candidate.decision not in {
            Decision.PASS,
            Decision.UNCERTAIN,
        }:
            raise QueryContextError(
                "INELIGIBLE_PARENT_CANDIDATE",
                f"candidate {candidate.candidate_id} is not a published PASS/UNCERTAIN parent",
            )

    material_terms: dict[str, list[str]] = defaultdict(list)
    material_candidate_ids: dict[str, list[str]] = defaultdict(list)
    material_sources: dict[str, list[str]] = defaultdict(list)

    exact_formula = requirement.hard_constraints.exact_formula
    if exact_formula:
        canonical, _ = _canonical_formula(exact_formula)
        material_terms[canonical].extend((canonical, exact_formula))

    for candidate in candidates:
        canonical, _ = _canonical_formula(candidate.reduced_formula or candidate.formula)
        material_terms[canonical].extend(
            value
            for value in (canonical, candidate.reduced_formula, candidate.formula)
            if value
        )
        material_candidate_ids[canonical].append(candidate.candidate_id)
        source = candidate.source_database
        material_sources[canonical].append(
            source.value if hasattr(source, "value") else str(source)
        )

    for alias_set in hints.material_aliases:
        canonical, _ = _canonical_formula(alias_set.formula)
        if canonical not in material_terms:
            raise QueryContextError(
                "ALIAS_FORMULA_NOT_IN_CONTEXT",
                f"reviewed aliases reference absent material {canonical}",
            )
        material_terms[canonical].extend(alias_set.aliases)

    materials: list[MaterialQueryContextV2] = []
    for formula in sorted(material_terms):
        _, elements = _canonical_formula(formula)
        materials.append(
            MaterialQueryContextV2(
                reduced_formula=formula,
                element_symbols=elements,
                query_terms=_unique_text(material_terms[formula]),
                candidate_ids=tuple(sorted(set(material_candidate_ids[formula]))),
                source_databases=tuple(sorted(set(material_sources[formula]))),
            )
        )

    source_text = " ".join(
        item
        for item in (
            goal,
            requirement.target_class,
            *(target.name for target in requirement.scientific_targets),
            *(
                target.operational_definition or ""
                for target in requirement.scientific_targets
            ),
        )
        if item
    ).casefold()
    mechanism_terms = _unique_text(
        [canonical for cue, canonical in _MECHANISM_TERMS if cue in source_text]
        + list(hints.mechanism_terms)
    )
    operation_terms = _unique_text(
        [canonical for cue, canonical in _OPERATION_TERMS if cue in source_text]
        + list(hints.operation_terms)
    )
    structure_terms = _unique_text(
        [requirement.target_class, *hints.structure_terms]
    )
    scientific_targets = tuple(
        ScientificTargetContextV2(
            name=_normalize_text(target.name, max_length=512),
            operational_definition=_normalize_optional_text(
                target.operational_definition, max_length=4_000
            ),
            required_evidence_level=target.required_evidence_level,
        )
        for target in requirement.scientific_targets
    )
    positive = tuple(
        sorted(
            (item for item in hints.anchors if item.polarity is AnchorPolarity.POSITIVE),
            key=lambda item: _text_key(item.provider_record_id),
        )
    )
    negative = tuple(
        sorted(
            (item for item in hints.anchors if item.polarity is AnchorPolarity.NEGATIVE),
            key=lambda item: _text_key(item.provider_record_id),
        )
    )

    payload = {
        "schema_version": QUERY_CONTEXT_SCHEMA_VERSION,
        "requirement_id": requirement.requirement_id,
        "requirement_revision": requirement.revision,
        "target_class": _normalize_text(requirement.target_class, max_length=512),
        "user_goal": goal,
        "materials": tuple(materials),
        "scientific_targets": scientific_targets,
        "mechanism_terms": mechanism_terms,
        "operation_terms": operation_terms,
        "structure_terms": structure_terms,
        "positive_anchors": positive,
        "negative_anchors": negative,
        "exclusion_terms": _unique_text(hints.exclusion_terms),
        "publication_year_from": publication_year_from,
        "publication_year_to": publication_year_to,
        "source_candidate_ids": tuple(item.candidate_id for item in candidates),
    }
    return QueryContextV2(
        context_id=deterministic_id("query-context", payload),
        **payload,
    )


def _canonical_formula(value: str) -> tuple[str, tuple[str, ...]]:
    try:
        composition = Composition(value.strip())
        formula = composition.reduced_formula
        elements = tuple(sorted(str(element) for element in composition.elements))
    except (TypeError, ValueError) as exc:
        raise QueryContextError(
            "INVALID_MATERIAL_FORMULA", f"invalid material formula: {value!r}"
        ) from exc
    if not formula or not elements:
        raise QueryContextError(
            "INVALID_MATERIAL_FORMULA", f"empty material formula: {value!r}"
        )
    return formula, elements


def _normalize_text(value: str, *, max_length: int) -> str:
    if not isinstance(value, str):
        raise QueryContextError("INVALID_CONTEXT_TEXT", "context text must be a string")
    normalized = re.sub(r"\s+", " ", value).strip()
    if not normalized or len(normalized) > max_length:
        raise QueryContextError(
            "INVALID_CONTEXT_TEXT", f"context text must contain 1-{max_length} characters"
        )
    return normalized


def _normalize_optional_text(value: str | None, *, max_length: int) -> str | None:
    if value is None:
        return None
    return _normalize_text(value, max_length=max_length)


def _text_key(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip().casefold()


def _unique_text(values: object) -> tuple[str, ...]:
    result: list[str] = []
    seen: set[str] = set()
    for raw in values:  # type: ignore[union-attr]
        normalized = _normalize_text(raw, max_length=512)
        key = normalized.casefold()
        if key not in seen:
            seen.add(key)
            result.append(normalized)
    return tuple(result)
