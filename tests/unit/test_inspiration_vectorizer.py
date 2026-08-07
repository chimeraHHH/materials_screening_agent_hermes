from __future__ import annotations

import hashlib
import json
import math
import struct

import pytest

from material_agent.inspiration import (
    ArtifactPointerV1,
    ComponentSnapshotV1,
    EmbeddingBudgetV1,
    PassageLocatorKind,
    PassageLocatorV1,
    PassageV1,
)
from material_agent.inspiration.vectorizer import (
    SIGNED_HASHING_IMPLEMENTATION_SHA256,
    SIGNED_HASHING_SNAPSHOT,
    VECTOR_ARTIFACT_MEDIA_TYPE,
    InspirationVectorizationError,
    PassageVectorizationRequest,
    build_embedding_input_bytes,
    tokenize_signed_hashing_v1,
    vectorize_selected_passages,
)


def _artifact(name: str, digest: str = "a") -> ArtifactPointerV1:
    return ArtifactPointerV1(
        uri=f"artifact://inspiration/{name}",
        sha256=digest * 64,
        media_type="application/json",
    )


def _passage(
    passage_id: str = "passage-1",
    *,
    text: str = "Compact localized states suppress dispersion in this lattice.",
    section_heading: str = "Mechanism",
) -> PassageV1:
    normalized_sha256 = hashlib.sha256(text.encode("utf-8")).hexdigest()
    return PassageV1(
        passage_id=passage_id,
        hit_id=f"hit-{passage_id}",
        document_id=f"document-{passage_id}",
        source_artifact=_artifact(f"raw/{passage_id}.json"),
        normalizer=ComponentSnapshotV1(
            component_id="passage-normalizer",
            version="1",
            implementation_sha256="b" * 64,
        ),
        locator=PassageLocatorV1(
            kind=PassageLocatorKind.JSON_PATH,
            selector="$.abstract",
            section_heading=section_heading,
        ),
        text=text,
        char_count=len(text),
        estimated_token_count=8,
        normalized_text_sha256=normalized_sha256,
        matched_tag_ids=("flat-band", "compact-localized-state"),
        lexical_score=0.9,
    )


def _request(
    passage_id: str = "passage-1",
    *,
    text: str = "Compact localized states suppress dispersion in this lattice.",
    tags: tuple[str, ...] = ("flat-band", "compact-localized-state"),
) -> PassageVectorizationRequest:
    return PassageVectorizationRequest(
        passage=_passage(passage_id, text=text),
        title="Destructive interference in layered lattices",
        normalized_tags=tags,
        vector_artifact=f"{passage_id}.f32le",
    )


def _values(artifact_bytes: bytes, dimension: int) -> tuple[float, ...]:
    return struct.unpack(f"<{dimension}f", artifact_bytes)


def test_signed_hashing_is_deterministic_l2_normalized_and_finite() -> None:
    budget = EmbeddingBudgetV1(vector_dimension=64, max_passages=4)

    first = vectorize_selected_passages((_request(),), budget=budget)[0]
    second = vectorize_selected_passages((_request(),), budget=budget)[0]

    assert first == second
    assert first.passage_vector.vectorizer == SIGNED_HASHING_SNAPSHOT
    assert SIGNED_HASHING_IMPLEMENTATION_SHA256 == (
        "dc5cbbf4ae9b9ef7bad3d8104e210035c7cea1b978c4631ac14df8434729e3e4"
    )
    assert first.passage_vector.embedding_input_sha256 == (
        "7b56c04c1e10739db2cdd0eabbb6dadb930ac786a7fa56a8f20d79b81b321c00"
    )
    assert first.passage_vector.vector_sha256 == (
        "6c1cebf08a109392554b31326895b8a648adea4a32f711c7968bc64b7fdda77c"
    )
    assert first.passage_vector.cache_key_sha256 == (
        "97eadb86643817dd1d0d87e47080a0f23421b54b551d46bf3ed138a9d759e75d"
    )
    assert len(first.artifact_bytes) == 64 * 4
    values = _values(first.artifact_bytes, 64)
    assert all(math.isfinite(value) for value in values)
    assert math.sqrt(math.fsum(value * value for value in values)) == pytest.approx(
        1.0, abs=1e-6
    )
    assert first.passage_vector.vector_sha256 == hashlib.sha256(
        first.artifact_bytes
    ).hexdigest()
    assert first.passage_vector.vector_artifact.sha256 == (
        first.passage_vector.vector_sha256
    )
    assert first.passage_vector.vector_artifact.size_bytes == 64 * 4
    assert (
        first.passage_vector.vector_artifact.media_type
        == VECTOR_ARTIFACT_MEDIA_TYPE
    )


