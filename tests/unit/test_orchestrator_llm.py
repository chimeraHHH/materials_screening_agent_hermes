from __future__ import annotations

import json
import subprocess
from copy import deepcopy
from typing import Any

import pytest

from material_agent.orchestrator.llm import (
    DEEPSEEK_BASE_URL,
    DEEPSEEK_MODEL_ID,
    DeepSeekProvider,
    EnvironmentOrKeychainSecretResolver,
    LLMProviderError,
    StructuredLLMResponse,
    UrllibJSONTransport,
)
from material_agent.orchestrator.models import LLMCallAudit
from material_agent.orchestrator.parser import (
    LLMRequirementParser,
    OfflineRequirementParser,
    requirement_parser_from_environment,
)
from material_agent.retrieval.models import Requirement


SECRET = "super-secret-llm-test-key"


class StaticSecretResolver:
    def resolve(self) -> str:
        return SECRET


class ScriptedTransport:
    def __init__(self, responses: list[tuple[int, bytes]]) -> None:
        self.responses = list(responses)
        self.calls: list[dict[str, Any]] = []

    def post_json(self, **kwargs):
        self.calls.append(kwargs)
        return self.responses.pop(0)


class FakeProvider:
    name = "fake-llm"
    version = "fake-llm-v1"

    def __init__(self, payload: dict[str, Any]) -> None:
        self.payload = payload
        self.calls: list[dict[str, Any]] = []

    def structured_generate(self, **kwargs) -> StructuredLLMResponse:
        self.calls.append(kwargs)
        return StructuredLLMResponse(
            payload=deepcopy(self.payload),
            audit=_audit(),
        )


def test_deepseek_provider_sends_json_request_and_returns_safe_audit() -> None:
    content = json.dumps(
        {"requirement": {"target_class": "custom"}, "clarification_questions": []}
    )
    transport = ScriptedTransport([_provider_response(content)])
    provider = DeepSeekProvider(
        secret_resolver=StaticSecretResolver(),
        transport=transport,
        retry_base_seconds=0,
    )

    result = provider.structured_generate(
        system_prompt="Return JSON.",
        user_payload={"raw_request": "find Si"},
        prompt_version="prompt-v1",
    )

    assert result.payload["requirement"]["target_class"] == "custom"
    assert result.audit.provider == "deepseek"
    assert result.audit.model_id == DEEPSEEK_MODEL_ID
    assert result.audit.prompt_tokens == 12
    call = transport.calls[0]
    assert call["url"] == f"{DEEPSEEK_BASE_URL}/chat/completions"
    assert call["headers"]["Authorization"] == f"Bearer {SECRET}"
    assert call["payload"]["model"] == DEEPSEEK_MODEL_ID
    assert call["payload"]["response_format"] == {"type": "json_object"}
    assert call["payload"]["thinking"] == {"type": "disabled"}
    assert "reasoning_effort" not in call["payload"]
    assert call["payload"]["stream"] is False
    assert "temperature" not in call["payload"]
    assert SECRET not in json.dumps(result.audit.model_dump(mode="json"))


def test_deepseek_provider_retries_only_retryable_statuses() -> None:
    transport = ScriptedTransport(
        [
            (429, b'{"error":"rate limited"}'),
            _provider_response('{"ok":true}'),
        ]
    )
    sleeps: list[float] = []
    provider = DeepSeekProvider(
        secret_resolver=StaticSecretResolver(),
        transport=transport,
        retry_base_seconds=0.5,
        sleeper=sleeps.append,
    )

    result = provider.structured_generate(
        system_prompt="Return JSON.",
        user_payload={"raw_request": "find Si"},
        prompt_version="prompt-v1",
    )

    assert result.payload == {"ok": True}
    assert len(transport.calls) == 2
    assert sleeps == [0.5]


def test_deepseek_provider_retries_empty_json_content() -> None:
    transport = ScriptedTransport([
        _provider_response(""),
        _provider_response('{"ok":true}'),
    ])
    sleeps: list[float] = []
    provider = DeepSeekProvider(
        secret_resolver=StaticSecretResolver(), transport=transport,
        retry_base_seconds=0.5, sleeper=sleeps.append,
    )

    result = provider.structured_generate(
        system_prompt="Return JSON.", user_payload={"raw_request": "find Si"},
        prompt_version="prompt-v1",
    )

    assert result.payload == {"ok": True}
    assert len(transport.calls) == 2
    assert sleeps == [0.5]
    assert result.audit.thinking_mode == "disabled"


