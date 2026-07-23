"""Passive lifecycle integration across separate coding-agent clients."""

from __future__ import annotations

import json
import subprocess
import threading
from pathlib import Path

import pytest

from djobs import handoff, lifecycle, observations
from djobs.queue.service import QueueService
from djobs.storage.sqlite import SQLiteJobRepository
from djobs.workspace import resolve_agent_session, resolve_workspace


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


def _queue(database: Path) -> tuple[SQLiteJobRepository, QueueService]:
    repository = SQLiteJobRepository.from_path(database)
    return repository, QueueService(repository)


def test_hooks_observe_but_never_create_claim_or_release_tasks(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = _git_repo(tmp_path / "project")
    database = tmp_path / "shared.db"
    repository, queue = _queue(database)
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
    repository, queue = _queue(database)
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
    repository, queue = _queue(database)
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


def test_snapshot_detects_repeated_content_changes_with_same_git_status(tmp_path: Path) -> None:
    root = _git_repo(tmp_path / "project")
    database = tmp_path / "shared.db"
    repository, _queue_service = _queue(database)
    workspace = resolve_workspace(cwd=str(root))
    agent = resolve_agent_session(
        workspace,
        agent_type="sidecar",
        session_id="snapshot-session",
    )

    assert not observations.capture_repository_snapshot(repository, workspace, agent)
    (root / "tracked.txt").write_text("first edit\n", encoding="utf-8")
    assert observations.capture_repository_snapshot(repository, workspace, agent)
    (root / "tracked.txt").write_text("second edit with same M status\n", encoding="utf-8")
    assert observations.capture_repository_snapshot(repository, workspace, agent)

    count = repository._connection.execute(
        "SELECT COUNT(*) FROM agent_observations WHERE event_type = 'repository_change'"
    ).fetchone()[0]
    assert count == 2


def test_concurrent_snapshots_write_one_repository_change(tmp_path: Path) -> None:
    root = _git_repo(tmp_path / "project")
    database = tmp_path / "shared.db"
    repo1, _queue1 = _queue(database)
    repo2, _queue2 = _queue(database)
    workspace = resolve_workspace(cwd=str(root))
    agent1 = resolve_agent_session(workspace, agent_type="codex", session_id="one")
    agent2 = resolve_agent_session(workspace, agent_type="gemini", session_id="two")

    assert not observations.capture_repository_snapshot(repo1, workspace, agent1)
    (root / "tracked.txt").write_text("concurrent edit\n", encoding="utf-8")

    barrier = threading.Barrier(2)
    results: list[bool] = []

    def capture(repo: SQLiteJobRepository, agent: object) -> None:
        barrier.wait()
        results.append(observations.capture_repository_snapshot(repo, workspace, agent))

    first = threading.Thread(target=capture, args=(repo1, agent1))
    second = threading.Thread(target=capture, args=(repo2, agent2))
    first.start()
    second.start()
    first.join()
    second.join()

    assert sorted(results) == [False, True]
    count = repo1._connection.execute(
        "SELECT COUNT(*) FROM agent_observations WHERE event_type = 'repository_change'"
    ).fetchone()[0]
    assert count == 1


def test_metadata_remains_valid_json_when_bounded(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = _git_repo(tmp_path / "project")
    database = tmp_path / "shared.db"
    repository, _queue_service = _queue(database)
    workspace = resolve_workspace(cwd=str(root))
    agent = resolve_agent_session(workspace, agent_type="custom", session_id="metadata")
    monkeypatch.setattr(observations, "_MAX_METADATA", 120)

    observations.record_observation(
        repository,
        workspace,
        agent,
        "tool_result",
        "done",
        metadata={"huge": "x" * 1000},
    )
    raw = repository._connection.execute(
        "SELECT metadata_json FROM agent_observations ORDER BY created_at DESC"
    ).fetchone()[0]
    decoded = json.loads(raw)
    assert decoded["truncated"] is True


def test_observation_retention_is_bounded(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = _git_repo(tmp_path / "project")
    database = tmp_path / "shared.db"
    repository, _queue_service = _queue(database)
    workspace = resolve_workspace(cwd=str(root))
    agent = resolve_agent_session(workspace, agent_type="custom", session_id="retention")
    monkeypatch.setattr(observations, "_MAX_OBSERVATIONS_PER_WORKSPACE", 3)

    for index in range(5):
        observations.record_observation(
            repository,
            workspace,
            agent,
            "tool_result",
            f"event {index}",
        )

    rows = repository._connection.execute(
        "SELECT summary FROM agent_observations ORDER BY created_at DESC, id DESC"
    ).fetchall()
    assert len(rows) == 3
    assert {row["summary"] for row in rows} == {"event 2", "event 3", "event 4"}


def test_tool_observation_redacts_common_secrets(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = _git_repo(tmp_path / "project")
    database = tmp_path / "shared.db"
    repository, queue = _queue(database)
    monkeypatch.setattr(handoff, "configure", lambda _path: queue)
    monkeypatch.setenv("DJOBS_DB", str(database))

    lifecycle.post_tool_use(
        {
            "cwd": str(root),
            "session_id": "secret-session",
            "tool_name": "Bash",
            "tool_input": {"command": "curl -H 'Authorization: Bearer abc123' https://example"},
            "tool_response": {"stdout": "token=super-secret"},
        },
        agent_type="custom",
    )
    summary = repository._connection.execute(
        "SELECT summary FROM agent_observations WHERE event_type = 'tool_result'"
    ).fetchone()[0]
    assert "abc123" not in summary
    assert "super-secret" not in summary
    assert "<redacted>" in summary
