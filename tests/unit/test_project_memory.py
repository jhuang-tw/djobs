from __future__ import annotations

import json
import sqlite3
import subprocess
from pathlib import Path

import pytest

from djobs import handoff, lifecycle
from djobs.handoff import checkpoint, sync_workspace
from djobs.memory import memory_action
from djobs.observations import recent_observations, search_observations
from djobs.queue.service import QueueService
from djobs.storage.sqlite import SQLiteJobRepository
from djobs.workspace import resolve_agent_session, resolve_workspace


def _git_repo(path: Path) -> Path:
    path.mkdir()
    subprocess.run(["git", "init", "-q", str(path)], check=True)
    (path / "app.py").write_text("print('base')\n", encoding="utf-8")
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


@pytest.fixture
def memory_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    root = _git_repo(tmp_path / "project")
    database = tmp_path / "shared.db"
    repository = SQLiteJobRepository.from_path(database)
    queue = QueueService(repository)
    monkeypatch.setattr(handoff, "configure", lambda _path: queue)
    monkeypatch.setenv("DJOBS_DB", str(database))
    return root, database, repository


def test_user_prompt_is_passive_memory_not_a_task(memory_env) -> None:
    root, database, repository = memory_env
    payload = {
        "cwd": str(root),
        "session_id": "session-one",
        "prompt": "Fix the login retry bug without changing the public API",
    }

    assert lifecycle.user_prompt_submit(payload, agent_type="copilot") == {}

    workspace = resolve_workspace(cwd=str(root))
    memories = recent_observations(repository, workspace, limit=10)
    assert memories[0]["event"] == "user_intent"
    assert "login retry bug" in memories[0]["summary"]
    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == 0


def test_user_prompt_redacts_secrets_and_supports_opt_out(memory_env) -> None:
    root, _database, repository = memory_env
    base = {"cwd": str(root), "session_id": "privacy"}

    lifecycle.user_prompt_submit(
        {**base, "prompt": "Use API_KEY=super-secret to debug login"},
        agent_type="claude",
    )
    lifecycle.user_prompt_submit(
        {**base, "prompt": "[djobs:no-memory] do not store this private experiment"},
        agent_type="claude",
    )

    workspace = resolve_workspace(cwd=str(root))
    memories = recent_observations(repository, workspace, limit=10)
    text = "\n".join(item["summary"] for item in memories)
    assert "super-secret" not in text
    assert "<redacted>" in text
    assert "private experiment" not in text


def test_pre_compact_creates_structured_session_capsule(memory_env) -> None:
    root, _database, repository = memory_env
    payload = {"cwd": str(root), "session_id": "capsule"}
    lifecycle.user_prompt_submit(
        {**payload, "prompt": "Repair parser tests and keep Python 3.10 support"},
        agent_type="copilot",
    )
    lifecycle.post_tool_failure(
        {
            **payload,
            "tool_name": "bash",
            "tool_input": {"command": "pytest tests/test_parser.py"},
            "error": "2 tests failed",
        },
        agent_type="copilot",
    )
    lifecycle.post_tool_use(
        {
            **payload,
            "tool_name": "edit",
            "tool_input": {"file_path": "src/parser.py"},
            "tool_response": {"success": True, "message": "updated fallback parser"},
        },
        agent_type="copilot",
    )

    lifecycle.pre_compact({**payload, "trigger": "auto"}, agent_type="copilot")

    workspace = resolve_workspace(cwd=str(root))
    memories = recent_observations(repository, workspace, limit=20)
    capsule = next(item for item in memories if item["event"] == "session_capsule")
    assert "Goal: Repair parser tests" in capsule["summary"]
    assert "Progress:" in capsule["summary"]
    assert "Failed:" in capsule["summary"]


