from __future__ import annotations

import hashlib
import warnings
from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import ValidationError
from pymatgen.core import Lattice, Structure
from pymatgen.io.cif import CifWriter

from material_agent.inspiration.models import (
    ArtifactPointerV1,
    SubstitutionParametersV1,
    TransformationPlanV1,
    TransformationStatus,
    ValidationStatus,
    deterministic_id,
    transformation_route_sha256,
)
from material_agent.inspiration.transformations import (
    DEFAULT_SUBSTITUTION_REGISTRY_V1,
    STRUCTURE_ARTIFACT_MEDIA_TYPE,
    SubstitutionExecutionRequestV1,
    TransformationIntegrityError,
    substitution_registry_bytes,
)
from material_agent.retrieval.storage import LocalArtifactStore
from material_agent.softchem import (
    DEFAULT_SMACT_PRIOR_POLICY_V1,
    DEFAULT_SOFTCHEM_OPERATOR_REGISTRY_V1,
    SMACT_WORKER_LOCK_SHA256,
    DownstreamIntent,
    ElementStoichiometryV1,
    SmactPriorDecision,
    SmactPriorGateResultV1,
    SoftChemDownstreamPlanV1,
    SoftChemDownstreamRunner,
    SoftChemExecutionBindings,
    SoftChemOperatorRegistryV1,
    StageStatus,
    build_softchem_downstream_plan,
    evaluate_smact_substitution_prior,
    execute_registered_softchem_operator,
    smact_prior_policy_sha256,
    softchem_registry_bytes,
    softchem_registry_sha256,
)


class _FixtureSmactPriorEvaluator:
    def evaluate(
        self,
        composition,
        *,
        input_formula: str | None,
        policy,
    ) -> SmactPriorGateResultV1:
        from pymatgen.core import Composition

        parsed = (
            composition
            if isinstance(composition, Composition)
            else Composition(composition)
        )
        formula = parsed.reduced_formula
        amounts = parsed.reduced_composition.element_composition.get_el_amt_dict()
        stoichiometry = tuple(
            ElementStoichiometryV1(element=symbol, amount=int(amounts[symbol]))
            for symbol in sorted(amounts)
        )
        if formula == "NaCl2":
            decision = SmactPriorDecision.REJECT
            valid = False
            reasons = ("NO_ALLOWED_OXIDATION_STATE_ASSIGNMENT",)
        elif formula == "CuAu":
            decision = SmactPriorDecision.REQUIRES_REVIEW
            valid = None
            reasons = ("ALLOY_COMPOSITION_REQUIRES_REVIEW",)
        else:
            decision = SmactPriorDecision.PASS
            valid = True
            reasons = ("CHARGE_NEUTRALITY_AND_PAULING_PASS",)
        return SmactPriorGateResultV1(
            decision=decision,
            policy_id=policy.policy_id,
            policy_sha256=smact_prior_policy_sha256(policy),
            backend_version=policy.smact_version,
            worker_lock_sha256=SMACT_WORKER_LOCK_SHA256,
            input_formula=input_formula or formula,
            proposed_formula=formula,
            reduced_stoichiometry=stoichiometry,
            smact_valid=valid,
            reason_codes=reasons,
        )


FIXTURE_SMACT_PRIOR = _FixtureSmactPriorEvaluator()


def _cif_bytes(structure: Structure) -> bytes:
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        text = str(
            CifWriter(
                structure,
                symprec=None,
                write_magmoms=False,
                significant_figures=12,
            )
        )
    text = text.replace("\r\n", "\n")
    return (text if text.endswith("\n") else text + "\n").encode("utf-8")


def _pointer(uri: str, payload: bytes, media_type: str) -> ArtifactPointerV1:
    return ArtifactPointerV1(
        uri=uri,
        sha256=hashlib.sha256(payload).hexdigest(),
        size_bytes=len(payload),
        media_type=media_type,
    )


