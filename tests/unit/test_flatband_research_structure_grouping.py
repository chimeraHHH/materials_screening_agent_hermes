from __future__ import annotations

import base64
import hashlib
import json
from datetime import datetime
from typing import Any, TypeVar

import pytest
from pymatgen.core import Lattice, Structure
from pymatgen.io.cif import CifWriter
from pymatgen.io.vasp import Poscar

import material_agent.research.flatband_structure_grouping as structure_grouping_module
from material_agent.inspiration.models import (
    StrictModel,
    canonical_sha256,
    deterministic_id,
)
from material_agent.research.flatband_contracts import (
    SOURCE_CATALOG_V1_SHA256,
    Dimensionality,
    FlatBandBenchmarkCaseV1,
    MechanismFamily,
    SourceRecordRefV1,
    TargetBandClass,
)
from material_agent.research.flatband_source_policy import (
    SourceUseRole,
    build_case_source_policy_attestation_v2,
)
from material_agent.research.flatband_structure_grouping import (
    FingerprintComponentEvidenceV2,
    FingerprintPairEvidenceV2,
    NormalizedStructureArtifactV2,
    PreGroupCandidatePreimageV2,
    PrototypeCaseEvidenceV2,
    RawStructureArtifactV2,
    StructureDimensionalityV2,
    StructureGroupingComputationReleaseV2,
    StructureGroupingInputManifestV2,
    StructureGroupingInputRootV2,
    StructureGroupingRuntimeIdentityV2,
    StructureGroupingUnionReplayReleaseV2,
    assert_structure_grouping_computation_exact_replay_v2,
    assert_structure_grouping_input_manifest_exact_replay_v2,
    assert_structure_grouping_release_exact_replay_v2,
    assert_structure_grouping_releases_disjoint_v2,
    assert_structure_grouping_union_replay_release_exact_v2,
    build_raw_structure_artifact_v2,
    build_structure_grouping_case_input_v2,
    build_structure_grouping_union_replay_release_v2,
    finalize_structure_grouping_release_v2,
    normalize_raw_structure_artifact_v2,
    run_structure_grouping_computation_v2,
    seal_structure_grouping_input_manifest_v2,
)

ModelT = TypeVar("ModelT", bound=StrictModel)
SOURCE_RAW_SHA = "1" * 64


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
            sha_field: digest,
            id_field: deterministic_id(prefix, {sha_field: digest}),
        }
    )


def _slab(*, vacuum: float = 20.0, shift: float = 0.0) -> Structure:
    return Structure(
        Lattice([[2.5, 0.0, 0.0], [-1.25, 2.165063509, 0.0], [0.0, 0.0, vacuum]]),
        ["B", "N"],
        [[shift, shift, 0.5 + shift], [1.0 / 3.0 + shift, 2.0 / 3.0 + shift, 0.5 + shift]],
        to_unit_cell=True,
    )


def _bulk() -> Structure:
    return Structure(
        Lattice.cubic(5.64),
        ["Na", "Cl"],
        [[0.0, 0.0, 0.0], [0.5, 0.5, 0.5]],
    )


def _ambiguous_vacuum() -> Structure:
    return Structure(
        Lattice.cubic(20.0),
        ["Na", "Cl"],
        [[0.0, 0.0, 0.0], [0.5, 0.5, 0.5]],
    )


