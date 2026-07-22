from __future__ import annotations

import json
import sqlite3
import subprocess
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest

from djobs import mcp_server
from djobs.handoff import checkpoint, handoff, sync_workspace
from djobs.queue.service import QueueService
from djobs.storage.sqlite import SQLiteJobRepository
from djobs.workspace import resolve_workspace


def _repo(path: Path) -> Path:
    path.mkdir()
    subprocess.run(["git", "init", "-q", str(path)], check=True)
    return path


@pytest.fixture
def shared_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    database = tmp_path / "djobs.db"
    monkeypatch.setenv("DJOBS_DB", str(database))
    mcp_server._queue = None
    return database


def _json(raw: str) -> dict[str, Any]:
    value = json.loads(raw)
    assert isinstance(value, dict)
    return value


def test_codex_hands_same_repo_work_to_claude_and_back(
    tmp_path: Path, shared_db: Path
) -> None:
    repo = _repo(tmp_path / "repo")
    created = _json(
        checkpoint(
            "Implement resolver",
            path="src/resolver.py",
            cwd=str(repo),
            agent_type="codex",
            session_id="codex-1",
        )
    )
    task_id = str(created["task_id"])
    assert created["state"] == "claimed"

    assert _json(
        handoff(
            task_id,
            "Resolver parses roots; tests remain.",
            cwd=str(repo),
            agent_type="codex",
            session_id="codex-1",
        )
    )["state"] == "pending"

    claude_sync = _json(
        sync_workspace(cwd=str(repo), agent_type="claude", session_id="claude-1")
    )
    assert any(task["id"] == task_id for task in claude_sync["tasks"])
    assert any(
        "tests remain" in task.get("evidence", "") for task in claude_sync["tasks"]
    )

    claude_claim = _json(
        checkpoint(
            "Implement resolver",
            path="src/resolver.py",
            cwd=str(repo),
            agent_type="claude",
            session_id="claude-1",
        )
    )
    assert claude_claim["task_id"] == task_id
    assert claude_claim["state"] == "claimed"

    handoff(
        task_id,
        "Tests added; Codex can review.",
        cwd=str(repo),
        agent_type="claude",
        session_id="claude-1",
    )
    codex_sync = _json(
        sync_workspace(cwd=str(repo), agent_type="codex", session_id="codex-2")
    )
    assert any(task["id"] == task_id for task in codex_sync["tasks"])


def test_sync_needs_no_correlation_id_and_reads_legacy_path_state(
    tmp_path: Path, shared_db: Path
) -> None:
    repo = _repo(tmp_path / "legacy")
    queue = QueueService(SQLiteJobRepository.from_path(shared_db))
    legacy = queue.submit(
        "legacy-task",
        {"summary": "Continue legacy work"},
        correlation_id=str(repo),
    )

    result = _json(sync_workspace(cwd=str(repo), agent_type="codex", session_id="one"))

    assert any(task["id"] == legacy.id for task in result["tasks"])


def test_different_repositories_are_completely_isolated(
    tmp_path: Path, shared_db: Path
) -> None:
    repo_a = _repo(tmp_path / "a")
    repo_b = _repo(tmp_path / "b")
    checkpoint("Only A", cwd=str(repo_a), agent_type="codex", session_id="a")

    result = _json(sync_workspace(cwd=str(repo_b), agent_type="claude", session_id="b"))

    assert result == {"ok": True, "workspace": "b", "state": "empty"}


def test_two_agents_cannot_claim_the_same_task(tmp_path: Path, shared_db: Path) -> None:
    repo = _repo(tmp_path / "repo")

    def claim(agent: str) -> dict[str, Any]:
        return _json(
            checkpoint(
                "Same work",
                path="src/shared.py",
                cwd=str(repo),
                agent_type=agent,
                session_id=agent,
            )
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(claim, ["codex", "claude"]))

    connection = sqlite3.connect(shared_db)
    count = connection.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
    running = connection.execute(
        "SELECT COUNT(*) FROM jobs WHERE status = 'running'"
    ).fetchone()[0]
    connection.close()
    assert count == 1
    assert running == 1
    assert sum(result.get("state") in {"claimed", "resumed"} for result in results) == 1


def test_expired_lease_can_be_recovered_by_another_agent(
    tmp_path: Path, shared_db: Path
) -> None:
    repo = _repo(tmp_path / "repo")
    first = _json(
        checkpoint(
            "Recover me",
            cwd=str(repo),
            agent_type="codex",
            session_id="dead",
        )
    )
    task_id = str(first["task_id"])
    expired = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
    connection = sqlite3.connect(shared_db)
    connection.execute("UPDATE jobs SET lease_expires_at = ? WHERE id = ?", (expired, task_id))
    connection.commit()
    connection.close()

    recovered = _json(
        checkpoint(
            "Recover me",
            cwd=str(repo),
            agent_type="claude",
            session_id="new",
        )
    )

    assert recovered["task_id"] == task_id
    assert recovered["state"] == "claimed"


def test_shared_sqlite_uses_wal_and_busy_timeout(tmp_path: Path, shared_db: Path) -> None:
    repo = _repo(tmp_path / "repo")
    checkpoint("Initialize database", cwd=str(repo), agent_type="codex", session_id="one")

    connection = sqlite3.connect(shared_db)
    journal_mode = connection.execute("PRAGMA journal_mode").fetchone()[0]
    busy_timeout = connection.execute("PRAGMA busy_timeout").fetchone()[0]
    connection.close()

    assert journal_mode.lower() == "wal"
    assert busy_timeout == 5000


def test_empty_sync_is_extremely_short(tmp_path: Path, shared_db: Path) -> None:
    repo = _repo(tmp_path / "empty")

    raw = sync_workspace(cwd=str(repo), agent_type="codex", session_id="one")

    assert len(raw) < 100
    assert _json(raw)["state"] == "empty"


def test_sync_output_respects_token_budget(tmp_path: Path, shared_db: Path) -> None:
    repo = _repo(tmp_path / "repo")
    for index in range(12):
        checkpoint(
            f"Task {index} with a deliberately long description " * 4,
            path=f"src/file_{index}.py",
            cwd=str(repo),
            agent_type="codex",
            session_id="one",
        )

    raw = sync_workspace(
        cwd=str(repo),
        agent_type="claude",
        session_id="two",
        token_budget=128,
        max_items=20,
    )

    assert (len(raw) + 3) // 4 <= 128


def test_workspace_ids_are_stable_per_repo(tmp_path: Path) -> None:
    repo = _repo(tmp_path / "repo")
    child = repo / "src"
    child.mkdir()

    assert (
        resolve_workspace(cwd=str(repo)).workspace_id
        == resolve_workspace(cwd=str(child)).workspace_id
    )
