from __future__ import annotations

import logging
import random
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import TypeVar

logger = logging.getLogger(__name__)

T = TypeVar("T")


@dataclass(frozen=True, slots=True)
class RetryPolicy:
    """Exponential backoff with jitter for transient remote failures."""

    max_attempts: int = 3
    base_delay_s: float = 0.25
    max_delay_s: float = 8.0
    jitter: float = 0.25
    retry_on: tuple[type[BaseException], ...] = (Exception,)

    def delay_for_attempt(self, attempt: int) -> float:
        """attempt is 0-based (after first failure)."""
        raw = min(self.max_delay_s, self.base_delay_s * (2**attempt))
        if self.jitter <= 0:
            return raw
        spread = raw * self.jitter
        return max(0.0, raw + random.uniform(-spread, spread))


def retry_call(
    fn: Callable[[], T],
    *,
    policy: RetryPolicy,
    label: str = "operation",
    should_retry: Callable[[BaseException], bool] | None = None,
) -> T:
    last_error: BaseException | None = None
    attempts = max(1, policy.max_attempts)
    for attempt in range(attempts):
        try:
            return fn()
        except policy.retry_on as exc:
            if should_retry is not None and not should_retry(exc):
                raise
            last_error = exc
            if attempt + 1 >= attempts:
                break
            delay = policy.delay_for_attempt(attempt)
            logger.warning(
                "%s failed (attempt %s/%s): %s; retrying in %.2fs",
                label,
                attempt + 1,
                attempts,
                exc,
                delay,
            )
            if delay > 0:
                time.sleep(delay)
    assert last_error is not None
    raise last_error
