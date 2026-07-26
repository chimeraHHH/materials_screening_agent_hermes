"""End-to-end Agent 01 stage runner."""

from __future__ import annotations

import importlib.metadata
import random
import time
from datetime import UTC, datetime
from typing import Any

from material_agent.retrieval.adapters import MaterialsSourceAdapter
from material_agent.retrieval.dedup import (
    annotate_exact_duplicates,
    annotate_similarity_clusters,
)
from material_agent.retrieval.evaluator import evaluate_candidate
from material_agent.retrieval.models import (
    CandidateAuditRecord,
    Decision,
    ErrorRecord,
    RetrievalPolicy,
    RetrievalQueryPlan,
    RetrievalStageInput,
    SourceMetadata,
    StageInputValidation,
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
from material_agent.retrieval.query import QueryPlanningError, build_query_plan
from material_agent.retrieval.ranking import rank_and_publish
from material_agent.retrieval.reporting import build_report, report_to_markdown
from material_agent.retrieval.storage import LocalArtifactStore
from material_agent.retrieval.structures import (
    StructureValidationError,
    calculate_dimensionality,
    process_structure,
)


class RetrievalStageRunner:
    """Execute a deterministic, resumable retrieval stage."""

    def __init__(
        self,
        *,
        adapter: MaterialsSourceAdapter,
        artifact_store: LocalArtifactStore,
        policy: RetrievalPolicy | None = None,
    ) -> None:
        self.adapter = adapter
        self.store = artifact_store
        self.policy = policy or RetrievalPolicy()

    def validate_input(
        self, requirement: Any, stage_input: RetrievalStageInput
    ) -> StageInputValidation:
        errors: list[str] = []
        missing: list[str] = []
        remediation: list[str] = []

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

        return StageInputValidation(
            valid=not errors and not missing,
            missing_fields=missing,
            errors=errors,
            remediation=remediation,
        )

    def prepare(
        self,
        requirement: Any,
        stage_input: RetrievalStageInput,
        metadata: SourceMetadata | None = None,
    ) -> RetrievalQueryPlan:
        validation = self.validate_input(requirement, stage_input)
        if not validation.valid:
            raise QueryPlanningError(
                "; ".join([*validation.errors, *validation.missing_fields])
            )
        source_metadata = metadata or self.adapter.metadata()
        return build_query_plan(
            requirement,
            stage_input.requirement_hash,
            source_metadata,
            self.policy,
        )

    def run(
        self,
        requirement: Any,
        stage_input: RetrievalStageInput,
    ) -> StageResultEnvelope:
        started_at = datetime.now(UTC)
        stage_prefix = f"stages/agent01/{stage_input.run_id}"
        input_ref = self.store.write_json(
            f"{stage_prefix}/input_snapshot.json",
            {
                "stage_input": stage_input.model_dump(mode="json"),
                "requirement": requirement.model_dump(mode="json"),
            },
        )
        validation = self.validate_input(requirement, stage_input)
        if not validation.valid:
            messages = [*validation.errors, *validation.missing_fields]
            return self._write_early_result(
                stage_input=stage_input,
                stage_prefix=stage_prefix,
                input_uri=input_ref.uri,
                started_at=started_at,
                status=StageStatus.BLOCKED_MISSING_INPUT,
                error=ErrorRecord(
                    category="MISSING_INPUT",
                    retryable=False,
                    operation="validate_input",
                    public_message="; ".join(messages),
                ),
                warnings=validation.remediation,
                provenance={"policy_version": self.policy.policy_version},
            )

        try:
            metadata = self.adapter.metadata()
            plan = self.prepare(requirement, stage_input, metadata)
        except Exception as exc:
            category = (
                "API_SCHEMA_DRIFT"
                if isinstance(exc, QueryPlanningError)
                else "PERMANENT_CONFIGURATION"
            )
            return self._write_early_result(
                stage_input=stage_input,
                stage_prefix=stage_prefix,
                input_uri=input_ref.uri,
                started_at=started_at,
                status=StageStatus.PERMANENT_FAILED,
                error=ErrorRecord(
                    category=category,
                    retryable=False,
                    operation="prepare",
                    public_message=f"retrieval preparation failed ({type(exc).__name__})",
                ),
                warnings=[],
                provenance={"policy_version": self.policy.policy_version},
            )

        prior_plan_path = f"{stage_prefix}/query_plan.json"
        try:
            prior_plan = self.store.read_json(prior_plan_path)
        except FileNotFoundError:
            prior_plan = None
        if prior_plan is not None:
            conflict_fields = [
                field
                for field in (
                    "database_version",
                    "requirement_hash",
                    "policy_version",
                )
                if prior_plan.get(field) != getattr(plan, field)
            ]
            if conflict_fields:
                return self._write_early_result(
                    stage_input=stage_input,
                    stage_prefix=stage_prefix,
                    input_uri=input_ref.uri,
                    started_at=started_at,
                    status=StageStatus.PERMANENT_FAILED,
                    error=ErrorRecord(
                        category="BACKEND_INCONSISTENT",
                        retryable=False,
                        operation="resume",
                        public_message=(
                            "existing Stage Run cannot be resumed because its "
                            f"snapshot changed: {', '.join(conflict_fields)}"
                        ),
                    ),
                    warnings=[
                        "start a new run_id rather than mixing retrieval snapshots"
                    ],
                    provenance={
                        "policy_version": self.policy.policy_version,
                        "database_version": plan.database_version,
                    },
                )

        operation_path = (
            f"{stage_prefix}/operations/{plan.query_fingerprint}.result.json"
        )
        existing = self._load_valid_result(operation_path)
        if existing is not None:
            return existing

        output_artifacts = []
        warnings: list[str] = []
        errors: list[ErrorRecord] = []

        output_artifacts.append(
            self.store.write_json(
                f"{stage_prefix}/capability_snapshot.json",
                metadata.model_dump(mode="json"),
            )
        )
        output_artifacts.append(
            self.store.write_json(
                f"{stage_prefix}/query_plan.json", plan.model_dump(mode="json")
            )
        )

        try:
            documents = self._search_with_retry(plan)
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
                    "is_mock": False,
                },
                started_at=started_at,
                finished_at=datetime.now(UTC),
            )
            self.store.write_json(
                f"{stage_prefix}/stage_result.json", result.model_dump(mode="json")
            )
            return result

        raw_manifest: list[dict[str, Any]] = []
        for batch_index, start in enumerate(
            range(0, len(documents), plan.chunk_size), start=1
        ):
            batch = documents[start : start + plan.chunk_size]
            ref = self.store.write_gzip_jsonl(
                f"{stage_prefix}/raw_response_batches/batch-{batch_index:05d}.jsonl.gz",
                batch,
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
                f"{stage_prefix}/raw_response_manifest.jsonl", raw_manifest
            )
        )

        retrieved_at = datetime.now(UTC)
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
                    policy=self.policy,
                )
                source_ref = self.store.write_json(
                    f"candidates/structures/{processed.structure_id}.source.json",
                    processed.source_payload,
                )
                cif_ref = self.store.write_text(
                    f"candidates/structures/{processed.structure_id}.cif",
                    processed.cif_text,
                    "chemical/x-cif",
                )
                output_artifacts.extend([source_ref, cif_ref])
                candidate = normalize_candidate(
                    document,
                    project_id=stage_input.project_id,
                    query_plan=plan,
                    processed_structure=processed,
                    source_uri=source_ref.uri,
                    structure_uri=cif_ref.uri,
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
                )
                if dimensionality.error:
                    warnings.append(
                        f"{material_id}: dimensionality unavailable "
                        f"({dimensionality.error.split(':', 1)[0]})"
                    )
                candidates.append(candidate)
                structures[candidate.candidate_id] = processed.structure
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

        usable = [candidate for candidate in candidates if candidate.decision is not Decision.FAILED]
        task_ids, material_ids = collect_origin_task_ids(usable)
        resolved_task_metadata: dict[str, dict[str, Any]] = {}
        if task_ids:
            try:
                resolved_task_metadata, origin_warnings = (
                    self.adapter.resolve_task_metadata(
                        task_ids, material_ids, self.policy.origin_batch_size
                    )
                )
                warnings.extend(origin_warnings)
            except Exception as exc:
                warnings.append(
                    f"origin metadata resolution failed ({type(exc).__name__})"
                )

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
        scan_truncated = len(documents) >= plan.max_records_scanned
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

        output_artifacts.extend(
            [
                self.store.write_jsonl(
                    f"{stage_prefix}/origin_resolution.jsonl",
                    [
                        {"task_id": task_id, **metadata}
                        for task_id, metadata in sorted(resolved_task_metadata.items())
                    ],
                ),
                self.store.write_jsonl(
                    f"{stage_prefix}/candidate_audit.jsonl",
                    [candidate.model_dump(mode="json") for candidate in candidates],
                ),
                self.store.write_jsonl(
                    "candidates/candidate_manifest.jsonl",
                    [candidate.model_dump(mode="json") for candidate in published],
                ),
                self.store.write_jsonl(
                    f"{stage_prefix}/dedup_map.jsonl", exact_groups
                ),
                self.store.write_jsonl(
                    f"{stage_prefix}/structure_clusters.jsonl",
                    similarity_clusters,
                ),
            ]
        )

        report = build_report(
            query_plan=plan,
            candidates=candidates,
            raw_count=len(documents),
            scan_truncated=scan_truncated,
            status=status,
            warnings=warnings,
            exact_duplicate_groups=exact_groups,
            similarity_clusters=similarity_clusters,
        )
        report_json_ref = self.store.write_json(
            f"{stage_prefix}/retrieval_report.json", report
        )
        report_md_ref = self.store.write_text(
            f"{stage_prefix}/retrieval_report.md",
            report_to_markdown(report),
            "text/markdown",
        )
        output_artifacts.extend([report_json_ref, report_md_ref])

        finished_at = datetime.now(UTC)
        result = StageResultEnvelope(
            run_id=stage_input.run_id,
            status=status,
            input_snapshot_uri=input_ref.uri,
            output_artifacts=_unique_artifacts(output_artifacts),
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
                "is_mock": False,
            },
            started_at=started_at,
            finished_at=finished_at,
        )
        self.store.write_json(
            f"{stage_prefix}/stage_result.json", result.model_dump(mode="json")
        )
        self.store.write_json(operation_path, result.model_dump(mode="json"))
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
    ) -> StageResultEnvelope:
        result = StageResultEnvelope(
            run_id=stage_input.run_id,
            status=status,
            input_snapshot_uri=input_uri,
            output_artifacts=[],
            candidate_ids=[],
            warnings=warnings,
            errors=[error],
            metrics={},
            provenance=provenance,
            started_at=started_at,
            finished_at=datetime.now(UTC),
        )
        self.store.write_json(
            f"{stage_prefix}/stage_result.json", result.model_dump(mode="json")
        )
        return result

    def _search_with_retry(self, plan: RetrievalQueryPlan) -> list[dict[str, Any]]:
        last_error: Exception | None = None
        for attempt in range(1, self.policy.max_attempts + 1):
            try:
                return self.adapter.search(plan)
            except Exception as exc:
                last_error = exc
                if attempt >= self.policy.max_attempts or not _is_retryable(exc):
                    raise
                delay = self.policy.retry_base_seconds * (2 ** (attempt - 1))
                delay += random.uniform(0, max(delay * 0.1, 0.001))
                time.sleep(delay)
        raise RuntimeError("search attempts exhausted") from last_error

    def _load_valid_result(self, operation_path: str) -> StageResultEnvelope | None:
        try:
            payload = self.store.read_json(operation_path)
            result = StageResultEnvelope.model_validate(payload)
        except (FileNotFoundError, ValueError):
            return None
        if all(
            self.store.exists_with_hash(ref.uri, ref.sha256)
            for ref in result.output_artifacts
        ):
            return result
        return None


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


def _unique_artifacts(artifacts):
    by_uri = {artifact.uri: artifact for artifact in artifacts}
    return [by_uri[uri] for uri in sorted(by_uri)]


def _distribution_version(name: str) -> str:
    try:
        return importlib.metadata.version(name)
    except importlib.metadata.PackageNotFoundError:
        return "unknown"
