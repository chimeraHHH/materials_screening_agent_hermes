"""Opt-in LangGraph V3 for material-aware contextual Inspiration execution.

V3 extends the verified Agent01-to-Inspiration bridge with a frozen
QueryContext, reviewed project memory, Semantic Scholar acquisition requests,
and family/era-specific allowances.  A V3 runner must explicitly implement
``run_contextual``; the graph never falls back to the legacy runner and thus
cannot silently ignore its new retrieval artifacts.
"""

from __future__ import annotations

from enum import StrEnum
from pathlib import Path
from typing import Any, Literal, Protocol, Self, TypedDict

from langgraph.graph import END, START, StateGraph
from pydantic import Field

from material_agent.inspiration.literature_budget import (
    FamilyReservationV2,
    LiteratureBudgetPlanV2,
    LiteratureBudgetPolicyV2,
    LiteratureQueryCandidateV2,
    LiteratureQueryFamily,
    allocate_literature_budget_v2,
    default_materials_literature_budget_v2,
    make_literature_query_candidate_v2,
)
from material_agent.inspiration.models import (
    ArtifactPointerV1,
    InspirationInputV1,
    TagGraphV1,
    canonical_json_bytes,
    deterministic_id,
)
from material_agent.inspiration.policy import InspirationPolicyV1, SearchBudgetV1
from material_agent.inspiration.query_context import (
    QueryContextV2,
    compile_query_context_v2,
)
from material_agent.inspiration.research_memory import (
    InspirationMemorySnapshotV1,
    InspirationMemoryStore,
    compile_memory_snapshot_v1,
    reviewed_hints_from_memory_v1,
)
from material_agent.inspiration.semantic_scholar import (
    SEMANTIC_SCHOLAR_REQUEST_VERSION,
    SemanticScholarRequestV1,
    SemanticScholarRoute,
    compile_semantic_scholar_requests,
)
from material_agent.inspiration.tag_graph import plan_tag_queries
from material_agent.orchestrator.inspiration_composite import (
    InspirationCompositeGraphV2,
    InspirationCompositeLaunchV2,
    InspirationCompositeRequestV2,
    InspirationCompositeRuntimeV2,
    InspirationCompositeStatus,
    InspirationRunnerProtocol,
)
from material_agent.orchestrator.models import ArtifactPointer, StrictModel
from material_agent.retrieval.models import (
    CandidateAuditRecord,
    CandidateAuditRecordV2,
    Decision,
    Requirement,
)
from material_agent.retrieval.storage import LocalArtifactStore


INSPIRATION_QUERY_COMPOSITE_VERSION = "orchestrator-inspiration-query-v3"


class InspirationQueryCompositeStatus(StrEnum):
    READY = "READY"
    SUCCEEDED = "SUCCEEDED"
    SCIENTIFIC_NO_MATCH = "SCIENTIFIC_NO_MATCH"
    FAILED = "FAILED"


class InspirationQueryCompositeRequestV3(StrictModel):
    schema_version: Literal["orchestrator-inspiration-query-v3"] = (
        INSPIRATION_QUERY_COMPOSITE_VERSION
    )
    base_request: InspirationCompositeRequestV2
    raw_request: str = Field(min_length=3, max_length=4_000)
    semantic_scholar_max_results: int = Field(default=10, ge=1, le=100)
    max_literature_queries: int = Field(default=12, ge=10, le=64)

    @property
    def request_id(self) -> str:
        return self.base_request.request_id


class InspirationQueryCompositeLaunchV3(StrictModel):
    """Workspace-resolved launch input for the opt-in V3 runtime."""

    schema_version: Literal["orchestrator-inspiration-query-v3"] = (
        INSPIRATION_QUERY_COMPOSITE_VERSION
    )
    base_launch: InspirationCompositeLaunchV2
    raw_request: str = Field(min_length=3, max_length=4_000)
    semantic_scholar_max_results: int = Field(default=10, ge=1, le=100)
    max_literature_queries: int = Field(default=12, ge=10, le=64)


