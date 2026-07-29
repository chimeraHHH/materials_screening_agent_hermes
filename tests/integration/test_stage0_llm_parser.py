from __future__ import annotations

import json
import sqlite3

import pytest

from material_agent.orchestrator.llm import (
    LLMProviderError,
    StructuredLLMResponse,
)
from material_agent.orchestrator.models import LLMCallAudit, RunStatus
from material_agent.orchestrator.parser import LLMRequirementParser
from material_agent.orchestrator.runtime import OrchestratorRuntime


class SuccessfulProvider:
    name = "deepseek"
    version = "deepseek-test-v1"

    def __init__(self, requirement: dict) -> None:
        self.requirement = requirement

    def structured_generate(self, **_kwargs) -> StructuredLLMResponse:
        return StructuredLLMResponse(
            payload={
                "requirement": self.requirement,
                "clarification_questions": [],
            },
            audit=LLMCallAudit(
                provider="deepseek",
                provider_version=self.version,
                model_id="deepseek-v4-pro",
                base_url="https://api.deepseek.com",
                prompt_version="stage0-requirement-deepseek-v1",
                request_sha256="a" * 64,
                response_sha256="b" * 64,
                thinking_mode="enabled",
                reasoning_effort="high",
                response_format="json_object",
                prompt_tokens=10,
                completion_tokens=20,
                total_tokens=30,
            ),
        )


class FailingProvider:
    name = "deepseek"
    version = "deepseek-test-v1"

    def structured_generate(self, **_kwargs):
        raise LLMProviderError(
            "AUTHENTICATION_FAILED",
            "LLM provider returned HTTP status 401",
            retryable=False,
        )


class ClarifyingProvider(SuccessfulProvider):
    def __init__(self, requirement: dict) -> None:
        super().__init__(requirement)
        self.calls = 0

    def structured_generate(self, **_kwargs) -> StructuredLLMResponse:
        self.calls += 1
        response = super().structured_generate()
        if self.calls == 1:
            response.payload["clarification_questions"] = [
                "请补充明确的筛选条件。"
            ]
        return response


def test_llm_parser_reaches_requirement_gate_and_audits_only_metadata(
    tmp_path, requirement, fixture_payload
) -> None:
    OrchestratorRuntime.create_project(tmp_path, "project-llm")
    parser = LLMRequirementParser(
        SuccessfulProvider(requirement.model_dump(mode="json"))
    )

    with OrchestratorRuntime.from_workspace(
        tmp_path, "project-llm", parser=parser
    ) as runtime:
        waiting = runtime.start_run(
            raw_request="寻找符合条件的 Si/O 半导体",
            fixture_payload=fixture_payload,
            run_id="run-llm",
        )
        assert waiting.status is RunStatus.REQUIREMENT_REVIEW
        assert waiting.interrupts[0].value["interaction_type"] == (
            "REQUIREMENT_CONFIRMATION"
        )
        database = sqlite3.connect(runtime.database_path)
        try:
            row = database.execute(
                "SELECT payload_json FROM events "
                "WHERE run_id = ? AND event_type = ?",
                ("run-llm", "REQUIREMENT_PARSED"),
            ).fetchone()
        finally:
            database.close()

    payload = json.loads(row[0])
    assert payload["parser"] == "llm-requirement-parser"
    assert payload["llm_audit"]["provider"] == "deepseek"
    assert payload["llm_audit"]["model_id"] == "deepseek-v4-pro"
    serialized = json.dumps(payload)
    assert "reasoning_content" not in serialized
    assert "API key" not in serialized


def test_llm_provider_failure_marks_run_failed_without_fallback(
    tmp_path, fixture_payload
) -> None:
    OrchestratorRuntime.create_project(tmp_path, "project-llm-failure")
    parser = LLMRequirementParser(FailingProvider())

    with OrchestratorRuntime.from_workspace(
        tmp_path, "project-llm-failure", parser=parser
    ) as runtime:
        with pytest.raises(LLMProviderError, match="HTTP status 401"):
            runtime.start_run(
                raw_request="寻找材料",
                fixture_payload=fixture_payload,
                run_id="run-llm-failure",
            )
        row = runtime.repository.get_run("run-llm-failure")
        assert row["status"] == "FAILED"
        database = sqlite3.connect(runtime.database_path)
        try:
            event = database.execute(
                "SELECT payload_json FROM events "
                "WHERE run_id = ? AND event_type = ?",
                ("run-llm-failure", "REQUIREMENT_PARSE_FAILED"),
            ).fetchone()
        finally:
            database.close()

    payload = json.loads(event[0])
    assert payload == {
        "error_type": "LLMProviderError",
        "parser": "llm-requirement-parser",
        "parser_version": "llm-requirement-parser-v1",
    }


def test_llm_natural_language_clarification_audits_metadata(
    tmp_path, requirement, fixture_payload
) -> None:
    OrchestratorRuntime.create_project(tmp_path, "project-llm-clarify")
    provider = ClarifyingProvider(requirement.model_dump(mode="json"))
    parser = LLMRequirementParser(provider)

    with OrchestratorRuntime.from_workspace(
        tmp_path, "project-llm-clarify", parser=parser
    ) as runtime:
        clarifying = runtime.start_run(
            raw_request="寻找材料",
            fixture_payload=fixture_payload,
            run_id="run-llm-clarify",
        )
        interaction = clarifying.interrupts[0]
        reviewing = runtime.respond(
            run_id="run-llm-clarify",
            interaction_id=interaction.interaction_id,
            response={"answer": "补充明确的 Si/O 半导体筛选条件。"},
        )

        assert reviewing.status is RunStatus.REQUIREMENT_REVIEW
        database = sqlite3.connect(runtime.database_path)
        try:
            row = database.execute(
                "SELECT payload_json FROM events "
                "WHERE run_id = ? AND event_type = ?",
                (
                    "run-llm-clarify",
                    "REQUIREMENT_CLARIFICATION_PARSED",
                ),
            ).fetchone()
        finally:
            database.close()

    assert provider.calls == 2
    payload = json.loads(row[0])
    assert payload["llm_audit"]["provider"] == "deepseek"
    assert "reasoning_content" not in json.dumps(payload)