def test_embedding_input_is_only_bounded_passage_context_and_tag_order_is_stable() -> None:
    passage = _passage()
    first = build_embedding_input_bytes(
        passage,
        title="Destructive interference in layered lattices",
        normalized_tags=("Flat-Band", "compact-localized-state", "flat-band"),
    )
    second = build_embedding_input_bytes(
        passage,
        title="Destructive interference in layered lattices",
        normalized_tags=("compact-localized-state", "flat-band"),
    )

    assert first == second
    assert json.loads(first) == {
        "normalized_tags": ["compact-localized-state", "flat-band"],
        "section_heading": "Mechanism",
        "selected_passage": passage.text,
        "title": "Destructive interference in layered lattices",
    }
    assert "document" not in first.decode("utf-8")
    assert "fulltext" not in first.decode("utf-8")


def test_request_and_tag_reordering_do_not_change_replay_but_text_does() -> None:
    budget = EmbeddingBudgetV1(vector_dimension=32, max_passages=3)
    first_request = _request("passage-b", tags=("flat-band", "localized-state"))
    second_request = _request("passage-a")

    forward = vectorize_selected_passages(
        (first_request, second_request), budget=budget
    )
    reversed_results = vectorize_selected_passages(
        (second_request, first_request), budget=budget
    )
    assert forward == reversed_results
    assert tuple(result.passage_vector.passage_id for result in forward) == (
        "passage-a",
        "passage-b",
    )

    reordered_tags = vectorize_selected_passages(
        (
            PassageVectorizationRequest(
                passage=first_request.passage,
                title=first_request.title,
                normalized_tags=("localized-state", "flat-band"),
                vector_artifact=first_request.vector_artifact,
            ),
        ),
        budget=budget,
    )[0]
    original = vectorize_selected_passages((first_request,), budget=budget)[0]
    assert reordered_tags == original

    changed = vectorize_selected_passages(
        (
            _request(
                "passage-b",
                text="Compact localized states amplify dispersion in this lattice.",
                tags=("flat-band", "localized-state"),
            ),
        ),
        budget=budget,
    )[0]
    assert changed.passage_vector.embedding_input_sha256 != (
        original.passage_vector.embedding_input_sha256
    )
    assert changed.passage_vector.cache_key_sha256 != (
        original.passage_vector.cache_key_sha256
    )
    assert changed.artifact_bytes != original.artifact_bytes


def test_passage_and_input_token_budgets_fail_closed() -> None:
    with pytest.raises(InspirationVectorizationError) as passage_error:
        vectorize_selected_passages(
            (_request("passage-1"), _request("passage-2")),
            budget=EmbeddingBudgetV1(max_passages=1),
        )
    assert passage_error.value.code == "MAX_PASSAGES_EXCEEDED"

    token_count = len(
        tokenize_signed_hashing_v1(
            "Destructive interference in layered lattices"
        )
    ) + len(tokenize_signed_hashing_v1("Mechanism")) + len(
        tokenize_signed_hashing_v1(_passage().text)
    ) + sum(
        len(tokenize_signed_hashing_v1(tag))
        for tag in ("compact-localized-state", "flat-band")
    )
    with pytest.raises(InspirationVectorizationError) as token_error:
        vectorize_selected_passages(
            (_request(),),
            budget=EmbeddingBudgetV1(max_input_tokens=token_count - 1),
        )
    assert token_error.value.code == "MAX_INPUT_TOKENS_EXCEEDED"

    result = vectorize_selected_passages(
        (_request(),),
        budget=EmbeddingBudgetV1(max_input_tokens=token_count),
    )[0]
    assert result.input_token_count == token_count


