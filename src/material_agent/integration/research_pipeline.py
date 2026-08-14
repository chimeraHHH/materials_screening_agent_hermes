"""Direct local-research entry for the complete non-DFT workflow.

The production four-tool Gateway remains unchanged.  This module is an
explicit research-mode composition that executes Agent01, the opt-in
Agent01-to-Inspiration LangGraph bridge, DeepSeek grounded RAG, the SMACT
prior, and the registered soft-chemistry operator.  CHGNet and DeepH retain
their native scientific gates; unavailable real bindings are reported as
blocked instead of being replaced by fixtures.  DFT and many-body execution
are always skipped by this entry.
"""

from __future__ import annotations

import hashlib
import os
import subprocess
import threading
import warnings
from contextlib import contextmanager
from enum import StrEnum
from pathlib import Path
from typing import Annotated, Literal

from pydantic import Field, model_validator
from pymatgen.core import Structure
from pymatgen.io.cif import CifWriter

from material_agent.gateway.models import (
    InspirationBudgetV1,
    InspirationConstraintsV1,
    InspirationRunRequestV1,
)
from material_agent.inspiration.engine import (
    PymatgenTransformationEngine,
    _equivalent_site_groups,
)
from material_agent.inspiration.models import (
    ArtifactPointerV1,
    BridgePacketV1,
    EvidenceCardV1,
    InspirationBundleV1,
    PassageV1,
    TransformationPlanV1,
    TransformationStatus,
    canonical_json_bytes,
)
from material_agent.inspiration.runner import (
    public_inspiration_runner_from_environment,
)
from material_agent.inspiration.semantic_rag import (
    LocalRAGCandidateV1,
    SemanticRAGBudgetV1,
    SemanticRAGRequestV1,
    semantic_rag_judge_from_environment,
)
from material_agent.inspiration.tag_graph import curated_flat_band_tag_graph
from material_agent.inspiration.transformations import (
    DEFAULT_SUBSTITUTION_REGISTRY_V1,
    SubstitutionExecutionRequestV1,
    substitution_registry_bytes,
)
from material_agent.integration.request_compiler import (
    HermesInspirationRequestCompiler,
)
from material_agent.ml_screening.models import (
    ArtifactPointer as MLArtifactPointer,
    EvidenceLevel,
    MLCandidateInput,
    MLDecision,
    MLRequirementView,
    MLScreeningRequest,
    ModelHealthSnapshot,
    StructureRef,
)
from material_agent.ml_screening.planner import build_ml_stage_plan
from material_agent.ml_screening.real_resources import real_registry
from material_agent.ml_screening.resources import default_policy
from material_agent.ml_screening.worker_client import SubprocessWorkerClient
from material_agent.orchestrator.inspiration_composite import (
    InspirationCompositeLaunchV2,
    InspirationCompositeRuntimeV2,
    InspirationCompositeStatus,
)
from material_agent.orchestrator.models import ArtifactPointer, RunStatus, StrictModel
from material_agent.orchestrator.runtime import OrchestratorRuntime
from material_agent.retrieval.models import SourceDatabase
from material_agent.retrieval.storage import LocalArtifactStore
from material_agent.softchem import (
    DEFAULT_SOFTCHEM_OPERATOR_REGISTRY_V1,
    DownstreamIntent,
    SoftChemDownstreamRunner,
    SoftChemExecutionBindings,
    build_softchem_downstream_plan,
    execute_registered_softchem_operator,
    softchem_registry_bytes,
)


RESEARCH_PIPELINE_SCHEMA_VERSION = "materials-research-pipeline-v1"
RESEARCH_PIPELINE_TOOL_NAME = "materials_research_pipeline_run"
RESEARCH_PIPELINE_IMPLEMENTATION_REVISION = "research-pipeline-20260814-r4"


class ResearchStageStatus(StrEnum):
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    PARTIAL = "PARTIAL"
    BLOCKED = "BLOCKED"
    FAILED = "FAILED"
    SKIPPED = "SKIPPED"


class ResearchPipelineStatus(StrEnum):
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    PARTIAL = "PARTIAL"
    FAILED = "FAILED"


class ResearchPipelineRunRequestV1(StrictModel):
    """One bounded TiS2 -> TiSe2 non-DFT research invocation."""

    schema_version: Literal["materials-research-pipeline-v1"] = (
        RESEARCH_PIPELINE_SCHEMA_VERSION
    )
    workflow: Literal["TIS2_TO_TISE2_NARROW_BAND_V1"] = (
        "TIS2_TO_TISE2_NARROW_BAND_V1"
    )
    submission_id: str | None = Field(
        default=None,
        min_length=1,
        max_length=64,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$",
        description=(
            "Optional caller label. Omit it for normal natural-language use; "
            "the service derives a stable identifier from the scientific request."
        ),
    )
    goal: str = Field(min_length=1, max_length=4_000)
    publication_year_from: int = Field(default=1960, ge=1600, le=2200)
    publication_year_to: int = Field(default=2026, ge=1600, le=2200)
    max_parent_candidates: int = Field(default=8, ge=1, le=16)
    top_k: int = Field(default=3, ge=1, le=8)
    enable_deepseek_rag: bool = True
    run_chgnet: bool = True
    run_deeph: bool = True
    skip_dft: Literal[True] = True
    skip_many_body: Literal[True] = True

    @model_validator(mode="after")
    def validate_years(self) -> "ResearchPipelineRunRequestV1":
        if self.publication_year_from > self.publication_year_to:
            raise ValueError("publication_year_from must not exceed publication_year_to")
        return self


class ResearchStageRecordV1(StrictModel):
    stage_id: Literal[
        "agent01",
        "literature_inspiration",
        "semantic_rag",
        "smact_softchem",
        "chgnet",
        "deeph",
        "dft",
        "many_body",
    ]
    status: ResearchStageStatus
    reason_codes: tuple[str, ...] = Field(min_length=1, max_length=32)
    artifacts: tuple[ArtifactPointerV1, ...] = Field(default=(), max_length=32)
    summary: str = Field(min_length=1, max_length=2_000)