class InspirationQueryHandoffV3(StrictModel):
    schema_version: Literal["orchestrator-inspiration-query-v3"] = (
        INSPIRATION_QUERY_COMPOSITE_VERSION
    )
    inspiration_input_artifact: ArtifactPointer
    query_context_artifact: ArtifactPointer
    memory_snapshot_artifact: ArtifactPointer
    literature_budget_policy_artifact: ArtifactPointer
    literature_request_artifact: ArtifactPointer
    literature_candidate_artifact: ArtifactPointer
    literature_budget_plan_artifact: ArtifactPointer


class InspirationQueryCompositeResultV3(StrictModel):
    schema_version: Literal["orchestrator-inspiration-query-v3"] = (
        INSPIRATION_QUERY_COMPOSITE_VERSION
    )
    status: InspirationQueryCompositeStatus
    parent_candidate_ids: list[str] = Field(default_factory=list)
    selected_candidate_ids: list[str] = Field(default_factory=list)
    handoff: InspirationQueryHandoffV3 | None = None
    stage_result_artifact: ArtifactPointer | None = None
    bundle_artifact: ArtifactPointer | None = None
    error_code: str | None = None
    error_message: str | None = None


class InspirationQueryCompositeState(TypedDict, total=False):
    request: dict[str, Any]
    inspiration_input: dict[str, Any]
    query_context: dict[str, Any]
    memory_snapshot: dict[str, Any]
    literature_requests: list[dict[str, Any]]
    literature_candidates: list[dict[str, Any]]
    literature_budget_policy: dict[str, Any]
    literature_budget_plan: dict[str, Any]
    _partial_handoff: dict[str, Any]
    result: dict[str, Any]


class ContextualInspirationRunnerV3(InspirationRunnerProtocol, Protocol):
    """Required V3 boundary; no legacy fallback is permitted."""

    def run_contextual(
        self,
        *,
        inspiration_input: InspirationInputV1,
        policy: InspirationPolicyV1,
        tag_graph: TagGraphV1,
        target_tag_ids: tuple[str, ...],
        query_context: QueryContextV2,
        memory_snapshot: InspirationMemorySnapshotV1,
        literature_requests: tuple[SemanticScholarRequestV1, ...],
        literature_candidates: tuple[LiteratureQueryCandidateV2, ...],
        literature_budget_policy: LiteratureBudgetPolicyV2,
        literature_budget_plan: LiteratureBudgetPlanV2,
        handoff: InspirationQueryHandoffV3,
    ) -> Any: ...


