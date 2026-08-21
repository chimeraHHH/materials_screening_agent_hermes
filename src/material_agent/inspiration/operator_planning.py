"""Compile DeepSeek structure ideas into frozen, executable soft-chemistry plans."""

from __future__ import annotations

import hashlib
import threading
import warnings
from collections.abc import Callable, Mapping
from typing import Any, Literal

from pydantic import Field, model_validator
from pymatgen.core import Element, Structure
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
    RegisteredTransformationAuditV2,
    TransformationCompileRejectionV2,
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
    DEFAULT_SOFTCHEM_OPERATOR_REGISTRY_V2,
    SoftChemOperatorRegistryV2,
    softchem_registry_sha256,
)
from material_agent.softchem.operations import (
    INTERCALATION_RULES,
    LAYER_SLIDE_RULES,
    STRAIN_RULES,
    EquivalentSiteVacancyParametersV1,
    HomogeneousStrainParametersV1,
    RegisteredIntercalationParametersV1,
    RegisteredLayerSlideParametersV1,
    StructureOperationExecutionRequestV2,
    StructureOperationPlanV2,
    bind_compile_prior,
    evaluate_structure_operation_prior,
    largest_c_gap,
    layer_groups,
    layer_partition_sha256,
    make_operation_plan,
    preview_structure_operation,
)


class CompileRegisteredSubstitutionArgsV1(StrictModel):
    candidate_id: str = Field(pattern=r"^candidate-[a-z0-9-]{1,64}$")
    database_candidate_id: str = Field(pattern=r"^db-candidate-[0-9a-f]{24}$")
    substitution_rule_id: Literal["s-to-se-isovalent-v1", "se-to-s-isovalent-v1"]


class CompileRegisteredOperationArgsV2(StrictModel):
    candidate_id: str = Field(pattern=r"^candidate-[a-z0-9-]{1,64}$")
    database_candidate_id: str = Field(pattern=r"^db-candidate-[0-9a-f]{24}$")
    operation_kind: Literal[
        "SUBSTITUTION", "HOMOGENEOUS_STRAIN", "VACANCY", "INTERCALATION", "LAYER_SLIDE"
    ]
    rule_id: Literal[
        "s-to-se-isovalent-v1",
        "se-to-s-isovalent-v1",
        "biaxial-compress-2pct-v1",
        "biaxial-tensile-2pct-v1",
        "out-of-plane-compress-3pct-v1",
        "vacancy-chalcogen-class-v1",
        "vacancy-transition-metal-class-v1",
        "li-vdw-hollow-a-v1",
        "na-vdw-hollow-a-v1",
        "slide-a-to-b-v1",
        "slide-a-to-c-v1",
    ]

    @model_validator(mode="after")
    def rule_matches_kind(self) -> CompileRegisteredOperationArgsV2:
        allowed = {
            "SUBSTITUTION": {"s-to-se-isovalent-v1", "se-to-s-isovalent-v1"},
            "HOMOGENEOUS_STRAIN": set(STRAIN_RULES),
            "VACANCY": {
                "vacancy-chalcogen-class-v1",
                "vacancy-transition-metal-class-v1",
            },
            "INTERCALATION": set(INTERCALATION_RULES),
            "LAYER_SLIDE": set(LAYER_SLIDE_RULES),
        }
        if self.rule_id not in allowed[self.operation_kind]:
            raise ValueError("rule_id is not registered for operation_kind")
        return self


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
        sorted(
            tuple(sorted(int(index) for index in group))
            for group in symmetrized.equivalent_indices
        )
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
    operator_registry: SoftChemOperatorRegistryV2 = (
        DEFAULT_SOFTCHEM_OPERATOR_REGISTRY_V2
    ),
    substitution_registry: SubstitutionRegistryV1 = DEFAULT_SUBSTITUTION_REGISTRY_V1,
) -> tuple[TransformationPlanV1, ...]:
    """Compile every eligible complete equivalence class for one pinned rule."""

    operator_registry.resolve(
        substitution_registry.operator_id, substitution_registry.operator_version
    )
    rule = next(
        (
            item
            for item in substitution_registry.rules
            if item.rule_id == substitution_rule_id
        ),
        None,
    )
    if rule is None:
        raise KeyError(f"substitution rule is not registered: {substitution_rule_id}")
    digest = hashlib.sha256(parent_artifact_bytes).hexdigest()
    if digest != database_candidate.structure_artifact_sha256:
        raise ValueError(
            "parent CIF bytes differ from the database-candidate artifact hash"
        )
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


