from __future__ import annotations

import hashlib
import inspect
import json
from collections.abc import Iterator
from typing import Any

import material_agent.inspiration as inspiration
from pydantic import BaseModel

from material_agent.inspiration import (
    PUBLIC_CONTRACT_MODELS,
    PUBLIC_POLICY_MODELS,
)


SCHEMA_SHA256 = {
    "ArtifactPointerV1": "110be326948fa3866eacd0678a1b86fc7c96e3ca7b978aee1a01a97fc949a126",
    "BridgePacketV1": "2ebcc2f76ec15b5612c0337ba684823252895cc3d1734dc2dbd8e51e873f705d",
    "BridgeRuleV1": "7d603c79d789b0497a0a8744e07c8de6bfd9194887e2b66cef43391ab7cdb6c3",
    "BridgeSearchPolicyV1": "3b487a8fb8de10354b80182620a5414f99bea1cc6e2946ff7adec4beea15025c",
    "CandidateRouteRefV1": "60f286c971b61a0e1d86b267e1f064a66a1cfefdd2673611bf06b905ad2dbe43",
    "CandidateScoresV1": "29b310617e39afacc1a70c0fdab966902f3e62f260161877a5c2ac90b90f10f7",
    "ComponentSnapshotV1": "3c797afe514fc01a78d1a1d51b88811557f37883a86f76a4fc6977d77a0518c5",
    "CostLedgerV1": "eaac459d4599e9ce3efa5b76eea9f8e5def09a4864e03c3728d9a7e31ccafb69",
    "EmbeddingBudgetV1": "a0114b0e1b0c20b9341dce14584582c3c06ead746858dac25036e45af27c3172",
    "EvidenceCardV1": "8eecc400000f84dedfbc3dcbcfb04cb2b2fe168235aa58e14c72e09596dd4647",
    "FetchBudgetV1": "7c820a4dcbe677353fefd7bc366007a2d31644475e0988b3cb8483840611906b",
    "InspirationBundleV1": "cfa85276a0f7437d1da852e165fba8582816b509e0575ef2fa70b330933a2445",
    "InspirationCandidateV1": "250def5133df234216858b7f34f6cfb2f96a43672eb841ab1c2872f829c1f062",
    "InspirationInputV1": "52fb541d2420a69b2a66eb8dd592fe8d0f80b903eb44a018d51168179e726e1b",
    "InspirationPolicyV1": "312a4b2d089c7f828f16ae5847bfc8c5f2a27152a30bf1ae38dff249ce591eb5",
    "InspirationStageResultV1": "3206923eec979f517e92fcb50102b1532659e6bba30a52c35f16ed39bf0c0619",
    "LLMBudgetV1": "3e3f0a3aa2c599683838e130a9be1aad68b7d5cfab1e63c49ee20d1edc1e20e0",
    "ParentCandidateRefV1": "8da932db28b1327f56725260142f2ae82f0a1674cc09da1cfc6331a6cf087605",
    "PassageBudgetV1": "3528b8241f6d845338221cca029346b64e4fed090f640197e1c10d7ac27d9782",
    "PassageLocatorV1": "1b4a7501f8f44503b6fa101935ac2c262222e7dc0429e04cb3f37a778be7c00b",
    "PassageV1": "ac7046d2bf9b6f36caa398360aab05d09b94c96f82a41304994fa71c42b4ff97",
    "PassageVectorV1": "2541a08dffa0a886e6265f4c837b3eb98131ae2b049db392552cc90d2c4b59c9",
    "RuntimeBudgetV1": "41f0920465d2da4db31ce6cbcad8a749611abc036b5aca2ec390bad39be36806",
    "SearchBudgetV1": "5c33c7db57d5161a2abf7c6cbf07605645cca76d1d3d2ccb0bf65923506050a7",
    "SearchHitV1": "75cbb2d6dd1a2c44aaf6869340ae5c0fa81bf107dbf3ae728b832b105a9862f3",
    "SearchQueryV1": "6a3db425b036f59dede7632440a0443619883f4429a6070e21046da3f0091b78",
    "SelectionPolicyV1": "924a041e6840c2d1f3c819545cc0d1becc6cd6e2c53f0e763d36448dc33f7e7e",
    "SubstitutionParametersV1": "f1c714d266269db075334e1afb096590de238a1d475e4e9f1eabe07f355e9c3c",
    "TagDefinitionV1": "2af0f1825d5ee088e9a4d6b369c4609770a2cf03ea622a5c27a38d24ef16e476",
    "TagEdgeV1": "1866ba256617ffbeff59266e825f179836eea281b16193faa58a94f3b76fbba7",
    "TagGraphV1": "77d35784d1b96074581301ef1c6c4c439a864b5f82d0ef460b4ec4386b52a4bc",
    "TransformationBudgetV1": "ae1fb7ae3c853635549e2029c4ca953714fb0369d78c9db35c9ac1be4e2551ff",
    "TransformationPlanV1": "9347b5d16f338f88fe1ce050c30af31b7b483bbdb2ab4ba5c8b1262618141fd3",
    "ValidationCheckV1": "dcfb057d3c56df5fa553512bfd7d3a828ce1166fc531b96b1bdea2336b7bec86",
}


