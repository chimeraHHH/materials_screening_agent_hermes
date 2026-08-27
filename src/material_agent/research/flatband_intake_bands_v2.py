"""Fermi-aligned, non-Gold band pre-audit for calibration-only C2DB intake."""

from __future__ import annotations

import hashlib
import json
import math
import re
from pathlib import Path
from typing import Annotated, Literal

import numpy as np
from pydantic import Field, model_validator

from material_agent.inspiration.models import Sha256, StrictModel, canonical_sha256
from material_agent.research.flatband_contracts import (
    ObservedBandClass,
    observed_band_class,
)
from material_agent.research.flatband_intake import (
    FlatbandCalibrationIntakeManifestV1,
    FlatbandCalibrationSelectionManifestV1,
)

_PBE_FERMI_LABEL = "Fermi level wrt. vacuum (PBE) [eV]"
_PBE_VBM_LABEL = "VBM wrt. vacuum (PBE) [eV]"


class FlatbandCalibrationFermiPreauditRecordV2(StrictModel):
    schema_version: Literal["flatband-calibration-fermi-preaudit-record-v2"] = (
        "flatband-calibration-fermi-preaudit-record-v2"
    )
    source_rank: Annotated[int, Field(ge=1)]
    c2db_record_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
    source_intake_manifest_sha256: Sha256
    band_page_sha256: Sha256
    trace_name: Literal["PBE no SOC"] = "PBE no SOC"
    plot_energy_reference: Literal["FERMI", "VBM"]
    fermi_alignment_method: Literal[
        "PLOT_FERMI_ZERO",
        "PBE_VACUUM_TABLE_DIFFERENCE",
        "UNAVAILABLE",
    ]
    pbe_fermi_wrt_vacuum_e_v: float | None = None
    pbe_vbm_wrt_vacuum_e_v: float | None = None
    fermi_coordinate_in_plot_e_v: float | None = None
    fermi_window_e_v: Literal[1.0] = 1.0
    window_status: Literal[
        "BAND_FOUND_IN_FERMI_WINDOW",
        "NO_BAND_IN_FERMI_WINDOW",
        "FERMI_ALIGNMENT_UNAVAILABLE",
    ]
    narrowest_band_index_in_window: Annotated[int, Field(ge=0)] | None = None
    narrowest_bandwidth_e_v: Annotated[float, Field(ge=0, le=100)] | None = None
    distance_to_fermi_e_v: Annotated[float, Field(ge=0, le=100)] | None = None
    algorithmic_bandwidth_stratum: ObservedBandClass | None = None
    band_count: Annotated[int, Field(ge=2)]
    k_point_count: Annotated[int, Field(ge=3)]
    target_class_assignment_authorized: Literal[False] = False
    gold_label_status: Literal["NOT_ANNOTATED"] = "NOT_ANNOTATED"
    selected_using_band_outcome: Literal[False] = False
    soc_explicit: Literal[False] = False
    scientific_conclusion: Literal[False] = False

    @model_validator(mode="after")
    def validate_record(self) -> FlatbandCalibrationFermiPreauditRecordV2:
        quantitative = (
            self.narrowest_band_index_in_window,
            self.narrowest_bandwidth_e_v,
            self.distance_to_fermi_e_v,
            self.algorithmic_bandwidth_stratum,
        )
        if self.window_status == "BAND_FOUND_IN_FERMI_WINDOW":
            if any(value is None for value in quantitative):
                raise ValueError("Fermi-window band status requires quantitative fields")
            if self.algorithmic_bandwidth_stratum is not observed_band_class(
                bandwidth_e_v=self.narrowest_bandwidth_e_v
            ):
                raise ValueError("Fermi pre-audit stratum differs from thresholds")
        elif any(value is not None for value in quantitative):
            raise ValueError("unresolved Fermi-window status cannot carry band values")
        numeric = (
            self.pbe_fermi_wrt_vacuum_e_v,
            self.pbe_vbm_wrt_vacuum_e_v,
            self.fermi_coordinate_in_plot_e_v,
            self.narrowest_bandwidth_e_v,
            self.distance_to_fermi_e_v,
        )
        if any(value is not None and not math.isfinite(value) for value in numeric):
            raise ValueError("Fermi pre-audit values must be finite")
        if self.fermi_alignment_method == "UNAVAILABLE":
            if self.window_status != "FERMI_ALIGNMENT_UNAVAILABLE":
                raise ValueError("unavailable alignment must fail closed")
        elif self.fermi_coordinate_in_plot_e_v is None:
            raise ValueError("resolved Fermi alignment requires a plot coordinate")
        return self


