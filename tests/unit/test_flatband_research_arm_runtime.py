from __future__ import annotations

import hashlib
from typing import Any, TypeVar

import pytest
from pydantic import ValidationError

from material_agent.inspiration.models import (
    StrictModel,
    canonical_sha256,
    deterministic_id,
)
from material_agent.research.flatband_arm_runtime import (
    ArmComponentTraceRefV1,
    ArmDerivationKind,
    ArmExecutionScope,
    ArmExecutionTraceV1,
    ModelNativeRankedOutputV1,
    ModelNativeReasoningReceiptV1,
    ModelNativeResponseStatus,
    assert_arm_execution_trace_exact,
    build_arm_execution_trace,
    build_model_native_reasoning_receipt,
    build_model_native_reasoning_response,
    build_model_native_reasoning_work_item,
    encode_model_native_reasoning_response,
)
from material_agent.research.flatband_contracts import (
    AssertedEvidenceRelation,
    EvidenceSpanRefV1,
    FalsificationPlanV1,
    HypothesisPacketV1,
    MechanismFamily,
)
from material_agent.research.flatband_execution import (
    LlmExecutionIdentityV1,
    LocalModelExecutionIdentityV1,
    MetadataModelInputRefV1,
    RankingPositionV1,
    ResearchRankingV1,
    ResearchSystemId,
    SourceBudgetV1,
    SourceVariant,
    SystemConfigV1,
    source_policy_values,
)

ModelT = TypeVar("ModelT", bound=StrictModel)
SHA_A = "a" * 64
SHA_B = "b" * 64
SHA_C = "c" * 64
SHA_D = "d" * 64
BASELINE_TAGS = canonical_sha256("baseline-tags-v1")
CROSS_DOMAIN_TAGS = canonical_sha256("cross-domain-tags-v1")
CASE_SHA = canonical_sha256("shared-case")


def _identified(
    model_type: type[ModelT],
    *,
    id_field: str,
    sha_field: str,
    prefix: str,
    values: dict[str, Any],
) -> ModelT:
    draft = model_type.model_construct(**values)
    digest = canonical_sha256(
        draft.model_dump(mode="python", exclude={id_field, sha_field})
    )
    return model_type.model_validate(
        {
            **values,
            id_field: deterministic_id(prefix, {sha_field: digest}),
            sha_field: digest,
        }
    )


def _budgets(variant: SourceVariant) -> tuple[SourceBudgetV1, ...]:
    allocations = {
        SourceVariant.CROSSREF_ONLY: {"crossref": 8},
        SourceVariant.E2_A: {"arxiv": 2, "crossref": 3, "openalex": 3},
        SourceVariant.E2_B: {
            "arxiv": 2,
            "crossref": 2,
            "openaire": 2,
            "openalex": 2,
        },
    }[variant]
    information = {
        SourceVariant.CROSSREF_ONLY: {
            "crossref": (4, 4, 100, 100_000, 50, 2),
        },
        SourceVariant.E2_A: {
            "arxiv": (1, 1, 25, 25_000, 12, 0),
            "crossref": (1, 1, 37, 37_500, 19, 1),
            "openalex": (2, 2, 38, 37_500, 19, 1),
        },
        SourceVariant.E2_B: {
            "arxiv": (1, 1, 25, 25_000, 12, 0),
            "crossref": (1, 1, 25, 25_000, 12, 0),
            "openaire": (1, 1, 25, 25_000, 13, 1),
            "openalex": (1, 1, 25, 25_000, 13, 1),
        },
    }[variant]
    return tuple(
        SourceBudgetV1(
            source_id=source_id,
            **source_policy_values(source_id),
            max_physical_requests=physical,
            max_logical_queries=information[source_id][0],
            max_pages=information[source_id][1],
            max_records=information[source_id][2],
            max_response_bytes=information[source_id][3],
            max_unique_documents=information[source_id][4],
            max_cache_hits=information[source_id][5],
        )
        for source_id, physical in sorted(allocations.items())
    )