def test_search_prefers_relevant_older_memory(memory_env) -> None:
    root, _database, repository = memory_env
    workspace = resolve_workspace(cwd=str(root))
    agent = resolve_agent_session(workspace, agent_type="copilot", session_id="search")
    from djobs.observations import record_observation

    record_observation(
        repository,
        workspace,
        agent,
        "session_capsule",
        "Goal: fix OAuth callback loop; Failed: redirect URI normalization broke state checks",
    )
    for index in range(8):
        record_observation(
            repository,
            workspace,
            agent,
            "tool_result",
            f"unrelated formatting task {index} completed",
        )

    results = search_observations(repository, workspace, "OAuth redirect callback", limit=4)

    assert results
    assert "OAuth callback" in results[0]["summary"]
    assert results[0]["id"]


def test_sync_workspace_query_returns_relevant_memory(memory_env) -> None:
    root, _database, _repository = memory_env
    lifecycle.user_prompt_submit(
        {
            "cwd": str(root),
            "session_id": "prior",
            "prompt": "Do not replace Zustand; the store API is public",
        },
        agent_type="copilot",
    )

    result = json.loads(
        sync_workspace(
            cwd=str(root),
            agent_type="copilot",
            session_id="next",
            query="Should I replace Zustand store?",
        )
    )

    assert result["query"] == "Should I replace Zustand store?"
    assert any("Do not replace Zustand" in item["summary"] for item in result["observations"])


def test_memory_forget_and_clear_preserve_explicit_tasks(memory_env) -> None:
    root, _database, _repository = memory_env
    lifecycle.user_prompt_submit(
        {"cwd": str(root), "session_id": "one", "prompt": "Remember the parser constraint"},
        agent_type="copilot",
    )
    created = json.loads(
        checkpoint(
            "Keep explicit parser task",
            cwd=str(root),
            agent_type="copilot",
            session_id="one",
        )
    )
    listed = json.loads(
        memory_action("list", cwd=str(root), agent_type="copilot", session_id="one")
    )
    memory_id = listed["memories"][0]["id"]

    forgotten = json.loads(
        memory_action(
            "forget",
            memory_id=memory_id,
            cwd=str(root),
            agent_type="copilot",
            session_id="one",
        )
    )
    assert forgotten["forgotten"] is True

    blocked = json.loads(
        memory_action("clear", cwd=str(root), agent_type="copilot", session_id="one")
    )
    assert blocked["requires_confirmation"] is True
    cleared = json.loads(
        memory_action(
            "clear",
            confirm=True,
            cwd=str(root),
            agent_type="copilot",
            session_id="one",
        )
    )
    assert cleared["explicit_tasks_preserved"] is True

    synced = json.loads(sync_workspace(cwd=str(root), agent_type="copilot", session_id="two"))
    assert any(task["id"] == created["task_id"] for task in synced["tasks"])


def test_duplicate_prompt_is_stored_once_per_session(memory_env) -> None:
    root, _database, repository = memory_env
    payload = {
        "cwd": str(root),
        "session_id": "duplicate",
        "prompt": "Keep the existing public parser API",
    }

    lifecycle.user_prompt_submit(payload, agent_type="copilot")
    lifecycle.user_prompt_submit(payload, agent_type="copilot")

    workspace = resolve_workspace(cwd=str(root))
    memories = recent_observations(repository, workspace, limit=20)
    matching = [item for item in memories if "public parser API" in item["summary"]]
    assert len(matching) == 1


def test_new_prompt_in_same_session_receives_new_relevant_context(memory_env) -> None:
    root, _database, _repository = memory_env
    lifecycle.user_prompt_submit(
        {
            "cwd": str(root),
            "session_id": "older",
            "prompt": "OAuth callback failed because state normalization removed plus signs",
        },
        agent_type="copilot",
    )

    first = lifecycle.prompt_context(
        {
            "cwd": str(root),
            "session_id": "current",
            "prompt": "What failed in the OAuth callback?",
        },
        agent_type="gemini",
    )
    duplicate = lifecycle.prompt_context(
        {
            "cwd": str(root),
            "session_id": "current",
            "prompt": "What failed in the OAuth callback?",
        },
        agent_type="gemini",
    )
    second = lifecycle.prompt_context(
        {
            "cwd": str(root),
            "session_id": "current",
            "prompt": "What constraint did we have for the parser?",
        },
        agent_type="gemini",
    )

    assert "state normalization" in first["additionalContext"]
    assert duplicate == {}
    assert second["additionalContext"]
