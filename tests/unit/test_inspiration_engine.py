from __future__ import annotations

import hashlib
import warnings
from dataclasses import replace
from pathlib import Path

import pytest
from pymatgen.core import Lattice, Structure
from pymatgen.io.cif import CifWriter

from material_agent.inspiration.engine import (
    PILOT_STRUCTURE_QUALITY,
    PYMATGEN_TRANSFORMATION_ENGINE_SNAPSHOT,
    PymatgenTransformationEngine,
    PymatgenTransformationEngineError,
)
from material_agent.inspiration.models import (
    ArtifactPointerV1,
    BridgePacketV1,
    ComponentSnapshotV1,
    EvidenceCardV1,
    EvidenceRelation,
    InspirationInputV1,
    ParentCandidateRefV1,
    TransformationStatus,
)
from material_agent.inspiration.parent_catalog import (
    LoadedParentCatalog,
    load_flat_band_parent_catalog_v1,
)
from material_agent.inspiration.policy import (
    InspirationPolicyV1,
    SelectionPolicyV1,
    TransformationBudgetV1,
)
from material_agent.inspiration.runner import (
    ParentStructureInput,
    TransformationContext,
)
from material_agent.inspiration.tag_graph import curated_flat_band_tag_graph
from material_agent.inspiration.transformations import (
    DEFAULT_SUBSTITUTION_REGISTRY_V1,
    STRUCTURE_ARTIFACT_MEDIA_TYPE,
    substitution_registry_bytes,
)
from material_agent.inspiration.vectorizer import SIGNED_HASHING_SNAPSHOT


FIXTURE_DIR = Path(__file__).parents[1] / "fixtures" / "inspiration"


def _pointer(
    uri: str,
    payload: bytes,
    media_type: str = "application/json",
) -> ArtifactPointerV1:
    return ArtifactPointerV1(
        uri=uri,
        sha256=hashlib.sha256(payload).hexdigest(),
        size_bytes=len(payload),
        media_type=media_type,
    )


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
    if not text.endswith("\n"):
        text += "\n"
    return text.encode("utf-8")


def _context(
    *,
    parent_bytes: bytes | None = None,
    registry_bytes: bytes | None = None,
    registry_pointer: ArtifactPointerV1 | None = None,
) -> TransformationContext:
    selected_parent_bytes = parent_bytes or (
        FIXTURE_DIR / "parent-tis2.cif"
    ).read_bytes()
    selected_registry_bytes = registry_bytes or substitution_registry_bytes(
        DEFAULT_SUBSTITUTION_REGISTRY_V1
    )
    parent_pointer = _pointer(
        "artifact://inputs/charged-parent.cif",
        selected_parent_bytes,
        STRUCTURE_ARTIFACT_MEDIA_TYPE,
    )
    selected_registry_pointer = registry_pointer or _pointer(
        "artifact://inputs/substitution-registry.json",
        selected_registry_bytes,
    )
    parent = ParentCandidateRefV1(
        candidate_id="parent-candidate-engine",
        structure_id="parent-structure-engine",
        structure_artifact=parent_pointer,
    )
    dummy = _pointer("artifact://inputs/dummy.json", b"{}")
    adapter = ComponentSnapshotV1(
        component_id="fixture-search-adapter",
        version="1",
        implementation_sha256="a" * 64,
    )
    inspiration_input = InspirationInputV1(
        project_id="project-engine",
        request_id="request-engine",
        run_id="run-engine",
        requirement_revision=1,
        requirement_artifact=dummy,
        parent_candidates=(parent,),
        policy_artifact=dummy,
        tag_graph_artifact=dummy,
        transformation_registry_artifact=selected_registry_pointer,
        search_fixture_artifact=dummy,
        search_adapter=adapter,
        vectorizer=SIGNED_HASHING_SNAPSHOT,
    )
    graph = curated_flat_band_tag_graph()
    rule = next(
        item
        for item in graph.bridge_rules
        if item.bridge_rule_id == "acoustic-resonance-to-electronic-flat-band"
    )
    card = EvidenceCardV1(
        evidence_card_id="evidence-engine",
        relation=EvidenceRelation.SUPPORT,
        claim_text="A bounded metadata passage supports local resonance.",
        mechanism_tag_ids=("local-resonance",),
        applicability_conditions=rule.required_conditions,
        passage_ids=("passage-engine",),
    )
    bridge = BridgePacketV1(
        bridge_packet_id="bridge-engine",
        bridge_rule_id=rule.bridge_rule_id,
        source_domain_tag_ids=rule.source_domain_tag_ids,
        target_tag_ids=rule.target_tag_ids,
        shared_invariant=rule.shared_invariant,
        transferable_control=rule.transferable_control,
        required_conditions=rule.required_conditions,
        breaking_conditions=rule.breaking_conditions,
        suggested_queries=("acoustic metamaterial local resonance",),
        evidence_card_ids=(card.evidence_card_id,),
    )
    return TransformationContext(
        inspiration_input=inspiration_input,
        policy=InspirationPolicyV1(),
        tag_graph=graph,
        bridge_packets=(bridge,),
        evidence_cards=(card,),
        parents=(
            ParentStructureInput(
                reference=parent,
                artifact_bytes=selected_parent_bytes,
            ),
        ),
        registry_artifact=selected_registry_pointer,
        registry_bytes=selected_registry_bytes,
        artifact_prefix="stages/inspiration/run-engine",
    )


