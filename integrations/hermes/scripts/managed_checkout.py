"""Narrow repair helpers for the pinned, repository-managed Hermes checkout.

The Hermes web/TUI build runs npm inside the pinned checkout.  Some npm
versions rewrite lockfile metadata even when dependency resolution is
unchanged.  Production deployment may restore that generated drift, but it
must never clean source edits or staging-area state.
"""

from __future__ import annotations

import os
import subprocess
import tempfile
from pathlib import Path, PurePosixPath


class ManagedCheckoutError(RuntimeError):
    """The checkout cannot be repaired without risking user-authored state."""


def _git_bytes(root: Path, *arguments: str) -> bytes:
    try:
        return subprocess.check_output(
            ("git", *arguments),
            cwd=root,
            stderr=subprocess.DEVNULL,
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        raise ManagedCheckoutError("managed Hermes checkout inspection failed") from exc


def _status_entries(root: Path) -> tuple[tuple[str, str], ...]:
    raw = _git_bytes(root, "status", "--porcelain=v1", "-z", "--untracked-files=all")
    if not raw:
        return ()
    fields = raw.split(b"\0")
    if fields[-1] != b"":
        raise ManagedCheckoutError("managed Hermes checkout status is malformed")
    entries: list[tuple[str, str]] = []
    index = 0
    while index < len(fields) - 1:
        field = fields[index]
        if len(field) < 4 or field[2:3] != b" ":
            raise ManagedCheckoutError("managed Hermes checkout status is malformed")
        status = field[:2].decode("ascii", errors="strict")
        path = field[3:].decode("utf-8", errors="surrogateescape")
        entries.append((status, path))
        index += 1
        if "R" in status or "C" in status:
            # Porcelain -z emits the second path as a separate NUL field.
            if index >= len(fields) - 1:
                raise ManagedCheckoutError("managed Hermes checkout status is malformed")
            index += 1
    return tuple(entries)


def _safe_lockfile_path(root: Path, relative: str) -> Path:
    pure = PurePosixPath(relative)
    if pure.is_absolute() or ".." in pure.parts or pure.name != "package-lock.json":
        raise ManagedCheckoutError(
            "Hermes checkout contains changes beyond generated package-lock.json drift"
        )
    destination = root.joinpath(*pure.parts)
    try:
        resolved_parent = destination.parent.resolve(strict=True)
        resolved_root = root.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise ManagedCheckoutError("managed Hermes lockfile path is invalid") from exc
    if resolved_parent != resolved_root and resolved_root not in resolved_parent.parents:
        raise ManagedCheckoutError("managed Hermes lockfile escapes the checkout")
    if destination.is_symlink() or not destination.is_file():
        raise ManagedCheckoutError("managed Hermes lockfile is not a regular file")
    return destination


def repair_generated_package_locks(root: Path, *, expected_commit: str) -> tuple[str, ...]:
    """Restore only unstaged package-lock.json drift at the exact pinned commit.

    Staged changes, untracked files, deletions, renames, and all non-lockfile
    edits fail closed.  Blobs are restored atomically from ``expected_commit``.
    """

    if not (root / ".git").is_dir():
        raise ManagedCheckoutError("managed Hermes checkout is unavailable")
    head = _git_bytes(root, "rev-parse", "HEAD").decode("ascii").strip()
    if head != expected_commit:
        raise ManagedCheckoutError("Hermes checkout is not at the pinned commit")
    entries = _status_entries(root)
    if not entries:
        return ()
    if any(status != " M" for status, _path in entries):
        raise ManagedCheckoutError(
            "Hermes checkout contains staged, untracked, deleted, or renamed state"
        )

    destinations: list[tuple[str, Path]] = []
    for _status, relative in entries:
        destinations.append((relative, _safe_lockfile_path(root, relative)))

    for relative, destination in destinations:
        mode_line = _git_bytes(root, "ls-tree", expected_commit, "--", relative).split()
        if not mode_line or mode_line[0] != b"100644":
            raise ManagedCheckoutError("pinned Hermes lockfile mode is not regular")
        blob = _git_bytes(root, "show", f"{expected_commit}:{relative}")
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{destination.name}.",
            dir=destination.parent,
        )
        try:
            with os.fdopen(descriptor, "wb") as temporary:
                temporary.write(blob)
                temporary.flush()
                os.fsync(temporary.fileno())
            os.chmod(temporary_name, 0o644)
            os.replace(temporary_name, destination)
        finally:
            try:
                os.unlink(temporary_name)
            except FileNotFoundError:
                pass

    if _status_entries(root):
        raise ManagedCheckoutError("Hermes lockfile repair did not restore a clean checkout")
    return tuple(relative for relative, _destination in destinations)
