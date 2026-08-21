"""Pinned, replayable registry for executable soft-chemistry operations."""

from __future__ import annotations

import hashlib
from typing import Annotated, Literal

from pydantic import Field, model_validator
from pymatgen.core import Structure

from material_agent.inspiration.models import (
    ArtifactPointerV1,
    Identifier,
    StrictModel,
    TransformationStatus,
    ValidationCheckV1,
    ValidationStatus,
    canonical_json_bytes,
)
from material_agent.inspiration.transformations import (
    DEFAULT_SUBSTITUTION_REGISTRY_V1,
    SUBSTITUTION_EXECUTION_SCHEMA_VERSION,
    SUBSTITUTION_OPERATOR_ID,
    SUBSTITUTION_OPERATOR_VERSION,
    SubstitutionExecutionRequestV1,
    SubstitutionRegistryV1,
    TransformationExecutionResult,
    TransformationIntegrityError,
    execute_equivalent_site_substitution,
)
from material_agent.softchem.prior import (
    DEFAULT_SMACT_PRIOR_POLICY_V1,
    SmactPriorDecision,
    SmactPriorEvaluator,
    SmactPriorGateResultV1,
    SmactPriorPolicyV1,
    evaluate_smact_substitution_prior,
)
from material_agent.softchem.prior_client import SMACT_WORKER_LOCK_SHA256

SOFTCHEM_OPERATOR_REGISTRY_SCHEMA_VERSION = "softchem-operator-registry-v1"
SOFTCHEM_OPERATOR_REGISTRY_V2_SCHEMA_VERSION = "softchem-operator-registry-v2"

SoftChemOperatorIdV2 = Literal[
    "APPLY_HOMOGENEOUS_STRAIN_V1",
    "INTERCALATE_REGISTERED_SITE_V1",
    "REMOVE_EQUIVALENT_SITE_CLASS_V1",
    "SLIDE_REGISTERED_LAYER_V1",
    "SUBSTITUTE_EQUIVALENT_SITE_V1",
]


class SoftChemOperatorSpecV1(StrictModel):
    """One reviewed executable and its non-negotiable chemistry invariants."""

    operator_id: Literal["SUBSTITUTE_EQUIVALENT_SITE_V1"]
    operator_version: Literal["1"]
    execution_schema_version: Literal["inspiration-substitution-execution-v1"]
    executor_id: Literal["pymatgen-equivalent-site-substitution-v1"]
    permitted_changes: tuple[Literal["species_on_complete_equivalence_class"], ...]
    required_invariants: Annotated[
        tuple[Identifier, ...], Field(min_length=1, max_length=32)
    ]
    unresolved_charge_policy: Literal["REQUIRES_REVIEW"] = "REQUIRES_REVIEW"
    scientific_conclusion: Literal[False] = False

    @model_validator(mode="after")
    def validate_invariants(self) -> SoftChemOperatorSpecV1:
        if self.permitted_changes != ("species_on_complete_equivalence_class",):
            raise ValueError("v1 operator permits only one frozen change class")
        if tuple(sorted(self.required_invariants)) != self.required_invariants:
            raise ValueError("operator invariants must be canonically sorted")
        if len(set(self.required_invariants)) != len(self.required_invariants):
            raise ValueError("operator invariants must be unique")
        return self


class SoftChemOperatorRegistryV1(StrictModel):
    schema_version: Literal["softchem-operator-registry-v1"] = (
        SOFTCHEM_OPERATOR_REGISTRY_SCHEMA_VERSION
    )
    registry_id: Literal["softchem-operator-registry-v1"] = (
        "softchem-operator-registry-v1"
    )
    registry_version: Literal["1"] = "1"
    operators: Annotated[
        tuple[SoftChemOperatorSpecV1, ...], Field(min_length=1, max_length=32)
    ]

    @model_validator(mode="after")
    def validate_operators(self) -> SoftChemOperatorRegistryV1:
        keys = tuple(
            (operator.operator_id, operator.operator_version)
            for operator in self.operators
        )
        if tuple(sorted(keys)) != keys or len(set(keys)) != len(keys):
            raise ValueError("operators must be sorted and unique by ID/version")
        return self

    def resolve(
        self, operator_id: str, operator_version: str
    ) -> SoftChemOperatorSpecV1:
        for operator in self.operators:
            if (
                operator.operator_id == operator_id
                and operator.operator_version == operator_version
            ):
                return operator
        raise KeyError(f"operator is not registered: {operator_id}@{operator_version}")