def _execution_request(
    parent_bytes: bytes,
    *,
    source: str = "S",
    target: str = "Se",
    allowed_elements: tuple[str, ...] = ("Se", "Ti"),
) -> SubstitutionExecutionRequestV1:
    parameters = SubstitutionParametersV1(
        equivalent_site_indices=(1, 2),
        source_species=source,
        target_species=target,
    )
    route_sha256 = transformation_route_sha256(
        parent_structure_id="structure-parent",
        operator_id="SUBSTITUTE_EQUIVALENT_SITE_V1",
        operator_version="1",
        parameters=parameters,
    )
    plan = TransformationPlanV1(
        plan_id=deterministic_id("plan", {"route": route_sha256}),
        parent_candidate_id="candidate-parent",
        parent_structure_id="structure-parent",
        parent_structure_artifact=_pointer(
            "artifact://retrieval/structures/parent.cif",
            parent_bytes,
            STRUCTURE_ARTIFACT_MEDIA_TYPE,
        ),
        parameters=parameters,
        preserved_features=("lattice and fractional coordinates",),
        changed_features=("complete equivalent-site species class",),
        falsification_tests=("charge and structure validation",),
        bridge_packet_ids=("bridge-1",),
        route_sha256=route_sha256,
        status=TransformationStatus.PLANNED,
    )
    substitution_payload = substitution_registry_bytes(
        DEFAULT_SUBSTITUTION_REGISTRY_V1
    )
    return SubstitutionExecutionRequestV1(
        plan=plan,
        registry_artifact=_pointer(
            "artifact://inspiration/registry/substitutions-v1.json",
            substitution_payload,
            "application/json",
        ),
        equivalent_site_groups=((0,), (1, 2)),
        allowed_output_elements=allowed_elements,
    )


def _execute(
    parent: Structure,
    *,
    source: str = "S",
    target: str = "Se",
    allowed_elements: tuple[str, ...] = ("Se", "Ti"),
):
    parent_bytes = _cif_bytes(parent)
    request = _execution_request(
        parent_bytes,
        source=source,
        target=target,
        allowed_elements=allowed_elements,
    )
    registry_payload = softchem_registry_bytes(
        DEFAULT_SOFTCHEM_OPERATOR_REGISTRY_V1
    )
    result = execute_registered_softchem_operator(
        request,
        parent_structure=parent,
        parent_artifact_bytes=parent_bytes,
        operator_registry=DEFAULT_SOFTCHEM_OPERATOR_REGISTRY_V1,
        operator_registry_artifact=_pointer(
            "artifact://softchem/registry/operators-v1.json",
            registry_payload,
            "application/json",
        ),
        prior_evaluator=FIXTURE_SMACT_PRIOR,
    )
    return result


def _softchem_registry_pointer() -> ArtifactPointerV1:
    payload = softchem_registry_bytes(DEFAULT_SOFTCHEM_OPERATOR_REGISTRY_V1)
    return _pointer(
        "artifact://softchem/registry/operators-v1.json",
        payload,
        "application/json",
    )


def _layered_tis2(*, titanium: str = "Ti4+", charged: bool = True) -> Structure:
    species = (titanium, "S2-", "S2-") if charged else ("Ti", "S", "S")
    return Structure(
        Lattice.hexagonal(3.4, 6.0),
        species,
        (
            (0.0, 0.0, 0.0),
            (1.0 / 3.0, 2.0 / 3.0, 0.25),
            (2.0 / 3.0, 1.0 / 3.0, 0.75),
        ),
    )


def _checks(result) -> dict[str, ValidationStatus]:
    return {
        check.check_id: check.status for check in result.plan.validation_checks
    }
def test_registry_is_versioned_canonical_and_rejects_unknown_fields() -> None:
    payload = softchem_registry_bytes(DEFAULT_SOFTCHEM_OPERATOR_REGISTRY_V1)

    assert hashlib.sha256(payload).hexdigest() == softchem_registry_sha256(
        DEFAULT_SOFTCHEM_OPERATOR_REGISTRY_V1
    )
    assert DEFAULT_SOFTCHEM_OPERATOR_REGISTRY_V1.schema_version == (
        "softchem-operator-registry-v1"
    )
    assert DEFAULT_SOFTCHEM_OPERATOR_REGISTRY_V1.resolve(
        "SUBSTITUTE_EQUIVALENT_SITE_V1", "1"
    ).executor_id == "pymatgen-equivalent-site-substitution-v1"

    bad = DEFAULT_SOFTCHEM_OPERATOR_REGISTRY_V1.model_dump(mode="json")
    bad["dynamic_entry_point"] = "arbitrary.module:execute"
    with pytest.raises(ValidationError, match="Extra inputs"):
        SoftChemOperatorRegistryV1.model_validate(bad)