def _parent_pointer(
    database_candidate: DatabaseCandidateV1,
    parent_artifact_bytes: bytes,
) -> ArtifactPointerV1:
    digest = hashlib.sha256(parent_artifact_bytes).hexdigest()
    if digest != database_candidate.structure_artifact_sha256:
        raise ValueError(
            "parent CIF bytes differ from the database-candidate artifact hash"
        )
    return ArtifactPointerV1(
        uri=database_candidate.structure_artifact_uri,
        sha256=digest,
        size_bytes=len(parent_artifact_bytes),
        media_type=STRUCTURE_ARTIFACT_MEDIA_TYPE,
    )


def compile_registered_structure_operation_plans(
    *,
    candidate_id: str,
    database_candidate: DatabaseCandidateV1,
    parent_structure: Structure,
    parent_artifact_bytes: bytes,
    operation_kind: str,
    rule_id: str,
    operator_registry: SoftChemOperatorRegistryV2 = DEFAULT_SOFTCHEM_OPERATOR_REGISTRY_V2,
) -> tuple[StructureOperationPlanV2, ...]:
    """Derive every coordinate/site selection locally for one registered v2 rule."""

    pointer = _parent_pointer(database_candidate, parent_artifact_bytes)
    common = {
        "candidate_id": candidate_id,
        "parent_candidate_id": database_candidate.database_candidate_id,
        "parent_structure_id": database_candidate.canonical_structure_id,
        "parent_pointer": pointer,
    }
    plans: list[StructureOperationPlanV2] = []
    if operation_kind == "HOMOGENEOUS_STRAIN":
        operator_registry.resolve("APPLY_HOMOGENEOUS_STRAIN_V1", "1")
        parameters = HomogeneousStrainParametersV1(
            rule_id=rule_id,
            deformation_matrix=STRAIN_RULES[rule_id],
        )
        plans.append(
            make_operation_plan(
                **common,
                operator_id="APPLY_HOMOGENEOUS_STRAIN_V1",
                parameters=parameters,
                preserved_features=(
                    "composition",
                    "site count",
                    "fractional coordinates",
                ),
                changed_features=(f"lattice by registered rule {rule_id}",),
            )
        )
    elif operation_kind == "VACANCY":
        operator_registry.resolve("REMOVE_EQUIVALENT_SITE_CLASS_V1", "1")
        for group in equivalent_site_groups(parent_structure):
            species = {_bare_element(parent_structure[index]) for index in group}
            if len(species) != 1 or None in species:
                continue
            symbol = next(iter(species))
            assert symbol is not None
            element = Element(symbol)
            selected = (
                rule_id == "vacancy-transition-metal-class-v1"
                and element.is_transition_metal
            ) or (
                rule_id == "vacancy-chalcogen-class-v1"
                and symbol in {"O", "S", "Se", "Te"}
            )
            if not selected:
                continue
            parameters = EquivalentSiteVacancyParametersV1(
                rule_id=rule_id,
                equivalent_site_indices=group,
                removed_species=symbol,
            )
            plans.append(
                make_operation_plan(
                    **common,
                    operator_id="REMOVE_EQUIVALENT_SITE_CLASS_V1",
                    parameters=parameters,
                    preserved_features=("lattice", "all unselected sites"),
                    changed_features=(
                        f"remove complete {symbol} equivalence class {group}",
                    ),
                )
            )
    elif operation_kind == "INTERCALATION":
        operator_registry.resolve("INTERCALATE_REGISTERED_SITE_V1", "1")
        midpoint, gap = largest_c_gap(parent_structure)
        if gap < 3.0:
            return ()
        species, site_rule, xy = INTERCALATION_RULES[rule_id]
        parameters = RegisteredIntercalationParametersV1(
            rule_id=rule_id,
            intercalant=species,
            site_rule_id=site_rule,
            insertion_frac_coords=(*xy, midpoint),
        )
        plans.append(
            make_operation_plan(
                **common,
                operator_id="INTERCALATE_REGISTERED_SITE_V1",
                parameters=parameters,
                preserved_features=("host lattice", "all host sites"),
                changed_features=(
                    f"insert one {species} at derived {site_rule} in largest c gap",
                ),
            )
        )
    elif operation_kind == "LAYER_SLIDE":
        operator_registry.resolve("SLIDE_REGISTERED_LAYER_V1", "1")
        groups = layer_groups(parent_structure)
        if len(groups) < 2:
            return ()
        selected_group = groups[-1]
        parameters = RegisteredLayerSlideParametersV1(
            rule_id=rule_id,
            layer_site_indices=selected_group,
            translation_fractional_ab=LAYER_SLIDE_RULES[rule_id],
            layer_partition_sha256=layer_partition_sha256(groups),
        )
        plans.append(
            make_operation_plan(
                **common,
                operator_id="SLIDE_REGISTERED_LAYER_V1",
                parameters=parameters,
                preserved_features=(
                    "composition",
                    "lattice",
                    "site count",
                    "intralayer geometry",
                ),
                changed_features=(
                    f"translate derived layer {selected_group} by registered vector",
                ),
            )
        )
    else:
        raise KeyError(
            f"unsupported v2 structure operation: {operation_kind}/{rule_id}"
        )
    return tuple(plans)


