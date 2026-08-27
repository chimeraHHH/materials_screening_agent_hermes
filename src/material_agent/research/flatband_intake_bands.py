"""Deterministic, non-Gold band pre-audit for calibration-only C2DB intake."""

from __future__ import annotations

import hashlib
import json
import math
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


class FlatbandCalibrationBandPreauditRecordV1(StrictModel):
    schema_version: Literal["flatband-calibration-band-preaudit-record-v1"] = (
        "flatband-calibration-band-preaudit-record-v1"
    )
    source_rank: Annotated[int, Field(ge=1)]
    c2db_record_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
    source_intake_manifest_sha256: Sha256
    band_page_sha256: Sha256
    trace_name: Literal["PBE no SOC"] = "PBE no SOC"
    energy_reference: Literal["FERMI", "VBM"]
    window_about_published_reference_e_v: Literal[1.0] = 1.0
    narrowest_band_index_in_window: Annotated[int, Field(ge=0)]
    narrowest_bandwidth_e_v: Annotated[float, Field(ge=0, le=100)]
    distance_to_published_reference_e_v: Annotated[float, Field(ge=0, le=100)]
    algorithmic_bandwidth_stratum: ObservedBandClass
    band_count: Annotated[int, Field(ge=2)]
    k_point_count: Annotated[int, Field(ge=3)]
    target_class_assignment_authorized: Literal[False] = False
    gold_label_status: Literal["NOT_ANNOTATED"] = "NOT_ANNOTATED"
    selected_using_band_outcome: Literal[False] = False
    soc_explicit: Literal[False] = False
    scientific_conclusion: Literal[False] = False

    @model_validator(mode="after")
    def validate_record(self) -> FlatbandCalibrationBandPreauditRecordV1:
        if not math.isfinite(self.narrowest_bandwidth_e_v) or not math.isfinite(
            self.distance_to_published_reference_e_v
        ):
            raise ValueError("band pre-audit values must be finite")
        if self.algorithmic_bandwidth_stratum is not observed_band_class(
            bandwidth_e_v=self.narrowest_bandwidth_e_v
        ):
            raise ValueError("band pre-audit stratum differs from frozen thresholds")
        return self


class FlatbandCalibrationBandPreauditManifestV1(StrictModel):
    schema_version: Literal["flatband-calibration-band-preaudit-manifest-v1"] = (
        "flatband-calibration-band-preaudit-manifest-v1"
    )
    manifest_sha256: Sha256
    selection_manifest_sha256: Sha256
    records: Annotated[
        tuple[FlatbandCalibrationBandPreauditRecordV1, ...],
        Field(min_length=12, max_length=12),
    ]
    fb100_count: Annotated[int, Field(ge=0, le=12)]
    nb300_count: Annotated[int, Field(ge=0, le=12)]
    border500_count: Annotated[int, Field(ge=0, le=12)]
    out_of_scope_count: Annotated[int, Field(ge=0, le=12)]
    calibration_only: Literal[True] = True
    pilot_execution_authorized: Literal[False] = False
    target_class_assignment_authorized: Literal[False] = False
    scientific_conclusion: Literal[False] = False

    @model_validator(mode="after")
    def validate_manifest(self) -> FlatbandCalibrationBandPreauditManifestV1:
        ranks = tuple(item.source_rank for item in self.records)
        ids = tuple(item.c2db_record_id for item in self.records)
        if ranks != tuple(sorted(set(ranks))) or len(ids) != len(set(ids)):
            raise ValueError("band pre-audit records must be rank-sorted and unique")
        expected_counts = {
            ObservedBandClass.FB100: self.fb100_count,
            ObservedBandClass.NB300: self.nb300_count,
            ObservedBandClass.BORDER500: self.border500_count,
            ObservedBandClass.OUT_OF_SCOPE: self.out_of_scope_count,
        }
        for band_class, observed in expected_counts.items():
            if observed != sum(
                item.algorithmic_bandwidth_stratum is band_class
                for item in self.records
            ):
                raise ValueError("band pre-audit class count does not replay")
        expected_sha256 = canonical_sha256(
            self.model_dump(mode="python", exclude={"manifest_sha256"})
        )
        if self.manifest_sha256 != expected_sha256:
            raise ValueError("band pre-audit manifest SHA-256 does not match")
        return self