class InspirationQueryCompositeGraphV3:
    """Compile and execute the opt-in material-aware composite graph."""

    def __init__(
        self,
        *,
        store: LocalArtifactStore,
        runner: ContextualInspirationRunnerV3,
        memory_store: InspirationMemoryStore | None = None,
    ) -> None:
        if not hasattr(runner, "run_contextual") or not callable(runner.run_contextual):
            raise TypeError("V3 runner must implement run_contextual")
        self.store = store
        self.runner = runner
        self.memory_store = memory_store
        self._base = InspirationCompositeGraphV2(store=store, runner=runner)

    def build(self) -> StateGraph:
        graph = StateGraph(InspirationQueryCompositeState)
        graph.add_node("compile_agent01_context", self.compile_agent01_context)
        graph.add_node("plan_contextual_literature", self.plan_contextual_literature)
        graph.add_node("execute_contextual_inspiration", self.execute_contextual_inspiration)
        graph.add_edge(START, "compile_agent01_context")
        graph.add_conditional_edges(
            "compile_agent01_context",
            self._route,
            {"continue": "plan_contextual_literature", "end": END},
        )
        graph.add_conditional_edges(
            "plan_contextual_literature",
            self._route,
            {"continue": "execute_contextual_inspiration", "end": END},
        )
        graph.add_edge("execute_contextual_inspiration", END)
        return graph

    def compile_agent01_context(
        self, state: InspirationQueryCompositeState
    ) -> dict[str, Any]:
        try:
            request = InspirationQueryCompositeRequestV3.model_validate(state["request"])
            base_state = self._base.compile_agent01_input(
                {"request": request.base_request.model_dump(mode="json")}
            )
            base_result = base_state["result"]
            if base_result["status"] != InspirationCompositeStatus.READY.value:
                return {
                    "result": self._failure(
                        base_result.get("error_code", "AGENT01_CONTEXT_FAILED"),
                        base_result.get("error_message", "Agent01 context compilation failed"),
                        parent_candidate_ids=base_result.get("parent_candidate_ids", []),
                    )
                }
            inspiration_input = InspirationInputV1.model_validate_json(
                canonical_json_bytes(base_state["inspiration_input"])
            )
            requirement = Requirement.model_validate_json(
                self.store.read_bytes(request.base_request.requirement_artifact.uri)
            )
            parents = self._load_candidate_records(request.base_request)
            memory = (
                self.memory_store.snapshot(request.base_request.project_id)
                if self.memory_store is not None
                else compile_memory_snapshot_v1(request.base_request.project_id, ())
            )
            context = compile_query_context_v2(
                requirement=requirement,
                parent_candidates=parents,
                raw_request=request.raw_request,
                reviewed_hints=reviewed_hints_from_memory_v1(memory),
            )
            prefix = f"plans/{request.base_request.inspiration_run_id}/query-v3"
            memory_pointer = self.store.write_json(
                f"{prefix}/memory_snapshot.json",
                memory.model_dump(mode="json"),
                immutable=True,
            )
            context_pointer = self.store.write_json(
                f"{prefix}/query_context.json",
                context.model_dump(mode="json"),
                immutable=True,
            )
            input_pointer = base_result["frozen_input_artifact"]
            partial_handoff = {
                "inspiration_input_artifact": input_pointer,
                "query_context_artifact": _control_pointer(context_pointer),
                "memory_snapshot_artifact": _control_pointer(memory_pointer),
            }
        except Exception as exc:
            return {
                "result": self._failure(
                    getattr(exc, "code", "QUERY_CONTEXT_COMPILATION_FAILED"),
                    f"V3 query context compilation failed ({type(exc).__name__})",
                )
            }
        return {
            "inspiration_input": inspiration_input.model_dump(mode="json"),
            "query_context": context.model_dump(mode="json"),
            "memory_snapshot": memory.model_dump(mode="json"),
            "result": InspirationQueryCompositeResultV3(
                status=InspirationQueryCompositeStatus.READY,
                parent_candidate_ids=[item.candidate_id for item in parents],
                handoff=None,
            ).model_dump(mode="json"),
            "_partial_handoff": partial_handoff,
        }

    def plan_contextual_literature(
        self, state: InspirationQueryCompositeState
    ) -> dict[str, Any]:
        try:
            request = InspirationQueryCompositeRequestV3.model_validate(state["request"])
            context = QueryContextV2.model_validate_json(
                canonical_json_bytes(state["query_context"])
            )
            tag_graph = TagGraphV1.model_validate_json(
                self.store.read_bytes(request.base_request.tag_graph_artifact.uri)
            )
            requests, candidates = _compile_v3_literature_candidates(
                context,
                tag_graph=tag_graph,
                target_tag_ids=tuple(request.base_request.target_tag_ids),
                max_results=request.semantic_scholar_max_results,
            )
            policy = _policy_for_candidates(
                candidates,
                max_queries=request.max_literature_queries,
            )
            plan = allocate_literature_budget_v2(candidates, policy=policy)
            prefix = f"plans/{request.base_request.inspiration_run_id}/query-v3"
            policy_pointer = self.store.write_json(
                f"{prefix}/literature_budget_policy.json",
                policy.model_dump(mode="json"),
                immutable=True,
            )
            request_pointer = self.store.write_jsonl(
                f"{prefix}/semantic_scholar_requests.jsonl",
                [item.model_dump(mode="json") for item in requests],
                immutable=True,
            )
            candidate_pointer = self.store.write_jsonl(
                f"{prefix}/literature_candidates.jsonl",
                [item.model_dump(mode="json") for item in candidates],
                immutable=True,
            )
            plan_pointer = self.store.write_json(
                f"{prefix}/literature_budget_plan.json",
                plan.model_dump(mode="json"),
                immutable=True,
            )
            partial = state["_partial_handoff"]
            handoff = InspirationQueryHandoffV3(
                **partial,
                literature_budget_policy_artifact=_control_pointer(policy_pointer),
                literature_request_artifact=_control_pointer(request_pointer),
                literature_candidate_artifact=_control_pointer(candidate_pointer),
                literature_budget_plan_artifact=_control_pointer(plan_pointer),
            )
        except Exception as exc:
            return {
                "result": self._failure(
                    getattr(exc, "code", "LITERATURE_PLANNING_FAILED"),
                    f"V3 literature planning failed ({type(exc).__name__})",
                    parent_candidate_ids=state.get("result", {}).get(
                        "parent_candidate_ids", []
                    ),
                )
            }
        return {
            "literature_requests": [item.model_dump(mode="json") for item in requests],
            "literature_candidates": [
                item.model_dump(mode="json") for item in candidates
            ],
            "literature_budget_policy": policy.model_dump(mode="json"),
            "literature_budget_plan": plan.model_dump(mode="json"),
            "result": InspirationQueryCompositeResultV3(
                status=InspirationQueryCompositeStatus.READY,
                parent_candidate_ids=state["result"]["parent_candidate_ids"],
                handoff=handoff,
            ).model_dump(mode="json"),
        }

    def execute_contextual_inspiration(
        self, state: InspirationQueryCompositeState
    ) -> dict[str, Any]:
        request = InspirationQueryCompositeRequestV3.model_validate(state["request"])
        current = InspirationQueryCompositeResultV3.model_validate(state["result"])
        assert current.handoff is not None
        try:
            run_result = self.runner.run_contextual(
                inspiration_input=InspirationInputV1.model_validate_json(
                    canonical_json_bytes(state["inspiration_input"])
                ),
                policy=InspirationPolicyV1.model_validate_json(
                    self.store.read_bytes(request.base_request.policy_artifact.uri)
                ),
                tag_graph=TagGraphV1.model_validate_json(
                    self.store.read_bytes(request.base_request.tag_graph_artifact.uri)
                ),
                target_tag_ids=tuple(request.base_request.target_tag_ids),
                query_context=QueryContextV2.model_validate_json(
                    canonical_json_bytes(state["query_context"])
                ),
                memory_snapshot=InspirationMemorySnapshotV1.model_validate_json(
                    canonical_json_bytes(state["memory_snapshot"])
                ),
                literature_requests=tuple(
                    SemanticScholarRequestV1.model_validate_json(
                        canonical_json_bytes(item)
                    )
                    for item in state["literature_requests"]
                ),
                literature_candidates=tuple(
                    LiteratureQueryCandidateV2.model_validate_json(
                        canonical_json_bytes(item)
                    )
                    for item in state["literature_candidates"]
                ),
                literature_budget_policy=LiteratureBudgetPolicyV2.model_validate_json(
                    canonical_json_bytes(state["literature_budget_policy"])
                ),
                literature_budget_plan=LiteratureBudgetPlanV2.model_validate_json(
                    canonical_json_bytes(state["literature_budget_plan"])
                ),
                handoff=current.handoff,
            )
            self._base._verify_v1_pointer(run_result.stage_result_artifact)
            self._base._verify_v1_pointer(run_result.stage_result.bundle_artifact)
            outcome = run_result.stage_result.outcome.value
            status = (
                InspirationQueryCompositeStatus.SUCCEEDED
                if outcome == "SUCCEEDED"
                else InspirationQueryCompositeStatus.SCIENTIFIC_NO_MATCH
            )
            selected = [
                item.candidate_id for item in run_result.bundle.selected_candidates
            ]
            return {
                "result": InspirationQueryCompositeResultV3(
                    status=status,
                    parent_candidate_ids=current.parent_candidate_ids,
                    selected_candidate_ids=selected,
                    handoff=current.handoff,
                    stage_result_artifact=_control_pointer(
                        run_result.stage_result_artifact
                    ),
                    bundle_artifact=_control_pointer(
                        run_result.stage_result.bundle_artifact
                    ),
                ).model_dump(mode="json")
            }
        except Exception as exc:
            return {
                "result": self._failure(
                    getattr(exc, "code", "CONTEXTUAL_INSPIRATION_FAILED"),
                    f"V3 contextual execution failed ({type(exc).__name__})",
                    parent_candidate_ids=current.parent_candidate_ids,
                    handoff=current.handoff,
                )
            }

    @staticmethod
    def _route(state: InspirationQueryCompositeState) -> str:
        result = InspirationQueryCompositeResultV3.model_validate(state["result"])
        return (
            "continue"
            if result.status is InspirationQueryCompositeStatus.READY
            else "end"
        )

    def _load_candidate_records(
        self, request: InspirationCompositeRequestV2
    ) -> tuple[CandidateAuditRecord | CandidateAuditRecordV2, ...]:
        records: list[CandidateAuditRecord | CandidateAuditRecordV2] = []
        for raw in self.store.read_jsonl(request.candidate_manifest_artifact.uri):
            model = (
                CandidateAuditRecordV2
                if raw.get("schema_version") == "agent01-contract-v2"
                else CandidateAuditRecord
            )
            record = model.model_validate(raw)
            if record.published_downstream and record.decision in {
                Decision.PASS,
                Decision.UNCERTAIN,
            }:
                records.append(record)
        records.sort(key=lambda item: (item.publication_rank or 10**9, item.candidate_id))
        selected = tuple(records[: request.max_parent_candidates])
        if not selected:
            raise ValueError("candidate manifest contains no contextual parent")
        return selected

    @staticmethod
    def _failure(
        code: str,
        message: str,
        *,
        parent_candidate_ids: list[str] | None = None,
        handoff: InspirationQueryHandoffV3 | None = None,
    ) -> dict[str, Any]:
        return InspirationQueryCompositeResultV3(
            status=InspirationQueryCompositeStatus.FAILED,
            parent_candidate_ids=parent_candidate_ids or [],
            handoff=handoff,
            error_code=str(code),
            error_message=message,
        ).model_dump(mode="json")