DEFAULT_SOFTCHEM_OPERATOR_REGISTRY_V1 = SoftChemOperatorRegistryV1(
    operators=(
        SoftChemOperatorSpecV1(
            operator_id=SUBSTITUTION_OPERATOR_ID,
            operator_version=SUBSTITUTION_OPERATOR_VERSION,
            execution_schema_version=SUBSTITUTION_EXECUTION_SCHEMA_VERSION,
            executor_id="pymatgen-equivalent-site-substitution-v1",
            permitted_changes=("species_on_complete_equivalence_class",),
            required_invariants=(
                "canonical_round_trip",
                "charge_or_oxidation",
                "coordinates_preserved",
                "dimensionality_and_site_budget",
                "finite_structure",
                "lattice_preserved",
                "minimum_distance",
                "ordered_occupancy",
                "positive_volume",
                "requirement_composition",
                "site_count_preserved",
            ),
        ),
    )
)


class SoftChemOperatorSpecV2(StrictModel):
    """Declarative contract for one non-programmable structure operation."""

    operator_id: SoftChemOperatorIdV2
    operator_version: Literal["1"] = "1"
    parameter_schema_id: Literal[
        "homogeneous-strain-parameters-v1",
        "registered-intercalation-parameters-v1",
        "equivalent-site-vacancy-parameters-v1",
        "registered-layer-slide-parameters-v1",
        "substitution-parameters-v1",
    ]
    executor_id: Literal[
        "pymatgen-homogeneous-strain-v1",
        "pymatgen-registered-intercalation-v1",
        "pymatgen-equivalent-site-vacancy-v1",
        "pymatgen-registered-layer-slide-v1",
        "pymatgen-equivalent-site-substitution-v1",
    ]
    permitted_changes: Annotated[
        tuple[Identifier, ...], Field(min_length=1, max_length=8)
    ]
    required_invariants: Annotated[
        tuple[Identifier, ...], Field(min_length=1, max_length=32)
    ]
    prior_ids: Annotated[tuple[Identifier, ...], Field(min_length=1, max_length=8)]
    validator_ids: Annotated[tuple[Identifier, ...], Field(min_length=1, max_length=16)]
    accepts_free_coordinates: Literal[False] = False
    accepts_arbitrary_species: Literal[False] = False
    accepts_code: Literal[False] = False
    scientific_conclusion: Literal[False] = False

    @model_validator(mode="after")
    def canonical_collections(self) -> SoftChemOperatorSpecV2:
        for name, values in (
            ("permitted_changes", self.permitted_changes),
            ("required_invariants", self.required_invariants),
            ("prior_ids", self.prior_ids),
            ("validator_ids", self.validator_ids),
        ):
            if tuple(sorted(values)) != values or len(set(values)) != len(values):
                raise ValueError(f"{name} must be sorted and unique")
        return self


class SoftChemOperatorRegistryV2(StrictModel):
    schema_version: Literal["softchem-operator-registry-v2"] = (
        SOFTCHEM_OPERATOR_REGISTRY_V2_SCHEMA_VERSION
    )
    registry_id: Literal["softchem-operator-registry-v2"] = (
        "softchem-operator-registry-v2"
    )
    registry_version: Literal["2"] = "2"
    operators: Annotated[
        tuple[SoftChemOperatorSpecV2, ...], Field(min_length=5, max_length=32)
    ]

    @model_validator(mode="after")
    def validate_operators(self) -> SoftChemOperatorRegistryV2:
        keys = tuple(
            (item.operator_id, item.operator_version) for item in self.operators
        )
        if keys != tuple(sorted(keys)) or len(set(keys)) != len(keys):
            raise ValueError("operators must be sorted and unique by ID/version")
        return self

    def resolve(
        self, operator_id: str, operator_version: str
    ) -> SoftChemOperatorSpecV2:
        for operator in self.operators:
            if (
                operator.operator_id == operator_id
                and operator.operator_version == operator_version
            ):
                return operator
        raise KeyError(f"operator is not registered: {operator_id}@{operator_version}")


_COMMON_INVARIANTS = (
    "canonical_round_trip",
    "finite_structure",
    "minimum_distance",
    "ordered_occupancy",
    "positive_volume",
)

