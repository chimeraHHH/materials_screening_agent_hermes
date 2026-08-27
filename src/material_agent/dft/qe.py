"""Frozen Quantum ESPRESSO method contracts, input rendering and output parsing."""

from __future__ import annotations

import math
import re
from enum import StrEnum
from typing import Literal

from pydantic import Field, model_validator
from pymatgen.core import Element, Structure

from material_agent.inspiration.models import Identifier, StrictModel, canonical_sha256


class QESocMode(StrEnum):
    NON_SOC = "NON_SOC"
    SOC = "SOC"


class QEMagneticMode(StrEnum):
    NONMAGNETIC = "NONMAGNETIC"
    COLLINEAR = "COLLINEAR"
    NONCOLLINEAR = "NONCOLLINEAR"


class QEPseudopotential(StrictModel):
    element: str
    filename: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._+-]*\.([Uu][Pp][Ff])$")
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    fully_relativistic: bool
    valence_electrons: float = Field(gt=0, le=100)

    @model_validator(mode="after")
    def validate_element(self) -> QEPseudopotential:
        if not Element.is_valid_symbol(self.element):
            raise ValueError("QE pseudopotential element is invalid")
        return self


class QEMethodSpec(StrictModel):
    method_id: Identifier
    qe_version: Literal["7.6"] = "7.6"
    exchange_correlation: Literal["PBE", "PBESOL", "LDA"]
    pseudopotential_set_id: Identifier
    pseudopotentials: dict[str, QEPseudopotential] = Field(min_length=1, max_length=128)
    ecutwfc_ry: float = Field(gt=0, le=2_000)
    ecutrho_ry: float = Field(gt=0, le=20_000)
    kpoint_grid: tuple[int, int, int]
    kpoint_shift: tuple[Literal[0, 1], Literal[0, 1], Literal[0, 1]] = (0, 0, 0)
    occupations: Literal["fixed", "smearing"]
    smearing: Literal["gaussian", "mp", "mv", "fd"] | None = None
    degauss_ry: float | None = Field(default=None, gt=0, le=1)
    conv_thr_ry: float = Field(gt=0, le=1e-2)
    electron_maxstep: int = Field(default=200, ge=1, le=1_000)
    mixing_beta: float = Field(default=0.3, gt=0, le=1)
    assume_isolated: Literal["2D", "none"] = "2D"
    soc_mode: QESocMode
    magnetic_mode: QEMagneticMode
    moment_scale_mu_b_by_element: dict[str, float] = Field(
        default_factory=dict,
        max_length=128,
    )

    @model_validator(mode="after")
    def validate_method(self) -> QEMethodSpec:
        if self.pseudopotentials != dict(sorted(self.pseudopotentials.items())):
            raise ValueError("QE pseudopotentials must use canonical element order")
        if any(key != value.element for key, value in self.pseudopotentials.items()):
            raise ValueError("QE pseudopotential key differs from its element")
        if self.ecutrho_ry < self.ecutwfc_ry:
            raise ValueError("ecutrho must not be smaller than ecutwfc")
        if any(value < 1 or value > 100 for value in self.kpoint_grid):
            raise ValueError("QE k-point grid entries must be in [1, 100]")
        uses_smearing = self.occupations == "smearing"
        if uses_smearing != (self.smearing is not None and self.degauss_ry is not None):
            raise ValueError("QE smearing and degauss must match occupations")
        if self.soc_mode is QESocMode.SOC:
            if self.magnetic_mode is QEMagneticMode.COLLINEAR:
                raise ValueError("SOC requires a noncollinear or nonmagnetic method")
            if any(
                not pseudo.fully_relativistic
                for pseudo in self.pseudopotentials.values()
            ):
                raise ValueError("SOC requires fully relativistic pseudopotentials")
        if self.magnetic_mode is QEMagneticMode.COLLINEAR and self.soc_mode is QESocMode.SOC:
            raise ValueError("collinear SOC is not a valid QE method")
        if self.moment_scale_mu_b_by_element != dict(
            sorted(self.moment_scale_mu_b_by_element.items())
        ):
            raise ValueError("moment scales must use canonical element order")
        if any(
            key not in self.pseudopotentials or not math.isfinite(value) or value <= 0
            for key, value in self.moment_scale_mu_b_by_element.items()
        ):
            raise ValueError("QE moment scale is invalid or lacks a pseudopotential")
        if (
            self.magnetic_mode is not QEMagneticMode.NONMAGNETIC
            and not self.moment_scale_mu_b_by_element
        ):
            raise ValueError("magnetic QE method requires element moment scales")
        return self