def build_inspiration_query_composite_v3(
    *,
    store: LocalArtifactStore,
    runner: ContextualInspirationRunnerV3,
    memory_store: InspirationMemoryStore | None = None,
) -> StateGraph:
    return InspirationQueryCompositeGraphV3(
        store=store,
        runner=runner,
        memory_store=memory_store,
    ).build()


class InspirationQueryCompositeRuntimeV3:
    """Resolve an Agent01 run and execute the explicit contextual V3 graph."""

    def __init__(
        self,
        project_root: Path | str,
        *,
        runner: ContextualInspirationRunnerV3,
        memory_store: InspirationMemoryStore | None = None,
    ) -> None:
        self._base_runtime = InspirationCompositeRuntimeV2(
            project_root,
            runner=runner,
        )
        self.store = self._base_runtime.store
        self.project_id = self._base_runtime.project_id
        self.runner = runner
        self.graph = build_inspiration_query_composite_v3(
            store=self.store,
            runner=runner,
            memory_store=memory_store,
        ).compile()

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()

    def close(self) -> None:
        self._base_runtime.close()

    @classmethod
    def from_workspace(
        cls,
        workspace_root: Path | str,
        project_id: str,
        *,
        runner: ContextualInspirationRunnerV3,
        memory_store: InspirationMemoryStore | None = None,
    ) -> InspirationQueryCompositeRuntimeV3:
        workspace = Path(workspace_root).resolve()
        project_root = (workspace / project_id).resolve()
        if workspace not in project_root.parents:
            raise ValueError("project path escapes workspace root")
        return cls(
            project_root,
            runner=runner,
            memory_store=memory_store,
        )

    def execute(
        self,
        launch: InspirationQueryCompositeLaunchV3 | dict[str, Any],
    ) -> InspirationQueryCompositeResultV3:
        selected = InspirationQueryCompositeLaunchV3.model_validate(launch)
        base_request = self._base_runtime.resolve_request(selected.base_launch)
        request = InspirationQueryCompositeRequestV3(
            base_request=base_request,
            raw_request=selected.raw_request,
            semantic_scholar_max_results=selected.semantic_scholar_max_results,
            max_literature_queries=selected.max_literature_queries,
        )
        state = self.graph.invoke({"request": request.model_dump(mode="json")})
        return InspirationQueryCompositeResultV3.model_validate(state["result"])


