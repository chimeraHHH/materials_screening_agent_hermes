"""Compile DeepSeek structure ideas into frozen, executable soft-chemistry plans."""

from __future__ import annotations

import hashlib
import threading
import warnings
from collections.abc import Callable, Mapping
from typing import Any, Literal

from pydantic import Field
from pymatgen.core import Structure
from pymatgen.symmetry.analyzer import SpacegroupAnalyzer

from material_agent.inspiration.deepseek_agent import DeepSeekFunctionTool
from material_agent.inspiration.models import (
    ArtifactPointerV1,
    SubstitutionParametersV1,
    TransformationPlanV1,
    TransformationStatus,
    canonical_json_bytes,
    deterministic_id,
    transformation_route_sha256,
)
from material_agent.inspiration.research_graph import (
    DatabaseCandidateV1,
    RegisteredTransformationAuditV1,
    TransformationCompileRejectionV1,
    TransformationPlanBindingV1,
)
from material_agent.inspiration.transformations import (
    DEFAULT_SUBSTITUTION_REGISTRY_V1,
    EQUIVALENT_SITE_ANGLE_TOLERANCE,
    EQUIVALENT_SITE_SYMPREC,
    STRUCTURE_ARTIFACT_MEDIA_TYPE,
    SubstitutionExecutionRequestV1,
    SubstitutionRegistryV1,
    substitution_registry_bytes,
    substitution_registry_sha256,
)
from material_agent.orchestrator.llm import LLMProviderError
from material_agent.orchestrator.models import StrictModel
from material_agent.retrieval.storage import LocalArtifactStore
from material_agent.softchem import (
    DEFAULT_SOFTCHEM_OPERATOR_REGISTRY_V1,
    SoftChemOperatorRegistryV1,
    softchem_registry_sha256,
)


class CompileRegisteredSubstitutionArgsV1(StrictModel):
    candidate_id: str = Field(pattern=r"^candidate-[a-z0-9-]{1,64}$")
    database_candidate_id: str = Field(pattern=r"^db-candidate-[0-9a-f]{24}$")
    substitution_rule_id: Literal[
        "s-to-se-isovalent-v1", "se-to-s-isovalent-v1"
    ]


def _bare_element(site: Any) -> str | None:
    if not site.is_ordered:
        return None
    specie = site.specie
    element = getattr(specie, "element", specie)
    symbol = getattr(element, "symbol", None)
    return symbol if isinstance(symbol, str) else None


def equivalent_site_groups(structure: Structure) -> tuple[tuple[int, ...], ...]:
    """Return canonical symmetry-equivalence groups used by the executor request."""

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        symmetrized = SpacegroupAnalyzer(
            structure,
            symprec=EQUIVALENT_SITE_SYMPREC,
            angle_tolerance=EQUIVALENT_SITE_ANGLE_TOLERANCE,
        ).get_symmetrized_structure()
    groups = tuple(
        sorted(tuple(sorted(int(index) for index in group)) for group in symmetrized.equivalent_indices)
    )
    if not groups or {index for group in groups for index in group} != set(
        range(len(structure))
    ):
        raise ValueError("symmetry analysis did not partition every structure site")
    return groups


def compile_registered_substitution_plans(
    *,
    candidate_id: str,
    database_candidate: DatabaseCandidateV1,
    parent_structure: Structure,
    parent_artifact_bytes: bytes,
    substitution_rule_id: str,
    operator_registry: SoftChemOperatorRegistryV1 = (
        DEFAULT_SOFTCHEM_OPERATOR_REGISTRY_V1
    ),
    substitution_registry: SubstitutionRegistryV1 = DEFAULT_SUBSTITUTION_REGISTRY_V1,
) -> tuple[TransformationPlanV1, ...]:
    """Compile every eligible complete equivalence class for one pinned rule."""

    operator_registry.resolve(
        substitution_registry.operator_id, substitution_registry.operator_version
    )
    rule = next(
        (item for item in substitution_registry.rules if item.rule_id == substitution_rule_id),
        None,
    )
    if rule is None:
        raise KeyError(f"substitution rule is not registered: {substitution_rule_id}")
    digest = hashlib.sha256(parent_artifact_bytes).hexdigest()
    if digest != database_candidate.structure_artifact_sha256:
        raise ValueError("parent CIF bytes differ from the database-candidate artifact hash")
    parent_pointer = ArtifactPointerV1(
        uri=database_candidate.structure_artifact_uri,
        sha256=digest,
        size_bytes=len(parent_artifact_bytes),
        media_type=STRUCTURE_ARTIFACT_MEDIA_TYPE,
    )
    plans: list[TransformationPlanV1] = []
    for group in equivalent_site_groups(parent_structure):
        species = tuple(_bare_element(parent_structure[index]) for index in group)
        if not species or any(item != rule.source_element for item in species):
            continue
        parameters = SubstitutionParametersV1(
            equivalent_site_indices=group,
            source_species=rule.source_element,
            target_species=rule.target_element,
        )
        route_sha256 = transformation_route_sha256(
            parent_structure_id=database_candidate.canonical_structure_id,
            operator_id=substitution_registry.operator_id,
            operator_version=substitution_registry.operator_version,
            parameters=parameters,
        )
        plans.append(
            TransformationPlanV1(
                plan_id=deterministic_id(
                    "plan",
                    {"candidate_id": candidate_id, "route_sha256": route_sha256},
                ),
                parent_candidate_id=database_candidate.database_candidate_id,
                parent_structure_id=database_candidate.canonical_structure_id,
                parent_structure_artifact=parent_pointer,
                operator_id=substitution_registry.operator_id,
                operator_version=substitution_registry.operator_version,
                parameters=parameters,
                preserved_features=(
                    "lattice vectors and fractional coordinates",
                    "complete crystallographic equivalence classes",
                ),
                changed_features=(
                    f"{rule.source_element}-to-{rule.target_element} species on sites {group}",
                ),
                falsification_tests=(
                    "SMACT oxidation and charge prior",
                    "registered structure-integrity validation suite",
                    "downstream relaxation and target-property calculation",
                ),
                bridge_packet_ids=("generic-research-mechanism-v1",),
                route_sha256=route_sha256,
                status=TransformationStatus.PLANNED,
            )
        )
    return tuple(plans)


