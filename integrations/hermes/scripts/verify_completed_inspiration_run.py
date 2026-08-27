#!/usr/bin/env python3
"""Read-only release verifier for one completed Hermes inspiration run.

The verifier deliberately does not construct a Gateway service or repository:
those production classes own writable SQLite connections.  Instead it rejects
uncheckpointed WAL files, opens both databases as immutable/query-only, and
recomputes the terminal/result/grant and Artifact closure from durable bytes.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
import stat
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path, PurePosixPath
from typing import Any, TypeVar

from pydantic import BaseModel, ValidationError

from material_agent.gateway.authorization import (
    ACTION_GRANT_SCHEMA_VERSION,
    is_valid_confirmation_reference,
)
from material_agent.gateway.companion import (
    PreparedInspirationRun,
    prepared_execution_manifest_sha256,
    requirement_freeze_prompt,
)
from material_agent.gateway.mcp_server import GatewayServerSettings
from material_agent.gateway.models import (
    ApprovalInteractionV1,
    ApproveActionV1,
    ArtifactClosureV1,
    ArtifactReferenceV1,
    CostLedgerProjectionV1,
    GatewayResultRecordV1,
    GatewayRunRecordV1,
    InspirationBundleSummaryV1,
    PartialStateV1,
    SucceededStateV1,
    artifact_closure_sha256,
    gateway_result_sha256,
    inspiration_report_uri,
    terminal_reference,
)
from material_agent.gateway.models import (
    canonical_json_bytes as gateway_canonical_json_bytes,
)
from material_agent.gateway.persistence import GATEWAY_DATABASE_SCHEMA_VERSION
from material_agent.inspiration.bridge import build_search_supported_bridges
from material_agent.inspiration.component_identity import execution_identity_snapshots
from material_agent.inspiration.engine import PymatgenTransformationEngine
from material_agent.inspiration.evidence import build_evidence_cards
from material_agent.inspiration.extractors import (
    ExtractionDecision,
    ExtractionLimits,
    ExtractionTier,
    extract_crossref_metadata,
)
from material_agent.inspiration.feedback import (
    FEEDBACK_COMPILER_SNAPSHOT,
    FeedbackInputArtifactV1,
    FeedbackVectorAllocationV1,
    TagFeedbackReviewV1,
    compile_tag_feedback_review,
)
from material_agent.inspiration.fetch import DisabledDocumentFetcher, FetchAttemptRecord
from material_agent.inspiration.identity import merge_run_internal_candidates
from material_agent.inspiration.models import (
    ArtifactPointerV1,
    BridgePacketV1,
    CostLedgerV1,
    EvidenceCardV1,
    InspirationBundleV1,
    InspirationInputV1,
    InspirationOutcome,
    InspirationStageResultV1,
    PassageV1,
    PassageVectorV1,
    SearchHitV1,
    SearchQueryKind,
    SearchQueryV1,
    TagGraphV1,
    TransformationPlanV1,
    TransformationStatus,
    canonical_json_bytes,
    deterministic_id,
)
from material_agent.inspiration.parent_catalog import load_flat_band_parent_catalog_v1
from material_agent.inspiration.passages import (
    select_passage_drafts,
    selection_config_from_policy,
)
from material_agent.inspiration.policy import InspirationPolicyV1, SearchExecutionMode
from material_agent.inspiration.reporting import render_inspiration_report
from material_agent.inspiration.retrieval_quality import (
    CuratedQueryCandidatePoolV1,
    MetadataQualityAuditV1,
    QueryAllocationAuditV1,
    audit_metadata_hits,
)
from material_agent.inspiration.runner import (
    MAX_SEARCH_RESPONSE_BYTES,
    STRUCTURE_MEDIA_TYPE,
    InspirationRunner,
    ParentStructureInput,
    TransformationContext,
    _bounded_warnings,
    _select_document_processing_hit,
    _unique_pointers,
)
from material_agent.inspiration.search import (
    _CROSSREF_POLITE_MIN_INTERVAL_SECONDS,
    _CROSSREF_PUBLIC_MIN_INTERVAL_SECONDS,
    _TRANSIENT_HTTP_STATUSES,
    CrossrefPublicAdapter,
    SearchAttemptRecord,
    group_document_hits,
    parse_crossref_page,
)
from material_agent.inspiration.selection import (
    MechanismQuotaStatus,
    select_diverse_candidates_with_audit,
)
from material_agent.inspiration.tag_graph import plan_tag_queries
from material_agent.inspiration.vectorizer import (
    SIGNED_HASHING_SNAPSHOT,
    VECTOR_ARTIFACT_MEDIA_TYPE,
    PassageVectorizationRequest,
    vectorize_selected_passages,
)
from material_agent.integration.hermes_service import (
    _PUBLIC_CROSSREF_MAX_RETRIES,
    _PUBLIC_CROSSREF_MAX_RETRY_DELAY_SECONDS,
    _PUBLIC_CROSSREF_MAX_TOTAL_WAIT_SECONDS,
    _PUBLIC_CROSSREF_TIMEOUT_SECONDS,
    GATEWAY_STATE_DATABASE_NAME,
    OPERATOR_APPROVAL_DATABASE_NAME,
    HermesFixtureProjector,
    resolve_hermes_project_root,
    trusted_state_database_path,
)

_SHA256_LENGTH = 64
_MAX_REPORT_BYTES = 1_000_000
_T = TypeVar("_T")


class CompletedRunVerificationError(RuntimeError):
    """A durable completed-run invariant did not hold."""


@dataclass(frozen=True, slots=True)
class VerificationExpectations:
    request_sha256: str
    execution_manifest_sha256: str
    interaction_id: str
    interaction_sha256: str
    action_sha256: str
    confirmation_reference: str
    expected_revision: int = 2
    require_nonempty_result: bool = True

    def __post_init__(self) -> None:
        for name in (
            "request_sha256",
            "execution_manifest_sha256",
            "interaction_sha256",
            "action_sha256",
        ):
            value = getattr(self, name)
            if (
                not isinstance(value, str)
                or len(value) != _SHA256_LENGTH
                or any(character not in "0123456789abcdef" for character in value)
            ):
                raise ValueError(f"{name} must be a lowercase SHA-256")
        if not self.interaction_id:
            raise ValueError("interaction_id must be non-empty")
        if not is_valid_confirmation_reference(self.confirmation_reference):
            raise ValueError("confirmation_reference is invalid")
        if self.expected_revision < 1:
            raise ValueError("expected_revision must be positive")


@dataclass(frozen=True, slots=True)
class _FileSnapshot:
    mode: int
    size: int
    mtime_ns: int
    inode: int
    link_count: int
    sha256: str


def _fail(message: str) -> None:
    raise CompletedRunVerificationError(message)


def _require(condition: bool, message: str) -> None:
    if not condition:
        _fail(message)


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _looks_like_pdf_payload(payload: bytes) -> bool:
    """Recognize a PDF signature behind common BOM/whitespace padding."""

    prefix = payload[:1_024].lstrip(b"\x00\x09\x0a\x0b\x0c\x0d\x20\xef\xbb\xbf\xff\xfe")
    return prefix[:5].lower() == b"%pdf-"


def _reject_embedded_body_fields(value: Any, *, path: str = "$") -> None:
    """Reject provider drift that embeds article/PDF bodies in metadata JSON."""

    forbidden = {
        "articlebody",
        "articlecontent",
        "body",
        "content",
        "fullbody",
        "fulltext",
        "pdf",
        "pdfbase64",
        "pdfbytes",
        "pdfcontent",
    }
    if isinstance(value, dict):
        for key, item in value.items():
            normalized = "".join(
                character for character in key.casefold() if character.isalnum()
            )
            _require(
                normalized not in forbidden,
                f"Crossref metadata embeds forbidden body field {path}.{key}",
            )
            _reject_embedded_body_fields(item, path=f"{path}.{key}")
    elif isinstance(value, list):
        for index, item in enumerate(value):
            _reject_embedded_body_fields(item, path=f"{path}[{index}]")


def _crossref_object(
    value: Any,
    *,
    path: str,
    allowed_fields: set[str],
) -> dict[str, Any]:
    _require(type(value) is dict, f"Crossref metadata {path} must be an object")
    unknown = set(value) - allowed_fields
    _require(
        not unknown,
        f"Crossref metadata {path} contains fields outside the selected metadata "
        f"schema: {sorted(unknown)}",
    )
    return value


def _crossref_text(value: Any, *, path: str, max_length: int) -> str:
    _require(type(value) is str, f"Crossref metadata {path} must be a string")
    _require(
        0 < len(value) <= max_length,
        f"Crossref metadata {path} exceeds its bounded string length",
    )
    normalized = value.lstrip("\x00\t\n\r \ufeff")
    folded = normalized.casefold()
    _require(
        all(
            marker not in folded
            for marker in ("%pdf-", "data:application/pdf", "jvberi0")
        ),
        f"Crossref metadata {path} embeds a PDF payload",
    )
    _require(
        all(
            marker not in folded
            for marker in ("<!doctype", "<article", "<body", "<html", "<iframe")
        ),
        f"Crossref metadata {path} embeds document-level markup",
    )
    return value


def _crossref_string_list(
    value: Any,
    *,
    path: str,
    max_items: int,
    max_item_length: int,
    require_nonempty: bool = False,
) -> None:
    _require(type(value) is list, f"Crossref metadata {path} must be an array")
    _require(
        (bool(value) or not require_nonempty) and len(value) <= max_items,
        f"Crossref metadata {path} exceeds its bounded item count",
    )
    for index, item in enumerate(value):
        _crossref_text(
            item,
            path=f"{path}[{index}]",
            max_length=max_item_length,
        )


def _validate_crossref_author(value: Any, *, path: str) -> None:
    author = _crossref_object(
        value,
        path=path,
        allowed_fields={
            "ORCID",
            "affiliation",
            "authenticated-orcid",
            "family",
            "given",
            "name",
            "role",
            "sequence",
            "suffix",
        },
    )
    for key in ("ORCID", "family", "given", "name", "sequence", "suffix"):
        if key in author:
            _crossref_text(author[key], path=f"{path}.{key}", max_length=512)
    if "authenticated-orcid" in author:
        _require(
            type(author["authenticated-orcid"]) is bool,
            f"Crossref metadata {path}.authenticated-orcid must be boolean",
        )
    if "affiliation" in author:
        affiliations = author["affiliation"]
        _require(
            type(affiliations) is list and len(affiliations) <= 64,
            f"Crossref metadata {path}.affiliation is not a bounded array",
        )
        for index, raw_affiliation in enumerate(affiliations):
            affiliation_path = f"{path}.affiliation[{index}]"
            affiliation = _crossref_object(
                raw_affiliation,
                path=affiliation_path,
                allowed_fields={"id", "name"},
            )
            _crossref_text(
                affiliation.get("name"),
                path=f"{affiliation_path}.name",
                max_length=1_024,
            )
            if "id" in affiliation:
                identifiers = affiliation["id"]
                _require(
                    type(identifiers) is list and len(identifiers) <= 16,
                    f"Crossref metadata {affiliation_path}.id is not a bounded array",
                )
                for identifier_index, raw_identifier in enumerate(identifiers):
                    identifier_path = f"{affiliation_path}.id[{identifier_index}]"
                    identifier = _crossref_object(
                        raw_identifier,
                        path=identifier_path,
                        allowed_fields={"asserted-by", "id", "id-type"},
                    )
                    for key in ("asserted-by", "id", "id-type"):
                        _crossref_text(
                            identifier.get(key),
                            path=f"{identifier_path}.{key}",
                            max_length=512,
                        )
    if "role" in author:
        roles = author["role"]
        _require(
            type(roles) is list and len(roles) <= 16,
            f"Crossref metadata {path}.role is not a bounded array",
        )
        for index, raw_role in enumerate(roles):
            role_path = f"{path}.role[{index}]"
            role = _crossref_object(
                raw_role,
                path=role_path,
                allowed_fields={"role", "vocabulary"},
            )
            for key in ("role", "vocabulary"):
                _crossref_text(
                    role.get(key),
                    path=f"{role_path}.{key}",
                    max_length=128,
                )


def _validate_crossref_published(value: Any, *, path: str) -> None:
    published = _crossref_object(
        value,
        path=path,
        allowed_fields={"date-parts", "date-time", "timestamp"},
    )
    if "date-time" in published:
        _crossref_text(published["date-time"], path=f"{path}.date-time", max_length=128)
    if "timestamp" in published:
        timestamp = published["timestamp"]
        _require(
            type(timestamp) is int and timestamp >= 0,
            f"Crossref metadata {path}.timestamp must be a non-negative integer",
        )
    if "date-parts" in published:
        date_parts = published["date-parts"]
        _require(
            type(date_parts) is list and 0 < len(date_parts) <= 4,
            f"Crossref metadata {path}.date-parts is not a bounded array",
        )
        for index, raw_part in enumerate(date_parts):
            _require(
                type(raw_part) is list and 0 < len(raw_part) <= 3,
                f"Crossref metadata {path}.date-parts[{index}] is invalid",
            )
            _require(
                all(type(item) is int and 0 <= item <= 9_999 for item in raw_part),
                f"Crossref metadata {path}.date-parts[{index}] has invalid values",
            )


def _validate_crossref_metadata_envelope(value: Any) -> None:
    """Validate the exact bounded metadata shape requested from Crossref."""

    root = _crossref_object(
        value,
        path="$",
        allowed_fields={"message", "message-type", "message-version", "status"},
    )
    _require(root.get("status") == "ok", "Crossref metadata status is not ok")
    for key in ("message-type", "message-version"):
        if key in root:
            _crossref_text(root[key], path=f"$.{key}", max_length=64)
    message = _crossref_object(
        root.get("message"),
        path="$.message",
        allowed_fields={
            "facets",
            "items",
            "items-per-page",
            "query",
            "total-results",
        },
    )
    _require(
        type(message.get("items-per-page")) is int and message["items-per-page"] == 1,
        "Crossref metadata $.message.items-per-page must equal the requested rows=1",
    )
    if "total-results" in message:
        _require(
            type(message["total-results"]) is int and message["total-results"] >= 0,
            "Crossref metadata $.message.total-results must be a non-negative integer",
        )
    if "facets" in message:
        _require(
            type(message["facets"]) is dict and not message["facets"],
            "Crossref metadata $.message.facets must be empty for this request",
        )
    if "query" in message:
        query = _crossref_object(
            message["query"],
            path="$.message.query",
            allowed_fields={"search-terms", "start-index"},
        )
        if "start-index" in query:
            _require(
                type(query["start-index"]) is int and query["start-index"] >= 0,
                "Crossref metadata $.message.query.start-index is invalid",
            )
        if query.get("search-terms") is not None:
            _crossref_text(
                query["search-terms"],
                path="$.message.query.search-terms",
                max_length=4_096,
            )
    items = message.get("items")
    _require(
        type(items) is list and len(items) == 1,
        "Crossref metadata $.message.items must contain exactly one requested item",
    )
    selected_fields = {
        "DOI",
        "URL",
        "abstract",
        "author",
        "published",
        "subject",
        "title",
    }
    for index, raw_item in enumerate(items):
        path = f"$.message.items[{index}]"
        item = _crossref_object(
            raw_item,
            path=path,
            allowed_fields=selected_fields,
        )
        _crossref_text(item.get("DOI"), path=f"{path}.DOI", max_length=2_048)
        _crossref_string_list(
            item.get("title"),
            path=f"{path}.title",
            max_items=16,
            max_item_length=4_096,
            require_nonempty=True,
        )
        if "URL" in item:
            _crossref_text(item["URL"], path=f"{path}.URL", max_length=4_096)
        if "abstract" in item:
            _crossref_text(item["abstract"], path=f"{path}.abstract", max_length=20_000)
        if "subject" in item:
            _crossref_string_list(
                item["subject"],
                path=f"{path}.subject",
                max_items=256,
                max_item_length=512,
            )
        if "author" in item:
            authors = item["author"]
            _require(
                type(authors) is list and len(authors) <= 256,
                f"Crossref metadata {path}.author is not a bounded array",
            )
            for author_index, author in enumerate(authors):
                _validate_crossref_author(
                    author,
                    path=f"{path}.author[{author_index}]",
                )
        if "published" in item:
            _validate_crossref_published(item["published"], path=f"{path}.published")


def _current_crossref_components() -> dict[object, float]:
    """Map both production pool snapshots to their pacing ceilings."""

    common = {
        "max_results": 1,
        "max_retries": _PUBLIC_CROSSREF_MAX_RETRIES,
        "max_retry_delay_seconds": _PUBLIC_CROSSREF_MAX_RETRY_DELAY_SECONDS,
        "max_total_wait_seconds": _PUBLIC_CROSSREF_MAX_TOTAL_WAIT_SECONDS,
        "retry_backoff_seconds": 1.0,
        "timeout_seconds": _PUBLIC_CROSSREF_TIMEOUT_SECONDS,
    }
    public_component = CrossrefPublicAdapter(
        contact_email=None,
        sleeper=lambda _seconds: None,
        **common,
    ).component
    polite_component = CrossrefPublicAdapter(
        contact_email="verifier@example.invalid",
        sleeper=lambda _seconds: None,
        **common,
    ).component
    return {
        public_component: _CROSSREF_PUBLIC_MIN_INTERVAL_SECONDS,
        polite_component: _CROSSREF_POLITE_MIN_INTERVAL_SECONDS,
    }


def _reject_duplicate_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            _fail(f"JSON object repeats key {key!r}")
        result[key] = value
    return result


def _decode_json(payload: bytes, *, label: str) -> Any:
    try:
        text = payload.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise CompletedRunVerificationError(f"{label} is not UTF-8") from exc
    try:
        return json.loads(text, object_pairs_hook=_reject_duplicate_pairs)
    except (json.JSONDecodeError, CompletedRunVerificationError) as exc:
        if isinstance(exc, CompletedRunVerificationError):
            raise
        raise CompletedRunVerificationError(f"{label} is not strict JSON") from exc


def _decode_jsonl(payload: bytes, *, label: str) -> tuple[tuple[Any, bytes], ...]:
    if not payload:
        return ()
    _require(payload.endswith(b"\n"), f"{label} must end with one newline")
    rows: list[tuple[Any, bytes]] = []
    for index, line in enumerate(payload.splitlines(), start=1):
        _require(bool(line.strip()), f"{label} contains a blank row")
        rows.append((_decode_json(line, label=f"{label}:{index}"), line))
    return tuple(rows)


def _model_from_bytes(model: type[_T], payload: bytes, *, label: str) -> _T:
    _decode_json(payload, label=label)
    try:
        return model.model_validate_json(payload)  # type: ignore[attr-defined,no-any-return]
    except (TypeError, ValueError, ValidationError) as exc:
        raise CompletedRunVerificationError(
            f"{label} violates its strict schema"
        ) from exc


def _models_from_jsonl(
    model: type[_T],
    payload: bytes,
    *,
    label: str,
    identity: Callable[[_T], Any],
) -> tuple[_T, ...]:
    values: list[_T] = []
    identities: set[Any] = set()
    for raw, line in _decode_jsonl(payload, label=label):
        try:
            value = model.model_validate_json(line)  # type: ignore[attr-defined]
        except (TypeError, ValueError, ValidationError) as exc:
            raise CompletedRunVerificationError(
                f"{label} contains a schema-invalid row"
            ) from exc
        row_identity = identity(value)
        _require(
            row_identity not in identities, f"{label} repeats identity {row_identity!r}"
        )
        identities.add(row_identity)
        _require(
            canonical_json_bytes(value) == line,
            f"{label} row is not canonical JSON",
        )
        values.append(value)
    return tuple(values)


def _dataclasses_from_jsonl(
    constructor: Callable[..., _T],
    payload: bytes,
    *,
    label: str,
    identity: Callable[[_T], Any],
    to_dict: Callable[[_T], dict[str, object]],
) -> tuple[_T, ...]:
    values: list[_T] = []
    identities: set[Any] = set()
    for raw, line in _decode_jsonl(payload, label=label):
        _require(isinstance(raw, dict), f"{label} row must be an object")
        try:
            value = constructor(**raw)
        except (TypeError, ValueError) as exc:
            raise CompletedRunVerificationError(
                f"{label} contains an invalid audit row"
            ) from exc
        row_identity = identity(value)
        _require(
            row_identity not in identities, f"{label} repeats identity {row_identity!r}"
        )
        identities.add(row_identity)
        expected = json.dumps(
            to_dict(value),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        _require(expected == line, f"{label} row is not canonical JSON")
        values.append(value)
    return tuple(values)


def _path_chain(root: Path, target: Path) -> tuple[Path, ...]:
    try:
        relative = target.relative_to(root)
    except ValueError as exc:
        raise CompletedRunVerificationError(
            "path escapes the trusted project root"
        ) from exc
    chain = [root]
    current = root
    for part in relative.parts:
        current = current / part
        chain.append(current)
    return tuple(chain)


def _read_regular_file(root: Path, path: Path, *, label: str) -> bytes:
    for component in _path_chain(root, path):
        try:
            metadata = component.lstat()
        except FileNotFoundError as exc:
            raise CompletedRunVerificationError(f"{label} is missing") from exc
        _require(not stat.S_ISLNK(metadata.st_mode), f"{label} traverses a symlink")
    before = path.lstat()
    _require(stat.S_ISREG(before.st_mode), f"{label} is not a regular file")
    _require(before.st_nlink == 1, f"{label} is a hard-linked file")
    payload = path.read_bytes()
    after = path.lstat()
    _require(
        (
            before.st_mode,
            before.st_size,
            before.st_mtime_ns,
            before.st_ino,
            before.st_nlink,
        )
        == (
            after.st_mode,
            after.st_size,
            after.st_mtime_ns,
            after.st_ino,
            after.st_nlink,
        ),
        f"{label} changed while it was read",
    )
    _require(len(payload) == before.st_size, f"{label} size changed while it was read")
    return payload


def _hash_regular_file(
    root: Path,
    path: Path,
    *,
    label: str,
) -> tuple[os.stat_result, str]:
    """Hash one regular file without loading the workspace entry into memory."""

    for component in _path_chain(root, path):
        try:
            metadata = component.lstat()
        except FileNotFoundError as exc:
            raise CompletedRunVerificationError(f"{label} is missing") from exc
        _require(not stat.S_ISLNK(metadata.st_mode), f"{label} traverses a symlink")
    before = path.lstat()
    _require(stat.S_ISREG(before.st_mode), f"{label} is not a regular file")
    _require(before.st_nlink == 1, f"{label} is a hard-linked file")
    digest = hashlib.sha256()
    bytes_read = 0
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            bytes_read += len(chunk)
            digest.update(chunk)
    after = path.lstat()
    _require(
        (
            before.st_mode,
            before.st_size,
            before.st_mtime_ns,
            before.st_ino,
            before.st_nlink,
        )
        == (
            after.st_mode,
            after.st_size,
            after.st_mtime_ns,
            after.st_ino,
            after.st_nlink,
        ),
        f"{label} changed while it was hashed",
    )
    _require(bytes_read == before.st_size, f"{label} size changed while it was hashed")
    return after, digest.hexdigest()


def _snapshot_tree(root: Path) -> dict[str, _FileSnapshot]:
    root_metadata = root.lstat()
    _require(not stat.S_ISLNK(root_metadata.st_mode), "project root is a symlink")
    snapshot: dict[str, _FileSnapshot] = {
        ".": _FileSnapshot(
            mode=root_metadata.st_mode,
            size=root_metadata.st_size,
            mtime_ns=root_metadata.st_mtime_ns,
            inode=root_metadata.st_ino,
            link_count=root_metadata.st_nlink,
            sha256="",
        )
    }
    for directory, directory_names, file_names in os.walk(root, followlinks=False):
        directory_path = Path(directory)
        for name in tuple(directory_names):
            path = directory_path / name
            metadata = path.lstat()
            _require(
                not stat.S_ISLNK(metadata.st_mode),
                f"workspace directory {path} is a symlink",
            )
            _require(
                stat.S_ISDIR(metadata.st_mode),
                f"workspace entry {path} is not a directory",
            )
            snapshot[path.relative_to(root).as_posix() + "/"] = _FileSnapshot(
                mode=metadata.st_mode,
                size=metadata.st_size,
                mtime_ns=metadata.st_mtime_ns,
                inode=metadata.st_ino,
                link_count=metadata.st_nlink,
                sha256="",
            )
        for name in file_names:
            path = directory_path / name
            metadata, payload_sha256 = _hash_regular_file(
                root,
                path,
                label=f"workspace file {path}",
            )
            relative = path.relative_to(root).as_posix()
            snapshot[relative] = _FileSnapshot(
                mode=metadata.st_mode,
                size=metadata.st_size,
                mtime_ns=metadata.st_mtime_ns,
                inode=metadata.st_ino,
                link_count=metadata.st_nlink,
                sha256=payload_sha256,
            )
    return snapshot


def _immutable_connection(root: Path, database_path: Path) -> sqlite3.Connection:
    wal_path = database_path.with_name(database_path.name + "-wal")
    if wal_path.exists():
        wal_payload = _read_regular_file(root, wal_path, label=f"WAL {wal_path.name}")
        _require(not wal_payload, f"{database_path.name} has an uncheckpointed WAL")
    _read_regular_file(root, database_path, label=f"database {database_path.name}")
    uri = database_path.resolve().as_uri() + "?mode=ro&immutable=1"
    connection = sqlite3.connect(uri, uri=True)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only = ON")
    integrity = tuple(row[0] for row in connection.execute("PRAGMA integrity_check"))
    _require(integrity == ("ok",), f"{database_path.name} failed integrity_check")
    foreign_keys = tuple(connection.execute("PRAGMA foreign_key_check"))
    _require(not foreign_keys, f"{database_path.name} failed foreign_key_check")
    return connection


def _pointer_path(project_root: Path, pointer: ArtifactPointerV1) -> Path:
    relative_text = pointer.uri.removeprefix("artifact://")
    relative = PurePosixPath(relative_text)
    _require(
        bool(relative.parts)
        and not relative.is_absolute()
        and ".." not in relative.parts,
        f"unsafe Artifact URI {pointer.uri}",
    )
    return project_root.joinpath(*relative.parts)


def _read_pointer(
    project_root: Path,
    pointer: ArtifactPointerV1,
    *,
    pointer_index: dict[str, ArtifactPointerV1],
) -> bytes:
    previous = pointer_index.get(pointer.uri)
    if previous is not None:
        _require(
            previous == pointer, f"Artifact URI {pointer.uri} has conflicting pointers"
        )
    else:
        pointer_index[pointer.uri] = pointer
    payload = _read_regular_file(
        project_root,
        _pointer_path(project_root, pointer),
        label=pointer.uri,
    )
    _require(
        len(payload) == pointer.size_bytes, f"Artifact size mismatch for {pointer.uri}"
    )
    _require(
        _sha256(payload) == pointer.sha256, f"Artifact SHA mismatch for {pointer.uri}"
    )
    return payload


def _database_state(
    project_root: Path,
    run_id: str,
    expectations: VerificationExpectations,
) -> tuple[GatewayRunRecordV1, GatewayResultRecordV1, dict[str, Any]]:
    gateway_path = trusted_state_database_path(
        project_root, GATEWAY_STATE_DATABASE_NAME, must_exist=True
    )
    approval_path = trusted_state_database_path(
        project_root, OPERATOR_APPROVAL_DATABASE_NAME, must_exist=True
    )
    for path in (gateway_path, approval_path):
        _require(
            stat.S_IMODE(path.lstat().st_mode) == 0o600,
            f"{path.name} must be mode 0600",
        )

    gateway = _immutable_connection(project_root, gateway_path)
    try:
        schema = tuple(
            gateway.execute(
                "SELECT value FROM gateway_schema_metadata WHERE key='schema_version'"
            )
        )
        _require(
            len(schema) == 1 and schema[0][0] == str(GATEWAY_DATABASE_SCHEMA_VERSION),
            "Gateway database schema version drifted",
        )
        _require(
            gateway.execute("SELECT COUNT(*) FROM gateway_runs").fetchone()[0] == 1,
            "workspace must contain exactly one Gateway run",
        )
        row = gateway.execute(
            "SELECT run_id, submission_id, request_sha256, revision, record_json "
            "FROM gateway_runs WHERE run_id=?",
            (run_id,),
        ).fetchone()
        _require(row is not None, "expected Gateway run is absent")
        record_payload = row["record_json"].encode("utf-8")
        _decode_json(record_payload, label="gateway run record")
        try:
            record = GatewayRunRecordV1.model_validate_json(record_payload)
        except ValidationError as exc:
            raise CompletedRunVerificationError(
                "Gateway run record violates its schema"
            ) from exc
        _require(
            gateway_canonical_json_bytes(record) == record_payload,
            "Gateway run record is not canonical JSON",
        )
        _require(
            row["run_id"] == record.run_id == run_id, "Gateway run ID columns disagree"
        )
        _require(
            row["submission_id"] == record.request.submission_id,
            "Gateway submission columns disagree",
        )
        _require(
            row["request_sha256"] == record.request_sha256,
            "Gateway request SHA columns disagree",
        )
        _require(
            row["revision"] == record.revision, "Gateway revision columns disagree"
        )
        _require(
            record.request_sha256 == expectations.request_sha256,
            "request SHA differs from the approved value",
        )
        _require(
            isinstance(record.state, (SucceededStateV1, PartialStateV1)),
            "Gateway run is not a result-bearing terminal state",
        )
        _require(
            record.revision == expectations.expected_revision,
            "Gateway revision differs from the expected terminal revision",
        )
        _require(
            gateway.execute("SELECT COUNT(*) FROM gateway_results").fetchone()[0] == 1,
            "workspace must contain exactly one Gateway result",
        )
        result_row = gateway.execute(
            "SELECT result_json FROM gateway_results WHERE run_id=?", (run_id,)
        ).fetchone()
        _require(result_row is not None, "terminal Gateway result is absent")
        result_payload = result_row["result_json"].encode("utf-8")
        _decode_json(result_payload, label="gateway result")
        try:
            result = GatewayResultRecordV1.model_validate_json(result_payload)
        except ValidationError as exc:
            raise CompletedRunVerificationError(
                "Gateway result violates its schema"
            ) from exc
        _require(
            gateway_canonical_json_bytes(result) == result_payload,
            "Gateway result is not canonical JSON",
        )
        result_sha256 = gateway_result_sha256(result)
        _require(result.run_id == run_id, "Gateway result references another run")
        _require(
            result.report_uri == inspiration_report_uri(run_id),
            "Gateway report URI is not canonical",
        )
        _require(
            terminal_reference(record.state)
            == (result.report_uri, result.authoritative_sha256, result_sha256),
            "Gateway terminal tuple does not bind the persisted result",
        )
    finally:
        gateway.close()

    approval = _immutable_connection(project_root, approval_path)
    try:
        schema = tuple(
            approval.execute(
                "SELECT value FROM approval_schema_metadata WHERE key='schema_version'"
            )
        )
        _require(
            len(schema) == 1 and schema[0][0] == str(ACTION_GRANT_SCHEMA_VERSION),
            "approval database schema version drifted",
        )
        _require(
            approval.execute("SELECT COUNT(*) FROM one_time_action_grants").fetchone()[
                0
            ]
            == 1,
            "completed run must have exactly one grant",
        )
        grant = approval.execute(
            "SELECT grant_id, run_id, interaction_id, interaction_sha256, "
            "request_sha256, action_sha256, action_json, action_kind, "
            "execution_manifest_sha256, audit_binding_version, "
            "confirmation_reference, consumed "
            "FROM one_time_action_grants"
        ).fetchone()
        _require(grant is not None, "approval grant is absent")
        _require(grant["run_id"] == run_id, "grant references another run")
        _require(
            grant["interaction_id"] == expectations.interaction_id,
            "grant interaction ID drifted",
        )
        _require(
            grant["interaction_sha256"] == expectations.interaction_sha256,
            "grant interaction SHA drifted",
        )
        _require(
            grant["request_sha256"] == expectations.request_sha256,
            "grant request SHA drifted",
        )
        _require(
            grant["action_sha256"] == expectations.action_sha256,
            "grant action SHA drifted",
        )
        _require(grant["action_kind"] == "approve", "grant decision is not approve")
        _require(
            grant["execution_manifest_sha256"]
            == expectations.execution_manifest_sha256,
            "approved execution manifest differs from the expected value",
        )
        _require(
            grant["audit_binding_version"] == 1,
            "grant audit binding is not fully migrated",
        )
        _require(grant["consumed"] == 1, "one-time grant was not consumed")
        _require(
            is_valid_confirmation_reference(grant["confirmation_reference"]),
            "grant confirmation reference is invalid",
        )
        _require(
            grant["confirmation_reference"] == expectations.confirmation_reference,
            "grant confirmation reference differs from the expected value",
        )
        action_payload = grant["action_json"].encode("utf-8")
        _decode_json(action_payload, label="grant action")
        try:
            action = ApproveActionV1.model_validate_json(action_payload)
        except ValidationError as exc:
            raise CompletedRunVerificationError(
                "grant action is not the exact approve action"
            ) from exc
        _require(
            action.interaction_id == expectations.interaction_id,
            "grant action interaction ID drifted",
        )
        action_bytes = gateway_canonical_json_bytes(action)
        _require(
            action_bytes.decode("utf-8") == grant["action_json"],
            "grant action JSON is not canonical",
        )
        _require(
            _sha256(action_bytes) == expectations.action_sha256,
            "approve action hash does not recompute",
        )
        expected_grant_id = (
            "grant-"
            + _sha256(
                gateway_canonical_json_bytes(
                    {
                        "action_sha256": grant["action_sha256"],
                        "confirmation_reference": grant["confirmation_reference"],
                        "interaction_id": grant["interaction_id"],
                        "interaction_sha256": grant["interaction_sha256"],
                        "request_sha256": grant["request_sha256"],
                        "run_id": grant["run_id"],
                    }
                )
            )[:24]
        )
        _require(grant["grant_id"] == expected_grant_id, "grant ID does not recompute")
        _require(
            approval.execute(
                "SELECT COUNT(*) FROM one_time_action_grant_recoveries"
            ).fetchone()[0]
            == 0,
            "grant recovery is forbidden for the release run",
        )
        grant_summary = {
            "action_kind": grant["action_kind"],
            "action_sha256": grant["action_sha256"],
            "confirmation_reference": grant["confirmation_reference"],
            "execution_manifest_sha256": grant[
                "execution_manifest_sha256"
            ],
            "grant_id": grant["grant_id"],
            "interaction_sha256": grant["interaction_sha256"],
        }
    finally:
        approval.close()
    return record, result, grant_summary


def _require_canonical_model(payload: bytes, model: BaseModel, *, label: str) -> None:
    _require(canonical_json_bytes(model) == payload, f"{label} is not canonical JSON")


def _dict_rows(
    payload: bytes, *, label: str, identity_key: str
) -> tuple[dict[str, Any], ...]:
    return tuple(
        value
        for value, _line in _dict_rows_with_bytes(
            payload,
            label=label,
            identity_key=identity_key,
        )
    )


def _dict_rows_with_bytes(
    payload: bytes, *, label: str, identity_key: str
) -> tuple[tuple[dict[str, Any], bytes], ...]:
    encoded_rows: list[tuple[dict[str, Any], bytes]] = []
    identities: set[Any] = set()
    for value, line in _decode_jsonl(payload, label=label):
        _require(isinstance(value, dict), f"{label} row must be an object")
        _require(identity_key in value, f"{label} row lacks {identity_key}")
        identity = value[identity_key]
        _require(
            identity not in identities, f"{label} repeats {identity_key} {identity!r}"
        )
        identities.add(identity)
        expected = json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
        _require(expected == line, f"{label} row is not canonical JSON")
        encoded_rows.append((value, line))
    return tuple(encoded_rows)


def _replay_public_search_and_passages(
    *,
    policy: InspirationPolicyV1,
    graph: TagGraphV1,
    target_tag_ids: tuple[str, ...],
    queries: tuple[SearchQueryV1, ...],
    search_attempts: tuple[SearchAttemptRecord, ...],
    hits: tuple[SearchHitV1, ...],
    passages: tuple[PassageV1, ...],
    raw_pointers: tuple[ArtifactPointerV1, ...],
    payloads: dict[str, bytes],
    fetch_manifest: tuple[dict[str, Any], ...],
    fetch_manifest_lines: dict[str, bytes],
    query_candidate_pool: CuratedQueryCandidatePoolV1,
    query_allocation_audit: QueryAllocationAuditV1,
    metadata_quality: MetadataQualityAuditV1,
) -> tuple[Any, tuple[Any, ...], tuple[str, ...]]:
    """Replay query planning, Crossref parsing, and metadata passage selection."""

    query_plan = plan_tag_queries(
        graph,
        target_tag_ids=target_tag_ids,
        budget=policy.search,
    )
    _require(
        query_plan.queries == queries, "query plan does not deterministically replay"
    )
    _require(
        query_plan.candidate_pool == query_candidate_pool,
        "curated query candidate pool does not deterministically replay",
    )
    _require(
        query_plan.allocation_audit == query_allocation_audit,
        "query allocation audit does not deterministically replay",
    )
    _require(
        query_plan.allocation_audit is not None,
        "query plan has no physical-request allocation audit",
    )
    physical_allowance_by_query = {
        allowance.query_id: allowance.max_physical_requests
        for allowance in query_plan.allocation_audit.allowances
    }
    _require(
        set(physical_allowance_by_query) == {query.query_id for query in queries},
        "physical-request allowances do not close over the query plan",
    )
    warnings: list[str] = [
        f"QUERY_RULE_SKIPPED:{rule_id}" for rule_id in query_plan.skipped_rule_ids
    ]

    raw_by_query = {
        PurePosixPath(pointer.uri.removeprefix("artifact://")).stem: pointer
        for pointer in raw_pointers
    }
    _require(
        set(raw_by_query) == {query.query_id for query in queries},
        "raw query responses do not close over the plan",
    )
    replayed_hits: list[SearchHitV1] = []
    for query in queries:
        pointer = raw_by_query[query.query_id]
        _require(
            pointer.media_type == "application/json",
            "Crossref raw response is not JSON",
        )
        _require(
            pointer.size_bytes <= MAX_SEARCH_RESPONSE_BYTES,
            f"search query {query.query_id} raw response exceeds the byte bound",
        )
        raw_value = _decode_json(
            payloads[pointer.uri],
            label=f"Crossref raw response {query.query_id}",
        )
        _reject_embedded_body_fields(raw_value)
        _validate_crossref_metadata_envelope(raw_value)
        query_attempts = tuple(
            attempt for attempt in search_attempts if attempt.query_id == query.query_id
        )
        _require(
            bool(query_attempts),
            f"search query {query.query_id} has no physical attempt",
        )
        _require(
            len(query_attempts) <= physical_allowance_by_query[query.query_id],
            f"search query {query.query_id} exceeded its reserved physical slots",
        )
        _require(
            query_attempts[-1].response_bytes == pointer.size_bytes,
            f"search query {query.query_id} terminal byte count differs from its raw response",
        )
        remaining_hits = policy.search.max_raw_hits - len(replayed_hits)
        _require(
            remaining_hits > 0, "raw-hit budget ended before every persisted query"
        )
        parsed = parse_crossref_page(
            query=query,
            payload=payloads[pointer.uri],
            raw_response_artifact=pointer,
            max_hits=remaining_hits,
        )
        replayed_hits.extend(parsed.hits)
        warnings.extend(parsed.warnings)
    _require(
        tuple(replayed_hits) == hits,
        "Crossref raw responses do not replay to search_hits.jsonl",
    )
    replayed_metadata_quality = audit_metadata_hits(
        hits,
        require_abstract=policy.fetch.max_requests == 0,
    )
    _require(
        replayed_metadata_quality == metadata_quality,
        "metadata quality audit does not exactly replay from search hits",
    )

    groups = group_document_hits(hits)
    group_by_document = {group.document_id: group for group in groups}
    manifest_by_document = {row["document_id"]: row for row in fetch_manifest}
    _require(
        set(manifest_by_document) == set(group_by_document),
        "fetch manifest document identities differ from canonical search groups",
    )

    query_index = {query.query_id: query for query in queries}
    hit_index = {hit.hit_id: hit for hit in hits}
    tag_index = {tag.tag_id: tag for tag in graph.tags}
    selected: list[PassageV1] = []
    selected_keys: set[tuple[str, str]] = set()
    config = selection_config_from_policy(policy.passages)
    metadata_limits = ExtractionLimits(
        max_input_bytes=MAX_SEARCH_RESPONSE_BYTES,
        max_drafts=64,
        min_abstract_tokens=policy.passages.min_tokens,
    )
    for group in groups:
        member_hits = tuple(hit_index[hit_id] for hit_id in group.member_hit_ids)
        hit = _select_document_processing_hit(
            member_hits,
            query_index=query_index,
            quality_rank_by_hit_id={
                item.hit_id: (
                    item.eligible_for_evidence_ranking,
                    item.quality_rank,
                )
                for item in metadata_quality.entries
            },
        )
        source_query_id = hit.query_ids[0]
        source_pointer = raw_by_query[source_query_id]
        member_query_ids = tuple(
            sorted(
                {query_id for member in member_hits for query_id in member.query_ids}
            )
        )
        member_queries = tuple(query_index[query_id] for query_id in member_query_ids)
        member_tag_ids = tuple(
            sorted({tag_id for query in member_queries for tag_id in query.tag_ids})
        )
        _require(
            set(member_tag_ids).issubset(tag_index),
            "search group references an unknown TagGraph tag",
        )
        tag_terms = {
            tag_id: (
                *tag_index[tag_id].query_terms,
                *tag_index[tag_id].synonyms,
                tag_index[tag_id].label,
            )
            for tag_id in member_tag_ids
        }
        target_terms = tuple(
            dict.fromkeys(
                tuple(query.text for query in member_queries)
                + tuple(term for terms in tag_terms.values() for term in terms)
            )
        )
        extraction = extract_crossref_metadata(
            payloads[source_pointer.uri],
            target_terms=target_terms,
            limits=metadata_limits,
            result_index=hit.provider_rank - 1,
        )
        metadata_decision = extraction.decision
        fetch_status = (
            "SKIPPED_METADATA_SUFFICIENT"
            if metadata_decision is ExtractionDecision.SKIP_BODY_ABSTRACT_SUFFICIENT
            else "METADATA_UNEXTRACTABLE"
        )
        fetch_error_code: str | None = None
        if metadata_decision is ExtractionDecision.FETCH_BODY_METADATA_INSUFFICIENT:
            _require(
                policy.fetch.max_requests == 0,
                "metadata-only replay encountered an enabled body fetch",
            )
            fetch_status = "DISABLED_BY_POLICY"
            fetch_error_code = "DOCUMENT_FETCH_DISABLED_BY_POLICY"

        passage_drafts = select_passage_drafts(
            extraction.drafts,
            hit_id=hit.hit_id,
            document_id=group.document_id,
            query_terms=target_terms,
            tag_terms=tag_terms,
            config=config,
            document_title=extraction.title or hit.title,
        )
        accepted_passage_ids: list[str] = []
        selected_locator_kinds: set[str] = set()
        deduplicated_for_hit = 0
        for draft in passage_drafts:
            _require(
                draft.source_tier is ExtractionTier.METADATA_API,
                "metadata-only replay produced a non-metadata passage",
            )
            passage_key = (group.document_id, draft.normalized_text_sha256)
            if passage_key in selected_keys:
                deduplicated_for_hit += 1
                continue
            if len(selected) >= policy.passages.max_total:
                warnings.append("PASSAGE_BUDGET_EXHAUSTED")
                break
            selected_keys.add(passage_key)
            passage = PassageV1(
                passage_id=deterministic_id(
                    "passage",
                    {
                        "document_id": group.document_id,
                        "locator": draft.locator,
                        "normalized_text_sha256": draft.normalized_text_sha256,
                    },
                ),
                hit_id=hit.hit_id,
                document_id=group.document_id,
                source_artifact=hit.raw_response_artifact,
                normalizer=draft.normalizer,
                locator=draft.locator,
                text=draft.text,
                char_count=len(draft.text),
                estimated_token_count=draft.estimated_token_count,
                normalized_text_sha256=draft.normalized_text_sha256,
                matched_tag_ids=draft.matched_tag_ids,
                lexical_score=draft.lexical_score,
            )
            selected.append(passage)
            accepted_passage_ids.append(passage.passage_id)
            selected_locator_kinds.add(passage.locator.kind.value)

        expected_row = json.loads(
            canonical_json_bytes(
                {
                    "schema_version": "inspiration-fetch-manifest-v1",
                    "decision": extraction.decision.value,
                    "metadata_decision": metadata_decision.value,
                    "deduplicated_passage_count": deduplicated_for_hit,
                    "document_id": group.document_id,
                    "fetch_error_category": None,
                    "fetch_error_code": fetch_error_code,
                    "fetch_request_id": None,
                    "fetch_response_bytes": 0,
                    "fetch_status": fetch_status,
                    "fetched": False,
                    "fetched_body_artifact": None,
                    "hit_id": hit.hit_id,
                    "member_hit_ids": group.member_hit_ids,
                    "media_type": extraction.media_type,
                    "metadata_media_type": extraction.media_type,
                    "available_locator_kinds": tuple(
                        sorted(
                            {draft.locator_kind.value for draft in extraction.drafts}
                        )
                    ),
                    "physical_request_count": 0,
                    "query_ids": member_query_ids,
                    "raw_response_uri": hit.raw_response_artifact.uri,
                    "representative_hit_id": hit.hit_id,
                    "selected_locator_kinds": tuple(sorted(selected_locator_kinds)),
                    "selected_passage_ids": tuple(accepted_passage_ids),
                    "selected_passage_count": len(accepted_passage_ids),
                    "warnings": tuple(sorted(set(extraction.warnings))),
                }
            )
        )
        _require(
            canonical_json_bytes(expected_row)
            == fetch_manifest_lines[group.document_id],
            f"fetch manifest row for {group.document_id} does not exactly replay",
        )
        warnings.extend(extraction.warnings)
        if metadata_decision is ExtractionDecision.FETCH_BODY_METADATA_INSUFFICIENT:
            warnings.append(f"BODY_NOT_FETCHED:{hit.hit_id}")

    _require(
        tuple(selected) == passages,
        "metadata responses do not replay to passages.jsonl",
    )
    return query_plan, groups, tuple(warnings)


def _replay_vectors(
    *,
    policy: InspirationPolicyV1,
    hits: tuple[SearchHitV1, ...],
    passages: tuple[PassageV1, ...],
    vectors: tuple[PassageVectorV1, ...],
    vector_payloads: dict[str, bytes],
) -> tuple[int, tuple[FeedbackVectorAllocationV1, ...]]:
    """Replay selected-passage vector bytes, hashes, cache keys, and token cost."""

    hit_index = {hit.hit_id: hit for hit in hits}
    unique_passages: list[PassageV1] = []
    seen_passages: set[tuple[str, str]] = set()
    for passage in passages:
        key = (passage.document_id, passage.normalized_text_sha256)
        if key in seen_passages:
            continue
        seen_passages.add(key)
        unique_passages.append(passage)
    selected = tuple(unique_passages[: policy.embedding.max_passages])
    vector_by_passage = {vector.passage_id: vector for vector in vectors}
    _require(
        set(vector_by_passage) == {passage.passage_id for passage in selected},
        "vector manifest does not identify the deterministic selected passages",
    )
    requests = tuple(
        PassageVectorizationRequest(
            passage=passage,
            title=hit_index[passage.hit_id].title,
            normalized_tags=passage.matched_tag_ids,
            vector_artifact=vector_by_passage[passage.passage_id].vector_artifact,
        )
        for passage in selected
    )
    generated = vectorize_selected_passages(
        requests,
        budget=policy.embedding,
        vectorizer=SIGNED_HASHING_SNAPSHOT,
    )
    _require(
        tuple(item.passage_vector for item in generated) == vectors,
        "passage vector records do not deterministically replay",
    )
    for item in generated:
        _require(
            vector_payloads[item.passage_vector.vector_artifact.uri]
            == item.artifact_bytes,
            f"vector bytes for {item.passage_vector.passage_id} do not replay",
        )
    return (
        sum(item.input_token_count for item in generated),
        tuple(
            FeedbackVectorAllocationV1(
                passage_id=item.passage_vector.passage_id,
                input_token_count=item.input_token_count,
            )
            for item in generated
        ),
    )


def _verify_artifacts(
    project_root: Path,
    record: GatewayRunRecordV1,
    result: GatewayResultRecordV1,
    expectations: VerificationExpectations,
) -> dict[str, Any]:
    run_id = record.run_id
    stage_relative = PurePosixPath("stages", "inspiration", run_id)
    stage_root = project_root.joinpath(*stage_relative.parts)
    _require(
        stage_root.is_dir() and not stage_root.is_symlink(),
        "stage directory is unavailable or symlinked",
    )
    pointer_index: dict[str, ArtifactPointerV1] = {}

    stage_result_path = stage_root / "stage_result.json"
    stage_result_bytes = _read_regular_file(
        project_root, stage_result_path, label="stage_result.json"
    )
    stage = _model_from_bytes(
        InspirationStageResultV1, stage_result_bytes, label="stage_result.json"
    )
    _require_canonical_model(stage_result_bytes, stage, label="stage_result.json")
    _require(stage.run_id == run_id, "stage result references another run")
    _require(
        stage.project_id == project_root.name,
        "stage project ID differs from the fixed project",
    )
    stage_uri_prefix = f"artifact://{stage_relative.as_posix()}"
    expected_stage_roles = (
        (
            stage.input_snapshot_artifact,
            f"{stage_uri_prefix}/input_snapshot.json",
            "application/json",
        ),
        (
            stage.policy_artifact,
            f"{stage_uri_prefix}/policy.json",
            "application/json",
        ),
        (
            stage.bundle_artifact,
            f"{stage_uri_prefix}/inspiration_bundle.json",
            "application/json",
        ),
        (
            stage.report_artifact,
            f"{stage_uri_prefix}/report.md",
            "text/markdown",
        ),
        (
            stage.cost_ledger_artifact,
            f"{stage_uri_prefix}/cost_ledger.json",
            "application/json",
        ),
    )
    for pointer, expected_uri, expected_media_type in expected_stage_roles:
        _require(
            pointer.uri == expected_uri and pointer.media_type == expected_media_type,
            f"stage role must use canonical Artifact {expected_uri}",
        )

    declared = (
        stage.input_snapshot_artifact,
        stage.policy_artifact,
        stage.bundle_artifact,
        stage.report_artifact,
        stage.cost_ledger_artifact,
        *stage.intermediate_artifacts,
    )
    payloads = {
        pointer.uri: _read_pointer(project_root, pointer, pointer_index=pointer_index)
        for pointer in declared
    }

    inspiration_input = _model_from_bytes(
        InspirationInputV1,
        payloads[stage.input_snapshot_artifact.uri],
        label="input_snapshot.json",
    )
    _require_canonical_model(
        payloads[stage.input_snapshot_artifact.uri],
        inspiration_input,
        label="input_snapshot.json",
    )
    policy = _model_from_bytes(
        InspirationPolicyV1,
        payloads[stage.policy_artifact.uri],
        label="policy.json",
    )
    _require_canonical_model(
        payloads[stage.policy_artifact.uri], policy, label="policy.json"
    )
    bundle = _model_from_bytes(
        InspirationBundleV1,
        payloads[stage.bundle_artifact.uri],
        label="inspiration_bundle.json",
    )
    _require_canonical_model(
        payloads[stage.bundle_artifact.uri], bundle, label="inspiration_bundle.json"
    )
    ledger = _model_from_bytes(
        CostLedgerV1,
        payloads[stage.cost_ledger_artifact.uri],
        label="cost_ledger.json",
    )
    _require_canonical_model(
        payloads[stage.cost_ledger_artifact.uri], ledger, label="cost_ledger.json"
    )

    _require(
        inspiration_input.run_id == run_id, "input snapshot references another run"
    )
    _require(
        inspiration_input.project_id == stage.project_id,
        "input/stage project IDs disagree",
    )
    _require(
        inspiration_input.request_id == stage.request_id == bundle.request_id,
        "request IDs disagree across stage artifacts",
    )
    _require(bundle.run_id == run_id, "bundle references another run")
    _require(stage.outcome == bundle.outcome, "stage and bundle outcomes disagree")
    _require(
        result.bundle.outcome == bundle.outcome.value,
        "Gateway and Artifact outcomes disagree",
    )
    _require(
        result.bundle.limitations == bundle.limitations,
        "Gateway and Artifact limitations disagree",
    )
    _require(
        result.bundle.next_validation_steps == bundle.next_validation_steps,
        "Gateway and Artifact next steps disagree",
    )
    _require(bundle.cost_ledger == ledger, "bundle and cost-ledger Artifact disagree")
    _require(
        tuple(stage.intermediate_artifacts) == tuple(bundle.lineage_artifacts),
        "stage intermediates and bundle lineage are not exactly equal",
    )
    _require(
        stage.report_artifact.uri == result.report_uri,
        "stage and Gateway report URIs disagree",
    )
    _require(
        stage.report_artifact.sha256 == result.authoritative_sha256,
        "stage and Gateway report hashes disagree",
    )
    report_bytes = payloads[stage.report_artifact.uri]
    _require(
        len(report_bytes) <= _MAX_REPORT_BYTES, "authoritative report is too large"
    )
    try:
        report_bytes.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise CompletedRunVerificationError(
            "authoritative report is not UTF-8"
        ) from exc
    external_pointers = (
        inspiration_input.requirement_artifact,
        inspiration_input.policy_artifact,
        inspiration_input.tag_graph_artifact,
        inspiration_input.transformation_registry_artifact,
        *(
            candidate.structure_artifact
            for candidate in inspiration_input.parent_candidates
        ),
    )
    external_payloads = {
        pointer.uri: _read_pointer(
            project_root,
            pointer,
            pointer_index=pointer_index,
        )
        for pointer in external_pointers
    }
    _require(
        inspiration_input.search_fixture_artifact is None,
        "public release must not use a search fixture",
    )
    _require(
        policy.search_mode is SearchExecutionMode.PUBLIC_METADATA_API,
        "release policy is not public metadata mode",
    )
    _require(policy.network_access, "public metadata policy lacks network access")
    current_crossref_profiles = _current_crossref_components()
    _require(
        inspiration_input.search_adapter in current_crossref_profiles,
        "frozen Crossref component differs from the current production profile",
    )
    max_pacing_delay_seconds = current_crossref_profiles[
        inspiration_input.search_adapter
    ]
    _require(
        inspiration_input.vectorizer == SIGNED_HASHING_SNAPSHOT,
        "frozen vectorizer differs from the current production snapshot",
    )
    _require(
        inspiration_input.llm_adapter is None,
        "frozen input names an internal model adapter",
    )
    _require(
        inspiration_input.policy_artifact.sha256 == stage.policy_artifact.sha256,
        "frozen and stage policy hashes disagree",
    )

    intermediate_by_name: dict[str, ArtifactPointerV1] = {}
    raw_pointers: list[ArtifactPointerV1] = []
    vector_binary_pointers: list[ArtifactPointerV1] = []
    structure_pointers: list[ArtifactPointerV1] = []
    for pointer in stage.intermediate_artifacts:
        path = PurePosixPath(pointer.uri.removeprefix("artifact://"))
        try:
            relative = path.relative_to(stage_relative)
        except ValueError as exc:
            raise CompletedRunVerificationError(
                f"stage intermediate escapes the stage directory: {pointer.uri}"
            ) from exc
        name = relative.as_posix()
        _require(name not in intermediate_by_name, f"stage repeats intermediate {name}")
        intermediate_by_name[name] = pointer
        if name.startswith("raw_search/"):
            raw_pointers.append(pointer)
        elif name.startswith("vectors/"):
            vector_binary_pointers.append(pointer)
        elif name.startswith("structures/"):
            structure_pointers.append(pointer)

    required_names = {
        "query_candidate_pool.json",
        "query_allocation_audit.json",
        "query_plans.jsonl",
        "search_attempts.jsonl",
        "search_hits.jsonl",
        "metadata_quality_audit.json",
        "fetch_attempts.jsonl",
        "fetch_manifest.jsonl",
        "passages.jsonl",
        "passage_vectors.jsonl",
        "evidence_cards.jsonl",
        "tag_graph.json",
        "bridge_packets.jsonl",
        "transformation_proposals.jsonl",
        "internal_duplicate_groups.jsonl",
        "selection_audit.json",
        "tag_feedback.json",
    }
    _require(
        required_names.issubset(intermediate_by_name),
        "stage is missing required intermediate Artifacts",
    )
    json_intermediate_names = {
        "query_candidate_pool.json",
        "query_allocation_audit.json",
        "metadata_quality_audit.json",
        "selection_audit.json",
        "tag_feedback.json",
        "tag_graph.json",
    }
    for name in required_names:
        expected_media_type = (
            "application/json"
            if name in json_intermediate_names
            else "application/x-ndjson"
        )
        _require(
            intermediate_by_name[name].media_type == expected_media_type,
            f"stage role {name} has a noncanonical media type",
        )
    _require(
        all(pointer.media_type == "application/json" for pointer in raw_pointers),
        "raw Crossref Artifacts have a noncanonical media type",
    )
    _require(
        all(
            pointer.media_type == VECTOR_ARTIFACT_MEDIA_TYPE
            for pointer in vector_binary_pointers
        ),
        "vector Artifacts have a noncanonical media type",
    )
    _require(
        all(
            pointer.media_type == STRUCTURE_MEDIA_TYPE for pointer in structure_pointers
        ),
        "structure Artifacts have a noncanonical media type",
    )
    _require(
        not any(
            name.startswith(("raw_fetch/", "fetched_documents/"))
            for name in intermediate_by_name
        ),
        "stage contains a fetched-body Artifact",
    )
    _require(
        all(
            pointer.media_type is None or "pdf" not in pointer.media_type.casefold()
            for pointer in declared
        ),
        "stage declares a PDF media type",
    )
    _require(
        all(not _looks_like_pdf_payload(payloads[pointer.uri]) for pointer in declared),
        "stage contains PDF signature bytes",
    )

    def named(name: str) -> bytes:
        return payloads[intermediate_by_name[name].uri]

    queries = _models_from_jsonl(
        SearchQueryV1,
        named("query_plans.jsonl"),
        label="query_plans.jsonl",
        identity=lambda item: item.query_id,
    )
    query_candidate_pool = _model_from_bytes(
        CuratedQueryCandidatePoolV1,
        named("query_candidate_pool.json"),
        label="query_candidate_pool.json",
    )
    _require_canonical_model(
        named("query_candidate_pool.json"),
        query_candidate_pool,
        label="query_candidate_pool.json",
    )
    query_allocation_audit = _model_from_bytes(
        QueryAllocationAuditV1,
        named("query_allocation_audit.json"),
        label="query_allocation_audit.json",
    )
    _require_canonical_model(
        named("query_allocation_audit.json"),
        query_allocation_audit,
        label="query_allocation_audit.json",
    )
    search_attempts = _dataclasses_from_jsonl(
        SearchAttemptRecord,
        named("search_attempts.jsonl"),
        label="search_attempts.jsonl",
        identity=lambda item: (item.query_id, item.attempt_number),
        to_dict=lambda item: item.to_dict(),
    )
    hits = _models_from_jsonl(
        SearchHitV1,
        named("search_hits.jsonl"),
        label="search_hits.jsonl",
        identity=lambda item: item.hit_id,
    )
    metadata_quality = _model_from_bytes(
        MetadataQualityAuditV1,
        named("metadata_quality_audit.json"),
        label="metadata_quality_audit.json",
    )
    _require_canonical_model(
        named("metadata_quality_audit.json"),
        metadata_quality,
        label="metadata_quality_audit.json",
    )
    fetch_attempts = _dataclasses_from_jsonl(
        FetchAttemptRecord,
        named("fetch_attempts.jsonl"),
        label="fetch_attempts.jsonl",
        identity=lambda item: (item.request_id, item.attempt_number),
        to_dict=lambda item: item.to_dict(),
    )
    fetch_manifest_rows = _dict_rows_with_bytes(
        named("fetch_manifest.jsonl"),
        label="fetch_manifest.jsonl",
        identity_key="document_id",
    )
    fetch_manifest = tuple(row for row, _line in fetch_manifest_rows)
    fetch_manifest_lines = {
        row["document_id"]: line for row, line in fetch_manifest_rows
    }
    passages = _models_from_jsonl(
        PassageV1,
        named("passages.jsonl"),
        label="passages.jsonl",
        identity=lambda item: item.passage_id,
    )
    vectors = _models_from_jsonl(
        PassageVectorV1,
        named("passage_vectors.jsonl"),
        label="passage_vectors.jsonl",
        identity=lambda item: item.passage_id,
    )
    evidence = _models_from_jsonl(
        EvidenceCardV1,
        named("evidence_cards.jsonl"),
        label="evidence_cards.jsonl",
        identity=lambda item: item.evidence_card_id,
    )
    bridges = _models_from_jsonl(
        BridgePacketV1,
        named("bridge_packets.jsonl"),
        label="bridge_packets.jsonl",
        identity=lambda item: item.bridge_packet_id,
    )
    plans = _models_from_jsonl(
        TransformationPlanV1,
        named("transformation_proposals.jsonl"),
        label="transformation_proposals.jsonl",
        identity=lambda item: item.plan_id,
    )
    duplicate_groups = _dict_rows(
        named("internal_duplicate_groups.jsonl"),
        label="internal_duplicate_groups.jsonl",
        identity_key="candidate_id",
    )
    selection_value = _decode_json(
        named("selection_audit.json"), label="selection_audit.json"
    )
    _require(isinstance(selection_value, dict), "selection audit must be an object")
    _require(
        json.dumps(
            selection_value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
        == named("selection_audit.json"),
        "selection audit is not canonical JSON",
    )
    feedback = _model_from_bytes(
        TagFeedbackReviewV1, named("tag_feedback.json"), label="tag_feedback.json"
    )
    _require_canonical_model(
        named("tag_feedback.json"), feedback, label="tag_feedback.json"
    )
    tag_graph = _model_from_bytes(
        TagGraphV1, named("tag_graph.json"), label="tag_graph.json"
    )
    _require_canonical_model(named("tag_graph.json"), tag_graph, label="tag_graph.json")
    _require(
        inspiration_input.tag_graph_artifact.sha256
        == intermediate_by_name["tag_graph.json"].sha256,
        "frozen and stage TagGraph hashes disagree",
    )

    vector_payloads = {
        vector.vector_artifact.uri: _read_pointer(
            project_root,
            vector.vector_artifact,
            pointer_index=pointer_index,
        )
        for vector in vectors
    }
    structure_payloads: dict[str, bytes] = {}
    for plan in plans:
        _read_pointer(
            project_root, plan.parent_structure_artifact, pointer_index=pointer_index
        )
        if plan.output_structure_artifact is not None:
            structure_payloads[plan.output_structure_artifact.uri] = _read_pointer(
                project_root,
                plan.output_structure_artifact,
                pointer_index=pointer_index,
            )
    for candidate in bundle.selected_candidates:
        _read_pointer(
            project_root, candidate.structure_artifact, pointer_index=pointer_index
        )
    _require(
        {item.vector_artifact.uri for item in vectors}
        == {item.uri for item in vector_binary_pointers},
        "vector manifest and declared vector files disagree",
    )
    _require(
        {
            PurePosixPath(item.uri.removeprefix("artifact://"))
            .relative_to(stage_relative)
            .as_posix()
            for item in vector_binary_pointers
        }
        == {f"vectors/{item.passage_id}.f32le" for item in vectors},
        "vector Artifact paths are not canonical",
    )
    _require(
        {
            item.output_structure_artifact.uri
            for item in plans
            if item.output_structure_artifact is not None
        }
        == {item.uri for item in structure_pointers},
        "transformation plans and declared structure files disagree",
    )
    allowed_intermediate_names = required_names | {
        PurePosixPath(pointer.uri.removeprefix("artifact://"))
        .relative_to(stage_relative)
        .as_posix()
        for pointer in (*raw_pointers, *vector_binary_pointers, *structure_pointers)
    }
    _require(
        set(intermediate_by_name) == allowed_intermediate_names,
        "stage declares an intermediate outside the closed release allowlist",
    )

    query_ids = {item.query_id for item in queries}
    _require(
        query_ids == {item.query_id for item in search_attempts},
        "query and physical-attempt sets disagree",
    )
    _require(bool(search_attempts), "release has no physical search attempt")
    _require(
        all(
            type(item.query_id) is str
            and type(item.attempt_number) is int
            and type(item.outcome) is str
            and (item.error_code is None or type(item.error_code) is str)
            and (item.http_status is None or type(item.http_status) is int)
            and type(item.retry_delay_seconds) is float
            and type(item.pacing_delay_seconds) is float
            and type(item.response_bytes) is int
            for item in search_attempts
        ),
        "search attempt audit fields do not use canonical production types",
    )
    _require(
        search_attempts[0].pacing_delay_seconds == 0.0,
        "first Crossref attempt has a nonzero pacing delay",
    )
    _require(
        all(
            item.pacing_delay_seconds <= max_pacing_delay_seconds
            for item in search_attempts
        ),
        "Crossref pacing delay exceeds the frozen pool interval",
    )
    for query_id in query_ids:
        query_attempts = tuple(
            item for item in search_attempts if item.query_id == query_id
        )
        _require(
            sum(item.outcome == "error" for item in query_attempts)
            <= _PUBLIC_CROSSREF_MAX_RETRIES,
            f"search query {query_id} exceeds the frozen retry bound",
        )
        _require(
            tuple(item.attempt_number for item in query_attempts)
            == tuple(range(1, len(query_attempts) + 1)),
            f"search attempts for {query_id} are not contiguous",
        )
        _require(
            all(
                item.outcome in {"error", "redirect"}
                for item in query_attempts[:-1]
            ),
            f"search query {query_id} continued after a successful attempt",
        )
        _require(
            all(
                item.outcome == "redirect"
                and item.error_code is None
                and item.http_status in {301, 302, 303, 307, 308}
                and item.response_bytes == 0
                and item.retry_delay_seconds == 0.0
                or item.outcome == "error"
                and (
                    (
                        item.error_code == "NETWORK_ERROR"
                        and item.http_status is None
                        and item.response_bytes == 0
                        and item.retry_delay_seconds == 1.0
                    )
                    or (
                        item.error_code == "TRANSIENT_HTTP_ERROR"
                        and item.http_status in _TRANSIENT_HTTP_STATUSES
                        and item.response_bytes == 0
                    )
                )
                for item in query_attempts[:-1]
            ),
            f"search query {query_id} retried a noncanonical transient error or redirect",
        )
        _require(
            query_attempts[-1].outcome == "success",
            f"search query {query_id} has no terminal successful attempt",
        )
        _require(
            query_attempts[-1].http_status == 200
            and query_attempts[-1].retry_delay_seconds == 0,
            f"search query {query_id} has an invalid terminal success record",
        )
        _require(
            all(
                item.retry_delay_seconds <= _PUBLIC_CROSSREF_MAX_RETRY_DELAY_SECONDS
                and item.response_bytes <= MAX_SEARCH_RESPONSE_BYTES
                for item in query_attempts
            ),
            f"search query {query_id} exceeds a response or retry-delay bound",
        )
        _require(
            sum(
                item.pacing_delay_seconds + item.retry_delay_seconds
                for item in query_attempts
            )
            <= _PUBLIC_CROSSREF_MAX_TOTAL_WAIT_SECONDS,
            f"search query {query_id} exceeds the frozen wait bound",
        )
    expected_attempt_keys = tuple(
        (query.query_id, attempt_number)
        for query in queries
        for attempt_number in range(
            1,
            sum(item.query_id == query.query_id for item in search_attempts) + 1,
        )
    )
    _require(
        tuple((item.query_id, item.attempt_number) for item in search_attempts)
        == expected_attempt_keys,
        "search attempts are not in canonical query-plan order",
    )
    _require(
        len(raw_pointers) == len(queries),
        "each executed query must have one raw response",
    )
    _require(
        {
            PurePosixPath(item.uri.removeprefix("artifact://"))
            .relative_to(stage_relative)
            .as_posix()
            for item in raw_pointers
        }
        == {f"raw_search/{query_id}.json" for query_id in query_ids},
        "raw response paths do not close over the query plan",
    )
    _require(
        {item.raw_response_artifact.uri for item in hits}.issubset(
            {item.uri for item in raw_pointers}
        ),
        "search hit references an undeclared raw response",
    )
    _require(
        all(set(item.query_ids).issubset(query_ids) for item in hits),
        "search hit references an unknown query",
    )
    target_tag_ids = tuple(
        sorted(
            {
                tag_id
                for query in queries
                if query.kind is SearchQueryKind.DIRECT
                for tag_id in query.tag_ids
            }
        )
    )
    prepared = PreparedInspirationRun(
        inspiration_input=inspiration_input,
        policy=policy,
        tag_graph=tag_graph,
        target_tag_ids=target_tag_ids,
    )
    parent_catalog = load_flat_band_parent_catalog_v1()
    transformation_engine = PymatgenTransformationEngine(parent_catalog=parent_catalog)
    current_execution_manifest_sha256 = prepared_execution_manifest_sha256(
        request=record.request,
        prepared=prepared,
        execution_components=(
            *execution_identity_snapshots(),
            transformation_engine.component,
            DisabledDocumentFetcher.component,
            FEEDBACK_COMPILER_SNAPSHOT,
            inspiration_input.search_adapter,
            inspiration_input.vectorizer,
        ),
    )
    _require(
        current_execution_manifest_sha256 == expectations.execution_manifest_sha256,
        "current frozen inputs/components do not reproduce the approved execution manifest",
    )
    _require(not fetch_attempts, "release performed an article-body fetch attempt")
    _require(
        all(
            row.get("schema_version") == "inspiration-fetch-manifest-v1"
            for row in fetch_manifest
        ),
        "fetch manifest schema drifted",
    )
    _require(
        all(row.get("fetched") is False for row in fetch_manifest),
        "release contains a fetched body",
    )
    _require(
        all(row.get("fetched_body_artifact") is None for row in fetch_manifest),
        "fetch manifest names a body Artifact",
    )
    _require(
        all(row.get("physical_request_count") == 0 for row in fetch_manifest),
        "fetch manifest records a body request",
    )
    _require(
        len(fetch_manifest) == len({item.document_id for item in hits}),
        "fetch manifest does not cover each canonical document exactly once",
    )
    _require(
        not any(name.lower().endswith(".pdf") for name in intermediate_by_name),
        "stage contains a PDF Artifact",
    )

    query_plan, groups, replay_warnings = _replay_public_search_and_passages(
        policy=policy,
        graph=tag_graph,
        target_tag_ids=target_tag_ids,
        queries=queries,
        search_attempts=search_attempts,
        hits=hits,
        passages=passages,
        raw_pointers=tuple(raw_pointers),
        payloads=payloads,
        fetch_manifest=fetch_manifest,
        fetch_manifest_lines=fetch_manifest_lines,
        query_candidate_pool=query_candidate_pool,
        query_allocation_audit=query_allocation_audit,
        metadata_quality=metadata_quality,
    )

    passage_ids = {item.passage_id for item in passages}
    _require(
        all(item.hit_id in {hit.hit_id for hit in hits} for item in passages),
        "passage references an unknown search hit",
    )
    _require(
        {item.source_artifact.uri for item in passages}.issubset(
            {item.uri for item in raw_pointers}
        ),
        "passage source is not a declared raw response",
    )
    _require(
        {item.passage_id for item in vectors} == passage_ids,
        "selected passage/vector identities disagree",
    )
    _require(
        all(item.dimension == policy.embedding.vector_dimension for item in vectors),
        "vector dimension differs from policy",
    )
    _require(
        all(set(item.passage_ids).issubset(passage_ids) for item in evidence),
        "evidence references an unknown passage",
    )
    evidence_ids = {item.evidence_card_id for item in evidence}
    _require(
        all(set(item.evidence_card_ids).issubset(evidence_ids) for item in bridges),
        "bridge references unknown evidence",
    )
    bridge_ids = {item.bridge_packet_id for item in bridges}
    _require(
        all(set(item.bridge_packet_ids).issubset(bridge_ids) for item in plans),
        "plan references an unknown bridge",
    )
    plan_ids = {item.plan_id for item in plans}
    _require(
        all(
            candidate.representative_plan_id in plan_ids
            for candidate in bundle.selected_candidates
        ),
        "candidate references an unknown plan",
    )
    _require(
        {row["candidate_id"] for row in duplicate_groups}.issuperset(
            {item.candidate_id for item in bundle.selected_candidates}
        ),
        "selected candidate is absent from duplicate groups",
    )

    embedding_input_tokens, vector_allocations = _replay_vectors(
        policy=policy,
        hits=hits,
        passages=passages,
        vectors=vectors,
        vector_payloads=vector_payloads,
    )
    evidence_result = build_evidence_cards(
        graph=tag_graph,
        queries=queries,
        hits=hits,
        passages=passages,
    )
    _require(
        evidence_result.cards == evidence,
        "passages do not deterministically replay to evidence cards",
    )
    bridge_result = build_search_supported_bridges(
        graph=tag_graph,
        queries=queries,
        hits=hits,
        passages=passages,
        evidence_cards=evidence,
    )
    _require(
        bridge_result.packets == bridges,
        "evidence does not deterministically replay to BridgePackets",
    )

    parent_inputs = tuple(
        ParentStructureInput(
            reference=parent,
            artifact_bytes=external_payloads[parent.structure_artifact.uri],
        )
        for parent in inspiration_input.parent_candidates
    )
    transformation_context = TransformationContext(
        inspiration_input=inspiration_input,
        policy=policy,
        tag_graph=tag_graph,
        bridge_packets=bridges,
        evidence_cards=evidence,
        parents=parent_inputs,
        registry_artifact=inspiration_input.transformation_registry_artifact,
        registry_bytes=external_payloads[
            inspiration_input.transformation_registry_artifact.uri
        ],
        artifact_prefix=f"stages/inspiration/{run_id}",
    )
    replayed_drafts = tuple(
        sorted(
            transformation_engine.generate(transformation_context),
            key=lambda draft: draft.plan.plan_id,
        )
    )
    _require(
        tuple(draft.plan for draft in replayed_drafts) == plans,
        "frozen parents/bridges do not replay to the transformation plans",
    )
    for draft in replayed_drafts:
        pointer = draft.plan.output_structure_artifact
        if draft.artifact_bytes is None:
            _require(
                pointer is None, "replayed transformation lost its structure bytes"
            )
            continue
        _require(
            pointer is not None, "replayed structure bytes have no Artifact pointer"
        )
        _require(
            structure_payloads[pointer.uri] == draft.artifact_bytes,
            f"structure bytes for {draft.plan.plan_id} do not replay",
        )

    proposals = InspirationRunner._candidate_proposals(
        inspiration_input=inspiration_input,
        drafts=replayed_drafts,
        bridge_packets=bridges,
        evidence_cards=evidence,
    )
    identities = merge_run_internal_candidates(
        proposals,
        run_id=run_id,
        policy_id=policy.policy_id,
        selection_policy=policy.selection,
    )
    selection_result = select_diverse_candidates_with_audit(
        identities,
        policy=policy.selection,
    )
    _require(
        selection_result.candidates == bundle.selected_candidates,
        "deterministic identity/dedup/MMR replay differs from the selected candidates",
    )
    expected_duplicate_groups = tuple(
        json.loads(canonical_json_bytes(row))
        for row in InspirationRunner._duplicate_group_records(identities)
    )
    _require(
        b"".join(canonical_json_bytes(row) + b"\n" for row in expected_duplicate_groups)
        == named("internal_duplicate_groups.jsonl"),
        "internal duplicate-group records do not exactly replay",
    )
    requested_route_count = (
        2 if policy.selection.min_mechanisms_when_available >= 2 else 1
    )
    selection_audit = selection_result.audit
    if selection_audit.selected_distinct_physical_route_count >= requested_route_count:
        route_quota_status = "MET"
    elif selection_audit.pool_distinct_physical_route_count < requested_route_count:
        route_quota_status = "POOL_INSUFFICIENT"
    else:
        route_quota_status = "HARD_QUOTA_INFEASIBLE"
    expected_selection_value = json.loads(
        canonical_json_bytes(
            {
                "schema_version": "inspiration-selection-audit-v1",
                "diversity_mode": (
                    "MECHANISM_COVERAGE_WHEN_FEASIBLE"
                    if policy.selection.min_mechanisms_when_available >= 2
                    else "MMR_ONLY"
                ),
                "structure_valid_proposal_count": len(proposals),
                "post_exact_merge_candidate_count": len(identities),
                "exact_merge_reduction_count": len(proposals) - len(identities),
                "requested_distinct_physical_route_count": requested_route_count,
                "route_quota_status": route_quota_status,
                **asdict(selection_audit),
            }
        )
    )
    _require(
        canonical_json_bytes(expected_selection_value) == named("selection_audit.json"),
        "selection audit does not exactly replay",
    )

    raw_pointer_by_query = {
        PurePosixPath(pointer.uri.removeprefix("artifact://")).stem: pointer
        for pointer in raw_pointers
    }
    replayed_structure_pointers = _unique_pointers(
        tuple(
            draft.plan.output_structure_artifact
            for draft in replayed_drafts
            if draft.plan.output_structure_artifact is not None
        )
    )
    expected_lineage = _unique_pointers(
        (
            intermediate_by_name["query_candidate_pool.json"],
            intermediate_by_name["query_allocation_audit.json"],
            intermediate_by_name["query_plans.jsonl"],
            intermediate_by_name["search_attempts.jsonl"],
            *(raw_pointer_by_query[query.query_id] for query in queries),
            intermediate_by_name["search_hits.jsonl"],
            intermediate_by_name["metadata_quality_audit.json"],
            intermediate_by_name["fetch_attempts.jsonl"],
            intermediate_by_name["fetch_manifest.jsonl"],
            intermediate_by_name["passages.jsonl"],
            *(vector.vector_artifact for vector in vectors),
            intermediate_by_name["passage_vectors.jsonl"],
            intermediate_by_name["evidence_cards.jsonl"],
            intermediate_by_name["tag_graph.json"],
            intermediate_by_name["bridge_packets.jsonl"],
            *replayed_structure_pointers,
            intermediate_by_name["transformation_proposals.jsonl"],
            intermediate_by_name["internal_duplicate_groups.jsonl"],
            intermediate_by_name["selection_audit.json"],
            intermediate_by_name["tag_feedback.json"],
        )
    )
    _require(
        tuple(stage.intermediate_artifacts) == expected_lineage,
        "stage intermediate lineage is not in canonical runner order",
    )
    _require(
        tuple(bundle.lineage_artifacts) == expected_lineage,
        "bundle lineage is not in canonical runner order",
    )

    expected_ledger = CostLedgerV1(
        search_requests=len(search_attempts),
        search_response_bytes=sum(pointer.size_bytes for pointer in raw_pointers),
        fetch_requests=0,
        fetch_response_bytes=0,
        raw_documents=len(hits),
        unique_documents=len(groups),
        extracted_passages=len(passages),
        vectorized_passages=len(vectors),
        embedding_input_tokens=embedding_input_tokens,
        llm_calls=0,
        llm_input_tokens=0,
        llm_output_tokens=0,
        generated_plans=len(plans),
        rejected_plans=sum(
            plan.status is TransformationStatus.REJECTED for plan in plans
        ),
        candidates_after_internal_dedup=len(identities),
        walltime_ms=ledger.walltime_ms,
    )
    _require(ledger == expected_ledger, "cost ledger does not exactly replay")

    expected_outcome = (
        InspirationOutcome.SUCCEEDED
        if selection_result.candidates
        else InspirationOutcome.SCIENTIFIC_NO_MATCH
    )
    selection_limitations = (
        (
            "Selection returned "
            f"{len(selection_result.candidates)} of requested "
            f"{policy.selection.top_k} candidates; audit reasons: "
            + ", ".join(selection_audit.underfill_reasons)
            + ".",
        )
        if selection_audit.underfill_reasons
        else ()
    )
    expected_limitations = (
        "Search evidence is limited to bounded metadata passages and may omit relevant context.",
        "Generated structures have no downstream property validation; target property status is UNKNOWN.",
        "walltime_ms is a monotonic runtime snapshot; the runner also enforces the deadline through terminal Artifact verification.",
        *selection_limitations,
    )
    expected_next_steps = tuple(
        dict.fromkeys(
            candidate.next_falsification_step
            for candidate in selection_result.candidates
        )
    ) or ("Resolve the listed review item before any downstream property calculation.",)
    expected_bundle_id = deterministic_id(
        "bundle",
        {
            "request_id": inspiration_input.request_id,
            "run_id": inspiration_input.run_id,
            "outcome": expected_outcome.value,
            "candidate_ids": tuple(
                candidate.candidate_id for candidate in selection_result.candidates
            ),
            "lineage": tuple(
                (pointer.uri, pointer.sha256, pointer.size_bytes)
                for pointer in expected_lineage
            ),
        },
    )
    _require(bundle.bundle_id == expected_bundle_id, "bundle ID does not recompute")
    _require(bundle.outcome is expected_outcome, "bundle outcome does not replay")
    _require(
        bundle.limitations == expected_limitations,
        "bundle limitations do not replay",
    )
    _require(
        bundle.next_validation_steps == expected_next_steps,
        "bundle next steps do not replay",
    )
    expected_stage_result_id = deterministic_id(
        "inspiration-result",
        {
            "project_id": inspiration_input.project_id,
            "request_id": inspiration_input.request_id,
            "run_id": inspiration_input.run_id,
            "bundle_sha256": stage.bundle_artifact.sha256,
        },
    )
    _require(
        stage.result_id == expected_stage_result_id,
        "stage result ID does not recompute",
    )

    feedback_inputs = (
        FeedbackInputArtifactV1(
            role="query-plan", artifact=intermediate_by_name["query_plans.jsonl"]
        ),
        FeedbackInputArtifactV1(
            role="search-attempts",
            artifact=intermediate_by_name["search_attempts.jsonl"],
        ),
        FeedbackInputArtifactV1(
            role="search-hits", artifact=intermediate_by_name["search_hits.jsonl"]
        ),
        FeedbackInputArtifactV1(
            role="fetch-attempts",
            artifact=intermediate_by_name["fetch_attempts.jsonl"],
        ),
        FeedbackInputArtifactV1(
            role="fetch-manifest",
            artifact=intermediate_by_name["fetch_manifest.jsonl"],
        ),
        FeedbackInputArtifactV1(
            role="passages", artifact=intermediate_by_name["passages.jsonl"]
        ),
        FeedbackInputArtifactV1(
            role="passage-vectors",
            artifact=intermediate_by_name["passage_vectors.jsonl"],
        ),
        FeedbackInputArtifactV1(
            role="evidence-cards",
            artifact=intermediate_by_name["evidence_cards.jsonl"],
        ),
        FeedbackInputArtifactV1(
            role="bridge-packets",
            artifact=intermediate_by_name["bridge_packets.jsonl"],
        ),
        FeedbackInputArtifactV1(
            role="cost-ledger", artifact=stage.cost_ledger_artifact
        ),
    )
    replayed_feedback = compile_tag_feedback_review(
        run_id=run_id,
        graph=tag_graph,
        graph_artifact=intermediate_by_name["tag_graph.json"],
        input_artifacts=feedback_inputs,
        planned_queries=query_plan.queries,
        executed_queries=queries,
        search_attempts=search_attempts,
        hits=hits,
        passages=passages,
        evidence_cards=evidence,
        bridge_packets=bridges,
        fetch_allocations=(),
        vector_allocations=vector_allocations,
    )
    _require(feedback == replayed_feedback, "tag feedback does not exactly replay")

    pipeline_warnings = list(replay_warnings)
    pipeline_warnings.extend(evidence_result.warnings)
    pipeline_warnings.extend(
        f"BRIDGE_SKIPPED:{item.bridge_rule_id}:{item.reason}"
        for item in bridge_result.skipped
    )
    review_items = tuple(
        f"Review transformation `{plan.plan_id}` because its structural "
        "checks contain an unresolved status."
        for plan in plans
        if plan.status is TransformationStatus.REQUIRES_REVIEW
    )
    if not bundle.selected_candidates and not review_items:
        empty_selection_review = (
            "Review the rejected or absent transformation records; no "
            "structure-qualified candidate entered selection."
        )
        review_items = (empty_selection_review,)
        pipeline_warnings.append("REVIEW_REQUIRED:NO_ELIGIBLE_CANDIDATE")
    pipeline_warnings.extend(
        f"SELECTION_UNDERFILL:{reason}" for reason in selection_audit.underfill_reasons
    )
    if selection_audit.quota_status is not MechanismQuotaStatus.MET:
        pipeline_warnings.append(
            f"SELECTION_MECHANISM_QUOTA:{selection_audit.quota_status.value}"
        )
    if route_quota_status != "MET":
        pipeline_warnings.append(f"SELECTION_ROUTE_QUOTA:{route_quota_status}")
    replayed_stage_warnings = _bounded_warnings(pipeline_warnings)
    _require(
        stage.warnings == replayed_stage_warnings,
        "stage warnings do not deterministically replay",
    )
    replayed_report = render_inspiration_report(
        inspiration_input=inspiration_input,
        bundle=bundle,
        queries=queries,
        hits=hits,
        passages=passages,
        evidence_cards=evidence,
        bridge_packets=bridges,
        transformation_plans=plans,
        review_items=review_items,
        warnings=replayed_stage_warnings,
        search_attempts=search_attempts,
        fetch_attempts=fetch_attempts,
        tag_feedback=feedback,
        selection_audit=selection_value,
    ).encode("utf-8")
    _require(
        report_bytes == replayed_report,
        "authoritative report does not exactly replay from its Artifacts",
    )
    projected_candidates, projected_lineage = (
        HermesFixtureProjector._project_candidates(
            bundle=bundle,
            prepared=prepared,
            passages=passages,
            evidence_cards=evidence,
            bridges=bridges,
            plans=plans,
        )
    )
    projected_readable_evidence = HermesFixtureProjector._project_readable_evidence(
        lineage=projected_lineage,
        passages=passages,
        evidence_cards=evidence,
        search_hits=hits,
    )
    stage_result_reference = ArtifactReferenceV1(
        uri=f"artifact://{stage_relative.as_posix()}/stage_result.json",
        sha256=_sha256(stage_result_bytes),
        size_bytes=len(stage_result_bytes),
        media_type="application/json",
    )
    closure_artifacts = tuple(
        sorted(
            (
                ArtifactReferenceV1(
                    uri=pointer.uri,
                    sha256=pointer.sha256,
                    size_bytes=pointer.size_bytes,
                    media_type=pointer.media_type,
                )
                for pointer in (
                    stage.input_snapshot_artifact,
                    stage.policy_artifact,
                    *stage.intermediate_artifacts,
                    stage.cost_ledger_artifact,
                    stage.report_artifact,
                    stage.bundle_artifact,
                )
            ),
            key=lambda item: item.uri,
        )
    )
    artifact_closure = ArtifactClosureV1(
        stage_result=stage_result_reference,
        artifacts=closure_artifacts,
        closure_sha256=artifact_closure_sha256(
            stage_result=stage_result_reference,
            artifacts=closure_artifacts,
        ),
    )
    replayed_gateway_result = GatewayResultRecordV1(
        run_id=run_id,
        report_uri=inspiration_report_uri(run_id),
        authoritative_sha256=stage.report_artifact.sha256,
        bundle=InspirationBundleSummaryV1(
            outcome=bundle.outcome.value,
            selected_candidates=projected_candidates,
            limitations=bundle.limitations,
            next_validation_steps=bundle.next_validation_steps,
        ),
        evidence_lineage=projected_lineage,
        readable_evidence=projected_readable_evidence,
        validation_boundaries=(
            "SEARCH_SUPPORTED records bounded source support, not property validation.",
            "STRUCTURE_VALID records deterministic structural QC only.",
            "The target property remains UNKNOWN until downstream calculation.",
        ),
        cost_ledger=CostLedgerProjectionV1(
            search_requests=ledger.search_requests,
            search_response_bytes=ledger.search_response_bytes,
            fetched_documents=0,
            extracted_passages=ledger.extracted_passages,
            vectorized_passages=ledger.vectorized_passages,
            model_calls=ledger.llm_calls,
            input_tokens=ledger.llm_input_tokens,
            output_tokens=ledger.llm_output_tokens,
            walltime_ms=ledger.walltime_ms,
        ),
        artifact_closure=artifact_closure,
    )
    _require(
        result == replayed_gateway_result,
        "persisted Gateway result does not exactly replay from authoritative Artifacts",
    )

    _require(
        ledger.search_requests == len(search_attempts),
        "search request ledger does not equal physical attempts",
    )
    _require(
        ledger.search_response_bytes
        == sum(pointer.size_bytes for pointer in raw_pointers),
        "search byte ledger does not equal raw responses",
    )
    _require(
        ledger.fetch_requests == len(fetch_attempts) == 0,
        "fetch request ledger is nonzero",
    )
    _require(ledger.fetch_response_bytes == 0, "fetch byte ledger is nonzero")
    _require(ledger.raw_documents == len(hits), "raw-document ledger mismatch")
    _require(
        ledger.unique_documents == len({item.document_id for item in hits}),
        "unique-document ledger mismatch",
    )
    _require(ledger.extracted_passages == len(passages), "passage ledger mismatch")
    _require(ledger.vectorized_passages == len(vectors), "vector ledger mismatch")
    _require(ledger.generated_plans == len(plans), "plan ledger mismatch")
    _require(
        ledger.rejected_plans
        == sum(item.status is TransformationStatus.REJECTED for item in plans),
        "rejected-plan ledger mismatch",
    )
    _require(
        ledger.candidates_after_internal_dedup == len(duplicate_groups),
        "deduplicated-candidate ledger mismatch",
    )
    _require(
        ledger.llm_calls == ledger.llm_input_tokens == ledger.llm_output_tokens == 0,
        "internal model ledger is nonzero",
    )

    budget = record.request.constraints.budget
    _require(
        len(bundle.selected_candidates) <= record.request.constraints.top_k,
        "candidate count exceeds request top_k",
    )
    _require(
        ledger.search_requests <= budget.max_search_requests,
        "search attempts exceed request budget",
    )
    _require(
        ledger.unique_documents <= budget.max_unique_documents,
        "documents exceed request budget",
    )
    _require(
        ledger.extracted_passages <= budget.max_passages,
        "passages exceed request budget",
    )
    _require(
        ledger.vectorized_passages <= budget.max_passages,
        "vectors exceed request budget",
    )
    _require(
        ledger.llm_calls <= budget.max_model_calls, "model calls exceed request budget"
    )
    _require(
        ledger.walltime_ms <= budget.max_walltime_seconds * 1000,
        "walltime exceeds request budget",
    )
    _require(
        not budget.allow_full_pdf and not policy.fetch.allow_pdf_fulltext,
        "PDF policy is enabled",
    )
    _require(
        policy.fetch.max_requests
        == policy.fetch.max_bytes_per_response
        == policy.fetch.max_total_bytes
        == 0,
        "article-body policy budget is nonzero",
    )
    _require(
        not policy.llm.enabled and policy.llm.max_calls == 0,
        "internal model policy is enabled",
    )

    _require(feedback.run_id == run_id, "tag feedback references another run")
    _require(
        feedback.graph_artifact.sha256 == inspiration_input.tag_graph_artifact.sha256,
        "tag feedback graph differs from frozen graph",
    )
    allowed_feedback_uris = {item.uri for item in stage.intermediate_artifacts} | {
        stage.cost_ledger_artifact.uri
    }
    _require(
        {item.artifact.uri for item in feedback.input_artifacts}.issubset(
            allowed_feedback_uris
        ),
        "tag feedback references an undeclared input",
    )
    _require(
        selection_value.get("schema_version") == "inspiration-selection-audit-v1",
        "selection audit schema drifted",
    )
    _require(
        selection_value.get("diversity_mode") == "MMR_ONLY",
        "top_k=1 release must report MMR_ONLY",
    )
    _require(
        selection_value.get("requested_top_k") == record.request.constraints.top_k,
        "selection top_k differs from request",
    )
    _require(
        selection_value.get("selected_candidate_count")
        == len(bundle.selected_candidates),
        "selection count differs from bundle",
    )
    _require(
        selection_value.get("selected_exact_duplicate_count") == 0,
        "selected exact duplicates are nonzero",
    )
    _require(
        selection_value.get("selected_strict_duplicate_count") == 0,
        "selected strict duplicates are nonzero",
    )

    _require(
        result.bundle.scientific_conclusion is False,
        "Gateway result promotes a scientific conclusion",
    )
    _require(
        all(
            item.target_property_status == "UNKNOWN"
            for item in result.bundle.selected_candidates
        ),
        "target property is not UNKNOWN",
    )
    _require(
        all(
            item.structural_status == "STRUCTURE_VALID"
            for item in result.bundle.selected_candidates
        ),
        "selected structure is not STRUCTURE_VALID",
    )
    _require(
        all(item.scientific_conclusion is False for item in bundle.selected_candidates),
        "bundle candidate promotes a scientific conclusion",
    )
    result_candidate_ids = tuple(
        item.candidate_id for item in result.bundle.selected_candidates
    )
    bundle_candidate_ids = tuple(
        item.candidate_id for item in bundle.selected_candidates
    )
    _require(
        result_candidate_ids == bundle_candidate_ids,
        "Gateway and Artifact candidate identities disagree",
    )
    evidence_by_id = {item.evidence_card_id: item for item in evidence}
    passage_by_id = {item.passage_id: item for item in passages}
    bundle_by_id = {item.candidate_id: item for item in bundle.selected_candidates}
    for candidate in result.bundle.selected_candidates:
        authoritative_candidate = bundle_by_id[candidate.candidate_id]
        candidate_passage_ids = tuple(
            sorted(
                {
                    passage_id
                    for evidence_id in authoritative_candidate.evidence_card_ids
                    for passage_id in evidence_by_id[evidence_id].passage_ids
                }
            )
        )
        candidate_document_ids = tuple(
            sorted({passage_by_id[item].document_id for item in candidate_passage_ids})
        )
        _require(
            candidate.evidence_passage_ids == candidate_passage_ids,
            "Gateway candidate passage lineage differs from Artifacts",
        )
        _require(
            candidate.evidence_document_ids == candidate_document_ids,
            "Gateway candidate document lineage differs from Artifacts",
        )
    if expectations.require_nonempty_result:
        _require(
            bundle.outcome is InspirationOutcome.SUCCEEDED,
            "release did not produce a successful bundle",
        )
        _require(bool(bundle.selected_candidates), "release result is empty")
        _require(bool(bridges), "release produced no BridgePacket")

    projected = result.cost_ledger
    _require(
        projected.search_requests == ledger.search_requests,
        "Gateway/Gateway-run search counts disagree",
    )
    _require(
        projected.search_response_bytes == ledger.search_response_bytes,
        "Gateway/Gateway-run byte counts disagree",
    )
    _require(
        projected.fetched_documents == 0, "Gateway projection reports fetched documents"
    )
    _require(
        projected.extracted_passages == ledger.extracted_passages,
        "Gateway passage projection disagrees",
    )
    _require(
        projected.vectorized_passages == ledger.vectorized_passages,
        "Gateway vector projection disagrees",
    )
    _require(
        projected.model_calls == projected.input_tokens == projected.output_tokens == 0,
        "Gateway model projection is nonzero",
    )
    _require(
        projected.walltime_ms == ledger.walltime_ms,
        "Gateway walltime projection disagrees",
    )
    if isinstance(record.state, PartialStateV1):
        _require(
            record.state.warnings
            == HermesFixtureProjector._bounded_warnings(stage.warnings),
            "terminal warnings do not match the bounded stage projection",
        )
    else:
        _require(
            not stage.warnings, "SUCCEEDED terminal state cannot hide stage warnings"
        )
    _require(
        isinstance(record.state, PartialStateV1) == bool(stage.warnings),
        "terminal status does not match projected warning presence",
    )

    complete_stage_files = {
        path.relative_to(project_root).as_posix()
        for path in stage_root.rglob("*")
        if path.is_file()
    }
    declared_stage_files = {
        stage_result_path.relative_to(project_root).as_posix(),
        *(
            PurePosixPath(pointer.uri.removeprefix("artifact://")).as_posix()
            for pointer in declared
        ),
    }
    _require(
        complete_stage_files == declared_stage_files,
        "stage directory contains an orphan or undeclared file",
    )
    complete_stage_directories = {
        path.relative_to(project_root).as_posix()
        for path in stage_root.rglob("*")
        if path.is_dir()
    }
    declared_stage_directories: set[str] = set()
    for filename in declared_stage_files:
        parent = PurePosixPath(filename).parent
        while parent != stage_relative:
            _require(
                stage_relative in (parent, *parent.parents),
                "declared stage file escapes its stage directory",
            )
            declared_stage_directories.add(parent.as_posix())
            parent = parent.parent
    _require(
        complete_stage_directories == declared_stage_directories,
        "stage directory contains an orphan or undeclared directory",
    )

    # Rebuild the exact pre-execution approval interaction only after the
    # approved manifest has been reproduced from current component snapshots.
    expected_interaction_id = (
        "interaction-"
        + _sha256(
            gateway_canonical_json_bytes(
                {
                    "kind": "requirement-freeze",
                    "request_sha256": expectations.execution_manifest_sha256,
                    "run_id": run_id,
                }
            )
        )[:24]
    )
    _require(
        expectations.interaction_id == expected_interaction_id,
        "approval interaction ID does not recompute",
    )
    prompt = requirement_freeze_prompt(
        request=record.request,
        prepared=prepared,
    )
    expected_interaction = ApprovalInteractionV1(
        interaction_id=expectations.interaction_id,
        approval_kind="requirement_freeze",
        prompt=prompt,
        input_sha256=expectations.execution_manifest_sha256,
        execution_manifest_sha256=expectations.execution_manifest_sha256,
    )
    interaction_sha256 = _sha256(gateway_canonical_json_bytes(expected_interaction))
    _require(
        interaction_sha256 == expectations.interaction_sha256,
        "approval interaction hash does not reconstruct",
    )

    return {
        "bridge_packets": len(bridges),
        "bundle_sha256": stage.bundle_artifact.sha256,
        "candidates": len(bundle.selected_candidates),
        "cost_ledger_sha256": stage.cost_ledger_artifact.sha256,
        "evidence_cards": len(evidence),
        "execution_manifest_sha256": current_execution_manifest_sha256,
        "fetch_requests": ledger.fetch_requests,
        "interaction_sha256": interaction_sha256,
        "model_calls": ledger.llm_calls,
        "passages": len(passages),
        "physical_search_attempts": len(search_attempts),
        "raw_search_bytes": ledger.search_response_bytes,
        "report_sha256": stage.report_artifact.sha256,
        "stage_result_sha256": _sha256(stage_result_bytes),
        "vectors": len(vectors),
    }


def verify_completed_run(
    *,
    settings: GatewayServerSettings,
    run_id: str,
    expectations: VerificationExpectations,
) -> dict[str, Any]:
    """Verify one completed release workspace without mutating it."""

    project_root = resolve_hermes_project_root(settings, create=False)
    before = _snapshot_tree(project_root)
    try:
        record, result, grant = _database_state(project_root, run_id, expectations)
        artifacts = _verify_artifacts(project_root, record, result, expectations)
        summary = {
            "artifact_closure_verified": True,
            "gateway_result_sha256": gateway_result_sha256(result),
            "grant": grant,
            "no_workspace_writes": True,
            "request_sha256": record.request_sha256,
            "revision": record.revision,
            "run_id": run_id,
            "schema_version": "materials-inspiration-completed-run-verification-v1",
            "status": record.state.status,
            **artifacts,
        }
    finally:
        after = _snapshot_tree(project_root)
        _require(before == after, "verifier changed the project workspace")
    return summary


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Read-only full-closure verifier for one completed inspiration run."
    )
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--project", required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--expected-request-sha256", required=True)
    parser.add_argument("--expected-execution-manifest-sha256", required=True)
    parser.add_argument("--expected-interaction-id", required=True)
    parser.add_argument("--expected-interaction-sha256", required=True)
    parser.add_argument("--expected-action-sha256", required=True)
    parser.add_argument("--expected-confirmation-reference", required=True)
    parser.add_argument("--expected-revision", type=int, default=2)
    parser.add_argument("--allow-scientific-no-match", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = _parser()
    arguments = parser.parse_args(argv)
    try:
        summary = verify_completed_run(
            settings=GatewayServerSettings(
                workspace=arguments.workspace,
                project_id=arguments.project,
            ),
            run_id=arguments.run_id,
            expectations=VerificationExpectations(
                request_sha256=arguments.expected_request_sha256,
                execution_manifest_sha256=(
                    arguments.expected_execution_manifest_sha256
                ),
                interaction_id=arguments.expected_interaction_id,
                interaction_sha256=arguments.expected_interaction_sha256,
                action_sha256=arguments.expected_action_sha256,
                confirmation_reference=arguments.expected_confirmation_reference,
                expected_revision=arguments.expected_revision,
                require_nonempty_result=not arguments.allow_scientific_no_match,
            ),
        )
    except (
        CompletedRunVerificationError,
        KeyError,
        OSError,
        RuntimeError,
        sqlite3.Error,
        TypeError,
        ValidationError,
        ValueError,
    ) as exc:
        parser.exit(2, f"verification failed: {exc}\n")
    print(
        json.dumps(
            summary,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