class QERenderedInput(StrictModel):
    input_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    method_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    structure_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    prefix: Identifier
    text: str = Field(min_length=100)
    atomic_type_labels: tuple[str, ...]
    site_starting_magnetization_fraction: tuple[float, ...]
    soc_explicit: bool


class QEPWResult(StrictModel):
    qe_version: str
    job_done: bool
    electronic_converged: bool
    total_energy_ry: float | None
    fermi_energy_ev: float | None
    highest_occupied_energy_ev: float | None
    wall_time_seconds: float | None
    reason_codes: tuple[Identifier, ...]
    scientific_conclusion: Literal[False] = False


def render_qe_scf_input(
    *,
    structure: Structure,
    structure_sha256: str,
    method: QEMethodSpec,
    prefix: str,
    site_moments_mu_b: tuple[float, ...] | None = None,
) -> QERenderedInput:
    """Render a deterministic SCF input, including site-resolved AFM labels."""

    if not re.fullmatch(r"[a-z][a-z0-9-]{0,31}", prefix):
        raise ValueError("QE prefix must be a safe Hermes identifier")
    elements = tuple(site.specie.symbol for site in structure)
    missing = sorted(set(elements) - set(method.pseudopotentials))
    if missing:
        raise ValueError(f"QE method lacks pseudopotentials for {missing}")
    if site_moments_mu_b is None:
        site_moments_mu_b = tuple(0.0 for _ in structure)
    if len(site_moments_mu_b) != len(structure):
        raise ValueError("QE site moments differ from the structure size")
    if any(not math.isfinite(value) for value in site_moments_mu_b):
        raise ValueError("QE site moments must be finite")
    if method.magnetic_mode is QEMagneticMode.NONMAGNETIC and any(
        abs(value) > 1e-12 for value in site_moments_mu_b
    ):
        raise ValueError("nonmagnetic QE method cannot carry starting moments")

    fractions = tuple(
        _moment_fraction(element, moment, method)
        for element, moment in zip(elements, site_moments_mu_b, strict=True)
    )
    labels, type_order = _atomic_type_labels(elements, fractions)
    system: dict[str, object] = {
        "ibrav": 0,
        "nat": len(structure),
        "ntyp": len(type_order),
        "ecutwfc": method.ecutwfc_ry,
        "ecutrho": method.ecutrho_ry,
        "occupations": method.occupations,
    }
    if method.occupations == "smearing":
        system["smearing"] = method.smearing
        system["degauss"] = method.degauss_ry
    if method.assume_isolated != "none":
        system["assume_isolated"] = method.assume_isolated
    if method.magnetic_mode is QEMagneticMode.COLLINEAR:
        system["nspin"] = 2
    if method.magnetic_mode is QEMagneticMode.NONCOLLINEAR:
        system["noncolin"] = True
    if method.soc_mode is QESocMode.SOC:
        system["noncolin"] = True
        system["lspinorb"] = True
    for index, (_element, fraction) in enumerate(type_order, start=1):
        if method.magnetic_mode is not QEMagneticMode.NONMAGNETIC:
            system[f"starting_magnetization({index})"] = fraction

    lines = [
        _namelist(
            "CONTROL",
            {
                "calculation": "scf",
                "prefix": prefix,
                "pseudo_dir": "./pseudo",
                "outdir": "./tmp",
                "restart_mode": "from_scratch",
                "disk_io": "high",
            },
        ),
        _namelist("SYSTEM", system),
        _namelist(
            "ELECTRONS",
            {
                "conv_thr": method.conv_thr_ry,
                "electron_maxstep": method.electron_maxstep,
                "mixing_beta": method.mixing_beta,
            },
        ),
        "ATOMIC_SPECIES",
    ]
    for label, (element, _fraction) in zip(
        tuple(dict.fromkeys(labels)),
        type_order,
        strict=True,
    ):
        pseudo = method.pseudopotentials[element]
        lines.append(f" {label} {float(Element(element).atomic_mass):.8f} {pseudo.filename}")
    lines.append("ATOMIC_POSITIONS crystal")
    for label, site in zip(labels, structure, strict=True):
        x, y, z = (float(value) for value in site.frac_coords)
        lines.append(f" {label} {x:.12f} {y:.12f} {z:.12f}")
    lines.append("K_POINTS automatic")
    lines.append(
        " "
        + " ".join(str(value) for value in (*method.kpoint_grid, *method.kpoint_shift))
    )
    lines.append("CELL_PARAMETERS angstrom")
    lines.extend(
        " " + " ".join(f"{float(value):.12f}" for value in vector)
        for vector in structure.lattice.matrix
    )
    text = "\n".join(lines) + "\n"
    return QERenderedInput(
        input_sha256=canonical_sha256({"text": text}),
        method_sha256=canonical_sha256(method),
        structure_sha256=structure_sha256,
        prefix=prefix,
        text=text,
        atomic_type_labels=tuple(labels),
        site_starting_magnetization_fraction=fractions,
        soc_explicit=method.soc_mode is QESocMode.SOC,
    )


