#!/usr/bin/env python3
"""Secret-safe operational primitives for the production Hermes deployment."""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import subprocess
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from time import time_ns
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


EVENT_SCHEMA_VERSION = "materials-inspiration-ops-event-v1"
PID_SCHEMA_VERSION = "materials-inspiration-process-set-v3"
METRICS_SCHEMA_VERSION = "materials-inspiration-metrics-v2"
QUEUE_SCHEMA_VERSION = "2"
MAX_EVENT_LOG_BYTES = 32_000_000
MAX_EVENT_LOG_GENERATIONS = 3
MAX_EVENT_TOTAL_BYTES = MAX_EVENT_LOG_BYTES * (MAX_EVENT_LOG_GENERATIONS + 1)
MAX_EVENT_LINES = 100_000
DEFAULT_MAX_READY_AGE_SECONDS = 120.0
DEFAULT_MAX_RUNNING_LEASE_AGE_SECONDS = 15.0
_SENSITIVE_KEY_PARTS = (
    "api_key",
    "authorization",
    "contact",
    "credential",
    "email",
    "password",
    "prompt",
    "secret",
    "token",
)
_TERMINAL_STATUSES = (
    "CANCELLED",
    "FAILED",
    "INTERACTION_REQUIRED",
    "PARTIAL",
    "RUNNING",
    "SUCCEEDED",
)
_QUEUE_STATUSES = ("FAILED", "READY", "RUNNING", "SUCCEEDED")
_QUEUE_TABLES = {
    "gateway_job_attempts",
    "gateway_job_events",
    "gateway_job_queue_metadata",
    "gateway_jobs",
}
_QUEUE_JOB_COLUMNS = {
    "attempt_count",
    "checkpoint_json",
    "checkpoint_sequence",
    "checkpoint_sha256",
    "created_at_ms",
    "failure_acknowledged_at_ms",
    "failure_acknowledged_by",
    "failure_acknowledgement_reason",
    "failure_code",
    "failure_message",
    "idempotency_key",
    "job_id",
    "job_kind",
    "lease_expires_at_ms",
    "lease_generation",
    "lease_owner",
    "lease_token",
    "payload_json",
    "payload_sha256",
    "priority",
    "queue_name",
    "result_json",
    "result_sha256",
    "status",
    "terminal_at_ms",
    "terminal_lease_owner",
    "terminal_lease_token",
    "updated_at_ms",
}
_QUEUE_EVENT_COLUMNS = {
    "actor",
    "details_json",
    "event_id",
    "event_type",
    "job_id",
    "lease_generation",
    "occurred_at_ms",
}
_QUEUE_ATTEMPT_COLUMNS = {
    "child_active_ms",
    "child_exit_code",
    "child_exited_at_ms",
    "child_signal",
    "child_started_at_ms",
    "claimed_at_ms",
    "clock_anomaly",
    "deadline_budget_ms",
    "duration_source",
    "ended_at_ms",
    "execution_started_at_ms",
    "job_id",
    "lease_generation",
    "lease_owner",
    "outcome",
    "queue_wait_ms",
    "recovery_ms",
    "termination_grace_ms",
    "timeout_phase",
    "worker_active_ms",
}
_QUEUE_INDEX_KEYS = {
    "gateway_jobs_claim_idx": (
        ("queue_name", 0),
        ("status", 0),
        ("priority", 1),
        ("created_at_ms", 0),
        ("job_id", 0),
    ),
    "gateway_job_events_job_idx": (("job_id", 0), ("event_id", 0)),
}
_QUEUE_SQL_TOKENS = {
    "gateway_jobs": (
        "UNIQUE(QUEUE_NAME,IDEMPOTENCY_KEY)",
        "STATUSIN('READY','RUNNING','SUCCEEDED','FAILED')",
        "STATUS='RUNNING'ANDLEASE_OWNERISNOTNULL",
    ),
    "gateway_job_events": (
        "REFERENCESGATEWAY_JOBS(JOB_ID)ONDELETERESTRICT",
    ),
    "gateway_job_attempts": (
        "PRIMARYKEY(JOB_ID,LEASE_GENERATION)",
        "REFERENCESGATEWAY_JOBS(JOB_ID)ONDELETERESTRICT",
        "CLOCK_ANOMALYIN(0,1)",
    ),
}
_LINUX_PROC_COMMAND_MAX_BYTES = 65_536
_LINUX_PROC_STAT_MAX_BYTES = 4_096
_LINUX_START_MARKER_PREFIX = "linux-proc-start-ticks:"


