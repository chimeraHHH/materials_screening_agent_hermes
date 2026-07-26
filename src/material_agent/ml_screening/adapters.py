"""Lightweight model protocol and deterministic Step-1 test doubles."""

from __future__ import annotations

import hashlib
from collections import Counter
from typing import Protocol, runtime_checkable

from material_agent.ml_screening.evidence import (
    build_structure_lineage,
    decision_for_ml_result,
    evidence_for_ml_result,
    recommended_downstream_structure_id,
)
from material_agent.ml_screening.models import (
    ApplicabilityStatus,
    ArtifactPointer,
    EvidenceLevel,
    ExecutionStatus,
    FakeStageArtifacts,
    MLCandidateManifestRecord,
    MLCandidateResult,
    MLDecision,
    MLExecutionIdentity,
    MLModelSpec,
    MLPropertyValue,
    MLRelaxationResult,
    MLScreeningPolicy,
    MLStagePlan,
    ModelHealthSnapshot,
    RelaxationRequest,
    RelaxationStatus,
    SelectionStatus,
    StaticPredictionRequest,
    StaticPredictionResult,
    WorkerInputArtifact,
    WorkerLimits,
    WorkerRequest,
    WorkerResponse,
    WorkerResponseStatus,
)
from material_agent.ml_screening.reporting import render_fake_report


@runtime_checkable
class MLModelAdapter(Protocol):
    def describe(self) -> MLModelSpec: ...

    def healthcheck(self, device: str) -> ModelHealthSnapshot: ...

    def predict_static(
        self,
        structure,
        request: StaticPredictionRequest,
    ) -> StaticPredictionResult: ...

    def relax(
        self,
        structure,
        request: RelaxationRequest,
    ) -> MLRelaxationResult: ...


class FakeMLModelAdapter:
    """Deterministic test double that is structurally unable to claim L2."""

    adapter_version = "agent02-fake-adapter-v1"

    def __init__(
        self,
        *,
        model: MLModelSpec,
        health: ModelHealthSnapshot,
    ) -> None:
        if not model.is_mock or not health.is_mock:
            raise ValueError("FakeMLModelAdapter requires mock model metadata")
        if model.model_id != health.model_id:
            raise ValueError("fake model and health snapshot differ")
        if model.checkpoint_sha256 != health.checkpoint_sha256:
            raise ValueError("fake model and health checkpoint differ")
        if model.package_lock_sha256 != health.package_lock_sha256:
            raise ValueError("fake model and health package lock differ")
        if model.adapter_version != health.adapter_version:
            raise ValueError("fake model and health adapter versions differ")
        self._model = model
        self._health = health
        self.calls: Counter[str] = Counter()

    def describe(self) -> MLModelSpec:
        self.calls["describe"] += 1
        return self._model

    def healthcheck(self, device: str) -> ModelHealthSnapshot:
        self.calls["healthcheck"] += 1
        if device != self._health.device_policy:
            raise ValueError(f"fake device is not available: {device}")
        return self._health

    def execution_identity(self) -> MLExecutionIdentity:
        return self._health.execution_identity()

    def predict_static(
        self,
        structure,
        request: StaticPredictionRequest,
    ) -> StaticPredictionResult:
        self.calls["predict_static"] += 1
        if structure != request.structure:
            raise ValueError("static request structure differs from argument")
        if request.model_id != self._model.model_id:
            raise ValueError("static request targets a different model")
        energy = _fake_energy_ev_atom(request.candidate_id)
        properties = [
            self._property(
                request,
                "mlip_potential_energy",
                energy,
                "eV/atom",
            ),
            self._property(
                request,
                "maximum_force",
                0.2,
                "eV/angstrom",
            ),
            self._property(
                request,
                "stress",
                [[0.0, 0.0, 0.0]] * 3,
                "GPa",
            ),
        ]
        return StaticPredictionResult(
            candidate_id=request.candidate_id,
            properties=properties,
            is_mock=True,
            warnings=["synthetic fixture values; no scientific inference"],
        )

    def relax(
        self,
        structure,
        request: RelaxationRequest,
    ) -> MLRelaxationResult:
        self.calls["relax"] += 1
        if structure != request.structure:
            raise ValueError("relaxation request structure differs from argument")
        if request.model_id != self._model.model_id:
            raise ValueError("relaxation request targets a different model")
        num_sites = structure.num_sites or 1
        initial_ev_atom = _fake_energy_ev_atom(request.candidate_id)
        final_ev_atom = initial_ev_atom - 0.01
        output_id = (
            "mock_struct_"
            + hashlib.sha256(
                f"{request.candidate_id}:{structure.sha256}".encode()
            ).hexdigest()[:20]
        )
        output_uri = f"artifact://fixtures/agent02/{output_id}.json"
        output_sha = hashlib.sha256(output_uri.encode()).hexdigest()
        return MLRelaxationResult(
            candidate_id=request.candidate_id,
            input_structure_id=structure.structure_id,
            input_structure_uri=structure.uri,
            input_structure_sha256=structure.sha256,
            output_structure_id=output_id,
            output_structure_uri=output_uri,
            output_structure_sha256=output_sha,
            model_id=self._model.model_id,
            checkpoint_sha256=self._model.checkpoint_sha256,
            adapter_version=self.adapter_version,
            execution_identity=self.execution_identity(),
            device=self._health.device_policy,
            relaxation_profile=request.relaxation_profile,
            status=RelaxationStatus.CONVERGED,
            num_steps=5,
            initial_energy_ev=initial_ev_atom * num_sites,
            final_energy_ev=final_ev_atom * num_sites,
            initial_energy_ev_atom=initial_ev_atom,
            final_energy_ev_atom=final_ev_atom,
            delta_energy_ev_atom=-0.01,
            initial_max_force_ev_angstrom=0.2,
            final_max_force_ev_angstrom=0.05,
            final_stress_gpa_3x3=[[0.0, 0.0, 0.0]] * 3,
            magmom_summary={"maximum_absolute_mu_b": 0.0},
            structure_drift={
                "volume_ratio": 1.0,
                "structure_match": True,
            },
            qc_passed=True,
            warnings=["synthetic fixture relaxation; no scientific inference"],
            wall_time_seconds=0.0,
            is_mock=True,
            provenance={
                "is_mock": True,
                "adapter": self.adapter_version,
            },
        )

    def _property(
        self,
        request: StaticPredictionRequest,
        name: str,
        value,
        unit: str,
    ) -> MLPropertyValue:
        return MLPropertyValue(
            property_name=name,
            value=value,
            unit=unit,
            evidence_level=EvidenceLevel.L1_RETRIEVED,
            method="deterministic fixture",
            model_id=self._model.model_id,
            checkpoint_sha256=self._model.checkpoint_sha256,
            input_structure_id=request.structure.structure_id,
            is_mock=True,
            provenance={
                "is_mock": True,
                "adapter": self.adapter_version,
            },
        )


