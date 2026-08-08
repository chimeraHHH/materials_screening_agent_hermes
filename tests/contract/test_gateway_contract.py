from __future__ import annotations

import hashlib
import inspect
import json
from collections.abc import Iterator
from typing import Any

import material_agent.gateway as gateway
from material_agent.gateway import PUBLIC_GATEWAY_MODELS
from pydantic import BaseModel


SCHEMA_SHA256 = {
    "InspirationBudgetV1": "721f322e9ab7b1d91c775d0a540a445622b74619d2dc81adb1cb6848706a5450",
    "InspirationConstraintsV1": "95663ac69b68c3280da56d806da145b78dd5ffbe693b9b4137b9cde38f7d3830",
    "InspirationRunRequestV1": "8e898609da3205e70f39601bb2093ae93ca87de4d3350b8febf6afdb1f9026eb",
    "ClarificationInteractionV1": "b5f907fadd2c557a9549f9329aa32cd26bfc6f030ef790912a45099e0bac293a",
    "ApprovalInteractionV1": "912b138ba37ba4edc936601ea0ddd302d9574f39701e17a7d7242639c870ac13",
    "RetryInteractionV1": "edb90c8ffc39488b60e03d2e22d9957bd256406048ededb7ef46673ad1e8a023",
    "ResumeInteractionV1": "0a03e8bc17feb23d17f0297d1a0d78059cc79d73a7685968f8a9c15add7269a6",
    "AnswerActionV1": "ba802771c5b17f0b55def13bd5d1756f16c9404d3d7aeea25db83f0a48dfd410",
    "ApproveActionV1": "b3caba93addedb2327d3016cab946d03e703558197dc930b42d6f5d720615719",
    "RejectActionV1": "317d6d71603e1d92147bb5d02e1eb647a4c40f71887019886e18611051ff3392",
    "ResumeActionV1": "d5e17209fb69a511ad6f6399c821c6454ccca38089c11d27f8e3b812d3e5d2ef",
    "RetryActionV1": "d32edd4489ead7954322df889052d140429c1ccec46b599764c82df442b202e2",
    "CancelActionV1": "1478ce3d4ae1ee655bd8a86a2bf0b6993d342cb5f069cfb5dec30f293738d717",
    "MaterialsRunGetRequestV1": "bb124dd1149c2127450df495ab1422b2ba28672121a3294fdbdc3bd3d2c58a82",
    "MaterialsRunActRequestV1": "f2d8353dbaa48099e095dff48c8ea762e60337572311400c70191380bb85b2f5",
    "MaterialsResultGetRequestV1": "c22359a3d4978bf4d974b5eb9026bc983f1a728f50357464e24f206a06e8da38",
    "RunningStateV1": "99e8f9a31ca0ee671cdb200766cea8c48f6491085381f2f2c0a875306061c2ba",
    "InteractionRequiredStateV1": "706c530438629e32797304dd405411aa2146c67d9fc81bedc7cebc0f0026748c",
    "SucceededStateV1": "542153b8b3237d24d3310e43162f7cc436e736470f65ff4ec2e76697a9679100",
    "PartialStateV1": "97a840486b561c6252fb6f68f5a372705efe99e8e12e96508cde6e40a798e613",
    "FailedStateV1": "f7fad52d4393f338700ab5e2931cb72cc4e913befc0e394f6a73dd3426bcb8ac",
    "CancelledStateV1": "a8c1264b695dde35fbf8b044e4f9353791601b9b2e047509a38af9615d867885",
    "EvidenceReferenceV1": "33aec0ab739cdd9d9bb69a32776b92ab815fc32fc8fbcdf0abd788284f78b603",
    "CandidateSummaryV1": "f91b25571bc2b8ce21da9acd4bf0c7a01039ac9d1e5b66bfd660f3a6581a40c3",
    "CostLedgerProjectionV1": "2ad27f1e5cfde76b23e85bd9cfe0dea398130ba727202d847a8b471f17de38f5",
    "InspirationBundleSummaryV1": "04fdcff734cd49861fbe18db18910e480cef6b715b0c9774f552f38ce4ff4638",
    "GatewayResultRecordV1": "ce6321a4137a2ee5e8a674c4132fa3c4953a787a9fde433e31e2115864179328",
    "MaterialsResultViewV1": "78f58bb5de5fdeb24c2c7477412f5b4634bb732ce8f3f5d46e6a2ebd9ec11456",
    "CompanionTransitionV1": "f78a08ee49a036d4c78d2081be4330fb0ab32ac90763c032cee91cffc67cca36",
    "GatewayRunRecordV1": "157230fdf0a45a3bd8ec227e7a2825bd1312fff568c51e3bcc25a38b7de0c5fc",
    "MaterialsRunViewV1": "8817835e0e2e7cbca42c89e2b25b8e8a5390582a6bc4ed75d6525744c9ad9d73",
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


def test_gateway_public_schemas_are_frozen() -> None:
    actual = {
        model.__name__: canonical_schema_sha256(model.model_json_schema())
        for model in PUBLIC_GATEWAY_MODELS
    }
    assert actual == SCHEMA_SHA256


def test_every_exported_gateway_model_is_registered() -> None:
    exported = {
        name
        for name in gateway.__all__
        if inspect.isclass(value := getattr(gateway, name))
        and issubclass(value, BaseModel)
    }
    registered = {model.__name__ for model in PUBLIC_GATEWAY_MODELS}
    assert exported == registered


def test_gateway_object_schemas_are_closed_and_discriminated() -> None:
    for model in PUBLIC_GATEWAY_MODELS:
        for node in walk_json(model.model_json_schema()):
            if isinstance(node, dict) and node.get("type") == "object":
                assert node.get("additionalProperties") is False

    act = gateway.MaterialsRunActRequestV1.model_json_schema()
    view = gateway.MaterialsRunViewV1.model_json_schema()
    assert act["properties"]["action"]["discriminator"]["propertyName"] == "kind"
    assert view["properties"]["state"]["discriminator"]["propertyName"] == "status"


def test_gateway_schema_preserves_scope_and_excludes_originality_judgments() -> None:
    schemas = {
        model.__name__: model.model_json_schema() for model in PUBLIC_GATEWAY_MODELS
    }
    text = json.dumps(schemas, ensure_ascii=False, sort_keys=True).lower()
    for forbidden in (
        "novelty",
        "is_novel",
        "prior_art",
        "prior-art",
        "originality",
        "unprecedented",
    ):
        assert forbidden not in text
    assert "stage_id" not in text

    request = schemas["InspirationRunRequestV1"]["properties"]
    assert request["capability"]["const"] == "inspiration_companion"
    assert "budget" not in request
    constraints = schemas["InspirationConstraintsV1"]["properties"]
    assert constraints["budget"]["$ref"].endswith("/$defs/InspirationBudgetV1")
    budget = schemas["InspirationBudgetV1"]["properties"]
    assert budget["allow_full_pdf"]["const"] is False
    assert budget["allow_expensive_computation"]["const"] is False
    candidate = schemas["CandidateSummaryV1"]["properties"]
    assert candidate["target_property_status"]["const"] == "UNKNOWN"
    bundle = schemas["InspirationBundleSummaryV1"]["properties"]
    assert bundle["scientific_conclusion"]["const"] is False