def _llm() -> LlmExecutionIdentityV1:
    return LlmExecutionIdentityV1(
        provider="model-provider",
        model="native-reasoner",
        revision="revision-20260810",
        prompt_sha256=SHA_A,
        tokenizer_sha256=SHA_B,
        output_schema_sha256=SHA_C,
        max_output_tokens=4_000,
    )


def _local_model() -> LocalModelExecutionIdentityV1:
    return LocalModelExecutionIdentityV1(
        bundle_sha256=SHA_A,
        tokenizer_sha256=SHA_B,
        model_card_sha256=SHA_C,
        license_manifest_sha256=SHA_D,
        vector_dimension=384,
    )


def _config(
    system_id: ResearchSystemId,
    *,
    cross_domain_tags: str | None = None,
    fusion_components: tuple[ResearchSystemId, ...] | None = None,
) -> SystemConfigV1:
    if system_id is ResearchSystemId.E2_A:
        variant = SourceVariant.E2_A
    elif system_id is ResearchSystemId.E2_B:
        variant = SourceVariant.E2_B
    else:
        variant = SourceVariant.CROSSREF_ONLY
    values: dict[str, Any] = {
        "system_id": system_id,
        "source_variant": variant,
        "source_budgets": _budgets(variant),
        "query_plan_sha256": SHA_A,
        "record_projection_sha256": SHA_B,
        "ranking_policy_sha256": SHA_C,
        "cache_policy_sha256": SHA_D,
        "cache_snapshot_sha256": canonical_sha256("empty-cache-v1"),
        "baseline_tag_graph_sha256": BASELINE_TAGS,
        "cross_domain_tag_graph_sha256": (
            cross_domain_tags
            if system_id is ResearchSystemId.E3
            else None
        ),
        "llm": _llm() if system_id is ResearchSystemId.E1 else None,
        "local_semantic_model": (
            _local_model() if system_id is ResearchSystemId.E1_LOCAL else None
        ),
        "fusion_components": (),
    }
    if system_id is ResearchSystemId.E3 and cross_domain_tags is None:
        values["cross_domain_tag_graph_sha256"] = CROSS_DOMAIN_TAGS
    if system_id is ResearchSystemId.FUSION:
        components = fusion_components or (
            ResearchSystemId.E1, ResearchSystemId.E2_A, ResearchSystemId.E3
        )
        if ResearchSystemId.E2_B in components:
            fusion_variant = SourceVariant.E2_B
        elif ResearchSystemId.E2_A in components:
            fusion_variant = SourceVariant.E2_A
        else:
            fusion_variant = SourceVariant.CROSSREF_ONLY
        values.update(
            {
                "source_variant": fusion_variant,
                "source_budgets": _budgets(fusion_variant),
                "cross_domain_tag_graph_sha256": (
                    (
                        CROSS_DOMAIN_TAGS
                        if cross_domain_tags is None
                        else cross_domain_tags
                    )
                    if ResearchSystemId.E3 in components
                    else None
                ),
                "llm": _llm() if ResearchSystemId.E1 in components else None,
                "fusion_components": components,
            }
        )
    return _identified(
        SystemConfigV1,
        id_field="config_id",
        sha_field="config_sha256",
        prefix="system-config",
        values=values,
    )


