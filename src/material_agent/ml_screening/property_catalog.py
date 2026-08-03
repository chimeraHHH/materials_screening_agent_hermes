"""Reviewed source catalog for the five user-requested property model families.

Entries here describe published upstream capabilities.  They are deliberately
not executable registry entries: deployment still requires an immutable,
locally supplied checkpoint and environment-lock ArtifactPointer.
"""

from __future__ import annotations

from dataclasses import dataclass

from material_agent.ml_screening.property_models import PropertyInputKind, PropertyModelFamily


@dataclass(frozen=True)
class ReviewedPropertyModelFamily:
    family: PropertyModelFamily
    source_repository: str
    reviewed_revision: str
    input_kind: PropertyInputKind
    published_properties: tuple[str, ...]
    deployment_note: str


def reviewed_property_model_families() -> tuple[ReviewedPropertyModelFamily, ...]:
    """Return the source revisions inspected for the initial integration.

    This is provenance for deployment review, not a claim that every upstream
    repository has a universal pretrained head for every listed property.
    """

    return (
        ReviewedPropertyModelFamily(
            family=PropertyModelFamily.CRYSTALFORMER,
            source_repository="https://github.com/omron-sinicx/crystalformer",
            reviewed_revision="83fdc6805818ba14208c8db05920f915800acff7",
            input_kind=PropertyInputKind.STRUCTURE_CIF,
            published_properties=("band_gap_ev", "formation_energy_ev_atom"),
            deployment_note="Published demonstration checkpoints cover MEGNet band gap and formation energy; its demo expects a PyTorch-Geometric structure dataset and CUDA.",
        ),
        ReviewedPropertyModelFamily(
            family=PropertyModelFamily.CRYSTALFRAMER,
            source_repository="https://github.com/omron-sinicx/crystalframer",
            reviewed_revision="0921650c64d462ddfc969491354bd0f5215ef856",
            input_kind=PropertyInputKind.STRUCTURE_CIF,
            published_properties=("band_gap_ev", "formation_energy_ev_atom"),
            deployment_note="Released weights are task-specific JARVIS/MEGNet structure regressors and require the matching source revision and CUDA deployment.",
        ),
        ReviewedPropertyModelFamily(
            family=PropertyModelFamily.CT_UAE,
            source_repository="https://github.com/fduabinitio/ct-UAE",
            reviewed_revision="0141ff9e09277d2229c9d7a24c1bcc5eac9de78e",
            input_kind=PropertyInputKind.STRUCTURE_CIF,
            published_properties=("band_gap_ev", "formation_energy_ev_atom", "total_energy_ev", "total_magnetization_mu_b"),
            deployment_note="The repository provides task-specific checkpoints and training scripts; a deployed head must retain the matching task label and dataset provenance.",
        ),
        ReviewedPropertyModelFamily(
            family=PropertyModelFamily.CRABNET,
            source_repository="https://github.com/anthony-wang/CrabNet",
            reviewed_revision="457b4d4835737ecbe4dd33e53174bd818bd82143",
            input_kind=PropertyInputKind.COMPOSITION,
            published_properties=(),
            deployment_note="CrabNet accepts composition only.  Each property requires an explicitly registered trained head; composition alone does not establish a structure-specific prediction.",
        ),
        ReviewedPropertyModelFamily(
            family=PropertyModelFamily.MODNET,
            source_repository="https://github.com/ppdebreuck/modnet",
            reviewed_revision="fca6bda0146e0fd3696b4df5fd14a0b71fcbe8cd",
            input_kind=PropertyInputKind.STRUCTURE_CIF,
            published_properties=("refractive_index", "vibrational_thermodynamics"),
            deployment_note="The repository bundles structure-based pretrained refractive-index and vibrational-thermodynamics models; its native serialized model format must remain isolated and verified before use.",
        ),
    )
