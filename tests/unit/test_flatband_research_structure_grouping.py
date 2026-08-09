from __future__ import annotations

import base64
import hashlib
import json
from typing import Any, TypeVar

import pytest
from pymatgen.core import Lattice, Structure
from pymatgen.io.cif import CifWriter
from pymatgen.io.vasp import Poscar

from material_agent.inspiration.models import (
    StrictModel,
    canonical_sha256,
    deterministic_id,
)
from material_agent.research.flatband_contracts import (
    MechanismFamily,
    SOURCE_CATALOG_V1_SHA256,
    SourceRecordRefV1,
    TargetBandClass,
)
from material_agent.research.flatband_structure_grouping import (
    NormalizedStructureArtifactV2,
    PreGroupCandidatePreimageV2,
    RawStructureArtifactV2,
    StructureDimensionalityV2,
    StructureGroupingInputManifestV2,
    StructureGroupingInputRootV2,
    StructureGroupingRuntimeIdentityV2,
    assert_structure_grouping_input_manifest_exact_replay_v2,
    build_raw_structure_artifact_v2,
    build_structure_grouping_case_input_v2,
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
):
    evidence = artifact.provenance.aperiodic_axis_evidence
    return build_structure_grouping_case_input_v2(
        preimage=PreGroupCandidatePreimageV2(
            study_phase="pilot-r1",
            slot_index=0,
            priority=0,
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
    runtime_values = manifest.runtime_identity.model_dump(
        mode="python", exclude={"runtime_id", "runtime_sha256"}
    )
    runtime_values["pymatgen_version"] = "forged-version"
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
        assert_structure_grouping_input_manifest_exact_replay_v2(forged_manifest)


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


def test_compute_stage_remains_explicitly_not_ready() -> None:
    with pytest.raises(RuntimeError, match="PILOT_NO_GO"):
        run_structure_grouping_computation_v2()
