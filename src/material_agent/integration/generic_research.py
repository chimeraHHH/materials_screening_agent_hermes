"""Runnable service for the generic DeepSeek materials-research graph."""

from __future__ import annotations

import getpass
import hashlib
import os
from collections.abc import Mapping
from pathlib import Path
from typing import Literal

from pydantic import Field, ValidationError, model_validator

from material_agent.inspiration.deepseek_agent import (
    DeepSeekAgentBudgetV1,
    DeepSeekAgentResultV1,
    DeepSeekThinkingAgent,
)
from material_agent.inspiration.models import canonical_json_bytes
from material_agent.inspiration.native_search import DeepSeekNativeSearchDiscovery
from material_agent.inspiration.policy import SearchBudgetV1
from material_agent.inspiration.research_graph import (
    MaterialsResearchDirector,
    MaterialsResearchGraphResultV3,
    research_graph_sha256,
)
from material_agent.inspiration.research_report import (
    build_generic_research_markdown_report,
)
from material_agent.inspiration.research_tools import (
    AuthoritativeLiteratureSearchState,
    FederatedCandidateSearchState,
    NativeSearchToolState,
)
from material_agent.inspiration.search import (
    OPENALEX_API_KEY_ENV,
    PUBLIC_SEARCH_MAX_RESULTS_ENV,
    PUBLIC_SEARCH_PROVIDER_ENV,
    public_search_adapter_from_environment,
)
from material_agent.orchestrator.llm import (
    DEFAULT_KEYCHAIN_SERVICE,
    DEFAULT_LLM_API_KEY_ENV,
    EnvironmentOrKeychainSecretResolver,
)
from material_agent.orchestrator.models import StrictModel
from material_agent.orchestrator.parser import (
    LLM_KEYCHAIN_ACCOUNT_ENV,
    LLM_KEYCHAIN_SERVICE_ENV,
)
from material_agent.orchestrator.runtime import OrchestratorRuntime
from material_agent.retrieval.adapters import C2dbAdapter, MaterialsProjectAdapter
from material_agent.retrieval.storage import LocalArtifactStore

GENERIC_RESEARCH_TOOL_NAME = "materials_generic_research_run"
GENERIC_RESEARCH_REQUEST_SCHEMA_VERSION = "materials-generic-research-run-v1"
GENERIC_RESEARCH_RESULT_SCHEMA_VERSION = "materials-generic-research-run-v3"
GENERIC_RESEARCH_IMPLEMENTATION_REVISION = "generic-research-20260821-r5"


def research_secret_resolver_from_environment(
    environment: Mapping[str, str] | None = None,
) -> EnvironmentOrKeychainSecretResolver:
    """Resolve the existing research key without persisting or logging it."""

    selected = dict(os.environ if environment is None else environment)
    if (
        not selected.get(DEFAULT_LLM_API_KEY_ENV, "").strip()
        and selected.get("DEEPSEEK_API_KEY", "").strip()
    ):
        selected[DEFAULT_LLM_API_KEY_ENV] = selected["DEEPSEEK_API_KEY"]
    return EnvironmentOrKeychainSecretResolver(
        environment=selected,
        keychain_service=selected.get(
            LLM_KEYCHAIN_SERVICE_ENV, DEFAULT_KEYCHAIN_SERVICE
        ),
        keychain_account=selected.get(LLM_KEYCHAIN_ACCOUNT_ENV, getpass.getuser()),
    )


class GenericResearchRunRequestV1(StrictModel):
    schema_version: Literal["materials-generic-research-run-v1"] = (
        GENERIC_RESEARCH_REQUEST_SCHEMA_VERSION
    )
    submission_id: str | None = Field(
        default=None,
        min_length=1,
        max_length=64,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$",
    )
    goal: str = Field(min_length=10, max_length=4_000)
    publication_year_from: int = Field(default=1960, ge=1600, le=2200)
    publication_year_to: int = Field(default=2026, ge=1600, le=2200)
    reasoning_effort: Literal["high", "max"] = "high"
    max_native_search_calls: int = Field(default=8, ge=1, le=16)
    max_authoritative_search_calls: int = Field(default=16, ge=2, le=24)
    max_agent_rounds_per_role: int = Field(default=12, ge=2, le=30)

    @model_validator(mode="after")
    def validate_years(self) -> GenericResearchRunRequestV1:
        if self.publication_year_from > self.publication_year_to:
            raise ValueError(
                "publication_year_from must not exceed publication_year_to"
            )
        return self


class GenericResearchRunResultV3(StrictModel):
    schema_version: Literal["materials-generic-research-run-v3"] = (
        GENERIC_RESEARCH_RESULT_SCHEMA_VERSION
    )
    implementation_revision: Literal["generic-research-20260821-r5"] = (
        GENERIC_RESEARCH_IMPLEMENTATION_REVISION
    )
    run_id: str
    submission_id: str
    request_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    status: Literal["SUCCEEDED"] = "SUCCEEDED"
    research_graph: MaterialsResearchGraphResultV3
    research_graph_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    result_artifact_uri: str = Field(pattern=r"^artifact://")
    report_artifact_uri: str | None = Field(default=None, pattern=r"^artifact://")
    report_manifest_artifact_uri: str | None = Field(
        default=None, pattern=r"^artifact://"
    )
    scientific_conclusion: str = Field(min_length=3, max_length=4_000)
    scientific_conclusion_status: Literal["REASONED_HYPOTHESIS"] = "REASONED_HYPOTHESIS"
    property_verification_complete: Literal[False] = False


