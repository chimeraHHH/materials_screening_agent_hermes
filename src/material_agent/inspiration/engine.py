"""Production adapter for the single pinned inspiration substitution route.

This adapter contains no model generation, free coordinates, or arbitrary
operator selection.  It deterministically maps one search-supported curated
bridge and each eligible parent to the registry-pinned S-to-Se equivalent-site
substitution implemented in :mod:`material_agent.inspiration.transformations`.
"""

from __future__ import annotations

import hashlib
import warnings
from dataclasses import dataclass

from pydantic import ValidationError
from pymatgen.analysis.structure_matcher import SpeciesComparator, StructureMatcher
from pymatgen.core import Structure
from pymatgen.symmetry.analyzer import SpacegroupAnalyzer

from material_agent.inspiration.identity import StrictStructureGroupInput
from material_agent.inspiration.models import (
    ArtifactPointerV1,
    BridgePacketV1,
    ComponentSnapshotV1,
    EvidenceRelation,
    SubstitutionParametersV1,
    TransformationPlanV1,
    TransformationStatus,
    canonical_sha256,
    deterministic_id,
    transformation_route_sha256,
)
from material_agent.inspiration.runner import (
    STRUCTURE_MEDIA_TYPE,
    TransformationContext,
    TransformationDraft,
)
from material_agent.inspiration.transformations import (
    DEFAULT_SUBSTITUTION_REGISTRY_V1,
    EQUIVALENT_SITE_ANGLE_TOLERANCE,
    EQUIVALENT_SITE_SYMPREC,
    SubstitutionExecutionRequestV1,
    SubstitutionRegistryV1,
    execute_equivalent_site_substitution,
    substitution_registry_bytes,
)


_ENGINE_SPEC = {
    "adapter": "pymatgen-equivalent-site-substitution-engine-v1",
    "bridge_choice": "lowest bridge_packet_id",
    "operator": "SUBSTITUTE_EQUIVALENT_SITE_V1",
    "route": "one complete sulfur equivalence class to selenium per parent",
    "registry_sha256": canonical_sha256(DEFAULT_SUBSTITUTION_REGISTRY_V1),
    "equivalence": {
        "implementation": "pymatgen.SpacegroupAnalyzer",
        "symprec": EQUIVALENT_SITE_SYMPREC,
        "angle_tolerance": EQUIVALENT_SITE_ANGLE_TOLERANCE,
    },
    "strict_group": {
        "implementation": "pymatgen.StructureMatcher+SpeciesComparator",
        "ltol": 1e-6,
        "stol": 1e-5,
        "angle_tol": 1e-5,
        "primitive_cell": False,
        "scale": False,
        "attempt_supercell": False,
        "allow_subset": False,
    },
    "minimum_distance_angstrom": 0.5,
    "pilot_structure_quality": 0.5,
    "free_coordinates": False,
    "llm": False,
}
PYMATGEN_TRANSFORMATION_ENGINE_SNAPSHOT = ComponentSnapshotV1(
    component_id="pymatgen-substitution-engine",
    version="1",
    implementation_sha256=canonical_sha256(_ENGINE_SPEC),
)
PILOT_STRUCTURE_QUALITY = 0.5