def _compile_v3_literature_candidates(
    context: QueryContextV2,
    *,
    tag_graph: TagGraphV1,
    target_tag_ids: tuple[str, ...],
    max_results: int,
) -> tuple[
    tuple[SemanticScholarRequestV1, ...],
    tuple[LiteratureQueryCandidateV2, ...],
]:
    base_requests = list(
        compile_semantic_scholar_requests(
            context,
            max_results_per_request=max_results,
            max_requests=64,
        )
    )
    request_families: list[tuple[SemanticScholarRequestV1, LiteratureQueryFamily, bool]] = []
    for request in base_requests:
        if request.route is SemanticScholarRoute.TOPIC:
            family = LiteratureQueryFamily.MATERIAL
            required = len(
                [item for item, selected, _flag in request_families if selected is family]
            ) < 2
        elif request.anchor_polarity is not None and request.anchor_polarity.value == "NEGATIVE":
            family = LiteratureQueryFamily.COUNTER
            required = False
        else:
            family = LiteratureQueryFamily.CITATION
            required = not any(
                selected is LiteratureQueryFamily.CITATION
                for _item, selected, _flag in request_families
            )
        request_families.append((request, family, required))

    material = context.materials[0]
    material_terms = list(material.query_terms[:3])
    if context.mechanism_terms:
        request_families.append(
            (
                _topic_request(
                    context,
                    " ".join((*material_terms, *context.mechanism_terms[:3])),
                    max_results=max_results,
                ),
                LiteratureQueryFamily.MECHANISM,
                True,
            )
        )
    if context.operation_terms:
        request_families.append(
            (
                _topic_request(
                    context,
                    " ".join((*material_terms, *context.operation_terms[:3])),
                    max_results=max_results,
                ),
                LiteratureQueryFamily.SOFT_CHEMISTRY,
                True,
            )
        )
    for bucket in default_materials_literature_budget_v2().required_historical_buckets:
        request_families.append(
            (
                _topic_request(
                    context,
                    " ".join((*material_terms, *context.mechanism_terms[:2])),
                    max_results=max_results,
                    year_from=bucket.year_from,
                    year_to=bucket.year_to,
                ),
                LiteratureQueryFamily.HISTORICAL,
                True,
            )
        )

    tag_plan = plan_tag_queries(
        tag_graph,
        target_tag_ids=target_tag_ids,
        budget=SearchBudgetV1(
            max_queries=6,
            max_physical_requests=6,
            max_direct_queries=0,
            max_bridge_queries=4,
            max_counter_queries=2,
            max_raw_hits=60,
            max_unique_documents=30,
        ),
    )
    bridge_required = True
    for query in tag_plan.queries:
        family = (
            LiteratureQueryFamily.BRIDGE
            if query.kind.value == "BRIDGE"
            else LiteratureQueryFamily.COUNTER
        )
        request_families.append(
            (
                _topic_request(context, query.text, max_results=max_results),
                family,
                bridge_required and family is LiteratureQueryFamily.BRIDGE,
            )
        )
        if family is LiteratureQueryFamily.BRIDGE:
            bridge_required = False

    unique: dict[str, tuple[SemanticScholarRequestV1, LiteratureQueryFamily, bool]] = {}
    for request, family, required in request_families:
        unique.setdefault(request.query_id, (request, family, required))
    selected = tuple(unique[key] for key in sorted(unique))
    requests = tuple(item[0] for item in selected)
    historical_buckets = default_materials_literature_budget_v2().required_historical_buckets
    bucket_by_range = {
        (item.year_from, item.year_to): item.bucket_id for item in historical_buckets
    }
    candidates = tuple(
        make_literature_query_candidate_v2(
            provider_id="semantic-scholar",
            provider_query_id=request.query_id,
            family=family,
            year_bucket_id=bucket_by_range.get(
                (request.publication_year_from, request.publication_year_to)
            )
            if family is LiteratureQueryFamily.HISTORICAL
            else None,
            publication_year_from=request.publication_year_from
            if family is LiteratureQueryFamily.HISTORICAL
            else None,
            publication_year_to=request.publication_year_to
            if family is LiteratureQueryFamily.HISTORICAL
            else None,
            priority=_family_priority(family),
            required=required,
        )
        for request, family, required in selected
    )
    return requests, candidates