def _packet(*, run_id: str, rank: int) -> HypothesisPacketV1:
    link_id = f"evidence-{run_id}-{rank}"
    span = f"Visible bounded evidence for {link_id}."
    return _identified(
        HypothesisPacketV1,
        id_field="packet_id",
        sha_field="packet_sha256",
        prefix="hypothesis-packet",
        values={
            "case_id": "case-shared",
            "case_sha256": CASE_SHA,
            "candidate_structure_sha256": canonical_sha256(
                (run_id, rank, "candidate")
            ),
            "strict_structure_group_id": f"structure-{run_id}-{rank}",
            "strict_hypothesis_group_id": f"hypothesis-{run_id}-{rank}",
            "transformation_operator_id": "SUBSTITUTE_EQUIVALENT_SITE_V1",
            "transformation_summary": "A bounded connectivity-preserving substitution.",
            "source_domain": "photonic lattices",
            "mechanism_family": MechanismFamily.LATTICE_INTERFERENCE,
            "source_mechanism": "Destructive interference localizes a mode.",
            "shared_invariant": "Connectivity-preserving destructive interference.",
            "target_mapping": "Map source connectivity to the target orbital graph.",
            "transferable_control": "Tune the symmetry-allowed hopping hierarchy.",
            "transfer_principle": "Preserve connectivity while changing orbital weight.",
            "required_conditions": ("dominant local hopping",),
            "breaking_conditions": ("large symmetry-breaking hopping",),
            "evidence_links": (
                EvidenceSpanRefV1(
                    evidence_link_id=link_id,
                    source_id="crossref",
                    source_record_id=f"record-{run_id}-{rank}",
                    source_url="https://doi.org/10.1000/example",
                    span_id=f"span-{run_id}-{rank}",
                    span_sha256=hashlib.sha256(span.encode("utf-8")).hexdigest(),
                    asserted_relation=AssertedEvidenceRelation.SUPPORT,
                    claim_summary="The source metadata supports a localized mode.",
                ),
            ),
            "falsification": FalsificationPlanV1(
                observable="Tracked-band width",
                method="Compute the preregistered target-band observable.",
                pass_condition="The tracked band meets the frozen width threshold.",
                fail_condition="The tracked band exceeds the frozen threshold.",
            ),
        },
    )


def _ranking(
    config: SystemConfigV1,
) -> tuple[ResearchRankingV1, tuple[HypothesisPacketV1, ...]]:
    run_id = f"run-{config.system_id.value.lower().replace('-', '_')}"
    packets = tuple(_packet(run_id=run_id, rank=rank) for rank in (1, 2))
    positions = tuple(
        RankingPositionV1(
            selection_rank=rank,
            packet_id=packet.packet_id,
            packet_sha256=packet.packet_sha256,
        )
        for rank, packet in enumerate(packets, start=1)
    )
    ranking = _identified(
        ResearchRankingV1,
        id_field="ranking_id",
        sha_field="ranking_sha256",
        prefix="research-ranking",
        values={
            "budget_manifest_id": f"budget-{config.system_id.value.lower().replace('-', '_')}",
            "budget_manifest_sha256": canonical_sha256(
                (config.config_sha256, "budget")
            ),
            "cell_id": f"cell-{config.system_id.value.lower().replace('-', '_')}",
            "run_id": run_id,
            "case_id": "case-shared",
            "case_sha256": CASE_SHA,
            "system_config_id": config.config_id,
            "system_config_sha256": config.config_sha256,
            "positions": positions,
            "underfill_reason_codes": ("SYNTHETIC_UNDERFILL",),
            "created_at": "2026-08-10T12:03:00+08:00",
        },
    )
    return ranking, packets


def _metadata_input(
    *, config: SystemConfigV1, ranking: ResearchRankingV1
) -> MetadataModelInputRefV1:
    source_id = config.source_budgets[0].source_id
    values = {
        "source_id": source_id,
        "receipt_bundle_id": f"bundle-{source_id}-{ranking.run_id}",
        "receipt_bundle_sha256": canonical_sha256((source_id, ranking.run_id, "bundle")),
        "record_receipt_id": f"record-receipt-{source_id}-{ranking.run_id}",
        "record_receipt_sha256": canonical_sha256(
            (source_id, ranking.run_id, "record-receipt")
        ),
        "record_identity_sha256": canonical_sha256(
            (source_id, ranking.run_id, "record")
        ),
    }
    metadata_packet_sha256 = canonical_sha256(values)
    return MetadataModelInputRefV1(
        **values,
        metadata_packet_id=deterministic_id(
            "model-metadata-packet",
            {"metadata_packet_sha256": metadata_packet_sha256},
        ),
        metadata_packet_sha256=metadata_packet_sha256,
    )