def _raw(
    structure: Structure,
    *,
    label: str,
    artifact_format: str = "PYMATGEN_JSON",
) -> RawStructureArtifactV2:
    if artifact_format == "PYMATGEN_JSON":
        raw_bytes = json.dumps(
            structure.as_dict(), sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
    elif artifact_format == "POSCAR":
        raw_bytes = str(Poscar(structure)).encode("utf-8")
    else:
        raw_bytes = str(CifWriter(structure)).encode("utf-8")
    return build_raw_structure_artifact_v2(
        private_artifact_uri=f"artifact://private/structures/{label}",
        source_id="cod",
        source_record_id=label,
        source_record_raw_sha256=SOURCE_RAW_SHA,
        artifact_format=artifact_format,  # type: ignore[arg-type]
        raw_bytes=raw_bytes,
    )


def _source(label: str) -> SourceRecordRefV1:
    return SourceRecordRefV1(
        source_id="cod",
        source_record_id=label,
        canonical_url=f"https://example.org/{label}",
        source_version="fixture-v1",
        license_expression="CC0-1.0",
        accessed_at="2026-08-10T00:00:00+00:00",
        raw_sha256=SOURCE_RAW_SHA,
        public_redistribution_allowed=True,
    )


def _case_input(
    artifact: NormalizedStructureArtifactV2,
    *,
    label: str,
    formula: str,
    dimensionality: StructureDimensionalityV2,
    study_phase: str = "pilot-r1",
    slot_index: int = 0,
    priority: int = 0,
):
    evidence = artifact.provenance.aperiodic_axis_evidence
    return build_structure_grouping_case_input_v2(
        preimage=PreGroupCandidatePreimageV2(
            study_phase=study_phase,
            slot_index=slot_index,
            priority=priority,
            source_catalog_sha256=SOURCE_CATALOG_V1_SHA256,
            parent_label=label,
            formula=formula,
            structure_artifact_id=artifact.artifact_id,
            structure_artifact_sha256=artifact.artifact_sha256,
            structure_sha256=artifact.structure_sha256,
            source_records=(_source(label),),
            target_class=TargetBandClass.FB100,
            dimensionality=dimensionality,
            aperiodic_axis=evidence.chosen_axis,
            aperiodic_axis_evidence=evidence,
            frozen_request="Find evidence-bounded flat-band inspiration.",
            frozen_requirement_sha256="2" * 64,
            hard_constraints=("ordered structure",),
            forbidden_transformations=("invent-sites",),
            primary_mechanism_stratum=MechanismFamily.LATTICE_INTERFERENCE,
            public_release_allowed=False,
        )
    )


def _final_case(
    case_input,
    *,
    leakage_group_ids: tuple[str, ...] = ("synthetic-leakage-group",),
) -> FlatBandBenchmarkCaseV1:
    preimage = case_input.preimage
    values: dict[str, Any] = {
        "source_catalog_sha256": preimage.source_catalog_sha256,
        "parent_label": preimage.parent_label,
        "formula": preimage.formula,
        "structure_sha256": preimage.structure_sha256,
        "source_records": preimage.source_records,
        "target_class": preimage.target_class,
        "target_fermi_distance_max_e_v": preimage.target_fermi_distance_max_e_v,
        "dimensionality": Dimensionality(preimage.dimensionality.value),
        "frozen_request": preimage.frozen_request,
        "frozen_requirement_sha256": preimage.frozen_requirement_sha256,
        "hard_constraints": preimage.hard_constraints,
        "soft_preferences": preimage.soft_preferences,
        "forbidden_transformations": preimage.forbidden_transformations,
        "seed_evidence": preimage.seed_evidence,
        "primary_mechanism_stratum": preimage.primary_mechanism_stratum,
        "leakage_group_ids": leakage_group_ids,
        "public_release_allowed": preimage.public_release_allowed,
    }
    return _identified(
        FlatBandBenchmarkCaseV1,
        id_field="case_id",
        sha_field="case_sha256",
        prefix="flatband-case",
        values=values,
    )


def _computation(
    case_inputs,
    artifacts,
) -> StructureGroupingComputationReleaseV2:
    manifest = seal_structure_grouping_input_manifest_v2(
        case_inputs=case_inputs,
        structure_artifacts=artifacts,
        sealed_at="2026-08-10T00:00:01+00:00",
        sealed_monotonic_ns=1,
    )
    return run_structure_grouping_computation_v2(
        input_manifest=manifest,
        started_at="2026-08-10T00:00:02+00:00",
        completed_at="2026-08-10T00:00:03+00:00",
        started_monotonic_ns=2,
        completed_monotonic_ns=3,
        created_at="2026-08-10T00:00:04+00:00",
    )


def _source_policy(case: FlatBandBenchmarkCaseV1):
    record = case.source_records[0]
    return build_case_source_policy_attestation_v2(
        case=case,
        source_id=record.source_id,
        source_record_id=record.source_record_id,
        usage_roles=(SourceUseRole.CASE_SEED, SourceUseRole.STRUCTURE),
        record_license_compatibility_sha256="3" * 64,
        record_provenance_token_sha256="4" * 64,
        public_fields_release_allowed=True,
        structure_payload_release_allowed=False,
    )


def _readdress_computation(
    computation: StructureGroupingComputationReleaseV2,
    *,
    prototype_evidence=None,
    prototype_components=None,
    fingerprint_pairs=None,
    fingerprint_components=None,
) -> StructureGroupingComputationReleaseV2:
    prototype = prototype_evidence or computation.prototype_evidence
    prototype_groups = prototype_components or computation.prototype_components
    pairs = fingerprint_pairs or computation.fingerprint_pair_evidence
    fingerprint_groups = fingerprint_components or computation.fingerprint_components
    root = structure_grouping_module._build_output_root_v2(
        prototype,
        prototype_groups,
        pairs,
        fingerprint_groups,
    )
    run = structure_grouping_module._build_formal_run_v2(
        computation.input_manifest,
        computation.grouping_algorithms,
        root,
        started_at=computation.formal_run.started_at,
        completed_at=computation.formal_run.completed_at,
        started_monotonic_ns=computation.formal_run.started_monotonic_ns,
        completed_monotonic_ns=computation.formal_run.completed_monotonic_ns,
    )
    return _identified(
        StructureGroupingComputationReleaseV2,
        id_field="computation_id",
        sha_field="computation_sha256",
        prefix="structure-computation-v2",
        values={
            "input_manifest": computation.input_manifest,
            "grouping_algorithms": computation.grouping_algorithms,
            "prototype_evidence": prototype,
            "prototype_components": prototype_groups,
            "fingerprint_pair_evidence": pairs,
            "fingerprint_components": fingerprint_groups,
            "output_root": root,
            "formal_run": run,
            "created_at": computation.created_at,
        },
    )


def _attempt_fully_readdressed_computation_clock_attack(
    computation: StructureGroupingComputationReleaseV2,
    *,
    started_at: str | None = None,
    completed_at: str | None = None,
    started_monotonic_ns: int | None = None,
    completed_monotonic_ns: int | None = None,
    created_at: str | None = None,
) -> StructureGroupingComputationReleaseV2:
    run_values = computation.formal_run.model_dump(
        mode="python", exclude={"formal_run_id", "formal_run_sha256"}
    )
    run_values.update(
        {
            "started_at": started_at or computation.formal_run.started_at,
            "completed_at": completed_at or computation.formal_run.completed_at,
            "started_monotonic_ns": (
                computation.formal_run.started_monotonic_ns
                if started_monotonic_ns is None
                else started_monotonic_ns
            ),
            "completed_monotonic_ns": (
                computation.formal_run.completed_monotonic_ns
                if completed_monotonic_ns is None
                else completed_monotonic_ns
            ),
        }
    )
    forged_run = _identified(
        type(computation.formal_run),
        id_field="formal_run_id",
        sha_field="formal_run_sha256",
        prefix="structure-formal-run-v2",
        values=run_values,
    )
    computation_values = computation.model_dump(
        mode="python", exclude={"computation_id", "computation_sha256"}
    )
    computation_values.update(
        formal_run=forged_run,
        created_at=created_at or computation.created_at,
    )
    return _identified(
        StructureGroupingComputationReleaseV2,
        id_field="computation_id",
        sha_field="computation_sha256",
        prefix="structure-computation-v2",
        values=computation_values,
    )


def test_computation_requires_input_seal_before_run_and_release(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raw = _raw(_slab(), label="chronology-slab")
    artifact = normalize_raw_structure_artifact_v2(
        raw_artifact=raw,
        dimensionality=StructureDimensionalityV2.TWO_D,
    )
    case_input = _case_input(
        artifact,
        label="chronology-slab",
        formula="BN",
        dimensionality=StructureDimensionalityV2.TWO_D,
    )
    manifest = seal_structure_grouping_input_manifest_v2(
        case_inputs=(case_input,),
        structure_artifacts=(artifact,),
        sealed_at="2026-08-10T00:00:01+00:00",
        sealed_monotonic_ns=10,
    )

    def _scientific_compute_must_not_run(_manifest):
        raise AssertionError("chronology must fail before scientific computation")

    monkeypatch.setattr(
        structure_grouping_module,
        "_recompute_grouping_outputs_v2",
        _scientific_compute_must_not_run,
    )
    with pytest.raises(ValueError, match="start must strictly follow the input seal"):
        run_structure_grouping_computation_v2(
            input_manifest=manifest,
            started_at=manifest.sealed_at,
            completed_at="2026-08-10T00:00:03+00:00",
            started_monotonic_ns=11,
            completed_monotonic_ns=12,
            created_at="2026-08-10T00:00:04+00:00",
        )
    with pytest.raises(
        ValueError, match="monotonic start must strictly follow the input seal"
    ):
        run_structure_grouping_computation_v2(
            input_manifest=manifest,
            started_at="2026-08-10T00:00:02+00:00",
            completed_at="2026-08-10T00:00:03+00:00",
            started_monotonic_ns=manifest.sealed_monotonic_ns,
            completed_monotonic_ns=12,
            created_at="2026-08-10T00:00:04+00:00",
        )


def test_fully_readdressed_computation_clock_inversions_fail_closed() -> None:
    raw = _raw(_slab(), label="readdressed-chronology-slab")
    artifact = normalize_raw_structure_artifact_v2(
        raw_artifact=raw,
        dimensionality=StructureDimensionalityV2.TWO_D,
    )
    case_input = _case_input(
        artifact,
        label="readdressed-chronology-slab",
        formula="BN",
        dimensionality=StructureDimensionalityV2.TWO_D,
    )
    computation = _computation((case_input,), (artifact,))
    assert_structure_grouping_computation_exact_replay_v2(computation)

    with pytest.raises(ValueError, match="start must strictly follow the input seal"):
        _attempt_fully_readdressed_computation_clock_attack(
            computation,
            started_at=computation.input_manifest.sealed_at,
        )
    with pytest.raises(
        ValueError, match="monotonic start must strictly follow the input seal"
    ):
        _attempt_fully_readdressed_computation_clock_attack(
            computation,
            started_monotonic_ns=(
                computation.input_manifest.sealed_monotonic_ns
            ),
        )
    with pytest.raises(ValueError, match="release predates run completion"):
        _attempt_fully_readdressed_computation_clock_attack(
            computation,
            created_at=computation.formal_run.started_at,
        )


@pytest.mark.parametrize("artifact_format", ["CIF", "POSCAR", "PYMATGEN_JSON"])
def test_raw_parser_replays_supported_text_formats(artifact_format: str) -> None:
    raw = _raw(_slab(), label=f"bn-{artifact_format.lower()}", artifact_format=artifact_format)
    first = normalize_raw_structure_artifact_v2(
        raw_artifact=raw, dimensionality=StructureDimensionalityV2.TWO_D
    )
    second = normalize_raw_structure_artifact_v2(
        raw_artifact=raw, dimensionality=StructureDimensionalityV2.TWO_D
    )

    assert first == second
    assert first.provenance.aperiodic_axis_evidence.candidate_axes == (2,)
    assert first.provenance.aperiodic_axis_evidence.chosen_axis == 2
    assert first.provenance.provenance_scope == "INTERNAL_REPLAY_NOT_EXTERNAL_ATTESTATION"
    assert raw.raw_byte_count == len(raw.raw_bytes())
    assert raw.raw_bytes_sha256 == hashlib.sha256(raw.raw_bytes()).hexdigest()


def test_axis_gap_rule_rejects_ambiguous_and_wrong_dimensionality() -> None:
    with pytest.raises(ValueError, match="exactly one geometric vacuum axis"):
        normalize_raw_structure_artifact_v2(
            raw_artifact=_raw(_ambiguous_vacuum(), label="ambiguous"),
            dimensionality=StructureDimensionalityV2.TWO_D,
        )
    with pytest.raises(ValueError, match="exactly one geometric vacuum axis"):
        normalize_raw_structure_artifact_v2(
            raw_artifact=_raw(_bulk(), label="bulk-as-2d"),
            dimensionality=StructureDimensionalityV2.TWO_D,
        )
    with pytest.raises(ValueError, match="zero geometric vacuum axes"):
        normalize_raw_structure_artifact_v2(
            raw_artifact=_raw(_slab(), label="slab-as-3d"),
            dimensionality=StructureDimensionalityV2.THREE_D,
        )

    bulk = normalize_raw_structure_artifact_v2(
        raw_artifact=_raw(_bulk(), label="bulk"),
        dimensionality=StructureDimensionalityV2.THREE_D,
    )
    assert bulk.provenance.aperiodic_axis_evidence.candidate_axes == ()
    assert bulk.provenance.aperiodic_axis_evidence.chosen_axis is None


def test_axis_gap_quantization_handles_skewed_large_height_cell() -> None:
    skewed = Structure(
        Lattice([[2.5, 0.0, 0.0], [-1.25, 2.165063509, 0.0], [1.2, -0.7, 10_000.0]]),
        ["B", "N"],
        [[0.0, 0.0, 0.5], [1.0 / 3.0, 2.0 / 3.0, 0.500001]],
    )
    artifact = normalize_raw_structure_artifact_v2(
        raw_artifact=_raw(skewed, label="skew-large"),
        dimensionality=StructureDimensionalityV2.TWO_D,
    )
    evidence = artifact.provenance.aperiodic_axis_evidence
    assert evidence.candidate_axes == (2,)
    assert evidence.axes[2].maximum_cyclic_gap_angstrom > 9_999.0


def test_normalizer_rejects_periodically_coincident_species() -> None:
    coincident = Structure(
        Lattice.cubic(5.0), ["Na", "Cl"], [[0.0, 0.0, 0.0], [1.0, 0.0, 0.0]]
    )
    with pytest.raises(ValueError, match="same periodic site"):
        normalize_raw_structure_artifact_v2(
            raw_artifact=_raw(coincident, label="coincident"),
            dimensionality=StructureDimensionalityV2.THREE_D,
        )


def test_seal_exactly_replays_raw_payload_axis_formula_and_source() -> None:
    raw = _raw(_slab(vacuum=25.0, shift=0.137), label="bn-seal")
    artifact = normalize_raw_structure_artifact_v2(
        raw_artifact=raw, dimensionality=StructureDimensionalityV2.TWO_D
    )
    case_input = _case_input(
        artifact,
        label="bn-seal",
        formula="BN",
        dimensionality=StructureDimensionalityV2.TWO_D,
    )
    manifest = seal_structure_grouping_input_manifest_v2(
        case_inputs=(case_input,),
        structure_artifacts=(artifact,),
        sealed_at="2026-08-10T00:00:01+00:00",
        sealed_monotonic_ns=10,
    )
    assert_structure_grouping_input_manifest_exact_replay_v2(manifest)
    assert manifest.contains_final_case_identity is False
    assert manifest.contains_grouping_output is False

    wrong_formula = _case_input(
        artifact,
        label="bn-seal",
        formula="NaCl",
        dimensionality=StructureDimensionalityV2.TWO_D,
    )
    with pytest.raises(ValueError, match="formula differs"):
        seal_structure_grouping_input_manifest_v2(
            case_inputs=(wrong_formula,), structure_artifacts=(artifact,)
        )


def test_raw_and_readdressed_normalized_tampering_fail_closed() -> None:
    raw = _raw(_slab(), label="bn-tamper")
    artifact = normalize_raw_structure_artifact_v2(
        raw_artifact=raw, dimensionality=StructureDimensionalityV2.TWO_D
    )
    changed_bytes = raw.raw_bytes() + b"\n# tampered"
    copied = raw.model_copy(
        update={"raw_bytes_base64": base64.b64encode(changed_bytes).decode("ascii")}
    )
    with pytest.raises(ValueError, match="byte count"):
        RawStructureArtifactV2.model_validate(
            copied.model_dump(mode="python", round_trip=True)
        )

    bulk_payload = normalize_raw_structure_artifact_v2(
        raw_artifact=_raw(_bulk(), label="bulk-payload"),
        dimensionality=StructureDimensionalityV2.THREE_D,
    ).payload
    values = artifact.model_dump(
        mode="python", exclude={"artifact_id", "artifact_sha256"}
    )
    values["payload"] = bulk_payload
    values["structure_sha256"] = canonical_sha256(
        bulk_payload.model_dump(mode="python", round_trip=True)
    )
    with pytest.raises(ValueError, match="does not replay from exact raw bytes"):
        _identified(
            NormalizedStructureArtifactV2,
            id_field="artifact_id",
            sha_field="artifact_sha256",
            prefix="normalized-structure-v2",
            values=values,
        )


def test_exact_replay_rejects_fully_readdressed_runtime_drift() -> None:
    raw = _raw(_slab(), label="bn-runtime")
    artifact = normalize_raw_structure_artifact_v2(
        raw_artifact=raw, dimensionality=StructureDimensionalityV2.TWO_D
    )
    case_input = _case_input(
        artifact,
        label="bn-runtime",
        formula="BN",
        dimensionality=StructureDimensionalityV2.TWO_D,
    )
    manifest = seal_structure_grouping_input_manifest_v2(
        case_inputs=(case_input,), structure_artifacts=(artifact,)
    )
    runtime_dump = manifest.runtime_identity.model_dump(mode="python")
    assert runtime_dump["platform_system"]
    assert runtime_dump["platform_machine"]
    assert len(runtime_dump["requirements_lock_sha256"]) == 64
    assert len(runtime_dump["spglib_extension_sha256"]) == 64
    assert len(runtime_dump["libsymspg_binary_sha256"]) == 64
    assert "spglib_extension_path" not in runtime_dump
    assert "libsymspg_path" not in runtime_dump

    forged_fields = (
        ("pymatgen_version", "forged-version"),
        ("platform_system", "forged-os"),
        ("platform_machine", "forged-machine"),
        ("requirements_lock_sha256", "5" * 64),
        ("spglib_extension_sha256", "6" * 64),
        ("libsymspg_binary_sha256", "7" * 64),
    )
    for field, forged_value in forged_fields:
        runtime_values = manifest.runtime_identity.model_dump(
            mode="python", exclude={"runtime_id", "runtime_sha256"}
        )
        runtime_values[field] = forged_value
        forged_runtime = _identified(
            StructureGroupingRuntimeIdentityV2,
            id_field="runtime_id",
            sha_field="runtime_sha256",
            prefix="structure-runtime-v2",
            values=runtime_values,
        )
        root_values = manifest.input_root.model_dump(
            mode="python", exclude={"input_root_sha256"}
        )
        root_values["runtime_sha256"] = forged_runtime.runtime_sha256
        root_digest = canonical_sha256(
            StructureGroupingInputRootV2.model_construct(**root_values).model_dump(
                mode="python", exclude={"input_root_sha256"}
            )
        )
        forged_root = StructureGroupingInputRootV2(
            **root_values,
            input_root_sha256=root_digest,
        )
        manifest_values = manifest.model_dump(
            mode="python", exclude={"manifest_id", "manifest_sha256"}
        )
        manifest_values.update(
            runtime_identity=forged_runtime,
            input_root=forged_root,
        )
        forged_manifest = _identified(
            StructureGroupingInputManifestV2,
            id_field="manifest_id",
            sha_field="manifest_sha256",
            prefix="structure-input-manifest",
            values=manifest_values,
        )
        with pytest.raises(ValueError, match="current local runtime"):
            assert_structure_grouping_input_manifest_exact_replay_v2(
                forged_manifest
            )


def test_raw_artifact_bounds_and_unsafe_uri_fail_closed() -> None:
    with pytest.raises(ValueError, match="opaque path"):
        build_raw_structure_artifact_v2(
            private_artifact_uri="artifact://private/structures/../escape",
            source_id="cod",
            source_record_id="unsafe",
            source_record_raw_sha256=SOURCE_RAW_SHA,
            artifact_format="POSCAR",
            raw_bytes=b"x",
        )
    with pytest.raises(ValueError, match="byte bound"):
        build_raw_structure_artifact_v2(
            private_artifact_uri="artifact://private/structures/empty",
            source_id="cod",
            source_record_id="empty",
            source_record_raw_sha256=SOURCE_RAW_SHA,
            artifact_format="POSCAR",
            raw_bytes=b"",
        )


def test_compute_and_finalize_exact_replay_complete_replacement_universe() -> None:
    first_artifact = normalize_raw_structure_artifact_v2(
        raw_artifact=_raw(_slab(), label="bn-primary"),
        dimensionality=StructureDimensionalityV2.TWO_D,
    )
    second_artifact = normalize_raw_structure_artifact_v2(
        raw_artifact=_raw(_slab(vacuum=25.0), label="bn-replacement"),
        dimensionality=StructureDimensionalityV2.TWO_D,
    )
    first_input = _case_input(
        first_artifact,
        label="bn-primary",
        formula="BN",
        dimensionality=StructureDimensionalityV2.TWO_D,
        slot_index=0,
        priority=0,
    )
    second_input = _case_input(
        second_artifact,
        label="bn-replacement",
        formula="BN",
        dimensionality=StructureDimensionalityV2.TWO_D,
        slot_index=0,
        priority=1,
    )
    computation = _computation(
        (second_input, first_input),
        (second_artifact, first_artifact),
    )

    assert_structure_grouping_computation_exact_replay_v2(computation)
    assert len(computation.prototype_components) == 1
    assert len(computation.fingerprint_components) == 1
    pair = computation.fingerprint_pair_evidence[0]
    assert pair.fit_anonymous
    assert pair.matcher_direction_policy == "BIDIRECTIONAL_CONSERVATIVE_OR"

    cases = (_final_case(first_input), _final_case(second_input))
    policies = tuple(_source_policy(case) for case in cases)
    release = finalize_structure_grouping_release_v2(
        computation=computation,
        final_cases=reversed(cases),
        source_policy_attestations=reversed(policies),
        final_cases_declared_at="2026-08-10T00:00:05+00:00",
        created_at="2026-08-10T00:00:06+00:00",
    )

    assert_structure_grouping_release_exact_replay_v2(release)
    assert tuple(item.candidate_key for item in release.final_case_projections) == (
        tuple(item.candidate_key for item in computation.input_manifest.case_inputs)
    )
    assert len(release.grouping_assignments) == 4
    assert all(item.scientific_conclusion is False for item in release.grouping_runs)


def test_two_d_layer_signature_ignores_vacuum_translation_and_skewed_c() -> None:
    structures = (
        _slab(vacuum=15.0),
        _slab(vacuum=25.0),
        _slab(vacuum=25.0, shift=0.137),
        Structure(
            Lattice(
                [[2.5, 0.0, 0.0], [-1.25, 2.165063509, 0.0], [3.1, 4.2, 40.0]]
            ),
            ["B", "N"],
            [[0.0, 0.0, 0.5], [1.0 / 3.0, 2.0 / 3.0, 0.5]],
        ),
    )
    artifacts = tuple(
        normalize_raw_structure_artifact_v2(
            raw_artifact=_raw(structure, label=f"bn-invariant-{index}"),
            dimensionality=StructureDimensionalityV2.TWO_D,
        )
        for index, structure in enumerate(structures)
    )
    inputs = tuple(
        _case_input(
            artifact,
            label=f"bn-invariant-{index}",
            formula="BN",
            dimensionality=StructureDimensionalityV2.TWO_D,
            slot_index=index,
        )
        for index, artifact in enumerate(artifacts)
    )
    computation = _computation(inputs, artifacts)

    signature_schedules = {
        tuple(item.signature_sha256 for item in evidence.threshold_evidence)
        for evidence in computation.prototype_evidence
    }
    canonical_payloads = {
        canonical_sha256(
            evidence.canonicalized_structure.canonical_payload.model_dump(
                mode="python", round_trip=True
            )
        )
        for evidence in computation.prototype_evidence
    }
    assert len(signature_schedules) == 1
    assert len(canonical_payloads) == 1
    assert computation.prototype_evidence[0].threshold_evidence[0].signature.group_number == 78
    assert len(computation.prototype_components) == 1
    assert len(computation.fingerprint_components) == 1


def test_three_d_threshold_union_and_anonymous_chemistry_are_conservative() -> None:
    structures = (
        Structure(Lattice.cubic(5.0), ["Cs", "Cl"], [[0, 0, 0], [0.5, 0.5, 0.5]]),
        Structure(
            Lattice.tetragonal(5.0, 5.1),
            ["Cs", "Cl"],
            [[0, 0, 0], [0.5, 0.5, 0.5]],
        ),
        Structure(Lattice.cubic(5.0), ["Na", "Br"], [[0, 0, 0], [0.5, 0.5, 0.5]]),
    )
    formulas = ("CsCl", "CsCl", "NaBr")
    artifacts = tuple(
        normalize_raw_structure_artifact_v2(
            raw_artifact=_raw(structure, label=f"bulk-threshold-{index}"),
            dimensionality=StructureDimensionalityV2.THREE_D,
        )
        for index, structure in enumerate(structures)
    )
    inputs = tuple(
        _case_input(
            artifact,
            label=f"bulk-threshold-{index}",
            formula=formulas[index],
            dimensionality=StructureDimensionalityV2.THREE_D,
            slot_index=index,
        )
        for index, artifact in enumerate(artifacts)
    )
    computation = _computation(inputs, artifacts)

    schedules = tuple(
        tuple(item.signature.group_number for item in evidence.threshold_evidence)
        for evidence in computation.prototype_evidence
    )
    assert (123, 123, 221) in schedules
    assert schedules.count((221, 221, 221)) == 2
    assert len(computation.prototype_components) == 1
    assert len(computation.fingerprint_components) == 1
    assert all(item.fit_anonymous for item in computation.fingerprint_pair_evidence)


def test_bounded_supercell_stage_catches_perturbed_commensurate_cell() -> None:
    base = _slab()
    supercell = base.copy()
    supercell.make_supercell([2, 1, 1])
    perturbed = supercell.copy()
    perturbed.translate_sites(
        [0], [0.3, 0.0, 0.0], frac_coords=False, to_unit_cell=True
    )
    structures = (base, supercell, perturbed)
    formulas = ("BN", "B2N2", "B2N2")
    artifacts = tuple(
        normalize_raw_structure_artifact_v2(
            raw_artifact=_raw(structure, label=f"bn-supercell-{index}"),
            dimensionality=StructureDimensionalityV2.TWO_D,
        )
        for index, structure in enumerate(structures)
    )
    inputs = tuple(
        _case_input(
            artifact,
            label=f"bn-supercell-{index}",
            formula=formulas[index],
            dimensionality=StructureDimensionalityV2.TWO_D,
            slot_index=index,
        )
        for index, artifact in enumerate(artifacts)
    )
    computation = _computation(inputs, artifacts)

    bounded = tuple(
        item
        for item in computation.fingerprint_pair_evidence
        if item.matcher_stage == "BOUNDED_SUPERCELL"
    )
    assert len(bounded) == 2
    assert all(item.anonymous_supercell_factor == 2 for item in bounded)
    assert all(item.fit_anonymous for item in bounded)
    assert len(computation.fingerprint_components) == 1
    assert sorted(len(item.candidate_keys) for item in computation.prototype_components) == [1, 2]


def test_fingerprint_pair_records_both_directional_calls(monkeypatch) -> None:
    first = Structure(Lattice.cubic(5.0), ["Cs", "Cl"], [[0, 0, 0], [0.5, 0.5, 0.5]])
    second = Structure(
        Lattice.tetragonal(5.0, 5.1),
        ["Cs", "Cl"],
        [[0, 0, 0], [0.5, 0.5, 0.5]],
    )

    def directional_fit(_self, left, right, **_kwargs):
        return left.volume < right.volume

    monkeypatch.setattr(
        structure_grouping_module.StructureMatcher,
        "fit_anonymous",
        directional_fit,
    )
    artifacts = tuple(
        normalize_raw_structure_artifact_v2(
            raw_artifact=_raw(structure, label=f"direction-{index}"),
            dimensionality=StructureDimensionalityV2.THREE_D,
        )
        for index, structure in enumerate((first, second))
    )
    inputs = tuple(
        _case_input(
            artifact,
            label=f"direction-{index}",
            formula="CsCl",
            dimensionality=StructureDimensionalityV2.THREE_D,
            slot_index=index,
        )
        for index, artifact in enumerate(artifacts)
    )
    pair = _computation(inputs, artifacts).fingerprint_pair_evidence[0]

    assert pair.forward_fit_anonymous is not pair.reverse_fit_anonymous
    assert pair.fit_anonymous is True


def test_prototype_components_use_cross_threshold_signature_intersection() -> None:
    structures = (
        Structure(Lattice.cubic(5.0), ["Cs", "Cl"], [[0, 0, 0], [0.5, 0.5, 0.5]]),
        Structure(
            Lattice.tetragonal(5.0, 5.1),
            ["Cs", "Cl"],
            [[0, 0, 0], [0.5, 0.5, 0.5]],
        ),
    )
    artifacts = tuple(
        normalize_raw_structure_artifact_v2(
            raw_artifact=_raw(structure, label=f"cross-threshold-{index}"),
            dimensionality=StructureDimensionalityV2.THREE_D,
        )
        for index, structure in enumerate(structures)
    )
    inputs = tuple(
        _case_input(
            artifact,
            label=f"cross-threshold-{index}",
            formula="CsCl",
            dimensionality=StructureDimensionalityV2.THREE_D,
            slot_index=index,
        )
        for index, artifact in enumerate(artifacts)
    )
    computation = _computation(inputs, artifacts)
    base_signature = computation.prototype_evidence[0].threshold_evidence[0].signature

    def signature(number: int):
        return type(base_signature).model_validate(
            base_signature.model_copy(
                update={
                    "group_number": number,
                    "hall_number": number,
                    "international_symbol": f"synthetic-{number}",
                }
            ).model_dump(mode="python", round_trip=True)
        )

    signatures = tuple(signature(index) for index in range(1, 6))
    schedules = (
        (signatures[0], signatures[1], signatures[2]),
        (signatures[3], signatures[4], signatures[0]),
    )
    forged_evidence: list[PrototypeCaseEvidenceV2] = []
    for evidence, row_signatures in zip(computation.prototype_evidence, schedules):
        thresholds = tuple(
            type(threshold).model_validate(
                threshold.model_copy(
                    update={
                        "signature": row_signature,
                        "signature_sha256": canonical_sha256(
                            row_signature.model_dump(mode="python")
                        ),
                    }
                ).model_dump(mode="python", round_trip=True)
            )
            for threshold, row_signature in zip(
                evidence.threshold_evidence, row_signatures
            )
        )
        values = evidence.model_dump(
            mode="python", exclude={"evidence_id", "evidence_sha256"}
        )
        values["threshold_evidence"] = thresholds
        forged_evidence.append(
            _identified(
                PrototypeCaseEvidenceV2,
                id_field="evidence_id",
                sha_field="evidence_sha256",
                prefix="prototype-case-evidence",
                values=values,
            )
        )
    assert all(
        left.signature_sha256 != right.signature_sha256
        for left, right in zip(
            forged_evidence[0].threshold_evidence,
            forged_evidence[1].threshold_evidence,
        )
    )
    components = structure_grouping_module._build_prototype_components_v2(
        computation.input_manifest, tuple(forged_evidence)
    )
    assert len(components) == 1


def test_exact_replay_rejects_fully_readdressed_signature_pair_and_component_forgery() -> None:
    artifacts = tuple(
        normalize_raw_structure_artifact_v2(
            raw_artifact=_raw(_slab(vacuum=vacuum), label=f"forge-{index}"),
            dimensionality=StructureDimensionalityV2.TWO_D,
        )
        for index, vacuum in enumerate((20.0, 25.0))
    )
    inputs = tuple(
        _case_input(
            artifact,
            label=f"forge-{index}",
            formula="BN",
            dimensionality=StructureDimensionalityV2.TWO_D,
            slot_index=index,
        )
        for index, artifact in enumerate(artifacts)
    )
    computation = _computation(inputs, artifacts)

    evidence = computation.prototype_evidence[0]
    threshold = evidence.threshold_evidence[0]
    forged_signature = type(threshold.signature).model_validate(
        threshold.signature.model_copy(
            update={"group_number": 77, "international_symbol": "forged-layer"}
        ).model_dump(mode="python", round_trip=True)
    )
    forged_threshold = type(threshold).model_validate(
        threshold.model_copy(
            update={
                "signature": forged_signature,
                "signature_sha256": canonical_sha256(
                    forged_signature.model_dump(mode="python")
                ),
            }
        ).model_dump(mode="python", round_trip=True)
    )
    evidence_values = evidence.model_dump(
        mode="python", exclude={"evidence_id", "evidence_sha256"}
    )
    evidence_values["threshold_evidence"] = (
        forged_threshold,
        *evidence.threshold_evidence[1:],
    )
    forged_evidence = _identified(
        PrototypeCaseEvidenceV2,
        id_field="evidence_id",
        sha_field="evidence_sha256",
        prefix="prototype-case-evidence",
        values=evidence_values,
    )
    forged_signature_release = _readdress_computation(
        computation,
        prototype_evidence=(forged_evidence, *computation.prototype_evidence[1:]),
    )
    with pytest.raises(ValueError, match="threshold evidence"):
        assert_structure_grouping_computation_exact_replay_v2(
            forged_signature_release
        )

    pair = computation.fingerprint_pair_evidence[0]
    pair_values = pair.model_dump(
        mode="python", exclude={"pair_id", "pair_sha256"}
    )
    pair_values.update(
        forward_fit_anonymous=False,
        reverse_fit_anonymous=False,
        fit_anonymous=False,
    )
    forged_pair = _identified(
        FingerprintPairEvidenceV2,
        id_field="pair_id",
        sha_field="pair_sha256",
        prefix="fingerprint-pair-v2",
        values=pair_values,
    )
    forged_pairs = (forged_pair,)
    forged_pair_components = structure_grouping_module._build_fingerprint_components_v2(
        computation.input_manifest, forged_pairs
    )
    forged_pair_release = _readdress_computation(
        computation,
        fingerprint_pairs=forged_pairs,
        fingerprint_components=forged_pair_components,
    )
    with pytest.raises(ValueError, match="pair matrix"):
        assert_structure_grouping_computation_exact_replay_v2(forged_pair_release)

    singleton_components: list[FingerprintComponentEvidenceV2] = []
    input_by_key = {
        item.candidate_key: item for item in computation.input_manifest.case_inputs
    }
    for candidate_key in sorted(input_by_key):
        structure_sha256 = input_by_key[candidate_key].preimage.structure_sha256
        singleton_components.append(
            _identified(
                FingerprintComponentEvidenceV2,
                id_field="component_id",
                sha_field="component_sha256",
                prefix="fingerprint-component-v2",
                values={
                    "canonical_group_key": deterministic_id(
                        "structure-fp-group",
                        {"structure_sha256s": (structure_sha256,)},
                    ),
                    "candidate_keys": (candidate_key,),
                    "structure_sha256s": (structure_sha256,),
                },
            )
        )
    forged_component_release = _readdress_computation(
        computation,
        fingerprint_components=tuple(singleton_components),
    )
    with pytest.raises(ValueError, match="fingerprint components"):
        assert_structure_grouping_computation_exact_replay_v2(
            forged_component_release
        )


def test_finalizer_rejects_case_source_policy_chronology_and_crosswire() -> None:
    artifact = normalize_raw_structure_artifact_v2(
        raw_artifact=_raw(_slab(), label="final-crosswire"),
        dimensionality=StructureDimensionalityV2.TWO_D,
    )
    case_input = _case_input(
        artifact,
        label="final-crosswire",
        formula="BN",
        dimensionality=StructureDimensionalityV2.TWO_D,
    )
    computation = _computation((case_input,), (artifact,))
    case = _final_case(case_input)
    policy = _source_policy(case)

    with pytest.raises(ValueError, match="after computation completion"):
        finalize_structure_grouping_release_v2(
            computation=computation,
            final_cases=(case,),
            source_policy_attestations=(policy,),
            final_cases_declared_at="2026-08-10T00:00:03+00:00",
            created_at="2026-08-10T00:00:06+00:00",
        )

    no_structure_policy = build_case_source_policy_attestation_v2(
        case=case,
        source_id="cod",
        source_record_id="final-crosswire",
        usage_roles=(SourceUseRole.CASE_SEED,),
        record_license_compatibility_sha256="3" * 64,
        record_provenance_token_sha256="4" * 64,
        public_fields_release_allowed=True,
        structure_payload_release_allowed=False,
    )
    with pytest.raises(ValueError, match="no catalog-approved structure source"):
        finalize_structure_grouping_release_v2(
            computation=computation,
            final_cases=(case,),
            source_policy_attestations=(no_structure_policy,),
            final_cases_declared_at="2026-08-10T00:00:05+00:00",
            created_at="2026-08-10T00:00:06+00:00",
        )

    crosswire_values = case.model_dump(
        mode="python", exclude={"case_id", "case_sha256"}
    )
    crosswire_values["parent_label"] = "foreign-parent"
    crosswired_case = _identified(
        FlatBandBenchmarkCaseV1,
        id_field="case_id",
        sha_field="case_sha256",
        prefix="flatband-case",
        values=crosswire_values,
    )
    with pytest.raises(ValueError, match="mechanical pre-group preimage"):
        finalize_structure_grouping_release_v2(
            computation=computation,
            final_cases=(crosswired_case,),
            source_policy_attestations=(_source_policy(crosswired_case),),
            final_cases_declared_at="2026-08-10T00:00:05+00:00",
            created_at="2026-08-10T00:00:06+00:00",
        )


def test_compute_rejects_formal_site_bound_before_symmetry_work() -> None:
    coordinates = tuple(
        (x / 6.0, y / 6.0, z / 4.0)
        for x in range(6)
        for y in range(6)
        for z in range(4)
    )[:129]
    structure = Structure(Lattice.cubic(5.0), ["Na"] * 129, coordinates)
    artifact = normalize_raw_structure_artifact_v2(
        raw_artifact=_raw(structure, label="too-many-sites"),
        dimensionality=StructureDimensionalityV2.THREE_D,
    )
    case_input = _case_input(
        artifact,
        label="too-many-sites",
        formula="Na129",
        dimensionality=StructureDimensionalityV2.THREE_D,
    )
    manifest = seal_structure_grouping_input_manifest_v2(
        case_inputs=(case_input,), structure_artifacts=(artifact,)
    )
    with pytest.raises(ValueError, match="128-site compute bound"):
        run_structure_grouping_computation_v2(input_manifest=manifest)


def test_exact_replay_success_cache_still_rejects_same_sha_model_copy() -> None:
    artifact = normalize_raw_structure_artifact_v2(
        raw_artifact=_raw(_bulk(), label="cache-safety"),
        dimensionality=StructureDimensionalityV2.THREE_D,
    )
    case_input = _case_input(
        artifact,
        label="cache-safety",
        formula="NaCl",
        dimensionality=StructureDimensionalityV2.THREE_D,
    )
    computation = _computation((case_input,), (artifact,))
    assert_structure_grouping_computation_exact_replay_v2(computation)

    forged = computation.model_copy(
        update={"created_at": "2026-08-10T00:00:09+00:00"}
    )
    with pytest.raises(ValueError, match="computation_sha256"):
        assert_structure_grouping_computation_exact_replay_v2(forged)


def test_compute_rejects_97_candidates_before_raw_or_symmetry_work(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    artifact = normalize_raw_structure_artifact_v2(
        raw_artifact=_raw(_slab(), label="candidate-cap"),
        dimensionality=StructureDimensionalityV2.TWO_D,
    )
    case_inputs = tuple(
        _case_input(
            artifact,
            label="candidate-cap",
            formula="BN",
            dimensionality=StructureDimensionalityV2.TWO_D,
            slot_index=index,
        )
        for index in range(97)
    )
    manifest = seal_structure_grouping_input_manifest_v2(
        case_inputs=case_inputs,
        structure_artifacts=(artifact,),
    )

    def unexpected_raw_replay(_manifest) -> None:
        raise AssertionError("candidate cap must precede raw replay")

    monkeypatch.setattr(
        structure_grouping_module,
        "assert_structure_grouping_input_manifest_exact_replay_v2",
        unexpected_raw_replay,
    )
    with pytest.raises(ValueError, match="96-candidate Pilot V0"):
        run_structure_grouping_computation_v2(input_manifest=manifest)


def test_cross_release_disjointness_recomputes_raw_union_not_local_group_keys(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def release_for(
        structure: Structure,
        *,
        label: str,
        study_phase: str,
        formula: str,
        dimensionality: StructureDimensionalityV2,
    ):
        artifact = normalize_raw_structure_artifact_v2(
            raw_artifact=_raw(structure, label=label),
            dimensionality=dimensionality,
        )
        case_input = _case_input(
            artifact,
            label=label,
            formula=formula,
            dimensionality=dimensionality,
            study_phase=study_phase,
        )
        computation = _computation((case_input,), (artifact,))
        case = _final_case(case_input)
        return finalize_structure_grouping_release_v2(
            computation=computation,
            final_cases=(case,),
            source_policy_attestations=(_source_policy(case),),
            final_cases_declared_at="2026-08-10T00:00:05+00:00",
            created_at="2026-08-10T00:00:06+00:00",
        )

    calibration = release_for(
        _slab(vacuum=20.0),
        label="union-calibration",
        study_phase="calibration",
        formula="BN",
        dimensionality=StructureDimensionalityV2.TWO_D,
    )
    disjoint_bulk = release_for(
        _bulk(),
        label="union-bulk",
        study_phase="pilot-r1",
        formula="NaCl",
        dimensionality=StructureDimensionalityV2.THREE_D,
    )
    assert_structure_grouping_releases_disjoint_v2(calibration, disjoint_bulk)
    members = (
        ("calibration", calibration),
        ("pilot-r1", disjoint_bulk),
    )
    frozen_clock = {
        "union_input_sealed_at": "2026-08-10T00:00:06.100000+00:00",
        "union_input_sealed_monotonic_ns": 10,
        "run_started_at": "2026-08-10T00:00:06.200000+00:00",
        "run_completed_at": "2026-08-10T00:00:06.300000+00:00",
        "run_started_monotonic_ns": 20,
        "run_completed_monotonic_ns": 30,
        "computation_created_at": "2026-08-10T00:00:06.400000+00:00",
        "verified_at": "2026-08-10T00:00:07+00:00",
    }
    union = build_structure_grouping_union_replay_release_v2(
        members=reversed(members),
        **frozen_clock,
    )
    assert union == build_structure_grouping_union_replay_release_v2(
        members=members,
        **frozen_clock,
    )
    assert_structure_grouping_union_replay_release_exact_v2(
        release=union,
        members=members,
    )
    assert union.cross_owner_prototype_component_count == 0
    assert union.cross_owner_fingerprint_component_count == 0
    assert union.merged_input_root_sha256 == (
        union.merged_computation.input_manifest.input_root.input_root_sha256
    )
    assert tuple(item.owner_id for item in union.member_refs) == (
        "calibration",
        "pilot-r1",
    )
    sealed = datetime.fromisoformat(union.union_input_sealed_at)
    started = datetime.fromisoformat(union.merged_computation.formal_run.started_at)
    completed = datetime.fromisoformat(
        union.merged_computation.formal_run.completed_at
    )
    created = datetime.fromisoformat(union.merged_computation.created_at)
    verified = datetime.fromisoformat(union.verified_at)
    assert sealed < started <= completed <= created < verified
    assert (
        union.merged_computation.input_manifest.sealed_monotonic_ns
        < union.merged_computation.formal_run.started_monotonic_ns
        <= union.merged_computation.formal_run.completed_monotonic_ns
    )

    with pytest.raises(ValueError, match="frozen clock override must be complete"):
        build_structure_grouping_union_replay_release_v2(
            members=members,
            verified_at="2026-08-10T00:00:07+00:00",
        )
    inverted_clock = {
        **frozen_clock,
        "verified_at": frozen_clock["computation_created_at"],
    }
    with pytest.raises(ValueError, match="frozen clock chronology is inverted"):
        build_structure_grouping_union_replay_release_v2(
            members=members,
            **inverted_clock,
        )
    forged_clock_union = union.model_copy(
        update={"verified_at": union.merged_computation.created_at}
    )
    with pytest.raises(ValueError, match="chronology is inverted"):
        assert_structure_grouping_union_replay_release_exact_v2(
            release=forged_clock_union,
            members=members,
        )

    duplicate_round = release_for(
        _slab(vacuum=25.0, shift=0.137),
        label="union-pilot-r2",
        study_phase="pilot-r2",
        formula="BN",
        dimensionality=StructureDimensionalityV2.TWO_D,
    )
    with pytest.raises(ValueError, match="cross-release STRUCTURE_PROTOTYPE"):
        assert_structure_grouping_releases_disjoint_v2(
            calibration, duplicate_round
        )

    with pytest.raises(ValueError, match="missing, extra, or foreign"):
        assert_structure_grouping_union_replay_release_exact_v2(
            release=union,
            members=(("calibration", calibration),),
        )
    with pytest.raises(ValueError, match="missing, extra, or foreign"):
        assert_structure_grouping_union_replay_release_exact_v2(
            release=union,
            members=(
                ("foreign-owner", calibration),
                ("pilot-r1", disjoint_bulk),
            ),
        )
    with pytest.raises(ValueError, match="aliases one member release"):
        build_structure_grouping_union_replay_release_v2(
            members=(
                ("calibration", calibration),
                ("foreign-alias", calibration),
            ),
        )

    original_ref = union.member_refs[0]
    ref_values = original_ref.model_dump(
        mode="python", exclude={"member_ref_id", "member_ref_sha256"}
    )
    ref_values["owner_id"] = "foreign-owner"
    foreign_ref = _identified(
        type(original_ref),
        id_field="member_ref_id",
        sha_field="member_ref_sha256",
        prefix="structure-union-member-v2",
        values=ref_values,
    )
    foreign_rows = []
    for row in union.candidate_owner_projection:
        if row.owner_id != original_ref.owner_id:
            foreign_rows.append(row)
            continue
        row_values = row.model_dump(
            mode="python",
            exclude={"owner_projection_id", "owner_projection_sha256"},
        )
        row_values["owner_id"] = "foreign-owner"
        foreign_rows.append(
            _identified(
                type(row),
                id_field="owner_projection_id",
                sha_field="owner_projection_sha256",
                prefix="structure-union-owner-v2",
                values=row_values,
            )
        )
    forged_refs = tuple(
        sorted(
            (foreign_ref, *union.member_refs[1:]),
            key=lambda item: (item.owner_id, item.member_release_id, item.member_ref_id),
        )
    )
    forged_rows = tuple(
        sorted(
            foreign_rows,
            key=lambda item: (
                item.candidate_key,
                item.owner_id,
                item.owner_projection_id,
            ),
        )
    )
    union_values = union.model_dump(
        mode="python", exclude={"union_release_id", "union_release_sha256"}
    )
    union_values.update(
        member_refs=forged_refs,
        candidate_owner_projection=forged_rows,
        member_release_set_sha256=canonical_sha256(forged_refs),
        candidate_owner_projection_sha256=canonical_sha256(forged_rows),
    )
    forged_owner_union = _identified(
        StructureGroupingUnionReplayReleaseV2,
        id_field="union_release_id",
        sha_field="union_release_sha256",
        prefix="structure-union-replay-v2",
        values=union_values,
    )
    with pytest.raises(ValueError, match="missing, extra, or foreign"):
        assert_structure_grouping_union_replay_release_exact_v2(
            release=forged_owner_union,
            members=members,
        )

    merged_evidence = union.merged_computation.prototype_evidence[0]
    merged_threshold = merged_evidence.threshold_evidence[0]
    forged_signature = type(merged_threshold.signature).model_validate(
        merged_threshold.signature.model_copy(
            update={
                "group_number": (
                    77
                    if merged_threshold.signature.symmetry_kind == "LAYER_GROUP_2D"
                    else 220
                ),
                "international_symbol": "forged-union-signature",
            }
        ).model_dump(mode="python", round_trip=True)
    )
    forged_threshold = type(merged_threshold).model_validate(
        merged_threshold.model_copy(
            update={
                "signature": forged_signature,
                "signature_sha256": canonical_sha256(
                    forged_signature.model_dump(mode="python")
                ),
            }
        ).model_dump(mode="python", round_trip=True)
    )
    merged_values = merged_evidence.model_dump(
        mode="python", exclude={"evidence_id", "evidence_sha256"}
    )
    merged_values["threshold_evidence"] = (
        forged_threshold,
        *merged_evidence.threshold_evidence[1:],
    )
    forged_merged_evidence = _identified(
        PrototypeCaseEvidenceV2,
        id_field="evidence_id",
        sha_field="evidence_sha256",
        prefix="prototype-case-evidence",
        values=merged_values,
    )
    forged_merged_computation = _readdress_computation(
        union.merged_computation,
        prototype_evidence=(
            forged_merged_evidence,
            *union.merged_computation.prototype_evidence[1:],
        ),
    )
    union_values = union.model_dump(
        mode="python", exclude={"union_release_id", "union_release_sha256"}
    )
    union_values.update(
        merged_computation=forged_merged_computation,
        merged_output_root_sha256=(
            forged_merged_computation.output_root.output_root_sha256
        ),
    )
    forged_computation_union = _identified(
        StructureGroupingUnionReplayReleaseV2,
        id_field="union_release_id",
        sha_field="union_release_sha256",
        prefix="structure-union-replay-v2",
        values=union_values,
    )
    with pytest.raises(ValueError, match="does not replay exactly"):
        assert_structure_grouping_union_replay_release_exact_v2(
            release=forged_computation_union,
            members=members,
        )

    def unexpected_member_replay(_release) -> None:
        raise AssertionError("union preflight must precede member replay")

    monkeypatch.setattr(
        structure_grouping_module,
        "assert_structure_grouping_release_exact_replay_v2",
        unexpected_member_replay,
    )
    with pytest.raises(ValueError, match="at most 32 member releases"):
        build_structure_grouping_union_replay_release_v2(
            members=tuple(
                (f"owner-{index}", calibration) for index in range(33)
            )
        )
    oversized_manifest = calibration.computation.input_manifest.model_copy(
        update={
            "case_inputs": (
                calibration.computation.input_manifest.case_inputs * 97
            )
        }
    )
    oversized_computation = calibration.computation.model_copy(
        update={"input_manifest": oversized_manifest}
    )
    oversized_release = calibration.model_copy(
        update={"computation": oversized_computation}
    )
    with pytest.raises(ValueError, match="96-candidate Pilot V0"):
        build_structure_grouping_union_replay_release_v2(
            members=(
                ("oversized", oversized_release),
                ("pilot-r1", disjoint_bulk),
            )
        )