def parse_qe_pw_output(text: str) -> QEPWResult:
    version_match = re.search(r"Program PWSCF v\.([^\s]+)", text)
    total_match = re.findall(
        r"!\s+total energy\s+=\s+([-+0-9.Ee]+)\s+Ry",
        text,
    )
    fermi_match = re.findall(
        r"the Fermi energy is\s+([-+0-9.Ee]+)\s+ev",
        text,
        flags=re.IGNORECASE,
    )
    occupied_match = re.findall(
        r"highest occupied level \(ev\):\s+([-+0-9.Ee]+)",
        text,
        flags=re.IGNORECASE,
    )
    wall_match = re.search(r"PWSCF\s+:.*?([0-9.]+)s WALL", text)
    reasons: set[str] = set()
    job_done = "JOB DONE." in text
    converged = "convergence has been achieved" in text
    if version_match is None:
        reasons.add("QE_VERSION_UNRESOLVED")
    if not job_done:
        reasons.add("QE_JOB_NOT_DONE")
    if not converged:
        reasons.add("QE_ELECTRONIC_CONVERGENCE_FAILED")
    if not total_match:
        reasons.add("QE_TOTAL_ENERGY_MISSING")
    if not reasons:
        reasons.add("QE_SCF_OUTPUT_VALIDATED")
    return QEPWResult(
        qe_version=version_match.group(1) if version_match else "unknown",
        job_done=job_done,
        electronic_converged=converged,
        total_energy_ry=float(total_match[-1]) if total_match else None,
        fermi_energy_ev=float(fermi_match[-1]) if fermi_match else None,
        highest_occupied_energy_ev=(
            float(occupied_match[-1]) if occupied_match else None
        ),
        wall_time_seconds=float(wall_match.group(1)) if wall_match else None,
        reason_codes=tuple(sorted(reasons)),
    )


def _moment_fraction(element: str, moment: float, method: QEMethodSpec) -> float:
    if method.magnetic_mode is QEMagneticMode.NONMAGNETIC:
        return 0.0
    scale = method.moment_scale_mu_b_by_element.get(element)
    if scale is None:
        if abs(moment) <= 1e-12:
            return 0.0
        raise ValueError(f"QE method lacks a moment scale for magnetic {element}")
    fraction = moment / scale
    if abs(fraction) > 1 + 1e-12:
        raise ValueError("QE starting magnetization exceeds the frozen moment scale")
    return max(-1.0, min(1.0, fraction))


def _atomic_type_labels(
    elements: tuple[str, ...],
    fractions: tuple[float, ...],
) -> tuple[tuple[str, ...], tuple[tuple[str, float], ...]]:
    unique = tuple(dict.fromkeys((element, round(value, 10)) for element, value in zip(elements, fractions, strict=True)))
    if len(unique) > 10:
        raise ValueError("QE magnetic type splitting exceeds ntypx=10")
    counters: dict[str, int] = {}
    label_by_type: dict[tuple[str, float], str] = {}
    for element, fraction in unique:
        counters[element] = counters.get(element, 0) + 1
        suffix = counters[element]
        label = element if suffix == 1 and sum(item[0] == element for item in unique) == 1 else f"{element}{suffix}"
        if len(label) > 3:
            raise ValueError("QE atomic type label exceeds the portable 3-character limit")
        label_by_type[(element, fraction)] = label
    labels = tuple(
        label_by_type[(element, round(fraction, 10))]
        for element, fraction in zip(elements, fractions, strict=True)
    )
    return labels, unique


def _namelist(name: str, values: dict[str, object]) -> str:
    lines = [f"&{name}"]
    for key in sorted(values):
        value = values[key]
        if value is None:
            continue
        if isinstance(value, bool):
            rendered = ".true." if value else ".false."
        elif isinstance(value, str):
            rendered = f"'{value}'"
        elif isinstance(value, float):
            rendered = f"{value:.12g}"
        else:
            rendered = str(value)
        lines.append(f"  {key} = {rendered},")
    lines.append("/")
    return "\n".join(lines)