def canonical_schema_sha256(schema: dict[str, Any]) -> str:
    payload = json.dumps(
        schema,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def walk_json(value: Any) -> Iterator[Any]:
    yield value
    if isinstance(value, dict):
        for child in value.values():
            yield from walk_json(child)
    elif isinstance(value, list):
        for child in value:
            yield from walk_json(child)


def test_public_contract_schemas_are_frozen() -> None:
    models = PUBLIC_CONTRACT_MODELS + PUBLIC_POLICY_MODELS
    actual = {
        model.__name__: canonical_schema_sha256(model.model_json_schema())
        for model in models
    }
    assert actual == SCHEMA_SHA256


def test_every_exported_concrete_model_is_registered() -> None:
    exported = {
        name
        for name in inspiration.__all__
        if name != "StrictModel"
        and inspect.isclass(value := getattr(inspiration, name))
        and issubclass(value, BaseModel)
    }
    registered = {
        model.__name__ for model in PUBLIC_CONTRACT_MODELS + PUBLIC_POLICY_MODELS
    }
    assert exported == registered


def test_every_object_contract_rejects_unknown_fields() -> None:
    for model in PUBLIC_CONTRACT_MODELS + PUBLIC_POLICY_MODELS:
        schema = model.model_json_schema()
        for node in walk_json(schema):
            if isinstance(node, dict) and node.get("type") == "object":
                assert node.get("additionalProperties") is False, (
                    f"{model.__name__} contains an open object schema"
                )


def test_schema_excludes_external_originality_conclusion_fields() -> None:
    schemas = {
        model.__name__: model.model_json_schema()
        for model in PUBLIC_CONTRACT_MODELS + PUBLIC_POLICY_MODELS
    }
    schema_text = json.dumps(schemas, ensure_ascii=False, sort_keys=True).lower()
    for forbidden in (
        "novelty",
        "is_novel",
        "prior_art",
        "prior-art",
        "originality",
        "unprecedented",
    ):
        assert forbidden not in schema_text


def test_generated_outputs_have_a_fixed_scientific_boundary() -> None:
    by_name = {
        model.__name__: model.model_json_schema()
        for model in PUBLIC_CONTRACT_MODELS
    }
    for name in (
        "TransformationPlanV1",
        "InspirationCandidateV1",
        "InspirationBundleV1",
        "InspirationStageResultV1",
    ):
        field = by_name[name]["properties"]["scientific_conclusion"]
        assert field["const"] is False
        assert field["default"] is False

    scope = by_name["EvidenceCardV1"]["properties"]["evidence_scope"]
    assert scope["const"] == "SOURCE_ASSERTION"
