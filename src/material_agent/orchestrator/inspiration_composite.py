"""Opt-in LangGraph bridge from Agent01 output to Inspiration.

The frozen four-stage :class:`~material_agent.orchestrator.models.StageId`
contract remains unchanged.  This module exposes a separate, versioned
composite graph that consumes an explicit Agent01 candidate-manifest pointer,
verifies every referenced structure, freezes the resulting Inspiration input,
and only then calls an injected ``InspirationRunner``.
"""

from __future__ import annotations

from enum import StrEnum
from pathlib import Path
from typing import Any, Literal, Protocol, TypedDict

from langgraph.graph import END, START, StateGraph
from pydantic import Field

from material_agent.inspiration.models import (
    ArtifactPointerV1,
    ComponentSnapshotV1,
    InspirationInputV1,
    InspirationOutcome,
    ParentCandidateRefV1,
    TagGraphV1,
    canonical_json_bytes,
)
from material_agent.inspiration.policy import (
    InspirationPolicyV1,
    SearchExecutionMode,
)
from material_agent.orchestrator.models import ArtifactPointer, StrictModel
from material_agent.orchestrator.models import (
    ControlStageOutcome,
    StageId,
    StageStatus,
)
from material_agent.orchestrator.storage import OrchestratorRepository
from material_agent.retrieval.models import (
    CandidateAuditRecord,
    CandidateAuditRecordV2,
    Decision,
    Requirement,
)
from material_agent.retrieval.storage import LocalArtifactStore


INSPIRATION_COMPOSITE_VERSION = "orchestrator-composite-inspiration-v2"
_STRUCTURE_PREFIX = "artifact://candidates/structures/"


