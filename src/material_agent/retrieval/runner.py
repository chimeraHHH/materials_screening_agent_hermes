"""End-to-end Agent 01 stage runner."""

from __future__ import annotations

import hashlib
import importlib.metadata
import random
import time
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any, TypeVar

from pydantic import ValidationError

from material_agent.retrieval.adapters import MaterialsSourceAdapter
from material_agent.retrieval.dedup import (
    annotate_exact_duplicates,
    annotate_similarity_clusters,
)
from material_agent.retrieval.evaluator import evaluate_candidate
from material_agent.retrieval.models import (
    ArtifactRef,
    CandidateAuditRecord,
    Decision,
    ErrorRecord,
    OperationRecord,
    ProvenanceStatus,
    Requirement,
    RetrievalPolicy,
    RetrievalQueryPlan,
    RetrievalStageContext,
    RetrievalStageInput,
    RetrievalStagePlan,
    SourceMetadata,
    StageInputValidation,
    StageOutcome,
    StageOutcomeType,
    StageResultEnvelope,
    StageStatus,
)
from material_agent.retrieval.normalizer import (
    add_dimensionality_property,
    apply_task_metadata,
    candidate_id_for,
    collect_origin_task_ids,
    normalize_candidate,
)
from material_agent.retrieval.query import (
    QueryPlanningError,
    build_query_plan,
    validate_requirement_contract,
)
from material_agent.retrieval.ranking import rank_and_publish
from material_agent.retrieval.reporting import build_report, report_to_markdown
from material_agent.retrieval.storage import (
    ArtifactConflictError,
    ArtifactStoreError,
    LocalArtifactStore,
)
from material_agent.retrieval.structures import (
    StructureValidationError,
    calculate_dimensionality,
    process_structure,
)


class BackendInconsistentError(RuntimeError):
    """Raised when immutable stage evidence is missing or has changed."""


ResultT = TypeVar("ResultT")


