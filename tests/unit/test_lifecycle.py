"""Passive lifecycle integration across separate coding-agent clients."""

from __future__ import annotations

import json
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
    (path / "tracked.txt").write_text("base\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(path), "add", "."], check=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(path),
            "-c",
            "user.name=Test",
            "-c",
            "user.email=test@example.com",
            "commit",
            "-qm",
            "base",
        ],
        check=True,
    )
    return path


def test_hooks_observe_but_never_create_claim_or_release_tasks(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = _git_repo(tmp_path / "project")
    database = tmp_path / "shared.db"
    repository = SQLiteJobRepository.from_path(database)
    queue = QueueService(repository)
    monkeypatch.setattr(handoff, "configure", lambda _path: queue)
    monkeypatch.setenv("DJOBS_DB", str(database))

    assert lifecycle.user_prompt_submit(
        {"cwd": str(root), "session_id": "codex-session", "prompt": "Explain this repo"},
        agent_type="codex",
    ) == {}
    workspace = resolve_workspace(cwd=str(root))
    assert repository.list_jobs_by_correlation_ids([workspace.workspace_id]) == []

    result = json.loads(
        handoff.checkpoint(
            "Implement parser",
            path="src/parser.py",
            cwd=str(root),
            agent_type="codex",
            session_id="codex-session",
        )
    )
    task_id = result["task_id"]
    task = repository.get_job(task_id)
    assert task is not None and task.status.value == "running"
    original_owner = task.leased_by

    context = lifecycle.session_start(
        {"cwd": str(root), "session_id": "claude-session"},
        agent_type="claude",
    )
    assert "no task was claimed automatically" in context["additionalContext"]
    task = repository.get_job(task_id)
    assert task is not None and task.leased_by == original_owner

    (root / "tracked.txt").write_text("changed by an unknown client\n", encoding="utf-8")
    lifecycle.post_tool_use(
        {
            "cwd": str(root),
            "session_id": "gemini-session",
            "tool_name": "write_file",
            "tool_input": {"file_path": "tracked.txt"},
            "tool_response": {"success": True, "output": "updated"},
        },
        agent_type="gemini",
    )
    count = repository._connection.execute(
        "SELECT COUNT(*) FROM agent_observations WHERE correlation_id = ?",
        (workspace.workspace_id,),
    ).fetchone()[0]
    assert count >= 2
    task = repository.get_job(task_id)
    assert task is not None and task.leased_by == original_owner

    lifecycle.stop({"cwd": str(root)}, agent_type="codex")
    lifecycle.session_end(
        {"cwd": str(root), "session_id": "codex-session", "reason": "exit"},
        agent_type="codex",
    )
    task = repository.get_job(task_id)
    assert task is not None and task.status.value == "running"
    assert task.leased_by == original_owner


def test_unknown_client_name_uses_same_observation_protocol(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = _git_repo(tmp_path / "project")
    database = tmp_path / "shared.db"
    repository = SQLiteJobRepository.from_path(database)
    queue = QueueService(repository)
    monkeypatch.setattr(handoff, "configure", lambda _path: queue)
    monkeypatch.setenv("DJOBS_DB", str(database))

    lifecycle.post_tool_failure(
        {
            "cwd": str(root),
            "session_id": "custom-1",
            "tool_name": "custom_editor",
            "error": "write failed",
        },
        agent_type="future-agent",
    )
    row = repository._connection.execute(
        "SELECT agent_type, event_type, summary FROM agent_observations ORDER BY created_at DESC"
    ).fetchone()
    assert row["agent_type"] == "future-agent"
    assert row["event_type"] == "tool_failure"
    assert "write failed" in row["summary"]


def test_first_snapshot_reports_existing_dirty_tree_from_uninstrumented_agent(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = _git_repo(tmp_path / "project")
    (root / "tracked.txt").write_text("changed before djobs started\n", encoding="utf-8")
    database = tmp_path / "shared.db"
    repository = SQLiteJobRepository.from_path(database)
    queue = QueueService(repository)
    monkeypatch.setattr(handoff, "configure", lambda _path: queue)
    monkeypatch.setenv("DJOBS_DB", str(database))

    context = lifecycle.session_start(
        {"cwd": str(root), "session_id": "future-session"},
        agent_type="future-agent",
    )
    assert "repository_change" in context["additionalContext"]
    row = repository._connection.execute(
        "SELECT summary FROM agent_observations WHERE event_type = 'repository_change'"
    ).fetchone()
    assert row is not None
    assert "tracked.txt" in row["summary"]