def execution_request_from_compiled_operation_plan(
    plan: StructureOperationPlanV2,
    *,
    operator_registry: SoftChemOperatorRegistryV2 = DEFAULT_SOFTCHEM_OPERATOR_REGISTRY_V2,
) -> StructureOperationExecutionRequestV2:
    payload = canonical_json_bytes(operator_registry)
    return StructureOperationExecutionRequestV2(
        plan=plan,
        operator_registry_artifact=ArtifactPointerV1(
            uri="artifact://softchem/registry/operators-v2.json",
            sha256=hashlib.sha256(payload).hexdigest(),
            size_bytes=len(payload),
            media_type="application/json",
        ),
    )


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
        raise ValueError(
            "compiled plan no longer resolves in the substitution registry"
        )
    registry_payload = substitution_registry_bytes(substitution_registry)
    output_elements = {
        _bare_element(site)
        for site in parent_structure
        if _bare_element(site) is not None
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
        operator_registry: SoftChemOperatorRegistryV2 = (
            DEFAULT_SOFTCHEM_OPERATOR_REGISTRY_V2
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
        self._plans: dict[str, TransformationPlanV1 | StructureOperationPlanV2] = {}
        self._bindings: dict[str, TransformationPlanBindingV1] = {}
        self._rejections: list[TransformationCompileRejectionV2] = []
        self._lock = threading.Lock()

    def as_tool(self) -> DeepSeekFunctionTool:
        return DeepSeekFunctionTool(
            name="compile_registered_operation",
            description=(
                "Compile a minimal structure operation against hash-pinned registries. "
                "Choose only operation_kind and rule_id. Local code derives complete "
                "symmetry classes, layers, vdW-gap centers, and coordinates from the CIF. "
                "The tool accepts no free coordinates, arbitrary species, or code. It "
                "supports substitution, bounded homogeneous strain, complete-class "
                "vacancy, registered Li/Na gap intercalation, and registered layer slide."
            ),
            arguments_model=CompileRegisteredOperationArgsV2,
            handler=self._handle,
        )

    def audit_snapshot(self) -> RegisteredTransformationAuditV2:
        with self._lock:
            return RegisteredTransformationAuditV2(
                registry_status="HASH_PINNED",
                operator_registry_sha256=softchem_registry_sha256(
                    self.operator_registry
                ),
                substitution_registry_sha256=substitution_registry_sha256(
                    self.substitution_registry
                ),
                compile_attempt_count=self._calls,
                plans=tuple(
                    sorted(self._plans.values(), key=lambda item: item.plan_id)
                ),
                bindings=tuple(
                    sorted(self._bindings.values(), key=lambda item: item.plan_id)
                ),
                rejections=tuple(self._rejections),
            )

    def checkpoint_snapshot(self) -> Mapping[str, Any]:
        return self.audit_snapshot().model_dump(mode="json")

    def restore_snapshot(self, value: object) -> None:
        audit = RegisteredTransformationAuditV2.model_validate_json(
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
        self,
        arguments: CompileRegisteredOperationArgsV2
        | CompileRegisteredSubstitutionArgsV1,
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
        legacy = isinstance(arguments, CompileRegisteredSubstitutionArgsV1)
        operation_kind = "SUBSTITUTION" if legacy else arguments.operation_kind
        rule_id = arguments.substitution_rule_id if legacy else arguments.rule_id
        if operation_kind == "SUBSTITUTION" and not any(
            item.rule_id == rule_id for item in self.substitution_registry.rules
        ):
            return self._reject(arguments, "UNREGISTERED_SUBSTITUTION_RULE")
        try:
            payload = self.store.read_bytes(parent.structure_artifact_uri)
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                structure = Structure.from_str(payload.decode("utf-8"), fmt="cif")
            if operation_kind == "SUBSTITUTION":
                plans = compile_registered_substitution_plans(
                    candidate_id=arguments.candidate_id,
                    database_candidate=parent,
                    parent_structure=structure,
                    parent_artifact_bytes=payload,
                    substitution_rule_id=rule_id,
                    operator_registry=self.operator_registry,
                    substitution_registry=self.substitution_registry,
                )
            else:
                plans = compile_registered_structure_operation_plans(
                    candidate_id=arguments.candidate_id,
                    database_candidate=parent,
                    parent_structure=structure,
                    parent_artifact_bytes=payload,
                    operation_kind=operation_kind,
                    rule_id=rule_id,
                    operator_registry=self.operator_registry,
                )
        except (KeyError, UnicodeDecodeError, ValueError) as exc:
            return self._reject(
                arguments,
                "PARENT_OR_ROUTE_INTEGRITY_FAILURE",
                detail=type(exc).__name__,
            )
        if not plans:
            reason = {
                "LAYER_SLIDE": "NO_MULTILAYER_PARTITION",
                "INTERCALATION": "NO_QUALIFYING_VDW_GAP",
            }.get(operation_kind, "NO_COMPLETE_EQUIVALENCE_CLASS")
            return self._reject(arguments, reason)
        compile_priors: dict[str, Mapping[str, Any]] = {}
        accepted_plans: list[TransformationPlanV1 | StructureOperationPlanV2] = []
        rejected_prior_reasons: list[str] = []
        try:
            for plan in plans:
                if isinstance(plan, StructureOperationPlanV2):
                    preview = preview_structure_operation(plan, structure)
                    prior = evaluate_structure_operation_prior(
                        plan,
                        parent_structure=structure,
                        proposed_structure=preview,
                    )
                    compile_priors[plan.plan_id] = prior.model_dump(mode="json")
                    if prior.decision.value == "REJECT":
                        rejected_prior_reasons.extend(prior.reason_codes)
                        continue
                    spec = self.operator_registry.resolve(
                        plan.operator_id, plan.operator_version
                    )
                    plan = bind_compile_prior(
                        plan,
                        prior,
                        validator_ids=spec.validator_ids,
                    )
                accepted_plans.append(plan)
        except (KeyError, TypeError, ValueError) as exc:
            return self._reject(
                arguments,
                "OPERATION_PREFLIGHT_INTEGRITY_FAILURE",
                detail=type(exc).__name__,
            )
        plans = tuple(accepted_plans)
        if not plans:
            return self._reject(
                arguments,
                "OPERATION_PRIOR_REJECTED",
                detail=",".join(sorted(set(rejected_prior_reasons))),
            )
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
            "compile_priors": compile_priors,
            "execution_boundary": "PLANNED_NOT_EXECUTED_OR_PROPERTY_VERIFIED",
        }

    def _reject(
        self,
        arguments: CompileRegisteredOperationArgsV2
        | CompileRegisteredSubstitutionArgsV1,
        reason_code: str,
        *,
        detail: str | None = None,
    ) -> Mapping[str, Any]:
        rejection = TransformationCompileRejectionV2(
            candidate_id=arguments.candidate_id,
            database_candidate_id=arguments.database_candidate_id,
            operation_rule_id=(
                arguments.substitution_rule_id
                if isinstance(arguments, CompileRegisteredSubstitutionArgsV1)
                else arguments.rule_id
            ),
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