def generic_research_tool_manifest() -> tuple[dict[str, object], ...]:
    return (
        {
            "name": GENERIC_RESEARCH_TOOL_NAME,
            "description": (
                "Run the generic nine-role DeepSeek materials-inspiration research graph "
                "with native lead discovery, authoritative metadata resolution, constraint "
                "evidence auditing, probabilistic hypothesis reasoning, and explicit "
                "property-verification boundaries."
            ),
            "inputSchema": GenericResearchRunRequestV1.model_json_schema(),
            "outputSchema": GenericResearchRunResultV3.model_json_schema(),
            "readOnly": False,
        },
    )


class GenericMaterialsResearchService:
    """Synchronous canonical service; callers may wrap it in a durable worker."""

    def __init__(
        self,
        *,
        workspace: Path | str,
        project_id: str,
        environment: Mapping[str, str] | None = None,
    ) -> None:
        selected = dict(os.environ if environment is None else environment)
        suffix = "-generic-research"
        bounded_project_id = f"{project_id[: 64 - len(suffix)]}{suffix}"
        project = OrchestratorRuntime.create_project(workspace, bounded_project_id)
        self.project_root = Path(project["project_root"])
        self.store = LocalArtifactStore(self.project_root)
        self.environment = selected

    def run(
        self, request: GenericResearchRunRequestV1 | Mapping[str, object]
    ) -> GenericResearchRunResultV3:
        selected = GenericResearchRunRequestV1.model_validate(request)
        semantic_request = selected.model_dump(mode="json", exclude={"submission_id"})
        semantic_sha = hashlib.sha256(
            canonical_json_bytes(semantic_request)
        ).hexdigest()
        if selected.submission_id is None:
            selected = selected.model_copy(
                update={"submission_id": f"auto-{semantic_sha[:24]}"}
            )
        identity = {
            "implementation_revision": GENERIC_RESEARCH_IMPLEMENTATION_REVISION,
            "request": selected.model_dump(mode="json"),
        }
        request_sha = hashlib.sha256(canonical_json_bytes(identity)).hexdigest()
        run_id = f"generic-{request_sha[:24]}"
        result_path = f"generic_research/{run_id}/result.json"
        result_uri = f"artifact://{result_path}"
        if self.store.exists(result_uri):
            cached = GenericResearchRunResultV3.model_validate(
                self.store.read_json(result_uri)
            )
            return self._with_report(cached)

        resolver = research_secret_resolver_from_environment(self.environment)
        search_environment = dict(self.environment)
        search_environment.setdefault(
            PUBLIC_SEARCH_PROVIDER_ENV,
            "crossref+openalex+semantic-scholar+arxiv+osti"
            if search_environment.get(OPENALEX_API_KEY_ENV, "").strip()
            else "crossref+semantic-scholar+arxiv+osti",
        )
        search_environment.setdefault(PUBLIC_SEARCH_MAX_RESULTS_ENV, "20")
        provider_count = len(search_environment[PUBLIC_SEARCH_PROVIDER_ENV].split("+"))
        authoritative_calls = min(
            selected.max_authoritative_search_calls,
            64 // provider_count,
        )
        search_budget = SearchBudgetV1(
            max_queries=authoritative_calls,
            max_physical_requests=authoritative_calls * provider_count,
            max_direct_queries=authoritative_calls,
            max_bridge_queries=0,
            max_counter_queries=0,
            max_raw_hits=authoritative_calls * 20,
            max_unique_documents=authoritative_calls * 20,
            publication_year_from=selected.publication_year_from,
            publication_year_to=selected.publication_year_to,
        )
        adapter = public_search_adapter_from_environment(
            budget=search_budget, environment=search_environment
        )
        native_state = NativeSearchToolState(
            DeepSeekNativeSearchDiscovery(
                secret_resolver=resolver,
                reasoning_effort=selected.reasoning_effort,
                max_uses=min(8, selected.max_native_search_calls),
            ),
            max_calls=selected.max_native_search_calls,
        )
        evidence_state = AuthoritativeLiteratureSearchState(
            adapter=adapter,
            store=self.store,
            run_id=run_id,
            publication_year_from=selected.publication_year_from,
            publication_year_to=selected.publication_year_to,
            max_calls=authoritative_calls,
            max_physical_requests=authoritative_calls * provider_count,
        )
        database_state = FederatedCandidateSearchState(
            store=self.store,
            run_id=run_id,
            environment=self.environment,
            max_calls=4,
            max_total_candidates=24,
        )
        snapshot_states = {
            "native_search_scout": native_state,
            "evidence_researcher": evidence_state,
            "database_scout": database_state,
        }

        def checkpoint_load(role, final_model):
            schema_hash = hashlib.sha256(
                canonical_json_bytes(final_model.model_json_schema())
            ).hexdigest()[:16]
            uris = (
                f"artifact://generic_research/{run_id}/roles/{role}-{schema_hash}.json",
                f"artifact://generic_research/{run_id}/roles/{role}.json",
            )
            for uri in uris:
                if not self.store.exists(uri):
                    continue
                try:
                    payload = self.store.read_json(uri)
                    if role in snapshot_states:
                        if (
                            not isinstance(payload, dict)
                            or "agent_result" not in payload
                        ):
                            continue
                        tool_snapshot = payload.get("tool_snapshot")
                        payload = payload["agent_result"]
                    elif isinstance(payload, dict) and "agent_result" in payload:
                        payload = payload["agent_result"]
                    result = DeepSeekAgentResultV1[final_model].model_validate(payload)
                    if role in snapshot_states:
                        snapshot_states[role].restore_snapshot(tool_snapshot)
                    return result
                except (ValidationError, ValueError):
                    continue
            return None

        def checkpoint_save(role, result):
            schema_hash = hashlib.sha256(
                canonical_json_bytes(type(result.final).model_json_schema())
            ).hexdigest()[:16]
            payload = {
                "agent_result": result.model_dump(mode="json"),
                "tool_snapshot": (
                    snapshot_states[role].checkpoint_snapshot()
                    if role == "database_scout"
                    else [
                        item.model_dump(mode="json")
                        if hasattr(item, "model_dump")
                        else dict(item)
                        for item in snapshot_states[role].snapshot()
                    ]
                    if role in snapshot_states
                    else []
                ),
            }
            self.store.write_json(
                f"generic_research/{run_id}/roles/{role}-{schema_hash}.json",
                payload,
                immutable=True,
            )

        def runner_factory(role, tools):
            if role == "native_search_scout":
                role_rounds = min(
                    selected.max_agent_rounds_per_role,
                    selected.max_native_search_calls + 3,
                )
                role_tool_calls = max(8, selected.max_native_search_calls + 4)
            elif role == "evidence_researcher":
                role_rounds = min(
                    selected.max_agent_rounds_per_role,
                    authoritative_calls + 3,
                )
                role_tool_calls = max(12, authoritative_calls + 4)
            elif role == "database_scout":
                role_rounds = min(selected.max_agent_rounds_per_role, 7)
                role_tool_calls = 16
            elif role == "skeptic" or role == "hypothesis_reasoner":
                role_rounds = selected.max_agent_rounds_per_role
                role_tool_calls = 8
            elif role == "synthesist":
                role_rounds = min(selected.max_agent_rounds_per_role, 8)
                role_tool_calls = 8
            else:
                role_rounds = min(selected.max_agent_rounds_per_role, 6)
                role_tool_calls = 8
            role_total_tokens = (
                300_000
                if role in {"skeptic", "hypothesis_reasoner"}
                else 240_000
                if role == "synthesist"
                else 180_000
            )
            return DeepSeekThinkingAgent(
                secret_resolver=resolver,
                tools=tools,
                budget=DeepSeekAgentBudgetV1(
                    max_rounds=max(2, role_rounds),
                    max_tool_calls=min(24, role_tool_calls),
                    max_total_tokens=role_total_tokens,
                    max_walltime_seconds=1_200,
                ),
                reasoning_effort=selected.reasoning_effort,
            )

        graph = MaterialsResearchDirector(
            runner_factory=runner_factory,
            native_search_tool=native_state.as_tool(),
            native_leads_snapshot=native_state.snapshot,
            authoritative_search_tool=evidence_state.as_tool(),
            evidence_snapshot=evidence_state.snapshot,
            database_search_tool=database_state.as_tool(),
            database_candidates_snapshot=database_state.snapshot,
            database_federation_snapshot=database_state.federation_snapshot,
            checkpoint_load=checkpoint_load,
            checkpoint_save=checkpoint_save,
        ).run(selected.goal)
        result = GenericResearchRunResultV3(
            run_id=run_id,
            submission_id=selected.submission_id,
            request_sha256=request_sha,
            research_graph=graph,
            research_graph_sha256=research_graph_sha256(graph),
            result_artifact_uri=result_uri,
            scientific_conclusion=graph.synthesis.scientific_conclusion,
            scientific_conclusion_status=(graph.synthesis.scientific_conclusion_status),
            property_verification_complete=(
                graph.synthesis.property_verification_complete
            ),
        )
        result = self._with_report(result)
        self.store.write_json(
            result_path, result.model_dump(mode="json"), immutable=True
        )
        return result

    def _with_report(
        self, result: GenericResearchRunResultV3
    ) -> GenericResearchRunResultV3:
        report = build_generic_research_markdown_report(
            graph=result.research_graph,
            store=self.store,
            run_id=result.run_id,
            materials_project_adapter=MaterialsProjectAdapter(
                environment=self.environment
            ),
            c2db_adapter=C2dbAdapter(),
        )
        return result.model_copy(
            update={
                "report_artifact_uri": report.markdown_artifact_uri,
                "report_manifest_artifact_uri": report.manifest_artifact_uri,
            }
        )
