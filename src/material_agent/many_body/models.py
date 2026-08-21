"""Strict, Orchestrator-independent Agent04 domain contracts (task 1)."""

from __future__ import annotations

import json
import math
import re
from enum import StrEnum
from hashlib import sha256
from typing import Annotated, Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)

AGENT04_CONTRACT_VERSION = "agent04-many-body-domain-v1"
Sha256 = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, validate_assignment=True, allow_inf_nan=False)


def _unique(values: tuple[str, ...], label: str) -> tuple[str, ...]:
    if any(not value.strip() for value in values) or len(set(values)) != len(values):
        raise ValueError(f"{label} must contain non-empty unique IDs")
    return values


class ArtifactRef(StrictModel):
    uri: str = Field(min_length=1)
    sha256: Sha256

    @field_validator("uri")
    @classmethod
    def safe_uri(cls, value: str) -> str:
        if value.startswith(("/", "\\")) or re.match(r"^[A-Za-z]:[\\/]", value):
            raise ValueError("artifact URI must not be an absolute path")
        normalized = value.replace("\\", "/")
        if ".." in normalized.split("/"):
            raise ValueError("artifact URI must not contain path traversal")
        if not re.match(r"^[A-Za-z][A-Za-z0-9+.-]*://[^\s/]+(?:/[^\s]*)?$", value):
            raise ValueError("artifact URI must be a logical URI")
        if value.lower().startswith("file://"):
            raise ValueError("local file URI is not allowed")
        if value.lower().endswith((".pkl", ".pickle")):
            raise ValueError("pickle artifacts are not allowed")
        return value


class EvidenceLevel(StrEnum):
    L0_PARSED = "L0_PARSED"
    L1_RETRIEVED = "L1_RETRIEVED"
    L2_ML_SCREENED = "L2_ML_SCREENED"
    L3_DFT_VALIDATED = "L3_DFT_VALIDATED"
    L4_MANY_BODY_VALIDATED = "L4_MANY_BODY_VALIDATED"
    L5_EXPERT_REVIEWED = "L5_EXPERT_REVIEWED"


class EvidenceScope(StrEnum):
    SOLVER_BENCHMARK = "SOLVER_BENCHMARK"
    ABSTRACT_MODEL = "ABSTRACT_MODEL"
    FINITE_CLUSTER = "FINITE_CLUSTER"
    EFFECTIVE_MODEL_FOR_CANDIDATE = "EFFECTIVE_MODEL_FOR_CANDIDATE"
    MATERIAL_CANDIDATE = "MATERIAL_CANDIDATE"


class ModelDefinitionStatus(StrEnum):
    UNVALIDATED = "UNVALIDATED"
    INVALID_SCHEMA = "INVALID_SCHEMA"
    MISSING_REQUIRED_PHYSICS = "MISSING_REQUIRED_PHYSICS"
    INTERNALLY_INCONSISTENT = "INTERNALLY_INCONSISTENT"
    VALIDATED_MODEL = "VALIDATED_MODEL"


class SolverValidationStatus(StrEnum):
    NOT_RUN = "NOT_RUN"
    MOCK_ONLY = "MOCK_ONLY"
    NOT_APPLICABLE = "NOT_APPLICABLE"
    RESOURCE_REJECTED = "RESOURCE_REJECTED"
    RUNNING = "RUNNING"
    NUMERICALLY_UNCONVERGED = "NUMERICALLY_UNCONVERGED"
    NUMERICALLY_INVALID = "NUMERICALLY_INVALID"
    NUMERICALLY_VALIDATED = "NUMERICALLY_VALIDATED"
    BENCHMARK_VALIDATED = "BENCHMARK_VALIDATED"


class MaterialLinkageStatus(StrEnum):
    NONE = "NONE"
    DECLARED_ONLY = "DECLARED_ONLY"
    PARTIAL_PROVENANCE = "PARTIAL_PROVENANCE"
    TRACEABLE_UNREVIEWED = "TRACEABLE_UNREVIEWED"
    EXPERT_APPROVED = "EXPERT_APPROVED"
    INVALIDATED = "INVALIDATED"