class ProductionOpsError(RuntimeError):
    """An operational state file or local process failed closed validation."""


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace(
        "+00:00", "Z"
    )


def _safe_path(path: Path, *, create_parent: bool) -> Path:
    resolved_parent = path.parent.resolve()
    if path.parent.is_symlink():
        raise ProductionOpsError("operational state directory cannot be a symlink")
    if create_parent:
        resolved_parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if not resolved_parent.is_dir():
        raise ProductionOpsError("operational state directory is unavailable")
    if path.is_symlink():
        raise ProductionOpsError("operational state file cannot be a symlink")
    if path.exists() and not path.is_file():
        raise ProductionOpsError("operational state path is not a regular file")
    return path


def fsync_directory(directory: Path) -> None:
    """Persist directory-entry changes after an atomic create or replace."""

    flags = os.O_RDONLY
    if hasattr(os, "O_DIRECTORY"):
        flags |= os.O_DIRECTORY
    descriptor = os.open(directory, flags)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def rotate_bounded_file(
    path: Path,
    *,
    max_bytes: int,
    generations: int,
) -> bool:
    """Rotate a regular mode-0600 file into bounded numbered generations."""

    if max_bytes <= 0 or generations <= 0:
        raise ValueError("rotation bounds must be positive")
    _safe_path(path, create_parent=True)
    if not path.exists() or path.stat().st_size <= max_bytes:
        return False
    rotated_paths = [
        path.with_name(f"{path.name}.{generation}")
        for generation in range(1, generations + 1)
    ]
    for rotated_path in rotated_paths:
        _safe_path(rotated_path, create_parent=False)
    for generation in range(generations, 1, -1):
        source = rotated_paths[generation - 2]
        destination = rotated_paths[generation - 1]
        if source.exists():
            os.replace(source, destination)
            os.chmod(destination, 0o600, follow_symlinks=False)
    os.replace(path, rotated_paths[0])
    os.chmod(rotated_paths[0], 0o600, follow_symlinks=False)
    fsync_directory(path.parent)
    return True


def sanitize(value: Any, *, key: str = "") -> Any:
    """Return a bounded JSON-safe value with sensitive fields removed."""

    lowered = key.casefold()
    if any(part in lowered for part in _SENSITIVE_KEY_PARTS):
        return "<redacted>"
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return value if len(value) <= 512 else value[:509] + "..."
    if isinstance(value, dict):
        return {
            str(item_key)[:128]: sanitize(item_value, key=str(item_key))
            for item_key, item_value in list(value.items())[:128]
        }
    if isinstance(value, (list, tuple)):
        return [sanitize(item) for item in value[:128]]
    return sanitize(str(value), key=key)


def append_event(
    path: Path,
    *,
    event: str,
    outcome: str,
    duration_ms: int = 0,
    details: dict[str, Any] | None = None,
) -> None:
    """Append one fsynced, mode-0600 JSONL event without logging secrets."""

    if not event or not outcome:
        raise ProductionOpsError("event and outcome are required")
    _safe_path(path, create_parent=True)
    payload = {
        "details": sanitize(details or {}),
        "duration_ms": max(0, int(duration_ms)),
        "event": event[:128],
        "outcome": outcome[:64],
        "schema_version": EVENT_SCHEMA_VERSION,
        "timestamp": utc_now(),
    }
    encoded = (
        json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        + "\n"
    ).encode("utf-8")
    if len(encoded) > 65_536:
        raise ProductionOpsError("operational event exceeds the bounded record size")
    rotate_bounded_file(
        path,
        max_bytes=MAX_EVENT_LOG_BYTES - len(encoded),
        generations=MAX_EVENT_LOG_GENERATIONS,
    )
    descriptor = os.open(path, os.O_APPEND | os.O_CREAT | os.O_WRONLY, 0o600)
    try:
        os.fchmod(descriptor, 0o600)
        os.write(descriptor, encoded)
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    fsync_directory(path.parent)