def test_deepseek_provider_redacts_authentication_failure() -> None:
    transport = ScriptedTransport(
        [(401, f'{{"error":"bad {SECRET}"}}'.encode())]
    )
    provider = DeepSeekProvider(
        secret_resolver=StaticSecretResolver(),
        transport=transport,
        retry_base_seconds=0,
    )

    with pytest.raises(LLMProviderError) as raised:
        provider.structured_generate(
            system_prompt="Return JSON.",
            user_payload={"raw_request": "find Si"},
            prompt_version="prompt-v1",
        )

    assert raised.value.category == "AUTHENTICATION_FAILED"
    assert raised.value.retryable is False
    assert SECRET not in str(raised.value)


def test_deepseek_provider_rejects_invalid_json_content() -> None:
    transport = ScriptedTransport([_provider_response("not-json")])
    provider = DeepSeekProvider(
        secret_resolver=StaticSecretResolver(),
        transport=transport,
        retry_base_seconds=0,
    )

    with pytest.raises(LLMProviderError) as raised:
        provider.structured_generate(
            system_prompt="Return JSON.",
            user_payload={"raw_request": "find Si"},
            prompt_version="prompt-v1",
        )

    assert raised.value.category == "INVALID_RESPONSE"
    assert raised.value.retryable is False


def test_urllib_transport_rejects_oversized_response(monkeypatch) -> None:
    class Response:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def read(self, size: int) -> bytes:
            return b"x" * size

    monkeypatch.setattr(
        "material_agent.orchestrator.llm.urllib.request.urlopen",
        lambda *_args, **_kwargs: Response(),
    )

    with pytest.raises(LLMProviderError) as raised:
        UrllibJSONTransport().post_json(
            url=f"{DEEPSEEK_BASE_URL}/chat/completions",
            headers={"Authorization": f"Bearer {SECRET}"},
            payload={"model": DEEPSEEK_MODEL_ID},
            timeout_seconds=1,
            max_response_bytes=8,
        )

    assert raised.value.category == "INVALID_RESPONSE"
    assert SECRET not in str(raised.value)


@pytest.mark.parametrize(
    ("base_url", "model_id"),
    [
        ("http://api.deepseek.com", DEEPSEEK_MODEL_ID),
        ("https://attacker.example", DEEPSEEK_MODEL_ID),
        (DEEPSEEK_BASE_URL, "deepseek-v4-flash"),
    ],
)
def test_deepseek_provider_rejects_unfrozen_endpoint_or_model(
    base_url: str, model_id: str
) -> None:
    with pytest.raises(ValueError):
        DeepSeekProvider(
            secret_resolver=StaticSecretResolver(),
            base_url=base_url,
            model_id=model_id,
        )


def test_secret_resolver_prefers_environment_without_keychain() -> None:
    def forbidden_runner(*_args, **_kwargs):
        raise AssertionError("Keychain must not be called")

    resolver = EnvironmentOrKeychainSecretResolver(
        environment={"MATERIAL_AGENT_LLM_API_KEY": SECRET},
        keychain_service="service",
        keychain_account="account",
        command_runner=forbidden_runner,
    )

    assert resolver.resolve() == SECRET


def test_secret_resolver_reads_keychain_without_shell_or_logging_secret() -> None:
    calls: list[tuple[list[str], dict[str, Any]]] = []

    def runner(command: list[str], **kwargs):
        calls.append((command, kwargs))
        return subprocess.CompletedProcess(command, 0, stdout=f"{SECRET}\n", stderr="")

    resolver = EnvironmentOrKeychainSecretResolver(
        environment={},
        keychain_service="material-screening-agent-llm-api",
        keychain_account="wuleyan",
        command_runner=runner,
    )

    assert resolver.resolve() == SECRET
    command, kwargs = calls[0]
    assert command == [
        "security",
        "find-generic-password",
        "-a",
        "wuleyan",
        "-s",
        "material-screening-agent-llm-api",
        "-w",
    ]
    assert kwargs["capture_output"] is True
    assert kwargs["text"] is True


def test_llm_requirement_parser_overrides_controlled_identity_fields(
    requirement: Requirement,
) -> None:
    payload = requirement.model_dump(mode="json")
    payload.update(
        {
            "requirement_id": "attacker-controlled",
            "revision": 999,
            "confirmed_by_user": True,
            "policy_version": "attacker-policy",
        }
    )
    provider = FakeProvider(
        {
            "requirement": payload,
            "clarification_questions": ["请确认这个目标。"],
        }
    )

    parsed = LLMRequirementParser(provider).parse(
        "寻找 Si/O 半导体", "req-local"
    )
    validated = Requirement.model_validate(parsed.requirement)

    assert validated.requirement_id == "req-local"
    assert validated.revision == 1
    assert validated.confirmed_by_user is False
    assert validated.policy_version == "requirement-policy-v1"
    assert parsed.clarification_questions == ["请确认这个目标。"]
    assert parsed.llm_audit == _audit()
    assert provider.calls[0]["user_payload"] == {
        "raw_request": "寻找 Si/O 半导体"
    }
    assert "JSON Schema" in provider.calls[0]["system_prompt"]


