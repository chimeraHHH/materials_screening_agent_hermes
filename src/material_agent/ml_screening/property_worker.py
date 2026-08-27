"""Isolated property-model worker.

The worker currently implements ct-UAE's published single-task checkpoints.
Other registered families fail as ``ADAPTER_UNAVAILABLE`` until their own
reviewed, non-pickle loader is supplied.  Heavy imports are intentionally
kept in this process.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path

from material_agent.ml_screening.property_execution import (
    PropertyOutputArtifact,
    PropertyWorkerRequest,
    PropertyWorkerResponse,
)
from material_agent.ml_screening.property_models import PropertyModelFamily


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--artifact-root", type=Path, required=True)
    parser.add_argument("--ct-uae-source-root", type=Path)
    args = parser.parse_args()
    try:
        root = args.artifact_root.resolve(strict=True)
        request = PropertyWorkerRequest.model_validate_json(sys.stdin.buffer.read())
        response = execute(request, root=root, ct_uae_source_root=args.ct_uae_source_root)
    except Exception as exc:  # noqa: BLE001
        print(f"property worker failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2
    sys.stdout.write(response.model_dump_json())
    return 0


def execute(
    request: PropertyWorkerRequest,
    *,
    root: Path,
    ct_uae_source_root: Path | None,
) -> PropertyWorkerResponse:
    plan = request.plan
    family = plan.selected_model.family
    if family is not PropertyModelFamily.CT_UAE:
        return _failed(plan, "ADAPTER_UNAVAILABLE", f"{family.value} worker adapter is not installed")
    if ct_uae_source_root is None:
        return _failed(plan, "MISSING_MODEL_SOURCE", "ct-UAE source root was not configured")
    try:
        value = _predict_ct_uae(plan, root=root, source_root=ct_uae_source_root)
    except Exception as exc:  # noqa: BLE001
        # This remains a structured, non-traceback failure, but preserve the
        # exception message so a missing reviewed runtime dependency can be
        # diagnosed rather than being indistinguishable from model failure.
        return _failed(
            plan,
            "PREDICTION_FAILED",
            f"ct-UAE prediction failed ({type(exc).__name__}: {exc})",
        )
    sandbox = root.joinpath(*plan.output_sandbox_relative_path.split("/"))
    sandbox.mkdir(parents=True, exist_ok=False)
    output_path = sandbox / "prediction.json"
    output_path.write_text(
        json.dumps(
            {
                "model_id": plan.selected_model.model_id,
                "property_id": plan.selected_capability.property_id,
                "target_label": plan.selected_capability.target_label,
                "value": value,
                "unit": plan.selected_capability.unit,
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    relative = output_path.relative_to(root).as_posix()
    output = PropertyOutputArtifact(
        artifact_uri=f"artifact://{relative}",
        root_relative_path=relative,
        sha256=_sha256(output_path),
        size_bytes=output_path.stat().st_size,
        media_type="application/json",
    )
    return PropertyWorkerResponse(
        operation_key=plan.operation_key,
        model_id=plan.selected_model.model_id,
        property_id=plan.selected_capability.property_id,
        unit=plan.selected_capability.unit,
        status="SUCCEEDED",
        prediction=value,
        output=output,
        is_mock=False,
        warnings=["ct-UAE prediction is an unbenchmarked model estimate and is not a DFT, stability, or experimental claim."],
    )


def _predict_ct_uae(plan, *, root: Path, source_root: Path) -> float:
    if plan.input_structure is None or plan.selected_model.checkpoint is None:
        raise ValueError("ct-UAE requires verified structure and checkpoint")
    source = source_root.resolve(strict=True)
    if not (source / "ct" / "model.py").is_file() or not (source / "atom_init.json").is_file():
        raise ValueError("ct-UAE source root is incomplete")
    revision = subprocess.run(
        ["git", "-C", str(source), "rev-parse", "HEAD"],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        check=False,
        text=True,
    )
    if revision.returncode or revision.stdout.strip() != plan.selected_model.source_revision:
        raise ValueError("ct-UAE source revision does not match the frozen model spec")
    structure_path = _verified_path(root, plan.input_structure.artifact_uri, plan.input_structure.sha256)
    checkpoint_path = _verified_path(root, plan.selected_model.checkpoint.uri, plan.selected_model.checkpoint.sha256)
    sys.path.insert(0, str(source))
    import numpy as np
    import torch
    from ct.model import CrystalTransformer
    from pymatgen.core import Structure

    structure = Structure.from_file(structure_path)
    if not 1 <= len(structure) <= 256:
        raise ValueError("ct-UAE supports 1..256 sites")
    embeddings = {int(key): value for key, value in json.loads((source / "atom_init.json").read_text()).items()}
    atom_features = np.asarray([embeddings[site.specie.number] for site in structure], dtype=np.float32)
    coordinates = np.asarray(structure.cart_coords, dtype=np.float32)
    atom = torch.tensor(atom_features).unsqueeze(0)
    coords = torch.tensor(coordinates).unsqueeze(0)
    mask = torch.zeros((1, len(structure)), dtype=torch.bool)
    checkpoint = torch.load(checkpoint_path, map_location="cpu", weights_only=True)
    model = _load_ct_uae_model(CrystalTransformer, checkpoint["state_dict"], torch)
    model.eval()
    with torch.no_grad():
        value = float(model(atom, coords, mask).reshape(-1)[0].item())
    normalizer = checkpoint.get("normalizer")
    if isinstance(normalizer, dict) and "mean" in normalizer and "std" in normalizer:
        value = value * float(normalizer["std"]) + float(normalizer["mean"])
    return value


def _load_ct_uae_model(crystal_transformer, state_dict: dict, torch):
    """Load the published ct-UAE checkpoint without relaxing key checks.

    The reviewed repository revision exposes a two-layer regression head, but
    its checked-in public checkpoints use the earlier one-layer head.  The
    compatibility implementation below is selected only by that exact state
    dictionary schema and still uses ``strict=True``.  Any other mismatch is
    rejected rather than partially loading weights.
    """

    current = crystal_transformer(
        feature_size=256, num_layers=8, num_heads=8, dim_feedforward=512
    )
    try:
        current.load_state_dict(state_dict, strict=True)
        return current
    except RuntimeError:
        pass
    expected_legacy_keys = {
        "output_linear.weight",
        "output_linear.bias",
    }
    if not expected_legacy_keys.issubset(state_dict) or any(
        key.startswith(("output_linear1.", "output_linear2."))
        for key in state_dict
    ):
        raise ValueError("ct-UAE checkpoint architecture is not recognized")

    class _PublishedCheckpointTransformer(torch.nn.Module):
        def __init__(self) -> None:
            super().__init__()
            self.positional_encoding = torch.nn.Parameter(torch.rand(1, 256, 256))
            self.coords_embed = torch.nn.Linear(3, 128)
            self.atom_embed = torch.nn.Linear(100, 128)
            self.coord_diff_embed = torch.nn.Linear(3, 64)
            layer = torch.nn.TransformerEncoderLayer(
                d_model=256,
                nhead=8,
                dim_feedforward=512,
                dropout=0.1,
                batch_first=True,
            )
            self.transformer_encoder = torch.nn.TransformerEncoder(layer, 8)
            self.output_linear = torch.nn.Linear(256, 1)

        def forward(self, atom, coords, mask):
            source = torch.cat(
                [self.atom_embed(atom), self.coords_embed(coords)], dim=-1
            )
            encoded = self.transformer_encoder(source, src_key_padding_mask=mask)
            return self.output_linear(encoded[:, 0, :])

    legacy = _PublishedCheckpointTransformer()
    legacy.load_state_dict(state_dict, strict=True)
    return legacy


def _verified_path(root: Path, uri: str, expected_sha256: str) -> Path:
    root = root.resolve(strict=True)
    relative = uri.removeprefix("artifact://")
    path = root.joinpath(*relative.split("/"))
    resolved = path.resolve(strict=True)
    if root not in resolved.parents or path.is_symlink() or not path.is_file() or _sha256(path) != expected_sha256:
        raise ValueError("property input artifact failed integrity validation")
    return path


def _failed(plan, code: str, message: str) -> PropertyWorkerResponse:
    return PropertyWorkerResponse(
        operation_key=plan.operation_key,
        model_id=plan.selected_model.model_id,
        property_id=plan.selected_capability.property_id,
        unit=plan.selected_capability.unit,
        status="FAILED",
        is_mock=False,
        errors=[f"{code}: {message}"],
    )


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


if __name__ == "__main__":
    raise SystemExit(main())
