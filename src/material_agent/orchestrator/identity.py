"""Injectable time and identifier providers for deterministic acceptance tests."""

from __future__ import annotations

import hashlib
import uuid
from datetime import UTC, datetime
from typing import Protocol, runtime_checkable


@runtime_checkable
class Clock(Protocol):
    def now(self) -> datetime: ...


@runtime_checkable
class IdFactory(Protocol):
    def new_id(self, prefix: str) -> str: ...


class SystemClock:
    def now(self) -> datetime:
        return datetime.now(UTC)


class UUIDIdFactory:
    def new_id(self, prefix: str) -> str:
        return f"{prefix}-{uuid.uuid4().hex[:16]}"


class FixedClock:
    def __init__(self, value: datetime) -> None:
        if value.tzinfo is None:
            raise ValueError("fixed clock value must be timezone-aware")
        self.value = value

    def now(self) -> datetime:
        return self.value


class DeterministicIdFactory:
    def __init__(self, seed: str = "orchestrator-test") -> None:
        self.seed = seed
        self._counters: dict[str, int] = {}

    def new_id(self, prefix: str) -> str:
        counter = self._counters.get(prefix, 0) + 1
        self._counters[prefix] = counter
        digest = hashlib.sha256(
            f"{self.seed}:{prefix}:{counter}".encode()
        ).hexdigest()[:16]
        return f"{prefix}-{digest}"
