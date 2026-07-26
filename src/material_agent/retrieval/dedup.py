"""Exact duplicate annotations and non-destructive structure clustering."""

from __future__ import annotations

import hashlib
from collections import defaultdict

from pymatgen.analysis.structure_matcher import SpeciesComparator, StructureMatcher
from pymatgen.core import Structure

from material_agent.retrieval.models import CandidateAuditRecord


def annotate_exact_duplicates(
    candidates: list[CandidateAuditRecord],
) -> tuple[list[CandidateAuditRecord], list[dict[str, object]]]:
    groups: dict[str, list[str]] = defaultdict(list)
    for candidate in candidates:
        if candidate.structure_id:
            groups[candidate.structure_id].append(candidate.candidate_id)

    group_by_candidate: dict[str, str] = {}
    mappings: list[dict[str, object]] = []
    for structure_id, candidate_ids in sorted(groups.items()):
        if len(candidate_ids) < 2:
            continue
        members = sorted(candidate_ids)
        digest = hashlib.sha256("|".join(members).encode("utf-8")).hexdigest()
        group_id = f"exact_{digest[:20]}"
        mappings.append(
            {
                "exact_duplicate_group_id": group_id,
                "structure_id": structure_id,
                "candidate_ids": members,
            }
        )
        for candidate_id in members:
            group_by_candidate[candidate_id] = group_id

    updated = [
        candidate.model_copy(
            update={
                "exact_duplicate_group_id": group_by_candidate.get(
                    candidate.candidate_id
                )
            }
        )
        for candidate in candidates
    ]
    return updated, mappings


def annotate_similarity_clusters(
    candidates: list[CandidateAuditRecord],
    structures: dict[str, Structure],
) -> tuple[list[CandidateAuditRecord], list[dict[str, object]]]:
    buckets: dict[str, list[CandidateAuditRecord]] = defaultdict(list)
    for candidate in candidates:
        if candidate.published_downstream and candidate.candidate_id in structures:
            buckets[candidate.reduced_formula or candidate.formula].append(candidate)

    matcher = StructureMatcher(
        ltol=0.2,
        stol=0.3,
        angle_tol=5,
        primitive_cell=True,
        scale=True,
        attempt_supercell=False,
        allow_subset=False,
        comparator=SpeciesComparator(),
    )
    cluster_by_candidate: dict[str, str] = {}
    records: list[dict[str, object]] = []

    for formula, bucket in sorted(buckets.items()):
        ordered = sorted(bucket, key=lambda item: item.candidate_id)
        structure_list = [structures[item.candidate_id] for item in ordered]
        if len(structure_list) == 1:
            grouped_structures = [structure_list]
        else:
            grouped_structures = matcher.group_structures(structure_list)

        structure_owner = {
            id(structure): candidate.candidate_id
            for candidate, structure in zip(ordered, structure_list, strict=True)
        }
        for group in grouped_structures:
            members = sorted(structure_owner[id(structure)] for structure in group)
            digest = hashlib.sha256("|".join(members).encode("utf-8")).hexdigest()
            cluster_id = f"sim_{digest[:20]}"
            records.append(
                {
                    "similarity_cluster_id": cluster_id,
                    "reduced_formula": formula,
                    "candidate_ids": members,
                    "member_count": len(members),
                    "matcher_policy": "structure-matcher-v1",
                }
            )
            for candidate_id in members:
                cluster_by_candidate[candidate_id] = cluster_id

    updated = [
        candidate.model_copy(
            update={
                "similarity_cluster_id": cluster_by_candidate.get(
                    candidate.candidate_id
                )
            }
        )
        for candidate in candidates
    ]
    return updated, records

