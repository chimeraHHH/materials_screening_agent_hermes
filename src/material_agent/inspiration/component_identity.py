"""Content-addressed identities for the complete inspiration execution path.

Approval must bind the implementation that will actually execute, not only a
hand-written description of that implementation.  This module therefore hashes
the byte content of the bounded inspiration source tree, its build/lock inputs,
and the source files for each scientific pipeline component.  Only repository-
relative POSIX paths enter a digest; machine-specific absolute paths never do.

The identities are intentionally recomputed at every approval start/action
boundary.  A deployment whose source or lock files drift after approval thus
produces a different execution manifest before the runner is invoked.
"""

from __future__ import annotations

import hashlib
import platform
import stat
import sys
import tomllib
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as package_version
from pathlib import Path
from types import MappingProxyType

from material_agent.inspiration.models import (
    ComponentSnapshotV1,
    canonical_sha256,
)

EXECUTION_IDENTITY_VERSION = "2"

# These roots are the complete source boundary used to prepare, authorize,
# execute, persist, and project a Materials Inspiration run.  The broad tree
# digest prevents a newly introduced helper in one of these packages from
# silently escaping approval identity.
_EXECUTION_SOURCE_ROOTS = (
    "src/material_agent/gateway",
    "src/material_agent/inspiration",
    "src/material_agent/integration",
    "src/material_agent/retrieval",
)
_EXECUTION_SOURCE_SUFFIXES = frozenset({".py", ".json", ".cif"})

# Build inputs are explicit and required.  requirements.lock is the production
# Python resolution; pyproject.toml binds the package/build metadata and version.
_BUILD_IDENTITY_PATHS = (
    "pyproject.toml",
    "requirements.lock",
)

# Each component digest is derived from real source bytes.  Specs embedded in
# those modules can still document behavior, but are never the sole identity.
EXECUTION_COMPONENT_SOURCE_PATHS = MappingProxyType(
    {
        "inspiration-search-implementation": (
            "src/material_agent/inspiration/search.py",
            "src/material_agent/inspiration/tag_graph.py",
        ),
        "inspiration-extraction-implementation": (
            "src/material_agent/inspiration/extractors.py",
            "src/material_agent/inspiration/fetch.py",
        ),
        "inspiration-passages-implementation": (
            "src/material_agent/inspiration/passages.py",
        ),
        "inspiration-vector-implementation": (
            "src/material_agent/inspiration/vectorizer.py",
        ),
        "inspiration-evidence-implementation": (
            "src/material_agent/inspiration/evidence.py",
            "src/material_agent/inspiration/validation.py",
        ),
        "inspiration-bridge-implementation": (
            "src/material_agent/inspiration/bridge.py",
            "src/material_agent/inspiration/tag_graph.py",
            "src/material_agent/inspiration/validation.py",
        ),
        "inspiration-transform-implementation": (
            "src/material_agent/inspiration/engine.py",
            "src/material_agent/inspiration/parent_catalog.py",
            "src/material_agent/inspiration/transformations.py",
            "src/material_agent/inspiration/validation.py",
            "src/material_agent/retrieval/models.py",
            "src/material_agent/retrieval/structures.py",
        ),
        "inspiration-identity-implementation": (
            "src/material_agent/inspiration/identity.py",
        ),
        "inspiration-selection-implementation": (
            "src/material_agent/inspiration/identity.py",
            "src/material_agent/inspiration/selection.py",
        ),
        "inspiration-report-implementation": (
            "src/material_agent/inspiration/feedback.py",
            "src/material_agent/inspiration/reporting.py",
        ),
        "inspiration-runner-implementation": (
            "src/material_agent/inspiration/runner.py",
            "src/material_agent/retrieval/storage.py",
        ),
        "inspiration-projector-implementation": (
            "src/material_agent/gateway/companion.py",
            "src/material_agent/gateway/models.py",
            "src/material_agent/integration/hermes_service.py",
        ),
        "inspiration-request-compiler-implementation": (
            "src/material_agent/integration/hermes_service.py",
            "src/material_agent/integration/request_compiler.py",
        ),
        "inspiration-contracts-implementation": (
            "src/material_agent/gateway/models.py",
            "src/material_agent/inspiration/models.py",
            "src/material_agent/inspiration/policy.py",
        ),
        "inspiration-feedback-implementation": (
            "src/material_agent/inspiration/feedback.py",
        ),
    }
)

