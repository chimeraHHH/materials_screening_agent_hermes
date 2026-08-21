from __future__ import annotations

from pathlib import Path
from typing import Literal

import pytest

from material_agent.inspiration.models import (
    StrictModel,
    canonical_sha256,
    deterministic_id,
)
from material_agent.research.flatband_artifact_store import (
    PrivateArtifactEnvelopeV1,
    read_private_artifact_envelope_v1,
    seal_private_artifact_envelope_v1,
    write_private_artifact_envelope_v1,
)
from material_agent.research.flatband_contracts import RawExpertAnnotationV1


class _SyntheticPrivateArtifactV1(StrictModel):
    schema_version: Literal["synthetic-private-artifact-v1"] = (
        "synthetic-private-artifact-v1"
    )
    artifact_id: str
    artifact_sha256: str
    runtime_inputs: dict[str, object]


def _synthetic_artifact(
    runtime_inputs: dict[str, object],
) -> _SyntheticPrivateArtifactV1:
    return _SyntheticPrivateArtifactV1(
        artifact_id="synthetic-private-artifact",
        artifact_sha256="a" * 64,
        runtime_inputs=runtime_inputs,
    )


def _raw_annotation() -> RawExpertAnnotationV1:
    from tests.unit.test_flatband_research_contracts import _annotation

    return _annotation()


def test_private_artifact_round_trip_is_atomic_owner_only(tmp_path: Path) -> None:
    annotation = _raw_annotation()
    envelope = seal_private_artifact_envelope_v1(
        annotation,
        id_field="annotation_id",
        sha_field="annotation_sha256",
        sealed_at="2026-08-10T12:00:00+08:00",
    )
    target = write_private_artifact_envelope_v1(
        tmp_path / "custody" / "annotation.json", envelope
    )
    assert target.stat().st_mode & 0o777 == 0o600
    rebuilt_envelope, rebuilt_annotation = read_private_artifact_envelope_v1(
        target, model_type=RawExpertAnnotationV1
    )
    assert rebuilt_envelope == envelope
    assert rebuilt_annotation == annotation
    with pytest.raises(FileExistsError):
        write_private_artifact_envelope_v1(target, envelope)


def test_private_envelope_rejects_secret_named_payload_fields() -> None:
    annotation = _raw_annotation()
    envelope = seal_private_artifact_envelope_v1(
        annotation,
        id_field="annotation_id",
        sha_field="annotation_sha256",
        sealed_at="2026-08-10T12:00:00+08:00",
    )
    tampered = envelope.model_copy(
        update={"payload": {**envelope.payload, "blind_key": "forbidden"}}
    )
    with pytest.raises(ValueError, match="forbidden secret"):
        PrivateArtifactEnvelopeV1.model_validate(
            tampered.model_dump(mode="python")
        )


@pytest.mark.parametrize(
    "runtime_inputs",
    (
        {"ephemeral_blinding_key": "A" * 32},
        {"main_gold": {"expert_annotation_keys": {"reviewer-a": "B" * 32}}},
        {"release": {"authority": {"keys": {"LICENSE": "C" * 32}}}},
        {"scientific_reviewer": {"decision_keys": {"reviewer-a": "D" * 32}}},
        {"key_material": "E" * 32},
    ),
)
def test_custom_artifact_rejects_normalized_secret_paths(
    runtime_inputs: dict[str, object],
) -> None:
    with pytest.raises(ValueError, match="forbidden secret"):
        seal_private_artifact_envelope_v1(
            _synthetic_artifact(runtime_inputs),
            id_field="artifact_id",
            sha_field="artifact_sha256",
            sealed_at="2026-08-10T12:00:00+08:00",
        )