class PymatgenTransformationEngineError(ValueError):
    """The pinned production adapter could not construct a safe route."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(f"{code}: {message}")


@dataclass(frozen=True, slots=True)
class _ExecutedDraft:
    plan: TransformationPlanV1
    artifact_bytes: bytes | None
    output_structure: Structure | None
    parent_family_id: str


class PymatgenTransformationEngine:
    """Execute only the fixed curated S-to-Se equivalent-site route."""

    component = PYMATGEN_TRANSFORMATION_ENGINE_SNAPSHOT

    def generate(
        self,
        context: TransformationContext,
    ) -> tuple[TransformationDraft, ...]:
        if not isinstance(context, TransformationContext):
            raise PymatgenTransformationEngineError(
                "INVALID_CONTEXT",
                "context must be a verified TransformationContext",
            )
        registry = _parse_frozen_registry(context)
        if not context.bridge_packets:
            return ()
        bridge = min(
            context.bridge_packets,
            key=lambda packet: packet.bridge_packet_id,
        )
        evidence_coverage = _bridge_evidence_coverage(context, bridge)

        executed: list[_ExecutedDraft] = []
        parents = tuple(
            sorted(
                context.parents,
                key=lambda item: item.reference.candidate_id,
            )
        )
        for parent_input in parents:
            if len(executed) >= context.policy.transformation.max_plans:
                break
            parent = _parse_parent(parent_input.artifact_bytes)
            groups = _equivalent_site_groups(parent)
            sulfur_groups = tuple(
                group
                for group in groups
                if all(
                    parent[index].is_ordered
                    and parent[index].specie.symbol == "S"
                    for index in group
                )
            )
            # Ambiguous multiple sulfur classes are not guessed.  The runner
            # will surface the resulting no-match as an explicit review item.
            if len(sulfur_groups) != 1:
                continue
            selected_group = sulfur_groups[0]
            parameters = SubstitutionParametersV1(
                equivalent_site_indices=selected_group,
                source_species="S",
                target_species="Se",
            )
            reference = parent_input.reference
            route_sha256 = transformation_route_sha256(
                parent_structure_id=reference.structure_id,
                operator_id="SUBSTITUTE_EQUIVALENT_SITE_V1",
                operator_version="1",
                parameters=parameters,
            )
            plan = TransformationPlanV1(
                plan_id=deterministic_id(
                    "plan",
                    {
                        "bridge_packet_id": bridge.bridge_packet_id,
                        "route_sha256": route_sha256,
                    },
                ),
                parent_candidate_id=reference.candidate_id,
                parent_structure_id=reference.structure_id,
                parent_structure_artifact=reference.structure_artifact,
                parameters=parameters,
                preserved_features=(
                    "ordered lattice and fractional coordinates",
                    "complete pymatgen/spglib equivalence class",
                ),
                changed_features=(
                    "all sulfur sites in one complete equivalence class become selenium",
                ),
                falsification_tests=(
                    "Compute the target-band dispersion for the hash-verified output CIF.",
                ),
                bridge_packet_ids=(bridge.bridge_packet_id,),
                route_sha256=route_sha256,
                status=TransformationStatus.PLANNED,
            )
            allowed_elements = tuple(
                sorted(
                    {
                        str(element)
                        for element in parent.composition.element_composition
                    }
                    | {"Se"}
                )
            )
            request = SubstitutionExecutionRequestV1(
                plan=plan,
                registry_artifact=context.registry_artifact,
                equivalent_site_groups=groups,
                allowed_output_elements=allowed_elements,
                allowed_output_dimensionalities=(0, 1, 2, 3),
                max_sites=context.policy.transformation.max_sites_per_structure,
                minimum_distance_angstrom=0.5,
            )
            result = execute_equivalent_site_substitution(
                request,
                parent_structure=parent,
                parent_artifact_bytes=parent_input.artifact_bytes,
                registry=registry,
            )
            relocated_plan = _relocate_output_plan(
                result.plan,
                artifact_prefix=context.artifact_prefix,
            )
            executed.append(
                _ExecutedDraft(
                    plan=relocated_plan,
                    artifact_bytes=result.artifact_bytes,
                    output_structure=result.output_structure,
                    parent_family_id=reference.structure_id,
                )
            )

        strict_groups = _strict_group_ids(executed)
        drafts: list[TransformationDraft] = []
        for item in executed:
            if item.plan.status is not TransformationStatus.STRUCTURE_VALID:
                drafts.append(
                    TransformationDraft(
                        plan=item.plan,
                        artifact_bytes=item.artifact_bytes,
                    )
                )
                continue
            if item.output_structure is None or item.artifact_bytes is None:
                raise PymatgenTransformationEngineError(
                    "INVALID_EXECUTION_RESULT",
                    "structure-valid operator result omitted structure data",
                )
            output_id = item.plan.output_structure_id
            if output_id is None:
                raise PymatgenTransformationEngineError(
                    "INVALID_EXECUTION_RESULT",
                    "structure-valid operator result omitted its structure ID",
                )
            fractions = _composition_fractions(item.output_structure)
            drafts.append(
                TransformationDraft(
                    plan=item.plan,
                    artifact_bytes=item.artifact_bytes,
                    structure_identity=StrictStructureGroupInput(
                        canonical_structure_id=output_id,
                        strict_structure_group_id=strict_groups[output_id],
                        composition_fractions=fractions,
                    ),
                    parent_family_id=item.parent_family_id,
                    # Neutral pilot engineering score.  It is deliberately
                    # not a property likelihood or scientific confidence.
                    quality=PILOT_STRUCTURE_QUALITY,
                    evidence_coverage=evidence_coverage,
                    next_falsification_step=item.plan.falsification_tests[0],
                )
            )
        return tuple(drafts)


def _parse_frozen_registry(context: TransformationContext) -> SubstitutionRegistryV1:
    pointer = context.registry_artifact
    digest = hashlib.sha256(context.registry_bytes).hexdigest()
    if pointer.sha256 != digest or pointer.size_bytes != len(context.registry_bytes):
        raise PymatgenTransformationEngineError(
            "REGISTRY_ARTIFACT_MISMATCH",
            "registry bytes differ from the frozen artifact hash or size",
        )
    try:
        registry = SubstitutionRegistryV1.model_validate_json(context.registry_bytes)
    except (ValueError, ValidationError) as error:
        raise PymatgenTransformationEngineError(
            "INVALID_REGISTRY",
            "registry artifact is not a valid substitution registry",
        ) from error
    canonical = substitution_registry_bytes(registry)
    if canonical != context.registry_bytes:
        raise PymatgenTransformationEngineError(
            "NONCANONICAL_REGISTRY",
            "registry artifact must use canonical frozen JSON bytes",
        )
    if registry != DEFAULT_SUBSTITUTION_REGISTRY_V1:
        raise PymatgenTransformationEngineError(
            "UNSUPPORTED_REGISTRY",
            "production adapter accepts only the pinned default registry",
        )
    if registry.registry_id != context.policy.transformation.registry_id:
        raise PymatgenTransformationEngineError(
            "REGISTRY_POLICY_MISMATCH",
            "registry ID differs from the frozen transformation policy",
        )
    return registry


def _bridge_evidence_coverage(
    context: TransformationContext,
    bridge: BridgePacketV1,
) -> float:
    rules = tuple(
        rule
        for rule in context.tag_graph.bridge_rules
        if rule.bridge_rule_id == bridge.bridge_rule_id
    )
    if len(rules) != 1:
        raise PymatgenTransformationEngineError(
            "BRIDGE_RULE_MISMATCH",
            "selected bridge does not resolve to one curated graph rule",
        )
    evidence = {card.evidence_card_id: card for card in context.evidence_cards}
    try:
        cards = tuple(evidence[card_id] for card_id in bridge.evidence_card_ids)
    except KeyError as error:
        raise PymatgenTransformationEngineError(
            "BRIDGE_EVIDENCE_MISSING",
            "selected bridge references evidence outside the run context",
        ) from error
    support_tags = {
        tag_id
        for card in cards
        if card.relation is EvidenceRelation.SUPPORT
        for tag_id in card.mechanism_tag_ids
    }
    required = set(rules[0].required_evidence_tag_ids)
    if not required:
        raise PymatgenTransformationEngineError(
            "BRIDGE_RULE_MISMATCH",
            "curated bridge rule has no required evidence tags",
        )
    coverage = len(required & support_tags) / len(required)
    if coverage < 1.0:
        raise PymatgenTransformationEngineError(
            "BRIDGE_EVIDENCE_INCOMPLETE",
            "selected bridge lacks required SUPPORT mechanism coverage",
        )
    return coverage


def _parse_parent(payload: bytes) -> Structure:
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            return Structure.from_str(payload.decode("utf-8"), fmt="cif")
    except Exception as error:
        raise PymatgenTransformationEngineError(
            "INVALID_PARENT_STRUCTURE",
            f"parent CIF cannot be parsed: {type(error).__name__}",
        ) from error


def _equivalent_site_groups(structure: Structure) -> tuple[tuple[int, ...], ...]:
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            analyzer = SpacegroupAnalyzer(
                structure,
                symprec=EQUIVALENT_SITE_SYMPREC,
                angle_tolerance=EQUIVALENT_SITE_ANGLE_TOLERANCE,
            )
            groups = tuple(
                sorted(
                    tuple(sorted(int(index) for index in group))
                    for group in analyzer.get_symmetrized_structure().equivalent_indices
                )
            )
    except Exception as error:
        raise PymatgenTransformationEngineError(
            "EQUIVALENCE_DISCOVERY_FAILED",
            "pymatgen/spglib could not determine complete site groups",
        ) from error
    if not groups or {index for group in groups for index in group} != set(
        range(len(structure))
    ):
        raise PymatgenTransformationEngineError(
            "EQUIVALENCE_DISCOVERY_FAILED",
            "equivalence groups do not partition every parent site",
        )
    return groups


def _relocate_output_plan(
    plan: TransformationPlanV1,
    *,
    artifact_prefix: str,
) -> TransformationPlanV1:
    pointer = plan.output_structure_artifact
    if pointer is None:
        return plan
    output_id = plan.output_structure_id
    if output_id is None:
        raise PymatgenTransformationEngineError(
            "INVALID_EXECUTION_RESULT",
            "operator output pointer has no structure ID",
        )
    relocated = ArtifactPointerV1(
        uri=f"artifact://{artifact_prefix}/structures/{output_id}.cif",
        sha256=pointer.sha256,
        size_bytes=pointer.size_bytes,
        media_type=STRUCTURE_MEDIA_TYPE,
    )
    payload = plan.model_dump(mode="python")
    payload["output_structure_artifact"] = relocated
    return TransformationPlanV1.model_validate(payload)


def _composition_fractions(
    structure: Structure,
) -> tuple[tuple[str, float], ...]:
    composition = structure.composition.element_composition
    total = float(composition.num_atoms)
    return tuple(
        sorted(
            (str(element), float(amount) / total)
            for element, amount in composition.items()
        )
    )


def _strict_group_ids(executed: list[_ExecutedDraft]) -> dict[str, str]:
    valid = tuple(
        sorted(
            (
                item
                for item in executed
                if item.plan.status is TransformationStatus.STRUCTURE_VALID
                and item.output_structure is not None
                and item.plan.output_structure_id is not None
            ),
            key=lambda item: item.plan.output_structure_id or "",
        )
    )
    matcher = StructureMatcher(
        ltol=1e-6,
        stol=1e-5,
        angle_tol=1e-5,
        primitive_cell=False,
        scale=False,
        attempt_supercell=False,
        allow_subset=False,
        comparator=SpeciesComparator(),
    )
    groups: list[list[_ExecutedDraft]] = []
    for item in valid:
        placed = False
        for group in groups:
            reference = group[0]
            if matcher.fit(reference.output_structure, item.output_structure):
                group.append(item)
                placed = True
                break
        if not placed:
            groups.append([item])
    output: dict[str, str] = {}
    for group in groups:
        member_ids = tuple(
            sorted(
                item.plan.output_structure_id
                for item in group
                if item.plan.output_structure_id is not None
            )
        )
        group_id = deterministic_id(
            "strict-group",
            {
                "matcher": "pymatgen-strict-structure-matcher-v1",
                "member_structure_ids": member_ids,
            },
        )
        for member_id in member_ids:
            output[member_id] = group_id
    return output


__all__ = [
    "PILOT_STRUCTURE_QUALITY",
    "PYMATGEN_TRANSFORMATION_ENGINE_SNAPSHOT",
    "PymatgenTransformationEngine",
    "PymatgenTransformationEngineError",
]