def test_smact_prior_policy_is_pinned_and_canonical() -> None:
    result = FIXTURE_SMACT_PRIOR.evaluate(
        "TiSe2",
        input_formula="TiS2",
        policy=DEFAULT_SMACT_PRIOR_POLICY_V1,
    )

    assert DEFAULT_SMACT_PRIOR_POLICY_V1.smact_version == "4.0.0"
    assert len(smact_prior_policy_sha256(DEFAULT_SMACT_PRIOR_POLICY_V1)) == 64
    assert result.decision is SmactPriorDecision.PASS
    assert result.backend_version == "4.0.0"
    assert result.worker_lock_sha256 == SMACT_WORKER_LOCK_SHA256
    assert result.policy_sha256 == smact_prior_policy_sha256(
        DEFAULT_SMACT_PRIOR_POLICY_V1
    )
    assert result.evidence_level == "NONE"
    assert result.scientific_conclusion is False


def test_smact_prior_rejects_invalid_ionic_stoichiometry_and_reviews_alloy() -> None:
    invalid = FIXTURE_SMACT_PRIOR.evaluate(
        "NaCl2",
        input_formula="NaCl",
        policy=DEFAULT_SMACT_PRIOR_POLICY_V1,
    )
    alloy = FIXTURE_SMACT_PRIOR.evaluate(
        "AuCu",
        input_formula="AuCu",
        policy=DEFAULT_SMACT_PRIOR_POLICY_V1,
    )

    assert invalid.decision is SmactPriorDecision.REJECT
    assert invalid.smact_valid is False
    assert invalid.reason_codes == ("NO_ALLOWED_OXIDATION_STATE_ASSIGNMENT",)
    assert alloy.decision is SmactPriorDecision.REQUIRES_REVIEW
    assert alloy.smact_valid is None
    assert alloy.reason_codes == ("ALLOY_COMPOSITION_REQUIRES_REVIEW",)


def test_smact_substitution_prior_uses_absolute_supercell_counts() -> None:
    parent = _layered_tis2()
    parent.make_supercell((1, 1, 2))
    request = _execution_request(_cif_bytes(parent))

    result = evaluate_smact_substitution_prior(
        request,
        parent_structure=parent,
        evaluator=FIXTURE_SMACT_PRIOR,
    )

    assert result.proposed_formula == "TiSeS"
    assert "ROUTE_SOURCE_STOICHIOMETRY_MISMATCH" not in result.reason_codes


def test_smact_worker_is_fail_closed_when_unconfigured(monkeypatch) -> None:
    monkeypatch.delenv("MATERIAL_AGENT_SMACT_WORKER_PYTHON", raising=False)
    parent = _layered_tis2()
    request = _execution_request(_cif_bytes(parent))

    result = evaluate_smact_substitution_prior(
        request,
        parent_structure=parent,
    )

    assert result.decision is SmactPriorDecision.REQUIRES_REVIEW
    assert result.reason_codes == ("SMACT_WORKER_UNCONFIGURED",)
    assert result.worker_lock_sha256 is None


def test_registry_artifact_hash_is_mandatory_before_execution() -> None:
    parent = _layered_tis2()
    parent_bytes = _cif_bytes(parent)
    request = _execution_request(parent_bytes)
    payload = softchem_registry_bytes(DEFAULT_SOFTCHEM_OPERATOR_REGISTRY_V1)
    pointer = _pointer(
        "artifact://softchem/registry/operators-v1.json",
        payload,
        "application/json",
    )
    tampered = pointer.model_copy(update={"sha256": "0" * 64})

    with pytest.raises(
        TransformationIntegrityError,
        match="SOFTCHEM_REGISTRY_HASH_MISMATCH",
    ):
        execute_registered_softchem_operator(
            request,
            parent_structure=parent,
            parent_artifact_bytes=parent_bytes,
            operator_registry=DEFAULT_SOFTCHEM_OPERATOR_REGISTRY_V1,
            operator_registry_artifact=tampered,
            prior_evaluator=FIXTURE_SMACT_PRIOR,
        )