class FlatbandCalibrationFermiPreauditManifestV2(StrictModel):
    schema_version: Literal["flatband-calibration-fermi-preaudit-manifest-v2"] = (
        "flatband-calibration-fermi-preaudit-manifest-v2"
    )
    manifest_sha256: Sha256
    selection_manifest_sha256: Sha256
    records: Annotated[
        tuple[FlatbandCalibrationFermiPreauditRecordV2, ...],
        Field(min_length=12, max_length=12),
    ]
    band_found_count: Annotated[int, Field(ge=0, le=12)]
    no_band_in_window_count: Annotated[int, Field(ge=0, le=12)]
    alignment_unavailable_count: Annotated[int, Field(ge=0, le=12)]
    fb100_count: Annotated[int, Field(ge=0, le=12)]
    nb300_count: Annotated[int, Field(ge=0, le=12)]
    border500_count: Annotated[int, Field(ge=0, le=12)]
    out_of_scope_count: Annotated[int, Field(ge=0, le=12)]
    calibration_only: Literal[True] = True
    pilot_execution_authorized: Literal[False] = False
    target_class_assignment_authorized: Literal[False] = False
    scientific_conclusion: Literal[False] = False

    @model_validator(mode="after")
    def validate_manifest(self) -> FlatbandCalibrationFermiPreauditManifestV2:
        ranks = tuple(item.source_rank for item in self.records)
        ids = tuple(item.c2db_record_id for item in self.records)
        if ranks != tuple(sorted(set(ranks))) or len(ids) != len(set(ids)):
            raise ValueError("Fermi pre-audit records must be rank-sorted and unique")
        statuses = {
            "BAND_FOUND_IN_FERMI_WINDOW": self.band_found_count,
            "NO_BAND_IN_FERMI_WINDOW": self.no_band_in_window_count,
            "FERMI_ALIGNMENT_UNAVAILABLE": self.alignment_unavailable_count,
        }
        for status, count in statuses.items():
            if count != sum(item.window_status == status for item in self.records):
                raise ValueError("Fermi pre-audit status count does not replay")
        class_counts = {
            ObservedBandClass.FB100: self.fb100_count,
            ObservedBandClass.NB300: self.nb300_count,
            ObservedBandClass.BORDER500: self.border500_count,
            ObservedBandClass.OUT_OF_SCOPE: self.out_of_scope_count,
        }
        for band_class, count in class_counts.items():
            if count != sum(
                item.algorithmic_bandwidth_stratum is band_class
                for item in self.records
            ):
                raise ValueError("Fermi pre-audit class count does not replay")
        expected = canonical_sha256(
            self.model_dump(mode="python", exclude={"manifest_sha256"})
        )
        if self.manifest_sha256 != expected:
            raise ValueError("Fermi pre-audit manifest SHA-256 does not match")
        return self