def test_llm_requirement_parser_accepts_canonical_adaptive_flat_band_mapping(
    requirement: Requirement,
) -> None:
    payload = requirement.model_dump(mode="json")
    payload["hard_constraints"]["dimensionality"] = 2
    provider = FakeProvider(
        {
            "requirement": payload,
            "clarification_questions": [],
            "mp_screening": {
                "mapped_clauses": [
                    {
                        "clause_id": "tm",
                        "source_text": "transition metal",
                        "capability_id": "composition.has_transition_metal",
                        "intent": "HARD",
                        "operator": "eq",
                        "value": True,
                        "unit": "dimensionless",
                        "priority": 100,
                    },
                    {
                        "clause_id": "layered",
                        "source_text": "layered",
                        "capability_id": "deep.layered",
                        "intent": "HARD",
                        "operator": "eq",
                        "value": True,
                        "unit": "dimensionless",
                        "priority": 100,
                    },
                    {
                        "clause_id": "bandwidth",
                        "source_text": "W <= 50 meV",
                        "capability_id": "deep.sampled_bandwidth",
                        "intent": "HARD",
                        "operator": "lte",
                        "value": 0.05,
                        "unit": "eV",
                        "priority": 100,
                    },
                    {
                        "clause_id": "vdw",
                        "source_text": "vdW gap preferred",
                        "capability_id": "proxy.vdw_gap",
                        "intent": "PREFERENCE",
                        "operator": "maximize",
                        "value": None,
                        "unit": "dimensionless",
                        "priority": 10,
                    },
                ],
                "unmapped_clauses": [],
                "deep_screen_limit": 20,
            },
        }
    )

    parsed = LLMRequirementParser(provider).parse("flat-band request", "req-local")

    assert parsed.mp_screening_spec is not None
    assert [
        clause["capability_id"] for clause in parsed.mp_screening_spec["mapped_clauses"]
    ] == [
        "composition.has_transition_metal",
        "deep.layered",
        "deep.sampled_bandwidth",
        "proxy.vdw_gap",
    ]
    assert parsed.mp_screening_spec["deep_endpoints"] == [
        "bandstructure_uniform",
        "robocrys",
    ]
    assert "50 meV" in provider.calls[0]["system_prompt"]


def test_llm_requirement_parser_repairs_omitted_flat_band_mapping(
    requirement: Requirement,
) -> None:
    provider = FakeProvider(
        {
            "requirement": requirement.model_dump(mode="json"),
            "clarification_questions": [],
            "mp_screening": None,
        }
    )

    parsed = LLMRequirementParser(provider).parse(
        "过渡金属层状材料，vdW gap 最优先，带宽 W<=50meV；"
        "进行价态分析；避免能带交点和孤立 cluster，要求互连子晶格。"
        "费米面附近的第一条能带应由杂化态构成。",
        "req-local",
    )

    assert parsed.mp_screening_spec is not None
    spec = parsed.mp_screening_spec
    assert {clause["capability_id"] for clause in spec["mapped_clauses"]} == {
        "composition.has_transition_metal",
        "deep.layered",
        "proxy.vdw_gap",
        "deep.sampled_bandwidth",
        "deep.oxidation_common",
        "proxy.band_crossing",
        "proxy.connected_sublattice",
    }
    bandwidth = next(
        clause for clause in spec["mapped_clauses"]
        if clause["capability_id"] == "deep.sampled_bandwidth"
    )
    assert bandwidth["value"] == 0.05
    assert bandwidth["unit"] == "eV"
    assert {gap["status"] for gap in spec["unmapped_clauses"]} == {
        "MISSING_THRESHOLD"
    }


def test_llm_requirement_parser_repairs_malformed_optional_mapping(
    requirement: Requirement,
) -> None:
    parser = LLMRequirementParser(
        FakeProvider(
            {
                "requirement": requirement.model_dump(mode="json"),
                "clarification_questions": [],
                "mp_screening": {"mapped_clauses": "not-a-list"},
            }
        )
    )

    parsed = parser.parse("过渡金属层状材料，带宽 W<=50meV。", "req-local")

    assert parsed.mp_screening_spec is not None
    assert {clause["capability_id"] for clause in parsed.mp_screening_spec["mapped_clauses"]} == {
        "composition.has_transition_metal",
        "deep.layered",
        "deep.sampled_bandwidth",
    }
    assert any(
        clause["clause_id"] == "invalid-mapping-envelope"
        for clause in parsed.mp_screening_spec["unmapped_clauses"]
    )