class FakeMLWorker:
    """In-process stand-in for the later JSON worker boundary."""

    def __init__(
        self,
        *,
        adapter: FakeMLModelAdapter,
        policy: MLScreeningPolicy,
        fail_once_candidate_ids: set[str] | None = None,
    ) -> None:
        self.adapter = adapter
        self.policy = policy
        self.fail_once_candidate_ids = set(fail_once_candidate_ids or ())
        self._failed_once: set[str] = set()

    def handshake(self) -> MLExecutionIdentity:
        self.adapter.describe()
        self.adapter.healthcheck("fixture")
        return self.adapter.execution_identity()

    def run(self, request: WorkerRequest) -> WorkerResponse:
        if (
            request.candidate_id in self.fail_once_candidate_ids
            and request.candidate_id not in self._failed_once
        ):
            self._failed_once.add(request.candidate_id)
            raise RuntimeError(
                f"injected worker crash for {request.candidate_id}"
            )
        actual = self.handshake()
        if request.expected_handshake != actual:
            raise ValueError("fake worker handshake does not match adapter")
        plan = request.plan
        if plan.execution_identity != actual:
            raise ValueError("plan execution identity does not match worker")
        planned = next(
            (
                item
                for item in plan.planned_candidates
                if item.candidate.candidate_id == request.candidate_id
            ),
            None,
        )
        if planned is None:
            raise ValueError("worker candidate is absent from the plan")
        record = self._candidate_record(planned, plan)
        response = WorkerResponse(
            actual_handshake=actual,
            candidate_id=request.candidate_id,
            candidate_operation_key=request.candidate_operation_key,
            status=WorkerResponseStatus.SUCCEEDED,
            candidate_result=record.candidate,
            produced_artifacts=[],
            warnings=[
                "Fake Worker used; result remains L1_RETRIEVED."
            ],
        )
        response.validate_against_request(request)
        return response

    def generate_artifacts(self, plan: MLStagePlan) -> FakeStageArtifacts:
        return FakeWorkerCoordinator(worker=self).execute(plan)

    def request_for_candidate(
        self,
        plan: MLStagePlan,
        candidate_id: str,
        *,
        input_root_relative_path: str | None = None,
        input_size_bytes: int = 0,
    ) -> WorkerRequest:
        planned = next(
            item
            for item in plan.planned_candidates
            if item.candidate.candidate_id == candidate_id
        )
        structure = planned.candidate.source_structure
        return WorkerRequest(
            expected_handshake=self.handshake(),
            plan=plan,
            candidate_id=candidate_id,
            candidate_operation_key=plan.candidate_operation_keys[
                candidate_id
            ],
            inputs=[
                WorkerInputArtifact(
                    artifact_uri=structure.uri,
                    root_relative_path=(
                        input_root_relative_path
                        or f"inputs/{candidate_id}.cif"
                    ),
                    sha256=structure.sha256,
                    size_bytes=input_size_bytes,
                )
            ],
            output_sandbox_relative_path=(
                "stages/agent02/candidate-operations/"
                f"{plan.candidate_operation_keys[candidate_id]}/worker-output"
            ),
            limits=WorkerLimits(
                wall_time_seconds=300,
                max_stdout_bytes=1_000_000,
                max_single_artifact_bytes=100_000_000,
                max_total_output_bytes=250_000_000,
            ),
        )

    def _candidate_record(
        self,
        planned,
        plan: MLStagePlan,
    ) -> MLCandidateManifestRecord:
        candidate = planned.candidate
        selected = planned.selection_status is SelectionStatus.SELECTED
        prediction = None
        relaxation = None
        lineage = None
        properties = []
        warnings = list(planned.applicability.warnings)

        if selected and plan.allow_real_inference:
            prediction = self.adapter.predict_static(
                candidate.source_structure,
                StaticPredictionRequest(
                    candidate_id=candidate.candidate_id,
                    structure=candidate.source_structure,
                    model_id=plan.model_id,
                    requested_properties=[
                        "mlip_potential_energy",
                        "maximum_force",
                        "stress",
                        "site_magnetic_moments",
                    ],
                ),
            )
            relaxation = self.adapter.relax(
                candidate.source_structure,
                RelaxationRequest(
                    candidate_id=candidate.candidate_id,
                    structure=candidate.source_structure,
                    model_id=plan.model_id,
                    relaxation_profile=plan.relaxation_profile,
                    device_policy=plan.device_policy,
                ),
            )
            properties = prediction.properties
            warnings.extend(prediction.warnings)
            warnings.extend(relaxation.warnings)
            model = self.adapter.describe()
            lineage = build_structure_lineage(
                candidate=candidate,
                output_structure_id=relaxation.output_structure_id or "",
                output_structure=ArtifactPointer(
                    uri=relaxation.output_structure_uri or "",
                    sha256=relaxation.output_structure_sha256
                    or ("0" * 64),
                ),
                model=model,
                policy=self.policy,
                is_mock=True,
            )
            execution = ExecutionStatus.MOCK_COMPLETED
            decision = decision_for_ml_result(
                is_mock=True,
                applicability=planned.applicability.status,
                qc_passed=True,
            )
            evidence = evidence_for_ml_result(
                is_mock=True,
                applicability=planned.applicability.status,
                qc_passed=True,
            )
        elif selected:
            execution = ExecutionStatus.DRY_RUN
            decision = MLDecision.UNCERTAIN
            evidence = EvidenceLevel.L1_RETRIEVED
            warnings.append("real inference disabled by stage request")
        else:
            execution = ExecutionStatus.NOT_RUN
            decision = (
                MLDecision.REJECT
                if planned.pre_filter.decision is MLDecision.REJECT
                else (
                    MLDecision.FAILED
                    if planned.pre_filter.decision is MLDecision.FAILED
                    else MLDecision.UNCERTAIN
                )
            )
            evidence = EvidenceLevel.L1_RETRIEVED

        recommended = recommended_downstream_structure_id(
            candidate=candidate,
            decision=decision,
            evidence_level=evidence,
            output_structure_id=(
                relaxation.output_structure_id if relaxation else None
            ),
        )
        result = MLCandidateResult(
            project_id=plan.project_id,
            run_id=plan.run_id,
            candidate_id=candidate.candidate_id,
            candidate_operation_key=(
                plan.candidate_operation_keys[candidate.candidate_id]
                if selected
                else None
            ),
            upstream_manifest_uri=candidate.upstream_manifest_uri,
            upstream_manifest_sha256=candidate.upstream_manifest_sha256,
            source_structure_id=candidate.source_structure.structure_id,
            source_structure_uri=candidate.source_structure.uri,
            source_structure_sha256=candidate.source_structure.sha256,
            pre_filter_decision=planned.pre_filter.decision,
            applicability=planned.applicability,
            selection_status=planned.selection_status,
            execution_status=execution,
            decision=decision,
            evidence_level=evidence,
            execution_identity=plan.execution_identity,
            ml_properties=properties,
            relaxation_result=relaxation,
            structure_lineage=lineage,
            recommended_downstream_structure_id=recommended,
            warnings=warnings,
        )
        return MLCandidateManifestRecord(
            candidate=result,
            scientific_rank=candidate.publication_rank,
        )