class FlatbandFermiCandidatePoolPreauditManifestV1(StrictModel):
    schema_version: Literal["flatband-fermi-candidate-pool-preaudit-manifest-v1"] = (
        "flatband-fermi-candidate-pool-preaudit-manifest-v1"
    )
    manifest_sha256: Sha256
    source_manifest_sha256s: Annotated[
        tuple[Sha256, ...], Field(min_length=1, max_length=32)
    ]
    records: Annotated[
        tuple[FlatbandCalibrationFermiPreauditRecordV2, ...],
        Field(min_length=1, max_length=96),
    ]
    unavailable_record_ids: Annotated[tuple[str, ...], Field(max_length=96)] = ()
    algorithmic_positive_record_ids: Annotated[
        tuple[str, ...], Field(max_length=96)
    ] = ()
    candidate_pool_only: Literal[True] = True
    calibration_only: Literal[True] = True
    pilot_execution_authorized: Literal[False] = False
    target_class_assignment_authorized: Literal[False] = False
    scientific_conclusion: Literal[False] = False

    @model_validator(mode="after")
    def validate_manifest(self) -> FlatbandFermiCandidatePoolPreauditManifestV1:
        if self.source_manifest_sha256s != tuple(
            sorted(set(self.source_manifest_sha256s))
        ):
            raise ValueError("candidate-pool source manifests must be sorted and unique")
        ranks = tuple(item.source_rank for item in self.records)
        ids = tuple(item.c2db_record_id for item in self.records)
        if ranks != tuple(sorted(set(ranks))) or len(ids) != len(set(ids)):
            raise ValueError("candidate-pool records must be rank-sorted and unique")
        if self.unavailable_record_ids != tuple(sorted(set(self.unavailable_record_ids))):
            raise ValueError("unavailable record IDs must be sorted and unique")
        expected_positive = tuple(
            item.c2db_record_id
            for item in self.records
            if item.window_status == "BAND_FOUND_IN_FERMI_WINDOW"
            and item.algorithmic_bandwidth_stratum
            in {ObservedBandClass.FB100, ObservedBandClass.NB300}
        )
        if self.algorithmic_positive_record_ids != expected_positive:
            raise ValueError("candidate-pool positive IDs do not replay from records")
        if set(ids) & set(self.unavailable_record_ids):
            raise ValueError("audited and unavailable candidate-pool IDs overlap")
        expected = canonical_sha256(
            self.model_dump(mode="python", exclude={"manifest_sha256"})
        )
        if self.manifest_sha256 != expected:
            raise ValueError("candidate-pool pre-audit manifest SHA-256 does not match")
        return self


class FermiQualifiedSelectionCandidateV2(StrictModel):
    source_rank: Annotated[int, Field(ge=1)]
    c2db_record_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
    source_intake_manifest_sha256: Sha256
    source_pool_preaudit_manifest_sha256: Sha256
    intake_status: Literal["COMPLETE", "PARTIAL", "FAILED"]
    fermi_window_status: Literal[
        "BAND_FOUND_IN_FERMI_WINDOW",
        "NO_BAND_IN_FERMI_WINDOW",
        "FERMI_ALIGNMENT_UNAVAILABLE",
        "NOT_AUDITED_UNAVAILABLE_INTAKE",
    ]
    algorithmic_bandwidth_stratum: ObservedBandClass | None = None
    qualification_status: Literal[
        "ALGORITHMIC_FB_NB_CANDIDATE",
        "NONPOSITIVE_FERMI_PREAUDIT",
        "UNAVAILABLE_INTAKE",
    ]

    @model_validator(mode="after")
    def validate_candidate(self) -> FermiQualifiedSelectionCandidateV2:
        positive = self.algorithmic_bandwidth_stratum in {
            ObservedBandClass.FB100,
            ObservedBandClass.NB300,
        }
        if self.qualification_status == "ALGORITHMIC_FB_NB_CANDIDATE":
            if self.intake_status != "COMPLETE" or not positive:
                raise ValueError("qualified candidate requires complete FB/NB pre-audit")
        elif self.qualification_status == "UNAVAILABLE_INTAKE":
            if (
                self.intake_status == "COMPLETE"
                or self.fermi_window_status != "NOT_AUDITED_UNAVAILABLE_INTAKE"
                or self.algorithmic_bandwidth_stratum is not None
            ):
                raise ValueError("unavailable candidate must fail closed")
        elif positive:
            raise ValueError("positive Fermi pre-audit must use qualified status")
        return self