class ModelFamily(StrEnum):
    SINGLE_BAND_HUBBARD = "SINGLE_BAND_HUBBARD"
    MULTI_ORBITAL_HUBBARD_KANAMORI = "MULTI_ORBITAL_HUBBARD_KANAMORI"
    EXTENDED_HUBBARD = "EXTENDED_HUBBARD"
    SPIN_MODEL = "SPIN_MODEL"
    IMPURITY_MODEL = "IMPURITY_MODEL"
    CUSTOM_DECLARED = "CUSTOM_DECLARED"


class BoundaryCondition(StrEnum):
    OPEN = "OPEN"
    PERIODIC = "PERIODIC"


class GeometryType(StrEnum):
    FINITE_GRAPH = "FINITE_GRAPH"
    PERIODIC_LATTICE = "PERIODIC_LATTICE"


class Edge(StrictModel):
    edge_id: str = Field(min_length=1)
    source_site: str = Field(min_length=1)
    target_site: str = Field(min_length=1)
    wrap: tuple[int, ...] = ()


class Geometry(StrictModel):
    schema_version: Literal["agent04-geometry-v1"] = "agent04-geometry-v1"
    geometry_id: str = Field(min_length=1)
    geometry_type: GeometryType
    dimension: Literal[1, 2, 3]
    num_sites: int = Field(ge=1)
    site_ids: tuple[str, ...] = Field(min_length=1)
    edges: tuple[Edge, ...] = ()
    boundary_condition: BoundaryCondition
    coordinates: tuple[tuple[float, ...], ...] | None = None

    @model_validator(mode="after")
    def validate_graph(self) -> Geometry:
        _unique(self.site_ids, "site_ids")
        if len(self.site_ids) != self.num_sites:
            raise ValueError("site_ids must have num_sites entries")
        edge_ids = _unique(tuple(edge.edge_id for edge in self.edges), "edge_ids")
        del edge_ids
        sites = set(self.site_ids)
        if any(edge.source_site not in sites or edge.target_site not in sites for edge in self.edges):
            raise ValueError("edge references an unknown site")
        if self.coordinates is not None and len(self.coordinates) != self.num_sites:
            raise ValueError("coordinates must have one entry per site")
        if self.geometry_type is GeometryType.FINITE_GRAPH and not self.edges:
            raise ValueError("finite graph requires explicit edges")
        return self


class BasisOrbital(StrictModel):
    orbital_id: str = Field(min_length=1)
    site_id: str = Field(min_length=1)
    label: str = Field(min_length=1)
    active: bool = True


class BasisDefinition(StrictModel):
    schema_version: Literal["agent04-basis-v1"] = "agent04-basis-v1"
    basis_id: str = Field(min_length=1)
    orbitals: tuple[BasisOrbital, ...] = Field(min_length=1)
    spin_convention: Literal["UP_DOWN", "NONE"]
    fock_ordering: Literal["SITE_ORBITAL_SPIN", "ALL_UP_THEN_ALL_DOWN"]
    index_base: Literal[0, 1]
    conventions_version: str = Field(min_length=1)
    complex_phase_convention: str = Field(min_length=1)
    includes_soc: bool = False
    nambu_doubling: bool = False

    @model_validator(mode="after")
    def validate_basis(self) -> BasisDefinition:
        _unique(tuple(item.orbital_id for item in self.orbitals), "orbital_ids")
        if self.spin_convention == "UP_DOWN" and self.fock_ordering != "ALL_UP_THEN_ALL_DOWN":
            raise ValueError("UP_DOWN v1 basis must freeze ALL_UP_THEN_ALL_DOWN ordering")
        return self


class OnsiteTerm(StrictModel):
    site_id: str = Field(min_length=1)
    orbital_id: str = Field(min_length=1)
    value: float
    unit: Literal["eV"]


class HoppingTerm(StrictModel):
    term_id: str = Field(min_length=1)
    source_orbital: str = Field(min_length=1)
    target_orbital: str = Field(min_length=1)
    value: float
    unit: Literal["eV"]
    directed: bool = True


