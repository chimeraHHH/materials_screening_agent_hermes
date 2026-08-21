"""Deterministic leakage graph closure for the flat-band benchmark.

The benchmark records several non-independent relationships for every case.  A
single user-selected label is not an acceptable resampling identity: all frozen
relationships are joined into one graph and its connected components become the
only split/bootstrap/randomization clusters.

Formal V3 keeps the ten-value broad mechanism taxonomy out of the independence
graph and instead uses a sealed, scientifically inspectable mechanism-lineage
registry with exact case assignments.  V2 structure grouping run/assignment
records remain internal, content-addressed provenance that can be replayed
against frozen implementation/configuration identities; they are not an
external attestation or trusted scientific signature.  Article/source grouping
conservatively joins an exact ``(source_id, source_record_id)``.  When the same
DOI occurs under multiple providers and no canonical-work registry exists, V3
fails closed rather than claiming that cross-source leakage is resolved.
"""

from __future__ import annotations

import json
import re
import unicodedata
from collections import defaultdict, deque
from collections.abc import Iterable
from datetime import datetime
from enum import StrEnum
from fractions import Fraction
from math import gcd, lcm
from typing import Annotated, Literal
from urllib.parse import unquote, urlparse

from pydantic import Field, field_validator, model_validator

from material_agent.inspiration.models import (
    Identifier,
    Sha256,
    ShortText,
    StrictModel,
    canonical_json_bytes,
    canonical_sha256,
    deterministic_id,
)
from material_agent.research.flatband_contracts import (
    BenchmarkSplit,
    BenchmarkSplitManifestV1,
    BenchmarkSplitManifestV2,
    FlatBandBenchmarkCaseV1,
    MechanismFamily,
    OodHoldoutAxis,
    SourceRecordRefV1,
    SplitManifestKind,
    _require_rfc3339,
    mechanism_holdout_taxonomy_group_id,
)


class LeakageAxis(StrEnum):
    COMPOSITION_FAMILY = "COMPOSITION_FAMILY"
    STRUCTURE_PROTOTYPE = "STRUCTURE_PROTOTYPE"
    STRUCTURE_FINGERPRINT = "STRUCTURE_FINGERPRINT"
    ARTICLE_OR_SOURCE_FAMILY = "ARTICLE_OR_SOURCE_FAMILY"
    MECHANISM_FAMILY = "MECHANISM_FAMILY"


REQUIRED_LEAKAGE_AXES = frozenset(LeakageAxis)


class LeakageMembershipV1(StrictModel):
    case_id: Identifier
    case_sha256: Sha256
    axis: LeakageAxis
    group_id: Identifier
    provenance_sha256: Sha256


class LeakageComponentV1(StrictModel):
    component_id: Identifier
    case_ids: Annotated[tuple[Identifier, ...], Field(min_length=1, max_length=120)]

    @model_validator(mode="after")
    def validate_component(self) -> LeakageComponentV1:
        if self.case_ids != tuple(sorted(set(self.case_ids))):
            raise ValueError("component case IDs must be sorted and unique")
        expected = deterministic_id("leakage-component", {"case_ids": self.case_ids})
        if self.component_id != expected:
            raise ValueError("leakage component ID does not match its cases")
        return self


class LeakageComponentReleaseV1(StrictModel):
    schema_version: Literal["flatband-leakage-component-release-v1"] = (
        "flatband-leakage-component-release-v1"
    )
    release_id: Identifier
    release_sha256: Sha256
    split_manifest_id: Identifier
    split_manifest_sha256: Sha256
    construction_policy_sha256: Sha256
    memberships: Annotated[
        tuple[LeakageMembershipV1, ...], Field(min_length=1, max_length=20_000)
    ]
    components: Annotated[
        tuple[LeakageComponentV1, ...], Field(min_length=1, max_length=120)
    ]
    created_at: Annotated[str, Field(min_length=20, max_length=40)]
    scientific_conclusion: Literal[False] = False

    @field_validator("created_at")
    @classmethod
    def validate_created_at(cls, value: str) -> str:
        return _require_rfc3339(value)

    @model_validator(mode="after")
    def validate_release(self) -> LeakageComponentReleaseV1:
        membership_keys = tuple(
            (item.case_id, item.axis.value, item.group_id, item.provenance_sha256)
            for item in self.memberships
        )
        if membership_keys != tuple(sorted(set(membership_keys))):
            raise ValueError("leakage memberships must be key-sorted and unique")
        component_ids = tuple(item.component_id for item in self.components)
        if component_ids != tuple(sorted(set(component_ids))):
            raise ValueError("leakage components must be component-ID sorted and unique")
        assigned = tuple(
            case_id for component in self.components for case_id in component.case_ids
        )
        if len(assigned) != len(set(assigned)):
            raise ValueError("a case appears in more than one leakage component")
        membership_cases = {item.case_id for item in self.memberships}
        if set(assigned) != membership_cases:
            raise ValueError("leakage components do not exactly cover membership cases")
        expected_components = _components_from_memberships(self.memberships)
        if self.components != expected_components:
            raise ValueError("persisted leakage components do not replay from memberships")
        semantic = self.model_dump(
            mode="python", exclude={"release_id", "release_sha256"}
        )
        expected_sha256 = canonical_sha256(semantic)
        if self.release_sha256 != expected_sha256:
            raise ValueError("leakage release SHA-256 does not match content")
        if self.release_id != deterministic_id(
            "leakage-release", {"release_sha256": expected_sha256}
        ):
            raise ValueError("leakage release ID does not match its SHA-256")
        return self


def _components_from_memberships(
    memberships: Iterable[LeakageMembershipV1],
) -> tuple[LeakageComponentV1, ...]:
    by_group: dict[tuple[LeakageAxis, str], set[str]] = defaultdict(set)
    all_cases: set[str] = set()
    for membership in memberships:
        all_cases.add(membership.case_id)
        by_group[(membership.axis, membership.group_id)].add(membership.case_id)
    neighbours: dict[str, set[str]] = {case_id: set() for case_id in all_cases}
    for grouped_cases in by_group.values():
        for case_id in grouped_cases:
            neighbours[case_id].update(grouped_cases - {case_id})

    remaining = set(all_cases)
    components: list[LeakageComponentV1] = []
    while remaining:
        root = min(remaining)
        queue = deque((root,))
        connected: set[str] = set()
        while queue:
            case_id = queue.popleft()
            if case_id in connected:
                continue
            connected.add(case_id)
            queue.extend(sorted(neighbours[case_id] - connected))
        remaining.difference_update(connected)
        case_ids = tuple(sorted(connected))
        components.append(
            LeakageComponentV1(
                component_id=deterministic_id(
                    "leakage-component", {"case_ids": case_ids}
                ),
                case_ids=case_ids,
            )
        )
    return tuple(sorted(components, key=lambda item: item.component_id))


def build_leakage_component_release(
    *,
    split_manifest: BenchmarkSplitManifestV1,
    memberships: Iterable[LeakageMembershipV1],
    construction_policy_sha256: str,
    created_at: str,
) -> LeakageComponentReleaseV1:
    """Build and fully replay a content-addressed component release."""

    split_manifest = BenchmarkSplitManifestV1.model_validate(
        split_manifest.model_dump(mode="python", round_trip=True)
    )
    ordered = tuple(
        sorted(
            (
                LeakageMembershipV1.model_validate(
                    item.model_dump(mode="python", round_trip=True)
                )
                for item in memberships
            ),
            key=lambda item: (
                item.case_id,
                item.axis.value,
                item.group_id,
                item.provenance_sha256,
            ),
        )
    )
    components = _components_from_memberships(ordered)
    values = {
        "split_manifest_id": split_manifest.manifest_id,
        "split_manifest_sha256": split_manifest.manifest_sha256,
        "construction_policy_sha256": construction_policy_sha256,
        "memberships": ordered,
        "components": components,
        "created_at": created_at,
    }
    semantic = LeakageComponentReleaseV1.model_construct(**values).model_dump(
        mode="python", exclude={"release_id", "release_sha256"}
    )
    digest = canonical_sha256(semantic)
    release = LeakageComponentReleaseV1.model_validate(
        {
            **values,
            "release_sha256": digest,
            "release_id": deterministic_id(
                "leakage-release", {"release_sha256": digest}
            ),
        }
    )
    assert_leakage_split_closure(split_manifest=split_manifest, release=release)
    return release


def assert_leakage_split_closure(
    *,
    split_manifest: BenchmarkSplitManifestV1,
    release: LeakageComponentReleaseV1,
) -> None:
    """Verify exact cases, all five axes, split isolation, OOD, and cluster counts."""

    split_manifest = BenchmarkSplitManifestV1.model_validate(
        split_manifest.model_dump(mode="python", round_trip=True)
    )
    release = LeakageComponentReleaseV1.model_validate(
        release.model_dump(mode="python", round_trip=True)
    )
    if (
        release.split_manifest_id,
        release.split_manifest_sha256,
    ) != (split_manifest.manifest_id, split_manifest.manifest_sha256):
        raise ValueError("leakage release references a different split manifest")

    expected_cases = {
        case.case_id: (case.case_sha256, case.split, set(case.leakage_group_ids))
        for case in split_manifest.cases
    }
    observed_cases = {item.case_id for item in release.memberships}
    if observed_cases != set(expected_cases):
        raise ValueError("leakage release does not exactly cover split cases")

    axes_by_case: dict[str, set[LeakageAxis]] = defaultdict(set)
    groups_by_case: dict[str, set[str]] = defaultdict(set)
    for membership in release.memberships:
        expected_sha256, _, _ = expected_cases[membership.case_id]
        if membership.case_sha256 != expected_sha256:
            raise ValueError("leakage membership case SHA-256 differs from split")
        axes_by_case[membership.case_id].add(membership.axis)
        groups_by_case[membership.case_id].add(membership.group_id)
    for case_id, (_, _, expected_groups) in expected_cases.items():
        if axes_by_case[case_id] != REQUIRED_LEAKAGE_AXES:
            raise ValueError("every case must declare all required leakage axes")
        if groups_by_case[case_id] != expected_groups:
            raise ValueError("split leakage groups differ from typed memberships")

    component_split: dict[str, BenchmarkSplit] = {}
    for component in release.components:
        splits = {expected_cases[case_id][1] for case_id in component.case_ids}
        if len(splits) != 1:
            raise ValueError("a leakage connected component crosses benchmark splits")
        split = next(iter(splits))
        component_split[component.component_id] = split

    counts: dict[BenchmarkSplit, int] = defaultdict(int)
    for split in component_split.values():
        counts[split] += 1
    if split_manifest.manifest_kind in {
        SplitManifestKind.PILOT_R1,
        SplitManifestKind.PILOT_R2,
    }:
        pilot_split = (
            BenchmarkSplit.PILOT_R1
            if split_manifest.manifest_kind is SplitManifestKind.PILOT_R1
            else BenchmarkSplit.PILOT_R2
        )
        if counts[pilot_split] < 10:
            raise ValueError("pilot requires at least ten independent components")
    else:
        required = {
            BenchmarkSplit.DEVELOPMENT: 20,
            BenchmarkSplit.LOCKED_IID: 10,
            BenchmarkSplit.LOCKED_OOD: 10,
        }
        if any(counts[split] < minimum for split, minimum in required.items()):
            raise ValueError("main splits do not meet independent-component minima")

    holdouts = {
        (LeakageAxis(item.axis.value), item.group_id)
        for item in split_manifest.ood_holdout_families
    }
    if split_manifest.manifest_kind is SplitManifestKind.MAIN_120:
        for case_id, (_, split, _) in expected_cases.items():
            typed_groups = {
                (item.axis, item.group_id)
                for item in release.memberships
                if item.case_id == case_id
            }
            hits = typed_groups & holdouts
            if split is BenchmarkSplit.LOCKED_OOD and not hits:
                raise ValueError("every OOD case must hit a frozen holdout family")
            if split is not BenchmarkSplit.LOCKED_OOD and hits:
                raise ValueError("development or IID case hits an OOD holdout family")


def component_assignments(
    release: LeakageComponentReleaseV1,
) -> dict[str, str]:
    """Return the only valid case-to-resampling-cluster mapping."""

    release = LeakageComponentReleaseV1.model_validate(
        release.model_dump(mode="python", round_trip=True)
    )
    return {
        case_id: component.component_id
        for component in release.components
        for case_id in component.case_ids
    }


def assert_leakage_releases_disjoint(
    *releases: LeakageComponentReleaseV1,
) -> None:
    """Legacy-v1 disjointness check; never use as the formal Pilot verifier."""

    validated = tuple(
        LeakageComponentReleaseV1.model_validate(
            release.model_dump(mode="python", round_trip=True)
        )
        for release in releases
    )
    case_owner: dict[str, str] = {}
    group_owner: dict[tuple[LeakageAxis, str], str] = {}
    for release in validated:
        for membership in release.memberships:
            previous_case = case_owner.setdefault(
                membership.case_id, release.release_id
            )
            if previous_case != release.release_id:
                raise ValueError("a case appears in more than one leakage release")
            key = (membership.axis, membership.group_id)
            previous_group = group_owner.setdefault(key, release.release_id)
            if previous_group != release.release_id:
                raise ValueError(
                    "a typed leakage family appears in more than one release"
                )


# V1 remains readable for the already content-addressed draft.  V2 introduced
# derived memberships, but is superseded for new Pilot/Main work by V3 below.
STRUCTURE_LEAKAGE_AXES = frozenset(
    {
        LeakageAxis.STRUCTURE_PROTOTYPE,
        LeakageAxis.STRUCTURE_FINGERPRINT,
    }
)


def _assert_addressed_v2(
    value: StrictModel, *, id_field: str, sha_field: str, prefix: str
) -> None:
    semantic = value.model_dump(mode="python", exclude={id_field, sha_field})
    expected_sha256 = canonical_sha256(semantic)
    if getattr(value, sha_field) != expected_sha256:
        raise ValueError(f"{sha_field} does not match semantic content")
    if getattr(value, id_field) != deterministic_id(
        prefix, {sha_field: expected_sha256}
    ):
        raise ValueError(f"{id_field} does not match {sha_field}")


def _build_addressed_v3(
    model_type: type[StrictModel],
    *,
    id_field: str,
    sha_field: str,
    prefix: str,
    values: dict[str, object],
) -> StrictModel:
    """Build one immutable content-addressed private-governance artifact."""

    draft = model_type.model_construct(**values)
    digest = canonical_sha256(
        draft.model_dump(mode="python", exclude={id_field, sha_field})
    )
    return model_type.model_validate(
        {
            **values,
            sha_field: digest,
            id_field: deterministic_id(prefix, {sha_field: digest}),
        }
    )


class StructureGroupingAlgorithmV2(StrictModel):
    """Frozen implementation/configuration identity for one structure axis."""

    schema_version: Literal["flatband-structure-grouping-algorithm-v2"] = (
        "flatband-structure-grouping-algorithm-v2"
    )
    algorithm_id: Identifier
    algorithm_sha256: Sha256
    axis: LeakageAxis
    algorithm_name: ShortText
    algorithm_version: ShortText
    implementation_sha256: Sha256
    configuration_sha256: Sha256
    scientific_conclusion: Literal[False] = False

    @model_validator(mode="after")
    def validate_algorithm(self) -> StructureGroupingAlgorithmV2:
        if self.axis not in STRUCTURE_LEAKAGE_AXES:
            raise ValueError("structure grouping algorithm has a non-structure axis")
        _assert_addressed_v2(
            self,
            id_field="algorithm_id",
            sha_field="algorithm_sha256",
            prefix="structure-group-algorithm",
        )
        return self


class StructureGroupingRunV2(StrictModel):
    """One content-addressed grouping execution over the exact case universe."""

    schema_version: Literal["flatband-structure-grouping-run-v2"] = (
        "flatband-structure-grouping-run-v2"
    )
    grouping_run_id: Identifier
    grouping_run_sha256: Sha256
    axis: LeakageAxis
    algorithm_id: Identifier
    algorithm_sha256: Sha256
    input_case_universe_sha256: Sha256
    runtime_environment_sha256: Sha256
    started_at: Annotated[str, Field(min_length=20, max_length=40)]
    completed_at: Annotated[str, Field(min_length=20, max_length=40)]
    status: Literal["SUCCEEDED"] = "SUCCEEDED"
    scientific_conclusion: Literal[False] = False

    @field_validator("started_at", "completed_at")
    @classmethod
    def validate_time(cls, value: str) -> str:
        return _require_rfc3339(value)

    @model_validator(mode="after")
    def validate_run(self) -> StructureGroupingRunV2:
        if self.axis not in STRUCTURE_LEAKAGE_AXES:
            raise ValueError("structure grouping run has a non-structure axis")
        started = datetime.fromisoformat(self.started_at)
        completed = datetime.fromisoformat(
            self.completed_at
        )
        if completed < started:
            raise ValueError("structure grouping run completes before it starts")
        _assert_addressed_v2(
            self,
            id_field="grouping_run_id",
            sha_field="grouping_run_sha256",
            prefix="structure-group-run",
        )
        return self


class StructureGroupingAssignmentV2(StrictModel):
    """Content-addressed result for exactly one case and one structure axis."""

    schema_version: Literal["flatband-structure-grouping-assignment-v2"] = (
        "flatband-structure-grouping-assignment-v2"
    )
    assignment_id: Identifier
    assignment_sha256: Sha256
    axis: LeakageAxis
    algorithm_id: Identifier
    algorithm_sha256: Sha256
    grouping_run_id: Identifier
    grouping_run_sha256: Sha256
    case_id: Identifier
    case_sha256: Sha256
    structure_sha256: Sha256
    canonical_group_key: ShortText
    scientific_conclusion: Literal[False] = False

    @model_validator(mode="after")
    def validate_assignment(self) -> StructureGroupingAssignmentV2:
        if self.axis not in STRUCTURE_LEAKAGE_AXES:
            raise ValueError("structure assignment has a non-structure axis")
        _assert_addressed_v2(
            self,
            id_field="assignment_id",
            sha_field="assignment_sha256",
            prefix="structure-group-assignment",
        )
        return self


def _canonical_preimage_v2(value: object) -> str:
    return canonical_json_bytes(value).decode("utf-8")


def _validate_group_preimage_v2(axis: LeakageAxis, value: str) -> None:
    try:
        decoded = json.loads(value)
        replayed = _canonical_preimage_v2(decoded)
    except (TypeError, ValueError) as exc:
        raise ValueError("leakage group preimage must be canonical JSON") from exc
    if replayed != value or not isinstance(decoded, dict):
        raise ValueError("leakage group preimage must be a canonical JSON object")
    expected_keys: dict[LeakageAxis, set[str]] = {
        LeakageAxis.COMPOSITION_FAMILY: {"kind", "stoichiometry"},
        LeakageAxis.MECHANISM_FAMILY: {"kind", "mechanism"},
        LeakageAxis.ARTICLE_OR_SOURCE_FAMILY: {
            "kind",
            "source_id",
            "source_record_id",
        },
        LeakageAxis.STRUCTURE_PROTOTYPE: {
            "kind",
            "axis",
            "algorithm_id",
            "algorithm_sha256",
            "canonical_group_key",
        },
        LeakageAxis.STRUCTURE_FINGERPRINT: {
            "kind",
            "axis",
            "algorithm_id",
            "algorithm_sha256",
            "canonical_group_key",
        },
    }
    if set(decoded) != expected_keys[axis]:
        raise ValueError("leakage group preimage fields differ from its axis")
    expected_kind = {
        LeakageAxis.COMPOSITION_FAMILY: "composition",
        LeakageAxis.MECHANISM_FAMILY: "mechanism",
        LeakageAxis.ARTICLE_OR_SOURCE_FAMILY: "source-record",
        LeakageAxis.STRUCTURE_PROTOTYPE: "structure-group",
        LeakageAxis.STRUCTURE_FINGERPRINT: "structure-group",
    }[axis]
    if decoded.get("kind") != expected_kind:
        raise ValueError("leakage group preimage kind differs from its axis")
    if axis in STRUCTURE_LEAKAGE_AXES and decoded.get("axis") != axis.value:
        raise ValueError("structure group preimage axis differs from definition")


class LeakageGroupDefinitionV2(StrictModel):
    """Canonical typed group definition; the ID is derived only from its preimage."""

    group_id: Identifier
    definition_sha256: Sha256
    axis: LeakageAxis
    canonical_preimage: Annotated[str, Field(min_length=2, max_length=2_048)]

    @model_validator(mode="after")
    def validate_definition(self) -> LeakageGroupDefinitionV2:
        _validate_group_preimage_v2(self.axis, self.canonical_preimage)
        semantic = self.model_dump(
            mode="python", exclude={"group_id", "definition_sha256"}
        )
        if self.definition_sha256 != canonical_sha256(semantic):
            raise ValueError("leakage group definition SHA-256 does not match")
        expected_group_id = deterministic_id(
            "leakage-group",
            {"axis": self.axis.value, "canonical_preimage": self.canonical_preimage},
        )
        if self.group_id != expected_group_id:
            raise ValueError("typed leakage group ID does not match canonical preimage")
        return self


class LeakageMembershipV2(StrictModel):
    """Derived membership with replayable group and optional assignment provenance."""

    case_id: Identifier
    case_sha256: Sha256
    axis: LeakageAxis
    group_id: Identifier
    group_definition_sha256: Sha256
    structure_assignment_id: Identifier | None = None
    structure_assignment_sha256: Sha256 | None = None
    provenance_sha256: Sha256

    @model_validator(mode="after")
    def validate_membership(self) -> LeakageMembershipV2:
        has_id = self.structure_assignment_id is not None
        has_sha = self.structure_assignment_sha256 is not None
        if has_id != has_sha:
            raise ValueError("structure assignment ID and SHA must be present together")
        if self.axis in STRUCTURE_LEAKAGE_AXES and not has_id:
            raise ValueError("structure membership requires grouping assignment")
        if self.axis not in STRUCTURE_LEAKAGE_AXES and has_id:
            raise ValueError("derived non-structure membership cannot cite assignment")
        return self


