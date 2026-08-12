from __future__ import annotations

import time
from dataclasses import dataclass


class DeadlineExceeded(TimeoutError):
    """Raised when an operation budget is exhausted."""


@dataclass(slots=True)
class Deadline:
    """Wall-clock budget for multi-step operations."""

    budget_s: float
    _started: float = 0.0

    def __post_init__(self) -> None:
        self._started = time.monotonic()

    @classmethod
    def from_seconds(cls, budget_s: float | None) -> Deadline | None:
        if budget_s is None or budget_s <= 0:
            return None
        return cls(budget_s=budget_s)

    def remaining(self) -> float:
        return self.budget_s - (time.monotonic() - self._started)

    def expired(self) -> bool:
        return self.remaining() <= 0

    def check(self, label: str = "operation") -> None:
        if self.expired():
            raise DeadlineExceeded(
                f"Deadline exceeded during {label} "
                f"(budget={self.budget_s:.1f}s)"
            )
