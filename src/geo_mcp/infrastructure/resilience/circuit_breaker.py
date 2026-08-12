from __future__ import annotations

import threading
import time
from dataclasses import dataclass


class CircuitOpenError(RuntimeError):
    """Raised when a circuit breaker is open and calls are short-circuited."""


@dataclass
class CircuitBreaker:
    """Simple consecutive-failure circuit breaker (thread-safe)."""

    failure_threshold: int = 5
    recovery_timeout_s: float = 30.0
    name: str = "circuit"

    def __post_init__(self) -> None:
        self._failures = 0
        self._opened_at: float | None = None
        self._lock = threading.Lock()

    def allow(self) -> bool:
        with self._lock:
            if self._opened_at is None:
                return True
            if time.monotonic() - self._opened_at >= self.recovery_timeout_s:
                # Half-open: allow a probe.
                return True
            return False

    def record_success(self) -> None:
        with self._lock:
            self._failures = 0
            self._opened_at = None

    def record_failure(self) -> None:
        with self._lock:
            self._failures += 1
            if self._failures >= self.failure_threshold:
                self._opened_at = time.monotonic()

    def guard(self) -> None:
        if not self.allow():
            raise CircuitOpenError(f"Circuit open for {self.name}")

    @property
    def is_open(self) -> bool:
        return not self.allow()