class FlatbandFermiQualifiedSelectionManifestV2(StrictModel):
    schema_version: Literal[
        "flatband-fermi-qualified-calibration-selection-manifest-v2"
    ] = "flatband-fermi-qualified-calibration-selection-manifest-v2"
    manifest_sha256: Sha256
    source_intake_manifest_sha256s: Annotated[
        tuple[Sha256, ...], Field(min_length=1, max_length=32)
    ]
    source_pool_preaudit_manifest_sha256s: Annotated[
        tuple[Sha256, ...], Field(min_length=1, max_length=32)
    ]
    candidates: Annotated[
        tuple[FermiQualifiedSelectionCandidateV2, ...],
        Field(min_length=12, max_length=96),
    ]
    selected_record_ids: Annotated[tuple[str, ...], Field(min_length=12, max_length=12)]
    positive_not_selected_record_ids: Annotated[tuple[str, ...], Field(max_length=84)]
    nonpositive_record_ids: Annotated[tuple[str, ...], Field(max_length=96)]
    unavailable_record_ids: Annotated[tuple[str, ...], Field(max_length=96)]
    selection_rule: Literal[
        "LOWEST_SOURCE_RANK_COMPLETE_FERMI_WINDOW_FB100_OR_NB300_UNTIL_12"
    ] = "LOWEST_SOURCE_RANK_COMPLETE_FERMI_WINDOW_FB100_OR_NB300_UNTIL_12"
    selection_uses_algorithmic_eligibility: Literal[True] = True
    expert_target_confirmation_required: Literal[True] = True
    calibration_only: Literal[True] = True
    gold_label_status: Literal["NOT_ANNOTATED"] = "NOT_ANNOTATED"
    pilot_execution_authorized: Literal[False] = False
    target_class_assignment_authorized: Literal[False] = False
    scientific_conclusion: Literal[False] = False

    @property
    def source_manifest_sha256s(self) -> tuple[str, ...]:
        """Compatibility projection used by private structure custody."""

        return self.source_intake_manifest_sha256s

    @model_validator(mode="after")
    def validate_selection(self) -> FlatbandFermiQualifiedSelectionManifestV2:
        for values, label in (
            (self.source_intake_manifest_sha256s, "source intake manifests"),
            (self.source_pool_preaudit_manifest_sha256s, "pool pre-audits"),
            (self.positive_not_selected_record_ids, "unselected positives"),
            (self.nonpositive_record_ids, "nonpositive records"),
            (self.unavailable_record_ids, "unavailable records"),
        ):
            if values != tuple(sorted(set(values))):
                raise ValueError(f"{label} must be sorted and unique")
        ranks = tuple(item.source_rank for item in self.candidates)
        ids = tuple(item.c2db_record_id for item in self.candidates)
        if ranks != tuple(sorted(set(ranks))) or len(ids) != len(set(ids)):
            raise ValueError("selection candidates must be rank-sorted and unique")
        qualified = tuple(
            item.c2db_record_id
            for item in self.candidates
            if item.qualification_status == "ALGORITHMIC_FB_NB_CANDIDATE"
        )
        if len(qualified) < 12 or self.selected_record_ids != qualified[:12]:
            raise ValueError("selection must take the first 12 qualified source ranks")
        if self.positive_not_selected_record_ids != tuple(sorted(qualified[12:])):
            raise ValueError("unselected positive records do not replay")
        expected_nonpositive = tuple(
            sorted(
                item.c2db_record_id
                for item in self.candidates
                if item.qualification_status == "NONPOSITIVE_FERMI_PREAUDIT"
            )
        )
        expected_unavailable = tuple(
            sorted(
                item.c2db_record_id
                for item in self.candidates
                if item.qualification_status == "UNAVAILABLE_INTAKE"
            )
        )
        if self.nonpositive_record_ids != expected_nonpositive:
            raise ValueError("nonpositive records do not replay")
        if self.unavailable_record_ids != expected_unavailable:
            raise ValueError("unavailable records do not replay")
        expected = canonical_sha256(
            self.model_dump(mode="python", exclude={"manifest_sha256"})
        )
        if self.manifest_sha256 != expected:
            raise ValueError("Fermi-qualified selection manifest SHA-256 does not match")
        return self


def _table_number(page: str, label: str) -> float | None:
    pattern = re.compile(
        rf'<td class="w-50">{re.escape(label)}</td>\s*'
        r'<td class="w-50">\s*([-+]?(?:\d+(?:\.\d*)?|\.\d+))\s*</td>'
    )
    matches = tuple(pattern.finditer(page))
    if len(matches) != 1:
        return None
    return float(matches[0].group(1))