def _model_receipt(
    *,
    config: SystemConfigV1,
    ranking: ResearchRankingV1,
    packets: tuple[HypothesisPacketV1, ...],
) -> ModelNativeReasoningReceiptV1:
    work = build_model_native_reasoning_work_item(
        budget_manifest_id=ranking.budget_manifest_id,
        budget_manifest_sha256=ranking.budget_manifest_sha256,
        cell_id=ranking.cell_id,
        run_id=ranking.run_id,
        case_id=ranking.case_id,
        case_sha256=ranking.case_sha256,
        system_config=config,
        call_index=1,
        metadata_inputs=(_metadata_input(config=config, ranking=ranking),),
        task={
            "instruction": "仅使用可见 metadata 生成结构化假设",
            "requested_top_k": 5,
        },
        created_at="2026-08-10T12:00:00+08:00",
    )
    visible = encode_model_native_reasoning_response(
        work,
        status=ModelNativeResponseStatus.SUCCEEDED,
        ranked_outputs=tuple(
            ModelNativeRankedOutputV1(selection_rank=rank, packet=packet)
            for rank, packet in enumerate(packets, start=1)
        ),
    )
    response = build_model_native_reasoning_response(
        work,
        visible_response_json_utf8=visible,
        completed_at="2026-08-10T12:02:00+08:00",
    )
    return build_model_native_reasoning_receipt(
        work,
        response,
        started_at="2026-08-10T12:01:00+08:00",
        completed_at="2026-08-10T12:02:00+08:00",
        token_usage=None,
    )


def _trace(
    config: SystemConfigV1,
    *,
    scope: ArmExecutionScope = ArmExecutionScope.DEVELOPMENT,
    components: tuple[ArmExecutionTraceV1, ...] = (),
) -> ArmExecutionTraceV1:
    ranking, packets = _ranking(config)
    model_enabled = config.system_id is ResearchSystemId.E1 or (
        config.system_id is ResearchSystemId.FUSION
        and ResearchSystemId.E1 in config.fusion_components
    )
    receipts = (
        (_model_receipt(config=config, ranking=ranking, packets=packets),)
        if model_enabled
        else ()
    )
    return build_arm_execution_trace(
        scope=scope,
        system_config=config,
        ranking=ranking,
        hypothesis_packets=packets,
        model_native_receipts=receipts,
        fusion_component_traces=components,
        assembled_at="2026-08-10T12:04:00+08:00",
    )


def test_all_six_arms_and_fusion_have_exact_ranked_packet_derivations() -> None:
    isolated = tuple(
        _trace(_config(system_id))
        for system_id in (
            ResearchSystemId.B0,
            ResearchSystemId.E1,
            ResearchSystemId.E2_A,
            ResearchSystemId.E2_B,
            ResearchSystemId.E3,
        )
    )
    by_system = {item.system_config.system_id: item for item in isolated}
    fusion_components = (
        by_system[ResearchSystemId.E1],
        by_system[ResearchSystemId.E2_A],
        by_system[ResearchSystemId.E3],
    )
    fusion = _trace(_config(ResearchSystemId.FUSION), components=fusion_components)

    for trace in isolated:
        assert_arm_execution_trace_exact(trace)
        assert len(trace.hypothesis_packets) == len(trace.ranking.positions)
        assert tuple(item.selection_rank for item in trace.derivations) == (1, 2)
    assert_arm_execution_trace_exact(
        fusion, fusion_component_traces=fusion_components
    )
    assert tuple(item.system_id for item in fusion.fusion_component_trace_refs) == (
        ResearchSystemId.E1,
        ResearchSystemId.E2_A,
        ResearchSystemId.E3,
    )
    assert all(
        item.derivation_kind is ArmDerivationKind.FUSION_FROZEN_COMPONENTS
        for item in fusion.derivations
    )
    assert fusion.model_native_receipts[0].token_usage is None
    assert fusion.model_native_receipts[0].provider_attestation == "UNAVAILABLE"
    request = fusion.model_native_receipts[0].work_item
    assert request.exact_request_utf8_bytes == len(
        request.visible_request_json_utf8.encode("utf-8")
    )
    assert request.exact_request_utf8_bytes > len(request.visible_request_json_utf8)


