"""Minimal MCP surface for coding checkpoints and context recovery.

The default coding-agent server deliberately exposes only six tools. The full
multi-agent queue API remains available through ``djobs-mcp-full`` for users who
explicitly need claims, leases, agent heartbeats, fleet views, or audit queries.
Keeping those schemas out of the default server reduces the fixed MCP context
that every coding session must load before any useful work begins.
"""

from __future__ import annotations

from typing import Any

from mcp.server.fastmcp import FastMCP

from djobs.delta_mcp import resume_delta as _resume_delta
from djobs.low_token_mcp import complete_batch as _complete_batch
from djobs.low_token_mcp import enqueue_batch as _enqueue_batch
from djobs.mcp_server import _get_queue
from djobs.mcp_server import check_task as _check_task
from djobs.mcp_server import fail_task as _fail_task
from djobs.mcp_server import work_receipt as _work_receipt

_server = FastMCP(
    "djobs",
    instructions=(
        "Optional coding checkpoints. Use only for genuinely long or interruption-prone "
        "coding work. Prefer resume_delta for bounded recovery, enqueue_batch and "
        "complete_batch for multi-file work, check_task only for one full record, "
        "fail_task for an unrecoverable unit, and work_receipt for a final handoff. "
        "Do not call djobs merely because a session started or the user said continue."
    ),
)


@_server.tool()
def resume_delta(
    correlation_id: str,
    since_revision: int = 0,
    max_items: int = 5,
    token_budget: int = 600,
    known_state_hash: str | None = None,
    include_blocked: bool = False,
) -> str:
    """Return only durable workspace changes since a saved revision."""

    return _resume_delta(
        correlation_id=correlation_id,
        since_revision=since_revision,
        max_items=max_items,
        token_budget=token_budget,
        known_state_hash=known_state_hash,
        include_blocked=include_blocked,
    )


@_server.tool()
def enqueue_batch(
    tasks: str | list[dict[str, Any]],
    correlation_id: str | None = None,
) -> str:
    """Checkpoint one or more coding units in a single tool call."""

    return _enqueue_batch(tasks=tasks, correlation_id=correlation_id)


@_server.tool()
def complete_batch(completions: str | list[str | dict[str, Any]]) -> str:
    """Complete one or more checkpointed coding units in a single call."""

    return _complete_batch(completions=completions)


@_server.tool()
def check_task(task_id: str) -> str:
    """Retrieve one complete task record when the compact delta is insufficient."""

    return _check_task(task_id=task_id)


@_server.tool()
def fail_task(task_id: str, error: str) -> str:
    """Record that one checkpointed coding unit could not be completed."""

    return _fail_task(task_id=task_id, error=error)


@_server.tool()
def work_receipt(correlation_id: str | None = None) -> str:
    """Return an evidence-backed coding handoff without replaying the chat."""

    return _work_receipt(correlation_id=correlation_id)


def main() -> None:
    """Run the minimal coding MCP server over stdio."""

    _get_queue()
    _server.run(transport="stdio")


if __name__ == "__main__":
    main()
