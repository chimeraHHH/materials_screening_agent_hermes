from __future__ import annotations

import hashlib
import json
from collections.abc import Iterator
from typing import Any

from material_agent.inspiration.transformations import (
    SubstitutionExecutionRequestV1,
    SubstitutionRegistryV1,
    SubstitutionRuleV1,
)


RUNTIME_TRANSFORMATION_MODELS = (
    SubstitutionRuleV1,
    SubstitutionRegistryV1,
    SubstitutionExecutionRequestV1,
)
SCHEMA_SHA256 = {
    "SubstitutionRuleV1": "2d3e60b9873b7ffa8749c29e0247ded453c6117cd28a6d3b8dbe62ed752d25ca",
    "SubstitutionRegistryV1": "41e2104fddddd399a6807f0f8223b1f0b23428ed69f5fe14096360dd43be6690",
    "SubstitutionExecutionRequestV1": "4289444de963b85da8fb121ecfc9197c82845ad5709cc44ca393113b460e5bcc",
}


def _schema_sha256(schema: dict[str, Any]) -> str:
    payload = json.dumps(
        schema,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _walk_json(value: Any) -> Iterator[Any]:
    yield value
    if isinstance(value, dict):
        for child in value.values():
            yield from _walk_json(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk_json(child)


def test_runtime_transformation_schemas_are_frozen_and_closed() -> None:
    actual = {
        model.__name__: _schema_sha256(model.model_json_schema())
        for model in RUNTIME_TRANSFORMATION_MODELS
    }
    assert actual == SCHEMA_SHA256

    for model in RUNTIME_TRANSFORMATION_MODELS:
        for node in _walk_json(model.model_json_schema()):
            if isinstance(node, dict) and node.get("type") == "object":
                assert node.get("additionalProperties") is False


def test_runtime_transformation_schema_has_no_originality_claim() -> None:
    text = json.dumps(
        {
            model.__name__: model.model_json_schema()
            for model in RUNTIME_TRANSFORMATION_MODELS
        },
        ensure_ascii=False,
        sort_keys=True,
    ).casefold()
    for forbidden in (
        "novelty",
        "is_novel",
        "prior_art",
        "prior-art",
        "originality",
        "unprecedented",
    ):
        assert forbidden not in text
