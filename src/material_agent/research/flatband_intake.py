"""Private, calibration-only intake for real flat-band benchmark sources.

This module deliberately stops before formal case construction.  It freezes the
exact source bytes and their hashes, but it cannot assign a mechanism, create a
benchmark label, or authorize a Pilot run.  Those steps require the independent
expert and leakage-review chain defined by the formal V3 contracts.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from pathlib import Path
from typing import Annotated, Any, Literal, Protocol

from pydantic import Field, model_validator

from material_agent.inspiration.models import (
    Sha256,
    StrictModel,
    canonical_sha256,
)
from material_agent.research.flatband_contracts import SOURCE_CATALOG_V1_SHA256

_C2DB_UID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_MAX_SOURCE_BYTES = 16 * 1024 * 1024


class _Response(Protocol):
    content: bytes
    text: str
    url: str

    def raise_for_status(self) -> None: ...


class _Session(Protocol):
    def get(
        self, url: str, *, timeout: float, headers: dict[str, str]
    ) -> _Response: ...


class FlatbandCalibrationSeedV1(StrictModel):
    schema_version: Literal["flatband-calibration-seed-v1"] = (
        "flatband-calibration-seed-v1"
    )
    source_id: Literal["struct2flat"] = "struct2flat"
    source_record_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
    source_rank: Annotated[int, Field(ge=1)]
    predicted_flatness_score: float
    kagome_like_seed: bool
    calibration_only: Literal[True] = True
    scientific_conclusion: Literal[False] = False

    @model_validator(mode="after")
    def validate_score(self) -> FlatbandCalibrationSeedV1:
        if not math.isfinite(self.predicted_flatness_score):
            raise ValueError("predicted flatness score must be finite")
        return self


class FlatbandPrivateIntakeArtifactV1(StrictModel):
    schema_version: Literal["flatband-private-intake-artifact-v1"] = (
        "flatband-private-intake-artifact-v1"
    )
    artifact_role: Literal["C2DB_STRUCTURE_JSON", "C2DB_BAND_PAGE"]
    source_url: str = Field(pattern=r"^https://")
    local_relative_path: str = Field(
        pattern=r"^records/[A-Za-z0-9][A-Za-z0-9._-]*/[A-Za-z0-9._-]+$"
    )
    media_type: Literal["application/json", "text/html"]
    size_bytes: Annotated[int, Field(ge=1, le=_MAX_SOURCE_BYTES)]
    sha256: Sha256
    public_release_allowed: Literal[False] = False
    scientific_conclusion: Literal[False] = False


class FlatbandCalibrationIntakeRecordV1(StrictModel):
    schema_version: Literal["flatband-calibration-intake-record-v1"] = (
        "flatband-calibration-intake-record-v1"
    )
    seed: FlatbandCalibrationSeedV1
    c2db_record_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
    status: Literal["COMPLETE", "PARTIAL", "FAILED"]
    artifacts: Annotated[
        tuple[FlatbandPrivateIntakeArtifactV1, ...], Field(max_length=2)
    ] = ()
    errors: Annotated[tuple[str, ...], Field(max_length=8)] = ()
    formal_case_status: Literal[
        "NOT_CONSTRUCTED_PENDING_EXPERT_AND_LEAKAGE_REVIEW"
    ] = "NOT_CONSTRUCTED_PENDING_EXPERT_AND_LEAKAGE_REVIEW"
    calibration_only: Literal[True] = True
    public_release_allowed: Literal[False] = False
    scientific_conclusion: Literal[False] = False

    @model_validator(mode="after")
    def validate_record(self) -> FlatbandCalibrationIntakeRecordV1:
        if self.seed.source_record_id != self.c2db_record_id:
            raise ValueError("seed and C2DB record identity differ")
        roles = tuple(item.artifact_role for item in self.artifacts)
        if roles != tuple(sorted(set(roles))):
            raise ValueError("intake artifacts must be role-sorted and unique")
        if self.errors != tuple(sorted(set(self.errors))):
            raise ValueError("intake errors must be sorted and unique")
        expected = {2: "COMPLETE", 1: "PARTIAL", 0: "FAILED"}[len(self.artifacts)]
        if self.status != expected:
            raise ValueError("intake status differs from artifact coverage")
        if self.status == "COMPLETE" and self.errors:
            raise ValueError("complete intake cannot carry errors")
        if self.status != "COMPLETE" and not self.errors:
            raise ValueError("incomplete intake requires an error ledger")
        return self


class FlatbandCalibrationIntakeManifestV1(StrictModel):
    schema_version: Literal["flatband-calibration-intake-manifest-v1"] = (
        "flatband-calibration-intake-manifest-v1"
    )
    manifest_sha256: Sha256
    benchmark_scope: Literal["TRACK_A_CALIBRATION_ONLY"] = (
        "TRACK_A_CALIBRATION_ONLY"
    )
    source_catalog_sha256: Literal[SOURCE_CATALOG_V1_SHA256] = (
        SOURCE_CATALOG_V1_SHA256
    )
    seed_repository_url: Literal[
        "https://github.com/Xiangwen-Wang/Struct2Flat"
    ] = "https://github.com/Xiangwen-Wang/Struct2Flat"
    seed_repository_commit: Literal[
        "2ed5f320ee2428e02ce8f054ad1c2bd30ec5d1a4"
    ] = "2ed5f320ee2428e02ce8f054ad1c2bd30ec5d1a4"
    seed_file_sha256: Sha256
    c2db_source_version: Literal["data version 2024-05-01"] = (
        "data version 2024-05-01"
    )
    retrieved_at: str = Field(min_length=20, max_length=40)
    records: Annotated[
        tuple[FlatbandCalibrationIntakeRecordV1, ...],
        Field(min_length=1, max_length=12),
    ]
    calibration_only: Literal[True] = True
    pilot_execution_authorized: Literal[False] = False
    public_release_allowed: Literal[False] = False
    scientific_conclusion: Literal[False] = False

    @model_validator(mode="after")
    def validate_manifest(self) -> FlatbandCalibrationIntakeManifestV1:
        record_ids = tuple(item.c2db_record_id for item in self.records)
        if record_ids != tuple(sorted(set(record_ids))):
            raise ValueError("intake records must be C2DB-ID sorted and unique")
        expected = canonical_sha256(
            self.model_dump(mode="python", exclude={"manifest_sha256"})
        )
        if self.manifest_sha256 != expected:
            raise ValueError("intake manifest SHA-256 does not match content")
        return self


class FlatbandCalibrationSelectionCandidateV1(StrictModel):
    source_manifest_sha256: Sha256
    source_rank: Annotated[int, Field(ge=1)]
    c2db_record_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
    intake_status: Literal["COMPLETE", "PARTIAL", "FAILED"]


class FlatbandCalibrationSelectionManifestV1(StrictModel):
    schema_version: Literal["flatband-calibration-selection-manifest-v1"] = (
        "flatband-calibration-selection-manifest-v1"
    )
    manifest_sha256: Sha256
    source_manifest_sha256s: Annotated[
        tuple[Sha256, ...], Field(min_length=1, max_length=12)
    ]
    candidates: Annotated[
        tuple[FlatbandCalibrationSelectionCandidateV1, ...],
        Field(min_length=12, max_length=144),
    ]
    selection_rule: Literal[
        "LOWEST_STRUCT2FLAT_RANK_WITH_COMPLETE_STRUCTURE_AND_BAND_UNTIL_12"
    ] = "LOWEST_STRUCT2FLAT_RANK_WITH_COMPLETE_STRUCTURE_AND_BAND_UNTIL_12"
    selected_record_ids: Annotated[tuple[str, ...], Field(min_length=12, max_length=12)]
    partial_record_ids: tuple[str, ...]
    nonselected_record_ids: tuple[str, ...]
    calibration_only: Literal[True] = True
    pilot_execution_authorized: Literal[False] = False
    gold_label_status: Literal["NOT_ANNOTATED"] = "NOT_ANNOTATED"
    scientific_conclusion: Literal[False] = False

    @model_validator(mode="after")
    def validate_selection(self) -> FlatbandCalibrationSelectionManifestV1:
        if self.source_manifest_sha256s != tuple(
            sorted(set(self.source_manifest_sha256s))
        ):
            raise ValueError("selection source manifests must be sorted and unique")
        ranks = tuple(item.source_rank for item in self.candidates)
        ids = tuple(item.c2db_record_id for item in self.candidates)
        if ranks != tuple(sorted(set(ranks))) or len(ids) != len(set(ids)):
            raise ValueError("selection candidate union must be rank-sorted and unique")
        expected_selected = tuple(
            item.c2db_record_id
            for item in self.candidates
            if item.intake_status == "COMPLETE"
        )[:12]
        if self.selected_record_ids != expected_selected:
            raise ValueError("selected records do not replay the frozen selection rule")
        expected_partial = tuple(
            sorted(
                item.c2db_record_id
                for item in self.candidates
                if item.intake_status == "PARTIAL"
            )
        )
        if self.partial_record_ids != expected_partial:
            raise ValueError("partial record ledger does not replay")
        expected_nonselected = tuple(sorted(set(ids) - set(expected_selected)))
        if self.nonselected_record_ids != expected_nonselected:
            raise ValueError("nonselected record ledger does not replay")
        expected_sha256 = canonical_sha256(
            self.model_dump(mode="python", exclude={"manifest_sha256"})
        )
        if self.manifest_sha256 != expected_sha256:
            raise ValueError("selection manifest SHA-256 does not match content")
        return self


def read_struct2flat_calibration_seeds(
    path: Path, *, max_records: int, start_rank: int = 1
) -> tuple[FlatbandCalibrationSeedV1, ...]:
    """Read a bounded calibration-only window of the pinned Struct2Flat table."""

    if not 1 <= max_records <= 12:
        raise ValueError("calibration intake must contain between 1 and 12 records")
    if start_rank < 1:
        raise ValueError("calibration intake start rank must be positive")
    lines = path.read_text("utf-8").splitlines()
    if not lines or lines[0].strip() != "Key\tPredicted Flatness Score\tif_kag":
        raise ValueError("Struct2Flat seed header drifted")
    seeds: list[FlatbandCalibrationSeedV1] = []
    seen: set[str] = set()
    for rank, line in enumerate(lines[1:], start=1):
        if not line.strip():
            continue
        if rank < start_rank:
            continue
        fields = line.split("\t")
        if len(fields) != 3:
            raise ValueError(f"Struct2Flat seed row {rank} is malformed")
        record_id, score_text, kagome_text = fields
        if not _C2DB_UID.fullmatch(record_id):
            raise ValueError(f"Struct2Flat seed row {rank} has an unsafe C2DB ID")
        if record_id in seen:
            raise ValueError("Struct2Flat seed table repeats a C2DB ID")
        if kagome_text not in {"0", "1"}:
            raise ValueError(f"Struct2Flat seed row {rank} has invalid if_kag")
        seen.add(record_id)
        seeds.append(
            FlatbandCalibrationSeedV1(
                source_record_id=record_id,
                source_rank=rank,
                predicted_flatness_score=float(score_text),
                kagome_like_seed=kagome_text == "1",
            )
        )
        if len(seeds) == max_records:
            break
    if len(seeds) != max_records:
        raise ValueError("Struct2Flat seed table underfills the requested intake")
    return tuple(seeds)


def fetch_c2db_calibration_intake(
    *,
    seeds: tuple[FlatbandCalibrationSeedV1, ...],
    seed_file_sha256: str,
    output_root: Path,
    session: _Session,
    retrieved_at: str,
    timeout_seconds: float = 30.0,
) -> FlatbandCalibrationIntakeManifestV1:
    """Fetch exact C2DB bytes and emit a non-executable private intake manifest."""

    if not seeds or len(seeds) > 12:
        raise ValueError("calibration intake requires between 1 and 12 seeds")
    output_root.mkdir(parents=True, exist_ok=True)
    output_root.chmod(0o700)
    records = tuple(
        sorted(
            (
                _fetch_record(
                    seed=seed,
                    output_root=output_root,
                    session=session,
                    timeout_seconds=timeout_seconds,
                )
                for seed in seeds
            ),
            key=lambda item: item.c2db_record_id,
        )
    )
    values: dict[str, Any] = {
        "seed_file_sha256": seed_file_sha256,
        "retrieved_at": retrieved_at,
        "records": records,
    }
    draft = FlatbandCalibrationIntakeManifestV1.model_construct(
        manifest_sha256="0" * 64, **values
    )
    manifest = FlatbandCalibrationIntakeManifestV1.model_validate(
        {
            **values,
            "manifest_sha256": canonical_sha256(
                draft.model_dump(mode="python", exclude={"manifest_sha256"})
            ),
        }
    )
    manifest_path = output_root / "manifest.json"
    manifest_path.write_text(
        json.dumps(
            manifest.model_dump(mode="json"),
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n",
        "utf-8",
    )
    manifest_path.chmod(0o600)
    return manifest


def select_flatband_calibration_records(
    *,
    intake_manifests: tuple[FlatbandCalibrationIntakeManifestV1, ...],
    output_path: Path,
) -> FlatbandCalibrationSelectionManifestV1:
    """Select exactly 12 complete records by a source-availability-only rule."""

    if not intake_manifests:
        raise ValueError("calibration selection requires at least one intake manifest")
    manifests = tuple(
        FlatbandCalibrationIntakeManifestV1.model_validate(
            item.model_dump(mode="python", round_trip=True)
        )
        for item in intake_manifests
    )
    candidates = tuple(
        sorted(
            (
                FlatbandCalibrationSelectionCandidateV1(
                    source_manifest_sha256=manifest.manifest_sha256,
                    source_rank=record.seed.source_rank,
                    c2db_record_id=record.c2db_record_id,
                    intake_status=record.status,
                )
                for manifest in manifests
                for record in manifest.records
            ),
            key=lambda item: item.source_rank,
        )
    )
    complete_ids = tuple(
        item.c2db_record_id
        for item in candidates
        if item.intake_status == "COMPLETE"
    )
    if len(complete_ids) < 12:
        raise ValueError("candidate union contains fewer than 12 complete records")
    selected = complete_ids[:12]
    partial = tuple(
        sorted(
            item.c2db_record_id
            for item in candidates
            if item.intake_status == "PARTIAL"
        )
    )
    nonselected = tuple(
        sorted({item.c2db_record_id for item in candidates} - set(selected))
    )
    values: dict[str, Any] = {
        "source_manifest_sha256s": tuple(
            sorted({item.manifest_sha256 for item in manifests})
        ),
        "candidates": candidates,
        "selected_record_ids": selected,
        "partial_record_ids": partial,
        "nonselected_record_ids": nonselected,
    }
    draft = FlatbandCalibrationSelectionManifestV1.model_construct(
        manifest_sha256="0" * 64, **values
    )
    selection = FlatbandCalibrationSelectionManifestV1.model_validate(
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
            selection.model_dump(mode="json"),
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n",
        "utf-8",
    )
    output_path.chmod(0o600)
    return selection


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _fetch_record(
    *,
    seed: FlatbandCalibrationSeedV1,
    output_root: Path,
    session: _Session,
    timeout_seconds: float,
) -> FlatbandCalibrationIntakeRecordV1:
    record_id = seed.source_record_id
    record_root = output_root / "records" / record_id
    record_root.mkdir(parents=True, exist_ok=True)
    (output_root / "records").chmod(0o700)
    record_root.chmod(0o700)
    artifacts: list[FlatbandPrivateIntakeArtifactV1] = []
    errors: list[str] = []
    headers = {
        "Accept": "application/json,text/html;q=0.9,*/*;q=0.1",
        "User-Agent": "materials-screening-agent-flatband-benchmark/1.0",
    }
    sources = (
        (
            "C2DB_BAND_PAGE",
            f"https://c2db.fysik.dtu.dk/material/{record_id}",
            "band-page.html",
            "text/html",
            _validate_band_page,
        ),
        (
            "C2DB_STRUCTURE_JSON",
            f"https://c2db.fysik.dtu.dk/material/{record_id}/download/json",
            "structure.json",
            "application/json",
            _validate_structure_json,
        ),
    )
    for role, url, filename, media_type, validator in sources:
        try:
            response = session.get(
                url,
                timeout=timeout_seconds,
                headers=headers,
            )
            response.raise_for_status()
            payload = bytes(response.content)
            if not 1 <= len(payload) <= _MAX_SOURCE_BYTES:
                raise ValueError("response violates the frozen source byte bound")
            validator(payload)
            relative = f"records/{record_id}/{filename}"
            artifact_path = output_root / relative
            artifact_path.write_bytes(payload)
            artifact_path.chmod(0o600)
            artifacts.append(
                FlatbandPrivateIntakeArtifactV1(
                    artifact_role=role,
                    source_url=url,
                    local_relative_path=relative,
                    media_type=media_type,
                    size_bytes=len(payload),
                    sha256=hashlib.sha256(payload).hexdigest(),
                )
            )
        except Exception as exc:  # noqa: BLE001 - bounded source error ledger
            errors.append(f"{role}:{type(exc).__name__}:{exc}")
    ordered_artifacts = tuple(sorted(artifacts, key=lambda item: item.artifact_role))
    ordered_errors = tuple(sorted(set(errors)))
    return FlatbandCalibrationIntakeRecordV1(
        seed=seed,
        c2db_record_id=record_id,
        status={2: "COMPLETE", 1: "PARTIAL", 0: "FAILED"}[
            len(ordered_artifacts)
        ],
        artifacts=ordered_artifacts,
        errors=ordered_errors,
    )


def _validate_structure_json(payload: bytes) -> None:
    parsed = json.loads(payload)
    if not isinstance(parsed, dict) or not isinstance(parsed.get("1"), dict):
        raise TypeError("C2DB structure JSON is missing atoms record '1'")


def _validate_band_page(payload: bytes) -> None:
    text = payload.decode("utf-8")
    marker = "Plotly.newPlot('bandstructure', graphs, {});"
    plot_end = text.find(marker)
    assignment = text.rfind("var graphs = ", 0, plot_end)
    if plot_end < 0 or assignment < 0:
        raise ValueError("C2DB page is missing the PBE band Plotly payload")
    graph, _ = json.JSONDecoder().raw_decode(
        text[assignment + len("var graphs = ") : plot_end]
    )
    if not isinstance(graph, dict) or not isinstance(graph.get("data"), list):
        raise TypeError("C2DB band Plotly payload is malformed")


__all__ = [
    "FlatbandCalibrationIntakeManifestV1",
    "FlatbandCalibrationIntakeRecordV1",
    "FlatbandCalibrationSelectionCandidateV1",
    "FlatbandCalibrationSelectionManifestV1",
    "FlatbandCalibrationSeedV1",
    "FlatbandPrivateIntakeArtifactV1",
    "fetch_c2db_calibration_intake",
    "read_struct2flat_calibration_seeds",
    "select_flatband_calibration_records",
    "sha256_file",
]
