"""Product-level regression: repository memory and explicit cross-agent handoff."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

import pytest

from djobs import mcp_server
from djobs.handoff import checkpoint, handoff, sync_workspace
from djobs.mcp_adoption import remember_current_request


def _repo(path: Path) -> Path:
    path.mkdir()
    subprocess.run(["git", "init", "-q", str(path)], check=True)
    return path


def _json(raw: str) -> dict[str, Any]:
    value = json.loads(raw)
    assert isinstance(value, dict)
    return value


def test_generic_mcp_memory_survives_session_and_agent_handoff(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Exercise the primary djobs product flow from an empty repository to completion."""

    database = tmp_path / "djobs.db"
    monkeypatch.setenv("DJOBS_DB", str(database))
    mcp_server._queue = None
    repo = _repo(tmp_path / "repo")

    initial = _json(
        sync_workspace(
            cwd=str(repo),
            agent_type="mcp",
            session_id="first",
            query="Fix the OAuth callback without changing the API",
        )
    )
    assert initial == {"ok": True, "workspace": "repo", "state": "empty"}

    # Generic MCP clients may have no lifecycle hooks. The first sync call therefore
    # remembers the current request after recovery so it becomes future context.
    remember_current_request(
        "Fix the OAuth callback without changing the API",
        roots=None,
        cwd=str(repo),
        agent_type=None,
    )

    recovered = _json(
        sync_workspace(
            cwd=str(repo),
            agent_type="codex",
            session_id="second",
            query="OAuth callback",
            context_tier="audit",
        )
    )
    assert recovered["ok"] is True
    assert "Fix the OAuth callback without changing the API" in json.dumps(
        recovered["observations"], ensure_ascii=False
    )

    created = _json(
        checkpoint(
            "Finish OAuth callback integration",
            path="src/oauth.py",
            cwd=str(repo),
            agent_type="codex",
            session_id="second",
        )
    )
    task_id = str(created["task_id"])
    assert created["state"] == "claimed"

    released = _json(
        handoff(
            task_id,
            "Parser fixed; callback integration test remains.",
            cwd=str(repo),
            agent_type="codex",
            session_id="second",
        )
    )
    assert released["state"] == "pending"

    next_agent = _json(
        sync_workspace(
            cwd=str(repo),
            agent_type="claude",
            session_id="third",
            query="OAuth callback integration",
            context_tier="audit",
        )
    )
    task = next(item for item in next_agent["tasks"] if item["id"] == task_id)
    assert "integration test remains" in task.get("evidence", "")

    claimed = _json(
        checkpoint(
            "Finish OAuth callback integration",
            path="src/oauth.py",
            cwd=str(repo),
            agent_type="claude",
            session_id="third",
        )
    )
    assert claimed["task_id"] == task_id
    assert claimed["state"] == "claimed"

    completed = _json(
        handoff(
            task_id,
            "Callback integration test passes.",
            completed=True,
            cwd=str(repo),
            agent_type="claude",
            session_id="third",
        )
    )
    assert completed == {"ok": True, "task_id": task_id, "state": "succeeded"}

    final = _json(
        sync_workspace(
            cwd=str(repo),
            agent_type="gemini",
            session_id="fourth",
            query="OAuth callback",
            context_tier="audit",
        )
    )
    assert all(item["id"] != task_id for item in final["tasks"])
    assert any(item["id"] == task_id for item in final["recent_completed"])