def test_locked_fusion_and_all_fusion_minus_traces_replay_same_case_components() -> None:
    locked_components = {
        system_id: _trace(_config(system_id), scope=ArmExecutionScope.LOCKED)
        for system_id in (
            ResearchSystemId.E1,
            ResearchSystemId.E2_A,
            ResearchSystemId.E3,
        )
    }
    component_sets = (
        (
            ResearchSystemId.E1,
            ResearchSystemId.E2_A,
            ResearchSystemId.E3,
        ),
        (ResearchSystemId.E2_A, ResearchSystemId.E3),  # Fusion-minus-E1
        (ResearchSystemId.E1, ResearchSystemId.E3),  # Fusion-minus-E2
        (ResearchSystemId.E1, ResearchSystemId.E2_A),  # Fusion-minus-E3
    )
    for selected in component_sets:
        components = tuple(locked_components[item] for item in selected)
        fusion = _trace(
            _config(ResearchSystemId.FUSION, fusion_components=selected),
            scope=ArmExecutionScope.LOCKED,
            components=components,
        )
        assert_arm_execution_trace_exact(
            fusion, fusion_component_traces=components
        )
        assert fusion.scope is ArmExecutionScope.LOCKED
        assert all(
            ref.scope is ArmExecutionScope.LOCKED
            and ref.case_id == fusion.ranking.case_id
            and ref.case_sha256 == fusion.ranking.case_sha256
            for ref in fusion.fusion_component_trace_refs
        )


def test_locked_fusion_rejects_development_component_scope_substitution() -> None:
    development_e1 = _trace(_config(ResearchSystemId.E1))
    locked_e2 = _trace(
        _config(ResearchSystemId.E2_A), scope=ArmExecutionScope.LOCKED
    )
    locked_e3 = _trace(_config(ResearchSystemId.E3), scope=ArmExecutionScope.LOCKED)
    with pytest.raises(ValidationError, match="scope must exactly match"):
        _trace(
            _config(ResearchSystemId.FUSION),
            scope=ArmExecutionScope.LOCKED,
            components=(development_e1, locked_e2, locked_e3),
        )


def test_visible_json_rejects_chain_of_thought_and_duplicate_keys() -> None:
    config = _config(ResearchSystemId.E1)
    ranking, _packets = _ranking(config)
    metadata = _metadata_input(config=config, ranking=ranking)
    with pytest.raises(ValueError, match="chain-of-thought"):
        build_model_native_reasoning_work_item(
            budget_manifest_id=ranking.budget_manifest_id,
            budget_manifest_sha256=ranking.budget_manifest_sha256,
            cell_id=ranking.cell_id,
            run_id=ranking.run_id,
            case_id=ranking.case_id,
            case_sha256=ranking.case_sha256,
            system_config=config,
            call_index=1,
            metadata_inputs=(metadata,),
            task={"chain_of_thought": "must never be retained"},
            created_at="2026-08-10T12:00:00+08:00",
        )

    receipt = _model_receipt(config=config, ranking=ranking, packets=_packets)
    duplicated = receipt.response.visible_response_json_utf8.replace(
        "{", '{"status":"SUCCEEDED",', 1
    )
    with pytest.raises(ValueError, match="repeats object key"):
        build_model_native_reasoning_response(
            receipt.work_item,
            visible_response_json_utf8=duplicated,
            completed_at="2026-08-10T12:02:00+08:00",
        )


def test_exact_utf8_bytes_and_single_call_are_fail_closed() -> None:
    trace = _trace(_config(ResearchSystemId.E1))
    receipt = trace.model_native_receipts[0]
    forged = receipt.model_copy(
        update={"exact_request_utf8_bytes": receipt.exact_request_utf8_bytes + 1}
    )
    with pytest.raises(ValidationError, match="byte/hash accounting"):
        ModelNativeReasoningReceiptV1.model_validate(
            forged.model_dump(mode="python", round_trip=True)
        )
    with pytest.raises(ValidationError):
        ModelNativeReasoningReceiptV1.model_validate(
            {
                **receipt.model_dump(mode="python", round_trip=True),
                "exact_provider_call_count": 2,
            }
        )


