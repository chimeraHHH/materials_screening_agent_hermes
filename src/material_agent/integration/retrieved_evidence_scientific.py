"""Typed retrieved-evidence import and DeepSeek scientific fusion executors.

These nodes are deliberately computation-light.  They turn an existing,
hash-bound database/literature research bundle into probabilistic screening
evidence without running DFT or pretending that model reasoning is a property
proof.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Literal

from pydantic import Field, model_validator

from material_agent.inspiration.deepseek_agent import (
    DeepSeekAgentBudgetV1,
    DeepSeekFunctionTool,
    DeepSeekThinkingAgent,
)
from material_agent.inspiration.models import (
    ArtifactPointerV1,
    Identifier,
    StrictModel,
    canonical_json_bytes,
)
from material_agent.integration.scientific_executors import artifact_pointer_from_ref
from material_agent.integration.scientific_loop import (
    DeepSeekReasoningProvenance,
    ModelExecutionReceipt,
    ScientificArtifactKind,
    ScientificEvidenceVerdict,
    ScientificTaskKind,
    TaskExecutionBinding,
    make_scientific_task_artifact,
    reasoning_provenance,
)
from material_agent.orchestrator.llm import SecretResolver
from material_agent.retrieval.storage import LocalArtifactStore

EVIDENCE_FUSION_PROMPT_VERSION = "retrieved-evidence-fusion-deepseek-v1"


class RetrievedConstraint(StrictModel):
    constraint_id: Identifier
    statement: str = Field(min_length=10, max_length=4_000)
    hard: bool
    threshold_value: float | None = None
    threshold_unit: str | None = Field(default=None, max_length=64)


class RetrievedDatabaseRecord(StrictModel):
    database_candidate_id: Identifier
    source_database: Identifier
    source_material_id: str = Field(min_length=1, max_length=512)
    formula: str = Field(min_length=1, max_length=256)
    dimensionality: int | None = Field(default=None, ge=0, le=3)
    band_gap_ev: float | None = None
    energy_above_hull_ev_atom: float | None = None
    structure_sha256: str | None = Field(
        default=None, pattern=r"^[0-9a-f]{64}$"
    )


class RetrievedLiteratureRecord(StrictModel):
    evidence_id: Identifier
    evidence_scope: Identifier
    title: str | None = Field(default=None, max_length=2_000)
    doi: str | None = Field(default=None, max_length=512)
    published_year: int | None = Field(default=None, ge=1800, le=2200)
    excerpt: str = Field(min_length=1, max_length=20_000)


class RetrievedConstraintAssessment(StrictModel):
    constraint_id: Identifier
    verdict: Literal["PASS", "FAIL", "UNKNOWN"]
    rationale: str = Field(min_length=10, max_length=8_000)
    evidence_ids: tuple[Identifier, ...] = Field(default=(), max_length=128)
    database_candidate_ids: tuple[Identifier, ...] = Field(
        default=(), max_length=64
    )

    @model_validator(mode="after")
    def canonical_references(self) -> RetrievedConstraintAssessment:
        for name in ("evidence_ids", "database_candidate_ids"):
            values = getattr(self, name)
            if values != tuple(sorted(set(values))):
                raise ValueError(f"{name} must be sorted and unique")
        return self


class PriorReasonedConstraintAssessment(StrictModel):
    constraint_id: Identifier
    predicted_verdict: Literal["LIKELY_PASS", "LIKELY_FAIL", "UNCERTAIN"]
    probability_pass: float = Field(ge=0, le=1)
    scientific_rationale: str = Field(min_length=10, max_length=8_000)


class RetrievedScientificEvidenceBundle(StrictModel):
    schema_version: Literal["retrieved-scientific-evidence-bundle-v1"] = (
        "retrieved-scientific-evidence-bundle-v1"
    )
    candidate_id: Identifier
    material_name: str = Field(min_length=1, max_length=512)
    formula: str | None = Field(default=None, max_length=256)
    hypothesis: str = Field(min_length=10, max_length=8_000)
    mechanism: str = Field(min_length=10, max_length=8_000)
    source_graph_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    constraints: tuple[RetrievedConstraint, ...] = Field(min_length=1, max_length=128)
    database_records: tuple[RetrievedDatabaseRecord, ...] = Field(
        default=(), max_length=128
    )
    literature_records: tuple[RetrievedLiteratureRecord, ...] = Field(
        default=(), max_length=256
    )
    deterministic_assessments: tuple[RetrievedConstraintAssessment, ...] = Field(
        default=(), max_length=128
    )
    prior_reasoned_assessments: tuple[
        PriorReasonedConstraintAssessment, ...
    ] = Field(default=(), max_length=128)
    evidence_boundary: Literal["RETRIEVED_AND_REASONED_SCREENING_NOT_PROPERTY_PROOF"] = (
        "RETRIEVED_AND_REASONED_SCREENING_NOT_PROPERTY_PROOF"
    )
    scientific_conclusion: Literal[False] = False

    @model_validator(mode="after")
    def validate_references(self) -> RetrievedScientificEvidenceBundle:
        keyed_sequences = (
            ("constraints", self.constraints, "constraint_id"),
            ("database_records", self.database_records, "database_candidate_id"),
            ("literature_records", self.literature_records, "evidence_id"),
            (
                "deterministic_assessments",
                self.deterministic_assessments,
                "constraint_id",
            ),
            (
                "prior_reasoned_assessments",
                self.prior_reasoned_assessments,
                "constraint_id",
            ),
        )
        for name, values, key in keyed_sequences:
            observed = tuple(getattr(item, key) for item in values)
            if observed != tuple(sorted(set(observed))):
                raise ValueError(f"{name} must be sorted and uniquely keyed")
        constraint_ids = {item.constraint_id for item in self.constraints}
        evidence_ids = {item.evidence_id for item in self.literature_records}
        database_ids = {
            item.database_candidate_id for item in self.database_records
        }
        for item in self.deterministic_assessments:
            if item.constraint_id not in constraint_ids:
                raise ValueError("assessment references an unknown constraint")
            if not set(item.evidence_ids) <= evidence_ids:
                raise ValueError("assessment references unavailable literature evidence")
            if not set(item.database_candidate_ids) <= database_ids:
                raise ValueError("assessment references unavailable database records")
        if not {
            item.constraint_id for item in self.prior_reasoned_assessments
        } <= constraint_ids:
            raise ValueError("prior reasoning references an unknown constraint")
        return self


class RetrievedEvidenceImportParameters(StrictModel):
    bundle_pointer: ArtifactPointerV1


class DeepSeekEvidenceFusionParameters(StrictModel):
    target_constraint_ids: tuple[Identifier, ...] = Field(min_length=1, max_length=128)
    pass_probability_threshold: float = Field(default=0.7, gt=0.5, le=1)
    fail_probability_threshold: float = Field(default=0.3, ge=0, lt=0.5)
    maximum_next_actions: int = Field(default=5, ge=1, le=16)

    @model_validator(mode="after")
    def validate_thresholds(self) -> DeepSeekEvidenceFusionParameters:
        if self.target_constraint_ids != tuple(
            sorted(set(self.target_constraint_ids))
        ):
            raise ValueError("target constraints must be sorted and unique")
        if self.fail_probability_threshold >= self.pass_probability_threshold:
            raise ValueError("failure threshold must be below pass threshold")
        return self


class ReasonedConstraintAssessment(StrictModel):
    constraint_id: Identifier
    predicted_verdict: Literal["LIKELY_PASS", "LIKELY_FAIL", "UNCERTAIN"]
    probability_pass: float = Field(ge=0, le=1)
    evidence_ids: tuple[Identifier, ...] = Field(default=(), max_length=128)
    rationale: str = Field(min_length=10, max_length=8_000)
    decisive_missing_evidence: str | None = Field(default=None, max_length=4_000)

    @model_validator(mode="after")
    def canonical_evidence(self) -> ReasonedConstraintAssessment:
        if self.evidence_ids != tuple(sorted(set(self.evidence_ids))):
            raise ValueError("reasoned evidence IDs must be sorted and unique")
        return self


class DeepSeekEvidenceFusionDraft(StrictModel):
    assessments: tuple[ReasonedConstraintAssessment, ...] = Field(
        min_length=1, max_length=128
    )
    joint_candidate_score: float = Field(ge=0, le=1)
    recommended_action: Literal["RETAIN", "ELIMINATE", "MODIFY_OPERATOR"]
    next_low_cost_actions: tuple[str, ...] = Field(default=(), max_length=16)
    summary: str = Field(min_length=10, max_length=8_000)

    @model_validator(mode="after")
    def canonical_assessments(self) -> DeepSeekEvidenceFusionDraft:
        ids = tuple(item.constraint_id for item in self.assessments)
        if ids != tuple(sorted(set(ids))):
            raise ValueError("fusion assessments must be sorted and uniquely keyed")
        if len(self.next_low_cost_actions) != len(set(self.next_low_cost_actions)):
            raise ValueError("next actions must be unique")
        return self


class DeepSeekEvidenceFusionResult(DeepSeekEvidenceFusionDraft):
    schema_version: Literal["deepseek-evidence-fusion-result-v1"] = (
        "deepseek-evidence-fusion-result-v1"
    )
    candidate_id: Identifier
    source_bundle_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    provenance: DeepSeekReasoningProvenance
    evidence_boundary: Literal["PROBABILISTIC_SCREENING_NOT_PROPERTY_PROOF"] = (
        "PROBABILISTIC_SCREENING_NOT_PROPERTY_PROOF"
    )
    scientific_conclusion: Literal[False] = False


class _InspectEvidenceBundleArgs(StrictModel):
    pass


@dataclass(frozen=True)
class RetrievedEvidenceImportExecutor:
    artifact_store: LocalArtifactStore
    executor_id: str = "retrieved-evidence-import-executor-v1"

    def execute(self, *, binding: TaskExecutionBinding) -> ModelExecutionReceipt:
        task = binding.proposed_task
        if task.task_kind is not ScientificTaskKind.RESEARCH_EVIDENCE_IMPORT:
            raise ValueError("evidence importer received an incompatible task")
        if task.produced_artifact_kinds != (
            ScientificArtifactKind.RETRIEVED_EVIDENCE_BUNDLE,
        ):
            raise ValueError("evidence importer requires its exact typed output")
        parameters = RetrievedEvidenceImportParameters.model_validate_json(
            canonical_json_bytes(task.parameters)
        )
        observed = self.artifact_store.inspect(
            parameters.bundle_pointer.uri,
            media_type="application/json",
        )
        if (
            observed.sha256 != parameters.bundle_pointer.sha256
            or (
                parameters.bundle_pointer.size_bytes is not None
                and observed.size_bytes != parameters.bundle_pointer.size_bytes
            )
        ):
            raise ValueError("retrieved evidence bundle failed Artifact integrity")
        bundle = RetrievedScientificEvidenceBundle.model_validate_json(
            canonical_json_bytes(
                self.artifact_store.read_json(parameters.bundle_pointer.uri)
            )
        )
        if bundle.candidate_id != task.candidate_id:
            raise ValueError("retrieved evidence bundle candidate mismatch")
        artifact = make_scientific_task_artifact(
            candidate_id=task.candidate_id,
            producer_task_id=task.task_id,
            kind=ScientificArtifactKind.RETRIEVED_EVIDENCE_BUNDLE,
            pointer=parameters.bundle_pointer,
            is_mock=False,
        )
        return ModelExecutionReceipt(
            task_id=task.task_id,
            status="SUCCEEDED",
            verdict=ScientificEvidenceVerdict.INCONCLUSIVE,
            tested_claim_ids=task.requested_observables,
            evidence_level=task.required_evidence_level,
            result_artifact=parameters.bundle_pointer,
            consumed_artifact_ids=(),
            produced_artifacts=(artifact,),
            runtime_provenance={
                "executor_id": self.executor_id,
                "source_bundle_sha256": parameters.bundle_pointer.sha256,
            },
            reason_codes=("HASH_VERIFIED_RETRIEVED_EVIDENCE_IMPORTED",),
            real_execution=True,
        )


@dataclass(frozen=True)
class DeepSeekEvidenceFusionExecutor:
    artifact_store: LocalArtifactStore
    secret_resolver: SecretResolver
    reasoning_effort: Literal["high", "max"] = "high"
    budget: DeepSeekAgentBudgetV1 | None = None
    agent_factory: Callable[..., DeepSeekThinkingAgent] = DeepSeekThinkingAgent
    live_call: bool = True
    executor_id: str = "deepseek-evidence-fusion-executor-v1"

    def execute(self, *, binding: TaskExecutionBinding) -> ModelExecutionReceipt:
        task = binding.proposed_task
        if task.task_kind is not ScientificTaskKind.DEEPSEEK_EVIDENCE_FUSION:
            raise ValueError("evidence fusion executor received an incompatible task")
        if task.produced_artifact_kinds != (
            ScientificArtifactKind.REASONED_PROPERTY_ASSESSMENT,
        ):
            raise ValueError("evidence fusion requires its exact typed output")
        parameters = DeepSeekEvidenceFusionParameters.model_validate_json(
            canonical_json_bytes(task.parameters)
        )
        bundle_artifacts = tuple(
            item
            for item in binding.consumed_artifacts
            if item.kind is ScientificArtifactKind.RETRIEVED_EVIDENCE_BUNDLE
        )
        if len(bundle_artifacts) != 1:
            raise ValueError("evidence fusion requires exactly one retrieved bundle")
        bundle_artifact = bundle_artifacts[0]
        bundle = RetrievedScientificEvidenceBundle.model_validate_json(
            canonical_json_bytes(
                self.artifact_store.read_json(bundle_artifact.pointer.uri)
            )
        )
        if bundle.candidate_id != task.candidate_id:
            raise ValueError("evidence fusion bundle candidate mismatch")
        target_ids = set(parameters.target_constraint_ids)
        if target_ids != set(task.requested_observables):
            raise ValueError("fusion parameters and requested observables differ")
        if not target_ids <= {item.constraint_id for item in bundle.constraints}:
            raise ValueError("fusion requests a constraint absent from the bundle")

        def inspect(_arguments: _InspectEvidenceBundleArgs) -> Mapping[str, object]:
            return bundle.model_dump(mode="json")

        agent = self.agent_factory(
            secret_resolver=self.secret_resolver,
            tools=(
                DeepSeekFunctionTool(
                    name="inspect_retrieved_evidence_bundle",
                    description=(
                        "Read the candidate-specific, hash-bound database, literature, "
                        "counter-evidence and prior assessment bundle."
                    ),
                    arguments_model=_InspectEvidenceBundleArgs,
                    handler=inspect,
                    max_calls_per_run=1,
                ),
            ),
            budget=self.budget
            or DeepSeekAgentBudgetV1(
                max_rounds=4,
                max_tool_calls=2,
                max_completion_tokens_per_round=32_768,
                max_total_tokens=80_000,
                max_walltime_seconds=900,
            ),
            reasoning_effort=self.reasoning_effort,
            timeout_seconds=900,
        )
        result = agent.run(
            system_prompt=(
                "You are a materials evidence-fusion scientist. Call the inspection "
                "tool exactly once. For every requested hard constraint, infer a "
                "calibrated probability of passing from only the returned database "
                "records, literature excerpts, deterministic assessments and prior "
                "reasoned assessments. Resolve scientific ambiguity with mechanistic "
                "reasoning, but cite only evidence IDs present in the tool result. "
                "Distinguish experimental Curie temperature from Neel, blocking, "
                "Curie-Weiss, mean-field and analogue values. Distinguish magnetic "
                "Chern/topological-insulator evidence from nonmagnetic Z2 or mere band "
                "inversion. A metallic database gap is decisive against a global "
                "insulator unless candidate-specific evidence shows the structure or "
                "state differs. Prefer a useful probabilistic conclusion over UNKNOWN, "
                "while keeping weakly grounded cases near 0.5. Do not request or assume "
                "DFT. Recommended next actions must be database retrieval, literature "
                "retrieval, a registered ML model, structure/operator revision, or "
                "human/experimental review. Do not claim property verification. Return "
                "one assessment for every requested constraint in sorted order."
            ),
            user_payload={
                "candidate_id": task.candidate_id,
                "fail_probability_threshold": parameters.fail_probability_threshold,
                "maximum_next_actions": parameters.maximum_next_actions,
                "pass_probability_threshold": parameters.pass_probability_threshold,
                "required_output_schema": DeepSeekEvidenceFusionDraft.model_json_schema(),
                "target_constraint_ids": list(parameters.target_constraint_ids),
            },
            prompt_version=EVIDENCE_FUSION_PROMPT_VERSION,
            final_model=DeepSeekEvidenceFusionDraft,
            require_tool_call=True,
        )
        final = result.final
        observed_ids = {item.constraint_id for item in final.assessments}
        if observed_ids != target_ids:
            raise ValueError("DeepSeek fusion did not assess every frozen constraint")
        available_evidence = {item.evidence_id for item in bundle.literature_records}
        if any(
            not set(item.evidence_ids) <= available_evidence
            for item in final.assessments
        ):
            raise ValueError("DeepSeek fusion cited evidence outside the bundle")
        hard_ids = {
            item.constraint_id for item in bundle.constraints if item.hard
        } & target_ids
        hard_failures = tuple(
            sorted(
                item.constraint_id
                for item in final.assessments
                if item.constraint_id in hard_ids
                and item.probability_pass <= parameters.fail_probability_threshold
            )
        )
        all_hard_pass = bool(hard_ids) and all(
            item.probability_pass >= parameters.pass_probability_threshold
            for item in final.assessments
            if item.constraint_id in hard_ids
        )
        if hard_failures:
            verdict = ScientificEvidenceVerdict.CONTRADICTS
            reason_codes = ("REASONED_HARD_CONSTRAINT_FAILURE",)
        elif all_hard_pass:
            verdict = ScientificEvidenceVerdict.SUPPORTS
            reason_codes = ("REASONED_ALL_HARD_CONSTRAINTS_LIKELY_PASS",)
        else:
            verdict = ScientificEvidenceVerdict.INCONCLUSIVE
            reason_codes = ("REASONED_MIXED_OR_BORDERLINE_EVIDENCE",)
        fusion = DeepSeekEvidenceFusionResult(
            candidate_id=task.candidate_id,
            source_bundle_sha256=bundle_artifact.pointer.sha256,
            provenance=reasoning_provenance(result.receipt, live_call=self.live_call),
            **final.model_dump(mode="python"),
        )
        reference = self.artifact_store.write_json(
            (
                f"scientific_loop/tasks/{task.task_id}/{binding.binding_id}/"
                "deepseek-evidence-fusion.json"
            ),
            fusion.model_dump(mode="json"),
            immutable=True,
        )
        pointer = artifact_pointer_from_ref(reference)
        artifact = make_scientific_task_artifact(
            candidate_id=task.candidate_id,
            producer_task_id=task.task_id,
            kind=ScientificArtifactKind.REASONED_PROPERTY_ASSESSMENT,
            pointer=pointer,
            is_mock=False,
        )
        return ModelExecutionReceipt(
            task_id=task.task_id,
            status="SUCCEEDED",
            verdict=verdict,
            tested_claim_ids=task.requested_observables,
            evidence_level=task.required_evidence_level,
            result_artifact=pointer,
            consumed_artifact_ids=tuple(
                item.artifact_id for item in binding.consumed_artifacts
            ),
            produced_artifacts=(artifact,),
            runtime_provenance={
                "executor_id": self.executor_id,
                "model_id": "deepseek-v4-pro",
                "prompt_version": EVIDENCE_FUSION_PROMPT_VERSION,
                "receipt_sha256": fusion.provenance.receipt_sha256,
                "source_bundle_sha256": bundle_artifact.pointer.sha256,
            },
            reason_codes=reason_codes,
            real_execution=True,
        )