def test_registered_operator_preserves_stoichiometry_and_explicit_charge() -> None:
    result = _execute(_layered_tis2())

    assert result.plan.status is TransformationStatus.STRUCTURE_VALID
    assert result.output_structure is not None
    assert result.output_structure.composition.element_composition.as_dict() == {
        "Ti": 1.0,
        "Se": 2.0,
    }
    checks = _checks(result)
    assert checks["smact_prior_gate"] is ValidationStatus.PASS
    assert checks["charge_or_oxidation"] is ValidationStatus.PASS
    assert checks["requirement_composition"] is ValidationStatus.PASS
    assert checks["lattice_preserved"] is ValidationStatus.PASS
    assert checks["coordinates_preserved"] is ValidationStatus.PASS


def test_smact_rejection_precedes_operator_registry_dispatch(monkeypatch) -> None:
    from material_agent.softchem import registry as registry_module

    parent = _layered_tis2()
    parent_bytes = _cif_bytes(parent)
    request = _execution_request(parent_bytes)
    rejected = FIXTURE_SMACT_PRIOR.evaluate(
        "NaCl2",
        input_formula="NaCl",
        policy=DEFAULT_SMACT_PRIOR_POLICY_V1,
    )
    payload = softchem_registry_bytes(DEFAULT_SOFTCHEM_OPERATOR_REGISTRY_V1)
    tampered_pointer = _pointer(
        "artifact://softchem/registry/operators-v1.json",
        payload,
        "application/json",
    ).model_copy(update={"sha256": "0" * 64})
    monkeypatch.setattr(
        registry_module,
        "evaluate_smact_substitution_prior",
        lambda *args, **kwargs: rejected,
    )

    result = execute_registered_softchem_operator(
        request,
        parent_structure=parent,
        parent_artifact_bytes=parent_bytes,
        operator_registry=DEFAULT_SOFTCHEM_OPERATOR_REGISTRY_V1,
        operator_registry_artifact=tampered_pointer,
    )

    assert result.plan.status is TransformationStatus.REJECTED
    assert result.output_structure is None
    assert _checks(result)["smact_prior_gate"] is ValidationStatus.FAIL


def test_smact_review_blocks_before_operator_registry_dispatch(monkeypatch) -> None:
    from material_agent.softchem import registry as registry_module

    parent = _layered_tis2()
    parent_bytes = _cif_bytes(parent)
    request = _execution_request(parent_bytes)
    review = FIXTURE_SMACT_PRIOR.evaluate(
        "AuCu",
        input_formula="AuCu",
        policy=DEFAULT_SMACT_PRIOR_POLICY_V1,
    )
    payload = softchem_registry_bytes(DEFAULT_SOFTCHEM_OPERATOR_REGISTRY_V1)
    tampered_pointer = _pointer(
        "artifact://softchem/registry/operators-v1.json",
        payload,
        "application/json",
    ).model_copy(update={"sha256": "0" * 64})
    monkeypatch.setattr(
        registry_module,
        "evaluate_smact_substitution_prior",
        lambda *args, **kwargs: review,
    )

    with pytest.raises(
        TransformationIntegrityError,
        match="SMACT_PRIOR_REVIEW_REQUIRED",
    ):
        execute_registered_softchem_operator(
            request,
            parent_structure=parent,
            parent_artifact_bytes=parent_bytes,
            operator_registry=DEFAULT_SOFTCHEM_OPERATOR_REGISTRY_V1,
            operator_registry_artifact=tampered_pointer,
        )


def test_unknown_oxidation_state_requires_review_and_cannot_enter_selection() -> None:
    result = _execute(_layered_tis2(charged=False))

    assert result.plan.status is TransformationStatus.REQUIRES_REVIEW
    assert result.candidate_selection_eligible is False
    assert _checks(result)["charge_or_oxidation"] is ValidationStatus.UNKNOWN


