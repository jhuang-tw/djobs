"""Storage integrity, backup, and maintenance helpers.

Maintenance operations stay inside the storage boundary.  Service and CLI
layers receive plain mappings and never access private database handles.
"""

from __future__ import annotations

import os
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class StorageMaintenance(Protocol):
    def integrity_check(self) -> dict[str, Any]: ...

    def backup(self, destination: str | Path | None = None) -> dict[str, Any]: ...


def probe_sqlite_database(db_path: str | os.PathLike[str]) -> tuple[bool, str]:
    """Check whether a SQLite database can be opened or created safely."""

    path = Path(db_path).expanduser()
    try:
        if path.exists():
            connection = sqlite3.connect(path)
            try:
                connection.execute("PRAGMA user_version")
            finally:
                connection.close()
            return True, "exists, writable"
        parent = path.parent
        parent.mkdir(parents=True, exist_ok=True)
        if parent.is_dir() and os.access(parent, os.W_OK):
            return True, "will be created on first use (parent writable)"
        return False, "parent directory not writable"
    except Exception as exc:
        return False, f"NOT usable: {exc}"


@dataclass(slots=True)
class SQLiteStorageMaintenance:
    repo: Any

    def _database_path(self) -> Path | None:
        with self.repo._lock:
            rows = self.repo._connection.execute("PRAGMA database_list").fetchall()
        for row in rows:
            # sqlite3.Row supports both index and key access.
            name = str(row[1] if not hasattr(row, "keys") else row["name"])
            filename = str(row[2] if not hasattr(row, "keys") else row["file"])
            if name == "main" and filename:
                return Path(filename).expanduser().resolve()
        return None

    def integrity_check(self) -> dict[str, Any]:
        with self.repo._lock:
            rows = self.repo._connection.execute("PRAGMA integrity_check").fetchall()
        messages = [str(row[0]) for row in rows]
        return {
            "backend": "sqlite",
            "ok": messages == ["ok"],
            "messages": messages[:20],
            "database_path": str(self._database_path() or ":memory:"),
        }

    def backup(self, destination: str | Path | None = None) -> dict[str, Any]:
        source = self._database_path()
        if source is None:
            return {
                "backend": "sqlite",
                "ok": False,
                "created": False,
                "reason": "in_memory_database",
                "backup_path": None,
            }
        if destination is None:
            stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            target = source.with_name(f"{source.name}.backup-{stamp}")
        else:
            target = Path(destination).expanduser().resolve()
        target.parent.mkdir(parents=True, exist_ok=True)
        if target == source:
            raise ValueError("backup destination must differ from source database")
        with self.repo._lock:
            destination_connection = sqlite3.connect(target)
            try:
                self.repo._connection.backup(destination_connection)
            finally:
                destination_connection.close()
        return {
            "backend": "sqlite",
            "ok": True,
            "created": True,
            "backup_path": str(target),
            "size_bytes": target.stat().st_size,
        }


@dataclass(slots=True)
class PostgresStorageMaintenance:
    repo: Any

    def integrity_check(self) -> dict[str, Any]:
        with self.repo._conn.cursor() as cur:
            cur.execute("SELECT 1 AS healthy")
            row = cur.fetchone()
        return {
            "backend": "postgres",
            "ok": bool(row and int(row["healthy"]) == 1),
            "messages": ["connection healthy"],
        }

    def backup(self, destination: str | Path | None = None) -> dict[str, Any]:
        return {
            "backend": "postgres",
            "ok": False,
            "created": False,
            "reason": "use_pg_dump",
            "backup_path": None,
        }


def storage_maintenance(repo: Any) -> StorageMaintenance:
    if hasattr(repo, "_connection"):
        return SQLiteStorageMaintenance(repo)
    if hasattr(repo, "_conn"):
        return PostgresStorageMaintenance(repo)
    raise TypeError(f"unsupported repository adapter: {type(repo).__name__}")