class LeakageComponentReleaseV2(StrictModel):
    """Historical derived-membership release, superseded by formal V3."""

    schema_version: Literal["flatband-leakage-component-release-v2"] = (
        "flatband-leakage-component-release-v2"
    )
    release_id: Identifier
    release_sha256: Sha256
    split_manifest_id: Identifier
    split_manifest_sha256: Sha256
    full_case_universe_sha256: Sha256
    grouping_algorithms: Annotated[
        tuple[StructureGroupingAlgorithmV2, ...], Field(min_length=2, max_length=2)
    ]
    grouping_runs: Annotated[
        tuple[StructureGroupingRunV2, ...], Field(min_length=2, max_length=2)
    ]
    grouping_assignments: Annotated[
        tuple[StructureGroupingAssignmentV2, ...],
        Field(min_length=60, max_length=240),
    ]
    group_definitions: Annotated[
        tuple[LeakageGroupDefinitionV2, ...], Field(min_length=1, max_length=20_000)
    ]
    memberships: Annotated[
        tuple[LeakageMembershipV2, ...], Field(min_length=1, max_length=20_000)
    ]
    components: Annotated[
        tuple[LeakageComponentV1, ...], Field(min_length=1, max_length=120)
    ]
    created_at: Annotated[str, Field(min_length=20, max_length=40)]
    caller_supplied_memberships_allowed: Literal[False] = False
    scientific_conclusion: Literal[False] = False

    @field_validator("created_at")
    @classmethod
    def validate_created_at(cls, value: str) -> str:
        return _require_rfc3339(value)

    @model_validator(mode="after")
    def validate_release(self) -> LeakageComponentReleaseV2:
        algorithm_axes = tuple(item.axis.value for item in self.grouping_algorithms)
        if algorithm_axes != tuple(sorted(axis.value for axis in STRUCTURE_LEAKAGE_AXES)):
            raise ValueError("V2 requires one sorted algorithm for each structure axis")
        run_axes = tuple(item.axis.value for item in self.grouping_runs)
        if run_axes != algorithm_axes:
            raise ValueError("V2 requires one sorted run for each structure axis")
        release_time = datetime.fromisoformat(
            self.created_at
        )
        if any(
            release_time
            < datetime.fromisoformat(item.completed_at)
            for item in self.grouping_runs
        ):
            raise ValueError("V2 release predates a referenced grouping run")
        assignment_keys = tuple(
            (item.axis.value, item.case_id, item.assignment_id)
            for item in self.grouping_assignments
        )
        if assignment_keys != tuple(sorted(assignment_keys)):
            raise ValueError("structure assignments must be axis/case/ID sorted")
        if len({(axis, case_id) for axis, case_id, _ in assignment_keys}) != len(
            assignment_keys
        ):
            raise ValueError("duplicate structure assignment for one case and axis")
        definition_keys = tuple(
            (item.axis.value, item.group_id, item.definition_sha256)
            for item in self.group_definitions
        )
        if definition_keys != tuple(sorted(set(definition_keys))):
            raise ValueError("leakage group definitions must be sorted and unique")
        if len({item.group_id for item in self.group_definitions}) != len(
            self.group_definitions
        ):
            raise ValueError("typed leakage group IDs must be globally unique")
        membership_keys = tuple(
            (item.case_id, item.axis.value, item.group_id, item.provenance_sha256)
            for item in self.memberships
        )
        if membership_keys != tuple(sorted(set(membership_keys))):
            raise ValueError("V2 memberships must be key-sorted and unique")
        definitions = {item.group_id: item for item in self.group_definitions}
        assignments = {item.assignment_id: item for item in self.grouping_assignments}
        for membership in self.memberships:
            definition = definitions.get(membership.group_id)
            if definition is None or (
                definition.axis,
                definition.definition_sha256,
            ) != (membership.axis, membership.group_definition_sha256):
                raise ValueError("membership does not bind its exact group definition")
            if membership.structure_assignment_id is not None:
                assignment = assignments.get(membership.structure_assignment_id)
                if assignment is None or (
                    assignment.assignment_sha256,
                    assignment.axis,
                    assignment.case_id,
                ) != (
                    membership.structure_assignment_sha256,
                    membership.axis,
                    membership.case_id,
                ):
                    raise ValueError("membership does not bind its structure assignment")
        component_ids = tuple(item.component_id for item in self.components)
        if component_ids != tuple(sorted(set(component_ids))):
            raise ValueError("V2 components must be component-ID sorted and unique")
        if self.components != _components_from_memberships(self.memberships):
            raise ValueError("V2 components do not replay from derived memberships")
        _assert_addressed_v2(
            self,
            id_field="release_id",
            sha_field="release_sha256",
            prefix="leakage-release-v2",
        )
        return self


_ELEMENT_SYMBOLS = frozenset(
    ["H", "He", "Li", "Be", "B", "C", "N", "O", "F", "Ne", "Na", "Mg", "Al", "Si", "P", "S", "Cl", "Ar", "K", "Ca", "Sc", "Ti", "V", "Cr", "Mn", "Fe", "Co", "Ni", "Cu", "Zn", "Ga", "Ge", "As", "Se", "Br", "Kr", "Rb", "Sr", "Y", "Zr", "Nb", "Mo", "Tc", "Ru", "Rh", "Pd", "Ag", "Cd", "In", "Sn", "Sb", "Te", "I", "Xe", "Cs", "Ba", "La", "Ce", "Pr", "Nd", "Pm", "Sm", "Eu", "Gd", "Tb", "Dy", "Ho", "Er", "Tm", "Yb", "Lu", "Hf", "Ta", "W", "Re", "Os", "Ir", "Pt", "Au", "Hg", "Tl", "Pb", "Bi", "Po", "At", "Rn", "Fr", "Ra", "Ac", "Th", "Pa", "U", "Np", "Pu", "Am", "Cm", "Bk", "Cf", "Es", "Fm", "Md", "No", "Lr", "Rf", "Db", "Sg", "Bh", "Hs", "Mt", "Ds", "Rg", "Cn", "Nh", "Fl", "Mc", "Lv", "Ts", "Og"]
)
_FORMULA_TOKEN = re.compile(r"[A-Z][a-z]?|(?:\d+(?:\.\d*)?|\.\d+)|[()\[\]]")
_FORMULA_SUBSCRIPTS = str.maketrans("₀₁₂₃₄₅₆₇₈₉", "0123456789")