def execution_request_from_compiled_plan(
    plan: TransformationPlanV1,
    *,
    parent_structure: Structure,
    substitution_registry: SubstitutionRegistryV1 = DEFAULT_SUBSTITUTION_REGISTRY_V1,
) -> SubstitutionExecutionRequestV1:
    """Turn a compiled graph plan into the existing executor's strict request."""

    rule = next(
        (
            item
            for item in substitution_registry.rules
            if item.source_element == plan.parameters.source_species
            and item.target_element == plan.parameters.target_species
        ),
        None,
    )
    if rule is None:
        raise ValueError("compiled plan no longer resolves in the substitution registry")
    registry_payload = substitution_registry_bytes(substitution_registry)
    output_elements = {
        _bare_element(site) for site in parent_structure if _bare_element(site) is not None
    }
    output_elements.discard(rule.source_element)
    output_elements.add(rule.target_element)
    return SubstitutionExecutionRequestV1(
        plan=plan,
        registry_artifact=ArtifactPointerV1(
            uri="artifact://inspiration/registry/substitutions-v1.json",
            sha256=hashlib.sha256(registry_payload).hexdigest(),
            size_bytes=len(registry_payload),
            media_type="application/json",
        ),
        equivalent_site_groups=equivalent_site_groups(parent_structure),
        allowed_output_elements=tuple(sorted(output_elements)),
    )