DEFAULT_SOFTCHEM_OPERATOR_REGISTRY_V2 = SoftChemOperatorRegistryV2(
    operators=(
        SoftChemOperatorSpecV2(
            operator_id="APPLY_HOMOGENEOUS_STRAIN_V1",
            parameter_schema_id="homogeneous-strain-parameters-v1",
            executor_id="pymatgen-homogeneous-strain-v1",
            permitted_changes=("lattice_matrix",),
            required_invariants=tuple(
                sorted(
                    (
                        *_COMMON_INVARIANTS,
                        "composition",
                        "fractional_coordinates",
                        "site_count",
                    )
                )
            ),
            prior_ids=("bounded-elastic-strain-prior-v1",),
            validator_ids=(
                "common-structure-validator-v2",
                "strain-delta-validator-v1",
            ),
        ),
        SoftChemOperatorSpecV2(
            operator_id="INTERCALATE_REGISTERED_SITE_V1",
            parameter_schema_id="registered-intercalation-parameters-v1",
            executor_id="pymatgen-registered-intercalation-v1",
            permitted_changes=("registered_intercalant_site",),
            required_invariants=tuple(
                sorted((*_COMMON_INVARIANTS, "host_lattice", "host_sites"))
            ),
            prior_ids=("registered-intercalant-prior-v1", "smact-composition-prior-v1"),
            validator_ids=(
                "common-structure-validator-v2",
                "intercalation-delta-validator-v1",
            ),
        ),
        SoftChemOperatorSpecV2(
            operator_id="REMOVE_EQUIVALENT_SITE_CLASS_V1",
            parameter_schema_id="equivalent-site-vacancy-parameters-v1",
            executor_id="pymatgen-equivalent-site-vacancy-v1",
            permitted_changes=("complete_equivalence_class_removal",),
            required_invariants=tuple(
                sorted((*_COMMON_INVARIANTS, "host_lattice", "unselected_sites"))
            ),
            prior_ids=(
                "bounded-vacancy-fraction-prior-v1",
                "smact-composition-prior-v1",
            ),
            validator_ids=(
                "common-structure-validator-v2",
                "vacancy-delta-validator-v1",
            ),
        ),
        SoftChemOperatorSpecV2(
            operator_id="SLIDE_REGISTERED_LAYER_V1",
            parameter_schema_id="registered-layer-slide-parameters-v1",
            executor_id="pymatgen-registered-layer-slide-v1",
            permitted_changes=("registered_layer_translation",),
            required_invariants=tuple(
                sorted(
                    (*_COMMON_INVARIANTS, "composition", "host_lattice", "site_count")
                )
            ),
            prior_ids=("layer-separation-prior-v1",),
            validator_ids=(
                "common-structure-validator-v2",
                "layer-slide-delta-validator-v1",
            ),
        ),
        SoftChemOperatorSpecV2(
            operator_id="SUBSTITUTE_EQUIVALENT_SITE_V1",
            parameter_schema_id="substitution-parameters-v1",
            executor_id="pymatgen-equivalent-site-substitution-v1",
            permitted_changes=("species_on_complete_equivalence_class",),
            required_invariants=tuple(
                sorted(
                    (
                        *_COMMON_INVARIANTS,
                        "fractional_coordinates",
                        "host_lattice",
                        "site_count",
                    )
                )
            ),
            prior_ids=("smact-substitution-prior-v1",),
            validator_ids=("substitution-structure-validator-v1",),
        ),
    )
)


def softchem_registry_bytes(
    registry: SoftChemOperatorRegistryV1 | SoftChemOperatorRegistryV2,
) -> bytes:
    if not isinstance(
        registry, (SoftChemOperatorRegistryV1, SoftChemOperatorRegistryV2)
    ):
        raise TransformationIntegrityError(
            "INVALID_SOFTCHEM_REGISTRY",
            "registry must be a SoftChemOperatorRegistryV1",
        )
    return canonical_json_bytes(registry)


def softchem_registry_sha256(
    registry: SoftChemOperatorRegistryV1 | SoftChemOperatorRegistryV2,
) -> str:
    return hashlib.sha256(softchem_registry_bytes(registry)).hexdigest()


def _verify_registry_artifact(
    pointer: ArtifactPointerV1,
    registry: SoftChemOperatorRegistryV1,
) -> None:
    payload = softchem_registry_bytes(registry)
    if pointer.sha256 != hashlib.sha256(payload).hexdigest():
        raise TransformationIntegrityError(
            "SOFTCHEM_REGISTRY_HASH_MISMATCH",
            "operator registry differs from its frozen Artifact SHA-256",
        )
    if pointer.size_bytes is not None and pointer.size_bytes != len(payload):
        raise TransformationIntegrityError(
            "SOFTCHEM_REGISTRY_SIZE_MISMATCH",
            "operator registry differs from its frozen Artifact size",
        )
    if pointer.media_type not in {None, "application/json"}:
        raise TransformationIntegrityError(
            "SOFTCHEM_REGISTRY_MEDIA_TYPE_MISMATCH",
            "operator registry Artifact must be JSON",
        )