def test_non_neutral_charge_and_unapproved_stoichiometry_fail_closed() -> None:
    non_neutral = _execute(_layered_tis2(titanium="Ti3+"))
    assert non_neutral.plan.status is TransformationStatus.REJECTED
    assert non_neutral.output_structure is None
    assert _checks(non_neutral)["charge_or_oxidation"] is ValidationStatus.FAIL

    disallowed_output = _execute(
        _layered_tis2(),
        allowed_elements=("S", "Ti"),
    )
    assert disallowed_output.plan.status is TransformationStatus.REJECTED
    assert disallowed_output.output_structure is None
    assert _checks(disallowed_output)["requirement_composition"] is (
        ValidationStatus.FAIL
    )


def test_unregistered_species_pair_is_rejected_without_dynamic_fallback() -> None:
    result = _execute(_layered_tis2(), target="Te", allowed_elements=("Te", "Ti"))

    assert result.plan.status is TransformationStatus.REJECTED
    assert result.output_structure is None
    assert _checks(result)["target_species_valid"] is ValidationStatus.FAIL


def test_downstream_plan_is_versioned_replayable_and_tamper_evident() -> None:
    transformation = _execute(_layered_tis2())
    plan = build_softchem_downstream_plan(
        transformation,
        operator_registry=DEFAULT_SOFTCHEM_OPERATOR_REGISTRY_V1,
        operator_registry_artifact=_softchem_registry_pointer(),
        deeph_intent=DownstreamIntent.RUN,
        dft_intent=DownstreamIntent.RUN,
    )
    replay = build_softchem_downstream_plan(
        transformation,
        operator_registry=DEFAULT_SOFTCHEM_OPERATOR_REGISTRY_V1,
        operator_registry_artifact=_softchem_registry_pointer(),
        deeph_intent=DownstreamIntent.RUN,
        dft_intent=DownstreamIntent.RUN,
        created_at=plan.created_at,
    )

    assert plan.schema_version == "softchem-downstream-plan-v1"
    assert replay == plan
    payload = plan.model_dump(mode="python")
    payload["transformation_route_sha256"] = "0" * 64
    with pytest.raises(ValidationError, match="operation key differs"):
        SoftChemDownstreamPlanV1.model_validate(payload)


def test_downstream_plan_rejects_operator_result_that_bypassed_smact() -> None:
    transformation = _execute(_layered_tis2())
    plan_payload = transformation.plan.model_dump(mode="python")
    plan_payload["validation_checks"] = tuple(
        check
        for check in transformation.plan.validation_checks
        if check.check_id != "smact_prior_gate"
    )
    bypassed = type(transformation)(
        plan=type(transformation.plan).model_validate(plan_payload),
        output_structure=transformation.output_structure,
        artifact_bytes=transformation.artifact_bytes,
    )

    with pytest.raises(ValueError, match="passing SMACT prior gate"):
        build_softchem_downstream_plan(
            bypassed,
            operator_registry=DEFAULT_SOFTCHEM_OPERATOR_REGISTRY_V1,
            operator_registry_artifact=_softchem_registry_pointer(),
        )


def test_unknown_charge_stops_before_every_downstream_executor(tmp_path) -> None:
    transformation = _execute(_layered_tis2(charged=False))
    plan = build_softchem_downstream_plan(
        transformation,
        operator_registry=DEFAULT_SOFTCHEM_OPERATOR_REGISTRY_V1,
        operator_registry_artifact=_softchem_registry_pointer(),
        deeph_intent=DownstreamIntent.RUN,
        dft_intent=DownstreamIntent.RUN,
    )

    result = SoftChemDownstreamRunner(
        artifact_store=LocalArtifactStore(tmp_path)
    ).execute(
        plan,
        transformation=transformation,
        operator_registry=DEFAULT_SOFTCHEM_OPERATOR_REGISTRY_V1,
        bindings=SoftChemExecutionBindings(),
    )

    assert result.operator.status is StageStatus.BLOCKED
    assert result.operator.reason_codes == ("CHARGE_OR_OXIDATION_REVIEW_REQUIRED",)
    assert result.chgnet.status is StageStatus.NOT_RUN
    assert result.deeph.status is StageStatus.NOT_RUN
    assert result.dft.status is StageStatus.NOT_RUN
    assert result.chgnet_result is None
    assert result.deeph_result is None
    assert result.dft_job is None


