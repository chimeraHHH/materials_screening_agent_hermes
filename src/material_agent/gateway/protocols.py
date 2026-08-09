"""Dependency protocols for the Materials Gateway core."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from material_agent.gateway.models import (
    CompanionTransitionV1,
    GatewayResultRecordV1,
    GatewayRunRecordV1,
    InspirationRunRequestV1,
    RunActionV1,
    RunStateV1,
)


@runtime_checkable
class GatewayRepository(Protocol):
    """Minimal persistence boundary; production storage can be added later."""

    def create_or_get(
        self, record: GatewayRunRecordV1
    ) -> tuple[GatewayRunRecordV1, bool]: ...

    def get_run(self, run_id: str) -> GatewayRunRecordV1 | None: ...

    def replace_run(
        self,
        record: GatewayRunRecordV1,
        *,
        expected_revision: int,
        result: GatewayResultRecordV1 | None,
    ) -> GatewayRunRecordV1: ...

    def get_result(self, run_id: str) -> GatewayResultRecordV1 | None: ...


@runtime_checkable
class InspirationCompanionAdapter(Protocol):
    """Adapter for the companion InspirationRunner, never an orchestrator stage."""

    def start(
        self,
        *,
        run_id: str,
        request: InspirationRunRequestV1,
    ) -> CompanionTransitionV1: ...

    def act(
        self,
        *,
        run_id: str,
        request: InspirationRunRequestV1,
        state: RunStateV1,
        action: RunActionV1,
    ) -> CompanionTransitionV1: ...


@runtime_checkable
class ArtifactReader(Protocol):
    """Read only exact artifact URIs already bound to a terminal closure."""

    def read_bytes(self, uri: str) -> bytes: ...
