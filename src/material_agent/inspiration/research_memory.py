"""Append-only, evidence-linked project memory for Inspiration.

The store borrows AutoSci's cross-run memory idea while retaining this
project's immutable artifact and SQLite conventions.  Automated observations,
reviewed anchors, expert corrections, and downstream outcomes remain distinct
payload kinds; none is promoted to a scientific conclusion by this module.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable, Iterator
from enum import StrEnum
from pathlib import Path
from typing import Annotated, Literal

from pydantic import Field, model_validator

from material_agent.inspiration.literature_budget import LiteratureQueryFamily
from material_agent.inspiration.models import (
    ArtifactPointerV1,
    Identifier,
    Sha256,
    ShortText,
    StrictModel,
    canonical_json_bytes,
    canonical_sha256,
    deterministic_id,
)
from material_agent.inspiration.query_context import (
    AnchorPolarity,
    LiteratureAnchorV2,
    ReviewedQueryHintsV2,
)


INSPIRATION_MEMORY_EVENT_VERSION = "inspiration-memory-event-v1"
INSPIRATION_MEMORY_SNAPSHOT_VERSION = "inspiration-memory-snapshot-v1"


class InspirationMemoryError(RuntimeError):
    """Fail-closed memory error with a stable code."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(f"{code}: {message}")


class MemoryPayloadKind(StrEnum):
    PAPER_ANCHOR = "PAPER_ANCHOR"
    QUERY_OUTCOME = "QUERY_OUTCOME"
    EVIDENCE_ASSESSMENT = "EVIDENCE_ASSESSMENT"
    EXPERT_CORRECTION = "EXPERT_CORRECTION"
    DOWNSTREAM_OUTCOME = "DOWNSTREAM_OUTCOME"


class QueryMemoryOutcome(StrEnum):
    SUCCEEDED_WITH_EVIDENCE = "SUCCEEDED_WITH_EVIDENCE"
    NO_MATCH = "NO_MATCH"
    FAILED = "FAILED"


class EvidenceMemoryStrength(StrEnum):
    SUPPORTED = "SUPPORTED"
    MIXED = "MIXED"
    INSUFFICIENT = "INSUFFICIENT"


class ExpertCorrectionKind(StrEnum):
    ADD_MECHANISM = "ADD_MECHANISM"
    ADD_OPERATION = "ADD_OPERATION"
    ADD_STRUCTURE = "ADD_STRUCTURE"
    EXCLUDE_TERM = "EXCLUDE_TERM"


class DownstreamMemoryStage(StrEnum):
    SMACT = "SMACT"
    CHGNET = "CHGNET"
    DEEPH = "DEEPH"
    DFT = "DFT"


class PaperAnchorMemoryV1(StrictModel):
    kind: Literal["PAPER_ANCHOR"] = MemoryPayloadKind.PAPER_ANCHOR.value
    provider_record_id: ShortText
    title: Annotated[str, Field(min_length=1, max_length=1_000)]
    polarity: AnchorPolarity
    rationale: ShortText
    reviewer_id: Identifier
    review_status: Literal["REVIEWED"] = "REVIEWED"


class QueryOutcomeMemoryV1(StrictModel):
    kind: Literal["QUERY_OUTCOME"] = MemoryPayloadKind.QUERY_OUTCOME.value
    provider_id: Identifier
    provider_query_id: Identifier
    family: LiteratureQueryFamily
    query_text: Annotated[str, Field(min_length=3, max_length=512)]
    outcome: QueryMemoryOutcome
    raw_hit_count: Annotated[int, Field(ge=0, le=10_000)]
    eligible_evidence_count: Annotated[int, Field(ge=0, le=2_000)]

    @model_validator(mode="after")
    def validate_counts(self) -> QueryOutcomeMemoryV1:
        if self.eligible_evidence_count > self.raw_hit_count:
            raise ValueError("eligible evidence count exceeds raw-hit count")
        if (
            self.outcome is QueryMemoryOutcome.SUCCEEDED_WITH_EVIDENCE
            and self.eligible_evidence_count == 0
        ):
            raise ValueError("successful query memory requires eligible evidence")
        if (
            self.outcome is QueryMemoryOutcome.NO_MATCH
            and (self.raw_hit_count != 0 or self.eligible_evidence_count != 0)
        ):
            raise ValueError("NO_MATCH query memory requires zero observed records")
        return self