class ResearchPipelineResultV1(StrictModel):
    schema_version: Literal["materials-research-pipeline-v1"] = (
        RESEARCH_PIPELINE_SCHEMA_VERSION
    )
    run_id: str
    submission_id: str
    request_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    implementation_revision: Literal[
        "research-pipeline-unversioned-legacy-v1",
        "research-pipeline-20260814-r2",
        "research-pipeline-20260814-r3",
        "research-pipeline-20260814-r4",
    ] = "research-pipeline-unversioned-legacy-v1"
    status: ResearchPipelineStatus
    source_run_id: str | None = None
    inspiration_run_id: str | None = None
    selected_candidate_ids: tuple[str, ...] = ()
    stages: tuple[ResearchStageRecordV1, ...]
    result_artifact_uri: str
    evidence_boundary: Literal[
        "HYPOTHESIS_AND_STRUCTURE_PROPOSAL_ONLY_NO_PROPERTY_CONCLUSION"
    ] = "HYPOTHESIS_AND_STRUCTURE_PROPOSAL_ONLY_NO_PROPERTY_CONCLUSION"
    scientific_conclusion: Literal[False] = False


class ResearchPipelineInProgress(RuntimeError):
    """A matching canonical run is still owned by another live executor."""


def research_pipeline_tool_manifest() -> tuple[dict[str, object], ...]:
    return (
        {
            "name": RESEARCH_PIPELINE_TOOL_NAME,
            "description": (
                "Run the direct local non-DFT research flow: C2DB Agent01, "
                "LangGraph Inspiration, DeepSeek grounded RAG, SMACT, registered "
                "soft chemistry, then native CHGNet/DeepH gates."
            ),
            "inputSchema": ResearchPipelineRunRequestV1.model_json_schema(),
            "outputSchema": ResearchPipelineResultV1.model_json_schema(),
            "readOnly": False,
        },
    )


