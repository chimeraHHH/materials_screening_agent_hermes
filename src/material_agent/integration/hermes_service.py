"""Trusted Hermes factories for the bounded inspiration runner.

The offline factory preserves the fixed P0.3 replay.  The public factory adds a
deterministic request compiler and Crossref metadata I/O, but remains a narrow
local beta rather than a general request-to-science planner.  The operator fixes
the workspace and project on the MCP command line; tool inputs contain no paths,
free structures, provider endpoints, or code.
"""

from __future__ import annotations

import hashlib
import json
import os
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic import TypeAdapter

from material_agent.gateway.authorization import SqliteOneTimeActionGrantStore
from material_agent.gateway.companion import (
    CompanionAdapterError,
    InspirationRunnerResultLike,
    OfflineInspirationCompanionAdapter,
    PreparedInspirationRun,
    ProjectedInspirationResult,
)
from material_agent.gateway.mcp_server import GatewayServerSettings
from material_agent.gateway.models import (
    ArtifactClosureV1,
    ArtifactReferenceV1,
    CandidateSummaryV1,
    CompanionTransitionV1,
    CostLedgerProjectionV1,
    EvidenceReferenceV1,
    FailedStateV1,
    GatewayResultRecordV1,
    Identifier,
    InspirationBudgetV1,
    InspirationBundleSummaryV1,
    InspirationConstraintsV1,
    InspirationRunRequestV1,
    ReadableEvidenceSetV1,
    ReadableEvidenceV1,
    inspiration_report_uri,
    inspiration_request_sha256,
    artifact_closure_sha256,
)
from material_agent.gateway.persistence import SqliteGatewayRepository
from material_agent.gateway.service import MaterialsGatewayService
from material_agent.inspiration.engine import PymatgenTransformationEngine
from material_agent.inspiration.models import (
    ArtifactPointerV1,
    BridgePacketV1,
    EvidenceCardV1,
    InspirationBundleV1,
    InspirationInputV1,
    InspirationStageResultV1,
    ParentCandidateRefV1,
    PassageV1,
    SearchHitV1,
    TagGraphV1,
    TransformationPlanV1,
)
from material_agent.inspiration.parent_catalog import (
    LoadedParentCatalog,
    ParentCatalogIntegrityError,
    load_flat_band_parent_catalog_v1,
)
from material_agent.inspiration.policy import (
    BridgeSearchPolicyV1,
    EmbeddingBudgetV1,
    FetchBudgetV1,
    InspirationPolicyV1,
    PassageBudgetV1,
    RuntimeBudgetV1,
    SearchBudgetV1,
    SelectionPolicyV1,
    TransformationBudgetV1,
)
from material_agent.inspiration.runner import (
    STRUCTURE_MEDIA_TYPE,
    InspirationRunner,
    verify_artifact_pointer,
)
from material_agent.inspiration.search import (
    BoundedHttpTransport,
    CrossrefPublicAdapter,
    FixtureSearchAdapter,
    SearchAdapterError,
)
from material_agent.inspiration.tag_graph import (
    curated_flat_band_tag_graph,
    plan_tag_queries,
)
from material_agent.inspiration.transformations import (
    DEFAULT_SUBSTITUTION_REGISTRY_V1,
    substitution_registry_bytes,
)
from material_agent.inspiration.vectorizer import SIGNED_HASHING_SNAPSHOT
from material_agent.integration.request_compiler import (
    CompiledHermesInspirationRequest,
    HermesInspirationRequestCompiler,
    HermesRequestCompilationError,
)
from material_agent.retrieval.storage import LocalArtifactStore, canonical_json_bytes


HERMES_FIXTURE_GOAL = (
    "Find bounded mechanism-guided structure proposals for a layered "
    "transition-metal compound."
)
HERMES_FIXTURE_CONSTRAINTS = InspirationConstraintsV1(
    required_elements=("Se", "Ti"),
    excluded_elements=("Pb",),
    material_classes=("layered transition-metal dichalcogenide",),
    dimensionality="2D",
    target_features=("electronic flat band",),
    top_k=1,
    require_diverse_routes=True,
    budget=InspirationBudgetV1(
        max_search_requests=3,
        max_unique_documents=1,
        max_passages=3,
        max_model_calls=0,
        max_walltime_seconds=300,
    ),
)

GATEWAY_STATE_DATABASE_NAME = "materials-gateway.sqlite3"
OPERATOR_APPROVAL_DATABASE_NAME = "operator-approval-grants.sqlite3"
_TRUSTED_STATE_DATABASE_NAMES = frozenset(
    {GATEWAY_STATE_DATABASE_NAME, OPERATOR_APPROVAL_DATABASE_NAME}
)

_FIXTURE_FILE_SHA256 = {
    "openalex-acoustic-flat-band.json": (
        "eebd31e9fde6bd1601b77af9adfefe6b2880f163af81a86e1dfea2bb78d8c293"
    ),
    "parent-tis2.cif": (
        "3c62ac050a978af0a785d950a25b77e65fd529cf8cbff1bf7d627bdb429c1ca6"
    ),
    "requirement.json": (
        "309242ed5b9e8a57640d82df0cdf9029dda12606d114c17f3474f73386c508ea"
    ),
    "search-fixture-manifest.json": (
        "c7cf6de38cd31844f035cdd3e1dc97f889ed5b779901af2942b934e1da5c214d"
    ),
    "substitution-registry.json": (
        "a9b10e75d1fc56379e71683a283a1f3a45355517cbf9872dc83c94953e14770e"
    ),
}
_IDENTIFIER_ADAPTER = TypeAdapter(Identifier)
_CROSSREF_CONTACT_EMAIL_ENV = "MATERIALS_CROSSREF_CONTACT_EMAIL"
_PUBLIC_CROSSREF_MAX_RETRIES = 1
_PUBLIC_CROSSREF_TIMEOUT_SECONDS = 20
_PUBLIC_CROSSREF_MAX_RETRY_DELAY_SECONDS = 10.0
_PUBLIC_CROSSREF_MAX_TOTAL_WAIT_SECONDS = 10.0
_RETRYABLE_CROSSREF_HTTP_STATUSES = frozenset({408, 429, 500, 502, 503, 504})


class HermesFixtureConfigurationError(RuntimeError):
    """The operator-controlled fixture or workspace is invalid."""


@dataclass(frozen=True, slots=True)
class _FixtureAssets:
    requirement: bytes
    search_response: bytes
    search_manifest: bytes
    parent_structure: bytes


def _fixture_root() -> Path:
    return Path(__file__).resolve().parents[3] / "tests" / "fixtures" / "inspiration"


