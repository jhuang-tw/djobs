"""Tests for AI handlers and batch submission."""

from __future__ import annotations

import random

from djobs.api.ai_handlers import (
    AI_HANDLERS,
    ai_classify_handler,
    ai_generate_handler,
    ai_summarize_handler,
)
from djobs.core.errors import RetryableJobError
from djobs.queue.service import QueueService
from djobs.storage.sqlite import SQLiteJobRepository

# ------------------------------------------------------------------
# ai_summarize_handler
# ------------------------------------------------------------------


def test_summarize_produces_result() -> None:
    random.seed(99)  # avoid transient failure
    payload = {"text": "Hello world this is a test sentence"}
    ai_summarize_handler(payload)

    assert "result" in payload
    assert payload["tokens_used"] > 0
    assert payload["cost_usd"] > 0


def test_summarize_rate_limit() -> None:
    random.seed(0)  # seed that triggers failure
    # Run many times to check RetryableJobError is possible
    errors = 0
    for i in range(200):
        random.seed(i)
        p = {"text": "test"}
        try:
            ai_summarize_handler(p)
        except RetryableJobError:
            errors += 1
    assert errors > 0, "Expected at least one rate limit error"


# ------------------------------------------------------------------
# ai_classify_handler
# ------------------------------------------------------------------


def test_classify_produces_result() -> None:
    random.seed(42)
    payload = {"text": "Great product!", "labels": ["pos", "neg"]}
    ai_classify_handler(payload)

    assert payload["result"] in ["pos", "neg"]
    assert 0.0 < payload["confidence"] < 1.0
    assert payload["tokens_used"] > 0


# ------------------------------------------------------------------
# ai_generate_handler
# ------------------------------------------------------------------


def test_generate_produces_result() -> None:
    random.seed(99)
    payload = {"prompt": "Explain queues", "max_tokens": 100}
    ai_generate_handler(payload)

    assert "result" in payload
    assert payload["input_tokens"] > 0
    assert payload["output_tokens"] > 0
    assert payload["cost_usd"] > 0


# ------------------------------------------------------------------
# AI_HANDLERS registry
# ------------------------------------------------------------------


def test_ai_handlers_registry() -> None:
    assert "ai.summarize" in AI_HANDLERS
    assert "ai.classify" in AI_HANDLERS
    assert "ai.generate" in AI_HANDLERS


# ------------------------------------------------------------------
# submit_batch
# ------------------------------------------------------------------


def test_submit_batch(tmp_path) -> None:
    repo = SQLiteJobRepository.from_path(tmp_path / "jobs.db")
    queue = QueueService(repo)

    specs = [
        {"type": "ai.summarize", "payload": {"text": "hello"}, "max_attempts": 3},
        {"type": "ai.classify", "payload": {"text": "great"}, "max_attempts": 2},
    ]
    jobs = queue.submit_batch(specs, correlation_id="batch-1")

    assert len(jobs) == 2
    assert all(j.correlation_id == "batch-1" for j in jobs)
    assert jobs[0].type == "ai.summarize"
    assert jobs[1].type == "ai.classify"
    assert jobs[0].max_attempts == 3


def test_submit_batch_empty(tmp_path) -> None:
    repo = SQLiteJobRepository.from_path(tmp_path / "jobs.db")
    queue = QueueService(repo)

    jobs = queue.submit_batch([])
    assert jobs == []
