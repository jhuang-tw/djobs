"""Passive lifecycle integration across separate coding-agent clients."""

from __future__ import annotations

import json
import subprocess
import threading
from pathlib import Path

import pytest

from djobs import handoff, lifecycle, observations
from djobs.core.pause import set_paused
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

    assert (
        lifecycle.user_prompt_submit(
            {"cwd": str(root), "session_id": "codex-session", "prompt": "Explain this repo"},
            agent_type="codex",
        )
        == {}
    )
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
    event_counts = {
        row["event_type"]: row["count"]
        for row in repository._connection.execute(
            "SELECT event_type, COUNT(*) AS count FROM agent_observations GROUP BY event_type"
        ).fetchall()
    }
    assert event_counts.get("tool_result") == 1
    assert event_counts.get("repository_change", 0) == 0
    task = repository.get_job(task_id)
    assert task is not None and task.leased_by == original_owner

    recovered = lifecycle.session_start(
        {"cwd": str(root), "session_id": "future-session"},
        agent_type="future-agent",
    )
    assert "repository_change" in recovered["additionalContext"]
    repository_changes = repository._connection.execute(
        "SELECT COUNT(*) FROM agent_observations WHERE event_type = 'repository_change'"
    ).fetchone()[0]
    assert repository_changes == 1

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


def test_pause_stops_automatic_capture_and_recovery(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = _git_repo(tmp_path / "project")
    database = tmp_path / "shared.db"
    repository, queue = _queue(database)
    monkeypatch.setattr(handoff, "configure", lambda _path: queue)
    monkeypatch.setenv("DJOBS_DB", str(database))
    set_paused(database, True)

    payload = {
        "cwd": str(root),
        "session_id": "paused-session",
        "prompt": "This prompt must not be stored while paused",
    }
    assert lifecycle.prompt_context(payload, agent_type="codex") == {}
    assert (
        lifecycle.post_tool_use(
            {
                "cwd": str(root),
                "session_id": "paused-session",
                "tool_name": "write_file",
                "tool_response": {"success": True, "output": "done"},
            },
            agent_type="codex",
        )
        == {}
    )
    assert lifecycle.session_end(payload, agent_type="codex") == {}

    count = repository._connection.execute("SELECT COUNT(*) FROM agent_observations").fetchone()[0]
    assert count == 0


def test_prompt_context_reads_history_before_storing_current_prompt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = _git_repo(tmp_path / "project")
    database = tmp_path / "shared.db"
    repository, queue = _queue(database)
    monkeypatch.setattr(handoff, "configure", lambda _path: queue)
    monkeypatch.setenv("DJOBS_DB", str(database))
    workspace = resolve_workspace(cwd=str(root))
    historical_agent = resolve_agent_session(
        workspace,
        agent_type="codex",
        session_id="historical-session",
    )
    observations.record_observation(
        repository,
        workspace,
        historical_agent,
        "tool_failure",
        "OAuth callback plus-sign normalization failed in the previous session",
    )

    current_prompt = "Continue OAuth callback plus-sign recovery CURRENT-REQUEST-ONLY"
    context = lifecycle.prompt_context(
        {
            "cwd": str(root),
            "session_id": "current-session",
            "prompt": current_prompt,
        },
        agent_type="codex",
    )

    assert "previous session" in context["additionalContext"]
    assert "CURRENT-REQUEST-ONLY" not in context["additionalContext"]
    stored = repository._connection.execute(
        "SELECT summary FROM agent_observations WHERE event_type = 'user_intent'"
    ).fetchall()
    assert current_prompt in {row["summary"] for row in stored}


def test_fail_open_tool_capture_records_bounded_diagnostic(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import sqlite3

    root = _git_repo(tmp_path / "project")
    database = tmp_path / "shared.db"
    _repository, queue = _queue(database)
    monkeypatch.setattr(handoff, "configure", lambda _path: queue)
    monkeypatch.setenv("DJOBS_DB", str(database))

    def fail_record(*_args, **_kwargs):
        raise sqlite3.OperationalError("database is locked with TOKEN=secret-value")

    monkeypatch.setattr(lifecycle, "record_observation", fail_record)

    result = lifecycle.post_tool_use(
        {
            "cwd": str(root),
            "session_id": "locked",
            "tool_name": "edit",
            "tool_response": {"success": True},
        },
        agent_type="copilot",
    )

    assert result == {}
    from djobs.diagnostics import list_diagnostics

    diagnostic_repo = SQLiteJobRepository.from_path(database)
    diagnostics = list_diagnostics(diagnostic_repo)
    diagnostic_repo.close()
    assert any(item["component"] == "lifecycle.tool_observation" for item in diagnostics)
    assert "secret-value" not in json.dumps(diagnostics)


def test_large_session_capsule_keeps_structured_fields(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = _git_repo(tmp_path / "project")
    database = tmp_path / "shared.db"
    repository, _queue_service = _queue(database)
    monkeypatch.setenv("DJOBS_DB", str(database))
    workspace = resolve_workspace(cwd=str(root))
    agent = resolve_agent_session(workspace, agent_type="codex", session_id="large-capsule")

    observations.record_observation(
        repository,
        workspace,
        agent,
        "user_intent",
        "Preserve the public API while completing a deliberately large hardening session",
    )
    for index in range(8):
        observations.record_observation(
            repository,
            workspace,
            agent,
            "tool_result",
            f"progress {index}: " + ("implementation evidence " * 20),
        )
    for index in range(5):
        observations.record_observation(
            repository,
            workspace,
            agent,
            "tool_failure",
            f"failure {index}: " + ("diagnostic detail " * 20),
        )

    assert observations.record_session_capsule(
        repository,
        workspace,
        agent,
        reason="test-large-capsule",
        next_hint="Run the complete compatibility matrix and inspect every failure " * 10,
    )
    row = repository._connection.execute(
        "SELECT metadata_json FROM agent_observations "
        "WHERE event_type = 'session_capsule' ORDER BY created_at DESC LIMIT 1"
    ).fetchone()
    metadata = json.loads(row["metadata_json"])

    assert len(row["metadata_json"]) <= observations._MAX_CAPSULE_METADATA
    assert metadata["capsule_schema"] == 2
    assert isinstance(metadata["goal"], str) and metadata["goal"]
    assert isinstance(metadata["progress"], list)
    assert isinstance(metadata["failures"], list)
    assert "next" in metadata
    assert isinstance(metadata["source_event_ids"], list)
    assert metadata["provenance"]["goal"]["source"] == "user_intent"
    assert metadata["provenance"]["next"]["advisory"] is True
    assert metadata["truncated_fields"]