def test_dimension_controls_little_endian_artifact_and_cache_identity() -> None:
    vector_8 = vectorize_selected_passages(
        (_request(),), budget=EmbeddingBudgetV1(vector_dimension=8)
    )[0]
    vector_16 = vectorize_selected_passages(
        (_request(),), budget=EmbeddingBudgetV1(vector_dimension=16)
    )[0]

    assert vector_8.passage_vector.dimension == 8
    assert vector_16.passage_vector.dimension == 16
    assert len(vector_8.artifact_bytes) == 32
    assert len(vector_16.artifact_bytes) == 64
    assert struct.pack("<f", _values(vector_8.artifact_bytes, 8)[0]) == (
        vector_8.artifact_bytes[:4]
    )
    assert vector_8.passage_vector.embedding_input_sha256 == (
        vector_16.passage_vector.embedding_input_sha256
    )
    assert vector_8.passage_vector.cache_key_sha256 != (
        vector_16.passage_vector.cache_key_sha256
    )


def test_hashes_and_caller_artifact_pointer_are_replayable_and_checked() -> None:
    generated = vectorize_selected_passages(
        (_request(),), budget=EmbeddingBudgetV1(vector_dimension=32)
    )[0]
    pointer = ArtifactPointerV1(
        uri="artifact://custom/vectors/passage-1.f32le",
        sha256=generated.passage_vector.vector_sha256,
        size_bytes=len(generated.artifact_bytes),
        media_type=VECTOR_ARTIFACT_MEDIA_TYPE,
    )
    replay_request = PassageVectorizationRequest(
        passage=_passage(),
        title="Destructive interference in layered lattices",
        normalized_tags=("compact-localized-state", "flat-band"),
        vector_artifact=pointer,
    )
    replay = vectorize_selected_passages(
        (replay_request,), budget=EmbeddingBudgetV1(vector_dimension=32)
    )[0]

    assert replay.artifact_bytes == generated.artifact_bytes
    assert replay.passage_vector.vector_artifact == pointer
    assert replay.passage_vector.embedding_input_sha256 == hashlib.sha256(
        replay.embedding_input_bytes
    ).hexdigest()

    bad_pointer = pointer.model_copy(update={"sha256": "f" * 64})
    with pytest.raises(InspirationVectorizationError) as error:
        vectorize_selected_passages(
            (
                PassageVectorizationRequest(
                    passage=replay_request.passage,
                    title=replay_request.title,
                    normalized_tags=replay_request.normalized_tags,
                    vector_artifact=bad_pointer,
                ),
            ),
            budget=EmbeddingBudgetV1(vector_dimension=32),
        )
    assert error.value.code == "ARTIFACT_HASH_MISMATCH"


def test_full_document_like_inputs_and_unsafe_artifact_names_are_not_accepted() -> None:
    with pytest.raises(InspirationVectorizationError) as passage_error:
        vectorize_selected_passages(
            (
                PassageVectorizationRequest(
                    passage="whole document",  # type: ignore[arg-type]
                    title="A title",
                    normalized_tags=(),
                    vector_artifact="whole-document.f32le",
                ),
            ),
            budget=EmbeddingBudgetV1(),
        )
    assert passage_error.value.code == "INVALID_PASSAGE"

    with pytest.raises(InspirationVectorizationError) as artifact_error:
        vectorize_selected_passages(
            (
                PassageVectorizationRequest(
                    passage=_passage(),
                    title="A title",
                    normalized_tags=(),
                    vector_artifact="../escape.f32le",
                ),
            ),
            budget=EmbeddingBudgetV1(),
        )
    assert artifact_error.value.code == "UNSAFE_ARTIFACT_NAME"
