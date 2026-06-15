"""Pause state for djobs.

Sometimes a durable workflow gets in the way: a task wedges on a command that
hangs, and because the managed guidance tells agents to ``resume_session`` and
continue, the agent keeps retrying the same stuck work instead of moving on.

Pausing is the escape hatch. It is a temporary, fully reversible switch that
tells the MCP tools (and therefore the agent) to stop resuming and enqueuing
durable work — without deleting any tasks. The state is a single marker file
placed next to the active queue database, so it applies to exactly the queue the
agent reads and writes, and survives process restarts until it is cleared.

The CLI (``djobs pause`` / ``djobs unpause``), the MCP server, and the VS Code
sidebar all go through these helpers so the rules cannot drift apart.
"""

from __future__ import annotations

import os
from pathlib import Path


def pause_marker_path(db_path: str | os.PathLike[str]) -> Path:
    """Return the marker file path that represents the paused state for *db_path*."""
    return Path(f"{os.fspath(db_path)}.paused")


def is_paused(db_path: str | os.PathLike[str]) -> bool:
    """Return ``True`` when the queue at *db_path* is currently paused."""
    return pause_marker_path(db_path).exists()


def set_paused(db_path: str | os.PathLike[str], paused: bool) -> bool:
    """Pause or unpause the queue at *db_path*.

    Returns ``True`` when the state actually changed (so callers can report
    "already paused" / "was not paused" without racing on a second check).
    """
    marker = pause_marker_path(db_path)
    if paused:
        if marker.exists():
            return False
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text("paused\n", encoding="utf-8")
        return True
    if marker.exists():
        marker.unlink()
        return True
    return False