class InspirationCompositeError(RuntimeError):
    """Fail-closed bridge error with a stable, non-secret code."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


class InspirationCompositeStatus(StrEnum):
    READY = "READY"
    SUCCEEDED = "SUCCEEDED"
    SCIENTIFIC_NO_MATCH = "SCIENTIFIC_NO_MATCH"
    FAILED = "FAILED"


class InspirationCompositeRequestV2(StrictModel):
    """Frozen inputs for one Agent01-to-Inspiration composite invocation."""

    schema_version: Literal["orchestrator-composite-inspiration-v2"] = (
        INSPIRATION_COMPOSITE_VERSION
    )
    project_id: str = Field(min_length=1, max_length=64)
    request_id: str = Field(min_length=1, max_length=128)
    source_run_id: str = Field(min_length=1, max_length=64)
    inspiration_run_id: str = Field(min_length=1, max_length=64)
    requirement_revision: int = Field(ge=1)
    requirement_artifact: ArtifactPointer
    candidate_manifest_artifact: ArtifactPointer
    policy_artifact: ArtifactPointer
    tag_graph_artifact: ArtifactPointer
    transformation_registry_artifact: ArtifactPointer
    search_fixture_artifact: ArtifactPointer | None = None
    target_tag_ids: list[str] = Field(min_length=1, max_length=16)
    max_parent_candidates: int = Field(default=32, ge=1, le=32)


class InspirationCompositeLaunchV2(StrictModel):
    """Caller-owned configuration resolved against one existing Agent01 run."""

    schema_version: Literal["orchestrator-composite-inspiration-v2"] = (
        INSPIRATION_COMPOSITE_VERSION
    )
    request_id: str = Field(
        min_length=1,
        max_length=128,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$",
    )
    source_run_id: str = Field(
        min_length=1,
        max_length=64,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$",
    )
    inspiration_run_id: str = Field(
        min_length=1,
        max_length=64,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$",
    )
    requirement_revision: int = Field(ge=1)
    policy_artifact: ArtifactPointer
    tag_graph_artifact: ArtifactPointer
    transformation_registry_artifact: ArtifactPointer
    search_fixture_artifact: ArtifactPointer | None = None
    target_tag_ids: list[str] = Field(min_length=1, max_length=16)
    max_parent_candidates: int = Field(default=32, ge=1, le=32)


class InspirationCompositeResultV2(StrictModel):
    schema_version: Literal["orchestrator-composite-inspiration-v2"] = (
        INSPIRATION_COMPOSITE_VERSION
    )
    status: InspirationCompositeStatus
    parent_candidate_ids: list[str] = Field(default_factory=list)
    frozen_input_artifact: ArtifactPointer | None = None
    stage_result_artifact: ArtifactPointer | None = None
    bundle_artifact: ArtifactPointer | None = None
    selected_candidate_ids: list[str] = Field(default_factory=list)
    error_code: str | None = None
    error_message: str | None = None


class InspirationCompositeState(TypedDict, total=False):
    request: dict[str, Any]
    inspiration_input: dict[str, Any]
    result: dict[str, Any]


class _InspirationRunResult(Protocol):
    stage_result: Any
    stage_result_artifact: ArtifactPointerV1
    bundle: Any


class InspirationRunnerProtocol(Protocol):
    store: LocalArtifactStore
    search_adapter: Any
    vectorizer: ComponentSnapshotV1

    def run(
        self,
        *,
        inspiration_input: InspirationInputV1,
        policy: InspirationPolicyV1,
        tag_graph: TagGraphV1,
        target_tag_ids: tuple[str, ...],
    ) -> _InspirationRunResult: ...


class InspirationCompositeGraphV2:
    """Build the opt-in Agent01 -> Inspiration LangGraph extension."""

    def __init__(
        self,
        *,
        store: LocalArtifactStore,
        runner: InspirationRunnerProtocol,
    ) -> None:
        if runner.store.root != store.root:
            raise ValueError("Inspiration runner and composite graph must share a store")
        self.store = store
        self.runner = runner

    def build(self) -> StateGraph:
        graph = StateGraph(InspirationCompositeState)
        graph.add_node("compile_agent01_input", self.compile_agent01_input)
        graph.add_node("execute_inspiration", self.execute_inspiration)
        graph.add_edge(START, "compile_agent01_input")
        graph.add_conditional_edges(
            "compile_agent01_input",
            self.route_after_compile,
            {"execute": "execute_inspiration", "end": END},
        )
        graph.add_edge("execute_inspiration", END)
        return graph

    def compile_agent01_input(
        self, state: InspirationCompositeState
    ) -> dict[str, Any]:
        try:
            request = InspirationCompositeRequestV2.model_validate(state["request"])
            inspiration_input = self._compile_input(request)
            frozen = self.store.write_json(
                (
                    f"plans/{request.inspiration_run_id}/"
                    "inspiration-composite-v2.input.json"
                ),
                inspiration_input.model_dump(mode="json"),
                immutable=True,
            )
        except InspirationCompositeError as exc:
            return {"result": self._failure(exc.code, str(exc))}
        except Exception as exc:
            return {
                "result": self._failure(
                    "INVALID_COMPOSITE_INPUT",
                    f"composite input validation failed ({type(exc).__name__})",
                )
            }
        return {
            "inspiration_input": inspiration_input.model_dump(mode="json"),
            "result": InspirationCompositeResultV2(
                status=InspirationCompositeStatus.READY,
                parent_candidate_ids=[
                    parent.candidate_id
                    for parent in inspiration_input.parent_candidates
                ],
                frozen_input_artifact=ArtifactPointer(
                    uri=frozen.uri, sha256=frozen.sha256
                ),
            ).model_dump(mode="json"),
        }

    @staticmethod
    def route_after_compile(state: InspirationCompositeState) -> str:
        result = InspirationCompositeResultV2.model_validate(state["result"])
        return (
            "execute"
            if result.status is InspirationCompositeStatus.READY
            else "end"
        )

    def execute_inspiration(
        self, state: InspirationCompositeState
    ) -> dict[str, Any]:
        request = InspirationCompositeRequestV2.model_validate(state["request"])
        prior = InspirationCompositeResultV2.model_validate(state["result"])
        try:
            inspiration_input = InspirationInputV1.model_validate_json(
                canonical_json_bytes(state["inspiration_input"])
            )
            policy = InspirationPolicyV1.model_validate_json(
                self.store.read_bytes(request.policy_artifact.uri)
            )
            tag_graph = TagGraphV1.model_validate_json(
                self.store.read_bytes(request.tag_graph_artifact.uri)
            )
            run_result = self.runner.run(
                inspiration_input=inspiration_input,
                policy=policy,
                tag_graph=tag_graph,
                target_tag_ids=tuple(request.target_tag_ids),
            )
            self._verify_v1_pointer(run_result.stage_result_artifact)
            self._verify_v1_pointer(run_result.stage_result.bundle_artifact)
        except Exception as exc:
            code = getattr(exc, "code", "INSPIRATION_EXECUTION_FAILED")
            return {
                "result": self._failure(
                    str(code),
                    f"Inspiration execution failed ({type(exc).__name__})",
                    parent_candidate_ids=prior.parent_candidate_ids,
                    frozen_input_artifact=prior.frozen_input_artifact,
                )
            }
        outcome = InspirationOutcome(run_result.stage_result.outcome)
        status = (
            InspirationCompositeStatus.SUCCEEDED
            if outcome is InspirationOutcome.SUCCEEDED
            else InspirationCompositeStatus.SCIENTIFIC_NO_MATCH
        )
        return {
            "result": InspirationCompositeResultV2(
                status=status,
                parent_candidate_ids=prior.parent_candidate_ids,
                frozen_input_artifact=prior.frozen_input_artifact,
                stage_result_artifact=_control_pointer(
                    run_result.stage_result_artifact
                ),
                bundle_artifact=_control_pointer(
                    run_result.stage_result.bundle_artifact
                ),
                selected_candidate_ids=[
                    item.candidate_id
                    for item in run_result.bundle.selected_candidates
                ],
            ).model_dump(mode="json")
        }

    def _compile_input(
        self, request: InspirationCompositeRequestV2
    ) -> InspirationInputV1:
        expected_requirement_uri = (
            f"artifact://requirements/{request.source_run_id}/"
            f"requirement.v{request.requirement_revision}.json"
        )
        if request.requirement_artifact.uri != expected_requirement_uri:
            raise InspirationCompositeError(
                "REQUIREMENT_SOURCE_MISMATCH",
                "Requirement URI is not owned by the declared Agent01 source run",
            )
        self._verify_pointer(
            request.requirement_artifact, "REQUIREMENT_HASH_MISMATCH"
        )
        requirement = Requirement.model_validate(
            self.store.read_json(request.requirement_artifact.uri)
        )
        if (
            requirement.revision != request.requirement_revision
            or not requirement.confirmed_by_user
        ):
            raise InspirationCompositeError(
                "UNCONFIRMED_REQUIREMENT",
                "Requirement revision must match and be user-confirmed",
            )

        expected_manifest_uri = (
            f"artifact://stages/agent01/{request.source_run_id}/"
            "candidate_manifest.jsonl"
        )
        if request.candidate_manifest_artifact.uri != expected_manifest_uri:
            raise InspirationCompositeError(
                "AGENT01_MANIFEST_SOURCE_MISMATCH",
                "candidate manifest is not owned by the declared Agent01 run",
            )
        self._verify_pointer(
            request.candidate_manifest_artifact,
            "AGENT01_MANIFEST_HASH_MISMATCH",
        )
        parents = self._parents_from_manifest(request)

        self._verify_pointer(request.policy_artifact, "POLICY_HASH_MISMATCH")
        policy = InspirationPolicyV1.model_validate_json(
            self.store.read_bytes(request.policy_artifact.uri)
        )
        self._verify_pointer(request.tag_graph_artifact, "TAG_GRAPH_HASH_MISMATCH")
        TagGraphV1.model_validate_json(
            self.store.read_bytes(request.tag_graph_artifact.uri)
        )
        self._verify_pointer(
            request.transformation_registry_artifact,
            "TRANSFORMATION_REGISTRY_HASH_MISMATCH",
        )
        if request.search_fixture_artifact is not None:
            self._verify_pointer(
                request.search_fixture_artifact,
                "SEARCH_FIXTURE_HASH_MISMATCH",
            )
        if (
            policy.search_mode is SearchExecutionMode.OFFLINE_FIXTURE
            and request.search_fixture_artifact is None
        ):
            raise InspirationCompositeError(
                "SEARCH_FIXTURE_REQUIRED",
                "offline Inspiration policy requires a frozen search fixture",
            )

        return InspirationInputV1(
            project_id=request.project_id,
            request_id=request.request_id,
            run_id=request.inspiration_run_id,
            requirement_revision=request.requirement_revision,
            requirement_artifact=self._v1_pointer(
                request.requirement_artifact, "application/json"
            ),
            parent_candidates=tuple(parents),
            policy_artifact=self._v1_pointer(
                request.policy_artifact, "application/json"
            ),
            tag_graph_artifact=self._v1_pointer(
                request.tag_graph_artifact, "application/json"
            ),
            transformation_registry_artifact=self._v1_pointer(
                request.transformation_registry_artifact, "application/json"
            ),
            search_fixture_artifact=(
                self._v1_pointer(
                    request.search_fixture_artifact, "application/json"
                )
                if request.search_fixture_artifact is not None
                else None
            ),
            search_adapter=self.runner.search_adapter.component,
            vectorizer=self.runner.vectorizer,
            llm_adapter=None,
        )

    def _parents_from_manifest(
        self, request: InspirationCompositeRequestV2
    ) -> list[ParentCandidateRefV1]:
        records: list[CandidateAuditRecord | CandidateAuditRecordV2] = []
        for raw in self.store.read_jsonl(request.candidate_manifest_artifact.uri):
            version = raw.get("schema_version") if isinstance(raw, dict) else None
            model = (
                CandidateAuditRecordV2
                if version == "agent01-contract-v2"
                else CandidateAuditRecord
            )
            record = model.model_validate(raw)
            if (
                record.published_downstream
                and record.decision in {Decision.PASS, Decision.UNCERTAIN}
            ):
                records.append(record)
        records.sort(key=lambda item: (item.publication_rank or 10**9, item.candidate_id))
        records = records[: request.max_parent_candidates]
        if not records:
            raise InspirationCompositeError(
                "NO_AGENT01_PARENT_CANDIDATES",
                "Agent01 manifest contains no published PASS/UNCERTAIN structures",
            )

        parents: list[ParentCandidateRefV1] = []
        for record in records:
            if (
                record.publication_rank is None
                or record.structure_id is None
                or record.structure_artifact_uri is None
                or record.structure_artifact_sha256 is None
            ):
                raise InspirationCompositeError(
                    "INCOMPLETE_AGENT01_PARENT",
                    f"Agent01 candidate {record.candidate_id} lacks frozen structure lineage",
                )
            if not record.structure_artifact_uri.startswith(_STRUCTURE_PREFIX):
                raise InspirationCompositeError(
                    "INVALID_AGENT01_STRUCTURE_URI",
                    f"Agent01 candidate {record.candidate_id} has an invalid structure URI",
                )
            pointer = ArtifactPointer(
                uri=record.structure_artifact_uri,
                sha256=record.structure_artifact_sha256,
            )
            self._verify_pointer(pointer, "AGENT01_STRUCTURE_HASH_MISMATCH")
            parents.append(
                ParentCandidateRefV1(
                    candidate_id=record.candidate_id,
                    structure_id=record.structure_id,
                    structure_artifact=self._v1_pointer(
                        pointer, "chemical/x-cif"
                    ),
                )
            )
        return parents

    def _verify_pointer(self, pointer: ArtifactPointer, error_code: str) -> None:
        if not pointer.uri.startswith("artifact://") or not self.store.exists_with_hash(
            pointer.uri, pointer.sha256
        ):
            raise InspirationCompositeError(
                error_code, f"artifact failed URI/SHA verification: {pointer.uri}"
            )

    def _v1_pointer(
        self, pointer: ArtifactPointer, media_type: str
    ) -> ArtifactPointerV1:
        inspected = self.store.inspect(pointer.uri, media_type=media_type)
        return ArtifactPointerV1.model_validate(inspected.model_dump(mode="json"))

    def _verify_v1_pointer(self, pointer: ArtifactPointerV1) -> None:
        if not self.store.exists_with_hash(pointer.uri, pointer.sha256):
            raise InspirationCompositeError(
                "INSPIRATION_RESULT_HASH_MISMATCH",
                "Inspiration result pointer failed integrity verification",
            )

    @staticmethod
    def _failure(
        code: str,
        message: str,
        *,
        parent_candidate_ids: list[str] | None = None,
        frozen_input_artifact: ArtifactPointer | None = None,
    ) -> dict[str, Any]:
        return InspirationCompositeResultV2(
            status=InspirationCompositeStatus.FAILED,
            parent_candidate_ids=parent_candidate_ids or [],
            frozen_input_artifact=frozen_input_artifact,
            error_code=code,
            error_message=message,
        ).model_dump(mode="json")


def build_inspiration_composite_v2(
    *,
    store: LocalArtifactStore,
    runner: InspirationRunnerProtocol,
) -> StateGraph:
    """Return the opt-in v2 builder without changing the default graph."""

    return InspirationCompositeGraphV2(store=store, runner=runner).build()


class InspirationCompositeRuntimeV2:
    """Resolve and execute a composite run from an existing Agent01 workspace.

    This API is deliberately separate from :class:`OrchestratorRuntime`.  It
    neither alters the default four-stage graph nor writes a fifth stage into
    the frozen business/checkpoint schema.
    """

    def __init__(
        self,
        project_root: Path | str,
        *,
        runner: InspirationRunnerProtocol,
    ) -> None:
        self.store = LocalArtifactStore(project_root)
        if not self.store.exists("project.json"):
            raise FileNotFoundError("project.json does not exist below project root")
        project = self.store.read_json("project.json")
        project_id = project.get("project_id")
        if not isinstance(project_id, str) or not project_id:
            raise ValueError("project.json has no valid project_id")
        self.project_id = project_id
        self.repository = OrchestratorRepository(
            self.store.root / "state" / "orchestrator.sqlite3"
        )
        self.runner = runner
        self.graph = build_inspiration_composite_v2(
            store=self.store, runner=runner
        ).compile()

    def __enter__(self) -> InspirationCompositeRuntimeV2:
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()

    def close(self) -> None:
        self.repository.close()

    @classmethod
    def from_workspace(
        cls,
        workspace_root: Path | str,
        project_id: str,
        *,
        runner: InspirationRunnerProtocol,
    ) -> InspirationCompositeRuntimeV2:
        workspace = Path(workspace_root).resolve()
        project_root = (workspace / project_id).resolve()
        if workspace not in project_root.parents:
            raise ValueError("project path escapes workspace root")
        return cls(project_root, runner=runner)

    def resolve_request(
        self, launch: InspirationCompositeLaunchV2 | dict[str, Any]
    ) -> InspirationCompositeRequestV2:
        selected = InspirationCompositeLaunchV2.model_validate(launch)
        source_run = self.repository.get_run(selected.source_run_id)
        if source_run is None or source_run.get("project_id") != self.project_id:
            raise InspirationCompositeError(
                "UNKNOWN_AGENT01_SOURCE_RUN",
                "source run is absent from this project",
            )
        requirement = self.repository.get_requirement(
            selected.source_run_id, selected.requirement_revision
        )
        if requirement is None:
            raise InspirationCompositeError(
                "UNKNOWN_REQUIREMENT_REVISION",
                "source run does not own the requested Requirement revision",
            )
        requirement_pointer = ArtifactPointer(
            uri=requirement["artifact_uri"],
            sha256=requirement["artifact_sha256"],
        )
        if not self.store.exists_with_hash(
            requirement_pointer.uri, requirement_pointer.sha256
        ):
            raise InspirationCompositeError(
                "REQUIREMENT_HASH_MISMATCH",
                "source Requirement artifact failed integrity verification",
            )

        stage = self.repository.get_stage_run(selected.source_run_id, "agent01")
        if stage is None or stage.get("stage_id") != StageId.RETRIEVAL.value:
            raise InspirationCompositeError(
                "AGENT01_STAGE_NOT_FOUND",
                "source run has no recorded Agent01 retrieval stage",
            )
        if stage.get("status") not in {
            StageStatus.SUCCEEDED.value,
            StageStatus.PARTIAL.value,
        }:
            raise InspirationCompositeError(
                "AGENT01_STAGE_NOT_USABLE",
                "Agent01 stage did not publish a usable terminal result",
            )
        control_uri = stage.get("result_uri")
        control_sha256 = stage.get("result_sha256")
        if (
            not isinstance(control_uri, str)
            or not isinstance(control_sha256, str)
            or not self.store.exists_with_hash(control_uri, control_sha256)
        ):
            raise InspirationCompositeError(
                "AGENT01_CONTROL_RESULT_HASH_MISMATCH",
                "Agent01 control result failed integrity verification",
            )
        control = ControlStageOutcome.model_validate(
            self.store.read_json(control_uri)
        )
        if (
            control.stage is not StageId.RETRIEVAL
            or control.status not in {StageStatus.SUCCEEDED, StageStatus.PARTIAL}
        ):
            raise InspirationCompositeError(
                "AGENT01_CONTROL_RESULT_MISMATCH",
                "Agent01 control result identity or status is inconsistent",
            )
        manifest_payload = control.summary.get("artifacts", {}).get(
            "candidate_manifest"
        )
        if not isinstance(manifest_payload, dict):
            raise InspirationCompositeError(
                "AGENT01_MANIFEST_NOT_PUBLISHED",
                "Agent01 control result has no candidate manifest",
            )
        manifest = ArtifactPointer.model_validate(manifest_payload)
        if not self.store.exists_with_hash(manifest.uri, manifest.sha256):
            raise InspirationCompositeError(
                "AGENT01_MANIFEST_HASH_MISMATCH",
                "Agent01 candidate manifest failed integrity verification",
            )

        return InspirationCompositeRequestV2(
            project_id=self.project_id,
            request_id=selected.request_id,
            source_run_id=selected.source_run_id,
            inspiration_run_id=selected.inspiration_run_id,
            requirement_revision=selected.requirement_revision,
            requirement_artifact=requirement_pointer,
            candidate_manifest_artifact=manifest,
            policy_artifact=selected.policy_artifact,
            tag_graph_artifact=selected.tag_graph_artifact,
            transformation_registry_artifact=(
                selected.transformation_registry_artifact
            ),
            search_fixture_artifact=selected.search_fixture_artifact,
            target_tag_ids=list(selected.target_tag_ids),
            max_parent_candidates=selected.max_parent_candidates,
        )

    def execute(
        self, launch: InspirationCompositeLaunchV2 | dict[str, Any]
    ) -> InspirationCompositeResultV2:
        request = self.resolve_request(launch)
        state = self.graph.invoke({"request": request.model_dump(mode="json")})
        return InspirationCompositeResultV2.model_validate(state["result"])


def _control_pointer(pointer: ArtifactPointerV1) -> ArtifactPointer:
    return ArtifactPointer(uri=pointer.uri, sha256=pointer.sha256)