def test_valid_operator_without_real_chgnet_binding_is_explicitly_blocked(
    tmp_path,
) -> None:
    transformation = _execute(_layered_tis2())
    plan = build_softchem_downstream_plan(
        transformation,
        operator_registry=DEFAULT_SOFTCHEM_OPERATOR_REGISTRY_V1,
        operator_registry_artifact=_softchem_registry_pointer(),
        deeph_intent=DownstreamIntent.RUN,
        dft_intent=DownstreamIntent.RUN,
    )
    result = SoftChemDownstreamRunner(
        artifact_store=LocalArtifactStore(tmp_path)
    ).execute(
        plan,
        transformation=transformation,
        operator_registry=DEFAULT_SOFTCHEM_OPERATOR_REGISTRY_V1,
        bindings=SoftChemExecutionBindings(),
    )

    assert result.operator.status is StageStatus.SUCCEEDED
    assert result.chgnet.status is StageStatus.BLOCKED
    assert result.chgnet.reason_codes == ("REAL_CHGNET_BINDING_UNAVAILABLE",)
    assert result.deeph.status is StageStatus.NOT_RUN
    assert result.dft.status is StageStatus.NOT_RUN


def test_mock_chgnet_worker_is_blocked_without_being_called(
    tmp_path,
    ml_candidate_factory,
    ml_plan_factory,
    ml_model,
    ml_health,
    ml_policy,
) -> None:
    from material_agent.ml_screening.adapters import FakeMLModelAdapter, FakeMLWorker
    transformation = _execute(_layered_tis2())
    native_plan = ml_plan_factory([ml_candidate_factory("fake-softchem")])
    adapter = FakeMLModelAdapter(model=ml_model, health=ml_health)
    fake_worker = FakeMLWorker(adapter=adapter, policy=ml_policy)
    plan = build_softchem_downstream_plan(
        transformation,
        operator_registry=DEFAULT_SOFTCHEM_OPERATOR_REGISTRY_V1,
        operator_registry_artifact=_softchem_registry_pointer(),
        chgnet_plan=native_plan,
    )

    result = SoftChemDownstreamRunner(
        artifact_store=LocalArtifactStore(tmp_path)
    ).execute(
        plan,
        transformation=transformation,
        operator_registry=DEFAULT_SOFTCHEM_OPERATOR_REGISTRY_V1,
        bindings=SoftChemExecutionBindings(
            chgnet_plan=native_plan,
            chgnet_worker=fake_worker,  # type: ignore[arg-type]
        ),
    )

    assert result.chgnet.status is StageStatus.BLOCKED
    assert "CHGNET_INDEPENDENT_WORKER_REQUIRED" in result.chgnet.reason_codes
    assert adapter.calls == {}


def test_requested_deeph_and_dft_require_explicit_real_bindings(tmp_path) -> None:
    transformation = _execute(_layered_tis2())
    plan = build_softchem_downstream_plan(
        transformation,
        operator_registry=DEFAULT_SOFTCHEM_OPERATOR_REGISTRY_V1,
        operator_registry_artifact=_softchem_registry_pointer(),
        deeph_intent=DownstreamIntent.RUN,
        dft_intent=DownstreamIntent.RUN,
    )
    relaxed = transformation.plan.output_structure_artifact
    assert relaxed is not None
    runner = SoftChemDownstreamRunner(
        artifact_store=LocalArtifactStore(tmp_path)
    )

    deeph, deeph_result = runner._run_deeph(
        plan,
        SoftChemExecutionBindings(),
        relaxed,
    )
    dft, dft_job = runner._run_dft(
        plan,
        SoftChemExecutionBindings(),
        relaxed,
    )

    assert deeph.status is StageStatus.BLOCKED
    assert deeph.reason_codes == ("REAL_DEEPH_BINDING_UNAVAILABLE",)
    assert deeph_result is None
    assert dft.status is StageStatus.BLOCKED
    assert dft.reason_codes == ("REAL_DFT_BINDING_UNAVAILABLE",)
    assert dft_job is None