def _pbe_bands(page: str) -> tuple[np.ndarray, str]:
    marker = "Plotly.newPlot('bandstructure', graphs, {});"
    end = page.find(marker)
    assignment = page.rfind("var graphs = ", 0, end)
    if end < 0 or assignment < 0:
        raise ValueError("C2DB band page is missing its Plotly payload")
    graph, _ = json.JSONDecoder().raw_decode(
        page[assignment + len("var graphs = ") : end]
    )
    if not isinstance(graph, dict) or not isinstance(graph.get("data"), list):
        raise TypeError("C2DB band Plotly payload is malformed")
    traces = [
        item
        for item in graph["data"]
        if isinstance(item, dict) and item.get("name") == "PBE no SOC"
    ]
    if len(traces) != 1:
        raise ValueError("C2DB PBE no SOC trace must resolve exactly once")
    x = np.asarray(traces[0].get("x"), dtype=float)
    y = np.asarray(traces[0].get("y"), dtype=float)
    if x.ndim != 1 or y.ndim != 1 or len(x) != len(y) or len(x) < 6:
        raise ValueError("C2DB PBE trace arrays have invalid shape")
    if not np.isfinite(x).all() or not np.isfinite(y).all():
        raise ValueError("C2DB PBE trace contains non-finite values")
    boundaries = np.flatnonzero(x[1:] <= x[:-1])
    if not len(boundaries):
        raise ValueError("C2DB PBE trace has no repeated k grid")
    period = int(boundaries[0] + 1)
    if period < 3 or len(x) % period:
        raise ValueError("C2DB PBE trace has an invalid repeated k grid")
    layout = graph.get("layout")
    yaxis = layout.get("yaxis") if isinstance(layout, dict) else None
    title = yaxis.get("title") if isinstance(yaxis, dict) else None
    title_text = title.get("text") if isinstance(title, dict) else None
    if not isinstance(title_text, str):
        raise TypeError("C2DB band page lacks an explicit energy reference")
    if "<sub>F</sub>" in title_text:
        reference = "FERMI"
    elif "<sub>VBM" in title_text:
        reference = "VBM"
    else:
        raise ValueError("C2DB band page energy reference is unsupported")
    return y.reshape((-1, period)), reference


def preaudit_c2db_band_page_fermi_v2(
    page: bytes,
    *,
    source_rank: int,
    c2db_record_id: str,
    source_intake_manifest_sha256: str,
    band_page_sha256: str,
) -> FlatbandCalibrationFermiPreauditRecordV2:
    """Align the PBE/no-SOC trace to the page's explicit PBE Fermi energy."""

    text = page.decode("utf-8")
    bands, reference = _pbe_bands(text)
    pbe_fermi = _table_number(text, _PBE_FERMI_LABEL)
    pbe_vbm = _table_number(text, _PBE_VBM_LABEL)
    if reference == "FERMI":
        method = "PLOT_FERMI_ZERO"
        coordinate = 0.0
    elif pbe_fermi is not None and pbe_vbm is not None:
        method = "PBE_VACUUM_TABLE_DIFFERENCE"
        coordinate = pbe_fermi - pbe_vbm
    else:
        method = "UNAVAILABLE"
        coordinate = None
    common = {
        "source_rank": source_rank,
        "c2db_record_id": c2db_record_id,
        "source_intake_manifest_sha256": source_intake_manifest_sha256,
        "band_page_sha256": band_page_sha256,
        "plot_energy_reference": reference,
        "fermi_alignment_method": method,
        "pbe_fermi_wrt_vacuum_e_v": pbe_fermi,
        "pbe_vbm_wrt_vacuum_e_v": pbe_vbm,
        "fermi_coordinate_in_plot_e_v": coordinate,
        "band_count": int(bands.shape[0]),
        "k_point_count": int(bands.shape[1]),
    }
    if coordinate is None:
        return FlatbandCalibrationFermiPreauditRecordV2(
            **common, window_status="FERMI_ALIGNMENT_UNAVAILABLE"
        )
    distances = np.min(np.abs(bands - coordinate), axis=1)
    within = np.flatnonzero(distances <= 1.0)
    if not len(within):
        return FlatbandCalibrationFermiPreauditRecordV2(
            **common, window_status="NO_BAND_IN_FERMI_WINDOW"
        )
    widths = np.ptp(bands[within], axis=1)
    width, distance, band_index = min(
        (
            (float(widths[offset]), float(distances[index]), int(index))
            for offset, index in enumerate(within)
        ),
        key=lambda item: item,
    )
    return FlatbandCalibrationFermiPreauditRecordV2(
        **common,
        window_status="BAND_FOUND_IN_FERMI_WINDOW",
        narrowest_band_index_in_window=band_index,
        narrowest_bandwidth_e_v=width,
        distance_to_fermi_e_v=distance,
        algorithmic_bandwidth_stratum=observed_band_class(bandwidth_e_v=width),
    )


