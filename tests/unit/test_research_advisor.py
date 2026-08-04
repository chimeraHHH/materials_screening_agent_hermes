from __future__ import annotations

import pytest

from material_agent.orchestrator.llm import StructuredLLMResponse
from material_agent.orchestrator.models import LLMCallAudit
from material_agent.orchestrator.research_advisor import (
    DeterministicResearchAdvisor,
    LLMResearchAdvisor,
    build_research_snapshot,
)
from material_agent.retrieval.storage import LocalArtifactStore


def _snapshot(tmp_path):
    store = LocalArtifactStore(tmp_path)
    retrieval = store.write_json(
        "stages/agent01/run/retrieval_report.json",
        {
            "published_candidates": [
                {
                    "candidate_id": "cand-1",
                    "material_id": "mp-1",
                    "formula": "FeSe",
                    "decision": "UNCERTAIN",
                    "missing_evidence": ["flat-band bandwidth is unavailable"],
                }
            ]
        },
        immutable=True,
    )
    native = store.write_json(
        "stages/agent01/run/stage_result.json",
        {
            "stage": "agent01",
            "output_artifacts": [retrieval.model_dump(mode="json")],
        },
        immutable=True,
    )
    report = store.write_json(
        "reports/run/report.json",
        {
            "project_id": "project",
            "run_id": "run",
            "status": "PARTIAL",
            "evidence_statement": "L1 retrieval evidence only.",
            "stages": {
                "agent01": {
                    "status": "SUCCEEDED",
                    "native_result_uri": native.uri,
                    "native_result_sha256": native.sha256,
                },
                "agent02": {"status": "CAPABILITY_UNAVAILABLE"},
            },
        },
        immutable=True,
    )
    return build_research_snapshot(
        store, report_uri=report.uri, report_sha256=report.sha256
    )


def test_snapshot_extracts_verified_agent01_gaps_and_only_nonexecuting_actions(tmp_path) -> None:
    snapshot = _snapshot(tmp_path)

    assert snapshot.candidate_cards[0].material_id == "mp-1"
    assert any("flat-band bandwidth" in gap.description for gap in snapshot.evidence_gaps)
    assert any("CAPABILITY_UNAVAILABLE" in gap.description for gap in snapshot.evidence_gaps)
    assert all(action.execution_allowed is False for action in snapshot.actions)
    assert {action.action_id for action in snapshot.actions} == {
        "review-candidate-evidence-gaps",
        "propose-electronic-structure-validation",
        "review-stage-prerequisites",
        "expert-review",
    }


def test_offline_advice_preserves_deterministic_action_set(tmp_path) -> None:
    snapshot = _snapshot(tmp_path)

    advice = DeterministicResearchAdvisor().advise(snapshot)

    assert advice.mode == "offline"
    assert advice.scientific_conclusion is False
    assert set(advice.narrative.action_explanations) == {
        action.action_id for action in snapshot.actions
    }


class _Provider:
    name = "deepseek"
    version = "test-v1"

    def __init__(self, payload: dict) -> None:
        self.payload = payload

    def structured_generate(self, **_kwargs) -> StructuredLLMResponse:
        return StructuredLLMResponse(
            payload=self.payload,
            audit=LLMCallAudit(
                provider="deepseek",
                provider_version=self.version,
                model_id="deepseek-v4-pro",
                base_url="https://api.deepseek.com",
                prompt_version="research-advice-deepseek-v1",
                request_sha256="a" * 64,
                response_sha256="b" * 64,
                thinking_mode="enabled",
                reasoning_effort="high",
                response_format="json_object",
            ),
        )


def test_llm_advice_can_explain_but_cannot_add_actions(tmp_path) -> None:
    snapshot = _snapshot(tmp_path)
    allowed = snapshot.actions[0].action_id
    advisor = LLMResearchAdvisor(
        _Provider(
            {
                "research_summary": "The evidence package contains unresolved gaps.",
                "action_explanations": {allowed: "Review the recorded missing evidence."},
            }
        )
    )

    advice = advisor.advise(snapshot)

    assert advice.mode == "llm"
    assert advice.llm_audit is not None
    assert set(advice.narrative.action_explanations) == {allowed}

    invalid = LLMResearchAdvisor(
        _Provider(
            {
                "research_summary": "Try an extra calculation.",
                "action_explanations": {"run-unapproved-dft": "Do it now."},
            }
        )
    )
    with pytest.raises(ValueError, match="absent from the policy snapshot"):
        invalid.advise(snapshot)
