from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from djobs import handoff
from djobs.coding_mcp import _with_context_hash
from djobs.handoff import checkpoint, sync_workspace
from djobs.observations import (
    memory_context_hash,
    recent_observations,
    record_observation,
    search_observations,
    update_observation_status,
)
from djobs.queue.service import QueueService
from djobs.storage.sqlite import SQLiteJobRepository
from djobs.workspace import resolve_agent_session, resolve_workspace


def _git_family(tmp_path: Path) -> tuple[Path, Path]:
    root = tmp_path / "primary"
    root.mkdir()
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    subprocess.run(["git", "-C", str(root), "config", "user.name", "Test"], check=True)
    subprocess.run(
        ["git", "-C", str(root), "config", "user.email", "test@example.invalid"],
        check=True,
    )
    subprocess.run(
        ["git", "-C", str(root), "remote", "add", "origin", "git@github.com:example/demo.git"],
        check=True,
    )
    (root / "app.py").write_text("print('base')\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(root), "add", "."], check=True)
    subprocess.run(["git", "-C", str(root), "commit", "-qm", "base"], check=True)
    sibling = tmp_path / "sibling"
    subprocess.run(
        ["git", "-C", str(root), "worktree", "add", "-q", "-b", "feature", str(sibling)],
        check=True,
    )
    return root, sibling


@pytest.fixture
def family_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    primary, sibling = _git_family(tmp_path)
    database = tmp_path / "memory.db"
    repository = SQLiteJobRepository.from_path(database)
    queue = QueueService(repository)
    monkeypatch.setattr(handoff, "configure", lambda _path: queue)
    monkeypatch.setenv("DJOBS_DB", str(database))
    return primary, sibling, repository


def test_worktrees_share_memory_but_not_checkout_identity(family_env) -> None:
    primary, sibling, _repository = family_env
    left = resolve_workspace(cwd=str(primary))
    right = resolve_workspace(cwd=str(sibling))

    assert left.checkout_id != right.checkout_id
    assert left.workspace_id == left.checkout_id
    assert right.workspace_id == right.checkout_id
    assert left.repo_family_id == right.repo_family_id
    assert left.repo_family_id in right.memory_correlation_ids


def test_passive_memory_crosses_worktrees_while_tasks_do_not(family_env) -> None:
    primary, sibling, repository = family_env
    left = resolve_workspace(cwd=str(primary))
    right = resolve_workspace(cwd=str(sibling))
    agent = resolve_agent_session(left, agent_type="codex", session_id="left-session")

    record_observation(
        repository,
        left,
        agent,
        "user_intent",
        "Keep Python 3.10 support and do not replace the public parser API",
    )
    recalled = search_observations(repository, right, "parser Python support", limit=4)
    assert recalled
    assert "Python 3.10" in recalled[0]["summary"]

    created = json.loads(
        checkpoint(
            "Checkout-local parser implementation",
            cwd=str(primary),
            agent_type="codex",
            session_id="left-session",
        )
    )
    sibling_sync = json.loads(
        sync_workspace(cwd=str(sibling), agent_type="claude", session_id="right-session")
    )
    assert all(task["id"] != created["task_id"] for task in sibling_sync.get("tasks", []))
    assert any("Python 3.10" in item["summary"] for item in sibling_sync["observations"])


def test_resolved_memory_remains_auditable_but_is_not_recalled(family_env) -> None:
    primary, _sibling, repository = family_env
    workspace = resolve_workspace(cwd=str(primary))
    agent = resolve_agent_session(workspace, agent_type="codex", session_id="lifecycle")
    record_observation(
        repository,
        workspace,
        agent,
        "tool_failure",
        "OAuth callback failed because plus signs were stripped",
    )
    memory_id = recent_observations(repository, workspace, limit=1)[0]["id"]

    assert update_observation_status(
        repository,
        workspace,
        memory_id,
        "resolved",
        resolved_by_commit="a" * 40,
    )
    assert search_observations(repository, workspace, "OAuth plus signs", limit=4) == []

    with repository._lock:
        row = repository._connection.execute(
            "SELECT metadata_json FROM agent_observations WHERE id = ?", (memory_id,)
        ).fetchone()
    metadata = json.loads(row["metadata_json"])
    assert metadata["memory_status"] == "resolved"
    assert metadata["resolved_by_commit"] == "a" * 40


def test_context_hash_suppresses_unchanged_memory_replay() -> None:
    observations = [
        {
            "id": "memory-1",
            "event": "user_intent",
            "summary": "Keep the public API",
            "status": "active",
        }
    ]
    raw = json.dumps(
        {
            "ok": True,
            "observations": observations,
            "counts": {"observations": 1},
            "tasks": [],
        }
    )
    known = memory_context_hash(observations)

    unchanged = json.loads(_with_context_hash(raw, known, "evidence"))
    assert unchanged["memory_unchanged"] is True
    assert unchanged["context_hash"] == known
    assert unchanged["observations"] == []
    assert unchanged["counts"]["observations"] == 0

    changed = json.loads(_with_context_hash(raw, "different", "evidence"))
    assert changed["memory_unchanged"] is False
    assert changed["observations"] == observations

    resume = {"goal": "Keep the public API", "next": "run focused tests"}
    resume_raw = json.dumps({"ok": True, "resume": resume, "tasks": []})
    resume_hash = memory_context_hash(resume)
    resume_unchanged = json.loads(_with_context_hash(resume_raw, resume_hash, "resume"))
    assert resume_unchanged["memory_unchanged"] is True
    assert resume_unchanged["resume"] == {}


def test_resume_tier_adds_compact_sources_and_hides_evidence_list() -> None:
    observations = [
        {
            "id": "memory-1",
            "event": "tool_failure",
            "summary": "Normalization removed plus signs",
            "status": "active",
            "score": 0.91,
        }
    ]
    raw = json.dumps(
        {
            "ok": True,
            "resume": {"goal": "Fix callback handling"},
            "observations": observations,
            "counts": {"observations": 1},
            "tasks": [],
        }
    )

    result = json.loads(_with_context_hash(raw, None, "resume"))

    assert "observations" not in result
    assert result["resume"]["sources"] == [
        {
            "event": "tool_failure",
            "summary": "Normalization removed plus signs",
            "status": "active",
            "score": 0.91,
        }
    ]
    assert result["resume"]["source_count"] == 1
    assert result["selected_memory"]["statuses"] == {"active": 1}