class EvidenceAssessmentMemoryV1(StrictModel):
    kind: Literal["EVIDENCE_ASSESSMENT"] = (
        MemoryPayloadKind.EVIDENCE_ASSESSMENT.value
    )
    candidate_id: Identifier
    evidence_card_ids: Annotated[
        tuple[Identifier, ...], Field(min_length=1, max_length=64)
    ]
    strength: EvidenceMemoryStrength
    assessment_basis: ShortText
    assessed_by: Literal["DEEPSEEK_GROUNDED_RERANKER", "EXPERT"]

    @model_validator(mode="after")
    def validate_evidence_ids(self) -> EvidenceAssessmentMemoryV1:
        if tuple(sorted(set(self.evidence_card_ids))) != self.evidence_card_ids:
            raise ValueError("evidence card IDs must be sorted and unique")
        return self


class ExpertCorrectionMemoryV1(StrictModel):
    kind: Literal["EXPERT_CORRECTION"] = MemoryPayloadKind.EXPERT_CORRECTION.value
    correction_kind: ExpertCorrectionKind
    term: ShortText
    rationale: ShortText
    reviewer_id: Identifier
    review_status: Literal["REVIEWED"] = "REVIEWED"


class DownstreamOutcomeMemoryV1(StrictModel):
    kind: Literal["DOWNSTREAM_OUTCOME"] = MemoryPayloadKind.DOWNSTREAM_OUTCOME.value
    candidate_id: Identifier
    stage: DownstreamMemoryStage
    outcome: Identifier
    evidence_level: Identifier
    scientific_conclusion: Literal[False] = False


MemoryPayloadV1 = Annotated[
    PaperAnchorMemoryV1
    | QueryOutcomeMemoryV1
    | EvidenceAssessmentMemoryV1
    | ExpertCorrectionMemoryV1
    | DownstreamOutcomeMemoryV1,
    Field(discriminator="kind"),
]


class InspirationMemoryEventV1(StrictModel):
    schema_version: Literal["inspiration-memory-event-v1"] = (
        INSPIRATION_MEMORY_EVENT_VERSION
    )
    event_id: Identifier
    project_id: Identifier
    source_run_id: Identifier
    context_id: Identifier
    source_artifact: ArtifactPointerV1
    payload: MemoryPayloadV1

    @model_validator(mode="after")
    def validate_identity(self) -> InspirationMemoryEventV1:
        expected = deterministic_id(
            "memory-event",
            self.model_dump(mode="json", exclude={"event_id"}),
        )
        if self.event_id != expected:
            raise ValueError("memory event ID does not match canonical content")
        return self


class RememberedQueryV1(StrictModel):
    event_id: Identifier
    provider_id: Identifier
    provider_query_id: Identifier
    family: LiteratureQueryFamily
    query_text: Annotated[str, Field(min_length=3, max_length=512)]
    outcome: QueryMemoryOutcome
    raw_hit_count: Annotated[int, Field(ge=0, le=10_000)]
    eligible_evidence_count: Annotated[int, Field(ge=0, le=2_000)]


class InspirationMemorySnapshotV1(StrictModel):
    schema_version: Literal["inspiration-memory-snapshot-v1"] = (
        INSPIRATION_MEMORY_SNAPSHOT_VERSION
    )
    snapshot_id: Identifier
    project_id: Identifier
    event_ids: Annotated[tuple[Identifier, ...], Field(max_length=10_000)]
    positive_anchors: Annotated[
        tuple[LiteratureAnchorV2, ...], Field(max_length=256)
    ] = ()
    negative_anchors: Annotated[
        tuple[LiteratureAnchorV2, ...], Field(max_length=256)
    ] = ()
    successful_queries: Annotated[
        tuple[RememberedQueryV1, ...], Field(max_length=2_000)
    ] = ()
    unsuccessful_queries: Annotated[
        tuple[RememberedQueryV1, ...], Field(max_length=2_000)
    ] = ()
    evidence_assessments: Annotated[
        tuple[EvidenceAssessmentMemoryV1, ...], Field(max_length=2_000)
    ] = ()
    expert_corrections: Annotated[
        tuple[ExpertCorrectionMemoryV1, ...], Field(max_length=1_000)
    ] = ()
    downstream_outcomes: Annotated[
        tuple[DownstreamOutcomeMemoryV1, ...], Field(max_length=2_000)
    ] = ()

    @model_validator(mode="after")
    def validate_snapshot(self) -> InspirationMemorySnapshotV1:
        if tuple(sorted(set(self.event_ids))) != self.event_ids:
            raise ValueError("snapshot event IDs must be sorted and unique")
        expected = deterministic_id(
            "memory-snapshot",
            self.model_dump(mode="json", exclude={"snapshot_id"}),
        )
        if self.snapshot_id != expected:
            raise ValueError("memory snapshot ID does not match canonical content")
        return self