def build_calibration_fermi_preaudit_v2(
    *,
    selection: FlatbandCalibrationSelectionManifestV1,
    intake_sources: tuple[
        tuple[FlatbandCalibrationIntakeManifestV1, Path], ...
    ],
    output_path: Path,
) -> FlatbandCalibrationFermiPreauditManifestV2:
    """Build a 12-record Fermi-aligned pre-audit without assigning Gold."""

    selected = FlatbandCalibrationSelectionManifestV1.model_validate(
        selection.model_dump(mode="python", round_trip=True)
    )
    record_map: dict[str, tuple[str, int, Path, str]] = {}
    source_shas: set[str] = set()
    for manifest, root in intake_sources:
        validated = FlatbandCalibrationIntakeManifestV1.model_validate(
            manifest.model_dump(mode="python", round_trip=True)
        )
        source_shas.add(validated.manifest_sha256)
        for record in validated.records:
            band = next(
                (
                    item
                    for item in record.artifacts
                    if item.artifact_role == "C2DB_BAND_PAGE"
                ),
                None,
            )
            if band is not None:
                record_map[record.c2db_record_id] = (
                    validated.manifest_sha256,
                    record.seed.source_rank,
                    root / band.local_relative_path,
                    band.sha256,
                )
    if source_shas != set(selected.source_manifest_sha256s):
        raise ValueError("Fermi pre-audit intake roots differ from selection roots")
    records: list[FlatbandCalibrationFermiPreauditRecordV2] = []
    for record_id in selected.selected_record_ids:
        manifest_sha, rank, path, expected_sha = record_map[record_id]
        payload = path.read_bytes()
        if hashlib.sha256(payload).hexdigest() != expected_sha:
            raise ValueError("Fermi pre-audit source page hash drifted")
        records.append(
            preaudit_c2db_band_page_fermi_v2(
                payload,
                source_rank=rank,
                c2db_record_id=record_id,
                source_intake_manifest_sha256=manifest_sha,
                band_page_sha256=expected_sha,
            )
        )
    ordered = tuple(sorted(records, key=lambda item: item.source_rank))
    values = {
        "selection_manifest_sha256": selected.manifest_sha256,
        "records": ordered,
        "band_found_count": sum(
            item.window_status == "BAND_FOUND_IN_FERMI_WINDOW" for item in ordered
        ),
        "no_band_in_window_count": sum(
            item.window_status == "NO_BAND_IN_FERMI_WINDOW" for item in ordered
        ),
        "alignment_unavailable_count": sum(
            item.window_status == "FERMI_ALIGNMENT_UNAVAILABLE" for item in ordered
        ),
        "fb100_count": sum(
            item.algorithmic_bandwidth_stratum is ObservedBandClass.FB100
            for item in ordered
        ),
        "nb300_count": sum(
            item.algorithmic_bandwidth_stratum is ObservedBandClass.NB300
            for item in ordered
        ),
        "border500_count": sum(
            item.algorithmic_bandwidth_stratum is ObservedBandClass.BORDER500
            for item in ordered
        ),
        "out_of_scope_count": sum(
            item.algorithmic_bandwidth_stratum is ObservedBandClass.OUT_OF_SCOPE
            for item in ordered
        ),
    }
    draft = FlatbandCalibrationFermiPreauditManifestV2.model_construct(
        manifest_sha256="0" * 64, **values
    )
    result = FlatbandCalibrationFermiPreauditManifestV2.model_validate(
        {
            **values,
            "manifest_sha256": canonical_sha256(
                draft.model_dump(mode="python", exclude={"manifest_sha256"})
            ),
        }
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(
            result.model_dump(mode="json"),
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n",
        "utf-8",
    )
    output_path.chmod(0o600)
    return result


def build_fermi_candidate_pool_preaudit_v1(
    *,
    intake_sources: tuple[
        tuple[FlatbandCalibrationIntakeManifestV1, Path], ...
    ],
    output_path: Path,
) -> FlatbandFermiCandidatePoolPreauditManifestV1:
    """Audit an arbitrary predeclared replenishment pool without selecting cases."""

    source_shas: set[str] = set()
    records: list[FlatbandCalibrationFermiPreauditRecordV2] = []
    unavailable: set[str] = set()
    seen_ids: set[str] = set()
    for manifest, root in intake_sources:
        validated = FlatbandCalibrationIntakeManifestV1.model_validate(
            manifest.model_dump(mode="python", round_trip=True)
        )
        source_shas.add(validated.manifest_sha256)
        for record in validated.records:
            if record.c2db_record_id in seen_ids:
                raise ValueError("candidate-pool intake record is duplicated")
            seen_ids.add(record.c2db_record_id)
            band = next(
                (
                    item
                    for item in record.artifacts
                    if item.artifact_role == "C2DB_BAND_PAGE"
                ),
                None,
            )
            if record.status != "COMPLETE" or band is None:
                unavailable.add(record.c2db_record_id)
                continue
            payload = (root / band.local_relative_path).read_bytes()
            if hashlib.sha256(payload).hexdigest() != band.sha256:
                raise ValueError("candidate-pool source page hash drifted")
            records.append(
                preaudit_c2db_band_page_fermi_v2(
                    payload,
                    source_rank=record.seed.source_rank,
                    c2db_record_id=record.c2db_record_id,
                    source_intake_manifest_sha256=validated.manifest_sha256,
                    band_page_sha256=band.sha256,
                )
            )
    ordered = tuple(sorted(records, key=lambda item: item.source_rank))
    values = {
        "source_manifest_sha256s": tuple(sorted(source_shas)),
        "records": ordered,
        "unavailable_record_ids": tuple(sorted(unavailable)),
        "algorithmic_positive_record_ids": tuple(
            item.c2db_record_id
            for item in ordered
            if item.window_status == "BAND_FOUND_IN_FERMI_WINDOW"
            and item.algorithmic_bandwidth_stratum
            in {ObservedBandClass.FB100, ObservedBandClass.NB300}
        ),
    }
    draft = FlatbandFermiCandidatePoolPreauditManifestV1.model_construct(
        manifest_sha256="0" * 64, **values
    )
    result = FlatbandFermiCandidatePoolPreauditManifestV1.model_validate(
        {
            **values,
            "manifest_sha256": canonical_sha256(
                draft.model_dump(mode="python", exclude={"manifest_sha256"})
            ),
        }
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(
            result.model_dump(mode="json"),
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n",
        "utf-8",
    )
    output_path.chmod(0o600)
    return result


def build_fermi_qualified_selection_v2(
    *,
    intake_manifests: tuple[FlatbandCalibrationIntakeManifestV1, ...],
    pool_preaudits: tuple[FlatbandFermiCandidatePoolPreauditManifestV1, ...],
    output_path: Path,
) -> FlatbandFermiQualifiedSelectionManifestV2:
    """Select the lowest-ranked 12 FB/NB candidates from a frozen audited pool."""

    intakes = tuple(
        FlatbandCalibrationIntakeManifestV1.model_validate(
            item.model_dump(mode="python", round_trip=True)
        )
        for item in intake_manifests
    )
    audits = tuple(
        FlatbandFermiCandidatePoolPreauditManifestV1.model_validate(
            item.model_dump(mode="python", round_trip=True)
        )
        for item in pool_preaudits
    )
    intake_by_id = {}
    intake_shas = {item.manifest_sha256 for item in intakes}
    for intake in intakes:
        for record in intake.records:
            if record.c2db_record_id in intake_by_id:
                raise ValueError("Fermi selection receives a repeated intake record")
            intake_by_id[record.c2db_record_id] = (intake, record)
    audit_record_by_id = {}
    unavailable_to_audit = {}
    for audit in audits:
        if not set(audit.source_manifest_sha256s) <= intake_shas:
            raise ValueError("pool pre-audit references an unknown intake manifest")
        for record in audit.records:
            if record.c2db_record_id in audit_record_by_id:
                raise ValueError("Fermi selection receives repeated audited record")
            audit_record_by_id[record.c2db_record_id] = (audit, record)
        for record_id in audit.unavailable_record_ids:
            if record_id in unavailable_to_audit:
                raise ValueError("Fermi selection repeats an unavailable record")
            unavailable_to_audit[record_id] = audit
    if set(intake_by_id) != set(audit_record_by_id) | set(unavailable_to_audit):
        raise ValueError("pool pre-audits do not exactly cover intake candidates")
    candidates: list[FermiQualifiedSelectionCandidateV2] = []
    for record_id, (intake, source) in intake_by_id.items():
        if record_id in unavailable_to_audit:
            candidates.append(
                FermiQualifiedSelectionCandidateV2(
                    source_rank=source.seed.source_rank,
                    c2db_record_id=record_id,
                    source_intake_manifest_sha256=intake.manifest_sha256,
                    source_pool_preaudit_manifest_sha256=(
                        unavailable_to_audit[record_id].manifest_sha256
                    ),
                    intake_status=source.status,
                    fermi_window_status="NOT_AUDITED_UNAVAILABLE_INTAKE",
                    qualification_status="UNAVAILABLE_INTAKE",
                )
            )
            continue
        audit, band = audit_record_by_id[record_id]
        if band.source_intake_manifest_sha256 != intake.manifest_sha256:
            raise ValueError("audited record drifts from its intake manifest")
        positive = band.algorithmic_bandwidth_stratum in {
            ObservedBandClass.FB100,
            ObservedBandClass.NB300,
        }
        candidates.append(
            FermiQualifiedSelectionCandidateV2(
                source_rank=source.seed.source_rank,
                c2db_record_id=record_id,
                source_intake_manifest_sha256=intake.manifest_sha256,
                source_pool_preaudit_manifest_sha256=audit.manifest_sha256,
                intake_status=source.status,
                fermi_window_status=band.window_status,
                algorithmic_bandwidth_stratum=(
                    band.algorithmic_bandwidth_stratum
                ),
                qualification_status=(
                    "ALGORITHMIC_FB_NB_CANDIDATE"
                    if positive
                    else "NONPOSITIVE_FERMI_PREAUDIT"
                ),
            )
        )
    ordered = tuple(sorted(candidates, key=lambda item: item.source_rank))
    qualified = tuple(
        item.c2db_record_id
        for item in ordered
        if item.qualification_status == "ALGORITHMIC_FB_NB_CANDIDATE"
    )
    if len(qualified) < 12:
        raise ValueError("frozen Fermi-qualified pool contains fewer than 12 candidates")
    values = {
        "source_intake_manifest_sha256s": tuple(sorted(intake_shas)),
        "source_pool_preaudit_manifest_sha256s": tuple(
            sorted(item.manifest_sha256 for item in audits)
        ),
        "candidates": ordered,
        "selected_record_ids": qualified[:12],
        "positive_not_selected_record_ids": tuple(sorted(qualified[12:])),
        "nonpositive_record_ids": tuple(
            sorted(
                item.c2db_record_id
                for item in ordered
                if item.qualification_status == "NONPOSITIVE_FERMI_PREAUDIT"
            )
        ),
        "unavailable_record_ids": tuple(
            sorted(
                item.c2db_record_id
                for item in ordered
                if item.qualification_status == "UNAVAILABLE_INTAKE"
            )
        ),
    }
    draft = FlatbandFermiQualifiedSelectionManifestV2.model_construct(
        manifest_sha256="0" * 64, **values
    )
    result = FlatbandFermiQualifiedSelectionManifestV2.model_validate(
        {
            **values,
            "manifest_sha256": canonical_sha256(
                draft.model_dump(mode="python", exclude={"manifest_sha256"})
            ),
        }
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(
            result.model_dump(mode="json"),
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n",
        "utf-8",
    )
    output_path.chmod(0o600)
    return result


__all__ = [
    "FlatbandCalibrationFermiPreauditManifestV2",
    "FlatbandCalibrationFermiPreauditRecordV2",
    "FlatbandFermiQualifiedSelectionManifestV2",
    "FlatbandFermiCandidatePoolPreauditManifestV1",
    "FermiQualifiedSelectionCandidateV2",
    "build_calibration_fermi_preaudit_v2",
    "build_fermi_candidate_pool_preaudit_v1",
    "build_fermi_qualified_selection_v2",
    "preaudit_c2db_band_page_fermi_v2",
]