def test_mock_deeph_request_is_blocked_before_subprocess_start(tmp_path) -> None:
    from material_agent.ml_screening.deeph_client import (
        DeepHFlowRunner,
        DeepHSubprocessClient,
    )
    from tests.unit.test_ml_deeph_contracts import _request as deeph_fixture_request

    transformation = _execute(_layered_tis2())
    request = deeph_fixture_request(tmp_path)
    plan = build_softchem_downstream_plan(
        transformation,
        operator_registry=DEFAULT_SOFTCHEM_OPERATOR_REGISTRY_V1,
        operator_registry_artifact=_softchem_registry_pointer(),
        deeph_intent=DownstreamIntent.RUN,
        deeph_request=request,
    )
    store = LocalArtifactStore(tmp_path)
    deeph_runner = DeepHFlowRunner(
        artifact_store=store,
        client=DeepHSubprocessClient(
            worker_python=Path("/definitely/not/a/worker-python"),
            deeph_executable=Path("/definitely/not/deeph-inference"),
            artifact_root=tmp_path,
        ),
    )
    relaxed = transformation.plan.output_structure_artifact
    assert relaxed is not None

    outcome, result = SoftChemDownstreamRunner(
        artifact_store=store
    )._run_deeph(
        plan,
        SoftChemExecutionBindings(
            deeph_request=request,
            deeph_runner=deeph_runner,
        ),
        relaxed,
    )

    assert outcome.status is StageStatus.BLOCKED
    assert "REAL_DEEPH_REQUEST_REQUIRED" in outcome.reason_codes
    assert result is None


def test_dft_approval_failure_blocks_before_live_backend_contact(tmp_path) -> None:
    from material_agent.dft.models import ArtifactRef, canonical_hash
    from material_agent.dft.preflight import ApprovalSnapshot
    from material_agent.dft.vaspilot_backend import VASPilotBackend
    from tests.unit.test_dft_control_plane import _preflight_input

    class NoCallTransport:
        def __init__(self) -> None:
            self.calls = 0

        def request(self, method, path, body=None):
            self.calls += 1
            raise AssertionError("backend must not be contacted before approval")

    transformation = _execute(_layered_tis2())
    relaxed = transformation.plan.output_structure_artifact
    assert relaxed is not None
    preflight = _preflight_input()
    dft_ref = ArtifactRef(uri=relaxed.uri, sha256=relaxed.sha256)
    candidate = preflight.request.candidates[0].model_copy(
        update={"structure_artifact": dft_ref}
    )
    task = preflight.request.task_specs[0].model_copy(
        update={"input_structure": dft_ref}
    )
    request = preflight.request.model_copy(
        update={"candidates": (candidate,), "task_specs": (task,)}
    )
    approval = ApprovalSnapshot(
        approval_id=request.approval_id or "missing",
        status="PENDING",
        request_sha256=canonical_hash(request),
        workflow_plan_hash=request.workflow_plan_hash,
    )
    current = datetime(2026, 8, 11, tzinfo=UTC)
    preflight = preflight.model_copy(
        update={"request": request, "approval": approval, "checked_at": current}
    )
    transport = NoCallTransport()
    backend = VASPilotBackend(
        transport=transport,
        descriptor=preflight.backend_descriptor,
    )
    plan = build_softchem_downstream_plan(
        transformation,
        operator_registry=DEFAULT_SOFTCHEM_OPERATOR_REGISTRY_V1,
        operator_registry_artifact=_softchem_registry_pointer(),
        dft_intent=DownstreamIntent.RUN,
        dft_preflight=preflight,
    )

    outcome, job = SoftChemDownstreamRunner(
        artifact_store=LocalArtifactStore(tmp_path)
    )._run_dft(
        plan,
        SoftChemExecutionBindings(
            dft_preflight=preflight,
            dft_backend=backend,
        ),
        relaxed,
        checked_at=current,
    )

    assert outcome.status is StageStatus.BLOCKED
    assert "APPROVAL_NOT_GRANTED" in outcome.reason_codes
    assert job is None
    assert transport.calls == 0
