from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from material_agent.inspiration.literature_budget import LiteratureQueryFamily
from material_agent.inspiration.models import ArtifactPointerV1
from material_agent.inspiration.query_context import AnchorPolarity
from material_agent.inspiration.research_memory import (
    EvidenceAssessmentMemoryV1,
    EvidenceMemoryStrength,
    ExpertCorrectionKind,
    ExpertCorrectionMemoryV1,
    InspirationMemoryError,
    InspirationMemoryStore,
    PaperAnchorMemoryV1,
    QueryMemoryOutcome,
    QueryOutcomeMemoryV1,
    compile_memory_snapshot_v1,
    make_memory_event_v1,
    reviewed_hints_from_memory_v1,
)


def _artifact() -> ArtifactPointerV1:
    return ArtifactPointerV1(
        uri="artifact://stages/inspiration/run/memory-source.json",
        sha256="a" * 64,
        size_bytes=128,
        media_type="application/json",
    )


def _event(payload):
    return make_memory_event_v1(
        project_id="materials-project",
        source_run_id="run-1",
        context_id="query-context-123",
        source_artifact=_artifact(),
        payload=payload,
    )


def test_store_is_append_only_idempotent_and_snapshot_is_replayable(
    tmp_path: Path,
) -> None:
    events = (
        _event(
            PaperAnchorMemoryV1(
                provider_record_id="10.1000/positive",
                title="Reviewed TiS2 anchor",
                polarity=AnchorPolarity.POSITIVE,
                rationale="Direct material evidence",
                reviewer_id="reviewer-1",
            )
        ),
        _event(
            QueryOutcomeMemoryV1(
                provider_id="semantic-scholar",
                provider_query_id="query-material-tis2",
                family=LiteratureQueryFamily.MATERIAL,
                query_text="TiS2 titanium disulfide electronic flat band",
                outcome=QueryMemoryOutcome.SUCCEEDED_WITH_EVIDENCE,
                raw_hit_count=10,
                eligible_evidence_count=3,
            )
        ),
        _event(
            EvidenceAssessmentMemoryV1(
                candidate_id="candidate-tis2",
                evidence_card_ids=("evidence-1",),
                strength=EvidenceMemoryStrength.SUPPORTED,
                assessment_basis="Grounded citations closed to the supplied passage",
                assessed_by="DEEPSEEK_GROUNDED_RERANKER",
            )
        ),
    )
    with InspirationMemoryStore(
        tmp_path / "memory.sqlite3",
        artifact_verifier=lambda pointer: pointer == _artifact(),
    ) as store:
        assert [store.append(event) for event in events] == [True, True, True]
        assert store.append(events[0]) is False
        snapshot = store.snapshot("materials-project")

    assert snapshot.event_ids == tuple(sorted(event.event_id for event in events))
    assert snapshot.positive_anchors[0].provider_record_id == "10.1000/positive"
    assert snapshot.successful_queries[0].eligible_evidence_count == 3
    assert not hasattr(snapshot.evidence_assessments[0], "scientific_conclusion")
    assert compile_memory_snapshot_v1("materials-project", tuple(reversed(events))) == snapshot


def test_only_reviewed_memory_becomes_next_run_context_hints() -> None:
    events = (
        _event(
            PaperAnchorMemoryV1(
                provider_record_id="10.1000/negative",
                title="Acoustic-only distractor",
                polarity=AnchorPolarity.NEGATIVE,
                rationale="Not direct evidence for an electronic material",
                reviewer_id="reviewer-1",
            )
        ),
        _event(
            ExpertCorrectionMemoryV1(
                correction_kind=ExpertCorrectionKind.ADD_MECHANISM,
                term="orbital hybridization",
                rationale="Required to express the physical mechanism",
                reviewer_id="reviewer-1",
            )
        ),
        _event(
            ExpertCorrectionMemoryV1(
                correction_kind=ExpertCorrectionKind.EXCLUDE_TERM,
                term="acoustic-only evidence",
                rationale="Prevent cross-domain evidence from becoming direct support",
                reviewer_id="reviewer-1",
            )
        ),
        _event(
            QueryOutcomeMemoryV1(
                provider_id="crossref",
                provider_query_id="failed-query",
                family=LiteratureQueryFamily.BRIDGE,
                query_text="acoustic flat band",
                outcome=QueryMemoryOutcome.NO_MATCH,
                raw_hit_count=0,
                eligible_evidence_count=0,
            )
        ),
    )
    snapshot = compile_memory_snapshot_v1("materials-project", events)

    hints = reviewed_hints_from_memory_v1(snapshot)

    assert hints.mechanism_terms == ("orbital hybridization",)
    assert hints.exclusion_terms == ("acoustic-only evidence",)
    assert hints.anchors[0].polarity is AnchorPolarity.NEGATIVE
    assert "acoustic flat band" not in hints.mechanism_terms


def test_store_rejects_unverified_source_before_database_write(tmp_path: Path) -> None:
    event = _event(
        QueryOutcomeMemoryV1(
            provider_id="crossref",
            provider_query_id="query-1",
            family=LiteratureQueryFamily.MATERIAL,
            query_text="TiS2 electronic flat band",
            outcome=QueryMemoryOutcome.NO_MATCH,
            raw_hit_count=0,
            eligible_evidence_count=0,
        )
    )
    with InspirationMemoryStore(
        tmp_path / "memory.sqlite3",
        artifact_verifier=lambda _pointer: False,
    ) as store:
        with pytest.raises(InspirationMemoryError, match="SOURCE_ARTIFACT_UNVERIFIED"):
            store.append(event)
        assert store.events("materials-project") == ()


def test_database_drift_is_detected_when_snapshot_is_read(tmp_path: Path) -> None:
    database = tmp_path / "memory.sqlite3"
    event = _event(
        PaperAnchorMemoryV1(
            provider_record_id="10.1000/anchor",
            title="Reviewed anchor",
            polarity=AnchorPolarity.POSITIVE,
            rationale="Direct evidence",
            reviewer_id="reviewer-1",
        )
    )
    with InspirationMemoryStore(
        database,
        artifact_verifier=lambda _pointer: True,
    ) as store:
        store.append(event)
        store._connection.execute(
            "UPDATE inspiration_memory_event_v1 SET canonical_sha256 = ?",
            ("0" * 64,),
        )
        store._connection.commit()
        with pytest.raises(InspirationMemoryError, match="MEMORY_DATABASE_DRIFT"):
            store.events("materials-project")


def test_store_schema_is_append_only_at_the_sql_boundary(tmp_path: Path) -> None:
    database = tmp_path / "memory.sqlite3"
    with InspirationMemoryStore(
        database,
        artifact_verifier=lambda _pointer: True,
    ):
        pass
    connection = sqlite3.connect(database)
    columns = {
        row[1]
        for row in connection.execute(
            "PRAGMA table_info(inspiration_memory_event_v1)"
        ).fetchall()
    }
    connection.close()
    assert columns == {
        "event_id",
        "project_id",
        "payload_kind",
        "canonical_json",
        "canonical_sha256",
    }