def _catalog_context(
    catalog: LoadedParentCatalog,
) -> TransformationContext:
    base = _context()
    graph = base.tag_graph
    cards: list[EvidenceCardV1] = []
    bridges: list[BridgePacketV1] = []
    for rule in graph.bridge_rules:
        card = EvidenceCardV1(
            evidence_card_id=f"evidence-{rule.bridge_rule_id}",
            relation=EvidenceRelation.SUPPORT,
            claim_text="A bounded metadata passage supports the reviewed mechanism.",
            mechanism_tag_ids=rule.required_evidence_tag_ids,
            applicability_conditions=rule.required_conditions,
            passage_ids=(f"passage-{rule.bridge_rule_id}",),
        )
        cards.append(card)
        bridges.append(
            BridgePacketV1(
                bridge_packet_id=f"bridge-{rule.bridge_rule_id}",
                bridge_rule_id=rule.bridge_rule_id,
                source_domain_tag_ids=rule.source_domain_tag_ids,
                target_tag_ids=rule.target_tag_ids,
                shared_invariant=rule.shared_invariant,
                transferable_control=rule.transferable_control,
                required_conditions=rule.required_conditions,
                breaking_conditions=rule.breaking_conditions,
                suggested_queries=(f"reviewed query for {rule.bridge_rule_id}",),
                evidence_card_ids=(card.evidence_card_id,),
            )
        )
    parents = tuple(
        ParentCandidateRefV1(
            candidate_id=item.record.candidate_id,
            structure_id=item.record.structure_id,
            structure_artifact=_pointer(
                f"artifact://inputs/catalog/{item.record.artifact.asset_name}",
                item.artifact_bytes,
                STRUCTURE_ARTIFACT_MEDIA_TYPE,
            ),
        )
        for item in catalog.entries
    )
    inspiration_input = base.inspiration_input.model_copy(
        update={"parent_candidates": parents}
    )
    return replace(
        base,
        inspiration_input=inspiration_input,
        policy=InspirationPolicyV1(
            transformation=TransformationBudgetV1(
                max_plans=6,
                max_plans_per_parent=1,
            ),
            selection=SelectionPolicyV1(top_k=5),
        ),
        bridge_packets=tuple(bridges),
        evidence_cards=tuple(cards),
        parents=tuple(
            ParentStructureInput(reference=reference, artifact_bytes=item.artifact_bytes)
            for reference, item in zip(parents, catalog.entries, strict=True)
        ),
    )


def test_production_engine_executes_one_real_bounded_route_and_replays() -> None:
    engine = PymatgenTransformationEngine()

    first = engine.generate(_context())
    second = engine.generate(_context())

    assert first == second
    assert engine.component == PYMATGEN_TRANSFORMATION_ENGINE_SNAPSHOT
    assert len(first) == 1
    draft = first[0]
    assert draft.plan.status is TransformationStatus.STRUCTURE_VALID
    assert draft.artifact_bytes is not None
    assert draft.plan.output_structure_artifact is not None
    assert draft.plan.output_structure_artifact.uri.startswith(
        "artifact://stages/inspiration/run-engine/structures/str_"
    )
    assert draft.plan.output_structure_artifact.sha256 == hashlib.sha256(
        draft.artifact_bytes
    ).hexdigest()
    assert draft.plan.output_structure_artifact.size_bytes == len(
        draft.artifact_bytes
    )
    checks = {
        check.check_id: check.status.value
        for check in draft.plan.validation_checks
    }
    assert checks["equivalent_sites_complete"] == "PASS"
    assert checks["charge_or_oxidation"] == "PASS"
    assert draft.quality == PILOT_STRUCTURE_QUALITY == 0.5
    assert draft.evidence_coverage == 1.0


def test_engine_requires_one_complete_sulfur_class_and_a_bridge() -> None:
    engine = PymatgenTransformationEngine()
    context = _context()
    assert engine.generate(replace(context, bridge_packets=())) == ()

    low_symmetry = Structure(
        Lattice.from_parameters(3.4, 4.1, 6.3, 81.0, 92.0, 103.0),
        ("Ti4+", "S2-", "S2-"),
        ((0.0, 0.0, 0.0), (0.13, 0.27, 0.31), (0.41, 0.18, 0.73)),
    )
    multiple_s_classes = _context(parent_bytes=_cif_bytes(low_symmetry))
    assert engine.generate(multiple_s_classes) == ()