def test_e1_requires_model_native_receipts_and_exact_visible_packet_cover() -> None:
    config = _config(ResearchSystemId.E1)
    ranking, packets = _ranking(config)
    with pytest.raises(ValidationError, match="requires model-native"):
        build_arm_execution_trace(
            scope=ArmExecutionScope.DEVELOPMENT,
            system_config=config,
            ranking=ranking,
            hypothesis_packets=packets,
            assembled_at="2026-08-10T12:04:00+08:00",
        )

    receipt = _model_receipt(config=config, ranking=ranking, packets=packets[:1])
    with pytest.raises(ValidationError, match="do not exactly cover"):
        build_arm_execution_trace(
            scope=ArmExecutionScope.DEVELOPMENT,
            system_config=config,
            ranking=ranking,
            hypothesis_packets=packets,
            model_native_receipts=(receipt,),
            assembled_at="2026-08-10T12:04:00+08:00",
        )


def test_e1_local_cannot_enter_promotion_or_locked_flow() -> None:
    config = _config(ResearchSystemId.E1_LOCAL)
    ranking, packets = _ranking(config)
    for scope in (ArmExecutionScope.PROMOTION, ArmExecutionScope.LOCKED):
        with pytest.raises(ValidationError, match="E1-local cannot enter"):
            build_arm_execution_trace(
                scope=scope,
                system_config=config,
                ranking=ranking,
                hypothesis_packets=packets,
                assembled_at="2026-08-10T12:04:00+08:00",
            )
    fusion = _config(ResearchSystemId.FUSION)
    relabelled = {
        field_name: getattr(fusion, field_name)
        for field_name in type(fusion).model_fields
        if field_name not in {"config_id", "config_sha256"}
    }
    relabelled["fusion_components"] = (ResearchSystemId.E1_LOCAL,)
    with pytest.raises(ValidationError, match="Fusion may contain only"):
        _identified(
            SystemConfigV1,
            id_field="config_id",
            sha_field="config_sha256",
            prefix="system-config",
            values=relabelled,
        )
    with pytest.raises(ValidationError, match="isolated non-local arm"):
        ArmComponentTraceRefV1(
            system_id=ResearchSystemId.E1_LOCAL,
            trace_id="local-trace",
            trace_sha256=SHA_A,
            system_config_id="local-config",
            system_config_sha256=SHA_B,
            ranking_id="local-ranking",
            ranking_sha256=SHA_C,
            case_id="case-shared",
            case_sha256=CASE_SHA,
            scope=ArmExecutionScope.LOCKED,
            assembled_at="2026-08-10T12:04:00+08:00",
        )


def test_baseline_and_cross_domain_taggraphs_cannot_alias() -> None:
    config = _config(ResearchSystemId.E3, cross_domain_tags=BASELINE_TAGS)
    ranking, packets = _ranking(config)
    with pytest.raises(ValidationError, match="TagGraphs must be isolated"):
        build_arm_execution_trace(
            scope=ArmExecutionScope.DEVELOPMENT,
            system_config=config,
            ranking=ranking,
            hypothesis_packets=packets,
            assembled_at="2026-08-10T12:04:00+08:00",
        )


def test_ranked_packet_and_fusion_component_substitution_are_rejected() -> None:
    b0 = _trace(_config(ResearchSystemId.B0))
    missing_packet_values = {
        field_name: getattr(b0, field_name)
        for field_name in type(b0).model_fields
        if field_name not in {"trace_id", "trace_sha256"}
    }
    missing_packet_values["hypothesis_packets"] = b0.hypothesis_packets[:1]
    with pytest.raises(ValidationError, match="exactly cover"):
        _identified(
            ArmExecutionTraceV1,
            id_field="trace_id",
            sha_field="trace_sha256",
            prefix="arm-execution-trace",
            values=missing_packet_values,
        )

    e1 = _trace(_config(ResearchSystemId.E1))
    e2a = _trace(_config(ResearchSystemId.E2_A))
    e2b = _trace(_config(ResearchSystemId.E2_B))
    e3 = _trace(_config(ResearchSystemId.E3))
    fusion = _trace(
        _config(ResearchSystemId.FUSION), components=(e1, e2a, e3)
    )
    with pytest.raises(ValueError, match="requires full component traces"):
        assert_arm_execution_trace_exact(fusion)
    with pytest.raises(ValidationError, match="exactly cover frozen components"):
        assert_arm_execution_trace_exact(
            fusion, fusion_component_traces=(e1, e2b, e3)
        )
