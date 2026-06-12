"""Tolerant correlation_id matching.

A ``correlation_id`` is usually a workspace path, and agents and humans spell
the same path inconsistently — ``c:\\proj`` vs ``C:/proj`` vs ``c:/proj/``.
correlation_id is matched as an exact string, so without normalization a task
enqueued under one spelling would be invisible to ``resume_session`` /
``list_tasks`` / CLI filters called with another, making real work look lost —
the exact thing that erodes trust in crash recovery.

The stored value is never rewritten (that could corrupt existing data);
callers instead query for every equivalent spelling. The rules are deliberately
conservative so non-path ids (UUIDs, custom session ids) collapse to just the
original value and are unaffected:

- ``\\`` and ``/`` are interchangeable (Windows vs POSIX);
- a trailing separator does not matter;
- a leading Windows drive letter is case-insensitive (``c:`` == ``C:``).

This module is the single source of truth; ``cli.py`` and ``mcp_server.py``
both import it so the rules can never drift apart.
"""

from __future__ import annotations


def correlation_id_variants(correlation_id: str) -> list[str]:
    """Return equivalent spellings of *correlation_id* for tolerant matching."""
    variants: set[str] = {correlation_id}

    forward = correlation_id.replace("\\", "/")
    variants.add(forward)
    variants.add(forward.replace("/", "\\"))

    for value in list(variants):
        trimmed = value.rstrip("/\\")
        if trimmed:
            variants.add(trimmed)

    for value in list(variants):
        if len(value) >= 2 and value[1] == ":" and value[0].isalpha():
            variants.add(value[0].lower() + value[1:])
            variants.add(value[0].upper() + value[1:])

    return sorted(variants)