class ResearchPipelineService:
    """Synchronous, idempotent local-research composition."""

    def __init__(
        self,
        *,
        workspace: Path | str,
        project_id: str,
        smact_worker_python: Path | str | None = None,
        chgnet_worker_python: Path | str | None = None,
    ) -> None:
        workspace_root = Path(workspace).resolve()
        suffix = "-research"
        bounded_project_id = f"{project_id[: 64 - len(suffix)]}{suffix}"
        project = OrchestratorRuntime.create_project(
            workspace_root, bounded_project_id
        )
        self.workspace = workspace_root
        self.project_id = bounded_project_id
        self.project_root = Path(project["project_root"])
        self.store = LocalArtifactStore(self.project_root)
        self.smact_worker_python = (
            Path(smact_worker_python).resolve()
            if smact_worker_python is not None
            else None
        )
        self.chgnet_worker_python = (
            Path(chgnet_worker_python).resolve()
            if chgnet_worker_python is not None
            else None
        )
        self._jobs_lock = threading.Lock()
        self._jobs: dict[str, threading.Thread] = {}
        self._reconcile_persisted_terminal_results()

    @staticmethod
    def _identity(
        request: ResearchPipelineRunRequestV1 | dict[str, object],
    ) -> tuple[ResearchPipelineRunRequestV1, str, str, str, str]:
        selected = ResearchPipelineRunRequestV1.model_validate(request)
        if selected.submission_id is None:
            semantic_payload = selected.model_dump(
                mode="json", exclude={"submission_id"}
            )
            semantic_sha256 = hashlib.sha256(
                canonical_json_bytes(semantic_payload)
            ).hexdigest()
            selected = selected.model_copy(
                update={"submission_id": f"auto-{semantic_sha256[:24]}"}
            )
        identity_payload = {
            "implementation_revision": RESEARCH_PIPELINE_IMPLEMENTATION_REVISION,
            "request": selected.model_dump(mode="json"),
        }
        request_sha256 = hashlib.sha256(
            canonical_json_bytes(identity_payload)
        ).hexdigest()
        token = request_sha256[:20]
        return (
            selected,
            request_sha256,
            f"research-{token}",
            f"agent01-{token}",
            f"inspiration-{token}",
        )

    def submit(
        self, request: ResearchPipelineRunRequestV1 | dict[str, object]
    ) -> ResearchPipelineResultV1:
        """Start or poll one canonical background run without blocking MCP."""

        selected, request_sha256, run_id, source_run_id, inspiration_run_id = (
            self._identity(request)
        )
        result_uri = f"artifact://research_pipeline/{run_id}/result.json"
        if self.store.exists(result_uri):
            prior = _strict_json(
                ResearchPipelineResultV1, self.store.read_json(result_uri)
            )
            if prior.request_sha256 != request_sha256:
                raise ValueError("stored research run does not match the request")
            self._reconcile_terminal_result(prior)
            return prior
        with self._jobs_lock:
            thread = self._jobs.get(request_sha256)
            if thread is None or not thread.is_alive():
                thread = threading.Thread(
                    target=self._background_run,
                    args=(selected, request_sha256),
                    name=f"research-pipeline-{request_sha256[:12]}",
                    daemon=True,
                )
                self._jobs[request_sha256] = thread
                thread.start()
        return ResearchPipelineResultV1(
            run_id=run_id,
            submission_id=selected.submission_id,
            request_sha256=request_sha256,
            implementation_revision=RESEARCH_PIPELINE_IMPLEMENTATION_REVISION,
            status=ResearchPipelineStatus.RUNNING,
            source_run_id=source_run_id,
            inspiration_run_id=inspiration_run_id,
            stages=(
                ResearchStageRecordV1(
                    stage_id="agent01",
                    status=ResearchStageStatus.RUNNING,
                    reason_codes=("CANONICAL_BACKGROUND_RUN_ACTIVE",),
                    summary=(
                        "The canonical request is running in the shared MCP Hub; "
                        "repeat the same request to poll or recover it."
                    ),
                ),
            ),
            result_artifact_uri=result_uri,
        )

    def _background_run(
        self, request: ResearchPipelineRunRequestV1, request_sha256: str
    ) -> None:
        try:
            self.run(request)
        except ResearchPipelineInProgress:
            # Another process still owns the exact Orchestrator advancement.
            # A later canonical poll will retry after that owner exits.
            return
        finally:
            with self._jobs_lock:
                current = self._jobs.get(request_sha256)
                if current is threading.current_thread():
                    self._jobs.pop(request_sha256, None)

    def run(
        self, request: ResearchPipelineRunRequestV1 | dict[str, object]
    ) -> ResearchPipelineResultV1:
        selected, request_sha256, run_id, _, _ = self._identity(request)
        with self._canonical_run_lock(run_id):
            result_uri = f"artifact://research_pipeline/{run_id}/result.json"
            if self.store.exists(result_uri):
                prior = _strict_json(
                    ResearchPipelineResultV1, self.store.read_json(result_uri)
                )
                if prior.request_sha256 != request_sha256:
                    raise ValueError("stored research run does not match the request")
                self._reconcile_terminal_result(prior)
                return prior
            return self._run_unlocked(selected)

    def _run_unlocked(
        self, request: ResearchPipelineRunRequestV1 | dict[str, object]
    ) -> ResearchPipelineResultV1:
        selected, request_sha256, run_id, source_run_id, inspiration_run_id = (
            self._identity(request)
        )
        result_path = f"research_pipeline/{run_id}/result.json"
        result_uri = f"artifact://{result_path}"
        stages: list[ResearchStageRecordV1] = []
        selected_ids: tuple[str, ...] = ()
        inspiration_bundle: InspirationBundleV1 | None = None

        try:
            agent01_view = self._run_agent01(selected, source_run_id)
            manifest = self._agent01_manifest_pointer(source_run_id)
            stages.append(
                ResearchStageRecordV1(
                    stage_id="agent01",
                    status=ResearchStageStatus.SUCCEEDED,
                    reason_codes=("REAL_C2DB_DYNAMIC_CANDIDATES_PUBLISHED",),
                    artifacts=(manifest,),
                    summary=(
                        f"C2DB retrieval published {len(agent01_view.candidate_ids)} "
                        "hash-bound dynamic parent candidates."
                    ),
                )
            )
        except ResearchPipelineInProgress:
            raise
        except Exception as exc:
            self._seal_agent01_failure(source_run_id, exc)
            stages.append(self._failure_stage("agent01", exc))
            return self._finish(
                selected,
                request_sha256=request_sha256,
                run_id=run_id,
                result_path=result_path,
                source_run_id=source_run_id,
                inspiration_run_id=None,
                selected_ids=(),
                stages=stages,
            )

        try:
            (
                inspiration_bundle,
                inspiration_artifacts,
                composite_status,
            ) = self._run_inspiration(
                selected,
                source_run_id=source_run_id,
                inspiration_run_id=inspiration_run_id,
            )
            selected_ids = tuple(
                candidate.candidate_id
                for candidate in inspiration_bundle.selected_candidates
            )
            if not selected_ids:
                selected_ids = self._reviewable_plan_ids(
                    inspiration_run_id, limit=selected.top_k
                )
            literature_status = (
                ResearchStageStatus.SUCCEEDED
                if composite_status is InspirationCompositeStatus.SUCCEEDED
                else ResearchStageStatus.PARTIAL
            )
            stages.append(
                ResearchStageRecordV1(
                    stage_id="literature_inspiration",
                    status=literature_status,
                    reason_codes=(
                        "LANGGRAPH_AGENT01_TO_INSPIRATION_SUCCEEDED",
                        "PUBLIC_METADATA_LITERATURE_RETRIEVED",
                        *(
                            ("SMACT_REVIEWABLE_PROPOSALS_RETAINED",)
                            if literature_status is ResearchStageStatus.PARTIAL
                            else ()
                        ),
                    ),
                    artifacts=inspiration_artifacts,
                    summary=(
                        f"LangGraph consumed Agent01 parents and selected "
                        f"{len(selected_ids)} structure hypotheses for the "
                        "SMACT-first research path."
                    ),
                )
            )
        except Exception as exc:
            stages.append(self._failure_stage("literature_inspiration", exc))
            self._append_skipped_tail(stages, from_stage="semantic_rag")
            return self._finish(
                selected,
                request_sha256=request_sha256,
                run_id=run_id,
                result_path=result_path,
                source_run_id=source_run_id,
                inspiration_run_id=inspiration_run_id,
                selected_ids=(),
                stages=stages,
            )

        if selected.enable_deepseek_rag:
            try:
                selected_ids, rag_pointer = self._run_semantic_rag(
                    selected, inspiration_run_id, inspiration_bundle
                )
                stages.append(
                    ResearchStageRecordV1(
                        stage_id="semantic_rag",
                        status=ResearchStageStatus.SUCCEEDED,
                        reason_codes=("DEEPSEEK_GROUNDED_RERANK_SUCCEEDED",),
                        artifacts=(rag_pointer,),
                        summary=(
                            "DeepSeek reranked only the persisted passages, evidence "
                            "cards, and local candidate descriptions with closed citations."
                        ),
                    )
                )
            except Exception as exc:
                stages.append(self._failure_stage("semantic_rag", exc))
        else:
            stages.append(
                self._skipped("semantic_rag", "SEMANTIC_RAG_DISABLED_BY_REQUEST")
            )

        try:
            downstream, downstream_pointer = self._run_softchem(
                selected,
                inspiration_run_id=inspiration_run_id,
                selected_candidate_id=selected_ids[0],
            )
            operator_status = ResearchStageStatus(
                "SUCCEEDED"
                if downstream.operator.status.value == "SUCCEEDED"
                else "BLOCKED"
            )
            stages.append(
                ResearchStageRecordV1(
                    stage_id="smact_softchem",
                    status=operator_status,
                    reason_codes=downstream.operator.reason_codes,
                    artifacts=(downstream_pointer,),
                    summary=(
                        "The independent SMACT prior ran before the registered "
                        "equivalent-site S-to-Se operator."
                    ),
                )
            )
            stages.append(
                ResearchStageRecordV1(
                    stage_id="chgnet",
                    status=ResearchStageStatus(
                        "SUCCEEDED"
                        if downstream.chgnet.status.value == "SUCCEEDED"
                        else "BLOCKED"
                    ),
                    reason_codes=downstream.chgnet.reason_codes,
                    summary=(
                        "Native CHGNet gate executed; only a real reviewed binding can "
                        "publish an L2 relaxed structure."
                    ),
                )
            )
            stages.append(
                ResearchStageRecordV1(
                    stage_id="deeph",
                    status=ResearchStageStatus(
                        "SUCCEEDED"
                        if downstream.deeph.status.value == "SUCCEEDED"
                        else "BLOCKED"
                    ),
                    reason_codes=downstream.deeph.reason_codes,
                    summary=(
                        "DeepH is conditional on a QC-passed CHGNet relaxed structure "
                        "and a real reviewed model binding."
                    ),
                )
            )
        except Exception as exc:
            stages.append(self._failure_stage("smact_softchem", exc))
            stages.append(self._blocked("chgnet", "SOFTCHEM_OUTPUT_UNAVAILABLE"))
            stages.append(self._blocked("deeph", "CHGNET_OUTPUT_UNAVAILABLE"))

        stages.append(self._skipped("dft", "DFT_EXCLUDED_BY_RESEARCH_ENTRY"))
        stages.append(
            self._skipped(
                "many_body", "MANY_BODY_REQUIRES_DFT_AND_IS_EXCLUDED_BY_ENTRY"
            )
        )
        return self._finish(
            selected,
            request_sha256=request_sha256,
            run_id=run_id,
            result_path=result_path,
            source_run_id=source_run_id,
            inspiration_run_id=inspiration_run_id,
            selected_ids=selected_ids,
            stages=stages,
        )

    def _run_agent01(self, request: ResearchPipelineRunRequestV1, run_id: str):
        requirement = {
            "requirement_id": f"req-{run_id}",
            "revision": 1,
            "target_class": "layered transition metal dichalcogenide",
            "hard_constraints": {
                "exact_formula": "TiS2",
                "include_elements": ["S", "Ti"],
                "exclude_elements": [],
                "dimensionality": 2,
                "max_num_sites": 32,
            },
            "scientific_targets": [
                {
                    "name": "narrow electronic band",
                    "operational_definition": (
                        "literature-supported hypothesis requiring downstream validation"
                    ),
                    "required_evidence_level": "L1_RETRIEVED",
                }
            ],
            "ranking_preferences": [
                {"property": "num_sites", "mode": "minimize"}
            ],
            "budget": {
                "max_candidates": request.max_parent_candidates,
                "allow_ml": False,
                "allow_dft": False,
                "allow_many_body": False,
            },
            "confirmed_by_user": True,
            "policy_version": "requirement-policy-v1",
        }
        with OrchestratorRuntime(self.project_root) as runtime:
            if runtime.repository.get_run(run_id) is None:
                view = runtime.start_run(
                    raw_request=request.goal,
                    initial_requirement=requirement,
                    run_id=run_id,
                    retrieval_source=SourceDatabase.C2DB,
                )
                if view.interrupts:
                    pending = view.interrupts[0].value
                    view = runtime.approve(
                        run_id=run_id,
                        approval_id=pending["approval_id"],
                        decision="approve",
                        reason="direct local research entry",
                    )
            else:
                view = runtime.status(run_id)
                if view.status is RunStatus.RUNNING:
                    try:
                        view = runtime.resume(run_id=run_id)
                    except RuntimeError as exc:
                        if "already being advanced" in str(exc):
                            raise ResearchPipelineInProgress(
                                "Agent01 is owned by another live executor"
                            ) from exc
                        raise
            if (
                view.status is RunStatus.PAUSED
                and view.interrupts
                and view.interrupts[0].value.get("interaction_type")
                == "RETRY_CONFIRMATION"
            ):
                view = runtime.retry(run_id=run_id)
            if view.status is not RunStatus.SUCCEEDED:
                raise RuntimeError(f"Agent01 ended with {view.status.value}")
            if not view.candidate_ids:
                raise RuntimeError("Agent01 published no candidates")
            return view

    def _seal_agent01_failure(self, run_id: str, exc: Exception) -> None:
        with OrchestratorRuntime(self.project_root) as runtime:
            if runtime.repository.get_run(run_id) is None:
                return
            runtime.seal_failed(
                run_id=run_id,
                category="RESEARCH_PIPELINE_AGENT01_EXCEPTION",
                operation="agent01",
                public_message=f"Agent01 aborted before outcome recording ({type(exc).__name__}).",
            )

    def _reconcile_terminal_result(self, result: ResearchPipelineResultV1) -> None:
        if (
            result.status is not ResearchPipelineStatus.FAILED
            or result.source_run_id is None
        ):
            return
        with OrchestratorRuntime(self.project_root) as runtime:
            row = runtime.repository.get_run(result.source_run_id)
            if row is None or row["status"] in {
                RunStatus.FAILED.value,
                RunStatus.SUCCEEDED.value,
                RunStatus.PARTIAL.value,
                RunStatus.CANCELLED.value,
            }:
                return
            runtime.seal_failed(
                run_id=result.source_run_id,
                category="RESEARCH_PIPELINE_TERMINAL_RECONCILIATION",
                operation="agent01",
                public_message=(
                    "A persisted research-pipeline failure supersedes the stale "
                    "nonterminal Orchestrator projection."
                ),
            )

    def _reconcile_persisted_terminal_results(self) -> None:
        root = self.project_root / "research_pipeline"
        if not root.is_dir():
            return
        for result_path in sorted(root.glob("research-*/result.json")):
            relative = result_path.relative_to(self.project_root).as_posix()
            result = _strict_json(
                ResearchPipelineResultV1, self.store.read_json(relative)
            )
            self._reconcile_terminal_result(result)

    @contextmanager
    def _canonical_run_lock(self, run_id: str):
        """Serialize the whole canonical flow across MCP clients/processes."""

        lock_path = self.project_root / "research_pipeline" / run_id / "run.lock"
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        with lock_path.open("a+b") as handle:
            if os.name == "nt":  # pragma: no cover - production target is POSIX
                import msvcrt

                handle.seek(0, 2)
                if handle.tell() == 0:
                    handle.write(b"\0")
                    handle.flush()
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                if os.name == "nt":  # pragma: no cover
                    handle.seek(0)
                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    def _agent01_manifest_pointer(self, run_id: str) -> ArtifactPointerV1:
        uri = f"artifact://stages/agent01/{run_id}/candidate_manifest.jsonl"
        return self._v1(self.store.inspect(uri, media_type="application/x-ndjson"))

    def _run_inspiration(
        self,
        request: ResearchPipelineRunRequestV1,
        *,
        source_run_id: str,
        inspiration_run_id: str,
    ) -> tuple[
        InspirationBundleV1,
        tuple[ArtifactPointerV1, ...],
        InspirationCompositeStatus,
    ]:
        gateway_request = InspirationRunRequestV1(
            submission_id=request.submission_id,
            goal=request.goal,
            constraints=InspirationConstraintsV1(
                required_elements=("Se", "Ti"),
                excluded_elements=(),
                material_classes=("layered transition-metal dichalcogenide",),
                dimensionality="2D",
                target_features=("narrow electronic band",),
                top_k=request.top_k,
                require_diverse_routes=request.top_k >= 2,
                budget=InspirationBudgetV1(
                    max_search_requests=8,
                    max_unique_documents=8,
                    max_passages=12,
                    max_model_calls=0,
                    max_walltime_seconds=600,
                ),
            ),
        )
        compiled = HermesInspirationRequestCompiler().compile(gateway_request)
        search = compiled.policy.search.model_copy(
            update={
                "publication_year_from": request.publication_year_from,
                "publication_year_to": request.publication_year_to,
            }
        )
        policy = compiled.policy.model_copy(update={"search": search})
        tag_graph = curated_flat_band_tag_graph()
        prefix = f"research_pipeline/{inspiration_run_id}/inputs"
        policy_ref = self.store.write_json(
            f"{prefix}/policy.json", policy.model_dump(mode="json"), immutable=True
        )
        graph_ref = self.store.write_json(
            f"{prefix}/tag_graph.json",
            tag_graph.model_dump(mode="json"),
            immutable=True,
        )
        registry_ref = self.store.write_bytes(
            f"{prefix}/substitution_registry.json",
            substitution_registry_bytes(DEFAULT_SUBSTITUTION_REGISTRY_V1),
            media_type="application/json",
            immutable=True,
        )
        runner = public_inspiration_runner_from_environment(
            store=self.store,
            policy=policy,
            transformation_engine=PymatgenTransformationEngine(),
        )
        launch = InspirationCompositeLaunchV2(
            request_id=request.submission_id,
            source_run_id=source_run_id,
            inspiration_run_id=inspiration_run_id,
            requirement_revision=1,
            policy_artifact=self._control(policy_ref),
            tag_graph_artifact=self._control(graph_ref),
            transformation_registry_artifact=self._control(registry_ref),
            target_tag_ids=list(compiled.target_tag_ids),
            max_parent_candidates=request.max_parent_candidates,
        )
        with InspirationCompositeRuntimeV2(
            self.project_root, runner=runner
        ) as composite:
            result = composite.execute(launch)
        if result.status not in {
            InspirationCompositeStatus.SUCCEEDED,
            InspirationCompositeStatus.SCIENTIFIC_NO_MATCH,
        }:
            raise RuntimeError(
                f"Inspiration composite ended with {result.status.value}: "
                f"{result.error_code or 'NO_MATCH'}"
            )
        if result.bundle_artifact is None or result.stage_result_artifact is None:
            raise RuntimeError("Inspiration composite omitted terminal artifacts")
        bundle = _strict_json(
            InspirationBundleV1, self.store.read_json(result.bundle_artifact.uri)
        )
        if (
            result.status is InspirationCompositeStatus.SCIENTIFIC_NO_MATCH
            and not self._reviewable_plan_ids(inspiration_run_id, limit=request.top_k)
        ):
            raise RuntimeError("Inspiration produced no SMACT-reviewable structure plan")
        return (
            bundle,
            (
                self._v1(self.store.inspect(result.bundle_artifact.uri)),
                self._v1(self.store.inspect(result.stage_result_artifact.uri)),
            ),
            result.status,
        )

    def _run_semantic_rag(
        self,
        request: ResearchPipelineRunRequestV1,
        inspiration_run_id: str,
        bundle: InspirationBundleV1,
    ) -> tuple[tuple[str, ...], ArtifactPointerV1]:
        prefix = f"artifact://stages/inspiration/{inspiration_run_id}"
        passages = tuple(
            _strict_json(PassageV1, item)
            for item in self.store.read_jsonl(f"{prefix}/passages.jsonl")
        )
        cards = tuple(
            _strict_json(EvidenceCardV1, item)
            for item in self.store.read_jsonl(f"{prefix}/evidence_cards.jsonl")
        )
        if bundle.selected_candidates:
            candidates = tuple(
                LocalRAGCandidateV1(
                    candidate_id=item.candidate_id,
                    description=(
                        f"Structure {item.canonical_structure_id}; mechanisms "
                        f"{', '.join(item.mechanism_tag_ids)}; "
                        f"{len(item.merged_routes)} retained physical route(s)."
                    ),
                    evidence_card_ids=item.evidence_card_ids,
                )
                for item in bundle.selected_candidates
            )
        else:
            proposals = tuple(
                _strict_json(TransformationPlanV1, item)
                for item in self.store.read_jsonl(
                    f"{prefix}/transformation_proposals.jsonl"
                )
            )
            bridges = {
                item.bridge_packet_id: item
                for item in (
                    _strict_json(BridgePacketV1, raw)
                    for raw in self.store.read_jsonl(
                        f"{prefix}/bridge_packets.jsonl"
                    )
                )
            }
            candidates = tuple(
                LocalRAGCandidateV1(
                    candidate_id=plan.plan_id,
                    description=(
                        f"SMACT-reviewable S-to-Se plan from parent "
                        f"{plan.parent_structure_id}; structural QC passed and "
                        "the source CIF lacked explicit oxidation states."
                    ),
                    evidence_card_ids=tuple(
                        sorted(
                            {
                                card_id
                                for bridge_id in plan.bridge_packet_ids
                                for card_id in bridges[bridge_id].evidence_card_ids
                            }
                        )
                    ),
                )
                for plan in proposals[: request.top_k]
            )
        requested_card_ids = {
            card_id for candidate in candidates for card_id in candidate.evidence_card_ids
        }
        selected_cards = tuple(
            card for card in cards if card.evidence_card_id in requested_card_ids
        )
        requested_passage_ids = {
            passage_id
            for card in selected_cards
            for passage_id in card.passage_ids
        }
        selected_passages = tuple(
            passage for passage in passages if passage.passage_id in requested_passage_ids
        )
        judge = semantic_rag_judge_from_environment()
        if judge is None:
            raise RuntimeError("DeepSeek semantic RAG is not enabled in the environment")
        rag_request = SemanticRAGRequestV1(
            request_id=f"rag-{inspiration_run_id}",
            query=request.goal,
            passages=selected_passages[:16],
            evidence_cards=selected_cards[:16],
            candidates=candidates,
        )
        result = judge.rerank(rag_request, budget=SemanticRAGBudgetV1())
        ref = self.store.write_json(
            f"stages/inspiration/{inspiration_run_id}/semantic_rag.deepseek.json",
            result.model_dump(mode="json"),
            immutable=True,
        )
        ordered = tuple(
            item.candidate_id for item in sorted(result.judgements, key=lambda x: x.rank)
        )
        return ordered, self._v1(ref)

    def _run_softchem(
        self,
        request: ResearchPipelineRunRequestV1,
        *,
        inspiration_run_id: str,
        selected_candidate_id: str,
    ):
        proposals_uri = (
            f"artifact://stages/inspiration/{inspiration_run_id}/"
            "transformation_proposals.jsonl"
        )
        bundle = _strict_json(
            InspirationBundleV1,
            self.store.read_json(
                f"artifact://stages/inspiration/{inspiration_run_id}/"
                "inspiration_bundle.json"
            )
        )
        plans = tuple(
            _strict_json(TransformationPlanV1, item)
            for item in self.store.read_jsonl(proposals_uri)
        )
        matching_candidate = next(
            (
                item
                for item in bundle.selected_candidates
                if item.candidate_id == selected_candidate_id
            ),
            None,
        )
        representative_plan_id = (
            matching_candidate.representative_plan_id
            if matching_candidate is not None
            else selected_candidate_id
        )
        executed_plan = next(
            plan for plan in plans if plan.plan_id == representative_plan_id
        )
        parent_bytes = self.store.read_bytes(
            executed_plan.parent_structure_artifact.uri
        )
        parent = Structure.from_str(parent_bytes.decode("utf-8"), fmt="cif")
        charged_parent = parent.copy()
        charged_parent.add_oxidation_state_by_guess()
        observed_states = tuple(
            (site.specie.symbol, float(site.specie.oxi_state))
            for site in charged_parent
        )
        if any(
            state != ("Ti", 4.0) and state != ("S", -2.0)
            for state in observed_states
        ) or abs(float(charged_parent.charge or 0.0)) > 1e-8:
            raise RuntimeError(
                "TiS2 oxidation-state assignment did not resolve to Ti4+/S2-"
            )
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            charged_text = str(
                CifWriter(
                    charged_parent,
                    symprec=None,
                    write_magmoms=False,
                    significant_figures=12,
                )
            )
        charged_text = charged_text.replace("\r\n", "\n")
        if not charged_text.endswith("\n"):
            charged_text += "\n"
        parent_bytes = charged_text.encode("utf-8")
        charged_ref = self.store.write_bytes(
            (
                f"research_pipeline/{inspiration_run_id}/charged_parents/"
                f"{executed_plan.parent_structure_id}.cif"
            ),
            parent_bytes,
            media_type="chemical/x-cif",
            immutable=True,
        )
        parent = Structure.from_str(parent_bytes.decode("utf-8"), fmt="cif")
        planned_payload = executed_plan.model_dump(mode="python")
        planned_payload.update(
            {
                "status": TransformationStatus.PLANNED,
                "validation_checks": (),
                "output_structure_id": None,
                "output_structure_artifact": None,
                "parent_structure_artifact": self._v1(charged_ref),
            }
        )
        planned = TransformationPlanV1.model_validate(planned_payload)
        substitution_payload = substitution_registry_bytes(
            DEFAULT_SUBSTITUTION_REGISTRY_V1
        )
        substitution_ref = self.store.write_bytes(
            f"research_pipeline/{inspiration_run_id}/inputs/substitution_registry.json",
            substitution_payload,
            media_type="application/json",
            immutable=True,
        )
        execution_request = SubstitutionExecutionRequestV1(
            plan=planned,
            registry_artifact=self._v1(substitution_ref),
            equivalent_site_groups=_equivalent_site_groups(parent),
            allowed_output_elements=tuple(
                sorted(
                    {site.specie.symbol for site in parent}
                    | {planned.parameters.target_species}
                )
            ),
            allowed_output_dimensionalities=(2,),
            max_sites=256,
        )
        softchem_payload = softchem_registry_bytes(
            DEFAULT_SOFTCHEM_OPERATOR_REGISTRY_V1
        )
        softchem_ref = self.store.write_bytes(
            f"research_pipeline/{inspiration_run_id}/inputs/softchem_registry.json",
            softchem_payload,
            media_type="application/json",
            immutable=True,
        )
        prior_evaluator = None
        if self.smact_worker_python is not None:
            from material_agent.softchem.prior_client import (
                SmactPriorSubprocessClient,
            )

            repository_root = Path(__file__).resolve().parents[3]
            prior_evaluator = SmactPriorSubprocessClient(
                python_executable=self.smact_worker_python,
                source_root=repository_root / "src",
                base_lock_path=repository_root / "requirements.lock",
                package_lock_path=repository_root / "requirements-smact.lock",
            )
        transformation = execute_registered_softchem_operator(
            execution_request,
            parent_structure=parent,
            parent_artifact_bytes=parent_bytes,
            operator_registry=DEFAULT_SOFTCHEM_OPERATOR_REGISTRY_V1,
            operator_registry_artifact=self._v1(softchem_ref),
            prior_evaluator=prior_evaluator,
        )
        chgnet_plan = None
        chgnet_worker = None
        if request.run_chgnet and self.chgnet_worker_python is not None:
            chgnet_plan, chgnet_worker = self._build_chgnet_binding(
                inspiration_run_id=inspiration_run_id,
                transformation=transformation,
            )
        downstream_plan = build_softchem_downstream_plan(
            transformation,
            operator_registry=DEFAULT_SOFTCHEM_OPERATOR_REGISTRY_V1,
            operator_registry_artifact=self._v1(softchem_ref),
            chgnet_plan=chgnet_plan,
            deeph_intent=(
                DownstreamIntent.RUN if request.run_deeph else DownstreamIntent.SKIP
            ),
            dft_intent=DownstreamIntent.SKIP,
        )
        downstream = SoftChemDownstreamRunner(artifact_store=self.store).execute(
            downstream_plan,
            transformation=transformation,
            operator_registry=DEFAULT_SOFTCHEM_OPERATOR_REGISTRY_V1,
            bindings=SoftChemExecutionBindings(
                chgnet_plan=chgnet_plan,
                chgnet_worker=chgnet_worker,
            ),
        )
        ref = self.store.write_json(
            f"research_pipeline/{inspiration_run_id}/downstream_result.json",
            downstream.model_dump(mode="json"),
            immutable=True,
        )
        return downstream, self._v1(ref)

    def _build_chgnet_binding(
        self,
        *,
        inspiration_run_id: str,
        transformation,
    ):
        """Bind the real worker while preserving the audited applicability Gate.

        A successful Si health probe proves worker/checkpoint integrity only.  The
        native Agent02 planner still decides whether the transformed TiSe2 input
        lies inside the reviewed scientific domain; no out-of-domain override is
        introduced here.
        """

        worker_python = self.chgnet_worker_python
        proposed = transformation.plan.output_structure_artifact
        structure = transformation.output_structure
        payload = transformation.artifact_bytes
        if worker_python is None or proposed is None or structure is None or payload is None:
            raise ValueError("CHGNet binding requires a persisted transformed structure")
        if not worker_python.is_absolute() or not worker_python.is_file():
            raise ValueError("configured CHGNet worker Python is unavailable")

        relative = proposed.uri.removeprefix("artifact://")
        if relative == proposed.uri:
            raise ValueError("CHGNet transformed structure must use an Artifact URI")
        persisted = self.store.write_bytes(
            relative,
            payload,
            media_type=proposed.media_type,
            immutable=True,
        )
        if persisted.sha256 != proposed.sha256:
            raise ValueError("persisted CHGNet input differs from transformation output")

        repository_root = Path(__file__).resolve().parents[3]
        lock_path = repository_root / "requirements-agent02.lock"
        source_root = repository_root / "src"
        fixture = repository_root / "tests/fixtures/real_ml/si-diamond.cif"
        runtime_root = self.project_root / "research_pipeline/runtime/chgnet"
        runtime_root.mkdir(parents=True, exist_ok=True)
        process = subprocess.run(
            [
                str(worker_python),
                "-m",
                "material_agent.ml_screening.chgnet_worker",
                "--artifact-root",
                str(self.project_root),
                "--package-lock",
                str(lock_path),
                "--health-structure",
                str(fixture),
                "--device",
                "cpu",
            ],
            check=False,
            capture_output=True,
            timeout=180,
            env={
                "PATH": os.environ.get("PATH", ""),
                "PYTHONPATH": str(source_root),
                "PYTHONNOUSERSITE": "1",
                "PYTHONDONTWRITEBYTECODE": "1",
                "MPLCONFIGDIR": str(runtime_root / "mpl"),
                "LANG": os.environ.get("LANG", "C.UTF-8"),
            },
        )
        if process.returncode != 0:
            raise ValueError("real CHGNet health probe failed")
        health = ModelHealthSnapshot.model_validate_json(process.stdout)
        policy = default_policy()
        registry = real_registry()

        prefix = f"research_pipeline/{inspiration_run_id}/inputs/chgnet"
        policy_ref = self.store.write_json(
            f"{prefix}/policy.json", policy.model_dump(mode="json"), immutable=True
        )
        registry_ref = self.store.write_json(
            f"{prefix}/registry.json", registry.model_dump(mode="json"), immutable=True
        )
        health_ref = self.store.write_json(
            f"{prefix}/health.json", health.model_dump(mode="json"), immutable=True
        )
        requirement = MLRequirementView(
            requirement_id=f"requirement-{inspiration_run_id}",
            revision=1,
            confirmed_by_user=True,
            allow_ml=True,
            include_elements=["Se", "Ti"],
            dimensionality=2,
            max_num_sites=256,
        )
        requirement_ref = self.store.write_json(
            f"{prefix}/requirement.json",
            requirement.model_dump(mode="json"),
            immutable=True,
        )
        proposal_ref = self.store.inspect(
            f"artifact://stages/inspiration/{inspiration_run_id}/transformation_proposals.jsonl"
        )
        elements = sorted(item.symbol for item in structure.composition.elements)
        distances = [
            float(structure.distance_matrix[i][j])
            for i in range(len(structure))
            for j in range(i + 1, len(structure))
        ]
        candidate = MLCandidateInput(
            candidate_id=transformation.plan.plan_id,
            upstream_manifest_uri=proposal_ref.uri,
            upstream_manifest_sha256=proposal_ref.sha256,
            formula=structure.composition.reduced_formula,
            elements=elements,
            num_sites=len(structure),
            publication_rank=1,
            upstream_decision=MLDecision.PASS,
            upstream_evidence_level=EvidenceLevel.L1_RETRIEVED,
            source_structure=StructureRef(
                structure_id=transformation.plan.output_structure_id,
                uri=proposed.uri,
                sha256=proposed.sha256,
                num_sites=len(structure),
                elements=elements,
                dimensionality=2,
                is_periodic=True,
                is_inorganic=True,
                parseable=True,
                ase_compatible=True,
                has_finite_values=True,
                positive_volume=bool(structure.volume > 0),
                minimum_distance_angstrom=min(distances) if distances else None,
                hash_verified=True,
            ),
        )
        snapshot_ref = self.store.write_json(
            f"{prefix}/input_snapshot.json",
            {
                "schema_version": "research-chgnet-input-v1",
                "candidate_id": candidate.candidate_id,
                "structure_sha256": proposed.sha256,
            },
            immutable=True,
        )
        native = build_ml_stage_plan(
            project_id=self.project_id,
            run_id=f"chgnet-{inspiration_run_id}",
            requirement_revision=1,
            attempt=1,
            orchestrator_input_snapshot=MLArtifactPointer(
                uri=snapshot_ref.uri, sha256=snapshot_ref.sha256
            ),
            requirement_artifact=MLArtifactPointer(
                uri=requirement_ref.uri, sha256=requirement_ref.sha256
            ),
            candidate_manifest_artifact=MLArtifactPointer(
                uri=proposal_ref.uri, sha256=proposal_ref.sha256
            ),
            stage_request_artifact=None,
            policy_artifact=MLArtifactPointer(
                uri=policy_ref.uri, sha256=policy_ref.sha256
            ),
            registry_artifact=MLArtifactPointer(
                uri=registry_ref.uri, sha256=registry_ref.sha256
            ),
            health_artifact=MLArtifactPointer(
                uri=health_ref.uri, sha256=health_ref.sha256
            ),
            requirement=requirement,
            candidates=[candidate],
            request=MLScreeningRequest(max_candidates=1),
            policy=policy,
            registry=registry,
            health=health,
            created_at=health.tested_at,
        )
        self.store.write_json(
            f"{prefix}/native_plan.json",
            native.model_dump(mode="json"),
            immutable=True,
        )
        return native, SubprocessWorkerClient(
            python_executable=worker_python,
            artifact_root=self.project_root,
            package_lock_path=lock_path,
            source_root=source_root,
        )

    def _reviewable_plan_ids(
        self, inspiration_run_id: str, *, limit: int
    ) -> tuple[str, ...]:
        uri = (
            f"artifact://stages/inspiration/{inspiration_run_id}/"
            "transformation_proposals.jsonl"
        )
        if not self.store.exists(uri):
            return ()
        result: list[str] = []
        for raw in self.store.read_jsonl(uri):
            plan = _strict_json(TransformationPlanV1, raw)
            checks = {check.check_id: check.status.value for check in plan.validation_checks}
            unknown = tuple(
                check_id for check_id, status in checks.items() if status == "UNKNOWN"
            )
            failed = any(status == "FAIL" for status in checks.values())
            if (
                plan.status is TransformationStatus.REQUIRES_REVIEW
                and not failed
                and unknown == ("charge_or_oxidation",)
            ):
                result.append(plan.plan_id)
            elif plan.status is TransformationStatus.STRUCTURE_VALID:
                result.append(plan.plan_id)
            if len(result) >= limit:
                break
        return tuple(result)

    def _finish(
        self,
        request: ResearchPipelineRunRequestV1,
        *,
        request_sha256: str,
        run_id: str,
        result_path: str,
        source_run_id: str | None,
        inspiration_run_id: str | None,
        selected_ids: tuple[str, ...],
        stages: list[ResearchStageRecordV1],
    ) -> ResearchPipelineResultV1:
        failed = any(stage.status is ResearchStageStatus.FAILED for stage in stages)
        blocked = any(stage.status is ResearchStageStatus.BLOCKED for stage in stages)
        status = (
            ResearchPipelineStatus.FAILED
            if failed and not selected_ids
            else ResearchPipelineStatus.PARTIAL
            if failed or blocked
            else ResearchPipelineStatus.SUCCEEDED
        )
        result = ResearchPipelineResultV1(
            run_id=run_id,
            submission_id=request.submission_id,
            request_sha256=request_sha256,
            implementation_revision=RESEARCH_PIPELINE_IMPLEMENTATION_REVISION,
            status=status,
            source_run_id=source_run_id,
            inspiration_run_id=inspiration_run_id,
            selected_candidate_ids=selected_ids,
            stages=tuple(stages),
            result_artifact_uri=f"artifact://{result_path}",
        )
        self.store.write_json(
            result_path, result.model_dump(mode="json"), immutable=True
        )
        return result

    def _append_skipped_tail(
        self, stages: list[ResearchStageRecordV1], *, from_stage: str
    ) -> None:
        ordered = (
            "semantic_rag",
            "smact_softchem",
            "chgnet",
            "deeph",
            "dft",
            "many_body",
        )
        start = ordered.index(from_stage)
        for stage in ordered[start:]:
            stages.append(self._skipped(stage, "UPSTREAM_STAGE_UNAVAILABLE"))

    @staticmethod
    def _failure_stage(stage_id: str, exc: Exception) -> ResearchStageRecordV1:
        code = getattr(exc, "code", None) or getattr(exc, "category", None)
        reason = str(code or f"{type(exc).__name__.upper()}_FAILED")
        return ResearchStageRecordV1(
            stage_id=stage_id,
            status=ResearchStageStatus.FAILED,
            reason_codes=(reason[:128],),
            summary=f"Stage failed without publishing a scientific conclusion ({type(exc).__name__}).",
        )

    @staticmethod
    def _skipped(stage_id: str, reason: str) -> ResearchStageRecordV1:
        return ResearchStageRecordV1(
            stage_id=stage_id,
            status=ResearchStageStatus.SKIPPED,
            reason_codes=(reason,),
            summary="Stage was deliberately not executed in this research entry.",
        )

    @staticmethod
    def _blocked(stage_id: str, reason: str) -> ResearchStageRecordV1:
        return ResearchStageRecordV1(
            stage_id=stage_id,
            status=ResearchStageStatus.BLOCKED,
            reason_codes=(reason,),
            summary="A required upstream scientific artifact or real binding is unavailable.",
        )

    @staticmethod
    def _control(reference) -> ArtifactPointer:
        return ArtifactPointer(uri=reference.uri, sha256=reference.sha256)

    @staticmethod
    def _v1(reference) -> ArtifactPointerV1:
        return ArtifactPointerV1.model_validate(reference.model_dump(mode="json"))


def _strict_json(model, payload):
    """Re-enter strict frozen models through their JSON wire representation."""

    return model.model_validate_json(canonical_json_bytes(payload))