def read_events(path: Path) -> tuple[dict[str, Any], ...]:
    candidates = [
        path.with_name(f"{path.name}.{generation}")
        for generation in range(MAX_EVENT_LOG_GENERATIONS, 0, -1)
    ] + [path]
    existing = [candidate for candidate in candidates if candidate.exists()]
    if not existing:
        return ()
    total_bytes = 0
    for candidate in existing:
        _safe_path(candidate, create_parent=False)
        total_bytes += candidate.stat().st_size
    if total_bytes > MAX_EVENT_TOTAL_BYTES:
        raise ProductionOpsError("operational event logs exceed their read budget")
    rows: list[dict[str, Any]] = []
    for candidate in existing:
        for line in candidate.read_text(encoding="utf-8").splitlines():
            if len(rows) >= MAX_EVENT_LINES:
                raise ProductionOpsError("operational event logs exceed their row budget")
            try:
                value = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ProductionOpsError(
                    "operational event log contains invalid JSON"
                ) from exc
            if (
                not isinstance(value, dict)
                or value.get("schema_version") != EVENT_SCHEMA_VERSION
            ):
                raise ProductionOpsError(
                    "operational event log contains an unknown record"
                )
            rows.append(value)
    return tuple(rows)


def write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    _safe_path(path, create_parent=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    if temporary.exists() or temporary.is_symlink():
        raise ProductionOpsError("temporary operational path already exists")
    encoded = (
        json.dumps(
            sanitize(payload),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        + "\n"
    ).encode("utf-8")
    descriptor: int | None = None
    try:
        descriptor = os.open(
            temporary,
            os.O_CREAT | os.O_EXCL | os.O_WRONLY,
            0o600,
        )
        os.write(descriptor, encoded)
        os.fsync(descriptor)
        os.close(descriptor)
        descriptor = None
        os.replace(temporary, path)
        fsync_directory(path.parent)
    except Exception:
        if descriptor is not None:
            os.close(descriptor)
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass
        raise


def read_json(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    _safe_path(path, create_parent=False)
    if path.stat().st_size > 65_536:
        raise ProductionOpsError("operational JSON exceeds its read budget")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ProductionOpsError("operational JSON is invalid") from exc
    if not isinstance(value, dict):
        raise ProductionOpsError("operational JSON must be an object")
    return value


def _linux_start_marker_from_stat(payload: bytes) -> str | None:
    """Parse Linux ``/proc/<pid>/stat`` field 22 without wall-clock conversion."""

    if not isinstance(payload, bytes) or len(payload) > _LINUX_PROC_STAT_MAX_BYTES:
        return None
    closing_parenthesis = payload.rfind(b")")
    if closing_parenthesis < 0:
        return None
    fields = payload[closing_parenthesis + 1 :].split()
    if len(fields) <= 19:
        return None
    start_ticks = fields[19]
    if not start_ticks.isdigit() or int(start_ticks) <= 0:
        return None
    return _LINUX_START_MARKER_PREFIX + start_ticks.decode("ascii")


def _read_linux_start_marker(pid: int) -> str | None:
    try:
        with Path(f"/proc/{pid}/stat").open("rb", buffering=0) as stream:
            payload = stream.read(_LINUX_PROC_STAT_MAX_BYTES + 1)
    except OSError:
        return None
    return _linux_start_marker_from_stat(payload)


def _read_linux_command(pid: int) -> bytes | None:
    try:
        with Path(f"/proc/{pid}/cmdline").open("rb", buffering=0) as stream:
            command = stream.read(_LINUX_PROC_COMMAND_MAX_BYTES + 1)
    except OSError:
        return None
    if (
        not command
        or len(command) > _LINUX_PROC_COMMAND_MAX_BYTES
        or not command.rstrip(b"\0")
    ):
        return None
    return command


def _linux_process_identity(pid: int) -> dict[str, Any] | None:
    start_marker = _read_linux_start_marker(pid)
    if start_marker is None:
        return None
    command = _read_linux_command(pid)
    if (
        command is None
        or not command.rstrip(b"\0")
        or _read_linux_start_marker(pid) != start_marker
    ):
        return None
    return {
        "command_sha256": hashlib.sha256(command).hexdigest(),
        "pid": pid,
        "start_marker": start_marker,
    }


def _ps_process_identity(pid: int) -> dict[str, Any] | None:
    try:
        completed = subprocess.run(
            ("ps", "-p", str(pid), "-o", "lstart=", "-o", "command="),
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    line = completed.stdout.strip()
    if completed.returncode != 0 or not line:
        return None
    parts = line.split(None, 5)
    if len(parts) < 6:
        return None
    start_marker = " ".join(parts[:5])
    command = parts[5]
    return {
        "command_sha256": hashlib.sha256(command.encode("utf-8")).hexdigest(),
        "pid": pid,
        "start_marker": start_marker,
    }


def process_identity(pid: int) -> dict[str, Any] | None:
    """Return stable POSIX process identity fields without exposing environment."""

    if not isinstance(pid, int) or pid <= 1:
        return None
    if sys.platform.startswith("linux"):
        return _linux_process_identity(pid)
    return _ps_process_identity(pid)


def owned_process_alive(record: Any) -> bool:
    if not isinstance(record, dict):
        return False
    try:
        pid = int(record["pid"])
    except (KeyError, TypeError, ValueError):
        return False
    current = process_identity(pid)
    return bool(
        current
        and current["start_marker"] == record.get("start_marker")
        and current["command_sha256"] == record.get("command_sha256")
    )


def probe_http_json(url: str, *, timeout_seconds: float = 3.0) -> dict[str, Any]:
    started = datetime.now(timezone.utc)
    try:
        request = Request(url, headers={"Accept": "application/json"})
        with urlopen(request, timeout=timeout_seconds) as response:
            payload = response.read(65_537)
            status = int(response.status)
        if len(payload) > 65_536:
            raise ProductionOpsError("health response exceeds its byte budget")
        value = json.loads(payload)
        if not isinstance(value, dict):
            raise ProductionOpsError("health response must be a JSON object")
        ok = status == 200 and value.get("ok") is True
        error = None
    except (HTTPError, URLError, OSError, ValueError, ProductionOpsError):
        ok = False
        status = 0
        value = {}
        error = "unavailable"
    elapsed = datetime.now(timezone.utc) - started
    return {
        "duration_ms": max(0, int(elapsed.total_seconds() * 1_000)),
        "error": error,
        "ok": ok,
        "status_code": status,
        "value": value if ok else {},
    }


def gateway_database_snapshot(database_path: Path) -> dict[str, Any]:
    """Read run status counters and integrity from the Gateway DB in read-only mode."""

    counts = Counter({status: 0 for status in _TERMINAL_STATUSES})
    if not database_path.exists():
        return {"counts": dict(counts), "integrity": False, "present": False}
    _safe_path(database_path, create_parent=False)
    uri = f"file:{database_path.resolve().as_posix()}?mode=ro"
    try:
        connection = sqlite3.connect(uri, uri=True, timeout=2.0)
        try:
            connection.execute("PRAGMA query_only = ON")
            integrity = connection.execute("PRAGMA quick_check").fetchone()
            rows = connection.execute("SELECT record_json FROM gateway_runs").fetchall()
        finally:
            connection.close()
    except sqlite3.Error:
        return {"counts": dict(counts), "integrity": False, "present": True}
    malformed = 0
    for (raw_record,) in rows:
        try:
            record = json.loads(raw_record)
            status = record["state"]["status"]
        except (KeyError, TypeError, json.JSONDecodeError):
            malformed += 1
            continue
        if status in counts:
            counts[status] += 1
        else:
            malformed += 1
    return {
        "counts": dict(counts),
        "integrity": integrity == ("ok",) and malformed == 0,
        "malformed_rows": malformed,
        "present": True,
        "total_runs": sum(counts.values()),
    }


def _empty_queue_snapshot(*, present: bool) -> dict[str, Any]:
    return {
        "acknowledged_blocked_total": 0,
        "blocked_count": 0,
        "counts": {status: 0 for status in _QUEUE_STATUSES},
        "expired_running_count": 0,
        "failed_count": 0,
        "integrity": False,
        "oldest_ready_age_seconds": 0.0,
        "oldest_running_lease_age_seconds": 0.0,
        "present": present,
        "schema_valid": False,
        "total_jobs": 0,
    }


def gateway_queue_snapshot(
    database_path: Path,
    *,
    now_ms: int | None = None,
) -> dict[str, Any]:
    """Read bounded queue health from the approval/outbox SQLite database.

    The production queued service deliberately stores one-time grants and jobs
    in the same SQLite database so grant consumption and enqueue can share one
    transaction.  This reader is query-only and validates both SQLite and the
    expected queue schema before reporting the aggregate operational state.
    """

    if now_ms is None:
        now_ms = time_ns() // 1_000_000
    if not isinstance(now_ms, int) or now_ms < 0:
        raise ValueError("now_ms must be a non-negative integer")
    if not database_path.exists():
        return _empty_queue_snapshot(present=False)
    _safe_path(database_path, create_parent=False)
    uri = f"file:{database_path.resolve().as_posix()}?mode=ro"
    try:
        connection = sqlite3.connect(uri, uri=True, timeout=2.0)
        try:
            connection.execute("PRAGMA query_only = ON")
            quick_check = connection.execute("PRAGMA quick_check").fetchone()
            foreign_key_violation = connection.execute(
                "PRAGMA foreign_key_check"
            ).fetchone()
            metadata_rows = connection.execute(
                "SELECT key,value FROM gateway_job_queue_metadata ORDER BY key"
            ).fetchall()
            tables = {
                str(row[0])
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' "
                    "AND name LIKE 'gateway_job%'"
                ).fetchall()
            }
            job_columns = {
                str(row[1])
                for row in connection.execute(
                    "PRAGMA table_info(gateway_jobs)"
                ).fetchall()
            }
            attempt_columns = {
                str(row[1])
                for row in connection.execute(
                    "PRAGMA table_info(gateway_job_attempts)"
                ).fetchall()
            }
            event_columns = {
                str(row[1])
                for row in connection.execute(
                    "PRAGMA table_info(gateway_job_events)"
                ).fetchall()
            }
            indexes = {
                str(row[0])
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='index' "
                    "AND name IN ('gateway_jobs_claim_idx',"
                    "'gateway_job_events_job_idx')"
                ).fetchall()
            }
            index_keys = {
                name: tuple(
                    (str(row[2]), int(row[3]))
                    for row in connection.execute(
                        f"PRAGMA index_xinfo({name})"
                    ).fetchall()
                    if int(row[5]) == 1
                )
                for name in _QUEUE_INDEX_KEYS
            }
            table_sql = {
                str(row[0]): "".join(str(row[1] or "").upper().split())
                for row in connection.execute(
                    "SELECT name,sql FROM sqlite_master WHERE type='table' "
                    "AND name IN ('gateway_jobs','gateway_job_events',"
                    "'gateway_job_attempts')"
                ).fetchall()
            }
            aggregate = connection.execute(
                """
                SELECT
                    COUNT(*) AS total_jobs,
                    SUM(CASE WHEN status='FAILED' THEN 1 ELSE 0 END) AS failed_count,
                    SUM(CASE WHEN status='FAILED' AND failure_code='BLOCKED_MANUAL_RECOVERY'
                                  AND failure_acknowledged_at_ms IS NULL
                             THEN 1 ELSE 0 END) AS blocked_count,
                    SUM(CASE WHEN status='FAILED' AND failure_code='BLOCKED_MANUAL_RECOVERY'
                                  AND failure_acknowledged_at_ms IS NOT NULL
                             THEN 1 ELSE 0 END) AS acknowledged_blocked_total,
                    SUM(CASE WHEN status='RUNNING' AND lease_expires_at_ms<=?
                             THEN 1 ELSE 0 END) AS expired_running_count,
                    MIN(CASE WHEN status='READY' THEN created_at_ms END) AS oldest_ready_ms,
                    MIN(CASE WHEN status='RUNNING' THEN updated_at_ms END) AS oldest_running_update_ms,
                    SUM(CASE WHEN created_at_ms<0 OR updated_at_ms<created_at_ms
                                  OR updated_at_ms>? THEN 1 ELSE 0 END) AS invalid_time_count,
                    SUM(CASE WHEN status='RUNNING' AND
                                  (lease_expires_at_ms IS NULL OR lease_expires_at_ms<0
                                   OR lease_owner IS NULL OR lease_token IS NULL)
                             THEN 1 ELSE 0 END) AS invalid_lease_count,
                    SUM(CASE WHEN status='FAILED' AND
                                  (failure_code IS NULL OR failure_message IS NULL)
                             THEN 1 ELSE 0 END) AS invalid_failure_count,
                    SUM(CASE WHEN failure_acknowledged_at_ms IS NOT NULL AND
                                  (failure_acknowledged_at_ms<0 OR failure_acknowledged_at_ms>?
                                   OR status!='FAILED' OR failure_acknowledged_by IS NULL
                                   OR failure_acknowledgement_reason IS NULL)
                             THEN 1 ELSE 0 END) AS invalid_acknowledgement_count
                FROM gateway_jobs
                """,
                (now_ms, now_ms + 5_000, now_ms + 5_000),
            ).fetchone()
            status_rows = connection.execute(
                "SELECT status,COUNT(*) FROM gateway_jobs GROUP BY status"
            ).fetchall()
        finally:
            connection.close()
    except sqlite3.Error:
        return _empty_queue_snapshot(present=True)

    counts = {status: 0 for status in _QUEUE_STATUSES}
    unknown_status = False
    for status, count in status_rows:
        if status not in counts or not isinstance(count, int) or count < 0:
            unknown_status = True
            continue
        counts[status] = count
    schema_valid = bool(
        metadata_rows == [("schema_version", QUEUE_SCHEMA_VERSION)]
        and tables == _QUEUE_TABLES
        and job_columns == _QUEUE_JOB_COLUMNS
        and event_columns == _QUEUE_EVENT_COLUMNS
        and attempt_columns == _QUEUE_ATTEMPT_COLUMNS
        and indexes == set(_QUEUE_INDEX_KEYS)
        and index_keys == _QUEUE_INDEX_KEYS
        and set(table_sql) == set(_QUEUE_SQL_TOKENS)
        and all(
            token in table_sql[name]
            for name, tokens in _QUEUE_SQL_TOKENS.items()
            for token in tokens
        )
    )
    total_jobs = int(aggregate[0] or 0)
    failed_count = int(aggregate[1] or 0)
    blocked_count = int(aggregate[2] or 0)
    acknowledged_blocked_total = int(aggregate[3] or 0)
    expired_running_count = int(aggregate[4] or 0)
    oldest_ready_ms = aggregate[5]
    oldest_running_update_ms = aggregate[6]
    invalid_rows = (
        int(aggregate[7] or 0)
        + int(aggregate[8] or 0)
        + int(aggregate[9] or 0)
        + int(aggregate[10] or 0)
    )
    counts_valid = sum(counts.values()) == total_jobs
    timestamps_valid = invalid_rows == 0
    oldest_ready_age = (
        0.0
        if oldest_ready_ms is None
        else max(0.0, (now_ms - int(oldest_ready_ms)) / 1_000.0)
    )
    oldest_running_lease_age = (
        0.0
        if oldest_running_update_ms is None
        else max(0.0, (now_ms - int(oldest_running_update_ms)) / 1_000.0)
    )
    return {
        "acknowledged_blocked_total": acknowledged_blocked_total,
        "blocked_count": blocked_count,
        "counts": counts,
        "expired_running_count": expired_running_count,
        "failed_count": failed_count,
        "integrity": bool(
            quick_check == ("ok",)
            and foreign_key_violation is None
            and schema_valid
            and counts_valid
            and timestamps_valid
            and not unknown_status
        ),
        "oldest_ready_age_seconds": oldest_ready_age,
        "oldest_running_lease_age_seconds": oldest_running_lease_age,
        "present": True,
        "schema_valid": schema_valid,
        "total_jobs": total_jobs,
    }


def process_snapshot(record: Any) -> dict[str, Any]:
    """Project one PID ownership record without revealing its command line."""

    if not isinstance(record, dict):
        return {
            "identity_match": False,
            "pid": 0,
            "recorded_identity": None,
            "up": False,
        }
    try:
        pid = int(record["pid"])
        start_marker = str(record["start_marker"])
        command_sha256 = str(record["command_sha256"])
    except (KeyError, TypeError, ValueError):
        return {
            "identity_match": False,
            "pid": 0,
            "recorded_identity": None,
            "up": False,
        }
    identity_valid = bool(
        pid > 1
        and start_marker
        and len(start_marker) <= 128
        and len(command_sha256) == 64
        and all(character in "0123456789abcdef" for character in command_sha256)
    )
    up = bool(identity_valid and owned_process_alive(record))
    return {
        "identity_match": up,
        "pid": pid if identity_valid else 0,
        "recorded_identity": (
            {
                "command_sha256": command_sha256,
                "start_marker": start_marker,
            }
            if identity_valid
            else None
        ),
        "up": up,
    }


def operational_readiness(
    snapshot: dict[str, Any],
    *,
    max_ready_age_seconds: float = DEFAULT_MAX_READY_AGE_SECONDS,
    max_running_lease_age_seconds: float = DEFAULT_MAX_RUNNING_LEASE_AGE_SECONDS,
) -> dict[str, Any]:
    """Evaluate local fail-closed readiness and return explicit reason codes."""

    if max_ready_age_seconds <= 0 or max_running_lease_age_seconds <= 0:
        raise ValueError("queue readiness thresholds must be positive")
    queue = snapshot["queue"]
    reasons: list[str] = []
    if not snapshot["process_set_valid"]:
        reasons.append("PROCESS_SET_INVALID")
    if not snapshot["runtime_binding_match"]:
        reasons.append("RUNTIME_BINDING_MISMATCH")
    if not snapshot["dashboard"]["up"]:
        reasons.append("DASHBOARD_PROCESS_MISSING")
    if not snapshot["monitor"]["up"]:
        reasons.append("MONITOR_PROCESS_MISSING")
    if not snapshot["worker"]["up"]:
        reasons.append("WORKER_PROCESS_MISSING")
    if not snapshot["database"]["integrity"]:
        reasons.append("GATEWAY_DATABASE_INVALID")
    if not queue["integrity"]:
        reasons.append("QUEUE_DATABASE_INVALID")
    if queue["oldest_ready_age_seconds"] > max_ready_age_seconds:
        reasons.append("READY_BACKLOG_STALE")
    if queue["oldest_running_lease_age_seconds"] > max_running_lease_age_seconds:
        reasons.append("RUNNING_LEASE_STALE")
    if queue["expired_running_count"]:
        reasons.append("RUNNING_LEASE_EXPIRED")
    if queue["blocked_count"]:
        reasons.append("MANUAL_RECOVERY_BLOCKED")
    return {
        "max_ready_age_seconds": float(max_ready_age_seconds),
        "max_running_lease_age_seconds": float(max_running_lease_age_seconds),
        "ok": not reasons,
        "reasons": reasons,
    }


def metrics_snapshot(
    *,
    event_log: Path,
    gateway_database: Path,
    queue_database: Path,
    process_record: dict[str, Any] | None,
    expected_runtime_binding_sha256: str,
    now_ms: int | None = None,
) -> dict[str, Any]:
    events = read_events(event_log)
    event_counts: Counter[tuple[str, str]] = Counter()
    last_preflight_success = ""
    for row in events:
        event_name = str(row.get("event") or "unknown")[:128]
        outcome = str(row.get("outcome") or "unknown")[:64]
        event_counts[(event_name, outcome)] += 1
        if event_name == "preflight" and outcome == "success":
            last_preflight_success = str(row.get("timestamp") or "")
    processes = (process_record or {}).get("processes", {})
    dashboard = process_snapshot(processes.get("dashboard"))
    monitor = process_snapshot(processes.get("monitor"))
    worker = process_snapshot(processes.get("worker"))
    database = gateway_database_snapshot(gateway_database)
    queue = gateway_queue_snapshot(queue_database, now_ms=now_ms)
    process_set_valid = bool(
        isinstance(process_record, dict)
        and process_record.get("schema_version") == PID_SCHEMA_VERSION
    )
    runtime_binding_match = bool(
        process_set_valid
        and process_record.get("runtime_binding_sha256")
        == expected_runtime_binding_sha256
    )
    snapshot = {
        "dashboard": dashboard,
        "database": database,
        "event_counts": [
            {"event": event, "outcome": outcome, "value": value}
            for (event, outcome), value in sorted(event_counts.items())
        ],
        "last_preflight_success": last_preflight_success,
        "monitor": monitor,
        "process_set_valid": process_set_valid,
        "queue": queue,
        "runtime_binding_match": runtime_binding_match,
        "schema_version": METRICS_SCHEMA_VERSION,
        "worker": worker,
    }
    snapshot["readiness"] = operational_readiness(snapshot)
    return snapshot


def _prometheus_label(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


def prometheus_metrics(snapshot: dict[str, Any]) -> str:
    lines = [
        "# HELP materials_inspiration_dashboard_up Whether the owned Hermes dashboard process is alive.",
        "# TYPE materials_inspiration_dashboard_up gauge",
        f"materials_inspiration_dashboard_up {1 if snapshot['dashboard']['up'] else 0}",
        "# HELP materials_inspiration_monitor_up Whether the owned operations sidecar is alive.",
        "# TYPE materials_inspiration_monitor_up gauge",
        f"materials_inspiration_monitor_up {1 if snapshot['monitor']['up'] else 0}",
        "# HELP materials_inspiration_worker_up Whether the owned queued Gateway worker is alive with matching process identity.",
        "# TYPE materials_inspiration_worker_up gauge",
        f"materials_inspiration_worker_up {1 if snapshot['worker']['up'] else 0}",
        "# HELP materials_inspiration_worker_pid Owned queued Gateway worker PID, or zero when unavailable.",
        "# TYPE materials_inspiration_worker_pid gauge",
        f"materials_inspiration_worker_pid {int(snapshot['worker']['pid'])}",
        "# HELP materials_inspiration_worker_identity_match Whether the worker PID start marker and command hash match the ownership record.",
        "# TYPE materials_inspiration_worker_identity_match gauge",
        "materials_inspiration_worker_identity_match "
        f"{1 if snapshot['worker']['identity_match'] else 0}",
        "# HELP materials_inspiration_runtime_binding_match Whether process ownership is bound to the expected workspace, project, databases, and Artifact root.",
        "# TYPE materials_inspiration_runtime_binding_match gauge",
        "materials_inspiration_runtime_binding_match "
        f"{1 if snapshot['runtime_binding_match'] else 0}",
        "# HELP materials_inspiration_gateway_database_up Whether the Gateway DB passes quick_check.",
        "# TYPE materials_inspiration_gateway_database_up gauge",
        "materials_inspiration_gateway_database_up "
        f"{1 if snapshot['database']['integrity'] else 0}",
        "# HELP materials_inspiration_queue_database_up Whether the approval/outbox DB passes quick_check and queue schema validation.",
        "# TYPE materials_inspiration_queue_database_up gauge",
        "materials_inspiration_queue_database_up "
        f"{1 if snapshot['queue']['integrity'] else 0}",
        "# HELP materials_inspiration_queue_jobs Current durable Gateway jobs by state.",
        "# TYPE materials_inspiration_queue_jobs gauge",
    ]
    for status, value in sorted(snapshot["queue"]["counts"].items()):
        lines.append(
            'materials_inspiration_queue_jobs{status="'
            + _prometheus_label(status)
            + f'"}} {int(value)}'
        )
    lines.extend(
        [
        "# HELP materials_inspiration_queue_oldest_ready_age_seconds Age of the oldest READY action job.",
        "# TYPE materials_inspiration_queue_oldest_ready_age_seconds gauge",
        "materials_inspiration_queue_oldest_ready_age_seconds "
        f"{float(snapshot['queue']['oldest_ready_age_seconds']):.3f}",
        "# HELP materials_inspiration_queue_oldest_running_lease_age_seconds Age since the oldest RUNNING lease update or heartbeat.",
        "# TYPE materials_inspiration_queue_oldest_running_lease_age_seconds gauge",
        "materials_inspiration_queue_oldest_running_lease_age_seconds "
        f"{float(snapshot['queue']['oldest_running_lease_age_seconds']):.3f}",
        "# HELP materials_inspiration_queue_failed_jobs Number of terminal failed jobs.",
        "# TYPE materials_inspiration_queue_failed_jobs gauge",
        f"materials_inspiration_queue_failed_jobs {int(snapshot['queue']['failed_count'])}",
        "# HELP materials_inspiration_queue_blocked_jobs Number of jobs requiring manual recovery.",
        "# TYPE materials_inspiration_queue_blocked_jobs gauge",
        f"materials_inspiration_queue_blocked_jobs {int(snapshot['queue']['blocked_count'])}",
        "# HELP materials_inspiration_queue_acknowledged_blocked_total Number of retained manual-recovery failures acknowledged by an operator.",
        "# TYPE materials_inspiration_queue_acknowledged_blocked_total gauge",
        "materials_inspiration_queue_acknowledged_blocked_total "
        f"{int(snapshot['queue']['acknowledged_blocked_total'])}",
        "# HELP materials_inspiration_queue_expired_running_leases Number of RUNNING jobs with expired leases.",
        "# TYPE materials_inspiration_queue_expired_running_leases gauge",
        "materials_inspiration_queue_expired_running_leases "
        f"{int(snapshot['queue']['expired_running_count'])}",
        "# HELP materials_inspiration_local_ready Whether all local production readiness gates pass.",
        "# TYPE materials_inspiration_local_ready gauge",
        f"materials_inspiration_local_ready {1 if snapshot['readiness']['ok'] else 0}",
        "# HELP materials_inspiration_runs_total Current Gateway runs by state.",
        "# TYPE materials_inspiration_runs_total gauge",
        ]
    )
    for status, value in sorted(snapshot["database"]["counts"].items()):
        lines.append(
            'materials_inspiration_runs_total{status="'
            + _prometheus_label(status)
            + f'"}} {int(value)}'
        )
    lines.extend(
        (
            "# HELP materials_inspiration_ops_events_total Durable operational events by type and outcome.",
            "# TYPE materials_inspiration_ops_events_total counter",
        )
    )
    for row in snapshot["event_counts"]:
        lines.append(
            'materials_inspiration_ops_events_total{event="'
            + _prometheus_label(row["event"])
            + '",outcome="'
            + _prometheus_label(row["outcome"])
            + f'"}} {int(row["value"])}'
        )
    lines.append("")
    return "\n".join(lines)
