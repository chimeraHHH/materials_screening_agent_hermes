from __future__ import annotations

import inspect

from material_agent.research.flatband_workflow import (
    PilotRoundArtifactsV3,
    assemble_formal_pilot_round_v3,
    assert_formal_pilot_round_artifacts_v3,
)


def test_private_pilot_bundle_never_embeds_blind_key_or_hidden_reasoning() -> None:
    fields = PilotRoundArtifactsV3.model_fields
    assert fields["source_catalog_checkpoint"].is_required()
    assert "blind_key" not in fields
    assert fields["blind_key_commitment_sha256"].is_required()
    assert fields["blind_key_embedded"].default is False
    assert fields["hidden_reasoning_persisted"].default is False
    assert fields["private_custody_required"].default is True
    assert fields["public_repository_release_allowed"].default is False


def test_pilot_assembler_does_not_accept_caller_created_final_artifacts() -> None:
    parameters = inspect.signature(assemble_formal_pilot_round_v3).parameters
    forbidden = {
        "reviewer_manifests",
        "private_identity_maps",
        "final_gold_release",
        "agreement_release",
        "agreement_gate",
    }
    assert not forbidden & set(parameters)
    assert {
        "source_catalog_checkpoint",
        "raw_annotations",
        "adjudications",
        "raw_duplicate_partitions",
        "duplicate_partition_adjudications",
        "blind_key",
    } <= set(parameters)


def test_pilot_bundle_verifier_requires_ephemeral_blind_key() -> None:
    parameters = inspect.signature(
        assert_formal_pilot_round_artifacts_v3
    ).parameters
    assert parameters["blind_key"].default is inspect.Parameter.empty
    assert parameters["prior_r1_bundle"].default is None
    assert parameters["prior_r1_blind_key"].default is None
