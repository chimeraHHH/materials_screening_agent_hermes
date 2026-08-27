from __future__ import annotations

import hashlib
from pathlib import Path

import pytest
from pydantic import ValidationError
from pymatgen.core import Structure

from material_agent.dft.qe import (
    QEMagneticMode,
    QEMethodSpec,
    QEPseudopotential,
    QESocMode,
    parse_qe_pw_output,
    render_qe_scf_input,
)
from material_agent.dft.qe_remote import build_qe_remote_scf_request
from material_agent.dft.qe_remote_worker import execute, prepare
from material_agent.inspiration.models import ArtifactPointerV1

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
MONOLAYER = (
    REPOSITORY_ROOT
    / "src/material_agent/inspiration/catalogs/flat_band_parent_catalog_v1"
    / "tis2-1t-vacuum-monolayer.cif"
)


def _pseudo(element: str, *, relativistic: bool = False) -> QEPseudopotential:
    return QEPseudopotential(
        element=element,
        filename=f"{element}.pbe.upf",
        sha256=("1" if element == "S" else "2") * 64,
        fully_relativistic=relativistic,
        valence_electrons=6 if element == "S" else 12,
    )


def test_qe_renderer_splits_one_element_into_afm_atomic_types() -> None:
    structure = Structure.from_file(MONOLAYER)
    structure.make_supercell((2, 1, 1))
    ti_indices = [
        index for index, site in enumerate(structure) if site.specie.symbol == "Ti"
    ]
    assert len(ti_indices) == 2
    moments = [0.0] * len(structure)
    moments[ti_indices[0]] = 1.0
    moments[ti_indices[1]] = -1.0
    method = QEMethodSpec(
        method_id="qe-pbe-tis2-collinear-v1",
        exchange_correlation="PBE",
        pseudopotential_set_id="pseudo-dojo-tis2-fixture",
        pseudopotentials={"S": _pseudo("S"), "Ti": _pseudo("Ti")},
        ecutwfc_ry=80,
        ecutrho_ry=640,
        kpoint_grid=(6, 12, 1),
        occupations="smearing",
        smearing="mv",
        degauss_ry=0.01,
        conv_thr_ry=1e-10,
        assume_isolated="2D",
        soc_mode=QESocMode.NON_SOC,
        magnetic_mode=QEMagneticMode.COLLINEAR,
        moment_scale_mu_b_by_element={"Ti": 2.0},
    )
    rendered = render_qe_scf_input(
        structure=structure,
        structure_sha256="3" * 64,
        method=method,
        prefix="tis2-afm",
        site_moments_mu_b=tuple(moments),
    )
    assert "nspin = 2" in rendered.text
    assert "Ti1" in rendered.text and "Ti2" in rendered.text
    assert "starting_magnetization(" in rendered.text
    assert 0.5 in rendered.site_starting_magnetization_fraction
    assert -0.5 in rendered.site_starting_magnetization_fraction
    assert not rendered.soc_explicit


def test_qe_soc_method_rejects_scalar_relativistic_pseudopotentials() -> None:
    with pytest.raises(ValidationError, match="fully relativistic"):
        QEMethodSpec(
            method_id="qe-pbe-tis2-soc-invalid",
            exchange_correlation="PBE",
            pseudopotential_set_id="invalid-soc-pseudos",
            pseudopotentials={"S": _pseudo("S"), "Ti": _pseudo("Ti")},
            ecutwfc_ry=80,
            ecutrho_ry=640,
            kpoint_grid=(12, 12, 1),
            occupations="fixed",
            conv_thr_ry=1e-10,
            soc_mode=QESocMode.SOC,
            magnetic_mode=QEMagneticMode.NONCOLLINEAR,
            moment_scale_mu_b_by_element={"Ti": 2.0},
        )


def test_qe_output_parser_requires_job_done_convergence_and_energy() -> None:
    parsed = parse_qe_pw_output(
        """
 Program PWSCF v.7.6 starts
 !    total energy              =     -16.92069827 Ry
 the Fermi energy is    1.2345 ev
 convergence has been achieved in 11 iterations
 PWSCF        :      0.30s CPU      0.34s WALL
 JOB DONE.
 """
    )
    assert parsed.job_done and parsed.electronic_converged
    assert parsed.total_energy_ry == pytest.approx(-16.92069827)
    assert parsed.fermi_energy_ev == pytest.approx(1.2345)
    assert parsed.wall_time_seconds == pytest.approx(0.34)
    assert parsed.reason_codes == ("QE_SCF_OUTPUT_VALIDATED",)


def test_qe_remote_worker_is_hash_bound_and_idempotent(tmp_path: Path) -> None:
    stack = tmp_path / "stack"
    run_root = tmp_path / "runs"
    binary = stack / "qe-7.6-install/bin/pw.x"
    binary.parent.mkdir(parents=True)
    binary.write_text(
        "#!/bin/sh\n"
        "printf ' Program PWSCF v.7.6 starts\\n'\n"
        "printf ' !    total energy = -1.00000000 Ry\\n'\n"
        "printf ' convergence has been achieved\\n'\n"
        "printf ' JOB DONE.\\n'\n",
        encoding="utf-8",
    )
    binary.chmod(0o755)
    run_root.mkdir()
    scf = b"&CONTROL\n/\nATOMIC_SPECIES\nSi 1 Si.upf\n"
    pseudo = b"fixture pseudo"
    request = {
        "schema_version": "hermes-qe-remote-scf-v1",
        "job_id": "qe-" + "1" * 24,
        "pw_binary_sha256": hashlib.sha256(binary.read_bytes()).hexdigest(),
        "scf_input_relative_path": "scf.in",
        "inputs": [
            {
                "remote_relative_path": "scf.in",
                "sha256": hashlib.sha256(scf).hexdigest(),
                "size_bytes": len(scf),
            },
            {
                "remote_relative_path": "pseudo/Si.upf",
                "sha256": hashlib.sha256(pseudo).hexdigest(),
                "size_bytes": len(pseudo),
            },
        ],
        "mpi_processes": 1,
        "omp_threads": 1,
        "wall_time_seconds": 10,
        "is_mock": False,
    }
    prepared = prepare(root=run_root, stack=stack, request=request)
    assert prepared["status"] == "PREPARED"
    job = run_root / request["job_id"]
    (job / "scf.in").write_bytes(scf)
    (job / "pseudo/Si.upf").write_bytes(pseudo)
    first = execute(root=run_root, stack=stack, job_id=request["job_id"])
    second = execute(root=run_root, stack=stack, job_id=request["job_id"])
    assert first == second
    assert first["status"] == "SUCCEEDED"
    assert first["pw_binary_sha256"] == request["pw_binary_sha256"]


def test_qe_remote_request_identity_includes_every_input_hash() -> None:
    scf = ArtifactPointerV1(
        uri="artifact://qe/scf.in",
        sha256="4" * 64,
        size_bytes=100,
        media_type="text/plain",
    )
    pseudo = ArtifactPointerV1(
        uri="artifact://qe/Si.upf",
        sha256="5" * 64,
        size_bytes=200,
        media_type="application/x-upf",
    )
    request = build_qe_remote_scf_request(
        pw_binary_sha256="6" * 64,
        scf_input=scf,
        pseudopotentials={"Si.upf": pseudo},
    )
    changed = build_qe_remote_scf_request(
        pw_binary_sha256="6" * 64,
        scf_input=scf,
        pseudopotentials={
            "Si.upf": pseudo.model_copy(update={"sha256": "7" * 64})
        },
    )
    assert request.job_id != changed.job_id
