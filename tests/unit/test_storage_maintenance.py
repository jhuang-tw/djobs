from __future__ import annotations

import json
from pathlib import Path

from djobs import handoff, lifecycle
from djobs.memory import memory_action
from djobs.observations import record_observation
from djobs.queue.service import QueueService
from djobs.storage.maintenance import SQLiteStorageMaintenance, storage_maintenance
from djobs.storage.sqlite import SQLiteJobRepository
from djobs.workspace import resolve_agent_session, resolve_workspace


def _environment(tmp_path: Path, monkeypatch):
    root = tmp_path / "project"
    root.mkdir()
    database = tmp_path / "shared.db"
    repository = SQLiteJobRepository.from_path(database)
    queue = QueueService(repository)
    monkeypatch.setattr(handoff, "configure", lambda _path: queue)
    monkeypatch.setenv("DJOBS_DB", str(database))
    return root, database, repository


def test_sqlite_integrity_and_backup(tmp_path: Path) -> None:
    database = tmp_path / "jobs.db"
    repository = SQLiteJobRepository.from_path(database)
    maintenance = storage_maintenance(repository)

    integrity = maintenance.integrity_check()
    backup = maintenance.backup()

    assert integrity["ok"] is True
    assert integrity["database_path"] == str(database.resolve())
    assert backup["created"] is True
    backup_path = Path(str(backup["backup_path"]))
    assert backup_path.exists()
    copied = SQLiteJobRepository.from_path(backup_path)
    assert storage_maintenance(copied).integrity_check()["ok"] is True


def test_memory_compact_keeps_newest_duplicate_and_creates_backup(tmp_path, monkeypatch) -> None:
    root, database, repository = _environment(tmp_path, monkeypatch)
    backup_calls = 0
    original_backup = SQLiteStorageMaintenance.backup

    def counted_backup(self, destination=None):
        nonlocal backup_calls
        backup_calls += 1
        return original_backup(self, destination)

    monkeypatch.setattr(SQLiteStorageMaintenance, "backup", counted_backup)
    workspace = resolve_workspace(cwd=str(root))
    agent = resolve_agent_session(workspace, agent_type="test", session_id="retention")

    record_observation(repository, workspace, agent, "tool_result", "same summary")
    record_observation(repository, workspace, agent, "tool_result", "same summary")
    duplicate_rows = repository.read_all(
        "SELECT id, created_at FROM agent_observations "
        "WHERE event_type = 'tool_result' ORDER BY created_at DESC, id DESC"
    )
    expected_retained_id = str(duplicate_rows[0]["id"])
    lifecycle.user_prompt_submit(
        {"cwd": str(root), "session_id": "retention", "prompt": "Preserve user intent"},
        agent_type="test",
    )

    preview = json.loads(
        memory_action("compact", cwd=str(root), agent_type="test", dry_run=True, keep_recent=1)
    )
    assert preview["total"] == 1
    assert preview["backup"] is None

    applied = json.loads(
        memory_action(
            "compact",
            cwd=str(root),
            agent_type="test",
            dry_run=False,
            confirm=True,
            keep_recent=1,
        )
    )
    assert applied["ok"] is True
    assert applied["total"] == 1
    assert applied["backup"]["created"] is True
    assert Path(applied["backup"]["backup_path"]).exists()
    assert backup_calls == 1

    rows = repository.read_all(
        "SELECT id, event_type, summary FROM agent_observations ORDER BY created_at, id"
    )
    tool_rows = [row for row in rows if row["event_type"] == "tool_result"]
    assert len(tool_rows) == 1
    assert str(tool_rows[0]["id"]) == expected_retained_id
    assert any(row["event_type"] == "user_intent" for row in rows)
    assert database.exists()
