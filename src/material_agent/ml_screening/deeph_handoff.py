"""Validated CHGNet-to-DeepH structure handoff.

The handoff selects only a real CHGNet L2/QC-passed relaxed structure.  It
does not manufacture the DeepH overlap or model: callers must supply those
independent, immutable bundles with matching scientific compatibility data.
"""

from __future__ import annotations

from material_agent.ml_screening.deeph_models import (
    DeepHArtifactBundle,
    DeepHArtifactFile,
    DeepHCompatibility,
    DeepHInferenceRequest,
)
from material_agent.ml_screening.models import (
    EvidenceLevel,
    MLCandidateResult,
    MLDecision,
    RelaxationStatus,
)
from material_agent.ml_screening.real_resources import (
    AGENT02_PACKAGE_LOCK_SHA256,
    CHGNET_ADAPTER_VERSION,
    CHGNET_CHECKPOINT_SHA256,
    CHGNET_MODEL_ID,
)
from material_agent.retrieval.storage import LocalArtifactStore


def deeph_request_from_chgnet_result(
    candidate: MLCandidateResult,
    *,
    artifact_store: LocalArtifactStore,
    trained_model: DeepHArtifactBundle,
    overlap: DeepHArtifactBundle,
    model_compatibility: DeepHCompatibility,
    overlap_compatibility: DeepHCompatibility,
    overlap_structure_sha256: str,
    deeph_model_id: str,
    deeph_source_revision: str,
    is_mock: bool,
) -> DeepHInferenceRequest:
    identity = candidate.execution_identity
    if (
        identity.model_id != CHGNET_MODEL_ID
        or identity.checkpoint_sha256 != CHGNET_CHECKPOINT_SHA256
        or identity.package_lock_sha256 != AGENT02_PACKAGE_LOCK_SHA256
        or identity.adapter_version != CHGNET_ADAPTER_VERSION
        or identity.is_mock
    ):
        raise ValueError("DeepH handoff requires the frozen CHGNet model identity")
    if (
        candidate.decision is not MLDecision.PASS
        or candidate.evidence_level is not EvidenceLevel.L2_ML_SCREENED
    ):
        raise ValueError("DeepH handoff requires a real L2 CHGNet PASS")
    relaxation = candidate.relaxation_result
    if (
        relaxation is None
        or relaxation.status is not RelaxationStatus.CONVERGED
        or not relaxation.qc_passed
        or relaxation.is_mock
        or relaxation.output_structure_id is None
        or relaxation.output_structure_uri is None
        or relaxation.output_structure_sha256 is None
    ):
        raise ValueError(
            "DeepH handoff requires a real converged CHGNet output structure"
        )
    if (
        candidate.recommended_downstream_structure_id
        != relaxation.output_structure_id
    ):
        raise ValueError(
            "DeepH handoff requires the QC-passed CHGNet structure to be "
            "the recommended downstream structure"
        )
    if not artifact_store.exists_with_hash(
        relaxation.output_structure_uri,
        relaxation.output_structure_sha256,
    ):
        raise ValueError("CHGNet relaxed structure failed Artifact integrity")
    structure_ref = artifact_store.inspect(
        relaxation.output_structure_uri,
        media_type="chemical/x-cif",
    )
    if structure_ref.size_bytes <= 0:
        raise ValueError("CHGNet relaxed structure cannot be empty")
    if overlap_structure_sha256 != structure_ref.sha256:
        raise ValueError(
            "DeepH overlap must be calculated for the selected CHGNet structure"
        )
    relative = structure_ref.uri.removeprefix("artifact://")
    return DeepHInferenceRequest(
        project_id=candidate.project_id,
        run_id=candidate.run_id,
        candidate_id=candidate.candidate_id,
        input_structure=DeepHArtifactFile(
            artifact_uri=structure_ref.uri,
            root_relative_path=relative,
            sha256=structure_ref.sha256,
            size_bytes=structure_ref.size_bytes,
            media_type=structure_ref.media_type,
        ),
        trained_model=trained_model,
        overlap=overlap,
        model_compatibility=model_compatibility,
        overlap_compatibility=overlap_compatibility,
        overlap_structure_sha256=overlap_structure_sha256,
        model_id=deeph_model_id,
        deeph_source_revision=deeph_source_revision,
        is_mock=is_mock,
    )