class OperatorPlanningToolState:
    """Bounded compiler tool with checkpointable plans and rejection receipts."""

    def __init__(
        self,
        *,
        store: LocalArtifactStore,
        database_candidates_snapshot: Callable[[], tuple[DatabaseCandidateV1, ...]],
        max_calls: int = 16,
        operator_registry: SoftChemOperatorRegistryV1 = (
            DEFAULT_SOFTCHEM_OPERATOR_REGISTRY_V1
        ),
        substitution_registry: SubstitutionRegistryV1 = (
            DEFAULT_SUBSTITUTION_REGISTRY_V1
        ),
    ) -> None:
        if not 1 <= max_calls <= 32:
            raise ValueError("max_calls must be between 1 and 32")
        self.store = store
        self.database_candidates_snapshot = database_candidates_snapshot
        self.max_calls = max_calls
        self.operator_registry = operator_registry
        self.substitution_registry = substitution_registry
        self._calls = 0
        self._plans: dict[str, TransformationPlanV1] = {}
        self._bindings: dict[str, TransformationPlanBindingV1] = {}
        self._rejections: list[TransformationCompileRejectionV1] = []
        self._lock = threading.Lock()

    def as_tool(self) -> DeepSeekFunctionTool:
        return DeepSeekFunctionTool(
            name="compile_registered_substitution",
            description=(
                "Compile a minimal element substitution against the hash-pinned operator "
                "and chemistry-rule registries. The tool chooses complete symmetry-"
                "equivalent site groups and returns executable plan IDs; it never accepts "
                "free coordinates or arbitrary code. Registered v1 rules are "
                "s-to-se-isovalent-v1 and se-to-s-isovalent-v1."
            ),
            arguments_model=CompileRegisteredSubstitutionArgsV1,
            handler=self._handle,
        )

    def audit_snapshot(self) -> RegisteredTransformationAuditV1:
        with self._lock:
            return RegisteredTransformationAuditV1(
                registry_status="HASH_PINNED",
                operator_registry_sha256=softchem_registry_sha256(
                    self.operator_registry
                ),
                substitution_registry_sha256=substitution_registry_sha256(
                    self.substitution_registry
                ),
                compile_attempt_count=self._calls,
                plans=tuple(sorted(self._plans.values(), key=lambda item: item.plan_id)),
                bindings=tuple(
                    sorted(self._bindings.values(), key=lambda item: item.plan_id)
                ),
                rejections=tuple(self._rejections),
            )

    def checkpoint_snapshot(self) -> Mapping[str, Any]:
        return self.audit_snapshot().model_dump(mode="json")

    def restore_snapshot(self, value: object) -> None:
        audit = RegisteredTransformationAuditV1.model_validate_json(
            canonical_json_bytes(value)
        )
        if audit.registry_status != "HASH_PINNED":
            raise ValueError("operator-planning checkpoint must bind registries")
        if audit.operator_registry_sha256 != softchem_registry_sha256(
            self.operator_registry
        ) or audit.substitution_registry_sha256 != substitution_registry_sha256(
            self.substitution_registry
        ):
            raise ValueError("operator-planning checkpoint registry hash mismatch")
        with self._lock:
            if self._calls or self._plans or self._bindings or self._rejections:
                raise ValueError("operator-planning state must be empty before restore")
            self._calls = audit.compile_attempt_count
            self._plans = {item.plan_id: item for item in audit.plans}
            self._bindings = {item.plan_id: item for item in audit.bindings}
            self._rejections = list(audit.rejections)

    def _handle(
        self, arguments: CompileRegisteredSubstitutionArgsV1
    ) -> Mapping[str, Any]:
        with self._lock:
            if self._calls >= self.max_calls:
                raise LLMProviderError(
                    "BUDGET_EXHAUSTED",
                    "operator compiler exhausted its call budget",
                    retryable=False,
                )
            self._calls += 1
        candidates = {
            item.database_candidate_id: item
            for item in self.database_candidates_snapshot()
        }
        parent = candidates.get(arguments.database_candidate_id)
        if parent is None:
            return self._reject(arguments, "UNKNOWN_DATABASE_CANDIDATE")
        if not any(
            item.rule_id == arguments.substitution_rule_id
            for item in self.substitution_registry.rules
        ):
            return self._reject(arguments, "UNREGISTERED_SUBSTITUTION_RULE")
        try:
            payload = self.store.read_bytes(parent.structure_artifact_uri)
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                structure = Structure.from_str(payload.decode("utf-8"), fmt="cif")
            plans = compile_registered_substitution_plans(
                candidate_id=arguments.candidate_id,
                database_candidate=parent,
                parent_structure=structure,
                parent_artifact_bytes=payload,
                substitution_rule_id=arguments.substitution_rule_id,
                operator_registry=self.operator_registry,
                substitution_registry=self.substitution_registry,
            )
        except (KeyError, UnicodeDecodeError, ValueError) as exc:
            return self._reject(
                arguments,
                "PARENT_OR_ROUTE_INTEGRITY_FAILURE",
                detail=type(exc).__name__,
            )
        if not plans:
            return self._reject(arguments, "NO_COMPLETE_EQUIVALENCE_CLASS")
        with self._lock:
            self._plans.update((item.plan_id, item) for item in plans)
            self._bindings.update(
                (
                    item.plan_id,
                    TransformationPlanBindingV1(
                        candidate_id=arguments.candidate_id,
                        plan_id=item.plan_id,
                    ),
                )
                for item in plans
            )
        return {
            "status": "COMPILED",
            "candidate_id": arguments.candidate_id,
            "database_candidate_id": arguments.database_candidate_id,
            "operator_registry_sha256": softchem_registry_sha256(
                self.operator_registry
            ),
            "substitution_registry_sha256": substitution_registry_sha256(
                self.substitution_registry
            ),
            "plans": tuple(item.model_dump(mode="json") for item in plans),
            "execution_boundary": "PLANNED_NOT_EXECUTED_OR_PROPERTY_VERIFIED",
        }

    def _reject(
        self,
        arguments: CompileRegisteredSubstitutionArgsV1,
        reason_code: str,
        *,
        detail: str | None = None,
    ) -> Mapping[str, Any]:
        rejection = TransformationCompileRejectionV1(
            candidate_id=arguments.candidate_id,
            database_candidate_id=arguments.database_candidate_id,
            substitution_rule_id=arguments.substitution_rule_id,
            reason_code=reason_code,
        )
        with self._lock:
            self._rejections.append(rejection)
        return {
            "status": "REJECTED",
            "reason_code": reason_code,
            "detail": detail,
            "registered_rule_ids": tuple(
                item.rule_id for item in self.substitution_registry.rules
            ),
            "scientific_conclusion": False,
        }


def planning_state_sha256(state: OperatorPlanningToolState) -> str:
    """Stable helper for audit tests and downstream cache keys."""

    return hashlib.sha256(canonical_json_bytes(state.checkpoint_snapshot())).hexdigest()