class OneBodyHamiltonian(StrictModel):
    schema_version: Literal["agent04-one-body-v1"] = "agent04-one-body-v1"
    hamiltonian_id: str = Field(min_length=1)
    energy_unit: Literal["eV"]
    onsite_terms: tuple[OnsiteTerm, ...] = ()
    hopping_terms: tuple[HoppingTerm, ...] = Field(min_length=1)
    is_complex: bool = False
    stores_hermitian_conjugate: bool = False
    sparse_format: Literal["COO_JSON", "CSR_ARTIFACT"]
    matrix_artifact: ArtifactRef | None = None
    numerical_zero_tolerance: float = Field(gt=0)

    @model_validator(mode="after")
    def validate_terms(self) -> OneBodyHamiltonian:
        _unique(tuple(item.term_id for item in self.hopping_terms), "hopping term IDs")
        if self.is_complex:
            raise ValueError("v1 domain schema does not accept complex hopping values")
        if self.sparse_format == "CSR_ARTIFACT" and self.matrix_artifact is None:
            raise ValueError("CSR_ARTIFACT requires matrix_artifact")
        return self


class InteractionKind(StrEnum):
    ONSITE_HUBBARD_U = "ONSITE_HUBBARD_U"
    KANAMORI = "KANAMORI"
    DENSITY_DENSITY = "DENSITY_DENSITY"
    NONLOCAL_DENSITY_V = "NONLOCAL_DENSITY_V"
    HEISENBERG_J = "HEISENBERG_J"
    CUSTOM_TENSOR = "CUSTOM_TENSOR"


class InteractionTerm(StrictModel):
    schema_version: Literal["agent04-interaction-v1"] = "agent04-interaction-v1"
    term_id: str = Field(min_length=1)
    kind: InteractionKind
    site_ids: tuple[str, ...] = Field(min_length=1)
    orbital_ids: tuple[str, ...] = ()
    value: float
    unit: Literal["eV"]
    index_convention: str = Field(min_length=1)
    density_density: bool
    spin_flip: bool = False
    pair_hopping: bool = False
    provenance_id: str = Field(min_length=1)
    parameter_artifact: ArtifactRef | None = None

    @model_validator(mode="after")
    def validate_interaction(self) -> InteractionTerm:
        _unique(self.site_ids, "interaction site_ids")
        if self.kind is InteractionKind.ONSITE_HUBBARD_U and len(self.site_ids) != 1:
            raise ValueError("onsite Hubbard U must target exactly one site")
        return self


class StatePoint(StrictModel):
    schema_version: Literal["agent04-state-point-v1"] = "agent04-state-point-v1"
    state_point_id: str = Field(min_length=1)
    ensemble: Literal["CANONICAL", "GRAND_CANONICAL"]
    temperature_definition: Literal["ZERO_T", "BETA", "KELVIN"]
    n_up: int | None = Field(default=None, ge=0)
    n_down: int | None = Field(default=None, ge=0)
    n_total: int | None = Field(default=None, ge=0)
    sz: float | None = None
    beta: float | None = Field(default=None, gt=0)
    temperature: float | None = Field(default=None, ge=0)
    chemical_potential: float | None = None
    unit: Literal["eV"] = "eV"
    provenance_id: str = Field(min_length=1)

    @model_validator(mode="after")
    def validate_state(self) -> StatePoint:
        if self.ensemble == "CANONICAL" and (self.n_up is None or self.n_down is None):
            raise ValueError("canonical state requires n_up and n_down")
        if self.ensemble == "GRAND_CANONICAL" and self.chemical_potential is None:
            raise ValueError("grand canonical state requires chemical_potential")
        if self.temperature_definition == "ZERO_T" and (self.beta is not None or self.temperature not in (None, 0)):
            raise ValueError("ZERO_T cannot contain finite temperature")
        if self.n_total is not None and self.n_up is not None and self.n_down is not None and self.n_total != self.n_up + self.n_down:
            raise ValueError("n_total must equal n_up+n_down")
        if self.sz is not None and self.n_up is not None and self.n_down is not None and not math.isclose(self.sz, (self.n_up-self.n_down)/2):
            raise ValueError("sz is inconsistent with n_up and n_down")
        return self


