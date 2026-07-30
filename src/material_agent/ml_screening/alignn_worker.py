"""Independent CPU-only ALIGNN worker; heavy imports remain inside this file."""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import sys
import zipfile
from pathlib import Path

from material_agent.ml_screening.alignn_models import AlignnArtifact, AlignnWorkerRequest, AlignnWorkerResponse


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--artifact-root", type=Path, required=True)
    args = parser.parse_args()
    try:
        root = args.artifact_root.resolve(strict=True)
        request = AlignnWorkerRequest.model_validate_json(sys.stdin.buffer.read())
        response = execute(request, root)
    except Exception as exc:
        print(f"ALIGNN worker failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2
    sys.stdout.write(response.model_dump_json())
    return 0


def execute(request: AlignnWorkerRequest, root: Path) -> AlignnWorkerResponse:
    plan = request.plan
    structure = _input(root, plan.request.input_structure)
    archive = _input(root, plan.request.model_archive)
    sandbox = root.joinpath(*plan.output_sandbox_relative_path.split("/"))
    if root not in sandbox.resolve(strict=True).parents or sandbox.is_symlink():
        raise ValueError("ALIGNN sandbox is unsafe")
    # Import only after all untrusted paths and hashes have been verified.
    import torch
    from alignn.graphs import Graph
    from alignn.models.alignn import ALIGNN, ALIGNNConfig
    from jarvis.core.atoms import Atoms
    with zipfile.ZipFile(archive) as bundle:
        names = bundle.namelist()
        if any(Path(name).is_absolute() or ".." in Path(name).parts for name in names):
            raise ValueError("ALIGNN model archive contains an unsafe path")
        configs = [name for name in names if name.endswith("config.json")]
        checkpoints = [name for name in names if ("checkpoint_" in name and name.endswith(".pt")) or name.endswith("best_model.pt")]
        if len(configs) != 1 or not checkpoints:
            raise ValueError("ALIGNN model archive lacks config or checkpoint")
        config = json.loads(bundle.read(configs[0]))
        state = torch.load(io.BytesIO(bundle.read(checkpoints[-1])), map_location="cpu", weights_only=False)["model"]
    atoms = Atoms.from_cif(str(structure))
    graph, line_graph = Graph.atom_dgl_multigraph(atoms, cutoff=8.0, max_neighbors=12)
    model = ALIGNN(ALIGNNConfig(**config["model"]))
    model.load_state_dict(state)
    model.eval()
    with torch.no_grad():
        value = float(model([graph, line_graph, torch.tensor(atoms.lattice_mat)]).detach().cpu().numpy().flatten()[0])
    output_path = sandbox / "prediction.json"
    output_path.write_text(json.dumps({"property": plan.request.target_property.value, "value": value, "unit": plan.request.unit, "target_method": plan.request.target_method}, sort_keys=True), encoding="utf-8")
    output = _artifact(root, output_path, "application/json")
    return AlignnWorkerResponse(operation_key=plan.operation_key, status="SUCCEEDED", prediction=value, output=output, is_mock=plan.request.is_mock, warnings=["ALIGNN prediction is a model estimate, not DFT or experimental evidence."])


def _input(root: Path, artifact: AlignnArtifact) -> Path:
    path = root.joinpath(*artifact.root_relative_path.split("/"))
    resolved = path.resolve(strict=True)
    if root not in resolved.parents or path.is_symlink() or not path.is_file() or path.stat().st_size != artifact.size_bytes or _sha256(path) != artifact.sha256:
        raise ValueError("ALIGNN input artifact integrity check failed")
    return path


def _artifact(root: Path, path: Path, media_type: str) -> AlignnArtifact:
    return AlignnArtifact(artifact_uri=f"artifact://{path.relative_to(root).as_posix()}", root_relative_path=path.relative_to(root).as_posix(), sha256=_sha256(path), size_bytes=path.stat().st_size, media_type=media_type)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    return digest


if __name__ == "__main__":
    raise SystemExit(main())