class FakeWorkerCoordinator:
    """Adapter-side finalizer used to prove one-candidate commit boundaries.

    The Fake Worker returns an untrusted response.  This coordinator validates
    it and records a candidate completion only after the response is accepted.
    The worker itself never receives or writes the ledger.
    """

    def __init__(
        self,
        *,
        worker: FakeMLWorker,
        completion_ledger: dict[str, MLCandidateResult] | None = None,
    ) -> None:
        self.worker = worker
        self.completion_ledger = (
            completion_ledger if completion_ledger is not None else {}
        )
        self.worker_invocations: Counter[str] = Counter()

    def execute(self, plan: MLStagePlan) -> FakeStageArtifacts:
        responses: list[WorkerResponse] = []
        records: list[MLCandidateManifestRecord] = []
        for planned in plan.planned_candidates:
            candidate_id = planned.candidate.candidate_id
            operation_key = plan.candidate_operation_keys.get(candidate_id)
            if (
                planned.selection_status is SelectionStatus.SELECTED
                and plan.allow_real_inference
            ):
                assert operation_key is not None
                result = self.completion_ledger.get(operation_key)
                if result is None:
                    request = self.worker.request_for_candidate(
                        plan,
                        candidate_id,
                    )
                    self.worker_invocations[candidate_id] += 1
                    response = self.worker.run(request)
                    response.validate_against_request(request)
                    assert response.candidate_result is not None
                    result = response.candidate_result
                    self.completion_ledger[operation_key] = result
                    responses.append(response)
                records.append(
                    MLCandidateManifestRecord(
                        candidate=result,
                        scientific_rank=planned.candidate.publication_rank,
                    )
                )
            else:
                records.append(
                    self.worker._candidate_record(planned, plan)
                )
        records = _assign_readiness_ranks(records)
        return FakeStageArtifacts(
            plan=plan,
            manifest=records,
            report_markdown=render_fake_report(plan, records),
            worker_responses=responses,
        )