def build_calibration_band_preaudit(
    *,
    selection: FlatbandCalibrationSelectionManifestV1,
    intake_sources: tuple[
        tuple[FlatbandCalibrationIntakeManifestV1, Path], ...
    ],
    output_path: Path,
) -> FlatbandCalibrationBandPreauditManifestV1:
    """Pre-audit bandwidth strata without assigning benchmark target labels."""

    selected = FlatbandCalibrationSelectionManifestV1.model_validate(
        selection.model_dump(mode="python", round_trip=True)
    )
    record_map: dict[str, tuple[str, int, Path, str]] = {}
    source_shas: set[str] = set()
    rank_by_id = {
        item.c2db_record_id: item.source_rank for item in selected.candidates
    }
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
        raise ValueError("band pre-audit intake roots differ from selection roots")
    if set(selected.selected_record_ids) - set(record_map):
        raise ValueError("selected calibration record lacks a frozen band page")

    records: list[FlatbandCalibrationBandPreauditRecordV1] = []
    for record_id in selected.selected_record_ids:
        manifest_sha, rank, path, expected_sha = record_map[record_id]
        if rank != rank_by_id[record_id]:
            raise ValueError("band pre-audit source rank drifts from selection")
        payload = path.read_bytes()
        if hashlib.sha256(payload).hexdigest() != expected_sha:
            raise ValueError("band pre-audit source page hash drifted")
        records.append(
            preaudit_c2db_band_page(
                payload,
                source_rank=rank,
                c2db_record_id=record_id,
                source_intake_manifest_sha256=manifest_sha,
                band_page_sha256=expected_sha,
            )
        )
    ordered = tuple(sorted(records, key=lambda item: item.source_rank))
    counts = {
        band_class: sum(
            item.algorithmic_bandwidth_stratum is band_class for item in ordered
        )
        for band_class in ObservedBandClass
    }
    values = {
        "selection_manifest_sha256": selected.manifest_sha256,
        "records": ordered,
        "fb100_count": counts[ObservedBandClass.FB100],
        "nb300_count": counts[ObservedBandClass.NB300],
        "border500_count": counts[ObservedBandClass.BORDER500],
        "out_of_scope_count": counts[ObservedBandClass.OUT_OF_SCOPE],
    }
    draft = FlatbandCalibrationBandPreauditManifestV1.model_construct(
        manifest_sha256="0" * 64, **values
    )
    result = FlatbandCalibrationBandPreauditManifestV1.model_validate(
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


def preaudit_c2db_band_page(
    page: bytes,
    *,
    source_rank: int,
    c2db_record_id: str,
    source_intake_manifest_sha256: str,
    band_page_sha256: str,
) -> FlatbandCalibrationBandPreauditRecordV1:
    """Extract one frozen PBE/no-SOC bandwidth pre-audit from a C2DB page."""

    text = page.decode("utf-8")
    marker = "Plotly.newPlot('bandstructure', graphs, {});"
    end = text.find(marker)
    assignment = text.rfind("var graphs = ", 0, end)
    if end < 0 or assignment < 0:
        raise ValueError("C2DB band page is missing its Plotly payload")
    graph, _ = json.JSONDecoder().raw_decode(
        text[assignment + len("var graphs = ") : end]
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
    x_raw, y_raw = traces[0].get("x"), traces[0].get("y")
    if not isinstance(x_raw, list) or not isinstance(y_raw, list):
        raise TypeError("C2DB PBE trace requires x/y arrays")
    x = np.asarray(x_raw, dtype=float)
    y = np.asarray(y_raw, dtype=float)
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
    bands = y.reshape((-1, period))
    distances = np.min(np.abs(bands), axis=1)
    within = np.flatnonzero(distances <= 1.0)
    if not len(within):
        raise ValueError("C2DB PBE trace has no band in the published 1 eV window")
    widths = np.ptp(bands[within], axis=1)
    target = min(
        (
            (float(widths[offset]), float(distances[index]), int(index))
            for offset, index in enumerate(within)
        ),
        key=lambda item: item,
    )
    width, distance, band_index = target
    layout = graph.get("layout")
    yaxis = layout.get("yaxis") if isinstance(layout, dict) else None
    title = yaxis.get("title") if isinstance(yaxis, dict) else None
    title_text = title.get("text") if isinstance(title, dict) else None
    if not isinstance(title_text, str):
        raise TypeError("C2DB band page lacks an explicit energy reference")
    if "<sub>F</sub>" in title_text:
        reference = "FERMI"
    elif "<sub>VBM</sub>" in title_text:
        reference = "VBM"
    else:
        raise ValueError("C2DB band page energy reference is unsupported")
    return FlatbandCalibrationBandPreauditRecordV1(
        source_rank=source_rank,
        c2db_record_id=c2db_record_id,
        source_intake_manifest_sha256=source_intake_manifest_sha256,
        band_page_sha256=band_page_sha256,
        energy_reference=reference,
        narrowest_band_index_in_window=band_index,
        narrowest_bandwidth_e_v=width,
        distance_to_published_reference_e_v=distance,
        algorithmic_bandwidth_stratum=observed_band_class(bandwidth_e_v=width),
        band_count=int(bands.shape[0]),
        k_point_count=period,
    )


__all__ = [
    "FlatbandCalibrationBandPreauditManifestV1",
    "FlatbandCalibrationBandPreauditRecordV1",
    "build_calibration_band_preaudit",
    "preaudit_c2db_band_page",
]
