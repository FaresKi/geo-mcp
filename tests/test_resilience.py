from __future__ import annotations

import time

import pytest

from geo_mcp.infrastructure.resilience import (
    CircuitBreaker,
    CircuitOpenError,
    Deadline,
    DeadlineExceeded,
    RetryPolicy,
    retry_call,
)


def test_retry_eventually_succeeds() -> None:
    attempts = {"n": 0}

    def flaky() -> str:
        attempts["n"] += 1
        if attempts["n"] < 3:
            raise RuntimeError("transient")
        return "ok"

    policy = RetryPolicy(max_attempts=4, base_delay_s=0.01, jitter=0)
    assert retry_call(flaky, policy=policy, label="flaky") == "ok"
    assert attempts["n"] == 3


def test_retry_exhausted() -> None:
    policy = RetryPolicy(max_attempts=2, base_delay_s=0.01, jitter=0)
    with pytest.raises(RuntimeError, match="boom"):
        retry_call(lambda: (_ for _ in ()).throw(RuntimeError("boom")), policy=policy)


def test_circuit_opens_and_recovers() -> None:
    breaker = CircuitBreaker(failure_threshold=2, recovery_timeout_s=0.05, name="t")
    breaker.record_failure()
    assert breaker.allow()
    breaker.record_failure()
    assert breaker.is_open
    with pytest.raises(CircuitOpenError):
        breaker.guard()
    time.sleep(0.06)
    assert breaker.allow()
    breaker.record_success()
    assert not breaker.is_open


def test_deadline_expires() -> None:
    deadline = Deadline(budget_s=0.01)
    time.sleep(0.02)
    with pytest.raises(DeadlineExceeded):
        deadline.check("unit")


def test_retry_delay_caps() -> None:
    policy = RetryPolicy(base_delay_s=1.0, max_delay_s=2.0, jitter=0)
    assert policy.delay_for_attempt(0) == 1.0
    assert policy.delay_for_attempt(5) == 2.0