def execute_registered_softchem_operator(
    request: SubstitutionExecutionRequestV1,
    *,
    parent_structure: Structure,
    parent_artifact_bytes: bytes,
    operator_registry: SoftChemOperatorRegistryV1,
    operator_registry_artifact: ArtifactPointerV1,
    substitution_registry: SubstitutionRegistryV1 = DEFAULT_SUBSTITUTION_REGISTRY_V1,
    prior_policy: SmactPriorPolicyV1 = DEFAULT_SMACT_PRIOR_POLICY_V1,
    prior_evaluator: SmactPriorEvaluator | None = None,
) -> TransformationExecutionResult:
    """Run the SMACT prior, then one hash-pinned allowlisted operator.

    Deliberately using an explicit branch avoids importing code from registry
    data or turning the registry into an arbitrary entry-point mechanism.
    """

    prior = evaluate_smact_substitution_prior(
        request,
        parent_structure=parent_structure,
        policy=prior_policy,
        evaluator=prior_evaluator,
    )
    if prior.decision is SmactPriorDecision.REQUIRES_REVIEW:
        reasons = ",".join(prior.reason_codes)
        raise TransformationIntegrityError(
            "SMACT_PRIOR_REVIEW_REQUIRED",
            f"SMACT prior requires review before registry dispatch: {reasons}",
        )
    if prior.decision is SmactPriorDecision.REJECT:
        return _prior_rejected(request, prior)
    if prior.worker_lock_sha256 != SMACT_WORKER_LOCK_SHA256:
        raise TransformationIntegrityError(
            "SMACT_PRIOR_WORKER_REQUIRED",
            "a passing SMACT prior must come from the pinned independent worker",
        )

    _verify_registry_artifact(operator_registry_artifact, operator_registry)
    plan = request.plan
    try:
        spec = operator_registry.resolve(plan.operator_id, plan.operator_version)
    except KeyError as error:
        raise TransformationIntegrityError(
            "SOFTCHEM_OPERATOR_NOT_REGISTERED",
            "planned operator ID/version is absent from the frozen registry",
        ) from error
    if spec.execution_schema_version != request.schema_version:
        raise TransformationIntegrityError(
            "SOFTCHEM_EXECUTION_SCHEMA_MISMATCH",
            "registered execution schema differs from the request",
        )
    if spec.executor_id != "pymatgen-equivalent-site-substitution-v1":
        raise TransformationIntegrityError(
            "SOFTCHEM_EXECUTOR_NOT_AVAILABLE",
            "registered executor is not present in this release",
        )
    result = execute_equivalent_site_substitution(
        request,
        parent_structure=parent_structure,
        parent_artifact_bytes=parent_artifact_bytes,
        registry=substitution_registry,
    )
    return _attach_prior_pass(result, prior)


def _prior_detail(prior: SmactPriorGateResultV1) -> str:
    return (
        f"SMACT {prior.backend_version or 'unavailable'} policy "
        f"{prior.policy_id}/{prior.policy_sha256} evaluated "
        f"{prior.input_formula} -> {prior.proposed_formula}: "
        f"{','.join(prior.reason_codes)}; worker lock "
        f"{prior.worker_lock_sha256 or 'unavailable'}. "
        "This is a heuristic prior only."
    )


def _prior_rejected(
    request: SubstitutionExecutionRequestV1,
    prior: SmactPriorGateResultV1,
) -> TransformationExecutionResult:
    check = ValidationCheckV1(
        check_id="smact_prior_gate",
        status=ValidationStatus.FAIL,
        detail=_prior_detail(prior),
    )
    plan_payload = request.plan.model_dump(mode="python")
    plan_payload.update(
        {
            "status": TransformationStatus.REJECTED,
            "validation_checks": (check,),
            "output_structure_id": None,
            "output_structure_artifact": None,
        }
    )
    plan = type(request.plan).model_validate(plan_payload)
    return TransformationExecutionResult(
        plan=plan,
        output_structure=None,
        artifact_bytes=None,
    )


def _attach_prior_pass(
    result: TransformationExecutionResult,
    prior: SmactPriorGateResultV1,
) -> TransformationExecutionResult:
    check = ValidationCheckV1(
        check_id="smact_prior_gate",
        status=ValidationStatus.PASS,
        detail=_prior_detail(prior),
    )
    plan_payload = result.plan.model_dump(mode="python")
    plan_payload["validation_checks"] = (check, *result.plan.validation_checks)
    plan = type(result.plan).model_validate(plan_payload)
    return TransformationExecutionResult(
        plan=plan,
        output_structure=result.output_structure,
        artifact_bytes=result.artifact_bytes,
    )
