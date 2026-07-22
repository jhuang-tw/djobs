"""Automatic lifecycle integration across separate coding-agent sessions."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from djobs import handoff, lifecycle
from djobs.queue.service import QueueService
from djobs.storage.sqlite import SQLiteJobRepository
from djobs.workspace import resolve_workspace


def _git_repo(path: Path) -> Path:
    path.mkdir()
    subprocess.run(
        ["git", "init", "-q", str(path)],
        check=True,
        capture_output=True,
        text=True,
    )
    return path


def test_codex_turn_is_released_and_claimed_by_claude(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = _git_repo(tmp_path / "project")
    database = tmp_path / "shared.db"
    repository = SQLiteJobRepository.from_path(database)
    queue = QueueService(repository)
    monkeypatch.setattr(handoff, "configure", lambda _path: queue)
    monkeypatch.setenv("DJOBS_DB", str(database))

    tracked = lifecycle.user_prompt_submit(
        {
            "cwd": str(root),
            "session_id": "codex-session",
            "prompt": "Implement the parser and preserve the existing API.",
        },
        agent_type="codex",
    )
    assert "automatically tracking" in tracked["additionalContext"]

    workspace = resolve_workspace(cwd=str(root))
    jobs = repository.list_jobs_by_correlation_ids([workspace.workspace_id])
    assert len(jobs) == 1
    task = jobs[0]
    assert task.status.value == "running"
    assert task.leased_by is not None and task.leased_by.startswith("codex:")

    lifecycle.post_tool_use(
        {
            "cwd": str(root),
            "session_id": "codex-session",
            "tool_name": "apply_patch",
            "tool_response": "Updated src/parser.py",
        },
        agent_type="codex",
    )
    events = repository.list_events(task.id)
    assert any(event.event_type == "tool_observed" for event in events)

    lifecycle.stop(
        {
            "cwd": str(root),
            "session_id": "codex-session",
            "last_assistant_message": "Parser is implemented; edge-case tests remain.",
        },
        agent_type="codex",
    )
    released = repository.get_job(task.id)
    assert released is not None
    assert released.status.value == "pending"
    assert released.leased_by is None

    resumed = lifecycle.session_start(
        {"cwd": str(root), "session_id": "claude-session"},
        agent_type="claude",
    )
    assert task.id in resumed["additionalContext"]
    claimed = repository.get_job(task.id)
    assert claimed is not None
    assert claimed.status.value == "running"
    assert claimed.leased_by is not None and claimed.leased_by.startswith("claude:")
