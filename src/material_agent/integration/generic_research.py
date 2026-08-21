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
from material_agent.inspiration.docling_parser import docling_parser_from_environment
from material_agent.inspiration.fulltext import (
    UNPAYWALL_EMAIL_ENV,
    LawfulFullTextResolver,
    OpenAccessPdfFetcher,
    PyMuPdfFigureRenderer,
    UnpaywallPublicAdapter,
    UrllibLocalGrobidTransport,
)
from material_agent.inspiration.models import canonical_json_bytes
from material_agent.inspiration.native_search import DeepSeekNativeSearchDiscovery
from material_agent.inspiration.opencitations import (
    OPENCITATIONS_ACCESS_TOKEN_ENV,
    OpenCitationsPublicAdapter,
)
from material_agent.inspiration.operator_planning import OperatorPlanningToolState
from material_agent.inspiration.policy import SearchBudgetV1
from material_agent.inspiration.research_graph import (
    MaterialsResearchDirector,
    MaterialsResearchGraphResultV7,
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
from material_agent.inspiration.semantic_scholar import (
    SEMANTIC_SCHOLAR_API_KEY_ENV,
    SemanticScholarPublicAdapter,
)
from material_agent.inspiration.specter2 import specter2_ranker_from_environment
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
GENERIC_RESEARCH_RESULT_SCHEMA_VERSION = "materials-generic-research-run-v7"
GENERIC_RESEARCH_IMPLEMENTATION_REVISION = "generic-research-20260821-r18"


def research_role_budget(
    *,
    role: str,
    requested_rounds: int,
    native_search_calls: int,
    authoritative_calls: int,
) -> DeepSeekAgentBudgetV1:
    """Return the production budget for one full scientific-research role.

    Tool-level states still enforce their own scientific request ceilings.  The
    agent budget is deliberately larger: it must also accommodate argument
    corrections, terminal budget receipts, counter-evidence loops, and final
    typed synthesis without aborting a valid long-form research run.
    """

    known_roles = {
        "requirements_analyst",
        "query_strategist",
        "native_search_scout",
        "evidence_researcher",
        "database_scout",
        "mechanism_chemist",
        "skeptic",
        "hypothesis_reasoner",
        "synthesist",
    }
    if role not in known_roles:
        raise ValueError(f"unknown research role: {role}")
    if not 2 <= requested_rounds <= 30:
        raise ValueError("requested_rounds must be between 2 and 30")
    if not 1 <= native_search_calls <= 16:
        raise ValueError("native_search_calls must be between 1 and 16")
    if not 2 <= authoritative_calls <= 24:
        raise ValueError("authoritative_calls must be between 2 and 24")
    if role == "native_search_scout":
        role_rounds = min(requested_rounds, native_search_calls + 3)
    elif role == "evidence_researcher":
        role_rounds = min(requested_rounds, authoritative_calls + 3)
    elif role == "database_scout":
        role_rounds = min(requested_rounds, 7)
    elif role in {"skeptic", "hypothesis_reasoner"}:
        role_rounds = requested_rounds
    elif role == "synthesist":
        role_rounds = min(requested_rounds, 8)
    else:
        role_rounds = min(requested_rounds, 6)
    return DeepSeekAgentBudgetV1(
        max_rounds=max(2, role_rounds),
        max_tool_calls=96,
        max_tool_result_bytes=1_000_000,
        max_total_tool_result_bytes=8_000_000,
        max_final_response_bytes=1_000_000,
        max_completion_tokens_per_round=32_768,
        max_total_tokens=1_000_000,
        max_walltime_seconds=3_600,
    )


def allocate_research_search_calls(
    *, provider_count: int, requested_authoritative_calls: int
) -> tuple[int, int, int]:
    """Fit authoritative, candidate, and counter searches into 64 physical calls."""

    if not 1 <= provider_count <= 16:
        raise ValueError("provider_count must be between 1 and 16")
    if not 2 <= requested_authoritative_calls <= 24:
        raise ValueError("requested authoritative calls must be between 2 and 24")
    logical_capacity = 64 // provider_count
    if logical_capacity < 4:
        raise ValueError("provider fan-out leaves no safe three-route search budget")
    authoritative = min(requested_authoritative_calls, logical_capacity - 2)
    remaining = logical_capacity - authoritative
    candidate = min(4, max(1, remaining // 3))
    counter = min(8, remaining - candidate)
    if counter < 1:
        raise ValueError("counter-evidence search requires at least one call")
    return authoritative, candidate, counter


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


class GenericResearchRunResultV7(StrictModel):
    schema_version: Literal["materials-generic-research-run-v7"] = (
        GENERIC_RESEARCH_RESULT_SCHEMA_VERSION
    )
    implementation_revision: Literal["generic-research-20260821-r18"] = (
        GENERIC_RESEARCH_IMPLEMENTATION_REVISION
    )
    run_id: str
    submission_id: str
    request_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    status: Literal["SUCCEEDED"] = "SUCCEEDED"
    research_graph: MaterialsResearchGraphResultV7
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
                "evidence auditing, probabilistic hypothesis reasoning, hash-pinned "
                "minimal-operation compilation, and explicit property-verification "
                "boundaries."
            ),
            "inputSchema": GenericResearchRunRequestV1.model_json_schema(),
            "outputSchema": GenericResearchRunResultV7.model_json_schema(),
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
    ) -> GenericResearchRunResultV7:
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
            cached = GenericResearchRunResultV7.model_validate_json(
                canonical_json_bytes(self.store.read_json(result_uri))
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
        (
            authoritative_calls,
            candidate_search_calls,
            counter_search_calls,
        ) = allocate_research_search_calls(
            provider_count=provider_count,
            requested_authoritative_calls=selected.max_authoritative_search_calls,
        )
        search_budget = SearchBudgetV1(
            max_queries=authoritative_calls,
            max_physical_requests=(
                (authoritative_calls + candidate_search_calls + counter_search_calls)
                * provider_count
            ),
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
            max_physical_requests=(
                authoritative_calls + candidate_search_calls + counter_search_calls
            )
            * provider_count,
            semantic_scholar_adapter=SemanticScholarPublicAdapter(
                api_key_resolver=lambda: search_environment.get(
                    SEMANTIC_SCHOLAR_API_KEY_ENV, ""
                )
            ),
            opencitations_adapter=OpenCitationsPublicAdapter(
                access_token_resolver=lambda: search_environment.get(
                    OPENCITATIONS_ACCESS_TOKEN_ENV, ""
                )
            ),
            native_leads_snapshot=native_state.snapshot,
            max_candidate_calls=candidate_search_calls,
            max_counter_calls=counter_search_calls,
            evidence_ranker=specter2_ranker_from_environment(search_environment),
            fulltext_resolver=LawfulFullTextResolver(
                unpaywall=UnpaywallPublicAdapter(
                    email_resolver=lambda: search_environment.get(
                        UNPAYWALL_EMAIL_ENV, ""
                    )
                ),
                pdf_fetcher=OpenAccessPdfFetcher(),
                grobid=UrllibLocalGrobidTransport(),
                store=self.store,
                run_id=run_id,
                docling=docling_parser_from_environment(search_environment),
                figure_renderer=PyMuPdfFigureRenderer(),
            ),
        )
        database_state = FederatedCandidateSearchState(
            store=self.store,
            run_id=run_id,
            environment=self.environment,
            max_calls=4,
            max_total_candidates=24,
        )
        operator_planning_state = OperatorPlanningToolState(
            store=self.store,
            database_candidates_snapshot=database_state.snapshot,
        )
        snapshot_states = {
            "native_search_scout": native_state,
            "evidence_researcher": evidence_state,
            "database_scout": database_state,
            "mechanism_chemist": operator_planning_state,
            "skeptic": evidence_state,
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
                    result = DeepSeekAgentResultV1[final_model].model_validate_json(
                        canonical_json_bytes(payload)
                    )
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
                    if role
                    in {
                        "evidence_researcher",
                        "database_scout",
                        "mechanism_chemist",
                        "skeptic",
                    }
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
            return DeepSeekThinkingAgent(
                secret_resolver=resolver,
                tools=tools,
                budget=research_role_budget(
                    role=role,
                    requested_rounds=selected.max_agent_rounds_per_role,
                    native_search_calls=selected.max_native_search_calls,
                    authoritative_calls=authoritative_calls,
                ),
                reasoning_effort=selected.reasoning_effort,
                timeout_seconds=1_800,
            )

        graph = MaterialsResearchDirector(
            runner_factory=runner_factory,
            native_search_tool=native_state.as_tool(),
            native_leads_snapshot=native_state.snapshot,
            authoritative_search_tool=evidence_state.as_tool(),
            evidence_snapshot=evidence_state.snapshot,
            lead_resolutions_snapshot=evidence_state.lead_resolutions_snapshot,
            candidate_literature_search=evidence_state.search_candidates,
            counter_evidence_search_tool=evidence_state.as_counter_tool(),
            counter_queries_snapshot=evidence_state.counter_queries_snapshot,
            database_search_tool=database_state.as_tool(),
            database_candidates_snapshot=database_state.snapshot,
            database_federation_snapshot=database_state.federation_snapshot,
            operator_planning_tool=operator_planning_state.as_tool(),
            transformation_audit_snapshot=operator_planning_state.audit_snapshot,
            checkpoint_load=checkpoint_load,
            checkpoint_save=checkpoint_save,
        ).run(selected.goal)
        result = GenericResearchRunResultV7(
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
        self, result: GenericResearchRunResultV7
    ) -> GenericResearchRunResultV7:
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