def test_fully_readdressed_envelope_rejects_authority_key_map() -> None:
    envelope = seal_private_artifact_envelope_v1(
        _synthetic_artifact({"safe": True}),
        id_field="artifact_id",
        sha_field="artifact_sha256",
        sealed_at="2026-08-10T12:00:00+08:00",
    )
    payload = {
        **envelope.payload,
        "release": {"authority": {"keys": {"LICENSE": "C" * 32}}},
    }
    values = {
        **envelope.model_dump(mode="python"),
        "payload": payload,
        "payload_sha256": canonical_sha256(payload),
    }
    semantic = {
        key: value
        for key, value in values.items()
        if key not in {"envelope_id", "envelope_sha256"}
    }
    digest = canonical_sha256(semantic)
    values.update(
        envelope_sha256=digest,
        envelope_id=deterministic_id(
            "private-artifact-envelope-v1", {"envelope_sha256": digest}
        ),
    )
    with pytest.raises(ValueError, match="forbidden secret"):
        PrivateArtifactEnvelopeV1.model_validate(values)


def test_commitments_signatures_and_negative_key_flags_remain_storable() -> None:
    envelope = seal_private_artifact_envelope_v1(
        _synthetic_artifact(
            {
                "authority_key_commitment_sha256": "b" * 64,
                "review_signature_hmac_sha256": "c" * 64,
                "annotation_key_material_included": False,
                "expert_annotation_keys_verified_not_stored": True,
                "external_key_custody_attestation": "NOT_PROVIDED",
            }
        ),
        id_field="artifact_id",
        sha_field="artifact_sha256",
        sealed_at="2026-08-10T12:00:00+08:00",
    )
    assert envelope.payload["runtime_inputs"]["authority_key_commitment_sha256"] == (
        "b" * 64
    )


def test_private_artifact_write_rejects_symlink_parent(tmp_path: Path) -> None:
    annotation = _raw_annotation()
    envelope = seal_private_artifact_envelope_v1(
        annotation,
        id_field="annotation_id",
        sha_field="annotation_sha256",
        sealed_at="2026-08-10T12:00:00+08:00",
    )
    real_parent = tmp_path / "real-parent"
    real_parent.mkdir()
    linked_parent = tmp_path / "linked-parent"
    linked_parent.symlink_to(real_parent, target_is_directory=True)

    with pytest.raises(ValueError, match="cannot traverse a symlink"):
        write_private_artifact_envelope_v1(linked_parent / "annotation.json", envelope)
    assert not (real_parent / "annotation.json").exists()


def test_private_artifact_write_rejects_symlink_target(tmp_path: Path) -> None:
    annotation = _raw_annotation()
    envelope = seal_private_artifact_envelope_v1(
        annotation,
        id_field="annotation_id",
        sha_field="annotation_sha256",
        sealed_at="2026-08-10T12:00:00+08:00",
    )
    real_target = tmp_path / "real.json"
    real_target.write_text("sentinel", encoding="utf-8")
    linked_target = tmp_path / "linked.json"
    linked_target.symlink_to(real_target)

    with pytest.raises(ValueError, match="cannot traverse a symlink"):
        write_private_artifact_envelope_v1(linked_target, envelope, overwrite=True)
    assert real_target.read_text(encoding="utf-8") == "sentinel"


def test_private_artifact_read_rejects_symlink_parent_and_target(
    tmp_path: Path,
) -> None:
    annotation = _raw_annotation()
    envelope = seal_private_artifact_envelope_v1(
        annotation,
        id_field="annotation_id",
        sha_field="annotation_sha256",
        sealed_at="2026-08-10T12:00:00+08:00",
    )
    real_parent = tmp_path / "real-parent"
    real_target = write_private_artifact_envelope_v1(
        real_parent / "annotation.json", envelope
    )
    linked_parent = tmp_path / "linked-parent"
    linked_parent.symlink_to(real_parent, target_is_directory=True)
    linked_target = tmp_path / "linked.json"
    linked_target.symlink_to(real_target)

    for attacked_path in (linked_parent / "annotation.json", linked_target):
        with pytest.raises(ValueError, match="cannot traverse a symlink"):
            read_private_artifact_envelope_v1(
                attacked_path, model_type=RawExpertAnnotationV1
            )