class InspirationMemoryStore:
    """Small append-only SQLite repository with artifact verification."""

    def __init__(
        self,
        database_path: Path | str,
        *,
        artifact_verifier: Callable[[ArtifactPointerV1], bool],
    ) -> None:
        if not callable(artifact_verifier):
            raise TypeError("artifact_verifier must be callable")
        path = Path(database_path).expanduser()
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path.resolve()
        self._artifact_verifier = artifact_verifier
        self._connection = sqlite3.connect(self.path)
        self._connection.execute("PRAGMA journal_mode=WAL")
        self._connection.execute("PRAGMA foreign_keys=ON")
        self._connection.execute(
            """
            CREATE TABLE IF NOT EXISTS inspiration_memory_event_v1 (
                event_id TEXT PRIMARY KEY,
                project_id TEXT NOT NULL,
                payload_kind TEXT NOT NULL,
                canonical_json BLOB NOT NULL,
                canonical_sha256 TEXT NOT NULL
            ) STRICT
            """
        )
        self._connection.execute(
            """
            CREATE INDEX IF NOT EXISTS inspiration_memory_project_v1
            ON inspiration_memory_event_v1(project_id, event_id)
            """
        )
        self._connection.commit()

    def __enter__(self) -> InspirationMemoryStore:
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()

    def close(self) -> None:
        self._connection.close()

    def append(self, event: InspirationMemoryEventV1) -> bool:
        """Insert once; identical retries are no-ops and collisions fail."""

        if not isinstance(event, InspirationMemoryEventV1):
            raise TypeError("event must be InspirationMemoryEventV1")
        if not self._artifact_verifier(event.source_artifact):
            raise InspirationMemoryError(
                "SOURCE_ARTIFACT_UNVERIFIED",
                "memory event source artifact failed URI/SHA verification",
            )
        payload = canonical_json_bytes(event)
        digest = canonical_sha256(event)
        existing = self._connection.execute(
            """
            SELECT canonical_json, canonical_sha256
            FROM inspiration_memory_event_v1
            WHERE event_id = ?
            """,
            (event.event_id,),
        ).fetchone()
        if existing is not None:
            existing_bytes = bytes(existing[0])
            if existing_bytes != payload or existing[1] != digest:
                raise InspirationMemoryError(
                    "EVENT_ID_COLLISION",
                    "existing memory event differs from the submitted canonical bytes",
                )
            return False
        with self._connection:
            self._connection.execute(
                """
                INSERT INTO inspiration_memory_event_v1(
                    event_id, project_id, payload_kind,
                    canonical_json, canonical_sha256
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (
                    event.event_id,
                    event.project_id,
                    event.payload.kind,
                    payload,
                    digest,
                ),
            )
        return True

    def events(self, project_id: str) -> tuple[InspirationMemoryEventV1, ...]:
        rows = self._connection.execute(
            """
            SELECT canonical_json, canonical_sha256
            FROM inspiration_memory_event_v1
            WHERE project_id = ?
            ORDER BY event_id
            """,
            (project_id,),
        ).fetchall()
        result: list[InspirationMemoryEventV1] = []
        for raw, expected_digest in rows:
            payload = bytes(raw)
            event = InspirationMemoryEventV1.model_validate_json(payload)
            if canonical_sha256(event) != expected_digest:
                raise InspirationMemoryError(
                    "MEMORY_DATABASE_DRIFT",
                    "stored memory event digest differs from canonical bytes",
                )
            result.append(event)
        return tuple(result)

    def snapshot(self, project_id: str) -> InspirationMemorySnapshotV1:
        return compile_memory_snapshot_v1(project_id, self.events(project_id))


def make_memory_event_v1(
    *,
    project_id: str,
    source_run_id: str,
    context_id: str,
    source_artifact: ArtifactPointerV1,
    payload: MemoryPayloadV1,
) -> InspirationMemoryEventV1:
    """Construct one event with a canonical idempotency key."""

    values = {
        "schema_version": INSPIRATION_MEMORY_EVENT_VERSION,
        "project_id": project_id,
        "source_run_id": source_run_id,
        "context_id": context_id,
        "source_artifact": source_artifact,
        "payload": payload,
    }
    return InspirationMemoryEventV1(
        event_id=deterministic_id("memory-event", values),
        **values,
    )


def compile_memory_snapshot_v1(
    project_id: str,
    events: tuple[InspirationMemoryEventV1, ...],
) -> InspirationMemorySnapshotV1:
    """Compile deterministic cross-run memory without adding new claims."""

    ordered = tuple(sorted(events, key=lambda item: item.event_id))
    if any(event.project_id != project_id for event in ordered):
        raise InspirationMemoryError(
            "PROJECT_MEMORY_MISMATCH",
            "snapshot input contains an event from another project",
        )
    positive: dict[str, LiteratureAnchorV2] = {}
    negative: dict[str, LiteratureAnchorV2] = {}
    successful: list[RememberedQueryV1] = []
    unsuccessful: list[RememberedQueryV1] = []
    evidence: list[EvidenceAssessmentMemoryV1] = []
    corrections: list[ExpertCorrectionMemoryV1] = []
    downstream: list[DownstreamOutcomeMemoryV1] = []
    for event in ordered:
        payload = event.payload
        if isinstance(payload, PaperAnchorMemoryV1):
            anchor = LiteratureAnchorV2(
                provider_record_id=payload.provider_record_id,
                title=payload.title,
                polarity=payload.polarity,
            )
            key = payload.provider_record_id.casefold()
            target = positive if payload.polarity is AnchorPolarity.POSITIVE else negative
            opposite = negative if payload.polarity is AnchorPolarity.POSITIVE else positive
            if key in opposite:
                raise InspirationMemoryError(
                    "CONFLICTING_PAPER_ANCHOR",
                    "one paper cannot be both a positive and negative anchor",
                )
            target[key] = anchor
        elif isinstance(payload, QueryOutcomeMemoryV1):
            remembered = RememberedQueryV1(
                event_id=event.event_id,
                provider_id=payload.provider_id,
                provider_query_id=payload.provider_query_id,
                family=payload.family,
                query_text=payload.query_text,
                outcome=payload.outcome,
                raw_hit_count=payload.raw_hit_count,
                eligible_evidence_count=payload.eligible_evidence_count,
            )
            (
                successful
                if payload.outcome is QueryMemoryOutcome.SUCCEEDED_WITH_EVIDENCE
                else unsuccessful
            ).append(remembered)
        elif isinstance(payload, EvidenceAssessmentMemoryV1):
            evidence.append(payload)
        elif isinstance(payload, ExpertCorrectionMemoryV1):
            corrections.append(payload)
        elif isinstance(payload, DownstreamOutcomeMemoryV1):
            downstream.append(payload)
        else:  # pragma: no cover - the discriminated union is closed above
            raise AssertionError("unsupported memory payload")

    values = {
        "schema_version": INSPIRATION_MEMORY_SNAPSHOT_VERSION,
        "project_id": project_id,
        "event_ids": tuple(item.event_id for item in ordered),
        "positive_anchors": tuple(positive[key] for key in sorted(positive)),
        "negative_anchors": tuple(negative[key] for key in sorted(negative)),
        "successful_queries": tuple(successful),
        "unsuccessful_queries": tuple(unsuccessful),
        "evidence_assessments": tuple(evidence),
        "expert_corrections": tuple(corrections),
        "downstream_outcomes": tuple(downstream),
    }
    return InspirationMemorySnapshotV1(
        snapshot_id=deterministic_id("memory-snapshot", values),
        **values,
    )


def reviewed_hints_from_memory_v1(
    snapshot: InspirationMemorySnapshotV1,
) -> ReviewedQueryHintsV2:
    """Project only human-reviewed memory into executable context hints."""

    mechanism: list[str] = []
    operation: list[str] = []
    structure: list[str] = []
    exclusion: list[str] = []
    for correction in snapshot.expert_corrections:
        target = {
            ExpertCorrectionKind.ADD_MECHANISM: mechanism,
            ExpertCorrectionKind.ADD_OPERATION: operation,
            ExpertCorrectionKind.ADD_STRUCTURE: structure,
            ExpertCorrectionKind.EXCLUDE_TERM: exclusion,
        }[correction.correction_kind]
        target.append(correction.term)
    return ReviewedQueryHintsV2(
        mechanism_terms=_unique_reviewed_terms(mechanism),
        operation_terms=_unique_reviewed_terms(operation),
        structure_terms=_unique_reviewed_terms(structure),
        anchors=(*snapshot.positive_anchors, *snapshot.negative_anchors),
        exclusion_terms=_unique_reviewed_terms(exclusion),
    )


def iter_memory_payloads(
    snapshot: InspirationMemorySnapshotV1,
) -> Iterator[StrictModel]:
    """Yield snapshot payloads for reporting without changing their meaning."""

    yield from snapshot.evidence_assessments
    yield from snapshot.expert_corrections
    yield from snapshot.downstream_outcomes


def _unique_reviewed_terms(values: list[str]) -> tuple[str, ...]:
    observed: set[str] = set()
    result: list[str] = []
    for value in values:
        key = " ".join(value.split()).casefold()
        if key not in observed:
            observed.add(key)
            result.append(" ".join(value.split()))
    return tuple(result)