REQUIRED_EXECUTION_IDENTITY_COMPONENT_IDS = frozenset(
    {
        "material-agent-build-identity",
        "material-agent-execution-source-tree",
        *EXECUTION_COMPONENT_SOURCE_PATHS,
    }
)

_MAX_IDENTITY_FILE_BYTES = 16 * 1024 * 1024


class ExecutionIdentityError(RuntimeError):
    """The deployed source/build identity cannot be read safely."""


def _default_project_root() -> Path:
    root = Path(__file__).resolve().parents[3]
    if not (root / "pyproject.toml").is_file():
        raise ExecutionIdentityError(
            "cannot locate pyproject.toml for the deployed inspiration source tree"
        )
    return root


def _safe_project_root(project_root: Path | None) -> Path:
    root = _default_project_root() if project_root is None else Path(project_root)
    if root.is_symlink():
        raise ExecutionIdentityError("execution identity project root cannot be a symlink")
    try:
        resolved = root.resolve(strict=True)
    except OSError as error:
        raise ExecutionIdentityError(
            "execution identity project root is unavailable"
        ) from error
    if not resolved.is_dir():
        raise ExecutionIdentityError("execution identity project root is not a directory")
    return resolved


def _read_stable_file(root: Path, relative_path: str) -> bytes:
    """Read one regular file while rejecting links and concurrent mutation."""

    candidate = root.joinpath(*relative_path.split("/"))
    try:
        relative = candidate.relative_to(root)
    except ValueError as error:
        raise ExecutionIdentityError("execution identity path escapes project root") from error
    if relative.as_posix() != relative_path or candidate.is_symlink():
        raise ExecutionIdentityError(
            f"execution identity path is not a canonical regular file: {relative_path}"
        )
    try:
        before = candidate.stat(follow_symlinks=False)
        if not stat.S_ISREG(before.st_mode):
            raise ExecutionIdentityError(
                f"execution identity path is not a regular file: {relative_path}"
            )
        if before.st_size > _MAX_IDENTITY_FILE_BYTES:
            raise ExecutionIdentityError(
                f"execution identity file exceeds the size limit: {relative_path}"
            )
        payload = candidate.read_bytes()
        after = candidate.stat(follow_symlinks=False)
    except OSError as error:
        raise ExecutionIdentityError(
            f"cannot read execution identity file: {relative_path}"
        ) from error
    stable_fields_before = (
        before.st_dev,
        before.st_ino,
        before.st_mode,
        before.st_size,
        before.st_mtime_ns,
    )
    stable_fields_after = (
        after.st_dev,
        after.st_ino,
        after.st_mode,
        after.st_size,
        after.st_mtime_ns,
    )
    if stable_fields_before != stable_fields_after or len(payload) != before.st_size:
        raise ExecutionIdentityError(
            f"execution identity file changed while being read: {relative_path}"
        )
    return payload


def _file_manifest(root: Path, relative_paths: tuple[str, ...]) -> dict[str, object]:
    if not relative_paths or relative_paths != tuple(sorted(set(relative_paths))):
        raise ExecutionIdentityError(
            "execution identity paths must be a non-empty sorted unique tuple"
        )
    files: dict[str, object] = {}
    for relative_path in relative_paths:
        payload = _read_stable_file(root, relative_path)
        files[relative_path] = {
            "sha256": hashlib.sha256(payload).hexdigest(),
            "size_bytes": len(payload),
        }
    return files


def _source_tree_paths(root: Path) -> tuple[str, ...]:
    paths: set[str] = set()
    for relative_root in _EXECUTION_SOURCE_ROOTS:
        source_root = root.joinpath(*relative_root.split("/"))
        if source_root.is_symlink() or not source_root.is_dir():
            raise ExecutionIdentityError(
                f"execution source root is unavailable: {relative_root}"
            )
        for candidate in source_root.rglob("*"):
            if candidate.is_file() and candidate.suffix in _EXECUTION_SOURCE_SUFFIXES:
                paths.add(candidate.relative_to(root).as_posix())
    if not paths:
        raise ExecutionIdentityError("execution source tree contains no source files")
    return tuple(sorted(paths))