def test_registry_hash_and_canonical_bytes_fail_closed() -> None:
    engine = PymatgenTransformationEngine()
    canonical = substitution_registry_bytes(DEFAULT_SUBSTITUTION_REGISTRY_V1)
    bad_pointer = ArtifactPointerV1(
        uri="artifact://inputs/substitution-registry.json",
        sha256="f" * 64,
        size_bytes=len(canonical),
        media_type="application/json",
    )
    with pytest.raises(PymatgenTransformationEngineError) as hash_error:
        engine.generate(_context(registry_pointer=bad_pointer))
    assert hash_error.value.code == "REGISTRY_ARTIFACT_MISMATCH"

    noncanonical = b"\n" + canonical
    with pytest.raises(PymatgenTransformationEngineError) as canonical_error:
        engine.generate(_context(registry_bytes=noncanonical))
    assert canonical_error.value.code == "NONCANONICAL_REGISTRY"


def test_evidence_coverage_is_computed_from_support_cards() -> None:
    engine = PymatgenTransformationEngine()
    context = _context()
    support = context.evidence_cards[0]
    counter_payload = support.model_dump(mode="python")
    counter_payload["relation"] = EvidenceRelation.COUNTER
    counter_payload["counterevidence"] = ("The local mode can delocalize.",)
    counter = EvidenceCardV1.model_validate(counter_payload)

    with pytest.raises(PymatgenTransformationEngineError) as raised:
        engine.generate(replace(context, evidence_cards=(counter,)))
    assert raised.value.code == "BRIDGE_EVIDENCE_INCOMPLETE"


def test_catalog_engine_executes_six_reviewed_routes_to_five_exact_outputs() -> None:
    catalog = load_flat_band_parent_catalog_v1()
    context = _catalog_context(catalog)
    engine = PymatgenTransformationEngine(parent_catalog=catalog)

    first = engine.generate(context)
    assert first == engine.generate(context)
    assert engine.component.version == "2-catalog-v1"
    assert len(first) == 6
    assert all(item.plan.status is TransformationStatus.STRUCTURE_VALID for item in first)
    assert len({item.plan.output_structure_id for item in first}) == 5

    records = {item.record.candidate_id: item.record for item in catalog.entries}
    by_parent = {item.plan.parent_candidate_id: item for item in first}
    bridge_rule_by_packet = {
        packet.bridge_packet_id: packet.bridge_rule_id
        for packet in context.bridge_packets
    }
    for candidate_id, draft in by_parent.items():
        record = records[candidate_id]
        assert draft.plan.route_sha256 == record.route_sha256
        assert draft.plan.output_structure_id == record.expected_output_structure_id
        assert draft.plan.output_structure_artifact is not None
        assert draft.plan.output_structure_artifact.sha256 == record.expected_output_sha256
        assert draft.parent_family_id == record.family_id
        assert bridge_rule_by_packet[draft.plan.bridge_packet_ids[0]] == (
            record.reviewed_bridge_rule_id
        )

    reference = by_parent["parent-candidate-tis2-1t-reference-v1"]
    control = by_parent["parent-candidate-tisse-1t-control-v1"]
    assert reference.plan.route_sha256 != control.plan.route_sha256
    assert reference.plan.parent_structure_id != control.plan.parent_structure_id
    assert reference.plan.output_structure_artifact == control.plan.output_structure_artifact
    assert reference.artifact_bytes == control.artifact_bytes


def test_catalog_engine_skips_unsupported_bridges_and_rejects_parent_drift() -> None:
    catalog = load_flat_band_parent_catalog_v1()
    context = _catalog_context(catalog)
    engine = PymatgenTransformationEngine(parent_catalog=catalog)
    supported = tuple(
        packet
        for packet in context.bridge_packets
        if packet.bridge_rule_id
        != "photonic-interference-to-electronic-flat-band"
    )

    drafts = engine.generate(replace(context, bridge_packets=supported))
    assert len(drafts) == 4
    assert all(
        records.record.reviewed_bridge_rule_id
        != "photonic-interference-to-electronic-flat-band"
        for draft in drafts
        for records in catalog.entries
        if records.record.candidate_id == draft.plan.parent_candidate_id
    )

    first_parent = context.parents[0]
    tampered = replace(
        first_parent,
        artifact_bytes=first_parent.artifact_bytes + b"# tampered\n",
    )
    with pytest.raises(PymatgenTransformationEngineError) as raised:
        engine.generate(replace(context, parents=(tampered, *context.parents[1:])))
    assert raised.value.code == "CATALOG_PARENT_MISMATCH"