def _load_fixture_assets() -> _FixtureAssets:
    root = _fixture_root().resolve()
    payloads: dict[str, bytes] = {}
    for name, expected_sha256 in _FIXTURE_FILE_SHA256.items():
        path = (root / name).resolve()
        if root not in path.parents or not path.is_file():
            raise HermesFixtureConfigurationError(
                "trusted inspiration fixture is incomplete"
            )
        payload = path.read_bytes()
        if hashlib.sha256(payload).hexdigest() != expected_sha256:
            raise HermesFixtureConfigurationError(
                "trusted inspiration fixture failed its pinned SHA-256"
            )
        payloads[name] = payload

    try:
        requirement = json.loads(payloads["requirement.json"].decode("utf-8"))
        registry = json.loads(
            payloads["substitution-registry.json"].decode("utf-8")
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise HermesFixtureConfigurationError(
            "trusted inspiration fixture contains invalid JSON"
        ) from exc
    if requirement.get("request") != HERMES_FIXTURE_GOAL:
        raise HermesFixtureConfigurationError(
            "trusted requirement no longer matches the fixed Gateway goal"
        )
    if registry != DEFAULT_SUBSTITUTION_REGISTRY_V1.model_dump(mode="json"):
        raise HermesFixtureConfigurationError(
            "trusted substitution registry differs from the pinned operator registry"
        )
    return _FixtureAssets(
        requirement=payloads["requirement.json"],
        search_response=payloads["openalex-acoustic-flat-band.json"],
        search_manifest=payloads["search-fixture-manifest.json"],
        parent_structure=payloads["parent-tis2.cif"],
    )


def hermes_fixture_policy() -> InspirationPolicyV1:
    """Return the frozen policy whose costs fit the Gateway fixture budget."""

    gateway_budget = HERMES_FIXTURE_CONSTRAINTS.budget
    policy = InspirationPolicyV1(
        policy_id="inspiration-offline-fixture-v1",
        search=SearchBudgetV1(
            max_queries=3,
            max_physical_requests=gateway_budget.max_search_requests,
            max_direct_queries=1,
            max_bridge_queries=1,
            max_counter_queries=1,
            max_raw_hits=3,
            max_unique_documents=1,
        ),
        fetch=FetchBudgetV1(
            max_requests=0,
            max_total_bytes=0,
            max_bytes_per_response=0,
        ),
        passages=PassageBudgetV1(
            min_tokens=16,
            max_tokens=64,
            max_per_hit=1,
            max_total=3,
        ),
        embedding=EmbeddingBudgetV1(
            vector_dimension=32,
            max_passages=3,
            max_input_tokens=1_000,
        ),
        bridge=BridgeSearchPolicyV1(max_bridge_packets=1),
        transformation=TransformationBudgetV1(
            max_plans=2,
            max_plans_per_parent=2,
        ),
        selection=SelectionPolicyV1(
            top_k=HERMES_FIXTURE_CONSTRAINTS.top_k,
            min_mechanisms_when_available=1,
        ),
        runtime=RuntimeBudgetV1(
            max_walltime_seconds=gateway_budget.max_walltime_seconds
        ),
    )
    if (
        policy.search.max_queries > gateway_budget.max_search_requests
        or policy.search.max_unique_documents > gateway_budget.max_unique_documents
        or policy.passages.max_total > gateway_budget.max_passages
        or policy.embedding.max_passages > gateway_budget.max_passages
        or policy.llm.max_calls > gateway_budget.max_model_calls
        or policy.selection.top_k != HERMES_FIXTURE_CONSTRAINTS.top_k
        or policy.runtime.max_walltime_seconds
        > gateway_budget.max_walltime_seconds
    ):
        raise HermesFixtureConfigurationError(
            "fixture runner policy exceeds its exact Gateway budget"
        )
    return policy


def _pointer(reference: Any) -> ArtifactPointerV1:
    return ArtifactPointerV1.model_validate(reference.model_dump(mode="python"))


class HermesFixturePreparer:
    """Seed only pinned inputs beneath the configured project artifact root."""

    def __init__(
        self,
        *,
        store: LocalArtifactStore,
        project_id: str,
        assets: _FixtureAssets,
        policy: InspirationPolicyV1,
        tag_graph: TagGraphV1,
        search_adapter: FixtureSearchAdapter,
    ) -> None:
        self.store = store
        self.project_id = project_id
        self.assets = assets
        self.policy = policy
        self.tag_graph = tag_graph
        self.search_adapter = search_adapter

    @staticmethod
    def supports(request: InspirationRunRequestV1) -> bool:
        candidate_elements = {"Se", "Ti"}
        if not set(request.constraints.required_elements).issubset(
            candidate_elements
        ):
            return False
        if set(request.constraints.excluded_elements) & candidate_elements:
            return False
        return (
            request.goal == HERMES_FIXTURE_GOAL
            and request.constraints == HERMES_FIXTURE_CONSTRAINTS
        )

    def prepare(
        self,
        *,
        run_id: str,
        request: InspirationRunRequestV1,
    ) -> PreparedInspirationRun:
        if not self.supports(request):
            raise CompanionAdapterError(
                "request does not match the trusted inspiration fixture"
            )
        fixture_requirement_pointer = _pointer(
            self.store.write_bytes(
                "inputs/fixture_requirement.json",
                self.assets.requirement,
                media_type="application/json",
                immutable=True,
            )
        )
        requirement_pointer = _pointer(
            self.store.write_json(
                f"inputs/requirements/{run_id}.json",
                {
                    "fixture_scope_artifact": fixture_requirement_pointer,
                    "gateway_request": request.model_dump(mode="json"),
                    "gateway_request_sha256": inspiration_request_sha256(request),
                    "requirement_revision": 1,
                    "schema_version": "materials-hermes-fixture-requirement-v1",
                },
                immutable=True,
            )
        )
        policy_pointer = _pointer(
            self.store.write_json(
                "inputs/policy.json",
                self.policy.model_dump(mode="json"),
                immutable=True,
            )
        )
        graph_pointer = _pointer(
            self.store.write_json(
                "inputs/tag_graph.json",
                self.tag_graph.model_dump(mode="json"),
                immutable=True,
            )
        )
        registry_pointer = _pointer(
            self.store.write_bytes(
                "inputs/substitution_registry.json",
                substitution_registry_bytes(DEFAULT_SUBSTITUTION_REGISTRY_V1),
                media_type="application/json",
                immutable=True,
            )
        )
        _pointer(
            self.store.write_bytes(
                "inputs/openalex_fixture.json",
                self.assets.search_response,
                media_type="application/json",
                immutable=True,
            )
        )
        search_manifest_pointer = _pointer(
            self.store.write_bytes(
                "inputs/search_fixture_manifest.json",
                self.assets.search_manifest,
                media_type="application/json",
                immutable=True,
            )
        )
        parent_pointer = _pointer(
            self.store.write_bytes(
                "inputs/parent-tis2.cif",
                self.assets.parent_structure,
                media_type=STRUCTURE_MEDIA_TYPE,
                immutable=True,
            )
        )
        inspiration_input = InspirationInputV1(
            project_id=self.project_id,
            request_id=request.submission_id,
            run_id=run_id,
            requirement_revision=1,
            requirement_artifact=requirement_pointer,
            parent_candidates=(
                ParentCandidateRefV1(
                    candidate_id="parent-candidate-tis2",
                    structure_id="parent-structure-tis2",
                    structure_artifact=parent_pointer,
                ),
            ),
            policy_artifact=policy_pointer,
            tag_graph_artifact=graph_pointer,
            transformation_registry_artifact=registry_pointer,
            search_fixture_artifact=search_manifest_pointer,
            search_adapter=self.search_adapter.component,
            vectorizer=SIGNED_HASHING_SNAPSHOT,
        )
        return PreparedInspirationRun(
            inspiration_input=inspiration_input,
            policy=self.policy,
            tag_graph=self.tag_graph,
            target_tag_ids=("electronic-flat-band",),
        )


class HermesInspirationPreparer:
    """Compile a request and seed only operator-owned, SHA-pinned inputs."""

    def __init__(
        self,
        *,
        store: LocalArtifactStore,
        project_id: str,
        parent_catalog: LoadedParentCatalog,
        tag_graph: TagGraphV1,
        search_adapter: CrossrefPublicAdapter,
        compiler: HermesInspirationRequestCompiler,
    ) -> None:
        if search_adapter.max_retries != compiler.max_retries_per_query:
            raise HermesFixtureConfigurationError(
                "request compiler and Crossref retry budgets differ"
            )
        self.store = store
        self.project_id = project_id
        self.parent_catalog = parent_catalog
        self.tag_graph = tag_graph
        self.search_adapter = search_adapter
        self.compiler = compiler

    def compile(
        self,
        request: InspirationRunRequestV1,
    ) -> CompiledHermesInspirationRequest:
        return self.compiler.compile(request)

    def prepare(
        self,
        *,
        run_id: str,
        request: InspirationRunRequestV1,
    ) -> PreparedInspirationRun:
        compiled = self.compile(request)
        catalog = self.parent_catalog
        if compiled.parent_catalog_id != catalog.manifest.catalog_id:
            raise HermesFixtureConfigurationError(
                "compiled parent catalog differs from the loaded catalog"
            )
        catalog_prefix = f"inputs/catalogs/{catalog.manifest.catalog_id}"
        catalog_pointer = _pointer(
            self.store.write_bytes(
                f"{catalog_prefix}/manifest.json",
                catalog.manifest_bytes,
                media_type="application/json",
                immutable=True,
            )
        )
        parent_candidates: list[ParentCandidateRefV1] = []
        catalog_entry_scope: list[dict[str, object]] = []
        for loaded_entry in catalog.entries:
            entry = loaded_entry.record
            parent_pointer = _pointer(
                self.store.write_bytes(
                    f"{catalog_prefix}/{entry.artifact.asset_name}",
                    loaded_entry.artifact_bytes,
                    media_type=STRUCTURE_MEDIA_TYPE,
                    immutable=True,
                )
            )
            parent_candidates.append(
                ParentCandidateRefV1(
                    candidate_id=entry.candidate_id,
                    structure_id=entry.structure_id,
                    structure_artifact=parent_pointer,
                )
            )
            catalog_entry_scope.append(
                {
                    "candidate_id": entry.candidate_id,
                    "entry_id": entry.entry_id,
                    "family_id": entry.family_id,
                    "parent_structure_artifact": parent_pointer,
                    "reviewed_bridge_rule_id": entry.reviewed_bridge_rule_id,
                    "route_sha256": entry.route_sha256,
                    "structure_id": entry.structure_id,
                }
            )
        requirement_pointer = _pointer(
            self.store.write_json(
                f"inputs/requirements/{run_id}.json",
                {
                    "compiled_scope": {
                        "catalog_entries": catalog_entry_scope,
                        "diversity_mode": compiled.diversity_mode.value,
                        "expected_output_elements": (
                            compiled.expected_output_elements
                        ),
                        "goal_role": "approval-bound-user-rationale-not-parsed",
                        "goal_sha256": compiled.goal_sha256,
                        "normalized_material_classes": (
                            compiled.normalized_material_classes
                        ),
                        "normalized_target_features": (
                            compiled.normalized_target_features
                        ),
                        "parent_catalog_artifact": catalog_pointer,
                        "parent_catalog_id": compiled.parent_catalog_id,
                        "parent_catalog_sha256": catalog.manifest_sha256,
                        "parent_catalog_version": catalog.manifest.catalog_version,
                        "physical_search_attempt_limit": (
                            compiled.physical_search_attempt_limit
                        ),
                        "target_tag_ids": compiled.target_tag_ids,
                    },
                    "gateway_request": request.model_dump(mode="json"),
                    "gateway_request_sha256": inspiration_request_sha256(request),
                    "requirement_revision": 1,
                    "schema_version": "materials-hermes-inspiration-requirement-v1",
                },
                immutable=True,
            )
        )
        policy_pointer = _pointer(
            self.store.write_json(
                f"inputs/policies/{run_id}.json",
                compiled.policy.model_dump(mode="json"),
                immutable=True,
            )
        )
        graph_pointer = _pointer(
            self.store.write_json(
                "inputs/tag_graph.json",
                self.tag_graph.model_dump(mode="json"),
                immutable=True,
            )
        )
        registry_pointer = _pointer(
            self.store.write_bytes(
                "inputs/substitution_registry.json",
                substitution_registry_bytes(DEFAULT_SUBSTITUTION_REGISTRY_V1),
                media_type="application/json",
                immutable=True,
            )
        )
        inspiration_input = InspirationInputV1(
            project_id=self.project_id,
            request_id=request.submission_id,
            run_id=run_id,
            requirement_revision=1,
            requirement_artifact=requirement_pointer,
            parent_candidates=tuple(parent_candidates),
            policy_artifact=policy_pointer,
            tag_graph_artifact=graph_pointer,
            transformation_registry_artifact=registry_pointer,
            search_fixture_artifact=None,
            search_adapter=self.search_adapter.component,
            vectorizer=SIGNED_HASHING_SNAPSHOT,
        )
        return PreparedInspirationRun(
            inspiration_input=inspiration_input,
            policy=compiled.policy,
            tag_graph=self.tag_graph,
            target_tag_ids=compiled.target_tag_ids,
        )


def _gateway_artifact_reference(pointer: ArtifactPointerV1) -> ArtifactReferenceV1:
    if not isinstance(pointer, ArtifactPointerV1):
        raise CompanionAdapterError("artifact closure contains an invalid pointer")
    return ArtifactReferenceV1(
        uri=pointer.uri,
        sha256=pointer.sha256,
        size_bytes=pointer.size_bytes,
        media_type=pointer.media_type,
    )


@dataclass(frozen=True, slots=True)
class _RecoveredInspirationRunnerResult:
    """Minimal structural runner result reconstructed from authoritative bytes."""

    stage_result: InspirationStageResultV1
    stage_result_artifact: ArtifactPointerV1
    bundle: InspirationBundleV1


class HermesFixtureProjector:
    """Re-read authoritative runner artifacts and build the bounded Gateway DTO."""

    def __init__(self, store: LocalArtifactStore) -> None:
        self.store = store

    def recover_or_bind_completed(
        self,
        *,
        run_id: str,
        request: InspirationRunRequestV1,
        prepared: PreparedInspirationRun,
        execution_manifest_sha256: str,
    ) -> InspirationRunnerResultLike | None:
        """Recover one exact completed run, or bind a pristine execution once.

        The intent record lives outside the scientific stage closure and is
        written with exclusive-create semantics before any runner I/O.  It
        prevents a completed stage produced by an older or different manifest
        from being silently promoted after a Gateway commit crash.
        """

        expected_binding = {
            "schema_version": "materials-inspiration-recovery-binding-v1",
            "run_id": run_id,
            "project_id": prepared.inspiration_input.project_id,
            "request_id": prepared.inspiration_input.request_id,
            "gateway_request_sha256": inspiration_request_sha256(request),
            "execution_manifest_sha256": execution_manifest_sha256,
            "stage_result_uri": (
                f"artifact://stages/inspiration/{run_id}/stage_result.json"
            ),
        }
        binding_path = self._recovery_binding_path(run_id)
        stage_root = self._stage_root(run_id)

        if binding_path.exists():
            self._verify_recovery_binding(binding_path, expected_binding)
            return self._recover_bound_completed(
                run_id=run_id,
                prepared=prepared,
                expected_stage_result_uri=expected_binding["stage_result_uri"],
            )
        if binding_path.is_symlink():
            raise CompanionAdapterError("completed-run recovery binding is unsafe")
        if self._stage_has_any_state(stage_root):
            raise CompanionAdapterError(
                "unbound or partial inspiration stage cannot be recovered"
            )

        binding_path.parent.mkdir(parents=True, exist_ok=True)
        if binding_path.parent.is_symlink():
            raise CompanionAdapterError("completed-run recovery directory is unsafe")
        payload = canonical_json_bytes(expected_binding)
        try:
            descriptor = os.open(
                binding_path,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                0o600,
            )
        except FileExistsError:
            self._verify_recovery_binding(binding_path, expected_binding)
            return self._recover_bound_completed(
                run_id=run_id,
                prepared=prepared,
                expected_stage_result_uri=expected_binding["stage_result_uri"],
            )
        except OSError as exc:
            raise CompanionAdapterError(
                "completed-run recovery binding could not be persisted"
            ) from exc
        try:
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
        except OSError as exc:
            raise CompanionAdapterError(
                "completed-run recovery binding could not be persisted"
            ) from exc
        try:
            directory_descriptor = os.open(binding_path.parent, os.O_RDONLY)
            try:
                os.fsync(directory_descriptor)
            finally:
                os.close(directory_descriptor)
        except OSError as exc:
            raise CompanionAdapterError(
                "completed-run recovery binding was not durably persisted"
            ) from exc
        return None

    def _recover_bound_completed(
        self,
        *,
        run_id: str,
        prepared: PreparedInspirationRun,
        expected_stage_result_uri: str,
    ) -> InspirationRunnerResultLike:
        if not self.store.exists(expected_stage_result_uri):
            raise CompanionAdapterError(
                "bound inspiration execution is incomplete and cannot be retried"
            )
        try:
            stage_payload = self.store.read_bytes(expected_stage_result_uri)
            stage = InspirationStageResultV1.model_validate_json(stage_payload)
            if stage_payload != canonical_json_bytes(stage.model_dump(mode="json")):
                raise ValueError("stage result is not canonical JSON")
            inspected = self.store.inspect(
                expected_stage_result_uri,
                media_type="application/json",
            )
            stage_pointer = ArtifactPointerV1.model_validate(
                {
                    "uri": inspected.uri,
                    "sha256": inspected.sha256,
                    "size_bytes": inspected.size_bytes,
                    "media_type": inspected.media_type,
                }
            )
            bundle = InspirationBundleV1.model_validate_json(
                self.store.read_bytes(stage.bundle_artifact.uri)
            )
        except (FileNotFoundError, OSError, RuntimeError, TypeError, ValueError) as exc:
            raise CompanionAdapterError(
                "bound inspiration stage result is invalid or incomplete"
            ) from exc

        recovered = _RecoveredInspirationRunnerResult(
            stage_result=stage,
            stage_result_artifact=stage_pointer,
            bundle=bundle,
        )
        verified_stage = self._verified_stage_result(run_id, recovered)
        verified_bundle = self._verified_bundle(verified_stage, bundle)
        inspiration_input = self._read_strict_artifact(
            verified_stage.input_snapshot_artifact,
            InspirationInputV1,
            label="input snapshot",
        )
        effective_policy = self._read_strict_artifact(
            verified_stage.policy_artifact,
            InspirationPolicyV1,
            label="effective policy",
        )
        if inspiration_input != prepared.inspiration_input:
            raise CompanionAdapterError(
                "completed runner input differs from the approved manifest"
            )
        if effective_policy != prepared.policy:
            raise CompanionAdapterError(
                "completed runner policy differs from the approved manifest"
            )
        if (
            verified_stage.project_id != prepared.inspiration_input.project_id
            or verified_stage.request_id != prepared.inspiration_input.request_id
            or verified_stage.run_id != run_id
        ):
            raise CompanionAdapterError(
                "completed runner result has a different run/request/project identity"
            )
        if (
            verified_bundle.run_id != run_id
            or verified_bundle.request_id != prepared.inspiration_input.request_id
            or verified_bundle.outcome != verified_stage.outcome
        ):
            raise CompanionAdapterError(
                "completed bundle differs from its bound stage identity"
            )
        if verified_bundle.lineage_artifacts != verified_stage.intermediate_artifacts:
            raise CompanionAdapterError(
                "completed bundle lineage differs from the stage closure"
            )
        if verified_stage.report_artifact.uri != inspiration_report_uri(run_id):
            raise CompanionAdapterError("completed report URI is not run-bound")
        try:
            self.store.read_bytes(verified_stage.report_artifact.uri).decode("utf-8")
        except (FileNotFoundError, OSError, UnicodeDecodeError) as exc:
            raise CompanionAdapterError(
                "completed report is unavailable or not UTF-8"
            ) from exc
        self._verify_stage_directory_closure(run_id, verified_stage, stage_pointer)
        return recovered

    def _read_strict_artifact(
        self,
        pointer: ArtifactPointerV1,
        model: type[Any],
        *,
        label: str,
    ) -> Any:
        self._verify_pointer(pointer)
        try:
            return model.model_validate_json(self.store.read_bytes(pointer.uri))
        except (FileNotFoundError, OSError, TypeError, ValueError) as exc:
            raise CompanionAdapterError(
                f"completed runner {label} is not a valid strict DTO"
            ) from exc

    def _verify_stage_directory_closure(
        self,
        run_id: str,
        stage: InspirationStageResultV1,
        stage_pointer: ArtifactPointerV1,
    ) -> None:
        stage_root = self._stage_root(run_id)
        stage_relative = Path("stages", "inspiration", run_id)
        declared_pointers = (
            stage_pointer,
            stage.input_snapshot_artifact,
            stage.policy_artifact,
            stage.bundle_artifact,
            stage.report_artifact,
            stage.cost_ledger_artifact,
            *stage.intermediate_artifacts,
        )
        declared_files: set[Path] = set()
        declared_directories = {stage_relative}
        for pointer in declared_pointers:
            relative = Path(pointer.uri.removeprefix("artifact://"))
            if (
                not pointer.uri.startswith("artifact://")
                or relative == stage_relative
                or stage_relative not in (relative, *relative.parents)
            ):
                raise CompanionAdapterError(
                    "completed stage closure contains an out-of-run artifact"
                )
            declared_files.add(relative)
            parent = relative.parent
            while parent != stage_relative:
                declared_directories.add(parent)
                parent = parent.parent

        actual_files: set[Path] = set()
        actual_directories = {stage_relative}
        try:
            for path in stage_root.rglob("*"):
                if path.is_symlink():
                    raise CompanionAdapterError(
                        "completed stage closure contains a symbolic link"
                    )
                relative = path.relative_to(self.store.root)
                if path.is_file():
                    actual_files.add(relative)
                elif path.is_dir():
                    actual_directories.add(relative)
                else:
                    raise CompanionAdapterError(
                        "completed stage closure contains a non-regular entry"
                    )
        except OSError as exc:
            raise CompanionAdapterError(
                "completed stage closure could not be enumerated"
            ) from exc
        if actual_files != declared_files or actual_directories != declared_directories:
            raise CompanionAdapterError(
                "completed stage directory differs from its declared closure"
            )

    def _verify_recovery_binding(
        self,
        binding_path: Path,
        expected_binding: dict[str, str],
    ) -> None:
        if binding_path.is_symlink() or not binding_path.is_file():
            raise CompanionAdapterError("completed-run recovery binding is unsafe")
        try:
            observed = json.loads(binding_path.read_bytes())
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise CompanionAdapterError(
                "completed-run recovery binding is invalid"
            ) from exc
        if observed != expected_binding:
            raise CompanionAdapterError(
                "completed-run recovery binding differs from the approved manifest"
            )

    def _recovery_binding_path(self, run_id: str) -> Path:
        root = self.store.root
        path = root / ".gateway" / "inspiration-recovery" / f"{run_id}.json"
        if path.parent.parent.is_symlink() or path.parent.is_symlink():
            raise CompanionAdapterError("completed-run recovery path is unsafe")
        return path

    def _stage_root(self, run_id: str) -> Path:
        path = self.store.root / "stages" / "inspiration" / run_id
        if path.is_symlink():
            raise CompanionAdapterError("inspiration stage path is unsafe")
        return path

    @staticmethod
    def _stage_has_any_state(stage_root: Path) -> bool:
        if not stage_root.exists():
            return False
        if stage_root.is_symlink() or not stage_root.is_dir():
            raise CompanionAdapterError("inspiration stage path is unsafe")
        try:
            next(stage_root.iterdir())
        except StopIteration:
            return True
        except OSError as exc:
            raise CompanionAdapterError("inspiration stage state is unreadable") from exc
        return True

    def project(
        self,
        *,
        run_id: str,
        request: InspirationRunRequestV1,
        prepared: PreparedInspirationRun,
        runner_result: InspirationRunnerResultLike,
    ) -> ProjectedInspirationResult:
        del request
        stage = self._verified_stage_result(run_id, runner_result)
        bundle = self._verified_bundle(stage, runner_result.bundle)
        self._verify_pointer(stage.report_artifact)
        report_bytes = self.store.read_bytes(stage.report_artifact.uri)
        try:
            report_bytes.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise CompanionAdapterError(
                "authoritative inspiration report is not UTF-8"
            ) from exc

        passages = self._read_jsonl(
            stage,
            run_id=run_id,
            filename="passages.jsonl",
            model=PassageV1,
        )
        search_hits = self._read_jsonl(
            stage,
            run_id=run_id,
            filename="search_hits.jsonl",
            model=SearchHitV1,
        )
        evidence_cards = self._read_jsonl(
            stage,
            run_id=run_id,
            filename="evidence_cards.jsonl",
            model=EvidenceCardV1,
        )
        bridges = self._read_jsonl(
            stage,
            run_id=run_id,
            filename="bridge_packets.jsonl",
            model=BridgePacketV1,
        )
        plans = self._read_jsonl(
            stage,
            run_id=run_id,
            filename="transformation_proposals.jsonl",
            model=TransformationPlanV1,
        )
        summaries, lineage = self._project_candidates(
            bundle=bundle,
            prepared=prepared,
            passages=passages,
            evidence_cards=evidence_cards,
            bridges=bridges,
            plans=plans,
        )
        readable_evidence = self._project_readable_evidence(
            lineage=lineage,
            passages=passages,
            evidence_cards=evidence_cards,
            search_hits=search_hits,
        )
        fetched_document_count = self._count_fetched_documents(
            stage,
            run_id=run_id,
        )
        ledger = bundle.cost_ledger
        closure_artifacts = tuple(
            sorted(
                (
                    _gateway_artifact_reference(pointer)
                    for pointer in (
                        stage.input_snapshot_artifact,
                        stage.policy_artifact,
                        *stage.intermediate_artifacts,
                        stage.cost_ledger_artifact,
                        stage.report_artifact,
                        stage.bundle_artifact,
                    )
                ),
                key=lambda item: item.uri,
            )
        )
        stage_result_reference = _gateway_artifact_reference(
            runner_result.stage_result_artifact
        )
        closure = ArtifactClosureV1(
            stage_result=stage_result_reference,
            artifacts=closure_artifacts,
            closure_sha256=artifact_closure_sha256(
                stage_result=stage_result_reference,
                artifacts=closure_artifacts,
            ),
        )
        result = GatewayResultRecordV1(
            run_id=run_id,
            report_uri=inspiration_report_uri(run_id),
            authoritative_sha256=stage.report_artifact.sha256,
            bundle=InspirationBundleSummaryV1(
                outcome=bundle.outcome.value,
                selected_candidates=summaries,
                limitations=bundle.limitations,
                next_validation_steps=bundle.next_validation_steps,
            ),
            evidence_lineage=lineage,
            readable_evidence=readable_evidence,
            validation_boundaries=(
                "SEARCH_SUPPORTED records bounded source support, not property validation.",
                "STRUCTURE_VALID records deterministic structural QC only.",
                "The target property remains UNKNOWN until downstream calculation.",
            ),
            cost_ledger=CostLedgerProjectionV1(
                search_requests=ledger.search_requests,
                search_response_bytes=ledger.search_response_bytes,
                fetched_documents=fetched_document_count,
                extracted_passages=ledger.extracted_passages,
                vectorized_passages=ledger.vectorized_passages,
                model_calls=ledger.llm_calls,
                input_tokens=ledger.llm_input_tokens,
                output_tokens=ledger.llm_output_tokens,
                walltime_ms=ledger.walltime_ms,
            ),
            artifact_closure=closure,
        )
        warnings = self._bounded_warnings(stage.warnings)
        return ProjectedInspirationResult(
            result=result,
            status="PARTIAL" if warnings else "SUCCEEDED",
            warnings=warnings,
        )

    def _verified_stage_result(
        self,
        run_id: str,
        runner_result: InspirationRunnerResultLike,
    ) -> InspirationStageResultV1:
        pointer = runner_result.stage_result_artifact
        expected_uri = f"artifact://stages/inspiration/{run_id}/stage_result.json"
        if pointer.uri != expected_uri:
            raise CompanionAdapterError("stage-result artifact URI drifted")
        self._verify_pointer(pointer)
        try:
            stored = InspirationStageResultV1.model_validate_json(
                self.store.read_bytes(pointer.uri)
            )
        except (TypeError, ValueError) as exc:
            raise CompanionAdapterError(
                "stage-result artifact is not a valid strict DTO"
            ) from exc
        if stored != runner_result.stage_result:
            raise CompanionAdapterError(
                "stage-result artifact differs from the runner result"
            )
        for artifact in (
            stored.input_snapshot_artifact,
            stored.policy_artifact,
            stored.bundle_artifact,
            stored.report_artifact,
            stored.cost_ledger_artifact,
            *stored.intermediate_artifacts,
        ):
            self._verify_pointer(artifact)
        return stored

    def _verified_bundle(
        self,
        stage: InspirationStageResultV1,
        runner_bundle: InspirationBundleV1,
    ) -> InspirationBundleV1:
        try:
            stored = InspirationBundleV1.model_validate_json(
                self.store.read_bytes(stage.bundle_artifact.uri)
            )
        except (TypeError, ValueError) as exc:
            raise CompanionAdapterError(
                "bundle artifact is not a valid strict DTO"
            ) from exc
        if stored != runner_bundle:
            raise CompanionAdapterError("bundle artifact differs from runner result")
        return stored

    def _read_jsonl(
        self,
        stage: InspirationStageResultV1,
        *,
        run_id: str,
        filename: str,
        model: type[Any],
    ) -> tuple[Any, ...]:
        expected_uri = f"artifact://stages/inspiration/{run_id}/{filename}"
        matches = tuple(
            pointer
            for pointer in stage.intermediate_artifacts
            if pointer.uri == expected_uri
        )
        if len(matches) != 1:
            raise CompanionAdapterError(
                f"stage result does not identify exactly one {filename} artifact"
            )
        self._verify_pointer(matches[0])
        try:
            lines = (
                line
                for line in self.store.read_bytes(matches[0].uri).splitlines()
                if line.strip()
            )
            return tuple(model.model_validate_json(line) for line in lines)
        except (TypeError, ValueError, OSError, json.JSONDecodeError) as exc:
            raise CompanionAdapterError(
                f"authoritative {filename} artifact is invalid"
            ) from exc

    def _count_fetched_documents(
        self,
        stage: InspirationStageResultV1,
        *,
        run_id: str,
    ) -> int:
        """Count successful documents without confusing them with HTTP attempts."""

        expected_uri = (
            f"artifact://stages/inspiration/{run_id}/fetch_manifest.jsonl"
        )
        matches = tuple(
            pointer
            for pointer in stage.intermediate_artifacts
            if pointer.uri == expected_uri
        )
        if len(matches) != 1:
            raise CompanionAdapterError(
                "stage result does not identify exactly one fetch manifest"
            )
        self._verify_pointer(matches[0])
        fetched_document_ids: set[str] = set()
        seen_document_ids: set[str] = set()
        try:
            lines = (
                line
                for line in self.store.read_bytes(matches[0].uri).splitlines()
                if line.strip()
            )
            for line in lines:
                record = json.loads(line)
                if not isinstance(record, dict):
                    raise ValueError("fetch manifest row must be an object")
                if record.get("schema_version") != "inspiration-fetch-manifest-v1":
                    raise ValueError("unsupported fetch manifest schema")
                document_id = record.get("document_id")
                fetched = record.get("fetched")
                body_value = record.get("fetched_body_artifact")
                if not isinstance(document_id, str) or type(fetched) is not bool:
                    raise ValueError("fetch manifest identity fields are invalid")
                if document_id in seen_document_ids:
                    raise ValueError("fetch manifest repeats a canonical document")
                seen_document_ids.add(document_id)
                if not fetched:
                    if body_value is not None:
                        raise ValueError("unfetched document names a body Artifact")
                    continue
                body_pointer = ArtifactPointerV1.model_validate(body_value)
                if body_pointer not in stage.intermediate_artifacts:
                    raise ValueError("fetched body is absent from stage lineage")
                self._verify_pointer(body_pointer)
                fetched_document_ids.add(document_id)
        except (TypeError, ValueError, OSError, json.JSONDecodeError) as exc:
            raise CompanionAdapterError(
                "authoritative fetch_manifest.jsonl artifact is invalid"
            ) from exc
        return len(fetched_document_ids)

    def _verify_pointer(self, pointer: ArtifactPointerV1) -> None:
        try:
            verify_artifact_pointer(self.store, pointer)
        except Exception as exc:
            raise CompanionAdapterError(
                "authoritative inspiration artifact failed URI/SHA/size verification"
            ) from exc

    @staticmethod
    def _project_candidates(
        *,
        bundle: InspirationBundleV1,
        prepared: PreparedInspirationRun,
        passages: tuple[PassageV1, ...],
        evidence_cards: tuple[EvidenceCardV1, ...],
        bridges: tuple[BridgePacketV1, ...],
        plans: tuple[TransformationPlanV1, ...],
    ) -> tuple[tuple[CandidateSummaryV1, ...], tuple[EvidenceReferenceV1, ...]]:
        passage_index = {item.passage_id: item for item in passages}
        evidence_index = {item.evidence_card_id: item for item in evidence_cards}
        bridge_index = {item.bridge_packet_id: item for item in bridges}
        plan_index = {item.plan_id: item for item in plans}
        tag_index = {item.tag_id: item for item in prepared.tag_graph.tags}
        summaries: list[CandidateSummaryV1] = []
        used_passages: set[str] = set()

        for candidate in bundle.selected_candidates:
            try:
                plan = plan_index[candidate.representative_plan_id]
                cards = tuple(
                    evidence_index[card_id]
                    for card_id in candidate.evidence_card_ids
                )
                candidate_passage_ids = tuple(
                    sorted(
                        {
                            passage_id
                            for card in cards
                            for passage_id in card.passage_ids
                        }
                    )
                )
                candidate_passages = tuple(
                    passage_index[passage_id]
                    for passage_id in candidate_passage_ids
                )
                candidate_bridge_ids = tuple(
                    sorted(
                        {
                            bridge_id
                            for route in candidate.merged_routes
                            for bridge_id in route.bridge_packet_ids
                        }
                    )
                )
                candidate_bridges = tuple(
                    bridge_index[bridge_id] for bridge_id in candidate_bridge_ids
                )
            except KeyError as exc:
                raise CompanionAdapterError(
                    "candidate projection has an orphaned lineage reference"
                ) from exc
            if not candidate_bridges:
                raise CompanionAdapterError(
                    "candidate projection must resolve to at least one bridge"
                )
            try:
                bridge_domains = tuple(
                    sorted(
                        {
                            tag_index[tag_id].label
                            for bridge in candidate_bridges
                            for tag_id in bridge.source_domain_tag_ids
                        }
                    )
                )
            except KeyError as exc:
                raise CompanionAdapterError(
                    "bridge projection references an unknown tag"
                ) from exc
            if not bridge_domains:
                raise CompanionAdapterError("bridge projection has no source domain")
            shared_invariants = tuple(
                dict.fromkeys(bridge.shared_invariant for bridge in candidate_bridges)
            )
            failure_conditions = tuple(
                sorted(
                    {
                        condition
                        for bridge in candidate_bridges
                        for condition in bridge.breaking_conditions
                    }
                )
            )
            used_passages.update(candidate_passage_ids)
            parameters = plan.parameters
            sites = ", ".join(str(index) for index in parameters.equivalent_site_indices)
            summaries.append(
                CandidateSummaryV1(
                    candidate_id=candidate.candidate_id,
                    parent_candidate_id=plan.parent_candidate_id,
                    deterministic_transformation=(
                        f"{plan.operator_id} replaces {parameters.source_species} "
                        f"with {parameters.target_species} at the complete "
                        f"equivalence class [{sites}]. The selected structure "
                        f"retains {len(candidate.merged_routes)} hash-distinct "
                        "physical transformation route(s)."
                    ),
                    shared_invariant=" | ".join(shared_invariants),
                    bridge_domain=", ".join(bridge_domains),
                    evidence_document_ids=tuple(
                        sorted({item.document_id for item in candidate_passages})
                    ),
                    evidence_passage_ids=candidate_passage_ids,
                    failure_conditions=failure_conditions,
                    cheapest_falsification_step=candidate.next_falsification_step,
                )
            )

        passages_by_document: dict[str, list[str]] = defaultdict(list)
        for passage_id in sorted(used_passages):
            passage = passage_index[passage_id]
            passages_by_document[passage.document_id].append(passage_id)
        lineage = tuple(
            EvidenceReferenceV1(
                document_id=document_id,
                passage_ids=tuple(passage_ids),
            )
            for document_id, passage_ids in sorted(passages_by_document.items())
        )
        return tuple(summaries), lineage

    @staticmethod
    def _project_readable_evidence(
        *,
        lineage: tuple[EvidenceReferenceV1, ...],
        passages: tuple[PassageV1, ...],
        evidence_cards: tuple[EvidenceCardV1, ...],
        search_hits: tuple[SearchHitV1, ...],
    ) -> ReadableEvidenceSetV1:
        """Project selected lineage to exact, bounded, human-readable excerpts."""

        passage_index = {item.passage_id: item for item in passages}
        hit_index = {item.hit_id: item for item in search_hits}
        cards_by_passage: dict[str, list[EvidenceCardV1]] = defaultdict(list)
        for card in evidence_cards:
            for passage_id in card.passage_ids:
                cards_by_passage[passage_id].append(card)
        selected_passage_ids = tuple(
            sorted(
                {
                    passage_id
                    for reference in lineage
                    for passage_id in reference.passage_ids
                }
            )
        )
        items: list[ReadableEvidenceV1] = []
        for passage_id in selected_passage_ids[:32]:
            try:
                passage = passage_index[passage_id]
                hit = hit_index[passage.hit_id]
                cards = cards_by_passage[passage_id]
            except KeyError as exc:
                raise CompanionAdapterError(
                    "readable evidence has an orphaned source reference"
                ) from exc
            if not cards:
                raise CompanionAdapterError(
                    "readable evidence passage has no evidence card"
                )
            excerpt = passage.text[:1_000]
            items.append(
                ReadableEvidenceV1(
                    document_id=passage.document_id,
                    passage_id=passage.passage_id,
                    source_title=hit.title,
                    published_year=hit.published_year,
                    doi=hit.doi,
                    canonical_url=hit.canonical_url,
                    relations=tuple(
                        sorted({card.relation.value for card in cards})
                    ),
                    claim_summaries=tuple(
                        sorted({card.claim_text for card in cards})
                    ),
                    excerpt=excerpt,
                    excerpt_truncated=len(excerpt) < len(passage.text),
                    source_passage_sha256=passage.normalized_text_sha256,
                )
            )
        return ReadableEvidenceSetV1(
            items=tuple(items),
            total_available=len(selected_passage_ids),
            truncated=len(selected_passage_ids) > len(items),
        )

    @staticmethod
    def _bounded_warnings(warnings: tuple[str, ...]) -> tuple[str, ...]:
        if len(warnings) <= 32:
            return warnings
        return (*warnings[:31], f"{len(warnings) - 31} additional warnings omitted")


class _TrustedFixtureCompanion(OfflineInspirationCompanionAdapter):
    def __init__(self, *, preparer: HermesFixturePreparer, **kwargs: Any) -> None:
        super().__init__(preparer=preparer, **kwargs)
        self.fixture_preparer = preparer

    def start(
        self,
        *,
        run_id: str,
        request: InspirationRunRequestV1,
    ) -> CompanionTransitionV1:
        if not self.fixture_preparer.supports(request):
            return CompanionTransitionV1(
                state=FailedStateV1(
                    public_error_code="UNSUPPORTED_FIXTURE_REQUEST",
                    public_message=(
                        "this local pilot accepts only its frozen flat-band fixture request"
                    ),
                    retryable=False,
                )
            )
        return super().start(run_id=run_id, request=request)


class _TrustedInspirationCompanion(OfflineInspirationCompanionAdapter):
    """Expose bounded compiler and transient-search failures at the public edge."""

    def __init__(self, *, preparer: HermesInspirationPreparer, **kwargs: Any) -> None:
        super().__init__(preparer=preparer, **kwargs)
        self.inspiration_preparer = preparer

    def start(
        self,
        *,
        run_id: str,
        request: InspirationRunRequestV1,
    ) -> CompanionTransitionV1:
        try:
            self.inspiration_preparer.compile(request)
        except HermesRequestCompilationError:
            return CompanionTransitionV1(
                state=FailedStateV1(
                    public_error_code="UNSUPPORTED_INSPIRATION_REQUEST",
                    public_message=(
                        "the request is outside the bounded inspiration beta contract"
                    ),
                    retryable=False,
                )
            )
        return super().start(run_id=run_id, request=request)

    def _execute(
        self,
        *,
        run_id: str,
        request: InspirationRunRequestV1,
        expected_execution_manifest_sha256: str | None = None,
    ) -> CompanionTransitionV1:
        try:
            return super()._execute(
                run_id=run_id,
                request=request,
                expected_execution_manifest_sha256=(
                    expected_execution_manifest_sha256
                ),
            )
        except SearchAdapterError as error:
            if not (
                error.code in {"NETWORK_ERROR", "WAIT_BUDGET_EXCEEDED"}
                or error.http_status in _RETRYABLE_CROSSREF_HTTP_STATUSES
            ):
                raise
            return CompanionTransitionV1(
                state=FailedStateV1(
                    public_error_code="EXTERNAL_SEARCH_UNAVAILABLE",
                    public_message=(
                        "public metadata search is temporarily unavailable; "
                        "submit a new run later"
                    ),
                    retryable=True,
                )
            )


class _ApprovalBoundInspirationRunner(InspirationRunner):
    """Production marker for the fully approval-bound inspiration runner."""

    @property
    def execution_components(self):
        return super().execution_components


def resolve_hermes_project_root(
    settings: GatewayServerSettings,
    *,
    create: bool,
) -> Path:
    """Resolve the shared service/CLI project boundary without symlinks."""

    if settings.workspace is None:
        raise HermesFixtureConfigurationError(
            "the trusted Hermes fixture requires --workspace"
        )
    try:
        project_id = _IDENTIFIER_ADAPTER.validate_python(
            settings.project_id,
            strict=True,
        )
    except (TypeError, ValueError) as exc:
        raise HermesFixtureConfigurationError("project ID is invalid") from exc
    workspace_input = Path(settings.workspace)
    if workspace_input.is_symlink():
        raise HermesFixtureConfigurationError("workspace cannot be a symlink")
    if create:
        workspace_input.mkdir(parents=True, exist_ok=True)
    if not workspace_input.is_dir():
        raise HermesFixtureConfigurationError("workspace is unavailable")
    workspace = workspace_input.resolve()
    candidate = workspace / project_id
    if candidate.is_symlink():
        raise HermesFixtureConfigurationError("project root cannot be a symlink")
    if create:
        candidate.mkdir(parents=True, exist_ok=True)
    if not candidate.is_dir():
        raise HermesFixtureConfigurationError("project root is unavailable")
    project_root = candidate.resolve()
    if workspace not in project_root.parents:
        raise HermesFixtureConfigurationError("project root escapes the workspace")
    state_directory = project_root / ".gateway"
    if state_directory.is_symlink():
        raise HermesFixtureConfigurationError("Gateway state cannot be a symlink")
    if create:
        state_directory.mkdir(parents=True, exist_ok=True)
    if not state_directory.is_dir():
        raise HermesFixtureConfigurationError("Gateway state is unavailable")
    return project_root


def trusted_state_database_path(
    project_root: Path,
    database_name: str,
    *,
    must_exist: bool,
) -> Path:
    """Return one allowlisted state DB path after explicit no-symlink checks."""

    if database_name not in _TRUSTED_STATE_DATABASE_NAMES:
        raise HermesFixtureConfigurationError("Gateway database name is not allowlisted")
    root = Path(project_root).resolve()
    state_directory = root / ".gateway"
    if state_directory.is_symlink() or not state_directory.is_dir():
        raise HermesFixtureConfigurationError("Gateway state is unavailable")
    database_path = state_directory / database_name
    if database_path.is_symlink():
        raise HermesFixtureConfigurationError(
            "Gateway database path cannot be a symlink"
        )
    if database_path.exists() and not database_path.is_file():
        raise HermesFixtureConfigurationError(
            "Gateway database path is not a regular file"
        )
    if must_exist and not database_path.is_file():
        raise HermesFixtureConfigurationError("Gateway database is unavailable")
    if database_path.parent.resolve() != state_directory.resolve():
        raise HermesFixtureConfigurationError("Gateway database escapes state root")
    return database_path


def create_hermes_fixture_service(
    settings: GatewayServerSettings,
) -> MaterialsGatewayService:
    """Build the trusted persistent service used by the pinned Hermes profile."""

    project_root = resolve_hermes_project_root(settings, create=True)
    assets = _load_fixture_assets()
    store = LocalArtifactStore(project_root)
    policy = hermes_fixture_policy()
    tag_graph = curated_flat_band_tag_graph()
    query_plan = plan_tag_queries(
        tag_graph,
        target_tag_ids=("electronic-flat-band",),
        budget=policy.search,
    )
    search_adapter = FixtureSearchAdapter(
        {
            query.query_id: assets.search_response
            for query in query_plan.queries
        }
    )
    preparer = HermesFixturePreparer(
        store=store,
        project_id=settings.project_id,
        assets=assets,
        policy=policy,
        tag_graph=tag_graph,
        search_adapter=search_adapter,
    )
    runner = InspirationRunner(
        store=store,
        search_adapter=search_adapter,
        transformation_engine=PymatgenTransformationEngine(),
    )
    companion = _TrustedFixtureCompanion(
        runner=runner,
        preparer=preparer,
        projector=HermesFixtureProjector(store),
    )
    gateway_database = trusted_state_database_path(
        project_root,
        GATEWAY_STATE_DATABASE_NAME,
        must_exist=False,
    )
    approval_database = trusted_state_database_path(
        project_root,
        OPERATOR_APPROVAL_DATABASE_NAME,
        must_exist=False,
    )
    repository = SqliteGatewayRepository(gateway_database)
    action_authorizer = SqliteOneTimeActionGrantStore(approval_database)
    return MaterialsGatewayService(
        repository=repository,
        companion=companion,
        artifact_reader=store,
        action_authorizer=action_authorizer,
    )


def create_hermes_inspiration_service(
    settings: GatewayServerSettings,
    *,
    transport: BoundedHttpTransport | None = None,
    sleeper: Callable[[float], None] | None = None,
    wall_clock: Callable[[], float] | None = None,
    monotonic_clock: Callable[[], float] | None = None,
) -> MaterialsGatewayService:
    """Build the approval-gated Crossref service for the bounded local beta.

    The optional transport and clock seams exist for deterministic offline tests;
    the Hermes/MCP factory loader supplies only ``settings``.  Crossref contact
    identity is operator-owned environment state and is never copied into an
    Artifact or component digest.
    """

    project_root = resolve_hermes_project_root(settings, create=True)
    store = LocalArtifactStore(project_root)
    try:
        parent_catalog = load_flat_band_parent_catalog_v1()
    except ParentCatalogIntegrityError as exc:
        raise HermesFixtureConfigurationError(
            "operator-owned parent catalog failed frozen validation"
        ) from exc
    contact_email = os.environ.get(_CROSSREF_CONTACT_EMAIL_ENV)
    if contact_email == "":
        raise HermesFixtureConfigurationError(
            "Crossref contact email environment value is invalid"
        )
    adapter_kwargs: dict[str, Any] = {
        "contact_email": contact_email,
        "max_results": 1,
        "max_retries": _PUBLIC_CROSSREF_MAX_RETRIES,
        "max_retry_delay_seconds": (
            _PUBLIC_CROSSREF_MAX_RETRY_DELAY_SECONDS
        ),
        "max_total_wait_seconds": _PUBLIC_CROSSREF_MAX_TOTAL_WAIT_SECONDS,
        "retry_backoff_seconds": 1.0,
        "timeout_seconds": _PUBLIC_CROSSREF_TIMEOUT_SECONDS,
        "transport": transport,
    }
    if sleeper is not None:
        adapter_kwargs["sleeper"] = sleeper
    if wall_clock is not None:
        adapter_kwargs["wall_clock"] = wall_clock
    if monotonic_clock is not None:
        adapter_kwargs["monotonic_clock"] = monotonic_clock
    try:
        search_adapter = CrossrefPublicAdapter(**adapter_kwargs)
    except (TypeError, ValueError) as exc:
        raise HermesFixtureConfigurationError(
            "Crossref public adapter configuration is invalid"
        ) from exc

    compiler = HermesInspirationRequestCompiler(
        max_retries_per_query=_PUBLIC_CROSSREF_MAX_RETRIES
    )
    tag_graph = curated_flat_band_tag_graph()
    preparer = HermesInspirationPreparer(
        store=store,
        project_id=settings.project_id,
        parent_catalog=parent_catalog,
        tag_graph=tag_graph,
        search_adapter=search_adapter,
        compiler=compiler,
    )
    runner_kwargs: dict[str, Any] = {
        "store": store,
        "search_adapter": search_adapter,
        "transformation_engine": PymatgenTransformationEngine(
            parent_catalog=parent_catalog
        ),
    }
    if monotonic_clock is not None:
        runner_kwargs["monotonic_clock"] = monotonic_clock
    runner = _ApprovalBoundInspirationRunner(**runner_kwargs)
    companion = _TrustedInspirationCompanion(
        runner=runner,
        preparer=preparer,
        projector=HermesFixtureProjector(store),
    )
    gateway_database = trusted_state_database_path(
        project_root,
        GATEWAY_STATE_DATABASE_NAME,
        must_exist=False,
    )
    approval_database = trusted_state_database_path(
        project_root,
        OPERATOR_APPROVAL_DATABASE_NAME,
        must_exist=False,
    )
    return MaterialsGatewayService(
        repository=SqliteGatewayRepository(gateway_database),
        companion=companion,
        artifact_reader=store,
        action_authorizer=SqliteOneTimeActionGrantStore(approval_database),
    )
