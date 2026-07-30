"""Versioned, immutable policy snapshots for Agent03 control-plane gates.

The contracts deliberately carry references and approval metadata, not a
locally invented VASP method profile.  A real policy body stays an immutable
Artifact managed and approved outside this module.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import Field, model_validator

from .models import ArtifactRef, StrictModel


class PolicyKind(StrEnum):
    METHOD = "METHOD"
    RESOURCE = "RESOURCE"
    EVIDENCE = "EVIDENCE"
    RETRY = "RETRY"
    RETENTION = "RETENTION"
    BACKEND = "BACKEND"
    SECURITY = "SECURITY"
    REPORTING = "REPORTING"


class PolicyStatus(StrEnum):
    DRAFT = "DRAFT"
    APPROVED = "APPROVED"
    DEPRECATED = "DEPRECATED"


class PolicySnapshot(StrictModel):
    """Identity and approval record for a policy stored as an Artifact."""

    kind: PolicyKind
    policy_id: str = Field(min_length=1)
    version: str = Field(min_length=1)
    status: PolicyStatus
    artifact: ArtifactRef
    effective_from: datetime | None = None
    approved_by: str | None = None
    approved_at: datetime | None = None
    supersedes: ArtifactRef | None = None

    @model_validator(mode="after")
    def approval_metadata_guard(self) -> PolicySnapshot:
        approved_fields = (self.approved_by, self.approved_at, self.effective_from)
        if self.status is PolicyStatus.APPROVED and any(
            value is None for value in approved_fields
        ):
            raise ValueError(
                "APPROVED policy requires approver, approval time, and effective time"
            )
        if self.status is not PolicyStatus.APPROVED and any(
            value is not None for value in (self.approved_by, self.approved_at)
        ):
            raise ValueError("only APPROVED policy may carry approval metadata")
        return self


def find_policy(
    snapshots: tuple[PolicySnapshot, ...], kind: PolicyKind
) -> PolicySnapshot | None:
    """Return exactly one policy of a kind, rejecting ambiguous snapshots."""

    matches = tuple(snapshot for snapshot in snapshots if snapshot.kind is kind)
    if len(matches) > 1:
        raise ValueError(f"multiple {kind.value} policy snapshots were supplied")
    return matches[0] if matches else None
