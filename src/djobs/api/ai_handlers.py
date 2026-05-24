"""Simulated AI job handlers for Phase 8 demo.

These handlers simulate real AI workloads:
- ai.summarize: text summarization (simulated token usage + latency)
- ai.classify: text classification (fast, cheap)
- ai.generate: long-running generation (expensive, may fail with rate limit)

Each handler writes cost/token metadata into the job payload for tracking.
"""

from __future__ import annotations

import random
import time
from typing import Any

from djobs.core.errors import RetryableJobError


def ai_summarize_handler(payload: dict[str, Any]) -> Any:
    """Simulate an AI summarization call.

    Payload keys:
        text: str — input text to summarize.

    Side effects:
        Adds ``result``, ``tokens_used``, ``cost_usd`` to payload.
    """
    text = payload.get("text", "")
    tokens = max(len(text.split()), 10)

    # Simulate 5% transient failure (rate limit)
    if random.random() < 0.05:
        raise RetryableJobError("AI API rate limit exceeded (429)")

    time.sleep(0.05)  # simulate latency

    payload["result"] = f"Summary of {tokens} tokens: {text[:60]}..."
    payload["tokens_used"] = tokens
    payload["cost_usd"] = round(tokens * 0.00003, 6)
    return payload


def ai_classify_handler(payload: dict[str, Any]) -> Any:
    """Simulate an AI classification call.

    Payload keys:
        text: str — input text to classify.
        labels: list[str] — candidate labels.
    """
    text = payload.get("text", "")
    labels = payload.get("labels", ["positive", "negative", "neutral"])
    tokens = max(len(text.split()), 5)

    time.sleep(0.02)

    chosen = random.choice(labels)
    payload["result"] = chosen
    payload["confidence"] = round(random.uniform(0.7, 0.99), 3)
    payload["tokens_used"] = tokens
    payload["cost_usd"] = round(tokens * 0.00001, 6)
    return payload


def ai_generate_handler(payload: dict[str, Any]) -> Any:
    """Simulate a long-running AI generation call.

    Payload keys:
        prompt: str — generation prompt.
        max_tokens: int — maximum output tokens (default 500).

    This handler has a higher failure rate to demonstrate retry + cost control.
    """
    prompt = payload.get("prompt", "")
    max_tokens = payload.get("max_tokens", 500)

    # Simulate 15% transient failure
    if random.random() < 0.15:
        raise RetryableJobError("AI API timeout — generation too slow")

    time.sleep(0.1)  # longer latency

    output_tokens = random.randint(max_tokens // 2, max_tokens)
    input_tokens = max(len(prompt.split()), 10)
    total_tokens = input_tokens + output_tokens

    payload["result"] = f"Generated {output_tokens} tokens for: {prompt[:40]}..."
    payload["input_tokens"] = input_tokens
    payload["output_tokens"] = output_tokens
    payload["tokens_used"] = total_tokens
    payload["cost_usd"] = round(total_tokens * 0.00006, 6)
    return payload


# Registry helper
AI_HANDLERS: dict[str, Any] = {
    "ai.summarize": ai_summarize_handler,
    "ai.classify": ai_classify_handler,
    "ai.generate": ai_generate_handler,
}