def test_llm_requirement_parser_fails_closed_on_invalid_scientific_unit(
    requirement: Requirement,
) -> None:
    payload = requirement.model_dump(mode="json")
    payload["hard_constraints"]["band_gap_ev"]["unit"] = "meV"
    parser = LLMRequirementParser(
        FakeProvider({"requirement": payload, "clarification_questions": []})
    )

    with pytest.raises(LLMProviderError) as raised:
        parser.parse("find a semiconductor", "req-local")

    assert raised.value.category == "INVALID_RESPONSE"


def test_llm_parser_revises_from_text_and_preserves_controlled_fields(
    requirement: Requirement,
) -> None:
    payload = requirement.model_dump(mode="json")
    payload.update(
        {
            "requirement_id": "provider-controlled",
            "revision": 99,
            "confirmed_by_user": True,
            "policy_version": "provider-policy",
        }
    )
    provider = FakeProvider(
        {"requirement": payload, "clarification_questions": []}
    )

    parsed = LLMRequirementParser(provider).revise_from_text(
        requirement.model_dump(mode="json"),
        "补充：带隙范围为 0.5 到 1.0 eV。",
    )
    validated = Requirement.model_validate(parsed.requirement)

    assert validated.requirement_id == requirement.requirement_id
    assert validated.revision == requirement.revision
    assert validated.confirmed_by_user is False
    assert validated.policy_version == requirement.policy_version
    assert parsed.llm_audit == _audit()
    assert provider.calls[0]["user_payload"] == {
        "current_requirement": requirement.model_dump(mode="json"),
        "clarification_response": "补充：带隙范围为 0.5 到 1.0 eV。",
    }
    assert provider.calls[0]["prompt_version"] == (
        "stage0-clarification-deepseek-v1"
    )


def test_llm_text_revision_fails_closed_on_invalid_unit(
    requirement: Requirement,
) -> None:
    payload = requirement.model_dump(mode="json")
    payload["hard_constraints"]["band_gap_ev"]["unit"] = "meV"
    parser = LLMRequirementParser(
        FakeProvider({"requirement": payload, "clarification_questions": []})
    )

    with pytest.raises(LLMProviderError) as raised:
        parser.revise_from_text(
            requirement.model_dump(mode="json"), "带隙单位使用 meV"
        )

    assert raised.value.category == "INVALID_RESPONSE"


def test_structured_requirement_never_calls_llm(requirement: Requirement) -> None:
    provider = FakeProvider({})
    parser = LLMRequirementParser(provider)

    parsed = parser.normalize_structured(
        requirement.model_dump(mode="json"), "req-local"
    )

    assert provider.calls == []
    assert parsed.parser_name == "structured-requirement-input"


def test_environment_factory_is_offline_by_default_and_deepseek_is_lazy() -> None:
    assert isinstance(
        requirement_parser_from_environment(environment={}),
        OfflineRequirementParser,
    )

    def forbidden_runner(*_args, **_kwargs):
        raise AssertionError("secret resolution must be lazy")

    parser = requirement_parser_from_environment(
        environment={
            "MATERIAL_AGENT_LLM_PROVIDER": "deepseek",
            "MATERIAL_AGENT_LLM_KEYCHAIN_ACCOUNT": "wuleyan",
        },
        command_runner=forbidden_runner,
        transport=ScriptedTransport([]),
    )

    assert isinstance(parser, LLMRequirementParser)


def _provider_response(content: str) -> tuple[int, bytes]:
    return (
        200,
        json.dumps(
            {
                "model": DEEPSEEK_MODEL_ID,
                "choices": [{"message": {"content": content}}],
                "usage": {
                    "prompt_tokens": 12,
                    "completion_tokens": 8,
                    "total_tokens": 20,
                },
            }
        ).encode(),
    )


def _audit() -> LLMCallAudit:
    return LLMCallAudit(
        provider="fake-llm",
        provider_version="fake-llm-v1",
        model_id="fake-model",
        base_url="https://fake.invalid",
        prompt_version="prompt-v1",
        request_sha256="a" * 64,
        response_sha256="b" * 64,
        thinking_mode="disabled",
        reasoning_effort="none",
        response_format="json_object",
        prompt_tokens=1,
        completion_tokens=2,
        total_tokens=3,
    )