def _formula_stoichiometry_v2(formula: str) -> tuple[tuple[str, int], ...]:
    compact = "".join(formula.translate(_FORMULA_SUBSCRIPTS).split())
    tokens = _FORMULA_TOKEN.findall(compact)
    if not compact or "".join(tokens) != compact:
        raise ValueError("formula cannot be deterministically normalized")
    index = 0

    def multiplier() -> Fraction:
        nonlocal index
        if index >= len(tokens) or not re.fullmatch(
            r"(?:\d+(?:\.\d*)?|\.\d+)", tokens[index]
        ):
            return Fraction(1)
        value = Fraction(tokens[index])
        index += 1
        if value <= 0 or value.denominator > 1_000_000:
            raise ValueError("formula multiplier is outside the canonical domain")
        return value

    def sequence(closing: str | None = None) -> dict[str, Fraction]:
        nonlocal index
        result: dict[str, Fraction] = defaultdict(Fraction)
        pairs = {"(": ")", "[": "]"}
        while index < len(tokens):
            token = tokens[index]
            if token in {")", "]"}:
                if token != closing:
                    raise ValueError("formula grouping delimiters are inconsistent")
                index += 1
                return result
            if token in pairs:
                index += 1
                nested = sequence(pairs[token])
                if not nested:
                    raise ValueError("formula contains an empty group")
                scale = multiplier()
                for symbol, count in nested.items():
                    result[symbol] += count * scale
                continue
            if token not in _ELEMENT_SYMBOLS:
                raise ValueError("formula contains an unknown element symbol")
            index += 1
            result[token] += multiplier()
        if closing is not None:
            raise ValueError("formula grouping delimiter is not closed")
        return result

    counts = sequence()
    if index != len(tokens) or not counts:
        raise ValueError("formula cannot be deterministically normalized")
    denominator = 1
    for count in counts.values():
        denominator = lcm(denominator, count.denominator)
    integers = {symbol: int(count * denominator) for symbol, count in counts.items()}
    divisor = 0
    for count in integers.values():
        divisor = gcd(divisor, count)
    if divisor <= 0:
        raise ValueError("formula has no positive stoichiometry")
    return tuple(sorted((symbol, count // divisor) for symbol, count in integers.items()))


def _group_definition_v2(
    *, axis: LeakageAxis, preimage_value: object
) -> LeakageGroupDefinitionV2:
    preimage = _canonical_preimage_v2(preimage_value)
    semantic = {"axis": axis, "canonical_preimage": preimage}
    definition_sha256 = canonical_sha256(semantic)
    return LeakageGroupDefinitionV2(
        group_id=deterministic_id(
            "leakage-group",
            {"axis": axis.value, "canonical_preimage": preimage},
        ),
        definition_sha256=definition_sha256,
        axis=axis,
        canonical_preimage=preimage,
    )


def _composition_definition_v2(formula: str) -> LeakageGroupDefinitionV2:
    return _group_definition_v2(
        axis=LeakageAxis.COMPOSITION_FAMILY,
        preimage_value={
            "kind": "composition",
            "stoichiometry": _formula_stoichiometry_v2(formula),
        },
    )


def _mechanism_definition_v2(
    mechanism: MechanismFamily,
) -> LeakageGroupDefinitionV2:
    return _group_definition_v2(
        axis=LeakageAxis.MECHANISM_FAMILY,
        preimage_value={"kind": "mechanism", "mechanism": mechanism.value},
    )


def _source_definition_v2(
    source_record: SourceRecordRefV1,
) -> LeakageGroupDefinitionV2:
    return _group_definition_v2(
        axis=LeakageAxis.ARTICLE_OR_SOURCE_FAMILY,
        preimage_value={
            "kind": "source-record",
            "source_id": source_record.source_id,
            "source_record_id": source_record.source_record_id,
        },
    )


def _structure_definition_v2(
    algorithm: StructureGroupingAlgorithmV2, canonical_group_key: str
) -> LeakageGroupDefinitionV2:
    return _group_definition_v2(
        axis=algorithm.axis,
        preimage_value={
            "kind": "structure-group",
            "axis": algorithm.axis.value,
            "algorithm_id": algorithm.algorithm_id,
            "algorithm_sha256": algorithm.algorithm_sha256,
            "canonical_group_key": canonical_group_key,
        },
    )


def derive_leakage_group_ids_v2(
    *,
    formula: str,
    primary_mechanism_stratum: MechanismFamily,
    source_records: tuple[SourceRecordRefV1, ...],
    structure_groups: tuple[tuple[StructureGroupingAlgorithmV2, str], ...],
) -> tuple[str, ...]:
    """Derive the exact group IDs needed before a full case is content-addressed."""

    algorithms = tuple(
        StructureGroupingAlgorithmV2.model_validate(
            algorithm.model_dump(mode="python", round_trip=True)
        )
        for algorithm, _ in structure_groups
    )
    if {item.axis for item in algorithms} != STRUCTURE_LEAKAGE_AXES or len(
        algorithms
    ) != 2:
        raise ValueError("case construction requires both structure grouping axes")
    records = tuple(
        SourceRecordRefV1.model_validate(
            item.model_dump(mode="python", round_trip=True)
        )
        for item in source_records
    )
    definitions = [
        _composition_definition_v2(formula),
        _mechanism_definition_v2(primary_mechanism_stratum),
        *(_source_definition_v2(item) for item in records),
        *(
            _structure_definition_v2(algorithm, key)
            for algorithm, key in structure_groups
        ),
    ]
    group_ids = tuple(sorted({item.group_id for item in definitions}))
    if len(group_ids) != len(definitions):
        raise ValueError("case derivation produced duplicate typed leakage groups")
    return group_ids


def structure_grouping_case_universe_sha256_v2(
    cases: tuple[FlatBandBenchmarkCaseV1, ...],
) -> str:
    """Hash the exact case/structure inputs consumed by both grouping runs."""

    validated = tuple(
        sorted(
            (
                FlatBandBenchmarkCaseV1.model_validate(
                    item.model_dump(mode="python", round_trip=True)
                )
                for item in cases
            ),
            key=lambda item: item.case_id,
        )
    )
    if len({item.case_id for item in validated}) != len(validated):
        raise ValueError("full case universe contains duplicate case identity")
    return canonical_sha256(
        tuple(
            {
                "case_id": item.case_id,
                "case_sha256": item.case_sha256,
                "structure_sha256": item.structure_sha256,
            }
            for item in validated
        )
    )


def _validate_full_cases_v2(
    *,
    cases: tuple[FlatBandBenchmarkCaseV1, ...],
    split_manifest: BenchmarkSplitManifestV1,
) -> tuple[FlatBandBenchmarkCaseV1, ...]:
    validated = tuple(
        sorted(
            (
                FlatBandBenchmarkCaseV1.model_validate(
                    item.model_dump(mode="python", round_trip=True)
                )
                for item in cases
            ),
            key=lambda item: item.case_id,
        )
    )
    if len({item.case_id for item in validated}) != len(validated):
        raise ValueError("full case universe contains duplicate case identity")
    full_by_id = {item.case_id: item for item in validated}
    split_by_id = {item.case_id: item for item in split_manifest.cases}
    if set(full_by_id) != set(split_by_id):
        raise ValueError("full cases do not exactly cover the split manifest")
    for case_id, full_case in full_by_id.items():
        split_case = split_by_id[case_id]
        if (
            full_case.case_sha256,
            full_case.target_class,
            full_case.dimensionality,
            full_case.primary_mechanism_stratum,
            full_case.leakage_group_ids,
        ) != (
            split_case.case_sha256,
            split_case.target_class,
            split_case.dimensionality,
            split_case.primary_mechanism_stratum,
            split_case.leakage_group_ids,
        ):
            raise ValueError("full case differs from its exact split projection")
    return validated


def _validate_structure_grouping_v2(
    *,
    cases: tuple[FlatBandBenchmarkCaseV1, ...],
    grouping_algorithms: tuple[StructureGroupingAlgorithmV2, ...],
    grouping_runs: tuple[StructureGroupingRunV2, ...],
    grouping_assignments: tuple[StructureGroupingAssignmentV2, ...],
) -> tuple[
    tuple[StructureGroupingAlgorithmV2, ...],
    tuple[StructureGroupingRunV2, ...],
    tuple[StructureGroupingAssignmentV2, ...],
    dict[tuple[LeakageAxis, str], StructureGroupingAssignmentV2],
]:
    algorithms = tuple(
        sorted(
            (
                StructureGroupingAlgorithmV2.model_validate(
                    item.model_dump(mode="python", round_trip=True)
                )
                for item in grouping_algorithms
            ),
            key=lambda item: item.axis.value,
        )
    )
    if len(algorithms) != 2 or {item.axis for item in algorithms} != STRUCTURE_LEAKAGE_AXES:
        raise ValueError("exactly one algorithm per structure axis is required")
    algorithm_by_axis = {item.axis: item for item in algorithms}
    runs = tuple(
        sorted(
            (
                StructureGroupingRunV2.model_validate(
                    item.model_dump(mode="python", round_trip=True)
                )
                for item in grouping_runs
            ),
            key=lambda item: item.axis.value,
        )
    )
    if len(runs) != 2 or {item.axis for item in runs} != STRUCTURE_LEAKAGE_AXES:
        raise ValueError("exactly one grouping run per structure axis is required")
    universe_sha256 = structure_grouping_case_universe_sha256_v2(cases)
    run_by_axis = {item.axis: item for item in runs}
    for run in runs:
        algorithm = algorithm_by_axis[run.axis]
        if (run.algorithm_id, run.algorithm_sha256) != (
            algorithm.algorithm_id,
            algorithm.algorithm_sha256,
        ):
            raise ValueError("grouping run records algorithm drift")
        if run.input_case_universe_sha256 != universe_sha256:
            raise ValueError("grouping run binds a different full case universe")
    assignments = tuple(
        sorted(
            (
                StructureGroupingAssignmentV2.model_validate(
                    item.model_dump(mode="python", round_trip=True)
                )
                for item in grouping_assignments
            ),
            key=lambda item: (item.axis.value, item.case_id, item.assignment_id),
        )
    )
    assignment_by_key: dict[
        tuple[LeakageAxis, str], StructureGroupingAssignmentV2
    ] = {}
    case_by_id = {item.case_id: item for item in cases}
    for assignment in assignments:
        key = (assignment.axis, assignment.case_id)
        if key in assignment_by_key:
            raise ValueError("duplicate structure assignment for one case and axis")
        if assignment.case_id not in case_by_id:
            raise ValueError("foreign case appears in structure assignments")
        algorithm = algorithm_by_axis[assignment.axis]
        run = run_by_axis[assignment.axis]
        if (
            assignment.algorithm_id,
            assignment.algorithm_sha256,
            assignment.grouping_run_id,
            assignment.grouping_run_sha256,
        ) != (
            algorithm.algorithm_id,
            algorithm.algorithm_sha256,
            run.grouping_run_id,
            run.grouping_run_sha256,
        ):
            raise ValueError("structure assignment records algorithm or run drift")
        case = case_by_id[assignment.case_id]
        if (assignment.case_sha256, assignment.structure_sha256) != (
            case.case_sha256,
            case.structure_sha256,
        ):
            raise ValueError("structure assignment binds a different case or structure")
        assignment_by_key[key] = assignment
    expected_keys = {
        (axis, case.case_id) for axis in STRUCTURE_LEAKAGE_AXES for case in cases
    }
    if set(assignment_by_key) != expected_keys:
        raise ValueError("structure assignments do not exactly cover cases and axes")
    return algorithms, runs, assignments, assignment_by_key


def _derive_memberships_v2(
    *,
    cases: tuple[FlatBandBenchmarkCaseV1, ...],
    algorithms: tuple[StructureGroupingAlgorithmV2, ...],
    assignments: dict[tuple[LeakageAxis, str], StructureGroupingAssignmentV2],
) -> tuple[
    tuple[LeakageGroupDefinitionV2, ...],
    tuple[LeakageMembershipV2, ...],
    dict[str, tuple[str, ...]],
]:
    algorithm_by_axis = {item.axis: item for item in algorithms}
    definitions: dict[str, LeakageGroupDefinitionV2] = {}
    memberships: list[LeakageMembershipV2] = []
    groups_by_case: dict[str, tuple[str, ...]] = {}
    shared_sources: dict[tuple[str, str], SourceRecordRefV1] = {}

    def add_membership(
        *,
        case: FlatBandBenchmarkCaseV1,
        definition: LeakageGroupDefinitionV2,
        derivation: object,
        assignment: StructureGroupingAssignmentV2 | None = None,
    ) -> None:
        previous = definitions.setdefault(definition.group_id, definition)
        if previous != definition:
            raise ValueError("typed group ID resolves to conflicting definitions")
        provenance = canonical_sha256(
            {
                "case_id": case.case_id,
                "case_sha256": case.case_sha256,
                "axis": definition.axis.value,
                "group_id": definition.group_id,
                "group_definition_sha256": definition.definition_sha256,
                "derivation": derivation,
                "structure_assignment_id": (
                    None if assignment is None else assignment.assignment_id
                ),
                "structure_assignment_sha256": (
                    None if assignment is None else assignment.assignment_sha256
                ),
            }
        )
        memberships.append(
            LeakageMembershipV2(
                case_id=case.case_id,
                case_sha256=case.case_sha256,
                axis=definition.axis,
                group_id=definition.group_id,
                group_definition_sha256=definition.definition_sha256,
                structure_assignment_id=(
                    None if assignment is None else assignment.assignment_id
                ),
                structure_assignment_sha256=(
                    None if assignment is None else assignment.assignment_sha256
                ),
                provenance_sha256=provenance,
            )
        )

    for case in cases:
        composition = _composition_definition_v2(case.formula)
        add_membership(
            case=case,
            definition=composition,
            derivation={
                "formula": case.formula,
                "normalized_stoichiometry": _formula_stoichiometry_v2(case.formula),
            },
        )
        mechanism = _mechanism_definition_v2(case.primary_mechanism_stratum)
        add_membership(
            case=case,
            definition=mechanism,
            derivation={"mechanism": case.primary_mechanism_stratum.value},
        )
        for record in case.source_records:
            source_key = (record.source_id, record.source_record_id)
            previous_record = shared_sources.setdefault(source_key, record)
            if previous_record != record:
                raise ValueError("shared source record identity has conflicting content")
            source = _source_definition_v2(record)
            add_membership(
                case=case,
                definition=source,
                derivation={
                    "source_record": record.model_dump(mode="python", round_trip=True),
                    "source_record_sha256": canonical_sha256(
                        record.model_dump(mode="python", round_trip=True)
                    ),
                },
            )
        for axis in sorted(STRUCTURE_LEAKAGE_AXES, key=lambda item: item.value):
            assignment = assignments[(axis, case.case_id)]
            structure = _structure_definition_v2(
                algorithm_by_axis[axis], assignment.canonical_group_key
            )
            add_membership(
                case=case,
                definition=structure,
                derivation={
                    "assignment_id": assignment.assignment_id,
                    "assignment_sha256": assignment.assignment_sha256,
                },
                assignment=assignment,
            )
        groups_by_case[case.case_id] = tuple(
            sorted(item.group_id for item in memberships if item.case_id == case.case_id)
        )
    ordered_definitions = tuple(
        sorted(definitions.values(), key=lambda item: (item.axis.value, item.group_id))
    )
    ordered_memberships = tuple(
        sorted(
            memberships,
            key=lambda item: (
                item.case_id,
                item.axis.value,
                item.group_id,
                item.provenance_sha256,
            ),
        )
    )
    return ordered_definitions, ordered_memberships, groups_by_case


def _assert_v2_component_policy(
    *,
    split_manifest: BenchmarkSplitManifestV1,
    release: LeakageComponentReleaseV2,
) -> None:
    split_by_case = {item.case_id: item.split for item in split_manifest.cases}
    component_splits: list[BenchmarkSplit] = []
    for component in release.components:
        splits = {split_by_case[case_id] for case_id in component.case_ids}
        if len(splits) != 1:
            raise ValueError("a canonical leakage component crosses benchmark splits")
        component_splits.append(next(iter(splits)))
    counts = {split: component_splits.count(split) for split in BenchmarkSplit}
    if split_manifest.manifest_kind in {
        SplitManifestKind.PILOT_R1,
        SplitManifestKind.PILOT_R2,
    }:
        pilot_split = BenchmarkSplit(split_manifest.manifest_kind.value)
        if counts[pilot_split] < 10:
            raise ValueError("pilot requires at least ten canonical components")
    else:
        minima = {
            BenchmarkSplit.DEVELOPMENT: 20,
            BenchmarkSplit.LOCKED_IID: 10,
            BenchmarkSplit.LOCKED_OOD: 10,
        }
        if any(counts[split] < minimum for split, minimum in minima.items()):
            raise ValueError("main splits do not meet canonical component minima")
    holdouts = {
        (LeakageAxis(item.axis.value), item.group_id)
        for item in split_manifest.ood_holdout_families
    }
    if split_manifest.manifest_kind is SplitManifestKind.MAIN_120:
        typed_by_case: dict[str, set[tuple[LeakageAxis, str]]] = defaultdict(set)
        for membership in release.memberships:
            typed_by_case[membership.case_id].add(
                (membership.axis, membership.group_id)
            )
        for case_id, split in split_by_case.items():
            hits = typed_by_case[case_id] & holdouts
            if split is BenchmarkSplit.LOCKED_OOD and not hits:
                raise ValueError("every OOD case must hit a canonical holdout family")
            if split is not BenchmarkSplit.LOCKED_OOD and hits:
                raise ValueError("development or IID case hits a canonical OOD holdout")


def build_leakage_component_release_v2(
    *,
    cases: tuple[FlatBandBenchmarkCaseV1, ...],
    split_manifest: BenchmarkSplitManifestV1,
    grouping_algorithms: tuple[StructureGroupingAlgorithmV2, ...],
    grouping_runs: tuple[StructureGroupingRunV2, ...],
    grouping_assignments: tuple[StructureGroupingAssignmentV2, ...],
    created_at: str,
) -> LeakageComponentReleaseV2:
    """Build V2 only from full cases and replayable structure grouping artifacts."""

    split = BenchmarkSplitManifestV1.model_validate(
        split_manifest.model_dump(mode="python", round_trip=True)
    )
    full_cases = _validate_full_cases_v2(cases=cases, split_manifest=split)
    algorithms, runs, assignments_tuple, assignment_by_key = (
        _validate_structure_grouping_v2(
            cases=full_cases,
            grouping_algorithms=grouping_algorithms,
            grouping_runs=grouping_runs,
            grouping_assignments=grouping_assignments,
        )
    )
    definitions, memberships, derived_groups = _derive_memberships_v2(
        cases=full_cases,
        algorithms=algorithms,
        assignments=assignment_by_key,
    )
    split_by_id = {item.case_id: item for item in split.cases}
    for case in full_cases:
        if case.leakage_group_ids != derived_groups[case.case_id]:
            raise ValueError("full case leakage group IDs differ from canonical derivation")
        if split_by_id[case.case_id].leakage_group_ids != derived_groups[case.case_id]:
            raise ValueError("split leakage group IDs differ from canonical derivation")
    components = _components_from_memberships(memberships)
    values = {
        "split_manifest_id": split.manifest_id,
        "split_manifest_sha256": split.manifest_sha256,
        "full_case_universe_sha256": structure_grouping_case_universe_sha256_v2(
            full_cases
        ),
        "grouping_algorithms": algorithms,
        "grouping_runs": runs,
        "grouping_assignments": assignments_tuple,
        "group_definitions": definitions,
        "memberships": memberships,
        "components": components,
        "created_at": _require_rfc3339(created_at),
    }
    semantic = LeakageComponentReleaseV2.model_construct(**values).model_dump(
        mode="python", exclude={"release_id", "release_sha256"}
    )
    digest = canonical_sha256(semantic)
    release = LeakageComponentReleaseV2.model_validate(
        {
            **values,
            "release_sha256": digest,
            "release_id": deterministic_id(
                "leakage-release-v2", {"release_sha256": digest}
            ),
        }
    )
    assert_leakage_split_closure_v2(
        cases=full_cases, split_manifest=split, release=release
    )
    return release


def assert_leakage_split_closure_v2(
    *,
    cases: tuple[FlatBandBenchmarkCaseV1, ...],
    split_manifest: BenchmarkSplitManifestV1,
    release: LeakageComponentReleaseV2,
) -> None:
    """Replay every V2 group, membership, provenance link, and component."""

    split = BenchmarkSplitManifestV1.model_validate(
        split_manifest.model_dump(mode="python", round_trip=True)
    )
    value = LeakageComponentReleaseV2.model_validate(
        release.model_dump(mode="python", round_trip=True)
    )
    if (value.split_manifest_id, value.split_manifest_sha256) != (
        split.manifest_id,
        split.manifest_sha256,
    ):
        raise ValueError("V2 leakage release references a different split")
    full_cases = _validate_full_cases_v2(cases=cases, split_manifest=split)
    if value.full_case_universe_sha256 != structure_grouping_case_universe_sha256_v2(
        full_cases
    ):
        raise ValueError("V2 release binds a different full case universe")
    algorithms, runs, assignments, assignment_by_key = _validate_structure_grouping_v2(
        cases=full_cases,
        grouping_algorithms=value.grouping_algorithms,
        grouping_runs=value.grouping_runs,
        grouping_assignments=value.grouping_assignments,
    )
    if (
        algorithms,
        runs,
        assignments,
    ) != (
        value.grouping_algorithms,
        value.grouping_runs,
        value.grouping_assignments,
    ):
        raise ValueError("V2 grouping artifacts are not in canonical order")
    definitions, memberships, derived_groups = _derive_memberships_v2(
        cases=full_cases,
        algorithms=algorithms,
        assignments=assignment_by_key,
    )
    if definitions != value.group_definitions:
        raise ValueError("V2 group definitions do not replay from authoritative inputs")
    if memberships != value.memberships:
        raise ValueError("V2 membership provenance does not replay from inputs")
    split_by_id = {item.case_id: item for item in split.cases}
    for case in full_cases:
        expected = derived_groups[case.case_id]
        if case.leakage_group_ids != expected:
            raise ValueError("full case leakage group IDs differ from canonical derivation")
        if split_by_id[case.case_id].leakage_group_ids != expected:
            raise ValueError("split leakage group IDs differ from canonical derivation")
    if value.components != _components_from_memberships(memberships):
        raise ValueError("V2 components do not uniquely replay")
    _assert_v2_component_policy(split_manifest=split, release=value)


def assert_pilot_leakage_v2(
    *,
    cases: tuple[FlatBandBenchmarkCaseV1, ...],
    split_manifest: BenchmarkSplitManifestV1,
    release: LeakageComponentReleaseV2,
) -> None:
    """Replay a historical V2 Pilot; this is insufficient for a new Pilot."""

    if split_manifest.manifest_kind not in {
        SplitManifestKind.PILOT_R1,
        SplitManifestKind.PILOT_R2,
    }:
        raise ValueError("V2 Pilot leakage verifier requires a Pilot split")
    assert_leakage_split_closure_v2(
        cases=cases, split_manifest=split_manifest, release=release
    )


def component_assignments_v2(
    release: LeakageComponentReleaseV2,
) -> dict[str, str]:
    """Return the canonical V2 case-to-component mapping."""

    value = LeakageComponentReleaseV2.model_validate(
        release.model_dump(mode="python", round_trip=True)
    )
    return {
        case_id: component.component_id
        for component in value.components
        for case_id in component.case_ids
    }


# V2 remains readable, but cannot be used for a formal Main release: its broad
# ten-value MechanismFamily axis mathematically caps the connected-component
# count at ten.  V3 separates broad holdout taxonomy from fine-grained,
# registry-backed mechanism-lineage edges.
class LeakageAxisV3(StrEnum):
    COMPOSITION_FAMILY = "COMPOSITION_FAMILY"
    STRUCTURE_PROTOTYPE = "STRUCTURE_PROTOTYPE"
    STRUCTURE_FINGERPRINT = "STRUCTURE_FINGERPRINT"
    ARTICLE_OR_SOURCE_FAMILY = "ARTICLE_OR_SOURCE_FAMILY"
    MECHANISM_LINEAGE = "MECHANISM_LINEAGE"


STRUCTURE_LEAKAGE_AXES_V3 = frozenset(
    {
        LeakageAxisV3.STRUCTURE_PROTOTYPE,
        LeakageAxisV3.STRUCTURE_FINGERPRINT,
    }
)
_V2_TO_V3_STRUCTURE_AXIS = {
    LeakageAxis.STRUCTURE_PROTOTYPE: LeakageAxisV3.STRUCTURE_PROTOTYPE,
    LeakageAxis.STRUCTURE_FINGERPRINT: LeakageAxisV3.STRUCTURE_FINGERPRINT,
}


def _canonical_scientific_text_v3(value: str) -> str:
    canonical = " ".join(unicodedata.normalize("NFKC", value).split())
    if value != canonical:
        raise ValueError("mechanism-lineage scientific text must be whitespace-canonical")
    return value


class MechanismLineageEvidenceRefV3(StrictModel):
    """Exact source record included in a lineage's scientific preimage."""

    source_id: Identifier
    source_record_id: Identifier
    source_record_raw_sha256: Sha256


class MechanismLineageDefinitionV3(StrictModel):
    """Curated, content-addressed fine-grained mechanism relationship.

    The identity includes inspectable scientific content and exact evidence
    records.  It is not an opaque label that can be minted independently for
    every benchmark case.
    """

    schema_version: Literal["flatband-mechanism-lineage-definition-v3"] = (
        "flatband-mechanism-lineage-definition-v3"
    )
    lineage_id: Identifier
    lineage_sha256: Sha256
    scientific_preimage_sha256: Sha256
    broad_mechanism_family: MechanismFamily
    source_mechanism: ShortText
    shared_invariant: ShortText
    transfer_route_family: ShortText
    taxonomy_evidence_refs: Annotated[
        tuple[MechanismLineageEvidenceRefV3, ...], Field(min_length=1, max_length=32)
    ]
    scientific_conclusion: Literal[False] = False

    @field_validator(
        "source_mechanism", "shared_invariant", "transfer_route_family"
    )
    @classmethod
    def validate_scientific_text(cls, value: str) -> str:
        return _canonical_scientific_text_v3(value)

    @model_validator(mode="after")
    def validate_definition(self) -> MechanismLineageDefinitionV3:
        evidence_keys = tuple(
            (item.source_id, item.source_record_id, item.source_record_raw_sha256)
            for item in self.taxonomy_evidence_refs
        )
        if evidence_keys != tuple(sorted(set(evidence_keys))):
            raise ValueError("mechanism-lineage evidence refs must be sorted and unique")
        scientific_preimage = {
            "broad_mechanism_family": self.broad_mechanism_family.value,
            "source_mechanism": self.source_mechanism,
            "shared_invariant": self.shared_invariant,
            "transfer_route_family": self.transfer_route_family,
        }
        if self.scientific_preimage_sha256 != canonical_sha256(scientific_preimage):
            raise ValueError("mechanism-lineage scientific preimage SHA-256 differs")
        _assert_addressed_v2(
            self,
            id_field="lineage_id",
            sha_field="lineage_sha256",
            prefix="mechanism-lineage-v3",
        )
        return self


class MechanismLineageReviewDecisionV3(StrEnum):
    INCLUDE = "INCLUDE"
    REJECT = "REJECT"


class MechanismLineageCurationPolicyV3(StrictModel):
    """Private, replayable policy fixed before the global taxonomy is used.

    This is governance evidence, not a provider attestation.  The formal
    verifier checks its content and ordering; external curator identity
    attestation remains a custody responsibility.
    """

    schema_version: Literal["flatband-mechanism-lineage-curation-policy-v3"] = (
        "flatband-mechanism-lineage-curation-policy-v3"
    )
    policy_id: Identifier
    policy_sha256: Sha256
    taxonomy_version: ShortText
    taxonomy_scope: ShortText
    definition_review_criteria: Annotated[
        tuple[ShortText, ...], Field(min_length=3, max_length=12)
    ]
    global_taxonomy_required: Literal[True] = True
    per_case_lineage_minting_allowed: Literal[False] = False
    independent_curator_count: Literal[2] = 2
    adjudication_required_on_disagreement: Literal[True] = True
    scientifically_supported_singletons_allowed: Literal[True] = True
    registry_seal_is_logical_preselection_requirement: Literal[True] = True
    sealed_at: Annotated[str, Field(min_length=20, max_length=40)]
    scientific_conclusion: Literal[False] = False

    @field_validator("sealed_at")
    @classmethod
    def validate_time(cls, value: str) -> str:
        return _require_rfc3339(value)

    @model_validator(mode="after")
    def validate_policy(self) -> MechanismLineageCurationPolicyV3:
        if self.definition_review_criteria != tuple(
            sorted(set(self.definition_review_criteria))
        ):
            raise ValueError("lineage review criteria must be sorted and unique")
        _assert_addressed_v2(
            self,
            id_field="policy_id",
            sha_field="policy_sha256",
            prefix="lineage-curation-policy-v3",
        )
        return self


class MechanismLineageCuratorDeclarationV3(StrictModel):
    """Private declaration used to replay dual-curator independence."""

    curator_id: Identifier
    opaque_natural_person_ref: Identifier
    natural_person_commitment_sha256: Sha256
    identity_evidence_uri: Annotated[str, Field(min_length=8, max_length=1_024)]
    identity_evidence_sha256: Sha256
    institutional_unit: ShortText
    conflict_declaration: ShortText


class MechanismLineageCuratorRosterV3(StrictModel):
    schema_version: Literal["flatband-mechanism-lineage-curator-roster-v3"] = (
        "flatband-mechanism-lineage-curator-roster-v3"
    )
    roster_id: Identifier
    roster_sha256: Sha256
    policy_id: Identifier
    policy_sha256: Sha256
    curators: Annotated[
        tuple[MechanismLineageCuratorDeclarationV3, ...],
        Field(min_length=2, max_length=2),
    ]
    adjudicators: Annotated[
        tuple[MechanismLineageCuratorDeclarationV3, ...],
        Field(min_length=1, max_length=8),
    ]
    independence_review: ShortText
    sealed_at: Annotated[str, Field(min_length=20, max_length=40)]
    private_custody_required: Literal[True] = True
    public_release_allowed: Literal[False] = False
    scientific_conclusion: Literal[False] = False

    @field_validator("sealed_at")
    @classmethod
    def validate_time(cls, value: str) -> str:
        return _require_rfc3339(value)

    @model_validator(mode="after")
    def validate_roster(self) -> MechanismLineageCuratorRosterV3:
        curator_ids = tuple(item.curator_id for item in self.curators)
        if curator_ids != tuple(sorted(set(curator_ids))):
            raise ValueError("lineage curators must be ID-sorted and unique")
        adjudicator_ids = tuple(item.curator_id for item in self.adjudicators)
        if adjudicator_ids != tuple(sorted(set(adjudicator_ids))):
            raise ValueError("lineage adjudicators must be ID-sorted and unique")
        if set(curator_ids) & set(adjudicator_ids):
            raise ValueError("lineage adjudicator must be independent of raw curators")
        people = (*self.curators, *self.adjudicators)
        for field_name in (
            "opaque_natural_person_ref",
            "natural_person_commitment_sha256",
            "identity_evidence_uri",
            "identity_evidence_sha256",
        ):
            values = tuple(getattr(item, field_name) for item in people)
            if len(values) != len(set(values)):
                raise ValueError(
                    "lineage governance natural-person bindings must be injective"
                )
        _assert_addressed_v2(
            self,
            id_field="roster_id",
            sha_field="roster_sha256",
            prefix="lineage-curator-roster-v3",
        )
        return self


class MechanismLineageDefinitionReviewV3(StrictModel):
    """One private raw decision over an exact public-safe definition."""

    schema_version: Literal["flatband-mechanism-lineage-definition-review-v3"] = (
        "flatband-mechanism-lineage-definition-review-v3"
    )
    review_id: Identifier
    review_sha256: Sha256
    policy_id: Identifier
    policy_sha256: Sha256
    lineage_id: Identifier
    lineage_sha256: Sha256
    scientific_preimage_sha256: Sha256
    taxonomy_evidence_refs: Annotated[
        tuple[MechanismLineageEvidenceRefV3, ...], Field(min_length=1, max_length=32)
    ]
    curator_id: Identifier
    decision: MechanismLineageReviewDecisionV3
    criterion_findings: Annotated[
        tuple[ShortText, ...], Field(min_length=3, max_length=12)
    ]
    rationale: ShortText
    reviewed_at: Annotated[str, Field(min_length=20, max_length=40)]
    private_custody_required: Literal[True] = True
    public_release_allowed: Literal[False] = False
    scientific_conclusion: Literal[False] = False

    @field_validator("reviewed_at")
    @classmethod
    def validate_time(cls, value: str) -> str:
        return _require_rfc3339(value)

    @model_validator(mode="after")
    def validate_review(self) -> MechanismLineageDefinitionReviewV3:
        evidence_keys = tuple(
            (item.source_id, item.source_record_id, item.source_record_raw_sha256)
            for item in self.taxonomy_evidence_refs
        )
        if evidence_keys != tuple(sorted(set(evidence_keys))):
            raise ValueError("lineage review evidence refs must be sorted and unique")
        if self.criterion_findings != tuple(sorted(set(self.criterion_findings))):
            raise ValueError("lineage review findings must be sorted and unique")
        _assert_addressed_v2(
            self,
            id_field="review_id",
            sha_field="review_sha256",
            prefix="lineage-definition-review-v3",
        )
        return self


class MechanismLineageDefinitionAdjudicationV3(StrictModel):
    schema_version: Literal[
        "flatband-mechanism-lineage-definition-adjudication-v3"
    ] = "flatband-mechanism-lineage-definition-adjudication-v3"
    adjudication_id: Identifier
    adjudication_sha256: Sha256
    policy_id: Identifier
    policy_sha256: Sha256
    lineage_id: Identifier
    lineage_sha256: Sha256
    review_refs: Annotated[
        tuple[tuple[Identifier, Sha256], ...], Field(min_length=2, max_length=2)
    ]
    adjudicator_id: Identifier
    final_decision: MechanismLineageReviewDecisionV3
    rationale: ShortText
    adjudicated_at: Annotated[str, Field(min_length=20, max_length=40)]
    private_custody_required: Literal[True] = True
    public_release_allowed: Literal[False] = False
    scientific_conclusion: Literal[False] = False

    @field_validator("adjudicated_at")
    @classmethod
    def validate_time(cls, value: str) -> str:
        return _require_rfc3339(value)

    @model_validator(mode="after")
    def validate_adjudication(self) -> MechanismLineageDefinitionAdjudicationV3:
        if self.review_refs != tuple(sorted(set(self.review_refs))):
            raise ValueError("lineage adjudication review refs must be sorted and unique")
        _assert_addressed_v2(
            self,
            id_field="adjudication_id",
            sha_field="adjudication_sha256",
            prefix="lineage-definition-adjud-v3",
        )
        return self


class MechanismLineageEvidenceReviewManifestV3(StrictModel):
    """Private exact-cover manifest for all definition-level raw decisions."""

    schema_version: Literal[
        "flatband-mechanism-lineage-evidence-review-manifest-v3"
    ] = "flatband-mechanism-lineage-evidence-review-manifest-v3"
    manifest_id: Identifier
    manifest_sha256: Sha256
    policy_id: Identifier
    policy_sha256: Sha256
    roster_id: Identifier
    roster_sha256: Sha256
    reviews: Annotated[
        tuple[MechanismLineageDefinitionReviewV3, ...],
        Field(min_length=2, max_length=1_024),
    ]
    adjudications: Annotated[
        tuple[MechanismLineageDefinitionAdjudicationV3, ...], Field(max_length=512)
    ] = ()
    sealed_at: Annotated[str, Field(min_length=20, max_length=40)]
    private_custody_required: Literal[True] = True
    public_release_allowed: Literal[False] = False
    scientific_conclusion: Literal[False] = False

    @field_validator("sealed_at")
    @classmethod
    def validate_time(cls, value: str) -> str:
        return _require_rfc3339(value)

    @model_validator(mode="after")
    def validate_manifest(self) -> MechanismLineageEvidenceReviewManifestV3:
        review_keys = tuple(
            (item.lineage_id, item.curator_id, item.review_id) for item in self.reviews
        )
        if review_keys != tuple(sorted(set(review_keys))):
            raise ValueError("lineage reviews must be definition/curator sorted and unique")
        by_lineage: dict[str, list[MechanismLineageDefinitionReviewV3]] = defaultdict(list)
        for review in self.reviews:
            if (review.policy_id, review.policy_sha256) != (
                self.policy_id,
                self.policy_sha256,
            ):
                raise ValueError("lineage review binds a foreign curation policy")
            by_lineage[review.lineage_id].append(review)
        if any(
            len(items) != 2 or len({item.curator_id for item in items}) != 2
            for items in by_lineage.values()
        ):
            raise ValueError("every lineage definition requires exactly two curators")

        adjudication_keys = tuple(
            (item.lineage_id, item.adjudication_id) for item in self.adjudications
        )
        if adjudication_keys != tuple(sorted(set(adjudication_keys))):
            raise ValueError("lineage adjudications must be definition-sorted and unique")
        if len({item.lineage_id for item in self.adjudications}) != len(
            self.adjudications
        ):
            raise ValueError("a lineage definition has multiple adjudications")
        adjudication_by_lineage = {
            item.lineage_id: item for item in self.adjudications
        }
        for lineage_id, reviews in by_lineage.items():
            decisions = {item.decision for item in reviews}
            adjudication = adjudication_by_lineage.get(lineage_id)
            if len(decisions) == 1:
                if adjudication is not None:
                    raise ValueError("agreed lineage reviews cannot be adjudicated")
                continue
            if adjudication is None:
                raise ValueError("disagreed lineage reviews require adjudication")
            if (
                adjudication.policy_id,
                adjudication.policy_sha256,
                adjudication.lineage_sha256,
                adjudication.review_refs,
            ) != (
                self.policy_id,
                self.policy_sha256,
                reviews[0].lineage_sha256,
                tuple(sorted((item.review_id, item.review_sha256) for item in reviews)),
            ):
                raise ValueError("lineage adjudication does not bind its exact raw reviews")
        if set(adjudication_by_lineage) - set(by_lineage):
            raise ValueError("lineage adjudication has no reviewed definition")
        sealed = datetime.fromisoformat(self.sealed_at)
        if any(
            sealed < datetime.fromisoformat(item.reviewed_at)
            for item in self.reviews
        ) or any(
            sealed < datetime.fromisoformat(item.adjudicated_at)
            for item in self.adjudications
        ):
            raise ValueError("lineage review manifest predates a raw decision")
        _assert_addressed_v2(
            self,
            id_field="manifest_id",
            sha_field="manifest_sha256",
            prefix="lineage-review-manifest-v3",
        )
        return self


class MechanismLineageCurationReleaseV3(StrictModel):
    """Private custody root; the public registry carries only its opaque SHAs."""

    schema_version: Literal["flatband-mechanism-lineage-curation-release-v3"] = (
        "flatband-mechanism-lineage-curation-release-v3"
    )
    release_id: Identifier
    release_sha256: Sha256
    registry_id: Identifier
    registry_sha256: Sha256
    policy: MechanismLineageCurationPolicyV3
    roster: MechanismLineageCuratorRosterV3
    review_manifest: MechanismLineageEvidenceReviewManifestV3
    assembled_at: Annotated[str, Field(min_length=20, max_length=40)]
    private_custody_required: Literal[True] = True
    public_release_allowed: Literal[False] = False
    external_identity_attestation_claimed: Literal[False] = False
    scientific_conclusion: Literal[False] = False

    @field_validator("assembled_at")
    @classmethod
    def validate_time(cls, value: str) -> str:
        return _require_rfc3339(value)

    @model_validator(mode="after")
    def validate_release(self) -> MechanismLineageCurationReleaseV3:
        if (self.roster.policy_id, self.roster.policy_sha256) != (
            self.policy.policy_id,
            self.policy.policy_sha256,
        ):
            raise ValueError("lineage curator roster binds a foreign policy")
        if (
            self.review_manifest.policy_id,
            self.review_manifest.policy_sha256,
            self.review_manifest.roster_id,
            self.review_manifest.roster_sha256,
        ) != (
            self.policy.policy_id,
            self.policy.policy_sha256,
            self.roster.roster_id,
            self.roster.roster_sha256,
        ):
            raise ValueError("lineage review manifest binds foreign governance")
        assembled = datetime.fromisoformat(self.assembled_at)
        if assembled < datetime.fromisoformat(
            self.review_manifest.sealed_at
        ):
            raise ValueError("lineage curation release predates its review manifest")
        _assert_addressed_v2(
            self,
            id_field="release_id",
            sha_field="release_sha256",
            prefix="lineage-curation-release-v3",
        )
        return self


class MechanismLineageRegistryV3(StrictModel):
    """Sealed registry from which every V3 lineage assignment must resolve."""

    schema_version: Literal["flatband-mechanism-lineage-registry-v3"] = (
        "flatband-mechanism-lineage-registry-v3"
    )
    registry_id: Identifier
    registry_sha256: Sha256
    taxonomy_version: ShortText
    curation_policy_sha256: Sha256
    evidence_review_manifest_sha256: Sha256
    curator_roster_sha256: Sha256
    definitions: Annotated[
        tuple[MechanismLineageDefinitionV3, ...], Field(min_length=1, max_length=512)
    ]
    sealed_at: Annotated[str, Field(min_length=20, max_length=40)]
    sealed: Literal[True] = True
    scientific_conclusion: Literal[False] = False

    @field_validator("sealed_at")
    @classmethod
    def validate_time(cls, value: str) -> str:
        return _require_rfc3339(value)

    @model_validator(mode="after")
    def validate_registry(self) -> MechanismLineageRegistryV3:
        definition_keys = tuple(
            (item.lineage_id, item.lineage_sha256) for item in self.definitions
        )
        if definition_keys != tuple(sorted(set(definition_keys))):
            raise ValueError("mechanism-lineage definitions must be ID-sorted and unique")
        scientific_preimages = tuple(
            item.scientific_preimage_sha256 for item in self.definitions
        )
        if len(scientific_preimages) != len(set(scientific_preimages)):
            raise ValueError("registry duplicates a mechanism scientific preimage")
        _assert_addressed_v2(
            self,
            id_field="registry_id",
            sha_field="registry_sha256",
            prefix="mechanism-lineage-registry-v3",
        )
        return self


class MechanismLineageAssignmentV3(StrictModel):
    """Sealed exact case-to-registry assignment; never a caller group string."""

    schema_version: Literal["flatband-mechanism-lineage-assignment-v3"] = (
        "flatband-mechanism-lineage-assignment-v3"
    )
    assignment_id: Identifier
    assignment_sha256: Sha256
    registry_id: Identifier
    registry_sha256: Sha256
    case_id: Identifier
    case_sha256: Sha256
    lineage_id: Identifier
    lineage_sha256: Sha256
    case_evidence_refs: Annotated[
        tuple[MechanismLineageEvidenceRefV3, ...], Field(min_length=1, max_length=32)
    ]
    assignment_basis_sha256: Sha256
    assigned_at: Annotated[str, Field(min_length=20, max_length=40)]
    sealed: Literal[True] = True
    scientific_conclusion: Literal[False] = False

    @field_validator("assigned_at")
    @classmethod
    def validate_time(cls, value: str) -> str:
        return _require_rfc3339(value)

    @model_validator(mode="after")
    def validate_assignment(self) -> MechanismLineageAssignmentV3:
        evidence_keys = tuple(
            (item.source_id, item.source_record_id, item.source_record_raw_sha256)
            for item in self.case_evidence_refs
        )
        if evidence_keys != tuple(sorted(set(evidence_keys))):
            raise ValueError("case-specific lineage evidence must be sorted and unique")
        _assert_addressed_v2(
            self,
            id_field="assignment_id",
            sha_field="assignment_sha256",
            prefix="mechanism-lineage-assignment-v3",
        )
        return self


class MechanismLineageAssignmentDecisionV3(StrEnum):
    ACCEPT = "ACCEPT"
    REJECT = "REJECT"


class MechanismLineageAssignmentCurationPolicyV3(StrictModel):
    """Private policy fixed before any candidate-to-lineage proposal is reviewed."""

    schema_version: Literal[
        "flatband-mechanism-lineage-assignment-curation-policy-v3"
    ] = "flatband-mechanism-lineage-assignment-curation-policy-v3"
    policy_id: Identifier
    policy_sha256: Sha256
    registry_id: Identifier
    registry_sha256: Sha256
    definition_curation_release_id: Identifier
    definition_curation_release_sha256: Sha256
    assignment_review_criteria: Annotated[
        tuple[ShortText, ...], Field(min_length=3, max_length=12)
    ]
    exact_candidate_universe_required: Literal[True] = True
    exact_case_evidence_required: Literal[True] = True
    independent_reviewer_count: Literal[2] = 2
    adjudication_required_on_disagreement: Literal[True] = True
    alternate_lineage_requires_new_preregistered_universe: Literal[True] = True
    sealed_at: Annotated[str, Field(min_length=20, max_length=40)]
    private_custody_required: Literal[True] = True
    public_release_allowed: Literal[False] = False
    scientific_conclusion: Literal[False] = False

    @field_validator("sealed_at")
    @classmethod
    def validate_time(cls, value: str) -> str:
        return _require_rfc3339(value)

    @model_validator(mode="after")
    def validate_policy(self) -> MechanismLineageAssignmentCurationPolicyV3:
        if self.assignment_review_criteria != tuple(
            sorted(set(self.assignment_review_criteria))
        ):
            raise ValueError("lineage-assignment criteria must be sorted and unique")
        _assert_addressed_v2(
            self,
            id_field="policy_id",
            sha_field="policy_sha256",
            prefix="lineage-assignment-policy-v3",
        )
        return self


class MechanismLineageAssignmentReviewerRosterV3(StrictModel):
    """Private roster proving two reviewers and a distinct natural-person adjudicator."""

    schema_version: Literal[
        "flatband-mechanism-lineage-assignment-reviewer-roster-v3"
    ] = "flatband-mechanism-lineage-assignment-reviewer-roster-v3"
    roster_id: Identifier
    roster_sha256: Sha256
    policy_id: Identifier
    policy_sha256: Sha256
    reviewers: Annotated[
        tuple[MechanismLineageCuratorDeclarationV3, ...],
        Field(min_length=2, max_length=2),
    ]
    adjudicators: Annotated[
        tuple[MechanismLineageCuratorDeclarationV3, ...],
        Field(min_length=1, max_length=8),
    ]
    independence_review: ShortText
    sealed_at: Annotated[str, Field(min_length=20, max_length=40)]
    private_custody_required: Literal[True] = True
    public_release_allowed: Literal[False] = False
    scientific_conclusion: Literal[False] = False

    @field_validator("sealed_at")
    @classmethod
    def validate_time(cls, value: str) -> str:
        return _require_rfc3339(value)

    @model_validator(mode="after")
    def validate_roster(self) -> MechanismLineageAssignmentReviewerRosterV3:
        reviewer_ids = tuple(item.curator_id for item in self.reviewers)
        adjudicator_ids = tuple(item.curator_id for item in self.adjudicators)
        if reviewer_ids != tuple(sorted(set(reviewer_ids))):
            raise ValueError("lineage-assignment reviewers must be sorted and unique")
        if adjudicator_ids != tuple(sorted(set(adjudicator_ids))):
            raise ValueError("lineage-assignment adjudicators must be sorted and unique")
        if set(reviewer_ids) & set(adjudicator_ids):
            raise ValueError(
                "lineage-assignment adjudicator must be distinct from reviewers"
            )
        people = (*self.reviewers, *self.adjudicators)
        for field_name in (
            "opaque_natural_person_ref",
            "natural_person_commitment_sha256",
            "identity_evidence_uri",
            "identity_evidence_sha256",
        ):
            values = tuple(getattr(item, field_name) for item in people)
            if len(values) != len(set(values)):
                raise ValueError(
                    "lineage-assignment natural-person bindings must be injective"
                )
        _assert_addressed_v2(
            self,
            id_field="roster_id",
            sha_field="roster_sha256",
            prefix="lineage-assignment-roster-v3",
        )
        return self


class MechanismLineageAssignmentProposalV3(StrictModel):
    """Private, prereview proposal for exactly one frozen candidate."""

    schema_version: Literal[
        "flatband-mechanism-lineage-assignment-proposal-v3"
    ] = "flatband-mechanism-lineage-assignment-proposal-v3"
    proposal_id: Identifier
    proposal_sha256: Sha256
    registry_id: Identifier
    registry_sha256: Sha256
    definition_curation_release_id: Identifier
    definition_curation_release_sha256: Sha256
    candidate_id: Identifier
    candidate_sha256: Sha256
    case: FlatBandBenchmarkCaseV1
    lineage_id: Identifier
    lineage_sha256: Sha256
    case_evidence_refs: Annotated[
        tuple[MechanismLineageEvidenceRefV3, ...], Field(min_length=1, max_length=16)
    ]
    assignment_basis_sha256: Sha256
    proposed_at: Annotated[str, Field(min_length=20, max_length=40)]
    private_custody_required: Literal[True] = True
    public_release_allowed: Literal[False] = False
    scientific_conclusion: Literal[False] = False

    @field_validator("proposed_at")
    @classmethod
    def validate_time(cls, value: str) -> str:
        return _require_rfc3339(value)

    @model_validator(mode="after")
    def validate_proposal(self) -> MechanismLineageAssignmentProposalV3:
        evidence_keys = tuple(
            (item.source_id, item.source_record_id, item.source_record_raw_sha256)
            for item in self.case_evidence_refs
        )
        if evidence_keys != tuple(sorted(set(evidence_keys))):
            raise ValueError(
                "lineage-assignment proposal evidence must be sorted and unique"
            )
        _assert_addressed_v2(
            self,
            id_field="proposal_id",
            sha_field="proposal_sha256",
            prefix="lineage-assignment-proposal-v3",
        )
        return self


class MechanismLineageAssignmentCandidateUniverseV3(StrictModel):
    """Private exact preimage of every candidate assignment proposed before review."""

    schema_version: Literal[
        "flatband-mechanism-lineage-assignment-candidate-universe-v3"
    ] = "flatband-mechanism-lineage-assignment-candidate-universe-v3"
    universe_id: Identifier
    universe_sha256: Sha256
    registry_id: Identifier
    registry_sha256: Sha256
    definition_curation_release_id: Identifier
    definition_curation_release_sha256: Sha256
    policy_id: Identifier
    policy_sha256: Sha256
    roster_id: Identifier
    roster_sha256: Sha256
    proposals: Annotated[
        tuple[MechanismLineageAssignmentProposalV3, ...],
        Field(min_length=1, max_length=2_040),
    ]
    sealed_at: Annotated[str, Field(min_length=20, max_length=40)]
    private_custody_required: Literal[True] = True
    public_release_allowed: Literal[False] = False
    post_review_proposal_allowed: Literal[False] = False
    scientific_conclusion: Literal[False] = False

    @field_validator("sealed_at")
    @classmethod
    def validate_time(cls, value: str) -> str:
        return _require_rfc3339(value)

    @model_validator(mode="after")
    def validate_universe(self) -> MechanismLineageAssignmentCandidateUniverseV3:
        order = tuple(
            (item.candidate_id, item.case.case_id, item.proposal_id)
            for item in self.proposals
        )
        if order != tuple(sorted(set(order))):
            raise ValueError("lineage-assignment proposals must be sorted and unique")
        if len({item.candidate_id for item in self.proposals}) != len(self.proposals):
            raise ValueError("candidate universe repeats a candidate")
        if len({item.case.case_id for item in self.proposals}) != len(self.proposals):
            raise ValueError("candidate universe repeats a scientific case")
        _assert_addressed_v2(
            self,
            id_field="universe_id",
            sha_field="universe_sha256",
            prefix="lineage-assignment-universe-v3",
        )
        return self


class MechanismLineageAssignmentReviewV3(StrictModel):
    schema_version: Literal[
        "flatband-mechanism-lineage-assignment-review-v3"
    ] = "flatband-mechanism-lineage-assignment-review-v3"
    review_id: Identifier
    review_sha256: Sha256
    policy_id: Identifier
    policy_sha256: Sha256
    roster_id: Identifier
    roster_sha256: Sha256
    universe_id: Identifier
    universe_sha256: Sha256
    proposal_id: Identifier
    proposal_sha256: Sha256
    reviewer_id: Identifier
    decision: MechanismLineageAssignmentDecisionV3
    criterion_findings: Annotated[
        tuple[ShortText, ...], Field(min_length=3, max_length=12)
    ]
    rationale: ShortText
    reviewed_at: Annotated[str, Field(min_length=20, max_length=40)]
    private_custody_required: Literal[True] = True
    public_release_allowed: Literal[False] = False
    scientific_conclusion: Literal[False] = False

    @field_validator("reviewed_at")
    @classmethod
    def validate_time(cls, value: str) -> str:
        return _require_rfc3339(value)

    @model_validator(mode="after")
    def validate_review(self) -> MechanismLineageAssignmentReviewV3:
        if self.criterion_findings != tuple(sorted(set(self.criterion_findings))):
            raise ValueError(
                "lineage-assignment review findings must be sorted and unique"
            )
        _assert_addressed_v2(
            self,
            id_field="review_id",
            sha_field="review_sha256",
            prefix="lineage-assignment-review-v3",
        )
        return self


class MechanismLineageAssignmentAdjudicationV3(StrictModel):
    schema_version: Literal[
        "flatband-mechanism-lineage-assignment-adjudication-v3"
    ] = "flatband-mechanism-lineage-assignment-adjudication-v3"
    adjudication_id: Identifier
    adjudication_sha256: Sha256
    policy_id: Identifier
    policy_sha256: Sha256
    roster_id: Identifier
    roster_sha256: Sha256
    universe_id: Identifier
    universe_sha256: Sha256
    proposal_id: Identifier
    proposal_sha256: Sha256
    review_refs: Annotated[
        tuple[tuple[Identifier, Sha256], ...], Field(min_length=2, max_length=2)
    ]
    adjudicator_id: Identifier
    final_decision: MechanismLineageAssignmentDecisionV3
    rationale: ShortText
    adjudicated_at: Annotated[str, Field(min_length=20, max_length=40)]
    private_custody_required: Literal[True] = True
    public_release_allowed: Literal[False] = False
    scientific_conclusion: Literal[False] = False

    @field_validator("adjudicated_at")
    @classmethod
    def validate_time(cls, value: str) -> str:
        return _require_rfc3339(value)

    @model_validator(mode="after")
    def validate_adjudication(self) -> MechanismLineageAssignmentAdjudicationV3:
        if self.review_refs != tuple(sorted(set(self.review_refs))):
            raise ValueError(
                "lineage-assignment adjudication review refs must be sorted and unique"
            )
        _assert_addressed_v2(
            self,
            id_field="adjudication_id",
            sha_field="adjudication_sha256",
            prefix="lineage-assign-adjud-v3",
        )
        return self


class MechanismLineageAssignmentReviewManifestV3(StrictModel):
    schema_version: Literal[
        "flatband-mechanism-lineage-assignment-review-manifest-v3"
    ] = "flatband-mechanism-lineage-assignment-review-manifest-v3"
    manifest_id: Identifier
    manifest_sha256: Sha256
    policy_id: Identifier
    policy_sha256: Sha256
    roster_id: Identifier
    roster_sha256: Sha256
    universe_id: Identifier
    universe_sha256: Sha256
    reviews: Annotated[
        tuple[MechanismLineageAssignmentReviewV3, ...],
        Field(min_length=2, max_length=4_080),
    ]
    adjudications: Annotated[
        tuple[MechanismLineageAssignmentAdjudicationV3, ...],
        Field(max_length=2_040),
    ] = ()
    sealed_at: Annotated[str, Field(min_length=20, max_length=40)]
    private_custody_required: Literal[True] = True
    public_release_allowed: Literal[False] = False
    scientific_conclusion: Literal[False] = False

    @field_validator("sealed_at")
    @classmethod
    def validate_time(cls, value: str) -> str:
        return _require_rfc3339(value)

    @model_validator(mode="after")
    def validate_manifest(self) -> MechanismLineageAssignmentReviewManifestV3:
        review_order = tuple(
            (item.proposal_id, item.reviewer_id, item.review_id)
            for item in self.reviews
        )
        if review_order != tuple(sorted(set(review_order))):
            raise ValueError(
                "lineage-assignment reviews must be proposal/reviewer sorted and unique"
            )
        reviews_by_proposal: dict[
            str, list[MechanismLineageAssignmentReviewV3]
        ] = defaultdict(list)
        for review in self.reviews:
            reviews_by_proposal[review.proposal_id].append(review)
        if any(
            len(items) != 2 or len({item.reviewer_id for item in items}) != 2
            for items in reviews_by_proposal.values()
        ):
            raise ValueError(
                "every lineage-assignment proposal requires exactly two reviewers"
            )
        adjudication_order = tuple(
            (item.proposal_id, item.adjudication_id) for item in self.adjudications
        )
        if adjudication_order != tuple(sorted(set(adjudication_order))):
            raise ValueError(
                "lineage-assignment adjudications must be proposal-sorted and unique"
            )
        if len({item.proposal_id for item in self.adjudications}) != len(
            self.adjudications
        ):
            raise ValueError("lineage-assignment proposal has multiple adjudications")
        adjudication_by_proposal = {
            item.proposal_id: item for item in self.adjudications
        }
        for proposal_id, reviews in reviews_by_proposal.items():
            decisions = {item.decision for item in reviews}
            adjudication = adjudication_by_proposal.get(proposal_id)
            if len(decisions) == 1:
                if adjudication is not None:
                    raise ValueError(
                        "agreed lineage-assignment reviews cannot be adjudicated"
                    )
                continue
            if adjudication is None:
                raise ValueError(
                    "disagreed lineage-assignment reviews require adjudication"
                )
            if (
                adjudication.proposal_sha256,
                adjudication.review_refs,
            ) != (
                reviews[0].proposal_sha256,
                tuple(sorted((item.review_id, item.review_sha256) for item in reviews)),
            ):
                raise ValueError(
                    "lineage-assignment adjudication does not bind exact raw reviews"
                )
        if set(adjudication_by_proposal) - set(reviews_by_proposal):
            raise ValueError("lineage-assignment adjudication has no raw reviews")
        sealed = datetime.fromisoformat(self.sealed_at)
        if any(
            sealed < datetime.fromisoformat(item.reviewed_at)
            for item in self.reviews
        ) or any(
            sealed
            < datetime.fromisoformat(item.adjudicated_at)
            for item in self.adjudications
        ):
            raise ValueError("lineage-assignment manifest predates a decision")
        _assert_addressed_v2(
            self,
            id_field="manifest_id",
            sha_field="manifest_sha256",
            prefix="lineage-assign-review-set-v3",
        )
        return self


class MechanismLineageAssignmentCurationReleaseV3(StrictModel):
    """Private root whose accepted decisions uniquely project public assignments."""

    schema_version: Literal[
        "flatband-mechanism-lineage-assignment-curation-release-v3"
    ] = "flatband-mechanism-lineage-assignment-curation-release-v3"
    release_id: Identifier
    release_sha256: Sha256
    registry_id: Identifier
    registry_sha256: Sha256
    definition_curation_release_id: Identifier
    definition_curation_release_sha256: Sha256
    policy: MechanismLineageAssignmentCurationPolicyV3
    roster: MechanismLineageAssignmentReviewerRosterV3
    candidate_universe: MechanismLineageAssignmentCandidateUniverseV3
    review_manifest: MechanismLineageAssignmentReviewManifestV3
    assembled_at: Annotated[str, Field(min_length=20, max_length=40)]
    private_custody_required: Literal[True] = True
    public_release_allowed: Literal[False] = False
    external_timestamp_attestation_claimed: Literal[False] = False
    scientific_conclusion: Literal[False] = False

    @field_validator("assembled_at")
    @classmethod
    def validate_time(cls, value: str) -> str:
        return _require_rfc3339(value)

    @model_validator(mode="after")
    def validate_release(self) -> MechanismLineageAssignmentCurationReleaseV3:
        if (
            self.policy.registry_id,
            self.policy.registry_sha256,
            self.policy.definition_curation_release_id,
            self.policy.definition_curation_release_sha256,
        ) != (
            self.registry_id,
            self.registry_sha256,
            self.definition_curation_release_id,
            self.definition_curation_release_sha256,
        ):
            raise ValueError("lineage-assignment policy binds foreign definition roots")
        if (self.roster.policy_id, self.roster.policy_sha256) != (
            self.policy.policy_id,
            self.policy.policy_sha256,
        ):
            raise ValueError("lineage-assignment roster binds a foreign policy")
        expected_universe_refs = (
            self.registry_id,
            self.registry_sha256,
            self.definition_curation_release_id,
            self.definition_curation_release_sha256,
            self.policy.policy_id,
            self.policy.policy_sha256,
            self.roster.roster_id,
            self.roster.roster_sha256,
        )
        if (
            self.candidate_universe.registry_id,
            self.candidate_universe.registry_sha256,
            self.candidate_universe.definition_curation_release_id,
            self.candidate_universe.definition_curation_release_sha256,
            self.candidate_universe.policy_id,
            self.candidate_universe.policy_sha256,
            self.candidate_universe.roster_id,
            self.candidate_universe.roster_sha256,
        ) != expected_universe_refs:
            raise ValueError("lineage-assignment universe binds foreign governance")
        if (
            self.review_manifest.policy_id,
            self.review_manifest.policy_sha256,
            self.review_manifest.roster_id,
            self.review_manifest.roster_sha256,
            self.review_manifest.universe_id,
            self.review_manifest.universe_sha256,
        ) != (
            self.policy.policy_id,
            self.policy.policy_sha256,
            self.roster.roster_id,
            self.roster.roster_sha256,
            self.candidate_universe.universe_id,
            self.candidate_universe.universe_sha256,
        ):
            raise ValueError("lineage-assignment manifest binds foreign governance")
        assembled = datetime.fromisoformat(self.assembled_at)
        if assembled <= datetime.fromisoformat(
            self.review_manifest.sealed_at
        ):
            raise ValueError("lineage-assignment release does not follow its manifest")
        _assert_addressed_v2(
            self,
            id_field="release_id",
            sha_field="release_sha256",
            prefix="lineage-assign-curation-v3",
        )
        return self


class LeakageGroupDefinitionV3(StrictModel):
    group_id: Identifier
    definition_sha256: Sha256
    axis: LeakageAxisV3
    canonical_preimage: Annotated[str, Field(min_length=2, max_length=4_096)]

    @model_validator(mode="after")
    def validate_definition(self) -> LeakageGroupDefinitionV3:
        try:
            decoded = json.loads(self.canonical_preimage)
        except (TypeError, ValueError) as exc:
            raise ValueError("V3 leakage preimage must be canonical JSON") from exc
        if (
            not isinstance(decoded, dict)
            or canonical_json_bytes(decoded).decode("utf-8") != self.canonical_preimage
        ):
            raise ValueError("V3 leakage preimage must be a canonical JSON object")
        expected_keys = {
            LeakageAxisV3.COMPOSITION_FAMILY: {"kind", "stoichiometry"},
            LeakageAxisV3.ARTICLE_OR_SOURCE_FAMILY: {
                "kind",
                "source_id",
                "source_record_id",
            },
            LeakageAxisV3.STRUCTURE_PROTOTYPE: {
                "kind",
                "axis",
                "algorithm_id",
                "algorithm_sha256",
                "canonical_group_key",
            },
            LeakageAxisV3.STRUCTURE_FINGERPRINT: {
                "kind",
                "axis",
                "algorithm_id",
                "algorithm_sha256",
                "canonical_group_key",
            },
            LeakageAxisV3.MECHANISM_LINEAGE: {
                "kind",
                "registry_id",
                "registry_sha256",
                "lineage_id",
                "lineage_sha256",
                "scientific_preimage_sha256",
            },
        }[self.axis]
        if set(decoded) != expected_keys:
            raise ValueError("V3 leakage preimage fields differ from its axis")
        expected_kind = {
            LeakageAxisV3.COMPOSITION_FAMILY: "composition",
            LeakageAxisV3.ARTICLE_OR_SOURCE_FAMILY: "source-record",
            LeakageAxisV3.STRUCTURE_PROTOTYPE: "structure-group",
            LeakageAxisV3.STRUCTURE_FINGERPRINT: "structure-group",
            LeakageAxisV3.MECHANISM_LINEAGE: "mechanism-lineage",
        }[self.axis]
        if decoded.get("kind") != expected_kind:
            raise ValueError("V3 leakage preimage kind differs from its axis")
        if self.axis in STRUCTURE_LEAKAGE_AXES_V3 and decoded.get("axis") != self.axis.value:
            raise ValueError("V3 structure preimage axis differs from definition")
        semantic = self.model_dump(
            mode="python", exclude={"group_id", "definition_sha256"}
        )
        if self.definition_sha256 != canonical_sha256(semantic):
            raise ValueError("V3 leakage group definition SHA-256 differs")
        expected_group_id = deterministic_id(
            "leakage-group-v3",
            {"axis": self.axis.value, "canonical_preimage": self.canonical_preimage},
        )
        if self.group_id != expected_group_id:
            raise ValueError("V3 leakage group ID differs from canonical preimage")
        return self


class LeakageMembershipV3(StrictModel):
    case_id: Identifier
    case_sha256: Sha256
    axis: LeakageAxisV3
    group_id: Identifier
    group_definition_sha256: Sha256
    structure_assignment_id: Identifier | None = None
    structure_assignment_sha256: Sha256 | None = None
    mechanism_lineage_assignment_id: Identifier | None = None
    mechanism_lineage_assignment_sha256: Sha256 | None = None
    provenance_sha256: Sha256

    @model_validator(mode="after")
    def validate_membership(self) -> LeakageMembershipV3:
        structure_ref = (
            self.structure_assignment_id is not None,
            self.structure_assignment_sha256 is not None,
        )
        lineage_ref = (
            self.mechanism_lineage_assignment_id is not None,
            self.mechanism_lineage_assignment_sha256 is not None,
        )
        if len(set(structure_ref)) != 1 or len(set(lineage_ref)) != 1:
            raise ValueError("V3 assignment ID and SHA must be present together")
        if (self.axis in STRUCTURE_LEAKAGE_AXES_V3) != all(structure_ref):
            raise ValueError("only V3 structure memberships may cite structure assignment")
        if (self.axis is LeakageAxisV3.MECHANISM_LINEAGE) != all(lineage_ref):
            raise ValueError("only mechanism lineage membership may cite lineage assignment")
        if any(structure_ref) and any(lineage_ref):
            raise ValueError("one V3 membership cannot cite two assignment kinds")
        return self


class LeakageComponentReleaseV3(StrictModel):
    """Formal non-contradictory leakage release for Pilot and Main."""

    schema_version: Literal["flatband-leakage-component-release-v3"] = (
        "flatband-leakage-component-release-v3"
    )
    release_id: Identifier
    release_sha256: Sha256
    split_manifest_id: Identifier
    split_manifest_sha256: Sha256
    full_case_universe_sha256: Sha256
    grouping_algorithms: Annotated[
        tuple[StructureGroupingAlgorithmV2, ...], Field(min_length=2, max_length=2)
    ]
    grouping_runs: Annotated[
        tuple[StructureGroupingRunV2, ...], Field(min_length=2, max_length=2)
    ]
    grouping_assignments: Annotated[
        tuple[StructureGroupingAssignmentV2, ...], Field(min_length=60, max_length=240)
    ]
    mechanism_lineage_registry: MechanismLineageRegistryV3
    mechanism_lineage_assignments: Annotated[
        tuple[MechanismLineageAssignmentV3, ...], Field(min_length=30, max_length=120)
    ]
    group_definitions: Annotated[
        tuple[LeakageGroupDefinitionV3, ...], Field(min_length=1, max_length=20_000)
    ]
    memberships: Annotated[
        tuple[LeakageMembershipV3, ...], Field(min_length=1, max_length=20_000)
    ]
    components: Annotated[
        tuple[LeakageComponentV1, ...], Field(min_length=1, max_length=120)
    ]
    created_at: Annotated[str, Field(min_length=20, max_length=40)]
    broad_mechanism_component_edges_allowed: Literal[False] = False
    caller_supplied_memberships_allowed: Literal[False] = False
    canonical_work_registry_id: Literal[None] = None  # noqa: PYI061 -- schema const
    uncanonicalized_cross_source_doi_alias_count: Literal[0] = 0
    scientific_conclusion: Literal[False] = False

    @field_validator("created_at")
    @classmethod
    def validate_created_at(cls, value: str) -> str:
        return _require_rfc3339(value)

    @model_validator(mode="after")
    def validate_release(self) -> LeakageComponentReleaseV3:
        algorithm_axes = tuple(item.axis.value for item in self.grouping_algorithms)
        expected_axes = tuple(sorted(axis.value for axis in STRUCTURE_LEAKAGE_AXES))
        if algorithm_axes != expected_axes:
            raise ValueError("V3 requires both sorted structure algorithms")
        if tuple(item.axis.value for item in self.grouping_runs) != expected_axes:
            raise ValueError("V3 requires both sorted structure runs")
        assignment_keys = tuple(
            (item.axis.value, item.case_id, item.assignment_id)
            for item in self.grouping_assignments
        )
        if assignment_keys != tuple(sorted(assignment_keys)) or len(
            {(axis, case_id) for axis, case_id, _ in assignment_keys}
        ) != len(assignment_keys):
            raise ValueError("V3 structure assignments must be sorted and exact")
        lineage_keys = tuple(
            (item.case_id, item.assignment_id)
            for item in self.mechanism_lineage_assignments
        )
        if lineage_keys != tuple(sorted(set(lineage_keys))):
            raise ValueError("V3 lineage assignments must be case-sorted and unique")
        if any(
            (item.registry_id, item.registry_sha256)
            != (
                self.mechanism_lineage_registry.registry_id,
                self.mechanism_lineage_registry.registry_sha256,
            )
            for item in self.mechanism_lineage_assignments
        ):
            raise ValueError("V3 lineage assignment references a foreign registry")
        definition_keys = tuple(
            (item.axis.value, item.group_id, item.definition_sha256)
            for item in self.group_definitions
        )
        if definition_keys != tuple(sorted(set(definition_keys))):
            raise ValueError("V3 group definitions must be sorted and unique")
        if len({item.group_id for item in self.group_definitions}) != len(
            self.group_definitions
        ):
            raise ValueError("V3 group IDs must be globally unique")
        membership_keys = tuple(
            (item.case_id, item.axis.value, item.group_id, item.provenance_sha256)
            for item in self.memberships
        )
        if membership_keys != tuple(sorted(set(membership_keys))):
            raise ValueError("V3 memberships must be key-sorted and unique")
        definitions = {item.group_id: item for item in self.group_definitions}
        for membership in self.memberships:
            definition = definitions.get(membership.group_id)
            if definition is None or (
                definition.axis,
                definition.definition_sha256,
            ) != (membership.axis, membership.group_definition_sha256):
                raise ValueError("V3 membership does not bind its group definition")
        component_ids = tuple(item.component_id for item in self.components)
        if component_ids != tuple(sorted(set(component_ids))):
            raise ValueError("V3 components must be component-ID sorted and unique")
        if self.components != _components_from_memberships_v3(self.memberships):
            raise ValueError("V3 components do not replay from memberships")
        created = datetime.fromisoformat(self.created_at)
        if any(
            created < datetime.fromisoformat(item.completed_at)
            for item in self.grouping_runs
        ):
            raise ValueError("V3 release predates a structure grouping run")
        if created < datetime.fromisoformat(
            self.mechanism_lineage_registry.sealed_at
        ) or any(
            created < datetime.fromisoformat(item.assigned_at)
            for item in self.mechanism_lineage_assignments
        ):
            raise ValueError("V3 release predates frozen mechanism lineage artifacts")
        _assert_addressed_v2(
            self,
            id_field="release_id",
            sha_field="release_sha256",
            prefix="leakage-release-v3",
        )
        return self


class LeakageRoundClosureContextV3(StrictModel):
    """Exact inputs required before a persisted round graph may enter union."""

    cases: Annotated[
        tuple[FlatBandBenchmarkCaseV1, ...], Field(min_length=30, max_length=120)
    ]
    split_manifest: BenchmarkSplitManifestV2
    release: LeakageComponentReleaseV3


class LeakageUnsplitCaseUniverseContextV3(StrictModel):
    """Derivation context for CandidatePool/calibration cases before selection.

    The owning cases/top-level verifier must prove that ``cases`` exactly
    project ``source_artifact_id/sha256``.  This module then derives every typed
    membership from the authoritative structure and lineage artifacts.
    """

    universe_id: Identifier
    source_artifact_id: Identifier
    source_artifact_sha256: Sha256
    cases: Annotated[
        tuple[FlatBandBenchmarkCaseV1, ...], Field(min_length=1, max_length=512)
    ]
    grouping_algorithms: Annotated[
        tuple[StructureGroupingAlgorithmV2, ...], Field(min_length=2, max_length=2)
    ]
    grouping_runs: Annotated[
        tuple[StructureGroupingRunV2, ...], Field(min_length=2, max_length=2)
    ]
    grouping_assignments: Annotated[
        tuple[StructureGroupingAssignmentV2, ...], Field(min_length=2, max_length=1_024)
    ]
    mechanism_lineage_registry: MechanismLineageRegistryV3
    mechanism_lineage_assignments: Annotated[
        tuple[MechanismLineageAssignmentV3, ...], Field(min_length=1, max_length=512)
    ]


def build_mechanism_lineage_definition_v3(
    *,
    broad_mechanism_family: MechanismFamily,
    source_mechanism: str,
    shared_invariant: str,
    transfer_route_family: str,
    taxonomy_evidence_refs: tuple[MechanismLineageEvidenceRefV3, ...],
) -> MechanismLineageDefinitionV3:
    refs = tuple(
        sorted(
            (
                MechanismLineageEvidenceRefV3.model_validate(
                    item.model_dump(mode="python", round_trip=True)
                )
                for item in taxonomy_evidence_refs
            ),
            key=lambda item: (
                item.source_id,
                item.source_record_id,
                item.source_record_raw_sha256,
            ),
        )
    )
    scientific_preimage = {
        "broad_mechanism_family": broad_mechanism_family.value,
        "source_mechanism": _canonical_scientific_text_v3(source_mechanism),
        "shared_invariant": _canonical_scientific_text_v3(shared_invariant),
        "transfer_route_family": _canonical_scientific_text_v3(
            transfer_route_family
        ),
    }
    values = {
        "scientific_preimage_sha256": canonical_sha256(scientific_preimage),
        "broad_mechanism_family": broad_mechanism_family,
        "source_mechanism": source_mechanism,
        "shared_invariant": shared_invariant,
        "transfer_route_family": transfer_route_family,
        "taxonomy_evidence_refs": refs,
    }
    draft = MechanismLineageDefinitionV3.model_construct(**values)
    digest = canonical_sha256(
        draft.model_dump(mode="python", exclude={"lineage_id", "lineage_sha256"})
    )
    return MechanismLineageDefinitionV3.model_validate(
        {
            **values,
            "lineage_sha256": digest,
            "lineage_id": deterministic_id(
                "mechanism-lineage-v3", {"lineage_sha256": digest}
            ),
        }
    )


def build_mechanism_lineage_registry_v3(
    *,
    taxonomy_version: str,
    curation_policy_sha256: str,
    evidence_review_manifest_sha256: str,
    curator_roster_sha256: str,
    definitions: tuple[MechanismLineageDefinitionV3, ...],
    sealed_at: str,
) -> MechanismLineageRegistryV3:
    ordered = tuple(
        sorted(
            (
                MechanismLineageDefinitionV3.model_validate(
                    item.model_dump(mode="python", round_trip=True)
                )
                for item in definitions
            ),
            key=lambda item: (item.lineage_id, item.lineage_sha256),
        )
    )
    values = {
        "taxonomy_version": taxonomy_version,
        "curation_policy_sha256": curation_policy_sha256,
        "evidence_review_manifest_sha256": evidence_review_manifest_sha256,
        "curator_roster_sha256": curator_roster_sha256,
        "definitions": ordered,
        "sealed_at": _require_rfc3339(sealed_at),
    }
    draft = MechanismLineageRegistryV3.model_construct(**values)
    digest = canonical_sha256(
        draft.model_dump(mode="python", exclude={"registry_id", "registry_sha256"})
    )
    return MechanismLineageRegistryV3.model_validate(
        {
            **values,
            "registry_sha256": digest,
            "registry_id": deterministic_id(
                "mechanism-lineage-registry-v3", {"registry_sha256": digest}
            ),
        }
    )


def build_mechanism_lineage_curation_policy_v3(
    *,
    taxonomy_version: str,
    taxonomy_scope: str,
    definition_review_criteria: tuple[str, ...],
    sealed_at: str,
) -> MechanismLineageCurationPolicyV3:
    criteria = tuple(sorted(set(definition_review_criteria)))
    values = {
        "taxonomy_version": taxonomy_version,
        "taxonomy_scope": taxonomy_scope,
        "definition_review_criteria": criteria,
        "sealed_at": _require_rfc3339(sealed_at),
    }
    draft = MechanismLineageCurationPolicyV3.model_construct(**values)
    digest = canonical_sha256(
        draft.model_dump(mode="python", exclude={"policy_id", "policy_sha256"})
    )
    return MechanismLineageCurationPolicyV3.model_validate(
        {
            **values,
            "policy_sha256": digest,
            "policy_id": deterministic_id(
                "lineage-curation-policy-v3",
                {"policy_sha256": digest},
            ),
        }
    )


def build_mechanism_lineage_curator_roster_v3(
    *,
    policy: MechanismLineageCurationPolicyV3,
    curators: tuple[MechanismLineageCuratorDeclarationV3, ...],
    adjudicators: tuple[MechanismLineageCuratorDeclarationV3, ...],
    independence_review: str,
    sealed_at: str,
) -> MechanismLineageCuratorRosterV3:
    policy_value = MechanismLineageCurationPolicyV3.model_validate(
        policy.model_dump(mode="python", round_trip=True)
    )
    ordered_curators = tuple(
        sorted(
            (
                MechanismLineageCuratorDeclarationV3.model_validate(
                    item.model_dump(mode="python", round_trip=True)
                )
                for item in curators
            ),
            key=lambda item: item.curator_id,
        )
    )
    ordered_adjudicators = tuple(
        sorted(
            (
                MechanismLineageCuratorDeclarationV3.model_validate(
                    item.model_dump(mode="python", round_trip=True)
                )
                for item in adjudicators
            ),
            key=lambda item: item.curator_id,
        )
    )
    values = {
        "policy_id": policy_value.policy_id,
        "policy_sha256": policy_value.policy_sha256,
        "curators": ordered_curators,
        "adjudicators": ordered_adjudicators,
        "independence_review": independence_review,
        "sealed_at": _require_rfc3339(sealed_at),
    }
    draft = MechanismLineageCuratorRosterV3.model_construct(**values)
    digest = canonical_sha256(
        draft.model_dump(mode="python", exclude={"roster_id", "roster_sha256"})
    )
    return MechanismLineageCuratorRosterV3.model_validate(
        {
            **values,
            "roster_sha256": digest,
            "roster_id": deterministic_id(
                "lineage-curator-roster-v3",
                {"roster_sha256": digest},
            ),
        }
    )


def build_mechanism_lineage_definition_review_v3(
    *,
    policy: MechanismLineageCurationPolicyV3,
    definition: MechanismLineageDefinitionV3,
    curator_id: str,
    decision: MechanismLineageReviewDecisionV3,
    criterion_findings: tuple[str, ...],
    rationale: str,
    reviewed_at: str,
) -> MechanismLineageDefinitionReviewV3:
    policy_value = MechanismLineageCurationPolicyV3.model_validate(
        policy.model_dump(mode="python", round_trip=True)
    )
    definition_value = MechanismLineageDefinitionV3.model_validate(
        definition.model_dump(mode="python", round_trip=True)
    )
    values = {
        "policy_id": policy_value.policy_id,
        "policy_sha256": policy_value.policy_sha256,
        "lineage_id": definition_value.lineage_id,
        "lineage_sha256": definition_value.lineage_sha256,
        "scientific_preimage_sha256": (
            definition_value.scientific_preimage_sha256
        ),
        "taxonomy_evidence_refs": definition_value.taxonomy_evidence_refs,
        "curator_id": curator_id,
        "decision": decision,
        "criterion_findings": tuple(sorted(set(criterion_findings))),
        "rationale": rationale,
        "reviewed_at": _require_rfc3339(reviewed_at),
    }
    draft = MechanismLineageDefinitionReviewV3.model_construct(**values)
    digest = canonical_sha256(
        draft.model_dump(mode="python", exclude={"review_id", "review_sha256"})
    )
    return MechanismLineageDefinitionReviewV3.model_validate(
        {
            **values,
            "review_sha256": digest,
            "review_id": deterministic_id(
                "lineage-definition-review-v3",
                {"review_sha256": digest},
            ),
        }
    )


def build_mechanism_lineage_definition_adjudication_v3(
    *,
    policy: MechanismLineageCurationPolicyV3,
    definition: MechanismLineageDefinitionV3,
    reviews: tuple[MechanismLineageDefinitionReviewV3, ...],
    adjudicator_id: str,
    final_decision: MechanismLineageReviewDecisionV3,
    rationale: str,
    adjudicated_at: str,
) -> MechanismLineageDefinitionAdjudicationV3:
    policy_value = MechanismLineageCurationPolicyV3.model_validate(
        policy.model_dump(mode="python", round_trip=True)
    )
    definition_value = MechanismLineageDefinitionV3.model_validate(
        definition.model_dump(mode="python", round_trip=True)
    )
    review_values = tuple(
        MechanismLineageDefinitionReviewV3.model_validate(
            item.model_dump(mode="python", round_trip=True)
        )
        for item in reviews
    )
    if len(review_values) != 2 or any(
        (
            item.policy_id,
            item.policy_sha256,
            item.lineage_id,
            item.lineage_sha256,
        )
        != (
            policy_value.policy_id,
            policy_value.policy_sha256,
            definition_value.lineage_id,
            definition_value.lineage_sha256,
        )
        for item in review_values
    ):
        raise ValueError("lineage adjudication requires two exact definition reviews")
    values = {
        "policy_id": policy_value.policy_id,
        "policy_sha256": policy_value.policy_sha256,
        "lineage_id": definition_value.lineage_id,
        "lineage_sha256": definition_value.lineage_sha256,
        "review_refs": tuple(
            sorted((item.review_id, item.review_sha256) for item in review_values)
        ),
        "adjudicator_id": adjudicator_id,
        "final_decision": final_decision,
        "rationale": rationale,
        "adjudicated_at": _require_rfc3339(adjudicated_at),
    }
    draft = MechanismLineageDefinitionAdjudicationV3.model_construct(**values)
    digest = canonical_sha256(
        draft.model_dump(
            mode="python", exclude={"adjudication_id", "adjudication_sha256"}
        )
    )
    return MechanismLineageDefinitionAdjudicationV3.model_validate(
        {
            **values,
            "adjudication_sha256": digest,
            "adjudication_id": deterministic_id(
                "lineage-definition-adjud-v3",
                {"adjudication_sha256": digest},
            ),
        }
    )


def build_mechanism_lineage_evidence_review_manifest_v3(
    *,
    policy: MechanismLineageCurationPolicyV3,
    roster: MechanismLineageCuratorRosterV3,
    reviews: tuple[MechanismLineageDefinitionReviewV3, ...],
    adjudications: tuple[MechanismLineageDefinitionAdjudicationV3, ...] = (),
    sealed_at: str,
) -> MechanismLineageEvidenceReviewManifestV3:
    policy_value = MechanismLineageCurationPolicyV3.model_validate(
        policy.model_dump(mode="python", round_trip=True)
    )
    roster_value = MechanismLineageCuratorRosterV3.model_validate(
        roster.model_dump(mode="python", round_trip=True)
    )
    ordered_reviews = tuple(
        sorted(
            (
                MechanismLineageDefinitionReviewV3.model_validate(
                    item.model_dump(mode="python", round_trip=True)
                )
                for item in reviews
            ),
            key=lambda item: (item.lineage_id, item.curator_id, item.review_id),
        )
    )
    ordered_adjudications = tuple(
        sorted(
            (
                MechanismLineageDefinitionAdjudicationV3.model_validate(
                    item.model_dump(mode="python", round_trip=True)
                )
                for item in adjudications
            ),
            key=lambda item: (item.lineage_id, item.adjudication_id),
        )
    )
    values = {
        "policy_id": policy_value.policy_id,
        "policy_sha256": policy_value.policy_sha256,
        "roster_id": roster_value.roster_id,
        "roster_sha256": roster_value.roster_sha256,
        "reviews": ordered_reviews,
        "adjudications": ordered_adjudications,
        "sealed_at": _require_rfc3339(sealed_at),
    }
    draft = MechanismLineageEvidenceReviewManifestV3.model_construct(**values)
    digest = canonical_sha256(
        draft.model_dump(mode="python", exclude={"manifest_id", "manifest_sha256"})
    )
    return MechanismLineageEvidenceReviewManifestV3.model_validate(
        {
            **values,
            "manifest_sha256": digest,
            "manifest_id": deterministic_id(
                "lineage-review-manifest-v3",
                {"manifest_sha256": digest},
            ),
        }
    )


def build_mechanism_lineage_curation_release_v3(
    *,
    registry: MechanismLineageRegistryV3,
    policy: MechanismLineageCurationPolicyV3,
    roster: MechanismLineageCuratorRosterV3,
    review_manifest: MechanismLineageEvidenceReviewManifestV3,
    assembled_at: str,
) -> MechanismLineageCurationReleaseV3:
    registry_value = MechanismLineageRegistryV3.model_validate(
        registry.model_dump(mode="python", round_trip=True)
    )
    policy_value = MechanismLineageCurationPolicyV3.model_validate(
        policy.model_dump(mode="python", round_trip=True)
    )
    roster_value = MechanismLineageCuratorRosterV3.model_validate(
        roster.model_dump(mode="python", round_trip=True)
    )
    manifest_value = MechanismLineageEvidenceReviewManifestV3.model_validate(
        review_manifest.model_dump(mode="python", round_trip=True)
    )
    values = {
        "registry_id": registry_value.registry_id,
        "registry_sha256": registry_value.registry_sha256,
        "policy": policy_value,
        "roster": roster_value,
        "review_manifest": manifest_value,
        "assembled_at": _require_rfc3339(assembled_at),
    }
    draft = MechanismLineageCurationReleaseV3.model_construct(**values)
    digest = canonical_sha256(
        draft.model_dump(mode="python", exclude={"release_id", "release_sha256"})
    )
    release = MechanismLineageCurationReleaseV3.model_validate(
        {
            **values,
            "release_sha256": digest,
            "release_id": deterministic_id(
                "lineage-curation-release-v3",
                {"release_sha256": digest},
            ),
        }
    )
    assert_formal_mechanism_lineage_registry_v3(
        registry=registry_value, curation_release=release
    )
    return release


def assert_formal_mechanism_lineage_registry_v3(
    *,
    registry: MechanismLineageRegistryV3,
    curation_release: MechanismLineageCurationReleaseV3,
) -> None:
    """Replay private dual review against the public-safe registry projection."""

    registry_value = MechanismLineageRegistryV3.model_validate(
        registry.model_dump(mode="python", round_trip=True)
    )
    curation = MechanismLineageCurationReleaseV3.model_validate(
        curation_release.model_dump(mode="python", round_trip=True)
    )
    if (curation.registry_id, curation.registry_sha256) != (
        registry_value.registry_id,
        registry_value.registry_sha256,
    ):
        raise ValueError("lineage curation release binds a foreign public registry")
    if (
        registry_value.taxonomy_version,
        registry_value.curation_policy_sha256,
        registry_value.curator_roster_sha256,
        registry_value.evidence_review_manifest_sha256,
    ) != (
        curation.policy.taxonomy_version,
        curation.policy.policy_sha256,
        curation.roster.roster_sha256,
        curation.review_manifest.manifest_sha256,
    ):
        raise ValueError("public lineage registry does not project exact private governance")
    if (curation.roster.policy_id, curation.roster.policy_sha256) != (
        curation.policy.policy_id,
        curation.policy.policy_sha256,
    ):
        raise ValueError("lineage curator roster does not replay from policy")
    if datetime.fromisoformat(
        curation.policy.sealed_at
    ) > datetime.fromisoformat(curation.roster.sealed_at):
        raise ValueError("lineage curator roster predates the curation policy")
    roster_sealed = datetime.fromisoformat(
        curation.roster.sealed_at
    )

    definition_by_id = {item.lineage_id: item for item in registry_value.definitions}
    reviews_by_lineage: dict[str, list[MechanismLineageDefinitionReviewV3]] = defaultdict(list)
    curator_ids = {item.curator_id for item in curation.roster.curators}
    criteria = set(curation.policy.definition_review_criteria)
    for review in curation.review_manifest.reviews:
        if datetime.fromisoformat(
            review.reviewed_at
        ) <= roster_sealed:
            raise ValueError("lineage raw review does not follow the sealed roster")
        definition = definition_by_id.get(review.lineage_id)
        if definition is None or (
            review.lineage_sha256,
            review.scientific_preimage_sha256,
            review.taxonomy_evidence_refs,
        ) != (
            definition.lineage_sha256,
            definition.scientific_preimage_sha256,
            definition.taxonomy_evidence_refs,
        ):
            raise ValueError("lineage raw review does not bind an exact registry definition")
        if review.curator_id not in curator_ids:
            raise ValueError("lineage raw review comes from outside the frozen roster")
        if set(review.criterion_findings) != criteria:
            raise ValueError("lineage raw review does not cover every frozen criterion")
        reviews_by_lineage[review.lineage_id].append(review)
    if set(reviews_by_lineage) != set(definition_by_id):
        raise ValueError("lineage raw reviews do not exactly cover registry definitions")

    adjudication_by_lineage = {
        item.lineage_id: item for item in curation.review_manifest.adjudications
    }
    for lineage_id, reviews in reviews_by_lineage.items():
        if len(reviews) != 2 or {item.curator_id for item in reviews} != curator_ids:
            raise ValueError("lineage definition lacks the exact dual-curator roster")
        decisions = {item.decision for item in reviews}
        adjudication = adjudication_by_lineage.get(lineage_id)
        if len(decisions) == 1:
            if decisions != {MechanismLineageReviewDecisionV3.INCLUDE}:
                raise ValueError("rejected lineage definition appears in public registry")
            if adjudication is not None:
                raise ValueError("agreed lineage definition has unnecessary adjudication")
            continue
        if adjudication is None:
            raise ValueError("lineage review disagreement lacks adjudication")
        if datetime.fromisoformat(
            adjudication.adjudicated_at
        ) <= max(
            datetime.fromisoformat(item.reviewed_at)
            for item in reviews
        ):
            raise ValueError("lineage adjudication does not follow both raw reviews")
        if adjudication.adjudicator_id not in {
            item.curator_id for item in curation.roster.adjudicators
        }:
            raise ValueError("lineage adjudication comes from outside the frozen roster")
        if adjudication.final_decision is not MechanismLineageReviewDecisionV3.INCLUDE:
            raise ValueError("adjudicated-rejected lineage appears in public registry")
    registry_sealed = datetime.fromisoformat(
        registry_value.sealed_at
    )
    if registry_sealed < datetime.fromisoformat(
        curation.review_manifest.sealed_at
    ):
        raise ValueError("public lineage registry predates private definition review")
    if datetime.fromisoformat(
        curation.assembled_at
    ) < registry_sealed:
        raise ValueError("lineage curation release assembly predates registry seal")


def build_mechanism_lineage_assignment_v3(
    *,
    registry: MechanismLineageRegistryV3,
    case: FlatBandBenchmarkCaseV1,
    lineage_id: str,
    case_evidence_refs: tuple[MechanismLineageEvidenceRefV3, ...],
    assignment_basis_sha256: str,
    assigned_at: str,
) -> MechanismLineageAssignmentV3:
    value = MechanismLineageRegistryV3.model_validate(
        registry.model_dump(mode="python", round_trip=True)
    )
    full_case = FlatBandBenchmarkCaseV1.model_validate(
        case.model_dump(mode="python", round_trip=True)
    )
    definition = next(
        (item for item in value.definitions if item.lineage_id == lineage_id), None
    )
    if definition is None:
        raise ValueError("lineage assignment references a definition outside registry")
    if definition.broad_mechanism_family is not full_case.primary_mechanism_stratum:
        raise ValueError("lineage broad family differs from case sampling stratum")
    refs = tuple(
        sorted(
            (
                MechanismLineageEvidenceRefV3.model_validate(
                    item.model_dump(mode="python", round_trip=True)
                )
                for item in case_evidence_refs
            ),
            key=lambda item: (
                item.source_id,
                item.source_record_id,
                item.source_record_raw_sha256,
            ),
        )
    )
    case_sources = {
        (item.source_id, item.source_record_id, item.raw_sha256)
        for item in full_case.source_records
    }
    assignment_sources = {
        (item.source_id, item.source_record_id, item.source_record_raw_sha256)
        for item in refs
    }
    if not assignment_sources <= case_sources:
        raise ValueError("case-specific lineage evidence is not a subset of case sources")
    assigned = _require_rfc3339(assigned_at)
    if datetime.fromisoformat(assigned) < datetime.fromisoformat(
        value.sealed_at
    ):
        raise ValueError("lineage assignment predates sealed registry")
    values = {
        "registry_id": value.registry_id,
        "registry_sha256": value.registry_sha256,
        "case_id": full_case.case_id,
        "case_sha256": full_case.case_sha256,
        "lineage_id": definition.lineage_id,
        "lineage_sha256": definition.lineage_sha256,
        "case_evidence_refs": refs,
        "assignment_basis_sha256": assignment_basis_sha256,
        "assigned_at": assigned,
    }
    draft = MechanismLineageAssignmentV3.model_construct(**values)
    digest = canonical_sha256(
        draft.model_dump(
            mode="python", exclude={"assignment_id", "assignment_sha256"}
        )
    )
    return MechanismLineageAssignmentV3.model_validate(
        {
            **values,
            "assignment_sha256": digest,
            "assignment_id": deterministic_id(
                "mechanism-lineage-assignment-v3",
                {"assignment_sha256": digest},
            ),
        }
    )


def build_mechanism_lineage_assignment_curation_policy_v3(
    *,
    registry: MechanismLineageRegistryV3,
    definition_curation_release: MechanismLineageCurationReleaseV3,
    assignment_review_criteria: tuple[str, ...],
    sealed_at: str,
) -> MechanismLineageAssignmentCurationPolicyV3:
    """Freeze assignment-review rules after taxonomy curation and before proposals."""

    registry_value = MechanismLineageRegistryV3.model_validate(
        registry.model_dump(mode="python", round_trip=True)
    )
    definition_curation = MechanismLineageCurationReleaseV3.model_validate(
        definition_curation_release.model_dump(mode="python", round_trip=True)
    )
    assert_formal_mechanism_lineage_registry_v3(
        registry=registry_value,
        curation_release=definition_curation,
    )
    sealed = _require_rfc3339(sealed_at)
    if datetime.fromisoformat(sealed) <= datetime.fromisoformat(
        definition_curation.assembled_at
    ):
        raise ValueError("lineage-assignment policy does not follow definition curation")
    return _build_addressed_v3(
        MechanismLineageAssignmentCurationPolicyV3,
        id_field="policy_id",
        sha_field="policy_sha256",
        prefix="lineage-assignment-policy-v3",
        values={
            "registry_id": registry_value.registry_id,
            "registry_sha256": registry_value.registry_sha256,
            "definition_curation_release_id": definition_curation.release_id,
            "definition_curation_release_sha256": definition_curation.release_sha256,
            "assignment_review_criteria": tuple(
                sorted(set(assignment_review_criteria))
            ),
            "sealed_at": sealed,
        },
    )


def build_mechanism_lineage_assignment_reviewer_roster_v3(
    *,
    policy: MechanismLineageAssignmentCurationPolicyV3,
    reviewers: tuple[MechanismLineageCuratorDeclarationV3, ...],
    adjudicators: tuple[MechanismLineageCuratorDeclarationV3, ...],
    independence_review: str,
    sealed_at: str,
) -> MechanismLineageAssignmentReviewerRosterV3:
    policy_value = MechanismLineageAssignmentCurationPolicyV3.model_validate(
        policy.model_dump(mode="python", round_trip=True)
    )
    ordered_reviewers = tuple(
        sorted(
            (
                MechanismLineageCuratorDeclarationV3.model_validate(
                    item.model_dump(mode="python", round_trip=True)
                )
                for item in reviewers
            ),
            key=lambda item: item.curator_id,
        )
    )
    ordered_adjudicators = tuple(
        sorted(
            (
                MechanismLineageCuratorDeclarationV3.model_validate(
                    item.model_dump(mode="python", round_trip=True)
                )
                for item in adjudicators
            ),
            key=lambda item: item.curator_id,
        )
    )
    sealed = _require_rfc3339(sealed_at)
    if datetime.fromisoformat(sealed) <= datetime.fromisoformat(
        policy_value.sealed_at
    ):
        raise ValueError("lineage-assignment roster does not follow policy")
    return _build_addressed_v3(
        MechanismLineageAssignmentReviewerRosterV3,
        id_field="roster_id",
        sha_field="roster_sha256",
        prefix="lineage-assignment-roster-v3",
        values={
            "policy_id": policy_value.policy_id,
            "policy_sha256": policy_value.policy_sha256,
            "reviewers": ordered_reviewers,
            "adjudicators": ordered_adjudicators,
            "independence_review": independence_review,
            "sealed_at": sealed,
        },
    )


def _case_lineage_evidence_refs_v3(
    case: FlatBandBenchmarkCaseV1,
) -> tuple[MechanismLineageEvidenceRefV3, ...]:
    return tuple(
        sorted(
            (
                MechanismLineageEvidenceRefV3(
                    source_id=item.source_id,
                    source_record_id=item.source_record_id,
                    source_record_raw_sha256=item.raw_sha256,
                )
                for item in case.source_records
            ),
            key=lambda item: (
                item.source_id,
                item.source_record_id,
                item.source_record_raw_sha256,
            ),
        )
    )


def build_mechanism_lineage_assignment_proposal_v3(
    *,
    candidate_id: str,
    candidate_sha256: str,
    case: FlatBandBenchmarkCaseV1,
    registry: MechanismLineageRegistryV3,
    definition_curation_release: MechanismLineageCurationReleaseV3,
    lineage_id: str,
    assignment_basis_sha256: str,
    proposed_at: str,
) -> MechanismLineageAssignmentProposalV3:
    """Precommit a full case/lineage/all-source-evidence assignment for review."""

    registry_value = MechanismLineageRegistryV3.model_validate(
        registry.model_dump(mode="python", round_trip=True)
    )
    definition_curation = MechanismLineageCurationReleaseV3.model_validate(
        definition_curation_release.model_dump(mode="python", round_trip=True)
    )
    full_case = FlatBandBenchmarkCaseV1.model_validate(
        case.model_dump(mode="python", round_trip=True)
    )
    assert_formal_mechanism_lineage_registry_v3(
        registry=registry_value,
        curation_release=definition_curation,
    )
    definition = next(
        (item for item in registry_value.definitions if item.lineage_id == lineage_id),
        None,
    )
    if definition is None:
        raise ValueError("lineage-assignment proposal uses an unknown definition")
    if definition.broad_mechanism_family is not full_case.primary_mechanism_stratum:
        raise ValueError("lineage-assignment proposal crosses broad sampling strata")
    proposed = _require_rfc3339(proposed_at)
    if datetime.fromisoformat(proposed) < datetime.fromisoformat(
        definition_curation.assembled_at
    ):
        raise ValueError("lineage-assignment proposal predates definition curation")
    return _build_addressed_v3(
        MechanismLineageAssignmentProposalV3,
        id_field="proposal_id",
        sha_field="proposal_sha256",
        prefix="lineage-assignment-proposal-v3",
        values={
            "registry_id": registry_value.registry_id,
            "registry_sha256": registry_value.registry_sha256,
            "definition_curation_release_id": definition_curation.release_id,
            "definition_curation_release_sha256": definition_curation.release_sha256,
            "candidate_id": candidate_id,
            "candidate_sha256": candidate_sha256,
            "case": full_case,
            "lineage_id": definition.lineage_id,
            "lineage_sha256": definition.lineage_sha256,
            "case_evidence_refs": _case_lineage_evidence_refs_v3(full_case),
            "assignment_basis_sha256": assignment_basis_sha256,
            "proposed_at": proposed,
        },
    )


def build_mechanism_lineage_assignment_candidate_universe_v3(
    *,
    registry: MechanismLineageRegistryV3,
    definition_curation_release: MechanismLineageCurationReleaseV3,
    policy: MechanismLineageAssignmentCurationPolicyV3,
    roster: MechanismLineageAssignmentReviewerRosterV3,
    proposals: tuple[MechanismLineageAssignmentProposalV3, ...],
    sealed_at: str,
) -> MechanismLineageAssignmentCandidateUniverseV3:
    registry_value = MechanismLineageRegistryV3.model_validate(
        registry.model_dump(mode="python", round_trip=True)
    )
    definition_curation = MechanismLineageCurationReleaseV3.model_validate(
        definition_curation_release.model_dump(mode="python", round_trip=True)
    )
    policy_value = MechanismLineageAssignmentCurationPolicyV3.model_validate(
        policy.model_dump(mode="python", round_trip=True)
    )
    roster_value = MechanismLineageAssignmentReviewerRosterV3.model_validate(
        roster.model_dump(mode="python", round_trip=True)
    )
    assert_formal_mechanism_lineage_registry_v3(
        registry=registry_value,
        curation_release=definition_curation,
    )
    ordered = tuple(
        sorted(
            (
                MechanismLineageAssignmentProposalV3.model_validate(
                    item.model_dump(mode="python", round_trip=True)
                )
                for item in proposals
            ),
            key=lambda item: (item.candidate_id, item.case.case_id, item.proposal_id),
        )
    )
    expected_governance = (
        registry_value.registry_id,
        registry_value.registry_sha256,
        definition_curation.release_id,
        definition_curation.release_sha256,
    )
    if (
        policy_value.registry_id,
        policy_value.registry_sha256,
        policy_value.definition_curation_release_id,
        policy_value.definition_curation_release_sha256,
    ) != expected_governance or any(
        (
            item.registry_id,
            item.registry_sha256,
            item.definition_curation_release_id,
            item.definition_curation_release_sha256,
        )
        != expected_governance
        for item in ordered
    ):
        raise ValueError("lineage-assignment universe crosswires definition roots")
    if (roster_value.policy_id, roster_value.policy_sha256) != (
        policy_value.policy_id,
        policy_value.policy_sha256,
    ):
        raise ValueError("lineage-assignment universe received a foreign roster")
    roster_sealed = datetime.fromisoformat(
        roster_value.sealed_at
    )
    if any(
        datetime.fromisoformat(item.proposed_at)
        <= roster_sealed
        for item in ordered
    ):
        raise ValueError("lineage-assignment proposal does not follow sealed roster")
    sealed = _require_rfc3339(sealed_at)
    sealed_time = datetime.fromisoformat(sealed)
    if any(
        datetime.fromisoformat(item.proposed_at)
        >= sealed_time
        for item in ordered
    ):
        raise ValueError("lineage-assignment universe does not follow every proposal")
    return _build_addressed_v3(
        MechanismLineageAssignmentCandidateUniverseV3,
        id_field="universe_id",
        sha_field="universe_sha256",
        prefix="lineage-assignment-universe-v3",
        values={
            "registry_id": registry_value.registry_id,
            "registry_sha256": registry_value.registry_sha256,
            "definition_curation_release_id": definition_curation.release_id,
            "definition_curation_release_sha256": definition_curation.release_sha256,
            "policy_id": policy_value.policy_id,
            "policy_sha256": policy_value.policy_sha256,
            "roster_id": roster_value.roster_id,
            "roster_sha256": roster_value.roster_sha256,
            "proposals": ordered,
            "sealed_at": sealed,
        },
    )


def build_mechanism_lineage_assignment_review_v3(
    *,
    policy: MechanismLineageAssignmentCurationPolicyV3,
    roster: MechanismLineageAssignmentReviewerRosterV3,
    candidate_universe: MechanismLineageAssignmentCandidateUniverseV3,
    proposal: MechanismLineageAssignmentProposalV3,
    reviewer_id: str,
    decision: MechanismLineageAssignmentDecisionV3,
    criterion_findings: tuple[str, ...],
    rationale: str,
    reviewed_at: str,
) -> MechanismLineageAssignmentReviewV3:
    policy_value = MechanismLineageAssignmentCurationPolicyV3.model_validate(
        policy.model_dump(mode="python", round_trip=True)
    )
    roster_value = MechanismLineageAssignmentReviewerRosterV3.model_validate(
        roster.model_dump(mode="python", round_trip=True)
    )
    universe = MechanismLineageAssignmentCandidateUniverseV3.model_validate(
        candidate_universe.model_dump(mode="python", round_trip=True)
    )
    proposal_value = MechanismLineageAssignmentProposalV3.model_validate(
        proposal.model_dump(mode="python", round_trip=True)
    )
    if (roster_value.policy_id, roster_value.policy_sha256) != (
        policy_value.policy_id,
        policy_value.policy_sha256,
    ) or (
        universe.policy_id,
        universe.policy_sha256,
        universe.roster_id,
        universe.roster_sha256,
    ) != (
        policy_value.policy_id,
        policy_value.policy_sha256,
        roster_value.roster_id,
        roster_value.roster_sha256,
    ):
        raise ValueError("lineage-assignment review crosswires governance")
    if proposal_value not in universe.proposals:
        raise ValueError("lineage-assignment review references an unsealed proposal")
    if reviewer_id not in {item.curator_id for item in roster_value.reviewers}:
        raise ValueError("lineage-assignment review comes from outside roster")
    reviewed = _require_rfc3339(reviewed_at)
    if datetime.fromisoformat(reviewed) <= datetime.fromisoformat(
        universe.sealed_at
    ):
        raise ValueError("lineage-assignment review does not follow universe seal")
    return _build_addressed_v3(
        MechanismLineageAssignmentReviewV3,
        id_field="review_id",
        sha_field="review_sha256",
        prefix="lineage-assignment-review-v3",
        values={
            "policy_id": policy_value.policy_id,
            "policy_sha256": policy_value.policy_sha256,
            "roster_id": roster_value.roster_id,
            "roster_sha256": roster_value.roster_sha256,
            "universe_id": universe.universe_id,
            "universe_sha256": universe.universe_sha256,
            "proposal_id": proposal_value.proposal_id,
            "proposal_sha256": proposal_value.proposal_sha256,
            "reviewer_id": reviewer_id,
            "decision": decision,
            "criterion_findings": tuple(sorted(set(criterion_findings))),
            "rationale": rationale,
            "reviewed_at": reviewed,
        },
    )


def build_mechanism_lineage_assignment_adjudication_v3(
    *,
    policy: MechanismLineageAssignmentCurationPolicyV3,
    roster: MechanismLineageAssignmentReviewerRosterV3,
    candidate_universe: MechanismLineageAssignmentCandidateUniverseV3,
    proposal: MechanismLineageAssignmentProposalV3,
    reviews: tuple[MechanismLineageAssignmentReviewV3, ...],
    adjudicator_id: str,
    final_decision: MechanismLineageAssignmentDecisionV3,
    rationale: str,
    adjudicated_at: str,
) -> MechanismLineageAssignmentAdjudicationV3:
    policy_value = MechanismLineageAssignmentCurationPolicyV3.model_validate(
        policy.model_dump(mode="python", round_trip=True)
    )
    roster_value = MechanismLineageAssignmentReviewerRosterV3.model_validate(
        roster.model_dump(mode="python", round_trip=True)
    )
    universe = MechanismLineageAssignmentCandidateUniverseV3.model_validate(
        candidate_universe.model_dump(mode="python", round_trip=True)
    )
    proposal_value = MechanismLineageAssignmentProposalV3.model_validate(
        proposal.model_dump(mode="python", round_trip=True)
    )
    review_values = tuple(
        sorted(
            (
                MechanismLineageAssignmentReviewV3.model_validate(
                    item.model_dump(mode="python", round_trip=True)
                )
                for item in reviews
            ),
            key=lambda item: (item.reviewer_id, item.review_id),
        )
    )
    if len(review_values) != 2 or len(
        {item.reviewer_id for item in review_values}
    ) != 2:
        raise ValueError("lineage-assignment adjudication requires two raw reviews")
    if len({item.decision for item in review_values}) != 2:
        raise ValueError("agreed lineage-assignment reviews cannot be adjudicated")
    if any(
        (
            item.policy_id,
            item.policy_sha256,
            item.roster_id,
            item.roster_sha256,
            item.universe_id,
            item.universe_sha256,
            item.proposal_id,
            item.proposal_sha256,
        )
        != (
            policy_value.policy_id,
            policy_value.policy_sha256,
            roster_value.roster_id,
            roster_value.roster_sha256,
            universe.universe_id,
            universe.universe_sha256,
            proposal_value.proposal_id,
            proposal_value.proposal_sha256,
        )
        for item in review_values
    ):
        raise ValueError("lineage-assignment adjudication crosswires raw reviews")
    if adjudicator_id not in {
        item.curator_id for item in roster_value.adjudicators
    }:
        raise ValueError("lineage-assignment adjudicator comes from outside roster")
    adjudicated = _require_rfc3339(adjudicated_at)
    if datetime.fromisoformat(adjudicated) <= max(
        datetime.fromisoformat(item.reviewed_at)
        for item in review_values
    ):
        raise ValueError("lineage-assignment adjudication does not follow raw reviews")
    return _build_addressed_v3(
        MechanismLineageAssignmentAdjudicationV3,
        id_field="adjudication_id",
        sha_field="adjudication_sha256",
        prefix="lineage-assign-adjud-v3",
        values={
            "policy_id": policy_value.policy_id,
            "policy_sha256": policy_value.policy_sha256,
            "roster_id": roster_value.roster_id,
            "roster_sha256": roster_value.roster_sha256,
            "universe_id": universe.universe_id,
            "universe_sha256": universe.universe_sha256,
            "proposal_id": proposal_value.proposal_id,
            "proposal_sha256": proposal_value.proposal_sha256,
            "review_refs": tuple(
                sorted((item.review_id, item.review_sha256) for item in review_values)
            ),
            "adjudicator_id": adjudicator_id,
            "final_decision": final_decision,
            "rationale": rationale,
            "adjudicated_at": adjudicated,
        },
    )


def build_mechanism_lineage_assignment_review_manifest_v3(
    *,
    policy: MechanismLineageAssignmentCurationPolicyV3,
    roster: MechanismLineageAssignmentReviewerRosterV3,
    candidate_universe: MechanismLineageAssignmentCandidateUniverseV3,
    reviews: tuple[MechanismLineageAssignmentReviewV3, ...],
    adjudications: tuple[MechanismLineageAssignmentAdjudicationV3, ...] = (),
    sealed_at: str,
) -> MechanismLineageAssignmentReviewManifestV3:
    policy_value = MechanismLineageAssignmentCurationPolicyV3.model_validate(
        policy.model_dump(mode="python", round_trip=True)
    )
    roster_value = MechanismLineageAssignmentReviewerRosterV3.model_validate(
        roster.model_dump(mode="python", round_trip=True)
    )
    universe = MechanismLineageAssignmentCandidateUniverseV3.model_validate(
        candidate_universe.model_dump(mode="python", round_trip=True)
    )
    ordered_reviews = tuple(
        sorted(
            (
                MechanismLineageAssignmentReviewV3.model_validate(
                    item.model_dump(mode="python", round_trip=True)
                )
                for item in reviews
            ),
            key=lambda item: (item.proposal_id, item.reviewer_id, item.review_id),
        )
    )
    ordered_adjudications = tuple(
        sorted(
            (
                MechanismLineageAssignmentAdjudicationV3.model_validate(
                    item.model_dump(mode="python", round_trip=True)
                )
                for item in adjudications
            ),
            key=lambda item: (item.proposal_id, item.adjudication_id),
        )
    )
    return _build_addressed_v3(
        MechanismLineageAssignmentReviewManifestV3,
        id_field="manifest_id",
        sha_field="manifest_sha256",
        prefix="lineage-assign-review-set-v3",
        values={
            "policy_id": policy_value.policy_id,
            "policy_sha256": policy_value.policy_sha256,
            "roster_id": roster_value.roster_id,
            "roster_sha256": roster_value.roster_sha256,
            "universe_id": universe.universe_id,
            "universe_sha256": universe.universe_sha256,
            "reviews": ordered_reviews,
            "adjudications": ordered_adjudications,
            "sealed_at": _require_rfc3339(sealed_at),
        },
    )


def build_mechanism_lineage_assignment_curation_release_v3(
    *,
    registry: MechanismLineageRegistryV3,
    definition_curation_release: MechanismLineageCurationReleaseV3,
    policy: MechanismLineageAssignmentCurationPolicyV3,
    roster: MechanismLineageAssignmentReviewerRosterV3,
    candidate_universe: MechanismLineageAssignmentCandidateUniverseV3,
    review_manifest: MechanismLineageAssignmentReviewManifestV3,
    assembled_at: str,
) -> MechanismLineageAssignmentCurationReleaseV3:
    registry_value = MechanismLineageRegistryV3.model_validate(
        registry.model_dump(mode="python", round_trip=True)
    )
    definition_curation = MechanismLineageCurationReleaseV3.model_validate(
        definition_curation_release.model_dump(mode="python", round_trip=True)
    )
    policy_value = MechanismLineageAssignmentCurationPolicyV3.model_validate(
        policy.model_dump(mode="python", round_trip=True)
    )
    roster_value = MechanismLineageAssignmentReviewerRosterV3.model_validate(
        roster.model_dump(mode="python", round_trip=True)
    )
    universe = MechanismLineageAssignmentCandidateUniverseV3.model_validate(
        candidate_universe.model_dump(mode="python", round_trip=True)
    )
    manifest = MechanismLineageAssignmentReviewManifestV3.model_validate(
        review_manifest.model_dump(mode="python", round_trip=True)
    )
    release = _build_addressed_v3(
        MechanismLineageAssignmentCurationReleaseV3,
        id_field="release_id",
        sha_field="release_sha256",
        prefix="lineage-assign-curation-v3",
        values={
            "registry_id": registry_value.registry_id,
            "registry_sha256": registry_value.registry_sha256,
            "definition_curation_release_id": definition_curation.release_id,
            "definition_curation_release_sha256": definition_curation.release_sha256,
            "policy": policy_value,
            "roster": roster_value,
            "candidate_universe": universe,
            "review_manifest": manifest,
            "assembled_at": _require_rfc3339(assembled_at),
        },
    )
    derive_formal_mechanism_lineage_assignments_v3(
        registry=registry_value,
        definition_curation_release=definition_curation,
        assignment_curation_release=release,
    )
    return release


def derive_formal_mechanism_lineage_assignments_v3(
    *,
    registry: MechanismLineageRegistryV3,
    definition_curation_release: MechanismLineageCurationReleaseV3,
    assignment_curation_release: MechanismLineageAssignmentCurationReleaseV3,
) -> tuple[MechanismLineageAssignmentV3, ...]:
    """Replay private assignment curation and return its only public projection."""

    registry_value = MechanismLineageRegistryV3.model_validate(
        registry.model_dump(mode="python", round_trip=True)
    )
    definition_curation = MechanismLineageCurationReleaseV3.model_validate(
        definition_curation_release.model_dump(mode="python", round_trip=True)
    )
    release = MechanismLineageAssignmentCurationReleaseV3.model_validate(
        assignment_curation_release.model_dump(mode="python", round_trip=True)
    )
    assert_formal_mechanism_lineage_registry_v3(
        registry=registry_value,
        curation_release=definition_curation,
    )
    if (
        release.registry_id,
        release.registry_sha256,
        release.definition_curation_release_id,
        release.definition_curation_release_sha256,
    ) != (
        registry_value.registry_id,
        registry_value.registry_sha256,
        definition_curation.release_id,
        definition_curation.release_sha256,
    ):
        raise ValueError("lineage-assignment curation binds foreign definition roots")

    definition_by_id = {
        item.lineage_id: item for item in registry_value.definitions
    }
    policy = release.policy
    roster = release.roster
    universe = release.candidate_universe
    manifest = release.review_manifest
    definition_assembled = datetime.fromisoformat(
        definition_curation.assembled_at
    )
    policy_sealed = datetime.fromisoformat(policy.sealed_at)
    roster_sealed = datetime.fromisoformat(roster.sealed_at)
    universe_sealed = datetime.fromisoformat(
        universe.sealed_at
    )
    if not (definition_assembled < policy_sealed < roster_sealed < universe_sealed):
        raise ValueError("lineage-assignment governance timestamps are inverted")

    proposal_by_id: dict[str, MechanismLineageAssignmentProposalV3] = {}
    for proposal in universe.proposals:
        if proposal.proposal_id in proposal_by_id:
            raise ValueError("lineage-assignment universe repeats a proposal")
        proposal_by_id[proposal.proposal_id] = proposal
        if (
            proposal.registry_id,
            proposal.registry_sha256,
            proposal.definition_curation_release_id,
            proposal.definition_curation_release_sha256,
        ) != (
            registry_value.registry_id,
            registry_value.registry_sha256,
            definition_curation.release_id,
            definition_curation.release_sha256,
        ):
            raise ValueError("lineage-assignment proposal binds foreign definition roots")
        proposal_time = datetime.fromisoformat(
            proposal.proposed_at
        )
        if not (roster_sealed < proposal_time < universe_sealed):
            raise ValueError("lineage-assignment proposal is outside prereview window")
        case = FlatBandBenchmarkCaseV1.model_validate(
            proposal.case.model_dump(mode="python", round_trip=True)
        )
        definition = definition_by_id.get(proposal.lineage_id)
        if definition is None or proposal.lineage_sha256 != definition.lineage_sha256:
            raise ValueError("lineage-assignment proposal uses a foreign lineage")
        if definition.broad_mechanism_family is not case.primary_mechanism_stratum:
            raise ValueError("lineage-assignment proposal crosses broad sampling strata")
        if proposal.case_evidence_refs != _case_lineage_evidence_refs_v3(case):
            raise ValueError(
                "lineage-assignment proposal does not exactly cover case evidence"
            )

    expected_governance = (
        policy.policy_id,
        policy.policy_sha256,
        roster.roster_id,
        roster.roster_sha256,
        universe.universe_id,
        universe.universe_sha256,
    )
    reviewer_ids = {item.curator_id for item in roster.reviewers}
    reviews_by_proposal: dict[
        str, list[MechanismLineageAssignmentReviewV3]
    ] = defaultdict(list)
    criteria = set(policy.assignment_review_criteria)
    for review in manifest.reviews:
        if (
            review.policy_id,
            review.policy_sha256,
            review.roster_id,
            review.roster_sha256,
            review.universe_id,
            review.universe_sha256,
        ) != expected_governance:
            raise ValueError("lineage-assignment raw review binds foreign governance")
        proposal = proposal_by_id.get(review.proposal_id)
        if proposal is None or review.proposal_sha256 != proposal.proposal_sha256:
            raise ValueError("lineage-assignment raw review binds a foreign proposal")
        if review.reviewer_id not in reviewer_ids:
            raise ValueError("lineage-assignment raw review comes from outside roster")
        if set(review.criterion_findings) != criteria:
            raise ValueError(
                "lineage-assignment raw review does not cover frozen criteria"
            )
        if datetime.fromisoformat(
            review.reviewed_at
        ) <= universe_sealed:
            raise ValueError("lineage-assignment raw review predates universe seal")
        reviews_by_proposal[review.proposal_id].append(review)
    if set(reviews_by_proposal) != set(proposal_by_id):
        raise ValueError(
            "lineage-assignment raw reviews do not exactly cover candidate universe"
        )

    adjudication_by_proposal = {
        item.proposal_id: item for item in manifest.adjudications
    }
    accepted_proposals: list[MechanismLineageAssignmentProposalV3] = []
    adjudicator_ids = {item.curator_id for item in roster.adjudicators}
    for proposal_id, reviews in reviews_by_proposal.items():
        if len(reviews) != 2 or {item.reviewer_id for item in reviews} != reviewer_ids:
            raise ValueError(
                "lineage-assignment proposal lacks exact dual-reviewer coverage"
            )
        decisions = {item.decision for item in reviews}
        adjudication = adjudication_by_proposal.get(proposal_id)
        if len(decisions) == 1:
            if adjudication is not None:
                raise ValueError(
                    "agreed lineage-assignment proposal has unnecessary adjudication"
                )
            final_decision = next(iter(decisions))
        else:
            if adjudication is None:
                raise ValueError(
                    "lineage-assignment disagreement lacks adjudication"
                )
            proposal = proposal_by_id[proposal_id]
            if (
                adjudication.policy_id,
                adjudication.policy_sha256,
                adjudication.roster_id,
                adjudication.roster_sha256,
                adjudication.universe_id,
                adjudication.universe_sha256,
            ) != expected_governance or (
                adjudication.proposal_sha256 != proposal.proposal_sha256
            ) or adjudication.review_refs != tuple(
                sorted((item.review_id, item.review_sha256) for item in reviews)
            ):
                raise ValueError(
                    "lineage-assignment adjudication does not bind exact inputs"
                )
            if adjudication.adjudicator_id not in adjudicator_ids:
                raise ValueError(
                    "lineage-assignment adjudication comes from outside roster"
                )
            if datetime.fromisoformat(
                adjudication.adjudicated_at
            ) <= max(
                datetime.fromisoformat(item.reviewed_at)
                for item in reviews
            ):
                raise ValueError(
                    "lineage-assignment adjudication does not follow raw reviews"
                )
            final_decision = adjudication.final_decision
        if final_decision is MechanismLineageAssignmentDecisionV3.ACCEPT:
            accepted_proposals.append(proposal_by_id[proposal_id])
    if set(adjudication_by_proposal) - set(reviews_by_proposal):
        raise ValueError("lineage-assignment adjudication has no proposal")

    assigned_at = release.assembled_at
    return tuple(
        sorted(
            (
                build_mechanism_lineage_assignment_v3(
                    registry=registry_value,
                    case=proposal.case,
                    lineage_id=proposal.lineage_id,
                    case_evidence_refs=proposal.case_evidence_refs,
                    assignment_basis_sha256=proposal.assignment_basis_sha256,
                    assigned_at=assigned_at,
                )
                for proposal in accepted_proposals
            ),
            key=lambda item: (item.case_id, item.assignment_id),
        )
    )


def assert_formal_mechanism_lineage_assignment_curation_v3(
    *,
    registry: MechanismLineageRegistryV3,
    definition_curation_release: MechanismLineageCurationReleaseV3,
    assignment_curation_release: MechanismLineageAssignmentCurationReleaseV3,
    public_assignments: tuple[MechanismLineageAssignmentV3, ...],
) -> None:
    """Require public assignments to equal the accepted private-review projection."""

    expected = derive_formal_mechanism_lineage_assignments_v3(
        registry=registry,
        definition_curation_release=definition_curation_release,
        assignment_curation_release=assignment_curation_release,
    )
    observed = tuple(
        sorted(
            (
                MechanismLineageAssignmentV3.model_validate(
                    item.model_dump(mode="python", round_trip=True)
                )
                for item in public_assignments
            ),
            key=lambda item: (item.case_id, item.assignment_id),
        )
    )
    if len({item.case_id for item in observed}) != len(observed):
        raise ValueError("public lineage assignments repeat a case")
    if observed != expected:
        raise ValueError(
            "public lineage assignments are not the exact accepted curation projection"
        )


def _group_definition_v3(
    *, axis: LeakageAxisV3, preimage_value: object
) -> LeakageGroupDefinitionV3:
    preimage = canonical_json_bytes(preimage_value).decode("utf-8")
    semantic = {"axis": axis, "canonical_preimage": preimage}
    definition_sha256 = canonical_sha256(semantic)
    return LeakageGroupDefinitionV3(
        group_id=deterministic_id(
            "leakage-group-v3",
            {"axis": axis.value, "canonical_preimage": preimage},
        ),
        definition_sha256=definition_sha256,
        axis=axis,
        canonical_preimage=preimage,
    )


def _composition_definition_v3(formula: str) -> LeakageGroupDefinitionV3:
    return _group_definition_v3(
        axis=LeakageAxisV3.COMPOSITION_FAMILY,
        preimage_value={
            "kind": "composition",
            "stoichiometry": _formula_stoichiometry_v2(formula),
        },
    )


def _source_definition_v3(record: SourceRecordRefV1) -> LeakageGroupDefinitionV3:
    return _group_definition_v3(
        axis=LeakageAxisV3.ARTICLE_OR_SOURCE_FAMILY,
        preimage_value={
            "kind": "source-record",
            "source_id": record.source_id,
            "source_record_id": record.source_record_id,
        },
    )


def _structure_definition_v3(
    algorithm: StructureGroupingAlgorithmV2,
    canonical_group_key: str,
) -> LeakageGroupDefinitionV3:
    axis = _V2_TO_V3_STRUCTURE_AXIS[algorithm.axis]
    return _group_definition_v3(
        axis=axis,
        preimage_value={
            "kind": "structure-group",
            "axis": axis.value,
            "algorithm_id": algorithm.algorithm_id,
            "algorithm_sha256": algorithm.algorithm_sha256,
            "canonical_group_key": canonical_group_key,
        },
    )


def _lineage_definition_v3(
    registry: MechanismLineageRegistryV3,
    lineage: MechanismLineageDefinitionV3,
) -> LeakageGroupDefinitionV3:
    return _group_definition_v3(
        axis=LeakageAxisV3.MECHANISM_LINEAGE,
        preimage_value={
            "kind": "mechanism-lineage",
            "registry_id": registry.registry_id,
            "registry_sha256": registry.registry_sha256,
            "lineage_id": lineage.lineage_id,
            "lineage_sha256": lineage.lineage_sha256,
            "scientific_preimage_sha256": lineage.scientific_preimage_sha256,
        },
    )


_DOI_V3 = re.compile(r"^10\.\d{4,9}/\S+$", flags=re.IGNORECASE)


def _canonical_doi_v3(record: SourceRecordRefV1) -> str | None:
    candidates: list[str] = []
    parsed = urlparse(record.canonical_url)
    if parsed.hostname is not None and parsed.hostname.casefold() in {
        "doi.org",
        "dx.doi.org",
    }:
        candidates.append(unquote(parsed.path).lstrip("/"))
    candidates.append(record.source_record_id)
    for candidate in candidates:
        normalized = unquote(candidate).strip().removeprefix("doi:").casefold()
        if _DOI_V3.fullmatch(normalized):
            return normalized
    return None


def _reject_cross_source_doi_aliases_v3(
    cases: tuple[FlatBandBenchmarkCaseV1, ...],
) -> None:
    record_keys_by_doi: dict[str, set[tuple[str, str]]] = defaultdict(set)
    for case in cases:
        for record in case.source_records:
            doi = _canonical_doi_v3(record)
            if doi is not None:
                record_keys_by_doi[doi].add(
                    (record.source_id, record.source_record_id)
                )
    aliases = {
        doi: record_keys
        for doi, record_keys in record_keys_by_doi.items()
        if len(record_keys) > 1
    }
    if aliases:
        raise ValueError(
            "cross-source DOI alias is not canonicalized; formal V3 is NO-GO"
        )


def derive_leakage_group_ids_v3(
    *,
    formula: str,
    primary_mechanism_stratum: MechanismFamily,
    source_records: tuple[SourceRecordRefV1, ...],
    structure_groups: tuple[tuple[StructureGroupingAlgorithmV2, str], ...],
    mechanism_lineage_registry: MechanismLineageRegistryV3,
    mechanism_lineage_id: str,
) -> tuple[str, ...]:
    """Derive V3 graph edges before content-addressing a full case."""

    algorithms = tuple(
        StructureGroupingAlgorithmV2.model_validate(
            algorithm.model_dump(mode="python", round_trip=True)
        )
        for algorithm, _ in structure_groups
    )
    if len(algorithms) != 2 or {item.axis for item in algorithms} != STRUCTURE_LEAKAGE_AXES:
        raise ValueError("V3 case derivation requires both structure axes")
    records = tuple(
        SourceRecordRefV1.model_validate(
            item.model_dump(mode="python", round_trip=True)
        )
        for item in source_records
    )
    # A one-case synthetic wrapper is sufficient for the alias audit because
    # the failure condition is duplicate DOI identity across source providers.
    record_keys_by_doi: dict[str, set[tuple[str, str]]] = defaultdict(set)
    for record in records:
        doi = _canonical_doi_v3(record)
        if doi is not None:
            record_keys_by_doi[doi].add((record.source_id, record.source_record_id))
    if any(len(value) > 1 for value in record_keys_by_doi.values()):
        raise ValueError(
            "cross-source DOI alias is not canonicalized; formal V3 is NO-GO"
        )
    registry = MechanismLineageRegistryV3.model_validate(
        mechanism_lineage_registry.model_dump(mode="python", round_trip=True)
    )
    lineage = next(
        (item for item in registry.definitions if item.lineage_id == mechanism_lineage_id),
        None,
    )
    if lineage is None:
        raise ValueError("V3 case derivation references a lineage outside registry")
    if lineage.broad_mechanism_family is not primary_mechanism_stratum:
        raise ValueError("V3 lineage broad family differs from sampling stratum")
    definitions = [
        _composition_definition_v3(formula),
        *(_source_definition_v3(item) for item in records),
        *(
            _structure_definition_v3(algorithm, key)
            for algorithm, key in structure_groups
        ),
        _lineage_definition_v3(registry, lineage),
    ]
    group_ids = tuple(sorted({item.group_id for item in definitions}))
    if len(group_ids) != len(definitions):
        raise ValueError("V3 case derivation produced duplicate typed groups")
    return group_ids


def _validate_full_cases_v3(
    *,
    cases: tuple[FlatBandBenchmarkCaseV1, ...],
    split_manifest: BenchmarkSplitManifestV2,
) -> tuple[FlatBandBenchmarkCaseV1, ...]:
    validated = tuple(
        sorted(
            (
                FlatBandBenchmarkCaseV1.model_validate(
                    item.model_dump(mode="python", round_trip=True)
                )
                for item in cases
            ),
            key=lambda item: item.case_id,
        )
    )
    if len({item.case_id for item in validated}) != len(validated):
        raise ValueError("V3 full case universe contains duplicate case identity")
    full_by_id = {item.case_id: item for item in validated}
    split_by_id = {item.case_id: item for item in split_manifest.cases}
    if set(full_by_id) != set(split_by_id):
        raise ValueError("V3 full cases do not exactly cover split V2")
    for case_id, full_case in full_by_id.items():
        split_case = split_by_id[case_id]
        if (
            full_case.case_sha256,
            full_case.target_class,
            full_case.dimensionality,
            full_case.primary_mechanism_stratum,
            full_case.leakage_group_ids,
        ) != (
            split_case.case_sha256,
            split_case.target_class,
            split_case.dimensionality,
            split_case.primary_mechanism_stratum,
            split_case.independence_group_ids,
        ):
            raise ValueError("V3 full case differs from exact split V2 projection")
    _reject_cross_source_doi_aliases_v3(validated)
    return validated


def _validate_lineage_artifacts_v3(
    *,
    cases: tuple[FlatBandBenchmarkCaseV1, ...],
    registry: MechanismLineageRegistryV3,
    assignments: tuple[MechanismLineageAssignmentV3, ...],
) -> tuple[
    MechanismLineageRegistryV3,
    tuple[MechanismLineageAssignmentV3, ...],
    dict[str, MechanismLineageAssignmentV3],
    dict[str, MechanismLineageDefinitionV3],
]:
    value = MechanismLineageRegistryV3.model_validate(
        registry.model_dump(mode="python", round_trip=True)
    )
    ordered = tuple(
        sorted(
            (
                MechanismLineageAssignmentV3.model_validate(
                    item.model_dump(mode="python", round_trip=True)
                )
                for item in assignments
            ),
            key=lambda item: (item.case_id, item.assignment_id),
        )
    )
    if len({item.case_id for item in ordered}) != len(ordered):
        raise ValueError("V3 requires exactly one lineage assignment per case")
    case_by_id = {item.case_id: item for item in cases}
    if {item.case_id for item in ordered} != set(case_by_id):
        raise ValueError("V3 lineage assignments do not exactly cover full cases")
    definition_by_id = {item.lineage_id: item for item in value.definitions}
    registry_sealed = datetime.fromisoformat(value.sealed_at)
    assignment_by_case: dict[str, MechanismLineageAssignmentV3] = {}
    for assignment in ordered:
        case = case_by_id[assignment.case_id]
        if (assignment.registry_id, assignment.registry_sha256) != (
            value.registry_id,
            value.registry_sha256,
        ):
            raise ValueError("V3 lineage assignment binds a foreign registry")
        definition = definition_by_id.get(assignment.lineage_id)
        if definition is None or definition.lineage_sha256 != assignment.lineage_sha256:
            raise ValueError("V3 lineage assignment binds a foreign definition")
        if (assignment.case_sha256, definition.broad_mechanism_family) != (
            case.case_sha256,
            case.primary_mechanism_stratum,
        ):
            raise ValueError("V3 lineage assignment drifts from case identity or stratum")
        case_sources = {
            (item.source_id, item.source_record_id, item.raw_sha256)
            for item in case.source_records
        }
        assignment_sources = {
            (item.source_id, item.source_record_id, item.source_record_raw_sha256)
            for item in assignment.case_evidence_refs
        }
        if not assignment_sources <= case_sources:
            raise ValueError(
                "V3 case-specific lineage evidence is not a subset of assigned case sources"
            )
        if datetime.fromisoformat(
            assignment.assigned_at
        ) < registry_sealed:
            raise ValueError("V3 lineage assignment predates sealed registry")
        assignment_by_case[assignment.case_id] = assignment
    return value, ordered, assignment_by_case, definition_by_id


def _derive_memberships_v3(
    *,
    cases: tuple[FlatBandBenchmarkCaseV1, ...],
    algorithms: tuple[StructureGroupingAlgorithmV2, ...],
    structure_assignments: dict[
        tuple[LeakageAxis, str], StructureGroupingAssignmentV2
    ],
    lineage_registry: MechanismLineageRegistryV3,
    lineage_assignments: dict[str, MechanismLineageAssignmentV3],
    lineage_definitions: dict[str, MechanismLineageDefinitionV3],
) -> tuple[
    tuple[LeakageGroupDefinitionV3, ...],
    tuple[LeakageMembershipV3, ...],
    dict[str, tuple[str, ...]],
]:
    algorithm_by_axis = {item.axis: item for item in algorithms}
    definitions: dict[str, LeakageGroupDefinitionV3] = {}
    memberships: list[LeakageMembershipV3] = []
    groups_by_case: dict[str, tuple[str, ...]] = {}
    shared_sources: dict[tuple[str, str], SourceRecordRefV1] = {}

    def add_membership(
        *,
        case: FlatBandBenchmarkCaseV1,
        definition: LeakageGroupDefinitionV3,
        derivation: object,
        structure_assignment: StructureGroupingAssignmentV2 | None = None,
        lineage_assignment: MechanismLineageAssignmentV3 | None = None,
    ) -> None:
        previous = definitions.setdefault(definition.group_id, definition)
        if previous != definition:
            raise ValueError("V3 typed group ID resolves to conflicting definitions")
        provenance = canonical_sha256(
            {
                "case_id": case.case_id,
                "case_sha256": case.case_sha256,
                "axis": definition.axis.value,
                "group_id": definition.group_id,
                "group_definition_sha256": definition.definition_sha256,
                "derivation": derivation,
                "structure_assignment_id": (
                    None
                    if structure_assignment is None
                    else structure_assignment.assignment_id
                ),
                "structure_assignment_sha256": (
                    None
                    if structure_assignment is None
                    else structure_assignment.assignment_sha256
                ),
                "mechanism_lineage_assignment_id": (
                    None
                    if lineage_assignment is None
                    else lineage_assignment.assignment_id
                ),
                "mechanism_lineage_assignment_sha256": (
                    None
                    if lineage_assignment is None
                    else lineage_assignment.assignment_sha256
                ),
            }
        )
        memberships.append(
            LeakageMembershipV3(
                case_id=case.case_id,
                case_sha256=case.case_sha256,
                axis=definition.axis,
                group_id=definition.group_id,
                group_definition_sha256=definition.definition_sha256,
                structure_assignment_id=(
                    None
                    if structure_assignment is None
                    else structure_assignment.assignment_id
                ),
                structure_assignment_sha256=(
                    None
                    if structure_assignment is None
                    else structure_assignment.assignment_sha256
                ),
                mechanism_lineage_assignment_id=(
                    None
                    if lineage_assignment is None
                    else lineage_assignment.assignment_id
                ),
                mechanism_lineage_assignment_sha256=(
                    None
                    if lineage_assignment is None
                    else lineage_assignment.assignment_sha256
                ),
                provenance_sha256=provenance,
            )
        )

    for case in cases:
        composition = _composition_definition_v3(case.formula)
        add_membership(
            case=case,
            definition=composition,
            derivation={
                "formula": case.formula,
                "normalized_stoichiometry": _formula_stoichiometry_v2(case.formula),
            },
        )
        for record in case.source_records:
            source_key = (record.source_id, record.source_record_id)
            previous_record = shared_sources.setdefault(source_key, record)
            if previous_record != record:
                raise ValueError("V3 shared source identity has conflicting content")
            source = _source_definition_v3(record)
            add_membership(
                case=case,
                definition=source,
                derivation={
                    "source_record": record.model_dump(mode="python", round_trip=True),
                    "source_record_sha256": canonical_sha256(
                        record.model_dump(mode="python", round_trip=True)
                    ),
                },
            )
        for axis in sorted(STRUCTURE_LEAKAGE_AXES, key=lambda item: item.value):
            assignment = structure_assignments[(axis, case.case_id)]
            structure = _structure_definition_v3(
                algorithm_by_axis[axis], assignment.canonical_group_key
            )
            add_membership(
                case=case,
                definition=structure,
                derivation={
                    "assignment_id": assignment.assignment_id,
                    "assignment_sha256": assignment.assignment_sha256,
                },
                structure_assignment=assignment,
            )
        lineage_assignment = lineage_assignments[case.case_id]
        lineage = lineage_definitions[lineage_assignment.lineage_id]
        lineage_group = _lineage_definition_v3(lineage_registry, lineage)
        add_membership(
            case=case,
            definition=lineage_group,
            derivation={
                "registry_id": lineage_registry.registry_id,
                "registry_sha256": lineage_registry.registry_sha256,
                "lineage_id": lineage.lineage_id,
                "lineage_sha256": lineage.lineage_sha256,
                "scientific_preimage_sha256": lineage.scientific_preimage_sha256,
                "assignment_id": lineage_assignment.assignment_id,
                "assignment_sha256": lineage_assignment.assignment_sha256,
            },
            lineage_assignment=lineage_assignment,
        )
        case_memberships = tuple(
            item for item in memberships if item.case_id == case.case_id
        )
        if {item.axis for item in case_memberships} != frozenset(LeakageAxisV3):
            raise ValueError("every V3 case must cover all five independence axes")
        groups_by_case[case.case_id] = tuple(
            sorted(item.group_id for item in case_memberships)
        )
    ordered_definitions = tuple(
        sorted(definitions.values(), key=lambda item: (item.axis.value, item.group_id))
    )
    ordered_memberships = tuple(
        sorted(
            memberships,
            key=lambda item: (
                item.case_id,
                item.axis.value,
                item.group_id,
                item.provenance_sha256,
            ),
        )
    )
    return ordered_definitions, ordered_memberships, groups_by_case


def _components_from_memberships_v3(
    memberships: Iterable[LeakageMembershipV3],
) -> tuple[LeakageComponentV1, ...]:
    by_group: dict[tuple[LeakageAxisV3, str], set[str]] = defaultdict(set)
    all_cases: set[str] = set()
    for membership in memberships:
        all_cases.add(membership.case_id)
        by_group[(membership.axis, membership.group_id)].add(membership.case_id)
    neighbours = {case_id: set() for case_id in all_cases}
    for grouped_cases in by_group.values():
        for case_id in grouped_cases:
            neighbours[case_id].update(grouped_cases - {case_id})
    remaining = set(all_cases)
    components: list[LeakageComponentV1] = []
    while remaining:
        root = min(remaining)
        queue = deque((root,))
        connected: set[str] = set()
        while queue:
            case_id = queue.popleft()
            if case_id in connected:
                continue
            connected.add(case_id)
            queue.extend(sorted(neighbours[case_id] - connected))
        remaining.difference_update(connected)
        case_ids = tuple(sorted(connected))
        components.append(
            LeakageComponentV1(
                component_id=deterministic_id(
                    "leakage-component", {"case_ids": case_ids}
                ),
                case_ids=case_ids,
            )
        )
    return tuple(sorted(components, key=lambda item: item.component_id))


def _assert_v3_component_policy(
    *,
    split_manifest: BenchmarkSplitManifestV2,
    release: LeakageComponentReleaseV3,
) -> None:
    split_by_case = {item.case_id: item.split for item in split_manifest.cases}
    observed_cases = {
        case_id for component in release.components for case_id in component.case_ids
    }
    if observed_cases != set(split_by_case):
        raise ValueError("V3 components do not exactly cover split V2 cases")
    component_splits: list[BenchmarkSplit] = []
    for component in release.components:
        splits = {split_by_case[case_id] for case_id in component.case_ids}
        if len(splits) != 1:
            raise ValueError("a V3 leakage component crosses benchmark splits")
        component_splits.append(next(iter(splits)))
    counts = {split: component_splits.count(split) for split in BenchmarkSplit}
    if split_manifest.manifest_kind in {
        SplitManifestKind.PILOT_R1,
        SplitManifestKind.PILOT_R2,
    }:
        pilot_split = BenchmarkSplit(split_manifest.manifest_kind.value)
        if counts[pilot_split] < 10:
            raise ValueError("formal V3 Pilot requires at least ten components")
    else:
        minima = {
            BenchmarkSplit.DEVELOPMENT: 20,
            BenchmarkSplit.LOCKED_IID: 10,
            BenchmarkSplit.LOCKED_OOD: 10,
        }
        if any(counts[split] < minimum for split, minimum in minima.items()):
            raise ValueError("formal V3 Main does not meet 20/10/10 component minima")

    definitions = {item.group_id: item for item in release.group_definitions}
    broad_taxonomy_ids = {
        mechanism_holdout_taxonomy_group_id(value) for value in MechanismFamily
    }
    if broad_taxonomy_ids & set(definitions):
        raise ValueError("broad mechanism taxonomy appears as a V3 component edge")
    for holdout in split_manifest.ood_holdout_families:
        if holdout.axis is OodHoldoutAxis.MECHANISM_FAMILY:
            if holdout.group_id in definitions:
                raise ValueError("broad mechanism holdout cannot become a V3 graph edge")
            continue
        expected_axis = {
            OodHoldoutAxis.STRUCTURE_PROTOTYPE: (
                LeakageAxisV3.STRUCTURE_PROTOTYPE
            ),
            OodHoldoutAxis.STRUCTURE_FINGERPRINT: (
                LeakageAxisV3.STRUCTURE_FINGERPRINT
            ),
        }[holdout.axis]
        definition = definitions.get(holdout.group_id)
        if definition is None or definition.axis is not expected_axis:
            raise ValueError("structure OOD holdout lacks its exact V3 group definition")


def build_leakage_component_release_v3(
    *,
    cases: tuple[FlatBandBenchmarkCaseV1, ...],
    split_manifest: BenchmarkSplitManifestV2,
    grouping_algorithms: tuple[StructureGroupingAlgorithmV2, ...],
    grouping_runs: tuple[StructureGroupingRunV2, ...],
    grouping_assignments: tuple[StructureGroupingAssignmentV2, ...],
    mechanism_lineage_registry: MechanismLineageRegistryV3,
    mechanism_lineage_assignments: tuple[MechanismLineageAssignmentV3, ...],
    created_at: str,
) -> LeakageComponentReleaseV3:
    """Build V3 solely from frozen cases and replayable provenance artifacts."""

    split = BenchmarkSplitManifestV2.model_validate(
        split_manifest.model_dump(mode="python", round_trip=True)
    )
    full_cases = _validate_full_cases_v3(cases=cases, split_manifest=split)
    algorithms, runs, structure_values, structure_by_key = (
        _validate_structure_grouping_v2(
            cases=full_cases,
            grouping_algorithms=grouping_algorithms,
            grouping_runs=grouping_runs,
            grouping_assignments=grouping_assignments,
        )
    )
    registry, lineage_values, lineage_by_case, lineage_by_id = (
        _validate_lineage_artifacts_v3(
            cases=full_cases,
            registry=mechanism_lineage_registry,
            assignments=mechanism_lineage_assignments,
        )
    )
    definitions, memberships, groups_by_case = _derive_memberships_v3(
        cases=full_cases,
        algorithms=algorithms,
        structure_assignments=structure_by_key,
        lineage_registry=registry,
        lineage_assignments=lineage_by_case,
        lineage_definitions=lineage_by_id,
    )
    split_by_id = {item.case_id: item for item in split.cases}
    for case in full_cases:
        expected = groups_by_case[case.case_id]
        if case.leakage_group_ids != expected:
            raise ValueError("full case independence groups differ from V3 derivation")
        if split_by_id[case.case_id].independence_group_ids != expected:
            raise ValueError("split V2 independence groups differ from V3 derivation")
    components = _components_from_memberships_v3(memberships)
    values = {
        "split_manifest_id": split.manifest_id,
        "split_manifest_sha256": split.manifest_sha256,
        "full_case_universe_sha256": structure_grouping_case_universe_sha256_v2(
            full_cases
        ),
        "grouping_algorithms": algorithms,
        "grouping_runs": runs,
        "grouping_assignments": structure_values,
        "mechanism_lineage_registry": registry,
        "mechanism_lineage_assignments": lineage_values,
        "group_definitions": definitions,
        "memberships": memberships,
        "components": components,
        "created_at": _require_rfc3339(created_at),
    }
    draft = LeakageComponentReleaseV3.model_construct(**values)
    digest = canonical_sha256(
        draft.model_dump(mode="python", exclude={"release_id", "release_sha256"})
    )
    release = LeakageComponentReleaseV3.model_validate(
        {
            **values,
            "release_sha256": digest,
            "release_id": deterministic_id(
                "leakage-release-v3", {"release_sha256": digest}
            ),
        }
    )
    assert_leakage_split_closure_v3(
        cases=full_cases,
        split_manifest=split,
        release=release,
    )
    return release


def assert_leakage_split_closure_v3(
    *,
    cases: tuple[FlatBandBenchmarkCaseV1, ...],
    split_manifest: BenchmarkSplitManifestV2,
    release: LeakageComponentReleaseV3,
) -> None:
    """Replay the formal V3 graph without accepting caller memberships."""

    split = BenchmarkSplitManifestV2.model_validate(
        split_manifest.model_dump(mode="python", round_trip=True)
    )
    value = LeakageComponentReleaseV3.model_validate(
        release.model_dump(mode="python", round_trip=True)
    )
    if (value.split_manifest_id, value.split_manifest_sha256) != (
        split.manifest_id,
        split.manifest_sha256,
    ):
        raise ValueError("V3 leakage release references a different split V2")
    full_cases = _validate_full_cases_v3(cases=cases, split_manifest=split)
    if value.full_case_universe_sha256 != structure_grouping_case_universe_sha256_v2(
        full_cases
    ):
        raise ValueError("V3 release binds a different full case universe")
    algorithms, runs, structure_values, structure_by_key = (
        _validate_structure_grouping_v2(
            cases=full_cases,
            grouping_algorithms=value.grouping_algorithms,
            grouping_runs=value.grouping_runs,
            grouping_assignments=value.grouping_assignments,
        )
    )
    if (algorithms, runs, structure_values) != (
        value.grouping_algorithms,
        value.grouping_runs,
        value.grouping_assignments,
    ):
        raise ValueError("V3 structure artifacts are not in canonical order")
    registry, lineage_values, lineage_by_case, lineage_by_id = (
        _validate_lineage_artifacts_v3(
            cases=full_cases,
            registry=value.mechanism_lineage_registry,
            assignments=value.mechanism_lineage_assignments,
        )
    )
    if (registry, lineage_values) != (
        value.mechanism_lineage_registry,
        value.mechanism_lineage_assignments,
    ):
        raise ValueError("V3 mechanism lineage artifacts are not canonical")
    definitions, memberships, groups_by_case = _derive_memberships_v3(
        cases=full_cases,
        algorithms=algorithms,
        structure_assignments=structure_by_key,
        lineage_registry=registry,
        lineage_assignments=lineage_by_case,
        lineage_definitions=lineage_by_id,
    )
    if definitions != value.group_definitions:
        raise ValueError("V3 group definitions do not replay")
    if memberships != value.memberships:
        raise ValueError("V3 memberships do not replay from authoritative inputs")
    split_by_id = {item.case_id: item for item in split.cases}
    for case in full_cases:
        expected = groups_by_case[case.case_id]
        if case.leakage_group_ids != expected:
            raise ValueError("full case independence groups differ from V3 derivation")
        if split_by_id[case.case_id].independence_group_ids != expected:
            raise ValueError("split V2 independence groups differ from V3 derivation")
    if value.components != _components_from_memberships_v3(memberships):
        raise ValueError("V3 components do not uniquely replay")
    _assert_v3_component_policy(split_manifest=split, release=value)


def assert_pilot_leakage_v3(
    *,
    cases: tuple[FlatBandBenchmarkCaseV1, ...],
    split_manifest: BenchmarkSplitManifestV2,
    release: LeakageComponentReleaseV3,
    lineage_curation_release: MechanismLineageCurationReleaseV3,
) -> None:
    """Only V3 is accepted for a new formal Pilot leakage release."""

    if split_manifest.manifest_kind not in {
        SplitManifestKind.PILOT_R1,
        SplitManifestKind.PILOT_R2,
    }:
        raise ValueError("formal V3 Pilot verifier requires a Pilot split")
    assert_formal_mechanism_lineage_registry_v3(
        registry=release.mechanism_lineage_registry,
        curation_release=lineage_curation_release,
    )
    assert_leakage_split_closure_v3(
        cases=cases, split_manifest=split_manifest, release=release
    )


def assert_main_leakage_v3(
    *,
    cases: tuple[FlatBandBenchmarkCaseV1, ...],
    split_manifest: BenchmarkSplitManifestV2,
    release: LeakageComponentReleaseV3,
    lineage_curation_release: MechanismLineageCurationReleaseV3,
) -> None:
    """Formal Main120 verifier with achievable 20/10/10 components."""

    if split_manifest.manifest_kind is not SplitManifestKind.MAIN_120:
        raise ValueError("formal V3 Main verifier requires MAIN_120")
    assert_formal_mechanism_lineage_registry_v3(
        registry=release.mechanism_lineage_registry,
        curation_release=lineage_curation_release,
    )
    assert_leakage_split_closure_v3(
        cases=cases, split_manifest=split_manifest, release=release
    )


def derive_formal_leakage_memberships_for_case_universe_v3(
    *,
    cases: tuple[FlatBandBenchmarkCaseV1, ...],
    grouping_algorithms: tuple[StructureGroupingAlgorithmV2, ...],
    grouping_runs: tuple[StructureGroupingRunV2, ...],
    grouping_assignments: tuple[StructureGroupingAssignmentV2, ...],
    mechanism_lineage_registry: MechanismLineageRegistryV3,
    mechanism_lineage_curation_release: MechanismLineageCurationReleaseV3,
    mechanism_lineage_assignments: tuple[MechanismLineageAssignmentV3, ...],
) -> tuple[LeakageMembershipV3, ...]:
    """Derive typed edges for an unsplit candidate universe.

    ``flatband_cases`` can use this on every CandidatePoolV3 primary and
    substitute before selection.  The caller must first exact-replay the pool;
    this function deliberately accepts no caller-provided group/component IDs.
    """

    full_cases = tuple(
        sorted(
            (
                FlatBandBenchmarkCaseV1.model_validate(
                    item.model_dump(mode="python", round_trip=True)
                )
                for item in cases
            ),
            key=lambda item: item.case_id,
        )
    )
    if not full_cases or len({item.case_id for item in full_cases}) != len(full_cases):
        raise ValueError("formal leakage case universe must be nonempty and unique")
    _reject_cross_source_doi_aliases_v3(full_cases)
    assert_formal_mechanism_lineage_registry_v3(
        registry=mechanism_lineage_registry,
        curation_release=mechanism_lineage_curation_release,
    )
    algorithms, _, _, structure_by_key = _validate_structure_grouping_v2(
        cases=full_cases,
        grouping_algorithms=grouping_algorithms,
        grouping_runs=grouping_runs,
        grouping_assignments=grouping_assignments,
    )
    registry, _, lineage_by_case, lineage_by_id = _validate_lineage_artifacts_v3(
        cases=full_cases,
        registry=mechanism_lineage_registry,
        assignments=mechanism_lineage_assignments,
    )
    _, memberships, groups_by_case = _derive_memberships_v3(
        cases=full_cases,
        algorithms=algorithms,
        structure_assignments=structure_by_key,
        lineage_registry=registry,
        lineage_assignments=lineage_by_case,
        lineage_definitions=lineage_by_id,
    )
    for case in full_cases:
        if case.leakage_group_ids != groups_by_case[case.case_id]:
            raise ValueError("candidate case independence groups do not replay")
    return memberships


def assert_cross_round_leakage_disjoint_v3(
    *,
    round_contexts: tuple[LeakageRoundClosureContextV3, ...],
    lineage_curation_release: MechanismLineageCurationReleaseV3,
    additional_case_universes: tuple[
        LeakageUnsplitCaseUniverseContextV3, ...
    ] = (),
) -> None:
    """Exact-replay each universe, then recompute one typed union graph.

    A local component ID is never trusted as the cross-round identity.  Any
    composition, source, structure, or mechanism-lineage edge connecting cases
    owned by different releases/pools fails closed.  CandidatePool/calibration
    inputs enter through ``additional_case_universes`` and are derived here;
    persisted caller memberships are never accepted.
    """

    rounds = tuple(
        LeakageRoundClosureContextV3.model_validate(
            item.model_dump(mode="python", round_trip=True)
        )
        for item in round_contexts
    )
    extra_universes = tuple(
        LeakageUnsplitCaseUniverseContextV3.model_validate(
            item.model_dump(mode="python", round_trip=True)
        )
        for item in additional_case_universes
    )
    if len(rounds) + len(extra_universes) < 2:
        raise ValueError("V3 union verification requires at least two universes")
    release_refs = tuple(
        (item.release.release_id, item.release.release_sha256) for item in rounds
    )
    if len(release_refs) != len(set(release_refs)):
        raise ValueError("cross-round V3 verification received a duplicate release")
    universe_refs = tuple(
        (item.universe_id, item.source_artifact_id, item.source_artifact_sha256)
        for item in extra_universes
    )
    if len(universe_refs) != len(set(universe_refs)):
        raise ValueError("V3 union verification received a duplicate case universe")

    owners_and_memberships: list[
        tuple[tuple[str, str, str], MechanismLineageRegistryV3, tuple[StructureGroupingAlgorithmV2, ...], tuple[LeakageMembershipV3, ...]]
    ] = []
    definitions: dict[str, LeakageGroupDefinitionV3] = {}
    for context in rounds:
        value = context.release
        assert_leakage_split_closure_v3(
            cases=context.cases,
            split_manifest=context.split_manifest,
            release=value,
        )
        assert_formal_mechanism_lineage_registry_v3(
            registry=value.mechanism_lineage_registry,
            curation_release=lineage_curation_release,
        )
        owner = ("ROUND", value.release_id, value.release_sha256)
        owners_and_memberships.append(
            (
                owner,
                value.mechanism_lineage_registry,
                value.grouping_algorithms,
                value.memberships,
            )
        )
        for definition in value.group_definitions:
            previous = definitions.setdefault(definition.group_id, definition)
            if previous != definition:
                raise ValueError(
                    "cross-universe typed leakage group has conflicting definitions"
                )

    for context in extra_universes:
        memberships = derive_formal_leakage_memberships_for_case_universe_v3(
            cases=context.cases,
            grouping_algorithms=context.grouping_algorithms,
            grouping_runs=context.grouping_runs,
            grouping_assignments=context.grouping_assignments,
            mechanism_lineage_registry=context.mechanism_lineage_registry,
            mechanism_lineage_curation_release=lineage_curation_release,
            mechanism_lineage_assignments=context.mechanism_lineage_assignments,
        )
        owners_and_memberships.append(
            (
                (
                    "UNSPLIT",
                    context.source_artifact_id,
                    context.source_artifact_sha256,
                ),
                context.mechanism_lineage_registry,
                context.grouping_algorithms,
                memberships,
            )
        )

    registry_ref = (
        owners_and_memberships[0][1].registry_id,
        owners_and_memberships[0][1].registry_sha256,
    )
    algorithms = owners_and_memberships[0][2]
    for _, registry, grouping_algorithms, _ in owners_and_memberships:
        if (registry.registry_id, registry.registry_sha256) != registry_ref:
            raise ValueError(
                "cross-universe V3 inputs do not share one global lineage registry"
            )
        if grouping_algorithms != algorithms:
            raise ValueError("cross-universe V3 structure algorithms drift")

    case_owner: dict[str, tuple[str, str, str]] = {}
    memberships: list[LeakageMembershipV3] = []
    for owner, _, _, universe_memberships in owners_and_memberships:
        for membership in universe_memberships:
            previous_owner = case_owner.setdefault(membership.case_id, owner)
            if previous_owner != owner:
                raise ValueError("a case appears in more than one V3 leakage universe")
            memberships.append(membership)

    for component in _components_from_memberships_v3(memberships):
        owners = {case_owner[case_id] for case_id in component.case_ids}
        if len(owners) != 1:
            raise ValueError("a typed leakage union component crosses benchmark universes")


def component_assignments_v3(
    release: LeakageComponentReleaseV3,
) -> dict[str, str]:
    """Return the formal V3 case-to-resampling-component projection."""

    value = LeakageComponentReleaseV3.model_validate(
        release.model_dump(mode="python", round_trip=True)
    )
    return {
        case_id: component.component_id
        for component in value.components
        for case_id in component.case_ids
    }