def _topic_request(
    context: QueryContextV2,
    text: str,
    *,
    max_results: int,
    year_from: int | None = None,
    year_to: int | None = None,
) -> SemanticScholarRequestV1:
    query_text = " ".join(text.split())[:512]
    payload = {
        "schema_version": SEMANTIC_SCHOLAR_REQUEST_VERSION,
        "context_id": context.context_id,
        "route": SemanticScholarRoute.TOPIC,
        "query_text": query_text,
        "anchor_paper_id": None,
        "anchor_polarity": None,
        "max_results": max_results,
        "publication_year_from": year_from,
        "publication_year_to": year_to,
    }
    return SemanticScholarRequestV1(
        query_id=deterministic_id("s2-query", payload),
        **payload,
    )


def _policy_for_candidates(
    candidates: tuple[LiteratureQueryCandidateV2, ...],
    *,
    max_queries: int,
) -> LiteratureBudgetPolicyV2:
    counts = {
        family: sum(item.family is family for item in candidates)
        for family in LiteratureQueryFamily
    }
    required_counts = {
        family: sum(item.family is family and item.required for item in candidates)
        for family in LiteratureQueryFamily
    }
    defaults = {
        item.family: item for item in default_materials_literature_budget_v2().family_reservations
    }
    minimum_total = sum(required_counts.values())
    selected_max = max(max_queries, minimum_total)
    return LiteratureBudgetPolicyV2(
        max_queries=selected_max,
        max_raw_hits=max(120, selected_max),
        max_physical_requests=max(24, selected_max),
        family_reservations=tuple(
            FamilyReservationV2(
                family=family,
                min_queries=min(required_counts[family], counts[family]),
                hit_weight=defaults[family].hit_weight,
                physical_request_weight=defaults[family].physical_request_weight,
            )
            for family in LiteratureQueryFamily
        ),
        required_historical_buckets=(
            default_materials_literature_budget_v2().required_historical_buckets
        ),
    )


def _family_priority(family: LiteratureQueryFamily) -> int:
    return {
        LiteratureQueryFamily.MATERIAL: 10,
        LiteratureQueryFamily.MECHANISM: 20,
        LiteratureQueryFamily.SOFT_CHEMISTRY: 30,
        LiteratureQueryFamily.HISTORICAL: 40,
        LiteratureQueryFamily.CITATION: 50,
        LiteratureQueryFamily.BRIDGE: 60,
        LiteratureQueryFamily.COUNTER: 70,
    }[family]


def _control_pointer(pointer: ArtifactPointerV1) -> ArtifactPointer:
    return ArtifactPointer(uri=pointer.uri, sha256=pointer.sha256)
