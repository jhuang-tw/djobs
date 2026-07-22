"""Tests for retry policy backoff calculation."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from djobs.core.retry import RetryPolicy


def test_calculate_delay_uses_exponential_backoff() -> None:
    policy = RetryPolicy(base_delay_seconds=2, multiplier=3, max_delay_seconds=100)

    assert policy.calculate_delay(1) == timedelta(seconds=2)
    assert policy.calculate_delay(2) == timedelta(seconds=6)
    assert policy.calculate_delay(3) == timedelta(seconds=18)


def test_calculate_delay_caps_at_max_delay() -> None:
    policy = RetryPolicy(base_delay_seconds=10, multiplier=10, max_delay_seconds=60)

    assert policy.calculate_delay(3) == timedelta(seconds=60)


def test_next_run_after_uses_utc_now() -> None:
    now = datetime(2026, 5, 23, 1, 2, 3, tzinfo=timezone.utc)
    policy = RetryPolicy(base_delay_seconds=5)

    assert policy.next_run_after(1, now=now) == now + timedelta(seconds=5)


def test_invalid_policy_values_are_rejected() -> None:
    with pytest.raises(ValueError):
        RetryPolicy(base_delay_seconds=0)
    with pytest.raises(ValueError):
        RetryPolicy(multiplier=0.5)
    with pytest.raises(ValueError):
        RetryPolicy(base_delay_seconds=10, max_delay_seconds=1)


def test_invalid_attempt_is_rejected() -> None:
    policy = RetryPolicy()

    with pytest.raises(ValueError):
        policy.calculate_delay(0)