class RetrievalStageRunner:
    """Execute a deterministic, resumable retrieval stage."""

    def __init__(
        self,
        *,
        adapter: MaterialsSourceAdapter,
        artifact_store: LocalArtifactStore,
        policy: RetrievalPolicy | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.adapter = adapter
        self.store = artifact_store
        self.policy = policy or RetrievalPolicy()
        self.clock = clock or (lambda: datetime.now(UTC))

    def validate_input(
        self, context: RetrievalStageContext
    ) -> StageInputValidation:
        requirement = context.requirement
        stage_input = context.stage_input
        errors: list[str] = []
        missing: list[str] = []
        remediation: list[str] = []
        error_category: str | None = None
        validated_artifact_uri: str | None = None
        validated_artifact_sha256: str | None = None

        if not stage_input.confirmed_by_user or not requirement.confirmed_by_user:
            errors.append("requirement is not confirmed")
            remediation.append("confirm the Requirement revision before running Agent 01")
        if stage_input.requirement_revision != requirement.revision:
            errors.append("requirement revision mismatch")
            remediation.append("rebuild StageInput from the confirmed Requirement revision")
        if stage_input.retrieval_policy_version != self.policy.policy_version:
            errors.append("retrieval policy version mismatch")
            remediation.append("prepare a new StageInput using the active retrieval policy")
        if not stage_input.requirement_hash:
            missing.append("requirement_hash")
        if not stage_input.requirement_artifact_uri:
            missing.append("requirement_artifact_uri")
        try:
            validate_requirement_contract(requirement)
        except QueryPlanningError as exc:
            errors.append(str(exc))
            remediation.append("correct and reconfirm the Requirement")
            error_category = "INVALID_INPUT"

        if not missing:
            if not stage_input.requirement_artifact_uri.startswith("artifact://"):
                errors.append(
                    "requirement_artifact_uri must reference the current Artifact Store"
                )
                error_category = "INPUT_INTEGRITY_ERROR"
            else:
                try:
                    artifact_ref = self.store.inspect(
                        stage_input.requirement_artifact_uri,
                        media_type="application/json",
                    )
                    artifact_payload = self.store.read_json(
                        stage_input.requirement_artifact_uri
                    )
                    artifact_requirement = Requirement.model_validate(
                        artifact_payload
                    )
                except FileNotFoundError:
                    missing.append("requirement_artifact")
                    remediation.append(
                        "write the confirmed Requirement to the project Artifact Store"
                    )
                except (ArtifactStoreError, ValueError, ValidationError):
                    errors.append("requirement artifact is invalid")
                    error_category = "INPUT_INTEGRITY_ERROR"
                else:
                    validated_artifact_uri = artifact_ref.uri
                    validated_artifact_sha256 = artifact_ref.sha256
                    if artifact_ref.sha256 != stage_input.requirement_hash:
                        errors.append("requirement artifact hash mismatch")
                        error_category = "INPUT_INTEGRITY_ERROR"
                    if artifact_requirement.revision != stage_input.requirement_revision:
                        errors.append("requirement artifact revision mismatch")
                        error_category = "INPUT_INTEGRITY_ERROR"
                    if (
                        artifact_requirement.model_dump(mode="json")
                        != requirement.model_dump(mode="json")
                    ):
                        errors.append(
                            "requirement artifact content does not match Runner input"
                        )
                        error_category = "INPUT_INTEGRITY_ERROR"

        if missing:
            error_category = "MISSING_INPUT"
        elif errors and error_category is None:
            error_category = "INVALID_INPUT"

        return StageInputValidation(
            valid=not errors and not missing,
            missing_fields=missing,
            errors=errors,
            remediation=remediation,
            error_category=error_category,
            requirement_artifact_uri=validated_artifact_uri,
            requirement_artifact_sha256=validated_artifact_sha256,
        )

    def prepare(
        self,
        context: RetrievalStageContext,
        metadata: SourceMetadata | None = None,
    ) -> RetrievalStagePlan:
        requirement = context.requirement
        stage_input = context.stage_input
        validation = self.validate_input(context)
        if not validation.valid:
            raise QueryPlanningError(
                "; ".join([*validation.errors, *validation.missing_fields])
            )
        source_metadata = metadata or self._call_with_retry(
            "materials_project.metadata", self.adapter.metadata
        )
        query_plan = build_query_plan(
            requirement,
            stage_input.requirement_hash,
            source_metadata,
            self.policy,
        )
        query_plan = query_plan.model_copy(
            update={"created_at": self.clock()}
        )
        idempotency_payload = (
            f"{stage_input.project_id}:{stage_input.run_id}:"
            f"{query_plan.query_fingerprint}"
        ).encode("utf-8")
        return RetrievalStagePlan(
            context=context,
            query_plan=query_plan,
            source_metadata=source_metadata,
            idempotency_key=hashlib.sha256(idempotency_payload).hexdigest(),
        )

    def start(
        self, plan: RetrievalStagePlan, idempotency_key: str
    ) -> StageOutcome:
        if idempotency_key != plan.idempotency_key:
            error = ErrorRecord(
                category="IDEMPOTENCY_KEY_MISMATCH",
                retryable=False,
                operation="start",
                public_message="idempotency key does not match the prepared stage plan",
            )
            return StageOutcome(
                outcome=StageOutcomeType.FAILED,
                status=StageStatus.PERMANENT_FAILED,
                errors=[error],
            )
        result = self.run(
            plan.context.requirement,
            plan.context.stage_input,
            prepared_stage_plan=plan,
        )
        operation_ref = (
            f"artifact://stages/agent01/{plan.context.stage_input.run_id}/"
            f"operations/{plan.query_plan.query_fingerprint}.result.json"
            if result.status in {StageStatus.SUCCEEDED, StageStatus.PARTIAL}
            else None
        )
        return _stage_outcome(result, operation_ref=operation_ref)

    def reconcile(self, operation_ref: str) -> StageOutcome:
        try:
            payload = self.store.read_json(operation_ref)
            operation = OperationRecord.model_validate(payload)
            result = self._load_valid_result(
                operation_ref, operation.query_fingerprint
            )
        except (
            FileNotFoundError,
            ValueError,
            ArtifactStoreError,
            BackendInconsistentError,
        ) as exc:
            error = ErrorRecord(
                category="BACKEND_INCONSISTENT",
                retryable=False,
                operation="reconcile",
                public_message=(
                    f"operation cannot be reconciled ({type(exc).__name__})"
                ),
            )
            return StageOutcome(
                outcome=StageOutcomeType.FAILED,
                status=StageStatus.PERMANENT_FAILED,
                operation_ref=operation_ref,
                errors=[error],
            )
        return _stage_outcome(result, operation_ref=operation_ref)

    def run(
        self,
        requirement: Requirement,
        stage_input: RetrievalStageInput,
        *,
        prepared_stage_plan: RetrievalStagePlan | None = None,
    ) -> StageResultEnvelope:
        context = RetrievalStageContext(
            requirement=requirement,
            stage_input=stage_input,
        )
        started_at = self.clock()
        stage_prefix = f"stages/agent01/{stage_input.run_id}"
        validation = self.validate_input(context)
        if not validation.valid:
            messages = [*validation.errors, *validation.missing_fields]
            return self._write_early_result(
                stage_input=stage_input,
                stage_prefix=stage_prefix,
                input_uri=stage_input.requirement_artifact_uri,
                started_at=started_at,
                status=(
                    StageStatus.BLOCKED_MISSING_INPUT
                    if validation.error_category
                    in {"MISSING_INPUT", "INVALID_INPUT"}
                    else StageStatus.PERMANENT_FAILED
                ),
                error=ErrorRecord(
                    category=validation.error_category or "INVALID_INPUT",
                    retryable=False,
                    operation="validate_input",
                    public_message="; ".join(messages),
                ),
                warnings=validation.remediation,
                provenance={"policy_version": self.policy.policy_version},
                persist=False,
            )

        try:
            input_ref = self.store.write_json(
                f"{stage_prefix}/input_snapshot.json",
                {
                    "stage_input": stage_input.model_dump(mode="json"),
                    "requirement": requirement.model_dump(mode="json"),
                },
                immutable=True,
            )
        except ArtifactConflictError:
            return self._backend_inconsistent_result(
                stage_input=stage_input,
                input_uri=stage_input.requirement_artifact_uri,
                started_at=started_at,
                operation="input_snapshot",
                message="existing Stage Run input snapshot has different content",
            )

        prior_plan_path = f"{stage_prefix}/query_plan.json"
        try:
            prior_plan = self._load_prior_plan(prior_plan_path)
        except BackendInconsistentError as exc:
            return self._backend_inconsistent_result(
                stage_input=stage_input,
                input_uri=input_ref.uri,
                started_at=started_at,
                operation="resume",
                message=str(exc),
            )
        if prior_plan is not None:
            conflict_fields = [
                field
                for field, expected in (
                    ("requirement_hash", stage_input.requirement_hash),
                    ("policy_version", self.policy.policy_version),
                )
                if getattr(prior_plan, field) != expected
            ]
            if conflict_fields:
                return self._backend_inconsistent_result(
                    stage_input=stage_input,
                    input_uri=input_ref.uri,
                    started_at=started_at,
                    operation="resume",
                    message=(
                        "existing Stage Run snapshot changed: "
                        + ", ".join(conflict_fields)
                    ),
                )
            operation_path = (
                f"{stage_prefix}/operations/"
                f"{prior_plan.query_fingerprint}.result.json"
            )
            if self.store.exists(operation_path):
                try:
                    return self._load_valid_result(
                        operation_path, prior_plan.query_fingerprint
                    )
                except BackendInconsistentError as exc:
                    return self._backend_inconsistent_result(
                        stage_input=stage_input,
                        input_uri=input_ref.uri,
                        started_at=started_at,
                        operation="reconcile",
                        message=str(exc),
                    )

        try:
            stage_plan = prepared_stage_plan or self.prepare(context)
            if stage_plan.context != context:
                raise QueryPlanningError(
                    "prepared StagePlan context does not match run input"
                )
            metadata = stage_plan.source_metadata
            prepared_plan = stage_plan.query_plan
        except Exception as exc:
            retryable = _is_retryable(exc)
            category = _error_category(exc, operation="prepare")
            status = (
                StageStatus.RETRYABLE_FAILED
                if retryable
                else StageStatus.PERMANENT_FAILED
            )
            return self._write_early_result(
                stage_input=stage_input,
                stage_prefix=stage_prefix,
                input_uri=input_ref.uri,
                started_at=started_at,
                status=status,
                error=ErrorRecord(
                    category=category,
                    retryable=retryable,
                    operation="prepare",
                    public_message=f"retrieval preparation failed ({type(exc).__name__})",
                ),
                warnings=[],
                provenance={"policy_version": self.policy.policy_version},
            )

        if prior_plan is not None:
            conflict_fields = [
                field
                for field in (
                    "database_version",
                    "requirement_hash",
                    "policy_version",
                    "query_fingerprint",
                )
                if getattr(prior_plan, field) != getattr(prepared_plan, field)
            ]
            if conflict_fields:
                return self._backend_inconsistent_result(
                    stage_input=stage_input,
                    input_uri=input_ref.uri,
                    started_at=started_at,
                    operation="resume",
                    message=(
                        "existing Stage Run cannot be resumed because its "
                        f"snapshot changed: {', '.join(conflict_fields)}"
                    ),
                )
            plan = prior_plan
        else:
            plan = prepared_plan

        operation_path = (
            f"{stage_prefix}/operations/{plan.query_fingerprint}.result.json"
        )

        output_artifacts = []
        warnings: list[str] = []
        errors: list[ErrorRecord] = []

        try:
            output_artifacts.append(
                self.store.write_json(
                    f"{stage_prefix}/capability_snapshot.json",
                    metadata.model_dump(mode="json"),
                    immutable=True,
                )
            )
            output_artifacts.append(
                self.store.write_json(
                    f"{stage_prefix}/query_plan.json",
                    plan.model_dump(mode="json"),
                    immutable=True,
                )
            )
        except ArtifactConflictError:
            return self._backend_inconsistent_result(
                stage_input=stage_input,
                input_uri=input_ref.uri,
                started_at=started_at,
                operation="write_stage_snapshot",
                message="immutable stage snapshot has different content",
            )

        try:
            resumed_raw = self._load_raw_documents(stage_prefix)
            if resumed_raw is None:
                documents = self._search_with_retry(plan)
            else:
                documents, raw_refs = resumed_raw
                output_artifacts.extend(raw_refs)
        except BackendInconsistentError as exc:
            return self._backend_inconsistent_result(
                stage_input=stage_input,
                input_uri=input_ref.uri,
                started_at=started_at,
                operation="resume_raw_response",
                message=str(exc),
            )
        except Exception as exc:
            retryable = _is_retryable(exc)
            status = (
                StageStatus.RETRYABLE_FAILED
                if retryable
                else StageStatus.PERMANENT_FAILED
            )
            search_error = ErrorRecord(
                category=(
                    "TRANSIENT_EXTERNAL" if retryable else "INVALID_RESPONSE"
                ),
                retryable=retryable,
                operation="materials_project.search",
                public_message=f"Materials Project search failed ({type(exc).__name__})",
            )
            report = build_report(
                query_plan=plan,
                candidates=[],
                raw_count=0,
                scan_truncated=False,
                status=status,
                warnings=[],
                exact_duplicate_groups=[],
                similarity_clusters=[],
            )
            output_artifacts.extend(
                [
                    self.store.write_json(
                        f"{stage_prefix}/retrieval_report.json", report
                    ),
                    self.store.write_text(
                        f"{stage_prefix}/retrieval_report.md",
                        report_to_markdown(report),
                        "text/markdown",
                    ),
                ]
            )
            result = StageResultEnvelope(
                run_id=stage_input.run_id,
                status=status,
                input_snapshot_uri=input_ref.uri,
                input_snapshot_sha256=input_ref.sha256,
                output_artifacts=_unique_artifacts(output_artifacts),
                candidate_ids=[],
                errors=[search_error],
                metrics=report["funnel"] | report["limits"],
                provenance={
                    "source": "materials_project",
                    "database_version": plan.database_version,
                    "query_id": plan.query_id,
                    "query_fingerprint": plan.query_fingerprint,
                    "policy_version": self.policy.policy_version,
                    "is_mock": bool(getattr(self.adapter, "is_mock", False)),
                },
                started_at=started_at,
                finished_at=self.clock(),
            )
            self.store.write_json(
                f"{stage_prefix}/stage_result.json", result.model_dump(mode="json")
            )
            return result

        if resumed_raw is None:
            raw_manifest: list[dict[str, Any]] = []
            try:
                for batch_index, start in enumerate(
                    range(0, len(documents), plan.chunk_size), start=1
                ):
                    batch = documents[start : start + plan.chunk_size]
                    ref = self.store.write_gzip_jsonl(
                        f"{stage_prefix}/raw_response_batches/"
                        f"batch-{batch_index:05d}.jsonl.gz",
                        batch,
                        immutable=True,
                    )
                    output_artifacts.append(ref)
                    raw_manifest.append(
                        {
                            "batch_index": batch_index,
                            "record_count": len(batch),
                            "artifact": ref.model_dump(mode="json"),
                        }
                    )
                output_artifacts.append(
                    self.store.write_jsonl(
                        f"{stage_prefix}/raw_response_manifest.jsonl",
                        raw_manifest,
                        immutable=True,
                    )
                )
            except ArtifactConflictError:
                return self._backend_inconsistent_result(
                    stage_input=stage_input,
                    input_uri=input_ref.uri,
                    started_at=started_at,
                    operation="archive_raw_response",
                    message="immutable raw response artifact has different content",
                )

        raw_count = len(documents)
        documents, duplicate_material_ids = _deduplicate_documents(documents)
        if duplicate_material_ids:
            warnings.append(
                "duplicate material IDs were returned and normalized once: "
                + ", ".join(duplicate_material_ids)
            )

        retrieved_at = plan.created_at
        candidates: list[CandidateAuditRecord] = []
        structures = {}

        for index, document in enumerate(documents):
            material_id = str(document.get("material_id") or f"unknown-{index}")
            candidate_id = candidate_id_for(stage_input.project_id, material_id)
            try:
                processed = process_structure(
                    document.get("structure"),
                    summary_elements=document.get("elements"),
                    summary_num_sites=document.get("nsites"),
                    summary_formula=document.get("formula_pretty"),
                    policy=self.policy,
                )
                for warning in processed.data_quality_warnings:
                    warnings.append(
                        f"{material_id}: CIF round-trip warning "
                        f"({warning.split(':', 1)[0]})"
                    )
                source_ref = self.store.write_json(
                    f"candidates/structures/{processed.structure_id}.source.json",
                    processed.source_payload,
                    immutable=True,
                )
                cif_ref = self.store.write_text(
                    f"candidates/structures/{processed.structure_id}.cif",
                    processed.cif_text,
                    "chemical/x-cif",
                    immutable=True,
                )
                output_artifacts.extend([source_ref, cif_ref])
                candidate = normalize_candidate(
                    document,
                    project_id=stage_input.project_id,
                    query_plan=plan,
                    processed_structure=processed,
                    source_uri=source_ref.uri,
                    source_sha256=source_ref.sha256,
                    structure_uri=cif_ref.uri,
                    structure_sha256=cif_ref.sha256,
                    retrieved_at=retrieved_at,
                )
                dimensionality = calculate_dimensionality(processed.structure)
                candidate = add_dimensionality_property(
                    candidate,
                    value=dimensionality.value,
                    method=dimensionality.method,
                    policy_version=self.policy.dimensionality_policy_version,
                    database_version=plan.database_version,
                    retrieved_at=retrieved_at,
                    warning_messages=dimensionality.warnings,
                )
                if dimensionality.error:
                    warnings.append(
                        f"{material_id}: dimensionality unavailable "
                        f"({dimensionality.error.split(':', 1)[0]})"
                    )
                for warning in dimensionality.warnings or []:
                    warnings.append(
                        f"{material_id}: dimensionality warning "
                        f"({warning.split(':', 1)[0]})"
                    )
                candidates.append(candidate)
                structures[candidate.candidate_id] = processed.structure
            except ArtifactConflictError:
                return self._backend_inconsistent_result(
                    stage_input=stage_input,
                    input_uri=input_ref.uri,
                    started_at=started_at,
                    operation="write_structure",
                    message=(
                        f"immutable structure artifact changed for {material_id}"
                    ),
                )
            except (StructureValidationError, KeyError, TypeError, ValueError) as exc:
                candidates.append(
                    CandidateAuditRecord(
                        candidate_id=candidate_id,
                        formula=str(document.get("formula_pretty") or "unknown"),
                        source_database_version=plan.database_version,
                        source_material_id=material_id,
                        query_id=plan.query_id,
                        decision=Decision.FAILED,
                        decision_reasons=["STRUCTURE_INVALID"],
                        data_quality_flags=["STRUCTURE_INVALID"],
                        provenance={
                            "source_endpoint": plan.endpoint,
                            "database_version": plan.database_version,
                            "query_fingerprint": plan.query_fingerprint,
                        },
                    )
                )
                errors.append(
                    ErrorRecord(
                        category="INVALID_RESPONSE",
                        retryable=False,
                        operation="process_structure",
                        public_message=f"{material_id}: structure could not be processed",
                        candidate_id=candidate_id,
                    )
                )
                warnings.append(
                    f"{material_id}: structure processing failed ({type(exc).__name__})"
                )

        usable = [
            candidate
            for candidate in candidates
            if candidate.decision is not Decision.FAILED
        ]
        task_ids, material_ids = collect_origin_task_ids(usable)
        resolved_task_metadata: dict[str, dict[str, Any]] = {}
        if task_ids:
            try:
                resolved_task_metadata, origin_warnings = self._call_with_retry(
                    "materials_project.resolve_task_metadata",
                    lambda: self.adapter.resolve_task_metadata(
                        task_ids, material_ids, self.policy.origin_batch_size
                    ),
                )
                warnings.extend(origin_warnings)
            except Exception as exc:
                warnings.append(
                    f"origin metadata resolution failed ({type(exc).__name__})"
                )
        origin_records = [
            _origin_resolution_record(task_id, resolved_task_metadata.get(task_id))
            for task_id in task_ids
        ]

        candidates = [
            (
                evaluate_candidate(
                    apply_task_metadata(candidate, resolved_task_metadata),
                    requirement,
                    self.policy,
                )
                if candidate.decision is not Decision.FAILED
                else candidate
            )
            for candidate in candidates
        ]

        candidates, exact_groups = annotate_exact_duplicates(candidates)
        candidates, _ = rank_and_publish(
            candidates,
            requirement.ranking_preferences,
            requirement.budget.max_candidates,
        )
        try:
            candidates, similarity_clusters = annotate_similarity_clusters(
                candidates, structures
            )
        except Exception as exc:
            similarity_clusters = []
            warnings.append(f"structure clustering failed ({type(exc).__name__})")

        published = sorted(
            [candidate for candidate in candidates if candidate.published_downstream],
            key=lambda item: item.publication_rank or 0,
        )
        scan_truncated = raw_count >= plan.max_records_scanned
        critical_properties = {"band_gap", "energy_above_hull", "num_sites"}
        provenance_total = 0
        provenance_unresolved = 0
        for candidate in candidates:
            if candidate.decision is Decision.FAILED:
                continue
            for prop in candidate.properties:
                if prop.name in critical_properties:
                    provenance_total += 1
                    if prop.origin.status.value == "UNRESOLVED":
                        provenance_unresolved += 1

        if scan_truncated or errors or (
            provenance_total > 0 and provenance_unresolved == provenance_total
        ):
            status = StageStatus.PARTIAL
        else:
            status = StageStatus.SUCCEEDED

        try:
            output_artifacts.extend(
                [
                    self.store.write_jsonl(
                        f"{stage_prefix}/origin_resolution.jsonl",
                        origin_records,
                        immutable=True,
                    ),
                    self.store.write_jsonl(
                        f"{stage_prefix}/candidate_audit.jsonl",
                        [
                            candidate.model_dump(mode="json")
                            for candidate in candidates
                        ],
                        immutable=True,
                    ),
                    self.store.write_jsonl(
                        f"{stage_prefix}/dedup_map.jsonl",
                        exact_groups,
                        immutable=True,
                    ),
                    self.store.write_jsonl(
                        f"{stage_prefix}/structure_clusters.jsonl",
                        similarity_clusters,
                        immutable=True,
                    ),
                ]
            )
            candidate_manifest_ref = self.store.write_jsonl(
                f"{stage_prefix}/candidate_manifest.jsonl",
                [candidate.model_dump(mode="json") for candidate in published],
                immutable=True,
            )
            output_artifacts.append(candidate_manifest_ref)

            report = build_report(
                query_plan=plan,
                candidates=candidates,
                raw_count=raw_count,
                scan_truncated=scan_truncated,
                status=status,
                warnings=warnings,
                exact_duplicate_groups=exact_groups,
                similarity_clusters=similarity_clusters,
            )
            report_json_ref = self.store.write_json(
                f"{stage_prefix}/retrieval_report.json",
                report,
                immutable=True,
            )
            report_md_ref = self.store.write_text(
                f"{stage_prefix}/retrieval_report.md",
                report_to_markdown(report),
                "text/markdown",
                immutable=True,
            )
            output_artifacts.extend([report_json_ref, report_md_ref])
        except ArtifactConflictError:
            return self._backend_inconsistent_result(
                stage_input=stage_input,
                input_uri=input_ref.uri,
                started_at=started_at,
                operation="write_stage_output",
                message="immutable Stage Run output has different content",
            )

        finished_at = self.clock()
        result = StageResultEnvelope(
            run_id=stage_input.run_id,
            status=status,
            input_snapshot_uri=input_ref.uri,
            input_snapshot_sha256=input_ref.sha256,
            output_artifacts=_unique_artifacts(output_artifacts),
            candidate_manifest=candidate_manifest_ref,
            candidate_ids=[candidate.candidate_id for candidate in published],
            warnings=sorted(set(warnings)),
            errors=errors,
            metrics=report["funnel"] | report["limits"],
            provenance={
                "source": "materials_project",
                "database_version": plan.database_version,
                "query_id": plan.query_id,
                "query_fingerprint": plan.query_fingerprint,
                "policy_version": self.policy.policy_version,
                "mp_api_version": _distribution_version("mp-api"),
                "pymatgen_version": _distribution_version("pymatgen"),
                "is_mock": bool(getattr(self.adapter, "is_mock", False)),
            },
            started_at=started_at,
            finished_at=finished_at,
        )
        stage_result_ref = self.store.write_json(
            f"{stage_prefix}/stage_result.json", result.model_dump(mode="json")
        )
        registered_artifacts = _unique_artifacts(
            [input_ref, *result.output_artifacts, stage_result_ref]
        )
        operation = OperationRecord(
            operation_id=plan.query_fingerprint,
            query_fingerprint=plan.query_fingerprint,
            result=result,
            registered_artifacts=registered_artifacts,
        )
        try:
            self.store.write_json(
                operation_path,
                operation.model_dump(mode="json"),
                immutable=True,
            )
        except ArtifactConflictError:
            return self._backend_inconsistent_result(
                stage_input=stage_input,
                input_uri=input_ref.uri,
                started_at=started_at,
                operation="complete_operation",
                message="completed operation record has conflicting content",
            )
        return result

    def _write_early_result(
        self,
        *,
        stage_input: RetrievalStageInput,
        stage_prefix: str,
        input_uri: str,
        started_at: datetime,
        status: StageStatus,
        error: ErrorRecord,
        warnings: list[str],
        provenance: dict[str, Any],
        persist: bool = True,
    ) -> StageResultEnvelope:
        input_hash = None
        if input_uri.startswith("artifact://"):
            try:
                if self.store.exists(input_uri):
                    input_hash = self.store.inspect(input_uri).sha256
            except ArtifactStoreError:
                pass
        result = StageResultEnvelope(
            run_id=stage_input.run_id,
            status=status,
            input_snapshot_uri=input_uri,
            input_snapshot_sha256=input_hash,
            output_artifacts=[],
            candidate_ids=[],
            warnings=warnings,
            errors=[error],
            metrics={},
            provenance=provenance,
            started_at=started_at,
            finished_at=self.clock(),
        )
        if persist:
            self.store.write_json(
                f"{stage_prefix}/stage_result.json", result.model_dump(mode="json")
            )
        return result

    def _backend_inconsistent_result(
        self,
        *,
        stage_input: RetrievalStageInput,
        input_uri: str,
        started_at: datetime,
        operation: str,
        message: str,
    ) -> StageResultEnvelope:
        return self._write_early_result(
            stage_input=stage_input,
            stage_prefix=f"stages/agent01/{stage_input.run_id}",
            input_uri=input_uri,
            started_at=started_at,
            status=StageStatus.PERMANENT_FAILED,
            error=ErrorRecord(
                category="BACKEND_INCONSISTENT",
                retryable=False,
                operation=operation,
                public_message=message,
            ),
            warnings=[
                "start a new run_id or restore the registered immutable artifact"
            ],
            provenance={"policy_version": self.policy.policy_version},
            persist=False,
        )

    def _load_prior_plan(self, path: str) -> RetrievalQueryPlan | None:
        try:
            payload = self.store.read_json(path)
        except FileNotFoundError:
            return None
        try:
            return RetrievalQueryPlan.model_validate(payload)
        except (ValueError, ValidationError) as exc:
            raise BackendInconsistentError(
                "existing query plan is invalid"
            ) from exc

    def _load_raw_documents(
        self, stage_prefix: str
    ) -> tuple[list[dict[str, Any]], list[ArtifactRef]] | None:
        manifest_path = f"{stage_prefix}/raw_response_manifest.jsonl"
        if not self.store.exists(manifest_path):
            return None
        try:
            manifest_ref = self.store.inspect(
                manifest_path, media_type="application/x-ndjson"
            )
            rows = self.store.read_jsonl(manifest_path)
            documents: list[dict[str, Any]] = []
            refs: list[ArtifactRef] = [manifest_ref]
            for expected_index, row in enumerate(rows, start=1):
                if row.get("batch_index") != expected_index:
                    raise BackendInconsistentError(
                        "raw response manifest batch order is invalid"
                    )
                ref = ArtifactRef.model_validate(row["artifact"])
                if not self.store.exists_with_hash(ref.uri, ref.sha256):
                    raise BackendInconsistentError(
                        f"registered raw response artifact is missing or changed: "
                        f"{ref.uri}"
                    )
                batch = self.store.read_gzip_jsonl(ref.uri)
                if len(batch) != row.get("record_count"):
                    raise BackendInconsistentError(
                        f"raw response record count changed: {ref.uri}"
                    )
                documents.extend(batch)
                refs.append(ref)
            return documents, refs
        except BackendInconsistentError:
            raise
        except (KeyError, TypeError, ValueError, ValidationError) as exc:
            raise BackendInconsistentError(
                "raw response manifest is invalid"
            ) from exc

    def _search_with_retry(self, plan: RetrievalQueryPlan) -> list[dict[str, Any]]:
        return self._call_with_retry(
            "materials_project.search", lambda: self.adapter.search(plan)
        )

    def _call_with_retry(
        self, operation: str, function: Callable[[], ResultT]
    ) -> ResultT:
        last_error: Exception | None = None
        for attempt in range(1, self.policy.max_attempts + 1):
            try:
                return function()
            except Exception as exc:
                last_error = exc
                if attempt >= self.policy.max_attempts or not _is_retryable(exc):
                    raise
                delay = self.policy.retry_base_seconds * (2 ** (attempt - 1))
                delay += random.uniform(0, max(delay * 0.1, 0.001))
                time.sleep(delay)
        raise RuntimeError(f"{operation} attempts exhausted") from last_error

    def _load_valid_result(
        self, operation_path: str, expected_query_fingerprint: str
    ) -> StageResultEnvelope:
        try:
            payload = self.store.read_json(operation_path)
            operation = OperationRecord.model_validate(payload)
        except (FileNotFoundError, ValueError, ValidationError) as exc:
            raise BackendInconsistentError(
                "completed operation record is missing or invalid"
            ) from exc
        if (
            operation.operation_id != expected_query_fingerprint
            or operation.query_fingerprint != expected_query_fingerprint
            or operation.result.provenance.get("query_fingerprint")
            != expected_query_fingerprint
        ):
            raise BackendInconsistentError(
                "completed operation identity does not match the query plan"
            )
        invalid = [
            ref.uri
            for ref in operation.registered_artifacts
            if not self.store.exists_with_hash(ref.uri, ref.sha256)
        ]
        if invalid:
            raise BackendInconsistentError(
                "completed operation has missing or changed artifacts: "
                + ", ".join(sorted(invalid))
            )
        result_refs = {ref.uri: ref.sha256 for ref in operation.result.output_artifacts}
        registered = {
            ref.uri: ref.sha256 for ref in operation.registered_artifacts
        }
        if any(registered.get(uri) != digest for uri, digest in result_refs.items()):
            raise BackendInconsistentError(
                "completed operation artifact registry does not match its result"
            )
        if operation.result.candidate_manifest is not None:
            manifest = operation.result.candidate_manifest
            if result_refs.get(manifest.uri) != manifest.sha256:
                raise BackendInconsistentError(
                    "completed operation candidate manifest is not registered"
                )
        if (
            registered.get(operation.result.input_snapshot_uri)
            != operation.result.input_snapshot_sha256
        ):
            raise BackendInconsistentError(
                "completed operation input snapshot is not registered"
            )
        return operation.result


def _is_retryable(exc: Exception) -> bool:
    try:
        import requests

        if isinstance(exc, (requests.Timeout, requests.ConnectionError)):
            return True
        if isinstance(exc, requests.HTTPError) and exc.response is not None:
            return exc.response.status_code == 429 or exc.response.status_code >= 500
    except ImportError:  # pragma: no cover
        pass
    status_code = getattr(exc, "status_code", None)
    if status_code == 429 or (isinstance(status_code, int) and status_code >= 500):
        return True
    text = str(exc).lower()
    return any(token in text for token in ("timeout", "rate limit", "429", "temporar"))


def _error_category(exc: Exception, *, operation: str) -> str:
    if _is_retryable(exc):
        return "TRANSIENT_EXTERNAL"
    if isinstance(exc, QueryPlanningError):
        return "API_SCHEMA_DRIFT"
    status_code = getattr(exc, "status_code", None)
    response = getattr(exc, "response", None)
    if response is not None:
        status_code = getattr(response, "status_code", status_code)
    if status_code in {401, 403} or "MP_API_KEY" in str(exc):
        return "PERMANENT_CONFIGURATION"
    return "PERMANENT_CONFIGURATION" if operation == "prepare" else "INVALID_RESPONSE"


def _origin_resolution_record(
    task_id: str, metadata: dict[str, Any] | None
) -> dict[str, Any]:
    if metadata is None:
        return {
            "task_id": task_id,
            "status": ProvenanceStatus.UNRESOLVED.value,
            "run_type": None,
            "task_type": None,
            "calc_type": None,
        }
    method_fields = {
        "run_type": metadata.get("run_type"),
        "task_type": metadata.get("task_type"),
        "calc_type": metadata.get("calc_type"),
    }
    status = (
        ProvenanceStatus.RESOLVED
        if any(value is not None for value in method_fields.values())
        else ProvenanceStatus.PARTIAL
    )
    return {
        "task_id": task_id,
        "status": status.value,
        **method_fields,
    }


def _deduplicate_documents(
    documents: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[str]]:
    unique: list[dict[str, Any]] = []
    seen: set[str] = set()
    duplicates: set[str] = set()
    for document in documents:
        material_id = document.get("material_id")
        if material_id is None:
            unique.append(document)
            continue
        key = str(material_id)
        if key in seen:
            duplicates.add(key)
            continue
        seen.add(key)
        unique.append(document)
    return unique, sorted(duplicates)


def _stage_outcome(
    result: StageResultEnvelope, *, operation_ref: str | None
) -> StageOutcome:
    if result.status in {StageStatus.SUCCEEDED, StageStatus.PARTIAL}:
        outcome = StageOutcomeType.COMPLETED
    elif result.status is StageStatus.BLOCKED_MISSING_INPUT:
        outcome = StageOutcomeType.BLOCKED
    else:
        outcome = StageOutcomeType.FAILED
    return StageOutcome(
        outcome=outcome,
        status=result.status,
        operation_ref=operation_ref,
        result=result,
        errors=result.errors,
    )


def _unique_artifacts(artifacts):
    by_uri = {artifact.uri: artifact for artifact in artifacts}
    return [by_uri[uri] for uri in sorted(by_uri)]


def _distribution_version(name: str) -> str:
    try:
        return importlib.metadata.version(name)
    except importlib.metadata.PackageNotFoundError:
        return "unknown"
