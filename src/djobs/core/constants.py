"""Shared domain constants.

Single source of truth for values that were previously duplicated across
``cli.py``, ``mcp_server.py`` (and mirrored in the VS Code extension). Keeping
them here prevents the copies from silently drifting apart.
"""

from __future__ import annotations

# A still-incomplete task older than this many days is flagged as likely
# abandoned, so the agent and sidebar read it as "archive me" rather than
# nagging forever. The VS Code extension mirrors this in
# ``vscode-ext/src/tasksProvider.ts`` (STALE_AFTER_DAYS) — keep them in sync.
STALE_AFTER_DAYS = 7