class ProvenanceRecord(StrictModel):
    provenance_id: str = Field(min_length=1)
    source_type: Literal["FIXTURE", "USER_ASSUMPTION", "LITERATURE", "FITTED", "DFT_DERIVED", "CONSTRAINED_DFT", "CRPA", "EXPERIMENT"]
    source_uri: str | None = None
    source_artifact: ArtifactRef | None = None
    method: str = Field(min_length=1)
    software_version: str | None = None
    notes: str = ""

    @field_validator("source_uri")
    @classmethod
    def safe_source_uri(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if value.startswith(("/", "\\")) or re.match(r"^[A-Za-z]:[\\/]", value):
            raise ValueError("provenance source URI must not be an absolute path")
        if ".." in value.replace("\\", "/").split("/"):
            raise ValueError("provenance source URI must not contain path traversal")
        if value.lower().endswith((".pkl", ".pickle")):
            raise ValueError("pickle provenance sources are not allowed")
        if not re.match(r"^[A-Za-z][A-Za-z0-9+.-]*:", value):
            raise ValueError("provenance source URI must have an explicit scheme")
        return value


class ModelMaterialLinkage(StrictModel):
    schema_version: Literal["agent04-material-linkage-v1"] = "agent04-material-linkage-v1"
    status: MaterialLinkageStatus
    candidate_id: str | None = None
    source_material_id: str | None = None
    structure_id: str | None = None
    structure_artifact: ArtifactRef | None = None
    dft_task_id: str | None = None
    electronic_structure_artifact: ArtifactRef | None = None
    wannier_artifact: ArtifactRef | None = None
    hamiltonian_artifact: ArtifactRef | None = None
    linkage_policy_version: str = Field(min_length=1)
    approval_id: str | None = None

    @model_validator(mode="after")
    def linkage_guard(self) -> ModelMaterialLinkage:
        if self.status is MaterialLinkageStatus.NONE:
            if any(value is not None for value in (self.candidate_id, self.structure_id, self.structure_artifact)):
                raise ValueError("NONE linkage cannot contain material references")
        elif not self.candidate_id or not self.structure_id or self.structure_artifact is None:
            raise ValueError("declared linkage requires candidate, structure, and artifact")
        if self.status is MaterialLinkageStatus.EXPERT_APPROVED and not self.approval_id:
            raise ValueError("expert-approved linkage requires approval_id")
        return self


class EffectiveModelPackage(StrictModel):
    schema_version: Literal["effective-model/v1"] = "effective-model/v1"
    model_id: str = Field(min_length=1)
    revision: int = Field(ge=1)
    name: str = Field(min_length=1)
    model_family: ModelFamily
    fixture: bool
    is_mock: bool
    geometry: Geometry
    basis: BasisDefinition
    one_body: OneBodyHamiltonian
    interactions: tuple[InteractionTerm, ...] = Field(min_length=1)
    state_points: tuple[StatePoint, ...] = Field(min_length=1)
    conventions_version: str = Field(min_length=1)
    material_linkage: ModelMaterialLinkage | None = None
    provenance: tuple[ProvenanceRecord, ...] = Field(min_length=1)
    artifacts: tuple[ArtifactRef, ...] = ()
    package_hash: Sha256

    @model_validator(mode="after")
    def package_guard(self) -> EffectiveModelPackage:
        _unique(tuple(item.provenance_id for item in self.provenance), "provenance IDs")
        _unique(tuple(item.term_id for item in self.interactions), "interaction IDs")
        _unique(tuple(item.state_point_id for item in self.state_points), "state point IDs")
        if not self.is_mock and self.fixture:
            raise ValueError("fixture package must be marked is_mock=true")
        if self.fixture and any(item.source_type != "FIXTURE" for item in self.provenance):
            raise ValueError("fixture package provenance must be FIXTURE only")
        return self


class ManyBodyRequest(StrictModel):
    schema_version: Literal["many-body-request/v1"] = "many-body-request/v1"
    request_id: str = Field(min_length=1)
    revision: int = Field(default=1, ge=1)
    model_id: str = Field(min_length=1)
    model_revision: int = Field(ge=1)
    model_package: ArtifactRef
    state_point_ids: tuple[str, ...] = Field(min_length=1)
    requested_observables: tuple[str, ...] = ()
    requested_claims: tuple[str, ...] = ()
    solver_preference: str | None = None
    approval_policy_version: str = Field(min_length=1)
    routing_policy_version: str = Field(min_length=1)
    is_mock: bool

    @model_validator(mode="after")
    def request_guard(self) -> ManyBodyRequest:
        _unique(self.state_point_ids, "state_point_ids")
        _unique(self.requested_observables, "requested_observables")
        _unique(self.requested_claims, "requested_claims")
        return self


class SolverCapability(StrictModel):
    schema_version: Literal["many-body-solver-capability/v1"] = "many-body-solver-capability/v1"
    solver_id: str = Field(min_length=1)
    backend_id: str = Field(min_length=1)
    backend_version: str = Field(min_length=1)
    method_family: Literal["CONTROL_FLOW", "ED", "QMC", "DMRG", "DMFT"] = "ED"
    research_catalog_id: str | None = None
    is_mock: bool
    lifecycle: Literal["PLANNED", "AVAILABLE"]
    registered: bool = False
    executable: bool = False
    supported_model_families: tuple[ModelFamily, ...] = Field(min_length=1)
    supported_geometry_types: tuple[GeometryType, ...] = Field(min_length=1)
    supported_dimensions: tuple[int, ...] = ()
    supports_real_hopping: bool = False
    supports_complex_hopping: bool = False
    supports_soc: bool = False
    supported_interaction_kinds: tuple[InteractionKind, ...] = ()
    supported_ensembles: tuple[Literal["CANONICAL", "GRAND_CANONICAL"], ...] = ()
    supported_temperatures: tuple[Literal["ZERO_T", "FINITE_T"], ...] = ()
    supported_boundaries: tuple[BoundaryCondition, ...] = ()
    supported_observables: tuple[str, ...] = ()
    max_sites: int | None = Field(default=None, ge=1)
    max_active_orbitals: int | None = Field(default=None, ge=1)
    evidence_ceiling: EvidenceLevel = EvidenceLevel.L1_RETRIEVED
    limitations: tuple[str, ...] = ()
    registry_snapshot: ArtifactRef

    @model_validator(mode="after")
    def capability_guard(self) -> SolverCapability:
        if self.lifecycle == "PLANNED" and (self.registered or self.executable):
            raise ValueError("planned capability cannot be registered or executable")
        if self.executable and not self.registered:
            raise ValueError("executable capability must be registered")
        if self.is_mock and self.evidence_ceiling in (EvidenceLevel.L4_MANY_BODY_VALIDATED, EvidenceLevel.L5_EXPERT_REVIEWED):
            raise ValueError("mock capability cannot have an L4/L5 evidence ceiling")
        if self.method_family == "CONTROL_FLOW" and not self.is_mock:
            raise ValueError("only mock capabilities may use CONTROL_FLOW method family")
        return self


class SolverRoutingDecision(StrictModel):
    schema_version: Literal["many-body-routing-decision/v1"] = "many-body-routing-decision/v1"
    routing_decision_id: str = Field(min_length=1)
    model_package_hash: Sha256
    request_hash: Sha256
    applicable_solver_ids: tuple[str, ...] = ()
    inapplicable_solver_ids: tuple[str, ...] = ()
    recommended_solver_id: str | None = None
    status: Literal["READY", "NOT_APPLICABLE", "BLOCKED"] = "BLOCKED"
    executable: bool = False
    requires_approval: bool = False
    scientific_capability_matches: tuple[str, ...] = ()
    available_scientific_solvers: tuple[str, ...] = ()
    control_flow_simulators: tuple[str, ...] = ()
    capability_matches: dict[str, dict[str, Any]] = Field(default_factory=dict)
    reason_codes: tuple[str, ...] = ()
    field_paths: tuple[str, ...] = ()
    missing_input: tuple[str, ...] = ()
    limitations: tuple[str, ...] = ()
    remediation: tuple[str, ...] = ()
    policy_version: str = Field(min_length=1)
    registry_snapshot: ArtifactRef

    @model_validator(mode="after")
    def routing_guard(self) -> SolverRoutingDecision:
        _unique(self.applicable_solver_ids, "applicable solver IDs")
        _unique(self.inapplicable_solver_ids, "inapplicable solver IDs")
        if self.recommended_solver_id is not None and self.recommended_solver_id not in self.applicable_solver_ids:
            raise ValueError("recommended solver must be applicable")
        if self.status == "READY" and self.recommended_solver_id is None:
            raise ValueError("READY routing requires a recommended solver")
        if self.executable and not (self.available_scientific_solvers or self.control_flow_simulators):
            raise ValueError("executable routing requires an available solver or control simulator")
        return self


class ResultArtifact(StrictModel):
    artifact: ArtifactRef
    role: Literal["REPORT", "LOG", "VALIDATION", "OTHER"]


class ManyBodyResultEnvelope(StrictModel):
    schema_version: Literal["many-body-result/v1"] = "many-body-result/v1"
    request_id: str = Field(min_length=1)
    model_snapshot: ArtifactRef
    backend_id: str = Field(min_length=1)
    backend_version: str = Field(min_length=1)
    is_mock: bool
    fixture: bool
    execution_status: Literal["CREATED", "RUNNING", "SUCCEEDED", "FAILED", "CANCELLED", "TIMEOUT"]
    model_definition_status: ModelDefinitionStatus
    solver_validation_status: SolverValidationStatus
    material_linkage_status: MaterialLinkageStatus
    evidence_scope: EvidenceScope
    evidence_level: EvidenceLevel
    artifacts: tuple[ResultArtifact, ...] = ()
    provenance: tuple[ProvenanceRecord, ...] = Field(min_length=1)
    warnings: tuple[str, ...] = ()
    errors: tuple[str, ...] = ()

    @model_validator(mode="after")
    def evidence_ceiling(self) -> ManyBodyResultEnvelope:
        if self.is_mock != self.fixture and self.fixture:
            raise ValueError("fixture result must be mock")
        if self.is_mock and self.solver_validation_status is not SolverValidationStatus.MOCK_ONLY:
            raise ValueError("mock result must have MOCK_ONLY solver status")
        if self.is_mock and self.evidence_level in (EvidenceLevel.L4_MANY_BODY_VALIDATED, EvidenceLevel.L5_EXPERT_REVIEWED):
            raise ValueError("mock result cannot claim L4/L5")
        if self.evidence_scope is EvidenceScope.MATERIAL_CANDIDATE and self.material_linkage_status is not MaterialLinkageStatus.EXPERT_APPROVED:
            raise ValueError("material scope requires expert-approved linkage")
        if self.evidence_level is EvidenceLevel.L4_MANY_BODY_VALIDATED:
            if self.backend_id == "exact-diagonalization" and "planned" in self.backend_version.lower():
                raise ValueError("planned ED capability cannot claim L4_MANY_BODY_VALIDATED")
            if self.is_mock or self.fixture or self.model_definition_status is not ModelDefinitionStatus.VALIDATED_MODEL or self.solver_validation_status not in (SolverValidationStatus.NUMERICALLY_VALIDATED, SolverValidationStatus.BENCHMARK_VALIDATED) or self.material_linkage_status is not MaterialLinkageStatus.EXPERT_APPROVED:
                raise ValueError("L4 requires real, validated model/solver and expert-approved linkage")
        return self


def canonical_json(value: BaseModel | dict[str, Any], *, exclude: set[str] | None = None) -> str:
    data = value.model_dump(mode="json", exclude=exclude or set()) if isinstance(value, BaseModel) else value
    return json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def canonical_hash(value: BaseModel | dict[str, Any], *, exclude: set[str] | None = None) -> str:
    return sha256(canonical_json(value, exclude=exclude).encode("utf-8")).hexdigest()


def package_content_hash(package: EffectiveModelPackage) -> str:
    """Hash immutable package content, excluding the self-referential field."""
    return canonical_hash(package, exclude={"package_hash"})
