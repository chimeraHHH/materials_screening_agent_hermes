"""Frozen metadata for the independently installed CHGNet worker.

This module is safe to import in the lightweight Orchestrator environment.  It
contains no Torch, CHGNet, ASE, or NumPy import.
"""

from __future__ import annotations

from material_agent.ml_screening.models import (
    MLModelRegistry,
    MLModelSpec,
    MLTask,
    ModelCard,
)
from material_agent.ml_screening.resources import sha256_payload


CHGNET_PACKAGE_VERSION = "0.4.2"
CHGNET_MODEL_NAME = "0.3.0"
CHGNET_MODEL_ID = "chgnet-mptrj-0.3.0"
CHGNET_ADAPTER_VERSION = "agent02-chgnet-adapter-v1"
CHGNET_CHECKPOINT_SHA256 = (
    "d14ab7c0f093efe64b60a7bcd540bca10e74fb7f46c86108a079af60524659d1"
)
AGENT02_PACKAGE_LOCK_SHA256 = (
    "278ab73807262c733c6196e9f6bea074b997d80e738ca77781b5b42a4451f870"
)


def real_model_card() -> ModelCard:
    return ModelCard(
        model_id=CHGNET_MODEL_ID,
        display_name="CHGNet 0.3.0 MPtrj pretrained potential",
        summary=(
            "Legacy CHGNet implementation using the packaged 0.3.0 MPtrj "
            "checkpoint for conservative bulk-crystal pre-relaxation."
        ),
        intended_use=[
            "static MLIP energy/force/stress/magnetic-moment prediction",
            "bulk inorganic crystal pre-relaxation before separately approved DFT",
        ],
        out_of_scope=[
            "formation-energy or convex-hull replacement",
            "experimental-property prediction",
            "magnetic-ground-state, topology, Mott, or DFT proof",
            "molecules, interfaces, two-dimensional structures, defects, or liquids",
        ],
        training_data=(
            "Materials Project GGA/GGA+U trajectories (MPtrj, September 2022); "
            "use remains subject to Materials Project terms."
        ),
        limitations=[
            "Single legacy checkpoint with no calibrated predictive uncertainty.",
            "This repository currently audits a release fixture only for elemental Si.",
            "The upstream project states that new development has moved to MatGL.",
        ],
        evidence_constraints=[
            "Results are L2_ML_SCREENED only after applicability and structure QC pass.",
            "MLIP values are never promoted to DFT or experimental evidence.",
        ],
        sources=[
            "https://github.com/CederGroupHub/chgnet",
            "https://chgnet.lbl.gov/",
            "https://doi.org/10.1038/s42256-023-00716-3",
        ],
        is_mock=False,
    )


def real_model_spec() -> MLModelSpec:
    card = real_model_card()
    return MLModelSpec(
        model_id=CHGNET_MODEL_ID,
        adapter_type="subprocess-json-chgnet",
        adapter_version=CHGNET_ADAPTER_VERSION,
        package_name="chgnet",
        package_version=CHGNET_PACKAGE_VERSION,
        checkpoint_name="chgnet_0.3.0_e29f68s314m37.pth.tar",
        checkpoint_artifact_uri=(
            "package://chgnet/pretrained/0.3.0/"
            "chgnet_0.3.0_e29f68s314m37.pth.tar"
        ),
        checkpoint_sha256=CHGNET_CHECKPOINT_SHA256,
        package_lock_uri="repository://requirements-agent02.lock",
        package_lock_sha256=AGENT02_PACKAGE_LOCK_SHA256,
        license="Modified BSD; MPtrj use is subject to Materials Project terms",
        training_dataset="MPtrj, Materials Project September 2022 trajectories",
        training_method="CHGNet 0.3.0 pretrained universal neural potential",
        supported_tasks=[
            MLTask.STATIC_PREDICTION,
            MLTask.STRUCTURE_RELAXATION,
        ],
        supported_properties=[
            "mlip_potential_energy",
            "maximum_force",
            "forces",
            "stress",
            "stress_trajectory",
            "site_magnetic_moments",
        ],
        # P1 deliberately audits only the fixed release fixtures.  Other
        # elements remain applicability-unknown until benchmarked.
        supported_elements=["Si"],
        supported_elements_source=(
            "Agent02 P1 release-fixture audit; not a complete training-domain claim"
        ),
        element_coverage_complete=False,
        supported_dimensionalities=[3],
        input_requirements=[
            "periodic 3D inorganic structure",
            "at most 100 sites",
            "minimum interatomic distance at least 0.5 angstrom",
        ],
        max_num_sites_policy=100,
        supported_devices=["cpu", "mps"],
        native_uncertainty=False,
        known_limitations=card.limitations,
        model_card_uri="repository://config/agent02/chgnet-0.3.0-model-card.json",
        model_card_sha256=sha256_payload(card),
        is_mock=False,
    )


def real_registry() -> MLModelRegistry:
    return MLModelRegistry(models=[real_model_spec()])
