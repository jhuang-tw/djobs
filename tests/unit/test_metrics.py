"""Tests for MetricsCollector."""

from __future__ import annotations

from djobs.observability.metrics import MetricsCollector


def test_counter_increments() -> None:
    m = MetricsCollector()
    m.inc("jobs.submitted")
    m.inc("jobs.submitted")
    m.inc("jobs.submitted", 3)
    assert m.counter("jobs.submitted") == 5


def test_counter_defaults_to_zero() -> None:
    m = MetricsCollector()
    assert m.counter("nonexistent") == 0


def test_gauge_set_and_read() -> None:
    m = MetricsCollector()
    m.set_gauge("queue.pending", 42.0)
    assert m.gauge("queue.pending") == 42.0
    m.set_gauge("queue.pending", 10.0)
    assert m.gauge("queue.pending") == 10.0


def test_gauge_defaults_to_zero() -> None:
    m = MetricsCollector()
    assert m.gauge("nonexistent") == 0.0


def test_snapshot_includes_all() -> None:
    m = MetricsCollector()
    m.inc("a", 1)
    m.set_gauge("b", 2.5)

    snap = m.snapshot()
    assert snap["counters"] == {"a": 1}
    assert snap["gauges"] == {"b": 2.5}
    assert "collected_since" in snap


def test_reset_clears_everything() -> None:
    m = MetricsCollector()
    m.inc("a", 5)
    m.set_gauge("b", 3.0)
    m.reset()
    assert m.counter("a") == 0
    assert m.gauge("b") == 0.0
