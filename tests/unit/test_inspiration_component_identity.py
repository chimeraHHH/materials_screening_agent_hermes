from __future__ import annotations

from pathlib import Path

import pytest

from material_agent.inspiration.component_identity import (
    EXECUTION_COMPONENT_SOURCE_PATHS,
    REQUIRED_EXECUTION_IDENTITY_COMPONENT_IDS,
    ExecutionIdentityError,
    execution_identity_snapshots,
)
from material_agent.inspiration.models import canonical_json_bytes, canonical_sha256


def _materialize_identity_tree(root: Path) -> None:
    (root / "pyproject.toml").parent.mkdir(parents=True, exist_ok=True)
    (root / "pyproject.toml").write_text(
        '[project]\nname = "material-screening-agent"\nversion = "9.8.7"\n',
        encoding="utf-8",
    )
    (root / "requirements.lock").write_text(
        "pydantic==2.12.5\npymatgen==2025.10.7\nspglib==2.7.0\n",
        encoding="utf-8",
    )
    source_paths = sorted(
        {
            relative_path
            for paths in EXECUTION_COMPONENT_SOURCE_PATHS.values()
            for relative_path in paths
        }
    )
    for relative_path in source_paths:
        path = root.joinpath(*relative_path.split("/"))
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"# frozen fixture: {relative_path}\n", encoding="utf-8")


def _by_id(root: Path) -> dict[str, str]:
    return {
        snapshot.component_id: snapshot.implementation_sha256
        for snapshot in execution_identity_snapshots(project_root=root)
    }


def test_deployed_execution_identity_is_complete_and_deterministic() -> None:
    first = execution_identity_snapshots()
    second = execution_identity_snapshots()

    assert first == second
    assert {item.component_id for item in first} == (
        REQUIRED_EXECUTION_IDENTITY_COMPONENT_IDS
    )
    assert tuple(item.component_id for item in first) == tuple(
        sorted(item.component_id for item in first)
    )


def test_execution_identity_is_relocatable_and_contains_no_absolute_path(
    tmp_path: Path,
) -> None:
    first_root = tmp_path / "machine-a" / "checkout"
    second_root = tmp_path / "different" / "machine-b" / "checkout"
    _materialize_identity_tree(first_root)
    _materialize_identity_tree(second_root)

    first = execution_identity_snapshots(project_root=first_root)
    second = execution_identity_snapshots(project_root=second_root)

    assert first == second
    serialized = canonical_json_bytes(first)
    assert str(first_root).encode() not in serialized
    assert str(second_root).encode() not in serialized


@pytest.mark.parametrize(
    "component_id",
    tuple(EXECUTION_COMPONENT_SOURCE_PATHS),
)
def test_each_execution_component_is_bound_to_real_source_bytes(
    tmp_path: Path,
    component_id: str,
) -> None:
    root = tmp_path / component_id
    _materialize_identity_tree(root)
    before = _by_id(root)
    before_manifest = canonical_sha256(execution_identity_snapshots(project_root=root))

    relative_path = EXECUTION_COMPONENT_SOURCE_PATHS[component_id][0]
    source_path = root.joinpath(*relative_path.split("/"))
    source_path.write_bytes(source_path.read_bytes() + b"# implementation drift\n")

    after = _by_id(root)
    after_manifest = canonical_sha256(execution_identity_snapshots(project_root=root))
    assert after[component_id] != before[component_id]
    assert after["material-agent-execution-source-tree"] != (
        before["material-agent-execution-source-tree"]
    )
    assert after_manifest != before_manifest


@pytest.mark.parametrize("relative_path", ("pyproject.toml", "requirements.lock"))
def test_build_or_lock_drift_changes_build_identity_and_manifest(
    tmp_path: Path,
    relative_path: str,
) -> None:
    root = tmp_path / relative_path.replace(".", "-")
    _materialize_identity_tree(root)
    before = _by_id(root)
    before_manifest = canonical_sha256(execution_identity_snapshots(project_root=root))

    path = root / relative_path
    path.write_bytes(path.read_bytes() + b"\n# build drift\n")

    after = _by_id(root)
    after_manifest = canonical_sha256(execution_identity_snapshots(project_root=root))
    assert after["material-agent-build-identity"] != (
        before["material-agent-build-identity"]
    )
    assert after_manifest != before_manifest


def test_execution_identity_fails_closed_when_required_source_is_missing(
    tmp_path: Path,
) -> None:
    _materialize_identity_tree(tmp_path)
    missing = EXECUTION_COMPONENT_SOURCE_PATHS[
        "inspiration-evidence-implementation"
    ][0]
    tmp_path.joinpath(*missing.split("/")).unlink()

    with pytest.raises(ExecutionIdentityError, match="cannot read execution identity"):
        execution_identity_snapshots(project_root=tmp_path)


def test_execution_identity_binds_actual_runtime_versions(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _materialize_identity_tree(tmp_path)
    before = _by_id(tmp_path)

    from material_agent.inspiration import component_identity

    original_version = component_identity.package_version
    monkeypatch.setattr(
        component_identity,
        "package_version",
        lambda distribution: (
            "2.12.5-runtime-drift"
            if distribution == "pydantic"
            else original_version(distribution)
        ),
    )
    after = _by_id(tmp_path)

    assert after["material-agent-build-identity"] != (
        before["material-agent-build-identity"]
    )