def _project_version(root: Path) -> str:
    try:
        metadata = tomllib.loads(_read_stable_file(root, "pyproject.toml").decode())
        value = metadata["project"]["version"]
    except (KeyError, TypeError, UnicodeDecodeError, tomllib.TOMLDecodeError) as error:
        raise ExecutionIdentityError(
            "pyproject.toml does not expose a valid project version"
        ) from error
    if not isinstance(value, str) or not value.strip() or len(value) > 128:
        raise ExecutionIdentityError("project version is invalid")
    return value.strip()


def _runtime_versions(root: Path) -> dict[str, object]:
    """Bind and verify the interpreter plus every locked distribution."""

    try:
        lock_lines = _read_stable_file(root, "requirements.lock").decode().splitlines()
    except UnicodeDecodeError as error:
        raise ExecutionIdentityError("requirements.lock is not UTF-8") from error
    locked_versions: dict[str, str] = {}
    installed_versions: dict[str, str] = {}
    for raw_line in lock_lines:
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.count("==") != 1:
            raise ExecutionIdentityError(
                "requirements.lock must contain only exact name==version pins"
            )
        distribution, expected_version = (part.strip() for part in line.split("=="))
        normalized_distribution = distribution.casefold().replace("_", "-")
        if (
            not distribution
            or not expected_version
            or normalized_distribution in locked_versions
        ):
            raise ExecutionIdentityError(
                "requirements.lock contains an invalid or duplicate pin"
            )
        try:
            actual_version = package_version(distribution)
        except PackageNotFoundError:
            actual_version = "NOT-INSTALLED"
        locked_versions[normalized_distribution] = expected_version
        installed_versions[normalized_distribution] = actual_version

    if not locked_versions:
        raise ExecutionIdentityError("requirements.lock contains no distribution pins")
    return {
        "python": (
            f"{platform.python_implementation()}-"
            f"{sys.version_info.major}.{sys.version_info.minor}"
        ),
        "locked_distributions": dict(sorted(locked_versions.items())),
        "installed_distributions": dict(sorted(installed_versions.items())),
        "lock_matches_runtime": locked_versions == installed_versions,
    }


def execution_identity_snapshots(
    *,
    project_root: Path | None = None,
) -> tuple[ComponentSnapshotV1, ...]:
    """Return deterministic content identities for the approved execution path.

    The optional root exists for relocation/drift tests and build verification;
    it never enters a digest.  Production callers omit it and bind the deployed
    checkout that contains this module.
    """

    root = _safe_project_root(project_root)
    project_version = _project_version(root)
    runtime_versions = _runtime_versions(root)
    shared_version = f"{project_version}-identity-v{EXECUTION_IDENTITY_VERSION}"

    snapshots = [
        ComponentSnapshotV1(
            component_id="material-agent-build-identity",
            version=shared_version,
            implementation_sha256=canonical_sha256(
                {
                    "schema_version": "material-agent-build-identity-v2",
                    "files": _file_manifest(root, _BUILD_IDENTITY_PATHS),
                    "project_version": project_version,
                    "runtime_versions": runtime_versions,
                }
            ),
        ),
        ComponentSnapshotV1(
            component_id="material-agent-execution-source-tree",
            version=shared_version,
            implementation_sha256=canonical_sha256(
                {
                    "schema_version": "material-agent-execution-source-tree-v2",
                    "files": _file_manifest(root, _source_tree_paths(root)),
                }
            ),
        ),
    ]
    for component_id, relative_paths in EXECUTION_COMPONENT_SOURCE_PATHS.items():
        snapshots.append(
            ComponentSnapshotV1(
                component_id=component_id,
                version=shared_version,
                implementation_sha256=canonical_sha256(
                    {
                        "schema_version": "material-agent-source-component-v2",
                        "component_id": component_id,
                        "files": _file_manifest(root, relative_paths),
                    }
                ),
            )
        )

    ordered = tuple(sorted(snapshots, key=lambda item: item.component_id))
    actual_ids = {item.component_id for item in ordered}
    if actual_ids != REQUIRED_EXECUTION_IDENTITY_COMPONENT_IDS:
        raise ExecutionIdentityError(
            "execution identity component coverage is incomplete or ambiguous"
        )
    return ordered