def _assign_readiness_ranks(
    records: list[MLCandidateManifestRecord],
) -> list[MLCandidateManifestRecord]:
    ordered = sorted(records, key=_readiness_key)
    rank_by_id = {
        record.candidate.candidate_id: index
        for index, record in enumerate(ordered, start=1)
    }
    return [
        record.model_copy(
            update={
                "downstream_readiness_rank": rank_by_id[
                    record.candidate.candidate_id
                ]
            }
        )
        for record in records
    ]


def _readiness_key(
    record: MLCandidateManifestRecord,
) -> tuple[int, bool, int, str]:
    candidate = record.candidate
    if (
        candidate.decision is MLDecision.PASS
        and candidate.evidence_level is EvidenceLevel.L2_ML_SCREENED
    ):
        group = 0
    elif candidate.selection_status is SelectionStatus.NOT_SELECTED_BUDGET:
        group = 1
    elif candidate.selection_status in {
        SelectionStatus.NOT_APPLICABLE,
        SelectionStatus.APPLICABILITY_UNKNOWN,
    }:
        group = 3
    elif (
        candidate.decision is MLDecision.UNCERTAIN
        and candidate.execution_status is not ExecutionStatus.NOT_RUN
    ):
        group = 2
    else:
        group = 4
    return (
        group,
        record.scientific_rank is None,
        record.scientific_rank or 0,
        candidate.candidate_id,
    )


def _fake_energy_ev_atom(candidate_id: str) -> float:
    bucket = int(hashlib.sha256(candidate_id.encode()).hexdigest()[:8], 16)
    return -1.0 - (bucket % 10000) / 100000.0
